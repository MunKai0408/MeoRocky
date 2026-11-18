# Plugin định dạng CastleDB (.cdb) — port từ IPA-LOCALIZATION
# js/modules/text_extract.js (parseCdb) + translation_apply.js (setter).
# Chỉ lấy các cột có kind="localizable" (game tự đánh dấu cột cần dịch),
# kể cả sheet con "cha@cột"; key dạng `Sheet[Id].col` (sheet con:
# `Sheet[Id].col[SubId].col2`). Cột khác, id, tag, đường dẫn tài nguyên
# giữ nguyên. Ghi ngược: sửa đúng ô localizable trong object JSON gốc
# (giữ thứ tự key), rồi đọc lại xác minh (lớp nền build).
#
# Bằng chứng: file data.cdb thật của Dead Cells (trong res4.pak, Heaps)
# cho 3192 dòng localizable theo app IPA-LOCALIZATION (HANDOFF v0.16.3);
# port Python này mới kiểm chứng trên fixture mô phỏng đúng cấu trúc đó
# (sheet + cột localizable + sheet con), chưa chạy trên data.cdb thật
# (tải IPA Dead Cells bị Drive chặn quota 00:33 +07) nên giữ nhãn
# [HYPOTHESIS] cho tới khi chạy lại được.
# Kiểm tra: python3 menu.py plugin test castle_db
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries, value_queue
from sdk.types import LocTable

MANIFEST = {
    "id": "castle_db",
    "name": "CastleDB (.cdb)",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M2",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".cdb"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:CastleDbPlugin",
    "selftest": True,
    "docs": "CastleDB (.cdb, JSON): chỉ dịch giá trị các cột có "
            "kind=\"localizable\" (kể cả sheet con \"cha@cột\"); giữ nguyên "
            "id, tag, đường dẫn tài nguyên và mọi cột khác. Key dạng "
            "`Sheet[Id].col`. File JSON không có mảng sheets thì không phải "
            "CastleDB (nhường cho plugin JSON thường). Ghi ngược dựng lại "
            "JSON (giữ thứ tự key, indent gốc), đọc lại xác minh.",
}

MAX_ROWS = 300000
MAX_DEPTH = 8

_SHEETS_HINT_RE = re.compile(r'"sheets"\s*:')
_COLUMNS_HINT_RE = re.compile(r'"columns"\s*:')


def _loc_cols(sheet: Dict[str, Any]) -> Dict[str, bool]:
    out = {}  # type: Dict[str, bool]
    for col in sheet.get("columns") or []:
        if isinstance(col, dict) and col.get("name"):
            out[str(col["name"])] = col.get("kind") == "localizable"
    return out


def _line_id(line: Dict[str, Any], index: int) -> str:
    ident = line.get("id")
    if ident is None:
        return str(index)
    return str(ident)


def parse_cdb_bytes(data: bytes, path: str,
                    locale: Optional[str] = None) -> Dict[str, Any]:
    """Parse .cdb (CastleDB) -> dict {entries, schema_hint, stats,
    conflicts, warnings} như FormatPluginBase._parse_bytes. Raise
    PB3Error E_UNSUPPORTED_FORMAT khi JSON không có mảng sheets (không
    phải CastleDB) — để plugin JSON thường nhận thay."""
    doc = decode_text(data, path)
    try:
        root = json.loads(doc.text)
    except ValueError:
        raise PB3Error("E_CORRUPT", "CastleDB sai cú pháp JSON", where=path)
    if not isinstance(root, dict) or not isinstance(root.get("sheets"), list):
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "JSON không có mảng sheets (không phải CastleDB)",
                       where=path)
    sheets = root["sheets"]
    by_name = {}  # type: Dict[str, Dict[str, Any]]
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name"):
            by_name[str(sheet["name"])] = sheet
    pairs = []  # type: List[Tuple[str, str, str, List[str]]]
    n_cols = 0

    def walk_line(sheet: Dict[str, Any], line: Any, prefix: str,
                  depth: int) -> None:
        nonlocal n_cols
        if (not isinstance(line, dict) or depth > MAX_DEPTH
                or len(pairs) >= MAX_ROWS):
            return
        loc = _loc_cols(sheet)
        columns = sheet.get("columns") or []
        for col in columns:
            if not isinstance(col, dict) or not col.get("name"):
                continue
            name = str(col["name"])
            value = line.get(name)
            if loc.get(name):
                n_cols += 1
                if isinstance(value, str) and value != "":
                    pairs.append((prefix + "." + name, value, "ok",
                                  ["CDB localizable"]))
                continue
            if isinstance(value, list):
                sub = by_name.get(str(sheet.get("name")) + "@" + name)
                if sub is not None:
                    for i, item in enumerate(value):
                        walk_line(
                            sub, item,
                            prefix + "." + name + "["
                            + _line_id(item, i) + "]" if isinstance(item, dict)
                            else prefix + "." + name + "[%d]" % i,
                            depth + 1)

    sheet_names = []
    for sheet in sheets:
        if (not isinstance(sheet, dict) or not sheet.get("name")
                or "@" in str(sheet["name"])
                or not isinstance(sheet.get("lines"), list)):
            continue
        sheet_names.append(str(sheet["name"]))
        for i, line in enumerate(sheet["lines"]):
            walk_line(sheet, line,
                      str(sheet["name"]) + "["
                      + _line_id(line, i) + "]" if isinstance(line, dict)
                      else str(sheet["name"]) + "[%d]" % i, 0)
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding,
                                      locale)
    return {"entries": entries,
            "schema_hint": {"flavor": "cdb-localizable",
                            "sheets": sheet_names},
            "stats": {"keys": len(entries), "sheets": len(sheet_names),
                      "localizable_cells": n_cols},
            "conflicts": conflicts, "warnings": []}


