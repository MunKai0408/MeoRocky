"""Bảng màu + helper bố cục dùng chung cho MỌI bề mặt người dùng (menu, CLI,
services, thanh tiến trình, bộ chạy test). Một chỗ duy nhất định nghĩa mã ANSI;
không rải mã escape ở nơi khác (test meta kiểm tra).

Quy tắc (decisions #82, #83):
- Màu CHỈ khi được bật. Thứ tự ưu tiên: NO_COLOR (có mặt, không rỗng, theo
  no-color.org) luôn tắt; ui.color=off tắt; ui.color=auto (mặc định) chỉ bật
  khi stdout là TTY thật; ui.color=on bật ép kể cả khi pipe (chỉ dùng khi xem
  qua bộ lọc giữ mã ANSI, vd `less -R`).
- Khi tắt, mọi helper trả lại chuỗi gốc KHÔNG đổi byte nào -> output thuần khi
  pipe/log/test giữ nguyên y hệt. Log file và file báo cáo không bao giờ qua
  module này.
- Màu vừa phải, nhất quán: PASS xanh, FAIL đỏ, SKIP vàng, cảnh báo vàng,
  lỗi đỏ đậm, tiêu đề mục cyan.
- Python 3.9: chỉ stdlib, không `match`, không `tomllib`.

Cách dùng: `from core.color import paint, status, title` (dùng bảng mặc định,
tự phát hiện TTY/NO_COLOR), hoặc `Color(mode=..., force=...)` riêng cho test.
Điểm vào (menu.py, run_tests.py) gọi `configure_from_config(cfg)` một lần khi
khởi động để áp dụng ui.color; các nơi khác chỉ gọi helper.
"""
from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

RESET = "\x1b[0m"
_CODES = {
    "pass": "\x1b[32m",      # xanh lá
    "ok": "\x1b[32m",
    "fail": "\x1b[31m",      # đỏ
    "error": "\x1b[1;31m",   # đỏ đậm
    "skip": "\x1b[33m",      # vàng
    "warn": "\x1b[33m",
    "title": "\x1b[36m",     # cyan
    "info": "\x1b[36m",
    "dim": "\x1b[2m",
    "bold": "\x1b[1m",
}
_SYMBOLS = {
    "pass": "✓",
    "ok": "✓",
    "fail": "✗",
    "error": "✗",
    "skip": "↷",
    "warn": "!",
    "info": "•",
}
MODES = ("auto", "on", "off")


def no_color_set() -> bool:
    """NO_COLOR có mặt và không rỗng (chuẩn no-color.org) -> tắt màu."""
    return bool(os.environ.get("NO_COLOR"))


def is_tty(stream=None) -> bool:
    """stdout (hoặc stream cho trước) có phải terminal thật không."""
    try:
        target = stream if stream is not None else sys.stdout
        return bool(target.isatty())
    except (AttributeError, ValueError):
        return False


def term_width() -> int:
    """Chiều rộng terminal cho màn hình điện thoại: 30..44 cột.
    Một chỗ duy nhất (X6); ui/screens.py dùng lại hàm này."""
    return max(30, min(44, shutil.get_terminal_size((48, 24)).columns - 2))


# Cache east_asian_width cho ký tự >= U+1100 (CJK/emoji lặp nhiều).
_EAW_CACHE: dict = {}


def _char_width(ch: str) -> int:
    """Độ rộng hiển thị của 1 ký tự (2 nếu CJK/fullwidth, còn lại 1).

    Tương đương `2 if east_asian_width(ch) in ("W", "F") else 1` nhưng rẻ hơn:
    mọi ký tự có mã < U+1100 đều không bao giờ là W/F (dải W/F nhỏ nhất bắt
    đầu ở U+1100 Hangul Jamo) nên trả 1 ngay không cần tra bảng; chỉ ký tự
    >= U+1100 mới gọi east_asian_width (có cache vì ký tự lặp nhiều)."""
    o = ord(ch)
    if o < 0x1100:
        return 1
    w = _EAW_CACHE.get(ch)
    if w is None:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if len(_EAW_CACHE) < 4096:
            _EAW_CACHE[ch] = w
    return w


