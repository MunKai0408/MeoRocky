#!/usr/bin/env python3
"""Chay mo phong song EPERM Anti-Wave Phase 6 (khong mang that, khong loi that).

  python3 scripts/run_wave_simulation.py [--root DIR] [--scenario NAME]
  python3 scripts/run_wave_simulation.py --list

Probe song truoc khi lam gi: song dang bat thi DUNG (exit 2) — script
khong ghi dia trong song. Sandbox tao duoi --root (mac dinh
anti_wave/sim-work o goc repo), don sach sau khi chay xong (tru --keep).

[MÔ PHỎNG]: probe gia, sync gia, fault-injection trong sandbox — xem
docstring anti_wave/simulation.py truoc khi doc ket qua.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
PB3 = os.path.join(ROOT, "pb3")
if PB3 not in sys.path:
    sys.path.insert(0, PB3)

from anti_wave.healthcheck import WAVE_OFF, probe_wave  # noqa: E402
from anti_wave.simulation import (  # noqa: E402
    BUILTIN_SCENARIOS,
    run_all_scenarios,
    run_wave_scenario,
    simulation_report_summary,
    simulation_summary,
)

MAIN_SHA = "e1cc72965435ff214e876d3a122c206a125ba474"  # main sau PR #38


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.join(ROOT, "anti_wave", "sim-work"),
                    help="thu muc sandbox (mac dinh anti_wave/sim-work)")
    ap.add_argument("--scenario", default=None,
                    help="chi chay 1 kich ban theo ten")
    ap.add_argument("--list", action="store_true",
                    help="liet ke kich ban co san roi thoat")
    ap.add_argument("--keep", action="store_true",
                    help="giu lai sandbox sau khi chay (de dieu tra)")
    ap.add_argument("--base-sha", default=MAIN_SHA,
                    help="base_commit_sha cho checkpoint/patch gia lap")
    args = ap.parse_args(argv)

    if args.list:
        for sc in BUILTIN_SCENARIOS:
            print("%-28s %s" % (sc["name"], sc["title"]))
        return 0

    res = probe_wave()
    if res.status != WAVE_OFF:
        print("DUNG: song EPERM dang bat — khong chay mo phong luc nay "
              "(probe: %s)" % res.detail)
        return 2

    work = os.path.abspath(args.root)
    shutil.rmtree(work, ignore_errors=True)
    try:
        if args.scenario:
            wanted = [s for s in BUILTIN_SCENARIOS if s["name"] == args.scenario]
            if not wanted:
                print("Khong co kich ban %r. Dung --list de xem." % args.scenario)
                return 2
            reports = [run_wave_scenario(wanted[0], os.path.join(work, wanted[0]["name"]),
                                         base_commit_sha=args.base_sha)]
            print(simulation_summary(reports[0]))
        else:
            reports = run_all_scenarios(work, base_commit_sha=args.base_sha)
            print(simulation_report_summary(reports))
            for rep in reports:
                print()
                print(simulation_summary(rep))
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in reports if r["verdict"] != "PASS"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
