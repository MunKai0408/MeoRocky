"""Test plugin Apple plist / .stringsdict / .xcstrings (Claude 06)."""
import os
import plistlib
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.apple_plist.plugin import ApplePlistPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "apple_plist")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "apple_plist")


def _plug():
    return ApplePlistPlugin()


def _parse(data, tmp, path="a.plist"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestPlistParse(TmpCase):
    def test_xml_nested_types_and_order(self):
        t = _parse(_read("a.plist"), self.tmp)
        keys = [e.key for e in t.entries]
        self.assertEqual(keys, ["app_name", "welcome", "max_lives", "premium", "menu.new_game",
                                "menu.settings", "tips[0]", "tips[1]", "empty"])
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["app_name"].value, "9 Kings 👑")
        self.assertEqual(by_key["welcome"].value, "Xin chào, người chơi!")
        self.assertEqual(by_key["max_lives"].value, "3")
        self.assertIn("json:number", by_key["max_lives"].notes)
        self.assertEqual(by_key["premium"].value, "true")
        self.assertIn("json:boolean", by_key["premium"].notes)
        self.assertEqual(by_key["empty"].status, "empty")
        self.assertEqual(t.adapter_id, "apple_plist")
        self.assertTrue(all(isinstance(e.source, SourceRef) for e in t.entries))

    def test_binary_parses_same_keys(self):
        t = _parse(_read("b.plist"), self.tmp, "b.plist")
        keys = [e.key for e in t.entries]
        self.assertEqual(keys, ["app_name", "welcome", "max_lives", "premium", "menu.new_game",
                                "menu.settings", "tips[0]", "tips[1]", "empty"])
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["app_name"].value, "9 Kings 👑")
        self.assertEqual(by_key["max_lives"].value, "3")
        self.assertIn("json:number", by_key["max_lives"].notes)

    def test_stringsdict_plural(self):
        t = _parse(_read("c.stringsdict"), self.tmp, "c.stringsdict")
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(sorted(by_key), ["apples.format", "apples.plural.one",
                                          "apples.plural.other", "apples.plural.zero",
                                          "days.format", "days.plural.one", "days.plural.other"])
        self.assertEqual(by_key["apples.format"].value, "%#@apples@")
        self.assertEqual(by_key["apples.plural.zero"].value, "Không có quả táo nào")
        self.assertEqual(by_key["days.plural.other"].value, "%d ngày")

    def test_stringsdict_nested_real_form(self):
        # Dạng lồng thật của Apple: {"count": {"zero": ..., "other": ...}}.
        data = (b'<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n<dict>\n'
                b'<key>items</key>\n<dict>\n<key>NSStringLocalizedFormatKey</key>\n'
                b'<string>%#@count@</string>\n<key>count</key>\n<dict>\n'
                b'<key>NSStringFormatSpecTypeKey</key>\n<string>NSStringPluralRuleType</string>\n'
                b'<key>zero</key>\n<string>het</string>\n'
                b'<key>other</key>\n<string>con %d</string>\n</dict>\n</dict>\n</dict>\n</plist>\n')
        t = _parse(data, self.tmp, "n.stringsdict")
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(sorted(by_key), ["items.format", "items.plural.other", "items.plural.zero"])
        self.assertEqual(by_key["items.plural.other"].value, "con %d")

    def test_xcstrings_locales_and_variations(self):
        t = _parse(_read("d.xcstrings"), self.tmp, "d.xcstrings")
        got = [(e.key, e.locale, e.value) for e in t.entries]
        self.assertEqual(got, [("apples", "en", "%lld apples"),
                               ("apples.plural.one", "en", "%lld apple"),
                               ("apples.plural.other", "en", "%lld apples"),
                               ("apples", "vi", "%lld quả táo"),
                               ("hello", "en", "Hello"),
                               ("hello", "vi", "Xin chào 🌟"),
                               ("welcome", "en", "Welcome, %@!"),
                               ("welcome", "vi", "Chào mừng, %@!")])

    def test_corrupt_raises(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"khong phai plist", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<?xml version="1.0"?><plist><dict><key>a</key>', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_json_without_strings_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'{"a": 1}', self.tmp, "x.plist")
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_xcstrings_bad_json_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'{"strings": ', self.tmp, "x.xcstrings")
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestPlistBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_xml_keeps_order_and_types(self):
        base = _read("a.plist")
        edited = LocTable([LocEntry("welcome", "Chào bạn", None, "native", "ok",
                                    SourceRef(["a.plist"], "a.plist")),
                           LocEntry("max_lives", "5", None, "native", "ok",
                                    SourceRef(["a.plist"], "a.plist"), ["json:number"]),
                           LocEntry("menu.settings", "Tuỳ chỉnh", None, "native", "ok",
                                    SourceRef(["a.plist"], "a.plist"))],
                          None, "apple_plist")
        new = self._build(edited, base)
        back = plistlib.loads(new)
        self.assertEqual(back["welcome"], "Chào bạn")
        self.assertEqual(back["max_lives"], 5)
        self.assertIsInstance(back["max_lives"], int)
        self.assertEqual(back["menu"]["settings"], "Tuỳ chỉnh")
        self.assertEqual(list(back.keys()), ["app_name", "welcome", "max_lives", "premium",
                                            "menu", "tips", "empty"])
        self.assertEqual(back["tips"], ["Mẹo một", "Mẹo hai"])  # không sửa thì giữ nguyên
        self.assertIs(back["premium"], True)

    def test_build_stringsdict_plural(self):
        base = _read("c.stringsdict")
        t = _parse(base, self.tmp, "c.stringsdict")
        edited = LocTable([LocEntry("apples.plural.other", "%d trái táo", None, "native", "ok",
                                    SourceRef(["c.stringsdict"], "c.stringsdict"))],
                          None, "apple_plist")
        new = self._build(edited, base)
        back = plistlib.loads(new)
        self.assertEqual(back["apples"]["NSStringPluralRuleOther"], "%d trái táo")
        self.assertEqual(back["apples"]["NSStringPluralRuleZero"], "Không có quả táo nào")
        self.assertEqual(back["days"]["NSStringPluralRuleOne"], "Một ngày")
        # parse lại sau khi ghi phải khớp
        t2 = _parse(new, self.tmp, "c.stringsdict")
        by_key = {e.key: e for e in t2.entries}
        self.assertEqual(by_key["apples.plural.other"].value, "%d trái táo")
        self.assertEqual(len(t2.entries), len(t.entries))

    def test_build_xcstrings_keeps_structure(self):
        base = _read("d.xcstrings")
        edited = LocTable([LocEntry("hello", "Chào bạn 🌟", "vi", "native", "ok",
                                    SourceRef(["d.xcstrings"], "d.xcstrings")),
                           LocEntry("apples.plural.one", "%lld trái táo", "en", "native", "ok",
                                    SourceRef(["d.xcstrings"], "d.xcstrings"))],
                          None, "apple_plist")
        new = self._build(edited, base)
        import json
        back = json.loads(new.decode("utf-8"))
        self.assertEqual(back["sourceLanguage"], "en")
        self.assertEqual(back["version"], "1.0")
        self.assertEqual(back["strings"]["hello"]["localizations"]["vi"]["stringUnit"]["value"],
                         "Chào bạn 🌟")
        self.assertEqual(back["strings"]["hello"]["localizations"]["en"]["stringUnit"]["value"],
                         "Hello")
        self.assertEqual(back["strings"]["apples"]["localizations"]["en"]["variations"]
                         ["plural"]["one"]["stringUnit"]["value"], "%lld trái táo")
        self.assertEqual(back["strings"]["apples"]["localizations"]["en"]["variations"]
                         ["plural"]["other"]["stringUnit"]["value"], "%lld apples")

    def test_binary_build_raises_write_disabled(self):
        base = _read("b.plist")
        t = _parse(base, self.tmp, "b.plist")
        with self.assertRaises(PB3Error) as cm:
            self._build(t, base)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_build_keeps_utf8_bom(self):
        import re as _re
        raw = _read("a.plist")
        raw = _re.sub(rb"<\?xml[^>]*\?>\n", b"", raw, count=1)
        base = b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?>\n' + raw
        t = _parse(base, self.tmp)
        edited = LocTable([LocEntry("welcome", "Chào bạn", None, "native", "ok",
                                    SourceRef(["a.plist"], "a.plist"))], None, "apple_plist")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(plistlib.loads(new)["welcome"], "Chào bạn")

    def test_verify_accepts_good_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        base = _read("a.plist")
        t = _parse(base, self.tmp)
        new = plug.build(ctx, t, base)
        self.assertTrue(plug.verify(ctx, base, new)["ok"])
        bad = bytearray(new)
        bad[len(bad) // 2] ^= 0xFF
        self.assertFalse(plug.verify(ctx, base, bytes(bad))["ok"])

    def test_verify_xcstrings_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        base = _read("d.xcstrings")
        t = _parse(base, self.tmp, "d.xcstrings")
        new = plug.build(ctx, t, base)
        self.assertTrue(plug.verify(ctx, base, new)["ok"])
        bad = bytearray(new)
        bad[len(bad) // 2] ^= 0xFF
        self.assertFalse(plug.verify(ctx, base, bytes(bad))["ok"])

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestPlistConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(ApplePlistPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        files = {n: _read(n) for n in sorted(os.listdir(FIX))}
        files["e.txt"] = b"hello"
        ctx = _ctx(files, self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        # XML ghi được lên trước, plist nhị phân (chỉ đọc) xếp cuối
        self.assertEqual(cands[0].path, "a.plist")
        self.assertEqual(cands[-1].path, "b.plist")
        self.assertTrue(all(c.adapter_id == "apple_plist" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.plist": _read("a.plist")}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.plist", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 9)

    def test_sniff_scores(self):
        plug = _plug()
        self.assertEqual(plug._sniff(b"bplist00abc", "x.plist"), 0.9)
        self.assertEqual(plug._sniff(b'<?xml version="1.0"?><plist version="1.0">', "x.plist"), 0.8)
        self.assertEqual(plug._sniff(b'{"strings": {', "x.xcstrings"), 0.8)
        self.assertEqual(plug._sniff(b"hello", "x.plist"), 0.0)
        self.assertFalse(plug._is_writable("b.plist", b"bplist00abc"))
        self.assertTrue(plug._is_writable("a.plist", b"<?xml"))


if __name__ == "__main__":
    unittest.main()
