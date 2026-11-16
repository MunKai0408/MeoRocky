"""Test tích hợp CLI cho trích xuất ngôn ngữ (Claude 07): `extract` +
`--apply` + menu [2] chạy thật trên bản sao dự án (tầng slow)."""
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
                           subprocess_timeout, unregister_tmp_dir)
from tests.test_app_menu_cli import run_menu_scripted

GAME1 = "tests/fixtures/extract/game1.zip"
AMBIG = "tests/fixtures/extract/ambig.zip"
UNITY = "tests/fixtures/unity/sample.unity3d"
GREETING_VPATH = ("sample.unity3d::CAB-0123456789abcdef0123456789abcdef"
                  "::greeting")


class TestLanguageCli(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-langcli-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args, stdin=None):
        r = subprocess.run([sys.executable, "menu.py"] + list(args),
                           cwd=str(self._shared_proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(180),
                           input=stdin,
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def outdir(self, name):
        d = self.tmp / name
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def test_cli_extract_zip(self):
        out = self.outdir("ext1")
        rc, stdout, err = self.cli("extract", GAME1, "--out", out)
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("tổng: 21 entry", stdout)
        for name in ("English.json", "Chinese.json", "Chinese (Simplified).json",
                     "English.en.json", "app.json", "ui.json",
                     "extraction_report.json", "extraction_report.txt"):
            self.assertTrue((Path(out) / name).exists(), name)
        en = json.loads((Path(out) / "English.en.json").read_text(encoding="utf-8"))
        self.assertEqual(en["hello"], "Hello")

    def test_cli_extract_unity(self):
        out = self.outdir("ext2")
        rc, stdout, err = self.cli("extract", UNITY, "--out", out)
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("engine: unity (CHẮC CHẮN", stdout)
        cfg = json.loads((Path(out) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg, {"level": "1", "lang": "vi", "title": "9 Kings"})

    def test_cli_file_pin_raw_scan(self):
        out = self.outdir("ext3")
        rc, stdout, err = self.cli("extract", UNITY, "--file", GREETING_VPATH,
                                   "--out", out)
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("dò chuỗi thô", stdout)
        g = json.loads((Path(out) / "greeting.json").read_text(encoding="utf-8"))
        self.assertEqual(g, {"off_00000000": "Xin chao 9 Kings!"})

    def test_cli_locale_filter(self):
        out = self.outdir("ext4")
        rc, stdout, err = self.cli("extract", GAME1, "--locale", "zh", "--out", out)
        self.assertEqual(rc, 0, err + stdout)
        self.assertTrue((Path(out) / "Chinese.json").exists())
        self.assertFalse((Path(out) / "English.json").exists())

    def test_cli_ambig_without_tty_shows_ask(self):
        # stdin không phải TTY -> không hỏi, in lựa chọn rồi thoát 0.
        rc, stdout, err = self.cli("extract", AMBIG, "--out", self.outdir("ext5"),
                                   stdin="")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("CHƯA TRÍCH XUẤT", stdout)
        self.assertIn("a/en.json", stdout)
        self.assertIn("b/en.json", stdout)

    def test_cli_garbage_no_candidate(self):
        src = self.tmp / "garbage.zip"
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("data.bin", b"\x00\x01\x02\xff" * 64)
        src.write_bytes(buf.getvalue())
        rc, stdout, err = self.cli("extract", str(src), "--out", self.outdir("ext6"))
        self.assertNotEqual(rc, 0)
        self.assertIn("E_NO_CANDIDATE", stdout + err)

    def _extract_then_edit(self, source, out_name, json_name, **changes):
        out = Path(self.outdir(out_name))
        rc, stdout, err = self.cli("extract", source, "--out", str(out))
        self.assertEqual(rc, 0, err + stdout)
        target = out / json_name
        data = json.loads(target.read_text(encoding="utf-8"))
        data.update(changes)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return out, str(target)

    def test_cli_apply_dry_run(self):
        written = (self._shared_proj / "workspace" / "output" / "game1"
                   / "strings.csv")
        if written.exists():
            written.unlink()
        _out, edited = self._extract_then_edit(GAME1, "ext7", "English.json",
                                               play="Chơi")
        rc, stdout, err = self.cli("extract", GAME1, "--apply", edited)
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("đổi 1 key", stdout)
        self.assertIn("dry-run", stdout)
        self.assertFalse(written.exists())

    def test_cli_apply_write_plain(self):
        _out, edited = self._extract_then_edit(GAME1, "ext8", "English.json",
                                               play="Chơi")
        rc, stdout, err = self.cli("extract", GAME1, "--apply", edited,
                                   "--allow-write")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("đã ghi output", stdout)
        written = self._shared_proj / "workspace" / "output" / "game1" / "strings.csv"
        self.assertTrue(written.exists())
        content = written.read_text(encoding="utf-8")
        self.assertIn("play,Chơi,开始", content)  # cột zh giữ nguyên

    def test_cli_apply_write_unity(self):
        _out, edited = self._extract_then_edit(UNITY, "ext9", "config.json",
                                               title="9 Kings VIET NAM")
        rc, stdout, err = self.cli("extract", UNITY, "--apply", edited,
                                   "--allow-write")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("đã ghi output", stdout)
        written = self._shared_proj / "workspace" / "output" / "sample" / "sample.unity3d"
        self.assertTrue(written.exists())
        self.assertGreater(written.stat().st_size, 0)


class TestMenuExtract(TmpCase):
    def test_menu_2_extracts_fixture(self):
        proj = copy_project(self.tmp / "proj")
        app = bootstrap(proj)
        self.addCleanup(app.close)
        fixture = str(proj / GAME1)
        rc, txt = run_menu_scripted(app, ["2", fixture, "", "", "", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("tổng: 21 entry", txt)
        out = proj / "workspace" / "output" / "game1"
        self.assertTrue((out / "English.json").exists())
        self.assertTrue((out / "extraction_report.json").exists())


if __name__ == "__main__":
    unittest.main()
