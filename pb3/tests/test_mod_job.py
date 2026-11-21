"""Test Mod job: cong kiem tra + ep + repack tren may chu (Colab/Actions).

- Schema Mod job (kind="mod"): build/validate, job v1 cu (khong co
  kind) van la extract; truong mod sai -> fail-closed.
- Manifest Mod: entry nhi phan (khong phai locale JSON), build/write/
  read round-trip, trang thai done/failed, gate tom tat trong manifest.
- Ngan sach artifact 400 MB: file nho duoc nhan, IPA repack 450 MB bi
  loai kem ly do tieng Viet.
- Runner tren fixture Unity tong hop (builder co san cua test_gate):
  cong FAIL -> dung lai, khong ep, khong raise; cong DAT (kem font
  hien thi day du) -> ep that + kiem lai pressed PASS.
- Round-trip tren game Unity that (9K/EK2/PB3): chi chay khi file IPA
  co san trong ~/workspace/test-ipas (bo qua neu thieu — khong tai
  tu Drive trong test); ban dich [FIXTURE] = gia tri goc them tien to
  "[VI] " (giu nguyen placeholder), kiem tra: so cong khop truc tiep
  run_gate, bytes da ep == apply_rebuild truc tiep, IPA repack dat
  verify 6 muc, cong pressed PASS, ket qua nhap lai duoc (doc tu
  out dir: gate-report.json + file da ep mo duoc).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.helpers import TmpCase

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

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from sdk import colab_job as cj  # noqa: E402
from sdk import gate as _gate  # noqa: E402
from sdk import mod_job as mj  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import repack as _rp  # noqa: E402
from sdk import unity as _u  # noqa: E402
from tests.tiering import slow  # noqa: E402

DEAD_CELLS_ID = "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM"
ORIG_ID = "1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR"
TRANS_ID = "1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR"
TEST_IPAS = Path.home() / "workspace" / "test-ipas"

EN = {"hello": "Hello", "play": "Play", "quit": "Quit", "title": "9 Kings"}
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


def _mod_job(**kw):
    base = {"game": "9 Kings", "drive_file_id": DEAD_CELLS_ID,
            "original_file_id": ORIG_ID, "translation_file_id": TRANS_ID}
    base.update(kw)
    return cj.build_mod_job(**base)


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


def _fixture_bundle():
    """1 bundle don: 1 SerializedFile, 1 TextAsset en.json chua tat ca
    8 key (de pressed-gate chay duoc: 1 file dich duy nhat)."""
    sf = _sf([(1001, "en.json", SRC)])
    return _bundle(sf, CAB_A)


def _full_display_font_bytes():
    return font_fx.build_font_full_vietnamese()


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


class TestModJobSchema(TmpCase):
    def test_build_mod_job_defaults(self):
        job = _mod_job()
        self.assertEqual(job["kind"], "mod")
        self.assertEqual(job["version"], 1)
        self.assertEqual(job["apply_mode"], "REBUILD_SECTION")
        self.assertTrue(job["repack"])
        self.assertFalse(job["gate_only"])
        self.assertEqual(job["to_lang"], "vi")
        self.assertEqual(job["display_font"], "donor")
        self.assertEqual(job["locales"], [])
        self.assertFalse(job["translate"])
        self.assertEqual(job["worker_url"], "")

    def test_mod_job_bad_display_font_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            _mod_job(display_font="comic-sans")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_build_mod_job_options(self):
        job = _mod_job(target_lang="de", apply_mode="PATCH_INPLACE",
                       repack=False, gate_only=True)
        self.assertEqual(job["to_lang"], "de")
        self.assertEqual(job["apply_mode"], "PATCH_INPLACE")
        self.assertFalse(job["repack"])
        self.assertTrue(job["gate_only"])

    def test_mod_job_accepts_drive_links(self):
        job = _mod_job(
            drive_file_id="https://drive.google.com/file/d/%s/view"
                          % DEAD_CELLS_ID,
            original_file_id="https://drive.google.com/open?id=%s" % ORIG_ID)
        self.assertEqual(job["drive_file_id"], DEAD_CELLS_ID)
        self.assertEqual(job["original_file_id"], ORIG_ID)

    def test_mod_job_missing_translation_id_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            cj.build_mod_job("9 Kings", DEAD_CELLS_ID, ORIG_ID, "")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_mod_job_bad_apply_mode_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            _mod_job(apply_mode="NOPE")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_mod_job_non_bool_repack_rejected(self):
        with self.assertRaises(PB3Error) as ctx:
            _mod_job(repack="yes")
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_old_v1_job_still_extract(self):
        job = cj.validate_job({"version": 1, "game": "X",
                               "drive_file_id": DEAD_CELLS_ID})
        self.assertEqual(job["kind"], "extract")
        self.assertEqual(job["locales"], ["en", "zh"])

    def test_extract_job_unchanged_by_mod_fields(self):
        job = cj.build_job("Dead Cells", DEAD_CELLS_ID)
        self.assertEqual(job["kind"], "extract")
        self.assertNotIn("apply_mode", job)

    def test_job_output_dir_mod_suffix(self):
        self.assertEqual(
            cj.job_output_dir("9kings.ipa", "9 Kings", suffix="-mod"),
            "MeoRocky-output/9kings-mod")
        self.assertEqual(cj.job_output_dir("", "9 Kings"),
                         "MeoRocky-output/9 Kings")


class TestModManifest(TmpCase):
    def _binary_file(self, name, data=b"ABCD" * 10):
        p = Path(self.tmp) / name
        p.write_bytes(data)
        return p

    def test_binary_entry_md5_bytes(self):
        p = self._binary_file("x.assets")
        e = cj.manifest_binary_entry(str(p), "applied")
        self.assertEqual(e["name"], "x.assets")
        self.assertEqual(e["kind"], "applied")
        self.assertEqual(e["bytes"], 40)
        self.assertEqual(e["md5"], hashlib.md5(b"ABCD" * 10).hexdigest())

    def test_build_mod_manifest_done_with_gate(self):
        job = _mod_job()
        gate = {"verdict": "PASS", "mode": "pre_write",
                "totals": {"keys": 10, "changed": 9}}
        m = cj.build_mod_manifest(
            job, "done", [cj.manifest_binary_entry(
                str(self._binary_file("gate-report.json")), "gate-report")],
            gate=gate, note="ok")
        self.assertEqual(m["kind"], "mod")
        self.assertEqual(m["status"], "done")
        self.assertEqual(m["gate"]["verdict"], "PASS")
        self.assertEqual(m["gate"]["totals"]["keys"], 10)
        self.assertNotIn("verdict", m["gate"].get("totals", {}))
        self.assertEqual(m["out_dir"], "MeoRocky-output/9 Kings-mod")
        self.assertEqual(m["files"][0]["kind"], "gate-report")

    def test_build_mod_manifest_rejects_extract_job(self):
        job = cj.build_job("Dead Cells", DEAD_CELLS_ID)
        with self.assertRaises(PB3Error) as ctx:
            cj.build_mod_manifest(job, "done", [])
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_build_mod_manifest_rejects_bad_status(self):
        with self.assertRaises(PB3Error):
            cj.build_mod_manifest(_mod_job(), "extracted", [])

    def test_build_mod_manifest_without_job_uses_explicit_ids(self):
        m = cj.build_mod_manifest(None, "done", [],
                                  job_id="mod-9-kings-20261118-120000",
                                  game="9 Kings")
        self.assertEqual(m["job_id"], "mod-9-kings-20261118-120000")
        self.assertEqual(m["game"], "9 Kings")
        self.assertEqual(m["out_dir"], "MeoRocky-output/9 Kings-mod")

    def test_write_read_mod_manifest_round_trip(self):
        job = _mod_job()
        m = cj.build_mod_manifest(job, "failed", [], note="loi chay")
        path = cj.write_mod_manifest(str(self.tmp), m)
        back = cj.read_manifest(path)
        self.assertEqual(back["job_id"], m["job_id"])
        self.assertEqual(back["kind"], "mod")
        self.assertEqual(back["status"], "failed")

    def test_write_mod_manifest_rejects_extract_manifest(self):
        job = cj.build_job("Dead Cells", DEAD_CELLS_ID)
        m = cj.build_manifest(job, "done", [])
        with self.assertRaises(PB3Error):
            cj.write_mod_manifest(str(self.tmp), m)


class TestArtifactPlan(TmpCase):
    def test_small_files_all_included(self):
        plan = mj.artifact_plan([
            {"name": "gate-report.json", "bytes": 5000,
             "kind": "gate-report", "required": True},
            {"name": "summary.json", "bytes": 2000,
             "kind": "summary", "required": True},
            {"name": "applied.zip", "bytes": 1024 * 1024,
             "kind": "applied", "required": False},
            {"name": "game-mod.ipa", "bytes": 165 * 1024 * 1024,
             "kind": "repacked", "required": False},
        ])
        self.assertEqual([e["name"] for e in plan["included"]],
                         ["gate-report.json", "summary.json", "applied.zip",
                          "game-mod.ipa"])
        self.assertEqual(plan["excluded"], [])
        self.assertEqual(plan["budget_bytes"], 400 * 1024 * 1024)

    def test_oversize_repacked_ipa_excluded_with_reason(self):
        plan = mj.artifact_plan([
            {"name": "gate-report.json", "bytes": 5000,
             "kind": "gate-report", "required": True},
            {"name": "game-mod.ipa", "bytes": 450 * 1024 * 1024,
             "kind": "repacked", "required": False},
        ])
        self.assertEqual([e["name"] for e in plan["included"]],
                         ["gate-report.json"])
        self.assertEqual(len(plan["excluded"]), 1)
        self.assertEqual(plan["excluded"][0]["name"], "game-mod.ipa")
        self.assertIn("Colab", plan["excluded"][0]["reason"])
        self.assertIn("400 MB", plan["excluded"][0]["reason"])

    def test_cumulative_budget_excludes_second_big_file(self):
        plan = mj.artifact_plan([
            {"name": "a.zip", "bytes": 300 * 1024 * 1024,
             "kind": "applied", "required": False},
            {"name": "b-mod.ipa", "bytes": 200 * 1024 * 1024,
             "kind": "repacked", "required": False},
        ])
        self.assertEqual([e["name"] for e in plan["included"]], ["a.zip"])
        self.assertEqual([e["name"] for e in plan["excluded"]],
                         ["b-mod.ipa"])


class TestModJobRunnerFixture(TmpCase):
    def _run_fixture(self, translations, **kw):
        src = _write_json(Path(self.tmp) / "en.json", SRC)
        tr = _write_json(Path(self.tmp) / "vi.json", translations)
        bundle = Path(self.tmp) / "game.unity3d"
        bundle.write_bytes(_fixture_bundle())
        out = Path(self.tmp) / "out"
        job = _mod_job()
        return mj.run_mod_job(
            ipa_path=str(bundle), original_path=str(src),
            translation_path=str(tr), out_dir=str(out), job=job,
            **kw), out

    def test_gate_fail_stops_without_apply(self):
        # Ban dich thieu key "quit" -> cong (a) FAIL -> dung lai, khong
        # ep, khong raise (FAIL la ket qua, khong phai loi).
        bad = dict(VI)
        del bad["quit"]
        summary, out = self._run_fixture(bad)
        self.assertEqual(summary["status"], "gate_failed")
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["gate_verdict"], "FAIL")
        self.assertFalse(summary["applied"])
        self.assertTrue((out / "gate-report.json").is_file())
        self.assertTrue((out / "summary.json").is_file())
        self.assertTrue((out / "handoff-manifest.json").is_file())
        manifest = cj.read_manifest(str(out / "handoff-manifest.json"))
        self.assertEqual(manifest["status"], "done")
        self.assertEqual(manifest["gate"]["verdict"], "FAIL")
        self.assertEqual(manifest["files"][0]["name"], "gate-report.json")

    def test_gate_pass_applies_and_pressed_verifies(self):
        # Mac dinh runner dung font di kem trong repo (donor, 142/142)
        # nen cong (d) DAT ngay ca khi file goc khong co font doc duoc.
        summary, out = self._run_fixture(VI, repack=False)
        self.assertEqual(summary["status"], "done", summary.get("note"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["gate_verdict"], "PASS")
        self.assertTrue(summary["applied"])
        self.assertEqual(summary["apply_totals"]["changed"], 7)
        self.assertEqual(summary["pressed_gate_verdict"], "PASS")
        self.assertTrue((out / "pressed-gate-report.json").is_file())
        self.assertTrue((out / "applied.zip").is_file())
        manifest = cj.read_manifest(str(out / "handoff-manifest.json"))
        self.assertEqual(manifest["gate"]["verdict"], "PASS")
        kinds = [f["kind"] for f in manifest["files"]]
        self.assertEqual(kinds[0], "gate-report")
        self.assertIn("applied", kinds)

    def test_scan_mode_fails_gate_when_no_readable_font(self):
        # Che do quet (nhu mac dinh web): fixture khong co font doc
        # duoc nao -> check (d) FAIL -> dung lai, khong ep. Day la ly
        # do server mac dinh dung donor (da do that tren 9K).
        summary, out = self._run_fixture(VI, display_font_path="scan",
                                         repack=False)
        self.assertEqual(summary["status"], "gate_failed")
        self.assertEqual(summary["gate_verdict"], "FAIL")
        self.assertFalse(summary["applied"])
        gate_back = json.loads(
            (out / "gate-report.json").read_text(encoding="utf-8"))
        fonts = [c for c in gate_back["checks"] if c["id"] == "fonts"]
        self.assertEqual(fonts[0]["status"], "fail")

    def test_summary_has_phase_timings(self):
        # Thoi gian tung buoc de do toc do ep (Duy 22:57 +07): gate /
        # apply / pressed_gate + total, khop elapsed_seconds.
        summary, out = self._run_fixture(VI, repack=False)
        timings = summary.get("timings") or {}
        for key in ("gate_seconds", "apply_seconds",
                    "pressed_gate_seconds", "total_seconds"):
            self.assertIn(key, timings, "thieu timings[%s]" % key)
            self.assertGreaterEqual(timings[key], 0)
        self.assertAlmostEqual(timings["total_seconds"],
                               summary["elapsed_seconds"], delta=2.0)

    def test_no_duplicate_rebuild_zip(self):
        # apply_rebuild ghi thang applied.zip; khong con rebuild.zip
        # rieng (truoc day 2 file zip giong het nhau).
        summary, out = self._run_fixture(VI, repack=False)
        self.assertTrue((out / "applied.zip").is_file())
        self.assertFalse((out / "rebuild.zip").exists())

    def test_gate_only_pass_stops_after_gate(self):
        # Font hien thi truyen tuong minh (duong dan .ttf) cung chay.
        font_path = Path(self.tmp) / "donor.ttf"
        font_path.write_bytes(_full_display_font_bytes())
        summary, out = self._run_fixture(
            VI, display_font_path=str(font_path), gate_only=True)
        self.assertEqual(summary["status"], "gate_only_pass")
        self.assertTrue(summary["ok"])
        self.assertFalse(summary["applied"])
        self.assertFalse((out / "applied.zip").exists())

    def test_repack_on_non_ipa_fails_closed(self):
        with self.assertRaises(PB3Error) as ctx:
            self._run_fixture(VI, repack=True)
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")

    def test_repack_on_non_ipa_fails_before_apply(self):
        # Fail-fast (sua 00:21 +07 19/11, sua lai vi tri sau khi thay
        # check truoc cong pha 2 test gate_fail/gate_only): cong van
        # chay (gate-report co, verdict PASS) roi loi truoc khi ep —
        # khong ton thoi gian ep, khong ghi summary/applied.zip.
        out = Path(self.tmp) / "out"
        with self.assertRaises(PB3Error) as ctx:
            self._run_fixture(VI, repack=True)
        self.assertEqual(ctx.exception.code, "E_CONFIG_INVALID")
        gate_back = json.loads(
            (out / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(gate_back["verdict"], "PASS")
        self.assertFalse((out / "summary.json").exists())
        self.assertFalse((out / "applied.zip").exists())

    def test_missing_display_font_fails_closed(self):
        # Duong dan font khong ton tai -> E_INPUT_NOT_FOUND (khong
        # phai FileNotFoundError tho — sua 00:21 +07 19/11).
        with self.assertRaises(PB3Error) as ctx:
            self._run_fixture(VI, repack=False,
                              display_font_path="khong-co-font.ttf")
        self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_missing_ipa_fails_closed(self):
        src = _write_json(Path(self.tmp) / "en.json", SRC)
        tr = _write_json(Path(self.tmp) / "vi.json", VI)
        with self.assertRaises(PB3Error) as ctx:
            mj.run_mod_job(ipa_path=str(Path(self.tmp) / "khong-co.ipa"),
                           original_path=str(src), translation_path=str(tr),
                           out_dir=str(Path(self.tmp) / "out"))
        self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_bad_translation_json_fails_closed(self):
        bad = Path(self.tmp) / "vi.json"
        bad.write_text("{khong phai json", encoding="utf-8")
        src = _write_json(Path(self.tmp) / "en.json", SRC)
        bundle = Path(self.tmp) / "game.unity3d"
        bundle.write_bytes(_fixture_bundle())
        with self.assertRaises(PB3Error) as ctx:
            mj.run_mod_job(ipa_path=str(bundle), original_path=str(src),
                           translation_path=str(bad),
                           out_dir=str(Path(self.tmp) / "out"))
        self.assertEqual(ctx.exception.code, "E_CORRUPT")


@slow
class TestModJobEngine(TmpCase):
    """Interface engine chung (mod_job.ENGINES) + truong engine trong
    schema/manifest — mo cho Unreal/Native iOS sau nay, hien chi Unity."""

    def test_registry_has_only_unity(self):
        self.assertEqual(sorted(mj.ENGINES), ["unity"])
        self.assertEqual(mj.get_engine("unity").name, "unity")
        self.assertEqual(mj.get_engine(" Unity ").name, "unity")
        # Schema phai khop registry (them engine thi dang ky ca 2 cho).
        self.assertEqual(sorted(cj.JOB_ENGINES), sorted(mj.ENGINES))

    def test_unknown_engine_fails_closed_before_reading_files(self):
        # Thong bao cu the nam o .detail (message_vi la text chung cua ma).
        with self.assertRaises(PB3Error) as cm:
            mj.get_engine("unreal")
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        self.assertIn("chua ho tro", cm.exception.detail)
        # run_mod_job voi engine la: loi engine, KHONG phai loi file
        # (chung to engine duoc giai quyet truoc khi doc file nao).
        with self.assertRaises(PB3Error) as cm2:
            mj.run_mod_job(ipa_path="/khong/co/ipa.ipa",
                           original_path="/khong/co/en.json",
                           translation_path="/khong/co/vi.json",
                           out_dir=str(Path(self.tmp) / "out"),
                           engine="native-ios")
        self.assertEqual(cm2.exception.code, "E_CONFIG_INVALID")
        self.assertIn("chua ho tro", cm2.exception.detail)

    def test_schema_engine_field_and_manifest(self):
        job = _mod_job()
        self.assertEqual(job["engine"], "unity")
        bad = dict(job)
        bad["engine"] = "unreal"
        with self.assertRaises(PB3Error):
            cj.validate_job(bad)
        manifest = cj.build_mod_manifest(
            job, "done", [], job_id=job["job_id"], game=job["game"])
        self.assertEqual(manifest["engine"], "unity")
        with self.assertRaises(PB3Error):
            cj.build_mod_manifest(
                None, "done", [], job_id="mod-x-20261118-000000",
                game="x", engine="unreal")


@slow
class TestModJobRoundTripRealGames(TmpCase):
    """Round-trip tren game Unity that (chi chay khi IPA co san o
    ~/workspace/test-ipas — bo qua neu thieu). Ban dich [FIXTURE]:
    gia tri goc + tien to "[VI] " (giu nguyen placeholder, doi moi
    key) — khong phai ban dich that cua user. EK2 co test liet ke
    don vi rieng (F3: bundle khong con bi tu choi E_ENCRYPTED) —
    round-trip day du EK2 chua do xong (cong day du > 30 phut duoi
    tai, xem test do), khong dung _round_trip."""

    # Tran timeout rieng cho runner (run_tests.py: mac dinh 180s):
    # PB3 round-trip do that ~16 phut (cong 370s + ep 185s + repack
    # 58s + kiem lai 370s, test ~25 phut); 9K ~230s — ca 2 deu vuot
    # 180s. EK2 liet ke don vi ~143s (F3 22/11) nhung o chung lop nen
    # cung huong tran nay (tran la gioi han tren, khong phai muc
    # tieu thoi gian).
    timeout_s = 1800

    def _vi_fixture(self, en):
        return {k: ("[VI] " + v if v else "[VI]") for k, v in en.items()}

    def _round_trip(self, ipa_name, en_path):
        ipa = TEST_IPAS / ipa_name
        if not ipa.is_file():
            self.skipTest("thieu IPA that: %s" % ipa)
        en_path = Path(en_path)
        if not en_path.is_file():
            self.skipTest("thieu en.json that: %s" % en_path)
        en = json.loads(en_path.read_text(encoding="utf-8"))
        vi = self._vi_fixture(en)
        src = _write_json(Path(self.tmp) / "en.json", en)
        tr = _write_json(Path(self.tmp) / "vi.json", vi)
        out = Path(self.tmp) / "out"
        data = ipa.read_bytes()
        # 1. Cong truc tiep (doi chieu): so phai khop so cua runner.
        # Runner mac dinh dung font di kem (donor) — cong truc tiep
        # phai dung cung font do (quet scan FAIL check font tren 9K
        # vi IPA khong co font doc duoc — da do that).
        donor = mj._donor_font_bytes()
        donor_fonts = ([_gate.font_entry_from_bytes(
            donor, "BeVietnamPro-Regular.ttf")] if donor else [])
        direct_gate = _gate.run_gate(
            data, source_name=ipa.name, translations=vi, source_texts=en,
            display_fonts=donor_fonts, target_lang="vi")
        # 2. Runner day du: cong -> ep -> repack -> kiem lai pressed.
        summary = mj.run_mod_job(
            ipa_path=str(ipa), original_path=str(src),
            translation_path=str(tr), out_dir=str(out),
            game=ipa.stem, target_lang="vi")
        self.assertEqual(summary["gate_verdict"],
                         direct_gate["verdict"], summary.get("note"))
        self.assertEqual(summary["gate_totals"],
                         direct_gate["totals"])
        self.assertEqual(summary["status"], "done", summary.get("note"))
        self.assertTrue(summary["applied"])
        self.assertTrue(summary["repacked"])
        self.assertEqual(summary["pressed_gate_verdict"], "PASS")
        # 3. Bytes da ep == apply_rebuild truc tiep (cung input).
        direct = _rb.apply_rebuild(
            data, source_name=ipa.name, translations=vi, dry_run=False,
            confirm=True, mode="REBUILD_SECTION",
            out_dir=str(Path(self.tmp) / "direct"),
            zip_path=str(Path(self.tmp) / "direct.zip"),
            source_locale="en")
        direct_by_unit = {u["unit"]: u for u in direct["units"]}
        for u in summary["applied_units"]:
            self.assertIn(u["unit"], direct_by_unit)
            mine = (out / "applied" / u["output"]).read_bytes()
            theirs = (Path(self.tmp) / "direct"
                      / direct_by_unit[u["unit"]]["output"]).read_bytes()
            self.assertEqual(mine, theirs,
                             "bytes da ep lech o don vi %s" % u["unit"])
        # 4. IPA repack dat verify 6 muc + mo lai duoc (doi chieu tung
        # entry da thay bang dung bytes da ep).
        repacked = out / "repack" / summary["repacked_name"]
        self.assertTrue(repacked.is_file())
        replacements = {}
        for u in summary["applied_units"]:
            entry = _rp.zip_entry_for_unit(u["unit"])
            replacements[entry] = (out / "applied" / u["output"]).read_bytes()
        check = _rp.verify_repacked_ipa(str(ipa), str(repacked),
                                        replacements)
        self.assertTrue(check["passed"],
                        [c for c in check["checks"] if not c["passed"]])
        # 5. Ket qua nhap lai duoc: gate-report.json doc duoc + co du
        # file de web hien (manifest + summary + gate-report dau tien).
        gate_back = json.loads(
            (out / "gate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(gate_back["verdict"], "PASS")
        self.assertEqual(gate_back["totals"]["keys"], len(en))
        manifest = cj.read_manifest(str(out / "handoff-manifest.json"))
        self.assertEqual(manifest["status"], "done")
        self.assertEqual(manifest["files"][0]["name"], "gate-report.json")
        self.assertIn(summary["repacked_name"],
                      [f["name"] for f in manifest["files"]])
        # Khong ghi thua: chi co applied.zip, khong con rebuild.zip
        # rieng (truoc day 2 file zip giong het nhau).
        self.assertFalse((out / "rebuild.zip").exists())
        self.assertTrue((out / "applied.zip").is_file())
        return summary, len(en), repacked.stat().st_size

    def test_round_trip_9kings(self):
        summary, nkeys, repacked_size = self._round_trip(
            "9kings-v1.0.6.ipa",
            Path.home() / "workspace" / "user" / "files" / "en.json")
        self.assertEqual(nkeys, 2252)
        self.assertGreater(repacked_size, 100 * 1024 * 1024)

    def test_ek2_bundles_no_longer_refused_as_encrypted(self):
        """EK2 that [DA DO THAT F3 22/11]: 100 bundle flag 0x243 KHONG
        phai ma hoa — revision "0.0.0" bi xoa nen bit 0x200 la co padding
        (co moi). Buoc liet ke don vi cua cong (rebuild._enumerate_units,
        backend NATIVE nhu run_gate) tra 112 don vi (105 bundle + 7
        .assets), refused 0 — khong con E_ENCRYPTED nao.
        [DA DO THAT: _enumerate_units 142,8s tren may nay (backend NATIVE
        dung lz4pure, duoi tai cua suite khac). Cong DAY DU thi CHUA do
        xong — probe gate_only timeout 1800s ngay 22/11 (cong cu chi
        46,5s vi tu choi nhanh ca 100 bundle), nen verdict cong + so
        unmatched KHONG ghim o day; do lai tren may roi (Dung/Minh)
        truoc khi round-trip EK2. en.json cu (1344 key) chi gom text
        trich duoc luc bundle con bi tu choi, nay da loi thoi.]"""
        ipa = TEST_IPAS / "ek2-v0.0.18.ipa"
        if not ipa.is_file():
            self.skipTest("thieu IPA that: %s" % ipa)
        data = ipa.read_bytes()
        refused = []
        units = _rb._enumerate_units(
            data, _u.UnityBackend("NATIVE", Limits()), Limits(),
            refused=refused)
        self.assertEqual(len(units), 112)
        kinds = {}
        for _n, k in units:
            kinds[k] = kinds.get(k, 0) + 1
        self.assertEqual(kinds, {"bundle": 105, "assets": 7})
        self.assertEqual(refused, [])
        self.assertNotIn("E_ENCRYPTED", json.dumps(refused))

    def test_round_trip_pb3(self):
        summary, nkeys, repacked_size = self._round_trip(
            "pb3-v1.1.7.ipa",
            TEST_IPAS / "pb3-extract" / "en.json")
        self.assertGreater(nkeys, 0)
        self.assertGreater(repacked_size, 100 * 1024 * 1024)


class TestNotebookModCell(TmpCase):
    """Cell job + cell Mod trong notebook (nguon that duy nhat)."""
    NOTEBOOK = ROOT.parent / "colab" / "extract-large-ipa.ipynb"
    TRANS2_ID = "1Q2w3e4r5t6y7u8i9o0p1234567890ab"

    def _source(self, marker):
        nb = json.loads(self.NOTEBOOK.read_text(encoding="utf-8"))
        found = ["".join(c["source"]) for c in nb["cells"]
                 if c["cell_type"] == "code"
                 and marker in "".join(c["source"])]
        self.assertEqual(len(found), 1, "phai co dung 1 cell chua %r" % marker)
        return found[0]

    def test_job_cell_sets_mod_vars(self):
        src = self._source("resolve_job")
        job = _mod_job(job_id="nine-kings-20261118-120000",
                       game_file_name="9kings.ipa")
        self.assertIn('JOB_JSON = ""', src)
        src = src.replace('JOB_JSON = ""',
                          "JOB_JSON = " + repr(cj.job_to_json(job)), 1)
        ns = {"REPO_DIR": str(ROOT.parent)}
        exec(compile(src, "<cell job>", "exec"), ns)  # noqa: S102
        self.assertEqual(ns["JOB"]["kind"], "mod")
        self.assertEqual(ns["ORIGINAL_FILE_ID"], ORIG_ID)
        self.assertEqual(ns["TRANSLATION_FILE_ID"], TRANS_ID)
        self.assertEqual(ns["APPLY_MODE"], "REBUILD_SECTION")
        self.assertTrue(ns["MOD_REPACK"])
        self.assertFalse(ns["MOD_GATE_ONLY"])
        self.assertEqual(ns["DISPLAY_FONT"], "donor")
        self.assertEqual(ns["ENGINE"], "unity")

    def test_mod_cell_skips_without_job(self):
        src = self._source("run_mod_job")
        ns = {"REPO_DIR": str(ROOT.parent)}
        exec(compile(src, "<cell mod>", "exec"), ns)  # noqa: S102
        self.assertIsNone(ns.get("_orig_id"))

    def test_mod_cell_runs_fixture_mod_job(self):
        import shutil
        src = self._source("run_mod_job")
        bundle = Path(self.tmp) / "game.unity3d"
        bundle.write_bytes(_fixture_bundle())
        en_path = _write_json(Path(self.tmp) / "en.json", SRC)
        vi_path = _write_json(Path(self.tmp) / "vi.json", VI)
        by_id = {ORIG_ID: str(en_path), self.TRANS2_ID: str(vi_path)}

        def _download(file_id, dest=None, service=None):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(by_id[file_id], dest)
            return dest

        job = _mod_job(translation_file_id=self.TRANS2_ID,
                       game_file_name="game.unity3d", repack=False)
        work_dir = Path(self.tmp) / "work"
        drive_dir = Path(self.tmp) / "drive"
        ns = {"REPO_DIR": str(ROOT.parent), "JOB": job,
              "IPA_PATH": str(bundle), "GAME_NAME": "test-game",
              "GAME_FILE_NAME": "game.unity3d",
              "MOD_WORK_DIR": str(work_dir), "MOD_DRIVE_DIR": str(drive_dir),
              "download_by_file_id": _download}
        mod_out = drive_dir / "MeoRocky-output" / "game-mod"
        try:
            exec(compile(src, "<cell mod>", "exec"), ns)  # noqa: S102
            summary = ns["_summary"]
            self.assertEqual(summary["status"], "done", summary.get("note"))
            self.assertEqual(summary["gate_verdict"], "PASS")
            self.assertEqual(summary["engine"], "unity")
            self.assertTrue((mod_out / "gate-report.json").is_file())
            self.assertTrue((mod_out / "handoff-manifest.json").is_file())
            self.assertTrue((mod_out / "applied.zip").is_file())
            self.assertFalse((mod_out / "rebuild.zip").exists())
        finally:
            shutil.rmtree(mod_out, ignore_errors=True)
            for p in (work_dir / "original.json", work_dir / "translation.json"):
                try:
                    os.unlink(p)
                except OSError:
                    pass


class TestModJobJsAndUi(TmpCase):
    """JS that (buildModJob) + UI Mod (2 panel) — chay that bang node,
    khong port tay. Mirror test_actions_extract.py."""

    def test_build_mod_job_runs_real_js(self):
        import pytest
        node = shutil.which("node")
        if not node:
            pytest.skip("thieu node")
        js = (ROOT.parent / "colab_handoff.js").read_text(encoding="utf-8")
        self.assertIn("function buildModJob", js)
        self.assertIn("modManifestResultFiles", js)
        check = self.tmp / "mod_job_check.cjs"
        check.write_text(
            "const h = require(%s);\n"
            "const job = h.buildModJob({game: '9 Kings',\n"
            "  driveInput: '1uomLfXLz6bd3ITQiACaw27mzhVe9U_NL',\n"
            "  originalInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR',\n"
            "  translationInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR',\n"
            "  gameFileName: '9kings.ipa'});\n"
            "const want = {kind: 'mod', apply_mode: 'REBUILD_SECTION',\n"
            "  display_font: 'donor', repack: true, gate_only: false,\n"
            "  to_lang: 'vi', from_lang: 'en', engine: 'unity'};\n"
            "for (const k of Object.keys(want)) {\n"
            "  if (job[k] !== want[k]) {\n"
            "    console.error('SAI ' + k + ': ' + JSON.stringify(job[k]));\n"
            "    process.exit(1);\n"
            "  }\n"
            "}\n"
            "const files = h.modManifestResultFiles({files: [\n"
            "  {name: 'gate-report.json', kind: 'gate-report', bytes: 100},\n"
            "  {name: 'x-mod.ipa', kind: 'repacked', bytes: 165000000}]});\n"
            "if (files[1].kind !== 'repacked' || files[0].kind !== 'gate-report') {\n"
            "  console.error('SAI modManifestResultFiles: ' + JSON.stringify(files));\n"
            "  process.exit(1);\n"
            "}\n"
            "let threw = false;\n"
            "try {\n"
            "  h.buildModJob({game: 'X', driveInput: 'abc',\n"
            "    originalInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR',\n"
            "    translationInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR'});\n"
            "} catch (e) { threw = true; }\n"
            "if (!threw) { console.error('SAI: ID xau phai throw'); process.exit(1); }\n"
            "let threwEngine = false;\n"
            "try {\n"
            "  h.buildModJob({game: 'X',\n"
            "    driveInput: '1uomLfXLz6bd3ITQiACaw27mzhVe9U_NL',\n"
            "    originalInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR',\n"
            "    translationInput: '1APld6Xm30CsCDFjytOBCkK7tZhaTCrnR',\n"
            "    engine: 'unreal'});\n"
            "} catch (e) { threwEngine = true; }\n"
            "if (!threwEngine) { console.error('SAI: engine la phai throw'); process.exit(1); }\n"
            "console.log('buildModJob OK');\n"
            % json.dumps(str(ROOT.parent / "colab_handoff.js")),
            encoding="utf-8")
        r = subprocess.run([node, str(check)], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("buildModJob OK", r.stdout)

    def test_mod_ui_ids_exist_once(self):
        html = (ROOT.parent / "index.html").read_text(encoding="utf-8")
        for ident in ("mod-server-section", "mod-server-game-name",
                      "mod-server-drive-ipa", "mod-server-drive-original",
                      "mod-server-drive-translation", "mod-server-to-lang",
                      "mod-server-apply-mode", "mod-server-display-font",
                      "mod-server-engine", "mod-server-repack", "mod-server-gate-only",
                      "mod-server-gh-token", "mod-server-token-state",
                      "btn-mod-server-save-token", "btn-mod-server-clear-token",
                      "btn-mod-server-run", "btn-mod-server-refresh",
                      "btn-mod-server-import-gate",
                      "mod-server-import-gate-file", "mod-server-status",
                      "mod-server-results",
                      "mod-colab-section", "mod-colab-game-name",
                      "mod-colab-drive-ipa", "mod-colab-drive-original",
                      "mod-colab-drive-translation", "mod-colab-ipa-name",
                      "mod-colab-to-lang", "mod-colab-apply-mode",
                      "mod-colab-display-font", "mod-colab-engine",
                      "mod-colab-repack",
                      "mod-colab-gate-only", "btn-mod-colab-copy-job",
                      "btn-mod-colab-download-job", "btn-mod-colab-open",
                      "btn-mod-colab-import-results", "mod-colab-import-file",
                      "mod-colab-status", "mod-colab-results",
                      "mod-colab-oauth-client-id",
                      "btn-mod-colab-save-client-id",
                      "btn-mod-colab-pick-ipa", "btn-mod-colab-upload-ipa",
                      "mod-colab-ipa-file", "btn-mod-colab-pick-original",
                      "btn-mod-colab-upload-original",
                      "mod-colab-original-file",
                      "btn-mod-colab-pick-translation",
                      "btn-mod-colab-upload-translation",
                      "mod-colab-translation-file",
                      "btn-mod-colab-check-results"):
            self.assertEqual(html.count('id="%s"' % ident), 1,
                             "thieu/trung id %s" % ident)
        # Engine: chi Unity chon duoc, Unreal/Native iOS khoa (sap co).
        self.assertEqual(html.count('value="unreal" disabled'), 2)
        self.assertEqual(html.count('value="native-ios" disabled'), 2)
        # Panel Actions phai dung TRUOC panel Colab (duong chinh truoc,
        # duong du phong sau) va ca 2 phai nam sau Buoc 5, truoc Nâng cao.
        self.assertLess(html.index('id="mod-server-section"'),
                        html.index('id="mod-colab-section"'))
        self.assertLess(html.index('id="repack-result"'),
                        html.index('id="mod-server-section"'))
        self.assertLess(html.index('id="mod-colab-section"'),
                        html.index('id="mod-advanced"'))
        self.assertIn("MOD_COLAB_ACTIONS.md", html)
        app_js = (ROOT.parent / "app.js").read_text(encoding="utf-8")
        for piece in ("MOD_SERVER_WORKFLOW", "mod-large.yml",
                      "meorocky_mod_server_run", "function modServerRun",
                      "function modServerPoll", "function modServerShowResults",
                      "mod-server-engine", "display_font: displayFont, engine",
                      "window.MeoRockyMod", "showGateReport"):
            self.assertIn(piece, app_js, "app.js thieu %s" % piece)
        # Khoi Mod server khong log token, khong chua token that.
        mod_block = app_js.split(
            "// ---- Ép trên máy chủ (GitHub Actions, Mod job)")[1].split(
                "// ---- Dịch ----")[0]
        self.assertNotIn("console.log", mod_block)
        self.assertNotRegex(mod_block, r"ghp_[A-Za-z0-9]{20,}")
        self.assertNotRegex(mod_block, r"github_pat_[A-Za-z0-9_]{20,}")
        # Colab handoff: panel Mod dung chung may moc Drive/poll.
        ch = (ROOT.parent / "colab_handoff.js").read_text(encoding="utf-8")
        for piece in ("function buildModJob", "function initMod",
                      "function downloadModResults",
                      "function importModResults",
                      "mod-colab-status", "mod-colab-engine", "MeoRockyMod"):
            self.assertIn(piece, ch, "colab_handoff.js thieu %s" % piece)
        self.assertIn("initMod();", ch)


class TestModJobScriptAndWorkflow(TmpCase):
    SCRIPT = ROOT.parent / ".github" / "scripts" / "mod_large.py"
    WORKFLOW = ROOT.parent / ".github" / "workflows" / "mod-large.yml"

    def _run_script(self, args, cwd, env=None):
        e = dict(os.environ)
        if env:
            e.update(env)
        return subprocess.run(
            [sys.executable, str(self.SCRIPT)] + args,
            cwd=str(cwd), env=e, capture_output=True, text=True,
            timeout=600)

    def test_script_gate_only_fixture(self):
        src = _write_json(Path(self.tmp) / "en.json", SRC)
        tr = _write_json(Path(self.tmp) / "vi.json", VI)
        ipa = Path(self.tmp) / "game.unity3d"
        ipa.write_bytes(_fixture_bundle())
        font_path = Path(self.tmp) / "donor.ttf"
        font_path.write_bytes(_full_display_font_bytes())
        out = Path(self.tmp) / "out"
        r = self._run_script([
            "--ipa", str(ipa), "--original", str(src),
            "--translation", str(tr), "--game", "test-game",
            "--gate-only", "--display-font", str(font_path),
            "--out", str(out),
        ], cwd=ROOT.parent, env={"TMPDIR": str(self.tmp)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        summary = json.loads((out / "summary.json").read_text(
            encoding="utf-8"))
        self.assertEqual(summary["status"], "gate_only_pass")
        self.assertEqual(summary["gate_verdict"], "PASS")
        self.assertIn("cong DAT", r.stdout)

    def test_script_fails_closed_on_missing_ipa(self):
        r = self._run_script([
            "--ipa", str(Path(self.tmp) / "khong-co.ipa"),
            "--original", str(Path(self.tmp) / "en.json"),
            "--translation", str(Path(self.tmp) / "vi.json"),
            "--game", "test-game", "--out", str(Path(self.tmp) / "out"),
        ], cwd=ROOT.parent, env={"TMPDIR": str(self.tmp)})
        self.assertEqual(r.returncode, 1)

    def test_workflow_file_wiring(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("on:\n  push:", text)
        self.assertNotIn("pull_request", text)
        for field in ("file_id:", "original_file_id:",
                      "translation_file_id:", "game_name:",
                      "target_lang:", "apply_mode:", "repack:",
                      "gate_only:", "display_font:", "engine:"):
            self.assertIn(field, text, "workflow thieu input %s" % field)
        self.assertIn("gdown", text)
        self.assertIn("drive.google.com/uc?id=", text)
        self.assertIn("mod_large.py", text)
        self.assertIn("upload-artifact@v4", text)
        self.assertIn("actions: write", text)
        self.assertIn("contents: read", text)
        self.assertIn("timeout-minutes: 90", text)
        self.assertNotIn("git push", text)
        self.assertNotIn("secrets.", text)
        # IPA repack chi upload khi vua ngan sach artifact.
        self.assertIn("INCLUDE_REPACK", text)

    def test_workflow_yaml_parses(self):
        import pytest
        yaml = pytest.importorskip("yaml",
                                   reason="thieu pyyaml (chi chay local)")
        data = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        on = data.get("on") or data.get(True)
        self.assertIsInstance(on, dict)
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("push", on)
        self.assertNotIn("pull_request", on)
        inputs = on["workflow_dispatch"]["inputs"]
        self.assertTrue(inputs["file_id"]["required"])
        self.assertTrue(inputs["original_file_id"]["required"])
        self.assertTrue(inputs["translation_file_id"]["required"])
        self.assertTrue(inputs["game_name"]["required"])
        self.assertEqual(inputs["engine"]["default"], "unity")
        self.assertEqual(inputs["engine"]["options"], ["unity"])
        self.assertEqual(inputs["target_lang"]["default"], "vi")
        self.assertEqual(inputs["apply_mode"]["default"], "REBUILD_SECTION")
        self.assertEqual(set(data["jobs"]), {"mod"})
