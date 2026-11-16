"""Test Claude 03 đợt 2, nhiệm vụ B: conformance kit (sdk/testing.py + registry/conformance.py),
skeleton (registry/template.py) và lệnh `plugin new/test` (services/plugin_manager.py).

Yêu cầu B4: skeleton sinh ra phải PASS conformance ngay; plugin cố ý hỏng phải FAIL đúng mục.
Guard là best effort (registry/isolation.py): test chỉ kiểm hành vi được hứa.
"""
import argparse
import ast
import contextlib
import copy
import glob
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.errors import PB3Error
from registry import template
from registry.conformance import run_conformance
from registry.loader import load_plugin_class
from registry.registry import build_registry
from sdk import testing
from sdk.api import Plugin
from sdk.example_kv import KvPlugin
from sdk.testing import FAIL, PASS, SKIP, load_fixture, run_checks
from sdk.types import Candidate, Evidence, LocEntry, LocTable, SourceRef
from tests.helpers import ROOT, TmpCase, can_symlink, copy_project, slow_subprocess, subprocess_timeout

BASE = {
    "id": "conf_demo", "name": "Conf", "version": "1.0.0", "api_version": 3, "kind": "format",
    "capabilities": ["read"], "maturity": "M1", "priority": 40, "true_cost": "cheap",
    "engines": ["generic"], "match": {"extensions": [".txt"]},
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 5},
    "entry": "plugin:P", "selftest": True, "docs": "mẫu",
}
FILES = {"a.txt": b"k=v\n"}


def _detect(self, ctx):
    return [Evidence("extension", ".txt", 0.2, "mẫu")]


def _cands(self, ctx):
    return [Candidate(p, "conf_demo", 0.5) for p in ctx.vfs.iter_entries() if p.endswith(".txt")]


def _probe(self, ctx, cand):
    return {"n": 1}


def _parse(self, ctx, cand):
    src = SourceRef([cand.path], cand.path)
    return LocTable([LocEntry("k", "v", None, "native", "ok", src)], None, "conf_demo")


def _selftest(self, ctx):
    return {"ok": True}


def make(manifest=None, **hooks):
    """Plugin mẫu TỐT; ghi đè hook/manifest để làm hỏng đúng một chỗ."""
    mf = copy.deepcopy(BASE)
    mf.update(manifest or {})
    ns = {"MANIFEST": mf, "detect": _detect, "list_candidates": _cands, "probe": _probe, "parse": _parse,
          "selftest": _selftest}
    ns.update(hooks)
    return type("P", (Plugin,), ns)()


def conf(plugin, files=FILES, **kw):
    kw.setdefault("hook_timeout", 2.0)
    return run_conformance(plugin, files=files, **kw)


def status(rep, name):
    return rep.get(name).status


