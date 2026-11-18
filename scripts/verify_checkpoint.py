#!/usr/bin/env python3
"""Verify checkpoint Anti-Wave (JSON v1, 10 truong) — dung tay hoac trong CI.

3 che do (chi dung 1 moi lan):
  * file local:  python3 scripts/verify_checkpoint.py anti_wave/checkpoints/agent-1.json
  * ca thu muc:  python3 scripts/verify_checkpoint.py --dir anti_wave/checkpoints
                 (thu muc chua co -> OK, in "chua co checkpoint")
  * tren GitHub: python3 scripts/verify_checkpoint.py --agent agent-1 --branch feat/x
                 (can token trong bien moi truong, xem anti_wave/github_sync.py)

Verify = du 9 truong bat buoc + schema_version v1 + checksum khop
(checkpoint.py neu import duoc, neu khong dung github_sync.validate_checkpoint).
Checkpoint sai -> exit 1, khong bao gio dung de tiep tuc (fail-closed).
Python 3.9 tuong thich, chi stdlib.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_and_validate(data: bytes, where: str):
    """Tra ve checkpoint dict da validate; sai -> raise ValueError/SyncError."""
    try:
        from anti_wave.checkpoint import loads_checkpoint
        return loads_checkpoint(data, where=where)
    except ImportError:
        pass
    from anti_wave.github_sync import validate_checkpoint
    payload = json.loads(data.decode("utf-8"))
    return validate_checkpoint(payload)


def _summary(checkpoint) -> str:
    try:
        from anti_wave.checkpoint import resume_summary
        return resume_summary(checkpoint)
    except ImportError:
        pass
    files = checkpoint.get("changed_files") or []
    lines = [
        "Checkpoint %s / %s" % (checkpoint.get("agent_id"), checkpoint.get("task_id")),
        "Nhanh: %s (base %s)" % (checkpoint.get("branch"),
                                 (checkpoint.get("base_commit_sha") or "")[:12]),
        "Trang thai test: %s" % checkpoint.get("test_status"),
        "Tao luc: %s" % checkpoint.get("created_at"),
        "File da doi (%d):" % len(files),
    ]
    for entry in files:
        lines.append("  - %s (%s byte)" % (entry.get("path"), entry.get("bytes")))
    if checkpoint.get("current_commit_sha"):
        lines.append("Commit hien tai: %s" % checkpoint["current_commit_sha"][:12])
    if checkpoint.get("next_action"):
        lines.append("Viec tiep theo: %s" % checkpoint["next_action"])
    return "\n".join(lines)


def _verify_file(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print("FAIL %s: khong doc duoc: %s" % (path, exc))
        return 1
    try:
        checkpoint = _load_and_validate(data, str(path))
    except Exception as exc:  # noqa: BLE001 — PB3Error / SyncError / JSON loi
        print("FAIL %s: %s" % (path, exc))
        return 1
    print("OK %s" % path)
    print(_summary(checkpoint))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=None,
                        help="file checkpoint local can verify")
    parser.add_argument("--dir", dest="directory", default=None,
                        help="verify tat ca *.json trong thu muc nay")
    parser.add_argument("--agent", default=None,
                        help="agent_id can tai checkpoint tu GitHub")
    parser.add_argument("--branch", default=None,
                        help="nhanh chua checkpoint (dung voi --agent)")
    parser.add_argument("--repo", default="MunKai0408/MeoRocky")
    parser.add_argument("--token-env", default="GITHUB_BEAR_TOKEN")
    args = parser.parse_args(argv)

    modes = sum([args.path is not None, args.directory is not None,
                 args.agent is not None])
    if modes != 1:
        parser.error("chi dung 1 trong 3 che do: <file> | --dir | --agent")
    if args.agent is not None and not args.branch:
        parser.error("--agent can kem --branch")

    if args.path is not None:
        return _verify_file(Path(args.path))

    if args.directory is not None:
        directory = Path(args.directory)
        if not directory.is_dir():
            print("chua co checkpoint (%s khong ton tai) — OK." % directory)
            return 0
        files = sorted(directory.glob("*.json"))
        if not files:
            print("chua co checkpoint trong %s — OK." % directory)
            return 0
        bad = 0
        for item in files:
            bad += _verify_file(item)
        print("verify %d checkpoint: %d loi" % (len(files), bad))
        return 1 if bad else 0

    # Remote mode.
    from anti_wave.github_sync import GitHubSync, SyncError
    try:
        sync = GitHubSync(repo=args.repo, token_env=args.token_env)
        checkpoint = sync.load_checkpoint(args.agent, args.branch)
    except SyncError as exc:
        print("FAIL agent %s tren %s: [%s] %s"
              % (args.agent, args.branch, exc.code, exc))
        if exc.hint:
            print("Goi y: %s" % exc.hint)
        return 1
    print("OK agent %s tren %s" % (args.agent, args.branch))
    print(_summary(checkpoint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
