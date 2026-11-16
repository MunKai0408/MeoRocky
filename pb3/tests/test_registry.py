"""Test loader, compatibility, registry, arbitration (Claude 03, đợt 1; đặc tả 4.5, 4.6, 14.2, Phụ lục C)."""
import copy
import os
import sys
import textwrap
import unittest
from unittest import mock

from core.errors import PB3Error
from registry import arbitration as arb
from registry import compatibility as compat
from registry import loader
from registry.registry import Registry, build_registry, magic_signal, match_info, name_signal
from sdk.api import Plugin
from tests.helpers import TmpCase

BASE = {
    "id": "demo_text", "name": "Demo", "version": "1.0.0", "api_version": 3, "kind": "format",
    "capabilities": ["read"], "maturity": "M2", "priority": 40, "true_cost": "cheap", "engines": ["generic"],
    "match": {"extensions": [".txt"], "name_regex": "(?i)(lang|text)"},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:DemoPlugin", "selftest": True, "docs": "Mẫu thử.",
}


def manifest_src(**over):
    m = copy.deepcopy(BASE)
    m.update(over)
    return m


DEFAULT_BODY = "    def parse(self, ctx, cand):\n        raise NotImplementedError\n"


def plugin_py(manifest, body="", extra_import=""):
    """plugin.py với MANIFEST literal (chuỗi repr) + lớp DemoPlugin."""
    return ("from sdk.api import Plugin\n%s\nMANIFEST = %s\n\nclass DemoPlugin(Plugin):\n    MANIFEST = MANIFEST\n%s\n"
            % (extra_import, repr(manifest), body or DEFAULT_BODY))


class PluginTree(TmpCase):
    def mk(self, scope_dir, name, source=None, toml=None):
        d = os.path.join(str(self.tmp), scope_dir, name)
        os.makedirs(d, exist_ok=True)
        if source is not None:
            with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
                f.write(source)
        if toml is not None:
            with open(os.path.join(d, "plugin.toml"), "w", encoding="utf-8") as f:
                f.write(toml)
        return d

    def dirs(self):
        return [("builtin", os.path.join(str(self.tmp), "plugins")), ("user", os.path.join(str(self.tmp), "plugins_user"))]

    def load(self, **kw):
        return loader.discover(self.dirs(), **kw)


class TestStaticManifest(unittest.TestCase):
    def test_literal_with_arithmetic(self):
        d = loader.static_manifest("MANIFEST = {'a': 16 * 1024 * 1024, 'b': [1, 'x'], 'c': -1, 'd': 1 << 20}\n")
        self.assertEqual(d["a"], 16 * 1024 * 1024)
        self.assertEqual(d["d"], 1 << 20)

    def test_rejects_calls_names_and_missing(self):
        for src in ("MANIFEST = dict(a=1)\n", "X=1\nMANIFEST = {'a': X}\n", "MANIFEST = {'a': __import__('os')}\n",
                    "MANIFEST = {'a': 2 ** 99}\n", "x = 1\n", "MANIFEST = {**{}}\n", "MANIFEST = [1]\n"):
            with self.assertRaises(PB3Error, msg=src) as cm:
                loader.static_manifest(src)
            self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_syntax_error(self):
        with self.assertRaises(PB3Error):
            loader.static_manifest("MANIFEST = {")


