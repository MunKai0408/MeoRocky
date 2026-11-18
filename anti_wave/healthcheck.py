"""Phat hien + phan loai loi ghi/doc trong dot song EPERM (Anti-Wave, Phase 2).

Probe chuan (xem ~/workspace/harness-fix/probe_wave.py): os.open + os.write
+ fsync + dong ro rang + doc lai + don dep. CO Y khong dung `tempfile`:
Python 3.12 `_get_default_tempdir()` tu probe ghi file vao tung thu muc ung
vien — trong song EPERM no chet voi FileNotFoundError "No usable temporary
directory" [DA DO THAT 2026-11-18] — nen probe_wave() tu chon thu muc va
dung os.open truc tiep.

Chi stdlib, Python 3.9 tuong thich.
"""
from __future__ import annotations

import errno
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

WAVE_ON = "WAVE_ON"
WAVE_OFF = "WAVE_OFF"

PROBE_BYTES = b"anti-wave-probe"
_PROBE_PREFIX = ".anti-wave-probe-"

# Ma phan loai (classify_error): moi ma kem goi y tieng Viet (xem HINTS).
READ_NOT_FOUND = "READ_NOT_FOUND"
READ_DENIED = "READ_DENIED"
READ_CORRUPT = "READ_CORRUPT"
WRITE_EPERM_WAVE = "WRITE_EPERM_WAVE"
WRITE_DENIED = "WRITE_DENIED"
WRITE_NO_SPACE = "WRITE_NO_SPACE"
FS_READONLY = "FS_READONLY"
OTHER = "OTHER"

HINTS = {
    READ_NOT_FOUND: "File/thư mục không tồn tại; kiểm tra lại đường dẫn.",
    READ_DENIED: "Không có quyền đọc; kiểm tra quyền của file/thư mục.",
    READ_CORRUPT: "File hỏng hoặc bị cắt cụt; dùng bản gốc nguyên vẹn, không đoán dữ liệu hỏng.",
    WRITE_EPERM_WAVE: "Đợt sóng chặn ghi (EPERM) đang bật: DỪNG ghi ngay, lưu checkpoint "
                      "(anti_wave.checkpoint) rồi thử lại sau; kiểm tra bằng probe_wave() "
                      "trước khi ghi tiếp.",
    WRITE_DENIED: "Không có quyền ghi thư mục đích; kiểm tra quyền hoặc chọn nơi khác.",
    WRITE_NO_SPACE: "Hết dung lượng trống; giải phóng bộ nhớ hoặc giảm phạm vi thao tác.",
    FS_READONLY: "Hệ thống file chỉ đọc (EROFS); chọn thư mục đích khác.",
    OTHER: "Lỗi chưa phân loại; xem chi tiết kỹ thuật kèm theo và báo lỗi kèm mã này.",
}


@dataclass(frozen=True)
class ErrorClass:
    """Ket qua classify_error: ma + goi y tieng Viet + errno goc + op."""
    code: str
    hint_vi: str
    errno: Optional[int] = None
    op: str = "write"


@dataclass
class ProbeResult:
    """Ket qua probe_wave: status WAVE_ON/WAVE_OFF + errno + chi tiet.

    WAVE_OFF + errno=None: ghi that su duoc (lang). WAVE_OFF + errno!=None:
    probe khong ket luan duoc vi ly do khac song (vd khong co thu muc tam
    nao ton tai) — nguoi goi phai doc `errno`/`detail`, khong duoc coi nhu
    lang chi vi status la WAVE_OFF.
    """
    status: str
    errno: Optional[int] = None
    detail: str = ""
    path: str = ""
    directory: str = ""


