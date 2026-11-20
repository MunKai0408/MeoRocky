"""Test nhận diện engine game cho tab Tổng quan (v64).

sdk.discovery.detect_game_engine: đa tín hiệu, liệt kê bằng chứng từng
tín hiệu, phân biệt Unity IL2CPP / Unity Mono / Unreal / Native iOS,
không đủ tín hiệu -> Unknown (fail-closed).

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

    def test_heaps_pak_head_not_unreal(self):
        r = disc.detect_game_engine(
            ["Payload/DeadCells.app/res.pak"],
            {"Payload/DeadCells.app/res.pak": _heaps_head()})
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("Heaps" in h for h in r["other_hints"]))

    def test_heaps_pak_head_offset64_not_unreal(self):
        # Layout Dead Cells: 64 ký tự hex ASCII rồi mới tới "PAK".
        r = disc.detect_game_engine(
            ["Payload/DeadCells.app/res.pak"],
            {"Payload/DeadCells.app/res.pak": _heaps_head(offset64=True)})
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("Heaps" in h for h in r["other_hints"]))

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

    def test_godot_pck_not_unreal(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Game.pck"],
            {"Payload/Game.app/Game.pck": GDPC_HEAD})
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("Godot" in h for h in r["other_hints"]))

    def test_monogame_dlls_unknown(self):
        # DLL ở gốc .app (không phải Data/Managed) + không có dấu Unity.
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist",
             "Payload/Game.app/MonoGame.Framework.dll",
             "Payload/Game.app/mscorlib.dll"])
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("MonoGame" in h for h in r["other_hints"]))

    def test_love_game_unknown(self):
        r = disc.detect_game_engine(
            ["Payload/Game.app/Info.plist", "Payload/Game.app/game.love"])
        self.assertEqual(r["engine"], "unknown")
        self.assertTrue(any("LÖVE" in h for h in r["other_hints"]))

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

    def test_real_carrion_unknown(self):
        e = self._detect("carrion-1.5.ipa")
        self.assertEqual(e["engine"], "unknown")
        self.assertTrue(any("MonoGame" in h for h in e["other_hints"]))

    def test_real_fortune_mill_unknown(self):
        e = self._detect("fortune-mill-1.0.6.ipa")
        self.assertEqual(e["engine"], "unknown")
        self.assertTrue(any("Godot" in h for h in e["other_hints"]))

    def test_real_balatro_unknown(self):
        e = self._detect("balatro.ipa")
        self.assertEqual(e["engine"], "unknown")
        self.assertTrue(any("LÖVE" in h for h in e["other_hints"]))

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

    def test_real_dead_cells_heaps_head_unknown(self):
        names = ["Payload/DeadCells.app/DeadCells",
                 "Payload/DeadCells.app/Info.plist",
                 "Payload/DeadCells.app/res.pak"]
        e = disc.detect_game_engine(
            names, {"Payload/DeadCells.app/res.pak": _heaps_head()})
        self.assertEqual(e["engine"], "unknown")
        self.assertTrue(any("Heaps" in h for h in e["other_hints"]))


if __name__ == "__main__":
    unittest.main()
