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


if __name__ == "__main__":
    unittest.main()
