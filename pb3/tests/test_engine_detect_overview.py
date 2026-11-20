"""Test nhận diện engine game cho tab Tổng quan (v65 + mở rộng v67).

sdk.discovery.detect_game_engine: đa tín hiệu, liệt kê bằng chứng từng
tín hiệu, phân biệt Unity IL2CPP / Unity Mono / Unreal / Native iOS /
Heaps / Godot / MonoGame / LÖVE / Cocos2d-x, không đủ tín hiệu ->
Unknown (fail-closed).

- Fixture tổng hợp: chạy mọi nơi, không cần file thật.
- Game thật local (~/workspace/test-ipas/): skip khi thiếu IPA (CI không
  có file) — chạy qua bridge.detect_game_engine (đúng đường UI dùng).
  Dead Cells: file IPA local đã mất (chỉ còn .tmp 0 byte) nên dùng tên
  entry ghi lại từ bản đo 17/11 (measure.json) + head Heaps tổng hợp.
"""
import json
import struct
import unittest
from pathlib import Path

from sdk import discovery as disc

TEST_IPAS = Path.home() / "workspace" / "test-ipas"

IL2CPP_HEAD = bytes.fromhex("af1bb1fa")
LOCRES_HEAD = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")
PAK_FOOTER = b"\xe1\x12\x6f\x5a"  # 0x5A6F12E1 little-endian
MACHO_HEAD = bytes.fromhex("cffaedfe")
GDPC_HEAD = b"GDPC"
XNB_HEAD = b"XNB" + b"i" + bytes([5])
LOVE_HEAD = b"PK\x03\x04"


def _heaps_head(offset64=False):
    """Đầu file Heaps PAK hợp lệ: magic PAK + version 1 + header 12."""
    hdr = b"PAK" + bytes([1]) + struct.pack("<II", 12, 0)
    if offset64:
        return b"ab" * 32 + hdr + b"\x00" * 116
    return hdr + b"\x00" * 116


