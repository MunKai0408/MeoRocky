"""Test lọc dịch theo ngôn ngữ nguồn + tách per-string (mục A).

- bridge._source_match: chuỗi thuộc ngôn ngữ nguồn mới được dịch; chuỗi
  phát hiện mạnh thuộc ngôn ngữ khác bị bỏ qua (giữ nguyên từng byte).
- bridge.translate_json / translate_json_google(src=...): kết quả giữ đủ
  key, self_check sạch (missing/wrong_lang_changed rỗng), skipped có lý do.
- sdk.extraction._mb_generic_kv(split_languages=True): tách theo từng
  chuỗi, term làm key, giữ thứ tự gốc, không hậu tố #N trong file tách.
"""
import asyncio
import json
import re
import sys
import unittest

import bridge
from sdk import extraction as ext

MIXED = {
    "Aristocrat_Catherine.s1": "Wie Extraordinär!",
    "Aristocrat_Catherine.s1#10": "Extraordinário!",
    "Aristocrat_Catherine.s1#12": "Необыкновенно!",
    "Aristocrat_Catherine.s1#14": "丹青妙手，神來之筆啊！",
    "Aristocrat_Catherine.s1#15": "丹青妙手，神来之笔啊！",
    "Aristocrat_Catherine.s1#2": "Extraordinary!",
    "Aristocrat_Catherine.s1#5": "すばらしいわ！",
    "Aristocrat_Catherine.s1#6": "엄청나구만！",
    "Play.s1": "Play",
    "Quit.s1": "Quit",
}
NON_EN = ["Wie Extraordinär!", "Extraordinário!", "Необыкновенно!",
          "丹青妙手，神來之筆啊！", "丹青妙手，神来之笔啊！",
          "すばらしいわ！", "엄청나구만！"]


class SourceMatchCase(unittest.TestCase):
    def test_strong_other_language_rejected(self):
        is_src, reason, det = bridge._source_match("Wie Extraordinär!", "en")
        self.assertFalse(is_src)
        self.assertEqual(det, "de")
        self.assertTrue(reason.startswith("other_lang:"))

    def test_strong_source_accepted(self):
        is_src, _r, det = bridge._source_match("Win your first game.", "en")
        self.assertTrue(is_src)
        self.assertEqual(det, "en")

    def test_chinese_strong(self):
        self.assertTrue(bridge._source_match("丹青妙手，神来之笔啊！", "zh")[0])
        self.assertFalse(bridge._source_match("丹青妙手，神来之笔啊！", "en")[0])

    def test_weak_latin_counts_as_english_only(self):
        self.assertTrue(bridge._source_match("Save 999+ gold.", "en")[0])
        self.assertFalse(bridge._source_match("Save 999+ gold.", "zh")[0])

    def test_non_english_diacritics_not_english(self):
        # Chuỗi ngắn không từ chức năng nhưng có dấu không phải tiếng Anh
        # ("Unerhört!", "Extraordinário!") — lưới an toàn, không gửi
        # Google như tiếng Anh nữa (Joseph 21:24: "Unerhört!" -> "Unerhort!").
        for text in ("Unerhört!", "Extraordinário!", "Enastående!"):
            is_src, reason, _det = bridge._source_match(text, "en")
            self.assertFalse(is_src, text)
            self.assertIn(reason, ("non_en_diacritics",) + tuple(
                "other_lang:%s" % c for c in ("de", "pt", "sv")))

    def test_no_letters(self):
        self.assertFalse(bridge._source_match("123 !!!", "en")[0])


