#!/usr/bin/env python3
"""So sanh doc TMP_FontAsset cua BearTea (sdk/tmp_font.py) voi dap an doc lap
tmp_expected.json cua studio (vendor/.../tests/make_tmp_ipas.py).

Dung: python3 exp/check_tmp_fixture.py [THU_MUC_FIXTURE]
  (mac dinh /tmp/item7_fixtures; sinh bang:
   python3 vendor/.../tests/make_tmp_ipas.py THU_MUC)

In JSON tom tat ra stdout, exit 0 neu khop het, 1 neu lech.
Kiem ca diem BearTea hon studio: doc duoc TMP trong node bundle UnityFS
(InBundle SDF) — studio chi canh bao, khong doc node.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site", "pb3"))
from sdk import tmp_font as tf  # noqa: E402


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/item7_fixtures"
    exp = json.load(open(os.path.join(d, "tmp_expected.json"),
                         encoding="utf-8"))
    res = tf.scan_source(os.path.join(d, "tmp_all.ipa"))
    by = {it.get("name"): it for it in res["items"]}
    fails = []

    def chk(cond, msg):
        if not cond:
            fails.append(msg)

    for name, ex in exp["assets"].items():
        it = by.get(name)
        if it is None:
            fails.append("%s: thieu asset" % name)
            continue
        chk(it.get("status") == ex.get("status"),
            "%s: status %r != %r" % (name, it.get("status"), ex.get("status")))
        fi = it.get("font_info") or {}
        chk(fi.get("name") == ex.get("face"),
            "%s: face %r != %r" % (name, fi.get("name"), ex.get("face")))
        if ex.get("declared") is not None:
            chk(fi.get("declared_char_count") == ex["declared"],
                "%s: declared %r != %r"
                % (name, fi.get("declared_char_count"), ex["declared"]))
        if ex.get("atlas"):
            chk((fi.get("atlas_width"), fi.get("atlas_height"),
                 fi.get("atlas_basis")) == tuple(ex["atlas"]),
                "%s: atlas %r != %r"
                % (name, (fi.get("atlas_width"), fi.get("atlas_height"),
                          fi.get("atlas_basis")), ex["atlas"]))
        if ex.get("status") == "glyph_table_not_found":
            chk(it.get("glyph_table") is None and it.get("code_points") is None
                and it.get("vietnamese") is None,
                "%s: khong duoc bia danh sach ky tu" % name)
            chk(bool((it.get("diagnostics") or {}).get("count_hits")),
                "%s: thieu diagnostics.count_hits" % name)
            continue
        dup = 2 if ex.get("problem") == "id glyph trùng" else 0
        gt = it.get("glyph_table") or {}
        cp = it.get("code_points") or {}
        vi = it.get("vietnamese") or {}
        chk(gt.get("count") == ex.get("count", 0) + dup,
            "%s: glyph count %r != %r" % (name, gt.get("count"), ex.get("count", 0) + dup))
        chk(cp.get("count") == ex.get("count"),
            "%s: code points %r != %r" % (name, cp.get("count"), ex.get("count")))
        chk(cp.get("ranges") == ex.get("ranges"),
            "%s: ranges lech" % name)
        chk(vi.get("covered") == ex.get("vi_covered")
            and vi.get("missing_count") == ex.get("vi_missing")
            and vi.get("level") == ex.get("vi_level"),
            "%s: VI %r/%r %r != %r/%r %r"
            % (name, vi.get("covered"), vi.get("missing_count"), vi.get("level"),
               ex.get("vi_covered"), ex.get("vi_missing"), ex.get("vi_level")))
        chk(vi.get("ascii_letters_ok") == ex.get("ascii_ok"),
            "%s: ascii_ok %r != %r" % (name, vi.get("ascii_letters_ok"), ex.get("ascii_ok")))
        if ex.get("problem"):
            chk(any(ex["problem"] in p for p in it.get("problems", [])),
                "%s: thieu problem %r" % (name, ex["problem"]))
        else:
            chk(not it.get("problems"),
                "%s: co problems khong mong %r" % (name, it.get("problems")))
        chk(gt.get("placeholder_glyphs", 0) == (ex.get("placeholders") or 0),
            "%s: placeholder_glyphs %r != %r"
            % (name, gt.get("placeholder_glyphs"), ex.get("placeholders") or 0))
    for nm, ex in exp["refs"].items():
        it = by.get(nm)
        if it is None:
            continue
        got = {k: sorted(x["name"] for x in v) for k, v in it["refs"].items()}
        want = {k: sorted(v) for k, v in ex.items()}
        chk(got == want, "%s: refs %r != %r" % (nm, got, want))
        mf = sorted(x["name"] for x in it.get("matched_fonts", []))
        chk(mf == sorted(exp["matched_fonts"][nm]),
            "%s: matched_fonts %r != %r" % (nm, mf, exp["matched_fonts"][nm]))
    # Diem hon studio: doc duoc node bundle.
    ib = by.get("InBundle SDF")
    chk(ib is not None and ib.get("status") == "parsed",
        "InBundle SDF: BearTea phai doc duoc node bundle (hon studio)")
    chk(res["bundle_nodes"] == 1, "bundle_nodes %r != 1" % res["bundle_nodes"])
    # Byte that DATA WING.
    raw = open(os.path.join(d, "tmp_real_xolonium.bin"), "rb").read()
    dec = tf.decode_object(raw, "<")
    rx = exp["real_xolonium"]
    chk(dec["status"] == "parsed" and dec["font_info"]["name"] == rx["face"]
        and dec["font_info"]["point_size"] == rx["point_size"]
        and (dec["font_info"]["atlas_width"], dec["font_info"]["atlas_height"])
        == tuple(rx["atlas"]) and dec["glyph_table"]["count"] == rx["count"]
        and dec["vietnamese"]["covered"] == rx["covered"]
        and dec["vietnamese"]["missing_count"] == rx["missing"]
        and dec["vietnamese"]["level"] == rx["level"]
        and dec["glyph_table"]["placeholder_glyphs"] == rx["placeholders"]
        and dec["code_points"]["ranges"] == rx["ranges"],
        "xolonium (byte that DATA WING) lech")
    # Glyph plan.
    plan = res["glyph_plan"]
    chk(plan["schema"] == "tmp_glyph_plan/1", "glyph plan schema lech")
    chk(plan["missing_total"] == 142,
        "glyph plan missing_total %r != 142" % plan["missing_total"])
    chk(len(plan["chars_text"]) == 142, "chars_text dai %d != 142"
        % len(plan["chars_text"]))
    chk(len(plan["targets"]) == 15,
        "glyph plan targets %d != 15" % len(plan["targets"]))
    out = {"assets": len(res["items"]), "expected": len(exp["assets"]),
           "summary": res["summary"], "bundle_nodes": res["bundle_nodes"],
           "files_scanned": res["files_scanned"],
           "glyph_plan_missing_total": plan["missing_total"],
           "fails": fails}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
