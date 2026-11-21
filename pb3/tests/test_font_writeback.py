"""Test ghi nguoc font tieng Viet (sdk/font_writeback.py, F2).

Fixture tong hop (khong can game that): phan loai ca, chan doan MAE,
bang character (layout cu: character TRUOC glyph nhu PB3; layout moi:
character SAU glyph nhu Kusa), chen bang vao object, vector fallback,
ghi typetree (fake SF + node toi thieu). Game that (@slow, skip neu
thieu IPA trong ~/workspace/test-ipas): ghi SDF Aniara_Light PB3,
gop PerfectDOS PB3, lien ket fallback (nhi phan + TMP Settings),
nhan ban fallback LiberationSans PB3.
"""
from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path

import bridge
from core.errors import PB3Error
from sdk import font_writeback as fw
from sdk import sdf as sm
from sdk import tmp_font as tf
from sdk import tmp_sdf as ts
from sdk import unity as uni
from tests.test_font_check_3step import _node, _str_node, _arr_node
from tests.test_tmp_font import (_astr, _build_sf, _faceinfo,
                                _faceinfo_new, _glyphs, _glyphs_new,
                                _obj, _pptr, _tmp_asset, _TY)
from tests.tiering import slow

ROOT = Path(__file__).resolve().parent.parent
IPAS = Path.home() / "workspace" / "test-ipas"
PB3_IPA = IPAS / "pb3-v1.1.7.ipa"

E = "<"


def _donor_bytes():
    return (ROOT / "fonts" / "BeVietnamPro-Regular.ttf").read_bytes()


def _char_table(records):
    """records: [(unicode, glyph_index)] -> bytes count + 16B/record."""
    out = bytearray(struct.pack(E + "i", len(records)))
    for (u, g) in records:
        out += struct.pack(E + "III", 1, u, g) + struct.pack(E + "f", 1.0)
    return bytes(out)


def _synth_old(name="Test SDF", ids=(32, 65, 66), fallback_pids=(),
               after=0):
    """TMP layout CU tong hop [FIXTURE]: header MB + FaceInfo cu +
    bang character (TRUOC bang glyph, nhu PB3 Aniara) + bang glyph 36B
    + duoi fallback [count][PPtr][after-int] (neu fallback_pids)."""
    hdr = _pptr(0, 0, E) + b"\1\0\0\0" + _pptr(0, 5, E) \
        + _astr(name.encode(), E)
    body = struct.pack(E + "i", 1)
    body += _faceinfo("TestFont", E, count=len(ids), aw=256.0, ah=256.0)
    body += _char_table([(u, i + 1) for i, u in enumerate(ids)])
    body += _glyphs(list(ids), E, aw=256, ah=256)
    if fallback_pids:
        body += struct.pack(E + "i", len(fallback_pids))
        for pid in fallback_pids:
            body += _pptr(0, pid, E)
        body += struct.pack(E + "i", after)
    return hdr + body + bytes(64)


def _synth_new(name="TestNew SDF", gids=(3, 4, 5), cps=(0x20, 0x21, 0x22),
               fallback_pids=(), after=0):
    """TMP layout MOI tong hop [FIXTURE]: header MB + FaceInfo moi +
    bang glyph 52B + bang character (SAU bang glyph, nhu Kusa) +
    duoi fallback."""
    hdr = _pptr(0, 0, E) + b"\1\0\0\0" + _pptr(0, 5, E) \
        + _astr(name.encode(), E)
    body = struct.pack(E + "i", 1)
    body += _faceinfo_new("TestFont", "Regular", E, pt=90,
                          count=len(gids))
    body += _glyphs_new(list(gids), E, aw=256, ah=256)
    body += _char_table([(u, g) for u, g in zip(cps, gids)])
    if fallback_pids:
        body += struct.pack(E + "i", len(fallback_pids))
        for pid in fallback_pids:
            body += _pptr(0, pid, E)
        body += struct.pack(E + "i", after)
    return hdr + body + bytes(64)


def _fake_glyph(cp, gid, x=0, y=0):
    return {"cp": cp, "gid": gid, "metrics": (30.0, 30.0, 1.5, 24.0, 20.0),
            "rect": (x, y, 30, 30)}


