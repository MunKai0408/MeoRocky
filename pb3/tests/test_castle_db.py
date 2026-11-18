"""Test plugin CastleDB (.cdb) — port parseCdb từ IPA-LOCALIZATION
js/modules/text_extract.js (chỉ cột kind="localizable", kể cả sheet con
"cha@cột"; key Sheet[Id].col).

Fixture mô phỏng cấu trúc data.cdb thật của Dead Cells (sheet + cột
localizable + sheet con, text nguồn tiếng Pháp) — [FIXTURE]. Số 3192 dòng
của file thật là bằng chứng phía app JS, chưa chạy lại được trên file
thật (Drive chặn quota) nên test này chỉ khóa hành vi port.
"""
import ast
import json
import os
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.castle_db.plugin import CastleDbPlugin, parse_cdb_bytes
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase, imports_of, read_src

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_PATH = os.path.join(ROOT, "plugins", "castle_db", "plugin.py")


def _cdb_bytes():
    doc = {
        "sheets": [
            {"name": "items",
             "columns": [{"name": "id"},
                         {"name": "name", "kind": "localizable"},
                         {"name": "desc", "kind": "localizable"},
                         {"name": "icon"},
                         {"name": "tags", "kind": "subsheet"}],
             "lines": [
                 {"id": "Sword", "name": "Épée maudite",
                  "desc": "Une lame qui chante.", "icon": "sword.png",
                  "tags": [{"id": "t1", "label": "Étiquette"},
                           {"id": "t2", "label": "Autre"}]},
                 {"id": "Shield", "name": "Bouclier", "desc": "",
                  "icon": "shield.png"},
                 {"name": "Sans id", "desc": "Pas d'identifiant."},
             ]},
            {"name": "items@tags",
             "columns": [{"name": "id"},
                         {"name": "label", "kind": "localizable"}],
             "lines": []},
        ]
    }
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None,
                         None, "castle_db")


def _plug():
    return CastleDbPlugin()


def _parse(data, tmp, path="data.cdb"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


def _build(table, base, tmp):
    plug = _plug()
    ctx = _ctx({"data.cdb": base}, tmp).for_plugin(plug.manifest)
    return plug.build(ctx, table, base)


class TestCastleDbParse(TmpCase):
    def test_localizable_columns_only(self):
        t = _parse(_cdb_bytes(), self.tmp)
        self.assertEqual(t.adapter_id, "castle_db")
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key, {
            "items[Sword].name": "Épée maudite",
            "items[Sword].desc": "Une lame qui chante.",
            "items[Sword].tags[t1].label": "Étiquette",
            "items[Sword].tags[t2].label": "Autre",
            "items[Shield].name": "Bouclier",
            "items[2].name": "Sans id",
            "items[2].desc": "Pas d'identifiant.",
        })
        # Cột không localizable (id, icon) và ô rỗng ("") không thành key.
        self.assertNotIn("items[Sword].icon", by_key)
        self.assertNotIn("items[Shield].desc", by_key)
        # Dòng không có id -> dùng chỉ số dòng.
        self.assertIn("items[2].desc", by_key)
        self.assertEqual(t.stats["sheets"], 1)
        self.assertEqual(t.stats["keys"], 7)

    def test_sniff_and_score(self):
        plug = _plug()
        self.assertEqual(plug._sniff(_cdb_bytes(), "data.cdb"), 0.9)
        self.assertEqual(plug._sniff(b'{"a": 1}', "x.json"), 0.0)
        self.assertEqual(plug._sniff(b"not json", "x.cdb"), 0.0)
        self.assertGreater(plug._score(_cdb_bytes(), "data.cdb"), 0.5)
        # Không có ý kiến nội dung -> _score lớp nền trả 0.5 (chỉ theo đuôi);
        # JSON thường vẫn bị loại bởi lọc đuôi ở list_candidates.
        self.assertEqual(plug._score(b'{"a": 1}', "x.json"), 0.5)

    def test_non_cdb_json_rejected(self):
        with self.assertRaises(PB3Error) as cm:
            parse_cdb_bytes(b'{"a": 1}', "x.json")
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")
        with self.assertRaises(PB3Error):
            _parse(b'{"a": 1}', self.tmp, "x.cdb")
        with self.assertRaises(PB3Error):
            parse_cdb_bytes(b"{json hong", "x.cdb")

    def test_duplicate_key_conflict(self):
        doc = {"sheets": [{"name": "s",
                           "columns": [{"name": "id"},
                                       {"name": "t", "kind": "localizable"}],
                           "lines": [{"id": "X", "t": "một"},
                                     {"id": "X", "t": "hai"}]}]}
        t = _parse(json.dumps(doc).encode(), self.tmp)
        # make_entries giữ cả 2 bản ghi, báo conflict, bản ghi đầu thắng khi ghi.
        self.assertEqual(len(t.entries), 2)
        self.assertEqual(t.entries[0].value, "một")
        self.assertTrue(t.conflicts)


