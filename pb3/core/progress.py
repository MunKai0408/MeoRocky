"""Tiến độ + hủy an toàn, thiết kế cho terminal HẸP của iSH (~48 cột).

Bài học từ mã cũ (legacy/HANDOFF_legacy.md): dòng vẽ lại bằng '\\r' dài hơn
bề rộng terminal sẽ bị gập và in dồn nhiều dòng. Vì vậy mọi dòng đều bị cắt theo
ĐỘ RỘNG HIỂN THỊ (chữ CJK = 2 cột) và luôn <= width (mặc định cột - 4).
"""
from __future__ import annotations

import contextlib
import shutil
import signal
import sys
import threading
import time
import unicodedata
from typing import Callable, List, Optional

from .color import paint
from .errors import PB3Error


# ---------------------------------------------------------------- hủy an toàn
class CancelToken:
    """Cờ hủy dùng chung. Vòng lặp dài phải gọi check() mỗi khối để phản hồi <= 2s."""

    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self, where: Optional[str] = None) -> None:
        if self._ev.is_set():
            raise PB3Error("E_CANCELLED", where=where)


@contextlib.contextmanager
def sigint_cancels(token: CancelToken, message: Optional[str] = None):
    """Ctrl-C lần 1 = đặt cờ hủy (dừng an toàn, dọn temp); lần 2 = KeyboardInterrupt ngay."""
    if threading.current_thread() is not threading.main_thread():
        yield token
        return
    prev = signal.getsignal(signal.SIGINT)
    state = {"n": 0}
    msg = message or "\nĐang hủy an toàn... nhấn Ctrl-C lần nữa để dừng ngay."

    def handler(signum, frame):
        state["n"] += 1
        if state["n"] == 1:
            token.cancel()
            try:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:
                pass
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)
    try:
        yield token
    finally:
        signal.signal(signal.SIGINT, prev)


# ---------------------------------------------------------------- định dạng
# Cache độ rộng từng ký tự cho display_width/truncate_display (dòng tiến độ
# vẽ lại mỗi giây + mỗi lần cập nhật nên cùng ký tự lặp rất nhiều; kết quả
# y hệt vòng lặp gốc: ký tự kết hợp = 0 cột, CJK/fullwidth = 2, còn lại 1).
_PROG_CHAR_WIDTH: dict = {}


def _prog_char_width(ch: str) -> int:
    w = _PROG_CHAR_WIDTH.get(ch)
    if w is None:
        if unicodedata.combining(ch):
            w = 0
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w = 2
        else:
            w = 1
        if len(_PROG_CHAR_WIDTH) < 8192:
            _PROG_CHAR_WIDTH[ch] = w
    return w