class FixtureCase(unittest.TestCase):
    def test_il2cpp_magic_certain(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat"],
            {"Payload/Game.app/Data/Managed/Metadata/global-metadata.dat":
             IL2CPP_HEAD})
        self.assertEqual(r["engine"], "unity-il2cpp")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)
        self.assertTrue(any("0xFAB11BAF" in e["reason"]
                            for e in r["evidence"]))

    def test_il2cpp_names_only_certain_with_two_markers(self):
        # Không có head để đọc magic, nhưng tên global-metadata.dat +
        # UnityFramework đã đủ CHẮC CHẮN (0.5 + 0.4 = 0.9).
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat",
             "Payload/Game.app/Data/globalgamemanagers"])
        self.assertEqual(r["engine"], "unity-il2cpp")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_il2cpp_gmd_name_only_likely(self):
        # Chỉ tên global-metadata.dat: 0.5 (tên file) + 0.2 (đường dẫn
        # Data/Managed trong chính tên đó) = 0.7 -> CÓ KHẢ NĂNG.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Data/Managed/Metadata/global-metadata.dat"])
        self.assertEqual(r["engine"], "unity-il2cpp")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_unity_without_gmd_or_dll_unknown(self):
        # Có dấu Unity nhưng KHÔNG có global-metadata.dat (IL2CPP) và
        # KHÔNG có DLL Managed (Mono) -> không dám kết luận loại nào.
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/resources.assets",
             "Payload/Game.app/Data/level0"])
        self.assertEqual(r["engine"], "unknown")
        self.assertEqual(r["confidence"], 0.0)

    def test_mono_managed_dll_certain(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/globalgamemanagers",
             "Payload/Game.app/Data/Managed/Assembly-CSharp.dll",
             "Payload/Game.app/Data/Managed/mscorlib.dll"])
        self.assertEqual(r["engine"], "unity-mono")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_mono_libmono_only_likely(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Frameworks/libmono.dylib"])
        self.assertEqual(r["engine"], "unity-mono")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_mono_ignored_when_il2cpp_present(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat",
             "Payload/Game.app/Data/Managed/Assembly-CSharp.dll"],
            {"Payload/Game.app/Data/Managed/Metadata/global-metadata.dat":
             IL2CPP_HEAD})
        self.assertEqual(r["engine"], "unity-il2cpp")

    def test_unreal_locres_magic_certain(self):
        r = disc.detect_game_engine(
            ["Game/Content/Localization/Game/en/Game.locres"],
            {"Game/Content/Localization/Game/en/Game.locres": LOCRES_HEAD})
        self.assertEqual(r["engine"], "unreal")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)

    def test_unreal_pak_footer_certain(self):
        r = disc.detect_game_engine(
            ["Game/Content/Paks/Game.pak"],
            {},
            {"Game/Content/Paks/Game.pak": b"\x00" * 100 + PAK_FOOTER})
        self.assertEqual(r["engine"], "unreal")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_unreal_uproject_only_likely(self):
        r = disc.detect_game_engine(["Game/Game.uproject"])
        self.assertEqual(r["engine"], "unreal")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_unreal_iostore_pair_certain(self):
        r = disc.detect_game_engine(
            ["Game/Content/Paks/global.utoc", "Game/Content/Paks/global.ucas"])
        self.assertEqual(r["engine"], "unreal")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_unreal_pak_name_only_unknown(self):
        # 1 file .pak riêng lẻ KHÔNG chứng minh Unreal (Heaps cũng dùng).
        r = disc.detect_game_engine(["Payload/Game.app/res.pak"])
        self.assertEqual(r["engine"], "unknown")

    def test_pak_names_do_not_add_up(self):
        # 5 file .pak (kiểu Dead Cells) vẫn chỉ tính 1 lần: 0.30.
        names = ["Payload/DeadCells.app/res%d.pak" % i for i in range(5)]
        r = disc.detect_game_engine(names)
        self.assertEqual(r["engine"], "unknown")
        cand = {c["engine"]: c for c in r["candidates"]}
        self.assertIn("unreal", cand)
        self.assertEqual(cand["unreal"]["confidence"], 0.3)

    def test_heaps_pak_head_certain(self):
        # v67: .pak có magic Heaps -> Heaps CHẮC CHẮN, không phải Unreal.
        r = disc.detect_game_engine(
            ["Payload/DeadCells.app/res.pak"],
            {"Payload/DeadCells.app/res.pak": _heaps_head()})
        self.assertEqual(r["engine"], "heaps")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)
        self.assertNotEqual(r["engine"], "unreal")

    def test_heaps_pak_head_offset64_certain(self):
        # Layout Dead Cells: 64 ký tự hex ASCII rồi mới tới "PAK".
        r = disc.detect_game_engine(
            ["Payload/DeadCells.app/res.pak"],
            {"Payload/DeadCells.app/res.pak": _heaps_head(offset64=True)})
        self.assertEqual(r["engine"], "heaps")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)

    def test_native_main_storyboard_certain(self):
        r = disc.detect_game_engine(
            ["Payload/App.app/Info.plist", "Payload/App.app/App",
             "Payload/App.app/Main.storyboardc/Info.plist"],
            {"Payload/App.app/App": MACHO_HEAD})
        self.assertEqual(r["engine"], "native-ios")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_native_weak_likely_without_storyboard(self):
        r = disc.detect_game_engine(
            ["Payload/App.app/Info.plist", "Payload/App.app/App"],
            {"Payload/App.app/App": MACHO_HEAD})
        self.assertEqual(r["engine"], "native-ios")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_native_launchscreen_only_not_enough(self):
        # Chỉ có LaunchScreen.storyboardc (mẫu Xcode, game Unity cũng có)
        # + cấu trúc .app, KHÔNG có Mach-O -> chưa đủ bằng chứng.
        r = disc.detect_game_engine(
            ["Payload/App.app/Info.plist",
             "Payload/App.app/LaunchScreen.storyboardc/Info.plist"])
        self.assertEqual(r["engine"], "unknown")

    def test_native_blocked_by_data_tree(self):
        # Kiểu DST: .app + Mach-O nhưng có cây data/ lớn -> Unknown.
        names = ["Payload/Game.app/Info.plist", "Payload/Game.app/Game"]
        names += ["Payload/Game.app/data/f%d.tex" % i for i in range(150)]
        r = disc.detect_game_engine(
            names, {"Payload/Game.app/Game": MACHO_HEAD})
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("data/" in h for h in r["other_hints"]))

    def test_native_blocked_by_unity_markers(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist", "Payload/Game.app/Game",
             "Payload/Game.app/Main.storyboardc/Info.plist",
             "Payload/Game.app/UnityFramework",
             "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat"],
            {"Payload/Game.app/Game": MACHO_HEAD,
             "Payload/Game.app/Data/Managed/Metadata/global-metadata.dat":
             IL2CPP_HEAD})
        self.assertEqual(r["engine"], "unity-il2cpp")

    def test_godot_pck_magic_certain(self):
        # v67: .pck có magic GDPC -> Godot CHẮC CHẮN, không phải Unreal.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Game.pck"],
            {"Payload/Game.app/Game.pck": GDPC_HEAD})
        self.assertEqual(r["engine"], "godot")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)

    def test_monogame_dll_likely(self):
        # v67: DLL ở gốc .app (không phải Data/Managed) + không có dấu
        # Unity -> MonoGame CÓ KHẢ NĂNG (chỉ tên DLL, 0.7).
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist",
             "Payload/Game.app/MonoGame.Framework.dll",
             "Payload/Game.app/mscorlib.dll"])
        self.assertEqual(r["engine"], "monogame")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_love_game_likely(self):
        # v67: game.love chỉ tên (không đọc được magic) -> CÓ KHẢ NĂNG.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist", "Payload/Game.app/game.love"])
        self.assertEqual(r["engine"], "love")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_empty_unknown(self):
        r = disc.detect_game_engine([])
        self.assertEqual(r["engine"], "unknown")
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["level"], "CHƯA ĐỦ BẰNG CHỨNG")

    def test_single_assets_file_unknown(self):
        r = disc.detect_game_engine(["resources.assets"])
        self.assertEqual(r["engine"], "unknown")

    def test_conflicting_signals_unknown(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Data/Managed/Metadata/global-metadata.dat",
             "Game/Content/Localization/Game/en/Game.locres"],
            {"Payload/Game.app/Data/Managed/Metadata/global-metadata.dat":
             IL2CPP_HEAD,
             "Game/Content/Localization/Game/en/Game.locres": LOCRES_HEAD})
        self.assertEqual(r["engine"], "unknown")
        self.assertIn("Mâu thuẫn", r["reason"])

    # --- v67: 5 engine mới ---

    def test_heaps_data_cdb_only_likely(self):
        # data.cdb (CastleDB) một mình: 0.5 -> CÓ KHẢ NĂNG.
        r = disc.detect_game_engine(
            ["Payload/DeadCells.app/res4.pak",
             "Payload/DeadCells.app/data.cdb"])
        self.assertEqual(r["engine"], "heaps")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.5)

    def test_godot_project_file_certain(self):
        # project.godot là file dự án chỉ Godot mới có (0.7) + tên chứa
        # "godot" (0.5) -> 1.2, CHẮC CHẮN.
        r = disc.detect_game_engine(["Game/project.godot"])
        self.assertEqual(r["engine"], "godot")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_godot_framework_name_only_likely(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Frameworks/GodotIap.framework/GodotIap"])
        self.assertEqual(r["engine"], "godot")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.5)

    def test_godot_pck_name_only_unknown(self):
        # .pck chỉ tên (không đọc được magic): 0.3 -> chưa đủ.
        r = disc.detect_game_engine(["Payload/Game.app/Game.pck"])
        self.assertEqual(r["engine"], "unknown")

    def test_godot_beats_native(self):
        # Game Godot iOS cũng có Info.plist + Mach-O, nhưng dấu Godot
        # chặn mức Native (native_blocked) -> Godot thắng.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist", "Payload/Game.app/Game",
             "Payload/Game.app/Game.pck"],
            {"Payload/Game.app/Game": MACHO_HEAD,
             "Payload/Game.app/Game.pck": GDPC_HEAD})
        self.assertEqual(r["engine"], "godot")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_monogame_xnb_magic_only_likely(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Content/Effects/BasicEffect.xnb"],
            {"Payload/Game.app/Content/Effects/BasicEffect.xnb": XNB_HEAD})
        self.assertEqual(r["engine"], "monogame")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.6)

    def test_monogame_dll_plus_xnb_certain(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/MonoGame.Framework.dll",
             "Payload/Game.app/Content/Effects/BasicEffect.xnb"],
            {"Payload/Game.app/Content/Effects/BasicEffect.xnb": XNB_HEAD})
        self.assertEqual(r["engine"], "monogame")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)

    def test_love_zip_magic_certain(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/game.love"],
            {"Payload/Game.app/game.love": LOVE_HEAD})
        self.assertEqual(r["engine"], "love")
        self.assertEqual(r["level"], "CHẮC CHẮN")
        self.assertEqual(r["confidence"], 1.0)

    def test_love_main_lua_only_likely(self):
        r = disc.detect_game_engine(["Payload/Game.app/main.lua"])
        self.assertEqual(r["engine"], "love")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.5)

    def test_love_beats_native(self):
        # Balatro: Info.plist + Mach-O + game.love -> LÖVE thắng.
        r = disc.detect_game_engine(
            ["Payload/Balatro.app/Info.plist", "Payload/Balatro.app/Balatro",
             "Payload/Balatro.app/game.love"],
            {"Payload/Balatro.app/Balatro": MACHO_HEAD,
             "Payload/Balatro.app/game.love": LOVE_HEAD})
        self.assertEqual(r["engine"], "love")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_cocos_libcocos_likely(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Frameworks/libcocos2d iOS.a"])
        self.assertEqual(r["engine"], "cocos")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.7)

    def test_cocos_ccb_only_likely(self):
        r = disc.detect_game_engine(["Game/Resources/MainScene.ccb"])
        self.assertEqual(r["engine"], "cocos")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.5)

    def test_cocos_project_plus_mainjs_likely(self):
        r = disc.detect_game_engine(
            ["Game/project.json", "Game/main.js"])
        self.assertEqual(r["engine"], "cocos")
        self.assertEqual(r["level"], "CÓ KHẢ NĂNG")
        self.assertEqual(r["confidence"], 0.6)

    def test_cocos_project_alone_unknown(self):
        r = disc.detect_game_engine(["Game/project.json"])
        self.assertEqual(r["engine"], "unknown")

    def test_cocos_mainjs_alone_unknown(self):
        # main.js một mình KHÔNG tính cho Cocos (quá chung).
        r = disc.detect_game_engine(["Game/main.js"])
        self.assertEqual(r["engine"], "unknown")

    def test_cocos_many_names_certain(self):
        # Nhiều dấu tên cộng lại (libcocos2d + .ccb + project.json +
        # main.js) thì CHẮC CHẮN — cùng quy tắc với Unreal .utoc+.ucas.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Frameworks/libcocos2d iOS.a",
             "Game/Resources/MainScene.ccb",
             "Game/project.json", "Game/main.js"])
        self.assertEqual(r["engine"], "cocos")
        self.assertEqual(r["level"], "CHẮC CHẮN")

    def test_heaps_unreal_conflict_unknown(self):
        # Vừa magic Heaps vừa magic Unreal -> mâu thuẫn -> Unknown.
        r = disc.detect_game_engine(
            ["Payload/Game.app/res.pak",
             "Game/Content/Localization/Game/en/Game.locres"],
            {"Payload/Game.app/res.pak": _heaps_head(),
             "Game/Content/Localization/Game/en/Game.locres": LOCRES_HEAD})
        self.assertEqual(r["engine"], "unknown")
        self.assertIn("Mâu thuẫn", r["reason"])


