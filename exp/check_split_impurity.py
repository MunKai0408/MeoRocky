#!/usr/bin/env python3
"""Do nhiem ngon ngu cua file tach (muc A) so voi ground truth.

- Passpartout: nhom 15 bien the trong file GOP (thu tu ngon ngu da biet:
  #1 de, #2 en, #3 es, #4 fr, #5 ja, #6 ko, #7 nl, #8 en (hai tac),
  #9 pl, #10 pt, #11 ro, #12 ru, #13 sv, #14 zh-Hant, #15 zh-Hans).
  Chi kiem nhom du 15 gia tri (859 base) — nhom thieu thi khong chac chan.
- 9 Kings: nhom 30 chuoi (term + 29 ban dich) theo thu tu slot da biet:
  [term, en, pt, ja, zh, de, es, ko, fr, ru, it, bg, cs, tl, el, hu, id,
  pl, ro, sk, tr, uk, da, nl, fi, no, sv]. Chi kiem nhom du 30.
- Kiem tra thu tu key (y 7): key term dau tien + thu tu term so voi file
  en.json cua Joseph (9 Kings).

Chay: python3 exp/check_split_impurity.py
Doc: exp/out/extract_5games/{passpartout,9kings}{,_merged}/resources.monobehaviour.*.json
Ket qua in ra stdout + ghi summary vao exp/out/extract_5games/impurity.json.
"""
import json
import os
import re

OUT = "/home/hatch/workspace/pb3_web/exp/out/extract_5games"
JOSEPH_EN = "/home/hatch/workspace/user/files/en.json"

PP_LANGS = ["de", "en", "es", "fr", "ja", "ko", "nl", "en", "pl", "pt",
            "ro", "ru", "sv", "zh", "zh"]
