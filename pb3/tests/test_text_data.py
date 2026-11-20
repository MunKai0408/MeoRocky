"""Test bang text dang danh sach (text-data list) — phuong phap 5 buoc
cua anh Duy (14:00-14:01 +07 2026-11-19, game Nhiet Huyet Sinh Ton
v1.0.3): StringTable chi 2 key (ngo cut), quet bundle tim duoc
MonoBehaviour LocalizationTextData, m_DataList 2.279 entry, 15 cot
ngon ngu, en.json la cot en trich rieng; kiem chung 2 chieu (0 trung /
0 rong luc trich, placeholder + xuong dong + the <move>/<punch> khop
100% sau dich).

Fixture tu dung bang dict (khong dung file game that, khong can mang).
So lieu Kusa that chi dung trong script kiem chung ngoai repo
(probe3_detector.py), khong phai test don vi.
"""
import json
import unittest

from core.errors import PB3Error
from sdk import exporters as exp
from sdk import extraction as ext
from sdk import gate
from sdk import text_data as td

# 15 cot nhu Kusa that (ja, en, fr, frc, it, de, es, zhs, zht, ko, ru,
# tr, ar, pt, nl) — zhs/zht la viet tat Unity Localization.
KUSA_COLS = ["ja", "en", "fr", "frc", "it", "de", "es", "zhs", "zht",
             "ko", "ru", "tr", "ar", "pt", "nl"]
KUSA_COLS_CANON = ["ja", "en", "fr", "frc", "it", "de", "es", "zh-CN",
                   "zh-TW", "ko", "ru", "tr", "ar", "pt", "nl"]
GROUPS = ["LocalizationTextData_UI", "LocalizationTextData_Scenario",
          "LocalizationTextData_Skill"]


def _kusa_val(n=12, list_field="m_DataList", key_field="key",
              group_field="fileName", cols=None):
    """Dict type tree dang Kusa: n entry, moi entry key + nhom + cot
    ngon ngu (gia tri "en#i" de phan biet)."""
    cols = cols if cols is not None else KUSA_COLS
    entries = []
    for i in range(n):
        e = {key_field: "Key_%02d" % i}
        if group_field:
            e[group_field] = GROUPS[i % len(GROUPS)]
        for c in cols:
            e[c] = "%s#%d" % (c, i)
        entries.append(e)
    return {"m_Name": "LocalizationTextData", list_field: entries}


def _kusa_item(n=12, name="LocalizationTextData", **kw):
    """Item text_data_list nhu sdk/unity.monobehaviour_texts tra ve."""
    val = _kusa_val(n=n, **kw)
    found = td.text_data_list_from_fields(val)
    assert found is not None
    found["path_id"] = -1
    found["name"] = name
    found["kind"] = "text_data_list"
    return found


class TestColumnCode(unittest.TestCase):
    def test_unity_shorthands(self):
        self.assertEqual(td.normalize_text_column_code("zhs"), "zh-CN")
        self.assertEqual(td.normalize_text_column_code("zht"), "zh-TW")
        self.assertEqual(td.normalize_text_column_code("zh_hans"), "zh-CN")
        self.assertEqual(td.normalize_text_column_code("zh-Hant"), "zh-TW")

    def test_plain_codes(self):
        self.assertEqual(td.normalize_text_column_code("en"), "en")
        self.assertEqual(td.normalize_text_column_code("pt-BR"), "pt-br")
        self.assertEqual(td.normalize_text_column_code("frc"), "frc")
        self.assertEqual(td.normalize_text_column_code("zh_CN"), "zh-cn")

    def test_not_a_locale_code(self):
        for bad in ("", "Notes", "Status", "name", "fileName", "m_Id",
                    "en_US_extra", "123", None, 42):
            self.assertIsNone(td.normalize_text_column_code(bad), bad)


