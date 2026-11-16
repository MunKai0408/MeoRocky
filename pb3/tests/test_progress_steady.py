"""Test tiến độ thật, tăng dần từng 1% (luật #152) + mẫu một dòng thống nhất
(luật #153): core.progress.eta_seconds / step_pcts / SteadyProgress.

Tầng fast: thuần bộ nhớ, đồng hồ giả, stream giả (không tiến trình con).
"""
import io
import re
import unittest

from core.progress import (SteadyProgress, display_width, eta_seconds,
                           format_progress, step_pcts)


class _FakeTTY:
    """Stream giả có isatty()=True để vẽ lại bằng '\\r'."""

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


def _pcts(text: str):
    """Mọi % xuất hiện theo thứ tự trong text đã vẽ (mỗi dòng '\\r' một lần)."""
    out = []
    for part in re.split(r"[\r\n]", text):
        m = re.search(r"(\d+)%", part)
        if m:
            out.append(int(m.group(1)))
    return out


class TestEtaSeconds(unittest.TestCase):
    def test_half_done(self):
        self.assertEqual(eta_seconds(10.0, 0.5), 10.0)

    def test_quarter_done(self):
        self.assertEqual(eta_seconds(30.0, 0.25), 90.0)

    def test_no_basis_is_none(self):
        self.assertIsNone(eta_seconds(10.0, 0.0))
        self.assertIsNone(eta_seconds(10.0, 1.0))
        self.assertIsNone(eta_seconds(10.0, 0.01))
        self.assertIsNone(eta_seconds(0.0, 0.5))
        self.assertIsNone(eta_seconds(-5.0, 0.5))

    def test_formula(self):
        # ETA = elapsed / frac * (1 - frac).
        self.assertAlmostEqual(eta_seconds(5.0, 0.9), 5.0 * 0.1 / 0.9)


class TestStepPcts(unittest.TestCase):
    def test_one_percent_steps(self):
        self.assertEqual(step_pcts(0, 5), [1, 2, 3, 4, 5])
        self.assertEqual(step_pcts(60, 63), [61, 62, 63])

    def test_no_step_when_not_ahead(self):
        self.assertEqual(step_pcts(5, 5), [])
        self.assertEqual(step_pcts(10, 5), [])
        self.assertEqual(step_pcts(0, 0), [])

    def test_max_step_two(self):
        self.assertEqual(step_pcts(0, 5, max_step=2), [2, 4, 5])
        self.assertEqual(step_pcts(60, 65, max_step=2), [62, 64, 65])

    def test_steps_never_exceed_two(self):
        for last, new in ((0, 100), (3, 97), (60, 80)):
            seq = step_pcts(last, new)
            self.assertEqual(seq[-1], new)
            prev = last
            for pct in seq:
                self.assertGreater(pct, prev)
                self.assertLessEqual(pct - prev, 2)
                prev = pct


class TestSteadyProgress(unittest.TestCase):
    def _clock(self, start=0.0):
        now = [float(start)]
        return now, lambda: now[0]

    def test_sweep_one_percent_steps(self):
        now, clock = self._clock(0.0)
        tty = _FakeTTY()
        sp = SteadyProgress("dịch", 10, stream=tty, width=44, clock=clock, isatty=True)
        now[0] = 10.0
        sp.update(5)
        pcts = _pcts(tty.text())
        self.assertEqual(pcts, list(range(1, 51)))
        last = tty.text().split("\r")[-1]
        self.assertIn("50%", last)
        self.assertIn("5/10", last)
        self.assertIn("~10s", last)
        for part in re.split(r"[\r\n]", tty.text()):
            if part.strip():
                self.assertLessEqual(display_width(part), 44, part)

    def test_small_total_still_one_percent_steps(self):
        now, clock = self._clock(0.0)
        tty = _FakeTTY()
        sp = SteadyProgress("dịch", 4, stream=tty, width=44, clock=clock, isatty=True)
        now[0] = 4.0
        sp.update(1)
        pcts = _pcts(tty.text())
        self.assertEqual(pcts, list(range(1, 26)))
        self.assertIn("1/4", tty.text())

    def test_never_goes_back(self):
        now, clock = self._clock(0.0)
        tty = _FakeTTY()
        sp = SteadyProgress("dịch", 10, stream=tty, width=44, clock=clock, isatty=True)
        now[0] = 5.0
        sp.update(5)
        sp.update(3)   # số nhỏ hơn: không lùi
        sp.update(5)
        pcts = _pcts(tty.text())
        self.assertEqual(pcts, list(range(1, 51)))
        now[0] = 10.0
        sp.update(10)
        pcts = _pcts(tty.text())
        self.assertEqual(pcts, list(range(1, 101)))
        self.assertIn("10/10", tty.text())

    def test_non_tty_no_spam(self):
        now, clock = self._clock(0.0)
        buf = io.StringIO()
        sp = SteadyProgress("dịch", 10, stream=buf, width=44, clock=clock, isatty=False)
        now[0] = 1.0
        sp.update(5)
        now[0] = 2.0
        sp.update(10)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertLessEqual(len(lines), 2, buf.getvalue())

    def test_finish_shows_100(self):
        now, clock = self._clock(0.0)
        tty = _FakeTTY()
        sp = SteadyProgress("dịch", 10, stream=tty, width=44, clock=clock, isatty=True)
        now[0] = 5.0
        sp.update(5)
        sp.finish()
        self.assertIn("100%", tty.text())


class TestFormatProgressLine(unittest.TestCase):
    def test_unified_template_bar_pct_counts_eta(self):
        line = format_progress("dịch", 5, 10, 10.0, 44)
        self.assertIn("50%", line)
        self.assertIn("5/10", line)
        self.assertIn("~10s", line)
        self.assertIn("█", line)
        self.assertLessEqual(display_width(line), 44)

    def test_no_eta_before_basis(self):
        line = format_progress("dịch", 0, 10, 0.0, 44)
        self.assertNotIn("~", line)
        self.assertIn("0%", line)

    def test_line_fits_phone_width(self):
        for done, total, elapsed in ((1, 1000, 60.0), (999, 1000, 599.0), (0, 3, 0.0)):
            line = format_progress("dịch bảng JSON rất dài", done, total, elapsed, 44)
            self.assertLessEqual(display_width(line), 44, line)


if __name__ == "__main__":
    unittest.main()
