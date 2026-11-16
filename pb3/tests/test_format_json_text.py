"""Test plugin JSON (Claude 06): parse/build/verify/conformance."""
import json
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.json_text.plugin import JsonPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "json_text")

SAMPLE = b'{"empty":"","hello":"Xin chao","lives":3,"menu":{"new_game":"Tro choi moi","settings":"Cai dat"},"premium":true,"tips":["Meo mot","Meo hai"]}\n'


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "json_text")


def _plug():
    return JsonPlugin()


def _parse(data, tmp, path="a.json"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestJsonParse(TmpCase):
    def test_object_nested_types_and_order(self):
        t = _parse(SAMPLE, self.tmp)
        keys = [e.key for e in t.entries]
        self.assertEqual(keys, ["empty", "hello", "lives", "menu.new_game", "menu.settings",
                                "premium", "tips[0]", "tips[1]"])
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["hello"].value, "Xin chao")
        self.assertEqual(by_key["empty"].status, "empty")
        self.assertEqual(by_key["lives"].value, "3")
        self.assertIn("json:number", by_key["lives"].notes)
        self.assertIn("json:boolean", by_key["premium"].notes)
        self.assertEqual(t.adapter_id, "json_text")
        self.assertTrue(all(isinstance(e.source, SourceRef) for e in t.entries))

    def test_list_of_objects(self):
        t = _parse(b'[{"key": "A", "value": "1"}, {"id": "B", "text": "2"}]', self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries], [("A", "1"), ("B", "2")])

    def test_list_of_strings_gets_stable_row_keys(self):
        t = _parse(b'["mot", "hai"]', self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("row_000001", "mot"), ("row_000002", "hai")])

    def test_duplicate_keys_keep_first_and_conflict(self):
        t = _parse(b'{"a": "1", "a": "2"}', self.tmp)
        self.assertEqual([e.value for e in t.entries], ["1", "2"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertTrue(any(c.get("key") == "a" for c in t.conflicts))
        self.assertEqual(t.to_key_values(on_conflict="first")["a"], "1")

    def test_unicode_emoji_and_vietnamese(self):
        t = _parse('{"chao": "Xin chào 🌟"}'.encode("utf-8"), self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào 🌟")

    def test_utf16_bom(self):
        data = '{"chao": "Xin chào"}'.encode("utf-16")
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Xin chào")
        self.assertEqual(t.entries[0].source.encoding, "utf-16-le")

    def test_corrupt_raises(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'{"a": ', self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_top_level_scalar_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'"hello"', self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_list_without_keys_unsupported(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b'[{"x": 1}]', self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")


class TestJsonBuild(TmpCase):
    def _build(self, table, base):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        return plug.build(ctx, table, base)

    def test_round_trip_keeps_order_types_and_indent(self):
        t = _parse(SAMPLE, self.tmp)
        edited = LocTable([LocEntry("hello", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json")),
                           LocEntry("lives", "5", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json"), ["json:number"]),
                           LocEntry("menu.settings", "Tuy chinh", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json"))],
                          None, "json_text")
        new = self._build(edited, SAMPLE)
        back = json.loads(new.decode("utf-8"))
        self.assertEqual(back["hello"], "Chao ban")
        self.assertEqual(back["lives"], 5)                       # vẫn là số, không thành chuỗi
        self.assertIsInstance(back["lives"], int)
        self.assertEqual(back["menu"]["settings"], "Tuy chinh")
        self.assertEqual(list(back.keys()), ["empty", "hello", "lives", "menu", "premium", "tips"])
        self.assertEqual(back["tips"], ["Meo mot", "Meo hai"])   # không sửa thì giữ nguyên

    def test_build_list_of_objects(self):
        base = b'[{"key": "A", "value": "1"}, {"key": "B", "value": "2"}]'
        t = _parse(base, self.tmp)
        edited = LocTable([LocEntry("B", "hai", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json"))], None, "json_text")
        new = self._build(edited, base)
        self.assertEqual(json.loads(new.decode("utf-8"))[1]["value"], "hai")

    def test_build_keeps_utf16_bom(self):
        base = '{"chao": "Xin chào"}'.encode("utf-16")
        t = _parse(base, self.tmp)
        edited = LocTable([LocEntry("chao", "Chao ban", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json"))], None, "json_text")
        new = self._build(edited, base)
        self.assertTrue(new.startswith(b"\xff\xfe"))
        self.assertEqual(json.loads(new.decode("utf-16"))["chao"], "Chao ban")

    def test_build_unknown_key_warns_not_fails(self):
        t = _parse(b'{"a": "1"}', self.tmp)
        edited = LocTable([LocEntry("a", "9", None, "native", "ok", SourceRef(["a.json"], "a.json")),
                           LocEntry("khong_co", "x", None, "native", "ok",
                                    SourceRef(["a.json"], "a.json"))], None, "json_text")
        new = self._build(edited, b'{"a": "1"}')
        self.assertEqual(json.loads(new.decode("utf-8")), {"a": "9"})
        self.assertTrue(any(w.get("key") == "khong_co" for w in edited.warnings))

    def test_build_duplicate_key_warns_and_keeps_first(self):
        base = b'{"a": "1", "a": "2"}'
        t = _parse(base, self.tmp)
        new = self._build(t, base)
        self.assertEqual(json.loads(new.decode("utf-8")), {"a": "1"})
        self.assertTrue(any(w.get("key") == "a" and "trùng" in w.get("msg", "")
                            for w in t.warnings))

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


class TestJsonConformance(TmpCase):
    def test_conformance_with_fixture(self):
        files = {}
        for n in sorted(os.listdir(FIX)):
            with open(os.path.join(FIX, n), "rb") as f:
                files[n] = f.read()
        rep = run_conformance(JsonPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.json": SAMPLE, "b.txt": b"hello", "c.json": b"not json {"}, self.tmp)
        ctx = ctx.for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.json", "c.json"])
        self.assertTrue(all(c.adapter_id == "json_text" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.json": SAMPLE}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.json", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 8)


if __name__ == "__main__":
    unittest.main()
