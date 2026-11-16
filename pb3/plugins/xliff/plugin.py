# Plugin định dạng XLIFF (Claude 06): parse XLIFF 1.2 và 2.0 thành LocTable + ghi ngược.
# XLIFF 1.2: <trans-unit id|resname><source>/<target>/<note>; XLIFF 2.0: <unit id>
# <segment><source>/<target>. Key = resname (1.2) hoặc name (2.0) nếu có, không thì id;
# giá trị = <target>, nếu chưa có target thì giá trị = <source> kèm ghi chú "chưa-dịch".
# Locale lấy từ thuộc tính target-language của <file>. Target chứa markup nội tuyến
# (<g>, <mrk>, <x/>...) -> entry status "unparsed", build BỎ QUA kèm cảnh báo (không
# làm phẳng markup im lặng). Từ chối <!ENTITY (E_CORRUPT) để tránh XXE. Ghi ngược giữ
# comment, chèn <target> ngay sau <source> khi cần, giữ khai báo <?xml ...?> nếu gốc có.
# Giới hạn: chỉ hỗ trợ UTF-8/UTF-16 (không hỗ trợ encoding khác), có thể đổi tiền tố
# namespace khi ghi ngược nhưng giữ nguyên ngữ nghĩa, không hỗ trợ <alt-trans>.
# Kiểm tra: python3 menu.py plugin test xliff
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import (FormatPluginBase, decode_text, make_entries, stable_hash_key,
                         value_queue)
from sdk.types import LocTable

MANIFEST = {
    "id": "xliff",
    "name": "XLIFF localization",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".xlf", ".xliff"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:XliffPlugin",
    "selftest": True,
    "docs": "XLIFF 1.2 (<trans-unit>) và 2.0 (<unit>/<segment>). Key = resname/name nếu có, "
            "không thì id; giá trị = <target>, chưa có target thì lấy <source> kèm ghi chú "
            "'chưa-dịch'. Locale từ target-language của <file>. Target chứa markup nội tuyến "
            "-> entry 'unparsed', build bỏ qua kèm cảnh báo. Từ chối <!ENTITY. Ghi ngược giữ "
            "comment và khai báo XML. Chỉ hỗ trợ UTF-8/UTF-16; có thể đổi tiền tố namespace "
            "khi ghi nhưng giữ nguyên ngữ nghĩa; không hỗ trợ <alt-trans>.",
}

_NOTE_LEN = 80
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16LE_BOM = b"\xff\xfe"
_UTF16BE_BOM = b"\xfe\xff"


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


def _sibling_tag(elem: ET.Element, local: str) -> str:
    ns = _ns(elem.tag)
    return "{%s}%s" % (ns, local) if ns else local


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


def _walk_units(root: ET.Element) -> List[Dict[str, Any]]:
    """Thu thập record theo thứ tự file: mỗi record là một cặp source/target."""
    records: List[Dict[str, Any]] = []
    target_language: Optional[str] = None
    for f in root.iter():
        if _local(f.tag) != "file":
            continue
        if target_language is None:
            target_language = f.attrib.get("target-language")
        for node in f.iter():
            tag = _local(node.tag)
            if tag == "trans-unit":
                _walk_trans_unit(node, records)
            elif tag == "unit":
                _walk_unit(node, records)
    return records, target_language


def _walk_trans_unit(node: ET.Element, records: List[Dict[str, Any]]) -> None:
    source = _first_child(node, "source")
    target = _first_child(node, "target")
    notes = [_trunc(_flat_text(n)) for n in _children(node, "note") if _flat_text(n).strip()]
    key = node.attrib.get("resname") or node.attrib.get("id")
    records.append({"key_hint": key, "source": source, "target": target,
                    "notes": notes, "node": node})


def _walk_unit(node: ET.Element, records: List[Dict[str, Any]]) -> None:
    segments = _children(node, "segment")
    notes: List[str] = []
    for n in node.iter():
        if _local(n.tag) == "note" and n is not node:
            txt = _flat_text(n).strip()
            if txt:
                notes.append(_trunc(txt))
    base = node.attrib.get("name") or node.attrib.get("id")
    for i, seg in enumerate(segments):
        source = _first_child(seg, "source")
        target = _first_child(seg, "target")
        key = base if (len(segments) == 1 or i == 0) else base + "[%d]" % i \
            if base is not None else None
        records.append({"key_hint": key, "source": source, "target": target,
                        "notes": list(notes), "node": seg})


