"""Test plugin Godot .translation (Claude 08, nhóm Godot): chỉ đọc.

Fixture tự dựng trong RAM bằng tests/fixtures/godot/builder.py (magic
"TR", version 1). Dữ liệu mẫu có tiếng Việt có dấu, placeholder và thẻ.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.errors import PB3Error
from plugins.godot_translation.plugin import GodotTranslationPlugin
from registry.conformance import run_conformance
from sdk.api import PluginContext
from sdk.types import Candidate, LocTable
from sdk.vfs import MemoryVFS
from tests.fixtures.godot import builder as gb
from tests.helpers import (ROOT, TmpCase, copy_project, register_tmp_dir,
                           slow_subprocess, subprocess_timeout, unregister_tmp_dir)
from core.limits import Limits

PAIRS = [
    ("Play", "Chơi"),
    ("Quit", "Thoát"),
    ("Settings", "Cài đặt"),
    ("Hello, {0}!", "Xin chào, {0}!"),
    ("Press [b]Start[/b]", "Nhấn [b]Bắt đầu[/b]"),
    ("Line1\nLine2", "Dòng1\nDòng2"),
    ("Untranslated", ""),
]


def _ctx(files, tmp):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None,
                         "godot_translation")


def _parse(data, tmp, path="game.vi.translation", locale=None):
    plug = GodotTranslationPlugin()
    ctx = _ctx({path: data}, tmp).for_plugin(plug.manifest)
    if locale is not None:
        ctx.locale.code = locale
    return plug.parse(ctx, Candidate(path, plug.id, 0.9, []))


class TestGodotTranslationParse(TmpCase):
    def test_fixture_vi(self):
        t = _parse(gb.build_translation(PAIRS, locale="vi"), self.tmp)
        got = [(e.key, e.value) for e in t.entries]
        self.assertEqual(got, PAIRS)
        self.assertEqual(t.locale, "vi")
        self.assertEqual(t.schema_hint["flavor"], "godot_translation")
        self.assertEqual(t.stats["messages"], len(PAIRS))
        # Placeholder, thẻ và xuống dòng giữ nguyên.
        by_key = {e.key: e.value for e in t.entries}
        self.assertEqual(by_key["Hello, {0}!"], "Xin chào, {0}!")
        self.assertEqual(by_key["Press [b]Start[/b]"], "Nhấn [b]Bắt đầu[/b]")
        self.assertEqual(by_key["Line1\nLine2"], "Dòng1\nDòng2")

    def test_empty_value_is_empty_status(self):
        t = _parse(gb.build_translation([("A", ""), ("B", "Bê")], locale="vi"),
                   self.tmp)
        by_key = {e.key: e for e in t.entries}
        self.assertEqual(by_key["A"].status, "empty")
        self.assertEqual(by_key["B"].status, "ok")

    def test_locale_from_file_wins(self):
        t = _parse(gb.build_translation([("A", "À")], locale="en"), self.tmp,
                   locale="fr")
        self.assertEqual(t.locale, "en")

    def test_empty_locale_falls_back_to_ctx(self):
        t = _parse(gb.build_translation([("A", "À")], locale=""), self.tmp,
                   locale="vi")
        self.assertEqual(t.locale, "vi")

    def test_duplicate_msgid_is_conflict(self):
        t = _parse(gb.build_translation([("A", "Một"), ("A", "Hai")], locale="vi"),
                   self.tmp)
        self.assertEqual([e.value for e in t.entries], ["Một", "Hai"])
        self.assertEqual(t.entries[1].status, "conflict")
        self.assertEqual(len(t.conflicts), 1)

    def test_empty_table(self):
        t = _parse(gb.build_translation([], locale="vi"), self.tmp)
        self.assertEqual(t.entries, [])
        self.assertEqual(t.stats["messages"], 0)


class TestGodotTranslationErrors(TmpCase):
    def test_bad_magic(self):
        with self.assertRaises(PB3Error) as cm:
            _parse(b"XX\x01\x00\x00\x00", self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_rsrc_binary_resource_unsupported(self):
        # File .translation thật của Godot là binary resource (RSRC):
        # báo rõ chưa hỗ trợ thay vì đọc sai.
        with self.assertRaises(PB3Error) as cm:
            _parse(b"RSRC" + b"\x00" * 20, self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_version_2_unsupported(self):
        data = bytearray(gb.build_translation([("A", "À")], locale="vi"))
        struct.pack_into("<I", data, 2, 2)
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_truncated(self):
        data = gb.build_translation([("A", "À")], locale="vi")
        with self.assertRaises(PB3Error) as cm:
            _parse(data[:10], self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_trailing_bytes(self):
        data = gb.build_translation([("A", "À")], locale="vi") + b"XYZ"
        with self.assertRaises(PB3Error) as cm:
            _parse(data, self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")

    def test_bad_utf8(self):
        data = bytearray(gb.build_translation([("A", "À")], locale="vi"))
        # Lật 1 byte trong msgstr ("À" = 2 byte UTF-8 ở cuối file).
        data[-1] ^= 0xFF
        with self.assertRaises(PB3Error) as cm:
            _parse(bytes(data), self.tmp)
        self.assertEqual(cm.exception.code, "E_CORRUPT")


class TestGodotTranslationPlugin(TmpCase):
    def test_detect_list_probe_selftest(self):
        data = gb.build_translation(PAIRS, locale="vi")
        plug = GodotTranslationPlugin()
        ctx = _ctx({"game.vi.translation": data, "x.txt": b"?"}, self.tmp).for_plugin(plug.manifest)
        ev = plug.detect(ctx)
        # Lớp nền trả 2 evidence: extension + content (sniff TR = 0.9).
        self.assertEqual([e.kind for e in ev], ["extension", "content"])
        cands = plug.list_candidates(ctx)
        self.assertEqual([c.path for c in cands], ["game.vi.translation"])
        probe = plug.probe(ctx, cands[0])
        self.assertTrue(probe["ok"], probe)
        self.assertEqual(probe["entries"], len(PAIRS))
        self.assertTrue(plug.selftest(ctx)["ok"])

    def test_probe_bad_file(self):
        plug = GodotTranslationPlugin()
        ctx = _ctx({"bad.translation": b"NOPE"}, self.tmp).for_plugin(plug.manifest)
        probe = plug.probe(ctx, plug.list_candidates(ctx)[0])
        self.assertFalse(probe["ok"])

    def test_build_and_verify_refused(self):
        # Chỉ đọc: build/verify phải từ chối E_WRITE_DISABLED (như sqlite_loc).
        plug = GodotTranslationPlugin()
        data = gb.build_translation([("A", "À")], locale="vi")
        ctx = _ctx({"game.vi.translation": data}, self.tmp).for_plugin(plug.manifest)
        table = plug.parse(ctx, Candidate("game.vi.translation", plug.id, 0.9, []))
        with self.assertRaises(PB3Error) as cm:
            plug.build(ctx, table, data)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")
        with self.assertRaises(PB3Error) as cm:
            plug.verify(ctx, data, data)
        self.assertEqual(cm.exception.code, "E_WRITE_DISABLED")

    def test_conformance_with_fixture(self):
        data = gb.build_translation(PAIRS, locale="vi")
        rep = run_conformance(GodotTranslationPlugin,
                              files={"game.vi.translation": data})
        self.assertTrue(rep.ok, rep.format(48))
        self.assertEqual(rep.get("parse").status, "PASS")
        self.assertEqual(rep.get("manifest").status, "PASS")

    def test_registry_lists_godot_translation(self):
        from registry.registry import build_registry
        reg = build_registry(str(ROOT), None)
        rows = {r["id"]: r for r in reg.rows()}
        self.assertIn("godot_translation", rows)
        self.assertEqual(rows["godot_translation"]["state"], "ok")
        self.assertEqual(rows["godot_translation"]["kind"], "format")
        self.assertEqual(rows["godot_translation"]["maturity"], "M2")


@slow_subprocess
class TestGodotTranslationCli(TmpCase):
    """CLI thật trên bản sao dự án (tầng slow)."""

    _shared_td = None
    _shared_proj = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3test-godot-tr-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args),
                           cwd=str(self._shared_proj), capture_output=True,
                           text=True, timeout=subprocess_timeout(120),
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_plugin_list_shows_godot_translation(self):
        rc, out, err = self.cli("plugin", "list")
        self.assertEqual(rc, 0, err)
        self.assertIn("godot_translation  [ok]", out, err)

    def test_plugin_test_godot_translation(self):
        rc, out, err = self.cli("plugin", "test", "godot_translation")
        self.assertEqual(rc, 0, err)
        self.assertIn("KẾT QUẢ: PASS", out, err)


if __name__ == "__main__":
    unittest.main()
