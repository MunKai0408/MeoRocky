"""Test plugin XLIFF (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.xliff.plugin import XliffPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "xliff")

SAMPLE_12 = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
             b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
             b'  <!-- Ghi chu file. -->\n'
             b'  <file source-language="en" target-language="vi">\n'
             b'    <body>\n'
             b'      <trans-unit id="hello" resname="greeting">\n'
             b'        <source>Hello</source><target>Xin chao</target>\n'
             b'        <note>Loi chao o man hinh chinh.</note>\n'
             b'      </trans-unit>\n'
             b'      <trans-unit id="bye"><source>Goodbye</source></trans-unit>\n'
             b'      <trans-unit id="styled"><source>Tap <g id="1">here</g> now</source>'
             b'<target>Cham <g id="1">vao day</g> ngay</target></trans-unit>\n'
             b'    </body>\n'
             b'  </file>\n'
             b'</xliff>\n')

SAMPLE_20 = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
             b'<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" '
             b'srcLang="en" trgLang="vi">\n'
             b'  <file id="game">\n'
             b'    <unit id="play">\n'
             b'      <segment><source>Play</source><target>Choi</target></segment>\n'
             b'      <segment><source>Play again</source><target>Choi lai</target></segment>\n'
             b'    </unit>\n'
             b'    <unit id="one"><segment><source>One item</source>'
             b'<target>Mot vat pham</target></segment></unit>\n'
             b'  </file>\n'
             b'</xliff>\n')


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "xliff")


def _plug():
    return XliffPlugin()


def _parse(data, tmp, path="a.xlf"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestXliffParse(TmpCase):
    def test_12_keys_values_notes_locale(self):
        t = _parse(SAMPLE_12, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["greeting", "bye", "styled"])
        self.assertEqual(by_key["greeting"].value, "Xin chao")
        self.assertEqual(by_key["greeting"].notes, ["Loi chao o man hinh chinh."])
        self.assertEqual(by_key["greeting"].locale, "vi")
        self.assertEqual(by_key["bye"].value, "Goodbye")
        self.assertEqual(by_key["bye"].notes, ["chưa-dịch"])
        self.assertEqual(t.schema_hint.get("format"), "xliff")
        self.assertEqual(t.schema_hint.get("version"), "1.2")

    def test_12_markup_target_unparsed(self):
        t = _parse(SAMPLE_12, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["styled"].status, "unparsed")
        self.assertEqual(by_key["styled"].value, "Cham vao day ngay")
        self.assertIn("có-markup-nội-tuyến", by_key["styled"].notes)
        self.assertEqual(t.stats["unparsed"], 1)
        self.assertTrue(any(w.get("key") == "styled" for w in t.warnings))

    def test_20_units_and_segments(self):
        t = _parse(SAMPLE_20, self.tmp, path="a.xliff")
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries], ["play", "play[1]", "one"])
        self.assertEqual(by_key["play"].value, "Choi")
        self.assertEqual(by_key["play[1]"].value, "Choi lai")
        self.assertEqual(by_key["play"].locale, "vi")
        self.assertEqual(t.schema_hint.get("version"), "2.0")

    def test_unicode_and_bom(self):
        data = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<xliff version="1.2"><file target-language="vi"><body>'
                '<trans-unit id="a"><source>Hi</source>'
                '<target>Xin chào 🌟</target></trans-unit>'
                '</body></file></xliff>\n').encode("utf-8")
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")
        bom = b"\xef\xbb\xbf" + data
        t = _parse(bom, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")

    def test_entity_rejected(self):
        bad = (b'<?xml version="1.0"?>\n'
               b'<!DOCTYPE xliff [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
               b'<xliff version="1.2"><file><body>'
               b'<trans-unit id="a"><source>&xxe;</source></trans-unit>'
               b'</body></file></xliff>\n')
        with self.assertRaises(PB3Error) as cm:
            _parse(bad, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_malformed_xml_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<xliff><file><body><trans-unit id="a">', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_non_xliff_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<root><data name="a"><value>x</value></data></root>', self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_binary_garbage_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"\x00\x01\x02\xff\xfe\x80\x81", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestXliffBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_comment_and_declaration(self):
        t = _parse(SAMPLE_12, self.tmp)
        edited = LocTable([
            LocEntry("greeting", "Chào bạn", None, "native", "ok",
                     SourceRef(["a.xlf"], "a.xlf")),
            LocEntry("bye", "Tạm biệt", None, "native", "ok",
                     SourceRef(["a.xlf"], "a.xlf")),
        ], None, "xliff")
        new = self._build(edited, SAMPLE_12)
        text = new.decode("utf-8")
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertIn('<!-- Ghi chu file. -->', text)
        self.assertIn('<target>Chào bạn</target>', text)
        self.assertIn('<target>Tạm biệt</target>', text)
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["greeting"].value, "Chào bạn")
        self.assertEqual(by_key["bye"].value, "Tạm biệt")
        self.assertEqual(by_key["bye"].notes, [])
        self.assertEqual([e.key for e in back.entries], [e.key for e in t.entries])

    def test_build_inserts_target_when_changed(self):
        base = (b'<xliff version="1.2"><file target-language="vi"><body>'
                b'<trans-unit id="a"><source>Hello</source></trans-unit>'
                b'</body></file></xliff>')
        edited = LocTable([LocEntry("a", "Xin chào", None, "native", "ok",
                                    SourceRef(["a.xlf"], "a.xlf"))], None, "xliff")
        new = self._build(edited, base)
        back = _parse(new, self.tmp)
        self.assertEqual(back.entries[0].value, "Xin chào")
        self.assertEqual(back.entries[0].notes, [])

    def test_build_leaves_untranslated_unchanged(self):
        base = (b'<xliff version="1.2"><file target-language="vi"><body>'
                b'<trans-unit id="a"><source>Hello</source></trans-unit>'
                b'</body></file></xliff>')
        edited = LocTable([LocEntry("a", "Hello", None, "native", "ok",
                                    SourceRef(["a.xlf"], "a.xlf"))], None, "xliff")
        new = self._build(edited, base)
        self.assertNotIn(b"<target>", new)

    def test_build_skips_unparsed_with_warning(self):
        t = _parse(SAMPLE_12, self.tmp)
        new = self._build(t, SAMPLE_12)
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["styled"].value, "Cham vao day ngay")
        self.assertEqual(by_key["styled"].status, "unparsed")
        self.assertTrue(any(w.get("key") == "styled" for w in t.warnings))
        self.assertIn('<g id="1">vao day</g>', new.decode("utf-8"))

    def test_build_20_multi_segment(self):
        t = _parse(SAMPLE_20, self.tmp, path="a.xliff")
        edited = LocTable([LocEntry("play[1]", "Chơi lại nhé", None, "native", "ok",
                                    SourceRef(["a.xliff"], "a.xliff"))], None, "xliff")
        new = self._build(edited, SAMPLE_20)
        back = _parse(new, self.tmp, path="a.xliff")
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["play"].value, "Choi")
        self.assertEqual(by_key["play[1]"].value, "Chơi lại nhé")

    def test_verify_accepts_good_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        t = _parse(SAMPLE_12, self.tmp)
        new = plug.build(ctx, t, SAMPLE_12)
        self.assertTrue(plug.verify(ctx, SAMPLE_12, new)["ok"])
        bad = bytearray(new)
        bad[len(bad) // 2] ^= 0xFF
        self.assertFalse(plug.verify(ctx, SAMPLE_12, bytes(bad))["ok"])

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestXliffConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(XliffPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.xlf": SAMPLE_12, "b.txt": b"hello",
                    "c.xliff": SAMPLE_20}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.xlf", "c.xliff"])
        self.assertTrue(all(c.adapter_id == "xliff" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "content" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.xlf": SAMPLE_12}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.xlf", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 3)


if __name__ == "__main__":
    unittest.main()
