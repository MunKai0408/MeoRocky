"""Mo phong song EPERM trong moi truong test rieng (Anti-Wave, Phase 6).

Day la giai doan CUOI cua chuong trinh anti-wave: chung minh dau-cuoi
rang bo cong cu (controller + checkpoint + patch + recovery + github_sync)
lam dung khi song that su bat/tat — ma KHONG BAO GIO gay loi that tren VM.

[MÔ PHỎNG — doc ky truoc khi doc ket qua]
  * Probe la GIA LAP (SimulatedProbe): lich trinh WAVE_ON/WAVE_OFF do
    kich ban dieu khien, khong phai probe_wave() that (probe that do /tmp,
    khong nam trong pham vi fault — xem duoi). Dem so lan probe (5 lan
    lang lien tiep) thi that: controller phai du `checks` lan moi chuyen.
  * Sync la GIA LAP (FakeGitHubSync): checkpoint/patch/dispatch chi luu
    trong RAM, KHONG mang that, KHONG ghi GitHub that. Dispatch Actions
    cung la gia (ghi lai lenh + tra run success gia) — bang chung dispatch
    THAT da co tu Phase 3 (204, run 44264828556) va PR #30 (run 44270988130).
  * Fault-injection o muc SYSCALL PYTHON: WaveFault patch `os.write` /
    `os.fsync` de raise EPERM (errno 1) chi cho file descriptor mo file
    nam trong sandbox root (tra qua /proc/self/fd/<fd>). Ghi qua `os.write`
    truc tiep (probe, _probe_dir_writable) that bai ngay; ghi qua
    `io.BufferedWriter` (AtomicWriter) di qua write() C nen khong bi chan
    o write — that bai o `os.fsync` sau do, temp bi don, file dich khong
    bi dong den. Ket qua cuoi (E_WRITE_WAVE, khong file 0 byte, dich
    nguyen ven) giong het song that.
  * File 0 byte do song: tao bang duong THO os.open(O_TRUNC) + os.write
    (dung co che kernel: open() thanh cong, write() EPERM) — tai hien
    dung vu viec 2026-11-18 06:26 (open(path, "w") cat file tien do ve
    0 byte). Ghi qua `open(..., "w")` cua Python thi buffer nen loi chi
    hien o flush — trong mo phong, duong tho moi la thu tao ra file 0 byte.

Kich ban dau-cuoi (run_wave_scenario):
  (a) song bat -> controller phat hien WAVE -> guard_write chan ghi
      (E_WRITE_WAVE) -> checkpoint + patch xuat qua kenh doc lap (fake
      sync) -> thu ghi that bi chan, khong de lai file 0 byte;
  (b) tao patch tu thay doi -> validate + ap patch thanh cong (hoac
      fail-closed khi patch hong);
  (c) song tat -> RECOVERY 12 buoc (5 probe lang — trong test dung probe
      gia, interval_s=0, KHONG cho that 150 giay; SO LAN (5) moi la thu
      duoc kiem chung, khoang cach 30 giay la mac dinh production) ->
      dong bo phan thieu -> ve NORMAL, toan ven khop;
  (d) xung dot (head SHA dich / checkpoint remote lech / patch hong /
      file 0 byte khong co ban tot / song bat lai trong RECOVERY) -> DUNG,
      needs_master=True, khong ve NORMAL.

Chi stdlib, Python 3.9 tuong thich. Khong ghi GitHub that, khong dispatch
that, khong gay loi that tren VM.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from anti_wave import integrity
from anti_wave.checkpoint import build_checkpoint
from anti_wave.controller import Controller
from anti_wave.github_sync import (  # noqa: F401
    CHECKPOINTS_DIR,
    SyncError,
    _canonical,
    failset_from_junitxml,
    validate_checkpoint,
    validate_patch_text,
)
from anti_wave.healthcheck import ProbeResult, WAVE_OFF, WAVE_ON
from anti_wave.patch_manager import build_patch, files_for_checkpoint
from anti_wave.recovery import Recovery, ready_for_normal
from core.errors import PB3Error
from core.safe_io import AtomicWriter, write_bytes_guarded

# ------------------------------------------------------------------ du lieu mau
# Workspace gia lap: 3 file ban dau; patch doi a.txt + them d.txt; c.txt
# se bi song cat ve 0 byte roi duoc khoi phuc tu ban tot.
DEFAULT_INITIAL_FILES = {
    "notes/a.txt": b"dong 1\ndong 2\ndong 3\n",
    "notes/b.txt": b"khong doi\n",
    "work/c.txt": b"se bi cat 0 byte\n",
}
DEFAULT_CHANGES = {
    "notes/a.txt": b"dong 1\ndong 2 DA SUA\ndong 3\ndong 4 moi\n",
    "notes/d.txt": b"file moi trong patch\n",
}
DEFAULT_TRUNCATE = ["work/c.txt"]

SIM_AGENT_ID = "sim-agent"
SIM_TASK_ID = "phase6-simulation"
SIM_BRANCH = "feat/anti-wave-phase6-sim"
SIM_WORKFLOW = "anti-wave-ci.yml"

__all__ = [
    "WaveFault",
    "truncate_by_failed_write",
    "FakeGitHubSync",
    "SimulatedProbe",
    "run_wave_scenario",
    "run_all_scenarios",
    "BUILTIN_SCENARIOS",
    "simulation_summary",
    "simulation_report_summary",
    "DEFAULT_INITIAL_FILES",
    "DEFAULT_CHANGES",
    "DEFAULT_TRUNCATE",
]


# ------------------------------------------------------------------ fault injection
class WaveFault:
    """Gia lap song EPERM: patch os.write/os.fsync de raise EPERM chi cho
    file mo trong sandbox `root`.

    Dung lam context manager (`with WaveFault(root): ...`). Chi chan ghi
    cua file dich nam trong root (tra duong dan fd qua /proc/self/fd);
    fd khong tra duoc duong dan (pipe, socket, /dev/pts) thi KHONG chan
    (fail-open) de khong lam chet pytest/logging. Dem so lan chan.
    Thoat khoi (ke ca khi co exception) thi khoi phuc os.write/os.fsync
    goc. Khong long 2 WaveFault cung luc (raise RuntimeError).
    """

    _GLOBAL_ACTIVE = False

    def __init__(self, root, *, fault_fsync: bool = True):
        self.root = os.path.realpath(str(root))
        self.fault_fsync = fault_fsync
        self.blocked_writes = 0
        self.blocked_fsyncs = 0
        self._orig_write = None
        self._orig_fsync = None
        self._active = False

    def _fd_under_root(self, fd) -> Optional[str]:
        """Duong dan that cua fd, neu nam trong root; None neu khong phai."""
        try:
            target = os.readlink("/proc/self/fd/%d" % fd)
        except OSError:
            return None
        if target.endswith(" (deleted)"):
            target = target[: -len(" (deleted)")]
        if target == self.root or target.startswith(self.root + os.sep):
            return target
        return None

    def _write(self, fd, data):
        target = self._fd_under_root(fd)
        if target is not None:
            self.blocked_writes += 1
            raise OSError(errno.EPERM,
                          "Operation not permitted (mo phong song EPERM)", target)
        return self._orig_write(fd, data)

    def _fsync(self, fd):
        target = self._fd_under_root(fd)
        if target is not None:
            self.blocked_fsyncs += 1
            raise OSError(errno.EPERM,
                          "Operation not permitted (mo phong song EPERM)", target)
        return self._orig_fsync(fd)

    def __enter__(self) -> "WaveFault":
        if self._active or WaveFault._GLOBAL_ACTIVE:
            raise RuntimeError("WaveFault da bat — khong long 2 fault cung luc")
        self._orig_write = os.write
        self._orig_fsync = os.fsync
        os.write = self._write  # type: ignore[assignment]
        if self.fault_fsync:
            os.fsync = self._fsync  # type: ignore[assignment]
        self._active = True
        WaveFault._GLOBAL_ACTIVE = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        os.write = self._orig_write  # type: ignore[assignment]
        if self.fault_fsync:
            os.fsync = self._orig_fsync  # type: ignore[assignment]
        self._active = False
        WaveFault._GLOBAL_ACTIVE = False
        return False


def truncate_by_failed_write(path, data: bytes = b"du lieu se mat") -> int:
    """Tai hien dung co che gay file 0 byte cua song that: open() thanh
    cong (cat file ve 0 byte) roi write() EPERM.

    Dung duong THO os.open + os.write (khong qua io buffered — xem
    docstring module). Tra ve kich thuoc file sau do (ky vong 0).
    Chi goi khi WaveFault dang bat; neu khong co fault thi ghi binh
    thuong va tra ve len(data).
    """
    p = Path(path)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
    except OSError:
        # Ghi that bai (song): file van 0 byte — dung nhu song that de lai.
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return p.stat().st_size


# ------------------------------------------------------------------ sync gia
class FakeGitHubSync:
    """GitHubSync gia lap — chi RAM, KHONG mang that, KHONG ghi GitHub that.

    Cung mat phuong thuc ma controller / recovery / patch_manager dung:
    save/load checkpoint, save patch, get_file, get_branch_head,
    dispatch/poll workflow, list artifacts. Dispatch chi ghi lai lenh va
    tra run gia completed/success (bang chung dispatch THAT da co tu
    Phase 3 + PR #30). blob_sha o day la sha256 noi dung (khong phai git
    blob sha that) — ghi ro de khong ai nham la bang chung GitHub that.
    """

    def __init__(self, head_sha: str, files: Optional[Dict[str, bytes]] = None):
        self.head_sha = head_sha
        self.files: Dict[str, bytes] = dict(files or {})
        self.saved_checkpoints: List[Any] = []  # [(agent_id, checkpoint, branch)]
        self.saved_patches: List[Any] = []  # [(agent_id, patch_text, branch)]
        self.dispatches: List[Any] = []  # [(workflow_file, ref, inputs)]
        self._runs: List[Dict[str, Any]] = []
        self._next_run_id = 1

    # -- duong dan (cung quy uoc github_sync) ----------------------
    def checkpoint_path(self, agent_id: str) -> str:
        return "%s/%s.json" % (CHECKPOINTS_DIR, agent_id)

    def patch_path(self, agent_id: str) -> str:
        return "%s/%s.patch" % (CHECKPOINTS_DIR, agent_id)

    # -- nhanh / file ------------------------------------------------
    def get_branch_head(self, branch: str) -> str:
        return self.head_sha

    def move_head(self, branch: str, new_sha: str) -> str:
        """Gia lap agent khac day commit moi len nhanh (de gay xung dot)."""
        self.head_sha = new_sha
        return new_sha

    def put_file(self, branch: str, path: str, data: bytes) -> None:
        """Gia lap agent khac ghi 1 file len nhanh."""
        self.files[path] = bytes(data)

    def get_file(self, branch: str, path: str):
        data = self.files.get(path)
        if data is None:
            return None
        return (hashlib.sha256(data).hexdigest(), data)

    # -- checkpoint / patch ------------------------------------------
    def save_checkpoint(self, agent_id: str, checkpoint: Dict[str, Any],
                        branch: str,
                        expected_base_sha: Optional[str] = None) -> Dict[str, str]:
        validate_checkpoint(checkpoint)
        if expected_base_sha is not None and expected_base_sha != self.head_sha:
            raise SyncError(
                "E_CONFLICT",
                "nhanh %s da dich (%s != %s) — dung ghi de"
                % (branch, self.head_sha[:12], expected_base_sha[:12]))
        data = _canonical(checkpoint) + b"\n"
        self.files[self.checkpoint_path(agent_id)] = data
        self.saved_checkpoints.append((agent_id, checkpoint, branch))
        return {
            "path": self.checkpoint_path(agent_id),
            "blob_sha": hashlib.sha256(data).hexdigest(),
            "commit_sha": self.head_sha,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def load_checkpoint(self, agent_id: str, branch: str) -> Dict[str, Any]:
        found = self.get_file(branch, self.checkpoint_path(agent_id))
        if found is None:
            raise SyncError(
                "E_NOT_FOUND",
                "chua co checkpoint cho agent %s tren nhanh %s" % (agent_id, branch))
        try:
            payload = json.loads(found[1].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SyncError("E_VALIDATION",
                            "checkpoint cua %s hong (khong phai JSON)" % agent_id)
        return validate_checkpoint(payload)

    def list_checkpoints(self, branch: str) -> List[str]:
        prefix = CHECKPOINTS_DIR + "/"
        return sorted(p[len(prefix):-5] for p in self.files
                      if p.startswith(prefix) and p.endswith(".json"))

    def save_patch(self, agent_id: str, patch_text: str, branch: str,
                   expected_base_sha: Optional[str] = None) -> Dict[str, str]:
        problems = validate_patch_text(patch_text)
        if problems:
            raise SyncError("E_VALIDATION",
                            "patch khong hop le: %s" % "; ".join(problems))
        if expected_base_sha is not None and expected_base_sha != self.head_sha:
            raise SyncError(
                "E_CONFLICT",
                "nhanh %s da dich (%s != %s) — dung ghi de"
                % (branch, self.head_sha[:12], expected_base_sha[:12]))
        data = patch_text.encode("utf-8")
        self.files[self.patch_path(agent_id)] = data
        self.saved_patches.append((agent_id, patch_text, branch))
        return {
            "path": self.patch_path(agent_id),
            "blob_sha": hashlib.sha256(data).hexdigest(),
            "commit_sha": self.head_sha,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    # -- Actions gia (khong mang that) --------------------------------
    def dispatch_workflow(self, workflow_file: str, ref: str,
                          inputs: Optional[Dict[str, str]] = None) -> bool:
        """Ghi lai lenh dispatch, tra run gia completed/success ngay."""
        self.dispatches.append((workflow_file, ref, dict(inputs or {})))
        run = {
            "id": self._next_run_id,
            "status": "completed",
            "conclusion": "success",
            "head_branch": ref,
            "head_sha": self.head_sha,
            "created_at": "2026-11-18T10:00:00Z",
            "html_url": "https://github.com/MunKai0408/MeoRocky/actions/runs/%d"
                        % self._next_run_id,
            "fake": True,
        }
        self._next_run_id += 1
        self._runs.append(run)
        return True

    def list_workflow_runs(self, workflow_file: str,
                           per_page: int = 5) -> List[Dict[str, Any]]:
        return list(reversed(self._runs))[:per_page]

    def get_workflow_run(self, run_id: int) -> Dict[str, Any]:
        for run in self._runs:
            if run["id"] == run_id:
                return dict(run)
        raise SyncError("E_NOT_FOUND", "khong co run gia %d" % run_id)

    def list_run_artifacts(self, run_id: int) -> List[Dict[str, Any]]:
        self.get_workflow_run(run_id)  # 404 neu khong co run
        return [{"id": 1, "name": "failset.json", "size_in_bytes": 0,
                 "expired": False, "fake": True}]

    def fake_failset(self) -> Dict[str, Any]:
        """Fail-set gia qua parser THAT failset_from_junitxml (0 fail)."""
        xml = (b'<?xml version="1.0" ?><testsuites><testsuite name="sim" '
               b'tests="3" failures="0" errors="0" skipped="0">'
               b'<testcase classname="sim" name="t1"/>'
               b'<testcase classname="sim" name="t2"/>'
               b'<testcase classname="sim" name="t3"/>'
               b'</testsuite></testsuites>')
        return failset_from_junitxml(xml)


# ------------------------------------------------------------------ probe gia
class SimulatedProbe:
    """Probe gia theo lich trinh: `.status` do kich ban dat truoc moi tick.

    Giong SeqProbe cua test_controller.py nhung dieu khien bang thuoc
    tinh (kich ban doi status giua cac tick — vd song bat lai trong
    RECOVERY). Dem so lan goi de doi chieu so probe that su chay.
    """

    def __init__(self, status: str = WAVE_OFF):
        self.status = status
        self.calls = 0

    def __call__(self) -> ProbeResult:
        self.calls += 1
        return ProbeResult(self.status)


# ------------------------------------------------------------------ chay kich ban
class _RecoveryRunner:
    """Doi tuong co .run() cho Controller: moi lan goi tao Recovery moi.

    Lan chay sau (vd song bat lai trong RECOVERY) khong ap lai patch da
    ap — truyen patch=None, de Recovery dong bo qua download tu sync
    (fake sync da co ban moi cua file). Ghi lai moi bao cao de kich ban
    doi chieu.
    """

    def __init__(self, root, *, probe, sync, branch, agent_id, checks,
                 good_copies, checkpoint, snapshot, patch):
        self.root = Path(root)
        self.probe = probe
        self.sync = sync
        self.branch = branch
        self.agent_id = agent_id
        self.checks = checks
        self.good_copies = dict(good_copies)
        self.checkpoint = checkpoint
        self.snapshot = snapshot
        self.patch = patch
        self.reports: List[Dict[str, Any]] = []
        self.patch_applied = False
        self.calls = 0

    def run(self) -> Dict[str, Any]:
        self.calls += 1
        patch = None if self.patch_applied else self.patch
        rec = Recovery(self.root, probe=self.probe, sync=self.sync,
                       branch=self.branch, agent_id=self.agent_id,
                       checks=self.checks, interval_s=0,
                       good_copies=self.good_copies, checkpoint_path=None)
        report = rec.run(checkpoint=self.checkpoint, snapshot=self.snapshot,
                         patch=patch)
        self.reports.append(report)
        if ready_for_normal(report):
            self.patch_applied = True
        return report


def run_wave_scenario(scenario: Dict[str, Any], root, *,
                      base_commit_sha: str,
                      agent_id: str = SIM_AGENT_ID,
                      task_id: str = SIM_TASK_ID,
                      branch: str = SIM_BRANCH) -> Dict[str, Any]:
    """Chay 1 kich ban mo phong song EPERM dau-cuoi, tra ve bao cao.

    scenario: dict {name, title, wave_ticks, checks, initial_files,
    changes, truncate, conflict, expect}. conflict: None | "head_moved" |
    "remote_checkpoint" | "bad_patch" | "zero_byte_no_copy" | "wave_back".
    expect: dict cac truong doi chieu de ket luan PASS/FAIL (chi cac
    khoa co trong expect moi duoc doi chieu).

    KHONG cho that 150 giay: probe gia + interval_s=0 o moi cho; SO LAN
    probe lang (checks, mac dinh 5) moi la thu duoc kiem chung.
    """
    t0 = time.monotonic()
    root = Path(root)
    name = scenario.get("name", "mo-phong")
    checks = int(scenario.get("checks", 5))
    wave_ticks = int(scenario.get("wave_ticks", 1))
    conflict = scenario.get("conflict")
    initial = {k: bytes(v) for k, v in
               scenario.get("initial_files", DEFAULT_INITIAL_FILES).items()}
    changes = {k: (None if v is None else bytes(v)) for k, v in
               scenario.get("changes", DEFAULT_CHANGES).items()}
    truncate = list(scenario.get("truncate", DEFAULT_TRUNCATE))
    expect = dict(scenario.get("expect") or {})

    # 1. Dung workspace gia lap + snapshot + ban tot.
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in initial.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    snapshot = integrity.snapshot([root / rel for rel in initial])
    good_copies = dict(initial)
    if conflict == "zero_byte_no_copy":
        for rel in truncate:
            good_copies.pop(rel, None)

    # 2. Patch + checkpoint (truoc khi song bat).
    new_files = dict(initial)
    new_files.update(changes)
    patch = build_patch(initial, new_files, base_commit_sha,
                        test_status="simulation %s" % name)
    if conflict == "bad_patch":
        patch = dict(patch)
        patch["patch_text"] = patch["patch_text"].replace("DA SUA", "DA SUA X", 1)
        # Co y KHONG bam lai checksum -> validate se fail (fail-closed).
    cp = build_checkpoint(agent_id=agent_id, task_id=task_id, branch=branch,
                          base_commit_sha=base_commit_sha,
                          changed_files=files_for_checkpoint(patch),
                          patch_or_content="patch:%s" % patch["checksum"],
                          test_status="simulation %s" % name,
                          step="simulation", next_action="ve NORMAL sau RECOVERY")

    # 3. Sync gia (seed ban moi cua file = anh nhanh remote, de lan chay
    # RECOVERY thu hai dong bo qua download thay vi ap lai patch).
    sync = FakeGitHubSync(base_commit_sha, files={
        rel: data for rel, data in new_files.items() if data is not None})

    # 4. Controller + recovery runner.
    probe = SimulatedProbe(WAVE_ON)
    runner = _RecoveryRunner(root, probe=probe, sync=sync, branch=branch,
                             agent_id=agent_id, checks=checks,
                             good_copies=good_copies, checkpoint=cp,
                             snapshot=snapshot, patch=patch)
    controller = Controller(probe=probe, checks=checks, interval_s=0,
                            recovery_runner=runner, sync=sync,
                            agent_id=agent_id, branch=branch,
                            checkpoint=cp, patch_text=patch["patch_text"])

    # 5. Song bat (fault dang bat): phat hien -> chan ghi -> xuat -> thu ghi.
    wave_info: Dict[str, Any] = {"guard_blocked": False, "guard_code": None,
                                 "guarded_write_failed": False,
                                 "guarded_write_code": None,
                                 "guarded_no_file": False,
                                 "atomic_failed": False,
                                 "atomic_no_file": False,
                                 "atomic_no_temp_left": False,
                                 "truncated_by_wave": [],
                                 "blocked_writes": 0, "blocked_fsyncs": 0}
    with WaveFault(root) as fault:
        for _ in range(wave_ticks):
            probe.status = WAVE_ON
            controller.tick()
        try:
            controller.guard_write("simulation")
        except PB3Error as exc:
            wave_info["guard_blocked"] = True
            wave_info["guard_code"] = exc.code
        dest = root / "work.txt"
        try:
            write_bytes_guarded(dest, b"du lieu trong song")
        except PB3Error as exc:
            wave_info["guarded_write_failed"] = True
            wave_info["guarded_write_code"] = exc.code
        wave_info["guarded_no_file"] = not dest.exists()
        dest2 = root / "work2.txt"
        try:
            with AtomicWriter(dest2, expected_size=5) as w:
                w.write(b"hello")
        except PB3Error as exc:
            wave_info["atomic_failed"] = exc.code == "E_WRITE_WAVE"
        wave_info["atomic_no_file"] = not dest2.exists()
        wave_info["atomic_no_temp_left"] = not any(
            p.name.startswith(".work2.txt.pb3part-") for p in root.iterdir())
        for rel in truncate:
            size_after = truncate_by_failed_write(root / rel, b"du lieu se mat")
            if size_after == 0:
                wave_info["truncated_by_wave"].append(rel)
        wave_info["blocked_writes"] = fault.blocked_writes
        wave_info["blocked_fsyncs"] = fault.blocked_fsyncs
        # Kiem tra qua Actions (GIA LAP — khong mang that).
        sync.dispatch_workflow(SIM_WORKFLOW, ref=branch,
                               inputs={"ref": branch, "scope": "unit"})
        runs = sync.list_workflow_runs(SIM_WORKFLOW)
        actions = {"workflow": SIM_WORKFLOW, "dispatched": True,
                   "run_id": runs[0]["id"] if runs else None,
                   "conclusion": runs[0]["conclusion"] if runs else None,
                   "artifacts": [a["name"] for a in
                                 sync.list_run_artifacts(runs[0]["id"])] if runs else [],
                   "failset": sync.fake_failset(),
                   "fake": True}

    # 6. Gay xung dot (sau khi song tat, truoc khi RECOVERY chay).
    if conflict == "head_moved":
        sync.move_head(branch, "f" * 40)
    elif conflict == "remote_checkpoint":
        other = build_checkpoint(
            agent_id=agent_id, task_id=task_id, branch=branch,
            base_commit_sha=base_commit_sha,
            changed_files=[{"path": "notes/a.txt", "sha256": "0" * 64,
                            "bytes": 999}],
            patch_or_content="patch:khac", test_status="agent khac")
        sync.save_checkpoint(agent_id, other, branch)

    # 7. Song tat: du `checks` lan probe lang -> RECOVERY (chay 12 buoc).
    probe.status = WAVE_OFF
    for _ in range(checks):
        controller.tick()

    # 8. Song bat lai trong RECOVERY (chi kich ban wave_back): ve WAVE,
    # roi lang lai -> RECOVERY lan hai.
    if conflict == "wave_back":
        probe.status = WAVE_ON
        controller.tick()
        probe.status = WAVE_OFF
        for _ in range(checks):
            controller.tick()

    # 9. Tick cuoi: ve NORMAL neu RECOVERY dat (giu RECOVERY neu xung dot).
    controller.tick()

    # 10. Toan ven sau khoi phuc.
    expected_final = dict(initial)
    for rel, data in changes.items():
        if data is None:
            expected_final.pop(rel, None)
        else:
            expected_final[rel] = data
    snap_expected = {
        str(root / rel): {"path": str(root / rel),
                          "sha256": integrity.sha256_bytes(data),
                          "bytes": len(data), "exists": True}
        for rel, data in expected_final.items()}
    verify_results = integrity.verify_snapshot(snap_expected)
    bad = [r for r in verify_results if r["status"] != "ok"]
    deleted_ok = all(not (root / rel).exists()
                     for rel, data in changes.items() if data is None)
    integrity_ok = (not bad) and deleted_ok

    # 11. Ket luan PASS/FAIL theo expect.
    last_report = runner.reports[-1] if runner.reports else None
    actual = {
        "final_state": controller.state,
        "needs_master": controller.needs_master,
        "recovery_status": (last_report or {}).get("status"),
        "recovery_runs": len(runner.reports),
        "integrity_ok": integrity_ok,
    }
    verdict = "PASS"
    reasons: List[str] = []
    for key, want in expect.items():
        got = actual.get(key)
        if got != want:
            verdict = "FAIL"
            reasons.append("%s: muon %r, duoc %r" % (key, want, got))
    # Luon kiem tra cac bat bien cung (khong phu thuoc expect).
    if not wave_info["guard_blocked"] or wave_info["guard_code"] != "E_WRITE_WAVE":
        verdict = "FAIL"
        reasons.append("guard_write khong chan ghi bang E_WRITE_WAVE trong song")
    if not wave_info["guarded_write_failed"] or not wave_info["guarded_no_file"]:
        verdict = "FAIL"
        reasons.append("ghi co bao ve trong song khong fail-closed / de lai file")
    if not wave_info["atomic_failed"] or not wave_info["atomic_no_file"]:
        verdict = "FAIL"
        reasons.append("AtomicWriter trong song khong fail-closed / de lai file")
    if sorted(wave_info["truncated_by_wave"]) != sorted(truncate):
        verdict = "FAIL"
        reasons.append("file 0 byte do song: muon %r, duoc %r"
                       % (truncate, wave_info["truncated_by_wave"]))
    if not sync.saved_checkpoints or not sync.saved_patches:
        verdict = "FAIL"
        reasons.append("checkpoint/patch khong xuat duoc qua kenh doc lap")

    transitions = [{"from": t["from"], "to": t["to"], "reason": t["reason"]}
                   for t in controller.transitions]
    report = {
        "scenario": name,
        "title": scenario.get("title", name),
        "fake": True,
        "verdict": verdict,
        "reasons": reasons,
        "final_state": controller.state,
        "needs_master": controller.needs_master,
        "transitions": transitions,
        "probe_calls": probe.calls,
        "wave": wave_info,
        "export": {"checkpoint": bool(sync.saved_checkpoints),
                   "patch": bool(sync.saved_patches)},
        "actions": actions,
        "recovery": last_report,
        "recovery_runs": len(runner.reports),
        "integrity": {"checked": len(verify_results),
                      "ok": len(verify_results) - len(bad),
                      "bad": [r["path"] for r in bad],
                      "integrity_ok": integrity_ok},
        "duration_s": round(time.monotonic() - t0, 3),
    }
    return report


# ------------------------------------------------------------------ kich ban co san
BUILTIN_SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "happy-path",
        "title": "Song bat -> chan ghi + xuat checkpoint/patch -> song tat -> "
                 "RECOVERY 12 buoc -> NORMAL",
        "wave_ticks": 1,
        "expect": {"final_state": "NORMAL", "needs_master": False,
                   "recovery_status": "ok", "recovery_runs": 1,
                   "integrity_ok": True},
    },
    {
        "name": "head-moved",
        "title": "Nhanh dich trong luc song (head SHA doi) -> RECOVERY dung "
                 "o buoc 5, bao MASTER",
        "conflict": "head_moved",
        "expect": {"final_state": "RECOVERY", "needs_master": True,
                   "recovery_status": "conflict", "recovery_runs": 1},
    },
    {
        "name": "remote-checkpoint-conflict",
        "title": "Checkpoint remote lech (cung file, sha256 khac) -> RECOVERY "
                 "dung o buoc 6, bao MASTER",
        "conflict": "remote_checkpoint",
        "expect": {"final_state": "RECOVERY", "needs_master": True,
                   "recovery_status": "conflict", "recovery_runs": 1},
    },
    {
        "name": "bad-patch",
        "title": "Patch hong checksum -> RECOVERY dung o buoc 9 (E_CORRUPT), "
                 "bao MASTER",
        "conflict": "bad_patch",
        "expect": {"final_state": "RECOVERY", "needs_master": True,
                   "recovery_status": "failed", "recovery_runs": 1},
    },
    {
        "name": "zero-byte-no-copy",
        "title": "File 0 byte khong co ban tot -> RECOVERY dung o buoc 8, "
                 "bao MASTER",
        "conflict": "zero_byte_no_copy",
        "expect": {"final_state": "RECOVERY", "needs_master": True,
                   "recovery_status": "failed", "recovery_runs": 1},
    },
    {
        "name": "wave-back",
        "title": "Song bat lai trong RECOVERY -> ve WAVE -> lang lai -> "
                 "RECOVERY lan hai -> NORMAL",
        "conflict": "wave_back",
        "expect": {"final_state": "NORMAL", "needs_master": False,
                   "recovery_status": "ok", "recovery_runs": 2,
                   "integrity_ok": True},
    },
    {
        "name": "delete-file",
        "title": "Patch xoa 1 file + sua 1 file + them 1 file -> RECOVERY "
                 "ap dung dung, file xoa bien mat",
        "changes": {"notes/a.txt": b"dong 1\ndong 2 DA SUA\ndong 3\ndong 4 moi\n",
                    "notes/b.txt": None,
                    "notes/d.txt": b"file moi trong patch\n"},
        "expect": {"final_state": "NORMAL", "needs_master": False,
                   "recovery_status": "ok", "recovery_runs": 1,
                   "integrity_ok": True},
    },
]


def run_all_scenarios(root, *, base_commit_sha: str,
                      **kw) -> List[Dict[str, Any]]:
    """Chay toan bo kich ban co san, moi kich ban 1 thu muc con rieng.

    Tra ve list bao cao theo thu tu BUILTIN_SCENARIOS.
    """
    root = Path(root)
    reports: List[Dict[str, Any]] = []
    for scenario in BUILTIN_SCENARIOS:
        reports.append(run_wave_scenario(
            scenario, root / scenario["name"],
            base_commit_sha=base_commit_sha, **kw))
    return reports


# ------------------------------------------------------------------ tom tat
def simulation_summary(report: Dict[str, Any]) -> str:
    """Tom tat tieng Viet 1 bao cao mo phong (de agent/MASTER doc nhanh)."""
    lines = ["Mô phỏng sóng EPERM: %s — %s." % (
        report.get("scenario"), report.get("verdict"))]
    chain = [t["from"] for t in report.get("transitions", [])]
    if report.get("transitions"):
        chain.append(report["transitions"][-1]["to"])
    lines.append("  Trạng thái cuối: %s (chuyển: %s)."
                 % (report.get("final_state"), "→".join(chain) or "—"))
    wave = report.get("wave", {})
    lines.append("  Sóng: ghi bị chặn %s (%d write + %d fsync bị chặn); "
                 "file 0 byte do sóng: %s."
                 % (wave.get("guard_code"), wave.get("blocked_writes", 0),
                    wave.get("blocked_fsyncs", 0),
                    ", ".join(wave.get("truncated_by_wave", [])) or "không có"))
    lines.append("  Xuất checkpoint/patch qua kênh độc lập: %s."
                 % ("có (giả lập)" if report.get("export", {}).get("checkpoint")
                    and report.get("export", {}).get("patch") else "KHÔNG"))
    actions = report.get("actions", {})
    lines.append("  Kiểm tra qua Actions (giả lập): %s run #%s %s."
                 % (actions.get("workflow"), actions.get("run_id"),
                    actions.get("conclusion")))
    rec = report.get("recovery") or {}
    steps = rec.get("steps", [])
    ok_steps = sum(1 for s in steps if s.get("status") == "ok")
    lines.append("  RECOVERY: %s (%d/%d bước ok, chạy %d lần)."
                 % (rec.get("status"), ok_steps, len(steps),
                    report.get("recovery_runs", 0)))
    integ = report.get("integrity", {})
    lines.append("  Toàn vẹn sau khôi phục: %d/%d file khớp."
                 % (integ.get("ok", 0), integ.get("checked", 0)))
    if report.get("needs_master"):
        lines.append("  CẦN BÁO MASTER (xung đột / khôi phục chưa đạt).")
    if report.get("reasons"):
        lines.append("  Lý do FAIL: %s" % "; ".join(report["reasons"]))
    lines.append("  [GIẢ LẬP — probe giả, sync giả, fault-injection; không "
                 "mạng thật, không lỗi thật; %.3f giây]" % report.get("duration_s", 0))
    return "\n".join(lines)


def simulation_report_summary(reports: List[Dict[str, Any]]) -> str:
    """Tom tat tieng Viet cho ca dot mo phong (nhieu kich ban)."""
    passed = sum(1 for r in reports if r.get("verdict") == "PASS")
    lines = ["Mô phỏng sóng EPERM: %d/%d kịch bản PASS." % (passed, len(reports))]
    for r in reports:
        lines.append("  - %-26s %s (cuối: %s, RECOVERY: %s, %d lần)"
                     % (r.get("scenario"), r.get("verdict"),
                        r.get("final_state"),
                        (r.get("recovery") or {}).get("status"),
                        r.get("recovery_runs", 0)))
    return "\n".join(lines)
