"""Test Unreal PAK (Claude 08, nhóm Unreal): PakVFS, open_vfs, plugin container.

Fixture dựng trong RAM bằng tests/fixtures/unreal/builder.py (không đọc
file nhị phân lạ). Test CLI mở tiến trình con (tầng slow, tự xếp) dùng
chung một bản sao dự án cho cả lớp.
"""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.errors import PB3Error
from core.limits import Limits
from plugins.unreal_pak.plugin import UnrealPakPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.containers import NestedVFS, ZipVFS, open_vfs
from sdk.unreal import PakVFS, build_pak
from sdk.vfs import MemoryVFS
from tests.fixtures.unreal.builder import (
    SAMPLE_TEXTS, sample_locres_bytes, sample_pak_bytes, sample_pak_v11_bytes)
from tests.helpers import (TmpCase, copy_project, register_tmp_dir,
                           subprocess_timeout, unregister_tmp_dir)

HELLO = "TestGame/Content/Text/hello.txt"
MENU = "TestGame/Content/Text/menu.txt"
LOCRES = "TestGame/Content/Text/vi.locres"
BIG = "TestGame/Content/Data/big.bin"
SECRET = "TestGame/Content/Data/secret.bin"
OLD = "TestGame/Content/Data/old.bin"
EVIL = "TestGame/Content/../evil.txt"


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "unreal_pak")


def _patch_first_entry_offset(data: bytes, new_offset: int) -> bytes:
    """Đổi offset của entry đầu trong index (để test phạm vi dữ liệu):
    footer V8B 221 byte -> index_offset ở footer+25; rồi FString mount
    point, u32 số entry, FString đường dẫn, rồi tới offset u64."""
    out = bytearray(data)
    (index_offset,) = struct.unpack_from("<q", data, len(data) - 221 + 25)
    pos = index_offset
    (n,) = struct.unpack_from("<i", data, pos)
    pos += 4 + (n if n > 0 else -n * 2)
    pos += 4  # số entry
    (n,) = struct.unpack_from("<i", data, pos)
    pos += 4 + (n if n > 0 else -n * 2)
    out[pos:pos + 8] = struct.pack("<Q", new_offset)
    return bytes(out)


