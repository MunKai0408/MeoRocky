"""Tao SDF (signed distance field) cho TextMeshPro tu glyph outline.

Chi stdlib, Python 3.9. Khong import fontTools/numpy: moi thu doc tu
bytes font (sfnt) bang struct.

Mo hinh SDF (do that tu atlas that trong game, xem exp/verify_sdf_regen.py):
    value = clamp(round(SDF_CENTER + s * 255 / (2 * spread)), 0, 255)
    s     = khoang cach co dau chinh xac tu outline (don vi px atlas,
            duong ben trong muc), spread = padding + 1.
Atlas luu theo hang BOTTOM-UP (hang 0 = day atlas), giong Unity.

Pham vi:
- Doc font: TrueType glyf (don + composite) va CFF/Type 2 (.otf, "OTTO")
  — can cho Aniara (CFF) cua PB3 va Liberation Sans (TTF).
- Raster SDF chinh xac theo outline (khong dung distance transform xap xi):
  khoang cach min toi doan thang + trong/ngoai bang non-zero winding.
- Dong goi atlas (shelf packing) + bang glyph 2 bo cuc TMP:
  cu 36 byte/record (id = codepoint) va moi 52 byte/record (id = glyph
  index, rect y tinh tu DAY atlas).
- Viet lai object TMP_FontAsset (chen bang glyph moi + sua characterCount)
  — ghi that vao file game do duong MB write (M1) + thay atlas Texture2D
  (xem bridge op tmp_sdf_regen).

GIOI HAN:
- Khong ho tro TTC o day (dung sdk/font.py de tach face truoc), khong ho
  tro glyph bitmap (EBDT/CBDT), khong ho tro hinting (SDF khong can).
- Toc do: ~0.2-0.5 s/glyph (CPython thuan) — du cho thao tac mot lan
  cua nguoi dung, khong dung cho dich hang loat.
"""
from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Tuple

from core.errors import PB3Error

#: Hang so tam cua truong SDF (fit tu atlas that; xem exp/verify_sdf_regen.py).
SDF_CENTER = 126.5
#: Spread = padding + 1 (do that: padding 5 -> slope 255/12; padding 9 -> 255/20).
SPREAD_EXTRA = 1

#: Nguong ti le pixel trung gian (1-254) de nhan dang atlas SDF.
#: Do that: LiberationSans SDF 68-73% trung gian; ContinuumBold (atlas
#: bitmap, bien cung) chi 5% trung gian, 95% la 0/255.
SDF_MIN_MID_FRACTION = 0.15


