"""Test container/VFS thật (Claude 04): ZIP/IPA, TAR/GZ/XZ, lồng, writer, plugin zip, CLI.

Hầu hết test chạy trong RAM/file nhỏ (tầng fast). Test CLI mở tiến trình con
(tầng slow, tự xếp) dùng chung một bản sao dự án cho cả lớp.
"""
import gzip
import io
import lzma
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.errors import PB3Error
from core.limits import Limits
from sdk.containers import (UNREAL_PAK_MAGIC, NestedVFS, SingleStreamVFS, TarVFS, ZipVFS,
                            detect_container_kind, open_vfs, rebuild_zip,
                            support_matrix)
from sdk.vfs import MemoryVFS
from tests.fixtures.godot import builder as godot_builder
from tests.fixtures.unreal.builder import SAMPLE_TEXTS, sample_pak_bytes
from tests.helpers import (ROOT, TmpCase, copy_project, register_tmp_dir, slow_subprocess,
                           subprocess_timeout, unregister_tmp_dir)
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

FIXTURE_IPA = ROOT / "tests" / "fixtures" / "zip" / "sample.ipa"


def make_ios_zip(entries):
    """ZIP kiểu "Nén" trong Files iOS: tên entry ghi bằng byte UTF-8 nhưng
    cờ UTF-8 bit 11 KHÔNG đặt. `zipfile` chuẩn khi đó giải tên theo cp437
    (mojibake). entries: [(tên str, bytes)]."""
    import zlib
    local, central = b"", b""
    for name, data in entries:
        nb = name.encode("utf-8")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        off = len(local)
        local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0000, 0, 0,
                             0x21, crc, len(data), len(data), len(nb), 0) + nb + data
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0000,
                               0, 0, 0x21, crc, len(data), len(data), len(nb),
                               0, 0, 0, 0, 0, off) + nb
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                       len(central), len(local), 0)
    return local + central + eocd


def make_zip(specs, *, method=zipfile.ZIP_DEFLATED):
    """specs: [(tên, bytes)] hoặc [(tên, bytes, {thuộc tính ZipInfo})]."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for item in specs:
            name, data = item[0], item[1]
            kw = item[2] if len(item) > 2 else {}
            info = zipfile.ZipInfo(name, kw.pop("date_time", (2020, 1, 2, 3, 4, 5)))
            info.compress_type = kw.pop("compress_type", method)
            info.external_attr = kw.pop("external_attr", 0o644 << 16)
            info.create_system = kw.pop("create_system", 3)
            info.extra = kw.pop("extra", b"")
            info.comment = kw.pop("comment", b"")
            zf.writestr(info, data)
    return buf.getvalue()


def stored_data_offset(zbytes, name):
    """Vị trí bắt đầu dữ liệu của entry STORED (để thử CRC)."""
    with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
        info = zf.getinfo(name)
        off = info.header_offset
    sig, _ver, _flag, _method, _t, _d, _crc, _c, _u, nlen, elen = struct.unpack_from(
        "<IHHHHHIIIHH", zbytes, off)
    assert sig == 0x04034B50
    return off + 30 + nlen + elen


def make_encrypted_zip():
    """ZIP tối thiểu có entry mã hóa (cờ bit 0) — zipfile chuẩn không ghi được."""
    name, data = b"secret.txt", b"abcd"
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0001, 0, 0, 0x21, 0, len(data),
                        len(data), len(name), 0) + name + data
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0001, 0, 0, 0x21, 0,
                          len(data), len(data), len(name), 0, 0, 0, 0, 0, 0) + name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + eocd


def make_tar(members, *, mode="w"):
    """members: [(tên, bytes)] hoặc [(tên, bytes, type, linkname)]."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for item in members:
            name, data = item[0], item[1]
            typ = item[2] if len(item) > 2 else tarfile.REGTYPE
            link = item[3] if len(item) > 3 else ""
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1577923200
            info.mode = 0o644
            info.type = typ
            info.linkname = link
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def nested_zip(levels, leaf=b"leaf-data"):
    """ZIP lồng `levels` tầng: tầng trong cùng chứa leaf.txt."""
    data = make_zip([("leaf.txt", leaf)])
    for i in range(levels):
        data = make_zip([("inner%d.zip" % i, data)])
    return data


