"""Test khoa cho doc TMP_FontAsset (muc 7, sdk/tmp_font.py).

Fixture SerializedFile v17 dung THU CONG (builder rut gon tu
vendor/ipa-localization-studio/.../tests/make_tmp_ipas.py, MIT, tac gia
Joseph) + ban tai dung tu BYTE THAT DATA WING (tmp_real_xolonium.bin,
hex goc trong make_tmp_ipas.py). Khong dung file game that, khong can
mang. Bo cuc TMP (FaceInfo + bang glyph 36 byte) la gia dinh cua studio
— test chung minh Python khop gia dinh do + khop dap an doc lap, khong
chung minh dung voi TextMesh Pro that (can 9 Kings that).
"""
from __future__ import annotations

import io
import struct
import unicodedata
import zipfile
from pathlib import Path

from sdk import font as fs
from sdk import tmp_font as tf
from sdk import unity as uni
from tests.helpers import TmpCase

ROOT = Path(__file__).resolve().parent.parent


def _align(n, a):
    return (n + a - 1) // a * a


def _astr(s, e):
    return struct.pack(e + "i", len(s)) + s + b"\0" * (_align(len(s), 4) - len(s))


def _monoscript(name, cn, ns, asm, e):
    return (_astr(name.encode(), e) + struct.pack(e + "i", 0) + bytes(16)
            + _astr(cn.encode(), e) + _astr(ns.encode(), e)
            + _astr(asm.encode(), e) + b"\1")


def _pptr(file_id, pid, e):
    return struct.pack(e + "iq", file_id, pid)


