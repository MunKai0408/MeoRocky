"""Ghi nguoc font tieng Viet vao game (F2, nhom PB3).

Duong GHI (doi voi duong DOC cua sdk/font_check.py + sdk/tmp_sdf.py):
- (b) TMP Static SDF: tai tao atlas + bang glyph (sdk/tmp_sdf.regen_tmp_asset)
  roi CHEN bang glyph + bang character moi vao object TMP + thay atlas
  trong .resS (cung kich thuoc) + dung lai SerializedFile
  (sdk/unity.build_serialized). Kiem tra bang gate.check_tmp_sdf truoc
  khi tra file da sua.
- (c) Font TTF/OTF thuong: gop glyph Viet (sdk/font.merge_vietnamese)
  roi CHEN font da gop vao Font object (m_FontData) + dung lai
  SerializedFile. Dynamic TMP dung font nguon nay se tu ve du chu.
- (a) TMP Dynamic: them font fallback — lien ket toi TMP asset co san
  (duong typetree neu co, khong thi chen PPtr vao vector fallback da
  co cau truc) hoac lien ket toan cuc qua TMP Settings; neu chua co
  asset fallback thi NHAN BAN 1 TMP asset (clone_fallback_asset):
  TMP moi + Texture2D moi (atlas tai tao tu font donor, chi chua chu
  Viet thieu) + Material moi, atlas noi them vao cuoi .resS.

Fail-closed: khong chac thi tu choi ro rang (ma E_*), khong ghi lang
le sai. Ham o day CHI tra bytes da sua trong RAM (khong ghi file);
bridge quyet dinh dry-run / ghi file /tmp. Khong ghi de file goc.
Chi stdlib, Python 3.9.
"""
from __future__ import annotations

import statistics
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from core.errors import PB3Error
from sdk import font as _font
from sdk import font_check as _fc
from sdk import gate as _gate
from sdk import sdf as _sdf
from sdk import tmp_font as _tf
from sdk import tmp_sdf as _ts
from sdk import unity as _uni

WHERE = "font_writeback"

# Bo 142 = 134 chu + 8 dau ket hop (xem sdk/tmp_font).
VIETNAMESE_142 = sorted(set(_tf.vietnamese_letters_134())
                        | set(_tf.combining_marks_8()))

# Nguong MAE (xem diagnose_mae): PASS < 3.0 [DA DO THAT]; WARN khi
# median < 8 + glyph tot nhat < 2 + pct<3 >= 25% (nghi font-version).
MAE_PASS = 3.0
MAE_WARN_MEDIAN = 8.0
MAE_WARN_BEST_Q = 2.0
MAE_WARN_PCT_LT3 = 25.0

# Tham so atlas fallback (xem sdk/tmp_font.build_fallback_plan): kep
# trong [256, 1024]; thu point size giam dan khi khong xep vua.
FALLBACK_PT_LADDER = (None, 90, 64, 48, 36, 24)
FALLBACK_ATLAS_MIN = 256
FALLBACK_ATLAS_MAX = 1024


def _err(code, msg, where=WHERE):
    return PB3Error(code, msg, where=where)


def _i32(data, off, endian="<"):
    return struct.unpack_from(endian + "i", data, off)[0]


def _u32(data, off, endian="<"):
    return struct.unpack_from(endian + "I", data, off)[0]


def _i64(data, off, endian="<"):
    return struct.unpack_from(endian + "q", data, off)[0]


# ------------------------------------------------------------- phan loai ca
def classify_case(entry):
    """Phan loai 1 muc font_check thanh duong ghi nguoc (F2).

    entry: dict chuan hoa {"kind": "tmp"|"font", "name", "confirmed",
    "population": "Dynamic"|"Static"|None, "atlas_letters",
    "atlas_marks", "source_letters", "source_marks",
    "source_present": bool, "atlas_is_bitmap": bool,
    "atlas_used_pct", "fallback_count"}. Tra {"route", "reason_vi",
    "action"}. Route "none" = du roi / khong co duong ghi (ly do trong
    reason_vi). Khong doc file, chi phan loai.
    """
    kind = (entry or {}).get("kind")
    name = (entry or {}).get("name") or "?"
    if kind == "font":
        covered = int((entry.get("source_letters") or 0)
                      + (entry.get("source_marks") or 0))
        if covered >= 142:
            return {"route": "none",
                    "reason_vi": "Font %s du 142/142, khong can ghi." % name,
                    "action": None}
        return {"route": "font_merge",
                "reason_vi": "Font %s thieu %d/142 (cmap) — gop glyph Viet "
                             "tu donor roi chen vao Font object." % (
                                 name, 142 - covered),
                "action": "writeback_font_merge"}
    if kind != "tmp":
        return {"route": "none",
                "reason_vi": "Muc khong phai tmp/font (%r) — khong co duong "
                             "ghi." % (kind,),
                "action": None}
    if not entry.get("confirmed"):
        return {"route": "none",
                "reason_vi": "TMP %s chua xac nhan layout (unconfirmed) — "
                             "khong ghi khi chua biet bo cuc byte." % name,
                "action": None}
    atlas_cov = int((entry.get("atlas_letters") or 0)
                    + (entry.get("atlas_marks") or 0))
    if atlas_cov >= 142:
        return {"route": "none",
                "reason_vi": "TMP %s du 142/142 trong atlas, khong can ghi."
                             % name,
                "action": None}
    pop = entry.get("population")
    if pop == "Dynamic":
        src_cov = int((entry.get("source_letters") or 0)
                      + (entry.get("source_marks") or 0))
        if not entry.get("source_present"):
            return {"route": "tmp_dynamic_fallback",
                    "reason_vi": "TMP %s Dynamic nhung khong co font nguon "
                                 "nhung — can asset fallback (clone tu "
                                 "donor)." % name,
                    "action": "clone_fallback_asset"}
        if src_cov < 142:
            return {"route": "tmp_dynamic_source_merge",
                    "reason_vi": "TMP %s Dynamic, font nguon chi %d/142 — "
                                 "gop glyph Viet vao font nguon, Dynamic se "
                                 "tu ve." % (name, src_cov),
                    "action": "writeback_font_merge"}
        return {"route": "tmp_dynamic_fallback",
                "reason_vi": "TMP %s Dynamic, font nguon du 142/142 nhung "
                             "atlas dung %s%% — can asset fallback (clone) "
                             "vi atlas khong con cho." % (
                                 name, entry.get("atlas_used_pct")),
                "action": "clone_fallback_asset"}
    if pop == "Static":
        if entry.get("atlas_is_bitmap"):
            return {"route": "none",
                    "reason_vi": "TMP %s Static nhung atlas la BITMAP "
                                 "(khong phai SDF) — khong tai tao SDF duoc."
                                 % name,
                    "action": None}
        if not entry.get("source_present"):
            return {"route": "none",
                    "reason_vi": "TMP %s Static nhung khong co font nguon "
                                 "nhung — khong tai tao atlas duoc." % name,
                    "action": None}
        return {"route": "tmp_static_sdf",
                "reason_vi": "TMP %s Static SDF, font nguon co san — tai "
                             "tao atlas + bang glyph roi ghi nguoc." % name,
                "action": "writeback_tmp_sdf"}
    return {"route": "none",
            "reason_vi": "TMP %s khong ro AtlasPopulationMode — khong doan "
                         "duong ghi." % name,
            "action": None}


