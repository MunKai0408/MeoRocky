"""Test mo phong song EPERM Phase 6 (anti_wave/simulation.py).

Phan lon test DUNG MOCK / fault-injection trong sandbox rieng, khong
ghi that ra ngoai sandbox, khong sleep that (interval_s=0, probe gia) —
chay duoc ngay ca khi song EPERM dang bat. Rieng test cham dia that
probe truoc va SKIP khi song bat, dung os.open/os.makedirs truc tiep
(KHONG dung tempfile — tempfile chet trong song EPERM, xem
anti_wave.healthcheck).

[MÔ PHỎNG]: moi ket qua o day deu tu probe gia + sync gia + WaveFault
(os.write/os.fsync bi patch trong sandbox) — khong mang that, khong loi
that tren VM. Bang chung dispatch THAT da co tu Phase 3 / PR #30.
"""
import errno
import os
import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave.github_sync import SyncError
from anti_wave.healthcheck import ProbeResult, WAVE_OFF, WAVE_ON, probe_wave
from anti_wave.integrity import classify_change, file_record
from anti_wave.simulation import (
    BUILTIN_SCENARIOS,
    DEFAULT_CHANGES,
    DEFAULT_INITIAL_FILES,
    FakeGitHubSync,
    SimulatedProbe,
    WaveFault,
    run_all_scenarios,
    run_wave_scenario,
    simulation_report_summary,
    simulation_summary,
    truncate_by_failed_write,
)
from core.errors import PB3Error
from core.safe_io import AtomicWriter, write_bytes_guarded

BASE_SHA = "e1cc72965435ff214e876d3a122c206a125ba474"  # main sau PR #38


def _scenario(name):
    for sc in BUILTIN_SCENARIOS:
        if sc["name"] == name:
            return sc
    raise KeyError(name)


def _step(report, n):
    for s in (report.get("recovery") or {}).get("steps", []):
        if s["step"] == n:
            return s
    return None


