"""Test phát hiện ngôn ngữ theo từng chuỗi (mục A, sdk/langdetect.py).

Rule-based, stdlib-only, không mạng: script (kana/hangul/cyrillic/Hán),
dấu Latin đặc trưng, từ chức năng. Độ chính xác đo trên game thật
(Passpartout 78,8%, 9 Kings 62,7% — xem exp/analysis_translate_full_results.md):
chuỗi ngắn/tên riêng không có từ chức năng thì mặc định "en" yếu, vị trí
đã học trong nhóm I2 mới cứu được — test này chỉ khóa hành vi của riêng
bộ phát hiện, không hứa độ chính xác trên game thật.
"""
import unittest

from sdk import langdetect as ld


class DetectScriptCase(unittest.TestCase):
    def test_kana_wins_over_han(self):
        # Dù ít kana vẫn là tiếng Nhật (kiểm tra TRƯỚC luật đa số Hán).
        code, conf = ld.detect_language("すばらしいわ！")
        self.assertEqual((code, conf), ("ja", "strong"))

    def test_hangul(self):
        self.assertEqual(ld.detect_language("엄청나구만！")[0], "ko")

    def test_cyrillic(self):
        self.assertEqual(ld.detect_language("Необыкновенно!")[0], "ru")

    def test_han(self):
        self.assertEqual(ld.detect_language("丹青妙手，神来之笔啊！")[0], "zh")

    def test_no_letters(self):
        self.assertEqual(ld.detect_language("123 !!!"), (None, "none"))


class DetectFunctionWordsCase(unittest.TestCase):
    def test_german_sentence(self):
        code, conf = ld.detect_language(
            "Häufe in einem einzigen Durchlauf 999+ Gold an.")
        self.assertEqual((code, conf), ("de", "strong"))

    def test_dutch_sentence(self):
        code, conf = ld.detect_language(
            "Bezit 999 of meer goudstukken in één run.")
        self.assertEqual((code, conf), ("nl", "strong"))

    def test_french_sentence(self):
        code, conf = ld.detect_language("Accumulez 999+ d'or en une partie.")
        self.assertEqual((code, conf), ("fr", "strong"))

    def test_plain_latin_defaults_to_en_weak(self):
        # Latin trơn không dấu/từ chức năng -> mặc định "en" yếu (ghi rõ
        # là heuristic, không phải kết luận chắc chắn).
        self.assertEqual(ld.detect_language("Save 999+ gold in a single run."),
                         ("en", "weak"))

    def test_danish(self):
        self.assertEqual(ld.detect_language("Vind ét spil.")[0], "da")

    def test_romanian(self):
        self.assertEqual(
            ld.detect_language("Passpartout face doar capodopere!")[0], "ro")

    def test_portuguese(self):
        self.assertEqual(
            ld.detect_language("Passpartout só faz obras-primas!")[0], "pt")

    def test_czech_and_slovak(self):
        self.assertEqual(ld.detect_language("Veľmi pekne ďakujem za pomoc.")[0],
                         "sk")
        self.assertEqual(ld.detect_language("Děkuji mockrát za pomoc.")[0],
                         "cs")

    def test_hungarian_and_tagalog(self):
        self.assertEqual(ld.detect_language("Nagyon szépen köszönöm.")[0], "hu")
        self.assertEqual(ld.detect_language("Maraming salamat sa tulong.")[0],
                         "tl")


