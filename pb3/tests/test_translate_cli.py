"""Test tích hợp CLI cho dịch tự động (sau Claude 07): `translate`,
`extract --translate` + menu [2] chạy thật trên bản sao dự án (tầng slow).

Provider fake qua env PB3_TRANSLATE_FAKE=1 (không gọi mạng thật);
provider offline dùng từ điển cụm game (không cần mạng).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.app import bootstrap
from tests.helpers import (TmpCase, copy_project, register_tmp_dir,
                           slow_subprocess, subprocess_timeout,
                           unregister_tmp_dir)
from tests.test_app_menu_cli import run_menu_scripted

GAME1 = "tests/fixtures/extract/game1.zip"

SAMPLE_KV = {
    "hello": "Hello",
    "play": "Play",
    "points": "You have {0} points",
    "bold": "Click <b>here</b> %s",
    "multi": "Line one\nLine two",
    "empty": "",
}


@slow_subprocess
class TestTranslateCli(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-trcli-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args, stdin=None, fake=True):
        env = dict(os.environ, PB3_PROGRESS="0")
        if fake:
            env["PB3_TRANSLATE_FAKE"] = "1"
        r = subprocess.run([sys.executable, "menu.py"] + list(args),
                           cwd=str(self._shared_proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(180),
                           input=stdin, env=env)
        return r.returncode, r.stdout, r.stderr

    def kv_file(self, name="en.json", data=None):
        path = self.tmp / name
        path.write_text(json.dumps(data if data is not None else SAMPLE_KV,
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)

    def test_cli_translate_fake(self):
        src = self.kv_file()
        rc, stdout, err = self.cli("translate", src, "--to", "vi", "--provider", "fake")
        self.assertEqual(rc, 0, err + stdout)
        out_path = self.tmp / "en.vi.json"
        self.assertTrue(out_path.exists(), stdout)
        got = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(list(got), list(SAMPLE_KV))
        self.assertEqual(got["hello"], "[vi] Hello")
        self.assertEqual(got["play"], "[vi] Play")
        self.assertEqual(got["points"], "[vi] You have {0} points")
        self.assertEqual(got["bold"], "[vi] Click <b>here</b> %s")
        self.assertEqual(got["multi"], "[vi] Line one\n[vi] Line two")
        self.assertEqual(got["empty"], "")
        self.assertIn("đã dịch", stdout)

    def test_cli_translate_offline_glossary(self):
        # Dịch chính fixture en.json (cũng là đầu vào cho checklist iSH).
        src = str(self.tmp / "en.json")
        shutil.copy(Path("tests/fixtures/extract/en.json"), src)
        # --no-cache: các test CLI dùng chung một bản sao dự án nên cache
        # của test fake (chạy trước) chứa "Play" -> "[vi] Play"; tắt cache
        # để test này thật sự gọi từ điển offline.
        rc, stdout, err = self.cli("translate", src, "--to", "vi", "--provider",
                                   "offline", "--no-cache")
        self.assertEqual(rc, 0, err + stdout)
        got = json.loads((self.tmp / "en.vi.json").read_text(encoding="utf-8"))
        self.assertEqual(got["play"], "Chơi")
        self.assertEqual(got["hello"], "Hello")
        self.assertIn("chưa dịch", stdout)

    def test_cli_translate_out_flag(self):
        src = self.kv_file()
        out = str(self.tmp / "dich.json")
        rc, stdout, err = self.cli("translate", src, "--to", "vi",
                                   "--provider", "fake", "--out", out)
        self.assertEqual(rc, 0, err + stdout)
        self.assertTrue(Path(out).exists())

    def test_cli_translate_bad_lang(self):
        src = self.kv_file()
        rc, stdout, err = self.cli("translate", src, "--to", "xx!")
        self.assertNotEqual(rc, 0)
        self.assertIn("E_INPUT_NOT_FOUND", stdout + err)

    def test_cli_translate_missing_file(self):
        rc, stdout, err = self.cli("translate", str(self.tmp / "khong-co.json"),
                                   "--to", "vi", "--provider", "fake")
        self.assertNotEqual(rc, 0)
        self.assertIn("E_CORRUPT", stdout + err)

    def test_cli_translate_no_cache_flag(self):
        src = self.kv_file()
        rc, stdout, err = self.cli("translate", src, "--to", "vi",
                                   "--provider", "fake", "--no-cache")
        self.assertEqual(rc, 0, err + stdout)
        self.assertTrue((self.tmp / "en.vi.json").exists())

    def test_cli_translate_credit_default(self):
        data = {"title": "9 Kings", "play": "Play"}
        src = self.kv_file(data=data)
        rc, stdout, err = self.cli("translate", src, "--to", "vi", "--provider", "fake")
        self.assertEqual(rc, 0, err + stdout)
        got = json.loads((self.tmp / "en.vi.json").read_text(encoding="utf-8"))
        self.assertEqual(list(got), list(data))
        self.assertEqual(got["title"], "[vi] 9 Kings — Việt Hoá Bởi Bear")
        self.assertEqual(got["play"], "[vi] Play")
        self.assertIn("credit 'Việt Hoá Bởi Bear' đặt ở key: title", stdout)

    def test_cli_translate_no_credit(self):
        data = {"title": "9 Kings", "play": "Play"}
        src = self.kv_file(data=data)
        rc, stdout, err = self.cli("translate", src, "--to", "vi", "--provider",
                                   "fake", "--no-credit")
        self.assertEqual(rc, 0, err + stdout)
        got = json.loads((self.tmp / "en.vi.json").read_text(encoding="utf-8"))
        self.assertEqual(got["title"], "[vi] 9 Kings")
        self.assertNotIn("Việt Hoá Bởi Bear", stdout)
        self.assertNotIn("Việt Hoá Bởi Bear",
                         (self.tmp / "en.vi.json").read_text(encoding="utf-8"))

    def test_cli_translate_credit_no_place_reported(self):
        # SAMPLE_KV không có chuỗi tiêu đề/version -> báo rõ, không lặng lẽ bỏ qua.
        src = self.kv_file()
        rc, stdout, err = self.cli("translate", src, "--to", "vi", "--provider", "fake")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("không đặt được credit", stdout)
        got = json.loads((self.tmp / "en.vi.json").read_text(encoding="utf-8"))
        self.assertNotIn("Việt Hoá Bởi Bear",
                         json.dumps(got, ensure_ascii=False))

    def test_cli_extract_translate_no_credit_flag(self):
        out = self.tmp / "ext-tr-nocredit"
        out.mkdir(parents=True, exist_ok=True)
        rc, stdout, err = self.cli("extract", GAME1, "--out", str(out),
                                   "--translate", "vi", "--translate-provider",
                                   "fake", "--no-credit")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("tổng: 21 entry", stdout)
        translated = out / "English.vi.json"
        self.assertTrue(translated.exists(), stdout)
        self.assertNotIn("Việt Hoá Bởi Bear",
                         translated.read_text(encoding="utf-8"))

    def test_cli_extract_translate_fake(self):
        out = self.tmp / "ext-tr"
        out.mkdir(parents=True, exist_ok=True)
        rc, stdout, err = self.cli("extract", GAME1, "--out", str(out),
                                   "--translate", "vi", "--translate-provider", "fake")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("tổng: 21 entry", stdout)
        translated = out / "English.vi.json"
        self.assertTrue(translated.exists(), stdout)
        got = json.loads(translated.read_text(encoding="utf-8"))
        self.assertEqual(got["play"], "[vi] Play")
        self.assertIn("đã dịch", stdout)


@slow_subprocess
class TestMenuTranslate(TmpCase):
    def test_menu_2_translate_fake(self):
        proj = copy_project(self.tmp / "proj")
        app = bootstrap(proj)
        self.addCleanup(app.close)
        os.environ["PB3_TRANSLATE_FAKE"] = "1"
        self.addCleanup(os.environ.pop, "PB3_TRANSLATE_FAKE", None)
        fixture = str(proj / GAME1)
        rc, txt = run_menu_scripted(app, ["2", fixture, "", "vi", "", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("tổng: 21 entry", txt)
        self.assertIn("đã dịch", txt)
        out = proj / "workspace" / "output" / "game1"
        translated = out / "English.vi.json"
        self.assertTrue(translated.exists(), txt)
        got = json.loads(translated.read_text(encoding="utf-8"))
        self.assertEqual(got["play"], "[vi] Play")


if __name__ == "__main__":
    unittest.main()
