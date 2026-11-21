"""Test F3: phan loai bundle ma hoa + revision 0.0.0 + danh gia Zstd.

Phat hien chinh [DA DO THAT 22/11]: EK2 v0.0.18 co 100/100 bundle flag
0x243, revision "0.0.0" — KHONG he ma hoa: blocks-info giai LZ4HC binh
thuong, parse toan bo duoc 197 entry, payload 1.125.039.389 B, 105 file
SF trong payload tat ca v22. Truoc fix, `_uses_new_flags("0.0.0")`
tra False (vi (0, 0, 0) < 2020) nen bit 0x200 bi doc la co ma hoa cu
va ca 100 bundle bi tu choi E_ENCRYPTED.

- `_uses_new_flags("0.0.0")` -> True (revision bi xoa, khong phai ban
  Unity that); revision cu that ("2019.4.40f1") van False, bit 0x200
  van la ma hoa.
- `classify_bundle_encryption`: none / padding_flag / xor_single_byte
  (magic hoac blocks-info) / header_obscured / encrypted (ma hoa that:
  blocks-info khong giai duoc plain + XOR 1 byte) / unparseable /
  not_a_bundle. Chi chan doan, khong giai ma, khong ghi gi.
- Zstd: `build_bundle(compression="zstd")` van tu choi ro rang
  E_UNSUPPORTED_FORMAT (khong co encoder — fzstd vendored chi giai
  nen); danh gia F3: 0/1904 bundle that dung ma 5 nen chua cai ghi Zstd.

Fixture TU DUNG IN-MEMORY (build_bundle + build_serialized_file);
test IPA that (@slow) skip neu thieu file trong ~/workspace/test-ipas/.
So lieu [FIXTURE] cho phan tong hop; kiem chung tren game that nam o
AUTOPILOT + bao cao F3.
"""
from __future__ import annotations

import struct
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_serialized_file, text_asset_data, typetree_blob)

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.helpers import pytest  # noqa: E402 - marker slow

LIM = Limits()
TEXT = (49, None)
ENTRY1 = "CAB-test-entry"
EK2_IPA = Path.home() / "workspace" / "test-ipas" / "ek2-v0.0.18.ipa"


def _sf_text():
    blob = typetree_blob(
        "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
        version=22)
    return build_serialized_file(
        version=22, unity_version="6000.0.23f1", enable_type_tree=True,
        objects=[(4001, 0, text_asset_data("greeting", "Xin chao!"))],
        types=[TEXT], typetree_blobs={0: blob})


def _ek2_style_bundle():
    """Bundle giong het EK2 that: v8, player 5.x.x, revision 0.0.0,
    LZ4HC, flag 0x243 (0x40 combined + 0x200 padding + 0x03 LZ4HC)."""
    return u.build_bundle(
        [(ENTRY1, 4, _sf_text())], version=8, unity_version="5.x.x",
        unity_revision="0.0.0", compression="lz4hc",
        extra_data_flags=0x200, pad_block_data=True, backend=u.UnityBackend("NATIVE", LIM),
        limits=LIM)


def _header_fields(data):
    """(version, player, engine, total, comp_info, uncomp_info, flags, hdr_end)."""
    r = u._Reader(data, ">", where="bundle")
    r.read(8)
    version = r.u32()
    player = r.string_to_null()
    engine = r.string_to_null()
    total, comp, uncomp, flags = r.unpack("QIII")
    return version, player, engine, total, comp, uncomp, flags, r.pos


