"""Nén IPA kiểu WinRAR: solid LZMA trên dữ liệu đã giải nén (đợt rà soát cuối).

Thiết kế (xem handoff/final_compress.md):
- Đọc từng entry của IPA (ZIP), giải nén về bytes gốc (streaming, kiểm CRC32
  từng entry), rồi nén SOLID toàn bộ thành một luồng LZMA duy nhất trong một
  file tar.xz. Vì LZMA thấy dữ liệu gốc (không phải byte đã nén DEFLATE) nên
  nó tận dụng được trùng lặp xuyên entry — đó là điểm khác với nén lại file
  IPA nguyên vẹn (byte đã nén sẵn thì gần như không nén thêm được).
- Archive đầu ra là tar.xz chuẩn (đọc được bằng `tarfile` thường) mà member
  đầu tiên là `pb3-manifest.json`: tên file gốc, kích thước gốc/nén, sha256
  từng entry + toàn bộ file gốc, metadata ZIP (method, date_time,
  external_attr, extra, comment) để `decompress` dựng lại IPA.
- `decompress` khôi phục IPA: từng entry kiểm sha256 (lỗi -> E_VERIFY_FAILED,
  giữ bản ứng viên để điều tra); cuối cùng kiểm sha256 toàn bộ file IPA
  khôi phục so với bản gốc và báo rõ có byte-identical hay không. IPA khôi
  phục bằng `zipfile` thường byte-identical với IPA do `zipfile` tạo ra
  (cùng method + cùng mức DEFLATE mặc định); IPA do công cụ khác tạo có thể
  khác byte ở mức container (mức nén DEFLATE khác nhau) dù nội dung từng
  entry vẫn khớp sha256 — trường hợp đó chỉ cảnh báo, không giấu.
- File nén ra KHÔNG phải IPA cài trực tiếp: chỉ là bản lưu trữ, phải
  `decompress` mới dùng lại được.
- Chỉ dùng stdlib Python 3.9 (zipfile/lzma/tarfile/hashlib); không gọi mạng;
  không giữ toàn bộ dữ liệu trong RAM (đọc/ghi theo khối 1 MiB), không giải
  nén toàn bộ ra đĩa tạm.

Giới hạn (luật bắt buộc, có test khóa):
- Đầu vào tối đa 2 GiB (file lớn hơn -> từ chối rõ bằng E_TOO_LARGE trước
  khi bắt đầu nén). Tổng giải nén + số entry theo `core.limits.Limits`.
- Đích <= 400 MiB; file nén ra KHÔNG BAO GIỜ quá 500 MiB: nén xong kiểm kích
  thước thật, quá 500 MiB thì xóa output và báo KHÔNG NÉN ĐƯỢC kèm kích
  thước tốt nhất đạt được + lý do (IPA đã nén sẵn nên tỉ lệ 5:1 thường
  không đạt được). Từ 400 MiB đến 500 MiB thì thành công nhưng báo rõ
  "trượt đích 400MB". Nguồn không bao giờ bị sửa.
- Preset LZMA mặc định 6 (dict 8 MiB, RAM mã hóa ~110 MiB — an toàn trên
  iPhone 11 Pro Max 4 GiB); preset 8/9/9e cho phép qua `--preset` kèm cảnh
  báo RAM (xem `preset_ram_estimate_mb` + số đo trong handoff/final_compress.md).
"""
from __future__ import annotations

import binascii
import calendar
import hashlib
import io
import json
import lzma
import os
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core import safe_io
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from core.progress import CancelToken
from sdk.containers import fix_zip_entry_name

FORMAT = "pb3-solid-xz"
FORMAT_VERSION = 1
MANIFEST_NAME = "pb3-manifest.json"
DEFAULT_PRESET = 6
# Giới hạn kích thước (luật bắt buộc; test khóa bằng cách truyền tham số nhỏ hơn).
MAX_INPUT_BYTES = 2 * 1024 ** 3        # 2 GiB
TARGET_BYTES = 400 * 1024 ** 2         # đích 400 MiB
MAX_OUTPUT_BYTES = 500 * 1024 ** 2     # cứng: không bao giờ vượt 500 MiB
CHUNK_SIZE = 1024 * 1024               # khối đọc/ghi streaming
MAX_MANIFEST_BYTES = 64 * 1024 * 1024  # manifest JSON tối đa 64 MiB

