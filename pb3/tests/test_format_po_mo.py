"""Test plugin PO/MO (Claude 06): parse/build/verify/conformance."""
import os
import struct
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.po_mo.plugin import PoMoPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "po_mo")

SAMPLE = (b"# Dich game.\n"
          b'msgid ""\n'
          b'msgstr ""\n'
          b'"Project-Id-Version: 1\\n"\n'
          b"\n"
          b"#. Ghi chu trich xuat.\n"
          b"#: src/menu.py:12\n"
          b'msgid "hello"\n'
          b'msgstr "Xin chao"\n'
          b"\n"
          b"#, fuzzy\n"
          b'msgid "goodbye"\n'
          b'msgstr "Tam biet"\n'
          b"\n"
          b'msgctxt "menu"\n'
          b'msgid "new_game"\n'
          b'msgstr "Tro choi moi"\n'
          b"\n"
          b'msgid "apple"\n'
          b'msgid_plural "apples"\n'
          b'msgstr[0] "mot qua tao"\n'
          b'msgstr[1] "%d qua tao"\n'
          b"\n"
          b'msgid "long"\n'
          b'msgstr ""\n'
          b'"Day la mot chuoi rat dai duoc ngat thanh nhieu dong trong file PO goc "\n'
          b'"de kiem tra viec noi chuoi nhieu dong khi doc."\n'
          b"\n"
          b'#~ msgid "old"\n'
          b'#~ msgstr "cu"\n'
          b"\n"
          b'msgid "empty"\n'
          b'msgstr ""\n')


def _mo_bytes(entries):
    n = len(entries)
    orig = [e[0].encode("utf-8") for e in entries]
    trans = [e[1].encode("utf-8") for e in entries]
    o_off, t_off = 28, 28 + 8 * n
    pos = 28 + 16 * n
    o_table, t_table, blob = b"", b"", b""
    for s in orig:
        o_table += struct.pack("<2I", len(s), pos)
        blob += s + b"\x00"
        pos += len(s) + 1
    for s in trans:
        t_table += struct.pack("<2I", len(s), pos)
        blob += s + b"\x00"
        pos += len(s) + 1
    return struct.pack("<7I", 0x950412DE, 0, n, o_off, t_off, 0, 0) + o_table + t_table + blob


MO_SAMPLE = _mo_bytes([
    ("", "Project-Id-Version: 1\n"),
    ("hello", "Xin chào"),
    ("menu\x04new_game", "Trò chơi mới"),
    ("apple\x00apples", "một quả táo\x00%d quả táo"),
    ("empty", ""),
])


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "po_mo")


def _plug():
    return PoMoPlugin()


