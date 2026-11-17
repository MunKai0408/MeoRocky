# Plugin định dạng Apple .strings (Claude 06): parse "key" = "value";
# + ghi ngược đúng span của giá trị (phần không sửa giữ nguyên byte,
# encoding/BOM gốc được giữ). Hỗ trợ escape \n \r \t \" \\ \uXXXX,
# comment /* */ và //, key trùng giữ bản ghi đầu, placeholder (%@, %d...)
# giữ nguyên như chuỗi thường.
# + Bổ sung 2026-11-17 (follow-up dò text): file .strings dạng bplist
# (binary plist, magic "bplist00" — toàn bộ .strings của vỏ app iOS
# trong IPA, vd My Talking Tom: 156/159 file) cũng đọc được cặp
# key→value bằng plistlib (stdlib), ghi ngược bằng plistlib round-trip.
# Kiểm tra: python3 menu.py plugin test apple_strings
from __future__ import annotations

import plistlib
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries, value_queue
from sdk.types import LocTable

BPLIST_MAGIC = b"bplist00"
BPLIST_FLAVOR = "strings-bplist"

MANIFEST = {
    "id": "apple_strings",
    "name": "Apple .strings",
    "version": "1.1.0",
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
            "Không hỗ trợ chuỗi không đóng hoặc thiếu dấu chấm phẩy (báo lỗi). "
            "Dạng bplist (binary plist, magic bplist00) cũng đọc/ghi được "
            "cặp key→value qua plistlib (ghi ngược rebuild toàn bộ dict, "
            "giữ đủ key).",
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


def _parse_bplist_bytes(data: bytes, path: str,
                        locale: Optional[str]) -> Dict[str, Any]:
    """Parse .strings dạng bplist (binary plist) -> cùng dict kết quả
    như _parse_bytes của text. Gốc phải là dict; cặp key→value không
    phải chuỗi thì bỏ qua kèm cảnh báo (không đoán kiểu)."""
    try:
        obj = plistlib.loads(data)
    except Exception as exc:  # noqa: BLE001 - plistlib lỗi nhiều dạng
        raise PB3Error("E_CORRUPT",
                       "bplist không giải mã được (%s)" % type(exc).__name__,
                       where=path)
    if not isinstance(obj, dict):
        raise PB3Error("E_CORRUPT",
                       "bplist .strings phải là dict ở gốc, gặp %s"
                       % type(obj).__name__, where=path)
    pairs: List[Tuple[str, str, str, List[str]]] = []
    warnings: List[Dict[str, Any]] = []
    for key, value in obj.items():
        if not isinstance(key, str) or not isinstance(value, str):
            warnings.append({"code": "E_PARSE_PARTIAL", "key": str(key),
                             "msg": "bỏ qua cặp key→value không phải chuỗi"})
            continue
        pairs.append((key, value, "ok", []))
    entries, conflicts = make_entries(pairs, path, len(data), "utf-8", locale)
    return {"entries": entries,
            "schema_hint": {"flavor": BPLIST_FLAVOR, "encoding": "bplist"},
            "stats": {"keys": len(entries)},
            "conflicts": conflicts, "warnings": warnings}


def parse_strings_bytes(data: bytes, path: str,
                        locale: Optional[str] = None) -> Dict[str, Any]:
    """Parse .strings ở cả 2 dạng (text "key" = "value"; và bplist).

    Hàm public dùng chung: plugin._parse_bytes + đường trích sâu
    (sdk/inspect.py) cùng gọi để khỏi parse 2 nơi lệch nhau.
    Trả dict {entries, schema_hint, stats, conflicts, warnings} như
    FormatPluginBase._parse_bytes; schema_hint["flavor"] là "strings"
    (text) hoặc "strings-bplist" (bplist)."""
    if bytes(data[:8]) == BPLIST_MAGIC:
        return _parse_bplist_bytes(data, path, locale)
    doc = decode_text(data, path)
    occ = _parse_occurrences(doc.text, path)
    pairs = [(k, v, "ok", []) for (k, v, _s, _e) in occ]
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    return {"entries": entries,
            "schema_hint": {"flavor": "strings", "encoding": doc.encoding},
            "stats": {"keys": len(entries)},
            "conflicts": conflicts, "warnings": []}


class AppleStringsPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".strings",)
    FLAVOR = "strings"
    SELFTEST_BASE = b'"hello" = "Xin chao";\n"bye" = "Tam biet";\n'

    def _sniff(self, head: bytes, path: str) -> float:
        # bplist: magic chắc chắn (8 byte đầu), không cần đoán encoding.
        if bytes(head[:8]) == BPLIST_MAGIC:
            return 0.9
        for enc in ("utf-8", "utf-16"):
            try:
                text = head.decode(enc)
            except UnicodeDecodeError:
                continue
            if _TEXT_RE.search(text):
                return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        return parse_strings_bytes(data, path, locale)

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        if bytes(base_bytes[:8]) == BPLIST_MAGIC:
            # Ghi ngược bplist: rebuild toàn bộ dict bằng plistlib (giữ
            # đủ key, giữ thứ tự chèn của dict gốc). Chỉ sửa key đã có
            # trong file gốc (giống đường text: key mới không ghi thêm).
            try:
                base = plistlib.loads(base_bytes)
            except Exception as exc:  # noqa: BLE001
                raise PB3Error(
                    "E_CORRUPT",
                    "bplist gốc không giải mã được (%s)" % type(exc).__name__,
                    where="base")
            if not isinstance(base, dict):
                raise PB3Error("E_CORRUPT",
                               "bplist gốc phải là dict ở gốc", where="base")
            if not all(isinstance(v, str) for v in base.values()):
                raise PB3Error(
                    "E_CORRUPT",
                    "bplist gốc có giá trị không phải chuỗi, không ghi được",
                    where="base")
            queue = value_queue(table)
            for key in list(base):
                if queue.get(key):
                    new = queue[key].pop(0)
                    if not isinstance(new, str):
                        raise PB3Error("E_CORRUPT",
                                       "giá trị mới không phải chuỗi",
                                       where="base")
                    base[key] = new
            # sort_keys=False: giữ đúng thứ tự key của dict gốc.
            return plistlib.dumps(base, fmt=plistlib.FMT_BINARY,
                                  sort_keys=False)
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
