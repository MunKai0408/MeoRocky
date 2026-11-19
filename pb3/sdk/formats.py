"""Lớp nền dùng chung cho plugin định dạng Claude 06 (JSON, CSV, XML, YAML/INI,
Properties, plist, .strings, SQLite, PO/MO, XLIFF, RESX, extractor chuỗi).

Plugin định dạng kế thừa `FormatPluginBase` và chỉ cài 3 hook chuyên biệt:
    _parse_bytes(data, path, locale) -> dict  # entries/schema_hint/stats/conflicts/warnings
    _build_bytes(table, base_bytes) -> bytes  # chỉ khi capability 'write'
    _sniff(head, path) -> float               # độ tin cậy nội dung (0.0 = không có ý kiến)
Lớp nền lo: detect / list_candidates / probe / parse / build / verify / selftest
theo đúng hợp đồng SDK (đặc tả 4.2), kiểm tra mã lỗi, bảo toàn thứ tự key,
báo xung đột key trùng (giữ bản ghi đầu, đặc tả 7.5), và từ chối ghi ở plugin
chỉ đọc bằng E_WRITE_DISABLED.

Quy tắc verify (đặc tả 6.7 + conformance): verify giải mã nghiêm ngặt + parse
lại cả hai file rồi so tập key theo đúng thứ tự; byte lạ (lật 1 byte) trong
văn bản ASCII làm hỏng giải mã UTF-8 nên bị từ chối. Plugin định dạng nhị
phân mà verify không phân biệt được byte lật thì KHÔNG khai 'write' (chỉ đọc,
M2) — xem apple_plist (binary plist) và po_mo (MO).

Chỉ thư viện chuẩn, Python >= 3.8. Không import UnityPy/lz4 (quy tắc iSH).
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.api import Plugin, PluginContext
from sdk.types import Candidate, Evidence, LocEntry, LocTable, SourceRef

# --------------------------------------------------------------------------- mã hóa
UTF8_BOM = b"\xef\xbb\xbf"
UTF16LE_BOM = b"\xff\xfe"
UTF16BE_BOM = b"\xfe\xff"
UTF32LE_BOM = b"\xff\xfe\x00\x00"
UTF32BE_BOM = b"\x00\x00\xfe\xff"


class TextDoc:
    """Văn bản đã giải mã + thông tin để ghi ngược đúng encoding/BOM gốc."""

    def __init__(self, text: str, encoding: str, bom: bool):
        self.text = text
        self.encoding = encoding      # 'utf-8' | 'utf-16-le' | 'utf-16-be'
        self.bom = bom

    def encode(self, text: Optional[str] = None) -> bytes:
        raw = (self.text if text is None else text).encode(self.encoding)
        if not self.bom:
            return raw
        if self.encoding == "utf-8":
            return UTF8_BOM + raw
        if self.encoding == "utf-16-le":
            return UTF16LE_BOM + raw
        return UTF16BE_BOM + raw


def detect_encoding(data: bytes, where: str = "") -> Tuple[str, bool, int]:
    """(encoding, có BOM, độ dài BOM). UTF-32 -> E_UNSUPPORTED_FORMAT (không hỗ trợ).
    Không BOM: thử UTF-8 nghiêm ngặt, rồi UTF-16 theo mẫu byte NUL; không khớp -> E_CORRUPT."""
    if data.startswith(UTF32LE_BOM) or data.startswith(UTF32BE_BOM):
        raise PB3Error("E_UNSUPPORTED_FORMAT", "UTF-32 chưa hỗ trợ", where=where)
    if data.startswith(UTF8_BOM):
        return "utf-8", True, 3
    if data.startswith(UTF16LE_BOM):
        return "utf-16-le", True, 2
    if data.startswith(UTF16BE_BOM):
        return "utf-16-be", True, 2
    try:
        data.decode("utf-8")
        return "utf-8", False, 0
    except UnicodeDecodeError:
        pass
    # UTF-16 không BOM: văn bản ASCII/Latin có byte NUL xen kẽ đều đặn.
    if len(data) >= 4 and b"\x00" in data:
        even_nul = sum(1 for i in range(1, min(len(data), 200), 2) if data[i] == 0)
        odd_nul = sum(1 for i in range(0, min(len(data), 200), 2) if data[i] == 0)
        probe = min(len(data), 200) // 2
        if probe and even_nul >= probe * 0.8:
            try:
                data.decode("utf-16-le")
                return "utf-16-le", False, 0
            except UnicodeDecodeError:
                pass
        if probe and odd_nul >= probe * 0.8:
            try:
                data.decode("utf-16-be")
                return "utf-16-be", False, 0
            except UnicodeDecodeError:
                pass
    raise PB3Error("E_CORRUPT", "không giải mã được (không phải UTF-8/UTF-16)", where=where)


def decode_text(data: bytes, where: str = "") -> TextDoc:
    """Giải mã nghiêm ngặt; lỗi -> E_CORRUPT (không đoán mò, không thay ký tự)."""
    enc, bom, n = detect_encoding(data, where)
    try:
        return TextDoc(data[n:].decode(enc), enc, bom)
    except UnicodeDecodeError:
        raise PB3Error("E_CORRUPT", "giải mã %s thất bại" % enc, where=where)


# --------------------------------------------------------------------------- key ổn định
def stable_row_key(i: int) -> str:
    """Key theo vị trí ổn định, tái lập được (đặc tả 7.5): row_000001..."""
    return "row_%06d" % i


def stable_hash_key(text: str) -> str:
    """Key theo hash nội dung (đặc tả 7.5): s_<sha1 8 ký tự>."""
    return "s_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- dựng bảng
def make_entries(pairs: List[Tuple], path: str, size: int,
                 encoding: str, locale: Optional[str]) -> Tuple[List[LocEntry], List[Dict[str, Any]]]:
    """pairs = (key, value, status, notes[, locale_riêng]). Key trùng (cùng locale):
    giữ bản ghi đầu (đặc tả 7.5), bản sau status='conflict' + ghi vào conflicts.
    Mọi entry đều có source."""
    entries: List[LocEntry] = []
    conflicts: List[Dict[str, Any]] = []
    seen = set()
    # SPEED-EXTRACT (2026-11-18): mọi entry trong 1 lần gọi đều chung nguồn
    # (cùng path/size/encoding) nên chỉ dựng 1 SourceRef rồi dùng chung —
    # trước đây dựng 1 SourceRef MỚI cho mỗi entry (9K: 161.100 SourceRef,
    # 0,41 s tottime + 0,92 s cumulative trong __post_init__ [ĐÃ ĐO THẬT]
    # bằng profile). SourceRef dùng chung KHÔNG được sửa sau khi tạo (không
    # có code nào sửa entry.source — xem grep .source.*=); notes vẫn là list
    # riêng từng entry vì _normalize_table có append vào e.notes.
    src = SourceRef([path], path, None, size, encoding, {})
    for item in pairs:
        if len(item) == 5:
            key, value, status, notes, loc = item
        else:
            key, value, status, notes = item
            loc = locale
        dup = (loc, key) in seen
        seen.add((loc, key))
        if dup:
            conflicts.append({"key": key, "locale": loc})
            status = "conflict"
        elif not value:
            status = "empty"
        entries.append(LocEntry(key, value, loc, "native", status, src, list(notes or [])))
    return entries, conflicts


def value_queue(table: LocTable) -> Dict[str, List[str]]:
    """Hàng đợi giá trị theo key, giữ đúng thứ tự xuất hiện (cho build)."""
    queue: Dict[str, List[str]] = {}
    for e in table.entries:
        queue.setdefault(e.key, []).append(e.value)
    return queue


# --------------------------------------------------------------------------- lớp nền
class FormatPluginBase(Plugin):
    """Lớp nền cho plugin định dạng. Lớp con đặt EXTENSIONS, FLAVOR và cài
    _parse_bytes (+ _build_bytes nếu ghi được, + _sniff để tăng tin cậy)."""
    EXTENSIONS: Tuple[str, ...] = ()
    FLAVOR = "format"
    SELFTEST_BASE: bytes = b""
    SELFTEST_VALUE = "GIÁ TRỊ MỚI"

    # ---- nhận diện ----
    def _candidate_paths(self, ctx: PluginContext) -> List[str]:
        exts = self.EXTENSIONS
        return sorted(p for p in ctx.vfs.iter_entries()
                      if p.lower().endswith(exts)) if exts else []

    def _sniff(self, head: bytes, path: str) -> float:
        """Độ tin cậy nội dung 0.0..1.0 (0.0 = không có ý kiến, chỉ dựa đuôi file)."""
        return 0.0

    def _is_writable(self, path: str, head: bytes) -> bool:
        """Định dạng con nào ghi được (vd: plist XML ghi được, binary thì không)."""
        return True

    def _score(self, head: bytes, path: str) -> float:
        sniff = self._sniff(head, path)
        if sniff >= 0.5:
            return min(0.9, 0.5 + 0.4 * sniff)
        if sniff > 0.0:
            return 0.3
        return 0.5

    def _evidence_for(self, path: str, head: bytes) -> List[Evidence]:
        sniff = self._sniff(head, path)
        ev = [Evidence("extension", path.rsplit(".", 1)[-1].lower(), 0.4,
                       "đuôi %s gợi ý %s" % (path.rsplit(".", 1)[-1].lower(), self.FLAVOR))]
        if sniff >= 0.7:
            ev.append(Evidence("content", self.FLAVOR, 0.75,
                               "nội dung %s giống %s" % (path, self.FLAVOR)))
        return ev

    def detect(self, ctx: PluginContext) -> List[Evidence]:
        out: List[Evidence] = []
        for p in self._candidate_paths(ctx):
            try:
                head = ctx.vfs.read(p, max_bytes=4096)
            except PB3Error:
                continue
            out.extend(self._evidence_for(p, head))
        return out

    def list_candidates(self, ctx: PluginContext) -> List[Candidate]:
        found = []
        for p in self._candidate_paths(ctx):
            try:
                head = ctx.vfs.read(p, max_bytes=4096)
            except PB3Error:
                continue
            score = self._score(head, p)
            found.append((not self._is_writable(p, head), -score, p, score, head))
        # Ghi được lên trước (conformance kiểm write trên bảng đầu tiên), rồi điểm cao, rồi tên.
        # head giữ theo để _evidence_for dùng lại — trước đây đọc lại head
        # lần 2 cho mỗi ứng viên (tích hợp đợt tổng cuối). p là duy nhất
        # nên head không bao giờ tham gia so sánh sắp xếp.
        found.sort()
        return [Candidate(p, self.id, s, ["%s (%s)" % (self.FLAVOR, p)],
                          self._evidence_for(p, head))
                for _nw, _ns, p, s, head in found]

    # ---- đọc ----
    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        """Lớp con cài: trả dict {entries, schema_hint, stats, conflicts, warnings}
        với entries = list[LocEntry] (đã có source)."""
        raise PB3Error("E_NOT_IMPLEMENTED", "_parse_bytes", plugin_id=self.id)

    def probe(self, ctx: PluginContext, cand: Candidate) -> Dict[str, Any]:
        try:
            data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
            # SPEED-EXTRACT (2026-11-18): plugin nào có _probe_bytes (đếm
            # nhẹ — chỉ trả số lượng, KHÔNG dựng LocEntry) thì dùng nó:
            # probe toàn pipeline chỉ cần ok + entries>0 cho trọng tài
            # (xem _contender), còn test test_probe_counts_entries khóa số
            # entries CHÍNH XÁC nên _probe_bytes phải đếm đúng bằng _parse_bytes
            # (kể cả key trùng) hoặc fallback về _parse_bytes đầy đủ.
            hook = getattr(self, "_probe_bytes", None)
            if callable(hook):
                counts = hook(data, cand.path, ctx.locale.code)
                return {"ok": True, "schema": self.FLAVOR,
                        "entries": int(counts.get("entries", 0)),
                        "warnings": int(counts.get("warnings", 0)),
                        "conflicts": int(counts.get("conflicts", 0))}
            res = self._parse_bytes(data, cand.path, ctx.locale.code)
            return {"ok": True, "schema": self.FLAVOR, "entries": len(res["entries"]),
                    "warnings": len(res.get("warnings", [])),
                    "conflicts": len(res.get("conflicts", []))}
        except PB3Error as e:
            return {"ok": False, "reason": "%s: %s" % (e.code, e.detail or e.message_vi)}
        except Exception as e:  # noqa: BLE001 - probe không bao giờ được làm crash extract
            return {"ok": False, "reason": "E_INTERNAL: %s" % type(e).__name__}

    def parse(self, ctx: PluginContext, cand: Candidate) -> LocTable:
        data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
        if len(data) > self.manifest.max_entry_bytes:
            raise PB3Error("E_TOO_LARGE", "file %d byte > giới hạn %d"
                           % (len(data), self.manifest.max_entry_bytes), where=cand.path)
        res = self._parse_bytes(data, cand.path, ctx.locale.code)
        t = LocTable(res["entries"], ctx.locale.code, self.id,
                     dict(res.get("schema_hint") or {"flavor": self.FLAVOR}),
                     dict(res.get("stats") or {}),
                     list(res.get("conflicts") or []), list(res.get("warnings") or []))
        t.stats = {"entries": len(t.entries), **t.count_by_status(), **(t.stats or {})}
        return t

    # ---- ghi ----
    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        raise PB3Error("E_NOT_IMPLEMENTED", "_build_bytes", plugin_id=self.id)

    def build(self, ctx: PluginContext, table: LocTable, base_bytes: bytes) -> bytes:
        if not self.manifest.can_write:
            raise PB3Error("E_WRITE_DISABLED", "plugin chỉ đọc", plugin_id=self.id)
        try:
            base_parsed = self._parse_bytes(base_bytes, "<gốc>", table.locale)
        except PB3Error as e:
            raise PB3Error("E_VERIFY_FAILED", "không parse được file gốc: %s" % e.code, plugin_id=self.id)
        base_keys = {e.key for e in base_parsed["entries"]}
        for e in table.entries:
            if e.key not in base_keys:
                table.warnings.append({"code": "E_PARSE_PARTIAL", "key": e.key,
                                       "msg": "key không có trong file, không ghi thêm"})
        new = self._build_bytes(table, base_bytes)
        # Tự xác minh trước khi trả (đặc tả 6.7): đọc lại phải khớp đúng giá trị cần ghi.
        try:
            back = self._parse_bytes(new, "<build>", table.locale)
        except PB3Error as e:
            raise PB3Error("E_VERIFY_FAILED", "đọc lại sau khi ghi lỗi: %s" % e.code, plugin_id=self.id)
        want = [(e.key, e.value) for e in table.entries
                if e.status in ("ok", "empty") and e.key in base_keys]
        got = {(e.key, e.value) for e in back["entries"] if e.status in ("ok", "empty", "conflict")}
        missing = [kv for kv in want if kv not in got]
        if missing:
            raise PB3Error("E_VERIFY_FAILED", "đọc lại thiếu %d giá trị cần ghi" % len(missing),
                           plugin_id=self.id)
        return new

    def _verify_bytes(self, base_bytes: bytes, new_bytes: bytes) -> Dict[str, Any]:
        """Mặc định: giải mã nghiêm ngặt + parse lại cả hai, so tập key theo đúng
        thứ tự. Byte lạ trong văn bản làm hỏng giải mã/parse nên bị từ chối."""
        try:
            base = self._parse_bytes(base_bytes, "<gốc>", None)
            new = self._parse_bytes(new_bytes, "<mới>", None)
        except PB3Error as e:
            return {"ok": False, "checked": True, "reason": "parse lại lỗi: %s" % e.code}
        keys_base = [e.key for e in base["entries"]]
        keys_new = [e.key for e in new["entries"]]
        if keys_base != keys_new:
            return {"ok": False, "checked": True,
                    "reason": "tập key khác gốc (%d -> %d)" % (len(keys_base), len(keys_new))}
        return {"ok": True, "checked": True, "changed": sum(
            1 for a, b in zip(base["entries"], new["entries"]) if a.value != b.value)}

    def verify(self, ctx: PluginContext, base_bytes: bytes, new_bytes: bytes) -> Dict[str, Any]:
        if not self.manifest.can_write:
            raise PB3Error("E_WRITE_DISABLED", "plugin chỉ đọc", plugin_id=self.id)
        return self._verify_bytes(base_bytes, new_bytes)

    # ---- tự kiểm tra ----
    def selftest(self, ctx: PluginContext) -> Dict[str, Any]:
        base = type(self).SELFTEST_BASE
        if not base:
            return {"ok": None, "skipped": True, "reason": "plugin chưa đặt SELFTEST_BASE"}
        try:
            table = self._parse_bytes(base, "selftest", None)
            entries = table["entries"]
            if not entries:
                return {"ok": False, "checked": "selftest: không parse được entry nào"}
            if not self.manifest.can_write:
                # Plugin chỉ đọc: selftest chỉ cần parse được, không build.
                return {"ok": True,
                        "checked": "selftest: chỉ đọc, parse được %d entry" % len(entries),
                        "reason": ""}
            first = entries[0]
            edited = LocTable([LocEntry(first.key, type(self).SELFTEST_VALUE, None, "native",
                                        "ok", SourceRef(["selftest"], "selftest"))],
                              None, self.id)
            new = self.build(ctx, edited, base)
            v = self.verify(ctx, base, new)
            back = self._parse_bytes(new, "selftest", None)
            got = {(e.key, e.value) for e in back["entries"]}
            ok = bool(v.get("ok")) and (first.key, type(self).SELFTEST_VALUE) in got
            return {"ok": ok, "checked": "selftest: 1 entry đổi giá trị, verify ok=%s" % (v.get("ok"),),
                    "reason": "" if ok else "giá trị sau khi ghi không khớp"}
        except PB3Error as e:
            return {"ok": False, "checked": "selftest", "reason": "%s: %s" % (e.code, e.detail or e.message_vi)}
