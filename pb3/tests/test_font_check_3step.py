# -*- coding: utf-8 -*-
"""Test kiem tra font 3 buoc (sdk/font_check.py, gate + bridge + UI).

- Bo 134 chu + 8 dau: dem that, doi chieu bo doc lap cua studio.
- Sfnt scan: TTF that (donor Be Vietnam Pro) + magic gia o ranh gioi
  field bi loai nho validate table directory (bay Kusa [DA DO THAT]).
- Trich m_FontData: Font object gia (header 92B + TTF that + 46B duoi,
  dung layout Kusa ar) -> cmap 134/134 + 8/8.
- Doc TMP typetree: node tree dung tay + ghi bang write_typetree_value
  (codec cua repo) -> doc lai dung so.
- Xac nhan TMP: (a) ten script, (b) truong typetree, (c) heuristic
  (unconfirmed, khong vao verdict).
- Gate: verdict KHONG doi khi them bao cao 3 tang; donor PASS, scan
  FAIL dung luat cu; khoi font_check_3step luon co.
- E2E game that (@slow, skip neu thieu IPA trong ~/workspace/test-ipas):
  Kusa 8 Font + 7 TMP confirmed (6 typetree + 1 binary); 9K/EK2/PB3
  Font object doc duoc cmap; Knightica 8 Font + 13 TMP binary,
  Dynamic KHONG cuu duoc; Tom 0 font -> donor.
- Binary parse TMP khong typetree (bo cuc 16 byte hoc tu Kusa,
  kiem chung Knightica): fixture tong hop + do day atlas + ket luan
  Dynamic (suc chua theo dien tich trong, gioi han boi cmap nguon)
  + de xuat fallback TV tu font nguon du 134/134 trong IPA.
"""
from __future__ import annotations

import base64
import io
import json
import struct
import sys
import unicodedata
import unittest
from pathlib import Path

import bridge
from sdk import font as font_sdk
from sdk import font_check as fc
from sdk import gate
from sdk import tmp_font as tf
from sdk import unity as uni
from tests.test_tmp_font import _build_sf, _obj, _sf_with_assets, _tmp_asset, _TY
from tests.tiering import slow

ROOT = Path(__file__).resolve().parent.parent
IPAS = Path.home() / "workspace" / "test-ipas"
KUSA_IPA = IPAS / "re-xue-xing-cun-zhe-1.0.3.ipa"
NINEK_IPA = IPAS / "9kings-v1.0.6.ipa"
EK2_IPA = IPAS / "ek2-v0.0.18.ipa"
PB3_IPA = IPAS / "pb3-v1.1.7.ipa"
TOM_IPA = IPAS / "My_Talking_Tom_v26.5.1_Decrypted.ipa"
KNIGHTICA_IPA = IPAS / "dou-zhen-qi-shi-1.0.3.ipa"

MARKS_8 = {0x300, 0x301, 0x302, 0x303, 0x306, 0x309, 0x31B, 0x323}


def _donor_bytes():
    return base64.b64decode(
        (ROOT.parent / "fonts" / "BeVietnamPro-Regular.ttf.b64").read_bytes())


def _studio_vi_134():
    """Bo 134 chu doc lap (12 goc x 5 dau x 2 ve + d/D, NFC)."""
    bases = "aăâeêioôơuưy"
    tones = ["", "\u0300", "\u0309", "\u0303", "\u0301", "\u0323"]
    out = set()
    for b in bases + bases.upper():
        for t in tones:
            ch = unicodedata.normalize("NFC", b + t)
            if len(ch) == 1 and ord(ch) > 127:
                out.add(ord(ch))
    out |= {0x110, 0x111}
    return out


# ---------------------------------------------------------- node typetree
def _node(name, type_="int", byte_size=4, children=None, level=0, index=0):
    return uni.TypeTreeNode(type=type_, name=name, byte_size=byte_size,
                            index=index, level=level, meta_flag=0,
                            children=children or [])


def _str_node(name):
    size = _node("common_795", "int", 4)
    elem = _node("common_106", "UInt8", 1)
    arr = _node("Array", "Array", -1, children=[size, elem])
    return _node(name, "string", -1, children=[arr])


def _arr_node(name, elem):
    size = _node("common_795", "int", 4)
    arr = _node("Array", "Array", -1, children=[size, elem])
    return _node(name, "vector", -1, children=[arr])


def _pptr_node(name):
    return _node(name, "PPtr", -1, children=[
        _node("m_FileID", "int", 4), _node("m_PathID", "SInt64", 8)])


def _tmp_typetree_node():
    char_elem = _node("common_106", "CharacterInfo", -1, children=[
        _node("m_Unicode", "UInt32", 4)])
    glyph_elem = _node("common_106", "GlyphInfo", -1, children=[
        _node("m_Index", "UInt32", 4)])
    face = _node("m_FaceInfo", "FaceInfo", -1, children=[
        _str_node("m_FamilyName"), _str_node("m_StyleName"),
        _node("m_PointSize", "float", 4),
        _node("m_AtlasWidth", "int", 4),
        _node("m_AtlasHeight", "int", 4)])
    return _node("TMP_FontAsset", "TMP_FontAsset", -1, children=[
        _str_node("m_Name"), face,
        _arr_node("m_CharacterTable", char_elem),
        _arr_node("m_GlyphTable", glyph_elem),
        _node("m_AtlasPopulationMode", "int", 4),
        _arr_node("m_FallbackFontAssetTable",
                  _node("common_106", "PPtr", -1, children=[
                      _node("m_FileID", "int", 4),
                      _node("m_PathID", "SInt64", 8)])),
        _arr_node("m_AtlasTextures",
                  _node("common_106", "PPtr", -1, children=[
                      _node("m_FileID", "int", 4),
                      _node("m_PathID", "SInt64", 8)])),
        _pptr_node("m_SourceFontFile"),
        _node("m_ClearDynamicDataOnBuild", "bool", 1),
        _node("m_IsMultiAtlasTexturesEnabled", "bool", 1)])