class TestLoader(PluginTree):
    def test_valid_plugin_loaded_and_active(self):
        self.mk("plugins", "demo_text", plugin_py(manifest_src()))
        rep = self.load()
        rec = rep.by_id("demo_text")[0]
        self.assertEqual(rec.state, "ok", rec.reason)
        self.assertIsInstance(rec.instance, Plugin)
        self.assertTrue(rec.active)

    def test_missing_dirs_are_fine(self):
        self.assertEqual(self.load().records, [])

    def test_hidden_and_pycache_skipped(self):
        self.mk("plugins", "__pycache__", "x")
        self.mk("plugins", ".git", "x")
        self.assertEqual(self.load().records, [])

    def test_no_manifest_file(self):
        self.mk("plugins", "empty_dir")
        rec = self.load().records[0]
        self.assertEqual(rec.state, "disabled")
        self.assertEqual(rec.code, "E_PLUGIN_INVALID")
        self.assertTrue(rec.plugin_id.startswith("?"))

    def test_bad_manifest_does_not_crash_others(self):
        self.mk("plugins", "bad", plugin_py(manifest_src(kind="nope")))
        self.mk("plugins", "good", plugin_py(manifest_src(id="good_one")))
        rep = self.load()
        states = {r.plugin_id: r.state for r in rep.records}
        self.assertEqual(states["good_one"], "ok")
        self.assertTrue(any(s == "disabled" for k, s in states.items() if k != "good_one"))

    def test_api_version_too_new_and_too_old_disabled(self):
        self.mk("plugins", "new", plugin_py(manifest_src(id="p_new", api_version=99)))
        self.mk("plugins", "old", plugin_py(manifest_src(id="p_old", api_version=2)))
        self.mk("plugins", "str", plugin_py(manifest_src(id="p_str", api_version="3")))
        rep = self.load()
        for pid in ("p_new", "p_old", "p_str"):
            recs = rep.by_id(pid) or [r for r in rep.records if r.path.endswith(pid[2:])]
            self.assertTrue(recs and all(r.state == "disabled" and r.instance is None for r in recs), pid)
        reasons = " ".join(r.reason for r in rep.records)
        self.assertIn("api_version", reasons)

    def test_missing_dependency_unavailable_not_installed(self):
        self.mk("plugins", "needs", plugin_py(manifest_src(id="needs_lib", requires=["no_such_module_xyz"])))
        rec = self.load().by_id("needs_lib")[0]
        self.assertEqual(rec.state, "unavailable")
        self.assertIn("no_such_module_xyz", rec.reason)
        self.assertIsNone(rec.instance)          # chưa import mã plugin khi thiếu dependency

    def test_dependency_present_via_finder(self):
        self.mk("plugins", "needs", plugin_py(manifest_src(id="needs_lib", requires=["lz4>=3"])))
        self.assertEqual(self.load(finder=lambda m: m == "lz4").by_id("needs_lib")[0].state, "ok")
        self.assertEqual(self.load(finder=lambda m: False).by_id("needs_lib")[0].state, "unavailable")

    def test_import_error_isolated(self):
        self.mk("plugins", "boom", plugin_py(manifest_src(id="boom_p"), extra_import="import module_that_does_not_exist_abc"))
        self.mk("plugins", "ok", plugin_py(manifest_src(id="fine_p")))
        rep = self.load()
        self.assertEqual(rep.by_id("boom_p")[0].state, "disabled")
        self.assertIn("ModuleNotFoundError", rep.by_id("boom_p")[0].reason)
        self.assertEqual(rep.by_id("fine_p")[0].state, "ok")

    def test_exception_at_import_time_isolated(self):
        self.mk("plugins", "raise", plugin_py(manifest_src(id="raise_p"), extra_import="raise RuntimeError('x')"))
        self.assertEqual(self.load().by_id("raise_p")[0].state, "disabled")

    def test_missing_class_and_wrong_base_class(self):
        self.mk("plugins", "noclass", plugin_py(manifest_src(id="noclass_p", entry="plugin:Missing")))
        src = "MANIFEST = %r\nclass Foo:\n    pass\n" % manifest_src(id="notplugin_p", entry="plugin:Foo")
        self.mk("plugins", "notplugin", src)
        rep = self.load()
        self.assertEqual(rep.by_id("noclass_p")[0].state, "disabled")
        self.assertEqual(rep.by_id("notplugin_p")[0].state, "disabled")

    def test_capability_without_hook_disabled(self):
        self.mk("plugins", "nohook", plugin_py(manifest_src(id="nohook_p", capabilities=["read", "write"],
                                                              write_modes=["REBUILD_SECTION"], maturity="M2")))
        self.assertEqual(self.load().by_id("nohook_p")[0].state, "disabled")

    def test_manifest_changed_at_runtime_rejected(self):
        src = plugin_py(manifest_src(id="shift_p"))
        src += "\nDemoPlugin.MANIFEST = dict(MANIFEST, priority=999)\n"
        self.mk("plugins", "shift", src)
        rec = self.load().by_id("shift_p")[0]
        self.assertEqual(rec.state, "disabled")
        self.assertIn("khác manifest", rec.reason)

    def test_entry_traversal_blocked(self):
        self.mk("plugins", "trav", plugin_py(manifest_src(id="trav_p", entry="plugin.__x:DemoPlugin")))
        self.assertEqual(self.load().by_id("trav_p")[0].state, "disabled")

    def test_toml_manifest_preferred_and_not_executing(self):
        marker = os.path.join(str(self.tmp), "ran.flag")
        src = "open(%r, 'w').write('x')\nfrom sdk.api import Plugin\nclass DemoPlugin(Plugin):\n    MANIFEST = {}\n" % marker
        toml = textwrap.dedent('''\
            id = "toml_p"
            name = "T"
            version = "1.0.0"
            api_version = 99
            kind = "format"
            capabilities = ["read"]
            maturity = "M2"
            priority = 10
            entry = "plugin:DemoPlugin"
            selftest = true
            docs = "x"
            [match]
            extensions = [".txt"]
            [limits]
            max_entry_bytes = 1048576
            hook_timeout_s = 5
            ''')
        self.mk("plugins", "t", src, toml)
        rec = self.load().by_id("toml_p")[0]
        self.assertEqual(rec.state, "disabled")                    # api_version 99
        self.assertFalse(os.path.exists(marker), "mã plugin KHÔNG được chạy khi manifest/api đã bị loại")

    def test_code_not_run_for_unavailable(self):
        marker = os.path.join(str(self.tmp), "ran2.flag")
        self.mk("plugins", "m", plugin_py(manifest_src(id="m_p", requires=["no_such_mod_q"]),
                                          extra_import="open(%r, 'w').write('x')" % marker))
        self.load()
        self.assertFalse(os.path.exists(marker))

    def test_duplicate_user_wins_with_warning(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p", priority=10)))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p", priority=20)))
        rep = self.load()
        recs = rep.by_id("dup_p")
        win = [r for r in recs if r.state == "ok"]
        self.assertEqual(len(win), 1)
        self.assertEqual(win[0].scope, "user")
        lose = [r for r in recs if r.state != "ok"][0]
        self.assertEqual(lose.scope, "builtin")
        self.assertIn("ghi đè", lose.reason)
        self.assertTrue(any("dup_p" in w for w in rep.warnings))

    def test_only_ids_skips_import_of_others(self):
        """X13 B2: nạp có đích — plugin ngoài only_ids không bị import (lỗi import của nó không xuất hiện)."""
        self.mk("plugins", "a", plugin_py(manifest_src(id="good_a")))
        self.mk("plugins", "b", plugin_py(manifest_src(id="bad_b"), extra_import="raise RuntimeError('B imported')"))
        rep = self.load(only_ids={"good_a"})
        good = rep.by_id("good_a")[0]
        self.assertEqual(good.state, "ok", good.reason)
        self.assertIsNotNone(good.instance)
        bad = rep.by_id("bad_b")[0]
        self.assertIsNone(bad.instance)
        self.assertEqual(bad.state, "disabled")
        self.assertIn("không nằm trong phạm vi nạp", bad.reason)
        # Nạp đủ: lỗi import của B lộ ra (chứng minh test trên phân biệt được).
        full = self.load()
        self.assertIn("RuntimeError", full.by_id("bad_b")[0].reason)

    def test_only_ids_keeps_user_wins_dedupe(self):
        """X13 B2: nạp có đích vẫn giữ quy tắc user thắng builtin trên cùng id (decision #60)."""
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p", priority=10)))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p", priority=20)))
        rep = self.load(only_ids={"dup_p"})
        win = [r for r in rep.by_id("dup_p") if r.state == "ok"]
        self.assertEqual(len(win), 1)
        self.assertEqual(win[0].scope, "user")

    def test_user_broken_falls_back_to_builtin(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p")))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p"), extra_import="raise RuntimeError('x')"))
        rep = self.load()
        ok = [r for r in rep.by_id("dup_p") if r.state == "ok"]
        self.assertEqual([r.scope for r in ok], ["builtin"])
        self.assertTrue(any("user" in w for w in rep.warnings))

    def test_duplicate_same_scope_both_disabled(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p")))
        self.mk("plugins", "b", plugin_py(manifest_src(id="dup_p")))
        rep = self.load()
        self.assertTrue(all(r.state == "disabled" for r in rep.by_id("dup_p")))
        self.assertEqual(len(rep.by_id("dup_p")), 2)

    def test_registry_keeps_user_winner_for_duplicate_id(self):
        # Hồi quy: loader đã cho user thắng, nhưng Registry.add() giữ bản đến
        # trước (builtin, bị ghi đè) nên `plugin list` hiện cả hai đều disabled.
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p", priority=10)))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p", priority=20)))
        reg = Registry.from_report(self.load())
        win = reg.get("dup_p")
        self.assertIsNotNone(win)
        self.assertEqual(win.scope, "user")
        self.assertEqual(win.state, "ok")
        rows = {r["scope"]: r for r in reg.rows() if r["id"] == "dup_p"}
        self.assertEqual(rows["user"]["state"], "ok")
        self.assertEqual(rows["builtin"]["state"], "disabled")
        self.assertIn("ghi đè", rows["builtin"]["reason"])

    def test_registry_keeps_user_copy_when_both_fail_compat(self):
        # Cả hai bản đều thiếu requires: không bản nào qua staging của loader,
        # nên bản hiển thị vẫn phải là bản user (4.5: plugins_user thắng).
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p", requires=["no_such_mod_q"])))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p", requires=["no_such_mod_q"])))
        reg = Registry.from_report(self.load())
        win = reg.get("dup_p")
        self.assertIsNotNone(win)
        self.assertEqual(win.scope, "user")
        self.assertEqual(win.state, "unavailable")
        self.assertIn("no_such_mod_q", win.reason)
        rows = {r["scope"]: r for r in reg.rows() if r["id"] == "dup_p"}
        self.assertEqual(rows["builtin"]["state"], "unavailable")
        self.assertIn("no_such_mod_q", rows["builtin"]["reason"])

    def test_registry_keeps_builtin_when_user_import_fails(self):
        # User thắng chỉ khi bản user nạp được; bản user hỏng import thì
        # builtin vẫn là bản dùng được (không bị luật user-thắng lật ngược).
        self.mk("plugins", "a", plugin_py(manifest_src(id="dup_p")))
        self.mk("plugins_user", "a", plugin_py(manifest_src(id="dup_p"), extra_import="raise RuntimeError('x')"))
        reg = Registry.from_report(self.load())
        win = reg.get("dup_p")
        self.assertIsNotNone(win)
        self.assertEqual(win.scope, "builtin")
        self.assertEqual(win.state, "ok")

    def test_user_disabled_flag(self):
        self.mk("plugins", "a", plugin_py(manifest_src()))
        rec = self.load(disabled_ids=["demo_text"]).by_id("demo_text")[0]
        self.assertEqual(rec.state, "ok")
        self.assertFalse(rec.enabled)
        self.assertFalse(rec.active)
        self.assertEqual(rec.display_state, "disabled")

    def test_symlink_escape_blocked(self):
        outside = os.path.join(str(self.tmp), "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "plugin.py"), "w", encoding="utf-8") as f:
            f.write(plugin_py(manifest_src(id="esc_p")))
        os.makedirs(os.path.join(str(self.tmp), "plugins"))
        try:
            os.symlink(outside, os.path.join(str(self.tmp), "plugins", "esc"))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("không tạo được symlink")
        rec = self.load().records[0]
        self.assertEqual(rec.state, "disabled")
        self.assertEqual(rec.code, "E_UNSAFE_PATH")

    def test_import_guard_hook_called(self):
        calls = []
        from contextlib import contextmanager

        @contextmanager
        def guard(pid):
            calls.append(pid)
            yield
        self.mk("plugins", "a", plugin_py(manifest_src()))
        self.load(import_guard=guard)
        self.assertEqual(calls, ["demo_text"])

    def test_oversized_plugin_file_rejected(self):
        self.mk("plugins", "big", "#" * (loader.MAX_SOURCE_BYTES + 10))
        self.assertEqual(self.load().records[0].state, "disabled")

    def test_load_plugin_class_unified_path(self):
        from sdk.api import Plugin as P

        class Good(P):
            MANIFEST = manifest_src(id="inproc_p")

            def parse(self, ctx, cand):
                raise NotImplementedError

        class Bad(P):
            MANIFEST = manifest_src(id="inproc_bad", api_version=1)
        self.assertEqual(loader.load_plugin_class(Good).state, "ok")
        self.assertEqual(loader.load_plugin_class(Bad).state, "disabled")
        self.assertEqual(loader.load_plugin_class(type("X", (), {})).state, "disabled")


class TestCompatibility(unittest.TestCase):
    def test_module_for_requirement(self):
        self.assertEqual(compat.module_for_requirement("unitypy"), "UnityPy")
        self.assertEqual(compat.module_for_requirement("lz4>=3.1"), "lz4")
        self.assertIsNone(compat.module_for_requirement("  "))
        self.assertIsNone(compat.module_for_requirement(5))

    def test_check_api(self):
        self.assertTrue(compat.check_api(3).ok)
        self.assertIn("mới hơn", compat.check_api(4).reason)
        self.assertIn("cũ hơn", compat.check_api(2).reason)
        self.assertEqual(compat.check_api(True).state, "disabled")

    def test_default_finder_stdlib(self):
        self.assertTrue(compat.default_finder("json"))
        self.assertFalse(compat.default_finder("zzz_not_a_module"))


class TestRegistry(PluginTree):
    def build(self):
        self.mk("plugins", "json", plugin_py(manifest_src(
            id="text_json", priority=50, match={"extensions": [".json"], "magic": [{"offset": 0, "hex": "7B"}]})))
        self.mk("plugins", "csv", plugin_py(manifest_src(id="text_csv", priority=30, engines=["unity"],
                                                          match={"extensions": [".csv"]})))
        self.mk("plugins", "blob", plugin_py(manifest_src(
            id="blob_x", kind="extractor", maturity="M1", true_cost="heavy", priority=90,
            match={"extensions": [".bin"], "name_regex": "(?i)lang"})))
        self.mk("plugins", "zip", plugin_py(manifest_src(
            id="zip_c", kind="container", capabilities=["container"], priority=60,
            match={"extensions": [".zip", ".ipa"], "magic": [{"offset": 0, "hex": "504B0304"}]},
            true_cost="cheap"), body="    def open_container(self, ctx, path):\n        raise NotImplementedError\n"))
        self.mk("plugins", "off", plugin_py(manifest_src(id="off_p", api_version=7)))
        return Registry.from_report(self.load())

    def test_index_lookups(self):
        r = self.build()
        self.assertEqual([x.plugin_id for x in r.by_extension(".json")], ["text_json"])
        self.assertEqual([x.plugin_id for x in r.by_kind("container")], ["zip_c"])
        self.assertEqual([x.plugin_id for x in r.by_engine("UNITY")], ["text_csv"])
        self.assertIn("zip_c", [x.plugin_id for x in r.by_capability("container")])
        self.assertEqual(r.by_extension(".nope"), [])

    def test_inactive_plugins_not_indexed(self):
        r = self.build()
        self.assertNotIn("off_p", [x.plugin_id for x in r.active()])
        rows = {x["id"]: x for x in r.rows()}
        self.assertEqual(rows["text_json"]["state"], "ok")
        bad = [x for x in r.rows() if x["state"] == "disabled" and "api_version" in x["reason"]]
        self.assertTrue(bad)

    def test_set_enabled(self):
        r = self.build()
        r.set_enabled("text_json", False)
        self.assertEqual(r.by_extension(".json"), [])
        self.assertEqual({x["id"]: x["state"] for x in r.rows()}["text_json"], "disabled")
        r.set_enabled("text_json", True)
        self.assertEqual(len(r.by_extension(".json")), 1)
        with self.assertRaises(PB3Error):
            r.set_enabled("missing", True)

    def test_cannot_enable_unavailable(self):
        self.mk("plugins", "n", plugin_py(manifest_src(id="needs_q", requires=["no_such_mod_q"])))
        r = Registry.from_report(self.load())
        with self.assertRaises(PB3Error) as cm:
            r.set_enabled("needs_q", True)
        self.assertIn("no_such_mod_q", cm.exception.detail)

    def test_add_rejects_ok_record_without_instance(self):
        rec = loader.PluginRecord("x_p", "builtin", "p", state="ok")
        with self.assertRaises(PB3Error):
            Registry().add(rec)

    def test_candidates_order_magic_then_ext_then_name_then_heavy(self):
        r = self.build()
        got = r.candidates_for("lang.json", head=b"{\"a\":1}")
        # "lang" cũng khớp name_regex của plugin heavy -> vẫn có mặt nhưng LUÔN cuối (4.5 mục 5)
        self.assertEqual([m.plugin_id for m in got], ["text_json", "blob_x"])
        self.assertEqual(got[0].magic_match, 0.5)                          # magic 1 byte (7B) chỉ 0.5
        self.assertEqual(got[0].stage, 2)
        self.assertEqual(got[1].stage, 5)
        got = r.candidates_for("game.ipa", head=b"PK\x03\x04rest")
        self.assertEqual(got[0].plugin_id, "zip_c")
        self.assertEqual(got[0].stage, 1)
        got = r.candidates_for("lang_pack.bin")
        self.assertEqual([m.plugin_id for m in got], ["blob_x"])
        self.assertEqual(got[0].stage, 5)                                   # heavy luôn cuối

    def test_candidates_none_for_unknown(self):
        self.assertEqual(self.build().candidates_for("photo.png", head=b"\x89PNG"), [])

    def test_candidates_filter_kinds_and_capability(self):
        r = self.build()
        self.assertEqual(r.candidates_for("a.zip", head=b"PK\x03\x04", kinds=["format"]), [])
        self.assertEqual(len(r.candidates_for("a.zip", head=b"PK\x03\x04", capability="container")), 1)

    def test_heavy_after_cheap_even_with_higher_priority(self):
        r = self.build()
        self.mk("plugins", "ext2", plugin_py(manifest_src(id="cheap_bin", priority=1, match={"extensions": [".bin"]})))
        r2 = Registry.from_report(self.load())
        self.assertEqual([m.plugin_id for m in r2.candidates_for("x.bin")], ["cheap_bin", "blob_x"])

    def test_profile_stage(self):
        r = self.build()
        got = r.candidates_for("unknown.dat", profile_ids=["text_csv"])
        self.assertEqual([(m.plugin_id, m.stage) for m in got], [("text_csv", 4)])

    def test_build_registry_from_project_root(self):
        self.mk("plugins", "a", plugin_py(manifest_src()))
        r = build_registry(str(self.tmp))
        self.assertEqual([x.plugin_id for x in r.active()], ["demo_text"])

    def test_real_project_has_first_builtin_plugin_zip(self):
        # Claude 04: plugin builtin đầu tiên là zip (container, thật, có test);
        # Claude 06 thêm 12 plugin định dạng builtin thật (decisions #117-124).
        # Claude 08 thêm 7 plugin engine game thật (decisions #154-160):
        # godot_pck, godot_translation, unreal_pak, unreal_locres,
        # cocos_detect, defold_detect, gamemaker_detect.
        # Vẫn không có plugin MẪU/giả (decisions #19): mọi id dưới đây đều có
        # plugin thật + fixture + test.
        from tests.helpers import ROOT
        r = build_registry(str(ROOT))
        rows = {row["id"]: row for row in r.rows()}
        self.assertEqual(sorted(rows), ["apple_plist", "apple_strings", "cocos_detect",
                                        "csv_table", "defold_detect", "gamemaker_detect",
                                        "generic_strings", "godot_pck", "godot_translation",
                                        "json_text", "po_mo", "properties",
                                        "resx", "sqlite_loc", "unreal_locres", "unreal_pak",
                                        "xliff", "xml_strings", "yaml_ini",
                                        "zip"])
        self.assertEqual(rows["zip"]["state"], "ok")
        self.assertEqual(rows["zip"]["kind"], "container")
        self.assertEqual(rows["zip"]["maturity"], "M2")
        for pid in ("json_text", "csv_table", "xml_strings", "yaml_ini", "properties",
                    "apple_plist", "apple_strings", "po_mo", "xliff", "resx"):
            self.assertEqual(rows[pid]["state"], "ok", pid)
            self.assertEqual(rows[pid]["kind"], "format", pid)
            self.assertEqual(rows[pid]["maturity"], "M3", pid)
        for pid in ("sqlite_loc", "generic_strings"):
            self.assertEqual(rows[pid]["state"], "ok", pid)
            self.assertEqual(rows[pid]["maturity"], "M2", pid)
        self.assertEqual(rows["sqlite_loc"]["kind"], "format")
        self.assertEqual(rows["generic_strings"]["kind"], "extractor")
        # Claude 08: plugin engine game (decisions #154-160).
        for pid in ("godot_pck", "unreal_pak"):
            self.assertEqual(rows[pid]["state"], "ok", pid)
            self.assertEqual(rows[pid]["kind"], "container", pid)
            self.assertEqual(rows[pid]["maturity"], "M1", pid)
        for pid in ("godot_translation", "unreal_locres"):
            self.assertEqual(rows[pid]["state"], "ok", pid)
            self.assertEqual(rows[pid]["kind"], "format", pid)
            self.assertEqual(rows[pid]["maturity"], "M2", pid)
        for pid in ("cocos_detect", "defold_detect", "gamemaker_detect"):
            self.assertEqual(rows[pid]["state"], "ok", pid)
            self.assertEqual(rows[pid]["kind"], "engine", pid)
            self.assertEqual(rows[pid]["maturity"], "M2", pid)


WRITE_BODY = ("    def parse(self, ctx, cand):\n        raise NotImplementedError\n"
              "    def build(self, ctx, table, base_bytes):\n        raise NotImplementedError\n")


class TestMaturityEvidence(PluginTree):
    """R5 (decisions #51): M3/M4 không có bằng chứng `tests/corpus/<id>.json` -> hạ hiệu lực (sàn M2), plugin vẫn nạp."""

    def corpus(self):
        d = os.path.join(str(self.tmp), "tests", "corpus")
        os.makedirs(d, exist_ok=True)
        return d

    def put(self, pid, data):
        import json
        with open(os.path.join(self.corpus(), pid + ".json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def add(self, pid, maturity="M4", **over):
        self.mk("plugins", pid, plugin_py(manifest_src(
            id=pid, capabilities=["read", "write"], write_modes=["REBUILD_SECTION"], maturity=maturity, **over),
            body=WRITE_BODY))

    def rec(self, pid):
        rep = self.load(corpus_dir=self.corpus())
        return rep, rep.by_id(pid)[0]

    def test_no_evidence_downgrades_but_plugin_stays_active(self):
        self.add("w_none")
        rep, rec = self.rec("w_none")
        self.assertEqual(rec.state, "ok", rec.reason)
        self.assertTrue(rec.active)
        self.assertEqual(rec.manifest.maturity, "M4")            # manifest không bị sửa
        self.assertEqual(rec.maturity_effective, "M2")
        self.assertTrue(any("w_none" in w and "M4" in w and "M2" in w for w in rep.warnings), rep.warnings)

    def test_full_evidence_keeps_declared(self):
        self.add("w_full")
        self.put("w_full", {"id": "w_full", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}})
        rep, rec = self.rec("w_full")
        self.assertEqual(rec.maturity_effective, "M4")
        # X16: bằng chứng không ghi version vẫn chấp nhận, nhưng kèm ghi chú nhắc ghi version.
        self.assertTrue(any("w_full" in w and "không ghi version" in w for w in rep.warnings), rep.warnings)

    def test_matching_version_keeps_level_without_note(self):
        """X16: bằng chứng ghi version khớp manifest -> giữ mức, không cảnh báo."""
        self.add("v_match", version="1.2.3")
        self.put("v_match", {"id": "v_match", "version": "1.2.3",
                             "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}})
        rep, rec = self.rec("v_match")
        self.assertEqual(rec.maturity_effective, "M4")
        self.assertEqual([w for w in rep.warnings if "v_match" in w], [])

    def test_mismatched_version_downgrades(self):
        """X16: bằng chứng của bản plugin khác (version khác manifest) -> không tính, hạ M2."""
        self.add("v_old", version="1.2.3")
        self.put("v_old", {"id": "v_old", "version": "9.9.9",
                           "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}})
        rep, rec = self.rec("v_old")
        self.assertEqual(rec.maturity_effective, "M2")
        self.assertTrue(any("v_old" in w and "version" in w for w in rep.warnings), rep.warnings)

    def test_missing_version_accepted_with_note(self):
        """X16: bằng chứng không ghi version -> chấp nhận (tương thích cũ) + ghi chú."""
        self.add("v_nov", version="2.0.0")
        self.put("v_nov", {"id": "v_nov", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}})
        rep, rec = self.rec("v_nov")
        self.assertEqual(rec.maturity_effective, "M4")
        self.assertTrue(any("v_nov" in w and "không ghi version" in w for w in rep.warnings), rep.warnings)

    def test_partial_evidence_caps_at_proven_level(self):
        self.add("w_part")
        self.put("w_part", {"id": "w_part", "results": {"read": "PASS", "write": "PASS", "verify": "FAIL"}})
        rep, rec = self.rec("w_part")
        self.assertEqual(rec.maturity_effective, "M3")
        self.assertTrue(any("verify" in w for w in rep.warnings), rep.warnings)
        self.add("w_m3", maturity="M3")
        self.put("w_m3", {"id": "w_m3", "results": {"read": "PASS", "write": "PASS"}})
        self.assertEqual(self.rec("w_m3")[1].maturity_effective, "M3")

    def test_bad_evidence_counts_as_none(self):
        cases = {
            "e_id": {"id": "khac", "results": {"read": "PASS", "write": "PASS"}},      # id không khớp
            "e_json": "{không phải json",
            "e_list": "[1, 2]",
            "e_fail": {"id": "e_fail", "results": {"read": "PASS", "write": "FAIL"}},
            "e_true": {"id": "e_true", "results": {"read": True, "write": True}},      # phải đúng chuỗi "PASS"
            "e_empty": {"id": "e_empty", "results": {}},
        }
        for pid, data in cases.items():
            self.add(pid)
            self.put(pid, data)
            self.assertEqual(self.rec(pid)[1].maturity_effective, "M2", pid)

    def test_oversized_evidence_rejected(self):
        from registry.evidence import MAX_EVIDENCE_BYTES
        self.add("e_big")
        self.put("e_big", '{"id": "e_big", "results": {"read": "PASS", "write": "PASS"}, "pad": "%s"}'
                 % ("x" * MAX_EVIDENCE_BYTES))
        self.assertEqual(self.rec("e_big")[1].maturity_effective, "M2")

    def test_symlinked_evidence_cannot_escape_corpus(self):
        from tests.helpers import can_symlink
        if not can_symlink(self.tmp):
            self.skipTest("không có symlink")
        import json
        outside = os.path.join(str(self.tmp), "outside.json")
        with open(outside, "w", encoding="utf-8") as f:
            json.dump({"id": "e_link", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}}, f)
        self.add("e_link")
        os.symlink(outside, os.path.join(self.corpus(), "e_link.json"))
        self.assertEqual(self.rec("e_link")[1].maturity_effective, "M2")

    def test_m2_and_below_not_checked(self):
        self.mk("plugins", "plain", plugin_py(manifest_src(id="plain_p", maturity="M2")))
        rep, rec = self.rec("plain_p")
        self.assertEqual(rec.maturity_effective, "M2")
        self.assertEqual([w for w in rep.warnings if "plain_p" in w], [])

    def test_no_corpus_dir_means_not_checked(self):
        self.add("w_off")
        rec = self.load().by_id("w_off")[0]                       # discover cũ, không truyền corpus_dir
        self.assertEqual(rec.maturity_effective, "M4")

    def test_build_registry_uses_project_corpus(self):
        self.add("w_reg")
        r = build_registry(str(self.tmp))
        row = {x["id"]: x for x in r.rows()}["w_reg"]
        self.assertEqual((row["maturity"], row["maturity_effective"]), ("M4", "M2"))
        self.put("w_reg", {"id": "w_reg", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}})
        row = {x["id"]: x for x in build_registry(str(self.tmp)).rows()}["w_reg"]
        self.assertEqual(row["maturity_effective"], "M4")

    def test_ranking_uses_effective_maturity(self):
        """Plugin khai M4 không bằng chứng KHÔNG được thắng plugin M3 có bằng chứng khi cùng giai đoạn/priority."""
        ext = {"extensions": [".kv"]}
        self.add("liar_p", maturity="M4", priority=50, match=ext)
        self.add("honest_p", maturity="M3", priority=50, match=ext)
        self.put("honest_p", {"id": "honest_p", "results": {"read": "PASS", "write": "PASS"}})
        r = build_registry(str(self.tmp))
        got = [i.plugin_id for i in r.candidates_for("x/a.kv")]
        self.assertEqual(got, ["honest_p", "liar_p"])

    def test_arbitration_contender_takes_effective_maturity(self):
        class M:
            id, maturity, priority = "p", "M4", 50

        class Cand:
            path, score, reasons = "a.kv", 0.9, []

        class Match:
            magic_match = ext_match = name_signal = 0.0
            reasons = []
        self.assertEqual(arb.contender_from_candidate(M, Cand, Match).maturity, "M4")
        self.assertEqual(arb.contender_from_candidate(M, Cand, Match, maturity="M2").maturity, "M2")

    def test_plugin_list_shows_declared_to_effective(self):
        from services import plugin_manager as pm
        row = {"id": "w_x", "kind": "format", "engines": [], "maturity": "M4", "maturity_effective": "M2",
               "version": "1.0.0", "scope": "user", "state": "ok", "reason": ""}
        self.assertIn("M4→M2", pm.format_rows([row]))
        row["maturity_effective"] = "M4"
        self.assertNotIn("→", pm.format_rows([row]))


class TestDiscoveryCache(PluginTree):
    """X13 B1: cache duyệt manifest tĩnh — chỉ cache phần tĩnh, import luôn chạy lại."""

    def cache_file(self):
        return os.path.join(str(self.tmp), "workspace", "cache", "discovery_cache.json")

    def counting(self, calls):
        import registry.loader as loader
        orig = loader.read_manifest_dict

        def wrap(pdir):
            calls.append(pdir)
            return orig(pdir)
        return mock.patch.object(loader, "read_manifest_dict", wrap)

    def test_cache_hit_skips_manifest_read(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="c_a")))
        self.mk("plugins", "b", plugin_py(manifest_src(id="c_b")))
        calls = []
        with self.counting(calls):
            r1 = build_registry(str(self.tmp))
            self.assertGreater(len(calls), 0)
            self.assertEqual({x.plugin_id for x in r1.records() if x.state == "ok"}, {"c_a", "c_b"})
            self.assertTrue(os.path.isfile(self.cache_file()))
            calls.clear()
            r2 = build_registry(str(self.tmp))
            self.assertEqual(len(calls), 0)   # hit cache: không đọc lại manifest
            self.assertEqual({x.plugin_id for x in r2.records() if x.state == "ok"}, {"c_a", "c_b"})

    def test_manifest_change_invalidates(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="c_a", priority=10)))
        self.mk("plugins", "b", plugin_py(manifest_src(id="c_b")))
        build_registry(str(self.tmp))
        self.mk("plugins", "a", plugin_py(manifest_src(id="c_a", priority=99)))   # sửa manifest
        calls = []
        with self.counting(calls):
            r = build_registry(str(self.tmp))
            self.assertEqual(r.get("c_a").manifest.priority, 99)
            self.assertEqual(len(calls), 1)   # chỉ đọc lại file đã đổi; b vẫn hit cache
            self.assertTrue(calls[0].endswith(os.path.join("plugins", "a")))

    def test_cache_off_flag_disables(self):
        from core import config as cfgmod
        from core.paths import Paths
        (self.tmp / "config").mkdir()
        p = Paths(self.tmp)
        p.local_toml.write_text("[registry]\ndiscovery_cache = false\n", encoding="utf-8")
        cfg = cfgmod.load_config(p)
        self.assertFalse(cfg.get("registry.discovery_cache"))
        self.mk("plugins", "a", plugin_py(manifest_src(id="c_a")))
        calls = []
        with self.counting(calls):
            build_registry(str(self.tmp), cfg)
            n1 = len(calls)
            self.assertGreater(n1, 0)
            build_registry(str(self.tmp), cfg)
            self.assertGreater(len(calls), n1)   # tắt cache: đọc lại lần 2
            self.assertFalse(os.path.isfile(self.cache_file()))

    def test_corrupt_cache_ignored(self):
        self.mk("plugins", "a", plugin_py(manifest_src(id="c_a")))
        build_registry(str(self.tmp))
        with open(self.cache_file(), "w", encoding="utf-8") as f:
            f.write("{không phải json{{{")
        r = build_registry(str(self.tmp))
        self.assertEqual(r.get("c_a").state, "ok")

    def test_code_change_reflected_on_cache_hit(self):
        """Đổi code ở file phụ (manifest không đổi) -> cache vẫn hit, nhưng import chạy lại nên code mới có hiệu lực."""
        self.mk("plugins", "m", plugin_py(manifest_src(id="multi_p"),
                                          body='    VALUE = VALUE\n\n    def parse(self, ctx, cand):\n        raise NotImplementedError\n',
                                          extra_import="from .helper import VALUE"))
        helper = os.path.join(str(self.tmp), "plugins", "m", "helper.py")
        with open(helper, "w", encoding="utf-8") as f:
            f.write('VALUE = "v1"\n')
        r1 = build_registry(str(self.tmp))
        self.assertEqual(r1.get("multi_p").instance.VALUE, "v1")
        with open(helper, "w", encoding="utf-8") as f:
            f.write('VALUE = "v2"\n')
        calls = []
        with self.counting(calls):
            r2 = build_registry(str(self.tmp))
            self.assertEqual(len(calls), 0)   # manifest không đổi -> hit cache
        self.assertEqual(r2.get("multi_p").instance.VALUE, "v2")   # import chạy lại


