"""Test plugin extractor dò chuỗi thô (Claude 06): parse/conformance, chỉ đọc."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.generic_strings.plugin import GenericStringsPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "generic_strings")

SAMPLE = (b"\x00\x01\x02\x03\xff\xfe"
          b"Hello 9 Kings\x00"
          + bytes(range(1, 32))
          + "Xin chao".encode("utf-16-le") + b"\x00\x00"
          + b"\x89PNG\r\n\x1a\n"
          + "Choi ngay".encode("utf-16-be") + b"\x00\x00"
          + b"short\x00ab\x00"
          + b"Tam biet nhe\x00")


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None,
                         "generic_strings")


def _plug():
    return GenericStringsPlugin()


def _parse(data, tmp, path="a.bin"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestStringsParse(TmpCase):
    def test_ascii_and_utf16_strings_with_offsets(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["off_00000006", "off_00000033", "off_0000004d",
                          "off_00000061", "off_0000006a"])
        self.assertEqual(by_key["off_00000006"].value, "Hello 9 Kings")
        self.assertEqual(by_key["off_00000006"].source.entry_offset, 6)
        self.assertEqual(by_key["off_00000033"].value, "Xin chao")
        self.assertEqual(by_key["off_00000033"].source.encoding, "utf-16-le")
        self.assertEqual(by_key["off_0000004d"].value, "Choi ngay")
        self.assertEqual(by_key["off_0000004d"].source.encoding, "utf-16-be")
        self.assertEqual(by_key["off_00000061"].value, "short")
        self.assertEqual(by_key["off_0000006a"].value, "Tam biet nhe")
        self.assertEqual(t.stats["chuoi_tim_thay"], 5)

    def test_short_strings_ignored(self):
        t = _parse(b"ab\x00abc\x00abcd\x00", self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("off_00000007", "abcd")])

    def test_no_strings_empty_table(self):
        t = _parse(b"\x00\x01\x02\x03", self.tmp)
        self.assertEqual(t.entries, [])
        self.assertEqual(t.stats["chuoi_tim_thay"], 0)

    def test_raw_scan_warning_always_present(self):
        t = _parse(SAMPLE, self.tmp)
        self.assertTrue(any("dò chuỗi thô" in w.get("msg", "") for w in t.warnings))
        t2 = _parse(b"\x00\x01\x02", self.tmp)
        self.assertTrue(any("dò chuỗi thô" in w.get("msg", "") for w in t2.warnings))

    def test_limit_5000_with_total_warning(self):
        data = b"".join(b"s%04d\x00" % i for i in range(6000))
        t = _parse(data, self.tmp)
        self.assertEqual(len(t.entries), 5000)
        self.assertEqual(t.stats["chuoi_tim_thay"], 6000)
        self.assertTrue(any("6000" in w.get("msg", "") and "5000" in w.get("msg", "")
                            for w in t.warnings))

    def test_keys_stable_and_reproducible(self):
        t1 = _parse(SAMPLE, self.tmp)
        t2 = _parse(SAMPLE, self.tmp)
        self.assertEqual([e.key for e in t1.entries], [e.key for e in t2.entries])
        self.assertEqual([e.value for e in t1.entries], [e.value for e in t2.entries])


class TestStringsReadOnly(TmpCase):
    def test_build_and_verify_raise_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        table = _parse(SAMPLE, self.tmp)
        with self.assertRaises(PB3Error) as cm:
            plug.build(ctx, table, SAMPLE)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")
        with self.assertRaises(PB3Error) as cm:
            plug.verify(ctx, SAMPLE, SAMPLE)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        res = plug.selftest(ctx)
        self.assertTrue(res["ok"])
        self.assertIn("chỉ đọc", res["checked"])

    def test_candidate_paths_exclude_known_formats(self):
        plug = _plug()
        files = {"a.json": b"{}", "b.po": b"", "c": b"hello", "d.unity3d": b"",
                 "e.txt": b"hello", "f.ipa": b"", "g.resx": b"", "h": b"world"}
        ctx = _ctx(files, self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["c", "e.txt", "h"])

    def test_detect_content_evidence(self):
        plug = _plug()
        ctx = _ctx({"a.bin": SAMPLE, "b.json": b"{}"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        ev = plug.detect(ctx)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].kind, "content")
        self.assertEqual(ev[0].value, "strings")
        self.assertEqual(ev[0].weight, 0.5)

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.bin": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.bin", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 5)


class TestStringsConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(GenericStringsPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("readonly").status, "PASS")
        self.assertEqual(rep.get("write").status, "SKIP")
        self.assertEqual(rep.get("selftest").status, "PASS")


if __name__ == "__main__":
    unittest.main()
