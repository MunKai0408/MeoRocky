"""Doc/ghi bang CFF + gop glyph vao font CFF/OpenType (.otf, "OTTO").

Chi stdlib, Python 3.9. Theo Adobe Technical Note #5176 (CFF) va #5177
(Type 2 Charstrings); chi doc spec / ma nguon mo qua web de hoc, khong
import thu vien font ngoai.

Pham vi:
- Doc CFF: header, Name/Top/String/GlobalSubr INDEX, CharStrings INDEX,
  charset format 0/1/2, Private DICT (defaultWidthX/nominalWidthX/Subrs),
  Encoding (ghi nhan vi tri, khong sua noi dung).
- Chuyen outline quadratic (TrueType glyf) -> Type 2 cubic (nang bac
  chinh xac: C1 = P0 + 2/3(Q-P0), C2 = P2 + 2/3(Q-P2); lam tron control
  point ve int nen duong cong lech toi da 0,375 don vi — do that khi
  verify). Glyph composite TTF duoc flatten (de quy + matrix) truoc.
- Gop glyph tu donor TTF vao target OTF; donor OTF chi copy charstring
  verbatim khi khong goi subr. Glyph cu giu nguyen bytes charstring.
- Dung lai bang CFF: CharStrings INDEX cu + charstring moi noi them,
  charset viet lai format 0, ten glyph moi ("uniXXXX") them vao String
  INDEX, Top DICT chi va operand offset (cac entry khac giu nguyen
  bytes), Private DICT doi nguyen khoi.

GIOI HAN (fail-closed, bao ro thay vi lam hong lang le):
- Font CID-keyed (ROS trong Top DICT: Noto Sans CJK OTF) -> tu choi:
  nhieu Font DICT/Private DICT theo FDArray/FDSelect, ngoai pham vi.
- Charset mac dinh (offset 0/1/2: ISOAdobe/Expert/ExpertSubset) ->
  tu choi (khong the them glyph khi charset la bang co dinh).
- CharstringType != 2, Top DICT INDEX != 1 dict, bang post 1.0/2.5
  (khong dat ten glyph moi) -> tu choi.
- Donor OTF co glyph goi callsubr/callgsubr -> tu choi glyph do (subr
  cua donor khong mang sang target duoc).
- Target TTF + donor OTF (cubic -> quadratic, xap xi hao hut) ->
  tu choi (xem font.merge_glyphs).
- Gop chi copy outline + advance/lsb; khong cap nhat OS/2, kern/GPOS,
  hinting; DSIG bi bo (nhu duong gop TTF).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from core.errors import PB3Error

#: Tag bang CFF trong sfnt (co khoang trang o cuoi).
CFF_TAG = "CFF "

# --- toan tu DICT (5176) -------------------------------------------------
OP_CHARSET = 15
OP_ENCODING = 16
OP_CHARSTRINGS = 17
OP_PRIVATE = 18
OP_SUBRS = 19
OP_DEFAULT_WIDTH = 20
OP_NOMINAL_WIDTH = 21
OP_ROS = (12, 30)
OP_CHARSTRING_TYPE = (12, 6)
OP_FDARRAY = (12, 36)
OP_FDSELECT = (12, 37)

#: Ten glyph moi them vao charset: "uniXXXX" (Unicode hex).
_NEW_GLYPH_NAME_FMT = "uni%04X"


def _err(code: str, msg: str, where: str = "font") -> PB3Error:
    return PB3Error(code, msg, where=where)


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _round_half_away(x: float) -> int:
    """Lam tron ve int (0,5 -> xa 0), tranh round() banker's cua Python."""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


# ------------------------------------------------------------------ so CFF
def _decode_number(data: bytes, pos: int, *, charstring: bool = False,
                   where: str = "font") -> Tuple[float, int]:
    """Doc 1 so CFF (DICT va charstring dung chung ma int).

    32-246: -107..107 (1 byte); 247-250: 108..1131; 251-254: -1131..-108;
    28: int16; DICT 29: int32, 30: real (nibble); charstring 255: fixed
    16.16. Tra (gia tri, vi tri moi). So nguyen tra int, real/fixed
    le tra float.
    """
    if pos >= len(data):
        raise _err("E_CORRUPT", "so CFF bi cat cut", where=where)
    b = data[pos]
    if 32 <= b <= 246:
        return b - 139, pos + 1
    if 247 <= b <= 250:
        if pos + 1 >= len(data):
            raise _err("E_CORRUPT", "so CFF 2 byte bi cat cut", where=where)
        return (b - 247) * 256 + data[pos + 1] + 108, pos + 2
    if 251 <= b <= 254:
        if pos + 1 >= len(data):
            raise _err("E_CORRUPT", "so CFF 2 byte bi cat cut", where=where)
        return -(b - 251) * 256 - data[pos + 1] - 108, pos + 2
    if b == 28:
        if pos + 2 >= len(data):
            raise _err("E_CORRUPT", "so CFF shortint bi cat cut", where=where)
        return struct.unpack_from(">h", data, pos + 1)[0], pos + 3
    if b == 29:
        if charstring:
            raise _err("E_CORRUPT", "byte 29 khong hop le trong charstring",
                       where=where)
        if pos + 4 >= len(data):
            raise _err("E_CORRUPT", "so CFF int32 bi cat cut", where=where)
        return struct.unpack_from(">i", data, pos + 1)[0], pos + 5
    if b == 30:
        if charstring:
            raise _err("E_CORRUPT", "byte 30 khong hop le trong charstring",
                       where=where)
        text = ""
        pos += 1
        while True:
            if pos >= len(data):
                raise _err("E_CORRUPT", "so thuc CFF bi cat cut", where=where)
            byte = data[pos]
            pos += 1
            for nib in (byte >> 4, byte & 0x0F):
                if nib == 0xF:
                    try:
                        return float(text), pos
                    except ValueError:
                        raise _err("E_CORRUPT", "so thuc CFF sai %r" % text,
                                   where=where)
                if nib < 10:
                    text += str(nib)
                elif nib == 0xA:
                    text += "."
                elif nib == 0xB:
                    text += "E"
                elif nib == 0xC:
                    text += "E-"
                elif nib == 0xD:
                    text += "-"
                else:  # 0xE: byte du tru (reserved)
                    raise _err("E_CORRUPT", "nibble 0xE trong so thuc CFF",
                               where=where)
    if b == 255 and charstring:
        if pos + 4 >= len(data):
            raise _err("E_CORRUPT", "so fixed 16.16 bi cat cut", where=where)
        raw = struct.unpack_from(">i", data, pos + 1)[0]
        return raw / 65536.0, pos + 5
    raise _err("E_CORRUPT", "byte so CFF khong hop le: %d" % b, where=where)


def _encode_int(value: int) -> bytes:
    """Ma 1 so nguyen theo ma int CFF (dung cho DICT + charstring)."""
    if not isinstance(value, int):
        raise _err("E_CORRUPT", "ma so CFF can int, nhan %r" % (value,))
    if -107 <= value <= 107:
        return bytes([value + 139])
    if 108 <= value <= 1131:
        value -= 108
        return bytes([247 + (value >> 8), value & 0xFF])
    if -1131 <= value <= -108:
        value = -value - 108
        return bytes([251 + (value >> 8), value & 0xFF])
    if -32768 <= value <= 32767:
        return b"\x1c" + struct.pack(">h", value)
    raise _err("E_CORRUPT", "so nguyen CFF %d vuot tam int16" % value)