class TestGoodPlugin(unittest.TestCase):
    def test_good_plugin_passes_every_checked_item(self):
        rep = conf(make())
        self.assertTrue(rep.ok, rep.format())
        for n in ("manifest", "readonly", "detect", "candidates", "probe", "parse", "selftest", "guard"):
            self.assertEqual(status(rep, n), PASS, rep.format())
        for n in ("write", "container", "hints"):
            self.assertEqual(status(rep, n), SKIP)
        self.assertEqual(rep.fixture_files, 1)

    def test_accepts_instance_class_and_record(self):
        inst = make()
        self.assertTrue(conf(inst).ok)
        self.assertTrue(conf(type(inst)).ok)
        rec = load_plugin_class(type(inst))
        self.assertEqual(rec.state, "ok")
        self.assertTrue(conf(rec).ok)
        with self.assertRaises(PB3Error) as cm:
            run_conformance("khong_phai_plugin")
        self.assertEqual(cm.exception.code, "E_PLUGIN_INVALID")

    def test_report_format_and_dict(self):
        rep = conf(make())
        txt = rep.format()
        self.assertIn("[PASS] manifest", txt)
        self.assertIn("KẾT QUẢ: PASS", txt)
        json.dumps(rep.to_dict())
        self.assertEqual(rep.to_dict()["verdict"], PASS)
        self.assertEqual(rep.counts()[FAIL], 0)

    def test_empty_fixture_is_reported_not_hidden(self):
        rep = conf(make(), files={})
        self.assertTrue(rep.ok)
        self.assertEqual(status(rep, "parse"), SKIP)
        self.assertEqual(status(rep, "probe"), SKIP)
        self.assertTrue(any("fixture rỗng" in n for n in rep.notes))
        self.assertIn("không phải sandbox", " ".join(rep.notes))

    def test_empty_fixture_verdict_label_says_data_not_tested(self):
        rep = conf(make(), files={})
        self.assertEqual(rep.verdict, PASS)                      # mã máy đọc (exit code) giữ nguyên
        self.assertFalse(rep.data_tested)
        self.assertEqual(rep.verdict_label, "PASS (CHƯA KIỂM DỮ LIỆU)")
        self.assertIn("KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU)", rep.format())
        self.assertIs(rep.to_dict()["data_tested"], False)

    def test_real_fixture_verdict_label_is_plain_pass(self):
        rep = conf(make())
        self.assertEqual(status(rep, "parse"), PASS)
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.verdict_label, "PASS")
        self.assertNotIn("CHƯA KIỂM DỮ LIỆU", rep.format())

    def test_fail_label_unchanged(self):
        rep = conf(make(parse=lambda self, ctx, c: 5))
        self.assertEqual(rep.verdict_label, FAIL)
        self.assertFalse(rep.ok)

    def test_example_kv_write_path_passes(self):
        files = {"m.kv": b"# c\nx=1\ny=a\\nb\n"}
        rep = conf(KvPlugin(), files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertEqual(status(rep, "write"), PASS)
        self.assertEqual(status(rep, "readonly"), SKIP)

    def test_temp_dir_is_cleaned(self):
        pat = os.path.join(tempfile.gettempdir(), "pb3conf-*")
        before = set(glob.glob(pat))
        conf(make())
        self.assertEqual(set(glob.glob(pat)) - before, set())

    def test_hook_may_write_inside_tempdir(self):
        def parse(self, ctx, cand):
            with open(os.path.join(ctx.tempdir, "scratch.bin"), "wb") as fh:
                fh.write(b"x")
            return _parse(self, ctx, cand)
        rep = conf(make(parse=parse))
        self.assertEqual(status(rep, "parse"), PASS, rep.format())
        self.assertEqual(status(rep, "guard"), PASS)

    def test_run_checks_requires_a_runner(self):
        with self.assertRaises(PB3Error):
            run_checks(make(), None, files=FILES)


class TestBrokenPluginsFailTheRightItem(unittest.TestCase):
    def assert_fails(self, rep, *names):
        self.assertFalse(rep.ok, rep.format())
        self.assertEqual(sorted(rep.failed()), sorted(names), rep.format())

    def test_selftest_none_is_fail_not_pass(self):
        rep = conf(make(selftest=lambda self, ctx: {"ok": None, "skipped": True, "reason": "chưa cài"}))
        self.assert_fails(rep, "selftest")
        self.assertIn("ok=None", rep.get("selftest").reason)

    def test_selftest_declared_but_default_hook_fails(self):
        p = make()
        p.selftest = lambda ctx: Plugin.selftest(p, ctx)           # hành vi mặc định của SDK: ok=None
        self.assert_fails(conf(p), "selftest")

    def test_selftest_false_in_manifest_is_skip(self):
        rep = conf(make(manifest={"selftest": False}, selftest=lambda self, ctx: {"ok": False}))
        self.assertEqual(status(rep, "selftest"), SKIP)
        self.assertTrue(rep.ok)

    def test_selftest_false_result_fails(self):
        self.assert_fails(conf(make(selftest=lambda self, ctx: {"ok": False})), "selftest")

    def test_detect_crash_is_contained(self):
        def boom(self, ctx):
            raise RuntimeError("nội dung nhạy cảm không được lộ")
        rep = conf(make(detect=boom))
        self.assert_fails(rep, "detect")
        self.assertEqual(status(rep, "parse"), PASS)                # các mục khác vẫn chạy
        self.assertNotIn("nhạy cảm", rep.get("detect").reason)

    def test_detect_wrong_type(self):
        self.assert_fails(conf(make(detect=lambda self, ctx: ["không phải Evidence"])), "detect")
        self.assert_fails(conf(make(detect=lambda self, ctx: None)), "detect")

    def test_candidates_pointing_outside_vfs(self):
        rep = conf(make(list_candidates=lambda self, ctx: [Candidate("ma.txt", "conf_demo", 0.5)]))
        self.assertEqual(status(rep, "candidates"), FAIL)
        self.assertIn("không có trong VFS", rep.get("candidates").reason)
        self.assertEqual(status(rep, "parse"), SKIP)                # ứng viên sai không được đưa tiếp

    def test_candidate_wrong_adapter_id(self):
        rep = conf(make(list_candidates=lambda self, ctx: [Candidate("a.txt", "plugin_khac", 0.5)]))
        self.assertEqual(status(rep, "candidates"), FAIL)
        self.assertIn("adapter_id", rep.get("candidates").reason)

    def test_probe_not_dict_and_crash(self):
        self.assert_fails(conf(make(probe=lambda self, ctx, cand: [1])), "probe")

        def boom(self, ctx, cand):
            raise ValueError("x")
        self.assert_fails(conf(make(probe=boom)), "probe")

    def test_parse_wrong_type(self):
        self.assert_fails(conf(make(parse=lambda self, ctx, cand: {"k": "v"})), "parse")

    def test_parse_wrong_adapter_id(self):
        def parse(self, ctx, cand):
            t = _parse(self, ctx, cand)
            t.adapter_id = "plugin_khac"
            return t
        rep = conf(make(parse=parse))
        self.assert_fails(rep, "parse")
        self.assertIn("adapter_id", rep.get("parse").reason)

    def test_parse_table_that_does_not_round_trip(self):
        def parse(self, ctx, cand):
            t = _parse(self, ctx, cand)
            t.entries[0].value = 123                                 # sai kiểu sau khi dựng
            return t
        self.assert_fails(conf(make(parse=parse)), "parse")

    def test_readonly_plugin_must_refuse_build(self):
        rep = conf(make(build=lambda self, ctx, table, base: b"fake-write"))
        self.assert_fails(rep, "readonly")
        self.assertIn("build", rep.get("readonly").reason)

    def test_write_plugin_verify_unchecked_is_fail(self):
        mf = {"capabilities": ["read", "write"], "write_modes": ["REBUILD_SECTION"]}
        rep = conf(make(mf, build=lambda self, ctx, table, base: base))      # verify mặc định: ok=None
        self.assert_fails(rep, "write")
        self.assertIn("ok=None", rep.get("write").reason)

    def test_write_plugin_build_crash_and_bad_verify(self):
        mf = {"capabilities": ["read", "write"], "write_modes": ["REBUILD_SECTION"]}

        def boom(self, ctx, table, base):
            raise OSError("x")
        self.assert_fails(conf(make(mf, build=boom)), "write")
        self.assert_fails(conf(make(mf, build=lambda s, c, t, b: "không phải bytes")), "write")
        self.assert_fails(conf(make(mf, build=lambda s, c, t, b: b,
                                    verify=lambda s, c, a, b: {"ok": False})), "write")
        ok = conf(make(mf, build=lambda s, c, t, b: b,
                      verify=lambda s, c, a, b: {"ok": b == a}))   # verify thật: chỉ ok khi dữ liệu nguyên vẹn
        self.assertTrue(ok.ok, ok.format())
        self.assertEqual(status(ok, "write"), PASS)

    def test_write_plugin_verify_accepts_tampered_is_fail(self):
        # C8: verify chấp nhận dữ liệu đã lật 1 byte thì write FAIL (kiểm âm).
        mf = {"capabilities": ["read", "write"], "write_modes": ["REBUILD_SECTION"]}
        rep = conf(make(mf, build=lambda s, c, t, b: b,
                        verify=lambda s, c, a, b: {"ok": True}))
        self.assert_fails(rep, "write")
        self.assertIn("lật 1 byte", rep.get("write").reason)

    def test_hint_plugin_hints_must_be_dict(self):
        mf = {"kind": "engine", "capabilities": ["hint"], "selftest": False}
        good = conf(make(mf, hints=lambda self, ctx: {"a": 1}), files={})
        self.assertTrue(good.ok, good.format())
        self.assertEqual(status(good, "hints"), PASS)
        self.assertEqual(status(good, "candidates"), SKIP)
        self.assert_fails(conf(make(mf, hints=lambda self, ctx: [1]), files={}), "hints")

    def test_container_plugin_must_return_vfs(self):
        from sdk.vfs import MemoryVFS
        mf = {"kind": "container", "capabilities": ["container"], "selftest": False}
        good = conf(make(mf, open_container=lambda self, ctx, path: MemoryVFS({"x.txt": b"1"})))
        self.assertTrue(good.ok, good.format())
        self.assertEqual(status(good, "container"), PASS)
        bad = conf(make(mf, open_container=lambda self, ctx, path: {"x.txt": b"1"}))
        self.assert_fails(bad, "container")

    def test_unloadable_plugin_fails_manifest_and_skips_rest(self):
        class Bad(Plugin):
            MANIFEST = dict(BASE, api_version=99)
        rep = conf(Bad)
        self.assertEqual(status(rep, "manifest"), FAIL)
        self.assertEqual([c.status for c in rep.checks[1:]], [SKIP] * (len(rep.checks) - 1))
        self.assertFalse(rep.ok)

    def test_oversized_fixture_is_reported(self):
        old = testing.MAX_FIXTURE_FILES
        testing.MAX_FIXTURE_FILES = 1
        try:
            td = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, td, ignore_errors=True)
            for n in ("a.txt", "b.txt"):
                Path(td, n).write_text("k=v\n")
            with self.assertRaises(PB3Error) as cm:
                load_fixture(td)
            self.assertEqual(cm.exception.code, "E_TOO_LARGE")
            rep = run_conformance(make(), fixture=td)
            self.assertEqual(status(rep, "manifest"), FAIL)
            self.assertIn("fixture", rep.get("manifest").reason)
        finally:
            testing.MAX_FIXTURE_FILES = old


