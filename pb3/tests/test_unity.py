"""Test backend Unity (Claude 05): detector, UnityFS, SerializedFile, LZ4 thuần,
hybrid backend, planner, patcher, CLI `unity`/`mod`.

Fixture tự tạo trong tests/fixtures/unity/ (xây bằng tests/fixtures/unity/
build_fixtures.py, độc lập với UnityPy); UnityPy chỉ là bên xác minh độc lập
ngoài sandbox. Không dùng IPA thật của người dùng.
"""
import hashlib
import importlib.metadata
import importlib.util
import io
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.errors import PB3Error
from sdk import lz4pure, unity as u
from tests.helpers import ROOT, TmpCase, copy_project, register_tmp_dir, slow_subprocess, subprocess_timeout, unregister_tmp_dir
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

FX = ROOT / "tests" / "fixtures" / "unity"
RES = FX / "resources.assets"
SHARED = FX / "sharedassets0.assets"
BUNDLE = FX / "sample.unity3d"
BUNDLE_LZ4 = FX / "sample_lz4.unity3d"
BUNDLE_LZMA = FX / "sample_lzma.unity3d"
BUNDLE_V6 = FX / "sample_v6.unity3d"
BUNDLE_V8 = FX / "sample_v8.unity3d"
BUNDLE_V8_LZ4 = FX / "sample_v8_lz4.unity3d"
TYPETREE = FX / "sample_typetree.assets"
V21 = FX / "sample_v21.assets"
V23 = FX / "sample_v23.assets"
NESTED_IPA = FX / "nested.ipa"
NESTED_V8_IPA = FX / "nested_v8.ipa"
ENTRY1 = "CAB-0123456789abcdef0123456789abcdef"
ENTRY2 = "CAB-0123456789abcdef0123456789abcde0"


def h(data):
    return hashlib.sha256(data).hexdigest()


def read(name):
    return Path(name).read_bytes()


def nat():
    return u.UnityBackend("NATIVE")


class TestDetect(unittest.TestCase):
    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_bundle_v7_none(self):
        info = u.detect_bundle(read(BUNDLE))
        self.assertIsNotNone(info)
        self.assertEqual(info["version"], 7)
        self.assertEqual(info["unity_version"], "2019.4.40f1")
        self.assertEqual(u.parse_bundle(read(BUNDLE), nat()).compression_name, "none")

    @pytest.mark.needs_fixture("unity/sample_lz4.unity3d", "unity/sample_lzma.unity3d", "unity/sample_v6.unity3d")
    def test_bundle_variants(self):
        self.assertEqual(u.parse_bundle(read(BUNDLE_LZ4), nat()).compression_name, "lz4")
        self.assertEqual(u.parse_bundle(read(BUNDLE_LZMA), nat()).compression_name, "lzma")
        info6 = u.detect_bundle(read(BUNDLE_V6))
        self.assertIsNotNone(info6)
        self.assertEqual(info6["version"], 6)

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_not_bundle(self):
        self.assertIsNone(u.detect_bundle(b"hello world"))
        self.assertIsNone(u.detect_bundle(read(RES)))

    def test_detect_engine_bundle(self):
        v = u.detect_engine(["sample.unity3d"])
        self.assertEqual(v.engine, "unity")
        self.assertEqual(v.level, "CÓ KHẢ NĂNG")  # 1 tín hiệu mạnh (.unity3d)

    def test_detect_engine_ipa(self):
        v = u.detect_engine(["Payload/Game.app/Data/level.unity3d",
                             "Payload/Game.app/Info.plist"])
        self.assertEqual(v.engine, "unity")
        self.assertEqual(v.level, "CÓ KHẢ NĂNG")
        self.assertTrue(any("level.unity3d" in s for s in v.reasons))

    def test_detect_engine_unknown(self):
        v = u.detect_engine(["plain.txt"])
        self.assertEqual(v.engine, "unknown")
        self.assertEqual(v.level, "CHƯA ĐỦ BẰNG CHỨNG")

    def test_parse_unity_version(self):
        self.assertEqual(u.parse_unity_version("2019.4.40f1"), (2019, 4, 40))
        self.assertEqual(u.parse_unity_version("2022.3.1f1"), (2022, 3, 1))
        self.assertEqual(u.parse_unity_version("6000.0.23f1"), (6000, 0, 23))
        self.assertEqual(u.parse_unity_version("không phải version"), ())
        self.assertEqual(u.parse_unity_version("5.x.x"), ())  # player version


class TestLZ4Pure(unittest.TestCase):
    def test_decode_literal_only(self):
        raw = b"Xin chao 9 Kings! " * 40
        enc = lz4pure.compress_block_literals(raw)
        self.assertEqual(lz4pure.decompress_block(enc, len(raw)), raw)

    def test_decode_matches_binding_when_available(self):
        try:
            import lz4.block
        except ImportError:
            self.skipTest("không có binding lz4 trong sandbox")
        raw = (b"abcabcabc" * 200) + bytes(range(256)) + b"tail" * 50
        comp = lz4.block.compress(raw, store_size=False)
        dec = lz4pure.decompress_block(comp, len(raw))
        self.assertEqual(dec, raw)
        # encoder thuần (literal-only) phải ra block mà binding đọc được
        comp2 = lz4pure.compress_block_literals(raw)
        self.assertEqual(lz4.block.decompress(comp2, uncompressed_size=len(raw)), raw)

    def test_decode_invalid_raises(self):
        with self.assertRaises(PB3Error) as cm:
            lz4pure.decompress_block(b"\xff\xff\xff\xff", 100)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_decode_truncated_raises(self):
        enc = lz4pure.compress_block_literals(b"0123456789abcdef")
        with self.assertRaises(PB3Error):
            lz4pure.decompress_block(enc[:-2], 16)

    def test_frame_detect_and_decode(self):
        try:
            import lz4.frame
        except ImportError:
            self.skipTest("không có lz4.frame trong sandbox")
        raw = b"frame test " * 300
        framed = lz4.frame.compress(raw)
        self.assertTrue(lz4pure.is_lz4_frame(framed))
        self.assertEqual(lz4pure.decompress_frame(framed), raw)

    def test_xxhash32_vectors(self):
        self.assertEqual(lz4pure.xxhash32(b""), 0x02CC5D05)
        self.assertEqual(lz4pure.xxhash32(b"a"), 0x550D7456)
        self.assertEqual(lz4pure.xxhash32(b"abc"), 0x32D153FF)