class TestDetector(unittest.TestCase):
    def test_kusa_shaped(self):
        found = td.text_data_list_from_fields(_kusa_val())
        self.assertIsNotNone(found)
        self.assertEqual(found["list_field"], "m_DataList")
        self.assertEqual(found["key_field"], "key")
        self.assertEqual(found["group_field"], "fileName")
        self.assertEqual(found["columns"], KUSA_COLS_CANON)
        self.assertEqual(len(found["entries"]), 12)
        self.assertEqual(found["entries"][0]["key"], "Key_00")
        self.assertEqual(found["entries"][0]["group"],
                         "LocalizationTextData_UI")
        self.assertEqual(found["entries"][0]["values"]["en"], "en#0")
        self.assertEqual(found["entries"][0]["values"]["zh-CN"], "zhs#0")
        self.assertEqual(found["entries"][0]["values"]["zh-TW"], "zht#0")
        self.assertEqual(found["groups"],
                         {"LocalizationTextData_UI": 4,
                          "LocalizationTextData_Scenario": 4,
                          "LocalizationTextData_Skill": 4})

    def test_field_names_not_hardcoded(self):
        # Ten field khac (Entries / Key / category) nhung cau truc giong
        # thi van nhan — khong hardcode m_DataList / key / fileName.
        val = _kusa_val(list_field="Entries", key_field="Key",
                        group_field="category",
                        cols=["en", "ja", "de"])
        found = td.text_data_list_from_fields(val)
        self.assertIsNotNone(found)
        self.assertEqual(found["list_field"], "Entries")
        self.assertEqual(found["key_field"], "Key")
        self.assertEqual(found["group_field"], "category")
        self.assertEqual(found["columns"], ["en", "ja", "de"])

    def test_no_group_field_ok(self):
        val = _kusa_val(group_field=None, cols=["en", "ja"])
        found = td.text_data_list_from_fields(val)
        self.assertIsNotNone(found)
        self.assertIsNone(found["group_field"])
        self.assertEqual(found["groups"], {})
        self.assertIsNone(found["entries"][0]["group"])

    def test_master_data_not_matched(self):
        # List du lieu thuan: entry co m_Id so + truong so, khong co key
        # chuoi — phai roi ve generic, khong duoc nhan la bang text.
        val = {"m_Name": "MasterEquipmentGradeSkillLotteryData",
               "m_DataList": [
                   {"m_Id": i, "m_Grade": i % 5, "m_Rate": 0.5}
                   for i in range(12)]}
        self.assertIsNone(td.text_data_list_from_fields(val))

    def test_too_few_entries(self):
        self.assertIsNone(td.text_data_list_from_fields(_kusa_val(n=9)))

    def test_single_locale_column(self):
        self.assertIsNone(
            td.text_data_list_from_fields(_kusa_val(cols=["en"])))

    def test_non_dict_entry(self):
        val = _kusa_val()
        val["m_DataList"][3] = "not a dict"
        self.assertIsNone(td.text_data_list_from_fields(val))

    def test_key_missing_or_empty(self):
        val = _kusa_val()
        del val["m_DataList"][2]["key"]
        self.assertIsNone(td.text_data_list_from_fields(val))
        val = _kusa_val()
        val["m_DataList"][2]["key"] = ""
        self.assertIsNone(td.text_data_list_from_fields(val))

    def test_column_value_not_string(self):
        val = _kusa_val(cols=["en", "ja"])
        val["m_DataList"][1]["ja"] = 42
        self.assertIsNone(td.text_data_list_from_fields(val))

    def test_not_a_dict(self):
        self.assertIsNone(td.text_data_list_from_fields(None))
        self.assertIsNone(td.text_data_list_from_fields([]))
        self.assertIsNone(td.text_data_list_from_fields({"m_DataList": "x"}))

    def test_dup_keys_still_detected(self):
        # Key trung KHONG loai o tang nhan dien — tang trich xuat se
        # assert va fail-closed (xem TestTextDataVirtualFiles).
        val = _kusa_val(cols=["en", "ja"])
        val["m_DataList"][1]["key"] = val["m_DataList"][0]["key"]
        found = td.text_data_list_from_fields(val)
        self.assertIsNotNone(found)
        self.assertEqual(len(found["entries"]), 12)

    def test_empty_value_still_detected(self):
        val = _kusa_val(cols=["en", "ja"])
        val["m_DataList"][1]["ja"] = ""
        found = td.text_data_list_from_fields(val)
        self.assertIsNotNone(found)

    def test_same_canonical_code_first_field_wins(self):
        # "zhs" + "zh-hans" cung anh xa ve zh-CN: chi giu cot gap dau.
        val = _kusa_val(cols=["en", "zhs", "zh-hans"])
        found = td.text_data_list_from_fields(val)
        self.assertIsNotNone(found)
        self.assertEqual(found["columns"], ["en", "zh-CN"])
        self.assertEqual(found["entries"][0]["values"]["zh-CN"], "zhs#0")