# ------------------------------------------------------- chan doan MAE (F2)
def diagnose_mae(rows):
    """Chan doan vi sao atlas FAIL nguong MAE, tu rows cua
    sdk/sdf.measure_atlas_error (moi row: {"mae", ...}).

    Quy tac [HYPOTHESIS] (can kiem chung tren game that):
    - glyph tot nhat (quartile duoi) MAE < 2.0 + median >= 3.0: generator
      dung (dat glyph tot), lech tap trung o 1 so glyph — nghi
      font-version (font nhung khac version voi font build atlas goc),
      khong phai bug generator.
    - quartile duoi cung cao (>= 2.0): lech he thong — nghi padding /
      point size / spread sai, can do lai tham so.
    Tra {"median_mae", "best_quartile_mae", "pct_lt3", "verdict",
    "reason_vi", "threshold"}. threshold = nguong de nghi ap cho asset
    nay (xem mae_threshold_advice).
    """
    maes = sorted(float(r.get("mae") or 0.0) for r in (rows or [])
                  if r.get("note") != "placeholder")
    if not maes:
        return {"median_mae": None, "best_quartile_mae": None,
                "pct_lt3": 0.0, "verdict": "unknown",
                "reason_vi": "Khong co glyph do duoc — khong ket luan.",
                "threshold": "FAIL"}
    n = len(maes)
    median = statistics.median(maes)
    q = maes[:max(1, n // 4)]
    best_q = statistics.median(q)
    pct_lt3 = 100.0 * sum(1 for m in maes if m < 3.0) / n
    threshold, why = mae_threshold_advice(median, best_q, pct_lt3)
    if median < MAE_PASS:
        verdict = "pass"
        reason = ("Median MAE %.2f < %.1f — generator khop atlas goc."
                  % (median, MAE_PASS))
    elif best_q < MAE_WARN_BEST_Q:
        verdict = "font_version_drift"
        reason = ("Median MAE %.2f >= %.1f nhung quartile tot nhat chi "
                  "%.2f (%.0f%% glyph < 3.0) — generator dat glyph tot, "
                  "lech tap trung o 1 so glyph: NGHI font-version (font "
                  "nhung khac version voi font build atlas goc), khong "
                  "phai bug generator [HYPOTHESIS]." % (
                      median, MAE_PASS, best_q, pct_lt3))
    else:
        verdict = "fail"
        reason = ("Median MAE %.2f, quartile tot nhat %.2f — lech HE "
                  "THONG, khong phai font-version: nghi padding / point "
                  "size / spread sai, can do lai tham so truoc khi ghi."
                  % (median, best_q))
    return {"median_mae": round(median, 2),
            "best_quartile_mae": round(best_q, 2),
            "pct_lt3": round(pct_lt3, 1), "verdict": verdict,
            "reason_vi": reason + " " + why,
            "threshold": threshold}


def mae_threshold_advice(median, best_quartile_mae, pct_lt3):
    """Nguong de nghi theo loai loi [HYPOTHESIS]:
    - PASS: median < 3.0 (nguong cu, [DA DO THAT]).
    - WARN (chap nhan co ghi chu, can kiem tra bang mat tren game that):
      median < 8.0 + quartile tot nhat < 2.0 + pct<3 >= 25% — dung dang
      LiberationSans [DA DO THAT: median 4.20, pct<3 35%].
    - FAIL: con lai — NotoSans [DA DO THAT: median 7.55, pct<3 9%]
      van FAIL vi qua it glyph khop.
    Tra (threshold, ly_do_vi).
    """
    if median is None:
        return "FAIL", "Khong do duoc MAE."
    if median < MAE_PASS:
        return "PASS", "Median MAE %.2f < %.1f (nguong cu)." % (
            median, MAE_PASS)
    if (median < MAE_WARN_MEDIAN
            and best_quartile_mae is not None
            and best_quartile_mae < MAE_WARN_BEST_Q
            and pct_lt3 >= MAE_WARN_PCT_LT3):
        return ("WARN", "Median MAE %.2f (%.0f%% glyph < 3.0, quartile tot "
                        "nhat %.2f): dang font-version — CHAP NHAN CO GHI "
                        "CHU, kiem tra bang mat tren game that truoc khi "
                        "giao." % (median, pct_lt3, best_quartile_mae))
    return ("FAIL", "Median MAE %.2f, chi %.0f%% glyph < 3.0 — khong dat, "
                    "khong doan la font-version." % (median, pct_lt3))


# ------------------------------------------------------- helper byte thap
def _pptr_bytes(endian, file_id, path_id):
    return struct.pack(endian + "i", file_id) + struct.pack(
        endian + "q", path_id)


def _find_pptr_occurrences(raw, endian, path_id, file_id=0):
    """Vi tri cac PPtr (file_id, path_id) trong bytes (buoc 4)."""
    needle = _pptr_bytes(endian, file_id, path_id)
    out = []
    pos = 0
    while True:
        pos = raw.find(needle, pos)
        if pos < 0:
            return out
        out.append(pos)
        pos += len(needle)


def _replace_pptr_all(raw, endian, old_pid, new_pid, file_id=0):
    """Thay MOI PPtr (file_id, old_pid) -> (file_id, new_pid) (cung do
    dai 12B). Tra (bytes_moi, so_lan_thay). 0 lan = loi o ben goi."""
    needle = _pptr_bytes(endian, file_id, old_pid)
    repl = _pptr_bytes(endian, file_id, new_pid)
    count = raw.count(needle)
    if count == 0:
        return bytes(raw), 0
    return bytes(raw).replace(needle, repl), count


def _replace_aligned_string_at(raw, off, new_text, endian="<"):
    """Thay chuoi length-prefixed + align 4 tai off (vd m_Name, ten
    FaceInfo). Tra bytes moi (dai hon/ngan hon tuy ten). Loi neu off
    khong phai chuoi hop le."""
    if off < 0 or off + 4 > len(raw):
        raise _err("E_CORRUPT", "vi tri chuoi %d ngoai pham vi object" % off)
    (old_len,) = struct.unpack_from(endian + "i", raw, off)
    if old_len < 0 or off + 4 + old_len > len(raw):
        raise _err("E_CORRUPT",
                   "chuoi tai %d dai %d khong hop le" % (off, old_len))
    new_b = new_text.encode("utf-8")
    old_end = (off + 4 + old_len + 3) & ~3
    new_field = struct.pack(endian + "i", len(new_b)) + new_b
    while len(new_field) % 4:
        new_field += b"\x00"
    return bytes(raw[:off]) + new_field + bytes(raw[old_end:])


def replace_mb_name(raw, endian, new_name):
    """Doi ten MonoBehaviour (m_Name o header, offset 28)."""
    hdr, problem = _tf.read_monobehaviour_header(raw, endian)
    if problem is not None or hdr is None:
        raise _err("E_CORRUPT",
                   "khong doc duoc header MonoBehaviour: %s" % problem)
    old_name = hdr["name"] or ""
    data_start = hdr["data_start"]
    field_len = (4 + len(old_name.encode("utf-8")) + 3) & ~3
    name_off = data_start - field_len
    if name_off < 0:
        raise _err("E_CORRUPT", "vi tri ten MB am (%d)" % name_off)
    return _replace_aligned_string_at(raw, name_off, new_name, endian)


# ------------------------------------------------------------ bang character
def _read_char_records(raw, off, count, endian):
    """Doc count record character 16B tai off+4 (et u32, unicode u32,
    glyphIndex u32, scale f32). Tra list dict hoac None neu record nao
    khong hop le."""
    out = []
    for i in range(count):
        o = off + 4 + 16 * i
        if o + 16 > len(raw):
            return None
        et, u, g = struct.unpack_from(endian + "III", raw, o)
        (s,) = struct.unpack_from(endian + "f", raw, o + 12)
        if not (et == 1 and 0 < u <= 0x10FFFF and g < 100000
                and 0 < s <= 1000):
            return None
        out.append({"element_type": et, "unicode": u, "glyph_index": g,
                    "scale": s})
    return out


def locate_char_table(raw, endian, gt):
    """Tim bang character cua 1 TMP asset (khong gia dinh vi tri).

    gt: dict glyph_table tu decode_object (offset/stride/count/layout).
    Quet toan object tim vung [count int32][count record 16B hop le]
    voi count in {glyph_count-1, glyph_count, glyph_count+1} (+0 cho
    asset rong): layout cu thi unicode phai trung tap id glyph;
    layout moi thi glyphIndex phai la id co trong bang glyph.
    Tra {"offset", "count", "records"}. Khong tim thay -> E_CHAR_TABLE.
    """
    off = gt["offset"]
    cnt = gt["count"]
    stride = gt["stride"]
    layout = gt["layout"]
    if off is None or cnt is None or stride is None:
        raise _err("E_CHAR_TABLE",
                   "thieu offset/stride/count bang glyph — khong tim duoc "
                   "bang character")
    if layout == "old":
        ids = None
        try:
            recs = _tf.read_glyph_records(raw, off, cnt, stride, endian)
            ids = set(r["id"] for r in recs)
        except Exception:
            ids = None
    else:
        try:
            recs = _tf.read_glyph_records(raw, off, cnt, stride, endian)
            ids = set(r["id"] for r in recs)
        except Exception:
            ids = None
    if ids is None:
        raise _err("E_CHAR_TABLE",
                   "khong doc duoc bang glyph — khong tim duoc bang "
                   "character")
    if cnt == 0:
        # Asset rong: bang character (neu co) phai ke bang glyph (truoc
        # hoac sau) — khong quet toan object (int32 0 o dau cung khop).
        allowed = [0, 1]
    else:
        allowed = sorted(set([cnt - 1, cnt, cnt + 1]))
    cands = []
    for p in range(0, len(raw) - 4, 4):
        c = _i32(raw, p, endian)
        if c not in allowed:
            continue
        if p + 4 + 16 * c > len(raw):
            continue
        if cnt == 0 and not (p == off + 4 or p + 4 + 16 * c == off):
            continue
        recs = _read_char_records(raw, p, c, endian)
        if recs is None:
            continue
        if layout == "old":
            if c and set(r["unicode"] for r in recs) != ids:
                continue
        else:
            if c and any(r["glyph_index"] not in ids for r in recs):
                continue
        cands.append({"offset": p, "count": c, "records": recs})
    if not cands:
        raise _err("E_CHAR_TABLE",
                   "khong tim thay bang character hop le (glyph count %d, "
                   "layout %s) — bo cuc TMP nay chua ho tro ghi nguoc"
                   % (cnt, layout))
    if len(cands) > 1:
        # Uu tien vung gan bang glyph nhat (Kusa/PB3 moi: ngay sau;
        # PB3 cu: ngay truoc — ca 2 deu gan).
        gt_end = off + 4 + stride * cnt
        cands.sort(key=lambda d: min(abs(d["offset"] - gt_end),
                                     abs(d["offset"] + 4 + 16 * d["count"]
                                         - off)))
    return cands[0]


def build_char_table_bytes(old_records, new_glyphs, layout):
    """Dung bang character moi (count + record 16B) tu glyph moi.

    old_records: record cu (giu thu tu, map lai glyphIndex theo vi tri
    moi). new_glyphs: list dict cua generate_tmp_atlas (co "cp",
    "gid"), theo thu tu bang glyph moi. Char moi (cp chua co) chen
    them cuoi, sap theo cp. Layout cu: glyphIndex = vi tri + 1
    (record 0 la .notdef — [DA DO THAT] Aniara PB3: char 1..40 ung
    glyph 0..39). Layout moi: glyphIndex = gid.
    """
    pos = {}
    gid = {}
    for i, g in enumerate(new_glyphs):
        pos[g["cp"]] = i
        gid[g["cp"]] = g["gid"]
    out = []
    seen = set()
    for r in (old_records or []):
        u = r["unicode"]
        if u in pos:
            gi = pos[u] + 1 if layout == "old" else gid[u]
        else:
            # Glyph cu khong con trong bang moi (font nguon thieu) —
            # giu nguyen record (ben goi phai tu choi truoc neu co).
            gi = r["glyph_index"]
        out.append({"element_type": 1, "unicode": u, "glyph_index": gi,
                    "scale": r.get("scale") or 1.0})
        seen.add(u)
    for g in sorted(new_glyphs, key=lambda d: d["cp"]):
        if g["cp"] in seen:
            continue
        gi = pos[g["cp"]] + 1 if layout == "old" else g["gid"]
        out.append({"element_type": 1, "unicode": g["cp"],
                    "glyph_index": gi, "scale": 1.0})
        seen.add(g["cp"])
    buf = bytearray()
    buf += struct.pack("<i", len(out))
    for r in out:
        buf += struct.pack("<III", r["element_type"], r["unicode"],
                           r["glyph_index"])
        buf += struct.pack("<f", r["scale"])
    return bytes(buf), out


def splice_tmp_object(raw, endian, new_table, new_char_table, new_count,
                      gt, char_loc, faceinfo_count_offset=None):
    """Chen bang glyph + bang character moi vao object TMP cu.

    Thay 2 vung (bang glyph [gt_off, gt_off+4+stride*count),
    bang character [char_off, char_off+4+16*char_count)) — khong gia
    dinh thu tu 2 bang (PB3 cu: character truoc glyph; Kusa/PB3 moi:
    glyph truoc character). Cap nhat CharacterCount trong FaceInfo
    (neu co offset; offset dich theo delta cua vung chen truoc no).
    Tra bytes object moi. Sau do ben goi phai decode lai de kiem tra.
    """
    gt_off = gt["offset"]
    gt_cnt = gt["count"]
    gt_stride = gt["stride"]
    gt_end = gt_off + 4 + gt_stride * gt_cnt
    ch_off = char_loc["offset"]
    ch_cnt = char_loc["count"]
    ch_end = ch_off + 4 + 16 * ch_cnt
    if gt_off < 0 or gt_end > len(raw) or ch_off < 0 or ch_end > len(raw):
        raise _err("E_CORRUPT", "bang glyph/character vuot qua object")
    if not (gt_end <= ch_off or ch_end <= gt_off):
        raise _err("E_CORRUPT",
                   "bang glyph va bang character chong lan nhau — bo cuc "
                   "TMP nay chua ho tro ghi nguoc")
    # Chen tu vung SAU ra truoc de offset vung truoc khong doi.
    regions = sorted([(gt_off, gt_end, new_table),
                      (ch_off, ch_end, new_char_table)],
                     key=lambda r: r[0], reverse=True)
    out = bytes(raw)
    new_count_off = faceinfo_count_offset
    for (start, end, blob) in regions:
        delta = len(blob) - (end - start)
        if new_count_off is not None and new_count_off >= end:
            new_count_off += delta
        elif (new_count_off is not None and start <= new_count_off < end):
            raise _err("E_CORRUPT",
                       "CharacterCount trong FaceInfo nam trong bang glyph/"
                       "character — khong sua duoc")
        out = out[:start] + blob + out[end:]
    if new_count_off is not None:
        if new_count_off < 0 or new_count_off + 4 > len(out):
            raise _err("E_CORRUPT",
                       "faceinfo_count_offset %s ngoai pham vi object sau "
                       "khi chen" % (new_count_off,))
        out = (out[:new_count_off]
               + struct.pack(endian + "i", new_count)
               + out[new_count_off + 4:])
    return out


# ------------------------------------------------- (b) TMP Static SDF ghi nguoc
def _regen_error_code(exc):
    """Anh xa loi regen (tmp_sdf, message-only) sang ma E_*."""
    msg = str(exc)
    if "bitmap" in msg:
        return "E_BITMAP_ATLAS"
    if "khong co font nguon" in msg:
        return "E_NO_SOURCE_FONT"
    if "khong xep vua" in msg:
        return "E_ATLAS_FULL"
    if "khong co atlas" in msg or "khong stream" in msg:
        return "E_NO_ATLAS"
    if "khong co bang glyph" in msg or "khong doc duoc TMP" in msg:
        return "E_TMP_LAYOUT"
    return "E_VERIFY_FAILED"


def writeback_tmp_sdf(sf_bytes, tmp_path_id, extra_codepoints,
                      ress_slice):
    """Ghi nguoc atlas SDF tai tao vao 1 TMP asset Static (F2-b).

    sf_bytes: bytes file .assets/.unity3d (SerializedFile). tmp_path_id:
    path_id TMP asset. extra_codepoints: list int can them (vd chu Viet
    thieu). ress_slice: ham(path, offset, size) -> bytes doc lat cat
    .resS (nhu tmp_sdf.regen_tmp_asset).
    Tra {"patched_sf_bytes", "atlas_patch": {"path","offset","size"},
    "new_atlas_bytes", "name", "layout", "glyphs_old/new", "added",
    "gate": <ket qua gate.check_tmp_sdf>, "mae": <diagnose_mae>,
    "warnings": [...]}. CHI tra bytes trong RAM, khong ghi file.
    Tu choi (E_*): bitmap / khong font nguon / atlas khong xep vua /
    regen lam roi glyph cu / bang character khong tim thay / gate FAIL.
    """
    try:
        sf = _uni.parse_serialized(bytes(sf_bytes), where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "khong parse duoc SerializedFile: %s" % exc)
    try:
        raw = sf.object_bytes(tmp_path_id)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_INPUT_NOT_FOUND",
                   "khong co TMP object %s: %s" % (tmp_path_id, exc))
    try:
        rep = _ts.regen_tmp_asset(sf, tmp_path_id,
                                  list(extra_codepoints or []), ress_slice)
    except Exception as exc:  # noqa: BLE001 - anh xa sang ma E_*
        raise _err(_regen_error_code(exc), "tai tao atlas that bai: %s" % exc)
    layout = rep["layout"]
    dec = _tf.decode_object(raw, sf.endian)
    gt = dec.get("glyph_table")
    if not gt:
        raise _err("E_TMP_LAYOUT",
                   "asset %s: khong co bang glyph" % rep["name"])
    # Bang character: phai tim thay + map duoc moi glyph cu sang bang moi.
    char_loc = locate_char_table(raw, sf.endian, gt)
    # Regen co the lam roi glyph cu neu font nguon thieu (font-version):
    # glyph cu phai con du trong bang moi, khong thi tu choi (khong ghi
    # de mat chu). Layout cu: id = codepoint; layout moi: id = gid.
    font_data = _ts.find_source_font(
        sf, rep["name"], (dec.get("font_info") or {}).get("family_name"))[0]
    font = _sdf.parse_font(font_data)
    gid2cp = {}
    for cp, gid in font.cmap.items():
        gid2cp.setdefault(gid, cp)
    old_recs = _tf.read_glyph_records(raw, gt["offset"], gt["count"],
                                      gt["stride"], sf.endian)
    new_table_recs = _tf.read_glyph_records(
        rep["new_table"], 0, rep["new_count"], gt["stride"], sf.endian)
    if layout == "old":
        old_keys = set(r["id"] for r in old_recs)
        new_keys = set(r["id"] for r in new_table_recs)
        dropped = sorted(old_keys - new_keys)
        new_glyphs = [{"cp": r["id"], "gid": gid2cp.get(r["id"])}
                      for r in new_table_recs]
    else:
        old_keys = set(r["id"] for r in old_recs)
        new_keys = set(r["id"] for r in new_table_recs)
        dropped = sorted(old_keys - new_keys)
        new_glyphs = [{"cp": gid2cp.get(r["id"]), "gid": r["id"]}
                      for r in new_table_recs]
        new_glyphs = [g for g in new_glyphs if g["cp"] is not None]
    if dropped:
        raise _err("E_REGEN_DROPS_GLYPHS",
                   "tai tao lam roi %d glyph cu (%s) — font nguon thieu "
                   "glyph atlas goc co (font-version); khong ghi de mat "
                   "chu." % (len(dropped),
                             ", ".join("U+%04X" % c for c in dropped[:8])
                             if layout == "old" else
                             ", ".join("gid %d" % c for c in dropped[:8])))
    new_char_table, _new_entries = build_char_table_bytes(
        char_loc["records"], new_glyphs, layout)
    fi = dec.get("font_info") or {}
    count_off = fi.get("count_offset")
    new_obj = splice_tmp_object(raw, sf.endian, rep["new_table"],
                                new_char_table, rep["new_count"], gt,
                                char_loc, count_off)
    # Dung lai SerializedFile + kiem tra lai toan bo.
    patched = _uni.build_serialized(sf, {tmp_path_id: new_obj})
    try:
        sf2 = _uni.parse_serialized(patched, where="font_writeback")
        raw2 = sf2.object_bytes(tmp_path_id)
        dec2 = _tf.decode_object(raw2, sf2.endian)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "file da sua khong parse lai duoc: %s" % exc)
    gt2 = dec2.get("glyph_table") or {}
    if gt2.get("count") != rep["new_count"]:
        raise _err("E_VERIFY_FAILED",
                   "bang glyph sau khi ghi co %s record, ky vong %d"
                   % (gt2.get("count"), rep["new_count"]))
    try:
        char2 = locate_char_table(raw2, sf2.endian, gt2)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "bang character sau khi ghi khong tim thay: %s" % exc)
    if char2["count"] != len(_new_entries):
        raise _err("E_VERIFY_FAILED",
                   "bang character sau khi ghi co %d record, ky vong %d"
                   % (char2["count"], len(_new_entries)))
    aw, ah = (int(x) for x in rep["atlas"].split("x"))
    gate_res = _gate.check_tmp_sdf(rep["new_table"], layout,
                                   rep["new_atlas"], aw, ah,
                                   expected_count=rep["new_count"])
    if gate_res.get("status") != "pass":
        raise _err("E_VERIFY_FAILED",
                   "gate.check_tmp_sdf FAIL sau khi ghi: %s"
                   % (gate_res.get("summary") or gate_res))
    # Atlas patch (.resS, cung kich thuoc).
    _apid, tinfo = _ts.find_atlas_texture(sf, rep["name"])
    st = (tinfo or {}).get("stream") or {}
    if not st or st.get("size") != len(rep["new_atlas"]):
        raise _err("E_NO_ATLAS",
                   "atlas stream %s khong khop atlas moi %d byte"
                   % (st, len(rep["new_atlas"])))
    # Chan doan MAE tu rows cua regen (khong do lai).
    warnings = []
    try:
        mae = diagnose_mae(rep.get("mae_rows") or [])
        if mae.get("median_mae") is None:
            raise ValueError("regen khong tra mae_rows")
    except Exception as exc:  # noqa: BLE001 - MAE la phu, khong chan ghi
        mae = {"median_mae": rep.get("median_mae"),
               "best_quartile_mae": None, "pct_lt3": rep.get("pct_lt3"),
               "verdict": "unknown",
               "reason_vi": "Khong chan doan duoc MAE day du: %s" % exc,
               "threshold": "FAIL" if not rep.get("pass") else "PASS"}
    if (dec2.get("font_info") or {}).get("declared_char_count") not in (
            None, rep["new_count"]):
        warnings.append(
            "CharacterCount trong FaceInfo sau khi ghi la %s, khac %d — "
            "ban FaceInfo tim duoc co the la ban sao; kiem tra bang mat "
            "tren game that." % (
                (dec2.get("font_info") or {}).get("declared_char_count"),
                rep["new_count"]))
    if mae.get("threshold") == "WARN":
        warnings.append("MAE o muc WARN (font-version): " + mae["reason_vi"])
    return {"patched_sf_bytes": patched,
            "atlas_patch": {"path": st["path"], "offset": st["offset"],
                            "size": st["size"]},
            "new_atlas_bytes": rep["new_atlas"],
            "name": rep["name"], "layout": layout,
            "point_size": rep["point_size"], "padding": rep["padding"],
            "atlas": rep["atlas"],
            "glyphs_old": rep["glyphs_old"],
            "glyphs_new": rep["glyphs_new"],
            "added": rep["added"], "unfitted": rep["unfitted"],
            "gate": {"status": gate_res.get("status"),
                     "summary": gate_res.get("summary")},
            "mae": mae, "warnings": warnings}


