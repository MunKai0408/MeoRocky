# plugins_user/myengine/plugin.py
from sdk.api import Plugin, api_version
from sdk.types import Evidence, Candidate, LocEntry, LocTable, SourceRef

MANIFEST = {
    "id": "myengine_text",
    "name": "MyEngine text table",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read"],
    "maturity": "M2",
    "priority": 40,
    "true_cost": "cheap",
    "engines": ["myengine", "generic"],
    "match": {"extensions": [".txt"], "name_regex": "(?i)(lang|text)"},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:MyEnginePlugin",
    "selftest": True,
    "docs": "Dòng dạng KEY=value, bỏ qua dòng bắt đầu bằng #.",
}

class MyEnginePlugin(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx):
        return [Evidence(kind="extension", value=".txt", weight=0.2,
                         reason="tên file gợi ý bảng text")]

    def list_candidates(self, ctx):
        out = []
        for p in ctx.vfs.iter_entries():
            if not p.lower().endswith(".txt"):
                continue
            head = ctx.vfs.read(p, max_bytes=4096)
            if b"=" in head and b"\x00" not in head:
                out.append(Candidate(path=p, adapter_id=MANIFEST["id"],
                                     score=0.5, reasons=["có dấu ="]))
        return out

    def probe(self, ctx, cand):
        data = ctx.vfs.read(cand.path, max_bytes=64 * 1024)
        text = data.decode("utf-8", errors="replace")
        n = sum(1 for ln in text.splitlines() if "=" in ln and ln.strip())
        return {"schema": "key=value", "pairs": n}

    def parse(self, ctx, cand):
        data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
        text = data.decode("utf-8", errors="replace")
        entries, conflicts = [], []
        seen = set()
        for i, ln in enumerate(text.splitlines()):
            s = ln.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in seen:
                conflicts.append({"key": k, "line": i + 1})
                continue
            seen.add(k)
            entries.append(LocEntry(
                key=k, value=v, locale=None, key_origin="native",
                status="ok",
                source=SourceRef(container_stack=[cand.path], rel_path=cand.path,
                                 entry_offset=None, entry_size=len(data),
                                 encoding="utf-8", extra={})))
        return LocTable(entries=entries, locale=ctx.locale.code,
                        adapter_id=MANIFEST["id"], schema_hint={},
                        stats={"entries": len(entries)},
                        conflicts=conflicts, warnings=[])

    # Không có build() -> capability "read" (chưa hỗ trợ ghi ngược).