class TestTextDataVirtualFiles(unittest.TestCase):
    def test_files_per_column_keep_order(self):
        item = _kusa_item(cols=["en", "ja", "zhs"])
        logs = []
        out = ext._text_data_list_virtual_files(
            [("u/b.bundle", "CAB-abc", item)], logs.append)
        self.assertEqual(
            sorted(out),
            ["u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.en.json",
             "u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.ja.json",
             "u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.zh-cn.json"])
        for vpath in out:
            self.assertNotIn("::CAB-abc.monobehaviour.", vpath)
            self.assertIn(".monobehaviour.", vpath)  # nhom __mono__ van nhan
        en = json.loads(out["u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.en.json"])
        self.assertEqual(list(en), ["Key_%02d" % i for i in range(12)])
        self.assertEqual(en["Key_00"], "en#0")
        zh = json.loads(out["u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.zh-cn.json"])
        self.assertEqual(zh["Key_00"], "zhs#0")
        # Nhom fileName chi ghi log, khong lam ban key.
        self.assertTrue(any("nhóm:" in m and "LocalizationTextData_UI:4" in m
                            for m in logs))

    def test_no_assets_name(self):
        item = _kusa_item(cols=["en", "ja"])
        out = ext._text_data_list_virtual_files(
            [("u/resources.assets", "", item)], lambda m: None)
        self.assertEqual(
            sorted(out),
            ["u/resources.assets::resources.textdata.monobehaviour.en.json",
             "u/resources.assets::resources.textdata.monobehaviour.ja.json"])

    def test_dup_keys_fail_closed(self):
        item = _kusa_item(cols=["en", "ja"])
        item["entries"][1]["key"] = item["entries"][0]["key"]
        with self.assertRaises(PB3Error) as cm:
            ext._text_data_list_virtual_files(
                [("u/b.bundle", "CAB-abc", item)], lambda m: None)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        self.assertIn("trùng", cm.exception.detail)

    def test_empty_value_fail_closed(self):
        item = _kusa_item(cols=["en", "ja"])
        item["entries"][3]["values"]["ja"] = ""
        with self.assertRaises(PB3Error) as cm:
            ext._text_data_list_virtual_files(
                [("u/b.bundle", "CAB-abc", item)], lambda m: None)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        self.assertIn("rỗng", cm.exception.detail)

    def test_two_tables_keep_first(self):
        item1 = _kusa_item(n=12, cols=["en", "ja"])
        item2 = _kusa_item(n=12, cols=["en", "ja"], name="OtherTable")
        # Bang 2 co cung key nhung gia tri khac -> giu ban dau, dem xung dot.
        for e in item2["entries"]:
            e["values"]["en"] = "OTHER:" + e["values"]["en"]
        logs = []
        out = ext._text_data_list_virtual_files(
            [("u/b.bundle", "CAB-abc", item1),
             ("u/b.bundle", "CAB-abc", item2)], logs.append)
        en = json.loads(out["u/b.bundle::CAB-abc::CAB-abc.textdata.monobehaviour.en.json"])
        self.assertEqual(en["Key_00"], "en#0")  # giu ban dau
        self.assertTrue(any("xung đột" in m for m in logs))


class TestExpandSerializedTextData(unittest.TestCase):
    def test_routes_text_data_list_separately(self):
        td_item = _kusa_item(cols=["en", "ja"])
        gen_item = {"path_id": 8, "name": "G", "kind": "generic",
                    "locale": None, "strings": ["hello"]}

        class FakeSF:
            def text_assets(self):
                return []

            def monobehaviour_texts(self, warnings=None):
                return [td_item, gen_item]

        _out, mb, _notes, n_ta, n_mb, n_str = ext._expand_serialized(
            FakeSF(), "", "v")
        self.assertEqual(n_mb, 2)
        self.assertEqual(len(mb["text_data_lists"]), 1)
        self.assertEqual(mb["text_data_lists"][0][1], td_item)
        self.assertEqual(len(mb["generic"]), 1)
        # 12 entry x 2 cot = 24 chuoi text-data + 1 chuoi generic.
        self.assertEqual(n_str, 25)