class TestDetect(unittest.TestCase):
    def test_zip_tar_gzip_xz_7z_unknown(self):
        self.assertEqual(detect_container_kind(b"PK\x03\x04" + b"\x00" * 10), "zip")
        self.assertEqual(detect_container_kind(b"PK\x05\x06" + b"\x00" * 10), "zip")
        self.assertEqual(detect_container_kind(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 10), "7z")
        self.assertEqual(detect_container_kind(b"\x1f\x8b\x08\x00" + b"\x00" * 10), "gzip")
        self.assertEqual(detect_container_kind(b"\xfd7zXZ\x00" + b"\x00" * 10), "xz")
        tar = make_tar([("a.txt", b"hi")])
        self.assertEqual(detect_container_kind(tar), "tar")
        self.assertEqual(detect_container_kind(b"hello world"), "unknown")
        self.assertEqual(detect_container_kind(b""), "unknown")
        with self.assertRaises(PB3Error) as cm:
            detect_container_kind("not-bytes")
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_open_vfs_unknown_and_7z(self):
        with self.assertRaises(PB3Error) as cm:
            open_vfs(b"hello world, not an archive")
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        with self.assertRaises(PB3Error) as cm:
            open_vfs(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 20)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        with self.assertRaises(PB3Error) as cm:
            open_vfs(12345)
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_open_vfs_godot_pck_and_unreal_pak(self):
        # Claude 08 (tích hợp): backend Godot/Unreal nay đã có thật — thay
        # test khóa cũ test_open_vfs_godot_pak_without_backend_is_clean_error
        # (kỳ vọng lỗi "backend chưa có") bằng test mở PCK/PAK thật qua
        # dispatch của open_vfs. Header hỏng vẫn lỗi sạch E_UNSUPPORTED_FORMAT.
        from sdk.godot import PckVFS
        from sdk.unreal import PakVFS
        pck = godot_builder.build_pck_v2([
            ("res://hello.txt", "Xin chào".encode("utf-8")),
        ])
        vfs = open_vfs(pck)
        try:
            self.assertIsInstance(vfs, PckVFS)
            self.assertEqual(vfs.read("hello.txt", None), "Xin chào".encode("utf-8"))
        finally:
            vfs.close()
        pak = sample_pak_bytes(version=8)
        vfs = open_vfs(pak)
        try:
            self.assertIsInstance(vfs, PakVFS)
            hello = "TestGame/Content/Text/hello.txt"
            self.assertEqual(vfs.read(hello, None), SAMPLE_TEXTS[hello])
        finally:
            vfs.close()
        with self.assertRaises(PB3Error) as cm:
            open_vfs(b"GDPC" + b"\x00" * 100)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        pak = b"\x00" * 100 + UNREAL_PAK_MAGIC + b"\x00" * 100
        with self.assertRaises(PB3Error) as cm:
            open_vfs(pak)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_support_matrix(self):
        rows = {r["format"]: r for r in support_matrix()}
        self.assertEqual(rows["ZIP / IPA"]["write"], "có (rebuild_zip)")
        self.assertEqual(rows["TAR"]["read"], "có")
        self.assertEqual(rows["TAR"]["write"], "không")
        self.assertEqual(rows["7z"]["read"], "không")
        self.assertIn("zipfile", rows["ZIP / IPA"]["backend"])
        self.assertNotIn("7z", rows["7z"]["backend"].replace("7z chưa", ""))


