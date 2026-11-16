#!/usr/bin/env python3
"""Phần (D): liệt kê font + gom 1 font trên 5 game thật.

Chạy: python3 exp/font_unify_5games.py [--only 9kings|data|ek2|pb3|passpartout]
Kết quả: exp/out/font_unify/summary.json + <game>_font_list.json +
  <game>_font_unified.zip (bản sao /tmp/font_unified.zip của game đó).
Dùng bridge.font_list + bridge.font_unify trên CPython (cùng code bridge
mà worker Pyodide gọi). File game thật KHÔNG commit vào repo (chỉ đọc).
"""
import argparse
import json
import os
import shutil
import sys
import time

SITE = "/home/hatch/workspace/pb3_web/site"
EXP = "/home/hatch/workspace/pb3_web/exp"
OUT = EXP + "/out/font_unify"
GAMES = {
    "9kings": "/home/hatch/workspace/real_ipa/9kings.ipa",
    "data": "/home/hatch/workspace/real_ipa/data.unity3d",
    "ek2": "/home/hatch/workspace/real_ipa/exploding_kittens2.ipa",
    "pb3": "/home/hatch/workspace/real_ipa/polybridge3.ipa",
    "passpartout": "/home/hatch/workspace/real_ipa/passpartout.ipa",
}
DONOR = SITE + "/fonts/BeVietnamPro-Regular.ttf.b64"

sys.path.insert(0, SITE + "/pb3")
import bridge  # noqa: E402

bridge.WEB_ROOT = SITE + "/pb3"


def _donor_bytes():
    import base64
    return base64.b64decode(open(DONOR, "rb").read())


def run_one(name, path):
    t0 = time.monotonic()
    listed = json.loads(bridge.font_list(path))
    dt_list = time.monotonic() - t0
    t1 = time.monotonic()
    # Ghi donor ra /tmp để bridge.font_unify đọc (CPython: không có /pb3web).
    donor_tmp = "/tmp/BeVietnamPro-Regular.ttf"
    open(donor_tmp, "wb").write(_donor_bytes())
    unified = json.loads(bridge.font_unify(path, donor_tmp))
    dt_unify = time.monotonic() - t1
    dest_list = os.path.join(OUT, name + "_font_list.json")
    dest_zip = os.path.join(OUT, name + "_font_unified.zip")
    shutil.copyfile("/tmp/font_list.json", dest_list)
    shutil.copyfile("/tmp/font_unified.zip", dest_zip)
    fonts = listed.get("fonts") or []
    plan = unified.get("plan") or {}
    row = {
        "game": name,
        "size": os.path.getsize(path),
        "list_ok": listed.get("ok"),
        "list_error": None if listed.get("ok") else listed,
        "list_seconds": round(dt_list, 1),
        "unify_ok": unified.get("ok"),
        "unify_error": None if unified.get("ok") else unified,
        "unify_seconds": round(dt_unify, 1),
        "fonts": len(fonts),
        "summary": listed.get("summary"),
        "files_scanned": listed.get("files_scanned"),
        "bundle_nodes": listed.get("bundle_nodes"),
        "warnings": listed.get("warnings"),
        "font_rows": [{
            "kind": f.get("kind"), "name": f.get("name"),
            "source": f.get("source"), "size": f.get("size"),
            "path_id": f.get("path_id"), "covered": f.get("covered"),
            "total": f.get("total"), "missing_total": f.get("missing_total"),
            "status": f.get("status"), "reason": f.get("reason"),
        } for f in fonts],
        "tmp_items": len(listed.get("tmp_items") or []),
        "tmp_candidates": len(listed.get("tmp_candidates") or []),
        "plan_counts": plan.get("counts"),
        "merged": [{
            "output": m.get("output"), "name": m.get("name"),
            "added": m.get("added"), "glyphs_copied": m.get("glyphs_copied"),
        } for m in unified.get("merged") or []],
        "zip_bytes": os.path.getsize(dest_zip),
        "zip_entries": [],
    }
    import zipfile
    with zipfile.ZipFile(dest_zip) as zf:
        row["zip_entries"] = [
            {"name": i.filename, "size": i.file_size}
            for i in zf.infolist()]
        # Xác minh thật: file TTF đã gộp phải đủ 142/142.
        from sdk import font as _f
        for m in row["merged"]:
            rep = _f.coverage(zf.read(m["output"]), where=m["output"])
            m["verified_covered"] = int(rep.covered)
            m["verified_full"] = bool(rep.is_full)
    print("== %s ok=%s/%s %.1fs/%.1fs fonts=%d summary=%s merged=%d zip=%dB" % (
        name, listed.get("ok"), unified.get("ok"), dt_list, dt_unify,
        len(fonts), listed.get("summary"), len(row["merged"]),
        row["zip_bytes"]))
    for f in fonts:
        print("   - %s | %s | %s | %s/%s | %s" % (
            f.get("kind"), f.get("name"), f.get("source"),
            f.get("covered"), f.get("total"), f.get("status")))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(GAMES))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    print("boot:", bridge.boot())
    summary = {"games": {}}
    names = [args.only] if args.only else list(GAMES)
    for name in names:
        summary["games"][name] = run_one(name, GAMES[name])
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    print("summary:", os.path.join(OUT, "summary.json"))


if __name__ == "__main__":
    main()
