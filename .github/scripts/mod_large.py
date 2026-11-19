#!/usr/bin/env python3
"""Cong kiem tra + ep ban dich + repack IPA lon — chay tren runner GitHub Actions.

Goi dung pipeline cua tool web (sdk.mod_job.run_mod_job: gate.run_gate
-> rebuild.apply_rebuild -> repack.repack_from_rebuild_units) nhu
notebook Colab, nhung ket qua ghi ra thu muc --out de workflow upload
lam artifact thay vi luu ve Drive.

Chi stdlib + code repo (Python 3.9 tuong thich); khong doc token/key nao.
Thoat 0 khi job chay xong co ket qua (ca khi cong CHUA DAT — do la ket
qua dung, xem gate-report.json); thoat 1 khi loi runner (file thieu/
hong, ep loi) hoac file da ep kiem lai pressed KHONG DAT.

Gioi han artifact (xem sdk.mod_job.ARTIFACT_BUDGET_BYTES = 400 MB):
runner ghi day du ket qua ra --out; workflow chi upload artifact theo
summary["artifact_plan"] (bao cao cong + summary luon duoc; IPA da
repack >400 MB thi khong len artifact — lay qua duong Colab/Drive).
"""
import argparse
import hashlib
import json
import os
import resource
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--original", required=True)
    ap.add_argument("--translation", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--file-id", default="")
    ap.add_argument("--original-file-id", default="")
    ap.add_argument("--translation-file-id", default="")
    ap.add_argument("--target-lang", default="vi")
    ap.add_argument("--apply-mode", default="REBUILD_SECTION")
    ap.add_argument("--repack", dest="repack", action="store_true",
                    default=True)
    ap.add_argument("--no-repack", dest="repack", action="store_false")
    ap.add_argument("--gate-only", action="store_true", default=False)
    ap.add_argument("--display-font", default="donor",
                    help="donor (font Be Vietnam Pro di kem, mac dinh) | "
                         "scan (quet font trong file goc) | duong dan .ttf")
    ap.add_argument("--engine", default="unity",
                    help="engine game: unity (duy nhat hien co; Unreal / "
                         "Native iOS se them sau)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--download-seconds", type=float, default=0.0)
    args = ap.parse_args()

    ipa_path = os.path.abspath(args.ipa)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.monotonic()
    sys.path.insert(0, os.path.join(REPO, "pb3"))
    from sdk import colab_job as cj  # noqa: E402
    from sdk import mod_job as mj  # noqa: E402

    job = None
    if args.file_id and args.original_file_id and args.translation_file_id:
        job = cj.build_mod_job(
            args.game, args.file_id, args.original_file_id,
            args.translation_file_id, target_lang=args.target_lang,
            apply_mode=args.apply_mode, repack=args.repack,
            gate_only=args.gate_only,
            display_font=("scan" if args.display_font == "scan"
                          else "donor"),
            game_file_name=os.path.basename(ipa_path),
            engine=args.engine)
    display_font_path = None
    if args.display_font == "scan":
        display_font_path = "scan"
    elif args.display_font != "donor":
        display_font_path = args.display_font  # duong dan .ttf tu chon

    print("Mod job: %s | engine %s | dich %s | ep %s | repack %s | gate_only %s | "
          "font %s" % (args.game, args.engine, args.target_lang, args.apply_mode,
                       "co" if args.repack else "khong",
                       "co" if args.gate_only else "khong",
                       args.display_font), flush=True)
    try:
        summary = mj.run_mod_job(
            ipa_path=ipa_path, original_path=args.original,
            translation_path=args.translation, out_dir=out_dir,
            game=args.game, target_lang=args.target_lang,
            apply_mode=args.apply_mode, repack=args.repack,
            gate_only=args.gate_only, job=job,
            display_font_path=display_font_path,
            engine=args.engine)
    except Exception as exc:  # noqa: BLE001 - loi runner: ghi manifest failed
        print("Loi chay Mod job: %s: %s" % (type(exc).__name__, exc),
              flush=True)
        try:
            manifest = cj.build_mod_manifest(
                job, "failed", [], note="Loi runner: %s" % exc,
                job_id=(job["job_id"] if job else
                        "mod-%s-%s" % (cj.slugify_game(args.game),
                                       time.strftime("%Y%m%d-%H%M%S",
                                                     time.gmtime()))),
                game=args.game)
            cj.write_mod_manifest(out_dir, manifest)
        except Exception:  # noqa: BLE001 - manifest la phu
            pass
        return 1

    total_seconds = time.monotonic() - t0
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print("", flush=True)
    print("=== Ket qua ===", flush=True)
    print("  Cong: %s | thay doi: %s | don vi: %s" % (
        summary.get("gate_verdict"),
        (summary.get("gate_totals") or {}).get("changed"),
        (summary.get("gate_totals") or {}).get("units")), flush=True)
    print("  Trang thai: %s — %s" % (summary.get("status"),
                                     summary.get("note")), flush=True)
    if summary.get("applied"):
        print("  Da ep: %s thay doi, %d don vi" % (
            (summary.get("apply_totals") or {}).get("changed"),
            (summary.get("apply_totals") or {}).get("units")), flush=True)
    if summary.get("repacked"):
        print("  IPA da repack: %s (%.1f MB, md5 %s)" % (
            summary.get("repacked_name"),
            summary.get("repacked_bytes", 0) / 1048576.0,
            summary.get("repacked_md5")), flush=True)
    if summary.get("pressed_gate_verdict"):
        print("  Kiem lai pressed: %s" % summary["pressed_gate_verdict"],
              flush=True)
    plan = summary.get("artifact_plan") or {}
    for e in plan.get("excluded") or []:
        print("  CANH BAO artifact: %s (%.1f MB) khong len artifact — %s"
              % (e["name"], e["bytes"] / 1048576.0, e["reason"]), flush=True)
    print("Tai: %.1f giay | Mod: %.1f giay | Tong: %.1f giay | RAM dinh: "
          "%.1f MB" % (args.download_seconds, summary.get("elapsed_seconds",
                                                         0),
                       args.download_seconds + total_seconds, peak_mb),
          flush=True)

    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        lines = ["## Mod: %s" % args.game, "",
                 "- Cong: **%s** | thay doi: %s | don vi: %s"
                 % (summary.get("gate_verdict"),
                    (summary.get("gate_totals") or {}).get("changed"),
                    (summary.get("gate_totals") or {}).get("units")),
                 "- Trang thai: %s — %s" % (summary.get("status"),
                                            summary.get("note")),
                 ""]
        if summary.get("repacked"):
            lines.append("- IPA da repack: `%s` (%.1f MB)"
                         % (summary.get("repacked_name"),
                            summary.get("repacked_bytes", 0) / 1048576.0))
        for e in plan.get("excluded") or []:
            lines.append("- CANH BAO: `%s` khong len artifact — %s"
                         % (e["name"], e["reason"]))
        with open(gh_summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if summary.get("status") == "pressed_gate_failed":
        print("File da ep kiem lai KHONG DAT — xem pressed-gate-report.json.",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