class TestClassify(unittest.TestCase):
    def test_static_sdf(self):
        r = fw.classify_case({"kind": "tmp", "name": "A SDF",
                              "confirmed": True, "population": "Static",
                              "atlas_letters": 32, "atlas_marks": 0,
                              "source_present": True})
        self.assertEqual(r["route"], "tmp_static_sdf")

    def test_dynamic_source_merge(self):
        r = fw.classify_case({"kind": "tmp", "name": "B SDF",
                              "confirmed": True, "population": "Dynamic",
                              "atlas_letters": 32, "atlas_marks": 0,
                              "source_present": True, "source_letters": 38,
                              "source_marks": 5, "atlas_used_pct": 50.0})
        self.assertEqual(r["route"], "tmp_dynamic_source_merge")

    def test_dynamic_fallback_atlas_full(self):
        r = fw.classify_case({"kind": "tmp", "name": "C SDF",
                              "confirmed": True, "population": "Dynamic",
                              "atlas_letters": 32, "atlas_marks": 0,
                              "source_present": True, "source_letters": 134,
                              "source_marks": 8, "atlas_used_pct": 92.0})
        self.assertEqual(r["route"], "tmp_dynamic_fallback")

    def test_dynamic_no_source_needs_clone(self):
        r = fw.classify_case({"kind": "tmp", "name": "D SDF",
                              "confirmed": True, "population": "Dynamic",
                              "atlas_letters": 0, "atlas_marks": 0,
                              "source_present": False})
        self.assertEqual(r["route"], "tmp_dynamic_fallback")

    def test_bitmap_refused(self):
        r = fw.classify_case({"kind": "tmp", "name": "E SDF",
                              "confirmed": True, "population": "Static",
                              "atlas_letters": 0, "atlas_marks": 0,
                              "source_present": True, "atlas_is_bitmap": True})
        self.assertEqual(r["route"], "none")

    def test_unconfirmed_refused(self):
        r = fw.classify_case({"kind": "tmp", "name": "F SDF",
                              "confirmed": False, "population": "Static"})
        self.assertEqual(r["route"], "none")

    def test_font_merge_and_full(self):
        r = fw.classify_case({"kind": "font", "name": "G",
                              "source_letters": 32, "source_marks": 0})
        self.assertEqual(r["route"], "font_merge")
        r = fw.classify_case({"kind": "font", "name": "G",
                              "source_letters": 134, "source_marks": 8})
        self.assertEqual(r["route"], "none")

    def test_tmp_already_full(self):
        r = fw.classify_case({"kind": "tmp", "name": "H SDF",
                              "confirmed": True, "population": "Static",
                              "atlas_letters": 134, "atlas_marks": 8,
                              "source_present": True})
        self.assertEqual(r["route"], "none")


