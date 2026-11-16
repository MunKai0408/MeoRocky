"""Khám phá và nạp plugin (đặc tả 4.5). Một plugin = một thư mục con trong plugins/ hoặc plugins_user/.

Quy trình mỗi thư mục (lỗi ở bước nào cũng chỉ làm plugin đó `disabled`/`unavailable`, KHÔNG làm sập ứng dụng):
  1. Manifest: `plugin.toml` (parse TOML) hoặc MANIFEST ở mức module trong plugin.py được đọc TĨNH bằng ast
     (chỉ literal + phép tính số đơn giản). Bước này KHÔNG chạy mã plugin.
  2. parse_manifest + compatibility (api_version, requires) - vẫn chưa chạy mã plugin.
  3. Trùng id: cùng phạm vi -> vô hiệu hóa tất cả bản trùng; khác phạm vi -> plugins_user thắng + cảnh báo
     (nếu bản user nạp lỗi thì bản builtin được dùng lại, có cảnh báo).
  4. Chỉ khi qua 1-3 mới import `entry` (module:Class) và khởi tạo `Plugin()`. Đây là bước DUY NHẤT chạy mã plugin;
     bọc trong `import_guard` (`registry/isolation.py::import_guard`, đã có). Thêm plugin = chạy code trên máy bạn (4.7).
  5. Kiểm: lớp kế thừa sdk.api.Plugin; manifest của instance khớp manifest đã duyệt (tránh MANIFEST đổi lúc import).
Không tự kích hoạt: chỉ record state=ok có instance mới được registry dùng.
Plugin NHIỀU FILE (C1, decisions #56): mỗi thư mục plugin được nạp như một package riêng (registry/pluginpkg.py);
file anh em import bằng import TƯƠNG ĐỐI (`from .helper import X`). KHÔNG sửa sys.path. Plugin user: mọi `.py` trong
cây thư mục bị quét tĩnh trước khi import.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from core import tomlmini
from core.errors import PB3Error
from core.paths import is_within, nfc
from registry import compatibility as compat
from registry.evidence import effective_maturity
from registry import pluginpkg
from registry.scan import scan_source
from sdk.api import Plugin
from sdk.manifest import Manifest, parse_manifest
from sdk.types import Diagnostic
from core.model import SupportLevel

SCOPE_BUILTIN = "builtin"
SCOPE_USER = "user"
MAX_SOURCE_BYTES = 1024 * 1024          # plugin.py tối đa 1 MiB (tránh nạp file khổng lồ)
MAX_TOML_BYTES = 256 * 1024
MAX_AST_DEPTH = 24
MAX_INT = 2 ** 62
MAX_STR = 20000
MODULE_PREFIX = pluginpkg.MODULE_PREFIX

ImportGuard = Callable[[str], Any]      # (plugin_id) -> context manager


@dataclass
class PluginRecord:
    """Kết quả nạp một plugin. `instance` chỉ khác None khi state == ok."""
    plugin_id: str                       # từ manifest; nếu manifest hỏng: '?<tên thư mục>'
    scope: str
    path: str                            # thư mục plugin (đường dẫn hiển thị)
    state: str = compat.DISABLED
    reason: str = ""
    code: str = "E_OK"
    manifest: Optional[Manifest] = None
    instance: Optional[Plugin] = None
    enabled: bool = True                 # công tắc người dùng; hiệu lực = state ok VÀ enabled
    warnings: List[str] = field(default_factory=list)
    effective_maturity: str = ""         # R5: mức dùng để xếp hạng; rỗng = chưa kiểm, dùng mức khai trong manifest

    @property
    def maturity_effective(self) -> str:
        if self.effective_maturity:
            return self.effective_maturity
        return self.manifest.maturity if self.manifest else "?"

    @property
    def maturity_rank_effective(self) -> int:
        return SupportLevel.rank(self.maturity_effective) if self.manifest else 0

    @property
    def active(self) -> bool:
        return self.state == compat.OK and self.enabled and self.instance is not None

    @property
    def display_state(self) -> str:
        if self.state == compat.OK and not self.enabled:
            return compat.DISABLED
        return self.state

    @property
    def display_reason(self) -> str:
        if self.state == compat.OK and not self.enabled:
            return "người dùng đã tắt"
        return self.reason

    def diagnostic(self) -> Optional[Diagnostic]:
        if self.state == compat.OK:
            return None
        return Diagnostic(self.code if self.code != "E_OK" else "E_PLUGIN_INVALID", "error",
                          detail=self.reason, plugin_id=self.plugin_id)

    def to_row(self) -> Dict[str, Any]:
        m = self.manifest
        return {"id": self.plugin_id, "kind": m.kind if m else "?", "engines": list(m.engines) if m else [],
                "maturity": m.maturity if m else "?", "maturity_effective": self.maturity_effective,
                "version": m.version if m else "?",
                "scope": self.scope, "state": self.display_state, "reason": self.display_reason}


@dataclass
class LoadReport:
    records: List[PluginRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def by_id(self, plugin_id: str) -> List[PluginRecord]:
        return [r for r in self.records if r.plugin_id == plugin_id]


# ---------------------------------------------------------------------------- đọc MANIFEST tĩnh
def _lit(node: ast.AST, depth: int = 0) -> Any:
    if depth > MAX_AST_DEPTH:
        raise PB3Error("E_PLUGIN_INVALID", "MANIFEST lồng quá sâu")
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            if len(v) > MAX_STR:
                raise PB3Error("E_PLUGIN_INVALID", "MANIFEST có chuỗi quá dài")
            return v
        raise PB3Error("E_PLUGIN_INVALID", "MANIFEST chỉ chứa chuỗi/số/bool/null")
    if isinstance(node, ast.Dict):
        out: Dict[Any, Any] = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                raise PB3Error("E_PLUGIN_INVALID", "MANIFEST không dùng ** mở rộng")
            key = _lit(k, depth + 1)
            if not isinstance(key, str):
                raise PB3Error("E_PLUGIN_INVALID", "khóa MANIFEST phải là chuỗi")
            out[key] = _lit(v, depth + 1)
        return out
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_lit(e, depth + 1) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _lit(node.operand, depth + 1)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise PB3Error("E_PLUGIN_INVALID", "dấu +/- chỉ dùng với số")
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add, ast.Sub, ast.LShift)):
        a, b = _lit(node.left, depth + 1), _lit(node.right, depth + 1)
        if isinstance(node.op, ast.Add) and isinstance(a, str) and isinstance(b, str):
            if len(a) + len(b) > MAX_STR:
                raise PB3Error("E_PLUGIN_INVALID", "MANIFEST có chuỗi quá dài")
            return a + b
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in (a, b)):
            raise PB3Error("E_PLUGIN_INVALID", "phép tính trong MANIFEST chỉ dành cho số")
        if isinstance(node.op, ast.LShift):
            if not (isinstance(a, int) and isinstance(b, int)) or not 0 <= b <= 62:
                raise PB3Error("E_PLUGIN_INVALID", "phép << không hợp lệ trong MANIFEST")
            r = a << b
        elif isinstance(node.op, ast.Mult):
            r = a * b
        elif isinstance(node.op, ast.Add):
            r = a + b
        else:
            r = a - b
        if abs(r) > MAX_INT:
            raise PB3Error("E_PLUGIN_INVALID", "số trong MANIFEST quá lớn")
        return r
    raise PB3Error("E_PLUGIN_INVALID", "MANIFEST phải là literal (không gọi hàm/biến): %s" % type(node).__name__)


def static_manifest(source: str) -> Dict[str, Any]:
    """Lấy `MANIFEST = {...}` ở mức module mà KHÔNG chạy mã. Lỗi -> E_PLUGIN_INVALID."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as e:
        raise PB3Error("E_PLUGIN_INVALID", "plugin.py sai cú pháp: %s" % type(e).__name__)
    found = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MANIFEST" for t in node.targets):
            found = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "MANIFEST" and node.value is not None:
            found = node.value
    if found is None:
        raise PB3Error("E_PLUGIN_INVALID", "không có MANIFEST ở mức module trong plugin.py (và không có plugin.toml)")
    data = _lit(found)
    if not isinstance(data, dict):
        raise PB3Error("E_PLUGIN_INVALID", "MANIFEST phải là dict")
    return data


