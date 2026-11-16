"""Test plugin YAML/INI (Claude 06): parse/build/verify/conformance."""
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.yaml_ini.plugin import YamlIniPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "yaml_ini")

YAML_SAMPLE = (b"# Chuoi YAML mau\nmenu:\n  new_game: Tro choi moi\n  settings: Cai dat\n"
               b"count: 3\npremium: true\nnote: |\n  dong mot\n  dong hai\n"
               b"tags: [meo, tro choi]\nflow: {k: v}\nref: &chao Xin chao\ncopy: *chao\n")
INI_SAMPLE = b"; Cau hinh INI mau\n[menu]\nnew_game = Tro choi moi\nsettings = Cai dat\n\n[game]\ncount = 3\nname = 9 Kings\n"


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "yaml_ini")


def _plug():
    return YamlIniPlugin()


def _parse(data, tmp, path="a.yaml"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestYamlParse(TmpCase):
    def test_nested_types_flow_anchor_block_scalar(self):
        t = _parse(YAML_SAMPLE, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(list(by_key), ["menu.new_game", "menu.settings", "count", "premium",
                                        "note", "tags[0]", "tags[1]", "flow.k", "ref", "copy"])
        self.assertEqual(by_key["menu.new_game"].value, "Tro choi moi")
        self.assertEqual(by_key["count"].value, "3")
        self.assertIn("json:number", by_key["count"].notes)
        self.assertIn("json:boolean", by_key["premium"].notes)
        self.assertEqual(by_key["note"].value, "dong mot\ndong hai\n")
        self.assertEqual(by_key["tags[1]"].value, "tro choi")
        self.assertEqual(by_key["flow.k"].value, "v")
        self.assertEqual(by_key["ref"].value, "Xin chao")
        self.assertEqual(by_key["copy"].value, "Xin chao")

    def test_list_of_maps(self):
        data = b"items:\n  - name: mot\n    value: 1\n  - name: hai\n    value: 2\n"
        t = _parse(data, self.tmp)
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key, {"items[0].name": "mot", "items[0].value": "1",
                                  "items[1].name": "hai", "items[1].value": "2"})

    def test_folded_and_chomping(self):
        data = b"a: >\n  dong mot\n  dong hai\nb: |-\n  khong xuong dong\nc: |+\n  giu\n\n"
        t = _parse(data, self.tmp)
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key["a"], "dong mot dong hai\n")
        self.assertEqual(by_key["b"], "khong xuong dong")
        self.assertEqual(by_key["c"], "giu\n\n")

    def test_quoted_scalars(self):
        data = b'a: "nhay kep \\n xuong dong"\nb: \'nhay don \'\'trich\'\'\'\nc: "123"\n'
        t = _parse(data, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["a"].value, "nhay kep \n xuong dong")
        self.assertEqual(by_key["b"].value, "nhay don 'trich'")
        self.assertEqual(by_key["c"].value, "123")
        self.assertNotIn("json:number", by_key["c"].notes)

    def test_scalar_types(self):
        data = b"i: 42\nf: 3.5\nn: null\nt: ~\nb: false\ns: hello\n"
        t = _parse(data, self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertIn("json:number", by_key["i"].notes)
        self.assertIn("json:number", by_key["f"].notes)
        self.assertIn("json:null", by_key["n"].notes)
        self.assertIn("json:null", by_key["t"].notes)
        self.assertIn("json:boolean", by_key["b"].notes)
        self.assertEqual(by_key["s"].notes, [])

    def test_duplicate_key_conflict_keep_first(self):
        t = _parse(b"a: 1\na: 2\n", self.tmp)
        self.assertEqual([e.value for e in t.entries], ["1", "2"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertTrue(any(c.get("key") == "a" for c in t.conflicts))

    def test_unicode_and_comment(self):
        t = _parse("# ghi chu\na: Xin chào 🌟 # cuoi dong\n".encode("utf-8"), self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")

    def test_tab_indent_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"a:\n\tb: 1\n", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_multi_document_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"a: 1\n---\nb: 2\n", self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_leading_document_marker_ok(self):
        t = _parse(b"---\na: 1\n", self.tmp)
        self.assertEqual(t.entries[0].value, "1")

    def test_anchor_alias_collection(self):
        data = b"base: &b\n  x: 1\n  y: 2\ncopy: *b\n"
        t = _parse(data, self.tmp)
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key["base.x"], "1")
        self.assertEqual(by_key["copy.x"], "1")
        self.assertEqual(by_key["copy.y"], "2")

    def test_unknown_alias_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"a: *khong_co\n", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestIniParse(TmpCase):
    def test_sections_and_order(self):
        t = _parse(INI_SAMPLE, self.tmp, "a.ini")
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("menu.new_game", "Tro choi moi"), ("menu.settings", "Cai dat"),
                          ("game.count", "3"), ("game.name", "9 Kings")])
        self.assertEqual(t.stats["sections"], 2)

    def test_sectionless(self):
        t = _parse(b"hello=Xin chao\nbye=Tam biet\n", self.tmp, "a.ini")
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("hello", "Xin chao"), ("bye", "Tam biet")])

    def test_case_preserved(self):
        t = _parse(b"[S]\nName = Mot\nname = Hai\n", self.tmp, "a.ini")
        self.assertEqual([e.key for e in t.entries], ["S.Name", "S.name"])

    def test_duplicate_key_in_section_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"[a]\nx=1\nx=2\n", self.tmp, "a.ini")
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_colon_separator(self):
        t = _parse(b"[a]\nx: 1\n", self.tmp, "a.ini")
        self.assertEqual(t.entries[0].value, "1")


class TestYamlIniBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_yaml_round_trip_keeps_types_and_rest(self):
        edited = LocTable([LocEntry("menu.settings", "Tuy chinh", None, "native", "ok",
                                    SourceRef(["a.yaml"], "a.yaml")),
                           LocEntry("count", "5", None, "native", "ok",
                                    SourceRef(["a.yaml"], "a.yaml"), ["json:number"]),
                           LocEntry("tags[1]", "tro choi moi", None, "native", "ok",
                                    SourceRef(["a.yaml"], "a.yaml"))],
                          None, "yaml_ini")
        new = self._build(edited, YAML_SAMPLE)
        back = _parse(new, self.tmp)
        by_key = {e.key: e for e in back.entries}
        self.assertEqual(by_key["menu.settings"].value, "Tuy chinh")
        self.assertEqual(by_key["count"].value, "5")
        self.assertIn("json:number", by_key["count"].notes)
        self.assertEqual(by_key["tags[1]"].value, "tro choi moi")
        self.assertEqual(by_key["menu.new_game"].value, "Tro choi moi")
        self.assertEqual(by_key["note"].value, "dong mot\ndong hai\n")
        self.assertEqual(by_key["copy"].value, "Xin chao")

    def test_yaml_string_that_looks_like_number_stays_string(self):
        base = b'a: "123"\n'
        edited = LocTable([LocEntry("a", "456", None, "native", "ok",
                                    SourceRef(["a.yaml"], "a.yaml"))], None, "yaml_ini")
        new = self._build(edited, base)
        back = _parse(new, self.tmp)
        self.assertEqual(back.entries[0].value, "456")
        self.assertNotIn("json:number", back.entries[0].notes)

    def test_ini_round_trip(self):
        edited = LocTable([LocEntry("game.count", "9", None, "native", "ok",
                                    SourceRef(["a.ini"], "a.ini"))], None, "yaml_ini")
        new = self._build(edited, INI_SAMPLE)
        back = _parse(new, self.tmp, "a.ini")
        by_key = {e.key: e.value for e in back.entries}
        self.assertEqual(by_key["game.count"], "9")
        self.assertEqual(by_key["menu.new_game"], "Tro choi moi")
        self.assertEqual(by_key["game.name"], "9 Kings")
        self.assertNotIn("Cau hinh", new.decode("utf-8"))  # comment mất khi ghi ngược

    def test_ini_sectionless_round_trip(self):
        base = b"hello=Xin chao\nbye=Tam biet\n"
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.ini"], "a.ini"))], None, "yaml_ini")
        new = self._build(edited, base)
        self.assertEqual(new, b"hello=Chao ban\nbye=Tam biet\n")
        self.assertNotIn(b"[", new)

    def test_verify_accepts_good_rejects_tampered(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        for base, path in ((YAML_SAMPLE, "a.yaml"), (INI_SAMPLE, "a.ini")):
            t = _parse(base, self.tmp, path)
            new = plug.build(ctx, t, base)
            self.assertTrue(plug.verify(ctx, base, new)["ok"], path)
            bad = bytearray(new)
            bad[len(bad) // 2] ^= 0xFF
            self.assertFalse(plug.verify(ctx, base, bytes(bad))["ok"], path)

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestYamlIniConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(YamlIniPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.yaml": YAML_SAMPLE, "b.ini": INI_SAMPLE, "c.txt": b"hello"},
                   self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["b.ini", "a.yaml"])
        self.assertTrue(all(c.adapter_id == "yaml_ini" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.yaml": YAML_SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.yaml", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 10)


if __name__ == "__main__":
    unittest.main()
