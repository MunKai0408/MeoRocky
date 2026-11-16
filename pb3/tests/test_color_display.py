"""Test meta cho màu sắc + bố cục hiển thị (đợt polish COLORS / FULL-SURFACE).

- Bảng màu duy nhất ở core/color.py; không mã ANSI rải rác nơi khác.
- Chỉ tô màu khi TTY (hoặc ui.color=on ép); NO_COLOR luôn tắt; khi tắt,
  output giữ nguyên byte (test assert output cũ vẫn pass).
"""
import io
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from core import color as C
from tests.helpers import ROOT
from tests.tiering import meta


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class FakePipe(io.StringIO):
    def isatty(self):
        return False


@meta
class TestColorEnablement(unittest.TestCase):
    def test_force_on_off(self):
        on = C.Color(force=True)
        self.assertIn("\x1b[", on.paint("PASS", "pass"))
        self.assertEqual(C.strip_ansi(on.paint("PASS", "pass")), "PASS")
        off = C.Color(force=False)
        self.assertEqual(off.paint("PASS", "pass"), "PASS")
        self.assertEqual(off.status("pass", "xong"), "xong")

    def test_no_color_wins_over_on(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(C.Color(mode="on").is_enabled())
            self.assertFalse(C.Color(mode="auto", stream=FakeTTY()).is_enabled())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(C.Color(mode="on").is_enabled())

    def test_auto_needs_tty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(C.Color(mode="auto", stream=FakeTTY()).is_enabled())
            self.assertFalse(C.Color(mode="auto", stream=FakePipe()).is_enabled())
            self.assertFalse(C.Color(mode="off", stream=FakeTTY()).is_enabled())

    def test_roles_have_distinct_codes(self):
        on = C.Color(force=True)
        codes = {r: on.paint("x", r) for r in ("pass", "fail", "skip", "warn", "error", "title")}
        self.assertEqual(len(set(codes.values()) - {"x"}), 5)   # pass/fail/skip-warn/error/title
        self.assertIn("\x1b[32m", codes["pass"])
        self.assertIn("\x1b[31m", codes["fail"])
        self.assertIn("\x1b[33m", codes["skip"])
        self.assertEqual(codes["skip"], codes["warn"])           # cùng vàng, theo bảng màu
        self.assertIn("\x1b[36m", codes["title"])

    def test_status_symbols(self):
        on = C.Color(force=True)
        self.assertTrue(on.status("pass", "xong").startswith("\x1b[32m✓ "))
        self.assertTrue(on.status("fail", "hỏng").startswith("\x1b[31m✗ "))
        self.assertTrue(on.status("skip", "bỏ").startswith("\x1b[33m↷ "))
        off = C.Color(force=False)
        self.assertEqual(off.status("fail", "hỏng"), "hỏng")

    def test_rule_empty_when_off(self):
        self.assertEqual(C.Color(force=False).rule(48), "")
        self.assertEqual(C.Color(force=True).rule(10), "─" * 10)

    def test_configure_from_config(self):
        C.configure_from_config({"ui.color": "off"})
        try:
            self.assertFalse(C.default_color().is_enabled())
            C.configure_from_config({"ui.color": "lạ"})
            self.assertEqual(C.default_color().mode, "auto")
            C.configure_from_config(None)
            self.assertEqual(C.default_color().mode, "auto")
        finally:
            C.set_mode("auto")

    def test_module_helpers_use_default(self):
        # Helper cấp module dùng bảng mặc định: tắt mặc định -> thuần,
        # bật mặc định -> có màu. (Bản cũ gọi C.Color(force=False) rồi bỏ —
        # không hề đổi bảng mặc định nên trên TTY thật vẫn ra màu.)
        old = C.default_color().mode
        try:
            with mock.patch.dict(os.environ, {}, clear=True):
                C.set_mode("off")
                self.assertEqual(C.paint("x", "pass"), "x")
                self.assertEqual(C.title("Mục"), "Mục")
                C.set_mode("on")
                self.assertIn("\x1b[", C.paint("x", "pass"))
        finally:
            C.set_mode(old)


@meta
class TestLayoutHelpers(unittest.TestCase):
    def test_display_width_cjk_counts_two(self):
        self.assertEqual(C.display_width("ab"), 2)
        self.assertEqual(C.display_width("你好"), 4)
        self.assertEqual(C.display_width("\x1b[32mab\x1b[0m"), 2)

    def test_wrap_text_phone_width(self):
        text = "Kiểm tra bản mod trên iPhone của bạn trước khi bàn giao cho người khác"
        lines = C.wrap_text(text, 30)
        self.assertTrue(all(C.display_width(ln) <= 30 for ln in lines))
        self.assertEqual(" ".join(lines), text)

    def test_truncate_text(self):
        long = "a" * 50
        out = C.truncate_text(long, 10)
        self.assertEqual(C.display_width(out), 10)
        self.assertTrue(out.endswith("…"))

    def test_kv_table_plain_and_colored(self):
        rows = [("Tổng", "699"), ("Đạt", "699"), ("Thời gian", "15.3s")]
        plain = C.Color(force=False).kv_table(rows, width=48)
        self.assertEqual(plain, ["Tổng     : 699", "Đạt      : 699", "Thời gian: 15.3s"])
        self.assertTrue(all("\x1b[" not in ln for ln in plain))
        colored = C.Color(force=True).kv_table(rows, width=48)
        self.assertTrue(any("\x1b[36m" in ln for ln in colored))
        self.assertEqual([C.strip_ansi(ln) for ln in colored], plain)


@meta
class TestNoStrayAnsi(unittest.TestCase):
    """Không mã màu ANSI nào ngoài core/color.py (trừ test): mọi chỗ in màu
    phải qua bảng chung. Cho phép \\033[K (xóa dòng, không phải màu)."""

    def test_no_raw_color_codes_outside_color_module(self):
        bad = []
        pat = re.compile(r"\\x1b\[[0-9;]*m|\\u001b\[[0-9;]*m|\x1b\[[0-9;]*m")
        for p in sorted(ROOT.rglob("*.py")):
            rel = str(p.relative_to(ROOT))
            if rel.startswith("tests/") or rel in ("core/color.py",):
                continue
            if "legacy" in p.parts:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if pat.search(text):
                bad.append(rel)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
