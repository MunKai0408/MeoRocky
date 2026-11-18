#!/usr/bin/env python3
"""Trich xuat text game tu file IPA lon — chay tren runner GitHub Actions.

Goi dung pipeline cua tool web (pb3/bridge.extract, locales en+zh, gop
1 file/locale) nhu notebook Colab, nhung ket qua ghi ra thu muc --out
de workflow upload lam artifact thay vi luu ve Drive.

Chi stdlib + code repo (Python 3.9 tuong thich); khong doc token/key nao.
Thoat 1 khi trich that bai (loi SDK / file hong); thoat 0 ca khi 0 file
locale (game khong phai Unity — ket qua DUNG, ghi ro trong summary.json).
"""
import argparse
import hashlib
import json
import os
import resource
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT_TMP = "/tmp/pb3out"


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--file-id", default="")
    ap.add_argument("--locales", default="en,zh")
    ap.add_argument("--out", default="out")
    ap.add_argument("--download-seconds", type=float, default=0.0)
    args = ap.parse_args()

    locales = [s.strip().lower() for s in args.locales.split(",") if s.strip()]
    if not locales:
        locales = ["en", "zh"]
    ipa_path = os.path.abspath(args.ipa)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.monotonic()
    sys.path.insert(0, os.path.join(REPO, "pb3"))
    import bridge  # noqa: E402

    bridge.WEB_ROOT = os.path.join(REPO, "pb3")
    # Ban web gioi han file <=2 GB vi RAM tab trinh duyet; tren runner
    # khong co gioi han do nen noi tran nay (chi trong phien nay).
    bridge.MAX_WEB_INPUT = 64 * 1024 ** 3

    boot = json.loads(bridge.boot())
    print("Khoi dong: %s | plugin: %s"
          % ("OK" if boot.get("ok") else "LOI", boot.get("plugins")),
          flush=True)
    if not boot.get("ok"):
        print("Loi boot: %s" % boot.get("error"), flush=True)
        return 1

    def _progress(kind, done, total):
        if total:
            print("  [%s] %d/%d (%.0f%%)" % (kind, done, total,
                                             100.0 * done / total),
                  flush=True)
        else:
            print("  [%s] %d" % (kind, done), flush=True)

    bridge.set_progress_cb(_progress)
    res = json.loads(bridge.extract(ipa_path, locales=locales,
                                    merge_one_file=True))
    print("Trich xuat: %s | quyet dinh: %s"
          % ("OK" if res.get("ok") else "LOI", res.get("decision")),
          flush=True)
    if not res.get("ok"):
        print("Loi: %s" % res.get("error"), flush=True)
        return 1

    files = []
    for m in res.get("merged_files") or []:
        name = m.get("file")
        if not name:
            continue
        src = os.path.join(OUT_TMP, name)
        if not os.path.isfile(src):
            print("CANH BAO: file gop %s khong co trong %s" % (name, OUT_TMP),
                  flush=True)
            continue
        shutil.copy2(src, os.path.join(out_dir, name))
        with open(src, encoding="utf-8") as f:
            keys = len(json.load(f))
        files.append({"name": name, "keys": keys,
                      "md5": md5_of(src),
                      "locale": m.get("locale"),
                      "entries_before": m.get("entries_before"),
                      "conflicts_total": m.get("conflicts_total")})
    files.sort(key=lambda d: d["name"])

    extract_seconds = time.monotonic() - t0
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    ipa_size = os.path.getsize(ipa_path)
    summary = {
        "game": args.game,
        "file_id": args.file_id,
        "ipa_size": ipa_size,
        "ipa_md5": md5_of(ipa_path),
        "locales": locales,
        "files": files,
        "skipped": len(res.get("skipped") or []),
        "download_seconds": args.download_seconds,
        "extract_seconds": round(extract_seconds, 1),
        "total_seconds": round(args.download_seconds + extract_seconds, 1),
        "peak_ram_mb": round(peak_mb, 1),
        "ok": True,
        "decision": res.get("decision"),
        "note": ("0 file locale — game co the khong phai Unity hoac khong "
                 "co text trich duoc (ket qua dung, khong phai loi).")
        if not files else "",
    }
    with open(os.path.join(out_dir, "summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("", flush=True)
    print("=== Ket qua ===", flush=True)
    for fi in files:
        print("  %-12s %6d key  md5=%s" % (fi["name"], fi["keys"], fi["md5"]),
              flush=True)
    if not files:
        print("  0 file locale (xem summary.json)", flush=True)
    print("Bo qua: %d muc" % summary["skipped"], flush=True)
    print("Tai: %.1f giay | Trich: %.1f giay | Tong: %.1f giay | RAM dinh: "
          "%.1f MB" % (args.download_seconds, extract_seconds,
                       summary["total_seconds"], peak_mb), flush=True)

    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        lines = ["## Trich xuat: %s" % args.game,
                 "",
                 "- IPA: %d byte, md5 `%s`" % (ipa_size, summary["ipa_md5"]),
                 "- Tai: %.1f giay | Trich: %.1f giay | Tong: %.1f giay | "
                 "RAM dinh: %.1f MB" % (args.download_seconds,
                                        extract_seconds,
                                        summary["total_seconds"], peak_mb),
                 "- Bo qua: %d muc" % summary["skipped"],
                 ""]
        for fi in files:
            lines.append("- `%s`: %d key, md5 `%s`"
                         % (fi["name"], fi["keys"], fi["md5"]))
        if not files:
            lines.append("- 0 file locale — game co the khong phai Unity "
                         "(ket qua dung, khong phai loi).")
        with open(gh_summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