def _parse(data, tmp, path="a.po"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestPoParse(TmpCase):
    def test_sample_keys_values_status_notes(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["hello", "goodbye", "menu\x04new_game", "apple",
                          "apple [plural]", "apple[1]", "long", "empty"])
        self.assertEqual(by_key["hello"].value, "Xin chao")
        self.assertEqual(by_key["hello"].notes,
                         ["ghi-chú-dịch: Ghi chu trich xuat.", "tham-chiếu: src/menu.py:12"])
        self.assertEqual(by_key["goodbye"].notes, ["fuzzy"])
        self.assertEqual(by_key["empty"].status, "empty")
        self.assertEqual(by_key["long"].value,
                         "Day la mot chuoi rat dai duoc ngat thanh nhieu dong trong file PO goc "
                         "de kiem tra viec noi chuoi nhieu dong khi doc.")
        self.assertEqual(t.stats["obsolete"], 1)
        self.assertEqual(t.stats["fuzzy"], 1)
        self.assertEqual(t.stats["plural"], 1)
        self.assertEqual(t.stats["header"], 1)
        self.assertTrue(any("obsolete" in w.get("msg", "") for w in t.warnings))
        self.assertTrue(any(w.get("key") == "goodbye" for w in t.warnings))
        self.assertEqual(t.schema_hint.get("format"), "po")

    def test_plural_keys(self):
        t = _parse(SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["apple"].value, "mot qua tao")
        self.assertEqual(by_key["apple [plural]"].value, "apples")
        self.assertEqual(by_key["apple[1]"].value, "%d qua tao")

    def test_escapes(self):
        data = b'msgid "esc"\nmsgstr "a\\nb\\t\\"q\\" \\\\ end"\n'
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, 'a\nb\t"q" \\ end')

    def test_unicode_bom_and_utf16(self):
        t = _parse('msgid "chao"\nmsgstr "Xin chào 🌟"\n'.encode("utf-8"), self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")
        bom = b"\xef\xbb\xbf" + b'msgid "chao"\nmsgstr "Xin chao"\n'
        t = _parse(bom, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chao")
        self.assertEqual(t.entries[0].source.encoding, "utf-8")
        utf16 = 'msgid "chao"\nmsgstr "Xin chào"\n'.encode("utf-16")
        t = _parse(utf16, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào")
        self.assertEqual(t.entries[0].source.encoding, "utf-16-le")

    def test_unterminated_string_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'msgid "hello\nmsgstr "x"\n', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_junk_line_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"day khong phai po\n", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_entry_without_msgid_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'msgstr "x"\n', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_mo_parse(self):
        t = _parse(MO_SAMPLE, self.tmp, path="a.mo")
        by_key = {e.key: e for e in t.entries}
        self.assertEqual([e.key for e in t.entries],
                         ["hello", "menu\x04new_game", "apple",
                          "apple [plural]", "apple[1]", "empty"])
        self.assertEqual(by_key["hello"].value, "Xin chào")
        self.assertEqual(by_key["apple"].value, "một quả táo")
        self.assertEqual(by_key["apple [plural]"].value, "apples")
        self.assertEqual(by_key["apple[1]"].value, "%d quả táo")
        self.assertEqual(t.schema_hint.get("format"), "mo")
        self.assertEqual(t.stats["header"], 1)

    def test_mo_truncated_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(MO_SAMPLE[:20], self.tmp, path="a.mo")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_mo_bad_table_corrupt(self):
        bad = bytearray(MO_SAMPLE)
        bad[16:20] = struct.pack("<I", 999999)  # O vượt quá file
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(bad), self.tmp, path="a.mo")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_binary_garbage_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"\x00\x01\x02\xff\xfe\x80\x81", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestPoBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_comments_flags_order(self):
        t = _parse(SAMPLE, self.tmp)
        edited = LocTable([
            LocEntry("hello", "Chào bạn", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
            LocEntry("apple", "một quả táo MỚI", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
            LocEntry("apple [plural]", "apples MỚI", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
            LocEntry("apple[1]", "%d quả táo MỚI", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
            LocEntry("long", "X" * 200, None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
        ], None, "po_mo")
        new = self._build(edited, SAMPLE)
        text = new.decode("utf-8")
        self.assertIn('#. Ghi chu trich xuat.', text)
        self.assertIn('#, fuzzy', text)
        self.assertIn('#~ msgid "old"', text)
        self.assertIn('msgstr "Chào bạn"', text)
        self.assertIn('msgid_plural "apples MỚI"', text)
        self.assertIn('msgstr[1] "%d quả táo MỚI"', text)
        self.assertIn('msgstr ""\n"XXXX', text)  # giá trị dài được ngắt dòng lại
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["hello"].value, "Chào bạn")
        self.assertEqual(by_key["long"].value, "X" * 200)
        self.assertEqual(by_key["apple[1]"].value, "%d quả táo MỚI")
        self.assertEqual([e.key for e in back.entries],
                         [e.key for e in t.entries])

    def test_build_inserts_missing_msgstr(self):
        base = b'msgid "new"\n'
        edited = LocTable([LocEntry("new", "Mới", None, "native", "ok",
                                    SourceRef(["a.po"], "a.po"))], None, "po_mo")
        new = self._build(edited, base)
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Mới")

    def test_build_escapes_round_trip(self):
        base = b'msgid "esc"\nmsgstr ""\n'
        edited = LocTable([LocEntry("esc", 'dòng một\ndòng "hai" \\ hết',
                                    None, "native", "ok",
                                    SourceRef(["a.po"], "a.po"))], None, "po_mo")
        new = self._build(edited, base)
        self.assertEqual(_parse(new, self.tmp).entries[0].value,
                         'dòng một\ndòng "hai" \\ hết')

    def test_build_keeps_utf16_bom(self):
        base = 'msgid "chao"\nmsgstr "Xin chào"\n'.encode("utf-16")
        edited = LocTable([LocEntry("chao", "Chào bạn", None, "native", "ok",
                                    SourceRef(["a.po"], "a.po"))], None, "po_mo")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xff\xfe"))
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Chào bạn")

    def test_build_unknown_key_warns_not_fails(self):
        edited = LocTable([
            LocEntry("hello", "Chào bạn", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
            LocEntry("khong_co", "x", None, "native", "ok",
                     SourceRef(["a.po"], "a.po")),
        ], None, "po_mo")
        new = self._build(edited, SAMPLE)
        self.assertEqual(_parse(new, self.tmp).entries[0].value, "Chào bạn")
        self.assertTrue(any(w.get("key") == "khong_co" for w in edited.warnings))

    def test_mo_build_raises_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        table = _parse(MO_SAMPLE, self.tmp, path="a.mo")
        with self.assertRaises(PB3Error) as cm:
            plug.build(ctx, table, MO_SAMPLE)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

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


class TestPoConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(PoMoPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates_writable_first(self):
        plug = _plug()
        ctx = _ctx({"a.po": SAMPLE, "b.mo": MO_SAMPLE, "c.txt": b"hello"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.po", "b.mo"])
        self.assertTrue(all(c.adapter_id == "po_mo" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "content" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.po": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.po", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 8)


if __name__ == "__main__":
    unittest.main()
