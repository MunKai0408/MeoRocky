"""Test anti_wave.github_sync (Anti-Wave, Phase 3).

CHI DUNG MOCK — khong goi mang that, khong ghi dia that, khong dung
tempfile (tempfile chet trong song EPERM [DA DO THAT]). Chay duoc ca luc
song bat: python3 -m pytest pb3/tests/test_github_sync.py -p no:cacheprovider -q
"""
import base64
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anti_wave import github_sync as gs  # noqa: E402

FAKE_TOKEN = "test-token-123"
BASE_SHA = "a5ec6400ab69bd3519d8ff83cfb916b601f192ec"


def _sample_checkpoint(**over):
    cp = {
        "schema_version": 1,
        "agent_id": "agent-1",
        "task_id": "phase3",
        "branch": "feat/anti-wave-phase3-prep",
        "base_commit_sha": BASE_SHA,
        "changed_files": [{"path": "anti_wave/github_sync.py",
                           "sha256": "a" * 64, "bytes": 123}],
        "patch_or_content": "patch-diff...",
        "test_status": "new-tests pass",
        "created_at": "2026-11-18T05:30:00Z",
    }
    cp.update(over)
    cp["checksum"] = gs._checkpoint_checksum(cp)
    return cp


class FakeGitHub:
    """Gia lap GitHub REST toi thieu cho cac endpoint github_sync dung."""

    def __init__(self):
        self.files = {}  # path -> (sha, bytes)
        self.head = BASE_SHA
        self.commits = 0
        self.dispatch_status = 204
        self.runs = []
        self.artifacts = []

    def _blob_sha(self, content: bytes) -> str:
        return "blob-" + hashlib.sha256(content).hexdigest()[:12]

    def request(self, method, path, body=None):
        if path.startswith("/repos/%s/git/ref/heads/" % gs.REPO):
            return 200, {"object": {"sha": self.head}}
        if path.startswith("/repos/%s/contents/" % gs.REPO):
            rel = path[len("/repos/%s/contents/" % gs.REPO):].split("?")[0]
            if method == "GET":
                if rel in self.files:
                    sha, content = self.files[rel]
                    return 200, {"sha": sha,
                                 "content": base64.b64encode(content).decode("ascii")}
                # Thu muc: API that tra ve LIST cac muc con (giong that).
                prefix = rel.rstrip("/") + "/"
                children = sorted({p[len(prefix):] for p in self.files
                                   if p.startswith(prefix)
                                   and "/" not in p[len(prefix):]})
                if children:
                    return 200, [{"name": name, "type": "file"}
                                 for name in children]
                raise gs.SyncError("E_NOT_FOUND", "khong thay %s" % rel)
            if method == "PUT":
                content = base64.b64decode(body["content"])
                if "sha" in body:
                    if rel not in self.files or self.files[rel][0] != body["sha"]:
                        raise gs.SyncError("E_CONFLICT", "sha file khong khop")
                    status = 200
                else:
                    status = 201
                blob = self._blob_sha(content)
                self.files[rel] = (blob, content)
                self.commits += 1
                commit = "commit-%04d" % self.commits
                self.head = commit
                return status, {"content": {"sha": blob}, "commit": {"sha": commit}}
        if path.startswith("/repos/%s/actions/workflows/" % gs.REPO) and path.endswith("/dispatches"):
            if self.dispatch_status == 204:
                return 204, None
            # Giong that: _request that raise _http_error (mapping that),
            # khong tra ve status 4xx. Dung mapping that qua sync that.
            raise self.sync._http_error(self.dispatch_status, path, "")
        if "/actions/workflows/" in path and path.endswith("/runs?per_page=5"):
            return 200, {"workflow_runs": self.runs}
        if "/actions/runs/" in path and path.endswith("/artifacts"):
            return 200, {"artifacts": self.artifacts}
        if "/actions/runs/" in path:
            return 200, {"id": 1, "status": "completed", "conclusion": "success"}
        raise AssertionError("endpoint chua gia lap: %s %s" % (method, path))


def _sync_with_fake(fake):
    sync = gs.GitHubSync(token=FAKE_TOKEN)
    fake.sync = sync  # fake dung mapping _http_error that cua sync
    patcher = mock.patch.object(gs.GitHubSync, "_request", fake.request)
    patcher.start()
    return sync, patcher


