"""Test T2: thêm ngôn ngữ dịch + từ điển theo game.

- sdk.glossary.GameGlossary: nạp JSON, tra cứu, round-trip, lỗi định dạng.
- sdk.translate: từ điển offline cặp mới (en<->de/fr/...), translate_table
  áp dụng từ điển game trước provider/cache, không bị sửa placeholder.
- sdk.langdetect + bridge._source_match: nguồn ms (họ ms/id).
- sdk.font + sdk.gate: cổng font kiểm theo ngôn ngữ đích (de PASS với
  font đủ chữ, FAIL khi thiếu chữ, fail-closed khi không có font hiển
  thị hoặc đích chưa đăng ký bộ chữ).
- bridge.translate_json / translate_json_google: từ điển game qua
  glossary_json (không bị dịch máy ghi đè).

Không gọi mạng thật. Không dùng file game thật — trừ font Be Vietnam Pro
đi kèm trong repo (fonts/BeVietnamPro-Regular.ttf.b64): file này là dữ
liệu đầu vào của cổng font nên test đọc trực tiếp để khóa danh sách đích
vào font thật.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import unittest
from pathlib import Path

import bridge
from core.errors import PB3Error
from sdk import font as _font
from sdk import gate
from sdk import langdetect as _ld
from sdk import translate as tr
from sdk.glossary import GameGlossary
from tests.fixtures.font import build_fixtures as font_fx

ROOT = Path(__file__).resolve().parent.parent.parent  # gốc repo

NEW_TARGETS = ["de", "fr", "es", "pt", "it", "nl", "id", "ms", "tr",
               "pl", "cs", "sk", "hu", "sv", "da", "no", "fi"]


class _DroppingProvider(tr.TranslationProvider):
    """Provider giả làm RƠI token che placeholder (để kiểm bước sửa).

    Provider chỉ thấy dòng ĐÃ CHE ("Win __PB3PH1__ games"); provider này
    xóa token che nên khi bỏ che, placeholder {0} bị mất -> dòng lệch
    placeholder, bước sửa sẽ dịch lại 1 lần.
    """

    name = "dropping"
    _MASK_RE = re.compile(r"__PB3PH\d+__")

    def __init__(self) -> None:
        self.calls: list = []

    def translate_batch(self, texts, src, dst, on_item=None):
        self.calls.append(list(texts))
        out = ["[%s] %s" % (dst, self._MASK_RE.sub("", t)) for t in texts]
        if on_item is not None:
            for i in range(len(texts)):
                on_item(i)
        return out


class TestGameGlossary(unittest.TestCase):
    def test_load_lookup_case_insensitive(self):
        g = GameGlossary.from_json(json.dumps({
            "version": 1, "game": "9 Kings", "src": "en", "dst": "de",
            "entries": {"Play": "Spielen",
                        "Save {0} gold": "Speichere {0} Gold"}}))
        self.assertEqual(g.lookup("play"), "Spielen")
        self.assertEqual(g.lookup("  PLAY  "), "Spielen")
        self.assertEqual(g.lookup("Save {0} gold"), "Speichere {0} Gold")
        self.assertIsNone(g.lookup("Quit"))
        self.assertEqual(g.game, "9 Kings")
        self.assertEqual(len(g), 2)

    def test_round_trip(self):
        g = GameGlossary.from_entries({"Play": "Spielen"}, game="9K",
                                      src="en", dst="de")
        g2 = GameGlossary.from_json(g.to_json())
        self.assertEqual(g2.lookup("play"), "Spielen")
        self.assertEqual(g2.game, "9K")
        self.assertEqual(g2.src, "en")
        self.assertEqual(g2.dst, "de")

    def test_set_remove_contains(self):
        g = GameGlossary()
        g.set("Play", "Chơi")
        self.assertIn("play", g)
        self.assertTrue(g.remove("PLAY"))
        self.assertNotIn("play", g)
        self.assertFalse(g.remove("play"))

    def test_invalid_inputs_raise(self):
        with self.assertRaises(PB3Error):
            GameGlossary.from_json("{không phải json")
        with self.assertRaises(PB3Error):
            GameGlossary.from_json('{"version": 99, "entries": {}}')
        with self.assertRaises(PB3Error):
            GameGlossary.from_json('{"version": 1}')
        with self.assertRaises(PB3Error):
            GameGlossary.from_json(
                '{"version": 1, "entries": {"Play": 123}}')
        with self.assertRaises(PB3Error):
            GameGlossary().set("", "Chơi")
        with self.assertRaises(PB3Error):
            GameGlossary().set("Play", "   ")


class TestOfflineNewPairs(unittest.TestCase):
    def test_each_new_pair_has_44_entries(self):
        for code in NEW_TARGETS:
            table = tr.GLOSSARIES.get(("en", code))
            self.assertIsNotNone(table, "thiếu từ điển en->%s" % code)
            self.assertEqual(len(table), 44, "en->%s phải đủ 44 cụm" % code)
            for key, value in table.items():
                self.assertEqual(key, key.strip().lower())
                self.assertTrue(key and value and value.strip())
            rev = tr.GLOSSARIES.get((code, "en"))
            self.assertIsNotNone(rev, "thiếu từ điển %s->en" % code)
            self.assertGreaterEqual(len(rev), 40)

    def test_spot_checks(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play"], "en", "de"), ["Spielen"])
        self.assertEqual(p.translate_batch(["Victory"], "en", "fr"), ["Victoire"])
        self.assertEqual(p.translate_batch(["You win"], "en", "es"), ["Ganas"])
        self.assertEqual(p.translate_batch(["OK"], "en", "tr"), ["Tamam"])
        self.assertEqual(p.translate_batch(["Yes"], "en", "fi"), ["Kyllä"])
        self.assertEqual(p.translate_batch(["No"], "en", "pl"), ["Nie"])
        self.assertEqual(p.translate_batch(["Quit"], "en", "hu"), ["Kilépés"])
        self.assertEqual(p.translate_batch(["Spielen"], "de", "en"), ["Play"])

    def test_unknown_pair_is_none(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play"], "en", "ru"), [None])
        self.assertEqual(p.translate_batch(["Play"], "en", "zh"), [None])
        self.assertEqual(p.translate_batch(["Spielen"], "de", "fr"), [None])

    def test_auto_unchanged(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play"], "auto", "vi"), ["Chơi"])
        self.assertEqual(p.translate_batch(["Chơi"], "auto", "vi"), ["Play"])


class TestTranslateTableGameGlossary(unittest.TestCase):
    def test_whole_value_uses_glossary_not_provider(self):
        provider = tr.FakeProvider()
        g = GameGlossary.from_entries({"Play": "Spielen"})
        res = tr.translate_table(
            {"play": "Play", "quit": "Quit"}, source_lang="en",
            target_lang="de", provider=provider, game_glossary=g,
            credit=False)
        self.assertEqual(res.translated["play"], "Spielen")
        self.assertEqual(res.translated["quit"], "[de] Quit")
        self.assertEqual(res.glossary, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(provider.batches, [["Quit"]])

    def test_partial_multiline_value(self):
        provider = tr.FakeProvider()
        g = GameGlossary.from_entries({"Play": "Spielen"})
        res = tr.translate_table(
            {"menu": "Play\nWin your first game."}, source_lang="en",
            target_lang="de", provider=provider, game_glossary=g,
            credit=False)
        self.assertEqual(res.translated["menu"],
                         "Spielen\n[de] Win your first game.")
        self.assertEqual(provider.batches, [["Win your first game."]])

    def test_placeholder_entry_kept(self):
        provider = tr.FakeProvider()
        g = GameGlossary.from_entries(
            {"Save {0} gold": "Speichere {0} Gold"})
        res = tr.translate_table(
            {"save": "Save {0} gold"}, source_lang="en", target_lang="de",
            provider=provider, game_glossary=g, credit=False)
        self.assertEqual(res.translated["save"], "Speichere {0} Gold")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(res.warnings, [])

    def test_glossary_beats_cache(self):
        import tempfile
        from sdk.translate import TranslationCache
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(str(Path(tmp) / "c.json"))
            cache.put("Play", "en", "de", "[de] Play (cũ)")
            cache.save()
            cache2 = TranslationCache(str(Path(tmp) / "c.json"))
            g = GameGlossary.from_entries({"Play": "Spielen"})
            res = tr.translate_table(
                {"play": "Play"}, source_lang="en", target_lang="de",
                provider=tr.FakeProvider(), cache=cache2,
                game_glossary=g, credit=False)
            self.assertEqual(res.translated["play"], "Spielen")
            self.assertEqual(res.glossary, 1)
            self.assertEqual(res.cached, 0)

    def test_glossary_line_not_repaired_by_provider(self):
        # Dòng từ điển game lệch placeholder vẫn giữ nguyên, không bị
        # bước sửa placeholder dịch lại; dòng provider lệch thì sửa.
        provider = _DroppingProvider()
        g = GameGlossary.from_entries({"Save {0} gold": "Speichere Gold"})
        res = tr.translate_table(
            {"a": "Save {0} gold\nWin {0} games"}, source_lang="en",
            target_lang="de", provider=provider, game_glossary=g,
            credit=False)
        lines = res.translated["a"].split("\n")
        self.assertEqual(lines[0], "Speichere Gold")
        # Dòng provider mất {0} dù đã sửa lại 1 lần (provider vẫn làm
        # rơi token) -> giữ bản máy + ghi warning, không rơi key.
        self.assertTrue(lines[1].startswith("[de] Win"))
        self.assertNotIn("{0}", lines[1])
        flat = [t for batch in provider.calls for t in batch]
        self.assertNotIn("Save __PB3PH0__ gold", flat)
        self.assertEqual(flat.count("Win __PB3PH0__ games"), 2)  # gốc + sửa


class TestSourceMatchMs(unittest.TestCase):
    def test_ms_detected_strong(self):
        code, conf = _ld.detect_language(
            "Kerana saya tidak tahu apa yang berlaku")
        self.assertEqual((code, conf), ("ms", "strong"))

    def test_id_detected_strong(self):
        code, conf = _ld.detect_language(
            "Karena saya tidak tahu apa yang terjadi")
        self.assertEqual((code, conf), ("id", "strong"))

    def test_ms_source_accepts_id_detected(self):
        is_src, _reason, det = bridge._source_match(
            "Karena saya tidak tahu apa yang terjadi", "ms")
        self.assertTrue(is_src)
        self.assertEqual(det, "id")

    def test_id_source_accepts_ms_detected(self):
        is_src, _reason, det = bridge._source_match(
            "Kerana saya tidak tahu apa yang berlaku", "id")
        self.assertTrue(is_src)
        self.assertEqual(det, "ms")

    def test_ms_source_rejects_german(self):
        is_src, _reason, det = bridge._source_match(
            "Wie Extraordinär!", "ms")
        self.assertFalse(is_src)
        self.assertEqual(det, "de")


def _de_font(missing_a=False):
    """Font tổng hợp đủ chữ Đức (hoặc thiếu ä khi missing_a)."""
    de_cps = [ord(c) for c in "äöüßÄÖÜẞ"]
    cps = font_fx.ascii_cps() + de_cps
    if missing_a:
        cps = [c for c in cps if c != 0xE4]
    data = font_fx.build_ttf(cps, name="DeFont")
    return gate.font_entry_from_bytes(
        data, "DeFont.ttf", required=_font.required_chars("de"))


def _full_vi_display():
    data = font_fx.build_font_full_vietnamese()
    return gate.font_entry_from_bytes(data, "DonorFont.ttf")


class TestGateFontTarget(unittest.TestCase):
    def test_de_display_full_pass(self):
        r = gate.check_font_coverage(
            {"k": "Spielen, Größe, Übung"}, [],
            display_fonts=[_de_font()], target_lang="de")
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["counts"]["target_lang"], "de")

    def test_de_display_missing_fail(self):
        r = gate.check_font_coverage(
            {"k": "Mädchen, Größe"}, [],
            display_fonts=[_de_font(missing_a=True)],
            target_lang="de")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "display_missing")
        self.assertIn("ä", r["failures"][0]["missing_chars"])

    def test_de_font_missing_vi_char_still_passes(self):
        # coverage() kiểm VI + tập đích: font thiếu 1 chữ Việt (ă) nhưng
        # đủ chữ Đức -> bản dịch Đức vẫn PASS (chỉ chữ ĐANG DÙNG mới tính).
        cps = font_fx.ascii_cps() + [ord(c) for c in "äöüßÄÖÜẞ"]
        cps = [c for c in cps if c != 0x103]  # bỏ ă (U+0103)
        data = font_fx.build_ttf(cps, name="DeFontNoA")
        entry = gate.font_entry_from_bytes(
            data, "DeFontNoA.ttf", required=_font.required_chars("de"))
        r = gate.check_font_coverage(
            {"k": "Mädchen, Größe"}, [],
            display_fonts=[entry], target_lang="de")
        self.assertEqual(r["status"], "pass")

    def test_de_no_display_font_fail_closed(self):
        # Không font hiển thị: font game quét chỉ theo bộ vi -> không
        # kiểm được chữ Đức -> FAIL, không tự cho là đạt.
        r = gate.check_font_coverage(
            {"k": "Größe"}, [_full_vi_display()], target_lang="de")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"],
                         "font_unverifiable_target")

    def test_unknown_target_fail_closed(self):
        # ro chưa đăng ký bộ chữ -> cổng không biết kiểm gì -> FAIL.
        r = gate.check_font_coverage(
            {"k": "Bună"}, [], display_fonts=[_full_vi_display()],
            target_lang="ro")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["failures"][0]["kind"], "font_unknown_target")
        # Tóm tắt phải nói rõ là chưa kiểm được, không được ghi "không
        # cần kiểm" trong khi status là fail.
        self.assertIn("Chưa có dữ liệu bộ chữ", r["summary_vi"])
        self.assertNotIn("không cần kiểm", r["summary_vi"])

    def test_vi_messages_unchanged(self):
        r = gate.check_font_coverage(
            {"k": "Xin chào"}, [], display_fonts=[_full_vi_display()],
            target_lang="vi")
        self.assertEqual(r["status"], "pass")
        self.assertIn("tiếng Việt", r["summary_vi"])

    def test_bundled_font_covers_all_new_targets(self):
        # Khóa danh sách đích vào font đi kèm thật: mọi đích mới phải
        # phủ đủ 100%; đồng thời chứng minh phép đo là thật (ro/ru/ja
        # thiếu chữ trên cùng font đó).
        raw = (ROOT / "fonts" / "BeVietnamPro-Regular.ttf.b64").read_bytes()
        data = base64.b64decode(raw)
        for code, req in sorted(_font.REQUIRED_CHARS.items()):
            if not req:
                continue
            rep = _font.coverage(data, required=sorted(req),
                                 where="BeVietnamPro")
            self.assertEqual(list(rep.missing), [],
                             "font thiếu chữ của %s" % code)
        for chars in ("ȘșȚț", "абвг", "あいう"):
            rep = _font.coverage(data, required=sorted(ord(c) for c in chars),
                                 where="BeVietnamPro")
            self.assertTrue(list(rep.missing),
                            "font bỗng phủ được %r (đo sai?)" % chars)


class TestBridgeGlossary(unittest.TestCase):
    def _glossary_json(self):
        return json.dumps({
            "version": 1, "game": "9 Kings", "src": "en", "dst": "de",
            "entries": {"Play": "SPIELEN (theo game)",
                        "Save {0} gold": "Speichere {0} Gold"}})

    def test_translate_json_offline_glossary(self):
        res = json.loads(bridge.translate_json(
            json.dumps({"play": "Play", "quit": "Quit",
                        "save": "Save {0} gold"}),
            to="de", src="en", glossary_json=self._glossary_json()))
        self.assertTrue(res["ok"])
        self.assertEqual(res["translated"]["play"], "SPIELEN (theo game)")
        self.assertEqual(res["translated"]["quit"], "Beenden")
        self.assertEqual(res["translated"]["save"], "Speichere {0} Gold")
        self.assertGreaterEqual(res["glossary_count"], 2)
        self.assertEqual(res["self_check"]["missing_keys"], [])
        self.assertEqual(res["self_check"]["wrong_lang_changed"], [])

    def test_translate_json_google_glossary(self):
        res = json.loads(asyncio.run(bridge.translate_json_google(
            json.dumps({"play": "Play", "quit": "Quit"}),
            to="de", src="en", glossary_json=self._glossary_json())))
        self.assertTrue(res["ok"])
        self.assertEqual(res["translated"]["play"], "SPIELEN (theo game)")
        # Quit không có trong từ điển game -> từ điển offline en->de.
        self.assertEqual(res["translated"]["quit"], "Beenden")
        self.assertGreaterEqual(res["glossary_count"], 1)

    def test_bad_glossary_json_fails_closed(self):
        res = json.loads(bridge.translate_json(
            json.dumps({"play": "Play"}), to="de", src="en",
            glossary_json="{không phải json"))
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