def _detect_indent(text: str) -> int:
    for ln in text.split("\n"):
        m = re.match(r"^( +)\S", ln)
        if m and len(m.group(1)) in (2, 4):
            return len(m.group(1))
    return 2


def apply_cdb_translations(root: Dict[str, Any],
                           queue: Dict[str, List[str]]) -> set:
    """Áp hàng đợi giá trị theo key vào đúng ô localizable của object CDB
    đã parse (sửa tại chỗ). Trả tập key đã áp. Cấu trúc/khóa/thứ tự giữ
    nguyên; ô không phải localizable không bao giờ bị sửa."""
    sheets = root.get("sheets") if isinstance(root, dict) else None
    if not isinstance(sheets, list):
        return set()
    by_name = {}  # type: Dict[str, Dict[str, Any]]
    for sheet in sheets:
        if isinstance(sheet, dict) and sheet.get("name"):
            by_name[str(sheet["name"])] = sheet
    applied = set()  # type: set

    def walk_line(sheet: Dict[str, Any], line: Any, prefix: str,
                  depth: int) -> None:
        if (not isinstance(line, dict) or depth > MAX_DEPTH):
            return
        loc = _loc_cols(sheet)
        columns = sheet.get("columns") or []
        for col in columns:
            if not isinstance(col, dict) or not col.get("name"):
                continue
            name = str(col["name"])
            value = line.get(name)
            if loc.get(name):
                if isinstance(value, str) and value != "":
                    key = prefix + "." + name
                    if queue.get(key):
                        line[name] = queue[key].pop(0)
                        applied.add(key)
                continue
            if isinstance(value, list):
                sub = by_name.get(str(sheet.get("name")) + "@" + name)
                if sub is not None:
                    for i, item in enumerate(value):
                        walk_line(
                            sub, item,
                            prefix + "." + name + "["
                            + _line_id(item, i) + "]" if isinstance(item, dict)
                            else prefix + "." + name + "[%d]" % i,
                            depth + 1)

    for sheet in sheets:
        if (not isinstance(sheet, dict) or not sheet.get("name")
                or "@" in str(sheet["name"])
                or not isinstance(sheet.get("lines"), list)):
            continue
        for i, line in enumerate(sheet["lines"]):
            walk_line(sheet, line,
                      str(sheet["name"]) + "["
                      + _line_id(line, i) + "]" if isinstance(line, dict)
                      else str(sheet["name"]) + "[%d]" % i, 0)
    return applied


class CastleDbPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".cdb",)
    FLAVOR = "cdb-localizable"
    SELFTEST_BASE = (
        b'{"sheets": [{"name": "items", "columns": [{"name": "id"}, '
        b'{"name": "name", "kind": "localizable"}], "lines": '
        b'[{"id": "Sword", "name": "Sword of doom"}]}]}')

    def _sniff(self, head: bytes, path: str) -> float:
        # JSON object có mảng sheets + columns trong 4 KiB đầu thì chắc là CDB.
        # Chỉ tìm marker ASCII nên decode lỗi thì bỏ qua ký tự lỗi.
        text = head.decode("utf-8", errors="ignore")
        stripped = text.lstrip("\ufeff \t\r\n")
        if not stripped.startswith("{"):
            return 0.0
        if _SHEETS_HINT_RE.search(text) and _COLUMNS_HINT_RE.search(text):
            return 0.9
        return 0.0

    def _parse_bytes(self, data: bytes, path: str,
                     locale: Optional[str]) -> Dict[str, Any]:
        return parse_cdb_bytes(data, path, locale)

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        doc = decode_text(base_bytes, "base")
        try:
            root = json.loads(doc.text)
        except ValueError:
            raise PB3Error("E_CORRUPT", "CastleDB gốc sai cú pháp JSON",
                           where="base")
        if not isinstance(root, dict) or not isinstance(root.get("sheets"), list):
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "JSON gốc không có mảng sheets", where="base")
        queue = value_queue(table)
        applied = apply_cdb_translations(root, queue)
        for key, vals in queue.items():
            if vals and key in applied:
                table.warnings.append(
                    {"code": "E_PARSE_PARTIAL", "key": key,
                     "msg": "key trùng: chỉ ghi bản ghi đầu, bỏ %d giá trị sau"
                            % len(vals)})
        out = json.dumps(root, ensure_ascii=False,
                         indent=_detect_indent(doc.text)) + "\n"
        return doc.encode(out)
