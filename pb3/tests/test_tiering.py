"""Kiểm cơ chế phân tầng test (tests/tiering.py) và chốt chặn để bộ NHANH không phình dần."""
import collections
import os
import subprocess
import unittest

from tests import tiering as T

SAMPLE = '''
import time
import unittest
from tests.helpers import copy_project, read_src, slow_subprocess


def run_proc():
    return subprocess.run(["x"])


class TestPlain(unittest.TestCase):
    def test_pure(self):
        self.assertEqual(1, 1)

    def test_text_in_string_is_ignored(self):
        code = """
def hang():
    time.sleep(5)
subprocess.run(["x"])
"""
        self.assertTrue(code)

    def test_direct_sleep(self):
        time.sleep(0.1)

    def test_module_helper(self):
        run_proc()

    def test_reads_source(self):
        read_src("a.py")


class TestHelperScope(unittest.TestCase):
    def _go(self):
        return subprocess.Popen(["x"])

    def test_uses_helper(self):
        self._go()

    def test_other(self):
        pass


class TestOtherScope(unittest.TestCase):
    def _go(self):
        return 1

    def test_same_name_helper_is_not_slow_here(self):
        self._go()


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.p = copy_project(self.tmp)

    def test_any(self):
        pass


@slow_subprocess
class TestDecorated(unittest.TestCase):
    def test_any(self):
        pass


class TestDevice(unittest.TestCase):
    def test_symlink(self):
        os.symlink("a", "b")

    def test_slow_and_device_goes_to_ish(self):
        os.symlink("a", "b")
        subprocess.run(["x"])


class TestMarked(unittest.TestCase):
    @smoke
    def test_marked_smoke(self):
        pass

    @slow
    def test_marked_slow(self):
        pass

    @meta
    def test_marked_meta(self):
        pass
'''


class TestClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = T._Facts(SAMPLE)

    def tier(self, cls, name):
        return self.f.tier(cls, name)

    def test_plain_is_fast(self):
        self.assertEqual(self.tier("TestPlain", "test_pure"), T.FAST)

    def test_text_inside_triple_quoted_string_is_ignored(self):
        self.assertEqual(self.tier("TestPlain", "test_text_in_string_is_ignored"), T.FAST)

    def test_direct_sleep_and_module_helper_are_slow(self):
        self.assertEqual(self.tier("TestPlain", "test_direct_sleep"), T.SLOW)
        self.assertEqual(self.tier("TestPlain", "test_module_helper"), T.SLOW)

    def test_source_scanning_is_meta(self):
        self.assertEqual(self.tier("TestPlain", "test_reads_source"), T.META)

    def test_helper_scope_is_per_class(self):
        self.assertEqual(self.tier("TestHelperScope", "test_uses_helper"), T.SLOW)
        self.assertEqual(self.tier("TestHelperScope", "test_other"), T.FAST)
        self.assertEqual(self.tier("TestOtherScope", "test_same_name_helper_is_not_slow_here"), T.FAST)

    def test_slow_setup_or_decorator_taints_whole_class(self):
        self.assertEqual(self.tier("TestSetup", "test_any"), T.SLOW)
        self.assertEqual(self.tier("TestDecorated", "test_any"), T.SLOW)

    def test_device_tests_are_ish_and_ish_beats_slow(self):
        self.assertEqual(self.tier("TestDevice", "test_symlink"), T.ISH)
        self.assertEqual(self.tier("TestDevice", "test_slow_and_device_goes_to_ish"), T.ISH)

    def test_smoke_decorator_is_detected(self):
        self.assertTrue(self.f.smoke_marked("TestMarked", "test_marked_smoke"))
        self.assertFalse(self.f.smoke_marked("TestPlain", "test_pure"))

    def test_manual_markers(self):
        self.assertEqual(self.tier("TestMarked", "test_marked_slow"), T.SLOW)
        self.assertEqual(self.tier("TestMarked", "test_marked_meta"), T.META)