class TestZipVFS(unittest.TestCase):
    def test_open_list_stat_read(self):
        data = make_zip([("a/b.txt", b"hello"), ("c.txt", b"world!")])
        with ZipVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), ["a/b.txt", "c.txt"])
            self.assertEqual(list(vfs.iter_entries("a/")), ["a/b.txt"])
            st = vfs.stat("a/b.txt")
            self.assertEqual((st.path, st.size, st.crc_verified), ("a/b.txt", 5, False))
            self.assertEqual(vfs.read("a/b.txt", None), b"hello")
            self.assertTrue(vfs.exists("c.txt"))
            self.assertFalse(vfs.exists("nope.txt"))
            self.assertEqual(vfs.read_entry("c.txt"), b"world!")
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(data).stat("nope.txt")
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_read_prefix_vs_full(self):
        data = make_zip([("big.bin", b"0123456789" * 10)])   # 100 byte
        lim = Limits(max_entry_bytes=128, max_memory_read_bytes=1024)
        with ZipVFS(data, limits=lim) as vfs:
            self.assertEqual(vfs.read("big.bin", 6), b"012345")   # tiền tố
            self.assertEqual(len(vfs.read("big.bin", None)), 100)  # toàn bộ
            self.assertEqual(len(vfs.read("big.bin", 128)), 100)   # >= max_entry -> toàn bộ
            self.assertEqual(len(vfs.read("big.bin", 1000)), 100)
        tiny = Limits(max_entry_bytes=10, max_memory_read_bytes=1024)
        with ZipVFS(data, limits=tiny) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read("big.bin", None)
            self.assertEqual(cm.exception.code, "E_TOO_LARGE")
            with self.assertRaises(PB3Error) as cm:
                vfs.read("big.bin", 64)   # >= max_entry -> đòi toàn bộ -> quá giới hạn
            self.assertEqual(cm.exception.code, "E_TOO_LARGE")
            self.assertEqual(vfs.read("big.bin", 4), b"0123")      # tiền tố vẫn được

    def test_slice_and_stream(self):
        data = make_zip([("d.bin", bytes(range(256)))])
        with ZipVFS(data) as vfs:
            self.assertEqual(vfs.slice("d.bin", 10, 5), bytes(range(10, 15)))
            self.assertEqual(vfs.slice("d.bin", 250, 100), bytes(range(250, 256)))
            self.assertEqual(vfs.slice("d.bin", 300, 10), b"")
            self.assertEqual(vfs.slice("d.bin", 0, 0), b"")
            with vfs.open_stream("d.bin") as s:
                self.assertTrue(s.readable())
                self.assertFalse(s.writable())
                self.assertEqual(s.read(3), b"\x00\x01\x02")
                s.seek(100)
                self.assertEqual(s.tell(), 100)
                self.assertEqual(s.read(2), b"de")
                with self.assertRaises(PB3Error) as cm:
                    s.write(b"x")
                self.assertEqual(cm.exception.code, "E_PLUGIN_FORBIDDEN")
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(data).slice("d.bin", -1, 5)
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_crc_full_read_fails_slice_does_not(self):
        raw = bytearray(make_zip([("s.txt", b"ABCDEFGH")], method=zipfile.ZIP_STORED))
        off = stored_data_offset(bytes(raw), "s.txt")
        raw[off] ^= 0xFF                       # hỏng 1 byte dữ liệu (CRC sai)
        with ZipVFS(bytes(raw)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read("s.txt", None)
            self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
            self.assertEqual(vfs.stat("s.txt").size, 8)
            got = vfs.slice("s.txt", 0, 4)      # slice không kiểm CRC (giải nén thô)
            self.assertEqual(len(got), 4)
            self.assertNotEqual(got, b"ABCD")
            full = vfs.slice("s.txt", 0, 8)     # slice tới cuối cũng không kiểm CRC
            self.assertEqual(len(full), 8)
            self.assertNotEqual(full, b"ABCDEFGH")

    def test_bad_and_empty_zip(self):
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(b"PK\x03\x04" + b"\x00" * 30)
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(b"not a zip at all")
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        with ZipVFS(buf.getvalue()) as vfs:
            self.assertEqual(list(vfs.iter_entries()), [])
            self.assertEqual(vfs.inventory().entries, [])

    def test_zip_name_utf8_without_flag_ios_mojibake(self):
        # Hồi quy bug clone-fix (2026-11-16): ZIP do "Nén" trong Files iOS
        # tạo — tên bắt đầu bằng U+200E (byte E2 80 8E), không đặt cờ UTF-8
        # bit 11. zipfile thô giải thành "ΓÇÄ ..."; PB3 phải hiển thị đúng.
        name = "\u200e 9 Kings_v1.0.5-AppAssassin.ipa"
        mac = "__MACOSX/._" + name
        data = make_ios_zip([(name, b"PKfake"), (mac, b"x")])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self.assertEqual(zf.namelist()[0],
                             "ΓÇÄ 9 Kings_v1.0.5-AppAssassin.ipa")
        with ZipVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), [name, mac])
            self.assertEqual(vfs.read(name, None), b"PKfake")
            self.assertEqual(vfs.read(mac, None), b"x")
            self.assertNotIn("ΓÇÄ", vfs.inventory().entries[0].name)

    def test_zip_name_utf8_with_flag_and_ascii_unchanged(self):
        data = make_zip([("café.txt", b"a"), ("plain.txt", b"b")])
        with ZipVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), ["café.txt", "plain.txt"])

    def test_compress_manifest_fixes_ios_mojibake_name(self):
        from sdk.compress import _inventory_zip
        name = "\u200e 9 Kings_v1.0.5-AppAssassin.ipa"
        data = make_ios_zip([(name, b"PKfake")])
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            recs = _inventory_zip(zf, Limits(), "test.zip")
        self.assertEqual([r.name for r in recs], [name])

    def test_zip_bomb_limits(self):
        data = make_zip([("a.txt", b"A" * 500), ("b.txt", b"B" * 500), ("c.txt", b"C" * 500)])
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(data, limits=Limits(max_total_uncompressed_bytes=1000))
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")
        with self.assertRaises(PB3Error) as cm:
            ZipVFS(data, limits=Limits(max_entries=2))
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")
        ratio = make_zip([("z.bin", b"\x00" * 10000)])
        with ZipVFS(ratio, limits=Limits(max_ratio=10)) as vfs:
            self.assertEqual(len(vfs.inventory().warnings), 1)
            self.assertIn("tỉ lệ nén", vfs.inventory().warnings[0])
            self.assertEqual(vfs.read("z.bin", None), b"\x00" * 10000)  # cảnh báo, không chặn

    def test_traversal_symlink_duplicate_blocked(self):
        link_attr = (0o120777 << 16)
        data = make_zip([
            ("ok.txt", b"ok"),
            ("../evil.txt", b"evil"),
            ("/abs.txt", b"abs"),
            ("a\\b.txt", b"backslash"),
            ("link.txt", b"target", {"external_attr": link_attr, "create_system": 3}),
            ("dup.txt", b"first"),
            ("dup.txt", b"second"),
        ])
        with ZipVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), ["ok.txt", "dup.txt"])
            self.assertEqual(vfs.read("dup.txt", None), b"first")   # trùng tên: lần đầu thắng
            rejected = dict(vfs.inventory().rejected)
            self.assertIn("../evil.txt", rejected)
            self.assertIn("/abs.txt", rejected)
            self.assertIn("a\\b.txt", rejected)
            self.assertIn("link.txt", rejected)
            self.assertIn("dup.txt", rejected)
            for bad in ("../evil.txt", "/abs.txt", "link.txt"):
                with self.assertRaises(PB3Error) as cm:
                    vfs.read(bad, None)
                self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")

    def test_encrypted_entry_blocked(self):
        with ZipVFS(make_encrypted_zip()) as vfs:
            rejected = dict(vfs.inventory().rejected)
            self.assertEqual(rejected.get("secret.txt"), "entry mã hóa (không giải mã)")
            self.assertEqual(list(vfs.iter_entries()), [])
            with self.assertRaises(PB3Error) as cm:
                vfs.read("secret.txt", None)
            self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_metadata_preserved_for_rebuild(self):
        data = make_zip([
            ("m.txt", b"meta", {"date_time": (2021, 5, 6, 7, 8, 8), "extra": b"\x01\x02",
                                "comment": b"hi", "external_attr": 0o640 << 16,
                                "compress_type": zipfile.ZIP_STORED}),
        ])
        with ZipVFS(data) as vfs:
            (entry,) = [e for e in vfs.inventory().entries if not e.is_dir]
            self.assertEqual(entry.date_time, (2021, 5, 6, 7, 8, 8))  # ZIP: giây chẵn 2
            self.assertEqual(entry.compress_type, "stored")
            self.assertEqual(entry.extra["comment"], "hi")
            self.assertEqual(entry.extra["external_attr"], 0o640 << 16)

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_sample_ipa_fixture(self):
        with open_vfs(FIXTURE_IPA) as vfs:
            self.assertEqual(vfs.kind, "zip")
            self.assertEqual(list(vfs.iter_entries()), [
                "Payload/Game.app/Info.plist",
                "Payload/Game.app/Data/level.unity3d",
                "Payload/Game.app/Data/config.txt"])
            self.assertEqual(vfs.read("Payload/Game.app/Data/config.txt", None), b"level=1\n")


