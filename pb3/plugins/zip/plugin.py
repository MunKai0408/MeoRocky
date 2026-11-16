# Plugin container ZIP/IPA (Claude 04): mở ZIP/IPA qua sdk.containers.
# Chỉ đọc. Ghi lại container do lớp gọi dùng sdk.containers.rebuild_zip
# (kiểm capability 'write'/'write_modes' ở planner, Claude 05).
# Kiểm tra: python3 menu.py plugin test zip
from core.errors import PB3Error
from sdk.api import Plugin
from sdk.containers import open_vfs
from sdk.types import Candidate, Evidence

MANIFEST = {
    "id": "zip",
    "name": "ZIP/IPA container",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "container",
    "capabilities": ["container"],
    "maturity": "M2",  # mở + đọc đã test trên fixture; chưa có bằng chứng game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".zip", ".ipa"], "magic": ["0:504B0304", "0:504B0506"],
              "container_kind": "zip", "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:ZipPlugin",
    "selftest": True,
    "docs": "Mở ZIP/IPA (đọc, streaming, container lồng qua '::'). Chỉ đọc; "
            "không giải mã entry mã hóa, không mở 7z.",
}

_CONTAINER_EXTS = (".zip", ".ipa")
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


class ZipPlugin(Plugin):
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
            if head[:4] in _ZIP_MAGICS:
                ev.append(Evidence(kind="magic", value="PK", weight=0.9,
                                   reason="magic ZIP ở %s" % p))
            else:
                ev.append(Evidence(kind="extension", value=".zip", weight=0.4,
                                   reason="tên %s gợi ý ZIP" % p))
        return ev

    def list_candidates(self, ctx):
        out = []
        for p in self._container_paths(ctx):
            try:
                head = ctx.vfs.read(p, max_bytes=4)
            except PB3Error:
                continue
            score = 0.9 if head[:4] in _ZIP_MAGICS else 0.5
            out.append(Candidate(path=p, adapter_id=MANIFEST["id"], score=score,
                                 reasons=["container ZIP/IPA"]))
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
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("hello.txt", b"hello pb3")
        vfs = open_vfs(buf.getvalue(), limits=ctx.limits, source_name="selftest.zip")
        try:
            ok = vfs.read("hello.txt", None) == b"hello pb3"
        finally:
            vfs.close()
        return {"ok": ok, "checked": "zip trong RAM: 1 entry",
                "reason": "" if ok else "đọc lại sai"}
