import hashlib
import os
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import helpers  # noqa: F401
from tests.helpers import TmpCase
from core import hashing, safe_io
from core.errors import PB3Error
from core.limits import Limits
from core.progress import CancelToken


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except PB3Error as e:
        return e.code
    return None


class TestRead(TmpCase):
    def test_check_readable_input(self):
        f = self.tmp / "a.bin"
        f.write_bytes(b"123")
        self.assertEqual(safe_io.check_readable_input(f), f.resolve())
        self.assertTrue(safe_io.check_readable_input(self.tmp).is_dir())
        self.assertEqual(code_of(safe_io.check_readable_input, self.tmp / "none"), "E_INPUT_NOT_FOUND")
        self.assertEqual(code_of(safe_io.check_readable_input, self.tmp, allow_dir=False), "E_INPUT_NOT_FOUND")
        self.assertEqual(code_of(safe_io.check_readable_input, "a\x00b"), "E_UNSAFE_PATH")
        self.assertEqual(code_of(safe_io.check_readable_input, f, Limits(max_input_bytes=2)), "E_TOO_LARGE")
        self.assertEqual(code_of(safe_io.check_readable_input, "x" * 5000), "E_UNSAFE_PATH")

    def test_dangling_symlink(self):
        if not helpers.can_symlink(self.tmp):
            self.skipTest("không có symlink")
        os.symlink(str(self.tmp / "ghost"), str(self.tmp / "dangling"))
        self.assertEqual(code_of(safe_io.check_readable_input, self.tmp / "dangling"), "E_INPUT_NOT_FOUND")

    def test_read_bytes_limited(self):
        f = self.tmp / "a.bin"
        f.write_bytes(b"0123456789")
        self.assertEqual(safe_io.read_bytes_limited(f, 100), b"0123456789")
        self.assertEqual(safe_io.read_bytes_limited(f, 100, offset=2, length=3), b"234")
        self.assertEqual(safe_io.read_bytes_limited(f, 100, offset=50), b"")
        self.assertEqual(code_of(safe_io.read_bytes_limited, f, 5), "E_TOO_LARGE")
        self.assertEqual(code_of(safe_io.read_bytes_limited, self.tmp / "x", 5), "E_INPUT_NOT_FOUND")
        self.assertEqual(code_of(safe_io.read_bytes_limited, self.tmp, 5), "E_INPUT_NOT_FOUND")

    def test_truncated_while_reading_is_corrupt(self):
        f = self.tmp / "a.bin"
        f.write_bytes(b"0123456789")
        real_open = open

        class Short:
            def __init__(self, fh):
                self.fh = fh

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.fh.close()

            def seek(self, n):
                self.fh.seek(n)

            def read(self, n):
                return self.fh.read(n // 2)

        with mock.patch("builtins.open", lambda p, m="r", *a, **k: Short(real_open(p, m, *a, **k))):
            self.assertEqual(code_of(safe_io.read_bytes_limited, f, 100), "E_CORRUPT")

    def test_read_text_limited_bad_utf8_is_corrupt(self):
        f = self.tmp / "t.txt"
        f.write_bytes("Việt".encode("utf-8"))
        self.assertEqual(safe_io.read_text_limited(f, 100), "Việt")
        f.write_bytes(b"ok\xff\xfe")
        self.assertEqual(code_of(safe_io.read_text_limited, f, 100), "E_CORRUPT")

    def test_zero_byte_and_iter_chunks(self):
        f = self.tmp / "z"
        f.write_bytes(b"")
        self.assertEqual(safe_io.read_bytes_limited(f, 10), b"")
        self.assertEqual(list(safe_io.iter_chunks(f)), [])
        g = self.tmp / "g"
        g.write_bytes(b"x" * 25)
        self.assertEqual([len(c) for c in safe_io.iter_chunks(g, 10)], [10, 10, 5])

    def test_iter_chunks_cancel(self):
        g = self.tmp / "g"
        g.write_bytes(b"x" * 100)
        tok = CancelToken()
        it = safe_io.iter_chunks(g, 10, cancel=tok)
        next(it)
        tok.cancel()
        with self.assertRaises(PB3Error) as cm:
            next(it)
        self.assertEqual(cm.exception.code, "E_CANCELLED")


class TestAtomicWrite(TmpCase):
    def leftovers(self):
        return [p for p in self.tmp.rglob("*") if safe_io.STALE_MARKER in p.name]

    def test_success_creates_file_and_no_temp(self):
        d = self.tmp / "sub" / "x.bin"
        safe_io.atomic_write_bytes(d, b"data")
        self.assertEqual(d.read_bytes(), b"data")
        self.assertEqual(self.leftovers(), [])

    def test_exception_keeps_old_file_and_cleans_temp(self):
        d = self.tmp / "x.bin"
        d.write_bytes(b"OLD")
        with self.assertRaises(RuntimeError):
            with safe_io.AtomicWriter(d) as w:
                w.write(b"NEW-PARTIAL")
                raise RuntimeError("giữa chừng")
        self.assertEqual(d.read_bytes(), b"OLD")
        self.assertEqual(self.leftovers(), [])

    def test_keyboard_interrupt_cleans(self):
        d = self.tmp / "x.bin"
        with self.assertRaises(KeyboardInterrupt):
            with safe_io.AtomicWriter(d) as w:
                w.write(b"abc")
                raise KeyboardInterrupt
        self.assertFalse(d.exists())
        self.assertEqual(self.leftovers(), [])

    def test_verify_failure_refuses_and_preserves_old(self):
        d = self.tmp / "x.bin"
        d.write_bytes(b"OLD")

        def bad(p):
            raise ValueError("sai")
        self.assertEqual(code_of(safe_io.atomic_write_bytes, d, b"NEW", verify=bad), "E_VERIFY_FAILED")
        self.assertEqual(d.read_bytes(), b"OLD")
        self.assertEqual(self.leftovers(), [])

    def test_verify_failure_can_keep_failed_copy(self):
        d = self.tmp / "out" / "x.bin"
        failed = self.tmp / "failed"

        def bad(p):
            raise ValueError("sai")
        self.assertEqual(code_of(safe_io.atomic_write_bytes, d, b"NEW", verify=bad, failed_dir=failed),
                         "E_VERIFY_FAILED")
        self.assertFalse(d.exists())
        kept = list(failed.iterdir())
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), b"NEW")

    def test_verify_sees_complete_data(self):
        d = self.tmp / "x.bin"
        seen = {}
        safe_io.atomic_write_bytes(d, b"hello", verify=lambda p: seen.update(data=Path(p).read_bytes()))
        self.assertEqual(seen["data"], b"hello")

    def test_cancel_during_write(self):
        d = self.tmp / "x.bin"
        tok = CancelToken()
        with self.assertRaises(PB3Error):
            with safe_io.AtomicWriter(d, cancel=tok) as w:
                w.write(b"a")
                tok.cancel()
                w.write(b"b")
        self.assertFalse(d.exists())
        self.assertEqual(self.leftovers(), [])

    def test_no_space_refuses_before_writing(self):
        d = self.tmp / "x.bin"
        with mock.patch.object(safe_io, "free_bytes", return_value=10):
            self.assertEqual(code_of(safe_io.atomic_write_bytes, d, b"x" * 100, disk_factor=2.5), "E_NO_SPACE")
        self.assertFalse(d.exists())
        self.assertEqual(self.leftovers(), [])

    def test_backup_option(self):
        d = self.tmp / "x.bin"
        d.write_bytes(b"OLD")
        safe_io.atomic_write_bytes(d, b"NEW", backup=True)
        baks = list(self.tmp.glob("x.bin.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), b"OLD")
        self.assertEqual(d.read_bytes(), b"NEW")

    def test_long_unicode_name(self):
        d = self.tmp / ("中" * 80 + ".bin")
        safe_io.atomic_write_bytes(d, b"1")
        self.assertTrue(d.exists())
        self.assertEqual(self.leftovers(), [])

    def test_unwritable_parent_maps_to_pb3error(self):
        (self.tmp / "file").write_text("x")
        self.assertIn(code_of(safe_io.atomic_write_bytes, self.tmp / "file" / "x.bin", b"1"),
                      ("E_IO", "E_INPUT_NOT_FOUND", "E_UNSAFE_PATH"))


class TestOutputPolicy(TmpCase):
    def test_first_plain_then_versions_never_overwrite(self):
        r = "Payload/Game.app/Data/xxx.unity3d"
        p0 = safe_io.write_output_bytes(self.tmp, r, b"1")
        p1 = safe_io.write_output_bytes(self.tmp, r, b"2")
        p2 = safe_io.write_output_bytes(self.tmp, r, b"3")
        self.assertEqual([p.name for p in (p0, p1, p2)], ["xxx.unity3d", "xxx.v1.unity3d", "xxx.v2.unity3d"])
        self.assertEqual(p0.read_bytes(), b"1")

    def test_error_policy(self):
        safe_io.write_output_bytes(self.tmp, "a.bin", b"1")
        self.assertEqual(code_of(safe_io.write_output_bytes, self.tmp, "a.bin", b"2", policy="error"),
                         "E_OUTPUT_EXISTS")
        self.assertEqual((self.tmp / "a.bin").read_bytes(), b"1")

    def test_bad_policy(self):
        safe_io.write_output_bytes(self.tmp, "a.bin", b"1")
        self.assertEqual(code_of(safe_io.write_output_bytes, self.tmp, "a.bin", b"2", policy="overwrite"),
                         "E_INTERNAL")

    def test_escape_blocked(self):
        for rel in ("../evil", "/etc/evil", "a/../../evil"):
            self.assertEqual(code_of(safe_io.write_output_bytes, self.tmp / "out", rel, b"x"), "E_UNSAFE_PATH")
        self.assertFalse((self.tmp / "evil").exists())

    def test_dry_run_creates_nothing(self):
        p = safe_io.write_output_bytes(self.tmp / "out", "a/b.bin", b"1", dry_run=True)
        self.assertEqual(p, self.tmp / "out" / "a" / "b.bin")
        self.assertFalse((self.tmp / "out").exists())


class TestCopyHashStale(TmpCase):
    def test_copy_atomic_and_hash(self):
        src = self.tmp / "s.bin"
        data = os.urandom(3 * 1024 + 7)
        src.write_bytes(data)
        h, n = safe_io.copy_atomic(src, self.tmp / "d" / "c.bin", chunk_size=1000)
        self.assertEqual((h, n), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertEqual((self.tmp / "d" / "c.bin").read_bytes(), data)
        self.assertEqual(hashing.sha256_file(src, chunk_size=100), h)

    def test_copy_cancel_midway_leaves_nothing(self):
        src = self.tmp / "s.bin"
        src.write_bytes(b"x" * 10000)
        tok = CancelToken()

        class P:
            def update(self, done=None, **k):
                if done and done >= 3000:
                    tok.cancel()
        self.assertEqual(code_of(safe_io.copy_atomic, src, self.tmp / "c.bin", chunk_size=1000,
                                 cancel=tok, progress=P()), "E_CANCELLED")
        self.assertFalse((self.tmp / "c.bin").exists())
        self.assertEqual([p for p in self.tmp.iterdir() if safe_io.STALE_MARKER in p.name], [])

    def test_copy_missing_source(self):
        self.assertEqual(code_of(safe_io.copy_atomic, self.tmp / "none", self.tmp / "c"), "E_INPUT_NOT_FOUND")

    def test_describe_never_includes_content(self):
        self.assertNotIn("SECRET", hashing.describe_bytes(b"SECRET"))
        f = self.tmp / "g.bin"
        f.write_bytes(b"SECRET")
        d = hashing.describe_file(f, with_hash=True)
        self.assertIn("size=6", d)
        self.assertNotIn("SECRET", d)

    def test_stale_find_and_quarantine_not_delete(self):
        (self.tmp / "out").mkdir()
        stale = self.tmp / "out" / (".x.bin" + safe_io.STALE_MARKER + "1-ab")
        stale.write_bytes("dở dang".encode("utf-8"))
        keep = self.tmp / "out" / "real.bin"
        keep.write_bytes(b"ok")
        found = safe_io.find_stale_temps([self.tmp / "out", self.tmp / "missing"])
        self.assertEqual(found, [stale])
        self.assertEqual(safe_io.find_stale_temps([self.tmp / "out"], min_age_s=3600), [])
        moved = safe_io.quarantine(found, self.tmp / "q")
        self.assertEqual(len(moved), 1)
        self.assertFalse(stale.exists())
        self.assertEqual(moved[0][1].read_bytes(), "dở dang".encode("utf-8"))
        self.assertTrue(keep.exists())

    def test_remove_tree_inside(self):
        base = self.tmp / "base"
        (base / "a").mkdir(parents=True)
        (base / "a" / "f").write_text("x")
        safe_io.remove_tree_inside(base, base / "a")
        self.assertFalse((base / "a").exists())
        for bad in (base, self.tmp, self.tmp / "other"):
            self.assertEqual(code_of(safe_io.remove_tree_inside, base, bad), "E_UNSAFE_PATH")
        self.assertTrue(base.exists())

    def test_remove_tree_blocks_symlink_escape(self):
        if not helpers.can_symlink(self.tmp):
            self.skipTest("không có symlink")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep").write_text("x")
        base = self.tmp / "base"
        base.mkdir()
        os.symlink(str(outside), str(base / "lnk"))
        self.assertEqual(code_of(safe_io.remove_tree_inside, base, base / "lnk"), "E_UNSAFE_PATH")
        self.assertTrue((outside / "keep").exists())


if __name__ == "__main__":
    unittest.main()
