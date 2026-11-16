# Plugin định dạng JSON (Claude 06): parse JSON localization thành LocTable + ghi ngược.
# Giữ nguyên key, thứ tự; object lồng nhau thành key chấm (i18next); số/bool/null
# giữ đúng kiểu qua ghi chú; key trùng giữ bản ghi đầu (đặc tả 7.5).
# Ghi ngược: dựng lại JSON (indent gốc nếu đoán được, UTF-8/UTF-16 + BOM gốc).
# Không bảo toàn comment (JSON không có comment chuẩn) và định dạng khoảng trắng gốc.
# Kiểm tra: python3 menu.py plugin test json_text
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import (FormatPluginBase, decode_text, make_entries,
                         stable_row_key, value_queue)
from sdk.types import LocEntry, LocTable, SourceRef

MANIFEST = {
    "id": "json_text",
    "name": "JSON localization",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".json"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:JsonPlugin",
    "selftest": True,
    "docs": "JSON object/danh sách object key-value, object lồng nhau (key chấm), "
            "mảng chuỗi (key[i]), số/bool/null giữ kiểu. Ghi ngược dựng lại JSON "
            "(không giữ comment/khoảng trắng gốc). Không hỗ trợ JSONC/JSON5 "
            "(comment/trailing comma -> lỗi), không hỗ trợ key trùng kiểu khác nhau "
            "sau khi làm phẳng (báo xung đột, giữ bản ghi đầu).",
}

_SCALAR_NOTES = {bool: "json:boolean", int: "json:number", float: "json:number",
                 type(None): "json:null"}


def _scalar_note(v: Any) -> str:
    if isinstance(v, bool):
        return "json:boolean"
    if isinstance(v, (int, float)):
        return "json:number"
    if v is None:
        return "json:null"
    return ""


