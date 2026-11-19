# -*- coding: utf-8 -*-
from __future__ import annotations

"""Kiem tra font 3 buoc cua anh Duy (13:52 +07 19/11), ap dung cho MOI game
Unity — khong hardcode theo game.

Khung 3 buoc (khung chinh, khong cung nhac mot duong duy nhat — theo chi
dao 13:55 +07 19/11: "Co nhieu cach, moi game moi khac dung cung nhac qua,
cai gi chua co thi minh them vao la dc"):
  Buoc 1 — Tim font: quet toan bo bundle + file .assets, loc 2 loai
    object: `Font` (TTF nhung, class 128) va `MonoBehaviour` dang
    TMP_FontAsset cua TextMeshPro (class 114).
  Buoc 2 — Check atlas SDF: doc truong `m_CharacterTable` cua
    TMP_FontAsset (unicode da bake san vao atlas), doi chieu bo chuan
    134 chu cai Tieng Viet co dau (67 thuong + 67 hoa) va 8 dau ket hop
    (U+0300, 0301, 0302, 0303, 0306, 0309, 031B, 0323).
  Buoc 3 — Check file TTF nguon: trich `m_FontData` (bytes TTF goc
    trong Font object) ra .ttf, doc cmap bang parser stdlib cua repo
    (sdk/font) — cmap du 134/134 thi font goc ve duoc het Tieng Viet.
    Dong thoi xem `AtlasPopulationMode` (Dynamic = tu sinh glyph thieu
    khi chay) va `m_FallbackFontAssetTable` (co font du phong hay khong).

Duong ung dung theo game (linh hoat, khong fail-closed vo ly):
  A. TMP atlas — game co TMP_FontAsset: atlas SDF la chinh, cmap nguon
     la phu (Dynamic co the sinh them glyph).
  B. Font cmap — game chi co Font object: cmap TTF nguon la chinh.
  C. Donor — game 0 font: dung font di kem Be Vietnam Pro (du 142/142).
  D. Layout TMP moi / co che khac: doc bang cach thu (typetree truoc,
     heuristic sau); co che nao chua co thi THEM ho tro thay vi bao loi
     cung — mien la co test + fail-closed dung cho (khong doan so lieu).
  E. Khong xac dinh duoc: ghi ro vao bao cao (chu khong doan), verdict
     cua cong (gate) giu nguyen fail-closed cu.

Cach xac nhan TMP_FontAsset (theo thu tu, dung cai nao duoc thi dung):
  (a) ten script == "TMP_FontAsset" (khi script resolve duoc);
  (b) typetree co truong m_FaceInfo + m_CharacterTable (build release:
      script nam ngoai file, khong xac nhan qua ten script duoc — Kusa
      6/6 TMP xac nhan bang cach nay [DA DO THAT]);
  (c2) ten MB chua "SDF" + binary parse (khong typetree — bo cuc 16
      byte hoc tu Kusa Rosetta Stone, kiem chung Knightica 12/12
      [DA DO THAT]);
  (c) thu decode heuristic (sdk/tmp_font.decode_object) — chi giu khi
      co FaceInfo name; danh dau "unconfirmed" (so lieu van do that,
      nhung danh tinh chua chac chan) va KHONG cho vao verdict cua cong.

Doc m_FontData: quet sfnt magic (TTF/OTF/TTC) + validate table
directory (so bang 1-64, tag ASCII, offset/length trong gioi han) —
cach nay tranh false positive magic o ranh gioi field (Kusa: magic
0x00010000 o offset 67 la gia, TTF that o offset 92 [DA DO THAT]).
Khong dung read_typetree_value cho Font object: struct branch cua no
can le 4 byte cho moi field con, doc sai 2 field bool lien tiep o cuoi
object (doc vuot qua 1 byte) — xem ghi chu o ham extract_font_data.

Schema bao cao: "font_check/1". Chi doc, khong ghi file game.
Python 3.9, stdlib-only, khong import UnityPy/lz4/fontTools.
"""
import os
import statistics
import struct
import zipfile

from sdk import containers as _containers
from sdk import font as _font
from sdk import tmp_font as _tf
from sdk import unity as _uni

SCHEMA = "font_check/1"

FONT_CLASS_ID = 128
MONO_BEHAVIOUR_CLASS_ID = 114

# Gioi han RAM cho 1 entry SerializedFile khi quet font: 256 MB — can
# voi duong trich xuat cu (file .assets tho doc tron toi da 256 MB,
# caps-po; font_list doc Unity entry nguyen ven, chi font roi trong ZIP
# moi bi gioi han 32 MB). Truoc day quet font dung 32 MB nen bo sot
# sharedassets0.assets cua Knightica (84,5 MB — 6 Font + 11 TMP nam
# trong do [DA DO THAT]). Entry vuot gioi han thi bo qua kem warning;
# VFS co the tu choi truoc (E_TOO_LARGE, vd sharedassets0 cua 9K
# 154,6 MB) — cung chi ghi warning, quet tiep.
MAX_SERIALIZED_BYTES = 256 * 1024 * 1024
MAX_ZIP_FONT_BYTES = 32 * 1024 * 1024

# Bo chuan Tieng Viet: 134 chu cai (67 thuong + 67 hoa) + 8 dau ket hop.
# Nguon chan ly duy nhat la sdk/font.REQUIRED_VIETNAMESE (142 muc);
# day chi tach ra de dung cho Buoc 2/3 (xem test_font_check_3step).
VIETNAMESE_LETTERS_134 = _tf.vietnamese_letters_134()
COMBINING_MARKS_8 = _tf.combining_marks_8()

POPULATION_MODE_NAMES = {0: "Static", 1: "Dynamic"}

# sfnt magic (big-endian): TTF (0x00010000), OTF-CFF ("OTTO"),
# TTF-true ("true"), TTC ("ttcf").
_SFNT_MAGICS = {
    b"\x00\x01\x00\x00": "TTF",
    b"OTTO": "OTF",
    b"true": "TTF",
    b"ttcf": "TTC",
}

_MISSING_HEX_CAP = 16


def vietnamese_letters_134():
    """134 chu cai Tieng Viet co dau (67 thuong + 67 hoa), da sap xep."""
    return list(VIETNAMESE_LETTERS_134)


def combining_marks_8():
    """8 dau ket hop: U+0300, 0301, 0302, 0303, 0306, 0309, 031B, 0323."""
    return list(COMBINING_MARKS_8)


def _hex_list(codepoints, cap=_MISSING_HEX_CAP):
    return ["U+%04X" % cp for cp in sorted(codepoints)[:cap]]


def atlas_coverage(unicodes):
    """Do phu atlas SDF theo bo 134 chu + 8 dau (unicodes: set[int])."""
    unis = set(unicodes or ())
    letters = set(VIETNAMESE_LETTERS_134)
    marks = set(COMBINING_MARKS_8)
    miss_l = sorted(letters - unis)
    miss_m = sorted(marks - unis)
    return {
        "letters_covered": len(letters) - len(miss_l),
        "letters_missing": miss_l,
        "letters_missing_hex": _hex_list(miss_l),
        "marks_covered": len(marks) - len(miss_m),
        "marks_missing": miss_m,
        "marks_missing_hex": _hex_list(miss_m),
        "covered": len(letters) - len(miss_l) + len(marks) - len(miss_m),
        "total": 142,
    }


def _split_missing(missing_codepoints):
    """Tach danh sach codepoint thieu thanh (chu cai, dau ket hop)."""
    letters = set(VIETNAMESE_LETTERS_134)
    marks = set(COMBINING_MARKS_8)
    miss = [int(c) for c in (missing_codepoints or [])]
    miss_l = sorted(c for c in miss if c in letters)
    miss_m = sorted(c for c in miss if c in marks)
    return miss_l, miss_m


# ------------------------------------------- sfnt scan (Buoc 3)
def _valid_sfnt(buf, off):
    """(flavor, num_tables, extent) neu off la dau sfnt hop le, else None.

    Validate table directory de loai false positive magic o ranh gioi
    field: so bang 1-64, tag ASCII in duoc, offset/length nam trong
    gioi han blob. TTC: tra num_fonts o cho num_tables, extent None
    (blob TTC lay toi het object — parser doc theo offset trong header
    nen bytes thua sau cung khong anh huong).
    """
    if off + 12 > len(buf):
        return None
    magic = bytes(buf[off:off + 4])
    if magic == b"ttcf":
        nfonts = struct.unpack_from(">I", buf, off + 8)[0]
        if not 1 <= nfonts <= 64:
            return None
        return ("TTC", nfonts, None)
    if magic not in _SFNT_MAGICS:
        return None
    num_tables = struct.unpack_from(">H", buf, off + 4)[0]
    if not 1 <= num_tables <= 64:
        return None
    if off + 12 + 16 * num_tables > len(buf):
        return None
    end = off + 12 + 16 * num_tables
    for i in range(num_tables):
        tag, _chk, toff, tlen = struct.unpack_from(
            ">4sIII", buf, off + 12 + 16 * i)
        try:
            tag.decode("ascii")
        except UnicodeDecodeError:
            return None
        if not all(32 <= b < 127 for b in tag):
            return None
        if toff + tlen > len(buf) - off or tlen > 100 * 1024 * 1024:
            return None
        end = max(end, off + toff + tlen)
    return (_SFNT_MAGICS[magic], num_tables, end - off)


