"""Backend Godot PCK (Claude 08, nhóm Godot; prompt 08 §1).

Phạm vi — CHỈ tuyên bố hỗ trợ biến thể có fixture + test:
  - PCK pack_version 1 (Godot 3) và 2 (Godot 4), entry KHÔNG nén,
    không mã hóa (entry stored). Đọc: mục lục, list/read/slice/stream,
    inventory, kiểm MD5 khi đọc nguyên entry.
  - Thư mục mã hóa (cờ PACK_DIR_ENCRYPTED) -> E_ENCRYPTED khi mở.
  - Entry mã hóa / đã xóa / cờ lạ -> loại khỏi VFS, ghi vào
    inventory.rejected với lý do rõ; đọc báo lỗi, không crash.
  - pack_version 0 / 3+ -> E_UNSUPPORTED_FORMAT (nhận diện được nhưng
    chưa hỗ trợ); magic sai / cấu trúc hỏng -> E_BAD_ARCHIVE; file cắt
    cụt -> E_CORRUPT.

Giới hạn trung thực (ghi rõ, không giấu):
  - PCK không có cờ nén entry trong đặc tả: entry nào mang cờ ngoài
    ENCRYPTED/DELETED đều bị loại ("cờ entry không hỗ trợ").
  - Không hỗ trợ PCK nhúng trong file thực thi (embedded): chỉ đọc file
    .pck độc lập, offset tính từ đầu file.
  - Không ghi lại PCK (chỉ đọc); không giải mã (cần khóa Godot).
  - Đường dẫn trong PCK dạng "res://..." được bỏ tiền tố "res://" (hoặc
    "user://") thành đường dẫn ảo tương đối; tên không an toàn (tuyệt
    đối, "..", "\\", NUL, ":") bị loại như ZipVFS.
  - MD5 chỉ kiểm khi đọc NGUYÊN entry; slice/đọc tiền tố/stream không
    kiểm (quy ước như ZipVFS: VStat.crc_verified=False). Entry có MD5
    toàn 0 (một số công cụ ghi vậy) được bỏ qua kiểm tra.
  - Fixture do tests/fixtures/godot/builder.py dựng (không có file PCK
    của game thật để đối chiếu); chưa kiểm chứng trên PCK Godot thật.

Layout đối chiếu tài liệu GodotPckTool / Godot file_access_pack
(little-endian):
  header: magic "GDPC" u32, pack_version u32, godot major/minor/patch
    u32, v2+: flags u32, file_base u64; reserved u32[16].
    -> v1: 84 byte; v2: 96 byte.
  thư mục ngay sau header: số entry u32, rồi mỗi entry: path_len u32
    (độ dài đường dẫn ĐÃ CĂN 4 byte) + path UTF-8 đệm NUL, offset u64,
    size u64, md5 16 byte, v2+: flags u32.
  v1: offset TUYỆT ĐỐI. v2: offset TƯƠNG ĐỐI, tuyệt đối = file_base +
    offset (file_base = đầu vùng dữ liệu, căn 32 byte).
  flags pack: PACK_DIR_ENCRYPTED = 1<<0, PACK_FILE_RELATIVE_BASE = 1<<1.
  flags entry: FILE_ENCRYPTED = 1<<0, FILE_DELETED = 1<<1.

Chỉ thư viện chuẩn, Python 3.9. Không gọi mạng, không import UnityPy/lz4.
"""
from __future__ import annotations

import hashlib
import io
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple, Union

from core import safe_io
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from sdk.containers import ContainerInventory, EntryInfo
from sdk.vfs import VFS, VStat, check_vpath, read_policy

PCK_MAGIC = b"GDPC"
PACK_VERSION_GODOT3 = 1
PACK_VERSION_GODOT4 = 2
SUPPORTED_VERSIONS = (PACK_VERSION_GODOT3, PACK_VERSION_GODOT4)

PACK_DIR_ENCRYPTED = 1 << 0
PACK_FILE_RELATIVE_BASE = 1 << 1
FILE_ENCRYPTED = 1 << 0
FILE_DELETED = 1 << 1
_FILE_KNOWN_FLAGS = FILE_ENCRYPTED | FILE_DELETED

_HEADER_V1_SIZE = 84
_HEADER_V2_SIZE = 96
_RESERVED_BYTES = 64
_MAX_PATH_LEN = 1024 * 1024


@dataclass(frozen=True)
class _PckEntry:
    name: str
    offset: int
    size: int
    md5: bytes
    flags: int
    order: int


def _unsafe_name_reason(name: str) -> Optional[str]:
    """Lý do chặn tên entry, None nếu an toàn (cùng quy tắc ZipVFS)."""
    if not name or "\x00" in name:
        return "tên rỗng hoặc chứa NUL"
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return "đường dẫn tuyệt đối"
    if "\\" in name:
        return "chứa dấu '\\'"
    if "//" in name:
        return "chứa '//' (đoạn rỗng)"
    if ":" in name:
        return "chứa ':' (mơ hồ với dấu tách '::')"
    parts = [p for p in name.split("/") if p != ""]
    if any(p in (".", "..") for p in parts):
        return "chứa '.' hoặc '..'"
    return None


