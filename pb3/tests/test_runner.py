# -*- coding: utf-8 -*-
"""Test cho bộ chạy test (Wave 3, R1-R10 + giao diện chạy test).

Meta test (chạy nhanh, trong tiến trình): hysteresis, _plan nhẹ, timeout,
ThreadedSuite, hiển thị tiến độ. Slow test (chạy tiến trình con):
dòng Ran giữ nguyên, bảng tóm tắt, timeout thành ERROR, Ctrl+C lưu một phần,
--changed (file đã xóa / slow-meta bị loại).
"""
import io
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import run_tests
from core import color as color_mod
from core.color import display_width
from tests import tiering as T
from tests.helpers import TmpCase, subprocess_timeout
from tests.tiering import meta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RAN_RE = re.compile(r"Ran \d+ tests in [\d.]+s  (OK|FAILED) \(skipped=\d+, lỗi=\d+, fail=\d+\)")


class _FakeTest:
    """Test giả cho R: chỉ cần id() + str()."""

    def __init__(self, tid):
        self._tid = tid

    def id(self):
        return self._tid

    def __str__(self):
        return "%s (%s)" % (self._tid.split(".")[-1], self._tid)


@meta
class TestPlanCounts(TmpCase):
    def test_plan_counts_match_real_load(self):
        # R8: đếm nhẹ bằng _Facts phải khớp số đếm khi nạp thật.
        total, cnt, smoke_n = T.plan_counts()
        loader = unittest.defaultTestLoader
        real_total = 0
        real_cnt = dict((n, 0) for n in T.PARTITIONS)
        real_smoke = 0
        mods = ["tests." + f[:-3] for f in sorted(os.listdir(os.path.join(ROOT, "tests")))
                if f.startswith("test") and f.endswith(".py")]
        for mod in mods:
            for t in run_tests._flatten(loader.loadTestsFromName(mod)):
                real_total += 1
                real_cnt[T.classify_id(t.id())] += 1
                if T.is_smoke_id(t.id()):
                    real_smoke += 1
        self.assertEqual(total, real_total)
        self.assertEqual(cnt, real_cnt)
        self.assertEqual(smoke_n, real_smoke)


@meta
class TestHysteresis(TmpCase):
    def setUp(self):
        super().setUp()
        self._old_env = os.environ.get("PB3_TEST_BUDGET_S")
        os.environ["PB3_TEST_BUDGET_S"] = "1.0"
        # Chốt hệ số máy = 1: test này kiểm LOGIC hysteresis với ngân sách
        # 1.0s cố định, không phụ thuộc máy (trên iSH budget_s() = 5.0s
        # nên 1.5s không vượt ngân sách — test sẽ fail oan nếu không chốt).
        self._old_scale = os.environ.get("PB3_TEST_TIMEOUT_SCALE")
        os.environ["PB3_TEST_TIMEOUT_SCALE"] = "1"
        self._prof = os.path.join(self.tmp, "test_times.json")
        self._old_prof = T.PROFILE_FILE
        T.PROFILE_FILE = type(self._old_prof)(self._prof)
        T.reset_cache()

    def tearDown(self):
        T.PROFILE_FILE = self._old_prof
        T.reset_cache()
        if self._old_env is None:
            os.environ.pop("PB3_TEST_BUDGET_S", None)
        else:
            os.environ["PB3_TEST_BUDGET_S"] = self._old_env
        if self._old_scale is None:
            os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)
        else:
            os.environ["PB3_TEST_TIMEOUT_SCALE"] = self._old_scale
        super().tearDown()

    def _write_profile(self, d):
        with open(self._prof, "w", encoding="utf-8") as f:
            json.dump(d, f)

    def _read_profile(self):
        with open(self._prof, encoding="utf-8") as f:
            return json.load(f)

    def test_promote_after_two_consecutive_overs(self):
        ident = "test_x.py::C::t"
        promoted, _ = T.record_times({ident: 1.5}, {ident: T.FAST}, False)
        self.assertEqual(promoted, [])
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.FAST)
        promoted, _ = T.record_times({ident: 1.5}, {ident: T.FAST}, False)
        self.assertEqual(promoted, [ident])
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.SLOW)

    def test_demote_after_two_fulls_under_budget(self):
        ident = "test_x.py::C::t"
        self._write_profile({ident: {"t": 1.5, "over": 2, "under": 0}})
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.SLOW)
        _, recovered = T.record_times({ident: 0.5}, {ident: T.SLOW}, True)
        self.assertEqual(recovered, [])
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.SLOW)
        _, recovered = T.record_times({ident: 0.5}, {ident: T.SLOW}, True)
        self.assertEqual(recovered, [ident])
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.FAST)

    def test_old_format_number_still_slow(self):
        # D-POLISH-3: profile cũ (một số) vượt ngân sách -> slow như trước.
        self._write_profile({"test_x.py::C::t": 2.0})
        T.reset_cache()
        self.assertEqual(T.classify("test_x.py", "C", "t"), T.SLOW)

    def test_prune_stale_keys_and_meta_on_full(self):
        self._write_profile({"test_x.py::C::t": 0.1, "test_gone.py::C::t": 0.1})
        T.reset_cache()
        T.record_times({"test_x.py::C::t": 0.1}, {"test_x.py::C::t": T.FAST},
                       True, {"test_x.py::C::t"})
        prof = self._read_profile()
        self.assertNotIn("test_gone.py::C::t", prof)
        self.assertIn("_meta", prof)
        self.assertIn("machine", prof["_meta"])
        self.assertIn("updated", prof["_meta"])


