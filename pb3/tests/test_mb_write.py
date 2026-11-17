"""Test writer MonoBehaviour/StringTable (M1, G1 — sdk/unity.py + plan.py).

Fixture TU DUNG bang builder co san trong repo
(tests/fixtures/unity/build_fixtures.py) + blob type tree long nhau tu
viet o day (builder co san chi lam cay phang); object bytes dung TAY
bang _Writer (duong doc lap, khong dung writer de sinh du lieu test).
Khong dung IPA that, khong can mang, tru test 9K cuoi (skip neu thieu
IPA, env NINEKINGS_IPA hoac ~/workspace/test-ipas/9kings-v1.0.6.ipa).
"""
from __future__ import annotations

import copy
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
from sdk import gate as _gate  # noqa: E402
from sdk import plan as _plan  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.tiering import slow  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)


# ------------------------------------------------------- blob type tree long nhau
def _nested_typetree_blob(spec, version=17):
    """Blob type tree v17 tu spec long nhau (type, name, byte_size, [con]).

    Node 24 byte '<hBBIIiii', level = do sau, thu tu pre-order (cha
    truoc con). Array: con duy nhat ten "Array", con cua no la
    common_795 (size, i32) + common_106 (data) — dung ten reader doi
    (sdk/unity.py _typetree_is_array)."""
    flat = []  # (level, type, name, byte_size, index)

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
    """Node Array cho 1 field mang: [size common_795, data common_106]."""
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


MB_SPEC = ("MonoBehaviour", "MonoBehaviour", -1, [
    _pptr_node("m_GameObject", "GameObject"),
    ("bool", "m_Enabled", 1, []),
    _pptr_node("m_Script", "MonoScript"),
    _string_node("m_Name"),
    _string_node("m_Title"),
    ("int", "m_Count", 4, []),
    ("vector", "m_Scores", -1, [_array_node("int", 4)]),
    ("vector", "m_Items", -1, [_array_node("Item", -1, [
        ("long", "m_Id", 8, []),
        _string_node("m_Text"),
    ])]),
    ("MyNested", "m_Nested", -1, [
        _string_node("m_Label"),
        ("int", "m_Value", 4, []),
    ]),
])

MB_PID = 1001


def _mb_object_bytes():
    """Bytes object MonoBehaviour theo MB_SPEC — dung TAY bang _Writer
    (khong dung write_typetree_value de sinh), kem trailing 'TRAIL'."""
    w = u._Writer("<")
    w.pack("i", 0)
    w.pack("q", 0)          # m_GameObject PPtr
    w.write(bytes([1]))     # m_Enabled
    w.align(4)
    w.pack("i", 0)
    w.pack("q", 0)          # m_Script PPtr
    w.align(4)
    w.aligned_string("TestBehaviour")   # m_Name
    w.align(4)
    w.aligned_string("Hello World")     # m_Title
    w.align(4)
    w.pack("i", 3)          # m_Count
    w.align(4)
    w.pack("i", 3)          # m_Scores: 3 phan tu
    for s in (10, 20, 30):
        w.align(4)
        w.pack("i", s)
    w.align(4)
    w.pack("i", 2)          # m_Items: 2 phan tu
    for mid, text in ((11, "one"), (22, "two")):
        w.align(4)
        w.align(4)
        w.pack("q", mid)
        w.align(4)
        w.aligned_string(text)
    w.align(4)
    w.aligned_string("nested")          # m_Nested.m_Label
    w.align(4)
    w.pack("i", 7)          # m_Nested.m_Value
    w.write(b"TRAIL")       # trailing bytes (phai giu nguyen khi ghi)
    return w.bytes()


def _mb_sf():
    """SerializedFile v17 chua 1 MonoBehaviour co type tree (MB_SPEC)."""
    blob = _nested_typetree_blob(MB_SPEC, version=17)
    data = build_serialized_file(
        version=17, objects=[(MB_PID, 0, _mb_object_bytes())],
        types=[(114, b"\x00" * 16)], enable_type_tree=True,
        typetree_blobs={0: blob})
    return u.parse_serialized(data, LIM, where="mb-fixture")


def _mb_bundle():
    """Bundle UnityFS v7 chua SF tren (1 entry CAB)."""
    sf_bytes = _mb_sf().raw
    return u.build_bundle([("CAB-testmb0000000000000000000000000000", 0, sf_bytes)],
                          version=7, unity_version="2019.4.40f1",
                          compression="none")


# ------------------------------------------------------- bang I2 tong hop
def _ls_astr(buf, text, endian="<"):
    raw = text.encode("utf-8")
    buf += struct.pack(endian + "i", len(raw)) + raw
    buf += b"\x00" * ((-len(buf)) % 4)


