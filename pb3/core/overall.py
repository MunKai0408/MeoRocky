"""Thanh tiến độ TỔNG 0 -> 100% cho cả một lần chạy (nhiều bước) + thời gian còn lại.

Khác `core.progress.Progress` (1 thao tác đo bằng số đếm), lớp này theo dõi nhiều BƯỚC:
- Mỗi bước có thời lượng DỰ KIẾN (giây). Giá trị học từ các lần chạy trước (EMA) và lưu ở
  workspace/cache/overall_times.json; lần đầu dùng giá trị mặc định (đó chỉ là ước đoán).
- Trong một bước: nếu mã gọi báo tiến độ thật (`update(frac)`) thì dùng nó; nếu không (lệnh chạy
  "mờ", không biết bao giờ xong) thì thanh tiến theo thời gian tới ~90% rồi nhích dần, KHÔNG
  chạm 100% cho tới khi bước thật sự xong. Thanh không bao giờ lùi.
- Luồng nhịp (heartbeat) vẽ lại mỗi giây nên đồng hồ "còn ~" vẫn chạy khi lệnh đang treo lâu.
- Chỉ vẽ khi stderr là tty (hoặc ép bằng PB3_PROGRESS=1); không tty -> im lặng (không làm bẩn
  log/pipe). Tiến độ không bao giờ được làm hỏng thao tác chính: mọi lỗi vẽ bị nuốt.
Chỉ thư viện chuẩn, Python >= 3.8.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import secrets
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from .errors import PB3Error
from .progress import (CancelToken, _can_encode, default_width, display_width, fmt_duration,
                       format_bar, truncate_display)

DEFAULT_STAGE_S = 3.0
HISTORY_KEEP_JOBS = 50
_EMA = 0.5
_OPAQUE_AT_EXPECTED = 0.90      # tới đúng thời lượng dự kiến thì hiển thị 90% của bước
_OPAQUE_CAP = 0.99


# ---------------------------------------------------------------- dựng dòng (thuần, dễ test)
def format_overall(label: str, frac: float, eta_s: Optional[float], width: int,
                   ascii_only: bool = False, finished: bool = False,
                   total_s: Optional[float] = None) -> str:
    """Một dòng <= width cột: `[██████░░░░] 63% 2/3 Nhãn còn ~2m10s`."""
    width = max(10, int(width))
    frac = min(1.0, max(0.0, frac))
    # +1e-9: tránh lỗi float (28.9999999 -> 28) khi frac vừa đúng mốc %.
    pct = "%3d%%" % int(frac * 100 + 1e-9 + (0.5 if finished else 0.0)) if not finished else "100%"
    if finished:
        tail = " xong %s" % fmt_duration(total_s or 0.0)
    elif eta_s is None:
        tail = " còn ?"
    else:
        tail = " còn ~" + fmt_duration(eta_s)
    bar = format_bar(frac, width, ascii_only)
    head = bar + pct + " "
    for t in (tail, ""):
        room = width - display_width(head) - display_width(t)
        if room >= 6 or t == "":
            lab = truncate_display(label, max(room, 0))
            line = head + lab + t
            return line if display_width(line) <= width else truncate_display(line, width)
    return truncate_display(head + label, width)


# ---------------------------------------------------------------- lịch sử thời lượng
MAX_HISTORY_BYTES = 64 * 1024


def load_history(path) -> Dict[str, Dict[str, float]]:
    try:
        if os.path.getsize(str(path)) > MAX_HISTORY_BYTES:
            return {}   # file lịch sử quá lớn (bị sửa tay/phình): bỏ, không đọc
        with open(str(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, OverflowError):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    if isinstance(data, dict):
        for job, stages in data.items():
            if isinstance(job, str) and isinstance(stages, dict):
                clean = {}
                for k, v in stages.items():
                    if not (isinstance(k, str) and isinstance(v, (int, float))
                            and not isinstance(v, bool)):
                        continue
                    try:
                        fv = float(v)
                    except (OverflowError, ValueError):
                        continue   # số quá lớn (vd 10**400): bỏ, không crash
                    if 0 < fv < 86400 * 7:
                        clean[k] = fv
                if clean:
                    out[job] = clean
    return out


def save_history(path, hist: Dict[str, Dict[str, float]]) -> bool:
    """Ghi nguyên tử (tệp tạm + os.replace). Lỗi -> False, không ném."""
    try:
        jobs = list(hist.items())[-HISTORY_KEEP_JOBS:]
        tmp = "%s.pb3part-%d-%s" % (path, os.getpid(), secrets.token_hex(4))
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(dict(jobs), fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, str(path))
        return True
    except (OSError, TypeError, ValueError):
        return False


class _Stage:
    __slots__ = ("name", "expected", "actual", "t0", "sub", "note", "state")

    def __init__(self, name: str, expected: float):
        self.name = name
        self.expected = max(0.05, float(expected))
        self.actual = 0.0
        self.t0 = 0.0
        self.sub = None      # type: Optional[float]   # tiến độ thật trong bước (0..1) nếu có
        self.note = ""
        self.state = "pending"        # pending | running | done


class OverallProgress:
    def __init__(self, job: str, stages: List[Tuple[str, float]], *, stream=None, width: int = 0,
                 enabled: Optional[bool] = None, history_path=None,
                 clock: Callable[[], float] = time.monotonic, heartbeat_s: float = 1.0,
                 heartbeat: bool = True, isatty: Optional[bool] = None,
                 ascii_only: Optional[bool] = None, cancel: Optional[CancelToken] = None):
        self.job = job
        self._stages = [_Stage(n, e) for n, e in stages]
        self._stream = stream
        self._width = width
        self._clock = clock
        self._hb_s = max(0.1, float(heartbeat_s))
        self._hb_on = heartbeat
        self._isatty = isatty
        self._ascii = ascii_only
        self.cancel = cancel
        self._hist_path = history_path
        self._hist = {}                   # type: Dict[str, Dict[str, float]]
        self._lock = threading.RLock()
        self._t0 = clock()
        self._cur = None                  # type: Optional[_Stage]
        self._shown = 0.0                 # tiến độ đã hiển thị (không lùi)
        self._last_len = 0
        self._visible = False
        self._started = False
        self._closed = False
        self._paused = False              # đang chờ người dùng nhập: không vẽ
        self._thread = None               # type: Optional[threading.Thread]
        self._stop = threading.Event()
        self._real_err = None             # stderr thật khi đang bọc (guard_output)
        self._enabled = self._decide(enabled)
        if history_path is not None:
            self._hist = load_history(history_path)
            known = self._hist.get(job, {})
            for st in self._stages:
                if st.name in known:
                    st.expected = max(0.05, known[st.name])

    # ---- cấu hình
    @property
    def stream(self):
        if self._stream is not None:
            return self._stream
        return self._real_err if self._real_err is not None else sys.stderr

    def _tty(self) -> bool:
        if self._isatty is not None:
            return self._isatty
        try:
            return bool(self.stream.isatty())
        except Exception:
            return False

    def _decide(self, enabled: Optional[bool]) -> bool:
        env = os.environ.get("PB3_PROGRESS", "")
        if env == "0":
            return False
        if env == "1":
            return True
        if enabled is not None:
            return bool(enabled) and self._tty()
        return self._tty()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- tính toán
    def _stage_sub(self, st: _Stage, now: float) -> float:
        if st.state == "done":
            return 1.0
        if st.state == "pending":
            return 0.0
        if st.sub is not None:
            return min(1.0, max(0.0, st.sub))
        t = max(0.0, now - st.t0)
        if t <= st.expected:
            return _OPAQUE_AT_EXPECTED * t / st.expected
        extra = 1.0 - math.exp(-(t - st.expected) / st.expected)
        return min(_OPAQUE_CAP, _OPAQUE_AT_EXPECTED + (_OPAQUE_CAP - _OPAQUE_AT_EXPECTED) * extra)

    def fraction(self) -> float:
        with self._lock:
            if self._closed and self._finished_ok:
                return 1.0
            now = self._clock()
            total = sum(s.expected for s in self._stages) or 1.0
            done = sum(s.expected * self._stage_sub(s, now) for s in self._stages)
            f = min(1.0, max(0.0, done / total))
            if f < self._shown:
                f = self._shown
            self._shown = f
            return f

    _finished_ok = False

    def eta(self) -> Optional[float]:
        """Giây còn lại (ước đoán); None nếu chưa có cơ sở."""
        with self._lock:
            now = self._clock()
            rest = 0.0
            for s in self._stages:
                if s.state == "pending":
                    rest += s.expected
                elif s.state == "running":
                    sub = self._stage_sub(s, now)
                    t = max(0.0, now - s.t0)
                    if s.sub is not None and sub > 0.02:
                        rest += t * (1.0 - sub) / sub
                    elif s.sub is None and t > s.expected:
                        rest += s.expected * (1.0 - sub)        # quá dự kiến: ước nhỏ, không bịa
                    else:
                        rest += max(0.0, s.expected - t)
            return rest

    def _label(self) -> str:
        cur = self._cur
        n = len(self._stages)
        if cur is None:
            return self.job
        idx = self._stages.index(cur) + 1
        name = cur.note or cur.name
        return "%d/%d %s" % (idx, n, name)

    def line(self, finished: bool = False) -> str:
        ascii_only = self._ascii if self._ascii is not None else not _can_encode(self.stream, "█░…")
        w = default_width(self.stream, self._width)
        if finished:
            ln = format_overall(self.job, 1.0, 0.0, w, ascii_only, True, self._clock() - self._t0)
        else:
            ln = format_overall(self._label(), self.fraction(), self.eta(), w, ascii_only)
        return ln.replace("…", "~") if ascii_only else ln

    # ---- vẽ
    def _draw(self, final: bool = False, finished: bool = False) -> None:
        if not self._enabled or (self._paused and not final):
            return
        try:
            line = self.line(finished=finished)
            s = self.stream
            if self._tty():
                pad = max(0, self._last_len - display_width(line))
                s.write("\r" + line + " " * pad)
                self._last_len = display_width(line)
                self._visible = True
                if final:
                    s.write("\n")
                    self._visible = False
            else:
                s.write(line + "\n")
            s.flush()
        except Exception:
            pass

    def render(self) -> None:
        """Vẽ lại ngay (luồng nhịp gọi mỗi giây; test gọi tay)."""
        with self._lock:
            if self._started and not self._closed:
                self._draw()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._hb_s):
            self.render()

    def _clear_line(self) -> None:
        if self._visible and self._tty():
            try:
                self.stream.write("\r" + " " * self._last_len + "\r")
                self._visible = False
            except Exception:
                pass

    # ---- API
    def start(self) -> "OverallProgress":
        with self._lock:
            if self._started:
                return self
            self._started = True
            self._t0 = self._clock()
            self._draw()
            if self._enabled and self._hb_on and self._tty() and self._thread is None:
                self._thread = threading.Thread(target=self._heartbeat, name="pb3-overall", daemon=True)
                self._thread.start()
        return self

    def _find(self, name: str, expected: Optional[float]) -> _Stage:
        for s in self._stages:
            if s.name == name and s.state != "done":
                return s
        s = _Stage(name, expected if expected else self._hist.get(self.job, {}).get(name, DEFAULT_STAGE_S))
        self._stages.append(s)          # bước phát sinh động: tổng dự kiến tăng, thanh vẫn không lùi
        return s

    def begin(self, name: str, expected_s: Optional[float] = None) -> None:
        with self._lock:
            if not self._started:
                self.start()
            if self._cur is not None and self._cur.state == "running":
                self._end_current()
            st = self._find(name, expected_s)
            st.state, st.t0, st.sub, st.note = "running", self._clock(), None, ""
            self._cur = st
            self._draw()

    def _end_current(self) -> None:
        st = self._cur
        if st is None:
            return
        st.actual = max(0.0, self._clock() - st.t0)
        st.state = "done"
        st.sub = 1.0

    def end(self) -> None:
        with self._lock:
            self._end_current()
            self._draw()

    def expect(self, seconds: float) -> None:
        """Đổi thời lượng dự kiến của bước đang chạy (vd. --deep chậm hơn bình thường)."""
        with self._lock:
            if self._cur is not None and self._cur.state == "running":
                self._cur.expected = max(0.05, float(seconds))

    def mark_done(self, name: str, seconds: float) -> None:
        """Ghi nhận bước đã xong ngoài vòng theo dõi (vd. khởi động chạy trước khi tạo thanh)."""
        with self._lock:
            st = self._find(name, None)
            st.state, st.actual, st.sub = "done", max(0.0, float(seconds)), 1.0

    def update(self, frac: Optional[float] = None, *, done: Optional[float] = None,
               total: Optional[float] = None, note: Optional[str] = None) -> None:
        """Báo tiến độ THẬT trong bước hiện tại: frac (0..1) hoặc done/total. Có thể kèm ghi chú."""
        if self.cancel is not None:
            self.cancel.check(self.job)
        with self._lock:
            st = self._cur
            if st is None or st.state != "running":
                return
            if frac is None and done is not None and total:
                frac = float(done) / float(total)
            if frac is not None:
                f = min(1.0, max(0.0, float(frac)))
                st.sub = f if st.sub is None else max(st.sub, f)       # không lùi
            if note is not None:
                st.note = note

    @contextlib.contextmanager
    def stage(self, name: str, expected_s: Optional[float] = None):
        self.begin(name, expected_s)
        try:
            yield self
        except BaseException:
            raise
        else:
            self.end()

    def pause(self) -> None:
        """Tạm ẩn thanh (khi chờ người dùng nhập) để dòng nhập không bị thanh vẽ đè."""
        with self._lock:
            self._paused = True
            self._clear_line()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            if self._started and not self._closed:
                self._draw()

    def message(self, text: str) -> None:
        """In 1 dòng thông báo mà không phá thanh: xóa thanh, in, vẽ lại."""
        with self._lock:
            try:
                self._clear_line()
                self.stream.write(text + "\n")
                self.stream.flush()
            except Exception:
                pass
            if self._started and not self._closed:
                self._draw()

    @contextlib.contextmanager
    def guard_stdout(self):
        """Trong khối này, mọi ghi tới stdout VÀ stderr (print, sys.stderr.write) tự xóa thanh
        trước và vẽ lại thanh sau, nên thông báo không bị dính vào dòng thanh."""
        if not self._enabled or not self._tty():
            yield
            return
        real_out, real_err = sys.stdout, sys.stderr
        if self._stream is None:
            self._real_err = real_err                 # thanh vẫn vẽ lên stderr THẬT
            sys.stderr = _StdoutGuard(real_err, self)      # type: ignore[assignment]
        sys.stdout = _StdoutGuard(real_out, self)          # type: ignore[assignment]
        try:
            yield
        finally:
            sys.stdout, sys.stderr = real_out, real_err
            self._real_err = None

    def finish(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._end_current()
            for s in self._stages:
                if s.state != "done":
                    s.state, s.actual = "done", 0.0
            self._closed = True
            self._finished_ok = True
            self._shown = 1.0
            self._stop_thread()
            self._clear_line()
            self._draw(final=True, finished=True)
            self._save()

    def abort(self) -> None:
        """Kết thúc khi lỗi/hủy: xuống dòng, KHÔNG ép 100%, KHÔNG ghi lịch sử."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop_thread()
            if self._enabled and self._tty() and self._visible:
                try:
                    self.stream.write("\n")
                    self.stream.flush()
                except Exception:
                    pass
                self._visible = False

    def _stop_thread(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5.0)

    def _save(self) -> None:
        if self._hist_path is None:
            return
        done = {s.name: s.actual for s in self._stages if s.state == "done" and s.actual > 0}
        if not done:
            return
        old = self._hist.get(self.job, {})
        merged = dict(old)
        for k, v in done.items():
            merged[k] = v if k not in old else (_EMA * v + (1 - _EMA) * old[k])
        self._hist.pop(self.job, None)
        self._hist[self.job] = merged
        save_history(self._hist_path, self._hist)

    def __enter__(self) -> "OverallProgress":
        return self.start()

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self.finish()
        else:
            self.abort()


class _StdoutGuard:
    """Bọc stdout: xóa dòng thanh trước khi ghi, vẽ lại sau mỗi dòng hoàn chỉnh."""

    def __init__(self, real, ov: OverallProgress):
        self._real = real
        self._ov = ov

    def write(self, s):
        with self._ov._lock:
            self._ov._clear_line()
            n = self._real.write(s)
            if isinstance(s, str) and s.endswith("\n"):
                self._ov._draw()
            return n

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._real, name)