class TestConfirmedTmp(unittest.TestCase):
    """_is_confirmed_tmp: can ca 3 — FaceInfo + ten SDF + bang glyph
    (hoac Dynamic rong). [DA DO THAT tren PB3] Quy tac cu (chi can
    bang glyph) nhan 10.305 "TMP" gia trong sharedassets1.assets
    (URPAsset, DepthOfField, World1...) + 244 gia trong
    resources.assets — bang glyph heuristic nhan ca "bang" 1 record
    gia; quy tac moi nhan dung 15 TMP that, loai het gia. Hoi quy
    PB3 pid 1196: Dynamic rong 0 glyph, ten SDF - DYNAMIC, co
    FaceInfo — tang 1 cu loai no lam dut doan vector fallback 9 muc
    cua TMP Settings -> link_global that bai E_NO_FALLBACK_VECTOR."""
    def _dec(self, name, glyphs, declared, fi_name="Noto Sans CJK KR"):
        return {"name": name,
                "glyph_table": ({"count": glyphs} if glyphs is not None
                                else None),
                "font_info": ({"name": fi_name, "declared_char_count": declared}
                              if fi_name is not None else None)}

    def test_with_glyph_table(self):
        self.assertTrue(fw._is_confirmed_tmp(
            self._dec("LiberationSans SDF", 250, 250)))

    def test_empty_dynamic_sdf(self):
        # Hoi quy 1196: 0 glyph, ten SDF - DYNAMIC.
        self.assertTrue(fw._is_confirmed_tmp(
            self._dec("NotoSansCJKkr-DemiLight SDF - DYNAMIC", None, 0)))

    def test_empty_static_refused(self):
        # Static rong 0 glyph la asset hong (Static luon nuong glyph
        # luc tao); chi Dynamic rong moi hop le (nap glyph luc chay).
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("SomeFont SDF", None, 0)))

    def test_fallback_suffix_accepted(self):
        # Asset fallback do chinh F2 tao: "<base> SDF - Fallback".
        self.assertTrue(fw._is_confirmed_tmp(
            self._dec("LiberationSans SDF - Fallback", 3, 3)))

    def test_no_glyph_non_sdf_name_refused(self):
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("SomeScript", None, 0)))

    def test_no_font_info_refused(self):
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("X SDF", None, 0, fi_name=None)))

    def test_glyph_table_but_no_face_info_refused(self):
        # Hinh dang gia trong sharedassets1: "bang" glyph heuristic
        # nhung khong co FaceInfo (ten cung khong phai SDF that).
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("DepthOfField", 1, 0, fi_name=None)))

    def test_sdf_name_but_no_face_info_refused(self):
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("Y SDF", 5, 5, fi_name=None)))

    def test_declared_but_no_glyph_table_refused(self):
        # Khai bao co chu nhung khong tim thay bang glyph: asset loi /
        # bo cuc la, khong phai Dynamic rong -> khong doan.
        self.assertFalse(fw._is_confirmed_tmp(
            self._dec("Y SDF", None, 38)))


class TestMae(unittest.TestCase):
    def test_threshold_liberation_warn_noto_fail(self):
        # So that [DA DO THAT] (exp/sdf_regen_results.md).
        self.assertEqual(fw.mae_threshold_advice(4.20, 1.5, 35.0)[0],
                         "WARN")
        self.assertEqual(fw.mae_threshold_advice(7.55, 1.5, 9.0)[0],
                         "FAIL")
        self.assertEqual(fw.mae_threshold_advice(2.05, 1.2, 61.0)[0],
                         "PASS")

    def test_diagnose_font_version_drift(self):
        rows = ([{"mae": 1.0}, {"mae": 1.2}] + [{"mae": 4.2}] * 4
                + [{"mae": 6.0}] * 2)
        d = fw.diagnose_mae(rows)
        self.assertEqual(d["verdict"], "font_version_drift")
        self.assertEqual(d["threshold"], "WARN")

    def test_diagnose_systematic_fail(self):
        rows = [{"mae": 5.0}] * 8
        d = fw.diagnose_mae(rows)
        self.assertEqual(d["verdict"], "fail")
        self.assertEqual(d["threshold"], "FAIL")

    def test_diagnose_pass(self):
        rows = [{"mae": 1.5}] * 8
        self.assertEqual(fw.diagnose_mae(rows)["verdict"], "pass")


