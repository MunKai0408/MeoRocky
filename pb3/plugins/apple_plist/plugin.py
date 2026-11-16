# Plugin định dạng Apple plist / .stringsdict / .xcstrings (Claude 06):
# parse plist XML + nhị phân, .stringsdict (số nhiều) và catalog .xcstrings
# thành LocTable + ghi ngược (trừ plist nhị phân: chỉ đọc).
# Dict lồng nhau thành key chấm, list chuỗi thành key[i], scalar không chuỗi
# giữ đúng kiểu qua ghi chú (cùng quy ước như JSON). Key trùng giữ bản ghi đầu.
# Ghi ngược: plist XML qua plistlib (comment/DTD gốc không được giữ),
# .xcstrings cập nhật giá trị trong JSON rồi ghi lại (giữ encoding/BOM gốc).
# Kiểm tra: python3 menu.py plugin test apple_plist
from __future__ import annotations

import base64
import json
import plistlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import (FormatPluginBase, decode_text, make_entries,
                         stable_row_key, value_queue)
from sdk.types import LocTable

MANIFEST = {
    "id": "apple_plist",
    "name": "Apple plist / stringsdict / xcstrings",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".plist", ".stringsdict", ".xcstrings"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:ApplePlistPlugin",
    "selftest": True,
    "docs": "Plist XML/nhị phân, .stringsdict (số nhiều) và .xcstrings (catalog JSON "
            "theo locale). Plist nhị phân chỉ đọc, không ghi ngược. Plist XML ghi ngược "
            "qua plistlib nên comment/DTD trong XML gốc không được giữ. .xcstrings giữ "
            "nguyên cấu trúc, chỉ đổi giá trị chuỗi. Không hỗ trợ plist đã mã hóa.",
}

_PLURAL_RULES = {
    "NSStringPluralRuleZero": "zero",
    "NSStringPluralRuleOne": "one",
    "NSStringPluralRuleTwo": "two",
    "NSStringPluralRuleFew": "few",
    "NSStringPluralRuleMany": "many",
    "NSStringPluralRuleOther": "other",
}
_PLURAL_NAMES = ("zero", "one", "two", "few", "many", "other")
_PLURAL_META_KEYS = ("NSStringFormatSpecTypeKey", "NSStringFormatValueTypeKey")

_Pair = Tuple[str, str, str, List[str]]


def _detect_kind(data: bytes) -> str:
    """'binary' | 'xcstrings' | 'xml' — chỉ theo nội dung, không theo đuôi file
    (lớp nền gọi _parse_bytes với đường dẫn '<gốc>' khi build/verify)."""
    if data.startswith(b"bplist00"):
        return "binary"
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped[:1] == b"{":
        return "xcstrings"
    return "xml"


def _is_plural_dict(node: Any) -> bool:
    """Dict biến thể số nhiều dạng lồng thật của Apple: {'count': {'zero': ...,
    'other': ..., 'NSStringFormatSpecTypeKey': ...}}."""
    if not isinstance(node, dict) or not node:
        return False
    names = set(node.keys())
    return bool(names & set(_PLURAL_NAMES)) and names <= set(_PLURAL_NAMES) | set(_PLURAL_META_KEYS)