def _encode_dict_number(value) -> bytes:
    """Ma 1 toan hang DICT (int -> ma int; float -> real nibble 30)."""
    if isinstance(value, int):
        if -32768 <= value <= 32767 or -1131 <= value <= 1131:
            return _encode_int(value)
        return b"\x1d" + struct.pack(">i", value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 32768:
            return _encode_int(int(value))
        text = repr(value)
        if "e" in text or "E" in text:
            mant, _, exp = text.partition("e")
            exp = exp.replace("+", "")
            text = mant + "E" + exp
        nibbles: List[int] = []
        for ch in text:
            if ch.isdigit():
                nibbles.append(int(ch))
            elif ch == ".":
                nibbles.append(0xA)
            elif ch == "E":
                nibbles.append(0xB)
            elif ch == "-":
                if nibbles and nibbles[-1] == 0xB:
                    nibbles[-1] = 0xC
                else:
                    nibbles.append(0xD)
            elif ch == "+":
                continue
            else:
                raise _err("E_CORRUPT", "so thuc DICT %r khong ma duoc" % value)
        nibbles.append(0xF)
        if len(nibbles) % 2:
            nibbles.append(0xF)
        out = bytearray(b"\x1e")
        for i in range(0, len(nibbles), 2):
            out.append((nibbles[i] << 4) | nibbles[i + 1])
        return bytes(out)
    raise _err("E_CORRUPT", "toan hang DICT %r khong ma duoc" % (value,))


def _op_bytes(op) -> bytes:
    return bytes([12, op[1]]) if isinstance(op, tuple) else bytes([op])


# ------------------------------------------------------------------ DICT
@dataclass
class DictEntry:
    """1 muc DICT: toan hang + toan tu + bytes goc (de giu nguyen khi
    dung lai)."""
    operands: List[float]
    op: object  # int | (12, int)
    raw: bytes = b""


def parse_dict(data: bytes, where: str = "font") -> List[DictEntry]:
    """Doc DICT thanh list entry (ghi nhan ca bytes goc cua moi entry)."""
    out: List[DictEntry] = []
    pos = 0
    stack: List[float] = []
    entry_start = 0
    while pos < len(data):
        b = data[pos]
        if b == 12:
            if pos + 1 >= len(data):
                raise _err("E_CORRUPT", "toan tu DICT escape bi cat cut",
                           where=where)
            out.append(DictEntry(list(stack), (12, data[pos + 1]),
                                 bytes(data[entry_start:pos + 2])))
            stack = []
            pos += 2
            entry_start = pos
        elif b <= 21:
            out.append(DictEntry(list(stack), b,
                                 bytes(data[entry_start:pos + 1])))
            stack = []
            pos += 1
            entry_start = pos
        elif b in (28, 29, 30) or 32 <= b <= 254:
            value, pos = _decode_number(data, pos, where=where)
            stack.append(value)
        else:
            raise _err("E_CORRUPT", "byte DICT khong hop le: %d" % b,
                       where=where)
    if stack:
        raise _err("E_CORRUPT", "DICT ket thuc con toan hang thua (%d)"
                   % len(stack), where=where)
    return out


def build_dict(entries: Iterable[DictEntry]) -> bytes:
    """Dung DICT tu list entry (ma lai toan hang, giu nguyen thu tu)."""
    out = bytearray()
    for entry in entries:
        for value in entry.operands:
            out += _encode_dict_number(value)
        out += _op_bytes(entry.op)
    return bytes(out)


def _dict_lookup(entries: List[DictEntry], op) -> Optional[List[float]]:
    for entry in entries:
        if entry.op == op:
            return entry.operands
    return None


# ------------------------------------------------------------------ INDEX
def parse_index(data: bytes, off: int, where: str = "font") -> Tuple[List[bytes], int]:
    """Doc INDEX tai `off`: (list muc, offset ket thuc). Kiem tra offSize,
    offset tang don dieu bat dau tu 1, muc nam trong `data`."""
    if off < 0 or off + 2 > len(data):
        raise _err("E_CORRUPT", "INDEX tai %d vuot bang" % off, where=where)
    count = _u16(data, off)
    if count == 0:
        return [], off + 2
    if off + 3 > len(data):
        raise _err("E_CORRUPT", "INDEX tai %d thieu offSize" % off, where=where)
    off_size = data[off + 2]
    if off_size not in (1, 2, 3, 4):
        raise _err("E_CORRUPT", "INDEX offSize %d khong hop le" % off_size,
                   where=where)
    head = off + 3 + (count + 1) * off_size
    if head > len(data):
        raise _err("E_CORRUPT", "INDEX tai %d bi cat cut (head)" % off,
                   where=where)
    offsets = [int.from_bytes(data[off + 3 + i * off_size:
                                   off + 3 + (i + 1) * off_size], "big")
               for i in range(count + 1)]
    if offsets[0] != 1:
        raise _err("E_CORRUPT", "INDEX offset dau phai 1 (nhan %d)"
                   % offsets[0], where=where)
    for i in range(count):
        if offsets[i + 1] < offsets[i]:
            raise _err("E_CORRUPT", "INDEX offset khong don dieu", where=where)
    end = head + offsets[-1] - 1
    if end > len(data):
        raise _err("E_CORRUPT", "INDEX tai %d vuot bang (end %d > %d)"
                   % (off, end, len(data)), where=where)
    return [bytes(data[head + offsets[i] - 1:head + offsets[i + 1] - 1])
            for i in range(count)], end


def build_index(items: List[bytes]) -> bytes:
    """Dung INDEX (offSize nho nhat du chua offset lon nhat)."""
    if not items:
        return struct.pack(">H", 0)
    total = sum(len(item) for item in items)
    for off_size in (1, 2, 3, 4):
        if total + 1 <= (1 << (8 * off_size)) - 1:
            break
    else:
        raise _err("E_CORRUPT", "INDEX qua lon de dung")
    out = bytearray()
    out += struct.pack(">H", len(items))
    out += bytes([off_size])
    pos = 1
    offsets = []
    for item in items:
        offsets.append(pos)
        pos += len(item)
    offsets.append(pos)
    for value in offsets:
        out += value.to_bytes(off_size, "big")
    for item in items:
        out += item
    return bytes(out)


# ------------------------------------------------------------------ charset
def parse_charset(data: bytes, off: int, num_glyphs: int,
                  where: str = "font") -> List[int]:
    """Doc charset thanh list SID theo gid (gid 0 -> SID 0 '.notdef').

    Offset 0/1/2 = charset mac dinh (ISOAdobe/Expert/ExpertSubset) ->
    tu choi (khong them glyph duoc khi charset la bang co dinh).
    """
    if off in (0, 1, 2):
        raise _err("E_UNSUPPORTED_FORMAT",
                   "charset CFF mac dinh (%s) — khong gop them glyph duoc "
                   "(chi ho tro charset ghi ro format 0/1/2)"
                   % ("ISOAdobe" if off == 0 else
                      "Expert" if off == 1 else "ExpertSubset"),
                   where=where)
    if off < 0 or off >= len(data):
        raise _err("E_CORRUPT", "offset charset %d vuot bang CFF" % off,
                   where=where)
    fmt = data[off]
    sids = [0]  # gid 0 (.notdef) khong co trong charset
    pos = off + 1
    if fmt == 0:
        need = num_glyphs - 1
        if pos + 2 * need > len(data):
            raise _err("E_CORRUPT", "charset format 0 bi cat cut", where=where)
        for _ in range(need):
            sids.append(_u16(data, pos))
            pos += 2
    elif fmt == 1:
        while len(sids) < num_glyphs:
            if pos + 3 > len(data):
                raise _err("E_CORRUPT", "charset format 1 bi cat cut",
                           where=where)
            first = _u16(data, pos)
            n_left = data[pos + 2]
            pos += 3
            sids.extend(range(first, first + n_left + 1))
        if len(sids) > num_glyphs:
            raise _err("E_CORRUPT", "charset format 1 thua glyph (%d > %d)"
                       % (len(sids), num_glyphs), where=where)
    elif fmt == 2:
        while len(sids) < num_glyphs:
            if pos + 4 > len(data):
                raise _err("E_CORRUPT", "charset format 2 bi cat cut",
                           where=where)
            first = _u16(data, pos)
            n_left = _u16(data, pos + 2)
            pos += 4
            sids.extend(range(first, first + n_left + 1))
        if len(sids) > num_glyphs:
            raise _err("E_CORRUPT", "charset format 2 thua glyph (%d > %d)"
                       % (len(sids), num_glyphs), where=where)
    else:
        raise _err("E_CORRUPT", "charset format %d la" % fmt, where=where)
    if len(sids) != num_glyphs:
        raise _err("E_CORRUPT", "charset co %d SID, can %d glyph"
                   % (len(sids), num_glyphs), where=where)
    return sids


def build_charset_format0(sids: List[int]) -> bytes:
    """Dung charset format 0 tu list SID theo gid (bo gid 0)."""
    out = bytearray(b"\x00")
    for sid in sids[1:]:
        if not 0 <= sid <= 0xFFFF:
            raise _err("E_CORRUPT", "SID charset %d vuot u16" % sid)
        out += struct.pack(">H", sid)
    return bytes(out)


# ------------------------------------------------------------------ charstring Type 2
#: Doan outline tuyet doi: ("move", x, y) | ("line", x, y) |
#: ("curve", x1, y1, x2, y2, x3, y3).
Segment = Tuple[object, ...]

# Toan tu charstring 1 byte.
_CS_HSTEM = 1
_CS_VSTEM = 3
_CS_VMOVETO = 4
_CS_RLINETO = 5
_CS_HLINETO = 6
_CS_VLINETO = 7
_CS_RRCURVETO = 8
_CS_CALLSUBR = 10
_CS_RETURN = 11
_CS_ENDCHAR = 14
_CS_HSTEMHM = 18
_CS_HINTMASK = 19
_CS_CNTRMASK = 20
_CS_RMOVETO = 21
_CS_HMOVETO = 22
_CS_VSTEMHM = 23
_CS_RCURVELINE = 24
_CS_RLINECURVE = 25
_CS_VVCURVETO = 26
_CS_HHCURVETO = 27
_CS_CALLGSUBR = 29
_CS_VHCURVETO = 30
_CS_HVCURVETO = 31
# Toan tu escape (12 xx).
_CS_FLEX = 35
_CS_HFLEX = 34
_CS_SEAC = 6
_CS_SBW = 7
_CS_DIV = 12
_CS_SETPOINT = 33
_CS_HFLEX1 = 36
_CS_FLEX1 = 37

#: So doi so cua toan tu ve duong (khong tinh width) — dung de nhan width.
_PATH_ARGC = {
    _CS_RMOVETO: 2, _CS_HMOVETO: 1, _CS_VMOVETO: 1,
    _CS_RLINETO: 2, _CS_HLINETO: 1, _CS_VLINETO: 1,
    _CS_RRCURVETO: 6, _CS_RCURVELINE: 8, _CS_RLINECURVE: 8,
    _CS_VVCURVETO: 4, _CS_HHCURVETO: 4,
    _CS_VHCURVETO: 4, _CS_HVCURVETO: 4,
}


@dataclass
class CharstringInfo:
    """Ket qua giai ma 1 charstring Type 2."""
    width: Optional[float] = None
    segments: List[Segment] = field(default_factory=list)
    uses_subr: bool = False
    has_endchar: bool = False
    #: Toan tu ve duong dau tien (int) — dung de biet width co tin duoc
    #: khong (chi moveto/stem dau tien cho width chac chan).
    first_op: Optional[int] = None


def decode_charstring(data: bytes, where: str = "font") -> CharstringInfo:
    """Giai ma charstring Type 2 thanh outline tuyet doi.

    Chi ho tro toan tu ve duong + stem/hintmask + flex/hflex (mo thanh
    2 duong cong). Gap callsubr/callgsubr -> dung, danh dau uses_subr
    (nguoi goi tu choi glyph, khong mo subr). Gap toan tu khong ho tro
    (seac/sbw/div/setcurrentpoint/hflex1/flex1/...) -> tu choi ro rang.
    """
    info = CharstringInfo()
    stack: List[float] = []
    stems = 0
    x = 0.0
    y = 0.0
    started = False
    width_done = False
    pos = 0

    def _width_check(n_expected: int) -> None:
        nonlocal width_done
        if width_done:
            return
        width_done = True
        if len(stack) == n_expected + 1:
            info.width = stack.pop(0)
        elif len(stack) != n_expected:
            raise _err("E_CORRUPT",
                       "charstring: doi so sai (%d, can %d)"
                       % (len(stack), n_expected), where=where)

    def _pop(n: int) -> List[float]:
        if len(stack) < n:
            raise _err("E_CORRUPT", "charstring: thieu doi so (can %d, co %d)"
                       % (n, len(stack)), where=where)
        out = stack[:n]
        del stack[:n]
        return out

    while pos < len(data):
        b = data[pos]
        if b == 28 or b == 255 or 32 <= b <= 254:
            value, pos = _decode_number(data, pos, charstring=True, where=where)
            if len(stack) >= 48:
                raise _err("E_CORRUPT", "charstring: stack tran 48", where=where)
            stack.append(value)
            continue
        if b == 12:
            if pos + 1 >= len(data):
                raise _err("E_CORRUPT", "charstring escape bi cat cut",
                           where=where)
            op = (12, data[pos + 1])
            pos += 2
            if op == (12, _CS_FLEX):
                args = _pop(13)
                # flex: 6 diem tuong doi + do sau; mo thanh 2 cubic.
                p = [(x, y)]
                for i in range(0, 12, 2):
                    p.append((p[-1][0] + args[i], p[-1][1] + args[i + 1]))
                info.segments.append(("curve", p[1][0], p[1][1], p[2][0],
                                      p[2][1], p[3][0], p[3][1]))
                info.segments.append(("curve", p[4][0], p[4][1], p[5][0],
                                      p[5][1], p[6][0], p[6][1]))
                x, y = p[6]
                started = True
            elif op == (12, _CS_HFLEX):
                args = _pop(7)
                p = [(x, y)]
                dx = [args[0], args[2], args[3], args[4], args[5], args[6]]
                dy = [args[1], 0.0, 0.0, 0.0, 0.0, 0.0]
                # hflex: dx1 dy1 dx2 dx3 dx4 dx5 dx6 (dy2..dy6 = 0, dy7 = -dy1)
                pts = [(dx[0], dy[0]), (dx[1], 0.0), (dx[2], 0.0),
                       (dx[3], 0.0), (dx[4], 0.0), (dx[5], -dy[0])]
                for ddx, ddy in pts:
                    p.append((p[-1][0] + ddx, p[-1][1] + ddy))
                info.segments.append(("curve", p[1][0], p[1][1], p[2][0],
                                      p[2][1], p[3][0], p[3][1]))
                info.segments.append(("curve", p[4][0], p[4][1], p[5][0],
                                      p[5][1], p[6][0], p[6][1]))
                x, y = p[6]
                started = True
            else:
                raise _err("E_UNSUPPORTED_FORMAT",
                           "charstring: toan tu escape 12 %d chua ho tro"
                           % op[1], where=where)
            continue
        op = b
        pos += 1
        if op in (_CS_HSTEM, _CS_VSTEM, _CS_HSTEMHM, _CS_VSTEMHM):
            if not width_done:
                if len(stack) % 2 == 1:
                    info.width = stack.pop(0)
                width_done = True
            if len(stack) % 2 != 0:
                raise _err("E_CORRUPT", "charstring: stem doi so le",
                           where=where)
            stems += len(stack) // 2
            stack = []
            continue
        if op in (_CS_HINTMASK, _CS_CNTRMASK):
            if not width_done:
                if len(stack) % 2 == 1:
                    info.width = stack.pop(0)
                width_done = True
            stems += len(stack) // 2
            stack = []
            mask_len = (stems + 7) // 8
            if pos + mask_len > len(data):
                raise _err("E_CORRUPT", "charstring: hintmask bi cat cut",
                           where=where)
            pos += mask_len
            continue
        if op in (_CS_CALLSUBR, _CS_CALLGSUBR):
            info.uses_subr = True
            return info
        if op == _CS_RETURN:
            raise _err("E_CORRUPT", "charstring: return o cap goc",
                       where=where)
        if op == _CS_ENDCHAR:
            if len(stack) == 4:
                raise _err("E_UNSUPPORTED_FORMAT",
                           "charstring: endchar 4 doi so (seac cu) chua ho tro",
                           where=where)
            if len(stack) == 1 and not started and info.width is None:
                # Glyph rong chi co width + endchar: so duy nhat la width.
                info.width = stack.pop()
            elif stack:
                raise _err("E_CORRUPT",
                           "charstring: endchar doi so la (%d)" % len(stack),
                           where=where)
            info.has_endchar = True
            stack = []
            break
        if op not in _PATH_ARGC:
            raise _err("E_UNSUPPORTED_FORMAT",
                       "charstring: toan tu %d chua ho tro" % op, where=where)
        # Width: chi xac dinh o toan tu dau tien. Voi op da doi so, width
        # ton tai khi tong so doi so = (so hop le) + 1. vvcurveto/hhcurveto
        # dang le (4n+1) la "doi so le dau" theo spec, KHONG phai width
        # (width truoc 2 op nay thuc te khong gap; neu gap o vi tri dau
        # thi tu choi o muc goi de khong doan sai width — xem decode
        # tra ve first_op_kind).
        n = len(stack)
        if not width_done:
            width_done = True
            if op in (_CS_RMOVETO, _CS_HMOVETO, _CS_VMOVETO):
                if n == _PATH_ARGC[op] + 1:
                    info.width = stack.pop(0)
                elif n != _PATH_ARGC[op]:
                    raise _err("E_CORRUPT",
                               "charstring: moveto doi so sai (%d)" % n,
                               where=where)
            elif op == _CS_RLINETO:
                if n % 2 == 1:
                    if n < 3:
                        raise _err("E_CORRUPT", "charstring: rlineto doi so sai",
                                   where=where)
                    info.width = stack.pop(0)
            elif op == _CS_RRCURVETO:
                if n % 6 == 1:
                    info.width = stack.pop(0)
            elif op == _CS_RCURVELINE:
                if n % 6 == 3:
                    info.width = stack.pop(0)
            elif op == _CS_RLINECURVE:
                if n % 2 == 1:
                    info.width = stack.pop(0)
            elif op in (_CS_HLINETO, _CS_VLINETO):
                # So doi so tuy y: khong phan biet duoc width voi duong
                # thang — chi chap nhan khi KHONG phai toan tu dau tien
                # co width (width da duoc xu ly o moveto/stem truoc do).
                pass
            elif op in (_CS_VVCURVETO, _CS_HHCURVETO, _CS_VHCURVETO,
                        _CS_HVCURVETO):
                # Dang le cua vv/hh la doi so le dau theo spec; vh/hv
                # dang 4+8n(+1): doi so thua cuoi cung moi co the la
                # width — mo ho, tu choi de an toan.
                if op in (_CS_VHCURVETO, _CS_HVCURVETO) and n % 8 == 5:
                    raise _err("E_UNSUPPORTED_FORMAT",
                               "charstring: %s o vi tri dau co doi so mo ho "
                               "(khong phan biet width)" % op, where=where)
            info.first_op = op
        args = stack
        stack = []
        if op == _CS_RMOVETO:
            x += args[0]
            y += args[1]
            info.segments.append(("move", x, y))
        elif op == _CS_HMOVETO:
            x += args[0]
            info.segments.append(("move", x, y))
        elif op == _CS_VMOVETO:
            y += args[0]
            info.segments.append(("move", x, y))
        elif op == _CS_RLINETO:
            if len(args) % 2 != 0 or not args:
                raise _err("E_CORRUPT", "charstring: rlineto doi so sai",
                           where=where)
            for i in range(0, len(args), 2):
                x += args[i]
                y += args[i + 1]
                info.segments.append(("line", x, y))
        elif op == _CS_HLINETO:
            horiz = True
            for a in args:
                if horiz:
                    x += a
                else:
                    y += a
                info.segments.append(("line", x, y))
                horiz = not horiz
        elif op == _CS_VLINETO:
            vert = True
            for a in args:
                if vert:
                    y += a
                else:
                    x += a
                info.segments.append(("line", x, y))
                vert = not vert
        elif op == _CS_RRCURVETO:
            if len(args) % 6 != 0 or not args:
                raise _err("E_CORRUPT", "charstring: rrcurveto doi so sai",
                           where=where)
            for i in range(0, len(args), 6):
                p1 = (x + args[i], y + args[i + 1])
                p2 = (p1[0] + args[i + 2], p1[1] + args[i + 3])
                p3 = (p2[0] + args[i + 4], p2[1] + args[i + 5])
                info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                      p3[0], p3[1]))
                x, y = p3
        elif op == _CS_RCURVELINE:
            if len(args) < 8 or len(args) % 6 != 2:
                raise _err("E_CORRUPT", "charstring: rcurveline doi so sai",
                           where=where)
            for i in range(0, len(args) - 2, 6):
                p1 = (x + args[i], y + args[i + 1])
                p2 = (p1[0] + args[i + 2], p1[1] + args[i + 3])
                p3 = (p2[0] + args[i + 4], p2[1] + args[i + 5])
                info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                      p3[0], p3[1]))
                x, y = p3
            x += args[-2]
            y += args[-1]
            info.segments.append(("line", x, y))
        elif op == _CS_RLINECURVE:
            if len(args) < 6 or len(args) % 2 != 0:
                raise _err("E_CORRUPT", "charstring: rlinecurve doi so sai",
                           where=where)
            for i in range(0, len(args) - 6, 2):
                x += args[i]
                y += args[i + 1]
                info.segments.append(("line", x, y))
            j = len(args) - 6
            p1 = (x + args[j], y + args[j + 1])
            p2 = (p1[0] + args[j + 2], p1[1] + args[j + 3])
            p3 = (p2[0] + args[j + 4], p2[1] + args[j + 5])
            info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                  p3[0], p3[1]))
            x, y = p3
        elif op == _CS_VVCURVETO:
            # dx1? {dya dxb dyb dyc}+ : le = dx1 cho curve dau.
            rest = list(args)
            dx1 = 0.0
            if len(rest) % 4 == 1:
                dx1 = rest.pop(0)
            if len(rest) % 4 != 0 or not rest:
                raise _err("E_CORRUPT", "charstring: vvcurveto doi so sai",
                           where=where)
            for i in range(0, len(rest), 4):
                dya, dxb, dyb, dyc = rest[i:i + 4]
                p1 = (x + dx1, y + dya)
                p2 = (p1[0] + dxb, p1[1] + dyb)
                p3 = (p2[0], p2[1] + dyc)
                info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                      p3[0], p3[1]))
                x, y = p3
                dx1 = 0.0
        elif op == _CS_HHCURVETO:
            # dy1? {dxa dxb dyb dxc}+ : le = dy1 cho curve dau.
            rest = list(args)
            dy1 = 0.0
            if len(rest) % 4 == 1:
                dy1 = rest.pop(0)
            if len(rest) % 4 != 0 or not rest:
                raise _err("E_CORRUPT", "charstring: hhcurveto doi so sai",
                           where=where)
            for i in range(0, len(rest), 4):
                dxa, dxb, dyb, dxc = rest[i:i + 4]
                p1 = (x + dxa, y + dy1)
                p2 = (p1[0] + dxb, p1[1] + dyb)
                p3 = (p2[0] + dxc, p2[1])
                info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                      p3[0], p3[1]))
                x, y = p3
                dy1 = 0.0
        elif op == _CS_VHCURVETO:
            # Chi dang 1 curve: dy1 dx2 dy2 dx3 [dyf] (dang nhieu curve
            # {8 doi so}* hiem gap -> tu choi ro rang thay vi doan sai).
            if len(args) not in (4, 5):
                raise _err("E_UNSUPPORTED_FORMAT",
                           "charstring: vhcurveto %d doi so (nhieu curve) "
                           "chua ho tro" % len(args), where=where)
            dy1, dx2, dy2, dx3 = args[:4]
            dy3 = args[4] if len(args) == 5 else 0.0
            p1 = (x, y + dy1)
            p2 = (p1[0] + dx2, p1[1] + dy2)
            p3 = (p2[0] + dx3, p2[1] + dy3)
            info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                  p3[0], p3[1]))
            x, y = p3
        elif op == _CS_HVCURVETO:
            # Chi dang 1 curve: dx1 dx2 dy2 dy3 [dxf].
            if len(args) not in (4, 5):
                raise _err("E_UNSUPPORTED_FORMAT",
                           "charstring: hvcurveto %d doi so (nhieu curve) "
                           "chua ho tro" % len(args), where=where)
            dx1, dx2, dy2, dy3 = args[:4]
            dx3 = args[4] if len(args) == 5 else 0.0
            p1 = (x + dx1, y)
            p2 = (p1[0] + dx2, p1[1] + dy2)
            p3 = (p2[0] + dx3, p2[1] + dy3)
            info.segments.append(("curve", p1[0], p1[1], p2[0], p2[1],
                                  p3[0], p3[1]))
            x, y = p3
        started = True
    if not info.has_endchar and not info.uses_subr:
        raise _err("E_CORRUPT", "charstring thieu endchar", where=where)
    if stack:
        raise _err("E_CORRUPT", "charstring con toan hang thua (%d)"
                   % len(stack), where=where)
    return info


