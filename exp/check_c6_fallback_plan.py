#!/usr/bin/env python3
"""Kiem tra ke hoach fallback C6 tren fixture studio (dung tu exp/verify_analysis_c6.mjs).

Quet tmp_all.ipa (18 TMP_FontAsset gia lap + 1 node bundle) + byte that
DATA WING (tmp_real_xolonium.bin), lap ke hoach fallback, doi chieu voi
ke hoach glyph muc 7: missing_total phai bang nhau; moi target cua glyph
plan co chu thieu phai co fallback tuong ung (cung ten, cung tham so
atlas goc, material_refs giu nguyen); asset du thi vao already_full;
khong asset nao bi bia danh sach ky tu. In JSON ra stdout, exit 0 neu
dat het.
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "site", "pb3"))
from sdk import tmp_font as tf  # noqa: E402

FAILS = []


def chk(cond, msg):
    if not cond:
        FAILS.append(msg)


def main(fx):
    ipa = os.path.join(fx, "tmp_all.ipa")
    res = tf.scan_source(ipa)
    gp = res["glyph_plan"]
    plan = tf.build_fallback_plan(res["items"], gp)
    # Byte that DATA WING: decode truc tiep (file .bin khong phai duong
    # scan_source nhan) roi lap item giong ket qua quet de tinh fallback.
    raw = open(os.path.join(fx, "tmp_real_xolonium.bin"), "rb").read()
    dec = tf.decode_object(raw, "<")
    xitem = {"status": dec["status"], "name": dec.get("name"),
             "source": "tmp_real_xolonium.bin", "path_id": None,
             "font_info": dec.get("font_info"),
             "glyph_table": dec.get("glyph_table"),
             "vietnamese": dec.get("vietnamese"),
             "refs": dec.get("refs") or {}}
    xplan = tf.build_fallback_plan([xitem])

    chk(plan["schema"] == "tmp_fallback_plan/1",
        "schema %r != tmp_fallback_plan/1" % plan["schema"])
    chk(plan["missing_total"] == gp["missing_total"],
        "fallback missing_total %d != glyph plan %d"
        % (plan["missing_total"], gp["missing_total"]))
    chk(plan["missing_total"] == 142,
        "missing_total %d != 142 (ky vong fixture)" % plan["missing_total"])
    chk(plan["chars_text"] == gp["chars_text"],
        "chars_text fallback != glyph plan")
    # Moi target glyph plan co chu thieu phai co fallback.
    need = {t["name"] for t in gp["targets"]
            if t.get("missing_letters") or t.get("missing_marks")}
    got = {f["name"] for f in plan["fallbacks"]}
    chk(need == got, "fallback thieu/thua target: thieu %s, thua %s"
        % (sorted(need - got), sorted(got - need)))
    by_name = {f["name"]: f for f in plan["fallbacks"]}
    by_target = {t["name"]: t for t in gp["targets"]}
    for name, fb in by_name.items():
        t = by_target[name]
        chk(fb["fallback_name"].endswith(" Vietnamese Fallback"),
            "%s: fallback_name %r" % (name, fb["fallback_name"]))
        chk(fb["base_point_size"] == t["point_size"],
            "%s: point_size %r != %r" % (name, fb["base_point_size"], t["point_size"]))
        chk(fb["base_padding"] == t["padding"],
            "%s: padding %r != %r" % (name, fb["base_padding"], t["padding"]))
        chk((fb["base_atlas_width"], fb["base_atlas_height"])
            == (t["atlas_width"], t["atlas_height"]),
            "%s: atlas goc %r != %r" % (name, (fb["base_atlas_width"], fb["base_atlas_height"]),
                                        (t["atlas_width"], t["atlas_height"])))
        chk(fb["material_refs"] == t["material_refs"],
            "%s: material_refs doi" % name)
        chk(fb["material_unchanged"] is True, "%s: material_unchanged" % name)
        chk(fb["missing_total"] == len(fb["missing_letters_codepoints"])
            + len(fb["missing_marks_codepoints"]),
            "%s: missing_total lech" % name)
        chk(len(fb["chars_text"]) == fb["missing_total"],
            "%s: chars_text dai %d != missing_total %d"
            % (name, len(fb["chars_text"]), fb["missing_total"]))
        chk(256 <= fb["fallback_atlas_width"] <= 1024
            and 256 <= fb["fallback_atlas_height"] <= 1024,
            "%s: atlas fallback ngoai [256,1024]: %dx%d"
            % (name, fb["fallback_atlas_width"], fb["fallback_atlas_height"]))
        chk(len(fb["editor_steps"]) == 5, "%s: editor_steps" % name)
    # SourceCodePro: du 134 chu cai, chi thieu 8 dau -> fallback chi 8 dau.
    scp = by_name.get("SourceCodePro SDF")
    if scp is not None:
        chk(scp["missing_letters_codepoints"] == []
            and scp["missing_marks_codepoints"] == [0x300, 0x301, 0x302,
                                                    0x303, 0x306, 0x309,
                                                    0x31B, 0x323],
            "SourceCodePro fallback lech: %r / %r"
            % (scp["missing_letters_codepoints"],
               scp["missing_marks_codepoints"]))
    # CJK: thieu toan bo 142.
    cjk = by_name.get("CJK SDF")
    if cjk is not None:
        chk(cjk["missing_total"] == 142, "CJK missing_total %d != 142"
            % cjk["missing_total"])
        chk((cjk["fallback_atlas_width"], cjk["fallback_atlas_height"])
            == (1024, 1024), "CJK atlas fallback %dx%d != 1024x1024"
            % (cjk["fallback_atlas_width"], cjk["fallback_atlas_height"]))
    # DATA WING that: 36/134 -> thieu 98 chu + 8 dau = 106.
    chk(xplan["missing_total"] == 106,
        "Xolonium missing_total %d != 106" % xplan["missing_total"])
    chk(xplan["fallbacks_needed"] == 1,
        "Xolonium fallbacks_needed %d != 1" % xplan["fallbacks_needed"])
    # 4 asset glyph_table_not_found (NoGlyphs, OutOfAtlas, ManyDup,
    # TooManyPlaceholders — xem item7_results.md §3): khong lap duoc ke
    # hoach, khong bia danh sach ky tu.
    chk(plan["unplanned_count"] == 4,
        "unplanned %d (ky vong 4 tren tmp_all.ipa)" % plan["unplanned_count"])
    chk({u["name"] for u in plan["unplanned"]} == {
        "NoGlyphs SDF", "OutOfAtlas SDF", "ManyDup SDF",
        "TooManyPlaceholders SDF"},
        "unplanned sai ten: %r" % [u["name"] for u in plan["unplanned"]])
    print(json.dumps({
        "fails": FAILS,
        "assets": len(res["items"]),
        "glyph_plan_missing_total": gp["missing_total"],
        "fallback_missing_total": plan["missing_total"],
        "fallbacks_needed": plan["fallbacks_needed"],
        "already_full_count": plan["already_full_count"],
        "unplanned_count": plan["unplanned_count"],
        "fallback_names": [f["fallback_name"] for f in plan["fallbacks"]],
        "xolonium_missing_total": xplan["missing_total"],
        "xolonium_fallbacks": [f["fallback_name"]
                               for f in xplan["fallbacks"]],
    }, ensure_ascii=False, indent=1))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/c6_fx"))