def _strip_scheme(path: str) -> str:
    """'res://scenes/main.tscn' -> 'scenes/main.tscn' (tiền tố Godot)."""
    for scheme in ("res://", "user://"):
        if path.startswith(scheme):
            return path[len(scheme):]
    return path


class _PckEntryStream(io.RawIOBase):
    """Luồng chỉ đọc trên một entry PCK (seek/read theo offset tuyệt đối)."""

    def __init__(self, vfs: "PckVFS", entry: _PckEntry):
        self._vfs = vfs
        self._entry = entry
        self._pos = 0

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
            n = self._entry.size - self._pos
        if n <= 0 or self._pos >= self._entry.size:
            return b""
        take = min(n, self._entry.size - self._pos)
        data = self._vfs._raw_at(self._entry.offset + self._pos, take)
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int", where=self._entry.name)
        if whence not in (0, 1, 2):
            raise PB3Error("E_PLUGIN_INVALID", "whence phải là 0/1/2", where=self._entry.name)
        if whence == 0:
            new = offset
        elif whence == 1:
            new = self._pos + offset
        else:
            new = self._entry.size + offset
        if new < 0:
            raise PB3Error("E_PLUGIN_INVALID", "seek tới vị trí âm", where=self._entry.name)
        self._pos = new
        return self._pos

    def tell(self) -> int:
        return self._pos

    def write(self, b):                                   # noqa: ARG002
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc", where=self._entry.name)


