"""Đọc/ghi/kiểm tra cấu hình (config/default.toml + config/local.toml).

Nguyên tắc: cấu hình hỏng KHÔNG được làm menu không khởi động được. Giá trị sai
-> cảnh báo + dùng mặc định; local.toml cú pháp hỏng -> bỏ qua kèm cảnh báo;
default.toml mất -> dùng mặc định dựng sẵn. OPTIONS là nguồn chân lý về kiểu,
miền giá trị và mặc định (test đảm bảo default.toml không lệch).
"""
from __future__ import annotations

import datetime
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import safe_io, tomlmini
from .errors import PB3Error
from .paths import Paths, safe_relpath

try:                                # Python >= 3.11
    import tomllib as _tomllib      # type: ignore
except ImportError:                 # iSH thường là Python 3.9
    _tomllib = None                 # type: ignore

MAX_CONFIG_BYTES = 1024 * 1024
MAX_STRLIST_ITEMS = 500
MAX_STRLIST_CHARS = 64
BACKEND = "tomllib" if _tomllib is not None else "tomlmini"


@dataclass(frozen=True)
class Opt:
    key: str                  # "section.name"
    kind: str                 # int | float | bool | str | relpath | abspaths | strlist
    default: Any
    lo: Optional[float] = None
    hi: Optional[float] = None
    choices: Optional[Tuple[str, ...]] = None


def _o(key, kind, default, lo=None, hi=None, choices=None):
    return Opt(key, kind, default, lo, hi, choices)


GiB = 1024 ** 3
MiB = 1024 ** 2

