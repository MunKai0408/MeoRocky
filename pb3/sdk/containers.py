"""Container và VFS thật (đặc tả §5, Claude 04; decisions #61-64).

Đọc: ZIP/IPA (`zipfile`), TAR/GZ/XZ (`tarfile`/`gzip`/`lzma`, chỉ đọc).
Định dạng 7z KHÔNG có backend (không thêm phụ thuộc chưa kiểm chứng): mở
file 7z báo E_UNSUPPORTED_FORMAT rõ ràng, không giả lập hỗ trợ.

Mọi lớp VFS ở đây tuân đúng giao diện `sdk.vfs.VFS` (không đổi chữ ký hay
quy ước `read()`): đọc toàn bộ entry kiểm CRC (ZIP), slice/stream không đảm
bảo xác thực CRC (`VStat.crc_verified=False`; slice đọc tới cuối entry vẫn
có thể báo CRC sai vì zipfile kiểm ở cuối luồng). Giới hạn lấy từ
`core.limits.Limits`.

Container lồng nhau: đường dẫn ảo nối bằng `'::'`
(`"Payload/Game.app/Data/xxx.unity3d::resources.assets"`), mở lazy, cache
theo hash, giới hạn độ sâu/kích thước, phát hiện vòng lặp (decisions #63).

Ghi lại: chỉ `rebuild_zip` (ZIP), tái tạo entry cùng thứ tự/method/metadata,
ghi nguyên tử + rollback khi lỗi (decisions #64). Đầu ra mặc định là đơn vị
nhỏ nhất có thể thay thế: chỉ file container được đưa vào, không bao giờ
đóng gói lại toàn bộ gói ngoài. Kiểm tra capability `write`/`write_modes`
thuộc về lớp gọi (planner, Claude 05); hàm này chỉ ghi nhận `write_mode`.
"""
from __future__ import annotations

import binascii
import bz2
import gzip
import hashlib
import io
import lzma
import os
import shutil
import struct
import tarfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, List, Optional, Tuple, Union

from core import safe_io
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from sdk.manifest import WRITE_MODES
from sdk.vfs import SEP, VFS, VStat, check_vpath, read_policy

# ------------------------------------------------------------------ nhận diện
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
GZIP_MAGIC = b"\x1f\x8b"
XZ_MAGIC = b"\xfd7zXZ\x00"
TAR_MAGIC_OFFSET = 257
TAR_MAGIC = b"ustar"
# Godot PCK: magic "GDPC" ở đầu file (Godot 3/4). Unreal PAK không có magic
# đầu file; magic footer little-endian 0x5A6F12E1 nằm trong ~256 byte cuối.
GODOT_PCK_MAGIC = b"GDPC"
UNREAL_PAK_MAGIC = b"\xe1\x12\x6f\x5a"
# Số byte cuối file cần quét khi tìm magic footer của PAK.
_PAK_TAIL_SCAN = 4096

KIND_ZIP = "zip"
KIND_TAR = "tar"
KIND_GZIP = "gzip"
KIND_XZ = "xz"
KIND_BZIP2 = "bzip2"
KIND_7Z = "7z"
KIND_GODOT_PCK = "godot_pck"
KIND_UNREAL_PAK = "unreal_pak"
KIND_UNKNOWN = "unknown"

# Tối đa container con giữ trong cache (theo hash) của một NestedVFS.
MAX_NESTED_CACHE = 16

_ZIP_METHOD_NAMES = {
    zipfile.ZIP_STORED: "stored",
    zipfile.ZIP_DEFLATED: "deflated",
    zipfile.ZIP_BZIP2: "bzip2",
    zipfile.ZIP_LZMA: "lzma",
}
_ZIP_METHOD_CODES = {v: k for k, v in _ZIP_METHOD_NAMES.items()}


