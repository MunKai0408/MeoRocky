#!/usr/bin/env python3
"""Kiem chung tai tao SDF TextMeshPro bang SDK (pb3/sdk/sdf.py) tren game that.

Voi moi asset TMP SDF tim thay trong cac IPA that (9K, EK2, PB3, Tom):
  1. Giai ma bang glyph bang sdk/tmp_font.py (ca layout cu 36B va moi 52B).
  2. Lay atlas that (Texture2D "<ten> Atlas", du lieu trong .resS).
  3. Lay font nguon nhung (Font object cung ten) — neu khong co thi dung
     font he thong metric-compatible (ghi ro la proxy).
  4. Voi moi glyph: lay outline tu font nguon bang sdk/sdf.py, dat vao vi
     tri rect that, raster SDF bang render_sdf_cell, so sanh voi atlas that
     tren cua so [rect mo rong padding] (loai pixel trong 8px cua rect ke).
  5. Bao cao MAE/median/max theo glyph va theo atlas.

Nguong dat (dinh truoc): median glyph MAE < 3.0 don vi gia tri
(~0.15px lech bien — khong thay bang mat). PASS/FAIL theo nguong nay.

Chay (tu goc repo):
    python3 exp/verify_sdf_regen.py
    python3 exp/verify_sdf_regen.py --ipapb3 <path> --ipaek2 <path> ...
IPA nao thieu thi BO QUA co ghi ro (khong fail) de harness chay duoc
tren may khong co file game. Ket qua in ra stdout + ghi
exp/sdf_regen_results.md + .json (ghi file best-effort: neu moi truong
chan ghi thi chi canh bao, van in stdout).

Nhan: moi so trong bao cao la [DA DO THAT] tren file game that.
"""
import argparse
import hashlib
import json
import math
import os
import struct
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "pb3"))

from sdk import sdf as sdfmod  # noqa: E402
from sdk import tmp_font as tf  # noqa: E402
from sdk import unity as uni  # noqa: E402
from core.errors import PB3Error  # noqa: E402

DEFAULTS = {
    "ipa9k": "/home/hatch/workspace/test-ipas/9kings-v1.0.6.ipa",
    "ipaek2": "/home/hatch/workspace/test-ipas/ek2-v0.0.18.ipa",
    "ipapb3": "/home/hatch/workspace/test-ipas/pb3-v1.1.7.ipa",
    "ipatom": "/home/hatch/workspace/test-ipas/"
              "My_Talking_Tom_v26.5.1_Decrypted.ipa",
}

PASS_MEDIAN_MAE = 3.0
NEIGHBOUR_EXCL = 8  # px: loai pixel gan rect ke (SDF cua glyph ke lan sang)

KNOWN_TAGS = {b'OS/2', b'cmap', b'cvt ', b'fpgm', b'gasp', b'glyf', b'head',
              b'hhea', b'hmtx', b'loca', b'maxp', b'name', b'post', b'prep',
              b'CFF ', b'VDMX', b'kern', b'EBDT', b'EBLC', b'BASE', b'GSUB',
              b'GPOS', b'GDEF', b'JSTF', b'MATH', b'COLR', b'CPAL', b'SVG ',
              b'sbix', b'CBDT', b'CBLC', b'hdmx', b'LTSH', b'PCLT', b'VORG',
              b'EBSC', b'FFTM', b'LINO', b'DSIG'}

# Font he thong dung lam proxy khi IPA khong nhung font goc (ghi ro proxy).
PROXY_FONTS = {
    "courier new": "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "liberation sans": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
}


def _rd_str(b, off, endian):
    (n,) = struct.unpack_from(endian + "i", b, off)
    return b[off + 4:off + 4 + n].decode("latin-1")


