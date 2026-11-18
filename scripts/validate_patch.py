#!/usr/bin/env python3
"""Validate patch (unified diff) truoc khi luu/ap dung — dung tay hoac trong CI.

Kiem tra:
  * patch khong rong, khong vuot gioi han byte;
  * co header diff + hunk @@ hop le;
  * duong dan trong patch la tuong doi, khong thoat ra ngoai repo (../, /...);
  * khong chua gia tri giong bi mat/token (heuristic — co mau la tu choi).

Day la kiem tra TINH, khong ap dung patch. Ap dung that la viec cua
Phase 4 (patch_manager.py). Patch hop le van can nguoi/agent review
noi dung truoc khi ap.

Dung: python3 scripts/validate_patch.py patch.diff
     cat patch.diff | python3 scripts/validate_patch.py --stdin
Exit: 0 = hop le; 1 = co van de.
Python 3.9 tuong thich, chi stdlib.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from anti_wave.github_sync import PATCH_MAX_BYTES, validate_patch_text  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default=None,
                        help="file patch can validate")
    parser.add_argument("--stdin", action="store_true",
                        help="doc patch tu stdin thay vi file")
    parser.add_argument("--max-bytes", type=int, default=PATCH_MAX_BYTES)
    args = parser.parse_args(argv)
    if bool(args.path) == bool(args.stdin):
        parser.error("chi dung 1 trong 2: <file> | --stdin")
    if args.stdin:
        patch_text = sys.stdin.read()
        where = "<stdin>"
    else:
        try:
            patch_text = Path(args.path).read_text(encoding="utf-8")
        except OSError as exc:
            print("FAIL: khong doc duoc %s: %s" % (args.path, exc))
            return 1
        except UnicodeDecodeError as exc:
            print("FAIL: patch %s khong phai UTF-8: %s" % (args.path, exc))
            return 1
        where = str(args.path)
    if len(patch_text.encode("utf-8")) > args.max_bytes:
        print("FAIL %s: patch vuot %d byte" % (where, args.max_bytes))
        return 1
    problems = validate_patch_text(patch_text)
    if problems:
        print("FAIL %s: %d van de:" % (where, len(problems)))
        for problem in problems:
            print("  - %s" % problem)
        return 1
    print("OK %s: patch hop le (%d byte)." % (where, len(patch_text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
