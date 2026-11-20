"""Test giao diện Bear pastel (không dark/light, icon gấu ảnh thật, credit
luôn bật, hoạt ảnh nhẹ) — đọc file web tĩnh + gọi SDK, offline, stdlib-only.

Khóa các quyết định của anh Duy (18:21 +07 19/11, bổ sung 18:25):
- icon gấu là ảnh icon chính 1242x1242 (bear-icon-user.jpg), dùng làm logo
  header + hero + các màn + favicon + manifest (PNG 192/512/180);
- KHÔNG còn dark/light: không nút/theme script/hàm theme/key localStorage;
- MÀU duy nhất: xanh dương bầu trời pastel + hồng nhẹ;
- credit "Việt Hoá Bởi Bear" LUÔN gắn (không còn checkbox tắt);
- hoạt ảnh nhẹ chỉ CSS (transform/opacity), tôn trọng prefers-reduced-motion.
"""
import json
import re
import struct
import unittest
from pathlib import Path

from sdk import translate as tr

ROOT = Path(__file__).resolve().parent.parent.parent
INDEX = ROOT / "index.html"
APPJS = ROOT / "app.js"
MANIFEST = ROOT / "manifest.webmanifest"
SWJS = ROOT / "sw.js"
ICONS = {
    "icons/icon-192.png": (192, 192),
    "icons/icon-512.png": (512, 512),
    "icons/apple-touch-icon.png": (180, 180),
}


def _read(path):
    return path.read_text(encoding="utf-8")


def _png_size(path):
    """Đọc kích thước PNG từ header IHDR (stdlib, không cần PIL)."""
    with open(path, "rb") as f:
        head = f.read(33)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "%s không phải PNG" % path
    assert head[12:16] == b"IHDR", "%s thiếu chunk IHDR" % path
    width, height = struct.unpack(">II", head[16:24])
    return width, height


class TestNoTheme(unittest.TestCase):
    """Không còn bất kỳ dấu vết dark/light nào."""

    def setUp(self):
        self.html = _read(INDEX)
        self.js = _read(APPJS)
        self.manifest = json.loads(_read(MANIFEST))

    def test_no_theme_elements_or_strings(self):
        for bad in ("btn-theme", "setting-theme", "pb3_theme", "data-theme",
                    "THEME_KEY", "currentTheme", "setTheme", "syncThemeUI",
                    "🌙", "☀️", "Chế độ sáng"):
            self.assertNotIn(bad, self.html, "index.html còn %r" % bad)
            self.assertNotIn(bad, self.js, "app.js còn %r" % bad)

    def test_no_dark_palette_values(self):
        for dark in ("#101418", "#1a2129", "#0d1117", "#2a343f", "#232c36",
                     "#303a45", "#0b1e3a", "#f2efe8"):
            self.assertNotIn(dark, self.html, "index.html còn màu tối %s" % dark)

    def test_manifest_colors_pastel(self):
        self.assertEqual(self.manifest["theme_color"], "#dcebf8")
        self.assertEqual(self.manifest["background_color"], "#dcebf8")


class TestPastelPalette(unittest.TestCase):
    """Một palette duy nhất: xanh dương bầu trời pastel + hồng nhẹ."""

    def setUp(self):
        self.html = _read(INDEX)

    def test_root_palette_values(self):
        for var in ("--bg: #dcebf8", "--card: #ffffff", "--border: #c8dcef",
                    "--text: #1d2530", "--sub: #5b6673",
                    "--input-bg: #f2f8fd", "--accent: #2f6fd0",
                    "--pink: #e0789a", "--pink-text: #c24e77",
                    "--green: #1e7a45", "--tab-bg: #eaf3fb",
                    "--hero-bg1: #bcdcf5", "--hero-bg2: #eaf3fb",
                    "--hero-mtn1: #9cc8ec", "--hero-mtn2: #7fb8e8"):
            self.assertIn(var, self.html, "thiếu biến màu %s" % var)

    def test_no_orange_left(self):
        for bad in ("--orange", "u-orange", "#f0883e", "#d96f1e"):
            self.assertNotIn(bad, self.html, "index.html còn %r" % bad)

    def test_pink_buttons_and_blue_active_nav(self):
        sec = re.search(r"button\.secondary \{[^}]*\}", self.html)
        self.assertIsNotNone(sec, "thiếu rule button.secondary")
        self.assertIn("var(--pink)", sec.group(0))
        self.assertIn("var(--pink-text)", sec.group(0))
        nav = re.search(r"\.bottomnav button\.active \{[^}]*\}", self.html)
        self.assertIsNotNone(nav, "thiếu rule .bottomnav button.active")
        self.assertIn("var(--accent)", nav.group(0))

    def test_hero_is_light(self):
        hero = re.search(r"\.hero h2 \{[^}]*\}", self.html)
        self.assertIsNotNone(hero)
        self.assertIn("color: #1d2530", hero.group(0))
        self.assertNotIn("#fff", hero.group(0))
        self.assertIn(".hero h2 .u-pink", self.html)
        self.assertIn('class="u-pink"', self.html)