def _read_text(path: str, limit: int) -> str:
    try:
        size = os.path.getsize(path)
        if size > limit:
            raise PB3Error("E_TOO_LARGE", "file plugin quá lớn (%d > %d byte)" % (size, limit))
        with open(path, "rb") as f:
            raw = f.read(limit + 1)
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PB3Error("E_PLUGIN_INVALID", "file plugin không phải UTF-8")
    except OSError as e:
        raise PB3Error("E_PLUGIN_INVALID", "không đọc được file plugin (%s)" % type(e).__name__)


def read_manifest_dict(plugin_dir: str) -> Tuple[Dict[str, Any], str]:
    """(dict thô, nguồn). plugin.toml được ưu tiên; không chạy mã plugin. Chưa validate (để còn biết id khi lỗi)."""
    toml = os.path.join(plugin_dir, "plugin.toml")
    py = os.path.join(plugin_dir, "plugin.py")
    if os.path.isfile(toml):
        try:
            data = tomlmini.loads(_read_text(toml, MAX_TOML_BYTES))
        except ValueError as e:
            raise PB3Error("E_PLUGIN_INVALID", "plugin.toml sai cú pháp: " + str(e)[:80])
        return data, "plugin.toml"
    if os.path.isfile(py):
        return static_manifest(_read_text(py, MAX_SOURCE_BYTES)), "plugin.py"
    raise PB3Error("E_PLUGIN_INVALID", "thư mục không có plugin.toml hoặc plugin.py")