class TranslateFilterCase(unittest.TestCase):
    def _parse(self, raw):
        res = json.loads(raw)
        self.assertTrue(res.get("ok"), raw[:200])
        return res

    def test_offline_zh_skips_german_byte_identical(self):
        res = self._parse(bridge.translate_json(
            json.dumps(MIXED, ensure_ascii=False), "vi", False, "zh"))
        self.assertEqual(res["source_lang"], "zh")
        self.assertEqual(set(res["translated"]), set(MIXED))
        self.assertEqual(res["translated"]["Aristocrat_Catherine.s1"],
                         "Wie Extraordinär!")
        self.assertTrue(res["self_check"]["ok"])
        self.assertEqual(res["self_check"]["missing_keys"], [])
        self.assertEqual(res["self_check"]["wrong_lang_changed"], [])
        self.assertGreater(res["skipped_count"], 0)

    def test_google_en_skips_non_english(self):
        # CPython: không có module js -> chỉ còn từ điển offline (glossary),
        # nhưng bộ lọc nguồn + self-check vẫn chạy y như trên worker.
        res = self._parse(asyncio.run(bridge.translate_json_google(
            json.dumps(MIXED, ensure_ascii=False), "vi", False, "en")))
        self.assertEqual(res["source_lang"], "en")
        self.assertEqual(set(res["translated"]), set(MIXED))
        for text in NON_EN:
            self.assertIn(text, res["translated"].values(), text)
        self.assertTrue(res["self_check"]["ok"])
        self.assertEqual(res["self_check"]["missing_keys"], [])
        self.assertEqual(res["self_check"]["wrong_lang_changed"], [])
        self.assertEqual(res["skipped_count"], 7)

    def test_google_zh_only_chinese_segments(self):
        res = self._parse(asyncio.run(bridge.translate_json_google(
            json.dumps(MIXED, ensure_ascii=False), "vi", False, "zh")))
        self.assertEqual(res["source_lang"], "zh")
        self.assertEqual(set(res["translated"]), set(MIXED))
        self.assertEqual(res["skipped_count"], 8)
        self.assertTrue(res["self_check"]["ok"])
        # Chuỗi tiếng Anh giữ nguyên (từ điển offline không dịch ngược).
        self.assertEqual(res["translated"]["Aristocrat_Catherine.s1#2"],
                         "Extraordinary!")

    def test_src_equals_to_is_error(self):
        res = json.loads(asyncio.run(bridge.translate_json_google(
            json.dumps(MIXED, ensure_ascii=False), "en", False, "en")))
        self.assertFalse(res.get("ok"))

    def test_mask_line_masks_closing_tags(self):
        # Thẻ đóng (</b>, </size>) từng không được che nên dòng chỉ còn
        # thẻ đóng vẫn có chữ Latin ('size', 'b') và bị coi là tiếng Anh.
        from sdk.translate import mask_line, unmask_line
        masked, tokens = mask_line("<size=28><b>초청장</b></size>")
        self.assertNotIn("</b>", masked)
        self.assertNotIn("</size>", masked)
        self.assertIn("초청장", masked)
        self.assertEqual(unmask_line(masked, tokens),
                         "<size=28><b>초청장</b></size>")
        masked2, tokens2 = mask_line("<size=35><b>{0} </b></size>")
        self.assertEqual(unmask_line(masked2, tokens2),
                         "<size=35><b>{0} </b></size>")

    def test_rich_text_cjk_kept_byte_identical(self):
        # Chuỗi rich-text thật trong fixture Passpartout: thẻ Unity +
        # chữ Hàn/Trung. Đường Google (CPython: chỉ còn glossary, nhưng
        # bộ lọc chạy y như worker) và đường offline đều phải giữ nguyên
        # từng byte khi nguồn là en.
        rich = {
            "Special_Aristocrat.s20#6":
                "<size=28><b>초청장</b></size>\n\n\n<size=18>제 성으로 초청을합니다",
            "Special_Aristocrat.s20#14":
                "<size=28><b>邀請函</b></size>\r\n\r\n\r\n<size=18>這是一封邀請你入住",
            "Special_TheCritic.s62#6":
                "<size=35><b>{0} </b></size>\n\n<size=30><i><b>{1}</b>",
            "plain_en": "Save the game.",
        }
        res = self._parse(asyncio.run(bridge.translate_json_google(
            json.dumps(rich, ensure_ascii=False), "vi", False, "en")))
        for key in ("Special_Aristocrat.s20#6",
                    "Special_Aristocrat.s20#14",
                    "Special_TheCritic.s62#6"):
            self.assertEqual(res["translated"][key], rich[key], key)
        self.assertTrue(res["self_check"]["ok"])
        res2 = self._parse(bridge.translate_json(
            json.dumps(rich, ensure_ascii=False), "vi", False, "en"))
        for key in ("Special_Aristocrat.s20#6",
                    "Special_Aristocrat.s20#14",
                    "Special_TheCritic.s62#6"):
            self.assertEqual(res2["translated"][key], rich[key], key)
        self.assertTrue(res2["self_check"]["ok"])

    def test_google_batch_dropped_tokens_recovered_by_single_retry(self):
        # Batch gộp (gtx) hay làm rơi token; dịch đơn lại từng dòng lệch
        # phải khôi phục đủ placeholder, cổng kiểm tra phải PASS.
        fake = _FakeJsDroppingBatch()
        sys.modules["js"] = fake
        try:
            kv = {"a": "Unlock {VAR1} now",
                  "b": "You have {0} points and %s",
                  "c": "Pay $VAR and use {foo-bar} here"}
            res = self._parse(asyncio.run(bridge.translate_json_google(
                json.dumps(kv, ensure_ascii=False), "vi", False, "en")))
        finally:
            sys.modules.pop("js", None)
        self.assertEqual(fake.batch_calls, 1)
        self.assertGreater(fake.single_calls, 0)
        self.assertEqual(res["warnings"], [])
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res["translated"])
        self.assertEqual(gate["status"], "pass", gate)