def is_sdf_atlas(atlas_bytes: bytes, sample: int = 200000) -> bool:
    """Nhan dang atlas SDF vs atlas bitmap (TMP co ca 2 loai).

    Atlas SDF co gradient muot: phan lon pixel co gia tri trung gian
    (1-254). Atlas bitmap co bien cung: hau het pixel la 0 (nen) hoac
    255 (muc), chi vai % trung gian (chong rang cua). Tra True neu ti
    le pixel trung gian >= SDF_MIN_MID_FRACTION. Lay mau toi da
    `sample` pixel de nhanh voi atlas lon.
    """
    n = len(atlas_bytes)
    if n == 0:
        return False
    step = max(1, n // sample)
    mid = 0
    total = 0
    for i in range(0, n, step):
        v = atlas_bytes[i]
        total += 1
        if 0 < v < 255:
            mid += 1
    return total > 0 and (mid / total) >= SDF_MIN_MID_FRACTION

#: So glyph toi da trong 1 atlas (bao ve RAM).
MAX_GLYPHS = 70000
#: Kich thuoc atlas toi da (px).
MAX_ATLAS = 8192
#: So doan thang toi da sau khi flatten 1 glyph.
MAX_SEGMENTS = 200000

# ------------------------------------------------------------------ font
# Outline: list contour; contour = list segment; segment =
#   (kind, x0, y0, x1, y1, x2, y2, x3, y3) voi kind 0=line (x0,y0)-(x1,y1),
#   1=quad (x0,y0)-(x1,y1)-(x2,y2), 2=cubic (x0,y0)..(x3,y3).
# Toa do font units, y HUONG LEN (goc toa do goc font).


class Font(object):
    """Font da doc: upem, cmap cp->gid, outline gid->contours, advance gid."""

    def __init__(self, upem, cmap, outlines, advances, names=None):
        self.upem = upem
        self.cmap = cmap
        self.outlines = outlines
        self.advances = advances
        self.names = names or {}

    def glyph_for(self, cp):
        """gid cua codepoint, None neu font khong co."""
        return self.cmap.get(cp)

    def outline(self, gid):
        return self.outlines.get(gid) or []

    def advance(self, gid):
        return self.advances.get(gid, 0)


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _i16(b, o):
    return struct.unpack_from(">h", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _i32(b, o):
    return struct.unpack_from(">i", b, o)[0]


def _tables(data):
    """Bang sfnt: {tag: (offset, length)}. Nem PB3Error neu khong phai sfnt."""
    if len(data) < 12:
        raise PB3Error("font qua ngan (%d byte), khong phai sfnt" % len(data))
    magic = data[:4]
    if magic not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        raise PB3Error("khong phai font sfnt (magic %r)" % (magic,))
    n = _u16(data, 4)
    out = {}
    for i in range(n):
        o = 12 + 16 * i
        if o + 16 > len(data):
            break
        tag = data[o:o + 4].decode("latin-1")
        out[tag] = (_u32(data, o + 8), _u32(data, o + 12))
    return out, magic == b"OTTO"


def _cmap_format4(data, off):
    seg_count = _u16(data, off + 6) // 2
    end_base = off + 14
    start_base = end_base + 2 * seg_count + 2
    delta_base = start_base + 2 * seg_count
    ro_base = delta_base + 2 * seg_count
    out = {}
    for i in range(seg_count):
        end = _u16(data, end_base + 2 * i)
        start = _u16(data, start_base + 2 * i)
        delta = _i16(data, delta_base + 2 * i)
        ro = _u16(data, ro_base + 2 * i)
        if start == 0xFFFF and end == 0xFFFF:
            continue
        for cp in range(start, end + 1):
            if ro == 0:
                gid = (cp + delta) & 0xFFFF
            else:
                p = ro_base + 2 * i + ro + 2 * (cp - start)
                gid = _u16(data, p)
                if gid:
                    gid = (gid + delta) & 0xFFFF
            if gid:
                out[cp] = gid
    return out


def _cmap_format12(data, off):
    n = _u32(data, off + 12)
    out = {}
    for i in range(n):
        o = off + 16 + 12 * i
        start, end, gid0 = _u32(data, o), _u32(data, o + 4), _u32(data, o + 8)
        for cp in range(start, end + 1):
            out[cp] = gid0 + (cp - start)
    return out


def _read_cmap(data, tables):
    if "cmap" not in tables:
        return {}
    off, _ln = tables["cmap"]
    n = _u16(data, off + 2)
    best = {}
    for i in range(n):
        sub = off + 4 + 8 * i
        platform, encoding = _u16(data, sub), _u16(data, sub + 2)
        soff = off + _u32(data, sub + 4)
        fmt = _u16(data, soff)
        try:
            if fmt == 4:
                m = _cmap_format4(data, soff)
            elif fmt == 12:
                m = _cmap_format12(data, soff)
            else:
                continue
        except (IndexError, struct.error):
            continue
        # Uu tien Unicode (platform 3,10 / 0,*); format 12 truoc format 4
        # neu cung platform (phu ngoai BMP).
        key = (0 if platform in (0, 3) else 1, 0 if fmt == 12 else 1)
        if key < best.get("_key", (9, 9)):
            best = dict(m)
            best["_key"] = key
    best.pop("_key", None)
    return best


def _read_hmtx(data, tables, num_glyphs):
    if "hhea" not in tables or "hmtx" not in tables:
        return {}
    hhea_off, _ = tables["hhea"]
    num_h = _u16(data, hhea_off + 34)
    hmtx_off, _ = tables["hmtx"]
    adv = {}
    last = 0
    for gid in range(num_glyphs):
        if gid < num_h:
            last = _u16(data, hmtx_off + 4 * gid)
        adv[gid] = last
    return adv


def _glyf_simple(data, off):
    """Glyph don: tra ve list contour, moi contour la list segment."""
    n_contours = _i16(data, off)
    end_pts = [_u16(data, off + 10 + 2 * i) for i in range(n_contours)]
    ins_len = _u16(data, off + 10 + 2 * n_contours)
    p = off + 12 + 2 * n_contours + ins_len
    n_pts = end_pts[-1] + 1 if end_pts else 0
    flags = []
    while len(flags) < n_pts:
        fl = data[p]
        p += 1
        flags.append(fl)
        if fl & 0x08:
            rep = data[p]
            p += 1
            flags.extend([fl] * rep)
    xs = []
    x = 0
    for fl in flags:
        if fl & 0x02:
            dx = data[p]
            p += 1
            x += dx if (fl & 0x10) else -dx
        elif not (fl & 0x10):
            dx = _i16(data, p)
            p += 2
            x += dx
        xs.append(x)
    ys = []
    y = 0
    for fl in flags:
        if fl & 0x04:
            dy = data[p]
            p += 1
            y += dy if (fl & 0x20) else -dy
        elif not (fl & 0x20):
            dy = _i16(data, p)
            p += 2
            y += dy
        ys.append(y)
    pts = [(xs[i], ys[i], bool(flags[i] & 0x01)) for i in range(n_pts)]
    contours = []
    start = 0
    for end in end_pts:
        contours.append(_contour_segments(pts[start:end + 1]))
        start = end + 1
    return contours


def _contour_segments(pts):
    """Diem glyf (x, y, on_curve) -> list segment (kind, x0..y3)."""
    if not pts:
        return []
    # Diem bat dau: neu diem cuoi on-curve thi bat dau tu do; neu khong,
    # bat dau tu trung diem diem cuoi/diem dau (ca hai off-curve).
    n = len(pts)
    if pts[-1][2]:
        start_pt = (pts[-1][0], pts[-1][1])
        seq = pts[:-1]
    elif pts[0][2]:
        start_pt = (pts[0][0], pts[0][1])
        seq = pts[1:]
    else:
        start_pt = ((pts[-1][0] + pts[0][0]) / 2.0,
                    (pts[-1][1] + pts[0][1]) / 2.0)
        seq = pts
    segs = []
    cur = start_pt
    i = 0
    while i < len(seq):
        x, y, on = seq[i]
        if on:
            segs.append((0, cur[0], cur[1], x, y, 0, 0, 0, 0))
            cur = (x, y)
            i += 1
        else:
            if i + 1 < len(seq) and not seq[i + 1][2]:
                # Hai off-curve lien tiep: on-curve am o giua.
                mx = (x + seq[i + 1][0]) / 2.0
                my = (y + seq[i + 1][1]) / 2.0
                segs.append((1, cur[0], cur[1], x, y, mx, my, 0, 0))
                cur = (mx, my)
                i += 1
            elif i + 1 < len(seq):
                nx, ny = seq[i + 1][0], seq[i + 1][1]
                segs.append((1, cur[0], cur[1], x, y, nx, ny, 0, 0))
                cur = (nx, ny)
                i += 2
            else:
                segs.append((1, cur[0], cur[1], x, y,
                             start_pt[0], start_pt[1], 0, 0))
                cur = start_pt
                i += 1
    # Dong contour ve diem bat dau neu chua kheo.
    if cur != start_pt:
        segs.append((0, cur[0], cur[1], start_pt[0], start_pt[1], 0, 0, 0, 0))
    return segs


def _read_glyf(data, tables, num_glyphs):
    """Doc toan bo glyf/loca: {gid: contours}. Composite de quy."""
    if "glyf" not in tables or "loca" not in tables or "head" not in tables:
        return {}
    glyf_off, _ = tables["glyf"]
    loca_off, _ = tables["loca"]
    head_off, _ = tables["head"]
    loc_fmt = _i16(data, head_off + 50)
    offs = []
    for gid in range(num_glyphs + 1):
        if loc_fmt == 0:
            offs.append(_u16(data, loca_off + 2 * gid) * 2)
        else:
            offs.append(_u32(data, loca_off + 4 * gid))
    cache = {}

    def read_gid(gid, depth):
        if gid in cache:
            return cache[gid]
        if depth > 8 or gid >= num_glyphs:
            return []
        o0, o1 = offs[gid], offs[gid + 1]
        if o1 <= o0:
            cache[gid] = []
            return []
        off = glyf_off + o0
        n_contours = _i16(data, off)
        if n_contours >= 0:
            out = _glyf_simple(data, off)
            cache[gid] = out
            return out
        # Composite.
        out = []
        p = off + 10
        while True:
            flags = _u16(data, p)
            comp = _u16(data, p + 2)
            p += 4
            if flags & 0x0001:
                dx = _i16(data, p)
                dy = _i16(data, p + 2)
                p += 4
            else:
                dx = data[p]
                dy = data[p + 1]
                p += 2
            if flags & 0x0008:
                s = _i16(data, p) / 16384.0
                m = (s, 0.0, 0.0, s)
                p += 2
            elif flags & 0x0040:
                sx = _i16(data, p) / 16384.0
                sy = _i16(data, p + 2) / 16384.0
                m = (sx, 0.0, 0.0, sy)
                p += 4
            elif flags & 0x0080:
                m = tuple(_i16(data, p + 2 * k) / 16384.0 for k in range(4))
                p += 8
            else:
                m = (1.0, 0.0, 0.0, 1.0)
            sub = read_gid(comp, depth + 1)
            for contour in sub:
                newc = []
                for seg in contour:
                    kind = seg[0]
                    pts = []
                    for k in range(4):
                        px, py = seg[1 + 2 * k], seg[2 + 2 * k]
                        nx = m[0] * px + m[2] * py + dx
                        ny = m[1] * px + m[3] * py + dy
                        pts.append((nx, ny))
                    newc.append((kind, pts[0][0], pts[0][1], pts[1][0],
                                 pts[1][1], pts[2][0], pts[2][1],
                                 pts[3][0], pts[3][1]))
                out.append(newc)
            if not (flags & 0x0020):
                break
        cache[gid] = out
        return out

    return {gid: read_gid(gid, 0) for gid in range(num_glyphs)}


# ------------------------------------------------------------------ CFF
def _cff_index(data, off):
    """Doc 1 CFF INDEX tai off: tra (items, off_sau_index)."""
    count = _u16(data, off)
    if count == 0:
        return [], off + 2
    off_size = data[off + 2]
    base = off + 3
    offs = []
    for i in range(count + 1):
        v = 0
        for k in range(off_size):
            v = (v << 8) | data[base + i * off_size + k]
        offs.append(v)
    data_start = base + (count + 1) * off_size
    items = []
    for i in range(count):
        s = data_start + offs[i] - 1
        e = data_start + offs[i + 1] - 1
        items.append(data[s:e])
    return items, data_start + offs[-1] - 1


def _cff_dict_number(data, p):
    """Doc 1 so DICT tai p: tra (gia_tri, p_moi)."""
    b = data[p]
    if 32 <= b <= 246:
        return b - 139, p + 1
    if 247 <= b <= 250:
        return (b - 247) * 256 + data[p + 1] + 108, p + 2
    if 251 <= b <= 254:
        return -(b - 251) * 256 - data[p + 1] - 108, p + 2
    if b == 28:
        return struct.unpack_from(">h", data, p + 1)[0], p + 3
    if b == 29:
        return struct.unpack_from(">i", data, p + 1)[0], p + 5
    if b == 30:
        # So thuc nibble: 0-9 chu so, A='.', B='E', C='E-', D='-', E='-'(?), F=ket thuc.
        out = ""
        p += 1
        done = False
        while not done:
            byte = data[p]
            p += 1
            for nib in (byte >> 4, byte & 0x0F):
                if nib == 0xF:
                    done = True
                    break
                elif nib == 0xA:
                    out += "."
                elif nib == 0xB:
                    out += "E"
                elif nib == 0xC:
                    out += "E-"
                elif nib == 0xD:
                    out += "-"
                elif nib == 0xE:
                    out += "-"
                else:
                    out += str(nib)
        try:
            return float(out), p
        except ValueError:
            return 0.0, p
    if b == 255:
        return struct.unpack_from(">i", data, p + 1)[0] / 65536.0, p + 5
    raise PB3Error("so DICT khong hop le: byte %d" % b)


def _cff_dict(data):
    """Doc 1 DICT: {op: [toan hang]}. Op 2 byte: (12, sub)."""
    out = {}
    p = 0
    stack = []
    while p < len(data):
        b = data[p]
        if b == 12:
            op = (12, data[p + 1])
            p += 2
            out[op] = stack
            stack = []
        elif b <= 21:
            out[b] = stack
            stack = []
            p += 1
        else:
            v, p = _cff_dict_number(data, p)
            stack.append(v)
    return out


def _cs_number(data, p):
    """Doc 1 so charstring tai p: tra (gia_tri, p_moi)."""
    b = data[p]
    if 32 <= b <= 246:
        return b - 139, p + 1
    if 247 <= b <= 250:
        return (b - 247) * 256 + data[p + 1] + 108, p + 2
    if 251 <= b <= 254:
        return -(b - 251) * 256 - data[p + 1] - 108, p + 2
    if b == 28:
        return struct.unpack_from(">h", data, p + 1)[0], p + 3
    if b == 255:
        return struct.unpack_from(">i", data, p + 1)[0] / 65536.0, p + 5
    raise PB3Error("so charstring khong hop le: byte %d" % b)


def _subr_bias(n):
    if n < 1240:
        return 107
    if n < 33900:
        return 1131
    return 32768


class _CSInterp(object):
    """May ao Type 2: chay charstring -> contours (segment kind 0/2)."""

    def __init__(self, local_subrs, global_subrs):
        self.local = local_subrs
        self.glob = global_subrs
        self.contours = []
        self.cur_contour = None
        self.x = 0.0
        self.y = 0.0
        self.start_x = 0.0
        self.start_y = 0.0
        self.width_seen = False
        self.nstems = 0

    def _stem_args(self, stack):
        """Xu ly toan hang stem/hintmask: tach width (neu co) roi dem stem."""
        if not self.width_seen and len(stack) % 2 == 1:
            del stack[0]
            self.width_seen = True
        self.nstems += len(stack) // 2
        del stack[:]

    def _moveto(self, x, y):
        self._close()
        self.x, self.y = x, y
        self.start_x, self.start_y = x, y
        self.cur_contour = []

    def _lineto(self, x, y):
        if self.cur_contour is None:
            self._moveto(self.x, self.y)
        self.cur_contour.append((0, self.x, self.y, x, y, 0, 0, 0, 0))
        self.x, self.y = x, y

    def _curveto(self, x1, y1, x2, y2, x3, y3):
        if self.cur_contour is None:
            self._moveto(self.x, self.y)
        self.cur_contour.append((2, self.x, self.y, x1, y1, x2, y2, x3, y3))
        self.x, self.y = x3, y3

    def _close(self):
        if self.cur_contour:
            if (self.x, self.y) != (self.start_x, self.start_y):
                self.cur_contour.append((0, self.x, self.y, self.start_x,
                                         self.start_y, 0, 0, 0, 0))
            self.contours.append(self.cur_contour)
            self.cur_contour = None

    def _drop_width(self, stack, need):
        if not self.width_seen and len(stack) > need:
            del stack[0]
        self.width_seen = True

    def run(self, prog, depth=0, stack=None):
        if depth > 10:
            raise PB3Error("charstring de quy subr qua sau")
        # Stack TOAN HANG DUNG CHUNG giua charstring va subr (spec Type 2):
        # subr doc toan hang caller da day len truoc callsubr/callgsubr.
        if stack is None:
            stack = []
        p = 0
        n = len(prog)
        while p < n:
            b = prog[p]
            if b == 28 or b == 255 or b >= 32:
                v, p = _cs_number(prog, p)
                stack.append(v)
                continue
            if b == 12:
                op = (12, prog[p + 1])
                p += 2
                if op == (12, 0):  # dotsection: bo qua
                    del stack[:]
                elif op == (12, 6):  # seac
                    raise PB3Error("glyph dung seac — chua ho tro")
                elif op == (12, 7):  # sbw
                    del stack[:]
                elif op == (12, 12):  # div
                    a = stack.pop()
                    bb = stack.pop()
                    stack.append(bb / a)
                elif op == (12, 16):  # callothersubr
                    raise PB3Error("glyph dung callothersubr — chua ho tro")
                elif op == (12, 17):  # pop
                    raise PB3Error("glyph dung pop — chua ho tro")
                elif op == (12, 33):  # setcurrentpoint
                    self.x, self.y = stack[0], stack[1]
                    del stack[:]
                elif op == (12, 34):  # hflex
                    dx1, dx2, dy2, dx3, dx4, dx5, dx6 = stack[:7]
                    self._curveto(self.x + dx1, self.y, self.x + dx1 + dx2,
                                  self.y + dy2, self.x + dx1 + dx2 + dx3,
                                  self.y + dy2)
                    self._curveto(self.x + dx4, self.y + dy2,
                                  self.x + dx4 + dx5, self.y,
                                  self.x + dx4 + dx5 + dx6, self.y)
                    del stack[:]
                elif op == (12, 35):  # flex
                    (dx1, dy1, dx2, dy2, dx3, dy3, dx4, dy4, dx5, dy5,
                     dx6, dy6, fd) = stack[:13]
                    self._curveto(self.x + dx1, self.y + dy1,
                                  self.x + dx1 + dx2, self.y + dy1 + dy2,
                                  self.x + dx1 + dx2 + dx3,
                                  self.y + dy1 + dy2 + dy3)
                    self._curveto(self.x + dx1 + dx2 + dx3 + dx4,
                                  self.y + dy1 + dy2 + dy3 + dy4,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5,
                                  self.y + dy1 + dy2 + dy3 + dy4 + dy5,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5 + dx6,
                                  self.y + dy1 + dy2 + dy3 + dy4 + dy5 + dy6)
                    del stack[:]
                elif op == (12, 36):  # hflex1
                    (dx1, dy1, dx2, dy2, dx3, dx4, dx5, dy5, dx6) = stack[:9]
                    self._curveto(self.x + dx1, self.y + dy1,
                                  self.x + dx1 + dx2, self.y + dy1 + dy2,
                                  self.x + dx1 + dx2 + dx3,
                                  self.y + dy1 + dy2)
                    self._curveto(self.x + dx1 + dx2 + dx3 + dx4,
                                  self.y + dy1 + dy2,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5,
                                  self.y + dy1 + dy2 + dy5,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5 + dx6,
                                  self.y + dy1 + dy2 + dy5)
                    del stack[:]
                elif op == (12, 37):  # flex1
                    (dx1, dy1, dx2, dy2, dx3, dy3, dx4, dy4, dx5, dy5,
                     d6) = stack[:11]
                    dx6, dy6 = d6, -dy1 - dy2 - dy3 - dy4 - dy5
                    self._curveto(self.x + dx1, self.y + dy1,
                                  self.x + dx1 + dx2, self.y + dy1 + dy2,
                                  self.x + dx1 + dx2 + dx3,
                                  self.y + dy1 + dy2 + dy3)
                    self._curveto(self.x + dx1 + dx2 + dx3 + dx4,
                                  self.y + dy1 + dy2 + dy3 + dy4,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5,
                                  self.y + dy1 + dy2 + dy3 + dy4 + dy5,
                                  self.x + dx1 + dx2 + dx3 + dx4 + dx5 + dx6,
                                  self.y + dy1 + dy2 + dy3 + dy4 + dy5 + dy6)
                    del stack[:]
                else:
                    raise PB3Error("operator Type 2 khong ho tro: 12 %d"
                                   % op[1])
                continue
            if b <= 31:
                p += 1
                if b in (1, 3, 18, 23):  # stem hints: dem stem, bo qua hinh
                    self._stem_args(stack)
                elif b == 19 or b == 20:  # hintmask / cntrmask + mask bytes
                    self._stem_args(stack)
                    p += (self.nstems + 7) // 8
                elif b == 4:  # vmoveto
                    self._drop_width(stack, 1)
                    self._moveto(self.x, self.y + stack[0])
                    del stack[:]
                elif b == 21:  # rmoveto
                    self._drop_width(stack, 2)
                    self._moveto(self.x + stack[0], self.y + stack[1])
                    del stack[:]
                elif b == 22:  # hmoveto
                    self._drop_width(stack, 1)
                    self._moveto(self.x + stack[0], self.y)
                    del stack[:]
                elif b == 5:  # rlineto
                    while len(stack) >= 2:
                        self._lineto(self.x + stack[0], self.y + stack[1])
                        del stack[:2]
                    del stack[:]
                elif b == 6:  # hlineto
                    horiz = True
                    while stack:
                        if horiz:
                            self._lineto(self.x + stack[0], self.y)
                        else:
                            self._lineto(self.x, self.y + stack[0])
                        del stack[0]
                        horiz = not horiz
                elif b == 7:  # vlineto
                    vert = True
                    while stack:
                        if vert:
                            self._lineto(self.x, self.y + stack[0])
                        else:
                            self._lineto(self.x + stack[0], self.y)
                        del stack[0]
                        vert = not vert
                elif b == 8:  # rrcurveto
                    while len(stack) >= 6:
                        (dx1, dy1, dx2, dy2, dx3, dy3) = stack[:6]
                        self._curveto(self.x + dx1, self.y + dy1,
                                      self.x + dx1 + dx2, self.y + dy1 + dy2,
                                      self.x + dx1 + dx2 + dx3,
                                      self.y + dy1 + dy2 + dy3)
                        del stack[:6]
                    del stack[:]
                elif b == 24:  # rcurveline
                    while len(stack) > 2:
                        (dx1, dy1, dx2, dy2, dx3, dy3) = stack[:6]
                        self._curveto(self.x + dx1, self.y + dy1,
                                      self.x + dx1 + dx2, self.y + dy1 + dy2,
                                      self.x + dx1 + dx2 + dx3,
                                      self.y + dy1 + dy2 + dy3)
                        del stack[:6]
                    self._lineto(self.x + stack[0], self.y + stack[1])
                    del stack[:]
                elif b == 25:  # rlinecurve
                    while len(stack) > 6:
                        self._lineto(self.x + stack[0], self.y + stack[1])
                        del stack[:2]
                    (dx1, dy1, dx2, dy2, dx3, dy3) = stack[:6]
                    self._curveto(self.x + dx1, self.y + dy1,
                                  self.x + dx1 + dx2, self.y + dy1 + dy2,
                                  self.x + dx1 + dx2 + dx3,
                                  self.y + dy1 + dy2 + dy3)
                    del stack[:]
                elif b == 26:  # vvcurveto
                    if len(stack) % 2 == 1:
                        dx1 = stack[0]
                        rest = stack[1:]
                    else:
                        dx1 = 0
                        rest = stack
                    for i in range(0, len(rest), 4):
                        (dy1, dx2, dy2, dy3) = rest[i:i + 4]
                        self._curveto(self.x + dx1, self.y + dy1,
                                      self.x + dx1 + dx2, self.y + dy1 + dy2,
                                      self.x + dx1 + dx2,
                                      self.y + dy1 + dy2 + dy3)
                        dx1 = 0
                    del stack[:]
                elif b == 27:  # hhcurveto
                    if len(stack) % 2 == 1:
                        dy1 = stack[0]
                        rest = stack[1:]
                    else:
                        dy1 = 0
                        rest = stack
                    for i in range(0, len(rest), 4):
                        (dx1, dx2, dy2, dx3) = rest[i:i + 4]
                        self._curveto(self.x + dx1, self.y + dy1,
                                      self.x + dx1 + dx2, self.y + dy1 + dy2,
                                      self.x + dx1 + dx2 + dx3,
                                      self.y + dy1 + dy2)
                        dy1 = 0
                    del stack[:]
                elif b == 30:  # vhcurveto
                    vert = True
                    i = 0
                    extra = None
                    if len(stack) % 2 == 1:
                        extra = stack[-1]
                        del stack[-1]
                    ngroups = len(stack) // 4
                    for gi in range(ngroups):
                        (d1, d2, d3, d4) = stack[i:i + 4]
                        last = extra if (gi == ngroups - 1 and extra is not None) else 0
                        if vert:
                            # doc: args (dya, dxb, dyb, dxc); extra (dyc)
                            # cong vao y cuoi.
                            self._curveto(self.x, self.y + d1,
                                          self.x + d2, self.y + d1 + d3,
                                          self.x + d2 + d4,
                                          self.y + d1 + d3 + last)
                        else:
                            # ngang: args (dxa, dxb, dyb, dyc); extra (dxc)
                            # cong vao x cuoi (KHONG doi d2/d3 nhu truoc).
                            self._curveto(self.x + d1, self.y,
                                          self.x + d1 + d2, self.y + d3,
                                          self.x + d1 + d2 + last,
                                          self.y + d3 + d4)
                        i += 4
                        vert = not vert
                    del stack[:]
                elif b == 31:  # hvcurveto
                    horiz = True
                    i = 0
                    extra = None
                    if len(stack) % 2 == 1:
                        extra = stack[-1]
                        del stack[-1]
                    ngroups = len(stack) // 4
                    for gi in range(ngroups):
                        (d1, d2, d3, d4) = stack[i:i + 4]
                        last = extra if (gi == ngroups - 1 and extra is not None) else 0
                        if horiz:
                            # ngang: args (dxa, dxb, dyb, dyc); extra (dxc)
                            # cong vao x cuoi (KHONG doi d2/d3 nhu truoc).
                            self._curveto(self.x + d1, self.y,
                                          self.x + d1 + d2, self.y + d3,
                                          self.x + d1 + d2 + last,
                                          self.y + d3 + d4)
                        else:
                            # doc: args (dya, dxb, dyb, dxc); extra (dyc)
                            # cong vao y cuoi.
                            self._curveto(self.x, self.y + d1,
                                          self.x + d2, self.y + d1 + d3,
                                          self.x + d2 + d4,
                                          self.y + d1 + d3 + last)
                        i += 4
                        horiz = not horiz
                    del stack[:]
                elif b == 9:  # closepath
                    self._close()
                    del stack[:]
                elif b == 10:  # callsubr
                    idx = int(stack.pop()) + _subr_bias(len(self.local))
                    self.run(self.local[idx], depth + 1, stack)
                elif b == 29:  # callgsubr
                    idx = int(stack.pop()) + _subr_bias(len(self.glob))
                    self.run(self.glob[idx], depth + 1, stack)
                elif b == 11:  # return
                    return
                elif b == 13:  # hsbw
                    self.width_seen = True
                    del stack[:]
                elif b == 14:  # endchar
                    if len(stack) == 4:
                        raise PB3Error("glyph dung seac — chua ho tro")
                    self._close()
                    return self.contours
                else:
                    raise PB3Error("operator Type 2 khong ho tro: %d" % b)
                continue
            raise PB3Error("byte charstring khong hop le: %d" % b)
        self._close()
        return self.contours


def _read_cff(data, tables, num_glyphs):
    """Doc bang CFF: {gid: contours}. Tra {} neu khong co CFF."""
    if "CFF " not in tables:
        return {}
    cff_off, _ln = tables["CFF "]
    hdr_size = data[cff_off + 2]
    p = cff_off + hdr_size
    _names, p = _cff_index(data, p)
    top_items, p = _cff_index(data, p)
    if not top_items:
        raise PB3Error("CFF khong co Top DICT")
    top = _cff_dict(top_items[0])
    _strings, p = _cff_index(data, p)
    global_subrs, _p = _cff_index(data, p)
    if 17 not in top:
        raise PB3Error("CFF khong co CharStrings")
    cs_off = cff_off + int(top[17][0])
    charstrings, _ = _cff_index(data, cs_off)
    # Charset.
    names = []
    if 15 not in top:
        names = None  # ISOAdobe mac dinh: dung ten chuan theo gid
    else:
        cs_fmt_off = cff_off + int(top[15][0])
        if top[15][0] == 0:
            names = None
        else:
            fmt = data[cs_fmt_off]
            q = cs_fmt_off + 1
            if fmt == 0:
                sids = [_u16(data, q + 2 * i) for i in range(num_glyphs - 1)]
                names = [0] + sids
            elif fmt == 1:
                sids = [0]
                while len(sids) < num_glyphs:
                    sid = _u16(data, q)
                    cnt = data[q + 2]
                    q += 3
                    sids.extend(range(sid, sid + cnt + 1))
                names = sids[:num_glyphs]
            elif fmt == 2:
                sids = [0]
                while len(sids) < num_glyphs:
                    sid = _u16(data, q)
                    cnt = _u16(data, q + 2)
                    q += 4
                    sids.extend(range(sid, sid + cnt + 1))
                names = sids[:num_glyphs]
            else:
                raise PB3Error("charset CFF format %d khong ho tro" % fmt)
    # Private + local subrs.
    local_subrs = []
    if 18 in top:
        priv_size, priv_off = int(top[18][0]), int(top[18][1])
        priv = _cff_dict(data[cff_off + priv_off:
                              cff_off + priv_off + priv_size])
        if 19 in priv:
            sub_off = cff_off + priv_off + int(priv[19][0])
            local_subrs, _ = _cff_index(data, sub_off)
    out = {}
    for gid in range(min(num_glyphs, len(charstrings))):
        interp = _CSInterp(local_subrs, global_subrs)
        try:
            out[gid] = interp.run(charstrings[gid])
        except PB3Error:
            raise
        except (IndexError, struct.error, ValueError) as e:
            raise PB3Error("giai ma charstring gid %d loi: %s" % (gid, e))
    return out


def parse_font(data):
    """Doc font sfnt (TTF glyf hoac OTF CFF) -> Font.

    Nem PB3Error neu khong phai sfnt, thieu cmap/head, hoac gap seac /
    callothersubr (chua ho tro). TTC phai tach face truoc (sdk/font.py).
    """
    if len(data) >= 4 and data[:4] == b"ttcf":
        raise PB3Error("font collection (.ttc): tach face truoc (sdk/font.py)")
    tables, is_cff = _tables(data)
    if "head" not in tables or "maxp" not in tables:
        raise PB3Error("font thieu bang head/maxp")
    head_off, _ = tables["head"]
    upem = _u16(data, head_off + 18)
    if upem == 0:
        raise PB3Error("unitsPerEm = 0")
    maxp_off, _ = tables["maxp"]
    num_glyphs = _u16(data, maxp_off + 4)
    cmap = _read_cmap(data, tables)
    advances = _read_hmtx(data, tables, num_glyphs)
    if is_cff:
        outlines = _read_cff(data, tables, num_glyphs)
    else:
        outlines = _read_glyf(data, tables, num_glyphs)
    return Font(upem, cmap, outlines, advances)


# ------------------------------------------------------------------ flatten
def _flatten_quad(x0, y0, x1, y1, x2, y2, tol, out):
    """Chia quad thanh doan thang (de quy, dung sai `tol` px)."""
    mx01 = (x0 + x1) / 2.0
    my01 = (y0 + y1) / 2.0
    mx12 = (x1 + x2) / 2.0
    my12 = (y1 + y2) / 2.0
    mx = (mx01 + mx12) / 2.0
    my = (my01 + my12) / 2.0
    # Khoang cach duong thang (x0,y0)-(x2,y2) toi diem giua duong cong.
    dx, dy = x2 - x0, y2 - y0
    ll = dx * dx + dy * dy
    if ll == 0:
        d = math.hypot(mx - x0, my - y0)
    else:
        t = max(0.0, min(1.0, ((mx - x0) * dx + (my - y0) * dy) / ll))
        d = math.hypot(mx - (x0 + t * dx), my - (y0 + t * dy))
    if d <= tol:
        out.append((x0, y0, x2, y2))
        return
    _flatten_quad(x0, y0, mx01, my01, mx, my, tol, out)
    _flatten_quad(mx, my, mx12, my12, x2, y2, tol, out)


def _flatten_cubic(x0, y0, x1, y1, x2, y2, x3, y3, tol, out):
    """Chia cubic thanh doan thang (de quy, dung sai `tol` px)."""
    mx01 = (x0 + x1) / 2.0
    my01 = (y0 + y1) / 2.0
    mx12 = (x1 + x2) / 2.0
    my12 = (y1 + y2) / 2.0
    mx23 = (x2 + x3) / 2.0
    my23 = (y2 + y3) / 2.0
    mx012 = (mx01 + mx12) / 2.0
    my012 = (my01 + my12) / 2.0
    mx123 = (mx12 + mx23) / 2.0
    my123 = (my12 + my23) / 2.0
    mx = (mx012 + mx123) / 2.0
    my = (my012 + my123) / 2.0
    dx, dy = x3 - x0, y3 - y0
    ll = dx * dx + dy * dy
    if ll == 0:
        d = max(math.hypot(mx - x0, my - y0),
                math.hypot(mx01 - x0, my01 - y0),
                math.hypot(mx23 - x3, my23 - y3))
    else:
        t = max(0.0, min(1.0, ((mx - x0) * dx + (my - y0) * dy) / ll))
        d = math.hypot(mx - (x0 + t * dx), my - (y0 + t * dy))
    if d <= tol:
        out.append((x0, y0, x3, y3))
        return
    _flatten_cubic(x0, y0, mx01, my01, mx012, my012, mx, my, tol, out)
    _flatten_cubic(mx, my, mx123, my123, mx23, my23, x3, y3, tol, out)


def flatten_contours(contours, scale, tol=0.05):
    """Outline (font units) -> list doan thang (px, y HUONG LEN).

    scale = point_size / upem. tol = dung sai flatten (px).
    """
    out = []
    for contour in contours:
        for seg in contour:
            kind = seg[0]
            if kind == 0:
                out.append((seg[1] * scale, seg[2] * scale,
                            seg[3] * scale, seg[4] * scale))
            elif kind == 1:
                _flatten_quad(seg[1] * scale, seg[2] * scale,
                              seg[3] * scale, seg[4] * scale,
                              seg[5] * scale, seg[6] * scale, tol, out)
            else:
                _flatten_cubic(seg[1] * scale, seg[2] * scale,
                               seg[3] * scale, seg[4] * scale,
                               seg[5] * scale, seg[6] * scale,
                               seg[7] * scale, seg[8] * scale, tol, out)
    if len(out) > MAX_SEGMENTS:
        raise PB3Error("glyph co %d doan thang sau flatten (gioi han %d)"
                       % (len(out), MAX_SEGMENTS))
    return out


def glyph_segments(font, cp, point_size, tol=0.05):
    """Doan thang px (y len) cua 1 codepoint; [] neu font khong co glyph.

    Tra (segments, bbox, advance_px); bbox = (x0, y0, x1, y1) px, y len.
    """
    gid = font.glyph_for(cp)
    if gid is None:
        return [], None, 0.0
    scale = point_size / font.upem
    segs = flatten_contours(font.outline(gid), scale, tol)
    adv = font.advance(gid) * scale
    if not segs:
        return [], None, adv
    xs = [p for s in segs for p in (s[0], s[2])]
    ys = [p for s in segs for p in (s[1], s[3])]
    return segs, (min(xs), min(ys), max(xs), max(ys)), adv


# ------------------------------------------------------------------ SDF
def _pt_seg_dist2(px, py, x0, y0, x1, y1):
    dx = x1 - x0
    dy = y1 - y0
    ll = dx * dx + dy * dy
    if ll == 0:
        qx, qy = x0, y0
    else:
        t = ((px - x0) * dx + (py - y0) * dy) / ll
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        qx = x0 + t * dx
        qy = y0 + t * dy
    ex = px - qx
    ey = py - qy
    return ex * ex + ey * ey


def render_sdf_cell(segments, x0, y0, w, h, spread, center=SDF_CENTER):
    """Raster SDF cho 1 o chu (cell) — tra bytes, hang 0 = TOP cua cell.

    segments: doan thang px, toa do TUYET DOI trong cell (x0,y0 la goc
    trai-duoi cua cell, y HUONG LEN). w/h: kich thuoc cell (px, int).
    spread: padding + 1. Gia tri = clamp(round(center + s*255/(2*spread))).
    s duong ben trong muc. Trong/ngoai: non-zero winding.
    """
    if w <= 0 or h <= 0:
        return b""
    if not segments:
        return bytes([0]) * (w * h)
    # Luoi deu cho tim doan gan nhat: o 8px.
    cell = 8.0
    gx = max(1, int(math.ceil(w / cell)) + 2)
    gy = max(1, int(math.ceil(h / cell)) + 2)
    grid = [[] for _ in range(gx * gy)]
    for si, (sx0, sy0, sx1, sy1) in enumerate(segments):
        ax = int((min(sx0, sx1) - x0) // cell)
        bx = int((max(sx0, sx1) - x0) // cell)
        ay = int((min(sy0, sy1) - y0) // cell)
        by = int((max(sy0, sy1) - y0) // cell)
        for cx in range(max(0, ax), min(gx, bx + 1)):
            for cy in range(max(0, ay), min(gy, by + 1)):
                grid[cy * gx + cx].append(si)
    slope = 255.0 / (2.0 * spread)
    out = bytearray(w * h)
    # Quet hang: hang j (tu duoi) co tam y = y0 + j + 0.5.
    for j in range(h):
        py = y0 + j + 0.5
        # Giao diem scanline (non-zero winding).
        crossings = []
        for (sx0, sy0, sx1, sy1) in segments:
            if (sy0 > py) != (sy1 > py):
                t = (py - sy0) / (sy1 - sy0)
                crossings.append((sx0 + t * (sx1 - sx0),
                                  1 if sy1 > sy0 else -1))
        crossings.sort()
        # Trong/ngoai tai moi pixel: winding != 0.
        inside = [False] * w
        wind = 0
        ci = 0
        ncr = len(crossings)
        for ix in range(w):
            px = x0 + ix + 0.5
            while ci < ncr and crossings[ci][0] <= px:
                wind += crossings[ci][1]
                ci += 1
            inside[ix] = wind != 0
        # Khoang cach toi doan gan nhat: mo rong vong luoi cho toi khi
        # vong tiep theo khong the chua doan gan hon (chinh xac).
        for ix in range(w):
            px = x0 + ix + 0.5
            gcx = int((px - x0) // cell)
            gcy = int((py - y0) // cell)
            best = None
            ring = 0
            while True:
                for ccx in range(max(0, gcx - ring), min(gx, gcx + ring + 1)):
                    for ccy in range(max(0, gcy - ring), min(gy, gcy + ring + 1)):
                        if ring and abs(ccx - gcx) < ring and abs(ccy - gcy) < ring:
                            continue
                        for si in grid[ccy * gx + ccx]:
                            (sx0, sy0, sx1, sy1) = segments[si]
                            d2 = _pt_seg_dist2(px, py, sx0, sy0, sx1, sy1)
                            if best is None or d2 < best:
                                best = d2
                # Vong ke (Chebyshev R+1) co diem gan nhat cach pixel
                # it nhat R*cell — chi dung khi best da nho hon the.
                if best is not None and ring * cell >= math.sqrt(best):
                    break
                ring += 1
                if ring > max(gx, gy):
                    break
            if best is None:  # khong co doan nao: lay tam cell lam xa nhat
                s = -spread - 2.0
            else:
                s = math.sqrt(best)
                if not inside[ix]:
                    s = -s
            v = int(round(center + s * slope))
            if v < 0:
                v = 0
            elif v > 255:
                v = 255
            # Hang 0 = TOP: hang j (tu duoi) -> dong h-1-j.
            out[(h - 1 - j) * w + ix] = v
    return bytes(out)


# ------------------------------------------------------------------ pack/atlas
def pack_cells(cells, atlas_w, atlas_h):
    """Xep o (shelf packing): cells = [(key, w, h)] (w/h int, da gom padding).

    Tra ({key: (x, y_top)}, fitted, unfitted): x/y_top la goc trai-tren
    cua o trong atlas (y tinh tu TOP). Xep theo hang: cao giam dan, day
    hang tu trai sang phai; het cho thi xuong hang moi. Khong vua -> unfitted.
    """
    if atlas_w <= 0 or atlas_h <= 0 or atlas_w > MAX_ATLAS or atlas_h > MAX_ATLAS:
        raise PB3Error("kich thuoc atlas khong hop le: %dx%d" % (atlas_w, atlas_h))
    order = sorted(cells, key=lambda c: (-c[2], -c[1], c[0]))
    placed = {}
    unfitted = []
    x = 0
    y = 0
    row_h = 0
    for key, w, h in order:
        if w > atlas_w or h > atlas_h:
            unfitted.append(key)
            continue
        if x + w > atlas_w:
            y += row_h
            x = 0
            row_h = 0
        if y + h > atlas_h:
            unfitted.append(key)
            continue
        placed[key] = (x, y)
        x += w
        if h > row_h:
            row_h = h
    return placed, len(unfitted) == 0, unfitted


def generate_tmp_atlas(font_data, codepoints, point_size, padding,
                       atlas_w, atlas_h, layout="old"):
    """Tao atlas SDF + bang glyph cho TMP_FontAsset.

    font_data: bytes font (TTF glyf / OTF CFF). codepoints: list int.
    layout: "old" (36 byte/record, id=codepoint, rect y tu TOP) hoac
    "new" (52 byte/record, id=glyph index, rect y tu BOTTOM, metrics int).
    Tra dict: atlas (bytes, hang BOTTOM-UP), atlas_w/h, glyphs (list dict
    co cp, gid, metrics, rect, cell), spread, padding, point_size,
    unfitted (list cp khong xep vua), empty (list cp khong co glyph).
    Glyph khong co outline (space...) co rect 0x0 o goc atlas + advance.
    """
    if padding < 0 or padding > 64:
        raise PB3Error("padding khong hop le: %r" % (padding,))
    if layout not in ("old", "new"):
        raise PB3Error("layout khong hop le: %r" % (layout,))
    font = parse_font(font_data)
    spread = padding + SPREAD_EXTRA
    scale = point_size / font.upem
    # 1. Do outline + tinh o.
    items = []
    empty = []
    for cp in codepoints:
        gid = font.glyph_for(cp)
        if gid is None:
            empty.append(cp)
            continue
        segs, bbox, adv = glyph_segments(font, cp, point_size)
        if bbox is None:
            # Glyph trong (space...): khong co o, chi co advance.
            items.append({"cp": cp, "gid": gid, "segs": [], "bbox": None,
                          "adv": adv, "w": 0, "h": 0})
            continue
        x0, y0, x1, y1 = bbox
        # O = ink + padding moi ben (lam tron len).
        cw = int(math.ceil(x1 - x0)) + 2 * padding
        ch = int(math.ceil(y1 - y0)) + 2 * padding
        items.append({"cp": cp, "gid": gid, "segs": segs, "bbox": bbox,
                      "adv": adv, "w": cw, "h": ch})
    # 2. Xep o (chi glyph co ink).
    cells = [(id(it), it["w"], it["h"]) for it in items if it["bbox"] is not None]
    placed, fitted, unfitted_keys = pack_cells(cells, atlas_w, atlas_h)
    unfitted = []
    by_id = {id(it): it for it in items}
    for key in unfitted_keys:
        unfitted.append(by_id[key]["cp"])
    # 3. Render tung glyph vao atlas (bytes BOTTOM-UP: hang 0 = day).
    atlas = bytearray(atlas_w * atlas_h)
    glyphs = []
    for it in items:
        if it["bbox"] is None:
            glyphs.append({"cp": it["cp"], "gid": it["gid"],
                           "metrics": (0.0, 0.0, 0.0, 0.0, it["adv"]),
                           "rect": (0, 0, 0, 0), "cell": None})
            continue
        if id(it) not in placed:
            continue
        cx, cy_top = placed[id(it)]
        x0, y0, x1, y1 = it["bbox"]
        # Dat outline sao cho ink-min trung goc cell + padding (gi ca phan
        # thap phan cua bearing). He y-len: goc duoi cell = y0c_len.
        cell_h = it["h"]
        y0c_len = atlas_h - (cy_top + cell_h)
        shifted = []
        for (sx0, sy0, sx1, sy1) in it["segs"]:
            tx0 = sx0 - x0 + cx + padding
            ty0 = sy0 - y0 + y0c_len + padding
            tx1 = sx1 - x0 + cx + padding
            ty1 = sy1 - y0 + y0c_len + padding
            shifted.append((tx0, ty0, tx1, ty1))
        cell_bytes = render_sdf_cell(shifted, cx, y0c_len, it["w"], cell_h,
                                     spread)
        # Chep cell (hang 0 = TOP) vao atlas (hang 0 = BOTTOM).
        for row in range(cell_h):
            src_off = row * it["w"]
            # Hang row (tu TOP cua cell) -> hang atlas tu duoi:
            # y_td cua hang = cy_top + row -> hang bottom-up = atlas_h-1-(cy_top+row)
            dst_row = atlas_h - 1 - (cy_top + row)
            dst_off = dst_row * atlas_w + cx
            atlas[dst_off:dst_off + it["w"]] = cell_bytes[src_off:src_off + it["w"]]
        # Metrics + rect (TMP): rect = ink bbox (lam tron ra ngoai cho
        # giong TMP: x/y nguyen, w/h = ink that).
        ink_w = x1 - x0
        ink_h = y1 - y0
        bx = x0  # bearingX (px, y len)
        by = y1  # bearingY
        metrics = (ink_w, ink_h, bx, by, it["adv"])
        if layout == "old":
            rect = (cx + padding, cy_top + padding, ink_w, ink_h)
        else:
            # y tu BOTTOM: khoang cach tu day atlas toi day rect.
            rect_bottom_td = cy_top + padding + ink_h
            rect = (cx + padding, atlas_h - rect_bottom_td, ink_w, ink_h)
        glyphs.append({"cp": it["cp"], "gid": it["gid"], "metrics": metrics,
                       "rect": rect, "cell": (cx, cy_top, it["w"], cell_h)})
    # Sap glyph theo cp de bang on dinh.
    glyphs.sort(key=lambda g: g["cp"])
    return {"atlas": bytes(atlas), "atlas_w": atlas_w, "atlas_h": atlas_h,
            "glyphs": glyphs, "spread": spread, "padding": padding,
            "point_size": point_size, "layout": layout,
            "unfitted": unfitted, "empty": empty}


def measure_atlas_error(font_data, records, atlas_bytes, atlas_w, atlas_h,
                        point_size, padding, layout="old"):
    """Do sai so giua SDF tu tao (tu font_data) va atlas that.

    records: list dict tu tmp_font.read_glyph_records (layout old: id la
    codepoint, co x/y/width/height; layout new: id la glyph index, co
    rect (x, y tu BOTTOM, w, h)). atlas_bytes: bytes atlas that
    (BOTTOM-UP, 1 byte/px). point_size/padding: tham so TMP goc.
    So sanh tren cua so [rect mo rong padding], loai pixel trong 8px cua
    rect ke (SDF glyph ke lan sang). Tra dict: rows (moi glyph: id, cp,
    n, mae, max, note), glyphs_ok, median_mae, mean_mae, pct_lt2,
    pct_lt3, pass. pass = median MAE < 3.0 (nguong dat: ~0.15px lech
    bien, khong thay bang mat). Glyph lech do font nhung khac version
    voi font build atlas that se co MAE cao — do la du lieu that, khong
    phai loi renderer (xem exp/verify_sdf_regen.py).
    """
    if layout not in ("old", "new"):
        raise PB3Error("layout khong hop le: %r" % (layout,))
    if len(atlas_bytes) != atlas_w * atlas_h:
        raise PB3Error("atlas size mismatch: %d != %d*%d"
                       % (len(atlas_bytes), atlas_w, atlas_h))
    font = parse_font(font_data)
    gid2cp = {}
    for cp, gid in font.cmap.items():
        gid2cp.setdefault(gid, cp)
    spread = padding + SPREAD_EXTRA
    # Rect ve toa do top-down (x, y_top, w, h).
    rects_td = []
    for r in records:
        if layout == "new":
            rx, ry_b, rw, rh = r["rect"]
            rects_td.append((rx, atlas_h - ry_b - rh, rw, rh))
        else:
            rects_td.append((r["x"], r["y"], r["width"], r["height"]))
    rows = []
    for i, r in enumerate(records):
        rx, ry, rw, rh = rects_td[i]
        if rw <= 0 or rh <= 0:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "placeholder"})
            continue
        if layout == "new":
            cp = gid2cp.get(r["id"])
            if cp is None:
                rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                             "note": "no-cp"})
                continue
        else:
            cp = r["id"]
        segs, bbox, _adv = glyph_segments(font, cp, point_size)
        if not segs or bbox is None:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "no-segs"})
            continue
        bx0, _by0, _bx1, by1 = bbox
        # Doi doan sang toa do atlas y-len: ink-min -> goc tren-trai rect.
        segs_a = []
        for (x0, y0, x1, y1) in segs:
            segs_a.append((x0 - bx0 + rx, atlas_h - ry - by1 + y0,
                           x1 - bx0 + rx, atlas_h - ry - by1 + y1))
        wx0 = max(0, int(math.floor(rx)) - padding)
        wy0 = max(0, int(math.floor(ry)) - padding)
        wx1 = min(atlas_w, int(math.ceil(rx + rw)) + padding)
        wy1 = min(atlas_h, int(math.ceil(ry + rh)) + padding)
        ww, wh = wx1 - wx0, wy1 - wy0
        if ww <= 0 or wh <= 0:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "empty-window"})
            continue
        cell = render_sdf_cell(segs_a, wx0, atlas_h - wy0 - wh, ww, wh,
                               spread)
        errs = []
        for j in range(wh):
            cy = wy0 + j
            for ix in range(ww):
                cx = wx0 + ix
                skip = False
                for k, (ox, oy, ow, oh) in enumerate(rects_td):
                    if k == i:
                        continue
                    if (ox - 8 <= cx + 0.5 <= ox + ow + 8
                            and oy - 8 <= cy + 0.5 <= oy + oh + 8):
                        skip = True
                        break
                if skip:
                    continue
                # Atlas raw BOTTOM-UP: cy top-down -> hang atlas_h-1-cy.
                real = atlas_bytes[(atlas_h - 1 - cy) * atlas_w + cx]
                pred = cell[j * ww + ix]
                errs.append(abs(real - pred))
        if not errs:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "no-pixels"})
            continue
        rows.append({"id": r["id"], "cp": cp, "n": len(errs),
                     "mae": sum(errs) / len(errs), "max": max(errs),
                     "note": "ok"})
    ok = [r for r in rows if r["note"] == "ok"]
    maes = sorted(r["mae"] for r in ok)
    n = len(maes)
    median = maes[n // 2] if n % 2 else (
        (maes[n // 2 - 1] + maes[n // 2]) / 2.0 if n else 0.0)
    return {"rows": rows, "glyphs_ok": len(ok),
            "median_mae": median if n else 0.0,
            "mean_mae": (sum(maes) / n) if n else 0.0,
            "pct_lt2": (100.0 * sum(1 for m in maes if m < 2) / n
                        if n else 0.0),
            "pct_lt3": (100.0 * sum(1 for m in maes if m < 3) / n
                        if n else 0.0),
            "worst": sorted(ok, key=lambda r: -r["mae"])[:5],
            "pass": bool(maes) and median < 3.0}


# ------------------------------------------------------------------ bang glyph
def encode_glyph_table_old(glyphs):
    """Bang glyph layout CU (36 byte/record): count + id + 8 float.

    id = codepoint; x/y/w/h = rect (y tu TOP); xOffset/yOffset/xAdvance =
    metrics; scale = 1.0.
    """
    out = bytearray()
    out += struct.pack("<i", len(glyphs))
    for g in glyphs:
        w, h, bx, by, adv = g["metrics"]
        x, y, rw, rh = g["rect"]
        out += struct.pack("<i8f", g["cp"], float(x), float(y), float(rw),
                           float(rh), float(bx), float(by), float(adv), 1.0)
    return bytes(out)


def encode_glyph_table_new(glyphs):
    """Bang glyph layout MOI (52 byte/record): count + 13 truong.

    id = glyph index; metrics 5 float (lam tron int, giong TMP moi);
    rect 4 int (x, y tu BOTTOM, w, h); scale 1.0; atlasIndex 0; trailing 0.
    """
    out = bytearray()
    out += struct.pack("<i", len(glyphs))
    for g in glyphs:
        w, h, bx, by, adv = g["metrics"]
        x, y, rw, rh = g["rect"]
        out += struct.pack("<I5f4ifii", g["gid"], float(round(w)),
                           float(round(h)), float(round(bx)), float(round(by)),
                           float(round(adv)), int(x), int(round(y)),
                           int(round(rw)), int(round(rh)), 1.0, 0, 0)
    return bytes(out)


def rebuild_tmp_object(orig, endian, new_table, new_count, table_offset,
                       table_stride, table_count, faceinfo_count_offset=None):
    """Chen bang glyph moi vao object TMP_FontAsset cu.

    orig: bytes object goc. new_table: bytes bang moi (da gom count).
    table_offset: vi tri int32 count cua bang cu; table_stride: 36/52;
    table_count: so record cu. faceinfo_count_offset: vi tri int32
    CharacterCount trong FaceInfo (layout cu) — None thi khong sua.
    Tra bytes object moi (dai hon/neu bang moi dai hon).
    """
    if table_offset < 0 or table_offset + 4 > len(orig):
        raise PB3Error("table_offset ngoai pham vi object")
    old_end = table_offset + 4 + table_stride * table_count
    if old_end > len(orig):
        raise PB3Error("bang glyph cu vuot qua object")
    out = bytearray()
    out += orig[:table_offset]
    out += new_table
    out += orig[old_end:]
    if faceinfo_count_offset is not None:
        if faceinfo_count_offset + 4 > len(out):
            raise PB3Error("faceinfo_count_offset ngoai pham vi object")
        struct.pack_into(endian + "i", out, faceinfo_count_offset, new_count)
    return bytes(out)




