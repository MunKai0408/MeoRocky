"""Test sdk.heaps — Heaps PAK (port từ IPA-LOCALIZATION pak_heads.js).

Builder _ent/_heaps_pak port từ tests/make_pakheads_ipas.py của app JS
(gốc: rootNamed, tên rỗng; dir flags 0x01 + count u32; file flags 0x00 +
pos/size u32 + checksum 4 byte; "DATA" sau cây). Fixture là dữ liệu giả
lập theo đúng bố cục đó — [FIXTURE], chưa đối chiếu pak thật.
"""
import ast
import os
import struct
import unittest

from sdk import heaps
from tests.helpers import imports_of, read_src

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAPS_PATH = os.path.join(ROOT, "sdk", "heaps.py")


def _ent(name, files=None, size=0, pos=0, csum=b"\xaa\xbb\xcc\xdd"):
    n = name.encode("utf-8")
    if files is not None:
        return (bytes([len(n)]) + n + b"\x01"
                + struct.pack("<I", len(files)) + b"".join(files))
    return (bytes([len(n)]) + n + b"\x00"
            + struct.pack("<II", pos, size) + csum)


def _tree():
    return _ent("", [_ent("lang", [_ent("en.xml", size=10, pos=0),
                                   _ent("fr.xml", size=10, pos=10)]),
                    _ent("data.cdb", size=20, pos=20)])


def _heaps_pak(ok_data=True, tree=None, version=0, hex_prefix=None,
               start_pad=b"", data_size=40, payload=None):
    tree = _tree() if tree is None else tree
    start = 12 + len(start_pad) + (len(hex_prefix) if hex_prefix else 0)
    hs = start + len(tree)
    body = (b"DATA" if ok_data else b"XXXX") + (payload if payload is not None
                                               else b"\x00" * 40)
    pak = (b"PAK" + bytes([version]) + struct.pack("<II", hs, data_size)
           + start_pad + (hex_prefix or b"") + tree + body)
    return pak


class TestHeapsOk(unittest.TestCase):
    def test_basic_ok_matches_js_builder(self):
        pak = _heaps_pak()
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["tree_complete"])
        self.assertEqual(r["files_total"], 3)
        self.assertEqual(r["dirs_total"], 2)
        self.assertEqual(r["layout"]["checksum_bytes"], 4)
        self.assertTrue(r["layout"]["root_named"])
        self.assertEqual(r["layout"]["tree_start"], 12)
        self.assertEqual(r["sample_paths"],
                         ["lang/en.xml", "lang/fr.xml", "data.cdb"])
        self.assertIn("data.cdb", r["text_like_paths"])
        self.assertEqual(r["ext_histogram"], {"xml": 2, "cdb": 1})
        self.assertEqual(r["version"], 0)
        self.assertEqual(r["header_size"], 12 + len(_tree()))

    def test_pak_files(self):
        files = heaps.pak_files(_heaps_pak(), len(_heaps_pak()))
        self.assertEqual(files, [{"path": "lang/en.xml", "pos": 0, "size": 10},
                                 {"path": "lang/fr.xml", "pos": 10, "size": 10},
                                 {"path": "data.cdb", "pos": 20, "size": 20}])

    def test_hex_prefix_dead_cells_layout(self):
        # Dead Cells thật: 64 ký tự hex ASCII giống nhau ở mọi pak rồi mới "PAK".
        prefix = b"ab12cd34" * 8
        pak = _heaps_pak(hex_prefix=prefix, version=1)
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        self.assertIsNotNone(r.get("hex_prefix"))
        self.assertEqual(r["hex_prefix"]["length"], 64)
        self.assertEqual(r["sample_paths"],
                         ["lang/en.xml", "lang/fr.xml", "data.cdb"])

    def test_start16_four_junk_bytes(self):
        # [HYPOTHESIS] 4 byte lạ ngay sau header 12 byte ở pak Dead Cells.
        pak = _heaps_pak(start_pad=b"f\x35\x66\x65", version=1)
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["layout"]["tree_start"], 16)

    def test_csum0_layout(self):
        tree = _ent("", [_ent("a.txt", size=5, pos=0, csum=b"")])
        pak = _heaps_pak(tree=tree)
        r = heaps.parse_heaps_pak(pak, len(pak))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["layout"]["checksum_bytes"], 0)
        self.assertEqual(r["sample_paths"], ["a.txt"])


