"""May trang thai NORMAL / WAVE / RECOVERY (Anti-Wave, Phase 2) — SKELETON.

Ba trang thai (spec MASTER):
  NORMAL   — ghi binh thuong; probe dinh ky van WAVE_OFF.
  WAVE     — song EPERM dang bat: DUNG moi ghi local, chi doc + checkpoint + CI.
  RECOVERY — song vua tan theo heuristic 5 lan probe thanh cong cach nhau
             30 giay: kiem tra toan ven, dong bo phan con thieu, roi moi ve NORMAL.

Phase 2 chi co skeleton + chuyen trang thai TUONG MINH (goi truc tiep).
Tu dong chuyen (AUTO transition theo probe dinh ky) la Phase 5 — khong co o day.
Chuyen sai (vd WAVE -> NORMAL thang, NORMAL -> RECOVERY) raise PB3Error
E_INTERNAL chu khong im lang.

Chi stdlib, Python 3.9 tuong thich.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from anti_wave.healthcheck import ProbeResult, WaveState, probe_wave, WAVE_OFF, WAVE_ON
from core.errors import PB3Error

NORMAL = "NORMAL"
WAVE = "WAVE"
RECOVERY = "RECOVERY"

STATES = (NORMAL, WAVE, RECOVERY)


class Controller:
    """May trang thai chong song (skeleton Phase 2)."""

    def __init__(self, *, probe: Optional[Callable[[], ProbeResult]] = None):
        self.state = NORMAL
        self.wave = WaveState()
        self.transitions: List[Dict[str, object]] = []
        self._probe = probe if probe is not None else (lambda: probe_wave())

    # ------------------------------------------------------------- log
    def _log(self, frm: str, to: str, reason: str) -> None:
        self.transitions.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "from": frm, "to": to, "reason": reason})

    # ------------------------------------------------------------- chuyen
    def to_wave(self, reason: str = "") -> str:
        """NORMAL/RECOVERY -> WAVE: song bat (probe EPERM hoac ghi loi EPERM)."""
        if self.state not in (NORMAL, RECOVERY):
            raise PB3Error("E_INTERNAL", "khong chuyen %s -> WAVE" % self.state,
                           where="anti_wave.controller")
        frm = self.state
        self.wave.record(WAVE_ON)
        self.state = WAVE
        self._log(frm, WAVE, reason or "song EPERM bat")
        return self.state

    def to_recovery(self, reason: str = "", *, checks: int = 5,
                    interval_s: float = 30.0) -> str:
        """WAVE -> RECOVERY: chi khi du `checks` lan probe ghi thanh cong
        cach nhau `interval_s` giay (heuristic MASTER — xem WaveState.
        wait_for_calm: KHONG phai bang chung on dinh lau dai). Chua du
        -> giu WAVE va raise E_WRITE_WAVE."""
        if self.state != WAVE:
            raise PB3Error("E_INTERNAL", "khong chuyen %s -> RECOVERY" % self.state,
                           where="anti_wave.controller")
        calm = self.wave.wait_for_calm(checks=checks, interval_s=interval_s,
                                       probe=self._probe)
        if not calm:
            raise PB3Error("E_WRITE_WAVE",
                           "chua du %d lan kiem tra ghi thanh cong cach nhau %.0fs; giu WAVE"
                           % (checks, interval_s),
                           where="anti_wave.controller")
        self.state = RECOVERY
        self._log(WAVE, RECOVERY, reason or "du %d lan probe lang" % checks)
        return self.state

    def to_normal(self, reason: str = "") -> str:
        """RECOVERY -> NORMAL: chi khi 1 probe ghi moi nhat WAVE_OFF (lang
        that luc nay) — kiem tra toan ven + dong bo phan con thieu do
        Phase 4 (recovery.py) dam nhan, o day chi co cong kiem tra probe.
        Probe van WAVE_ON -> giu RECOVERY va raise E_WRITE_WAVE."""
        if self.state != RECOVERY:
            raise PB3Error("E_INTERNAL", "khong chuyen %s -> NORMAL" % self.state,
                           where="anti_wave.controller")
        res = self._probe()
        status = res.status if isinstance(res, ProbeResult) else str(res)
        self.wave.record(status)
        if status != WAVE_OFF:
            raise PB3Error("E_WRITE_WAVE", "probe van WAVE_ON; giu RECOVERY",
                           where="anti_wave.controller")
        self.state = NORMAL
        self._log(RECOVERY, NORMAL, reason or "probe lang, ve NORMAL")
        return self.state

    # ------------------------------------------------------------- hoi
    def is_wave_on(self) -> bool:
        return self.state == WAVE

    def status_summary(self) -> str:
        """Tom tat tieng Viet 1 dong ve trang thai hien tai."""
        if self.state == NORMAL:
            return "NORMAL: ghi bình thường."
        if self.state == WAVE:
            est = self.wave.duration_estimate_s()
            extra = (" Đợt sóng trước kéo dài trung bình ~%.0f giây."
                     % est) if est is not None else ""
            return "WAVE: sóng EPERM đang bật — dừng ghi local.%s" % extra
        return "RECOVERY: sóng vừa tắt theo heuristic — kiểm tra toàn vẹn trước khi về NORMAL."
