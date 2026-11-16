import json
import os
import shutil
import tempfile
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import TmpCase, copy_project, register_tmp_dir, slow_subprocess, subprocess_timeout, unregister_tmp_dir
from core import runstate, safe_io
from core.app import bootstrap
from core.errors import PB3Error
from core.progress import display_width
from core.runstate import MARKER_NAME
from ui import cli, handlers, screens


# Như tests/test_hardening.py: pid cha chốt MỘT LẦN lúc import (main
# thread). Probe iSH 2026-11-16: getppid() trong daemon thread trả về pid
# của chính tiến trình (1881) thay vì pid cha thật (8) — gọi os.getppid()
# trong test sẽ bịa marker mang đúng pid của chính tiến trình, acquire()
# trả 'interrupted' thay vì 'other_running'.
_PARENT_PID = os.getppid()


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class TestBootstrap(TmpCase):
    def test_empty_root_starts_with_builtin_defaults(self):
        with bootstrap(self.tmp) as app:
            self.assertTrue(any("default.toml" in p for p in app.problems))
            self.assertEqual(app.limits.workers, 1)
            for d in (app.paths.input, app.paths.output, app.paths.temp, app.paths.logs):
                self.assertTrue(d.is_dir())
            self.assertTrue(app.logsetup.session_log.exists())
            self.assertTrue((app.paths.temp / MARKER_NAME).exists())

    def test_nothing_written_outside_root(self):
        parent = self.tmp / "p"
        parent.mkdir()
        root = parent / "root"
        root.mkdir()
        before = [e for e in tree(parent) if not e.startswith("root")]
        with bootstrap(root) as app:
            app.make_tempdir("x")
        after = [e for e in tree(parent) if not e.startswith("root")]
        self.assertEqual(before, after)

    def test_close_cleans_tempdir_and_marker_unless_debug(self):
        app = bootstrap(self.tmp)
        d = app.make_tempdir("job-")
        (d / "f").write_text("x")
        app.close()
        self.assertFalse(d.exists())
        self.assertFalse((app.paths.temp / MARKER_NAME).exists())
        app.close()                                     # idempotent
        app2 = bootstrap(self.tmp, debug=True)
        d2 = app2.make_tempdir("job-")
        app2.close()
        self.assertTrue(d2.exists())                    # --debug giữ lại

    def test_detects_interrupted_previous_run(self):
        (self.tmp / "workspace" / "temp").mkdir(parents=True)
        (self.tmp / "workspace" / "temp" / MARKER_NAME).write_text(json.dumps({"pid": 999999999, "session": "old"}))
        with bootstrap(self.tmp) as app:
            self.assertEqual(app.run_info.state, "interrupted")
            self.assertTrue(any("bị ngắt" in p for p in app.problems))
        with bootstrap(self.tmp) as app:               # lần thoát sạch ở trên đã xóa marker
            self.assertEqual(app.run_info.state, "clean")

    def test_corrupt_marker_is_interrupted_not_crash(self):
        (self.tmp / "workspace" / "temp").mkdir(parents=True)
        (self.tmp / "workspace" / "temp" / MARKER_NAME).write_text("{không phải json")
        with bootstrap(self.tmp) as app:
            self.assertEqual(app.run_info.state, "interrupted")

    def test_other_running_does_not_steal_marker(self):
        (self.tmp / "workspace" / "temp").mkdir(parents=True)
        mk = self.tmp / "workspace" / "temp" / MARKER_NAME
        mk.write_text(json.dumps({"pid": _PARENT_PID, "session": "other"}))
        # Như TestPidReuse: trên iSH, test chạy trong daemon thread thì
        # pid_alive(ppid) không đáng tin; test này chỉ kiểm bootstrap không
        # giành marker khi phiên khác còn chạy — ép pid còn sống.
        with mock.patch.object(runstate, "pid_alive", return_value=True):
            with bootstrap(self.tmp) as app:
                self.assertEqual(app.run_info.state, "other_running")
        self.assertEqual(json.loads(mk.read_text())["session"], "other")

    def test_stale_temp_detected_then_quarantined_not_deleted(self):
        (self.tmp / "workspace" / "output").mkdir(parents=True)
        stale = self.tmp / "workspace" / "output" / (".a.bin" + safe_io.STALE_MARKER + "1-x")
        stale.write_bytes("dở".encode("utf-8"))
        with bootstrap(self.tmp) as app:
            self.assertEqual(app.stale_temps, [stale])
            self.assertTrue(any("dở dang" in p for p in app.problems))
            moved = app.quarantine_stale()
            self.assertEqual(len(moved), 1)
            self.assertTrue(moved[0].exists())
            self.assertEqual(app.stale_temps, [])

    def test_broken_config_and_unwritable_logs_still_boot(self):
        (self.tmp / "config").mkdir()
        (self.tmp / "config" / "local.toml").write_text("[[[ rác")
        (self.tmp / "logs").write_text("tệp")
        with bootstrap(self.tmp) as app:
            self.assertTrue(any("local.toml" in p for p in app.problems))
            self.assertTrue(any("log" in p for p in app.problems))

    def test_log_has_no_file_contents(self):
        with bootstrap(self.tmp) as app:
            app.log.info("%s", b"SECRET-PAYLOAD")
            log = app.logsetup.session_log
        self.assertNotIn("SECRET", log.read_text(encoding="utf-8"))


