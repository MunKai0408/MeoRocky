"""Luật kiến trúc/iSH-only kiểm bằng phân tích mã nguồn (không chạy mã)."""
import ast
import re
import unittest
from pathlib import Path

from tests.helpers import ROOT, ast_nodes, imports_of, read_src

ACTIVE = [ROOT / "menu.py"] + sorted((ROOT / "core").glob("*.py")) + sorted((ROOT / "ui").glob("*.py")) \
    + sorted((ROOT / "sdk").glob("*.py")) + sorted((ROOT / "registry").glob("*.py")) + sorted((ROOT / "services").glob("*.py"))
ALL_PY = [p for p in ROOT.rglob("*.py") if "legacy" not in p.parts and ".git" not in p.parts]
DOCS = [p for p in list(ROOT.glob("*.txt")) + list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md"))
        + list((ROOT / "handoff").glob("*.md"))]

NETWORK = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib",
           "xmlrpc", "requests", "httpx", "aiohttp", "websocket", "websockets", "paramiko", "asyncio",
           "socketserver", "wsgiref"}
# Ngoại lệ mạng DUY NHẤT (decision #151): sdk/translate.py được import urllib
# (chỉ urllib) để gọi Google Translate theo lệnh của người dùng; mọi file
# khác vẫn cấm mạng, và chỉ host translate.googleapis.com được phép.
NETWORK_ALLOWED = {"sdk/translate.py": {"urllib"}}
URL_ALLOWED_HOST = {"sdk/translate.py": "https://translate.googleapis.com"}
# lý do hợp lệ duy nhất để gọi tiến trình con: thử import module trong tiến trình riêng (ish_compat)
SUBPROCESS_ALLOWED = {"core/ish_compat.py"}


def imports(path):
    return imports_of(path)


def rel(p):
    return str(p.relative_to(ROOT)).replace("\\", "/")


class TestIshOnlyRules(unittest.TestCase):
    def test_active_files_found(self):
        self.assertGreaterEqual(len(ACTIVE), 15)

    def test_no_network_modules(self):
        for p in ACTIVE:
            allowed = NETWORK_ALLOWED.get(rel(p), set())
            for name, ln in imports(p):
                top = name.split(".")[0]
                if top in allowed:
                    continue
                self.assertNotIn(top, NETWORK, "%s:%d import %s" % (rel(p), ln, name))

    def test_no_urls_or_downloads_in_code(self):
        for p in ACTIVE:
            txt = read_src(p)
            urls = re.findall(r"https?://[A-Za-z0-9.-]+", txt)
            allowed_host = URL_ALLOWED_HOST.get(rel(p))
            if allowed_host is not None:
                # Ngoại lệ duy nhất (decision #151): chỉ host này, đúng một chỗ.
                self.assertEqual(urls, [allowed_host], rel(p))
            else:
                self.assertEqual(urls, [], rel(p))
            self.assertIsNone(re.search(r"pip install|apk add .*&&|curl |wget ", txt.replace("apk add python3", "")), rel(p))

    def test_subprocess_only_where_allowed(self):
        for p in ACTIVE:
            uses = any(n.split(".")[0] == "subprocess" for n, _ in imports(p)) or "os.system" in read_src(p)
            if uses:
                self.assertIn(rel(p), SUBPROCESS_ALLOWED, rel(p))

    def test_no_dynamic_code_execution(self):
        for p in ACTIVE:
            for n in ast_nodes(p):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec"):
                    self.fail("%s:%d dùng %s" % (rel(p), n.lineno, n.func.id))

    def test_no_hardcoded_external_paths(self):
        for p in ACTIVE:
            txt = read_src(p)
            for bad in ("/mnt/", "/Users/", "/storage/", "/sdcard", "/home/"):
                self.assertFalse(bad in txt, "%s chứa %s" % (rel(p), bad))
            if rel(p) not in ("core/paths.py", "menu.py", "core/ish_compat.py"):   # chỉ để cảnh báo/hướng dẫn
                self.assertFalse("/root/pb3" in txt, rel(p))

    def test_no_game_specific_branches_or_forbidden_scope(self):
        words = ["Space " + "Marshals", "sm2_json", "I2" + "Languages", "an" + "droid", "datapack"]
        apk = re.compile(r"\bapk\b(?! (add|update|upgrade|info|del|search)\b)")   # lệnh quản lý gói Alpine: hợp lệ
        for p in ACTIVE + DOCS:
            if p.name in ("INVENTORY.md", "account_01.md", "decisions.md", "CHANGELOG.txt"):
                continue          # tài liệu khảo sát được phép NÊU tên mã cũ để giải thích
            txt = read_src(p).lower()
            for w in words:
                self.assertFalse(w.lower() in txt, "%s chứa %r" % (rel(p), w))
            self.assertIsNone(apk.search(txt), "%s nhắc tới APK" % rel(p))

    def test_dependency_rule_core_does_not_import_upper_layers(self):
        for p in sorted((ROOT / "core").glob("*.py")):
            for name, ln in imports(p):
                top = name.lstrip(".").split(".")[0]
                self.assertNotIn(top, {"ui", "services", "registry", "plugins", "plugins_user", "sdk", "archive",
                                       "unity", "language", "modding", "kb", "batch"},
                                 "%s:%d core import ngược lên %s" % (rel(p), ln, name))

    def test_menu_py_has_no_parse_or_patch_logic(self):
        txt = (ROOT / "menu.py").read_text(encoding="utf-8")
        for w in ("struct", "zipfile", "lz4", "UnityPy", "json", "sqlite3", "plistlib"):
            self.assertNotIn("import " + w, txt)

    def test_no_heavy_import_at_startup(self):
        """UnityPy import mất ~1 phút trên iSH: tuyệt đối không import khi khởi động."""
        for p in ACTIVE:
            for name, ln in imports(p):
                self.assertNotIn(name.split(".")[0], {"UnityPy", "lz4"}, "%s:%d" % (rel(p), ln))


