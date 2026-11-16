import os
import unittest
from pathlib import Path

from tests import helpers
from tests.helpers import TmpCase
from core import paths
from core.errors import PB3Error


class TestSafeRelpath(unittest.TestCase):
    def bad(self, rel):
        with self.assertRaises(PB3Error) as cm:
            paths.safe_relpath(rel)
        self.assertEqual(cm.exception.code, "E_UNSAFE_PATH", rel)

    def test_rejects_dangerous(self):
        for rel in ["", "/etc/passwd", "../x", "a/../../x", "a/..", "..\\x", "C:\\Windows", "c:/x",
                    "a\x00b", "a/b\x01c", "\\\\server\\share", "./", "///"]:
            self.bad(rel)

    def test_rejects_too_long(self):
        self.bad("a" * 300)
        self.bad("/".join(["a" * 100] * 12))

    def test_accepts_unicode_and_spaces(self):
        self.assertEqual(paths.safe_relpath("Dữ liệu/中文 文件.json"), ("Dữ liệu", "中文 文件.json"))
        self.assertEqual(paths.safe_relpath("a\\b/c"), ("a", "b", "c"))
        self.assertEqual(paths.safe_relpath("./a//b/./c"), ("a", "b", "c"))

    def test_nfc_normalization(self):
        decomposed = "e\u0302"      # e + dấu mũ
        self.assertEqual(paths.safe_relpath("x/" + decomposed)[1], "\u00ea")

    def test_byte_length_limit_counts_utf8(self):
        self.assertEqual(len(paths.safe_relpath("中" * 80)[0]), 80)    # 240 byte: được
        self.bad("中" * 90)                                            # 270 byte: quá dài


class TestResolveInside(TmpCase):
    def test_normal(self):
        p = paths.resolve_inside(self.tmp, "Payload/Game.app/x.bin")
        self.assertEqual(p, self.tmp / "Payload" / "Game.app" / "x.bin")

    def test_blocks_escape(self):
        for rel in ["../x", "/abs", "a/../../x"]:
            with self.assertRaises(PB3Error):
                paths.resolve_inside(self.tmp, rel)

    def test_blocks_symlink_escape(self):
        if not helpers.can_symlink(self.tmp):
            self.skipTest("hệ thống không hỗ trợ symlink")
        outside = self.tmp / "outside"
        outside.mkdir()
        base = self.tmp / "base"
        base.mkdir()
        os.symlink(str(outside), str(base / "link"))
        with self.assertRaises(PB3Error) as cm:
            paths.resolve_inside(base, "link/evil.txt")
        self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")

    def test_allows_symlink_inside(self):
        if not helpers.can_symlink(self.tmp):
            self.skipTest("hệ thống không hỗ trợ symlink")
        base = self.tmp / "base"
        (base / "real").mkdir(parents=True)
        os.symlink(str(base / "real"), str(base / "alias"))
        self.assertEqual(paths.resolve_inside(base, "alias/f.txt"), base / "alias" / "f.txt")

    def test_is_within(self):
        self.assertTrue(paths.is_within(self.tmp, self.tmp / "a" / "b"))
        self.assertFalse(paths.is_within(self.tmp / "a", self.tmp / "ab"))
        self.assertFalse(paths.is_within(self.tmp / "a", self.tmp))


class TestPaths(TmpCase):
    def test_root_is_derived_from_source_not_cwd(self):
        cwd = os.getcwd()
        try:
            os.chdir(str(self.tmp))
            self.assertEqual(paths.Paths().root, helpers.ROOT.resolve())
        finally:
            os.chdir(cwd)

    def test_layout_and_ensure_dirs(self):
        p = paths.Paths(self.tmp)
        self.assertEqual(p.output, self.tmp / "workspace" / "output")
        self.assertEqual(p.ensure_dirs(), [])
        for d in (p.input, p.temp, p.output, p.cache, p.logs, p.reports, p.kb):
            self.assertTrue(d.is_dir(), d)

    def test_ensure_dirs_reports_failure_without_raising(self):
        (self.tmp / "logs").write_text("tôi là tệp, không phải thư mục")
        failed = paths.Paths(self.tmp).ensure_dirs()
        self.assertTrue(any(str(d).endswith("logs") for d, _ in failed))

    def test_configure_rejects_unsafe(self):
        p = paths.Paths(self.tmp)
        with self.assertRaises(PB3Error):
            p.configure(workspace="../outside", logs="logs", reports="reports", kb="kb")
        with self.assertRaises(PB3Error):
            p.configure(workspace="/abs", logs="logs", reports="reports", kb="kb")

    def test_expected_root_flag(self):
        self.assertFalse(paths.Paths(self.tmp).at_expected_root())

    def test_rel(self):
        p = paths.Paths(self.tmp)
        self.assertEqual(p.rel(p.output / "x"), os.path.join("workspace", "output", "x"))


if __name__ == "__main__":
    unittest.main()
