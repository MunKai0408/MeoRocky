"""C3 (decisions #57): lưu bật/tắt plugin (`plugins.disabled`) + `plugin enable|disable` + menu "Quản lý plugin"."""
import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import config
from core.app import bootstrap
from core.errors import PB3Error
from core.paths import Paths
from registry.registry import build_registry
from services import plugin_manager as pm
from tests.helpers import ROOT, TmpCase, copy_project, register_tmp_dir, subprocess_timeout, unregister_tmp_dir


def make_app(root):
    paths = Paths(str(root))
    os.makedirs(str(paths.config_dir), exist_ok=True)
    return SimpleNamespace(paths=paths, config=config.load_config(paths, apply_paths=False))


def reload_app(app):
    """Mô phỏng khởi động lại: đọc lại config/local.toml từ đĩa."""
    return SimpleNamespace(paths=app.paths, config=config.load_config(app.paths, apply_paths=False))


def run_cmd(app, action, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = pm.run_cli(app, argparse.Namespace(command="plugin", action=action, args=list(args)))
    return rc, out.getvalue(), err.getvalue()


class ToggleCase(TmpCase):
    def setUp(self):
        super().setUp()
        self.app = make_app(self.tmp)
        for pid in ("alpha_csv", "beta_csv"):
            rc, out, err = run_cmd(self.app, "new", "format", pid)
            self.assertEqual(rc, 0, out + err)

    def state_of(self, app, pid):
        reg = build_registry(str(self.tmp), app.config)
        return reg.get(pid).display_state


class TestStrlistConfig(unittest.TestCase):
    OPT = config.BY_KEY["plugins.disabled"]

    def test_option_exists_with_empty_default(self):
        self.assertEqual((self.OPT.kind, self.OPT.default), ("strlist", []))

    def test_coerce_dedupes_and_keeps_order(self):
        self.assertEqual(config.coerce(self.OPT, ["b", "a", "b"]), ["b", "a"])

    def test_coerce_rejects_bad_values(self):
        for bad in ("abc", ["a", 1], [""], ["a\x00b"], ["x" * 65], ["a"] * 1 + [None], [str(i) for i in range(501)]):
            with self.assertRaises(ValueError, msg=repr(bad)[:40]):
                config.coerce(self.OPT, bad)

    def test_bad_value_in_local_toml_warns_and_uses_default(self):
        tree = {"plugins": {"disabled": "oops"}}
        ok, warns = config.validate_tree(tree)
        self.assertEqual(ok, {})
        self.assertTrue(any("plugins.disabled" in w for w in warns))

    def test_default_is_not_shared_between_loads(self):
        a, b = config.builtin_defaults(), config.builtin_defaults()
        a["plugins.disabled"].append("zzz")
        self.assertEqual(b["plugins.disabled"], [])


class TestSaveAndReload(TmpCase):
    def test_roundtrip_keeps_comments_and_other_keys(self):
        app = make_app(self.tmp)
        app.paths.local_toml.write_text("# ghi chú của tôi\n[limits]\nworkers = 2   # hai luồng\n", encoding="utf-8")
        config.save_local(app.paths, {"plugins.disabled": ["b_x", "a_x"]})
        text = app.paths.local_toml.read_text(encoding="utf-8")
        self.assertIn("# ghi chú của tôi", text)
        self.assertIn("# hai luồng", text)
        cfg = config.load_config(app.paths, apply_paths=False)
        self.assertEqual(cfg.get("plugins.disabled"), ["b_x", "a_x"])
        self.assertEqual(cfg.get("limits.workers"), 2)

    def test_config_update_validates(self):
        app = make_app(self.tmp)
        app.config.update("plugins.disabled", ["ok_id", "ok_id"])
        self.assertEqual(app.config.get("plugins.disabled"), ["ok_id"])
        with self.assertRaises(PB3Error) as cm:
            app.config.update("plugins.disabled", [1])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        self.assertEqual(app.config.get("plugins.disabled"), ["ok_id"])      # không đổi khi sai


class TestRegistryHonoursConfig(ToggleCase):
    def test_disabled_in_config_is_loaded_but_inactive(self):
        config.save_local(self.app.paths, {"plugins.disabled": ["alpha_csv"]})
        app2 = reload_app(self.app)
        reg = build_registry(str(self.tmp), app2.config)
        rec = reg.get("alpha_csv")
        self.assertEqual((rec.state, rec.enabled, rec.display_state), ("ok", False, "disabled"))
        self.assertEqual(rec.display_reason, "người dùng đã tắt")
        self.assertEqual([r.plugin_id for r in reg.active()], ["beta_csv"])
        self.assertEqual([r.plugin_id for r in reg.by_kind("format")], ["beta_csv"])

    def test_explicit_disabled_ids_and_config_are_merged(self):
        config.save_local(self.app.paths, {"plugins.disabled": ["alpha_csv"]})
        app2 = reload_app(self.app)
        reg = build_registry(str(self.tmp), app2.config, disabled_ids=["beta_csv"])
        self.assertEqual(reg.active(), [])

    def test_no_config_means_nothing_disabled(self):
        reg = build_registry(str(self.tmp))
        self.assertEqual(sorted(r.plugin_id for r in reg.active()), ["alpha_csv", "beta_csv"])


class TestToggleCli(ToggleCase):
    def test_disable_persists_across_restart(self):
        rc, out, err = run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(rc, 0, err)
        self.assertIn("Đã tắt plugin 'alpha_csv'", out)
        self.assertEqual(self.app.config.get("plugins.disabled"), ["alpha_csv"])
        self.assertIn("alpha_csv", self.app.paths.local_toml.read_text(encoding="utf-8"))
        app2 = reload_app(self.app)
        self.assertEqual(self.state_of(app2, "alpha_csv"), "disabled")
        self.assertEqual(self.state_of(app2, "beta_csv"), "ok")

    def test_list_shows_disabled_with_reason(self):
        run_cmd(self.app, "disable", "alpha_csv")
        rc, out, _ = run_cmd(self.app, "list")
        self.assertEqual(rc, 0)
        self.assertIn("alpha_csv  [disabled]", out)
        self.assertIn("người dùng đã tắt", out)
        self.assertIn("beta_csv  [ok]", out)

    def test_enable_restores(self):
        run_cmd(self.app, "disable", "alpha_csv")
        rc, out, _ = run_cmd(self.app, "enable", "alpha_csv")
        self.assertEqual(rc, 0)
        self.assertIn("Đã bật lại plugin 'alpha_csv'", out)
        self.assertEqual(self.app.config.get("plugins.disabled"), [])
        self.assertEqual(self.state_of(reload_app(self.app), "alpha_csv"), "ok")

    def test_disable_twice_is_a_noop_and_does_not_rewrite(self):
        run_cmd(self.app, "disable", "alpha_csv")
        before = self.app.paths.local_toml.read_bytes()
        rc, out, _ = run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(rc, 0)
        self.assertIn("đã tắt từ trước", out)
        self.assertEqual(self.app.paths.local_toml.read_bytes(), before)

    def test_enable_when_not_disabled_is_a_noop(self):
        rc, out, _ = run_cmd(self.app, "enable", "alpha_csv")
        self.assertEqual(rc, 0)
        self.assertIn("không nằm trong danh sách tắt", out)
        self.assertFalse(self.app.paths.local_toml.exists())               # không tạo file khi không có gì đổi

    def test_list_is_kept_sorted_and_unique(self):
        run_cmd(self.app, "disable", "beta_csv")
        run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(self.app.config.get("plugins.disabled"), ["alpha_csv", "beta_csv"])

    def test_unknown_id_is_user_error_and_writes_nothing(self):
        for action in ("disable", "enable"):
            rc, out, err = run_cmd(self.app, action, "khong_co")
            self.assertEqual(rc, 1, (action, out, err))
            self.assertIn("E_INPUT_NOT_FOUND", err)
        self.assertFalse(self.app.paths.local_toml.exists())

    def test_usage_errors(self):
        for action, args in (("disable", []), ("enable", []), ("disable", ["a", "b"])):
            rc, out, err = run_cmd(self.app, action, *args)
            self.assertEqual(rc, 1, (action, args))
            self.assertIn("E_CONFIG_INVALID", err)

    def test_stale_id_warns_in_list_and_can_be_cleared(self):
        config.save_local(self.app.paths, {"plugins.disabled": ["da_xoa"]})
        app2 = reload_app(self.app)
        rc, out, _ = run_cmd(app2, "list")
        self.assertIn("da_xoa", out)
        self.assertIn("plugin enable da_xoa", out)
        rc, out, _ = run_cmd(app2, "enable", "da_xoa")
        self.assertEqual(rc, 0)
        self.assertIn("không còn plugin này", out)
        self.assertEqual(reload_app(app2).config.get("plugins.disabled"), [])

    def test_stale_id_cannot_be_added_by_disable(self):
        rc, _, err = run_cmd(self.app, "disable", "da_xoa")
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)

    def test_plugin_missing_dependency_can_be_disabled_and_enabled_with_note(self):
        p = self.tmp / "plugins_user/alpha_csv/plugin.py"
        p.write_text(p.read_text(encoding="utf-8").replace('"api_version": 3', '"api_version": 3, "requires": ["pb3_khong_co_goi"]'),
                     encoding="utf-8")
        self.assertEqual(self.state_of(self.app, "alpha_csv"), "unavailable")
        rc, out, _ = run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(rc, 0)
        self.assertIn("Đã tắt plugin 'alpha_csv'", out)
        self.assertIn("thiếu thư viện", out)                                  # nhắc: vốn đã không dùng được
        rc, out, _ = run_cmd(self.app, "enable", "alpha_csv")
        self.assertEqual(rc, 0)
        self.assertIn("thiếu thư viện", out)
        self.assertEqual(self.state_of(self.app, "alpha_csv"), "unavailable")  # bật lại KHÔNG che lỗi dependency

    def test_plugin_with_broken_manifest_has_no_usable_id(self):
        p = self.tmp / "plugins_user/alpha_csv/plugin.py"
        p.write_text(p.read_text(encoding="utf-8").replace('"api_version": 3', '"api_version": 99'), encoding="utf-8")
        rc, out, err = run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(rc, 1)                                               # id chỉ là '?alpha_csv' trong plugin list
        self.assertIn("E_INPUT_NOT_FOUND", err)
        self.assertFalse(self.app.paths.local_toml.exists())

    def test_save_failure_leaves_memory_and_reports_error(self):
        with mock.patch.object(config, "save_local", side_effect=PB3Error("E_OUTPUT_DENIED", "đĩa chỉ đọc")):
            rc, out, err = run_cmd(self.app, "disable", "alpha_csv")
        self.assertEqual(rc, PB3Error("E_OUTPUT_DENIED").exit_code, err)
        self.assertIn("E_OUTPUT_DENIED", err)
        self.assertEqual(self.app.config.get("plugins.disabled"), [])


