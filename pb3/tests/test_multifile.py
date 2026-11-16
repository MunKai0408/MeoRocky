"""Plugin nhiều file (C1, decisions #56): package riêng, import tương đối, quét cả cây, không dùng bytecode đệm."""
import copy
import os
import py_compile
import sys
import textwrap
import unittest

from importlib.util import cache_from_source

from registry import loader, pluginpkg
from tests.helpers import TmpCase, can_symlink
from tests.test_isolation import BASE

PLUGIN_SRC = textwrap.dedent('''\
    from sdk.api import Plugin
    from sdk.types import LocTable
    %(imports)s
    MANIFEST = %(manifest)r
    class P(Plugin):
        MANIFEST = MANIFEST
        VALUE = %(value)s
        def parse(self, ctx, cand): return LocTable([], None, %(pid)r)
        def build(self, ctx, t, b): return b
''')


class MultiCase(TmpCase):
    def plugin(self, name, files=None, scope="user", pid=None, imports="", value="None", entry="plugin:P"):
        """Tạo plugins_<scope>/<name>/ với plugin.py + các file phụ {tên_tương_đối: nội dung}."""
        pid = pid or name.replace("-", "_")
        root = self.tmp / ("plugins_" + scope)
        d = root / name
        os.makedirs(str(d), exist_ok=True)
        m = copy.deepcopy(BASE)
        m.update(id=pid, entry=entry)
        (d / "plugin.py").write_text(PLUGIN_SRC % dict(imports=imports, manifest=m, value=value, pid=pid), encoding="utf-8")
        for rel, body in (files or {}).items():
            f = d / rel
            os.makedirs(str(f.parent), exist_ok=True)
            f.write_text(body, encoding="utf-8")
        return d

    def load(self, scope="user", **kw):
        return loader.discover([(scope, str(self.tmp / ("plugins_" + scope)))], **kw)

    def rec(self, pid, scope="user", **kw):
        r = self.load(scope, **kw).by_id(pid)
        self.assertEqual(len(r), 1, [x.plugin_id for x in self.load(scope, **kw).records])
        return r[0]


