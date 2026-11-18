"""Test anti_wave.patch_manager (Anti-Wave, Phase 4).

Test pure/mock chay ca luc song bat. Test ghi dia that chi chay khi
probe WAVE_OFF (tempfile chet trong song EPERM [DA DO THAT]; probe_wave()
khong dung tempfile nen dung duoc de quyet dinh skip TRUOC khi goi
tempfile). Mock EPERM bang unittest.mock — KHONG gay loi that tren VM.
"""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import helpers  # noqa: F401  (them pb3/ vao sys.path)
from anti_wave import patch_manager as pm
from anti_wave.github_sync import PATCH_MAX_BYTES
from anti_wave.healthcheck import WAVE_ON, probe_wave
from core.errors import PB3Error

BASE_SHA = "0ee305d1" + "0" * 32
OTHER_SHA = "fc1c0a50" + "0" * 32


def _wave_on() -> bool:
    """True neu probe thay song EPERM (chi doc, khong ghi, khong tempfile)."""
    try:
        return probe_wave().status == WAVE_ON
    except Exception:
        return False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestBuildPatch(unittest.TestCase):
    def test_no_changes_empty_patch(self):
        files = {"a.txt": b"hello\n"}
        p = pm.build_patch(files, dict(files), BASE_SHA)
        self.assertEqual(p["files"], [])
        self.assertEqual(p["patch_text"], "")
        self.assertEqual(p["base_commit_sha"], BASE_SHA)
        self.assertEqual(p["checksum"], pm.patch_checksum(p))

    def test_added_file(self):
        p = pm.build_patch({}, {"a.txt": b"hello\n"}, BASE_SHA, test_status="ok")
        self.assertEqual(len(p["files"]), 1)
        meta = p["files"][0]
        self.assertEqual(meta["action"], "added")
        self.assertEqual(meta["sha256"], _sha(b"hello\n"))
        self.assertEqual(meta["bytes"], 6)
        self.assertIn("diff --git a/a.txt b/a.txt", p["patch_text"])
        self.assertIn("+hello", p["patch_text"])
        self.assertEqual(pm.validate_patch(p), [])

    def test_modified_file(self):
        p = pm.build_patch({"a.txt": b"l1\nl2\nl3\n"},
                           {"a.txt": b"l1\nL2\nl3\n"}, BASE_SHA)
        self.assertEqual(p["files"][0]["action"], "modified")
        self.assertIn("-l2", p["patch_text"])
        self.assertIn("+L2", p["patch_text"])
        self.assertEqual(pm.validate_patch(p), [])

    def test_deleted_file(self):
        p = pm.build_patch({"a.txt": b"bye\n"}, {"a.txt": None}, BASE_SHA)
        self.assertEqual(p["files"][0]["action"], "deleted")
        self.assertIsNone(p["files"][0]["sha256"])
        self.assertEqual(p["files"][0]["bytes"], 0)
        self.assertIn("+++ /dev/null", p["patch_text"])
        self.assertEqual(pm.validate_patch(p), [])

    def test_no_trailing_newline_marker(self):
        p = pm.build_patch({"a.txt": b"x\ny"}, {"a.txt": b"x\nz"}, BASE_SHA)
        self.assertIn("\\ No newline at end of file", p["patch_text"])
        self.assertEqual(pm.validate_patch(p), [])

    def test_non_utf8_raises(self):
        with self.assertRaises(PB3Error) as ctx:
            pm.build_patch({"a.txt": b"\xff\xfe"}, {"a.txt": b"ok\n"}, BASE_SHA)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_absolute_path_raises(self):
        with self.assertRaises(PB3Error) as ctx:
            pm.build_patch({}, {"/etc/passwd": b"x\n"}, BASE_SHA)
        self.assertEqual(ctx.exception.code, "E_UNSAFE_PATH")

    def test_dotdot_path_raises(self):
        with self.assertRaises(PB3Error) as ctx:
            pm.build_patch({}, {"../x.py": b"x\n"}, BASE_SHA)
        self.assertEqual(ctx.exception.code, "E_UNSAFE_PATH")

    def test_missing_base_sha_raises(self):
        with self.assertRaises(PB3Error) as ctx:
            pm.build_patch({}, {"a.txt": b"x\n"}, "")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_patch_too_large_raises(self):
        with mock.patch.object(pm, "PATCH_MAX_BYTES", 10):
            with self.assertRaises(PB3Error) as ctx:
                pm.build_patch({}, {"a.txt": b"0123456789ABCDEF\n"}, BASE_SHA)
            self.assertEqual(ctx.exception.code, "E_TOO_LARGE")

    def test_files_for_checkpoint_shape(self):
        p = pm.build_patch({"a.txt": b"old\n", "b.txt": b"bye\n"},
                           {"a.txt": b"new\n", "b.txt": None}, BASE_SHA)
        got = pm.files_for_checkpoint(p)
        self.assertEqual(len(got), 2)
        for entry in got:
            self.assertEqual(set(entry), {"path", "sha256", "bytes"})