# Dict LZMA theo preset (chuẩn xz): preset 6 -> 8 MiB, 7 -> 16 MiB,
# 8 -> 32 MiB, 9/9e -> 64 MiB. RAM mã hóa ~ dict x 11 + phụ phí.
_PRESET_DICT_BYTES = {
    0: 256 * 1024, 1: 1024 * 1024, 2: 2 * 1024 * 1024,
    3: 4 * 1024 * 1024, 4: 4 * 1024 * 1024, 5: 8 * 1024 * 1024,
    6: 8 * 1024 * 1024, 7: 16 * 1024 * 1024, 8: 32 * 1024 * 1024,
    9: 64 * 1024 * 1024,
}
_METHOD_NAMES = {
    zipfile.ZIP_STORED: "stored",
    zipfile.ZIP_DEFLATED: "deflated",
    zipfile.ZIP_BZIP2: "bzip2",
    zipfile.ZIP_LZMA: "lzma",
}
_METHOD_CODES = {v: k for k, v in _METHOD_NAMES.items()}


def parse_preset(value) -> object:
    """Chuẩn hóa --preset: int 0..9 hoặc "9e" (extreme). Sai -> E_CONFIG_INVALID."""
    if isinstance(value, bool):
        raise PB3Error("E_CONFIG_INVALID", "preset phải là 0..9 hoặc '9e', nhận %r" % (value,))
    if isinstance(value, int):
        if 0 <= value <= 9:
            return value
        raise PB3Error("E_CONFIG_INVALID", "preset phải là 0..9 hoặc '9e', nhận %d" % value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s == "9e":
            return "9e"
        try:
            n = int(s, 10)
        except ValueError:
            raise PB3Error("E_CONFIG_INVALID", "preset phải là 0..9 hoặc '9e', nhận %r" % (value,))
        if 0 <= n <= 9:
            return n
        raise PB3Error("E_CONFIG_INVALID", "preset phải là 0..9 hoặc '9e', nhận %r" % (value,))
    raise PB3Error("E_CONFIG_INVALID", "preset phải là 0..9 hoặc '9e', nhận %r" % (value,))


def preset_dict_size(preset) -> int:
    """Dict LZMA (byte) của preset đã chuẩn hóa."""
    if preset == "9e":
        return _PRESET_DICT_BYTES[9]
    return _PRESET_DICT_BYTES[int(preset)]


def preset_ram_estimate_mb(preset) -> int:
    """RAM mã hóa ước tính (MiB): dict x 11 + 20 MiB phụ phí (encoder + đệm).

    Chỉ là ước tính để cảnh báo, không phải cam kết: số đo thật trên fixture
    trong handoff/final_compress.md. Preset 9e (extreme) tốn thêm nhiều RAM
    do tìm kiếm sâu hơn nên cộng gấp đôi phụ phí."""
    base = preset_dict_size(preset) * 11 // (1024 * 1024) + 20
    if preset == "9e":
        base += 300
    return base


def preset_ram_warning(preset) -> Optional[str]:
    """Cảnh báo RAM khi preset cao; None khi preset an toàn trên iPhone 4 GiB."""
    if preset == "9e":
        return ("preset 9e cần khoảng %d MiB RAM khi mã hóa (dict 64 MiB, tìm kiếm "
                "sâu) — có thể bị hệ thống dừng trên iPhone 4 GiB; chỉ dùng khi "
                "máy còn nhiều RAM trống." % preset_ram_estimate_mb(preset))
    if isinstance(preset, int) and preset >= 8:
        return ("preset %d cần khoảng %d MiB RAM khi mã hóa (dict %d MiB) — cao "
                "hơn nhiều so với mặc định (preset 6: ~%d MiB); chỉ dùng khi máy "
                "còn nhiều RAM trống." % (preset, preset_ram_estimate_mb(preset),
                                         preset_dict_size(preset) // (1024 * 1024),
                                         preset_ram_estimate_mb(DEFAULT_PRESET)))
    return None


def _lzma_preset(preset) -> int:
    """Preset dùng cho lzma.LZMAFile (9e = 9 | PRESET_EXTREME)."""
    if preset == "9e":
        return 9 | lzma.PRESET_EXTREME
    return int(preset)


def _fmt_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return "%.1fGB" % n


def _unsafe_name_reason(name: str) -> Optional[str]:
    """Lý do chặn tên entry (giống quy tắc của sdk/containers.ZipVFS)."""
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


def _zip_datetime_to_epoch(dt: tuple) -> int:
    """date_time của ZIP (giờ địa phương) -> epoch UTC (chỉ để ghi mtime tar)."""
    try:
        return calendar.timegm(dt + (0, 0, 0))
    except (ValueError, OverflowError, TypeError):
        return 0


@dataclass
class EntryRecord:
    """Một entry IPA + metadata cần để dựng lại ZIP byte-identical."""
    name: str
    size: int
    sha256: str = ""
    crc32: int = 0
    compress_size: int = 0
    method: str = "stored"
    date_time: Optional[tuple] = None
    external_attr: int = 0
    create_system: int = 3
    create_version: int = 20
    extract_version: int = 20
    extra: str = ""      # hex
    comment: str = ""
    is_dir: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "size": self.size, "sha256": self.sha256,
                "crc32": self.crc32, "compress_size": self.compress_size,
                "method": self.method,
                "date_time": list(self.date_time) if self.date_time else None,
                "external_attr": self.external_attr, "create_system": self.create_system,
                "create_version": self.create_version,
                "extract_version": self.extract_version,
                "extra": self.extra, "comment": self.comment, "is_dir": self.is_dir}

    @classmethod
    def from_dict(cls, d: dict, where: str) -> "EntryRecord":
        try:
            name = d["name"]
            size = d["size"]
            sha256 = d["sha256"]
            if not isinstance(name, str) or not isinstance(size, int) or size < 0:
                raise ValueError("trường name/size sai")
            if not isinstance(sha256, str) or len(sha256) != 64:
                raise ValueError("sha256 sai")
            dt = d.get("date_time")
            return cls(
                name=name, size=size, sha256=sha256, crc32=int(d.get("crc32", 0)),
                compress_size=int(d.get("compress_size", 0)),
                method=str(d.get("method", "stored")),
                date_time=tuple(dt) if isinstance(dt, list) else None,
                external_attr=int(d.get("external_attr", 0)),
                create_system=int(d.get("create_system", 3)),
                create_version=int(d.get("create_version", 20)),
                extract_version=int(d.get("extract_version", 20)),
                extra=str(d.get("extra", "")),
                comment=str(d.get("comment", "")),
                is_dir=bool(d.get("is_dir", False)))
        except (KeyError, TypeError, ValueError) as exc:
            raise PB3Error("E_BAD_ARCHIVE", "manifest hỏng ở entry: %s" % exc,
                           where=where)


