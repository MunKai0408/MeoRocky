"""Test B1+B2 (fix/strings-arbitration): arbitration ưu tiên file của app
chính + Localizable.strings, bplist Localizable được gộp vào merge, nhóm
ASK chọn file .strings của app chính, bảng locale .lproj BCP 47.

Bối cảnh [ĐÃ ĐO THẬT]: trích Tom ở chế độ gộp ra 0 file vì
en.lproj/Localizable.strings của chính Tom (bplist, 64 key) thua tie-break
đường dẫn trước InfoPlist.strings cùng app (ngắn hơn 2 ký tự) rồi bị loại
vì origin "other"; CSJ BULanguage.strings text 0.77 < ngưỡng auto 0.80;
các .lproj bn-BD/gu/kn/... -> locale None -> merge bỏ, non-merge xuất
tên rác đánh số (BGNativeAd.BGNativeAd.2.json...).
"""
import io
import json
import plistlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.app import bootstrap
from sdk import arbitration as arb
from sdk import discovery as disc
from sdk import extraction as ext
from services import language_tool
from tests.helpers import TmpCase, copy_project, register_tmp_dir, unregister_tmp_dir

INFO_PLIST = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
              b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
              b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
              b'<plist version="1.0">\n<dict>\n'
              b'<key>CFBundleIdentifier</key><string>com.example.game</string>\n'
              b'<key>CFBundleName</key><string>Game</string>\n'
              b'</dict>\n</plist>\n')


def _strings_text(entries):
    return "".join('"%s" = "%s";\n' % (k, v) for k, v in entries).encode("utf-8")


def _strings_bplist(entries):
    return plistlib.dumps(dict(entries), fmt=plistlib.FMT_BINARY)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


def _same_signals():
    # Tín hiệu giống hệt nhau -> điểm giống hệt nhau (hòa tuyệt đối),
    # chỉ tie-break mới phân định thắng thua.
    return arb.Signals(magic_match=0.9, ext_match=1.0, name_signal=1.0,
                       schema_fit=0.86, locale_evidence=0.9, manifest_ref=0.0,
                       content_lang_fit=1.0, kb_agreement=0.0)


class TestMainAppPath(unittest.TestCase):
    def test_direct_in_app_is_main(self):
        self.assertTrue(arb.is_main_app_path(
            "Payload/Game.app/en.lproj/Localizable.strings"))
        self.assertTrue(arb.is_main_app_path(
            "Payload/Game.app/Info.plist"))
        self.assertTrue(arb.is_main_app_path(
            "Payload/MyTalkingTom-row.app/en.lproj/Localizable.strings"))

    def test_nested_bundles_are_not_main(self):
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/Frameworks/AdSDK.framework/en.lproj/Localizable.strings"))
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/BigoADSRes.bundle/de.lproj/BGNativeAd.strings"))
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/PlugIns/Stickers.appex/Info.plist"))
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/PlugIns/Stickers/Info.plist"))
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/Frameworks/X.framework/Y.xctest/Z.strings"))

    def test_no_app_is_neutral(self):
        # Đường dẫn không có .app (file Unity, đường dẫn tương đối):
        # False cho tất cả -> tie-break rơi về các mức sau như cũ.
        self.assertFalse(arb.is_main_app_path("Data/level.unity3d"))
        self.assertFalse(arb.is_main_app_path("localization/en.json"))
        self.assertFalse(arb.is_main_app_path(""))

    def test_backslash_and_case(self):
        self.assertTrue(arb.is_main_app_path(
            "Payload\\Game.APP\\en.lproj\\Localizable.strings"))
        self.assertFalse(arb.is_main_app_path(
            "Payload/Game.app/Frameworks/X.FRAMEWORK/a.strings"))


class TestStringsBasenameRank(unittest.TestCase):
    def test_ranks(self):
        self.assertEqual(arb.strings_basename_rank("x/Localizable.strings"), 0)
        self.assertEqual(arb.strings_basename_rank("x/InfoPlist.strings"), 2)
        self.assertEqual(arb.strings_basename_rank("x/BGNativeAd.strings"), 1)
        self.assertEqual(arb.strings_basename_rank("x/en.json"), 1)

    def test_case_insensitive(self):
        self.assertEqual(arb.strings_basename_rank("x/LOCALIZABLE.STRINGS"), 0)
        self.assertEqual(arb.strings_basename_rank("x/infoplist.strings"), 2)