# ------------------------------------------------ (c) Font TTF/OTF ghi nguoc
def writeback_font_merge(sf_bytes, font_path_id, donor_bytes,
                         extra=None):
    """Gop glyph Viet vao 1 Font object nhung (F2-c).

    sf_bytes: bytes SerializedFile. font_path_id: path_id Font object
    (class 128). donor_bytes: font donor (Be Vietnam Pro). extra: list
    cp them (mac dinh bo 142 Viet). Tra {"patched_sf_bytes", "name",
    "covered_before/after", "added", "already", "donor_missing",
    "warnings"}. TTC tu choi (con field sau m_FontData).
    """
    try:
        sf = _uni.parse_serialized(bytes(sf_bytes), where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "khong parse duoc SerializedFile: %s" % exc)
    try:
        raw = sf.object_bytes(font_path_id)
        obj = sf.get_object(font_path_id)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_INPUT_NOT_FOUND",
                   "khong co Font object %s: %s" % (font_path_id, exc))
    if obj.class_id != 128:
        raise _err("E_CONFIG_INVALID",
                   "object %d khong phai Font (class %d)"
                   % (font_path_id, obj.class_id))
    name = _font._read_first_aligned_string(raw, sf.endian) or (
        "Font #%d" % font_path_id)
    blobs = _fc.find_sfnt_blobs(raw)
    if not blobs:
        raise _err("E_NO_SOURCE_FONT",
                   "Font %s khong co blob sfnt nao" % name)
    if len(blobs) > 1:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "Font %s co %d blob sfnt — khong biet blob nao la "
                   "m_FontData" % (name, len(blobs)))
    blob = blobs[0]
    if blob["flavor"] == "TTC":
        raise _err("E_UNSUPPORTED_FORMAT",
                   "Font %s la TTC — chua ho tro ghi nguoc (con field sau "
                   "m_FontData)" % name)
    if blob["extent"] is None:
        raise _err("E_UNSUPPORTED_FORMAT",
                   "Font %s: khong biet do dai blob sfnt" % name)
    start = blob["offset"]
    end = start + blob["extent"]
    if start < 4 or end > len(raw):
        raise _err("E_CORRUPT", "blob sfnt vuot qua Font object")
    # m_FontData la vector<byte>: count int32 ngay truoc blob phai bang
    # do dai blob — khong khop thi khong doan vi tri count.
    (count_before,) = struct.unpack_from(sf.endian + "i", raw, start - 4)
    if count_before != blob["extent"]:
        raise _err("E_TMP_LAYOUT",
                   "Font %s: int32 truoc blob sfnt la %d, khac do dai blob "
                   "%d — khong chac la count cua m_FontData"
                   % (name, count_before, blob["extent"]))
    old_font = bytes(raw[start:end])
    before = _fc.check_source_font(old_font, name)
    try:
        res = _font.merge_vietnamese(old_font, bytes(donor_bytes),
                                     extra=extra)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED", "gop glyph that bai: %s" % exc)
    new_font = res.data
    new_obj = (bytes(raw[:start - 4])
               + struct.pack(sf.endian + "i", len(new_font)) + new_font
               + bytes(raw[end:]))
    patched = _uni.build_serialized(sf, {font_path_id: new_obj})
    try:
        sf2 = _uni.parse_serialized(patched, where="font_writeback")
        raw2 = sf2.object_bytes(font_path_id)
        blobs2 = _fc.find_sfnt_blobs(raw2)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "file da sua khong parse lai duoc: %s" % exc)
    if len(blobs2) != 1:
        raise _err("E_VERIFY_FAILED",
                   "sau khi ghi Font %s co %d blob sfnt (ky vong 1)"
                   % (name, len(blobs2)))
    after = _fc.check_source_font(bytes(raw2[blobs2[0]["offset"]:
                                             blobs2[0]["offset"]
                                             + blobs2[0]["extent"]]), name)
    warnings = list(res.warnings or [])
    if after.get("covered") is not None and after["covered"] < 142:
        warnings.append("Sau khi gop Font %s chi %s/142 (donor thieu %s) — "
                        "van thieu, can donor khac."
                        % (name, after["covered"],
                           after.get("missing_codepoints", [])[:8]))
    return {"patched_sf_bytes": patched, "name": name,
            "covered_before": before.get("covered"),
            "covered_after": after.get("covered"),
            "added": list(res.added), "already": list(res.already),
            "donor_missing": list(res.donor_missing),
            "glyphs_copied": int(res.glyphs_copied),
            "glyphs_total": int(res.glyphs_total),
            "warnings": warnings}


