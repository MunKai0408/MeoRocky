"""Test plugin Apple .strings (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.apple_strings.plugin import AppleStringsPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "apple_strings")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "apple_strings")


def _plug():
    return AppleStringsPlugin()


def _parse(data, tmp, path="a.strings"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestStringsParse(TmpCase):
    def test_utf8_parse_escapes_comments_placeholders(self):
        t = _parse(_read("a.strings"), self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["hello", "bye", "welcome", "quote", "path", "empty", "emoji"])
        self.assertEqual(by_key["hello"].value, "Xin chào")
        self.assertEqual(by_key["bye"].value, "Tạm biệt\nHẹn gặp lại")
        self.assertEqual(by_key["welcome"].value, "Chào mừng, %@! Bạn có %d tin nhắn mới.")
        self.assertEqual(by_key["quote"].value, 'Cô ấy nói "Xin chào"')
        self.assertEqual(by_key["path"].value, "C:\\Games\\9Kings")
        self.assertEqual(by_key["empty"].status, "empty")
        self.assertEqual(by_key["emoji"].value, "Chúc mừng 🎉")
        self.assertEqual(t.adapter_id, "apple_strings")
        self.assertTrue(all(isinstance(e.source, SourceRef) for e in t.entries))

    def test_utf16le_bom_parse(self):
        t = _parse(_read("b.strings"), self.tmp, "b.strings")
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries], ["hello", "lives", "tab", "unicode"])
        self.assertEqual(by_key["hello"].value, "Xin chào")
        self.assertEqual(by_key["lives"].value, "Bạn còn %d mạng")
        self.assertEqual(by_key["tab"].value, "Cột\tnày")
        self.assertEqual(by_key["unicode"].value, "Tiếng Việt có dấu: ễ ộ 🌟")
        self.assertEqual(by_key["hello"].source.encoding, "utf-16-le")

    def test_unicode_escape(self):
        t = _parse('"a" = "Xin ch\\u00e0o";'.encode("utf-8"), self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào")

    def test_duplicate_key_conflict_keeps_first(self):
        t = _parse(b'"a" = "1";\n"a" = "2";', self.tmp)
        self.assertEqual([e.value for e in t.entries], ["1", "2"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertTrue(any(c.get("key") == "a" for c in t.conflicts))
        self.assertEqual(t.to_key_values(on_conflict="first")["a"], "1")

    def test_empty_file_parses_empty(self):
        t = _parse(b"/* chi comment */\n", self.tmp)
        self.assertEqual(t.entries, [])

    def test_corrupt_missing_semicolon(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'"a" = "1"', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_corrupt_unterminated_string(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'"a" = "1;', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_corrupt_missing_equals(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'"a" "1";', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_corrupt_unclosed_comment(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"/* khong dong\na", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_bad_encoding_raises(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'"a" = "\xff\xfe";', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestStringsBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_layout_and_comments(self):
        base = _read("a.strings")
        edited = LocTable([LocEntry("hello", "Chào bạn", None, "native", "ok",
                                    SourceRef(["a.strings"], "a.strings")),
                           LocEntry("emoji", "Hoan hô 🎊", None, "native", "ok",
                                    SourceRef(["a.strings"], "a.strings"))],
                          None, "apple_strings")
        new = self._build(edited, base)
        text = new.decode("utf-8")
        self.assertIn("/* Ghi chú đầu file */", text)       # comment giữ nguyên
        self.assertIn("// ghi chú dòng", text)
        self.assertIn('"hello" = "Chào bạn";', text)
        self.assertIn('"emoji" = "Hoan hô 🎊";', text)
        self.assertIn('"welcome" = "Chào mừng, %@! Bạn có %d tin nhắn mới.";', text)
        self.assertIn('"quote" = "Cô ấy nói \\"Xin chào\\"";', text)
        t = _parse(new, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["hello"].value, "Chào bạn")
        self.assertEqual(by_key["bye"].value, "Tạm biệt\nHẹn gặp lại")

    def test_build_keeps_utf16_bom(self):
        base = _read("b.strings")
        edited = LocTable([LocEntry("hello", "Chào bạn", None, "native", "ok",
                                    SourceRef(["b.strings"], "b.strings"))], None, "apple_strings")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xff\xfe"))
        t = _parse(new, self.tmp, "b.strings")
        self.assertEqual({e.key: e for e in t.entries}["hello"].value, "Chào bạn")
        self.assertEqual({e.key: e for e in t.entries}["lives"].value, "Bạn còn %d mạng")

    def test_build_escapes_new_value(self):
        base = b'"a" = "1";\n"b" = "2";\n'
        edited = LocTable([LocEntry("a", 'xuống\ndòng "trích"', None, "native", "ok",
                                    SourceRef(["a.strings"], "a.strings"))], None, "apple_strings")
        new = self._build(edited, base)
        text = new.decode("utf-8")
        self.assertIn('"a" = "xuống\\ndòng \\"trích\\"";', text)
        t = _parse(new, self.tmp)
        self.assertEqual({e.key: e for e in t.entries}["a"].value, 'xuống\ndòng "trích"')
        self.assertEqual({e.key: e for e in t.entries}["b"].value, "2")

    def test_build_duplicate_keeps_both_occurrences(self):
        base = b'"a" = "1";\n"a" = "2";\n'
        t = _parse(base, self.tmp)
        edited = LocTable([LocEntry("a", "mot", None, "native", "ok",
                                    SourceRef(["a.strings"], "a.strings"))], None, "apple_strings")
        new = self._build(edited, base)
        self.assertIn(b'"a" = "mot";', new)
        self.assertIn(b'"a" = "2";', new)  # lần xuất hiện thứ hai giữ nguyên

    def test_verify_accepts_good_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        base = _read("a.strings")
        t = _parse(base, self.tmp)
        new = plug.build(ctx, t, base)
        self.assertTrue(plug.verify(ctx, base, new)["ok"])
        bad = bytearray(new)
        bad[len(bad) // 2] ^= 0xFF
        self.assertFalse(plug.verify(ctx, base, bytes(bad))["ok"])

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestStringsConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(AppleStringsPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.strings": _read("a.strings"), "b.strings": _read("b.strings"),
                    "c.txt": b"hello"}, self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        # UTF-8 (a.strings) đứng trước UTF-16 để kiểm write dùng bản UTF-8
        self.assertEqual([c.path for c in cands], ["a.strings", "b.strings"])
        self.assertTrue(all(c.adapter_id == "apple_strings" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.strings": _read("a.strings")}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.strings", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 7)

    def test_sniff_scores(self):
        plug = _plug()
        self.assertEqual(plug._sniff(b'"a" = "1";', "x.strings"), 0.8)
        self.assertEqual(plug._sniff('"a" = "1";'.encode("utf-16"), "x.strings"), 0.8)
        self.assertEqual(plug._sniff(b"hello world", "x.strings"), 0.0)


if __name__ == "__main__":
    unittest.main()