class TestBundleParse(unittest.TestCase):
    def _check_entries(self, bundle):
        self.assertEqual([e.name for e in bundle.entries], [ENTRY1, ENTRY2])
        sf = u.parse_serialized(bundle.get_entry(ENTRY1))
        self.assertEqual([o.path_id for o in sf.objects], [1001, 1002, 1003])
        names = {pid: n for pid, n, _s in sf.text_assets()}
        self.assertEqual(names[1001], "greeting")
        self.assertEqual(names[1002], "longtext")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_v7_none(self):
        self._check_entries(u.parse_bundle(read(BUNDLE), nat()))

    @pytest.mark.needs_fixture("unity/sample_lz4.unity3d")
    def test_v7_lz4_blocks_at_end(self):
        # sample_lz4.unity3d dùng blocks-info-at-end (flag 0x80)
        self._check_entries(u.parse_bundle(read(BUNDLE_LZ4), nat()))

    @pytest.mark.needs_fixture("unity/sample_lzma.unity3d")
    def test_v7_lzma(self):
        self._check_entries(u.parse_bundle(read(BUNDLE_LZMA), nat()))

    @pytest.mark.needs_fixture("unity/sample_v6.unity3d")
    def test_v6(self):
        self._check_entries(u.parse_bundle(read(BUNDLE_V6), nat()))

    @pytest.mark.needs_fixture("unity/resources.assets", "unity/sample.unity3d")
    def test_truncated_raises(self):
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(read(BUNDLE)[:100], nat())
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_bad_magic_raises(self):
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(b"NotUnity" + read(BUNDLE)[8:], nat())
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_unsupported_version_raises(self):
        data = bytearray(read(BUNDLE))
        # version là u32 big-endian ở offset 8 (sau "UnityFS\0")
        # v9: không có bằng chứng tồn tại (Unity 6 mới nhất vẫn dùng v8),
        # không bịa hỗ trợ (decisions #77).
        data[8:12] = struct.pack(">I", 9)  # UnityFS v9 (chưa hỗ trợ)
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(bytes(data), nat())
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    @pytest.mark.needs_fixture("unity/resources.assets", "unity/sharedassets0.assets")
    def test_build_roundtrip_none_and_lz4(self):
        raw1 = read(RES)
        raw2 = read(SHARED)
        for compression in ("none", "lz4"):
            built = u.build_bundle([(ENTRY1, 0, raw1), (ENTRY2, 0, raw2)],
                                   compression=compression, backend=nat())
            parsed = u.parse_bundle(built, nat())
            self.assertEqual(parsed.compression_name, compression)
            self.assertEqual(parsed.get_entry(ENTRY1), raw1)
            self.assertEqual(parsed.get_entry(ENTRY2), raw2)

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_build_unknown_entry_raises(self):
        bundle = u.parse_bundle(read(BUNDLE), nat())
        with self.assertRaises(PB3Error) as cm:
            bundle.get_entry("khong-co")
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")


class TestSerializedParse(unittest.TestCase):
    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_resources_assets(self):
        sf = u.parse_serialized(read(RES))
        self.assertEqual(sf.version, 17)
        self.assertEqual(sf.unity_version, "2019.4.40f1")
        texts = {pid: (n, s) for pid, n, s in sf.text_assets()}
        self.assertEqual(texts[1001][1], "Xin chao 9 Kings!")
        self.assertEqual(texts[1002][0], "longtext")
        self.assertEqual(len(texts[1002][1]), 5437)  # chuỗi dài
        self.assertIn(b"DummyBehaviour", sf.object_bytes(1003))  # MonoBehaviour mờ

    @pytest.mark.needs_fixture("unity/sharedassets0.assets")
    def test_sharedassets_json(self):
        sf = u.parse_serialized(read(SHARED))
        texts = dict((pid, s) for pid, _n, s in sf.text_assets())
        self.assertEqual(texts[2001], '{"level": 1, "lang": "vi", "title": "9 Kings"}')

    @pytest.mark.needs_fixture("unity/sample_typetree.assets")
    def test_typetree_blob(self):
        sf = u.parse_serialized(read(TYPETREE))
        self.assertTrue(sf.enable_type_tree)
        self.assertEqual(len(sf.types), 1)
        node = sf.types[0].node
        self.assertEqual(node.type, "TextAsset")
        self.assertEqual([c.name for c in node.children], ["m_Name", "m_Script"])
        self.assertEqual(sf.text_assets()[0][1], "greeting")

    @pytest.mark.needs_fixture("unity/sample_v21.assets")
    def test_v21(self):
        sf = u.parse_serialized(read(V21))
        self.assertEqual(sf.version, 21)
        self.assertEqual(sf.text_assets()[0], (3001, "v21text", "SerializedFile v21 ok"))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_truncated_raises(self):
        with self.assertRaises(PB3Error) as cm:
            u.parse_serialized(read(RES)[:50])
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_build_serialized_roundtrip(self):
        sf = u.parse_serialized(read(RES))
        rebuilt = u.build_serialized(sf, {})  # {} = giữ nguyên mọi object
        sf2 = u.parse_serialized(rebuilt)
        self.assertEqual(sf2.text_assets(), sf.text_assets())
        self.assertEqual(sf2.object_bytes(1003), sf.object_bytes(1003))
        self.assertEqual(sf2.data_offset, sf.data_offset)


