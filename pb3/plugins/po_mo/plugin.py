# Plugin định dạng PO/MO (Claude 06): parse file gettext PO (văn bản) và MO (nhị phân).
# PO: msgid/msgstr, msgid_plural + msgstr[n], msgctxt (key = msgctxt + "\x04" + msgid
# theo quy ước gettext), chuỗi nhiều dòng, entry fuzzy/obsolete. Ghi ngược PO bằng
# cách thay đúng span của giá trị msgstr, giữ nguyên comment/flag/thứ tự entry.
# MO: chỉ đọc (ghi MO nhị phân không xác minh được bằng verify mặc định nên không khai
# ghi được; build MO -> E_WRITE_DISABLED). Số nhiều: entry msgid_plural có key
# msgid + " [plural]" (giá trị là chuỗi số nhiều gốc), msgstr[1..n] có key msgid + "[n]".
# Giới hạn: không hỗ trợ entry có msgid chứa ký tự NUL, không giữ định dạng ngắt dòng
# gốc của giá trị dài (ghi lại ngắt ở ~78 cột), không hỗ trợ PO mã hóa khác UTF-8/UTF-16.
# Kiểm tra: python3 menu.py plugin test po_mo
from __future__ import annotations

import re
import struct
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import FormatPluginBase, decode_text, make_entries, value_queue
from sdk.types import LocTable

