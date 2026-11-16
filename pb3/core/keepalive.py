"""Giữ iSH chạy nền bằng thiết bị vị trí của iSH (/dev/location).

Cơ chế (do iSH/iOS, không phải PB3): ứng dụng iOS có dùng vị trí được phép tiếp tục chạy khi
ở nền. iSH phơi dữ liệu vị trí qua tệp thiết bị /dev/location; một luồng ĐỌC liên tục tệp này
(tương đương `cat /dev/location > /dev/null &`) làm iOS coi iSH đang dùng vị trí.
Điều kiện: lần đầu iOS hỏi quyền vị trí cho iSH -> phải cho phép; muốn chạy khi khóa màn hình
nên chọn mức cho phép luôn (Cài đặt > iSH > Vị trí). Tốn pin hơn; iOS hiện chỉ báo đang dùng vị trí.

TRUNG THỰC (đặc tả: không tuyên bố khi chưa có bằng chứng thiết bị):
- `running` chỉ nghĩa là đã MỞ được thiết bị và đang đọc; PB3 không thể chứng minh iOS thật sự
  không dừng iSH. Phải thử trên máy (khóa màn hình, chờ vài phút, xem tiến trình còn sống).
- Không có /dev/location (ngoài iSH, hoặc bản iSH không hỗ trợ) -> `unavailable`, không giả lập.
- Không dùng tiến trình con, không mạng; chỉ một luồng nền daemon (tự chết khi PB3 thoát).
Chỉ thư viện chuẩn, Python >= 3.8.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

DEVICE = "/dev/location"

S_OFF, S_UNAVAILABLE, S_STARTING, S_RUNNING, S_ERROR, S_STOPPED, S_STOPPING = (
    "off", "unavailable", "starting", "running", "error", "stopped", "stopping")

_STATE_VI = {
    S_OFF: "TẮT",
    S_UNAVAILABLE: "KHÔNG KHẢ DỤNG",
    S_STARTING: "đang mở thiết bị vị trí",
    S_RUNNING: "ĐANG ĐỌC thiết bị vị trí (chưa chứng minh iOS giữ nền)",
    S_ERROR: "LỖI",
    S_STOPPED: "đã dừng",
    S_STOPPING: "đang dừng (luồng đọc chưa thoát)",
}


@dataclass
class KeepAliveStatus:
    state: str
    detail: str = ""
    bytes_read: int = 0
    opened: int = 0              # số lần mở thiết bị thành công
    seconds: float = 0.0         # thời gian kể từ start()
    stopping: bool = False       # đã yêu cầu dừng nhưng luồng đọc chưa thoát
    thread_alive: bool = False   # luồng đọc nền còn sống hay không

    @property
    def ok(self) -> bool:
        return self.state in (S_STARTING, S_RUNNING)

    def vi(self) -> str:
        txt = _STATE_VI.get(self.state, self.state)
        return "%s%s" % (txt, (" - " + self.detail) if self.detail else "")


class KeepAlive:
    def __init__(self, device: str = DEVICE, *, retry_s: float = 2.0, max_failures: int = 5,
                 opener: Optional[Callable[[str], object]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.device = device
        self.retry_s = max(0.01, float(retry_s))
        self.max_failures = max(1, int(max_failures))
        self._open = opener or (lambda p: open(p, "rb", buffering=0))
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None            # type: Optional[threading.Thread]
        self._state = S_OFF
        self._detail = ""
        self._bytes = 0
        self._opened = 0
        self._t0 = 0.0

    # ---- trạng thái
    def available(self) -> bool:
        try:
            return os.path.exists(self.device)
        except OSError:
            return False

    def status(self) -> KeepAliveStatus:
        with self._lock:
            secs = (self._clock() - self._t0) if self._t0 else 0.0
            alive = self._thread is not None and self._thread.is_alive()
            stopping = bool(self._stop.is_set() and alive)
            return KeepAliveStatus(self._state, self._detail, self._bytes, self._opened, secs,
                                   stopping=stopping, thread_alive=alive)

    def _set(self, state: str, detail: str = "") -> None:
        with self._lock:
            self._state, self._detail = state, detail

    # ---- điều khiển
    def start(self) -> KeepAliveStatus:
        """Bật luồng đọc nền. Không chặn; trả trạng thái ngay (starting hoặc unavailable)."""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
        if alive:
            return self.status()
        if not self.available():
            self._set(S_UNAVAILABLE, "không có %s (ngoài iSH hoặc iSH không hỗ trợ vị trí)" % self.device)
            return self.status()
        self._stop.clear()
        with self._lock:
            self._t0 = self._clock()
            self._bytes = 0
            self._opened = 0
        self._set(S_STARTING)
        t = threading.Thread(target=self._loop, name="pb3-keepalive", daemon=True)
        self._thread = t
        t.start()
        return self.status()

    def stop(self) -> KeepAliveStatus:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=0.5)       # đọc đang chặn thì không ngắt được; luồng daemon tự chết khi thoát
        with self._lock:
            alive = t is not None and t.is_alive()
            if alive and self._state in (S_STARTING, S_RUNNING):
                # TRUNG THỰC: luồng chưa thoát thì báo 'stopping', không báo 'stopped'.
                self._state, self._detail = S_STOPPING, "luồng đọc chưa thoát sau 0.5s (daemon, tự chết khi thoát)"
            elif self._state in (S_STARTING, S_RUNNING):
                self._state, self._detail = S_STOPPED, ""
        return self.status()

    def wait_running(self, timeout_s: float = 3.0) -> bool:
        end = self._clock() + timeout_s
        while self._clock() < end:
            if self.status().state in (S_RUNNING, S_ERROR, S_UNAVAILABLE):
                break
            time.sleep(0.02)
        return self.status().state == S_RUNNING

    # ---- vòng đọc
    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                fh = self._open(self.device)
            except PermissionError:
                self._set(S_ERROR, "chưa được cấp quyền vị trí cho iSH (Cài đặt > iSH > Vị trí)")
                failures += 1
            except OSError as exc:
                self._set(S_ERROR, "không mở được %s (%s, errno=%s)" % (self.device, type(exc).__name__, exc.errno))
                failures += 1
            else:
                failures = 0
                with self._lock:
                    self._opened += 1
                    self._state, self._detail = S_RUNNING, ""
                try:
                    while not self._stop.is_set():
                        data = fh.read(1024)            # type: ignore[attr-defined]
                        if not data:                    # EOF: mở lại sau một nhịp
                            break
                        with self._lock:
                            self._bytes += len(data)
                except OSError as exc:
                    self._set(S_ERROR, "lỗi đọc (%s, errno=%s)" % (type(exc).__name__, exc.errno))
                    failures += 1
                finally:
                    try:
                        fh.close()                      # type: ignore[attr-defined]
                    except Exception:
                        pass
            if failures >= self.max_failures:
                self._set(S_ERROR, self.status().detail or "lỗi liên tiếp %d lần, dừng" % failures)
                return
            if self._stop.wait(self.retry_s):
                break
        with self._lock:
            if self._state in (S_STARTING, S_RUNNING, S_STOPPING):
                self._state, self._detail = S_STOPPED, ""

    def __enter__(self) -> "KeepAlive":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def keep_ish_alive(device: str = DEVICE) -> KeepAlive:
    """Tiện ích: tạo + bật. Dùng `ka.status().vi()` để báo trạng thái trung thực; `ka.stop()` để tắt."""
    ka = KeepAlive(device)
    ka.start()
    return ka