class TestCastleDbBuild(TmpCase):
    def _edited(self, table, prefix="VI:"):
        return LocTable(
            [LocEntry(e.key, prefix + e.value, None, "native", "ok",
                      SourceRef(["data.cdb"], "data.cdb"))
             for e in table.entries], None, "castle_db")

    def test_round_trip_keeps_structure(self):
        base = _cdb_bytes()
        t = _parse(base, self.tmp)
        new = _build(self._edited(t), base, self.tmp)
        t2 = _parse(new, self.tmp)
        by_key = {e.key: e.value for e in t2.entries}
        self.assertEqual(by_key["items[Sword].name"], "VI:Épée maudite")
        self.assertEqual(by_key["items[Sword].tags[t1].label"],
                         "VI:Étiquette")
        self.assertEqual(by_key["items[Shield].name"], "VI:Bouclier")
        # Cấu trúc giữ nguyên: id/icon không đổi, thứ tự sheet/dòng giữ.
        doc = json.loads(new.decode("utf-8"))
        self.assertEqual(doc["sheets"][0]["lines"][0]["id"], "Sword")
        self.assertEqual(doc["sheets"][0]["lines"][0]["icon"], "sword.png")
        self.assertEqual(doc["sheets"][0]["lines"][1]["id"], "Shield")
        self.assertEqual([ln.get("id") for ln in
                          doc["sheets"][0]["lines"]],
                         ["Sword", "Shield", None])

    def test_verify_counts_changed(self):
        base = _cdb_bytes()
        t = _parse(base, self.tmp)
        new = _build(self._edited(t), base, self.tmp)
        plug = _plug()
        ctx = _ctx({"data.cdb": base}, self.tmp).for_plugin(plug.manifest)
        rep = plug.verify(ctx, base, new)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["changed"], 7)

    def test_selftest(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        self.assertTrue(plug.selftest(ctx)["ok"])


class TestCastleDbConformance(TmpCase):
    def test_conformance_with_fixture(self):
        rep = run_conformance(CastleDbPlugin,
                              files={"data.cdb": _cdb_bytes()})
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("write").status, "PASS")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"data.cdb": _cdb_bytes(),
                    "plain.json": b'{"a": 1}'}, self.tmp).for_plugin(
                        plug.manifest)
        cands = plug.list_candidates(ctx)
        paths = [c.path for c in cands]
        self.assertIn("data.cdb", paths)
        self.assertNotIn("plain.json", paths)


class TestCastleDbStdlibPy39(unittest.TestCase):
    def test_stdlib_only_and_py39_syntax(self):
        ast.parse(read_src(PLUGIN_PATH), feature_version=(3, 9))
        allowed = {"__future__", "json", "re", "typing", "core", "sdk"}
        for name, _line in imports_of(PLUGIN_PATH):
            self.assertIn(name.split(".")[0], allowed, name)


if __name__ == "__main__":
    unittest.main()
