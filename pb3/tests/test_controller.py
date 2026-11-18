"""Test AUTO controller Phase 5 (tick/run/guard_write/xuat checkpoint).

Phan lon test DUNG MOCK probe (unittest.mock / ham gia), khong ghi dia
that, khong sleep that (interval_s=0, checks nho) — chay duoc ngay ca
khi song EPERM dang bat. Rieng test ghi dia that probe truoc va SKIP
khi song bat, dung os.open truc tiep (KHONG dung tempfile — tempfile
chet trong song EPERM, xem anti_wave.healthcheck).
"""
import io
import logging
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import logger as awlog
from anti_wave.controller import Controller, NORMAL, RECOVERY, WAVE
from anti_wave.healthcheck import ProbeResult, WAVE_OFF, WAVE_ON, probe_wave
from core.errors import PB3Error
from core.safe_io import write_bytes_guarded


def _off():
    return ProbeResult(WAVE_OFF)


def _on():
    return ProbeResult(WAVE_ON)


class SeqProbe:
    """Probe gia: tra lan luot cac status trong list, het list thi tra cai cuoi."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self):
        idx = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        return ProbeResult(self.statuses[idx])


class FakeSync:
    """Sync gia: ghi lai checkpoint/patch duoc xuat (khong mang that)."""

    def __init__(self, fail=False):
        self.saved_checkpoints = []
        self.saved_patches = []
        self.fail = fail

    def save_checkpoint(self, agent_id, checkpoint, branch, **_kw):
        if self.fail:
            raise RuntimeError("mang gia loi")
        self.saved_checkpoints.append((agent_id, checkpoint, branch))
        return {"sha": "x" * 40}

    def save_patch(self, agent_id, patch_text, branch, **_kw):
        if self.fail:
            raise RuntimeError("mang gia loi")
        self.saved_patches.append((agent_id, patch_text, branch))
        return {"sha": "x" * 40}


def _ok_report():
    return {"status": "ok", "ready_for_normal": True, "detail": "dat"}


def _conflict_report():
    return {"status": "conflict", "ready_for_normal": False,
            "conflicts": [{"path": "a.txt"}],
            "detail": "xung dot 1 file"}


class TestTickNormalToWave(unittest.TestCase):
    def test_probe_on_goes_wave_and_blocks_writes(self):
        c = Controller(probe=SeqProbe([WAVE_OFF, WAVE_ON]), checks=2)
        self.assertEqual(c.tick(), NORMAL)
        self.assertFalse(c.writes_blocked)
        self.assertTrue(c.guard_write())
        self.assertEqual(c.tick(), WAVE)
        self.assertTrue(c.is_wave_on())
        self.assertTrue(c.writes_blocked)
        with self.assertRaises(PB3Error) as ctx:
            c.guard_write()
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(len(c.transitions), 1)
        self.assertEqual(c.transitions[0]["from"], NORMAL)
        self.assertEqual(c.transitions[0]["to"], WAVE)
        self.assertIn("sóng", c.status_summary().lower())

    def test_wave_enter_exports_checkpoint_and_patch(self):
        sync = FakeSync()
        cp = {"agent_id": "a1", "task_id": "t1"}
        c = Controller(probe=SeqProbe([WAVE_ON]), checks=2, sync=sync,
                       agent_id="a1", branch="feat/x", checkpoint=cp,
                       patch_text="--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-x\n+y\n")
        c.tick()
        self.assertEqual(c.state, WAVE)
        self.assertEqual(len(sync.saved_checkpoints), 1)
        self.assertEqual(sync.saved_checkpoints[0][0], "a1")
        self.assertEqual(sync.saved_checkpoints[0][2], "feat/x")
        self.assertEqual(len(sync.saved_patches), 1)

    def test_wave_enter_export_failure_does_not_raise(self):
        sync = FakeSync(fail=True)
        cp = {"agent_id": "a1", "task_id": "t1"}
        c = Controller(probe=SeqProbe([WAVE_ON]), checks=2, sync=sync,
                       agent_id="a1", branch="feat/x", checkpoint=cp,
                       patch_text="diff gia")
        c.tick()  # khong raise du sync loi
        self.assertEqual(c.state, WAVE)

    def test_wave_enter_without_sync_only_logs(self):
        c = Controller(probe=SeqProbe([WAVE_ON]), checks=2)
        c.tick()
        self.assertEqual(c.state, WAVE)

    def test_probe_raising_is_wave_on(self):
        def _boom():
            raise RuntimeError("probe chet")
        c = Controller(probe=_boom, checks=2)
        self.assertEqual(c.tick(), WAVE)  # fail-closed


class TestTickWaveStreak(unittest.TestCase):
    def test_enough_consecutive_calm_goes_recovery(self):
        c = Controller(probe=SeqProbe([WAVE_ON, WAVE_OFF, WAVE_OFF, WAVE_OFF]),
                       checks=3, recovery_runner=_ok_report)
        c.tick()
        self.assertEqual(c.state, WAVE)
        self.assertEqual(c.tick(), WAVE)  # streak 1
        self.assertEqual(c.tick(), WAVE)  # streak 2
        self.assertEqual(c.tick(), RECOVERY)  # streak 3 -> RECOVERY
        self.assertEqual(c.calm_streak, 3)

    def test_wave_on_resets_streak(self):
        c = Controller(probe=SeqProbe([WAVE_ON, WAVE_OFF, WAVE_ON,
                                       WAVE_OFF, WAVE_OFF, WAVE_OFF]),
                       checks=3, recovery_runner=_ok_report)
        c.tick()  # -> WAVE
        c.tick()  # streak 1
        c.tick()  # WAVE_ON -> streak 0, giu WAVE
        self.assertEqual(c.state, WAVE)
        self.assertEqual(c.calm_streak, 0)
        c.tick()  # streak 1
        c.tick()  # streak 2
        self.assertEqual(c.state, WAVE)
        c.tick()  # streak 3 -> RECOVERY
        self.assertEqual(c.state, RECOVERY)

    def test_not_enough_calm_stays_wave(self):
        c = Controller(probe=SeqProbe([WAVE_ON, WAVE_OFF, WAVE_OFF]),
                       checks=5, recovery_runner=_ok_report)
        c.tick()
        c.tick()
        c.tick()
        self.assertEqual(c.state, WAVE)
        self.assertEqual(c.calm_streak, 2)


class TestTickRecovery(unittest.TestCase):
    def _drive_to_recovery(self, runner, checks=2):
        c = Controller(probe=SeqProbe([WAVE_ON] + [WAVE_OFF] * 10),
                       checks=checks, recovery_runner=runner)
        c.tick()
        for _ in range(checks):
            c.tick()
        self.assertEqual(c.state, RECOVERY)
        return c

    def test_recovery_ok_then_normal(self):
        c = self._drive_to_recovery(_ok_report, checks=2)
        self.assertFalse(c.needs_master)
        self.assertEqual(c.tick(), NORMAL)  # probe lang + to_normal
        self.assertFalse(c.writes_blocked)
        self.assertIn("NORMAL", c.status_summary())

    def test_recovery_conflict_stays_and_needs_master(self):
        c = self._drive_to_recovery(_conflict_report, checks=2)
        self.assertTrue(c.needs_master)
        self.assertIn("MASTER", c.status_summary())
        self.assertEqual(c.tick(), RECOVERY)  # tick tiep van giu RECOVERY
        self.assertEqual(c.tick(), RECOVERY)
        with self.assertRaises(PB3Error) as ctx:
            c.guard_write()
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")

    def test_recovery_failed_runner_stays(self):
        def _boom():
            raise RuntimeError("recovery chet")
        c = self._drive_to_recovery(_boom, checks=2)
        self.assertTrue(c.needs_master)
        self.assertEqual(c.state, RECOVERY)
        self.assertEqual(c.tick(), RECOVERY)

    def test_recovery_without_runner_is_probe_only(self):
        c = self._drive_to_recovery(None, checks=2)
        self.assertFalse(c.needs_master)
        self.assertEqual(c.last_recovery_report["status"], "ok")
        self.assertEqual(c.tick(), NORMAL)

    def test_wave_back_in_recovery(self):
        c = Controller(probe=SeqProbe([WAVE_ON, WAVE_OFF, WAVE_OFF, WAVE_ON]),
                       checks=2, recovery_runner=_conflict_report)
        c.tick()  # -> WAVE
        c.tick()  # streak 1
        c.tick()  # -> RECOVERY (runner conflict)
        self.assertEqual(c.state, RECOVERY)
        self.assertTrue(c.needs_master)
        self.assertEqual(c.tick(), WAVE)  # song bat lai -> WAVE
        self.assertEqual(c.calm_streak, 0)
        self.assertFalse(c.needs_master)

    def test_recovery_object_with_run_method(self):
        class _Rec:
            def __init__(self):
                self.ran = 0

            def run(self):
                self.ran += 1
                return _ok_report()

        rec = _Rec()
        c = self._drive_to_recovery(rec, checks=2)
        self.assertEqual(rec.ran, 1)
        self.assertEqual(c.tick(), NORMAL)


class TestRunLoop(unittest.TestCase):
    def test_run_full_cycle(self):
        probe = SeqProbe([WAVE_ON, WAVE_OFF, WAVE_OFF, WAVE_OFF, WAVE_OFF])
        c = Controller(probe=probe, checks=2, recovery_runner=_ok_report)
        final = c.run(cycles=5, interval_s=0)
        self.assertEqual(final, NORMAL)
        self.assertEqual(probe.calls, 5 + 1)  # 5 tick + 1 probe trong to_normal
        self.assertEqual(len(c.transitions), 3)  # NORMAL->WAVE->RECOVERY->NORMAL

    def test_run_respects_cycles(self):
        probe = SeqProbe([WAVE_OFF] * 10)
        c = Controller(probe=probe, checks=2)
        c.run(cycles=3, interval_s=0)
        self.assertEqual(probe.calls, 3)
        self.assertEqual(c.state, NORMAL)

    def test_run_negative_interval_raises(self):
        c = Controller(probe=_off, checks=2)
        with self.assertRaises(ValueError):
            c.run(cycles=1, interval_s=-1)

    def test_illegal_transition_still_raises(self):
        c = Controller(probe=_off, checks=2)
        with self.assertRaises(PB3Error):
            c.to_recovery(checks=1, interval_s=0)  # NORMAL -> RECOVERY
        with self.assertRaises(PB3Error):
            c.to_normal()  # NORMAL -> NORMAL


class TestControllerLogging(unittest.TestCase):
    def test_transition_reason_token_is_redacted(self):
        c = Controller(probe=_off, checks=2)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(awlog.RedactingFilter())
        lg = c._logger
        lg.addHandler(handler)
        try:
            c.to_wave("ly do co token ghp_faketoken1234567890abcdef")
        finally:
            lg.removeHandler(handler)
        out = stream.getvalue()
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("ghp_faketoken", out)

    def test_constructor_bad_args_raise(self):
        with self.assertRaises(ValueError):
            Controller(probe=_off, checks=0)
        with self.assertRaises(ValueError):
            Controller(probe=_off, interval_s=-1)


class TestControllerDiskProbeSkip(unittest.TestCase):
    def test_write_guarded_probe_skip_when_wave(self):
        """Ghi dia that chi khi probe lang; song bat thi SKIP (khong chet)."""
        res = probe_wave()
        if res.status != WAVE_OFF:
            self.skipTest("song EPERM dang bat — bo qua test ghi dia")
        tmpdir = os.environ.get("TMPDIR") or "/tmp"
        d = os.path.join(tmpdir, "aw-ctrl-test-%d" % os.getpid())
        os.makedirs(d, exist_ok=True)
        try:
            target = os.path.join(d, "a.txt")
            write_bytes_guarded(target, b"hello-controller")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), b"hello-controller")
        finally:
            shutil.rmtree(d, ignore_errors=True)