class TestGuardInsideConformance(unittest.TestCase):
    def test_socket_in_detect_fails_guard(self):
        def detect(self, ctx):
            socket.socket()
            return []
        rep = conf(make(detect=detect))
        self.assertEqual(status(rep, "guard"), FAIL, rep.format())
        self.assertIn("E_PLUGIN_FORBIDDEN", rep.get("guard").reason)
        self.assertEqual(status(rep, "detect"), FAIL)

    def test_swallowed_violation_still_fails(self):
        def detect(self, ctx):
            try:
                socket.socket()
            except Exception:
                pass
            return []
        self.assertEqual(status(conf(make(detect=detect)), "guard"), FAIL)

    def test_subprocess_fails_guard(self):
        def probe(self, ctx, cand):
            subprocess.Popen([sys.executable, "-c", "pass"])
            return {}
        rep = conf(make(probe=probe))
        self.assertEqual(status(rep, "guard"), FAIL)
        self.assertEqual(status(rep, "probe"), FAIL)

    def test_write_outside_tempdir_fails_guard(self):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        out = os.path.join(td, "x.bin")

        def parse(self, ctx, cand):
            with open(out, "wb") as fh:
                fh.write(b"x")
            return _parse(self, ctx, cand)
        rep = conf(make(parse=parse))
        self.assertEqual(status(rep, "guard"), FAIL, rep.format())
        self.assertFalse(os.path.exists(out))

    def test_hang_fails_guard_with_timeout_and_pipeline_continues(self):
        def probe(self, ctx, cand):
            while True:
                time.sleep(0.02)
                ctx.check_cancel()
        t0 = time.monotonic()
        rep = conf(make(probe=probe), hook_timeout=0.3)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual(status(rep, "guard"), FAIL)
        self.assertIn("E_PLUGIN_TIMEOUT", rep.get("guard").reason)
        self.assertEqual(status(rep, "selftest"), PASS)             # hook sau vẫn chạy


