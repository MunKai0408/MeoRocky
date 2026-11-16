import io
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from core.color import strip_ansi
from tests.helpers import TmpCase
from tests.tiering import timeout_scale
from core.app import bootstrap
from core.errors import PB3Error
from core.keepalive import (KeepAlive, KeepAliveStatus, S_ERROR, S_OFF, S_RUNNING, S_STOPPED,
                            S_UNAVAILABLE)
from core.overall import (OverallProgress, format_overall, load_history, save_history)
from core.progress import CancelToken, display_width


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(stages=None, clock=None, **kw):
    out = FakeTTY()
    clk = clock or Clock()
    kw.setdefault("heartbeat", False)
    kw.setdefault("ascii_only", True)
    ov = OverallProgress("job", stages or [("A", 10.0), ("B", 10.0)], stream=out, width=44, clock=clk, **kw)
    return ov, out, clk


class TestFormat(unittest.TestCase):
    def test_width_never_exceeded_and_content(self):
        # Trên iSH (timeout_scale > 1) chỉ quét 17 độ rộng đại diện (cách 5
        # + các mốc 10/44/80) thay vì đủ 71 độ rộng: 1704 lần format_overall
        # mất 14.5s trên iSH (bằng chứng 01:52); tính chất "không vượt width"
        # giữ nguyên trên từng độ rộng, đủ 71 độ rộng vẫn chạy trong sandbox.
        widths = range(10, 81)
        if timeout_scale() > 1:
            widths = sorted(set(range(10, 81, 5)) | {10, 44, 80})
        for w in widths:
            for label in ("x", "2/3 Chạy lệnh rất dài rất dài rất dài", "中文标签很长很长很长"):
                for ascii_only in (True, False):
                    for eta in (None, 0, 75, 4000):
                        ln = format_overall(label, 0.456, eta, w, ascii_only)
                        self.assertLessEqual(display_width(ln), w, (w, label, ln))
        ln = format_overall("buoc", 0.5, 130, 44, True)
        plain = strip_ansi(ln)   # trên TTY thật thanh tiến độ có mã ANSI xen vào
        self.assertIn(" 50%", plain)
        self.assertIn("còn ~2m10s", plain)
        self.assertIn("#####-----", plain)
        self.assertIn("còn ?", format_overall("b", 0.1, None, 44, True))
        done = format_overall("job", 1.0, 0, 44, True, finished=True, total_s=65)
        self.assertIn("100%", done)
        self.assertIn("xong 1m05s", done)


class TestFractionAndEta(unittest.TestCase):
    def test_monotonic_and_never_100_before_finish(self):
        ov, out, clk = make()
        ov.start()
        ov.begin("A")
        last = 0.0
        for _ in range(60):
            clk.t += 2.0
            f = ov.fraction()
            self.assertGreaterEqual(f, last)
            self.assertLess(f, 1.0)
            last = f
        ov.end()
        ov.begin("B")
        clk.t += 1000
        self.assertLess(ov.fraction(), 1.0)
        ov.finish()
        self.assertEqual(ov.fraction(), 1.0)

    def test_explicit_progress_and_eta(self):
        ov, out, clk = make([("A", 10.0), ("B", 10.0)])
        ov.start()
        ov.begin("A")
        clk.t += 5.0
        ov.update(0.5)                         # thật: 50% sau 5s -> còn 5s, rồi B 10s
        self.assertAlmostEqual(ov.fraction(), 0.25, places=2)
        self.assertAlmostEqual(ov.eta(), 15.0, places=1)
        ov.update(0.2)                         # không lùi
        self.assertAlmostEqual(ov.fraction(), 0.25, places=2)
        ov.update(done=9, total=10)
        self.assertAlmostEqual(ov.fraction(), 0.45, places=2)

    def test_opaque_stage_eta_counts_down_then_stays_small(self):
        ov, out, clk = make([("A", 10.0)])
        ov.start()
        ov.begin("A")
        e0 = ov.eta()
        clk.t += 4.0
        self.assertLess(ov.eta(), e0)
        clk.t += 100.0                         # quá dự kiến: không bịa số lớn, không âm
        self.assertGreaterEqual(ov.eta(), 0.0)
        self.assertLess(ov.eta(), 5.0)

    def test_mark_done_and_dynamic_stage(self):
        ov, out, clk = make([("Boot", 1.0), ("Run", 4.0)])
        ov.mark_done("Boot", 0.3)
        ov.start()
        ov.begin("Run")
        ov.begin("Extra", 2.0)                 # bước không khai báo: thêm động, không lùi thanh
        self.assertEqual(len(ov._stages), 3)
        f1 = ov.fraction()
        clk.t += 1.0
        self.assertGreaterEqual(ov.fraction(), f1)

    def test_expect_changes_current_stage(self):
        ov, out, clk = make([("A", 2.0)])
        ov.start()
        ov.begin("A")
        ov.expect(100.0)
        clk.t += 10.0
        self.assertGreater(ov.eta(), 80.0)

    def test_cancel_raises_in_update(self):
        tok = CancelToken()
        ov, out, clk = make(cancel=tok)
        ov.start()
        ov.begin("A")
        tok.cancel()
        with self.assertRaises(PB3Error) as cm:
            ov.update(0.1)
        self.assertEqual(cm.exception.code, "E_CANCELLED")


