"""I/O an toàn (đặc tả 5.2, 6.7, 13).

- Đọc: kiểm tra tồn tại/quyền/kích thước TRƯỚC khi đọc; file bị cắt cụt giữa chừng
  -> E_CORRUPT; quá lớn -> E_TOO_LARGE (không bao giờ đọc "thử").
- Ghi: temp cùng thư mục -> (verify) -> fsync -> os.replace (nguyên tử). Lỗi hoặc
  hủy giữa chừng: file đích cũ còn nguyên, temp bị dọn.
- Output nằm trong thư mục output, chặn '..', tuyệt đối, symlink; KHÔNG ghi đè:
  đã tồn tại thì tạo name.v1.ext, name.v2.ext... (hoặc E_OUTPUT_EXISTS).
- Temp dở dang (iOS dừng tiến trình) mang dấu '.pb3part-' để lần sau phát hiện;
  PB3 chỉ CÁCH LY (di chuyển), không tự xóa.
"""
from __future__ import annotations

import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from . import hashing
from .errors import PB3Error, error_from_exception
from .paths import is_within, resolve_inside

STALE_MARKER = ".pb3part-"
FAILED_SUFFIX = ".failed"


# ------------------------------------------------------------------ dung lượng
def _nearest_existing(p: Path) -> Path:
    q = p
    while not os.path.lexists(str(q)):
        if q.parent == q:
            break
        q = q.parent
    return q


def free_bytes(path) -> int:
    try:
        return int(shutil.disk_usage(str(_nearest_existing(Path(path)))).free)
    except OSError as exc:
        raise error_from_exception(exc, where=str(path), op="write")


def ensure_free_space(path, needed_bytes: int, *, factor: float = 1.0,
                      where: Optional[str] = None) -> None:
    """E_NO_SPACE nếu trống < needed*factor (mặc định cần 2.5x do cấu hình, đặc tả 2.3)."""
    need = int(max(0, needed_bytes) * factor)
    free = free_bytes(path)
    if free < need:
        raise PB3Error("E_NO_SPACE", "cần %d byte, trống %d byte" % (need, free),
                       where=where or str(path))


# ---------------------------------------------------------------------- đọc
def check_readable_input(path, limits=None, *, allow_dir: bool = True) -> Path:
    """Kiểm tra đầu vào: tồn tại, đọc được, không quá lớn. Trả về Path đã resolve."""
    s = str(path)
    if "\x00" in s:
        raise PB3Error("E_UNSAFE_PATH", "đường dẫn chứa byte NUL")
    p = Path(os.path.expanduser(s))
    try:
        if not p.exists():
            raise PB3Error("E_INPUT_NOT_FOUND", "không tồn tại (hoặc symlink hỏng)", where=s)
        if p.is_dir():
            if not allow_dir:
                raise PB3Error("E_INPUT_NOT_FOUND", "cần là tệp, nhưng đây là thư mục", where=s)
        elif not p.is_file():
            raise PB3Error("E_INPUT_NOT_FOUND", "không phải tệp thường", where=s)
        if not os.access(str(p), os.R_OK):
            raise PB3Error("E_INPUT_NOT_FOUND", "không có quyền đọc", where=s)
        if p.is_file() and limits is not None:
            limits.check_input_size(p.stat().st_size, s)
        return p.resolve()
    except PB3Error:
        raise
    except OSError as exc:
        raise error_from_exception(exc, where=s)


def read_bytes_limited(path, max_bytes: int, *, offset: int = 0, length: Optional[int] = None,
                       where: Optional[str] = None) -> bytes:
    w = where or str(path)
    try:
        st = os.stat(str(path))
        if not os.path.isfile(str(path)):
            raise PB3Error("E_INPUT_NOT_FOUND", "không phải tệp thường", where=w)
        avail = max(0, st.st_size - offset)
        want = avail if length is None else min(length, avail)
        if want > max_bytes:
            raise PB3Error("E_TOO_LARGE", "cần đọc %d byte > giới hạn %d (dùng streaming)"
                           % (want, max_bytes), where=w)
        with open(str(path), "rb") as f:
            if offset:
                f.seek(offset)
            data = f.read(want)
        if len(data) < want:
            raise PB3Error("E_CORRUPT", "đọc được %d/%d byte (tệp bị cắt cụt hoặc đổi trong lúc đọc)"
                           % (len(data), want), where=w)
        return data
    except PB3Error:
        raise
    except OSError as exc:
        raise error_from_exception(exc, where=w)


