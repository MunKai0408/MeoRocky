"""Test Godot PCK (Claude 08, nhóm Godot): sdk.godot.PckVFS + plugin godot_pck.

Fixture tự dựng trong RAM bằng tests/fixtures/godot/builder.py (xác định,
không dùng os.urandom — decisions #143). Dữ liệu mẫu có tiếng Việt có dấu.
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.errors import PB3Error
from core.limits import Limits
from plugins.godot_pck.plugin import GodotPckPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.containers import open_vfs
from sdk.godot import PckVFS
from sdk.vfs import MemoryVFS
from tests.fixtures.godot import builder as gb
from tests.helpers import (ROOT, TmpCase, copy_project, register_tmp_dir,
                           slow_subprocess, subprocess_timeout, unregister_tmp_dir)

HELLO = "Xin chào".encode("utf-8")
BYE = "Tạm biệt, hẹn gặp lại!".encode("utf-8")
SETTINGS = "Cài đặt: âm lượng 80%, ngôn ngữ Tiếng Việt".encode("utf-8")


def _pck_v1():
    return gb.build_pck_v1([
        ("res://hello.txt", HELLO),
        ("res://texts/bye.txt", BYE),
        ("res://config/settings.txt", SETTINGS),
    ])


def _pck_v2():
    return gb.build_pck_v2([
        ("res://hello.txt", HELLO),
        ("res://texts/bye.txt", BYE),
        ("res://config/settings.txt", SETTINGS),
    ])


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "godot_pck")


class TestPckV1(TmpCase):
    def test_list_read_stat_slice(self):
        vfs = PckVFS(_pck_v1())
        try:
            self.assertEqual(vfs.pack_version, 1)
            self.assertEqual(vfs.godot_version, (3, 5, 2))
            self.assertEqual(list(vfs.iter_entries()),
                             ["hello.txt", "texts/bye.txt", "config/settings.txt"])
            self.assertEqual(vfs.read("hello.txt", None), HELLO)
            self.assertEqual(vfs.read("texts/bye.txt", None), BYE)
            self.assertEqual(vfs.stat("config/settings.txt").size, len(SETTINGS))
            self.assertFalse(vfs.stat("hello.txt").crc_verified)
            self.assertEqual(vfs.slice("hello.txt", 3, 4), HELLO[3:7])
            self.assertEqual(vfs.read("hello.txt", max_bytes=4), HELLO[:4])
            inv = vfs.inventory()
            self.assertEqual(inv.kind, "godot_pck")
            self.assertEqual(len(inv.entries), 3)
            self.assertEqual(inv.rejected, [])
            with vfs.open_stream("texts/bye.txt") as st:
                self.assertEqual(st.read(), BYE)
                st.seek(0)
                self.assertEqual(st.read(5), BYE[:5])
        finally:
            vfs.close()

    def test_from_path(self):
        p = self.tmp / "game.pck"
        p.write_bytes(_pck_v1())
        vfs = PckVFS(str(p))
        try:
            self.assertEqual(vfs.read("hello.txt", None), HELLO)
            self.assertEqual(vfs.source_name, "game.pck")
        finally:
            vfs.close()

    def test_prefix_entries(self):
        vfs = PckVFS(_pck_v1())
        try:
            self.assertEqual(list(vfs.iter_entries("texts/")), ["texts/bye.txt"])
        finally:
            vfs.close()


class TestPckV2(TmpCase):
    def test_list_read(self):
        vfs = PckVFS(_pck_v2())
        try:
            self.assertEqual(vfs.pack_version, 2)
            self.assertEqual(vfs.godot_version, (4, 2, 1))
            self.assertEqual(list(vfs.iter_entries()),
                             ["hello.txt", "texts/bye.txt", "config/settings.txt"])
            self.assertEqual(vfs.read("hello.txt", None), HELLO)
            self.assertEqual(vfs.read("config/settings.txt", None), SETTINGS)
            self.assertEqual(vfs.slice("texts/bye.txt", 0, 6), BYE[:6])
        finally:
            vfs.close()

    def test_open_vfs_dispatch_gdpc(self):
        # Dispatch GDPC trong sdk/containers.py: mở PCK thật trả PckVFS
        # (test khóa cũ kỳ vọng lỗi "backend chưa có" đã được thay bằng
        # test_open_vfs_godot_pck_and_unreal_pak trong tests/test_container.py
        # lúc tích hợp Claude 08, decisions #157).
        vfs = open_vfs(_pck_v2())
        try:
            self.assertIsInstance(vfs, PckVFS)
            self.assertEqual(vfs.read("hello.txt", None), HELLO)
        finally:
            vfs.close()

    def test_md5_mismatch_is_bad_archive(self):
        data = bytearray(_pck_v2())
        # Lật 1 byte trong dữ liệu entry đầu (cuối file là dữ liệu).
        data[-1] ^= 0xFF
        vfs = PckVFS(bytes(data))
        try:
            with self.assertRaises(PB3Error) as cm:
                vfs.read("config/settings.txt", None)
            self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")
            # Slice không kiểm MD5 nên vẫn đọc được (quy ước như ZipVFS).
            self.assertEqual(len(vfs.slice("config/settings.txt", 0, 4)), 4)
        finally:
            vfs.close()

    def test_empty_entry(self):
        vfs = PckVFS(gb.build_pck_v2([("res://empty.txt", b"")]))
        try:
            self.assertEqual(vfs.read("empty.txt", None), b"")
            self.assertEqual(vfs.stat("empty.txt").size, 0)
        finally:
            vfs.close()


class TestPckRejected(TmpCase):
    def test_encrypted_entry_rejected_and_read_errors(self):
        data = gb.build_pck_v2([
            ("res://hello.txt", HELLO),
            ("res://secret.txt", b"bi mat", gb.FILE_ENCRYPTED),
        ])
        vfs = PckVFS(data)
        try:
            self.assertEqual(list(vfs.iter_entries()), ["hello.txt"])
            inv = vfs.inventory()
            self.assertEqual(inv.rejected, [("secret.txt", "entry mã hóa (cần khóa Godot, không giải mã)")])
            with self.assertRaises(PB3Error) as cm:
                vfs.read("secret.txt", None)
            self.assertEqual(cm.exception.code, "E_ENCRYPTED")
        finally:
            vfs.close()

    def test_deleted_and_unknown_flag_rejected(self):
        data = gb.build_pck_v2([
            ("res://gone.txt", b"x", gb.FILE_DELETED),
            ("res://weird.txt", b"y", 1 << 5),
            ("res://hello.txt", HELLO),
        ])
        vfs = PckVFS(data)
        try:
            self.assertEqual(list(vfs.iter_entries()), ["hello.txt"])
            names = [n for n, _r in vfs.inventory().rejected]
            self.assertEqual(names, ["gone.txt", "weird.txt"])
        finally:
            vfs.close()

    def test_dir_encrypted_pack_is_encrypted_error(self):
        data = gb.build_pck_v2([("res://hello.txt", HELLO)],
                               pack_flags=gb.PACK_DIR_ENCRYPTED)
        with self.assertRaises(PB3Error) as cm:
            PckVFS(data)
        self.assertEqual(cm.exception.code, "E_ENCRYPTED")

    def test_unsafe_and_duplicate_names_rejected(self):
        data = gb.build_pck_v2([
            ("res://../thoat.txt", b"a"),
            ("res://hello.txt", HELLO),
            ("res://hello.txt", BYE),
        ])
        vfs = PckVFS(data)
        try:
            self.assertEqual(list(vfs.iter_entries()), ["hello.txt"])
            self.assertEqual(vfs.read("hello.txt", None), HELLO)  # lần đầu thắng
            reasons = dict(vfs.inventory().rejected)
            self.assertIn("../thoat.txt", reasons)
            self.assertIn("hello.txt", reasons)
            self.assertIn("trùng tên", reasons["hello.txt"])
        finally:
            vfs.close()

    def test_missing_entry(self):
        vfs = PckVFS(_pck_v1())
        try:
            with self.assertRaises(PB3Error) as cm:
                vfs.read("khong-co.txt", None)
            self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")
        finally:
            vfs.close()


class TestPckBadFiles(TmpCase):
    def test_bad_magic(self):
        with self.assertRaises(PB3Error) as cm:
            PckVFS(b"NOPE" + b"\x00" * 100)
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_truncated(self):
        with self.assertRaises(PB3Error) as cm:
            PckVFS(b"GDPC\x01\x00\x00\x00")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_version_3_unsupported(self):
        # pack_version 3 (Godot 4.5+): nhận diện được nhưng chưa hỗ trợ.
        data = bytearray(_pck_v2())
        struct.pack_into("<I", data, 4, 3)
        with self.assertRaises(PB3Error) as cm:
            PckVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_version_0_unsupported(self):
        data = bytearray(_pck_v2())
        struct.pack_into("<I", data, 4, 0)
        with self.assertRaises(PB3Error) as cm:
            PckVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_bad_path_length(self):
        data = bytearray(_pck_v1())
        # v1: header 84 byte + count u32 ở 84..88, path_len entry đầu ở 88.
        struct.pack_into("<I", data, 88, 7)  # path_len 7: không căn 4
        with self.assertRaises(PB3Error) as cm:
            PckVFS(bytes(data))
        self.assertEqual(cm.exception.code, "E_BAD_ARCHIVE")

    def test_entry_beyond_file(self):
        # Cắt ngắn file ngay giữa thư mục: đọc entry bị cắt cụt.
        cut = _pck_v1()[:120]
        with self.assertRaises(PB3Error) as cm:
            PckVFS(cut)
        self.assertIn(cm.exception.code, ("E_CORRUPT", "E_BAD_ARCHIVE"))


class TestPckLimits(TmpCase):
    def test_too_many_entries_is_archive_bomb(self):
        lim = Limits(max_entries=2)
        with self.assertRaises(PB3Error) as cm:
            PckVFS(_pck_v1(), limits=lim)
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    def test_total_too_large_is_archive_bomb(self):
        lim = Limits(max_total_uncompressed_bytes=10)
        with self.assertRaises(PB3Error) as cm:
            PckVFS(_pck_v1(), limits=lim)
        self.assertEqual(cm.exception.code, "E_ARCHIVE_BOMB")

    def test_deterministic_bytes_not_urandom(self):
        # Khóa decisions #143: fixture không cần os.urandom.
        import os as _os
        real = _os.urandom

        def _boom(n):
            raise OSError(5, "I/O error")

        _os.urandom = _boom
        try:
            data = gb.deterministic_bytes(1000, seed=7)
            self.assertEqual(len(data), 1000)
            self.assertEqual(data, gb.deterministic_bytes(1000, seed=7))
            self.assertNotEqual(data, gb.deterministic_bytes(1000, seed=8))
        finally:
            _os.urandom = real


class TestGodotPckPlugin(TmpCase):
    def test_detect_list_probe_open_selftest(self):
        data = _pck_v2()
        plug = GodotPckPlugin()
        ctx = _ctx({"game.pck": data, "note.txt": b"x"}, self.tmp).for_plugin(plug.manifest)
        self.assertEqual([e.value for e in plug.detect(ctx)], ["GDPC"])
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["game.pck"])
        self.assertEqual(cands[0].score, 0.9)
        probe = plug.probe(ctx, cands[0])
        self.assertTrue(probe["ok"], probe)
        self.assertEqual(probe["entries"], 3)
        with plug.open_container(ctx, "game.pck") as vfs:
            self.assertEqual(vfs.read("hello.txt", None), HELLO)
        self.assertTrue(plug.selftest(ctx)["ok"])

    def test_probe_bad_pck(self):
        plug = GodotPckPlugin()
        ctx = _ctx({"game.pck": b"GDPC" + b"\x00" * 100}, self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        probe = plug.probe(ctx, cands[0])
        self.assertFalse(probe["ok"])
        self.assertIn("E_", probe["reason"])

    def test_conformance_with_fixture(self):
        rep = run_conformance(GodotPckPlugin, files={"game.pck": _pck_v2()})
        self.assertTrue(rep.ok, rep.format(48))
        self.assertEqual(rep.get("container").status, "PASS")
        self.assertEqual(rep.get("manifest").status, "PASS")

    def test_registry_lists_godot_pck(self):
        from registry.registry import build_registry
        reg = build_registry(str(ROOT), None)
        rows = {r["id"]: r for r in reg.rows()}
        self.assertIn("godot_pck", rows)
        self.assertEqual(rows["godot_pck"]["state"], "ok")
        self.assertEqual(rows["godot_pck"]["kind"], "container")
        self.assertEqual(rows["godot_pck"]["maturity"], "M1")


@slow_subprocess
class TestGodotPckCli(TmpCase):
    """CLI thật trên bản sao dự án (tầng slow)."""

    _shared_td = None
    _shared_proj = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3test-godot-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args),
                           cwd=str(self._shared_proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(120),
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_plugin_list_shows_godot_pck(self):
        rc, out, err = self.cli("plugin", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("godot_pck  [ok]", out, err)

    def test_plugin_test_godot_pck(self):
        rc, out, err = self.cli("plugin", "test", "godot_pck")
        self.assertEqual(rc, 0, err)
        self.assertIn("KẾT QUẢ: PASS", out, err)


if __name__ == "__main__":
    unittest.main()
