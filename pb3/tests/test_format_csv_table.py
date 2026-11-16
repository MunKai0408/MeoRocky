"""Test plugin CSV/TSV (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.csv_table.plugin import CsvPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "csv_table")

SAMPLE = (b"key,en,vi\nhello,Hello,Xin chao\nbye,Bye,\n"
          b'menu_new,New Game,"Tro choi moi, hay nhat"\nsettings,Settings,Cai dat\n')


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "csv_table")


def _plug():
    return CsvPlugin()


def _parse(data, tmp, path="a.csv"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestCsvParse(TmpCase):
    def test_header_locales_and_order(self):
        t = _parse(SAMPLE, self.tmp)
        self.assertEqual([(e.key, e.locale) for e in t.entries],
                         [("hello", "en"), ("hello", "vi"), ("bye", "en"), ("bye", "vi"),
                          ("menu_new", "en"), ("menu_new", "vi"),
                          ("settings", "en"), ("settings", "vi")])
        by_key = {(e.key, e.locale): e for e in t.entries}
        self.assertEqual(by_key[("hello", "vi")].value, "Xin chao")
        self.assertEqual(by_key[("bye", "vi")].value, "")
        self.assertEqual(by_key[("bye", "vi")].status, "empty")
        self.assertEqual(by_key[("menu_new", "vi")].value, "Tro choi moi, hay nhat")
        self.assertIn("csv:0:2", by_key[("hello", "vi")].notes)
        self.assertEqual(t.stats["rows"], 4)
        self.assertEqual(t.stats["columns"], 3)
        self.assertEqual(t.stats["delimiter"], ",")
        self.assertTrue(t.stats["header"])

    def test_no_header_col0_key_col1_value(self):
        t = _parse(b"hello,Xin chao\nbye,Tam biet\n", self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("hello", "Xin chao"), ("bye", "Tam biet")])
        self.assertFalse(t.stats["header"])

    def test_empty_key_gets_stable_row_key(self):
        t = _parse(b",Gia tri mot\n,Gia tri hai\n", self.tmp)
        self.assertEqual([e.key for e in t.entries], ["row_000000", "row_000001"])

    def test_tsv_delimiter_and_header(self):
        t = _parse(b"key\ten\tvi\nhello\tHello\tXin chao\n", self.tmp, "a.tsv")
        self.assertEqual(t.stats["delimiter"], "tab")
        self.assertEqual([(e.key, e.locale, e.value) for e in t.entries],
                         [("hello", "en", "Hello"), ("hello", "vi", "Xin chao")])

    def test_semicolon_delimiter(self):
        t = _parse(b"key;value\nhello;Xin chao\n", self.tmp)
        self.assertEqual(t.stats["delimiter"], ";")
        self.assertEqual(t.entries[0].value, "Xin chao")

    def test_delimiter_tie_csv_takes_comma(self):
        t = _parse(b"a,b;c\n", self.tmp)
        self.assertEqual(t.stats["delimiter"], ",")

    def test_duplicate_key_same_locale_conflict(self):
        t = _parse(b"key,value\nhello,A\nhello,B\n", self.tmp)
        self.assertEqual([e.value for e in t.entries], ["A", "B"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertTrue(any(c.get("key") == "hello" for c in t.conflicts))

    def test_unicode_bom_and_emoji(self):
        data = b"\xef\xbb\xbfkey,vi\nhello,Xin ch\xc3\xa0\nemoji,Ch\xc3\xa0o b\xe1\xba\xa1n \xf0\x9f\x8c\x9f\n"
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[1].value, "Chào bạn 🌟")
        self.assertEqual(t.entries[0].source.encoding, "utf-8")

    def test_utf16(self):
        data = "key,value\nhello,Xin chào\n".encode("utf-16")
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào")

    def test_corrupt_unterminated_quote(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'key,value\n"chua dong\n', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_header_case_insensitive(self):
        t = _parse(b"KEY,EN\nhello,Xin chao\n", self.tmp)
        self.assertTrue(t.stats["header"])
        self.assertEqual(t.entries[0].locale, "EN")


class TestCsvBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_edits_one_cell_keeps_rest(self):
        edited = LocTable([LocEntry("hello", "Chao ban", "vi", "native", "ok",
                                    SourceRef(["a.csv"], "a.csv"), ["csv:0:2"])],
                          None, "csv_table")
        new = self._build(edited, SAMPLE)
        back = _parse(new, self.tmp)
        by_key = {(e.key, e.locale): e.value for e in back.entries}
        self.assertEqual(by_key[("hello", "vi")], "Chao ban")
        self.assertEqual(by_key[("hello", "en")], "Hello")
        self.assertEqual(by_key[("menu_new", "vi")], "Tro choi moi, hay nhat")
        self.assertEqual(by_key[("bye", "vi")], "")

    def test_round_trip_no_header(self):
        base = b"hello,Xin chao\nbye,Tam biet\n"
        edited = LocTable([LocEntry("bye", "Chao nhe", None, "native", "ok",
                                    SourceRef(["a.csv"], "a.csv"), ["csv:1:1"])],
                          None, "csv_table")
        new = self._build(edited, base)
        self.assertEqual(_parse(new, self.tmp).entries[1].value, "Chao nhe")

    def test_build_keeps_crlf(self):
        base = b"key,value\r\nhello,Xin chao\r\n"
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.csv"], "a.csv"), ["csv:0:1"])],
                          None, "csv_table")
        new = self._build(edited, base)
        self.assertIn(b"\r\n", new)
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Chao ban")

    def test_build_value_with_delimiter_gets_quoted(self):
        edited = LocTable([LocEntry("hello", "Xin, chao", "vi", "native", "ok",
                                    SourceRef(["a.csv"], "a.csv"), ["csv:0:2"])],
                          None, "csv_table")
        new = self._build(edited, SAMPLE)
        back = _parse(new, self.tmp)
        self.assertEqual([e.value for e in back.entries
                          if e.key == "hello" and e.locale == "vi"][0], "Xin, chao")

    def test_verify_accepts_good_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        t = _parse(SAMPLE, self.tmp)
        new = plug.build(ctx, t, SAMPLE)
        self.assertTrue(plug.verify(ctx, SAMPLE, new)["ok"])
        bad = bytearray(new)
        bad[len(bad) // 2] ^= 0xFF
        self.assertFalse(plug.verify(ctx, SAMPLE, bytes(bad))["ok"])

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestCsvConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(CsvPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.csv": SAMPLE, "b.txt": b"hello", "c.tsv": b"k\tv\n"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.csv", "c.tsv"])
        self.assertTrue(all(c.adapter_id == "csv_table" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.csv": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.csv", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 8)


if __name__ == "__main__":
    unittest.main()