OLD_DIR = "/mnt/" + "ios"        # đường dẫn cũ: không được xuất hiện lại trong tài liệu/mã


class TestProjectDirDecision(unittest.TestCase):
    """Quyết định #40: thư mục dự án chuẩn là /root/pb3 trong tài liệu; mã vẫn không ghi cứng nó.

    (Việc cấm đường dẫn ngoài dự án trong mã đang dùng nằm ở test_no_hardcoded_external_paths: không hạ.)
    """
    CURRENT_DOCS = ["README.txt", "CURRENT_STATE.md", "PROGRESS.md",
                    "handoff/account_03.md", "handoff/TASKS_CLAUDE03.md"]

    def test_current_docs_use_new_dir_and_no_old_cd_command(self):
        for name in self.CURRENT_DOCS:
            txt = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("/root/pb3", txt, name)
            self.assertNotIn(OLD_DIR, txt, name)

    def test_decision_recorded_with_limits(self):
        txt = (ROOT / "handoff" / "decisions.md").read_text(encoding="utf-8")
        self.assertIn("40. **Thư mục dự án chuẩn là `/root/pb3`.**", txt)
        self.assertNotIn(OLD_DIR, txt)

    def test_code_still_derives_root_from_file_location(self):
        txt = (ROOT / "core" / "paths.py").read_text(encoding="utf-8")
        self.assertIn("__file__", txt)
        self.assertNotIn(OLD_DIR, txt)


class TestPythonCompat(unittest.TestCase):
    """iSH thường có Python 3.9 (bằng chứng: .pyc cpython-39 trong mã cũ); mục tiêu >= 3.8."""

    def test_grammar_is_python38(self):
        for p in ALL_PY:
            src = read_src(p)
            try:
                ast.parse(src, filename=str(p), feature_version=(3, 8))
            except SyntaxError as e:
                self.fail("%s không hợp lệ ở Python 3.8: %s" % (rel(p), e))

    def test_no_newer_stdlib_apis(self):
        pats = [r"\.removeprefix\(", r"\.removesuffix\(", r"is_relative_to\(", r"functools\.cache\b",
                r"\bzip\([^)]*strict=", r"\.bit_count\(", r"math\.lcm", r"\bmatch\s+\w+:\s*$",
                r"importlib\.resources\.files", r"\|=\s*\{", r"str \| None", r"int \| None",
                r"dataclass\([^)]*(slots|kw_only)=", r"tomllib\.load",
                # X10 mở rộng (3.9+): dict union, union type tổng quát, module/hàm mới hơn 3.8
                r"\}\s*\|\s*\{", r"[A-Za-z_][\w.]* \| None", r"\bzoneinfo\b", r"\bgraphlib\b",
                r"asyncio\.to_thread", r"\bTaskGroup\b", r"\bExceptionGroup\b", r"except\s*\*",
                r"sys\.exception\(\)"]
        for p in ACTIVE:
            txt = read_src(p)
            for pat in pats:
                for m in re.finditer(pat, txt, re.M):
                    line = txt[:m.start()].count("\n") + 1
                    # tomllib chỉ được dùng sau khi guard bằng try/except ImportError
                    if "tomllib" in pat and "except ImportError" in txt:
                        continue
                    self.fail("%s:%d dùng API mới hơn 3.8: %s" % (rel(p), line, pat))

    def test_future_annotations_in_core_and_ui(self):
        for p in ACTIVE:
            if p.name == "menu.py" or p.name == "__init__.py":
                continue
            self.assertIn("from __future__ import annotations", read_src(p), rel(p))

    def test_all_text_files_are_utf8(self):
        for p in ALL_PY + DOCS + [ROOT / "config" / "default.toml"]:
            p.read_bytes().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