def display_width(text: str) -> int:
    """Độ rộng hiển thị (ký tự CJK/emoji tính 2 cột, mã ANSI tính 0).

    Kết quả y hệt bản cũ (vòng lặp từng ký tự + east_asian_width), chỉ nhanh
    hơn: chuỗi ASCII thuần (không mã ANSI) thì độ rộng = số ký tự; chuỗi
    không ASCII thì cộng dồn qua _char_width thay vì gọi east_asian_width
    cho từng ký tự."""
    if "\x1b" not in text:
        if text.isascii():
            return len(text)
        return sum(map(_char_width, text))
    w = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = text.find("m", i + 2)
            if j != -1:
                i = j + 1
                continue
        w += _char_width(ch)
        i += 1
    return w


@lru_cache(maxsize=8192)
def _word_width(word: str) -> int:
    """Độ rộng hiển thị của 1 từ không mã ANSI, có cache.

    Từ lặp rất nhiều trong output plan (cùng đường dẫn/kind/object-type lặp
    hàng nghìn lần trong 24k dòng) nên tính 1 lần rồi dùng lại; kết quả y
    hệt display_width (hàm thuần, không trạng thái). Chỉ dùng cho từ không
    mã ANSI (nhánh nhanh của wrap_text)."""
    return display_width(word)