class TestBearIcon(unittest.TestCase):
    """Icon gấu = ảnh chính của anh Duy, PNG đúng cỡ, dùng ở mọi màn."""

    def test_icon_files_png_and_sizes(self):
        for rel, (w, h) in ICONS.items():
            path = ROOT / rel
            self.assertTrue(path.is_file(), "thiếu %s" % rel)
            self.assertEqual(_png_size(path), (w, h),
                             "%s sai kích thước" % rel)

    def test_header_hero_and_view_heads_use_icon_img(self):
        html = _read(INDEX)
        self.assertIn('<img class="logo-bear" src="icons/icon-192.png"',
                      html, "header chưa dùng <img> icon")
        self.assertEqual(html.count('src="icons/icon-192.png"'), 6,
                         "phải có 6 <img> icon (header + hero + 4 view-head)")
        self.assertIn('<link rel="icon" type="image/png" '
                      'href="icons/icon-192.png">', html,
                      "thiếu link favicon")

    def test_no_inline_mascot_svg_left(self):
        html = _read(INDEX)
        self.assertNotIn("bear-mascot", html)
        self.assertNotIn("<use href=", html)
        self.assertNotIn("logo-bear\" aria-hidden", html)

    def test_manifest_references_icons(self):
        manifest = json.loads(_read(MANIFEST))
        srcs = {icon["src"] for icon in manifest["icons"]}
        self.assertEqual(srcs, set(ICONS.keys()))

    def test_sw_keeps_precache_icons_and_version(self):
        sw = _read(SWJS)
        # Chưa bump version ở PR này (v65 lấy ở lượt gộp).
        self.assertIn('CACHE_VERSION = "v68-20261121"', sw)
        for rel in ICONS:
            self.assertIn(rel, sw, "sw.js precache thiếu %s" % rel)


class TestCreditLocked(unittest.TestCase):
    """Credit luôn bật: không checkbox, mọi đường dịch đều gắn credit."""

    def setUp(self):
        self.html = _read(INDEX)
        self.js = _read(APPJS)

    def test_no_credit_checkbox(self):
        self.assertNotIn("translate-credit", self.html)
        self.assertNotIn("translate-credit", self.js)
        self.assertNotIn("$(\"translate-credit\")", self.js)

    def test_google_job_has_no_credit_param(self):
        self.assertIn("async function runTranslateGoogleJob(job, to) {",
                      self.js)
        self.assertNotIn("runTranslateGoogleJob(job, to, credit)", self.js)
        self.assertIn('if (job.status === "done") {', self.js)
        self.assertNotIn('job.status === "done" && credit', self.js)

    def test_offline_path_passes_true(self):
        self.assertIn('"translate_json", [text, to, true, from',
                      self.js, "đường offline phải truyền credit=true")

    def test_bridge_defaults_credit_true(self):
        bridge = _read(ROOT / "pb3" / "bridge.py")
        self.assertIn('def translate_json(json_text, to="vi", credit=True',
                      bridge)
        self.assertIn('async def translate_json_google(json_text, to="vi", '
                      'credit=True', bridge)

    def test_note_says_credit_always_on(self):
        self.assertIn("luôn được gắn vào bản dịch", self.html)

    def test_default_translate_attaches_credit(self):
        kv = {"title": "9 Kings", "play": "Play"}
        res = tr.translate_table(kv, source_lang="en", target_lang="vi",
                                 provider=tr.FakeProvider())
        self.assertEqual(res.credit_key, "title")
        self.assertIn(tr.CREDIT_TEXT, res.translated["title"])


class TestAnimations(unittest.TestCase):
    """Hoạt ảnh nhẹ chỉ CSS + spinner, tôn trọng prefers-reduced-motion."""

    def setUp(self):
        self.html = _read(INDEX)
        self.js = _read(APPJS)

    def test_keyframes_present(self):
        for name in ("bear-bob", "fade-in", "spin", "pulse-soft"):
            self.assertIn("@keyframes %s" % name, self.html,
                          "thiếu keyframes %s" % name)

    def test_logo_bobs_and_buttons_respond(self):
        logo = re.search(r"\.logo-bear \{[^}]*\}", self.html)
        self.assertIsNotNone(logo)
        self.assertIn("animation: bear-bob", logo.group(0))
        self.assertIn("button:active { transform: scale(.97); }", self.html)
        card = re.search(r"\.card \{[^}]*\}", self.html)
        self.assertIsNotNone(card)
        self.assertIn("animation: fade-in", card.group(0))

    def test_prefers_reduced_motion_disables_all(self):
        block = re.search(
            r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n  \}",
            self.html, re.S)
        self.assertIsNotNone(block, "thiếu media prefers-reduced-motion")
        self.assertIn("animation: none !important", block.group(1))
        self.assertIn("transition: none !important", block.group(1))

    def test_progress_spinner_wired(self):
        self.assertIn('id="progress-spinner"', self.html)
        self.assertIn('class="spinner"', self.html)
        self.assertIn('$("progress-spinner").hidden = false', self.js)
        self.assertIn('$("progress-spinner").hidden = true', self.js)
        self.assertIn('$("progress-bar").classList.add("loading")', self.js)
        self.assertIn('$("progress-bar").classList.remove("loading")',
                      self.js)


if __name__ == "__main__":
    unittest.main()
