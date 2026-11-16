"""Logging xoay vòng + log theo phiên, KHÔNG làm lộ nội dung file người dùng.

Tên module trùng 'logging' của thư viện chuẩn (đúng đặc tả 2.2). Nó an toàn vì
mọi import trong dự án đều là tuyệt đối (core.logging) và thư mục core/ không
nằm trong sys.path; bên trong đây thư viện chuẩn được import là `_std`.

Chống lộ dữ liệu: (1) bytes/bytearray trong tham số log -> '<bytes len= sha8=>';
(2) mỗi dòng bị cắt ở log.max_message_chars; (3) ký tự điều khiển bị thay.
Hãy log hash/kích thước (core.hashing.describe_*), không log nội dung.
Không ghi được file log -> rơi về stderr và báo lỗi rõ, KHÔNG crash.
"""
from __future__ import annotations

import logging as _std
import logging.handlers as _handlers
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

from .hashing import describe_bytes

ROOT_NAME = "pb3"
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def get_logger(name: str = "") -> _std.Logger:
    return _std.getLogger(ROOT_NAME + ("." + name if name else ""))


class SafeFormatter(_std.Formatter):
    def __init__(self, fmt: str, max_chars: int = 500):
        super().__init__(fmt, "%Y-%m-%d %H:%M:%S")
        self.max_chars = max(80, int(max_chars))

    def _clip(self, s: str) -> str:
        s = _CTRL.sub("?", s)
        if len(s) > self.max_chars:
            s = s[:self.max_chars] + "…[+%d ký tự]" % (len(s) - self.max_chars)
        return s

    def format(self, record: _std.LogRecord) -> str:
        # thay bytes trong args trước khi nội suy chuỗi
        if isinstance(record.args, tuple):
            record.args = tuple(describe_bytes(a) if isinstance(a, (bytes, bytearray, memoryview))
                                else a for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: (describe_bytes(v) if isinstance(v, (bytes, bytearray, memoryview)) else v)
                           for k, v in record.args.items()}
        if isinstance(record.msg, (bytes, bytearray)):
            record.msg = describe_bytes(record.msg)
        saved_exc, saved_text = record.exc_info, record.exc_text
        record.exc_info = record.exc_text = None
        head = self._clip(super().format(record))
        record.exc_info, record.exc_text = saved_exc, saved_text
        if saved_exc:
            tb = "".join(traceback.format_exception(*saved_exc))
            head += "\n" + "\n".join(self._clip(ln) for ln in tb.rstrip().splitlines())
        return head


class LogSetup:
    def __init__(self) -> None:
        self.logger = get_logger()
        self.main_log: Optional[Path] = None
        self.session_log: Optional[Path] = None
        self.problems: List[str] = []
        self._handlers: List[_std.Handler] = []

    def close(self) -> None:
        for h in self._handlers:
            try:
                self.logger.removeHandler(h)
                h.close()
            except Exception:
                pass
        self._handlers = []


def _prune_sessions(logs_dir: Path, keep: int) -> None:
    try:
        files = sorted(logs_dir.glob("session-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()           # chỉ xóa log theo phiên do PB3 tạo
            except OSError:
                pass
    except OSError:
        pass


def setup_logging(logs_dir, *, session_id: str, level: str = "INFO", max_bytes: int = 1024 * 1024,
                  backups: int = 3, keep_sessions: int = 20, max_message_chars: int = 500,
                  debug_console: bool = False) -> LogSetup:
    """Dựng handler. Không bao giờ raise; vấn đề được ghi vào LogSetup.problems."""
    ls = LogSetup()
    lg = ls.logger
    for h in list(lg.handlers):                       # tránh nhân đôi khi gọi lại;
        if not getattr(h, "_pb3_owned", False):       # chỉ đóng handler DO PB3 TẠO (không động vào handler ngoài)
            continue
        lg.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    lg.setLevel(getattr(_std, level.upper(), _std.INFO))
    lg.propagate = False
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    sf = SafeFormatter(fmt, max_chars=max_message_chars)
    try:
        d = Path(logs_dir)
        d.mkdir(parents=True, exist_ok=True)
        ls.main_log = d / "pb3.log"
        h1 = _handlers.RotatingFileHandler(str(ls.main_log), maxBytes=max_bytes, backupCount=backups,
                                           encoding="utf-8")
        h1.setFormatter(sf)
        h1._pb3_owned = True
        lg.addHandler(h1)
        ls._handlers.append(h1)
        ls.session_log = d / ("session-%s.log" % session_id)
        # Log phiên cũng xoay vòng (tránh một phiên dài ghi log vô hạn).
        h2 = _handlers.RotatingFileHandler(str(ls.session_log), maxBytes=max_bytes, backupCount=backups,
                                           encoding="utf-8")
        h2.setFormatter(sf)
        h2._pb3_owned = True
        lg.addHandler(h2)
        ls._handlers.append(h2)
        _prune_sessions(d, keep_sessions)
    except OSError as exc:
        ls.problems.append("không ghi được log tại %s (%s, errno=%s); chỉ log ra stderr mức WARNING"
                           % (logs_dir, type(exc).__name__, exc.errno))
        ls.main_log = ls.session_log = None
        hs = _std.StreamHandler(sys.stderr)
        hs.setLevel(_std.WARNING)
        hs.setFormatter(sf)
        hs._pb3_owned = True
        lg.addHandler(hs)
        ls._handlers.append(hs)
    if debug_console:
        hc = _std.StreamHandler(sys.stderr)
        hc.setLevel(_std.DEBUG)
        hc.setFormatter(sf)
        hc._pb3_owned = True
        lg.addHandler(hc)
        ls._handlers.append(hc)
    return ls


def log_exception(logger: _std.Logger, exc: BaseException, where: str = "") -> None:
    """Ghi traceback đầy đủ vào log (người dùng chỉ thấy thông báo tiếng Việt + mã lỗi)."""
    logger.error("ngoại lệ %s tại %s", type(exc).__name__, where or "?",
                 exc_info=(type(exc), exc, exc.__traceback__))


def new_session_id() -> str:
    return "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), os.getpid())
