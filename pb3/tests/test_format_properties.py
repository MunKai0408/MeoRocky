"""Test plugin Java .properties (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.properties.plugin import PropertiesPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "properties")

SAMPLE = (b"# Chuoi properties mau\n! Chu thich kieu !\nhello=Xin chao\\nthe gioi\n"
          b"bye: Tam biet\nkey\\ with\\ space = gia tri co khoang trang\nno_sep\n"
          b"cont=dong mot\\\n  dong hai\nuni=caf\\u00e9\n")


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "properties")


def _plug():
    return PropertiesPlugin()


def _parse(data, tmp, path="a.properties"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestPropertiesParse(TmpCase):
    def test_sample(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(list(by_key), ["hello", "bye", "key with space", "no_sep", "cont", "uni"])
        self.assertEqual(by_key["hello"].value, "Xin chao\nthe gioi")
        self.assertEqual(by_key["bye"].value, "Tam biet")
        self.assertEqual(by_key["key with space"].value, "gia tri co khoang trang")
        self.assertEqual(by_key["no_sep"].value, "")
        self.assertEqual(by_key["no_sep"].status, "empty")
        self.assertEqual(by_key["cont"].value, "dong motdong hai")
        self.assertEqual(by_key["uni"].value, "café")
        self.assertEqual(t.adapter_id, "properties")

    def test_separators(self):
        t = _parse(b"a=1\nb: 2\nc 3\nd\n", self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("a", "1"), ("b", "2"), ("c", "3"), ("d", "")])

    def test_escapes(self):
        t = _parse(b"a=x\\ty\\nz\\rw\\\\v\\f\\=\\:\\  w\n", self.tmp)
        self.assertEqual(t.entries[0].value, "x\ty\nz\rw\\v\x0c=:  w")

    def test_unicode_escape_bad_raises(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"a=\\u12xz\n", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_duplicate_key_conflict_keep_first(self):
        t = _parse(b"a=1\na=2\n", self.tmp)
        self.assertEqual([e.value for e in t.entries], ["1", "2"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertTrue(any(c.get("key") == "a" for c in t.conflicts))

    def test_utf8_bom_and_emoji(self):
        data = b"\xef\xbb\xbfhello=Xin ch\xc3\xa0\nemoji=Ch\xc3\xa0o b\xe1\xba\xa1n \xf0\x9f\x8c\x9f\n"
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[1].value, "Chào bạn 🌟")
        self.assertEqual(t.entries[0].source.encoding, "utf-8")

    def test_latin1_fallback(self):
        t = _parse(b"cafe=caf\xe9\nhello=Xin chao\n", self.tmp)
        self.assertEqual(t.entries[0].value, "café")
        self.assertEqual(t.entries[0].source.encoding, "latin-1")

    def test_continuation_multiline(self):
        t = _parse(b"a=dong mot\\\n    dong hai\\\n    dong ba\n", self.tmp)
        self.assertEqual(t.entries[0].value, "dong motdong haidong ba")

    def test_escaped_backslash_before_newline_not_continuation(self):
        # Hai backslash cuối dòng: backslash escape, không nối dòng.
        t = _parse(b"a=x\\\\\nb=y\n", self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries], [("a", "x\\"), ("b", "y")])

    def test_leading_whitespace_and_comments(self):
        t = _parse(b"   # comment\n   ! comment 2\n   a = 1\n", self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries], [("a", "1")])


class TestPropertiesBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_comments_order_separators(self):
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties")),
                           LocEntry("cont", "dong moi", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties")),
                           LocEntry("no_sep", "co gia tri", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties"))],
                          None, "properties")
        new = self._build(edited, SAMPLE)
        text = new.decode("utf-8")
        self.assertIn("# Chuoi properties mau", text)
        self.assertIn("! Chu thich kieu !", text)
        self.assertIn("bye: Tam biet", text)
        self.assertIn("key\\ with\\ space = gia tri co khoang trang", text)
        self.assertIn("uni=caf\\u00e9", text)
        back = _parse(new, self.tmp)
        by_key = {e.key: e.value for e in back.entries}
        self.assertEqual(by_key["hello"], "Chao ban")
        self.assertEqual(by_key["cont"], "dong moi")
        self.assertEqual(by_key["no_sep"], "co gia tri")
        self.assertEqual(by_key["uni"], "café")

    def test_build_escapes_newline_and_separators(self):
        base = b"a=cu\n"
        edited = LocTable([LocEntry("a", "x=y:z\nw", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties"))],
                          None, "properties")
        new = self._build(edited, base)
        self.assertEqual(new, b"a=x\\=y\\:z\\nw\n")
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "x=y:z\nw")

    def test_build_latin1_escapes_non_ascii(self):
        base = b"cafe=caf\xe9\nhello=Xin chao\n"
        edited = LocTable([LocEntry("cafe", "caf\xe9 m\xe9i", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties"))],
                          None, "properties")
        new = self._build(edited, base)
        self.assertEqual(new, b"cafe=caf\\u00e9 m\\u00e9i\nhello=Xin chao\n")
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "café méi")

    def test_build_utf8_keeps_non_ascii_and_bom(self):
        base = b"\xef\xbb\xbfhello=Xin ch\xc3\xa0\n"
        edited = LocTable([LocEntry("hello", "Chào bạn", None, "native", "ok",
                                    SourceRef(["a.properties"], "a.properties"))],
                          None, "properties")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Chào bạn")

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


class TestPropertiesConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(PropertiesPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.properties": SAMPLE, "b.txt": b"hello"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.properties"])
        self.assertTrue(all(c.adapter_id == "properties" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.properties": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.properties", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 6)


if __name__ == "__main__":
    unittest.main()
