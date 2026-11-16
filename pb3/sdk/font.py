"""Công cụ font TrueType tối thiểu (chỉ stdlib, Python 3.8+).

Phạm vi đợt tổng cuối — nhóm font, chế độ (1)–(4):
- Đọc bảng cmap (format 4/12), head/hhea/maxp/hmtx/name, glyf/loca để
  kiểm tra độ phủ ký tự tiếng Việt của một font TTF.
- Gộp glyph từ font nguồn (donor) vào font đích cho chế độ (1)/(2):
  copy outline glyf + loca + hmtx + entry cmap, dựng lại các bảng,
  tính lại checksum — xử lý cả glyph đơn và glyph composite.
- Liệt kê Font object nhúng trong SerializedFile (class 128) để quét
  font trong bundle Unity (chỉ liệt kê + cảnh báo, không sửa object).

GIỚI HẠN TRUNG THỰC (xem handoff/final_font.md):
- Chỉ gộp được font TrueType có outline glyf (sfnt 0x00010000/"true").
  Font CFF/OpenType (.otf, "OTTO") đọc được cmap nhưng KHÔNG gộp được;
  font collection (.ttc) chưa hỗ trợ.
- Atlas SDF của TextMeshPro KHÔNG tái tạo được trong đợt này: gặp font
  SDF thì báo rõ, hướng dùng chế độ (3)/(4) thay thế.
- Gộp glyph chỉ copy outline + advance/lsb; không cập nhật OS/2
  (unicodeRanges), `post` (tên glyph), kerning (`kern`/`GPOS`) hay
  hinting cho glyph mới; chữ ký số DSIG bị bỏ (font gộp không còn
  chữ ký gốc). Bảng cmap dựng lại chỉ giữ 1 subtable (format 4 hoặc
  format 12 — chọn bản nhỏ hơn khi toàn bộ ký tự trong BMP, format 12
  khi có ký tự ngoài BMP).
- Font nhúng trong SerializedFile (Font object, class 128): đợt này
  chỉ liệt kê, KHÔNG sửa được bytes object (chưa có parser/writer
  cho Font object); thay thế chỉ làm được với file .ttf rời hoặc
  entry .ttf trong container ZIP/IPA.
"""
from __future__ import annotations

import math
import os
import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from core.errors import PB3Error

# ------------------------------------------------------------------ hằng số
#: Tên file font nhập sẵn (chế độ 4) trong thư mục `fonts/` ở gốc dự án.
BUNDLED_FONT_NAME = "BeVietnamPro-Regular.ttf"
#: Giấy phép của font nhập sẵn (SIL Open Font License 1.1).
BUNDLED_FONT_LICENSE = "OFL.txt"

#: Class ID của Font object trong SerializedFile Unity.
UNITY_FONT_CLASS_ID = 128

#: Chữ cái tiếng Việt cơ bản (thường + hoa): ă â đ ê ô ơ ư.
_VI_BASE = (0x103, 0xE2, 0x111, 0xEA, 0xF4, 0x1A1, 0x1B0,
            0x102, 0xC2, 0x110, 0xCA, 0xD4, 0x1A0, 0x1AF)
#: 12 nguyên âm gốc làm nền cho chữ có dấu: a ă â e ê i o ô ơ u ư y.
_VI_TONE_BASES = ("a", "ă", "â", "e", "ê", "i", "o", "ô", "ơ", "u", "ư", "y")
#: 5 dấu thanh tiếng Việt (dạng kết hợp): huyền/sắc/hỏi/ngã/nặng.
_VI_TONES = ("\u0300", "\u0301", "\u0309", "\u0303", "\u0323")
#: Dấu kết hợp tiếng Việt: huyền/sắc/mũ/ngã/breve/móc/dấu sừng/chấm dưới.
_VI_MARKS = (0x300, 0x301, 0x302, 0x303, 0x306, 0x309, 0x31B, 0x323)


def _accented_vietnamese() -> frozenset:
    """120 chữ có dấu tiếng Việt: 12 gốc × 5 dấu thanh × 2 case.

    Mỗi gốc + dấu được chuẩn hóa NFC thành 1 ký tự ghép sẵn (vd a + U+0300
    -> à); hoa của ký tự đó cũng được thêm. Mọi tổ hợp đều có dạng ghép
    sẵn trong Unicode nên len(ch) == 1 luôn đúng — tổ hợp nào không ghép
    được (không xảy ra với tiếng Việt) thì bị bỏ qua, không làm vỡ import.
    """
    out = set()
    for base in _VI_TONE_BASES:
        for tone in _VI_TONES:
            ch = unicodedata.normalize("NFC", base + tone)
            if len(ch) != 1:
                continue
            up = ch.upper()
            if len(up) != 1:
                continue
            out.add(ord(ch))
            out.add(ord(up))
    return frozenset(out)


#: 120 chữ có dấu tiếng Việt (12 gốc × 5 dấu × 2 case).
_VI_ACCENTED = _accented_vietnamese()

#: Tập ký tự tiếng Việt bắt buộc phải có để hiển thị đúng — 142 mục =
#: 134 chữ cái (120 chữ có dấu + 14 gốc đặc biệt) + 8 dấu kết hợp.
#: (Trước patch02 chỉ có 112: dải U+1EA0–U+1EF9 bỏ sót 30 chữ Latin-1
#: à á ã è é ì í ò ó õ ù ú ý ĩ ũ — xem decisions.md #206.)
REQUIRED_VIETNAMESE = frozenset(_VI_BASE) | _VI_ACCENTED | frozenset(_VI_MARKS)