# ---------------------------------------------------------------------------- import + khởi tạo
@contextmanager
def _null_guard(plugin_id: str) -> Iterator[None]:
    yield


def _entry_file(plugin_dir: str, entry: str) -> Tuple[str, str]:
    mod, cls = entry.split(":", 1)
    parts = mod.split(".")
    if any(not p or p.startswith("__") for p in parts):
        raise PB3Error("E_PLUGIN_INVALID", "entry trỏ tới module không hợp lệ")
    path = os.path.join(plugin_dir, *parts) + ".py"
    if not is_within(plugin_dir, path):
        raise PB3Error("E_UNSAFE_PATH", "entry thoát ra ngoài thư mục plugin")
    if not os.path.isfile(path):
        raise PB3Error("E_PLUGIN_INVALID", "không thấy file entry %s.py" % "/".join(parts))
    return path, cls


def _instantiate(cls: Any, expected: Manifest) -> Plugin:
    if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise PB3Error("E_PLUGIN_INVALID", "entry không phải lớp kế thừa sdk.api.Plugin", plugin_id=expected.id)
    inst = cls()                                   # Plugin.__init__ validate manifest + hook theo capability
    if inst.manifest.to_dict() != expected.to_dict():
        raise PB3Error("E_PLUGIN_INVALID", "manifest lúc chạy khác manifest đã duyệt", plugin_id=expected.id)
    return inst


def _scan_tree(plugin_dir: str, entry_path: str, manifest_id: str) -> None:
    """Quét tĩnh MỌI .py trong cây plugin user (C1). Raise PB3Error nếu có vi phạm/vượt giới hạn."""
    try:
        files = pluginpkg.list_sources(plugin_dir)
    except PB3Error as e:
        raise PB3Error(e.code, "quét tĩnh từ chối: " + (e.detail or e.message_vi), plugin_id=manifest_id)
    found: List[str] = []
    for rel, real in files:
        for f in scan_source(_read_text(real, MAX_SOURCE_BYTES)):
            found.append(f if os.path.realpath(real) == os.path.realpath(entry_path) else "%s: %s" % (rel, f))
    if found:
        raise PB3Error("E_PLUGIN_FORBIDDEN", "quét tĩnh từ chối: " + "; ".join(found[:3])
                       + (" (+%d mục nữa)" % (len(found) - 3) if len(found) > 3 else ""), plugin_id=manifest_id)


def _import_failure(e: Exception, plugin_dir: str, manifest_id: str) -> PB3Error:
    hint = ""
    if isinstance(e, ModuleNotFoundError) and getattr(e, "name", None):
        top = str(e.name).split(".")[0]
        if top in pluginpkg.sibling_names(plugin_dir):
            hint = " (module phụ '%s' của plugin phải import tương đối: from .%s import ...)" % (top, top)
    return PB3Error("E_PLUGIN_INVALID", "import/khởi tạo thất bại: %s%s" % (type(e).__name__, hint),
                    plugin_id=manifest_id)