class TestValidatePatch(unittest.TestCase):
    def _sample(self, **over):
        p = pm.build_patch({"a.txt": b"l1\nl2\n"}, {"a.txt": b"l1\nL2\n"}, BASE_SHA)
        p.update(over)
        return p

    def test_valid_patch_ok(self):
        self.assertEqual(pm.validate_patch(self._sample()), [])

    def test_tampered_patch_text_checksum(self):
        p = self._sample()
        p["patch_text"] = p["patch_text"].replace("L2", "L3")
        problems = pm.validate_patch(p)
        self.assertTrue(any("checksum" in x for x in problems), problems)

    def test_tampered_file_sha(self):
        p = self._sample()
        p["files"][0]["sha256"] = "0" * 64
        problems = pm.validate_patch(p)
        self.assertTrue(any("checksum" in x for x in problems), problems)

    def test_secret_in_patch(self):
        p = pm.build_patch({}, {"a.txt": b"key = 'ghp_" + b"A" * 30 + b"'\n"},
                           BASE_SHA)
        problems = pm.validate_patch(p)
        self.assertTrue(any("bi mat" in x for x in problems), problems)

    def test_empty_patch_text(self):
        p = self._sample(patch_text="")
        self.assertTrue(pm.validate_patch(p))

    def test_bad_schema_version(self):
        p = self._sample(schema_version=99)
        self.assertTrue(any("schema_version" in x for x in pm.validate_patch(p)))

    def test_not_a_dict(self):
        self.assertEqual(pm.validate_patch("nope"), ["patch khong phai JSON object"])

    def test_max_bytes_constant(self):
        self.assertEqual(PATCH_MAX_BYTES, 1024 * 1024)


class TestDumpsLoads(unittest.TestCase):
    def _sample(self):
        return pm.build_patch({"a.txt": b"l1\n"}, {"a.txt": b"l1\nl2\n"}, BASE_SHA)

    def test_roundtrip(self):
        p = self._sample()
        self.assertEqual(pm.loads_patch(pm.dumps_patch(p)), p)

    def test_loads_tampered_json(self):
        data = bytearray(pm.dumps_patch(self._sample()))
        data[20] ^= 0x01
        with self.assertRaises(PB3Error) as ctx:
            pm.loads_patch(bytes(data))
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_loads_bad_checksum(self):
        p = self._sample()
        p["checksum"] = "0" * 64
        data = pm._canonical(p) + b"\n"
        with self.assertRaises(PB3Error) as ctx:
            pm.loads_patch(data)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_dumps_invalid_raises(self):
        p = self._sample()
        p["patch_text"] = "khong phai diff"
        with self.assertRaises(PB3Error) as ctx:
            pm.dumps_patch(p)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")


