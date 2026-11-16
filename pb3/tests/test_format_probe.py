"""Test format probe cho engine lạ (mục phân tích C8).

Probe chạy trên bytes trong RAM (fixture tự dựng bằng builder có sẵn
hoặc zip dựng tay) — không cần file game thật. Kiểm tra:
- engine đã biết (Godot/Unreal/Cocos/Defold/GameMaker/Unity) nhận đúng
  + mức hỗ trợ thật (chỉ dò thì ghi rõ chỉ dò);
- file hoàn toàn lạ / zip không phải game -> "unknown", supported rỗng,
  không raise, không PASS giả;
- plugin chưa bật -> thao tác của nó rơi xuống unsupported kèm lý do.
"""
import io
import random
import unittest
import zipfile

from sdk import format_probe as fp
from tests.fixtures.godot import builder as godot_b
from tests.fixtures.unreal import builder as unreal_b

ALL_PLUGINS = frozenset(fp.ALL_ENGINE_PLUGIN_IDS)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


def _random_bytes(n, seed=7):
    return bytes(random.Random(seed).randrange(256) for _ in range(n))


def _win_bytes(gen8=True):
    return (b"FORM" + (100).to_bytes(4, "big")
            + (b"GEN8" if gen8 else b"XXXX") + b"\x00" * 20)