class TestTarVFS(unittest.TestCase):
    def test_tar_plain(self):
        data = make_tar([("dir/", b""), ("dir/a.txt", b"hello tar"), ("b.bin", bytes(range(50)))])
        with TarVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), ["dir/a.txt", "b.bin"])
            self.assertEqual(vfs.read("dir/a.txt", None), b"hello tar")
            self.assertEqual(vfs.slice("b.bin", 10, 5), bytes(range(10, 15)))
            with vfs.open_stream("b.bin") as s:
                self.assertEqual(s.read(4), b"\x00\x01\x02\x03")
            self.assertEqual(vfs.stat("b.bin").size, 50)
            self.assertFalse(vfs.stat("b.bin").crc_verified)

    def test_tar_gz_and_xz(self):
        for mode, kind in (("w:gz", "gzip"), ("w:xz", "xz")):
            data = make_tar([("x.txt", b"compressed tar")], mode=mode)
            with open_vfs(data) as vfs:
                self.assertEqual(vfs.kind, "tar")
                self.assertEqual(vfs.read("x.txt", None), b"compressed tar")
        # tar v7 không có magic "ustar": dùng USTAR rồi xóa magic + tính lại
        # checksum của header (đổi magic làm hỏng checksum cũ), vẫn mở được
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            info = tarfile.TarInfo("v7.txt")
            info.size = 3
            tf.addfile(info, io.BytesIO(b"v7!"))
        raw = bytearray(buf.getvalue())
        raw[257:262] = b"\x00" * 5
        hdr = bytearray(raw[:512])
        hdr[148:156] = b"        "
        hdr[148:156] = b"%06o\0 " % sum(hdr)
        raw[:512] = hdr
        with open_vfs(bytes(raw)) as vfs:
            self.assertEqual(vfs.read("v7.txt", None), b"v7!")

    def test_tar_symlink_and_traversal_blocked(self):
        data = make_tar([
            ("ok.txt", b"ok"),
            ("link.txt", b"", tarfile.SYMTYPE, "ok.txt"),
            ("hard.txt", b"", tarfile.LNKTYPE, "ok.txt"),
            ("../evil.txt", b"evil"),
            ("/abs.txt", b"abs"),
        ])
        with TarVFS(data) as vfs:
            self.assertEqual(list(vfs.iter_entries()), ["ok.txt"])
            rejected = dict(vfs.inventory().rejected)
            self.assertIn("link.txt", rejected)
            self.assertIn("hard.txt", rejected)
            self.assertIn("../evil.txt", rejected)
            self.assertIn("/abs.txt", rejected)
            with self.assertRaises(PB3Error) as cm:
                vfs.read("link.txt", None)
            self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")

    def test_tar_bad_data(self):
        with self.assertRaises(PB3Error) as cm:
            TarVFS(b"this is not a tar file at all, just text........")
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")