def _f32bits(value):
    return struct.unpack("<i", struct.pack("<f", value))[0]


def _synthetic_tmp_mb(name="Test SDF", apm=1, src_pid=999, src_path_len=0,
                      chars=((0xE0, 0), (0x1EAF, 1), (0x110, 2)),
                      char_et=1, fallback_pids=(),
                      atlas_used=((0, 0, 52, 52), (60, 0, 52, 52)),
                      atlas_free=((0, 60, 900, 900),), aw=1024, ah=1024):
    """MB TMP_FontAsset tong hop dung bo cuc nhi phan Kusa/Knightica
    [FIXTURE]: header MB (ten + script ngoai) + FaceInfo cu (pt 36,
    scale 1, khai bao 3 ky tu, atlas 1024x1024) + khoi APM 24B
    [src PPtr][path len][APM][internal] + glyph table 52B x 3 +
    char table 16B + atlas block + duoi fallback [count][PPtr][weight].
    Mac dinh: Dynamic, nguon (0, 999), 3 chu (a, a, D), renderMode 4165.
    """
    b = bytearray()
    b += struct.pack("<i", 0) + struct.pack("<q", 12345)
    b += bytes([1, 0, 0, 0])
    b += struct.pack("<i", 0) + struct.pack("<q", 999)
    nb = name.encode()
    b += struct.pack("<i", len(nb)) + nb
    while len(b) % 4:
        b.append(0)
    fb = b"TestFont"
    b += struct.pack("<i", len(fb)) + fb
    while len(b) % 4:
        b.append(0)
    b += struct.pack("<ff", 36.0, 1.0) + struct.pack("<i", 3)
    rest = [40.0] + [0.0] * 14 + [1024.0, 1024.0]
    b += struct.pack("<%df" % len(rest), *rest)
    b += struct.pack("<i", 0) + struct.pack("<q", src_pid)
    b += struct.pack("<i", src_path_len) + struct.pack("<i", apm)
    b += struct.pack("<i", 0)
    b += struct.pack("<i", 3)
    for gid in range(3):
        b += struct.pack("<I", gid)
        b += struct.pack("<5f", 52.0, 52.0, 0.0, 0.0, 52.0)
        b += struct.pack("<4i", gid * 60, 0, 52, 52)
        b += struct.pack("<f", 1.0) + struct.pack("<i", 0)
        b += struct.pack("<i", 0)
    b += struct.pack("<i", len(chars))
    for u, g in chars:
        b += struct.pack("<III", char_et, u, g) + struct.pack("<f", 1.0)
    b += struct.pack("<i", 1) + struct.pack("<i", 0) + struct.pack("<q", 555)
    b += struct.pack("<4i", 0, 0, 0, 0)
    b += struct.pack("<4i", aw, ah, 9, 4165)
    b += struct.pack("<i", len(atlas_used))
    for r in atlas_used:
        b += struct.pack("<4i", *r)
    b += struct.pack("<i", len(atlas_free))
    for r in atlas_free:
        b += struct.pack("<4i", *r)
    if fallback_pids:
        b += struct.pack("<i", len(fallback_pids))
        for pid in fallback_pids:
            b += struct.pack("<i", 0) + struct.pack("<q", pid)
        b += struct.pack("<i", 2 * len(fallback_pids))
    return bytes(b)


class _FakeType:
    def __init__(self, node):
        self.node = node


class _FakeObj:
    type_id = 0


class _FakeSF:
    endian = "<"

    def __init__(self, node, raw):
        self.types = [_FakeType(node)]
        self._raw = raw

    def get_object(self, path_id):
        return _FakeObj()

    def object_bytes(self, path_id):
        return self._raw


class TestSets(unittest.TestCase):
    def test_134_letters_67_lower_67_upper(self):
        letters = fc.vietnamese_letters_134()
        self.assertEqual(len(letters), 134)
        lower = [c for c in letters if chr(c).islower()]
        upper = [c for c in letters if chr(c).isupper()]
        self.assertEqual(len(lower), 67)
        self.assertEqual(len(upper), 67)
        self.assertEqual(set(letters), _studio_vi_134())

    def test_8_combining_marks_exact(self):
        self.assertEqual(set(fc.combining_marks_8()), MARKS_8)
        self.assertEqual(len(fc.combining_marks_8()), 8)

    def test_134_plus_8_equals_required_142(self):
        self.assertEqual(
            set(fc.vietnamese_letters_134()) | set(fc.combining_marks_8()),
            set(font_sdk.REQUIRED_VIETNAMESE))
        self.assertEqual(len(font_sdk.REQUIRED_VIETNAMESE), 142)


class TestSfntScan(unittest.TestCase):
    def test_donor_ttf_one_blob_at_zero(self):
        data = _donor_bytes()
        blobs = fc.find_sfnt_blobs(data)
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0]["offset"], 0)
        self.assertEqual(blobs[0]["flavor"], "TTF")
        self.assertEqual(blobs[0]["extent"], len(data))

    def test_false_positive_magic_at_field_boundary_rejected(self):
        # Magic 0x00010000 o offset 67 + num_tables 0xFFFF (nhu bay Kusa).
        fake = bytearray(b"X" * 67 + b"\x00\x01\x00\x00"
                         + struct.pack(">H", 0xFFFF) + b"Y" * 100)
        self.assertEqual(fc.find_sfnt_blobs(bytes(fake)), [])

    def test_ttc_header_recognized(self):
        ttc = (b"ttcf" + struct.pack(">I", 0x10000)
               + struct.pack(">I", 1) + struct.pack(">I", 12))
        blobs = fc.find_sfnt_blobs(ttc)
        self.assertEqual(len(blobs), 1)
        self.assertEqual(blobs[0]["flavor"], "TTC")
        self.assertEqual(blobs[0]["num_fonts"], 1)
        self.assertIsNone(blobs[0]["extent"])

    def test_source_font_donor_full(self):
        chk = fc.check_source_font(_donor_bytes(), "BeVietnamPro")
        self.assertEqual(chk["status"], "full")
        self.assertEqual(chk["cmap_letters"], 134)
        self.assertEqual(chk["cmap_marks"], 8)
        self.assertEqual(chk["covered"], 142)
        self.assertEqual(chk["family"], "Be Vietnam Pro")

    def test_source_font_corrupt_unreadable(self):
        chk = fc.check_source_font(b"not a font at all", "X")
        self.assertEqual(chk["status"], "unreadable")
        self.assertIsNotNone(chk.get("error"))


