"""Test A7 — parity .xcstrings giữa apple_plist và parseXcstrings của
IPA-LOCALIZATION (js/modules/text_extract.js).

Hai gap đã sửa trong pb3/plugins/apple_plist/plugin.py:
(1) mục không có localizations nhưng có sourceLanguage -> hàng khóa gốc
    (text = khóa, lang = sourceLanguage);
(2) localization/biến thể chỉ có variations, không có stringUnit -> hàng
    rỗng với ghi chú "variations: chưa trích".
Cả hai hàng chỉ để thấy — chưa hỗ trợ thêm localization mới / tạo
stringUnit nên build từ chối (fail-closed) nếu người dùng sửa giá trị.
Fixture mô phỏng Localizable.xcstrings của make_text_ipas.py — [FIXTURE].
"""
import json
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.apple_plist.plugin import ApplePlistPlugin
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "apple_plist")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _xcstrings_bytes():
    doc = {
        "sourceLanguage": "en",
        "strings": {
            "Play": {"localizations": {
                "en": {"stringUnit": {"state": "translated",
                                      "value": "Play"}},
                "vi": {"stringUnit": {"state": "translated",
                                      "value": "Chơi"}}}},
            "NoLoc": {},
            "VarOnly": {"localizations": {
                "en": {"variations": {
                    "plural": {
                        "one": {"stringUnit": {"value": "one item"}},
                        "other": {}}}}}},
            "TopVar": {"variations": {
                "plural": {
                    "one": {"stringUnit": {"value": "top one"}},
                    "other": {}}}},
        },
    }
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None,
                         None, "apple_plist")


def _plug():
    return ApplePlistPlugin()


def _parse(data, tmp, path="Localizable.xcstrings"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


def _build(table, base, tmp, path="Localizable.xcstrings"):
    plug = _plug()
    ctx = _ctx({path: base}, tmp).for_plugin(plug.manifest)
    return plug.build(ctx, table, base)


class TestXcstringsFallbacks(TmpCase):
    def test_source_language_fallback_row(self):
        t = _parse(_xcstrings_bytes(), self.tmp)
        by_key = {(e.key, e.locale): e for e in t.entries}
        row = by_key[("NoLoc", "en")]
        self.assertEqual(row.value, "NoLoc")
        self.assertEqual(row.status, "ok")
        self.assertIn("khóa gốc (chưa có localizations)", row.notes)

    def test_no_localizations_no_source_language_warns(self):
        doc = {"strings": {"Lonely": {}}}
        t = _parse(json.dumps(doc).encode(), self.tmp)
        self.assertEqual(t.entries, [])
        self.assertTrue(any(
            w.get("msg") == "mục xcstrings không có localization nào"
            for w in t.warnings))

    def test_variations_only_empty_rows(self):
        t = _parse(_xcstrings_bytes(), self.tmp)
        by_key = {(e.key, e.locale): e for e in t.entries}
        base = by_key[("VarOnly", "en")]
        self.assertEqual(base.value, "")
        self.assertEqual(base.status, "empty")
        self.assertIn("variations: chưa trích", base.notes)
        # Biến thể CÓ stringUnit vẫn trích như thường.
        self.assertEqual(by_key[("VarOnly.plural.one", "en")].value,
                         "one item")
        # Biến thể KHÔNG có stringUnit -> hàng rỗng (khóa ghép).
        other = by_key[("VarOnly.plural.other", "en")]
        self.assertEqual(other.value, "")
        self.assertEqual(other.status, "empty")
        self.assertIn("variations: chưa trích", other.notes)

    def test_top_level_variations(self):
        t = _parse(_xcstrings_bytes(), self.tmp)
        by_key = {(e.key, e.locale): e for e in t.entries}
        self.assertEqual(by_key[("TopVar.plural.one", None)].value, "top one")
        other = by_key[("TopVar.plural.other", None)]
        self.assertEqual(other.value, "")
        self.assertEqual(other.status, "empty")

    def test_full_row_set(self):
        t = _parse(_xcstrings_bytes(), self.tmp)
        got = sorted((e.key, e.locale, e.value) for e in t.entries)
        self.assertEqual(got, sorted([
            ("Play", "en", "Play"),
            ("Play", "vi", "Chơi"),
            ("NoLoc", "en", "NoLoc"),
            ("VarOnly", "en", ""),
            ("VarOnly.plural.one", "en", "one item"),
            ("VarOnly.plural.other", "en", ""),
            ("TopVar.plural.one", None, "top one"),
            ("TopVar.plural.other", None, ""),
        ]))


class TestXcstringsFallbackBuild(TmpCase):
    def _table_with(self, t, edits):
        entries = []
        for e in t.entries:
            value = edits.get((e.key, e.locale), e.value)
            entries.append(LocEntry(e.key, value, e.locale, "native",
                                    e.status,
                                    SourceRef(["Localizable.xcstrings"],
                                              "Localizable.xcstrings"),
                                    list(e.notes)))
        return LocTable(entries, None, "apple_plist")

    def test_unedited_fallback_rows_build_fine(self):
        base = _xcstrings_bytes()
        t = _parse(base, self.tmp)
        new = _build(self._table_with(t, {("Play", "vi"): "Chơi game"}),
                     base, self.tmp)
        t2 = _parse(new, self.tmp)
        by_key = {(e.key, e.locale): e.value for e in t2.entries}
        self.assertEqual(by_key[("Play", "vi")], "Chơi game")
        self.assertEqual(by_key[("NoLoc", "en")], "NoLoc")
        self.assertEqual(by_key[("VarOnly", "en")], "")

    def test_edited_fallback_row_fails_closed(self):
        base = _xcstrings_bytes()
        t = _parse(base, self.tmp)
        with self.assertRaises(PB3Error) as cm:
            _build(self._table_with(t, {("NoLoc", "en"): "Không chỗ"}),
                   base, self.tmp)
        self.assertEqual(cm.exception.code, "E_VERIFY_FAILED")

    def test_edited_variations_only_row_fails_closed(self):
        base = _xcstrings_bytes()
        t = _parse(base, self.tmp)
        with self.assertRaises(PB3Error) as cm:
            _build(self._table_with(t, {("VarOnly", "en"): "Một mục"}),
                   base, self.tmp)
        self.assertEqual(cm.exception.code, "E_VERIFY_FAILED")


class TestXcstringsRegression(TmpCase):
    def test_existing_fixture_unchanged(self):
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


if __name__ == "__main__":
    unittest.main()
