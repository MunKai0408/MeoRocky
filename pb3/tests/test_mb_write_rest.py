"""Test duong ghi con lai cua M1 (mb-write-rest): MonoBehaviour generic theo
scan-path, bang ngon ngu I2 CO type tree, PATCH_INPLACE cho MB, phan loai
unchanged + mb_index passthrough (string_table_other_unit).

Fixture TU DUNG (builder blob type tree copy tu tests/test_mb_write.py
de file nay tu dung, khong import cheo giua cac file test) + du lieu
that 9K v1.0.6 / Build 68 (skip neu thieu file: env NINEKINGS_IPA hoac
~/workspace/test-ipas/9kings-v1.0.6.ipa; BUILD68_ASSETS hoac
~/workspace/user/files/A868947C-4963-4991-A547-15988908E58B-resources.assets).
Khong can mang.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    aligned_string, build_bundle, build_serialized_file)

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import extraction as _ext  # noqa: E402
from sdk import plan as _plan  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.tiering import slow  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)


def _astr(buf, text, endian="<"):
    raw = text.encode("utf-8")
    buf += struct.pack(endian + "i", len(raw)) + raw
    buf += b"\x00" * ((-len(buf)) % 4)


# ------------------------------------------------------- blob type tree long nhau
# (copy tu tests/test_mb_write.py — file test tu dung, khong import cheo).
def _nested_typetree_blob(spec, version=17):
    flat = []

    def walk(node, level, index):
        t, n, bs, children = node
        flat.append((level, t, n, bs, index))
        for i, c in enumerate(children):
            walk(c, level + 1, i)

    walk(spec, 0, 0)
    names = []
    for _lv, t, n, _bs, _ix in flat:
        for s in (t, n):
            if s not in names:
                names.append(s)
    strings = b"".join(s.encode("utf-8") + b"\x00" for s in names)

    def off(s):
        return strings.index(s.encode("utf-8"))

    body = b"".join(struct.pack("<hBBIIiii", 1, lv, 0, off(t), off(n), bs, ix, 0)
                    for lv, t, n, bs, ix in flat)
    return struct.pack("<ii", len(flat), len(strings)) + body + strings


def _array_node(elem_type, elem_byte_size, elem_children=()):
    return ("Array", "Array", -1, [
        ("int", "common_795", 4, []),
        (elem_type, "common_106", elem_byte_size, list(elem_children)),
    ])


def _string_node(name):
    return ("string", name, -1, [_array_node("char", 1)])


def _pptr_node(name, target):
    return ("PPtr<%s>" % target, name, -1, [
        ("int", "m_FileID", 4, []),
        ("long", "m_PathID", 8, []),
    ])


# ------------------------------------------------------- generic khong type tree
def _generic_mb_bytes(name, strings, trailing=b""):
    """Bytes object MonoBehaviour generic KHONG type tree: header 28 byte
    + m_Name + cac aligned_string (moi chuoi >= 3 ky tu de bo quet nhan)."""
    buf = bytearray(28)
    _astr(buf, name)
    for s in strings:
        _astr(buf, s)
    buf += trailing
    return bytes(buf)


GENERIC_PID = 3003


def _generic_sf(name="SomePanel", strings=("Alpha Bravo", "Charlie Delta",
                                           "Echo Foxtrot")):
    data = build_serialized_file(
        version=17,
        objects=[(GENERIC_PID, 0, _generic_mb_bytes(name, strings,
                                                    trailing=b"TRAIL-G"))],
        types=[(114, b"\x00" * 16)], enable_type_tree=False)
    return u.parse_serialized(data, LIM, where="generic-fixture")


# ------------------------------------------------------- bang I2 byte-walk
def _ls_bytes(terms, languages, name="LangSource", trailing=b""):
    """Bytes object bang ngon ngu I2 (byte-walk, KHONG type tree) — copy
    layout tu tests/test_mb_write.py."""
    buf = bytearray(28)
    _astr(buf, name)
    for v in (0, 0, 1):
        buf += struct.pack("<i", v)
    buf += struct.pack("<i", len(terms))
    n = len(languages)
    for term, values, touch in terms:
        _astr(buf, term)
        buf += struct.pack("<i", 0)  # TermType
        buf += struct.pack("<i", n)
        for v in values:
            _astr(buf, v)
        buf += struct.pack("<i", n)
        buf += bytes(n)
        buf += b"\x00" * ((-len(buf)) % 4)
        buf += struct.pack("<i", len(touch))
        for v in touch:
            _astr(buf, v)
    for v in (0, 1, 0):
        buf += struct.pack("<i", v)
    buf += struct.pack("<i", n)
    for lname, code, flag in languages:
        _astr(buf, lname)
        _astr(buf, code)
        buf += struct.pack("<i", flag)
    buf += trailing
    return bytes(buf)


LS_LANGS = [("English", "en", 0), ("Vietnamese", "vi", 0)]
LS_TERMS = [("greet", ["Hello", "Xin chào"], []),
            ("bye", ["Goodbye", "Tạm biệt"], [])]
LS_PID = 2002


def _ls_sf():
    data = build_serialized_file(
        version=17,
        objects=[(LS_PID, 0, _ls_bytes(LS_TERMS, LS_LANGS,
                                       trailing=b"TRAIL-LS"))],
        types=[(114, b"\x00" * 16)], enable_type_tree=False)
    return u.parse_serialized(data, LIM, where="ls-fixture")


# ------------------------------------------------------- bang I2 CO type tree
LS_TT_SPEC = ("MonoBehaviour", "MonoBehaviour", -1, [
    _pptr_node("m_GameObject", "GameObject"),
    ("bool", "m_Enabled", 1, []),
    _pptr_node("m_Script", "MonoScript"),
    _string_node("m_Name"),
    ("MyLangSource", "mSource", -1, [
        ("vector", "mLanguages", -1, [_array_node("Lang", -1, [
            _string_node("m_Name"),
            _string_node("m_Code"),
        ])]),
        ("vector", "mTerms", -1, [_array_node("Term", -1, [
            _string_node("m_Term"),
            ("vector", "Languages", -1,
             [_array_node("string", -1, [_array_node("char", 1)])]),
        ])]),
    ]),
])
LS_TT_PID = 4004


def _ls_tt_object_bytes():
    """Bytes object bang I2 CO type tree theo LS_TT_SPEC — dung TAY bang
    _Writer (khong dung writer de sinh du lieu test)."""
    w = u._Writer("<")
    w.pack("i", 0)
    w.pack("q", 0)          # m_GameObject PPtr
    w.write(bytes([1]))     # m_Enabled
    w.align(4)
    w.pack("i", 0)
    w.pack("q", 0)          # m_Script PPtr
    w.align(4)
    w.aligned_string("LangSourceTT")  # m_Name
    w.align(4)
    w.pack("i", 2)          # mSource.mLanguages: 2 ngon ngu
    for lname, code in (("English", "en"), ("Vietnamese", "vi")):
        w.align(4)
        w.aligned_string(lname)
        w.align(4)
        w.aligned_string(code)
        w.align(4)
    w.pack("i", 2)          # mSource.mTerms: 2 term
    for term, values in (("greet", ["Hello", "Xin chào"]),
                         ("bye", ["Goodbye", "Tạm biệt"])):
        w.align(4)
        w.aligned_string(term)
        w.align(4)
        w.pack("i", 2)      # Languages: 2 cot
        for v in values:
            w.align(4)
            w.aligned_string(v)
    w.write(b"TRAIL-TT")
    return w.bytes()


def _ls_tt_sf():
    blob = _nested_typetree_blob(LS_TT_SPEC, version=17)
    data = build_serialized_file(
        version=17, objects=[(LS_TT_PID, 0, _ls_tt_object_bytes())],
        types=[(114, b"\x00" * 16)], enable_type_tree=True,
        typetree_blobs={0: blob})
    return u.parse_serialized(data, LIM, where="ls-tt-fixture")


# ======================================================= scan writer
class TestScanWriter(unittest.TestCase):
    def test_slots_match_scan_strings_order(self):
        # [FIXTURE] slot va scan_strings cung thu tu / so luong.
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        name, slots = u._scan_monobehaviour_slots(raw, "<")
        _n2, strings = u.scan_monobehaviour_strings(raw, "<")
        self.assertEqual(name, "SomePanel")
        self.assertEqual([s for (_lp, _ss, _se, s) in slots], strings)
        self.assertEqual(strings, ["Alpha Bravo", "Charlie Delta",
                                   "Echo Foxtrot"])

    def test_change_one_string_others_unchanged(self):
        # [FIXTURE] doi dung 1 slot: slot doi dung gia tri moi, slot
        # khac + trailing giu nguyen, so slot khong doi.
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        new = u.write_scanned_monobehaviour_bytes(raw, [
            {"field_path": "scan[1]", "before": "Charlie Delta",
             "after": "Changed value here, longer"}])
        _n, slots = u._scan_monobehaviour_slots(new, "<")
        self.assertEqual([s for (_lp, _ss, _se, s) in slots],
                         ["Alpha Bravo", "Changed value here, longer",
                          "Echo Foxtrot"])
        self.assertTrue(new.endswith(b"TRAIL-G"))
        # Do dai file doi dung do dai slot da doi (tinh ca dem can 4).
        _n0, slots0 = u._scan_monobehaviour_slots(raw, "<")
        old_len = slots0[1][2] - slots0[1][0]
        new_len = slots[1][2] - slots[1][0]
        self.assertEqual(len(new) - len(raw), new_len - old_len)

    def test_round_trip_identity(self):
        # [FIXTURE] khong doi gi -> dung bytes goc.
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        self.assertEqual(u.write_scanned_monobehaviour_bytes(raw, []), raw)

    def test_rejects_before_mismatch(self):
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_scanned_monobehaviour_bytes(raw, [
                {"field_path": "scan[0]", "before": "sai",
                 "after": "moi"}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_rejects_bad_field_path(self):
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        for fp in ("terms[0].values[0]", "scan[x]", "scan[99]"):
            with self.assertRaises(PB3Error, msg=fp):
                u.write_scanned_monobehaviour_bytes(raw, [
                    {"field_path": fp, "before": "Alpha Bravo",
                     "after": "moi"}])

    def test_rejects_nul_in_new_value(self):
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_scanned_monobehaviour_bytes(raw, [
                {"field_path": "scan[0]", "before": "Alpha Bravo",
                 "after": "a\x00b"}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_rejects_too_long_new_value(self):
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_scanned_monobehaviour_bytes(raw, [
                {"field_path": "scan[0]", "before": "Alpha Bravo",
                 "after": "x" * 65537}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_verify_rejects_short_new_value(self):
        # [FIXTURE] gia tri moi < 3 ky tu: bo quet khong nhan dien duoc
        # -> verify that bai (fail-closed), khong ghi nua voi.
        sf = _generic_sf()
        raw = sf.object_bytes(GENERIC_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_scanned_monobehaviour_bytes(raw, [
                {"field_path": "scan[0]", "before": "Alpha Bravo",
                 "after": "ab"}])
        self.assertEqual(cm.exception.code, "E_VERIFY_FAILED")


# ======================================================= bang I2 co type tree
class TestTypetreeLanguageSource(unittest.TestCase):
    def test_targets_use_real_field_names(self):
        # [FIXTURE] target dung ten field that trong type tree.
        sf = _ls_tt_sf()
        val = u.read_monobehaviour_object(sf, LS_TT_PID)
        tgts = u.language_source_tt_targets(val, "en")
        self.assertEqual(tgts, [
            ("greet", "mSource.mTerms[0].Languages[0]", "Hello"),
            ("bye", "mSource.mTerms[1].Languages[0]", "Goodbye")])

    def test_targets_fallback_to_language_variant(self):
        # [FIXTURE] P0 (EK2): bang chi co cot en-GB/en-US, khong co cot
        # "en" — writer phai ghi vao cot bien the dau tien (en-GB),
        # khong tra None (khong thi phu ghi EK2 = 0).
        val = {"m_Name": "LangVar",
               "mSource": {
                   "mLanguages": [{"Name": "English (UK)", "Code": "en-GB"},
                                  {"Name": "English (US)", "Code": "en-US"}],
                   "mTerms": [{"Term": "greet",
                               "Languages": ["Hello UK", "Hello US"]},
                              {"Term": "bye",
                               "Languages": ["Bye UK", "Bye US"]}]}}
        tgts = u.language_source_tt_targets(val, "en")
        self.assertEqual(tgts, [
            ("greet", "mSource.mTerms[0].Languages[0]", "Hello UK"),
            ("bye", "mSource.mTerms[1].Languages[0]", "Bye UK")])

    def test_targets_exact_locale_wins_over_variant(self):
        # [FIXTURE] co ca cot "en" lan "en-GB" -> lay cot "en" (uu tien
        # khop chinh xac, giu hanh vi cu).
        val = {"m_Name": "LangVar",
               "mSource": {
                   "mLanguages": [{"Name": "English", "Code": "en"},
                                  {"Name": "English (UK)", "Code": "en-GB"}],
                   "mTerms": [{"Term": "greet",
                               "Languages": ["Hello", "Hello UK"]}]}}
        tgts = u.language_source_tt_targets(val, "en")
        self.assertEqual(tgts, [
            ("greet", "mSource.mTerms[0].Languages[0]", "Hello")])

    def test_targets_none_when_no_language_family_match(self):
        # [FIXTURE] khong co cot "en" cung khong co bien the tieng Anh
        # -> None (fail-closed, nhu cu).
        val = {"m_Name": "LangVar",
               "mSource": {
                   "mLanguages": [{"Name": "German", "Code": "de"},
                                  {"Name": "French", "Code": "fr"}],
                   "mTerms": [{"Term": "greet",
                               "Languages": ["Hallo", "Bonjour"]}]}}
        self.assertIsNone(u.language_source_tt_targets(val, "en"))

    def test_targets_none_for_non_language_table(self):
        # [FIXTURE] khong phai bang I2 -> None (fail-closed).
        self.assertIsNone(u.language_source_tt_targets({"m_Name": "x"}, "en"))
        self.assertIsNone(u.language_source_tt_targets(
            {"mSource": {"mLanguages": [], "mTerms": []}}, "en"))

    def test_targets_in_write_targets(self):
        # [FIXTURE] monobehaviour_write_targets liet ke kind
        # language_source_tt (khong con note "chua ho tro").
        sf = _ls_tt_sf()
        targets, notes = u.monobehaviour_write_targets(sf, "en")
        tt = [t for t in targets if t.get("kind") == "language_source_tt"]
        self.assertEqual(len(tt), 2)
        self.assertEqual({t["key"] for t in tt}, {"greet", "bye"})
        self.assertEqual(notes, [])

    def test_plan_apply_roundtrip(self):
        # [FIXTURE] plan -> ap -> doc lai: term doi dung gia tri, term
        # kia + ngon ngu khac giu nguyen.
        sf = _ls_tt_sf()
        raw = sf.raw
        tr = {"greet": "Xin chào mới"}
        plan = _plan.build_plan(raw, tr, source_bundle="fx.assets",
                                entry=None, mode="REBUILD_SECTION")
        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        self.assertEqual(entry["asset_type"], "MonoBehaviour")
        self.assertEqual(entry["changes"][0]["field_path"],
                         "mSource.mTerms[0].Languages[0]")
        out, _mode = _plan._apply_entry_changes(
            "assets", raw, None, entry, BE, LIM)
        sf2 = u.parse_serialized(out, LIM, where="after")
        val = u.read_monobehaviour_object(sf2, LS_TT_PID)
        terms = val["mSource"]["mTerms"]
        self.assertEqual(terms[0]["Languages"], ["Xin chào mới", "Xin chào"])
        self.assertEqual(terms[1]["Languages"], ["Goodbye", "Tạm biệt"])
        self.assertEqual(val["mSource"]["mLanguages"][0]["m_Code"], "en")


# ======================================================= generic qua plan
class TestGenericPlanMapping(unittest.TestCase):
    def test_fixture_generic_key_roundtrip(self):
        # [FIXTURE] key trich xuat "SomePanel.s2" -> plan field_path
        # scan[1] -> ap -> doc lai dung gia tri moi.
        sf = _generic_sf()
        raw = sf.raw
        targets = _ext.generic_write_targets(sf)
        self.assertEqual(
            [(t["key"], t["field_path"]) for t in targets],
            [("SomePanel.s1", "scan[0]"),
             ("SomePanel.s2", "scan[1]"),
             ("SomePanel.s3", "scan[2]")])
        plan = _plan.build_plan(raw, {"SomePanel.s2": "Đã đổi"},
                                source_bundle="fx.assets", entry=None,
                                mode="REBUILD_SECTION")
        self.assertEqual(len(plan["entries"]), 1)
        self.assertEqual(plan["entries"][0]["changes"][0]["field_path"],
                         "scan[1]")
        out, _mode = _plan._apply_entry_changes(
            "assets", raw, None, plan["entries"][0], BE, LIM)
        sf2 = u.parse_serialized(out, LIM, where="after")
        _n, strings = u.scan_monobehaviour_strings(
            sf2.object_bytes(GENERIC_PID), "<")
        self.assertEqual(strings, ["Alpha Bravo", "Đã đổi", "Echo Foxtrot"])

    def test_ambiguous_key_is_skipped(self):
        # [FIXTURE] key anh xa >1 locator khac nhau -> khong co target
        # (khong doan). 2 object cung ten "Dup", moi object 1 chuoi o
        # 1 ngon ngu khac nhau: split=False cho "Dup.s1" (object 1) +
        # "Dup.s1#2" (object 2); split=True cho "Dup.s1" o CA 2 bucket
        # en + vi -> gop lai "Dup.s1" co 2 locator khac nhau -> bo qua.
        data = build_serialized_file(
            version=17,
            objects=[(5001, 0, _generic_mb_bytes(
                "Dup", ("The quick brown fox jumps",))),
                     (5002, 0, _generic_mb_bytes(
                         "Dup", ("Tôi yêu tiếng Việt rất nhiều",)))],
            types=[(114, b"\x00" * 16)], enable_type_tree=False)
        sf = u.parse_serialized(data, LIM, where="dup-fixture")
        targets = _ext.generic_write_targets(sf)
        keys = [t["key"] for t in targets]
        self.assertNotIn("Dup.s1", keys)


# ======================================================= PATCH_INPLACE cho MB
class TestPatchInplaceMB(unittest.TestCase):
    def test_shorter_translation_stays_inplace(self):
        # [FIXTURE] dich ngan hon -> PATCH_INPLACE: file cung do dai,
        # offset object khong doi, gia tri dung.
        sf = _ls_sf()
        raw = sf.raw
        before_sf = u.parse_serialized(raw, LIM, where="before")
        before_obj = before_sf.get_object(LS_PID)
        req = u.MbPatchRequest(
            data=raw, kind="assets", path_id=LS_PID,
            changes=[{"field_path": "terms[0].values[0]",
                      "before": "Hello", "after": "Hi"}],
            write_mode="PATCH_INPLACE")
        res = u.apply_monobehaviour_patch(req)
        self.assertEqual(res.write_mode_used, "PATCH_INPLACE")
        self.assertEqual(len(res.output_bytes), len(raw))
        after_sf = u.parse_serialized(res.output_bytes, LIM, where="after")
        after_obj = after_sf.get_object(LS_PID)
        self.assertEqual(after_obj.byte_start, before_obj.byte_start)
        self.assertEqual(after_obj.byte_size, before_obj.byte_size)
        info = u.parse_language_source_bytes(
            after_sf.object_bytes(LS_PID), "<")
        self.assertEqual(info["terms"][0]["values"][0], "Hi")
        self.assertEqual(info["terms"][1]["values"][0], "Goodbye")

    def test_longer_translation_escalates(self):
        # [FIXTURE] dich dai hon -> leo thang REBUILD_SECTION + ghi ro
        # trong warnings.
        sf = _ls_sf()
        raw = sf.raw
        req = u.MbPatchRequest(
            data=raw, kind="assets", path_id=LS_PID,
            changes=[{"field_path": "terms[0].values[0]",
                      "before": "Hello",
                      "after": "Xin chào, đây là bản dịch dài hơn rất nhiều"}],
            write_mode="PATCH_INPLACE")
        res = u.apply_monobehaviour_patch(req)
        self.assertEqual(res.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(any("PATCH_INPLACE" in w for w in res.report.warnings))
        info = u.parse_language_source_bytes(
            u.parse_serialized(res.output_bytes, LIM, where="after"
                               ).object_bytes(LS_PID), "<")
        self.assertEqual(info["terms"][0]["values"][0],
                         "Xin chào, đây là bản dịch dài hơn rất nhiều")

    def test_no_escalate_refuses_longer(self):
        # [FIXTURE] allow_escalate=False + dich dai hon -> tu choi ro
        # rang, khong cat cut.
        sf = _ls_sf()
        raw = sf.raw
        req = u.MbPatchRequest(
            data=raw, kind="assets", path_id=LS_PID,
            changes=[{"field_path": "terms[0].values[0]",
                      "before": "Hello", "after": "Hello world, longer"}],
            write_mode="PATCH_INPLACE", allow_escalate=False)
        with self.assertRaises(PB3Error) as cm:
            u.apply_monobehaviour_patch(req)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_plan_inplace_op_matches_apply(self):
        # [FIXTURE] plan mode PATCH_INPLACE: entry ngan hon -> op
        # PATCH_INPLACE + after_sha la sha cua bytes da dem NUL (khop
        # ket qua ap that).
        sf = _ls_sf()
        raw = sf.raw
        plan = _plan.build_plan(raw, {"greet": "Hi"},
                                source_bundle="fx.assets", entry=None,
                                mode="PATCH_INPLACE")
        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        self.assertEqual(entry["op"], "PATCH_INPLACE")
        out, mode = _plan._apply_entry_changes(
            "assets", raw, None, entry, BE, LIM)
        self.assertEqual(mode, "PATCH_INPLACE")
        self.assertEqual(len(out), len(raw))
        self.assertEqual(hashlib.sha256(
            u.parse_serialized(out, LIM, where="after"
                               ).object_bytes(LS_PID)).hexdigest(),
            entry["after_sha"])


# ======================================================= du lieu that
def _nine_kings_ipa():
    env = os.environ.get("NINEKINGS_IPA")
    if env:
        return Path(env)
    return Path.home() / "workspace" / "test-ipas" / "9kings-v1.0.6.ipa"


def _build68_assets():
    env = os.environ.get("BUILD68_ASSETS")
    if env:
        return Path(env)
    return (Path.home() / "workspace" / "user" / "files"
            / "A868947C-4963-4991-A547-15988908E58B-resources.assets")


def _mb_index_from_9k_ipa(ipa_bytes):
    """{collection: {key: m_Id}} that tu IPA 9K (SharedTableData)."""
    zf = zipfile.ZipFile(io.BytesIO(ipa_bytes))
    sfs = []
    for n in zf.namelist():
        if "localization" not in n or not n.endswith(".bundle"):
            continue
        b = u.parse_bundle(zf.read(n), BE, LIM, where=n)
        for e in b.entries:
            try:
                sfs.append(u.parse_serialized(b.get_entry(e.name), LIM,
                                              where=e.name))
            except PB3Error:
                continue
    return _plan.build_mb_shared_index(sfs)


class TestRealBuild68Classification(unittest.TestCase):
    @slow
    def test_dryrun_1985_103_100(self):
        # [ĐÃ ĐO THẬT] Build 68 resources.assets + translated.vi.json
        # (2188 key) + mb_index that tu IPA 9K: 1985 doi / 103 giu
        # nguyen (vi==en) / 100 StringTable o don vi khac — 0 refused.
        # (Truoc fix 106-key: 1985/101/102 — 2 key ST rong
        # Spells/Cemetery_Desc + Spells/Rebirth_Desc gio dem unchanged
        # vi ban dich "" == gia tri goc hieu dung "", khong phai loi.)
        assets_path = _build68_assets()
        ipa_path = _nine_kings_ipa()
        if not assets_path.exists():
            raise unittest.SkipTest("thieu Build 68: %s" % assets_path)
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        tr = json.loads(
            (Path.home() / "workspace" / "user" / "files"
             / "translated.vi.json").read_text(encoding="utf-8"))
        self.assertEqual(len(tr), 2188)
        mb_index = _mb_index_from_9k_ipa(ipa_path.read_bytes())
        res = _rb.build_rebuild_preview(
            assets_path.read_bytes(), source_name=assets_path.name,
            translations=tr, mb_index=mb_index)
        tot = res["totals"]
        self.assertEqual(tot["changed"], 1985, str(tot))
        self.assertEqual(tot["unchanged"], 103, str(tot))
        self.assertEqual(tot["unmatched"], 100, str(tot))
        self.assertEqual(tot["refused"], 0)
        reasons = {x["reason"] for x in res["unmatched"]}
        self.assertEqual(reasons, {"string_table_other_unit"})

    @slow
    def test_dryrun_fixed_file(self):
        # [ĐÃ ĐO THẬT] Ban fixed: TerrainPopup_Mulligan sua thanh
        # 'Mulligan!' (= goc) nen chuyen sang unchanged: 1984/104/100.
        # (Truoc fix 106-key: 1984/102/102 — 2 key ST rong
        # Spells/Cemetery_Desc + Spells/Rebirth_Desc gio dem unchanged
        # vi ban dich "" == gia tri goc hieu dung "".)
        assets_path = _build68_assets()
        ipa_path = _nine_kings_ipa()
        if not assets_path.exists():
            raise unittest.SkipTest("thieu Build 68: %s" % assets_path)
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        tr = json.loads(
            (Path.home() / "workspace" / "your_files"
             / "translated.vi-fixed.json").read_text(encoding="utf-8"))
        self.assertEqual(len(tr), 2188)
        mb_index = _mb_index_from_9k_ipa(ipa_path.read_bytes())
        res = _rb.build_rebuild_preview(
            assets_path.read_bytes(), source_name=assets_path.name,
            translations=tr, mb_index=mb_index)
        tot = res["totals"]
        self.assertEqual(tot["changed"], 1984, str(tot))
        self.assertEqual(tot["unchanged"], 104, str(tot))
        self.assertEqual(tot["unmatched"], 100, str(tot))


class TestRealNineKingsMbIndex(unittest.TestCase):
    @slow
    def test_st_bundle_alone_writable_with_real_mb_index(self):
        # [ĐÃ ĐO THẬT] Chi bundle ST en + mb_index that: 102 key ST ep
        # duoc (ke ca 2 key chen moi Cemetery_Desc/Rebirth_Desc khi ban
        # dich khong rong).
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        ipa = ipa_path.read_bytes()
        sample = json.loads(
            (Path.home() / "workspace" / "user" / "files" / "en.json"
             ).read_text(encoding="utf-8"))
        tr = {k: ("Tiếng Việt: " + v) for k, v in sample.items()}
        zf = zipfile.ZipFile(io.BytesIO(ipa))
        bname = ("Payload/9Kings.app/Data/Raw/aa/iOS/"
                 "localization-string-tables-english(en)_assets_all.bundle")
        bdata = zf.read(bname)
        mb_index = _mb_index_from_9k_ipa(ipa)
        plan = _plan.build_plan(bdata, tr, source_bundle=bname,
                                entry="CAB-7f58a1d076a0579bc99ac19d9e397afa",
                                mode="REBUILD_SECTION",
                                mb_index=mb_index)
        n_changes = sum(len(e["changes"]) for e in plan["entries"])
        self.assertEqual(n_changes, 102, str(plan["skipped"][:3]))

    @slow
    def test_resources_assets_alone_st_keys_other_unit(self):
        # [ĐÃ ĐO THẬT] Chi resources.assets + mb_index that: 102 key ST
        # duoc phan loai string_table_other_unit (khong phai not_found).
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        ipa = ipa_path.read_bytes()
        zf = zipfile.ZipFile(io.BytesIO(ipa))
        assets = zf.read("Payload/9Kings.app/Data/resources.assets")
        sample = json.loads(
            (Path.home() / "workspace" / "user" / "files" / "en.json"
             ).read_text(encoding="utf-8"))
        tr = {k: ("Tiếng Việt: " + v) for k, v in sample.items()}
        mb_index = _mb_index_from_9k_ipa(ipa)
        res = _rb.build_rebuild_preview(
            assets, source_name="resources.assets", translations=tr,
            mb_index=mb_index)
        from collections import Counter
        reasons = Counter(x["reason"] for x in res["unmatched"])
        self.assertEqual(reasons, {"string_table_other_unit": 102},
                         str(res["unmatched"][:5]))
        self.assertEqual(res["totals"]["changed"], 2150,
                         str(res["totals"]))


class TestRealNineKingsGeneric(unittest.TestCase):
    @slow
    def test_generic_key_plan_apply_rescan(self):
        # [ĐÃ ĐO THẬT] Lay 1 key generic that trong resources.assets 9K
        # (khong phai term I2 / ST), dich qua plan -> ap -> quet lai
        # dung gia tri moi, cac chuoi khac giu nguyen.
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        ipa = ipa_path.read_bytes()
        zf = zipfile.ZipFile(io.BytesIO(ipa))
        assets = zf.read("Payload/9Kings.app/Data/resources.assets")
        sf = u.parse_serialized(assets, LIM, where="resources.assets")
        targets = _ext.generic_write_targets(sf)
        self.assertTrue(targets, "khong co target generic nao trong 9K")
        # Chon target co gia tri dai, duy nhat trong object (tranh mo ho).
        pick = None
        for t in targets:
            if len(t["before"]) >= 12 and t["field_path"].startswith("scan["):
                pick = t
                break
        self.assertIsNotNone(pick, "khong tim duoc target generic scan")
        new_val = "ĐÃ DỊCH: " + pick["before"]
        plan = _plan.build_plan(assets, {pick["key"]: new_val},
                                source_bundle="resources.assets",
                                entry=None, mode="REBUILD_SECTION")
        self.assertEqual(len(plan["entries"]), 1)
        self.assertEqual(plan["entries"][0]["changes"][0]["field_path"],
                         pick["field_path"])
        out, _mode = _plan._apply_entry_changes(
            "assets", assets, None, plan["entries"][0], BE, LIM)
        sf2 = u.parse_serialized(out, LIM, where="after")
        _n, slots = u._scan_monobehaviour_slots(
            sf2.object_bytes(pick["path_id"]), "<")
        i = int(pick["field_path"][5:-1])
        self.assertEqual(slots[i][3], new_val)

    @slow
    def test_inplace_on_real_resources_assets(self):
        # [ĐÃ ĐO THẬT] Dich NGAN HON 1 term I2 that trong resources.assets
        # 9K, mode PATCH_INPLACE: file cung do dai, gia tri dung.
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        ipa = ipa_path.read_bytes()
        zf = zipfile.ZipFile(io.BytesIO(ipa))
        assets = zf.read("Payload/9Kings.app/Data/resources.assets")
        sf = u.parse_serialized(assets, LIM, where="resources.assets")
        targets, _notes = u.monobehaviour_write_targets(sf, "en")
        ls_targets = [t for t in targets
                      if t.get("kind") == "language_source"
                      and len(t["before"]) >= 10]
        self.assertTrue(ls_targets, "khong co target I2 nao")
        pick = ls_targets[0]
        plan = _plan.build_plan(assets, {pick["key"]: "Ngắn"},
                                source_bundle="resources.assets",
                                entry=None, mode="PATCH_INPLACE")
        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        self.assertEqual(entry["op"], "PATCH_INPLACE")
        out, mode = _plan._apply_entry_changes(
            "assets", assets, None, entry, BE, LIM)
        self.assertEqual(mode, "PATCH_INPLACE")
        self.assertEqual(len(out), len(assets))
        sf2 = u.parse_serialized(out, LIM, where="after")
        info = u.parse_language_source_bytes(
            sf2.object_bytes(pick["path_id"]), "<")
        got = [t for t in info["terms"] if t["term"] == pick["key"]]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["values"][0], "Ngắn")


if __name__ == "__main__":
    unittest.main()