class TestSingleStreamVFS(unittest.TestCase):
    def test_gzip_bare(self):
        raw = b"hello gzip world " * 100
        with open_vfs(gzip.compress(raw, mtime=0)) as vfs:
            self.assertEqual(vfs.kind, "gzip")
            self.assertEqual(list(vfs.iter_entries()), ["data"])
            self.assertEqual(vfs.read("data", None), raw)
            self.assertEqual(vfs.slice("data", 6, 4), b"gzip")
            self.assertEqual(vfs.stat("data").size, len(raw))
            with vfs.open_stream("data") as s:
                self.assertEqual(s.read(5), b"hello")

    def test_xz_bare_and_bad(self):
        raw = b"hello xz " * 50
        with open_vfs(lzma.compress(raw)) as vfs:
            self.assertEqual(vfs.kind, "xz")
            self.assertEqual(vfs.read("data", None), raw)
        # Luồng đơn mở lazy: lỗi chỉ lộ khi thật sự đọc (stat/read).
        with self.assertRaises(PB3Error) as cm:
            open_vfs(b"\xfd7zXZ\x00" + b"\x00" * 20).stat("data")
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_stream_size_limit(self):
        raw = b"A" * 5000
        with self.assertRaises(PB3Error) as cm:
            SingleStreamVFS(gzip.compress(raw, mtime=0), kind="gzip",
                            limits=Limits(max_total_uncompressed_bytes=1000)).stat("data")
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")


