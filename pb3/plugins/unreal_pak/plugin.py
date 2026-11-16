# Plugin container Unreal PAK (Claude 08, nhóm Unreal): mở PAK UE4/UE5 qua sdk.unreal.
# Chỉ đọc entry không nén, không mã hóa (xem sdk/unreal.py). PAK không có
# magic ở đầu file nên detect/list_candidates chỉ dựa vào đuôi ".pak";
# footer (magic 0x5A6F12E1 ở cuối file) được kiểm ở probe/open_container.
# Kiểm tra: python3 menu.py plugin test unreal_pak
from __future__ import annotations

from core.errors import PB3Error
from sdk.api import Plugin
from sdk.containers import open_vfs
from sdk.types import Candidate, Evidence
from sdk.unreal import PakVFS, build_pak

MANIFEST = {
    "id": "unreal_pak",
    "name": "Unreal PAK container",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "container",
    "capabilities": ["container"],
    "maturity": "M1",  # list/read đã test trên fixture V8B/V11; chưa có bằng chứng game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["unreal"],
    "match": {"extensions": [".pak"], "container_kind": "unreal_pak",
              "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:UnrealPakPlugin",
    "selftest": True,
    "docs": "Mở file PAK của Unreal Engine (UE4/UE5, chỉ đọc): liệt kê và đọc "
            "entry không nén, không mã hóa. PAK không có magic ở đầu file nên "
            "nhận diện dựa vào đuôi .pak + kiểm footer ở probe. Entry nén "
            "(Zlib/Oodle/...) hoặc mã hóa bị loại kèm lý do rõ, không giải mã. "
            "Đã kiểm chứng bằng fixture: footer V8B (UE 4.23-4.24) và V11 "
            "(UE 4.26-5.3); index mã hóa -> E_ENCRYPTED. Không ghi PAK.",
}

_CONTAINER_EXTS = (".pak",)


class UnrealPakPlugin(Plugin):
    MANIFEST = MANIFEST

    def _container_paths(self, ctx):
        out = []
        for p in ctx.vfs.iter_entries():
            if p.lower().endswith(_CONTAINER_EXTS):
                out.append(p)
        return sorted(out)

    def detect(self, ctx):
        # Chỉ đuôi file: đọc cả file .pak để kiểm footer trong detect thì
        # quá nặng (file game có thể hàng GB); footer do probe kiểm.
        ev = []
        for p in self._container_paths(ctx):
            ev.append(Evidence(kind="extension", value=".pak", weight=0.4,
                               reason="tên %s gợi ý PAK (footer kiểm ở probe)" % p))
        return ev

    def list_candidates(self, ctx):
        out = []
        for p in self._container_paths(ctx):
            out.append(Candidate(path=p, adapter_id=MANIFEST["id"], score=0.5,
                                 reasons=["container PAK (đuôi .pak, chờ probe kiểm footer)"]))
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
                    "rejected": len(inv.rejected), "warnings": inv.warnings,
                    "pak_version": vfs.pak_version_name,
                    "mount_point": vfs.mount_point}
        except PB3Error as e:
            return {"ok": False, "reason": "%s: %s" % (e.code, e.detail)}

    def open_container(self, ctx, path):
        data = ctx.vfs.read(path, None)
        return open_vfs(data, limits=ctx.limits, source_name=path)

    def selftest(self, ctx):
        try:
            data = build_pak([
                ("TestGame/Content/UI/menu.txt", "Chào mừng đến với trò chơi!".encode("utf-8")),
                ("TestGame/Content/Text/hello.txt", "Xin chào".encode("utf-8")),
            ], version=8)
            vfs = PakVFS(data, limits=ctx.limits, source_name="selftest.pak")
            try:
                names = list(vfs.iter_entries())
                ok = names == ["TestGame/Content/UI/menu.txt",
                               "TestGame/Content/Text/hello.txt"] \
                    and vfs.read("TestGame/Content/Text/hello.txt", None) == "Xin chào".encode("utf-8")
            finally:
                vfs.close()
            return {"ok": ok, "checked": "pak V8B trong RAM: 2 entry, đọc lại đúng",
                    "reason": "" if ok else "đọc lại sai"}
        except PB3Error as e:
            return {"ok": False, "checked": "selftest",
                    "reason": "%s: %s" % (e.code, e.detail or e.message_vi)}
