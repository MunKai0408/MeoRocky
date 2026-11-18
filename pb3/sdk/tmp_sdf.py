"""Tai tao atlas SDF TextMeshPro cho 1 asset (dung boi bridge + exp).

Luong: decode TMP object (sdk/tmp_font) -> tim font nguon nhung
(Font object class 128, cung ten/family) -> tim atlas Texture2D
("<ten> Atlas", du lieu trong .resS) -> generate_tmp_atlas (sdk/sdf,
them codepoint moi) -> measure_atlas_error (sdk/sdf, so atlas moi
voi atlas that tren glyph cu) -> encode bang glyph moi.

Chi tai tao TRONG CUNG kich thuoc atlas (fail-closed neu khong xep
vua). Ghi nguoc vao file game (M1 apply_monobehaviour_patch + thay
atlas trong resS) do bridge/gate lam, khong lam o day.

Chi stdlib, Python 3.9.
"""
from __future__ import annotations

import struct
from typing import Callable, Dict, List, Optional

from core.errors import PB3Error
from sdk import sdf as sdfmod
from sdk import tmp_font as tf

TEXTURE_CLASS_ID = 28
FONT_CLASS_ID = 128

KNOWN_TAGS = {b'OS/2', b'cmap', b'cvt ', b'fpgm', b'gasp', b'glyf', b'head',
              b'hhea', b'hmtx', b'loca', b'maxp', b'name', b'post', b'prep',
              b'CFF ', b'VDMX', b'kern', b'EBDT', b'EBLC', b'BASE', b'GSUB',
              b'GPOS', b'GDEF', b'JSTF', b'MATH', b'COLR', b'CPAL', b'SVG ',
              b'sbix', b'CBDT', b'CBLC', b'hdmx', b'LTSH', b'PCLT', b'VORG',
              b'EBSC', b'FFTM', b'LINO', b'DSIG'}


def _rd_str(b, off, endian):
    (n,) = struct.unpack_from(endian + "i", b, off)
    return b[off + 4:off + 4 + n].decode("latin-1")