def parse_texture2d(raw, endian):
    """Texture2D Unity 2022/6000 (khong type tree). Tra dict hoac None.

    Bo cuc truoc Width khac nhau theo ban Unity: 2022 co
    ForcedFallbackFormat(4) + 2 bool(2) + align -> Width o off+8;
    6000 (9K) chi co 4 byte -> Width o off+4. Thu ca 2, chon cai
    hop le (w/h trong [1,16384], complete >= w*h, complete > 0);
    uu tien bo cuc 2022 de khong doi hanh vi cu.
    """
    try:
        name = _rd_str(raw, 0, endian)
    except Exception:
        return None
    off = (4 + len(name.encode("latin-1")) + 3) & ~3
    width = height = complete = fmt = mip = None
    for pos in (((off + 4 + 2 + 3) & ~3), off + 4):
        try:
            w, h, comp = struct.unpack_from(endian + "iii", raw, pos)
            f = struct.unpack_from(endian + "i", raw, pos + 12)[0]
            m = struct.unpack_from(endian + "i", raw, pos + 16)[0]
        except Exception:
            continue
        if 1 <= w <= 16384 and 1 <= h <= 16384 and comp > 0 \
                and comp >= w * h:
            width, height, complete, fmt, mip = w, h, comp, f, m
            break
    if width is None:
        return None
    stream = None
    for p in range(len(raw) - 8, max(0, len(raw) - 120), -4):
        try:
            (plen,) = struct.unpack_from(endian + "i", raw, p)
        except Exception:
            continue
        if not (5 <= plen <= 80):
            continue
        if p + 4 + plen > len(raw):
            continue
        cand = raw[p + 4:p + 4 + plen]
        try:
            path = cand.decode("ascii")
        except UnicodeDecodeError:
            continue
        if not path.endswith(".resS"):
            continue
        if not all(32 <= ord(c) < 127 for c in path):
            continue
        try:
            (soffset,) = struct.unpack_from(endian + "Q", raw, p - 12)
            (ssize,) = struct.unpack_from(endian + "I", raw, p - 4)
        except Exception:
            continue
        stream = {"offset": soffset, "size": ssize, "path": path}
        break
    return {"name": name, "width": width, "height": height,
            "complete": complete, "format": fmt, "mip": mip,
            "stream": stream}


def extract_font(raw, endian):
    """Tim TTF/OTF trong bytes Font object. Tra (bytes, kind) hoac None."""
    for magic in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        i = raw.find(magic)
        while i != -1:
            if i + 12 <= len(raw):
                (num,) = struct.unpack_from(">H", raw, i + 4)
                if 5 <= num <= 40:
                    tags = [raw[i + 12 + k * 16:i + 16 + k * 16]
                            for k in range(num)]
                    if all(t in KNOWN_TAGS for t in tags):
                        end = 0
                        for k in range(num):
                            (o, ln) = struct.unpack_from(
                                ">II", raw, i + 12 + k * 16 + 8)
                            end = max(end, o + ln)
                        if 0 < end <= len(raw) - i:
                            kind = "otf" if magic == b"OTTO" else "ttf"
                            return raw[i:i + end], kind
            i = raw.find(magic, i + 1)
    return None