class TestNestedVFS(unittest.TestCase):
    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_nested_read_and_iter(self):
        with NestedVFS(open_vfs(FIXTURE_IPA)) as nv:
            path = "Payload/Game.app/Data/level.unity3d::resources.assets"
            data = nv.read(path, None)
            self.assertTrue(data.startswith(b"UNITYFS-fake-resources-"))
            self.assertEqual(nv.stat(path).size, 223)
            self.assertEqual(nv.slice(path, 0, 7), b"UNITYFS")
            self.assertEqual(list(nv.iter_entries(
                "Payload/Game.app/Data/level.unity3d::")),
                ["Payload/Game.app/Data/level.unity3d::resources.assets",
                 "Payload/Game.app/Data/level.unity3d::sharedassets0.assets"])
            self.assertEqual(list(nv.iter_entries(
                "Payload/Game.app/Data/level.unity3d::res")),
                ["Payload/Game.app/Data/level.unity3d::resources.assets"])
            self.assertEqual(list(nv.iter_entries()), [
                "Payload/Game.app/Info.plist",
                "Payload/Game.app/Data/level.unity3d",
                "Payload/Game.app/Data/config.txt"])

    def test_nested_three_levels_lazy(self):
        data = nested_zip(3)
        with NestedVFS(ZipVFS(data)) as nv:
            self.assertEqual(nv.read("inner2.zip::inner1.zip::inner0.zip::leaf.txt", None),
                             b"leaf-data")
            self.assertEqual(len(nv._cache), 3)          # mỗi tầng mở khi cần, có cache

    def test_nested_depth_limit(self):
        data = nested_zip(5)
        with NestedVFS(ZipVFS(data), limits=Limits(max_depth=2)) as nv:
            with self.assertRaises(PB3Error) as cm:
                nv.read("inner4.zip::inner3.zip::inner2.zip::leaf.txt", None)
            self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    def test_nested_loop_detected(self):
        # Ghi đè open_vfs: mỗi container con đều trả về cùng VFS gồm 1 entry
        # 'x' chứa cùng bytes -> hash lặp lại ở tầng 2 -> E_ARCHIVE_BOMB.
        # (Cần 2 đoạn container: "a.zip::x::..." mới kích hoạt kiểm tra vòng lặp.)
        inner = MemoryVFS({"a.zip": b"Z1"})
        canned = MemoryVFS({"x": b"Z1"})
        with mock.patch("sdk.containers.open_vfs", return_value=canned):
            with NestedVFS(inner) as nv:
                with self.assertRaises(PB3Error) as cm:
                    nv.read("a.zip::x::leaf.txt", None)
                self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_nested_missing_and_not_archive(self):
        with NestedVFS(open_vfs(FIXTURE_IPA)) as nv:
            with self.assertRaises(PB3Error) as cm:
                nv.read("Payload/Game.app/Data/config.txt::nope.txt", None)
            self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
            with self.assertRaises(PB3Error) as cm:
                nv.read("nope.zip::x.txt", None)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
            with self.assertRaises(PB3Error) as cm:
                nv.read("a::b::c", None)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")