class TestMergeTextData(unittest.TestCase):
    def _make_outcome(self, locale, kv, origin="text_data_list"):
        return ext.FileOutcome(
            "u::%s.json" % locale, "json_text", locale,
            entries=len(kv), ok=len(kv), kv=dict(kv),
            source="u::%s" % locale, origin_kind=origin)

    def test_merged_per_locale_with_origin(self):
        files = [self._make_outcome("en", {"K1": "Hi", "K2": "Bye"}),
                 self._make_outcome("zh-cn", {"K1": "你好", "K2": "再见"}),
                 self._make_outcome("zh-tw", {"K1": "你好", "K2": "再見"})]
        # Target da mo rong theo bien the co that trong du lieu (nhu
        # _expand_merge_targets lam truoc khi goi ham nay).
        merged, stats = ext.merge_language_outcomes(
            files, {"en", "zh-cn", "zh-tw"})
        self.assertEqual(sorted(f.locale for f in merged),
                         ["en", "zh-CN", "zh-TW"])
        for f in merged:
            self.assertTrue(f.merged)
            self.assertEqual(f.origin_kind, "text_data_list")
        en = next(f for f in merged if f.locale == "en")
        self.assertEqual(en.kv, {"K1": "Hi", "K2": "Bye"})

    def test_structured_blocks_mb_generic(self):
        # Game co bang text-data thi MB-generic da tach khong duoc gop
        # (I2/ST thang truoc — cung nguyen tac nhu language_source).
        files = [self._make_outcome("en", {"K1": "Hi"}),
                 self._make_outcome("vi", {"K9": "Chào"},
                               origin=ext.MB_LANG_ORIGIN)]
        merged, _stats = ext.merge_language_outcomes(files, {"en", "vi"})
        locales = sorted(f.locale for f in merged if f.merged)
        self.assertEqual(locales, ["en"])
        self.assertTrue(all(f.origin_kind == "text_data_list"
                            for f in merged if f.merged))


class TestGateClosingTags(unittest.TestCase):
    """Buoc 5b phuong phap Duy: the <move>/<punch> khop 100% tung key —
    ca the dong </move> cung phai dem (truoc day gate chi dem the mo)."""
    SRC = {"k1": "<move>Go {0}</move>\nNext {0:0.##}",
           "k2": "<punch>Hit!</punch>"}

    def test_pass_when_identical(self):
        r = gate.check_placeholders(self.SRC, dict(self.SRC))
        self.assertEqual(r["status"], "pass")

    def test_fail_when_closing_tag_dropped(self):
        tr = dict(self.SRC)
        tr["k1"] = "<move>Go {0}\nNext {0:0.##}"  # mat </move>
        r = gate.check_placeholders(self.SRC, tr)
        self.assertEqual(r["status"], "fail")
        self.assertEqual([f["key"] for f in r["failures"]], ["k1"])

    def test_fail_when_format_placeholder_changed(self):
        tr = dict(self.SRC)
        tr["k1"] = "<move>Go {0}</move>\nNext {0}"  # {0:0.##} -> {0}
        r = gate.check_placeholders(self.SRC, tr)
        self.assertEqual(r["status"], "fail")

    def test_fail_when_newline_count_changed(self):
        tr = dict(self.SRC)
        tr["k1"] = "<move>Go {0}</move> Next {0:0.##}"  # mat xuong dong
        r = gate.check_placeholders(self.SRC, tr)
        self.assertEqual(r["status"], "fail")

    def test_exporters_sees_closing_tags(self):
        self.assertIn("</move>", exp.placeholders("<move>Go</move>"))
        self.assertIn("<move>", exp.placeholders("<move>Go</move>"))


if __name__ == "__main__":
    unittest.main()
