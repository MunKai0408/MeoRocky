"""Test plugin SQLite localization (Claude 06): chỉ đọc."""
import os
import sqlite3
import unittest

from core.errors import PB3Error
from core.limits import Limits
from plugins.sqlite_loc.plugin import SqliteLocPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocTable, SourceRef
from sdk.vfs import MemoryVFS
from tests.helpers import TmpCase
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "sqlite_loc")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "sqlite_loc")


def _plug():
    return SqliteLocPlugin()


def _parse(data, tmp, path="a.sqlite"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


def _db_bytes(tmp, stmts):
    """DB bytes dựng trong bộ nhớ (test): tạo file tạm, chạy lệnh, đọc bytes."""
    path = str(tmp / "t.sqlite")
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    try:
        for s in stmts:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()
    with open(path, "rb") as fh:
        return fh.read()


class TestSqliteParse(TmpCase):
    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_fixture_localization_table_and_skipped_table(self):
        t = _parse(_read("a.sqlite"), self.tmp)
        got = [(e.key, e.locale, e.value) for e in t.entries]
        self.assertEqual(got, [("hello", "en", "Hello"), ("hello", "vi", "Xin chào"),
                               ("bye", "en", "Goodbye"), ("bye", "vi", "Tạm biệt"),
                               ("welcome", "en", "Welcome, %@!"),
                               ("welcome", "vi", "Chào mừng, %@!"),
                               ("emoji", "en", "Congratulations 🎉"),
                               ("emoji", "vi", "Chúc mừng 🎉")])
        self.assertEqual(t.stats["tables"], 1)
        self.assertEqual(t.stats["tables_total"], 2)
        self.assertEqual(t.stats["rows"], 4)
        self.assertEqual(t.schema_hint["tables"], ["strings"])
        self.assertEqual(t.schema_hint["key_col"], {"strings": "key"})
        self.assertEqual(t.schema_hint["value_cols"], {"strings": ["en", "vi"]})
        self.assertTrue(any("scores" in (w.get("msg", "") or "") for w in t.warnings))
        self.assertEqual(t.adapter_id, "sqlite_loc")
        self.assertTrue(all(isinstance(e.source, SourceRef) for e in t.entries))

    def test_multi_table_prefixes_keys(self):
        data = _db_bytes(self.tmp, [
            "CREATE TABLE ui (key TEXT PRIMARY KEY, en TEXT)",
            "INSERT INTO ui VALUES ('ok', 'OK')",
            "CREATE TABLE menu (key TEXT PRIMARY KEY, en TEXT)",
            "INSERT INTO menu VALUES ('ok', 'Menu OK')",
        ])
        t = _parse(data, self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("ui.ok", "OK"), ("menu.ok", "Menu OK")])

    def test_name_hint_table_qualifies(self):
        # Bảng tên gợi ý localization, cột key không mang tên quen thuộc.
        data = _db_bytes(self.tmp, [
            "CREATE TABLE translations (txt TEXT, en TEXT)",
            "INSERT INTO translations VALUES ('chao', 'Hello')",
        ])
        t = _parse(data, self.tmp)
        self.assertEqual([(e.key, e.locale, e.value) for e in t.entries],
                         [("chao", "en", "Hello")])

    def test_value_col_not_locale_gets_no_locale(self):
        data = _db_bytes(self.tmp, [
            "CREATE TABLE strings (key TEXT PRIMARY KEY, value TEXT)",
            "INSERT INTO strings VALUES ('a', 'mot')",
        ])
        t = _parse(data, self.tmp)
        self.assertEqual([(e.key, e.locale, e.value) for e in t.entries],
                         [("a", None, "mot")])

    def test_null_value_becomes_empty(self):
        data = _db_bytes(self.tmp, [
            "CREATE TABLE strings (key TEXT PRIMARY KEY, en TEXT, vi TEXT)",
            "INSERT INTO strings VALUES ('a', 'one', NULL)",
        ])
        t = _parse(data, self.tmp)
        by_key = {(e.key, e.locale): e for e in t.entries}
        self.assertEqual(by_key[("a", "vi")].value, "")
        self.assertEqual(by_key[("a", "vi")].status, "empty")

    def test_empty_db_no_tables(self):
        data = _db_bytes(self.tmp, [])
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries, [])
        self.assertEqual(t.stats["tables"], 0)

    def test_corrupt_not_sqlite(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"khong phai sqlite", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_corrupt_truncated(self):
        data = _read("a.sqlite")[:100]  # còn magic, mất phần lớn nội dung
        with self.assertRaises(PB3Error) as cm:
            _parse(data, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_corrupt_bad_magic(self):
        data = bytearray(_read("a.sqlite"))
        data[0] = ord("X")
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_parse_bytes_direct(self):
        # _parse_bytes dùng trực tiếp (không qua guard) vẫn parse được.
        plug = _plug()
        res = plug._parse_bytes(_read("a.sqlite"), "a.sqlite", None)
        self.assertEqual(len(res["entries"]), 8)


class TestSqliteReadOnly(TmpCase):
    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_build_raises_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        with self.assertRaises(PB3Error) as cm:
            plug.build(ctx, LocTable([], None, "sqlite_loc"), _read("a.sqlite"))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_verify_raises_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        with self.assertRaises(PB3Error) as cm:
            plug.verify(ctx, _read("a.sqlite"), _read("a.sqlite"))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        res = plug.selftest(ctx)
        self.assertTrue(res["ok"], res)

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_parse_does_not_modify_db(self):
        data = _read("a.sqlite")
        t = _parse(data, self.tmp)
        self.assertEqual(len(t.entries), 8)
        # DB gốc không bị thay đổi: parse lại bytes gốc cho cùng kết quả.
        t2 = _parse(data, self.tmp)
        self.assertEqual([(e.key, e.locale, e.value) for e in t2.entries],
                         [(e.key, e.locale, e.value) for e in t.entries])


class TestSqliteConformance(TmpCase):
    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_conformance_with_fixture(self):
        files = {"a.sqlite": _read("a.sqlite")}
        rep = run_conformance(SqliteLocPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("readonly").status, "PASS")
        self.assertEqual(rep.get("write").status, "SKIP")
        self.assertEqual(rep.get("selftest").status, "PASS")

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"a.sqlite": _read("a.sqlite"), "b.txt": b"hello"},
                   self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["a.sqlite"])
        self.assertTrue(all(c.adapter_id == "sqlite_loc" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    @pytest.mark.needs_fixture("sqlite_loc/a.sqlite")
    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"a.sqlite": _read("a.sqlite")}, self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("a.sqlite", plug.id, 0.9, []))
        self.assertTrue(p["ok"])
        self.assertEqual(p["entries"], 8)

    def test_sniff_scores(self):
        plug = _plug()
        self.assertEqual(plug._sniff(b"SQLite format 3\x00abc", "x.sqlite"), 0.9)
        self.assertEqual(plug._sniff(b"hello", "x.sqlite"), 0.0)


if __name__ == "__main__":
    unittest.main()
