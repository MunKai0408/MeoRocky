# Plugin định dạng XML strings (Claude 06): Android <resources> + dạng generic.
# Parse: từ chối <!ENTITY (không hỗ trợ thực thể DTD tự định nghĩa); giữ comment
# (parser insert_comments=True); giải escape kiểu Android khi đọc, mã hóa lại khi ghi.
# Ghi ngược: parse lại gốc (giữ comment), thay text của element theo key, serialize lại
# (giữ khai báo <?xml ...?> nếu gốc có; UTF-8 trừ khi gốc là UTF-16 thì giữ UTF-16+BOM).
# Chuỗi có markup con bên trong: giá trị đọc là toàn bộ text gộp; khi ghi, markup con
# của chuỗi bị sửa sẽ bị bỏ (chỉ giữ text mới).
# Kiểm tra: python3 menu.py plugin test xml_strings
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries
from sdk.types import LocTable

MANIFEST = {
    "id": "xml_strings",
    "name": "XML strings",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".xml"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:XmlPlugin",
    "selftest": True,
    "docs": "Android <resources>: <string name> thành entry, <string-array> thành key[i], "
            "<plurals> thành key[quantity]; translatable=\"false\" bị bỏ qua (có cảnh báo). "
            "Dạng generic: <string> có attr name/key/id ở bất cứ đâu thì dùng attr đó; "
            "không có thì con trực tiếp của root có text thì key là tên thẻ. Từ chối "
            "<!ENTITY (thực thể DTD tự định nghĩa). Escape Android (\\' \\\" \\n \\t \\uXXXX \\@) "
            "được giải khi đọc và mã hóa lại khi ghi. Ghi ngược giữ comment (trong thẻ "
            "root) và khai báo XML gốc — comment đứng trước thẻ root không giữ được "
            "(ElementTree bỏ); chuỗi có markup con sẽ mất markup khi bị sửa.",
}

_ESC_RE = re.compile(r"\\(u[0-9a-fA-F]{4}|n|t|r|'|\"|@|\\)")
_ESC_MAP = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "@": "@", "\\": "\\"}


def _unescape_android(text: str) -> str:
    def _rep(m):
        code = m.group(1)
        if code.startswith("u"):
            try:
                return chr(int(code[1:], 16))
            except ValueError:
                return m.group(0)
        return _ESC_MAP.get(code, code)
    return _ESC_RE.sub(_rep, text)