class TestRebuildZip(TmpCase):
    def setUp(self):
        super().setUp()
        self.src = self.tmp / "src.zip"
        self.dst = self.tmp / "out.zip"
        self.src.write_bytes(make_zip([
            ("a.txt", b"AAAA", {"compress_type": zipfile.ZIP_STORED}),
            ("b.txt", b"BBBBBBBB"),
            ("c.txt", b"CCCC"),
        ]))

    def test_roundtrip_replace_preserves_order_method(self):
        rep = rebuild_zip(self.src, self.dst, {"b.txt": b"NEW!"})
        self.assertEqual(rep.replaced, ["b.txt"])
        self.assertEqual(rep.copied, 2)
        self.assertEqual(rep.method_changes, [])
        self.assertEqual(rep.entries, 3)
        self.assertGreater(rep.bytes_written, 0)
        with zipfile.ZipFile(self.dst) as zf:
            self.assertEqual(zf.namelist(), ["a.txt", "b.txt", "c.txt"])
            self.assertEqual(zf.read("a.txt"), b"AAAA")
            self.assertEqual(zf.read("b.txt"), b"NEW!")
            self.assertEqual(zf.read("c.txt"), b"CCCC")
            self.assertEqual(zf.getinfo("a.txt").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(zf.getinfo("b.txt").compress_type, zipfile.ZIP_DEFLATED)

    def test_rebuild_fixes_ios_mojibake_name(self):
        # Hồi quy bug clone-fix: rebuild ZIP kiểu iOS (tên UTF-8, không cờ
        # bit 11) phải ghi ra tên đúng, và replace bằng tên đã sửa phải khớp
        # (khóa repl là đường dẫn VFS — tên đã sửa).
        name = "\u200e 9 Kings_v1.0.5-AppAssassin.ipa"
        src = self.tmp / "ios.zip"
        src.write_bytes(make_ios_zip([(name, b"PKfake")]))
        rep = rebuild_zip(src, self.dst)
        self.assertEqual(rep.copied, 1)
        with zipfile.ZipFile(self.dst) as zf:
            self.assertEqual(zf.namelist(), [name])
            self.assertEqual(zf.read(name), b"PKfake")
        rep2 = rebuild_zip(src, self.tmp / "ios2.zip", {name: b"NEW"})
        self.assertEqual(rep2.replaced, [name])
        with zipfile.ZipFile(self.tmp / "ios2.zip") as zf:
            self.assertEqual(zf.read(name), b"NEW")

    def test_method_change_reported(self):
        rep = rebuild_zip(self.src, self.dst, {"a.txt": b"AAAA"},
                          method_overrides={"a.txt": "deflated"})
        self.assertEqual(rep.method_changes, [("a.txt", "stored", "deflated")])
        with zipfile.ZipFile(self.dst) as zf:
            self.assertEqual(zf.getinfo("a.txt").compress_type, zipfile.ZIP_DEFLATED)
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(self.src, self.dst, method_overrides={"nope.txt": "deflated"})
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(self.src, self.dst, method_overrides={"a.txt": "nope"})
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_add_new_entry_and_metadata_kept(self):
        rep = rebuild_zip(self.src, self.dst, {"new.txt": b"fresh"})
        self.assertEqual(rep.added, ["new.txt"])
        with zipfile.ZipFile(self.dst) as zf:
            self.assertEqual(zf.namelist(), ["a.txt", "b.txt", "c.txt", "new.txt"])
            self.assertEqual(zf.read("new.txt"), b"fresh")
        src = make_zip([("m.txt", b"meta", {"date_time": (2021, 5, 6, 7, 8, 8),
                                            "extra": b"\x01\x02", "comment": b"hi",
                                            "external_attr": 0o640 << 16,
                                            "compress_type": zipfile.ZIP_STORED})])
        p = self.tmp / "m.zip"
        p.write_bytes(src)
        rebuild_zip(p, self.tmp / "m2.zip", {"m.txt": b"meta2"})
        with zipfile.ZipFile(self.tmp / "m2.zip") as zf:
            info = zf.getinfo("m.txt")
            self.assertEqual(info.date_time, (2021, 5, 6, 7, 8, 8))
            self.assertEqual(info.extra, b"\x01\x02")
            self.assertEqual(info.comment, b"hi")
            self.assertEqual(info.external_attr, 0o640 << 16)
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)

    def test_rollback_dest_kept_on_failure(self):
        self.dst.write_bytes(b"SENTINEL-OLD")
        raw = bytearray(make_zip([("s.txt", b"ABCDEFGH")], method=zipfile.ZIP_STORED))
        off = stored_data_offset(bytes(raw), "s.txt")
        raw[off] ^= 0xFF
        bad = self.tmp / "bad.zip"
        bad.write_bytes(bytes(raw))
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(bad, self.dst)
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
        self.assertEqual(self.dst.read_bytes(), b"SENTINEL-OLD")   # đích cũ nguyên vẹn
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(self.tmp / "nope.zip", self.dst)
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
        self.assertEqual(self.dst.read_bytes(), b"SENTINEL-OLD")

    def test_encrypted_entry_refuses(self):
        p = self.tmp / "enc.zip"
        p.write_bytes(make_encrypted_zip())
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(p, self.dst)
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")
        rep = rebuild_zip(p, self.dst, {"secret.txt": b"plain now"})
        self.assertEqual(rep.replaced, ["secret.txt"])
        with zipfile.ZipFile(self.dst) as zf:
            self.assertEqual(zf.read("secret.txt"), b"plain now")

    def test_bad_write_mode(self):
        with self.assertRaises(PB3Error) as cm:
            rebuild_zip(self.src, self.dst, write_mode="NOPE")
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")


