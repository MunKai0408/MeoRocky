"""Test batch (Claude 09, đặc tả §9.1 + §14.5): manifest, lô 3 game,
resume, dry-run, --only/--limit, timeout, checkpoint, tiến độ 1%, KB.

Tầng fast: fixture xác định (zip dựng bằng zipfile, dữ liệu cố định),
không os.urandom, không mạng. Chạy thật pipeline extract trên zip nhỏ
(registry thật, bootstrap trên bản sao dự án như test_extraction).
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

from core.app import bootstrap
from core.errors import PB3Error
from sdk import batch as b
from sdk.batch import BatchItem, BatchRunner, parse_manifest
from services import language_tool
from tests.helpers import TmpCase, copy_project, register_tmp_dir, unregister_tmp_dir
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

UNITY = "tests/fixtures/unity/sample.unity3d"
CAB = "CAB-0123456789abcdef0123456789abcdef"


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


def _game_zip(entries=None):
    kv = {"hello": "Hello", "play": "Play"}
    data = json.dumps(kv, ensure_ascii=False, indent=2).encode("utf-8")
    return _zip_bytes(entries or {"localization/en.json": data})


class _FakeTTY:
    """Stream giả có isatty()=True (mẫu tests/test_progress_steady.py)."""

    def __init__(self):
        self.buf = []
        self.encoding = "utf-8"

    def write(self, s):
        self.buf.append(s)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return True

    def text(self):
        return "".join(self.buf)


def _pcts(text):
    out = []
    for part in re.split(r"[\r\n]", text):
        m = re.search(r"(\d+)%", part)
        if m:
            out.append(int(m.group(1)))
    return out


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 0.1
        return self.now


class _FakeKB:
    def __init__(self, profile=None):
        self.profile = profile
        self.remembered = []
        self.runs = []
        self.stats = []

    def lookup(self, path):
        return self.profile

    def remember(self, fp, files, locales, choices):
        self.remembered.append((fp, files, locales, choices))
        return 1  # như KB thật: remember trả game_id int

    def record_run(self, game_id, mode, result, error_code=None):
        self.runs.append((game_id, mode, result, error_code))

    def record_plugin_stats(self, adapter_id, version, success):
        self.stats.append((adapter_id, version, success))

    def close(self):
        pass


class _FailingKB(_FakeKB):
    def lookup(self, path):
        raise RuntimeError("KB hỏng")


def _install_fake_kb_module():
    """sdk/kb.py do nhóm KB viết song song (chưa có): giả module trong
    sys.modules để thử đường remember/record_run, không tạo file thật."""
    mod = types.ModuleType("sdk.kb")
    mod.fingerprint_game = lambda path: {
        "bundle_id": "com.test.game", "version": "1.0", "engine": "unity",
        "engine_conf": 1.0, "fingerprint": "fp-test",
        "path_hash": "x", "size": 1, "files": []}
    mod.KB = object
    mod.InventoryCache = object
    sys.modules["sdk.kb"] = mod
    return mod


class TestParseManifest(unittest.TestCase):
    def test_csv_basic_defaults(self):
        items = parse_manifest("path,locale,mode\n/tmp/a.zip,en,extract\n/tmp/b.zip,,mod\n")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], BatchItem(path="/tmp/a.zip", locale="en", mode="extract"))
        self.assertEqual(items[1].mode, "mod")
        self.assertIsNone(items[1].locale)

    def test_csv_all_columns(self):
        items = parse_manifest(
            "path,locale,mode,old,new,file,object,profile_override\n"
            "/tmp/u.unity3d,,mod,Xin chao,Xin chào,CAB-1,1001,true\n")
        it = items[0]
        self.assertEqual((it.old, it.new, it.file, it.object), ("Xin chao", "Xin chào", "CAB-1", "1001"))
        self.assertTrue(it.profile_override)

    def test_csv_from_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                        encoding="utf-8") as fh:
            fh.write("path\n/tmp/a.zip\n")
            name = fh.name
        try:
            self.assertEqual(parse_manifest(name)[0].path, "/tmp/a.zip")
        finally:
            os.unlink(name)

    def test_csv_blank_rows_skipped(self):
        items = parse_manifest("path\n/tmp/a.zip\n\n/tmp/b.zip\n")
        self.assertEqual([i.path for i in items], ["/tmp/a.zip", "/tmp/b.zip"])

    def test_json_list(self):
        items = parse_manifest(
            '[{"path": "/tmp/c.zip", "mode": "mod", "old": "x", "new": "y", '
            '"object": "1001"}]')
        self.assertEqual(items[0].mode, "mod")
        self.assertEqual(items[0].object, "1001")

    def test_missing_path_column(self):
        with self.assertRaises(PB3Error) as cm:
            parse_manifest("locale,mode\nen,extract\n")
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_bad_mode(self):
        with self.assertRaises(PB3Error) as cm:
            parse_manifest("path,mode\n/tmp/a.zip,delete\n")
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_json_wrong_shape(self):
        for txt in ('{"path": "/tmp/a.zip"}', "[1, 2]", '[{"mode": "extract"}]'):
            with self.assertRaises(PB3Error) as cm:
                parse_manifest(txt)
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID", txt)

    def test_json_broken_syntax(self):
        with self.assertRaises(PB3Error) as cm:
            parse_manifest('[{"path": "/tmp/a.zip",}]')
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")

    def test_empty_manifest(self):
        for txt in ("", "   \n", "path\n"):
            with self.assertRaises(PB3Error) as cm:
                parse_manifest(txt)
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID", repr(txt))

    def test_missing_file_path(self):
        with self.assertRaises(PB3Error) as cm:
            parse_manifest("/tmp/khong-co-manifest-xyz.csv")
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")

    def test_item_key_changes_with_old_new(self):
        a = BatchItem(path="/tmp/a.zip", mode="mod", old="x", new="y")
        c = BatchItem(path="/tmp/a.zip", mode="mod", old="x", new="z")
        self.assertNotEqual(a.key(), c.key())
        self.assertEqual(len(a.key()), 64)


class BatchCase(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-batch-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"
        self._fake_kb_mod = None

    def fix(self, rel):
        return str(self._shared_proj / rel)

    def game_zip(self, name, entries=None):
        p = self.tmp / name
        p.write_bytes(_game_zip(entries))
        return str(p)

    def runner(self, **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("app", self.app)
        return BatchRunner(**kw)


class TestBatchRun(BatchCase):
    def test_three_games_one_error(self):
        a = self.game_zip("gameA.zip")
        c = self.game_zip("gameC.zip")
        missing = str(self.tmp / "khong-co.zip")
        summary = self.runner().run(
            [BatchItem(path=a, locale="en"), BatchItem(path=missing, locale="en"),
             BatchItem(path=c, locale="en")],
            out_dir=self.out, dry_run=False)
        self.assertEqual((summary.total, summary.ok, summary.failed, summary.skipped),
                         (3, 2, 1, 0))
        self.assertEqual([g.status for g in summary.games], ["ok", "failed", "ok"])
        self.assertEqual(summary.games[1].error_code, "E_INPUT_NOT_FOUND")
        # failures.txt có đúng mã lỗi
        failures = (self.out / "failures.txt").read_text(encoding="utf-8")
        self.assertIn("E_INPUT_NOT_FOUND", failures)
        self.assertIn("game_02_", failures)
        # index.csv đủ 3 dòng + header; summary.json đếm đúng
        rows = (self.out / "index.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 4)
        self.assertIn("kết quả", rows[0])
        saved = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["ok"], saved["failed"]), (2, 1))
        self.assertEqual(saved["budgets"]["extract_locale_s"], 10)
        # thư mục từng game: result.json + file đã xuất
        for g in (summary.games[0], summary.games[2]):
            d = Path(g.game_dir)
            self.assertTrue((d / "result.json").exists())
            self.assertTrue((d / "English.json").exists(), list(d.iterdir()))
            self.assertEqual(g.budget_s, 10.0)
            self.assertTrue(g.budget_ok)
        self.assertTrue((self.out / "checkpoint.json").exists())
        ck = json.loads((self.out / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ck["games"]), 3)

    def test_resume_runs_only_missing_part(self):
        a = self.game_zip("gameA.zip")
        c = self.game_zip("gameC.zip")
        missing = str(self.tmp / "khong-co.zip")
        items = [BatchItem(path=a, locale="en"), BatchItem(path=missing, locale="en"),
                 BatchItem(path=c, locale="en")]
        base = self.runner()
        calls = []

        def counting(item, staging, ctx):
            calls.append(item.path)
            return base._default_job(item, staging, ctx)

        runner = self.runner(job_runner=counting)
        s1 = runner.run(items, out_dir=self.out, dry_run=False)
        self.assertEqual((s1.ok, s1.failed), (2, 1))
        self.assertEqual(len(calls), 3)
        calls.clear()
        s2 = runner.run(items, out_dir=self.out, dry_run=False, resume=True)
        self.assertEqual(calls, [missing])
        self.assertEqual((s2.ok, s2.failed, s2.skipped), (0, 1, 2))
        self.assertIn("checkpoint", s2.games[0].note)

    def test_resume_without_out_finds_previous_run(self):
        # Tích hợp C09: --resume không kèm --out phải tự tìm lần chạy cũ
        # (CLI trước đây tạo thư mục mới nên resume lặng lẽ không làm gì).
        a = self.game_zip("gameA.zip")
        missing = str(self.tmp / "khong-co.zip")
        items = [BatchItem(path=a, locale="en"), BatchItem(path=missing, locale="en")]
        prev = self.app.paths.output / "_batch" / "20200101-000000"
        base = self.runner()
        calls = []

        def counting(item, staging, ctx):
            calls.append(item.path)
            return base._default_job(item, staging, ctx)

        runner = self.runner(job_runner=counting)
        s1 = runner.run(items, out_dir=prev, dry_run=False)
        self.assertEqual((s1.ok, s1.failed), (1, 1))
        self.assertIsNone(s1.resumed_from)
        calls.clear()
        s2 = runner.run(items, dry_run=False, resume=True)
        self.assertEqual(s2.out_dir, str(prev))
        self.assertEqual(s2.resumed_from, str(prev))
        self.assertEqual(calls, [missing])
        self.assertEqual((s2.ok, s2.failed, s2.skipped), (0, 1, 1))

    def test_resume_without_out_and_no_previous_run_starts_fresh(self):
        a = self.game_zip("gameA.zip")
        s = self.runner().run([BatchItem(path=a, locale="en")],
                              dry_run=False, resume=True)
        self.assertIsNone(s.resumed_from)
        self.assertEqual((s.ok, s.failed), (1, 0))

    def test_checkpoint_exists_after_each_game(self):
        a = self.game_zip("gameA.zip")
        c = self.game_zip("gameC.zip")
        base = self.runner()
        seen = []

        def counting(item, staging, ctx):
            if len(seen) == 1:
                ck = json.loads((self.out / "checkpoint.json").read_text(encoding="utf-8"))
                self.assertIn(BatchItem(path=a, locale="en").key(), ck["games"])
            seen.append(item.path)
            return base._default_job(item, staging, ctx)

        runner = self.runner(job_runner=counting)
        runner.run([BatchItem(path=a, locale="en"), BatchItem(path=c, locale="en")],
                   out_dir=self.out, dry_run=False)
        self.assertEqual(len(seen), 2)

    def test_only_and_limit_filter(self):
        def ok_job(item, staging, ctx):
            return {"status": "ok", "files": [], "note": "giả"}

        items = [BatchItem(path="/tmp/gameA.zip"), BatchItem(path="/tmp/gameB.zip"),
                 BatchItem(path="/tmp/gameC.zip")]
        s = BatchRunner(job_runner=ok_job).run(items, out_dir=self.out, only="gameB")
        self.assertEqual((s.ok, s.skipped), (1, 2))
        self.assertEqual(s.games[1].status, "ok")
        s = BatchRunner(job_runner=ok_job).run(items, out_dir=self.tmp / "out2", limit=2)
        self.assertEqual((s.ok, s.skipped), (2, 1))
        self.assertIn("--limit 2", s.games[2].note)

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_mod_dry_run_writes_no_patched_file(self):
        # Chuỗi mới cùng độ dài byte với chuỗi cũ: PATCH_INPLACE chứa được.
        unity = self.fix(UNITY)
        items = [BatchItem(path=unity, mode="mod", file=CAB, object="1001",
                           old="Xin chao 9 Kings!", new="Xin chao 8 Kings!")]
        summary = self.runner().run(items, out_dir=self.out, dry_run=True)
        self.assertEqual(summary.ok, 1)
        game_dir = Path(summary.games[0].game_dir)
        names = sorted(p.name for p in game_dir.iterdir())
        self.assertEqual(names, ["result.json"])
        self.assertIn("dry-run", summary.games[0].note)

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_mod_write_writes_unit_only(self):
        unity = self.fix(UNITY)
        items = [BatchItem(path=unity, mode="mod", file=CAB, object="1001",
                           old="Xin chao 9 Kings!", new="Xin chao 8 Kings!")]
        summary = self.runner().run(items, out_dir=self.out, dry_run=False)
        self.assertEqual(summary.ok, 1)
        game_dir = Path(summary.games[0].game_dir)
        patched = game_dir / "sample.unity3d"
        self.assertTrue(patched.exists(), list(game_dir.iterdir()))
        self.assertIn(b"Xin chao 8 Kings!", patched.read_bytes())
        # Không đóng gói lại IPA: chỉ có đúng 1 file đơn vị + result.json.
        self.assertEqual(sorted(p.name for p in game_dir.iterdir()),
                         ["result.json", "sample.unity3d"])

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_mod_longer_within_same_alignment_escalates(self):
        # #180 (C10): "Xin chao" -> "Xin chào" (+1 byte, aligned size bằng
        # nhau) trước đây batch mod fail E_WRITE_DISABLED; nay leo lên
        # REBUILD_SECTION qua apply_patch, game vẫn ok.
        unity = self.fix(UNITY)
        items = [BatchItem(path=unity, mode="mod", file=CAB, object="1001",
                           old="Xin chao", new="Xin chào")]
        summary = self.runner().run(items, out_dir=self.out, dry_run=False)
        self.assertEqual(summary.ok, 1)
        patched = Path(summary.games[0].game_dir) / "sample.unity3d"
        self.assertIn("Xin chào 9 Kings!".encode("utf-8"), patched.read_bytes())

    def test_mod_missing_old_new_is_per_game_error(self):
        unity = self.fix(UNITY)
        items = [BatchItem(path=unity, mode="mod", file=CAB, object="1001")]
        summary = self.runner().run(items, out_dir=self.out, dry_run=True)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.games[0].error_code, "E_CONFIG_INVALID")

    def test_timeout_slow_job_is_e_timeout_and_batch_finishes(self):
        # Biên an toàn theo số đo thật: job chậm sleep 1.0s so với timeout
        # 0.5s (tỉ lệ 2×); job nhanh chỉ tốn vài ms trong worker + jitter
        # khởi động thread (đo thô tối đa ~46ms) — biên 10×. Trước đây
        # timeout 0.05s NHỎ HƠN overhead cố định ~0.09s của _execute (ghi
        # result.json + fsync) nên job nhanh bị E_TIMEOUT oan ~50% lần chạy;
        # SDK nay tách finalize khỏi vùng timeout (xem batch._execute_job),
        # test vẫn giữ biên rộng để không phụ thuộc tải máy.
        import time as _time

        def slow_job(item, staging, ctx):
            if "slow" in item.path:
                _time.sleep(1.0)
            return {"status": "ok", "files": []}

        items = [BatchItem(path="/tmp/slow-game.zip"),
                 BatchItem(path="/tmp/fast-game.zip")]
        summary = BatchRunner(job_runner=slow_job).run(
            items, out_dir=self.out, timeout_game=0.5)
        self.assertEqual((summary.ok, summary.failed), (1, 1))
        self.assertEqual(summary.games[0].error_code, "E_TIMEOUT")
        self.assertEqual(summary.games[0].status, "failed")
        self.assertIn("best-effort", summary.games[0].error_message)

    def test_progress_steps_one_percent(self):
        def ok_job(item, staging, ctx):
            return {"status": "ok", "files": []}

        stream = _FakeTTY()
        clock = _FakeClock()
        items = [BatchItem(path="/tmp/g%d.zip" % i) for i in range(3)]
        BatchRunner(job_runner=ok_job, stream=stream, clock=clock).run(
            items, out_dir=self.out)
        pcts = _pcts(stream.text())
        self.assertTrue(pcts, "không vẽ tiến độ nào")
        self.assertEqual(pcts[-1], 100)
        for a, b in zip(pcts, pcts[1:]):
            self.assertGreaterEqual(b, a, (a, b))
            self.assertLessEqual(b - a, 1, (a, b, pcts))

    def test_budget_helpers(self):
        self.assertEqual(b._extract_budget_s(100), 10.0)
        self.assertEqual(b._extract_budget_s(600 * 1024 * 1024), 90.0)


class TestBatchKB(BatchCase):
    def test_kb_preset_used_and_remembered(self):
        en_bytes = json.dumps({"hello": "Hello", "play": "Play"},
                              ensure_ascii=False, indent=2).encode("utf-8")
        sha = hashlib.sha256(en_bytes).hexdigest()
        path = self.game_zip("gameK.zip", {"localization/en.json": en_bytes})
        profile = {"game": {"bundle_id": "com.test.game", "version": "1.0",
                            "engine": "unity"},
                   "files": [{"rel_path": "localization/en.json",
                              "size": len(en_bytes), "sha256": sha,
                              "role": "localization"}],
                   "locales": ["en"], "choices": {sha: "json_text"},
                   "updated_at": "2026-11-16"}
        kb = _FakeKB(profile)
        self._fake_kb_mod = _install_fake_kb_module()
        self.addCleanup(lambda: sys.modules.pop("sdk.kb", None))
        summary = self.runner().run([BatchItem(path=path)], out_dir=self.out,
                                    dry_run=False, kb=kb)
        self.assertEqual(summary.ok, 1)
        game = summary.games[0]
        self.assertTrue(game.kb_used)
        self.assertIn("2026-11-16", game.kb_note)
        self.assertEqual(len(kb.remembered), 1)
        # remember nhận đúng dạng KB thật: files/locales là dict, không
        # phải chuỗi (lỗi tích hợp C09: chuỗi làm remember ném E_PLUGIN_INVALID).
        _fp, files, locales, _choices = kb.remembered[0]
        self.assertTrue(all(isinstance(f, dict) and f.get("rel_path") for f in files))
        self.assertTrue(all(isinstance(l, dict) and l.get("locale") for l in locales))
        self.assertEqual(kb.runs, [(1, "extract", "ok", None)])
        self.assertTrue(kb.stats)

    def test_kb_profile_override_skips_preset(self):
        en_bytes = json.dumps({"hello": "Hello"}, ensure_ascii=False).encode("utf-8")
        sha = hashlib.sha256(en_bytes).hexdigest()
        path = self.game_zip("gameK.zip", {"localization/en.json": en_bytes})
        profile = {"files": [{"rel_path": "localization/en.json", "size": 1,
                              "sha256": sha, "role": "localization"}],
                   "locales": ["en"], "choices": {sha: "json_text"},
                   "updated_at": "2026-11-16"}
        kb = _FakeKB(profile)
        summary = self.runner().run(
            [BatchItem(path=path, profile_override=True)], out_dir=self.out,
            dry_run=False, kb=kb)
        self.assertEqual(summary.ok, 1)
        self.assertFalse(summary.games[0].kb_used)

    def test_kb_lookup_error_does_not_fail_game(self):
        path = self.game_zip("gameK.zip")
        summary = self.runner().run([BatchItem(path=path, locale="en")],
                                    out_dir=self.out, dry_run=False,
                                    kb=_FailingKB(None))
        self.assertEqual(summary.ok, 1)
        self.assertFalse(summary.games[0].kb_used)


class TestBatchRealKB(BatchCase):
    def test_extract_twice_second_run_uses_kb_profile(self):
        # Tích hợp C09 (KB thật, không fake): lần 1 ghi profile + runs +
        # thống kê plugin; lần 2 không ghi locale thì preset từ KB.
        from sdk.kb import KB
        path = self.game_zip("gameR.zip")
        kb = KB(str(self.tmp / "kb.sqlite"))
        try:
            s1 = self.runner().run([BatchItem(path=path)], out_dir=self.out,
                                   dry_run=False, kb=kb)
            self.assertEqual(s1.ok, 1)
            self.assertFalse(s1.games[0].kb_used)
            self.assertEqual(len(kb.list_games()), 1)
            profile = kb.lookup(path)
            self.assertIsNotNone(profile)
            self.assertTrue(profile["locales"])
            self.assertTrue(profile["choices"])
            # Lựa chọn (hash file đã trích) khớp inventory fingerprint.
            stored_hashes = {f["sha256"] for f in profile["files"]}
            self.assertTrue(set(profile["choices"]) & stored_hashes)
            self.assertEqual(kb.plugin_stats(), {"json_text": 1})
            s2 = self.runner().run([BatchItem(path=path)],
                                   out_dir=self.tmp / "out2",
                                   dry_run=False, kb=kb)
            self.assertEqual(s2.ok, 1)
            self.assertTrue(s2.games[0].kb_used)
            self.assertIn("dùng tri thức", s2.games[0].kb_note)
        finally:
            kb.close()

    def test_kb_preset_file_does_not_leak_across_locales(self):
        # Lựa chọn ghi cho locale en không được ép file en sang job zh
        # (hồi quy: job en dry-run ghi profile rồi job zh-Hans lỗi
        # E_LOCALE_NOT_FOUND vì preset ép file en).
        from sdk.kb import KB
        path = self.fix("tests/fixtures/extract/game1.zip")
        kb = KB(str(self.tmp / "kb2.sqlite"))
        try:
            s1 = self.runner().run([BatchItem(path=path, locale="en")],
                                   out_dir=self.out, dry_run=False, kb=kb)
            self.assertEqual(s1.ok, 1)
            s2 = self.runner().run([BatchItem(path=path, locale="zh-Hans")],
                                   out_dir=self.tmp / "out2",
                                   dry_run=False, kb=kb)
            self.assertEqual(s2.ok, 1)
            self.assertFalse(s2.games[0].kb_used)
        finally:
            kb.close()


if __name__ == "__main__":
    unittest.main()
