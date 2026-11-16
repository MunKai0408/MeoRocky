"""Test tích hợp CLI cho plugin định dạng Claude 06: `plugin list` + `plugin test`
chạy thật trên bản sao dự án (tiến trình con — tầng slow)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import TmpCase, copy_project, register_tmp_dir, subprocess_timeout, unregister_tmp_dir

ALL_FORMAT_IDS = ["json_text", "csv_table", "xml_strings", "yaml_ini", "properties",
                  "apple_plist", "apple_strings", "sqlite_loc", "po_mo", "xliff", "resx",
                  "generic_strings"]
# Đại diện kiểm qua CLI thật (conformance đủ 12 id đã kiểm trong bộ nhớ ở từng file test_format_*).
CLI_TEST_IDS = ["json_text", "apple_strings", "sqlite_loc", "po_mo"]


class TestFormatCli(TmpCase):
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

    def cli(self, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args), cwd=str(self._shared_proj),
                           capture_output=True, text=True, timeout=subprocess_timeout(120),
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_plugin_list_shows_all_format_plugins(self):
        rc, out, err = self.cli("plugin", "list")
        self.assertEqual(rc, 0, err)
        for pid in ALL_FORMAT_IDS:
            self.assertIn("%s  [ok]" % pid, out, err)

    def test_plugin_test_representative_ids(self):
        for pid in CLI_TEST_IDS:
            with self.subTest(pid=pid):
                rc, out, err = self.cli("plugin", "test", pid)
                self.assertEqual(rc, 0, err + out)
                self.assertIn("KẾT QUẢ: PASS", out, err + out)


if __name__ == "__main__":
    unittest.main()
