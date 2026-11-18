"""Test phat hien song EPERM + may trang thai (Anti-Wave, Phase 2).

Phan lon test o day DUNG MOCK, khong ghi dia that — chay duoc ngay ca khi
song EPERM dang bat (tempfile/os.write that deu chet trong song). Rieng
test ghi dia that (write_bytes_guarded) probe truoc va SKIP khi song bat.
"""
import errno
import io
import logging
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import healthcheck as hc
from anti_wave import logger as awlog
from anti_wave.controller import Controller, NORMAL, RECOVERY, WAVE
from anti_wave.healthcheck import (
    FS_READONLY,
    OTHER,
    READ_CORRUPT,
    READ_DENIED,
    READ_NOT_FOUND,
    WAVE_OFF,
    WAVE_ON,
    WRITE_DENIED,
    WRITE_EPERM_WAVE,
    WRITE_NO_SPACE,
    ProbeResult,
    WaveState,
)
from core import safe_io
from core.errors import PB3Error


def _err(no):
    return OSError(no, "mock")


class TestClassifyError(unittest.TestCase):
    def test_eperm_write_is_wave(self):
        c = hc.classify_error(_err(errno.EPERM), "write")
        self.assertEqual(c.code, WRITE_EPERM_WAVE)
        self.assertIn("sóng", c.hint_vi)

    def test_eperm_read_is_denied_not_wave(self):
        self.assertEqual(hc.classify_error(_err(errno.EPERM), "read").code, READ_DENIED)

    def test_eacces(self):
        self.assertEqual(hc.classify_error(_err(errno.EACCES), "read").code, READ_DENIED)
        self.assertEqual(hc.classify_error(_err(errno.EACCES), "write").code, WRITE_DENIED)

    def test_enoent(self):
        self.assertEqual(hc.classify_error(_err(errno.ENOENT), "read").code, READ_NOT_FOUND)
        # Ghi thieu thu muc cha khong co ma rieng -> OTHER (hint neu ro).
        self.assertEqual(hc.classify_error(_err(errno.ENOENT), "write").code, OTHER)

    def test_enospc_and_edquot(self):
        self.assertEqual(hc.classify_error(_err(errno.ENOSPC), "write").code, WRITE_NO_SPACE)
        self.assertEqual(hc.classify_error(_err(errno.ENOSPC), "read").code, WRITE_NO_SPACE)
        if hasattr(errno, "EDQUOT"):
            self.assertEqual(hc.classify_error(_err(errno.EDQUOT), "write").code,
                             WRITE_NO_SPACE)

    def test_erofs(self):
        self.assertEqual(hc.classify_error(_err(errno.EROFS), "write").code, FS_READONLY)
        self.assertEqual(hc.classify_error(_err(errno.EROFS), "read").code, FS_READONLY)

    def test_corrupt(self):
        try:
            b"\xff".decode("utf-8")
        except UnicodeDecodeError as exc:
            c = hc.classify_error(exc, "read")
        self.assertEqual(c.code, READ_CORRUPT)
        self.assertEqual(hc.classify_error(zipfile.BadZipFile("x"), "read").code,
                         READ_CORRUPT)

    def test_other(self):
        self.assertEqual(hc.classify_error(ValueError("x"), "write").code, OTHER)
        self.assertEqual(hc.classify_error(_err(errno.EIO), "write").code, OTHER)
        # Moi ma deu co goi y tieng Viet khong rong.
        for code in (READ_NOT_FOUND, READ_DENIED, READ_CORRUPT, WRITE_EPERM_WAVE,
                     WRITE_DENIED, WRITE_NO_SPACE, FS_READONLY, OTHER):
            self.assertTrue(hc.HINTS[code].strip(), code)