def read_text_limited(path, max_bytes: int, *, encoding: str = "utf-8", errors: str = "strict",
                      where: Optional[str] = None) -> str:
    data = read_bytes_limited(path, max_bytes, where=where)
    try:
        return data.decode(encoding, errors)
    except UnicodeDecodeError as exc:
        raise error_from_exception(exc, where=where or str(path))


def iter_chunks(path, chunk_size: int = 1024 * 1024, *, cancel=None) -> Iterator[bytes]:
    try:
        with open(str(path), "rb") as f:
            while True:
                if cancel is not None:
                    cancel.check(str(path))
                b = f.read(chunk_size)
                if not b:
                    return
                yield b
    except OSError as exc:
        raise error_from_exception(exc, where=str(path))


# ---------------------------------------------------------------------- ghi
def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass   # một số filesystem (kể cả trên iSH) không hỗ trợ; best-effort


def _tmp_name(dest: Path) -> str:
    base = dest.name
    if len(base.encode("utf-8")) > 100:
        base = hashing.sha256_bytes(base.encode("utf-8"))[:24]
    return ".%s%s%d-%s" % (base, STALE_MARKER, os.getpid(), secrets.token_hex(4))


def remove_quiet(path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass


class AtomicWriter:
    """with AtomicWriter(dest, expected_size=N, verify=fn) as w: w.write(b'...')

    Chỉ khi thoát khối KHÔNG lỗi và verify(tmp_path) không ném lỗi thì tmp mới được
    fsync + os.replace thành dest. Ngược lại dest cũ giữ nguyên và tmp bị xóa
    (hoặc chuyển vào failed_dir khi verify lỗi, để điều tra - đặc tả 6.7).
    """

    def __init__(self, dest, *, expected_size: int = 0, disk_factor: float = 2.5,
                 verify: Optional[Callable[[Path], None]] = None, backup: bool = False,
                 fsync: bool = True, mode: Optional[int] = None, cancel=None,
                 failed_dir=None, exclusive: bool = False):
        self.exclusive = exclusive      # True: KHÔNG ghi đè dest (os.link độc quyền); dest đã có -> E_OUTPUT_EXISTS
        self.dest = Path(dest)
        self.expected_size = expected_size
        self.disk_factor = disk_factor
        self.verify = verify
        self.backup = backup
        self.fsync = fsync
        self.mode = mode
        self.cancel = cancel
        self.failed_dir = Path(failed_dir) if failed_dir is not None else None
        self.path = None        # type: Optional[Path]
        self.bytes_written = 0
        self._fh = None

    def __enter__(self) -> "AtomicWriter":
        try:
            self.dest.parent.mkdir(parents=True, exist_ok=True)
            ensure_free_space(self.dest.parent, self.expected_size, factor=self.disk_factor,
                              where=str(self.dest))
            self.path = self.dest.parent / _tmp_name(self.dest)
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            self._fh = os.fdopen(fd, "wb")
        except PB3Error:
            raise
        except OSError as exc:
            raise error_from_exception(exc, where=str(self.dest), op="write")
        return self

    def write(self, data) -> int:
        if self.cancel is not None:
            self.cancel.check(str(self.dest))
        try:
            n = self._fh.write(data)
        except OSError as exc:
            raise error_from_exception(exc, where=str(self.dest), op="write")
        self.bytes_written += len(data)
        return n

    def _discard(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        except OSError:
            pass
        self._fh = None
        if self.path is not None:
            remove_quiet(self.path)

    def __exit__(self, et, ev, tb) -> bool:
        if et is not None:
            self._discard()
            return False
        try:
            self._fh.flush()
            if self.fsync:
                os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None
            if self.verify is not None:
                try:
                    self.verify(self.path)
                except Exception as vexc:
                    kept = self._keep_failed()
                    if isinstance(vexc, PB3Error) and vexc.code == "E_VERIFY_FAILED":
                        raise
                    raise PB3Error("E_VERIFY_FAILED", "%s%s" % (type(vexc).__name__, kept),
                                   where=str(self.dest))
            if self.mode is not None:
                os.chmod(str(self.path), self.mode)
            if self.backup and os.path.lexists(str(self.dest)):
                backup_file(self.dest)
            if self.exclusive:
                self._publish_exclusive()
            else:
                os.replace(str(self.path), str(self.dest))
            _fsync_dir(self.dest.parent)
        except PB3Error:
            self._discard()
            raise
        except OSError as exc:
            self._discard()
            raise error_from_exception(exc, where=str(self.dest), op="write")
        return False

    def _publish_exclusive(self) -> None:
        """Đặt tmp thành dest mà không bao giờ đè: os.link thất bại nếu dest đã có (kể cả do phiên song song vừa tạo).
        Hệ tệp không hỗ trợ link -> kiểm tồn tại rồi os.replace (còn khe đua rất hẹp, chỉ ở nhánh dự phòng)."""
        try:
            os.link(str(self.path), str(self.dest))
        except FileExistsError:
            raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại", where=str(self.dest))
        except OSError:
            if os.path.lexists(str(self.dest)):
                raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại", where=str(self.dest))
            os.replace(str(self.path), str(self.dest))
            return
        remove_quiet(self.path)

    def _keep_failed(self) -> str:
        if self.failed_dir is None or self.path is None:
            self._discard()
            return ""
        try:
            self.failed_dir.mkdir(parents=True, exist_ok=True)
            target = self.failed_dir / (self.dest.name + FAILED_SUFFIX + "-" + secrets.token_hex(3))
            os.replace(str(self.path), str(target))
            self.path = None
            return "; bản lỗi giữ tại %s" % target.name
        except OSError:
            self._discard()
            return ""


def atomic_write_bytes(dest, data: bytes, **kw) -> Path:
    kw.setdefault("expected_size", len(data))
    with AtomicWriter(dest, **kw) as w:
        w.write(data)
    return Path(dest)


# ------------------------------------------------------------------- output
def versioned_path(dest) -> Path:
    """name.ext -> name.v1.ext, name.v2.ext... (số đầu tiên chưa tồn tại)."""
    d = Path(dest)
    n = 1
    while True:
        cand = d.with_name("%s.v%d%s" % (d.stem, n, d.suffix))
        if not os.path.lexists(str(cand)):
            return cand
        n += 1


def resolve_output(output_root, rel: str, *, policy: str = "version") -> Path:
    """Đường dẫn output cuối cùng. Lần đầu: tên gốc (game cần đúng tên). Đã tồn tại:
    policy 'version' -> name.vN.ext; 'error' -> E_OUTPUT_EXISTS. KHÔNG bao giờ ghi đè."""
    target = resolve_inside(output_root, rel)
    if not os.path.lexists(str(target)):
        return target
    if policy == "error":
        raise PB3Error("E_OUTPUT_EXISTS", "đã tồn tại", where=rel)
    if policy != "version":
        raise PB3Error("E_INTERNAL", "policy output không hợp lệ: %r" % policy)
    return versioned_path(target)


def write_output_bytes(output_root, rel: str, data: bytes, *, policy: str = "version",
                       dry_run: bool = False, **kw) -> Path:
    """Ghi file output an toàn. dry_run=True chỉ trả về đường dẫn sẽ ghi (không tạo gì)."""
    final = resolve_output(output_root, rel, policy=policy)
    if dry_run:
        return final
    kw.setdefault("exclusive", True)                 # không bao giờ đè, kể cả khi phiên khác vừa tạo cùng tên
    for _ in range(100):
        try:
            return atomic_write_bytes(final, data, **kw)
        except PB3Error as exc:
            if exc.code != "E_OUTPUT_EXISTS" or policy != "version" or not kw["exclusive"]:
                raise
            final = resolve_output(output_root, rel, policy=policy)     # tên vừa bị giành: chọn số kế tiếp
    raise PB3Error("E_OUTPUT_EXISTS", "không chọn được tên .vN sau nhiều lần thử", where=rel)


def backup_file(path, backup_dir=None) -> Path:
    """Sao lưu file đã tồn tại thành <tên>.bak-<thời gian>[-n]; không ghi đè bản sao lưu cũ."""
    src = Path(path)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = Path(backup_dir) if backup_dir is not None else src.parent
    n = 0
    while True:
        cand = bdir / ("%s.bak-%s%s" % (src.name, ts, "" if n == 0 else "-%d" % n))
        if not os.path.lexists(str(cand)):
            break
        n += 1
    copy_atomic(src, cand, verify_hash=False)
    return cand


def copy_atomic(src, dest, *, chunk_size: int = 1024 * 1024, cancel=None, progress=None,
                verify_hash: bool = True) -> Tuple[str, int]:
    """Sao chép streaming nguyên tử; trả (sha256, kích thước). Hủy giữa chừng không để lại dở dang."""
    src_p, h, total = Path(src), None, 0
    import hashlib
    h = hashlib.sha256()
    try:
        size = os.stat(str(src_p)).st_size
    except OSError as exc:
        raise error_from_exception(exc, where=str(src_p))

    def _verify(tmp: Path) -> None:
        if hashing.sha256_file(tmp, chunk_size=chunk_size) != h.hexdigest():
            raise PB3Error("E_VERIFY_FAILED", "hash bản sao khác nguồn", where=str(dest))

    with AtomicWriter(dest, expected_size=size, cancel=cancel,
                      verify=_verify if verify_hash else None) as w:
        for b in iter_chunks(src_p, chunk_size, cancel=cancel):
            h.update(b)
            w.write(b)
            total += len(b)
            if progress is not None:
                progress.update(done=total)
    return h.hexdigest(), total


# ------------------------------------------------------- temp dở dang / dọn
def make_tempdir(base, prefix: str = "tmp-") -> Path:
    base_p = Path(base)
    try:
        base_p.mkdir(parents=True, exist_ok=True)
        import tempfile
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(base_p)))
    except OSError as exc:
        raise error_from_exception(exc, where=str(base), op="write")


