# Plugin định dạng CSV/TSV (Claude 06): bảng key-value dạng lưới.
# Header (nếu có) cho tên cột làm locale; không header thì cột 0=key, cột 1=value.
# Ghi ngược: parse lại lưới từ file gốc, áp giá trị theo vị trí ô trong notes
# ("csv:<hàng>:<cột>", 0-based tính từ dòng dữ liệu), serialize lại bằng csv.writer.
# Ô không sửa giữ nguyên nội dung khi parse lại (quoting có thể đổi).
# Kiểm tra: python3 menu.py plugin test csv_table
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.discovery import normalize_locale_name
from sdk.formats import FormatPluginBase, decode_text, make_entries, stable_row_key
from sdk.types import LocTable

MANIFEST = {
    "id": "csv_table",
    "name": "CSV/TSV localization",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".csv", ".tsv"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:CsvPlugin",
    "selftest": True,
    "docs": "Bảng CSV/TSV: dòng đầu là header nếu có ô tên key/id/name/string_id/key_id "
            "(không phân biệt hoa thường); cột key là cột đầu mang tên đó, nếu không có "
            "thì cột 0; các cột còn lại là cột giá trị, tên cột làm locale. Không header: "
            "cột 0 là key, cột 1 là value, các cột sau bị bỏ qua. Ô rỗng vẫn giữ entry "
            "(status empty). Key trùng (cùng locale) báo xung đột, giữ bản ghi đầu. "
            "Delimiter đoán bằng cách đếm , ; tab | ở 5 dòng đầu (hòa thì .csv lấy ',', "
            ".tsv lấy tab). Ghi ngược giữ delimiter và kiểu xuống dòng gốc; quoting có "
            "thể đổi nhưng nội dung parse lại giữ nguyên.",
}

_KEY_NAMES = {"key", "id", "name", "string_id", "key_id"}
_DELIMS = (",", ";", "\t", "|")
_NOTE_RE = re.compile(r"^csv:(\d+):(\d+)$")
# Tên cột KHÔNG phải ngôn ngữ: bỏ hẳn (không thành locale, không ra entry)
# — cột ghi chú/diễn giải, cột trống (đợt sửa 2026-11-16: 'Notes', col13/14
# của PB3_LOC_DEFAULT.csv trước đây thành locale "Notes" rồi bị lọc loại ở
# vòng export; nay bỏ ngay từ lúc đọc).
_DROP_COL_NAMES = {"notes", "note", "comment", "comments"}
# Tên cột giá trị CHUNG (không phải tên ngôn ngữ nhưng cột chứa text dịch,
# vd bảng key,value): giữ entry, không gắn locale (locale None) thay vì bỏ
# — bỏ hẳn sẽ mất text thật; gắn locale None thì vòng lọc mềm vẫn giữ lại
# (nội dung không gắn ngôn ngữ không thể gọi là "không phải en/zh").
_VALUE_COL_NAMES = {"value", "text", "translation", "string", "val", "txt",
                    "content", "message", "label", "translated", "target"}


def _col_locale(header_name):
    """Tên cột header -> mã locale chuẩn, None (giữ cột, không gắn locale),
    hoặc False (bỏ cột). 'English'->'en', 'EN'->'en'; cột không phải ngôn
    ngữ và không phải cột giá trị chung (vd 'Notes', 'Max Length', cột
    trống) thì bỏ. Ngôn ngữ ngoài bảng LOCALE_BUILTIN (vd Czech) hiện cũng
    bị bỏ — ghi nhận là giới hạn (trước đây thành locale giả "Czech" rồi
    cũng bị lọc loại khỏi output en/zh)."""
    loc = normalize_locale_name(header_name)
    if loc is not None:
        return loc
    raw = (header_name or "").strip()
    if not raw or re.match(r"^col\d+$", raw, re.IGNORECASE):
        return False
    low = raw.lower()
    if low in _DROP_COL_NAMES:
        return False
    if low in _VALUE_COL_NAMES:
        return None
    return False


def _detect_delimiter(text: str, path: str) -> str:
    lines = text.splitlines()[:5]
    counts = {d: sum(ln.count(d) for ln in lines) for d in _DELIMS}
    best = max(counts.values())
    if best == 0:
        return "\t" if path.lower().endswith(".tsv") else ","
    winners = [d for d in _DELIMS if counts[d] == best]
    if len(winners) == 1:
        return winners[0]
    if path.lower().endswith(".tsv") and "\t" in winners:
        return "\t"
    if path.lower().endswith(".csv") and "," in winners:
        return ","
    return winners[0]


def _read_grid(text: str, path: str):
    """Trả (lưới, delimiter, line_terminator). Lỗi csv -> E_CORRUPT."""
    delim = _detect_delimiter(text, path)
    term = "\r\n" if "\r\n" in text else "\n"
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim, strict=True))
    except csv.Error:
        raise PB3Error("E_CORRUPT", "CSV sai cú pháp (trích dẫn không đóng hoặc sai định dạng)",
                       where=path)
    return rows, delim, term


class CsvPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".csv", ".tsv")
    FLAVOR = "csv"
    SELFTEST_BASE = b"key,en,vi\nhello,Xin chao,Chao ban\nbye,Tam biet,Chao nhe\n"

    def _sniff(self, head: bytes, path: str) -> float:
        try:
            text = head.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - sniff không được làm hỏng detect
            return 0.0
        if "\n" in text and any(d in text for d in _DELIMS):
            return 0.7
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        doc = decode_text(data, path)
        rows, delim, _term = _read_grid(doc.text, path)
        if not rows:
            raise PB3Error("E_UNSUPPORTED_FORMAT", "file CSV/TSV rỗng, không có dòng nào",
                           where=path)
        header = rows[0]
        has_header = any((c or "").strip().lower() in _KEY_NAMES for c in header)
        if has_header:
            key_col = next((i for i, c in enumerate(header)
                            if (c or "").strip().lower() in _KEY_NAMES), 0)
            value_cols = [i for i in range(len(header)) if i != key_col]
            # Đợt sửa 2026-11-16: chuẩn hoá tên cột thành mã locale NGAY khi
            # đọc header, trước mọi so sánh lọc ('English'->'en' — trước đây
            # cột tiếng Anh cũng bị lọc loại vì 'English' != 'en'). Cột
            # không phải ngôn ngữ ('Notes', col13/col14 trống...) bị bỏ hẳn;
            # cột giá trị chung ('value'...) giữ entry với locale None.
            col_locale = []
            for i in value_cols:
                loc = _col_locale(header[i])
                if loc is not False:
                    col_locale.append((i, loc))
            data_rows = rows[1:]
            ncols = len(header)
        else:
            key_col, col_locale = 0, [(1, locale)]
            data_rows = rows
            ncols = max((len(r) for r in rows), default=0)
        pairs: List[Tuple] = []
        for r, row in enumerate(data_rows):
            key = row[key_col] if key_col < len(row) else ""
            if not (key or "").strip():
                # BUG#2: dòng HOÀN TOÀN TRỐNG (không key, mọi giá trị đều
                # rỗng — vd 2 dòng trống trong PB3_LOC_DEFAULT.csv) không
                # phải term: bỏ hẳn thay vì sinh key vị trí row_NNNNNN
                # rỗng rồi lọt vào file locale đã gộp. Dòng key rỗng
                # nhưng CÓ giá trị vẫn giữ key vị trí (đặc tả 7.5;
                # test_empty_key_gets_stable_row_key).
                if all(not (row[c] if c < len(row) else "").strip()
                       for c, _loc in col_locale):
                    continue
                key = stable_row_key(r)
            for c, loc in col_locale:
                val = row[c] if c < len(row) else ""
                notes = ["csv:%d:%d" % (r, c)]
                pairs.append((key, val, "ok", notes, loc))
        entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
        stats = {"rows": len(data_rows), "columns": ncols,
                 "delimiter": {"\t": "tab"}.get(delim, delim), "header": has_header}
        return {"entries": entries, "schema_hint": {"flavor": "csv", "delimiter": stats["delimiter"],
                                                    "header": has_header},
                "stats": stats, "conflicts": conflicts, "warnings": []}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        doc = decode_text(base_bytes, "base")
        rows, delim, term = _read_grid(doc.text, "base")
        if not rows:
            raise PB3Error("E_CORRUPT", "file CSV/TSV gốc rỗng", where="base")
        header = rows[0]
        has_header = any((c or "").strip().lower() in _KEY_NAMES for c in header)
        offset = 1 if has_header else 0
        if has_header:
            key_col = next((i for i, c in enumerate(header)
                            if (c or "").strip().lower() in _KEY_NAMES), 0)
            col_locale = []
            for i in range(len(header)):
                if i == key_col:
                    continue
                loc = _col_locale(header[i])
                if loc is not False:
                    col_locale.append((i, loc))
        else:
            key_col, col_locale = 0, [(1, table.locale)]
        # Áp giá trị theo vị trí ô trong notes; chỉ ghi entry ok/empty (bản trùng giữ nguyên).
        # Entry không có note vị trí (vd bảng tự dựng trong selftest): tìm ô đầu tiên
        # khớp key (và locale nếu entry có locale), chưa bị ghi bởi entry trước.
        used = set()
        for e in table.entries:
            if e.status not in ("ok", "empty"):
                continue
            placed = False
            for note in e.notes:
                m = _NOTE_RE.match(note)
                if m is None:
                    continue
                r, c = int(m.group(1)), int(m.group(2))
                gi = r + offset
                if gi >= len(rows):
                    continue
                row = rows[gi]
                while len(row) <= c:
                    row.append("")
                row[c] = e.value
                used.add((gi, c))
                placed = True
            if placed:
                continue
            for gi in range(offset, len(rows)):
                row = rows[gi]
                key = row[key_col] if key_col < len(row) else ""
                if (key or "").strip() != e.key:
                    continue
                for c, loc in col_locale:
                    if (gi, c) in used:
                        continue
                    if e.locale is not None and loc != e.locale:
                        continue
                    while len(row) <= c:
                        row.append("")
                    row[c] = e.value
                    used.add((gi, c))
                    placed = True
                    break
                if placed:
                    break
        out = io.StringIO(newline="")
        w = csv.writer(out, delimiter=delim, lineterminator=term, quoting=csv.QUOTE_MINIMAL)
        w.writerows(rows)
        return doc.encode(out.getvalue())