class SimRootMixin:
    """Sandbox dia rieng cho moi test: probe-skip khi song bat."""

    def setUp(self):
        res = probe_wave()
        if res.status != WAVE_OFF:
            self.skipTest("song EPERM dang bat — bo qua test ghi dia")
        tmpdir = os.environ.get("TMPDIR") or "/tmp"
        self.root = Path(tmpdir) / ("aw-sim-%d-%s" % (os.getpid(), self._testMethodName))
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.root), True)

    def _write(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p


class TestWaveFault(SimRootMixin, unittest.TestCase):
    def test_write_under_root_blocked_outside_allowed(self):
        inside = self._write("a.txt", b"hello")
        outside = Path(os.environ.get("TMPDIR") or "/tmp") / (
            "aw-sim-outside-%d.txt" % os.getpid())
        try:
            with WaveFault(self.root) as fault:
                fd = os.open(str(inside), os.O_WRONLY)
                try:
                    with self.assertRaises(OSError) as ctx:
                        os.write(fd, b"x")
                    self.assertEqual(ctx.exception.errno, errno.EPERM)
                finally:
                    os.close(fd)
                # Ghi ra ngoai root van duoc (fault co pham vi).
                fd2 = os.open(str(outside), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
                try:
                    self.assertEqual(os.write(fd2, b"ok"), 2)
                finally:
                    os.close(fd2)
                self.assertGreaterEqual(fault.blocked_writes, 1)
            self.assertEqual(outside.read_bytes(), b"ok")
        finally:
            try:
                os.unlink(str(outside))
            except OSError:
                pass

    def test_fsync_under_root_blocked(self):
        p = self._write("a.txt", b"hello")
        with WaveFault(self.root) as fault:
            fd = os.open(str(p), os.O_RDONLY)
            try:
                with self.assertRaises(OSError) as ctx:
                    os.fsync(fd)
                self.assertEqual(ctx.exception.errno, errno.EPERM)
            finally:
                os.close(fd)
            self.assertGreaterEqual(fault.blocked_fsyncs, 1)

    def test_fault_restores_os_write_even_on_exception(self):
        orig_write = os.write
        orig_fsync = os.fsync
        with self.assertRaises(RuntimeError):
            with WaveFault(self.root):
                raise RuntimeError("chet trong fault")
        self.assertIs(os.write, orig_write)
        self.assertIs(os.fsync, orig_fsync)

    def test_nested_fault_raises(self):
        with WaveFault(self.root):
            with self.assertRaises(RuntimeError):
                with WaveFault(self.root):
                    pass

    def test_truncate_by_failed_write_leaves_zero_byte(self):
        p = self._write("work/c.txt", b"se bi cat 0 byte\n")
        with WaveFault(self.root):
            size = truncate_by_failed_write(p, b"du lieu se mat")
        self.assertEqual(size, 0)
        self.assertEqual(p.stat().st_size, 0)
        rec = file_record(p)
        self.assertEqual(
            classify_change({"sha256": "x" * 64, "bytes": 17, "exists": True}, rec),
            "zero_byte")

    def test_guarded_write_fails_fast_no_file(self):
        dest = self.root / "work.txt"
        with WaveFault(self.root):
            with self.assertRaises(PB3Error) as ctx:
                write_bytes_guarded(dest, b"du lieu trong song")
            self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertFalse(dest.exists())

    def test_atomic_writer_fails_at_fsync_no_dest_no_temp(self):
        dest = self.root / "work2.txt"
        with WaveFault(self.root):
            with self.assertRaises(PB3Error) as ctx:
                with AtomicWriter(dest, expected_size=5) as w:
                    w.write(b"hello")
            self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertFalse(dest.exists())
        leftovers = [p for p in self.root.iterdir()
                     if "pb3part-" in p.name]
        self.assertEqual(leftovers, [])


class TestFakeGitHubSync(unittest.TestCase):
    def _checkpoint(self):
        from anti_wave.checkpoint import build_checkpoint
        return build_checkpoint(agent_id="a1", task_id="t1", branch="feat/x",
                                base_commit_sha=BASE_SHA, changed_files=[],
                                patch_or_content="khong co",
                                test_status="chua test")

    def test_save_load_checkpoint_roundtrip(self):
        sync = FakeGitHubSync(BASE_SHA)
        cp = self._checkpoint()
        res = sync.save_checkpoint("a1", cp, "feat/x")
        self.assertEqual(res["sha256"], res["blob_sha"])
        back = sync.load_checkpoint("a1", "feat/x")
        self.assertEqual(back["checksum"], cp["checksum"])
        self.assertEqual(sync.list_checkpoints("feat/x"), ["a1"])

    def test_save_checkpoint_conflict_when_head_moved(self):
        sync = FakeGitHubSync(BASE_SHA)
        sync.move_head("feat/x", "f" * 40)
        with self.assertRaises(SyncError) as ctx:
            sync.save_checkpoint("a1", self._checkpoint(), "feat/x",
                                 expected_base_sha=BASE_SHA)
        self.assertEqual(ctx.exception.code, "E_CONFLICT")

    def test_load_checkpoint_missing_raises(self):
        sync = FakeGitHubSync(BASE_SHA)
        with self.assertRaises(SyncError) as ctx:
            sync.load_checkpoint("khong-co", "feat/x")
        self.assertEqual(ctx.exception.code, "E_NOT_FOUND")

    def test_save_patch_rejects_secret(self):
        sync = FakeGitHubSync(BASE_SHA)
        bad = ("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
               "@@ -1 +1 @@\n-x\n+ghp_faketoken1234567890abcdef\n")
        with self.assertRaises(SyncError) as ctx:
            sync.save_patch("a1", bad, "feat/x")
        self.assertEqual(ctx.exception.code, "E_VALIDATION")

    def test_dispatch_fake_records_run_and_failset(self):
        sync = FakeGitHubSync(BASE_SHA)
        self.assertTrue(sync.dispatch_workflow(
            "anti-wave-ci.yml", ref="feat/x",
            inputs={"ref": "feat/x", "scope": "unit"}))
        runs = sync.list_workflow_runs("anti-wave-ci.yml")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "completed")
        self.assertEqual(runs[0]["conclusion"], "success")
        self.assertTrue(runs[0]["fake"])
        arts = sync.list_run_artifacts(runs[0]["id"])
        self.assertEqual([a["name"] for a in arts], ["failset.json"])
        failset = sync.fake_failset()
        self.assertEqual(failset["tests"], 3)
        self.assertEqual(failset["failures"], 0)
        self.assertEqual(failset["failed"], [])


class TestSimulatedProbe(unittest.TestCase):
    def test_status_switch_and_call_count(self):
        probe = SimulatedProbe(WAVE_ON)
        self.assertEqual(probe().status, WAVE_ON)
        probe.status = WAVE_OFF
        self.assertEqual(probe().status, WAVE_OFF)
        self.assertEqual(probe.calls, 2)


class TestWaveScenarios(SimRootMixin, unittest.TestCase):
    def test_happy_path(self):
        rep = run_wave_scenario(_scenario("happy-path"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertTrue(rep["fake"])
        self.assertEqual(rep["final_state"], "NORMAL")
        self.assertFalse(rep["needs_master"])
        self.assertEqual([(t["from"], t["to"]) for t in rep["transitions"]],
                         [("NORMAL", "WAVE"), ("WAVE", "RECOVERY"),
                          ("RECOVERY", "NORMAL")])
        self.assertEqual(rep["wave"]["guard_code"], "E_WRITE_WAVE")
        self.assertTrue(rep["wave"]["guarded_write_failed"])
        self.assertTrue(rep["wave"]["guarded_no_file"])
        self.assertTrue(rep["wave"]["atomic_failed"])
        self.assertTrue(rep["wave"]["atomic_no_file"])
        self.assertEqual(rep["wave"]["truncated_by_wave"], ["work/c.txt"])
        self.assertGreaterEqual(rep["wave"]["blocked_writes"], 1)
        self.assertTrue(rep["export"]["checkpoint"])
        self.assertTrue(rep["export"]["patch"])
        self.assertTrue(rep["actions"]["fake"])
        self.assertEqual(rep["actions"]["conclusion"], "success")
        self.assertEqual(rep["actions"]["failset"]["failures"], 0)
        rec = rep["recovery"]
        self.assertEqual(rec["status"], "ok")
        self.assertTrue(all(s["status"] == "ok" for s in rec["steps"]))
        self.assertEqual(len(rec["steps"]), 12)
        self.assertEqual(rep["integrity"]["integrity_ok"], True)
        self.assertEqual(rep["integrity"]["bad"], [])
        # File cuoi dung noi dung patch (khong phai ban dau).
        self.assertEqual((self.root / "notes/a.txt").read_bytes(),
                         DEFAULT_CHANGES["notes/a.txt"])
        self.assertEqual((self.root / "work/c.txt").read_bytes(),
                         DEFAULT_INITIAL_FILES["work/c.txt"])
        self.assertTrue((self.root / "notes/d.txt").exists())

    def test_head_moved_conflict_stops_at_step5(self):
        rep = run_wave_scenario(_scenario("head-moved"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "RECOVERY")
        self.assertTrue(rep["needs_master"])
        self.assertEqual(rep["recovery"]["status"], "conflict")
        self.assertEqual(_step(rep, 5)["status"], "conflict")
        self.assertEqual(_step(rep, 6)["status"], "skipped")
        self.assertEqual(rep["recovery"]["conflicts"][0]["path"],
                         "<branch feat/anti-wave-phase6-sim>")

    def test_remote_checkpoint_conflict_stops_at_step6(self):
        rep = run_wave_scenario(_scenario("remote-checkpoint-conflict"),
                                self.root, base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "RECOVERY")
        self.assertTrue(rep["needs_master"])
        self.assertEqual(rep["recovery"]["status"], "conflict")
        self.assertEqual(_step(rep, 5)["status"], "ok")
        self.assertEqual(_step(rep, 6)["status"], "conflict")
        self.assertEqual(rep["recovery"]["conflicts"][0]["path"], "notes/a.txt")

    def test_bad_patch_fails_at_step9(self):
        rep = run_wave_scenario(_scenario("bad-patch"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "RECOVERY")
        self.assertTrue(rep["needs_master"])
        self.assertEqual(rep["recovery"]["status"], "failed")
        self.assertEqual(_step(rep, 9)["status"], "failed")
        self.assertIn("E_CORRUPT", _step(rep, 9)["detail"])

    def test_zero_byte_no_copy_fails_at_step8(self):
        rep = run_wave_scenario(_scenario("zero-byte-no-copy"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "RECOVERY")
        self.assertTrue(rep["needs_master"])
        self.assertEqual(rep["recovery"]["status"], "failed")
        self.assertEqual(_step(rep, 8)["status"], "failed")
        self.assertEqual(rep["recovery"]["unrecoverable"], ["work/c.txt"])

    def test_wave_back_recovery_runs_twice_then_normal(self):
        rep = run_wave_scenario(_scenario("wave-back"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "NORMAL")
        self.assertFalse(rep["needs_master"])
        self.assertEqual(rep["recovery_runs"], 2)
        self.assertEqual([(t["from"], t["to"]) for t in rep["transitions"]],
                         [("NORMAL", "WAVE"), ("WAVE", "RECOVERY"),
                          ("RECOVERY", "WAVE"), ("WAVE", "RECOVERY"),
                          ("RECOVERY", "NORMAL")])
        self.assertEqual(rep["integrity"]["integrity_ok"], True)

    def test_delete_file_scenario(self):
        rep = run_wave_scenario(_scenario("delete-file"), self.root,
                                base_commit_sha=BASE_SHA)
        self.assertEqual(rep["verdict"], "PASS", rep["reasons"])
        self.assertEqual(rep["final_state"], "NORMAL")
        self.assertFalse((self.root / "notes/b.txt").exists())
        self.assertEqual((self.root / "notes/a.txt").read_bytes(),
                         DEFAULT_CHANGES["notes/a.txt"])
        self.assertTrue((self.root / "notes/d.txt").exists())

    def test_run_all_scenarios(self):
        reps = run_all_scenarios(self.root, base_commit_sha=BASE_SHA)
        self.assertEqual(len(reps), len(BUILTIN_SCENARIOS))
        self.assertEqual([r["scenario"] for r in reps],
                         [s["name"] for s in BUILTIN_SCENARIOS])
        failed = [r for r in reps if r["verdict"] != "PASS"]
        self.assertEqual(failed, [])

    def test_simulation_summary_vietnamese(self):
        rep = run_wave_scenario(_scenario("happy-path"), self.root,
                                base_commit_sha=BASE_SHA)
        text = simulation_summary(rep)
        self.assertIn("Mô phỏng sóng EPERM", text)
        self.assertIn("NORMAL", text)
        self.assertIn("RECOVERY", text)
        self.assertIn("GIẢ LẬP", text)
        all_text = simulation_report_summary(
            run_all_scenarios(self.root / "all", base_commit_sha=BASE_SHA))
        self.assertIn("7/7 kịch bản PASS", all_text)


if __name__ == "__main__":
    unittest.main()
