"""Test định dạng file game mà tool chấp nhận (GAME_EXTS trong app.js).

Bug 20/11 13:55 (v64): tool từ chối file .bundle ở mọi cửa nạp dù khung
thả file ghi ".bundle..." — GAME_EXTS thiếu ".bundle" trong khi bundle
Unity (.bundle) cùng định dạng UnityFS như .unity3d và parser đọc được.
Khóa: ".bundle" phải có trong GAME_EXTS, và regex kiểm tra trong
exp/verify_phase1_ci.mjs phải khớp đúng mảng đó (harness này từng kiểm
tra đúng từng ký tự mảng, nên sửa mảng mà quên sửa regex là CI đỏ).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
APP_JS = ROOT / "app.js"
VERIFY_MJS = ROOT / "exp" / "verify_phase1_ci.mjs"

EXPECTED_EXTS = (
    ".ipa",
    ".zip",
    ".unity3d",
    ".bundle",
    ".assets",
    ".ttf",
    ".json",
    ".tar.xz",
)


def _game_exts(text):
    m = re.search(r"GAME_EXTS = \[([^\]]+)\]", text)
    assert m is not None, "app.js: không tìm thấy GAME_EXTS"
    return re.findall(r'"([^"]+)"', m.group(1))


class TestGameExts(unittest.TestCase):
    def test_bundle_accepted(self):
        exts = _game_exts(APP_JS.read_text(encoding="utf-8"))
        self.assertIn(".bundle", exts,
                      "GAME_EXTS thiếu '.bundle' (tool từ chối file .bundle)")
        self.assertEqual(tuple(exts), EXPECTED_EXTS,
                         "GAME_EXTS khác danh sách kỳ vọng: %s" % (exts,))

    def test_verify_harness_matches_app(self):
        appjs = APP_JS.read_text(encoding="utf-8")
        mjs = VERIFY_MJS.read_text(encoding="utf-8")
        m = re.search(r"js_ext_check: /(GAME_EXTS = [^/]+)/", mjs)
        assert m is not None, "verify_phase1_ci.mjs: không tìm thấy js_ext_check"
        pattern = m.group(1)
        self.assertTrue(re.search(pattern, appjs),
                        "regex js_ext_check trong verify_phase1_ci.mjs "
                        "không khớp GAME_EXTS trong app.js (sửa mảng thì "
                        "phải sửa regex theo)")


if __name__ == "__main__":
    unittest.main()
