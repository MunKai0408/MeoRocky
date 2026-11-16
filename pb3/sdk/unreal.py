"""Unreal PAK (UE4/UE5) — VFS chỉ đọc, thuần Python (Claude 08, nhóm Unreal).

Định dạng (đối chiếu repak / rust-unreal-unpak và tài liệu công khai về
FPakInfo/FPakEntry; không dùng mã của các dự án đó, chỉ dùng đặc tả):
  file = [entry: header chuẩn + dữ liệu]... + [index] + [footer]
  footer (cuối file, little-endian, magic 0x5A6F12E1):
    V2-V3 (44 byte): magic u32, version i32, index_offset i64,
        index_size i64, index_hash 20 byte.
    V4-V6 (45 byte): + bEncryptedIndex u8 ở đầu.
    V7 (61 byte): + encryption_guid 16 byte ở đầu.
    V8A (189 byte): + 4 tên nén, mỗi tên 32 byte đệm NUL.
    V8B-V11 (221 byte): + 5 tên nén (32 byte mỗi tên).
    V9 frozen (222 byte): như V8B + cờ frozen u8 sau index_hash.
  index: mount point (FString), số entry i32, rồi mỗi entry:
    FString đường dẫn + FPakEntry.
  FPakEntry chuẩn (V1-V9): offset u64, compressed u64, uncompressed u64,
    compression (V8A: u8, còn lại: u32), timestamp u64 (chỉ V1),
    hash SHA-1 20 byte, rồi nếu version >= V3: nếu nén thì mảng block
    (u32 số lượng + mỗi block i64 start + i64 end), flags u8,
    compression_block_size u32.
    Trước V8, compression là bitmask cũ (0=không nén, 1=Zlib, 2=Gzip,
    4=Oodle); từ V8 là slot+1 (0=không nén, 1=slot đầu trong footer).
  FPakEntry mã hóa bit-packed (V10/V11, chỉ trong index): 1 từ u32 gồm
    cờ (block size 6 bit, số block 16 bit, mã hóa bit 22, slot nén+1
    6 bit, cờ u32/u64 cho offset/uncompressed/compressed), rồi các số
    nguyên có độ dài theo cờ, rồi kích thước block (nếu > 1 block hoặc
    mã hóa). Header trước dữ liệu trong file VẪN ở dạng chuẩn.
  FString: i32 độ dài; 0 = rỗng; > 0 = số byte UTF-8/ANSI (kể cả NUL
    cuối); < 0 = số đơn vị mã UTF-16LE (kể cả NUL cuối).

Phiên bản đã kiểm chứng bằng fixture (builder trong file này):
  V8B (UE 4.23-4.24) và V11 (UE 4.26-5.3). Các version 1-7, 8A, 9, 10
  đọc bằng cùng đường parse (entry chuẩn, hoặc entry mã hóa với V10)
  nhưng chưa có fixture nên không tuyên bố hỗ trợ chính thức; footer
  sai/version ngoài 1-11 -> E_BAD_ARCHIVE.

Giới hạn (ghi rõ, không giấu):
  - CHỈ đọc entry KHÔNG NÉN (compression 0) và KHÔNG MÃ HÓA. Entry nén
    (kể cả Zlib — stdlib có zlib nhưng ngoài phạm vi nhiệm vụ này) hoặc
    mã hóa (cờ encrypted, index mã hóa) hoặc đã xóa thì bị LOẠI khỏi
    VFS, ghi vào inventory.rejected với lý do rõ; đọc chúng báo lỗi
    (E_UNSUPPORTED_FORMAT / E_ENCRYPTED / E_INPUT_NOT_FOUND) chứ không
    crash. TUYỆT ĐỐI KHÔNG tìm cách vượt mã hóa hay giải mã Oodle.
  - Index mã hóa (cờ bEncryptedIndex trong footer) -> E_ENCRYPTED ngay
    khi mở (không đọc được cả mục lục).
  - Không xác thực hash SHA-1 của entry khi đọc (giống UE/repak: hash
    chỉ để tham chiếu); hash SHA-1 của index không khớp footer chỉ ghi
    cảnh báo trong inventory, không chặn mở.
  - VFS không có thư mục (PAK không lưu entry thư mục): stat() một
    tiền tố thư mục -> E_INPUT_NOT_FOUND.
  - Tên entry được chuẩn hóa (bỏ tiền tố "../" của mount point, bỏ "/"
    đầu); tên không an toàn/trùng thì bị loại kèm lý do.
  - Số entry, tổng byte, kích thước index tuân Limits (E_ARCHIVE_BOMB /
    E_TOO_LARGE); file quá lớn theo max_input_bytes -> E_TOO_LARGE.

Hàm `build_pak` ở cuối file CHỈ để dựng fixture kiểm thử trong RAM
(không phải trình ghi PAK sản phẩm: không nén thật ngoài Zlib của
stdlib cho fixture, không mã hóa thật — entry "mã hóa" chỉ mang cờ).
Chỉ dùng thư viện chuẩn, Python >= 3.8. Không import UnityPy/lz4,
không mạng, không tiến trình con.
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

from core import safe_io
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from sdk.containers import ContainerInventory, EntryInfo
from sdk.vfs import VFS, VStat, check_vpath, read_policy

PAK_MAGIC = 0x5A6F12E1
PAK_KIND = "unreal_pak"

# Tên nén mặc định của các version trước V8 (footer không lưu tên).
_DEFAULT_COMPRESSION = ("Zlib", "Gzip", "Oodle")

# Version footer đã có fixture kiểm chứng (xem build_pak + tests/test_unreal.py).
_FIXTURE_VERSIONS = (8, 11)

_VERSION_NAMES = {
    1: "V1 (Initial)",
    2: "V2 (NoTimestamps, UE 4.0-4.2)",
    3: "V3 (CompressionEncryption, UE 4.3-4.15)",
    4: "V4 (IndexEncryption, UE 4.16-4.19)",
    5: "V5 (RelativeChunkOffsets, UE 4.20)",
    6: "V6 (DeleteRecords, UE 4.21)",
    7: "V7 (EncryptionKeyGuid, UE 4.22)",
    8: "V8 (FNameBasedCompression, UE 4.22-4.25)",
    9: "V9 (FrozenIndex, UE 4.25)",
    10: "V10 (PathHashIndex, UE 4.26)",
    11: "V11 (Fnv64BugFix, UE 4.26-5.3)",
}

# (cỡ footer, version bắt buộc; None = kiểm theo dải ở _parse_footer)
_FOOTER_TRIES = (222, 221, 189, 61, 45, 44)


# ------------------------------------------------------------------ FString
def read_fstring(data: bytes, pos: int, where: str = "") -> Tuple[str, int]:
    """Đọc FString của Unreal tại `pos`: trả (chuỗi, vị trí mới).

    i32 độ dài: 0 = rỗng; > 0 = số byte (kể cả byte NUL cuối), giải mã
    UTF-8, rơi về latin-1 nếu không phải UTF-8; < 0 = số đơn vị mã
    UTF-16LE (kể cả NUL cuối). Cắt cụt/lỗi -> E_CORRUPT."""
    if pos < 0 or pos + 4 > len(data):
        raise PB3Error("E_CORRUPT", "FString cắt cụt (thiếu độ dài)", where=where)
    (n,) = struct.unpack_from("<i", data, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n > 0:
        if pos + n > len(data):
            raise PB3Error("E_CORRUPT", "FString cắt cụt (thiếu %d byte)" % n, where=where)
        raw = data[pos:pos + n]
        pos += n
        if raw.endswith(b"\x00"):
            raw = raw[:-1]
        try:
            return raw.decode("utf-8"), pos
        except UnicodeDecodeError:
            return raw.decode("latin-1"), pos
    count = -n
    need = count * 2
    if pos + need > len(data):
        raise PB3Error("E_CORRUPT", "FString UTF-16 cắt cụt", where=where)
    raw = data[pos:pos + need]
    pos += need
    if raw.endswith(b"\x00\x00"):
        raw = raw[:-2]
    try:
        return raw.decode("utf-16-le"), pos
    except UnicodeDecodeError:
        raise PB3Error("E_CORRUPT", "FString UTF-16 lỗi", where=where)


def write_fstring(text: str, *, utf16: bool = False) -> bytes:
    """Ghi FString (kiểu UE viết): ASCII -> UTF-8, độ dài dương; có ký tự
    ngoài ASCII -> UTF-16LE, độ dài âm. Độ dài luôn kể cả ký tự NUL cuối.
    `utf16=True` ép viết UTF-16 (vd tên namespace trong .locres legacy)."""
    if utf16:
        raw = text.encode("utf-16-le")
        units = len(raw) // 2 + 1
        return struct.pack("<i", -units) + raw + b"\x00\x00"
    try:
        raw = text.encode("ascii")
        return struct.pack("<i", len(raw) + 1) + raw + b"\x00"
    except UnicodeEncodeError:
        raw = text.encode("utf-16-le")
        units = len(raw) // 2 + 1
        return struct.pack("<i", -units) + raw + b"\x00\x00"


class _Reader:
    """Con trỏ đọc trên bytes, lỗi cắt cụt -> E_CORRUPT."""

    def __init__(self, data: bytes, where: str = ""):
        self._data = data
        self._pos = 0
        self._where = where

    def tell(self) -> int:
        return self._pos

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def _need(self, n: int) -> None:
        if self._pos + n > len(self._data):
            raise PB3Error("E_CORRUPT", "dữ liệu cắt cụt (cần %d byte, còn %d)"
                           % (n, self.remaining()), where=self._where)

    def u8(self) -> int:
        self._need(1)
        v = self._data[self._pos]
        self._pos += 1
        return v

    def u32(self) -> int:
        self._need(4)
        (v,) = struct.unpack_from("<I", self._data, self._pos)
        self._pos += 4
        return v

    def i32(self) -> int:
        self._need(4)
        (v,) = struct.unpack_from("<i", self._data, self._pos)
        self._pos += 4
        return v

    def u64(self) -> int:
        self._need(8)
        (v,) = struct.unpack_from("<Q", self._data, self._pos)
        self._pos += 8
        return v

    def i64(self) -> int:
        self._need(8)
        (v,) = struct.unpack_from("<q", self._data, self._pos)
        self._pos += 8
        return v

    def bytes(self, n: int) -> bytes:
        self._need(n)
        v = self._data[self._pos:self._pos + n]
        self._pos += n
        return v

    def fstring(self) -> str:
        s, self._pos = read_fstring(self._data, self._pos, self._where)
        return s


# ------------------------------------------------------------------- footer
@dataclass
class PakFooter:
    version: int
    index_offset: int
    index_size: int
    index_hash: bytes
    encrypted_index: bool
    encryption_guid: bytes
    frozen: bool
    compression: List[Optional[str]]
    footer_size: int

    @property
    def version_name(self) -> str:
        return _VERSION_NAMES.get(self.version, "V%d" % self.version)

    @property
    def is_v8a(self) -> bool:
        """V8A: footer 189 byte, compression trong entry chỉ 1 byte."""
        return self.version == 8 and self.footer_size == 189


def _parse_footer(tail: bytes, file_size: int, where: str) -> PakFooter:
    """Thử từng cỡ footer (lớn -> nhỏ): magic + version + phạm vi index
    phải khớp; không cỡ nào khớp -> E_BAD_ARCHIVE."""
    for size in _FOOTER_TRIES:
        if file_size < size or len(tail) < size:
            continue
        r = _Reader(tail[len(tail) - size:], where)
        guid = b"\x00" * 16
        encrypted = False
        if size >= 61:
            guid = r.bytes(16)
        if size >= 45:
            encrypted = r.u8() != 0
        magic = r.u32()
        if magic != PAK_MAGIC:
            continue
        version = r.i32()
        if size == 222 and version != 9:
            continue
        if size == 221 and version not in (8, 10, 11):
            continue
        if size == 189 and version != 8:
            continue
        if size == 61 and version != 7:
            continue
        if size == 45 and version not in (4, 5, 6):
            continue
        if size == 44 and version not in (1, 2, 3):
            continue
        if version < 1 or version > 11:
            continue
        index_offset = r.i64()
        index_size = r.i64()
        index_hash = r.bytes(20)
        if index_offset < 0 or index_size < 0:
            continue
        if index_offset + index_size > file_size:
            continue
        frozen = False
        if size == 222:
            frozen = r.u8() != 0
        compression: List[Optional[str]] = []
        if size >= 189:
            slots = 4 if size == 189 else 5
            for _ in range(slots):
                raw = r.bytes(32)
                name = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
                compression.append(name or None)
        if r.remaining() != 0:
            continue
        return PakFooter(version, index_offset, index_size, index_hash,
                         encrypted, guid, frozen, compression, size)
    raise PB3Error("E_BAD_ARCHIVE",
                   "không phải file PAK (footer sai: magic/version/phạm vi index không khớp)",
                   where=where)


# -------------------------------------------------------------------- entry
@dataclass
class _PakEntry:
    name: str = ""
    orig_name: str = ""
    offset: int = 0
    compressed: int = 0
    uncompressed: int = 0
    compression_slot: Optional[int] = None
    compression_name: Optional[str] = None   # None = không nén
    flags: int = 0
    blocks: Optional[List[Tuple[int, int]]] = None
    block_size: int = 0
    digest: Optional[bytes] = None           # SHA-1 20 byte (None với entry mã hóa V10+)
    timestamp: Optional[int] = None
    header_size: Optional[int] = None        # kích thước header chuẩn trước dữ liệu

    @property
    def is_encrypted(self) -> bool:
        return bool(self.flags & 1)

    @property
    def is_deleted(self) -> bool:
        return bool(self.flags & 2)


def _slot_name(footer: PakFooter, slot: int) -> str:
    if 0 <= slot < len(footer.compression):
        name = footer.compression[slot]
        if name:
            return name
    return "slot %d (không rõ tên)" % slot


def _parse_standard_entry(r: _Reader, footer: PakFooter) -> _PakEntry:
    """FPakEntry dạng chuẩn (V1-V9 trong index; mọi version trước dữ liệu)."""
    e = _PakEntry()
    e.offset = r.u64()
    e.compressed = r.u64()
    e.uncompressed = r.u64()
    raw = r.u8() if footer.is_v8a else r.u32()
    if footer.version >= 8:
        if raw == 0:
            e.compression_slot, e.compression_name = None, None
        else:
            e.compression_slot = raw - 1
            e.compression_name = _slot_name(footer, e.compression_slot)
    else:
        # Trước V8: bitmask cũ (0=không nén, 1=Zlib, 2=Gzip, 4=Oodle).
        if raw == 0:
            e.compression_slot, e.compression_name = None, None
        elif raw == 1:
            e.compression_slot, e.compression_name = 0, "Zlib"
        elif raw == 2:
            e.compression_slot, e.compression_name = 1, "Gzip"
        elif raw == 4:
            e.compression_slot, e.compression_name = 2, "Oodle"
        else:
            e.compression_slot, e.compression_name = None, "flags=0x%02x (không rõ)" % raw
    if footer.version == 1:
        e.timestamp = r.u64()
    e.digest = r.bytes(20)
    if footer.version >= 3:
        if e.compression_name is not None:
            count = r.i32()
            if count < 0 or count > 1000000:
                raise PB3Error("E_CORRUPT", "số block nén %d bất thường" % count, where=r._where)
            e.blocks = [(r.i64(), r.i64()) for _ in range(count)]
        e.flags = r.u8()
        e.block_size = r.u32()
    return e


def _parse_encoded_entry(r: _Reader, footer: PakFooter) -> _PakEntry:
    """FPakEntry mã hóa bit-packed (V10/V11, chỉ trong index)."""
    e = _PakEntry()
    bits = r.u32()
    raw = (bits >> 23) & 0x3F
    if raw == 0:
        e.compression_slot, e.compression_name = None, None
    else:
        e.compression_slot = raw - 1
        e.compression_name = _slot_name(footer, e.compression_slot)
    encrypted = bool(bits & (1 << 22))
    block_count = (bits >> 6) & 0xFFFF
    block_size = bits & 0x3F
    if block_size == 0x3F:
        block_size = r.u32()
    else:
        block_size <<= 11
    e.block_size = block_size

    def varint(bit: int) -> int:
        return r.u32() if (bits & (1 << bit)) else r.u64()

    e.offset = varint(31)
    e.uncompressed = varint(30)
    e.compressed = e.uncompressed if e.compression_name is None else varint(29)
    if block_count == 1 and not encrypted:
        e.blocks = None  # 1 block tổng hợp, không lưu kích thước riêng
    elif block_count > 0:
        e.blocks = []
        pos = 0
        for _ in range(block_count):
            size = r.u32()
            e.blocks.append((pos, pos + size))
            pos += size
    e.flags = 1 if encrypted else 0
    return e


def _parse_entry(r: _Reader, footer: PakFooter) -> _PakEntry:
    if footer.version >= 10:
        return _parse_encoded_entry(r, footer)
    return _parse_standard_entry(r, footer)


def _standard_entry_size(footer: PakFooter, entry: _PakEntry) -> int:
    """Kích thước header chuẩn của một entry (để đo vị trí dữ liệu khi
    cần tính mà không parse lại; chỉ dùng cho entry không nén)."""
    size = 8 + 8 + 8
    size += 1 if footer.is_v8a else 4
    if footer.version == 1:
        size += 8
    size += 20
    if footer.version >= 3:
        if entry.compression_name is not None and entry.blocks is not None:
            size += 4 + 16 * len(entry.blocks)
        size += 1 + 4
    return size


# --------------------------------------------------------------- chuẩn hóa tên
def _normalize_mount_point(mp: str) -> str:
    """'../../../TestGame/Content/' -> 'TestGame/Content/' (chỉ để hiển thị)."""
    p = mp
    while p.startswith("../"):
        p = p[3:]
    return p.lstrip("/")


def _normalize_entry_path(path: str) -> Tuple[Optional[str], Optional[str]]:
    """(tên chuẩn hóa, lý do loại bỏ hoặc None). Bỏ tiền tố '../' của
    mount point (kiểu UE: '../../../Game/...'); đường dẫn tuyệt đối hoặc
    còn '.'/'..' sau đó thì bị loại. Tên trả về luôn là đường dẫn tương
    đối an toàn theo quy ước VFS."""
    p = path
    while p.startswith("../"):
        p = p[3:]
    if p.startswith("/"):
        return None, "đường dẫn tuyệt đối"
    if not p:
        return None, "tên rỗng sau khi chuẩn hóa"
    if "\x00" in p or "\\" in p or "//" in p:
        return None, "tên chứa ký tự không an toàn"
    if ":" in p:
        return None, "tên chứa ':' (mơ hồ với dấu tách '::')"
    parts = [x for x in p.split("/") if x != ""]
    if any(x in (".", "..") for x in parts):
        return None, "tên chứa '.' hoặc '..'"
    return p, None


# --------------------------------------------------------------------- stream
class _PakEntryStream(io.RawIOBase):
    """Stream chỉ đọc trên một entry PAK (dữ liệu nằm liên tục trong file)."""

    def __init__(self, vfs: "PakVFS", entry: _PakEntry):
        self._vfs = vfs
        self._entry = entry
        self._start = vfs._data_start(entry)
        self._size = entry.uncompressed
        self._pos = 0
        self._fh = None
        if vfs._path is not None:
            self._fh = open(str(vfs._path), "rb")

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        if self.closed:
            raise ValueError("luồng đã đóng")
        if n is None or n < 0:
            n = self._size - self._pos
        n = min(n, self._size - self._pos)
        if n <= 0:
            return b""
        try:
            if self._fh is not None:
                self._fh.seek(self._start + self._pos)
                data = self._fh.read(n)
            else:
                data = self._vfs._data[self._start + self._pos:self._start + self._pos + n]
        except OSError as exc:
            raise error_from_exception(exc, where=self._entry.name)
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            pos = offset
        elif whence == 1:
            pos = self._pos + offset
        elif whence == 2:
            pos = self._size + offset
        else:
            raise PB3Error("E_PLUGIN_INVALID", "whence phải là 0/1/2", where=self._entry.name)
        if pos < 0 or pos > self._size:
            raise PB3Error("E_PLUGIN_INVALID", "vị trí seek ngoài entry", where=self._entry.name)
        self._pos = pos
        return pos

    def tell(self) -> int:
        return self._pos

    def write(self, b):                                     # noqa: ARG002
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc", where=self._entry.name)

    def close(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except OSError:
            pass
        super().close()


# ---------------------------------------------------------------------- VFS
class PakVFS(VFS):
    """VFS chỉ đọc trên file PAK của Unreal Engine (UE4/UE5).

    `source`: đường dẫn tệp hoặc bytes (như ZipVFS). Chỉ entry không nén
    và không mã hóa mới đọc được; entry nén/mã hóa/đã xóa/tên xấu bị loại
    khỏi VFS và ghi vào inventory.rejected. Footer sai/version ngoài
    1-11 -> E_BAD_ARCHIVE; index mã hóa -> E_ENCRYPTED.
    """

    kind = PAK_KIND

    def __init__(self, source: Union[str, os.PathLike, bytes, bytearray], *,
                 limits: Optional[Limits] = None, source_name: str = ""):
        self.limits = limits or Limits()
        self._path: Optional[Path] = None
        self._data: Optional[bytes] = None
        if isinstance(source, (str, os.PathLike)):
            self._path = safe_io.check_readable_input(source, self.limits)
            self.source_name = source_name or self._path.name
            self._file_size = self._path.stat().st_size
            tail = self._read_at(max(0, self._file_size - 222), min(222, self._file_size))
        elif isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
            self.source_name = source_name or "<bytes>"
            self._file_size = len(self._data)
            tail = self._data[-222:] if self._file_size >= 222 else self._data
        else:
            raise PB3Error("E_PLUGIN_INVALID", "PakVFS cần đường dẫn tệp hoặc bytes",
                           where=source_name or "?")
        self._footer = _parse_footer(tail, self._file_size, self.source_name)
        if self._footer.encrypted_index:
            raise PB3Error("E_ENCRYPTED", "index PAK mã hóa (cần key AES, không giải mã)",
                           where=self.source_name)
        self.mount_point = ""
        self._entries: Dict[str, _PakEntry] = {}
        self._rejected: Dict[str, str] = {}
        self._inventory = self._build_inventory()

    # ---- thuộc tính công khai (để plugin/probe hiển thị) ----
    @property
    def pak_version(self) -> int:
        return self._footer.version

    @property
    def pak_version_name(self) -> str:
        return self._footer.version_name

    @property
    def compression_methods(self) -> List[Optional[str]]:
        return list(self._footer.compression)

    @property
    def footer_size(self) -> int:
        return self._footer.footer_size

    # ---- đọc thô ----
    def _read_at(self, pos: int, n: int) -> bytes:
        """Đọc n byte tại vị trí pos (mở file mỗi lần với nguồn đường dẫn)."""
        if n <= 0:
            return b""
        if self._path is not None:
            try:
                with open(str(self._path), "rb") as fh:
                    fh.seek(pos)
                    return fh.read(n)
            except OSError as exc:
                raise error_from_exception(exc, where=self.source_name)
        return (self._data or b"")[pos:pos + n]

    # ---- mở và kiểm tra ----
    def _build_inventory(self) -> ContainerInventory:
        footer = self._footer
        self.limits.check_memory_read(footer.index_size, where=self.source_name)
        index_bytes = self._read_at(footer.index_offset, footer.index_size)
        if len(index_bytes) != footer.index_size:
            raise PB3Error("E_CORRUPT", "index cắt cụt: đọc được %d/%d byte"
                           % (len(index_bytes), footer.index_size), where=self.source_name)
        inv = ContainerInventory(kind=PAK_KIND, source_name=self.source_name)
        r = _Reader(index_bytes, self.source_name)
        try:
            self.mount_point = _normalize_mount_point(r.fstring())
            count = r.i32()
        except PB3Error as exc:
            raise PB3Error("E_BAD_ARCHIVE", "index lỗi: %s" % (exc.detail or exc.message_vi),
                           where=self.source_name)
        if count < 0:
            raise PB3Error("E_BAD_ARCHIVE", "số entry index âm (%d)" % count, where=self.source_name)
        self.limits.check_entries(count, self.source_name)
        seen = set()
        total = 0
        for order in range(count):
            try:
                path = r.fstring()
                entry = _parse_entry(r, footer)
            except PB3Error as exc:
                raise PB3Error("E_BAD_ARCHIVE", "entry thứ %d trong index lỗi: %s"
                               % (order, exc.detail or exc.message_vi), where=self.source_name)
            total += entry.uncompressed
            self.limits.check_total(total, self.source_name)
            norm, reason = _normalize_entry_path(path)
            if reason is None and entry.is_encrypted:
                reason = "entry mã hóa (cần key AES, không giải mã)"
            if reason is None and entry.is_deleted:
                reason = "entry đã xóa (cờ deleted)"
            if reason is None and entry.compression_name is not None:
                reason = "nén bằng %s (chỉ đọc entry không nén)" % entry.compression_name
            if reason is None and norm in seen:
                reason = "trùng tên (chỉ dùng lần đầu)"
            if reason is not None:
                self._rejected[path] = reason
                if norm is not None and norm != path:
                    self._rejected[norm] = reason
                inv.rejected.append((path, reason))
                continue
            if entry.offset + entry.compressed > self._file_size:
                raise PB3Error("E_BAD_ARCHIVE",
                               "entry %s vượt cuối file (offset %d + %d byte)"
                               % (norm, entry.offset, entry.compressed), where=self.source_name)
            seen.add(norm)
            entry.name = norm
            entry.orig_name = path
            self._entries[norm] = entry
            extra = {"pak_version": footer.version,
                     "offset": entry.offset,
                     "hash": entry.digest.hex() if entry.digest else None,
                     "compression_slot": entry.compression_slot,
                     "flags": entry.flags,
                     "block_size": entry.block_size}
            if path != norm:
                extra["orig_name"] = path
            inv.entries.append(EntryInfo(
                name=norm, size=entry.uncompressed, compress_size=entry.compressed,
                compress_type="stored",
                is_encrypted=entry.is_encrypted, order=order, extra=extra))
        if footer.index_hash != b"\x00" * 20:
            if hashlib.sha1(index_bytes).digest() != footer.index_hash:
                inv.warnings.append("hash SHA-1 của index không khớp footer "
                                    "(index có thể đã bị sửa)")
        if footer.encryption_guid != b"\x00" * 16:
            inv.warnings.append("footer có encryption GUID (index không mã hóa; "
                                "entry nào mã hóa sẽ bị loại)")
        if footer.version not in _FIXTURE_VERSIONS:
            inv.warnings.append("phiên bản PAK %s chưa có fixture kiểm chứng "
                                "(đã kiểm chứng: V8B, V11)" % footer.version_name)
        return inv

    def inventory(self) -> ContainerInventory:
        return self._inventory

    # ---- VFS ----
    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        for name in self._entries:                     # thứ tự trong index
            if prefix is None or name.startswith(prefix):
                yield name

    def _lookup(self, path: str) -> _PakEntry:
        check_vpath(path)
        entry = self._entries.get(path)
        if entry is not None:
            return entry
        reason = self._rejected.get(path)
        if reason is not None:
            if reason.startswith("entry mã hóa"):
                raise PB3Error("E_ENCRYPTED", reason, where=path)
            if reason.startswith("nén bằng"):
                raise PB3Error("E_UNSUPPORTED_FORMAT", reason, where=path)
            if reason.startswith("entry đã xóa"):
                raise PB3Error("E_INPUT_NOT_FOUND", reason, where=path)
            raise PB3Error("E_UNSAFE_PATH", reason, where=path)
        raise PB3Error("E_INPUT_NOT_FOUND", "không có trong container", where=path)

    def stat(self, path: str) -> VStat:
        return VStat(path, self._lookup(path).uncompressed, False)

    def _data_start(self, entry: _PakEntry) -> int:
        """Vị trí bắt đầu dữ liệu: offset + kích thước header chuẩn trước
        dữ liệu (đo bằng cách parse header thật tại offset)."""
        if entry.header_size is not None:
            return entry.offset + entry.header_size
        raw = self._read_at(entry.offset, 128)
        r = _Reader(raw, self.source_name)
        try:
            _parse_standard_entry(r, self._footer)
        except PB3Error as exc:
            raise PB3Error("E_BAD_ARCHIVE", "header entry lỗi: %s"
                           % (exc.detail or exc.message_vi), where=entry.name)
        entry.header_size = r.tell()
        return entry.offset + entry.header_size

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        entry = self._lookup(path)
        take = read_policy(entry.uncompressed, max_bytes, self.limits, path)
        data = self._read_at(self._data_start(entry), take)
        if len(data) != take:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte" % (len(data), take), where=path)
        return data

    def slice(self, path: str, offset: int, size: int) -> bytes:
        entry = self._lookup(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int >= 0", where=path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise PB3Error("E_PLUGIN_INVALID", "size phải là int >= 0", where=path)
        remaining = max(0, entry.uncompressed - offset)
        self.limits.check_memory_read(min(size, remaining), where=path)
        if size == 0 or offset >= entry.uncompressed:
            return b""
        take = min(size, remaining)
        data = self._read_at(self._data_start(entry) + offset, take)
        if len(data) != take:
            raise PB3Error("E_CORRUPT", "slice cắt cụt: đọc được %d/%d byte"
                           % (len(data), take), where=path)
        return data

    def open_stream(self, path: str):
        return _PakEntryStream(self, self._lookup(path))

    def close(self) -> None:
        return None

    def __enter__(self) -> "PakVFS":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


# ------------------------------------------------------- builder (chỉ fixture)
def _write_standard_entry(offset: int, compressed: int, uncompressed: int,
                          comp_raw: int, digest: bytes, blocks, flags: int,
                          block_size: int, version: int, v8a: bool) -> bytes:
    """Ghi FPakEntry dạng chuẩn (dùng cho cả header trước dữ liệu — offset
    ghi 0 — và entry trong index). blocks: None hoặc list[(start, end)]."""
    out = struct.pack("<QQQ", offset, compressed, uncompressed)
    out += struct.pack("<B", comp_raw) if v8a else struct.pack("<I", comp_raw)
    out += digest
    if version >= 3:
        if blocks is not None:
            out += struct.pack("<i", len(blocks))
            for (s, e) in blocks:
                out += struct.pack("<qq", s, e)
        out += struct.pack("<B", flags)
        out += struct.pack("<I", block_size)
    return out


def _write_encoded_entry(offset: int, compressed: int, uncompressed: int,
                         slot, blocks, block_size: int, encrypted: bool) -> bytes:
    """Ghi FPakEntry mã hóa bit-packed (V10/V11, chỉ trong index)."""
    block_count = 0 if blocks is None else len(blocks)
    cbs = (block_size >> 11) & 0x3F
    if (cbs << 11) != block_size:
        cbs = 0x3F
    flags = cbs | (block_count << 6) | ((1 if encrypted else 0) << 22)
    flags |= (((slot + 1) if slot is not None else 0) << 23)
    off32 = offset <= 0xFFFFFFFF
    unc32 = uncompressed <= 0xFFFFFFFF
    com32 = compressed <= 0xFFFFFFFF
    flags |= ((1 if com32 else 0) << 29) | ((1 if unc32 else 0) << 30) \
        | ((1 if off32 else 0) << 31)
    out = struct.pack("<I", flags)
    if cbs == 0x3F:
        out += struct.pack("<I", block_size)
    out += struct.pack("<I" if off32 else "<Q", offset)
    out += struct.pack("<I" if unc32 else "<Q", uncompressed)
    if slot is not None:
        out += struct.pack("<I" if com32 else "<Q", compressed)
        if block_count > 1 or encrypted:
            for (s, e) in blocks:
                out += struct.pack("<I", e - s)
    return out


def build_pak(entries, *, version: int = 8, v8a: bool = False,
              mount_point: str = "../../../TestGame/Content/",
              compression_names=None, frozen: bool = False,
              encrypted_index: bool = False) -> bytes:
    """Dựng file PAK tối thiểu trong RAM — CHỈ để làm fixture kiểm thử.

    entries: list[(đường_dẫn, dữ_liệu, tùy_chọn)] với tùy_chọn là dict:
        {"compression": None | "Zlib" | "Gzip" | "Oodle" | int (slot),
         "encrypted": bool, "deleted": bool}
    version: 3, 7, 8, 9 hoặc 11 (8 = V8B, footer 221 byte; v8a=True thì
        V8A, footer 189 byte, compression 1 byte trong entry; 11 = V11,
        index entry mã hóa bit-packed; 9 = frozen, footer 222 byte).
    mount_point: tiền tố mount ghi trong index (mặc định dạng UE).
    compression_names: tên nén ghi vào footer (V8+); mặc định
        ["Zlib", "Gzip", "Oodle", "", ""].
    encrypted_index: ghi cờ index mã hóa vào footer (để test E_ENCRYPTED;
        index vẫn ghi dạng rõ, chỉ cờ được bật).
    Entry "mã hóa" chỉ mang cờ encrypted — dữ liệu KHÔNG mã hóa thật.
    Entry nén dùng Zlib của stdlib (để test đường loại bỏ, không phải để
    đọc được). Không dùng os.urandom; dữ liệu do người gọi cung cấp.
    """
    if version not in (3, 7, 8, 9, 11):
        raise ValueError("build_pak chỉ hỗ trợ version 3, 7, 8, 9, 11")
    if v8a and version != 8:
        raise ValueError("v8a chỉ dùng với version 8")
    names = list(compression_names) if compression_names is not None \
        else ["Zlib", "Gzip", "Oodle", "", ""]
    if version >= 8:
        while len(names) < 5:
            names.append("")
        names = names[:5]
    if version == 8 and v8a:
        names = names[:4]

    # Chuẩn bị từng entry: dữ liệu lưu trữ, block, cờ, hash.
    prepared = []
    for item in entries:
        path, data = item[0], bytes(item[1])
        opts = item[2] if len(item) > 2 else {}
        comp = opts.get("compression")
        if comp is None:
            slot = None
            stored = data
        elif isinstance(comp, int):
            slot = comp
            stored = zlib.compress(data)
        else:
            slot = {"Zlib": 0, "Gzip": 1, "Oodle": 2}[comp]
            stored = zlib.compress(data)
        flags = 0
        if opts.get("encrypted"):
            flags |= 1
        if opts.get("deleted"):
            if version >= 10:
                # Entry mã hóa bit-packed (V10+) không có bit deleted: file
                # bị xóa trong pak V10+ thì bị loại khỏi index, không đánh dấu.
                raise ValueError("version 10+ không lưu cờ deleted trong entry mã hóa")
            flags |= 2
        # Block: V5+ dùng offset tương đối theo đầu dữ liệu; V3/V4 tuyệt đối.
        # V3/V4: giữ chỗ 1 block để kích thước header tính đúng, điền offset
        # tuyệt đối sau khi biết vị trí (lượt 1 dưới đây).
        if slot is None:
            blocks = None
        elif version >= 5:
            blocks = [(0, len(stored))]
        else:
            blocks = [(0, 0)]
        prepared.append({"path": path, "data": data, "stored": stored,
                         "slot": slot, "flags": flags,
                         "block_size": 65536 if slot is not None else 0,
                         "digest": hashlib.sha1(data).digest(),
                         "blocks": blocks, "offset": 0, "header_size": 0})

    def comp_raw_v8(slot):
        return 0 if slot is None else slot + 1

    def comp_raw_old(slot):
        return {None: 0, 0: 1, 1: 2, 2: 4}.get(slot, 0)

    # Lượt 1: tính offset + kích thước header trước dữ liệu.
    pos = 0
    for p in prepared:
        comp_raw = comp_raw_v8(p["slot"]) if version >= 8 else comp_raw_old(p["slot"])
        hdr = _write_standard_entry(0, len(p["stored"]), len(p["data"]), comp_raw,
                                    p["digest"], p["blocks"], p["flags"],
                                    p["block_size"], version, v8a)
        p["header_size"] = len(hdr)
        p["offset"] = pos
        pos += len(hdr) + len(p["stored"])
        if p["blocks"] is not None and version < 5:
            # V3/V4: block tuyệt đối (đầu dữ liệu -> cuối dữ liệu).
            start = p["offset"] + p["header_size"]
            p["blocks"] = [(start, start + len(p["stored"]))]

    # Lượt 2: ghi vùng dữ liệu.
    blob = bytearray()
    for p in prepared:
        comp_raw = comp_raw_v8(p["slot"]) if version >= 8 else comp_raw_old(p["slot"])
        blob += _write_standard_entry(0, len(p["stored"]), len(p["data"]), comp_raw,
                                      p["digest"], p["blocks"], p["flags"],
                                      p["block_size"], version, v8a)
        blob += p["stored"]

    # Index.
    index = write_fstring(mount_point)
    index += struct.pack("<i", len(prepared))
    for p in prepared:
        index += write_fstring(p["path"])
        if version >= 10:
            index += _write_encoded_entry(p["offset"], len(p["stored"]), len(p["data"]),
                                          p["slot"], p["blocks"], p["block_size"],
                                          bool(p["flags"] & 1))
        else:
            comp_raw = comp_raw_v8(p["slot"]) if version >= 8 else comp_raw_old(p["slot"])
            index += _write_standard_entry(p["offset"], len(p["stored"]), len(p["data"]),
                                           comp_raw, p["digest"], p["blocks"],
                                           p["flags"], p["block_size"], version, v8a)
    index_offset = len(blob)
    blob += index
    index_hash = hashlib.sha1(bytes(index)).digest()

    # Footer.
    foot = b""
    if version >= 7:
        foot += b"\x00" * 16  # encryption GUID (không có)
    if version >= 4:
        foot += b"\x01" if encrypted_index else b"\x00"
    foot += struct.pack("<IIqq", PAK_MAGIC, version, index_offset, len(index))
    foot += index_hash
    if version == 9:
        foot += b"\x01" if frozen else b"\x00"
    if version >= 8:
        for name in names:
            raw = name.encode("ascii")[:31]
            foot += raw + b"\x00" * (32 - len(raw))
    return bytes(blob) + foot