class TestZeroRevisionFlags(unittest.TestCase):
    def test_uses_new_flags_zero_revision(self):
        # Revision bi xoa "0.0.0" -> co moi (nhu khong parse duoc).
        self.assertTrue(u._uses_new_flags("0.0.0"))
        self.assertTrue(u._uses_new_flags(""))
        self.assertTrue(u._uses_new_flags("5.x.x"))
        # Revision cu that van co cu.
        self.assertFalse(u._uses_new_flags("2019.4.40f1"))
        self.assertFalse(u._uses_new_flags("2020.3.33f1"))
        self.assertTrue(u._uses_new_flags("6000.0.23f1"))

    def test_ek2_style_bundle_parses(self):
        # Hoi quy: truoc fix, bundle nay bi E_ENCRYPTED vi revision 0.0.0.
        built = _ek2_style_bundle()
        self.assertEqual(_header_fields(built)[6], 0x243)
        bundle = u.parse_bundle(built, u.UnityBackend("NATIVE", LIM))
        self.assertTrue(bundle.uses_padding)
        self.assertEqual(bundle.compression_name, "lz4hc")
        self.assertEqual(bundle.get_entry(ENTRY1), _sf_text())

    def test_ek2_style_bundle_classifies_padding_flag(self):
        res = u.classify_bundle_encryption(_ek2_style_bundle())
        self.assertEqual(res["kind"], "padding_flag")
        self.assertEqual(res["flags"], 0x243)
        self.assertEqual(res["unity_revision"], "0.0.0")
        self.assertIsNone(res["xor_key"])

    def test_old_revision_encryption_still_refused(self):
        # Cung bit 0x200 nhung revision cu that -> ma hoa, van tu choi.
        built = u.build_bundle(
            [(ENTRY1, 4, _sf_text())], version=7,
            unity_version="2019.4.40f1",
            extra_data_flags=0x200, backend=u.UnityBackend("NATIVE", LIM),
            limits=LIM)
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(built, u.UnityBackend("NATIVE", LIM))
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")
        res = u.classify_bundle_encryption(built)
        self.assertEqual(res["kind"], "encrypted")
        self.assertIn("0x200", res["detail"])


class TestClassifyEncryption(unittest.TestCase):
    def test_none_plain_bundle(self):
        built = u.build_bundle(
            [(ENTRY1, 4, _sf_text())], version=8, unity_version="5.x.x",
            unity_revision="6000.0.23f1", compression="lz4hc",
            backend=u.UnityBackend("NATIVE", LIM), limits=LIM)
        res = u.classify_bundle_encryption(built)
        self.assertEqual(res["kind"], "none")

    def test_xor_single_byte_magic(self):
        built = bytearray(_ek2_style_bundle())
        key = 0x5A
        for i in range(7):
            built[i] ^= key
        res = u.classify_bundle_encryption(bytes(built))
        self.assertEqual(res["kind"], "xor_single_byte")
        self.assertEqual(res["xor_key"], key)
        # Parse van tu choi ro E_ENCRYPTED (kem key), khong phai
        # E_UNSUPPORTED_FORMAT chung chung nhu truoc.
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(bytes(built), u.UnityBackend("NATIVE", LIM))
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_header_obscured(self):
        res = u.classify_bundle_encryption(b"\x00" * 32)
        self.assertEqual(res["kind"], "header_obscured")
        self.assertIsNone(res["xor_key"])

    def test_not_a_bundle_short(self):
        res = u.classify_bundle_encryption(b"Unity")
        self.assertEqual(res["kind"], "not_a_bundle")

    def test_xor_single_byte_blocks_info(self):
        # Bundle revision cu + co 0x200 + blocks-info XOR 1 byte:
        # phan loai tim ra key; chua cai giai ma nen parse van tu choi.
        built = u.build_bundle(
            [(ENTRY1, 4, _sf_text())], version=7,
            unity_version="2019.4.40f1", compression="lz4hc",
            extra_data_flags=0x200, backend=u.UnityBackend("NATIVE", LIM),
            limits=LIM)
        version, _player, engine, _total, comp, _uncomp, _flags, hdr_end = (
            _header_fields(built))
        start = u._align_up(hdr_end, 16)
        key = 0xA5
        data = bytearray(built)
        for i in range(start, start + comp):
            data[i] ^= key
        res = u.classify_bundle_encryption(bytes(data))
        self.assertEqual(res["kind"], "xor_single_byte")
        self.assertEqual(res["xor_key"], key)
        with self.assertRaises(PB3Error) as cm:
            u.parse_bundle(bytes(data), u.UnityBackend("NATIVE", LIM))
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_encrypted_multibyte_xor_blocks_info(self):
        # Blocks-info XOR bang key nhieu byte: khong giai duoc plain,
        # khong giai duoc XOR 1 byte -> ma hoa that.
        built = u.build_bundle(
            [(ENTRY1, 4, _sf_text())], version=7,
            unity_version="2019.4.40f1", compression="lz4hc",
            extra_data_flags=0x200, backend=u.UnityBackend("NATIVE", LIM),
            limits=LIM)
        _version, _player, _engine, _total, comp, _uncomp, _flags, hdr_end = (
            _header_fields(built))
        start = u._align_up(hdr_end, 16)
        multi = b"\x12\x34\x56\x78"
        data = bytearray(built)
        for i in range(start, start + comp):
            data[i] ^= multi[(i - start) % len(multi)]
        res = u.classify_bundle_encryption(bytes(data))
        self.assertEqual(res["kind"], "encrypted")
        self.assertIsNone(res["xor_key"])
        self.assertIn("ma hoa that", res["detail"])