# ------------------------------------------------- (a) lien ket fallback
def _is_confirmed_tmp(dec):
    """1 decode dict co phai TMP_FontAsset da xac nhan? Can CA 3 dieu
    kien (fail-closed — chi 1 trong 3 thi chua du):

    1. Decode duoc FaceInfo hop le (ten font + point size) — bang
       glyph don thuan KHONG du: find_glyph_table la quet heuristic,
       no nhan ca "bang" 1 record gia trong MonoBehaviour thuong.
    2. Ten asset (m_Name) ket thuc bang "SDF" / "SDF - DYNAMIC" /
       "SDF - Fallback" — quy uoc dat ten cua TextMeshPro.
    3. Co bang glyph, HOAC Dynamic RONG (declared_char_count == 0,
       atlas chua nap glyph nao — vd NotoSansCJKkr-DemiLight SDF -
       DYNAMIC pid 1196 PB3, 828 B, 0 glyph).

    [DA DO THAT tren PB3] Quy tac cu (chi can bang glyph) nhan
    10.305 "TMP" gia trong sharedassets1.assets (URPAsset,
    DepthOfField, World1...) + 244 gia trong resources.assets — chi
    15 TMP that (14 ten SDF + 1 Fallback, tat ca co FaceInfo + ten
    SDF). Quy tac moi nhan dung 15/15, loai het gia. Gia tri that
    cua viec chat: link_fallback dung tap nay de chon donor — nhan
    gia nghia la chen PPtr toi MonoBehaviour la vao bang fallback,
    lam hong file game."""
    fi = dec.get("font_info") or {}
    name = dec.get("name") or ""
    if not fi or not fi.get("name"):
        return False
    if not (name.endswith("SDF") or name.endswith("SDF - DYNAMIC")
            or name.endswith("SDF - Fallback")):
        return False
    if dec.get("glyph_table"):
        return True
    if fi.get("declared_char_count") in (0, None) \
            and name.endswith("SDF - DYNAMIC"):
        return True
    return False


