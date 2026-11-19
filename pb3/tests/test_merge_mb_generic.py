"""Test gop MonoBehaviour generic da tach ngon ngu vao file locale
(merge-mb-generic, 2026-11-18).

Khoa hanh vi moi:
- outcome monobehaviour_lang (file MB tach ngon ngu: 1 file moi locale,
  key on dinh .p<path_id>, khong hau to #N) DUOC gop — nhung CHI khi
  game KHONG co nguon ngon ngu co cau truc nao (language_source /
  string_table): I2/ST thang truoc, giu chuan 9K byte-identical;
- file MB gop chua tach (generic_flat, key #N) va do chuoi tho
  (generic_flat) van KHONG gop nhu cu;
- outcome gop toan tu MB mang origin_kind monobehaviour_lang (nhan
  nguon rieng); thong ke moi locale co them "mb_keys";
- zh cua MB (khong ro bien the) ra CA zh-CN lan zh-TW, khong ra zh.json;
- _build_language_columns liet ke ca cot MB (chi khi game khong co I2/ST).

So do tren game that (PvZ 0 -> 7 file; 9K/PB3/EK2 byte-identical) nam o
measure/*.summary.json trong cay lam viec + harness exp/verify_bug2_merge.py.
"""
import json
import unittest

from sdk import extraction as ext
from sdk.extraction import (
    ExtractionResult,
    FileOutcome,
    _build_language_columns,
    _expand_merge_targets,
    _is_mb_lang_file,
    _Layer,
    _origin_kind_for,
    merge_language_outcomes,
)
from sdk.vfs import MemoryVFS


def _out(path, locale, kv, origin="text_asset", plugin="json_text",
         exported=None, source=None):
    f = FileOutcome(path, plugin, locale, entries=len(kv), ok=len(kv),
                    kv=dict(kv), source=source or path, origin_kind=origin)
    if exported:
        f.exported = exported
    return f


def _mb(path, locale, kv, **kw):
    return _out(path, locale, kv, origin=ext.MB_LANG_ORIGIN, **kw)


