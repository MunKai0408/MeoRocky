# Plugin định dạng Java .properties (Claude 06).
# Parse chuẩn Java: thử UTF-8 nghiêm ngặt, không được thì latin-1; comment #/!;
# separator = hoặc : hoặc khoảng trắng; escape \t \n \r \f \\ \uXXXX \= \: \space;
# nối dòng bằng backslash cuối dòng; key không separator -> value "".
# Key trùng: xung đột, giữ bản ghi đầu.
# Ghi ngược thay theo span: parse ghi lại span của value (trong text đã decode);
# build chỉ thay đúng span của value đã sửa, escape lại giá trị. Comment, thứ tự
# và separator giữ nguyên byte (trừ span value bị sửa). Ký tự không ASCII ->
# \uXXXX nếu encoding gốc là latin-1, giữ nguyên UTF-8 nếu gốc UTF-8.
# Hạn chế trung thực: verify mặc định so tập key; với file latin-1, một byte lật
# trong value vẫn decode được nên verify có thêm kiểm ký tự điều khiển nhưng
# không bắt được mọi byte lật trong value latin-1 (xem _verify_bytes).
# Kiểm tra: python3 menu.py plugin test properties
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, make_entries
from sdk.types import LocTable

MANIFEST = {
    "id": "properties",
    "name": "Java properties",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".properties"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:PropertiesPlugin",
    "selftest": True,
    "docs": "Chuẩn Java .properties: encoding UTF-8 (BOM được bỏ) nếu giải mã được, "
            "không thì latin-1; comment # và !; separator '=' ':' hoặc khoảng trắng; "
            "escape \\t \\n \\r \\f \\\\ \\uXXXX \\= \\: và \\space; nối dòng bằng "
            "backslash cuối dòng; key không separator thì value rỗng; key trùng báo "
            "xung đột, giữ bản ghi đầu. Ghi ngược chỉ thay đúng span của value (comment, "
            "thứ tự, separator giữ nguyên). Ký tự không ASCII thành \\uXXXX khi file "
            "gốc latin-1, giữ nguyên khi gốc UTF-8. Verify: file UTF-8 thì byte lật "
            "làm hỏng giải mã nên bị từ chối; file latin-1 thì verify chỉ bắt được "
            "byte lật làm đổi key/cấu trúc hoặc sinh ký tự điều khiển.",
}

