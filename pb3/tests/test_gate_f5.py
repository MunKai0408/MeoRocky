"""Test F5: cong buoc 6 mo rong + bao cao tung buoc Mod job + duong lon.

- (f) do dai ban dich: key dai hon goc chi bi tu choi khi thuoc duong
  ghi do dai co dinh (too_long / length_must_match, giong il2cpp);
  duong rebuild cho phep dai hon (chi canh bao).
- (g) atlas chu Viet: chu Viet dang dung phai co glyph trong atlas SDF
  cua TMP game se dung; cuu bang Dynamic [HYPOTHESIS] hoac fallback;
  font hien thi da chi dinh thi atlas chi tham khao.
- run_gate tra 7 check; verdict FAIL khi (f)/(g) FAIL.
- run_mod_job tra summary["steps"] (5 buoc: gate/apply/repack/pressed/
  artifact) — moi buoc ghi ro file da ghi + muc bi tu choi + ly do.
- Duong lon Colab/Actions con chay: workflow YAML hop le, co goi
  mod_large.py dung tham so, notebook Buoc 4 Mod con dung run_mod_job.

Fixture tu dung bang builder co san trong repo (nhu test_gate.py /
test_mod_job.py), KHONG them file fixture roi, khong dung file game
that, khong can mang.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)
import importlib.util as _importlib_util  # noqa: E402
_spec = _importlib_util.spec_from_file_location(
    "font_build_fixtures",
    str(ROOT / "tests" / "fixtures" / "font" / "build_fixtures.py"))
font_fx = _importlib_util.module_from_spec(_spec)  # noqa: E402
_spec.loader.exec_module(font_fx)  # noqa: E402

from tests.helpers import TmpCase  # noqa: E402
from sdk import gate  # noqa: E402
from sdk import mod_job as mj  # noqa: E402

EN = {"hello": "Hello", "play": "Play", "quit": "Quit",
      "title": "9 Kings"}
MENU = {"start": "Start Game", "options": "Options"}
DIALOG = {"greeting": "Welcome, hero!", "farewell": "Goodbye!"}
SRC = dict(EN)
SRC.update(MENU)
SRC.update(DIALOG)
VI = {"hello": "Xin chào", "play": "Chơi", "quit": "Thoát",
      "title": "9 Kings", "start": "Bắt đầu chơi",
      "options": "Tùy chọn", "greeting": "Chào mừng, anh hùng!",
      "farewell": "Tạm biệt!"}
CAB_A = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CAB_B = "CAB-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _sf(entries):
    objs = []
    for pid, name, val in entries:
        script = val if isinstance(val, str) else json.dumps(
            val, ensure_ascii=False)
        objs.append((pid, 0, text_asset_data(name, script)))
    return build_serialized_file(version=17, objects=objs, types=[(49, None)])


def _bundle(sf_bytes, cab=CAB_A, compression="lz4", **kw):
    return build_bundle(version=7, entries=[(cab, 4, sf_bytes)],
                        compression=compression, **kw)


def _zip_two(bundle_a, bundle_b):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.bundle", bundle_a)
        zf.writestr("b.bundle", bundle_b)
    return buf.getvalue()


def _prov_all():
    keys = {}
    for k in list(EN) + list(MENU):
        keys[k] = "a.bundle"
    for k in DIALOG:
        keys[k] = "b.bundle"
    return {"format": "beartea-merge-provenance/1", "keys": keys,
            "conflicts": [], "conflicts_total": 0}


def _fixture_zip():
    sf_a = _sf([(1001, "en.json", EN), (1002, "menu.json", MENU)])
    sf_b = _sf([(1003, "dialog.json", DIALOG)])
    return _zip_two(_bundle(sf_a, CAB_A), _bundle(sf_b, CAB_B))


def _fixture_bundle():
    sf = _sf([(1001, "en.json", SRC)])
    return _bundle(sf, CAB_A)


def _full_display():
    return gate.font_entry_from_bytes(
        font_fx.build_font_full_vietnamese(), "DonorFont.ttf")


def _check(res, check_id):
    for c in res["checks"]:
        if c["id"] == check_id:
            return c
    raise AssertionError("thieu check %s" % check_id)


def _tmp_entry(missing, *, name="Main SDF", pid=200, label="Static",
               src_pid=None, fallback=()):
    """1 muc tmp_font_asset tong hop. missing: codepoint atlas thieu
    (chi chu cai — covered = 134 - len(missing)). Dynamic can them
    source_font_file + kich thuoc atlas de build_gate_report tinh
    dynamic_rescue."""
    entry = {"kind": "tmp_font_asset", "name": name, "status": "partial",
             "confirmed": True, "path_id": pid,
             "missing_codepoints": list(missing),
             "covered": 134 - len(missing), "total": 142,
             "population_label": label,
             "fallback_count": len(fallback),
             "fallback_entries": [{"path_id": p} for p in fallback]}
    if label == "Dynamic":
        entry.update({
            "population_mode": 0,
            "source_font_file": {"path_id": src_pid},
            "atlas_width": 1024, "atlas_height": 1024,
            "used_area_px": 100000, "median_glyph_rect": [40, 40],
            "atlas_used_pct": 9.5, "free_rects_fit_median_glyph": 50})
    return entry


def _src_font(pid=100, missing=()):
    return {"kind": "unity_font", "name": "LiberationSans",
            "status": "full", "path_id": pid,
            "missing_codepoints": list(missing),
            "covered": 142 - len(missing), "total": 142,
            "cmap_letters": 134 - len(missing)}


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


class TestTranslationLength(unittest.TestCase):
    def test_no_source_fails_closed(self):
        r = gate.check_translation_length(None, {"a": "Xin chào"})
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "no_source")

    def test_longer_without_fixed_keys_passes_with_warning(self):
        r = gate.check_translation_length(
            {"a": "Hello", "b": "Play"},
            {"a": "Xin chào các bạn", "b": "Chơi"})
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["longer"], 2)
        self.assertEqual(r["counts"]["rejected"], 0)
        self.assertTrue(r["warnings"])
        self.assertEqual(r["longer_keys"], ["a", "b"])

    def test_fixed_key_longer_rejected_too_long(self):
        r = gate.check_translation_length(
            {"a": "Hello"}, {"a": "Xin chào các bạn"},
            fixed_length_keys={"a"})
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "too_long")
        self.assertEqual(r["counts"]["rejected"], 1)
        self.assertEqual(r["rejected_keys"], ["a"])

    def test_fixed_key_shorter_passes_non_strict_warns(self):
        r = gate.check_translation_length(
            {"a": "Hello"}, {"a": "Hi"}, fixed_length_keys={"a"})
        self.assertEqual(r["status"], "pass")
        self.assertTrue(r["warnings"])
        r2 = gate.check_translation_length(
            {"a": "Hello"}, {"a": "Hi"}, fixed_length_keys={"a"},
            strict=True)
        self.assertEqual(r2["status"], "fail")
        self.assertEqual(r2["failures"][0]["kind"], "length_must_match")

    def test_fixed_length_exact_match_passes(self):
        r = gate.check_translation_length(
            {"a": "Hello"}, {"a": "Chào!"}, fixed_length_keys={"a"})
        # "Chào!" = 6 byte, goc 5 byte -> dai hon -> van FAIL.
        self.assertEqual(r["status"], "fail")
        r2 = gate.check_translation_length(
            {"a": "Hello"}, {"a": "Hello"}, fixed_length_keys={"a"})
        self.assertEqual(r2["status"], "pass")
        self.assertEqual(r2["counts"]["longer"], 0)


class TestVietnameseAtlasGlyphs(unittest.TestCase):
    def test_no_tmp_passes(self):
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào"}, [])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["atlases"], 0)

    def test_no_fonts_scanned_fails_closed(self):
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào"}, None)
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "no_fonts")

    def test_not_scanned_with_display_passes_without_claiming_no_tmp(self):
        r = gate.check_vietnamese_atlas_glyphs(
            {"a": "Xin chào"}, None, display_fonts=[_full_display()])
        self.assertEqual(r["status"], "pass")
        self.assertIn("chưa quét", r["summary_vi"])
        self.assertTrue(r["warnings"])

    def test_display_font_supplied_passes_with_warnings(self):
        tmp = _tmp_entry([0xE0])
        r = gate.check_vietnamese_atlas_glyphs(
            {"a": "Xin chào à"}, [tmp],
            display_fonts=[_full_display()])
        self.assertEqual(r["status"], "pass")
        self.assertTrue(r["warnings"])

    def test_static_atlas_missing_fails(self):
        tmp = _tmp_entry([0xE0, 0x1EB9])
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à ẹ"},
                                               [tmp])
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "atlas_missing_glyph")
        self.assertEqual(r["failures"][0]["missing_chars"], ["à", "ẹ"])
        self.assertEqual(r["counts"]["missing_used"], 2)

    def test_static_atlas_covering_passes(self):
        tmp = _tmp_entry([0x1EA1])
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào"}, [tmp])
        self.assertEqual(r["status"], "pass")

    def test_dynamic_rescue_passes_with_hypothesis_warning(self):
        src = _src_font()
        tmp = _tmp_entry([0xE0], label="Dynamic", src_pid=100)
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à"},
                                               [src, tmp])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["rescued_dynamic"], 1)
        self.assertTrue(any("HYPOTHESIS" in w for w in r["warnings"]))

    def test_dynamic_without_source_link_fails(self):
        tmp = _tmp_entry([0xE0], label="Dynamic", src_pid=None)
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à"}, [tmp])
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "atlas_missing_glyph")

    def test_fallback_rescue_passes(self):
        fb = _tmp_entry([], name="Fallback SDF", pid=300)
        tmp = _tmp_entry([0xE0], fallback=(300,))
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à"},
                                               [tmp, fb])
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["rescued_fallback"], 1)

    def test_unknown_atlas_only_fails_closed(self):
        unk = _tmp_entry([0xE0], name="Old SDF")
        unk["confirmed"] = False
        unk["atlas_unknown"] = True
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à"}, [unk])
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "atlas_unknown")

    def test_unknown_beside_confirmed_warns_only(self):
        good = _tmp_entry([])
        unk = _tmp_entry([0xE0], name="Old SDF")
        unk["confirmed"] = False
        unk["atlas_unknown"] = True
        r = gate.check_vietnamese_atlas_glyphs({"a": "Xin chào à"},
                                               [good, unk])
        self.assertEqual(r["status"], "pass")
        self.assertTrue(r["warnings"])

    def test_non_vietnamese_target_passes_with_warning(self):
        r = gate.check_vietnamese_atlas_glyphs(
            {"a": "Grüße"}, [], target_lang="de")
        self.assertEqual(r["status"], "pass")
        self.assertTrue(r["warnings"])

    def test_unknown_target_fails(self):
        r = gate.check_vietnamese_atlas_glyphs(
            {"a": "Xin chào"}, [], target_lang="xx")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "font_unknown_target")


class TestGateF5Integration(unittest.TestCase):
    def _run(self, translations, **kw):
        kw.setdefault("display_fonts", [_full_display()])
        return gate.run_gate(_fixture_zip(),
                             source_name="game.zip",
                             translations=translations,
                             source_texts=SRC,
                             provenance=_prov_all(), **kw)

    def test_pass_all_seven_checks(self):
        res = self._run(VI)
        self.assertEqual(res["verdict"], "PASS")
        self.assertEqual([c["id"] for c in res["checks"]],
                         list(gate.CHECK_IDS))
        self.assertEqual(len(res["checks"]), 7)
        self.assertTrue(all(c["status"] == "pass"
                            for c in res["checks"]))

    def test_fixed_length_key_fails_gate(self):
        res = self._run(VI, fixed_length_keys={"greeting"})
        self.assertEqual(res["verdict"], "FAIL")
        chk = _check(res, "translation_length")
        self.assertEqual(chk["status"], "fail")
        self.assertEqual(chk["failures"][0]["kind"], "too_long")
        self.assertEqual(chk["failures"][0]["key"], "greeting")

    def test_static_atlas_fails_gate_without_display(self):
        tmp = _tmp_entry([0xE0])
        res = self._run(VI, fonts=[tmp], display_fonts=())
        self.assertEqual(res["verdict"], "FAIL")
        chk = _check(res, "vietnamese_atlas_glyphs")
        self.assertEqual(chk["status"], "fail")
        self.assertEqual(chk["failures"][0]["kind"], "atlas_missing_glyph")

    def test_static_atlas_passes_gate_with_display(self):
        tmp = _tmp_entry([0xE0])
        res = self._run(VI, fonts=[tmp])
        self.assertEqual(res["verdict"], "PASS")
        chk = _check(res, "vietnamese_atlas_glyphs")
        self.assertEqual(chk["status"], "pass")
        self.assertTrue(chk["warnings"])


class TestModJobStepReport(TmpCase):
    def _run_fixture(self, translations, **kw):
        from sdk import colab_job as cj
        src = _write_json(Path(self.tmp) / "en.json", SRC)
        tr = _write_json(Path(self.tmp) / "vi.json", translations)
        bundle = Path(self.tmp) / "game.unity3d"
        bundle.write_bytes(_fixture_bundle())
        out = Path(self.tmp) / "out"
        job = cj.build_mod_job(
            "9 Kings", "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM",
            "1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR",
            "1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR")
        return mj.run_mod_job(
            ipa_path=str(bundle), original_path=str(src),
            translation_path=str(tr), out_dir=str(out), job=job,
            **kw), out

    def test_done_summary_has_five_steps(self):
        summary, out = self._run_fixture(VI, repack=False)
        self.assertEqual(summary["status"], "done", summary.get("note"))
        steps = summary["steps"]
        self.assertEqual([s["step"] for s in steps],
                         ["gate", "apply", "repack", "pressed", "artifact"])
        gate_step = steps[0]
        self.assertEqual(gate_step["status"], "pass")
        self.assertEqual([w["name"] for w in gate_step["written"]],
                         ["gate-report.json"])
        self.assertEqual(gate_step["rejected_total"], 0)
        apply_step = steps[1]
        self.assertEqual(apply_step["status"], "done")
        self.assertIn("applied.zip",
                      [w["name"] for w in apply_step["written"]])
        self.assertEqual(apply_step["rejected_total"], 0)
        repack_step = steps[2]
        self.assertEqual(repack_step["status"], "skipped")
        self.assertIn("repack", repack_step["skipped_reason"])
        pressed_step = steps[3]
        self.assertEqual(pressed_step["status"], "pass")
        self.assertEqual([w["name"] for w in pressed_step["written"]],
                         ["pressed-gate-report.json"])
        artifact_step = steps[4]
        self.assertIn("gate-report.json",
                      [w["name"] for w in artifact_step["written"]])
        self.assertIn("summary.json",
                      [w["name"] for w in artifact_step["written"]])
        # summary.json tren dia cung co steps.
        on_disk = json.loads((out / "summary.json").read_text(
            encoding="utf-8"))
        self.assertEqual(len(on_disk["steps"]), 5)

    def test_gate_failed_steps_show_rejections(self):
        bad = dict(VI)
        del bad["quit"]
        summary, out = self._run_fixture(bad)
        self.assertEqual(summary["status"], "gate_failed")
        steps = summary["steps"]
        gate_step = steps[0]
        self.assertEqual(gate_step["status"], "failed")
        self.assertTrue(gate_step["rejected_total"] > 0)
        self.assertTrue(any("quit" in r["item"]
                            for r in gate_step["rejected"]))
        self.assertEqual(steps[1]["status"], "skipped")
        self.assertEqual(steps[2]["status"], "skipped")
        self.assertEqual(steps[3]["status"], "skipped")

    def test_build_step_report_lists_length_and_atlas_rejections(self):
        fake_gate = {
            "verdict": "FAIL",
            "refused": [{"unit": "a.bundle", "code": "E_ENCRYPTED",
                         "message": "bundle ma hoa"}],
            "unmatched": [{"key": "ghost", "reason": "not_found",
                           "unit": None}],
            "unmatched_total": 1344,
            "checks": [
                {"id": "translation_length", "status": "fail",
                 "failures": [{"key": "greeting", "kind": "too_long",
                               "message_vi": "ban dich dai hon goc"}]},
                {"id": "vietnamese_atlas_glyphs", "status": "fail",
                 "failures": [{"font": "Main SDF",
                               "kind": "atlas_missing_glyph",
                               "message_vi": "atlas thieu chu à"}]},
            ],
        }
        steps = mj.build_step_report(gate=fake_gate, out_dir=self.tmp,
                                     summary={"status": "gate_failed",
                                              "repack": True})
        gate_step = steps[0]
        self.assertEqual(gate_step["rejected_total"], 4)
        items = [r["item"] for r in gate_step["rejected"]]
        self.assertTrue(any("a.bundle" in i for i in items))
        self.assertTrue(any("ghost" in i for i in items))
        self.assertTrue(any("greeting" in i for i in items))
        self.assertTrue(any("Main SDF" in i for i in items))
        # Tong that (1344 key chua ep) lon hon so liet ke -> co note.
        self.assertIn("1344", gate_step.get("note") or "")


class TestLargePathF5(TmpCase):
    WORKFLOW = ROOT.parent / ".github" / "workflows" / "mod-large.yml"
    NOTEBOOK = ROOT.parent / "colab" / "extract-large-ipa.ipynb"
    SCRIPT = ROOT.parent / ".github" / "scripts" / "mod_large.py"

    def test_workflow_yaml_parses(self):
        import pytest
        yaml = pytest.importorskip("yaml",
                                   reason="thieu pyyaml (chi chay local)")
        data = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        on = data.get("on") or data.get(True)
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("push", on)
        self.assertEqual(on["workflow_dispatch"]["inputs"]["engine"]
                         ["default"], "unity")
        self.assertEqual(set(data["jobs"]), {"mod"})
        self.assertEqual(data["jobs"]["mod"]["timeout-minutes"], 90)
        steps = data["jobs"]["mod"]["steps"]
        run_steps = [s for s in steps if "run" in s]
        self.assertTrue(any("mod_large.py" in s["run"]
                            for s in run_steps),
                        "workflow khong co buoc chay mod_large.py")
        self.assertTrue(any("upload-artifact@v4" in str(s.get("uses", ""))
                            for s in steps))

    def test_workflow_flags_match_script_argparse(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        m = re.search(r"python3 \.github/scripts/mod_large\.py(.*?)(?=\n      -|\Z)",
                      text, re.S)
        self.assertIsNotNone(m, "khong tim thay lenh goi mod_large.py")
        invoked = set(re.findall(r"--([a-z][a-z0-9-]*)", m.group(1)))
        script = self.SCRIPT.read_text(encoding="utf-8")
        defined = set(re.findall(r"add_argument\(\"--([a-z][a-z0-9-]*)\"",
                                 script))
        defined.update(re.findall(r"add_argument\(\"--[a-z][a-z0-9-]*\", "
                                  r"\"--([a-z][a-z0-9-]*)\"", script))
        self.assertTrue(invoked, "khong goi flag nao")
        self.assertEqual(invoked - defined, set(),
                         "workflow goi flag khong co trong mod_large.py: %s"
                         % (invoked - defined,))
        for need in ("ipa", "original", "translation", "game", "out"):
            self.assertIn(need, invoked, "workflow thieu --%s" % need)

    def test_notebook_step4_mod_cell(self):
        nb = json.loads(self.NOTEBOOK.read_text(encoding="utf-8"))
        code_cells = ["".join(c["source"]) for c in nb["cells"]
                      if c["cell_type"] == "code"]
        mod_cells = [s for s in code_cells if "run_mod_job" in s]
        self.assertEqual(len(mod_cells), 1,
                         "phai co dung 1 cell Buoc 4 goi run_mod_job")
        src = mod_cells[0]
        self.assertIn("ENGINE", src)
        self.assertIn("run_mod_job", src)
        job_cells = [s for s in code_cells if "resolve_job" in s]
        self.assertEqual(len(job_cells), 1)

    def test_mod_large_help(self):
        r = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--gate-only", r.stdout)
        self.assertIn("--engine", r.stdout)
        self.assertIn("--display-font", r.stdout)


if __name__ == "__main__":
    unittest.main()