def parse_texture2d(raw, endian):
    """Texture2D Unity 2022/6000 (khong type tree). Tra dict hoac None.

    stream: {offset u64, size u32, path} neu co (du lieu trong .resS),
    None neu anh nhung truc tiep (khong ho tro o day).
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


def find_source_font(sf, tmp_name, face_family=None):
    """Tim font nguon nhung cho 1 TMP asset. Tra (bytes, kind, source)
    hoac None. Uu tien: cung ten (bo hau to SDF) -> family FaceInfo."""
    base = _base_name(tmp_name)
    cands = {}
    for o in sf.objects:
        if o.class_id != FONT_CLASS_ID:
            continue
        try:
            raw = sf.object_bytes(o.path_id)
            nm = _rd_str(raw, 0, sf.endian)
        except Exception:
            continue
        got = extract_font(raw, sf.endian)
        if got:
            cands[nm] = got
    if base in cands:
        data, kind = cands[base]
        return data, kind, "embedded:%s" % base
    if face_family and face_family in cands:
        data, kind = cands[face_family]
        return data, kind, "embedded:%s" % face_family
    return None


def find_atlas_texture(sf, tmp_name):
    """Tim Texture2D atlas "<ten> Atlas". Tra (path_id, info) hoac None.

    Co the co nhieu Texture2D cung ten (vd 9K: 1 stub 1x1 khong stream
    + 1 atlas that co stream) — uu tien cai co stream (.resS); neu
    khong co cai nao co stream thi tra cai dau tien.
    """
    want = tmp_name + " Atlas"
    first = None
    for o in sf.objects:
        if o.class_id != TEXTURE_CLASS_ID:
            continue
        try:
            raw = sf.object_bytes(o.path_id)
        except Exception:
            continue
        info = parse_texture2d(raw, sf.endian)
        if info and info["name"] == want:
            if first is None:
                first = (o.path_id, info)
            if info.get("stream"):
                return o.path_id, info
    return first


def regen_tmp_asset(sf, tmp_path_id, extra_codepoints,
                    ress_slice: Callable[[str, int, int], bytes]):
    """Tai tao atlas SDF cho 1 TMP asset (dry-run, khong ghi file game).

    sf: SerializedFile da parse. tmp_path_id: path_id cua TMP asset.
    extra_codepoints: list int (vd chu Viet thieu) can them vao atlas.
    ress_slice: ham(path, offset, size) -> bytes, doc 1 lat cat cua
    file .resS (trong IPA hoac roi tren dia) — chi doc dung phan atlas,
    khong nap ca file resS. Tra dict bao cao + new_atlas (bytes) +
    new_table (bytes) + new_count. Nem PB3Error neu thieu font/atlas
    hoac khong xep vua (fail-closed).
    """
    try:
        raw = sf.object_bytes(tmp_path_id)
    except Exception as e:
        raise PB3Error("khong doc duoc TMP object %s: %s" % (tmp_path_id, e))
    dec = tf.decode_object(raw, sf.endian)
    name = dec.get("name") or ("TMP #%s" % tmp_path_id)
    gt = dec.get("glyph_table")
    if not gt:
        raise PB3Error("asset %s: khong co bang glyph (%s)"
                       % (name, dec.get("reason") or dec.get("status")))
    fi = dec.get("font_info") or {}
    layout = gt["layout"]
    pt = fi.get("point_size")
    pad = fi.get("padding")
    pad_estimated = False
    if pt is None:
        raise PB3Error("asset %s: thieu PointSize (FaceInfo)" % name)
    if pad is None:
        # FaceInfo moi (TMP 3.x) khong chua padding — mac dinh 5 (pho
        # bien nhat o PB3: Aniara dung 5, chi LiberationSans dung 9).
        # Ghi ro la uoc luong; neu MAE cao thi padding co the sai.
        pad = 5
        pad_estimated = True
    pad = int(pad)
    # Font nguon.
    fam = fi.get("family_name")
    found = find_source_font(sf, name, fam)
    if found is None:
        raise PB3Error("asset %s: khong co font nguon nhung (base=%s, "
                       "family=%s)" % (name, _base_name(name), fam))
    font_data, font_kind, font_source = found
    # Atlas that.
    at = find_atlas_texture(sf, name)
    if at is None:
        raise PB3Error("asset %s: khong co atlas %s"
                       % (name, name + " Atlas"))
    _apid, tinfo = at
    st = tinfo.get("stream")
    if not st:
        raise PB3Error("asset %s: atlas khong stream (.resS)" % name)
    aw, ah = int(tinfo["width"]), int(tinfo["height"])
    if st["size"] != aw * ah:
        raise PB3Error("asset %s: atlas stream %d != %dx%d"
                       % (name, st["size"], aw, ah))
    ress = ress_slice(st["path"], st["offset"], st["size"])
    atlas_bytes = ress
    if len(atlas_bytes) != st["size"]:
        raise PB3Error("asset %s: atlas cat (%d != %d)"
                       % (name, len(atlas_bytes), st["size"]))
    if not sdfmod.is_sdf_atlas(atlas_bytes):
        # Atlas bitmap (bien cung, hau het pixel 0/255) — khong phai
        # SDF, khong tai tao bang duong SDF duoc (vd ContinuumBold PvZ).
        raise PB3Error("asset %s: atlas la bitmap (khong phai SDF — "
                       "ti le pixel trung gian < %.0f%%), khong tai tao "
                       "SDF duoc" % (name, sdfmod.SDF_MIN_MID_FRACTION * 100))
    # Codepoint cu (tu bang glyph) + moi.
    records = tf.read_glyph_records(raw, gt["offset"], gt["count"],
                                    gt["stride"], sf.endian)
    if layout == "new":
        font = sdfmod.parse_font(font_data)
        gid2cp = {}
        for cp, gid in font.cmap.items():
            gid2cp.setdefault(gid, cp)
        old_cps = []
        for r in records:
            cp = gid2cp.get(r["id"])
            if cp is not None:
                old_cps.append(cp)
    else:
        old_cps = [r["id"] for r in records]
    cps = sorted(set(old_cps) | set(extra_codepoints or []))
    # Tao atlas moi (cung kich thuoc, cung padding/pointSize).
    gen = sdfmod.generate_tmp_atlas(font_data, cps, float(pt), pad,
                                    aw, ah, layout=layout)
    if gen["unfitted"]:
        raise PB3Error("asset %s: %d glyph khong xep vua atlas %dx%d "
                       "(them %s)" % (name, len(gen["unfitted"]), aw, ah,
                                      gen["unfitted"][:10]))
    # Do sai so tren glyph cu (so voi atlas that).
    err = sdfmod.measure_atlas_error(font_data, records, atlas_bytes,
                                     aw, ah, float(pt), pad, layout=layout)
    # Bang glyph moi (cung layout voi ban goc).
    if layout == "new":
        new_table = sdfmod.encode_glyph_table_new(gen["glyphs"])
    else:
        new_table = sdfmod.encode_glyph_table_old(gen["glyphs"])
    # Offset CharacterCount trong FaceInfo (layout cu): sau chuoi ten
    # family (align 4) + pointSize(4) + scale(4) -> count o +8. Chi
    # dung cho layout cu (FaceInfo tim duoc la chinh thuc); layout moi
    # co FaceInfo chinh dang int pointSize (chua co neo) nen de None —
    # ghi nguoc can tim FaceInfo moi truoc (follow-up).
    count_off = None
    if layout == "old" and fi.get("offset") is not None \
            and fi.get("name") is not None:
        e = (fi["offset"] + 4 + len(fi["name"]) + 3) & ~3
        count_off = e + 8
    return {"name": name, "layout": layout, "point_size": float(pt),
            "padding": pad, "atlas": "%dx%d" % (aw, ah),
            "font_kind": font_kind, "font_source": font_source,
            "glyphs_old": len(records), "glyphs_new": len(gen["glyphs"]),
            "added": sorted(set(cps) - set(old_cps)),
            "unfitted": gen["unfitted"], "empty": gen["empty"],
            "median_mae": err["median_mae"], "mean_mae": err["mean_mae"],
            "pct_lt3": err["pct_lt3"], "pass": err["pass"],
            "worst": err["worst"],
            "new_atlas": gen["atlas"], "new_table": new_table,
            "new_count": len(gen["glyphs"]),
            "table_offset": gt["offset"], "table_stride": gt["stride"],
            "table_count": gt["count"],
            "faceinfo_count_offset": count_off,
            "note": "thu nghiem: sai so trung vi MAE %.2f (nguong < 3.0); "
                    "glyph lech cao thuong do font nhung khac version voi "
                    "font build atlas goc%s" % (
                        err["median_mae"],
                        "; padding uoc luong=5 (FaceInfo moi khong co padding)"
                        if pad_estimated else "")}