class TestTemplate(TmpCase):
    def test_id_validation(self):
        for bad in ("", "A", "a", "1abc", "my-game", "My_Game", "x" * 42, "../x", "a b", None, 5):
            with self.assertRaises(PB3Error, msg=repr(bad)) as cm:
                template.validate_id(bad)
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        for good in ("ab", "mygame_csv", "a1", "x" * 41):
            self.assertEqual(template.validate_id(good), good)

    def test_class_name(self):
        self.assertEqual(template.class_name("mygame_csv"), "MygameCsvPlugin")
        self.assertEqual(template.class_name("a__b"), "ABPlugin")

    def test_profile_and_unknown_kind(self):
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "profile", "pr_x")
        self.assertEqual(cm.exception.code, "E_NOT_IMPLEMENTED")
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "weird", "pr_x")
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        self.assertFalse((self.tmp / "plugins_user").exists())      # không để lại gì

    def test_rendered_source_is_python38_and_static_manifest_ok(self):
        from registry.loader import static_manifest
        from sdk.manifest import parse_manifest
        for kind in template.TEMPLATED_KINDS:
            src = template.render(kind, "demo_" + kind)
            ast.parse(src, feature_version=(3, 8))
            m = parse_manifest(static_manifest(src))
            self.assertEqual((m.id, m.kind, m.maturity), ("demo_" + kind, kind, "M1"))
            self.assertNotRegex(src, r"@[A-Z]+@")

    def test_generate_creates_files_and_refuses_overwrite(self):
        r = template.generate(str(self.tmp), "format", "mygame_csv")
        self.assertEqual(r["plugin_dir"], "plugins_user/mygame_csv")
        self.assertEqual(r["fixture_dir"], "tests/fixtures/mygame_csv")
        self.assertTrue((self.tmp / "plugins_user/mygame_csv/plugin.py").is_file())
        self.assertTrue((self.tmp / "tests/fixtures/mygame_csv/.gitkeep").is_file())
        before = (self.tmp / "plugins_user/mygame_csv/plugin.py").read_bytes()
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "format", "mygame_csv")
        self.assertEqual(cm.exception.code, "E_OUTPUT_EXISTS")
        self.assertEqual((self.tmp / "plugins_user/mygame_csv/plugin.py").read_bytes(), before)

    def test_existing_fixture_dir_blocks_and_leaves_no_plugin_dir(self):
        (self.tmp / "tests/fixtures/zz_demo").mkdir(parents=True)
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "format", "zz_demo")
        self.assertEqual(cm.exception.code, "E_OUTPUT_EXISTS")
        self.assertFalse((self.tmp / "plugins_user/zz_demo").exists())

    def test_builtin_id_collision_refused(self):
        (self.tmp / "plugins/taken_id").mkdir(parents=True)
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "format", "taken_id")
        self.assertEqual(cm.exception.code, "E_OUTPUT_EXISTS")
        self.assertFalse((self.tmp / "plugins_user/taken_id").exists())

    def test_failed_self_check_cleans_up(self):
        old = template._self_check

        def boom(target, pid):
            raise PB3Error("E_INTERNAL", "giả lập")
        template._self_check = boom
        try:
            with self.assertRaises(PB3Error):
                template.generate(str(self.tmp), "format", "zz_clean")
        finally:
            template._self_check = old
        self.assertFalse((self.tmp / "plugins_user/zz_clean").exists())
        self.assertFalse((self.tmp / "tests/fixtures/zz_clean").exists())

    def test_symlinked_plugins_user_cannot_escape(self):
        if not can_symlink(self.tmp):
            self.skipTest("không tạo được symlink ở đây")
        outside = self.tmp / "outside"
        outside.mkdir()
        proj = self.tmp / "proj"
        proj.mkdir()
        os.symlink(str(outside), str(proj / "plugins_user"))
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(proj), "format", "esc_demo")
        self.assertEqual(cm.exception.code, "E_UNSAFE_PATH")
        self.assertEqual(list(outside.iterdir()), [])


