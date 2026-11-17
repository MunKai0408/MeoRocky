"""Test planner/executor plan JSON (muc 8, sdk/plan.py).

Fixture tu dung bang builder CO SAN trong repo
(tests/fixtures/unity/build_fixtures.py): bundle UnityFS v7 LZ4, 1 CAB
SerializedFile v17, 3 TextAsset JSON tieng Anh (en.json / menu.json /
dialog.json, key khac nhau theo file). Khong dung file game that, khong
can mang.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)

from core.limits import Limits  # noqa: E402
from sdk import plan as pl  # noqa: E402
from sdk import unity as u  # noqa: E402

EN = {"hello": "Hello", "play": "Play", "quit": "Quit",
      "title": "9 Kings"}
MENU = {"start": "Start Game", "options": "Options", "exit": "Exit"}
DIALOG = {"greeting": "Welcome, hero!", "farewell": "Goodbye!"}
VI = {"hello": "Xin chào", "play": "Chơi", "quit": "Thoát",
      "start": "Bắt đầu chơi", "options": "Tùy chọn", "exit": "Thoát game",
      "greeting": "Chào mừng, anh hùng!", "farewell": "Tạm biệt!"}
CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _bundle_bytes():
    sf = build_serialized_file(
        version=17,
        objects=[(1001, 0, text_asset_data(
                      "en.json", json.dumps(EN, ensure_ascii=False))),
                 (1002, 0, text_asset_data(
                      "menu.json", json.dumps(MENU, ensure_ascii=False))),
                 (1003, 0, text_asset_data(
                      "dialog.json", json.dumps(DIALOG, ensure_ascii=False)))],
        types=[(49, None)])
    return build_bundle(version=7, entries=[(CAB, 4, sf)],
                        compression="lz4")


def _scripts(bundle_bytes):
    be = u.UnityBackend("NATIVE")
    b = u.parse_bundle(bundle_bytes, be, Limits())
    sf = u.parse_serialized(b.get_entry(CAB), Limits(), where=CAB)
    return {n: s for _p, n, s in sf.text_assets()}


class TestBuildPlan(unittest.TestCase):
    def test_build_three_entries_model(self):
        p = pl.build_plan(_bundle_bytes(), VI, source_bundle="game.bundle",
                          entry=CAB)
        self.assertEqual(p["version"], 1)
        self.assertEqual(p["source_bundle"], "game.bundle")
        self.assertEqual(len(p["entries"]), 3)
        self.assertEqual(p["skipped"], [])
        for e in p["entries"]:
            for f in ("id", "asset_path", "asset_type", "op", "object",
                      "changes", "preview_before", "preview_after",
                      "before_sha", "after_sha", "selected"):
                self.assertIn(f, e, f)
            self.assertEqual(e["asset_type"], "TextAsset")
            self.assertEqual(e["op"], "REBUILD_SECTION")
            self.assertTrue(e["selected"])
            self.assertEqual(e["before_sha"],
                             hashlib.sha256(
                                 _scripts(_bundle_bytes())[
                                     e["asset_path"].rsplit("::", 1)[-1]
                                 ].encode("utf-8")).hexdigest())
        by_name = {e["asset_path"].rsplit("::", 1)[-1]: e
                   for e in p["entries"]}
        self.assertEqual(len(by_name["en.json"]["changes"]), 3)
        self.assertEqual(by_name["en.json"]["preview_after"]["hello"],
                         "Xin chào")
        self.assertEqual(by_name["menu.json"]["changes"][0]["key"], "exit")
        # after_sha phai la mo phong dung apply_patch (replace lan 1).
        self.assertEqual(
            by_name["dialog.json"]["after_sha"],
            hashlib.sha256(json.dumps(
                {"greeting": "Chào mừng, anh hùng!",
                 "farewell": "Tạm biệt!"}, ensure_ascii=False).encode()
            ).hexdigest())

    def test_build_asset_filter_and_skipped(self):
        p = pl.build_plan(_bundle_bytes(), {"hello": "Xin chào"},
                          source_bundle="game.bundle", entry=CAB,
                          asset_names=["en.json"])
        self.assertEqual(len(p["entries"]), 1)
        self.assertEqual(p["entries"][0]["object"], 1001)
        self.assertEqual(len(p["skipped"]), 2)  # menu + dialog bi loai

    def test_build_no_match_raises(self):
        with self.assertRaises(ValueError):
            pl.build_plan(_bundle_bytes(), {"nope": "Khong co"},
                          source_bundle="game.bundle", entry=CAB)

    def test_build_empty_translations_raises(self):
        with self.assertRaises(ValueError):
            pl.build_plan(_bundle_bytes(), {}, source_bundle="game.bundle",
                          entry=CAB)

    def test_placeholder_warning(self):
        p = pl.build_plan(_bundle_bytes(),
                          {"hello": "Xin chào {0}", "play": "Chơi"},
                          source_bundle="game.bundle", entry=CAB)
        en = [e for e in p["entries"] if e["object"] == 1001][0]
        self.assertTrue(any("placeholder" in w for w in en["warnings"]))

    def test_validate_rejects_bad_version_and_op(self):
        p = pl.build_plan(_bundle_bytes(), VI, source_bundle="game.bundle",
                          entry=CAB)
        bad = copy.deepcopy(p)
        bad["version"] = 99
        with self.assertRaises(ValueError):
            pl.validate_plan(bad)
        bad = copy.deepcopy(p)
        bad["entries"][0]["op"] = "NOPE"
        with self.assertRaises(ValueError):
            pl.validate_plan(bad)


class TestApplyPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan_test_")
        self.bundle = _bundle_bytes()
        self.plan = pl.build_plan(self.bundle, VI, source_bundle="game.bundle",
                                  entry=CAB)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_changes_nothing(self):
        r = pl.apply_plan(self.bundle, copy.deepcopy(self.plan),
                          dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "done")
        self.assertEqual(len(r["applied"]), 3)
        self.assertEqual(r["outputs"], [])
        self.assertEqual(_scripts(self.bundle), {
            "en.json": json.dumps(EN, ensure_ascii=False),
            "menu.json": json.dumps(MENU, ensure_ascii=False),
            "dialog.json": json.dumps(DIALOG, ensure_ascii=False)})

    def test_apply_selected_only(self):
        plan = copy.deepcopy(self.plan)
        # Bo tick dialog.json -> chi en + menu doi.
        for e in plan["entries"]:
            if e["object"] == 1003:
                e["selected"] = False
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(self.bundle, plan, dry_run=False,
                              backup_root=os.path.join(self.tmp, "bak"))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["applied"]), 2)
        self.assertEqual(len(r["outputs"]), 1)
        got = _scripts(open(os.path.join(out_dir, r["outputs"][0]),
                            "rb").read())
        self.assertEqual(json.loads(got["en.json"])["hello"], "Xin chào")
        self.assertEqual(json.loads(got["menu.json"])["start"], "Bắt đầu chơi")
        # dialog.json GIU NGUYEN.
        self.assertEqual(json.loads(got["dialog.json"]), DIALOG)

    def test_rebuild_all_ignores_selection(self):
        plan = copy.deepcopy(self.plan)
        for e in plan["entries"]:
            e["selected"] = False
        plan["rebuild_all"] = True
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(self.bundle, plan, dry_run=False,
                              backup_root=os.path.join(self.tmp, "bak"))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["applied"]), 3)

    def test_rebuild_all_op_refused(self):
        plan = copy.deepcopy(self.plan)
        plan["entries"][0]["op"] = "REBUILD_ALL"
        r = pl.apply_plan(self.bundle, plan, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["applied"]), 2)
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(r["errors"][0]["code"], "E_WRITE_DISABLED")
        self.assertIn("M4", r["errors"][0]["message"])

    def test_resume_after_stop(self):
        plan = copy.deepcopy(self.plan)
        bak = os.path.join(self.tmp, "bak")
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r1 = pl.apply_plan(self.bundle, plan, dry_run=False,
                               stop_after=1, backup_root=bak)
        self.assertEqual(r1["status"], "partial")
        self.assertEqual(len(r1["applied"]), 1)
        self.assertEqual(len(r1["remaining"]), 2)
        ck = json.dumps(r1["checkpoint"], ensure_ascii=False)
        # Doc ngay output cua lan 1 TRUOC khi lan resume ghi de cung ten file.
        r1_bytes = _scripts(open(os.path.join(out_dir, r1["outputs"][0]),
                                 "rb").read())
        # Resume: phai ap not 2 entry con lai, khong ap lai entry dau.
        with _patched_outdir(out_dir):
            r2 = pl.apply_plan(self.bundle, plan, dry_run=False,
                               checkpoint_json=ck, backup_root=bak)
        self.assertEqual(r2["status"], "done")
        self.assertEqual(len(r2["applied"]), 2)
        self.assertEqual(sorted(r2["checkpoint"]["completed"]),
                         sorted(e["id"] for e in plan["entries"]))
        got = _scripts(open(os.path.join(out_dir, r2["outputs"][0]),
                            "rb").read())
        # Hop 2 lan resume: entry dau tu r1 + 2 entry sau tu r2 phai bang
        # ap day du (so sanh noi dung TextAsset).
        with _patched_outdir(out_dir):
            full = pl.apply_plan(self.bundle, copy.deepcopy(self.plan),
                                 dry_run=False, backup_root=bak)
        full_bytes = _scripts(open(os.path.join(out_dir, full["outputs"][0]),
                                   "rb").read())
        merged = {"en.json": r1_bytes["en.json"],
                  "menu.json": got["menu.json"],
                  "dialog.json": got["dialog.json"]}
        self.assertEqual(merged["en.json"], full_bytes["en.json"])
        self.assertEqual(merged["menu.json"], full_bytes["menu.json"])
        self.assertEqual(merged["dialog.json"], full_bytes["dialog.json"])

    def test_undo_from_backup(self):
        plan = copy.deepcopy(self.plan)
        bak = os.path.join(self.tmp, "bak")
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(self.bundle, plan, dry_run=False,
                              backup_root=bak)
        patched_bytes = open(os.path.join(out_dir, r["outputs"][0]),
                             "rb").read()
        # Undo tren file DA AP plan (dau vao la file da patch). Undo phai
        # chay trong cung _patched_outdir de output ve dung tmp cua test.
        with _patched_outdir(out_dir):
            undo = pl.undo_plan(patched_bytes, copy.deepcopy(self.plan),
                                backup_root=bak)
        self.assertTrue(undo["ok"], undo["errors"])
        self.assertEqual(len(undo["restored"]), 3)
        self.assertEqual(undo["restored"][0]["how"], "backup")
        restored = _scripts(open(os.path.join(out_dir, undo["outputs"][0]),
                                 "rb").read())
        self.assertEqual(restored, {
            "en.json": json.dumps(EN, ensure_ascii=False),
            "menu.json": json.dumps(MENU, ensure_ascii=False),
            "dialog.json": json.dumps(DIALOG, ensure_ascii=False)})

    def test_undo_only_selected_entries(self):
        """Undo chi hoan tac entry DA CHON khi ap: entry bi bo tick chua
        tung duoc ap nen patch nguoc no se loi 'khong tim thay chuoi cu'.
        (Loi that tim duoc khi chay harness muc 8.)"""
        plan = copy.deepcopy(self.plan)
        for e in plan["entries"]:
            if e["object"] == 1003:
                e["selected"] = False
        bak = os.path.join(self.tmp, "bak")
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(self.bundle, plan, dry_run=False,
                              backup_root=bak)
        self.assertEqual(len(r["applied"]), 2)
        patched_bytes = open(os.path.join(out_dir, r["outputs"][0]),
                             "rb").read()
        import shutil
        shutil.rmtree(bak, ignore_errors=True)  # ep patch nguoc
        with _patched_outdir(out_dir):
            undo = pl.undo_plan(patched_bytes, copy.deepcopy(plan),
                                backup_root=bak)
        self.assertTrue(undo["ok"], undo["errors"])
        self.assertEqual(len(undo["restored"]), 2)
        self.assertEqual(undo["restored"][0]["how"], "reverse-patch")
        restored = _scripts(open(os.path.join(out_dir, undo["outputs"][0]),
                                 "rb").read())
        self.assertEqual(json.loads(restored["en.json"]), EN)
        self.assertEqual(json.loads(restored["menu.json"]), MENU)
        self.assertEqual(json.loads(restored["dialog.json"]), DIALOG)

    def test_undo_reverse_patch_without_backup(self):
        plan = copy.deepcopy(self.plan)
        bak = os.path.join(self.tmp, "bak")
        out_dir = os.path.join(self.tmp, "out")
        with _patched_outdir(out_dir):
            r = pl.apply_plan(self.bundle, plan, dry_run=False,
                              backup_root=bak)
        patched_bytes = open(os.path.join(out_dir, r["outputs"][0]),
                             "rb").read()
        # Xoa backup -> undo phai dung patch nguoc, van verify before_sha.
        import shutil
        shutil.rmtree(bak, ignore_errors=True)
        with _patched_outdir(out_dir):
            undo = pl.undo_plan(patched_bytes, copy.deepcopy(self.plan),
                                backup_root=bak)
        self.assertTrue(undo["ok"], undo["errors"])
        self.assertEqual(undo["restored"][0]["how"], "reverse-patch")
        restored = _scripts(open(os.path.join(out_dir, undo["outputs"][0]),
                                 "rb").read())
        self.assertEqual(json.loads(restored["en.json"]), EN)


class _patched_outdir:
    """Tam doi PLAN_OUT_DIR/PLAN_ZIP_PATH cua sdk.plan ve tmp cua test
    (de tranh ghi /tmp/plan_out chung khi chay song song)."""

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


class TestKeyAnchoredApply(unittest.TestCase):
    """P0-1: ap change theo KEY (span cua literal gia tri), khong phai
    thay chuoi tran xuat hien dau tien. Bao phu cac truong hop du lieu
    that gap o 9 Kings resources.assets (gia tri rong, key trung o
    nhieu asset, gia tri trung nhau)."""

    def _apply_script(self, script, translations):
        sf = build_serialized_file(
            version=17,
            objects=[(1001, 0, text_asset_data("t.json", script))],
            types=[(49, None)])
        plan = pl.build_plan(sf, translations, source_bundle="t.assets",
                             entry=None, mode="REBUILD_SECTION")
        self.assertEqual(len(plan["entries"]), 1)
        entry = plan["entries"][0]
        patched, _mode = pl._apply_entry_changes(
            "assets", sf, None, entry, u.UnityBackend("NATIVE"), Limits())
        sf2 = u.parse_serialized(patched, Limits(), where="patched")
        _pid, _name, out = sf2.find_text_asset(path_id=1001)
        return out

    def test_empty_before_value(self):
        out = self._apply_script('{"Version": "", "name": "X"}',
                                 {"Version": "[VI]"})
        self.assertEqual(json.loads(out), {"Version": "[VI]", "name": "X"})
        self.assertEqual(out, '{"Version": "[VI]", "name": "X"}')

    def test_duplicate_values_across_keys(self):
        out = self._apply_script('{"a": "Same", "b": "Same"}',
                                 {"a": "A2", "b": "B2"})
        self.assertEqual(json.loads(out), {"a": "A2", "b": "B2"})

    def test_value_appearing_earlier_elsewhere(self):
        # "Hi" xuat hien truoc trong value cua key khac — ban cu thay
        # nham cho do; ban moi chi doi dung key dich.
        out = self._apply_script('{"note": "Say Hi", "greeting": "Hi"}',
                                 {"greeting": "Xin chao"})
        self.assertEqual(json.loads(out),
                         {"note": "Say Hi", "greeting": "Xin chao"})

    def test_duplicate_json_key_last_wins(self):
        out = self._apply_script('{"k": "first", "k": "second"}',
                                 {"k": "NEW"})
        self.assertEqual(json.loads(out), {"k": "NEW"})
        self.assertIn('"first"', out)  # lan xuat hien dau giu nguyen

    def test_indent_and_escapes_preserved(self):
        script = '{\n    "a": "caf\\u00e9",\n    "b": "x"\n}'
        out = self._apply_script(script, {"b": "y"})
        self.assertEqual(out,
                         '{\n    "a": "caf\\u00e9",\n    "b": "y"\n}')


if __name__ == "__main__":
    unittest.main()