def _glyphs_new(ids, e, aw=1024, ah=1024, ph_ids=()):
    """Bang glyph layout MOI (52 byte/record): id=glyph index (u32) +
    5 float metrics + rect 4 int (x, y tu BOTTOM, w, h) + scale +
    atlasIndex + trailing."""
    ids = list(ids)
    b = b""
    for i, gid in enumerate(ids):
        x, yb, w, h = (i % 30) * 32, (i // 30) * 32 % (ah - 32), 30, 30
        if gid in ph_ids:
            w, h = 0, 0
            x, yb = 0, 0
            b += struct.pack(e + "I5f4ifii", gid, 0.0, 0.0, 0.0, 0.0,
                             20.0, 0, 0, 0, 0, 1.0, 0, 0)
        else:
            b += struct.pack(e + "I5f4ifii", gid, float(w), float(h), 1.5,
                             24.0, 20.0, int(x), int(yb), int(w), int(h),
                             1.0, 0, 0)
    return struct.pack(e + "i", len(ids)) + b


def _glyphs(ids, e, aw=1024, ah=1024, ph_ids=()):
    ids = list(ids)
    b = b""
    for i, cp in enumerate(ids):
        x, y, w, h = (i % 30) * 32, (i // 30) * 32 % (ah - 32), 30, 30
        if cp in ph_ids:
            x, y, w, h = 4, ah + 3, (0 if cp == 10 else 8.8125), \
                (0 if cp == 10 else 24.625)
        b += struct.pack(e + "i8f", cp, float(x), float(y), float(w),
                         float(h), 1.5, 24.0, 20.0, 1.0)
    return struct.pack(e + "i", len(ids)) + b


def _faceinfo(name, e, pt=36.0, count=0, aw=1024.0, ah=1024.0, legacy=False):
    vals = [pt, 1.0]
    after = [48.0, 36.0, 30.0, 25.0, -9.0, 15.0, 10.0, -10.0, 8.0, -12.0,
             3.0] + ([] if legacy else [14.0, 3.0]) + [16.0, 6.0, aw, ah]
    return (_astr(name.encode(), e) + struct.pack(e + "ff", *vals)
            + struct.pack(e + "i", count)
            + struct.pack(e + "%df" % len(after), *after))


def _faceinfo_new(family, style, e, pt=87, count=0, lh=118.78):
    """FaceInfo MOI (TMP 3.x): 2 chuoi (family + style) + pointSize INT
    + scale float + characterCount int + lineHeight/ascender/capHeight.
    Khong co padding/atlas (xem sdk/tmp_font.find_face_info_new)."""
    return (_astr(family.encode(), e) + _astr(style.encode(), e)
            + struct.pack(e + "i", pt) + struct.pack(e + "f", 1.0)
            + struct.pack(e + "i", count)
            + struct.pack(e + "fff", lh, 92.97, 62.0))


def _tmp_asset(name, e, script_pid=5, face=None, ids=None, declared=None,
               atlas_pid=20, mat_pid=30, font_pid=40, no_glyphs=False,
               aw=1024.0, ah=1024.0, legacy_fi=False, ph_ids=(),
               layout="old", face_new=None):
    hdr = _pptr(0, 0, e) + b"\1\0\0\0" + _pptr(0, script_pid, e) \
        + _astr(name.encode(), e)
    body = struct.pack(e + "i", 1)
    if font_pid:
        body += _pptr(0, font_pid, e)
    if face_new is not None:
        # FaceInfo moi (TMP 3.x), khong co ban sao cu. characterCount
        # phai khop so glyph (neu khong decode tra 'partial').
        fam, sty = face_new
        body += _faceinfo_new(
            fam, sty, e,
            count=declared if declared is not None else len(ids or []))
    else:
        body += _faceinfo(face, e, count=declared if declared is not None
                          else len(ids or []), aw=aw, ah=ah, legacy=legacy_fi)
    if atlas_pid:
        body += _pptr(0, atlas_pid, e)
    if layout == "new":
        body += (bytes(2000) if no_glyphs
                 else _glyphs_new(ids, e, aw=int(aw), ah=int(ah),
                                  ph_ids=ph_ids))
    else:
        body += (bytes(2000) if no_glyphs
                 else _glyphs(ids, e, aw=int(aw), ah=int(ah), ph_ids=ph_ids))
    if mat_pid:
        body += _pptr(0, mat_pid, e)
    return hdr + body + bytes(96)


def _tex(name, e):
    return _astr(name.encode(), e) + bytes(40)


def _build_sf(version=17, le=True, types=None, objs=None, scripts=(),
              unity="5.6.5p2"):
    e = "<" if le else ">"
    objs = objs or []
    hdr_len = 20
    m = bytearray()

    def pos():
        return hdr_len + len(m)

    def al4():
        while pos() % 4:
            m.append(0)

    m += unity.encode() + b"\0"
    m += struct.pack(e + "i", 9)
    m.append(0)
    m += struct.pack(e + "i", len(types))
    for t in types:
        m += struct.pack(e + "i", t["cid"])
        m.append(0)
        m += struct.pack(e + "h", t["sti"])
        if t["cid"] == 114:
            m += bytes(16)
        m += bytes(16)
    m += struct.pack(e + "i", len(objs))
    off = 0
    starts = []
    for o in objs:
        starts.append(off)
        off = _align(off + len(o["data"]), 8)
    for o, st in zip(objs, starts):
        al4()
        m += struct.pack(e + "q", o["pid"])
        m += struct.pack(e + "I", st)
        m += struct.pack(e + "I", len(o["data"]))
        m += struct.pack(e + "i", o["tidx"])
    m += struct.pack(e + "i", len(scripts))
    for fi, pid in scripts:
        m += struct.pack(e + "i", fi)
        al4()
        m += struct.pack(e + "q", pid)
    m += struct.pack(e + "i", 0)
    m.append(0)
    data_off = _align(hdr_len + len(m), 16)
    total = data_off + off
    head = struct.pack(">IIII", len(m), total, version, data_off) \
        + bytes([0 if le else 1]) + b"\0\0\0"
    blob = bytearray(head + bytes(m)
                     + b"\0" * (data_off - hdr_len - len(m)))
    for o in objs:
        blob += o["data"] + b"\0" * (_align(len(o["data"]), 8) - len(o["data"]))
    return bytes(blob)


def _obj(pid, cid, data, tidx):
    return dict(pid=pid, cid=cid, data=data, tidx=tidx)


_SA = ("TMP_FontAsset", "TMP_FontAsset", "TMPro", "Unity.TextMeshPro")
_SB = ("TextMeshProUGUI", "TextMeshProUGUI", "TMPro", "Unity.TextMeshPro")
_TY = [dict(cid=49, sti=-1), dict(cid=28, sti=-1), dict(cid=128, sti=-1),
       dict(cid=115, sti=-1), dict(cid=21, sti=-1),
       dict(cid=114, sti=0), dict(cid=114, sti=1)]


def _studio_vi_134():
    """Bo 134 chu cua studio (12 goc x 5 dau + d/D, NFC) — dap an doc lap."""
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


ASCII = set(range(32, 127))
VI = _studio_vi_134()
LATIN1_ALL = set(range(0xA0, 0x100))


def _sf_with_assets(specs, extra_objs=(), scripts=((0, 5), (0, 6))):
    """SerializedFile v17 co MonoScript TMP + TextMeshProUGUI + cac asset."""
    objs = [_obj(5, 115, _monoscript(*_SA, "<"), 3),
            _obj(6, 115, _monoscript(*_SB, "<"), 3),
            _obj(20, 28, _tex("SourceCodePro Atlas", "<"), 1),
            _obj(30, 21, _tex("SourceCodePro Material", "<"), 4),
            _obj(40, 128, _tex("SourceCodePro-Regular", "<"), 2)]
    for pid, name, kw in specs:
        objs.append(_obj(pid, 114, _tmp_asset(name, "<", **kw), 5))
    objs.extend(extra_objs)
    return _build_sf(17, True, _TY, objs, scripts=scripts)


# Ban tai dung tu BYTE THAT DATA WING (hex goc trong make_tmp_ipas.py).
_REAL_HEAD = (
    "1a 77 c6 4a 00 00 00 00 1b 00 00 00 00 00 00 00 66 88 63 66 01 00 00 00 "
    "08 00 00 00 58 6f 6c 6f 6e 69 75 6d 00 00 b0 41 00 00 80 3f fe 00 00 00 "
    "00 00 d4 41 00 00 00 00 00 00 9d 41 00 00 68 41 00 00 a0 c0 00 00 00 00 "
    "00 00 9d 41 7b 14 3e c0 00 00 00 3f 7b 14 3e c0 c3 f5 a8 3f 8c 2e aa 40 "
    "c3 f5 a8 3f 00 40 b0 42 00 00 40 40 00 00 00 44 00 00 80 43 00 00 00 00 "
    "39 00 00 00 00 00 00 00 fe 00 00 00")
_REAL_SPACE = ("20 00 00 00 00 00 80 40 00 80 81 43 00 00 0d 41 00 00 c5 41 "
               "00 00 00 00 00 00 9d 41 00 00 0d 41 00 00 80 3f")


def _real_xolonium_bytes():
    head = bytes.fromhex(_REAL_HEAD.replace(" ", ""))
    space = bytes.fromhex(_REAL_SPACE.replace(" ", ""))
    real = (_pptr(0, 0, "<") + b"\1\0\0\0" + _pptr(1, 67, "<")
            + _astr("European Chars - Xolonium".encode(), "<") + head + space)
    for i, cp in enumerate(range(33, 286)):
        real += struct.pack("<i8f", cp, float((i % 24) * 20),
                            float((i // 24) * 22), 12.0 + (i % 5), 21.0,
                            0.5, 19.6, 13.0 + (i % 4), 1.0)
    return real + _pptr(0, 31, "<") + bytes(64)


class TestRequiredSet(TmpCase):
    def test_134_matches_studio(self):
        self.assertEqual(len(tf.vietnamese_letters_134()), 134)
        self.assertEqual(set(tf.vietnamese_letters_134()), VI)
        self.assertEqual(len(tf.combining_marks_8()), 8)
        # Tong 142 muc cua font scan = 134 chu + 8 dau.
        self.assertEqual(len(fs.REQUIRED_VIETNAMESE), 142)
        self.assertEqual(set(tf.vietnamese_letters_134())
                         | set(tf.combining_marks_8()),
                         set(fs.REQUIRED_VIETNAMESE))

    def test_chars_text(self):
        text = tf.vietnamese_chars_text()
        self.assertEqual(len(text), 142)
        self.assertEqual(
            sorted(ord(c) for c in text),
            sorted(set(fs.REQUIRED_VIETNAMESE)))


class TestDecodeObject(TmpCase):
    def _full(self):
        return _tmp_asset("SourceCodePro SDF", "<",
                           face="Source Code Pro",
                           ids=sorted(ASCII | VI | LATIN1_ALL))

    def test_full_asset_parsed(self):
        d = tf.decode_object(self._full(), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["name"], "SourceCodePro SDF")
        fi = d["font_info"]
        self.assertEqual(fi["name"], "Source Code Pro")
        self.assertEqual(fi["point_size"], 36.0)
        self.assertEqual(fi["scale"], 1.0)
        self.assertEqual(fi["declared_char_count"], len(ASCII | VI | LATIN1_ALL))
        self.assertEqual(fi["line_height"], 48.0)
        self.assertEqual(fi["padding"], 6.0)
        self.assertEqual(fi["atlas_width"], 1024.0)
        self.assertEqual(fi["atlas_height"], 1024.0)
        self.assertEqual(fi["atlas_basis"], "faceinfo_layout")
        self.assertEqual(d["glyph_table"]["count"], len(ASCII | VI | LATIN1_ALL))
        self.assertEqual(d["glyph_table"]["stride"], 36)
        self.assertTrue(d["glyph_table"]["count_matches_declared"])
        self.assertEqual(d["code_points"]["count"], len(ASCII | VI | LATIN1_ALL))
        vi = d["vietnamese"]
        self.assertEqual(vi["level"], "full")
        self.assertEqual(vi["covered"], 134)
        self.assertEqual(vi["missing_count"], 0)
        self.assertTrue(vi["ascii_letters_ok"])
        # 8 dau ket hop khong co trong atlas gia lap nay.
        self.assertEqual(vi["marks"]["covered"], 0)
        self.assertEqual(vi["marks"]["missing_count"], 8)
        self.assertEqual(d["problems"], [])
        self.assertEqual(d["glyph_table"]["sample"][0]["scale"], 1.0)

    def test_one_missing_is_partial(self):
        ids = sorted(ASCII | (VI - {0x1EF9}))
        d = tf.decode_object(
            _tmp_asset("OneMissing SDF", "<", face="One Missing", ids=ids), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["vietnamese"]["level"], "partial")
        self.assertEqual(d["vietnamese"]["covered"], 133)
        self.assertEqual(d["vietnamese"]["missing"], ["U+1EF9"])

    def test_no_glyphs_not_found_with_diagnostics(self):
        d = tf.decode_object(
            _tmp_asset("NoGlyphs SDF", "<", face="Broken Font", ids=[],
                       declared=90, no_glyphs=True), "<")
        self.assertEqual(d["status"], "glyph_table_not_found")
        self.assertIsNone(d["glyph_table"])
        self.assertIsNone(d["code_points"])
        self.assertIsNone(d["vietnamese"])
        self.assertTrue(d["diagnostics"]["hex_head"])
        self.assertGreater(d["diagnostics"]["faceinfo_offset"], 0)
        self.assertTrue(d["diagnostics"]["count_hits"])

    def test_count_mismatch_partial(self):
        d = tf.decode_object(
            _tmp_asset("CountMismatch SDF", "<", face="Mismatch",
                       ids=sorted(range(65, 165)), declared=120), "<")
        self.assertEqual(d["status"], "partial")
        self.assertTrue(any("CharacterCount khai báo 120" in p
                            for p in d["problems"]))

    def test_big_endian(self):
        d = tf.decode_object(
            _tmp_asset("BE SDF", ">", face="BE Font",
                       ids=sorted(ASCII | VI), atlas_pid=20, mat_pid=None,
                       font_pid=40), ">")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["vietnamese"]["level"], "full")
        self.assertEqual(d["font_info"]["atlas_basis"], "faceinfo_layout")

    def test_legacy18_faceinfo(self):
        d = tf.decode_object(
            _tmp_asset("Legacy18 SDF", "<", face="Legacy18",
                       ids=sorted(ASCII), legacy_fi=True), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["font_info"]["atlas_basis"], "faceinfo_layout_18")
        self.assertEqual(d["font_info"]["atlas_width"], 1024.0)

    def test_placeholder_glyphs_accepted(self):
        ids = sorted(ASCII | LATIN1_ALL | {10, 0x3000})
        d = tf.decode_object(
            _tmp_asset("Placeholders SDF", "<", face="Xolonium", ids=ids,
                       ph_ids={10, 32, 0xA0, 0x3000}), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["glyph_table"]["placeholder_glyphs"], 4)

    def test_new_layout_parsed(self):
        # Layout moi 52 byte/record (TMP 3.x, gap tren LiberationSans PB3):
        # id = glyph index, rect y tu BOTTOM.
        gids = [3, 4, 5, 66, 1999]
        d = tf.decode_object(
            _tmp_asset("NewLayout SDF", "<", face="New Font", ids=gids,
                       layout="new"), "<")
        self.assertEqual(d["status"], "parsed")
        gt = d["glyph_table"]
        self.assertEqual(gt["layout"], "new")
        self.assertEqual(gt["stride"], 52)
        self.assertEqual(gt["count"], 5)
        self.assertEqual(gt["sample"][0]["id"], 3)
        self.assertEqual(gt["sample"][0]["scale"], 1.0)
        self.assertEqual(gt["sample"][0]["atlas_index"], 0)
        self.assertEqual(d["code_points"]["kind"], "glyph_index")
        self.assertEqual(d["code_points"]["count"], 5)
        # layout moi: vietnamese = unknown (can cmap font nguon)
        self.assertEqual(d["vietnamese"]["level"], "unknown")

    def test_new_layout_placeholder(self):
        gids = [3, 4, 5]
        d = tf.decode_object(
            _tmp_asset("NewPlaceholders SDF", "<", face="New Font",
                       ids=gids, layout="new", ph_ids={3}), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["glyph_table"]["layout"], "new")
        self.assertEqual(d["glyph_table"]["placeholder_glyphs"], 1)

    def test_read_glyph_records_new(self):
        gids = [3, 4, 5, 66]
        raw = _tmp_asset("NewRecords SDF", "<", face="New Font", ids=gids,
                         layout="new")
        d = tf.decode_object(raw, "<")
        gt = d["glyph_table"]
        recs = tf.read_glyph_records(raw, gt["offset"], gt["count"],
                                     gt["stride"], "<")
        self.assertEqual([r["id"] for r in recs], gids)
        self.assertEqual(recs[0]["width"], 30.0)
        self.assertEqual(recs[0]["height"], 30.0)
        self.assertEqual(recs[0]["rect"][2], 30)
        self.assertEqual(recs[0]["rect"][3], 30)
        self.assertEqual(recs[0]["trailing"], 0)

    def test_find_face_info_new(self):
        # FaceInfo moi truc tiep: 2 chuoi + pointSize INT.
        raw = _faceinfo_new("Noto Sans", "Regular", "<", pt=87, lh=118.78)
        fi = tf.find_face_info_new(raw, 0, "<")
        self.assertIsNotNone(fi)
        self.assertEqual(fi["family_name"], "Noto Sans")
        self.assertEqual(fi["style_name"], "Regular")
        self.assertEqual(fi["point_size"], 87.0)
        self.assertEqual(fi["scale"], 1.0)
        self.assertEqual(fi["line_height"], 118.78)
        self.assertIsNone(fi["padding"])
        self.assertIsNone(fi["atlas_width"])

    def test_find_face_info_new_rejects_old(self):
        # FaceInfo cu (1 chuoi + pointSize FLOAT) khong duoc nhan la moi.
        raw = _faceinfo("Old Font", "<", pt=86.0)
        self.assertIsNone(tf.find_face_info_new(raw, 0, "<"))
        # Rac ngau nhien cung khong duoc nhan.
        self.assertIsNone(tf.find_face_info_new(b"\x00" * 200, 0, "<"))

    def test_decode_uses_new_faceinfo(self):
        # Asset chi co FaceInfo moi (khong ban sao cu): decode_object lay
        # pt + family tu FaceInfo moi; padding/atlas = None.
        gids = [3, 4, 5]
        d = tf.decode_object(
            _tmp_asset("NewFI SDF", "<", ids=gids, layout="new",
                       face_new=("Noto Sans", "Regular")), "<")
        self.assertEqual(d["status"], "parsed")
        fi = d["font_info"]
        self.assertIsNotNone(fi)
        self.assertEqual(fi["point_size"], 87.0)
        self.assertEqual(fi["family_name"], "Noto Sans")
        self.assertEqual(fi["style_name"], "Regular")
        self.assertEqual(fi["format"], "new")
        self.assertIsNone(fi["padding"])
        self.assertIsNone(fi["atlas_width"])

    def test_garbage_is_layout_unknown(self):
        d = tf.decode_object(b"khong phai TMP gi ca" * 8, "<")
        self.assertEqual(d["status"], "layout_unknown")
        self.assertIsNone(d["glyph_table"])

    def test_real_xolonium_bytes(self):
        d = tf.decode_object(_real_xolonium_bytes(), "<")
        self.assertEqual(d["status"], "parsed")
        self.assertEqual(d["font_info"]["name"], "Xolonium")
        self.assertEqual(d["font_info"]["point_size"], 22.0)
        self.assertEqual((d["font_info"]["atlas_width"],
                          d["font_info"]["atlas_height"]), (512.0, 256.0))
        self.assertEqual(d["glyph_table"]["count"], 254)
        self.assertEqual(d["vietnamese"]["covered"], 36)
        self.assertEqual(d["vietnamese"]["missing_count"], 98)
        self.assertEqual(d["vietnamese"]["level"], "partial")
        self.assertEqual(d["glyph_table"]["placeholder_glyphs"], 1)
        self.assertEqual(d["code_points"]["ranges"], [[32, 285]])


class TestScanSerialized(TmpCase):
    def test_find_tmp_assets_only_tmp(self):
        data = _sf_with_assets([
            (100, "SourceCodePro SDF",
             dict(face="Source Code Pro", ids=sorted(ASCII | VI | LATIN1_ALL))),
            (101, "Orbitron SDF",
             dict(face="Orbitron Bold", ids=sorted(ASCII), atlas_pid=21,
                  font_pid=41, mat_pid=None)),
        ], extra_objs=[
            _obj(200, 114, _tmp_asset("NotTmp", "<", ids=sorted(ASCII),
                                      face="Decoy"), 6),
        ])
        sf = uni.parse_serialized(data, where="resources.assets")
        found = tf.find_tmp_font_assets(sf)
        by_pid = {f["path_id"]: f for f in found}
        self.assertTrue(by_pid[100]["is_tmp"])
        self.assertTrue(by_pid[101]["is_tmp"])
        self.assertFalse(by_pid[200]["is_tmp"])
        self.assertEqual(by_pid[200]["script_class"], "TextMeshProUGUI")
        res = tf.scan_serialized_file(sf, "resources.assets")
        self.assertEqual(len(res["items"]), 2)
        self.assertEqual(res["summary"]["assets"], 2)
        self.assertEqual(res["summary"]["vietnamese_full"], 1)
        by_name = {it["name"]: it for it in res["items"]}
        refs = by_name["SourceCodePro SDF"]["refs"]
        self.assertEqual([r["name"] for r in refs["texture"]],
                         ["SourceCodePro Atlas"])
        self.assertEqual([r["name"] for r in refs["material"]],
                         ["SourceCodePro Material"])
        self.assertEqual([r["name"] for r in refs["font"]],
                         ["SourceCodePro-Regular"])
        self.assertEqual([m["name"] for m in
                          by_name["SourceCodePro SDF"]["matched_fonts"]],
                         ["SourceCodePro-Regular"])

    def test_no_header_still_parsed(self):
        data = _sf_with_assets([
            (105, "NoHeader SDF",
             dict(face="NoHeader", ids=sorted(ASCII), script_pid=0)),
        ])
        sf = uni.parse_serialized(data, where="resources.assets")
        res = tf.scan_serialized_file(sf, "resources.assets")
        self.assertEqual(len(res["items"]), 1)
        it = res["items"][0]
        self.assertEqual(it["status"], "parsed")
        self.assertIsNone(it["header"])
        self.assertTrue(any("Header MonoBehaviour không khớp" in p
                            for p in it["problems"]))

    def test_no_tmp_file(self):
        data = _build_sf(17, True,
                          _TY[:4],
                          [_obj(1, 49, _astr(b"x", "<") + _astr(b"y", "<"), 0)],
                          scripts=[])
        sf = uni.parse_serialized(data, where="sharedassets0.assets")
        res = tf.scan_serialized_file(sf, "sharedassets0.assets")
        self.assertEqual(res["items"], [])
        self.assertEqual(res["summary"]["assets"], 0)


class TestScanSourceAndPlan(TmpCase):
    def test_scan_zip_and_plan(self):
        data = _sf_with_assets([
            (100, "SourceCodePro SDF",
             dict(face="Source Code Pro", ids=sorted(ASCII | VI | LATIN1_ALL))),
            (110, "OneMissing SDF",
             dict(face="One Missing",
                  ids=sorted(ASCII | (VI - {0x1EF9})))),
        ])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Payload/Game.app/Data/resources.assets", data)
        ipa = self.tmp / "game.ipa"
        ipa.write_bytes(buf.getvalue())
        res = tf.scan_source(str(ipa))
        self.assertEqual(len(res["items"]), 2)
        self.assertEqual(res["summary"]["vietnamese_full"], 1)
        plan = res["glyph_plan"]
        self.assertEqual(plan["schema"], "tmp_glyph_plan/1")
        # Hop nhat chu thieu cua ca 2 asset: chi U+1EF9 + 8 dau ket hop.
        self.assertEqual(plan["missing_letters_codepoints"], [0x1EF9])
        self.assertEqual(len(plan["missing_marks_codepoints"]), 8)
        self.assertIn("ỹ", plan["chars_text"])
        self.assertEqual(len(plan["targets"]), 2)
        tgt = [t for t in plan["targets"]
               if t["name"] == "SourceCodePro SDF"][0]
        self.assertEqual((tgt["atlas_width"], tgt["atlas_height"]),
                         (1024.0, 1024.0))
        self.assertEqual(tgt["point_size"], 36.0)
        self.assertEqual(tgt["padding"], 6.0)
        self.assertEqual([m["name"] for m in tgt["material_refs"]],
                         ["SourceCodePro Material"])

    def test_scan_missing_source_raises(self):
        with self.assertRaises(Exception):
            tf.scan_source("/khong/co/file/nay.assets")

    def test_scan_plain_assets_file(self):
        data = _sf_with_assets([
            (100, "SourceCodePro SDF",
             dict(face="Source Code Pro", ids=sorted(ASCII | VI | LATIN1_ALL))),
        ])
        path = self.tmp / "resources.assets"
        path.write_bytes(data)
        res = tf.scan_source(str(path))
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual(res["items"][0]["status"], "parsed")