class TestSkeletonPassesConformance(TmpCase):
    """B4: skeleton sinh ra phải PASS conformance ngay (qua loader thật, như `plugin test`)."""

    def conformance_of(self, kind):
        pid = "demo_" + kind
        template.generate(str(self.tmp), kind, pid)
        reg = build_registry(str(self.tmp))
        rec = reg.get(pid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, "ok", rec.reason)
        return run_conformance(rec, fixture=str(self.tmp / "tests" / "fixtures" / pid), hook_timeout=3.0)

    def test_every_templated_kind_passes_with_empty_fixture(self):
        for kind in template.TEMPLATED_KINDS:
            rep = self.conformance_of(kind)
            self.assertTrue(rep.ok, rep.format())
            self.assertEqual(status(rep, "manifest"), PASS)
            self.assertEqual(status(rep, "guard"), PASS)
            self.assertEqual(rep.fixture_files, 0)

    def test_write_skeleton_passes_write_check(self):
        """X15: `plugin new format <id> --write` sinh skeleton ghi được, nhiều file,
        và mục write của conformance PASS thật (không SKIP) trên fixture có dữ liệu."""
        pid = "demo_write"
        r = template.generate(str(self.tmp), "format", pid, write=True)
        self.assertIn("plugins_user/demo_write/kv.py", r["files"])
        self.assertIn("plugins_user/demo_write/plugin.py", r["files"])
        reg = build_registry(str(self.tmp))
        rec = reg.get(pid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.state, "ok", rec.reason)
        rep = run_conformance(rec, files={"sample.txt": b"a=1\nb=2\n"}, hook_timeout=3.0)
        self.assertTrue(rep.ok, rep.format())
        w = rep.get("write")
        self.assertIsNotNone(w)
        self.assertEqual(w.status, "PASS", rep.format())
        self.assertEqual(rep.get("parse").status, "PASS", rep.format())

    def test_write_flag_only_for_kv_kinds(self):
        with self.assertRaises(PB3Error) as cm:
            template.render("container", "demo_x", write=True)
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        with self.assertRaises(PB3Error) as cm:
            template.generate(str(self.tmp), "engine", "demo_y", write=True)
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        self.assertFalse((self.tmp / "plugins_user/demo_y").exists())

    def test_format_skeleton_selftest_really_runs(self):
        rep = self.conformance_of("format")
        self.assertEqual(status(rep, "selftest"), PASS)
        self.assertEqual(status(rep, "parse"), SKIP)                # fixture rỗng: nói thật là chưa kiểm
        self.assertTrue(any("fixture rỗng" in n for n in rep.notes))

    def test_container_and_engine_skeletons_do_not_pretend(self):
        self.assertEqual(status(self.conformance_of("container"), "selftest"), SKIP)
        eng = self.conformance_of("engine")
        self.assertEqual(status(eng, "hints"), PASS)
        self.assertEqual(status(eng, "selftest"), SKIP)

    def test_format_skeleton_parses_a_real_fixture_file(self):
        self.conformance_of("format")
        (self.tmp / "tests/fixtures/demo_format/sample.txt").write_text("a=1\n# c\nb=2\na=3\n", encoding="utf-8")
        reg = build_registry(str(self.tmp))
        rep = run_conformance(reg.get("demo_format"), fixture=str(self.tmp / "tests/fixtures/demo_format"))
        self.assertTrue(rep.ok, rep.format())
        for n in ("candidates", "probe", "parse"):
            self.assertEqual(status(rep, n), PASS, rep.format())
        self.assertIn("2 entry", rep.get("parse").reason)           # a trùng -> conflicts, không gộp

    def test_container_skeleton_fails_once_a_candidate_exists_but_not_written(self):
        """Container chưa viết open_container: có ứng viên mà vẫn E_NOT_IMPLEMENTED thì phải FAIL, không giả PASS."""
        self.conformance_of("container")
        reg = build_registry(str(self.tmp))
        plug = reg.get("demo_container").instance
        plug.list_candidates = lambda ctx: [Candidate("c.bin", "demo_container", 0.5)]
        rep = run_conformance(plug, files={"c.bin": b"\x00\x01"})
        self.assertEqual(status(rep, "container"), FAIL, rep.format())
        self.assertIn("E_NOT_IMPLEMENTED", rep.get("container").reason)

    def test_broken_skeleton_fails_selftest(self):
        self.conformance_of("format")
        p = self.tmp / "plugins_user/demo_format/plugin.py"
        src = p.read_text(encoding="utf-8")
        self.assertIn('== ["a", "b"]', src)
        p.write_text(src.replace('== ["a", "b"]', '== ["a", "b", "c"]'), encoding="utf-8")
        reg = build_registry(str(self.tmp))
        rep = run_conformance(reg.get("demo_format"), fixture=str(self.tmp / "tests/fixtures/demo_format"))
        self.assertEqual(rep.failed(), ["selftest"], rep.format())