class TestFontObjectExtract(unittest.TestCase):
    def _kusa_style_raw(self):
        # Layout Kusa ar: 92B truoc m_FontData + TTF + 46B duoi, cong
        # magic gia o offset 67.
        raw = bytearray(b"\x00" * 67 + b"\x00\x01\x00\x00"
                        + struct.pack(">H", 0xFFFF) + b"\x00" * 19)
        self.assertEqual(len(raw), 92)
        raw += _donor_bytes()
        raw += b"\x00" * 46
        return bytes(raw)

    def test_extract_finds_real_ttf_not_false_positive(self):
        found = fc.extract_font_data(self._kusa_style_raw())
        self.assertEqual(found["status"], "found")
        self.assertEqual(len(found["blobs"]), 1)
        self.assertEqual(found["blobs"][0]["offset"], 92)
        self.assertEqual(found["blobs"][0]["flavor"], "TTF")
        self.assertEqual(found["blobs"][0]["bytes_len"], 132948)

    def test_extract_no_font_returns_none(self):
        found = fc.extract_font_data(b"\x00" * 500)
        self.assertEqual(found["status"], "none")
        self.assertEqual(found["blobs"], [])

    def test_scan_serialized_file_font_object_readable(self):
        raw = self._kusa_style_raw()
        blob = _build_sf(17, True, _TY,
                         [_obj(40, 128, raw, 2)], scripts=())
        sf = uni.parse_serialized(blob, where="test.assets")
        res = fc.scan_serialized_file(sf, "test.assets")
        self.assertEqual(len(res["fonts"]), 1)
        f = res["fonts"][0]
        self.assertEqual(f["kind"], "unity_font")
        self.assertEqual(f["status"], "full")
        self.assertEqual(f["covered"], 142)
        self.assertEqual(f["cmap_letters"], 134)
        self.assertEqual(f["cmap_marks"], 8)


class TestTmpTypetree(unittest.TestCase):
    def _make_raw(self):
        node = _tmp_typetree_node()
        val = {
            "m_Name": "TestFont SDF",
            "m_FaceInfo": {"m_FamilyName": "TestFont",
                           "m_StyleName": "Bold",
                           "m_PointSize": _f32bits(36.0),
                           "m_AtlasWidth": 1024, "m_AtlasHeight": 1024},
            "m_CharacterTable": [{"m_Unicode": cp} for cp in
                                 (0x41, 0xE0, 0x1EAF, 0x110, 0x301, 0x20AC)],
            "m_GlyphTable": [{"m_Index": i} for i in range(3)],
            "m_AtlasPopulationMode": 1,
            "m_FallbackFontAssetTable": [{"m_FileID": 0, "m_PathID": 123}],
            "m_AtlasTextures": [{"m_FileID": 0, "m_PathID": 456}],
            "m_SourceFontFile": {"m_FileID": 0, "m_PathID": 999},
            "m_ClearDynamicDataOnBuild": 0,
            "m_IsMultiAtlasTexturesEnabled": 0,
        }
        w = uni._Writer("<")  # noqa: SLF001 - codec cua repo
        uni.write_typetree_value(w, node, val, "<")
        return node, w.buf.getvalue()

    def test_read_tmp_asset_typetree(self):
        node, raw = self._make_raw()
        sf = _FakeSF(node, raw)
        tt = fc.read_tmp_asset_typetree(sf, 12345)
        self.assertEqual(tt["status"], "ok")
        self.assertEqual(tt["face_family"], "TestFont")
        self.assertEqual(tt["face_style"], "Bold")
        self.assertAlmostEqual(tt["point_size"], 36.0)
        self.assertEqual(tt["atlas_width"], 1024)
        self.assertEqual(tt["atlas_height"], 1024)
        self.assertEqual(tt["atlas_population_mode"], 1)
        self.assertEqual(tt["population_label"], "Dynamic")
        self.assertEqual(tt["char_table_count"], 6)
        self.assertEqual(tt["glyph_table_count"], 3)
        self.assertEqual(tt["atlas_letters"], 3)  # à, ắ, Đ
        self.assertEqual(tt["atlas_marks"], 1)    # U+0301
        self.assertEqual(tt["fallback_count"], 1)
        self.assertEqual(tt["source_font_file"],
                         {"file_id": 0, "path_id": 999})
        self.assertEqual(tt["clear_dynamic_on_build"], 0)
        self.assertEqual(tt["multi_atlas_enabled"], 0)

    def test_read_no_typetree(self):
        node, raw = self._make_raw()
        sf = _FakeSF(None, raw)
        tt = fc.read_tmp_asset_typetree(sf, 1)
        self.assertEqual(tt["status"], "no_typetree")