def _escape_android(value: str) -> str:
    out = []
    for i, ch in enumerate(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "@" and i == 0:
            out.append("\\@")
        elif ch == " " and (i == 0 or i == len(value) - 1):
            out.append("\\ ")
        else:
            out.append(ch)
    return "".join(out)


def _full_text(elem: ET.Element) -> str:
    return "".join(elem.itertext())


def _parse_tree(data: bytes, path: str):
    """Trả (root, doc, có_khai_báo_xml). Từ chối ENTITY, lỗi XML -> E_CORRUPT."""
    doc = decode_text(data, path)
    if "<!ENTITY" in doc.text.upper():
        raise PB3Error("E_CORRUPT", "không hỗ trợ thực thể DTD tự định nghĩa (<!ENTITY>)",
                       where=path)
    # Đưa về UTF-8 để expat luôn đọc được (kể cả UTF-16 không BOM); bỏ khai báo
    # encoding trong <?xml ...?> vì chuỗi đã là UTF-8.
    text = re.sub(r"^(\s*<\?xml\b[^?>]*?)\s+encoding\s*=\s*(\"[^\"]*\"|'[^']*')",
                  r"\1", doc.text, count=1)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(text.encode("utf-8"), parser=parser)
    except ET.ParseError:
        raise PB3Error("E_CORRUPT", "XML sai cú pháp", where=path)
    has_decl = doc.text.lstrip().startswith("<?xml")
    return root, doc, has_decl


def _collect(root: ET.Element, path: str, size: int, encoding: str,
             locale: Optional[str]):
    """Trả (pairs, key->element, warnings) cho cả dạng Android lẫn generic."""
    pairs: List[Tuple[str, str, str, List[str]]] = []
    keymap: Dict[str, ET.Element] = {}
    warnings: List[Dict[str, Any]] = []

    def _add(key, elem, value):
        pairs.append((key, value, "ok", []))
        keymap.setdefault(key, elem)

    if root.tag == "resources":
        for child in root:
            if not isinstance(child.tag, str):
                continue  # comment
            trans = (child.get("translatable") or "").strip().lower() == "false"
            name = child.get("name") or ""
            if child.tag == "string":
                if trans:
                    warnings.append({"code": "E_PARSE_PARTIAL", "key": name,
                                     "msg": "bỏ qua chuỗi translatable=false"})
                    continue
                if not name:
                    warnings.append({"code": "E_PARSE_PARTIAL",
                                     "msg": "bỏ qua <string> không có thuộc tính name"})
                    continue
                _add(name, child, _unescape_android(_full_text(child)))
            elif child.tag == "string-array":
                if trans:
                    warnings.append({"code": "E_PARSE_PARTIAL", "key": name,
                                     "msg": "bỏ qua mảng translatable=false"})
                    continue
                if not name:
                    warnings.append({"code": "E_PARSE_PARTIAL",
                                     "msg": "bỏ qua <string-array> không có thuộc tính name"})
                    continue
                for i, item in enumerate(child):
                    if not isinstance(item.tag, str):
                        continue
                    _add("%s[%d]" % (name, i), item, _unescape_android(_full_text(item)))
            elif child.tag == "plurals":
                if trans:
                    warnings.append({"code": "E_PARSE_PARTIAL", "key": name,
                                     "msg": "bỏ qua plurals translatable=false"})
                    continue
                if not name:
                    warnings.append({"code": "E_PARSE_PARTIAL",
                                     "msg": "bỏ qua <plurals> không có thuộc tính name"})
                    continue
                for i, item in enumerate(child):
                    if not isinstance(item.tag, str):
                        continue
                    qty = item.get("quantity") or ("item%d" % i)
                    _add("%s[%s]" % (name, qty), item, _unescape_android(_full_text(item)))
            # Thẻ khác trong <resources> (color, dimen...) không phải chuỗi: bỏ qua.
        return pairs, keymap, warnings

    # Dạng generic.
    for elem in root.iter():
        if not isinstance(elem.tag, str) or elem is root:
            continue
        if elem.tag == "string":
            key = elem.get("name") or elem.get("key") or elem.get("id")
            if key:
                _add(key, elem, _unescape_android(_full_text(elem)))
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if child.tag == "string" and (child.get("name") or child.get("key") or child.get("id")):
            continue  # đã thêm ở trên
        text = _full_text(child)
        if text.strip():
            _add(child.tag, child, _unescape_android(text))
    return pairs, keymap, warnings


class XmlPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".xml",)
    FLAVOR = "xml"
    SELFTEST_BASE = b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="hello">Xin chao</string>\n</resources>\n'

    def _sniff(self, head: bytes, path: str) -> float:
        s = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        if s.startswith(b"<?xml") or s.startswith(b"<resources") or s.startswith(b"<string"):
            return 0.8
        if head.startswith(b"\xff\xfe") and head[2:6] in (b"<\x00?\x00", b"<\x00r\x00", b"<\x00s\x00"):
            return 0.8
        if head.startswith(b"\xfe\xff") and head[2:6] in (b"\x00<\x00?", b"\x00<\x00r", b"\x00<\x00s"):
            return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        root, doc, _decl = _parse_tree(data, path)
        pairs, _keymap, warnings = _collect(root, path, len(data), doc.encoding, locale)
        entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
        return {"entries": entries, "schema_hint": {"flavor": "xml", "root": root.tag},
                "stats": {"keys": len(entries)}, "conflicts": conflicts, "warnings": warnings}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        root, doc, has_decl = _parse_tree(base_bytes, "base")
        _pairs, keymap, _warnings = _collect(root, "base", len(base_bytes), doc.encoding,
                                             table.locale)
        for e in table.entries:
            if e.status not in ("ok", "empty"):
                continue
            elem = keymap.get(e.key)
            if elem is None:
                continue
            # Thay text mới, bỏ markup con (đã ghi rõ trong docs).
            for child in list(elem):
                elem.remove(child)
            elem.text = _escape_android(e.value)
        enc = "utf-16" if doc.encoding.startswith("utf-16") else "utf-8"
        out = ET.tostring(root, encoding=enc, xml_declaration=has_decl)
        if not has_decl:
            return out
        # ET đã ghi khai báo + BOM (utf-16); giữ nguyên.
        return out