class FakeApp:
    config = None

    def __init__(self, root):
        self.paths = SimpleNamespace(root=Path(root))


def run_plugin_cmd(app, action, *args):
    from services import plugin_manager as pm
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = pm.run_cli(app, argparse.Namespace(command="plugin", action=action, args=list(args)))
    return rc, out.getvalue(), err.getvalue()


class TestPluginCommands(TmpCase):
    def setUp(self):
        super().setUp()
        self.app = FakeApp(self.tmp)

    def test_new_then_test_then_list(self):
        rc, out, err = run_plugin_cmd(self.app, "new", "format", "mygame_csv")
        self.assertEqual(rc, 0, err)
        self.assertIn("plugins_user/mygame_csv/plugin.py", out)
        self.assertIn("plugin test mygame_csv", out)
        rc, out, err = run_plugin_cmd(self.app, "test", "mygame_csv")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("KẾT QUẢ: PASS", out)
        rc, out, _ = run_plugin_cmd(self.app, "list")
        self.assertIn("mygame_csv", out)
        self.assertIn("[ok]", out)

    def test_test_returns_nonzero_and_names_the_failed_item(self):
        run_plugin_cmd(self.app, "new", "format", "mygame_csv")
        p = self.tmp / "plugins_user/mygame_csv/plugin.py"
        p.write_text(p.read_text(encoding="utf-8").replace('== ["a", "b"]', '== ["zzz"]'), encoding="utf-8")
        rc, out, err = run_plugin_cmd(self.app, "test", "mygame_csv")
        self.assertEqual(rc, PB3Error("E_PLUGIN_INVALID").exit_code)
        self.assertIn("[FAIL] selftest", out)
        self.assertIn("KẾT QUẢ: FAIL", out)

    def test_test_unloadable_plugin(self):
        run_plugin_cmd(self.app, "new", "format", "mygame_csv")
        p = self.tmp / "plugins_user/mygame_csv/plugin.py"
        p.write_text(p.read_text(encoding="utf-8").replace('"api_version": 3', '"api_version": 99'), encoding="utf-8")
        rc, out, _ = run_plugin_cmd(self.app, "test", "mygame_csv")
        self.assertNotEqual(rc, 0)
        self.assertIn("[FAIL] manifest", out)

    def test_usage_and_unknown_errors_are_user_errors(self):
        for action, args, code in (("new", [], "E_CONFIG_INVALID"), ("new", ["format"], "E_CONFIG_INVALID"),
                                   ("new", ["format", "Bad-Id"], "E_CONFIG_INVALID"),
                                   ("new", ["weird", "ok_id"], "E_CONFIG_INVALID"),
                                   ("new", ["profile", "ok_id"], "E_NOT_IMPLEMENTED"),
                                   ("test", [], "E_CONFIG_INVALID"), ("test", ["a", "b"], "E_CONFIG_INVALID"),
                                   ("test", ["khong_co"], "E_INPUT_NOT_FOUND")):
            rc, out, err = run_plugin_cmd(self.app, action, *args)
            self.assertIn(code, err, (action, args))
            self.assertNotEqual(rc, 0)
        self.assertEqual(run_plugin_cmd(self.app, "new", "weird", "ok_id")[0], 1)
        self.assertEqual(run_plugin_cmd(self.app, "test", "khong_co")[0], 1)
        self.assertFalse((self.tmp / "plugins_user").exists())

    def test_second_new_does_not_overwrite(self):
        run_plugin_cmd(self.app, "new", "format", "mygame_csv")
        p = self.tmp / "plugins_user/mygame_csv/plugin.py"
        p.write_text("# đã sửa bởi người dùng\n", encoding="utf-8")
        rc, _, err = run_plugin_cmd(self.app, "new", "format", "mygame_csv")
        self.assertEqual(rc, 1)
        self.assertIn("E_OUTPUT_EXISTS", err)
        self.assertEqual(p.read_text(encoding="utf-8"), "# đã sửa bởi người dùng\n")

    def test_support_is_implemented(self):
        # Claude 09: `support` nay đã triển khai (in ma trận hỗ trợ), không còn E_NOT_IMPLEMENTED.
        from services import plugin_manager as pm
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pm.run_cli(self.app, argparse.Namespace(command="support"))
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("Mức hỗ trợ chỉ đúng với những gì đã kiểm thử", out.getvalue())


