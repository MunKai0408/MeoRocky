"""Test dịch tự động (sdk.translate): mask placeholder, provider Google
(mock transport, KHÔNG gọi mạng thật), từ điển offline, cache, bảng dịch.
Tầng fast: thuần bộ nhớ/file nhỏ, không tiến trình con.
"""
import json
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path

from core.errors import PB3Error
from sdk import translate as tr
from tests.helpers import TmpCase


class _Resp:
    """Giả HTTP response (context manager có read)."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _gt_payload(translated: str, source: str = "Hello") -> bytes:
    doc = [[["%s" % translated, source, None, None, 3]], None, "en"]
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


class TestMask(unittest.TestCase):
    def test_round_trip_all_placeholders(self):
        value = "Chào {0}, bạn có %s điểm {name} và %1$s <b>đậm</b> <color=red>đỏ</color> \\n xong"
        masked, tokens = tr.mask_line(value)
        self.assertNotIn("{0}", masked)
        self.assertNotIn("%s", masked)
        self.assertNotIn("<b>", masked)
        self.assertNotIn("\\n", masked)
        self.assertEqual(tr.unmask_line(masked, tokens), value)

    def test_unmask_tolerates_spaces_and_case(self):
        # Google Translate đôi khi chèn khoảng trắng/chữ thường vào token.
        masked, tokens = tr.mask_line("Bạn có {0} điểm")
        mangled = masked.replace("__PB3PH0__", "__ pb3ph0 __")
        self.assertEqual(tr.unmask_line(mangled, tokens), "Bạn có {0} điểm")

    def test_placeholders_ok(self):
        self.assertTrue(tr.placeholders_ok("Có {0} xu", "Có {0} xu"))
        self.assertTrue(tr.placeholders_ok("A\nB", "A\nB"))
        self.assertFalse(tr.placeholders_ok("Có {0} xu", "Có xu"))
        self.assertFalse(tr.placeholders_ok("A\nB", "A B"))
        self.assertFalse(tr.placeholders_ok("Có %s", "Có %d"))

    def test_check_lang(self):
        tr.check_lang("vi")
        tr.check_lang("zh-CN")
        tr.check_lang("auto", allow_auto=True)
        with self.assertRaises(PB3Error):
            tr.check_lang("xx!")
        with self.assertRaises(PB3Error):
            tr.check_lang("auto")
        with self.assertRaises(PB3Error):
            tr.check_lang("")


class TestMaskGateCoverage(unittest.TestCase):
    """mask_line phải che mọi dạng placeholder mà cổng kiểm tra đếm
    (sdk/gate.py::_PLACEHOLDER_RE): $var và {nội dung bất kỳ} trước đây
    không được che nên máy dịch nhìn thấy chúng như chữ thường và làm mất."""

    def test_mask_covers_gate_forms(self):
        cases = {
            "Pay $VAR now": ["$VAR"],
            "Cost $5 and $VAR": ["$VAR"],
            "Use {foo-bar} here": ["{foo-bar}"],
            "Set {a.b} value": ["{a.b}"],
            "Empty {} here": ["{}"],
            "Text {0:>5} pad": ["{0:>5}"],
            "Unlock {VAR1} now": ["{VAR1}"],
            "Color {KINGCOLOR} here": ["{KINGCOLOR}"],
        }
        for value, want in cases.items():
            masked, tokens = tr.mask_line(value)
            self.assertEqual(tokens, want, value)
            self.assertEqual(tr.unmask_line(masked, tokens), value)

    def test_mask_does_not_swallow_plain_text(self):
        # $ theo sau là số không phải placeholder; { không đóng cũng không.
        masked, tokens = tr.mask_line("Cost $5 and $100, brace { open")
        self.assertEqual(tokens, [])
        self.assertEqual(masked, "Cost $5 and $100, brace { open")
        # ${VAR}: chỉ {VAR} là placeholder, $ đứng riêng.
        masked, tokens = tr.mask_line("Value ${VAR} here")
        self.assertEqual(tokens, ["{VAR}"])
        self.assertEqual(tr.unmask_line(masked, tokens), "Value ${VAR} here")

    def test_unmask_recovers_stripped_underscores(self):
        # Google/MyMemory hay lột dấu gạch dưới của token khi dịch.
        masked, tokens = tr.mask_line("Unlock {VAR1} and {KINGCOLOR} now")
        bare = masked.replace("__PB3PH0__", "PB3PH0").replace(
            "__PB3PH1__", "PB3PH1")
        self.assertEqual(tr.unmask_line(bare, tokens),
                         "Unlock {VAR1} and {KINGCOLOR} now")
        one_side = masked.replace("__PB3PH0__", "__PB3PH0")
        self.assertEqual(tr.unmask_line(one_side, tokens),
                         "Unlock {VAR1} and {KINGCOLOR} now")

    def test_strict_match_mirrors_gate(self):
        # So theo thứ tự + số lần (như cổng), không chỉ tập hợp.
        self.assertTrue(tr.placeholders_match_strict("A {0} B {0}", "A {0} B {0}"))
        self.assertFalse(tr.placeholders_match_strict("A {0} B {0}", "A {0} B"))
        self.assertFalse(tr.placeholders_match_strict("A {x} B {y}", "A {y} B {y}"))
        self.assertFalse(tr.placeholders_match_strict("Pay $VAR", "Pay"))
        self.assertTrue(tr.placeholders_match_strict("Pay $VAR", "Pay $VAR"))


class _MangleUnderscoresProvider(tr.TranslationProvider):
    """Giả máy dịch lột dấu gạch dưới của token: __PB3PH0__ -> PB3PH0."""

    name = "mangle-underscores"

    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts, src, dst, on_item=None):
        self.calls += 1
        out = []
        for t in texts:
            mangled = re.sub(r"__PB3PH(\d+)__", r"PB3PH\1", t)
            out.append("[vi] " + mangled)
            if on_item is not None:
                on_item(len(out) - 1)
        return out


class _DropOnceProvider(tr.TranslationProvider):
    """Lần đầu làm rơi token (như batch gộp), dịch lại lần sau thì giữ."""

    name = "drop-once"

    def __init__(self):
        self.calls = 0
        self._seen = set()

    def translate_batch(self, texts, src, dst, on_item=None):
        self.calls += 1
        out = []
        for t in texts:
            if t in self._seen:
                out.append("[vi] " + t)
            else:
                self._seen.add(t)
                out.append("[vi] " + re.sub(r"__PB3PH\d+__", "", t))
            if on_item is not None:
                on_item(len(out) - 1)
        return out


class _CountingDroppingProvider(tr.TranslationProvider):
    """Lúc nào cũng làm rơi token, đếm số lần gọi (để kiểm tra có retry)."""

    name = "counting-dropping"

    def __init__(self):
        self.calls = 0

    def translate_batch(self, texts, src, dst, on_item=None):
        self.calls += 1
        return [re.sub(r"__PB3PH\d+__", "", t) for t in texts]


class TestPlaceholderRepair(unittest.TestCase):
    def test_mangled_tokens_recovered_no_warning(self):
        kv = {"a": "Unlock {VAR1} now",
              "b": "You have {0} points and %s",
              "c": "Color {KINGCOLOR}!"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_MangleUnderscoresProvider())
        self.assertEqual(res.warnings, [])
        from sdk.gate import check_placeholders
        gate = check_placeholders(kv, res.translated)
        self.assertEqual(gate["status"], "pass", gate)
        for key, value in kv.items():
            self.assertTrue(
                tr.placeholders_match_strict(value, res.translated[key]), key)

    def test_dropped_tokens_recovered_by_retry(self):
        provider = _DropOnceProvider()
        kv = {"a": "Unlock {VAR1} now", "b": "You have {0} points"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=provider)
        self.assertEqual(res.warnings, [])
        self.assertEqual(provider.calls, 2)  # lần đầu + 1 lần dịch lại
        from sdk.gate import check_placeholders
        self.assertEqual(
            check_placeholders(kv, res.translated)["status"], "pass")
        self.assertIn("{VAR1}", res.translated["a"])
        self.assertIn("{0}", res.translated["b"])

    def test_dropped_tokens_still_warn_when_retry_fails(self):
        # Hành vi đã pin: giữ bản dịch hỏng + cảnh báo + không cache.
        provider = _CountingDroppingProvider()
        cache = tr.TranslationCache(None)
        res = tr.translate_table({"a": "You have {0} points"}, source_lang="en",
                                 target_lang="vi", provider=provider, cache=cache)
        self.assertEqual(res.translated["a"], "You have  points")
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("placeholder", res.warnings[0])
        self.assertIsNone(cache.get("You have {0} points", "en", "vi"))
        self.assertEqual(provider.calls, 2)  # đã thử dịch lại 1 lần rồi mới chịu

    def test_dollar_and_brace_forms_survive_mangling(self):
        kv = {"a": "Pay $VAR now", "b": "Use {foo-bar} and {0} here"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_MangleUnderscoresProvider())
        self.assertEqual(res.warnings, [])
        from sdk.gate import check_placeholders
        self.assertEqual(
            check_placeholders(kv, res.translated)["status"], "pass")


class TestGoogleProvider(unittest.TestCase):
    def test_single_request_parses(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            return _Resp(_gt_payload("Xin chào thế giới", "Hello world"))

        p = tr.GoogleTranslateProvider(urlopen=fake_urlopen, sleep=lambda s: None)
        got = p.translate_batch(["Hello world"], "en", "vi")
        self.assertEqual(got, ["Xin chào thế giới"])
        self.assertIn("translate.googleapis.com", seen["url"])
        self.assertIn("q=Hello+world", seen["url"])

    def test_retry_429_then_success(self):
        sleeps = []
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
            return _Resp(_gt_payload("Chào", "Hi"))

        p = tr.GoogleTranslateProvider(urlopen=fake_urlopen,
                                       sleep=lambda s: sleeps.append(s))
        self.assertEqual(p.translate_batch(["Hi"], "en", "vi"), ["Chào"])
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_urlerror_becomes_e_network(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.URLError("mất mạng")

        p = tr.GoogleTranslateProvider(urlopen=fake_urlopen, sleep=lambda s: None,
                                       max_retries=2, backoff=(0.1, 0.2))
        with self.assertRaises(PB3Error) as ctx:
            p.translate_batch(["Hi"], "en", "vi")
        self.assertEqual(ctx.exception.code, "E_NETWORK")
        self.assertEqual(calls["n"], 3)

    def test_malformed_response_is_e_network(self):
        p = tr.GoogleTranslateProvider(
            urlopen=lambda req, timeout=None: _Resp(b"khong phai json"),
            sleep=lambda s: None)
        with self.assertRaises(PB3Error) as ctx:
            p.translate_batch(["Hi"], "en", "vi")
        self.assertEqual(ctx.exception.code, "E_NETWORK")

    def test_empty_response_is_e_network(self):
        p = tr.GoogleTranslateProvider(
            urlopen=lambda req, timeout=None: _Resp(b"[]"),
            sleep=lambda s: None)
        with self.assertRaises(PB3Error) as ctx:
            p.translate_batch(["Hi"], "en", "vi")
        self.assertEqual(ctx.exception.code, "E_NETWORK")

    def test_gap_between_requests(self):
        sleeps = []
        p = tr.GoogleTranslateProvider(
            urlopen=lambda req, timeout=None: _Resp(_gt_payload("X", "Y")),
            sleep=lambda s: sleeps.append(s), gap_s=0.7)
        p.translate_batch(["A", "B"], "en", "vi")
        self.assertEqual(sleeps, [0.7])

    def test_http_400_not_retried(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

        p = tr.GoogleTranslateProvider(urlopen=fake_urlopen, sleep=lambda s: None)
        with self.assertRaises(PB3Error) as ctx:
            p.translate_batch(["Hi"], "en", "vi")
        self.assertEqual(ctx.exception.code, "E_NETWORK")
        self.assertEqual(calls["n"], 1)


class TestGlossary(unittest.TestCase):
    def test_en_vi_known(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play", "Settings", "Loading..."], "en", "vi"),
                         ["Chơi", "Cài đặt", "Đang tải..."])
        self.assertEqual(p.translate_batch(["PLAY"], "en", "vi"), ["Chơi"])

    def test_unknown_is_none(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Flibberty gibbet"], "en", "vi"), [None])

    def test_vi_en_reverse(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Chơi", "Cài đặt"], "vi", "en"),
                         ["Play", "Settings"])

    def test_other_pair_is_none(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play"], "en", "fr"), [None])

    def test_auto_src_tries_en_first(self):
        p = tr.OfflineGlossaryProvider()
        self.assertEqual(p.translate_batch(["Play"], "auto", "vi"), ["Chơi"])


class TestCache(TmpCase):
    def test_put_get_round_trip(self):
        path = str(self.tmp / "cache.json")
        c = tr.TranslationCache(path)
        self.assertIsNone(c.get("Hello", "en", "vi"))
        c.put("Hello", "en", "vi", "Xin chào")
        self.assertEqual(c.get("Hello", "en", "vi"), "Xin chào")
        self.assertIsNone(c.get("Hello", "en", "fr"))
        c.save()
        c2 = tr.TranslationCache(path)
        self.assertEqual(c2.get("Hello", "en", "vi"), "Xin chào")

    def test_eviction_oldest_first(self):
        c = tr.TranslationCache(None, max_entries=2)
        c.put("a", "en", "vi", "A")
        c.put("b", "en", "vi", "B")
        c.put("c", "en", "vi", "C")
        self.assertIsNone(c.get("a", "en", "vi"))
        self.assertEqual(c.get("b", "en", "vi"), "B")
        self.assertEqual(c.get("c", "en", "vi"), "C")

    def test_corrupt_file_starts_empty(self):
        path = self.tmp / "cache.json"
        path.write_text("{ hỏng", encoding="utf-8")
        c = tr.TranslationCache(str(path))
        self.assertIsNone(c.get("x", "en", "vi"))
        c.put("x", "en", "vi", "X")
        c.save()
        self.assertEqual(tr.TranslationCache(str(path)).get("x", "en", "vi"), "X")

    def test_save_creates_parent_dir(self):
        path = str(self.tmp / "sau" / "cache.json")
        c = tr.TranslationCache(path)
        c.put("x", "en", "vi", "X")
        c.save()
        self.assertTrue(Path(path).exists())


class _FailingProvider(tr.TranslationProvider):
    name = "failing"

    def translate_batch(self, texts, src, dst, on_item=None):
        raise PB3Error("E_NETWORK", "mất mạng (giả lập)")


class _PartialProvider(tr.TranslationProvider):
    """Dịch mọi dòng trừ dòng chứa 'KHÔNG-DỊCH-ĐƯỢC' (trả None cho dòng đó)."""
    name = "partial"

    def translate_batch(self, texts, src, dst, on_item=None):
        out = [None if "KHÔNG-DỊCH-ĐƯỢC" in t else "[%s] %s" % (dst, t)
               for t in texts]
        if on_item is not None:
            for i in range(len(texts)):
                on_item(i)
        return out


class TestTranslateTable(unittest.TestCase):
    def test_keys_kept_values_translated_placeholders_kept(self):
        kv = {"hello": "Hello", "points": "You have {0} points",
              "bold": "Click <b>here</b> %s", "multi": "Line one\nLine two",
              "empty": "", "only_ph": "{0}", "pct": "100%"}
        fake = tr.FakeProvider()
        res = tr.translate_table(kv, source_lang="en", target_lang="vi", provider=fake)
        self.assertEqual(list(res.translated), list(kv))
        self.assertEqual(res.translated["hello"], "[vi] Hello")
        self.assertEqual(res.translated["points"], "[vi] You have {0} points")
        self.assertEqual(res.translated["bold"], "[vi] Click <b>here</b> %s")
        self.assertEqual(res.translated["multi"], "[vi] Line one\n[vi] Line two")
        self.assertEqual(res.translated["empty"], "")
        self.assertEqual(res.translated["only_ph"], "{0}")
        self.assertEqual(res.translated["pct"], "[vi] 100%")
        self.assertEqual(res.total, 7)
        self.assertEqual(res.done, 5)
        self.assertEqual(res.empty, 1)
        self.assertEqual(res.unchanged, 1)
        self.assertEqual(res.warnings, [])

    def test_cache_skips_second_call(self):
        kv = {"a": "Hello", "b": "World"}
        fake = tr.FakeProvider()
        cache = tr.TranslationCache(None)
        tr.translate_table(kv, source_lang="en", target_lang="vi",
                           provider=fake, cache=cache)
        self.assertEqual(fake.calls, 1)
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=fake, cache=cache)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(res.cached, 2)
        self.assertEqual(res.done, 0)

    def test_network_error_falls_back_to_glossary(self):
        kv = {"play": "Play", "weird": "Flibberty gibbet"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_FailingProvider(),
                                 fallback=tr.OfflineGlossaryProvider())
        self.assertIsNotNone(res.network_error)
        self.assertEqual(res.translated["play"], "Chơi")
        self.assertEqual(res.translated["weird"], "Flibberty gibbet")
        self.assertEqual(res.glossary, 1)
        self.assertEqual(res.untranslated, 1)
        self.assertIn("weird", res.untranslated_keys)

    def test_network_error_without_fallback_keeps_source(self):
        kv = {"a": "Hello"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_FailingProvider())
        self.assertIsNotNone(res.network_error)
        self.assertEqual(res.translated["a"], "Hello")
        self.assertEqual(res.untranslated, 1)

    def test_partial_value_keeps_untranslated_lines(self):
        kv = {"mix": "Hello\nKHÔNG-DỊCH-ĐƯỢC"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_PartialProvider())
        self.assertEqual(res.translated["mix"], "[vi] Hello\nKHÔNG-DỊCH-ĐƯỢC")
        self.assertEqual(res.partial, 1)
        self.assertIn("mix", res.untranslated_keys)
        self.assertTrue(any("mix" in w for w in res.warnings))

    def test_placeholder_loss_warns_and_skips_cache(self):
        class _Dropping(tr.TranslationProvider):
            name = "dropping"

            def translate_batch(self, texts, src, dst, on_item=None):
                return [re.sub(r"__PB3PH\d+__", "", t) for t in texts]

        kv = {"a": "You have {0} points"}
        cache = tr.TranslationCache(None)
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=_Dropping(), cache=cache)
        self.assertEqual(res.translated["a"], "You have  points")
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("placeholder", res.warnings[0])
        self.assertIsNone(cache.get("You have {0} points", "en", "vi"))

    def test_bad_lang_and_same_lang(self):
        fake = tr.FakeProvider()
        with self.assertRaises(PB3Error):
            tr.translate_table({"a": "x"}, source_lang="en", target_lang="xx!",
                               provider=fake)
        with self.assertRaises(PB3Error):
            tr.translate_table({"a": "x"}, source_lang="en", target_lang="en",
                               provider=fake)

    def test_non_string_value_is_corrupt(self):
        with self.assertRaises(PB3Error) as ctx:
            tr.translate_table({"a": 5}, source_lang="en", target_lang="vi",
                               provider=tr.FakeProvider())
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_progress_called(self):
        seen = []
        kv = {"a": "Hello", "b": "World", "c": "Again"}
        tr.translate_table(kv, source_lang="en", target_lang="vi",
                           provider=tr.FakeProvider(),
                           progress_fn=lambda d, t: seen.append((d, t)), chunk=2)
        # Báo sau MỖI chuỗi đã dịch xong (không đợi hết lô).
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_progress_never_goes_back_on_network_fallback(self):
        class _Flaky(tr.TranslationProvider):
            name = "flaky"

            def translate_batch(self, texts, src, dst, on_item=None):
                # Dịch xong chuỗi đầu rồi lỗi mạng giữa lô.
                if on_item is not None:
                    on_item(0)
                raise PB3Error("E_NETWORK", "mất mạng giữa lô (giả lập)")

        seen = []
        kv = {"play": "Play", "weird": "Flibberty gibbet", "set": "Settings"}
        tr.translate_table(kv, source_lang="en", target_lang="vi",
                           provider=_Flaky(),
                           fallback=tr.OfflineGlossaryProvider(),
                           progress_fn=lambda d, t: seen.append((d, t)), chunk=2)
        dones = [d for d, _t in seen]
        self.assertEqual(dones, sorted(dones))
        self.assertEqual(dones[-1], 3)

    def test_vi_to_en(self):
        kv = {"a": "Chơi"}
        res = tr.translate_table(kv, source_lang="vi", target_lang="en",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.translated["a"], "[en] Chơi")


class TestTranslateJsonFile(TmpCase):
    def _kv_file(self, data, name="en.json"):
        path = self.tmp / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)

    def test_round_trip(self):
        src = self._kv_file({"hello": "Hello", "play": "Play"})
        out = str(self.tmp / "en.vi.json")
        res = tr.translate_json_file(src, out, source_lang="en", target_lang="vi",
                                     provider=tr.FakeProvider())
        self.assertEqual(res.done, 2)
        got = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(got, {"hello": "[vi] Hello", "play": "[vi] Play"})

    def test_non_dict_is_corrupt(self):
        src = self._kv_file(["a", "b"], "list.json")
        with self.assertRaises(PB3Error) as ctx:
            tr.translate_json_file(src, str(self.tmp / "o.json"),
                                   source_lang="en", target_lang="vi",
                                   provider=tr.FakeProvider())
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_non_string_value_is_corrupt(self):
        src = self._kv_file({"a": 5})
        with self.assertRaises(PB3Error) as ctx:
            tr.translate_json_file(src, str(self.tmp / "o.json"),
                                   source_lang="en", target_lang="vi",
                                   provider=tr.FakeProvider())
        self.assertEqual(ctx.exception.code, "E_CORRUPT")

    def test_missing_file_is_corrupt(self):
        with self.assertRaises(PB3Error) as ctx:
            tr.translate_json_file(str(self.tmp / "khong-co.json"),
                                   str(self.tmp / "o.json"),
                                   source_lang="en", target_lang="vi",
                                   provider=tr.FakeProvider())
        self.assertEqual(ctx.exception.code, "E_CORRUPT")


class TestCredit(unittest.TestCase):
    """Credit 'Việt Hoá Bởi Bear' (bổ sung sau Claude 08, Joseph 02:47)."""

    def test_credit_attaches_to_title(self):
        kv = {"title": "9 Kings", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "title")
        self.assertTrue(res.credit_requested)
        self.assertEqual(res.translated["title"],
                         "[vi] 9 Kings — Việt Hoá Bởi Bear")
        self.assertEqual(res.translated["play"], "[vi] Play")
        self.assertEqual(list(res.translated), list(kv))

    def test_credit_prefers_title_over_version(self):
        kv = {"version": "1.0.5", "title": "9 Kings", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "title")
        self.assertEqual(res.translated["version"], "[vi] 1.0.5")
        self.assertIn("Việt Hoá Bởi Bear", res.translated["title"])

    def test_credit_falls_back_to_version_and_copyright(self):
        kv = {"version": "1.0.5", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "version")
        self.assertEqual(res.translated["version"],
                         "[vi] 1.0.5 — Việt Hoá Bởi Bear")
        kv2 = {"copyright": "© 2024 Bear Studio", "play": "Play"}
        res2 = tr.translate_table(kv2, source_lang="en", target_lang="vi",
                                  provider=tr.FakeProvider())
        self.assertEqual(res2.credit_key, "copyright")
        self.assertIn("Việt Hoá Bởi Bear", res2.translated["copyright"])

    def test_credit_none_when_no_candidate(self):
        kv = {"hello": "Hello", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertIsNone(res.credit_key)
        self.assertTrue(res.credit_requested)
        self.assertEqual(res.translated["play"], "[vi] Play")
        self.assertNotIn("Việt Hoá Bởi Bear",
                         json.dumps(res.translated, ensure_ascii=False))

    def test_credit_disabled(self):
        kv = {"title": "9 Kings", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider(), credit=False)
        self.assertIsNone(res.credit_key)
        self.assertFalse(res.credit_requested)
        self.assertEqual(res.translated["title"], "[vi] 9 Kings")
        self.assertNotIn("Việt Hoá Bởi Bear",
                         json.dumps(res.translated, ensure_ascii=False))

    def test_credit_idempotent(self):
        kv = {"title": "9 Kings — Việt Hoá Bởi Bear", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "title")
        self.assertEqual(res.translated["title"].count("Việt Hoá Bởi Bear"), 1)
        # Gắn trực tiếp 2 lần cũng chỉ có 1 credit.
        table = {"title": "9 Kings"}
        self.assertEqual(tr.apply_credit(table), "title")
        self.assertEqual(tr.apply_credit(table), "title")
        self.assertEqual(table["title"].count("Việt Hoá Bởi Bear"), 1)

    def test_credit_skips_empty_title(self):
        kv = {"title": "", "version": "1.0.5", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "version")
        self.assertEqual(res.translated["title"], "")

    def test_credit_keeps_translation_rules(self):
        # Luật dịch vẫn giữ nguyên với entry credit: chỉ dịch value, giữ
        # key/placeholder/thẻ/xuống dòng; cache chỉ giữ bản dịch thuần.
        kv = {"title": "9 Kings", "points": "You have {0} points",
              "bold": "Click <b>here</b> %s", "multi": "Line one\nLine two"}
        cache = tr.TranslationCache(None)
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider(), cache=cache)
        self.assertEqual(res.credit_key, "title")
        self.assertEqual(res.translated["points"], "[vi] You have {0} points")
        self.assertEqual(res.translated["bold"], "[vi] Click <b>here</b> %s")
        self.assertEqual(res.translated["multi"], "[vi] Line one\n[vi] Line two")
        self.assertEqual(list(res.translated), list(kv))
        # Cache giữ bản dịch thuần (không credit): chạy lại với credit=False
        # vẫn ra đúng bản dịch thuần.
        self.assertEqual(cache.get("9 Kings", "en", "vi"), "[vi] 9 Kings")
        res2 = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                  provider=tr.FakeProvider(), cache=cache,
                                  credit=False)
        self.assertEqual(res2.translated["title"], "[vi] 9 Kings")

    def test_find_credit_entry(self):
        self.assertEqual(tr.find_credit_entry({"game_title": "X"}), "game_title")
        self.assertEqual(tr.find_credit_entry({"ui_title": "X"}), "ui_title")
        self.assertEqual(tr.find_credit_entry({"app_name": "X"}), "app_name")
        self.assertIsNone(tr.find_credit_entry({"title": ""}))
        self.assertIsNone(tr.find_credit_entry({"title": "{0}"}))
        self.assertIsNone(tr.find_credit_entry({"hello": "Hello"}))


if __name__ == "__main__":
    unittest.main()