_SNIFF_RE = re.compile(r"(?m)^[^#!\s][^=:\n]*[=:]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _decode(data: bytes, path: str) -> Tuple[str, str, bool]:
    """Trả (text, encoding, có_BOM_UTF8). UTF-8 nghiêm ngặt, không được thì latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "utf-8", True
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1", False


def _unescape(raw: str, path: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(raw):
            out.append("\\")
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt == "u":
            hexpart = raw[i + 2:i + 6]
            if len(hexpart) != 4 or not all(c in "0123456789abcdefABCDEF" for c in hexpart):
                raise PB3Error("E_CORRUPT", "escape \\u sai trong file properties", where=path)
            out.append(chr(int(hexpart, 16)))
            i += 6
            continue
        mapping = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
        out.append(mapping.get(nxt, nxt))  # escape lạ -> giữ ký tự sau backslash
        i += 2
    return "".join(out)


def _escape_value(value: str, encoding: str) -> str:
    out: List[str] = []
    for idx, ch in enumerate(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\f":
            out.append("\\f")
        elif ch in ("=", ":"):
            out.append("\\" + ch)
        elif ch == " " and (idx == 0 or idx == len(value) - 1):
            out.append("\\ ")
        elif ord(ch) > 127 and encoding == "latin-1":
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _odd_trailing_backslashes(s: str) -> bool:
    n = 0
    for ch in reversed(s):
        if ch == "\\":
            n += 1
        else:
            break
    return n % 2 == 1


def _parse_logical_lines(text: str):
    """Chia text thành các dòng logic (đã gộp nối dòng). Mỗi phần tử:
    (dòng_logic, vị_trí_bắt_đầu, ánh_xạ_vị_trí).
    ánh_xạ_vị_trí[k] = offset trong text của ký tự thứ k của dòng logic;
    ánh_xạ_vị_trí[len] = offset ngay sau ký tự cuối của dòng logic."""
    phys: List[Tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("\n", i)
        if j == -1:
            phys.append((text[i:], i, n))
            break
        line = text[i:j]
        if line.endswith("\r"):
            line = line[:-1]
        phys.append((line, i, j + 1))
        i = j + 1
    out = []
    k = 0
    while k < len(phys):
        line, start, _end = phys[k]
        buf = line
        smap = list(range(start, start + len(line)))
        while _odd_trailing_backslashes(buf) and k + 1 < len(phys):
            buf = buf[:-1]      # bỏ backslash nối dòng
            smap = smap[:-1]
            nxt, nstart, _ne = phys[k + 1]
            stripped = nxt.lstrip(" \t\f")
            lead = len(nxt) - len(stripped)
            buf += stripped
            smap += list(range(nstart + lead, nstart + len(nxt)))
            k += 1
        smap.append(smap[-1] + 1 if smap else start)
        out.append((buf, start, smap))
        k += 1
    return out


def _split_key_value(logical: str, path: str) -> Tuple[str, str, int, int, bool]:
    """Trả (key_thô, value_thô, vị_trí_bắt_đầu_value, vị_trí_kết_thúc_value, có_separator).
    Vị trí tính trong dòng logic."""
    n = len(logical)
    i = 0
    while i < n and logical[i] in (" ", "\t", "\f"):
        i += 1
    key_start = i
    key_end = None
    while i < n:
        ch = logical[i]
        if ch == "\\":
            i += 2
            continue
        if ch in ("=", ":"):
            key_end = i
            break
        if ch in (" ", "\t", "\f"):
            key_end = i
            break
        i += 1
    if key_end is None:
        return logical[key_start:], "", n, n, False
    key_raw = logical[key_start:key_end]
    j = key_end
    while j < n and logical[j] in (" ", "\t", "\f"):
        j += 1
    if j < n and logical[j] in ("=", ":"):
        j += 1
        while j < n and logical[j] in (" ", "\t", "\f"):
            j += 1
    return key_raw, logical[j:], j, n, True


def _parse_props(text: str, path: str):
    """Trả (entries_thô, spans): entries_thô = [(key, value, có_separator)],
    spans = {key: (start, end, có_separator)} theo offset trong text (bản ghi đầu)."""
    entries: List[Tuple[str, str, bool]] = []
    spans: Dict[str, Tuple[int, int, bool]] = {}
    for logical, _start, smap in _parse_logical_lines(text):
        stripped = logical.lstrip(" \t\f")
        if stripped == "" or stripped[0] in ("#", "!"):
            continue
        key_raw, val_raw, vs, ve, has_sep = _split_key_value(logical, path)
        key = _unescape(key_raw, path)
        value = _unescape(val_raw, path)
        entries.append((key, value, has_sep))
        if key not in spans:
            spans[key] = (smap[vs], smap[ve], has_sep)
    return entries, spans


class PropertiesPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".properties",)
    FLAVOR = "properties"
    SELFTEST_BASE = b"hello=Xin chao\nbye=Tam biet\n"

    def _sniff(self, head: bytes, path: str) -> float:
        try:
            text = head.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - sniff không được làm hỏng detect
            return 0.0
        if _SNIFF_RE.search(text):
            return 0.7
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        text, encoding, _bom = _decode(data, path)
        raw_entries, _spans = _parse_props(text, path)
        pairs = [(k, v, "ok", []) for k, v, _s in raw_entries]
        entries, conflicts = make_entries(pairs, path, len(data), encoding, locale)
        return {"entries": entries, "schema_hint": {"flavor": "properties", "encoding": encoding},
                "stats": {"keys": len(entries)}, "conflicts": conflicts, "warnings": []}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        text, encoding, bom = _decode(base_bytes, "base")
        _entries, spans = _parse_props(text, "base")
        # Chỉ ghi entry ok/empty; key trùng (bản sau) giữ nguyên.
        values: Dict[str, str] = {}
        for e in table.entries:
            if e.status in ("ok", "empty") and e.key in spans and e.key not in values:
                values[e.key] = e.value
        edits: List[Tuple[int, int, str]] = []
        for key, new_val in values.items():
            start, end, has_sep = spans[key]
            esc = _escape_value(new_val, encoding)
            if not has_sep:
                # Dòng chỉ có key: chèn "=giá trị" ngay sau key.
                edits.append((start, end, "=" + esc))
            else:
                edits.append((start, end, esc))
        edits.sort()
        out: List[str] = []
        pos = 0
        for start, end, rep in edits:
            out.append(text[pos:start])
            out.append(rep)
            pos = end
        out.append(text[pos:])
        result = "".join(out)
        raw = result.encode(encoding)
        if bom and encoding == "utf-8":
            raw = b"\xef\xbb\xbf" + raw
        return raw

    def _verify_bytes(self, base_bytes: bytes, new_bytes: bytes) -> Dict[str, Any]:
        """Mạnh hơn mặc định: ngoài so tập key, còn yêu cầu file mới giải mã
        được bằng cùng encoding như file gốc (byte lật trong file UTF-8 làm
        hỏng giải mã -> bị từ chối thay vì rơi sang latin-1), và không sinh
        ký tự điều khiển mới."""
        base_text, base_enc, _b = _decode(base_bytes, "<gốc>")
        if base_enc == "utf-8":
            try:
                new_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return {"ok": False, "checked": True,
                        "reason": "file mới không còn giải mã được UTF-8"}
        else:
            new_text = new_bytes.decode("latin-1")
            if _CTRL_RE.search(new_text) and not _CTRL_RE.search(base_text):
                return {"ok": False, "checked": True,
                        "reason": "file mới sinh ký tự điều khiển (có thể do byte lật)"}
        return super()._verify_bytes(base_bytes, new_bytes)
