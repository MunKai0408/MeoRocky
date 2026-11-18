"""Test kiểm tra tính toàn vẹn file (Anti-Wave, Phase 2).

classify_change() là pure (không chạm đĩa) nên chạy mọi lúc, kể cả khi
sóng EPERM đang bật. Test ghi/đọc đĩa thật CHỈ chạy khi probe WAVE_OFF:
setUp probe trước rồi skip — không bao giờ gọi tempfile khi sóng bật
(tempfile chết trong sóng EPERM [ĐÃ ĐO THẬT]).
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import integrity as ig
from anti_wave.healthcheck import WAVE_ON, probe_wave


def _wave_on() -> bool:
    """True neu probe thay song EPERM (chi doc, khong ghi, khong tempfile)."""
    try:
        return probe_wave().status == WAVE_ON
    except Exception:
        return False


def _rec(path, sha, nbytes, exists=True):
    return {"path": str(path), "sha256": sha, "bytes": nbytes, "exists": exists}


class TestSha256(unittest.TestCase):
    def test_sha256_bytes_known_vectors(self):
        self.assertEqual(ig.sha256_bytes(b""), hashlib.sha256(b"").hexdigest())
        self.assertEqual(ig.sha256_bytes(b"abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(ig.sha256_bytes(b"anti-wave"),
                         hashlib.sha256(b"anti-wave").hexdigest())


class TestClassifyChange(unittest.TestCase):
    """Pure — chạy mọi lúc, không chạm đĩa."""

    def test_ok(self):
        self.assertEqual(
            ig.classify_change(_rec("a", "x" * 64, 10), _rec("a", "x" * 64, 10)),
            ig.STATUS_OK)

    def test_missing(self):
        self.assertEqual(
            ig.classify_change(_rec("a", "x" * 64, 10), _rec("a", None, None, False)),
            ig.STATUS_MISSING)

    def test_zero_byte(self):
        self.assertEqual(
            ig.classify_change(_rec("a", "x" * 64, 10), _rec("a", "d" * 64, 0)),
            ig.STATUS_ZERO_BYTE)

    def test_zero_byte_expected_zero_is_ok(self):
        # File goc von 0 byte thi 0 byte hien tai la binh thuong, khong phai truncate.
        self.assertEqual(
            ig.classify_change(_rec("a", "e" * 64, 0), _rec("a", "e" * 64, 0)),
            ig.STATUS_OK)

    def test_truncated(self):
        self.assertEqual(
            ig.classify_change(_rec("a", "x" * 64, 100), _rec("a", "y" * 64, 40)),
            ig.STATUS_TRUNCATED)

    def test_changed_same_size(self):
        self.assertEqual(
            ig.classify_change(_rec("a", "x" * 64, 10), _rec("a", "y" * 64, 10)),
            ig.STATUS_CHANGED)

    def test_changed_size_no_sha(self):
        self.assertEqual(
            ig.classify_change(_rec("a", None, 10), _rec("a", None, 12)),
            ig.STATUS_CHANGED)
        self.assertEqual(
            ig.classify_change(_rec("a", None, 10), _rec("a", None, 10)),
            ig.STATUS_OK)


class TestRealFiles(unittest.TestCase):
    """Test đĩa thật — CHỈ chạy khi probe WAVE_OFF (sóng tắt)."""

    def setUp(self):
        if _wave_on():
            self.skipTest("sóng EPERM đang bật — bỏ qua test ghi đĩa thật")
        self.td = Path(tempfile.mkdtemp(prefix="awf-int-"))
        self.addCleanup(shutil.rmtree, str(self.td), True)

    def _mk(self, name, data: bytes) -> Path:
        p = self.td / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_sha256_file_matches_bytes(self):
        p = self._mk("a.bin", b"hello anti-wave")
        self.assertEqual(ig.sha256_file(p), ig.sha256_bytes(b"hello anti-wave"))

    def test_snapshot_verify_ok(self):
        a = self._mk("a.bin", b"aaa")
        b = self._mk("sub/b.bin", b"bbb")
        snap = ig.snapshot([a, b])
        res = ig.verify_snapshot(snap)
        self.assertEqual([r["status"] for r in res], [ig.STATUS_OK, ig.STATUS_OK])
        self.assertEqual(res[0]["actual_bytes"], 3)

    def test_detect_changed(self):
        p = self._mk("a.bin", b"0123456789")
        snap = ig.snapshot([p])
        p.write_bytes(b"012345678X")  # cung 10 byte, khac noi dung
        self.assertEqual(ig.verify_snapshot(snap)[0]["status"], ig.STATUS_CHANGED)

    def test_detect_truncated(self):
        p = self._mk("a.bin", b"0123456789")
        snap = ig.snapshot([p])
        p.write_bytes(b"01234")
        self.assertEqual(ig.verify_snapshot(snap)[0]["status"], ig.STATUS_TRUNCATED)

    def test_detect_zero_byte(self):
        p = self._mk("a.bin", b"0123456789")
        snap = ig.snapshot([p])
        p.write_bytes(b"")
        res = ig.verify_snapshot(snap)[0]
        self.assertEqual(res["status"], ig.STATUS_ZERO_BYTE)
        self.assertEqual(res["actual_bytes"], 0)

    def test_detect_missing(self):
        p = self._mk("a.bin", b"0123456789")
        snap = ig.snapshot([p])
        p.unlink()
        self.assertEqual(ig.verify_snapshot(snap)[0]["status"], ig.STATUS_MISSING)

    def test_verify_file_and_detect_truncate(self):
        p = self._mk("a.bin", b"0123456789")
        sha = ig.sha256_file(p)
        self.assertTrue(ig.verify_file(p, sha, 10)["ok"])
        self.assertFalse(ig.verify_file(p, "0" * 64, 10)["ok"])
        self.assertEqual(ig.verify_file(p, "0" * 64, 10)["status"], ig.STATUS_CHANGED)
        self.assertFalse(ig.detect_truncate(p, expected_bytes=10)["truncated"])
        p.write_bytes(b"01")
        self.assertTrue(ig.detect_truncate(p, expected_bytes=10)["truncated"])
        self.assertEqual(ig.detect_truncate(p, expected_bytes=10)["status"],
                         ig.STATUS_TRUNCATED)

    def test_scan_for_zero_bytes(self):
        self._mk("a.bin", b"x")
        z1 = self._mk("zero.bin", b"")
        z2 = self._mk("sub/zero2.bin", b"")
        self._mk("sub/b.bin", b"y")
        found = ig.scan_for_zero_bytes(self.td)
        self.assertEqual(found, sorted([z1, z2]))
        # Khong de quy: khong thay file trong sub.
        self.assertEqual(ig.scan_for_zero_bytes(self.td, recursive=False), [z1])
        # Thu muc khong ton tai -> [] (khong raise).
        self.assertEqual(ig.scan_for_zero_bytes(self.td / "khong-co"), [])


if __name__ == "__main__":
    unittest.main()
