#!/usr/bin/env python3
"""Trich xuat 5 game that (muc A) bang bridge.extract tren CPython.

Chay: python3 exp/extract_5games.py [--only 9kings|data|ek2|pb3|passpartout]
Ket qua: exp/out/extract_5games/<game>/ (file trich xuat) + summary.json.
Dung locales ["en","zh"] -> split_mono bat (file .monobehaviour.<lang>.json
rieng tung ngon ngu, key sach khong #N). Them lan trich xuat GOP (khong
loc) cho 9kings + passpartout de lam ground truth cho check_split_impurity.
File game that KHONG commit vao repo (chi doc).
"""
import json
import os
import shutil
import sys
import time

SITE = "/home/hatch/workspace/pb3_web/site"
EXP = "/home/hatch/workspace/pb3_web/exp"
OUT = EXP + "/out/extract_5games"
GAMES = {
    "9kings": "/home/hatch/workspace/real_ipa/9kings.ipa",
    "data": "/home/hatch/workspace/real_ipa/data.unity3d",
    "ek2": "/home/hatch/workspace/real_ipa/exploding_kittens2.ipa",
    "pb3": "/home/hatch/workspace/real_ipa/polybridge3.ipa",
    "passpartout": "/home/hatch/workspace/real_ipa/passpartout.ipa",
}

sys.path.insert(0, SITE + "/pb3")
import bridge  # noqa: E402

bridge.WEB_ROOT = SITE + "/pb3"
print("boot:", bridge.boot())


def run_one(name, path, locales):
    t0 = time.monotonic()
    res = json.loads(bridge.extract(path, locales=locales))
    dt = time.monotonic() - t0
    dest = os.path.join(OUT, name + ("_merged" if not locales else ""))
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree("/tmp/pb3out", dest)
    files = [{"path": f["path"], "locale": f.get("locale"),
              "entries": f["entries"], "source": f.get("source")}
             for f in res.get("files", [])]
    total = sum(f["entries"] for f in files)
    print("== %s locales=%s ok=%s %.1fs total=%d files=%d" %
          (name, locales, res.get("ok"), dt, total, len(files)))
    for f in files:
        print("   - %-55s locale=%-4s entries=%d" %
              (f["path"][-55:], f["locale"], f["entries"]))
    return {"game": name, "locales": locales, "ok": res.get("ok"),
            "decision": res.get("decision"), "seconds": round(dt, 1),
            "total_entries": total, "files": files,
            "errors": res.get("errors") or []}


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    os.makedirs(OUT, exist_ok=True)
    summary = {}
    try:
        prev = json.load(open(OUT + "/summary.json", encoding="utf-8"))
        summary = prev.get("games", {})
    except Exception:
        pass
    for name, path in GAMES.items():
        if only and name != only:
            continue
        print("---", name, os.path.getsize(path), "byte")
        summary[name] = run_one(name, path, ["en", "zh"])
        if name in ("9kings", "passpartout"):
            summary[name + "_merged"] = run_one(name, path, None)
    json.dump({"games": summary}, open(OUT + "/summary.json", "w",
                                        encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("SUMMARY WRITTEN", OUT + "/summary.json")


if __name__ == "__main__":
    main()