class TestBundleV8(unittest.TestCase):
    """UnityFS v8 + Unity 6 (Claude 05b, decisions #77-78)."""

    def _check_v8_entries(self, bundle):
        self.assertEqual(bundle.version, 8)
        self.assertEqual(bundle.unity_version, "5.x.x")  # player version
        self.assertEqual(bundle.unity_revision, "6000.0.23f1")
        self.assertEqual([e.name for e in bundle.entries], [ENTRY1, ENTRY2])
        sf = u.parse_serialized(bundle.get_entry(ENTRY1))
        self.assertEqual(sf.version, 23)
        self.assertEqual([o.path_id for o in sf.objects], [4001, 4002, 4003])
        names = {pid: n for pid, n, _s in sf.text_assets()}
        self.assertEqual(names[4001], "greeting")
        self.assertEqual(names[4002], "longtext")

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_detect_v8(self):
        info = u.detect_bundle(read(BUNDLE_V8))
        self.assertIsNotNone(info)
        self.assertEqual(info["version"], 8)
        self.assertEqual(info["unity_version"], "5.x.x")
        self.assertEqual(info["unity_revision"], "6000.0.23f1")

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_v8_none(self):
        self._check_v8_entries(u.parse_bundle(read(BUNDLE_V8), nat()))
        sf = u.parse_serialized(
            u.parse_bundle(read(BUNDLE_V8), nat()).get_entry(ENTRY1))
        self.assertEqual(sf.text_assets()[0][2], "Xin chao Unity 6!")

    @pytest.mark.needs_fixture("unity/sample_v8_lz4.unity3d")
    def test_v8_lz4_blocks_at_end_padded(self):
        # sample_v8_lz4: LZ4 + blocks-info-at-end (0x80) + padding (0x200)
        bundle = u.parse_bundle(read(BUNDLE_V8_LZ4), nat())
        self.assertEqual(bundle.compression_name, "lz4")
        self.assertTrue(bundle.blocks_info_at_end)
        self.assertTrue(bundle.uses_padding)
        self._check_v8_entries(bundle)

    def test_flag_gating_by_revision(self):
        # Cờ chọn theo unityRevision (engine), không theo unityVersion
        # (player "5.x.x" parse ra () -> mặc định cờ mới).
        self.assertFalse(u._uses_new_flags("2019.4.40f1"))
        self.assertFalse(u._uses_new_flags("2020.3.33f1"))
        self.assertTrue(u._uses_new_flags("2020.3.34f1"))
        self.assertTrue(u._uses_new_flags("2022.3.58f1"))
        self.assertTrue(u._uses_new_flags("6000.0.23f1"))
        self.assertTrue(u._uses_new_flags("5.x.x"))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_revision_gates_encryption_flag_old(self):
        # Bundle 2019 + cờ 0x200 = MÃ HÓA (cờ cũ), không phải padding.
        built = u.build_bundle([(ENTRY1, 0, read(RES))], version=7,
                               unity_version="2019.4.40f1",
                               extra_data_flags=0x200, backend=nat())
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(built, nat())
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_revision_gates_encryption_flag_new(self):
        # Bundle Unity 6 + cờ 0x1400 (0x400 cũ / 0x1000 mới) = MÃ HÓA.
        built = u.build_bundle([(ENTRY1, 0, read(RES))], version=8,
                               unity_version="5.x.x",
                               unity_revision="6000.0.23f1",
                               extra_data_flags=0x1400, backend=nat())
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(built, nat())
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_padding_flag_new_revision_parses(self):
        # Bundle Unity 6 + cờ 0x200 = PADDING (cờ mới), không phải mã hóa;
        # vùng block data được căn 16 nên đọc đúng.
        built = u.build_bundle([(ENTRY1, 0, read(RES))], version=8,
                               unity_version="5.x.x",
                               unity_revision="6000.0.23f1",
                               extra_data_flags=0x200, pad_block_data=True,
                               backend=nat())
        bundle = u.parse_bundle(built, nat())
        self.assertTrue(bundle.uses_padding)
        self.assertEqual(bundle.get_entry(ENTRY1), read(RES))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_old_revision_with_padding_bit_is_encrypted(self):
        # Cùng bit 0x200 nhưng revision cũ -> mã hóa (dù writer có căn đệm).
        built = u.build_bundle([(ENTRY1, 0, read(RES))], version=7,
                               unity_version="2019.4.40f1",
                               extra_data_flags=0x200, pad_block_data=True,
                               backend=nat())
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(built, nat())
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_build_roundtrip_v8_preserves_revision(self):
        raw1 = read(V23)
        built = u.build_bundle([(ENTRY1, 0, raw1)], version=8,
                               unity_version="5.x.x",
                               unity_revision="6000.0.23f1", backend=nat())
        parsed = u.parse_bundle(built, nat())
        self.assertEqual(parsed.version, 8)
        self.assertEqual(parsed.unity_version, "5.x.x")
        self.assertEqual(parsed.unity_revision, "6000.0.23f1")
        self.assertEqual(parsed.get_entry(ENTRY1), raw1)

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_plan_v8_bundle(self):
        plan = u.plan_bytes(read(BUNDLE_V8), name="sample_v8.unity3d",
                            backend=nat())
        self.assertEqual(plan.verdict.engine, "unity")
        self.assertEqual(plan.verdict.level, "CHẮC CHẮN")
        self.assertIn("UnityFS v8 (6000.0.23f1)", plan.verdict.reasons[0])
        ta = [r for r in plan.rows if r.object_type == "TextAsset"]
        self.assertEqual({r.path_id for r in ta}, {4001, 4002, 4004})

    @pytest.mark.needs_fixture("unity/nested_v8.ipa")
    def test_plan_nested_v8_ipa(self):
        plan = u.plan_bytes(read(NESTED_V8_IPA), name="nested_v8.ipa",
                            backend=nat())
        self.assertEqual(plan.verdict.engine, "unity")
        ta = [r for r in plan.rows if r.object_type == "TextAsset"]
        self.assertEqual({r.path_id for r in ta}, {4001, 4002, 4004})


