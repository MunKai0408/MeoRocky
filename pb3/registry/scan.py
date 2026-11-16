"""Quét tĩnh mã plugin TRƯỚC khi import (không chạy mã). Claude 03, bổ sung isolation (decisions #53).

ĐÂY LÀ LINT, KHÔNG PHẢI BẢO MẬT. Nó chỉ chặn các cách lách "lộ liễu" mà guard runtime (registry/isolation.py) đã
biết là không bịt được: import thẳng `_socket`, `posix`, `ctypes`, `subprocess`, `importlib`...; gọi `eval/exec/
compile/__import__`; chạm `sys.modules`, `__builtins__`, `__subclasses__`...; chuỗi hằng trùng tên module cấm
(bắt `import_module("posix")`). Mã cố tình che giấu (nối chuỗi, mã hóa, getattr động...) vẫn qua được.
Việc nên làm với plugin lạ vẫn là: đọc mã trước khi bỏ vào plugins_user/.

Chỉ áp cho plugin phạm vi user (plugin builtin là mã đi kèm PB3). Loader quét MỌI `.py` trong cây thư mục plugin
(plugin nhiều file, C1, decisions #56), không chỉ file entry.
"""
from __future__ import annotations

import ast
from typing import Dict, List

FORBIDDEN_MODULES = frozenset({
    "socket", "_socket", "ssl", "_ssl", "select", "selectors", "asyncio",
    "urllib", "http", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "socketserver",
    "subprocess", "_posixsubprocess", "pty", "multiprocessing", "_multiprocessing",
    "posix", "nt", "_winapi", "ctypes", "_ctypes", "cffi",
    "importlib", "imp", "builtins", "__builtin__", "runpy", "code", "codeop", "marshal", "gc",
})
# chuỗi hằng chỉ bị bắt nếu là tên module "khó nhầm" (tránh báo nhầm các chữ thường như "code", "select", "http")
STRING_MODULES = frozenset({"socket", "_socket", "_ssl", "subprocess", "_posixsubprocess", "posix", "nt", "_winapi",
                            "ctypes", "_ctypes", "importlib", "builtins", "__builtin__", "_multiprocessing", "runpy"})
# Hằng chuỗi TRẦN (không nằm trong lời gọi import): chỉ bắt tên "khó nhầm" thật
# (gạch dưới đầu hoặc posix/nt) — từ thường như "socket"/"subprocess" làm dữ
# liệu game vẫn bị báo nhầm. Tên thường chỉ bị bắt khi là đối số của
# import_module/__import__/eval/exec (xem _STRING_CALLS).
STRING_MODULES_BARE = frozenset({"_socket", "_ssl", "_posixsubprocess", "posix", "nt", "_winapi",
                                 "_ctypes", "__builtin__", "_multiprocessing"})
_STRING_CALLS = frozenset({"import_module", "__import__", "eval", "exec", "compile"})
FORBIDDEN_OS = frozenset({"system", "popen", "fork", "forkpty", "startfile", "kill", "killpg"})
FORBIDDEN_CALLS = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
FORBIDDEN_ATTRS = frozenset({
    "__builtins__", "__subclasses__", "__globals__", "__loader__", "__spec__", "__code__", "__closure__",
    "f_globals", "f_builtins", "gi_frame", "tb_frame", "co_code",
})
MAX_FINDINGS = 20


def scan_source(source: str) -> List[str]:
    """Trả danh sách vi phạm dạng 'dòng N: lý do'. Rỗng = không thấy gì (KHÔNG có nghĩa là an toàn)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ["dòng %s: lỗi cú pháp" % (e.lineno or "?")]
    out: List[str] = []
    aliases: Dict[str, str] = {}   # bí danh module: {'o': 'os'} từ `import os as o`

    def add(node: ast.AST, why: str) -> None:
        if len(out) < MAX_FINDINGS:
            out.append("dòng %d: %s" % (getattr(node, "lineno", 0), why))

    def _bad_string(value: str) -> bool:
        return value.split(".")[0] in STRING_MODULES and len(value) <= 40 and " " not in value

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    add(n, "import %s" % a.name)
                if a.asname and a.name.split(".")[0] in ("os", "sys"):
                    aliases[a.asname] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            if n.level == 0 and (n.module or "").split(".")[0] in FORBIDDEN_MODULES:
                add(n, "import từ %s" % n.module)
            elif n.level == 0 and (n.module or "").split(".")[0] == "os":
                for a in n.names:   # `from os import system` từng lọt (chỉ kiểm tên module)
                    if a.name in FORBIDDEN_OS or a.name.startswith(("exec", "spawn", "posix_spawn")):
                        add(n, "import os.%s" % a.name)
        elif isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in FORBIDDEN_CALLS:
                add(n, "gọi %s()" % n.func.id)
            elif isinstance(n.func, ast.Attribute) and n.func.attr == "__import__":
                add(n, "gọi .__import__()")
            # đối số chuỗi của import_module/__import__/eval/exec: tên module cấm
            fname = n.func.id if isinstance(n.func, ast.Name) else (
                n.func.attr if isinstance(n.func, ast.Attribute) else "")
            if fname in _STRING_CALLS:
                for arg in n.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _bad_string(arg.value):
                        add(arg, "chuỗi trùng tên module cấm: %r" % arg.value)
        elif isinstance(n, ast.Attribute):
            if n.attr in FORBIDDEN_ATTRS:
                add(n, "truy cập .%s" % n.attr)
            elif n.attr == "modules" and isinstance(n.value, ast.Name) and n.value.id == "sys":
                add(n, "truy cập sys.modules")
            elif isinstance(n.value, ast.Name) and aliases.get(n.value.id, n.value.id) == "os" and (
                    n.attr in FORBIDDEN_OS or n.attr.startswith(("exec", "spawn", "posix_spawn"))):
                add(n, "truy cập os.%s" % n.attr)   # gồm cả bí danh `import os as o; o.system()`
        elif isinstance(n, ast.Name) and n.id in FORBIDDEN_ATTRS:
            add(n, "dùng tên %s" % n.id)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and n.value.split(".")[0] in STRING_MODULES_BARE \
                and len(n.value) <= 40 and " " not in n.value:
            add(n, "chuỗi trùng tên module cấm: %r" % n.value)
    return out
