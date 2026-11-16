"""Giao diện hệ thống file ảo (đặc tả 5.1, 4.4). Plugin CHỈ đọc qua giao diện này; không có hàm ghi.

Đường dẫn ảo là chuỗi, các tầng container nối bằng '::' :
    "Payload/Game.app/Data/xxx.unity3d::resources.assets"
Mỗi đoạn tương đối, dùng '/', cấm '..', '.', đoạn rỗng, '\\', NUL, ':', đường dẫn tuyệt đối (E_UNSAFE_PATH).

Quy ước `read(path, max_bytes)` (ghi nhận ở handoff/decisions.md, vì Phụ lục B dùng cả hai kiểu):
  - max_bytes nhỏ hơn limits.max_entry_bytes  -> đọc TIỀN TỐ tối đa max_bytes byte (đọc "đầu file").
  - max_bytes None hoặc >= limits.max_entry_bytes -> đọc NGUYÊN entry; entry lớn hơn max_entry_bytes -> E_TOO_LARGE
    (không bao giờ cắt cụt im lặng).
  - Số byte thực nạp vào RAM luôn <= limits.max_memory_read_bytes, vượt -> E_TOO_LARGE (hãy dùng slice/open_stream).
VFS thật do Claude 04 cài; MemoryVFS là bản tham chiếu cho test và ví dụ.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

from core.errors import PB3Error
from core.limits import Limits
from core.model import _rel_ok

SEP = "::"


def check_vpath(path: str) -> str:
    """Kiểm tra đường dẫn ảo; trả lại chính nó hoặc raise E_UNSAFE_PATH."""
    if not isinstance(path, str) or not path:
        raise PB3Error("E_UNSAFE_PATH", "đường dẫn ảo rỗng hoặc không phải chuỗi")
    for part in path.split(SEP):
        _rel_ok(part, "đường dẫn ảo")
        if ":" in part:                        # ':' đơn lẻ gây mơ hồ với dấu tách '::'
            raise PB3Error("E_UNSAFE_PATH", "đường dẫn ảo chứa ':' trong một đoạn", where=path)
    return path


def vpath_split(path: str):
    """'a.ipa::b/c' -> ['a.ipa', 'b/c'] (ngoài -> trong)."""
    return check_vpath(path).split(SEP)


def vpath_join(*parts: str) -> str:
    if not parts:
        raise PB3Error("E_UNSAFE_PATH", "vpath_join không có đoạn nào")
    return check_vpath(SEP.join(parts))


@dataclass(frozen=True)
class VStat:
    path: str
    size: int
    crc_verified: bool = False       # 5.2: slice/stream chưa xác thực CRC -> False


def read_policy(size: int, max_bytes: Optional[int], limits: Limits, where: str) -> int:
    """Số byte được phép đọc cho `read()`; raise E_TOO_LARGE khi vi phạm (xem quy ước ở đầu file)."""
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        raise PB3Error("E_PLUGIN_INVALID", "max_bytes phải là int >= 0 hoặc None", where=where)
    if max_bytes is None or max_bytes >= limits.max_entry_bytes:
        limits.check_entry_size(size, where=where)
        take = size
    else:
        take = min(size, max_bytes)
    limits.check_memory_read(take, where=where)
    return take


def _int_arg(v, name, where):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise PB3Error("E_PLUGIN_INVALID", "%s phải là int >= 0" % name, where=where)
    return v


class VFS:
    """Giao diện đọc. Lớp cài đặt ghi đè 5 hàm đầu; `exists`/`read_entry` dựng trên chúng."""

    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        raise PB3Error("E_NOT_IMPLEMENTED", "VFS.iter_entries")

    def stat(self, path: str) -> VStat:
        raise PB3Error("E_NOT_IMPLEMENTED", "VFS.stat")

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        raise PB3Error("E_NOT_IMPLEMENTED", "VFS.read")

    def slice(self, path: str, offset: int, size: int) -> bytes:
        """Đọc `size` byte từ `offset` (không kiểm CRC). Giới hạn RAM tính
        theo số byte THỰC ĐỌC (min(size, còn lại sau offset)), giống
        MemoryVFS.slice và LimitedVFS.slice (đã thống nhất)."""
        raise PB3Error("E_NOT_IMPLEMENTED", "VFS.slice")

    def open_stream(self, path: str):
        """Trả đối tượng nhị phân chỉ đọc: read(n), seek, tell, close, with-statement. Không nạp cả file."""
        raise PB3Error("E_NOT_IMPLEMENTED", "VFS.open_stream")

    # ---- tiện ích dựng trên giao diện ----
    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except PB3Error as e:
            if e.code == "E_INPUT_NOT_FOUND":
                return False
            raise

    def read_entry(self, path: str) -> bytes:
        """Đọc nguyên entry (max_bytes=None)."""
        return self.read(path, None)


class _ReadOnlyStream(io.BytesIO):
    def writable(self) -> bool:
        return False

    def write(self, b):                                        # pragma: no cover - đường lỗi
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc")

    def getbuffer(self):                                       # ghi qua buffer cũng bị chặn
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc (getbuffer bị chặn)")


class MemoryVFS(VFS):
    """VFS trong RAM: files = {đường_dẫn_ảo: bytes}. Chỉ đọc."""

    def __init__(self, files: Optional[Dict[str, bytes]] = None, limits: Optional[Limits] = None):
        self.limits = limits or Limits()
        self._files: Dict[str, bytes] = {}
        for p, d in (files or {}).items():
            check_vpath(p)
            if not isinstance(d, (bytes, bytearray)):
                raise PB3Error("E_PLUGIN_INVALID", "MemoryVFS chỉ nhận bytes", where=p)
            self._files[p] = bytes(d)

    def _get(self, path: str) -> bytes:
        check_vpath(path)
        if path not in self._files:
            raise PB3Error("E_INPUT_NOT_FOUND", "không có trong VFS", where=path)
        return self._files[path]

    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        for p in sorted(self._files):
            if prefix is None or p.startswith(prefix):
                yield p

    def stat(self, path: str) -> VStat:
        return VStat(path, len(self._get(path)), False)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        data = self._get(path)
        take = read_policy(len(data), max_bytes, self.limits, path)
        return data[:take]

    def slice(self, path: str, offset: int, size: int) -> bytes:
        data = self._get(path)
        _int_arg(offset, "offset", path)
        _int_arg(size, "size", path)
        self.limits.check_memory_read(min(size, max(0, len(data) - offset)), where=path)
        return data[offset:offset + size]

    def open_stream(self, path: str):
        return _ReadOnlyStream(self._get(path))


class _CountingStream(io.RawIOBase):
    """Bọc stream chỉ đọc: đếm tổng byte đã đọc; vượt max_entry_bytes thì
    E_TOO_LARGE (plugin không đọc lén cả entry lớn qua open_stream)."""

    def __init__(self, inner, limit: int, where: str):
        self._inner = inner
        self._limit = limit
        self._where = where
        self._read = 0

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        try:
            return bool(self._inner.seekable())
        except (OSError, ValueError):
            return False

    def read(self, n: int = -1) -> bytes:
        data = self._inner.read(n)
        self._read += len(data)
        if self._read > self._limit:
            raise PB3Error("E_TOO_LARGE", "đọc qua stream vượt %d byte" % self._limit,
                           where=self._where)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()

    def write(self, b):
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc")

    def close(self) -> None:
        try:
            self._inner.close()
        except (OSError, ValueError):
            pass
        super().close()


class LimitedVFS(VFS):
    """Bọc một VFS bằng limits của plugin (đã hạ theo manifest). Plugin không thể nới limit của ctx."""

    def __init__(self, inner: VFS, limits: Limits):
        if not isinstance(inner, VFS):
            raise PB3Error("E_PLUGIN_INVALID", "LimitedVFS cần một VFS")
        self._inner = inner
        self.limits = limits

    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        return self._inner.iter_entries(prefix)

    def stat(self, path: str) -> VStat:
        check_vpath(path)
        return self._inner.stat(path)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        size = self.stat(path).size
        take = read_policy(size, max_bytes, self.limits, path)
        return self._inner.read(path, take)

    def slice(self, path: str, offset: int, size: int) -> bytes:
        check_vpath(path)
        _int_arg(offset, "offset", path)
        _int_arg(size, "size", path)
        # Thống nhất với MemoryVFS.slice: chỉ tính số byte THỰC ĐỌC.
        size_total = self.stat(path).size
        self.limits.check_memory_read(min(size, max(0, size_total - offset)), where=path)
        return self._inner.slice(path, offset, size)

    def open_stream(self, path: str):
        check_vpath(path)
        stream = self._inner.open_stream(path)
        return _CountingStream(stream, self.limits.max_entry_bytes, path)
