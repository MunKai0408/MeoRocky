"""Sinh skeleton plugin (đặc tả 4.8, Phụ lục B; Claude 03 đợt 2, nhiệm vụ B2): `plugin new <kind> <id>`.

Tạo:
    plugins_user/<id>/plugin.py        skeleton + MANIFEST literal hợp lệ (đọc tĩnh được, xem decisions #32)
    tests/fixtures/<id>/.gitkeep       thư mục fixture RỖNG (tự thêm dữ liệu nhỏ, tự tạo, không bản quyền)

Quy tắc:
  * `id` phải khớp ^[a-z][a-z0-9_]{1,40}$ (cùng luật manifest).
  * KHÔNG ghi đè: thư mục plugin hoặc fixture đã có -> E_OUTPUT_EXISTS; id trùng thư mục plugin builtin -> E_OUTPUT_EXISTS
    (plugin người dùng sẽ che plugin builtin theo decisions #34; muốn che thì tự làm có chủ ý, không qua lệnh này).
  * Skeleton sinh xong được kiểm lại (manifest đọc tĩnh + parse_manifest); lỗi -> dọn những gì vừa tạo.
  * Skeleton trung thực: maturity M1; container/engine để E_NOT_IMPLEMENTED/trống thay vì giả vờ; chỉ kind=format/extractor
    có parse thật (dạng KEY=value) và selftest chạy được. `profile` là JSON dữ liệu (không phải code) -> chưa hỗ trợ ở đây.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from core.paths import resolve_inside
from core.safe_io import atomic_write_bytes
from sdk.manifest import KINDS

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
TEMPLATED_KINDS = ("format", "container", "engine", "extractor")
FIXTURE_REL = "tests/fixtures"

_HEAD = r'''# Sinh bởi: python3 menu.py plugin new @KIND@ @ID@
# Đọc docs/SDK_CONTRACT.md. Thêm plugin = chạy mã trên máy bạn (đặc tả 4.7); đây không phải sandbox.
# Kiểm tra: python3 menu.py plugin test @ID@
@IMPORTS@
MANIFEST = {
    "id": "@ID@",
    "name": "@NAME@",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "@KIND@",
    "capabilities": [@CAPS@],
@MODES_LINE@    "maturity": "M1",  # M2 khi parse đã test trên fixture; M3/M4 cần ghi ngược + bằng chứng (tests/corpus/@ID@.json)
    "priority": 40,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [@EXTS@]},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:@CLASS@",
    "selftest": @SELFTEST@,
    "docs": "@DOCS@",
}

'''

_KV_BODY = r'''
def _pairs(text):
    for i, ln in enumerate(text.splitlines()):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            yield i + 1, k.strip(), v.strip()


def _table(data, path, locale):
    entries, conflicts, seen = [], [], set()
    for line, k, v in _pairs(data.decode("utf-8", errors="replace")):
        if k in seen:
            conflicts.append({"key": k, "line": line})
            continue
        seen.add(k)
        src = SourceRef(container_stack=[path], rel_path=path, entry_offset=None,
                        entry_size=len(data), encoding="utf-8", extra={})
        entries.append(LocEntry(key=k, value=v, locale=None, key_origin="native", status="ok", source=src))
    return LocTable(entries=entries, locale=locale, adapter_id=MANIFEST["id"], schema_hint={},
                    stats={"entries": len(entries)}, conflicts=conflicts, warnings=[])


class @CLASS@(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx):
        return [Evidence(kind="extension", value=".txt", weight=0.2, reason="tên file gợi ý bảng text")]

    def list_candidates(self, ctx):
        out = []
        for p in ctx.vfs.iter_entries():
            if not p.lower().endswith(".txt"):
                continue
            head = ctx.vfs.read(p, max_bytes=4096)
            if b"=" in head and b"\x00" not in head:
                out.append(Candidate(path=p, adapter_id=MANIFEST["id"], score=0.5, reasons=["có dấu ="]))
        return out

    def probe(self, ctx, cand):
        text = ctx.vfs.read(cand.path, max_bytes=64 * 1024).decode("utf-8", errors="replace")
        return {"schema": "key=value", "pairs": sum(1 for _ in _pairs(text))}

    def parse(self, ctx, cand):
        return _table(ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes), cand.path, ctx.locale.code)

    def selftest(self, ctx):
        t = _table(b"a=1\n# c\nb=2\na=3\n", "selftest.txt", None)
        return {"ok": [e.key for e in t.entries] == ["a", "b"] and len(t.conflicts) == 1}

    # Không có build(): plugin chỉ đọc. Muốn ghi ngược: thêm "write" vào capabilities, "write_modes", build(), verify().
'''

_KV_MODULE = r'''# Helper của skeleton ghi được: logic bảng KEY=value (plugin.py import tương đối).
from sdk.types import LocEntry, LocTable, SourceRef


def _pairs(text):
    for i, ln in enumerate(text.splitlines()):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            yield i + 1, k.strip(), v.strip()


def _table(data, path, locale, adapter_id):
    entries, conflicts, seen = [], [], set()
    for line, k, v in _pairs(data.decode("utf-8", errors="replace")):
        if k in seen:
            conflicts.append({"key": k, "line": line})
            continue
        seen.add(k)
        src = SourceRef(container_stack=[path], rel_path=path, entry_offset=None,
                        entry_size=len(data), encoding="utf-8", extra={})
        entries.append(LocEntry(key=k, value=v, locale=None, key_origin="native", status="ok", source=src))
    return LocTable(entries=entries, locale=locale, adapter_id=adapter_id, schema_hint={},
                    stats={"entries": len(entries)}, conflicts=conflicts, warnings=[])


def build_table(table):
    """Ghi ngược bảng thành bytes KEY=value (mỗi entry một dòng, theo thứ tự bảng)."""
    out = []
    for e in table.entries:
        out.append(("%s=%s" % (e.key, e.value)).encode("utf-8"))
    return b"\n".join(out) + (b"\n" if out else b"")
'''

_KV_WRITE_BODY = r'''
from .kv import _pairs, _table, build_table


class @CLASS@(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx):
        return [Evidence(kind="extension", value=".txt", weight=0.2, reason="tên file gợi ý bảng text")]

    def list_candidates(self, ctx):
        out = []
        for p in ctx.vfs.iter_entries():
            if not p.lower().endswith(".txt"):
                continue
            head = ctx.vfs.read(p, max_bytes=4096)
            if b"=" in head and b"\x00" not in head:
                out.append(Candidate(path=p, adapter_id=MANIFEST["id"], score=0.5, reasons=["có dấu ="]))
        return out

    def probe(self, ctx, cand):
        text = ctx.vfs.read(cand.path, max_bytes=64 * 1024).decode("utf-8", errors="replace")
        return {"schema": "key=value", "pairs": sum(1 for _ in _pairs(text))}

    def parse(self, ctx, cand):
        return _table(ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes),
                      cand.path, ctx.locale.code, MANIFEST["id"])

    def build(self, ctx, table, base_bytes):
        return build_table(table)

    def verify(self, ctx, base_bytes, new_bytes):
        # Xác minh thật (so byte): verify giả (luôn ok) sẽ TRƯỢT kiểm âm của conformance.
        same = base_bytes == new_bytes
        return {"ok": same, "checked": True,
                "reason": "" if same else "nội dung khác bản gốc"}

    def selftest(self, ctx):
        t = _table(b"a=1\n# c\nb=2\na=3\n", "selftest.txt", None, MANIFEST["id"])
        ok_read = [e.key for e in t.entries] == ["a", "b"] and len(t.conflicts) == 1
        built = build_table(t)
        ok_write = built == b"a=1\nb=2\n" and self.verify(ctx, built, built)["ok"] is True
        return {"ok": ok_read and ok_write}
'''

_CONTAINER_BODY = r'''
class @CLASS@(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx):
        return []          # TODO: trả Evidence (magic/phần mở rộng) khi nhận ra container của bạn

    def open_container(self, ctx, path):
        # TODO: trả một sdk.vfs.VFS chỉ đọc cho container tại `path`. Chưa viết -> báo thật, không giả vờ.
        raise PB3Error("E_NOT_IMPLEMENTED", "open_container chưa viết", plugin_id=MANIFEST["id"])
'''

_ENGINE_BODY = r'''
class @CLASS@(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx):
        return []          # TODO: trả Evidence (có trọng số + lý do) cho engine này; không kết luận từ tên file đơn lẻ

    def hints(self, ctx):
        return {}          # TODO: gợi ý đường dẫn/locale cho pipeline (chỉ gợi ý, không parse dữ liệu)
'''

_IMPORTS_KV = "from sdk.api import Plugin\nfrom sdk.types import Candidate, Evidence, LocEntry, LocTable, SourceRef\n"
_IMPORTS_KV_WRITE = "from sdk.api import Plugin\nfrom sdk.types import Candidate, Evidence\n"
_IMPORTS_CONTAINER = "from core.errors import PB3Error\nfrom sdk.api import Plugin\nfrom sdk.types import Evidence  # noqa: F401\n"
_IMPORTS_ENGINE = "from sdk.api import Plugin\nfrom sdk.types import Evidence  # noqa: F401\n"

_DOCS = {
    "format": "Mẫu: dòng KEY=value, bỏ dòng bắt đầu bằng #. CHƯA test trên game thật; sửa mô tả này khi biết giới hạn.",
    "extractor": "Mẫu trích xuất chỉ đọc (KEY=value). CHƯA test trên game thật; sửa mô tả này khi biết giới hạn.",
    "container": "Mẫu container: open_container chưa viết (E_NOT_IMPLEMENTED). Sửa mô tả này khi viết xong.",
    "engine": "Mẫu nhận diện engine (hint): chưa có logic. Sửa mô tả này khi viết xong.",
}
_DOCS_WRITE = ("Mẫu ghi được (KEY=value, nhiều file: plugin.py + kv.py): build()/verify() "
               "ghi ngược bảng; CHƯA test trên game thật; sửa mô tả này khi biết giới hạn.")


def validate_id(plugin_id: Any) -> str:
    if not isinstance(plugin_id, str) or not ID_RE.match(plugin_id):
        raise PB3Error("E_CONFIG_INVALID", "id plugin phải khớp ^[a-z][a-z0-9_]{1,40}$ (chữ thường, số, gạch dưới)",
                       hint="Ví dụ: mygame_csv", message_vi="Tham số lệnh plugin không hợp lệ.")
    return plugin_id


def class_name(plugin_id: str) -> str:
    parts = [p for p in plugin_id.split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts) + "Plugin"


def render(kind: str, plugin_id: str, write: bool = False) -> str:
    """Mã nguồn skeleton (chuỗi). Không ghi file. write=True: skeleton ghi được
    (build/verify + write_modes), chỉ cho kind format/extractor (X15)."""
    validate_id(plugin_id)
    if kind == "profile":
        raise PB3Error("E_NOT_IMPLEMENTED", "plugin new profile chưa hỗ trợ",
                       hint="profile là JSON dữ liệu (kb/profiles/), không phải mã plugin; thuộc Claude 09.")
    if kind not in TEMPLATED_KINDS:
        raise PB3Error("E_CONFIG_INVALID", "kind phải thuộc %s, nhận %r" % ("|".join(KINDS), kind),
                       hint="Ví dụ: plugin new format mygame_csv", message_vi="Tham số lệnh plugin không hợp lệ.")
    if write and kind not in ("format", "extractor"):
        raise PB3Error("E_CONFIG_INVALID", "--write chỉ dùng với kind format/extractor",
                       hint="Ví dụ: plugin new format mygame_csv --write",
                       message_vi="Tham số lệnh plugin không hợp lệ.")
    caps = {"format": '"read"', "extractor": '"read"', "container": '"container"', "engine": '"hint"'}[kind]
    if write:
        caps += ', "write"'
    exts = '".txt"' if kind in ("format", "extractor") else '".bin"'
    imports = {"format": _IMPORTS_KV_WRITE if write else _IMPORTS_KV,
               "extractor": _IMPORTS_KV_WRITE if write else _IMPORTS_KV,
               "container": _IMPORTS_CONTAINER, "engine": _IMPORTS_ENGINE}[kind]
    body = {"format": _KV_WRITE_BODY if write else _KV_BODY,
            "extractor": _KV_WRITE_BODY if write else _KV_BODY,
            "container": _CONTAINER_BODY, "engine": _ENGINE_BODY}[kind]
    text = _HEAD + body
    name = " ".join(p[:1].upper() + p[1:] for p in plugin_id.split("_") if p)
    modes_line = '    "write_modes": ["REBUILD_ALL"],\n' if write else ""
    docs = _DOCS_WRITE if write else _DOCS[kind]
    for k, v in (("@IMPORTS@", imports), ("@CAPS@", caps), ("@EXTS@", exts), ("@MODES_LINE@", modes_line),
                 ("@SELFTEST@", "True" if kind in ("format", "extractor") else "False"),
                 ("@DOCS@", docs), ("@CLASS@", class_name(plugin_id)), ("@KIND@", kind),
                 ("@NAME@", name), ("@ID@", plugin_id)):
        text = text.replace(k, v)
    return text


def render_module(kind: str, plugin_id: str, write: bool = False) -> str:
    """Mã nguồn file phụ của skeleton nhiều file (kv.py); chỉ có khi write=True."""
    if not write:
        return ""
    return _KV_MODULE


def _exists(p) -> bool:
    return os.path.lexists(str(p))


def _rel(root, p) -> str:
    return os.path.relpath(str(p), str(root)).replace(os.sep, "/")


def generate(root, kind: str, plugin_id: str, cfg: Optional[Any] = None, write: bool = False) -> Dict[str, Any]:
    """Tạo skeleton dưới `root` (gốc dự án). Trả {"plugin_dir", "files", "fixture_dir"} (đường dẫn tương đối gốc).
    write=True: skeleton ghi được, nhiều file (plugin.py + kv.py) (X15).
    Raise PB3Error: E_CONFIG_INVALID (id/kind sai, exit 1), E_NOT_IMPLEMENTED (profile), E_OUTPUT_EXISTS (đã có), E_IO."""
    source = render(kind, plugin_id, write=write)
    user_rel = cfg.get("paths.plugins_user") if cfg is not None else "plugins_user"
    builtin_rel = cfg.get("paths.plugins") if cfg is not None else "plugins"
    user_root = resolve_inside(root, user_rel)
    target = resolve_inside(user_root, plugin_id)
    fixture = resolve_inside(root, "%s/%s" % (FIXTURE_REL, plugin_id))
    if _exists(resolve_inside(root, builtin_rel) / plugin_id):
        raise PB3Error("E_OUTPUT_EXISTS", "id '%s' trùng thư mục plugin có sẵn trong %s/" % (plugin_id, builtin_rel),
                       hint="Chọn id khác.")
    for p in (target, fixture):
        if _exists(p):
            raise PB3Error("E_OUTPUT_EXISTS", "đã có %s; PB3 không ghi đè" % _rel(root, p), hint="Chọn id khác.")
    made: List[Any] = []
    try:
        os.makedirs(str(user_root), exist_ok=True)
        os.mkdir(str(target))
        made.append(target)
        os.makedirs(str(fixture.parent), exist_ok=True)
        os.mkdir(str(fixture))
        made.append(fixture)
        atomic_write_bytes(target / "plugin.py", source.encode("utf-8"))
        extra_files = []
        if write:
            atomic_write_bytes(target / "kv.py", render_module(kind, plugin_id, write=True).encode("utf-8"))
            extra_files.append(_rel(root, target / "kv.py"))
        atomic_write_bytes(fixture / ".gitkeep", b"")
        _self_check(target, plugin_id)
    except FileExistsError:
        _cleanup(made)
        raise PB3Error("E_OUTPUT_EXISTS", "thư mục vừa được tạo bởi tiến trình khác: %s" % plugin_id)
    except PB3Error:
        _cleanup(made)
        raise
    except OSError as e:
        _cleanup(made)
        raise PB3Error("E_IO", "không tạo được skeleton: %s" % type(e).__name__)
    return {"plugin_dir": _rel(root, target), "fixture_dir": _rel(root, fixture),
            "files": [_rel(root, target / "plugin.py")] + extra_files + [_rel(root, fixture / ".gitkeep")]}


def _cleanup(made: List[Any]) -> None:
    for p in reversed(made):          # chỉ xóa thư mục do CHÍNH lần gọi này tạo
        shutil.rmtree(str(p), ignore_errors=True)


def _self_check(target, plugin_id: str) -> None:
    """Manifest sinh ra phải đọc tĩnh được và hợp lệ; nếu không là lỗi của template (E_INTERNAL), không giao cho người dùng."""
    from registry.loader import read_manifest_dict
    from sdk.manifest import parse_manifest
    try:
        raw, _src = read_manifest_dict(str(target))
        m = parse_manifest(raw)
    except PB3Error as e:
        raise PB3Error("E_INTERNAL", "skeleton sinh ra không hợp lệ: [%s] %s" % (e.code, e.detail))
    if m.id != plugin_id:
        raise PB3Error("E_INTERNAL", "skeleton sinh ra sai id")
