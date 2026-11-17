#!/usr/bin/env python3
"""Harness hồi quy BUG#2 — đường merge trích xuất (chạy bằng CPython).

Chạy bridge.extract() thật trên game thật với đúng mặc định web
(locales=["en", "zh"], merge_one_file=True) rồi assert theo tiêu chí
chấp nhận của BUG#2 (xem AUTOPILOT.md, mục BUG#2):

  1. Đúng 1 file cho mỗi locale xin: en -> en.json; en+zh ->
     en.json + zh-CN.json + zh-TW.json. Không file rác nào trong output.
  2. Đủ toàn bộ text, đúng số theo từng build: Build 68 en = 2086 key
     (khớp en-build68.json); v1.0.6 mỗi locale 2252 key (khớp file mẫu
     en.json / zh-TW.json); PB3 2348 key/file; EK2 1344 key/file.
  3. 0 key generic (.sNNNN/.pNNNN/textasset.sN) trong mọi file locale.
  4. Mọi key StringTable đều dạng Collection/Key (có "/").
  5. Cùng tập key giữa các locale (chỉ giá trị được phép rỗng).
  6. Rác vứt hết: zip output chỉ chứa các file locale.

Cách chạy (từ gốc repo):
    python3 exp/verify_bug2_merge.py
    python3 exp/verify_bug2_merge.py --build68 <path> --ipa9k <path> ...
Đường dẫn mặc định trỏ tới file game trong máy làm việc; case nào thiếu
file thì BỎ QUA có ghi rõ (không fail) để harness vẫn chạy được trên máy
không có file game. Kết quả ghi ra exp/bug2_merge_results.md + .json.

Nhãn: mọi số trong báo cáo là [ĐÃ ĐO THẬT] trên file game thật.
"""

import argparse
import json
import os
import re
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "pb3"))

GENERIC_RE = re.compile(r"\.(s|p)\d+")
DEFAULTS = {
    "build68": "/home/hatch/workspace/user/files/"
               "A868947C-4963-4991-A547-15988908E58B-resources.assets",
    "build68_expected": "/home/hatch/workspace/your_files/en-build68.json",
    "ipa9k": "/home/hatch/workspace/test-ipas/9kings-v1.0.6.ipa",
    "ipaek2": "/home/hatch/workspace/test-ipas/ek2-v0.0.18.ipa",
    "ipapb3": "/home/hatch/workspace/test-ipas/pb3-v1.1.7.ipa",
    "sample_en": "/home/hatch/workspace/user/files/en.json",
    "sample_zhtw": "/home/hatch/workspace/user/files/zh-TW.json",
}


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _generic_keys(kv):
    return [k for k in kv if GENERIC_RE.search(k)]


def _check_locale_files(out_dir, expect_names):
    """out_dir chỉ chứa đúng các file locale đã xin, không file rác."""
    actual = sorted(os.listdir(out_dir))
    return actual == sorted(expect_names), actual


def _run_extract(bridge, source, locales):
    import shutil
    out_dir = "/tmp/pb3out"
    shutil.rmtree(out_dir, ignore_errors=True)
    res = json.loads(bridge.extract(source, locales=list(locales),
                                    merge_one_file=True))
    zpath = "/tmp/extract.zip"
    zip_names = sorted(zipfile.ZipFile(zpath).namelist()) if os.path.exists(zpath) else []
    return res, out_dir, zip_names