class MergeMbGenericCase(unittest.TestCase):
    def test_mb_only_merges_every_locale(self):
        files = [
            _mb("u::r.monobehaviour.en.json", "en",
                 {"A.s1": "one", "B.s1": "two"}, exported="r.mb.en.json"),
            _mb("u::g.monobehaviour.en.json", "en",
                 {"C.s1": "three"}, exported="g.mb.en.json"),
            _mb("u::r.monobehaviour.vi.json", "vi",
                 {"A.s1": "mot"}, exported="r.mb.vi.json"),
            _mb("u::r.monobehaviour.zh.json", "zh",
                 {"A.s1": "yi"}, exported="r.mb.zh.json"),
            _out("u::cfg.json", None, {"X": "1"}, origin="other"),
        ]
        merged, stats = merge_language_outcomes(
            files, {"en", "vi", "zh-cn", "zh-tw"})
        self.assertEqual(len(merged), 5)  # 4 file gop + 1 config
        by_loc = {f.locale: f for f in merged if f.merged}
        self.assertEqual(set(by_loc), {"en", "vi", "zh-CN", "zh-TW"})
        self.assertEqual(list(by_loc["en"].kv), ["A.s1", "B.s1", "C.s1"])
        self.assertEqual(by_loc["vi"].kv, {"A.s1": "mot"})
        # zh goc lam fallback cho CA 2 bien the (khong ra zh.json).
        self.assertEqual(by_loc["zh-CN"].kv, {"A.s1": "yi"})
        self.assertEqual(by_loc["zh-TW"].kv, {"A.s1": "yi"})
        for f in by_loc.values():
            self.assertEqual(f.origin_kind, ext.MB_LANG_ORIGIN)
        stat = {s["locale"]: s for s in stats}
        self.assertEqual(stat["en"]["keys"], 3)
        self.assertEqual(stat["en"]["mb_keys"], 3)
        self.assertEqual(stat["vi"]["mb_keys"], 1)
        self.assertEqual(stat["zh-CN"]["mb_keys"], 1)
        self.assertEqual(stat["zh-TW"]["mb_keys"], 1)
        self.assertEqual(stat["en"]["conflicts_total"], 0)

    def test_structured_presence_blocks_mb(self):
        # Game co I2: MB (ca en lan vi) deu bi chan, du vi khong co
        # trong I2 — I2 thang truoc o cap game, khong phai cap locale
        # (9K co ca bucket MB ar/de/es/... neu mo theo locale se rot).
        files = [
            _out("u::r.langtable.monobehaviour.en.json", "en",
                 {"T1": "Hi"}, origin="language_source"),
            _mb("u::r.monobehaviour.en.json", "en",
                {"Generic.s1": "junk", "T1": "Hi"}),
            _mb("u::r.monobehaviour.vi.json", "vi",
                {"Generic.s1": "rac"}),
        ]
        merged, stats = merge_language_outcomes(
            files, {"en", "vi", "zh-cn", "zh-tw"})
        self.assertEqual(len(merged), 3)
        en = merged[0]
        self.assertTrue(en.merged)
        self.assertEqual(en.kv, {"T1": "Hi"})  # khong key MB
        self.assertEqual(en.origin_kind, "text_asset")
        self.assertFalse(merged[1].merged)  # MB en passthrough
        self.assertFalse(merged[2].merged)  # MB vi passthrough
        self.assertEqual({s["locale"] for s in stats}, {"en"})
        self.assertEqual(stats[0].get("mb_keys"), 0)

    def test_structured_empty_kv_does_not_block(self):
        # Outcome co cau truc NHUNG kv rong thi khong tinh la "co nguon":
        # MB van duoc gop (khong co gi de thang).
        files = [
            _out("u::r.langtable.monobehaviour.en.json", "en",
                 {}, origin="language_source"),
            _mb("u::r.monobehaviour.en.json", "en", {"A.s1": "one"}),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        # Outcome rong giu lai lam passthrough (nhu test_empty_kv_outcome_
        # not_merged_away); file gop chi co key MB. Thu tu: passthrough
        # giu vi tri goc (dau), file gop the vi tri cua outcome MB.
        self.assertEqual(len(merged), 2)
        self.assertFalse(merged[0].merged)
        self.assertEqual(merged[0].kv, {})
        self.assertTrue(merged[1].merged)
        self.assertEqual(merged[1].kv, {"A.s1": "one"})
        self.assertEqual(stats[0]["mb_keys"], 1)

    def test_text_asset_plus_mb_mixes(self):
        # Game khong co I2/ST nhung co file locale co cau truc (JSON) +
        # MB: gop chung; nhan gop la text_asset (khong toan MB); mb_keys
        # chi dem key lay tu MB.
        files = [
            _out("u::a.en.json", "en", {"A": "one"}, exported="a.en.json"),
            _mb("u::r.monobehaviour.en.json", "en", {"B.s1": "two"},
                exported="r.mb.en.json"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(len(merged), 1)
        self.assertEqual(list(merged[0].kv), ["A", "B.s1"])
        self.assertEqual(merged[0].origin_kind, "text_asset")
        self.assertEqual(stats[0]["mb_keys"], 1)
        self.assertEqual(stats[0]["keys"], 2)

    def test_generic_flat_still_excluded(self):
        # File MB gop chua tach (generic_flat, key #N) + do chuoi tho:
        # khong gop ngay ca khi game khong co I2/ST — chi nhan MB DA
        # TACH NGON NGU (monobehaviour_lang).
        files = [
            _out("u::r.monobehaviour.json", None,
                 {"A.s1": "one", "A.s1#2": "two"}, origin="generic_flat"),
            _out("u::r.monobehaviour.en.json", "en",
                 {"B.s1": "three"}, origin="generic_flat"),
            _out("u::raw.bin", "en", {"C": "four"},
                 plugin="generic_strings", origin="generic_flat"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(stats, [])
        self.assertEqual(merged, files)
        self.assertFalse(any(f.merged for f in merged))

    def test_mb_conflict_keeps_first_and_counts_mb_keys(self):
        # Trung key giua 2 don vi MB (cung ten object + path_id): giu
        # ban dau, liet ke xung dot; mb_keys dem theo ban duoc giu.
        files = [
            _mb("u::r.monobehaviour.en.json", "en", {"A.s1": "first"}),
            _mb("u::g.monobehaviour.en.json", "en", {"A.s1": "second"}),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(merged[0].kv, {"A.s1": "first"})
        self.assertEqual(stats[0]["conflicts_total"], 1)
        self.assertEqual(stats[0]["mb_keys"], 1)


class ExpandTargetsMbCase(unittest.TestCase):
    def test_mb_locales_become_targets(self):
        self.assertEqual(
            _expand_merge_targets(
                {"en", "zh"}, {"en", "zh", "vi", "ro"},
                {"en", "vi", "ro", "zh"}),
            {"en", "vi", "ro", "zh-cn", "zh-tw"})

    def test_mb_zh_with_present_variants_uses_variants(self):
        self.assertEqual(
            _expand_merge_targets(
                {"en", "zh"}, {"en", "zh", "zh-cn", "zh-tw"}, {"zh"}),
            {"en", "zh-cn", "zh-tw"})

    def test_no_mb_arg_keeps_old_behavior(self):
        # Bang "zh" co cau truc (khong phai MB) van ra zh.json nhu cu.
        self.assertEqual(
            _expand_merge_targets({"zh"}, {"en", "zh"}), {"zh"})
        self.assertEqual(
            _expand_merge_targets({"en", "zh"}, {"en", "zh-cn", "zh-tw"}),
            {"en", "zh-cn", "zh-tw"})

    def test_mb_locale_not_in_present_ignored(self):
        self.assertEqual(
            _expand_merge_targets({"en"}, {"en"}, {"de"}), {"en"})


class MbOriginTaggingCase(unittest.TestCase):
    def test_origin_kind_for_mb_lang_file(self):
        layer = _Layer("", MemoryVFS({}), "virtual", origin_map={
            "u::r.monobehaviour.vi.json": ext.MB_LANG_ORIGIN,
            "u::r.monobehaviour.json": "generic_flat",
        })
        self.assertEqual(
            _origin_kind_for("u::r.monobehaviour.vi.json", layer, "json_text"),
            ext.MB_LANG_ORIGIN)
        self.assertEqual(
            _origin_kind_for("u::r.monobehaviour.json", layer, "json_text"),
            "generic_flat")
        self.assertTrue(_is_mb_lang_file("u::r.monobehaviour.vi.json", layer))
        self.assertFalse(_is_mb_lang_file("u::r.monobehaviour.json", layer))
        self.assertFalse(_is_mb_lang_file("u::r.monobehaviour.vi.json", None))

    def test_build_language_columns_lists_mb(self):
        virtual = {
            "u::r.monobehaviour.en.json": json.dumps(
                {"A.s1": "one"}, ensure_ascii=False).encode("utf-8"),
            "u::r.monobehaviour.zh.json": json.dumps(
                {"A.s1": "yi"}, ensure_ascii=False).encode("utf-8"),
        }
        layer = _Layer("", MemoryVFS(virtual), "virtual", origin_map={
            "u::r.monobehaviour.en.json": ext.MB_LANG_ORIGIN,
            "u::r.monobehaviour.zh.json": ext.MB_LANG_ORIGIN,
        })
        res = ExtractionResult(ok=True, decision="done")
        merged_en = _mb("<merged:en>", "en", {"A.s1": "one"})
        merged_en.merged = True
        merged_zh = _mb("<merged:zh-CN>", "zh-CN", {"A.s1": "yi"})
        merged_zh.merged = True
        res.files = [merged_en, merged_zh]
        _build_language_columns([layer], res, {"en", "zh"})
        cols = {(c["unit"], c["code"]): c for c in res.language_columns}
        self.assertEqual(cols[("u", "en")]["status"], "exported")
        self.assertEqual(cols[("u", "en")]["keys"], 1)
        # Cot "zh" goc tinh la exported nho bien the zh-CN da gop.
        self.assertEqual(cols[("u", "zh")]["status"], "exported")

    def test_build_language_columns_skips_mb_when_structured(self):
        # Game co I2: ban ke KHONG them hang MB (giu nguyen nhu cu).
        virtual = {
            "u::r.monobehaviour.en.json": json.dumps(
                {"A.s1": "one"}, ensure_ascii=False).encode("utf-8"),
            "u::r.langtable.monobehaviour.en.json": json.dumps(
                {"T1": "Hi"}, ensure_ascii=False).encode("utf-8"),
        }
        layer = _Layer("", MemoryVFS(virtual), "virtual", origin_map={
            "u::r.monobehaviour.en.json": ext.MB_LANG_ORIGIN,
            "u::r.langtable.monobehaviour.en.json": "language_source",
        })
        res = ExtractionResult(ok=True, decision="done")
        merged_en = _out("<merged:en>", "en", {"T1": "Hi"},
                         origin="language_source")
        merged_en.merged = True
        res.files = [merged_en]
        _build_language_columns([layer], res, {"en", "zh"})
        codes = [(c["unit"], c["code"]) for c in res.language_columns]
        self.assertEqual(codes, [("u", "en")])
        self.assertEqual(res.language_columns[0]["status"], "exported")


# --- Hoi quy: file .assets tho > tran streaming khong duoc skip ---
# (2026-11-18, merge-mb-generic) caps-po dua moi entry Unity >
# stream_entry_bytes (32 MiB) sang duong streaming, nhung
# _expand_unity_stream chi biet doc UnityFS bundle: file .assets tho
# (SerializedFile, vd PvZ resources.assets 34 MB) bi E_UNSUPPORTED_FORMAT
# va ca file bien mat khoi ket qua (0 key). Fix: sniff magic 16 byte
# dau — khong phai bundle thi ve duong cu (doc tron + _expand_unity).
# Test nay ha tran streaming xuong 64 byte de fixture .assets nho
# (6.763 byte) cung di qua nhanh do, roi assert 2 TextAsset cua no
# van xuat hien va file khong bi skip. Fixture chi co local (giai ma
# tu .b64) nen skip khi thieu — giong pattern needs_fixture cua repo.
import os as _os
import shutil as _shutil
import tempfile as _tempfile
import zipfile as _zipfile
from pathlib import Path as _Path

from core.app import bootstrap as _bootstrap
from core.limits import Limits as _Limits
from services import language_tool as _language_tool
from tests.helpers import TmpCase as _TmpCase
from tests.helpers import copy_project as _copy_project
from tests.helpers import register_tmp_dir as _register_tmp_dir
from tests.helpers import unregister_tmp_dir as _unregister_tmp_dir

_FIXTURE_ASSETS = _Path(__file__).resolve().parent / "fixtures" / "unity" / "resources.assets"


class StreamingAssetsFallbackCase(_TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = _tempfile.mkdtemp(prefix="pb3-merge-mb-")
        _register_tmp_dir(cls._shared_td)
        cls._shared_proj = _copy_project(_Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        _shutil.rmtree(cls._shared_td, ignore_errors=True)
        _unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = _bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = _language_tool._registry(self.app)
        self.out = self.tmp / "out"

    @unittest.skipIf(not _FIXTURE_ASSETS.exists(),
                     "fixture resources.assets chua giai ma (needs_fixture)")
    def test_raw_assets_over_stream_threshold_not_skipped(self):
        src = self.tmp / "game.zip"
        with _zipfile.ZipFile(src, "w", _zipfile.ZIP_DEFLATED) as zf:
            zf.write(_FIXTURE_ASSETS, "resources.assets")
        lim = _Limits(stream_entry_bytes=64)  # ep .assets 6.763 byte vao nhanh streaming
        res = ext.run_extraction(ext.ExtractionRequest(
            source=str(src), registry=self.reg, limits=lim,
            tempdir=str(self.app.paths.temp), root=str(self._shared_proj),
            out_dir=str(self.out)))
        # Entry .assets phai vuot qua tang layers (khong bi skip voi loi
        # streaming). Bang chung no da duoc parse: 2 TextAsset cua no
        # xuat hien trong skipped o vong candidate (diem thap 0.16/0.28
        # < 0.55 — loai dung quy trinh, khong phai loi mo file). Truoc
        # fix, ca entry bi skip voi "khong mo duoc file Unity (streaming)
        # [E_UNSUPPORTED_FORMAT]" va khong co muc ::greeting nao.
        self.assertFalse(
            [s for s in res.skipped if s.get("path") == "resources.assets"],
            res.skipped)
        self.assertFalse(
            [s for s in res.skipped if "streaming" in s.get("reason", "")],
            res.skipped)
        skipped_paths = [s.get("path", "") for s in res.skipped]
        self.assertIn("resources.assets::greeting", skipped_paths)
        self.assertIn("resources.assets::longtext", skipped_paths)


if __name__ == "__main__":
    unittest.main()
