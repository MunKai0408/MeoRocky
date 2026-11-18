"""Test tai tao SDF TextMeshPro (sdk/sdf.py) + bang glyph 2 layout.

Fixture TTF TU DUNG (chi stdlib): 3 glyph — .notdef rong, hinh chu nhat
('A'), hinh tam giac ('B') — du de kiem parse/glyph/SDF/atlas ma khong
can font that. Test CFF hvcurveto chay truc tiep _CSInterp tren
charstring tu viet (khong can file .otf).
"""
from __future__ import annotations

import math
import struct
import unittest

from sdk import sdf as S
from sdk import tmp_font as tf
from sdk import tmp_sdf as ts
from core.errors import PB3Error
from tests.helpers import TmpCase


# ------------------------------------------------------------------ TTF fixture
def _csum(data: bytes) -> int:
    buf = data + b"\x00" * ((-len(data)) % 4)
    total = 0
    for i in range(0, len(buf), 4):
        total = (total + struct.unpack_from(">I", buf, i)[0]) & 0xFFFFFFFF
    return total


def _simple_glyph(points):
    """1 contour polygon, tat ca diem on-curve. Tra bytes glyf."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    flags = []
    x_bytes = bytearray()
    y_bytes = bytearray()
    px = py = 0
    for (x, y) in points:
        dx, dy = x - px, y - py
        f = 0x01
        if dx == 0:
            f |= 0x10
        elif -255 <= dx <= 255:
            f |= 0x02
            if dx > 0:
                f |= 0x10
            x_bytes.append(abs(dx))
        else:
            x_bytes += struct.pack(">h", dx)
        if dy == 0:
            f |= 0x20
        elif -255 <= dy <= 255:
            f |= 0x04
            if dy > 0:
                f |= 0x20
            y_bytes.append(abs(dy))
        else:
            y_bytes += struct.pack(">h", dy)
        flags.append(f)
        px, py = x, y
    out = struct.pack(">hhhhH", 1, min(xs), min(ys), max(xs), max(ys))
    out += struct.pack(">H", len(points) - 1)
    out += struct.pack(">H", 0)
    out += bytes(flags)
    out += bytes(x_bytes)
    out += bytes(y_bytes)
    if len(out) % 2:
        out += b"\x00"
    return out


def _cmap4(mapping):
    """cmap format 4 cho mapping cp->gid (chi delta segment + ket thuc)."""
    items = sorted((cp, g) for cp, g in mapping.items() if g != 0)
    runs = []
    for cp, g in items:
        if runs and runs[-1][-1][0] == cp - 1:
            runs[-1].append((cp, g))
        else:
            runs.append([(cp, g)])
    segs = []
    for run in runs:
        deltas = {(g - cp) & 0xFFFF for cp, g in run}
        if len(deltas) == 1:
            segs.append((run[0][0], run[-1][0], deltas.pop(), False))
        else:
            segs.append((run[0][0], run[-1][0], 0, True))
    segs.append((0xFFFF, 0xFFFF, 1, False))
    n = len(segs)
    n_range = sum(e - s + 1 for s, e, _d, rm in segs if rm)
    length = 16 + 8 * n + 2 * n_range
    sr = 2 * (2 ** int(math.floor(math.log2(n))))
    es = int(math.log2(sr // 2))
    rs = 2 * n - sr
    out = bytearray()
    out += struct.pack(">HHHHHHH", 4, length, 0, n * 2, sr, es, rs)
    for _s, e, _d, _r in segs:
        out += struct.pack(">H", e)
    out += struct.pack(">H", 0)
    for s, _e, _d, _r in segs:
        out += struct.pack(">H", s)
    for _s, _e, d, _r in segs:
        out += struct.pack(">h", d - 0x10000 if d > 0x7FFF else d)
    seen = 0
    for i, (_s, _e, _d, rm) in enumerate(segs):
        if not rm:
            out += struct.pack(">H", 0)
        else:
            out += struct.pack(">H", 2 * (n - i + seen))
            seen += _e - _s + 1
    # glyphIdArray cho range segment (neu co)
    for s, e, _d, rm in segs:
        if rm:
            for cp in range(s, e + 1):
                out += struct.pack(">H", mapping[cp])
    return bytes(out)


def _build_ttf():
    """TTF toi thieu: upem 1000; gid0 rong, gid1 chu nhat (A=65),
    gid2 tam giac (B=66)."""
    rect = _simple_glyph([(0, 0), (100, 0), (100, 150), (0, 150)])
    tri = _simple_glyph([(0, 0), (120, 0), (60, 180)])
    glyf = rect + tri
    loca = struct.pack(">HHHH", 0, 0, len(rect) // 2,
                       (len(rect) + len(tri)) // 2)
    head = bytearray(54)
    struct.pack_into(">I", head, 0, 0x00010000)
    struct.pack_into(">I", head, 12, 0x5F0F3CF5)
    struct.pack_into(">H", head, 18, 1000)
    struct.pack_into(">h", head, 50, 0)  # loca short
    maxp = struct.pack(">IH", 0x00010000, 3) + bytes(26)
    hhea = bytearray(36)
    struct.pack_into(">H", hhea, 34, 3)
    hmtx = struct.pack(">HhHhHh", 600, 0, 600, 0, 600, 0)
    cmap_sub = _cmap4({65: 1, 66: 2})
    # Header cmap: version 0, 1 subtable (platform 3/encoding 1, offset 12).
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + cmap_sub
    tables = {"head": bytes(head), "maxp": maxp, "hhea": bytes(hhea),
              "hmtx": hmtx, "cmap": cmap, "loca": loca, "glyf": glyf}
    tags = sorted(tables)
    n = len(tags)
    sr = 16 * (2 ** int(math.floor(math.log2(n))))
    es = int(math.log2(sr // 16))
    rs = 16 * n - sr
    out = struct.pack(">IHHHH", 0x00010000, n, sr, es, rs)
    off = 12 + 16 * n
    body = b""
    for tag in tags:
        data = tables[tag]
        out += struct.pack(">4sIII", tag.encode(), _csum(data), off,
                           len(data))
        padded = data + b"\x00" * ((-len(data)) % 4)
        body += padded
        off += len(padded)
    return out + body


def _bruteforce_cell(segs, x0, y0, w, h, spread, center=126.5):
    """SDF chinh xac kieu brute-force (khong luoi) de doi chieu renderer."""
    out = bytearray(w * h)
    for j in range(h):  # j tu duoi
        py = y0 + j + 0.5
        crossings = []
        for (sx0, sy0, sx1, sy1) in segs:
            if (sy0 > py) != (sy1 > py):
                t = (py - sy0) / (sy1 - sy0)
                crossings.append((sx0 + t * (sx1 - sx0),
                                  1 if sy1 > sy0 else -1))
        crossings.sort()
        for ix in range(w):
            px = x0 + ix + 0.5
            wind = 0
            for (cx, sgn) in crossings:
                if cx <= px:
                    wind += sgn
                else:
                    break
            best = None
            for (sx0, sy0, sx1, sy1) in segs:
                d2 = S._pt_seg_dist2(px, py, sx0, sy0, sx1, sy1)
                if best is None or d2 < best:
                    best = d2
            s = math.sqrt(best) if best is not None else -spread - 2.0
            if wind == 0:
                s = -s
            v = int(round(center + s * 255.0 / (2.0 * spread)))
            out[(h - 1 - j) * w + ix] = max(0, min(255, v))
    return bytes(out)


class TestParseFont(TmpCase):
    def test_rect_ttf(self):
        font = S.parse_font(_build_ttf())
        self.assertEqual(font.upem, 1000)
        self.assertEqual(font.glyph_for(65), 1)
        self.assertEqual(font.glyph_for(66), 2)
        self.assertIsNone(font.glyph_for(67))
        self.assertEqual(font.advance(1), 600)

    def test_glyph_segments_rect(self):
        font = S.parse_font(_build_ttf())
        # 1000pt / upem 1000 -> scale 1.0: bbox = toa do font goc.
        segs, bbox, adv = S.glyph_segments(font, 65, 1000.0)
        self.assertEqual(len(segs), 4)
        self.assertEqual(bbox, (0.0, 0.0, 100.0, 150.0))
        self.assertEqual(adv, 600.0)
        segs2, bbox2, _ = S.glyph_segments(font, 66, 1000.0)
        self.assertEqual(bbox2, (0.0, 0.0, 120.0, 180.0))
        self.assertTrue(len(segs2) >= 3)

    def test_empty_glyph(self):
        font = S.parse_font(_build_ttf())
        segs, bbox, adv = S.glyph_segments(font, 32, 100.0)
        self.assertEqual(segs, [])
        self.assertIsNone(bbox)


class TestRenderSdf(TmpCase):
    def test_cell_matches_bruteforce(self):
        font = S.parse_font(_build_ttf())
        for cp in (65, 66):
            segs, bbox, _ = S.glyph_segments(font, cp, 100.0)
            bx0, by0, bx1, by1 = bbox
            pad = 5
            cw = int(math.ceil(bx1 - bx0)) + 2 * pad
            ch = int(math.ceil(by1 - by0)) + 2 * pad
            shifted = [(x - bx0 + pad, y - by0 + pad,
                        x1 - bx0 + pad, y1 - by0 + pad)
                       for (x, y, x1, y1) in segs]
            cell = S.render_sdf_cell(shifted, 0, 0, cw, ch, 6)
            bf = _bruteforce_cell(shifted, 0, 0, cw, ch, 6)
            self.assertEqual(cell, bf, "cp %d: renderer khac brute-force"
                             % cp)

    def test_cell_values(self):
        # Hinh chu nhat 100x150 o 100pt, pad 5: tam trong muc gan max,
        # goc xa ngoai muc = 0, bien ~126.5.
        font = S.parse_font(_build_ttf())
        segs, bbox, _ = S.glyph_segments(font, 65, 100.0)
        bx0, by0, bx1, by1 = bbox
        pad = 5
        cw = int(math.ceil(bx1 - bx0)) + 2 * pad
        ch = int(math.ceil(by1 - by0)) + 2 * pad
        shifted = [(x - bx0 + pad, y - by0 + pad,
                    x1 - bx0 + pad, y1 - by0 + pad)
                   for (x, y, x1, y1) in segs]
        cell = S.render_sdf_cell(shifted, 0, 0, cw, ch, 6)
        # tam hinh (row tu TOP): y ~ ch//2
        cx, cy = cw // 2, ch // 2
        self.assertGreater(cell[cy * cw + cx], 200)
        # goc (0,0) xa muc > spread -> 0
        self.assertEqual(cell[0], 0)
        self.assertEqual(cell[(ch - 1) * cw + cw - 1], 0)
        # pixel ngay trong bien trai (x=pad) gan 126.5
        mid_row = ch // 2
        self.assertTrue(110 <= cell[mid_row * cw + pad] <= 145)

    def test_empty_cell_is_zero(self):
        self.assertEqual(S.render_sdf_cell([], 0, 0, 4, 3, 6),
                         bytes(12))


class TestAtlas(TmpCase):
    def test_generate_old_layout(self):
        res = S.generate_tmp_atlas(_build_ttf(), [65, 66], 100.0, 5,
                                   256, 256, layout="old")
        self.assertEqual(res["atlas_w"], 256)
        self.assertEqual(len(res["atlas"]), 256 * 256)
        self.assertEqual(res["unfitted"], [])
        self.assertEqual(res["empty"], [])
        self.assertEqual(res["spread"], 6)
        self.assertEqual(len(res["glyphs"]), 2)
        for g in res["glyphs"]:
            self.assertEqual(len(g["rect"]), 4)
            self.assertIsNotNone(g["cell"])
        # atlas co pixel muc (>126) va pixel nen (0)
        self.assertTrue(any(v > 126 for v in res["atlas"]))
        self.assertTrue(any(v == 0 for v in res["atlas"]))
        # bang cu encode/decode vong tron qua tmp_font
        table = S.encode_glyph_table_old(res["glyphs"])
        recs = tf.read_glyph_records(table, 0, len(res["glyphs"]), 36, "<")
        self.assertEqual([r["id"] for r in recs], [65, 66])
        self.assertEqual(recs[0]["scale"], 1.0)

    def test_generate_new_layout(self):
        res = S.generate_tmp_atlas(_build_ttf(), [65, 66], 100.0, 5,
                                   256, 256, layout="new")
        self.assertEqual(res["layout"], "new")
        self.assertEqual(res["unfitted"], [])
        table = S.encode_glyph_table_new(res["glyphs"])
        recs = tf.read_glyph_records(table, 0, len(res["glyphs"]), 52, "<")
        # id layout moi = glyph index
        self.assertEqual([r["id"] for r in recs], [1, 2])
        self.assertEqual([r["atlas_index"] for r in recs], [0, 0])
        self.assertEqual([r["trailing"] for r in recs], [0, 0])
        self.assertEqual(recs[0]["scale"], 1.0)
        # rect y tu BOTTOM: rect nam trong atlas
        for r in recs:
            x, y, w, h = r["rect"]
            self.assertTrue(0 <= x and 0 <= y)
            self.assertTrue(x + w <= 256 and y + h <= 256)

    def test_space_like_glyph_empty(self):
        res = S.generate_tmp_atlas(_build_ttf(), [32, 65], 100.0, 5,
                                   256, 256, layout="old")
        # cp 32 khong co trong cmap -> empty; chi A co glyph
        self.assertEqual(res["empty"], [32])
        self.assertEqual(len(res["glyphs"]), 1)

    def test_pack_fail_closed(self):
        placed, fitted, unfitted = S.pack_cells(
            [("a", 200, 200), ("b", 200, 200)], 256, 256)
        self.assertFalse(fitted)
        self.assertEqual(sorted(unfitted), ["b"])
        self.assertIn("a", placed)

    def test_generate_unfitted_fail_closed(self):
        res = S.generate_tmp_atlas(_build_ttf(), [65, 66], 100.0, 5,
                                   16, 16, layout="old")
        # atlas 16x16 khong du cho cell 20x25 / 22x28 (100pt)
        self.assertTrue(res["unfitted"])
        self.assertEqual(len(res["glyphs"]), 0)

    def test_measure_atlas_error_self_consistent(self):
        # Atlas tu tao bang chinh font fixture: do lai phai MAE nho
        # (chi lech do rect lam tron) — kiem measure_atlas_error chay dung.
        font_data = _build_ttf()
        res = S.generate_tmp_atlas(font_data, [65, 66], 100.0, 5,
                                   256, 256, layout="old")
        table = S.encode_glyph_table_old(res["glyphs"])
        recs = tf.read_glyph_records(table, 0, len(res["glyphs"]), 36, "<")
        m = S.measure_atlas_error(font_data, recs, res["atlas"], 256, 256,
                                  100.0, 5, layout="old")
        self.assertEqual(m["glyphs_ok"], 2)
        self.assertLess(m["median_mae"], 3.0)
        self.assertTrue(m["pass"])

    def test_measure_atlas_error_bad_layout(self):
        with self.assertRaises(PB3Error):
            S.measure_atlas_error(_build_ttf(), [], bytes(256 * 256),
                                  256, 256, 100.0, 5, layout="x")

    def test_bad_layout_rejected(self):
        with self.assertRaises(PB3Error):
            S.generate_tmp_atlas(_build_ttf(), [65], 100.0, 5, 256, 256,
                                 layout="x")
        with self.assertRaises(PB3Error):
            S.generate_tmp_atlas(_build_ttf(), [65], 100.0, 99, 256, 256)


class TestRebuild(TmpCase):
    def test_rebuild_splice_old(self):
        # object gia: prefix + bang cu (2 record 36B) + suffix
        prefix = b"PRE" + bytes(5)
        old_table = struct.pack("<i", 2)
        for cp in (65, 66):
            old_table += struct.pack("<i8f", cp, 1.0, 2.0, 3.0, 4.0,
                                     0.0, 0.0, 5.0, 1.0)
        suffix = b"POST" + bytes(7)
        orig = prefix + old_table + suffix
        table_off = len(prefix)
        glyphs = [{"cp": 65, "gid": 1,
                   "metrics": (10.0, 20.0, 1.0, 2.0, 30.0),
                   "rect": (5, 6, 10, 20), "cell": None},
                  {"cp": 66, "gid": 2,
                   "metrics": (11.0, 21.0, 1.0, 2.0, 31.0),
                   "rect": (7, 8, 11, 21), "cell": None}]
        new_table = S.encode_glyph_table_old(glyphs)
        out = S.rebuild_tmp_object(orig, "<", new_table, 2, table_off, 36,
                                   2)
        self.assertTrue(out.startswith(prefix))
        self.assertTrue(out.endswith(suffix))
        recs = tf.read_glyph_records(out, table_off, 2, 36, "<")
        self.assertEqual([r["id"] for r in recs], [65, 66])
        self.assertAlmostEqual(recs[0]["x_advance"], 30.0)

    def test_rebuild_bad_offset(self):
        with self.assertRaises(PB3Error):
            S.rebuild_tmp_object(b"xx", "<", b"yy", 1, 99, 36, 1)


class TestCffHvCurveto(TmpCase):
    """Hoi quy bug hv/vhcurveto: nhanh ngang doi arg 2/3 (tim 02:30 +07).

    Charstring: rmoveto(100,200) + hvcurveto(-28,-28,0,-1,-28) + endchar.
    Theo spec (fontTools hcurveto): args ngang (dxa,dxb,dyb,dyc) + extra
    dxc -> c1=(72,200), c2=(44,200), end=(16,199).
    """

    def _run(self, prog):
        interp = S._CSInterp([], [])
        return interp.run(bytes(prog))

    @staticmethod
    def _num(v):
        if 32 <= v + 139 <= 246:
            return bytes([v + 139])
        if 0 <= v - 108 < 256:
            return bytes([247, v - 108])
        if 0 <= -v - 108 < 256:
            return bytes([251, -v - 108])
        return bytes([28]) + struct.pack(">h", v)

    def test_hvcurveto_horizontal(self):
        prog = b""
        prog += self._num(100) + self._num(200) + bytes([21])  # rmoveto
        for v in (-28, -28, 0, -1, -28):
            prog += self._num(v)
        prog += bytes([31, 14])  # hvcurveto, endchar
        contours = self._run(prog)
        self.assertEqual(len(contours), 1)
        seg = contours[0][0]
        self.assertEqual(seg[0], 2)  # cubic
        self.assertEqual((seg[1], seg[2]), (100.0, 200.0))
        self.assertEqual((seg[3], seg[4]), (72.0, 200.0))
        self.assertEqual((seg[5], seg[6]), (44.0, 200.0))
        self.assertEqual((seg[7], seg[8]), (16.0, 199.0))

    def test_vhcurveto_vertical(self):
        prog = b""
        prog += self._num(100) + self._num(200) + bytes([21])  # rmoveto
        for v in (-28, -28, 0, -1, -28):
            prog += self._num(v)
        prog += bytes([30, 14])  # vhcurveto, endchar
        contours = self._run(prog)
        seg = contours[0][0]
        # doc: args (dya,dxb,dyb,dxc) + extra dyc
        # c1=(100,172), c2=(72,172), end=(71,144)
        self.assertEqual((seg[3], seg[4]), (100.0, 172.0))
        self.assertEqual((seg[5], seg[6]), (72.0, 172.0))
        self.assertEqual((seg[7], seg[8]), (71.0, 144.0))


class TestParseTexture2d(TmpCase):
    """Hoi quy parse Texture2D 2 bo cuc: Unity 2022 (Width o off+8) va
    Unity 6000 (9K: Width o off+4, khong co 2 bool)."""

    @staticmethod
    def _astr(s):
        n = len(s)
        return struct.pack("<i", n) + s + b"\x00" * ((4 - n % 4) % 4)

    def _tex2d(self, name, w, h, complete, layout="2022", stream=None):
        b = self._astr(name.encode())
        if layout == "2022":
            b += struct.pack("<i", 4)  # ForcedFallbackFormat
            b += b"\x00\x00\x00\x00"  # 2 bool + align
        else:  # 6000: chi 4 byte truoc Width
            b += struct.pack("<i", 1)  # ForcedFallbackFormat
        b += struct.pack("<iiiii", w, h, complete, 0, 1)
        b += struct.pack("<i", 1)  # IsReadable
        b += b"\x00" * 32
        if stream is not None:
            off, size, path = stream
            b += struct.pack("<Q", off) + struct.pack("<I", size)
            b += self._astr(path.encode())
        return b

    def test_layout_2022(self):
        raw = self._tex2d("COURBD SDF Atlas", 512, 512, 262144,
                           layout="2022",
                           stream=(18319776, 262144,
                                   "resources.assets.resS"))
        info = ts.parse_texture2d(raw, "<")
        self.assertIsNotNone(info)
        self.assertEqual(info["width"], 512)
        self.assertEqual(info["height"], 512)
        self.assertEqual(info["complete"], 262144)
        self.assertEqual(info["format"], 0)
        self.assertIsNotNone(info["stream"])
        self.assertEqual(info["stream"]["size"], 262144)
        self.assertEqual(info["stream"]["offset"], 18319776)

    def test_layout_6000(self):
        # 9K: Width o off+4; doc o off+8 se ra h=1048576, complete=0
        # (khong hop le) nen phai roi ve off+4.
        raw = self._tex2d("LiberationSans SDF Atlas", 1024, 1024, 1048576,
                           layout="6000",
                           stream=(60425808, 1048576,
                                   "resources.assets.resS"))
        info = ts.parse_texture2d(raw, "<")
        self.assertIsNotNone(info)
        self.assertEqual(info["width"], 1024)
        self.assertEqual(info["height"], 1024)
        self.assertEqual(info["complete"], 1048576)
        self.assertIsNotNone(info["stream"])
        self.assertEqual(info["stream"]["size"], 1048576)

    def test_garbage_returns_none(self):
        self.assertIsNone(ts.parse_texture2d(b"\x00" * 64, "<"))
        self.assertIsNone(ts.parse_texture2d(b"ngan", "<"))


class TestIsSdfAtlas(TmpCase):
    """Nhan dang atlas SDF vs bitmap: SDF co nhieu pixel trung gian."""

    def test_sdf_like(self):
        # Gia lap SDF: nhieu gia tri trung gian (gradient muot).
        atlas = bytes([0] * 1000 + [128] * 3000 + [200] * 2000 + [255] * 500)
        self.assertTrue(S.is_sdf_atlas(atlas))

    def test_bitmap_like(self):
        # Gia lap bitmap: hau het 0/255, chi 5% trung gian (ContinuumBold).
        atlas = bytes([0] * 6000 + [255] * 3500 + [128] * 500)
        self.assertFalse(S.is_sdf_atlas(atlas))

    def test_empty(self):
        self.assertFalse(S.is_sdf_atlas(b""))


if __name__ == "__main__":
    unittest.main()