class TestSignals(unittest.TestCase):
    def test_magic_levels(self):
        self.assertEqual(magic_signal([{"offset": 0, "hex": "504B0304"}], b"PK\x03\x04abc"), 1.0)
        self.assertEqual(magic_signal([{"offset": 0, "hex": "504B0304"}], b"PK"), 0.5)       # mới khớp phần đầu
        self.assertEqual(magic_signal([{"offset": 0, "hex": "7B"}], b"{"), 0.5)               # magic ngắn
        self.assertEqual(magic_signal([{"offset": 0, "hex": "504B0304"}], b"XXXXXXXX"), 0.0)
        self.assertEqual(magic_signal([{"offset": 4, "hex": "41424344"}], b"....ABCD"), 1.0)
        self.assertEqual(magic_signal([{"offset": 0, "hex": "41424344"}], b""), 0.0)

    def test_name_signal(self):
        self.assertEqual(name_signal("(?i)lang", "a/b/Lang_en.txt"), 1.0)
        self.assertEqual(name_signal("(?i)lang", "lang/data.txt"), 0.6)
        self.assertEqual(name_signal("(?i)lang", "x/y.txt"), 0.0)
        self.assertEqual(name_signal("(", "x"), 0.0)

    def test_match_info_container_kind(self):
        rec = loader.load_plugin_class(type("C", (Plugin,), {"MANIFEST": manifest_src(
            id="ck_p", match={"container_kind": "zip"})}))
        self.assertEqual(match_info(rec, "a.bin", b"", "zip").stage, 2)
        self.assertEqual(match_info(rec, "a.bin", b"", "tar").stage, 0)


