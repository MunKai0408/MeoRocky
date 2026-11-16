"""Test plugin XML strings (Claude 06): parse/build/verify/conformance."""
import os
import unittest
import xml.etree.ElementTree as ET

from core.errors import PB3Error
from core.limits import Limits
from plugins.xml_strings.plugin import XmlPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "xml_strings")

SAMPLE = (b'<?xml version="1.0" encoding="utf-8"?>\n<resources>\n<!-- ghi chu -->\n'
          b'    <string name="hello">Xin chao</string>\n'
          b'    <string name="quote">Chao \\\'ban\\\' \\"nhe\\"</string>\n'
          b'    <string name="skip" translatable="false">Bo qua</string>\n'
          b'    <string-array name="tips"><item>Meo 1</item><item>Meo 2</item></string-array>\n'
          b'    <plurals name="items"><item quantity="one">1 muc</item>'
          b'<item quantity="other">%d muc</item></plurals>\n</resources>\n')
GENERIC = (b'<?xml version="1.0" encoding="utf-8"?>\n<strings>\n'
           b'    <string name="hello">Xin chao</string>\n'
           b'    <string key="bye">Tam biet</string>\n'
           b'    <title>Tieu de game</title>\n</strings>\n')


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "xml_strings")


def _plug():
    return XmlPlugin()


def _parse(data, tmp, path="a.xml"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestXmlParse(TmpCase):
    def test_android_resources(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(list(by_key), ["hello", "quote", "tips[0]", "tips[1]",
                                        "items[one]", "items[other]"])
        self.assertEqual(by_key["hello"].value, "Xin chao")
        self.assertEqual(by_key["quote"].value, "Chao 'ban' \"nhe\"")
        self.assertEqual(by_key["tips[1]"].value, "Meo 2")
        self.assertEqual(by_key["items[other]"].value, "%d muc")
        self.assertNotIn("skip", by_key)
        self.assertTrue(any(w.get("key") == "skip" for w in t.warnings))
        self.assertEqual(t.adapter_id, "xml_strings")

    def test_generic_form(self):
        t = _parse(GENERIC, self.tmp)
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key, {"hello": "Xin chao", "bye": "Tam biet",
                                  "title": "Tieu de game"})

    def test_android_escapes(self):
        data = b'<resources><string name="a">a\\n b\\t c\\u0041 \\@ d</string></resources>'
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "a\n b\t cA @ d")

    def test_unicode_and_vietnamese(self):
        t = _parse('<resources><string name="a">Xin chào 🌟</string></resources>'.encode("utf-8"),
                   self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")

    def test_utf16_bom(self):
        data = ('<?xml version="1.0" encoding="utf-16"?>\n<resources>'
                '<string name="hello">Xin chào</string></resources>').encode("utf-16")
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào")
        self.assertEqual(t.entries[0].source.encoding, "utf-16-le")

    def test_entity_rejected(self):
        data = (b'<?xml version="1.0"?><!DOCTYPE resources [<!ENTITY x "y">]>'
                b'<resources><string name="a">x</string></resources>')
        with self.assertRaises(PB3Error) as cm:
            _parse(data, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_malformed_rejected(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<resources><string name="a">x</resources>', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_empty_string_is_empty_status(self):
        t = _parse(b'<resources><string name="a"></string></resources>', self.tmp)
        self.assertEqual(t.entries[0].value, "")
        self.assertEqual(t.entries[0].status, "empty")

    def test_string_without_name_skipped_with_warning(self):
        t = _parse(b'<resources><string>Khong ten</string></resources>', self.tmp)
        self.assertEqual(t.entries, [])
        self.assertTrue(t.warnings)


class TestXmlBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_comment_and_rest(self):
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.xml"], "a.xml")),
                           LocEntry("tips[1]", "Meo hai", None, "native", "ok",
                                    SourceRef(["a.xml"], "a.xml"))],
                          None, "xml_strings")
        new = self._build(edited, SAMPLE)
        self.assertIn(b"ghi chu", new)
        back = _parse(new, self.tmp)
        by_key = {e.key: e.value for e in back.entries}
        self.assertEqual(by_key["hello"], "Chao ban")
        self.assertEqual(by_key["tips[1]"], "Meo hai")
        self.assertEqual(by_key["tips[0]"], "Meo 1")
        self.assertEqual(by_key["items[one]"], "1 muc")

    def test_build_re_escapes_android(self):
        edited = LocTable([LocEntry("hello", "Chào 'bạn'\nnhé", None, "native", "ok",
                                    SourceRef(["a.xml"], "a.xml"))], None, "xml_strings")
        new = self._build(edited, SAMPLE)
        back = _parse(new, self.tmp)
        self.assertEqual([e.value for e in back.entries if e.key == "hello"][0],
                         "Chào 'bạn'\nnhé")

    def test_build_generic(self):
        edited = LocTable([LocEntry("title", "Tieu de moi", None, "native", "ok",
                                    SourceRef(["a.xml"], "a.xml"))], None, "xml_strings")
        new = self._build(edited, GENERIC)
        root = ET.fromstring(new)
        self.assertEqual(root.find("title").text, "Tieu de moi")

    def test_build_keeps_utf16_bom(self):
        base = ('<?xml version="1.0" encoding="utf-16"?>\n<resources>'
                '<string name="hello">Xin chào</string></resources>').encode("utf-16")
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.xml"], "a.xml"))], None, "xml_strings")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xff\xfe"))
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Chao ban")

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


class TestXmlConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(XmlPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.xml": SAMPLE, "b.txt": b"hello", "c.xml": b"not xml"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.xml", "c.xml"])
        self.assertTrue(all(c.adapter_id == "xml_strings" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.xml": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.xml", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 6)


if __name__ == "__main__":
    unittest.main()
