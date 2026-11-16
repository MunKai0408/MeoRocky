"""Test phần (D): liệt kê font + gom về 1 font đủ dấu (sdk/font_list.py).

Fixture TTF tổng hợp (tests/fixtures/font/build_fixtures.py) + SerializedFile
TMP tổng hợp (tests/test_tmp_font.py::_sf_with_assets). Không dùng file
game thật, không cần mạng. Bộ 142 mục lấy từ sdk/font.REQUIRED_VIETNAMESE.
"""
from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path

from sdk import font as font_sdk
from sdk import font_list as fl
from sdk import tmp_font as tf
from tests.fixtures.font import build_fixtures as fx
from tests.helpers import TmpCase
from tests.test_tmp_font import _sf_with_assets

ROOT = Path(__file__).resolve().parent.parent


class TestStandaloneFonts(TmpCase):
    def _dir_with(self, files):
        d = self.tmp / "game"
        d.mkdir()
        for name, data in files.items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return d

    def test_partial_ttf_reports_exact_missing(self):
        missing = [0x1EA0, 0x1EA1, 0x300, 0x301]  # Ạ ạ + huyền/sắc
        have = [c for c in fx.vietnamese_cps() if c not in missing]
        data = fx.build_ttf(fx.ascii_cps() + have, name="GameFont")
        scan = fl.scan_source(str(self._dir_with({"GameFont.ttf": data})))
        self.assertEqual(scan["summary"]["fonts"], 1)
        fnt = scan["fonts"][0]
        self.assertEqual(fnt["kind"], "ttf")
        self.assertEqual(fnt["status"], "partial")
        self.assertEqual(fnt["covered"], 142 - len(missing))
        self.assertEqual(fnt["missing_codepoints"], sorted(missing))
        self.assertEqual(fnt["missing_total"], len(missing))

    def test_full_ttf_is_full(self):
        data = fx.build_font_full_vietnamese()
        scan = fl.scan_source(str(self._dir_with({"Full.ttf": data})))
        fnt = scan["fonts"][0]
        self.assertEqual(fnt["status"], "full")
        self.assertEqual(fnt["covered"], 142)
        self.assertEqual(fnt["missing_total"], 0)

    def test_otf_is_partial_but_not_mergeable(self):
        data = fx.build_otf_stub()
        scan = fl.scan_source(str(self._dir_with({"Game.otf": data})))
        fnt = scan["fonts"][0]
        self.assertEqual(fnt["kind"], "otf")
        self.assertEqual(fnt["status"], "partial")
        plan = fl.build_unify_plan(scan)
        self.assertEqual(plan["fonts"][0]["action"], "replace_with_donor")
        self.assertNotIn("output", plan["fonts"][0])

    def test_ttc_is_unsupported(self):
        data = fx.build_ttc_stub()
        scan = fl.scan_source(str(self._dir_with({"Game.ttc": data})))
        fnt = scan["fonts"][0]
        self.assertEqual(fnt["kind"], "ttc")
        self.assertEqual(fnt["status"], "unsupported")
        plan = fl.build_unify_plan(scan)
        self.assertEqual(plan["fonts"][0]["action"], "replace_with_donor")

    def test_zip_entry_ttf(self):
        zpath = self.tmp / "game.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("fonts/GameFont.ttf", fx.build_font_missing_vietnamese())
            zf.writestr("notes.txt", "hello")
        scan = fl.scan_source(str(zpath))
        self.assertEqual(scan["summary"]["fonts"], 1)
        self.assertEqual(scan["fonts"][0]["kind"], "ttf")
        self.assertEqual(scan["fonts"][0]["source"], "fonts/GameFont.ttf")

    def test_missing_source_raises(self):
        with self.assertRaises(Exception) as cm:
            fl.scan_source(str(self.tmp / "khong-co"))
        self.assertEqual(getattr(cm.exception, "code", None), "E_INPUT_NOT_FOUND")

    def test_unity_entry_names(self):
        self.assertTrue(fl._is_unity_entry("Payload/x.app/Data/level0"))
        self.assertTrue(fl._is_unity_entry("Payload/x.app/Data/sharedassets0"))
        self.assertTrue(fl._is_unity_entry("resources.assets"))
        self.assertFalse(fl._is_unity_entry("sharedassets0.assets.resS"))
        self.assertFalse(fl._is_unity_entry("sharedassets0.resource"))
        self.assertFalse(fl._is_unity_entry("notes.txt"))