#: Hằng số checksum toàn font (đặc tả TrueType).
_CHECKSUM_MAGIC = 0xB1B0AFBA
#: Magic của bảng head.
_HEAD_MAGIC = 0x5F0F3CF5

#: Thông báo giới hạn SDF (dùng chung cho sdk + services để wording nhất quán).
SDF_LIMIT_MSG = ("không hỗ trợ atlas SDF của TextMeshPro trong đợt này "
                 "(không tái tạo được atlas SDF trên điện thoại); "
                 "dùng chế độ (3) thay bằng font TTF có tiếng Việt "
                 "hoặc chế độ (4) dùng font nhập sẵn của PB3")

#: Cờ component trong glyph composite (đặc tả TrueType).
_COMP_ARG_WORDS = 0x0001
_COMP_ARGS_XY = 0x0002
_COMP_HAVE_SCALE = 0x0008
_COMP_MORE = 0x0020
_COMP_HAVE_XY_SCALE = 0x0040
_COMP_HAVE_2X2 = 0x0080
_COMP_HAVE_INSTR = 0x0100


def required_vietnamese_chars(extra: Optional[Iterable[int]] = None) -> List[int]:
    """Danh sách ký tự tiếng Việt cần kiểm (đã sắp xếp) + ký tự thêm (nếu có)."""
    req = set(REQUIRED_VIETNAMESE)
    if extra:
        req.update(int(c) for c in extra)
    return sorted(req)


def format_codepoint(cp: int) -> str:
    """Hiển thị một codepoint: 'U+0103 (ă)' (ký tự không in được thì chỉ mã)."""
    ch = chr(cp)
    if ch.isprintable() and cp not in (0x2028, 0x2029):
        return "U+%04X (%s)" % (cp, ch)
    return "U+%04X" % cp


def is_sdf_name(name: str) -> bool:
    """Heuristic: tên gợi ý atlas SDF (TextMeshPro) — chứa 'sdf'/'atlas'.

    Chỉ xét TÊN FILE (basename), không xét toàn bộ đường dẫn: thư mục tạm
    của bộ test (vd `.tmp_suite`) chứa 'tmp_' sẽ làm mọi font bị nghi oan
    (tích hợp đợt tổng cuối: test_scan_dir fail 3 != 1 chỉ khi TMPDIR có
    'tmp_' trong đường dẫn)."""
    low = os.path.basename(name or "").lower()
    return "sdf" in low or "textmeshpro" in low or "tmp_" in low