def C(aid, path="a/lang.json", maturity="M2", priority=50, **sig):
    return arb.Contender(aid, path, arb.Signals(**sig), maturity, priority)


FULL = dict(magic_match=1.0, ext_match=1.0, name_signal=1.0, schema_fit=1.0, locale_evidence=1.0,
            manifest_ref=1.0, content_lang_fit=1.0, kb_agreement=1.0)


class TestArbitration(unittest.TestCase):
    def test_default_weights_sum_to_one_and_match_config(self):
        w = arb.Weights()
        self.assertAlmostEqual(sum(v for _, v in w.as_pairs()), 1.0)
        from core.config import load_config
        from core.paths import Paths
        from tests.helpers import ROOT
        self.assertEqual(arb.Weights.from_config(load_config(Paths(ROOT), apply_paths=False)), w)

    def test_score_formula_appendix_c(self):
        w = arb.Weights()
        s = arb.Signals(magic_match=1.0, ext_match=1.0, schema_fit=0.5)
        self.assertAlmostEqual(arb.score(s, w), 0.30 + 0.10 + 0.09)
        self.assertAlmostEqual(arb.score(arb.Signals(**FULL), w), 1.0)
        self.assertEqual(arb.score(arb.Signals(), w), 0.0)

    def test_signal_validation_and_clamp(self):
        self.assertEqual(arb.Signals(magic_match=5).magic_match, 1.0)
        self.assertEqual(arb.Signals(magic_match=-1).magic_match, 0.0)
        for bad in (True, "1", float("nan"), None):
            with self.assertRaises(PB3Error):
                arb.Signals(magic_match=bad)

    def test_auto_select(self):
        d = arb.arbitrate([C("a", **FULL), C("b", magic_match=1.0, ext_match=1.0, name_signal=1.0, schema_fit=0.5)])
        self.assertEqual((d.status, d.code, d.chosen.adapter_id), ("auto", "E_OK", "a"))
        self.assertEqual(d.rejected[0].adapter_id, "b")
        self.assertIn("điểm thấp hơn", d.rejected[0].reason)
        self.assertIsNone(d.to_diagnostic())

    def test_ask_band_max_five_options(self):
        cs = [C("p%d" % i, path="f%d.json" % i, magic_match=1.0, ext_match=1.0, name_signal=1.0, schema_fit=0.7 - i * 0.001)
              for i in range(8)]
        # tổng = 0.30+0.10+0.12+0.18*0.7 ~ 0.646 -> vùng hỏi
        d = arb.arbitrate(cs)
        self.assertEqual((d.status, d.code), ("ask", "E_AMBIGUOUS"))
        self.assertIsNone(d.chosen)
        self.assertEqual(len(d.options), 5)
        self.assertEqual(len(d.rejected), 3)
        self.assertEqual(d.to_diagnostic().code, "E_AMBIGUOUS")

    def test_below_threshold_no_candidate_with_nearest(self):
        cs = [C("x%d" % i, path="p%d" % i, ext_match=1.0, schema_fit=0.1 * i) for i in range(7)]
        d = arb.arbitrate(cs)
        self.assertEqual((d.status, d.code), ("none", "E_NO_CANDIDATE"))
        self.assertEqual(len(d.nearest), 5)
        self.assertEqual(d.nearest[0].adapter_id, "x6")
        diag = d.to_diagnostic()
        self.assertEqual(diag.code, "E_NO_CANDIDATE")
        self.assertIn("x6", diag.detail)

    def test_empty_input(self):
        d = arb.arbitrate([])
        self.assertEqual((d.status, d.code, d.nearest), ("none", "E_NO_CANDIDATE", []))

    def test_boundaries_inclusive(self):
        w = arb.Weights(w_magic=1.0, w_ext=0, w_name=0, w_schema=0, w_locale=0, w_manifest=0, w_content=0, w_kb=0)
        self.assertEqual(arb.arbitrate([C("a", magic_match=0.8)], w).status, "auto")
        self.assertEqual(arb.arbitrate([C("a", magic_match=0.55)], w).status, "ask")
        self.assertEqual(arb.arbitrate([C("a", magic_match=0.54)], w).status, "none")

    def test_tiebreak_order(self):
        base = dict(FULL)
        # maturity
        d = arb.arbitrate([C("a", maturity="M1", **base), C("b", maturity="M3", **base)])
        self.assertEqual(d.chosen.adapter_id, "b")
        self.assertTrue(d.used_tiebreak)
        self.assertIn("maturity", d.rejected[0].reason)
        # priority (cùng maturity)
        d = arb.arbitrate([C("a", priority=10, **base), C("b", priority=70, **base)])
        self.assertEqual(d.chosen.adapter_id, "b")
        self.assertIn("priority", d.rejected[0].reason)
        # đường dẫn ngắn hơn
        d = arb.arbitrate([C("a", path="deep/dir/x.json", **base), C("b", path="x.json", **base)])
        self.assertEqual(d.chosen.path, "x.json")
        self.assertIn("đường dẫn", d.rejected[0].reason)
        # adapter_id alphabet
        d = arb.arbitrate([C("zeta", **base), C("alpha", **base)])
        self.assertEqual(d.chosen.adapter_id, "alpha")

    def test_tiebreak_independent_of_input_order(self):
        base = dict(FULL)
        cs = [C(n, **base) for n in ("m", "b", "q", "a")]
        first = arb.arbitrate(cs).chosen.adapter_id
        for _ in range(6):
            cs = cs[1:] + cs[:1]
            self.assertEqual(arb.arbitrate(cs).chosen.adapter_id, first)

    def test_plugin_cannot_force_score(self):
        """Plugin tự khai score=1.0 mà thiếu tín hiệu host -> chỉ đóng góp schema_fit (0.18)."""
        from sdk.types import Candidate
        rec = loader.load_plugin_class(type("F", (Plugin,), {"MANIFEST": manifest_src(id="forcer_p")}))
        info = match_info(rec, "unrelated.dat")
        cand = Candidate(path="unrelated.dat", adapter_id="forcer_p", score=1.0)
        c = arb.contender_from_candidate(rec.manifest, cand, info)
        d = arb.arbitrate([c])
        self.assertEqual(d.status, "none")
        self.assertAlmostEqual(d.nearest[0].score, 0.18)

    def test_conflict_margin_downgrades_to_ask(self):
        cs = [C("a", path="a.json", **dict(FULL, kb_agreement=0.0, content_lang_fit=0.0)),
              C("b", path="b.json", **dict(FULL, kb_agreement=0.0, content_lang_fit=0.0, locale_evidence=0.8))]
        self.assertEqual(arb.arbitrate(cs).status, "auto")                    # đúng đặc tả: 0.83 vs 0.81 -> tự chọn
        d = arb.arbitrate(cs, conflict_margin=0.05)
        self.assertEqual((d.status, d.code), ("ask", "E_AMBIGUOUS"))
        self.assertIn("xung đột", d.reason_vi)
        self.assertEqual(len(d.options), 2)

    def test_duplicate_contenders_deduped(self):
        d = arb.arbitrate([C("a", ext_match=1.0), C("a", **FULL)])
        self.assertEqual(len(d.options), 1)
        self.assertEqual(d.chosen.score, 1.0)

    def test_bad_thresholds_rejected(self):
        with self.assertRaises(PB3Error):
            arb.arbitrate([C("a", **FULL)], auto_select=0.5, ask_min=0.9)

    def test_end_to_end_registry_to_arbitration(self):
        """magic tin cậy + đuôi + tên + schema tốt -> tự chọn; chỉ đuôi -> không đủ."""
        from sdk.types import Candidate
        rec = loader.load_plugin_class(type("J", (Plugin,), {"MANIFEST": manifest_src(
            id="e2e_json", match={"extensions": [".json"], "magic": [{"offset": 0, "hex": "7B226B22"}],
                                  "name_regex": "(?i)lang"})}))
        strong = match_info(rec, "lang_en.json", b'{"k":1}')
        weak = match_info(rec, "other.json", b"[1,2]")
        c1 = arb.contender_from_candidate(rec.manifest, Candidate("lang_en.json", "e2e_json", 0.9), strong,
                                          locale_evidence=1.0, manifest_ref=1.0)
        c2 = arb.contender_from_candidate(rec.manifest, Candidate("other.json", "e2e_json", 0.3), weak)
        self.assertEqual(arb.arbitrate([c1]).status, "auto")
        self.assertEqual(arb.arbitrate([c2]).status, "none")