class TestSerializedV23(unittest.TestCase):
    """SerializedFile v23 + Unity 6 (Claude 05b, decisions #77, #80)."""

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_parse(self):
        sf = u.parse_serialized(read(V23))
        self.assertEqual(sf.version, 23)
        self.assertEqual(sf.unity_version, "6000.0.23f1")
        self.assertTrue(sf.enable_type_tree)
        texts = {pid: (n, s) for pid, n, s in sf.text_assets()}
        self.assertEqual(texts[4001][1], "Xin chao Unity 6!")
        self.assertEqual(texts[4002][0], "longtext")
        self.assertEqual(len(texts[4002][1]), 5437)  # chuỗi dài
        self.assertIn(b"DummyBehaviour6", sf.object_bytes(4003))

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_typetree_blob(self):
        sf = u.parse_serialized(read(V23))
        self.assertEqual(len(sf.types), 2)
        node = sf.types[0].node
        self.assertEqual(node.type, "TextAsset")
        self.assertEqual([c.name for c in node.children], ["m_Name", "m_Script"])
        # blob v23 mở đầu bằng magic "mhtt" + format version
        self.assertTrue(sf.types[0].raw_typetree.startswith(b"mhtt"))
        self.assertEqual(sf.types[1].node.type, "MonoBehaviour")

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_extended_header_is_big_endian(self):
        # Hồi quy #80: cả 48 byte đầu đều big-endian (theo UnityPy);
        # đọc bằng endian của file sẽ đảo byte metadata_size/data_offset.
        data = read(V23)
        sf = u.parse_serialized(data)
        ems, efs, edoff, unk = struct.unpack_from(">IQQQ", data, 20)
        self.assertEqual(ems, sf.metadata_size)
        self.assertEqual(efs, len(data))
        self.assertEqual(edoff, sf.data_offset)
        self.assertEqual(unk, 0)

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_build_roundtrip(self):
        sf = u.parse_serialized(read(V23))
        rebuilt = u.build_serialized(sf, {})  # {} = giữ nguyên mọi object
        sf2 = u.parse_serialized(rebuilt)
        self.assertEqual(sf2.text_assets(), sf.text_assets())
        self.assertEqual(sf2.object_bytes(4003), sf.object_bytes(4003))
        self.assertEqual(sf2.data_offset, sf.data_offset)
        # header mở rộng của bản dựng lại cũng big-endian
        ems, efs, edoff, _unk = struct.unpack_from(">IQQQ", rebuilt, 20)
        self.assertEqual(ems, sf2.metadata_size)
        self.assertEqual(edoff, sf2.data_offset)

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_empty_typetree(self):
        # Type tree size 0 -> node None, object vẫn đọc được (theo UnityPy).
        sf = u.parse_serialized(read(V23))
        sf.types[1].raw_typetree = b""
        rebuilt = u.build_serialized(sf, {})
        sf2 = u.parse_serialized(rebuilt)
        self.assertIsNone(sf2.types[1].node)
        self.assertEqual(sf2.types[0].node.type, "TextAsset")
        self.assertEqual(sf2.text_assets()[0][2], "Xin chao Unity 6!")
        self.assertEqual(sf2.object_bytes(4003), sf.object_bytes(4003))

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_patch_inplace(self):
        r = u.apply_patch(u.PatchRequest(
            data=read(V23), kind="assets", path_id=4001,
            old_text="Unity 6", new_text="Unity 7"))
        self.assertEqual(r.write_mode_used, "PATCH_INPLACE")
        self.assertTrue(r.verified)
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.version, 23)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao Unity 7!")

    @pytest.mark.needs_fixture("unity/sample_v23.assets")
    def test_v23_patch_rebuild_section(self):
        before = u.parse_serialized(read(V23))
        r = u.apply_patch(u.PatchRequest(
            data=read(V23), kind="assets", path_id=4001,
            old_text="Xin chao",
            new_text="Xin chao cac ban choi game Unity 6",
            write_mode="REBUILD_SECTION"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.version, 23)
        self.assertEqual(sf.text_assets()[0][2],
                         "Xin chao cac ban choi game Unity 6 Unity 6!")
        # object không liên quan giữ nguyên (longtext + MonoBehaviour mờ)
        self.assertEqual(sf.text_assets()[1][2],
                         before.text_assets()[1][2])
        self.assertEqual(sf.object_bytes(4003), before.object_bytes(4003))

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_v23_patch_bundle_keeps_v8(self):
        r = u.apply_patch(u.PatchRequest(
            data=read(BUNDLE_V8), kind="bundle", entry=ENTRY1, path_id=4001,
            old_text="Unity 6", new_text="Unity 7"))
        self.assertTrue(r.verified)
        bundle = u.parse_bundle(r.output_bytes, nat())
        self.assertEqual(bundle.version, 8)  # giữ v8
        self.assertEqual(bundle.unity_revision, "6000.0.23f1")  # giữ revision
        sf = u.parse_serialized(bundle.get_entry(ENTRY1))
        self.assertEqual(sf.text_assets()[0][2], "Xin chao Unity 7!")
        # entry còn lại giữ nguyên
        before = u.parse_bundle(read(BUNDLE_V8), nat())
        self.assertEqual(bundle.get_entry(ENTRY2), before.get_entry(ENTRY2))


class TestBackendModes(unittest.TestCase):
    def test_probe_backends_matches_environment(self):
        # Probe phải khớp môi trường thật (dùng find_spec làm kỳ vọng), không
        # giả định sandbox/iSH: trên iSH UnityPy có mặt, ở sandbox thì không.
        probe = u.probe_backends()
        self.assertEqual(set(probe), {"python", "lz4", "unitypy"})
        self.assertEqual(probe["python"]["status"], "ok")
        self.assertEqual(probe["unitypy"]["status"],
                         "ok" if importlib.util.find_spec("UnityPy") is not None
                         else "missing")
        self.assertEqual(probe["lz4"]["status"],
                         "ok" if importlib.util.find_spec("lz4.block") is not None
                         else "missing")
        # Probe không được import UnityPy (import thật trên iSH mất ~1 phút).
        before = "UnityPy" in sys.modules
        u._reset_probe_cache()
        try:
            u.probe_backends()
        finally:
            u._reset_probe_cache()
        self.assertEqual("UnityPy" in sys.modules, before)

    def test_probe_does_not_import_unitypy_when_present(self):
        # Mô phỏng iSH: UnityPy có mặt trên sys.path (find_spec thấy) nhưng
        # chưa import. Probe + khởi tạo AUTO không được thực thi module.
        real_find_spec = importlib.util.find_spec
        real_version = importlib.metadata.version

        def fake_find_spec(name):
            if name == "UnityPy":
                return mock.Mock()
            return real_find_spec(name)

        def fake_version(dist):
            if dist == "UnityPy":
                return "1.25.4"
            return real_version(dist)

        u._reset_probe_cache()
        try:
            with mock.patch.object(u, "find_spec", side_effect=fake_find_spec), \
                    mock.patch.object(u.importlib_metadata, "version",
                                      side_effect=fake_version):
                before = "UnityPy" in sys.modules
                probe = u.probe_backends()
                self.assertEqual(probe["unitypy"]["status"], "ok")
                self.assertEqual(probe["unitypy"]["version"], "1.25.4")
                b = u.UnityBackend("AUTO")
                self.assertTrue(any("UnityPy 1.25.4 có mặt" in d for d in b.decisions))
                self.assertEqual("UnityPy" in sys.modules, before)
        finally:
            u._reset_probe_cache()

    def test_probe_backends_cached(self):
        # Probe lần 2 không quét lại sys.path: nhiều UnityBackend() trong một
        # tiến trình chỉ probe một lần.
        u._reset_probe_cache()
        try:
            with mock.patch.object(u, "find_spec",
                                   wraps=importlib.util.find_spec) as spy:
                first = u.probe_backends()
                n_first = len(spy.call_args_list)
                second = u.probe_backends()
                self.assertEqual(first, second)
                self.assertEqual(len(spy.call_args_list), n_first)
        finally:
            u._reset_probe_cache()

    @pytest.mark.needs_fixture("unity/resources.assets", "unity/sample_lz4.unity3d")
    def test_native_never_needs_library(self):
        b = nat()
        self.assertIn("NATIVE", b.decisions[0])
        # giải nén block LZ4 bằng decoder thuần, không cần binding
        raw = b"pure python lz4 " * 100
        comp = lz4pure.compress_block_literals(raw)
        self.assertEqual(b.decompress(comp, len(raw), 2), raw)
        data = read(BUNDLE_LZ4)
        bundle = u.parse_bundle(data, b)
        self.assertEqual(bundle.compression_name, "lz4")
        self.assertEqual(bundle.get_entry(ENTRY1), read(RES))

    def test_library_raises_when_unitypy_missing(self):
        if u.probe_backends()["unitypy"]["status"] == "ok":
            self.skipTest("sandbox có UnityPy (không kỳ vọng)")
        with self.assertRaises(PB3Error) as cm:
            u.UnityBackend("LIBRARY")
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_ON_ISH")

    def test_auto_init_logs_unitypy_decision(self):
        # Khởi tạo AUTO phải nhanh và không import UnityPy (import thật trên
        # iSH từng treo suite > 149s); decisions luôn ghi rõ UnityPy có/thiếu.
        before = "UnityPy" in sys.modules
        b = u.UnityBackend("AUTO")
        self.assertEqual("UnityPy" in sys.modules, before)
        self.assertTrue(any("UnityPy" in d for d in b.decisions))
        self.assertEqual(b.decompress(b"abc", 3, 0), b"abc")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_unitypy_load_guard(self):
        b = nat()
        with self.assertRaises(PB3Error) as cm:
            b.unitypy_load(read(RES))
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_ON_ISH")

    def test_unknown_mode_raises(self):
        with self.assertRaises(PB3Error) as cm:
            u.UnityBackend("KHONG_CO")
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")


class TestPlanner(unittest.TestCase):
    @pytest.mark.needs_fixture("unity/nested.ipa")
    def test_plan_nested_ipa(self):
        plan = u.plan_bytes(read(NESTED_IPA), name="nested.ipa", backend=nat())
        self.assertEqual(plan.verdict.engine, "unity")
        kinds = [r.kind for r in plan.rows]
        self.assertIn("assets", kinds)
        self.assertIn("bundle", kinds)
        ta = [r for r in plan.rows if r.object_type == "TextAsset"]
        self.assertEqual({r.path_id for r in ta}, {1001, 1002, 2001})
        # TextAsset được gợi ý trước (ưu tiên)
        self.assertEqual(ta[0].guess, "text")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_plan_bundle(self):
        plan = u.plan_bytes(read(BUNDLE), name="sample.unity3d", backend=nat())
        self.assertEqual(len([r for r in plan.rows if r.kind == "assets"]), 4)

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_plan_assets_file(self):
        plan = u.plan_bytes(read(RES), name="resources.assets", backend=nat())
        self.assertEqual(plan.verdict.engine, "unity")
        self.assertEqual(len(plan.rows), 3)
        self.assertEqual(plan.rows[0].object_type, "TextAsset")

    def test_plan_unknown(self):
        plan = u.plan_bytes(b"hello world", name="plain.txt", backend=nat())
        self.assertEqual(plan.verdict.level, "CHƯA ĐỦ BẰNG CHỨNG")
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual(plan.rows[0].kind, "file")


def _req(**kw):
    base = dict(data=read(RES), kind="assets", path_id=1001,
                old_text="9 Kings", new_text="8 Kings")
    base.update(kw)
    return u.PatchRequest(**base)


class TestPatcher(unittest.TestCase):
    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_inplace_same_length(self):
        r = u.apply_patch(_req())
        self.assertEqual(r.write_mode_used, "PATCH_INPLACE")
        self.assertTrue(r.verified)
        self.assertEqual(len(r.output_bytes), len(read(RES)))
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_inplace_byte_diff_minimal(self):
        old = read(RES)
        r = u.apply_patch(_req())
        diffs = [i for i, (a, b) in enumerate(zip(old, r.output_bytes)) if a != b]
        self.assertEqual(diffs, [old.index(b"9 Kings")])  # đúng 1 byte đổi

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_inplace_shorter_nul_padded(self):
        r = u.apply_patch(_req(old_text="Xin chao 9 Kings!", new_text="Hi!"))
        self.assertEqual(r.write_mode_used, "PATCH_INPLACE")
        self.assertTrue(r.verified)
        self.assertEqual(len(r.output_bytes), len(read(RES)))
        self.assertTrue(any("đệm NUL" in w for w in r.report.warnings))
        sf = u.parse_serialized(r.output_bytes)
        script = sf.text_assets()[0][2]
        self.assertTrue(script.startswith("Hi!"))
        self.assertEqual(len(script), 17)  # giữ nguyên độ dài script

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_longer_escalates_to_rebuild_section(self):
        r = u.apply_patch(_req(old_text="Xin chao",
                               new_text="Xin chao cac ban choi game"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        self.assertTrue(any("REBUILD_SECTION" in w for w in r.report.warnings))
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.text_assets()[0][2], "Xin chao cac ban choi game 9 Kings!")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_longer_refused_without_escalate(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(old_text="Xin chao", new_text="Xin chao cac ban",
                               allow_escalate=False))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_longer_within_same_alignment_escalates(self):
        # #180 (C10): "Xin chao" -> "Xin chào" (+1 byte) nhưng kích thước
        # object đã căn bằng nhau (24 byte) -> leo lên REBUILD_SECTION thay
        # vì E_WRITE_DISABLED như trước.
        r = u.apply_patch(_req(old_text="Xin chao", new_text="Xin chào"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        self.assertTrue(any("REBUILD_SECTION" in w for w in r.report.warnings))
        sf = u.parse_serialized(r.output_bytes)
        self.assertEqual(sf.text_assets()[0][2], "Xin chào 9 Kings!")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_longer_within_same_alignment_refused_without_escalate(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(old_text="Xin chao", new_text="Xin chào",
                               allow_escalate=False))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_shorter_within_same_alignment_nul_padded(self):
        # #180 mirror (C10): -1 byte trong cùng khối căn (longtext 6450 ->
        # 6449, aligned 6456 = 6456) trước đây lỗi E_WRITE_DISABLED; nay đệm
        # NUL trong script, giữ nguyên PATCH_INPLACE.
        r = u.apply_patch(u.PatchRequest(
            data=read(RES), kind="assets", path_id=1002,
            old_text="thế giới!", new_text="thế giới"))
        self.assertEqual(r.write_mode_used, "PATCH_INPLACE")
        self.assertTrue(r.verified)
        self.assertEqual(len(r.output_bytes), len(read(RES)))
        sf = u.parse_serialized(r.output_bytes)
        script = [s for pid, _n, s in sf.text_assets() if pid == 1002][0]
        self.assertEqual(len(script.encode("utf-8")), 6450)  # giữ nguyên độ dài script
        self.assertTrue(script.startswith("Dòng dài"))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_explicit_rebuild_section(self):
        r = u.apply_patch(_req(old_text="9 Kings", new_text="8 Kings",
                               write_mode="REBUILD_SECTION"))
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        self.assertTrue(r.verified)
        self.assertEqual(u.parse_serialized(r.output_bytes).text_assets()[0][2],
                         "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_rebuild_all_refused_m2(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(write_mode="REBUILD_ALL", confirm=True))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")
        self.assertIn("M4", cm.exception.detail)

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_old_text_not_found(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(old_text="khong co chu nay"))
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_path_id_not_found(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(path_id=9999))
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_non_textasset_refused(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(_req(path_id=1003, old_text="x", new_text="y"))
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_unrelated_objects_keep_hash(self):
        before = u.parse_serialized(read(RES))
        for kw in ({}, {"write_mode": "REBUILD_SECTION"},
                   {"old_text": "Xin chao", "new_text": "Xin chao cac ban choi game"}):
            r = u.apply_patch(_req(**kw))
            after = u.parse_serialized(r.output_bytes)
            self.assertEqual(h(before.object_bytes(1002)), h(after.object_bytes(1002)))
            self.assertEqual(h(before.object_bytes(1003)), h(after.object_bytes(1003)))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_verify_failure_keeps_candidate_and_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(u, "_verify_assets",
                                   return_value=(False, ["giả lập verify lỗi"])):
                with self.assertRaises(PB3Error) as cm:
                    u.apply_patch(_req(temp_dir=Path(td)))
                self.assertEqual(cm.exception.code, "E_VERIFY_FAILED")
            kept = list(Path(td).glob("patch-failed.bin"))
            self.assertEqual(len(kept), 1)  # bản ứng viên giữ trong temp
            # bản ứng viên là output ĐÃ patch (chưa được chứng minh), không phải gốc
            sf = u.parse_serialized(kept[0].read_bytes())
            self.assertEqual(sf.text_assets()[0][2], "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/sample_lz4.unity3d")
    def test_patch_bundle_lz4_roundtrip(self):
        bundle = read(BUNDLE_LZ4)
        r = u.apply_patch(u.PatchRequest(
            data=bundle, kind="bundle", entry=ENTRY1, path_id=1002,
            old_text="xin chào thế giới! ", new_text="HELLO ",
            write_mode="REBUILD_SECTION"))
        self.assertTrue(r.verified)
        self.assertEqual(r.write_mode_used, "REBUILD_SECTION")
        parsed = u.parse_bundle(r.output_bytes, nat())
        self.assertEqual(parsed.compression_name, "lz4")  # giữ compression gốc
        sf = u.parse_serialized(parsed.get_entry(ENTRY1))
        self.assertTrue(sf.text_assets()[1][2].startswith("Dòng dài để thử chuỗi dài: HELLO "))
        # patch lần nữa trên output đã patch (round-trip)
        r2 = u.apply_patch(u.PatchRequest(
            data=r.output_bytes, kind="bundle", entry=ENTRY1, path_id=1001,
            old_text="9 Kings", new_text="8 Kings"))
        self.assertTrue(r2.verified)
        sf2 = u.parse_serialized(u.parse_bundle(r2.output_bytes, nat()).get_entry(ENTRY1))
        self.assertEqual(sf2.text_assets()[0][2], "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_patch_bundle_entry_missing(self):
        with self.assertRaises(PB3Error) as cm:
            u.apply_patch(u.PatchRequest(
                data=read(BUNDLE), kind="bundle", entry="khong-co",
                path_id=1001, old_text="9 Kings", new_text="8 Kings"))
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_patch_bundle_inplace_preserves_other_entry(self):
        bundle = read(BUNDLE)
        before = u.parse_bundle(bundle, nat())
        r = u.apply_patch(u.PatchRequest(
            data=bundle, kind="bundle", entry=ENTRY1, path_id=1001,
            old_text="9 Kings", new_text="8 Kings"))
        after = u.parse_bundle(r.output_bytes, nat())
        self.assertEqual(after.get_entry(ENTRY2), before.get_entry(ENTRY2))
        self.assertEqual(after.compression_name, "none")

    def test_support_matrix(self):
        m = u.support_matrix()
        by_format = {row["format"]: row for row in m}
        self.assertIn("UnityFS v6/v7/v8 (bundle)", by_format)
        self.assertEqual(by_format["UnityFS v6/v7/v8 (bundle)"]["maturity"], "M2")
        self.assertIn("SerializedFile v15-23 (.assets)", by_format)
        self.assertEqual(by_format["SerializedFile v15-23 (.assets)"]["maturity"], "M2")
        unsupported = [r for r in m if r["maturity"] == "M0"]
        self.assertTrue(any("LZHAM" in r["format"] for r in unsupported))


class TestPatchIO(TmpCase):
    def test_write_patch_output_versions(self):
        data = b"patched"
        p1 = u.write_patch_output(self.tmp, "game/sample.unity3d", data)
        self.assertEqual(p1.name, "sample.unity3d")
        self.assertEqual(p1.read_bytes(), data)
        p2 = u.write_patch_output(self.tmp, "game/sample.unity3d", data)
        self.assertEqual(p2.name, "sample.v1.unity3d")
        p3 = u.write_patch_output(self.tmp, "game/sample.unity3d", data)
        self.assertEqual(p3.name, "sample.v2.unity3d")

    def test_backup_and_rollback(self):
        src = self.tmp / "orig.assets"
        src.write_bytes(b"original")
        bak = u.backup_source_file(src, self.tmp)
        self.assertTrue(bak.exists())
        self.assertEqual(bak.read_bytes(), b"original")
        out = self.tmp / "out.assets"
        out.write_bytes(b"patched")
        self.assertTrue(u.rollback_output(out))  # xóa file output vừa ghi
        self.assertFalse(out.exists())
        self.assertEqual(src.read_bytes(), b"original")  # nguồn không đổi

    def test_default_output_rel(self):
        from services import unity_tool
        # bundle trong IPA: chỉ xuất bundle, không xuất cả IPA (6.6)
        self.assertEqual(unity_tool._default_output_rel(
            Path("game.ipa"), "bundle", "Payload/Game.app/Data/level.unity3d"),
            "game/level.unity3d")
        self.assertEqual(unity_tool._default_output_rel(
            Path("resources.assets"), "assets", "assets"),
            "resources/resources.assets")
        self.assertEqual(unity_tool._default_output_rel(
            Path("sample.unity3d"), "bundle", "bundle"),
            "sample/sample.unity3d")


class TestUnityRegistered(unittest.TestCase):
    def test_unity_in_commands_and_mod_m2(self):
        from ui import handlers
        self.assertIn("unity", handlers.COMMANDS)
        self.assertEqual(handlers.COMMANDS["unity"].module, "services.unity_tool")
        self.assertEqual(handlers.COMMANDS["mod"].maturity, "M2")


@slow_subprocess
class TestUnityCli(TmpCase):
    """CLI thật trên bản sao dự án (tầng slow)."""

    _shared_td = None
    _shared_proj = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3test-unity-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def run_menu(self, args, cwd=None):
        return subprocess.run([sys.executable, str(self._shared_proj / "menu.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(cwd or self.tmp), timeout=subprocess_timeout(60))

    def _fixture(self, name):
        return str(self._shared_proj / "tests" / "fixtures" / "unity" / name)

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_inspect_bundle(self):
        r = self.run_menu(["unity", "inspect", self._fixture("sample.unity3d")])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("UnityFS", out)
        self.assertIn(ENTRY1, out)
        self.assertIn("TextAsset", out)
        self.assertIn("greeting", out)

    @pytest.mark.needs_fixture("unity/nested.ipa")
    def test_inspect_nested_ipa(self):
        r = self.run_menu(["unity", "inspect", self._fixture("nested.ipa")])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("level.unity3d", out)
        self.assertIn("TextAsset", out)

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_inspect_v8_bundle(self):
        # UnityFS v8 + Unity 6 (05b): inspect ra plan, không còn E_UNSUPPORTED_FORMAT
        r = self.run_menu(["unity", "inspect", self._fixture("sample_v8.unity3d")])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("UnityFS v8", out)
        self.assertIn("6000.0.23f1", out)
        self.assertIn("TextAsset", out)
        self.assertIn("greeting", out)

    @pytest.mark.needs_fixture("unity/nested_v8.ipa")
    def test_inspect_v8_ipa(self):
        r = self.run_menu(["unity", "inspect", self._fixture("nested_v8.ipa")])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("level.unity3d", out)
        self.assertIn("TextAsset", out)

    @pytest.mark.needs_fixture("unity/sample_v8.unity3d")
    def test_patch_v8_bundle(self):
        src = self._fixture("sample_v8.unity3d")
        r = self.run_menu(["unity", "patch", src, ENTRY1, "--object", "4001",
                           "--old", "Unity 6", "--new", "Unity 7"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("KHÔNG ghi", r.stdout.decode("utf-8"))
        r = self.run_menu(["unity", "patch", src, ENTRY1, "--object", "4001",
                           "--old", "Unity 6", "--new", "Unity 7",
                           "--allow-write"])
        self.assertEqual(r.returncode, 0, r.stderr)
        written = list((self._shared_proj / "workspace" / "output").rglob(
            "sample_v8*.unity3d"))
        self.assertEqual(len(written), 1)
        bundle = u.parse_bundle(written[0].read_bytes(), nat())
        self.assertEqual(bundle.version, 8)
        self.assertEqual(bundle.unity_revision, "6000.0.23f1")
        sf = u.parse_serialized(bundle.get_entry(ENTRY1))
        self.assertEqual(sf.text_assets()[0][2], "Xin chao Unity 7!")
        self.assertEqual(read(BUNDLE_V8), Path(src).read_bytes())  # nguồn không đổi

    def test_inspect_plain_text_no_evidence(self):
        # inspect chỉ khám phá: file lạ không lỗi, chỉ báo CHƯA ĐỦ BẰNG CHỨNG
        txt = self.tmp / "plain.txt"
        txt.write_text("not a unity file")
        r = self.run_menu(["unity", "inspect", str(txt)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CHƯA ĐỦ BẰNG CHỨNG", r.stdout.decode("utf-8"))

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_read_text_asset(self):
        r = self.run_menu(["unity", "read", self._fixture("resources.assets"),
                           "--object", "1001"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Xin chao 9 Kings!", r.stdout.decode("utf-8"))

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_read_bundle_entry_out(self):
        dest = self.tmp / "level.unity3d"
        r = self.run_menu(["unity", "read", self._fixture("sample.unity3d"),
                           ENTRY1, "--out", str(dest)])
        self.assertEqual(r.returncode, 0, r.stderr)
        sf = u.parse_serialized(dest.read_bytes())
        self.assertEqual(sf.text_assets()[0][2], "Xin chao 9 Kings!")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_patch_dry_run_then_write(self):
        src = self._fixture("resources.assets")
        out_dir = self._shared_proj / "workspace" / "output"
        before = sorted(out_dir.rglob("*")) if out_dir.exists() else []
        r = self.run_menu(["unity", "patch", src, "--object", "1001",
                           "--old", "9 Kings", "--new", "8 Kings"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("KHÔNG ghi", out)
        after_dry = sorted(out_dir.rglob("*")) if out_dir.exists() else []
        self.assertEqual(after_dry, before)  # dry-run không tạo file nào
        r = self.run_menu(["unity", "patch", src, "--object", "1001",
                           "--old", "9 Kings", "--new", "8 Kings", "--allow-write"])
        self.assertEqual(r.returncode, 0, r.stderr)
        written = list((self._shared_proj / "workspace" / "output").rglob("*.assets"))
        self.assertEqual(len(written), 1)
        sf = u.parse_serialized(written[0].read_bytes())
        self.assertEqual(sf.text_assets()[0][2], "Xin chao 8 Kings!")
        self.assertEqual(read(RES), Path(src).read_bytes())  # nguồn không đổi

    @pytest.mark.needs_fixture("unity/nested.ipa")
    def test_patch_ipa_writes_bundle_only(self):
        r = self.run_menu(["unity", "patch", self._fixture("nested.ipa"),
                           "Payload/Game.app/Data/level.unity3d::" + ENTRY1,
                           "--object", "1001", "--old", "9 Kings", "--new", "8 Kings",
                           "--allow-write"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out_dir = self._shared_proj / "workspace" / "output"
        written = list(out_dir.rglob("*"))
        self.assertTrue(any(w.name.endswith(".unity3d") for w in written), written)
        self.assertFalse(any(w.name.endswith(".ipa") for w in written))
        bundle = u.parse_bundle(next(w for w in written if w.name.endswith(".unity3d")).read_bytes(), nat())
        sf = u.parse_serialized(bundle.get_entry(ENTRY1))
        self.assertEqual(sf.text_assets()[0][2], "Xin chao 8 Kings!")

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_mod_dry_run_and_write(self):
        src = self._fixture("sample.unity3d")
        # mod <file> --file <entry trong bundle> --object <id> ...
        r = self.run_menu(["mod", src, "--file", ENTRY1, "--object", "1001",
                           "--old", "9 Kings", "--new", "8 Kings"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Chưa ghi file", r.stdout.decode("utf-8"))
        r = self.run_menu(["mod", src, "--file", ENTRY1, "--object", "1001",
                           "--old", "9 Kings", "--new", "8 Kings", "--allow-write"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out_dir = self._shared_proj / "workspace" / "output"
        self.assertTrue(list(out_dir.rglob("*.unity3d")))


if __name__ == "__main__":
    unittest.main()