class TestIdentify(unittest.TestCase):
    def test_script_confirmed(self):
        blob = _sf_with_assets([(100, "TestFont SDF",
                                 dict(face="TestFont", ids=[0x41, 0xE0]))])
        sf = uni.parse_serialized(blob, where="test.assets")
        ident = fc.identify_tmp_assets(sf)
        by_pid = {c["path_id"]: c["method"] for c in ident["confirmed"]}
        self.assertEqual(by_pid.get(100), "script")
        self.assertEqual(ident["unconfirmed"], [])

    def _sf_no_scripts(self):
        # SF chi co TMP asset, KHONG co MonoScript object nao (script
        # nam ngoai file nhu build release — Kusa 209.858 MB nhu vay).
        data = _tmp_asset("TestFont SDF", "<",
                          face="TestFont", ids=[0x41, 0xE0])
        blob = _build_sf(17, True, _TY, [_obj(100, 114, data, 5)],
                         scripts=())
        return uni.parse_serialized(blob, where="test.assets")

    def test_typetree_confirmed_when_script_external(self):
        sf = self._sf_no_scripts()
        # Script ngoai file: khong resolve duoc -> (a) that bai, (b) cuu.
        self.assertEqual(fc.identify_tmp_assets(sf)["confirmed"], [])
        sf.types[5].node = _tmp_typetree_node()
        ident = fc.identify_tmp_assets(sf)
        by_pid = {c["path_id"]: c["method"] for c in ident["confirmed"]}
        self.assertEqual(by_pid.get(100), "typetree")

    def test_heuristic_unconfirmed(self):
        sf = self._sf_no_scripts()
        ident = fc.identify_tmp_assets(sf)
        self.assertEqual(ident["confirmed"], [])
        self.assertEqual(len(ident["unconfirmed"]), 1)
        self.assertEqual(ident["unconfirmed"][0]["method"], "heuristic")
        self.assertEqual(ident["unconfirmed"][0]["path_id"], 100)


class TestBinaryTmp(unittest.TestCase):
    def test_binary_parse_ok(self):
        # [FIXTURE] MB tong hop dung layout Kusa/Knightica.
        raw = _synthetic_tmp_mb(fallback_pids=(2001, 2002))
        r = fc.read_tmp_asset_binary(raw, "<",
                                     tmp_path_ids={2001, 2002, 7777})
        self.assertEqual(r["status"], "ok", r.get("error"))
        self.assertEqual(r["atlas_letters"], 3)  # à, ắ, Đ
        self.assertEqual(r["atlas_marks"], 0)
        self.assertEqual(r["atlas_population_mode"], 1)
        self.assertEqual(r["population_label"], "Dynamic")
        self.assertEqual(r["source_font_file"],
                         {"file_id": 0, "path_id": 999})
        self.assertEqual(r["atlas_width"], 1024)
        self.assertEqual(r["atlas_height"], 1024)
        self.assertEqual(r["atlas_padding"], 9)
        self.assertEqual(r["atlas_render_mode"], 4165)  # bitfield, khong
        # phai enum 0..4 (Kusa ja + Knightica deu 4165) — doc, khong
        # validate.
        self.assertEqual(r["fallback_count"], 2)
        self.assertTrue(r["fallback_structural"])
        self.assertEqual(r["fallback_entries"],
                         [{"file_id": 0, "path_id": 2001},
                          {"file_id": 0, "path_id": 2002}])
        self.assertEqual(r["used_area_pct"], 0.5)
        self.assertEqual(r["median_glyph_rect"], [52.0, 52.0])
        self.assertEqual(r["free_rects_fit_median_glyph"], 1)
        self.assertEqual(r["free_rects_fit_40x70"], 1)
        self.assertEqual(r["largest_free_rect"], [900, 900])

    def test_binary_invalid_char_record_rejected(self):
        raw = _synthetic_tmp_mb(char_et=2)
        r = fc.read_tmp_asset_binary(raw, "<")
        self.assertEqual(r["status"], "error")
        self.assertIn("char table", r["error"])

    def test_binary_source_path_nonempty_rejected(self):
        raw = _synthetic_tmp_mb(src_path_len=5)
        r = fc.read_tmp_asset_binary(raw, "<")
        self.assertEqual(r["status"], "error")
        self.assertIn("SourceFontFilePath khong rong", r["error"])

    def test_binary_static_apm(self):
        raw = _synthetic_tmp_mb(apm=0)
        r = fc.read_tmp_asset_binary(raw, "<")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["population_label"], "Static")

    def test_binary_identify_in_sf(self):
        # Khong typetree, khong script -> (c2) binary xac nhan.
        raw = _synthetic_tmp_mb()
        blob = _build_sf(17, True, _TY, [_obj(7777, 114, raw, 5)],
                         scripts=())
        sf = uni.parse_serialized(blob, where="test.assets")
        ident = fc.identify_tmp_assets(sf)
        by_pid = {c["path_id"]: c["method"] for c in ident["confirmed"]}
        self.assertEqual(by_pid.get(7777), "binary")
        self.assertEqual(ident["unconfirmed"], [])
        res = fc.scan_serialized_file(sf, "test.assets")
        self.assertEqual(len(res["tmp_assets"]), 1)
        t = res["tmp_assets"][0]
        self.assertTrue(t["confirmed"])
        self.assertEqual(t["method"], "binary")
        self.assertEqual(t["atlas_letters"], 3)
        self.assertEqual(t["atlas_used_pct"], 0.5)
        self.assertEqual(t["free_rects_fit_median_glyph"], 1)
        # Fallback tro toi 2001/2002 khong co trong SF nay -> 0
        # (chi giai quyet cung file).
        self.assertEqual(t["fallback_count"], 0)


class TestRectStats(unittest.TestCase):
    def test_atlas_rect_stats(self):
        st = fc._atlas_rect_stats([(0, 0, 52, 52), (60, 0, 52, 52)],
                                  [(0, 60, 900, 900)], 1024, 1024)
        self.assertEqual(st["used_glyph_rects"], 2)
        self.assertEqual(st["free_glyph_rects"], 1)
        self.assertEqual(st["used_area_px"], 5408)
        self.assertEqual(st["used_area_pct"], 0.5)
        self.assertEqual(st["median_glyph_rect"], [52.0, 52.0])
        self.assertEqual(st["free_rects_fit_median_glyph"], 1)
        self.assertEqual(st["free_rects_fit_40x70"], 1)
        self.assertEqual(st["largest_free_rect"], [900, 900])

    def test_atlas_rect_stats_empty(self):
        st = fc._atlas_rect_stats([], [], 1024, 1024)
        self.assertEqual(st["used_area_px"], 0)
        self.assertEqual(st["used_area_pct"], 0.0)
        self.assertIsNone(st["median_glyph_rect"])
        self.assertIsNone(st["free_rects_fit_median_glyph"])
        self.assertEqual(st["largest_free_rect"], [0, 0])


