"""Test isolation (Claude 03 đợt 2, nhiệm vụ A): timeout, guard mạng/tiến trình/ghi file, chuẩn hóa lỗi.

Guard là best effort (xem registry/isolation.py); test kiểm hành vi được hứa, không hứa hơn.
"""
import copy
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import unittest

from core.errors import PB3Error
from core.limits import Limits
from registry import isolation as iso
from registry import loader
from sdk.api import Plugin, PluginContext
from sdk.types import LocTable
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase, subprocess_timeout

BASE = {
    "id": "iso_demo", "name": "Iso", "version": "1.0.0", "api_version": 3, "kind": "format",
    "capabilities": ["read", "write"], "write_modes": ["REBUILD_SECTION"], "maturity": "M2", "priority": 40, "true_cost": "cheap",
    "engines": ["generic"], "match": {"extensions": [".txt"]},
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 5},
    "entry": "plugin:P", "selftest": True, "docs": "mẫu",
}


def make_plugin(**hooks):
    ns = {"MANIFEST": copy.deepcopy(BASE)}
    ns.update(hooks)
    ns.setdefault("parse", lambda self, ctx, cand: LocTable([], None, "iso_demo"))
    ns.setdefault("build", lambda self, ctx, table, base: base)
    return type("P", (Plugin,), ns)()


class IsoCase(TmpCase):
    def setUp(self):
        super().setUp()
        self.temp = str(self.tmp / "work")
        os.makedirs(self.temp)
        self.outside = str(self.tmp / "outside.txt")

    def ctx(self, timeout=None, **kw):
        if timeout is not None:
            kw["limits"] = Limits(hook_timeout_s=timeout)
        return PluginContext(MemoryVFS({"a.txt": b"x"}), tempdir=self.temp, **kw)

    def run_(self, plugin, name, *args, **kw):
        return iso.run_hook(plugin, name, self.ctx(), *args, **kw)


class TestNormalizeErrors(IsoCase):
    def test_success_returns_value(self):
        p = make_plugin(probe=lambda self, ctx, cand: {"n": 1})
        r = self.run_(p, "probe", None)
        self.assertTrue(r.ok, r.diagnostic)
        self.assertEqual(r.value, {"n": 1})
        self.assertEqual(r.code, "E_OK")

    def test_plugin_exception_becomes_internal(self):
        def boom(self, ctx, cand):
            raise ValueError("bí mật trong file người dùng")
        r = self.run_(make_plugin(probe=boom), "probe", None)
        self.assertFalse(r.ok)
        self.assertEqual(r.diagnostic.code, "E_INTERNAL")
        self.assertEqual(r.diagnostic.plugin_id, "iso_demo")
        self.assertNotIn("bí mật", r.diagnostic.detail)      # chỉ tên kiểu, không nội dung
        self.assertIn("ValueError", r.traceback)

    def test_pb3error_code_preserved(self):
        def bad(self, ctx, cand):
            raise PB3Error("E_CORRUPT", "hỏng")
        r = self.run_(make_plugin(probe=bad), "probe", None)
        self.assertEqual(r.diagnostic.code, "E_CORRUPT")
        self.assertEqual(r.diagnostic.plugin_id, "iso_demo")

    def test_baseexception_in_hook_is_contained(self):
        def bye(self, ctx, cand):
            raise SystemExit(3)
        r = self.run_(make_plugin(probe=bye), "probe", None)
        self.assertEqual(r.code, "E_INTERNAL")

    def test_unknown_hook_and_missing_hook(self):
        p = make_plugin()
        self.assertEqual(self.run_(p, "rm_rf").code, "E_PLUGIN_INVALID")
        p.probe = None
        self.assertEqual(self.run_(p, "probe", None).code, "E_PLUGIN_INVALID")

    def test_build_must_return_bytes(self):
        r = self.run_(make_plugin(build=lambda self, ctx, t, b: "chuỗi"), "build", LocTable([], None, "x"), b"")
        self.assertEqual(r.code, "E_PLUGIN_INVALID")
        r = self.run_(make_plugin(build=lambda self, ctx, t, b: bytearray(b"ok")), "build", LocTable([], None, "x"), b"")
        self.assertTrue(r.ok)
        self.assertEqual(r.value, b"ok")
        self.assertIsInstance(r.value, bytes)

    def test_host_cancel_reaches_hook(self):
        def loop(self, ctx, cand):
            while True:
                ctx.check_cancel()
                time.sleep(0.01)
        c = self.ctx(timeout=5, cancel=lambda: True)
        r = iso.run_hook(make_plugin(probe=loop), "probe", c, None)
        self.assertEqual(r.code, "E_CANCELLED")

    def test_messages_and_diagnostics_shared_with_caller_ctx(self):
        def talk(self, ctx, cand):
            ctx.log("xin chào")
            ctx.diag("E_PARSE_PARTIAL", "một phần", "x", "warning")
            return {}
        c = self.ctx()
        r = iso.run_hook(make_plugin(probe=talk), "probe", c, None)
        self.assertTrue(r.ok)
        self.assertEqual(c.messages[0], ("info", "xin chào"))
        self.assertEqual(c.diagnostics[0].code, "E_PARSE_PARTIAL")