def _walk_stringsdict(node: Dict[str, Any], prefix: str, pairs: List[_Pair],
                      warnings: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    fmt_key = (prefix + ".format") if prefix else "format"
    fmt = node.get("NSStringLocalizedFormatKey")
    if isinstance(fmt, str):
        pairs.append((fmt_key, fmt, "ok", []))
    for k, v in node.items():
        if k == "NSStringLocalizedFormatKey":
            continue
        if k in _PLURAL_RULES:
            pk = (prefix + ".plural." + _PLURAL_RULES[k]) if prefix else "plural." + _PLURAL_RULES[k]
            if isinstance(v, str):
                pairs.append((pk, v, "ok", []))
            else:
                _walk_plist(v, pk, pairs, warnings, stats)
        elif _is_plural_dict(v):
            for rk, rv in v.items():
                if rk in _PLURAL_NAMES:
                    pk = (prefix + ".plural." + rk) if prefix else "plural." + rk
                    if isinstance(rv, str):
                        pairs.append((pk, rv, "ok", []))
                    else:
                        _walk_plist(rv, pk, pairs, warnings, stats)
                elif rk in _PLURAL_META_KEYS:
                    continue  # siêu dữ liệu định dạng, không phải chuỗi dịch
                else:
                    _walk_plist(rv, (prefix + "." + str(k) + "." + str(rk)) if prefix
                                else str(k) + "." + str(rk), pairs, warnings, stats)
        elif isinstance(v, str):
            pairs.append(((prefix + "." + str(k)) if prefix else str(k), v, "ok", []))
        else:
            _walk_plist(v, (prefix + "." + str(k)) if prefix else str(k), pairs, warnings, stats)


def _walk_plist(value: Any, prefix: str, pairs: List[_Pair],
                warnings: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    if isinstance(value, str):
        pairs.append((prefix, value, "ok", []))
    elif isinstance(value, dict):
        if "NSStringLocalizedFormatKey" in value:
            _walk_stringsdict(value, prefix, pairs, warnings, stats)
            return
        if not value:
            pairs.append((prefix, "{}", "ok", ["plist:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        for k, v in value.items():
            _walk_plist(v, (prefix + "." + str(k)) if prefix else str(k), pairs, warnings, stats)
    elif isinstance(value, list):
        if not value:
            pairs.append((prefix, "[]", "ok", ["plist:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        for i, x in enumerate(value):
            _walk_plist(x, "%s[%d]" % (prefix, i), pairs, warnings, stats)
    elif isinstance(value, bool):
        pairs.append((prefix, "true" if value else "false", "ok", ["json:boolean"]))
    elif isinstance(value, (int, float)):
        pairs.append((prefix, json.dumps(value, ensure_ascii=False), "ok", ["json:number"]))
    elif value is None:
        pairs.append((prefix, "null", "ok", ["json:null"]))
    elif isinstance(value, bytes):
        pairs.append((prefix, base64.b64encode(value).decode("ascii"), "ok", ["plist:bytes"]))
    elif isinstance(value, datetime):
        pairs.append((prefix, value.isoformat(), "ok", ["plist:date"]))
    else:
        pairs.append((prefix, str(value), "ok", ["plist:complex"]))
        stats["complex"] = stats.get("complex", 0) + 1


def _restore_plist(raw: str, notes: List[str], original: Any) -> Any:
    """Khôi phục kiểu gốc từ ghi chú; lỗi -> giữ giá trị gốc (không đoán mò)."""
    if "plist:bytes" in notes:
        try:
            return base64.b64decode(raw.encode("ascii"))
        except ValueError:
            return original
    if "plist:date" in notes:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return original
    if "plist:complex" in notes:
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    for note in notes:
        if note in ("json:number", "json:boolean", "json:null"):
            try:
                return json.loads(raw)
            except ValueError:
                return raw
    return raw


def _apply_plist(node: Any, prefix: str, queue: Dict[str, List[str]],
                 notes: Dict[str, List[str]], applied: set) -> Any:
    """Áp hàng đợi giá trị theo đúng key lá; trả node đã sửa (lá scalar được thay)."""
    if isinstance(node, dict):
        if "NSStringLocalizedFormatKey" in node:
            fk = (prefix + ".format") if prefix else "format"
            if queue.get(fk):
                node["NSStringLocalizedFormatKey"] = queue[fk].pop(0)
                applied.add(fk)
            for k in list(node.keys()):
                if k == "NSStringLocalizedFormatKey":
                    continue
                if k in _PLURAL_RULES:
                    pk = (prefix + ".plural." + _PLURAL_RULES[k]) if prefix else "plural." + _PLURAL_RULES[k]
                    if queue.get(pk) and isinstance(node[k], str):
                        node[k] = queue[pk].pop(0)
                        applied.add(pk)
                    elif not isinstance(node[k], str):
                        node[k] = _apply_plist(node[k], pk, queue, notes, applied)
                elif _is_plural_dict(node[k]):
                    for rk in list(node[k].keys()):
                        if rk in _PLURAL_NAMES:
                            pk = (prefix + ".plural." + rk) if prefix else "plural." + rk
                            if queue.get(pk) and isinstance(node[k][rk], str):
                                node[k][rk] = queue[pk].pop(0)
                                applied.add(pk)
                        elif rk in _PLURAL_META_KEYS:
                            continue
                        else:
                            node[k][rk] = _apply_plist(
                                node[k][rk], (prefix + "." + str(k) + "." + str(rk)) if prefix
                                else str(k) + "." + str(rk), queue, notes, applied)
                else:
                    node[k] = _apply_plist(node[k], (prefix + "." + str(k)) if prefix else str(k),
                                           queue, notes, applied)
            return node
        for k in list(node.keys()):
            node[k] = _apply_plist(node[k], (prefix + "." + str(k)) if prefix else str(k),
                                   queue, notes, applied)
        return node
    if isinstance(node, list):
        for i, x in enumerate(node):
            node[i] = _apply_plist(x, "%s[%d]" % (prefix, i), queue, notes, applied)
        return node
    if queue.get(prefix):
        raw = queue[prefix].pop(0)
        applied.add(prefix)
        return _restore_plist(raw, notes.get(prefix, []), node)
    return node


# --------------------------------------------------------------------------- .xcstrings
def _walk_xcstrings_variations(key: str, loc: Optional[str], variations: Dict[str, Any],
                               pairs: List[Tuple]) -> None:
    for vtype, vdict in variations.items():
        if not isinstance(vdict, dict):
            continue
        for vname, vobj in vdict.items():
            if not isinstance(vobj, dict):
                continue
            su = vobj.get("stringUnit")
            if isinstance(su, dict) and isinstance(su.get("value"), str):
                pairs.append((key + "." + str(vtype) + "." + str(vname), su["value"], "ok", [], loc))


def _walk_xcstrings_entry(key: str, ent: Dict[str, Any], pairs: List[Tuple],
                          warnings: List[Dict[str, Any]]) -> None:
    found = False
    locs = ent.get("localizations")
    if isinstance(locs, dict):
        for loc, lobj in locs.items():
            if not isinstance(lobj, dict):
                continue
            found = True
            su = lobj.get("stringUnit")
            if isinstance(su, dict) and isinstance(su.get("value"), str):
                pairs.append((key, su["value"], "ok", [], str(loc)))
            variations = lobj.get("variations")
            if isinstance(variations, dict):
                _walk_xcstrings_variations(key, str(loc), variations, pairs)
    variations = ent.get("variations")
    if isinstance(variations, dict):
        found = True
        _walk_xcstrings_variations(key, None, variations, pairs)
    if not found:
        warnings.append({"code": "E_PARSE_PARTIAL", "key": key,
                         "msg": "mục xcstrings không có localization nào"})


def _parse_xcstrings(data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
    doc = decode_text(data, path)
    try:
        root = json.loads(doc.text)
    except ValueError:
        raise PB3Error("E_CORRUPT", "xcstrings sai cú pháp JSON", where=path)
    if not isinstance(root, dict) or not isinstance(root.get("strings"), dict):
        raise PB3Error("E_UNSUPPORTED_FORMAT", "xcstrings thiếu mục strings", where=path)
    pairs: List[Tuple] = []
    warnings: List[Dict[str, Any]] = []
    for key, ent in root["strings"].items():
        if not isinstance(ent, dict):
            warnings.append({"code": "E_PARSE_PARTIAL", "key": str(key),
                             "msg": "mục xcstrings không phải object"})
            continue
        _walk_xcstrings_entry(str(key), ent, pairs, warnings)
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    return {"entries": entries,
            "schema_hint": {"flavor": "xcstrings", "kind": "xcstrings"},
            "stats": {"keys": len(entries), "kind": "xcstrings"},
            "conflicts": conflicts, "warnings": warnings}


def _apply_xcstrings_variations(key: str, loc: Optional[str], variations: Dict[str, Any],
                                by_loc: Dict[Tuple[Optional[str], str], List[str]]) -> None:
    for vtype, vdict in variations.items():
        if not isinstance(vdict, dict):
            continue
        for vname, vobj in vdict.items():
            vk = key + "." + str(vtype) + "." + str(vname)
            if isinstance(vobj, dict):
                su = vobj.get("stringUnit")
                if isinstance(su, dict) and isinstance(su.get("value"), str):
                    vals = by_loc.get((loc, vk)) or by_loc.get((None, vk))
                    if vals:
                        su["value"] = vals.pop(0)


def _build_xcstrings(table: LocTable, base_bytes: bytes) -> bytes:
    doc = decode_text(base_bytes, "base")
    try:
        root = json.loads(doc.text)
    except ValueError:
        raise PB3Error("E_CORRUPT", "xcstrings gốc sai cú pháp JSON", where="base")
    if not isinstance(root, dict) or not isinstance(root.get("strings"), dict):
        raise PB3Error("E_UNSUPPORTED_FORMAT", "xcstrings gốc thiếu mục strings", where="base")
    # Hàng đợi theo (locale, key): cùng key ở nhiều locale phải sửa đúng locale,
    # kể cả khi bảng chỉ sửa 1 trong các locale đó.
    by_loc: Dict[Tuple[Optional[str], str], List[str]] = {}
    for e in table.entries:
        if e.status in ("ok", "empty"):
            by_loc.setdefault((e.locale, e.key), []).append(e.value)
    for key, ent in root["strings"].items():
        if not isinstance(ent, dict):
            continue
        locs = ent.get("localizations")
        if isinstance(locs, dict):
            for loc, lobj in locs.items():
                if not isinstance(lobj, dict):
                    continue
                su = lobj.get("stringUnit")
                if isinstance(su, dict) and isinstance(su.get("value"), str):
                    vals = by_loc.get((str(loc), str(key))) or by_loc.get((None, str(key)))
                    if vals:
                        su["value"] = vals.pop(0)
                variations = lobj.get("variations")
                if isinstance(variations, dict):
                    _apply_xcstrings_variations(str(key), str(loc), variations, by_loc)
        variations = ent.get("variations")
        if isinstance(variations, dict):
            _apply_xcstrings_variations(str(key), None, variations, by_loc)
    out = json.dumps(root, ensure_ascii=False, indent=2) + "\n"
    return doc.encode(out)


class ApplePlistPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".plist", ".stringsdict", ".xcstrings")
    FLAVOR = "plist"
    SELFTEST_BASE = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
                     b'<plist version="1.0">\n<dict>\n'
                     b'\t<key>hello</key>\n\t<string>Xin chao</string>\n'
                     b'\t<key>bye</key>\n\t<string>Tam biet</string>\n'
                     b'</dict>\n</plist>\n')

    def _sniff(self, head: bytes, path: str) -> float:
        if head.startswith(b"bplist00"):
            return 0.9
        if b"<?xml" in head and b"<plist" in head:
            return 0.8
        if path.lower().endswith(".xcstrings") and b'"strings"' in head:
            return 0.8
        return 0.0

    def _is_writable(self, path: str, head: bytes) -> bool:
        return not head.startswith(b"bplist00")

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        kind = _detect_kind(data)
        if kind == "xcstrings":
            return _parse_xcstrings(data, path, locale)
        try:
            root = plistlib.loads(data)
        except Exception:
            raise PB3Error("E_CORRUPT", "plist sai cú pháp hoặc bị cắt cụt", where=path)
        if not isinstance(root, (dict, list)):
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "plist phải là dict hoặc danh sách ở gốc", where=path)
        pairs: List[_Pair] = []
        warnings: List[Dict[str, Any]] = []
        stats: Dict[str, Any] = {"kind": kind}
        if isinstance(root, dict):
            for k, v in root.items():
                _walk_plist(v, str(k), pairs, warnings, stats)
        else:
            for i, x in enumerate(root):
                _walk_plist(x, stable_row_key(i + 1), pairs, warnings, stats)
        entries, conflicts = make_entries(
            pairs, path, len(data), "utf-8" if kind == "xml" else "binary", locale)
        stats["keys"] = len(entries)
        return {"entries": entries,
                "schema_hint": {"flavor": "plist", "kind": kind},
                "stats": stats, "conflicts": conflicts, "warnings": warnings}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        kind = _detect_kind(base_bytes)
        if kind == "binary":
            raise PB3Error("E_WRITE_DISABLED", "plist nhị phân chỉ đọc, không ghi ngược được",
                           plugin_id=self.id)
        if kind == "xcstrings":
            return _build_xcstrings(table, base_bytes)
        try:
            root = plistlib.loads(base_bytes)
        except Exception:
            raise PB3Error("E_CORRUPT", "plist gốc sai cú pháp", where="base")
        queue = value_queue(table)
        notes_by_key: Dict[str, List[str]] = {}
        for e in table.entries:
            if e.status in ("ok", "empty"):
                notes_by_key.setdefault(e.key, e.notes)
        applied: set = set()
        if isinstance(root, dict):
            for k in list(root.keys()):
                root[k] = _apply_plist(root[k], str(k), queue, notes_by_key, applied)
        elif isinstance(root, list):
            for i, x in enumerate(root):
                root[i] = _apply_plist(x, stable_row_key(i + 1), queue, notes_by_key, applied)
        for k, vals in queue.items():
            if vals and k in applied:
                table.warnings.append({"code": "E_PARSE_PARTIAL", "key": k,
                                       "msg": "key trùng: chỉ ghi bản ghi đầu, bỏ %d giá trị sau"
                                              % len(vals)})
        out = plistlib.dumps(root, fmt=plistlib.FMT_XML, sort_keys=False)
        if base_bytes.startswith(b"\xef\xbb\xbf"):
            out = b"\xef\xbb\xbf" + out
        return out