def _import_instance(plugin_dir: str, scope: str, manifest: Manifest, guard: ImportGuard,
                     static_scan: bool = True) -> Plugin:
    path, cls_name = _entry_file(plugin_dir, manifest.entry)
    if static_scan and scope == SCOPE_USER:           # lint trước khi import (registry/scan.py); KHÔNG phải bảo mật
        _scan_tree(plugin_dir, path, manifest.id)
    entry_mod = manifest.entry.split(":", 1)[0]
    pkg = pluginpkg.package_name(scope, os.path.basename(plugin_dir), plugin_dir)
    pluginpkg.install_package(pkg, plugin_dir)
    try:
        with guard(manifest.id):
            mod = importlib.import_module(pkg + "." + entry_mod)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                raise PB3Error("E_PLUGIN_INVALID", "module không có lớp %s" % cls_name, plugin_id=manifest.id)
            return _instantiate(cls, manifest)
    except PB3Error:
        pluginpkg.remove_package(pkg)
        raise
    except KeyboardInterrupt:
        # Ctrl-C của NGƯỜI DÙNG: không nuốt, không biến thành lỗi plugin.
        pluginpkg.remove_package(pkg)
        raise
    except Exception as e:                         # mã plugin lỗi (gồm SyntaxError/ImportError): chỉ vô hiệu plugin đó
        pluginpkg.remove_package(pkg)
        raise _import_failure(e, plugin_dir, manifest.id)
    except BaseException as e:                     # sys.exit()/SystemExit trong plugin: chỉ vô hiệu plugin đó
        pluginpkg.remove_package(pkg)
        raise _import_failure(e, plugin_dir, manifest.id)


# ---------------------------------------------------------------------------- API công khai
def _fail(rec: PluginRecord, e: PB3Error, state: str = compat.DISABLED) -> PluginRecord:
    rec.state, rec.code = state, e.code
    rec.reason = (e.detail or e.message_vi)
    rec.instance = None
    return rec


def load_plugin_class(cls: Any, scope: str = SCOPE_BUILTIN, origin: str = "<in-process>",
                      finder: Optional[Callable[[str], bool]] = None) -> PluginRecord:
    """Đường đăng ký THỐNG NHẤT cho plugin có sẵn trong tiến trình (test, plugin dựng sẵn): cùng các bước kiểm
    như nạp từ thư mục (manifest, api_version, requires, hook theo capability)."""
    pid = "?" + getattr(cls, "__name__", "plugin")
    rec = PluginRecord(pid, scope, origin)
    try:
        m = parse_manifest(getattr(cls, "MANIFEST", None))
        rec.plugin_id, rec.manifest = m.id, m
        c = compat.check_manifest(m, finder)
        if not c.ok:
            rec.state, rec.code, rec.reason = c.state, c.code, c.reason
            return rec
        rec.instance = _instantiate(cls, m)
        rec.state = compat.OK
    except PB3Error as e:
        _fail(rec, e)
    except Exception as e:
        _fail(rec, PB3Error("E_PLUGIN_INVALID", "khởi tạo thất bại: %s" % type(e).__name__))
    return rec


def _list_plugin_dirs(root: str, report: LoadReport, scope: str) -> List[Tuple[str, str]]:
    if not os.path.isdir(root):
        return []
    out = []
    try:
        names = sorted(nfc(n) for n in os.listdir(root))
    except OSError as e:
        report.warnings.append("không đọc được thư mục plugin %s (%s)" % (scope, type(e).__name__))
        return []
    for n in names:
        if n.startswith((".", "_")):
            continue
        p = os.path.join(root, n)
        if os.path.isdir(p):
            out.append((n, p))
    return out


CACHE_VERSION = 1
CACHE_FILENAME = "discovery_cache.json"