class TestRendering(unittest.TestCase):
    def setUp(self):
        # kết quả không được phụ thuộc biến PB3_PROGRESS của máy đang chạy
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("PB3_PROGRESS", None)

    def test_tty_draw_finish_and_abort(self):
        ov, out, clk = make()
        ov.start()
        ov.begin("A")
        clk.t += 3
        ov.render()
        s = out.getvalue()
        self.assertIn("\r", s)
        self.assertIn("1/2", s)
        self.assertIn("còn ~", s)
        ov.finish()
        end = out.getvalue()
        self.assertTrue(end.endswith("\n"))
        self.assertIn("100%", end.splitlines()[-1].split("\r")[-1])
        self.assertIn("xong", end)

        ov2, out2, clk2 = make()
        ov2.start()
        ov2.begin("A")
        ov2.abort()
        self.assertTrue(out2.getvalue().endswith("\n"))
        self.assertNotIn("100%", out2.getvalue())
        ov2.abort()                            # idempotent

    def test_non_tty_is_silent(self):
        out = io.StringIO()
        ov = OverallProgress("job", [("A", 1.0)], stream=out, heartbeat=False)
        self.assertFalse(ov.enabled)
        ov.start()
        ov.begin("A")
        ov.update(0.5)
        ov.finish()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(ov.fraction(), 1.0)

    def test_env_override(self):
        old = os.environ.get("PB3_PROGRESS")
        try:
            os.environ["PB3_PROGRESS"] = "1"
            out = io.StringIO()
            ov = OverallProgress("job", [("A", 1.0)], stream=out, heartbeat=False, ascii_only=True)
            self.assertTrue(ov.enabled)
            ov.start()
            ov.finish()
            self.assertIn("100%", out.getvalue())          # không tty -> in dòng thưa, không '\r'
            self.assertNotIn("\r", out.getvalue())
            os.environ["PB3_PROGRESS"] = "0"
            self.assertFalse(OverallProgress("job", [("A", 1.0)], stream=FakeTTY()).enabled)
        finally:
            if old is None:
                os.environ.pop("PB3_PROGRESS", None)
            else:
                os.environ["PB3_PROGRESS"] = old

    def test_message_clears_bar_line(self):
        ov, out, clk = make()
        ov.start()
        ov.begin("A")
        ov.message("thong bao")
        s = out.getvalue()
        i = s.index("thong bao")
        self.assertIn("\r", s[:i])
        self.assertTrue(s[i:].startswith("thong bao\n"))
        self.assertIn("\r", s[i:])                         # vẽ lại sau thông báo

    def test_stage_context_error_leaves_not_done(self):
        ov, out, clk = make()
        ov.start()
        with self.assertRaises(ValueError):
            with ov.stage("A"):
                raise ValueError("x")
        self.assertEqual(ov._stages[0].state, "running")
        ov.abort()

    def test_heartbeat_thread_redraws_during_blocking_call(self):
        out = FakeTTY()
        ov = OverallProgress("job", [("A", 30.0)], stream=out, width=44, ascii_only=True,
                             heartbeat_s=0.1)
        before = set(t for t in threading.enumerate() if t.name == "pb3-overall")
        ov.start()
        ov.begin("A")
        time.sleep(0.45)                                   # "lệnh" đang treo, không ai gọi update
        n = out.getvalue().count("\r")
        ov.finish()
        self.assertGreaterEqual(n, 3)
        mine = [t for t in threading.enumerate() if t.name == "pb3-overall" and t.is_alive() and t not in before]
        self.assertFalse(mine)

    def test_guard_also_protects_stderr_writes(self):
        real_err = FakeTTY()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), real_err
        try:
            ov = OverallProgress("job", [("A", 10.0)], width=44, heartbeat=False, ascii_only=True)
            self.assertTrue(ov.enabled)                    # stderr là tty giả
            ov.start()
            ov.begin("A")
            with ov.guard_stdout():
                sys.stderr.write("plugin: x\n")
            self.assertIs(sys.stderr, real_err)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        s = real_err.getvalue()
        i = s.index("plugin: x")
        self.assertIn("\r", s[:i])                         # thanh bị xóa trước khi ghi
        self.assertTrue(s[i - 1] == "\r")                  # thông báo bắt đầu ở đầu dòng sạch
        ov.abort()

    def test_guard_stdout_redraws_after_print(self):
        ov, out, clk = make()
        real = io.StringIO()
        old = sys.stdout
        sys.stdout = real
        try:
            ov.start()
            ov.begin("A")
            before = out.getvalue().count("\r")
            with ov.guard_stdout():
                print("dong ket qua")
            self.assertIs(sys.stdout, real)
        finally:
            sys.stdout = old
        self.assertIn("dong ket qua\n", real.getvalue())
        self.assertGreater(out.getvalue().count("\r"), before)
        ov.abort()