class TestArbitrateTieBreak(unittest.TestCase):
    def _contenders(self, paths):
        return [arb.Contender("apple_strings", p, _same_signals(),
                              maturity="M3", priority=10) for p in paths]

    def test_app_localizable_wins_tie(self):
        # Ba ứng viên hòa điểm tuyệt đối như nhóm "en" của Tom:
        # app InfoPlist (đường dẫn NGẮN nhất) phải thua app Localizable
        # và framework Localizable — tie-break cũ (độ dài) chọn sai.
        paths = [
            "Payload/Game.app/en.lproj/InfoPlist.strings",
            "Payload/Game.app/Frameworks/AdSDK.framework/en.lproj/Localizable.strings",
            "Payload/Game.app/en.lproj/Localizable.strings",
        ]
        d = arb.arbitrate(self._contenders(paths))
        self.assertEqual(d.status, arb.AUTO)
        self.assertEqual(d.chosen.path,
                         "Payload/Game.app/en.lproj/Localizable.strings")

    def test_app_beats_framework_same_basename(self):
        paths = [
            "Payload/Game.app/Frameworks/AdSDK.framework/en.lproj/Localizable.strings",
            "Payload/Game.app/en.lproj/Localizable.strings",
        ]
        d = arb.arbitrate(self._contenders(paths))
        self.assertEqual(d.chosen.path,
                         "Payload/Game.app/en.lproj/Localizable.strings")
        self.assertIn("app chính", d.rejected[0].reason)

    def test_localizable_beats_infoplist_same_app(self):
        paths = [
            "Payload/Game.app/en.lproj/InfoPlist.strings",
            "Payload/Game.app/en.lproj/Localizable.strings",
        ]
        d = arb.arbitrate(self._contenders(paths))
        self.assertEqual(d.chosen.path,
                         "Payload/Game.app/en.lproj/Localizable.strings")
        self.assertIn("InfoPlist", d.rejected[0].reason)

    def test_path_length_still_breaks_remaining_ties(self):
        # Hai file cùng là app chính + cùng basename: đường dẫn ngắn
        # hơn vẫn thắng (hành vi cũ được giữ cho trường hợp còn lại).
        paths = [
            "Payload/Game.app/SubDir/en.lproj/Localizable.strings",
            "Payload/Game.app/en.lproj/Localizable.strings",
        ]
        d = arb.arbitrate(self._contenders(paths))
        self.assertEqual(d.chosen.path,
                         "Payload/Game.app/en.lproj/Localizable.strings")

    def test_score_difference_still_wins(self):
        # Tie-break mới chỉ tác động khi hòa điểm tuyệt đối: điểm cao
        # hơn vẫn thắng dù là file framework.
        good = arb.Contender("apple_strings",
                             "Payload/Game.app/Frameworks/AdSDK.framework/en.lproj/Localizable.strings",
                             arb.Signals(magic_match=1.0, ext_match=1.0, name_signal=1.0,
                                         schema_fit=1.0, locale_evidence=1.0,
                                         manifest_ref=1.0, content_lang_fit=1.0,
                                         kb_agreement=1.0),
                             maturity="M3", priority=10)
        weak = arb.Contender("apple_strings",
                             "Payload/Game.app/en.lproj/Localizable.strings",
                             _same_signals(), maturity="M3", priority=10)
        d = arb.arbitrate([weak, good])
        self.assertEqual(d.chosen.path, good.path)


