"""Test sửa BUG#2 đường merge trích xuất (2026-11-17).

Khóa hành vi mới sau sửa:
- merge chỉ gộp outcome có cấu trúc (language_source / string_table /
  text_asset); outcome generic_flat và locale:None không lọt vào file
  locale (rác vứt hết, không giữ _non_localization.json);
- zh-CN/zh-TW là merge target riêng; bảng mã gốc (zh) chỉ làm fallback
  sau biến thể (giữ giá trị thật của bảng gốc, không để trống);
- key rỗng được lấp bằng giá trị thật sau đó (filled_empty), không tính
  xung đột; tên file gộp dùng mã chuẩn (zh-CN.json);
- chế độ gộp chỉ ghi file locale ra out_dir (không sidecar/provenance/
  conflicts/checkpoint/report);
- bảng ngôn ngữ liệt kê đủ mọi cột (zh-CN/zh-TW/bg/fil/nb là mã riêng);
- StringTable gộp nhóm Collection/Key, điền đủ vũ trụ SharedTableData;
- dòng CSV hoàn toàn trống bị bỏ (không sinh key row_NNNNNN rỗng).

Số đo trên game thật nằm ở harness exp/verify_bug2_merge.py (Build 68
2086 key == en-build68.json; v1.0.6 2252 key/locale khớp mẫu; PB3 2348;
EK2 1344).
"""
import json
import os
import tempfile
import unittest

from sdk import discovery as disc
from sdk.extraction import (
    ExtractionRequest,
    ExtractionResult,
    FileOutcome,
    _apply_merge,
    _expand_merge_targets,
    _export,
    _fill_string_table_universe,
    _language_source_all_columns,
    _language_source_virtual_files,
    join_string_tables_grouped,
    merge_language_outcomes,
)
from sdk.rebuild import _collect_mb_keys


def _out(path, locale, kv, origin="text_asset", plugin="json_text",
         exported=None):
    f = FileOutcome(path, plugin, locale, entries=len(kv), ok=len(kv),
                    kv=dict(kv), source=path, origin_kind=origin)
    if exported:
        f.exported = exported
    return f