class TestHistory(TmpCase):
    def test_roundtrip_ema_and_garbage(self):
        p = self.tmp / "h.json"
        self.assertEqual(load_history(p), {})
        p.write_text("{khong phai json", encoding="utf-8")
        self.assertEqual(load_history(p), {})
        p.write_text('{"job": {"A": 4.0, "B": "x", "C": -1}, "j2": 5}', encoding="utf-8")
        self.assertEqual(load_history(p), {"job": {"A": 4.0}})
        self.assertTrue(save_history(p, {"job": {"A": 2.0}}))
        self.assertEqual(load_history(p), {"job": {"A": 2.0}})
        self.assertFalse(save_history(self.tmp / "khong_co" / "h.json", {"job": {"A": 1.0}}))

    def test_finish_learns_durations_abort_does_not(self):
        p = self.tmp / "h.json"
        clk = Clock()
        ov = OverallProgress("job", [("A", 10.0)], stream=FakeTTY(), clock=clk, history_path=p,
                             heartbeat=False, ascii_only=True)
        ov.start()
        ov.begin("A")
        clk.t += 4.0
        ov.finish()
        self.assertAlmostEqual(load_history(p)["job"]["A"], 4.0, places=2)
        ov2 = OverallProgress("job", [("A", 99.0)], stream=FakeTTY(), clock=clk, history_path=p,
                              heartbeat=False)
        self.assertAlmostEqual(ov2._stages[0].expected, 4.0, places=2)      # học từ lần trước
        ov2.start()
        ov2.begin("A")
        clk.t += 8.0
        ov2.finish()
        self.assertAlmostEqual(load_history(p)["job"]["A"], 6.0, places=2)   # EMA 0.5*8 + 0.5*4
        ov3 = OverallProgress("job", [("A", 1.0)], stream=FakeTTY(), clock=clk, history_path=p,
                              heartbeat=False)
        ov3.start()
        ov3.begin("A")
        clk.t += 500
        ov3.abort()
        self.assertAlmostEqual(load_history(p)["job"]["A"], 6.0, places=2)


class TestKeepAlive(TmpCase):
    def test_unavailable_without_device(self):
        ka = KeepAlive(str(self.tmp / "khong_co"))
        self.assertEqual(ka.status().state, S_OFF)
        st = ka.start()
        self.assertEqual(st.state, S_UNAVAILABLE)
        self.assertFalse(st.ok)
        self.assertIn("không", st.vi())
        self.assertEqual(ka.stop().state, S_UNAVAILABLE)

    def test_reads_device_and_stops(self):
        # Dùng os.pipe() thay cho mkfifo: FIFO trên iSH có thể kẹt khi mở (đầu đọc/ghi chờ nhau).
        r, w = os.pipe()
        ka = KeepAlive("/dev/null", retry_s=0.05, opener=lambda p: os.fdopen(r, "rb", 0))
        ka.start()
        try:
            self.assertTrue(ka.wait_running(2.0))
            os.write(w, b"lat,lon\n" * 3)
            end = time.monotonic() + 2.0
            while ka.status().bytes_read < 24 and time.monotonic() < end:
                time.sleep(0.02)
            self.assertEqual(ka.status().bytes_read, 24)
            self.assertEqual(ka.status().state, S_RUNNING)
            self.assertTrue(ka.status().ok)
        finally:
            ka._stop.set()                          # đặt cờ dừng TRƯỚC khi đóng, để luồng không mở lại
            os.close(w)                             # EOF -> luồng đang chặn ở read() thoát
        self.assertEqual(ka.stop().state, S_STOPPED)

    def test_permission_error_reported_not_hidden(self):
        def opener(path):
            raise PermissionError(13, "denied")
        ka = KeepAlive(self.tmp.as_posix(), retry_s=0.01, max_failures=3, opener=opener)
        ka.start()
        end = time.monotonic() + 2.0
        while ka.status().state != S_ERROR and time.monotonic() < end:
            time.sleep(0.01)
        st = ka.status()
        self.assertEqual(st.state, S_ERROR)
        self.assertIn("quyền vị trí", st.detail)
        self.assertFalse(st.ok)
        ka.stop()

    def test_eof_reopens_then_stop(self):
        f = self.tmp / "loc.txt"
        f.write_bytes(b"abc")
        ka = KeepAlive(str(f), retry_s=0.02)
        ka.start()
        end = time.monotonic() + 2.0
        while (ka.status().opened < 2 or ka.status().bytes_read < 6) and time.monotonic() < end:
            time.sleep(0.01)
        self.assertGreaterEqual(ka.status().opened, 2)
        self.assertGreaterEqual(ka.status().bytes_read, 6)
        ka.stop()
        self.assertEqual(ka.status().state, S_STOPPED)

    def test_status_text_is_honest(self):
        self.assertIn("chưa chứng minh", KeepAliveStatus(S_RUNNING).vi())
        self.assertIn("LỖI", KeepAliveStatus(S_ERROR, "x").vi())