class TestCharTable(unittest.TestCase):
    def test_locate_old_char_before_glyph(self):
        raw = _synth_old()
        dec = tf.decode_object(raw, E)
        self.assertEqual(dec["status"], "parsed")
        loc = fw.locate_char_table(raw, E, dec["glyph_table"])
        self.assertEqual(loc["count"], 3)
        self.assertEqual([r["unicode"] for r in loc["records"]],
                         [32, 65, 66])
        self.assertLess(loc["offset"], dec["glyph_table"]["offset"])

    def test_locate_new_char_after_glyph(self):
        raw = _synth_new()
        dec = tf.decode_object(raw, E)
        self.assertEqual(dec["status"], "parsed")
        loc = fw.locate_char_table(raw, E, dec["glyph_table"])
        self.assertEqual(loc["count"], 3)
        self.assertEqual([r["glyph_index"] for r in loc["records"]],
                         [3, 4, 5])
        self.assertGreater(loc["offset"], dec["glyph_table"]["offset"])

    def test_build_char_old_index_plus_one(self):
        old = [{"element_type": 1, "unicode": 65, "glyph_index": 2,
                "scale": 1.0},
               {"element_type": 1, "unicode": 66, "glyph_index": 3,
                "scale": 1.0}]
        glyphs = [_fake_glyph(65, 10), _fake_glyph(66, 11),
                  _fake_glyph(0xE0, 12)]
        blob, entries = fw.build_char_table_bytes(old, glyphs, "old")
        self.assertEqual([e["glyph_index"] for e in entries], [1, 2, 3])
        self.assertEqual([e["unicode"] for e in entries],
                         [65, 66, 0xE0])

    def test_build_char_new_uses_gid(self):
        old = [{"element_type": 1, "unicode": 0x20, "glyph_index": 3,
                "scale": 1.0}]
        glyphs = [_fake_glyph(0x20, 3), _fake_glyph(0xE0, 200)]
        _blob, entries = fw.build_char_table_bytes(old, glyphs, "new")
        self.assertEqual([(e["unicode"], e["glyph_index"]) for e in entries],
                         [(0x20, 3), (0xE0, 200)])


class TestSplice(unittest.TestCase):
    def test_splice_old_adds_vietnamese(self):
        raw = _synth_old()
        dec = tf.decode_object(raw, E)
        gt = dec["glyph_table"]
        loc = fw.locate_char_table(raw, E, gt)
        glyphs = [_fake_glyph(32, 0, 0, 0), _fake_glyph(65, 0, 32, 0),
                  _fake_glyph(66, 0, 64, 0), _fake_glyph(0xE0, 0, 96, 0)]
        new_table = sm.encode_glyph_table_old(glyphs)
        new_char, _e = fw.build_char_table_bytes(loc["records"], glyphs,
                                                 "old")
        count_off = (dec.get("font_info") or {}).get("count_offset")
        self.assertIsNotNone(count_off)
        out = fw.splice_tmp_object(raw, E, new_table, new_char, 4, gt,
                                   loc, count_off)
        dec2 = tf.decode_object(out, E)
        self.assertEqual(dec2["glyph_table"]["count"], 4)
        self.assertEqual(
            (dec2.get("font_info") or {}).get("declared_char_count"), 4)
        loc2 = fw.locate_char_table(out, E, dec2["glyph_table"])
        self.assertEqual(loc2["count"], 4)
        self.assertIn(0xE0, [r["unicode"] for r in loc2["records"]])
        self.assertEqual(
            dec2["vietnamese"]["covered"], 1)

    def test_splice_new_adds_vietnamese(self):
        raw = _synth_new()
        dec = tf.decode_object(raw, E)
        gt = dec["glyph_table"]
        loc = fw.locate_char_table(raw, E, gt)
        glyphs = [_fake_glyph(0x20, 3, 0, 0), _fake_glyph(0x21, 4, 32, 0),
                  _fake_glyph(0x22, 5, 64, 0), _fake_glyph(0xE0, 200, 96, 0)]
        new_table = sm.encode_glyph_table_new(glyphs)
        new_char, _e = fw.build_char_table_bytes(loc["records"], glyphs,
                                                 "new")
        count_off = (dec.get("font_info") or {}).get("count_offset")
        self.assertIsNotNone(count_off)
        out = fw.splice_tmp_object(raw, E, new_table, new_char, 4, gt,
                                   loc, count_off)
        dec2 = tf.decode_object(out, E)
        self.assertEqual(dec2["glyph_table"]["count"], 4)
        self.assertEqual(
            (dec2.get("font_info") or {}).get("declared_char_count"), 4)
        loc2 = fw.locate_char_table(out, E, dec2["glyph_table"])
        self.assertEqual(loc2["count"], 4)
        self.assertIn(0xE0, [r["unicode"] for r in loc2["records"]])