class TestDynamicRescue(unittest.TestCase):
    def _atlas(self, **kw):
        a = {"letters": 24, "population_label": "Dynamic",
             "source_font_file": {"file_id": 0, "path_id": 1705},
             "median_glyph_rect": [52, 52],
             "atlas_width": 1024, "atlas_height": 1024,
             "used_area_px": 967442,
             "free_rects_fit_median_glyph": 0,
             "atlas_used_pct": 92.2, "multi_atlas_enabled": 0}
        a.update(kw)
        return a

    def test_knightica_like_full_atlas_no_rescue(self):
        # [ĐÃ ĐO THẬT] SSP-Bold Knightica: Dynamic + nguon 134/134
        # nhung atlas dung 92.2%, suc chua ~30 glyph < 110 chu thieu.
        rescue, note = fc._dynamic_rescue(self._atlas(), 134,
                                          "SourceSansPro-Bold")
        self.assertIs(rescue, False)
        self.assertIn("KHONG cuu duoc", note)

    def test_kusa_like_empty_atlas_rescue(self):
        # [ĐÃ ĐO THẬT] Kusa ja: Dynamic, atlas 2048x2048 dung 77.3%,
        # suc chua ~352 glyph > 129 chu thieu [HYPOTHESIS].
        a = self._atlas(letters=5, atlas_width=2048, atlas_height=2048,
                        used_area_px=3242196, atlas_used_pct=77.3,
                        free_rects_fit_median_glyph=26,
                        source_font_file={"file_id": 0, "path_id": 1})
        rescue, note = fc._dynamic_rescue(a, 134, "NotoSansJP-Bold")
        self.assertIs(rescue, True)
        self.assertIn("[HYPOTHESIS", note)

    def test_static_no_rescue(self):
        rescue, note = fc._dynamic_rescue(
            self._atlas(population_label="Static"), 134, "X")
        self.assertIs(rescue, False)
        self.assertIn("Static", note)

    def test_dynamic_no_source_no_rescue(self):
        rescue, note = fc._dynamic_rescue(
            self._atlas(source_font_file=None), 134, "X")
        self.assertIs(rescue, False)
        self.assertIn("khong co font nguon", note)

    def test_source_capped_by_cmap(self):
        # Atlas rong nhung nguon chi ve duoc 38/134 chu (Sen Knightica)
        # -> suc chua bi gioi han boi cmap nguon, khong cuu duoc.
        a = self._atlas(letters=0, used_area_px=1000, atlas_used_pct=0.1,
                        free_rects_fit_median_glyph=12,
                        source_font_file={"file_id": 0, "path_id": 1708})
        rescue, note = fc._dynamic_rescue(a, 38, "Sen-ExtraBold")
        self.assertIs(rescue, False)

    def test_no_data_returns_none(self):
        self.assertEqual(fc._dynamic_rescue(
            self._atlas(letters=None), 134, "X"), (None, ""))
        self.assertEqual(fc._dynamic_rescue(
            self._atlas(letters=134), 134, "X"), (None, ""))
        self.assertEqual(fc._dynamic_rescue(
            self._atlas(population_label=None), 134, "X"), (None, ""))


class TestRecommendation(unittest.TestCase):
    def _tmp(self, name, pid, letters, marks=0, fallback_entries=None,
             population_label="Static"):
        unis = sorted(fc.VIETNAMESE_LETTERS_134)[:letters]
        cov = fc.atlas_coverage(set(unis))
        return {"kind": "tmp_font_asset", "name": name, "path_id": pid,
                "source": "test.assets", "status": "partial",
                "covered": cov["covered"],
                "missing_codepoints": sorted(cov["letters_missing"]
                                             + cov["marks_missing"]),
                "total": 142, "confirmed": True, "method": "binary",
                "atlas_letters": letters, "atlas_marks": marks,
                "population_label": population_label,
                "fallback_count": len(fallback_entries or []),
                "fallback_entries": fallback_entries or [],
                "fallback_structural": True, "has_fallback": bool(
                    fallback_entries),
                "source_font_file": None, "atlas_width": 1024,
                "atlas_height": 1024, "atlas_used_pct": 90.0,
                "used_area_px": 900000,
                "free_rects_fit_median_glyph": 0,
                "median_glyph_rect": [52, 52],
                "multi_atlas_enabled": 0}

    def _font(self, name, pid, cmap_letters=134):
        return {"kind": "unity_font", "name": name, "path_id": pid,
                "source": "test.assets",
                "status": "full" if cmap_letters == 134 else "partial",
                "covered": 142 if cmap_letters == 134 else 40,
                "missing_codepoints": [] if cmap_letters == 134 else [0xE0],
                "total": 142, "cmap_letters": cmap_letters,
                "cmap_marks": 8 if cmap_letters == 134 else 0}

    def test_recommendation_names_full_source_in_ipa(self):
        rep = fc.build_gate_report([
            self._tmp("Latin SDF", 1, 32),
            self._font("LiberationSans", 1705)])
        self.assertIn("LiberationSans", rep["recommendation"])
        self.assertIn("134/134", rep["recommendation"])

    def test_recommendation_falls_back_to_donor(self):
        rep = fc.build_gate_report([
            self._tmp("Latin SDF", 1, 32),
            self._font("Partial", 1705, cmap_letters=38)])
        self.assertIn("Be Vietnam Pro", rep["recommendation"])

    def test_fallback_useless_note(self):
        # Fallback tro toi 2 TMP deu 0/134 chu -> chi cuu CJK.
        rep = fc.build_gate_report([
            self._tmp("Latin SDF", 1, 32, fallback_entries=[
                {"file_id": 0, "path_id": 2},
                {"file_id": 0, "path_id": 3}]),
            self._tmp("Noto A SDF", 2, 0),
            self._tmp("Noto B SDF", 3, 0),
            self._font("LiberationSans", 1705)])
        self.assertTrue(any("chi cuu duoc chu CJK" in n
                            for n in rep["notes"]))

    def test_fallback_unknown_target_no_note(self):
        # Target chua biet atlas (None) thi KHONG ket luan vo dung
        # (None != 0) — fail-closed.
        t = self._tmp("Latin SDF", 1, 32, fallback_entries=[
            {"file_id": 0, "path_id": 2}])
        u = self._tmp("Noto A SDF", 2, 0)
        u["atlas_unknown"] = True
        u["covered"] = None
        rep = fc.build_gate_report(
            [t, u, self._font("LiberationSans", 1705)])
        self.assertFalse(any("chi cuu duoc chu CJK" in n
                             for n in rep["notes"]))