def _cubic_point(p0, p1, p2, p3, t: float) -> Tuple[float, float]:
    """Diem tren cubic Bezier tai t (de Casteljau)."""
    mt = 1.0 - t
    x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
         + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
    y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
         + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
    return x, y


def _quad_point(p0, q, p2, t: float) -> Tuple[float, float]:
    """Diem tren quadratic Bezier tai t."""
    mt = 1.0 - t
    x = mt * mt * p0[0] + 2 * mt * t * q[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * q[1] + t * t * p2[1]
    return x, y


def max_outline_deviation(src: List[Segment], dst: List[Segment],
                          samples: int = 25) -> float:
    """Sai lech lon nhat giua 2 outline (cung so doan, cung thu tu).

    Lay mau moi doan tai `samples` diem; doan move/line so diem cuoi.
    Tra float('inf') khi so doan khac nhau (khong so duoc).
    """
    if len(src) != len(dst):
        return float("inf")
    worst = 0.0
    cur_s = (0.0, 0.0)
    cur_d = (0.0, 0.0)
    for seg_s, seg_d in zip(src, dst):
        if seg_s[0] != seg_d[0]:
            return float("inf")
        kind = seg_s[0]
        if kind == "move":
            worst = max(worst, math.hypot(seg_s[1] - seg_d[1],
                                          seg_s[2] - seg_d[2]))
            cur_s = (seg_s[1], seg_s[2])
            cur_d = (seg_d[1], seg_d[2])
        elif kind == "line":
            worst = max(worst, math.hypot(seg_s[1] - seg_d[1],
                                          seg_s[2] - seg_d[2]))
            cur_s = (seg_s[1], seg_s[2])
            cur_d = (seg_d[1], seg_d[2])
        elif kind == "curve":
            for i in range(samples + 1):
                t = i / samples
                ps = _cubic_point(cur_s, (seg_s[1], seg_s[2]),
                                  (seg_s[3], seg_s[4]), (seg_s[5], seg_s[6]), t)
                pd = _cubic_point(cur_d, (seg_d[1], seg_d[2]),
                                  (seg_d[3], seg_d[4]), (seg_d[5], seg_d[6]), t)
                worst = max(worst, math.hypot(ps[0] - pd[0], ps[1] - pd[1]))
            cur_s = (seg_s[5], seg_s[6])
            cur_d = (seg_d[5], seg_d[6])
    return worst


# ------------------------------------------------------------------ outline TTF
#: Co component glyph composite (TrueType).
_COMP_ARG_WORDS = 0x0001
_COMP_ARGS_XY = 0x0002
_COMP_HAVE_SCALE = 0x0008
_COMP_MORE = 0x0020
_COMP_HAVE_XY_SCALE = 0x0040
_COMP_HAVE_2X2 = 0x0080
_COMP_HAVE_INSTR = 0x0100

#: Do sau de quy composite toi da (chong vong lap).
_MAX_COMPOSITE_DEPTH = 8


def _font_module():
    """Import lui sdk.font (tranh import vong: font.py import cff)."""
    from sdk import font as _fs
    return _fs


def _parse_simple_glyph(raw: bytes, where: str = "font") -> List[List[Tuple[float, float, bool]]]:
    """Doc glyph don thanh contour [(x, y, on_curve)]."""
    if len(raw) < 10:
        raise _err("E_CORRUPT", "glyph don qua ngan", where=where)
    n_contours = struct.unpack_from(">h", raw, 0)[0]
    if n_contours <= 0:
        return []
    if len(raw) < 10 + 2 * n_contours + 2:
        raise _err("E_CORRUPT", "glyph don bi cat cut (endPts)", where=where)
    end_pts = [struct.unpack_from(">H", raw, 10 + 2 * i)[0]
               for i in range(n_contours)]
    n_points = end_pts[-1] + 1 if end_pts else 0
    pos = 10 + 2 * n_contours
    instr_len = struct.unpack_from(">H", raw, pos)[0]
    pos += 2 + instr_len
    if pos > len(raw):
        raise _err("E_CORRUPT", "glyph don bi cat cut (instructions)",
                   where=where)
    flags: List[int] = []
    while len(flags) < n_points:
        if pos >= len(raw):
            raise _err("E_CORRUPT", "glyph don bi cat cut (flags)", where=where)
        flag = raw[pos]
        pos += 1
        flags.append(flag)
        if flag & 0x08:
            if pos >= len(raw):
                raise _err("E_CORRUPT", "glyph don bi cat cut (repeat)",
                           where=where)
            repeat = raw[pos]
            pos += 1
            flags.extend([flag] * repeat)
    if len(flags) != n_points:
        raise _err("E_CORRUPT", "glyph don: so flag != so diem", where=where)
    xs: List[float] = []
    for flag in flags:
        if flag & 0x02:
            if pos >= len(raw):
                raise _err("E_CORRUPT", "glyph don bi cat cut (x)", where=where)
            delta = raw[pos]
            pos += 1
            xs.append(delta if flag & 0x10 else -delta)
        elif flag & 0x10:
            xs.append(0)
        else:
            if pos + 2 > len(raw):
                raise _err("E_CORRUPT", "glyph don bi cat cut (x16)",
                           where=where)
            xs.append(struct.unpack_from(">h", raw, pos)[0])
            pos += 2
    ys: List[float] = []
    for flag in flags:
        if flag & 0x04:
            if pos >= len(raw):
                raise _err("E_CORRUPT", "glyph don bi cat cut (y)", where=where)
            delta = raw[pos]
            pos += 1
            ys.append(delta if flag & 0x20 else -delta)
        elif flag & 0x20:
            ys.append(0)
        else:
            if pos + 2 > len(raw):
                raise _err("E_CORRUPT", "glyph don bi cat cut (y16)",
                           where=where)
            ys.append(struct.unpack_from(">h", raw, pos)[0])
            pos += 2
    points: List[Tuple[float, float, bool]] = []
    x = 0.0
    y = 0.0
    for i in range(n_points):
        x += xs[i]
        y += ys[i]
        points.append((x, y, bool(flags[i] & 0x01)))
    contours: List[List[Tuple[float, float, bool]]] = []
    start = 0
    for end in end_pts:
        contours.append(points[start:end + 1])
        start = end + 1
    return contours


def _flatten_glyph(data: bytes, info, gid: int, where: str,
                   _depth: int = 0,
                   _seen: Optional[set] = None) -> List[List[Tuple[float, float, bool]]]:
    """Flatten glyph TTF thanh contour (glyph don doc thang; composite
    de quy + matrix scale/2x2 + offset)."""
    fs = _font_module()
    if _depth > _MAX_COMPOSITE_DEPTH:
        raise _err("E_CORRUPT", "glyph composite de quy qua sau", where=where)
    if _seen is None:
        _seen = set()
    if gid in _seen:
        raise _err("E_CORRUPT", "glyph composite lap vong (gid %d)" % gid,
                   where=where)
    _seen = _seen | {gid}
    raw = fs.glyph_data(data, info, gid)
    if not raw:
        return []
    n_contours = struct.unpack_from(">h", raw, 0)[0]
    if n_contours >= 0:
        return _parse_simple_glyph(raw, where=where)
    # Composite.
    out: List[List[Tuple[float, float, bool]]] = []
    pos = 10
    while True:
        if pos + 4 > len(raw):
            raise _err("E_CORRUPT", "glyph composite bi cat cut", where=where)
        flags, comp_gid = struct.unpack_from(">HH", raw, pos)
        pos += 4
        if flags & _COMP_ARG_WORDS:
            if pos + 4 > len(raw):
                raise _err("E_CORRUPT", "glyph composite bi cat cut (args)",
                           where=where)
            arg1, arg2 = struct.unpack_from(">hh", raw, pos)
            pos += 4
        else:
            if pos + 2 > len(raw):
                raise _err("E_CORRUPT", "glyph composite bi cat cut (args)",
                           where=where)
            arg1, arg2 = struct.unpack_from(">bb", raw, pos)
            pos += 2
        if not flags & _COMP_ARGS_XY:
            raise _err("E_UNSUPPORTED_FORMAT",
                       "glyph composite dung point-matching (khong phai "
                       "offset x/y) — chua ho tro", where=where)
        dx, dy = float(arg1), float(arg2)
        a, b, c, d = 1.0, 0.0, 0.0, 1.0
        if flags & _COMP_HAVE_SCALE:
            if pos + 2 > len(raw):
                raise _err("E_CORRUPT", "glyph composite bi cat cut (scale)",
                           where=where)
            s = struct.unpack_from(">h", raw, pos)[0] / 16384.0
            pos += 2
            a, d = s, s
        elif flags & _COMP_HAVE_XY_SCALE:
            if pos + 4 > len(raw):
                raise _err("E_CORRUPT", "glyph composite bi cat cut (xyscale)",
                           where=where)
            sx, sy = struct.unpack_from(">hh", raw, pos)
            pos += 4
            a, d = sx / 16384.0, sy / 16384.0
        elif flags & _COMP_HAVE_2X2:
            if pos + 8 > len(raw):
                raise _err("E_CORRUPT", "glyph composite bi cat cut (2x2)",
                           where=where)
            xs, s01, s10, ys = struct.unpack_from(">hhhh", raw, pos)
            pos += 8
            a, b, c, d = (xs / 16384.0, s01 / 16384.0,
                          s10 / 16384.0, ys / 16384.0)
        sub = _flatten_glyph(data, info, comp_gid, where,
                             _depth + 1, _seen)
        for contour in sub:
            out.append([(a * px + b * py + dx, c * px + d * py + dy, on)
                        for (px, py, on) in contour])
        if not flags & _COMP_MORE:
            break
    return out


def _walk_contour(contour: List[Tuple[float, float, bool]]):
    """Duyet 1 contour TTF, sinh (kind, ...) voi toa do float tuyet doi:
    ("move", x, y) | ("line", x, y) | ("quad", qx, qy, ex, ey).

    Quy tac diem bat dau TrueType: diem on-curve dau tien; neu diem
    dau off-curve thi bat dau o diem giua diem off-curve dau/cuoi (hoac
    diem on-curve cuoi). Diem on-curve an giua 2 diem off-curve lien
    tiep la diem giua cua chung; contour dong kin ve diem bat dau.
    """
    pts = [(float(px), float(py), bool(on)) for (px, py, on) in contour]
    n = len(pts)
    if n == 0:
        return
    if n == 1:
        return  # 1 diem: khong ve duoc gi
    if pts[0][2]:
        start = (pts[0][0], pts[0][1])
        seq = pts[1:]
    elif pts[-1][2]:
        start = (pts[-1][0], pts[-1][1])
        seq = pts[:-1]
    else:
        start = ((pts[-1][0] + pts[0][0]) / 2.0,
                 (pts[-1][1] + pts[0][1]) / 2.0)
        seq = pts
    yield ("move", start[0], start[1])
    cur = start
    j = 0
    while j < len(seq):
        px, py, on = seq[j]
        if on:
            if (px, py) != cur:
                yield ("line", px, py)
            cur = (px, py)
            j += 1
            continue
        # Diem off-curve Q: diem tiep theo (hoac diem bat dau khi het).
        if j + 1 < len(seq):
            nx, ny, n_on = seq[j + 1]
        else:
            nx, ny, n_on = start[0], start[1], True
        if n_on:
            end = (nx, ny)
            j += 2
        else:
            end = ((px + nx) / 2.0, (py + ny) / 2.0)
            j += 1
        if (px, py) == cur and end == cur:
            cur = end
            continue  # quad suy bien (khong ve gi)
        yield ("quad", px, py, end[0], end[1])
        cur = end
    if cur != start:
        yield ("line", start[0], start[1])


def contours_to_cubic_segments(
        contours: List[List[Tuple[float, float, bool]]]) -> List[Segment]:
    """Chuyen outline quadratic thanh doan cubic TUYET DOI (float chinh
    xac, chua lam tron) — dung de do sai lech khi verify."""
    out: List[Segment] = []
    cur = (0.0, 0.0)
    for contour in contours:
        for item in _walk_contour(contour):
            if item[0] == "move":
                out.append(("move", item[1], item[2]))
                cur = (item[1], item[2])
            elif item[0] == "line":
                out.append(("line", item[1], item[2]))
                cur = (item[1], item[2])
            else:
                _k, qx, qy, ex, ey = item
                c1 = (cur[0] + 2.0 / 3.0 * (qx - cur[0]),
                      cur[1] + 2.0 / 3.0 * (qy - cur[1]))
                c2 = (ex + 2.0 / 3.0 * (qx - ex),
                      ey + 2.0 / 3.0 * (qy - ey))
                out.append(("curve", c1[0], c1[1], c2[0], c2[1], ex, ey))
                cur = (ex, ey)
    return out


def contours_to_charstring(
        contours: List[List[Tuple[float, float, bool]]],
        width_value: Optional[int]) -> bytes:
    """Chuyen outline quadratic thanh charstring Type 2 (rmoveto /
    rlineto / rrcurveto / endchar; width dan dau neu co).

    Nang bac quadratic->cubic chinh xac, control point va diem cuoi
    lam tron ve int (sai lech duong cong <= ~0,71 don vi). Toa do phat theo diem int
    hien tai de phep tru luon chinh xac.
    """
    out = bytearray()
    if width_value is not None:
        out += _encode_int(width_value)
    cur = (0, 0)
    started = False
    for contour in contours:
        for item in _walk_contour(contour):
            if item[0] == "move":
                target = (_round_half_away(item[1]), _round_half_away(item[2]))
                out += _encode_int(target[0] - cur[0])
                out += _encode_int(target[1] - cur[1])
                out += bytes([_CS_RMOVETO])
                cur = target
                started = True
            elif item[0] == "line":
                if not started:
                    raise _err("E_CORRUPT", "outline thieu moveto", where="font")
                target = (_round_half_away(item[1]), _round_half_away(item[2]))
                if target == cur:
                    continue
                out += _encode_int(target[0] - cur[0])
                out += _encode_int(target[1] - cur[1])
                out += bytes([_CS_RLINETO])
                cur = target
            else:
                if not started:
                    raise _err("E_CORRUPT", "outline thieu moveto", where="font")
                _k, qx, qy, ex, ey = item
                c1 = (cur[0] + 2.0 / 3.0 * (qx - cur[0]),
                      cur[1] + 2.0 / 3.0 * (qy - cur[1]))
                c2 = (ex + 2.0 / 3.0 * (qx - ex),
                      ey + 2.0 / 3.0 * (qy - ey))
                c1i = (_round_half_away(c1[0]), _round_half_away(c1[1]))
                c2i = (_round_half_away(c2[0]), _round_half_away(c2[1]))
                endi = (_round_half_away(ex), _round_half_away(ey))
                if c1i == cur and c2i == cur and endi == cur:
                    continue
                out += _encode_int(c1i[0] - cur[0])
                out += _encode_int(c1i[1] - cur[1])
                out += _encode_int(c2i[0] - c1i[0])
                out += _encode_int(c2i[1] - c1i[1])
                out += _encode_int(endi[0] - c2i[0])
                out += _encode_int(endi[1] - c2i[1])
                out += bytes([_CS_RRCURVETO])
                cur = endi
    out += bytes([_CS_ENDCHAR])
    return bytes(out)


# ------------------------------------------------------------------ bang CFF
@dataclass
class CFFInfo:
    """Ket qua doc 1 bang CFF (font khong CID)."""
    data: bytes
    num_glyphs: int
    name: bytes = b""
    header: bytes = b""
    top_entries: List[DictEntry] = field(default_factory=list)
    strings: List[bytes] = field(default_factory=list)
    gsubrs: List[bytes] = field(default_factory=list)
    charstrings: List[bytes] = field(default_factory=list)
    charset_sids: List[int] = field(default_factory=list)
    charset_offset: Optional[int] = None
    encoding_offset: Optional[int] = None
    encoding_bytes: Optional[bytes] = None
    charstrings_offset: int = 0
    private_offset: Optional[int] = None
    private_size: int = 0
    private_bytes: bytes = b""
    private_entries: List[DictEntry] = field(default_factory=list)
    default_width: float = 0
    nominal_width: float = 0
    has_local_subrs: bool = False
    #: Bytes INDEX subr cuc bo (nam sau Private DICT) + offset tuong doi
    #: cua no tinh tu dau Private DICT (toan hang Subrs).
    local_subrs_raw: bytes = b""
    local_subrs_rel_off: int = 0
    name_index_raw: bytes = b""
    gsubr_index_raw: bytes = b""
    where: str = "font"


def parse_cff(cff_bytes: bytes, num_glyphs: int,
              where: str = "font") -> CFFInfo:
    """Doc bang CFF (fail-closed: CID, charset mac dinh, CharstringType
    != 2, Top DICT INDEX != 1 dict deu tu choi ro rang)."""
    data = bytes(cff_bytes)
    if len(data) < 4:
        raise _err("E_CORRUPT", "bang CFF qua ngan", where=where)
    major, minor, hdr_size, _off_size = data[0], data[1], data[2], data[3]
    if major != 1 or minor != 0:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "phien ban CFF %d.%d chua ho tro (chi 1.0)"
                   % (major, minor), where=where)
    if hdr_size < 4 or hdr_size > len(data):
        raise _err("E_CORRUPT", "header CFF sai (hdrSize %d)" % hdr_size,
                   where=where)
    info = CFFInfo(data=data, num_glyphs=num_glyphs,
                   header=bytes(data[:hdr_size]), where=where)
    pos = hdr_size
    names, pos = parse_index(data, pos, where=where)
    if not names:
        raise _err("E_CORRUPT", "bang CFF thieu Name INDEX", where=where)
    info.name = names[0]
    name_end = pos
    info.name_index_raw = bytes(data[hdr_size:name_end])
    tops, pos = parse_index(data, pos, where=where)
    if len(tops) != 1:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "Top DICT INDEX co %d dict (chi ho tro 1 font)"
                   % len(tops), where=where)
    info.top_entries = parse_dict(tops[0], where=where)
    strings, pos = parse_index(data, pos, where=where)
    info.strings = strings
    gsubrs, pos = parse_index(data, pos, where=where)
    info.gsubrs = gsubrs
    # -- Top DICT (chi lay cac op can thiet; entry khac giu nguyen bytes).
    top = {entry.op: entry.operands for entry in info.top_entries}
    if OP_ROS in top or OP_FDARRAY in top or OP_FDSELECT in top:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "font CID-keyed (ROS/FDArray) — gop CFF chua ho tro "
                   "(nhieu Font DICT/Private DICT theo FDSelect)",
                   where=where)
    cst = _dict_lookup(info.top_entries, OP_CHARSTRING_TYPE)
    if cst is not None and (len(cst) != 1 or cst[0] != 2):
        raise _err("E_UNSUPPORTED_FORMAT",
                   "CharstringType %r (chi ho tro Type 2 = 2)" % (cst,),
                   where=where)
    cs_ops = _dict_lookup(info.top_entries, OP_CHARSTRINGS)
    if cs_ops is None or len(cs_ops) != 1:
        raise _err("E_CORRUPT", "Top DICT thieu CharStrings", where=where)
    info.charstrings_offset = int(cs_ops[0])
    charset_ops = _dict_lookup(info.top_entries, OP_CHARSET)
    charset_off = int(charset_ops[0]) if charset_ops else 0
    info.charset_offset = charset_off
    info.charset_sids = parse_charset(data, charset_off, num_glyphs,
                                      where=where)
    enc_ops = _dict_lookup(info.top_entries, OP_ENCODING)
    if enc_ops is None or len(enc_ops) != 1:
        info.encoding_offset = None  # mac dinh 0 = StandardEncoding
    else:
        enc_off = int(enc_ops[0])
        if enc_off in (0, 1):
            info.encoding_offset = enc_off
        else:
            if enc_off < 0 or enc_off >= len(data):
                raise _err("E_CORRUPT", "offset Encoding %d vuot bang"
                           % enc_off, where=where)
            info.encoding_offset = enc_off
            # Encoding: format 0 (nCodes + code) / 1 (ranges); cat toi
            # het vung — do dai suy tu format (khong can chinh xac
            # tuyet doi: chi can copy dung bytes; do dai tinh dung).
            fmt = data[enc_off]
            if fmt == 0:
                n_codes = data[enc_off + 1]
                info.encoding_bytes = bytes(data[enc_off:enc_off + 2 + n_codes])
            elif fmt == 1:
                n_ranges = data[enc_off + 1]
                info.encoding_bytes = bytes(
                    data[enc_off:enc_off + 2 + 3 * n_ranges])
            else:
                raise _err("E_CORRUPT", "Encoding format %d la" % fmt,
                           where=where)
    priv_ops = _dict_lookup(info.top_entries, OP_PRIVATE)
    if priv_ops is not None:
        if len(priv_ops) != 2:
            raise _err("E_CORRUPT", "Private DICT can (size, offset)",
                       where=where)
        info.private_size = int(priv_ops[0])
        info.private_offset = int(priv_ops[1])
        poff = info.private_offset
        psize = info.private_size
        if poff < 0 or psize < 0 or poff + psize > len(data):
            raise _err("E_CORRUPT", "Private DICT vuot bang CFF", where=where)
        info.private_bytes = bytes(data[poff:poff + psize])
        info.private_entries = parse_dict(info.private_bytes, where=where)
        dw = _dict_lookup(info.private_entries, OP_DEFAULT_WIDTH)
        nw = _dict_lookup(info.private_entries, OP_NOMINAL_WIDTH)
        info.default_width = dw[0] if dw else 0
        info.nominal_width = nw[0] if nw else 0
        sub = _dict_lookup(info.private_entries, OP_SUBRS)
        if sub is not None:
            info.has_local_subrs = True
            sub_off = int(sub[0])
            if sub_off < 0 or poff + sub_off >= len(data):
                raise _err("E_CORRUPT", "Local Subr INDEX vuot bang",
                           where=where)
            _items, sub_end = parse_index(data, poff + sub_off,
                                          where=where)
            info.local_subrs_raw = bytes(data[poff + sub_off:sub_end])
            info.local_subrs_rel_off = sub_off
    info.charstrings, _end = parse_index(data, info.charstrings_offset,
                                         where=where)
    if len(info.charstrings) != num_glyphs:
        raise _err("E_CORRUPT",
                   "CharStrings INDEX co %d muc, can %d glyph"
                   % (len(info.charstrings), num_glyphs), where=where)
    # Raw cua Global Subr INDEX (de dung lai nguyen ven): doc lai
    # tu dau de lay span chinh xac.
    p2 = hdr_size
    _n, p2 = parse_index(data, p2, where=where)
    _t, p2 = parse_index(data, p2, where=where)
    _s, p2 = parse_index(data, p2, where=where)
    g_start = p2
    _g, g_end = parse_index(data, p2, where=where)
    info.gsubr_index_raw = bytes(data[g_start:g_end])
    return info


