"""Builder xác định cho fixture Godot (Claude 08, nhóm Godot).

Xây file PCK v1 (Godot 3) / v2 (Godot 4) và file .translation nhị phân
(magic "TR") trong RAM — KHÔNG commit file nhị phân lạ, test tự dựng
fixture khi chạy. Chỉ dùng thư viện chuẩn; dữ liệu giả ngẫu nhiên (khi
cần) dùng random.Random với seed, KHÔNG dùng os.urandom (decisions #143).

Layout PCK (đối chiếu tài liệu GodotPckTool / Godot file_access_pack):
  header LE: magic "GDPC", pack_version u32, godot major/minor/patch u32,
    v2+: flags u32, file_base u64; reserved u32[16] (64 byte 0).
    -> v1: 84 byte; v2: 96 byte.
  thư mục ngay sau header: số entry u32, rồi mỗi entry:
    path_len u32 (độ dài đường dẫn ĐÃ CĂN 4 byte) + path UTF-8 đệm NUL,
    offset u64, size u64, md5 16 byte, v2+: flags u32.
  v1: offset TUYỆT ĐỐI. v2: offset TƯƠNG ĐỐI theo file_base (đầu vùng dữ
    liệu, căn 32 byte); dữ liệu mỗi entry căn 32 byte.
  flags pack: PACK_DIR_ENCRYPTED = 1<<0, PACK_FILE_RELATIVE_BASE = 1<<1.
  flags entry: FILE_ENCRYPTED = 1<<0, FILE_DELETED = 1<<1.

Layout .translation (theo nhiệm vụ Claude 08):
  magic "TR" (2 byte), version u32 LE (= 1), locale: u32 độ dài + UTF-8,
  count u32 LE, rồi mỗi entry: msgid_len u32 + msgid UTF-8,
  msgstr_len u32 + msgstr UTF-8.
"""
from __future__ import annotations

import hashlib
import random
import struct

PACK_DIR_ENCRYPTED = 1 << 0
PACK_FILE_RELATIVE_BASE = 1 << 1
FILE_ENCRYPTED = 1 << 0
FILE_DELETED = 1 << 1

PCK_MAGIC = b"GDPC"
TR_MAGIC = b"TR"
TR_VERSION = 1


def _align(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def _padded_path(path: str) -> bytes:
    raw = path.encode("utf-8")
    padded_len = _align(len(raw), 4)
    return struct.pack("<I", padded_len) + raw + b"\x00" * (padded_len - len(raw))


def build_pck(entries, *, pack_version: int = 2,
              godot_version=(4, 2, 1), pack_flags: int = 0) -> bytes:
    """entries: list[(path, data)] hoặc [(path, data, entry_flags)].

    v1: godot_version mặc định (3, 5, 2) nếu không truyền; v2: (4, 2, 1).
    pack_flags: cờ mức pack (vd PACK_DIR_ENCRYPTED — thư mục vẫn ghi
    dạng rõ để test đọc được cờ, vì thư mục mã hóa thật không giải mã
    được và không có fixture thật)."""
    if pack_version not in (1, 2):
        raise ValueError("build_pck chỉ hỗ trợ pack_version 1/2")
    norm = []
    for item in entries:
        if len(item) == 2:
            path, data = item
            flags = 0
        else:
            path, data, flags = item
        norm.append((path, bytes(data), flags))
    major, minor, patch = godot_version
    header_size = 84 if pack_version == 1 else 96
    dir_size = 4
    for path, _data, _flags in norm:
        dir_size += 4 + _align(len(path.encode("utf-8")), 4) + 8 + 8 + 16
        if pack_version == 2:
            dir_size += 4
    if pack_version == 1:
        file_base = 0
        data_start = header_size + dir_size
    else:
        data_start = _align(header_size + dir_size, 32)
        file_base = data_start
    # Tính offset từng entry (dữ liệu căn 32 byte ở v2).
    offsets = []
    pos = data_start
    for _path, data, _flags in norm:
        if pack_version == 2:
            pos = _align(pos, 32)
        offsets.append(pos - file_base if pack_version == 2 else pos)
        pos += len(data)
    out = bytearray()
    out += PCK_MAGIC
    out += struct.pack("<IIII", pack_version, major, minor, patch)
    if pack_version == 2:
        flags = pack_flags | PACK_FILE_RELATIVE_BASE
        out += struct.pack("<IQ", flags, file_base)
    out += b"\x00" * 64
    assert len(out) == header_size, (len(out), header_size)
    out += struct.pack("<I", len(norm))
    for (path, data, flags), off in zip(norm, offsets):
        out += _padded_path(path)
        out += struct.pack("<QQ", off, len(data))
        out += hashlib.md5(data).digest()
        if pack_version == 2:
            out += struct.pack("<I", flags)
    if pack_version == 2:
        out += b"\x00" * (data_start - len(out))
    for (path, data, _flags), off in zip(norm, offsets):
        abs_off = file_base + off if pack_version == 2 else off
        if pack_version == 2:
            out += b"\x00" * (abs_off - len(out))
        assert len(out) == abs_off, (len(out), abs_off)
        out += data
    return bytes(out)


def build_pck_v1(entries, godot_version=(3, 5, 2)) -> bytes:
    """PCK Godot 3 (pack_version 1, offset tuyệt đối)."""
    return build_pck(entries, pack_version=1, godot_version=godot_version)


def build_pck_v2(entries, godot_version=(4, 2, 1), pack_flags: int = 0) -> bytes:
    """PCK Godot 4 (pack_version 2, offset tương đối theo file_base)."""
    return build_pck(entries, pack_version=2, godot_version=godot_version,
                     pack_flags=pack_flags)


def build_translation(pairs, locale: str = "vi") -> bytes:
    """pairs: list[(msgid, msgstr)] — chuỗi UTF-8, giữ đúng thứ tự."""
    loc = locale.encode("utf-8")
    out = bytearray()
    out += TR_MAGIC
    out += struct.pack("<I", TR_VERSION)
    out += struct.pack("<I", len(loc)) + loc
    out += struct.pack("<I", len(pairs))
    for msgid, msgstr in pairs:
        a = msgid.encode("utf-8")
        b = msgstr.encode("utf-8")
        out += struct.pack("<I", len(a)) + a
        out += struct.pack("<I", len(b)) + b
    return bytes(out)


def deterministic_bytes(n: int, seed: int = 1) -> bytes:
    """n byte giả ngẫu nhiên xác định (không nén được), seed cố định.

    Dùng random.Random, KHÔNG dùng os.urandom (decisions #143: urandom
    đọc lớn lỗi EIO trên iSH)."""
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(n))