class _FakeJsDroppingBatch:
    """Giả module js: batch gộp làm rơi token, dịch đơn giữ token."""

    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    async def pb3_google_translate_batch(self, texts, src, dst):
        self.batch_calls += 1
        items = ["[vi] " + re.sub(r"__PB3PH\d+__", "", t) for t in texts]
        return json.dumps({"translations": items, "mode": "gtx-joined"},
                          ensure_ascii=False)

    async def pb3_google_translate(self, text, src, dst):
        self.single_calls += 1
        return "[vi] " + text

    def pb3_google_cancelled(self):
        return False

    def pb3_fallback_trace(self):
        return "[]"


def _i2_items():
    """6 term I2-like: [term, en, de, fr, zh, ja] x 6 — đủ để học vị trí."""
    terms = ["Achievement_First", "Achievement_Ten", "Achievement_Hundred",
             "Achievement_Thousand", "Achievement_Every", "Achievement_Last"]
    en = ["Win your first game.", "Win ten games.", "Win a hundred games.",
          "Win a thousand games.", "Win every game.", "Win the last game."]
    de = ["Ich habe das Spiel gewonnen.", "Ich habe zehn Spiele gewonnen.",
          "Ich habe hundert Spiele gewonnen.",
          "Ich habe tausend Spiele gewonnen.",
          "Ich habe jedes Spiel gewonnen.",
          "Ich habe das letzte Spiel gewonnen."]
    fr = ["Vous avez gagné la partie.", "Vous avez gagné dix parties.",
          "Vous avez gagné cent parties.", "Vous avez gagné mille parties.",
          "Vous avez gagné chaque partie.",
          "Vous avez gagné la dernière partie."]
    zh = ["赢得第一场比赛。", "赢得十场比赛。", "赢得一百场比赛。",
          "赢得一千场比赛。", "赢得每场比赛。", "赢得最后一场比赛。"]
    ja = ["最初のゲームに勝った。", "10回のゲームに勝った。",
          "100回のゲームに勝った。", "1000回のゲームに勝った。",
          "すべてのゲームに勝った。", "最後のゲームに勝った。"]
    strings = []
    for i in range(6):
        strings += [terms[i], en[i], de[i], fr[i], zh[i], ja[i]]
    return terms, en, zh, [{"name": "I2Languages", "path_id": 1,
                            "strings": strings}]


class SplitCase(unittest.TestCase):
    def test_split_term_keyed_order_preserved_no_hash_suffix(self):
        terms, en, zh, items = _i2_items()
        res = ext._mb_generic_kv(items, split_languages=True)
        by_lang = res["by_lang"]
        self.assertIsNotNone(by_lang)
        # Term làm key, giữ đúng thứ tự gốc (không sort A-Z).
        self.assertEqual(list(by_lang["en"].keys()), terms)
        self.assertEqual([by_lang["en"][t] for t in terms], en)
        self.assertEqual(list(by_lang["zh"].keys()), terms)
        self.assertEqual([by_lang["zh"][t] for t in terms], zh)
        # File tách không còn hậu tố #N.
        for lang, kv in by_lang.items():
            for key in kv:
                self.assertNotIn("#", key, "%s:%s" % (lang, key))
        self.assertGreaterEqual(res["stats"].get("term_keyed", 0), 6)

    def test_merged_mode_keeps_hash_suffix_and_order(self):
        terms, _en, _zh, items = _i2_items()
        res = ext._mb_generic_kv(items, split_languages=False)
        self.assertIsNone(res["by_lang"])
        keys = list(res["kv"].keys())
        # Thứ tự gốc giữ nguyên: s1..s36 theo thứ tự chuỗi trong object.
        self.assertEqual(keys, ["I2Languages.s%d" % i for i in range(1, 37)])
        # Key trùng thật (2 object cùng tên) thì gộp bằng hậu tố #N.
        dup = ext._mb_generic_kv(
            [{"name": "Dup", "path_id": 1, "strings": ["Hello world."]},
             {"name": "Dup", "path_id": 2, "strings": ["Hello world."]}],
            split_languages=False)
        self.assertEqual(list(dup["kv"].keys()), ["Dup.s1", "Dup.s1#2"])

    def test_identical_value_deduped_not_lost(self):
        # Cùng term + cùng giá trị ở 2 object khác nhau: gộp 1 bản, đếm
        # identical_dropped — KHÔNG mất chuỗi (giá trị giống hệt từng byte).
        terms, _en, _zh, items = _i2_items()
        items2 = list(items) + [{"name": "I2LanguagesCopy", "path_id": 2,
                                 "strings": list(items[0]["strings"])}]
        res = ext._mb_generic_kv(items2, split_languages=True)
        self.assertEqual(list(res["by_lang"]["en"].keys()), terms)
        self.assertGreater(res["stats"].get("identical_dropped", 0), 0)


if __name__ == "__main__":
    unittest.main()
