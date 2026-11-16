"""Test tích hợp CLI cho batch (Claude 09): `batch` chạy thật trên bản sao
dự án (tầng slow, mẫu tests/test_translate_cli.py).

Manifest 2 game extract trên fixture tests/fixtures/extract/game1.zip
+ 1 game lỗi (không tồn tại): lô chạy hết, failures.txt có mã lỗi đúng.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import (TmpCase, copy_project, register_tmp_dir,
                           slow_subprocess, subprocess_timeout,
                           unregister_tmp_dir)

GAME1 = "tests/fixtures/extract/game1.zip"


@slow_subprocess
class TestBatchCli(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-batchcli-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args):
        env = dict(os.environ, PB3_PROGRESS="0")
        r = subprocess.run([sys.executable, "menu.py"] + list(args),
                           cwd=str(self._shared_proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(180), env=env)
        return r.returncode, r.stdout, r.stderr

    def manifest(self, games):
        p = self.tmp / "manifest.csv"
        lines = ["path,locale,mode"]
        for g in games:
            lines.append("%s,%s,%s" % (g[0], g[1] if len(g) > 1 else "",
                                       g[2] if len(g) > 2 else "extract"))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(p)

    def out_dir_of(self, stdout):
        m = re.search(r"output: (\S+)", stdout)
        self.assertIsNotNone(m, stdout)
        return Path(m.group(1))

    def test_cli_batch_two_extract_one_error(self):
        game1 = str(self._shared_proj / GAME1)
        missing = str(self.tmp / "khong-co.zip")
        manifest = self.manifest([(game1, "en"), (game1, "zh-Hans"), (missing, "en")])
        rc, stdout, err = self.cli("batch", manifest, "--allow-write")
        # Có game lỗi -> mã thoát 1 (xem failures.txt); lô vẫn chạy hết.
        self.assertEqual(rc, 1, err + stdout)
        out_dir = self.out_dir_of(stdout)
        self.assertTrue(out_dir.is_dir(), stdout)
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["total"], summary["ok"], summary["failed"]),
                         (3, 2, 1))
        failures = (out_dir / "failures.txt").read_text(encoding="utf-8")
        self.assertIn("E_INPUT_NOT_FOUND", failures)
        index = (out_dir / "index.csv").read_text(encoding="utf-8")
        self.assertIn("E_INPUT_NOT_FOUND", index)
        # Game extract thật: thư mục game có file JSON đã xuất.
        game_dirs = sorted(p for p in out_dir.iterdir() if p.name.startswith("game_"))
        self.assertEqual(len(game_dirs), 3)
        exported = [p.name for d in game_dirs for p in d.iterdir()
                    if p.suffix == ".json" and p.name != "result.json"
                    and not p.name.startswith("extraction_")]
        self.assertTrue(exported, [list(d.iterdir()) for d in game_dirs])
        self.assertTrue((out_dir / "checkpoint.json").exists())

    def test_cli_batch_dry_run_writes_no_export(self):
        game1 = str(self._shared_proj / GAME1)
        manifest = self.manifest([(game1, "en")])
        rc, stdout, err = self.cli("batch", manifest, "--dry-run")
        self.assertEqual(rc, 0, err + stdout)
        out_dir = self.out_dir_of(stdout)
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["ok"], 1)
        self.assertTrue(summary["dry_run"])
        game_dirs = [p for p in out_dir.iterdir() if p.name.startswith("game_")]
        self.assertEqual(len(game_dirs), 1)
        names = sorted(p.name for p in game_dirs[0].iterdir())
        self.assertEqual(names, ["result.json"])

    def test_cli_batch_missing_manifest_is_exit_1(self):
        rc, stdout, err = self.cli("batch", str(self.tmp / "khong-co.csv"))
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err + stdout)

    def test_cli_resume_with_out_skips_done_games(self):
        game1 = str(self._shared_proj / GAME1)
        missing = str(self.tmp / "khong-co.zip")
        manifest = self.manifest([(game1, "en"), (game1, "zh-Hans"), (missing, "en")])
        out = self.tmp / "batch-out"
        rc, stdout, err = self.cli("batch", manifest, "--allow-write", "--out", str(out))
        self.assertEqual(rc, 1, err + stdout)
        s1 = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((s1["total"], s1["ok"], s1["failed"]), (3, 2, 1))
        rc, stdout, err = self.cli("batch", manifest, "--allow-write",
                                   "--resume", "--out", str(out))
        self.assertEqual(rc, 1, err + stdout)
        self.assertIn("tiếp tục từ", stdout)
        s2 = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((s2["ok"], s2["failed"], s2["skipped"]), (0, 1, 2))
        self.assertEqual(s2["resumed_from"], str(out))

    def test_cli_resume_without_out_finds_previous_run(self):
        # Tích hợp C09: --resume không kèm --out phải tự tìm lần chạy cũ
        # (trước đây tạo thư mục mới nên resume lặng lẽ không làm gì).
        game1 = str(self._shared_proj / GAME1)
        manifest = self.manifest([(game1, "en")])
        rc, stdout, err = self.cli("batch", manifest, "--allow-write")
        self.assertEqual(rc, 0, err + stdout)
        first = self.out_dir_of(stdout)
        rc, stdout, err = self.cli("batch", manifest, "--allow-write", "--resume")
        self.assertEqual(rc, 0, err + stdout)
        self.assertIn("tiếp tục từ", stdout)
        self.assertEqual(self.out_dir_of(stdout), first)
        s2 = json.loads((first / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((s2["ok"], s2["skipped"]), (0, 1))
        self.assertEqual(s2["resumed_from"], str(first))


if __name__ == "__main__":
    unittest.main()