def strip_ansi(text: str) -> str:
    """Bỏ mọi mã escape ANSI (dùng trong test để so sánh output thuần)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = text.find("m", i + 2)
            if j != -1:
                i = j + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def wrap_text(text: str, width: int) -> List[str]:
    """Ngắt dòng theo độ rộng hiển thị (không cắt giữa từ nếu được; từ dài hơn
    width thì cắt cứng). Dùng cho output vừa màn hình điện thoại.

    Kết quả y hệt bản cũ: độ rộng hiển thị có tính cộng dồn theo ký tự nên
    cộng dồn độ rộng từng từ cho cùng một điểm ngắt; chỉ khi dòng chứa mã
    ANSI (độ rộng không cộng dồn chắc chắn do escape có thể dở dang) mới
    dùng lại vòng lặp cũ gọi display_width trên cả ứng viên."""
    if width <= 0:
        return [text]
    lines: List[str] = []
    for raw in text.split("\n"):
        if "\x1b" in raw:
            _wrap_slow(raw, width, lines)
            continue
        line = ""
        line_w = 0
        for word in raw.split(" "):
            # nhánh nhanh này chỉ chạy khi raw không chứa mã ANSI, nên từ
            # ASCII thuần có độ rộng = số ký tự; từ không ASCII thì dùng
            # _word_width (có cache — cùng từ lặp hàng nghìn lần trong plan).
            ww = len(word) if word.isascii() else _word_width(word)
            cand_w = ww if not line else line_w + 1 + ww
            if cand_w <= width:
                line = word if not line else line + " " + word
                line_w = cand_w
            else:
                if line:
                    lines.append(line)
                while ww > width:
                    cut, w = "", 0
                    for ch in word:
                        cw = _char_width(ch)
                        if w + cw > width:
                            break
                        cut += ch
                        w += cw
                    if not cut:
                        # ký tự đầu đã rộng hơn width (vd width=1 gặp CJK):
                        # nuốt 1 ký tự để tiến triển thay vì lặp vô hạn.
                        cut = word[:1]
                    lines.append(cut)
                    word = word[len(cut):]
                    ww = _word_width(word)
                line = word
                line_w = ww
        lines.append(line)
    return lines


def _wrap_slow(raw: str, width: int, lines: List[str]) -> None:
    """Vòng lặp ngắt dòng gốc (gọi display_width trên cả ứng viên mỗi từ):
    chỉ dùng cho dòng chứa mã ANSI để giữ y hệt hành vi cũ."""
    line = ""
    for word in raw.split(" "):
        cand = word if not line else line + " " + word
        if display_width(cand) <= width:
            line = cand
        else:
            if line:
                lines.append(line)
            while display_width(word) > width:
                cut, w = "", 0
                for ch in word:
                    cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
                    if w + cw > width:
                        break
                    cut += ch
                    w += cw
                if not cut:
                    cut = word[:1]
                lines.append(cut)
                word = word[len(cut):]
            line = word
    lines.append(line)


def truncate_text(text: str, width: int) -> str:
    """Cắt chuỗi theo độ rộng hiển thị, thêm '…' (giữ đầu + cuối khi dài)."""
    if display_width(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    head = width - 1
    out, w = "", 0
    for ch in text:
        cw = _char_width(ch)
        if w + cw > head:
            break
        out += ch
        w += cw
    return out + "…"


class Color:
    """Một bảng màu + trạng thái bật/tắt. `force=True/False` ép trạng thái
    (dùng trong test); mặc định tự phát hiện theo mode + TTY + NO_COLOR."""

    def __init__(self, mode: str = "auto", stream=None, force: Optional[bool] = None):
        if mode not in MODES:
            mode = "auto"
        self.mode = mode
        self.stream = stream
        self.force = force

    def is_enabled(self) -> bool:
        if self.force is not None:
            return bool(self.force)
        if no_color_set():
            return False
        if self.mode == "off":
            return False
        if self.mode == "on":
            return True
        return is_tty(self.stream)

    enabled = property(is_enabled)

    def paint(self, text: str, role: str) -> str:
        """Tô màu `text` theo vai trò; tắt màu thì trả nguyên văn."""
        code = _CODES.get(role)
        if code is None or not self.is_enabled():
            return text
        return code + text + RESET

    def status(self, kind: str, text: str) -> str:
        """'✓ text' / '✗ text' / '↷ text'... có màu khi bật; tắt thì chỉ text."""
        if not self.is_enabled():
            return text
        sym = _SYMBOLS.get(kind, "")
        prefix = (sym + " ") if sym else ""
        return self.paint(prefix + text, kind)

    def title(self, text: str) -> str:
        """Tiêu đề mục: cyan khi bật, nguyên văn khi tắt."""
        return self.paint(text, "title")

    def rule(self, width: int = 48) -> str:
        """Đường kẻ ngang trang trí: chỉ có khi bật màu, tắt thì chuỗi rỗng
        (để output thuần không đổi byte nào)."""
        if not self.is_enabled():
            return ""
        return "─" * max(0, min(width, 78))

    def kv_table(self, rows: Sequence[Tuple[str, str]], width: int = 48) -> List[str]:
        """Bảng 'Nhãn: giá trị' gọn, vừa màn hình điện thoại: mỗi dòng ngắn,
        nhãn cyan khi bật màu. Trả danh sách dòng (không in trực tiếp)."""
        label_w = 0
        for label, _ in rows:
            label_w = max(label_w, display_width(label))
        out = []
        for label, value in rows:
            pad = " " * max(0, label_w - display_width(label))
            shown = self.paint(label, "title") + pad + ": " + value
            if display_width(strip_ansi(shown)) > width:
                shown = self.paint(label, "title") + ": " + value
            out.extend(wrap_text(shown, width) if display_width(strip_ansi(shown)) > width else [shown])
        return out


# ---- bảng mặc định (dùng khắp nơi) ----
_DEFAULT = Color()
_STDERR = Color(stream=sys.stderr)   # lỗi/cảnh báo ghi ra stderr: kiểm TTY của stderr


def paint_err(text: str, role: str) -> str:
    """Như paint() nhưng theo TTY của stderr (dùng cho thông báo lỗi)."""
    return _STDERR.paint(text, role)


def paint_first_line(text: str, role: str, color: Optional[Color] = None) -> str:
    """Chỉ tô dòng đầu của khối nhiều dòng (vd dòng '[E_...]' của format_error),
    các dòng sau giữ nguyên."""
    c = color or _DEFAULT
    if not c.is_enabled():
        return text
    head, sep, tail = text.partition("\n")
    return c.paint(head, role) + (sep + tail if sep else "")


def default_color() -> Color:
    return _DEFAULT


def set_mode(mode: str) -> None:
    """Đặt mode cho bảng mặc định (điểm vào gọi khi khởi động)."""
    _DEFAULT.mode = mode if mode in MODES else "auto"
    _DEFAULT.force = None


def configure_from_config(cfg) -> None:
    """Áp ui.color từ cấu hình (nếu đọc được) vào bảng mặc định.
    Lỗi/khóa lạ -> giữ auto, không làm hỏng khởi động."""
    try:
        mode = cfg.get("ui.color", "auto")
    except Exception:
        mode = "auto"
    if not isinstance(mode, str) or mode not in MODES:
        mode = "auto"
    set_mode(mode)


def paint(text: str, role: str, color: Optional[Color] = None) -> str:
    return (color or _DEFAULT).paint(text, role)


def status(kind: str, text: str, color: Optional[Color] = None) -> str:
    return (color or _DEFAULT).status(kind, text)


def title(text: str, color: Optional[Color] = None) -> str:
    return (color or _DEFAULT).title(text)


def rule(width: int = 48, color: Optional[Color] = None) -> str:
    return (color or _DEFAULT).rule(width)


def kv_table(rows, width: int = 48, color: Optional[Color] = None) -> List[str]:
    return (color or _DEFAULT).kv_table(rows, width)