def _confirmed_tmp_ids(sf):
    """Tap path_id TMP_FontAsset da xac nhan trong 1 SerializedFile
    (decode duoc bang glyph, hoac Dynamic rong co ten SDF — xem
    _is_confirmed_tmp) + ten. Dung de phan biet PPtr fallback
    that voi PPtr toi script/material/texture."""
    ids = set()
    names = {}
    for f in _tf.find_tmp_font_assets(sf):
        try:
            raw = sf.object_bytes(f["path_id"])
            dec = _tf.decode_object(raw, sf.endian)
        except Exception:
            continue
        if _is_confirmed_tmp(dec):
            ids.add(f["path_id"])
            names[f["path_id"]] = dec.get("name") or ""
    return ids, names


def find_fallback_vector(raw, endian, confirmed_ids, min_offset=0):
    """Tim vector m_FallbackFontAssetTable trong object TMP (khong
    typetree) bang quet PPtr nghiem ngat [DA DO THAT tren PB3]:
    run lien tiep cac PPtr (file_id=0, path_id thuoc confirmed_ids),
    int32 count == len(run) ngay truoc run, int32 sau run trong
    [0, 64], run nam sau min_offset (sau bang glyph). Tra
    {"count_offset", "start", "end", "entries", "after", "after_offset"}
    hoac None. file_id=0 loai false positive PPtr toi script (PB3:
    file_id=1, path_id=1478).
    """
    hits = []
    for off in range(0, len(raw) - 12, 4):
        fid = _i32(raw, off, endian)
        ppid = _i64(raw, off + 4, endian)
        if fid == 0 and ppid in confirmed_ids:
            hits.append((off, fid, ppid))
    if not hits:
        return None
    runs = [[hits[0]]]
    for h in hits[1:]:
        if h[0] == runs[-1][-1][0] + 12:
            runs[-1].append(h)
        else:
            runs.append([h])
    for run in runs:
        start = run[0][0]
        end_off = run[-1][0] + 12
        if start < min_offset:
            continue
        if start < 4 or end_off + 4 > len(raw):
            continue
        if _i32(raw, start - 4, endian) != len(run):
            continue
        after = _i32(raw, end_off, endian)
        if not 0 <= after <= 64:
            continue
        return {"count_offset": start - 4, "start": start, "end": end_off,
                "entries": [p for _, _, p in run],
                "after": after, "after_offset": end_off}
    return None


def _explain_after_int(raw, endian, vec, object_ids, confirmed_ids):
    """Giai thich int32 ngay sau vector fallback: phai la count cua
    vector tiep theo (moi PPtr deu tro toi object co that) hoac count
    cua bang font-weight (moi cap 2 PPtr toi TMP). Tra ("empty" |
    "pptr_vector" | "weight_pairs") hoac None neu khong giai thich
    duoc -> ben goi tu choi (khong doan).
    """
    after = vec["after"]
    off = vec["after_offset"]
    if after == 0:
        return "empty"
    # Vector PPtr tiep theo: [after][after x 12B PPtr toi object co that].
    if off + 4 + 12 * after <= len(raw):
        ok = True
        for i in range(after):
            o = off + 4 + 12 * i
            if (_i32(raw, o, endian) != 0
                    or _i64(raw, o + 4, endian) not in object_ids):
                ok = False
                break
        if ok:
            return "pptr_vector"
    # Bang font-weight: [after][after cap x (2 PPtr toi TMP)].
    if off + 4 + 24 * after <= len(raw):
        ok = True
        for i in range(2 * after):
            o = off + 4 + 12 * i
            if (_i32(raw, o, endian) != 0
                    or _i64(raw, o + 4, endian) not in confirmed_ids):
                ok = False
                break
        if ok:
            return "weight_pairs"
    return None


def insert_fallback_entry(raw, endian, vec, donor_pid, object_ids,
                          confirmed_ids):
    """Chen 1 PPtr fallback (0, donor_pid) vao cuoi vector da tim.
    Tang count +1; KHONG dong vao int32 sau run (no la count cua vector
    tiep theo, khong doi khi them fallback — phai giai thich duoc no
    truoc, xem _explain_after_int). Tra bytes moi."""
    if donor_pid in vec["entries"]:
        raise _err("E_ALREADY_LINKED",
                   "donor %d da co trong vector fallback" % donor_pid)
    explained = _explain_after_int(raw, endian, vec, object_ids,
                                   confirmed_ids)
    if explained is None:
        raise _err("E_FALLBACK_AFTER",
                   "int32 sau vector fallback la %d — khong giai thich "
                   "duoc la count vector tiep theo hay bang font-weight; "
                   "khong chen khi chua biet bo cuc" % vec["after"])
    out = bytearray(raw)
    out[vec["count_offset"]:vec["count_offset"] + 4] = struct.pack(
        endian + "i", len(vec["entries"]) + 1)
    out[vec["end"]:vec["end"]] = _pptr_bytes(endian, 0, donor_pid)
    return bytes(out)


