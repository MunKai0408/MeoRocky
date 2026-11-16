#!/usr/bin/env python3
"""Do chinh xac sdk.langdetect tren game that (ground truth tu file GOP).

- Passpartout: nhom 15 bien the (859 base du 15): slot 0..14 =
  [de, en, es, fr, ja, ko, nl, en(hai tac), pl, pt, ro, ru, sv, zh, zh].
- 9 Kings: nhom 30 chuoi (1561 nhom du 30) cat bang _split_term_chunks:
  slot = [term, en, pt, pt, ja, zh, de, es, es, ko, fr, ru, it, bg, cs,
  tl, el, hu, id, id, pl, ro, sk, tr, uk, da, nl, fi, no, sv].
Chi do CHUOI CO CHU (bo term slot 0 va chuoi khong chu); chuoi trung
value o nhieu ngon ngu thi tinh dung neu detect tra 1 trong cac ngon
ngu do (mo). In ty le dung theo tung ngon ngu + tong.
"""
import json
import re
import sys
from collections import Counter

sys.path.insert(0, "/home/hatch/workspace/pb3_web/site/pb3")
from sdk import langdetect as ld
from sdk.extraction import _split_term_chunks

OUT = "/home/hatch/workspace/pb3_web/exp/out/extract_5games"
PP_LANGS = ["de", "en", "es", "fr", "ja", "ko", "nl", "en", "pl", "pt",
            "ro", "ru", "sv", "zh", "zh"]
K9_SLOTS = ["term", "en", "pt", "pt", "ja", "zh", "de", "es", "es", "ko",
            "fr", "ru", "it", "bg", "cs", "tl", "el", "hu", "id", "id",
            "pl", "ro", "sk", "tr", "uk", "da", "nl", "fi", "no", "sv"]


def base_key(k):
    k = re.sub(r"\.x\d+$", "", k)
    k = re.sub(r"\.p\d+$", "", k)
    return re.sub(r"#\d+$", "", k)


def measure(groups, langs, label, skip_slot0=True):
    total = correct = 0
    per = {}
    conf = Counter()
    for vals in groups:
        for i, v in enumerate(vals):
            if skip_slot0 and i == 0:
                continue
            if not v or not any(ch.isalpha() for ch in v):
                continue
            want = langs[i]
            # Chuoi trung value o nhieu ngon ngu: chap nhan bat ky ngon ngu nao co value do.
            acceptable = {langs[j] for j, g in enumerate(vals) if g == v}
            code, c = ld.detect_language(v)
            conf[c] += 1
            total += 1
            ok = code in acceptable
            if ok:
                correct += 1
            per.setdefault(want, [0, 0])
            per[want][1] += 1
            if ok:
                per[want][0] += 1
    print("Passpartout: 859 base du 15 lan" if label.startswith("Passpartout")
          else "9 Kings: 2134 nhom / 1561 nhom du 30 chuoi")
    print("== %s: %d/%d dung = %.1f%%  (tin cay: %s)" %
          (label, correct, total, 100.0 * correct / max(1, total), dict(conf)))
    for lang in sorted(per):
        c, t = per[lang]
        print("   %-4s %4d/%4d = %5.1f%%" % (lang, c, t, 100.0 * c / max(1, t)))
    return correct, total


def main():
    # Passpartout
    merged = json.load(open(OUT + "/passpartout_merged/resources.monobehaviour.json",
                            encoding="utf-8"))
    groups = {}
    for k, v in merged.items():
        groups.setdefault(base_key(k), []).append(v)
    full15 = [g for g in groups.values() if len(g) == 15]
    measure(full15, PP_LANGS, "Passpartout 15-ngon-ngu")
    # 9 Kings
    merged = json.load(open(OUT + "/9kings_merged/resources.monobehaviour.json",
                            encoding="utf-8"))
    pairs = [(k, v) for k, v in merged.items()
             if re.match(r"^I2Languages\.s\d+$", base_key(k))]
    full30 = []
    for has_term, chunk in _split_term_chunks(pairs):
        if has_term and len(chunk) == 30:
            full30.append([v for _k, v in chunk])
    measure(full30, K9_SLOTS, "9 Kings I2Languages")


if __name__ == "__main__":
    main()
