"""Test ho tro SerializedFile v24+ (sdk/unity.py).

Doi chieu UnityPy master (commit af1503d — AssetStudio/AssetRipper/UABE
deu dung o v22, khong co spec cong khai cho v23-26):
- v24/v25: layout giong het v23 (UnityPy khong co nhanh 24/25 nao).
- v23+ (TypeTreeNodeInfo): sau node blob LUON co u32 ref_hash_count +
  ref_hashes 16 byte (ke ca count = 0); fixture v23 cu cua repo thieu
  phan duoi nay nen parser chap nhan ca 2 dang (phan biet bang
  serialized_size, kiem tra chat).
- v26: them muc shared subtrees sau ref types; node co m_TypeFlags & 32
  thi children lay tu subtree chung theo ref_hashes[m_RefTypeHash].
- v27+: fail-closed E_UNSUPPORTED_FORMAT (chua co bang chung layout).

Fixture TU DUNG IN-MEMORY bang builder co san
(tests/fixtures/unity/build_fixtures.py) + blob type tree long nhau tu
viet o day (builder co san chi lam cay phang). Khong dung IPA that,
khong can mang. So lieu [FIXTURE]; kiem chung tren game that (9K/PB3/
Tom/PvZ Fusion: tat ca v22) nam o AUTOPILOT + bao cao merge.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    aligned_string, build_serialized_file, text_asset_data, typetree_blob,
    typetree_handler)

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import unity as u  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)
TEXT = (49, None)
MONO = (114, b"\x00" * 16)
SHARED_HASH = b"shared-subtree#1"
MISSING_HASH = b"missing-hash-000"


def _ta_blob(version):
    return typetree_blob(
        "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
        version=version)


def _simple_data(version, greeting="Xin chao!"):
    """SF 1 TextAsset, type tree day du (dang chuan UnityPy)."""
    return build_serialized_file(
        version=version, unity_version="6000.0.23f1", enable_type_tree=True,
        objects=[(4001, 0, text_asset_data("greeting", greeting))],
        types=[TEXT], typetree_blobs={0: _ta_blob(version)})


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


def _nested_blob_v26(spec, ref_hashes=()):
    """Blob type tree v26 long nhau tu spec (type, name, byte_size,
    [con], flags=0, ref_idx=0) — node 32 byte (Q m_RefTypeHash), mo dau
    "mhtt" + format version 26, ket thuc u32 ref_hash_count + ref hashes."""
    flat = []

    def walk(node, level, index):
        t, n, bs, children = node[0], node[1], node[2], node[3]
        fl = node[4] if len(node) > 4 else 0
        ri = node[5] if len(node) > 5 else 0
        flat.append((level, t, n, bs, index, fl, ri))
        for i, c in enumerate(children):
            walk(c, level + 1, i)

    walk(spec, 0, 0)
    names = []
    for _lv, t, n, _bs, _ix, _fl, _ri in flat:
        for s in (t, n):
            if s not in names:
                names.append(s)
    strings = b"".join(s.encode("utf-8") + b"\x00" for s in names)

    def off(s):
        return strings.index(s.encode("utf-8"))

    body = b"".join(
        struct.pack("<hBBIIiiiQ", 1, lv, fl, off(t), off(n), bs, ix, 0, ri)
        for lv, t, n, bs, ix, fl, ri in flat)
    blob = struct.pack("<ii", len(flat), len(strings)) + body + strings
    blob = b"mhtt" + struct.pack("<i", 26) + blob
    blob += struct.pack("<I", len(ref_hashes))
    for h in ref_hashes:
        blob += h
    return blob


def _shared_data_info():
    """Info cua shared subtree "SharedData": 2 leaf int."""
    return typetree_blob(
        "SharedData", [("int", "m_Score"), ("int", "m_Level")], version=26,
        byte_sizes={1: 4, 2: 4})


def _v26_data(user_information=b"v26-fixture"):
    """SF v26: 1 TextAsset + 1 MonoBehaviour co node flag-32 tham chieu
    shared subtree; object MB = 2 int (m_Score=100, m_Level=5)."""
    handler = typetree_handler(SHARED_HASH, _shared_data_info())
    blob26 = typetree_blob(
        "MonoBehaviour", [("SharedData", "m_Shared")], version=26,
        ref_hashes=[SHARED_HASH], node_flags={1: (32, 0)})
    return build_serialized_file(
        version=26, unity_version="6000.0.23f1", enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao v26!")),
            (4006, 1, struct.pack("<ii", 100, 5)),
        ], types=[TEXT, MONO],
        typetree_blobs={0: _ta_blob(26), 1: blob26},
        shared_subtrees=[handler], user_information=user_information)


def _v26_mb_data():
    """SF v26 voi MonoBehaviour co struct long nhau qua shared subtree
    (m_Nested.m_Label la chuoi) — de test duong ghi MB qua cay da resolve."""
    shared_info = _nested_blob_v26(
        ("MyNested", "m_Nested", -1, [
            _string_node("m_Label"),
            ("int", "m_Value", 4, []),
        ]))
    handler = typetree_handler(SHARED_HASH, shared_info)
    mb_spec = ("MonoBehaviour", "MonoBehaviour", -1, [
        _pptr_node("m_GameObject", "GameObject"),
        ("bool", "m_Enabled", 1, []),
        _pptr_node("m_Script", "MonoScript"),
        _string_node("m_Name"),
        _string_node("m_Title"),
        ("MyNested", "m_Nested", -1, [], 32, 0),
    ])
    blob26 = _nested_blob_v26(mb_spec, ref_hashes=[SHARED_HASH])
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
    w.aligned_string("nested")          # m_Nested.m_Label
    w.align(4)
    w.pack("i", 7)          # m_Nested.m_Value
    return build_serialized_file(
        version=26, unity_version="6000.0.23f1", enable_type_tree=True,
        objects=[(4006, 0, w.bytes())],
        types=[MONO], typetree_blobs={0: blob26},
        shared_subtrees=[handler], user_information=b"v26-mb")


class TestV24V25(unittest.TestCase):
    """v24/v25: layout = v23 (UnityPy khong co nhanh 24/25)."""

    def test_v24_parse(self):
        sf = u.parse_serialized(_simple_data(24, "Xin chao v24!"))
        self.assertEqual(sf.version, 24)
        self.assertEqual(sf.unity_version, "6000.0.23f1")
        self.assertTrue(sf.enable_type_tree)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao v24!")

    def test_v24_roundtrip_byte_identical(self):
        data = _simple_data(24)
        sf = u.parse_serialized(data)
        rebuilt = u.build_serialized(sf, {})
        self.assertEqual(rebuilt, data)
        sf2 = u.parse_serialized(rebuilt)
        self.assertEqual(sf2.text_assets(), sf.text_assets())

    def test_v24_patch_inplace(self):
        r = u.apply_patch(u.PatchRequest(
            data=_simple_data(24, "Xin chao Unity 6!"), kind="assets",
            path_id=4001, old_text="Unity 6", new_text="Unity 7"))
        self.assertEqual(r.write_mode_used, "PATCH_INPLACE")
        self.assertTrue(r.verified)
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.version, 24)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao Unity 7!")

    def test_v24_patch_rebuild_section(self):
        r = u.apply_patch(u.PatchRequest(
            data=_simple_data(24), kind="assets", path_id=4001,
            old_text="Xin chao",
            new_text="Xin chao cac ban choi game v24",
            write_mode="REBUILD_SECTION"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.version, 24)
        self.assertEqual(sf.text_assets()[0][2],
                         "Xin chao cac ban choi game v24!")

    def test_v25_parse_and_roundtrip(self):
        data = _simple_data(25, "Xin chao v25!")
        sf = u.parse_serialized(data)
        self.assertEqual(sf.version, 25)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao v25!")
        self.assertEqual(u.build_serialized(sf, {}), data)

    def test_v24_type_without_typetree(self):
        # size 0 -> node None, object van doc duoc (theo UnityPy).
        data = build_serialized_file(
            version=24, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4001, 0, text_asset_data("greeting", "Xin chao!")),
                     (4002, 1, aligned_string("Dummy"))],
            types=[TEXT, MONO], typetree_blobs={0: _ta_blob(24)})
        sf = u.parse_serialized(data)
        self.assertIsNone(sf.types[1].node)
        self.assertEqual(sf.types[0].node.type, "TextAsset")
        self.assertEqual(u.build_serialized(sf, {}), data)


class TestV23RefHashes(unittest.TestCase):
    """v23 dang chuan UnityPy: info co duoi ref_hash_count + ref_hashes."""

    def test_parse_ref_hashes(self):
        blob23 = typetree_blob(
            "MonoBehaviour", [("string", "m_Name")], version=23,
            ref_hashes=[SHARED_HASH], node_flags={1: (32, 0)})
        data = build_serialized_file(
            version=23, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4001, 0, text_asset_data("greeting", "Xin chao!")),
                     (4005, 1, aligned_string("SharedRef"))],
            types=[TEXT, MONO],
            typetree_blobs={0: _ta_blob(23), 1: blob23})
        sf = u.parse_serialized(data)
        self.assertEqual(sf.types[1].ref_hashes, [SHARED_HASH])
        self.assertEqual(sf.types[1].node.children[0].type_flags, 32)
        self.assertEqual(sf.types[1].node.children[0].ref_type_hash, 0)
        # v23 khong co bang shared: node flag-32 giu children inline (rong).
        self.assertEqual(sf.types[1].node.children[0].children, [])
        self.assertEqual(u.build_serialized(sf, {}), data)

    def test_legacy_shape_still_parses(self):
        # Fixture v23 cu cua repo: blob khong co duoi ref_hash_count.
        legacy = _ta_blob(23)[:-4]  # cat u32 count=0 o cuoi info
        data = build_serialized_file(
            version=23, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4001, 0, text_asset_data("greeting", "Xin chao!"))],
            types=[TEXT], typetree_blobs={0: legacy})
        sf = u.parse_serialized(data)
        self.assertEqual(sf.types[0].ref_hashes, [])
        self.assertEqual(sf.text_assets()[0][2], "Xin chao!")
        self.assertEqual(u.build_serialized(sf, {}), data)

    def test_bad_ref_hash_count_fails_closed(self):
        # serialized_size khai lon hon thuc te 4 byte -> phan du khong
        # phai ref hashes -> E_CORRUPT (khong lang le bo qua).
        blob = bytearray(_ta_blob(23))
        # info = blob; tang size trong handler len 4 bang cach chen 4 byte
        # rac vao cuoi info (khong phai u32 count hop le + hash).
        bad_info = bytes(blob) + b"\x99\x99\x99\x99"
        data = build_serialized_file(
            version=23, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4001, 0, text_asset_data("greeting", "Xin chao!"))],
            types=[TEXT], typetree_blobs={0: bad_info})
        with self.assertRaises(PB3Error) as cm:
            u.parse_serialized(data)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestV26SharedSubtrees(unittest.TestCase):
    """v26: muc shared subtrees + resolve node flag-32."""

    def test_parse(self):
        sf = u.parse_serialized(_v26_data())
        self.assertEqual(sf.version, 26)
        self.assertEqual(sf.user_information, "v26-fixture")
        self.assertEqual(len(sf.shared_subtrees), 1)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao v26!")

    def test_resolve_children(self):
        sf = u.parse_serialized(_v26_data())
        shared = sf.types[1].node.children[0]
        self.assertEqual(shared.type, "SharedData")
        self.assertEqual(shared.type_flags, 32)
        self.assertEqual([c.name for c in shared.children],
                         ["m_Score", "m_Level"])

    def test_read_value_via_resolved_tree(self):
        sf = u.parse_serialized(_v26_data())
        val, _pos = u.read_typetree_value(
            sf.object_bytes(4006), sf.types[1].node, 0)
        self.assertEqual(val, {"m_Shared": {"m_Score": 100, "m_Level": 5}})

    def test_roundtrip_byte_identical(self):
        data = _v26_data()
        sf = u.parse_serialized(data)
        rebuilt = u.build_serialized(sf, {})
        self.assertEqual(rebuilt, data)
        sf2 = u.parse_serialized(rebuilt)
        self.assertEqual(
            [c.name for c in sf2.types[1].node.children[0].children],
            ["m_Score", "m_Level"])
        self.assertEqual(sf2.user_information, "v26-fixture")

    def test_patch_textasset_keeps_shared_section(self):
        r = u.apply_patch(u.PatchRequest(
            data=_v26_data(), kind="assets", path_id=4001,
            old_text="Xin chao", new_text="Xin chao v26 da dich",
            write_mode="REBUILD_SECTION"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.version, 26)
        self.assertEqual(len(sf.shared_subtrees), 1)
        self.assertEqual(sf.user_information, "v26-fixture")
        self.assertEqual(
            [c.name for c in sf.types[1].node.children[0].children],
            ["m_Score", "m_Level"])

    def test_mb_patch_via_shared_subtree(self):
        data = _v26_mb_data()
        res = u.apply_monobehaviour_patch(u.MbPatchRequest(
            data=data, kind="assets", path_id=4006,
            changes=[{"field_path": "m_Nested.m_Label", "before": "nested",
                      "after": "da dich"}],
            write_mode="REBUILD_SECTION", backend=BE, limits=LIM))
        self.assertTrue(res.verified)
        self.assertEqual(res.write_mode_used, "REBUILD_SECTION")
        sf2 = u.parse_serialized(res.output_bytes, LIM, where="after")
        val = u.read_monobehaviour_object(sf2, 4006)
        self.assertEqual(val["m_Nested"]["m_Label"], "da dich")
        self.assertEqual(val["m_Nested"]["m_Value"], 7)
        self.assertEqual(val["m_Title"], "Hello World")
        self.assertEqual(len(sf2.shared_subtrees), 1)

    def test_missing_hash_fails_closed(self):
        bad_blob = typetree_blob(
            "MonoBehaviour", [("SharedData", "m_Shared")], version=26,
            ref_hashes=[MISSING_HASH], node_flags={1: (32, 0)})
        handler = typetree_handler(SHARED_HASH, _shared_data_info())
        data = build_serialized_file(
            version=26, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4006, 1, struct.pack("<ii", 1, 2))],
            types=[MONO], typetree_blobs={0: bad_blob},
            shared_subtrees=[handler], user_information=b"x")
        with self.assertRaises(PB3Error) as cm:
            u.parse_serialized(data)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_cycle_fails_closed(self):
        # Shared subtree tu tham chieu chinh no (flag-32 -> chinh hash
        # cua no) -> E_CORRUPT, khong de quy vo han.
        loop_info = _nested_blob_v26(
            ("SharedData", "SharedData", -1, [
                ("SharedData", "m_Loop", -1, [], 32, 0),
            ]), ref_hashes=[SHARED_HASH])
        handler = typetree_handler(SHARED_HASH, loop_info)
        blob26 = typetree_blob(
            "MonoBehaviour", [("SharedData", "m_Shared")], version=26,
            ref_hashes=[SHARED_HASH], node_flags={1: (32, 0)})
        data = build_serialized_file(
            version=26, unity_version="6000.0.23f1", enable_type_tree=True,
            objects=[(4006, 1, struct.pack("<ii", 1, 2))],
            types=[MONO], typetree_blobs={0: blob26},
            shared_subtrees=[handler], user_information=b"x")
        with self.assertRaises(PB3Error) as cm:
            u.parse_serialized(data)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestV27FailClosed(unittest.TestCase):
    """v27+: chua co bang chung layout -> tu choi ro rang."""

    def test_v27_rejected(self):
        data = _simple_data(27)
        with self.assertRaises(PB3Error) as cm:
            u.parse_serialized(data)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_support_matrix_mentions_v26(self):
        rows = u.support_matrix()
        text = " ".join(r["format"] for r in rows)
        self.assertIn("v15-26", text)
        self.assertNotIn("v15-23", text)


if __name__ == "__main__":
    unittest.main()