# 9 Kings: 30 slot (term + 29 ban dich); pt/es/id co 2 bien the (vd 2 giong
# tieng Tay Ban Nha), giu NGUYEN ca 2 de chi so slot khop voi file gop.
K9_SLOTS = ["term", "en", "pt", "pt", "ja", "zh", "de", "es", "es", "ko",
            "fr", "ru", "it", "bg", "cs", "tl", "el", "hu", "id", "id",
            "pl", "ro", "sk", "tr", "uk", "da", "nl", "fi", "no", "sv"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def base_key(k):
    k = re.sub(r"\.x\d+$", "", k)
    k = re.sub(r"\.p\d+$", "", k)
    return re.sub(r"#\d+$", "", k)


def group_merged(merged):
    """{base: [values theo thu tu xuat hien]} tu file gop #N."""
    groups = {}
    for k, v in merged.items():
        groups.setdefault(base_key(k), []).append(v)
    return groups


def check_split(split, groups, langs, target, term_map=None):
    """Doi chieu file tach voi ground truth.
    target: chi so ngon ngu dich trong `langs` (vd 'en' -> cac vi tri co
    langs[i]=='en'); term_map: {term: group_values} cho key dang term."""
    correct = wrong = ambiguous = unknown = 0
    wrong_by_lang = {}
    wrong_examples = []
    for key, value in split.items():
        group = None
        if term_map and key in term_map:
            group = term_map[key]
        elif term_map and base_key(key) in term_map:
            # Key trung co hau to on dinh .p<path_id> (vd ban dich trung
            # term tu object khac): doi chieu theo term goc.
            group = term_map[base_key(key)]
        else:
            group = groups.get(base_key(key))
        if group is None or len(group) != len(langs):
            unknown += 1
            continue
        # Gia tri xuat hien o bao nhieu vi tri ngon ngu trong nhom?
        hits = [langs[i] for i, g in enumerate(group) if g == value]
        if target in hits and len(set(hits)) == 1:
            correct += 1
        elif len(set(hits)) > 1:
            ambiguous += 1
        elif hits:
            wrong += 1
            wrong_by_lang[hits[0]] = wrong_by_lang.get(hits[0], 0) + 1
            if len(wrong_examples) < 5:
                wrong_examples.append((key, value, hits[0]))
        else:
            unknown += 1
    return {"total": len(split), "correct": correct, "wrong": wrong,
            "ambiguous": ambiguous, "unknown": unknown,
            "wrong_by_lang": wrong_by_lang, "wrong_examples": wrong_examples}


def main():
    report = {}

    # ---- Passpartout ----
    pp_merged = load(OUT + "/passpartout_merged/resources.monobehaviour.json")
    pp_groups = group_merged(pp_merged)
    full15 = {b: g for b, g in pp_groups.items() if len(g) == 15}
    print("Passpartout: %d base, %d nhom du 15" % (len(pp_groups), len(full15)))
    for lang in ("en", "zh"):
        split = load(OUT + "/passpartout/resources.monobehaviour.%s.json" % lang)
        r = check_split(split, full15, PP_LANGS, lang)
        print("Passpartout .%s.json: total=%d dung=%d sai=%d mo=%d khong-ro=%d" %
              (lang, r["total"], r["correct"], r["wrong"], r["ambiguous"], r["unknown"]))
        print("   sai theo ngon ngu that:", r["wrong_by_lang"])
        for k, v, t in r["wrong_examples"]:
            print("   SAI vd: %s = %r (that la %s)" % (k, v, t))
        print("   #N trong file tach:", sum(1 for k in split if "#" in k),
              "| key dau:", next(iter(split)))
        report["passpartout_" + lang] = r

    # ---- 9 Kings ----
    # Nhom term KHONG phai theo base key (moi chuoi co key s<n> rieng) ma
    # theo CHUOI LIEN TIEP trong object bang term, cat tai ranh gioi term
    # (dung chinh _split_term_chunks cua extraction — nhom ngan 27-29
    # chuoi ton tai nen khong cat co dinh theo 30).
    import sys
    sys.path.insert(0, "/home/hatch/workspace/pb3_web/site/pb3")
    from sdk.extraction import _split_term_chunks
    k9_merged = load(OUT + "/9kings_merged/resources.monobehaviour.json")
    i2_pairs = []  # (key, value) cua object bang term, theo thu tu file
    for k, v in k9_merged.items():
        if re.match(r"^I2Languages\.s\d+$", base_key(k)):
            i2_pairs.append((k, v))
    term_map = {}
    n_full = n_short = 0
    for has_term, chunk in _split_term_chunks(i2_pairs):
        if not has_term:
            continue
        vals = [v for _k, v in chunk]
        if len(vals) == 30:
            term_map[vals[0]] = vals
            n_full += 1
        else:
            n_short += 1
    print("9Kings: object bang term %d chuoi, %d nhom du 30, %d nhom ngan" %
          (len(i2_pairs), n_full, n_short))
    for lang in ("en", "zh"):
        split = load(OUT + "/9kings/resources.monobehaviour.%s.json" % lang)
        # Chi kiem cac entry dang term (nhom bang term); entry dang duong
        # dan (object khac) khong co ground truth slot -> khong-ro.
        r = check_split(split, {}, K9_SLOTS, lang, term_map=term_map)
        print("9Kings .%s.json: total=%d dung=%d sai=%d mo=%d khong-ro=%d" %
              (lang, r["total"], r["correct"], r["wrong"], r["ambiguous"], r["unknown"]))
        print("   sai theo ngon ngu that:", r["wrong_by_lang"])
        for k, v, t in r["wrong_examples"]:
            print("   SAI vd: %s = %r (that la %s)" % (k, v, t))
        print("   #N trong file tach:", sum(1 for k in split if "#" in k),
              "| key dau:", next(iter(split)))
        report["9kings_" + lang] = r

    # ---- Thu tu key (y 7): term 9 Kings so voi file Joseph ----
    joseph = load(JOSEPH_EN)
    joseph_terms = list(joseph.keys())
    k9_en = load(OUT + "/9kings/resources.monobehaviour.en.json")
    split_terms = [k for k in k9_en if k in term_map]
    common = [t for t in joseph_terms if t in split_terms]
    order_ok = [t for t in split_terms if t in joseph_terms] == common
    print("9Kings thu tu term: key term dau tien trong file tach: %s" %
          (split_terms[0] if split_terms else None))
    print("9Kings thu tu term: %d term chung voi file Joseph, thu tu khop: %s" %
          (len(common), order_ok))
    print("9Kings file Joseph: %d key, key dau: %s" %
          (len(joseph_terms), joseph_terms[0]))
    report["order_9kings"] = {"first_split_term": split_terms[0] if split_terms else None,
                              "first_joseph_term": joseph_terms[0],
                              "common_terms": len(common), "order_match": order_ok}
    # ---- Dem key hau to on dinh .p (trung key trong cung ngon ngu) ----
    for game in ("passpartout", "9kings"):
        for lang in ("en", "zh"):
            split = load(OUT + "/%s/resources.monobehaviour.%s.json" % (game, lang))
            n_p = sum(1 for k in split if ".p" in k)
            print("%s .%s.json: %d/%d key co hau to on dinh .p<path_id>" %
                  (game, lang, n_p, len(split)))
            report["%s_%s_p_keys" % (game, lang)] = n_p

    json.dump(report, open(OUT + "/impurity.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("IMPURITY WRITTEN", OUT + "/impurity.json")


if __name__ == "__main__":
    main()
