"""Test duong ghi TextAsset CSV (M3, sdk/csvutil.py + plan/rebuild/gate).

Fixture tu dung (khong dung file game that, khong can mang):
- CSV kieu PB3_LOC_DEFAULT.csv (header Key,English,Notes,...; gia tri co
  dau phay/quote, placeholder {0}, dong trong, key trung).
- ZIP gia (fake IPA) chua file CSV doc lap + bundle co TextAsset CSV.
- Bundle fixture bang builder co san (tests/fixtures/unity/build_fixtures.py).
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
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

from core.limits import Limits  # noqa: E402
from sdk import csvutil  # noqa: E402
from sdk import gate  # noqa: E402
from sdk import plan as pl  # noqa: E402
from sdk import rebuild as rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from tests.helpers import TmpCase  # noqa: E402

CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CSV_PATH = "Payload/ipa.store.app/Data/Raw/Localization/PB3_LOC_DEFAULT.csv"

# CSV kieu PB3: header, cot Notes bi bo, gia tri co dau phay + quote,
# placeholder {0}, 1 dong trong, 1 key trung (lay ban dau).
CSV = (
    b"Key,English,Notes,French,Chinese (Simplified)\n"
    b"hello,Hello,,Bonjour,\xe4\xbd\xa0\xe5\xa5\xbd\n"
    b"play,Play,note here,Jouer,\n"
    b"styled,Style {0} of {1},,Style {0} sur {1},\n"
    b"quoted,\"New, Game\",,\"Nouveau, jeu\",\n"
    b"empty,,,\n"
    b"hello,Hello dup,,Bonjour dup,\n"
)
CSV_VI = {"hello": "Xin chào", "play": "Chơi",
          "styled": "Kiểu {0} trên {1}", "quoted": "Trò chơi mới, hay nhất"}
# Ban dich cho gate: them "empty": "" (goc rong + dich rong duoc mien
# o completeness — khong thi gate FAIL vi thieu key).
CSV_VI_GATE = dict(CSV_VI, empty="")
CSV_EN = {"hello": "Hello", "play": "Play", "styled": "Style {0} of {1}",
          "quoted": "New, Game", "empty": ""}
CSV_NO_EN = b"Key,French\nhello,Bonjour\n"


def _zip_with_csv(csv_bytes=CSV, extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(CSV_PATH, csv_bytes)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _bundle_with_csv_text_asset(csv_text, json_obj=None):
    objs = [(1001, 0, text_asset_data("loc.csv", csv_text))]
    if json_obj is not None:
        objs.append((1002, 0, text_asset_data(
            "en.json", json.dumps(json_obj, ensure_ascii=False))))
    sf = build_serialized_file(version=17, objects=objs, types=[(49, None)])
    return build_bundle(version=7, entries=[(CAB, 4, sf)],
                        compression="lz4")


def _full_display():
    return gate.font_entry_from_bytes(
        font_fx.build_font_full_vietnamese(), "DonorFont.ttf")


class _patched_outdir:
    """Tam doi PLAN_OUT_DIR/PLAN_ZIP_PATH cua sdk.plan ve tmp cua test
    (nhu test_plan.py — tham so out_dir cua apply_plan khong duoc
    _write_outputs dung, chi co patch hang module moi doi duoc)."""

    def __init__(self, out_dir):
        self.out_dir = out_dir

    def __enter__(self):
        self.old_out = pl.PLAN_OUT_DIR
        self.old_zip = pl.PLAN_ZIP_PATH
        pl.PLAN_OUT_DIR = self.out_dir
        pl.PLAN_ZIP_PATH = os.path.join(
            os.path.dirname(self.out_dir), "plan.zip")
        return self

    def __exit__(self, *exc):
        pl.PLAN_OUT_DIR = self.old_out
        pl.PLAN_ZIP_PATH = self.old_zip
        return False


class TestCsvUtil(unittest.TestCase):
    def test_is_localization_csv(self):
        self.assertTrue(csvutil.is_localization_csv(CSV, "PB3_LOC_DEFAULT.csv"))
        self.assertTrue(csvutil.is_localization_csv(CSV, "loc"))  # co header
        # File CSV 1 dong key,value van la bang CSV hop le (theo ten).
        self.assertTrue(csvutil.is_localization_csv(
            b"Hello, world\n", "a.csv"))
        # Ten khong co duoi .csv nhung noi dung co header -> nhan theo
        # noi dung (TextAsset trong bundle cung di duong nay).
        self.assertTrue(csvutil.is_localization_csv(CSV, "a.txt"))
        self.assertFalse(csvutil.is_localization_csv(
            b'{"hello": "Hello"}', "a.csv"))
        self.assertFalse(csvutil.is_localization_csv(b"\x00\x01\x02", "a.csv"))
        # Text thuong khong header, khong duoi .csv -> khong nhan.
        self.assertFalse(csvutil.is_localization_csv(
            b"Hello world, this is plain text\nSecond line here\n", "a.txt"))
        self.assertFalse(csvutil.is_localization_csv(b"", "a.csv"))

    def test_source_map_first_wins_and_cells(self):
        mapping, dups, eff = csvutil.source_map(CSV, "PB3_LOC_DEFAULT.csv", "en")
        self.assertEqual(eff, "en")
        self.assertEqual(len(mapping), 5)  # hello, play, styled, quoted, empty
        self.assertEqual(mapping["hello"]["value"], "Hello")
        self.assertEqual(mapping["hello"]["cell"], "csv:0:1")
        self.assertEqual(mapping["quoted"]["value"], "New, Game")
        self.assertEqual(mapping["empty"]["value"], "")
        self.assertIn("hello", dups)  # key trung: ban dau thang

    def test_source_map_no_source_column(self):
        mapping, _d, _e = csvutil.source_map(CSV_NO_EN, "a.csv", "en")
        self.assertEqual(mapping, {})

    def test_simulate_keeps_header_order_and_other_cells(self):
        changes = [{"key": "hello", "before": "Hello", "after": "Xin chào",
                    "cell": "csv:0:1"},
                   {"key": "quoted", "before": "New, Game",
                    "after": "Trò chơi mới, hay nhất", "cell": "csv:3:1"}]
        out = csvutil.simulate_csv(CSV, "PB3_LOC_DEFAULT.csv", changes, "en")
        self.assertTrue(out.startswith(
            b"Key,English,Notes,French,Chinese (Simplified)\n"))
        m2, _, _ = csvutil.source_map(out, "PB3_LOC_DEFAULT.csv", "en")
        self.assertEqual(m2["hello"]["value"], "Xin chào")
        self.assertEqual(m2["quoted"]["value"], "Trò chơi mới, hay nhất")
        self.assertEqual(m2["play"]["value"], "Play")  # o khac giu nguyen
        self.assertEqual(m2["styled"]["value"], "Style {0} of {1}")
        self.assertEqual([k for k in m2],
                         ["hello", "play", "styled", "quoted", "empty"])
        ok, details = csvutil.verify_csv_values(
            CSV, out, "PB3_LOC_DEFAULT.csv", changes, "en")
        self.assertTrue(ok, details)

    def test_simulate_rejects_before_mismatch(self):
        with self.assertRaises(ValueError):
            csvutil.simulate_csv(CSV, "PB3_LOC_DEFAULT.csv",
                                 [{"key": "hello", "before": "Hi",
                                   "after": "Xin chào", "cell": "csv:0:1"}],
                                 "en")

    def test_verify_catches_extra_change(self):
        changes = [{"key": "hello", "before": "Hello", "after": "Xin chào",
                    "cell": "csv:0:1"}]
        out = csvutil.simulate_csv(CSV, "PB3_LOC_DEFAULT.csv", changes, "en")
        # Gia mo them 1 o ngoai ke hoach.
        tampered = out.replace(b"Play", b"Play!", 1)
        ok, details = csvutil.verify_csv_values(
            CSV, tampered, "PB3_LOC_DEFAULT.csv", changes, "en")
        self.assertFalse(ok)
        self.assertTrue(any("play" in d for d in details))


class TestBuildPlanCsv(unittest.TestCase):
    def test_standalone_csv_in_zip(self):
        z = _zip_with_csv()
        p = pl.build_plan(z, CSV_VI, source_bundle="game.ipa", entry=CSV_PATH,
                          source_locale="en")
        self.assertEqual(p["unit_kind"], "csv")
        self.assertEqual(len(p["entries"]), 1)
        e = p["entries"][0]
        self.assertEqual(e["asset_type"], "TextAsset")
        self.assertEqual(e.get("format"), "csv")
        self.assertEqual(e["kind"], "csv")
        self.assertEqual(e["object"], 0)
        self.assertEqual(len(e["changes"]), 4)
        self.assertTrue(all(c.get("cell", "").startswith("csv:")
                            for c in e["changes"]))
        self.assertEqual(e["before_sha"], hashlib.sha256(CSV).hexdigest())
        # placeholder {0}/{1} giu nguyen -> khong warning placeholder.
        styled = [c for c in e["changes"] if c["key"] == "styled"][0]
        self.assertEqual(styled["after"], "Kiểu {0} trên {1}")
        self.assertEqual(e["warnings"], [])

    def test_standalone_csv_bytes_direct(self):
        p = pl.build_plan(CSV, CSV_VI, source_bundle="PB3_LOC_DEFAULT.csv",
                          source_locale="en")
        self.assertEqual(p["unit_kind"], "csv")
        self.assertEqual(len(p["entries"]), 1)

    def test_csv_without_source_column_raises(self):
        z = _zip_with_csv(CSV_NO_EN)
        with self.assertRaises(ValueError):
            pl.build_plan(z, {"hello": "Xin chào"},
                          source_bundle="game.ipa", entry=CSV_PATH,
                          source_locale="en")

    def test_text_asset_csv_inside_bundle(self):
        csv_text = CSV.decode("utf-8")
        bundle = _bundle_with_csv_text_asset(
            csv_text, json_obj={"title": "9 Kings"})
        p = pl.build_plan(bundle, dict(CSV_VI, title="9 Vua"),
                          source_bundle="game.bundle", entry=CAB,
                          source_locale="en")
        self.assertEqual(len(p["entries"]), 2)
        by_format = {e.get("format", "json"): e for e in p["entries"]}
        self.assertIn("csv", by_format)
        csv_e = by_format["csv"]
        self.assertEqual(csv_e["object"], 1001)
        self.assertEqual(csv_e["kind"], "bundle")
        self.assertEqual(len(csv_e["changes"]), 4)
        self.assertEqual(by_format["json"]["object"], 1002)


class TestApplyCsv(TmpCase):
    def test_rebuild_dry_run_and_write_zip_csv(self):
        z = _zip_with_csv()
        res = rb.apply_rebuild(z, source_name="game.ipa", translations=CSV_VI,
                               dry_run=True, source_locale="en")
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["units"]), 1)
        unit = res["units"][0]
        self.assertEqual(unit["kind"], "csv")
        self.assertEqual(unit["changes"], 4)
        self.assertEqual(res.get("unmatched_total"), 0, res.get("unmatched"))
        out_dir = os.path.join(self.tmp, "out")
        res2 = rb.apply_rebuild(z, source_name="game.ipa",
                                translations=CSV_VI, dry_run=False,
                                confirm=True, source_locale="en",
                                out_dir=out_dir,
                                zip_path=os.path.join(self.tmp, "out.zip"))
        self.assertEqual(res2["outputs"], ["PB3_LOC_DEFAULT.csv"])
        dest = res2["dest"]
        self.assertTrue(dest.endswith("PB3_LOC_DEFAULT.csv"))
        new_csv = open(dest, "rb").read()
        m2, _, _ = csvutil.source_map(new_csv, "PB3_LOC_DEFAULT.csv", "en")
        for k, v in CSV_VI.items():
            self.assertEqual(m2[k]["value"], v, k)
        self.assertEqual(m2["empty"]["value"], "")
        self.assertTrue(new_csv.startswith(
            b"Key,English,Notes,French,Chinese (Simplified)\n"))

    def test_undo_reverse_patch_restores_csv(self):
        p = pl.build_plan(CSV, CSV_VI, source_bundle="PB3_LOC_DEFAULT.csv",
                          source_locale="en")
        bak = os.path.join(self.tmp, "bak")
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(CSV, copy.deepcopy(p), dry_run=False,
                              backup_root=bak)
        patched = open(os.path.join(out_dir, r["outputs"][0]), "rb").read()
        m2, _, _ = csvutil.source_map(patched, "PB3_LOC_DEFAULT.csv", "en")
        self.assertEqual(m2["hello"]["value"], "Xin chào")
        import shutil
        shutil.rmtree(bak, ignore_errors=True)  # ep patch nguoc
        with _patched_outdir(out_dir):
            undo = pl.undo_plan(patched, copy.deepcopy(p), backup_root=bak)
        self.assertTrue(undo["ok"], undo["errors"])
        self.assertEqual(undo["restored"][0]["how"], "reverse-patch")
        restored = open(os.path.join(out_dir, undo["outputs"][0]),
                        "rb").read()
        self.assertEqual(restored, CSV)


class TestGateCsv(unittest.TestCase):
    def test_gate_pass_csv_zip(self):
        z = _zip_with_csv()
        res = gate.run_gate(z, source_name="game.ipa", translations=CSV_VI_GATE,
                            source_texts=CSV_EN, source_locale="en",
                            display_fonts=[_full_display()])
        self.assertEqual(res["verdict"], "PASS", res)
        self.assertTrue(res["delivery_allowed"])
        self.assertEqual(res["totals"]["changed"], 4)

    def test_gate_pressed_csv_file(self):
        z = _zip_with_csv()
        out = csvutil.simulate_csv(
            CSV, "PB3_LOC_DEFAULT.csv",
            [{"key": k, "before": CSV_EN[k], "after": v,
              "cell": csvutil.source_map(CSV, "PB3_LOC_DEFAULT.csv", "en")[0][k]["cell"]}
             for k, v in CSV_VI.items()], "en")
        res = gate.run_gate(z, source_name="game.ipa", translations=CSV_VI_GATE,
                            source_texts=CSV_EN, source_locale="en",
                            pressed_bytes=out, pressed_name="PB3_LOC_DEFAULT.csv",
                            display_fonts=[_full_display()])
        self.assertEqual(res["verdict"], "PASS", res)


if __name__ == "__main__":
    unittest.main()
