"""Giới hạn tài nguyên + ngân sách thời gian (đặc tả 2.3, 5.2, 9.2, 18.4).

Mọi kiểm tra đều trả PB3Error có mã chuẩn thay vì crash/cắt cụt im lặng.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Optional

from .errors import PB3Error


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 8 * 1024 ** 3
    max_entry_bytes: int = 256 * 1024 ** 2
    plan_max_entry_bytes: int = 32 * 1024 * 1024  # plan không đọc entry lớn hơn (X12)
    # Ngưỡng streaming (caps-po): entry Unity lớn hơn ngưỡng này thì mở bằng
    # spool-to-temp + stream_bundle_entries thay vì nạp trọn vào RAM rồi
    # parse_bundle (payload giải nén có thể gấp nhiều lần entry, vd Tom
    # data.unity3d 78 MB -> payload 171 MB). Nhỏ hơn ngưỡng giữ đường cũ.
    stream_entry_bytes: int = 32 * 1024 * 1024
    max_memory_read_bytes: int = 128 * 1024 ** 2
    max_total_uncompressed_bytes: int = 8 * 1024 ** 3
    max_entries: int = 200000
    max_depth: int = 4
    max_ratio: int = 200
    hook_timeout_s: float = 20.0
    hook_timeout_heavy_s: float = 120.0
    chunk_size: int = 1024 ** 2
    workers: int = 1
    disk_factor: float = 2.5

    @classmethod
    def from_config(cls, cfg) -> "Limits":
        names = cls.__dataclass_fields__.keys()   # type: ignore[attr-defined]
        return cls(**{n: cfg.get("limits." + n) for n in names})

    def with_overrides(self, **kw: Any) -> "Limits":
        return replace(self, **kw)

    # ---- kiểm tra (raise PB3Error) ----
    def check_input_size(self, size: int, where: Optional[str] = None) -> None:
        if size > self.max_input_bytes:
            raise PB3Error("E_TOO_LARGE", "đầu vào %d byte > giới hạn %d" % (size, self.max_input_bytes),
                           where=where)

    def check_entry_size(self, size: int, where: Optional[str] = None) -> None:
        if size > self.max_entry_bytes:
            raise PB3Error("E_TOO_LARGE", "entry %d byte > giới hạn %d" % (size, self.max_entry_bytes),
                           where=where)

    def check_memory_read(self, size: int, where: Optional[str] = None) -> None:
        if size > self.max_memory_read_bytes:
            raise PB3Error("E_TOO_LARGE", "đọc vào RAM %d byte > giới hạn %d; hãy dùng streaming"
                           % (size, self.max_memory_read_bytes), where=where)

    def check_entries(self, n: int, where: Optional[str] = None) -> None:
        if n > self.max_entries:
            raise PB3Error("E_ARCHIVE_BOMB", "%d entry > giới hạn %d" % (n, self.max_entries), where=where)

    def check_depth(self, depth: int, where: Optional[str] = None) -> None:
        if depth > self.max_depth:
            raise PB3Error("E_ARCHIVE_BOMB", "container lồng %d tầng > giới hạn %d" % (depth, self.max_depth),
                           where=where)

    def check_total(self, total: int, where: Optional[str] = None) -> None:
        if total > self.max_total_uncompressed_bytes:
            raise PB3Error("E_ARCHIVE_BOMB", "tổng giải nén %d byte > giới hạn %d"
                           % (total, self.max_total_uncompressed_bytes), where=where)

    def check_ratio(self, compressed: int, uncompressed: int, where: Optional[str] = None) -> None:
        if compressed <= 0:
            if uncompressed > 0:
                raise PB3Error("E_ARCHIVE_BOMB", "kích thước nén 0 nhưng giải nén %d byte" % uncompressed,
                               where=where)
            return
        if uncompressed / float(compressed) > self.max_ratio:
            raise PB3Error("E_ARCHIVE_BOMB", "tỉ lệ nén %.0f:1 > giới hạn %d:1"
                           % (uncompressed / float(compressed), self.max_ratio), where=where)

    def required_free_bytes(self, expected: int) -> int:
        return int(max(0, expected) * self.disk_factor)

    def hook_timeout(self, heavy: bool = False) -> float:
        return self.hook_timeout_heavy_s if heavy else self.hook_timeout_s


class TimeBudget:
    """Ngân sách thời gian dựa trên đồng hồ đơn điệu (không bị đổi giờ hệ thống)."""

    def __init__(self, seconds: Optional[float], *, plugin_id: Optional[str] = None,
                 clock=time.monotonic):
        self._clock = clock
        self.seconds = seconds
        self.plugin_id = plugin_id
        self._t0 = clock()

    def elapsed(self) -> float:
        return self._clock() - self._t0

    def remaining(self) -> float:
        return float("inf") if self.seconds is None else self.seconds - self.elapsed()

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, where: Optional[str] = None) -> None:
        if self.expired:
            raise PB3Error("E_PLUGIN_TIMEOUT", "quá %.1fs" % (self.seconds or 0),
                           plugin_id=self.plugin_id, where=where)


def peak_memory_kb() -> Optional[int]:
    """RSS đỉnh của tiến trình (KB) nếu hệ điều hành cho biết; không thì None."""
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        import sys
        return int(v // 1024) if sys.platform == "darwin" else int(v)
    except Exception:
        return None


def read_meminfo() -> dict:
    """Đọc MemTotal/MemAvailable từ /proc/meminfo (KB) nếu có; rỗng nếu không."""
    out = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as f:
            for line in f:
                k, _, rest = line.partition(":")
                if k in ("MemTotal", "MemAvailable", "MemFree"):
                    out[k] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    return out
