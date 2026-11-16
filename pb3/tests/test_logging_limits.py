import logging as std_logging
import time
import unittest

from tests import helpers  # noqa: F401
from tests.helpers import TmpCase
from core import logging as pb3log
from core.errors import PB3Error
from core.limits import Limits, TimeBudget
from core import config
from core.paths import Paths


class TestLogging(TmpCase):
    def setup(self, **kw):
        kw.setdefault("session_id", "t1")
        ls = pb3log.setup_logging(self.tmp / "logs", **kw)
        self.addCleanup(ls.close)
        return ls

    def read_all(self, ls):
        for h in ls.logger.handlers:
            h.flush()
        return ls.main_log.read_text(encoding="utf-8")

    def test_stdlib_logging_not_shadowed(self):
        self.assertTrue(hasattr(std_logging, "getLogger"))
        self.assertIn("RotatingFileHandler", dir(__import__("logging.handlers").handlers))

    def test_creates_main_and_session_logs(self):
        ls = self.setup()
        pb3log.get_logger("x").info("xin chào")
        self.assertTrue(ls.main_log.exists() and ls.session_log.exists())
        self.assertIn("xin chào", self.read_all(ls))
        self.assertEqual(ls.problems, [])

    def test_bytes_are_never_logged_raw(self):
        ls = self.setup()
        lg = pb3log.get_logger("x")
        lg.info("data %s", b"SECRET-BYTES-123")
        lg.info(b"SECRET-MSG-456")
        lg.info("map %(d)s", {"d": bytearray(b"SECRET-789")})
        text = self.read_all(ls)
        self.assertNotIn("SECRET", text)
        self.assertIn("sha8-prefix=", text)   # hash tiền tố, không phải toàn bộ blob

    def test_long_lines_truncated_and_control_chars_replaced(self):
        ls = self.setup(max_message_chars=100)
        pb3log.get_logger("x").info("A" * 5000 + "\x07\x00")
        text = self.read_all(ls)
        self.assertNotIn("A" * 200, text)
        self.assertIn("ký tự]", text)
        self.assertNotIn("\x07", text)

    def test_exception_traceback_goes_to_log(self):
        ls = self.setup()
        try:
            raise ValueError("boom")
        except ValueError as exc:
            pb3log.log_exception(pb3log.get_logger("x"), exc, "unit")
        text = self.read_all(ls)
        self.assertIn("Traceback", text)
        self.assertIn("ValueError", text)

    def test_rotation(self):
        ls = self.setup(max_bytes=65536, backups=2, max_message_chars=5000)
        lg = pb3log.get_logger("x")
        for _ in range(60):
            lg.info("y" * 4000)
        files = list((self.tmp / "logs").glob("pb3.log*"))
        self.assertGreaterEqual(len(files), 2)
        self.assertLessEqual(len(files), 3)

    def test_session_pruning_keeps_newest(self):
        d = self.tmp / "logs"
        d.mkdir()
        for i in range(6):
            f = d / ("session-old%d.log" % i)
            f.write_text("x")
            import os
            os.utime(str(f), (time.time() - 1000 + i, time.time() - 1000 + i))
        self.setup(keep_sessions=3)
        self.assertLessEqual(len(list(d.glob("session-*.log"))), 3)

    def test_unwritable_logdir_falls_back_without_crash(self):
        (self.tmp / "logs").write_text("tệp, không phải thư mục")
        ls = self.setup()
        self.assertTrue(ls.problems)
        self.assertIsNone(ls.main_log)
        pb3log.get_logger("x").warning("vẫn không crash")

    def test_setup_twice_does_not_duplicate_handlers(self):
        self.setup()
        ls = self.setup()
        # Chỉ đếm handler DO PB3 TẠO: runner test (pytest logging plugin)
        # cũng gắn handler của nó vào logger, không phải handler PB3 nhân đôi.
        owned = [h for h in ls.logger.handlers if getattr(h, "_pb3_owned", False)]
        self.assertEqual(len(owned), 2)


class TestLimits(TmpCase):
    def code(self, fn, *a):
        with self.assertRaises(PB3Error) as cm:
            fn(*a)
        return cm.exception.code

    def test_checks_raise_proper_codes(self):
        L = Limits(max_input_bytes=100, max_entry_bytes=10, max_memory_read_bytes=5, max_entries=3,
                   max_depth=2, max_total_uncompressed_bytes=50, max_ratio=10)
        self.assertEqual(self.code(L.check_input_size, 101), "E_TOO_LARGE")
        self.assertEqual(self.code(L.check_entry_size, 11), "E_TOO_LARGE")
        self.assertEqual(self.code(L.check_memory_read, 6), "E_TOO_LARGE")
        self.assertEqual(self.code(L.check_entries, 4), "E_ARCHIVE_BOMB")
        self.assertEqual(self.code(L.check_depth, 3), "E_ARCHIVE_BOMB")
        self.assertEqual(self.code(L.check_total, 51), "E_ARCHIVE_BOMB")
        self.assertEqual(self.code(L.check_ratio, 1, 11), "E_ARCHIVE_BOMB")
        self.assertEqual(self.code(L.check_ratio, 0, 5), "E_ARCHIVE_BOMB")
        for ok in (lambda: L.check_input_size(100), lambda: L.check_entries(3), lambda: L.check_ratio(1, 10),
                   lambda: L.check_ratio(0, 0)):
            ok()

    def test_from_config_matches_defaults(self):
        (self.tmp / "config").mkdir()
        L = Limits.from_config(config.load_config(Paths(self.tmp)))
        self.assertEqual(L, Limits())
        self.assertEqual(L.workers, 1)
        self.assertEqual(L.required_free_bytes(1000), 2500)

    def test_overrides_and_timeouts(self):
        L = Limits().with_overrides(workers=2)
        self.assertEqual(L.workers, 2)
        self.assertEqual(L.hook_timeout(), 20.0)
        self.assertEqual(L.hook_timeout(heavy=True), 120.0)

    def test_time_budget_with_fake_clock(self):
        t = [0.0]
        b = TimeBudget(5, plugin_id="p", clock=lambda: t[0])
        b.check()
        t[0] = 6.0
        self.assertTrue(b.expired)
        with self.assertRaises(PB3Error) as cm:
            b.check("w")
        self.assertEqual((cm.exception.code, cm.exception.plugin_id), ("E_PLUGIN_TIMEOUT", "p"))
        self.assertEqual(TimeBudget(None).remaining(), float("inf"))


if __name__ == "__main__":
    unittest.main()
