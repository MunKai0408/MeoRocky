import io
import os
import signal
import subprocess
import sys
import threading
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import TmpCase
from core import file_dialog, ish_compat
from core.errors import PB3Error
from core.paths import Paths
from core.progress import (CancelToken, Progress, display_width, fmt_bytes, fmt_duration,
                           format_progress, sigint_cancels, truncate_display)


class TestFormat(unittest.TestCase):
    def test_width_never_exceeded(self):
        labels = ["Giải nén resources.assets", "中文文件名很长很长很长的文件.json", "x", "", "e\u0302" * 40]
        for w in (10, 20, 28, 40, 44, 48, 76):
            for lab in labels:
                for total in (None, 0, 1000):
                    for ascii_only in (False, True):
                        for bm in (False, True):
                            line = format_progress(lab, 450, total, 12.0, w, ascii_only, bm)
                            self.assertLessEqual(display_width(line), max(w, 10), (w, lab, line))
                            self.assertNotIn("\n", line)

    def test_content(self):
        l = format_progress("abc", 50, 100, 10, 44)
        self.assertIn(" 50%", l)
        self.assertIn("~10s", l)
        self.assertIn("50/100", l)
        self.assertIn("  ?", format_progress("abc", 5, None, 1, 44))

    def test_ascii_fallback_chars(self):
        l = format_progress("a", 50, 100, 1, 44, ascii_only=True)
        self.assertNotIn("█", l)
        self.assertIn("#", l)

    def test_truncate_and_width_helpers(self):
        self.assertEqual(display_width("中a"), 3)
        self.assertEqual(truncate_display("abcdef", 4), "abc…")
        self.assertEqual(truncate_display("abc", 0), "")
        self.assertLessEqual(display_width(truncate_display("中文中文中文", 5)), 5)
        self.assertEqual(fmt_bytes(1536), "1.5KB")
        self.assertEqual(fmt_duration(3700), "1h01m")
        self.assertEqual(fmt_duration(-5), "0s")


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class TestProgress(unittest.TestCase):
    def test_tty_redraws_in_place_with_throttle(self):
        t = [0.0]
        out = FakeTTY()
        p = Progress("job", 10, stream=out, width=30, interval_s=1.0, clock=lambda: t[0], ascii_only=True)
        p.start()
        for i in range(1, 5):
            t[0] += 0.2
            p.update(done=i)
        self.assertEqual(out.getvalue().count("\r"), 1)       # chỉ vẽ lại khi >= 1s
        t[0] += 2
        p.update(done=6)
        self.assertEqual(out.getvalue().count("\r"), 2)
        p.finish()
        self.assertTrue(out.getvalue().endswith("\n"))
        self.assertIn("100%", out.getvalue())
        n = out.getvalue().count("\n")
        p.finish()                                              # idempotent
        self.assertEqual(out.getvalue().count("\n"), n)

    def test_non_tty_no_carriage_return_and_sparse(self):
        t = [0.0]
        out = io.StringIO()
        p = Progress("job", 10, stream=out, width=30, interval_s=1.0, clock=lambda: t[0], ascii_only=True)
        p.start()
        for i in range(1, 9):
            t[0] += 1.0
            p.update(done=i)
        p.finish()
        self.assertNotIn("\r", out.getvalue())
        self.assertLess(out.getvalue().count("\n"), 5)

    def test_update_checks_cancel(self):
        tok = CancelToken()
        p = Progress("job", 10, stream=io.StringIO(), cancel=tok)
        p.update(done=1)
        tok.cancel()
        with self.assertRaises(PB3Error) as cm:
            p.update(done=2)
        self.assertEqual(cm.exception.code, "E_CANCELLED")

    def test_context_manager_abort_on_error_no_fake_100(self):
        out = FakeTTY()
        with self.assertRaises(RuntimeError):
            with Progress("job", 10, stream=out, ascii_only=True) as p:
                p.update(done=3)
                raise RuntimeError
        self.assertNotIn("100%", out.getvalue())

    def test_broken_stream_never_breaks_work(self):
        class Bad:
            encoding = "utf-8"

            def isatty(self):
                return True

            def write(self, s):
                raise OSError("x")

            def flush(self):
                raise OSError("x")
        p = Progress("job", 10, stream=Bad())
        p.start()
        p.update(done=5)
        p.finish()


