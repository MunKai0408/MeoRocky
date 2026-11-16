"""Quét tĩnh plugin (registry/scan.py, decisions #53). Đây là lint, không phải bảo mật."""
import copy
import os
import textwrap
import unittest

from registry import loader
from registry.scan import scan_source
from tests.helpers import TmpCase
from tests.test_isolation import BASE


class TestScanSource(unittest.TestCase):
    def bad(self, src):
        self.assertTrue(scan_source(src), src)

    def test_clean_code_passes(self):
        self.assertEqual(scan_source("import json, re, os\nx = os.path.join('a', 'b')\ny = {'code': 1, 'select': 2}\n"), [])

    def test_forbidden_imports(self):
        for src in ("import socket", "import _socket", "from posix import system", "import ctypes.util",
                    "import subprocess as sp", "import importlib", "import builtins"):
            self.bad(src)

    def test_relative_import_not_flagged(self):
        self.assertEqual(scan_source("from . import socket_helpers\n"), [])

    def test_dynamic_code_and_introspection(self):
        for src in ("eval('1')", "exec('1')", "__import__('os')", "compile('1','x','eval')",
                    "().__class__.__base__.__subclasses__()", "f.__globals__", "x = __builtins__"):
            self.bad(src)

    def test_sys_modules_and_os_process(self):
        for src in ("import sys\nsys.modules['x']", "import os\nos.system('x')", "import os\nos.execv(1, 2)",
                    "import os\nos.fork()", "import os\nos.spawnl(1, 2)"):
            self.bad(src)

    def test_literal_module_name_caught(self):
        self.bad("x = 'posix'")
        self.bad("x = '_socket'")

    def test_syntax_error_reported(self):
        self.assertIn("cú pháp", scan_source("def (:")[0])

    def test_known_limit_string_concat_bypasses(self):
        # GHI NHẬN GIỚI HẠN: lint không bắt được mã che giấu. Test này khóa lại để không ai tưởng là bịt được.
        self.assertEqual(scan_source("n = '_so' + 'cket'\n"), [])

    def test_findings_are_capped(self):
        self.assertLessEqual(len(scan_source("import socket\n" * 100)), 20)


class TestScanInLoader(TmpCase):
    def mk(self, body, scope="user"):
        d = self.tmp / ("plugins_" + scope) / "p1"
        os.makedirs(str(d))
        m = copy.deepcopy(BASE)
        m["entry"] = "plugin:P"
        src = ("from sdk.api import Plugin\nMANIFEST = %r\n%s\nclass P(Plugin):\n    MANIFEST = MANIFEST\n"
               "    def parse(self, ctx, cand): return None\n    def build(self, ctx, t, b): return b\n") % (m, textwrap.dedent(body))
        (d / "plugin.py").write_text(src, encoding="utf-8")
        return [(scope, str(self.tmp / ("plugins_" + scope)))]

    def test_user_plugin_rejected_before_import(self):
        marker = str(self.tmp / "ran.txt")
        dirs = self.mk("import posix\nX = %r\n" % marker)
        rec = loader.discover(dirs).records[0]
        self.assertEqual((rec.state, rec.code), ("disabled", "E_PLUGIN_FORBIDDEN"))
        self.assertIn("quét tĩnh", rec.reason)
        self.assertIsNone(rec.instance)

    def test_clean_user_plugin_loads(self):
        rec = loader.discover(self.mk("X = 1\n")).records[0]
        self.assertEqual((rec.state, rec.code), ("ok", "E_OK"))

    def test_static_scan_can_be_disabled(self):
        rec = loader.discover(self.mk("import ctypes\n"), static_scan=False).records[0]
        self.assertEqual(rec.state, "ok")

    def test_builtin_scope_not_scanned(self):
        rec = loader.discover(self.mk("import ctypes\n", scope="builtin")).records[0]
        self.assertEqual(rec.state, "ok")

    def test_reason_truncates_long_findings(self):
        rec = loader.discover(self.mk("import socket\nimport ssl\nimport posix\nimport nt\nimport ctypes\n")).records[0]
        self.assertIn("mục nữa", rec.reason)


if __name__ == "__main__":
    unittest.main()