class Script:
    """Giả lập nhập liệu: trả lần lượt từng dòng, hết thì EOFError. Ghi lại lời nhắc."""

    def __init__(self, *lines):
        self.lines, self.prompts = list(lines), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


class TestManageMenu(ToggleCase):
    def run_menu(self, *lines, app=None):
        app = app or self.app
        said, ask = [], Script(*lines)
        pm.run_interactive(app, input_fn=ask, say=said.append)
        return "\n".join(said), ask

    def test_toggle_by_number_persists_and_updates_screen(self):
        out, ask = self.run_menu("1", "0")
        self.assertIn("QUẢN LÝ PLUGIN", out)
        self.assertIn("[1] alpha_csv  [ok]", out)
        self.assertIn("Đã tắt plugin 'alpha_csv'", out)
        self.assertIn("[1] alpha_csv  [disabled]", out)                      # màn hình vẽ lại sau khi đảo
        self.assertEqual(self.app.config.get("plugins.disabled"), ["alpha_csv"])
        self.assertEqual(self.state_of(reload_app(self.app), "alpha_csv"), "disabled")

    def test_toggle_back_on(self):
        out, _ = self.run_menu("1", "1", "0")
        self.assertIn("Đã bật lại plugin 'alpha_csv'", out)
        self.assertEqual(self.app.config.get("plugins.disabled"), [])

    def test_invalid_choices_do_not_change_anything(self):
        out, _ = self.run_menu("abc", "9", "-1", "0")
        self.assertEqual(out.count("Lựa chọn không hợp lệ."), 3)
        self.assertFalse(self.app.paths.local_toml.exists())

    def test_zero_blank_and_eof_leave_cleanly(self):
        for lines in (("0",), ("",), ()):
            out, _ = self.run_menu(*lines)
            self.assertIn("Tạo mới / kiểm tra", out)

    def test_unusable_plugin_is_not_toggled(self):
        p = self.tmp / "plugins_user/alpha_csv/plugin.py"
        p.write_text(p.read_text(encoding="utf-8").replace('"api_version": 3', '"api_version": 99'), encoding="utf-8")
        out, _ = self.run_menu("1", "0")           # sắp theo id: '?alpha_csv' (manifest hỏng) đứng đầu danh sách
        self.assertIn("Không bật/tắt được", out)
        self.assertFalse(self.app.paths.local_toml.exists())

    def test_write_failure_keeps_plugin_state(self):
        with mock.patch.object(config, "save_local", side_effect=PB3Error("E_OUTPUT_DENIED", "đĩa chỉ đọc")):
            out, _ = self.run_menu("1", "0")
        self.assertIn("E_OUTPUT_DENIED", out)
        self.assertNotIn("Đã tắt plugin", out)
        self.assertNotIn("[1] alpha_csv  [disabled]", out)                   # không giả vờ đã tắt
        self.assertEqual(self.app.config.get("plugins.disabled"), [])

    def test_empty_registry_returns_without_prompt(self):
        empty = TmpCase()
        empty.setUp()
        try:
            app = make_app(empty.tmp)
            out, ask = self.run_menu(app=app)
            self.assertIn("Chưa có plugin", out)
            self.assertEqual(ask.prompts, [])
        finally:
            empty.tearDown()

    def test_lines_fit_narrow_screen(self):
        out, _ = self.run_menu("0")
        for ln in out.splitlines():
            self.assertLessEqual(len(ln), 80, ln)