def _base_name(tmp_name):
    n = tmp_name
    for suf in (" SDF - DYNAMIC", " SDF - Fallback", " SDF"):
        if n.endswith(suf):
            n = n[:-len(suf)]
            break
    return n


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2:
        return float(xs[n // 2])
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _rects_topdown(records, layout, atlas_h):
    """Doi tat ca rect sang toa do top-down (x, y_top, w, h)."""
    out = []
    for r in records:
        if layout == "new":
            rx, ry_b, rw, rh = r["rect"]
            out.append((rx, atlas_h - ry_b - rh, rw, rh))
        else:
            out.append((r["x"], r["y"], r["width"], r["height"]))
    return out


def verify_asset(tmp_name, dec, atlas, font_data, font_kind,
                 font_source, max_glyphs=None):
    """So sanh SDF SDK vs atlas that cho 1 asset TMP. Tra dict ket qua."""
    gt = dec["glyph_table"]
    fi = dec.get("font_info") or {}
    layout = gt["layout"]
    pt = fi.get("point_size") or 0.0
    pad = fi.get("padding")
    aw = int(fi.get("atlas_width") or atlas["width"])
    ah = int(fi.get("atlas_height") or atlas["height"])
    if pad is None:
        pad = 5
    pad = int(pad)
    spread = pad + 1
    records = tf.read_glyph_records(
        dec["_raw"], gt["offset"], gt["count"], gt["stride"],
        dec["_endian"])
    atlas_bytes = atlas["bytes"]
    if len(atlas_bytes) != aw * ah:
        return {"name": tmp_name, "error": "atlas size mismatch: %d != %d*%d"
                % (len(atlas_bytes), aw, ah)}
    if not sdfmod.is_sdf_atlas(atlas_bytes):
        # Atlas bitmap (bien cung) — khong phai SDF, khong thuoc pham vi
        # tai tao SDF (vd ContinuumBold PvZ: 95% pixel 0/255).
        return {"name": tmp_name, "skip": "atlas la bitmap (khong phai SDF)"}
    try:
        font = sdfmod.parse_font(font_data)
    except PB3Error as e:
        return {"name": tmp_name, "error": "parse_font: %s" % e}
    gid2cp = {}
    for cp, gid in font.cmap.items():
        gid2cp.setdefault(gid, cp)
    rects_td = _rects_topdown(records, layout, ah)
    rows = []
    idxs = list(range(len(records)))
    if max_glyphs is not None and len(idxs) > max_glyphs:
        step = len(idxs) / float(max_glyphs)
        idxs = [int(i * step) for i in range(max_glyphs)]
    for i in idxs:
        r = records[i]
        rx, ry, rw, rh = rects_td[i]
        if rw <= 0 or rh <= 0:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "placeholder"})
            continue
        if layout == "new":
            cp = gid2cp.get(r["id"])
            if cp is None:
                rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                             "note": "no-cp"})
                continue
        else:
            cp = r["id"]
        segs, bbox, adv = sdfmod.glyph_segments(font, cp, pt)
        if not segs or bbox is None:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "no-segs"})
            continue
        bx0, by0, bx1, by1 = bbox
        # Doi doan sang toa do atlas y-len: ink-min -> goc tren-trai rect.
        segs_a = []
        for (x0, y0, x1, y1) in segs:
            segs_a.append((x0 - bx0 + rx, ah - ry - by1 + y0,
                           x1 - bx0 + rx, ah - ry - by1 + y1))
        # Cua so so sanh: rect mo rong padding, cat theo atlas.
        wx0 = max(0, int(math.floor(rx)) - pad)
        wy0 = max(0, int(math.floor(ry)) - pad)
        wx1 = min(aw, int(math.ceil(rx + rw)) + pad)
        wy1 = min(ah, int(math.ceil(ry + rh)) + pad)
        ww, wh = wx1 - wx0, wy1 - wy0
        if ww <= 0 or wh <= 0:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "empty-window"})
            continue
        cell = sdfmod.render_sdf_cell(
            segs_a, wx0, ah - wy0 - wh, ww, wh, spread)
        errs = []
        for j in range(wh):
            cy = wy0 + j
            for ix in range(ww):
                cx = wx0 + ix
                skip = False
                for k, (ox, oy, ow, oh) in enumerate(rects_td):
                    if k == i:
                        continue
                    if (ox - NEIGHBOUR_EXCL <= cx + 0.5 <= ox + ow + NEIGHBOUR_EXCL
                            and oy - NEIGHBOUR_EXCL <= cy + 0.5 <= oy + oh + NEIGHBOUR_EXCL):
                        skip = True
                        break
                if skip:
                    continue
                # Atlas raw la BOTTOM-UP (row 0 = day): cy top-down ->
                # row ah-1-cy trong bytes.
                real = atlas_bytes[(ah - 1 - cy) * aw + cx]
                pred = cell[j * ww + ix]
                errs.append(abs(real - pred))
        if not errs:
            rows.append({"id": r["id"], "n": 0, "mae": 0.0, "max": 0.0,
                         "note": "no-pixels"})
            continue
        rows.append({"id": r["id"], "cp": cp, "n": len(errs),
                     "mae": sum(errs) / len(errs), "max": max(errs),
                     "note": "ok"})
    ok = [r for r in rows if r["note"] == "ok"]
    maes = [r["mae"] for r in ok]
    res = {"name": tmp_name, "layout": layout, "point_size": pt,
           "padding": pad, "atlas": "%dx%d" % (aw, ah),
           "font_kind": font_kind, "font_source": font_source,
           "glyphs_total": len(records), "glyphs_ok": len(ok),
           "median_mae": _median(maes) if maes else 0.0,
           "mean_mae": (sum(maes) / len(maes)) if maes else 0.0,
           "pct_lt2": (100.0 * sum(1 for m in maes if m < 2) / len(maes)
                       if maes else 0.0),
           "pct_lt3": (100.0 * sum(1 for m in maes if m < 3) / len(maes)
                       if maes else 0.0),
           "worst": sorted(ok, key=lambda r: -r["mae"])[:5],
           "pass": bool(maes) and _median(maes) < PASS_MEDIAN_MAE}
    return res