class MergeExcludesGenericCase(unittest.TestCase):
    def test_generic_flat_not_merged_into_locale(self):
        files = [
            _out("u::r.langtable.monobehaviour.en.json", "en",
                 {"T1": "Hi"}, origin="language_source"),
            _out("u::r.monobehaviour.en.json", "en",
                 {"Generic.s1": "junk", "T1": "Hi"},
                 origin="generic_flat"),
            _out("u::cfg.json", None, {"X": "1"}, origin="other"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        # merge_language_outcomes (hàm thuần) GIỮ outcome passthrough
        # (generic/config) trong danh sách — việc loại chúng khỏi đĩa là
        # của _export ở chế độ gộp (xem ExportMergeModeCase). Điều khóa
        # ở đây: file gộp en CHỈ có key từ nguồn có cấu trúc.
        self.assertEqual(len(merged), 3)
        en = merged[0]
        self.assertTrue(en.merged)
        self.assertEqual(en.kv, {"T1": "Hi"})  # không key Generic.s1
        self.assertFalse(merged[1].merged)  # generic passthrough, không gộp
        self.assertEqual(stats[0]["keys"], 1)
        self.assertEqual(stats[0]["sources"],
                         ["u::r.langtable.monobehaviour.en.json"])

    def test_other_origin_not_merged(self):
        files = [
            _out("u::a.plist", None, {"K": "v"}, origin="other"),
        ]
        merged, stats = merge_language_outcomes(files, {"en"})
        self.assertEqual(merged, files)
        self.assertEqual(stats, [])


class VariantFallbackCase(unittest.TestCase):
    def test_base_zh_falls_back_after_variants(self):
        files = [
            _out("u::r.langtable.monobehaviour.zh-cn.json", "zh-CN",
                 {"T1": "你好"}, origin="language_source"),
            _out("u::r.langtable.monobehaviour.zh-tw.json", "zh-TW",
                 {"T1": "你好"}, origin="language_source"),
            # Bảng StringTable duy nhất của game mang mã gốc "zh".
            _out("u::st.zh.json", "zh",
                 {"Policies/A": "策略", "Policies/B": ""},
                 origin="string_table"),
        ]
        merged, stats = merge_language_outcomes(files, {"zh-cn", "zh-tw"})
        by_locale = {f.locale: f for f in merged if f.merged}
        self.assertEqual(set(by_locale), {"zh-CN", "zh-TW"})
        self.assertEqual(by_locale["zh-CN"].kv,
                         {"T1": "你好", "Policies/A": "策略",
                          "Policies/B": ""})
        self.assertEqual(by_locale["zh-TW"].kv,
                         {"T1": "你好", "Policies/A": "策略",
                          "Policies/B": ""})
        # Không còn file zh.json riêng (mã gốc chỉ là fallback).
        self.assertFalse(any(f.locale == "zh" for f in merged))

    def test_variant_wins_over_base_fallback(self):
        files = [
            _out("u::lt.zh-tw.json", "zh-TW", {"T1": "你好（繁）"},
                 origin="language_source"),
            _out("u::lt.zh.json", "zh", {"T1": "你好"},
                 origin="language_source"),
        ]
        merged, _stats = merge_language_outcomes(files, {"zh-tw"})
        # Knightica (2026-11-19): bảng "zh" có cấu trúc (giản thể) KHÔNG
        # còn bị lấp vào file phồn thể khi đích đã có bảng cùng họ nguồn —
        # outcome "zh" đi qua nguyên vẹn (không thuộc target), file gộp
        # zh-TW giữ đúng giá trị phồn thể.
        by_merged = [f for f in merged if f.merged]
        self.assertEqual(len(by_merged), 1)
        self.assertEqual(by_merged[0].locale, "zh-TW")
        self.assertEqual(by_merged[0].kv, {"T1": "你好（繁）"})
        self.assertEqual([f.locale for f in merged if not f.merged], ["zh"])

    def test_structured_zh_not_folded_into_same_family_traditional(self):
        # Knightica (2026-11-19): bảng StringTable "zh" (giản thể) + bảng
        # StringTable "zh-hant" (phồn thể) cùng họ nguồn — giản thể KHÔNG
        # được lấp vào file phồn thể; xin zh ra 2 file riêng.
        files = [
            _out("u::st.zh.json", "zh",
                 {"K1": "游戏", "K2": "开始"}, origin="string_table"),
            _out("u::st.zh-hant.json", "zh-hant",
                 {"K1": "遊戲", "K3": "設定"}, origin="string_table"),
        ]
        merged, stats = merge_language_outcomes(files, {"zh-cn", "zh-hant"})
        by_locale = {f.locale: f for f in merged if f.merged}
        self.assertEqual(set(by_locale), {"zh-CN", "zh-Hant"})
        self.assertEqual(by_locale["zh-CN"].kv,
                         {"K1": "游戏", "K2": "开始"})
        self.assertEqual(by_locale["zh-Hant"].kv,
                         {"K1": "遊戲", "K3": "設定"})
        by_stat = {s["locale"]: s for s in stats}
        self.assertEqual(by_stat["zh-CN"]["file"], "zh-CN.json")
        self.assertEqual(by_stat["zh-Hant"]["file"], "zh-Hant.json")
        self.assertEqual(by_stat["zh-CN"]["conflicts_total"], 0)
        self.assertEqual(by_stat["zh-Hant"]["conflicts_total"], 0)

    def test_structured_zh_single_traditional_target_stays_pure(self):
        # Xin thẳng zh-hant khi game có ST zh + ST zh-hant: file phồn thể
        # chỉ chứa dữ liệu phồn thể, không lấp key giản thể.
        files = [
            _out("u::st.zh.json", "zh",
                 {"K1": "游戏", "K2": "开始"}, origin="string_table"),
            _out("u::st.zh-hant.json", "zh-hant",
                 {"K1": "遊戲", "K3": "設定"}, origin="string_table"),
        ]
        merged, _stats = merge_language_outcomes(files, {"zh-hant"})
        by_merged = {f.locale: f for f in merged if f.merged}
        self.assertEqual(set(by_merged), {"zh-Hant"})
        self.assertEqual(by_merged["zh-Hant"].kv,
                         {"K1": "遊戲", "K3": "設定"})
        # Outcome "zh" (giản thể) đi qua nguyên vẹn, không lọt vào file gộp.
        self.assertEqual([f.locale for f in merged if not f.merged], ["zh"])

    def test_prefer_non_empty_fills_empty(self):
        files = [
            _out("u::lt.zh-tw.json", "zh-TW", {"K": ""},
                 origin="language_source"),
            _out("u::st.zh.json", "zh", {"K": "thật"},
                 origin="string_table"),
        ]
        merged, stats = merge_language_outcomes(files, {"zh-tw"})
        self.assertEqual(merged[0].kv, {"K": "thật"})
        self.assertEqual(stats[0]["filled_empty"], 1)
        self.assertEqual(stats[0]["conflicts_total"], 0)

    def test_canonical_locale_in_outcome_and_stats(self):
        files = [_out("u::lt.zh-cn.json", "zh-CN", {"T1": "你好"},
                      origin="language_source")]
        merged, stats = merge_language_outcomes(files, {"zh-cn"})
        self.assertEqual(merged[0].locale, "zh-CN")
        self.assertEqual(stats[0]["locale"], "zh-CN")
        self.assertEqual(stats[0]["file"], "zh-CN.json")


class ExpandMergeTargetsCase(unittest.TestCase):
    def test_base_request_expands_to_present_variants(self):
        self.assertEqual(
            _expand_merge_targets({"en", "zh"}, {"en", "zh-cn", "zh-tw"}),
            {"en", "zh-cn", "zh-tw"})

    def test_only_base_present_keeps_base_target(self):
        self.assertEqual(
            _expand_merge_targets({"zh"}, {"en", "zh"}), {"zh"})

    def test_explicit_variant_stays_variant(self):
        self.assertEqual(
            _expand_merge_targets({"zh-cn"}, {"zh-cn", "zh-tw"}), {"zh-cn"})

    def test_base_and_variant_present_keeps_base_as_simplified(self):
        # Knightica (2026-11-19): dữ liệu có cả bảng "zh" (giản thể) lẫn
        # "zh-hant" (phồn thể) — xin zh ra cả 2 target, "zh" thành "zh-cn".
        self.assertEqual(
            _expand_merge_targets({"en", "zh"}, {"en", "zh", "zh-hant"}),
            {"en", "zh-cn", "zh-hant"})

    def test_explicit_simplified_variant_uses_base_zh(self):
        # Xin thẳng zh-cn mà game chỉ có bảng "zh": bảng đó là giản thể.
        self.assertEqual(
            _expand_merge_targets({"zh-cn"}, {"en", "zh"}), {"zh-cn"})
        self.assertEqual(
            _expand_merge_targets({"zh-hans"}, {"en", "zh"}), {"zh-hans"})

    def test_explicit_traditional_variant_not_fabricated_from_base_zh(self):
        # Xin thẳng zh-tw mà game chỉ có bảng "zh" (giản thể): không bịa
        # file phồn thể từ dữ liệu giản thể.
        self.assertEqual(
            _expand_merge_targets({"zh-tw"}, {"en", "zh"}), set())


class ExportMergeModeCase(unittest.TestCase):
    def test_merge_mode_writes_only_locale_files(self):
        res = ExtractionResult(ok=True, decision="done", total_entries=0)
        merged_en = _out("<merged:en>", "en", {"T1": "Hi"},
                         origin="language_source")
        merged_en.merged = True
        merged_en.merged_from = ["u::a.en.json"]
        merged_zh = _out("<merged:zh-CN>", "zh-CN", {"T1": "你好"},
                         origin="language_source")
        merged_zh.merged = True
        merged_zh.merged_from = ["u::a.zh-cn.json"]
        junk = _out("u::cfg.json", None, {"X": "1"}, origin="other")
        res.files = [merged_en, merged_zh, junk]
        res.merged_files = [
            {"locale": "en", "file": "en.json", "keys": 1,
             "duplicates_identical": 0, "conflicts": [],
             "conflicts_total": 0},
            {"locale": "zh-CN", "file": "zh-CN.json", "keys": 1,
             "duplicates_identical": 0, "conflicts": [],
             "conflicts_total": 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            req = ExtractionRequest(source="game.ipa", out_dir=tmp,
                                    merge_one_file=True)
            _export(req, res, "deadbeef", [])
            self.assertEqual(sorted(os.listdir(tmp)),
                             ["en.json", "zh-CN.json"])
            self.assertEqual(json.load(open(os.path.join(tmp, "en.json"))),
                             {"T1": "Hi"})
            self.assertEqual(
                json.load(open(os.path.join(tmp, "zh-CN.json"))),
                {"T1": "你好"})

    def test_split_mode_still_writes_sidecar_and_reports(self):
        # Chế độ tách (merge_one_file=False) giữ hành vi cũ: có sidecar
        # provenance + checkpoint + report.
        res = ExtractionResult(ok=True, decision="done", total_entries=0)
        merged_en = _out("<merged:en>", "en", {"T1": "Hi"},
                         origin="language_source")
        merged_en.merged = True
        merged_en.merged_from = ["u::a.en.json"]
        res.files = [merged_en]
        res.merged_files = [
            {"locale": "en", "file": "en.json", "keys": 1,
             "duplicates_identical": 0, "conflicts": [],
             "conflicts_total": 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            req = ExtractionRequest(source="game.ipa", out_dir=tmp)
            _export(req, res, "deadbeef", [])
            names = sorted(os.listdir(tmp))
            self.assertIn("en.json", names)
            self.assertIn("en.provenance.json", names)
            self.assertIn("extraction_checkpoint.json", names)
            self.assertIn("extraction_report.json", names)


class LanguageColumnsCase(unittest.TestCase):
    def _item(self, terms, codes):
        return {"path_id": 1, "name": "LS", "kind": "language_source",
                "languages": [{"name": "L%d" % i, "code": c}
                              for i, c in enumerate(codes)],
                "terms": [{"term": t, "values": v} for t, v in terms],
                "trailing_bytes": 0}

    def test_all_columns_listed_per_variant(self):
        item = self._item([("T1", ["Hi", "你好", "你好"])],
                          ["en", "zh-TW", "zh-CN"])
        cols, n_nonlocale = _language_source_all_columns(item)
        # Mã cột ở dạng thường (khớp tên file ảo .monobehaviour.zh-cn.json);
        # bước gộp chuẩn hóa lại thành zh-CN/zh-TW.
        self.assertEqual(cols, {"en": 0, "zh-tw": 1, "zh-cn": 2})
        self.assertEqual(n_nonlocale, 0)

    def test_duplicate_code_counts_as_nonlocale(self):
        item = self._item([("T1", ["Hi", "Hi"])], ["en", "en"])
        cols, n_nonlocale = _language_source_all_columns(item)
        self.assertEqual(cols, {"en": 0})
        self.assertEqual(n_nonlocale, 1)

    def test_virtual_files_one_per_variant(self):
        item = self._item([("T1", ["Hi", "你好", "你好"]),
                           ("T2", ["", "X", "Y"])],
                          ["en", "zh-TW", "zh-CN"])
        out = _language_source_virtual_files(
            [("u/resources.assets", "", item)], lambda *a: None)
        self.assertEqual(
            sorted(out),
            ["u/resources.assets::resources.langtable.monobehaviour.en.json",
             "u/resources.assets::resources.langtable.monobehaviour.zh-cn.json",
             "u/resources.assets::resources.langtable.monobehaviour.zh-tw.json"])
        self.assertEqual(
            json.loads(out["u/resources.assets::resources.langtable.monobehaviour.zh-cn.json"]),
            {"T1": "你好", "T2": "Y"})
        self.assertEqual(
            json.loads(out["u/resources.assets::resources.langtable.monobehaviour.zh-tw.json"]),
            {"T1": "你好", "T2": "X"})


class LocaleDetectionCase(unittest.TestCase):
    def test_variants_are_own_codes(self):
        code, _c, _e = disc.detect_locale("x.langtable.monobehaviour.zh-cn.json")
        self.assertEqual(code, "zh-CN")
        code, _c, _e = disc.detect_locale("x.langtable.monobehaviour.zh-tw.json")
        self.assertEqual(code, "zh-TW")

    def test_bg_fil_nb_detected(self):
        for name, want in [("a.langtable.monobehaviour.bg.json", "bg"),
                           ("a.langtable.monobehaviour.fil.json", "fil"),
                           ("a.langtable.monobehaviour.nb.json", "nb")]:
            code, _c, _e = disc.detect_locale(name)
            self.assertEqual(code, want, name)

    def test_locale_in_wanted_family_match(self):
        self.assertTrue(disc.locale_in_wanted("zh-CN", {"zh"}))
        self.assertTrue(disc.locale_in_wanted("zh-TW", {"zh"}))
        self.assertTrue(disc.locale_in_wanted("zh", {"zh"}))
        self.assertFalse(disc.locale_in_wanted("zh-TW", {"zh-CN"}))
        self.assertFalse(disc.locale_in_wanted("en", {"zh"}))
        self.assertTrue(disc.locale_in_wanted("en", {"en"}))

    def test_canonical_locale_code(self):
        self.assertEqual(disc.canonical_locale_code("zh-cn"), "zh-CN")
        self.assertEqual(disc.canonical_locale_code("zh-tw"), "zh-TW")
        self.assertEqual(disc.canonical_locale_code("en"), "en")


class StringTableUniverseCase(unittest.TestCase):
    def test_fill_creates_missing_collection_buckets(self):
        grouped = {("b", "zh", "Policies"): {"A": "x"}}
        shared = {"Policies": {1: "A", 2: "B"},
                  "Buildings": {1: "Farm", 2: "Castle"}}
        out, filled = _fill_string_table_universe(grouped, shared)
        self.assertEqual(out[("b", "zh", "Policies")], {"A": "x", "B": ""})
        self.assertEqual(out[("b", "zh", "Buildings")],
                         {"Farm": "", "Castle": ""})
        self.assertEqual(filled, 3)

    def test_fill_does_not_overwrite_real_values(self):
        grouped = {("b", "en", "Policies"): {"A": "real"}}
        shared = {"Policies": {1: "A"}}
        out, filled = _fill_string_table_universe(grouped, shared)
        self.assertEqual(out[("b", "en", "Policies")], {"A": "real"})
        self.assertEqual(filled, 0)


class StringTableOrderCase(unittest.TestCase):
    """Thứ tự StringTable khớp file mẫu P0-3 (2026-11-17, fix sau BUG#2).

    Đo thật trên 9 Kings v1.0.6: file mẫu xếp key trong mỗi collection
    theo m_Id tăng dần của SharedTableData (đúng cả 6 collection; 2
    collection phân biệt Buildings/Spells — thứ tự asset của StringTable
    lẫn SharedTableData đều khác mẫu), và xếp collection theo lần gặp
    đầu trong SharedTableData (Policies, Buildings, ...), không theo
    chữ cái. Test dưới đây khóa đúng 3 quy tắc đó bằng dữ liệu tổng hợp
    (m_Id cố tình lệch thứ tự asset để phân biệt)."""

    def _st(self, name, locale, entries):
        return {"name": name, "locale": locale, "entries": dict(entries)}

    def _shared(self, name, coll, entries):
        return {"name": name, "collection": coll, "entries": dict(entries)}

    def test_join_orders_keys_by_shared_m_id(self):
        # Asset order: Farm(30), Castle(10), Concabulator(20) — m_Id
        # order phải là Castle, Concabulator, Farm (dù ST đọc ngược).
        shared = [self._shared("B Shared", "Buildings",
                               {30: "Farm", 10: "Castle", 20: "Concabulator"})]
        tables = [("bun", self._st("Buildings_en", "en",
                                   {20: "Concabulator", 30: "Farm", 10: "Castle"}))]
        grouped, _n, _c, _u = join_string_tables_grouped(tables, shared)
        self.assertEqual(list(grouped[("bun", "en", "Buildings")]),
                         ["Castle", "Concabulator", "Farm"])

    def test_join_id_fallback_keys_sort_by_numeric_id(self):
        shared = [self._shared("B Shared", "Buildings", {1: "A", 20: "B"})]
        tables = [("bun", self._st("Buildings_en", "en",
                                   {20: "B", 9: "Orphan", 1: "A"}))]
        grouped, _n, _c, _u = join_string_tables_grouped(tables, shared)
        self.assertEqual(list(grouped[("bun", "en", "Buildings")]),
                         ["A", "id_9", "B"])

    def test_fill_places_filled_keys_by_m_id_not_appended(self):
        # Key điền "" (A, m_Id 5) phải nằm GIỮA theo m_Id, không dồn cuối.
        grouped = {("b", "en", "Buildings"): {"B": "x", "C": "y"}}
        shared = {"Buildings": {5: "A", 10: "B", 15: "C"}}
        out, filled = _fill_string_table_universe(grouped, shared)
        self.assertEqual(list(out[("b", "en", "Buildings")]), ["A", "B", "C"])
        self.assertEqual(out[("b", "en", "Buildings")]["A"], "")
        self.assertEqual(filled, 1)

    def test_fill_orders_collections_by_shared_first_seen(self):
        # grouped gặp Buildings trước, nhưng shared gặp Policies trước
        # -> Policies phải đứng trước Buildings (khớp file mẫu).
        grouped = {("b", "en", "Buildings"): {"Farm": "x"},
                   ("b", "en", "Policies"): {"Arcane": "y"}}
        shared = {"Policies": {1: "Arcane"},
                  "Buildings": {1: "Farm"}}
        out, _filled = _fill_string_table_universe(grouped, shared)
        self.assertEqual([t[2] for t in out],
                         ["Policies", "Buildings"])


class RebuildGroupedKeysCase(unittest.TestCase):
    def test_collect_mb_keys_has_grouped_forms(self):
        class FakeSF:
            def monobehaviour_texts(self):
                return [{"kind": "shared_table", "collection": "Policies",
                         "entries": {1: "ArcanePinnacle", 2: "Farshot"}},
                        {"kind": "language_source",
                         "terms": [{"term": "T1"}]}]

        terms = _collect_mb_keys([FakeSF()])
        self.assertIn("ArcanePinnacle", terms)
        self.assertIn("Policies/ArcanePinnacle", terms)
        self.assertIn("Policies/Farshot", terms)
        self.assertIn("T1", terms)


class CsvBlankRowsCase(unittest.TestCase):
    def test_fully_blank_rows_skipped_keyless_with_values_kept(self):
        from core.limits import Limits
        from plugins.csv_table.plugin import CsvPlugin
        from sdk.api import PluginContext
        from sdk.types import Candidate
        from sdk.vfs import MemoryVFS
        from tests.helpers import TmpCase

        class _T(TmpCase):
            def runTest(self):
                pass

        t = _T("runTest")
        t.setUp()
        try:
            data = (b"Key,English,Notes\n"
                    b"HELLO,Hello,\n"
                    b",,,\n"
                    b",No key value,\n")
            plug = CsvPlugin()
            vfs = MemoryVFS({"a.csv": data})
            ctx = PluginContext(vfs, Limits(), None, None, str(t.tmp),
                                True, None, None, "csv_table")
            ctx = ctx.for_plugin(plug.manifest)
            table = plug.parse(ctx, Candidate("a.csv", plug.id, 0.9, []))
            keys = [e.key for e in table.entries]
            self.assertNotIn("row_000001", keys)  # dòng trống hẳn: bỏ
            self.assertIn("HELLO", keys)
            self.assertIn("row_000002", keys)  # key rỗng nhưng có giá trị: giữ
        finally:
            t.tearDown()


if __name__ == "__main__":
    unittest.main()