@slow_subprocess
class TestCliEndToEnd(TmpCase):
    """Một lần chạy thật qua `python3 menu.py` trên bản sao dự án (không đụng cây gốc)."""

    def test_new_and_test_via_menu(self):
        proj = copy_project(self.tmp)
        env = dict(os.environ, PB3_PROGRESS="0")

        def run(*argv):
            return subprocess.run([sys.executable, "menu.py"] + list(argv), cwd=str(proj), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=subprocess_timeout(60))
        r = run("plugin", "new", "format", "demo_x")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8"))
        r = run("plugin", "test", "demo_x")
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8") + r.stderr.decode("utf-8"))
        self.assertIn("KẾT QUẢ: PASS", r.stdout.decode("utf-8"))
        r = run("plugin", "new", "format", "demo_x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("E_OUTPUT_EXISTS", r.stderr.decode("utf-8"))

    def test_new_write_via_menu(self):
        """X15: `plugin new format <id> --write` qua menu.py thật tạo skeleton nhiều file."""
        proj = copy_project(self.tmp)
        env = dict(os.environ, PB3_PROGRESS="0")

        def run(*argv):
            return subprocess.run([sys.executable, "menu.py"] + list(argv), cwd=str(proj), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=subprocess_timeout(60))
        r = run("plugin", "new", "format", "demo_w", "--write")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8"))
        self.assertTrue((proj / "plugins_user/demo_w/kv.py").is_file())
        self.assertIn("có ghi", r.stdout.decode("utf-8"))
        r = run("plugin", "test", "demo_w")
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8") + r.stderr.decode("utf-8"))
        self.assertIn("KẾT QUẢ: PASS", r.stdout.decode("utf-8"))


class TestLayering(unittest.TestCase):
    def test_sdk_testing_does_not_import_registry_or_unsafe_modules(self):
        tree = ast.parse((ROOT / "sdk" / "testing.py").read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for nm in names:
                self.assertNotIn(nm.split(".")[0], ("registry", "subprocess", "socket"), nm)


if __name__ == "__main__":
    unittest.main()