# ------------------------------------------------------------------ lỗi nhỏ
def _err(code: str, msg: str, where: str = "font") -> PB3Error:
    return PB3Error(code, msg, where=where)


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _i16(data: bytes, off: int) -> int:
    return struct.unpack_from(">h", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _table_checksum(data: bytes, zero_adjustment: bool = False) -> int:
    """Checksum một bảng (đệm NUL tới bội 4, cộng từng u32 big-endian).

    Đặc tả TrueType: checksum của bảng `head` tính với checkSumAdjustment
    (byte 8..12) coi như 0 — kể cả khi kiểm tra font thật lẫn khi dựng font.
    """
    if zero_adjustment and len(data) >= 12:
        data = data[:8] + b"\x00\x00\x00\x00" + data[12:]
    pad = (-len(data)) % 4
    buf = data + b"\x00" * pad
    total = 0
    for i in range(0, len(buf), 4):
        total = (total + struct.unpack_from(">I", buf, i)[0]) & 0xFFFFFFFF
    return total


# ------------------------------------------------------------------ parse
def detect_font_kind(data: bytes) -> str:
    """'ttf' | 'otf' | 'ttc' | 'unknown' theo 4 byte đầu (sfnt version)."""
    if len(data) < 4:
        return "unknown"
    magic = bytes(data[:4])
    if magic in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if magic == b"OTTO":
        return "otf"
    if magic == b"ttcf":
        return "ttc"
    return "unknown"


@dataclass
class FontInfo:
    """Kết quả đọc một font (chỉ đọc, không giữ bytes gốc)."""
    kind: str
    family: str = ""
    full_name: str = ""
    num_glyphs: int = 0
    units_per_em: int = 0
    cmap: Dict[int, int] = field(default_factory=dict)
    tables: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    has_glyf: bool = False
    index_to_loc_format: int = 0
    number_of_hmetrics: int = 0
    warnings: List[str] = field(default_factory=list)
    where: str = "font"


def _parse_cmap_format4(data: bytes, off: int, cmap: Dict[int, int]) -> None:
    """Đọc subtable cmap format 4 vào `cmap` (ghi đè khi trùng)."""
    length = _u16(data, off + 2)
    if off + length > len(data) or length < 16:
        raise _err("E_CORRUPT", "subtable cmap format 4 bị cắt cụt")
    seg_count = _u16(data, off + 6) // 2
    end_off = off + 14
    start_off = end_off + 2 * seg_count + 2
    delta_off = start_off + 2 * seg_count
    range_off = delta_off + 2 * seg_count
    glyph_off = range_off + 2 * seg_count
    for i in range(seg_count):
        end = _u16(data, end_off + 2 * i)
        start = _u16(data, start_off + 2 * i)
        delta = _i16(data, delta_off + 2 * i)
        ro = _u16(data, range_off + 2 * i)
        if start == 0xFFFF and end == 0xFFFF:
            continue
        if start > end:
            raise _err("E_CORRUPT", "đoạn cmap format 4 ngược (%04X > %04X)" % (start, end))
        for cp in range(start, end + 1):
            if ro == 0:
                gid = (cp + delta) & 0xFFFF
            else:
                pos = range_off + 2 * i + ro + 2 * (cp - start)
                if pos + 2 > off + length:
                    raise _err("E_CORRUPT", "glyphIdArray của cmap format 4 vượt bảng")
                gid = _u16(data, pos)
                if gid != 0:
                    gid = (gid + delta) & 0xFFFF
            if gid != 0:
                cmap[cp] = gid


def _parse_cmap_format12(data: bytes, off: int, cmap: Dict[int, int]) -> None:
    """Đọc subtable cmap format 12 vào `cmap` (ghi đè khi trùng)."""
    length = _u32(data, off + 4)
    if off + length > len(data) or length < 16:
        raise _err("E_CORRUPT", "subtable cmap format 12 bị cắt cụt")
    n_groups = _u32(data, off + 12)
    pos = off + 16
    for _ in range(n_groups):
        if pos + 12 > off + length:
            raise _err("E_CORRUPT", "nhóm cmap format 12 bị cắt cụt")
        start, end, gid0 = struct.unpack_from(">III", data, pos)
        pos += 12
        if start > end:
            raise _err("E_CORRUPT", "nhóm cmap format 12 ngược (%04X > %04X)" % (start, end))
        for cp in range(start, end + 1):
            gid = gid0 + (cp - start)
            if gid != 0:
                cmap[cp] = gid


def _parse_name_table(data: bytes, off: int, length: int) -> Tuple[str, str]:
    """Đọc tên family (ID 1) + tên đầy đủ (ID 4) từ bảng name."""
    if length < 6 or off + length > len(data):
        return "", ""
    count = _u16(data, off + 2)
    str_off = _u16(data, off + 4)
    best: Dict[int, str] = {}
    for i in range(count):
        rec = off + 6 + 12 * i
        if rec + 12 > off + length:
            break
        platform, encoding, lang, name_id, ln, ro = struct.unpack_from(">HHHHHH", data, rec)
        if name_id not in (1, 4, 6) or ln == 0:
            continue
        start = off + str_off + ro
        raw = data[start:start + ln]
        if platform in (0, 3):
            try:
                text = raw.decode("utf-16-be")
            except UnicodeDecodeError:
                continue
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
        key = (0 if (platform, encoding, lang) == (3, 1, 0x409) else
               1 if platform == 3 else 2)
        if name_id not in best or key < best.get(name_id + 100, 9):
            best[name_id] = text
            best[name_id + 100] = key
    return best.get(1, ""), best.get(4, "") or best.get(1, "")


def parse_font(data: bytes, where: str = "font") -> FontInfo:
    """Đọc font TTF/OTF: cmap + metadata. TTC -> E_UNSUPPORTED_FORMAT."""
    data = bytes(data)
    kind = detect_font_kind(data)
    if kind == "unknown":
        raise _err("E_CORRUPT", "không phải font TrueType/OpenType (magic lạ)", where=where)
    if kind == "ttc":
        raise _err("E_UNSUPPORTED_FORMAT",
                   "font collection (.ttc) chưa hỗ trợ — tách từng font con ra file .ttf riêng",
                   where=where)
    if len(data) < 12:
        raise _err("E_CORRUPT", "font quá ngắn (thiếu bảng offset)", where=where)
    num_tables = _u16(data, 4)
    tables: Dict[str, Tuple[int, int]] = {}
    for i in range(num_tables):
        rec = 12 + 16 * i
        if rec + 16 > len(data):
            raise _err("E_CORRUPT", "bảng thư mục font bị cắt cụt", where=where)
        tag = data[rec:rec + 4].decode("latin-1")
        _cs, off, ln = struct.unpack_from(">III", data, rec + 4)
        if off + ln > len(data):
            raise _err("E_CORRUPT", "bảng %r vượt quá file" % tag, where=where)
        tables[tag] = (off, ln)
    info = FontInfo(kind=kind, tables=tables, where=where)
    if "cmap" not in tables:
        raise _err("E_CORRUPT", "font thiếu bảng cmap", where=where)
    cmap: Dict[int, int] = {}
    coff, clen = tables["cmap"]
    if clen < 4:
        raise _err("E_CORRUPT", "bảng cmap quá ngắn", where=where)
    n_sub = _u16(data, coff + 2)
    subs = []
    for i in range(n_sub):
        rec = coff + 4 + 8 * i
        if rec + 8 > coff + clen:
            break
        platform, encoding, sub_off = struct.unpack_from(">HHI", data, rec)
        subs.append((platform, encoding, sub_off))
    # Format 4 trước, format 12 sau (12 ghi đè khi trùng — đầy đủ hơn).
    for fmt_want in (4, 12):
        for _platform, _enc, sub_off in subs:
            pos = coff + sub_off
            if pos + 2 > len(data):
                continue
            fmt = _u16(data, pos)
            if fmt != fmt_want:
                continue
            try:
                if fmt == 4:
                    _parse_cmap_format4(data, pos, cmap)
                else:
                    _parse_cmap_format12(data, pos, cmap)
            except PB3Error:
                raise
    if not cmap:
        info.warnings.append("không đọc được subtable cmap format 4/12 nào")
    info.cmap = cmap
    if "head" in tables:
        hoff, _hlen = tables["head"]
        if _hlen >= 54:
            info.units_per_em = _u16(data, hoff + 18)
            info.index_to_loc_format = _i16(data, hoff + 50)
    if "maxp" in tables:
        moff, _mlen = tables["maxp"]
        if _mlen >= 6:
            info.num_glyphs = _u16(data, moff + 4)  # version u32 + numGlyphs u16
    if "hhea" in tables:
        hhoff, _hlen = tables["hhea"]
        if _hlen >= 36:
            info.number_of_hmetrics = _u16(data, hhoff + 34)
    info.has_glyf = "glyf" in tables and "loca" in tables
    if "name" in tables:
        noff, nlen = tables["name"]
        info.family, info.full_name = _parse_name_table(data, noff, nlen)
    if not info.family:
        info.family = where.rsplit("/", 1)[-1]
    return info


# ------------------------------------------------------------------ glyf/hmtx
def _hmtx_entries(data: bytes, info: FontInfo) -> List[Tuple[int, int]]:
    """(advance, lsb) cho MỌI glyph (mở rộng advance cuối khi numberOfHMetrics < numGlyphs)."""
    if "hmtx" not in info.tables:
        raise _err("E_CORRUPT", "font thiếu bảng hmtx", where=info.where)
    off, _ln = info.tables["hmtx"]
    n = info.number_of_hmetrics
    out: List[Tuple[int, int]] = []
    for i in range(min(n, info.num_glyphs)):
        out.append(struct.unpack_from(">Hh", data, off + 4 * i))
    last_adv = out[-1][0] if out else 0
    lsb_base = off + 4 * n
    for gid in range(len(out), info.num_glyphs):
        lsb = _i16(data, lsb_base + 2 * (gid - n)) if n else 0
        out.append((last_adv, lsb))
    return out


def _loca_offsets(data: bytes, info: FontInfo) -> List[int]:
    """Danh sách numGlyphs+1 offset vào glyf (theo indexToLocFormat)."""
    if "loca" not in info.tables:
        raise _err("E_CORRUPT", "font thiếu bảng loca", where=info.where)
    off, _ln = info.tables["loca"]
    if info.index_to_loc_format == 0:
        return [_u16(data, off + 2 * i) * 2 for i in range(info.num_glyphs + 1)]
    return [_u32(data, off + 4 * i) for i in range(info.num_glyphs + 1)]


def glyph_data(data: bytes, info: FontInfo, gid: int) -> bytes:
    """Bytes outline thô của một glyph (b"" khi glyph rỗng)."""
    if gid < 0 or gid >= info.num_glyphs:
        raise _err("E_CORRUPT", "glyph id %d ngoài bảng (%d glyph)" % (gid, info.num_glyphs),
                   where=info.where)
    if "glyf" not in info.tables:
        raise _err("E_CORRUPT", "font thiếu bảng glyf", where=info.where)
    goff, glen = info.tables["glyf"]
    locs = _loca_offsets(data, info)
    start, end = locs[gid], locs[gid + 1]
    if start > end or end > glen:
        raise _err("E_CORRUPT", "loca của glyph %d vượt bảng glyf" % gid, where=info.where)
    return bytes(data[goff + start:goff + end])


def composite_components(raw: bytes) -> List[int]:
    """Glyph id các component của một glyph composite (rỗng khi glyph đơn/rỗng)."""
    if len(raw) < 10:
        return []
    n_contours = _i16(raw, 0)
    if n_contours >= 0:
        return []
    out: List[int] = []
    pos = 10
    while True:
        if pos + 4 > len(raw):
            raise _err("E_CORRUPT", "glyph composite bị cắt cụt ở component")
        flags, gid = struct.unpack_from(">HH", raw, pos)
        pos += 4
        out.append(gid)
        pos += 4 if flags & _COMP_ARG_WORDS else 2
        if flags & _COMP_HAVE_SCALE:
            pos += 2
        elif flags & _COMP_HAVE_XY_SCALE:
            pos += 4
        elif flags & _COMP_HAVE_2X2:
            pos += 8
        if pos > len(raw):
            raise _err("E_CORRUPT", "glyph composite bị cắt cụt ở tham số scale")
        if not (flags & _COMP_MORE):
            break
    return out


def _rewrite_composite(raw: bytes, remap: Dict[int, int]) -> bytes:
    """Copy glyph composite, đổi glyph id component theo `remap` (giữ nguyên còn lại)."""
    out = bytearray(raw[:10])
    pos = 10
    while True:
        flags, gid = struct.unpack_from(">HH", raw, pos)
        if gid not in remap:
            raise _err("E_CORRUPT", "component glyph %d chưa có trong bảng gộp" % gid)
        out += struct.pack(">HH", flags, remap[gid])
        pos += 4
        arg_len = 4 if flags & _COMP_ARG_WORDS else 2
        out += raw[pos:pos + arg_len]
        pos += arg_len
        if flags & _COMP_HAVE_SCALE:
            out += raw[pos:pos + 2]
            pos += 2
        elif flags & _COMP_HAVE_XY_SCALE:
            out += raw[pos:pos + 4]
            pos += 4
        elif flags & _COMP_HAVE_2X2:
            out += raw[pos:pos + 8]
            pos += 8
        if not (flags & _COMP_MORE):
            break
    # Phần còn lại (số lượng + bytes instructions, nếu có) giữ nguyên.
    out += raw[pos:]
    return bytes(out)


# ------------------------------------------------------------------ độ phủ
@dataclass
class CoverageReport:
    """Độ phủ ký tự tiếng Việt của một font."""
    family: str
    kind: str
    num_glyphs: int
    total: int
    covered: int
    missing: List[int] = field(default_factory=list)
    where: str = "font"

    @property
    def pct(self) -> int:
        return int(self.covered * 100 / self.total) if self.total else 100

    @property
    def is_full(self) -> bool:
        return not self.missing

    def summary_vi(self) -> str:
        if self.is_full:
            return ("%s: đủ tiếng Việt (%d/%d ký tự, %d glyph)"
                    % (self.family, self.covered, self.total, self.num_glyphs))
        shown = ", ".join(format_codepoint(c) for c in self.missing[:8])
        more = "…" if len(self.missing) > 8 else ""
        return ("%s: thiếu %d/%d ký tự tiếng Việt: %s%s"
                % (self.family, len(self.missing), self.total, shown, more))


def missing_chars(font, required: Optional[Iterable[int]] = None) -> List[int]:
    """Ký tự bắt buộc mà font chưa có (đã sắp xếp). `font`: bytes hoặc FontInfo."""
    info = font if isinstance(font, FontInfo) else parse_font(font)
    req = required_vietnamese_chars(required)
    return [cp for cp in req if cp not in info.cmap]


def coverage(font, required: Optional[Iterable[int]] = None,
             where: str = "font") -> CoverageReport:
    """Báo cáo độ phủ tiếng Việt của một font (bytes hoặc FontInfo)."""
    info = font if isinstance(font, FontInfo) else parse_font(font, where=where)
    req = required_vietnamese_chars(required)
    missing = [cp for cp in req if cp not in info.cmap]
    return CoverageReport(family=info.family, kind=info.kind,
                          num_glyphs=info.num_glyphs, total=len(req),
                          covered=len(req) - len(missing), missing=missing,
                          where=info.where)


def verify_font(data: bytes, where: str = "font") -> List[str]:
    """Kiểm tra tính toàn vẹn font: magic head, checksum từng bảng, checksum tổng.

    Trả danh sách vấn đề (rỗng = đạt). Không raise khi font hỏng cấu trúc
    nặng — parse lỗi thì trả 1 vấn đề mô tả lỗi.
    """
    issues: List[str] = []
    try:
        info = parse_font(data, where=where)
    except PB3Error as exc:
        return ["%s: %s" % (exc.code, exc.message_vi)]
    if "head" in info.tables:
        hoff, _hlen = info.tables["head"]
        if _hlen >= 54 and _u32(data, hoff) != 0x00010000:
            issues.append("head: version lạ")
        if _hlen >= 54 and _u32(data, hoff + 12) != _HEAD_MAGIC:
            issues.append("head: magic sai (không phải 0x5F0F3CF5)")
    total = 0
    for tag in sorted(info.tables):
        off, ln = info.tables[tag]
        stored = _table_checksum(data[off:off + ln], zero_adjustment=(tag == "head"))
        # Checksum ghi trong thư mục bảng (head: tính với adjustment = 0).
        rec = 12 + 16 * sorted(info.tables).index(tag)
        recorded = _u32(data, rec + 4)
        if recorded != stored:
            issues.append("bảng %s: checksum ghi %08X != tính %08X" % (tag, recorded, stored))
    # Checksum toàn font: tổng mọi u32 của TOÀN FILE (cả thư mục bảng,
    # gồm checkSumAdjustment đã lưu) phải bằng 0xB1B0AFBA (đặc tả head:
    # adjustment được tính sao cho tổng này đúng).
    if "head" in info.tables:
        buf = bytes(data) + b"\x00" * ((-len(data)) % 4)
        whole = 0
        for i in range(0, len(buf), 4):
            whole = (whole + struct.unpack_from(">I", buf, i)[0]) & 0xFFFFFFFF
        if whole != _CHECKSUM_MAGIC:
            issues.append("checksum toàn font %08X != %08X" % (whole, _CHECKSUM_MAGIC))
    try:
        locs = _loca_offsets(data, info)
        if any(locs[i] > locs[i + 1] for i in range(len(locs) - 1)):
            issues.append("loca: offset không đơn điệu")
    except (PB3Error, struct.error, IndexError) as exc:
        issues.append("loca: %s" % getattr(exc, "message_vi", str(exc)))
    return issues


# ------------------------------------------------------------------ dựng cmap
def _build_cmap_format4(cmap: Dict[int, int]) -> bytes:
    """Dựng subtable cmap format 4 từ map codepoint -> glyph id (chỉ BMP).

    Đoạn codepoint liên tiếp có delta (gid - cp) không đổi thì dùng idDelta;
    đoạn delta thay đổi thì dùng idRangeOffset + glyphIdArray (cách chuẩn
    để cmap gọn như font thật, thay vì tách mỗi codepoint thành 1 đoạn).
    """
    items = sorted((cp, gid) for cp, gid in cmap.items() if cp <= 0xFFFF and gid != 0
                   and cp != 0xFFFF)
    runs: List[List[Tuple[int, int]]] = []
    for cp, gid in items:
        if runs and runs[-1][-1][0] == cp - 1:
            runs[-1].append((cp, gid))
        else:
            runs.append([(cp, gid)])
    segments: List[Tuple[int, int, int, bool]] = []  # (start, end, delta, range_mode)
    for run in runs:
        deltas = {(gid - cp) & 0xFFFF for cp, gid in run}
        if len(deltas) == 1:
            segments.append((run[0][0], run[-1][0], deltas.pop(), False))
        else:
            segments.append((run[0][0], run[-1][0], 0, True))
    segments.append((0xFFFF, 0xFFFF, 1, False))
    seg_count = len(segments)
    range_glyphs = sum(end - start + 1 for start, end, _d, rm in segments if rm)
    length = 16 + 8 * seg_count + 2 * range_glyphs
    search_range = 2 * (2 ** int(math.floor(math.log2(seg_count))))
    entry_selector = int(math.log2(search_range // 2))
    range_shift = 2 * seg_count - search_range
    out = bytearray()
    out += struct.pack(">HHHHHHH", 4, length, 0, seg_count * 2,
                       search_range, entry_selector, range_shift)
    for start, end, _d, _rm in segments:
        out += struct.pack(">H", end)
    out += struct.pack(">H", 0)
    for start, _e, _d, _rm in segments:
        out += struct.pack(">H", start)
    for _s, _e, delta, _rm in segments:
        out += struct.pack(">h", delta - 0x10000 if delta > 0x7FFF else delta)
    seen = 0  # số glyph đã ghi vào glyphIdArray ở các đoạn range-mode trước
    for i, (_s, _e, _d, rm) in enumerate(segments):
        if not rm:
            out += struct.pack(">H", 0)
        else:
            out += struct.pack(">H", 2 * (seg_count - i + seen))
            seen += _e - _s + 1
    for (start, end, _d, rm), run in zip(segments, runs + [[(0xFFFF, 0)]]):
        if rm:
            for _cp, gid in run:
                out += struct.pack(">H", gid)
    return bytes(out)


def _build_cmap_format12(cmap: Dict[int, int]) -> bytes:
    """Dựng subtable cmap format 12 từ map codepoint -> glyph id."""
    items = sorted((cp, gid) for cp, gid in cmap.items() if gid != 0)
    groups: List[List[int]] = []  # [start, end, start_glyph]
    for cp, gid in items:
        if groups and groups[-1][1] == cp - 1 and groups[-1][2] + (cp - groups[-1][0]) == gid:
            groups[-1][1] = cp
        else:
            groups.append([cp, cp, gid])
    out = bytearray()
    out += struct.pack(">HHIII", 12, 0, 16 + 12 * len(groups), 0, len(groups))
    for start, end, gid0 in groups:
        out += struct.pack(">III", start, end, gid0)
    return bytes(out)


def build_cmap_table(cmap: Dict[int, int]) -> bytes:
    """Dựng bảng cmap mới (1 subtable).

    Có ký tự ngoài BMP -> format 12 (platform 3, enc 10). Toàn BMP:
    chọn subtable NHỎ HƠN giữa format 4 (delta-only, phình to khi glyph id
    rời rạc so với codepoint) và format 12 — cả hai đều hợp lệ, iOS đọc được.
    """
    if any(cp > 0xFFFF for cp in cmap):
        sub = _build_cmap_format12(cmap)
        plat, enc = 3, 10
    else:
        f4 = _build_cmap_format4(cmap)
        f12 = _build_cmap_format12(cmap)
        if len(f4) <= len(f12):
            sub, plat, enc = f4, 3, 1
        else:
            sub, plat, enc = f12, 3, 10
    out = bytearray()
    out += struct.pack(">HH", 0, 1)
    out += struct.pack(">HHI", plat, enc, 12)
    out += sub
    return bytes(out)


# ------------------------------------------------------------------ dựng font
def _assemble_font(sfnt_version: int, tables: Dict[str, bytes]) -> bytes:
    """Dựng file TTF từ dict tag -> bytes (sắp xếp tag, tính checksum, adjustment)."""
    if "head" in tables and len(tables["head"]) >= 12:
        tables = dict(tables)
        head = bytearray(tables["head"])
        head[8:12] = b"\x00\x00\x00\x00"  # adjustment tính lại sau
        tables["head"] = bytes(head)
    tags = sorted(tables)
    num = len(tags)
    search_range = 16 * (2 ** int(math.floor(math.log2(num)))) if num else 0
    entry_selector = int(math.log2(search_range // 16)) if search_range else 0
    range_shift = num * 16 - search_range
    header_len = 12 + 16 * num
    offset = (header_len + 3) // 4 * 4
    records = []
    body = bytearray()
    for tag in tags:
        raw = tables[tag]
        pad = (-len(raw)) % 4
        # Bảng head: checksum tính với checkSumAdjustment = 0 (đặc tả).
        records.append((tag, _table_checksum(raw, zero_adjustment=(tag == "head")),
                        offset, len(raw)))
        body += raw + b"\x00" * pad
        offset += len(raw) + pad
    out = bytearray()
    out += struct.pack(">IHHHH", sfnt_version, num, search_range, entry_selector, range_shift)
    for tag, csum, off, ln in records:
        out += tag.encode("latin-1") + struct.pack(">III", csum, off, ln)
    out += body
    # Tính checkSumAdjustment: tổng mọi u32 của TOÀN FILE (thư mục bảng +
    # dữ liệu bảng, với adjustment = 0) phải bằng 0xB1B0AFBA (đặc tả head).
    padded = bytes(out) + b"\x00" * ((-len(out)) % 4)
    total = 0
    for i in range(0, len(padded), 4):
        total = (total + struct.unpack_from(">I", padded, i)[0]) & 0xFFFFFFFF
    adjustment = (_CHECKSUM_MAGIC - total) & 0xFFFFFFFF
    # Vá adjustment vào bytes của bảng head trong file (checksum ghi trong
    # thư mục bảng KHÔNG đổi — vẫn là bản tính với adjustment = 0).
    head_off = dict((t, o) for t, _c, o, _l in records).get("head")
    if head_off is None:
        raise _err("E_CORRUPT", "không dựng được font thiếu bảng head")
    struct.pack_into(">I", out, head_off + 8, adjustment)
    return bytes(out)


# ------------------------------------------------------------------ gộp glyph
@dataclass
class MergeResult:
    """Kết quả gộp glyph từ font nguồn vào font đích."""
    data: bytes
    added: List[int] = field(default_factory=list)          # ký tự đã thêm cmap
    already: List[int] = field(default_factory=list)        # đã có sẵn, không cần thêm
    donor_missing: List[int] = field(default_factory=list)  # donor cũng không có
    glyphs_copied: int = 0      # số glyph đã copy (kể cả component của composite)
    glyphs_total: int = 0
    warnings: List[str] = field(default_factory=list)


def merge_glyphs(target: bytes, donor: bytes, codepoints: Iterable[int], *,
                 progress_fn: Optional[Callable[[int, int], None]] = None,
                 where: str = "font") -> MergeResult:
    """Gộp glyph cho `codepoints` từ donor vào target; trả font mới (bytes).

    Chỉ font TrueType có outline glyf. Glyph composite được copy kèm toàn bộ
    component (đệ quy), id component được viết lại theo bảng gộp. `progress_fn`
    được gọi (đã xong, tổng) sau mỗi glyph đã copy.
    """
    target = bytes(target)
    donor = bytes(donor)
    t = parse_font(target, where=where)
    d = parse_font(donor, where=where + " (font nguồn)")
    if t.kind != "ttf" or not t.has_glyf:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "font đích không phải TrueType có outline glyf (kind=%s) — không gộp được"
                   % t.kind, where=where)
    if d.kind != "ttf" or not d.has_glyf:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "font nguồn không phải TrueType có outline glyf (kind=%s) — không gộp được"
                   % d.kind, where=where)
    wanted = sorted(set(int(c) for c in codepoints))
    to_add = [cp for cp in wanted if cp not in t.cmap and cp in d.cmap]
    already = [cp for cp in wanted if cp in t.cmap]
    donor_missing = [cp for cp in wanted if cp not in t.cmap and cp not in d.cmap]
    warnings: List[str] = []
    if donor_missing:
        warnings.append("font nguồn cũng thiếu %d ký tự: %s"
                        % (len(donor_missing),
                           ", ".join(format_codepoint(c) for c in donor_missing[:8])))
    # Bao đóng glyph: glyph đích + toàn bộ component của composite (đệ quy).
    remap: Dict[int, int] = {}
    next_gid = t.num_glyphs
    queue = [d.cmap[cp] for cp in to_add]
    donor_raw: Dict[int, bytes] = {}
    while queue:
        gid = queue.pop(0)
        if gid == 0 or gid in remap:
            continue
        remap[gid] = next_gid
        next_gid += 1
        raw = glyph_data(donor, d, gid)
        donor_raw[gid] = raw
        try:
            queue.extend(composite_components(raw))
        except PB3Error as exc:
            raise _err("E_CORRUPT", "glyph %d của font nguồn hỏng: %s" % (gid, exc.message_vi),
                       where=where)
    total = len(remap)
    done = 0
    if progress_fn is not None and total == 0:
        progress_fn(0, 0)
    # Glyph mới theo thứ tự gid mới (xác định, ổn định giữa các lần chạy).
    new_order = sorted(remap, key=lambda g: remap[g])
    new_glyph_bytes: List[bytes] = []
    for gid in new_order:
        raw = donor_raw[gid]
        if composite_components(raw):
            raw = _rewrite_composite(raw, remap)
        new_glyph_bytes.append(raw)
        done += 1
        if progress_fn is not None:
            progress_fn(done, total)
    # hmtx: mở rộng danh sách đích, thêm (advance, lsb) của donor cho glyph mới.
    t_hmtx = _hmtx_entries(target, t)
    d_hmtx = _hmtx_entries(donor, d)
    for gid in new_order:
        t_hmtx.append(d_hmtx[gid])
    # glyf + loca mới.
    t_locs = _loca_offsets(target, t)
    t_goff, _t_glen = t.tables["glyf"]
    glyf_parts: List[bytes] = []
    loca = [0]
    for gid in range(t.num_glyphs):
        part = bytes(target[t_goff + t_locs[gid]:t_goff + t_locs[gid + 1]])
        glyf_parts.append(part)
        loca.append(loca[-1] + len(part))
    for part in new_glyph_bytes:
        glyf_parts.append(part)
        loca.append(loca[-1] + len(part))
    new_glyf = b"".join(glyf_parts)
    # cmap mới.
    new_cmap = dict(t.cmap)
    for cp in to_add:
        new_cmap[cp] = remap[d.cmap[cp]]
    # Các bảng mới (head/hhea/maxp giữ nguyên trừ trường đổi).
    tables: Dict[str, bytes] = {}
    for tag, (off, ln) in t.tables.items():
        if tag in ("cmap", "glyf", "loca", "hmtx", "hhea", "maxp", "head", "DSIG"):
            continue
        tables[tag] = bytes(target[off:off + ln])
    if "DSIG" in t.tables:
        warnings.append("đã bỏ chữ ký số DSIG — font gộp không còn chữ ký gốc")
    head = bytearray(target[t.tables["head"][0]:t.tables["head"][0] + t.tables["head"][1]])
    struct.pack_into(">I", head, 8, 0)  # checkSumAdjustment: tính lại khi dựng
    tables["head"] = bytes(head)
    hhea = bytearray(target[t.tables["hhea"][0]:t.tables["hhea"][0] + t.tables["hhea"][1]])
    struct.pack_into(">H", hhea, 34, len(t_hmtx))
    tables["hhea"] = bytes(hhea)
    maxp = bytearray(target[t.tables["maxp"][0]:t.tables["maxp"][0] + t.tables["maxp"][1]])
    struct.pack_into(">H", maxp, 4, t.num_glyphs + len(new_order))  # numGlyphs là u16
    tables["maxp"] = bytes(maxp)
    tables["hmtx"] = b"".join(struct.pack(">Hh", a, l) for a, l in t_hmtx)
    if t.index_to_loc_format == 0:
        tables["loca"] = b"".join(struct.pack(">H", o // 2) for o in loca)
    else:
        tables["loca"] = b"".join(struct.pack(">I", o) for o in loca)
    tables["glyf"] = new_glyf
    tables["cmap"] = build_cmap_table(new_cmap)
    sfnt_version = _u32(target, 0)
    out = _assemble_font(sfnt_version, tables)
    # Tự xác minh trước khi trả: parse lại + checksum.
    issues = verify_font(out, where=where)
    if issues:
        raise _err("E_VERIFY_FAILED",
                   "font gộp xong không đạt kiểm tra: %s" % "; ".join(issues[:3]),
                   where=where)
    check = parse_font(out, where=where)
    missing_after = [cp for cp in to_add if cp not in check.cmap]
    if missing_after:
        raise _err("E_CORRUPT",
                   "font gộp xong vẫn thiếu: %s"
                   % ", ".join(format_codepoint(c) for c in missing_after[:4]),
                   where=where)
    return MergeResult(data=out, added=to_add, already=already,
                       donor_missing=donor_missing, glyphs_copied=len(new_order),
                       glyphs_total=t.num_glyphs + len(new_order),
                       warnings=warnings)


def merge_vietnamese(target: bytes, donor: bytes, *,
                     extra: Optional[Iterable[int]] = None,
                     progress_fn: Optional[Callable[[int, int], None]] = None,
                     where: str = "font") -> MergeResult:
    """Gộp toàn bộ ký tự tiếng Việt còn thiếu từ donor vào target."""
    return merge_glyphs(target, donor, required_vietnamese_chars(extra),
                        progress_fn=progress_fn, where=where)


# ------------------------------------------------------------------ Font nhúng Unity
def _read_first_aligned_string(data: bytes, endian: str) -> Optional[str]:
    """Chuỗi đầu object SerializedFile (m_Name): i32 độ dài + bytes + đệm 4."""
    if len(data) < 4:
        return None
    (size,) = struct.unpack_from(endian + "i", data, 0)
    if size < 0 or 4 + size > len(data):
        return None
    try:
        return data[4:4 + size].decode("utf-8")
    except UnicodeDecodeError:
        return None


def list_unity_font_objects(serialized_file) -> List[Tuple[int, Optional[str]]]:
    """[(path_id, tên)] cho mọi Font object (class 128) trong một SerializedFile.

    Chỉ liệt kê — đợt này không sửa được Font object (xem giới hạn ở đầu file).
    Tên đọc từ m_Name (chuỗi đầu object); đọc lỗi thì tên là None.
    """
    out: List[Tuple[int, Optional[str]]] = []
    for obj in getattr(serialized_file, "objects", []):
        if getattr(obj, "class_id", None) != UNITY_FONT_CLASS_ID:
            continue
        name = None
        try:
            raw = serialized_file.object_bytes(obj.path_id)
            name = _read_first_aligned_string(raw, serialized_file.endian)
        except Exception:  # noqa: BLE001 - liệt kê không được làm hỏng quét
            name = None
        out.append((obj.path_id, name))
    return out
