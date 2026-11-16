"""Test nhận diện engine Cocos / Defold / GameMaker + mở rộng nhận diện
Godot / Unreal (Claude 08, nhóm engine-detection).

- detect_engine: magic + tên cho từng engine, Unity không bị ảnh hưởng.
- 3 plugin kind="engine" (chỉ nhận diện): detect / hints / selftest qua
  conformance trong bộ nhớ + CLI thật (`plugin test`).
- Pipeline trích xuất: engine hint chạy end-to-end qua run_extraction.
Fixture chỉ dùng tên entry + magic byte mẫu trong RAM (không cần file nhị
phân thật)."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.app import bootstrap
from core.errors import PB3Error
from core.limits import Limits
from plugins.cocos_detect.plugin import CocosDetectPlugin
from plugins.defold_detect.plugin import DefoldDetectPlugin
from plugins.gamemaker_detect.plugin import GameMakerDetectPlugin
from registry.conformance import run_conformance
from sdk import discovery as disc
from sdk import extraction as ext
from sdk.api import PluginContext
from sdk.vfs import MemoryVFS
from services import language_tool
from tests.helpers import ROOT, TmpCase, copy_project, register_tmp_dir, subprocess_timeout, unregister_tmp_dir

LOCRES_MAGIC = bytes.fromhex("0E147475674A03FC4A15909DC3377F1B")
GDPC_HEAD = b"GDPC\x02\x00\x00\x00" + b"\x00" * 56


def _win_bytes(gen8=True):
    """data.win giả: IFF FORM + chunk đầu GEN8 (hoặc XXXX để phủ định)."""
    return b"FORM" + (100).to_bytes(4, "big") + (b"GEN8" if gen8 else b"XXXX") + b"\x00" * 20


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


def _ctx(files, tmp, pid):
    vfs = MemoryVFS(files)
    return PluginContext(vfs, Limits(), None, None, str(tmp), True, None, None, pid)


class TestDetectEngine(TmpCase):
    # -- Unity: hồi quy, không được thay đổi hành vi cũ -------------------
    def test_unity_magic_still_certain(self):
        g = disc.detect_engine(["x.unity3d"], {"x.unity3d": b"UnityFS\x00\x00"})
        self.assertEqual(g.engine, "unity")
        self.assertEqual(g.level, "CHẮC CHẮN")

    def test_unity_names_still_likely(self):
        g = disc.detect_engine(["Payload/g.app/Data/globalgamemanagers",
                                "Payload/g.app/Data/resources.assets"], {})
        self.assertEqual(g.engine, "unity")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_unknown_still_unknown(self):
        g = disc.detect_engine(["game1.zip"], {"game1.zip": b"PK\x03\x04"})
        self.assertEqual(g.engine, "unknown")
        self.assertEqual(g.level, "CHƯA ĐỦ BẰNG CHỨNG")

    # -- Godot -------------------------------------------------------------
    def test_godot_pck_magic_certain(self):
        g = disc.detect_engine(["data.pck"], {"data.pck": GDPC_HEAD})
        self.assertEqual(g.engine, "godot")
        self.assertEqual(g.level, "CHẮC CHẮN")
        self.assertGreaterEqual(g.confidence, 0.9)

    def test_godot_names_likely(self):
        g = disc.detect_engine(["game.pck", "project.godot"], {})
        self.assertEqual(g.engine, "godot")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_godot_translation_name_counts(self):
        g = disc.detect_engine(["locale/vi.translation", "project.godot"], {})
        self.assertEqual(g.engine, "godot")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_godot_single_weak_name_insufficient(self):
        g = disc.detect_engine(["main.gd"], {})
        self.assertEqual(g.engine, "unknown")

    # -- Unreal ------------------------------------------------------------
    def test_unreal_locres_magic_certain(self):
        g = disc.detect_engine(["Game.locres"],
                               {"Game.locres": LOCRES_MAGIC + b"\x03\x00\x00\x00"})
        self.assertEqual(g.engine, "unreal")
        self.assertEqual(g.level, "CHẮC CHẮN")

    def test_unreal_names_likely(self):
        g = disc.detect_engine(["Game.pak", "Game.uproject"], {})
        self.assertEqual(g.engine, "unreal")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_unreal_legacy_locres_name_only(self):
        # .locres Legacy không có magic: một tên mạnh chưa đủ, hai tên thì đủ.
        g = disc.detect_engine(["Game.locres"], {})
        self.assertEqual(g.engine, "unknown")
        g = disc.detect_engine(["Game.locres", "Game.locmeta"], {})
        self.assertEqual(g.engine, "unreal")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    # -- Cocos -------------------------------------------------------------
    def test_cocos_names_likely(self):
        g = disc.detect_engine(["project.json", "main.js", "res/icon.png"], {})
        self.assertEqual(g.engine, "cocos")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_cocos_never_certain_without_magic(self):
        # Đủ tên để confidence chạm trần 1.0 nhưng không có magic -> vẫn chỉ
        # CÓ KHẢ NĂNG (Cocos không có magic đầu file đã xác minh).
        g = disc.detect_engine(["project.json", "frameworks/cocos2d-x/cocos2d.js",
                                "a.ccb", "scenes/s.fire"], {})
        self.assertEqual(g.engine, "cocos")
        self.assertEqual(g.confidence, 1.0)
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    # -- Defold ------------------------------------------------------------
    def test_defold_names_likely(self):
        g = disc.detect_engine(["game.project", "main.collection"], {})
        self.assertEqual(g.engine, "defold")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_defold_never_certain_without_magic(self):
        g = disc.detect_engine(["game.project", "main.collection",
                                "assets/a.atlas", "main.script"], {})
        self.assertEqual(g.engine, "defold")
        self.assertEqual(g.confidence, 1.0)
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    # -- GameMaker ---------------------------------------------------------
    def test_gamemaker_win_magic_certain(self):
        g = disc.detect_engine(["data.win"], {"data.win": _win_bytes()})
        self.assertEqual(g.engine, "gamemaker")
        self.assertEqual(g.level, "CHẮC CHẮN")

    def test_gamemaker_form_without_gen8_is_not_magic(self):
        # "FORM" một mình quá chung (AIFF/IFF): không tính là magic.
        g = disc.detect_engine(["data.win"], {"data.win": _win_bytes(gen8=False)})
        self.assertEqual(g.engine, "unknown")

    def test_gamemaker_names_likely_never_certain(self):
        g = disc.detect_engine(["game.yyp", "objects/o/o.yy", "scripts/s/s.gml"], {})
        self.assertEqual(g.engine, "gamemaker")
        self.assertAlmostEqual(g.confidence, 0.9)
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    # -- Trọng tài giữa các engine ------------------------------------------
    def test_tie_prefers_unity(self):
        g = disc.detect_engine(["globalgamemanagers", "resources.assets",
                                "game.pck", "project.godot"], {})
        self.assertEqual(g.engine, "unity")
        self.assertEqual(g.level, "CÓ KHẢ NĂNG")

    def test_no_false_unity_for_other_engines(self):
        for names in (["project.json", "main.js", "res/a.png"],
                      ["game.project", "main.collection"],
                      ["game.yyp", "o/o.yy", "s/s.gml"],
                      ["data.pck"], ["Game.pak", "Game.uproject"]):
            with self.subTest(names=names):
                g = disc.detect_engine(names, {})
                self.assertNotEqual(g.engine, "unity")

    def test_engine_signals_single_engine(self):
        ev, conf, has_magic = disc.engine_signals(
            "cocos", ["project.json", "main.js", "res/a.png"], {})
        self.assertFalse(has_magic)
        self.assertEqual(conf, 0.6)
        self.assertEqual([e.value for e in ev], ["project.json", "main.js", "res/"])

    def test_engine_signals_unknown_engine_raises(self):
        with self.assertRaises(PB3Error):
            disc.engine_signals("renpy", ["game.rpy"], {})


class TestExtensionTables(TmpCase):
    def test_container_extensions_include_pck_pak(self):
        self.assertIn(".pck", disc.CONTAINER_EXTENSIONS)
        self.assertIn(".pak", disc.CONTAINER_EXTENSIONS)

    def test_loc_extensions_include_engine_locales(self):
        for ext in (".translation", ".locres", ".locmeta", ".yy", ".yyp", ".gml"):
            with self.subTest(ext=ext):
                self.assertIn(ext, disc.LOC_EXTENSIONS)

    def test_engine_extensions_cover_detected_exts(self):
        for ext in (".pck", ".translation", ".pak", ".locres", ".locmeta",
                    ".uproject", ".ccb", ".fire", ".archive", ".arci",
                    ".collection", ".atlas", ".yyp", ".yy", ".gml", ".yyz"):
            with self.subTest(ext=ext):
                self.assertIn(ext, disc.ENGINE_EXTENSIONS)


# ------------------------------------------------------------------ plugin
PLUGIN_CASES = [
    (CocosDetectPlugin, "cocos",
     {"project.json": b'{"version": "3.8.0"}', "main.js": b"cc.game.run();\n",
      "res/icon.png": b"\x89PNG\r\n\x1a\n", "assets/scene.fire": b"[]"},
     "CÓ KHẢ NĂNG"),
    (DefoldDetectPlugin, "defold",
     {"game.project": b"[project]\ntitle = Test\n", "main.collection": b"",
      "main.script": b"-- lua\n", "assets/atlas.atlas": b""},
     "CÓ KHẢ NĂNG"),
    (GameMakerDetectPlugin, "gamemaker",
     {"game.yyp": b'{"resources": []}', "objects/o/o.yy": b"{}",
      "scripts/s/s.gml": b"// gml\n", "data.win": _win_bytes()},
     "CHẮC CHẮN"),
]

OTHER_ENGINE_FILES = {
    "cocos": {"game.project": b"[project]\n", "main.collection": b""},
    "defold": {"project.json": b"{}", "main.js": b"cc.game.run();\n"},
    "gamemaker": {"game.project": b"[project]\n", "main.collection": b""},
}


class TestEnginePlugins(TmpCase):
    def test_conformance_ok_in_memory(self):
        for cls, engine, files, _level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                rep = run_conformance(cls, files=dict(files))
                self.assertTrue(rep.ok, rep.format())
                self.assertEqual(rep.get("manifest").status, "PASS")
                self.assertEqual(rep.get("readonly").status, "PASS")
                self.assertEqual(rep.get("detect").status, "PASS")
                self.assertEqual(rep.get("hints").status, "PASS")
                self.assertEqual(rep.get("selftest").status, "PASS")

    def test_detect_and_hints_find_own_engine(self):
        for cls, engine, files, level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                plug = cls()
                ctx = _ctx(dict(files), self.tmp, plug.id).for_plugin(plug.manifest)
                ev = plug.detect(ctx)
                self.assertTrue(ev, "detect phải trả bằng chứng")
                self.assertTrue(all(isinstance(e, disc.Evidence) for e in ev))
                hints = plug.hints(ctx)
                self.assertEqual(hints["engine"], engine)
                self.assertEqual(hints["level"], level)
                self.assertGreaterEqual(hints["confidence"], 0.5)
                self.assertIn("CHỈ NHẬN DIỆN", hints["note"])
                self.assertIn("suggested_plugins", hints)

    def test_detect_empty_vfs_and_hints_unknown(self):
        for cls, _engine, _files, _level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                plug = cls()
                ctx = _ctx({}, self.tmp, plug.id).for_plugin(plug.manifest)
                self.assertEqual(plug.detect(ctx), [])
                hints = plug.hints(ctx)
                self.assertEqual(hints["engine"], "unknown")
                self.assertEqual(hints["confidence"], 0.0)
                self.assertEqual(hints["level"], "CHƯA ĐỦ BẰNG CHỨNG")

    def test_detect_ignores_other_engine(self):
        for cls, engine, _files, _level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                plug = cls()
                ctx = _ctx(dict(OTHER_ENGINE_FILES[engine]), self.tmp,
                           plug.id).for_plugin(plug.manifest)
                self.assertEqual(plug.detect(ctx), [])

    def test_selftest_direct(self):
        for cls, _engine, _files, _level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                plug = cls()
                ctx = _ctx({}, self.tmp, plug.id).for_plugin(plug.manifest)
                res = plug.selftest(ctx)
                self.assertTrue(res["ok"], res)

    def test_registry_lists_engine_plugins(self):
        from registry.registry import build_registry
        rows = {row["id"]: row for row in build_registry(str(ROOT)).rows()}
        for cls, engine, _files, _level in PLUGIN_CASES:
            with self.subTest(plugin=cls.MANIFEST["id"]):
                self.assertEqual(rows[cls.MANIFEST["id"]]["state"], "ok")
                self.assertEqual(rows[cls.MANIFEST["id"]]["kind"], "engine")
                self.assertEqual(rows[cls.MANIFEST["id"]]["maturity"], "M2")
                self.assertEqual(rows[cls.MANIFEST["id"]]["engines"], [engine])


# ------------------------------------------------------------------ pipeline
class PipelineCase(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-enginedetect-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def setUp(self):
        super().setUp()
        self.app = bootstrap(self._shared_proj)
        self.addCleanup(self.app.close)
        self.reg = language_tool._registry(self.app)
        self.out = self.tmp / "out"

    def run_ext(self, entries):
        path = self.tmp / "game.zip"
        path.write_bytes(_zip_bytes(entries))
        return ext.run_extraction(ext.ExtractionRequest(
            source=str(path), registry=self.reg, tempdir=str(self.app.paths.temp),
            root=str(self._shared_proj), out_dir=str(self.out)))


class TestPipelineEngineHint(PipelineCase):
    def test_pipeline_godot_pck_magic(self):
        res = self.run_ext({"data.pck": GDPC_HEAD,
                            "localization/en.json": b'{"hello": "Hello"}'})
        self.assertEqual(res.engine, "godot")
        self.assertEqual(res.engine_level, "CHẮC CHẮN")

    def test_pipeline_cocos_names(self):
        # project.json (0.3) + main.js (0.15) + res/ (0.15) = 0.6 >= 0.5;
        # chỉ hai dấu hiệu đầu thì chưa đủ ngưỡng (0.45).
        res = self.run_ext({"project.json": b'{"version": "3.8.0"}',
                            "main.js": b"cc.game.run();\n",
                            "res/icon.png": b"\x89PNG\r\n\x1a\n",
                            "localization/en.json": b'{"hello": "Hello"}'})
        self.assertEqual(res.engine, "cocos")
        self.assertEqual(res.engine_level, "CÓ KHẢ NĂNG")

    def test_pipeline_gamemaker_win_magic(self):
        res = self.run_ext({"data.win": _win_bytes(),
                            "localization/en.json": b'{"hello": "Hello"}'})
        self.assertEqual(res.engine, "gamemaker")
        self.assertEqual(res.engine_level, "CHẮC CHẮN")


# ------------------------------------------------------------------ CLI thật
ENGINE_PLUGIN_IDS = ["cocos_detect", "defold_detect", "gamemaker_detect"]


class TestEnginePluginCli(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-realcli-engine-")
        register_tmp_dir(cls._shared_td)
        cls._shared_proj = copy_project(Path(cls._shared_td))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._shared_td, ignore_errors=True)
        unregister_tmp_dir(cls._shared_td)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "menu.py"] + list(args), cwd=str(self._shared_proj),
                           capture_output=True, text=True, timeout=subprocess_timeout(120),
                           env=dict(os.environ, PB3_PROGRESS="0"))
        return r.returncode, r.stdout, r.stderr

    def test_plugin_list_shows_engine_plugins(self):
        rc, out, err = self.cli("plugin", "list")
        self.assertEqual(rc, 0, err)
        for pid in ENGINE_PLUGIN_IDS:
            with self.subTest(pid=pid):
                self.assertIn("%s  [ok]" % pid, out, err)

    def test_plugin_test_each_engine_plugin(self):
        for pid in ENGINE_PLUGIN_IDS:
            with self.subTest(pid=pid):
                rc, out, err = self.cli("plugin", "test", pid)
                self.assertEqual(rc, 0, err + out)
                self.assertIn("KẾT QUẢ: PASS", out, err + out)


if __name__ == "__main__":
    unittest.main()
