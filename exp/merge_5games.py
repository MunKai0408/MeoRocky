#!/usr/bin/env python3
"""Gop 1 file: trich xuat 5 game that, vua per-unit vua gop, de doi chieu.

Chay: python3 exp/merge_5games.py [--only 9kings|data|ek2|pb3|passpartout]
Ket qua: exp/out/merge_one_file/<game>_perunit/ + <game>_merged/
  + summary.json (so sanh: key gop == hop cac file tach, thu tu, trung key,
  xung dot, file config giu nguyen, sidecar provenance).
Dung bridge.extract tren CPython, locales ["en","zh"].
File game that KHONG commit vao repo (chi doc).
"""
import json
import os
import shutil
import sys
import time

SITE = "/home/hatch/workspace/pb3_web/site"
EXP = "/home/hatch/workspace/pb3_web/exp"
OUT = EXP + "/out/merge_one_file"
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


def _load_jsons(d):
    out = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            try:
                out[fn] = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            except Exception:
                pass
    return out


def run_one(name, path, merge):
    t0 = time.monotonic()
    res = json.loads(bridge.extract(path, locales=["en", "zh"],
                                    merge_one_file=merge))
    dt = time.monotonic() - t0
    dest = os.path.join(OUT, name + ("_merged" if merge else "_perunit"))
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree("/tmp/pb3out", dest)
    files = [{"path": f["path"], "locale": f.get("locale"),
              "entries": f["entries"], "exported": f.get("exported"),
              "source": f.get("source")}
             for f in res.get("files", [])]
    print("== %s merge=%s ok=%s %.1fs total=%d files=%d"
          % (name, merge, res.get("ok"), dt, res.get("total_entries"),
             len(files)))
    for f in files:
        print("   - %-50s locale=%-4s entries=%d"
              % (f["exported"] or f["path"][-50:], f["locale"], f["entries"]))
    return {"game": name, "merge": merge, "ok": res.get("ok"),
            "decision": res.get("decision"), "seconds": round(dt, 1),
            "total_entries": res.get("total_entries"), "files": files,
            "merged_files": res.get("merged_files") or [],
            "errors": res.get("errors") or []}


def compare(name, perunit, merged):
    """Doi chieu file gop voi hop cac file tach per-unit (cung dot chay)."""
    pu_dir = os.path.join(OUT, name + "_perunit")
    mg_dir = os.path.join(OUT, name + "_merged")
    pu = _load_jsons(pu_dir)
    mg = _load_jsons(mg_dir)
    out = {}
    # Thu tu file tach = thu tu trong res files (luu trong summary).
    for lang in ("en", "zh"):
        want_file = "%s.json" % lang
        got = mg.get(want_file)
        # Cac file tach cua locale nay, theo thu tu res files.
        parts = [(f["exported"], pu.get(f["exported"]))
                 for f in perunit["files"]
                 if f["locale"] == lang and f.get("exported")
                 and isinstance(pu.get(f["exported"]), dict)]
        exp_kv = {}
        exp_order = []
        dups = 0
        conflicts = []
        for fname, kv in parts:
            for k, v in kv.items():
                if k not in exp_kv:
                    exp_kv[k] = v
                    exp_order.append(k)
                elif exp_kv[k] == v:
                    dups += 1
                else:
                    conflicts.append({"key": k, "kept": exp_kv[k],
                                      "dropped": v, "file": fname})
        ok_keys = got is not None and list(got) == exp_order
        ok_vals = got is not None and all(got.get(k) == v
                                          for k, v in exp_kv.items())
        hash_n = sum(1 for k in (got or {}) if "#" in k)
        side = mg.get("%s.provenance.json" % lang)
        side_ok = (isinstance(side, dict)
                   and side.get("format") == "beartea-merge-provenance/1"
                   and len(side.get("keys") or {}) == len(got or {})
                   and side.get("file") == want_file)
        rep = mg.get("extraction_report.json") or {}
        rep_m = [m for m in (rep.get("merged_files") or [])
                 if m.get("locale") == lang]
        stat = rep_m[0] if rep_m else {}
        out[lang] = {
            "merged_file": want_file,
            "merged_keys": len(got or {}),
            "expected_keys": len(exp_kv),
            "perunit_files": len(parts),
            "perunit_entries_sum": sum(len(kv or {}) for _f, kv in parts),
            "order_and_values_match": bool(ok_keys and ok_vals),
            "hash_suffix_keys": hash_n,
            "duplicates_identical": dups,
            "conflicts": len(conflicts),
            "conflict_keys": [c["key"] for c in conflicts[:10]],
            "conflict_detail": conflicts[:5],
            "sidecar_ok": bool(side_ok),
            "report_stat_ok": bool(stat)
            and stat.get("keys") == len(got or {})
            and stat.get("duplicates_identical") == dups
            and stat.get("conflicts_total") == len(conflicts),
            "merged_bytes": os.path.getsize(os.path.join(mg_dir, want_file))
            if got is not None else 0,
        }
        print("   [%s] %s: gop %d key (ky vong %d, tu %d file tach, tong %d) "
              "khop=%s #N=%d trung_giong=%d xung_dot=%d sidecar=%s report=%s"
              % (name, lang, out[lang]["merged_keys"],
                 out[lang]["expected_keys"], out[lang]["perunit_files"],
                 out[lang]["perunit_entries_sum"],
                 out[lang]["order_and_values_match"], hash_n, dups,
                 len(conflicts), side_ok, out[lang]["report_stat_ok"]))
    # File config (locale None): ten + noi dung giong het giua 2 dot.
    cfg_pu = {f["exported"]: pu.get(f["exported"])
              for f in perunit["files"]
              if f["locale"] is None and f.get("exported")}
    cfg_mg = {f["exported"]: mg.get(f["exported"])
              for f in merged["files"]
              if f["locale"] is None and f.get("exported")}
    same_cfg = (set(cfg_pu) == set(cfg_mg)
                and all(cfg_pu[k] == cfg_mg[k] for k in cfg_pu))
    out["config_unchanged"] = bool(same_cfg)
    out["config_files"] = sorted(cfg_pu)
    print("   [%s] config giu nguyen: %s (%d file)"
          % (name, same_cfg, len(cfg_pu)))
    return out


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
        perunit = run_one(name, path, False)
        merged = run_one(name, path, True)
        summary[name] = {"perunit": perunit, "merged": merged,
                         "compare": compare(name, perunit, merged)}
    json.dump({"games": summary}, open(OUT + "/summary.json", "w",
                                        encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("SUMMARY WRITTEN", OUT + "/summary.json")


if __name__ == "__main__":
    main()