class TestGateIntegration(unittest.TestCase):
    VI = {"a": "Xin chào", "b": "Độ khó", "c": "Chơi"}

    def _fonts_9k(self):
        return [
            {"kind": "unity_font", "name": "LiberationSans",
             "family": "LiberationSans", "source": "resources.assets",
             "status": "full", "covered": 142, "total": 142,
             "missing_codepoints": [], "missing": [], "missing_total": 0,
             "reason": "", "num_glyphs": 500},
            {"kind": "unity_font", "name": "PerfectDOSVGA437",
             "family": "PerfectDOSVGA437", "source": "resources.assets",
             "status": "partial", "covered": 32, "total": 142,
             "missing_codepoints": [0xE0, 0x1ED9, 0x110],
             "missing": [], "missing_total": 3,
             "reason": "", "num_glyphs": 256},
        ]

    def test_3step_block_present_and_verdict_unchanged(self):
        fonts = self._fonts_9k()
        plain = gate.check_font_coverage(self.VI, fonts, target_lang="vi")
        rep = fc.build_gate_report(fonts)
        enriched = gate.check_font_coverage(
            self.VI, fonts, target_lang="vi", font_check=rep)
        self.assertEqual(plain["status"], enriched["status"])
        self.assertEqual(plain["status"], "fail")  # PerfectDOS thieu chu dung
        self.assertEqual(enriched["font_check_3step"]["path"], "font_cmap")
        self.assertEqual(plain["font_check_3step"]["path"], "font_cmap")

    def test_donor_pass_with_3step(self):
        donor = gate.font_entry_from_bytes(
            _donor_bytes(), "BeVietnamPro-Regular.ttf")
        d = gate.check_font_coverage(
            self.VI, self._fonts_9k(), display_fonts=[donor],
            target_lang="vi")
        self.assertEqual(d["status"], "pass")
        self.assertEqual(d["font_check_3step"]["path"], "donor")

    def test_zero_fonts_donor_path(self):
        d = gate.check_font_coverage(self.VI, [], target_lang="vi")
        self.assertEqual(d["status"], "fail")  # fail-closed cu, scan mode
        self.assertEqual(d["font_check_3step"]["path"], "donor")

    def test_build_gate_report_paths(self):
        tmp = {"kind": "tmp_font_asset", "name": "Noto Sans SDF",
               "status": "partial", "covered": 32, "total": 142,
               "missing_codepoints": [0xE0], "confirmed": True,
               "population_label": "Dynamic", "fallback_count": 0,
               "method": "typetree"}
        self.assertEqual(fc.build_gate_report([tmp])["path"], "tmp_atlas")
        self.assertEqual(
            fc.build_gate_report([tmp] + self._fonts_9k())["path"], "mixed")
        self.assertEqual(
            fc.build_gate_report(self._fonts_9k())["path"], "font_cmap")
        self.assertEqual(fc.build_gate_report([])["path"], "donor")


class TestBridgeOp(unittest.TestCase):
    def test_font_check_3step_on_synthetic_assets(self):
        raw = (b"\x00" * 92 + _donor_bytes() + b"\x00" * 46)
        blob = _build_sf(17, True, _TY,
                         [_obj(40, 128, raw, 2)], scripts=())
        tmp = Path(__file__).resolve().parent / "_fc3_tmp.assets"
        tmp.write_bytes(blob)
        try:
            res = json.loads(bridge.font_check_3step(str(tmp)))
            self.assertTrue(res["ok"], res.get("error"))
            self.assertEqual(res["schema"], "font_check/1")
            self.assertEqual(len(res["fonts"]), 1)
            self.assertEqual(res["fonts"][0]["status"], "full")
            self.assertEqual(res["fonts"][0]["cmap_letters"], 134)
            self.assertEqual(res["report"]["path"], "font_cmap")
            # Khong co TMP (best_atlas None) -> khong co de xuat
            # fallback; chi can co key report + tiers.
            self.assertIsNone(res["report"]["recommendation"])
            self.assertIn("tiers", res["report"])
        finally:
            tmp.unlink(missing_ok=True)