class HomographCase(unittest.TestCase):
    """Từ đồng nghĩa (homograph) gây bỏ phiếu sai — đã sửa, khóa lại.

    'in'/'an'/'a'/'of'/'or' vừa là tiếng Anh vừa là giới từ Đức/Hà Lan...;
    'en' vừa là tiếng Pháp/TBN vừa là tiếng Hà Lan/Đan Mạch...; 'wie'
    vừa là tiếng Đức vừa lọt vào list Ba Lan. Từ đồng nghĩa chỉ được ở
    trong NHIỀU list (hòa, không tín hiệu) hoặc KHÔNG ở list nào — không
    bao giờ chỉ ở 1 list rồi bỏ phiếu sai cho ngôn ngữ đó.
    """

    def test_in_not_in_dutch_or_italian(self):
        self.assertNotIn("in", ld._FUNCTION_WORDS["nl"])
        self.assertNotIn("in", ld._FUNCTION_WORDS["it"])

    def test_en_not_in_single_language(self):
        for code in ("es", "nl", "da", "no", "sv", "tr"):
            self.assertNotIn("en", ld._FUNCTION_WORDS[code])

    def test_a_not_in_portuguese(self):
        self.assertNotIn("a", ld._FUNCTION_WORDS["pt"])

    def test_i_not_in_polish(self):
        self.assertNotIn("i", ld._FUNCTION_WORDS["pl"])

    def test_wie_not_in_polish(self):
        self.assertNotIn("wie", ld._FUNCTION_WORDS["pl"])

    def test_german_not_misread_as_english(self):
        # 'in'/'an' từng chỉ có trong list en nên câu Đức này bị gán en.
        self.assertEqual(
            ld.detect_language("Häufe in einem einzigen Durchlauf 999+ Gold an.")[0],
            "de")

    def test_homograph_tie_breakers_from_real_fixture(self):
        # Từ file Passpartout thật: 'most' (hu "bây giờ") cũng là so sánh
        # nhất tiếng Anh; 've' (tr "và") là mảnh vỡ của would've; 'dove'
        # (it "ở đâu") cũng là tên màu/danh từ tiếng Anh. Cả ba chỉ được
        # HÒA phiếu trong list en, không cho ngôn ngữ kia thắng 1-0.
        self.assertEqual(
            ld.detect_language("A most liberal choice of colour.")[0], "en")
        self.assertEqual(
            ld.detect_language("I would've chosen such fantastic colours.")[0], "en")
        self.assertEqual(ld.detect_language("dove-grey")[0], "en")

    def test_french_circumflex_i_not_romanian(self):
        # 'î' cũng có trong tiếng Pháp (plaît) nên không còn là dấu riêng
        # của Rumani; chuỗi này phải về fr nhờ từ chức năng (il, vous).
        self.assertEqual(
            ld.detect_language("Originalité s'il vous plaît!")[0], "fr")


class PositionsCase(unittest.TestCase):
    def test_infer_positions_from_strong_votes(self):
        groups = [
            ["Win your first game.", "Ich habe das Spiel gewonnen.",
             "Vous avez gagné la partie.", "赢得第一场比赛。"],
            ["You have won ten games.", "Ich habe zehn Spiele gewonnen.",
             "Vous avez gagné dix parties.", "赢得十场比赛。"],
            ["You have won a hundred games.",
             "Ich habe hundert Spiele gewonnen.",
             "Vous avez gagné cent parties.", "赢得一百场比赛。"],
            ["You have won a thousand games.",
             "Ich habe tausend Spiele gewonnen.",
             "Vous avez gagné mille parties.", "赢得一千场比赛。"],
            ["You have won all the games.", "Ich habe jedes Spiel gewonnen.",
             "Vous avez gagné chaque partie.", "赢得每场比赛。"],
            ["You have won the last game.",
             "Ich habe das letzte Spiel gewonnen.",
             "Vous avez gagné la dernière partie.", "赢得最后一场比赛。"],
        ]
        langs = ld.infer_positions(groups)
        self.assertEqual(langs, ["en", "de", "fr", "zh"])

    def test_chinese_variant(self):
        self.assertEqual(ld.detect_chinese_variant("學國會"), "Hant")
        self.assertEqual(ld.detect_chinese_variant("学国会"), "Hans")
        self.assertIsNone(ld.detect_chinese_variant("Hello"))


if __name__ == "__main__":
    unittest.main()
