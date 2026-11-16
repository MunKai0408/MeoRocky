"""Test plugin Unreal LOCRES (Claude 08, nhóm Unreal): chỉ đọc.

Fixture dựng trong RAM bằng tests/fixtures/unreal/builder.py (không đọc
file nhị phân lạ). .locmeta dạng JSON không cần plugin riêng: chứng minh
bằng test json_text đọc được file .locmeta JSON ở cuối file.
"""
import struct
import unittest

from core.errors import PB3Error
from plugins.json_text.plugin import JsonPlugin
from plugins.unreal_locres.plugin import (
    LOCRES_MAGIC, UnrealLocresPlugin, build_locres)
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocTable
from sdk.vfs import MemoryVFS
from core.limits import Limits
from tests.fixtures.unreal.builder import SAMPLE_NAMESPACES, sample_locres_bytes
from tests.helpers import TmpCase

EXPECTED = [("UI/Play", "Chơi"), ("UI/Quit", "Thoát"),
            ("UI/Settings", "Cài đặt"),
            ("Menu/Welcome", "Chào mừng, {0}!"), ("Menu/Score", "Điểm: {0}"),
            ("Hello", "Xin chào")]


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, "unreal_locres")


def _plug():
    return UnrealLocresPlugin()


def _parse(data, tmp, path="vi.locres"):
    plug = _plug()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestLocresParse(TmpCase):
    def test_parse_v3_cityhash(self):
        t = _parse(sample_locres_bytes(3), self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries], EXPECTED)
        self.assertEqual(t.stats["namespaces"], 3)
        self.assertEqual(t.stats["keys"], 6)
        self.assertEqual(t.stats["version"], 3)
        self.assertEqual(t.schema_hint["namespaces"], ["UI", "Menu", ""])
        self.assertEqual(t.adapter_id, "unreal_locres")
        self.assertTrue(all(e.source is not None for e in t.entries))
        self.assertIn("source_hash=0x11111111", t.entries[0].notes)

    def test_parse_v2_optimized_v1_compact_v0_legacy(self):
        for version in (2, 1, 0):
            with self.subTest(version=version):
                t = _parse(sample_locres_bytes(version), self.tmp)
                self.assertEqual([(e.key, e.value) for e in t.entries], EXPECTED)
                self.assertEqual(t.stats["version"], version)

    def test_empty_namespace_has_bare_key(self):
        t = _parse(build_locres([("", [("Hi", "Xin chào", 0)])], version=3), self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries], [("Hi", "Xin chào")])

    def test_duplicate_key_is_conflict(self):
        data = build_locres([("UI", [("Play", "Chơi", 0), ("Play", "Chơi lần nữa", 0)])],
                            version=3)
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].value, "Chơi")
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertEqual(t.conflicts, [{"key": "UI/Play", "locale": None}])

    def test_shared_string_deduplicated(self):
        # Hai key cùng bản dịch -> bảng chuỗi chỉ có 1 chuỗi.
        data = build_locres([("A", [("x", "Giống nhau", 0)]),
                             ("B", [("y", "Giống nhau", 0)])], version=3)
        t = _parse(data, self.tmp)
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("A/x", "Giống nhau"), ("B/y", "Giống nhau")])
        (count,) = struct.unpack_from("<I", data, struct.unpack_from("<Q", data, 17)[0])
        self.assertEqual(count, 1)

    def test_empty_value_is_empty_status(self):
        data = build_locres([("UI", [("Blank", "", 0)])], version=3)
        t = _parse(data, self.tmp)
        self.assertEqual(t.entries[0].status, "empty")