def _ls_bytes(terms, languages, name="LangSource", trailing=b""):
    """Bytes object bang ngon ngu I2 (byte-walk, KHONG type tree) theo
    layout da do: header 28 byte + ten + 3 i32 + so term + tung term
    (ten, TermType, so cot, gia tri tung cot, so flag, flag, touch) +
    3 i32 + so ngon ngu + tung ngon ngu (ten, code, flag) + trailing."""
    buf = bytearray(28)
    _ls_astr(buf, name)
    for v in (0, 0, 1):
        buf += struct.pack("<i", v)
    buf += struct.pack("<i", len(terms))
    n = len(languages)
    for term, values, touch in terms:
        _ls_astr(buf, term)
        buf += struct.pack("<i", 0)  # TermType
        buf += struct.pack("<i", n)
        for v in values:
            _ls_astr(buf, v)
        buf += struct.pack("<i", n)
        buf += bytes(n)
        buf += b"\x00" * ((-len(buf)) % 4)
        buf += struct.pack("<i", len(touch))
        for v in touch:
            _ls_astr(buf, v)
    for v in (0, 1, 0):
        buf += struct.pack("<i", v)
    buf += struct.pack("<i", n)
    for lname, code, flag in languages:
        _ls_astr(buf, lname)
        _ls_astr(buf, code)
        buf += struct.pack("<i", flag)
    buf += trailing
    return bytes(buf)


LS_LANGS = [("English", "en", 0), ("Chinese (Simplified)", "zh-CN", 0)]
LS_TERMS = [("greet", ["Hello", "你好"], []),
            ("bye", ["Bye", "再见"], ["touch1"])]
LS_PID = 2002


def _ls_sf():
    data = build_serialized_file(
        version=17,
        objects=[(LS_PID, 0, _ls_bytes(LS_TERMS, LS_LANGS,
                                       trailing=b"TRAIL-LS"))],
        types=[(114, b"\x00" * 16)], enable_type_tree=False)
    return u.parse_serialized(data, LIM, where="ls-fixture")


