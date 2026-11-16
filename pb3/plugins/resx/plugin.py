# Plugin định dạng RESX (Claude 06): parse file .resx (.NET) thành LocTable + ghi ngược.
# Mỗi <data name><value>: key = name, giá trị = text của <value>; <comment> đưa vào
# ghi chú. xml:space="preserve" -> không trim giá trị, mặc định trim hai đầu.
# <data> có thuộc tính type hoặc mimetype (không phải chuỗi) -> entry status "unparsed"
# kèm cảnh báo, build BỎ QUA kèm cảnh báo; <value> chứa markup con cũng vậy. Từ chối
# <!ENTITY (E_CORRUPT) để tránh XXE. Ghi ngược chỉ thay text của <value>, giữ nguyên
# resheader và comment, giữ khai báo <?xml ...?> nếu gốc có.
# Giới hạn: chỉ hỗ trợ UTF-8/UTF-16 (không hỗ trợ encoding khác), không khôi phục kiểu
# dữ liệu nhị phân (entry unparsed), có thể đổi tiền tố namespace khi ghi ngược nhưng
# giữ nguyên ngữ nghĩa.
# Kiểm tra: python3 menu.py plugin test resx
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries, value_queue
from sdk.types import LocTable

MANIFEST = {
    "id": "resx",
    "name": "RESX .NET",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".resx"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:ResxPlugin",
    "selftest": True,
    "docs": "File .resx (.NET): <data name><value>, <comment> đưa vào ghi chú. "
            "xml:space=\"preserve\" thì không trim giá trị, mặc định trim hai đầu. "
            "<data> có type/mimetype (không phải chuỗi) hoặc <value> chứa markup -> entry "
            "'unparsed', build bỏ qua kèm cảnh báo. Từ chối <!ENTITY. Ghi ngược giữ resheader "
            "và comment. Chỉ hỗ trợ UTF-8/UTF-16; không khôi phục dữ liệu nhị phân.",
}

_NOTE_LEN = 80
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16LE_BOM = b"\xff\xfe"
_UTF16BE_BOM = b"\xfe\xff"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _local(tag: str) -> str:
    # Nút comment có tag là hàm ET.Comment, không phải chuỗi.
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _ns(tag: str) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _flat_text(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext())


def _trunc(text: str) -> str:
    text = (text or "").strip()
    return text[:_NOTE_LEN] + ("…" if len(text) > _NOTE_LEN else "")


def _parse_xml(data: bytes, path: str) -> Tuple[ET.Element, Any, bool]:
    """Parse XML có giữ comment; trả (root, TextDoc, có khai báo <?xml?>).
    <!ENTITY -> E_CORRUPT; XML sai cú pháp -> E_CORRUPT; giải mã lỗi -> E_CORRUPT."""
    if b"<!ENTITY" in data:
        raise PB3Error("E_CORRUPT", "phát hiện <!ENTITY, từ chối để tránh XXE", where=path)
    doc = decode_text(data, path)
    body = data
    for bom in (_UTF8_BOM, _UTF16LE_BOM, _UTF16BE_BOM):
        if body.startswith(bom):
            body = body[len(bom):]
            break
    had_decl = body.lstrip(b" \t\r\n").startswith(b"<?xml")
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(data, parser=parser)
    except ET.ParseError:
        raise PB3Error("E_CORRUPT", "XML sai cú pháp", where=path)
    return root, doc, had_decl


def _serialize(root: ET.Element, doc: Any, had_decl: bool) -> bytes:
    ns = _ns(root.tag)
    if ns:
        try:
            ET.register_namespace("", ns)
        except ValueError:
            pass  # tiền tố mặc định đã gắn URI khác: giữ nguyên, vẫn đúng ngữ nghĩa
    text = ET.tostring(root, encoding="unicode")
    if had_decl:
        enc_name = "UTF-8" if doc.encoding == "utf-8" else "UTF-16"
        text = '<?xml version="1.0" encoding="%s"?>\n%s' % (enc_name, text)
    return doc.encode(text)


def _children(elem: ET.Element, local: str) -> List[ET.Element]:
    return [c for c in list(elem) if _local(c.tag) == local]


def _first_child(elem: ET.Element, local: str) -> Optional[ET.Element]:
    found = _children(elem, local)
    return found[0] if found else None