def _scalar_text(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return json.dumps(v, ensure_ascii=False)


def _detect_indent(text: str) -> int:
    for ln in text.split("\n"):
        m = re.match(r"^( +)\S", ln)
        if m and len(m.group(1)) in (2, 4):
            return len(m.group(1))
    return 2


class _Pairs:
    """object_pairs_hook giữ bản ghi đầu khi key trùng, ghi lại giá trị bị rớt."""

    def __init__(self):
        self.dups: List[Tuple[str, Any]] = []

    def __call__(self, pairs):
        out: Dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                self.dups.append((k, v))
            else:
                out[k] = v
        return out


def _walk(value: Any, prefix: str, pairs: List[Tuple[str, str, str, List[str]]],
          warnings: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    if isinstance(value, str):
        pairs.append((prefix, value, "ok", []))
    elif isinstance(value, dict):
        if not value:
            pairs.append((prefix, "{}", "ok", ["json:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        for k, v in value.items():
            _walk(v, (prefix + "." + str(k)) if prefix else str(k), pairs, warnings, stats)
    elif isinstance(value, list):
        if not value:
            pairs.append((prefix, "[]", "ok", ["json:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        if all(isinstance(x, str) for x in value):
            for i, x in enumerate(value):
                pairs.append(("%s[%d]" % (prefix, i), x, "ok", []))
        elif all(isinstance(x, dict) for x in value):
            for i, x in enumerate(value):
                _walk(x, "%s[%d]" % (prefix, i), pairs, warnings, stats)
        else:
            pairs.append((prefix, json.dumps(value, ensure_ascii=False), "ok", ["json:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            warnings.append({"code": "E_PARSE_PARTIAL", "key": prefix,
                             "msg": "giá trị phức tạp giữ nguyên dạng JSON"})
    else:
        pairs.append((prefix, _scalar_text(value), "ok", [_scalar_note(value)]))


def _restore(value: str, notes: List[str]) -> Any:
    """Khôi phục kiểu gốc từ ghi chú (số/bool/null/phức tạp); lỗi -> giữ chuỗi."""
    if "json:complex" in notes:
        try:
            return json.loads(value)
        except ValueError:
            return value
    for note in notes:
        if note in ("json:number", "json:boolean", "json:null"):
            try:
                return json.loads(value)
            except ValueError:
                return value
    return value


_INDEX_RE = re.compile(r"^(.*)\[(\d+)\]$")


def _set_path(obj: Any, dotted: str, value: Any) -> bool:
    """Đặt giá trị theo key chấm / key[i]. Trả False nếu đường dẫn không có trong gốc."""
    parts = dotted.split(".")
    cur = obj
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        m = _INDEX_RE.match(part)
        if m is not None:
            name, idx = m.group(1), int(m.group(2))
            if not isinstance(cur, dict) or name not in cur or not isinstance(cur[name], list):
                return False
            lst = cur[name]
            if idx >= len(lst):
                return False
            if last:
                lst[idx] = value
                return True
            cur = lst[idx]
            continue
        if not isinstance(cur, dict) or part not in cur:
            return False
        if last:
            cur[part] = value
            return True
        cur = cur[part]
    return False


class JsonPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".json",)
    FLAVOR = "json"
    SELFTEST_BASE = b'{"hello": "Xin chao", "bye": "Tam biet"}\n'

    def _sniff(self, head: bytes, path: str) -> float:
        s = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        if s[:1] in (b"{", b"["):
            return 0.8
        if head.startswith(b"\xff\xfe") and head[2:4] in (b"{\x00", b"[\x00"):
            return 0.8
        if head.startswith(b"\xfe\xff") and head[2:4] in (b"\x00{", b"\x00["):
            return 0.8
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        doc = decode_text(data, path)
        pairs_hook = _Pairs()
        try:
            root = json.loads(doc.text, object_pairs_hook=pairs_hook)
        except ValueError:
            raise PB3Error("E_CORRUPT", "JSON sai cú pháp", where=path)
        pairs: List[Tuple[str, str, str, List[str]]] = []
        warnings: List[Dict[str, Any]] = []
        stats: Dict[str, Any] = {}
        if isinstance(root, dict):
            for k, v in root.items():
                _walk(v, str(k), pairs, warnings, stats)
        elif isinstance(root, list):
            if all(isinstance(x, str) for x in root):
                for i, x in enumerate(root):
                    pairs.append((stable_row_key(i + 1), x, "ok", []))
            elif all(isinstance(x, dict) for x in root):
                for item in root:
                    kf = next((f for f in ("key", "id", "name") if f in item), None)
                    vf = next((f for f in ("value", "text", "translation") if f in item), None)
                    if kf is None or vf is None or not isinstance(item.get(kf), str):
                        raise PB3Error("E_UNSUPPORTED_FORMAT",
                                       "danh sách JSON phải là object có key/value", where=path)
                    v = item[vf]
                    if isinstance(v, str):
                        pairs.append((str(item[kf]), v, "ok", []))
                    elif isinstance(v, (bool, int, float)) or v is None:
                        pairs.append((str(item[kf]), _scalar_text(v), "ok", [_scalar_note(v)]))
                    else:
                        raise PB3Error("E_UNSUPPORTED_FORMAT",
                                       "giá trị trong danh sách JSON phải là chuỗi/số", where=path)
            else:
                raise PB3Error("E_UNSUPPORTED_FORMAT",
                               "JSON phải là object hoặc danh sách object key-value", where=path)
        else:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "JSON phải là object hoặc danh sách, không phải giá trị đơn", where=path)
        entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
        # Key trùng trong JSON: giá trị bị rớt vẫn giữ trong bảng (status conflict)
        # để không mất dữ liệu im lặng; build chỉ ghi bản ghi đầu + cảnh báo.
        for k, v in pairs_hook.dups:
            if isinstance(v, str):
                val, notes = v, []
            elif isinstance(v, (bool, int, float)) or v is None:
                val, notes = _scalar_text(v), [_scalar_note(v)]
            else:
                val, notes = json.dumps(v, ensure_ascii=False), ["json:complex"]
            entries.append(LocEntry(k, val, locale, "native", "conflict",
                                    SourceRef([path], path, None, len(data), doc.encoding, {}),
                                    notes + ["json:trùng"]))
            conflicts.append({"key": k, "msg": "key trùng trong JSON, giữ bản ghi đầu"})
        stats["keys"] = len(entries)
        stats["dups"] = len(pairs_hook.dups)
        return {"entries": entries, "schema_hint": {"flavor": "json", "indent": _detect_indent(doc.text)},
                "stats": stats, "conflicts": conflicts, "warnings": warnings}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        doc = decode_text(base_bytes, "base")
        pairs_hook = _Pairs()
        try:
            root = json.loads(doc.text, object_pairs_hook=pairs_hook)
        except ValueError:
            raise PB3Error("E_CORRUPT", "JSON gốc sai cú pháp", where="base")
        if not isinstance(root, (dict, list)):
            raise PB3Error("E_UNSUPPORTED_FORMAT", "JSON gốc không phải object/danh sách", where="base")
        queue = value_queue(table)
        notes_by_key: Dict[str, List[str]] = {}
        for e in table.entries:
            if e.status in ("ok", "empty"):
                notes_by_key.setdefault(e.key, e.notes)
        # Thu thập đường dẫn lá theo thứ tự duyệt gốc, rồi áp hàng đợi theo key.
        leaves: List[str] = []
        applied_keys = set()

        def _collect(prefix: str, node: Any) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    _collect((prefix + "." + str(k)) if prefix else str(k), v)
            elif isinstance(node, list):
                for i, x in enumerate(node):
                    _collect("%s[%d]" % (prefix, i), x)
            else:
                leaves.append(prefix)

        if isinstance(root, dict):
            for k, v in root.items():
                _collect(str(k), v)
        else:
            for i, x in enumerate(root):
                _collect(stable_row_key(i + 1), x)
        for key in leaves:
            if queue.get(key):
                value = queue[key].pop(0)
                restored = _restore(value, notes_by_key.get(key, []))
                applied_keys.add(key)
                if isinstance(root, list) and key.startswith("row_"):
                    try:
                        root[int(key[4:]) - 1] = restored
                    except (ValueError, IndexError):
                        pass
                else:
                    _set_path(root, key, restored)
        # Danh sách object key/value ở gốc: áp theo key của từng object.
        if isinstance(root, list) and root and all(isinstance(x, dict) for x in root):
            for item in root:
                kf = next((f for f in ("key", "id", "name") if f in item), None)
                vf = next((f for f in ("value", "text", "translation") if f in item), None)
                if kf is None or vf is None:
                    continue
                k = str(item[kf])
                if queue.get(k):
                    item[vf] = _restore(queue[k].pop(0), notes_by_key.get(k, []))
                    applied_keys.add(k)
        # Key trùng: hàng đợi còn dư -> chỉ ghi bản ghi đầu, cảnh báo bản sau bị bỏ.
        for k, vals in queue.items():
            if vals and k in applied_keys:
                table.warnings.append({"code": "E_PARSE_PARTIAL", "key": k,
                                       "msg": "key trùng: chỉ ghi bản ghi đầu, bỏ %d giá trị sau"
                                              % len(vals)})
        out = json.dumps(root, ensure_ascii=False, indent=_detect_indent(doc.text)) + "\n"
        return doc.encode(out)
