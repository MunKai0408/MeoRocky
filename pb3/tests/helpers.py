"""Tiện ích test: thư mục tạm + bản sao dự án để chạy subprocess không đụng /root/pb3."""
import atexit
import os
import shutil
import sys
import tempfile
import ast
import unittest
from pathlib import Path

try:
    import pytest
except ImportError:  # iSH: pytest không có sẵn, run_tests.py (unittest) vẫn phải chạy
    class _PytestMarkStub:
        """Bản thay thế tối giản cho pytest.mark khi pytest chưa cài:
        @pytest.mark.needs_fixture(...) thành decorator giữ nguyên hàm
        (marker chỉ có tác dụng khi chạy bằng pytest ở CI/trình duyệt)."""
        def __getattr__(self, _name):
            def _deco(*args, **kwargs):
                def _wrap(fn):
                    return fn
                return _wrap
            return _deco

    class _PytestStub:
        mark = _PytestMarkStub()

    pytest = _PytestStub()  # noqa: F811 - stub chỉ dùng khi thiếu pytest thật

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Thư mục tạm đã tạo nhưng chưa dọn (test bị ngắt giữa chừng thì tearDown
# không chạy): dọn nốt khi tiến trình thoát bình thường/Ctrl+C. Tiến trình bị
# giết (SIGKILL) thì atexit không chạy — run_tests.py dọn nốt lúc khởi động.
_TMP_DIRS = set()


def _cleanup_tmp_dirs():
    for d in list(_TMP_DIRS):
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


atexit.register(_cleanup_tmp_dirs)


def register_tmp_dir(path):
    """Đăng ký thư mục tạm dùng chung (setUpClass) để atexit dọn nốt khi lần
    chạy bị ngắt (tearDownClass không kịp chạy)."""
    _TMP_DIRS.add(str(path))


def unregister_tmp_dir(path):
    _TMP_DIRS.discard(str(path))


class TmpCase(unittest.TestCase):
    """Mỗi test có self.tmp (Path) riêng, tự dọn."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="pb3test-")
        _TMP_DIRS.add(self._td)
        self.tmp = Path(self._td)

        def _rm():
            shutil.rmtree(self._td, ignore_errors=True)
            _TMP_DIRS.discard(self._td)
        self.addCleanup(_rm)


def copy_project(dest: Path) -> Path:
    """Sao chép mã dự án (không .git/legacy/dữ liệu chạy) sang dest/pb3."""
    target = dest / "pb3"
    ignore = shutil.ignore_patterns(".git", "legacy", "__pycache__", "*.pyc", "logs", "workspace",
                                    "reports", "kb", "local.toml")
    shutil.copytree(str(ROOT), str(target), ignore=ignore)
    return target


def can_symlink(tmp: Path) -> bool:
    try:
        a = tmp / "_probe_target"
        a.write_text("x")
        os.symlink(str(a), str(tmp / "_probe_link"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


def subprocess_timeout(base):
    raw = os.environ.get("PB3_TEST_TIMEOUT_SCALE")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return base * v
        except ValueError:
            pass
    return base * (5.0 if os.path.exists("/proc/ish") else 1.0)


# ---- Bộ nhớ đệm cho các test "phân tích mã nguồn" (quan trọng trên iSH: ast chậm ~100x) ----
_SRC, _NODES, _IMPORTS = {}, {}, {}


def read_src(path):
    """Đọc file nguồn một lần cho cả phiên test."""
    k = str(path)
    if k not in _SRC:
        _SRC[k] = Path(k).read_text(encoding="utf-8")
    return _SRC[k]


def ast_nodes(path):
    """Parse + duyệt AST một lần/file; trả list node (dùng chung giữa các test)."""
    k = str(path)
    if k not in _NODES:
        _NODES[k] = list(ast.walk(ast.parse(read_src(k))))
    return _NODES[k]


def imports_of(path):
    """[(tên module, số dòng)] của file, tính một lần."""
    k = str(path)
    if k not in _IMPORTS:
        out = []
        for n in ast_nodes(k):
            if isinstance(n, ast.Import):
                out += [(a.name, n.lineno) for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                out.append((("." * n.level) + (n.module or ""), n.lineno))
        _IMPORTS[k] = out
    return _IMPORTS[k]


# Bỏ qua các test mở tiến trình Python con khi PB3_FAST=1 (trên iSH mỗi lần mở rất chậm).
# Mặc định (không đặt biến) vẫn chạy ĐẦY ĐỦ; chạy đầy đủ trước khi chốt/bàn giao.
slow_subprocess = unittest.skipIf(os.environ.get("PB3_FAST") == "1",
                                  "PB3_FAST=1: bỏ qua test tiến trình con")