class TestLocresCorrupt(TmpCase):
    def test_empty_is_corrupt(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_truncated_is_corrupt(self):
        data = sample_locres_bytes(3)
        with self.assertRaises(PB3Error) as cm:
            _parse(data[:40], self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_bad_version_is_unsupported(self):
        data = bytearray(sample_locres_bytes(3))
        data[16] = 9  # version u8 sau magic
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_bad_string_table_offset_is_corrupt(self):
        data = bytearray(sample_locres_bytes(3))
        data[17:25] = struct.pack("<Q", 10 ** 6)
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_bad_string_index_is_corrupt(self):
        # Chỉ số chuỗi của entry đầu: sau header 25 + [u32 tổng key] +
        # u32 số namespace + [u32 hash ns] + FString "UI" + u32 số key +
        # [u32 hash key] + FString "Play" + u32 source_hash -> tới u32 index.
        data = bytearray(sample_locres_bytes(3))
        pos = 25 + 4 + 4 + 4 + 4 + 3 + 4 + 4 + 4 + 5 + 4
        data[pos:pos + 4] = struct.pack("<I", 99)
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_trailing_garbage_is_corrupt(self):
        data = sample_locres_bytes(3) + b"rac"
        with self.assertRaises(PB3Error) as cm:
            _parse(data, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_legacy_truncated_is_corrupt(self):
        data = sample_locres_bytes(0)
        with self.assertRaises(PB3Error) as cm:
            _parse(data[:10], self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_not_locres_is_corrupt(self):
        # Số namespace = 1 nhưng FString tên cắt cụt -> E_CORRUPT.
        data = struct.pack("<I", 1) + struct.pack("<i", 5) + b"ab"
        with self.assertRaises(PB3Error) as cm:
            _parse(data, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestLocresReadOnly(TmpCase):
    def test_build_raises_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        with self.assertRaises(PB3Error) as cm:
            plug.build(ctx, LocTable([], None, "unreal_locres"), sample_locres_bytes(3))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_verify_raises_write_disabled(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        with self.assertRaises(PB3Error) as cm:
            plug.verify(ctx, sample_locres_bytes(3), sample_locres_bytes(3))
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_selftest_ok(self):
        plug = _plug()
        ctx = _ctx({}, self.tmp).for_plugin(plug.manifest)
        res = plug.selftest(ctx)
        self.assertTrue(res["ok"], res)


class TestLocresPlugin(TmpCase):
    def test_detect_and_candidates(self):
        plug = _plug()
        ctx = _ctx({"vi.locres": sample_locres_bytes(3), "b.txt": b"hello"},
                   self.tmp).for_plugin(plug.manifest)
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["vi.locres"])
        self.assertTrue(all(c.adapter_id == "unreal_locres" for c in cands))
        ev = plug.detect(ctx)
        self.assertTrue(any(e.kind == "extension" for e in ev))

    def test_probe_counts_entries(self):
        plug = _plug()
        ctx = _ctx({"vi.locres": sample_locres_bytes(3)},
                   self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("vi.locres", plug.id, 0.9, []))
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["entries"], 6)

    def test_probe_legacy_no_magic(self):
        plug = _plug()
        ctx = _ctx({"old.locres": sample_locres_bytes(0)},
                   self.tmp).for_plugin(plug.manifest)
        p = plug.probe(ctx, Candidate("old.locres", plug.id, 0.9, []))
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["entries"], 6)

    def test_sniff_scores(self):
        plug = _plug()
        self.assertEqual(plug._sniff(LOCRES_MAGIC + b"abc", "x.locres"), 0.9)
        self.assertEqual(plug._sniff(b"hello", "x.locres"), 0.0)

    def test_conformance_with_fixture(self):
        files = {"vi.locres": sample_locres_bytes(3)}
        rep = run_conformance(UnrealLocresPlugin, files=files)
        self.assertTrue(rep.ok, rep.format())
        self.assertTrue(rep.data_tested)
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("readonly").status, "PASS")
        self.assertEqual(rep.get("write").status, "SKIP")
        self.assertEqual(rep.get("selftest").status, "PASS")

    def test_locmeta_json_read_by_json_text(self):
        # .locmeta dạng JSON không cần plugin riêng: json_text đọc được.
        plug = JsonPlugin()
        ctx = PluginContext(MemoryVFS(
            {"game.locmeta": b'{"native_culture": "en", "game": "TestGame"}'}),
            Limits(), None, None, str(self.tmp), True, None, None, "json_text")
        ctx = ctx.for_plugin(plug.manifest)
        t = plug.parse(ctx, Candidate("game.locmeta", plug.id, 0.9, []))
        self.assertEqual([(e.key, e.value) for e in t.entries],
                         [("native_culture", "en"), ("game", "TestGame")])


if __name__ == "__main__":
    unittest.main()