def fix_zip_entry_name(info: zipfile.ZipInfo) -> str:
    """Sửa tên entry ZIP bị mojibake do cờ UTF-8 (bit 11) không được đặt.

    Trường hợp thật (Safari iPhone, 2026-11-16): "Nén" trong Files iOS ghi
    tên entry bằng byte UTF-8 (vd tên bắt đầu bằng U+200E LEFT-TO-RIGHT
    MARK: byte E2 80 8E) nhưng KHÔNG đặt cờ UTF-8 bit 11. `zipfile` chuẩn
    khi đó giải mã tên theo cp437, biến E2 80 8E thành chuỗi "ΓÇÄ" hiển thị
    sai ("ΓÇÄ 9 Kings_v1.0.5-AppAssassin.ipa"). Sửa bằng cách mã hóa ngược
    tên đã giải sai thành byte cp437 rồi giải lại thành UTF-8.

    Chỉ áp dụng khi cờ UTF-8 chưa đặt VÀ byte giải lại thành UTF-8 hợp lệ
    (strict) VÀ không chứa ký tự điều khiển. Tên ASCII, tên đã có cờ UTF-8,
    và tên cp437 thật mà byte không phải UTF-8 hợp lệ thì giữ nguyên.
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    try:
        fixed = raw.decode("utf-8")
    except UnicodeDecodeError:
        return name
    if fixed == name:
        return name
    if any(ord(c) < 0x20 and c not in "\t" for c in fixed):
        return name
    return fixed


def support_matrix() -> List[Dict[str, str]]:
    """Bảng định dạng: format | đọc | ghi | backend (để in ra / ghi hồ sơ)."""
    return [
        {"format": "ZIP / IPA", "read": "có", "write": "có (rebuild_zip)",
         "backend": "zipfile (thư viện chuẩn)",
         "note": "slice thô: stored/deflate/bzip2/lzma; entry plugin tối đa theo "
                 "limits của plugin (zip khai 16 MiB)"},
        {"format": "TAR", "read": "có", "write": "không",
         "backend": "tarfile (thư viện chuẩn)"},
        {"format": "TAR.GZ / TGZ", "read": "có", "write": "không",
         "backend": "tarfile + gzip (thư viện chuẩn)"},
        {"format": "TAR.XZ", "read": "có", "write": "không",
         "backend": "tarfile + lzma (thư viện chuẩn)"},
        {"format": "TAR.BZ2", "read": "có", "write": "không",
         "backend": "tarfile + bzip2 (thư viện chuẩn)"},
        {"format": "GZ (một luồng)", "read": "có", "write": "không",
         "backend": "gzip (thư viện chuẩn)"},
        {"format": "XZ (một luồng)", "read": "có", "write": "không",
         "backend": "lzma (thư viện chuẩn)"},
        {"format": "BZ2 (một luồng)", "read": "có", "write": "không",
         "backend": "bzip2 (thư viện chuẩn)"},
        {"format": "7z", "read": "không", "write": "không",
         "backend": "chưa có (không thêm phụ thuộc chưa kiểm chứng)"},
        {"format": "Godot PCK (3/4)", "read": "có (chỉ đọc mục lục + entry không nén)",
         "write": "không", "backend": "sdk.godot.PckVFS (thuần Python)"},
        {"format": "Unreal PAK (UE4/UE5)", "read": "có (chỉ entry không nén, không mã hóa)",
         "write": "không", "backend": "sdk.unreal.PakVFS (thuần Python)"},
    ]


def detect_container_kind(data: bytes) -> str:
    """Nhận diện container từ magic. Trả 'zip'|'tar'|'gzip'|'xz'|'7z'|'godot_pck'|'unknown'.

    Lưu ý: Unreal PAK không có magic ở đầu file (magic nằm ở footer cuối
    file) nên hàm này không nhận diện được PAK; open_vfs kiểm tra footer
    riêng (xem _looks_like_pak)."""
    if not isinstance(data, (bytes, bytearray)):
        raise PB3Error("E_PLUGIN_INVALID", "detect_container_kind cần bytes", where="?")
    head = bytes(data)
    if head[:4] in ZIP_MAGICS:
        return KIND_ZIP
    if head[:4] == GODOT_PCK_MAGIC:
        return KIND_GODOT_PCK
    if head[:6] == SEVEN_ZIP_MAGIC:
        return KIND_7Z
    if head[:2] == GZIP_MAGIC:
        return KIND_GZIP
    if head[:6] == XZ_MAGIC:
        return KIND_XZ
    if head[:3] == b"BZh":
        return KIND_BZIP2
    if len(head) >= TAR_MAGIC_OFFSET + 5 and head[TAR_MAGIC_OFFSET:TAR_MAGIC_OFFSET + 5] == TAR_MAGIC:
        return KIND_TAR
    return KIND_UNKNOWN


# ------------------------------------------------------------------ inventory
@dataclass(frozen=True)
class EntryInfo:
    """Một entry trong container (metadata giữ để phục vụ ghi lại, đặc tả 5.2)."""
    name: str
    size: int
    compress_size: int = 0
    compress_type: str = "stored"
    date_time: Optional[tuple] = None
    mode: int = 0
    is_dir: bool = False
    is_symlink: bool = False
    is_encrypted: bool = False
    order: int = 0
    extra: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "size": self.size, "compress_size": self.compress_size,
                "compress_type": self.compress_type,
                "date_time": list(self.date_time) if self.date_time else None,
                "mode": self.mode, "is_dir": self.is_dir, "is_symlink": self.is_symlink,
                "is_encrypted": self.is_encrypted, "order": self.order, "extra": dict(self.extra)}


@dataclass
class ContainerInventory:
    """Bảng kê container: entry dùng được, entry bị chặn, cảnh báo."""
    kind: str
    source_name: str
    entries: List[EntryInfo] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)   # (tên, lý do)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {"kind": self.kind, "source_name": self.source_name,
                "entries": [e.to_dict() for e in self.entries],
                "rejected": [[n, r] for n, r in self.rejected],
                "warnings": list(self.warnings)}


def _unsafe_name_reason(name: str) -> Optional[str]:
    """Lý do chặn tên entry, None nếu tên an toàn (đường dẫn tương đối, dùng '/')."""
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


def _tar_error(exc: BaseException, where: str) -> PB3Error:
    """Dịch lỗi tarfile (error_from_exception không biết loại này)."""
    if isinstance(exc, PB3Error):
        return exc
    if isinstance(exc, (tarfile.TarError, EOFError, lzma.LZMAError)):
        detail = str(exc)[:120]
        return PB3Error("E_BAD_ARCHIVE", "cấu trúc tar lỗi: %s%s"
                        % (type(exc).__name__, (": " + detail) if detail else ""),
                        where=where)
    return error_from_exception(exc, where=where)


def _read_exact(stream: BinaryIO, n: int, where: str) -> bytes:
    """Đọc đúng n byte (stream có thể trả ít hơn mỗi lần); thiếu -> E_CORRUPT."""
    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            break
        out.extend(chunk)
    if len(out) < n:
        raise PB3Error("E_CORRUPT", "đọc được %d/%d byte (dữ liệu bị cắt cụt)" % (len(out), n), where=where)
    return bytes(out)


class _ROStream(io.RawIOBase):
    """Bọc stream nhị phân thành chỉ đọc: write -> E_PLUGIN_FORBIDDEN; lỗi đọc -> PB3Error."""

    def __init__(self, raw: BinaryIO, where: str):
        self._raw = raw
        self._where = where

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        try:
            return bool(self._raw.seekable())
        except (OSError, ValueError):
            return False

    def read(self, n: int = -1) -> bytes:
        try:
            return self._raw.read(n)
        except zipfile.BadZipFile:
            raise PB3Error("E_BAD_ARCHIVE", "CRC sai hoặc cấu trúc lỗi khi đọc", where=self._where)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _tar_error(exc, self._where)

    def seek(self, offset: int, whence: int = 0) -> int:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int", where=self._where)
        if whence not in (0, 1, 2):
            raise PB3Error("E_PLUGIN_INVALID", "whence phải là 0/1/2", where=self._where)
        if whence == 0 and offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset âm với whence=0", where=self._where)
        try:
            return self._raw.seek(offset, whence)
        except zipfile.BadZipFile:
            raise PB3Error("E_BAD_ARCHIVE", "CRC sai hoặc cấu trúc lỗi khi đọc", where=self._where)
        except ValueError as exc:   # luồng đã đóng/không seek được
            raise PB3Error("E_CORRUPT", "seek lỗi: %s" % str(exc)[:80], where=self._where)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _tar_error(exc, self._where)

    def tell(self) -> int:
        return self._raw.tell()

    def write(self, b):                                   # noqa: ARG002
        raise PB3Error("E_PLUGIN_FORBIDDEN", "VFS chỉ đọc", where=self._where)

    def close(self) -> None:
        try:
            self._raw.close()
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError):
            pass
        super().close()


# ---------------------------------------------------------------------- ZIP
class ZipVFS(VFS):
    """VFS thật trên ZIP/IPA. `source`: đường dẫn tệp hoặc bytes.

    Mở: kiểm tra EOCD (BadZipFile -> E_BAD_ARCHIVE), số entry và tổng giải
    nén theo limits (E_ARCHIVE_BOMB), tỉ lệ nén bất thường chỉ ghi cảnh báo
    (đặc tả 5.2). Entry không an toàn (tuyệt đối, '..', NUL, '\\', symlink,
    mã hóa) bị loại khỏi VFS và ghi vào inventory.rejected; đọc chúng báo lỗi
    chứ không làm hỏng cả container. Entry trùng tên: chỉ lần đầu được dùng.
    """

    kind = KIND_ZIP

    def __init__(self, source: Union[str, os.PathLike, bytes, bytearray], *,
                 limits: Optional[Limits] = None, source_name: str = ""):
        self.limits = limits or Limits()
        self._path: Optional[Path] = None
        self._buf: Optional[io.BytesIO] = None
        if isinstance(source, (str, os.PathLike)):
            self._path = safe_io.check_readable_input(source, self.limits)
            self.source_name = source_name or self._path.name
            try:
                self._zf = zipfile.ZipFile(str(self._path), "r")
            except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
                raise error_from_exception(exc, where=self.source_name)
        elif isinstance(source, (bytes, bytearray)):
            self._buf = io.BytesIO(bytes(source))
            self._data = bytes(source)
            self.source_name = source_name or "<bytes>"
            try:
                self._zf = zipfile.ZipFile(self._buf, "r")
            except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                detail = str(exc)[:120]
                raise PB3Error("E_BAD_ARCHIVE", "cấu trúc zip lỗi: %s%s"
                               % (type(exc).__name__, (": " + detail) if detail else ""),
                               where=self.source_name)
        else:
            raise PB3Error("E_PLUGIN_INVALID", "ZipVFS cần đường dẫn tệp hoặc bytes",
                           where=source_name or "?")
        self._infos: Dict[str, zipfile.ZipInfo] = {}
        self._rejected: Dict[str, str] = {}
        self._dirs: set = set()
        if self._path is None and getattr(self, "_data", None) is None:
            self._data = b""
        self._inventory = self._build_inventory()

    # ---- mở và kiểm tra ----
    def _build_inventory(self) -> ContainerInventory:
        infos = self._zf.infolist()
        self.limits.check_entries(len(infos), self.source_name)
        self.limits.check_total(sum(i.file_size for i in infos), self.source_name)
        inv = ContainerInventory(kind=KIND_ZIP, source_name=self.source_name)
        seen = set()
        for order, info in enumerate(infos):
            # Tên hiển thị/đọc: sửa mojibake UTF-8-không-cờ (vd ZIP do "Nén"
            # trong Files iOS tạo) — xem fix_zip_entry_name. ZipInfo gốc vẫn
            # giữ để đọc dữ liệu (open theo object, không theo tên).
            name = fix_zip_entry_name(info)
            entry = EntryInfo(
                name=name, size=info.file_size, compress_size=info.compress_size,
                compress_type=_ZIP_METHOD_NAMES.get(info.compress_type, "method-%d" % info.compress_type),
                date_time=info.date_time, mode=(info.external_attr >> 16) & 0o7777,
                is_dir=name.endswith("/"),
                is_symlink=((info.external_attr >> 16) & 0o170000) == 0o120000,
                is_encrypted=bool(info.flag_bits & 0x1), order=order,
                extra={"external_attr": info.external_attr, "extra": info.extra.hex(),
                       "comment": info.comment.decode("utf-8", errors="replace"),
                       "crc": info.CRC, "create_system": info.create_system})
            try:
                self.limits.check_ratio(info.compress_size, info.file_size, name)
            except PB3Error as exc:   # chỉ CẢNH BÁO (đặc tả 5.2), không thành lỗi cứng
                inv.warnings.append("%s ở %s" % (exc.detail or exc.message_vi, name))
            if entry.is_dir:
                self._dirs.add(name)
                inv.entries.append(entry)
                continue
            reason = _unsafe_name_reason(name)
            if reason is None and entry.is_symlink:
                reason = "symlink bị chặn"
            if reason is None and entry.is_encrypted:
                reason = "entry mã hóa (không giải mã)"
            if reason is None and name in seen:
                reason = "trùng tên (chỉ dùng lần đầu)"
            if reason is not None:
                self._rejected[name] = reason
                inv.rejected.append((name, reason))
                continue
            seen.add(name)
            self._infos[name] = info
            inv.entries.append(entry)
        # dọn _rejected thừa: tên đã chấp nhận không còn nằm trong rejected;
        # dedupe rejected theo tên (giữ lý do đầu tiên).
        for n in list(self._rejected):
            if n in self._infos:
                del self._rejected[n]
        deduped: List[Tuple[str, str]] = []
        seen_rej = set()
        for n, r in inv.rejected:
            if n not in seen_rej:
                seen_rej.add(n)
                deduped.append((n, r))
        inv.rejected = deduped
        return inv

    def inventory(self) -> ContainerInventory:
        return self._inventory

    # ---- VFS ----
    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        for name in self._infos:                       # thứ tự trong archive
            if prefix is None or name.startswith(prefix):
                yield name

    def _lookup(self, path: str) -> zipfile.ZipInfo:
        check_vpath(path)
        info = self._infos.get(path)
        if info is not None:
            return info
        reason = self._rejected.get(path)
        if reason == "entry mã hóa (không giải mã)":
            raise PB3Error("E_ENCRYPTED", "entry mã hóa", where=path)
        if reason is not None:
            raise PB3Error("E_UNSAFE_PATH", reason, where=path)
        raise PB3Error("E_INPUT_NOT_FOUND", "không có trong container", where=path)

    def stat(self, path: str) -> VStat:
        if path in self._dirs or path.rstrip("/") in self._dirs:
            return VStat(path, 0, False)   # thư mục trong inventory: size 0, nhất quán với entries
        return VStat(path, self._lookup(path).file_size, False)

    def _open_raw(self, path: str):
        try:
            return self._zf.open(self._lookup(path), "r")
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
            if isinstance(exc, RuntimeError) and "encrypted" in str(exc).lower():
                raise PB3Error("E_ENCRYPTED", "entry mã hóa", where=path)
            raise error_from_exception(exc, where=path)

    # ---- đọc thô không kiểm CRC (cho slice/đọc tiền tố) ----
    def _raw_at(self, pos: int, n: int) -> bytes:
        """Đọc n byte thô từ file gốc tại vị trí pos (không qua zipfile)."""
        if self._path is not None:
            try:
                with open(str(self._path), "rb") as fh:
                    fh.seek(pos)
                    return fh.read(n)
            except OSError as exc:
                raise error_from_exception(exc, where=self.source_name)
        return (self._data or b"")[pos:pos + n]   # giữ bytes gốc, không getvalue() (copy) mỗi lần

    def _data_start(self, info: zipfile.ZipInfo) -> int:
        """Vị trí bắt đầu dữ liệu nén của entry (qua local header)."""
        head = self._raw_at(info.header_offset, 30)
        if len(head) < 30 or head[:4] != b"PK\x03\x04":
            raise PB3Error("E_BAD_ARCHIVE", "local header lỗi", where=info.filename)
        _sig, _ver, _flag, _method, _t, _d, _crc, _c, _u, nlen, elen = struct.unpack(
            "<IHHHHHIIIHH", head)
        return info.header_offset + 30 + nlen + elen

    def _slice_decompress(self, info: zipfile.ZipInfo, offset: int, take: int,
                          decomp: Any, header_skip: int = 0) -> bytes:
        """Slice bằng giải nén thô (deflate/bzip2/lzma): bỏ qua `offset` byte đầu,
        trả tối đa `take` byte. Luồng cắt cụt (hết dữ liệu nén mà chưa tới EOF
        của bộ giải nén) -> E_CORRUPT, khác với EOF hợp lệ ở cuối entry."""
        start = self._data_start(info) + header_skip
        pos, remaining_in = start, info.compress_size - header_skip
        skipped, out = 0, bytearray()
        pending = b""
        try:
            while len(out) < take and (remaining_in > 0 or pending) and not decomp.eof:
                if pending:
                    chunk = pending
                    pending = b""
                else:
                    chunk = self._raw_at(pos, min(65536, remaining_in))
                    if not chunk:
                        break
                    pos += len(chunk)
                    remaining_in -= len(chunk)
                data = decomp.decompress(chunk, take - len(out) + (offset - skipped))
                pending = decomp.unconsumed_tail
                if skipped < offset:
                    cut = min(len(data), offset - skipped)
                    data = data[cut:]
                    skipped += cut
                out.extend(data[:take - len(out)])
        except (zlib.error, OSError, EOFError, lzma.LZMAError) as exc:
            raise PB3Error("E_BAD_ARCHIVE", "giải nén lỗi: %s" % type(exc).__name__,
                           where=info.filename)
        if len(out) < take and not decomp.eof:
            raise PB3Error("E_CORRUPT", "luồng nén cắt cụt: đọc được %d/%d byte"
                           % (len(out), take), where=info.filename)
        return bytes(out)

    def _lzma_decompressor(self, info: zipfile.ZipInfo) -> Tuple[Any, int]:
        """Bộ giải nén LZMA kiểu ZIP (đầu 4 byte: version 2 + cỡ props 2,
        rồi props 5 byte) + số byte header cần bỏ qua."""
        start = self._data_start(info)
        head = self._raw_at(start, 9)
        if len(head) < 9:
            raise PB3Error("E_BAD_ARCHIVE", "header LZMA cắt cụt", where=info.filename)
        props_size = struct.unpack_from("<H", head, 2)[0]
        if props_size != 5:
            raise PB3Error("E_BAD_ARCHIVE", "LZMA props dài %d (cần 5)" % props_size,
                           where=info.filename)
        props = head[4:9]
        lc = props[0] % 9
        rem = props[0] // 9
        pb = rem // 5
        lp = rem % 5
        dict_size = struct.unpack_from("<I", props, 1)[0]
        dec = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                      "lc": lc, "lp": lp, "pb": pb}])
        return dec, 4 + props_size

    def _slice_raw(self, info: zipfile.ZipInfo, offset: int, size: int) -> Optional[bytes]:
        """Slice KHÔNG kiểm CRC bằng giải nén thô. Trả None nếu method chưa hỗ trợ."""
        if size == 0 or offset >= info.file_size:
            return b""
        take = min(size, info.file_size - offset)
        if info.compress_type == zipfile.ZIP_STORED:
            return self._raw_at(self._data_start(info) + offset, take)
        if info.compress_type == zipfile.ZIP_DEFLATED:
            return self._slice_decompress(info, offset, take, zlib.decompressobj(-15))
        if info.compress_type == zipfile.ZIP_BZIP2:
            return self._slice_decompress(info, offset, take, bz2.BZ2Decompressor())
        if info.compress_type == zipfile.ZIP_LZMA:
            dec, skip = self._lzma_decompressor(info)
            return self._slice_decompress(info, offset, take, dec, skip)
        return None

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        info = self._lookup(path)
        take = read_policy(info.file_size, max_bytes, self.limits, path)
        if take < info.file_size:
            raw = self._slice_raw(info, 0, take)   # tiền tố: không kiểm CRC
            if raw is not None:
                if len(raw) != take:
                    raise PB3Error("E_CORRUPT", "đọc được %d/%d byte" % (len(raw), take), where=path)
                return raw
        try:
            with self._open_raw(path) as stream:
                data = _read_exact(stream, take, path) if take < info.file_size else stream.read()
        except zipfile.BadZipFile:
            raise PB3Error("E_BAD_ARCHIVE", "CRC sai hoặc cấu trúc lỗi khi đọc", where=path)
        if len(data) != take:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte" % (len(data), take), where=path)
        return data

    def slice(self, path: str, offset: int, size: int) -> bytes:
        info = self._lookup(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int >= 0", where=path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise PB3Error("E_PLUGIN_INVALID", "size phải là int >= 0", where=path)
        remaining = max(0, info.file_size - offset)
        self.limits.check_memory_read(min(size, remaining), where=path)
        raw = self._slice_raw(info, offset, size)
        if raw is not None:
            return raw
        try:   # method chưa có slice thô: dùng luồng zipfile (có thể báo CRC sai)
            with self._open_raw(path) as stream:
                skipped = 0
                while skipped < offset:
                    chunk = stream.read(min(self.limits.chunk_size, offset - skipped))
                    if not chunk:
                        break
                    skipped += len(chunk)
                out = bytearray()
                while len(out) < size:
                    chunk = stream.read(min(self.limits.chunk_size, size - len(out)))
                    if not chunk:
                        break
                    out.extend(chunk)
        except zipfile.BadZipFile:
            raise PB3Error("E_BAD_ARCHIVE", "CRC sai hoặc cấu trúc lỗi khi đọc", where=path)
        want = min(size, max(0, info.file_size - offset))
        if len(out) < want:
            raise PB3Error("E_CORRUPT", "slice cắt cụt: đọc được %d/%d byte"
                           % (len(out), want), where=path)
        return bytes(out)

    def open_stream(self, path: str):
        self._lookup(path)
        return _ROStream(self._open_raw(path), path)

    def close(self) -> None:
        try:
            self._zf.close()
        except (zipfile.BadZipFile, OSError):
            pass

    def __enter__(self) -> "ZipVFS":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


# ---------------------------------------------------------------------- TAR
class TarVFS(VFS):
    """VFS chỉ đọc trên TAR (kể cả TAR.GZ/TAR.XZ qua `tarfile`, thư viện chuẩn).

    Chỉ file thường được đọc; symlink/hardlink, thiết bị, tên không an toàn
    bị loại khỏi VFS và ghi vào inventory.rejected. TAR không có CRC từng
    entry nên `VStat.crc_verified` luôn False. Không dùng `filter=` của
    tarfile (chỉ có từ Python 3.12; iSH dùng 3.9): kiểm tên bằng tay.
    """

    kind = KIND_TAR

    def __init__(self, source: Union[str, os.PathLike, bytes, bytearray], *,
                 limits: Optional[Limits] = None, source_name: str = "",
                 compression: Optional[str] = None):
        self.limits = limits or Limits()
        self.compression = compression or "none"
        self._path: Optional[Path] = None
        self._buf: Optional[io.BytesIO] = None
        if isinstance(source, (str, os.PathLike)):
            self._path = safe_io.check_readable_input(source, self.limits)
            self.source_name = source_name or self._path.name
            opener = lambda: tarfile.open(str(self._path), mode=self._tar_mode())
        elif isinstance(source, (bytes, bytearray)):
            self._buf = io.BytesIO(bytes(source))
            self.source_name = source_name or "<bytes>"
            opener = lambda: tarfile.open(fileobj=self._buf, mode=self._tar_mode())
        else:
            raise PB3Error("E_PLUGIN_INVALID", "TarVFS cần đường dẫn tệp hoặc bytes",
                           where=source_name or "?")
        try:
            self._tf = opener()
            # Liệt kê LAZY (next() từng member): vượt giới hạn thì dừng sớm,
            # không đọc hết cả tar lớn chỉ để báo E_ARCHIVE_BOMB.
            members = iter(self._tf)
        except (tarfile.TarError, EOFError, OSError, lzma.LZMAError) as exc:
            raise _tar_error(exc, self.source_name)
        self._members: Dict[str, tarfile.TarInfo] = {}
        self._rejected: Dict[str, str] = {}
        self._dirs: set = set()
        try:
            self._inventory = self._build_inventory(members)
        except (tarfile.TarError, EOFError, OSError, lzma.LZMAError) as exc:
            raise _tar_error(exc, self.source_name)

    def _tar_mode(self) -> str:
        return {"none": "r:", "gzip": "r:gz", "xz": "r:xz", "bzip2": "r:bz2"}.get(
            self.compression, "r:*")

    def _build_inventory(self, members) -> ContainerInventory:
        inv = ContainerInventory(kind=KIND_TAR, source_name=self.source_name)
        seen = set()
        total = 0
        for order, m in enumerate(members):
            # kiểm giới hạn NGAY khi liệt kê (dừng sớm thay vì đọc hết tar)
            self.limits.check_entries(order + 1, self.source_name)
            if m.isfile():
                total += m.size
                self.limits.check_total(total, self.source_name)
            name = m.name
            is_dir = m.isdir() or name.endswith("/")
            try:
                dt = time.gmtime(m.mtime)[:6] if m.mtime else None
            except (ValueError, OverflowError, OSError):
                dt = None   # mtime quá lớn/âm: không có date_time thay vì crash
            entry = EntryInfo(
                name=name, size=m.size, compress_size=0, compress_type=self.compression,
                date_time=dt, mode=m.mode,
                is_dir=is_dir, is_symlink=m.issym() or m.islnk(), order=order,
                extra={"type": m.type.decode("ascii", errors="replace") if isinstance(m.type, bytes) else str(m.type),
                       "uid": m.uid, "gid": m.gid, "uname": m.uname})
            if is_dir:
                self._dirs.add(name)
                inv.entries.append(entry)
                continue
            reason = _unsafe_name_reason(name.rstrip("/"))
            if reason is None and entry.is_symlink:
                reason = "symlink/hardlink bị chặn"
            if reason is None and not m.isfile():
                reason = "loại entry không hỗ trợ (chỉ file thường)"
            if reason is None and name in seen:
                reason = "trùng tên (chỉ dùng lần đầu)"
            if reason is not None:
                self._rejected[name] = reason
                inv.rejected.append((name, reason))
                continue
            seen.add(name)
            self._members[name] = m
            inv.entries.append(entry)
        for n in list(self._rejected):
            if n in self._members:
                del self._rejected[n]
        deduped: List[Tuple[str, str]] = []
        seen_rej = set()
        for n, r in inv.rejected:
            if n not in seen_rej:
                seen_rej.add(n)
                deduped.append((n, r))
        inv.rejected = deduped
        return inv

    def inventory(self) -> ContainerInventory:
        return self._inventory

    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        for name in self._members:
            if prefix is None or name.startswith(prefix):
                yield name

    def _lookup(self, path: str) -> tarfile.TarInfo:
        check_vpath(path)
        member = self._members.get(path)
        if member is not None:
            return member
        reason = self._rejected.get(path)
        if reason is not None:
            raise PB3Error("E_UNSAFE_PATH", reason, where=path)
        raise PB3Error("E_INPUT_NOT_FOUND", "không có trong container", where=path)

    def stat(self, path: str) -> VStat:
        if path in self._dirs or path.rstrip("/") in self._dirs:
            return VStat(path, 0, False)   # thư mục trong inventory: size 0, nhất quán với entries
        return VStat(path, self._lookup(path).size, False)

    def _open_raw(self, path: str):
        member = self._lookup(path)
        try:
            stream = self._tf.extractfile(member)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _tar_error(exc, path)
        if stream is None:
            raise PB3Error("E_INPUT_NOT_FOUND", "không trích được entry", where=path)
        return stream

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        member = self._lookup(path)
        take = read_policy(member.size, max_bytes, self.limits, path)
        try:
            with self._open_raw(path) as stream:
                data = _read_exact(stream, take, path) if take < member.size else stream.read()
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _tar_error(exc, path)
        if len(data) != take:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte" % (len(data), take), where=path)
        return data

    def slice(self, path: str, offset: int, size: int) -> bytes:
        member = self._lookup(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int >= 0", where=path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise PB3Error("E_PLUGIN_INVALID", "size phải là int >= 0", where=path)
        remaining = max(0, member.size - offset)
        self.limits.check_memory_read(min(size, remaining), where=path)
        try:
            with self._open_raw(path) as stream:
                skipped = 0
                while skipped < offset:
                    chunk = stream.read(min(self.limits.chunk_size, offset - skipped))
                    if not chunk:
                        break
                    skipped += len(chunk)
                out = bytearray()
                while len(out) < size:
                    chunk = stream.read(min(self.limits.chunk_size, size - len(out)))
                    if not chunk:
                        break
                    out.extend(chunk)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise _tar_error(exc, path)
        want = min(size, max(0, member.size - offset))
        if len(out) < want:
            raise PB3Error("E_CORRUPT", "slice cắt cụt: đọc được %d/%d byte"
                           % (len(out), want), where=path)
        return bytes(out)

    def open_stream(self, path: str):
        self._lookup(path)
        return _ROStream(self._open_raw(path), path)

    def close(self) -> None:
        try:
            self._tf.close()
        except (tarfile.TarError, OSError):
            pass

    def __enter__(self) -> "TarVFS":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


# ------------------------------------------------- luồng nén đơn (GZ/XZ lẻ)
class SingleStreamVFS(VFS):
    """VFS chỉ đọc cho MỘT luồng nén (file .gz/.xz lẻ, không phải tar).

    Một entry duy nhất; kích thước được đếm bằng một lượt giải nén streaming
    (không nạp cả file vào RAM) rồi ghi nhớ. Đọc/slice/stream đều giải nén
    lại từ đầu theo yêu cầu.
    """

    def __init__(self, source: Union[str, os.PathLike, bytes, bytearray], *,
                 kind: str, limits: Optional[Limits] = None,
                 entry_name: Optional[str] = None, source_name: str = ""):
        if kind not in (KIND_GZIP, KIND_XZ, KIND_BZIP2):
            raise PB3Error("E_PLUGIN_INVALID", "SingleStreamVFS chỉ nhận 'gzip'/'xz'/'bzip2'",
                           where=source_name or "?")
        self.kind = kind
        self.limits = limits or Limits()
        self._path: Optional[Path] = None
        self._data: Optional[bytes] = None
        if isinstance(source, (str, os.PathLike)):
            self._path = safe_io.check_readable_input(source, self.limits)
            self.source_name = source_name or self._path.name
            base = self._path.name
        elif isinstance(source, (bytes, bytearray)):
            self._data = bytes(source)
            self.source_name = source_name or "<bytes>"
            base = ""
        else:
            raise PB3Error("E_PLUGIN_INVALID", "SingleStreamVFS cần đường dẫn tệp hoặc bytes",
                           where=source_name or "?")
        if entry_name:
            self.entry_name = entry_name
        else:
            stem = base
            for suffix in (".gz", ".xz", ".lzma"):
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            try:
                check_vpath(stem)
                self.entry_name = stem
            except PB3Error:
                self.entry_name = "data"
        self._size: Optional[int] = None

    def _open_raw(self):
        try:
            if self.kind == KIND_GZIP:
                if self._path is not None:
                    return gzip.GzipFile(filename=str(self._path), mode="rb")
                return gzip.GzipFile(fileobj=io.BytesIO(self._data), mode="rb")
            if self.kind == KIND_BZIP2:
                if self._path is not None:
                    return bz2.BZ2File(str(self._path), mode="rb")
                return bz2.BZ2File(io.BytesIO(self._data), mode="rb")
            if self._path is not None:
                return lzma.LZMAFile(str(self._path), mode="rb")
            return lzma.LZMAFile(io.BytesIO(self._data), mode="rb")
        except (OSError, lzma.LZMAError, EOFError) as exc:
            raise error_from_exception(exc, where=self.source_name)

    def _count_size(self) -> int:
        if self._size is None:
            total = 0
            try:
                with self._open_raw() as stream:
                    while True:
                        chunk = stream.read(self.limits.chunk_size)
                        if not chunk:
                            break
                        total += len(chunk)
                        self.limits.check_total(total, self.source_name)
            except PB3Error:
                raise
            except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError) as exc:
                raise PB3Error("E_BAD_ARCHIVE", "giải nén lỗi: %s" % type(exc).__name__,
                               where=self.source_name)
            self._size = total
        return self._size

    def inventory(self) -> ContainerInventory:
        inv = ContainerInventory(kind=self.kind, source_name=self.source_name)
        inv.entries.append(EntryInfo(name=self.entry_name, size=self._count_size(),
                                     compress_type=self.kind, order=0))
        return inv

    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        if prefix is None or self.entry_name.startswith(prefix):
            yield self.entry_name

    def _check_path(self, path: str) -> None:
        check_vpath(path)
        if path != self.entry_name:
            raise PB3Error("E_INPUT_NOT_FOUND", "không có trong container", where=path)

    def stat(self, path: str) -> VStat:
        self._check_path(path)
        return VStat(path, self._count_size(), False)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        self._check_path(path)
        if max_bytes is not None and max_bytes < self.limits.max_entry_bytes:
            # Đọc TIỀN TỐ nhỏ: không cần đếm tổng kích thước (tránh giải nén
            # cả luồng lớn chỉ để đọc vài KB đầu); EOF sớm là hợp lệ.
            take = min(max_bytes, self.limits.max_memory_read_bytes)
            self.limits.check_memory_read(take, where=path)
            try:
                with self._open_raw() as stream:
                    return stream.read(take)
            except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError) as exc:
                raise PB3Error("E_BAD_ARCHIVE", "giải nén lỗi: %s" % type(exc).__name__, where=path)
        size = self._count_size()
        take = read_policy(size, max_bytes, self.limits, path)
        try:
            with self._open_raw() as stream:
                if take == size:
                    out = bytearray()
                    while True:
                        chunk = stream.read(self.limits.chunk_size)
                        if not chunk:
                            break
                        out.extend(chunk)
                    data = bytes(out)
                else:
                    data = _read_exact(stream, take, path)
        except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError) as exc:
            raise PB3Error("E_BAD_ARCHIVE", "giải nén lỗi: %s" % type(exc).__name__, where=path)
        if len(data) != take:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte" % (len(data), take), where=path)
        return data

    def slice(self, path: str, offset: int, size: int) -> bytes:
        self._check_path(path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PB3Error("E_PLUGIN_INVALID", "offset phải là int >= 0", where=path)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise PB3Error("E_PLUGIN_INVALID", "size phải là int >= 0", where=path)
        total = self._count_size()
        remaining = max(0, total - offset)
        self.limits.check_memory_read(min(size, remaining), where=path)
        try:
            with self._open_raw() as stream:
                skipped = 0
                while skipped < offset:
                    chunk = stream.read(min(self.limits.chunk_size, offset - skipped))
                    if not chunk:
                        break
                    skipped += len(chunk)
                out = bytearray()
                while len(out) < size:
                    chunk = stream.read(min(self.limits.chunk_size, size - len(out)))
                    if not chunk:
                        break
                    out.extend(chunk)
        except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError) as exc:
            raise PB3Error("E_BAD_ARCHIVE", "giải nén lỗi: %s" % type(exc).__name__, where=path)
        want = min(size, max(0, total - offset))
        if len(out) < want:
            raise PB3Error("E_CORRUPT", "slice cắt cụt: đọc được %d/%d byte"
                           % (len(out), want), where=path)
        return bytes(out)

    def open_stream(self, path: str):
        self._check_path(path)
        return _ROStream(self._open_raw(), path)

    def close(self) -> None:
        return None

    def __enter__(self) -> "SingleStreamVFS":
        return self

    def __exit__(self, *exc) -> bool:
        return False


# ------------------------------------------------------------------ mở chung
def _probe_tar_first_members(source, kind: str) -> bool:
    """Thử đọc vài member đầu để biết có phải tar không (không đọc hết file).
    Trả True nếu tarfile mở được và đọc được member đầu (hoặc tar rỗng)."""
    mode = {"gzip": "r:gz", "xz": "r:xz", "bzip2": "r:bz2"}.get(kind, "r:*")
    try:
        if isinstance(source, (str, os.PathLike)):
            tf = tarfile.open(str(source), mode=mode)
        else:
            tf = tarfile.open(fileobj=io.BytesIO(bytes(source)), mode=mode)
    except (tarfile.TarError, EOFError, OSError, lzma.LZMAError):
        return False
    try:
        for _ in range(4):
            m = tf.next()
            if m is None:
                break
        return True
    except (tarfile.TarError, EOFError, OSError, lzma.LZMAError):
        return False
    finally:
        try:
            tf.close()
        except (tarfile.TarError, OSError):
            pass


def _open_compressed(source, kind: str, limits: Limits, name: str) -> VFS:
    """GZIP/XZ/BZ2: thử TAR trước (tar.gz/tar.xz/tar.bz2), không phải tar thì là luồng đơn.
    Chỉ thử TarVFS khi probe vài member đầu thành công (tránh đọc hết tar lớn
    chỉ để biết nó không phải tar); tar.gz HỎNG (luồng nén lỗi) vẫn báo lỗi
    tar rõ ràng thay vì lặng lẽ thành luồng đơn."""
    if _probe_tar_first_members(source, kind):
        return TarVFS(source, limits=limits, source_name=name, compression=kind)
    single = SingleStreamVFS(source, kind=kind, limits=limits, source_name=name)
    try:
        with single._open_raw() as stream:
            stream.read(65536)   # probe: luồng nén có giải nén được không?
    except (OSError, EOFError, gzip.BadGzipFile, lzma.LZMAError) as exc:
        raise PB3Error("E_BAD_ARCHIVE",
                       "không phải tar và luồng nén cũng lỗi (%s); file %s có thể bị hỏng"
                       % (type(exc).__name__, kind), where=name)
    return single


def _looks_like_pak(source: Union[str, os.PathLike, bytes, bytearray]) -> bool:
    """Heuristic: magic footer PAK có trong ~4KB cuối file không?

    Chỉ là gợi ý để chọn nhánh mở; PakVFS vẫn xác minh footer đầy đủ và
    báo E_BAD_ARCHIVE nếu không đúng. Trả False khi không đọc được đuôi."""
    try:
        if isinstance(source, (bytes, bytearray)):
            tail = bytes(source)[-_PAK_TAIL_SCAN:]
        else:
            path = safe_io.check_readable_input(source, Limits())
            size = path.stat().st_size
            with open(str(path), "rb") as fh:
                fh.seek(max(0, size - _PAK_TAIL_SCAN))
                tail = fh.read(_PAK_TAIL_SCAN)
    except (OSError, PB3Error):
        return False
    return UNREAL_PAK_MAGIC in tail


def open_vfs(source: Union[str, os.PathLike, bytes, bytearray], *,
             limits: Optional[Limits] = None, source_name: str = "") -> VFS:
    """Mở container (đường dẫn tệp hoặc bytes) thành VFS chỉ đọc.

    ZIP -> ZipVFS; TAR -> TarVFS; GZ/XZ -> TarVFS nếu là tar nén, không thì
    SingleStreamVFS; Godot PCK -> sdk.godot.PckVFS; Unreal PAK (nhận diện
    bằng magic footer) -> sdk.unreal.PakVFS; 7z -> E_UNSUPPORTED_FORMAT
    (chưa có backend); không nhận diện được -> E_UNSUPPORTED_FORMAT
    (thử TAR v7 không magic trước).
    """
    lim = limits or Limits()
    if isinstance(source, (str, os.PathLike)):
        path = safe_io.check_readable_input(source, lim)
        name = source_name or path.name
        try:
            with open(str(path), "rb") as fh:
                head = fh.read(2048)
        except OSError as exc:
            raise error_from_exception(exc, where=name)
        src: Union[str, os.PathLike, bytes, bytearray] = path
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        name = source_name or "<bytes>"
        head = data[:2048]
        src = data
    else:
        raise PB3Error("E_PLUGIN_INVALID", "open_vfs cần đường dẫn tệp hoặc bytes",
                       where=source_name or "?")
    kind = detect_container_kind(head)
    if kind == KIND_ZIP:
        return ZipVFS(src, limits=lim, source_name=name)
    if kind == KIND_GODOT_PCK:
        try:
            from sdk import godot as godot_sdk
        except ImportError:
            # Lưới an toàn: backend đã có từ Claude 08; import lỗi chỉ khi
            # cây thiếu file — trả lỗi sạch thay vì ImportError (crash).
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "Godot PCK: không nạp được backend (thiếu sdk.godot)",
                           where=name)
        return godot_sdk.PckVFS(src, limits=lim, source_name=name)
    if kind == KIND_TAR:
        return TarVFS(src, limits=lim, source_name=name)
    if kind in (KIND_GZIP, KIND_XZ, KIND_BZIP2):
        return _open_compressed(src, kind, lim, name)
    if kind == KIND_7Z:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "7z chưa có backend (không thêm phụ thuộc chưa kiểm chứng)",
                       where=name)
    if _looks_like_pak(src):
        try:
            from sdk import unreal as unreal_sdk
        except ImportError:
            # Lưới an toàn như Godot ở trên (backend đã có từ Claude 08).
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "Unreal PAK: không nạp được backend (thiếu sdk.unreal)",
                           where=name)
        try:
            return unreal_sdk.PakVFS(src, limits=lim, source_name=name)
        except PB3Error as exc:
            if exc.code != "E_BAD_ARCHIVE":
                raise
    try:   # TAR v7 không có magic "ustar": thử mở bằng tarfile
        return TarVFS(src, limits=lim, source_name=name)
    except PB3Error as exc:
        if exc.code != "E_BAD_ARCHIVE":
            raise
    try:   # ZIP có dữ liệu thêm ở đầu (prepended): zipfile tự tìm EOCD
        return ZipVFS(src, limits=lim, source_name=name)
    except PB3Error as exc:
        if exc.code != "E_BAD_ARCHIVE":
            raise
    raise PB3Error("E_UNSUPPORTED_FORMAT", "không nhận diện được container (không phải ZIP/TAR/GZ/XZ/BZ2/PCK/PAK)",
                   where=name)


def open_vfs_bytes(data: bytes, *, limits: Optional[Limits] = None, source_name: str = "") -> VFS:
    """Bí danh rõ nghĩa cho open_vfs(bytes)."""
    return open_vfs(data, limits=limits, source_name=source_name)


# ------------------------------------------------------------ container lồng
class NestedVFS(VFS):
    """VFS trên một container gốc, mở container con lazy qua '::'.

    Mỗi đoạn giữa là một container con: đọc bytes qua VFS cha (giới hạn bởi
    read_policy của cha), mở bằng open_vfs, cache theo hash SHA-256 (tối đa
    MAX_NESTED_CACHE, đuổi bản cũ nhất). Độ sâu > limits.max_depth hoặc hash
    con trùng hash tổ tiên (vòng lặp) -> E_ARCHIVE_BOMB. `iter_entries`
    không liệt kê đệ quy container con khi không có tiền tố '::' (lazy).
    """

    def __init__(self, inner: VFS, *, limits: Optional[Limits] = None):
        if not isinstance(inner, VFS):
            raise PB3Error("E_PLUGIN_INVALID", "NestedVFS cần một VFS", where="?")
        self._inner = inner
        self.limits = limits or getattr(inner, "limits", None) or Limits()
        # cache: (id(VFS cha), tên đoạn) -> (VFS con, digest bytes đoạn đó).
        # VFS con được giữ tham chiếu mạnh nên id() không bị tái dùng khi còn cache.
        self._cache: Dict[Tuple[int, str], Tuple[VFS, str]] = {}

    # ---- phân giải ----
    def _resolve_container(self, segments: List[str]) -> VFS:
        current: VFS = self._inner
        seen = set()
        for depth, seg in enumerate(segments, 1):
            self.limits.check_depth(depth, where=SEP.join(segments))
            key = (id(current), seg)
            hit = self._cache.get(key)
            if hit is not None:
                child, digest = hit
                if digest in seen:
                    raise PB3Error("E_ARCHIVE_BOMB", "container lồng lặp lại chính nó (vòng lặp)",
                                   where=SEP.join(segments))
                seen.add(digest)
                current = child
                continue
            data = current.read(seg, None)
            digest = hashlib.sha256(data).hexdigest()
            if digest in seen:
                raise PB3Error("E_ARCHIVE_BOMB", "container lồng lặp lại chính nó (vòng lặp)",
                               where=SEP.join(segments))
            seen.add(digest)
            child = open_vfs(data, limits=self.limits, source_name=seg)
            if len(self._cache) >= MAX_NESTED_CACHE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (child, digest)
            current = child
        return current

    def _resolve(self, path: str) -> Tuple[VFS, str]:
        parts = check_vpath(path).split(SEP)
        return self._resolve_container(parts[:-1]), parts[-1]

    # ---- VFS ----
    def iter_entries(self, prefix: Optional[str] = None) -> Iterator[str]:
        if prefix is None or SEP not in prefix:
            yield from self._inner.iter_entries(prefix)
            return
        if prefix.endswith(SEP):                       # tiền tố là chính container: "...::"
            container_parts = check_vpath(prefix[: -len(SEP)]).split(SEP)
            entry_prefix = None
        else:
            parts = check_vpath(prefix).split(SEP)
            container_parts, entry_prefix = parts[:-1], parts[-1]
        container = self._resolve_container(container_parts)
        head = SEP.join(container_parts) + SEP
        for name in container.iter_entries(entry_prefix):
            yield head + name

    def stat(self, path: str) -> VStat:
        if SEP not in path:
            return self._inner.stat(path)
        container, entry = self._resolve(path)
        return container.stat(entry)

    def read(self, path: str, max_bytes: Optional[int] = None) -> bytes:
        if SEP not in path:
            return self._inner.read(path, max_bytes)
        container, entry = self._resolve(path)
        return container.read(entry, max_bytes)

    def slice(self, path: str, offset: int, size: int) -> bytes:
        if SEP not in path:
            return self._inner.slice(path, offset, size)
        container, entry = self._resolve(path)
        return container.slice(entry, offset, size)

    def open_stream(self, path: str):
        if SEP not in path:
            return self._inner.open_stream(path)
        container, entry = self._resolve(path)
        return container.open_stream(entry)

    def inventory(self, path: Optional[str] = None) -> ContainerInventory:
        if path is None:
            inv = getattr(self._inner, "inventory", None)
            if inv is None:
                raise PB3Error("E_NOT_IMPLEMENTED", "VFS gốc không có inventory",
                               where=getattr(self._inner, "source_name", "?"))
            return inv()
        # Toàn bộ `path` là đường tới container con (vd "a.zip::b.zip"):
        # phân giải HẾT các đoạn, không coi đoạn cuối là entry.
        container = self._resolve_container(check_vpath(path).split(SEP))
        inv = getattr(container, "inventory", None)
        if inv is None:
            raise PB3Error("E_NOT_IMPLEMENTED", "container con không có inventory",
                           where=getattr(container, "source_name", "?"))
        return inv()

    def close(self) -> None:
        for child, _digest in self._cache.values():
            close = getattr(child, "close", None)
            if close is not None:
                try:
                    close()
                except (PB3Error, OSError):
                    pass
        self._cache.clear()
        close = getattr(self._inner, "close", None)
        if close is not None:
            try:
                close()
            except (PB3Error, OSError):
                pass

    def __enter__(self) -> "NestedVFS":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


# ------------------------------------------------------------------ ghi ZIP
@dataclass
class ZipRebuildReport:
    """Kết quả rebuild_zip: entry nào giữ, thay, thêm; method nào đổi."""
    source_name: str
    dest_name: str
    write_mode: str
    copied: int = 0
    replaced: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    method_changes: List[Tuple[str, str, str]] = field(default_factory=list)  # (tên, cũ, mới)
    warnings: List[str] = field(default_factory=list)
    entries: int = 0
    bytes_written: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {"source": self.source_name, "dest": self.dest_name, "write_mode": self.write_mode,
                "copied": self.copied, "replaced": list(self.replaced), "added": list(self.added),
                "method_changes": [[n, o, w] for n, o, w in self.method_changes],
                "warnings": list(self.warnings), "entries": self.entries,
                "bytes_written": self.bytes_written}


def _zip_info_copy(info: zipfile.ZipInfo, compress_type: int, replaced: bool = False) -> zipfile.ZipInfo:
    # Ghi lại bằng tên ĐÃ sửa mojibake (fix_zip_entry_name): nếu giữ tên
    # mojibake, zipfile sẽ mã hóa nó thành UTF-8 và "đóng đinh" lỗi sai vào
    # file đích. Tên ASCII/tên đã có cờ UTF-8 thì fix trả về y nguyên.
    out = zipfile.ZipInfo(fix_zip_entry_name(info), info.date_time)
    out.compress_type = compress_type
    out.external_attr = info.external_attr
    out.internal_attr = info.internal_attr
    out.extra = info.extra
    out.comment = info.comment
    out.create_system = info.create_system
    # Giữ metadata gốc (kể cả cờ UTF-8 trong flag_bits — mất cờ này gây
    # mojibake tên file; entry được THAY thì bỏ cờ mã hóa vì dữ liệu mới
    # đã là plaintext).
    out.create_version = info.create_version
    out.extract_version = info.extract_version
    out.flag_bits = info.flag_bits & ~0x1 if replaced else info.flag_bits
    return out


def rebuild_zip(source: Union[str, os.PathLike, bytes, bytearray],
                dest: Union[str, os.PathLike],
                replacements: Optional[Dict[str, bytes]] = None, *,
                limits: Optional[Limits] = None,
                method_overrides: Optional[Dict[str, Union[int, str]]] = None,
                write_mode: str = "REBUILD_ALL", verify: bool = True) -> ZipRebuildReport:
    """Dựng lại ZIP: giữ thứ tự/method/metadata của entry không đổi; thay entry
    trong `replacements` (tên mới được thêm vào cuối); đổi method chỉ khi
    `method_overrides` yêu cầu (ghi vào report).

    Ghi bằng `safe_io.AtomicWriter` (temp cùng thư mục -> verify -> fsync ->
    os.replace): lỗi giữa chừng thì file đích cũ còn nguyên, temp bị dọn.
    Entry mã hóa mà không được thay -> E_ENCRYPTED (không thể ghi lại khi
    không giải mã). Entry tên không an toàn được giữ nguyên vẹn và ghi cảnh
    báo (an toàn vẫn do phía đọc chặn, đặc tả 5.2).
    """
    if write_mode not in WRITE_MODES:
        raise PB3Error("E_PLUGIN_INVALID", "write_mode phải thuộc %s" % "|".join(WRITE_MODES),
                       where="rebuild_zip")
    lim = limits or Limits()
    repl = dict(replacements or {})
    for name, data in repl.items():
        check_vpath(name)
        if not isinstance(data, (bytes, bytearray)):
            raise PB3Error("E_PLUGIN_INVALID", "replacement phải là bytes", where=name)
        lim.check_entry_size(len(data), name)   # replacement cũng chịu giới hạn entry
    overrides: Dict[str, int] = {}
    for name, method in (method_overrides or {}).items():
        check_vpath(name)
        if isinstance(method, str):
            if method not in _ZIP_METHOD_CODES:
                raise PB3Error("E_PLUGIN_INVALID", "method lạ: %r" % method, where=name)
            overrides[name] = _ZIP_METHOD_CODES[method]
        elif isinstance(method, int) and method in _ZIP_METHOD_NAMES:
            overrides[name] = method
        else:
            raise PB3Error("E_PLUGIN_INVALID", "method lạ: %r" % (method,), where=name)
    if isinstance(source, (str, os.PathLike)):
        src_path = safe_io.check_readable_input(source, lim)
        src_name = str(src_path)
        src_bytes: Optional[bytes] = None
    elif isinstance(source, (bytes, bytearray)):
        src_path = None
        src_bytes = bytes(source)
        src_name = "<bytes>"
    else:
        raise PB3Error("E_PLUGIN_INVALID", "rebuild_zip cần đường dẫn tệp hoặc bytes",
                       where="rebuild_zip")
    if src_path is not None:
        # Chốt dest != source (sửa-lỗi): không bao giờ ghi đè file gốc,
        # kể cả khi caller truyền cùng đường dẫn qua dạng viết khác
        # (realpath) hoặc symlink/hardlink (samefile). Trước đây
        # rebuild_zip(src, src) ghi đè nguồn thành công.
        if os.path.realpath(str(dest)) == os.path.realpath(str(src_path)):
            raise PB3Error("E_CONFIG_INVALID",
                           "không ghi đè file gốc: đích trùng nguồn",
                           where=str(dest))
        if os.path.exists(str(dest)):
            try:
                if os.path.samefile(str(src_path), str(dest)):
                    raise PB3Error("E_CONFIG_INVALID",
                                   "không ghi đè file gốc: đích trùng nguồn",
                                   where=str(dest))
            except OSError:
                pass
    try:
        zf = (zipfile.ZipFile(str(src_path), "r") if src_path is not None
              else zipfile.ZipFile(io.BytesIO(src_bytes), "r"))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise error_from_exception(exc, where=src_name)
    try:
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        zf.close()
        raise error_from_exception(exc, where=src_name)
    # Mọi so sánh tên ở dưới dùng tên ĐÃ sửa mojibake: khóa trong `repl` /
    # `overrides` do phía gọi đưa vào đều là đường dẫn VFS (tên đã sửa),
    # còn info.filename gốc có thể vẫn là chuỗi mojibake. Đọc dữ liệu vẫn
    # dùng object ZipInfo gốc nên không ảnh hưởng.
    names = [fix_zip_entry_name(i) for i in infos]
    for name in overrides:
        if name not in names and name not in repl:
            zf.close()
            raise PB3Error("E_INPUT_NOT_FOUND", "method_overrides cho entry không có", where=name)
    for info in infos:
        if info.flag_bits & 0x1 and fix_zip_entry_name(info) not in repl:
            zf.close()
            raise PB3Error("E_ENCRYPTED", "không ghi lại entry mã hóa khi không thay thế",
                           where=fix_zip_entry_name(info))
    expected: List[Tuple[str, int, int]] = []        # (tên, crc, size) để verify
    report = ZipRebuildReport(source_name=src_name, dest_name=str(dest), write_mode=write_mode)
    for info in infos:
        _fixed = fix_zip_entry_name(info)
        if _unsafe_name_reason(_fixed.rstrip("/")) is not None and not _fixed.endswith("/"):
            report.warnings.append("giữ nguyên entry tên không an toàn: %s" % _fixed)
    # Kích thước đích dự kiến = nguồn - cỡ cũ của entry được thay (lần đầu)
    # + cỡ mới + cỡ entry thêm mới (trước đây cộng gộp sai: nguồn + mọi replacement).
    src_size = (os.path.getsize(str(src_path)) if src_path is not None else len(src_bytes or b""))
    _src_names = [fix_zip_entry_name(i) for i in infos]
    expected_size = src_size
    _seen_repl: set = set()
    for info in infos:
        _fixed = fix_zip_entry_name(info)
        if _fixed in repl and _fixed not in _seen_repl:
            _seen_repl.add(_fixed)
            expected_size += len(repl[_fixed]) - info.file_size
    for name, data in repl.items():
        if name not in _src_names:
            expected_size += len(data)

    def _verify(tmp: Path) -> None:
        try:
            with zipfile.ZipFile(str(tmp), "r") as check:
                got = check.infolist()
                if [i.filename for i in got] != [n for n, _c, _s in expected]:
                    raise PB3Error("E_VERIFY_FAILED", "thứ tự/tên entry sau khi ghi khác dự kiến",
                                   where=str(dest))
                for info, (name, crc, size) in zip(got, expected):
                    if info.CRC != crc or info.file_size != size:
                        raise PB3Error("E_VERIFY_FAILED", "entry %s sai CRC/kích thước sau khi ghi" % name,
                                       where=str(dest))
                    # CRC streaming (không nạp cả entry vào RAM)
                    running = 0
                    n_read = 0
                    with check.open(info) as stream:
                        while True:
                            chunk = stream.read(lim.chunk_size)
                            if not chunk:
                                break
                            running = binascii.crc32(chunk, running)
                            n_read += len(chunk)
                    if (running & 0xFFFFFFFF) != crc or n_read != size:
                        raise PB3Error("E_VERIFY_FAILED", "entry %s sai CRC/kích thước sau khi ghi" % name,
                                       where=str(dest))
        except PB3Error:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
            raise PB3Error("E_VERIFY_FAILED", "không mở được bản ghi để xác minh: %s"
                           % type(exc).__name__, where=str(tmp))

    dest_path = Path(dest)
    try:
        with safe_io.AtomicWriter(dest_path, expected_size=expected_size, disk_factor=lim.disk_factor,
                                  verify=_verify if verify else None) as writer:
            with zipfile.ZipFile(writer.path, "w", zipfile.ZIP_DEFLATED) as zout:
                zout.comment = zf.comment   # giữ archive comment gốc
                replaced_once: set = set()
                for info in infos:
                    name = fix_zip_entry_name(info)
                    new_method = overrides.get(name, info.compress_type)
                    if new_method != info.compress_type:
                        report.method_changes.append(
                            (name, _ZIP_METHOD_NAMES.get(info.compress_type, "method-%d" % info.compress_type),
                             _ZIP_METHOD_NAMES.get(new_method, "method-%d" % new_method)))
                    is_repl = name in repl and name not in replaced_once
                    zinfo = _zip_info_copy(info, new_method, replaced=is_repl)
                    if name in repl and name in replaced_once:
                        report.warnings.append("entry %s trùng tên: chỉ thay lần đầu, giữ nguyên bản sau" % name)
                    if is_repl:
                        replaced_once.add(name)
                        data = bytes(repl[name])
                        with zout.open(zinfo, "w") as out:
                            out.write(data)
                        expected.append((name, binascii.crc32(data) & 0xFFFFFFFF, len(data)))
                        report.replaced.append(name)
                    elif name.endswith("/"):
                        zout.writestr(zinfo, b"")
                        expected.append((name, 0, 0))
                        report.copied += 1
                    else:
                        with zf.open(info, "r") as zin, zout.open(zinfo, "w") as out:
                            shutil.copyfileobj(zin, out, length=lim.chunk_size)
                        expected.append((name, info.CRC, info.file_size))
                        report.copied += 1
                for name, data in repl.items():
                    if name in names:
                        continue
                    data = bytes(data)
                    method = overrides.get(name, zipfile.ZIP_DEFLATED)
                    zinfo = zipfile.ZipInfo(name, time.localtime(time.time())[:6])
                    zinfo.compress_type = method
                    zinfo.external_attr = 0o600 << 16
                    with zout.open(zinfo, "w") as out:
                        out.write(data)
                    expected.append((name, binascii.crc32(data) & 0xFFFFFFFF, len(data)))
                    report.added.append(name)
        try:   # zipfile ghi thẳng ra file tạm nên AtomicWriter không đếm được
            report.bytes_written = os.path.getsize(str(dest_path))
        except OSError:
            report.bytes_written = 0
    except NotImplementedError as exc:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "method nén không hỗ trợ: %s" % type(exc).__name__,
                       where=src_name)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
        raise PB3Error("E_BAD_ARCHIVE", "đọc/ghi ZIP lỗi: %s" % type(exc).__name__, where=src_name)
    finally:
        zf.close()
    report.entries = len(expected)
    return report
