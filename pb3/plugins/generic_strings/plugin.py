# Plugin extractor dò chuỗi thô (Claude 06): lưới cuối cho file không thuộc định dạng
# đã biết. Quét bytes tìm chuỗi ASCII in được (>= 4 ký tự 0x20-0x7E) và chuỗi
# UTF-16LE/BE (>= 4 ký tự trong cùng khoảng in được); mỗi chuỗi thành 1 entry với
# key = "off_%08x" % offset (ổn định, tái lập được) và source.entry_offset = offset.
# CHỈ ĐỌC (kind extractor, M2): không có build/verify, lớp nền tự từ chối ghi bằng
# E_WRITE_DISABLED. Giới hạn 5000 entry + cảnh báo ghi rõ tổng số chuỗi tìm được;
# table.warnings LUÔN có cảnh báo dò chuỗi thô (có thể có false positive).
# Giới hạn trung thực: chuỗi tìm được không phải bảng localization đã xác minh — thứ tự
# và offset có thể đổi khi file gốc đổi, chuỗi UTF-16 ngoài khoảng ASCII in được
# không được dò, chuỗi chồng lấn thì chỉ giữ chuỗi bắt đầu sớm nhất/dài nhất.
# Kiểm tra: python3 menu.py plugin test generic_strings
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.api import PluginContext
from sdk.formats import FormatPluginBase
from sdk.types import Candidate, Evidence, LocEntry, LocTable, SourceRef

MANIFEST = {
    "id": "generic_strings",
    "name": "Dò chuỗi thô",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "extractor",
    "capabilities": ["read"],
    "maturity": "M2",
    "priority": 1,
    "true_cost": "heavy",
    "engines": ["generic"],
    "match": {"extensions": []},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 20},
    "entry": "plugin:GenericStringsPlugin",
    "selftest": True,
    "docs": "Extractor chỉ đọc, lưới cuối cho file không thuộc định dạng đã biết: dò chuỗi "
            "ASCII in được và chuỗi UTF-16LE/BE (>= 4 ký tự), key theo offset. Không phải "
            "bảng localization đã xác minh (có thể có false positive), tối đa 5000 entry. "
            "Không dò chuỗi UTF-16 ngoài khoảng ASCII in được; chuỗi chồng lấn chỉ giữ "
            "chuỗi bắt đầu sớm nhất/dài nhất.",
}

_KNOWN_EXTS = frozenset([
    ".json", ".csv", ".tsv", ".xml", ".yaml", ".yml", ".ini", ".properties",
    ".plist", ".stringsdict", ".xcstrings", ".strings", ".sqlite", ".db",
    ".sqlite3", ".db3", ".po", ".mo", ".xlf", ".xliff", ".resx", ".zip",
    ".ipa", ".unity3d", ".assets",
])
_MAX_ENTRIES = 5000
_ASCII_RE = re.compile(rb"[\x20-\x7e]{4,}")
_UTF16LE_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
_UTF16BE_RE = re.compile(rb"(?:\x00[\x20-\x7e]){4,}")
_RAW_WARNING = ("dò chuỗi thô, không phải bảng localization đã xác minh "
                "(có thể có false positive)")


def _scan(data: bytes) -> List[Tuple[int, int, str, str]]:
    """Trả [(offset, end, giá trị, encoding)] đã khử chồng lấn, theo thứ tự offset.
    Chồng lấn: chỉ giữ chuỗi bắt đầu sớm nhất, dài nhất tại cùng offset."""
    found: List[Tuple[int, int, str, str]] = []
    for m in _ASCII_RE.finditer(data):
        found.append((m.start(), m.end(), m.group().decode("ascii"), "utf-8"))
    for m in _UTF16LE_RE.finditer(data):
        found.append((m.start(), m.end(), m.group().decode("utf-16-le"), "utf-16-le"))
    for m in _UTF16BE_RE.finditer(data):
        found.append((m.start(), m.end(), m.group().decode("utf-16-be"), "utf-16-be"))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    out: List[Tuple[int, int, str, str]] = []
    last_end = -1
    for item in found:
        if item[0] < last_end:
            continue
        out.append(item)
        last_end = item[1]
    return out


class GenericStringsPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = ()
    FLAVOR = "strings"
    SELFTEST_BASE = (b"Hello\x00\x00"
                     + "Xin chao".encode("utf-16-le") + b"\x00\x00"
                     + b"test1234\x00")

    def _candidate_paths(self, ctx: PluginContext) -> List[str]:
        out = []
        for p in ctx.vfs.iter_entries():
            ext = os.path.splitext(p)[1].lower()
            if ext not in _KNOWN_EXTS:
                out.append(p)
        return sorted(out)

    def detect(self, ctx: PluginContext) -> List[Evidence]:
        out: List[Evidence] = []
        for p in self._candidate_paths(ctx):
            out.append(Evidence("content", "strings", 0.5,
                                "dò chuỗi thô trong %s" % p))
        return out

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        found = _scan(data)
        total = len(found)
        entries: List[LocEntry] = []
        for offset, end, value, encoding in found[:_MAX_ENTRIES]:
            src = SourceRef([path], path, offset, end - offset, encoding, {})
            entries.append(LocEntry("off_%08x" % offset, value, locale, "generated",
                                    "ok", src, []))
        warnings: List[Dict[str, Any]] = [
            {"code": "E_PARSE_PARTIAL", "msg": _RAW_WARNING}]
        if total > _MAX_ENTRIES:
            warnings.append({"code": "E_PARSE_PARTIAL",
                             "msg": "tìm thấy %d chuỗi, chỉ giữ %d entry đầu"
                                    % (total, _MAX_ENTRIES)})
        return {"entries": entries, "schema_hint": {"format": "strings"},
                "stats": {"chuoi_tim_thay": total, "gioi_han": _MAX_ENTRIES},
                "conflicts": [], "warnings": warnings}

    def list_candidates(self, ctx: PluginContext) -> List[Candidate]:
        # Lưới cuối, điểm thấp: ứng viên nào cũng chỉ 0.5 để plugin chuyên biệt thắng.
        found = []
        for p in self._candidate_paths(ctx):
            try:
                ctx.vfs.read(p, max_bytes=4096)
            except PB3Error:
                continue
            found.append(p)
        return [Candidate(p, self.id, 0.5, ["dò chuỗi thô (%s)" % p],
                          [Evidence("content", "strings", 0.5,
                                    "dò chuỗi thô trong %s" % p)])
                for p in sorted(found)]