def _rebuild_top_dict(info: CFFInfo, *, charset_off: int,
                      encoding_off: Optional[int], charstrings_off: int,
                      private_off: Optional[int],
                      private_size: Optional[int] = None) -> bytes:
    """Dung lai Top DICT: chi va operand offset (CharStrings/charset/
    Private/Encoding); cac entry khac giu nguyen bytes goc."""
    out = bytearray()
    for entry in info.top_entries:
        if entry.op == OP_CHARSET:
            out += _encode_dict_number(charset_off)
            out += _op_bytes(entry.op)
        elif entry.op == OP_CHARSTRINGS:
            out += _encode_dict_number(charstrings_off)
            out += _op_bytes(entry.op)
        elif entry.op == OP_PRIVATE:
            if private_off is None:
                continue  # khong co Private: bo entry (khong xay ra)
            out += _encode_dict_number(
                info.private_size if private_size is None else private_size)
            out += _encode_dict_number(private_off)
            out += _op_bytes(entry.op)
        elif entry.op == OP_ENCODING:
            if encoding_off is None:
                out += entry.raw
            else:
                out += _encode_dict_number(encoding_off)
                out += _op_bytes(entry.op)
        else:
            out += entry.raw
    return bytes(out)


def build_cff(info: CFFInfo, new_charstrings: List[bytes],
              new_charset_sids: List[int],
              new_strings: List[bytes]) -> bytes:
    """Dung lai bang CFF sau khi gop.

    Thu tu: header (goc) | Name INDEX (goc) | Top DICT INDEX (dung lai)
    | String INDEX (+ ten moi) | Global Subr INDEX (goc) | charset fmt 0
    | Encoding (goc, neu co bytes) | CharStrings INDEX (+ moi) | Private
    DICT (goc). Lap 2-pass toi khi offset on dinh (Top DICT chua offset).
    """
    charset_bytes = build_charset_format0(new_charset_sids)
    string_index = build_index(list(info.strings) + list(new_strings))
    charstrings_index = build_index(list(new_charstrings))
    # Private DICT: giu nguyen bytes, tru khi vi tri Local Subr INDEX
    # doi (Subrs offset tinh tu dau Private). Khi dung lai, Local Subr
    # INDEX dat ngay sau Private DICT nen offset moi = len(private).
    private_bytes = bytes(info.private_bytes)
    if info.has_local_subrs and info.local_subrs_rel_off != len(private_bytes):
        # Lap toi khi operand Subrs == len(Private) on dinh (ma operand
        # doi do dai o nguong 108/1132 nen hoi tu sau <= 2 vong).
        for _ in range(4):
            rebuilt = bytearray()
            for entry in info.private_entries:
                if entry.op == OP_SUBRS:
                    rebuilt += _encode_dict_number(len(private_bytes))
                    rebuilt += _op_bytes(entry.op)
                else:
                    rebuilt += entry.raw
            if bytes(rebuilt) == private_bytes:
                break
            private_bytes = bytes(rebuilt)
    top = b""
    # Uoc luong offset ban dau (dung top cu), lap toi khi on dinh.
    charset_off = 0
    encoding_off = info.encoding_offset
    charstrings_off = 0
    private_off = info.private_offset
    for _ in range(8):
        pos = len(info.header)
        pos += len(info.name_index_raw)
        top_index = build_index([top]) if top else build_index([b""])
        pos += len(top_index)
        pos += len(string_index)
        pos += len(info.gsubr_index_raw)
        new_charset_off = pos
        pos += len(charset_bytes)
        new_encoding_off = None
        if info.encoding_bytes is not None:
            new_encoding_off = pos
            pos += len(info.encoding_bytes)
        elif encoding_off in (0, 1):
            new_encoding_off = encoding_off
        new_charstrings_off = pos
        pos += len(charstrings_index)
        new_private_off = pos if info.private_offset is not None else None
        pos += len(private_bytes)
        pos += len(info.local_subrs_raw)
        new_top = _rebuild_top_dict(
            info, charset_off=new_charset_off,
            encoding_off=new_encoding_off,
            charstrings_off=new_charstrings_off,
            private_off=new_private_off,
            private_size=len(private_bytes))
        if (new_charset_off == charset_off
                and new_charstrings_off == charstrings_off
                and new_private_off == private_off
                and new_encoding_off == encoding_off
                and new_top == top):
            top = new_top
            charset_off, charstrings_off, private_off = (
                new_charset_off, new_charstrings_off, new_private_off)
            encoding_off = new_encoding_off
            break
        top = new_top
        charset_off, charstrings_off, private_off = (
            new_charset_off, new_charstrings_off, new_private_off)
        encoding_off = new_encoding_off
    else:
        raise _err("E_CORRUPT", "dung bang CFF khong hoi tu offset",
                   where=info.where)
    out = bytearray()
    out += info.header
    out += info.name_index_raw
    out += build_index([top])
    out += string_index
    out += info.gsubr_index_raw
    out += charset_bytes
    if info.encoding_bytes is not None:
        out += info.encoding_bytes
    out += charstrings_index
    out += private_bytes
    out += info.local_subrs_raw
    return bytes(out)