class TestZstdWriteDecision(unittest.TestCase):
    def test_build_bundle_zstd_still_refused(self):
        # F3: chua cai ghi Zstd (khong co encoder; fzstd vendored chi
        # giai nen) — tu choi ro, ben goi dung block khong nen.
        with self.assertRaises(PB3Error) as cm:
            u.build_bundle([(ENTRY1, 4, _sf_text())], version=7,
                           compression="zstd",
                           backend=u.UnityBackend("NATIVE", LIM), limits=LIM)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_support_matrix_mentions_ek2_and_zstd(self):
        rows = u.support_matrix()
        text = " ".join(r["format"] + " " + r["read"] + " " + r["write"]
                        for r in rows)
        self.assertIn("EK2", text)
        self.assertIn("padding", text)
        self.assertIn("Zstd", text)
        self.assertIn("fzstd", text)


class TestRealEk2Bundles(unittest.TestCase):
    """[DA DO THAT] tren IPA EK2 that — skip khi thieu file."""

    @pytest.mark.slow
    def test_real_ek2_all_bundles_parse(self):
        if not EK2_IPA.is_file():
            self.skipTest("thieu IPA that: %s" % EK2_IPA)
        zf = zipfile.ZipFile(str(EK2_IPA))
        names = []
        for n in zf.namelist():
            if zf.getinfo(n).file_size == 0:
                continue
            with zf.open(n) as f:
                head = f.read(7)
            if head == b"UnityFS":
                names.append(n)
        self.assertEqual(len(names), 100)
        entries_total = 0
        sf_vers = {}
        for name in names:
            data = zf.read(name)
            bundle = u.parse_bundle(data, where=name)
            self.assertEqual(bundle.data_flags, 0x243)
            self.assertEqual(bundle.unity_revision, "0.0.0")
            entries_total += len(bundle.entries)
            for e in bundle.entries:
                if e.name.endswith(".assets") or (e.flags & 0x04):
                    raw = bundle.get_entry(e.name)
                    if len(raw) >= 12:
                        ver = struct.unpack(">I", raw[8:12])[0]
                        sf_vers[ver] = sf_vers.get(ver, 0) + 1
        zf.close()
        # So da do o script tham do F3 (tmp/f3_ek2_fullparse.py).
        self.assertEqual(entries_total, 197)
        self.assertEqual(sf_vers, {22: 105})

    @pytest.mark.slow
    def test_real_ek2_bundle_classifies_padding_flag(self):
        if not EK2_IPA.is_file():
            self.skipTest("thieu IPA that: %s" % EK2_IPA)
        zf = zipfile.ZipFile(str(EK2_IPA))
        data = zf.read("Payload/ipastore.app/Data/Raw/ios/avatars")
        zf.close()
        res = u.classify_bundle_encryption(data)
        self.assertEqual(res["kind"], "padding_flag")
        self.assertEqual(res["flags"], 0x243)


if __name__ == "__main__":
    unittest.main()
