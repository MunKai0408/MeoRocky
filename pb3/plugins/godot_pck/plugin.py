# Plugin container Godot PCK (Claude 08, nhóm Godot): mở PCK Godot 3/4
# qua sdk.godot.PckVFS. Chỉ đọc (M1: list/read). Không giải mã entry mã
# hóa, không mở PCK pack_version 0/3+, không hỗ trợ PCK nhúng trong file
# thực thi. Kiểm tra: python3 menu.py plugin test godot_pck
from core.errors import PB3Error
from sdk.api import Plugin
from sdk.containers import open_vfs
from sdk.types import Candidate, Evidence

MANIFEST = {
    "id": "godot_pck",
    "name": "Godot PCK container",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "container",
    "capabilities": ["container"],
    "maturity": "M1",  # mở + đọc đã test trên fixture tự dựng; chưa có PCK game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["godot"],
    "match": {"extensions": [".pck"], "magic": ["0:47445043"],
              "container_kind": "godot_pck", "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:GodotPckPlugin",
    "selftest": True,
    "docs": "Mở file PCK Godot 3 (v1) / Godot 4 (v2), chỉ đọc entry không nén, "
            "không mã hóa (đường dẫn res:// bỏ tiền tố). Entry mã hóa/đã xóa "
            "bị loại kèm lý do; thư mục mã hóa, pack_version 0/3+ và PCK "
            "nhúng trong file thực thi chưa hỗ trợ.",
}

_CONTAINER_EXTS = (".pck",)
_PCK_MAGIC = b"GDPC"


def _build_selftest_pck() -> bytes:
    """PCK v2 nhỏ trong RAM (builder tối thiểu của riêng plugin, chỉ cho
    selftest): 1 entry res://hello.txt = "hello pb3". Layout như
    tests/fixtures/godot/builder.py (không import tests từ plugin)."""
    import hashlib
    import struct
    data = b"hello pb3"
    path = b"res://hello.txt"
    padded_len = (len(path) + 3) // 4 * 4
    header_size = 96
    dir_size = 4 + 4 + padded_len + 8 + 8 + 16 + 4
    file_base = (header_size + dir_size + 31) // 32 * 32
    out = bytearray()
    out += b"GDPC"
    out += struct.pack("<IIII", 2, 4, 2, 1)          # pack_version 2, Godot 4.2.1
    out += struct.pack("<IQ", 1 << 1, file_base)     # cờ RELATIVE_BASE + file_base
    out += b"\x00" * 64
    out += struct.pack("<I", 1)
    out += struct.pack("<I", padded_len) + path + b"\x00" * (padded_len - len(path))
    out += struct.pack("<QQ", 0, len(data))          # offset tương đối = 0
    out += hashlib.md5(data).digest()
    out += struct.pack("<I", 0)
    out += b"\x00" * (file_base - len(out))
    out += data
    return bytes(out)


class GodotPckPlugin(Plugin):
    MANIFEST = MANIFEST

    def _container_paths(self, ctx):
        out = []
        for p in ctx.vfs.iter_entries():
            if p.lower().endswith(_CONTAINER_EXTS):
                out.append(p)
        return sorted(out)

    def detect(self, ctx):
        ev = []
        for p in self._container_paths(ctx):
            try:
                head = ctx.vfs.read(p, max_bytes=4)
            except PB3Error:
                continue
            if head[:4] == _PCK_MAGIC:
                ev.append(Evidence(kind="magic", value="GDPC", weight=0.9,
                                   reason="magic PCK Godot ở %s" % p))
            else:
                ev.append(Evidence(kind="extension", value=".pck", weight=0.4,
                                   reason="tên %s gợi ý PCK Godot" % p))
        return ev

    def list_candidates(self, ctx):
        out = []
        for p in self._container_paths(ctx):
            try:
                head = ctx.vfs.read(p, max_bytes=4)
            except PB3Error:
                continue
            score = 0.9 if head[:4] == _PCK_MAGIC else 0.5
            out.append(Candidate(path=p, adapter_id=MANIFEST["id"], score=score,
                                 reasons=["container PCK Godot"]))
        return out

    def probe(self, ctx, cand):
        try:
            data = ctx.vfs.read(cand.path, None)
            vfs = open_vfs(data, limits=ctx.limits, source_name=cand.path)
            try:
                inv = vfs.inventory()
            finally:
                vfs.close()
            return {"ok": True, "kind": inv.kind, "entries": len(inv.entries),
                    "rejected": len(inv.rejected), "warnings": inv.warnings}
        except PB3Error as e:
            return {"ok": False, "reason": "%s: %s" % (e.code, e.detail)}

    def open_container(self, ctx, path):
        data = ctx.vfs.read(path, None)
        return open_vfs(data, limits=ctx.limits, source_name=path)

    def selftest(self, ctx):
        vfs = open_vfs(_build_selftest_pck(), limits=ctx.limits,
                       source_name="selftest.pck")
        try:
            ok = vfs.read("hello.txt", None) == b"hello pb3"
        finally:
            vfs.close()
        return {"ok": ok, "checked": "pck v2 trong RAM: 1 entry",
                "reason": "" if ok else "đọc lại sai"}