class TestArchitectureRules(unittest.TestCase):
    def test_core_does_not_import_registry(self):
        from tests.helpers import ROOT, imports_of
        for d in ("core", "sdk"):
            for p in (ROOT / d).glob("*.py"):
                for nm, _ in imports_of(p):
                    self.assertNotEqual(nm.split(".")[0], "registry", "%s import registry" % p.name)


if __name__ == "__main__":
    unittest.main()


class TestPluginManagerService(unittest.TestCase):
    def test_format_rows_empty_and_filled(self):
        from services import plugin_manager as pm
        self.assertIn("Chưa có plugin", pm.format_rows([]))
        txt = pm.format_rows([{"id": "a_p", "kind": "format", "engines": ["generic"], "maturity": "M2",
                               "version": "1.0.0", "scope": "builtin", "state": "unavailable",
                               "reason": "thiếu thư viện: lz4"}])
        self.assertIn("a_p", txt)
        self.assertIn("[unavailable]", txt)
        self.assertIn("lz4", txt)

    def test_cli_list_and_unimplemented(self):
        """`plugin list` chạy; `plugin test <id lạ>` -> E_INPUT_NOT_FOUND (rc 1, đợt 2 đã nối); `support` đã triển khai ở Claude 09 (rc 0, in ma trận)."""
        import argparse
        import contextlib
        import io
        from services import plugin_manager as pm

        class FakeApp:
            class paths:
                root = None
            config = None
        from tests.helpers import ROOT
        FakeApp.paths.root = ROOT
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pm.run_cli(FakeApp, argparse.Namespace(command="plugin", action="list", args=[]))
            rc2 = pm.run_cli(FakeApp, argparse.Namespace(command="plugin", action="test", args=["x"]))
            rc3 = pm.run_cli(FakeApp, argparse.Namespace(command="support"))
        self.assertEqual(rc, 0)
        self.assertIn("zip", out.getvalue())       # Claude 04: plugin builtin đầu tiên
        self.assertIn("[ok]", out.getvalue())
        self.assertEqual(rc2, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err.getvalue())
        self.assertEqual(rc3, 0)
        self.assertIn("Mức hỗ trợ chỉ đúng với những gì đã kiểm thử", out.getvalue())