class TestProbeWave(unittest.TestCase):
    def _mock_ok(self):
        """Gia lap ghi + doc lai thanh cong (khong cham dia that)."""
        p = mock.patch.object(os, "open", return_value=99)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(os, "write", return_value=len(hc.PROBE_BYTES))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(os, "fsync", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(os, "close", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(os, "unlink", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch("builtins.open", mock.mock_open(read_data=hc.PROBE_BYTES))
        p.start()
        self.addCleanup(p.stop)

    def test_wave_on_when_write_raises_eperm(self):
        self._mock_ok()
        with mock.patch.object(os, "write",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            res = hc.probe_wave(directory="/tmp")
        self.assertEqual(res.status, WAVE_ON)
        self.assertEqual(res.errno, errno.EPERM)

    def test_wave_off_when_write_succeeds(self):
        self._mock_ok()
        res = hc.probe_wave(directory="/tmp")
        self.assertEqual(res.status, WAVE_OFF)
        self.assertIsNone(res.errno)

    def test_readback_mismatch_is_not_wave(self):
        self._mock_ok()
        with mock.patch("builtins.open", mock.mock_open(read_data=b"khac")):
            res = hc.probe_wave(directory="/tmp")
        self.assertEqual(res.status, WAVE_OFF)
        self.assertIn("khac", res.detail)

    def test_no_usable_directory(self):
        with mock.patch.object(os.path, "isdir", return_value=False):
            res = hc.probe_wave(directory="/khong-co-thu-muc-nay")
        self.assertEqual(res.status, WAVE_OFF)
        self.assertEqual(res.errno, errno.ENOENT)

    def test_probe_never_raises_on_os_error(self):
        with mock.patch.object(os.path, "isdir", side_effect=OSError(errno.EIO, "x")):
            res = hc.probe_wave(directory="/tmp")
        self.assertIn(res.status, (WAVE_ON, WAVE_OFF))


class TestWaveState(unittest.TestCase):
    def test_record_and_is_wave_on(self):
        ws = WaveState()
        self.assertFalse(ws.is_wave_on())
        ws.record(WAVE_ON, now=100.0)
        self.assertTrue(ws.is_wave_on())
        self.assertEqual(ws.wave_start, 100.0)
        ws.record(WAVE_OFF, now=160.0)
        self.assertFalse(ws.is_wave_on())
        self.assertEqual(ws.wave_end, 160.0)
        self.assertEqual(ws.duration_estimate_s(), 60.0)

    def test_duration_estimate_averages_completed_waves(self):
        ws = WaveState()
        ws.record(WAVE_ON, now=100.0)
        ws.record(WAVE_OFF, now=160.0)   # 60s
        ws.record(WAVE_ON, now=200.0)
        ws.record(WAVE_OFF, now=260.0)   # 60s
        self.assertEqual(ws.duration_estimate_s(), 60.0)
        ws.record(WAVE_ON, now=300.0)
        self.assertEqual(ws.duration_estimate_s(), 60.0)  # song dang mo khong tinh

    def test_duration_estimate_none_without_completed_wave(self):
        self.assertIsNone(WaveState().duration_estimate_s())

    def test_wait_for_calm_all_ok(self):
        ws = WaveState()
        ok = ws.wait_for_calm(checks=3, interval_s=0,
                              probe=lambda: ProbeResult(WAVE_OFF))
        self.assertTrue(ok)
        self.assertEqual(len(ws.history), 3)
        self.assertFalse(ws.is_wave_on())

    def test_wait_for_calm_stops_at_first_wave(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return ProbeResult(WAVE_OFF if calls["n"] < 2 else WAVE_ON)

        ws = WaveState()
        ok = ws.wait_for_calm(checks=5, interval_s=0, probe=flaky)
        self.assertFalse(ok)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(ws.is_wave_on())

    def test_wait_for_calm_rejects_bad_checks(self):
        with self.assertRaises(ValueError):
            WaveState().wait_for_calm(checks=0, interval_s=0,
                                      probe=lambda: ProbeResult(WAVE_OFF))


class TestController(unittest.TestCase):
    def _calm(self):
        return lambda: ProbeResult(WAVE_OFF)

    def _wavy(self):
        return lambda: ProbeResult(WAVE_ON)

    def test_happy_path_normal_wave_recovery_normal(self):
        c = Controller(probe=self._calm())
        self.assertEqual(c.state, NORMAL)
        self.assertEqual(c.to_wave("song bat"), WAVE)
        self.assertTrue(c.is_wave_on())
        self.assertIn("sóng", c.status_summary().lower())
        self.assertEqual(c.to_recovery(checks=2, interval_s=0), RECOVERY)
        self.assertIn("RECOVERY", c.status_summary())
        self.assertEqual(c.to_normal(), NORMAL)
        self.assertEqual(len(c.transitions), 3)
        self.assertIn("NORMAL", c.status_summary())

    def test_to_recovery_needs_calm(self):
        c = Controller(probe=self._wavy())
        c.to_wave()
        with self.assertRaises(PB3Error) as ctx:
            c.to_recovery(checks=2, interval_s=0)
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(c.state, WAVE)  # giu WAVE, khong doi trang thai

    def test_to_normal_needs_calm_probe(self):
        c = Controller(probe=self._calm())
        c.to_wave()
        c.to_recovery(checks=1, interval_s=0)
        c._probe = self._wavy()
        with self.assertRaises(PB3Error) as ctx:
            c.to_normal()
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(c.state, RECOVERY)

    def test_illegal_transitions_raise(self):
        c = Controller(probe=self._calm())
        with self.assertRaises(PB3Error):
            c.to_recovery(checks=1, interval_s=0)  # NORMAL -> RECOVERY
        with self.assertRaises(PB3Error):
            c.to_normal()  # NORMAL -> NORMAL
        c.to_wave()
        with self.assertRaises(PB3Error):
            c.to_wave()  # WAVE -> WAVE
        with self.assertRaises(PB3Error):
            c.to_normal()  # WAVE -> NORMAL thang

    def test_status_summary_vietnamese(self):
        c = Controller(probe=self._calm())
        self.assertIn("NORMAL", c.status_summary())
        c.to_wave()
        self.assertIn("WAVE", c.status_summary())


class TestAtomicWriterEperm(unittest.TestCase):
    def test_atomic_writer_enter_eperm_becomes_e_write_wave(self):
        """AtomicWriter gap EPERM khi tao temp -> E_WRITE_WAVE (qua
        error_from_exception), file dich khong bi dong den."""
        from core import safe_io
        with mock.patch.object(os, "open",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            with self.assertRaises(PB3Error) as ctx:
                with safe_io.AtomicWriter("/tmp/khong-duoc-tao.txt"):
                    pass
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")


def _wave_on() -> bool:
    """True neu probe thay song EPERM (chi doc, khong ghi, khong tempfile)."""
    try:
        return hc.probe_wave().status == WAVE_ON
    except Exception:
        return False


class TestWriteBytesGuarded(unittest.TestCase):
    """write_bytes_guarded(): probe thu muc dich truoc — EPERM thi raise
    E_WRITE_WAVE ngay, khong tao file dich (khong de lai file 0 byte)."""

    def test_eperm_probe_raises_e_write_wave_no_dest(self):
        dest = Path("/tmp") / "awf-guarded-khong-duoc-tao.bin"
        if os.path.lexists(str(dest)):
            self.skipTest("file thu nghiem da ton tai truoc")
        with mock.patch.object(os, "open",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            with self.assertRaises(PB3Error) as ctx:
                safe_io.write_bytes_guarded(dest, b"hello")
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertFalse(os.path.lexists(str(dest)))

    def test_success_writes_file(self):
        if _wave_on():
            self.skipTest("sóng EPERM đang bật — bỏ qua test ghi đĩa thật")
        td = Path(tempfile.mkdtemp(prefix="awf-guard-"))
        self.addCleanup(shutil.rmtree, str(td), True)
        dest = td / "a.bin"
        self.assertEqual(safe_io.write_bytes_guarded(dest, b"hello"), dest)
        self.assertEqual(dest.read_bytes(), b"hello")
        self.assertEqual([p for p in td.iterdir() if ".pb3part-" in p.name], [])

    def test_eperm_preserves_old_file(self):
        if _wave_on():
            self.skipTest("sóng EPERM đang bật — bỏ qua test ghi đĩa thật")
        td = Path(tempfile.mkdtemp(prefix="awf-guard-"))
        self.addCleanup(shutil.rmtree, str(td), True)
        dest = td / "a.bin"
        dest.write_bytes(b"OLD")
        with mock.patch.object(os, "open",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            with self.assertRaises(PB3Error) as ctx:
                safe_io.write_bytes_guarded(dest, b"NEW")
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(dest.read_bytes(), b"OLD")


class TestSafeLogger(unittest.TestCase):
    """logger.py: redact token/secret truoc khi log; khong bao gio lo gia tri key."""

    def test_redact_github_pat(self):
        s = awlog.redact("key=github_pat_11ABCDEFG0abcDEF1234567890xyz")
        self.assertNotIn("github_pat_11ABCDEFG0abcDEF1234567890xyz", s)
        self.assertIn(awlog.REDACTED, s)

    def test_redact_kv_keeps_key_name(self):
        s = awlog.redact("token=SECRET123")
        self.assertIn("token=", s)
        self.assertIn(awlog.REDACTED, s)
        self.assertNotIn("SECRET123", s)

    def test_redact_bearer(self):
        s = awlog.redact("Bearer abc.def.ghi")
        self.assertIn("Bearer", s)
        self.assertIn(awlog.REDACTED, s)
        self.assertNotIn("abc.def.ghi", s)

    def test_redact_sk(self):
        self.assertEqual(awlog.redact("sk-abcdef123456"), awlog.REDACTED)

    def test_redact_leaves_normal_text(self):
        s = "hello world, branch feat/anti-wave-fallback, key cobonla (ten, khong phai gia tri)"
        self.assertEqual(awlog.redact(s), s)

    def test_redact_idempotent(self):
        s = "token=SECRET123 and github_pat_abc"
        self.assertEqual(awlog.redact(awlog.redact(s)), awlog.redact(s))

    def test_logger_filter_redacts_args(self):
        buf = io.StringIO()
        lg = logging.getLogger("awf-test-filter")
        lg.handlers = []
        lg.setLevel(logging.INFO)
        lg.propagate = False
        h = logging.StreamHandler(buf)
        h.addFilter(awlog.RedactingFilter())
        lg.addHandler(h)
        lg.info("luu checkpoint token=%s", "SECRET123")
        self.assertNotIn("SECRET123", buf.getvalue())
        self.assertIn(awlog.REDACTED, buf.getvalue())

    def test_get_logger_no_duplicate_handlers(self):
        lg1 = awlog.get_logger("awf-test-dup")
        lg2 = awlog.get_logger("awf-test-dup")
        self.assertIs(lg1, lg2)
        n = len([h for h in lg1.handlers if isinstance(h, logging.StreamHandler)])
        self.assertEqual(n, 1)

    def test_log_event_redacts_values(self):
        buf = io.StringIO()
        lg = logging.getLogger("awf-test-event")
        lg.handlers = []
        lg.setLevel(logging.INFO)
        lg.propagate = False
        h = logging.StreamHandler(buf)
        h.addFilter(awlog.RedactingFilter())
        lg.addHandler(h)
        awlog.log_event(lg, "wave_on", branch="feat/x", token="SECRET123")
        out = buf.getvalue()
        self.assertIn("wave_on", out)
        self.assertIn("branch=feat/x", out)
        self.assertNotIn("SECRET123", out)
        self.assertIn(awlog.REDACTED, out)


if __name__ == "__main__":
    unittest.main()