class TestMenuWiring(unittest.TestCase):
    """ui/screens.run_feature chỉ truyền input_fn/say cho dịch vụ khai báo chúng; dịch vụ cũ (app) không bị vỡ."""

    class App:
        def __init__(self):
            self.log = None

        @contextlib.contextmanager
        def job(self, name, stages=None):
            yield None

        def pausing_input(self, fn):
            return fn

    def feed(self, run_interactive):
        from ui import handlers, screens
        mod = SimpleNamespace(run_interactive=run_interactive)
        said = []
        feat = handlers.Feature("plugins", "x", "services.plugin_manager", "t")
        with mock.patch.object(handlers, "load_module", return_value=mod):
            screens.run_feature(self.App(), feat, said.append, Script("7"), 40)
        return said

    def test_new_style_service_gets_menu_input_and_say(self):
        got = {}

        def svc(app, input_fn=None, say=None):
            got["answer"] = input_fn("?")
            say("xin chào")
        said = self.feed(svc)
        self.assertEqual(got["answer"], "7")
        self.assertEqual(said, ["xin chào"])

    def test_old_style_service_still_called_with_app_only(self):
        calls = []
        self.feed(lambda app: calls.append(app))
        self.assertEqual(len(calls), 1)


class TestRealCli(TmpCase):
    """Chạy menu.py thật (tiến trình con) trên bản sao dự án DÙNG CHUNG (setUpClass,
    theo TEST_STRATEGY #2). new/disable/enable chạy trong tiến trình (qua service
    thật, App thật); list kiểm chứng bằng tiến trình mới — vẫn giữ ý nghĩa
    'survives_new_process': trạng thái do một tiến trình ghi, tiến trình khác
    đọc lại từ đĩa (X1: 6 spawn -> 3)."""

    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-realcli-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, proj, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args), cwd=str(proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(90), env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_disable_survives_new_process(self):
        proj = self._shared_proj
        with bootstrap(root=str(proj)) as app:
            self.assertEqual(pm.cmd_new(app, ["format", "demo_tg"]), 0)
            self.assertEqual(pm.cmd_toggle(app, ["demo_tg"], True), 0)
        rc, out, err = self.cli(proj, "plugin", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("demo_tg  [disabled]", out, err)
        self.assertIn("plugins", (proj / "config" / "local.toml").read_text(encoding="utf-8"))
        with bootstrap(root=str(proj)) as app:
            self.assertEqual(pm.cmd_toggle(app, ["demo_tg"], False), 0)
        rc, out, err = self.cli(proj, "plugin", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("demo_tg  [ok]", out, err)
        rc, out, err = self.cli(proj, "plugin", "disable", "khong_co")
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)


if __name__ == "__main__":
    unittest.main()
