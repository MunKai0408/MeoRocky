"""Test gop font CFF/.otf (sdk/cff.py + noi vao sdk/font.merge_glyphs).

- Font tong hop: build_otf() dung font CFF that (co bang CFF doc duoc),
  build_ttf(outlines=...) dung donor quadratic that.
- Font that: trich Font object nhung tu IPA PB3/Tom (skip neu thieu IPA),
  gop tieng Viet tu BeVietnamPro (giai ma .b64 trong repo).
"""
from __future__ import annotations

import base64
import os
import struct
import unittest
import zipfile
from pathlib import Path

from core.errors import PB3Error
from sdk import cff
from sdk import font as fs
from sdk import unity as uni
from tests.fixtures.font import build_fixtures as fx
from tests.helpers import TmpCase
from tests.tiering import slow

ROOT = Path(__file__).resolve().parents[2]
PB3_IPA = Path(os.environ.get(
    "PB3_IPA", Path.home() / "workspace" / "test-ipas" / "pb3-v1.1.7.ipa"))
TOM_IPA = Path(os.environ.get(
    "TOM_IPA", Path.home() / "workspace" / "test-ipas" /
    "My_Talking_Tom_v26.5.1_Decrypted.ipa"))


def _donor_bundled() -> bytes:
    """Font Be Vietnam Pro (giai ma .b64 trong repo)."""
    raw = (ROOT / "fonts" / "BeVietnamPro-Regular.ttf.b64").read_bytes()
    return base64.b64decode(raw)


def _extract_font_from_ipa(ipa: Path, entry: str, font_name: str) -> bytes:
    """Trich m_FontData cua Font object (theo ten) tu 1 entry Unity.

    Tim font that: quet magic sfnt + numTables hop le + tag dau in duoc,
    do dai prefix i32 ngay truoc magic (cung cach nhu khao sat).
    """
    zip_name, _, inner = entry.partition("::")
    with zipfile.ZipFile(str(ipa)) as zf:
        data = zf.read(zip_name)
    if uni.detect_bundle(data) is not None:
        bundle = uni.parse_bundle(data, where=zip_name)
        sfs = []
        if inner:
            sfs.append(uni.parse_serialized(
                bundle.get_entry(inner), where=inner))
        else:
            for en in bundle.entries:
                if not en.is_serialized:
                    continue
                try:
                    sfs.append(uni.parse_serialized(
                        bundle.get_entry(en.name), where=en.name))
                except PB3Error:
                    continue
    else:
        sfs = [uni.parse_serialized(data, where=zip_name)]
    for sf in sfs:
        for path_id, name in fs.list_unity_font_objects(sf):
            if name != font_name:
                continue
            raw = sf.object_bytes(path_id)
            for off in range(0, len(raw) - 16):
                magic = raw[off:off + 4]
                if magic not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                    continue
                n = struct.unpack_from(">H", raw, off + 4)[0]
                if not 1 <= n <= 40:
                    continue
                if not all(32 <= b < 127 for b in raw[off + 12:off + 16]):
                    continue
                if off < 4:
                    continue
                (ln,) = struct.unpack_from(sf.endian + "i", raw, off - 4)
                if ln <= 0 or off + ln > len(raw):
                    continue
                return bytes(raw[off:off + ln])
    raise AssertionError("khong tim thay font %r trong %s" % (font_name, entry))


def _cff_of(data: bytes):
    info = fs.parse_font(data)
    off, ln = info.tables["CFF "]
    return info, cff.parse_cff(data[off:off + ln], info.num_glyphs)