class TestTypetreeWriter(unittest.TestCase):
    def test_round_trip_identity(self):
        # [FIXTURE] ghi khong doi -> dung bytes goc (ke ca trailing).
        sf = _mb_sf()
        raw = sf.object_bytes(MB_PID)
        new, applied = u.write_monobehaviour_object_bytes(sf, MB_PID, [])
        self.assertEqual(new, raw)
        self.assertEqual(applied, [])

    def test_read_matches_manual_layout(self):
        # [FIXTURE] reader doc dung dict da dung tay.
        sf = _mb_sf()
        val = u.read_monobehaviour_object(sf, MB_PID)
        self.assertEqual(val["m_Name"], "TestBehaviour")
        self.assertEqual(val["m_Title"], "Hello World")
        self.assertEqual(val["m_Count"], 3)
        self.assertEqual(val["m_Scores"], [10, 20, 30])
        self.assertEqual(val["m_Items"],
                         [{"m_Id": 11, "m_Text": "one"},
                          {"m_Id": 22, "m_Text": "two"}])
        self.assertEqual(val["m_Nested"],
                         {"m_Label": "nested", "m_Value": 7})

    def test_change_string_longer_shorter_nested(self):
        # [FIXTURE] doi chuoi dai hon + ngan hon + trong struct long
        # nhau + trong mang struct; cac field khac + trailing giu nguyen.
        sf = _mb_sf()
        raw = sf.object_bytes(MB_PID)
        changes = [
            {"field_path": "m_Title", "before": "Hello World",
             "after": "Xin chào thế giới dài hơn"},
            {"field_path": "m_Items[1].m_Text", "before": "two", "after": "x"},
            {"field_path": "m_Nested.m_Label", "before": "nested",
             "after": "nhãn mới"},
        ]
        new, applied = u.write_monobehaviour_object_bytes(sf, MB_PID, changes)
        self.assertEqual(len(applied), 3)
        # Parse lai bang SF moi (qua _rebuild_assets_section) va doi chieu.
        rebuilt = u._rebuild_assets_section(sf, MB_PID, {MB_PID: new})
        sf2 = u.parse_serialized(rebuilt, LIM, where="rebuilt")
        val = u.read_monobehaviour_object(sf2, MB_PID)
        self.assertEqual(val["m_Title"], "Xin chào thế giới dài hơn")
        self.assertEqual(val["m_Items"][1]["m_Text"], "x")
        self.assertEqual(val["m_Items"][0], {"m_Id": 11, "m_Text": "one"})
        self.assertEqual(val["m_Nested"]["m_Label"], "nhãn mới")
        self.assertEqual(val["m_Count"], 3)
        self.assertEqual(val["m_Scores"], [10, 20, 30])
        self.assertTrue(new.endswith(b"TRAIL"))
        self.assertNotEqual(new, raw)

    def test_write_rejects_before_mismatch(self):
        sf = _mb_sf()
        with self.assertRaises(PB3Error) as cm:
            u.write_monobehaviour_object_bytes(sf, MB_PID, [
                {"field_path": "m_Title", "before": "sai",
                 "after": "moi"}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_write_rejects_non_string_leaf(self):
        # Chi ghi duoc la chuoi — doi so thi tu choi, khong doan.
        sf = _mb_sf()
        with self.assertRaises(PB3Error) as cm:
            u.write_monobehaviour_object_bytes(sf, MB_PID, [
                {"field_path": "m_Count", "before": 3, "after": 4}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_write_rejects_missing_field(self):
        sf = _mb_sf()
        with self.assertRaises(PB3Error) as cm:
            u.write_monobehaviour_object_bytes(sf, MB_PID, [
                {"field_path": "m_NoSuch", "before": "",
                 "after": "x"}])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")


class TestLanguageSourceWriter(unittest.TestCase):
    def test_round_trip_identity(self):
        # [FIXTURE] ghi khong doi -> dung bytes goc.
        sf = _ls_sf()
        raw = sf.object_bytes(LS_PID)
        new = u.write_language_source_bytes(raw, [], sf.endian)
        self.assertEqual(new, raw)

    def test_change_one_value(self):
        # [FIXTURE] doi 1 gia tri: term khac + cot khac + trailing giu nguyen.
        sf = _ls_sf()
        raw = sf.object_bytes(LS_PID)
        new = u.write_language_source_bytes(raw, [
            {"field_path": "terms[0].values[0]", "before": "Hello",
             "after": "Xin chào"}], sf.endian)
        back = u.parse_language_source_bytes(new, sf.endian)
        self.assertEqual(back["terms"][0]["values"], ["Xin chào", "你好"])
        self.assertEqual(back["terms"][1]["values"], ["Bye", "再见"])
        self.assertEqual(back["languages"][0]["code"], "en")
        self.assertEqual(back["trailing_bytes"], len(b"TRAIL-LS"))

    def test_write_rejects_bad_term_index(self):
        sf = _ls_sf()
        raw = sf.object_bytes(LS_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_language_source_bytes(raw, [
                {"field_path": "terms[99].values[0]", "before": "",
                 "after": "x"}], sf.endian)
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_write_rejects_before_mismatch(self):
        sf = _ls_sf()
        raw = sf.object_bytes(LS_PID)
        with self.assertRaises(PB3Error) as cm:
            u.write_language_source_bytes(raw, [
                {"field_path": "terms[0].values[0]", "before": "sai",
                 "after": "x"}], sf.endian)
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")


class TestMbPatch(unittest.TestCase):
    def test_apply_patch_assets(self):
        # [FIXTURE] patch SF doc lap: verify 2 lop PASS, gia tri dung,
        # object khac giu nguyen, file goc khong doi.
        sf = _mb_sf()
        orig = sf.raw
        res = u.apply_monobehaviour_patch(u.MbPatchRequest(
            data=orig, kind="assets", path_id=MB_PID,
            changes=[{"field_path": "m_Title", "before": "Hello World",
                      "after": "Tieu de moi"}],
            write_mode="REBUILD_SECTION", backend=BE, limits=LIM))
        self.assertTrue(res.verified)
        self.assertEqual(res.write_mode_used, "REBUILD_SECTION")
        self.assertNotEqual(res.output_bytes, orig)
        sf2 = u.parse_serialized(res.output_bytes, LIM, where="after")
        val = u.read_monobehaviour_object(sf2, MB_PID)
        self.assertEqual(val["m_Title"], "Tieu de moi")
        self.assertEqual(val["m_Name"], "TestBehaviour")
        self.assertEqual(sf.raw, orig)

    def test_apply_patch_bundle(self):
        # [FIXTURE] patch trong bundle: chi entry do doi, gia tri dung.
        bundle_bytes = _mb_bundle()
        entry = "CAB-testmb0000000000000000000000000000"
        res = u.apply_monobehaviour_patch(u.MbPatchRequest(
            data=bundle_bytes, kind="bundle", entry=entry, path_id=MB_PID,
            changes=[{"field_path": "m_Items[0].m_Text", "before": "one",
                      "after": "mot"}],
            write_mode="REBUILD_SECTION", backend=BE, limits=LIM))
        self.assertTrue(res.verified)
        b2 = u.parse_bundle(res.output_bytes, BE, LIM, where="after")
        sf2 = u.parse_serialized(b2.get_entry(entry), LIM, where="after")
        val = u.read_monobehaviour_object(sf2, MB_PID)
        self.assertEqual(val["m_Items"][0]["m_Text"], "mot")
        self.assertEqual(val["m_Items"][1]["m_Text"], "two")

    def test_apply_patch_inplace_accepted(self):
        # mb-write-rest: PATCH_INPLACE duoc ho tro cho MB — dich ngan
        # hon thi noi tai cho (cung do dai file), gia tri dung.
        sf = _mb_sf()
        orig = sf.raw
        res = u.apply_monobehaviour_patch(u.MbPatchRequest(
            data=orig, kind="assets", path_id=MB_PID,
            changes=[{"field_path": "m_Title",
                      "before": "Hello World", "after": "x"}],
            write_mode="PATCH_INPLACE", backend=BE, limits=LIM))
        self.assertTrue(res.verified)
        self.assertEqual(res.write_mode_used, "PATCH_INPLACE")
        self.assertEqual(len(res.output_bytes), len(orig))
        sf2 = u.parse_serialized(res.output_bytes, LIM, where="after")
        val = u.read_monobehaviour_object(sf2, MB_PID)
        self.assertEqual(val["m_Title"], "x")
        self.assertEqual(val["m_Name"], "TestBehaviour")

    def test_apply_patch_rejects_rebuild_all(self):
        # REBUILD_ALL van bi tu choi ro rang cho MonoBehaviour.
        sf = _mb_sf()
        with self.assertRaises(PB3Error) as cm:
            u.apply_monobehaviour_patch(u.MbPatchRequest(
                data=sf.raw, kind="assets", path_id=MB_PID,
                changes=[{"field_path": "m_Title",
                          "before": "Hello World", "after": "x"}],
                write_mode="REBUILD_ALL", backend=BE, limits=LIM))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_apply_patch_ls_assets(self):
        # [FIXTURE] patch bang I2 trong SF doc lap.
        sf = _ls_sf()
        res = u.apply_monobehaviour_patch(u.MbPatchRequest(
            data=sf.raw, kind="assets", path_id=LS_PID,
            changes=[{"field_path": "terms[1].values[0]", "before": "Bye",
                      "after": "Tam biet"}],
            write_mode="REBUILD_SECTION", backend=BE, limits=LIM))
        self.assertTrue(res.verified)
        sf2 = u.parse_serialized(res.output_bytes, LIM, where="after")
        info = u.parse_language_source_bytes(sf2.object_bytes(LS_PID),
                                             sf2.endian)
        self.assertEqual(info["terms"][1]["values"][0], "Tam biet")
        self.assertEqual(info["terms"][0]["values"][0], "Hello")


class TestMbPlanGate(unittest.TestCase):
    def test_build_plan_mb_language_source(self):
        # [FIXTURE] build_plan lap entry MB cho bang I2; after_sha la sha
        # object bytes; apply_plan (dry-run) ap + verify dung.
        sf = _ls_sf()
        data = sf.raw
        tr = {"greet": "Xin chao", "bye": "Tam biet",
              "khong_co": "xxx"}
        plan = _plan.build_plan(data, tr, source_bundle="fx.assets",
                                entry=None, mode="REBUILD_SECTION",
                                backend=BE, limits=LIM)
        self.assertEqual(len(plan["entries"]), 1)
        e = plan["entries"][0]
        self.assertEqual(e["asset_type"], "MonoBehaviour")
        self.assertEqual(e["op"], "REBUILD_SECTION")
        self.assertEqual(e["object"], LS_PID)
        self.assertEqual(len(e["changes"]), 2)
        self.assertEqual(e["changes"][0]["field_path"], "terms[0].values[0]")
        self.assertEqual(e["before_sha"],
                         hashlib.sha256(sf.object_bytes(LS_PID)).hexdigest())
        _plan.validate_plan(plan)
        out = _plan.apply_plan(data, plan, dry_run=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "done")
        self.assertEqual(len(out["applied"]), 1)
        self.assertTrue(out["applied"][0]["verified"])

    def test_build_plan_mb_no_match_raises(self):
        sf = _ls_sf()
        with self.assertRaises(ValueError):
            _plan.build_plan(sf.raw, {"khong_co": "xxx"},
                             source_bundle="fx.assets", entry=None,
                             backend=BE, limits=LIM)

    def test_gate_pre_write_pass_fixture(self):
        # [FIXTURE] cong kiem tra pre_write PASS tren bang I2 tong hop
        # (ban dich khong dau de khoi can font hien thi).
        sf = _ls_sf()
        tr = {"greet": "Xin chao", "bye": "Tam biet"}
        verdict = _gate.run_gate(
            sf.raw, source_name="fx.assets", translations=tr,
            source_texts={"greet": "Hello", "bye": "Bye"})
        self.assertEqual(verdict["verdict"], "PASS")
        self.assertEqual(verdict["mode"], "pre_write")
        self.assertTrue(verdict["delivery_allowed"])


def _nine_kings_ipa():
    env = os.environ.get("NINEKINGS_IPA")
    if env:
        return Path(env)
    return Path.home() / "workspace" / "test-ipas" / "9kings-v1.0.6.ipa"


class TestRealNineKings(unittest.TestCase):
    @slow
    def test_preview_2252_keys(self):
        # [ĐÃ ĐO THẬT] 9K v1.0.6: dich en->vi ("Tiếng Việt: " + en) cho
        # dung 2252 key (2150 term I2 + 102 key StringTable) -> preview
        # phai ep du 2252, 0 unmatched, 0 refused; kiem tra diem: 1 term
        # I2, 1 entry ST, 1 entry ST chen moi (Cemetery_Desc).
        ipa_path = _nine_kings_ipa()
        if not ipa_path.exists():
            raise unittest.SkipTest("thieu IPA 9K: %s" % ipa_path)
        ipa = ipa_path.read_bytes()
        sample = json.loads(
            (Path.home() / "workspace" / "user" / "files" / "en.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(len(sample), 2252)
        tr = {k: ("Tiếng Việt: " + v) for k, v in sample.items()}
        res = _rb.build_rebuild_preview(
            ipa, source_name="9kings-v1.0.6.ipa", translations=tr)
        self.assertEqual(res["totals"]["changed"], 2252,
                         "preview doi %s thay vi 2252: %s"
                         % (res["totals"], res["unmatched"][:5]))
        self.assertEqual(res["totals"]["unmatched"], 0)
        self.assertEqual(res["totals"]["refused"], 0)
        # Kiem tra diem tren bytes da mo phong (duong gate).
        built = _gate._build_plans(ipa, "9kings-v1.0.6.ipa", tr, None, None,
                                   BE, LIM)
        self.assertEqual(built["total_changes"], 2252)
        sims = {p["vpath"]: p["simulated"] for p in built["plans"]}
        # 1. term I2 trong resources.assets.
        ls_sim = sims["Payload/9Kings.app/Data/resources.assets"]
        sf = u.parse_serialized(ls_sim, LIM, where="ls-sim")
        info = u.parse_language_source_bytes(sf.object_bytes(15312),
                                             sf.endian)
        self.assertEqual(info["terms"][0]["values"][0],
                         "Tiếng Việt: His Winningness")
        # 2. entry ST + entry chen moi trong bundle ST en.
        st_key = ("Payload/9Kings.app/Data/Raw/aa/iOS/"
                  "localization-string-tables-english(en)_assets_all.bundle")
        st_sim = None
        for vpath, data in sims.items():
            if vpath and vpath.startswith(st_key):
                st_sim = data
        self.assertIsNotNone(st_sim, "khong thay unit ST en trong plans")
        bundle = u.parse_bundle(st_sim, BE, LIM, where="st-sim")
        found_farm = False
        found_cemetery = False
        for ename in bundle.entry_names():
            sfx = u.parse_serialized(bundle.get_entry(ename), LIM,
                                     where=ename)
            for o in sfx.objects:
                if o.class_id != 114:
                    continue
                try:
                    val = u.read_monobehaviour_object(sfx, o.path_id)
                except PB3Error:
                    continue
                rows = val.get("m_TableData") or []
                for row in rows:
                    if row.get("m_Localized") == "Tiếng Việt: Farm":
                        found_farm = True
                    if row.get("m_Id") == 69153160815030272:
                        # Gia tri goc trong mau la "" nen ban dich tong hop
                        # la "Tiếng Việt: " + "" = "Tiếng Việt: ".
                        self.assertEqual(row.get("m_Localized"),
                                         "Tiếng Việt: ")
                        found_cemetery = True
        self.assertTrue(found_farm, "khong thay Farm da dich trong ST")
        self.assertTrue(found_cemetery,
                        "khong thay entry chen moi Cemetery_Desc trong ST")


if __name__ == "__main__":
    unittest.main()