def _sf_entries(zf):
    """Tim cac SerializedFile trong IPA (resources.assets / sharedassets* /
    data.unity3d) — tra [(entry_name, bytes)]."""
    out = []
    for n in zf.namelist():
        base = n.rsplit("/", 1)[-1]
        if base.endswith(".assets") or base in ("data.unity3d",):
            try:
                out.append((n, zf.read(n)))
            except KeyError:
                continue
    return out


def verify_ipa(path, label):
    """Kiem chung moi asset TMP SDF trong 1 IPA. Tra (results, skipped)."""
    results = []
    skipped = []
    if not os.path.exists(path):
        return results, ["%s: khong co file %s" % (label, path)]
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        return results, ["%s: khong mo duoc IPA: %s" % (label, e)]
    for entry, data in _sf_entries(zf):
        try:
            sf = uni.parse_serialized(data, where=entry)
        except Exception:
            continue
        # Atlas + font trong file serialized nay.
        textures = {}
        fonts = {}
        ress_cache = {}
        for o in sf.objects:
            try:
                raw = sf.object_bytes(o.path_id)
            except Exception:
                continue
            if o.class_id == 28:
                info = parse_texture2d(raw, sf.endian)
                if info and info.get("stream"):
                    # Trung ten (vd 9K co stub 1x1 + atlas that): uu tien
                    # cai co stream; chi ghi de neu cai cu khong co stream.
                    old = textures.get(info["name"])
                    if old is None or not old[1].get("stream"):
                        textures[info["name"]] = (o.path_id, info)
                elif info and info["name"] not in textures:
                    textures[info["name"]] = (o.path_id, info)
            elif o.class_id == 128:
                try:
                    nm = _rd_str(raw, 0, sf.endian)
                except Exception:
                    continue
                got = extract_font(raw, sf.endian)
                if got:
                    fonts[nm] = got
        for o in sf.objects:
            if o.class_id != 114:
                continue
            try:
                raw = sf.object_bytes(o.path_id)
            except Exception:
                continue
            try:
                dec = tf.decode_object(raw, sf.endian)
            except Exception as e:
                skipped.append("%s %s: decode loi %s" % (label, o.path_id, e))
                continue
            name = dec.get("name") or ""
            if "SDF" not in name or "DYNAMIC" in name:
                continue
            gt = dec.get("glyph_table")
            if not gt:
                skipped.append("%s %s: khong co bang glyph" % (label, name))
                continue
            atlas_key = name + " Atlas"
            if atlas_key not in textures:
                skipped.append("%s %s: khong co atlas %s"
                               % (label, name, atlas_key))
                continue
            _, tinfo = textures[atlas_key]
            st = tinfo["stream"]
            if st["path"] not in ress_cache:
                cands = [n for n in zf.namelist() if n.endswith(st["path"])]
                if not cands:
                    skipped.append("%s %s: khong co file resS %s"
                                   % (label, name, st["path"]))
                    continue
                ress_cache[st["path"]] = zf.read(cands[0])
            blob = ress_cache[st["path"]][st["offset"]:st["offset"] + st["size"]]
            if len(blob) != st["size"]:
                skipped.append("%s %s: atlas cat (%d != %d)"
                               % (label, name, len(blob), st["size"]))
                continue
            atlas = dict(tinfo)
            atlas["bytes"] = blob
            # Font nguon: cung ten, hoac theo family FaceInfo, hoac proxy.
            font_data = font_kind = font_source = None
            base = _base_name(name)
            if base in fonts:
                font_data, font_kind = fonts[base]
                font_source = "embedded:%s" % base
            else:
                fam = ((dec.get("font_info") or {}).get("family_name") or "")
                fam_key = fam.strip().lower()
                if fam and fam in fonts:
                    font_data, font_kind = fonts[fam]
                    font_source = "embedded:%s" % fam
                elif fam_key in PROXY_FONTS and os.path.exists(
                        PROXY_FONTS[fam_key]):
                    with open(PROXY_FONTS[fam_key], "rb") as fh:
                        font_data = fh.read()
                    font_kind = "ttf"
                    font_source = "PROXY:%s" % PROXY_FONTS[fam_key]
            if font_data is None:
                skipped.append("%s %s: khong co font nguon (base=%s, "
                               "family=%s)" % (label, name, base,
                               (dec.get("font_info") or {}).get("family_name")))
                continue
            dec["_raw"] = raw
            dec["_endian"] = sf.endian
            try:
                res = verify_asset(name, dec, atlas, font_data, font_kind,
                                   font_source)
            except PB3Error as e:
                skipped.append("%s %s: PB3Error %s" % (label, name, e))
                continue
            except Exception as e:
                skipped.append("%s %s: loi verify %s: %s"
                               % (label, name, type(e).__name__, e))
                continue
            if res.get("skip"):
                skipped.append("%s %s: %s" % (label, name, res["skip"]))
                continue
            results.append(res)
            res["game"] = label
            res["sf_entry"] = entry
    return results, skipped