class TestCFFParse(TmpCase):
    def test_parse_synthetic_charset_formats(self):
        for fmt in (0, 1, 2):
            data = fx.build_otf([0x41, 0x4F, 0xE0], charset_format=fmt)
            self.assertEqual(fs.verify_font(data), [])
            info, parsed = _cff_of(data)
            self.assertEqual(parsed.charset_sids,
                             [0, 391, 392, 393])
            self.assertEqual(parsed.default_width, 500)
            self.assertEqual(parsed.nominal_width, 100)
            self.assertEqual(len(parsed.charstrings), 4)

    def test_parse_cid_refused(self):
        data = fx.build_otf([0x41], cid=True)
        with self.assertRaises(PB3Error) as cm:
            _cff_of(data)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_parse_predefined_charset_refused(self):
        data = fx.build_otf([0x41], charset_predefined=True)
        with self.assertRaises(PB3Error) as cm:
            _cff_of(data)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_parse_missing_cff_table(self):
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(fx.build_otf_stub(),
                            fx.build_font_full_vietnamese(), [0x1EA0])
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_dict_index_roundtrip(self):
        entries = [([17, 1234], 17), ([500, 100], 18), ([391], 2)]
        raw = cff.build_dict([cff.DictEntry(list(o), op) for o, op in entries])
        back = cff.parse_dict(raw)
        self.assertEqual([(e.operands, e.op) for e in back], entries)
        items = [b"abc", b"", b"\x00" * 300]
        self.assertEqual(cff.parse_index(cff.build_index(items), 0)[0], items)
        self.assertEqual(cff.build_charset_format0([0, 391, 392]),
                         b"\x00\x01\x87\x01\x88")


