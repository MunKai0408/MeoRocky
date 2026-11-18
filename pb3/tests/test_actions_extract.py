#!/usr/bin/env python3
"""Test cho tinh nang trich xuat IPA lon tren may chu (GitHub Actions).

- Script `.github/scripts/extract_large.py`: chay that tren fixture
  game1.zip (co san tren main), ghi summary.json dung cau truc; that bai
  kin (exit 1) khi file IPA khong ton tai.
- Workflow `.github/workflows/extract-large.yml`: chi chay bang
  workflow_dispatch, du input, tai bang gdown, trich bang script cua repo,
  upload artifact, khong commit gi.
- Ham JS `serverDriveIdFromInput` trong app.js: tach dung Drive file ID
  tu ID tran / link Drive (chay that ham JS bang node, khong port tay).
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.helpers import TmpCase

PB3 = Path(__file__).resolve().parent.parent
ROOT = PB3.parent
SCRIPT = ROOT / ".github" / "scripts" / "extract_large.py"
WORKFLOW = ROOT / ".github" / "workflows" / "extract-large.yml"
FIXTURE = PB3 / "tests" / "fixtures" / "extract" / "game1.zip"


def run_script(args, cwd, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=str(cwd), env=e, capture_output=True, text=True, timeout=300)


class TestActionsExtract(TmpCase):
    """Test Actions extract phai nam trong lop TestCase (quy uoc repo):
    he tiering (`tests/tiering.py::_facts_cache_note`) chi ghi cache cho
    test trong lop — test ham cap module se "tang hinh" voi `plan_modules`
    va lam hong `test_plan_modules_matches_plan_counts` (80 != 81); lop
    thuong (khong phai TestCase) thi bo nap that bang unittest lai khong
    thay test — lam hong `test_plan_counts_match_real_load` (2018 != 2012)
    va `test_build_selective_matches_plan`. Dung `self.tmp` (TmpCase) thay
    cho fixture pytest `tmp_path`."""

    def test_script_extracts_fixture(self):
        out = self.tmp / "out"
        r = run_script([
            "--ipa", str(FIXTURE), "--game", "test-game",
            "--file-id", "TESTFILEID", "--locales", "en,zh",
            "--out", str(out), "--download-seconds", "1.5",
        ], cwd=ROOT, env={"TMPDIR": str(self.tmp)})
        assert r.returncode == 0, r.stdout + r.stderr
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["ok"] is True
        assert summary["game"] == "test-game"
        assert summary["file_id"] == "TESTFILEID"
        assert summary["locales"] == ["en", "zh"]
        assert summary["download_seconds"] == 1.5
        assert summary["extract_seconds"] >= 0
        assert summary["peak_ram_mb"] > 0
        assert summary["ipa_md5"]
        by_name = {f["name"]: f for f in summary["files"]}
        assert set(by_name) == {"en.json", "zh-Hans.json"}
        assert by_name["en.json"]["keys"] == 8
        assert by_name["zh-Hans.json"]["keys"] == 8
        for name in ("en.json", "zh-Hans.json"):
            kv = json.loads((out / name).read_text(encoding="utf-8"))
            assert len(kv) == 8
            assert by_name[name]["md5"]
        assert "Trich xuat: OK" in r.stdout

    def test_script_fails_closed_on_missing_ipa(self):
        r = run_script([
            "--ipa", str(self.tmp / "khong-co.ipa"), "--game", "test-game",
            "--out", str(self.tmp / "out"),
        ], cwd=ROOT, env={"TMPDIR": str(self.tmp)})
        assert r.returncode == 1
        assert "LOI" in r.stdout or "Loi" in r.stdout

    def test_workflow_file_wiring(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text
        assert "on:\n  push:" not in text
        assert "pull_request" not in text
        for field in ("file_id:", "game_name:", "locales:"):
            assert field in text, "workflow thieu input %s" % field
        assert "gdown" in text
        assert "drive.google.com/uc?id=" in text
        # gdown 5+/6+ khong con co --id; loi quota tai cong khai cua Drive
        # ("Too many users") phai co thong bao rieng de user biet chi can cho
        assert "gdown --id" not in text
        assert "too many users" in text.lower()
        assert "extract_large.py" in text
        assert "upload-artifact@v4" in text
        assert "actions: write" in text
        assert "contents: read" in text
        assert "timeout-minutes: 90" in text
        # Khong commit gi ve repo, khong dung secret nao (chi GITHUB_TOKEN
        # mac dinh cua runner cho upload-artifact).
        assert "git push" not in text
        assert "secrets." not in text

    def test_workflow_yaml_parses(self):
        yaml = pytest.importorskip("yaml", reason="thieu pyyaml (chi chay local)")
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        on = data.get("on") or data.get(True)  # "on" parse thanh True (YAML 1.1)
        assert isinstance(on, dict) and "workflow_dispatch" in on
        assert "push" not in on and "pull_request" not in on
        inputs = on["workflow_dispatch"]["inputs"]
        assert inputs["file_id"]["required"] is True
        assert inputs["game_name"]["required"] is True
        assert inputs["locales"]["default"] == "en,zh"
        jobs = data["jobs"]
        assert set(jobs) == {"extract"}
        steps = jobs["extract"]["steps"]
        assert any("gdown" in str(s.get("run", "")) for s in steps)
        assert any("extract_large.py" in str(s.get("run", "")) for s in steps)
        assert sum(1 for s in steps
                   if str(s.get("uses", "")).startswith("actions/upload-artifact"))
        assert data["permissions"]["actions"] == "write"

    def test_drive_id_parsing_runs_real_js_function(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("thieu node")
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        start = app_js.index("function serverDriveIdFromInput")
        end = app_js.index("\n}\n", start) + len("\n}\n")
        fn = app_js[start:end]
        cases = [
            ("18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM",
             "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"),
            ("https://drive.google.com/file/d/18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM/view?usp=sharing",
             "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"),
            ("https://drive.google.com/open?id=18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM",
             "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"),
            ("https://drive.google.com/uc?export=download&id=18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM",
             "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"),
            ("", ""),
            ("khong phai link", ""),
            ("abc", ""),
        ]
        js_path = self.tmp / "drive_id_check.mjs"
        js_path.write_text(
            fn + "\nconst cases = " + json.dumps(cases) + ";\n"
            "for (const [input, want] of cases) {\n"
            "  const got = serverDriveIdFromInput(input);\n"
            "  if (got !== want) {\n"
            "    console.error('SAI: ' + JSON.stringify(input) + ' -> ' + JSON.stringify(got) + ' (muon ' + JSON.stringify(want) + ')');\n"
            "    process.exit(1);\n"
            "  }\n"
            "}\nconsole.log('drive-id OK: ' + cases.length + ' case');\n",
            encoding="utf-8")
        r = subprocess.run([node, str(js_path)], capture_output=True, text=True,
                           timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "drive-id OK: 7 case" in r.stdout

    def test_server_ui_ids_exist_once(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        for ident in ("server-section", "server-game-name", "server-drive-input",
                      "server-locales", "server-gh-token", "server-token-state",
                      "btn-server-save-token", "btn-server-clear-token",
                      "btn-server-run", "btn-server-refresh", "server-status",
                      "server-results", "btn-large-file-server"):
            assert html.count('id="%s"' % ident) == 1, "thieu/trung id %s" % ident
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        assert "function serverDriveIdFromInput" in app_js
        assert "meorocky_gh_token" in app_js
        assert "meorocky_server_run" in app_js
        assert "extract-large.yml" in app_js
        # Khoi server khong log token: khong co console.log trong doan server.
        server_block = app_js.split(
            "// ---- Xử lý file lớn trên máy chủ")[1].split("// ---- Dịch ----")[0]
        assert "console.log" not in server_block
        # Khong co token that dang ghp_ + >=20 ky tu (chuoi "ghp_" trong regex
        # kiem tra dinh dang thi duoc phep).
        assert not re.search(r"ghp_[A-Za-z0-9]{20,}", app_js)
        assert not re.search(r"github_pat_[A-Za-z0-9_]{20,}", app_js)