def find_sfnt_blobs(data):
    """Tim moi blob sfnt hop le trong bytes (da validate table dir).

    Tra [{offset, flavor, num_tables, num_fonts, extent}] — extent None
    voi TTC. Tim thay blob thi nhay qua het blob (khong tim magic long
    trong than font); magic khong hop le thi nhay 4 byte.
    """
    buf = bytes(data)
    out = []
    i = 0
    while True:
        nxt = None
        for m in _SFNT_MAGICS:
            j = buf.find(m, i)
            if j >= 0 and (nxt is None or j < nxt):
                nxt = j
        if nxt is None:
            break
        v = _valid_sfnt(buf, nxt)
        if v is not None:
            flavor, n, extent = v
            rec = {"offset": nxt, "flavor": flavor, "extent": extent}
            if flavor == "TTC":
                rec["num_fonts"] = n
                rec["num_tables"] = None
            else:
                rec["num_tables"] = n
                rec["num_fonts"] = None
            out.append(rec)
            i = nxt + max(extent or 12, 12)
        else:
            i = nxt + 4
    return out


def extract_font_data(raw):
    """Trich bytes TTF/OTF/TTC nhung trong 1 Font object (raw bytes).

    Dung sfnt scan + validate (khong dung read_typetree_value: struct
    branch cua no can le 4 byte cho moi field con nen doc sai 2 field
    bool lien tiep o cuoi Font object — Kusa ar: doc vuot qua 1 byte
    o offset 194517/194518 [DA DO THAT]). Tra {"blobs": [...], "status":
    "found"/"none"}; moi blob co them "bytes" (slice dung extent, TTC
    lay toi het object).
    """
    raw = bytes(raw)
    blobs = []
    for b in find_sfnt_blobs(raw):
        if b["flavor"] == "TTC":
            data = raw[b["offset"]:]
        else:
            data = raw[b["offset"]:b["offset"] + b["extent"]]
        rec = dict(b)
        rec["bytes"] = data
        rec["bytes_len"] = len(data)
        blobs.append(rec)
    return {"blobs": blobs,
            "status": "found" if blobs else "none"}


def check_source_font(blob_bytes, name, where="font"):
    """Check cmap TTF nguon (Buoc 3) bang parser stdlib sdk/font.

    Tra dict 1 face (TTF/OTF) hoac {"faces": [...]} (TTC, moi face 1
    dict) — moi dict: flavor/num_glyphs/family/cmap_letters (x/134)/
    cmap_marks (x/8)/missing_codepoints/covered/total/status
    (full/partial/unreadable) /error.
    """
    data = bytes(blob_bytes)
    kind = _font.detect_font_kind(data)
    if kind == "ttc":
        try:
            faces = _font.list_ttc_faces(data, where=where)
        except Exception as exc:  # noqa: BLE001 - ghi loi, khong raise
            return {"name": name, "flavor": "TTC", "status": "unreadable",
                    "error": str(exc), "faces": []}
        out = {"name": name, "flavor": "TTC", "faces": [],
               "status": "partial", "covered": 0, "total": 142}
        for i, face in enumerate(faces):
            out["faces"].append(
                _check_one_face(data, name, where, face_index=i,
                                face_name=face.get("family") or name))
        covered = [f.get("covered") for f in out["faces"]
                   if f.get("covered") is not None]
        if covered:
            out["covered"] = max(covered)
            out["status"] = ("full" if any(
                f.get("status") == "full" for f in out["faces"])
                else "partial")
        else:
            out["status"] = "unreadable"
        return out
    return _check_one_face(data, name, where)


def _check_one_face(data, name, where, face_index=None, face_name=None):
    letters = set(VIETNAMESE_LETTERS_134)
    marks = set(COMBINING_MARKS_8)
    try:
        info = _font.parse_font(data, where=where, face_index=face_index)
    except Exception as exc:  # noqa: BLE001 - ghi loi, khong raise
        return {"name": face_name or name, "flavor": "unknown",
                "status": "unreadable", "error": str(exc),
                "cmap_letters": 0, "cmap_marks": 0, "covered": None,
                "total": 142, "missing_codepoints": [],
                "num_glyphs": None, "family": ""}
    cmap = set(info.cmap or {})
    miss_l = sorted(letters - cmap)
    miss_m = sorted(marks - cmap)
    missing = sorted(miss_l + miss_m)
    return {
        "name": face_name or name, "flavor": info.kind,
        "family": info.family, "num_glyphs": info.num_glyphs,
        "cmap_letters": len(letters) - len(miss_l),
        "cmap_marks": len(marks) - len(miss_m),
        "missing_letters_hex": _hex_list(miss_l),
        "missing_marks_hex": _hex_list(miss_m),
        "missing_codepoints": missing,
        "covered": 142 - len(missing), "total": 142,
        "status": "full" if not missing else "partial",
        "error": None,
    }


# ------------------------------------------- TMP_FontAsset (Buoc 2)
def _f32_maybe(value):
    """Float typetree co the tra ve int bits (read_typetree_value) —
    doi sang float; float thi giu nguyen; khac thi None."""
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        try:
            return struct.unpack("<f", struct.pack("<i", value))[0]
        except struct.error:
            return None
    return None


def _pptr(d):
    if not isinstance(d, dict):
        return None
    return {"file_id": d.get("m_FileID"), "path_id": d.get("m_PathID")}


def read_tmp_asset_typetree(sf, path_id):
    """Doc TMP_FontAsset bang typetree (Buoc 2 + phu: population mode,
    fallback, source font).

    Tra dict status ok/no_typetree/read_error + face_family/face_style/
    point_size/atlas_population_mode (+label)/char_table_count/
    glyph_table_count/unicode_count/atlas_letters/atlas_marks (+missing)/
    fallback_count/fallback_entries/atlas_textures_count/source_font_file/
    clear_dynamic_on_build/multi_atlas_enabled/atlas_width/atlas_height.
    """
    try:
        obj = sf.get_object(path_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "read_error", "error": "no object %s: %s"
                % (path_id, exc)}
    typ = sf.types[obj.type_id] if obj.type_id < len(sf.types) else None
    node = getattr(typ, "node", None) if typ is not None else None
    if node is None:
        return {"status": "no_typetree",
                "error": "type %s khong co typetree (stripped)" % obj.type_id}
    try:
        raw = sf.object_bytes(path_id)
        val, _end = _uni.read_typetree_value(raw, node, 0, sf.endian)
    except Exception as exc:  # noqa: BLE001
        return {"status": "read_error", "error": str(exc)}
    fi = val.get("m_FaceInfo") or {}
    ct = val.get("m_CharacterTable") or []
    gt = val.get("m_GlyphTable") or []
    fb = val.get("m_FallbackFontAssetTable")
    at = val.get("m_AtlasTextures")
    apm = val.get("m_AtlasPopulationMode")
    unis = set()
    for e in ct:
        if isinstance(e, dict):
            u = e.get("m_Unicode")
            if isinstance(u, int) and u > 0:
                unis.add(u)
    cov = atlas_coverage(unis)
    fb_list = fb if isinstance(fb, list) else []
    aw = val.get("m_AtlasWidth")
    if not isinstance(aw, int):
        aw = fi.get("m_AtlasWidth")
    ah = val.get("m_AtlasHeight")
    if not isinstance(ah, int):
        ah = fi.get("m_AtlasHeight")
    stats = _atlas_rect_stats(_rects_from_typetree(val, "m_UsedGlyphRects"),
                              _rects_from_typetree(val, "m_FreeGlyphRects"),
                              aw if isinstance(aw, int) else None,
                              ah if isinstance(ah, int) else None)
    return {
        "status": "ok",
        "face_family": fi.get("m_FamilyName"),
        "face_style": fi.get("m_StyleName"),
        "point_size": _f32_maybe(fi.get("m_PointSize")),
        "atlas_width": aw,
        "atlas_height": ah,
        "atlas_padding": val.get("m_AtlasPadding"),
        "atlas_render_mode": val.get("m_AtlasRenderMode"),
        "atlas_population_mode": apm,
        "population_label": (POPULATION_MODE_NAMES.get(apm)
                             if isinstance(apm, int) else None),
        "char_table_count": len(ct),
        "glyph_table_count": len(gt),
        "unicode_count": len(unis),
        "atlas_letters": cov["letters_covered"],
        "atlas_marks": cov["marks_covered"],
        "missing_letters_hex": cov["letters_missing_hex"],
        "missing_marks_hex": cov["marks_missing_hex"],
        "missing_codepoints": sorted(cov["letters_missing"]
                                     + cov["marks_missing"]),
        "covered": cov["covered"],
        "total": 142,
        "fallback_count": len(fb_list),
        "fallback_entries": [_pptr(x) for x in fb_list],
        "fallback_structural": True,
        "atlas_textures_count": len(at) if isinstance(at, list) else None,
        "source_font_file": _pptr(val.get("m_SourceFontFile")),
        "clear_dynamic_on_build": val.get("m_ClearDynamicDataOnBuild"),
        "multi_atlas_enabled": val.get("m_IsMultiAtlasTexturesEnabled"),
        **stats,
    }