@meta
class TestRunnerTimeout(TmpCase):
    def test_default_180s(self):
        # Timeout gốc 180s nhân hệ số máy: x5 trên iSH (/proc/ish) là đúng
        # thiết kế (iSH 23:36 fail vì test cũ đòi đúng 180.0 dù ở đâu).
        old = os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)
        try:
            self.assertEqual(T.runner_timeout_s(), 180.0 * T.timeout_scale())
        finally:
            if old is None:
                os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)
            else:
                os.environ["PB3_TEST_TIMEOUT_SCALE"] = old

    def test_default_base_180s_when_scale_1(self):
        os.environ["PB3_TEST_TIMEOUT_SCALE"] = "1"
        try:
            self.assertEqual(T.runner_timeout_s(), 180.0)
        finally:
            os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)

    def test_scale_env(self):
        os.environ["PB3_TEST_TIMEOUT_SCALE"] = "2"
        try:
            self.assertEqual(T.runner_timeout_s(), 360.0)
        finally:
            os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)


@meta
class TestBudgetScale(TmpCase):
    def test_budget_scales_with_machine(self):
        # budget_s() = PB3_TEST_BUDGET_S * timeout_scale: 1.0s sandbox,
        # 5.0s trên iSH (đợt fix 02:00 — 41 test vượt 1.0s trên iSH chỉ vì
        # môi trường chậm (CPU, spawn subprocess), không phải code chậm).
        old_b = os.environ.get("PB3_TEST_BUDGET_S")
        old_s = os.environ.get("PB3_TEST_TIMEOUT_SCALE")
        try:
            os.environ.pop("PB3_TEST_BUDGET_S", None)
            os.environ["PB3_TEST_TIMEOUT_SCALE"] = "1"
            self.assertEqual(T.budget_s(), 1.0)
            os.environ["PB3_TEST_TIMEOUT_SCALE"] = "5"
            self.assertEqual(T.budget_s(), 5.0)
            os.environ["PB3_TEST_BUDGET_S"] = "2.0"
            self.assertEqual(T.budget_s(), 10.0)
        finally:
            if old_b is None:
                os.environ.pop("PB3_TEST_BUDGET_S", None)
            else:
                os.environ["PB3_TEST_BUDGET_S"] = old_b
            if old_s is None:
                os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)
            else:
                os.environ["PB3_TEST_TIMEOUT_SCALE"] = old_s


@meta
class TestDepsImportModule(TmpCase):
    def test_import_module_literal_is_dependency(self):
        rel = "tests/test_tmp_import_module.py"
        path = os.path.join(ROOT, rel)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("import importlib\nimport unittest\n\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_x(self):\n"
                        "        m = importlib.import_module(\"core.color\")\n"
                        "        self.assertTrue(m)\n")
            T.reset_cache()
            deps = T._deps(rel, {"core/color.py", "core/__init__.py"})
            self.assertIn("core/color.py", deps)
        finally:
            if os.path.exists(path):
                os.remove(path)
            T.reset_cache()