@dataclass
class CompressReport:
    source: str
    dest: str
    preset: object
    original_size: int
    original_sha256: str
    compressed_size: int
    entries: int
    target_met: bool
    elapsed_s: float = 0.0
    ram_warning: Optional[str] = None

    @property
    def ratio(self) -> float:
        return (self.original_size / self.compressed_size) if self.compressed_size else 0.0

    @property
    def saved_pct(self) -> float:
        if not self.original_size:
            return 0.0
        return 100.0 * (1.0 - self.compressed_size / self.original_size)

    def to_dict(self) -> Dict[str, object]:
        return {"source": self.source, "dest": self.dest, "preset": self.preset,
                "original_size": self.original_size,
                "original_sha256": self.original_sha256,
                "compressed_size": self.compressed_size,
                "ratio": round(self.ratio, 2), "saved_pct": round(self.saved_pct, 1),
                "entries": self.entries, "target_met": self.target_met,
                "elapsed_s": round(self.elapsed_s, 1),
                "ram_warning": self.ram_warning}


@dataclass
class DecompressReport:
    source: str
    dest: str
    original_name: str
    entries: int
    restored_size: int
    restored_sha256: str
    byte_identical: bool
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {"source": self.source, "dest": self.dest,
                "original_name": self.original_name, "entries": self.entries,
                "restored_size": self.restored_size,
                "restored_sha256": self.restored_sha256,
                "byte_identical": self.byte_identical,
                "elapsed_s": round(self.elapsed_s, 1)}


class _CountingReader:
    """Bọc stream đọc: đếm byte đã đọc + gọi progress + kiểm token hủy."""

    def __init__(self, raw, on_chunk=None, cancel=None, where: str = ""):
        self._raw = raw
        self._on_chunk = on_chunk
        self._cancel = cancel
        self._where = where
        self.read_bytes = 0

    def read(self, n: int = -1) -> bytes:
        if self._cancel is not None:
            self._cancel.check(self._where)
        chunk = self._raw.read(n)
        if chunk:
            self.read_bytes += len(chunk)
            if self._on_chunk is not None:
                self._on_chunk(len(chunk))
        return chunk

    def close(self) -> None:
        try:
            self._raw.close()
        except (OSError, ValueError):
            pass


def _check_input_size(path: Path, max_input_bytes: int, where: str) -> int:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise error_from_exception(exc, where=where)
    if size > max_input_bytes:
        raise PB3Error(
            "E_TOO_LARGE",
            "file %s (%s) vượt giới hạn đầu vào %s — từ chối nén, không bắt đầu"
            % (_fmt_size(size), where, _fmt_size(max_input_bytes)),
            where=where,
            hint="Chia nhỏ IPA hoặc chỉ nén một bundle con; PB3 không nén file trên 2 GiB.")
    return size