class RealGameCase(unittest.TestCase):
    """Game thật local qua bridge.detect_game_engine (đúng đường UI)."""

    def _detect(self, ipa_name):
        import bridge

        ipa = TEST_IPAS / ipa_name
        if not ipa.is_file():
            self.skipTest("thieu IPA that: %s" % ipa)
        out = json.loads(bridge.detect_game_engine(str(ipa)))
        self.assertTrue(out["ok"], out)
        return out["engine"]

    def test_real_9k_il2cpp(self):
        e = self._detect("9kings-v1.0.6.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_ek2_il2cpp(self):
        e = self._detect("ek2-v0.0.18.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_pb3_il2cpp(self):
        e = self._detect("pb3-v1.1.7.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_tom_il2cpp(self):
        e = self._detect("My_Talking_Tom_v26.5.1_Decrypted.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_pvz_il2cpp(self):
        e = self._detect("pvz-fusion.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_kusa_il2cpp(self):
        e = self._detect("re-xue-xing-cun-zhe-1.0.3.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_knightica_il2cpp(self):
        e = self._detect("dou-zhen-qi-shi-1.0.3.ipa")
        self.assertEqual(e["engine"], "unity-il2cpp")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_dst_unknown(self):
        e = self._detect("dont-starve-together-2.1.0-vh.ipa")
        self.assertEqual(e["engine"], "unknown")
        self.assertNotEqual(e["engine"], "unity-il2cpp")

    def test_real_carrion_monogame(self):
        # v67: MonoGame.Framework.dll + magic XNB -> MonoGame CHẮC CHẮN.
        e = self._detect("carrion-1.5.ipa")
        self.assertEqual(e["engine"], "monogame")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_fortune_mill_godot(self):
        # v67: .pck magic GDPC -> Godot CHẮC CHẮN.
        e = self._detect("fortune-mill-1.0.6.ipa")
        self.assertEqual(e["engine"], "godot")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_balatro_love(self):
        # v67: game.love là ZIP hợp lệ -> LÖVE CHẮC CHẮN.
        e = self._detect("balatro.ipa")
        self.assertEqual(e["engine"], "love")
        self.assertEqual(e["level"], "CHẮC CHẮN")

    def test_real_dead_cells_recorded_names_unknown(self):
        # Tên entry ghi lại từ bản đo 17/11 (file IPA local đã mất).
        names = ["Payload/DeadCells.app/DeadCells",
                 "Payload/DeadCells.app/Info.plist",
                 "Payload/DeadCells.app/PkgInfo",
                 "Payload/DeadCells.app/Assets.car",
                 "Payload/DeadCells.app/res.pak",
                 "Payload/DeadCells.app/res1.pak",
                 "Payload/DeadCells.app/res2.pak",
                 "Payload/DeadCells.app/res3.pak",
                 "Payload/DeadCells.app/res4.pak"]
        e = disc.detect_game_engine(names)
        self.assertEqual(e["engine"], "unknown")
        self.assertNotEqual(e["engine"], "unreal")

    def test_real_dead_cells_heaps_head_certain(self):
        # v67: .pak có magic Heaps -> Heaps CHẮC CHẮN.
        names = ["Payload/DeadCells.app/DeadCells",
                 "Payload/DeadCells.app/Info.plist",
                 "Payload/DeadCells.app/res.pak"]
        e = disc.detect_game_engine(
            names, {"Payload/DeadCells.app/res.pak": _heaps_head()})
        self.assertEqual(e["engine"], "heaps")
        self.assertEqual(e["level"], "CHẮC CHẮN")


if __name__ == "__main__":
    unittest.main()
