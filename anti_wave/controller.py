"""May trang thai NORMAL / WAVE / RECOVERY (Anti-Wave, Phase 5 — AUTO).

Ba trang thai (spec MASTER):
  NORMAL   — ghi binh thuong; probe dinh ky van WAVE_OFF.
  WAVE     — song EPERM dang bat: DUNG moi ghi local, chi doc + checkpoint + CI.
  RECOVERY — song vua tat theo heuristic 5 lan probe thanh cong cach nhau
             30 giay: kiem tra toan ven, dong bo phan con thieu, roi moi ve NORMAL.

Phase 2: skeleton + chuyen trang thai TUONG MINH (to_wave / to_recovery /
to_normal). Phase 5 them AUTO: tick() probe 1 lan moi buoc, tu chuyen
trang thai; run() lap tick() dinh ky; guard_write() chan ghi khi khong
o NORMAL; vao WAVE thi xuat checkpoint/patch qua kenh doc lap (Bear);
o RECOVERY thi chay recovery (12 buoc Phase 4), xung dot -> DUNG, bao MASTER.

Quy tac bat buoc:
  * KHONG bao gio tu WAVE ve thang NORMAL sau 1 lan ghi thanh cong —
    phai du `checks` lan probe WAVE_OFF lien tiep (mac dinh 5 lan; moi
    tick cach nhau `interval_s` giay, mac dinh 30 giay). Day la heuristic
    khoi dau cua MASTER, KHONG phai bang chung on dinh lau dai.
  * Chuyen sai (vd WAVE -> NORMAL thang, NORMAL -> RECOVERY) raise
    PB3Error E_INTERNAL chu khong im lang.
  * Moi chuyen trang thai + su kien song deu log qua anti_wave.logger
    (RedactingFilter — khong bao gio log gia tri token/secret).
  * tick() khong bao gio raise vi ly do probe (probe chet -> coi nhu
    WAVE_ON, fail-closed); chi raise PB3Error E_WRITE_WAVE tu guard_write.

Chi stdlib, Python 3.9 tuong thich.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from anti_wave.healthcheck import ProbeResult, WaveState, probe_wave, WAVE_OFF, WAVE_ON
from anti_wave.logger import get_logger, log_event
from anti_wave.recovery import ready_for_normal
from core.errors import PB3Error

NORMAL = "NORMAL"
WAVE = "WAVE"
RECOVERY = "RECOVERY"

STATES = (NORMAL, WAVE, RECOVERY)

DEFAULT_CHECKS = 5
DEFAULT_INTERVAL_S = 30.0


class Controller:
    """May trang thai chong song — AUTO (Phase 5).

    probe: ham tra ProbeResult (mac dinh probe_wave that).
    checks / interval_s: heuristic lang cua MASTER (5 lan / 30 giay).
    recovery_runner: doi tuong co .run() (vd Recovery Phase 4) hoac
        callable () -> report dict (co ready_for_normal). None = chi
        probe (khong chay 12 buoc RECOVERY — ghi ro trong bao cao).
    sync: doi tuong kieu GitHubSync (save_checkpoint / save_patch) hoac
        None (= lam viec offline: vao WAVE chi log, khong xuat).
    agent_id / branch: dinh danh de xuat checkpoint/patch khi vao WAVE.
    checkpoint: dict checkpoint v1 de xuat khi vao WAVE (None = khong xuat).
    patch_text: unified diff de xuat khi vao WAVE ("" = khong xuat).
    """

    def __init__(self, *, probe: Optional[Callable[[], ProbeResult]] = None,
                 checks: int = DEFAULT_CHECKS, interval_s: float = DEFAULT_INTERVAL_S,
                 recovery_runner: Any = None, sync: Any = None,
                 agent_id: Optional[str] = None, branch: Optional[str] = None,
                 checkpoint: Optional[Dict[str, Any]] = None,
                 patch_text: str = "", logger: Any = None):
        if checks < 1:
            raise ValueError("checks phai >= 1")
        if interval_s < 0:
            raise ValueError("interval_s phai >= 0")
        self.state = NORMAL
        self.wave = WaveState()
        self.transitions: List[Dict[str, object]] = []
        self._probe = probe if probe is not None else (lambda: probe_wave())
        self.checks = checks
        self.interval_s = interval_s
        self.recovery_runner = recovery_runner
        self.sync = sync
        self.agent_id = agent_id
        self.branch = branch
        self.checkpoint = checkpoint
        self.patch_text = patch_text or ""
        self._logger = logger if logger is not None else get_logger("anti_wave.controller")
        # Trang thai AUTO (chi tick()/run() dung).
        self.calm_streak = 0
        self.needs_master = False
        self.last_recovery_report: Optional[Dict[str, Any]] = None
        self._recovery_done = False

    # ------------------------------------------------------------- log
    def _log(self, frm: str, to: str, reason: str) -> None:
        self.transitions.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "from": frm, "to": to, "reason": reason})
        log_event(self._logger, "state_change",
                  from_state=frm, to_state=to, reason=reason)

    # ------------------------------------------------------------- chuyen (Phase 2, giu nguyen)
    def to_wave(self, reason: str = "") -> str:
        """NORMAL/RECOVERY -> WAVE: song bat (probe EPERM hoac ghi loi EPERM)."""
        if self.state not in (NORMAL, RECOVERY):
            raise PB3Error("E_INTERNAL", "khong chuyen %s -> WAVE" % self.state,
                           where="anti_wave.controller")
        frm = self.state
        self.calm_streak = 0
        self.needs_master = False
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
        self._recovery_done = False
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

    # ------------------------------------------------------------- AUTO (Phase 5)
    def _probe_once(self) -> str:
        """Probe 1 lan, tra status; probe chet -> WAVE_ON (fail-closed)."""
        try:
            res = self._probe()
        except Exception as exc:  # noqa: BLE001 - probe loi = coi nhu song
            log_event(self._logger, "probe_error", error=type(exc).__name__)
            return WAVE_ON
        status = res.status if isinstance(res, ProbeResult) else str(res)
        return status if status in (WAVE_ON, WAVE_OFF) else WAVE_ON

    def _enter_recovery(self, reason: str = "") -> str:
        """WAVE -> RECOVERY NOI BO cho tick(): khong probe lai — tick() da
        dem du `checks` lan WAVE_OFF lien tiep cach nhau interval_s."""
        if self.state != WAVE:
            raise PB3Error("E_INTERNAL", "khong chuyen %s -> RECOVERY" % self.state,
                           where="anti_wave.controller")
        self.state = RECOVERY
        self._recovery_done = False
        self._log(WAVE, RECOVERY,
                  reason or "du %d lan probe lang lien tiep" % self.checks)
        return self.state

    def _on_wave_enter(self) -> None:
        """Vao WAVE: dung ghi VM (co guard_write), xuat checkpoint/patch
        qua kenh doc lap (sync/Bear) neu co. Best-effort: loi xuat chi log,
        khong bao gio raise (song dang bat, khong duoc chet them)."""
        log_event(self._logger, "wave_on", state=WAVE,
                  detail="dung ghi VM; xuat checkpoint/patch neu co")
        if self.sync is None:
            return
        if self.checkpoint is not None and self.agent_id and self.branch:
            try:
                self.sync.save_checkpoint(self.agent_id, self.checkpoint, self.branch)
                log_event(self._logger, "checkpoint_exported",
                          agent_id=self.agent_id, branch=self.branch)
            except Exception as exc:  # noqa: BLE001 - xuat loi thi log, khong chet
                log_event(self._logger, "checkpoint_export_failed",
                          agent_id=self.agent_id or "",
                          error="%s: %s" % (type(exc).__name__, exc))
        if self.patch_text and self.agent_id and self.branch:
            try:
                self.sync.save_patch(self.agent_id, self.patch_text, self.branch)
                log_event(self._logger, "patch_exported",
                          agent_id=self.agent_id, branch=self.branch)
            except Exception as exc:  # noqa: BLE001 - xuat loi thi log, khong chet
                log_event(self._logger, "patch_export_failed",
                          agent_id=self.agent_id or "",
                          error="%s: %s" % (type(exc).__name__, exc))

    def _run_recovery_runner(self) -> Optional[Dict[str, Any]]:
        """Chay 1 lan recovery_runner, tra report dict (None neu loi)."""
        runner = self.recovery_runner
        if runner is None:
            return {"status": "ok", "ready_for_normal": True,
                    "detail": "khong co recovery_runner — chi probe, "
                              "khong chay 12 buoc RECOVERY"}
        try:
            if hasattr(runner, "run") and callable(getattr(runner, "run")):
                report = runner.run()
            else:
                report = runner()
        except Exception as exc:  # noqa: BLE001 - recovery loi = chua dat
            return {"status": "failed", "ready_for_normal": False,
                    "detail": "%s: %s" % (type(exc).__name__, exc)}
        if not isinstance(report, dict):
            return {"status": "failed", "ready_for_normal": False,
                    "detail": "recovery_runner tra %s, khong phai dict"
                              % type(report).__name__}
        return report

    def _maybe_run_recovery(self) -> None:
        """Chay recovery 1 lan cho dot RECOVERY hien tai (ghi nho ket qua).
        Report khong dat -> needs_master=True (dung lai, bao MASTER)."""
        if self._recovery_done:
            return
        self._recovery_done = True
        report = self._run_recovery_runner()
        self.last_recovery_report = report
        if report is None or not ready_for_normal(report):
            self.needs_master = True
            log_event(self._logger, "recovery_blocked",
                      status=(report or {}).get("status", "failed"),
                      detail=(report or {}).get("detail", ""))
        else:
            self.needs_master = False
            log_event(self._logger, "recovery_ok",
                      detail=(report or {}).get("detail", ""))

    def tick(self) -> str:
        """1 buoc tu dong: probe 1 lan, chuyen trang thai neu du dieu kien.

        NORMAL: probe WAVE_ON -> WAVE (+ xuat checkpoint/patch).
        WAVE: probe WAVE_OFF lien tiep du `checks` lan -> RECOVERY (+ chay
            recovery); WAVE_ON giua chung -> dem lai tu 0.
        RECOVERY: probe WAVE_ON -> WAVE lai; probe WAVE_OFF -> chay recovery
            (neu chua chay), dat thi ve NORMAL (to_normal probe lai 1 lan).
        Khong bao gio raise vi ly do probe.
        """
        status = self._probe_once()
        self.wave.record(status)
        if self.state == NORMAL:
            if status == WAVE_ON:
                self.to_wave("probe EPERM")
                self._on_wave_enter()
            else:
                self.calm_streak = 0
            return self.state
        if self.state == WAVE:
            if status != WAVE_OFF:
                self.calm_streak = 0
                return self.state
            self.calm_streak += 1
            log_event(self._logger, "calm_probe",
                      streak=self.calm_streak, checks=self.checks)
            if self.calm_streak >= self.checks:
                self._enter_recovery(
                    "du %d lan probe lang lien tiep cach nhau %.0fs (heuristic)"
                    % (self.checks, self.interval_s))
                self._maybe_run_recovery()
            return self.state
        # RECOVERY
        if status != WAVE_OFF:
            self.calm_streak = 0
            self._recovery_done = False
            self.to_wave("song bat lai trong RECOVERY")
            return self.state
        if not self._recovery_done:
            self._maybe_run_recovery()
        if self.last_recovery_report is not None and \
                ready_for_normal(self.last_recovery_report):
            try:
                self.to_normal("RECOVERY dat, probe lang")
            except Exception as exc:  # noqa: BLE001 - giu RECOVERY, cho tick sau
                log_event(self._logger, "to_normal_blocked",
                          error="%s: %s" % (type(exc).__name__, exc))
        return self.state

    def run(self, cycles: Optional[int] = None,
            interval_s: Optional[float] = None) -> str:
        """Vong lap tu dong: tick() roi ngu `interval_s` giay.

        cycles=None: chay mai (daemon — goi tu script chu, bat Ctrl+C de
        dung; KeyboardInterrupt khong bi nuot). cycles=N: chay dung N tick
        roi tra trang thai cuoi (de test / chay 1 dot). interval_s=None:
        dung self.interval_s (mac dinh 30 giay).
        """
        gap = self.interval_s if interval_s is None else interval_s
        if gap < 0:
            raise ValueError("interval_s phai >= 0")
        n = 0
        while cycles is None or n < cycles:
            self.tick()
            n += 1
            if cycles is None or n < cycles:
                time.sleep(gap)
        return self.state

    # ------------------------------------------------------------- chan ghi / hoi
    @property
    def writes_blocked(self) -> bool:
        """True khi khong o NORMAL — agent phai dung moi ghi VM."""
        return self.state != NORMAL

    def guard_write(self, where: str = "") -> bool:
        """Chan ghi: o NORMAL thi True; WAVE/RECOVERY thi raise E_WRITE_WAVE."""
        if self.state != NORMAL:
            raise PB3Error("E_WRITE_WAVE",
                           "dang o trang thai %s — dung ghi VM" % self.state,
                           where=where or "anti_wave.controller")
        return True

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
            return ("WAVE: sóng EPERM đang bật — dừng ghi local.%s "
                    "(probe lặng liên tiếp: %d/%d)"
                    % (extra, self.calm_streak, self.checks))
        tail = (" Cần báo MASTER (xung đột/khôi phục chưa đạt)."
                if self.needs_master else "")
        return ("RECOVERY: sóng vừa tắt theo heuristic — kiểm tra toàn vẹn "
                "trước khi về NORMAL.%s" % tail)