def _inventory_zip(zf: zipfile.ZipFile, limits: Limits, where: str) -> List[EntryRecord]:
    """Liệt kê + kiểm tra entry IPA (tên an toàn, không mã hóa/symlink/trùng)."""
    infos = zf.infolist()
    limits.check_entries(len(infos), where)
    limits.check_total(sum(i.file_size for i in infos), where)
    out: List[EntryRecord] = []
    seen = set()
    for info in infos:
        # Tên entry trong manifest nén/khôi phục: sửa mojibake UTF-8-không-cờ
        # (ZIP do "Nén" trong Files iOS tạo) để lúc giải nén khôi phục đúng
        # tên gốc, thay vì tên "ΓÇÄ ...".
        name = fix_zip_entry_name(info)
        is_dir = name.endswith("/")
        if name in seen:
            raise PB3Error("E_BAD_ARCHIVE", "entry trùng tên %r (không nén được: "
                           "khôi phục sẽ mơ hồ)" % name, where=where)
        seen.add(name)
        if not is_dir:
            reason = _unsafe_name_reason(name)
            if reason is None and ((info.external_attr >> 16) & 0o170000) == 0o120000:
                reason = "symlink bị chặn"
            if reason is None and (info.flag_bits & 0x1):
                raise PB3Error("E_ENCRYPTED", "entry mã hóa", where=name)
            if reason is not None:
                raise PB3Error("E_UNSAFE_PATH", reason, where=name)
        out.append(EntryRecord(
            name=name, size=info.file_size, crc32=info.CRC,
            compress_size=info.compress_size,
            method=_METHOD_NAMES.get(info.compress_type, "method-%d" % info.compress_type),
            date_time=info.date_time, external_attr=info.external_attr,
            create_system=info.create_system, create_version=info.create_version,
            extract_version=info.extract_version, extra=info.extra.hex(),
            comment=info.comment.decode("utf-8", errors="replace"), is_dir=is_dir))
    return out