def _replace_charstring_width(cs: bytes, new_width) -> bytes:
    """Thay/them/bo token width o dau charstring.

    new_width = None -> bo token width (neu co); so -> dat width do
    (chen neu chua co, thay neu da co). Chi dung khi biet chac token
    dau tien la width (giai ma tu moveto/stem dau tien) — xem merge.
    Tra charstring moi (bytes).
    """
    info = decode_charstring(cs)
    if info.width is None and new_width is None:
        return bytes(cs)
    if info.width is None:
        # Chen width vao dau: token dau tien bat dau tai 0.
        return _encode_int(int(new_width)) + bytes(cs)
    # Tim span cua token so dau tien (la width).
    _value, end = _decode_number(cs, 0, charstring=True)
    if new_width is None:
        return bytes(cs[end:])
    return _encode_int(int(new_width)) + bytes(cs[end:])


def _post_format(data: bytes, tables: Dict[str, Tuple[int, int]]) -> float:
    if "post" not in tables:
        return 3.0
    off, _ln = tables["post"]
    return struct.unpack_from(">I", data, off)[0] / 65536.0


# ------------------------------------------------------------------ gop glyph
def _extend_post_2_0(target: bytes, tables: Dict[str, Tuple[int, int]],
                     old_num_glyphs: int, new_names: List[str],
                     where: str) -> bytes:
    """Noi them ten glyph vao bang post format 2.0.

    Tra bytes post moi: header + glyphNameIndex (cu + moi) + chuoi Pascal
    (cu + moi). Ten moi dung chi muc 258 + vi tri trong mang ten rieng.
    """
    off, ln = tables["post"]
    raw = bytes(target[off:off + ln])
    if len(raw) < 34:
        raise _err("E_CORRUPT", "bang post 2.0 qua ngan", where=where)
    num = _u16(raw, 32)
    if num != old_num_glyphs:
        raise _err("E_CORRUPT",
                   "bang post 2.0 co %d glyph, can %d" % (num, old_num_glyphs),
                   where=where)
    indices = list(struct.unpack_from(">%dH" % num, raw, 34))
    pos = 34 + 2 * num
    customs: List[bytes] = []
    max_custom = -1
    for idx in indices:
        if idx >= 258:
            max_custom = max(max_custom, idx - 258)
    # Doc chuoi Pascal cu (de giu nguyen): can so luong = max_custom + 1.
    p = pos
    for _ in range(max_custom + 1):
        if p >= len(raw):
            raise _err("E_CORRUPT", "bang post 2.0 bi cat cut (ten)",
                       where=where)
        n = raw[p]
        customs.append(bytes(raw[p:p + 1 + n]))
        p += 1 + n
    new_indices = list(indices)
    for name in new_names:
        encoded = name.encode("latin-1")
        if len(encoded) > 255:
            raise _err("E_CORRUPT", "ten glyph post qua dai: %r" % name,
                       where=where)
        customs.append(bytes([len(encoded)]) + encoded)
        new_indices.append(258 + len(customs) - 1)
    out = bytearray()
    out += raw[:32]
    out += struct.pack(">H", len(new_indices))
    out += struct.pack(">%dH" % len(new_indices), *new_indices)
    for blob in customs:
        out += blob
    return bytes(out)