def classify_error(exc: BaseException, op: str = "write") -> ErrorClass:
    """Phan loai ngoai le doc/ghi thanh 1 trong 8 ma (+ goi y tieng Viet).

    Quy tac (op = "read" | "write"):
      errno 1  EPERM  + write -> WRITE_EPERM_WAVE (dau hieu dot song)
      errno 1  EPERM  + read  -> READ_DENIED
      errno 13 EACCES + read  -> READ_DENIED; + write -> WRITE_DENIED
      errno 2  ENOENT + read  -> READ_NOT_FOUND; + write -> OTHER
        (ghi thieu thu muc cha khong co ma rieng; hint neu ro)
      errno 28 ENOSPC / EDQUOT -> WRITE_NO_SPACE (moi op)
      errno 30 EROFS           -> FS_READONLY (moi op)
      loi giai ma / zip hong + read -> READ_CORRUPT
      con lai -> OTHER.
    Khong bao gio raise: ngoai le la thi tra OTHER.
    """
    if isinstance(exc, UnicodeDecodeError):
        return ErrorClass(READ_CORRUPT, HINTS[READ_CORRUPT], None, op)
    if isinstance(exc, OSError):
        no = getattr(exc, "errno", None)
        if no == errno.EPERM:
            if op == "write":
                return ErrorClass(WRITE_EPERM_WAVE, HINTS[WRITE_EPERM_WAVE], no, op)
            return ErrorClass(READ_DENIED, HINTS[READ_DENIED], no, op)
        if no == errno.EACCES:
            code = READ_DENIED if op == "read" else WRITE_DENIED
            return ErrorClass(code, HINTS[code], no, op)
        if no == errno.ENOENT:
            if op == "read":
                return ErrorClass(READ_NOT_FOUND, HINTS[READ_NOT_FOUND], no, op)
            return ErrorClass(OTHER, "Thư mục đích không tồn tại (ENOENT khi ghi).", no, op)
        if no == errno.ENOSPC or no == getattr(errno, "EDQUOT", -1):
            return ErrorClass(WRITE_NO_SPACE, HINTS[WRITE_NO_SPACE], no, op)
        if no == getattr(errno, "EROFS", 30):
            return ErrorClass(FS_READONLY, HINTS[FS_READONLY], no, op)
        if no == errno.EIO and op == "read":
            return ErrorClass(READ_CORRUPT, HINTS[READ_CORRUPT], no, op)
        if no == errno.EISDIR:
            code = READ_DENIED if op == "read" else WRITE_DENIED
            return ErrorClass(code, HINTS[code], no, op)
        return ErrorClass(OTHER, HINTS[OTHER], no, op)
    # BadZipFile khong phai OSError: import cham de tranh phu thuoc luc nap.
    import zipfile
    if isinstance(exc, zipfile.BadZipFile):
        return ErrorClass(READ_CORRUPT, HINTS[READ_CORRUPT], None, op)
    return ErrorClass(OTHER, HINTS[OTHER], None, op)


def _probe_candidates(directory=None):
    # Thu tu: thu muc chi dinh -> $TMPDIR -> /tmp -> thu muc lam viec.
    # Khong dung tempfile.gettempdir() (chet trong song EPERM, xem docstring module).
    cands = []
    if directory:
        cands.append(str(directory))
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        cands.append(env_tmp)
    cands.extend(["/tmp", os.getcwd()])
    seen = set()
    out = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def probe_wave(directory=None) -> ProbeResult:
    """Probe ghi chuan: tao file tam, ghi, fsync, dong, doc lai, xoa.

    Tra WAVE_ON neu gap EPERM (errno 1) o buoc ghi/fsync ma khong thu muc
    nao ghi duoc; WAVE_OFF neu ghi + doc lai thanh cong. Moi loi khac cung
    tra WAVE_OFF kem errno/detail (xem ProbeResult) — chi EPERM moi la song.
    Khong bao gio raise vi ly do probe (thu muc khong ton tai -> bo qua,
    thu muc tiep theo); file tam luon duoc don (best-effort).
    """
    last_err = None
    saw_eperm = False
    for cand in _probe_candidates(directory):
        try:
            is_dir = os.path.isdir(cand)
        except OSError as exc:
            # os.path.isdir() hiem khi raise, nhung neu no raise (vd EIO) thi
            # probe khong duoc chet: bo qua thu muc nay, thu muc tiep theo.
            last_err = (getattr(exc, "errno", None),
                        "%s (errno=%s)" % (type(exc).__name__, getattr(exc, "errno", None)))
            continue
        if not is_dir:
            last_err = (errno.ENOENT, "thu muc tam khong ton tai: %s" % cand)
            continue
        path = os.path.join(cand, "%s%d-%s" % (_PROBE_PREFIX, os.getpid(),
                                               secrets.token_hex(4)))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, PROBE_BYTES)
                os.fsync(fd)
            finally:
                os.close(fd)
            with open(path, "rb") as f:
                back = f.read()
            if back != PROBE_BYTES:
                return ProbeResult(WAVE_OFF, None,
                                   "doc lai khac du lieu da ghi", path, cand)
            return ProbeResult(WAVE_OFF, None, "ghi + doc lai OK", path, cand)
        except OSError as exc:
            no = getattr(exc, "errno", None)
            last_err = (no, "%s (errno=%s)" % (type(exc).__name__, no))
            if no == errno.EPERM:
                # Co the la quyen rieng cua thu muc nay: thu thu muc khac.
                saw_eperm = True
            continue
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    # Het ung vien ma khong ghi duoc o dau: thay EPERM o bat ky thu muc nao
    # -> ket luan song (fail-closed: bao song nham chi mat thoi gian cho,
    # bao lang nham co the lam mat/truncate file). Khong thay EPERM thi tra
    # WAVE_OFF kem errno cuoi de nguoi goi biet probe khong ket luan duoc.
    if saw_eperm:
        return ProbeResult(WAVE_ON, errno.EPERM,
                           "ghi bi chan (EPERM); loi cuoi: %s" % (last_err[1] if last_err else ""), "", "")
    if last_err:
        return ProbeResult(WAVE_OFF, last_err[0],
                           "khong probe duoc: %s" % last_err[1], "", "")
    return ProbeResult(WAVE_OFF, None, "khong co thu muc tam nao", "", "")


