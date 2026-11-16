"""LZ4 thuần Python (Claude 05; đặc tả 6.3C).

- Block LZ4: giải mã đầy đủ (kiểm tra offset/kích thước), mã hóa dạng
  literal-only (hợp lệ nhưng không nén — phương án dự phòng khi binding
  native không chạy; ghi rõ trong báo cáo).
- Frame LZ4: phát hiện + giải mã (khối nén/không nén, kiểm tra checksum
  nội dung bằng xxhash32 thuần Python khi cờ bật). Không mã hóa frame
  bằng Python thuần (dùng binding `lz4.frame` khi có).
- Chỉ dùng thư viện chuẩn; chạy được trên Python 3.9 (iSH).

Tham chiếu format: LZ4 block (token nibble, literal/match, offset u16 LE),
LZ4 frame (magic 0x184D2204). Đối chiếu `lz4.block`/`lz4.frame` trong test.
"""
from __future__ import annotations

import struct

from core.errors import PB3Error

LZ4_FRAME_MAGIC = b"\x04\x22\x4d\x18"
LZ4_FRAME_MAGIC_INT = 0x184D2204
MAX_BLOCK_SIZE_IDS = {4: 64 * 1024, 5: 256 * 1024, 6: 1024 * 1024, 7: 4 * 1024 * 1024}

# ------------------------------------------------------------------ xxhash32
_P1 = 2654435761
_P2 = 2246822519
_P3 = 3266489917
_P4 = 668265263
_P5 = 374761393
_MASK = 0xFFFFFFFF


def _rotl(x, r):
    return ((x << r) | (x >> (32 - r))) & _MASK


def xxhash32(data, seed=0):
    """xxHash32 thuần Python (dùng kiểm checksum của LZ4 frame)."""
    data = bytes(data)
    n = len(data)
    pos = 0
    if n >= 16:
        v1 = (seed + _P1 + _P2) & _MASK
        v2 = (seed + _P2) & _MASK
        v3 = seed & _MASK
        v4 = (seed - _P1) & _MASK
        while pos + 16 <= n:
            for v_i, k in ((1, 0), (2, 4), (3, 8), (4, 12)):
                lane = struct.unpack_from("<I", data, pos + k)[0]
                if v_i == 1:
                    v1 = (_rotl((v1 + lane * _P2) & _MASK, 13) * _P1) & _MASK
                elif v_i == 2:
                    v2 = (_rotl((v2 + lane * _P2) & _MASK, 13) * _P1) & _MASK
                elif v_i == 3:
                    v3 = (_rotl((v3 + lane * _P2) & _MASK, 13) * _P1) & _MASK
                else:
                    v4 = (_rotl((v4 + lane * _P2) & _MASK, 13) * _P1) & _MASK
            pos += 16
        h = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & _MASK
    else:
        h = (seed + _P5) & _MASK
    h = (h + n) & _MASK
    while pos + 4 <= n:
        h = (_rotl((h + struct.unpack_from("<I", data, pos)[0] * _P3) & _MASK, 17) * _P4) & _MASK
        pos += 4
    while pos < n:
        h = (_rotl((h + data[pos] * _P5) & _MASK, 11) * _P1) & _MASK
        pos += 1
    h ^= h >> 15
    h = (h * _P2) & _MASK
    h ^= h >> 13
    h = (h * _P3) & _MASK
    h ^= h >> 16
    return h & _MASK


# ------------------------------------------------------------------ block
def _read_ext(data, pos):
    """Đọc độ dài mở rộng (byte 255 nối tiếp); trả (giá trị, vị trí mới)."""
    total = 0
    while True:
        if pos >= len(data):
            raise PB3Error("E_CORRUPT", "khối LZ4 cắt cụt ở độ dài mở rộng")
        b = data[pos]
        pos += 1
        total += b
        if b != 255:
            break
    return total, pos