def merge_cff_glyphs(target: bytes, donor: bytes,
                     codepoints: Iterable[int], *,
                     progress_fn: Optional[Callable[[int, int], None]] = None,
                     where: str = "font"):
    """Gop glyph cho `codepoints` tu donor vao target CFF (.otf).

    Donor TTF: outline quadratic duoc flatten + chuyen sang Type 2 cubic.
    Donor OTF: copy charstring verbatim (chi khi khong goi subr va width
    doc duoc chac chan tu moveto/stem dau tien). Tra MergeResult cua
    sdk.font (cung dang voi duong gop TTF).
    """
    from sdk import font as fs

    target = bytes(target)
    donor = bytes(donor)
    t = fs.parse_font(target, where=where)
    d = fs.parse_font(donor, where=where + " (font nguon)")
    if t.kind != "otf":
        raise _err("E_UNSUPPORTED_FORMAT",
                   "font dich khong phai CFF (.otf) (kind=%s)" % t.kind,
                   where=where)
    if "CFF " not in t.tables:
        raise _err("E_CORRUPT", "font OTF thieu bang CFF", where=where)
    cff_off, cff_len = t.tables["CFF "]
    info = parse_cff(target[cff_off:cff_off + cff_len], t.num_glyphs,
                     where=where)
    donor_is_cff = d.kind == "otf"
    if donor_is_cff:
        if "CFF " not in d.tables:
            raise _err("E_CORRUPT", "font nguon OTF thieu bang CFF",
                       where=where)
        doff, dlen = d.tables["CFF "]
        donor_cff = parse_cff(donor[doff:doff + dlen], d.num_glyphs,
                              where=where + " (font nguon)")
    else:
        if d.kind != "ttf" or not d.has_glyf:
            raise _err("E_UNSUPPORTED_FORMAT",
                       "font nguon khong phai TrueType co outline glyf "
                       "(kind=%s) — khong gop duoc" % d.kind, where=where)
        donor_cff = None
    wanted = sorted(set(int(c) for c in codepoints))
    to_add = [cp for cp in wanted if cp not in t.cmap and cp in d.cmap]
    already = [cp for cp in wanted if cp in t.cmap]
    donor_missing = [cp for cp in wanted
                     if cp not in t.cmap and cp not in d.cmap]
    warnings: List[str] = []
    if donor_missing:
        warnings.append("font nguon cung thieu %d ky tu: %s"
                        % (len(donor_missing),
                           ", ".join(fs.format_codepoint(c)
                                     for c in donor_missing[:8])))
    # Donor hmtx (advance/lsb) cho glyph moi.
    d_hmtx = fs._hmtx_entries(donor, d)
    # Charstring moi + outline goc (de do sai lech) + ten glyph moi.
    new_charstrings: List[bytes] = []
    new_names: List[str] = []
    new_sids: List[int] = []
    src_segments: Dict[int, List[Segment]] = {}
    existing_strings = list(info.strings)
    total = len(to_add)
    done = 0
    if progress_fn is not None and total == 0:
        progress_fn(0, 0)
    for cp in to_add:
        gid = d.cmap[cp]
        advance, _lsb = d_hmtx[gid]
        if donor_is_cff:
            assert donor_cff is not None
            cs = donor_cff.charstrings[gid]
            try:
                dec = decode_charstring(cs, where=where + " (font nguon)")
            except PB3Error as exc:
                raise _err("E_UNSUPPORTED_FORMAT",
                           "glyph %s cua font nguon CFF khong doc duoc "
                           "(%s: %s) — khong copy duoc"
                           % (fs.format_codepoint(cp), exc.code,
                              exc.message_vi), where=where)
            if dec.uses_subr:
                raise _err("E_UNSUPPORTED_FORMAT",
                           "glyph %s cua font nguon CFF goi subr "
                           "(callsubr/callgsubr) — khong mang subr sang "
                           "font dich duoc" % fs.format_codepoint(cp),
                           where=where)
            if dec.first_op not in (_CS_RMOVETO, _CS_HMOVETO, _CS_VMOVETO,
                                    _CS_HSTEM, _CS_VSTEM, _CS_HSTEMHM,
                                    _CS_VSTEMHM, None):
                raise _err("E_UNSUPPORTED_FORMAT",
                           "glyph %s cua font nguon CFF bat dau bang toan tu "
                           "%r — khong doc duoc width chac chan"
                           % (fs.format_codepoint(cp), dec.first_op),
                           where=where)
            # Width: gia tri trong charstring = advance - nominal(donor);
            # can doi sang advance - nominal(target), hoac bo neu bang
            # defaultWidthX cua target.
            if dec.width is not None:
                target_width = advance - info.nominal_width
                if advance == info.default_width:
                    cs = _replace_charstring_width(cs, None)
                elif target_width != dec.width:
                    cs = _replace_charstring_width(cs, target_width)
            else:
                if advance != info.default_width:
                    cs = _replace_charstring_width(
                        cs, advance - info.nominal_width)
            new_charstrings.append(bytes(cs))
            src_segments[cp] = list(dec.segments)
        else:
            contours = _flatten_glyph(donor, d, gid,
                                      where=where + " (font nguon)")
            src_segments[cp] = contours_to_cubic_segments(contours)
            width_value = None
            if advance != info.default_width:
                width_value = int(advance - info.nominal_width)
            new_charstrings.append(
                contours_to_charstring(contours, width_value))
        name = _NEW_GLYPH_NAME_FMT % cp if cp <= 0xFFFF else "uni%06X" % cp
        new_names.append(name)
        encoded = name.encode("latin-1")
        if encoded in existing_strings:
            new_sids.append(391 + existing_strings.index(encoded))
        else:
            new_sids.append(391 + len(existing_strings))
            existing_strings.append(encoded)
        done += 1
        if progress_fn is not None:
            progress_fn(done, total)
    # -- Dung lai bang CFF.
    all_charstrings = list(info.charstrings) + new_charstrings
    all_sids = list(info.charset_sids) + new_sids
    added_strings = existing_strings[len(info.strings):]
    new_cff = build_cff(info, all_charstrings, all_sids, added_strings)
    # -- Dung lai font (OTTO).
    t_hmtx = fs._hmtx_entries(target, t)
    for cp in to_add:
        t_hmtx.append(d_hmtx[d.cmap[cp]])
    tables: Dict[str, bytes] = {}
    for tag, (off, ln) in t.tables.items():
        if tag in ("cmap", "hmtx", "hhea", "maxp", "head", "CFF ", "DSIG"):
            continue
        tables[tag] = bytes(target[off:off + ln])
    if "DSIG" in t.tables:
        warnings.append("da bo chu ky so DSIG — font gop khong con chu ky goc")
    post_fmt = _post_format(target, t.tables)
    if "post" in t.tables:
        if post_fmt == 2.0:
            tables["post"] = _extend_post_2_0(
                target, t.tables, t.num_glyphs, new_names, where=where)
        elif post_fmt == 3.0:
            tables["post"] = bytes(
                target[t.tables["post"][0]:
                       t.tables["post"][0] + t.tables["post"][1]])
        else:
            raise _err("E_UNSUPPORTED_FORMAT",
                       "bang post format %.1f — khong dat ten glyph moi duoc "
                       "(chi ho tro 2.0/3.0)" % post_fmt, where=where)
    head = bytearray(target[t.tables["head"][0]:
                            t.tables["head"][0] + t.tables["head"][1]])
    struct.pack_into(">I", head, 8, 0)  # checkSumAdjustment: tinh lai
    tables["head"] = bytes(head)
    hhea = bytearray(target[t.tables["hhea"][0]:
                            t.tables["hhea"][0] + t.tables["hhea"][1]])
    struct.pack_into(">H", hhea, 34, len(t_hmtx))
    tables["hhea"] = bytes(hhea)
    maxp = bytearray(target[t.tables["maxp"][0]:
                            t.tables["maxp"][0] + t.tables["maxp"][1]])
    struct.pack_into(">H", maxp, 4, t.num_glyphs + len(to_add))
    tables["maxp"] = bytes(maxp)
    tables["hmtx"] = b"".join(struct.pack(">Hh", a, l) for a, l in t_hmtx)
    new_cmap = dict(t.cmap)
    for i, cp in enumerate(to_add):
        new_cmap[cp] = t.num_glyphs + i
    tables["cmap"] = fs.build_cmap_table(new_cmap)
    tables["CFF "] = new_cff
    out = fs._assemble_font(0x4F54544F, tables)
    # -- Tu xac minh truoc khi tra.
    issues = fs.verify_font(out, where=where)
    if issues:
        raise _err("E_VERIFY_FAILED",
                   "font CFF gop xong khong dat kiem tra: %s"
                   % "; ".join(issues[:3]), where=where)
    check = fs.parse_font(out, where=where)
    missing_after = [cp for cp in to_add if cp not in check.cmap]
    if missing_after:
        raise _err("E_CORRUPT",
                   "font gop xong van thieu: %s"
                   % ", ".join(fs.format_codepoint(c)
                               for c in missing_after[:4]), where=where)
    # Glyph cu: charstring byte-identical; glyph moi: decode + do lech.
    out_cff = parse_cff(out[check.tables["CFF "][0]:
                            check.tables["CFF "][0] + check.tables["CFF "][1]],
                        check.num_glyphs, where=where)
    if list(out_cff.charstrings[:t.num_glyphs]) != list(info.charstrings):
        raise _err("E_VERIFY_FAILED",
                   "font gop xong doi charstring cua glyph cu", where=where)
    worst = 0.0
    for i, cp in enumerate(to_add):
        dec = decode_charstring(out_cff.charstrings[t.num_glyphs + i],
                                where=where)
        if dec.uses_subr:
            raise _err("E_VERIFY_FAILED",
                       "charstring moi %s lai goi subr"
                       % fs.format_codepoint(cp), where=where)
        dev = max_outline_deviation(src_segments[cp], dec.segments)
        worst = max(worst, dev)
    if worst == float("inf"):
        raise _err("E_VERIFY_FAILED",
                   "outline glyph moi lech dang (so doan khac goc)",
                   where=where)
    if worst > 1.0:
        warnings.append("sai lech outline lon nhat %.3f don vi (> 1,0 — "
                        "vuot gioi han lam tron int)" % worst)
    warnings.append("sai lech outline lon nhat %.3f don vi (quadratic->"
                    "cubic, lam tron int)" % worst)
    return fs.MergeResult(data=out, added=to_add, already=already,
                          donor_missing=donor_missing,
                          glyphs_copied=len(to_add),
                          glyphs_total=check.num_glyphs,
                          warnings=warnings)
