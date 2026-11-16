#!/usr/bin/env python3
"""Kiem chung acceptance gop 1 file tren fixture resources.assets cua Joseph.

Chay: python3 exp/check_merge_fixture.py
Ket qua: exp/out/merge_one_file/fixture.json
  - en merge: dung 1 file en.json, so key, 0 #N, thu tu, doi chieu mau 2.086
  - zh merge: dung 1 file zh.json, ti le CJK
  - mac dinh (khong truyen merge): van ra file tach per-unit, khong co en.json
  - khong loc: chuoi 'His Winningness'/'Win one game.' khong co trong file goc
  - all-langs: hop term qua moi ngon ngu (do 22 key mau vang mat hoan toan)

Luu y trung thuc (do bang code, xem bao cao): file mau 2.086 key KHONG the
khop 100% tu fixture nay — bang I2Languages trong build nay (Unity
6000.3.8f1, 20MB) khong co khe tieng Anh (30 khe dich, khong khe nao la
en), nen nhieu term khong co ban tieng Anh trong file; 22 term khong co
trong file hoan toan. Gop giu nguyen noi dung file tach (0 mat), khong
tu tao chuoi tieng Anh khong co trong game.
File fixture KHONG commit vao repo (chi doc).
"""
import json
import os
import shutil
import sys

SITE = "/home/hatch/workspace/pb3_web/site"
EXP = "/home/hatch/workspace/pb3_web/exp"
OUT = EXP + "/out/merge_one_file"
FIXTURE = "/home/hatch/workspace/user/files/EAC193F5-FBAE-43A0-8CE2-6748EA5AF5DC-resources.assets"
SAMPLE = "/home/hatch/workspace/user/files/en.json"

sys.path.insert(0, SITE + "/pb3")
import bridge  # noqa: E402
from sdk import langdetect as ld  # noqa: E402

bridge.WEB_ROOT = SITE + "/pb3"
print("boot:", bridge.boot())


def run(locales, merge, dest):
    res = json.loads(bridge.extract(FIXTURE, locales=locales,
                                    merge_one_file=merge))
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree("/tmp/pb3out", dest)
    return res