class TestOriginKindBplistSplit(unittest.TestCase):
    def test_localizable_bplist_is_text_asset(self):
        fn = ext._origin_kind_for
        self.assertEqual(fn("Payload/Game.app/en.lproj/Localizable.strings",
                            None, "apple_strings", flavor="strings-bplist"),
                         "text_asset")
        self.assertEqual(fn("Payload/Game.app/en.lproj/Localizable.strings",
                            None, "apple_strings", flavor="strings-xml-plist"),
                         "text_asset")

    def test_infoplist_bplist_stays_other(self):
        # Chuẩn 9K: InfoPlist.strings có 1 key/locale, gộp vào sẽ vỡ
        # en.json 2252 key byte-identical -> vẫn "other".
        fn = ext._origin_kind_for
        self.assertEqual(fn("Payload/Game.app/en.lproj/InfoPlist.strings",
                            None, "apple_strings", flavor="strings-bplist"),
                         "other")
        self.assertEqual(fn("Payload/Game.app/en.lproj/InfoPlist.strings",
                            None, "apple_strings", flavor="strings-xml-plist"),
                         "other")

    def test_other_bplist_basenames_stay_other(self):
        # Bảng bplist tên SDK/vendor: text quảng cáo, không lọt vào
        # file locale của game.
        fn = ext._origin_kind_for
        for base in ("BGNativeAd.strings", "HyBidLocalizable.strings",
                     "BULanguage.strings", "a.strings"):
            self.assertEqual(fn("x/" + base, None, "apple_strings",
                                flavor="strings-bplist"), "other")

    def test_text_strings_unchanged(self):
        fn = ext._origin_kind_for
        self.assertEqual(fn("a.strings", None, "apple_strings",
                            flavor="strings"), "text_asset")
        self.assertEqual(fn("a.strings", None, "apple_strings"),
                         "text_asset")
        self.assertEqual(fn("a.plist", None, "apple_plist"), "other")
        self.assertEqual(fn("a", None, "generic_strings"), "generic_flat")


class TestDetectLocaleBcp47(unittest.TestCase):
    def test_new_lproj_codes(self):
        cases = {
            "x/bn-BD.lproj/y.strings": "bn",
            "x/bn-IN.lproj/y.strings": "bn",
            "x/gu.lproj/y.strings": "gu",
            "x/kn.lproj/y.strings": "kn",
            "x/mr.lproj/y.strings": "mr",
            "x/ne.lproj/y.strings": "ne",
            "x/pa.lproj/y.strings": "pa",
            "x/si.lproj/y.strings": "si",
            "x/ta.lproj/y.strings": "ta",
            "x/te.lproj/y.strings": "te",
            "x/ur.lproj/y.strings": "ur",
            "x/uz.lproj/y.strings": "uz",
            "x/az.lproj/y.strings": "az",
            "x/hy.lproj/y.strings": "hy",
            "x/kk.lproj/y.strings": "kk",
            "x/ky.lproj/y.strings": "ky",
            "x/lv.lproj/y.strings": "lv",
            "x/sr.lproj/y.strings": "sr",
            "x/ca.lproj/y.strings": "ca",
            "x/hr.lproj/y.strings": "hr",
            "x/iw.lproj/y.strings": "he",
        }
        for path, want in cases.items():
            code, _c, _e = disc.detect_locale(path, None)
            self.assertEqual(code, want, path)

    def test_existing_codes_unchanged(self):
        self.assertEqual(disc.detect_locale("x/en.lproj/y.strings", None)[0], "en")
        self.assertEqual(disc.detect_locale("x/zh-Hans.lproj/y.strings", None)[0], "zh-Hans")
        self.assertEqual(disc.detect_locale("x/zh-Hant.lproj/y.strings", None)[0], "zh-Hant")
        self.assertEqual(disc.detect_locale("x/pt-BR.lproj/y.strings", None)[0], "pt")
        self.assertEqual(disc.detect_locale("x/fr.lproj/y.strings", None)[0], "fr")
        self.assertIsNone(disc.detect_locale("x/Base.lproj/y.strings", None)[0])
        self.assertIsNone(disc.detect_locale("x/NoLproj/y.strings", None)[0])

    def test_short_codes_do_not_false_match(self):
        # Mã 2 ký tự mới không được khớp nhầm trong từ dài hơn.
        self.assertIsNone(disc.detect_locale("data/test/file.json", None)[0])
        self.assertIsNone(disc.detect_locale("game/data/level.json", None)[0])
        self.assertIsNone(disc.detect_locale("res/music/song.mp3", None)[0])
        # fr_CA vẫn là fr (ca chỉ thắng khi đứng độc lập trong .lproj).
        self.assertEqual(disc.detect_locale("x/fr_CA.lproj/y.strings", None)[0], "fr")