def decompress_block(data, uncompressed_size=None, *, where="lz4-block",
                     max_size=128 * 1024 * 1024):
    """Giải mã một LZ4 block.

    `uncompressed_size` cho trước (bundle Unity): kiểm tra đúng kích thước.
    `None` (block độc lập trong frame, không ghi kích thước): giải mã tới
    hết input, giới hạn bằng `max_size`."""
    data = bytes(data)
    if uncompressed_size is not None and uncompressed_size < 0:
        raise PB3Error("E_CORRUPT", "kích thước giải nén âm", where=where)
    limit = uncompressed_size if uncompressed_size is not None else max_size
    if uncompressed_size == 0:
        if data:
            raise PB3Error("E_CORRUPT", "khối LZ4 có dữ liệu nhưng kích thước 0", where=where)
        return b""
    out = bytearray()
    pos = 0
    n = len(data)
    while True:
        if pos >= n:
            raise PB3Error("E_CORRUPT", "khối LZ4 cắt cụt ở token", where=where)
        token = data[pos]
        pos += 1
        lit_len = token >> 4
        if lit_len == 15:
            ext, pos = _read_ext(data, pos)
            lit_len += ext
        if pos + lit_len > n:
            raise PB3Error("E_CORRUPT", "khối LZ4: literal vượt quá dữ liệu", where=where)
        if len(out) + lit_len > limit:
            raise PB3Error("E_CORRUPT", "khối LZ4: giải nén vượt kích thước cho phép",
                           where=where)
        out += data[pos:pos + lit_len]
        pos += lit_len
        if pos == n:
            break
        if pos + 2 > n:
            raise PB3Error("E_CORRUPT", "khối LZ4 cắt cụt ở offset", where=where)
        offset = data[pos] | (data[pos + 1] << 8)
        pos += 2
        if offset == 0 or offset > len(out):
            raise PB3Error("E_CORRUPT", "khối LZ4: offset không hợp lệ (%d)" % offset,
                           where=where)
        match_len = token & 0x0F
        if match_len == 15:
            ext, pos = _read_ext(data, pos)
            match_len += ext
        match_len += 4
        if len(out) + match_len > limit:
            raise PB3Error("E_CORRUPT", "khối LZ4: match vượt kích thước cho phép",
                           where=where)
        start = len(out) - offset
        if offset >= match_len:
            out += out[start:start + match_len]   # không chồng lấn: sao chép theo lát
        else:
            for i in range(match_len):
                out.append(out[start + i])
    if uncompressed_size is not None and len(out) != uncompressed_size:
        raise PB3Error("E_CORRUPT", "khối LZ4: giải nén được %d/%d byte"
                       % (len(out), uncompressed_size), where=where)
    return bytes(out)


def compress_block_literals(data):
    """Mã hóa LZ4 block dạng literal-only (hợp lệ, không nén).

    Dùng làm fallback khi không có binding lz4; mọi decoder LZ4 chuẩn đều
    đọc được. Ghi rõ trong báo cáo (backend NATIVE / fallback)."""
    data = bytes(data)
    out = bytearray()
    n = len(data)
    if n < 15:
        out.append(n << 4)
    else:
        out.append(0xF0)
        rest = n - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    out += data
    return bytes(out)


# ------------------------------------------------------------------ frame
def is_lz4_frame(data):
    return bytes(data[:4]) == LZ4_FRAME_MAGIC