class TestCancelSignal(unittest.TestCase):
    def test_first_sigint_sets_flag_second_raises(self):
        # signal.signal chỉ cài được trên main thread; runner (run_tests.py) chạy
        # mỗi test trong luồng riêng nên test này bỏ qua ở đó (vẫn chạy khi gọi
        # trực tiếp bằng python3 -m unittest, lúc đó test chạy trên main thread).
        if threading.current_thread() is not threading.main_thread():
            self.skipTest("cần main thread để cài handler SIGINT (runner chạy test trong luồng riêng)")
        # Tiến trình chạy nền (&/nohup) thừa hưởng SIGINT=SIG_IGN: tự đặt lại handler mặc định cho test, rồi khôi phục.
        saved = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            tok = CancelToken()
            with mock.patch.object(sys, "stderr", io.StringIO()):
                with sigint_cancels(tok):
                    os.kill(os.getpid(), signal.SIGINT)
                    self.assertTrue(tok.cancelled)
                    with self.assertRaises(KeyboardInterrupt):
                        os.kill(os.getpid(), signal.SIGINT)
                        for _ in range(1000):
                            pass
            self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
        finally:
            signal.signal(signal.SIGINT, saved)

    def test_token(self):
        t = CancelToken()
        t.check()
        t.cancel()
        with self.assertRaises(PB3Error):
            t.check("w")


