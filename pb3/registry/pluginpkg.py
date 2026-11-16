"""Nạp plugin NHIỀU FILE như một package riêng (Claude 03, C1, decisions #56).

Quy tắc:
- Mỗi thư mục plugin = một package tên duy nhất `pb3_plugin_<scope>_<thư_mục>` (không đụng nhau, không đụng
  module của PB3). KHÔNG thêm thư mục plugin vào `sys.path`.
- File trong plugin import nhau bằng import TƯƠNG ĐỐI: `from .helper import X`, `from .lib import util`.
  `import helper` (tuyệt đối) sẽ lỗi; loader báo gợi ý rõ ràng.
- Chỉ nạp được file nguồn `.py` nằm trong thư mục plugin. KHÔNG nạp: `.pyc` đứng một mình, extension `.so/.pyd`,
  namespace package. Thư mục con muốn là package phải có `__init__.py`.
- Bộ nạp BỎ QUA bộ nhớ đệm bytecode (không đọc, không ghi `__pycache__`) để mã chạy luôn đúng là mã đã quét tĩnh
  (một `.pyc` cài sẵn không qua mặt được quét) và không ghi rác vào thư mục plugin (trên iSH).
- `__init__.py` ở GỐC thư mục plugin KHÔNG được chạy (package gốc dựng sẵn rỗng); nhưng vẫn bị quét tĩnh.
- Mọi `.py` trong cây thư mục plugin (kể cả file không import) bị quét tĩnh với plugin user. Giới hạn kích thước:
  MAX_FILES file, độ sâu MAX_DEPTH, tổng MAX_TOTAL_BYTES. Liên kết tượng trưng (symlink) bị từ chối.

Đây vẫn là lint/ràng buộc nạp, KHÔNG phải sandbox (xem registry/isolation.py, decisions #42, #53).
Giới hạn đã biết: plugin này vẫn `import pb3_plugin_<scope>_<dir_khác>.x` được nếu đoán đúng tên package
của plugin khác (tên không bí mật). Test khóa giới hạn này.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import types
from typing import Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.paths import is_within

MODULE_PREFIX = "pb3_plugin_"
MAX_FILES = 64
MAX_DEPTH = 4
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024

_ROOTS: Dict[str, str] = {}          # tên package -> realpath thư mục plugin


class _NoCacheLoader(importlib.machinery.SourceFileLoader):
    """Luôn biên dịch từ nguồn: không dùng/không ghi bytecode đệm."""

    def get_code(self, fullname):
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)

    def set_data(self, path, data, *, _mode=0o666):      # chặn mọi ghi (kể cả .pyc)
        return None


def _find_file(root: str, rel: List[str]) -> Optional[Tuple[str, bool]]:
    """(đường dẫn file .py, là_package) cho module `rel` dưới `root`; None nếu không có/không an toàn."""
    base = os.path.join(root, *rel)
    cur = root
    for comp in rel:                                     # không đi qua symlink
        cur = os.path.join(cur, comp)
        if os.path.islink(cur) or os.path.islink(cur + ".py"):
            return None
    init = os.path.join(base, "__init__.py")
    if os.path.isdir(base) and os.path.isfile(init):
        cand, is_pkg = init, True
    elif os.path.isfile(base + ".py"):
        cand, is_pkg = base + ".py", False
    else:
        return None
    if not is_within(root, cand):
        return None
    return cand, is_pkg


class _PluginFinder(importlib.abc.MetaPathFinder):
    """Chỉ phục vụ tên bắt đầu bằng một package plugin đã đăng ký; thiếu thì BÁO LỖI ngay (không để các finder
    khác tìm .pyc/.so)."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(MODULE_PREFIX):
            return None
        for pkg, root in list(_ROOTS.items()):
            if fullname == pkg:
                return None                              # package gốc luôn có sẵn trong sys.modules
            if fullname.startswith(pkg + "."):
                rel = fullname[len(pkg) + 1:].split(".")
                found = _find_file(root, rel)
                if found is None:
                    raise ModuleNotFoundError("No module named %r" % fullname, name=fullname)
                file, is_pkg = found
                loader = _NoCacheLoader(fullname, file)
                return importlib.util.spec_from_file_location(
                    fullname, file, loader=loader,
                    submodule_search_locations=[os.path.dirname(file)] if is_pkg else None)
        return None