def _load_discovery_cache(path: str) -> Dict[str, Any]:
    """dict realpath -> entry; file thiếu/hỏng/sai version -> {} (không bao giờ raise)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return {}
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {k: v for k, v in entries.items() if isinstance(k, str) and isinstance(v, dict)}
    except Exception:
        return {}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _save_discovery_cache(path: str, cache: Dict[str, Any]) -> None:
    """Ghi cache (atomic qua file tạm + os.replace). Tỉa mục của file đã xóa; lỗi ghi -> bỏ qua, không raise."""
    try:
        entries: Dict[str, Any] = {}
        for key, ent in cache.items():
            if not (isinstance(ent, dict) and isinstance(ent.get("raw"), dict)):
                continue
            if not os.path.isfile(key):
                continue
            json.dumps(ent)                      # bỏ mục không JSON được (tuple/datetime/set trong raw)
            entries[key] = ent
        data = json.dumps({"version": CACHE_VERSION, "entries": entries}, ensure_ascii=False).encode("utf-8")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = "%s.tmp.%d" % (path, os.getpid())
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    except Exception:
        pass


def _manifest_file(pdir: str):
    """(đường dẫn, nguồn, giới hạn byte) của file manifest; (None, None, None) nếu không có file."""
    toml = os.path.join(pdir, "plugin.toml")
    if os.path.isfile(toml):
        return toml, "plugin.toml", MAX_TOML_BYTES
    py = os.path.join(pdir, "plugin.py")
    if os.path.isfile(py):
        return py, "plugin.py", MAX_SOURCE_BYTES
    return None, None, None


def _manifest_with_cache(pdir: str, cache: Optional[Dict[str, Any]]):
    """(raw, src) — dùng cache khi file manifest không đổi (size/mtime_ns/sha256), nếu không đọc lại.
    Chỉ cache phần TĨNH (dict manifest thô); import + effective_maturity luôn chạy lại (B1).
    cache=None -> đọc trực tiếp như cũ. Raise PB3Error như read_manifest_dict khi đọc lỗi."""
    if cache is None:
        return read_manifest_dict(pdir)
    mpath, src, limit = _manifest_file(pdir)
    if mpath is None:
        raise PB3Error("E_PLUGIN_INVALID", "thư mục không có plugin.toml hoặc plugin.py")
    try:
        st = os.stat(mpath)
    except OSError:
        return read_manifest_dict(pdir)     # file biến mất giữa chừng: để hàm gốc báo lỗi đúng
    if st.st_size > limit:
        return read_manifest_dict(pdir)     # quá lớn: hàm gốc báo E_TOO_LARGE (không hash cả file lớn)
    key = os.path.realpath(mpath)
    ent = cache.get(key)
    if (isinstance(ent, dict) and ent.get("size") == st.st_size and ent.get("mtime_ns") == st.st_mtime_ns
            and ent.get("source") == src and isinstance(ent.get("raw"), dict)):
        try:
            if _sha256_file(mpath) == ent.get("sha256"):
                return ent["raw"], src
        except OSError:
            pass
    raw, src2 = read_manifest_dict(pdir)
    try:
        ent = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
               "sha256": _sha256_file(mpath), "source": src2, "raw": raw}
        json.dumps(ent)                     # chỉ cache khi JSON được
        cache[key] = ent
    except Exception:
        pass
    return raw, src2


def discover(dirs: Iterable[Tuple[str, str]], disabled_ids: Iterable[str] = (),
             finder: Optional[Callable[[str], bool]] = None,
             import_guard: Optional[ImportGuard] = None, corpus_dir: Optional[str] = None,
             static_scan: bool = True, only_ids: Optional[Iterable[str]] = None,
             cache_file: Optional[str] = None) -> LoadReport:
    """Quét `dirs` = [(scope, đường_dẫn_thư_mục)] và nạp plugin. Không bao giờ raise vì plugin lỗi.

    `disabled_ids`: id người dùng đã tắt (vẫn nạp để hiển thị, nhưng enabled=False).
    `corpus_dir`: thư mục `tests/corpus` để kiểm bằng chứng M3/M4 (R5, decisions #51). Thiếu bằng chứng -> plugin vẫn
    nạp nhưng `effective_maturity` bị hạ (sàn M2) kèm cảnh báo. None = không kiểm (mức hiệu lực = mức khai).
    `only_ids`: chỉ import các id này (B2, X13); các plugin khác vẫn đọc manifest tĩnh nhưng KHÔNG import
    (record giữ lại với lý do rõ, để tra cứu). None = nạp đủ.
    `cache_file`: file cache duyệt manifest tĩnh (B1, X13); None = không cache.
    """
    guard = import_guard or _null_guard
    off = set(disabled_ids)
    only = None if only_ids is None else set(only_ids)
    cache = _load_discovery_cache(cache_file) if cache_file else None
    report = LoadReport()
    staged: List[Tuple[PluginRecord, str]] = []       # (record đã duyệt manifest, plugin_dir)

    # --- bước 1-2: manifest + tương thích (không chạy mã plugin)
    for scope, root in dirs:
        for dirname, pdir in _list_plugin_dirs(root, report, scope):
            rec = PluginRecord("?" + dirname, scope, pdir)
            if not is_within(root, pdir):
                _fail(rec, PB3Error("E_UNSAFE_PATH", "thư mục plugin thoát khỏi thư mục gốc (symlink?)"))
                report.records.append(rec)
                continue
            try:
                raw, src = _manifest_with_cache(pdir, cache)
                if isinstance(raw.get("id"), str) and raw["id"]:
                    rec.plugin_id = raw["id"][:60]                 # để bảng plugin list hiện đúng id dù manifest sai
                ca = compat.check_api(raw.get("api_version"))      # api lệch: lý do rõ, ưu tiên hơn lỗi schema khác
                if not ca.ok:
                    rec.state, rec.code, rec.reason = ca.state, ca.code, ca.reason
                    report.records.append(rec)
                    continue
                m = parse_manifest(raw)
                rec.plugin_id, rec.manifest = m.id, m
                for ign in m.ignored_fields:
                    rec.warnings.append("trường manifest lạ bị bỏ qua: %s" % ign)
                if only is not None and m.id not in only:
                    # B2: ngoài phạm vi nạp có đích — không import, không kiểm compat (vẫn giữ record).
                    rec.state, rec.code, rec.reason = (compat.DISABLED, "E_OK",
                        "không nằm trong phạm vi nạp (chỉ nạp: %s)" % ", ".join(sorted(only)))
                else:
                    c = compat.check_manifest(m, finder)
                    if not c.ok:
                        rec.state, rec.code, rec.reason = c.state, c.code, c.reason
                    else:
                        staged.append((rec, pdir))
            except PB3Error as e:
                # api lệch cũng rơi vào đây (parse_manifest đã kiểm) -> vẫn là disabled kèm lý do rõ
                _fail(rec, e)
            except Exception as e:   # lỗi bất ngờ khi đọc manifest (vd RecursionError): chỉ vô hiệu plugin đó
                _fail(rec, PB3Error("E_PLUGIN_INVALID",
                                    "đọc manifest lỗi: %s" % type(e).__name__))
            report.records.append(rec)

    if cache is not None and cache_file:
        _save_discovery_cache(cache_file, cache)   # lưu ngay sau bước tĩnh (trước import, bước 4-5)

    # --- bước 3: trùng id
    by_id: Dict[str, List[Tuple[PluginRecord, str]]] = {}
    for rec, pdir in staged:
        by_id.setdefault(rec.plugin_id, []).append((rec, pdir))
    queue: List[Tuple[PluginRecord, str]] = []
    for pid in sorted(by_id):
        group = by_id[pid]
        survivors = []
        for scope in (SCOPE_USER, SCOPE_BUILTIN):
            same = [x for x in group if x[0].scope == scope]
            if len(same) > 1:
                for rec, _ in same:
                    rec.state, rec.code = compat.DISABLED, "E_PLUGIN_INVALID"
                    rec.reason = "trùng id '%s' trong cùng phạm vi %s (không đoán bản nào đúng)" % (pid, scope)
                report.warnings.append("id '%s' bị trùng trong %s: cả %d bản bị vô hiệu hóa" % (pid, scope, len(same)))
            else:
                survivors.extend(same)                     # user trước builtin nhờ thứ tự vòng lặp
        queue.extend(survivors)

    # --- bước 4-5: import theo thứ tự ưu tiên; mỗi id chỉ một bản được active
    taken: Dict[str, PluginRecord] = {}
    for rec, pdir in queue:
        pid = rec.plugin_id
        if pid in taken:
            w = taken[pid]
            rec.state, rec.code = compat.DISABLED, "E_OK"
            rec.reason = "bị plugin cùng id ở %s ghi đè" % w.scope
            if rec.scope == SCOPE_BUILTIN:
                report.warnings.append("plugin builtin '%s' bị plugins_user ghi đè" % pid)
            continue
        try:
            rec.instance = _import_instance(pdir, rec.scope, rec.manifest, guard, static_scan)   # type: ignore[arg-type]
            rec.state, rec.code, rec.reason = compat.OK, "E_OK", ""
            rec.enabled = pid not in off
            if corpus_dir is not None and rec.manifest is not None:
                eff, note = effective_maturity(corpus_dir, pid, rec.manifest.maturity, rec.manifest.version)
                rec.effective_maturity = eff
                if note:
                    rec.warnings.append("maturity: " + note)
                    report.warnings.append("plugin '%s' %s" % (pid, note))
            taken[pid] = rec
        except PB3Error as e:
            _fail(rec, e)
            if rec.scope == SCOPE_USER:
                report.warnings.append("plugin user '%s' nạp lỗi: %s" % (pid, rec.reason))
        except KeyboardInterrupt:
            raise
        except BaseException as e:   # sys.exit() trong import/khởi tạo: chỉ vô hiệu plugin đó
            _fail(rec, PB3Error("E_PLUGIN_INVALID",
                                "import/khởi tạo thất bại: %s" % type(e).__name__))
            if rec.scope == SCOPE_USER:
                report.warnings.append("plugin user '%s' nạp lỗi: %s" % (pid, rec.reason))
    return report
