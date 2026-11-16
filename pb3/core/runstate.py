"""Dấu hiệu 'lần chạy trước bị ngắt' (iOS có thể đóng iSH bất kỳ lúc nào, đặc tả 12.3, 18.2).

Mỗi phiên ghi workspace/temp/.pb3-run.json. Phiên kết thúc bình thường thì xóa.
Nếu lần sau còn thấy file này mà pid không còn sống -> lần trước bị ngắt.
Chỉ mang tính CẢNH BÁO (không chặn chạy); pid có thể trùng ngẫu nhiên sau khi iSH
khởi động lại, nên 'other_running' chỉ là gợi ý.

Lưu ý iSH (probe 2026-11-16): getppid() trong daemon thread trả về pid của
chính tiến trình thay vì pid cha thật — nên mã sản phẩm KHÔNG BAO GIỜ dùng
getppid() (chỉ os.getpid()/os.kill, vốn đúng trong mọi thread); test nào cần
pid của "phiên khác đang chạy" phải chốt pid cha một lần lúc import (main
thread), không gọi getppid() lúc chạy test.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

MARKER_NAME = ".pb3-run.json"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Chỉ ESRCH (ProcessLookupError) mới chắc chắn là đã chết. Errno lạ
        # (vd iSH) nghĩa là "không rõ" — coi như còn sống để khỏi giành
        # marker của phiên đang chạy (chỉ là cảnh báo, an toàn hơn).
        return True
    return True


def proc_start(pid: int):
    """Dấu thời điểm khởi động tiến trình từ /proc/<pid>/stat (trường 22), hoặc None nếu không đọc được (iSH có thể thiếu)."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            raw = fh.read().decode("utf-8", "replace")
        rest = raw[raw.rindex(")") + 2:].split()
        return rest[19]
    except (OSError, ValueError, IndexError):
        return None


def _pid_reused(pid: int, prev: Dict[str, Any]) -> bool:
    """True nếu marker có dấu khởi động mà tiến trình hiện mang pid đó có dấu khác (pid bị tái dùng sau khi khởi động lại).
    Không đọc được dấu (iSH thiếu /proc, marker cũ không có pstart) -> False = giữ hành vi cũ."""
    old = prev.get("pstart")
    if not isinstance(old, str):
        return False
    cur = proc_start(pid)
    return cur is not None and cur != old


class RunInfo:
    def __init__(self, state: str, previous: Optional[Dict[str, Any]] = None):
        self.state = state            # clean | interrupted | other_running
        self.previous = previous

    def __repr__(self) -> str:
        return "RunInfo(%s)" % self.state


class RunMarker:
    def __init__(self, temp_dir, session_id: str):
        self.path = Path(temp_dir) / MARKER_NAME
        self.session_id = session_id
        self._owned = False

    def acquire(self) -> RunInfo:
        info = RunInfo("clean")
        try:
            if self.path.exists():
                try:
                    prev = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(prev, dict):
                        raise ValueError
                except (OSError, ValueError):
                    prev = {}
                pid = prev.get("pid") if isinstance(prev.get("pid"), int) else 0
                if pid and pid != os.getpid() and pid_alive(pid) and not _pid_reused(pid, prev):
                    return RunInfo("other_running", prev)      # không giành marker
                info = RunInfo("interrupted", prev)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"pid": os.getpid(), "session": self.session_id,
                                             "started": time.time(), "pstart": proc_start(os.getpid())}), encoding="utf-8")
            self._owned = True
        except OSError:
            pass   # không ghi được marker: bỏ qua, không làm hỏng khởi động
        return info

    def release(self) -> None:
        if not self._owned:
            return
        try:
            cur = json.loads(self.path.read_text(encoding="utf-8"))
            if cur.get("pid") == os.getpid() and cur.get("session") == self.session_id:
                self.path.unlink()
        except (OSError, ValueError):
            pass
        self._owned = False