def _ensure_finder() -> None:
    if not any(isinstance(f, _PluginFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _PluginFinder())


def package_name(scope: str, dirname: str, plugin_dir: str) -> str:
    """Tên package duy nhất. Thư mục khác nhau nhưng cùng tên sau khi làm sạch -> thêm hậu tố băm."""
    safe = "".join(c if c.isalnum() else "_" for c in "%s_%s" % (scope, dirname))
    name = MODULE_PREFIX + safe
    real = os.path.realpath(plugin_dir)
    if name in _ROOTS and _ROOTS[name] != real:
        name += "_" + hashlib.sha1(real.encode("utf-8", "replace")).hexdigest()[:8]
    return name


def remove_package(pkg: str) -> None:
    for k in [k for k in sys.modules if k == pkg or k.startswith(pkg + ".")]:
        sys.modules.pop(k, None)
    _ROOTS.pop(pkg, None)


def install_package(pkg: str, plugin_dir: str) -> types.ModuleType:
    """Dựng package gốc rỗng cho plugin (xóa bản cũ cùng tên trước: nạp lại luôn lấy mã mới)."""
    remove_package(pkg)
    _ensure_finder()
    _ROOTS[pkg] = os.path.realpath(plugin_dir)
    spec = importlib.machinery.ModuleSpec(pkg, None, is_package=True)
    spec.submodule_search_locations = []
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = []                                     # type: ignore[attr-defined]  # PathFinder không tìm được gì
    sys.modules[pkg] = mod
    return mod


def sibling_names(plugin_dir: str) -> List[str]:
    """Tên module cấp cao có thể import tương đối (file .py và thư mục có __init__.py)."""
    out = []
    try:
        for n in os.listdir(plugin_dir):
            p = os.path.join(plugin_dir, n)
            if n.endswith(".py") and not n.startswith("__") and os.path.isfile(p):
                out.append(n[:-3])
            elif os.path.isdir(p) and os.path.isfile(os.path.join(p, "__init__.py")) and not n.startswith((".", "_")):
                out.append(n)
    except OSError:
        pass
    return sorted(out)


def list_sources(plugin_dir: str) -> List[Tuple[str, str]]:
    """[(đường dẫn tương đối dùng '/', đường dẫn thật)] của mọi .py trong cây plugin.
    Vượt giới hạn hoặc gặp symlink -> PB3Error (E_TOO_LARGE / E_PLUGIN_FORBIDDEN)."""
    out: List[Tuple[str, str]] = []
    total = 0
    stack: List[Tuple[str, int]] = [(plugin_dir, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            names = sorted(os.listdir(d))
        except OSError as e:
            raise PB3Error("E_PLUGIN_INVALID", "không đọc được thư mục plugin (%s)" % type(e).__name__)
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, plugin_dir).replace(os.sep, "/")
            if n == "__pycache__" or n.startswith("."):
                continue
            if os.path.islink(p):
                raise PB3Error("E_PLUGIN_FORBIDDEN", "plugin chứa liên kết tượng trưng (symlink): %s" % rel)
            if os.path.isdir(p):
                if depth + 1 > MAX_DEPTH:
                    raise PB3Error("E_TOO_LARGE", "thư mục plugin lồng quá %d cấp: %s" % (MAX_DEPTH, rel))
                stack.append((p, depth + 1))
            elif n.endswith(".py") and os.path.isfile(p):
                try:
                    size = os.path.getsize(p)
                except OSError:
                    raise PB3Error("E_PLUGIN_INVALID", "không đọc được %s" % rel)
                if size > MAX_FILE_BYTES:
                    raise PB3Error("E_TOO_LARGE", "file plugin quá lớn: %s (%d > %d byte)" % (rel, size, MAX_FILE_BYTES))
                total += size
                out.append((rel, p))
                if len(out) > MAX_FILES:
                    raise PB3Error("E_TOO_LARGE", "plugin có hơn %d file .py" % MAX_FILES)
                if total > MAX_TOTAL_BYTES:
                    raise PB3Error("E_TOO_LARGE", "tổng mã plugin vượt %d byte" % MAX_TOTAL_BYTES)
    out.sort()
    return out