def _open_zip(path: Path, where: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(str(path), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise error_from_exception(exc, where=where)


def _publish_exclusive(tmp: Path, dest: Path, where: str) -> None:
    """Đặt file tạm thành đích mà không bao giờ ghi đè (đã có -> E_OUTPUT_EXISTS)."""
    try:
        os.link(str(tmp), str(dest))
    except FileExistsError:
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại", where=where)
    except OSError:
        if os.path.lexists(str(dest)):
            raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại", where=where)
        os.replace(str(tmp), str(dest))
        return
    try:
        os.unlink(str(tmp))
    except OSError:
        pass


def _discard(path: Path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass


def _reject_same_source_dest(src_p: Path, dest_p: Path) -> None:
    """Chốt dest != source: từ chối khi đích trùng nguồn (cùng đường dẫn
    sau realpath, hoặc cùng file qua symlink/hardlink) — kể cả --force.
    Trước đây compress_ipa/decompress_archive chỉ kiểm tra đích-đã-tồn-
    tại, nên --force + --out trùng nguồn sẽ ghi đè file gốc."""
    if os.path.realpath(str(dest_p)) == os.path.realpath(str(src_p)):
        raise PB3Error("E_CONFIG_INVALID",
                       "không ghi đè file gốc: đích trùng nguồn",
                       where=str(dest_p))
    if dest_p.exists():
        try:
            if os.path.samefile(str(src_p), str(dest_p)):
                raise PB3Error("E_CONFIG_INVALID",
                               "không ghi đè file gốc: đích trùng nguồn",
                               where=str(dest_p))
        except OSError:
            pass


def compress_ipa(src, dest, *, preset=DEFAULT_PRESET, limits: Optional[Limits] = None,
                 max_input_bytes: int = MAX_INPUT_BYTES,
                 target_bytes: int = TARGET_BYTES,
                 max_output_bytes: int = MAX_OUTPUT_BYTES,
                 progress=None, cancel: Optional[CancelToken] = None,
                 force: bool = False) -> CompressReport:
    """Nén IPA (ZIP) thành archive solid LZMA (tar.xz + manifest).

    progress(done, total): gọi theo byte thật đã xử lý (lượt băm + lượt nén
    = 2 lần tổng giải nén). dest đã tồn tại và không --force -> E_OUTPUT_EXISTS.
    """
    t0 = time.monotonic()
    preset = parse_preset(preset)
    lim = limits or Limits()
    src_p = Path(os.path.expanduser(str(src)))
    dest_p = Path(os.path.expanduser(str(dest)))
    where = str(src_p)
    if not src_p.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "không phải tệp", where=where)
    _reject_same_source_dest(src_p, dest_p)
    src_size = _check_input_size(src_p, max_input_bytes, where)
    if dest_p.exists() and not force:
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại (dùng --force để ghi đè)",
                       where=str(dest_p))
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    safe_io.ensure_free_space(dest_p.parent, src_size, factor=1.0, where=str(dest_p))
    # sha256 toàn bộ file gốc (để decompress kiểm byte-identical).
    orig_hash = hashlib.sha256()
    try:
        with open(str(src_p), "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                orig_hash.update(chunk)
    except OSError as exc:
        raise error_from_exception(exc, where=where)
    original_sha256 = orig_hash.hexdigest()

    zf = _open_zip(src_p, where)
    try:
        entries = _inventory_zip(zf, lim, where)
    except PB3Error:
        zf.close()
        raise
    total = sum(e.size for e in entries)
    work_total = max(1, 2 * total)
    done = [0]

    def _tick(n: int) -> None:
        done[0] += n
        if progress is not None:
            progress(done[0], work_total)

    # Lượt 1: băm từng entry (streaming) + kiểm CRC32 với central directory.
    for rec in entries:
        if rec.is_dir:
            rec.sha256 = hashlib.sha256(b"").hexdigest()
            continue
        info = zf.getinfo(rec.name)
        h = hashlib.sha256()
        crc = 0
        try:
            with zf.open(info, "r") as stream:
                reader = _CountingReader(stream, on_chunk=_tick, cancel=cancel,
                                         where=rec.name)
                while True:
                    chunk = reader.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    h.update(chunk)
                    crc = binascii.crc32(chunk, crc)
        except zipfile.BadZipFile:
            zf.close()
            raise PB3Error("E_BAD_ARCHIVE", "CRC sai hoặc cấu trúc lỗi khi đọc",
                           where=rec.name)
        if (crc & 0xFFFFFFFF) != rec.crc32:
            zf.close()
            raise PB3Error("E_BAD_ARCHIVE",
                           "CRC32 entry khác central directory (%08x != %08x)"
                           % (crc & 0xFFFFFFFF, rec.crc32), where=rec.name)
        rec.sha256 = h.hexdigest()

    manifest = {
        "format": FORMAT, "version": FORMAT_VERSION,
        "original_name": src_p.name, "original_size": src_size,
        "original_sha256": original_sha256,
        "created_by": "PB3 v3.1 (BearTea) solid-xz",
        "preset": preset if isinstance(preset, str) else int(preset),
        "entries": [e.to_dict() for e in entries],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")

    # Lượt 2: ghi tar.xz streaming vào file tạm (cùng thư mục đích).
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".pb3part-compress-", dir=str(dest_p.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw:
                lz = lzma.LZMAFile(raw, "wb", preset=_lzma_preset(preset))
                try:
                    tf = tarfile.open(fileobj=lz, mode="w|")
                    try:
                        mi = tarfile.TarInfo(MANIFEST_NAME)
                        mi.size = len(manifest_bytes)
                        mi.mtime = 0
                        mi.mode = 0o644
                        mi.uid = mi.gid = 0
                        mi.uname = mi.gname = ""
                        tf.addfile(mi, io.BytesIO(manifest_bytes))
                        for rec in entries:
                            ti = tarfile.TarInfo(rec.name)
                            ti.size = rec.size
                            ti.mtime = _zip_datetime_to_epoch(rec.date_time) if rec.date_time else 0
                            ti.mode = (rec.external_attr >> 16) & 0o7777 or 0o644
                            ti.uid = ti.gid = 0
                            ti.uname = ti.gname = ""
                            if rec.is_dir:
                                ti.type = tarfile.DIRTYPE
                                tf.addfile(ti)
                                continue
                            info = zf.getinfo(rec.name)
                            stream = zf.open(info, "r")
                            reader = _CountingReader(stream, on_chunk=_tick,
                                                     cancel=cancel, where=rec.name)
                            try:
                                tf.addfile(ti, reader)
                            finally:
                                reader.close()
                    finally:
                        tf.close()
                finally:
                    lz.close()
                raw.flush()
                os.fsync(raw.fileno())
        except (tarfile.TarError, lzma.LZMAError, OSError, EOFError) as exc:
            raise error_from_exception(exc, where=str(dest_p), op="write")
    finally:
        zf.close()
    if tmp is None:
        raise PB3Error("E_INTERNAL", "không tạo được file tạm", where=str(dest_p))
    compressed_size = tmp.stat().st_size
    if compressed_size > max_output_bytes:
        best = compressed_size
        _discard(tmp)
        raise PB3Error(
            "E_TOO_LARGE",
            "KHÔNG NÉN ĐƯỢC: file nén tốt nhất đạt %s, vượt giới hạn %s "
            "(đích %s). IPA đã nén sẵn (texture/âm thanh nén, entry DEFLATE) "
            "nên tỉ lệ 5:1 thường không đạt được — giữ lại file gốc, không tạo "
            "file nén quá lớn." % (_fmt_size(best), _fmt_size(max_output_bytes),
                                  _fmt_size(target_bytes)),
            where=str(dest_p),
            hint="Thử preset cao hơn (--preset 8) hoặc nén từng bundle con thay vì cả IPA.")
    if dest_p.exists() and not force:
        _discard(tmp)
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại (dùng --force để ghi đè)",
                       where=str(dest_p))
    try:
        if force and dest_p.exists():
            os.replace(str(tmp), str(dest_p))
        else:
            _publish_exclusive(tmp, dest_p, str(dest_p))
    except PB3Error:
        _discard(tmp)
        raise
    if progress is not None:
        progress(work_total, work_total)
    return CompressReport(
        source=str(src_p), dest=str(dest_p), preset=preset,
        original_size=src_size, original_sha256=original_sha256,
        compressed_size=compressed_size, entries=len(entries),
        target_met=compressed_size <= target_bytes,
        elapsed_s=time.monotonic() - t0,
        ram_warning=preset_ram_warning(preset))


def _open_tar_stream(src_p: Path, where: str):
    """Mở tar.xz streaming; lỗi -> E_BAD_ARCHIVE. Trả (raw_fh, tarfile)."""
    try:
        raw = open(str(src_p), "rb")
    except OSError as exc:
        raise error_from_exception(exc, where=where)
    try:
        tf = tarfile.open(fileobj=raw, mode="r|xz")
    except (tarfile.TarError, lzma.LZMAError, EOFError, OSError) as exc:
        raw.close()
        raise PB3Error("E_BAD_ARCHIVE", "không mở được archive xz: %s"
                       % type(exc).__name__, where=where)
    return raw, tf


def _read_manifest_member(tf, where: str) -> dict:
    """Đọc member đầu tiên (phải là pb3-manifest.json) + kiểm tra hình thức."""
    # LƯU Ý: ở chế độ stream ("r|") KHÔNG dùng next(iter(tf)) — __iter__ của
    # tarfile phát lại từ self.members nên mỗi lần gọi lại trả member đầu.
    # Dùng tf.next() trực tiếp (trả None khi hết archive sạch).
    try:
        first = tf.next()
    except (tarfile.TarError, lzma.LZMAError, EOFError) as exc:
        raise PB3Error("E_BAD_ARCHIVE", "archive rỗng hoặc hỏng: %s"
                       % type(exc).__name__, where=where)
    if first is None:
        raise PB3Error("E_BAD_ARCHIVE", "archive rỗng (không có member nào)",
                       where=where)
    if first.name != MANIFEST_NAME:
        raise PB3Error(
            "E_BAD_ARCHIVE",
            "không phải archive nén PB3 (member đầu là %r, cần %r) — có thể đây "
            "là tar.xz thường, không khôi phục được IPA từ nó"
            % (first.name, MANIFEST_NAME), where=where)
    if first.size > MAX_MANIFEST_BYTES:
        raise PB3Error("E_BAD_ARCHIVE", "manifest lớn bất thường (%d byte)" % first.size,
                       where=where)
    try:
        stream = tf.extractfile(first)
        if stream is None:
            raise PB3Error("E_BAD_ARCHIVE", "không đọc được manifest", where=where)
        data = stream.read()
    except (tarfile.TarError, lzma.LZMAError, EOFError, OSError) as exc:
        raise PB3Error("E_BAD_ARCHIVE", "đọc manifest lỗi: %s" % type(exc).__name__,
                       where=where)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_BAD_ARCHIVE", "manifest không phải JSON hợp lệ: %s"
                       % type(exc).__name__, where=where)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise PB3Error("E_BAD_ARCHIVE", "manifest không đúng định dạng %s" % FORMAT,
                       where=where)
    if manifest.get("version") != FORMAT_VERSION:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "manifest phiên bản %r (PB3 chỉ đọc phiên bản %d)"
                       % (manifest.get("version"), FORMAT_VERSION), where=where)
    if not isinstance(manifest.get("entries"), list):
        raise PB3Error("E_BAD_ARCHIVE", "manifest thiếu danh sách entries", where=where)
    if not isinstance(manifest.get("original_name"), str) or not manifest.get("original_name"):
        raise PB3Error("E_BAD_ARCHIVE", "manifest thiếu tên file gốc", where=where)
    if not isinstance(manifest.get("original_sha256"), str) \
            or len(manifest.get("original_sha256", "")) != 64:
        raise PB3Error("E_BAD_ARCHIVE", "manifest thiếu sha256 file gốc", where=where)
    manifest["entries"] = [EntryRecord.from_dict(e, where) for e in manifest["entries"]]
    return manifest


def load_manifest(src) -> dict:
    """Đọc manifest của một archive nén PB3 (không giải nén hết). Trả dict
    với khóa "entries" là list[EntryRecord]."""
    src_p = Path(os.path.expanduser(str(src)))
    where = str(src_p)
    if not src_p.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "không phải tệp", where=where)
    raw, tf = _open_tar_stream(src_p, where)
    try:
        return _read_manifest_member(tf, where)
    finally:
        try:
            tf.close()
        except (tarfile.TarError, OSError):
            pass
        raw.close()