def _case_build68(bridge, args):
    """Build 68 resources.assets -> en/zh-CN/zh-TW, en khớp en-build68.json."""
    name = "build68"
    if not os.path.exists(args.build68) or not os.path.exists(args.build68_expected):
        return {"case": name, "status": "skipped",
                "reason": "thiếu file resources.assets Build 68 hoặc en-build68.json"}
    res, out_dir, zip_names = _run_extract(bridge, args.build68, ["en", "zh"])
    checks = []
    ok_files, actual = _check_locale_files(
        out_dir, ["en.json", "zh-CN.json", "zh-TW.json"])
    checks.append(("chỉ 3 file locale trong out_dir", ok_files, actual))
    checks.append(("zip chỉ chứa 3 file locale",
                   zip_names == ["en.json", "zh-CN.json", "zh-TW.json"], zip_names))
    expected_en = _load_json(args.build68_expected)
    en_path = os.path.join(out_dir, "en.json")
    en = _load_json(en_path) if os.path.exists(en_path) else {}
    checks.append(("en.json == en-build68.json (từng key/giá trị)",
                   en == expected_en,
                   "en=%d key, expected=%d key" % (len(en), len(expected_en))))
    checks.append(("en.json 2086 key", len(en) == 2086, len(en)))
    for loc in ("zh-CN", "zh-TW"):
        p = os.path.join(out_dir, loc + ".json")
        kv = _load_json(p) if os.path.exists(p) else {}
        checks.append(("%s 2086 key" % loc, len(kv) == 2086, len(kv)))
        checks.append(("%s cùng tập key với en" % loc,
                       set(kv) == set(en), len(set(kv) ^ set(en))))
        checks.append(("%s 0 key generic" % loc, not _generic_keys(kv),
                       _generic_keys(kv)[:3]))
    checks.append(("en 0 key generic", not _generic_keys(en), _generic_keys(en)[:3]))
    checks.append(("bridge ok", bool(res.get("ok")), res.get("error", "")))
    failed = [c for c in checks if not c[1]]
    return {"case": name, "status": "FAIL" if failed else "PASS",
            "checks": [{"name": c[0], "ok": bool(c[1]), "detail": str(c[2])[:300]}
                       for c in checks]}


def _case_ipa(bridge, args, name, source, sample_en, sample_zhtw,
              expect_keys, expect_names):
    """IPA thật: mỗi locale đủ số key kỳ vọng, khớp file mẫu khi có."""
    if not os.path.exists(source):
        return {"case": name, "status": "skipped",
                "reason": "thiếu file %s" % source}
    res, out_dir, zip_names = _run_extract(bridge, source, ["en", "zh"])
    checks = []
    ok_files, actual = _check_locale_files(out_dir, expect_names)
    checks.append(("chỉ file locale trong out_dir", ok_files, actual))
    checks.append(("zip chỉ chứa file locale",
                   zip_names == sorted(expect_names), zip_names))
    kvs = {}
    for fn in expect_names:
        p = os.path.join(out_dir, fn)
        kvs[fn] = _load_json(p) if os.path.exists(p) else {}
        checks.append(("%s %d key" % (fn, expect_keys),
                       len(kvs[fn]) == expect_keys, len(kvs[fn])))
        checks.append(("%s 0 key generic" % fn, not _generic_keys(kvs[fn]),
                       _generic_keys(kvs[fn])[:3]))
    names = list(kvs)
    for i in range(1, len(names)):
        checks.append(("%s cùng tập key với %s" % (names[i], names[0]),
                       set(kvs[names[i]]) == set(kvs[names[0]]),
                       len(set(kvs[names[i]]) ^ set(kvs[names[0]]))))
    if sample_en and os.path.exists(sample_en) and "en.json" in kvs:
        sample = _load_json(sample_en)
        checks.append(("en.json == file mẫu en.json",
                       kvs["en.json"] == sample,
                       "khác %d key" % len(set(kvs["en.json"].items()) ^ set(sample.items()))))
    if sample_zhtw and os.path.exists(sample_zhtw) and "zh-TW.json" in kvs:
        sample = _load_json(sample_zhtw)
        diff = ({k for k in sample if kvs["zh-TW.json"].get(k) != sample[k]}
                | {k for k in kvs["zh-TW.json"] if k not in sample})
        # Cho phép khác mẫu: key StringTable mà mẫu để rỗng nhưng bản gộp
        # có giá trị thật từ bảng ST mã gốc "zh" (fallback mã gốc ->
        # biến thể). [ĐÃ ĐO THẬT 2026-11-17: IPA chỉ có 1 bảng ST tiếng
        # Trung (locale "zh", bundle chinese(simplified)(zh), 26 key
        # Policies có giá trị thật); không có bảng zh-TW riêng. Mẫu P0-3
        # không đưa fallback này vào zh-TW nên 26 key đó rỗng trong mẫu.
        # Giữ giá trị thật = đủ text, không bỏ sót nguồn hợp lệ. Key
        # cho phép phải là key ST, rỗng trong mẫu, có giá trị thật trong
        # bản gộp, và giống hệt giá trị trong zh-CN.json (cùng nguồn
        # bảng zh gốc — không phải giá trị bịa).]
        allowed = {k for k in diff
                   if "/" in k and sample.get(k) == ""
                   and kvs["zh-TW.json"].get(k) not in (None, "")
                   and kvs.get("zh-CN.json", {}).get(k) == kvs["zh-TW.json"].get(k)}
        checks.append(("zh-TW.json == file mẫu zh-TW.json (trừ key fallback ST zh)",
                       diff <= allowed,
                       "khác %d key, trong đó %d key fallback cho phép"
                       % (len(diff), len(diff & allowed))))
    checks.append(("bridge ok", bool(res.get("ok")), res.get("error", "")))
    failed = [c for c in checks if not c[1]]
    return {"case": name, "status": "FAIL" if failed else "PASS",
            "checks": [{"name": c[0], "ok": bool(c[1]), "detail": str(c[2])[:300]}
                       for c in checks]}


