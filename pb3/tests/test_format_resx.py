"""Test plugin RESX (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.resx.plugin import ResxPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "resx")

SAMPLE = (b'<?xml version="1.0" encoding="utf-8"?>\n'
          b'<root>\n'
          b'  <!-- Ghi chu file. -->\n'
          b'  <resheader name="resmimetype"><value>text/microsoft-resx</value></resheader>\n'
          b'  <data name="hello" xml:space="preserve"><value>Xin chao</value>'
          b'<comment>Loi chao o man hinh chinh.</comment></data>\n'
          b'  <data name="padded" xml:space="preserve">'
          b'<value>  co khoang trang hai dau  </value></data>\n'
          b'  <data name="trimmed"><value>\n      Xin chao da trim\n    </value></data>\n'
          b'  <data name="empty" xml:space="preserve"><value></value></data>\n'
          b'  <data name="icon" type="System.Drawing.Bitmap" '
          b'mimetype="application/x-microsoft.net.object.bytearray.base64">'
          b'<value>aGVsbG8=</value></data>\n'
          b'</root>\n')


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "resx")


def _plug():
    return ResxPlugin()


def _parse(data, tmp, path="a.resx"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestResxParse(TmpCase):
    def test_sample_keys_values_notes(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["hello", "padded", "trimmed", "empty", "icon"])
        self.assertEqual(by_key["hello"].value, "Xin chao")
        self.assertEqual(by_key["hello"].notes, ["Loi chao o man hinh chinh."])
        self.assertEqual(by_key["empty"].status, "empty")
        self.assertEqual(t.schema_hint.get("format"), "resx")

    def test_preserve_keeps_spaces_default_trims(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["padded"].value, "  co khoang trang hai dau  ")
        self.assertEqual(by_key["trimmed"].value, "Xin chao da trim")

    def test_typed_data_unparsed(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["icon"].status, "unparsed")
        self.assertEqual(by_key["icon"].value, "aGVsbG8=")
        self.assertTrue(any("không phải chuỗi" in n for n in by_key["icon"].notes))
        self.assertEqual(t.stats["unparsed"], 1)
        self.assertTrue(any(w.get("key") == "icon" for w in t.warnings))

    def test_unicode_and_bom(self):
        data = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<root><data name="a" xml:space="preserve">'
                '<value>Xin chào 🌟</value></data></root>\n').encode("utf-8")
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")
        bom = b"\xef\xbb\xbf" + data
        t = _parse(bom, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")

    def test_entity_rejected(self):
        bad = (b'<?xml version="1.0"?>\n'
               b'<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
               b'<root><data name="a"><value>&xxe;</value></data></root>\n')
        with self.assertRaises(PB3Error) as cm:
            _parse(bad, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_malformed_xml_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<root><data name="a"><value>x</value>', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_non_resx_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'<xliff version="1.2"></xliff>', self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_binary_garbage_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"\x00\x01\x02\xff\xfe\x80\x81", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestResxBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_resheader_comment_declaration(self):
        t = _parse(SAMPLE, self.tmp)
        edited = LocTable([
            LocEntry("hello", "Chào bạn", None, "native", "ok",
                     SourceRef(["a.resx"], "a.resx")),
            LocEntry("padded", "  đã sửa  ", None, "native", "ok",
                     SourceRef(["a.resx"], "a.resx")),
        ], None, "resx")
        new = self._build(edited, SAMPLE)
        text = new.decode("utf-8")
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertIn('<!-- Ghi chu file. -->', text)
        self.assertIn('<value>text/microsoft-resx</value>', text)
        self.assertIn('<value>Chào bạn</value>', text)
        self.assertIn('<comment>Loi chao o man hinh chinh.</comment>', text)
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["hello"].value, "Chào bạn")
        self.assertEqual(by_key["padded"].value, "  đã sửa  ")
        self.assertEqual(by_key["trimmed"].value, "Xin chao da trim")
        self.assertEqual([e.key for e in back.entries], [e.key for e in t.entries])

    def test_build_skips_unparsed_with_warning(self):
        t = _parse(SAMPLE, self.tmp)
        new = self._build(t, SAMPLE)
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["icon"].value, "aGVsbG8=")
        self.assertEqual(by_key["icon"].status, "unparsed")
        self.assertTrue(any(w.get("key") == "icon" for w in t.warnings))

    def test_build_inserts_missing_value(self):
        base = ('<root><data name="a" xml:space="preserve">'
                '<comment>ghi chú</comment></data></root>').encode("utf-8")
        edited = LocTable([LocEntry("a", "Xin chào", None, "native", "ok",
                                    SourceRef(["a.resx"], "a.resx"))], None, "resx")
        new = self._build(edited, base)
        back = _parse(new, self.tmp)
        self.assertEqual(back.entries[0].value, "Xin chào")

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


class TestResxConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(ResxPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.resx": SAMPLE, "b.txt": b"hello",
                    "c.resx": b"<root></root>"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.resx", "c.resx"])
        self.assertTrue(all(c.adapter_id == "resx" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "content" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.resx": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.resx", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 5)


if __name__ == "__main__":
    unittest.main()
