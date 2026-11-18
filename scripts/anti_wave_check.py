#!/usr/bin/env python3
"""Kiem tra tinh toan ven file — dung trong .github/workflows/integrity-check.yml.

Kiem tra, tren cay da checkout:
  * file 0 byte (dau hieu song EPERM cat ngang luc ghi);
  * file *.json khong parse duoc;
  * file *.yml/*.yaml khong parse duoc (neu co PyYAML; khong co thi kiem
    tra nhe: khong dung tab de thut dau dong);
  * checkpoint JSON duoi anti_wave/checkpoints/: du 9 truong bat buoc +
    checksum khop (uu tien anti_wave.checkpoint neu import duoc, neu
    khong dung anti_wave.github_sync.validate_checkpoint).

Chi doc file (tru --json ghi 1 file bao cao; ghi hong trong song EPERM thi
chi canh bao, khong lam hong ket qua kiem tra). Khong dung tempfile.
Python 3.9 tuong thich, chi stdlib (tru PyYAML tuy chon).

Dung: python3 scripts/anti_wave_check.py --root . --strict [--json bao-cao.json]
Exit: 0 = khong van de; 1 = co van de (hoac --strict ma thieu duong quet).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PATHS = ["anti_wave", "scripts", ".github/workflows", "docs"]

try:
    import yaml as _yaml
except ImportError:  # PyYAML khong phai stdlib; thieu thi kiem tra nhe.
    _yaml = None


def _validate_checkpoint_file(data: bytes, where: str):
    """Tra ve None neu checkpoint hop le, neu khong tra ve chuoi ly do."""
    try:
        from anti_wave.checkpoint import loads_checkpoint
        loads_checkpoint(data, where=where)
        return None
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — checkpoint.py raise PB3Error
        return str(exc)
    try:
        from anti_wave.github_sync import validate_checkpoint
        payload = json.loads(data.decode("utf-8"))
        validate_checkpoint(payload)
        return None
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — SyncError / JSON loi
        return str(exc)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "khong phai JSON hop le"
    required = ("agent_id", "task_id", "branch", "base_commit_sha",
                "changed_files", "patch_or_content", "test_status",
                "created_at", "checksum")
    missing = [f for f in required if f not in payload]
    if missing:
        return "thieu truong: %s" % ", ".join(missing)
    return "khong verify duoc checksum (thieu ca checkpoint.py lan github_sync.py)"


def _check_yaml_light(text: str):
    """Kiem tra nhe khi khong co PyYAML: cam tab thut dau dong."""
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("\t") or line.startswith(" \t"):
            return "dong %d dung tab de thut (YAML cam tab)" % i
    return None


def check_tree(root: Path, paths, strict: bool):
    """Quet cay; tra ve (problems, stats). problems: list[str]."""
    problems = []
    stats = {"files": 0, "zero_byte": 0, "json": 0, "yaml": 0, "checkpoint": 0}
    for rel in paths:
        base = root / rel
        if not base.exists():
            if strict:
                problems.append("thieu duong quet: %s" % rel)
            else:
                print("bo qua (khong co): %s" % rel)
            continue
        targets = [base] if base.is_file() else sorted(base.rglob("*"))
        for item in targets:
            if not item.is_file() or item.is_symlink():
                continue
            stats["files"] += 1
            try:
                data = item.read_bytes()
            except OSError as exc:
                problems.append("%s: khong doc duoc: %s" % (item, exc))
                continue
            if len(data) == 0:
                stats["zero_byte"] += 1
                problems.append("%s: file 0 byte (nghi song EPERM cat ngang)" % item)
                continue
            suffix = item.suffix.lower()
            if suffix == ".json":
                stats["json"] += 1
                try:
                    json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    problems.append("%s: JSON hong: %s" % (item, exc))
                    continue
                if "checkpoints" in item.parts:
                    stats["checkpoint"] += 1
                    reason = _validate_checkpoint_file(data, str(item))
                    if reason is not None:
                        problems.append("%s: checkpoint khong hop le: %s" % (item, reason))
            elif suffix in (".yml", ".yaml"):
                stats["yaml"] += 1
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    problems.append("%s: khong phai UTF-8: %s" % (item, exc))
                    continue
                if _yaml is not None:
                    try:
                        _yaml.safe_load(text)
                    except Exception as exc:  # noqa: BLE001 — yaml.YAMLError
                        problems.append("%s: YAML hong: %s" % (item, exc))
                else:
                    reason = _check_yaml_light(text)
                    if reason is not None:
                        problems.append("%s: %s" % (item, reason))
    return problems, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT),
                        help="goc repo can quet (mac dinh: goc repo chua script)")
    parser.add_argument("--paths", nargs="*", default=DEFAULT_PATHS,
                        help="duong tuong doi can quet")
    parser.add_argument("--strict", action="store_true",
                        help="thieu duong quet cung tinh la loi")
    parser.add_argument("--json", dest="json_out", default=None,
                        help="ghi them bao cao JSON vao file nay")
    args = parser.parse_args(argv)
    root = Path(args.root)
    problems, stats = check_tree(root, args.paths, args.strict)
    print("quet %d file: %d JSON, %d YAML, %d checkpoint, %d file 0 byte"
          % (stats["files"], stats["json"], stats["yaml"],
             stats["checkpoint"], stats["zero_byte"]))
    if problems:
        print("PHAT HIEN %d VAN DE:" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
    else:
        print("KHONG PHAT HIEN VAN DE.")
    if args.json_out:
        report = {"stats": stats, "problems": problems,
                  "ok": not problems}
        try:
            Path(args.json_out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            print("canh bao: khong ghi duoc bao cao JSON %s: %s"
                  % (args.json_out, exc))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