def _parse_xliff(data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
    root, doc, had_decl = _parse_xml(data, path)
    if _local(root.tag) != "xliff":
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "không phải file XLIFF (thẻ gốc không phải <xliff>)", where=path)
    records, target_language = _walk_units(root)
    version = root.attrib.get("version", "")
    # Locale: target-language của <file> (1.2), không có thì trgLang của gốc (2.0).
    file_locale = target_language or root.attrib.get("trgLang") or locale
    pairs: List[Tuple[str, str, str, List[str]]] = []
    unparsed_keys: List[str] = []
    stats: Dict[str, Any] = {"chua_dich": 0, "unparsed": 0, "bo_qua": 0}
    warnings: List[Dict[str, Any]] = []
    for rec in records:
        source = rec["source"]
        if source is None:
            stats["bo_qua"] += 1
            warnings.append({"code": "E_PARSE_PARTIAL",
                             "msg": "bỏ qua unit không có <source>"})
            continue
        source_text = _flat_text(source)
        key = rec["key_hint"] or stable_hash_key(source_text)
        notes = list(rec["notes"])
        target = rec["target"]
        if target is not None:
            value = _flat_text(target)
            if len(list(target)) > 0:
                status = "unparsed"
                notes = notes + ["có-markup-nội-tuyến"]
                stats["unparsed"] += 1
                unparsed_keys.append(key)
                warnings.append({"code": "E_PARSE_PARTIAL", "key": key,
                                 "msg": "target chứa markup nội tuyến, không làm phẳng khi ghi"})
            else:
                status = "ok"
        else:
            value = source_text
            status = "ok"
            notes = notes + ["chưa-dịch"]
            stats["chua_dich"] += 1
        pairs.append((key, value, status, notes))
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, file_locale)
    # make_entries đổi status thành "empty" khi giá trị rỗng; khôi phục "unparsed".
    for e in entries:
        if e.key in unparsed_keys:
            e.status = "unparsed"
    return {"entries": entries,
            "schema_hint": {"format": "xliff", "version": version,
                            "target_language": file_locale},
            "stats": stats, "conflicts": conflicts, "warnings": warnings,
            "records": records, "root": root, "doc": doc, "had_decl": had_decl}


class XliffPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".xlf", ".xliff")
    FLAVOR = "xliff"
    SELFTEST_BASE = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                     b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
                     b'  <file source-language="en" target-language="vi">\n'
                     b'    <body>\n'
                     b'      <trans-unit id="hello"><source>Hello</source>'
                     b'<target>Xin chao</target></trans-unit>\n'
                     b'      <trans-unit id="bye"><source>Goodbye</source></trans-unit>\n'
                     b'    </body>\n'
                     b'  </file>\n'
                     b'</xliff>\n')

    def _sniff(self, head: bytes, path: str) -> float:
        if b"<xliff" in head:
            return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        res = _parse_xliff(data, path, locale)
        return {"entries": res["entries"], "schema_hint": res["schema_hint"],
                "stats": res["stats"], "conflicts": res["conflicts"],
                "warnings": res["warnings"]}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        res = _parse_xliff(base_bytes, "base", table.locale)
        queue = value_queue(table)
        # Ánh xạ key -> danh sách record (key có thể lặp lại trong file).
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for rec in res["records"]:
            key = rec["key_hint"]
            if key is None:
                key = stable_hash_key(_flat_text(rec["source"]))
            by_key.setdefault(key, []).append(rec)
        for key, recs in by_key.items():
            vals = queue.get(key)
            if not vals:
                continue
            for rec in recs:
                if rec["source"] is None:
                    continue
                if not vals:
                    break
                new_value = vals.pop(0)
                target = rec["target"]
                source = rec["source"]
                if target is not None and len(list(target)) > 0:
                    table.warnings.append(
                        {"code": "E_PARSE_PARTIAL", "key": key,
                         "msg": "bỏ qua entry có markup nội tuyến khi ghi"})
                    continue
                if target is not None:
                    target.text = new_value
                    del target[:]
                elif new_value != _flat_text(source):
                    parent = rec["node"]
                    new_target = ET.Element(_sibling_tag(source, "target"))
                    new_target.text = new_value
                    idx = list(parent).index(source)
                    parent.insert(idx + 1, new_target)
        return _serialize(res["root"], res["doc"], res["had_decl"])