class TestCharstringDecode(TmpCase):
    def test_decode_lines_curves_width(self):
        cs = fx.cs_bytes(100, 0, "rmoveto", 0, 700, "rlineto",
                         300, 0, "rlineto", "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.segments, [("move", 100.0, 0.0),
                                        ("line", 100.0, 700.0),
                                        ("line", 400.0, 700.0)])
        self.assertIsNone(dec.width)
        # Width: advance 620, nominal 100 -> width 520 dan dau rmoveto.
        cs = fx.cs_bytes(520, 100, 0, "rmoveto", 0, 700, "rlineto", "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.width, 520)
        self.assertEqual(dec.segments[0], ("move", 100.0, 0.0))

    def test_decode_multi_arg_ops(self):
        # rlineto 2 duong, rrcurveto 2 duong cong, hlineto/vlineto xen ke.
        cs = fx.cs_bytes(0, 0, "rmoveto", 100, 0, 0, 100, "rlineto",
                         10, 10, 20, 20, 30, 30, "rrcurveto",
                         50, "hlineto", 60, "vlineto", "endchar")
        dec = cff.decode_charstring(cs)
        kinds = [s[0] for s in dec.segments]
        self.assertEqual(kinds, ["move", "line", "line", "curve",
                                 "line", "line"])
        self.assertEqual(dec.segments[-1], ("line", 210.0, 220.0))

    def test_decode_vv_hh_vh_hv_curveto(self):
        # vvcurveto: dy1 dx2 dy2 dy3 (duong cong dung).
        cs = fx.cs_bytes(0, 0, "rmoveto", 100, 50, 100, 100, "vvcurveto",
                         "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.segments[1],
                         ("curve", 0.0, 100.0, 50.0, 200.0, 50.0, 300.0))
        # hhcurveto: dx1 dy1? (le: dy1) dxa dxb dyb dxc.
        cs = fx.cs_bytes(0, 0, "rmoveto", 10, 100, 50, 100, 100, "hhcurveto",
                         "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.segments[1],
                         ("curve", 100.0, 10.0, 150.0, 110.0, 250.0, 110.0))
        # vhcurveto 4 doi so: dy1 dx2 dy2 dx3.
        cs = fx.cs_bytes(0, 0, "rmoveto", 100, 50, 100, 100, "vhcurveto",
                         "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.segments[1],
                         ("curve", 0.0, 100.0, 50.0, 200.0, 150.0, 200.0))
        # hvcurveto 4 doi so: dx1 dx2 dy2 dy3.
        cs = fx.cs_bytes(0, 0, "rmoveto", 100, 50, 100, 100, "hvcurveto",
                         "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.segments[1],
                         ("curve", 100.0, 0.0, 150.0, 100.0, 150.0, 200.0))

    def test_decode_hintmask_and_stems(self):
        cs = fx.cs_bytes(0, 100, "hstem", 200, 50, "vstem",
                         0, 0, "rmoveto", "hintmask1", 10, 10, "rlineto",
                         "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(len(dec.segments), 2)
        # Width truoc stem: stack le doi so (width + 1 cap stem).
        cs = fx.cs_bytes(520, 0, 100, "hstem", 0, 0, "rmoveto", "endchar")
        dec = cff.decode_charstring(cs)
        self.assertEqual(dec.width, 520)

    def test_decode_callsubr_detected_not_followed(self):
        cs = fx.cs_bytes(0, 0, "rmoveto", 3, "callsubr", "endchar")
        dec = cff.decode_charstring(cs)
        self.assertTrue(dec.uses_subr)
        cs = fx.cs_bytes(0, 0, "rmoveto", 3, "callgsubr", "endchar")
        self.assertTrue(cff.decode_charstring(cs).uses_subr)

    def test_decode_missing_endchar_rejected(self):
        with self.assertRaises(PB3Error):
            cff.decode_charstring(fx.cs_bytes(0, 0, "rmoveto"))
        with self.assertRaises(PB3Error):
            cff.decode_charstring(b"\x0c\x06")  # seac (escape 6)


class TestQuadraticToCubic(TmpCase):
    def test_square_with_quadratic_sides_deviation(self):
        # Hinh vuong 400x400, moi canh la quadratic lech 50 ra ngoai.
        donor = fx.build_ttf(
            [0xE0], name="DonorQuad",
            outlines={0xE0: [[(100, 100, True), (300, 50, False),
                              (500, 100, True), (550, 300, False),
                              (500, 500, True), (300, 550, False),
                              (100, 500, True), (50, 300, False)]]})
        target = fx.build_otf([0x41], name="TargetOTF")
        res = fs.merge_glyphs(target, donor, [0xE0])
        self.assertEqual(fs.verify_font(res.data), [])
        _info, parsed = _cff_of(res.data)
        # Glyph moi la gid 2 (target co 2 glyph: .notdef + A).
        dec = cff.decode_charstring(parsed.charstrings[2])
        src = cff._flatten_glyph(donor, fs.parse_font(donor),
                                 fs.parse_font(donor).cmap[0xE0],
                                 where="test")
        exact = cff.contours_to_cubic_segments(src)
        dev = cff.max_outline_deviation(exact, dec.segments)
        # Gioi han lam tron int ~0,71 don vi (sqrt(0,5^2 + 0,5^2)).
        self.assertLessEqual(dev, 0.71)
        self.assertGreaterEqual(dev, 0.0)

    def test_composite_flattened_with_offset_and_scale(self):
        # A-grave = A (duong thang) + dau huyen (composite, offset y).
        donor = fx.build_ttf(
            [0x41, 0x300, 0xC0], name="DonorComp",
            outlines={0x41: [[(100, 0, True), (100, 700, True),
                              (400, 700, True), (400, 0, True)]],
                      0x300: [[(150, 750, True), (250, 800, False),
                               (350, 750, True)]]},
            composite={0xC0: [(0x41, 0, 0), (0x300, 0, 0)]})
        target = fx.build_otf([0x42], name="TargetOTF")
        res = fs.merge_glyphs(target, donor, [0xC0])
        self.assertEqual(fs.verify_font(res.data), [])
        _info, parsed = _cff_of(res.data)
        dec = cff.decode_charstring(parsed.charstrings[2])
        kinds = [s[0] for s in dec.segments]
        self.assertIn("curve", kinds)  # dau huyen quadratic -> cubic
        self.assertIn("line", kinds)   # chu A duong thang
        src = cff._flatten_glyph(donor, fs.parse_font(donor),
                                 fs.parse_font(donor).cmap[0xC0],
                                 where="test")
        exact = cff.contours_to_cubic_segments(src)
        self.assertLessEqual(
            cff.max_outline_deviation(exact, dec.segments), 0.71)


class TestMergeCFFSynthetic(TmpCase):
    def _target(self, fmt=0, post_format=3.0):
        cs_A = fx.cs_bytes(100, 0, "rmoveto", 0, 700, "rlineto",
                           300, 0, "rlineto", 0, -700, "rlineto", "endchar")
        return fx.build_otf([0x41, 0x4F], name="TargetOTF",
                            charstrings={0x41: cs_A},
                            charset_format=fmt, post_format=post_format)

    def test_merge_simple_keeps_old_glyphs(self):
        target = self._target()
        donor = fx.build_font_full_vietnamese()
        res = fs.merge_glyphs(target, donor, fs.required_vietnamese_chars())
        self.assertEqual(len(res.added), 142)
        self.assertEqual(res.already, [])
        self.assertEqual(fs.verify_font(res.data), [])
        self.assertTrue(fs.coverage(res.data).is_full)
        _t, t_parsed = _cff_of(target)
        _m, m_parsed = _cff_of(res.data)
        self.assertEqual(list(m_parsed.charstrings[:3]),
                         list(t_parsed.charstrings))
        self.assertEqual(m_parsed.charset_sids[:3],
                         t_parsed.charset_sids)

    def test_merge_charset_format1_target(self):
        target = self._target(fmt=1)
        donor = fx.build_ttf([0xE0], name="Donor",
                             outlines={0xE0: [[(0, 0, True), (100, 0, True),
                                                (100, 100, True)]]})
        res = fs.merge_glyphs(target, donor, [0xE0])
        self.assertEqual(res.added, [0xE0])
        self.assertEqual(fs.verify_font(res.data), [])

    def test_merge_post_2_appends_names(self):
        target = self._target(post_format=2.0)
        donor = fx.build_ttf([0xE0], name="Donor",
                             outlines={0xE0: [[(0, 0, True), (100, 0, True),
                                                (100, 100, True)]]})
        res = fs.merge_glyphs(target, donor, [0xE0])
        self.assertEqual(fs.verify_font(res.data), [])
        info = fs.parse_font(res.data)
        off, ln = info.tables["post"]
        raw = res.data[off:off + ln]
        num = struct.unpack_from(">H", raw, 32)[0]
        self.assertEqual(num, 4)
        idx = struct.unpack_from(">4H", raw, 34)
        self.assertEqual(idx[3], 258 + 2)  # ten moi thu 3 trong mang rieng

    def test_merge_donor_otf_copy_no_subr(self):
        # Donor OTF co glyph 'a' (U+00E0) bang duong thang, khong subr.
        cs_e = fx.cs_bytes(0, 0, "rmoveto", 200, 0, "rlineto", 0, 200,
                           "rlineto", -200, 0, "rlineto", "endchar")
        donor = fx.build_otf([0xE0], name="DonorOTF",
                             charstrings={0xE0: cs_e})
        target = self._target()
        res = fs.merge_glyphs(target, donor, [0xE0])
        self.assertEqual(res.added, [0xE0])
        self.assertEqual(fs.verify_font(res.data), [])
        _m, m_parsed = _cff_of(res.data)
        dec = cff.decode_charstring(m_parsed.charstrings[3])
        self.assertEqual(dec.segments[1], ("line", 200.0, 0.0))

    def test_merge_donor_otf_subr_refused(self):
        cs_e = fx.cs_bytes(0, 0, "rmoveto", 3, "callsubr", "endchar")
        donor = fx.build_otf([0xE0], name="DonorOTF",
                             charstrings={0xE0: cs_e})
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(self._target(), donor, [0xE0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_merge_ttf_target_otf_donor_refused(self):
        target = fx.build_font_missing_vietnamese()
        donor = fx.build_otf([0xE0], name="DonorOTF")
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(target, donor, [0xE0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_merge_cid_target_refused(self):
        target = fx.build_otf([0x41], cid=True)
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(target, fx.build_font_full_vietnamese(),
                            [0x1EA0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_merge_progress_counts_glyphs(self):
        seen = []
        fs.merge_glyphs(self._target(), fx.build_font_full_vietnamese(),
                        fs.required_vietnamese_chars(),
                        progress_fn=lambda d, t: seen.append(d))
        self.assertEqual(seen[-1], 142)
        self.assertTrue(all(b >= a for a, b in zip(seen, seen[1:])))

    def test_merge_ttc_cff_face(self):
        face_cff = fx.build_otf([0x41, 0x4F], name="FaceCFF")
        face_ttf = fx.build_ttf(fx.ascii_cps(), name="FaceTTF")
        ttc = fx.build_ttc([face_cff, face_ttf])
        res = fs.merge_vietnamese(ttc, fx.build_font_full_vietnamese(),
                                  target_face=0)
        self.assertEqual(fs.detect_font_kind(res.data), "ttc")
        self.assertEqual(res.face_index, 0)
        self.assertTrue(res.added)
        self.assertEqual(fs.missing_chars(res.data, face_index=0), [])
        self.assertEqual(fs.parse_font(res.data, face_index=1).family,
                         "FaceTTF")
        self.assertEqual(fs.verify_ttc(res.data), [])


@slow
class TestMergeCFFRealFonts(TmpCase):
    """Font CFF that tu IPA (skip neu thieu file)."""

    def _merge_real(self, ipa, entry, font_name):
        if not ipa.exists():
            raise unittest.SkipTest("thieu IPA: %s" % ipa)
        target = _extract_font_from_ipa(ipa, entry, font_name)
        self.assertEqual(fs.verify_font(target), [])
        before = fs.coverage(target)
        self.assertTrue(before.missing)
        res = fs.merge_glyphs(target, _donor_bundled(),
                              fs.required_vietnamese_chars())
        self.assertEqual(fs.verify_font(res.data), [])
        after = fs.coverage(res.data)
        self.assertEqual(after.missing, [])
        self.assertEqual(len(res.added), len(before.missing))
        _t, t_parsed = _cff_of(target)
        _m, m_parsed = _cff_of(res.data)
        self.assertEqual(list(m_parsed.charstrings[:len(t_parsed.charstrings)]),
                         list(t_parsed.charstrings))
        warn = " ".join(res.warnings)
        self.assertIn("sai lech outline lon nhat", warn)
        worst = float(warn.split("sai lech outline lon nhat ")[1]
                      .split(" ")[0])
        self.assertLessEqual(worst, 0.71)
        return res

    def test_pb3_fedoka(self):
        # [ĐÃ ĐO THẬT] Fedoka-Regular: thieu 110/142 -> du 142/142.
        self._merge_real(PB3_IPA, "Payload/ipa.store.app/Data/resources.assets",
                         "Fedoka-Regular")

    def test_pb3_aniara_regular(self):
        # [ĐÃ ĐO THẬT] Aniara_Regular: thieu 102/142 -> du 142/142.
        self._merge_real(PB3_IPA, "Payload/ipa.store.app/Data/resources.assets",
                         "Aniara_Regular")

    def test_tom_cheeky(self):
        # [ĐÃ ĐO THẬT] CheekySansRounded: thieu 96/142 -> du 142/142.
        self._merge_real(
            TOM_IPA,
            "Payload/MyTalkingTom-row.app/Frameworks/UnityFramework.framework"
            "/Data/data.unity3d::resources.assets",
            "CheekySansRounded-SemiboldCondensed")

    def test_pb3_noto_cjk_cid_refused(self):
        if not PB3_IPA.exists():
            raise unittest.SkipTest("thieu IPA: %s" % PB3_IPA)
        target = _extract_font_from_ipa(
            PB3_IPA, "Payload/ipa.store.app/Data/resources.assets",
            "NotoSansCJKsc-DemiLight")
        with self.assertRaises(PB3Error) as cm:
            fs.merge_glyphs(target, _donor_bundled(), [0x1EA0])
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")


if __name__ == "__main__":
    unittest.main()
