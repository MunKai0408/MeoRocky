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

    def test_error_message_lists_bundle(self):
        """Thông báo lỗi sai định dạng phải liệt kê đủ đuôi, gồm .bundle.

        Bug 21/11 (v69): gameExtError() dựng nửa đầu từ GAME_EXTS.join()
        (đủ .bundle) nhưng nửa sau liệt kê cứng ".ipa/.zip/..." thiếu
        .bundle. Khóa: nửa sau phải dựng động từ cùng GAME_EXTS +
        GZIP_EXT, không còn chuỗi liệt kê cứng nào.
        """
        appjs = APP_JS.read_text(encoding="utf-8")
        m = re.search(r"function gameExtError\(name\) \{(.*?)\n\}",
                      appjs, re.S)
        assert m is not None, "app.js: không tìm thấy gameExtError"
        # Bỏ comment dòng (ghi chú lịch sử có nhắc chuỗi cứng cũ).
        body = re.sub(r"//[^\n]*", "", m.group(1))
        self.assertNotIn(".ipa/.zip", body,
                         "gameExtError còn liệt kê cứng đuôi file "
                         "(dễ lệch với GAME_EXTS như vụ thiếu .bundle)")
        self.assertIn("GAME_EXTS", body,
                      "gameExtError không dựng đuôi từ GAME_EXTS")
        self.assertIn("GZIP_EXT", body,
                      "gameExtError không dựng đuôi từ GZIP_EXT")
        # Mô phỏng đúng logic JS: concat + join("/") phải chứa .bundle.
        exts = _game_exts(appjs)
        gz = re.search(r"GZIP_EXT = \"([^\"]+)\"", appjs).group(1)
        tail = "/".join(list(exts) + [gz])
        self.assertIn(".bundle", tail,
                      "đuôi liệt kê từ GAME_EXTS + GZIP_EXT thiếu '.bundle'")


if __name__ == "__main__":
    unittest.main()