class TestRealGames(unittest.TestCase):
    @slow
    def test_kusa_7_tmp_8_fonts(self):
        # [ĐÃ ĐO THẬT] Kusa: 8 Font object + 7 TMP confirmed
        # (6 typetree + 1 binary #130 o sharedassets0).
        if not KUSA_IPA.exists():
            raise unittest.SkipTest("thieu IPA Kusa: %s" % KUSA_IPA)
        res = fc.scan_source(str(KUSA_IPA))
        s = res["summary"]
        self.assertEqual(s["fonts"], 8)
        self.assertEqual(s["font_objects_readable"], 8)
        self.assertEqual(s["tmp_confirmed"], 7)
        self.assertEqual(s["tmp_unconfirmed"], 0)
        self.assertEqual(s["best_atlas_letters"], 32)
        self.assertEqual(s["best_cmap_letters"], 134)
        self.assertEqual(s["dynamic_count"], 7)
        self.assertEqual(s["no_fallback_count"], 7)
        by_name = {f["name"]: f for f in res["fonts"]}
        self.assertEqual(by_name["NotoSans-Bold"]["cmap_letters"], 134)
        self.assertEqual(by_name["NotoSans-Bold"]["cmap_marks"], 8)
        self.assertEqual(by_name["NotoSansJP-Bold"]["cmap_letters"], 134)
        self.assertEqual(by_name["NotoSansJP-Bold"]["cmap_marks"], 2)
        self.assertEqual(by_name["NotoSansArabic-Bold"]["cmap_letters"], 38)
        tmp_by_name = {t["name"]: t for t in res["tmp_assets"]}
        # TMP thu 7: binary, cung atlas voi ja (77.3%, 26 free rect).
        t130 = tmp_by_name["NotoSansJP-Bold SDF"]
        self.assertEqual(t130["method"], "binary")
        self.assertEqual(t130["atlas_letters"], 5)
        self.assertEqual(t130["atlas_used_pct"], 77.3)
        self.assertEqual(t130["free_rects_fit_median_glyph"], 26)
        rep = fc.build_gate_report(res["fonts"] + res["tmp_assets"])
        self.assertEqual(rep["path"], "mixed")
        self.assertIs(rep["summary"]["dynamic_rescue"], True)
        self.assertTrue(any("Dynamic co the sinh" in n
                            for n in rep["notes"]))
        self.assertTrue(any("Dynamic KHONG cuu duoc" in n
                            for n in rep["notes"]))
        self.assertIn("NotoSans-Bold", rep["recommendation"])
        tiers = {t["name"]: t for t in rep["tiers"]}
        # Shared: atlas dung 92.0%, Dynamic KHONG cuu duoc.
        self.assertEqual(tiers["Noto Sans"]["atlas"]["atlas_used_pct"], 92.0)
        self.assertIs(tiers["Noto Sans"]["atlas"]["dynamic_rescue"], False)
        # ja: Dynamic co the cuu [HYPOTHESIS].
        self.assertIs(tiers["Noto Sans JP"]["atlas"]["dynamic_rescue"], True)

    @slow
    def test_9k_fonts_readable(self):
        # [ĐÃ ĐO THẬT] 9K: 3 Font object doc duoc cmap (2 full) +
        # LiberationSans SDF xac nhan bang binary (atlas 32, Static,
        # dung 96.0%) + 1 TMP placeholder chua xac nhan (#15333).
        if not NINEK_IPA.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % NINEK_IPA)
        res = fc.scan_source(str(NINEK_IPA))
        s = res["summary"]
        self.assertEqual(s["font_objects"], 3)
        self.assertEqual(s["font_objects_readable"], 3)
        self.assertEqual(s["tmp_confirmed"], 1)
        self.assertEqual(s["tmp_unconfirmed"], 1)
        self.assertEqual(s["best_atlas_letters"], 32)
        by_name = {f["name"]: f for f in res["fonts"]}
        self.assertEqual(by_name["LiberationSans"]["status"], "full")
        self.assertEqual(by_name["NotInter-Regular"]["status"], "full")
        self.assertEqual(by_name["PerfectDOSVGA437"]["cmap_letters"], 32)
        tmp_by_name = {t["name"]: t for t in res["tmp_assets"]}
        lib = tmp_by_name["LiberationSans SDF"]
        self.assertEqual(lib["method"], "binary")
        self.assertEqual(lib["atlas_letters"], 32)
        self.assertEqual(lib["population_label"], "Static")
        self.assertEqual(lib["atlas_used_pct"], 96.0)
        self.assertEqual(lib["free_rects_fit_median_glyph"], 1)
        self.assertEqual(lib["fallback_count"], 1)
        self.assertEqual(fc.build_gate_report(
            res["fonts"] + res["tmp_assets"])["path"], "mixed")

    @slow
    def test_knightica_fonts_readable(self):
        # [ĐÃ ĐO THẬT] Knightica v1.0.3 (game Unity thu 7):
        # 8 Font object doc duoc cmap (5 full 134/134+8/8) + 13 TMP
        # confirmed bang binary + 1 TMP placeholder chua xac nhan
        # (#3184). Atlas tot nhat 32/134; Dynamic KHONG cuu duoc
        # (atlas day 91-95%); 4 font Latin co fallback toi 5 TMP Noto
        # (chi cuu CJK). Duong mixed, rescue False.
        if not KNIGHTICA_IPA.exists():
            raise unittest.SkipTest("thieu IPA Knightica: %s" % KNIGHTICA_IPA)
        res = fc.scan_source(str(KNIGHTICA_IPA))
        s = res["summary"]
        self.assertEqual(s["files_scanned"], 23)
        self.assertEqual(s["fonts"], 8)
        self.assertEqual(s["font_objects"], 8)
        self.assertEqual(s["font_objects_readable"], 8)
        self.assertEqual(s["tmp_assets"], 14)
        self.assertEqual(s["tmp_confirmed"], 13)
        self.assertEqual(s["tmp_unconfirmed"], 1)
        self.assertEqual(s["best_atlas_letters"], 32)
        self.assertEqual(s["best_cmap_letters"], 134)
        self.assertEqual(s["dynamic_count"], 7)
        self.assertEqual(s["no_fallback_count"], 9)
        self.assertEqual(s["tmp_with_fallback"], 4)
        by_name = {f["name"]: f for f in res["fonts"]}
        self.assertEqual(by_name["LiberationSans"]["status"], "full")
        self.assertEqual(by_name["LiberationSans"]["cmap_marks"], 8)
        self.assertEqual(by_name["SourceSansPro-Bold"]["status"], "full")
        self.assertEqual(by_name["Platypi"]["status"], "full")
        self.assertEqual(by_name["Sen-ExtraBold"]["cmap_letters"], 38)
        self.assertEqual(by_name["PerfectDOSVGA437"]["cmap_letters"], 32)
        self.assertEqual(by_name["pocket_pixel"]["cmap_letters"], 0)
        tmp_by_name = {t["name"]: t for t in res["tmp_assets"]}
        # 4 font Latin co fallback toi cung 5 TMP Noto.
        for nm in ("Platypi SDF", "SourceSansPro-Bold SDF",
                   "SourceSansPro-BoldIt SDF", "SourceSansPro-Semibold SDF"):
            self.assertEqual(tmp_by_name[nm]["fallback_count"], 5, nm)
        # SSP-Bold: Dynamic nhung atlas dung 92.2%, 0 free rect.
        ssp = tmp_by_name["SourceSansPro-Bold SDF"]
        self.assertEqual(ssp["atlas_letters"], 24)
        self.assertEqual(ssp["population_label"], "Dynamic")
        self.assertEqual(ssp["atlas_used_pct"], 92.2)
        self.assertEqual(ssp["free_rects_fit_median_glyph"], 0)
        # TMP thu 13 (check tay bo sot): pocket_pixel SDF.
        self.assertTrue(tmp_by_name["pocket_pixel SDF"]["confirmed"])
        self.assertEqual(tmp_by_name["pocket_pixel SDF"]["method"], "binary")
        # Placeholder #3184 giu unconfirmed (fail-closed).
        self.assertEqual(tmp_by_name["TMP #3184"]["confirmed"], False)
        rep = fc.build_gate_report(res["fonts"] + res["tmp_assets"])
        self.assertEqual(rep["path"], "mixed")
        self.assertIs(rep["summary"]["dynamic_rescue"], False)
        self.assertTrue(any("Dynamic KHONG cuu duoc" in n
                            for n in rep["notes"]))
        self.assertTrue(any("chi cuu duoc chu CJK" in n
                            for n in rep["notes"]))
        self.assertIn("LiberationSans", rep["recommendation"])

    @slow
    def test_ek2_fonts_readable(self):
        # [ĐÃ ĐO THẬT] EK2: 7 Font object + 1 font roi Roboto-Bold.ttf
        # trong GoogleSignIn.bundle; 11 TMP chi heuristic (layout TMP
        # cu, stride 36 — binary tu choi, fail-closed).
        if not EK2_IPA.exists():
            raise unittest.SkipTest("thieu IPA EK2: %s" % EK2_IPA)
        res = fc.scan_source(str(EK2_IPA))
        s = res["summary"]
        self.assertEqual(s["fonts"], 8)
        self.assertEqual(s["font_objects"], 7)
        self.assertEqual(s["font_objects_readable"], 7)
        self.assertEqual(s["tmp_confirmed"], 0)
        self.assertEqual(s["tmp_unconfirmed"], 11)
        self.assertEqual(s["best_cmap_letters"], 134)
        kinds = {(f["name"], f["kind"]) for f in res["fonts"]}
        self.assertIn(("Roboto", "ttf"), kinds)
        self.assertEqual(fc.build_gate_report(
            res["fonts"] + res["tmp_assets"])["path"], "font_cmap")

    @slow
    def test_pb3_consola_full(self):
        # [ĐÃ ĐO THẬT] PB3: resources.assets 59MB nay doc duoc
        # (gioi han 256MB) -> 16 Font object + 43 TMP (chi heuristic,
        # layout TMP cu stride 36 — binary tu choi, fail-closed).
        if not PB3_IPA.exists():
            raise unittest.SkipTest("thieu IPA PB3: %s" % PB3_IPA)
        res = fc.scan_source(str(PB3_IPA))
        s = res["summary"]
        self.assertEqual(s["files_scanned"], 303)
        self.assertEqual(s["fonts"], 16)
        self.assertEqual(s["font_objects"], 16)
        self.assertEqual(s["font_objects_readable"], 16)
        self.assertEqual(s["tmp_confirmed"], 0)
        self.assertEqual(s["tmp_unconfirmed"], 43)
        self.assertEqual(s["best_cmap_letters"], 134)
        by_name = {f["name"]: f for f in res["fonts"]}
        self.assertEqual(by_name["CONSOLA"]["status"], "full")
        self.assertEqual(by_name["Roboto-Regular"]["cmap_letters"], 134)
        self.assertEqual(by_name["LiberationSans"]["status"], "full")
        self.assertEqual(fc.build_gate_report(
            res["fonts"] + res["tmp_assets"])["path"], "font_cmap")

    @slow
    def test_tom_zero_fonts_donor_path(self):
        # [ĐÃ ĐO THẬT] Tom: data.unity3d 78MB nay doc duoc (gioi han
        # 256MB, truoc day >32MB bi bo qua) — van 0 font -> donor.
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        res = fc.scan_source(str(TOM_IPA))
        self.assertEqual(res["summary"]["files_scanned"], 1)
        self.assertEqual(res["summary"]["fonts"], 0)
        self.assertEqual(fc.build_gate_report(
            res["fonts"] + res["tmp_assets"])["path"], "donor")

    @slow
    def test_enrich_9k_matches_scan(self):
        # [ĐÃ ĐO THẬT] enrich (duong cong dung) = scan day du tren 9K.
        if not NINEK_IPA.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % NINEK_IPA)
        from sdk import font_list as fl
        fonts = fl.scan_source(str(NINEK_IPA))["fonts"]
        self.assertTrue(all(f["status"] == "unreadable" for f in fonts))
        enriched, rep = fc.enrich_fonts(str(NINEK_IPA), fonts)
        self.assertTrue(all(f["status"] in ("full", "partial")
                            for f in enriched))
        by_name = {f["name"]: f for f in enriched}
        self.assertEqual(by_name["LiberationSans"]["covered"], 142)
        self.assertEqual(by_name["PerfectDOSVGA437"]["covered"], 32)
        self.assertEqual(rep["path"], "font_cmap")
