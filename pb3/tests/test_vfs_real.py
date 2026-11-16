"""Hợp đồng VFS trên container thật (Claude 04): read/slice/stream/limits
của ZipVFS/NestedVFS phải khớp quy ước ở sdk/vfs.py (decisions #61).

So sánh trực tiếp với MemoryVFS cùng nội dung: slice/read cho kết quả giống
nhau; khác biệt duy nhất được phép là CRC (đọc toàn bộ ZIP kiểm CRC).
"""
import io
import unittest
import zipfile

from core.errors import PB3Error
from core.limits import Limits
from sdk.containers import NestedVFS, ZipVFS, open_vfs
from sdk.vfs import LimitedVFS, MemoryVFS
from tests.test_container import FIXTURE_IPA, make_zip, nested_zip, stored_data_offset


class TestVfsContractOnZip(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.files = {
            "a.txt": b"hello world",
            "dir/b.bin": bytes(range(200)),
            "empty.txt": b"",
        }
        specs = [(k, v) for k, v in self.files.items()]
        self.zbytes = make_zip(specs)
        self.mem = MemoryVFS(dict(self.files))
        self.zip = ZipVFS(self.zbytes)

    def tearDown(self):
        self.zip.close()
        super().tearDown()

    def test_read_prefix_full_match_memory(self):
        for path, data in self.files.items():
            for max_bytes in (None, 0, 1, 5, 64, 1000):
                self.assertEqual(self.zip.read(path, max_bytes),
                                 self.mem.read(path, max_bytes),
                                 (path, max_bytes))
            self.assertEqual(self.zip.read_entry(path), self.mem.read_entry(path))

    def test_slice_match_memory(self):
        for path, data in self.files.items():
            for offset, size in ((0, 0), (0, 5), (3, 10), (199, 50), (500, 10)):
                self.assertEqual(self.zip.slice(path, offset, size),
                                 self.mem.slice(path, offset, size), (path, offset, size))

    def test_stream_read_seek_match_memory(self):
        for path in self.files:
            with self.zip.open_stream(path) as zs, self.mem.open_stream(path) as ms:
                self.assertEqual(zs.read(), ms.read())
        with self.zip.open_stream("dir/b.bin") as zs:
            zs.seek(150)
            self.assertEqual(zs.read(10), bytes(range(150, 160)))
            self.assertEqual(zs.tell(), 160)

    def test_empty_entry(self):
        self.assertEqual(self.zip.read("empty.txt", None), b"")
        self.assertEqual(self.zip.slice("empty.txt", 0, 5), b"")
        self.assertEqual(self.zip.stat("empty.txt").size, 0)

    def test_unsafe_vpath_rejected_same_as_memory(self):
        # "::" là dấu tách container (NestedVFS xử lý), không phải tên entry:
        # ZipVFS đơn coi "a::b" là tên không có -> E_INPUT_NOT_FOUND.
        for bad in ("", "../x", "/abs", "a/../b", "a\\b", "a:b"):
            with self.assertRaises(PB3Error) as cm:
                self.zip.stat(bad)
            self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")

    def test_limits_via_limited_vfs(self):
        tight = Limits(max_entry_bytes=64, max_memory_read_bytes=64)
        limited = LimitedVFS(self.zip, tight)
        self.assertEqual(limited.read("a.txt", None), b"hello world")
        with self.assertRaises(PB3Error) as cm:
            limited.read("dir/b.bin", None)
        self.assertEqual(cm.exception.code, "E_TOO_LARGE")
        self.assertEqual(limited.read("dir/b.bin", 10), bytes(range(10)))
        with self.assertRaises(PB3Error) as cm:
            limited.slice("dir/b.bin", 0, 100)
        self.assertEqual(cm.exception.code, "E_TOO_LARGE")

    def test_crc_verified_only_on_full_read(self):
        raw = bytearray(make_zip([("s.txt", b"ABCDEFGH")], method=zipfile.ZIP_STORED))
        off = stored_data_offset(bytes(raw), "s.txt")
        raw[off] ^= 0xFF
        with ZipVFS(bytes(raw)) as vfs:
            self.assertFalse(vfs.stat("s.txt").crc_verified)   # stat không xác thực CRC
            with self.assertRaises(PB3Error) as cm:
                vfs.read("s.txt", None)                        # đọc toàn bộ: kiểm CRC
            self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
            self.assertEqual(len(vfs.slice("s.txt", 0, 4)), 4)  # slice: không kiểm CRC
            self.assertEqual(len(vfs.slice("s.txt", 0, 8)), 8)  # slice tới cuối cũng không kiểm


class TestNestedVfsContract(unittest.TestCase):
    def test_nested_read_slice_stream(self):
        data = nested_zip(2, leaf=b"0123456789" * 20)
        with NestedVFS(ZipVFS(data)) as nv:
            path = "inner1.zip::inner0.zip::leaf.txt"
            self.assertEqual(nv.read(path, None), b"0123456789" * 20)
            self.assertEqual(nv.read(path, 5), b"01234")
            self.assertEqual(nv.slice(path, 10, 5), b"01234")
            with nv.open_stream(path) as s:
                s.seek(199)
                self.assertEqual(s.read(1), b"9")
            self.assertEqual(nv.stat(path).size, 200)
            self.assertTrue(nv.exists(path))
            self.assertFalse(nv.exists("inner1.zip::nope.txt"))

    def test_nested_open_vfs_bytes(self):
        with open_vfs(FIXTURE_IPA.read_bytes(), source_name="game.ipa") as vfs:
            self.assertEqual(vfs.kind, "zip")
            self.assertEqual(vfs.read("Payload/Game.app/Data/config.txt", None), b"level=1\n")

    def test_nested_cache_reuses_child(self):
        data = nested_zip(1)
        with NestedVFS(ZipVFS(data)) as nv:
            nv.read("inner0.zip::leaf.txt", None)
            first = nv._cache
            self.assertEqual(len(first), 1)
            nv.read("inner0.zip::leaf.txt", None)
            self.assertEqual(len(nv._cache), 1)      # mở lần 2 dùng cache, không tăng