class TestRelativeImports(MultiCase):
    def test_sibling_file_relative_import(self):
        self.plugin("mf1", {"helper.py": "X = 41\n"}, imports="from .helper import X", value="X + 1")
        r = self.rec("mf1")
        self.assertEqual((r.state, r.code), ("ok", "E_OK"), r.reason)
        self.assertEqual(r.instance.VALUE, 42)

    def test_subpackage(self):
        self.plugin("mf2", {"lib/__init__.py": "", "lib/util.py": "def f(): return 7\n"},
                    imports="from .lib.util import f", value="f()")
        r = self.rec("mf2")
        self.assertEqual(r.state, "ok", r.reason)
        self.assertEqual(r.instance.VALUE, 7)

    def test_entry_inside_subpackage(self):
        self.plugin("mf3", {"sub/__init__.py": "", "sub/main.py": "from . import other\nY = other.Z\n", "sub/other.py": "Z = 5\n"},
                    entry="sub.main:Q")
        # plugin.py gốc vẫn cần để đọc MANIFEST; entry trỏ vào sub/main.py nên viết lớp ở đó
        d = self.tmp / "plugins_user" / "mf3"
        m = copy.deepcopy(BASE)
        m.update(id="mf3", entry="sub.main:Q")
        (d / "sub" / "main.py").write_text(
            "from sdk.api import Plugin\nfrom sdk.types import LocTable\nfrom . import other\n"
            "MANIFEST = %r\nclass Q(Plugin):\n    MANIFEST = MANIFEST\n    VALUE = other.Z\n"
            "    def parse(self, ctx, cand): return LocTable([], None, 'mf3')\n    def build(self, ctx, t, b): return b\n" % m,
            encoding="utf-8")
        r = self.rec("mf3")
        self.assertEqual(r.state, "ok", r.reason)
        self.assertEqual(r.instance.VALUE, 5)

    def test_two_plugins_same_helper_name_do_not_clash(self):
        self.plugin("pa", {"helper.py": "X = 'A'\n"}, imports="from .helper import X", value="X")
        self.plugin("pb", {"helper.py": "X = 'B'\n"}, imports="from .helper import X", value="X")
        rep = self.load()
        self.assertEqual({r.plugin_id: r.instance.VALUE for r in rep.records}, {"pa": "A", "pb": "B"})

    def test_dirnames_that_sanitize_equal_get_distinct_packages(self):
        self.plugin("a-b", {"helper.py": "X = 1\n"}, pid="ab_one", imports="from .helper import X", value="X")
        self.plugin("a_b", {"helper.py": "X = 2\n"}, pid="ab_two", imports="from .helper import X", value="X")
        rep = self.load()
        got = {r.plugin_id: (r.state, r.instance.VALUE if r.instance else None) for r in rep.records}
        self.assertEqual(got, {"ab_one": ("ok", 1), "ab_two": ("ok", 2)})

    def test_absolute_sibling_import_fails_with_hint(self):
        self.plugin("mf4", {"helper.py": "X = 1\n"}, imports="import helper")
        r = self.rec("mf4")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_INVALID"))
        self.assertIn("from .helper import", r.reason)

    def test_missing_module_has_no_hint_but_fails_cleanly(self):
        self.plugin("mf5", {}, imports="from .nope import X")
        r = self.rec("mf5")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_INVALID"))
        self.assertNotIn("import tương đối", r.reason)

    def test_root_init_is_not_executed(self):
        self.plugin("mf6", {"__init__.py": "raise RuntimeError('không được chạy')\n"})
        self.assertEqual(self.rec("mf6").state, "ok")

    def test_sys_path_untouched(self):
        before = list(sys.path)
        self.plugin("mf7", {"helper.py": "X = 1\n"}, imports="from .helper import X")
        self.load()
        self.assertEqual(sys.path, before)

    def test_builtin_scope_multifile_loads_and_is_not_scanned(self):
        self.plugin("bi", {"helper.py": "import ctypes\nX = 1\n"}, scope="builtin", imports="from .helper import X", value="X")
        r = self.rec("bi", scope="builtin")
        self.assertEqual(r.state, "ok", r.reason)


class TestScanWholeTree(MultiCase):
    def test_forbidden_import_in_helper_rejected_before_any_code_runs(self):
        marker = self.tmp / "ran.txt"
        self.plugin("sc1", {"helper.py": "import socket\nopen(%r, 'w').close()\n" % str(marker)}, imports="from .helper import x")
        r = self.rec("sc1")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_FORBIDDEN"))
        self.assertIn("helper.py", r.reason)
        self.assertFalse(marker.exists())

    def test_unimported_file_is_scanned_too(self):
        self.plugin("sc2", {"tools/gen.py": "import subprocess\n"})
        r = self.rec("sc2")
        self.assertEqual(r.code, "E_PLUGIN_FORBIDDEN")
        self.assertIn("tools/gen.py", r.reason)

    def test_entry_file_finding_has_no_filename_prefix(self):
        d = self.plugin("sc3")
        (d / "plugin.py").write_text((d / "plugin.py").read_text(encoding="utf-8") + "\nimport posix\n", encoding="utf-8")
        r = self.rec("sc3")
        self.assertTrue(r.reason.startswith("quét tĩnh từ chối: dòng"), r.reason)

    def test_static_scan_can_be_disabled_for_helper(self):
        self.plugin("sc4", {"helper.py": "import socket\nX = 1\n"}, imports="from .helper import X")
        self.assertEqual(self.rec("sc4", static_scan=False).state, "ok")

    def test_too_many_files(self):
        files = {"f%02d.py" % i: "X = 1\n" for i in range(pluginpkg.MAX_FILES + 1)}
        self.plugin("sc5", files)
        r = self.rec("sc5")
        self.assertEqual((r.state, r.code), ("disabled", "E_TOO_LARGE"))

    def test_too_deep(self):
        self.plugin("sc6", {"a/b/c/d/e/x.py": "X = 1\n"})
        self.assertEqual(self.rec("sc6").code, "E_TOO_LARGE")

    def test_symlink_inside_plugin_rejected(self):
        if not can_symlink(self.tmp):
            self.skipTest("hệ tệp không hỗ trợ symlink")
        d = self.plugin("sc7")
        outside = self.tmp / "outside.py"
        outside.write_text("X = 1\n", encoding="utf-8")
        os.symlink(str(outside), str(d / "linked.py"))
        r = self.rec("sc7")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_FORBIDDEN"))
        self.assertIn("symlink", r.reason)

    def test_non_py_files_ignored_by_scan(self):
        self.plugin("sc8", {"data/notes.txt": "import socket\n", "README.md": "import socket\n"})
        self.assertEqual(self.rec("sc8").state, "ok")