def load(d, name):
    with open(os.path.join(d, name), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    os.makedirs(OUT, exist_ok=True)
    out = {}
    sample = json.load(open(SAMPLE, encoding="utf-8"))
    sample_keys = list(sample)

    # 1. en merge
    res_en = run(["en"], True, OUT + "/fixture_en")
    en_files = [f for f in res_en.get("files", []) if f.get("locale") == "en"]
    en = load(OUT + "/fixture_en", "en.json")
    en_keys = list(en)
    present = [k for k in sample_keys if k in en]
    pos = [en_keys.index(k) for k in present]
    side = load(OUT + "/fixture_en", "en.provenance.json")
    rep = load(OUT + "/fixture_en", "extraction_report.json")
    cfg_en = sorted(f["exported"] for f in res_en.get("files", [])
                    if f.get("locale") is None and f.get("exported"))
    out["en"] = {
        "ok": res_en.get("ok"),
        "locale_en_files": len(en_files),
        "exported_en": [f.get("exported") for f in en_files],
        "keys": len(en),
        "bytes": os.path.getsize(OUT + "/fixture_en/en.json"),
        "first_key": en_keys[0] if en_keys else None,
        "hash_suffix_keys": sum(1 for k in en_keys if "#" in k),
        "config_files": cfg_en,
        "sample_present": len(present),
        "sample_total": len(sample_keys),
        "sample_order_preserved": pos == sorted(pos),
        "sample_first_present_key": present[0] if present else None,
        "sample_first_present_pos": pos[0] if pos else None,
        # Gia tri trong file en gop bang dung ban dich tieng Viet cua Joseph
        # (= chuoi tieng Viet bi gan nham vao en o buoc tach (A) tren build
        # nay) — do nhiem ke thua tu file tach, khong phai do buoc gop.
        "en_value_equals_sample_vi": sum(
            1 for k in present if en.get(k) == sample.get(k)),
        "sidecar_keys": len(side.get("keys") or {}),
        "sidecar_format": side.get("format"),
        "report_merged": rep.get("merged_files"),
    }
    print("en: %d key, mau co %d/%d, thu tu giu: %s, #N=%d, config=%d"
          % (len(en), len(present), len(sample_keys),
             out["en"]["sample_order_preserved"],
             out["en"]["hash_suffix_keys"], len(cfg_en)))

    # 2. zh merge
    res_zh = run(["zh"], True, OUT + "/fixture_zh")
    zh_files = [f for f in res_zh.get("files", []) if f.get("locale") == "zh"]
    zh = load(OUT + "/fixture_zh", "zh.json")
    cjk = sum(1 for v in zh.values()
              if any(0x4E00 <= ord(c) <= 0x9FFF for c in v))
    out["zh"] = {
        "ok": res_zh.get("ok"),
        "locale_zh_files": len(zh_files),
        "exported_zh": [f.get("exported") for f in zh_files],
        "keys": len(zh),
        "bytes": os.path.getsize(OUT + "/fixture_zh/zh.json"),
        "first_key": list(zh)[0] if zh else None,
        "hash_suffix_keys": sum(1 for k in zh if "#" in k),
        "cjk_values": cjk,
        "sidecar_keys": len(load(OUT + "/fixture_zh",
                                 "zh.provenance.json").get("keys") or {}),
    }
    print("zh: %d key, CJK %d/%d, #N=%d"
          % (len(zh), cjk, len(zh), out["zh"]["hash_suffix_keys"]))

    # 3. Mac dinh (khong merge): van file tach per-unit, khong co en.json.
    res_off = run(["en"], None, OUT + "/fixture_perunit")
    off_names = sorted(f.get("exported") or "" for f in res_off.get("files", []))
    out["merge_off_default"] = {
        "ok": res_off.get("ok"),
        "has_en_json": "en.json" in off_names,
        "has_perunit_en": any("monobehaviour.en.json" in n for n in off_names),
        "files": off_names,
    }
    print("merge off: en.json" + (" CO (sai)" if out["merge_off_default"]["has_en_json"]
                                  else " khong co (dung)")
          + " · per-unit en: %s"
          % out["merge_off_default"]["has_perunit_en"])

    # 4. Khong loc: chuoi tieng Anh cua term mau co trong file goc khong?
    res_raw = run(None, None, OUT + "/fixture_nofilter")
    raw = load(OUT + "/fixture_nofilter",
               "EAC193F5-FBAE-43A0-8CE2-6748EA5AF5DC-resources.monobehaviour.json")
    raw_values = set(raw.values())
    out["raw_no_filter"] = {
        "ok": res_raw.get("ok"),
        "total_strings": len(raw),
        "his_winningness_present": "His Winningness" in raw_values,
        "win_one_game_present": "Win one game." in raw_values,
        "a_little_treat_present": "A Little Treat" in raw_values,
    }
    print("raw: %d chuoi; 'His Winningness' co: %s; 'A Little Treat' co: %s"
          % (len(raw), out["raw_no_filter"]["his_winningness_present"],
             out["raw_no_filter"]["a_little_treat_present"]))

    # 5. All-langs: hop term qua moi ngon ngu (term nao co trong file?).
    res_all = run(sorted(ld.SUPPORTED_CODES), False, OUT + "/fixture_all_langs")
    union = set()
    for fn in os.listdir(OUT + "/fixture_all_langs"):
        if ".monobehaviour." in fn and fn.endswith(".json"):
            d = load(OUT + "/fixture_all_langs", fn)
            union |= {k for k in d if "." not in k}
    absent = [k for k in sample_keys if k not in union]
    no_en = [k for k in sample_keys if k in union and k not in en]
    out["union_terms"] = {
        "distinct_term_keys": len(union),
        "sample_present_in_union": len(set(sample_keys) & union),
        "sample_absent_entirely": len(absent),
        "sample_absent_keys": absent,
        "sample_in_union_but_no_english": len(no_en),
    }
    print("union term: %d; mau co trong union %d/%d; vang mat hoan toan %d; "
          "co trong union nhung khong co tieng Anh %d"
          % (len(union), out["union_terms"]["sample_present_in_union"],
             len(sample_keys), len(absent), len(no_en)))

    json.dump(out, open(OUT + "/fixture.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("FIXTURE WRITTEN", OUT + "/fixture.json")


if __name__ == "__main__":
    main()
