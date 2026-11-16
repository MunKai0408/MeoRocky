"""Test Game Knowledge Base + fingerprint + inventory cache (Claude 09,
đặc tả §8, §14.5).

Fixture xác định (random.Random seed, không os.urandom), không mạng.
"""
import hashlib
import json
import os
import plistlib
import random
import sqlite3
import time
import unittest
import zipfile
from pathlib import Path

from core.errors import PB3Error
from sdk.kb import (InventoryCache, KB, SCHEMA_VERSION, fingerprint_game,
                    inventory_key)
from tests.helpers import TmpCase

_RNG = random.Random(20261116)


def _det_bytes(n, seed):
    return random.Random(seed).randbytes(n) if hasattr(random.Random(0), "randbytes") \
        else bytes(random.Random(seed).getrandbits(8) for _ in range(n))


def _plist_bytes(bundle_id="com.example.nineking", version="1.0.5"):
    return plistlib.dumps({
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleName": "9 Kings",
    })


def _build_ipa(path, *, plist=None, extra=None, text_content=None):
    """IPA giả (ZIP) xác định: Info.plist + vài entry đặc trưng + entry khác."""
    entries = {
        "Payload/Game.app/Info.plist": plist if plist is not None else _plist_bytes(),
        "Payload/Game.app/config.json": b'{"locale": "en", "v": 1}',
        "Payload/Game.app/manifest.json": b'{"files": ["config.json"]}',
        "Payload/Game.app/version.txt": b"1.0.5\n",
        "Payload/Game.app/data1.bin": _det_bytes(100, 11),
        "Payload/Game.app/data2.bin": _det_bytes(100, 22),
        "Payload/Game.app/data3.bin": _det_bytes(100, 33),
    }
    if text_content is not None:
        entries["Payload/Game.app/texts/en.json"] = text_content
    if extra:
        entries.update(extra)
    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(entries):
            zf.writestr(name, entries[name])
    return path


def _build_plain(path, content=b"hello pb3 kb"):
    path.write_bytes(content)
    return path


class TestFingerprint(TmpCase):
    def test_stable_across_copy_same_fingerprint_different_path_hash(self):
        src = _build_ipa(self.tmp / "game.ipa")
        dst = self.tmp / "other_dir" / "game_copy.ipa"
        dst.parent.mkdir()
        dst.write_bytes(src.read_bytes())
        a, b = fingerprint_game(src), fingerprint_game(dst)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertEqual(a["bundle_id"], "com.example.nineking")
        self.assertEqual(a["version"], "1.0.5")
        self.assertNotEqual(a["path_hash"], b["path_hash"])
        self.assertEqual(a["size"], b["size"])
        self.assertEqual(len(a["files"]), 5)

    def test_change_one_byte_in_featured_entry_changes_fingerprint(self):
        a_path = _build_ipa(self.tmp / "a.ipa")
        b_path = _build_ipa(
            self.tmp / "b.ipa",
            plist=_plist_bytes(version="1.0.6"))  # 1 byte khác trong Info.plist
        self.assertNotEqual(fingerprint_game(a_path)["fingerprint"],
                            fingerprint_game(b_path)["fingerprint"])

    def test_change_non_featured_entry_keeps_fingerprint(self):
        # data3.bin không nằm trong 5 entry đặc trưng (Info.plist, config,
        # manifest, version, data1) nên đổi nó không đổi fingerprint (§8.2).
        a_path = _build_ipa(self.tmp / "a.ipa")
        b_path = _build_ipa(self.tmp / "b.ipa",
                            extra={"Payload/Game.app/data3.bin": _det_bytes(100, 99)})
        self.assertEqual(fingerprint_game(a_path)["fingerprint"],
                         fingerprint_game(b_path)["fingerprint"])

    def test_plain_file_full_hash_and_mode(self):
        p = _build_plain(self.tmp / "note.txt", b"xin chao BearTea")
        fp = fingerprint_game(p)
        self.assertEqual(fp["files"][0]["sha256"],
                         hashlib.sha256(b"xin chao BearTea").hexdigest())
        self.assertEqual(fp["hash_mode"], "full")
        self.assertIsNone(fp["bundle_id"])
        self.assertIsNone(fp["version"])

    def test_large_plain_file_head_tail_mode(self):
        # File thưa (sparse) 600MB: chỉ hash 64MB đầu + 64MB cuối + size.
        p = self.tmp / "big.bin"
        with open(str(p), "wb") as fh:
            fh.truncate(600 * 1024 * 1024)
        fp = fingerprint_game(p)
        self.assertEqual(fp["hash_mode"], "head_tail_64mb")
        self.assertEqual(fp["size"], 600 * 1024 * 1024)
        h = hashlib.sha256()
        h.update(b"\x00" * (64 * 1024 * 1024))
        h.update(b"\x00" * (64 * 1024 * 1024))
        h.update(str(600 * 1024 * 1024).encode("ascii"))
        self.assertEqual(fp["files"][0]["sha256"], h.hexdigest())

    def test_missing_file_raises(self):
        with self.assertRaises(PB3Error) as ctx:
            fingerprint_game(self.tmp / "khong_co.ipa")
        self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")

    def test_engine_unknown_for_plain_text(self):
        p = _build_plain(self.tmp / "a.txt", b"just text")
        fp = fingerprint_game(p)
        self.assertEqual(fp["engine"], "unknown")
        self.assertEqual(fp["engine_conf"], 0.0)

    def test_zip_without_plist_has_no_bundle_id(self):
        p = self.tmp / "plain.zip"
        with zipfile.ZipFile(str(p), "w") as zf:
            zf.writestr("config.json", b"{}")
            zf.writestr("data.bin", _det_bytes(10, 5))
        fp = fingerprint_game(p)
        self.assertIsNone(fp["bundle_id"])
        self.assertIsNone(fp["version"])
        self.assertEqual(len(fp["files"]), 2)