class TestApplyPatchDisk(unittest.TestCase):
    """Test ghi dia that — skip toan bo khi song bat."""

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

    def _read(self, rel):
        return (self.root / rel).read_bytes()

    def test_roundtrip_modified(self):
        base = {"a.txt": b"l1\nl2\nl3\n"}
        new = {"a.txt": b"l1\nL2\nl3\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        report = pm.apply_patch(p, self.root)
        self.assertEqual(self._read("a.txt"), new["a.txt"])
        self.assertEqual(report["files"], 1)
        self.assertEqual(report["applied"][0]["action"], "modified")
        self.assertFalse(report["dry_run"])

    def test_added_and_deleted(self):
        base = {"a.txt": b"bye\n", "keep.txt": b"k\n"}
        new = {"b.txt": b"moi\n", "keep.txt": b"k\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        report = pm.apply_patch(p, self.root)
        self.assertEqual(self._read("b.txt"), b"moi\n")
        self.assertFalse((self.root / "a.txt").exists())
        self.assertEqual(self._read("keep.txt"), b"k\n")
        actions = {e["path"]: e["action"] for e in report["applied"]}
        self.assertEqual(actions, {"a.txt": "deleted", "b.txt": "added"})

    def test_dry_run_writes_nothing(self):
        base = {"a.txt": b"l1\n"}
        new = {"a.txt": b"l1\nl2\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        report = pm.apply_patch(p, self.root, dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["bytes_written"], 0)
        self.assertEqual(self._read("a.txt"), b"l1\n")

    def test_no_newline_roundtrip(self):
        base = {"a.txt": b"x\ny"}
        new = {"a.txt": b"x\nz"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        pm.apply_patch(p, self.root)
        self.assertEqual(self._read("a.txt"), b"x\nz")

    def test_context_mismatch_fails_closed_atomic(self):
        base = {"a.txt": b"l1\nl2\n", "b.txt": b"m1\nm2\n"}
        new = {"a.txt": b"l1\nL2\n", "b.txt": b"m1\nM2\n"}
        self._write(base)
        # Doi file b.txt tren dia truoc khi ap: ca 2 file deu khong duoc ghi.
        (self.root / "b.txt").write_bytes(b"m1\nKHAC\n")
        p = pm.build_patch(base, new, BASE_SHA)
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(p, self.root)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertEqual(self._read("a.txt"), b"l1\nl2\n")
        self.assertEqual(self._read("b.txt"), b"m1\nKHAC\n")

    def test_base_sha_mismatch(self):
        base = {"a.txt": b"l1\n"}
        new = {"a.txt": b"l1\nl2\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(p, self.root, expected_base_sha=OTHER_SHA)
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")
        self.assertEqual(self._read("a.txt"), b"l1\n")

    def test_sha_mismatch_after_apply(self):
        base = {"a.txt": b"l1\n"}
        new = {"a.txt": b"l1\nl2\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        p["files"][0]["sha256"] = "0" * 64
        p["checksum"] = pm.patch_checksum(p)  # bam lai cho envelope hop le
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(p, self.root)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertEqual(self._read("a.txt"), b"l1\n")

    def test_deleted_missing_file(self):
        self._write({"keep.txt": b"k\n"})
        p = pm.build_patch({"gone.txt": b"x\n"}, {"gone.txt": None}, BASE_SHA)
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(p, self.root)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_added_existing_file(self):
        self._write({"a.txt": b"da co\n"})
        p = pm.build_patch({}, {"a.txt": b"moi\n"}, BASE_SHA)
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(p, self.root)
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertEqual(self._read("a.txt"), b"da co\n")

    def test_drive_letter_path_rejected(self):
        # Duong dan kieu Windows drive: validate_patch_text bo qua nhung
        # _check_rel_path phai chan (E_UNSAFE_PATH), khong ghi gi.
        text = ("diff --git a/C:/evil.txt b/C:/evil.txt\n"
                "--- a/C:/evil.txt\n+++ b/C:/evil.txt\n"
                "@@ -0,0 +1 @@\n+x\n")
        with self.assertRaises(PB3Error) as ctx:
            pm.apply_patch(text, self.root)
        self.assertEqual(ctx.exception.code, "E_UNSAFE_PATH")

    def test_eperm_mock_propagates_wave(self):
        base = {"a.txt": b"l1\n"}
        new = {"a.txt": b"l1\nl2\n"}
        self._write(base)
        p = pm.build_patch(base, new, BASE_SHA)
        err = PB3Error("E_WRITE_WAVE", "ghi bi chan (EPERM)")
        with mock.patch("anti_wave.patch_manager.write_bytes_guarded",
                        side_effect=err):
            with self.assertRaises(PB3Error) as ctx:
                pm.apply_patch(p, self.root)
        self.assertEqual(ctx.exception.code, "E_WRITE_WAVE")
        self.assertEqual(self._read("a.txt"), b"l1\n")

    def test_apply_summary_vietnamese(self):
        report = {"dry_run": False, "files": 2, "bytes_written": 9,
                  "applied": [{"path": "a.txt", "action": "modified", "bytes": 6},
                              {"path": "b.txt", "action": "deleted", "bytes": 0}]}
        text = pm.apply_summary(report)
        self.assertIn("2 file", text)
        self.assertIn("a.txt", text)
        self.assertIn("b.txt", text)


class FakeSync:
    """Gia lap toi thieu GitHubSync.save_patch/get_file/patch_path."""

    def __init__(self):
        self.store = {}

    def patch_path(self, agent_id):
        return "anti_wave/checkpoints/%s.patch" % agent_id

    def save_patch(self, agent_id, patch_text, branch, expected_base_sha=None):
        self.store[self.patch_path(agent_id)] = patch_text.encode("utf-8")
        return {"path": self.patch_path(agent_id), "blob_sha": "blob-x",
                "commit_sha": "c" * 40, "sha256": _sha(patch_text.encode("utf-8"))}

    def get_file(self, branch, path):
        data = self.store.get(path)
        if data is None:
            return None
        return ("blob-x", data)


class TestSaveLoadGitHub(unittest.TestCase):
    def test_save_load_roundtrip(self):
        sync = FakeSync()
        p = pm.build_patch({"a.txt": b"l1\n"}, {"a.txt": b"l1\nl2\n"}, BASE_SHA)
        pm.save_patch_to_github(sync, "agent-1", p, "feat/x")
        self.assertEqual(pm.load_patch_text_from_github(sync, "agent-1", "feat/x"),
                         p["patch_text"])

    def test_load_missing_raises(self):
        sync = FakeSync()
        with self.assertRaises(PB3Error) as ctx:
            pm.load_patch_text_from_github(sync, "agent-9", "feat/x")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_save_invalid_patch_raises_and_stores_nothing(self):
        sync = FakeSync()
        p = pm.build_patch({"a.txt": b"l1\n"}, {"a.txt": b"l1\nl2\n"}, BASE_SHA)
        p["patch_text"] = "khong phai diff"
        with self.assertRaises(PB3Error) as ctx:
            pm.save_patch_to_github(sync, "agent-1", p, "feat/x")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")
        self.assertEqual(sync.store, {})

    def test_load_corrupt_patch_raises(self):
        sync = FakeSync()
        sync.store[sync.patch_path("agent-1")] = b"khong phai diff"
        with self.assertRaises(PB3Error) as ctx:
            pm.load_patch_text_from_github(sync, "agent-1", "feat/x")
        self.assertEqual(ctx.exception.code, "E_CORRUPT")


if __name__ == "__main__":
    unittest.main()
