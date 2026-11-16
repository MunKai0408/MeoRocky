"""Băm streaming (sha256) + mô tả an toàn cho log (chỉ hash/kích thước, không nội dung)."""
from __future__ import annotations

import hashlib
import os
from typing import Optional

DEFAULT_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, *, chunk_size: int = DEFAULT_CHUNK, cancel=None, on_bytes=None) -> str:
    """Băm file theo từng khối. cancel: đối tượng có .check(); on_bytes(n) báo tiến độ."""
    from .errors import error_from_exception, PB3Error
    h = hashlib.sha256()
    try:
        with open(str(path), "rb") as f:
            while True:
                if cancel is not None:
                    cancel.check(str(path))
                b = f.read(chunk_size)
                if not b:
                    break
                h.update(b)
                if on_bytes is not None:
                    on_bytes(len(b))
    except PB3Error:
        raise
    except OSError as exc:
        raise error_from_exception(exc, where=str(path))
    return h.hexdigest()


def short(digest: str, n: int = 8) -> str:
    return digest[:n]


DESCRIBE_PREFIX_BYTES = 65536


def describe_bytes(data, prefix_bytes: int = DESCRIBE_PREFIX_BYTES) -> str:
    """Thay nội dung nhị phân trong log bằng độ dài + sha8 của N byte ĐẦU
    (bounded: không băm cả blob lớn chỉ để ghi log). Nhãn 'sha8-prefix' nói rõ
    đây là hash tiền tố, không phải hash toàn bộ."""
    b = bytes(data)
    head = b[:prefix_bytes]
    return "<bytes len=%d sha8-prefix=%s>" % (len(b), short(sha256_bytes(head)))


def describe_file(path, *, with_hash: bool = False, chunk_size: int = DEFAULT_CHUNK) -> str:
    """'<tên> size=N [sha8=...]' - chỉ tên cuối, không nội dung. Băm chỉ khi yêu cầu (tốn thời gian)."""
    p = str(path)
    try:
        size: Optional[int] = os.stat(p).st_size
    except OSError:
        size = None
    s = "%s size=%s" % (os.path.basename(p), size if size is not None else "?")
    if with_hash and size is not None:
        try:
            s += " sha8=" + short(sha256_file(p, chunk_size=chunk_size))
        except Exception:
            s += " sha8=?"
    return s
