"""Test patch pack nhi phan (muc C7, sdk/patch_pack.py).

Fixture tu dung bang builder CO SAN trong repo
(tests/fixtures/unity/build_fixtures.py): bundle UnityFS v7 LZ4, 1 CAB
SerializedFile v17, TextAsset JSON tieng Anh. Khong dung file game that,
khong can mang. Script ap duoc chay that bang subprocess (Python he
thong) tren ban sao fixture trong thu muc tam.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "unity"))
from build_fixtures import (  # noqa: E402
    build_bundle, build_serialized_file, text_asset_data)

from core.limits import Limits  # noqa: E402
from sdk import patch_pack as pp  # noqa: E402
from sdk import plan as pl  # noqa: E402
from sdk import unity as u  # noqa: E402

EN = {"hello": "Hello", "play": "Play", "quit": "Quit"}
VI = {"hello": "Xin chào", "play": "Chơi", "quit": "Thoát"}
CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _bundle_bytes(version=7, scripts=None):
    scripts = scripts or EN
    sf = build_serialized_file(
        version=17,
        objects=[(1001, 0, text_asset_data(
                      "en.json", json.dumps(scripts, ensure_ascii=False)))],
        types=[(49, None)])
    return build_bundle(version=version, entries=[(CAB, 4, sf)],
                        compression="lz4")


def _plan(bundle_bytes):
    return pl.build_plan(bundle_bytes, VI, source_bundle="test.unity3d",
                         entry=CAB)


def _script_of(bundle_bytes):
    be = u.UnityBackend("NATIVE")
    b = u.parse_bundle(bundle_bytes, be, Limits())
    sf = u.parse_serialized(b.get_entry(CAB), Limits(), where=CAB)
    return {n: s for _p, n, s in sf.text_assets()}


class TestBuildPackFromPlan(unittest.TestCase):
    def test_manifest_schema_compatible_with_plan(self):
        bundle = _bundle_bytes()
        plan = _plan(bundle)
        man = pp.build_patch_pack(bundle, "test.unity3d", "plan", plan)
        self.assertEqual(man["format"], "pb3-patch-pack")
        self.assertEqual(man["pack_version"], 1)
        self.assertEqual(man["version"], 1)
        self.assertEqual(man["id"], plan["id"])
        self.assertEqual(man["source_file"], "test.unity3d")
        self.assertEqual(man["source_sha256"],
                         hashlib.sha256(bundle).hexdigest())
        self.assertEqual(man["apply_script"], "apply_patch.py")
        self.assertEqual(len(man["entries"]), 1)
        e = man["entries"][0]
        self.assertEqual(e["asset_path"], plan["entries"][0]["asset_path"])
        self.assertEqual(e["asset_type"], "TextAsset")
        self.assertEqual(e["operation"], "replace_text")
        self.assertEqual(e["op"], "REBUILD_SECTION")
        self.assertEqual(e["object"], 1001)
        self.assertEqual(e["before_sha"], plan["entries"][0]["before_sha"])
        self.assertEqual(e["after_sha"], plan["entries"][0]["after_sha"])
        self.assertEqual(e["preview_before"], {"hello": "Hello",
                                               "play": "Play",
                                               "quit": "Quit"})
        self.assertEqual(e["preview_after"], {"hello": "Xin chào",
                                              "play": "Chơi",
                                              "quit": "Thoát"})
        self.assertTrue(e["selected"])

    def test_after_sha_matches_real_apply(self):
        # after_sha trong pack phai la ket qua apply_patch that, khong
        # phai mo phong: ap lai entry roi doi chieu script.
        bundle = _bundle_bytes()
        man = pp.build_patch_pack(bundle, "test.unity3d", "plan",
                                  _plan(bundle))
        e = man["entries"][0]
        cur = bundle
        be = u.UnityBackend("NATIVE")
        for ch in e["changes"]:
            req = u.PatchRequest(data=cur, kind="bundle", entry=CAB,
                                 path_id=1001, old_text=ch["before"],
                                 new_text=ch["after"],
                                 write_mode=e["op"], backend=be,
                                 temp_dir=None)
            cur = u.apply_patch(req).output_bytes
        script = _script_of(cur)["en.json"]
        self.assertEqual(hashlib.sha256(script.encode("utf-8")).hexdigest(),
                         e["after_sha"])

    def test_unselected_entry_excluded(self):
        bundle = _bundle_bytes()
        plan = _plan(bundle)
        plan["entries"][0]["selected"] = False
        with self.assertRaises(ValueError):
            pp.build_patch_pack(bundle, "test.unity3d", "plan", plan)

    def test_rebuild_all_op_refused(self):
        bundle = _bundle_bytes()
        plan = _plan(bundle)
        plan["entries"][0]["op"] = "REBUILD_ALL"
        with self.assertRaises(ValueError) as cm:
            pp.build_patch_pack(bundle, "test.unity3d", "plan", plan)
        self.assertIn("M4", str(cm.exception))

    def test_before_sha_mismatch_refused(self):
        # Plan lap tu file A, pack lap tu file B (script khac) -> tu choi.
        bundle = _bundle_bytes()
        plan = _plan(bundle)
        other = _bundle_bytes(scripts={"hello": "Hello!", "play": "Play",
                                       "quit": "Quit"})
        with self.assertRaises(ValueError) as cm:
            pp.build_patch_pack(other, "test.unity3d", "plan", plan)
        self.assertIn("before_sha", str(cm.exception))

    def test_v9_bundle_builds_pack(self):
        # [ĐÃ ĐO THẬT] bundle v9 nay ghi được (M-formats): pack lập được.
        bundle9 = _bundle_bytes(version=9)
        plan = pl.build_plan(bundle9, VI, source_bundle="test.unity3d",
                             entry=CAB)
        man = pp.build_patch_pack(bundle9, "test.unity3d", "plan", plan)
        self.assertEqual(len(man["entries"]), 1)
        self.assertEqual(man["entries"][0]["after_sha"],
                         plan["entries"][0]["after_sha"])

    def test_bad_kind_refused(self):
        with self.assertRaises(ValueError):
            pp.build_patch_pack(_bundle_bytes(), "test.unity3d", "zip", {})

    def test_empty_value_change_builds_pack(self):
        # Hoi quy (sua-loi, cung loi plan.py P0-1 §4a): change co
        # before="" truoc day bi E_CONFIG_INVALID o apply_patch (gia tri
        # tran "" co 0 occurrence). Nay thay theo KEY nen pack duoc va
        # after_sha khop dung plan (khong can tinh lai).
        scripts = {"Version": "", "CreatedAt": "2020"}
        bundle = _bundle_bytes(scripts=scripts)
        plan = pl.build_plan(bundle, {"Version": "1.2.3",
                                      "CreatedAt": "2021"},
                             source_bundle="test.unity3d", entry=CAB)
        man = pp.build_patch_pack(bundle, "test.unity3d", "plan", plan)
        self.assertEqual(len(man["entries"]), 1)
        self.assertEqual(man["entries"][0]["after_sha"],
                         plan["entries"][0]["after_sha"])
        warnings = " ".join(man["entries"][0].get("warnings") or [])
        self.assertNotIn("after_sha tinh lai", warnings)


class TestBuildPackFromMod(unittest.TestCase):
    def test_mod_edits_become_entries(self):
        bundle = _bundle_bytes()
        edits = [{"object": 1001, "old": "Hello", "new": "Xin chào",
                  "entry": CAB, "mode": "REBUILD_SECTION"}]
        man = pp.build_patch_pack(bundle, "test.unity3d", "mod",
                                  json.dumps(edits))
        self.assertEqual(len(man["entries"]), 1)
        e = man["entries"][0]
        self.assertEqual(e["operation"], "replace_text")
        self.assertEqual(e["op"], "REBUILD_SECTION")
        self.assertEqual(e["changes"],
                         [{"key": "Hello", "before": "Hello",
                           "after": "Xin chào"}])
        self.assertEqual(e["preview_before"], {"Hello": "Hello"})
        self.assertEqual(e["preview_after"], {"Hello": "Xin chào"})
        # after_sha khop script sau khi ap that.
        cur = bundle
        be = u.UnityBackend("NATIVE")
        req = u.PatchRequest(data=cur, kind="bundle", entry=CAB,
                             path_id=1001, old_text="Hello",
                             new_text="Xin chào", write_mode="REBUILD_SECTION",
                             backend=be, temp_dir=None)
        cur = u.apply_patch(req).output_bytes
        script = _script_of(cur)["en.json"]
        self.assertEqual(hashlib.sha256(script.encode("utf-8")).hexdigest(),
                         e["after_sha"])

    def test_mod_two_edits_same_asset_sequential(self):
        bundle = _bundle_bytes()
        edits = [{"object": 1001, "old": "Hello", "new": "Xin chào",
                  "entry": CAB, "mode": "REBUILD_SECTION"},
                 {"object": 1001, "old": "Play", "new": "Chơi",
                  "entry": CAB, "mode": "REBUILD_SECTION"}]
        man = pp.build_patch_pack(bundle, "test.unity3d", "mod",
                                  json.dumps(edits))
        self.assertEqual(len(man["entries"]), 2)
        # Entry 2 phai thay before_sha = after_sha cua entry 1 (noi tiep).
        self.assertEqual(man["entries"][1]["before_sha"],
                         man["entries"][0]["after_sha"])

    def test_mod_missing_old_text_refused(self):
        bundle = _bundle_bytes()
        edits = [{"object": 1001, "old": "KhongCoChuoiNay",
                  "new": "X", "entry": CAB, "mode": "REBUILD_SECTION"}]
        with self.assertRaises(Exception):
            pp.build_patch_pack(bundle, "test.unity3d", "mod",
                                json.dumps(edits))

    def test_mod_bad_mode_refused(self):
        bundle = _bundle_bytes()
        edits = [{"object": 1001, "old": "Hello", "new": "X",
                  "entry": CAB, "mode": "REBUILD_ALL"}]
        with self.assertRaises(ValueError):
            pp.build_patch_pack(bundle, "test.unity3d", "mod",
                                json.dumps(edits))


class TestPackZipAndScript(unittest.TestCase):
    def test_zip_contains_manifest_script_readme(self):
        bundle = _bundle_bytes()
        man = pp.build_patch_pack(bundle, "test.unity3d", "plan",
                                  _plan(bundle))
        with tempfile.TemporaryDirectory() as td:
            zpath = os.path.join(td, "pack.zip")
            info = pp.make_pack_zip(man, zpath)
            self.assertEqual(info["files"], ["patch_manifest.json",
                                             "apply_patch.py",
                                             "README.txt"])
            with zipfile.ZipFile(zpath) as zf:
                back = json.loads(zf.read("patch_manifest.json"))
            self.assertEqual(back["id"], man["id"])
            self.assertEqual(back["entries"][0]["after_sha"],
                             man["entries"][0]["after_sha"])

    def test_apply_script_parses_and_is_stdlib_only(self):
        tree = ast.parse(pp.APPLY_SCRIPT)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        self.assertEqual(imports, {"__future__", "argparse", "hashlib",
                                   "json", "os", "shutil", "sys", "pathlib",
                                   # Nhap trong ham _load_pb3 / du phong ten
                                   # file output: chi cay PB3, khong mang.
                                   "core", "sdk"})
        self.assertFalse(imports & {"socket", "urllib", "http", "requests",
                                    "subprocess"})
        # Script khong chua chuoi cam theo luat iSH-only (quyet dinh
        # kiem tra ca chuoi template trong sdk/patch_pack.py).
        for bad in ("https://", "http://", "pip install", "curl ",
                    "wget ", "/home/", "/root/pb3", "android"):
            self.assertNotIn(bad, pp.APPLY_SCRIPT)
            self.assertNotIn(bad, pp.PACK_README)

    def _run_script(self, td, *argv, env_extra=None):
        env = dict(os.environ)
        env["PB3_ROOT"] = str(ROOT)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, os.path.join(td, "apply_patch.py")] + list(argv),
            cwd=td, env=env, capture_output=True, text=True, timeout=120)

    def _stage_pack(self, td, bundle, kind="plan", payload=None):
        if kind == "plan":
            man = pp.build_patch_pack(bundle, "test.unity3d", "plan",
                                      payload or _plan(bundle))
        else:
            man = pp.build_patch_pack(bundle, "test.unity3d", "mod", payload)
        pp.make_pack_zip(man, os.path.join(td, "pack.zip"))
        with zipfile.ZipFile(os.path.join(td, "pack.zip")) as zf:
            zf.extractall(td)
        Path(td, "test.unity3d").write_bytes(bundle)
        return man

    def test_script_dry_run_writes_nothing(self):
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            before = hashlib.sha256(bundle).hexdigest()
            r = self._run_script(td, "test.unity3d")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DRY-RUN", r.stdout)
            self.assertIn("khop before_sha/after_sha", r.stdout)
            self.assertEqual(hashlib.sha256(
                Path(td, "test.unity3d").read_bytes()).hexdigest(), before)
            self.assertFalse(Path(td, "test.pb3-patched").exists())

    def test_script_allow_write_patches_and_keeps_original(self):
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            man = self._stage_pack(td, bundle)
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("DA GHI", r.stdout)
            out_dir = Path(td, "test.pb3-patched")
            patched = out_dir / "test.unity3d"
            self.assertTrue(patched.is_file())
            script = _script_of(patched.read_bytes())["en.json"]
            self.assertEqual(json.loads(script), VI)
            self.assertEqual(
                hashlib.sha256(script.encode("utf-8")).hexdigest(),
                man["entries"][0]["after_sha"])
            # Ban sao luu goc dung byte goc; file goc khong doi.
            self.assertEqual((out_dir / "backup-goc" / "test.unity3d")
                             .read_bytes(), bundle)
            self.assertEqual(Path(td, "test.unity3d").read_bytes(), bundle)

    def test_script_applies_empty_value_change(self):
        # Hoi quy dau-cuoi: pack tu plan co before="" phai ap duoc bang
        # script (thay theo KEY ca hai ben), file goc khong doi.
        scripts = {"Version": "", "CreatedAt": "2020"}
        bundle = _bundle_bytes(scripts=scripts)
        plan = pl.build_plan(bundle, {"Version": "1.2.3",
                                      "CreatedAt": "2021"},
                             source_bundle="test.unity3d", entry=CAB)
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle, payload=plan)
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            patched = Path(td, "test.pb3-patched") / "test.unity3d"
            script = _script_of(patched.read_bytes())["en.json"]
            self.assertEqual(json.loads(script),
                             {"Version": "1.2.3", "CreatedAt": "2021"})
            self.assertEqual(Path(td, "test.unity3d").read_bytes(), bundle)

    def test_script_mod_raw_substring_fallback(self):
        # Edit kieu Mod tren script khong phai JSON: khong mo phong theo
        # key duoc -> roi ve thay chuoi tran nhu cu, ca hai ben.
        sf = build_serialized_file(
            version=17,
            objects=[(1001, 0, text_asset_data("notes.txt",
                                               "alpha beta alpha"))],
            types=[(49, None)])
        bundle = build_bundle(version=7, entries=[(CAB, 4, sf)],
                              compression="lz4")
        edits = [{"object": 1001, "old": "beta", "new": "BETA",
                  "entry": CAB, "mode": "REBUILD_SECTION"}]
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle, kind="mod", payload=edits)
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            patched = Path(td, "test.pb3-patched") / "test.unity3d"
            script = _script_of(patched.read_bytes())["notes.txt"]
            self.assertEqual(script, "alpha BETA alpha")
            self.assertEqual(Path(td, "test.unity3d").read_bytes(), bundle)

    def test_script_out_dir_overwriting_target_refuses(self):
        # Hoi quy chot dest != source (sua-loi): --out-dir trung thu muc
        # chua file goc + ten don vi trung ten file goc thi truoc day
        # script ghi de file goc (va van in "KHONG bi thay doi").
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            r = self._run_script(td, "test.unity3d", "--allow-write",
                                 "--out-dir", td)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("ghi de file goc", r.stderr)
            self.assertEqual(Path(td, "test.unity3d").read_bytes(), bundle)
            self.assertFalse(Path(td, "backup-goc").exists())

    def test_script_refuses_wrong_before_sha(self):
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            mpath = Path(td, "patch_manifest.json")
            man = json.loads(mpath.read_text(encoding="utf-8"))
            man["entries"][0]["before_sha"] = "0" * 64
            mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("before_sha", r.stderr)
            self.assertFalse(Path(td, "test.pb3-patched").exists())
            self.assertEqual(Path(td, "test.unity3d").read_bytes(), bundle)

    def test_script_refuses_wrong_source_sha(self):
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            # Sua file goc (doi 1 byte trong script) -> source sha lech.
            other = bytearray(bundle)
            other[-10] ^= 0xFF
            Path(td, "test.unity3d").write_bytes(bytes(other))
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("source_sha256", r.stderr)
            self.assertFalse(Path(td, "test.pb3-patched").exists())

    def test_script_refuses_missing_asset(self):
        # Pack lap tu bundle co en.json; ap len bundle khac khong co asset
        # do nhung co cung source sha? Khong the — source sha da chan.
        # Truong hop that: manifest sua asset_path/object sai -> asset
        # khong tim thay (before_sha cung sai) -> tu choi.
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            mpath = Path(td, "patch_manifest.json")
            man = json.loads(mpath.read_text(encoding="utf-8"))
            man["entries"][0]["object"] = 9999
            mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            r = self._run_script(td, "test.unity3d", "--allow-write")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("khong tim thay", r.stderr)
            self.assertFalse(Path(td, "test.pb3-patched").exists())

    def test_script_finds_no_pb3_tree_refuses_clearly(self):
        # Chi chay khi may nay khong co cay PB3 o duong dan mac dinh
        # (tren iSH co, test se bo qua).
        if Path(os.sep + "root" + os.sep + "pb3").is_dir():
            self.skipTest("may nay co cay PB3 mac dinh")
        bundle = _bundle_bytes()
        with tempfile.TemporaryDirectory() as td:
            self._stage_pack(td, bundle)
            env = dict(os.environ)
            env.pop("PB3_ROOT", None)
            env.pop("PYTHONPATH", None)
            r = subprocess.run(
                [sys.executable, os.path.join(td, "apply_patch.py"),
                 "test.unity3d"],
                cwd=td, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("khong tim thay cay PB3", r.stderr)


if __name__ == "__main__":
    unittest.main()