def run_menu_scripted(app, answers, features=None):
    it = iter(answers)
    out = []

    def inp(prompt):
        out.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    rc = screens.run_menu(app, input_fn=inp, say=out.append)
    return rc, "\n".join(out)


class TestMenuInProcess(TmpCase):
    def setUp(self):
        super().setUp()
        self.app = bootstrap(self.tmp)
        self.addCleanup(self.app.close)

    def test_menu_has_exact_spec_items(self):
        rc, txt = run_menu_scripted(self.app, ["0"])
        self.assertEqual(rc, 0)
        # Tên hiển thị trong menu là BearTea (Joseph 02:47); tên project
        # PB3 giữ nguyên bên trong, không hiện ở tiêu đề menu nữa.
        self.assertIn("BearTea", txt)
        self.assertNotIn("PB3 TOOLKIT", txt)
        # Nhãn menu bị wrap theo chiều rộng terminal (COLUMNS=40 gãy khác
        # COLUMNS=80): chuẩn hoá khoảng trắng rồi mới so khớp, như test_help_text.
        flat = " ".join(txt.split())
        for label in ("[1] Mod và ghi ngược (đa engine, Unity hạng nhất)",
                      "[2] Trích xuất ngôn ngữ (đa engine)", "[3] Quản lý plugin và ma trận hỗ trợ",
                      "[4] Xử lý hàng loạt", "[5] Chẩn đoán và điều tra dữ liệu", "[0] Thoát"):
            self.assertIn(label, flat)
        self.assertNotIn("Space Marshals", txt)

    def test_menu_lines_fit_iphone_width(self):
        _, txt = run_menu_scripted(self.app, ["1", "", "0"])
        for ln in txt.splitlines():
            self.assertLessEqual(display_width(ln), 48, ln)

    def test_unimplemented_shows_clear_status_and_changes_nothing(self):
        before = tree(self.tmp / "workspace" / "output")
        # Không còn mục menu nào chưa triển khai (F6 đã triển khai [5][3]
        # Inventory/hex/strings ở đợt tổng cuối); kiểm "chưa triển khai"
        # bằng Feature giả không có module, qua run_feature trực tiếp
        # (mẫu test_module_missing_vs_module_broken_distinguished).
        feat = handlers.Feature("zz", "Chức năng không có", "services.khong_co_that", "Test")
        out = []

        def _eof(prompt=""):
            raise EOFError

        screens.run_feature(self.app, feat, out.append, _eof, 40)
        txt = "\n".join(out)
        self.assertIn("CHƯA TRIỂN KHAI", txt)
        self.assertIn("không giả lập", txt)
        self.assertEqual(before, tree(self.tmp / "workspace" / "output"))

    def test_invalid_choice_reprompts_and_eof_exits_cleanly(self):
        rc, txt = run_menu_scripted(self.app, ["x", "9", "", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(txt.count("Lựa chọn không hợp lệ"), 3)
        self.assertEqual(run_menu_scripted(self.app, [])[0], 0)

    def test_startup_problems_banner(self):
        self.app.problems.append("sự cố thử")
        _, txt = run_menu_scripted(self.app, ["0"])
        self.assertIn("Cảnh báo khởi động", txt)
        self.assertIn("sự cố thử", txt)

    def test_diagnose_env_works_and_quarantine_needs_confirm(self):
        rc, txt = run_menu_scripted(self.app, ["5", "1", "0", "0"])
        self.assertIn("Phát hiện iSH", txt)
        stale = self.tmp / "workspace" / "output" / (".a" + safe_io.STALE_MARKER + "1")
        stale.write_bytes(b"x")
        self.app.stale_temps = [stale]
        run_menu_scripted(self.app, ["5", "2", "khong", "0", "0"])
        self.assertTrue(stale.exists())
        run_menu_scripted(self.app, ["5", "2", "co", "0", "0"])
        self.assertFalse(stale.exists())

    def test_feature_exception_is_isolated_menu_keeps_running(self):
        bad = types.ModuleType("services.unity_tool")

        def boom(app):
            raise ZeroDivisionError("nội bộ")
        bad.run_interactive = boom
        with mock.patch.object(handlers, "load_module",
                               side_effect=lambda f: bad if f.key == "mod" else None):
            rc, txt = run_menu_scripted(self.app, ["1", "", "2", "", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("[E_INTERNAL]", txt)
        self.assertNotIn("Traceback", txt)
        self.assertIn("CHƯA TRIỂN KHAI", txt)           # mục 2 vẫn chạy sau lỗi mục 1
        log = self.app.logsetup.session_log.read_text(encoding="utf-8")
        self.assertIn("ZeroDivisionError", log)

    def test_pb3error_and_keyboard_interrupt_from_service(self):
        for exc, needle in ((PB3Error("E_NO_SPACE", "x"), "[E_NO_SPACE]"), (KeyboardInterrupt(), "Đã hủy")):
            m = types.ModuleType("m")

            def run(app, e=exc):
                raise e
            m.run_interactive = run
            with mock.patch.object(handlers, "load_module", return_value=m):
                rc, txt = run_menu_scripted(self.app, ["2", "", "0"])
            self.assertEqual(rc, 0)
            self.assertIn(needle, txt)

    def test_service_hook_is_called_when_module_exists(self):
        called = []
        m = types.ModuleType("m")
        m.run_interactive = lambda app: called.append(app)
        with mock.patch.object(handlers, "load_module", return_value=m):
            run_menu_scripted(self.app, ["4", "", "0"])
        self.assertEqual(called, [self.app])

    def test_module_missing_vs_module_broken_distinguished(self):
        # mục 4 (batch, Claude 09) nay ĐÃ có module nên không dùng nó để test
        # "chưa có module" nữa; dùng Feature giả không tồn tại. Mục 2 đã có từ Claude 07.
        feat = handlers.Feature("zz", "Chức năng không có", "services.khong_co_that", "Test")
        self.assertFalse(handlers.module_present(feat))
        self.assertIsNone(handlers.load_module(feat))
        pkg = self.tmp / "svc_pkg"
        (pkg / "services").mkdir(parents=True)
        (pkg / "services" / "__init__.py").write_text("")
        (pkg / "services" / "khong_co_that.py").write_text("raise RuntimeError('lỗi import thật')\n")
        with mock.patch.object(sys, "path", [str(pkg)] + sys.path):
            sys.modules.pop("services", None)
            sys.modules.pop("services.khong_co_that", None)
            try:
                self.assertTrue(handlers.module_present(feat))
                with self.assertRaises(RuntimeError):      # lỗi thật KHÔNG bị nuốt thành 'chưa triển khai'
                    handlers.load_module(feat)
            finally:
                sys.modules.pop("services", None)
                sys.modules.pop("services.khong_co_that", None)


class TestCliInProcess(TmpCase):
    def test_parser_accepts_spec_commands(self):
        p = cli.build_parser()
        ns = p.parse_args(["extract", "g.ipa", "--locale", "en", "--out", "o", "--dry-run"])
        self.assertEqual((ns.command, ns.locale, ns.dry_run), ("extract", "en", True))
        ns = p.parse_args(["mod", "g.ipa", "--file", "a/b", "--diff", "--allow-write"])
        self.assertEqual((ns.rel_file, ns.diff, ns.allow_write), ("a/b", True, True))
        ns = p.parse_args(["batch", "m.csv", "--locale", "zh", "--jobs", "4", "--resume"])
        self.assertEqual((ns.jobs, ns.resume), (4, True))
        self.assertEqual(p.parse_args(["plugin", "new", "format", "x"]).args, ["format", "x"])
        self.assertTrue(p.parse_args(["support", "--write"]).write)
        self.assertTrue(p.parse_args(["diagnose", "p", "--verbose", "--ish"]).ish)
        self.assertEqual(p.parse_args(["kb", "import", "f.json"]).arg, "f.json")

    def test_help_text(self):
        """X10: --help nói đúng trạng thái (chưa triển khai thì ghi rõ), cờ riêng của dịch vụ thấy được."""
        import argparse
        p = cli.build_parser()
        txt = p.format_help()
        subs = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
        for sp in subs.choices.values():
            txt += "\n" + sp.format_help()
        # Help bị wrap theo chiều rộng terminal (trên iSH hẹp nên cụm từ có
        # thể gãy đôi giữa chừng, vd "triển\nkhai"): chuẩn hoá khoảng trắng
        # rồi mới so khớp, không đổi nội dung help.
        flat = " ".join(txt.split())
        for needle in ("ma trận hỗ trợ", "kho tri thức game",
                       "--write", "ĐƯỜNG_DẪN", "--allow-write", "--backend", "--deep"):
            self.assertIn(needle, flat)
        self.assertNotIn("CURRENT_STATE.md", txt)

    def test_usage_error_is_exit_1_not_2(self):
        for argv in (["bogus"], ["plugin", "nope"], ["batch"], ["extract"]):
            with mock.patch.object(sys, "stderr", mock.MagicMock()):
                self.assertEqual(cli.main_cli(argv), 1, argv)

    def test_service_can_add_arguments(self):
        m = types.ModuleType("m")
        m.add_arguments = lambda sp: sp.add_argument("--backend", default="auto")
        with mock.patch.object(handlers, "load_module", return_value=m):
            ns = cli.build_parser("mod").parse_args(["mod", "x", "--backend", "native"])
        self.assertEqual(ns.backend, "native")


@slow_subprocess
class TestMenuSubprocess(TmpCase):
    """Chạy menu.py thật từ BẢN SAO ở vị trí khác /root/pb3, từ cwd khác."""

    _shared_td = None
    _shared_proj = None

    @classmethod
    def setUpClass(cls):
        # Sao chép dự án MỘT lần cho cả lớp (trên iSH copytree + rmtree mỗi test rất tốn).
        cls._shared_td = tempfile.mkdtemp(prefix="pb3test-cls-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.proj = self._shared_proj          # test nào cần sửa cây thì tự copy_project riêng

    def run_menu(self, args=(), stdin="", cwd=None):
        return subprocess.run([sys.executable, str(self.proj / "menu.py")] + list(args),
                              input=stdin.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=str(cwd or self.tmp), timeout=subprocess_timeout(60))

    def test_menu_runs_from_other_location_and_cwd(self):
        r = self.run_menu(stdin="0\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BearTea", r.stdout.decode("utf-8"))
        self.assertTrue((self.proj / "workspace" / "output").is_dir())
        self.assertTrue(list((self.proj / "logs").glob("session-*.log")))
        self.assertFalse((self.tmp / "workspace").exists())          # không tạo gì ở cwd

    def test_eof_immediately_exits_0(self):
        self.assertEqual(self.run_menu(stdin="").returncode, 0)

    def test_cli_exit_codes(self):
        self.assertEqual(self.run_menu(["--version"]).returncode, 0)
        r = self.run_menu(["extract", "x.ipa", "--locale", "en"])
        self.assertEqual(r.returncode, 2)
        err = r.stderr.decode("utf-8")
        out = r.stdout.decode("utf-8")
        # extract đã triển khai (Claude 07, M2): file không tồn tại -> tóm tắt
        # ghi E_INPUT_NOT_FOUND, CLI báo E_NO_CANDIDATE (không còn E_NOT_IMPLEMENTED).
        self.assertIn("E_NO_CANDIDATE", err)
        self.assertIn("E_INPUT_NOT_FOUND", out)
        self.assertEqual(self.run_menu(["bogus"]).returncode, 1)
        self.assertEqual(self.run_menu([]).returncode, 0)            # menu (EOF)

    def test_diagnose_ish_cli(self):
        r = self.run_menu(["diagnose", "--ish"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("Backend Unity", out)
        for ln in out.splitlines():
            self.assertLessEqual(display_width(ln), 44, ln)

    def test_runs_without_tomllib_like_python39(self):
        """Mô phỏng Python 3.9 (không có tomllib): phải dùng bộ đọc TOML tối giản và vẫn chạy."""
        code = ("import sys; sys.modules['tomllib']=None; sys.argv=['menu.py','diagnose','--ish'];"
                "sys.path.insert(0,%r); import runpy; runpy.run_path(%r, run_name='__main__')"
                % (str(self.proj), str(self.proj / "menu.py")))
        r = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=str(self.tmp), timeout=subprocess_timeout(60))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.decode("utf-8")
        self.assertIn("tomllib: không", out)
        self.assertNotIn("local.toml", r.stderr.decode("utf-8"))

    def test_missing_core_reports_precise_env_error(self):
        self.proj = copy_project(self.tmp)     # bản riêng vì test này xóa core/
        shutil.rmtree(str(self.proj / "core"))
        r = self.run_menu(stdin="0\n")
        self.assertEqual(r.returncode, 4)
        err = r.stderr.decode("utf-8")
        self.assertIn("Không nạp được mã PB3", err)
        self.assertIn("ModuleNotFoundError", err)
        self.assertNotIn("Traceback", err)

    def test_broken_pipe_is_quiet(self):
        p = subprocess.Popen([sys.executable, str(self.proj / "menu.py"), "diagnose", "--ish"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(self.tmp))
        p.stdout.readline()
        p.stdout.close()
        err = p.stderr.read().decode("utf-8")
        p.stderr.close()
        p.wait(timeout=subprocess_timeout(30))
        self.assertNotIn("Traceback", err)
        self.assertNotIn("E_INTERNAL", err)


if __name__ == "__main__":
    unittest.main()