class TestChecksumVector(unittest.TestCase):
    def test_vector_1_ascii(self):
        # Vector tinh doc lap (hashlib+json, khong qua github_sync) — neu
        # doi dinh dang canonical thi test nay do ngay.
        payload = {
            "agent_id": "test-agent",
            "task_id": "test-task",
            "branch": "feat/test",
            "base_commit_sha": "a" * 40,
            "changed_files": [{"bytes": 10, "path": "a.py", "sha256": "b" * 64}],
            "created_at": "2026-11-18T00:00:00Z",
            "patch_or_content": "diff --git a/a.py b/a.py",
            "schema_version": 1,
            "test_status": "ok",
        }
        self.assertEqual(
            gs._checkpoint_checksum(payload),
            "fff3c13203cb5d033bae45ccaf781bd32ace5a5841bd7353ab4d94fb0053022c")

    def test_vector_2_unicode_empty_files(self):
        payload = {
            "agent_id": "agent-v2",
            "task_id": "phase3",
            "branch": "feat/anti-wave-phase3-prep",
            "base_commit_sha": BASE_SHA,
            "changed_files": [],
            "created_at": "2026-11-18T05:20:00Z",
            "patch_or_content": "chưa có",
            "schema_version": 1,
            "test_status": "chưa chạy",
        }
        self.assertEqual(
            gs._checkpoint_checksum(payload),
            "9bfd69a81e78341557a054eec82378bda2fb5cdcce405129fb4ef2e99ee38f6a")


class TestValidateCheckpoint(unittest.TestCase):
    def test_ok(self):
        cp = _sample_checkpoint()
        self.assertEqual(gs.validate_checkpoint(cp)["agent_id"], "agent-1")

    def test_bad_checksum(self):
        cp = _sample_checkpoint()
        cp["test_status"] = "da sua sau khi bam checksum"
        with self.assertRaises(gs.SyncError) as ctx:
            gs.validate_checkpoint(cp)
        self.assertEqual(ctx.exception.code, "E_VALIDATION")

    def test_missing_field(self):
        cp = _sample_checkpoint()
        del cp["branch"]
        with self.assertRaises(gs.SyncError) as ctx:
            gs.validate_checkpoint(cp)
        self.assertEqual(ctx.exception.code, "E_VALIDATION")

    def test_wrong_schema(self):
        cp = _sample_checkpoint(schema_version=2)
        with self.assertRaises(gs.SyncError):
            gs.validate_checkpoint(cp)

    def test_not_a_dict(self):
        with self.assertRaises(gs.SyncError):
            gs.validate_checkpoint(["khong-phai-dict"])


class TestSaveLoadCheckpoint(unittest.TestCase):
    def test_roundtrip(self):
        fake = FakeGitHub()
        sync, patcher = _sync_with_fake(fake)
        try:
            cp = _sample_checkpoint()
            saved = sync.save_checkpoint("agent-1", cp, "feat/anti-wave-phase3-prep",
                                         expected_base_sha=BASE_SHA)
            self.assertEqual(saved["path"], "anti_wave/checkpoints/agent-1.json")
            self.assertEqual(saved["sha256"],
                             hashlib.sha256(gs._canonical(cp) + b"\n").hexdigest())
            loaded = sync.load_checkpoint("agent-1", "feat/anti-wave-phase3-prep")
            self.assertEqual(loaded, cp)
            self.assertEqual(sync.list_checkpoints("feat/anti-wave-phase3-prep"),
                             ["agent-1"])
        finally:
            patcher.stop()

    def test_refuses_when_branch_moved(self):
        fake = FakeGitHub()
        sync, patcher = _sync_with_fake(fake)
        try:
            fake.head = "d" * 40  # nhanh da dich sau khi agent doc head cu
            with self.assertRaises(gs.SyncError) as ctx:
                sync.save_checkpoint("agent-1", _sample_checkpoint(),
                                     "feat/anti-wave-phase3-prep",
                                     expected_base_sha=BASE_SHA)
            self.assertEqual(ctx.exception.code, "E_CONFLICT")
            self.assertEqual(fake.files, {})  # khong ghi gi ca
        finally:
            patcher.stop()

    def test_load_missing(self):
        fake = FakeGitHub()
        sync, patcher = _sync_with_fake(fake)
        try:
            with self.assertRaises(gs.SyncError) as ctx:
                sync.load_checkpoint("agent-chua-co", "feat/anti-wave-phase3-prep")
            self.assertEqual(ctx.exception.code, "E_NOT_FOUND")
        finally:
            patcher.stop()

    def test_save_rejects_bad_checkpoint(self):
        fake = FakeGitHub()
        sync, patcher = _sync_with_fake(fake)
        try:
            cp = _sample_checkpoint()
            cp["checksum"] = "0" * 64
            with self.assertRaises(gs.SyncError):
                sync.save_checkpoint("agent-1", cp, "feat/anti-wave-phase3-prep")
            self.assertEqual(fake.files, {})
        finally:
            patcher.stop()