class TestHeapsFailClosed(unittest.TestCase):
    def test_no_data_marker_is_unverified(self):
        r = heaps.parse_heaps_pak(_heaps_pak(ok_data=False), 100)
        self.assertEqual(r["status"], "unverified")
        self.assertFalse(r["tree_complete"])

    def test_garbage_is_not_heaps_pak(self):
        for head in (b"hello world, not a pak", b"UnityFS1234567890",
                     b"GDPC" + b"\x00" * 40, b"", b"PA"):
            r = heaps.parse_heaps_pak(head, len(head))
            self.assertEqual(r["status"], "not_heaps_pak", head[:8])
            self.assertFalse(heaps.is_heaps_pak_head(head))

    def test_bad_name_rejected(self):
        tree = _ent("", [_ent("a/b.txt", size=1, pos=0)])
        r = heaps.parse_heaps_pak(_heaps_pak(tree=tree), 200)
        self.assertEqual(r["status"], "unverified")
        tree2 = _ent("", [_ent("a\x00b.txt", size=1, pos=0)])
        r2 = heaps.parse_heaps_pak(_heaps_pak(tree=tree2), 200)
        self.assertEqual(r2["status"], "unverified")

    def test_truncated_head_is_partial(self):
        pak = _heaps_pak()
        # Chỉ đưa 20 byte đầu nhưng khai pak_size lớn -> cây cụt.
        r = heaps.parse_heaps_pak(pak[:20], len(pak))
        self.assertEqual(r["status"], "partial")

    def test_deep_tree_rejected(self):
        node = _ent("leaf.txt", size=1, pos=0)
        for i in range(35):
            node = _ent("d%02d" % i, [node])
        tree = _ent("", [node])
        r = heaps.parse_heaps_pak(_heaps_pak(tree=tree), 4096)
        self.assertEqual(r["status"], "unverified")

    def test_header_size_out_of_range(self):
        pak = bytearray(_heaps_pak())
        struct.pack_into("<I", pak, 4, 10 ** 9)  # header_size vô lý
        r = heaps.parse_heaps_pak(bytes(pak), len(pak))
        self.assertEqual(r["status"], "unverified")
        self.assertFalse(r["tree_complete"])

    def test_version_too_big(self):
        pak = _heaps_pak(version=99)
        self.assertFalse(heaps.is_heaps_pak_head(pak))


class TestHeapsHead(unittest.TestCase):
    def test_is_heaps_pak_head_both_layouts(self):
        self.assertTrue(heaps.is_heaps_pak_head(_heaps_pak(version=1)))
        prefix = b"ab12cd34" * 8
        self.assertTrue(heaps.is_heaps_pak_head(_heaps_pak(hex_prefix=prefix,
                                                           version=1)))
        # Head 64 byte không thấy được "PAK" ở offset 64 (cần >= 76 byte).
        self.assertFalse(heaps.is_heaps_pak_head(
            _heaps_pak(hex_prefix=prefix, version=1)[:64]))

    def test_probe_header(self):
        pak = _heaps_pak()
        probe = heaps.probe_header(pak, 12 + len(_tree()))
        self.assertIn("entropy_bits_per_byte", probe)
        self.assertIn("interpretation", probe)


class TestHeapsStdlibPy39(unittest.TestCase):
    def test_stdlib_only_and_py39_syntax(self):
        ast.parse(read_src(HEAPS_PATH), feature_version=(3, 9))
        allowed = {"__future__", "math", "re", "struct", "typing"}
        for name, _line in imports_of(HEAPS_PATH):
            self.assertIn(name.split(".")[0], allowed, name)


if __name__ == "__main__":
    unittest.main()