class TestPakVFS(TmpCase):
    def test_open_v8_list_read_stat(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            self.assertEqual(vfs.pak_version, 8)
            self.assertEqual(vfs.footer_size, 221)
            self.assertEqual(vfs.mount_point, "TestGame/Content/")
            self.assertEqual(list(vfs.iter_entries()),
                             [HELLO, MENU, LOCRES, "thoat.txt"])
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
            self.assertEqual(vfs.stat(HELLO).size, len(SAMPLE_TEXTS[HELLO]))
            self.assertFalse(vfs.stat(HELLO).crc_verified)
            self.assertEqual(list(vfs.iter_entries("TestGame/Content/Text/")),
                             [HELLO, MENU, LOCRES])
            self.assertEqual(vfs.read(MENU, 9), SAMPLE_TEXTS[MENU][:9])
            self.assertEqual(vfs.slice(HELLO, 0, 9), "Xin chào".encode("utf-8"))
            self.assertEqual(vfs.slice(HELLO, 10, 1000),
                             SAMPLE_TEXTS[HELLO][10:])
            self.assertEqual(vfs.slice(HELLO, 10000, 10), b"")
            inv = vfs.inventory()
            self.assertEqual(inv.kind, "unreal_pak")
            self.assertEqual(len(inv.entries), 4)
            self.assertEqual(inv.entries[0].name, HELLO)
            self.assertEqual(inv.entries[0].compress_type, "stored")
            self.assertEqual(inv.entries[0].extra["pak_version"], 8)
            self.assertEqual(inv.warnings, [])

    def test_open_v11_encoded_index(self):
        with PakVFS(sample_pak_v11_bytes()) as vfs:
            self.assertEqual(vfs.pak_version, 11)
            self.assertEqual(vfs.pak_version_name, "V11 (Fnv64BugFix, UE 4.26-5.3)")
            self.assertEqual(list(vfs.iter_entries()),
                             [HELLO, MENU, LOCRES, "thoat.txt"])
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
            self.assertEqual(vfs.read(LOCRES, None), sample_locres_bytes(3))
            self.assertEqual(len(vfs.inventory().rejected), 4)

    def test_open_from_path(self):
        path = self.tmp / "game.pak"
        path.write_bytes(sample_pak_bytes(version=8))
        with PakVFS(str(path)) as vfs:
            self.assertEqual(vfs.source_name, "game.pak")
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])

    def test_open_stream_seek(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with vfs.open_stream(MENU) as st:
                self.assertTrue(st.readable())
                self.assertFalse(st.writable())
                self.assertEqual(st.read(5), "Chơi".encode("utf-8"))
                self.assertEqual(st.tell(), 5)
                self.assertEqual(st.seek(0), 0)
                self.assertEqual(st.read(), SAMPLE_TEXTS[MENU])
                with self.assertRaises(PB3Error) as cm:
                    st.write(b"x")
                self.assertEqual(cm.exception.code, "E_PLUGIN_FORBIDDEN")

    def test_missing_entry(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read("khong/co.txt", None)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
            with self.assertRaises(PB3Error) as cm:
                vfs.stat("TestGame/Content")  # PAK không có entry thư mục
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_normalized_dotdot_prefix_is_readable(self):
        # "../thoat.txt" trong index -> chuẩn hóa thành "thoat.txt".
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            self.assertEqual(vfs.read("thoat.txt", None),
                             "Tên có tiền tố mount point, chuẩn hóa thành thoat.txt".encode("utf-8"))
            self.assertEqual(vfs.inventory().entries[3].extra.get("orig_name"),
                             "../thoat.txt")

    def test_nested_zip_contains_pak(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("game.pak", sample_pak_bytes(version=8))
        with NestedVFS(ZipVFS(buf.getvalue())) as nv:
            self.assertEqual(nv.read("game.pak::" + HELLO, None), SAMPLE_TEXTS[HELLO])
            self.assertEqual(list(nv.iter_entries("game.pak::")),
                             ["game.pak::" + HELLO, "game.pak::" + MENU,
                              "game.pak::" + LOCRES, "game.pak::thoat.txt"])


class TestPakRejected(TmpCase):
    def test_rejected_inventory(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            inv = vfs.inventory()
            self.assertEqual(inv.rejected, [
                (BIG, "nén bằng Zlib (chỉ đọc entry không nén)"),
                (SECRET, "entry mã hóa (cần key AES, không giải mã)"),
                (HELLO, "trùng tên (chỉ dùng lần đầu)"),
                (EVIL, "tên chứa '.' hoặc '..'"),
                (OLD, "entry đã xóa (cờ deleted)"),
            ])

    def test_read_compressed_is_unsupported(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read(BIG, None)
            self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
            self.assertIn("Zlib", cm.exception.detail)

    def test_read_encrypted_is_encrypted(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read(SECRET, None)
            self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_read_deleted_is_not_found(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read(OLD, None)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_read_bad_name_is_unsafe(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read(EVIL, None)
            self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")

    def test_duplicate_keeps_first(self):
        with PakVFS(sample_pak_bytes(version=8)) as vfs:
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])

    def test_v11_rejected_without_deleted(self):
        # V10+ không có cờ deleted: mẫu V11 có 4 rejected (không có old.bin).
        with PakVFS(sample_pak_v11_bytes()) as vfs:
            names = [n for (n, _r) in vfs.inventory().rejected]
            self.assertEqual(names, [BIG, SECRET, HELLO, EVIL])


class TestPakFooterErrors(unittest.TestCase):
    def test_garbage_is_bad_archive(self):
        with self.assertRaises(PB3Error) as cm:
            PakVFS(b"khong phai pak" * 10)
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_truncated_is_bad_archive(self):
        with self.assertRaises(PB3Error) as cm:
            PakVFS(sample_pak_bytes(version=8)[:100])
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_bad_magic_is_bad_archive(self):
        data = bytearray(sample_pak_bytes(version=8))
        data[-221 + 17] ^= 0xFF  # magic ở footer offset 17
        with self.assertRaises(PB3Error) as cm:
            PakVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_bad_version_is_bad_archive(self):
        data = bytearray(sample_pak_bytes(version=8))
        data[-221 + 21] = 99  # version i32 ở footer offset 21
        with self.assertRaises(PB3Error) as cm:
            PakVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_bad_index_range_is_bad_archive(self):
        data = bytearray(sample_pak_bytes(version=8))
        data[-221 + 25:-221 + 33] = struct.pack("<q", 10 ** 9)
        with self.assertRaises(PB3Error) as cm:
            PakVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_entry_offset_out_of_file_is_bad_archive(self):
        data = _patch_first_entry_offset(sample_pak_bytes(version=8), 10 ** 9)
        with self.assertRaises(PB3Error) as cm:
            PakVFS(data)
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_encrypted_index_is_encrypted(self):
        data = build_pak([("a.txt", b"x", {})], version=8, encrypted_index=True)
        with self.assertRaises(PB3Error) as cm:
            PakVFS(data)
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_index_hash_mismatch_is_warning_only(self):
        data = bytearray(sample_pak_bytes(version=8))
        data[-221 + 41] ^= 0xFF  # 1 byte trong index_hash của footer
        with PakVFS(bytes(data)) as vfs:
            self.assertIn("hash SHA-1 của index không khớp footer",
                          vfs.inventory().warnings[0])
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])


class TestPakVersions(unittest.TestCase):
    def test_v3_v7_v8a_v9_parse(self):
        cases = [(3, 44, "V3 (CompressionEncryption, UE 4.3-4.15)"),
                 (7, 61, "V7 (EncryptionKeyGuid, UE 4.22)"),
                 (9, 222, "V9 (FrozenIndex, UE 4.25)")]
        for version, footer_size, name in cases:
            with self.subTest(version=version):
                with PakVFS(sample_pak_bytes(version=version)) as vfs:
                    self.assertEqual(vfs.pak_version, version)
                    self.assertEqual(vfs.footer_size, footer_size)
                    self.assertEqual(vfs.pak_version_name, name)
                    self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
                    self.assertTrue(any("chưa có fixture kiểm chứng" in w
                                        for w in vfs.inventory().warnings))
        with PakVFS(sample_pak_bytes(version=8, v8a=True)) as vfs:
            self.assertEqual(vfs.pak_version, 8)
            self.assertEqual(vfs.footer_size, 189)
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
            self.assertEqual(vfs.inventory().warnings, [])

    def test_builder_rejects_bad_versions(self):
        with self.assertRaises(ValueError):
            build_pak([("a.txt", b"x", {})], version=99)
        with self.assertRaises(ValueError):
            build_pak([("a.txt", b"x", {})], version=11, v8a=True)
        with self.assertRaises(ValueError):
            build_pak([("a.txt", b"x", {"deleted": True})], version=11)

    def test_builders_do_not_need_urandom(self):
        # Khóa decisions #143: fixture xác định, không cần os.urandom (iSH).
        import os as _os
        real = _os.urandom

        def _boom(n):
            raise AssertionError("os.urandom không được gọi")

        _os.urandom = _boom
        try:
            sample_pak_bytes(version=8)
            sample_pak_v11_bytes()
            sample_locres_bytes(3)
            sample_locres_bytes(0)
        finally:
            _os.urandom = real


class TestPakLimits(TmpCase):
    def test_too_many_entries_is_archive_bomb(self):
        data = sample_pak_bytes(version=8)
        with self.assertRaises(PB3Error) as cm:
            PakVFS(data, limits=Limits(max_entries=2))
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    def test_total_too_big_is_archive_bomb(self):
        data = sample_pak_bytes(version=8)
        with self.assertRaises(PB3Error) as cm:
            PakVFS(data, limits=Limits(max_total_uncompressed_bytes=100))
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    def test_entry_too_big_to_read(self):
        with PakVFS(sample_pak_bytes(version=8),
                    limits=Limits(max_entry_bytes=10)) as vfs:
            with self.assertRaises(PB3Error) as cm:
                vfs.read(HELLO, None)
            self.assertEqual(cm.exception.code, "E_TOO_LARGE")
            self.assertEqual(vfs.read(HELLO, 5), SAMPLE_TEXTS[HELLO][:5])


class TestPakOpenVfs(unittest.TestCase):
    def test_open_vfs_dispatches_pak(self):
        vfs = open_vfs(sample_pak_bytes(version=8))
        try:
            self.assertIsInstance(vfs, PakVFS)
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
        finally:
            vfs.close()
        vfs = open_vfs(sample_pak_v11_bytes())
        try:
            self.assertIsInstance(vfs, PakVFS)
            self.assertEqual(vfs.pak_version, 11)
        finally:
            vfs.close()

    def test_open_vfs_bad_footer_falls_through(self):
        # Magic footer có trong đuôi nhưng footer hỏng: open_vfs thử TAR/ZIP
        # rồi báo không nhận diện được (không phải E_BAD_ARCHIVE cứng).
        pak = b"\x00" * 100 + b"\xe1\x12\x6f\x5a" + b"\x00" * 100
        with self.assertRaises(PB3Error) as cm:
            open_vfs(pak)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")


class TestUnrealPakPlugin(TmpCase):
    def _plug(self):
        return UnrealPakPlugin()

    def test_detect_and_candidates(self):
        plug = self._plug()
        ctx = _ctx({"game.pak": sample_pak_bytes(version=8),
                    "b.txt": b"hello"}, self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["game.pak"])
        self.assertEqual(cands[0].adapter_id, "unreal_pak")
        self.assertEqual(cands[0].score, 0.5)
        ev = plug.detect(ctx)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "extension")

    def test_probe_ok_and_bad(self):
        from sdk.types import Candidate
        plug = self._plug()
        ctx = _ctx({"game.pak": sample_pak_bytes(version=8),
                    "xau.pak": b"khong phai pak" * 10},
                   self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("game.pak", plug.id, 0.5, []))
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["kind"], "unreal_pak")
        self.assertEqual(p["entries"], 4)
        self.assertEqual(p["rejected"], 5)
        self.assertEqual(p["pak_version"], "V8 (FNameBasedCompression, UE 4.22-4.25)")
        self.assertEqual(p["mount_point"], "TestGame/Content/")
        bad = plug.probe(ctx, Candidate("xau.pak", plug.id, 0.5, []))
        self.assertFalse(bad["ok"])
        # probe đi qua open_vfs: footer hỏng rơi qua TAR/ZIP rồi báo
        # E_UNSUPPORTED_FORMAT (không phải E_BAD_ARCHIVE cứng của PakVFS).
        self.assertIn("E_UNSUPPORTED_FORMAT", bad["reason"])

    def test_open_container(self):
        plug = self._plug()
        ctx = _ctx({"game.pak": sample_pak_v11_bytes()},
                   self.tmp).for_plugin(plug.manifest)
        vfs = plug.open_container(ctx, "game.pak")
        try:
            self.assertIsInstance(vfs, PakVFS)
            self.assertEqual(vfs.pak_version, 11)
            self.assertEqual(vfs.read(HELLO, None), SAMPLE_TEXTS[HELLO])
        finally:
            vfs.close()

    def test_selftest_ok(self):
        plug = self._plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        res = plug.selftest(ctx)
        self.assertTrue(res["ok"], res)

    def test_conformance_with_fixture(self):
        files = {"game.pak": sample_pak_bytes(version=8)}
        rep = run_conformance(UnrealPakPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertEqual(rep.get("manifest").status, "PASS")
        self.assertEqual(rep.get("readonly").status, "PASS")
        self.assertEqual(rep.get("candidates").status, "PASS")
        self.assertEqual(rep.get("probe").status, "PASS")
        self.assertEqual(rep.get("parse").status, "SKIP")
        self.assertEqual(rep.get("write").status, "SKIP")
        self.assertEqual(rep.get("container").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")
        self.assertEqual(rep.get("guard").status, "PASS")


class TestUnrealCli(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-realcli-unreal-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args), cwd=str(self._shared_proj),
                           capture_output=True, text=True, timeout=subprocess_timeout(120),
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_plugin_list_shows_unreal_plugins(self):
        rc, out, err = self.cli("plugin", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("unreal_pak  [ok]", out, err)
        self.assertIn("unreal_locres  [ok]", out, err)

    def test_plugin_test_unreal_pak(self):
        rc, out, err = self.cli("plugin", "test", "unreal_pak")
        self.assertEqual(rc, 0, err + out)
        self.assertIn("KẾT QUẢ: PASS", out, err + out)

    def test_plugin_test_unreal_locres(self):
        rc, out, err = self.cli("plugin", "test", "unreal_locres")
        self.assertEqual(rc, 0, err + out)
        self.assertIn("KẾT QUẢ: PASS", out, err + out)


if __name__ == "__main__":
    unittest.main()
