#!/usr/bin/env python3
"""Fixture phần (D): IPA tổng hợp -> bridge.font_list/font_unify.

IPA gồm: GameFont.ttf thiếu 4 ký tự, FullFont.ttf đủ 142/142, Game.otf
(OTF stub), resources.assets tổng hợp (1 TMP_FontAsset thiếu 42 ký tự +
1 Font object nhúng). Kết quả: exp/out/font_unify/fixture.json.
Chạy: python3 exp/check_font_unify_fixture.py
"""
import base64
import json
import os
import sys
import zipfile

SITE = "/home/hatch/workspace/pb3_web/site"
EXP = "/home/hatch/workspace/pb3_web/exp"
OUT = EXP + "/out/font_unify"

sys.path.insert(0, SITE + "/pb3")
import bridge  # noqa: E402
from sdk import font as font_sdk  # noqa: E402
from sdk import tmp_font as tf  # noqa: E402
from tests.fixtures.font import build_fixtures as fx  # noqa: E402
from tests.test_tmp_font import _sf_with_assets  # noqa: E402

MISSING = [0x1EA0, 0x1EA1, 0x300, 0x301]


def main():
    os.makedirs(OUT, exist_ok=True)
    print("boot:", bridge.boot())
    have = [c for c in fx.vietnamese_cps() if c not in MISSING]
    game_ttf = fx.build_ttf(fx.ascii_cps() + have, name="GameFont")
    full_ttf = fx.build_font_full_vietnamese()
    otf = fx.build_otf_stub()
    letters = tf.vietnamese_letters_134()
    ids = list(range(32, 127)) + letters[:100]
    sf = _sf_with_assets([(100, "GameFont SDF",
                           {"face": "GameFont", "ids": ids})])
    ipa = os.path.join(OUT, "fixture.ipa")
    with zipfile.ZipFile(ipa, "w") as zf:
        zf.writestr("Payload/x.app/Data/fonts/GameFont.ttf", game_ttf)
        zf.writestr("Payload/x.app/Data/fonts/FullFont.ttf", full_ttf)
        zf.writestr("Payload/x.app/Data/fonts/Game.otf", otf)
        zf.writestr("Payload/x.app/Data/resources.assets", sf)
    listed = json.loads(bridge.font_list(ipa))
    donor_tmp = "/tmp/BeVietnamPro-Regular.ttf"
    open(donor_tmp, "wb").write(base64.b64decode(
        open(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "rb").read()))
    unified = json.loads(bridge.font_unify(ipa, donor_tmp))
    import zipfile as zf_mod
    zip_names = []
    merged_full = None
    with zf_mod.ZipFile("/tmp/font_unified.zip") as zf:
        zip_names = sorted(zf.namelist())
        if "GameFont.vi.ttf" in zip_names:
            rep = font_sdk.coverage(zf.read("GameFont.vi.ttf"),
                                    where="GameFont.vi.ttf")
            merged_full = {"covered": int(rep.covered),
                           "total": int(rep.total),
                           "is_full": bool(rep.is_full)}
    plan = unified.get("plan") or {}
    acts = {e["kind"] + ":" + e["name"]: e["action"]
            for e in plan.get("fonts") or []}
    res = {
        "list_ok": listed.get("ok"),
        "fonts": len(listed.get("fonts") or []),
        "by_kind": (listed.get("summary") or {}).get("by_kind"),
        "by_status": (listed.get("summary") or {}).get("by_status"),
        "ttf_partial": None,
        "tmp_partial": None,
        "unity_font_unreadable": None,
        "otf_partial": None,
        "unify_ok": unified.get("ok"),
        "plan_counts": plan.get("counts"),
        "actions": acts,
        "merged": unified.get("merged"),
        "merged_full": merged_full,
        "zip_names": zip_names,
        "donor_in_zip": "BeVietnamPro-Regular.ttf" in zip_names,
    }
    for f in listed.get("fonts") or []:
        if f["kind"] == "ttf" and f["name"] == "GameFont.ttf":
            res["ttf_partial"] = {
                "covered": f["covered"],
                "missing_codepoints": f["missing_codepoints"]}
        if f["kind"] == "tmp_font_asset":
            res["tmp_partial"] = {
                "covered": f["covered"],
                "missing_total": f["missing_total"]}
        if f["kind"] == "unity_font":
            res["unity_font_unreadable"] = {
                "status": f["status"], "path_id": f["path_id"]}
        if f["kind"] == "otf":
            res["otf_partial"] = {"status": f["status"]}
    json.dump(res, open(os.path.join(OUT, "fixture.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