@meta
class TestThreadedSuite(TmpCase):
    def _case(self, name, body):
        def runTest(self):
            body()
        cls = type(name, (unittest.TestCase,), {"runTest": runTest})
        return cls("runTest")

    def test_timeout_is_error_and_pipeline_continues(self):
        # D-POLISH-2: test treo -> ERROR (không phải skip), test sau vẫn chạy.
        stop = threading.Event()

        def hang():
            stop.wait(30)

        def ok():
            return True

        hang_case = self._case("HangCase", hang)
        ok_case = self._case("OkCase", ok)
        suite = run_tests.ThreadedSuite([hang_case, ok_case])
        suite.timeout = 0.3
        res = unittest.TestResult()
        t0 = time.monotonic()
        suite.run(res)
        el = time.monotonic() - t0
        self.assertLess(el, 5.0)
        self.assertEqual(res.testsRun, 2)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("TimeoutError", res.errors[0][1])   # 3.12: .errors lưu chuỗi đã định dạng
        self.assertEqual(len(res.failures), 0)
        self.assertEqual(getattr(res, "timeouts", 0), 1)
        stop.set()

    def test_success_failure_skip_merged(self):
        def ok():
            return True

        def bad():
            raise AssertionError("sai rồi")

        def skip():
            raise unittest.SkipTest("bỏ qua")

        suite = run_tests.ThreadedSuite([
            self._case("OkCase", ok),
            self._case("BadCase", bad),
            self._case("SkipCase", skip),
        ])
        suite.timeout = 30.0
        res = unittest.TestResult()
        suite.run(res)
        self.assertEqual(res.testsRun, 3)
        self.assertEqual(len(res.errors), 0)
        self.assertEqual(len(res.failures), 1)
        self.assertEqual(len(res.skipped), 1)

    def test_error_merged_with_traceback(self):
        def boom():
            raise ValueError("nổ")

        suite = run_tests.ThreadedSuite([self._case("BoomCase", boom)])
        suite.timeout = 30.0
        res = unittest.TestResult()
        suite.run(res)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("ValueError", res.errors[0][1])
        self.assertIn("nổ", res.errors[0][1])