class TestFallbackVector(unittest.TestCase):
    def test_find_and_insert(self):
        raw = _synth_old(fallback_pids=(777,), after=0)
        confirmed = {777, 888}
        vec = fw.find_fallback_vector(raw, E, confirmed, min_offset=0)
        self.assertIsNotNone(vec)
        self.assertEqual(vec["entries"], [777])
        out = fw.insert_fallback_entry(raw, E, vec, 888, {777, 888},
                                       confirmed)
        vec2 = fw.find_fallback_vector(out, E, confirmed, min_offset=0)
        self.assertEqual(vec2["entries"], [777, 888])

    def test_find_none_when_empty(self):
        raw = _synth_old()
        self.assertIsNone(fw.find_fallback_vector(raw, E, {777},
                                                  min_offset=0))

    def test_after_unexplained_refused(self):
        raw = _synth_old(fallback_pids=(777,), after=7)
        vec = fw.find_fallback_vector(raw, E, {777}, min_offset=0)
        self.assertIsNotNone(vec)
        with self.assertRaises(PB3Error) as cm:
            fw.insert_fallback_entry(raw, E, vec, 888, {777, 888}, {777})
        self.assertEqual(cm.exception.code, "E_FALLBACK_AFTER")

    def test_insert_already_linked_refused(self):
        raw = _synth_old(fallback_pids=(777,), after=0)
        vec = fw.find_fallback_vector(raw, E, {777}, min_offset=0)
        with self.assertRaises(PB3Error) as cm:
            fw.insert_fallback_entry(raw, E, vec, 777, {777}, {777})
        self.assertEqual(cm.exception.code, "E_ALREADY_LINKED")


class TestPptrHelpers(unittest.TestCase):
    def test_replace_pptr_all(self):
        raw = b"\x00" * 8 + struct.pack(E + "iq", 0, 44) + b"\x00" * 4 \
            + struct.pack(E + "iq", 0, 44)
        out, n = fw._replace_pptr_all(raw, E, 44, 99)
        self.assertEqual(n, 2)
        self.assertEqual(fw._find_pptr_occurrences(out, E, 99), [8, 24])

    def test_replace_mb_name(self):
        raw = _synth_old(name="Test SDF")
        out = fw.replace_mb_name(raw, E, "Test SDF - Fallback")
        hdr, _p = tf.read_monobehaviour_header(out, E)
        self.assertEqual(hdr["name"], "Test SDF - Fallback")
        dec = tf.decode_object(out, E)
        self.assertEqual(dec["glyph_table"]["count"], 3)


class _FakeType:
    def __init__(self, node):
        self.node = node


class _FakeObj:
    type_id = 0
    class_id = 114


class _FakeSF:
    endian = "<"
    where = "fixture"

    def __init__(self, node, raw):
        self.types = [_FakeType(node)]
        self._raw = raw

    def get_object(self, path_id):
        return _FakeObj()

    def object_bytes(self, path_id):
        return self._raw


def _mini_tmp_node():
    fb_elem = _node("common_106", "PPtr", -1, children=[
        _node("m_FileID", "int", 4), _node("m_PathID", "SInt64", 8)])
    return _node("TMP_FontAsset", "TMP_FontAsset", -1, children=[
        _str_node("m_Name"),
        _arr_node("m_FallbackFontAssetTable", fb_elem)])


class TestTypetreeLink(unittest.TestCase):
    def test_append_fallback_via_typetree(self):
        node = _mini_tmp_node()
        val = {"m_Name": "Test SDF",
               "m_FallbackFontAssetTable": [{"m_FileID": 0,
                                             "m_PathID": 777}]}
        w = uni._Writer("<")
        uni.write_typetree_value(w, node, val, "<")
        raw = w.bytes()
        fake = _FakeSF(node, raw)
        self.assertIsNotNone(fw._typetree_fallback_field(fake, 11))
        new_obj, _applied = uni.write_monobehaviour_object_bytes(
            fake, 11, [{"field_path": "m_FallbackFontAssetTable[+]",
                        "element": {"m_FileID": 0, "m_PathID": 888}}])
        val2, _end = uni.read_typetree_value(new_obj, node, 0, "<")
        self.assertEqual([e["m_PathID"] for e in
                          val2["m_FallbackFontAssetTable"]], [777, 888])