class TestTimeout(IsoCase):
    def test_cooperative_hang_times_out_and_stops(self):
        def hang(self, ctx, cand):
            while True:
                ctx.check_cancel()
                time.sleep(0.01)
        t0 = time.monotonic()
        r = iso.run_hook(make_plugin(probe=hang), "probe", self.ctx(), None,
                         timeout=subprocess_timeout(0.3))
        self.assertEqual(r.code, "E_PLUGIN_TIMEOUT")
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - t0, subprocess_timeout(3.0))
        time.sleep(subprocess_timeout(0.2))
        self.assertEqual(iso.abandoned_count(), 0)           # hợp tác dừng -> không bị bỏ lại

    def test_uncooperative_hang_is_abandoned_but_pipeline_continues(self):
        stop = threading.Event()

        def hang(self, ctx, cand):
            stop.wait(30)
        old = iso.CANCEL_GRACE_S
        iso.CANCEL_GRACE_S = 0.1
        try:
            before = iso.abandoned_count()
            r = iso.run_hook(make_plugin(probe=hang), "probe", self.ctx(), None,
                             timeout=subprocess_timeout(0.2))
            self.assertEqual(r.code, "E_PLUGIN_TIMEOUT")
            self.assertEqual(iso.abandoned_count(), before + 1)
            nxt = self.run_(make_plugin(probe=lambda self, ctx, cand: {"ok": 1}), "probe", None)
            self.assertTrue(nxt.ok)                          # pipeline vẫn chạy tiếp
        finally:
            iso.CANCEL_GRACE_S = old
            stop.set()
        time.sleep(subprocess_timeout(0.3))
        self.assertEqual(iso.abandoned_count(), 0)
        self.assertFalse(iso.guard_installed())              # luồng kết thúc -> guard được gỡ

    def test_timeout_does_not_poison_next_call_on_same_ctx(self):
        def hang(self, ctx, cand):
            while True:
                ctx.check_cancel()
                time.sleep(0.01)
        c = self.ctx()
        iso.run_hook(make_plugin(probe=hang), "probe", c, None,
                     timeout=subprocess_timeout(0.2))
        r = iso.run_hook(make_plugin(probe=lambda self, ctx, cand: {"a": 1}), "probe", c, None)
        self.assertTrue(r.ok)