def _rects_from_typetree(val, key):
    """GlyphRect typetree (m_X/m_Y/m_Width/m_Height) -> list (x, y, w, h)."""
    out = []
    for r in (val.get(key) or []):
        if not isinstance(r, dict):
            continue
        try:
            out.append((int(r.get("m_X") or 0), int(r.get("m_Y") or 0),
                        int(r.get("m_Width") or 0),
                        int(r.get("m_Height") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _atlas_rect_stats(used, free, aw, ah):
    """Do day atlas + suc chua con lai cho glyph moi (Dynamic).

    used/free: list (x, y, w, h). "Glyph co trung binh" = median cua
    used rects (w>0/h>0) — chuan cua agent check Knightica [DA DO THAT].
    Tra used/free counts, used_area_px/pct, median_glyph_rect,
    free_rects_fit_median_glyph (+ ban 40x70), largest_free_rect.
    """
    uw = [w for _, _, w, h in used if w > 0]
    uh = [h for _, _, w, h in used if h > 0]
    med_w = statistics.median(uw) if uw else None
    med_h = statistics.median(uh) if uh else None
    used_area = sum(w * h for _, _, w, h in used)
    fits = None
    if med_w is not None and med_h is not None:
        fits = sum(1 for r in free if r[2] >= med_w and r[3] >= med_h)
    largest = max(((w * h, w, h) for _, _, w, h in free),
                  default=(0, 0, 0))
    return {
        "used_glyph_rects": len(used),
        "free_glyph_rects": len(free),
        "used_area_px": used_area,
        "used_area_pct": (round(100.0 * used_area / (aw * ah), 1)
                          if aw and ah else None),
        "median_glyph_rect": ([med_w, med_h]
                              if med_w is not None else None),
        "free_rects_fit_median_glyph": fits,
        "free_rects_fit_40x70": sum(
            1 for r in free if r[2] >= 40 and r[3] >= 70),
        "largest_free_rect": [largest[1], largest[2]],
    }


def _i32(data, off, endian="<"):
    return struct.unpack_from(endian + "i", data, off)[0]


def _i64(data, off, endian="<"):
    return struct.unpack_from(endian + "q", data, off)[0]


def _binary_fallback(raw, endian, tmp_path_ids):
    """Tim m_FallbackFontAssetTable trong object TMP (khong typetree).

    Quet toan object tim PPtr toi TMP khac (cung SF), gom run lien tiep
    (buoc 12B) va xac nhan cau truc: int32 count == len(run) ngay truoc
    run + int32 weight count (0..64) ngay sau run. Kiem chung: Knightica
    36713: count=5 @24468, 5 PPtr (0,36708..36712), weight=10 @24532;
    Kusa ja: khong co PPtr toi TMP khac -> [] [DA DO THAT].
    Tra {"entries": [{"file_id","path_id"}...], "structural": bool}.
    Khong tim thay cau truc thi van tra cac PPtr tim duoc (scan-only,
    structural=False) — bao cao ghi ro, khong doan.
    """
    end = endian
    hits = []
    for off in range(0, len(raw) - 12, 4):
        fid = _i32(raw, off, end)
        ppid = _i64(raw, off + 4, end)
        if 0 <= fid <= 100 and ppid in tmp_path_ids:
            hits.append((off, fid, ppid))
    if not hits:
        return {"entries": [], "structural": False}
    runs = [[hits[0]]]
    for h in hits[1:]:
        if h[0] == runs[-1][-1][0] + 12:
            runs[-1].append(h)
        else:
            runs.append([h])
    for run in runs:
        start = run[0][0]
        end_off = run[-1][0] + 12
        if (start >= 4 and end_off + 4 <= len(raw)
                and _i32(raw, start - 4, end) == len(run)
                and 0 <= _i32(raw, end_off, end) <= 64):
            return {"entries": [{"file_id": f, "path_id": p}
                                for _, f, p in run],
                    "structural": True}
    return {"entries": [{"file_id": f, "path_id": p} for _, f, p in hits],
            "structural": False}


def read_tmp_asset_binary(raw, endian, tmp_path_ids=None):
    """Doc TMP_FontAsset KHONG typetree bang bo cuc nhi phan (Buoc 2).

    Layout hoc tu TMP Kusa (co typetree) lam Rosetta Stone, kiem chung
    tren Knightica [DA DO THAT]:
    - Glyph table: lay offset/stride/count tu sdk/tmp_font.decode_object
      (heuristic, da chay duoc tren MB khong typetree).
    - APM + SourceFontFile: 24 byte truoc glyph count:
      [PPtr 12B][SourceFontFilePath len 4B (=0)][APM 4B][internal 4B]
      [glyph count 4B]. Path khac rong thi tu choi (chua ho tro).
    - Char table ngay sau glyph table: count int32 + record 16 byte
      (ElementType u32, Unicode u32, GlyphIndex u32, Scale f32) —
      kiem chung 1306/1306 record hop le tren Knightica SC; tat ca
      record phai hop le (et==1, 0<u<=0x10FFFF, g<100000, 0<s<=1000)
      thi moi nhan, khong thi bao loi (fail-closed).
    - Atlas xuoi tu char_end: [AtlasTextures count + PPtr 12B/muc]
      [AtlasTextureIndex, multiAtlas, getFeatures, clearDyn — 4x int32]
      [W, H, padding, renderMode — 4x int32; renderMode la bitfield
      (Kusa + Knightica deu 4165 = 0x1045), chi doc khong validate]
      [used count + rect 16B][free count + rect 16B].
    - Fallback: chi giai quyet khi truyen tmp_path_ids (tap path_id
      TMP cung SF); None = bo qua (fallback_count=None).
    Tra dict cung dang read_tmp_asset_typetree (status ok/error + cac
    truong atlas/population/fallback/source/rect stats).
    """
    end = endian
    try:
        dec = _tf.decode_object(raw, end)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": "decode_object loi: %s" % exc}
    gt = dec.get("glyph_table") or {}
    off, cnt, st = gt.get("offset"), gt.get("count"), gt.get("stride")
    if off is None or not cnt or not st:
        return {"status": "error", "error": "khong tim duoc glyph table"}
    if off < 24 or off + 4 + st * cnt > len(raw):
        return {"status": "error", "error": "glyph table vuot file"}
    c = off
    src_fid = _i32(raw, c - 24, end)
    src_pid = _i64(raw, c - 20, end)
    path_len = _i32(raw, c - 12, end)
    apm = _i32(raw, c - 8, end)
    internal = _i32(raw, c - 4, end)
    if not (0 <= src_fid <= 100):
        return {"status": "error",
                "error": "source file_id la: %d" % src_fid}
    if src_pid < 0:
        return {"status": "error", "error": "source path_id am"}
    if path_len != 0:
        return {"status": "error",
                "error": "SourceFontFilePath khong rong (len=%d) — "
                         "chua ho tro" % path_len}
    if apm not in (0, 1) or internal not in (0, 1):
        return {"status": "error",
                "error": "APM/internal la: %d/%d" % (apm, internal)}
    skip = off + 4 + st * cnt
    if skip + 4 > len(raw):
        return {"status": "error",
                "error": "het du lieu truoc char table"}
    char_cnt = _i32(raw, skip, end)
    if not (0 < char_cnt <= 100000):
        return {"status": "error", "error": "char count la: %d" % char_cnt}
    if skip + 4 + 16 * char_cnt > len(raw):
        return {"status": "error", "error": "char table vuot file"}
    unis = set()
    ok = 0
    for i in range(char_cnt):
        o = skip + 4 + 16 * i
        et, u, g = struct.unpack_from(end + "III", raw, o)
        s = struct.unpack_from(end + "f", raw, o + 12)[0]
        if et == 1 and 0 < u <= 0x10FFFF and g < 100000 and 0 < s <= 1000:
            ok += 1
            unis.add(u)
    if ok != char_cnt:
        return {"status": "error",
                "error": "char table chi %d/%d record hop le"
                         % (ok, char_cnt)}
    cov = atlas_coverage(unis)
    p = skip + 4 + 16 * char_cnt
    if p + 4 > len(raw):
        return {"status": "error", "error": "het du lieu truoc atlas"}
    ntex = _i32(raw, p, end)
    if not (0 <= ntex <= 8):
        return {"status": "error",
                "error": "atlas textures count la: %d" % ntex}
    p += 4 + 12 * ntex
    if p + 32 > len(raw):
        return {"status": "error",
                "error": "het du lieu o atlas header"}
    idx, multi, feat, clear = struct.unpack_from(end + "iiii", raw, p)
    p += 16
    if (multi not in (0, 1) or feat not in (0, 1)
            or clear not in (0, 1) or idx < 0):
        return {"status": "error",
                "error": "atlas flags la: %d/%d/%d/%d"
                         % (idx, multi, feat, clear)}
    aw, ah, apad, arender = struct.unpack_from(end + "iiii", raw, p)
    p += 16
    if not (64 <= aw <= 8192 and 64 <= ah <= 8192 and 0 <= apad <= 128):
        return {"status": "error",
                "error": "atlas W/H/pad la: %d/%d/%d" % (aw, ah, apad)}
    if p + 4 > len(raw):
        return {"status": "error",
                "error": "het du lieu truoc used rects"}
    ucnt = _i32(raw, p, end)
    p += 4
    if not (0 <= ucnt <= 100000) or p + 16 * ucnt > len(raw):
        return {"status": "error", "error": "used rects la: %d" % ucnt}
    used = [struct.unpack_from(end + "iiii", raw, p + 16 * i)
            for i in range(ucnt)]
    p += 16 * ucnt
    if p + 4 > len(raw):
        return {"status": "error",
                "error": "het du lieu truoc free rects"}
    fcnt = _i32(raw, p, end)
    p += 4
    if not (0 <= fcnt <= 100000) or p + 16 * fcnt > len(raw):
        return {"status": "error", "error": "free rects la: %d" % fcnt}
    free = [struct.unpack_from(end + "iiii", raw, p + 16 * i)
            for i in range(fcnt)]
    stats = _atlas_rect_stats(used, free, aw, ah)
    fb_entries = None
    fb_count = None
    fb_structural = None
    if tmp_path_ids is not None:
        fb = _binary_fallback(raw, end, set(tmp_path_ids))
        fb_entries = fb["entries"]
        fb_count = len(fb["entries"])
        fb_structural = fb["structural"]
    return {
        "status": "ok",
        "face_family": None,
        "face_style": None,
        "point_size": None,
        "atlas_width": aw,
        "atlas_height": ah,
        "atlas_padding": apad,
        "atlas_render_mode": arender,
        "atlas_population_mode": apm,
        "population_label": POPULATION_MODE_NAMES.get(apm),
        "char_table_count": char_cnt,
        "glyph_table_count": cnt,
        "unicode_count": len(unis),
        "atlas_letters": cov["letters_covered"],
        "atlas_marks": cov["marks_covered"],
        "missing_letters_hex": cov["letters_missing_hex"],
        "missing_marks_hex": cov["marks_missing_hex"],
        "missing_codepoints": sorted(cov["letters_missing"]
                                     + cov["marks_missing"]),
        "covered": cov["covered"],
        "total": 142,
        "fallback_count": fb_count,
        "fallback_entries": fb_entries,
        "fallback_structural": fb_structural,
        "atlas_textures_count": ntex,
        "source_font_file": {"file_id": src_fid, "path_id": src_pid}
        if src_pid else None,
        "clear_dynamic_on_build": clear,
        "multi_atlas_enabled": multi,
        "internal_dynamic_os": internal,
        **stats,
    }


def identify_tmp_assets(sf, heuristic=True, binary=True):
    """Xac nhan TMP_FontAsset trong 1 SerializedFile, theo thu tu:
    (a) ten script, (b) truong typetree, (c2) ten MB chua "SDF" +
    binary parse (khong typetree), (c) thu decode heuristic.

    Tra {"confirmed": [{path_id, method, parsed?, name?}],
    "unconfirmed": [{path_id, method: "heuristic", decoded}],
    "warnings": [...]}. (c2) chi giu khi binary parse OK het cac
    kiem chung (char record, APM, atlas) — muc binary mang theo
    "parsed" (dict cua read_tmp_asset_binary, fallback chua giai
    quyet: scan_serialized_file giai quyet sau khi biet het TMP
    trong SF) + "name" (ten MB). (c) chi giu khi decode co FaceInfo
    name (loc false positive nhu DOTweenSettings / bang ngon ngu /
    TMP Settings / EmojiOne tren PB3 [DA DO THAT]).
    heuristic=False bo qua buoc (c); binary=False bo qua buoc (c2)
    — ca hai dung cho enrich trong cong (tranh thu decode 200k+ MB).
    """
    confirmed = []
    unconfirmed = []
    warnings = []
    seen = set()

    def _add(pid, method, bucket, **extra):
        if pid in seen:
            return
        seen.add(pid)
        item = {"path_id": pid, "method": method}
        item.update(extra)
        bucket.append(item)

    # (a) ten script resolve duoc.
    try:
        for item in _tf.find_tmp_font_assets(sf):
            if item.get("is_tmp"):
                _add(item["path_id"], "script", confirmed)
    except Exception as exc:  # noqa: BLE001 - (b)/(c) van chay
        warnings.append("script scan loi: %s" % exc)
    # (b) typetree co m_FaceInfo + m_CharacterTable.
    for obj in getattr(sf, "objects", []):
        if getattr(obj, "class_id", None) != MONO_BEHAVIOUR_CLASS_ID:
            continue
        if obj.path_id in seen:
            continue
        typ = (sf.types[obj.type_id]
               if 0 <= obj.type_id < len(sf.types) else None)
        node = getattr(typ, "node", None) if typ is not None else None
        if node is None:
            continue
        names = {c.name for c in (node.children or [])}
        if "m_FaceInfo" in names and "m_CharacterTable" in names:
            _add(obj.path_id, "typetree", confirmed)
    # (c2)+(c) thu doc MB con lai (1 lan doc raw cho moi MB).
    if not heuristic and not binary:
        return {"confirmed": confirmed, "unconfirmed": unconfirmed,
                "warnings": warnings}
    for obj in getattr(sf, "objects", []):
        if getattr(obj, "class_id", None) != MONO_BEHAVIOUR_CLASS_ID:
            continue
        if obj.path_id in seen:
            continue
        try:
            raw = sf.object_bytes(obj.path_id)
        except Exception:  # noqa: BLE001 - MB nay khong doc duoc
            continue
        if binary:
            name = None
            try:
                hdr, _ = _tf.read_monobehaviour_header(raw, sf.endian)
                if hdr:
                    name = hdr.get("name")
            except Exception:  # noqa: BLE001 - ten khong doc duoc
                name = None
            if name and "SDF" in name:
                parsed = read_tmp_asset_binary(raw, sf.endian)
                if parsed["status"] == "ok":
                    _add(obj.path_id, "binary", confirmed,
                         parsed=parsed, name=name)
                    continue
                warnings.append("MB '%s' (#%s) co ten SDF nhung binary "
                                "parse loi (%s) — thu heuristic."
                                % (name, obj.path_id,
                                   parsed.get("error") or "?"))
        if not heuristic:
            continue
        try:
            dec = _tf.decode_object(raw, sf.endian)
        except Exception:  # noqa: BLE001 - MB nay khong phai TMP
            continue
        if dec.get("status") not in ("parsed", "partial"):
            continue
        fi = dec.get("font_info") or {}
        if not fi.get("name"):
            continue
        seen.add(obj.path_id)
        unconfirmed.append({"path_id": obj.path_id,
                            "method": "heuristic", "decoded": dec})
    return {"confirmed": confirmed, "unconfirmed": unconfirmed,
            "warnings": warnings}


def _tmp_entry_from_parsed(item, tt, where, name_hint=None):
    """1 muc tmp_font_asset (dang font_list) tu du lieu da parse
    (typetree hoac binary — cung dang dict).

    name_hint: ten MB (duong binary) — uu tien hon face_family.
    Cac truong atlas day + suc chua Dynamic (atlas_used_pct,
    free_rects_fit_median_glyph...) co o ca 2 duong.
    """
    name = (name_hint or tt.get("face_family")
            or ("TMP #%s" % item["path_id"]))
    missing = tt["missing_codepoints"]
    return {
        "kind": "tmp_font_asset",
        "name": name,
        "family": tt.get("face_family") or "",
        "source": where,
        "size": None,
        "path_id": item["path_id"],
        "num_glyphs": tt.get("glyph_table_count"),
        "covered": tt["covered"],
        "total": 142,
        "missing_codepoints": missing,
        "missing": _hex_list(missing),
        "missing_total": len(missing),
        "status": "full" if not missing else "partial",
        "reason": "",
        "face_name": tt.get("face_family"),
        "point_size": tt.get("point_size"),
        "atlas_width": tt.get("atlas_width"),
        "atlas_height": tt.get("atlas_height"),
        "atlas_padding": tt.get("atlas_padding"),
        "atlas_render_mode": tt.get("atlas_render_mode"),
        "tmp_status": "parsed",
        "confirmed": True,
        "method": item["method"],
        "atlas_letters": tt["atlas_letters"],
        "atlas_marks": tt["atlas_marks"],
        "missing_letters_hex": tt["missing_letters_hex"],
        "missing_marks_hex": tt["missing_marks_hex"],
        "population_mode": tt.get("atlas_population_mode"),
        "population_label": tt.get("population_label"),
        "fallback_count": tt.get("fallback_count"),
        "fallback_entries": tt.get("fallback_entries"),
        "fallback_structural": tt.get("fallback_structural"),
        "has_fallback": bool(tt.get("fallback_count")),
        "source_font_file": tt.get("source_font_file"),
        "clear_dynamic_on_build": tt.get("clear_dynamic_on_build"),
        "multi_atlas_enabled": tt.get("multi_atlas_enabled"),
        "atlas_textures_count": tt.get("atlas_textures_count"),
        "char_table_count": tt.get("char_table_count"),
        "used_glyph_rects": tt.get("used_glyph_rects"),
        "free_glyph_rects": tt.get("free_glyph_rects"),
        "used_area_px": tt.get("used_area_px"),
        "atlas_used_pct": tt.get("used_area_pct"),
        "median_glyph_rect": tt.get("median_glyph_rect"),
        "free_rects_fit_median_glyph":
            tt.get("free_rects_fit_median_glyph"),
        "free_rects_fit_40x70": tt.get("free_rects_fit_40x70"),
        "largest_free_rect": tt.get("largest_free_rect"),
    }


def _tmp_entry_from_decoded(item, where):
    """1 muc tmp_font_asset tu heuristic decode — status unconfirmed
    (khong cho vao verdict cua cong; so lieu van do that).

    Layout TMP moi (52B): glyph id la index, khong phai codepoint —
    vietnamese_coverage tra level "unknown" voi missing rong (khong
    phai du 134!). Truong hop do dat covered/atlas_* = None + co
    atlas_unknown de bao cao khong doan 134/134.
    """
    dec = item["decoded"]
    vi = dec.get("vietnamese") or {}
    fi = dec.get("font_info") or {}
    unknown = vi.get("level") == "unknown"
    miss_l = list(vi.get("missing_codepoints", []))
    marks = vi.get("marks") or {}
    miss_m = list(marks.get("missing_codepoints", []))
    missing = sorted(set(miss_l) | set(miss_m))
    covered = None
    atlas_letters = None
    atlas_marks = None
    if not unknown and vi.get("required") is not None:
        covered = (int(vi.get("covered", 0)) + int(marks.get("required", 0))
                   - int(marks.get("missing_count", 0)))
        atlas_letters = int(vi.get("covered", 0))
        atlas_marks = (int(marks.get("required", 0))
                       - int(marks.get("missing_count", 0)))
    return {
        "kind": "tmp_font_asset",
        "name": "TMP #%s" % item["path_id"],
        "family": fi.get("name") or "",
        "source": where,
        "size": None,
        "path_id": item["path_id"],
        "num_glyphs": (dec.get("glyph_table") or {}).get("count"),
        "covered": covered,
        "total": 142,
        "missing_codepoints": missing,
        "missing": _hex_list(missing),
        "missing_total": len(missing),
        "status": "unconfirmed",
        "reason": "MonoBehaviour chua xac nhan duoc class script "
                  "(script nam ngoai file) — doc bang heuristic "
                  "FaceInfo + glyph table, can doi chieu trong Editor."
                  + (" Layout TMP moi: atlas chua doi duoc sang "
                     "codepoint (can cmap font nguon)." if unknown else ""),
        "face_name": fi.get("name"),
        "point_size": fi.get("point_size"),
        "atlas_width": fi.get("atlas_width"),
        "atlas_height": fi.get("atlas_height"),
        "tmp_status": dec.get("status"),
        "confirmed": False,
        "method": "heuristic",
        "atlas_unknown": unknown,
        "atlas_letters": atlas_letters,
        "atlas_marks": atlas_marks,
        "population_mode": None,
        "population_label": None,
        "fallback_count": None,
        "has_fallback": None,
        "source_font_file": None,
    }


def scan_serialized_file(sf, where, heuristic=True, binary=True):
    """Quet 1 SerializedFile: Font object (Buoc 3) + TMP_FontAsset
    (Buoc 2). Tra {"fonts": [...], "tmp_assets": [...], "warnings": [...]}.
    heuristic=False bo qua buoc thu decode; binary=False bo qua buoc
    binary parse — ca hai dung cho enrich trong cong.
    """
    fonts = []
    tmp_assets = []
    warnings = []
    # Font object (class 128): trich m_FontData + check cmap.
    try:
        font_objs = _font.list_unity_font_objects(sf)
    except Exception as exc:  # noqa: BLE001
        font_objs = []
        warnings.append("liet ke Font object loi: %s" % exc)
    for path_id, name in font_objs:
        try:
            raw = sf.object_bytes(path_id)
        except Exception as exc:  # noqa: BLE001
            fonts.append(_unreadable_font_entry(
                name, path_id, where, "khong doc duoc bytes object: %s" % exc))
            continue
        found = extract_font_data(raw)
        if found["status"] != "found":
            fonts.append(_unreadable_font_entry(
                name, path_id, where,
                "m_FontData khong chua TTF/OTF hop le (da quet sfnt magic "
                "+ validate table directory)."))
            continue
        for blob in found["blobs"]:
            chk = check_source_font(blob["bytes"], name, where=where)
            faces = chk.get("faces") or [chk]
            for face in faces:
                if face.get("status") == "unreadable":
                    fonts.append(_unreadable_font_entry(
                        name, path_id, where,
                        "parse font loi: %s" % (face.get("error") or "?")))
                    continue
                missing = face["missing_codepoints"]
                fonts.append({
                    "kind": "unity_font",
                    "name": name or ("Font #%s" % path_id),
                    "family": face.get("family") or name or "",
                    "source": where,
                    "size": len(raw),
                    "path_id": path_id,
                    "num_glyphs": face.get("num_glyphs"),
                    "covered": face["covered"],
                    "total": 142,
                    "missing_codepoints": missing,
                    "missing": _hex_list(missing),
                    "missing_total": len(missing),
                    "status": face["status"],
                    "reason": "",
                    "flavor": face.get("flavor"),
                    "sfnt_offset": blob["offset"],
                    "cmap_letters": face.get("cmap_letters"),
                    "cmap_marks": face.get("cmap_marks"),
                    "missing_letters_hex": face.get("missing_letters_hex", []),
                    "missing_marks_hex": face.get("missing_marks_hex", []),
                })
    # TMP_FontAsset (class 114).
    ident = identify_tmp_assets(sf, heuristic=heuristic, binary=binary)
    warnings.extend(ident["warnings"])
    all_tmp_pids = ({i["path_id"] for i in ident["confirmed"]}.union(
        {i["path_id"] for i in ident["unconfirmed"]}))
    for item in ident["confirmed"]:
        if item["method"] == "binary" and item.get("parsed"):
            # Da parse bang bo cuc nhi phan o identify — chi con giai
            # fallback (can biet het TMP trong SF).
            parsed = dict(item["parsed"])
            try:
                raw = sf.object_bytes(item["path_id"])
                fb = _binary_fallback(raw, sf.endian,
                                      all_tmp_pids - {item["path_id"]})
                parsed["fallback_entries"] = fb["entries"]
                parsed["fallback_count"] = len(fb["entries"])
                parsed["fallback_structural"] = fb["structural"]
            except Exception as exc:  # noqa: BLE001
                warnings.append("TMP #%s: giai fallback loi: %s"
                                % (item["path_id"], exc))
            tmp_assets.append(_tmp_entry_from_parsed(
                item, parsed, where, name_hint=item.get("name")))
            continue
        tt = read_tmp_asset_typetree(sf, item["path_id"])
        if tt["status"] != "ok":
            # Typetree doc loi du da xac nhan qua script/field — thu
            # binary parse truoc, roi moi heuristic decode de van co
            # so atlas, ghi warning.
            done = False
            if binary:
                try:
                    raw = sf.object_bytes(item["path_id"])
                    parsed = read_tmp_asset_binary(
                        raw, sf.endian,
                        all_tmp_pids - {item["path_id"]})
                    if parsed["status"] == "ok":
                        tmp_assets.append(_tmp_entry_from_parsed(
                            item, parsed, where))
                        tmp_assets[-1]["method"] = \
                            item["method"] + "+binary"
                        done = True
                except Exception as exc:  # noqa: BLE001
                    warnings.append("TMP #%s: binary cung loi: %s"
                                    % (item["path_id"], exc))
            if done:
                continue
            warnings.append("TMP #%s: typetree doc loi (%s) — dung "
                            "heuristic decode." % (item["path_id"],
                                                   tt.get("error") or "?"))
            try:
                raw = sf.object_bytes(item["path_id"])
                dec = _tf.decode_object(raw, sf.endian)
                tmp_assets.append(_tmp_entry_from_decoded(
                    {"path_id": item["path_id"], "decoded": dec}, where))
                tmp_assets[-1]["confirmed"] = True
                tmp_assets[-1]["method"] = item["method"] + "+heuristic"
                tmp_assets[-1]["status"] = "partial"
                tmp_assets[-1]["reason"] = ""
            except Exception as exc:  # noqa: BLE001
                warnings.append("TMP #%s: heuristic cung loi: %s"
                                % (item["path_id"], exc))
            continue
        tmp_assets.append(_tmp_entry_from_parsed(item, tt, where))
    for item in ident["unconfirmed"]:
        tmp_assets.append(_tmp_entry_from_decoded(item, where))
        warnings.append("TMP chua xac nhan #%s (%s) — so lieu atlas do "
                        "that nhung danh tinh chua chac chan."
                        % (item["path_id"], item["method"]))
    return {"fonts": fonts, "tmp_assets": tmp_assets, "warnings": warnings}


def _unreadable_font_entry(name, path_id, where, reason):
    return {
        "kind": "unity_font",
        "name": name or ("Font #%s" % path_id),
        "family": name or "",
        "source": where,
        "size": None,
        "path_id": path_id,
        "num_glyphs": None,
        "covered": None,
        "total": 142,
        "missing_codepoints": [],
        "missing": [],
        "missing_total": 0,
        "status": "unreadable",
        "reason": reason,
    }


# ------------------------------------------- quet game (Buoc 1)
def _is_unity_entry(name):
    """Loc entry Unity — GIONG sdk/font_list._is_unity_entry (khong dung
    sdk/tmp_font.is_unity_entry: ban do nhan ca sharedassets*.resS —
    du lieu tho kem theo, khong phai SerializedFile — chi them warning
    nhieu)."""
    low = name.lower()
    if low.endswith((".assets", ".unity3d", ".bundle")):
        return True
    # level/sharedassets KHONG duoi: chi nhan dung ten tran (level0,
    # sharedassets0), KHONG nhan sharedassets0.assets.resS.
    base = os.path.basename(low)
    for prefix in ("level", "sharedassets"):
        if base.startswith(prefix):
            rest = base[len(prefix):]
            if rest.isdigit():
                return True
    return False


def _iter_unity_sources(path):
    """Yield (kind, name, data) cho moi entry Unity trong file/IPA/
    thu muc — giong sdk/font_list.scan_source. kind: "serialized" |
    "font". Gioi han RAM: entry SerializedFile > 256 MB bo qua kem
    warning (can voi duong trich xuat cu: .assets tho doc tron toi da
    256 MB); font roi trong ZIP > 32 MB bo qua (giong font_list)."""
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, path)
                if _is_unity_entry(rel):
                    if os.path.getsize(full) > MAX_SERIALIZED_BYTES:
                        yield ("skipped", rel,
                               "> %d MB — gioi han RAM."
                               % (MAX_SERIALIZED_BYTES // (1024 * 1024)))
                        continue
                    yield ("serialized", rel, open(full, "rb").read())
                elif rel.lower().endswith((".ttf", ".otf", ".ttc")):
                    yield ("font", rel, open(full, "rb").read())
        return
    data = open(path, "rb").read()
    if _is_unity_entry(os.path.basename(path)):
        if len(data) > MAX_SERIALIZED_BYTES:
            yield ("skipped", os.path.basename(path),
                   "> %d MB — gioi han RAM."
                   % (MAX_SERIALIZED_BYTES // (1024 * 1024)))
            return
        yield ("serialized", os.path.basename(path), data)
        return
    if path.lower().endswith((".ttf", ".otf", ".ttc")):
        yield ("font", os.path.basename(path), data)
        return
    if not zipfile.is_zipfile(path):
        raise ValueError("khong phai ZIP/IPA, .assets/.bundle hay thu muc: %s"
                         % path)
    vfs = _containers.open_vfs(path)
    for name in vfs.iter_entries():
        if _is_unity_entry(name):
            try:
                raw = vfs.read(name)
            except Exception as exc:  # noqa: BLE001 - nhu font_list: bo qua entry
                yield ("skipped", name, "khong doc duoc (%s)" % exc)
                continue
            if len(raw) > MAX_SERIALIZED_BYTES:
                yield ("skipped", name,
                       "> %d MB — gioi han RAM."
                       % (MAX_SERIALIZED_BYTES // (1024 * 1024)))
                continue
            yield ("serialized", name, raw)
        elif name.lower().endswith((".ttf", ".otf", ".ttc")):
            try:
                raw = vfs.read(name)
            except Exception as exc:  # noqa: BLE001
                yield ("skipped", name, "khong doc duoc (%s)" % exc)
                continue
            if len(raw) > MAX_ZIP_FONT_BYTES:
                yield ("skipped", name,
                       "> %d MB — gioi han RAM."
                       % (MAX_ZIP_FONT_BYTES // (1024 * 1024)))
                continue
            yield ("font", name, raw)


def _parse_sf_bytes(data, where):
    """Parse bytes thanh SerializedFile (bundle thi mo entry dau tien)."""
    if _uni.detect_bundle(data) is not None:
        bundle = _uni.parse_bundle(data, where=where)
        if not bundle.entries:
            raise ValueError("bundle rong")
        entry = bundle.get_entry(bundle.entries[0].name)
        return _uni.parse_serialized(entry, where=where + "::"
                                     + bundle.entries[0].name), \
            where + "::" + bundle.entries[0].name
    return _uni.parse_serialized(data, where=where), where


def scan_source(path):
    """Quet font 3 buoc cho ca game (file/IPA/.assets/.unity3d/thu muc).

    Tra {"schema": "font_check/1", "source", "fonts", "tmp_assets",
    "summary", "warnings"}. "fonts" gom ca Font object (co cmap) +
    font roi; "tmp_assets" la TMP_FontAsset (confirmed/unconfirmed).
    """
    fonts = []
    tmp_assets = []
    warnings = []
    files_scanned = 0
    for kind, name, data in _iter_unity_sources(path):
        if kind == "skipped":
            warnings.append("bo qua %s (%s)" % (name, data))
            continue
        if kind == "font":
            chk = check_source_font(data, os.path.basename(name), where=name)
            faces = chk.get("faces") or [chk]
            for face in faces:
                if face.get("status") == "unreadable":
                    warnings.append("%s: khong doc duoc font (%s)"
                                    % (name, face.get("error") or "?"))
                    continue
                missing = face["missing_codepoints"]
                fonts.append({
                    "kind": face.get("flavor", "ttf").lower(),
                    "name": face.get("family") or os.path.basename(name),
                    "family": face.get("family") or "",
                    "source": name,
                    "size": len(data),
                    "path_id": None,
                    "num_glyphs": face.get("num_glyphs"),
                    "covered": face["covered"],
                    "total": 142,
                    "missing_codepoints": missing,
                    "missing": _hex_list(missing),
                    "missing_total": len(missing),
                    "status": face["status"],
                    "reason": "",
                    "flavor": face.get("flavor"),
                    "cmap_letters": face.get("cmap_letters"),
                    "cmap_marks": face.get("cmap_marks"),
                })
            files_scanned += 1
            continue
        # serialized: bundle hoac .assets/.unity3d.
        try:
            sf, src = _parse_sf_bytes(data, name)
        except Exception as exc:  # noqa: BLE001 - ghi warning, quet tiep
            warnings.append("%s: khong parse duoc SerializedFile (%s)"
                            % (name, exc))
            continue
        files_scanned += 1
        res = scan_serialized_file(sf, src)
        fonts.extend(res["fonts"])
        tmp_assets.extend(res["tmp_assets"])
        warnings.extend(res["warnings"])
    summary = _summarize(fonts, tmp_assets, files_scanned)
    return {"schema": SCHEMA, "source": str(path), "fonts": fonts,
            "tmp_assets": tmp_assets, "summary": summary,
            "warnings": warnings}


def _summarize(fonts, tmp_assets, files_scanned):
    tmp_conf = [t for t in tmp_assets if t.get("confirmed")]
    tmp_unconf = [t for t in tmp_assets if not t.get("confirmed")]
    font_objs = [f for f in fonts if f.get("kind") == "unity_font"]
    font_objs_read = [f for f in font_objs
                      if f.get("status") in ("full", "partial")]
    standalone = [f for f in fonts
                  if f.get("kind") in ("ttf", "otf", "ttc")]
    best_atlas = None
    for t in tmp_conf:
        if t.get("atlas_letters") is not None:
            best_atlas = t["atlas_letters"] if best_atlas is None \
                else max(best_atlas, t["atlas_letters"])
    best_cmap = None
    for f in font_objs_read:
        if f.get("cmap_letters") is not None:
            best_cmap = f["cmap_letters"] if best_cmap is None \
                else max(best_cmap, f["cmap_letters"])
    dynamic = sum(1 for t in tmp_conf
                  if t.get("population_label") == "Dynamic")
    no_fallback = sum(1 for t in tmp_conf
                      if t.get("fallback_count") == 0)
    with_fallback = sum(1 for t in tmp_conf
                        if (t.get("fallback_count") or 0) > 0)
    return {
        "files_scanned": files_scanned,
        "fonts": len(fonts),
        "tmp_assets": len(tmp_assets),
        "tmp_confirmed": len(tmp_conf),
        "tmp_unconfirmed": len(tmp_unconf),
        "font_objects": len(font_objs),
        "font_objects_readable": len(font_objs_read),
        "standalone_fonts": len(standalone),
        "best_atlas_letters": best_atlas,
        "best_cmap_letters": best_cmap,
        "dynamic_count": dynamic,
        "no_fallback_count": no_fallback,
        "tmp_with_fallback": with_fallback,
    }


def _dynamic_rescue(a, source_cmap_letters=None, source_name=None):
    """Ket luan Dynamic co cuu duoc atlas thieu chu khong.

    a: dict atlas cua tier (letters/population_label/
    free_rects_fit_median_glyph/median_glyph_rect/atlas_width/
    atlas_height/used_area_px/atlas_used_pct/source_font_file/
    multi_atlas_enabled). source_cmap_letters: so chu TV font nguon
    ve duoc (tra theo PPtr source_font_file; None = khong biet).
    Suc chua uoc theo DIEN TICH trong: free_area / dien tich glyph
    trung binh (median used rect) — 1 free rect lon chua duoc nhieu
    glyph, dem so rect se danh gia thap (Kusa ja: 26 rect vua glyph
    nhung free area ~950k px2 chua duoc ~350 glyph co 52x52).
    Gioi han tren: min(suc chua dien tich, chu font nguon ve duoc).
    Tra (rescue, note): True/False/None (None = khong du du lieu).
    True luon kem [HYPOTHESIS] — can test runtime trong game that.
    """
    letters = a.get("letters")
    if letters is None or letters >= 134:
        return None, ""
    missing = 134 - letters
    label = a.get("population_label")
    if label == "Static":
        return False, ("Static: khong sinh them glyph khi chay "
                       "(thieu %d chu)." % missing)
    if label != "Dynamic":
        return None, ""
    src = a.get("source_font_file") or {}
    has_source = bool(src.get("path_id"))
    if not has_source:
        return False, ("Dynamic nhung khong co font nguon link — "
                       "khong sinh them duoc (thieu %d chu)." % missing)
    med = a.get("median_glyph_rect") or []
    aw, ah = a.get("atlas_width"), a.get("atlas_height")
    used_area = a.get("used_area_px")
    cap_area = None
    if (len(med) == 2 and med[0] and med[1] and aw and ah
            and used_area is not None):
        free_area = aw * ah - used_area
        cap_area = int(free_area // (med[0] * med[1])) if med[0] * med[1] else 0
    fits = a.get("free_rects_fit_median_glyph")
    used_pct = a.get("atlas_used_pct")
    src_txt = ""
    if cap_area is None:
        return None, ""
    cap = cap_area
    if source_cmap_letters is not None:
        cap = min(cap_area, source_cmap_letters)
        src_txt = (" font nguon %s chi ve duoc %d/134 chu"
                   % (source_name or "?", source_cmap_letters))
    multi = ""
    if a.get("multi_atlas_enabled") == 0:
        multi = " multiAtlas=0: khong mo rong them atlas khi day."
    if cap >= missing:
        return True, ("Dynamic co the sinh them %d chu con thieu "
                      "(suc chua uoc ~%d glyph theo dien tich trong, "
                      "%s free rect vua glyph co trung binh%s).%s "
                      "[HYPOTHESIS, chua chay thu game that]."
                      % (missing, cap,
                         fits if fits is not None else "?", src_txt, multi))
    return False, ("Dynamic KHONG cuu duoc: atlas da dung %s%%, chi con "
                   "%s free rect vua glyph co trung binh, suc chua uoc "
                   "~%d glyph%s — thieu %d chu.%s"
                   % (used_pct if used_pct is not None else "?",
                      fits if fits is not None else "?",
                      cap, src_txt, missing, multi))


# ------------------------------------------- bao cao 3 tang (gate)
def build_gate_report(fonts, target_lang="vi"):
    """Bao cao 3 tang cho cong (gate) + UI tu danh sach font (dang
    font_list, co the da enrich them truong 3 buoc).

    Tra {"path", "path_reason", "tiers": [...], "summary", "notes"}.
    path: donor | tmp_atlas | font_cmap | mixed | standalone |
    unreadable. Verdict cua cong KHONG phu thuoc ham nay (chi thong tin).
    """
    fonts = list(fonts or [])
    supplied = [f for f in fonts if f.get("supplied")]
    tmp_conf = [f for f in fonts
                if f.get("kind") == "tmp_font_asset"
                and f.get("confirmed", True)
                and f.get("status") in ("full", "partial")]
    tmp_all = [f for f in fonts if f.get("kind") == "tmp_font_asset"]
    unity_read = [f for f in fonts
                  if f.get("kind") == "unity_font"
                  and f.get("status") in ("full", "partial")]
    standalone = [f for f in fonts
                  if f.get("kind") in ("ttf", "otf", "ttc")]
    if supplied:
        path = "donor"
        reason = ("Font hien thi da chi dinh quyet dinh (donor) — font "
                  "trong game chi de tham khao.")
    elif tmp_conf and unity_read:
        path = "mixed"
        reason = ("Game co ca TMP_FontAsset (atlas SDF) va Font object "
                  "(cmap TTF nguon) — atlas quyet dinh chu hien thi, "
                  "cmap nguon cho biet Dynamic co sinh them duoc khong.")
    elif tmp_conf:
        path = "tmp_atlas"
        reason = ("Game dung TMP_FontAsset — atlas SDF quyet dinh chu "
                  "hien thi; cmap TTF nguon cho biet Dynamic co sinh "
                  "them glyph duoc khong.")
    elif unity_read:
        path = "font_cmap"
        reason = ("Game chi co Font object (khong TMP) — cmap TTF nguon "
                  "quyet dinh chu ve duoc.")
    elif standalone:
        path = "standalone"
        reason = ("Game co font TTF/OTF/TTC roi — cmap cua font roi "
                  "quyet dinh.")
    elif fonts:
        path = "unreadable"
        reason = ("Tim thay font trong game nhung chua doc duoc do phu "
                  "(font nhung khong trich duoc TTF / TMP chua xac nhan) "
                  "— cong fail-closed, hay chi dinh font hien thi.")
    else:
        path = "donor"
        reason = ("Game 0 font — dung font di kem Be Vietnam Pro "
                  "(donor, du 142/142). Chon che do donor o cong de DAT.")
    tiers = []
    for f in fonts:
        tier = {"name": f.get("name") or "?",
                "kind": f.get("kind") or "?",
                "source": f.get("source") or "",
                "path_id": f.get("path_id"),
                "status": f.get("status") or "unreadable",
                "atlas": None, "cmap": None}
        if f.get("kind") == "tmp_font_asset":
            miss_l, miss_m = _split_missing(
                f.get("missing_codepoints") or [])
            cov_l = 134 - len(miss_l)
            cov_m = 8 - len(miss_m)
            tier["atlas"] = {
                # Layout TMP moi (heuristic): missing rong = CHUA BIET,
                # khong phai du — dat None thay vi doan 134/134.
                "letters": None if f.get("atlas_unknown") else (
                    cov_l if f.get("covered") is not None else None),
                "marks": None if f.get("atlas_unknown") else (
                    cov_m if f.get("covered") is not None else None),
                "missing_letters_hex": _hex_list(miss_l),
                "missing_marks_hex": _hex_list(miss_m),
                "population_mode": f.get("population_mode"),
                "population_label": f.get("population_label"),
                "fallback_count": f.get("fallback_count"),
                "fallback_entries": f.get("fallback_entries"),
                "fallback_structural": f.get("fallback_structural"),
                "has_fallback": f.get("has_fallback"),
                "confirmed": f.get("confirmed", True),
                "method": f.get("method"),
                "atlas_width": f.get("atlas_width"),
                "atlas_height": f.get("atlas_height"),
                "atlas_used_pct": f.get("atlas_used_pct"),
                "used_area_px": f.get("used_area_px"),
                "free_rects_fit_median_glyph":
                    f.get("free_rects_fit_median_glyph"),
                "median_glyph_rect": f.get("median_glyph_rect"),
                "multi_atlas_enabled": f.get("multi_atlas_enabled"),
                "source_font_file": f.get("source_font_file"),
            }
        elif f.get("kind") == "unity_font":
            if f.get("status") in ("full", "partial"):
                miss_l, miss_m = _split_missing(
                    f.get("missing_codepoints") or [])
                tier["cmap"] = {
                    "letters": 134 - len(miss_l),
                    "marks": 8 - len(miss_m),
                    "missing_letters_hex": _hex_list(miss_l),
                    "missing_marks_hex": _hex_list(miss_m),
                    "flavor": f.get("flavor"),
                    "num_glyphs": f.get("num_glyphs"),
                }
            else:
                tier["cmap"] = {"status": "unreadable",
                                "reason": f.get("reason") or ""}
        elif f.get("kind") in ("ttf", "otf", "ttc"):
            miss_l, miss_m = _split_missing(
                f.get("missing_codepoints") or [])
            tier["cmap"] = {
                "letters": 134 - len(miss_l),
                "marks": 8 - len(miss_m),
                "missing_letters_hex": _hex_list(miss_l),
                "missing_marks_hex": _hex_list(miss_m),
                "flavor": f.get("flavor"),
                "num_glyphs": f.get("num_glyphs"),
            }
        tiers.append(tier)
    best_atlas = None
    best_cmap = None
    dynamic = 0
    no_fallback = 0
    with_fallback = 0
    # Tra cuu cmap font nguon theo path_id (cho ket luan Dynamic) +
    # atlas cua TMP theo path_id (cho phan tich fallback).
    font_by_pid = {}
    for f in fonts:
        if (f.get("kind") in ("unity_font", "ttf", "otf", "ttc")
                and f.get("path_id") is not None
                and f.get("cmap_letters") is not None):
            font_by_pid.setdefault(f["path_id"], f)
    tmp_by_pid = {}
    for t in tiers:
        if t.get("kind") == "tmp_font_asset" and t.get("path_id") is not None:
            tmp_by_pid[t["path_id"]] = t
    rescue_true = []
    rescue_false = []
    for t in tiers:
        a = t.get("atlas") or {}
        c = t.get("cmap") or {}
        if a.get("letters") is not None:
            best_atlas = a["letters"] if best_atlas is None \
                else max(best_atlas, a["letters"])
        if c.get("letters") is not None:
            best_cmap = c["letters"] if best_cmap is None \
                else max(best_cmap, c["letters"])
        if a.get("population_label") == "Dynamic":
            dynamic += 1
        if a.get("fallback_count") == 0:
            no_fallback += 1
        elif (a.get("fallback_count") or 0) > 0:
            with_fallback += 1
        # Ket luan Dynamic cho tung TMP (atlas day -> khong cuu duoc).
        if t.get("kind") == "tmp_font_asset" and a:
            src = a.get("source_font_file") or {}
            src_pid = src.get("path_id")
            src_f = font_by_pid.get(src_pid) if src_pid else None
            rescue, note = _dynamic_rescue(
                a,
                source_cmap_letters=(src_f.get("cmap_letters")
                                     if src_f else None),
                source_name=(src_f.get("name") if src_f else None))
            a["dynamic_rescue"] = rescue
            a["dynamic_note"] = note
            # Chi TMP Dynamic moi vao danh sach ket luan Dynamic;
            # TMP Static co note rieng o tier ("Static: khong sinh...").
            if a.get("population_label") == "Dynamic":
                if rescue is True:
                    rescue_true.append(t["name"])
                elif rescue is False:
                    rescue_false.append(t["name"])
    notes = []
    if rescue_true:
        notes.append(
            "Dynamic co the sinh them chu thieu o %s "
            "[HYPOTHESIS, chua chay thu game that] — xem chi tiet tung "
            "font ben duoi." % ", ".join(rescue_true))
    if rescue_false:
        notes.append(
            "Dynamic KHONG cuu duoc atlas thieu chu o %s — atlas da day "
            "va/hoac font nguon khong du chu; xem chi tiet tung font ben "
            "duoi." % ", ".join(rescue_false))
    # Fallback hien tai co cuu duoc tieng Viet khong?
    fb_useless = []
    for t in tiers:
        a = t.get("atlas") or {}
        entries = a.get("fallback_entries") or []
        if not entries:
            continue
        targets = []
        known = True
        for e in entries:
            if not isinstance(e, dict):
                continue
            tgt = tmp_by_pid.get(e.get("path_id"))
            if tgt is None:
                known = False
                continue
            targets.append(tgt)
        if targets and known:
            letters = [((tgt.get("atlas") or {}).get("letters"))
                       for tgt in targets]
            # Chi ket luan khi biet ro atlas cua MOI target (None =
            # chua biet, khong phai 0).
            if all(l is not None for l in letters) and all(
                    l == 0 for l in letters):
                fb_useless.append((t["name"], [x["name"] for x in targets]))
    for name, targets in fb_useless:
        notes.append(
            "Fallback hien tai cua %s tro toi %s (0/134 chu TV) — chi "
            "cuu duoc chu CJK, khong cuu duoc tieng Viet."
            % (name, ", ".join(targets)))
    if no_fallback:
        notes.append(
            "Khong co font du phong (m_FallbackFontAssetTable rong o %d "
            "TMP) — neu Dynamic that bai thi chu thieu hien o vuong; can "
            "them TMP_FontAsset fallback (vd tu Be Vietnam Pro) hoac bake "
            "lai atlas du 134 chu." % no_fallback)
    if best_cmap == 134:
        notes.append(
            "Ban dich NFC dung chu ghep san nen khong can dau ket hop roi; "
            "chi thieu khi text o dang NFD hoac TMP can compose.")
    # De xuat them fallback TV (uu tien font nguon da du 134/134
    # trong IPA; khong co thi donor Be Vietnam Pro).
    recommendation = None
    if best_atlas is not None and best_atlas < 134:
        full_sources = sorted(
            {f["name"] for f in fonts
             if f.get("kind") in ("unity_font", "ttf", "otf", "ttc")
             and f.get("cmap_letters") == 134})
        if full_sources:
            recommendation = (
                "Them 1 TMP_FontAsset fallback tieng Viet tu %s "
                "(134/134 chu, da co trong IPA) vao bang fallback cua "
                "cac font Latin — co che fallback da co san trong game."
                % ", ".join(full_sources))
        else:
            recommendation = (
                "Them 1 TMP_FontAsset fallback tieng Viet tu Be Vietnam "
                "Pro (donor, 142/142) — khong co font nguon nao trong "
                "IPA du 134/134 chu.")
        notes.append(recommendation)
    summary = {
        "path": path,
        "fonts_total": len(fonts),
        "tmp_total": len(tmp_all),
        "tmp_confirmed": len(tmp_conf),
        "tmp_unconfirmed": len(tmp_all) - len(tmp_conf),
        "font_objects": len([f for f in fonts
                             if f.get("kind") == "unity_font"]),
        "font_objects_readable": len(unity_read),
        "best_atlas_letters": best_atlas,
        "best_cmap_letters": best_cmap,
        "dynamic_count": dynamic,
        "no_fallback_count": no_fallback,
        "tmp_with_fallback": with_fallback,
        "dynamic_rescue": (True if rescue_true else
                           False if rescue_false else None),
    }
    return {"path": path, "path_reason": reason, "tiers": tiers,
            "summary": summary, "notes": notes,
            "recommendation": recommendation}


# ------------------------------------------- enrich scan cu (gate)
def _read_sf_for_source(path, source):
    """Doc SerializedFile tu 1 source cua font_list scan.

    source dang "bundle_path::node_name" (bundle trong IPA) hoac duong
    dan .assets (trong IPA hoac file roi). Thu VFS truoc, roi moi doc
    truc tiep tu dia (khi path chinh la file .assets do).
    """
    if "::" in source:
        bundle_name, node_name = source.split("::", 1)
        vfs = _containers.open_vfs(path)
        data = vfs.read(bundle_name)
        bundle = _uni.parse_bundle(data, where=bundle_name)
        return _uni.parse_serialized(bundle.get_entry(node_name),
                                     where=source)
    try:
        vfs = _containers.open_vfs(path)
        data = vfs.read(source)
        if _uni.detect_bundle(data) is not None:
            bundle = _uni.parse_bundle(data, where=source)
            entry = bundle.get_entry(bundle.entries[0].name)
            return _uni.parse_serialized(entry, where=source)
        return _uni.parse_serialized(data, where=source)
    except Exception:
        data = open(source, "rb").read()
        if _uni.detect_bundle(data) is not None:
            bundle = _uni.parse_bundle(data, where=source)
            entry = bundle.get_entry(bundle.entries[0].name)
            return _uni.parse_serialized(entry, where=source)
        return _uni.parse_serialized(data, where=source)


def enrich_fonts(path, fonts):
    """Bo sung du lieu 3 buoc vao danh sach font cua font_list scan.

    - unity_font "unreadable" -> trich m_FontData + check cmap (co the
      thanh full/partial, anh huong verdict cua cong theo luat cu:
      moi font doc duoc phai phu het chu dang dung).
    - tmp_font_asset -> them population_mode/fallback/source_font_file
      (chi thong tin, khong doi verdict).
    Tra (enriched_fonts, report) — report = build_gate_report(...).
    Loi o 1 SF chi ghi warning, khong fail ca enrich (best-effort).
    Khong sua dict goc (scan cache dung chung).
    """
    fonts = [dict(f) for f in (fonts or [])]
    warnings = []
    by_source = {}
    for idx, f in enumerate(fonts):
        src = f.get("source") or ""
        if src:
            by_source.setdefault(src, []).append(idx)
    sf_cache = {}
    for src, idxs in by_source.items():
        kinds = {fonts[i].get("kind") for i in idxs}
        if not (kinds & {"unity_font", "tmp_font_asset"}):
            continue
        try:
            sf = _read_sf_for_source(path, src)
        except Exception as exc:  # noqa: BLE001 - bo qua SF nay
            warnings.append("enrich %s loi: %s" % (src, exc))
            continue
        sf_cache[src] = sf
        res = scan_serialized_file(sf, src, heuristic=False, binary=False)
        by_pid_font = {f["path_id"]: f for f in res["fonts"]}
        by_pid_tmp = {t["path_id"]: t for t in res["tmp_assets"]}
        for i in idxs:
            f = fonts[i]
            pid = f.get("path_id")
            if f.get("kind") == "unity_font" and pid in by_pid_font:
                fonts[i] = by_pid_font[pid]
            elif (f.get("kind") == "tmp_font_asset"
                    and pid in by_pid_tmp):
                new = dict(f)
                extra = by_pid_tmp[pid]
                for key in ("population_mode", "population_label",
                            "fallback_count", "fallback_entries",
                            "fallback_structural", "has_fallback",
                            "source_font_file", "clear_dynamic_on_build",
                            "multi_atlas_enabled", "atlas_textures_count",
                            "char_table_count", "atlas_letters",
                            "atlas_marks", "confirmed", "method",
                            "atlas_width", "atlas_height", "atlas_padding",
                            "atlas_render_mode", "atlas_used_pct",
                            "used_area_px", "used_glyph_rects",
                            "free_glyph_rects", "median_glyph_rect",
                            "free_rects_fit_median_glyph",
                            "free_rects_fit_40x70", "largest_free_rect"):
                    if extra.get(key) is not None:
                        new[key] = extra[key]
                fonts[i] = new
    report = build_gate_report(fonts)
    report["warnings"] = warnings
    return fonts, report
