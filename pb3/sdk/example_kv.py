"""Plugin ví dụ TỐI THIỂU theo API v3: file .kv ('key=value', dòng '#' là comment). Đọc + ghi ngược (build).

Mã hóa giá trị: '\\' -> '\\\\', xuống dòng -> '\\n'. build() chỉ đổi giá trị của key đã có, giữ nguyên thứ tự,
comment, dòng lạ; key thiếu trong file -> cảnh báo trong table.warnings (không tự thêm); tự parse lại để xác
minh trước khi trả bytes. KHÔNG ghi vào VFS: bytes trả về do planner/patcher ghi ra (mục 6).
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from core.errors import PB3Error
from sdk.api import Plugin, PluginContext
from sdk.types import Candidate, Evidence, LocEntry, LocTable, SourceRef

MANIFEST = {
    "id": "example_kv", "name": "Ví dụ .kv", "version": "1.0.0", "api_version": 3, "kind": "format",
    "capabilities": ["read", "write"], "maturity": "M2", "priority": 10, "true_cost": "cheap",
    "engines": ["generic"], "match": {"extensions": [".kv"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 5},
    "entry": "sdk.example_kv:KvPlugin", "selftest": True,
    "docs": "Chỉ UTF-8, một giá trị mỗi dòng 'key=value'. Chưa test trên game thật (chỉ fixture).",
}


def enc(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n")


def dec(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append("\\" + nxt)   # escape lạ: giữ nguyên, không nuốt backslash
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_text(text: str) -> List[Tuple[str, str]]:
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]      # CRLF: bỏ \r cuối dòng
    return [(ln.split("=", 1)[0], dec(ln.split("=", 1)[1]))
            for ln in lines if not ln.startswith("#") and "=" in ln]


def _decode(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise PB3Error("E_CORRUPT", "không phải utf-8", where=where)


class KvPlugin(Plugin):
    MANIFEST = MANIFEST

    def detect(self, ctx: PluginContext) -> List[Evidence]:
        return [Evidence("extension", ".kv", 0.1, "tên file gợi ý bảng key=value")]

    def list_candidates(self, ctx: PluginContext) -> List[Candidate]:
        out = []
        for p in ctx.vfs.iter_entries():
            if p.endswith(".kv"):
                head = ctx.vfs.read(p, max_bytes=4096)
                if b"=" in head and b"\x00" not in head:
                    out.append(Candidate(p, self.id, 0.5, ["đuôi .kv và có dấu ="],
                                         [Evidence("extension", ".kv", 0.1, "đuôi .kv")]))
        return out

    def probe(self, ctx: PluginContext, cand: Candidate) -> Dict[str, Any]:
        # Đọc cùng phạm vi với parse() (max_entry_bytes) để số cặp khớp nhau;
        # trước đây probe chỉ đọc 64 KiB đầu nên đếm thiếu trên file lớn.
        text = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes).decode("utf-8", errors="replace")
        return {"schema": "key=value", "pairs": len(parse_text(text))}

    def parse(self, ctx: PluginContext, cand: Candidate) -> LocTable:
        data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
        entries, conflicts, seen = [], [], set()
        for k, v in parse_text(_decode(data, cand.path)):
            src = SourceRef([cand.path], cand.path, None, len(data), "utf-8", {})
            dup = k in seen
            seen.add(k)
            if dup:
                conflicts.append({"key": k})
            entries.append(LocEntry(k, v, ctx.locale.code, "native", "conflict" if dup else ("ok" if v else "empty"),
                                    src))
        t = LocTable(entries, ctx.locale.code, self.id, {"flavor": "kv"}, {}, conflicts, [])
        t.stats = {"entries": len(entries), **t.count_by_status()}
        return t

    def build(self, ctx: PluginContext, table: LocTable, base_bytes: bytes) -> bytes:
        queue: Dict[str, List[str]] = {}
        for e in table.entries:
            queue.setdefault(e.key, []).append(e.value)
        spare = {k: list(v) for k, v in queue.items()}
        text = _decode(base_bytes, "base")
        expected = [(k, spare[k].pop(0) if spare.get(k) else v) for k, v in parse_text(text)]
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if ln.startswith("#") or "=" not in ln:
                continue
            cr = "\r" if ln.endswith("\r") else ""                                      # giữ nguyên CRLF
            k = ln.split("=", 1)[0]
            if queue.get(k):
                lines[i] = k + "=" + enc(queue[k].pop(0)) + cr
        for k, v in queue.items():
            if v:
                table.warnings.append({"code": "E_PARSE_PARTIAL", "key": k, "msg": "key không có trong file"})
        new = "\n".join(lines).encode("utf-8")
        if parse_text(new.decode("utf-8")) != expected:
            raise PB3Error("E_VERIFY_FAILED", "đọc lại khác giá trị cần ghi", plugin_id=self.id)
        return new

    def verify(self, ctx: PluginContext, base_bytes: bytes, new_bytes: bytes) -> Dict[str, Any]:
        a, b = _decode(base_bytes, "base").split("\n"), _decode(new_bytes, "new").split("\n")
        changed = [i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y]
        ok = len(a) == len(b) and all(a[i - 1].startswith("#") == b[i - 1].startswith("#") for i in changed)
        return {"ok": ok, "checked": True, "changed_lines": changed}

    def selftest(self, ctx: PluginContext) -> Dict[str, Any]:
        base = b"# c\nx=1\ny=a\\nb"
        t = LocTable([LocEntry("x", "9", None, "native", "ok", SourceRef(["m.kv"], "m.kv"))], None, self.id)
        new = self.build(ctx, t, base)
        return {"ok": new == b"# c\nx=9\ny=a\\nb" and self.verify(ctx, base, new)["ok"]}