class PckVFS(VFS):
    """VFS chỉ đọc trên file PCK Godot 3/4. `source`: đường dẫn tệp hoặc bytes."""

    kind = "godot_pck"

    def __init__(self, source: Union[str, os.PathLike, bytes, bytearray], *,
                 limits: Optional[Limits] = None, source_name: str = ""):
        self.limits = limits or Limits()
        self._path: Optional[Path] = None
        self._data: Optional[bytes] = None
        if isinstance(source, (str, os.PathLike)):
            self._path = safe_io.check_readable_input(source, self.limits)
            self.source_name = source_name or self._path.name
            try:
                self._file_size = self._path.stat().st_size
            except OSError as exc:
                raise error_from_exception(exc, where=self.source_name)
        elif isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
            self._file_size = len(self._data)
            self.source_name = source_name or "<bytes>"
        else:
            raise PB3Error("E_PLUGIN_INVALID", "PckVFS cần đường dẫn tệp hoặc bytes",
                           where=source_name or "?")
        self.pack_version: int = 0
        self.godot_version: Tuple[int, int, int] = (0, 0, 0)
        self.file_base: int = 0
        self._entries: Dict[str, _PckEntry] = {}
        self._rejected: Dict[str, str] = {}
        self._dirs: set = set()
        self._inventory = self._parse()

    # ---- đọc thô ----
    def _raw_at(self, pos: int, n: int) -> bytes:
        """Đọc n byte thô tại vị trí pos (thiếu -> E_CORRUPT)."""
        if n == 0:
            return b""
        if self._path is not None:
            try:
                with open(str(self._path), "rb") as fh:
                    fh.seek(pos)
                    data = fh.read(n)
            except OSError as exc:
                raise error_from_exception(exc, where=self.source_name)
        else:
            data = (self._data or b"")[pos:pos + n]
        if len(data) != n:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte tại offset %d (dữ liệu bị cắt cụt)"
                           % (len(data), n, pos), where=self.source_name)
        return data

    # ---- phân tích header + thư mục ----
    def _parse(self) -> ContainerInventory:
        head = self._raw_at(0, 8) if self._file_size >= 8 else None
        if head is None:
            raise PB3Error("E_CORRUPT", "file PCK cắt cụt (chỉ %d byte)"
                           % self._file_size, where=self.source_name)
        if head[:4] != PCK_MAGIC:
            raise PB3Error("E_BAD_ARCHIVE", "không phải file PCK Godot (magic GDPC)",
                           where=self.source_name)
        version = struct.unpack_from("<I", head, 4)[0]
        if version not in SUPPORTED_VERSIONS:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "PCK pack_version %d chưa hỗ trợ (chỉ v1 Godot 3 và v2 Godot 4)"
                           % version, where=self.source_name)
        self.pack_version = version
        header_size = _HEADER_V1_SIZE if version == 1 else _HEADER_V2_SIZE
        header = self._raw_at(0, header_size)
        _magic, _ver, major, minor, patch = struct.unpack_from("<IIIII", header, 0)
        self.godot_version = (major, minor, patch)
        pack_flags = 0
        if version == 2:
            pack_flags, self.file_base = struct.unpack_from("<IQ", header, 20)
        if pack_flags & PACK_DIR_ENCRYPTED:
            raise PB3Error("E_ENCRYPTED", "thư mục PCK mã hóa (cần khóa Godot)",
                           where=self.source_name)
        count_raw = self._raw_at(header_size, 4)
        count = struct.unpack("<I", count_raw)[0]
        self.limits.check_entries(count, self.source_name)
        inv = ContainerInventory(kind="godot_pck", source_name=self.source_name)
        inv.warnings.append("PCK pack_version %d (Godot %d.%d.%d), chỉ đọc entry không nén/không mã hóa"
                            % (version, major, minor, patch))
        pos = header_size + 4
        total = 0
        seen = set()
        for order in range(count):
            path_len_raw = self._raw_at(pos, 4)
            path_len = struct.unpack("<I", path_len_raw)[0]
            pos += 4
            if path_len == 0 or path_len > _MAX_PATH_LEN or path_len % 4 != 0:
                raise PB3Error("E_BAD_ARCHIVE",
                               "độ dài đường dẫn entry %d bất thường (%d)" % (order, path_len),
                               where=self.source_name)
            raw_path = self._raw_at(pos, path_len)
            pos += path_len
            try:
                path = raw_path.rstrip(b"\x00").decode("utf-8")
            except UnicodeDecodeError:
                raise PB3Error("E_BAD_ARCHIVE",
                               "đường dẫn entry %d không phải UTF-8" % order,
                               where=self.source_name)
            if "\x00" in path:
                raise PB3Error("E_BAD_ARCHIVE",
                               "đường dẫn entry %d chứa NUL giữa chừng" % order,
                               where=self.source_name)
            fixed = 8 + 8 + 16 + (4 if version == 2 else 0)
            fields = self._raw_at(pos, fixed)
            pos += fixed
            stored_off, size = struct.unpack_from("<QQ", fields, 0)
            md5 = bytes(fields[16:32])
            flags = struct.unpack_from("<I", fields, 32)[0] if version == 2 else 0
            abs_off = stored_off if version == 1 else self.file_base + stored_off
            name = _strip_scheme(path)
            is_dir = name.endswith("/")
            entry = EntryInfo(
                name=name, size=size, compress_size=size, compress_type="stored",
                mode=0, is_dir=is_dir, is_encrypted=bool(flags & FILE_ENCRYPTED),
                order=order, extra={"md5": md5.hex(), "flags": flags,
                                    "pack_version": version,
                                    "offset": abs_off})
            if is_dir:
                self._dirs.add(name)
                inv.entries.append(entry)
                continue
            reason = _unsafe_name_reason(name)
            if reason is None and flags & FILE_ENCRYPTED:
                reason = "entry mã hóa (cần khóa Godot, không giải mã)"
            if reason is None and flags & FILE_DELETED:
                reason = "entry đã xóa"
            if reason is None and flags & ~_FILE_KNOWN_FLAGS:
                reason = "cờ entry không hỗ trợ (0x%X)" % (flags & ~_FILE_KNOWN_FLAGS)
            if reason is None and name in seen:
                reason = "trùng tên (chỉ dùng lần đầu)"
            if reason is None and (abs_off + size > self._file_size or abs_off < 0):
                reason = "dữ liệu entry vượt ngoài file"
            if reason is not None:
                self._rejected[name] = reason
                inv.rejected.append((name, reason))
                continue
            seen.add(name)
            total += size
            self.limits.check_total(total, self.source_name)
            self._entries[name] = _PckEntry(name, abs_off, size, md5, flags, order)
            inv.entries.append(entry)
        return inv

    def inventory(self) -> ContainerInventory:
        return self._inventory

    # ---- VFS ----
    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        for name in self._entries:                         # thứ tự trong thư mục PCK
            if prefix is None or name.startswith(prefix):
                yield name

    def _lookup(self, path: str) -> _PckEntry:
        check_vpath(path)
        entry = self._entries.get(path)
        if entry is not None:
            return entry
        reason = self._rejected.get(path)
        if reason is not None and "mã hóa" in reason:
            raise PB3Error("E_ENCRYPTED", "entry mã hóa", where=path)
        if reason is not None:
            raise PB3Error("E_UNSAFE_PATH", reason, where=path)
        raise PB3Error("E_INPUT_NOT_FOUND", "không có trong container", where=path)

    def stat(self, path: str) -> VStat:
        if path in self._dirs or path.rstrip("/") in self._dirs:
            return VStat(path, 0, False)
        return VStat(path, self._lookup(path).size, False)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        entry = self._lookup(path)
        take = read_policy(entry.size, max_bytes, self.limits, path)
        data = self._raw_at(entry.offset, take)
        if take == entry.size and entry.md5 != b"\x00" * 16:
            if hashlib.md5(data).digest() != entry.md5:
                raise PB3Error("E_BAD_ARCHIVE", "MD5 của entry sai (dữ liệu hỏng hoặc đã sửa)",
                               where=path)
        return data

    def slice(self, path: str, offset: int, size: int) -> bytes:
        entry = self._lookup(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int >= 0", where=path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise PB3Error("E_PLUGIN_INVALID", "size phải là int >= 0", where=path)
        remaining = max(0, entry.size - offset)
        self.limits.check_memory_read(min(size, remaining), where=path)
        if size == 0 or remaining == 0:
            return b""
        return self._raw_at(entry.offset + offset, min(size, remaining))

    def open_stream(self, path: str):
        return _PckEntryStream(self, self._lookup(path))

    def close(self) -> None:
        return None

    def __enter__(self) -> "PckVFS":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