class TestGuards(IsoCase):
    def assertForbidden(self, r):
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "E_PLUGIN_FORBIDDEN", r.diagnostic)
        self.assertTrue(r.violations)

    def test_socket_blocked(self):
        def f(self, ctx, cand):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_dns_blocked(self):
        def f(self, ctx, cand):
            socket.getaddrinfo("localhost", 80)
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_subprocess_blocked(self):
        def f(self, ctx, cand):
            subprocess.run([sys.executable, "-c", "pass"])
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_os_shell_blocked(self):
        def f(self, ctx, cand):
            getattr(os, "sys" + "tem")("true")
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_popen_blocked(self):
        def f(self, ctx, cand):
            os.popen("true")
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_write_outside_tempdir_blocked(self):
        def f(self, ctx, cand):
            with open(self_path[0], "w") as fh:
                fh.write("x")
        self_path = [self.outside]
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))
        self.assertFalse(os.path.exists(self.outside))

    def test_write_variants_blocked(self):
        out = self.outside

        def a(self, ctx, cand):
            os.open(out, os.O_WRONLY | os.O_CREAT)

        def b(self, ctx, cand):
            os.mkdir(out)

        def c(self, ctx, cand):
            import io
            io.open(out, "ab")
        for fn in (a, b, c):
            self.assertForbidden(self.run_(make_plugin(probe=fn), "probe", None))
        self.assertFalse(os.path.exists(out))

    def test_path_traversal_out_of_tempdir_blocked(self):
        sneaky = os.path.join(self.temp, "..", "escaped.txt")

        def f(self, ctx, cand):
            open(sneaky, "w").close()
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))
        self.assertFalse(os.path.exists(os.path.join(self.temp, "..", "escaped.txt")))

    def test_swallowed_violation_still_fails(self):
        def f(self, ctx, cand):
            try:
                socket.socket()
            except Exception:
                pass
            return {"ok": True}
        self.assertForbidden(self.run_(make_plugin(probe=f), "probe", None))

    def test_tempdir_write_and_reads_allowed(self):
        target = os.path.join(self.temp, "ok.bin")

        def f(self, ctx, cand):
            with open(target, "wb") as fh:
                fh.write(b"abc")
            os.makedirs(os.path.join(ctx.tempdir, "sub", "deep"))
            with open(__file__, "rb") as fh:               # đọc ngoài ctx không bị chặn (hạn chế đã ghi)
                fh.read(1)
            with open(target, "rb") as fh:
                return {"n": len(fh.read())}
        r = self.run_(make_plugin(probe=f), "probe", None)
        self.assertTrue(r.ok, r.diagnostic)
        self.assertEqual(r.value, {"n": 3})

    def test_no_tempdir_means_no_writes(self):
        c = PluginContext(MemoryVFS({"a.txt": b"x"}))
        p = make_plugin(probe=lambda s, ctx, cand: open(os.path.join(str(self.tmp), "x"), "w"))
        self.assertForbidden(iso.run_hook(p, "probe", c, None))

    def test_guard_only_affects_hook_thread_and_is_restored(self):
        orig_sock_init = socket.socket.__init__
        orig_open = open
        seen = {}

        def f(self, ctx, cand):
            def other():                                   # luồng khác của PB3 trong lúc hook chạy: không bị chặn
                try:
                    s = socket.socket()
                    s.close()
                    seen["other"] = "ok"
                except PB3Error:
                    seen["other"] = "blocked"
            t = threading.Thread(target=other)
            t.start()
            t.join()
            return {}
        r = self.run_(make_plugin(probe=f), "probe", None)
        self.assertTrue(r.ok, r.diagnostic)
        self.assertEqual(seen["other"], "ok")
        self.assertIs(socket.socket.__init__, orig_sock_init)       # đã phục hồi
        import builtins
        self.assertIs(builtins.open, orig_open)
        self.assertFalse(iso.guard_installed())
        s = socket.socket()                                         # luồng chính dùng bình thường
        s.close()

    def test_nested_active_refcount(self):
        with iso.active("a"):
            with iso.active("b"):
                self.assertTrue(iso.guard_installed())
            self.assertTrue(iso.guard_installed())
        self.assertFalse(iso.guard_installed())

    def test_trusted_suspends_guard_in_thread(self):
        with iso.active("a", [self.temp]):
            with self.assertRaises(PB3Error):
                open(self.outside, "w")
            with iso.trusted():
                open(self.outside, "w").close()
            with self.assertRaises(PB3Error):
                open(self.outside, "w")
        self.assertTrue(os.path.exists(self.outside))

    def test_sink_and_kb_of_host_run_trusted(self):
        log_path = os.path.join(str(self.tmp), "host.log")       # NGOÀI tempdir: chỉ host được ghi

        def sink(level, text):
            with open(log_path, "a") as fh:
                fh.write(text + "\n")

        class KB:
            def get(self, pid, k):
                return None

            def put(self, pid, k, v):
                with open(log_path, "a") as fh:
                    fh.write("kb\n")

        def f(self, ctx, cand):
            ctx.log("hello")
            ctx.kb_write("k", 1)
            return {}
        c = PluginContext(MemoryVFS({}), tempdir=self.temp, kb=KB(), sink=sink, plugin_id="iso_demo")
        r = iso.run_hook(make_plugin(probe=f), "probe", c, None)
        self.assertTrue(r.ok, r.diagnostic)
        with open(log_path) as fh:
            self.assertEqual(fh.read(), "hello\nkb\n")

    def test_coverage_report_lists_core_items(self):
        with iso.active("a"):
            pass
        cov = iso.guard_coverage()
        for k in ("socket.socket", "subprocess.Popen", "builtins.open", "os.open"):
            self.assertTrue(cov.get(k), k)