@dataclass
class WaveState:
    """Lich su song: ghi nhan tung lan probe, uoc luong do dai dot song.

    wave_start/wave_end la timestamp (time.time()) cua dot song gan nhat;
    history luu (timestamp, status) moi lan record(). duration_estimate_s()
    la trung binh cac dot song DA ket thuc — chi de tham khao lap ke hoach,
    khong phai du bao.
    """
    history: List[Tuple[float, str]] = field(default_factory=list)
    wave_start: Optional[float] = None
    wave_end: Optional[float] = None
    _durations: List[float] = field(default_factory=list)

    def record(self, status: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.history.append((now, status))
        if status == WAVE_ON and self.wave_start is None:
            self.wave_start = now
            self.wave_end = None
        elif status == WAVE_OFF and self.wave_start is not None:
            self._durations.append(now - self.wave_start)
            self.wave_end = now
            self.wave_start = None

    def is_wave_on(self) -> bool:
        return bool(self.history) and self.history[-1][1] == WAVE_ON

    def duration_estimate_s(self) -> Optional[float]:
        """Trung binh do dai cac dot song da ket thuc; None neu chua co dot nao."""
        if not self._durations:
            return None
        return sum(self._durations) / len(self._durations)

    def wait_for_calm(self, checks: int = 5, interval_s: float = 30.0,
                      probe: Optional[Callable[[], ProbeResult]] = None) -> bool:
        """Kiem tra lang: `checks` lan probe ghi thanh cong, cach nhau
        `interval_s` giay (mac dinh 5 lan / 30 giay — heuristic khoi dau cua
        MASTER).

        HEURISTIC, KHONG PHAI BANG CHUNG ON DINH LAU DAI: 5 lan ghi duoc
        chi noi "luc nay ghi duoc", khong bao dam song se khong bat lai sau
        do. Nguoi goi van phai probe lai truoc moi loat ghi lon, va chi
        chuyen sang NORMAL sau khi kiem tra toan ven (controller.to_normal).
        Tra True chi khi TAT CA lan probe deu WAVE_OFF; bat ky lan nao
        WAVE_ON (hoac probe loi) -> False ngay. Moi lan probe deu record()
        vao history.
        """
        if checks < 1:
            raise ValueError("checks phai >= 1")
        fn = probe if probe is not None else (lambda: probe_wave())
        for i in range(checks):
            if i:
                time.sleep(interval_s)
            res = fn()
            status = res.status if isinstance(res, ProbeResult) else str(res)
            self.record(status)
            if status != WAVE_OFF:
                return False
        return True