class TestOverridesAndProfile(unittest.TestCase):
    def setUp(self):
        T.reset_cache()
        self.addCleanup(T.reset_cache)
        T._FACTS["x.py"] = T._Facts(SAMPLE)

    def set_ctx(self, ov=None, prof=None, budget=1.0):
        T._CTX.pop("memo", None)
        T._CTX.update(ov=ov or {}, prof=prof or {}, budget=budget, smoke=[])

    def test_longest_prefix_override_wins(self):
        self.set_ctx(ov={"x.py::TestPlain": T.SLOW, "x.py::TestPlain::test_pure": T.FAST})
        self.assertEqual(T.classify("x.py", "TestPlain", "test_pure"), T.FAST)
        self.assertEqual(T.classify("x.py", "TestPlain", "test_other_name"), T.SLOW)

    def test_override_can_rescue_false_positive(self):
        self.set_ctx(ov={"x.py::TestPlain::test_direct_sleep": T.FAST})
        self.assertEqual(T.classify("x.py", "TestPlain", "test_direct_sleep"), T.FAST)

    def test_measured_time_over_budget_promotes_to_slow(self):
        self.set_ctx(prof={"x.py::TestPlain::test_pure": 2.5}, budget=1.0)
        self.assertEqual(T.classify("x.py", "TestPlain", "test_pure"), T.SLOW)
        self.set_ctx(prof={"x.py::TestPlain::test_pure": 0.2}, budget=1.0)
        self.assertEqual(T.classify("x.py", "TestPlain", "test_pure"), T.FAST)

    def test_split_id_both_styles_and_unknown(self):
        self.assertEqual(T.split_id("tests/test_a.py::C::t"), ("test_a.py", "C", "t"))
        self.assertEqual(T.split_id("tests/test_a.py::C::t[param]"), ("test_a.py", "C", "t"))
        self.assertEqual(T.split_id("tests.test_a.C.t"), ("test_a.py", "C", "t"))
        self.assertIsNone(T.split_id("unittest.loader._FailedTest.tests.test_a"))
        self.assertEqual(T.classify_id("unittest.loader._FailedTest.tests.test_a"), T.FAST)   # lỗi import luôn chạy


class TestTierSelection(unittest.TestCase):
    def setUp(self):
        T.reset_cache()
        self.addCleanup(T.reset_cache)
        T._FACTS["x.py"] = T._Facts(SAMPLE)

    def test_parse_tiers(self):
        self.assertEqual(T.parse_tiers("fast"), {T.FAST})
        self.assertEqual(T.parse_tiers("fast, ish"), {T.FAST, T.ISH})
        self.assertTrue(T.is_full(T.parse_tiers("full")))
        self.assertFalse(T.is_full(T.parse_tiers("fast,ish")))
        for bad in ("", "nhanh", "fast,xyz"):
            with self.assertRaises(ValueError):
                T.parse_tiers(bad)

    def test_selection_rules(self):
        self.assertTrue(T.is_selected({T.FAST}, T.FAST, False))
        self.assertFalse(T.is_selected({T.FAST}, T.ISH, False))
        self.assertTrue(T.is_selected({T.SMOKE}, T.FAST, True))
        self.assertFalse(T.is_selected({T.SMOKE}, T.FAST, False))
        self.assertTrue(T.is_selected({T.SMOKE, T.ISH}, T.ISH, False))

    def test_smoke_must_be_fast(self):
        T._CTX.update(ov={}, prof={}, budget=1.0, smoke=["x.py::TestPlain::", "x.py::TestDevice::"])
        self.assertTrue(T.is_smoke("x.py", "TestPlain", "test_pure"))
        self.assertFalse(T.is_smoke("x.py", "TestPlain", "test_direct_sleep"))      # chậm: không được vào smoke
        self.assertFalse(T.is_smoke("x.py", "TestDevice", "test_symlink"))          # ish: không được vào smoke
        self.assertFalse(T.is_smoke("x.py", "TestOtherScope", "test_same_name_helper_is_not_slow_here"))

    def test_registry_is_consistent(self):
        self.assertEqual(len(T.TIER_NAMES), len(set(T.TIER_NAMES)))
        for tier, _rx in T.RULES:
            self.assertIn(tier, T.PARTITIONS)
        self.assertNotIn(T.SMOKE, T.PARTITIONS)
        self.assertEqual(sum(1 for t in T.TIERS if t[2]), 1, "đúng một tầng chạy mặc định hằng ngày")


class TestFactsView(unittest.TestCase):
    """Bản đọc nhẹ _Facts từ cache đĩa: trả đúng tầng/cờ smoke đã lưu; test
    không có trong cache thì coi như fast/không smoke (như _Facts gốc)."""

    def test_view_returns_cached_tier_and_smoke(self):
        v = T._FactsView({"C::test_cham": (T.SLOW, False),
                          "C::test_nhanh": (T.FAST, True)})
        self.assertEqual(v.tier("C", "test_cham"), T.SLOW)
        self.assertEqual(v.tier("C", "test_nhanh"), T.FAST)
        self.assertTrue(v.smoke_marked("C", "test_nhanh"))
        self.assertFalse(v.smoke_marked("C", "test_cham"))
        self.assertEqual(v.tier("C", "test_khong_co"), T.FAST)
        self.assertFalse(v.smoke_marked("C", "test_khong_co"))
        self.assertIn(("C", "test_cham"), v.blocks)
        self.assertIn(("C", "test_nhanh"), v.blocks)