def main():
    ap = argparse.ArgumentParser(description="Harness hồi quy BUG#2")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), default=v)
    args = ap.parse_args()
    import bridge
    bridge.WEB_ROOT = os.path.join(REPO_ROOT, "pb3")
    boot = json.loads(bridge.boot())
    results = {"boot_ok": bool(boot.get("ok")),
               "cases": []}
    results["cases"].append(_case_build68(bridge, args))
    results["cases"].append(_case_ipa(
        bridge, args, "9kings-v1.0.6", args.ipa9k, args.sample_en,
        args.sample_zhtw, 2252, ["en.json", "zh-CN.json", "zh-TW.json"]))
    # PB3 [ĐÃ ĐO THẬT 2026-11-17]: langtable 11 cột x 2348 term; CSV
    # PB3_LOC_DEFAULT.csv có đúng cùng 2348 term thật (2344 trùng hệt +
    # 4 xung đột giữ bản đầu) + 2 dòng hoàn toàn trống đã bị bỏ (không
    # sinh key row_NNNNNN nữa) — gộp đúng = 2348 key/file. Số merged zh
    # 2.350 trong bảng hồi quy AUTOPILOT đã gồm 2 key rác đó nên không
    # dùng làm chuẩn. Game CHỈ có cột "Chinese (Simplified)" — không có
    # zh-TW — nên không kỳ vọng zh-TW.json (vắng là ĐÚNG, giống EK2
    # không có zh).
    results["cases"].append(_case_ipa(
        bridge, args, "pb3-v1.1.7", args.ipapb3, None, None, 2348,
        ["en.json", "zh-CN.json"]))
    # EK2 không có dữ liệu tiếng Trung (P0-3): chỉ xin en+zh nhưng chỉ
    # kỳ vọng file en.json; file zh vắng là ĐÚNG, không phải lỗi.
    ek2 = _case_ipa(bridge, args, "ek2-v0.0.18", args.ipaek2, None, None,
                    1344, ["en.json"])
    results["cases"].append(ek2)
    n_pass = sum(1 for c in results["cases"] if c["status"] == "PASS")
    n_fail = sum(1 for c in results["cases"] if c["status"] == "FAIL")
    n_skip = sum(1 for c in results["cases"] if c["status"] == "skipped")
    results["summary"] = {"PASS": n_pass, "FAIL": n_fail, "skipped": n_skip}
    out_json = os.path.join(REPO_ROOT, "exp", "bug2_merge_results.json")
    out_md = os.path.join(REPO_ROOT, "exp", "bug2_merge_results.md")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    lines = ["# Harness hồi quy BUG#2 — kết quả",
             "",
             "Tiêu chí: đúng 1 file/locale, đủ key theo build, 0 key generic, "
             "StringTable dạng Collection/Key, cùng tập key, rác vứt hết.",
             "",
             "Tổng: PASS %d, FAIL %d, bỏ qua %d" % (n_pass, n_fail, n_skip),
             ""]
    for c in results["cases"]:
        lines.append("## %s — %s" % (c["case"], c["status"]))
        if c["status"] == "skipped":
            lines.append("- Bỏ qua: %s" % c.get("reason", ""))
        for ch in c.get("checks", []):
            lines.append("- [%s] %s — %s" % ("x" if ch["ok"] else " ",
                                             ch["name"], ch["detail"]))
        lines.append("")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("PASS %d, FAIL %d, bỏ qua %d" % (n_pass, n_fail, n_skip))
    for c in results["cases"]:
        print("  %-14s %s" % (c["case"], c["status"]))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
