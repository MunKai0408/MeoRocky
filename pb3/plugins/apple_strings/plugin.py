# Plugin định dạng Apple .strings (Claude 06): parse "key" = "value";
# + ghi ngược đúng span của giá trị (phần không sửa giữ nguyên byte,
# encoding/BOM gốc được giữ). Hỗ trợ escape \n \r \t \" \\ \uXXXX,
# comment /* */ và //, key trùng giữ bản ghi đầu, placeholder (%@, %d...)
# giữ nguyên như chuỗi thường.
# Kiểm tra: python3 menu.py plugin test apple_strings
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries, value_queue
from sdk.types import LocTable

MANIFEST = {
    "id": "apple_strings",
    "name": "Apple .strings",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".strings"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:AppleStringsPlugin",
    "selftest": True,
    "docs": "Tệp .strings dạng \"key\" = \"value\"; (thường UTF-16LE có BOM, cũng đọc "
            "UTF-8). Giữ nguyên comment, thứ tự và phần không sửa khi ghi ngược; "
            "escape lại giá trị đã sửa. Placeholder (%@, %d...) giữ nguyên. "
            "Không hỗ trợ chuỗi không đóng hoặc thiếu dấu chấm phẩy (báo lỗi).",
}

_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "'": "'"}
_TEXT_RE = re.compile(r'"(?:[^"\\]|\\.)*"\s*=\s*"(?:[^"\\]|\\.)*"\s*;')


def _skip_ws_comments(text: str, i: int, path: str) -> int:
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise PB3Error("E_CORRUPT", "comment /* không đóng", where=path)
            i = end + 2
        elif text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = n if end < 0 else end + 1
        else:
            break
    return i


def _read_quoted(text: str, i: int, path: str) -> Tuple[str, int]:
    """text[i] == '"'; trả (giá trị đã giải escape, chỉ số sau dấu " đóng)."""
    n = len(text)
    j = i + 1
    out: List[str] = []
    while j < n:
        c = text[j]
        if c == '"':
            return "".join(out), j + 1
        if c == "\\":
            if j + 1 >= n:
                raise PB3Error("E_CORRUPT", "escape ở cuối chuỗi", where=path)
            e = text[j + 1]
            if e == "u":
                hexs = text[j + 2:j + 6]
                if len(hexs) < 4 or any(ch not in "0123456789abcdefABCDEF" for ch in hexs):
                    raise PB3Error("E_CORRUPT", "escape \\u sai (cần 4 ký tự hex)", where=path)
                out.append(chr(int(hexs, 16)))
                j += 6
            elif e in _ESCAPES:
                out.append(_ESCAPES[e])
                j += 2
            else:
                out.append(e)  # escape lạ: giữ nguyên ký tự, bỏ dấu \
                j += 2
        else:
            out.append(c)
            j += 1
    raise PB3Error("E_CORRUPT", "chuỗi không đóng dấu ngoặc kép", where=path)


def _parse_occurrences(text: str, path: str) -> List[Tuple[str, str, int, int]]:
    """[(key, value, đầu span giá trị, cuối span giá trị)] theo thứ tự trong file."""
    occ: List[Tuple[str, str, int, int]] = []
    i = _skip_ws_comments(text, 0, path)
    n = len(text)
    while i < n:
        if text[i] != '"':
            raise PB3Error("E_CORRUPT", "thiếu chuỗi key", where=path)
        key, i = _read_quoted(text, i, path)
        i = _skip_ws_comments(text, i, path)
        if i >= n or text[i] != "=":
            raise PB3Error("E_CORRUPT", "thiếu dấu = sau key", where=path)
        i += 1
        i = _skip_ws_comments(text, i, path)
        if i >= n or text[i] != '"':
            raise PB3Error("E_CORRUPT", "thiếu chuỗi giá trị sau dấu =", where=path)
        vstart = i + 1
        value, i = _read_quoted(text, i, path)
        vend = i - 1
        i = _skip_ws_comments(text, i, path)
        if i >= n or text[i] != ";":
            raise PB3Error("E_CORRUPT", "thiếu dấu ; sau giá trị", where=path)
        i += 1
        occ.append((key, value, vstart, vend))
        i = _skip_ws_comments(text, i, path)
    return occ


def _escape(value: str) -> str:
    out: List[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


class AppleStringsPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".strings",)
    FLAVOR = "strings"
    SELFTEST_BASE = b'"hello" = "Xin chao";\n"bye" = "Tam biet";\n'

    def _sniff(self, head: bytes, path: str) -> float:
        for enc in ("utf-8", "utf-16"):
            try:
                text = head.decode(enc)
            except UnicodeDecodeError:
                continue
            if _TEXT_RE.search(text):
                return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        doc = decode_text(data, path)
        occ = _parse_occurrences(doc.text, path)
        pairs = [(k, v, "ok", []) for (k, v, _s, _e) in occ]
        entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
        return {"entries": entries,
                "schema_hint": {"flavor": "strings", "encoding": doc.encoding},
                "stats": {"keys": len(entries)},
                "conflicts": conflicts, "warnings": []}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        doc = decode_text(base_bytes, "base")
        occ = _parse_occurrences(doc.text, "base")
        queue = value_queue(table)
        edits: List[Tuple[int, int, str]] = []
        for (key, _old, vs, ve) in occ:
            if queue.get(key):
                edits.append((vs, ve, _escape(queue[key].pop(0))))
        text = doc.text
        for vs, ve, new in sorted(edits, reverse=True):
            text = text[:vs] + new + text[ve:]
        return doc.encode(text)