def _parse_resx(data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
    root, doc, had_decl = _parse_xml(data, path)
    if _local(root.tag) != "root":
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "không phải file RESX (thẻ gốc không phải <root>)", where=path)
    pairs: List[Tuple[str, str, str, List[str]]] = []
    records: List[Dict[str, Any]] = []
    unparsed_keys: List[str] = []
    stats: Dict[str, Any] = {"unparsed": 0, "bo_qua": 0}
    warnings: List[Dict[str, Any]] = []
    for data_elem in _children(root, "data"):
        name = data_elem.attrib.get("name")
        if not name:
            stats["bo_qua"] += 1
            warnings.append({"code": "E_PARSE_PARTIAL",
                             "msg": "bỏ qua <data> không có thuộc tính name"})
            continue
        value_elem = _first_child(data_elem, "value")
        comment_elem = _first_child(data_elem, "comment")
        notes: List[str] = []
        if comment_elem is not None and _flat_text(comment_elem).strip():
            notes.append(_trunc(_flat_text(comment_elem)))
        raw = _flat_text(value_elem) if value_elem is not None else ""
        type_attr = data_elem.attrib.get("type")
        mime_attr = data_elem.attrib.get("mimetype")
        has_markup = value_elem is not None and len(list(value_elem)) > 0
        if type_attr is not None or mime_attr is not None or has_markup:
            if has_markup:
                reason = "giá trị chứa markup"
            else:
                reason = "không phải chuỗi (type=%s mimetype=%s)" % (type_attr, mime_attr)
            status = "unparsed"
            notes = notes + [reason]
            stats["unparsed"] += 1
            unparsed_keys.append(name)
            warnings.append({"code": "E_PARSE_PARTIAL", "key": name,
                             "msg": "%s, không ghi ngược được" % reason})
            value = raw
        else:
            space = data_elem.attrib.get(_XML_SPACE) or data_elem.attrib.get("xml:space")
            value = raw if space == "preserve" else raw.strip()
            status = "ok"
        pairs.append((name, value, status, notes))
        records.append({"key": name, "data": data_elem, "value": value_elem,
                        "unparsed": status == "unparsed"})
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    # make_entries đổi status thành "empty" khi giá trị rỗng; khôi phục "unparsed".
    for e in entries:
        if e.key in unparsed_keys:
            e.status = "unparsed"
    return {"entries": entries, "schema_hint": {"format": "resx"},
            "stats": stats, "conflicts": conflicts, "warnings": warnings,
            "records": records, "root": root, "doc": doc, "had_decl": had_decl}


class ResxPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".resx",)
    FLAVOR = "resx"
    SELFTEST_BASE = (b'<?xml version="1.0" encoding="utf-8"?>\n'
                     b'<root>\n'
                     b'  <resheader name="resmimetype">'
                     b'<value>text/microsoft-resx</value></resheader>\n'
                     b'  <data name="hello" xml:space="preserve">'
                     b'<value>Xin chao</value></data>\n'
                     b'  <data name="bye" xml:space="preserve">'
                     b'<value>Tam biet</value></data>\n'
                     b'</root>\n')

    def _sniff(self, head: bytes, path: str) -> float:
        if b"<root" in head and b"<data" in head:
            return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        res = _parse_resx(data, path, locale)
        return {"entries": res["entries"], "schema_hint": res["schema_hint"],
                "stats": res["stats"], "conflicts": res["conflicts"],
                "warnings": res["warnings"]}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        res = _parse_resx(base_bytes, "base", table.locale)
        queue = value_queue(table)
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for rec in res["records"]:
            by_key.setdefault(rec["key"], []).append(rec)
        for key, recs in by_key.items():
            vals = queue.get(key)
            if not vals:
                continue
            for rec in recs:
                if not vals:
                    break
                new_value = vals.pop(0)
                if rec["unparsed"]:
                    table.warnings.append(
                        {"code": "E_PARSE_PARTIAL", "key": key,
                         "msg": "bỏ qua entry không phải chuỗi khi ghi"})
                    continue
                value_elem = rec["value"]
                if value_elem is not None:
                    value_elem.text = new_value
                    del value_elem[:]
                elif new_value != "":
                    data_elem = rec["data"]
                    new_elem = ET.Element("value")
                    new_elem.text = new_value
                    data_elem.insert(0, new_elem)
        return _serialize(res["root"], res["doc"], res["had_decl"])