class TestBridgeOp(unittest.TestCase):
    """Op bridge font_writeback tren fixture tong hop (khong can game).

    Cung hoi quy loi _iter_tmp_sf dung bien global `tf` chua import
    (NameError) — loi nay lam nut "Tai tao atlas SDF" (tmp_sdf_regen)
    vo dung tu v48 vi khong test nao goi qua bridge.
    """
    def _synth_font_sf(self):
        raw = (b"\x00" * 92 + _donor_bytes() + b"\x00" * 46)
        blob = _build_sf(17, True, _TY,
                         [_obj(40, 128, raw, 2)], scripts=())
        tmp = Path(__file__).resolve().parent / "_fw_tmp.assets"
        tmp.write_bytes(blob)
        return tmp

    def test_auto_plan_synthetic_font_full(self):
        tmp = self._synth_font_sf()
        try:
            res = json.loads(bridge.font_writeback(str(tmp)))
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(len(res["plan"]), 1)
            self.assertEqual(res["plan"][0]["kind"], "font")
            self.assertEqual(res["plan"][0]["route"], "none")
            self.assertEqual(res["routes"], {"none": 1})
            self.assertTrue(Path(res["dest"]).exists())
        finally:
            tmp.unlink(missing_ok=True)

    def test_unknown_mode(self):
        tmp = self._synth_font_sf()
        try:
            res = json.loads(bridge.font_writeback(str(tmp), mode="nope"))
            self.assertFalse(res["ok"])
            self.assertIn("khong ho tro", res["error"])
        finally:
            tmp.unlink(missing_ok=True)

    def test_missing_asset(self):
        tmp = self._synth_font_sf()
        try:
            res = json.loads(bridge.font_writeback(
                str(tmp), asset_name="KhongCo", mode="tmp_sdf"))
            self.assertFalse(res["ok"])
            self.assertIn("khong tim thay asset", res["error"])
        finally:
            tmp.unlink(missing_ok=True)

    def test_tmp_sdf_regen_iter_fixed(self):
        # Hoi quy NameError `tf` trong _iter_tmp_sf: phai tra loi
        # "khong tim thay TMP asset", khong phai NameError.
        tmp = self._synth_font_sf()
        try:
            res = json.loads(bridge.tmp_sdf_regen(str(tmp)))
            self.assertFalse(res["ok"])
            self.assertIn("khong tim thay TMP asset", res["error"])
            self.assertNotIn("NameError", res["error"])
        finally:
            tmp.unlink(missing_ok=True)