class TestUnityFonts(TmpCase):
    def test_tmp_partial_and_embedded_font_listed(self):
        letters = tf.vietnamese_letters_134()
        ids = list(range(32, 127)) + letters[:100]  # thiếu 34 chữ + 8 dấu
        sf = _sf_with_assets([(100, "GameFont SDF", {"face": "GameFont", "ids": ids})])
        p = self.tmp / "resources.assets"
        p.write_bytes(sf)
        scan = fl.scan_source(str(p))
        self.assertEqual(len(scan["tmp_items"]), 1)
        self.assertIn("tmp_candidates", scan)
        by_kind = {}
        for fnt in scan["fonts"]:
            by_kind.setdefault(fnt["kind"], []).append(fnt)
        # 1 TMP + 1 Font object nhúng (pid 40 trong fixture _sf_with_assets).
        self.assertEqual(len(by_kind.get("tmp_font_asset", [])), 1)
        self.assertEqual(len(by_kind.get("unity_font", [])), 1)
        tmp = by_kind["tmp_font_asset"][0]
        self.assertEqual(tmp["status"], "partial")
        self.assertEqual(tmp["covered"], 100)
        self.assertEqual(tmp["total"], 142)
        self.assertEqual(tmp["missing_total"], 42)
        emb = by_kind["unity_font"][0]
        self.assertEqual(emb["status"], "unreadable")
        self.assertIsNone(emb["covered"])
        self.assertEqual(emb["path_id"], 40)
        plan = fl.build_unify_plan(scan)
        acts = {e["kind"]: e["action"] for e in plan["fonts"]}
        self.assertEqual(acts["tmp_font_asset"], "tmp_fallback")
        self.assertEqual(acts["unity_font"], "replace_with_donor")
        self.assertIsNotNone(plan["fallback_plan"])
        self.assertEqual(plan["fallback_plan"]["schema"], "tmp_fallback_plan/1")

    def test_tmp_full_needs_nothing(self):
        letters = tf.vietnamese_letters_134()
        marks = tf.combining_marks_8()
        ids = list(range(32, 127)) + letters + marks
        sf = _sf_with_assets([(100, "FullFont SDF", {"face": "FullFont", "ids": ids})])
        p = self.tmp / "resources.assets"
        p.write_bytes(sf)
        scan = fl.scan_source(str(p))
        tmp = [f for f in scan["fonts"] if f["kind"] == "tmp_font_asset"][0]
        self.assertEqual(tmp["status"], "full")
        self.assertEqual(tmp["covered"], 142)
        plan = fl.build_unify_plan(scan)
        tmp_plan = [e for e in plan["fonts"] if e["kind"] == "tmp_font_asset"][0]
        self.assertEqual(tmp_plan["action"], "none_full")

    def test_unity_fixture_without_fonts(self):
        fixture = ROOT / "tests" / "fixtures" / "unity" / "resources.assets"
        if not fixture.exists():
            self.skipTest("thiếu fixture unity/resources.assets")
        scan = fl.scan_source(str(fixture))
        self.assertEqual(scan["fonts"], [])
        self.assertEqual(scan["summary"]["fonts"], 0)


class TestUnifyPlan(TmpCase):
    def test_merge_action_and_real_merge(self):
        missing = [0x1EA0, 0x1EA1, 0x300]
        have = [c for c in fx.vietnamese_cps() if c not in missing]
        data = fx.build_ttf(fx.ascii_cps() + have, name="GameFont")
        d = self.tmp / "game"
        d.mkdir()
        (d / "GameFont.ttf").write_bytes(data)
        scan = fl.scan_source(str(d))
        plan = fl.build_unify_plan(scan)
        self.assertEqual(plan["schema"], "font_unify_plan/1")
        self.assertEqual(plan["donor"]["covered"], 142)
        entry = plan["fonts"][0]
        self.assertEqual(entry["action"], "merge_glyphs")
        self.assertEqual(entry["output"], "GameFont.vi.ttf")
        self.assertIn("Atlas SDF", " ".join(plan["limits"]))
        self.assertTrue(any("Unity Editor" in s for s in plan["editor_steps"]))
        merged = fl.merge_standalone_ttfs(scan, fx.build_font_full_vietnamese())
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["output"], "GameFont.vi.ttf")
        self.assertEqual(merged[0]["added"], len(missing))
        rep = font_sdk.coverage(merged[0]["data"], where="merged")
        self.assertTrue(rep.is_full)
        self.assertEqual(rep.covered, 142)

    def test_full_ttf_not_merged(self):
        d = self.tmp / "game"
        d.mkdir()
        (d / "Full.ttf").write_bytes(fx.build_font_full_vietnamese())
        scan = fl.scan_source(str(d))
        self.assertEqual(fl.merge_standalone_ttfs(
            scan, fx.build_font_full_vietnamese()), [])

    def test_plan_counts_and_union(self):
        d = self.tmp / "game"
        d.mkdir()
        (d / "A.ttf").write_bytes(fx.build_font_missing_vietnamese())
        (d / "B.ttf").write_bytes(fx.build_font_full_vietnamese())
        scan = fl.scan_source(str(d))
        plan = fl.build_unify_plan(scan)
        self.assertEqual(plan["counts"]["merge_glyphs"], 1)
        self.assertEqual(plan["counts"]["none_full"], 1)
        self.assertGreater(plan["missing_total_union"], 0)
        self.assertEqual(json.loads(json.dumps(plan))["schema"],
                         "font_unify_plan/1")


if __name__ == "__main__":
    unittest.main()