class SyntheticIpaCase(TmpCase):
    _shared_proj = None
    _shared_td = None

    @classmethod
    def setUpClass(cls):
        cls._shared_td = tempfile.mkdtemp(prefix="pb3-strings-arb-")
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

    def run_ext(self, source, **kw):
        kw.setdefault("registry", self.reg)
        kw.setdefault("tempdir", str(self.app.paths.temp))
        kw.setdefault("root", str(self._shared_proj))
        kw.setdefault("out_dir", str(self.out))
        return ext.run_extraction(ext.ExtractionRequest(source=source, **kw))

    def extract_ipa_bytes(self, entries, **kw):
        p = self.tmp / "game.ipa"
        p.write_bytes(_zip_bytes(entries))
        return self.run_ext(str(p), **kw)

    def read_out_json(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))


class TestSyntheticIpaArbitration(SyntheticIpaCase):
    APP = "Payload/Game.app/en.lproj/Localizable.strings"
    APP_INFO = "Payload/Game.app/en.lproj/InfoPlist.strings"
    SDK = ("Payload/Game.app/Frameworks/AdSDK.framework/en.lproj/"
           "Localizable.strings")

    def test_bplist_tie_app_localizable_wins_merge(self):
        # Cả 3 file bplist hòa 0.8048 như nhóm "en" của Tom: app
        # Localizable (64 key thật) phải thắng app InfoPlist (1 key vỏ
        # app) và framework Localizable (text SDK) -> en.json có key game.
        r = self.extract_ipa_bytes({
            "Payload/Game.app/Info.plist": INFO_PLIST,
            self.APP: _strings_bplist([("GAME_KEY", "Game Value")]),
            self.APP_INFO: _strings_bplist(
                [("NSCameraUsageDescription", "Needs camera")]),
            self.SDK: _strings_bplist([("SDK_KEY", "SDK Value")]),
        }, locales={"en"}, merge_one_file=True)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.read_out_json("en.json"),
                         {"GAME_KEY": "Game Value"})

    def test_ask_text_app_strings_wins_merge(self):
        # Cả 2 file text hòa 0.7676 (< ngưỡng auto 0.80) như CSJ: nhóm
        # ASK nhưng ứng viên đầu là .strings của app chính -> chọn luôn,
        # không hạ ngưỡng chung, không đổi điểm.
        r = self.extract_ipa_bytes({
            "Payload/Game.app/Info.plist": INFO_PLIST,
            self.APP: _strings_text([("APP_KEY", "App One")]),
            self.SDK: _strings_text([("SDK_KEY", "SDK One")]),
        }, locales={"en"}, merge_one_file=True)
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(self.read_out_json("en.json"),
                         {"APP_KEY": "App One"})

    def test_non_merge_lproj_locale_names_not_numbered(self):
        # B2: BGNativeAd ở bn-BD/gu .lproj trước đây locale None -> xuất
        # BGNativeAd.BGNativeAd.2.json...; nay xuất theo tên locale.
        r = self.extract_ipa_bytes({
            "Payload/Game.app/Info.plist": INFO_PLIST,
            "Payload/Game.app/BigoADSRes.bundle/bn-BD.lproj/BGNativeAd.strings":
                _strings_bplist([("AdKey", "Ad BD")]),
            "Payload/Game.app/BigoADSRes.bundle/gu.lproj/BGNativeAd.strings":
                _strings_bplist([("AdKey", "Ad GU")]),
        }, locales={"bn", "gu"}, merge_one_file=False)
        self.assertTrue(r.ok, r.errors)
        exported = sorted(f.exported for f in r.files if f.exported)
        self.assertIn("Bengali.json", exported)
        self.assertIn("Gujarati.json", exported)
        self.assertFalse([n for n in exported if ".2.json" in n or ".3.json" in n])


if __name__ == "__main__":
    unittest.main()
