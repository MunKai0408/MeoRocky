#!/usr/bin/env python3
"""Bao cao trang thai Anti-Wave: song EPERM + nhanh + checkpoint + Actions.

In ra tieng Viet, gom 2 phan:
  (1) LOCAL (luon chay duoc): probe song EPERM. Uu tien anti_wave.healthcheck
      neu import duoc; neu khong, dung probe toi thieu inline (os.open +
      os.write + fsync + close + doc lai — KHONG dung tempfile vi tempfile
      chet trong song EPERM [DA DO THAT]).
  (2) GITHUB (chi khi co token trong bien moi truong): head SHA cua nhanh,
      danh sach checkpoint, 3 run moi nhat cua anti-wave-ci.yml. Khong co
      token thi bo qua phan nay va ghi ro (khong phai loi).

Day la bao cao, khong phai gate: exit luon 0 tru khi probe gap loi la
(khong phai EPERM).
Python 3.9 tuong thich, chi stdlib.
"""
from __future__ import annotations

import argparse
import errno
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _probe_inline() -> str:
    """Probe toi thieu khi khong co anti_wave.healthcheck: WAVE_ON/WAVE_OFF."""
    probe_dir = os.environ.get("TMPDIR", "/tmp")
    probe_path = os.path.join(probe_dir, "awf-status-probe-%d.txt" % os.getpid())
    try:
        fd = os.open(probe_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"anti-wave-probe")
            os.fsync(fd)
        finally:
            os.close(fd)
        with open(probe_path, "rb") as handle:
            if handle.read() != b"anti-wave-probe":
                return "WAVE_UNKNOWN (doc lai khong khop)"
        return "WAVE_OFF (ghi + fsync + doc lai OK)"
    except OSError as exc:
        if exc.errno == errno.EPERM:
            return "WAVE_ON (write/fsync EPERM — song dang bat)"
        return "WAVE_UNKNOWN (loi khac: %s)" % exc
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass


def _probe_wave() -> str:
    try:
        from anti_wave.healthcheck import WAVE_ON, probe_wave
        result = probe_wave()
        return "WAVE_ON (song dang bat)" if result.status == WAVE_ON \
            else "WAVE_OFF (ghi binh thuong)"
    except ImportError:
        return _probe_inline() + " [probe inline — chua co healthcheck.py]"
    except Exception as exc:  # noqa: BLE001 — probe khong duoc phep crash bao cao
        return "WAVE_UNKNOWN (probe loi: %s)" % exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default=os.environ.get("AWF_BRANCH", "main"),
                        help="nhanh can bao cao (mac dinh: $AWF_BRANCH hoac main)")
    parser.add_argument("--repo", default="MunKai0408/MeoRocky")
    parser.add_argument("--token-env", default="GITHUB_BEAR_TOKEN")
    args = parser.parse_args(argv)

    print("=== Anti-Wave status ===")
    print("Song EPERM: %s" % _probe_wave())
    print("Nhanh: %s" % args.branch)

    token = os.environ.get(args.token_env)
    if not token:
        print("GitHub: khong co token (%s) — chi bao cao local." % args.token_env)
        print("Ghi chu: tren VM nay, ghi GitHub qua helper github_bear.py "
              "cua connector custom.github-bear.")
        return 0
    from anti_wave.github_sync import GitHubSync, SyncError
    try:
        sync = GitHubSync(repo=args.repo, token_env=args.token_env)
        head = sync.get_branch_head(args.branch)
        print("Head SHA: %s" % head)
        agents = sync.list_checkpoints(args.branch)
        if agents:
            print("Checkpoint (%d agent): %s" % (len(agents), ", ".join(agents)))
        else:
            print("Checkpoint: chua co agent nao luu tren nhanh nay.")
        runs = sync.list_workflow_runs("anti-wave-ci.yml", per_page=3)
        if runs:
            print("Run anti-wave-ci.yml moi nhat:")
            for run in runs:
                print("  - #%s %s / %s (nhanh %s, %s)"
                      % (run.get("id"), run.get("status"),
                         run.get("conclusion"), run.get("head_branch"),
                         run.get("created_at")))
        else:
            print("Run anti-wave-ci.yml: chua co (workflow moi, chua dispatch lan nao).")
    except SyncError as exc:
        print("GitHub: loi [%s] %s" % (exc.code, exc))
        if exc.hint:
            print("Goi y: %s" % exc.hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
