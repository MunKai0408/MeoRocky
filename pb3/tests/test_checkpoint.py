"""Test checkpoint JSON v1 (Anti-Wave, Phase 2).

Test chi dung MOCK hoac BO QUA khi song bat — khong bao gio ghi dia that
trong song (tempfile chet trong song EPERM [DA DO THAT]; probe_wave()
khong dung tempfile nen dung duoc de quyet dinh skip TRUOC khi goi
tempfile). Test ghi dia that chi chay khi probe WAVE_OFF.
"""
import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import checkpoint as cp
from anti_wave.healthcheck import WAVE_ON, probe_wave
from core.errors import PB3Error


def _wave_on() -> bool:
    """True neu probe thay song EPERM (chi doc, khong ghi, khong tempfile)."""
    try:
        return probe_wave().status == WAVE_ON
    except Exception:
        return False


def _sample(**over):
    kw = dict(agent_id="agent-1", task_id="phase2", branch="feat/anti-wave-fallback",
              base_commit_sha="a5ec6400" + "0" * 32,
              changed_files=[{"path": "anti_wave/checkpoint.py",
                              "sha256": "a" * 64, "bytes": 123}],
              patch_or_content="patch-diff...",
              test_status="new-tests 3/3 pass")
    kw.update(over)
    return cp.build_checkpoint(**kw)


class TestBuildCheckpoint(unittest.TestCase):
    def test_all_ten_fields_present(self):
        c = _sample(current_commit_sha="6eb53c9e" + "0" * 32)
        for f in ("agent_id", "task_id", "branch", "base_commit_sha",
                  "current_commit_sha", "changed_files", "patch_or_content",
                  "test_status", "created_at", "checksum"):
            self.assertIn(f, c, f)
        self.assertEqual(c["schema_version"], cp.CHECKPOINT_VERSION)
        self.assertEqual(c["checksum"], cp.checksum_of(c))

    def test_current_commit_sha_optional(self):
        c = _sample()
        self.assertNotIn("current_commit_sha", c)
        # Van validate duoc: loads lai qua dumps.
        self.assertEqual(cp.loads_checkpoint(cp.dumps_checkpoint(c)), c)

    def test_missing_identity_raises(self):
        for kw in ({"agent_id": ""}, {"task_id": ""}, {"branch": ""},
                   {"base_commit_sha": ""}):
            with self.assertRaises(PB3Error) as ctx:
                _sample(**kw)
            self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_changed_files_list_copied(self):
        files = [{"path": "a.py", "sha256": "b" * 64, "bytes": 1}]
        c = cp.build_checkpoint("a", "t", "b", "c" * 40, files, "p", "ok")
        files.append({"path": "b.py", "sha256": "c" * 64, "bytes": 2})
        self.assertEqual(len(c["changed_files"]), 1)

    def test_created_at_default_is_iso_utc(self):
        c = _sample()
        self.assertTrue(c["created_at"].endswith("Z"), c["created_at"])
        self.assertIn("T", c["created_at"])


class TestDumpsLoads(unittest.TestCase):
    def test_round_trip_memory_only(self):
        c = _sample(current_commit_sha="f" * 40, step="verify", next_action="push")
        self.assertEqual(cp.loads_checkpoint(cp.dumps_checkpoint(c)), c)

    def test_tampered_checksum_rejected(self):
        c = _sample()
        data = json.loads(cp.dumps_checkpoint(c).decode("utf-8"))
        data["test_status"] = "doi trang thai"
        with self.assertRaises(PB3Error) as ctx:
            cp.loads_checkpoint(json.dumps(data).encode("utf-8"))
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_missing_field_rejected(self):
        c = _sample()
        del c["branch"]
        c["checksum"] = cp.checksum_of(c)  # checksum dung nhung thieu truong
        with self.assertRaises(PB3Error) as ctx:
            cp.loads_checkpoint(cp.dumps_checkpoint(c))
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_wrong_schema_rejected(self):
        c = _sample()
        c["schema_version"] = 99
        with self.assertRaises(PB3Error) as ctx:
            cp.loads_checkpoint(cp.dumps_checkpoint(c))
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_changed_files_entry_invalid(self):
        c = _sample(changed_files=[{"path": "a.py", "sha256": "b" * 64}])
        with self.assertRaises(PB3Error) as ctx:
            cp.dumps_checkpoint(c)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_not_json_or_not_object_rejected(self):
        for bad in (b"khong phai json", b"[1, 2]", b'"chuoi"'):
            with self.assertRaises(PB3Error) as ctx:
                cp.loads_checkpoint(bad)
            self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_checksum_ignores_checksum_field(self):
        c = _sample()
        self.assertEqual(cp.checksum_of(c),
                         cp.checksum_of({k: v for k, v in c.items()
                                         if k != "checksum"}))