def decompress_frame(data, where="lz4-frame"):
    """Giải mã một LZ4 frame; kiểm tra checksum nội dung nếu có."""
    data = bytes(data)
    if len(data) < 7 or not is_lz4_frame(data):
        raise PB3Error("E_CORRUPT", "không phải LZ4 frame", where=where)
    pos = 4
    flg = data[pos]
    pos += 1
    if (flg >> 6) != 1:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "LZ4 frame version lạ (%d)" % (flg >> 6),
                       where=where)
    block_independent = bool(flg & 0x20)
    block_checksum = bool(flg & 0x10)
    has_content_size = bool(flg & 0x08)
    has_content_checksum = bool(flg & 0x04)
    has_dict_id = bool(flg & 0x01)
    if flg & 0x02:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "LZ4 frame có bit dự trữ", where=where)
    if pos >= len(data):
        raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở BD", where=where)
    bd = data[pos]
    pos += 1
    max_block = MAX_BLOCK_SIZE_IDS.get((bd >> 4) & 0x07)
    if max_block is None:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "LZ4 frame: cỡ block lạ", where=where)
    content_size = None
    if has_content_size:
        if pos + 8 > len(data):
            raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở content size", where=where)
        content_size = struct.unpack_from("<Q", data, pos)[0]
        pos += 8
    if has_dict_id:
        if pos + 4 > len(data):
            raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở dict id", where=where)
        dict_id = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if dict_id != 0:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "LZ4 frame cần từ điển ngoài (dict_id=%d), chưa hỗ trợ"
                           % dict_id, where=where)
    if pos >= len(data):
        raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở header checksum", where=where)
    header = data[4:pos]
    expect_hc = (xxhash32(header, 0) >> 8) & 0xFF
    if data[pos] != expect_hc:
        raise PB3Error("E_CORRUPT", "LZ4 frame: header checksum sai", where=where)
    pos += 1
    out = bytearray()
    while True:
        if pos + 4 > len(data):
            raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở block size", where=where)
        block_size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if block_size == 0:
            break
        uncompressed_flag = bool(block_size & 0x80000000)
        block_size &= 0x7FFFFFFF
        if block_size > max_block + max_block // 255 + 16 and not uncompressed_flag:
            raise PB3Error("E_CORRUPT", "LZ4 frame: block quá lớn", where=where)
        if pos + block_size > len(data):
            raise PB3Error("E_CORRUPT", "LZ4 frame: block cắt cụt", where=where)
        block = data[pos:pos + block_size]
        pos += block_size
        if block_checksum:
            if pos + 4 > len(data):
                raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở block checksum", where=where)
            expect_bc = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if xxhash32(block, 0) != expect_bc:
                raise PB3Error("E_CORRUPT", "LZ4 frame: block checksum sai", where=where)
        if uncompressed_flag:
            out += block
        elif block_independent:
            # block độc lập không ghi kích thước giải nén: giải tới hết input
            out += decompress_block(block, None, where=where, max_size=max_block)
        else:
            # khối phụ thuộc: giải nén với toàn bộ output làm từ điển
            out += _decompress_block_linked(block, bytes(out), where=where)
    if has_content_checksum:
        if pos + 4 > len(data):
            raise PB3Error("E_CORRUPT", "LZ4 frame cắt cụt ở content checksum", where=where)
        expect_cc = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if xxhash32(bytes(out), 0) != expect_cc:
            raise PB3Error("E_CORRUPT", "LZ4 frame: content checksum sai", where=where)
    if content_size is not None and len(out) != content_size:
        raise PB3Error("E_CORRUPT", "LZ4 frame: giải nén được %d/%d byte"
                       % (len(out), content_size), where=where)
    return bytes(out)


def _decompress_block_linked(data, prefix, where="lz4-frame"):
    """Giải mã LZ4 block mà match có thể trỏ về dữ liệu của block trước."""
    data = bytes(data)
    out = bytearray(prefix)
    base = len(prefix)
    pos = 0
    n = len(data)
    while True:
        if pos >= n:
            break
        token = data[pos]
        pos += 1
        lit_len = token >> 4
        if lit_len == 15:
            ext, pos = _read_ext(data, pos)
            lit_len += ext
        if pos + lit_len > n:
            raise PB3Error("E_CORRUPT", "khối LZ4: literal vượt quá dữ liệu", where=where)
        out += data[pos:pos + lit_len]
        pos += lit_len
        if pos == n:
            break
        if pos + 2 > n:
            raise PB3Error("E_CORRUPT", "khối LZ4 cắt cụt ở offset", where=where)
        offset = data[pos] | (data[pos + 1] << 8)
        pos += 2
        if offset == 0 or offset > len(out):
            raise PB3Error("E_CORRUPT", "khối LZ4: offset không hợp lệ (%d)" % offset,
                           where=where)
        match_len = token & 0x0F
        if match_len == 15:
            ext, pos = _read_ext(data, pos)
            match_len += ext
        match_len += 4
        start = len(out) - offset
        if offset >= match_len:
            out += out[start:start + match_len]   # không chồng lấn: sao chép theo lát
        else:
            for i in range(match_len):
                out.append(out[start + i])
    return bytes(out[base:])