class TestAppWiring(TmpCase):
    def test_bootstrap_keepalive_unavailable_is_reported(self):
        app = bootstrap(self.tmp, keepalive=True)
        try:
            if os.path.exists("/dev/location"):
                self.skipTest("máy này có /dev/location")
            self.assertEqual(app.keepalive.status().state, S_UNAVAILABLE)
            self.assertTrue(any("chạy nền" in p for p in app.problems))
        finally:
            app.close()

    def test_default_keepalive_off_and_make_overall(self):
        with mock.patch.dict(os.environ, {"PB3_PROGRESS": "0"}), bootstrap(self.tmp) as app:
            self.assertIsNone(app.keepalive)
            app.report_progress(0.5)                       # không có thanh: không làm gì, không lỗi
            ov = app.make_overall("t", [("A", 1.0)])
            self.assertFalse(ov.enabled)                   # test không phải tty
            ov.start()
            ov.begin("A")
            ov.finish()
            self.assertTrue((app.paths.cache / "overall_times.json").exists())

    def test_cli_keepalive_flag_parses(self):
        from ui import cli
        ns = cli.build_parser().parse_args(["--keepalive", "diagnose", "--ish"])
        self.assertTrue(ns.keepalive)


class TestJobWrapper(TmpCase):
    """app.job: mọi việc (menu/CLI/dịch vụ mới) chạy dưới thanh tiến độ mà không phải tự gắn."""

    def _run(self, fn):
        import builtins
        real_err = FakeTTY()
        old_out, old_err, old_in = sys.stdout, sys.stderr, builtins.input
        sys.stdout, sys.stderr = io.StringIO(), real_err
        try:
            with mock.patch.dict(os.environ):
                os.environ.pop("PB3_PROGRESS", None)
                with bootstrap(self.tmp) as app:
                    res = fn(app)
            self.assertIs(builtins.input, old_in)          # input luôn được trả lại
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        return real_err.getvalue(), res

    def test_job_draws_and_finishes_100(self):
        def f(app):
            with app.job("t:x") as ov:
                self.assertIs(app.overall, ov)
                app.report_progress(0.5, note="file a")
            self.assertIsNone(app.overall)
        out, _ = self._run(f)
        self.assertIn("100%", out)

    def test_job_error_does_not_fake_100(self):
        def f(app):
            with self.assertRaises(ValueError):
                with app.job("t:err"):
                    raise ValueError("x")
        out, _ = self._run(f)
        self.assertNotIn("100%", out)

    def test_nested_job_reuses_outer_bar(self):
        def f(app):
            with app.job("t:outer") as a:
                with app.job("t:inner") as b:
                    self.assertIs(a, b)
        self._run(f)

    def test_input_hides_bar_while_waiting(self):
        import builtins
        seen = {}

        def f(app):
            def fake_input(prompt=""):
                seen["paused"] = app.overall._paused
                return "co"
            with mock.patch.object(builtins, "input", fake_input):
                with app.job("t:in"):
                    seen["answer"] = builtins.input("? ")
                    seen["after"] = app.overall._paused
        self._run(f)
        self.assertTrue(seen["paused"])
        self.assertEqual(seen["answer"], "co")
        self.assertFalse(seen["after"])

    def test_run_feature_is_wrapped_in_bar(self):
        from ui import screens, handlers
        feat = handlers.MENU[0][1]
        got = {}

        class Mod:
            @staticmethod
            def run_interactive(app):
                got["bar"] = app.overall is not None
        def f(app):
            with mock.patch.object(handlers, "load_module", lambda ft: Mod):
                screens.run_feature(app, feat, lambda s="": None, lambda p="": "", 40)
        out, _ = self._run(f)
        self.assertTrue(got["bar"])
        self.assertIn("menu:" + feat.key, out)


if __name__ == "__main__":
    unittest.main()
