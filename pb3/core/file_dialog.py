"""Chọn file/thư mục bằng văn bản (không cần giao diện đồ họa), có nhập tay đường dẫn.

Thiết kế cho màn hình iPhone: danh sách đánh số, phân trang, dòng <= width.
input_fn/print_fn được tiêm để kiểm thử. Trả None khi người dùng hủy (q) hoặc hết input.
Không đọc nội dung file, chỉ liệt kê tên + kích thước.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .progress import fmt_bytes, truncate_display

PAGE = 12


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int


def list_dir_entries(directory, *, show_hidden: bool = False, max_items: int = 500) -> List[Entry]:
    # SẮP XẾP TRƯỚC RỒI MỚI CẮT: cắt trước rồi xếp thì trang đầu chỉ toàn
    # tên đầu bảng chữ cái theo thứ tự scandir (không xác định).
    out: List[Entry] = []
    try:
        for e in os.scandir(str(directory)):
            if not show_hidden and e.name.startswith("."):
                continue
            try:
                is_dir = e.is_dir()
                size = 0 if is_dir else e.stat().st_size
            except OSError:
                continue
            out.append(Entry(e.name, is_dir, size))
    except OSError:
        return []
    out.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    return out[:max_items]


def choose_path(start_dir, *, prompt: str = "Chọn", input_fn: Optional[Callable[[str], str]] = None,
                print_fn: Callable[[str], None] = print, width: int = 44,
                allow_dir: bool = True, allow_file: bool = True) -> Optional[Path]:
    # input_fn=None -> lấy input() TẠI LÚC GỌI (mặc định input ở chữ ký sẽ
    # đóng băng builtins.input lúc import, làm test vá input bị qua mặt).
    ask = input_fn if input_fn is not None else input
    cur = Path(start_dir)
    page = 0
    while True:
        items = list_dir_entries(cur)
        pages = max(1, (len(items) + PAGE - 1) // PAGE)
        page = min(page, pages - 1)
        print_fn(truncate_display("== " + str(cur), width))
        chunk = items[page * PAGE:(page + 1) * PAGE]
        if not items:
            print_fn("(thư mục trống hoặc không đọc được)")
        for i, e in enumerate(chunk, page * PAGE + 1):
            tail = "/" if e.is_dir else " " + fmt_bytes(e.size)
            print_fn(truncate_display("%2d. %s%s" % (i, e.name, tail), width))
        print_fn("[số]=mở/chọn  ..=lên  .=chọn thư mục này")
        print_fn("n/p=trang %d/%d  hoặc gõ đường dẫn  q=hủy" % (page + 1, pages))
        try:
            s = ask(prompt + ": ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if s in ("q", "Q"):
            return None
        if s == "n":
            page = min(page + 1, pages - 1)
            continue
        if s == "p":
            page = max(page - 1, 0)
            continue
        if s == "..":
            cur = cur.parent
            page = 0
            continue
        if s == ".":
            if allow_dir:
                return cur
            print_fn("Cần chọn một tệp, không phải thư mục.")
            continue
        if s.isdigit():
            idx = int(s) - 1
            if 0 <= idx < len(items):
                e = items[idx]
                target = cur / e.name
                if e.is_dir:
                    cur, page = target, 0
                    continue
                if allow_file:
                    return target
                print_fn("Cần chọn thư mục.")
                continue
            print_fn("Số không hợp lệ.")
            continue
        if s:
            p = Path(os.path.expanduser(s))
            if not p.is_absolute():
                p = cur / p
            if p.is_dir() and allow_dir:
                return p
            if p.is_file() and allow_file:
                return p
            print_fn("Không tìm thấy hoặc không đúng loại: " + truncate_display(s, width - 30))