def decompress_archive(src, dest, *, limits: Optional[Limits] = None,
                       progress=None, cancel: Optional[CancelToken] = None,
                       force: bool = False) -> DecompressReport:
    """Khôi phục IPA từ archive solid LZMA (tar.xz + manifest).

    Từng entry kiểm sha256 (sai -> E_VERIFY_FAILED, giữ bản ứng viên để điều
    tra); cuối cùng kiểm sha256 toàn bộ file IPA khôi phục và báo rõ
    `byte_identical` (khớp = byte-identical với IPA gốc).
    """
    t0 = time.monotonic()
    lim = limits or Limits()
    src_p = Path(os.path.expanduser(str(src)))
    dest_p = Path(os.path.expanduser(str(dest)))
    where = str(src_p)
    if not src_p.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "không phải tệp", where=where)
    _reject_same_source_dest(src_p, dest_p)
    if dest_p.exists() and not force:
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại (dùng --force để ghi đè)",
                       where=str(dest_p))
    dest_p.parent.mkdir(parents=True, exist_ok=True)

    raw, tf = _open_tar_stream(src_p, where)
    tmp = None
    try:
        manifest = _read_manifest_member(tf, where)
        entries = manifest["entries"]
        lim.check_entries(len(entries), where)
        lim.check_total(sum(e.size for e in entries), where)
        total = sum(e.size for e in entries)
        work_total = max(1, total)
        done = [0]

        def _tick(n: int) -> None:
            done[0] += n
            if progress is not None:
                progress(done[0], work_total)

        fd, tmp_name = tempfile.mkstemp(prefix=".pb3part-decompress-",
                                        dir=str(dest_p.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw_out:
                zf = zipfile.ZipFile(raw_out, "w", zipfile.ZIP_DEFLATED,
                                     allowZip64=True, compresslevel=6)
                try:
                    for idx, rec in enumerate(entries):
                        reason = _unsafe_name_reason(rec.name.rstrip("/")) \
                            if not rec.is_dir else None
                        if reason is not None:
                            raise PB3Error("E_UNSAFE_PATH", reason, where=rec.name)
                        if rec.method not in _METHOD_CODES:
                            raise PB3Error("E_UNSUPPORTED_FORMAT",
                                           "method ZIP %r chưa hỗ trợ khi khôi phục"
                                           % rec.method, where=rec.name)
                        try:
                            member = tf.next()
                        except (tarfile.TarError, lzma.LZMAError, EOFError) as exc:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "archive lỗi khi đọc entry %r: %s"
                                           % (rec.name, type(exc).__name__), where=where)
                        if member is None:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "archive thiếu entry %r (hết dữ liệu)"
                                           % rec.name, where=where)
                        # tarfile lược dấu '/' cuối tên thư mục khi đọc lại
                        # ("Data/" -> "Data") nên so sánh đã chuẩn hóa với dir.
                        same = member.name == rec.name or (
                            rec.is_dir and member.name.rstrip("/") == rec.name.rstrip("/"))
                        if not same:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "thứ tự entry lệch: gặp %r, cần %r"
                                           % (member.name, rec.name), where=where)
                        if member.size != rec.size:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "kích thước entry %r lệch manifest "
                                           "(%d != %d)" % (rec.name, member.size, rec.size),
                                           where=where)
                        if member.issym() or member.islnk():
                            raise PB3Error("E_UNSAFE_PATH",
                                           "symlink trong archive bị chặn", where=rec.name)
                        ni = zipfile.ZipInfo(rec.name, tuple(rec.date_time)
                                             if rec.date_time else (1980, 1, 1, 0, 0, 0))
                        ni.compress_type = _METHOD_CODES[rec.method]
                        ni.external_attr = rec.external_attr
                        ni.create_system = rec.create_system
                        ni.create_version = rec.create_version
                        ni.extract_version = rec.extract_version
                        try:
                            ni.extra = bytes.fromhex(rec.extra)
                        except ValueError:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "extra của entry %r không phải hex"
                                           % rec.name, where=where)
                        ni.comment = rec.comment.encode("utf-8", errors="replace")
                        if rec.is_dir:
                            if not ni.filename.endswith("/"):
                                ni.filename += "/"
                            zf.writestr(ni, b"")
                            continue
                        stream = tf.extractfile(member)
                        if stream is None:
                            raise PB3Error("E_BAD_ARCHIVE",
                                           "không đọc được entry %r" % rec.name, where=where)
                        reader = _CountingReader(stream, on_chunk=_tick,
                                                 cancel=cancel, where=rec.name)
                        h = hashlib.sha256()
                        crc = 0
                        try:
                            with zf.open(ni, "w") as writer:
                                while True:
                                    chunk = reader.read(CHUNK_SIZE)
                                    if not chunk:
                                        break
                                    h.update(chunk)
                                    crc = binascii.crc32(chunk, crc)
                                    writer.write(chunk)
                        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                            raise error_from_exception(exc, where=rec.name)
                        finally:
                            reader.close()
                        if h.hexdigest() != rec.sha256:
                            raise PB3Error(
                                "E_VERIFY_FAILED",
                                "sha256 entry %r khác manifest (dữ liệu trong "
                                "archive đã hỏng hoặc bị sửa)" % rec.name, where=rec.name)
                        if (crc & 0xFFFFFFFF) != rec.crc32:
                            raise PB3Error(
                                "E_VERIFY_FAILED",
                                "CRC32 entry %r khác manifest" % rec.name, where=rec.name)
                    # Không được thừa member nào sau entries của manifest.
                    try:
                        extra_member = tf.next()
                    except (tarfile.TarError, lzma.LZMAError, EOFError):
                        extra_member = None
                    if extra_member is not None:
                        raise PB3Error("E_BAD_ARCHIVE",
                                       "archive thừa entry %r ngoài manifest"
                                       % extra_member.name, where=where)
                finally:
                    zf.close()
                raw_out.flush()
                os.fsync(raw_out.fileno())
        except PB3Error as exc:
            if exc.code == "E_VERIFY_FAILED" and tmp is not None:
                kept = dest_p.with_name(dest_p.name + ".failed")
                try:
                    if kept.exists():
                        kept.unlink()
                    os.replace(str(tmp), str(kept))
                    tmp = None
                except OSError:
                    pass
                raise PB3Error(exc.code, exc.detail + "; bản ứng viên giữ tại %s"
                               % kept, where=exc.where, hint=exc.hint)
            raise
        except (tarfile.TarError, lzma.LZMAError, EOFError) as exc:
            # Lỗi khi ĐỌC archive (cắt cụt, luồng xz hỏng, header tar lỗi):
            # archive hỏng, không phải lỗi nội bộ.
            raise PB3Error("E_BAD_ARCHIVE", "archive hỏng khi đọc: %s"
                           % type(exc).__name__, where=where)
        except OSError as exc:
            raise error_from_exception(exc, where=where, op="write")
    finally:
        try:
            tf.close()
        except (tarfile.TarError, OSError):
            pass
        raw.close()
    if tmp is None:
        raise PB3Error("E_INTERNAL", "không tạo được file tạm", where=str(dest_p))
    # Kiểm sha256 toàn bộ file IPA khôi phục so với bản gốc.
    restored_hash = hashlib.sha256()
    try:
        with open(str(tmp), "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                restored_hash.update(chunk)
    except OSError as exc:
        _discard(tmp)
        raise error_from_exception(exc, where=str(dest_p))
    restored_sha256 = restored_hash.hexdigest()
    byte_identical = restored_sha256 == manifest["original_sha256"]
    restored_size = tmp.stat().st_size
    if dest_p.exists() and not force:
        _discard(tmp)
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại (dùng --force để ghi đè)",
                       where=str(dest_p))
    try:
        if force and dest_p.exists():
            os.replace(str(tmp), str(dest_p))
        else:
            _publish_exclusive(tmp, dest_p, str(dest_p))
    except PB3Error:
        _discard(tmp)
        raise
    if progress is not None:
        progress(work_total, work_total)
    return DecompressReport(
        source=str(src_p), dest=str(dest_p),
        original_name=manifest["original_name"], entries=len(entries),
        restored_size=restored_size, restored_sha256=restored_sha256,
        byte_identical=byte_identical, elapsed_s=time.monotonic() - t0)


__all__ = ["FORMAT", "FORMAT_VERSION", "MANIFEST_NAME", "DEFAULT_PRESET",
           "MAX_INPUT_BYTES", "TARGET_BYTES", "MAX_OUTPUT_BYTES",
           "EntryRecord", "CompressReport", "DecompressReport",
           "parse_preset", "preset_dict_size", "preset_ram_estimate_mb",
           "preset_ram_warning", "compress_ipa", "decompress_archive",
           "load_manifest"]