class TestEnv(TmpCase):
    def paths(self):
        p = Paths(self.tmp)
        p.ensure_dirs()
        return p

    def test_report_fields_and_format_width(self):
        rep = ish_compat.detect_environment(self.paths())
        self.assertTrue(rep.python_ok)
        self.assertTrue(rep.workspace_writable)
        self.assertFalse(rep.root_is_expected)
        txt = ish_compat.format_report(rep, 44)
        for ln in txt.splitlines():
            self.assertLessEqual(display_width(ln), 44, ln)
        self.assertIn("Phát hiện iSH", txt)

    def test_is_ish_detection(self):
        with mock.patch("platform.release", return_value="4.20.69-ish"):
            ok, ev = ish_compat.detect_ish()
        self.assertTrue(ok)
        self.assertTrue(ev)
        with mock.patch("platform.release", return_value="6.1.0-generic"), \
                mock.patch("os.path.isdir", return_value=False):
            ok, _ = ish_compat.detect_ish()
        self.assertFalse(ok)

    def test_non_ish_is_flagged_not_evidence(self):
        with mock.patch.object(ish_compat, "detect_ish", return_value=(False, [])):
            rep = ish_compat.detect_environment(self.paths())
        self.assertTrue(any("KHÔNG tính là bằng chứng" in n for n in rep.notes))

    def test_probe_absent_present_untested_import_ok_failed(self):
        self.assertEqual(ish_compat.probe_module("module_khong_ton_tai_xyz").status, "absent")
        pr = ish_compat.probe_module("json")
        self.assertEqual(pr.status, "present_untested")          # có mặt != hoạt động
        self.assertEqual(ish_compat.probe_module("json", deep=True).status, "import_ok")
        (self.tmp / "badmod_pb3.py").write_text("raise RuntimeError('hỏng khi import')\n")
        env = dict(os.environ, PYTHONPATH=str(self.tmp))
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "path", sys.path + [str(self.tmp)]):
            res = ish_compat.probe_module("badmod_pb3", deep=True)
        self.assertEqual(res.status, "import_failed")
        self.assertIn("RuntimeError", res.detail)

    def test_deep_probe_timeout_and_crash(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(ish_compat.probe_module("json", deep=True, timeout_s=1).status, "import_failed")
        crashed = mock.Mock(returncode=-4, stdout=b"", stderr=b"")
        with mock.patch("subprocess.run", return_value=crashed):
            r = ish_compat.probe_module("json", deep=True)
        self.assertEqual(r.status, "import_failed")
        self.assertIn("tín hiệu", r.detail)

    def test_report_never_claims_target_modules_work_when_untested(self):
        fake = [ish_compat.ModuleProbe("UnityPy", "present_untested", "x"),
                ish_compat.ModuleProbe("lz4", "absent", "x")]
        rep = ish_compat.detect_environment(self.paths())
        rep.modules = fake
        txt = ish_compat.format_report(rep, 44)
        self.assertIn("CHƯA kiểm thử", txt)
        self.assertNotIn("hoạt động", txt)
        self.assertNotIn("OK", [l.split()[-1] for l in txt.splitlines() if "UnityPy" in l])

    def test_unwritable_workspace_reported(self):
        p = self.paths()
        with mock.patch.object(ish_compat, "_writable", return_value=(False, "PermissionError")):
            rep = ish_compat.detect_environment(p)
        self.assertFalse(rep.workspace_writable)
        self.assertTrue(any("ghi" in f for f in rep.disabled_features))


class TestDialog(TmpCase):
    def run_dialog(self, answers, **kw):
        it = iter(answers)
        out = []

        def inp(prompt):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        res = file_dialog.choose_path(self.tmp, input_fn=inp, print_fn=out.append, **kw)
        return res, out

    def test_pick_file_by_number_and_navigate(self):
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "g.ipa").write_bytes(b"x" * 10)
        (self.tmp / "a.txt").write_text("x")
        res, out = self.run_dialog(["1", "1"])            # thư mục trước tệp: 1=sub, rồi 1=g.ipa
        self.assertEqual(res, self.tmp / "sub" / "g.ipa")
        for ln in out:
            self.assertLessEqual(display_width(ln), 44, ln)

    def test_select_dir_dot_and_up(self):
        (self.tmp / "sub").mkdir()
        res, _ = self.run_dialog(["1", "..", "."])
        self.assertEqual(res, self.tmp)

    def test_typed_dotdot_path_navigates_up(self):
        """X10: gõ đường dẫn có '..' được giải theo thư mục hiện tại (kể cả ra ngoài thư mục bắt đầu).
        choose_path là picker do người dùng điều khiển, không phải sandbox: lệnh '..' cũng đi lên tự do
        (xem test_select_dir_dot_and_up); nơi cần chặn phải dùng resolve_inside ở bên gọi."""
        (self.tmp / "sub").mkdir()
        (self.tmp / "a.txt").write_text("x")
        it = iter(["../a.txt"])
        out = []
        res = file_dialog.choose_path(self.tmp / "sub", input_fn=lambda p: next(it),
                                      print_fn=out.append)
        # Đường dẫn trả về KHÔNG chuẩn hóa (giữ ".." như người dùng gõ), nhưng trỏ đúng file.
        self.assertEqual(res, self.tmp / "sub" / ".." / "a.txt")
        self.assertTrue(res.is_file())

    def test_manual_path_cancel_eof_and_invalid(self):
        f = self.tmp / "x.bin"
        f.write_bytes(b"1")
        res, _ = self.run_dialog(["không-có-file", str(f)])
        self.assertEqual(res, f)
        self.assertIsNone(self.run_dialog(["q"])[0])
        self.assertIsNone(self.run_dialog([])[0])         # hết input -> None, không crash

    def test_hidden_hidden_and_type_restrictions(self):
        (self.tmp / ".secret").write_text("x")
        (self.tmp / "d").mkdir()
        res, out = self.run_dialog([".", "1", "q"], allow_dir=False)
        self.assertIsNone(res)
        self.assertTrue(any("tệp" in l for l in out))
        self.assertFalse(any(".secret" in l for l in out))

    def test_paging(self):
        for i in range(30):
            (self.tmp / ("f%02d.txt" % i)).write_text("x")
        res, out = self.run_dialog(["n", "13"])
        self.assertEqual(res.name, "f12.txt")


if __name__ == "__main__":
    unittest.main()