def remove_tree_inside(base, path) -> None:
    """rmtree CHỈ khi path nằm hẳn trong base (không bao giờ xóa base, không thoát bằng symlink)."""
    b, p = os.path.realpath(str(base)), os.path.realpath(str(path))
    if b == p or not is_within(b, p):
        raise PB3Error("E_UNSAFE_PATH", "từ chối xóa ngoài thư mục tạm", where=str(path))
    shutil.rmtree(p, ignore_errors=True)


def find_stale_temps(roots: Sequence, *, min_age_s: float = 0.0,
                     now: Optional[float] = None) -> List[Path]:
    """Tìm file '.pb3part-*' còn sót (tiến trình bị dừng) và thư mục 'run-*'
    cũ (phiên bị ngắt, vd workspace/temp/run-<session>). Không đi theo symlink.
    Chỉ TÌM (để cách ly bằng quarantine); KHÔNG tự xóa gì."""
    now = time.time() if now is None else now
    found: List[Path] = []
    for r in roots:
        if not os.path.isdir(str(r)):
            continue
        for dirpath, dirnames, filenames in os.walk(str(r), followlinks=False):
            for fn in filenames:
                if STALE_MARKER in fn:
                    fp = Path(dirpath) / fn
                    try:
                        if now - fp.lstat().st_mtime >= min_age_s:
                            found.append(fp)
                    except OSError:
                        continue
            for dn in dirnames:
                if dn.startswith("run-"):
                    dp = Path(dirpath) / dn
                    try:
                        if now - dp.lstat().st_mtime >= min_age_s:
                            found.append(dp)
                    except OSError:
                        continue
    return sorted(found)


def quarantine(paths: Sequence, dest_dir) -> List[Tuple[Path, Path]]:
    """Di chuyển (không xóa) temp dở dang vào dest_dir để người dùng xem/xóa thủ công."""
    out: List[Tuple[Path, Path]] = []
    d = Path(dest_dir)
    for p in paths:
        try:
            d.mkdir(parents=True, exist_ok=True)
            target = d / ("%s.%s" % (Path(p).name.lstrip("."), secrets.token_hex(3)))
            try:
                os.replace(str(p), str(target))
            except OSError:
                shutil.move(str(p), str(target))
            out.append((Path(p), target))
        except OSError:
            continue
    return out