class TestImpact(unittest.TestCase):
    def setUp(self):
        T.reset_cache()
        self.addCleanup(T.reset_cache)

    def test_nothing_changed_selects_nothing(self):
        self.assertFalse(T.is_affected("test_registry.py", T.FAST, set()))

    def test_source_change_selects_only_dependents(self):
        ch = {"sdk/ir.py"}
        self.assertTrue(T.is_affected("test_ir.py", T.FAST, ch))
        self.assertFalse(T.is_affected("test_registry.py", T.FAST, ch))

    def test_indirect_dependency_is_followed(self):
        # test_registry nạp registry/loader.py; loader import core.errors -> đổi errors.py phải chọn cả hai
        self.assertTrue(T.is_affected("test_registry.py", T.FAST, {"registry/loader.py"}))
        self.assertTrue(T.is_affected("test_registry.py", T.FAST, {"core/errors.py"}))

    def test_own_test_file_change_selects_it(self):
        self.assertTrue(T.is_affected("test_ir.py", T.FAST, {"tests/test_ir.py"}))
        self.assertFalse(T.is_affected("test_registry.py", T.FAST, {"tests/test_ir.py"}))

    def test_shared_helper_change_selects_importers(self):
        self.assertTrue(T.is_affected("test_registry.py", T.FAST, {"tests/helpers.py"}))

    def test_data_or_infra_change_selects_everything(self):
        self.assertTrue(T.is_affected("test_ir.py", T.FAST, {"config/default.toml"}))
        self.assertTrue(T.is_affected("test_ir.py", T.FAST, {"tests/tiers.json"}))

    def test_slow_tier_depends_on_all_source(self):
        self.assertTrue(T.is_affected("test_plugin_toggle.py", T.SLOW, {"sdk/ir.py"}))
        self.assertFalse(T.is_affected("test_plugin_toggle.py", T.SLOW, {"tests/test_ir.py"}))


class TestFastTierStaysHealthy(unittest.TestCase):
    """Chốt chặn: nếu bộ nhanh co lại quá nhiều thì phân tầng đang sai (hoặc ai đó viết toàn test nặng)."""

    def counts(self):
        T.reset_cache()
        self.addCleanup(T.reset_cache)
        c = collections.Counter()
        for p in sorted(T.TESTS.glob("test_*.py")):
            f = T._facts(p.name)
            for (cls, name) in f.blocks:
                if cls and name.startswith("test"):
                    c[T.classify(p.name, cls, name)] += 1
        return c

    def test_every_test_file_is_classifiable_and_fast_is_the_majority(self):
        c = self.counts()
        total = sum(c.values())
        self.assertGreater(total, 100)
        self.assertGreaterEqual(c[T.FAST] * 100 // total, 70,
                                "bộ nhanh chỉ còn %d/%d test; kiểm tests/tiers.json và dấu hiệu chậm" % (c[T.FAST], total))

    def test_smoke_set_is_small_real_and_fast(self):
        T.reset_cache()
        self.addCleanup(T.reset_cache)
        n = 0
        for p in sorted(T.TESTS.glob("test_*.py")):
            for (cls, name) in T._facts(p.name).blocks:
                if cls and name.startswith("test") and T.is_smoke(p.name, cls, name):
                    n += 1
        self.assertGreaterEqual(n, 20, "smoke quá nhỏ: không còn bao phủ các lớp then chốt")
        self.assertLessEqual(n, 80, "smoke phình quá 80 test: không còn là vài giây")
        for prefix in T.load_smoke_prefixes():
            rel = prefix.split("::")[0]
            self.assertTrue((T.TESTS / rel).exists(), "tiers.json smoke: không có file %s" % rel)
            self.assertTrue(any(k[0] == prefix.split("::")[1] for k in T._facts(rel).blocks),
                            "tiers.json smoke: không có lớp trong %s" % prefix)

    def test_override_file_only_names_real_tests(self):
        for prefix in T.load_overrides():
            rel = prefix.split("::")[0]
            self.assertTrue((T.TESTS / rel).exists(), "tiers.json: không có file %s" % rel)
            if prefix.count("::") == 2:
                _, cls, name = prefix.split("::")
                self.assertIn((cls, name), T._facts(rel).blocks, "tiers.json: không có test %s" % prefix)


if __name__ == "__main__":
    unittest.main()