class TestSaveLoadReal(unittest.TestCase):
    """Test ghi dia that — CHI chay khi probe WAVE_OFF (song tat)."""

    def setUp(self):
        if _wave_on():
            self.skipTest("sóng EPERM đang bật — bỏ qua test ghi đĩa thật")
        self.td = Path(tempfile.mkdtemp(prefix="awf-ckpt-"))
        self.addCleanup(shutil.rmtree, str(self.td), True)

    def test_save_load_round_trip(self):
        dest = self.td / "agent-1.json"
        c = _sample(current_commit_sha="6eb53c9e" + "0" * 32)
        self.assertEqual(cp.save(dest, c), dest)
        self.assertEqual(cp.load(dest), c)
        # Khong con file tam (.pb3part-) sot lai.
        self.assertEqual([p for p in self.td.iterdir() if ".pb3part-" in p.name], [])

    def test_save_replaces_atomically(self):
        dest = self.td / "agent-1.json"
        cp.save(dest, _sample(test_status="chua test"))
        cp.save(dest, _sample(test_status="pass 3/3"))
        self.assertEqual(cp.load(dest)["test_status"], "pass 3/3")

    def test_load_tampered_file_rejected(self):
        dest = self.td / "agent-1.json"
        cp.save(dest, _sample())
        with open(dest, "ab") as f:
            f.write(b"garbage")
        with self.assertRaises(PB3Error) as ctx:
            cp.load(dest)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_save_eperm_preserves_old_file(self):
        """Ghi giua chung gap EPERM: file cu con nguyen, khong co file 0 byte."""
        dest = self.td / "agent-1.json"
        dest.write_bytes(b"OLD")
        with mock.patch.object(os, "open",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            with self.assertRaises(PB3Error) as ctx:
                cp.save(dest, _sample())
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(dest.read_bytes(), b"OLD")

    def test_resume_summary_vietnamese(self):
        c = _sample(current_commit_sha="6eb53c9e" + "0" * 32,
                    step="verify", next_action="day len Bear")
        s = cp.resume_summary(c)
        for needle in ("agent-1", "phase2", "feat/anti-wave-fallback",
                       "Commit hiện tại", "Bước hiện tại", "Việc tiếp theo"):
            self.assertIn(needle, s)
        self.assertNotIn("Patch/commit:", s)


class TestSaveWaveFail(unittest.TestCase):
    """Ghi gap EPERM (mock) — khong cham dia that, chay ca trong song."""

    def test_save_eperm_raises_e_write_wave_no_zero_byte(self):
        dest = Path("/tmp") / "awf-ckpt-khong-duoc-tao.json"
        if os.path.lexists(str(dest)):
            self.skipTest("file thu nghiem da ton tai truoc")
        with mock.patch.object(os, "open",
                               side_effect=OSError(errno.EPERM, "mock wave")):
            with self.assertRaises(PB3Error) as ctx:
                cp.save(dest, _sample())
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertFalse(os.path.lexists(str(dest)))


if __name__ == "__main__":
    unittest.main()
