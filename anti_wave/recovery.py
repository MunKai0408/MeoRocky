"""Quy trinh RECOVERY 12 buoc khi song EPERM tat (Anti-Wave, Phase 4).

Chay SAU khi song tat (controller o trang thai RECOVERY), TRUOC khi ve
NORMAL. Quy tac bat buoc (spec MASTER v1.0/v1.1):
  * KHONG ve NORMAL ngay sau 1 lan ghi thanh cong — phai probe ghi
    thanh cong 5 lan cach nhau 30 giay (buoc 1). Day la heuristic khoi
    dau, KHONG phai bang chung on dinh lau dai: nguoi goi van phai probe
    lai truoc moi loat ghi lon.
  * Kiem tra toan ven (sha256, phat hien file 0 byte / bi cat).
  * Doi chieu branch/commit SHA/checkpoint/patch; chi dong bo phan con
    thieu; co xung dot -> DUNG va bao MASTER (khong tu giai quyet).

Tai dung, khong tao lai:
  * healthcheck.probe_wave / WaveState.wait_for_calm — probe + heuristic.
  * integrity.verify_snapshot / scan_for_zero_bytes — toan ven dia.
  * checkpoint.load / build_checkpoint / save — checkpoint khoi phuc.
  * patch_manager.apply_patch — ap patch dong bo (buoc 9).

Thiet ke de test: moi buoc la phuong thuc rieng, cong tac vien (probe,
sync, good_copies) tiem duoc qua constructor — test dung fake, khong
can mang that hay gay loi that. run() khong raise vi buoc loi/xung dot
ma ghi vao bao cao va dung lai (fail-fast); chi raise ValueError cho
tham so sai.

Chi stdlib, Python 3.9 tuong thich.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from anti_wave import checkpoint as checkpoint_mod
from anti_wave import patch_manager
from anti_wave.healthcheck import ProbeResult, WaveState, probe_wave, WAVE_OFF, WAVE_ON
from anti_wave.integrity import file_record, scan_for_zero_bytes, verify_snapshot
from core.errors import PB3Error
from core.safe_io import write_bytes_guarded

# 12 buoc (ten viet tat dung trong bao cao + recovery_summary).
STEP_NAMES = (
    "xac-nhan-song-tat",
    "quet-file-0-byte",
    "kiem-tra-checkpoint",
    "doi-chieu-snapshot",
    "kiem-tra-head-nhanh",
    "phat-hien-xung-dot",
    "lap-ke-hoach-dong-bo",
    "khoi-phuc-file-0-byte",
    "ap-patch-dong-bo",
    "kiem-tra-lai-toan-ven",
    "ghi-checkpoint-khoi-phuc",
    "probe-cuoi",
)

STEP_OK = "ok"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"
STEP_CONFLICT = "conflict"

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_CONFLICT = "conflict"

DEFAULT_CHECKS = 5
DEFAULT_INTERVAL_S = 30.0


class RecoveryConflict(Exception):
    """Xung dot phat hien trong RECOVERY: dung lai, bao MASTER.

    `conflicts`: list {path, local_sha256, remote_sha256} (hoac
    {path, expected_sha, actual_sha} cho lech head SHA).
    """

    def __init__(self, conflicts: List[Dict[str, Any]], message: str = ""):
        super().__init__(message or "xung dot trong RECOVERY: %d muc"
                         % len(conflicts))
        self.conflicts = list(conflicts)


def verify_calm(checks: int = DEFAULT_CHECKS, interval_s: float = DEFAULT_INTERVAL_S,
                probe: Optional[Callable[[], ProbeResult]] = None) -> bool:
    """True chi khi `checks` lan probe ghi thanh cong lien tiep, cach nhau
    `interval_s` giay (heuristic MASTER — xem WaveState.wait_for_calm).
    Probe loi / WAVE_ON o bat ky lan nao -> False ngay. Khong bao gio raise
    vi ly do probe (probe chet -> coi nhu chua lang)."""
    state = WaveState()
    try:
        return state.wait_for_calm(checks=checks, interval_s=interval_s,
                                   probe=probe)
    except Exception:
        return False


def check_conflicts(local_files: List[Dict[str, Any]],
                    remote_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """So sanh 2 danh sach changed_files [{path, sha256, ...}] (pure).

    Xung dot = cung path, ca 2 ben deu co sha256 va khac nhau (ca 2 ben
    deu doi file do tu cung base). Tra ve list {path, local_sha256,
    remote_sha256}. File chi 1 ben doi thi khong xung dot.
    """
    remote = {e.get("path"): e for e in remote_files if isinstance(e, dict)}
    conflicts: List[Dict[str, Any]] = []
    for entry in local_files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        other = remote.get(path)
        if other is None:
            continue
        local_sha = entry.get("sha256")
        remote_sha = other.get("sha256")
        if local_sha and remote_sha and local_sha != remote_sha:
            conflicts.append({"path": path, "local_sha256": local_sha,
                              "remote_sha256": remote_sha})
    return conflicts


def recovery_summary(report: Dict[str, Any]) -> str:
    """Tom tat tieng Viet bao cao RECOVERY (de agent/MASTER doc nhanh)."""
    lines = ["RECOVERY: %s (%s)." % (
        {"ok": "ĐẠT — đủ điều kiện về NORMAL",
         "failed": "CHƯA ĐẠT — dừng ở bước lỗi, chưa về NORMAL",
         "conflict": "XUNG ĐỘT — dừng lại, báo MASTER, chưa về NORMAL"}.get(
             report.get("status"), report.get("status")),
        "đủ điều kiện về NORMAL" if report.get("ready_for_normal")
        else "chưa đủ điều kiện về NORMAL")]
    for step in report.get("steps", []):
        mark = {"ok": "ok", "failed": "LỖI", "skipped": "bỏ qua",
                "conflict": "XUNG ĐỘT"}.get(step.get("status"), step.get("status"))
        lines.append("  %2d. %-26s [%s] %s" % (step.get("step"), step.get("name"),
                                              mark, step.get("detail", "")))
    if report.get("conflicts"):
        lines.append("Xung đột (%d):" % len(report["conflicts"]))
        for c in report["conflicts"]:
            lines.append("  - %s" % c.get("path"))
    if report.get("unrecoverable"):
        lines.append("Không khôi phục được (%d):" % len(report["unrecoverable"]))
        for path in report["unrecoverable"]:
            lines.append("  - %s" % path)
    if report.get("restored"):
        lines.append("Đã khôi phục (%d): %s" % (
            len(report["restored"]), ", ".join(report["restored"])))
    return "\n".join(lines)


def ready_for_normal(report: Dict[str, Any]) -> bool:
    """True chi khi bao cao status ok (moi buoc bat buoc pass; buoc tuy
    chon bi bo qua vi thieu sync/checkpoint/snapshot van duoc ghi ro
    trong bao cao de nguoi doc thay)."""
    return bool(report.get("status") == STATUS_OK and report.get("ready_for_normal"))


class Recovery:
    """Quy trinh RECOVERY 12 buoc cho 1 workspace (root).

    probe: ham tra ProbeResult (mac dinh probe_wave). sync: doi tuong
    kieu GitHubSync (get_branch_head / get_file / load_checkpoint) hoac
    None (= lam viec offline: buoc can mang se bo qua co ghi chu).
    good_copies: {rel_path: bytes} — ban tot de khoi phuc file 0 byte /
    bi cat (lay tu GitHub/patch truoc khi chay). checkpoint_path: file
    checkpoint cua agent can verify (None = bo qua buoc 3).
    """

    def __init__(self, root, *, probe: Optional[Callable[[], ProbeResult]] = None,
                 sync=None, branch: Optional[str] = None,
                 agent_id: Optional[str] = None,
                 checks: int = DEFAULT_CHECKS, interval_s: float = DEFAULT_INTERVAL_S,
                 good_copies: Optional[Dict[str, bytes]] = None,
                 checkpoint_path=None, recovery_dir=None):
        self.root = Path(root)
        self._probe = probe if probe is not None else (lambda: probe_wave())
        self.sync = sync
        self.branch = branch
        self.agent_id = agent_id
        if checks < 1:
            raise ValueError("checks phai >= 1")
        self.checks = checks
        self.interval_s = interval_s
        self.good_copies = dict(good_copies or {})
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.recovery_dir = (Path(recovery_dir) if recovery_dir
                             else self.root / "anti_wave" / "checkpoints")

    # ------------------------------------------------------------- chay
    def run(self, *, checkpoint: Optional[Dict[str, Any]] = None,
            expected_base_sha: Optional[str] = None,
            snapshot: Optional[Dict[str, Any]] = None,
            patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Chay 12 buoc theo thu tu; buoc loi/xung dot -> dung ngay, cac
        buoc sau ghi "bo qua (dung o buoc N)".

        checkpoint: dict checkpoint da load (uu tien hon checkpoint_path).
        expected_base_sha: mac dinh lay tu checkpoint["base_commit_sha"].
        snapshot: {path: record} ky vong (khoa la duong dan tuyet doi
        hoac tuong doi so voi root). patch: envelope patch_manager de ap
        o buoc 9 (None = bo qua buoc 9).
        """
        started = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        ctx: Dict[str, Any] = {
            "checkpoint": checkpoint,
            "expected_base_sha": expected_base_sha
                or (checkpoint or {}).get("base_commit_sha"),
            "snapshot": self._normalize_snapshot(snapshot or {}),
            "patch": patch,
            "zero_byte_files": [],
            "mismatches": [],
            "restored": [],
            "unrecoverable": [],
            "plan": None,
        }
        steps: List[Dict[str, Any]] = []
        status = STATUS_OK
        for idx, name in enumerate(STEP_NAMES, start=1):
            fn = getattr(self, "_step_%d_%s" % (idx, name.replace("-", "_")))
            try:
                result = fn(ctx)
            except RecoveryConflict as exc:
                result = {"status": STEP_CONFLICT,
                          "detail": str(exc) or "xung dot (xem conflicts)"}
                ctx.setdefault("conflicts", []).extend(exc.conflicts)
                status = STATUS_CONFLICT
            except PB3Error as exc:
                result = {"status": STEP_FAILED,
                          "detail": "%s: %s" % (exc.code, exc)}
                status = STATUS_FAILED
            except Exception as exc:  # noqa: BLE001 - buoc loi la thi ghi lai, khong chet
                result = {"status": STEP_FAILED,
                          "detail": "%s: %s" % (type(exc).__name__, exc)}
                status = STATUS_FAILED
            steps.append({"step": idx, "name": name,
                          "status": result["status"], "detail": result.get("detail", "")})
            if result["status"] in (STEP_FAILED, STEP_CONFLICT):
                for j in range(idx + 1, len(STEP_NAMES) + 1):
                    steps.append({"step": j, "name": STEP_NAMES[j - 1],
                                  "status": STEP_SKIPPED,
                                  "detail": "bỏ qua (dừng ở bước %d)" % idx})
                break
        finished = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        report = {
            "status": status,
            "ready_for_normal": status == STATUS_OK,
            "steps": steps,
            "conflicts": ctx.get("conflicts", []),
            "mismatches": ctx["mismatches"],
            "zero_byte_files": [str(p) for p in ctx["zero_byte_files"]],
            "restored": ctx["restored"],
            "unrecoverable": ctx["unrecoverable"],
            "plan": ctx["plan"],
            "started_at": started,
            "finished_at": finished,
        }
        return report

    # ------------------------------------------------------------- tien ich
    @staticmethod
    def _rel(path) -> str:
        """Duong dan (tuyet doi hoac tuong doi) -> rel so voi root (dang str)."""
        return str(path)

    def _normalize_snapshot(self, snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Chuan hoa snapshot ve khoa rel-path so voi root."""
        out: Dict[str, Dict[str, Any]] = {}
        for key, rec in snapshot.items():
            if not isinstance(rec, dict):
                continue
            p = Path(str(key))
            if p.is_absolute():
                try:
                    rel = str(p.relative_to(self.root.resolve()))
                except ValueError:
                    rel = str(p)
            else:
                rel = str(p)
            out[rel] = dict(rec, path=rel)
        return out

    def _local_changed_files(self, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Danh sach file dia dang lech so voi snapshot (de so xung dot)."""
        out: List[Dict[str, Any]] = []
        for rel, mismatch in ctx["mismatches"]:
            actual = file_record(self.root / rel)
            out.append({"path": rel, "sha256": actual.get("sha256"),
                        "bytes": actual.get("bytes")})
        cp = ctx.get("checkpoint") or {}
        for entry in cp.get("changed_files", []):
            if isinstance(entry, dict) and entry.get("path") not in {e["path"] for e in out}:
                out.append(entry)
        return out

    # ------------------------------------------------------------- 12 buoc
    def _step_1_xac_nhan_song_tat(self, ctx) -> Dict[str, Any]:
        ok = verify_calm(checks=self.checks, interval_s=self.interval_s,
                         probe=self._probe)
        if not ok:
            raise PB3Error("E_WRITE_WAVE",
                           "chua du %d lan probe ghi thanh cong cach nhau %.0fs; giu WAVE"
                           % (self.checks, self.interval_s),
                           where="anti_wave.recovery")
        return {"status": STEP_OK,
                "detail": "%d lan probe ghi thanh cong cach nhau %.0fs (heuristic — "
                          "chua phai bang chung on dinh lau dai)"
                          % (self.checks, self.interval_s)}

    def _step_2_quet_file_0_byte(self, ctx) -> Dict[str, Any]:
        found = scan_for_zero_bytes(self.root)
        ctx["zero_byte_files"] = found
        if not found:
            return {"status": STEP_OK, "detail": "khong co file 0 byte"}
        return {"status": STEP_OK,
                "detail": "%d file 0 byte — khoi phuc o buoc 8" % len(found)}

    def _step_3_kiem_tra_checkpoint(self, ctx) -> Dict[str, Any]:
        if ctx.get("checkpoint") is not None:
            try:
                data = json.dumps(ctx["checkpoint"]).encode("utf-8")
                checkpoint_mod.loads_checkpoint(data, where="<run checkpoint>")
            except PB3Error as exc:
                raise PB3Error("E_CORRUPT", "checkpoint truyen vao hong: %s" % exc,
                               where="anti_wave.recovery")
            return {"status": STEP_OK, "detail": "checkpoint truyen vao hop le"}
        if self.checkpoint_path is None:
            return {"status": STEP_SKIPPED, "detail": "khong co checkpoint de kiem tra"}
        try:
            loaded = checkpoint_mod.load(self.checkpoint_path)
        except PB3Error as exc:
            raise PB3Error("E_CORRUPT", "checkpoint %s hong: %s"
                           % (self.checkpoint_path, exc),
                           where="anti_wave.recovery")
        ctx["checkpoint"] = loaded
        if ctx.get("expected_base_sha") is None:
            ctx["expected_base_sha"] = loaded.get("base_commit_sha")
        return {"status": STEP_OK,
                "detail": "checkpoint %s hop le" % self.checkpoint_path}

    def _step_4_doi_chieu_snapshot(self, ctx) -> Dict[str, Any]:
        if not ctx["snapshot"]:
            return {"status": STEP_SKIPPED, "detail": "khong co snapshot de doi chieu"}
        snap = {str(self.root / rel): rec for rel, rec in ctx["snapshot"].items()}
        results = verify_snapshot(snap)
        bad = [(r["path"], r["status"]) for r in results if r["status"] != "ok"]
        ctx["mismatches"] = [(str(Path(p).relative_to(self.root.resolve()))
                              if Path(p).is_absolute() else p, s) for p, s in bad]
        if not bad:
            return {"status": STEP_OK,
                    "detail": "%d file doi chieu, deu khop" % len(results)}
        detail = ", ".join("%s (%s)" % (Path(p).name, s) for p, s in bad[:5])
        if len(bad) > 5:
            detail += " (+%d)" % (len(bad) - 5)
        return {"status": STEP_OK,
                "detail": "%d/%d file lech: %s — xu ly o buoc 8/9"
                % (len(bad), len(results), detail)}

    def _step_5_kiem_tra_head_nhanh(self, ctx) -> Dict[str, Any]:
        if self.sync is None or not self.branch:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co sync/branch de kiem tra head"}
        expected = ctx.get("expected_base_sha")
        if not expected:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co base_commit_sha ky vong de doi chieu"}
        head = self.sync.get_branch_head(self.branch)
        if head != expected:
            raise RecoveryConflict(
                [{"path": "<branch %s>" % self.branch,
                  "expected_sha": expected, "actual_sha": head}],
                "nhanh %s da dich (%s != base %s) — dung lai, bao MASTER"
                % (self.branch, head[:12], expected[:12]))
        return {"status": STEP_OK,
                "detail": "head nhanh %s khop base %s" % (self.branch, expected[:12])}

    def _step_6_phat_hien_xung_dot(self, ctx) -> Dict[str, Any]:
        if self.sync is None or not self.branch or not self.agent_id:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co sync/branch/agent de doi chieu 2 phia"}
        try:
            remote_cp = self.sync.load_checkpoint(self.agent_id, self.branch)
        except Exception as exc:  # noqa: BLE001 - khong co checkpoint remote = khong co du lieu de so
            return {"status": STEP_SKIPPED,
                    "detail": "khong doc duoc checkpoint remote (%s) — bo qua" % exc}
        local = self._local_changed_files(ctx)
        remote = remote_cp.get("changed_files", [])
        conflicts = check_conflicts(local, remote)
        if conflicts:
            raise RecoveryConflict(conflicts, "xung dot %d file (doi ca 2 phia)"
                                   % len(conflicts))
        return {"status": STEP_OK, "detail": "khong co xung dot"}

    def _step_7_lap_ke_hoach_dong_bo(self, ctx) -> Dict[str, Any]:
        patch = ctx.get("patch") or {}
        patch_files = {e.get("path") for e in patch.get("files", [])
                       if isinstance(e, dict)}
        to_restore = [str(p.relative_to(self.root.resolve()))
                      for p in ctx["zero_byte_files"]]
        to_download = [rel for rel, _s in ctx["mismatches"]
                       if rel not in to_restore and rel not in patch_files]
        to_upload: List[str] = []
        plan = {"to_restore": sorted(to_restore),
                "to_download": sorted(to_download),
                "to_upload": to_upload,
                "to_apply_patch": sorted(patch_files)}
        ctx["plan"] = plan
        total = len(to_restore) + len(to_download) + len(patch_files)
        return {"status": STEP_OK,
                "detail": "can dong bo %d muc (khoi phuc %d, tai %d, ap patch %d)"
                % (total, len(to_restore), len(to_download), len(patch_files))}

    def _step_8_khoi_phuc_file_0_byte(self, ctx) -> Dict[str, Any]:
        missing_copy = []
        for path in ctx["zero_byte_files"]:
            rel = str(path.relative_to(self.root.resolve())) \
                if path.is_absolute() else str(path)
            data = self.good_copies.get(rel)
            if data is None:
                missing_copy.append(rel)
                continue
            write_bytes_guarded(path, data)
            ctx["restored"].append(rel)
        if missing_copy:
            ctx["unrecoverable"] = missing_copy
            raise PB3Error("E_CORRUPT",
                           "%d file 0 byte khong co ban tot de khoi phuc: %s"
                           % (len(missing_copy), ", ".join(missing_copy[:5])),
                           where="anti_wave.recovery")
        if not ctx["zero_byte_files"]:
            return {"status": STEP_OK, "detail": "khong co file 0 byte can khoi phuc"}
        return {"status": STEP_OK,
                "detail": "da khoi phuc %d file 0 byte" % len(ctx["restored"])}

    def _step_9_ap_patch_dong_bo(self, ctx) -> Dict[str, Any]:
        patch = ctx.get("patch")
        downloaded: List[str] = []
        if patch:
            result = patch_manager.apply_patch(patch, self.root)
            meta = {e.get("path"): e for e in patch.get("files", [])
                    if isinstance(e, dict)}
            for entry in result["applied"]:
                p = entry["path"]
                if entry["action"] == "deleted":
                    ctx["snapshot"].pop(p, None)
                else:
                    m = meta.get(p, {})
                    ctx["snapshot"][p] = {"path": p, "sha256": m.get("sha256"),
                                          "bytes": entry["bytes"], "exists": True}
                downloaded.append(p)
        elif self.sync is not None and self.branch:
            for rel in (ctx["plan"] or {}).get("to_download", []):
                found = self.sync.get_file(self.branch, rel)
                if found is None:
                    raise PB3Error("E_CORRUPT",
                                   "khong tai duoc %s tu nhanh %s" % (rel, self.branch),
                                   where="anti_wave.recovery")
                write_bytes_guarded(self.root / rel, found[1])
                ctx["snapshot"][rel] = file_record(self.root / rel)
                downloaded.append(rel)
        else:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co patch va khong co sync de dong bo"}
        if not downloaded and not patch:
            return {"status": STEP_OK, "detail": "khong co gi can dong bo"}
        return {"status": STEP_OK,
                "detail": "da dong bo %d file" % len(downloaded)}

    def _step_10_kiem_tra_lai_toan_ven(self, ctx) -> Dict[str, Any]:
        if not ctx["snapshot"]:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co snapshot de kiem tra lai"}
        snap = {str(self.root / rel): rec for rel, rec in ctx["snapshot"].items()}
        results = verify_snapshot(snap)
        bad = [(r["path"], r["status"]) for r in results if r["status"] != "ok"]
        if bad:
            detail = ", ".join("%s (%s)" % (Path(p).name, s) for p, s in bad[:5])
            raise PB3Error("E_CORRUPT",
                           "sau dong bo van lech %d file: %s" % (len(bad), detail),
                           where="anti_wave.recovery")
        return {"status": STEP_OK,
                "detail": "%d file doi chieu, deu khop" % len(results)}

    def _step_11_ghi_checkpoint_khoi_phuc(self, ctx) -> Dict[str, Any]:
        if not self.branch or not self.agent_id:
            return {"status": STEP_SKIPPED,
                    "detail": "khong co branch/agent de ghi checkpoint khoi phuc"}
        changed = []
        for rel in ctx["restored"]:
            rec = file_record(self.root / rel)
            changed.append({"path": rel, "sha256": rec.get("sha256"),
                            "bytes": rec.get("bytes")})
        cp = checkpoint_mod.build_checkpoint(
            agent_id=self.agent_id, task_id="phase4-recovery",
            branch=self.branch,
            base_commit_sha=ctx.get("expected_base_sha") or "0" * 40,
            changed_files=changed,
            patch_or_content="recovery: khoi phuc %d file" % len(changed),
            test_status="recovery %s" % ctx.get("status", ""))
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self.recovery_dir / ("recovery-%s.json" % stamp)
        checkpoint_mod.save(dest, cp)
        return {"status": STEP_OK, "detail": "da ghi %s" % dest}

    def _step_12_probe_cuoi(self, ctx) -> Dict[str, Any]:
        try:
            res = self._probe()
        except Exception as exc:  # noqa: BLE001 - probe loi = chua lang
            raise PB3Error("E_WRITE_WAVE", "probe cuoi loi: %s" % exc,
                           where="anti_wave.recovery")
        status = res.status if isinstance(res, ProbeResult) else str(res)
        if status != WAVE_OFF:
            raise PB3Error("E_WRITE_WAVE", "probe cuoi van %s; giu RECOVERY" % status,
                           where="anti_wave.recovery")
        return {"status": STEP_OK, "detail": "probe cuoi WAVE_OFF"}