class TestKBSchema(TmpCase):
    def test_creates_tables_and_wal(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            self.assertEqual(kb.schema_version, SCHEMA_VERSION)
            conn = sqlite3.connect(str(kb.db_path))
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                self.assertEqual(mode.lower(), "wal")
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                for t in ("schema_version", "games", "files", "locales",
                          "choices", "runs", "plugin_stats"):
                    self.assertIn(t, tables)
            finally:
                conn.close()
        finally:
            kb.close()

    def test_migration_v0_to_v1_creates_tables(self):
        db = self.tmp / "old.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE schema_version (version INTEGER)")
        conn.execute("INSERT INTO schema_version VALUES (0)")
        conn.commit()
        conn.close()
        kb = KB(db)
        try:
            self.assertEqual(kb.schema_version, 1)
            tables = {r[0] for r in sqlite3.connect(str(db)).execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for t in ("games", "files", "locales", "choices", "runs",
                      "plugin_stats"):
                self.assertIn(t, tables)
        finally:
            kb.close()

    def test_kb_reopen_twice_keeps_data(self):
        ipa = _build_ipa(self.tmp / "game.ipa")
        fp = fingerprint_game(ipa)
        kb = KB(self.tmp / "kb.sqlite")
        gid = kb.remember(fp, fp["files"], [], {})
        kb.close()
        kb2 = KB(self.tmp / "kb.sqlite")
        try:
            self.assertEqual(kb2.schema_version, 1)
            games = kb2.list_games()
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0]["id"], gid)
            prof = kb2.lookup(ipa)
            self.assertIsNotNone(prof)
            self.assertEqual(prof["game"]["bundle_id"], "com.example.nineking")
        finally:
            kb2.close()
        kb3 = KB(self.tmp / "kb.sqlite")  # mở lần 2 liên tiếp không lỗi
        kb3.close()