class TestNoBytecodeTricks(MultiCase):
    def test_stale_unchecked_pyc_cannot_replace_scanned_source(self):
        d = self.plugin("bc1", {"helper.py": "X = 1\n"}, imports="from .helper import X", value="X")
        evil = self.tmp / "evil_src.py"
        evil.write_text("X = 999\n", encoding="utf-8")
        cache = cache_from_source(str(d / "helper.py"))
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        py_compile.compile(str(evil), cfile=cache, doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        r = self.rec("bc1")
        self.assertEqual(r.state, "ok", r.reason)
        self.assertEqual(r.instance.VALUE, 1)

    def test_sourceless_pyc_is_not_importable(self):
        d = self.plugin("bc2", {}, imports="from . import ghost")
        src = self.tmp / "ghost_src.py"
        src.write_text("X = 1\n", encoding="utf-8")
        py_compile.compile(str(src), cfile=str(d / "ghost.pyc"), doraise=True)
        r = self.rec("bc2")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_INVALID"))

    def test_loader_writes_no_pycache(self):
        d = self.plugin("bc3", {"helper.py": "X = 1\n"}, imports="from .helper import X")
        self.assertEqual(self.rec("bc3").state, "ok")
        self.assertFalse((d / "__pycache__").exists())


class TestReloadAndCleanup(MultiCase):
    def test_reload_picks_up_changed_helper(self):
        d = self.plugin("rl1", {"helper.py": "X = 1\n"}, imports="from .helper import X", value="X")
        self.assertEqual(self.rec("rl1").instance.VALUE, 1)
        (d / "helper.py").write_text("X = 2\n", encoding="utf-8")
        self.assertEqual(self.rec("rl1").instance.VALUE, 2)

    def test_failed_plugin_leaves_no_modules(self):
        self.plugin("rl2", {"helper.py": "X = 1\n"}, imports="from .helper import X\nraise RuntimeError('x')")
        r = self.rec("rl2")
        self.assertEqual(r.state, "disabled")
        self.assertEqual([k for k in sys.modules if k.startswith(pluginpkg.MODULE_PREFIX + "user_rl2")], [])

    def test_missing_entry_file_still_clear_error(self):
        d = self.plugin("rl3", entry="nope:P")
        r = self.rec("rl3")
        self.assertEqual((r.state, r.code), ("disabled", "E_PLUGIN_INVALID"))


class TestKnownLimit(MultiCase):
    def test_known_limit_guessing_other_plugin_package_name_works(self):
        """Giới hạn khóa lại (decisions #56): tên package plugin khác đoán được; đây không phải sandbox."""
        self.plugin("lim_a", {"helper.py": "X = 'A'\n"}, imports="from .helper import X", value="X")
        pkg = pluginpkg.package_name("user", "lim_a", str(self.tmp / "plugins_user" / "lim_a"))
        self.plugin("lim_b", {}, imports="from %s.helper import X" % pkg, value="X")
        rep = self.load()
        got = {r.plugin_id: r.state for r in rep.records}
        self.assertEqual(got["lim_a"], "ok")
        self.assertIn(got["lim_b"], ("ok", "disabled"))      # phụ thuộc thứ tự nạp; chỉ ghi nhận không crash


if __name__ == "__main__":
    unittest.main()