class TestPatch(unittest.TestCase):
    GOOD = ("diff --git a/x.py b/x.py\n"
            "--- a/x.py\n+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n-old\n+new\n")

    def test_valid_patch_ok(self):
        self.assertEqual(gs.validate_patch_text(self.GOOD), [])

    def test_empty_patch_rejected(self):
        self.assertTrue(gs.validate_patch_text(""))

    def test_secret_rejected(self):
        bad = self.GOOD + "# token ghp_abcdefghij1234567890\n"
        problems = gs.validate_patch_text(bad)
        self.assertTrue(any("bi mat" in p for p in problems))

    def test_absolute_path_rejected(self):
        bad = ("diff --git a/x.py b/x.py\n"
               "--- /home/hatch/x.py\n+++ /home/hatch/x.py\n"
               "@@ -1 +1 @@\n-old\n+new\n")
        self.assertTrue(gs.validate_patch_text(bad))

    def test_save_patch_roundtrip(self):
        fake = FakeGitHub()
        sync, patcher = _sync_with_fake(fake)
        try:
            saved = sync.save_patch("agent-1", self.GOOD,
                                    "feat/anti-wave-phase3-prep")
            self.assertEqual(saved["path"], "anti_wave/checkpoints/agent-1.patch")
        finally:
            patcher.stop()


class TestDispatch(unittest.TestCase):
    def test_204_ok(self):
        fake = FakeGitHub()
        fake.dispatch_status = 204
        sync, patcher = _sync_with_fake(fake)
        try:
            self.assertTrue(sync.dispatch_workflow("anti-wave-ci.yml", "main",
                                                   {"ref": "feat/x", "scope": "unit"}))
        finally:
            patcher.stop()

    def test_404_hint_mentions_main(self):
        fake = FakeGitHub()
        fake.dispatch_status = 404
        sync, patcher = _sync_with_fake(fake)
        try:
            with self.assertRaises(gs.SyncError) as ctx:
                sync.dispatch_workflow("extract-large.yml", "feat/actions-extract")
            # Hint phai noi ro luat default-branch, khong do loi cho quyen.
            self.assertIn("main", ctx.exception.hint)
        finally:
            patcher.stop()

    def test_403_is_forbidden(self):
        fake = FakeGitHub()
        fake.dispatch_status = 403
        sync, patcher = _sync_with_fake(fake)
        try:
            with self.assertRaises(gs.SyncError) as ctx:
                sync.dispatch_workflow("anti-wave-ci.yml", "main")
            self.assertEqual(ctx.exception.code, "E_FORBIDDEN")
        finally:
            patcher.stop()


class TestHttpErrorMapping(unittest.TestCase):
    def test_401_403_404_429(self):
        sync = gs.GitHubSync(token=FAKE_TOKEN)
        err = sync._http_error(401, "/repos/x", "")
        self.assertEqual(err.code, "E_AUTH")
        err = sync._http_error(403, "/repos/x/actions/workflows/a/dispatches", "")
        self.assertEqual(err.code, "E_FORBIDDEN")
        self.assertIn("Actions", err.hint)
        err = sync._http_error(404, "/repos/x/actions/workflows/a/dispatches", "")
        self.assertEqual(err.code, "E_NOT_FOUND")
        self.assertIn("main", err.hint)
        err = sync._http_error(429, "/repos/x", "")
        self.assertEqual(err.code, "E_RATE_LIMIT")


class TestNoTokenLeak(unittest.TestCase):
    def test_redact(self):
        self.assertEqual(gs._redact("token=%s end" % FAKE_TOKEN, FAKE_TOKEN),
                         "token=[REDACTED] end")

    def test_http_error_hides_token(self):
        sync = gs.GitHubSync(token=FAKE_TOKEN)
        err = sync._http_error(401, "/repos/x", "detail co %s trong do" % FAKE_TOKEN)
        self.assertNotIn(FAKE_TOKEN, str(err))
        self.assertNotIn(FAKE_TOKEN, err.hint)

    def test_missing_token_names_env_var_only(self):
        with mock.patch.dict(os.environ, {"GITHUB_BEAR_TOKEN": ""}):
            with self.assertRaises(gs.SyncError) as ctx:
                gs.GitHubSync(token=None)
            self.assertEqual(ctx.exception.code, "E_CONFIG")
            self.assertIn("GITHUB_BEAR_TOKEN", str(ctx.exception))


class TestFailset(unittest.TestCase):
    XML = (b'<?xml version="1.0" encoding="utf-8"?>'
           b'<testsuites><testsuite name="pytest" tests="3" failures="1" '
           b'errors="1" skipped="1">'
           b'<testcase classname="test_a" name="test_pass"/>'
           b'<testcase classname="test_a" name="test_fail"><failure message="x"/></testcase>'
           b'<testcase classname="test_b" name="test_err"><error message="y"/></testcase>'
           b'</testsuite></testsuites>')

    def test_parse(self):
        out = gs.failset_from_junitxml(self.XML)
        self.assertEqual(out["tests"], 3)
        self.assertEqual(out["failures"], 1)
        self.assertEqual(out["errors"], 1)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["failed"], ["test_a::test_fail"])
        self.assertEqual(out["error_ids"], ["test_b::test_err"])

    def test_bad_xml(self):
        with self.assertRaises(gs.SyncError):
            gs.failset_from_junitxml(b"<khong-dong-the>")


if __name__ == "__main__":
    unittest.main()