@meta
class TestProgressDisplay(TmpCase):
    def setUp(self):
        super().setUp()
        # Ép màu tắt (không phụ thuộc stdout của tiến trình chạy test có phải TTY không).
        self._old_mode = color_mod._DEFAULT.mode
        color_mod.set_mode("off")
        # Chốt hệ số máy = 1: test_slow_flag_and_eta kiểm cờ SLOW với
        # profile 2.5s trên ngân sách 1.0s cố định; trên iSH budget_s() =
        # 5.0s nên 2.5s không còn là SLOW (đúng hành vi sản phẩm) nhưng test
        # sẽ fail oan nếu không chốt.
        self._old_scale = os.environ.get("PB3_TEST_TIMEOUT_SCALE")
        os.environ["PB3_TEST_TIMEOUT_SCALE"] = "1"

    def tearDown(self):
        color_mod.set_mode(self._old_mode)
        if self._old_scale is None:
            os.environ.pop("PB3_TEST_TIMEOUT_SCALE", None)
        else:
            os.environ["PB3_TEST_TIMEOUT_SCALE"] = self._old_scale
        super().tearDown()

    def _res(self, total=100):
        stream = io.StringIO()
        res = run_tests.R(stream, False, 0)
        res.total = total
        res.eta_sums = [0.0] * total
        res._tty = False   # ép chế độ pipe: dòng thưa, không mã escape
        return res, stream

    def test_progress_line_plain_no_escapes(self):
        res, stream = self._res()
        fake = _FakeTest("tests.test_x.C.test_short_name")
        for _ in range(50):
            res.startTest(fake)
            res.addSuccess(fake)
            res.stopTest(fake)
        out = stream.getvalue()
        self.assertNotIn("\x1b", out)
        self.assertIn("[50/100]", out)

    def test_short_name_keeps_test_name(self):
        res, _ = self._res()
        fake = _FakeTest("tests.test_plugin_toggle.TestRealCli.test_disable_survives_new_process")
        short = res._short_name(fake)
        self.assertIn("test_disable_survives_new_process", short)
        self.assertLessEqual(len(short), 40)

    def test_slow_flag_and_eta(self):
        res, _ = self._res()
        res.n = 1
        res._cur_short = "test_x (C)"
        res._cur_profile = 2.5
        res.eta_sums = [65.0] * 100
        line = res._progress_line()
        self.assertIn("SLOW", line)
        self.assertIn("ETA ~1m05s", line)
        self.assertNotIn("\x1b", line)

    def test_save_partial_profile_only_passing(self):
        res = unittest.TestResult()
        res.times = {"tests.test_x.C.test_ok": 0.1, "tests.test_x.C.test_bad": 0.2}
        err_case = _FakeTest("tests.test_x.C.test_bad")
        res.errors = [(err_case, "boom")]
        res.skipped = []
        old = T.PROFILE_FILE
        prof = os.path.join(self.tmp, "test_times.json")
        T.PROFILE_FILE = type(old)(prof)
        try:
            run_tests._save_partial_profile(res, {})
            with open(prof, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertIn("test_x.py::C::test_ok", saved)
            self.assertNotIn("test_x.py::C::test_bad", saved)
        finally:
            T.PROFILE_FILE = old


@meta
class TestFactsCache(TmpCase):
    """Bộ nhớ đệm _Facts trên đĩa (tests/tiering.py): khóa bằng sha1 nội dung
    file + hash luật xếp tầng; hỏng cache hay đổi nội dung/luật thì phân
    tích lại, không bao giờ cho kết quả cũ."""

    def setUp(self):
        super().setUp()
        self._old_cache = T.FACTS_CACHE_FILE
        self._cache = os.path.join(self.tmp, "facts.json")
        T.FACTS_CACHE_FILE = type(self._old_cache)(self._cache)
        T.reset_cache()
        T._FACTS_DIRTY.clear()
        self._rel = "test_tmp_facts.py"
        self._path = os.path.join(ROOT, "tests", self._rel)
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("import time\nimport unittest\n\n\n"
                    "class C(unittest.TestCase):\n"
                    "    def test_cham(self):\n"
                    "        time.sleep(0.1)\n"
                    "    def test_nhanh(self):\n"
                    "        pass\n")

    def tearDown(self):
        if os.path.exists(self._path):
            os.remove(self._path)
        T.FACTS_CACHE_FILE = self._old_cache
        T.reset_cache()
        T._FACTS_DIRTY.clear()
        super().tearDown()

    def test_round_trip_from_disk(self):
        self.assertEqual(T._facts(self._rel).tier("C", "test_cham"), T.SLOW)
        self.assertEqual(T._facts(self._rel).tier("C", "test_nhanh"), T.FAST)
        T.flush_facts_cache()
        self.assertTrue(os.path.exists(self._cache))
        T.reset_cache()
        f = T._facts(self._rel)
        self.assertIsInstance(f, T._FactsView)
        self.assertEqual(f.tier("C", "test_cham"), T.SLOW)
        self.assertEqual(f.tier("C", "test_nhanh"), T.FAST)
        self.assertFalse(f.smoke_marked("C", "test_nhanh"))

    def test_content_change_invalidates(self):
        T._facts(self._rel)
        T.flush_facts_cache()
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("import unittest\n\n\n"
                    "class C(unittest.TestCase):\n"
                    "    def test_cham(self):\n"
                    "        pass\n")
        T.reset_cache()
        self.assertEqual(T._facts(self._rel).tier("C", "test_cham"), T.FAST)

    def test_corrupt_cache_falls_back_to_parse(self):
        T._facts(self._rel)
        T.flush_facts_cache()
        with open(self._cache, "w", encoding="utf-8") as f:
            f.write("khong phai json {")
        T.reset_cache()
        self.assertEqual(T._facts(self._rel).tier("C", "test_cham"), T.SLOW)

    def test_wrong_rules_hash_invalidates(self):
        T._facts(self._rel)
        T.flush_facts_cache()
        with open(self._cache, encoding="utf-8") as f:
            d = json.load(f)
        d["rules"] = "0" * 40
        with open(self._cache, "w", encoding="utf-8") as f:
            json.dump(d, f)
        T.reset_cache()
        self.assertEqual(T._facts(self._rel).tier("C", "test_cham"), T.SLOW)
        T.flush_facts_cache()
        with open(self._cache, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["rules"], T._facts_rules_hash())


@meta
class TestModuleSelection(TmpCase):
    def test_plan_modules_matches_plan_counts(self):
        total, cnt, smoke_n = T.plan_counts()
        total2, cnt2, smoke_n2, mods = T.plan_modules(T.parse_tiers("full"))
        self.assertEqual((total, cnt, smoke_n), (total2, cnt2, smoke_n2))
        n_files = len([f for f in os.listdir(os.path.join(ROOT, "tests"))
                       if f.startswith("test") and f.endswith(".py")])
        self.assertEqual(len(mods), n_files)

    def test_smoke_modules_subset_and_some_skipped(self):
        _, _, _, smoke_mods = T.plan_modules(T.parse_tiers("smoke"))
        _, _, _, fast_mods = T.plan_modules(T.parse_tiers("fast"))
        _, _, _, full_mods = T.plan_modules(T.parse_tiers("full"))
        self.assertTrue(set(smoke_mods) <= set(fast_mods) <= set(full_mods))
        # Nạp chọn lọc thực sự bỏ được module: tầng fast không cần mọi module.
        self.assertLess(len(fast_mods), len(full_mods))
        self.assertLess(len(smoke_mods), len(fast_mods))

    def test_build_selective_matches_plan(self):
        # Đếm cây 2 lần để phát hiện cây đổi giữa chừng (nhóm khác đang sửa
        # file test song song): cây đổi thì bỏ qua so khớp số thay vì fail oan.
        total, cnt, smoke_n = T.plan_counts()
        sel, plan, _notes, _f, _tiers, _want, _ids = run_tests._build(["--tier", "smoke"])
        total2, cnt2, smoke_n2 = T.plan_counts()
        if (total, cnt, smoke_n) != (total2, cnt2, smoke_n2):
            self.skipTest("cây test đổi giữa chừng — bỏ qua so khớp số")
        self.assertEqual(len(sel), smoke_n)
        for t in sel:
            self.assertTrue(T.is_smoke_id(t.id()))
        self.assertIn("chạy %d" % smoke_n, plan)
        sel_f, _plan_f, _n2, _f2, _t2, _w2, _i2 = run_tests._build([])
        self.assertEqual(len(sel_f), cnt[T.FAST])


class TestProgressPipeSteps(TmpCase):
    def setUp(self):
        super().setUp()
        self._old_mode = color_mod._DEFAULT.mode
        color_mod.set_mode("off")

    def tearDown(self):
        color_mod.set_mode(self._old_mode)
        super().tearDown()

    def test_pipe_progress_steps_one_percent(self):
        # Luật #152: tiến độ tăng từng 1%, không nhảy cục — cả khi pipe/log.
        stream = io.StringIO()
        res = run_tests.R(stream, False, 0)
        res.total = 100
        res.eta_sums = [0.0] * 100
        res._tty = False
        fake = _FakeTest("tests.test_x.C.test_short_name")
        for _ in range(100):
            res.startTest(fake)
            res.addSuccess(fake)
            res.stopTest(fake)
        lines = [ln for ln in stream.getvalue().splitlines() if ln.startswith("[")]
        pcts = [int(re.search(r"(\d+)%", ln).group(1)) for ln in lines]
        self.assertEqual(pcts, list(range(1, 101)))
        self.assertNotIn("\x1b", stream.getvalue())


class TestWatchdogPhase(TmpCase):
    def test_setup_phase_visible_during_setup(self):
        holder = {}

        class WithSetup(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                holder["phase"] = getattr(holder.get("res"), "_phase", None)
                holder["phase_test"] = getattr(holder.get("res"), "_phase_test", None)

            def test_x(self):
                pass

        res = unittest.TestResult()
        holder["res"] = res
        run_tests.ThreadedSuite([WithSetup("test_x")]).run(res)
        self.assertEqual(holder["phase"], "setUpClass")
        self.assertIn("test_x", holder["phase_test"])
        self.assertEqual(res.errors, [])
        self.assertEqual(res.testsRun, 1)

    def test_phase_cleared_when_test_body_starts(self):
        stream = io.StringIO()
        res = run_tests.R(stream, False, 0)
        res.total = 1
        res.eta_sums = [0.0]
        res._tty = False

        class Plain(unittest.TestCase):
            def test_x(self):
                pass

        run_tests.ThreadedSuite([Plain("test_x")]).run(res)
        self.assertEqual(res._phase, "")
        self.assertEqual(res.testsRun, 1)


@meta
class TestOutputFailure(TmpCase):
    """Đĩa đầy giữa chừng không được giết bộ chạy: ghi tiến độ/báo cáo lỗi
    ra stream hỏng (ENOSPC) phải nuốt lỗi, suite vẫn chạy hết + mã thoát
    vẫn theo kết quả test. Bài học 2026-11-16: /tmp đầy làm lần full chết
    ở test ~151 vì write() tiến độ không được bắt."""

    def test_broken_stream_does_not_kill_run(self):
        class BadStream(io.StringIO):
            def write(self, s):
                raise OSError(28, "No space left on device")

            def flush(self):
                raise OSError(28, "No space left on device")

        stream = BadStream()
        res = run_tests.R(stream, False, 0)
        res.total = 2
        res.eta_sums = [0.0, 0.0]
        res._tty = False

        def ok():
            return True

        def bad():
            raise AssertionError("sai rồi")

        def _case(name, body):
            def runTest(self):
                body()
            return type(name, (unittest.TestCase,), {"runTest": runTest})("runTest")

        run_tests.ThreadedSuite([_case("OkCase", ok), _case("BadCase", bad)]).run(res)
        self.assertEqual(res.testsRun, 2)
        self.assertEqual(len(res.failures), 1)
        self.assertEqual(len(res.errors), 0)

    def test_emit_swallows_oserror(self):
        class BadStream(io.StringIO):
            def write(self, s):
                raise OSError(28, "No space left on device")

            def flush(self):
                raise OSError(28, "No space left on device")

        run_tests._emit(BadStream(), "x")   # không raise


@meta
class TestFileRunKeepsProfile(TmpCase):
    def test_single_file_run_does_not_prune_profile(self):
        # QT-10: chạy một file (tier mặc định full) không được tỉa profile
        # về chỉ còn test của file đó — tỉa/demote chỉ xảy ra ở lần FULL thật
        # (không lọc file). Trước đây chạy 1 file cũng tỉa vì tier mặc định
        # là "full" nên record_times nhận is_full=True.
        dest = _copy_repo()
        try:
            os.makedirs(os.path.join(dest, "reports"), exist_ok=True)
            with open(os.path.join(dest, "reports", "test_times.json"), "w", encoding="utf-8") as f:
                json.dump({"test_gone.py::C::t": 0.1}, f)
            r = subprocess.run([sys.executable, "run_tests.py", "tests/test_tomlmini_config.py"],
                               cwd=dest, capture_output=True, text=True, timeout=subprocess_timeout(120))
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            with open(os.path.join(dest, "reports", "test_times.json"), encoding="utf-8") as f:
                after = json.load(f)
            self.assertIn("test_gone.py::C::t", after)
            self.assertGreater(len(after), 10)
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


class TestRanLineAndSummary(TmpCase):
    def test_ran_line_byte_identical_and_summary_table_plain(self):
        r = subprocess.run([sys.executable, "run_tests.py", "tests/test_tomlmini_config.py"],
                           cwd=ROOT, capture_output=True, text=True, timeout=subprocess_timeout(120))
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertNotIn("\x1b", r.stdout)
        m = RAN_RE.search(r.stdout)
        self.assertIsNotNone(m, "dòng Ran không giữ nguyên định dạng")
        self.assertIn("Tóm tắt", r.stdout)
        self.assertIn("Timeout", r.stdout)
        # Bảng tóm tắt vừa màn hình điện thoại: mỗi dòng <= 48 cột hiển thị.
        lines = r.stdout.splitlines()
        start = lines.index("Tóm tắt")
        for ln in lines[start:start + 12]:
            if ln.startswith(("MỚI CHẬM", "ĐÃ HỒI PHỤC", "VƯỢT NGÂN SÁCH", "LƯU Ý", "Đã lập mốc")):
                break
            self.assertLessEqual(display_width(ln), 48, ln)


def _copy_repo():
    """Sao chép repo để chạy subprocess cô lập. GIỮ __pycache__: bản sao không
    có bytecode phải biên dịch lại mọi module (~4,5s mỗi lần chạy con trên
    sandbox, còn đắt hơn trên iSH); .pyc được Python kiểm bằng mtime+size
    nguồn nên không sợ bytecode cũ (file nào test sửa trong bản sao thì
    biên dịch lại). Cũng sao chép bộ nhớ đệm _Facts để con khỏi phân tích
    lại file test (cache khóa bằng sha1 nội dung nên luôn đúng)."""
    dest = tempfile.mkdtemp(prefix="pb3-runner-")
    shutil.copytree(ROOT, os.path.join(dest, "pb3"),
                    ignore=shutil.ignore_patterns("reports", "logs", "workspace"))
    src_cache = os.path.join(ROOT, "reports", "test_facts_cache.json")
    if os.path.exists(src_cache):
        try:
            os.makedirs(os.path.join(dest, "pb3", "reports"), exist_ok=True)
            shutil.copy2(src_cache, os.path.join(dest, "pb3", "reports", "test_facts_cache.json"))
        except OSError:
            pass
    return os.path.join(dest, "pb3")


@meta
class TestCopyRepoWarm(TmpCase):
    def test_copy_keeps_bytecode_and_facts_cache(self):
        dest = _copy_repo()
        try:
            self.assertTrue(os.path.exists(os.path.join(dest, "tests", "__pycache__")),
                            "bản sao phải giữ __pycache__ để con khỏi biên dịch lại")
            if os.path.exists(os.path.join(ROOT, "reports", "test_facts_cache.json")):
                self.assertTrue(os.path.exists(
                    os.path.join(dest, "reports", "test_facts_cache.json")))
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


class TestTimeoutSubprocess(TmpCase):
    def test_timeout_is_error_no_baseline(self):
        dest = _copy_repo()
        try:
            with open(os.path.join(dest, "tests", "test_hang_tmp.py"), "w", encoding="utf-8") as f:
                f.write("import threading\nimport unittest\n\n\n"
                        "class TestHangTmp(unittest.TestCase):\n"
                        "    def test_hang_forever(self):\n"
                        "        threading.Event().wait(30)\n"
                        "    def test_after_hang_ok(self):\n"
                        "        self.assertTrue(True)\n")
            env = dict(os.environ)
            env["PB3_TEST_TIMEOUT_SCALE"] = "0.005"   # timeout 180s -> 0,9s (test treo 30s nên biên vẫn lớn)
            r = subprocess.run([sys.executable, "run_tests.py", "tests/test_hang_tmp.py"],
                               cwd=dest, capture_output=True, text=True, timeout=subprocess_timeout(120), env=env)
            self.assertEqual(r.returncode, 1, r.stdout[-2000:])
            self.assertIn("Ran 2 tests", r.stdout)
            self.assertIn("lỗi=1", r.stdout)
            self.assertIn("Timeout", r.stdout)
            self.assertIn("không lập mốc", r.stdout)
            self.assertFalse(os.path.exists(os.path.join(dest, "reports", "test_baseline.json")))
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


class TestCtrlC(TmpCase):
    def test_ctrl_c_saves_partial_and_reports_130(self):
        proc = subprocess.Popen([sys.executable, "run_tests.py", "--tier", "full"],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        try:
            # Chờ con nạp xong và in kế hoạch (đã vào vùng try/except Ctrl+C)
            # rồi mới gửi SIGINT. Gửi sớm lúc con còn import (trên iSH import
            # typing rất chậm) thì tiến trình chết bởi tín hiệu (-2) thay vì
            # thoát 130 — đã kiểm chứng bằng thực nghiệm.
            deadline = time.monotonic() + subprocess_timeout(300)
            ready = False
            while time.monotonic() < deadline:
                try:
                    r, _, _ = select.select([proc.stdout], [], [],
                                            max(0.0, deadline - time.monotonic()))
                except (OSError, ValueError):
                    break
                if not r:
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                if "TẦNG" in line:
                    ready = True
                    break
            self.assertTrue(ready, "con chưa in kế hoạch trước hạn")
            proc.send_signal(signal.SIGINT)
            out, _ = proc.communicate(timeout=subprocess_timeout(60))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 130, out[-2000:])
        self.assertIn("Đã hủy ở test:", out)
        self.assertIn("Đã chạy", out)
        self.assertIn("còn lại", out)


class TestChanged(TmpCase):
    def _baseline_in_copy(self, dest):
        r = subprocess.run([sys.executable, "-c",
                            "from tests.tiering import save_baseline; save_baseline()"],
                           cwd=dest, capture_output=True, text=True, timeout=subprocess_timeout(60))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_deleted_file_runs_full_tier_selection(self):
        dest = _copy_repo()
        try:
            self._baseline_in_copy(dest)
            base_path = os.path.join(dest, "reports", "test_baseline.json")
            with open(base_path, encoding="utf-8") as f:
                base = json.load(f)
            base["tests/test_gone.py"] = "0" * 64
            with open(base_path, "w", encoding="utf-8") as f:
                json.dump(base, f)
            r = subprocess.run([sys.executable, "run_tests.py", "--changed",
                                "--tier", "fast", "-k", "test_tomlmini"],
                               cwd=dest, capture_output=True, text=True, timeout=subprocess_timeout(120))
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            self.assertIn("file đã xóa", r.stdout)
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)

    def test_slow_meta_excluded_warning(self):
        dest = _copy_repo()
        try:
            self._baseline_in_copy(dest)
            with open(os.path.join(dest, "sdk", "unity.py"), "a", encoding="utf-8") as f:
                f.write("\n# thay đổi giả để kiểm --changed\n")
            r = subprocess.run([sys.executable, "run_tests.py", "--changed",
                                "--tier", "fast", "-k", "test_u13"],
                               cwd=dest, capture_output=True, text=True, timeout=subprocess_timeout(120))
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            self.assertIn("nằm ngoài tầng đang chạy", r.stdout)
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


class TestFailuresFile(TmpCase):
    def test_failures_saved_to_file(self):
        # Bộ chạy test phải lưu danh sách lỗi ra reports/failures.txt mỗi lần
        # chạy (iSH 23:36 mất danh sách vì chỉ in lên màn hình).
        dest = _copy_repo()
        try:
            with open(os.path.join(dest, "tests", "test_fail_tmp.py"), "w", encoding="utf-8") as f:
                f.write("import unittest\n\n\n"
                        "class TestFailTmp(unittest.TestCase):\n"
                        "    def test_noi_that_bai(self):\n"
                        "        self.assertEqual(1, 2, 'sai cố ý')\n")
            r = subprocess.run([sys.executable, "run_tests.py", "tests/test_fail_tmp.py"],
                               cwd=dest, capture_output=True, text=True,
                               timeout=subprocess_timeout(120))
            self.assertEqual(r.returncode, 1, r.stdout[-2000:])
            path = os.path.join(dest, "reports", "failures.txt")
            self.assertTrue(os.path.exists(path))
            txt = open(path, encoding="utf-8").read()
            self.assertIn("FAIL: tests.test_fail_tmp.TestFailTmp.test_noi_that_bai", txt)
            self.assertIn("AssertionError", txt)
            self.assertIn("Ran 1 tests", txt)
        finally:
            shutil.rmtree(os.path.dirname(dest), ignore_errors=True)


@meta
class TestSweepStaleTmpDirs(TmpCase):
    def test_sweeps_only_old_pb3test_dirs(self):
        old = os.path.join(self.tmp, "pb3test-cu")
        fresh = os.path.join(self.tmp, "pb3test-moi")
        other = os.path.join(self.tmp, "khac")
        for d in (old, fresh, other):
            os.mkdir(d)
        ancient = time.time() - 7200
        os.utime(old, (ancient, ancient))
        gone = run_tests._sweep_stale_tmp_dirs(root=str(self.tmp), max_age_s=3600)
        self.assertEqual(gone, 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(other))

    def test_shared_tmp_dir_registered_for_atexit_cleanup(self):
        # Thư mục dùng chung (setUpClass) phải đăng ký để atexit dọn nốt khi
        # lần chạy bị ngắt (tearDownClass không kịp chạy).
        from tests import helpers
        d = tempfile.mkdtemp(prefix="pb3test-reg-")
        try:
            helpers.register_tmp_dir(d)
            self.assertIn(d, helpers._TMP_DIRS)
            helpers.unregister_tmp_dir(d)
            self.assertNotIn(d, helpers._TMP_DIRS)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestTmpCleanup(TmpCase):
    def test_tmpcase_dir_removed_on_process_exit_without_teardown(self):
        # atexit trong tests/helpers.py dọn thư mục TmpCase khi tiến trình
        # thoát mà tearDown chưa chạy (test bị ngắt giữa chừng).
        code = ("import sys; sys.path.insert(0, %r); "
                "from tests.helpers import TmpCase; "
                "c = TmpCase(); c.setUp(); print(c._td, flush=True)"
                % ROOT)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=subprocess_timeout(60), cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = r.stdout.strip().splitlines()[-1].strip()
        self.assertTrue(d.startswith(os.path.join(tempfile.gettempdir(), "pb3test-")), d)
        self.assertFalse(os.path.exists(d))


if __name__ == "__main__":
    unittest.main()