MANIFEST = {
    "id": "po_mo",
    "name": "PO/MO gettext",
    "version": "1.1.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".po", ".mo"]},
    "write_modes": ["REBUILD_SECTION"],
    # caps-po: nới lên 32 MiB để file .po lớn (vd DST 17-19 MB, ~87,8k entry)
    # parse được; trần host (256 MiB) vẫn là trần cứng phía trên.
    "limits": {"max_entry_bytes": 32 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:PoMoPlugin",
    "selftest": True,
    "docs": "File gettext PO (đọc + ghi) và MO nhị phân (chỉ đọc). PO hỗ trợ msgid/msgstr, "
            "msgid_plural + msgstr[n], msgctxt (key = msgctxt + \\x04 + msgid), chuỗi nhiều "
            "dòng, escape, entry fuzzy (ghi chú + cảnh báo) và entry obsolete (bỏ qua). Ghi "
            "ngược PO chỉ thay đúng span giá trị msgstr, giữ comment/flag/thứ tự. MO chỉ đọc "
            "vì verify mặc định không phân biệt được byte lật trong file nhị phân. Không hỗ "
            "trợ PO mã hóa khác UTF-8/UTF-16, không giữ cách ngắt dòng gốc của giá trị dài.",
}

_MO_MAGIC_LE = b"\xde\x12\x04\x95"
_MO_MAGIC_BE = b"\x95\x04\x12\xde"
_DIRECTIVE_RE = re.compile(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)([ \t]+.*)?$")
_NOTE_LEN = 80
_WRAP_COL = 76

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
            "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _unescape(raw: str, path: str) -> str:
    """Giải escape PO; escape lạ giữ nguyên ký tự sau dấu \\; \\ cuối chuỗi -> E_CORRUPT."""
    out: List[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(raw):
            raise PB3Error("E_CORRUPT", "escape dở dang ở cuối chuỗi", where=path)
        nxt = raw[i + 1]
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def _escape(value: str) -> str:
    out: List[str] = []
    for c in value:
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        else:
            out.append(c)
    return "".join(out)


def _wrap_po_string(value: str) -> str:
    """Trả chuỗi PO đã escape, dạng "..." hoặc "" + các dòng nối khi dài (~78 cột)."""
    esc = _escape(value)
    if len(esc) <= _WRAP_COL:
        return '"%s"' % esc
    chunks: List[str] = []
    i = 0
    while i < len(esc):
        j = min(i + _WRAP_COL, len(esc))
        if j < len(esc):
            # Không cắt giữa cặp escape: số dấu \\ ở cuối đoạn phải chẵn.
            k = 0
            while j - 1 - k >= i and esc[j - 1 - k] == "\\":
                k += 1
            if k % 2 == 1:
                j -= 1
        chunks.append(esc[i:j])
        i = j
    return '""\n' + "\n".join('"%s"' % c for c in chunks)


def _scan_quoted(rest: str, base_off: int, path: str) -> Tuple[str, int]:
    """Đọc một hoặc nhiều chuỗi "..." nối nhau trong `rest`; trả (giá trị, offset
    ngay sau dấu " cuối cùng, tính theo text gốc). Ký tự lạ sau chuỗi -> E_CORRUPT."""
    value: List[str] = []
    i = 0
    end = 0
    while True:
        while i < len(rest) and rest[i] in " \t":
            i += 1
        if i >= len(rest):
            break
        if rest[i] != '"':
            raise PB3Error("E_CORRUPT", "chuỗi PO phải bắt đầu bằng dấu ngoặc kép", where=path)
        j = i + 1
        closed = False
        while j < len(rest):
            if rest[j] == "\\":
                j += 2
                continue
            if rest[j] == '"':
                closed = True
                break
            j += 1
        if not closed:
            raise PB3Error("E_CORRUPT", "chuỗi PO không có dấu ngoặc kép đóng", where=path)
        value.append(_unescape(rest[i + 1:j], path))
        end = base_off + j + 1
        i = j + 1
    if not value:
        raise PB3Error("E_CORRUPT", "chỉ thị PO thiếu chuỗi giá trị", where=path)
    return "".join(value), end


def _trunc_note(text: str) -> str:
    text = text.strip()
    return text[:_NOTE_LEN] + ("…" if len(text) > _NOTE_LEN else "")


class _PoEntry:
    """Entry PO đang lắp ráp (một khối, kết thúc ở dòng trống)."""

    def __init__(self) -> None:
        self.msgctxt: Optional[str] = None
        self.msgid: Optional[str] = None
        self.msgid_plural: Optional[str] = None
        self.msgstr: Dict[int, str] = {}
        self.fuzzy = False
        self.notes: List[str] = []
        self.obsolete = False
        self.spans: Dict[str, Dict[str, Any]] = {}
        self.last_line_end = 0       # cuối dòng chỉ thị cuối cùng (điểm chèn msgstr thiếu)
        self.cur: Optional[str] = None  # chỉ thị đang nối chuỗi: msgctxt/msgid/msgid_plural/msgstr[n]

    def is_empty(self) -> bool:
        return (self.msgctxt is None and self.msgid is None and self.msgid_plural is None
                and not self.msgstr and not self.notes and not self.fuzzy and not self.obsolete)


def _iter_po_lines(text: str):
    """Yield (dòng không gồm "\\n", offset bắt đầu của dòng) theo thứ tự.

    caps-po: thay cho `lines = text.split("\\n")` + list `offsets` (file .po
    lớn như vietnamese.po ~18 MB / ~350k dòng thì 2 list đó tốn thêm ~45 MB
    RAM đỉnh). Generator chỉ giữ 1 dòng tại 1 thời điểm; offset tính đúng
    như cách cũ (mỗi dòng + 1 cho "\\n") để span byte của write-back không
    đổi. Hành vi cuối file giống hệt split: text kết thúc bằng "\\n" thì
    không sinh thêm dòng trống cuối (flush cuối vòng lặp đã xử lý entry dở).
    """
    pos = 0
    n = len(text)
    while pos < n:
        nl = text.find("\n", pos)
        if nl < 0:
            yield text[pos:], pos
            return
        yield text[pos:nl], pos
        pos = nl + 1


def _parse_po(data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
    doc = decode_text(data, path)
    text = doc.text

    pairs: List[Tuple[str, str, str, List[str]]] = []
    spans: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"obsolete": 0, "fuzzy": 0, "plural": 0, "header": 0}

    def flush(cur: _PoEntry) -> None:
        if cur.is_empty():
            return
        if cur.obsolete:
            stats["obsolete"] += 1
            return
        if cur.msgid is None:
            raise PB3Error("E_CORRUPT", "entry PO thiếu msgid", where=path)
        if cur.msgid == "":
            stats["header"] += 1
            return
        base_key = ((cur.msgctxt + "\x04" + cur.msgid) if cur.msgctxt is not None
                    else cur.msgid)
        notes = list(cur.notes)
        if cur.fuzzy:
            notes = ["fuzzy"] + notes
            stats["fuzzy"] += 1
            warnings.append({"code": "E_PARSE_PARTIAL", "key": base_key,
                             "msg": "entry fuzzy, bản dịch có thể chưa chắc chắn"})
        if 0 in cur.msgstr:
            pairs.append((base_key, cur.msgstr[0], "ok", list(notes)))
        else:
            pairs.append((base_key, "", "ok", list(notes)))
            spans.append({"key": base_key, "start": cur.last_line_end, "end": cur.last_line_end,
                          "prefix": "\nmsgstr "})
        if cur.msgid_plural is not None:
            stats["plural"] += 1
            pairs.append((base_key + " [plural]", cur.msgid_plural, "ok",
                          list(notes) + ["msgid_plural"]))
            for n in sorted(cur.msgstr):
                if n == 0:
                    continue
                pairs.append((base_key + "[%d]" % n, cur.msgstr[n], "ok", list(notes)))
        else:
            for n in sorted(cur.msgstr):
                if n == 0:
                    continue
                pairs.append((base_key + "[%d]" % n, cur.msgstr[n], "ok", list(notes)))
        # Span của các dạng số nhiều thiếu: chèn sau dòng cuối của entry.
        if cur.msgid_plural is not None:
            have = set(cur.msgstr)
            if have:
                top = max(have)
                for n in range(1, top + 1):
                    if n not in have:
                        spans.append({"key": base_key + "[%d]" % n, "start": cur.last_line_end,
                                      "end": cur.last_line_end, "prefix": "\nmsgstr[%d] " % n})
        # Đổi span nội bộ (msgstr[n], msgid_plural) sang khóa entry ngay tại đây,
        # vì chỉ lúc flush mới biết msgid của entry này.
        for internal, span in cur.spans.items():
            if internal == "msgid_plural":
                entry_key = base_key + " [plural]"
            elif internal.startswith("msgstr["):
                n = int(internal[7:-1])
                entry_key = base_key if n == 0 else base_key + "[%d]" % n
            else:
                continue
            spans.append({"key": entry_key, "start": span["start"], "end": span["end"],
                          "prefix": None})

    cur = _PoEntry()
    for raw, start in _iter_po_lines(text):
        line = raw[:-1] if raw.endswith("\r") else raw
        if line == "":
            flush(cur)
            cur = _PoEntry()
            continue
        if line.startswith("#~"):
            cur.obsolete = True
            cur.last_line_end = start + len(line)
            continue
        if line.startswith("#"):
            if line.startswith("#,"):
                flags = [f.strip() for f in line[2:].split(",")]
                if "fuzzy" in flags:
                    cur.fuzzy = True
            elif line.startswith("#."):
                cur.notes.append("ghi-chú-dịch: " + _trunc_note(line[2:]))
            elif line.startswith("#:"):
                cur.notes.append("tham-chiếu: " + _trunc_note(line[2:]))
            elif line.startswith("#|"):
                pass  # msgid cũ, không đưa vào bảng
            else:
                body = line[1:].strip()
                if body:
                    cur.notes.append(_trunc_note(body))
            cur.last_line_end = start + len(line)
            continue
        if line.lstrip().startswith('"'):
            if cur.cur is None:
                raise PB3Error("E_CORRUPT", "chuỗi nối không thuộc chỉ thị nào", where=path)
            stripped = line.lstrip()
            val, end = _scan_quoted(stripped, start + (len(line) - len(stripped)), path)
            target = cur.cur
            if target == "msgctxt":
                cur.msgctxt = (cur.msgctxt or "") + val
            elif target == "msgid":
                cur.msgid = (cur.msgid or "") + val
            elif target == "msgid_plural":
                cur.msgid_plural = (cur.msgid_plural or "") + val
            else:
                n = int(target.split("[", 1)[1][:-1])
                cur.msgstr[n] = cur.msgstr.get(n, "") + val
            cur.spans[target]["end"] = end
            cur.last_line_end = start + len(line)
            continue
        m = _DIRECTIVE_RE.match(line)
        if not m:
            raise PB3Error("E_CORRUPT", "dòng không nhận diện được trong file PO", where=path)
        raw_name, idx, rest = m.group(1), m.group(2), m.group(3)
        # Nhóm 1 gồm cả "[n]" (vd "msgstr[0]") nên chuẩn hóa về tên chỉ thị.
        name = "msgstr" if raw_name.startswith("msgstr") else raw_name
        if name in ("msgctxt", "msgid") and cur.msgid is not None:
            flush(cur)
            cur = _PoEntry()
        if name == "msgstr":
            n = int(idx) if idx is not None else 0
            key = "msgstr[%d]" % n
            if n in cur.msgstr:
                raise PB3Error("E_CORRUPT", "msgstr[%d] lặp lại trong cùng entry" % n, where=path)
        else:
            key = name
            if name == "msgctxt" and cur.msgctxt is not None:
                raise PB3Error("E_CORRUPT", "msgctxt lặp lại trong cùng entry", where=path)
            if name == "msgid" and cur.msgid is not None:
                raise PB3Error("E_CORRUPT", "msgid lặp lại trong cùng entry", where=path)
            if name == "msgid_plural" and cur.msgid_plural is not None:
                raise PB3Error("E_CORRUPT", "msgid_plural lặp lại trong cùng entry", where=path)
        val, end = _scan_quoted(rest, start + (len(line) - len(rest)), path)
        if name == "msgctxt":
            cur.msgctxt = val
        elif name == "msgid":
            cur.msgid = val
        elif name == "msgid_plural":
            cur.msgid_plural = val
        else:
            cur.msgstr[n] = val
        cur.spans[key] = {"key": key, "start": start + (len(line) - len(rest)) + rest.index('"'),
                          "end": end, "prefix": None}
        cur.cur = key
        cur.last_line_end = start + len(line)
    flush(cur)
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    if stats["obsolete"]:
        warnings.append({"code": "E_PARSE_PARTIAL",
                         "msg": "bỏ qua %d entry obsolete" % stats["obsolete"]})
    spans.sort(key=lambda s: (s["start"], s["end"]))
    return {"entries": entries, "schema_hint": {"format": "po"}, "stats": stats,
            "conflicts": conflicts, "warnings": warnings, "spans": spans,
            "text": text, "doc": doc}


def _parse_mo(data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
    if len(data) < 28:
        raise PB3Error("E_CORRUPT", "file MO quá ngắn (thiếu header 28 byte)", where=path)
    if data[:4] == _MO_MAGIC_LE:
        endian = "<"
    elif data[:4] == _MO_MAGIC_BE:
        endian = ">"
    else:
        raise PB3Error("E_CORRUPT", "file MO sai magic", where=path)
    _magic, revision, count, orig_off, trans_off, hash_size, _hash_off = struct.unpack(
        endian + "7I", data[:28])
    if revision != 0:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "phiên bản MO %d chưa hỗ trợ" % revision,
                       where=path)
    if count > 1000000:
        raise PB3Error("E_CORRUPT", "số entry MO quá lớn: %d" % count, where=path)
    if orig_off + 8 * count > len(data) or trans_off + 8 * count > len(data):
        raise PB3Error("E_CORRUPT", "bảng chuỗi MO vượt quá kích thước file", where=path)

    def read_table(off: int) -> List[str]:
        out: List[str] = []
        for i in range(count):
            length, pos = struct.unpack_from(endian + "2I", data, off + 8 * i)
            if pos + length > len(data):
                raise PB3Error("E_CORRUPT", "chuỗi MO vượt quá kích thước file", where=path)
            try:
                out.append(data[pos:pos + length].decode("utf-8"))
            except UnicodeDecodeError:
                raise PB3Error("E_CORRUPT", "chuỗi MO không phải UTF-8", where=path)
        return out

    orig = read_table(orig_off)
    trans = read_table(trans_off)
    pairs: List[Tuple[str, str, str, List[str]]] = []
    stats: Dict[str, Any] = {"plural": 0, "header": 0}
    for raw_id, raw_str in zip(orig, trans):
        if raw_id == "":
            stats["header"] += 1
            continue
        parts = raw_id.split("\x00")
        singular = parts[0]
        plural = parts[1] if len(parts) > 1 else None
        if "\x04" in singular:
            ctxt, singular = singular.split("\x04", 1)
            base_key = ctxt + "\x04" + singular
        else:
            base_key = singular
        forms = raw_str.split("\x00") if raw_str else [""]
        pairs.append((base_key, forms[0], "ok", []))
        if plural is not None:
            stats["plural"] += 1
            pairs.append((base_key + " [plural]", plural, "ok", ["msgid_plural"]))
            for n in range(1, len(forms)):
                pairs.append((base_key + "[%d]" % n, forms[n], "ok", []))
    entries, conflicts = make_entries(pairs, path, len(data), "utf-8", locale)
    return {"entries": entries,
            "schema_hint": {"format": "mo", "revision": revision, "hash_size": hash_size},
            "stats": stats, "conflicts": conflicts, "warnings": [],
            "spans": [], "text": "", "doc": None}


class PoMoPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".po", ".mo")
    FLAVOR = "po"
    SELFTEST_BASE = (b'msgid "hello"\nmsgstr "Xin chao"\n\n'
                     b'msgid "bye"\nmsgstr "Tam biet"\n')

    def _sniff(self, head: bytes, path: str) -> float:
        if head[:4] in (_MO_MAGIC_LE, _MO_MAGIC_BE):
            return 0.9
        if b'msgid "' in head:
            return 0.8
        return 0.0

    def _is_writable(self, path: str, head: bytes) -> bool:
        if head[:4] in (_MO_MAGIC_LE, _MO_MAGIC_BE):
            return False
        return not path.lower().endswith(".mo")

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        if data[:4] in (_MO_MAGIC_LE, _MO_MAGIC_BE):
            return _parse_mo(data, path, locale)
        res = _parse_po(data, path, locale)
        return {"entries": res["entries"], "schema_hint": res["schema_hint"],
                "stats": res["stats"], "conflicts": res["conflicts"],
                "warnings": res["warnings"]}

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        if (table.schema_hint or {}).get("format") == "mo" \
                or base_bytes[:4] in (_MO_MAGIC_LE, _MO_MAGIC_BE):
            raise PB3Error("E_WRITE_DISABLED", "MO nhị phân chỉ đọc", plugin_id=self.id)
        res = _parse_po(base_bytes, "base", table.locale)
        queue = value_queue(table)
        text = res["text"]
        repls: List[Tuple[int, int, str, int]] = []
        for seq, span in enumerate(res["spans"]):
            vals = queue.get(span["key"])
            if not vals:
                continue
            new_value = vals.pop(0)
            quoted = _wrap_po_string(new_value)
            if span["prefix"] is not None:
                repls.append((span["start"], span["end"], span["prefix"] + quoted, seq))
            else:
                repls.append((span["start"], span["end"], quoted, seq))
        # Thay từ cuối file lên đầu để offset không lệch; chèn cùng vị trí thì
        # áp dụng theo thứ tự ngược để thứ tự cuối cùng vẫn đúng.
        repls.sort(key=lambda r: (r[0], -r[3]), reverse=True)
        for start, end, rep, _seq in repls:
            text = text[:start] + rep + text[end:]
        return res["doc"].encode(text)
