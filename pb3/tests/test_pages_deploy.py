"""Test deploy GitHub Pages (pages.yml staging): mọi file web root mà index.html
dùng (đặc biệt icons/ + manifest.webmanifest) phải nằm trong allowlist staging
của job build-deploy — nếu không Pages không deploy chúng, live 404 và icon
hiện dấu "?" (bug thật 13:33 20/11, v63: icons/ + manifest.webmanifest thiếu
khỏi vòng lặp cp trong pages.yml, live curl 404 trong khi file trong repo
vẫn nguyên).

Khóa: bước staging chỉ copy đúng danh sách tường minh trong pages.yml —
thêm file web root mới thì phải thêm vào allowlist ở đây VÀ vào test này.

Bug 20/11 13:55 (v64): tab Chẩn đoán FAIL "Trích xuất thử (fixture) —
HTTP 404" vì app.js fetch fixtures/selftest.zip nhưng pages.yml không
deploy thư mục fixtures/ — cùng họ allowlist như bug icon gấu v63.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PAGES_YML = ROOT / ".github" / "workflows" / "pages.yml"

# File web root mà index.html/app.js/sw.js dùng trực tiếp (phải được staging).
REQUIRED_FILES = (
    "index.html",
    "app.js",
    "worker.js",
    "sw.js",
    "manifest.webmanifest",
    "README.md",
)
REQUIRED_DIRS = ("fonts", "icons", "fixtures", "pb3", "pyodide")
# File lõi: staging fail nếu thiếu (xem vòng lặp kiểm tra trong pages.yml).
CORE_FILES = ("index.html", "app.js", "worker.js")
REQUIRED_ICONS = (
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/apple-touch-icon.png",
)


def _staging_block(text):
    """Trích đúng khối `run:` của bước staging build-deploy trong pages.yml
    (không lấy nhầm vòng lặp `for f in ...` trong các heredoc Python khác)."""
    m = re.search(
        r"name: Đóng gói file web.*?run: \|(.*?)\n      - name: Upload Pages artifact",
        text, re.S)
    assert m is not None, \
        "pages.yml: không tìm thấy bước staging build-deploy"
    return m.group(1)


def _staging_lists(text):
    """Trích danh sách file + thư mục từ bước staging trong pages.yml.

    Khối staging có 2 vòng lặp `for f in ...` (staging + kiểm tra file lõi):
    lấy danh sách dài nhất (staging). Chỉ có 1 vòng lặp `for d in ...`.
    """
    block = _staging_block(text)
    file_lists = re.findall(r"for f in ([^;]+); do", block)
    dir_lists = re.findall(r"for d in ([^;]+); do", block)
    assert file_lists, "pages.yml: không tìm thấy vòng lặp file staging"
    assert dir_lists, "pages.yml: không tìm thấy vòng lặp thư mục staging"
    files = max(file_lists, key=lambda s: len(s.split())).split()
    dirs = dir_lists[0].split()
    return files, dirs


class TestPagesDeploy(unittest.TestCase):
    def setUp(self):
        self.text = PAGES_YML.read_text(encoding="utf-8")
        self.files, self.dirs = _staging_lists(self.text)

    def test_manifest_and_icons_in_staging(self):
        self.assertIn("manifest.webmanifest", self.files,
                      "pages.yml staging thiếu manifest.webmanifest (live 404)")
        self.assertIn("icons", self.dirs,
                      "pages.yml staging thiếu thư mục icons/ (live 404)")

    def test_required_files_and_dirs_in_staging(self):
        for f in REQUIRED_FILES:
            self.assertIn(f, self.files,
                          "pages.yml staging thiếu file %s" % f)
        for d in REQUIRED_DIRS:
            self.assertIn(d, self.dirs,
                          "pages.yml staging thiếu thư mục %s" % d)

    def test_core_staged_files_exist_on_disk(self):
        # Theo đúng ngữ nghĩa staging: chỉ file lõi (index.html, app.js,
        # worker.js) là fail nếu thiếu; file phase sau (translate.js,
        # file-reader.js, register-sw.js, ...) chỉ cảnh báo khi thiếu.
        for f in CORE_FILES:
            self.assertIn(f, self.files,
                          "pages.yml staging thiếu file lõi %s" % f)
            self.assertTrue((ROOT / f).is_file(),
                            "thiếu file lõi trên đĩa: %s" % f)
        for d in self.dirs:
            self.assertTrue((ROOT / d).is_dir(),
                            "pages.yml staging liệt kê thư mục không có trên đĩa: %s" % d)

    def test_icons_exist_and_are_png(self):
        for rel in REQUIRED_ICONS:
            p = ROOT / rel
            self.assertTrue(p.is_file(), "thiếu icon: %s" % rel)
            self.assertGreater(p.stat().st_size, 0, "%s rỗng" % rel)
            with open(p, "rb") as fh:
                head = fh.read(8)
            self.assertEqual(head, b"\x89PNG\r\n\x1a\n",
                             "%s không phải PNG" % rel)

    def test_manifest_lists_icons(self):
        manifest = json.loads(
            (ROOT / "manifest.webmanifest").read_text(encoding="utf-8"))
        srcs = {icon.get("src") for icon in manifest.get("icons", [])}
        for rel in REQUIRED_ICONS:
            self.assertIn(rel, srcs,
                          "manifest.webmanifest không liệt kê %s" % rel)

    def test_selftest_fixture_exists(self):
        # Tab Chẩn đoán fetch fixtures/selftest.zip (SELFTEST_FIXTURE trong
        # app.js) — file phải có trên đĩa VÀ được staging (bug 20/11: thiếu
        # khỏi allowlist nên live 404, Chẩn đoán FAIL).
        p = ROOT / "fixtures" / "selftest.zip"
        self.assertTrue(p.is_file(), "thiếu fixture tự kiểm tra: %s" % p)
        self.assertGreater(p.stat().st_size, 0, "selftest.zip rỗng")
        with open(p, "rb") as fh:
            head = fh.read(2)
        self.assertEqual(head, b"PK",
                         "selftest.zip không phải file ZIP")


if __name__ == "__main__":
    unittest.main()