@slow
class TestBridgeOpReal(unittest.TestCase):
    """Op bridge font_writeback tren PB3 that (dry-run mac dinh)."""
    @classmethod
    def setUpClass(cls):
        if not PB3_IPA.exists():
            raise unittest.SkipTest("thieu IPA PB3")

    def test_auto_plan_real(self):
        res = json.loads(bridge.font_writeback(str(PB3_IPA)))
        self.assertTrue(res["ok"], res.get("error"))
        # PB3 that: 16 TMP that (15 o resources.assets + 1 Fedoka ban
        # sao o sharedassets0) + 16 Font object — khong con 10.770 muc
        # "none" tu MonoBehaviour gia trong sharedassets1 (xem
        # TestConfirmedTmp).
        self.assertEqual(len(res["plan"]), 32)
        self.assertEqual(res["routes"], {
            "font_merge": 12, "tmp_static_sdf": 9,
            "tmp_dynamic_source_merge": 4, "tmp_dynamic_fallback": 1,
            "none": 6})
        by_name = {p["name"]: p for p in res["plan"]}
        self.assertEqual(by_name["Aniara_Light SDF"]["route"],
                         "tmp_static_sdf")
        self.assertEqual(by_name["PerfectDOSVGA437"]["route"],
                         "font_merge")
        self.assertEqual(by_name["NotoSans-Regular SDF - DYNAMIC"]["route"],
                         "tmp_dynamic_fallback")
        self.assertEqual(by_name["LiberationSans SDF - Fallback"]["route"],
                         "none")
        # Fedoka ban sao o sharedassets0: that nhung font nguon khong
        # co trong cung file -> none, co ghi chu file khac.
        fedoka_extra = [p for p in res["plan"]
                        if p["name"] == "Fedoka-Regular SDF"
                        and "sharedassets0" in p["source"]]
        self.assertEqual(len(fedoka_extra), 1)
        self.assertEqual(fedoka_extra[0]["route"], "none")
        self.assertTrue(any("file .assets khac" in n
                            for n in fedoka_extra[0]["notes"]))

    def test_tmp_sdf_dry_run_real(self):
        zip_path = Path("/tmp/font_writeback.zip")
        if zip_path.exists():
            zip_path.unlink()
        res = json.loads(bridge.font_writeback(
            str(PB3_IPA), asset_name="Aniara_Light", mode="tmp_sdf",
            extra="à"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["glyphs_old"], 96)
        self.assertEqual(res["glyphs_new"], 97)
        self.assertEqual(res["gate"]["status"], "pass")
        self.assertFalse(zip_path.exists())

    def test_tmp_sdf_regen_real(self):
        # Hoi quy: op cu tmp_sdf_regen loc is_tmp nen tren PB3 (khong
        # typetree) tra "da quet: khong co" — gio phai tai tao duoc.
        res = json.loads(bridge.tmp_sdf_regen(
            str(PB3_IPA), "Aniara_Light SDF", "à"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["name"], "Aniara_Light SDF")
        self.assertEqual(res["glyphs_old"], 96)
        self.assertEqual(res["glyphs_new"], 97)

    def test_font_merge_dry_run_real(self):
        res = json.loads(bridge.font_writeback(
            str(PB3_IPA), asset_name="Perfect DOS", mode="font_merge"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["covered_before"], 32)
        self.assertEqual(res["covered_after"], 142)

    def test_link_dry_run_real(self):
        res = json.loads(bridge.font_writeback(
            str(PB3_IPA), asset_name="LiberationSans SDF", mode="link",
            donor_tmp="NotoSans-Regular SDF"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["entries_before"], 1)
        self.assertEqual(res["entries_after"], 2)

    def test_global_link_dry_run_real(self):
        res = json.loads(bridge.font_writeback(
            str(PB3_IPA), asset_name="LiberationSans SDF",
            mode="global_link"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["entries_before"], 9)
        self.assertEqual(res["entries_after"], 10)

    def test_clone_dry_run_real(self):
        res = json.loads(bridge.font_writeback(
            str(PB3_IPA), asset_name="LiberationSans SDF",
            mode="fallback_clone"))
        self.assertTrue(res["ok"], res.get("error"))
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["clone_name"], "LiberationSans SDF - Fallback")
        self.assertEqual(res["gate"]["status"], "pass")


def _load_pb3():
    from sdk import containers as cont
    vfs = cont.open_vfs(str(PB3_IPA))
    sf_bytes = vfs.read("Payload/ipa.store.app/Data/resources.assets")
    ress_bytes = vfs.read("Payload/ipa.store.app/Data/resources.assets.resS")
    return sf_bytes, ress_bytes


def _ress_slice(ress_bytes):
    def _slice(path, off, size):
        return ress_bytes[off:off + size]
    return _slice


@slow
class TestRealPb3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PB3_IPA.exists():
            raise unittest.SkipTest("thieu IPA PB3")
        cls.sf_bytes, cls.ress_bytes = _load_pb3()
        cls.donor = _donor_bytes()

    def test_writeback_tmp_sdf_aniara_light(self):
        rep = fw.writeback_tmp_sdf(self.sf_bytes, 1189, [0xE0],
                                   _ress_slice(self.ress_bytes))
        self.assertEqual(rep["glyphs_old"], 96)
        self.assertEqual(rep["glyphs_new"], 97)
        self.assertEqual(rep["added"], [0xE0])
        self.assertEqual(rep["gate"]["status"], "pass")
        self.assertEqual(rep["atlas_patch"]["size"], 512 * 512)
        sf2 = uni.parse_serialized(rep["patched_sf_bytes"],
                                    where="font_writeback")
        dec2 = tf.decode_object(sf2.object_bytes(1189), sf2.endian)
        self.assertEqual(dec2["glyph_table"]["count"], 97)
        loc2 = fw.locate_char_table(sf2.object_bytes(1189), sf2.endian,
                                    dec2["glyph_table"])
        self.assertEqual(loc2["count"], 97)
        self.assertIn(0xE0, [r["unicode"] for r in loc2["records"]])
        # Asset khac giu nguyen bytes.
        sf0 = uni.parse_serialized(self.sf_bytes, where="font_writeback")
        self.assertEqual(sf2.object_bytes(1207),
                         sf0.object_bytes(1207))

    def test_writeback_font_merge_perfectdos(self):
        rep = fw.writeback_font_merge(self.sf_bytes, 171, self.donor)
        self.assertEqual(rep["covered_before"], 32)
        self.assertEqual(rep["covered_after"], 142)
        self.assertGreater(len(rep["added"]), 100)

    def test_link_fallback_binary(self):
        rep = fw.link_fallback(self.sf_bytes, 1207, 1193)
        self.assertEqual(rep["method"], "binary")
        self.assertEqual(rep["status"], "linked")
        self.assertEqual(rep["entries_before"], 1)
        self.assertEqual(rep["entries_after"], 2)
        rep2 = fw.link_fallback(self.sf_bytes, 1207, 1206)
        self.assertEqual(rep2["status"], "already_linked")

    def test_link_global_fallback(self):
        rep = fw.link_global_fallback(self.sf_bytes, 1207)
        self.assertEqual(rep["method"], "global_binary")
        self.assertEqual(rep["status"], "linked")
        self.assertEqual(rep["entries_before"], 9)
        self.assertEqual(rep["entries_after"], 10)

    def test_clone_fallback_liberation(self):
        sf0 = uni.parse_serialized(self.sf_bytes, where="font_writeback")
        raw = sf0.object_bytes(1207)
        dec = tf.decode_object(raw, sf0.endian)
        gt = dec["glyph_table"]
        coff = gt["offset"] + 4 + gt["stride"] * gt["count"]
        (cc,) = struct.unpack_from(E + "i", raw, coff)
        unis = set()
        for i in range(cc):
            o = coff + 4 + 16 * i
            _et, u, _g = struct.unpack_from(E + "III", raw, o)
            unis.add(u)
        vi = set(tf.vietnamese_letters_134()) \
            | set(tf.combining_marks_8())
        missing = sorted(vi - unis)
        self.assertEqual(len(missing), 110)
        rep = fw.clone_fallback_asset(self.sf_bytes, 1207, self.donor,
                                      self.ress_bytes, missing)
        self.assertEqual(rep["clone_name"], "LiberationSans SDF - Fallback")
        self.assertEqual(rep["gate"]["status"], "pass")
        self.assertEqual(rep["ress_append_offset"], len(self.ress_bytes))
        sf2 = uni.parse_serialized(rep["patched_sf_bytes"],
                                    where="font_writeback")
        self.assertEqual(sf2.object_bytes(1207), raw)
        dec2 = tf.decode_object(
            sf2.object_bytes(rep["clone_tmp_pid"]), sf2.endian)
        self.assertEqual(dec2["glyph_table"]["count"], 110)
        tex2 = ts.parse_texture2d(
            sf2.object_bytes(rep["clone_texture_pid"]), sf2.endian)
        self.assertEqual(tex2["stream"]["offset"], len(self.ress_bytes))
        self.assertEqual(tex2["stream"]["size"], 1024 * 1024)

    def test_clone_fedoka_no_stream_refused(self):
        with self.assertRaises(PB3Error) as cm:
            fw.clone_fallback_asset(self.sf_bytes, 1191, self.donor,
                                    self.ress_bytes)
        self.assertEqual(cm.exception.code, "E_NO_ATLAS")

    def test_find_atlas_texture_base_name(self):
        sf0 = uni.parse_serialized(self.sf_bytes, where="font_writeback")
        at = ts.find_atlas_texture(sf0, "Fedoka-Regular SDF - DYNAMIC")
        self.assertIsNotNone(at)
        self.assertEqual(at[1]["width"], 1024)


if __name__ == "__main__":
    unittest.main()