def _typetree_fallback_field(sf, path_id):
    """Tra node field m_FallbackFontAssetTable neu type co typetree
    va co field do, khong thi None."""
    try:
        obj = sf.get_object(path_id)
    except Exception:
        return None
    if obj.class_id != 114:
        return None
    typ = sf.types[obj.type_id] if obj.type_id < len(sf.types) else None
    node = getattr(typ, "node", None) if typ is not None else None
    if node is None:
        return None
    for c in node.children:
        if c.name == "m_FallbackFontAssetTable":
            return c
    return None


def link_fallback(sf_bytes, tmp_path_id, donor_tmp_path_id):
    """Them donor TMP vao bang fallback cua 1 TMP asset (F2-a).

    Duong typetree neu co (Kusa): chen phan tu PPtr qua
    write_monobehaviour_object_bytes. Khong thi duong byte (PB3):
    chen PPtr vao vector fallback da co cau truc (find_fallback_vector
    + insert_fallback_entry). Tra {"patched_sf_bytes", "method",
    "name", "donor", "entries_before/after"}. Da lien ket -> tra
    status "already_linked" (khong loi). Khong co vector / khong giai
    thich duoc after-int -> tu choi E_NO_FALLBACK_VECTOR /
    E_FALLBACK_AFTER.
    """
    if tmp_path_id == donor_tmp_path_id:
        raise _err("E_CONFIG_INVALID",
                   "khong tu lien ket fallback vao chinh minh")
    try:
        sf = _uni.parse_serialized(bytes(sf_bytes), where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "khong parse duoc SerializedFile: %s" % exc)
    confirmed, names = _confirmed_tmp_ids(sf)
    if donor_tmp_path_id not in confirmed:
        raise _err("E_NO_FALLBACK_ASSET",
                   "donor %d khong phai TMP_FontAsset da xac nhan trong "
                   "cung file" % donor_tmp_path_id)
    if tmp_path_id not in confirmed:
        raise _err("E_NO_FALLBACK_ASSET",
                   "asset dich %d khong phai TMP_FontAsset da xac nhan"
                   % tmp_path_id)
    raw = sf.object_bytes(tmp_path_id)
    name = names.get(tmp_path_id) or ("TMP #%d" % tmp_path_id)
    # Duong typetree (uu tien khi co).
    if _typetree_fallback_field(sf, tmp_path_id) is not None:
        try:
            val, _end = _uni.read_typetree_value(
                raw, sf.types[sf.get_object(tmp_path_id).type_id].node,
                0, sf.endian)
            cur = list(val.get("m_FallbackFontAssetTable") or [])
        except Exception as exc:  # noqa: BLE001
            raise _err("E_CORRUPT",
                       "khong doc duoc typetree TMP %s: %s" % (name, exc))
        cur_pids = [e.get("m_PathID") for e in cur
                    if isinstance(e, dict)]
        if donor_tmp_path_id in cur_pids:
            return {"patched_sf_bytes": bytes(sf_bytes), "method": "typetree",
                    "status": "already_linked", "name": name,
                    "donor": donor_tmp_path_id,
                    "entries_before": len(cur_pids),
                    "entries_after": len(cur_pids)}
        try:
            new_obj, _applied = _uni.write_monobehaviour_object_bytes(
                sf, tmp_path_id,
                [{"field_path": "m_FallbackFontAssetTable[+]",
                  "element": {"m_FileID": 0,
                              "m_PathID": donor_tmp_path_id}}])
        except Exception as exc:  # noqa: BLE001
            raise _err("E_VERIFY_FAILED",
                       "ghi typetree fallback that bai: %s" % exc)
        patched = _uni.build_serialized(sf, {tmp_path_id: new_obj})
        sf2 = _uni.parse_serialized(patched, where="font_writeback")
        val2, _e = _uni.read_typetree_value(
            sf2.object_bytes(tmp_path_id),
            sf2.types[sf2.get_object(tmp_path_id).type_id].node,
            0, sf2.endian)
        after_pids = [e.get("m_PathID") for e in
                      (val2.get("m_FallbackFontAssetTable") or [])
                      if isinstance(e, dict)]
        if donor_tmp_path_id not in after_pids:
            raise _err("E_VERIFY_FAILED",
                       "sau khi ghi typetree khong thay donor trong "
                       "fallback table")
        return {"patched_sf_bytes": patched, "method": "typetree",
                "status": "linked", "name": name,
                "donor": donor_tmp_path_id,
                "entries_before": len(cur_pids),
                "entries_after": len(after_pids)}
    # Duong byte (khong typetree).
    dec = _tf.decode_object(raw, sf.endian)
    gt = dec.get("glyph_table") or {}
    gt_end = (gt.get("offset") or 0) + 4 + (gt.get("stride") or 0) * (
        gt.get("count") or 0)
    vec = find_fallback_vector(raw, sf.endian, confirmed,
                               min_offset=gt_end)
    if vec is None:
        raise _err("E_NO_FALLBACK_VECTOR",
                   "TMP %s khong co vector fallback nhan dien duoc (trong "
                   "hoac khong co cau truc) — khong chen khi chua biet bo "
                   "cuc" % name)
    if donor_tmp_path_id in vec["entries"]:
        return {"patched_sf_bytes": bytes(sf_bytes), "method": "binary",
                "status": "already_linked", "name": name,
                "donor": donor_tmp_path_id,
                "entries_before": len(vec["entries"]),
                "entries_after": len(vec["entries"])}
    object_ids = set(o.path_id for o in sf.objects)
    try:
        new_obj = insert_fallback_entry(raw, sf.endian, vec,
                                        donor_tmp_path_id, object_ids,
                                        confirmed)
    except PB3Error:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED", "chen fallback that bai: %s" % exc)
    patched = _uni.build_serialized(sf, {tmp_path_id: new_obj})
    sf2 = _uni.parse_serialized(patched, where="font_writeback")
    vec2 = find_fallback_vector(sf2.object_bytes(tmp_path_id), sf2.endian,
                                confirmed, min_offset=gt_end)
    if vec2 is None or donor_tmp_path_id not in vec2["entries"]:
        raise _err("E_VERIFY_FAILED",
                   "sau khi ghi khong tim thay donor trong vector fallback")
    return {"patched_sf_bytes": patched, "method": "binary",
            "status": "linked", "name": name,
            "donor": donor_tmp_path_id,
            "entries_before": len(vec["entries"]),
            "entries_after": len(vec2["entries"])}


def find_tmp_settings(sf):
    """Tim object TMP Settings (MonoBehaviour ten "TMP Settings")."""
    for o in sf.objects:
        if o.class_id != 114:
            continue
        try:
            raw = sf.object_bytes(o.path_id)
            hdr, problem = _tf.read_monobehaviour_header(raw, sf.endian)
        except Exception:
            continue
        if problem is None and hdr is not None \
                and hdr.get("name") == "TMP Settings":
            return o.path_id
    return None


def link_global_fallback(sf_bytes, donor_tmp_path_id):
    """Them donor TMP vao danh sach fallback TOAN CUC cua TMP Settings
    (F2-a, khong can sua tung font asset).

    Hieu ung runtime [HYPOTHESIS]: TMP_Text tim chu thieu qua fallback
    table cua font asset roi toi danh sach fallback cua TMP Settings
    (tai lieu TextMeshPro: "Fallback Font Assets"). Chen co che giong
    link_fallback duong byte (vector phai co >= 2 muc + giai thich
    duoc int32 sau run) + kiem tra doc lai. Tra giong link_fallback
    (method "global_binary").
    """
    try:
        sf = _uni.parse_serialized(bytes(sf_bytes), where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "khong parse duoc SerializedFile: %s" % exc)
    confirmed, names = _confirmed_tmp_ids(sf)
    if donor_tmp_path_id not in confirmed:
        raise _err("E_NO_FALLBACK_ASSET",
                   "donor %d khong phai TMP_FontAsset da xac nhan trong "
                   "cung file" % donor_tmp_path_id)
    settings_pid = find_tmp_settings(sf)
    if settings_pid is None:
        raise _err("E_NO_TMP_SETTINGS",
                   "khong tim thay object TMP Settings trong file nay")
    raw = sf.object_bytes(settings_pid)
    vec = find_fallback_vector(raw, sf.endian, confirmed, min_offset=0)
    if vec is None or len(vec["entries"]) < 2:
        raise _err("E_NO_FALLBACK_VECTOR",
                   "TMP Settings khong co vector fallback nhan dien duoc "
                   "(can >= 2 muc de phan biet voi default font asset) — "
                   "khong chen khi chua biet bo cuc")
    if donor_tmp_path_id in vec["entries"]:
        return {"patched_sf_bytes": bytes(sf_bytes),
                "method": "global_binary", "status": "already_linked",
                "name": "TMP Settings", "donor": donor_tmp_path_id,
                "entries_before": len(vec["entries"]),
                "entries_after": len(vec["entries"])}
    object_ids = set(o.path_id for o in sf.objects)
    new_obj = insert_fallback_entry(raw, sf.endian, vec,
                                    donor_tmp_path_id, object_ids,
                                    confirmed)
    patched = _uni.build_serialized(sf, {settings_pid: new_obj})
    sf2 = _uni.parse_serialized(patched, where="font_writeback")
    vec2 = find_fallback_vector(sf2.object_bytes(settings_pid), sf2.endian,
                                confirmed, min_offset=0)
    if vec2 is None or donor_tmp_path_id not in vec2["entries"]:
        raise _err("E_VERIFY_FAILED",
                   "sau khi ghi khong tim thay donor trong fallback cua "
                   "TMP Settings")
    return {"patched_sf_bytes": patched, "method": "global_binary",
            "status": "linked", "name": "TMP Settings",
            "donor": donor_tmp_path_id,
            "entries_before": len(vec["entries"]),
            "entries_after": len(vec2["entries"])}