OPTIONS: Tuple[Opt, ...] = (
    _o("app.language", "str", "vi", choices=("vi",)),
    _o("app.debug", "bool", False),
    _o("paths.workspace", "relpath", "workspace"),
    _o("paths.logs", "relpath", "logs"),
    _o("paths.reports", "relpath", "reports"),
    _o("paths.kb", "relpath", "kb"),
    _o("paths.plugins", "relpath", "plugins"),
    _o("paths.plugins_user", "relpath", "plugins_user"),
    # RESERVED (dành riêng, chưa dùng bởi pipeline): thư mục local do người dùng
    # chọn trong iSH. Giữ khóa để tương thích về sau; pipeline hiện tại KHÔNG đọc
    # input_extra — đừng dựa vào nó để đưa file vào (xem handoff/decisions.md).
    _o("paths.input_extra", "abspaths", []),
    _o("plugins.disabled", "strlist", []),          # id plugin người dùng đã tắt (menu 3 / `plugin disable`)
    _o("registry.discovery_cache", "bool", True),   # cache kết quả duyệt manifest tĩnh (B1, X13)
    _o("output.versioning", "str", "version", choices=("version", "error")),
    _o("limits.max_input_bytes", "int", 8 * GiB, 1, 1024 * GiB),
    _o("limits.max_entry_bytes", "int", 64 * MiB, 1, 8 * GiB),
    _o("limits.plan_max_entry_bytes", "int", 8 * MiB, 1, 8 * GiB),
    _o("limits.max_memory_read_bytes", "int", 128 * MiB, 1, 512 * MiB),
    _o("limits.max_total_uncompressed_bytes", "int", 8 * GiB, 1, 1024 * GiB),
    _o("limits.max_entries", "int", 200000, 1, 10 ** 7),
    _o("limits.max_depth", "int", 4, 1, 32),
    _o("limits.max_ratio", "int", 200, 1, 10 ** 6),
    _o("limits.hook_timeout_s", "float", 20.0, 0.1, 3600),
    _o("limits.hook_timeout_heavy_s", "float", 120.0, 0.1, 7200),
    _o("limits.chunk_size", "int", MiB, 4096, 16 * MiB),
    _o("limits.workers", "int", 1, 1, 8),
    _o("limits.disk_factor", "float", 2.5, 1.0, 10.0),
    _o("log.level", "str", "INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    _o("log.max_bytes", "int", MiB, 65536, 100 * MiB),
    _o("log.backups", "int", 3, 0, 20),
    _o("log.keep_sessions", "int", 20, 1, 200),
    _o("log.max_message_chars", "int", 500, 80, 5000),
    _o("progress.interval_s", "float", 1.0, 0.1, 30.0),
    _o("progress.width", "int", 0, 0, 120),
    _o("progress.overall", "bool", True),
    _o("progress.heartbeat_s", "float", 1.0, 0.2, 30.0),
    # Màu ANSI cho menu/CLI (core/color.py): auto = chỉ khi stdout là TTY;
    # on = bật ép kể cả khi pipe (xem qua less -R); off = không bao giờ.
    # NO_COLOR (không rỗng) luôn thắng khóa này.
    _o("ui.color", "str", "auto", choices=("auto", "on", "off")),
    _o("keepalive.enabled", "bool", False),
    _o("keepalive.device", "str", "/dev/location"),
    _o("scoring.w_magic", "float", 0.30, 0.0, 1.0),
    _o("scoring.w_ext", "float", 0.10, 0.0, 1.0),
    _o("scoring.w_name", "float", 0.12, 0.0, 1.0),
    _o("scoring.w_schema", "float", 0.18, 0.0, 1.0),
    _o("scoring.w_locale", "float", 0.10, 0.0, 1.0),
    _o("scoring.w_manifest", "float", 0.08, 0.0, 1.0),
    _o("scoring.w_content", "float", 0.07, 0.0, 1.0),
    _o("scoring.w_kb", "float", 0.05, 0.0, 1.0),
    _o("scoring.auto_select", "float", 0.80, 0.0, 1.0),
    _o("scoring.ask_min", "float", 0.55, 0.0, 1.0),
)
BY_KEY: Dict[str, Opt] = {o.key: o for o in OPTIONS}


def coerce(opt: Opt, raw: Any) -> Any:
    """Kiểm tra + chuẩn hóa 1 giá trị theo Opt. Raise ValueError (thông điệp tiếng Việt)."""
    k = opt.kind
    if k == "bool":
        if not isinstance(raw, bool):
            raise ValueError("cần true/false")
        return raw
    if k == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("cần số nguyên")
        v: Any = raw
    elif k == "float":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("cần số")
        v = float(raw)
    elif k == "str":
        if not isinstance(raw, str):
            raise ValueError("cần chuỗi")
        if opt.choices and raw not in opt.choices:
            raise ValueError("phải là một trong %s" % ", ".join(opt.choices))
        return raw
    elif k == "relpath":
        if not isinstance(raw, str):
            raise ValueError("cần chuỗi đường dẫn")
        try:
            safe_relpath(raw)
        except PB3Error as exc:
            raise ValueError("đường dẫn tương đối không an toàn: %s" % exc.detail)
        return raw
    elif k == "abspaths":
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise ValueError("cần mảng chuỗi")
        for x in raw:
            if "\x00" in x or not os.path.isabs(x):
                raise ValueError("%r không phải đường dẫn tuyệt đối hợp lệ" % x[:60])
        return list(raw)
    elif k == "strlist":
        if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
            raise ValueError("cần mảng chuỗi")
        if len(raw) > MAX_STRLIST_ITEMS:
            raise ValueError("tối đa %d phần tử" % MAX_STRLIST_ITEMS)
        out: List[str] = []
        for x in raw:
            if not x or len(x) > MAX_STRLIST_CHARS or any(ord(c) < 32 or ord(c) == 127 for c in x):
                raise ValueError("phần tử %r rỗng, quá dài hoặc có ký tự điều khiển" % x[:40])
            if x not in out:                     # bỏ trùng, giữ thứ tự
                out.append(x)
        return out
    else:  # pragma: no cover
        raise ValueError("kind lạ: " + k)
    if opt.lo is not None and v < opt.lo:
        raise ValueError("phải >= %s" % opt.lo)
    if opt.hi is not None and v > opt.hi:
        raise ValueError("phải <= %s" % opt.hi)
    return v


def _reject_unsupported(tree: Any, where: str = "") -> None:
    """Từ chối giá trị mà tomlmini không hỗ trợ (datetime/inf/nan): tomllib
    parse được nhưng tomlmini không, nên parse_toml phải từ chối ở CẢ hai
    backend để hành vi đồng nhất (test đối chiếu hai backend)."""
    if isinstance(tree, dict):
        for k, v in tree.items():
            _reject_unsupported(v, "%s.%s" % (where, k) if where else str(k))
    elif isinstance(tree, list):
        for v in tree:
            _reject_unsupported(v, where)
    elif isinstance(tree, (datetime.datetime, datetime.date, datetime.time)):
        raise ValueError("datetime không hỗ trợ tại %s (dùng chuỗi ISO)" % (where or "?"))
    elif isinstance(tree, float) and (math.isnan(tree) or math.isinf(tree)):
        raise ValueError("inf/nan không hỗ trợ tại %s" % (where or "?"))


_HEX_LIT = re.compile(r"\b0[xX][0-9a-fA-F]+\b")


def _reject_hex_literals(text: str) -> None:
    """Số hex 0x...: tomlmini từ chối (không khớp _INT) nên tomllib cũng phải
    từ chối. Quét ngoài chuỗi/chú thích để không báo nhầm chuỗi chứa '0x'."""
    out, i, q = [], 0, None
    while i < len(text):
        ch = text[i]
        if q:
            if ch == q:
                q = None
            i += 1
            continue
        if ch in "\"'":
            q = ch
            i += 1
            continue
        if ch == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    m = _HEX_LIT.search("".join(out))
    if m:
        raise ValueError("số hex %s không hỗ trợ (dùng số thập phân)" % m.group(0))


def parse_toml(text: str) -> Dict[str, Any]:
    """tomllib nếu có, ngược lại tomlmini. Raise ValueError khi sai cú pháp.
    Cả hai backend đều từ chối datetime/inf/nan/số hex (parity với tomlmini)."""
    _reject_hex_literals(text)
    if _tomllib is not None:
        tree = _tomllib.loads(text)
        _reject_unsupported(tree)
        return tree
    return tomlmini.loads(text)


def validate_tree(tree: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Trả (giá trị hợp lệ theo khóa 'a.b', danh sách cảnh báo). Không raise."""
    ok: Dict[str, Any] = {}
    warns: List[str] = []
    for sec, body in tree.items():
        if not isinstance(body, dict):
            warns.append("khóa '%s' nằm ngoài bảng nào; bỏ qua" % sec)
            continue
        for name, raw in body.items():
            key = "%s.%s" % (sec, name)
            opt = BY_KEY.get(key)
            if opt is None:
                warns.append("khóa lạ '%s'; bỏ qua" % key)
                continue
            try:
                ok[key] = coerce(opt, raw)
            except ValueError as exc:
                warns.append("%s: %s; dùng mặc định %r" % (key, exc, opt.default))
    return ok, warns


class Config:
    def __init__(self, values: Dict[str, Any], warnings: List[str], sources: List[str]):
        self._v = values
        self.warnings = warnings
        self.sources = sources

    def get(self, key: str, default: Any = None) -> Any:
        """Giá trị cấu hình. Không truyền default thì khóa lạ vẫn KeyError
        (lỗi lập trình); truyền default thì trả default khi khóa chưa có."""
        if default is None:
            return self._v[key]          # KeyError = lỗi lập trình (khóa không có trong OPTIONS)
        return self._v.get(key, default)

    def section(self, name: str) -> Dict[str, Any]:
        p = name + "."
        return {k[len(p):]: v for k, v in self._v.items() if k.startswith(p)}

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._v)

    def update(self, key: str, value: Any) -> None:
        """Cập nhật giá trị TRONG BỘ NHỚ sau khi đã `save_local` (không ghi file). Giá trị phải qua `coerce`."""
        opt = BY_KEY[key]                       # KeyError = lỗi lập trình
        try:
            self._v[key] = coerce(opt, value)
        except ValueError as exc:
            raise PB3Error("E_CONFIG_INVALID", "%s: %s" % (key, exc))


def builtin_defaults() -> Dict[str, Any]:
    return {o.key: (list(o.default) if isinstance(o.default, list) else o.default) for o in OPTIONS}


def _salvage_toml(text: str) -> Tuple[Optional[Dict[str, Any]], List[int]]:
    """Cứu vãn một phần file TOML sai cú pháp: bỏ lần lượt từng dòng lỗi
    (tối đa 10 dòng) rồi parse lại. Trả (cây đã cứu, các dòng đã bỏ);
    (None, [...]) nếu không cứu được -> người gọi bỏ cả file như cũ."""
    lines = text.splitlines()
    dropped: List[int] = []
    for _ in range(10):
        try:
            return parse_toml("\n".join(lines)), dropped
        except ValueError as exc:
            ln = getattr(exc, "line", None)
            if ln is None:
                m = re.search(r"at line (\d+)", str(exc))
                ln = int(m.group(1)) if m else None
            if ln is None or ln < 1 or ln > len(lines):
                return None, dropped
            dropped.append(ln)
            lines[ln - 1] = ""   # bỏ dòng lỗi, giữ nguyên số dòng còn lại
    return None, dropped


def _load_file(path, label: str, warns: List[str], sources: List[str]) -> Dict[str, Any]:
    try:
        if not os.path.exists(str(path)):
            return {}
        text = safe_io.read_text_limited(path, MAX_CONFIG_BYTES, encoding="utf-8-sig")
        tree = parse_toml(text)
    except PB3Error as exc:
        warns.append("%s không đọc được (%s: %s); bỏ qua" % (label, exc.code, exc.detail))
        return {}
    except ValueError as exc:
        # Cứu vãn một phần: giữ khóa tốt, chỉ bỏ dòng lỗi (kèm số dòng);
        # không cứu được thì bỏ cả file như cũ.
        salvaged, dropped = _salvage_toml(text)
        if salvaged is None:
            warns.append("%s sai cú pháp TOML (%s); bỏ qua" % (label, exc))
            return {}
        warns.append("%s sai cú pháp TOML (%s); giữ lại các khóa tốt, bỏ dòng %s"
                     % (label, exc, ", ".join(map(str, dropped)) or "?"))
        tree = salvaged
    ok, w = validate_tree(tree)
    warns.extend("%s: %s" % (label, x) for x in w)
    sources.append(str(path))
    return ok


def load_config(paths: Paths, *, apply_paths: bool = True) -> Config:
    """Nạp mặc định dựng sẵn < default.toml < local.toml. Không bao giờ raise."""
    values = builtin_defaults()
    warns: List[str] = []
    sources: List[str] = []
    if not paths.default_toml.exists():
        warns.append("thiếu config/default.toml; dùng mặc định dựng sẵn")
    else:
        values.update(_load_file(paths.default_toml, "default.toml", warns, sources))
    values.update(_load_file(paths.local_toml, "local.toml", warns, sources))
    cfg = Config(values, warns, sources)
    if apply_paths:
        try:
            paths.configure(workspace=cfg.get("paths.workspace"), logs=cfg.get("paths.logs"),
                            reports=cfg.get("paths.reports"), kb=cfg.get("paths.kb"))
        except PB3Error as exc:       # đã lọc ở coerce, đây là lưới an toàn thứ hai
            warns.append("đường dẫn cấu hình không dùng được (%s); dùng mặc định" % exc.detail)
            paths.configure(workspace="workspace", logs="logs", reports="reports", kb="kb")
    return cfg


_HDR = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_TRIPLE = ('"' * 3, "'" * 3)


def _patch_local_text(old: str, clean: Dict[str, Any]) -> Optional[str]:
    """Cập nhật local.toml TẠI CHỖ để giữ chú thích/thứ tự. Trả None nếu gặp cấu trúc không chắc (bảng mảng,
    chuỗi nhiều dòng...) -> người gọi ghi lại toàn bộ như cũ. Kết quả luôn được người gọi đối chiếu bằng parse."""
    lines = old.splitlines()
    for ln in lines:
        s = ln.strip()
        if s.startswith("[[") or s.startswith("{") or any(t in s for t in _TRIPLE):
            return None
    for key, v in clean.items():
        sec, name = key.split(".", 1)
        val = tomlmini.dumps({sec: {name: v}}).split("\n")[1]          # "name = giá trị" (một dòng)
        start = None
        for i, ln in enumerate(lines):
            m = _HDR.match(ln)
            if m and m.group(1).strip() == sec:
                start = i
                break
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += ["[%s]" % sec, val]
            continue
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _HDR.match(lines[j]):
                end = j
                break
        pat = re.compile(r"^\s*%s\s*=" % re.escape(name))
        hit = None
        for j in range(start + 1, end):
            if pat.match(lines[j]):
                rhs = lines[j].split("=", 1)[1].strip()
                if rhs.startswith("[") and "]" not in rhs:
                    return None                                          # mảng nhiều dòng
                hit = j
                break
        if hit is not None:
            lines[hit] = val
        else:
            at = end
            while at > start + 1 and not lines[at - 1].strip():
                at -= 1
            lines.insert(at, val)
    return "\n".join(lines) + "\n"


def save_local(paths: Paths, updates: Dict[str, Any]) -> None:
    """Gộp `updates` ({'limits.workers': 2}) vào config/local.toml bằng ghi nguyên tử.

    Giá trị sai -> E_CONFIG_INVALID (không ghi gì). local.toml cũ bị hỏng -> được sao
    lưu (.bak-...) trước khi thay. Chú thích/thứ tự được giữ khi cập nhật tại chỗ an toàn; nếu không chắc (bảng mảng, chuỗi nhiều dòng) thì ghi lại toàn bộ và chú thích mất.
    """
    clean: Dict[str, Any] = {}
    for key, raw in updates.items():
        opt = BY_KEY.get(key)
        if opt is None:
            raise PB3Error("E_CONFIG_INVALID", "khóa không tồn tại: %s" % key)
        try:
            clean[key] = coerce(opt, raw)
        except ValueError as exc:
            raise PB3Error("E_CONFIG_INVALID", "%s: %s" % (key, exc))
    tree: Dict[str, Any] = {}
    old_text = None
    if paths.local_toml.exists():
        try:
            old_text = safe_io.read_text_limited(paths.local_toml, MAX_CONFIG_BYTES, encoding="utf-8-sig")
            tree = parse_toml(old_text)
        except (PB3Error, ValueError):
            safe_io.backup_file(paths.local_toml)     # không mất dữ liệu người dùng
            tree, old_text = {}, None
    for key, v in clean.items():
        sec, name = key.split(".", 1)
        tree.setdefault(sec, {})[name] = v
    data = None
    if old_text is not None:                          # thử giữ chú thích: chỉ nhận nếu parse lại ĐÚNG bằng cây mong muốn
        patched = _patch_local_text(old_text, clean)
        if patched is not None:
            try:
                if parse_toml(patched) == tree:
                    data = patched
            except ValueError:
                data = None
    if data is None:
        data = ("# config/local.toml - ghi đè cấu hình mặc định (do PB3 ghi)\n" + tomlmini.dumps(tree))
    safe_io.atomic_write_bytes(paths.local_toml, data.encode("utf-8"))