def display_width(s: str) -> int:
    """Độ rộng hiển thị; mã ANSI (màu thanh tiến độ) tính 0 cột.

    Kết quả y hệt bản cũ: chuỗi ASCII thuần (không mã ANSI) thì độ rộng =
    số ký tự; chuỗi khác cộng dồn qua _prog_char_width (có cache)."""
    if "\x1b" not in s:
        if s.isascii():
            return len(s)
        return sum(map(_prog_char_width, s))
    w = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\x1b" and i + 1 < n and s[i + 1] == "[":
            j = s.find("m", i + 2)
            if j != -1:
                i = j + 1
                continue
        w += _prog_char_width(ch)
        i += 1
    return w


def truncate_display(s: str, width: int) -> str:
    """Cắt chuỗi theo độ rộng hiển thị, thêm '…' khi bị cắt."""
    if width <= 0:
        return ""
    if display_width(s) <= width:
        return s
    out, w = [], 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\x1b" and i + 1 < n and s[i + 1] == "[":
            j = s.find("m", i + 2)
            if j != -1:
                out.append(s[i:j + 1])
                i = j + 1
                continue
        cw = _prog_char_width(ch)
        if w + cw > width - 1:
            break
        out.append(ch)
        w += cw
        i += 1
    return "".join(out) + "…"


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return "%.1fGB" % n


def format_bar(frac: Optional[float], width: int, ascii_only: bool = False) -> str:
    """Thanh '[██████░░░░] ' dùng chung cho format_progress/format_overall
    (trước đây hai nơi tự dựng thanh giống nhau nhưng lệch nhau).
    Phần đã xong tô xanh, phần chưa tô mờ khi bật màu; tắt màu -> y hệt cũ."""
    barw = 10 if width >= 40 else (6 if width >= 28 else 0)
    if not barw:
        return ""
    fill, empty = ("#", "-") if ascii_only else ("█", "░")
    n = int(round((frac or 0.0) * barw))
    return "[" + paint(fill * n, "pass") + paint(empty * (barw - n), "dim") + "] "


def fmt_duration(sec: float) -> str:
    sec = int(max(0, sec))
    if sec >= 3600:
        return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)
    if sec >= 60:
        return "%dm%02ds" % (sec // 60, sec % 60)
    return "%ds" % sec


def eta_seconds(elapsed: float, frac: float) -> Optional[float]:
    """Giây còn lại ước từ % đã xong và thời gian thực đã chạy (luật #152).

    ETA = elapsed / frac * (1 - frac). None khi chưa có cơ sở (frac <= 0.02,
    frac >= 1, elapsed <= 0): không bịa số."""
    if elapsed <= 0 or frac <= 0.02 or frac >= 1.0:
        return None
    return elapsed * (1.0 - frac) / frac


def step_pcts(last_pct: int, new_pct: int, max_step: int = 1) -> List[int]:
    """Các mốc % nguyên từ last_pct tới new_pct, mỗi bước <= max_step (luật #152).

    Trả [] nếu new_pct <= last_pct. Dãy đơn điệu tăng, mốc cuối đúng new_pct.
    Dùng để vẽ tiến độ tăng dần từng 1% (không nhảy cục) khi số thật nhảy vọt
    trong một lần cập nhật (vd một lô xong 50% cùng lúc)."""
    if new_pct <= last_pct:
        return []
    out: List[int] = []
    cur = int(last_pct)
    step = max(1, int(max_step))
    while cur < new_pct:
        cur = min(new_pct, cur + step)
        out.append(cur)
    return out


def format_progress(label: str, done: float, total: Optional[float], elapsed: float,
                    width: int, ascii_only: bool = False, bytes_mode: bool = False) -> str:
    """Dựng 1 dòng tiến độ có độ rộng hiển thị <= width (thuần, dễ kiểm thử)."""
    width = max(10, int(width))
    unit: Callable = fmt_bytes if bytes_mode else (lambda v: str(int(v)))
    if total and total > 0:
        frac = min(1.0, max(0.0, done / float(total)))
        # +1e-9: tránh lỗi float (28.9999999 -> 28) khi done/total vừa đúng mốc %.
        pct = "%3d%%" % int(frac * 100 + 1e-9)
        counts = " %s/%s" % (unit(done), unit(total))
    else:
        frac = None
        pct = "  ? "
        counts = " %s" % unit(done)
    eta = ""
    if frac is not None:
        rest = eta_seconds(elapsed, frac)
        if rest is not None:
            eta = " ~" + fmt_duration(rest)
    bar = format_bar(frac, width, ascii_only)
    head = bar + pct + " "
    for tail in (counts + eta, counts, ""):
        room = width - display_width(head) - display_width(tail)
        if room >= 6 or tail == "":
            lab = truncate_display(label, max(room, 0))
            return (head + lab + tail) if display_width(head + lab + tail) <= width \
                else truncate_display(head + lab + tail, width)
    return truncate_display(head + label, width)  # không tới được


def default_width(stream=None, override: int = 0) -> int:
    """Bề rộng dòng tiến độ: override, hoặc cột terminal - 4 (kẹp 20..76); iSH ~48 -> 44."""
    if override and override >= 10:
        return int(override)
    cols = shutil.get_terminal_size((48, 24)).columns
    return max(20, min(76, cols - 4))


def _can_encode(stream, text: str) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class Progress:
    """Báo tiến độ. Trên tty vẽ lại 1 dòng; không tty thì in dòng thưa (không '\\r')."""

    def __init__(self, label: str, total: Optional[float] = None, *, stream=None,
                 width: int = 0, interval_s: float = 1.0, isatty: Optional[bool] = None,
                 clock: Callable[[], float] = time.monotonic, bytes_mode: bool = False,
                 ascii_only: Optional[bool] = None, cancel: Optional[CancelToken] = None):
        self.label = label
        self.total = total
        self.done = 0.0
        self.interval_s = interval_s
        self.bytes_mode = bytes_mode
        self.cancel = cancel
        self._stream = stream
        self._width_override = width
        self._clock = clock
        self._t0 = clock()
        self._last = None          # type: Optional[float]
        self._last_len = 0
        self._finished = False
        self._isatty = isatty
        self._ascii = ascii_only

    # -- phụ trợ
    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stderr

    def _tty(self) -> bool:
        if self._isatty is not None:
            return self._isatty
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def _line(self) -> str:
        ascii_only = self._ascii if self._ascii is not None else not _can_encode(self.stream, "█░…")
        w = default_width(self.stream, self._width_override)
        line = format_progress(self.label, self.done, self.total, self._clock() - self._t0,
                               w, ascii_only, self.bytes_mode)
        if ascii_only:
            line = line.replace("…", "~")
        return line

    def _emit(self, final: bool = False) -> None:
        line = self._line()
        s = self.stream
        try:
            if self._tty():
                pad = max(0, self._last_len - display_width(line))
                s.write("\r" + line + " " * pad)
                self._last_len = display_width(line)
                if final:
                    s.write("\n")
            else:
                s.write(line + "\n")
            s.flush()
        except Exception:
            pass   # tiến độ không bao giờ được làm hỏng thao tác chính
        self._last = self._clock()

    # -- API
    def start(self) -> "Progress":
        self._emit()
        return self

    def update(self, done: Optional[float] = None, advance: float = 0.0,
               note: Optional[str] = None) -> None:
        if self.cancel is not None:
            self.cancel.check(self.label)
        self.done = float(done) if done is not None else self.done + advance
        if note is not None:
            self.label = note
        now = self._clock()
        gap = self.interval_s if self._tty() else max(self.interval_s, 5.0)
        if self._last is None or now - self._last >= gap:
            self._emit()

    def finish(self, note: Optional[str] = None) -> None:
        if self._finished:
            return
        self._finished = True
        if note is not None:
            self.label = note
        if self.total:
            self.done = float(self.total)
        self._emit(final=True)

    def abort(self) -> None:
        """Kết thúc dòng khi thao tác lỗi/bị hủy (xuống dòng, không ép 100%)."""
        if self._finished:
            return
        self._finished = True
        try:
            if self._tty():
                self.stream.write("\n")
                self.stream.flush()
        except Exception:
            pass

    def __enter__(self) -> "Progress":
        return self.start()

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self.finish()
        else:
            self.abort()


class SteppedReporter:
    """Báo tiến độ THẬT qua app.report_progress, tăng dần từng 1% (luật #152).

    Dùng chung cho các thao tác quét/liệt kê/đọc (inspect, mod, container):
    mỗi lần report(done, total) mà % nhảy hơn 1% thì quét dần qua từng mốc
    % trung gian (như _ProgressAdapter của dịch, services/translate_tool.py)
    rồi vẽ lại thanh ngay để người dùng thấy thanh chạy dần, không nhảy cục.
    Có thanh Overall thì dùng nó; không có thì dùng SteadyProgress riêng
    (cùng mẫu một dòng format_progress, thưa khi không TTY). Không bao giờ
    lùi; total <= 0 thì không báo gì (không bịa %).

    `note` là ghi chú hiện tại (tên entry đang xử lý) hoặc hàm
    note(đã_xong_nội_suy, tổng) -> ghi chú (để hiện số lượng thật theo mốc
    %, như adapter dịch hiện "dịch i/N")."""

    def __init__(self, app, label: str = "đang chạy"):
        self._app = app
        self._label = label
        # Chỉ dùng thanh Overall khi nó đang BẬT (TTY): thanh tắt (chuyển
        # hướng/PB3_PROGRESS=0) thì rơi về SteadyProgress riêng để khi chạy
        # nền vẫn có dòng tiến độ thưa ra stderr thay vì im lặng.
        ov = getattr(app, "overall", None) if app is not None else None
        self._ov = ov if (ov is not None and ov.enabled) else None
        self._steady = None            # type: Optional[SteadyProgress]
        self._shown_pct = 0

    def _note(self, note, interp: int, total: float):
        if callable(note):
            try:
                return note(interp, total)
            except Exception:
                return self._label
        return note if note is not None else self._label

    def report(self, done: float, total: Optional[float], note=None) -> None:
        if total is None or total <= 0:
            return
        new_pct = int(min(1.0, max(0.0, float(done) / float(total))) * 100 + 1e-9)
        if self._ov is not None:
            for pct in step_pcts(self._shown_pct, new_pct):
                interp = int(round(pct / 100.0 * float(total)))
                self._app.report_progress(pct / 100.0, note=self._note(note, interp, total))
                self._ov.render()
            self._shown_pct = new_pct
            return
        if self._steady is None:
            self._steady = SteadyProgress(self._label, total)
        self._steady.update(done, note=note if isinstance(note, str) else self._label)

    def finish(self, note: Optional[str] = None) -> None:
        """Kết thúc thanh riêng (khi không có Overall); thanh Overall do
        job ngoài kết thúc nên ở đây không làm gì."""
        if self._steady is not None:
            self._steady.finish(note)


class SteadyProgress:
    """Báo tiến độ THẬT theo số đếm, hiển thị tăng dần từng 1% (luật #152).

    Bọc Progress (cùng mẫu một dòng format_progress: vạch + % + số lượng +
    ETA, màu qua core/color.py, vừa màn hình điện thoại). Mỗi lần update(done)
    mà % nhảy hơn max_step thì vẽ lần lượt từng mốc % trung gian trên cùng
    dòng (\\r, TTY) — số lượng hiển thị nội suy theo mốc % (không vượt số
    thật đã xong), ETA tính từ thời gian thực đã chạy. Không TTY thì chỉ in
    dòng thưa (không vẽ từng mốc 1% ra log). Không bao giờ lùi.
    Dòng kết thúc (finish -> 100% + "xong") là mốc hoàn thành, không tính là
    bước nhảy tiến độ."""

    def __init__(self, label: str, total: Optional[float] = None, *, stream=None,
                 width: int = 0, clock: Callable[[], float] = time.monotonic,
                 isatty: Optional[bool] = None, ascii_only: Optional[bool] = None,
                 max_step: int = 1):
        self._prog = Progress(label, total, stream=stream, width=width,
                              interval_s=0, isatty=isatty, clock=clock,
                              ascii_only=ascii_only)
        self._clock = clock
        self._max_step = max(1, int(max_step))
        self._real_done = 0.0
        self._shown_pct = 0
        self._last_emit: Optional[float] = None
        self._finished = False

    @property
    def total(self) -> Optional[float]:
        return self._prog.total

    def _emit(self) -> None:
        self._prog._emit()
        self._last_emit = self._clock()

    def update(self, done: Optional[float] = None, note: Optional[str] = None) -> None:
        """Báo số thật đã xong (không bao giờ lùi)."""
        if self._finished:
            return
        if done is not None:
            self._real_done = max(self._real_done, float(done))
        total = self._prog.total
        new_pct = 0
        if total and total > 0:
            new_pct = int(min(1.0, max(0.0, self._real_done / float(total))) * 100)
        if note is not None:
            self._prog.label = note
        now = self._clock()
        if self._prog._tty():
            stepped = False
            for pct in step_pcts(self._shown_pct, new_pct, self._max_step):
                self._prog.done = pct / 100.0 * float(total) if total else 0.0
                self._emit()
                self._shown_pct = pct
                stepped = True
            if not stepped and (self._last_emit is None or now - self._last_emit >= 1.0):
                # Chưa đủ 1% mới nhưng đã quá 1s: vẽ lại để ETA/đồng hồ chạy.
                self._prog.done = self._real_done
                self._emit()
        else:
            self._prog.done = self._real_done
            self._prog.update()
            self._last_emit = now

    def finish(self, note: Optional[str] = None) -> None:
        if self._finished:
            return
        self._finished = True
        self._prog.finish(note)

    def abort(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._prog.abort()

    def __enter__(self) -> "SteadyProgress":
        self._prog.start()
        return self

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self.finish()
        else:
            self.abort()