# ------------------------------------------------ (a) nhan ban asset fallback
def _norm_name(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _first_pptr_to_class(raw, endian, sf, class_id):
    """PPtr (file_id=0) toi object class_id co offset NHO NHAT trong
    raw. Tra (path_id, offset) hoac None. Dung cho m_Material (field
    dau trong TMP_FontAsset)."""
    want = {o.path_id for o in sf.objects if o.class_id == class_id}
    best = None
    for off in range(0, len(raw) - 12, 4):
        if _i32(raw, off, endian) != 0:
            continue
        pid = _i64(raw, off + 4, endian)
        if pid in want and (best is None or off < best[1]):
            best = (pid, off)
    return best


def clone_fallback_asset(sf_bytes, base_tmp_path_id, donor_font_bytes,
                         ress_bytes, missing_codepoints=None):
    """Nhan ban 1 TMP asset thanh asset fallback Viet (F2-a).

    sf_bytes: bytes SerializedFile. base_tmp_path_id: TMP goc.
    donor_font_bytes: font donor (Be Vietnam Pro, du 142/142).
    ress_bytes: bytes file .resS hien tai (atlas moi noi them vao
    cuoi). missing_codepoints: chu Viet thieu can dua vao fallback
    (mac dinh: toan bo 142 hoac phan thieu doc duoc tu asset cu).
    Tra {"patched_sf_bytes", "new_ress_bytes", "clone_tmp_pid",
    "clone_texture_pid", "clone_material_pid", "clone_name",
    "point_size", "atlas", "gate", "warnings", "added"}.
    - Clone giu NGUYEN kich thuoc atlas + padding cua asset goc (khong
      sua FaceInfo W/H); point size giam dan theo thang
      [goc, 90, 64, 48, 36, 24] cho toi khi 142 chu xep vua.
    - Clone giu APM cua asset goc (khong tim duoc vi tri APM tren
      layout PB3) — glyph da dien san trong bang nen van hien duoc
      qua fallback [HYPOTHESIS, can kiem chung tren game that].
    - m_SourceFontFile giu nguyen (chi dung luc tao atlas trong
      Editor); khong tao Font object BeVietnamPro trong game.
    Tu choi (E_*): khong atlas / khong material / donor thieu chu /
    khong xep vua o moi point size / kiem tra sau ghi FAIL.
    """
    donor_font_bytes = bytes(donor_font_bytes)
    ress_bytes = bytes(ress_bytes)
    try:
        sf = _uni.parse_serialized(bytes(sf_bytes), where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "khong parse duoc SerializedFile: %s" % exc)
    try:
        raw = sf.object_bytes(base_tmp_path_id)
        base_obj = sf.get_object(base_tmp_path_id)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_INPUT_NOT_FOUND",
                   "khong co TMP object %s: %s" % (base_tmp_path_id, exc))
    dec = _tf.decode_object(raw, sf.endian)
    if not dec.get("glyph_table"):
        raise _err("E_TMP_LAYOUT",
                   "asset %s khong decode duoc bang glyph"
                   % (dec.get("name") or base_tmp_path_id))
    base_name = dec.get("name") or ("TMP #%d" % base_tmp_path_id)
    layout = dec["glyph_table"]["layout"]
    gt = dec["glyph_table"]
    fi = dec.get("font_info") or {}
    base_pt = fi.get("point_size")
    if base_pt is None:
        raise _err("E_TMP_LAYOUT",
                   "asset %s thieu PointSize (FaceInfo)" % base_name)
    pad = fi.get("padding")
    if pad is None:
        pad = 5
    pad = int(pad)
    if missing_codepoints is None:
        vn = dec.get("vietnamese") or {}
        miss = vn.get("missing_codepoints")
        if miss:
            missing_codepoints = sorted(set(miss))
        else:
            missing_codepoints = list(VIETNAMESE_142)
    missing_codepoints = sorted(set(int(c) for c in missing_codepoints))
    if not missing_codepoints:
        raise _err("E_CONFIG_INVALID",
                   "asset %s khong thieu chu Viet nao — khong can fallback"
                   % base_name)
    # Donor phai co du chu thieu.
    try:
        donor_font = _sdf.parse_font(donor_font_bytes)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_CORRUPT", "font donor khong parse duoc: %s" % exc)
    donor_missing = [c for c in missing_codepoints
                     if donor_font.glyph_for(c) is None]
    if donor_missing:
        raise _err("E_DONOR_MISSING",
                   "font donor thieu %d chu can thiet (%s)"
                   % (len(donor_missing),
                      ", ".join("U+%04X" % c for c in donor_missing[:8])))
    # Atlas goc (giu nguyen kich thuoc).
    at = _ts.find_atlas_texture(sf, base_name)
    if at is None:
        raise _err("E_NO_ATLAS",
                   "asset %s khong co atlas Texture2D" % base_name)
    tex_pid, tinfo = at
    aw, ah = int(tinfo["width"]), int(tinfo["height"])
    st = tinfo.get("stream") or {}
    if not st:
        raise _err("E_NO_ATLAS",
                   "atlas %s khong stream (.resS)" % (base_name + " Atlas"))
    # Material goc (PPtr dau tien toi class 21 + ten chua family).
    mat_hit = _first_pptr_to_class(raw, sf.endian, sf, 21)
    if mat_hit is None:
        raise _err("E_NO_MATERIAL",
                   "asset %s khong co PPtr toi Material" % base_name)
    mat_pid, _mat_off = mat_hit
    try:
        mat_raw = sf.object_bytes(mat_pid)
        mat_name = _font._read_first_aligned_string(mat_raw, sf.endian)
    except Exception:
        mat_name = None
    fam = _norm_name(fi.get("family_name") or fi.get("name") or "")
    if not mat_name or (fam and fam not in _norm_name(mat_name)):
        raise _err("E_NO_MATERIAL",
                   "Material %s khong khop family %s cua asset %s"
                   % (mat_name, fi.get("family_name"), base_name))
    # Tai tao atlas fallback: thu point size giam dan.
    ladder = []
    for pt in FALLBACK_PT_LADDER:
        v = float(base_pt) if pt is None else float(pt)
        if v not in ladder:
            ladder.append(v)
    gen = None
    used_pt = None
    for pt in ladder:
        cand = _sdf.generate_tmp_atlas(donor_font_bytes,
                                       missing_codepoints, pt, pad,
                                       aw, ah, layout=layout)
        if not cand["unfitted"]:
            gen = cand
            used_pt = pt
            break
    if gen is None:
        raise _err("E_ATLAS_FULL",
                   "khong xep vua %d chu Viet vao atlas %dx%d o moi point "
                   "size %s — can atlas lon hon (chua ho tro doi W/H "
                   "FaceInfo/texture)" % (len(missing_codepoints), aw, ah,
                                          ladder))
    if gen["empty"]:
        raise _err("E_DONOR_MISSING",
                   "font donor khong co glyph cho %s"
                   % ", ".join("U+%04X" % c for c in gen["empty"][:8]))
    new_atlas = gen["atlas"]
    if layout == "new":
        new_table = _sdf.encode_glyph_table_new(gen["glyphs"])
    else:
        new_table = _sdf.encode_glyph_table_old(gen["glyphs"])
    new_char_table, _entries = build_char_table_bytes(
        [], gen["glyphs"], layout)
    new_count = len(gen["glyphs"])
    # Path id moi (lon nhat + 1, +2, +3).
    max_pid = max([o.path_id for o in sf.objects] or [0])
    clone_tmp_pid = max_pid + 1
    clone_tex_pid = max_pid + 2
    clone_mat_pid = max_pid + 3
    clone_name = base_name + " - Fallback"
    # 1. Texture2D moi: doi stream sang vung noi them o cuoi .resS.
    tex_raw = sf.object_bytes(tex_pid)
    tex_obj = sf.get_object(tex_pid)
    stream_off_pos = (st.get("offset_pos"))
    stream_size_pos = (st.get("size_pos"))
    if stream_off_pos is None or stream_size_pos is None:
        raise _err("E_NO_ATLAS",
                   "khong tim duoc vi tri offset/size cua stream trong "
                   "Texture2D %s" % (base_name + " Atlas"))
    new_ress_offset = len(ress_bytes)
    new_tex = bytearray(tex_raw)
    struct.pack_into(sf.endian + "Q", new_tex, stream_off_pos,
                     new_ress_offset)
    struct.pack_into(sf.endian + "I", new_tex, stream_size_pos,
                     len(new_atlas))
    new_tex = _replace_aligned_string_at(bytes(new_tex), 0,
                                         clone_name + " Atlas", sf.endian)
    # 2. Material moi: tro texture sang texture moi + doi ten.
    new_mat, tex_replaced = _replace_pptr_all(mat_raw, sf.endian,
                                              tex_pid, clone_tex_pid)
    if tex_replaced < 1:
        raise _err("E_CLONE_REF",
                   "Material %s khong tro toi atlas %d" % (mat_name, tex_pid))
    new_mat = _replace_aligned_string_at(new_mat, 0,
                                         clone_name + " Material", sf.endian)
    # 3. TMP moi: bang glyph + character moi, FaceInfo (count + pt +
    # family), ten MB, PPtr texture/material moi.
    char_loc = locate_char_table(raw, sf.endian, gt)
    new_tmp = splice_tmp_object(raw, sf.endian, new_table, new_char_table,
                                new_count, gt, char_loc,
                                fi.get("count_offset"))
    dec3 = _tf.decode_object(new_tmp, sf.endian)
    fi3 = dec3.get("font_info") or {}
    if fi3.get("point_size_offset") is not None:
        poff = fi3["point_size_offset"]
        if (fi3.get("format") or "old") == "new":
            new_tmp = (new_tmp[:poff]
                       + struct.pack(sf.endian + "i", int(round(used_pt)))
                       + new_tmp[poff + 4:])
        else:
            new_tmp = (new_tmp[:poff]
                       + struct.pack(sf.endian + "f", float(used_pt))
                       + new_tmp[poff + 4:])
    if fi3.get("offset") is not None:
        new_tmp = _replace_aligned_string_at(
            new_tmp, fi3["offset"], "Be Vietnam Pro", sf.endian)
    new_tmp = replace_mb_name(new_tmp, sf.endian, clone_name)
    new_tmp, n_tex = _replace_pptr_all(new_tmp, sf.endian,
                                       tex_pid, clone_tex_pid)
    if n_tex < 1:
        raise _err("E_CLONE_REF",
                   "TMP clone khong tro toi atlas %d" % tex_pid)
    new_tmp, n_mat = _replace_pptr_all(new_tmp, sf.endian,
                                       mat_pid, clone_mat_pid)
    if n_mat < 1:
        raise _err("E_CLONE_REF",
                   "TMP clone khong tro toi material %d" % mat_pid)
    # 4. Them 3 object moi vao SerializedFile + dung lai.
    def _clone_entry(donor_obj, new_pid):
        return _uni.SerializedObject(
            path_id=new_pid, byte_start=0, byte_size=0,
            type_id=donor_obj.type_id, class_id=donor_obj.class_id,
            script_type_index=donor_obj.script_type_index,
            is_stripped=donor_obj.is_stripped)
    sf.objects.append(_clone_entry(base_obj, clone_tmp_pid))
    sf.objects.append(_clone_entry(tex_obj, clone_tex_pid))
    sf.objects.append(_clone_entry(sf.get_object(mat_pid), clone_mat_pid))
    sf._by_path_id = None
    patched = _uni.build_serialized(
        sf, {clone_tmp_pid: new_tmp, clone_tex_pid: bytes(new_tex),
             clone_mat_pid: new_mat})
    new_ress = ress_bytes + new_atlas
    # 5. Kiem tra lai toan bo.
    try:
        sf2 = _uni.parse_serialized(patched, where="font_writeback")
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "file da sua khong parse lai duoc: %s" % exc)
    if sf2.object_bytes(base_tmp_path_id) != raw:
        raise _err("E_VERIFY_FAILED",
                   "asset goc %s bi doi sau khi ghi (phai giu nguyen)"
                   % base_name)
    try:
        dec2 = _tf.decode_object(sf2.object_bytes(clone_tmp_pid),
                                 sf2.endian)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "TMP clone khong decode duoc: %s" % exc)
    gt2 = dec2.get("glyph_table") or {}
    if gt2.get("count") != new_count or gt2.get("layout") != layout:
        raise _err("E_VERIFY_FAILED",
                   "TMP clone co bang glyph %s (ky vong %d, %s)"
                   % (gt2.get("count"), new_count, layout))
    try:
        char2 = locate_char_table(sf2.object_bytes(clone_tmp_pid),
                                  sf2.endian, gt2)
    except Exception as exc:  # noqa: BLE001
        raise _err("E_VERIFY_FAILED",
                   "bang character cua TMP clone khong tim thay: %s" % exc)
    if char2["count"] != new_count:
        raise _err("E_VERIFY_FAILED",
                   "bang character clone co %d record, ky vong %d"
                   % (char2["count"], new_count))
    tex2 = _ts.parse_texture2d(sf2.object_bytes(clone_tex_pid), sf2.endian)
    if tex2 is None or tex2.get("name") != clone_name + " Atlas":
        raise _err("E_VERIFY_FAILED",
                   "Texture2D clone khong parse lai duoc / sai ten")
    st2 = tex2.get("stream") or {}
    if st2.get("offset") != new_ress_offset \
            or st2.get("size") != len(new_atlas):
        raise _err("E_VERIFY_FAILED",
                   "stream cua Texture2D clone sai (%s)" % (st2,))
    mat2 = sf2.object_bytes(clone_mat_pid)
    if len(_find_pptr_occurrences(mat2, sf2.endian, clone_tex_pid)) < 1:
        raise _err("E_VERIFY_FAILED",
                   "Material clone khong tro toi texture moi")
    if _find_pptr_occurrences(mat2, sf2.endian, tex_pid):
        raise _err("E_VERIFY_FAILED",
                   "Material clone van tro toi atlas cu")
    gate_res = _gate.check_tmp_sdf(new_table, layout, new_atlas, aw, ah,
                                   expected_count=new_count)
    if gate_res.get("status") != "pass":
        raise _err("E_VERIFY_FAILED",
                   "gate.check_tmp_sdf FAIL cho clone: %s"
                   % (gate_res.get("summary") or gate_res))
    warnings = [
        "Clone giu APM cua asset goc (khong tim duoc vi tri APM tren "
        "layout PB3) — glyph Viet da dien san trong bang nen van hien "
        "duoc qua fallback [HYPOTHESIS, can kiem chung tren game that].",
        "m_SourceFontFile cua clone giu nguyen (chi dung luc tao atlas "
        "trong Editor); khong tao Font object BeVietnamPro trong game.",
    ]
    if aw > FALLBACK_ATLAS_MAX or ah > FALLBACK_ATLAS_MAX:
        warnings.append("Atlas goc %dx%d lon hon %d — clone giu nguyen "
                        "kich thuoc, .resS tang %d byte."
                        % (aw, ah, FALLBACK_ATLAS_MAX, len(new_atlas)))
    if used_pt != float(base_pt):
        warnings.append("Point size cua clone giam %.0f -> %.0f de xep vua "
                        "%d chu (TMP tu scale theo point size — [HYPOTHESIS, "
                        "can kiem chung tren game that])."
                        % (float(base_pt), used_pt, len(missing_codepoints)))
    return {"patched_sf_bytes": patched, "new_ress_bytes": new_ress,
            "clone_tmp_pid": clone_tmp_pid,
            "clone_texture_pid": clone_tex_pid,
            "clone_material_pid": clone_mat_pid,
            "clone_name": clone_name, "base_name": base_name,
            "layout": layout, "point_size": used_pt,
            "base_point_size": float(base_pt), "padding": pad,
            "atlas": "%dx%d" % (aw, ah),
            "added": sorted(missing_codepoints),
            "ress_append_offset": new_ress_offset,
            "gate": {"status": gate_res.get("status"),
                     "summary": gate_res.get("summary")},
            "warnings": warnings}
