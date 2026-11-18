"""Test anti_wave.recovery (Anti-Wave, Phase 4).

Test pure/mock chay ca luc song bat. Test ghi dia that chi chay khi
probe WAVE_OFF (tempfile chet trong song EPERM [DA DO THAT]; probe_wave()
khong dung tempfile nen dung duoc de quyet dinh skip TRUOC khi goi
tempfile). Mock EPERM bang unittest.mock — KHONG gay loi that tren VM.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import checkpoint as cp_mod
from anti_wave import patch_manager as pm
from anti_wave import recovery as rec
from anti_wave.healthcheck import WAVE_OFF, WAVE_ON, ProbeResult
from anti_wave.integrity import snapshot as snap_of
from core.errors import PB3Error

BASE_SHA = "0ee305d1" + "0" * 32
OTHER_SHA = "fc1c0a50" + "0" * 32


def _wave_on() -> bool:
    """True neu probe thay song EPERM (chi doc, khong ghi, khong tempfile)."""
    try:
        from anti_wave.healthcheck import probe_wave
        return probe_wave().status == WAVE_ON
    except Exception:
        return False


def _probe_off():
    return ProbeResult(WAVE_OFF, None, "ok", "", "")


def _probe_on():
    return ProbeResult(WAVE_ON, 1, "song", "", "")


class FakeSync:
    """Gia lap toi thieu GitHubSync cho recovery (head + file + checkpoint)."""

    def __init__(self, head=BASE_SHA, files=None, remote_checkpoint=None):
        self.head = head
        self.files = dict(files or {})  # rel_path -> bytes
        self.remote_checkpoint = remote_checkpoint

    def get_branch_head(self, branch):
        return self.head

    def get_file(self, branch, path):
        data = self.files.get(path)
        if data is None:
            return None
        return ("blob-x", data)

    def load_checkpoint(self, agent_id, branch):
        if self.remote_checkpoint is None:
            raise PB3Error("E_CORRUPT", "chua co checkpoint cho %s" % agent_id)
        return self.remote_checkpoint


def _sample_checkpoint(**over):
    kw = dict(agent_id="agent-1", task_id="phase4", branch="feat/x",
              base_commit_sha=BASE_SHA,
              changed_files=[{"path": "a.txt", "sha256": "a" * 64, "bytes": 3}],
              patch_or_content="patch", test_status="ok")
    kw.update(over)
    return cp_mod.build_checkpoint(**kw)


class TestVerifyCalm(unittest.TestCase):
    def test_all_off_true(self):
        calls = []

        def probe():
            calls.append(1)
            return _probe_off()

        self.assertTrue(rec.verify_calm(checks=3, interval_s=0, probe=probe))
        self.assertEqual(len(calls), 3)

    def test_one_on_false(self):
        seq = [_probe_on(), _probe_off(), _probe_off()]

        def probe():
            return seq.pop(0)

        self.assertFalse(rec.verify_calm(checks=3, interval_s=0, probe=probe))

    def test_probe_raises_false(self):
        def probe():
            raise OSError("probe chet")

        self.assertFalse(rec.verify_calm(checks=2, interval_s=0, probe=probe))

    def test_checks_must_be_positive(self):
        with self.assertRaises(ValueError):
            rec.Recovery(".", checks=0)


class TestCheckConflicts(unittest.TestCase):
    def test_no_overlap(self):
        local = [{"path": "a.txt", "sha256": "a" * 64}]
        remote = [{"path": "b.txt", "sha256": "b" * 64}]
        self.assertEqual(rec.check_conflicts(local, remote), [])

    def test_same_sha_no_conflict(self):
        f = [{"path": "a.txt", "sha256": "a" * 64}]
        self.assertEqual(rec.check_conflicts(f, f), [])

    def test_different_sha_conflict(self):
        local = [{"path": "a.txt", "sha256": "a" * 64}]
        remote = [{"path": "a.txt", "sha256": "b" * 64}]
        got = rec.check_conflicts(local, remote)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["path"], "a.txt")
        self.assertEqual(got[0]["local_sha256"], "a" * 64)
        self.assertEqual(got[0]["remote_sha256"], "b" * 64)

    def test_missing_sha_no_conflict(self):
        local = [{"path": "a.txt", "sha256": None}]
        remote = [{"path": "a.txt", "sha256": "b" * 64}]
        self.assertEqual(rec.check_conflicts(local, remote), [])


class TestSummaryAndReady(unittest.TestCase):
    def test_ready_only_when_ok(self):
        ok = {"status": "ok", "ready_for_normal": True, "steps": []}
        bad = {"status": "failed", "ready_for_normal": False, "steps": []}
        self.assertTrue(rec.ready_for_normal(ok))
        self.assertFalse(rec.ready_for_normal(bad))
        self.assertFalse(rec.ready_for_normal({"status": "ok"}))

    def test_summary_vietnamese(self):
        report = {"status": "ok", "ready_for_normal": True,
                  "steps": [{"step": 1, "name": "xac-nhan-song-tat",
                             "status": "ok", "detail": "5 lan probe"}],
                  "conflicts": [], "unrecoverable": [], "restored": ["a.txt"]}
        text = rec.recovery_summary(report)
        self.assertIn("ĐẠT", text)
        self.assertIn("xac-nhan-song-tat", text)
        self.assertIn("a.txt", text)


class TestRecoveryRunDisk(unittest.TestCase):
    """Test chay quy trinh that tren thu muc tam — skip khi song bat."""

    def setUp(self):
        if _wave_on():
            self.skipTest("song EPERM dang bat — bo qua test ghi dia")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, files):
        for rel, data in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def _snapshot(self, *rels):
        return snap_of([self.root / r for r in rels])

    def _recovery(self, **kw):
        kw.setdefault("probe", _probe_off)
        kw.setdefault("checks", 2)
        kw.setdefault("interval_s", 0)
        return rec.Recovery(self.root, **kw)

    def test_happy_path_offline(self):
        self._write({"a.txt": b"l1\n"})
        snap = self._snapshot("a.txt")
        cp = _sample_checkpoint()
        report = self._recovery(agent_id="agent-1", branch="feat/x").run(
            checkpoint=cp, snapshot=snap)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(rec.ready_for_normal(report))
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(len(report["steps"]), 12)
        self.assertEqual(by_step[1]["status"], "ok")
        self.assertEqual(by_step[5]["status"], "skipped")  # khong co sync
        self.assertEqual(by_step[6]["status"], "skipped")
        self.assertEqual(by_step[9]["status"], "skipped")  # khong patch/sync
        self.assertEqual(by_step[11]["status"], "ok")
        # Buoc 11 da ghi checkpoint khoi phuc.
        saved = list((self.root / "anti_wave" / "checkpoints").glob("recovery-*.json"))
        self.assertEqual(len(saved), 1)
        loaded = json.loads(saved[0].read_text(encoding="utf-8"))
        self.assertEqual(loaded["task_id"], "phase4-recovery")

    def test_wave_on_at_step1_stops(self):
        self._write({"a.txt": b"l1\n"})
        report = self._recovery(probe=_probe_on).run()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(rec.ready_for_normal(report))
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[1]["status"], "failed")
        self.assertIn("E_WRITE_WAVE", by_step[1]["detail"])
        for n in range(2, 13):
            self.assertEqual(by_step[n]["status"], "skipped")

    def test_zero_byte_restored_from_good_copy(self):
        orig = b"l1\nl2\n"
        self._write({"a.txt": orig})
        snap = self._snapshot("a.txt")
        (self.root / "a.txt").write_bytes(b"")  # song cat file ve 0 byte
        report = self._recovery(good_copies={"a.txt": orig}).run(snapshot=snap)
        self.assertEqual(report["status"], "ok")
        self.assertEqual((self.root / "a.txt").read_bytes(), orig)
        self.assertEqual(report["restored"], ["a.txt"])
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertIn("1 file 0 byte", by_step[2]["detail"])

    def test_zero_byte_no_good_copy_fails(self):
        self._write({"a.txt": b"l1\n"})
        snap = self._snapshot("a.txt")
        (self.root / "a.txt").write_bytes(b"")
        report = self._recovery().run(snapshot=snap)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unrecoverable"], ["a.txt"])
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[8]["status"], "failed")
        for n in range(9, 13):
            self.assertEqual(by_step[n]["status"], "skipped")

    def test_branch_head_mismatch_conflict(self):
        self._write({"a.txt": b"l1\n"})
        sync = FakeSync(head=OTHER_SHA)
        report = self._recovery(sync=sync, branch="feat/x",
                                agent_id="agent-1").run(
            checkpoint=_sample_checkpoint())
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(len(report["conflicts"]), 1)
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[5]["status"], "conflict")
        for n in range(6, 13):
            self.assertEqual(by_step[n]["status"], "skipped")

    def test_file_conflict_both_sides(self):
        self._write({"a.txt": b"l1\nLOCAL\n"})
        snap = self._snapshot("a.txt")
        # Dia hien tai khac snapshot (doi local); remote doi khac nua.
        remote_cp = _sample_checkpoint(
            changed_files=[{"path": "a.txt", "sha256": "c" * 64, "bytes": 9}])
        sync = FakeSync(head=BASE_SHA, remote_checkpoint=remote_cp)
        report = self._recovery(sync=sync, branch="feat/x",
                                agent_id="agent-1").run(
            checkpoint=_sample_checkpoint(), snapshot=snap)
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(report["conflicts"][0]["path"], "a.txt")
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[5]["status"], "ok")  # head khop
        self.assertEqual(by_step[6]["status"], "conflict")

    def test_patch_applied_at_step9(self):
        base = {"a.txt": b"l1\nl2\n"}
        new = {"a.txt": b"l1\nL2\n"}
        self._write(base)
        snap = self._snapshot("a.txt")
        patch = pm.build_patch(base, new, BASE_SHA)
        report = self._recovery().run(snapshot=snap, patch=patch)
        self.assertEqual(report["status"], "ok")
        self.assertEqual((self.root / "a.txt").read_bytes(), b"l1\nL2\n")
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[9]["status"], "ok")
        self.assertEqual(by_step[10]["status"], "ok")

    def test_damaged_checkpoint_fails_step3(self):
        bad = self.root / "cp.json"
        bad.write_text("{khong phai json", encoding="utf-8")
        report = self._recovery(checkpoint_path=bad).run()
        self.assertEqual(report["status"], "failed")
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[3]["status"], "failed")
        for n in range(4, 13):
            self.assertEqual(by_step[n]["status"], "skipped")

    def test_restore_eperm_mock(self):
        orig = b"l1\n"
        self._write({"a.txt": orig})
        snap = self._snapshot("a.txt")
        (self.root / "a.txt").write_bytes(b"")
        err = PB3Error("E_WRITE_WAVE", "ghi bi chan (EPERM)")
        with mock.patch("anti_wave.recovery.write_bytes_guarded",
                        side_effect=err):
            report = self._recovery(good_copies={"a.txt": orig}).run(snapshot=snap)
        self.assertEqual(report["status"], "failed")
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[8]["status"], "failed")
        self.assertIn("E_WRITE_WAVE", by_step[8]["detail"])

    def test_sync_download_missing_file(self):
        # File co trong snapshot nhung mat tren dia; sync co ban tot.
        self._write({"a.txt": b"l1\n"})
        snap = self._snapshot("a.txt")
        (self.root / "a.txt").unlink()
        sync = FakeSync(head=BASE_SHA, files={"a.txt": b"l1\n"})
        report = self._recovery(sync=sync, branch="feat/x",
                                agent_id="agent-1").run(
            checkpoint=_sample_checkpoint(), snapshot=snap)
        self.assertEqual(report["status"], "ok")
        self.assertEqual((self.root / "a.txt").read_bytes(), b"l1\n")
        by_step = {s["step"]: s for s in report["steps"]}
        self.assertEqual(by_step[9]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