class TestImportGuard(TmpCase):
    def mk(self, body):
        d = self.tmp / "plugins_user" / "p1"
        os.makedirs(str(d))
        m = copy.deepcopy(BASE)
        m["entry"] = "plugin:P"
        src = "from sdk.api import Plugin\nMANIFEST = %r\n%s\nclass P(Plugin):\n    MANIFEST = MANIFEST\n    def parse(self, ctx, cand): return None\n    def build(self, ctx, t, b): return b\n" % (m, textwrap.dedent(body))
        (d / "plugin.py").write_text(src, encoding="utf-8")
        return [("user", str(self.tmp / "plugins_user"))]

    def test_import_time_socket_is_rejected_only_that_plugin(self):
        dirs = self.mk("import socket\nsocket.socket()\n")
        rep = loader.discover(dirs, import_guard=iso.import_guard, static_scan=False)
        rec = rep.records[0]
        self.assertEqual(rec.state, "disabled")
        self.assertIn(rec.code, ("E_PLUGIN_FORBIDDEN", "E_PLUGIN_INVALID"))

    def test_import_time_forbidden_code_reported(self):
        dirs = self.mk("import socket\ntry:\n    socket.socket()\nexcept Exception:\n    pass\n")
        rec = loader.discover(dirs, import_guard=iso.import_guard, static_scan=False).records[0]
        self.assertEqual((rec.state, rec.code), ("disabled", "E_PLUGIN_FORBIDDEN"))

    def test_import_time_write_blocked(self):
        target = str(self.tmp / "evil.txt")
        dirs = self.mk("open(%r, 'w').close()\n" % target)
        rec = loader.discover(dirs, import_guard=iso.import_guard, static_scan=False).records[0]
        self.assertEqual(rec.state, "disabled")
        self.assertFalse(os.path.exists(target))

    def test_good_plugin_loads_under_guard(self):
        dirs = self.mk("X = 1\n")
        rec = loader.discover(dirs, import_guard=iso.import_guard, static_scan=False).records[0]
        self.assertEqual((rec.state, rec.code), ("ok", "E_OK"))
        self.assertFalse(iso.guard_installed())

    def test_import_timeout_best_effort(self):
        if iso._load("ctypes") is None:
            self.skipTest("không có ctypes: không có timeout import (đã ghi trong tài liệu)")
        dirs = self.mk("import time\nwhile True:\n    time.sleep(0.01)\n")
        g = iso.make_import_guard(0.3)
        t0 = time.monotonic()
        rec = loader.discover(dirs, import_guard=g, static_scan=False).records[0]
        self.assertLess(time.monotonic() - t0, 5.0)
        self.assertEqual((rec.state, rec.code), ("disabled", "E_PLUGIN_TIMEOUT"))
        self.assertFalse(iso.guard_installed())


if __name__ == "__main__":
    unittest.main()