class TestZipPlugin(unittest.TestCase):
    def _ctx(self, files):
        from sdk.api import PluginContext
        return PluginContext(MemoryVFS(files), Limits(), None, None, None, True, None, None, "zip")

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_detect_candidates_probe_open(self):
        from plugins.zip.plugin import ZipPlugin
        ipa = FIXTURE_IPA.read_bytes()
        ctx = self._ctx({"game.ipa": ipa, "note.txt": b"no"})
        plugin = ZipPlugin()
        ev = plugin.detect(ctx)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "magic")
        cands = plugin.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["game.ipa"])
        self.assertEqual(cands[0].adapter_id, "zip")
        probe = plugin.probe(ctx, cands[0])
        self.assertTrue(probe["ok"])
        self.assertEqual(probe["entries"], 3)
        with plugin.open_container(ctx, "game.ipa") as vfs:
            self.assertEqual(vfs.read("Payload/Game.app/Data/config.txt", None), b"level=1\n")
        self.assertTrue(plugin.selftest(ctx)["ok"])

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_conformance_pass_on_fixture(self):
        from plugins.zip.plugin import ZipPlugin
        from registry.conformance import run_conformance
        rep = run_conformance(ZipPlugin, fixture=str(ROOT / "tests" / "fixtures" / "zip"))
        self.assertTrue(rep.ok, rep.format(48))
        self.assertEqual(rep.get("container").status, "PASS")
        self.assertEqual(rep.get("manifest").status, "PASS")
        self.assertEqual(rep.fixture_files, 1)

    def test_plugin_list_shows_zip(self):
        from registry.registry import build_registry
        reg = build_registry(str(ROOT), None)
        rows = {r["id"]: r for r in reg.rows()}
        self.assertIn("zip", rows)
        self.assertEqual(rows["zip"]["state"], "ok")
        self.assertEqual(rows["zip"]["kind"], "container")
        self.assertEqual(rows["zip"]["maturity"], "M2")


@slow_subprocess
@pytest.mark.needs_fixture("zip/sample.ipa")
class TestContainerCli(TmpCase):
    """CLI thật trên bản sao dự án (tầng slow)."""

    _shared_td = None
    _shared_proj = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3test-cont-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))
        (cls._shared_proj / "game.ipa").write_bytes(FIXTURE_IPA.read_bytes())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def run_menu(self, args, cwd=None):
        return subprocess.run([sys.executable, str(self._shared_proj / "menu.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(cwd or self.tmp), timeout=subprocess_timeout(60))

    def test_container_list(self):
        r = self.run_menu(["container", "list", str(self._shared_proj / "game.ipa")])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("container: ", out)
        self.assertIn("Payload/Game.app/Data/level.unity3d", out)
        self.assertIn("3 entry", out)

    def test_container_read_nested_and_out(self):
        dest = self.tmp / "res.assets"
        r = self.run_menu(["container", "read", str(self._shared_proj / "game.ipa"),
                           "Payload/Game.app/Data/level.unity3d::resources.assets",
                           "--out", str(dest)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(dest.read_bytes().startswith(b"UNITYFS-fake-resources-"))
        r2 = self.run_menu(["container", "read", str(self._shared_proj / "game.ipa"),
                            "Payload/Game.app/Data/config.txt", "--out", str(dest)])
        self.assertEqual(r2.returncode, 1, r2.stderr)   # E_OUTPUT_EXISTS -> EXIT_USER
        self.assertIn(b"E_OUTPUT_EXISTS", r2.stderr)

    def test_container_errors(self):
        txt = self.tmp / "plain.txt"
        txt.write_text("not an archive")
        r = self.run_menu(["container", "list", str(txt)])
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"E_UNSUPPORTED_FORMAT", r.stderr)
        r = self.run_menu(["container", "read", str(self._shared_proj / "game.ipa"),
                           "Payload/Game.app/Data/config.txt"])
        self.assertEqual(r.returncode, 1, r.stderr)     # thiếu --out
        r = self.run_menu(["container", "list", str(self.tmp / "nope.ipa")])
        self.assertEqual(r.returncode, 1, r.stderr)     # E_INPUT_NOT_FOUND

    def test_plugin_test_zip_via_cli(self):
        r = self.run_menu(["plugin", "test", "zip"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("KẾT QUẢ: PASS", out)
        self.assertIn("[PASS] container", out)