class TestKBRememberLookup(TmpCase):
    def _remembered(self, kb, path=None, choices=None):
        ipa = path or _build_ipa(self.tmp / "game.ipa")
        fp = fingerprint_game(ipa)
        locales = [{"entry_path": "Payload/Game.app/texts/en.json",
                    "locale": "en", "adapter_id": "json_text",
                    "key_count": 3, "maturity": "M3"}]
        gid = kb.remember(fp, fp["files"], locales,
                          choices if choices is not None else
                          {fp["files"][0]["sha256"]: "json_text"})
        return ipa, fp, gid

    def test_remember_and_lookup_reuse(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp, gid = self._remembered(kb)
            prof = kb.lookup(ipa)
            self.assertIsNotNone(prof)
            self.assertIsNone(kb.last_lookup_reason)
            self.assertEqual(prof["game"]["bundle_id"], "com.example.nineking")
            self.assertEqual(prof["game"]["version"], "1.0.5")
            self.assertEqual(len(prof["files"]), 5)
            self.assertEqual(len(prof["locales"]), 1)
            self.assertEqual(prof["locales"][0]["locale"], "en")
            self.assertEqual(prof["choices"],
                             {fp["files"][0]["sha256"]: "json_text"})
        finally:
            kb.close()

    def test_lookup_unknown_game_returns_none(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa = _build_ipa(self.tmp / "game.ipa")
            self.assertIsNone(kb.lookup(ipa))
            self.assertIn("chưa có profile", kb.last_lookup_reason or "")
        finally:
            kb.close()

    def test_lookup_hash_mismatch_no_silent_reuse(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp, gid = self._remembered(kb)
            changed = _build_ipa(self.tmp / "game2.ipa",
                                 plist=_plist_bytes(version="1.0.6"))
            # Ghi đè đúng đường dẫn cũ bằng IPA đã đổi 1 byte.
            changed.replace(ipa)
            self.assertIsNone(kb.lookup(ipa))
            self.assertIn("hash lệch", kb.last_lookup_reason or "")
        finally:
            kb.close()

    def test_no_translation_content_in_db(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            secret = "Xin chào thế giới bí mật".encode("utf-8")
            ipa = _build_ipa(self.tmp / "game.ipa", text_content=secret)
            fp = fingerprint_game(ipa)
            kb.remember(fp, fp["files"], [], {})
            kb.close()
            raw = (self.tmp / "kb.sqlite").read_bytes()
            self.assertNotIn(b"Xin ch\xc3\xa0o", raw)
            self.assertNotIn(b"th\xe1\xba\xbf gi\xe1\xbb\x9bi", raw)
            conn = sqlite3.connect(str(self.tmp / "kb.sqlite"))
            try:
                cols = [r[1] for r in conn.execute(
                    "SELECT name, sql FROM sqlite_master")]
                for sql in conn.execute(
                        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
                    low = (sql[0] or "").lower()
                    self.assertNotIn("translation", low)
                    self.assertNotIn("content", low)
            finally:
                conn.close()
        finally:
            try:
                kb.close()
            except Exception:
                pass

    def test_forget_removes_everything(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp, gid = self._remembered(kb)
            kb.record_run(gid, "extract", "ok")
            self.assertEqual(len(kb.list_games()), 1)
            self.assertTrue(kb.forget("com.example.nineking"))
            self.assertEqual(kb.list_games(), [])
            self.assertIsNone(kb.lookup(ipa))
            self.assertFalse(kb.forget("com.example.nineking"))
            self.assertFalse(kb.forget(999999))
        finally:
            kb.close()

    def test_record_run_and_plugin_stats(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp, gid = self._remembered(kb)
            run_id = kb.record_run(gid, "extract", "ok")
            self.assertIsInstance(run_id, int)
            kb.record_run(gid, "mod", "fail", error_code="E_VERIFY_FAILED")
            row = kb.record_plugin_stats("json_text", "1.0", True)
            self.assertEqual((row["games_seen"], row["success"], row["fail"]),
                             (1, 1, 0))
            row = kb.record_plugin_stats("json_text", "1.0", False)
            self.assertEqual((row["games_seen"], row["success"], row["fail"]),
                             (2, 1, 1))
            with self.assertRaises(PB3Error) as ctx:
                kb.record_run(999999, "extract", "ok")
            self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")
        finally:
            kb.close()

    def test_plugin_stats_getter_sums_across_versions(self):
        # Ma trận hỗ trợ đọc số game đã test qua plugin_stats() (C09 tích hợp).
        kb = KB(self.tmp / "kb.sqlite")
        try:
            self.assertEqual(kb.plugin_stats(), {})
            kb.record_plugin_stats("json_text", "1.0", True)
            kb.record_plugin_stats("json_text", "1.0", False)
            kb.record_plugin_stats("json_text", "2.0", True)
            kb.record_plugin_stats("csv_table", "1.0", True)
            self.assertEqual(kb.plugin_stats(),
                             {"json_text": 3, "csv_table": 1})
        finally:
            kb.close()

    def test_prune_frees_when_over_limit(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp, gid = self._remembered(kb)
            for _ in range(50):
                kb.record_run(gid, "extract", "ok")
            freed = kb.prune(max_mb=0)  # giới hạn 0 -> luôn vượt
            self.assertGreater(freed, 0)
            self.assertEqual(kb.prune(max_mb=64), 0)  # dưới giới hạn: không dọn
            self.assertEqual(len(kb.list_games()), 1)  # chỉ xóa runs, giữ game
        finally:
            kb.close()

    def test_list_games(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            a = _build_ipa(self.tmp / "a.ipa")
            b = _build_ipa(self.tmp / "b.ipa",
                           plist=_plist_bytes(bundle_id="com.example.other"))
            kb.remember(fingerprint_game(a), fingerprint_game(a)["files"], [], {})
            kb.remember(fingerprint_game(b), fingerprint_game(b)["files"], [], {})
            games = kb.list_games()
            self.assertEqual(len(games), 2)
            self.assertEqual({g["bundle_id"] for g in games},
                             {"com.example.nineking", "com.example.other"})
        finally:
            kb.close()


class TestKBExportImport(TmpCase):
    def _setup_kb(self, kb):
        ipa = _build_ipa(self.tmp / "game.ipa")
        fp = fingerprint_game(ipa)
        locales = [{"entry_path": "Payload/Game.app/texts/en.json",
                    "locale": "en", "adapter_id": "json_text",
                    "key_count": 3, "maturity": "M3"}]
        choices = {fp["files"][0]["sha256"]: "json_text"}
        kb.remember(fp, fp["files"], locales, choices)
        return ipa, fp

    def test_export_import_roundtrip_keeps_choices(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp = self._setup_kb(kb)
            prof = kb.export_profile("com.example.nineking")
            self.assertEqual(prof["game"]["bundle_id"], "com.example.nineking")
            self.assertEqual(prof["game"]["version"], "1.0.5")
            self.assertEqual(prof["game"]["engine"], "unknown")
            for f in prof["files"]:
                self.assertEqual(len(f["sha256_8"]), 8)
                self.assertIn("adapter", f)
            self.assertEqual(prof["locales"][0]["locale"], "en")
            # JSON thuần, đọc lại được.
            prof2 = json.loads(json.dumps(prof, ensure_ascii=False))
            self.assertTrue(kb.forget("com.example.nineking"))
            gid, warnings = kb.import_profile(prof2)
            self.assertIn("chưa kiểm", " ".join(warnings))
            back = kb.export_profile(gid)
            self.assertEqual(back["choices"], prof["choices"])
            self.assertEqual(back["files"], prof["files"])
            self.assertEqual(back["locales"], prof["locales"])
        finally:
            kb.close()

    def test_import_with_mismatched_hash_warns_and_distrusts(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp = self._setup_kb(kb)
            prof = kb.export_profile("com.example.nineking")
            changed = _build_ipa(self.tmp / "changed.ipa",
                                 plist=_plist_bytes(version="1.0.6"))
            gid, warnings = kb.import_profile(prof, path=changed)
            joined = " ".join(warnings)
            self.assertIn("hash lệch", joined)
            # File hash lệch (Info.plist) bị bỏ qua, không lưu vào DB;
            # các file khớp hash vẫn lưu và lookup tái dùng được.
            conn = sqlite3.connect(str(self.tmp / "kb.sqlite"))
            conn.row_factory = sqlite3.Row
            stored = [dict(r) for r in conn.execute(
                "SELECT rel_path FROM files WHERE game_id = ?", (gid,))]
            rels = {r["rel_path"] for r in stored}
            self.assertNotIn("Payload/Game.app/Info.plist", rels)
            self.assertIn("Payload/Game.app/config.json", rels)
            prof2 = kb.lookup(changed)
            self.assertIsNotNone(prof2)
            self.assertNotIn("Payload/Game.app/Info.plist",
                             {f["rel_path"] for f in prof2["files"]})
        finally:
            kb.close()

    def test_import_without_file_warns_chua_kiem(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            ipa, fp = self._setup_kb(kb)
            prof = kb.export_profile("com.example.nineking")
            gid, warnings = kb.import_profile(prof)
            self.assertTrue(any("chưa kiểm" in w for w in warnings))
        finally:
            kb.close()

    def test_import_bad_shape_raises(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            for bad in ({}, {"game": {}}, {"game": {"bundle_id": ""}},
                        {"game": {"bundle_id": "x"}, "files": {}}):
                with self.assertRaises(PB3Error, msg=repr(bad)):
                    kb.import_profile(bad)
        finally:
            kb.close()

    def test_export_unknown_game_raises(self):
        kb = KB(self.tmp / "kb.sqlite")
        try:
            with self.assertRaises(PB3Error) as ctx:
                kb.export_profile("com.example.khongco")
            self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")
        finally:
            kb.close()


class TestInventoryCache(TmpCase):
    def _file(self, name="game.ipa", content=None):
        p = self.tmp / name
        p.write_bytes(content if content is not None else _det_bytes(5000, 7))
        return p

    def test_hit_and_miss(self):
        cache = InventoryCache(self.tmp / "cache")
        p = self._file()
        entries = [{"rel_path": "a.json", "size": 10}]
        self.assertIsNone(cache.get(p))
        self.assertTrue(cache.put(p, entries))
        self.assertEqual(cache.get(p), entries)
        self.assertEqual(len(cache), 1)

    def test_invalidate_when_mtime_changes(self):
        cache = InventoryCache(self.tmp / "cache")
        p = self._file()
        cache.put(p, [{"rel_path": "a.json"}])
        st = p.stat()
        os.utime(str(p), ns=(st.st_mtime_ns + 10 ** 9, st.st_mtime_ns + 10 ** 9))
        self.assertIsNone(cache.get(p))

    def test_invalidate_when_size_changes(self):
        cache = InventoryCache(self.tmp / "cache")
        p = self._file()
        cache.put(p, [{"rel_path": "a.json"}])
        with open(str(p), "ab") as fh:
            fh.write(b"x")
        self.assertIsNone(cache.get(p))

    def test_invalidate_when_content_changes_same_size(self):
        cache = InventoryCache(self.tmp / "cache")
        p = self._file(content=b"A" * 5000)
        cache.put(p, [{"rel_path": "a.json"}])
        p.write_bytes(b"B" * 5000)  # cùng size, khác nội dung -> hash đầu đổi
        self.assertIsNone(cache.get(p))

    def test_clear_returns_count(self):
        cache = InventoryCache(self.tmp / "cache")
        p, q = self._file("a.bin"), self._file("b.bin", _det_bytes(100, 8))
        cache.put(p, [{"rel_path": "a"}])
        cache.put(q, [{"rel_path": "b"}])
        self.assertEqual(cache.clear(), 2)
        self.assertEqual(len(cache), 0)
        self.assertIsNone(cache.get(p))

    def test_lru_max_32_items(self):
        cache = InventoryCache(self.tmp / "cache")
        paths = []
        for i in range(33):
            p = self._file("f%02d.bin" % i, _det_bytes(50, 100 + i))
            paths.append(p)
            cache.put(p, [{"rel_path": "x"}])
        self.assertEqual(len(cache), 32)
        self.assertIsNone(cache.get(paths[0]))   # mục cũ nhất bị đuổi
        self.assertIsNotNone(cache.get(paths[-1]))

    def test_persists_across_reopen(self):
        cache = InventoryCache(self.tmp / "cache")
        p = self._file()
        cache.put(p, [{"rel_path": "a.json", "size": 3}])
        cache2 = InventoryCache(self.tmp / "cache")
        self.assertEqual(cache2.get(p), [{"rel_path": "a.json", "size": 3}])

    def test_corrupt_cache_file_ignored(self):
        cdir = self.tmp / "cache"
        cdir.mkdir()
        (cdir / "inventory_cache.json").write_text("{hong json", encoding="utf-8")
        cache = InventoryCache(cdir)
        p = self._file()
        self.assertIsNone(cache.get(p))
        self.assertTrue(cache.put(p, [{"rel_path": "a"}]))
        self.assertEqual(cache.get(p), [{"rel_path": "a"}])

    def test_inventory_key_shape(self):
        p = self._file(content=b"hello")
        key = inventory_key(p)
        self.assertEqual(len(key), 4)
        self.assertEqual(key[2], 5)
        self.assertEqual(key[3], hashlib.sha256(b"hello").hexdigest())
        self.assertTrue(key[0].endswith("game.ipa"))


if __name__ == "__main__":
    unittest.main()