class TestFormatProbeKnownEngines(unittest.TestCase):
    def test_godot_pck_v2_certain(self):
        data = godot_b.build_pck_v2(
            [("res://hello.txt", b"hello pb3"),
             ("res://data/text.csv", b"a,b\n1,2\n")])
        r = fp.probe_data(data, "game.pck", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "godot")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")
        self.assertEqual(r["file"]["container_kind"], "godot_pck")
        self.assertTrue(r["file"]["container_opened"])
        ops = [s["op"] for s in r["supported"]]
        self.assertTrue(any("pck v1/v2" in o for o in ops))
        self.assertTrue(any("Trích xuất text game Godot" in u["op"]
                            for u in r["unsupported"]))

    def test_godot_pck_v1_certain(self):
        data = godot_b.build_pck_v1([("res://hello.txt", b"hello pb3")])
        r = fp.probe_data(data, "game.pck", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "godot")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")

    def test_godot_translation_tr_likely(self):
        data = godot_b.build_translation([("Hello", "Xin chào")], locale="vi")
        r = fp.probe_data(data, "game.vi.translation",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "godot")
        self.assertEqual(r["engine"]["level"], "CÓ KHẢ NĂNG")
        ops = [s["op"] for s in r["supported"]]
        self.assertTrue(any(".translation" in o for o in ops))
        self.assertTrue(any("RSRC" in u["op"] for u in r["unsupported"]))

    def test_godot_translation_rsrc_not_recognised(self):
        # File .translation do Godot thật xuất ra (RSRC) chưa đọc được:
        # probe phải báo chưa đủ bằng chứng, không nhận bừa là Godot.
        data = b"RSRC" + b"\x00" * 100
        r = fp.probe_data(data, "game.vi.translation",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")

    def test_unreal_pak_v8_certain_via_footer(self):
        data = unreal_b.sample_pak_bytes(version=8)
        r = fp.probe_data(data, "game_v8.pak", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unreal")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")
        self.assertTrue(r["file"]["pak_footer"])
        self.assertTrue(r["file"]["container_opened"])
        ops = [s["op"] for s in r["supported"]]
        self.assertTrue(any(".pak UE4/UE5" in o for o in ops))
        self.assertTrue(any("Oodle" in u["op"] for u in r["unsupported"]))

    def test_unreal_pak_v11_certain_via_footer(self):
        data = unreal_b.sample_pak_v11_bytes()
        r = fp.probe_data(data, "game_v11.pak", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unreal")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")

    def test_unreal_locres_v3_certain(self):
        data = unreal_b.sample_locres_bytes(version=3)
        r = fp.probe_data(data, "vi.locres", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unreal")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")
        ops = [s["op"] for s in r["supported"]]
        self.assertTrue(any(".locres v0" in o for o in ops))

    def test_unreal_locres_legacy_name_only(self):
        # Legacy không có magic: MỘT tên mạnh chưa đủ (luật của discovery:
        # "một tên mạnh chưa đủ, hai tên thì đủ") -> probe báo unknown
        # nhưng vẫn hiện ứng viên unreal gần nhất (conf 0.3) để user biết
        # hướng, không nhận bừa.
        data = unreal_b.sample_locres_bytes(version=0)
        r = fp.probe_data(data, "legacy.locres", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        cand = [c for c in r["candidates"] if c["engine"] == "unreal"]
        self.assertEqual(len(cand), 1)
        self.assertEqual(cand[0]["confidence"], 0.3)
        self.assertTrue(any(".locres" in e for e in cand[0]["evidence"]))

    def test_unreal_locres_legacy_two_names_likely(self):
        # Hai tên mạnh (.locres + .locmeta trong cùng ZIP) thì đủ likely.
        data = _zip_bytes({"Game.locres": unreal_b.sample_locres_bytes(0),
                           "Game.locmeta": b"{}"})
        r = fp.probe_data(data, "game.zip", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unreal")
        self.assertEqual(r["engine"]["level"], "CÓ KHẢ NĂNG")

    def test_gamemaker_data_win_certain(self):
        r = fp.probe_data(_win_bytes(), "data.win",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "gamemaker")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")
        self.assertTrue(all("Nhận diện" in s["op"] for s in r["supported"]))
        self.assertTrue(any("Mở data.win" in u["op"]
                            for u in r["unsupported"]))

    def test_gamemaker_form_without_gen8_not_certain(self):
        # FORM một mình quá chung (AIFF/IFF) -> không CHẮC CHẮN.
        r = fp.probe_data(_win_bytes(gen8=False), "data.win",
                          active_plugins=ALL_PLUGINS)
        self.assertNotEqual(r["engine"]["level"], "CHẮC CHẮN")

    def test_cocos_zip_likely(self):
        data = _zip_bytes({
            "project.json": b'{"version": "3.8.0", "assets": []}',
            "main.js": b"// Cocos Creator\ncc.game.run();\n",
            "res/icon.png": b"\x89PNG\r\n\x1a\n",
            "assets/scene.fire": b"[]",
        })
        r = fp.probe_data(data, "cocos_game.zip", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "cocos")
        self.assertEqual(r["engine"]["level"], "CÓ KHẢ NĂNG")
        self.assertTrue(all("Nhận diện" in s["op"] for s in r["supported"]))
        self.assertTrue(any("chưa có backend Cocos" in u["reason"]
                            for u in r["unsupported"]))

    def test_defold_zip_likely(self):
        data = _zip_bytes({
            "game.project": b"[project]\ntitle = Test\n",
            "main.collection": b"",
            "main.script": b"function init(self) end\n",
            "assets/atlas.atlas": b"",
        })
        r = fp.probe_data(data, "defold_game.zip",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "defold")
        self.assertEqual(r["engine"]["level"], "CÓ KHẢ NĂNG")
        self.assertTrue(all("Nhận diện" in s["op"] for s in r["supported"]))

    def test_unity_bundle_head_certain(self):
        data = b"UnityFS\x00\x00" + b"\x00" * 200
        r = fp.probe_data(data, "game.unity3d", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unity")
        self.assertEqual(r["engine"]["level"], "CHẮC CHẮN")
        ops = [s["op"] for s in r["supported"]]
        self.assertTrue(any("Trích xuất text" in o for o in ops))

    def test_unity_resources_assets_name_likely(self):
        # resources.assets đơn lẻ: MỘT dấu hiệu tên mạnh (0.3) chưa đủ
        # mức CÓ KHẢ NĂNG theo luật discovery -> probe báo unknown nhưng
        # vẫn hiện ứng viên unity gần nhất (conf 0.3) kèm bằng chứng.
        data = b"\x00" * 64  # SerializedFile thật không bắt đầu bằng UnityFS
        r = fp.probe_data(data, "resources.assets",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        cand = [c for c in r["candidates"] if c["engine"] == "unity"]
        self.assertEqual(len(cand), 1)
        self.assertEqual(cand[0]["confidence"], 0.3)

    def test_unity_two_names_likely(self):
        data = _zip_bytes({
            "Payload/g.app/Data/globalgamemanagers": b"\x00" * 16,
            "Payload/g.app/Data/resources.assets": b"\x00" * 16,
        })
        r = fp.probe_data(data, "game.zip", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unity")
        self.assertEqual(r["engine"]["level"], "CÓ KHẢ NĂNG")


class TestFormatProbeUnknown(unittest.TestCase):
    def test_random_bytes_unknown_no_crash(self):
        r = fp.probe_data(_random_bytes(8192), "mystery.bin",
                          active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        self.assertEqual(r["engine"]["level"], "CHƯA ĐỦ BẰNG CHỨNG")
        self.assertEqual(r["supported"], [])
        self.assertTrue(r["unsupported"])
        self.assertTrue(r["next_steps"])
        self.assertEqual(r["file"]["container_kind"], "unknown")
        self.assertEqual(r["file"]["size"], 8192)
        self.assertEqual(len(r["file"]["sha256"]), 64)
        self.assertFalse(r["file"]["pak_footer"])

    def test_plain_zip_not_a_game_unknown(self):
        data = _zip_bytes({"readme.txt": b"hello world\n",
                           "docs/guide.txt": b"not a game\n"})
        r = fp.probe_data(data, "docs.zip", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        self.assertEqual(r["supported"], [])
        self.assertTrue(r["file"]["container_opened"])
        self.assertEqual(r["entries"]["total"], 2)

    def test_empty_file_unknown(self):
        r = fp.probe_data(b"", "empty.bin", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        self.assertEqual(r["file"]["size"], 0)

    def test_7z_reports_open_error_but_still_unknown(self):
        # 7z chưa có backend: probe ghi open_error rõ ràng, vẫn trả báo cáo.
        data = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 100
        r = fp.probe_data(data, "game.7z", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["engine"]["id"], "unknown")
        self.assertIsNotNone(r["open_error"])
        self.assertIn("7z", r["open_error"]["message"])

    def test_plugin_inactive_downgrades_support(self):
        data = godot_b.build_pck_v2([("res://hello.txt", b"hello pb3")])
        r = fp.probe_data(data, "game.pck", active_plugins=frozenset())
        self.assertEqual(r["engine"]["id"], "godot")
        self.assertEqual(r["supported"], [])
        self.assertTrue(any("godot_pck chưa bật" in u["reason"]
                            for u in r["unsupported"]))
        self.assertFalse(r["plugins_checked"]["godot_pck"])

    def test_report_schema(self):
        data = _zip_bytes({"project.json": b"{}"})
        r = fp.probe_data(data, "x.zip", active_plugins=ALL_PLUGINS)
        self.assertEqual(r["format"], "pb3-format-probe")
        self.assertEqual(r["probe_version"], 1)
        for key in ("file", "engine", "candidates", "entries",
                    "open_error", "supported", "unsupported", "next_steps",
                    "strings", "plugins_checked", "notes"):
            self.assertIn(key, r)
        for key in ("name", "size", "sha256", "head_hex", "container_kind",
                    "magic_head", "pak_footer", "container_opened"):
            self.assertIn(key, r["file"])
        for key in ("id", "label", "level", "confidence", "evidence",
                    "note"):
            self.assertIn(key, r["engine"])


if __name__ == "__main__":
    unittest.main()