def main():
    ap = argparse.ArgumentParser()
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k, default=v)
    args = ap.parse_args()
    labels = [("9K", args.ipa9k), ("EK2", args.ipaek2),
              ("PB3", args.ipapb3), ("Tom", args.ipatom)]
    all_results = []
    all_skipped = []
    for label, path in labels:
        res, sk = verify_ipa(path, label)
        all_results.extend(res)
        all_skipped.extend(sk)
    games_ok = sorted({r["game"] for r in all_results})
    lines = []
    lines.append("# Ket qua kiem chung tai tao SDF (exp/verify_sdf_regen.py)")
    lines.append("")
    lines.append("Nguong dat (dinh truoc): median glyph MAE < %.1f "
                 "(~0.15px lech bien)." % PASS_MEDIAN_MAE)
    lines.append("Mo hinh: v = clamp(round(126.5 + s*255/(2*spread))), "
                 "spread = padding + 1.")
    lines.append("")
    for r in all_results:
        if "error" in r:
            lines.append("- %s %s: LOI %s" % (r["game"], r["name"], r["error"]))
            print("%s %-28s ERROR %s" % (r["game"], r["name"], r["error"]))
            continue
        verdict = "PASS" if r["pass"] else "FAIL"
        lines.append("- %s %s: %s layout=%s pt=%s pad=%s atlas=%s "
                     "font=%s (%s) glyphs=%d/%d median MAE=%.2f "
                     "mean=%.2f <2: %.0f%% <3: %.0f%%" % (
                         r["game"], r["name"], verdict, r["layout"],
                         r["point_size"], r["padding"], r["atlas"],
                         r["font_kind"], r["font_source"],
                         r["glyphs_ok"], r["glyphs_total"],
                         r["median_mae"], r["mean_mae"],
                         r["pct_lt2"], r["pct_lt3"]))
        print("%s %-28s %s median MAE=%.2f mean=%.2f <3: %.0f%% "
              "(%d/%d glyphs) font=%s" % (
                  r["game"], r["name"], verdict, r["median_mae"],
                  r["mean_mae"], r["pct_lt3"], r["glyphs_ok"],
                  r["glyphs_total"], r["font_source"]))
        for w in r["worst"]:
            ch = ""
            if w.get("cp") and 32 <= w["cp"] < 127:
                ch = " %r" % chr(w["cp"])
            lines.append("    xau nhat: id=%s%s n=%d MAE=%.2f max=%.1f"
                         % (w["id"], ch, w["n"], w["mae"], w["max"]))
    lines.append("")
    lines.append("Game da kiem chung: %s" % (", ".join(games_ok) or "khong co"))
    for s in all_skipped:
        lines.append("Bo qua: %s" % s)
        print("SKIP:", s)
    n_pass = sum(1 for r in all_results if r.get("pass"))
    lines.append("")
    lines.append("Tong: %d asset, %d PASS, %d game."
                 % (len(all_results), n_pass, len(games_ok)))
    print("Tong: %d asset, %d PASS, %d game: %s"
          % (len(all_results), n_pass, len(games_ok), ", ".join(games_ok)))
    overall = bool(all_results) and n_pass == len(all_results) \
        and len(games_ok) >= 3
    lines.append("KET LUAN: %s" % ("PASS" if overall else "FAIL"))
    print("KET LUAN: %s" % ("PASS" if overall else "FAIL"))
    md = "\n".join(lines) + "\n"
    out = {"results": all_results, "skipped": all_skipped,
           "games": games_ok, "pass": overall,
           "threshold_median_mae": PASS_MEDIAN_MAE}
    for path, text in (
            (os.path.join(REPO_ROOT, "exp", "sdf_regen_results.md"), md),
            (os.path.join(REPO_ROOT, "exp", "sdf_regen_results.json"),
             json.dumps(out, ensure_ascii=False, indent=1))):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as e:
            print("CANH BAO: khong ghi duoc %s: %s (moi truong chan ghi?)"
                  % (path, e))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
