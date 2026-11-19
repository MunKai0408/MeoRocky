"""Đọc tham số TMP_FontAsset (TextMeshPro) từ SerializedFile — mục 7.

Port logic từ IPA Localization Studio (js/modules/tmp_font.js, MIT, tác
gia Joseph; xem vendor/ipa-localization-studio/) sang Python, đối chiếu
với sdk/font.py (bộ 134 chữ cái tiếng Việt + 8 dấu kết hợp = 142 mục).

RANH GIỚI (đọc kỹ trước khi dùng):
- Đây CHỈ ĐỌC: neo FaceInfo (tên font, PointSize, Scale, CharacterCount,
  LineHeight, Padding, kích thước atlas) + bảng glyph 2 bố cục (36
  byte/phần tử cũ: id=codepoint; 52 byte/phần tử mới TMP 3.x: id=glyph
  index, rect y tính từ ĐÁY atlas) + tham chiếu PPtr tới
  Texture2D/Material/Font/TMP_FontAsset khác. KHÔNG giải mã ảnh atlas
  (Texture2D), KHÔNG đọc giá trị shader SDF trong material (vd
  _FaceDilate, độ rộng outline) — các giá trị đó chỉ đọc được trong
  Unity Editor (xem site/unity_editor/TMP_VietnameseAtlas.cs).
- Bố cục TMP_Glyph 36 byte + FaceInfo 20 trường (Unity 5.6) đã xác nhận
  trên DATA WING thật (3/7 asset, theo studio); bản 18 trường là dự
  phòng. Bố cục 52 byte đã xác nhận trên PB3 LiberationSans SDF thật
  (250 glyph). 9 Kings dùng Unity 6 + TMP mới hơn — bố cục CÓ THỂ khác:
  khi không khớp, hàm trả `glyph_table_not_found` kèm chẩn đoán (hex head
  + count_hits) chứ KHÔNG bịa danh sách ký tự.
- Atlas SDF tái tạo ĐƯỢC bằng Python thuần: xem sdk/sdf.py (đo thật trên
  atlas Aniara Light: MAE trung vị 1.34, xem exp/verify_sdf_regen.py).
  Layout mới lưu glyph INDEX thay vì codepoint nên độ phủ tiếng Việt
  cần cmap của font nguồn (sdk/sdf.py) mới tính được.
- Chỉ stdlib, tương thích Python 3.9 (iSH Alpine 3.14). Không COI, không
  SharedArrayBuffer.
"""
from __future__ import annotations

import math
import os
import struct
import zipfile
from typing import Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk import font as font_sdk

#: Buoc nhay bang glyph: id:int32 + 8 float32 = 36 byte (xac nhan DATA WING).
STRIDE = 36
#: Buoc nhay bang glyph MOI (TMP 3.x, Unity 2022.3+): id:uint32 (glyph index)
#: + 5 float32 metrics + rect 4 int32 (x, y tu DAY atlas, w, h) + scale
#: float32 + atlasIndex int32 + trailing int32 = 52 byte (xac nhan PB3
#: LiberationSans SDF, 250 glyph, 191/250 record khop nghiem ngat).
STRIDE_NEW = 52
MAX_ASSETS = 200
MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_GLYPHS = 70000
MAX_RANGES = 5000
MAX_LISTED_MISSING = 150
HEX_HEAD_BYTES = 192
MAX_REFS = 8
MAX_DIAG_HITS = 6
#: Entry trong ZIP/IPA lon hon nguong nay thi bo qua khi quet (bao ve RAM iPhone).
SCAN_MAX_ENTRY_BYTES = 32 * 1024 * 1024

MONO_BEHAVIOUR_CLASS_ID = 114
MONO_SCRIPT_CLASS_ID = 115
TEXTURE_CLASS_ID = 28
MATERIAL_CLASS_ID = 21
FONT_CLASS_ID = 128

SCHEMA = "tmp_fonts/1"
NOT_DONE = [
    "giải mã ảnh atlas (Texture2D) và kiểm glyph có hình",
    "liên kết TextMeshPro component -> TMP_FontAsset đang dùng thật",
    "bảng kerning, font weights, giá trị shader SDF trong material "
    "(bang fallback font m_FallbackFontAssetTable DOC DUOC qua typetree "
    "— xem sdk/font_check.read_tmp_asset_typetree)",
    "tạo TMP_FontAsset fallback trên web (mục C6 làm phần kế hoạch + script "
    "Editor; atlas SDF của fallback sinh bằng sdk/sdf.py)",
]


# ------------------------------------------------------------------ bo chu
def vietnamese_letters_134() -> List[int]:
    """134 chữ cái tiếng Việt (không tính 8 dấu kết hợp).

    Lấy từ bộ chuẩn của sdk/font.py (REQUIRED_VIETNAMESE trừ 8 dấu) nên
    luôn khớp với font scan 142/142; kiểm chứng bằng test: bằng đúng bộ
    của studio (12 gốc x 5 dấu, NFC) trong tests/test_tmp_font.py.
    """
    marks = set(font_sdk._VI_MARKS)  # noqa: SLF001 - cung goi sdk
    return sorted(set(font_sdk.REQUIRED_VIETNAMESE) - marks)


def combining_marks_8() -> List[int]:
    """8 dấu kết hợp tiếng Việt (huyền/sắc/mũ/ngã/breve/móc/dấu sừng/chấm dưới)."""
    return sorted(font_sdk._VI_MARKS)  # noqa: SLF001 - cung goi sdk


def vietnamese_chars_text(codepoints=None) -> str:
    """Chuỗi ký tự (NFC) cho ô "Custom Characters" của TMP Font Asset Creator.

    Mặc định: cả 134 chữ cái + 8 dấu kết hợp. Truyền danh sách riêng để
    chỉ lấy chữ thiếu.
    """
    cps = vietnamese_letters_134() + combining_marks_8() if codepoints is None \
        else sorted(codepoints)
    return "".join(chr(cp) for cp in cps)


def _hex(cp: int) -> str:
    return "U+%04X" % cp


# ------------------------------------------------------------------ doc so
def _align4(n: int) -> int:
    return (n + 3) & ~3


def _read_aligned_string(data: bytes, off: int, endian: str,
                         max_len: int = 512):
    """Chuỗi Unity (i32 độ dài + bytes + đệm 4). Trả (str, offset_tiếp) hoặc None."""
    if off < 0 or off + 4 > len(data):
        return None
    (n,) = struct.unpack_from(endian + "i", data, off)
    if n < 0 or n > max_len or off + 4 + n > len(data):
        return None
    raw = data[off + 4:off + 4 + n]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text, _align4(off + 4 + n)


def _printable_ascii(data: bytes, off: int, n: int) -> bool:
    for i in range(n):
        c = data[off + i]
        if c < 0x20 or c > 0x7E:
            return False
    return True


def _is_pow2(v: float) -> bool:
    return (isinstance(v, float) and math.isfinite(v) and 32 <= v <= 16384
            and float(int(v)) == v and (int(v) & (int(v) - 1)) == 0)


def _pid64(data: bytes, off: int, endian: str):
    """PathID 64-bit (theo endian). Trả int hoặc None nếu lạ."""
    if off < 0 or off + 8 > len(data):
        return None
    if endian == "<":
        lo, hi = struct.unpack_from("<Ii", data, off)
    else:
        hi, lo = struct.unpack_from(">iI", data, off)
    if hi < 0 or hi > 0x1FFFFF:
        return None
    return hi * 4294967296 + lo


def _f32(data: bytes, off: int, endian: str) -> float:
    return struct.unpack_from(endian + "f", data, off)[0]


def _i32(data: bytes, off: int, endian: str) -> int:
    return struct.unpack_from(endian + "i", data, off)[0]


def _u32(data: bytes, off: int, endian: str) -> int:
    return struct.unpack_from(endian + "I", data, off)[0]


def read_monobehaviour_header(data: bytes, endian: str):
    """Header MonoBehaviour: m_GameObject, m_Enabled, m_Script, m_Name.

    Trả dict hoặc None (kèm lý do trong `problem` khi truyền list vào).
    """
    if len(data) < 32:
        return None, "Object quá ngắn (%d byte) cho header MonoBehaviour" % len(data)
    go_file = _i32(data, 0, endian)
    go_pid = _pid64(data, 4, endian)
    sc_file = _i32(data, 16, endian)
    sc_pid = _pid64(data, 20, endian)
    nm = _read_aligned_string(data, 28, endian, 512)
    if (go_pid is None or sc_pid is None or not nm or sc_pid == 0
            or sc_file < 0 or sc_file > 4096 or go_file < 0 or go_file > 4096):
        return None, ("Header MonoBehaviour không khớp bố cục giả định "
                      "(m_GameObject, m_Enabled, m_Script, m_Name) — quét từ đầu object")
    return {
        "game_object": {"file_id": go_file, "path_id": go_pid},
        "enabled": data[12],
        "script": {"file_id": sc_file, "path_id": sc_pid},
        "name": nm[0],
        "data_start": nm[1],
    }, None


# ------------------------------------------------------------------ FaceInfo
#: Trường FaceInfo tính từ offset PointSize (e), bố cục 20 trường (Unity 5.6,
#: xác nhận DATA WING) và bản 18 trường (không có 2 trường strikethrough).
_FACEINFO_FIELDS_20 = [
    ("point_size", 0), ("scale", 1), ("line_height", 3), ("baseline", 4),
    ("ascender", 5), ("cap_height", 6), ("descender", 7), ("center_line", 8),
    ("superscript_offset", 9), ("subscript_offset", 10), ("sub_size", 11),
    ("underline", 12), ("underline_thickness", 13), ("strikethrough", 14),
    ("strikethrough_thickness", 15), ("tab_width", 16), ("padding", 17),
    ("atlas_width", 18), ("atlas_height", 19),
]
_FACEINFO_FIELDS_18 = [
    ("point_size", 0), ("scale", 1), ("line_height", 3), ("baseline", 4),
    ("ascender", 5), ("cap_height", 6), ("descender", 7), ("center_line", 8),
    ("superscript_offset", 9), ("subscript_offset", 10), ("sub_size", 11),
    ("underline", 12), ("underline_thickness", 13), ("tab_width", 14),
    ("padding", 15), ("atlas_width", 16), ("atlas_height", 17),
]
#: Chỉ số slot (từ e) của các trường độc lập bố cục (tới underline_thickness).
_FACEINFO_COMMON = _FACEINFO_FIELDS_20[:13]


def find_face_info(data: bytes, start: int, endian: str):
    """Neo FaceInfo: chuỗi tên + PointSize/Scale/CharacterCount hợp lý.

    Trả dict đầy đủ các trường (strikethrough/tab_width/padding có thể
    None nếu bố cục quét) hoặc None.
    """
    n = len(data)
    p = _align4(start)
    while p + 8 <= n:
        length = _i32(data, p, endian)
        if 1 <= length <= 120 and p + 4 + length <= n \
                and _printable_ascii(data, p + 4, length):
            e = _align4(p + 4 + length)
            if e + 4 * 20 <= n:
                pt = _f32(data, e, endian)
                sc = _f32(data, e + 4, endian)
                cc = _i32(data, e + 8, endian)
                lh = _f32(data, e + 12, endian)
                if (1 <= pt <= 4096 and 0 < sc <= 64
                        and 0 <= cc <= MAX_GLYPHS and 0 < lh <= 20000):
                    name = data[p + 4:p + 4 + length].decode("latin-1")
                    info = {
                        "offset": p, "name": name, "point_size": round(pt, 2),
                        "scale": round(sc, 3), "declared_char_count": cc,
                        "line_height": round(lh, 2), "atlas_width": None,
                        "atlas_height": None, "atlas_basis": None,
                    }
                    for key, slot in _FACEINFO_COMMON:
                        info[key] = _f32(data, e + 4 * slot, endian)
                    w20 = _f32(data, e + 4 * 18, endian)
                    h20 = _f32(data, e + 4 * 19, endian)
                    w18 = _f32(data, e + 4 * 16, endian)
                    h18 = _f32(data, e + 4 * 17, endian)
                    if _is_pow2(w20) and _is_pow2(h20):
                        info["atlas_width"] = w20
                        info["atlas_height"] = h20
                        info["atlas_basis"] = "faceinfo_layout"
                        for key, slot in _FACEINFO_FIELDS_20[13:]:
                            info[key] = _f32(data, e + 4 * slot, endian)
                    elif _is_pow2(w18) and _is_pow2(h18):
                        info["atlas_width"] = w18
                        info["atlas_height"] = h18
                        info["atlas_basis"] = "faceinfo_layout_18"
                        for key, slot in _FACEINFO_FIELDS_18[13:]:
                            info[key] = _f32(data, e + 4 * slot, endian)
                        info["strikethrough"] = None
                        info["strikethrough_thickness"] = None
                    else:
                        for k in range(3, 27):
                            if e + 4 * (k + 2) > n:
                                break
                            a = _f32(data, e + 4 * k, endian)
                            b = _f32(data, e + 4 * (k + 1), endian)
                            if (_is_pow2(a) and _is_pow2(b) and a >= 64 and b >= 64
                                    and max(a, b) / min(a, b) <= 4):
                                info["atlas_width"] = a
                                info["atlas_height"] = b
                                info["atlas_basis"] = "scan"
                                break
                        info["strikethrough"] = None
                        info["strikethrough_thickness"] = None
                        info["tab_width"] = None
                        info["padding"] = None
                    return info
        p += 4
    return None


def find_face_info_new(data: bytes, start: int, endian: str):
    """Neo FaceInfo MOI (TMP 3.x): 2 chuoi (familyName + styleName) +
    pointSize INT + scale float + characterCount int + lineHeight float.

    Bo cuc moi khac cu: cu chi co 1 chuoi roi pointSize FLOAT; moi co 2
    chuoi roi pointSize INT. Chi doc cac truong chac chan (family/style/
    pointSize/scale/characterCount/lineHeight/ascender/capHeight);
    padding/atlas KHONG nam trong FaceInfo moi (do tren LiberationSans
    + NotoSans: 15 float sau characterCount, khong co 9.0/1024) — tra
    None, de nguoi goi lay tu ban sao cu hoac Texture2D. Tra dict nhu
    find_face_info (them family_name/style_name/format="new") hoac None.
    """
    n = len(data)
    p = _align4(start)
    while p + 8 <= n:
        length = _i32(data, p, endian)
        if 1 <= length <= 60 and p + 4 + length <= n \
                and _printable_ascii(data, p + 4, length):
            e = _align4(p + 4 + length)
            if e + 8 <= n:
                length2 = _i32(data, e, endian)
                if 1 <= length2 <= 40 and e + 4 + length2 <= n \
                        and _printable_ascii(data, e + 4, length2):
                    f = _align4(e + 4 + length2)
                    if f + 4 * 6 <= n:
                        pt = _i32(data, f, endian)
                        sc = _f32(data, f + 4, endian)
                        cc = _i32(data, f + 8, endian)
                        lh = _f32(data, f + 12, endian)
                        if (10 <= pt <= 500 and 0 < sc <= 64
                                and 0 <= cc <= MAX_GLYPHS
                                and 0 < lh <= 20000
                                and pt * 0.5 < lh < pt * 3.0):
                            fam = data[p + 4:p + 4 + length].decode("latin-1")
                            sty = data[e + 4:e + 4 + length2].decode("latin-1")
                            info = {
                                "offset": p, "name": fam,
                                "family_name": fam, "style_name": sty,
                                "point_size": float(pt),
                                "scale": round(sc, 3),
                                "declared_char_count": cc,
                                "line_height": round(lh, 2),
                                "atlas_width": None, "atlas_height": None,
                                "atlas_basis": None, "format": "new",
                            }
                            info["ascender"] = _f32(data, f + 16, endian)
                            info["cap_height"] = _f32(data, f + 20, endian)
                            for key in ("baseline", "descender", "center_line",
                                        "superscript_offset",
                                        "subscript_offset", "sub_size",
                                        "underline", "underline_thickness",
                                        "strikethrough",
                                        "strikethrough_thickness",
                                        "tab_width", "padding"):
                                info[key] = None
                            return info
        p += 4
    return None


# ------------------------------------------------------------------ bang glyph
def _is_invisible_id(cp: int) -> bool:    return (cp <= 0x20 or (0x7F <= cp <= 0xA0) or cp == 0xAD or cp == 0x1680
            or (0x2000 <= cp <= 0x200F) or (0x2028 <= cp <= 0x202F)
            or (0x205F <= cp <= 0x206F) or cp == 0x3000 or cp == 0xFEFF)


def _glyph_kind(data: bytes, off: int, endian: str, aw, ah) -> int:
    """0 = không hợp lệ; 1 = hợp lệ (trong atlas); 2 = glyph vô hình ngoài atlas."""
    cp = _i32(data, off, endian)
    if cp < 0 or cp > 0x10FFFF:
        return 0
    f = []
    for k in range(8):
        v = _f32(data, off + 4 + 4 * k, endian)
        if not math.isfinite(v) or abs(v) > 100000:
            return 0
        f.append(v)
    x, y, w, h, sc = f[0], f[1], f[2], f[3], f[7]
    if w < 0 or h < 0 or x < -0.5 or y < -0.5 or not (0 < sc <= 1000):
        return 0
    if w == 0 and h == 0:
        # Glyph rong (placeholder, vd 'y' cua Aniara_Graphics): chap nhan
        # du x/y co nam ngoai atlas.
        return 2
    if aw is not None and (x + w > aw + 1 or y + h > ah + 1):
        return 2 if _is_invisible_id(cp) else 0
    return 1


def _glyph_kind_new(data: bytes, off: int, endian: str, aw, ah) -> int:
    """0 = khong hop le; 1 = hop le (trong atlas); 2 = placeholder rong.

    Bo cuc MOI 52 byte: id uint32 = glyph index; metrics 5 float32
    (width, height, bearingX, bearingY, advance); rect 4 int32 (x, y tu
    DAY atlas, w, h); scale float32; atlasIndex int32; trailing int32.
    """
    gid = _u32(data, off, endian) if off + 4 <= len(data) else 0
    if gid > MAX_GLYPHS:
        return 0
    f = []
    for k in range(5):
        v = _f32(data, off + 4 + 4 * k, endian)
        if not math.isfinite(v) or abs(v) > 100000:
            return 0
        f.append(v)
    w, h, bx, by, adv = f
    if w < 0 or h < 0 or adv < 0 or bx < -1000 or by < -1000:
        return 0
    rx = _i32(data, off + 24, endian)
    ry = _i32(data, off + 28, endian)
    rw = _i32(data, off + 32, endian)
    rh = _i32(data, off + 36, endian)
    sc = _f32(data, off + 40, endian)
    ai = _i32(data, off + 44, endian)
    tr = _i32(data, off + 48, endian)
    if not (0 < sc <= 1000) or ai != 0 or tr != 0:
        return 0
    if rw < 0 or rh < 0 or rx < 0 or ry < 0:
        return 0
    if w == 0 and h == 0 and rw == 0 and rh == 0:
        return 2
    if aw is not None and (rx + rw > aw + 1 or ry + rh > ah + 1):
        return 0
    # Rect phai khop metrics (lam tron): w/h rect ~ metrics w/h.
    if abs(rw - round(w)) > 2 or abs(rh - round(h)) > 2:
        return 0
    return 1


def _table_check(data: bytes, p: int, n: int, endian: str, aw, ah,
                 stride=STRIDE, kind=None) -> dict:
    """Chẩn đoán: vì sao bảng N phần tử tại p bị từ chối."""
    kind = kind or _glyph_kind
    out = {"records": n, "fail_count": 0, "placeholder_glyphs": 0,
           "first_fail": None, "stride": stride}
    for i in range(n):
        o = p + 4 + stride * i
        k = kind(data, o, endian, aw, ah)
        if k == 2:
            out["placeholder_glyphs"] += 1
        if k != 0:
            continue
        out["fail_count"] += 1
        if out["first_fail"] is None:
            if stride == STRIDE_NEW:
                gid = _u32(data, o, endian)
                f = [_f32(data, o + 4 + 4 * j, endian) for j in range(5)]
                r = [_i32(data, o + 24 + 4 * j, endian) for j in range(4)]
                sc = _f32(data, o + 40, endian)
                finite = all(math.isfinite(v) and abs(v) <= 100000
                             for v in f) and math.isfinite(sc)
                if gid > MAX_GLYPHS:
                    reason = "id_not_glyph_index"
                elif not finite:
                    reason = "non_finite_or_huge"
                elif r[2] < 0 or r[3] < 0 or r[0] < 0 or r[1] < 0:
                    reason = "negative_rect"
                elif not (0 < sc <= 1000):
                    reason = "bad_scale"
                else:
                    reason = "outside_atlas_or_rect_mismatch"
                out["first_fail"] = {
                    "index": i, "id": gid, "reason": reason,
                    "metrics": f if finite else None,
                    "rect": r, "scale": sc if finite else None,
                }
                continue
            cp = _i32(data, o, endian)
            f = [_f32(data, o + 4 + 4 * j, endian) for j in range(8)]
            finite = all(math.isfinite(v) and abs(v) <= 100000 for v in f)
            if cp < 0 or cp > 0x10FFFF:
                reason = "id_not_code_point"
            elif not finite:
                reason = "non_finite_or_huge"
            elif (f[2] < 0 or f[3] < 0 or f[0] < -0.5 or f[1] < -0.5
                    or not (f[7] > 0 and f[7] <= 1000)):
                reason = "negative_or_bad_scale"
            else:
                reason = "outside_atlas"
            out["first_fail"] = {
                "index": i, "id": cp, "reason": reason,
                "x": f[0] if finite else None, "y": f[1] if finite else None,
                "width": f[2] if finite else None,
                "height": f[3] if finite else None,
                "scale": f[7] if finite else None,
            }
    return out


def _scan_table_stride(data: bytes, start: int, endian: str, aw, ah,
                       stride, kind):
    """Quet ung vien bang glyph voi 1 stride: tra list candidate."""
    n = len(data)
    cands = []
    p = _align4(start)
    while p + 4 + stride <= n:
        count = _i32(data, p, endian)
        if 1 <= count <= MAX_GLYPHS and p + 4 + stride * count <= n:
            ph = 0
            ok = True
            for i in range(count):
                k = kind(data, p + 4 + stride * i, endian, aw, ah)
                if k == 0:
                    ok = False
                    break
                if k == 2:
                    ph += 1
            if ok and ph <= max(8, count // 10):
                ids = set()
                dup = 0
                for i in range(count):
                    if stride == STRIDE_NEW:
                        cp = _u32(data, p + 4 + stride * i, endian)
                    else:
                        cp = _i32(data, p + 4 + stride * i, endian)
                    if cp in ids:
                        dup += 1
                    else:
                        ids.add(cp)
                if dup <= max(0, count // 100):
                    cands.append({"offset": p, "count": count, "stride": stride,
                                  "duplicate_ids": dup,
                                  "placeholder_glyphs": ph})
        p += 4
    return cands


def find_glyph_table(data: bytes, start: int, endian: str, aw, ah,
                     declared: int):
    """Tìm bảng glyph: mọi ứng viên TOÀN BỘ phần tử hợp lệ (ca 2 bo cuc).

    Thu ca stride 36 (cu, id=codepoint) va 52 (moi, id=glyph index).
    Chọn bảng có số phần tử = CharacterCount khai báo (nếu có); không thì
    bảng LỚN NHẤT. Hoa: cung count thi uu tien stride 52 (rang buoc chat
    hon: trailing/atlasIndex = 0, rect khop metrics). Trả None nếu không
    có ứng viên.
    """
    cands = (_scan_table_stride(data, start, endian, aw, ah, STRIDE,
                                _glyph_kind)
             + _scan_table_stride(data, start, endian, aw, ah, STRIDE_NEW,
                                  _glyph_kind_new))
    if not cands:
        return None
    pick = None
    if declared > 0:
        same = [c for c in cands if c["count"] == declared]
        if same:
            pick = max(same, key=lambda c: c["stride"])
    if pick is None:
        pick = max(cands, key=lambda c: (c["count"], c["stride"]))
    pick["others"] = [{"offset": c["offset"], "count": c["count"],
                       "stride": c["stride"]}
                      for c in cands if c is not pick][:4]
    return pick


def read_glyph_records(data: bytes, offset: int, count: int,
                       stride: int, endian: str) -> List[dict]:
    """Doc TOAN BO record cua 1 bang glyph (da duoc find_glyph_table tim).

    offset: vi tri so luong (int32 count); record bat dau o offset + 4.
    stride 36 (layout cu): id = codepoint (int32) + 8 float
    (x, y, width, height, x_offset, y_offset, x_advance, scale).
    stride 52 (layout moi): id = glyph index (uint32) + 5 float
    (width, height, x_offset, y_offset, x_advance) + rect 4 int
    (x, y-from-BOTTOM, w, h) + scale float + atlasIndex int + trailing int.
    """
    out = []
    for i in range(count):
        o = offset + 4 + stride * i
        if stride == STRIDE_NEW:
            out.append({
                "id": _u32(data, o, endian),
                "width": _f32(data, o + 4, endian),
                "height": _f32(data, o + 8, endian),
                "x_offset": _f32(data, o + 12, endian),
                "y_offset": _f32(data, o + 16, endian),
                "x_advance": _f32(data, o + 20, endian),
                "rect": [_i32(data, o + 24 + 4 * j, endian)
                         for j in range(4)],
                "scale": _f32(data, o + 40, endian),
                "atlas_index": _i32(data, o + 44, endian),
                "trailing": _i32(data, o + 48, endian),
            })
        else:
            out.append({
                "id": _i32(data, o, endian),
                "x": _f32(data, o + 4, endian),
                "y": _f32(data, o + 8, endian),
                "width": _f32(data, o + 12, endian),
                "height": _f32(data, o + 16, endian),
                "x_offset": _f32(data, o + 20, endian),
                "y_offset": _f32(data, o + 24, endian),
                "x_advance": _f32(data, o + 28, endian),
                "scale": _f32(data, o + 32, endian),
            })
    return out


def _ranges(cps) -> List[List[int]]:
    a = sorted(cps)
    out = []
    s = e = None
    for c in a:
        if s is None:
            s = e = c
        elif c == e + 1:
            e = c
        elif c > e:
            out.append([s, e])
            s = e = c
    if s is not None:
        out.append([s, e])
    return out


def vietnamese_coverage(codepoints) -> dict:
    """Độ phủ tiếng Việt của tập code point trong atlas."""
    req = vietnamese_letters_134()
    have = set(codepoints)
    miss = [c for c in req if c not in have]
    cov = len(req) - len(miss)
    ascii_letters = list(range(65, 91)) + list(range(97, 123))
    amiss = [c for c in ascii_letters if c not in have]
    marks = combining_marks_8()
    mmiss = [c for c in marks if c not in have]
    return {
        "level": "full" if not miss else ("none" if cov == 0 else "partial"),
        "required": len(req),
        "covered": cov,
        "missing_count": len(miss),
        "missing": [_hex(c) for c in miss[:MAX_LISTED_MISSING]],
        "missing_codepoints": miss,
        "percent": round(cov * 1000 / len(req)) / 10,
        "ascii_letters_ok": not amiss,
        "ascii_missing": [_hex(c) for c in amiss],
        "marks": {
            "required": len(marks),
            "covered": len(marks) - len(mmiss),
            "missing_count": len(mmiss),
            "missing": [_hex(c) for c in mmiss],
            "missing_codepoints": mmiss,
        },
    }


# ------------------------------------------------------------------ tham chieu
def _find_refs(data: bytes, endian: str, ranges2, ctx: dict) -> dict:
    """PPtr thấy được tới Texture2D/Material/Font/TMP_FontAsset cùng file."""
    found = {"texture": [], "material": [], "font": [], "tmp_font_asset": []}
    seen = set()

    def kind_of(pid):
        if pid in ctx.get("textures", {}):
            return "texture"
        if pid in ctx.get("materials", {}):
            return "material"
        if pid in ctx.get("fonts", {}):
            return "font"
        if pid in ctx.get("tmp", {}):
            return "tmp_font_asset"
        return None

    for r0, r1 in ranges2:
        p = _align4(r0)
        while p + 12 <= r1:
            if _i32(data, p, endian) == 0:
                pid = _pid64(data, p + 4, endian)
                if pid is not None and pid != 0:
                    k = kind_of(pid)
                    if k is not None:
                        key = "%s#%d" % (k, pid)
                        if key not in seen:
                            seen.add(key)
                            if k == "tmp_font_asset":
                                nm = ctx["tmp"].get(pid)
                            elif k == "texture":
                                nm = ctx["textures"].get(pid)
                            elif k == "material":
                                nm = ctx["materials"].get(pid)
                            else:
                                nm = ctx["fonts"].get(pid)
                            if len(found[k]) < MAX_REFS:
                                found[k].append({"path_id": pid, "name": nm,
                                                 "offset": p})
            p += 4
    return found


def _norm_name(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def decode_object(data: bytes, endian: str, ctx=None) -> dict:
    """Giải mã THUẦN một object TMP_FontAsset (giống tmp_font.js decodeObject)."""
    ctx = ctx or {"textures": {}, "materials": {}, "fonts": {}, "tmp": {}}
    problems = []
    out = {
        "status": "layout_unknown", "reason": "", "name": None, "header": None,
        "font_info": None, "glyph_table": None, "refs": None,
        "matched_fonts": [], "code_points": None, "vietnamese": None,
        "problems": problems, "diagnostics": None,
    }
    hdr, hdr_problem = read_monobehaviour_header(data, endian)
    if hdr_problem is not None:
        problems.append(hdr_problem)
    if hdr is not None:
        out["header"] = {
            "script_file_id": hdr["script"]["file_id"],
            "script_path_id": hdr["script"]["path_id"],
            "enabled": hdr["enabled"],
        }
        out["name"] = hdr["name"]
    start = hdr["data_start"] if hdr is not None else 0
    fi = find_face_info(data, start, endian)
    if fi is None:
        # Asset layout moi (TMP 3.x) khong co ban sao FaceInfo cu:
        # lay FaceInfo chinh (2 chuoi + pointSize INT).
        fi = find_face_info_new(data, start, endian)
    if fi is not None:
        out["font_info"] = {
            "name": fi["name"],
            "family_name": fi.get("family_name") or fi["name"],
            "style_name": fi.get("style_name"),
            "format": fi.get("format", "old"),
            "point_size": fi["point_size"],
            "scale": fi["scale"],
            "declared_char_count": fi["declared_char_count"],
            "line_height": fi["line_height"],
            "baseline": fi.get("baseline"),
            "ascender": fi.get("ascender"),
            "cap_height": fi.get("cap_height"),
            "descender": fi.get("descender"),
            "center_line": fi.get("center_line"),
            "superscript_offset": fi.get("superscript_offset"),
            "subscript_offset": fi.get("subscript_offset"),
            "sub_size": fi.get("sub_size"),
            "underline": fi.get("underline"),
            "underline_thickness": fi.get("underline_thickness"),
            "strikethrough": fi.get("strikethrough"),
            "strikethrough_thickness": fi.get("strikethrough_thickness"),
            "tab_width": fi.get("tab_width"),
            "padding": fi.get("padding"),
            "atlas_width": fi["atlas_width"],
            "atlas_height": fi["atlas_height"],
            "atlas_basis": fi["atlas_basis"],
            "offset": fi["offset"],
        }
    else:
        problems.append("Không thấy neo FaceInfo (tên font + PointSize/Scale/"
                        "CharacterCount hợp lý)")

    def hex_head(off, count):
        return " ".join("%02x" % b for b in data[off:min(len(data), off + count)])

    # Quet bang glyph tu data_start (khong phai tu FaceInfo): layout moi
    # co the dat bang TRUOC ban sao FaceInfo ma find_face_info tim thay
    # (vd LiberationSans: bang @244, FaceInfo duplicate @23336).
    gt = find_glyph_table(data, start,
                          endian,
                          fi["atlas_width"] if fi is not None else None,
                          fi["atlas_height"] if fi is not None else None,
                          fi["declared_char_count"] if fi is not None else 0)
    out["diagnostics"] = {
        "object_size": len(data),
        "data_start": start,
        "faceinfo_offset": fi["offset"] if fi is not None else None,
        "hex_head": hex_head(start, HEX_HEAD_BYTES),
        "stride": gt["stride"] if gt is not None else None,
    }
    if gt is None:
        out["status"] = "glyph_table_not_found" if fi is not None else "layout_unknown"
        out["reason"] = ("Không thấy bảng glyph hợp lệ (36 byte/phần tử "
                         "cu hay 52 byte/phần tử mới)"
                         + (" (FaceInfo: %s, khai báo %d ký tự)"
                            % (fi["name"], fi["declared_char_count"])
                            if fi is not None else "")
                         + ". Bố cục TMP của bản TextMesh Pro này có thể khác "
                           "giả định; xem diagnostics.")
        if fi is not None and fi["declared_char_count"] > 0:
            hits = []
            p = _align4(start)
            while p + 4 <= len(data) and len(hits) < MAX_DIAG_HITS:
                if _i32(data, p, endian) == fi["declared_char_count"]:
                    h = {"offset": p,
                         "next_hex": hex_head(p + 4, 48)}
                    best_tc = None
                    for st, kd in ((STRIDE, _glyph_kind),
                                   (STRIDE_NEW, _glyph_kind_new)):
                        if p + 4 + st * fi["declared_char_count"] <= len(data):
                            tc = _table_check(
                                data, p, fi["declared_char_count"], endian,
                                fi["atlas_width"], fi["atlas_height"],
                                stride=st, kind=kd)
                            if (best_tc is None
                                    or tc["fail_count"] < best_tc["fail_count"]):
                                best_tc = tc
                    if best_tc is not None:
                        h["table_check"] = best_tc
                    hits.append(h)
                p += 4
            out["diagnostics"]["count_hits"] = hits
            best = None
            for h in hits:
                tc = h.get("table_check")
                if tc is not None and (best is None or tc["fail_count"] <=
                                       best["table_check"]["fail_count"]):
                    best = h
            if best is not None:
                d = dict(best["table_check"])
                d["offset"] = best["offset"]
                out["diagnostics"]["best_table_check"] = d
        return out
    stride = gt["stride"]
    is_new = stride == STRIDE_NEW
    if is_new:
        ids = [_u32(data, gt["offset"] + 4 + stride * i, endian)
               for i in range(gt["count"])]
    else:
        ids = [_i32(data, gt["offset"] + 4 + stride * i, endian)
               for i in range(gt["count"])]
    sample = read_glyph_records(data, gt["offset"], min(3, gt["count"]),
                                stride, endian)
    idset = set(ids)
    rg = _ranges(idset)
    out["glyph_table"] = {
        "offset": gt["offset"], "stride": stride,
        "layout": "new" if is_new else "old", "count": gt["count"],
        "duplicate_ids": gt["duplicate_ids"],
        "count_matches_declared": (fi["declared_char_count"] == gt["count"]
                                   if fi is not None else None),
        "placeholder_glyphs": gt["placeholder_glyphs"],
        "other_tables": gt["others"], "sample": sample,
    }
    out["code_points"] = {
        "kind": "glyph_index" if is_new else "code_point",
        "count": len(idset),
        "ranges": rg[:MAX_RANGES],
        "ranges_total": len(rg),
        "ranges_truncated": len(rg) > MAX_RANGES,
    }
    if is_new:
        # Layout moi luu glyph INDEX, khong phai codepoint: khong biet do
        # phu tieng Viet neu khong co cmap cua font nguon (xem sdk/sdf.py).
        out["vietnamese"] = {
            "level": "unknown", "required": len(vietnamese_letters_134()),
            "covered": 0, "missing_count": 0, "missing": [],
            "missing_codepoints": [], "percent": 0.0,
            "ascii_letters_ok": False, "ascii_missing": [],
            "marks": {"required": len(combining_marks_8()), "covered": 0,
                      "missing_count": 0, "missing": [],
                      "missing_codepoints": []},
            "note": "layout moi: id la glyph index; can cmap font nguon "
                    "de doi sang codepoint (sdk/sdf.py)",
        }
    else:
        out["vietnamese"] = vietnamese_coverage(idset)
    skip = gt["offset"] + 4 + stride * gt["count"]
    out["refs"] = _find_refs(data, endian, [[start, gt["offset"]],
                                            [skip, len(data)]], ctx)
    if fi is not None:
        nf = _norm_name(fi["name"])
        for pid, name in ctx.get("fonts", {}).items():
            nn = _norm_name(name)
            if nn and nf and (nn == nf or (min(len(nn), len(nf)) >= 4
                                           and (nn.startswith(nf)
                                                or nf.startswith(nn)))):
                out["matched_fonts"].append({
                    "path_id": pid, "name": name,
                    "basis": "tên giống FaceInfo.name (không phải tham chiếu)",
                })
    if gt["others"]:
        problems.append("Có thêm %d bảng glyph hợp lệ khác (đã chọn bảng %d "
                        "phần tử%s): %s"
                        % (len(gt["others"]), gt["count"],
                           " khớp CharacterCount" if fi is not None
                           and fi["declared_char_count"] == gt["count"]
                           else " lớn nhất",
                           ", ".join("%d@%d" % (o["count"], o["offset"])
                                     for o in gt["others"])))
    if gt["duplicate_ids"]:
        problems.append("%d id glyph trùng (<=1%% nên vẫn nhận)"
                        % gt["duplicate_ids"])
    status = "parsed"
    if fi is not None and fi["declared_char_count"] != gt["count"]:
        problems.append("CharacterCount khai báo %d != số glyph đọc được %d "
                        "(có thể bảng glyph thực khác hoặc FaceInfo cũ)"
                        % (fi["declared_char_count"], gt["count"]))
        status = "partial"
    if fi is None:
        problems.append("Không có FaceInfo: không biết tên font nguồn/kích "
                        "thước atlas; bảng glyph chỉ được kiểm bằng ràng "
                        "buộc chung")
        status = "partial"
    out["status"] = status
    if is_new:
        out["reason"] = ("Atlas có %d glyph (layout mới, id = glyph index; "
                         "cần cmap font nguồn để biết codepoint/độ phủ "
                         "tiếng Việt)" % len(idset))
    else:
        out["reason"] = ("Atlas có %d ký tự khác nhau; tiếng Việt %d/%d (%s)"
                         % (len(idset), out["vietnamese"]["covered"],
                            out["vietnamese"]["required"],
                            out["vietnamese"]["level"]))
    return out


# ------------------------------------------------------------------ nhan dien
def monoscript_class_name(data: bytes, endian: str):
    """Tên class của MonoScript: m_Name, m_ExecutionOrder, hash 16 byte,
    m_ClassName, m_Namespace, m_AssemblyName. Trả None nếu không hợp lệ."""
    first = _read_aligned_string(data, 0, endian, 512)
    if first is None:
        return None
    off = first[1]
    if off + 4 + 16 > len(data):
        return None
    off += 4 + 16
    second = _read_aligned_string(data, off, endian, 512)
    if second is None:
        return None
    return second[0]


def script_class_of_object(sf, obj):
    """(tên_class, đã_xác_nhận, script_ngoài_file) của MonoBehaviour.

    Đường chính: types[type_id].script_type_index -> script_types ->
    MonoScript cùng file. Dự phòng: đọc trực tiếp PPtr m_Script trong
    bytes object (offset 16/20).
    """
    def resolve(file_id, path_id):
        if file_id != 0:
            return None, False, True
        try:
            target = sf.get_object(path_id)
        except PB3Error:
            return None, False, False
        if target.class_id != MONO_SCRIPT_CLASS_ID:
            return None, False, False
        try:
            raw = sf.object_bytes(path_id)
        except PB3Error:
            return None, False, False
        name = monoscript_class_name(raw, sf.endian)
        if name is None:
            return None, False, False
        return name, True, False

    typ = None
    try:
        typ = sf.types[obj.type_id]
    except (IndexError, AttributeError):
        typ = None
    sti = getattr(typ, "script_type_index", None) if typ is not None else None
    if sti is not None and sti >= 0 and sti < len(getattr(sf, "script_types", [])):
        file_id, path_id = sf.script_types[sti]
        name, ok, ext = resolve(file_id, path_id)
        if ok or ext:
            return name, ok, ext
    try:
        raw = sf.object_bytes(obj.path_id)
    except PB3Error:
        return None, False, False
    if len(raw) >= 28:
        file_id = _i32(raw, 16, sf.endian)
        path_id = _pid64(raw, 20, sf.endian)
        if path_id is not None:
            return resolve(file_id, path_id)
    return None, False, False


def find_tmp_font_assets(sf):
    """Tất cả MonoBehaviour (class 114) kèm thông tin script.

    Mỗi mục: {object_index, path_id, script_class, script_resolved,
    script_external, is_tmp}. `is_tmp` = script là TMP_FontAsset.
    """
    out = []
    for idx, obj in enumerate(getattr(sf, "objects", [])):
        if getattr(obj, "class_id", None) != MONO_BEHAVIOUR_CLASS_ID:
            continue
        name, ok, ext = script_class_of_object(sf, obj)
        out.append({
            "object_index": idx,
            "path_id": obj.path_id,
            "script_class": name,
            "script_resolved": ok,
            "script_external": ext,
            "is_tmp": name == "TMP_FontAsset",
        })
    return out


def _object_display_name(sf, obj, at_name_off=0):
    """m_Name: chuoi dau object (at_name_off=0) hoac offset 28 (MonoBehaviour)."""
    try:
        raw = sf.object_bytes(obj.path_id)
    except PB3Error:
        return None
    got = _read_aligned_string(raw, at_name_off, sf.endian, 512)
    return got[0] if got else None


def scan_serialized_file(sf, source: str) -> dict:
    """Quét 1 SerializedFile đã parse: đọc mọi TMP_FontAsset xác nhận.

    Trả {schema, source, items, candidates, summary, warnings}.
    `candidates` = MonoBehaviour không xác nhận được class script (script
    ngoài file / không đọc được) — KHÔNG đưa vào `items`, chỉ ghi để
    Joseph đối chiếu thủ công.
    """
    ctx = {"textures": {}, "materials": {}, "fonts": {}, "tmp": {}}
    tmp_index = set()
    for idx, obj in enumerate(getattr(sf, "objects", [])):
        cid = getattr(obj, "class_id", None)
        if cid == TEXTURE_CLASS_ID:
            ctx["textures"][obj.path_id] = _object_display_name(sf, obj, 0)
        elif cid == MATERIAL_CLASS_ID:
            ctx["materials"][obj.path_id] = _object_display_name(sf, obj, 0)
        elif cid == FONT_CLASS_ID:
            ctx["fonts"][obj.path_id] = _object_display_name(sf, obj, 0)
    found = find_tmp_font_assets(sf)
    for f in found:
        if f["is_tmp"]:
            tmp_index.add(f["object_index"])
    for idx, obj in enumerate(getattr(sf, "objects", [])):
        if idx in tmp_index:
            ctx["tmp"][obj.path_id] = _object_display_name(sf, obj, 28)
    res = {"schema": SCHEMA, "source": source, "items": [], "candidates": [],
           "summary": None, "warnings": []}
    for f in found:
        if not f["is_tmp"]:
            if not f["script_resolved"]:
                res["candidates"].append({
                    "object_index": f["object_index"],
                    "path_id": f["path_id"],
                    "script_class": f["script_class"],
                    "script_external": f["script_external"],
                    "note": "Không xác nhận được class script (script ngoài "
                            "file hoặc không đọc được) — chưa đưa vào kết quả "
                            "TMP, cần đối chiếu thủ công.",
                })
            continue
        if len(res["items"]) >= MAX_ASSETS:
            if not any("Quá %d TMP_FontAsset" % MAX_ASSETS in w
                       for w in res["warnings"]):
                res["warnings"].append(
                    "Quá %d TMP_FontAsset, bỏ phần còn lại" % MAX_ASSETS)
            continue
        obj = sf.objects[f["object_index"]]
        item = {"id": len(res["items"]), "source": source,
                "object_index": f["object_index"], "path_id": obj.path_id,
                "size": obj.byte_size, "name": None}
        res["items"].append(item)
        if obj.byte_size > MAX_ASSET_BYTES:
            item.update({"status": "too_large",
                         "reason": "Object %d byte vượt giới hạn %d"
                                   % (obj.byte_size, MAX_ASSET_BYTES),
                         "problems": []})
            continue
        try:
            raw = sf.object_bytes(obj.path_id)
        except PB3Error as exc:
            item.update({"status": "error",
                         "reason": "Object ngoài dữ liệu đã đọc (%s)"
                                   % getattr(exc, "code", "E_CORRUPT"),
                         "problems": []})
            continue
        decoded = decode_object(raw, sf.endian, ctx)
        item.update(decoded)
        item["id"] = len(res["items"]) - 1
        if decoded.get("name") is None:
            item["name"] = ctx["tmp"].get(obj.path_id)
    res["summary"] = _summarize(res)
    return res


def _summarize(res: dict) -> dict:
    s = {"assets": len(res["items"]), "parsed": 0, "partial": 0,
         "not_found": 0, "other": 0, "by_status": {},
         "vietnamese_full": 0, "vietnamese_partial": 0, "vietnamese_none": 0,
         "marks_full": 0}
    for it in res["items"]:
        st = it.get("status")
        s["by_status"][st] = s["by_status"].get(st, 0) + 1
        if st == "parsed":
            s["parsed"] += 1
        elif st == "partial":
            s["partial"] += 1
        elif st in ("glyph_table_not_found", "layout_unknown"):
            s["not_found"] += 1
        else:
            s["other"] += 1
        vi = it.get("vietnamese")
        if vi:
            if vi["level"] == "full":
                s["vietnamese_full"] += 1
            elif vi["level"] == "partial":
                s["vietnamese_partial"] += 1
            else:
                s["vietnamese_none"] += 1
            if vi["marks"]["missing_count"] == 0:
                s["marks_full"] += 1
    return s


# ------------------------------------------------------------------ quet nguon
def _scan_unity_bytes(data: bytes, display: str, out: dict) -> None:
    """Quét 1 blob Unity (bundle hoặc SerializedFile rời)."""
    from sdk import unity as uni

    if len(data) > MAX_FILE_BYTES:
        out["warnings"].append(
            "%s: %d byte vượt giới hạn %d — bỏ qua"
            % (display, len(data), MAX_FILE_BYTES))
        return
    try:
        if uni.detect_bundle(data) is not None:
            bundle = uni.parse_bundle(data, where=display)
            for entry in bundle.entries:
                if not entry.is_serialized:
                    continue
                try:
                    payload = bundle.get_entry(entry.name)
                except PB3Error:
                    continue
                # Node bundle: BearTea ĐỌC ĐƯỢC (hơn studio — studio chỉ
                # cảnh báo, không đọc node). Ghi rõ nguồn là node bundle.
                try:
                    inner = uni.parse_serialized(payload,
                                                 where=display + "::" + entry.name)
                except PB3Error as exc:
                    out["warnings"].append(
                        "%s::%s: không đọc được SerializedFile (%s)"
                        % (display, entry.name, getattr(exc, "code", "?")))
                    continue
                scanned = scan_serialized_file(
                    inner, display + "::" + entry.name)
                out["items"].extend(scanned["items"])
                out["candidates"].extend(scanned["candidates"])
                out["warnings"].extend(scanned["warnings"])
                out["bundle_nodes"] += 1
            out["files_scanned"].append(display)
            return
        sf = uni.parse_serialized(data, where=display)
    except PB3Error as exc:
        out["warnings"].append("%s: không đọc được Unity (%s)"
                               % (display, getattr(exc, "code", "?")))
        return
    scanned = scan_serialized_file(sf, display)
    out["items"].extend(scanned["items"])
    out["candidates"].extend(scanned["candidates"])
    out["warnings"].extend(scanned["warnings"])
    out["files_scanned"].append(display)


def is_unity_entry(name: str) -> bool:
    low = name.lower()
    if low.endswith((".assets", ".unity3d", ".bundle")):
        return True
    base = os.path.basename(low)
    return base.startswith("level") or base.startswith("sharedassets")


def _is_unity_entry(name: str) -> bool:
    return is_unity_entry(name)


def scan_source(path: str) -> dict:
    """Quét TMP_FontAsset trong file/thư mục/ZIP/IPA/bundle Unity.

    Không ghi gì. Trả {ok, assets, candidates, summary, warnings,
    files_scanned, bundle_nodes, glyph_plan}.
    """
    from sdk import containers as cont

    src = str(path)
    if not os.path.exists(src):
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không có file/thư mục: %s" % src, where=src)
    out = {"ok": True, "items": [], "candidates": [], "warnings": [],
           "files_scanned": [], "bundle_nodes": 0}
    targets = []  # (display, data|None, from_zip)
    if os.path.isdir(src):
        for dirpath, _dirs, files in os.walk(src):
            for fn in sorted(files):
                if _is_unity_entry(fn):
                    targets.append((os.path.join(dirpath, fn), None, False))
    elif os.path.isfile(src):
        if zipfile.is_zipfile(src):
            vfs = cont.open_vfs(src)
            for name in vfs.iter_entries():
                if not _is_unity_entry(name):
                    continue
                try:
                    data = vfs.read(name)
                except PB3Error as exc:
                    out["warnings"].append(
                        "%s: không đọc được (%s)"
                        % (name, getattr(exc, "code", "?")))
                    continue
                if len(data) > SCAN_MAX_ENTRY_BYTES:
                    out["warnings"].append(
                        "%s: quá lớn (%d byte), bỏ qua"
                        % (name, len(data)))
                    continue
                targets.append((name, data, True))
        elif _is_unity_entry(src) or src.lower().endswith((".assets", ".unity3d")):
            targets.append((src, None, False))
        else:
            raise PB3Error(
                "E_INPUT_NOT_FOUND",
                "không nhận diện được nguồn TMP: %s (cần .assets/.unity3d, "
                "level/sharedassets, thư mục, ZIP/IPA)" % src, where=src)
    for display, data, _from_zip in targets:
        if len(out["items"]) >= MAX_ASSETS:
            out["warnings"].append(
                "Quá %d TMP_FontAsset tổng, dừng quét" % MAX_ASSETS)
            break
        try:
            if data is None:
                with open(display, "rb") as fh:
                    data = fh.read()
            _scan_unity_bytes(data, display, out)
        except PB3Error as exc:
            out["warnings"].append("%s: %s"
                                  % (display, getattr(exc, "code", "?")))
    out["summary"] = _summarize({"items": out["items"]})
    out["glyph_plan"] = build_glyph_plan(out["items"])
    return out


# ------------------------------------------------------------------ glyph plan
def build_glyph_plan(items) -> dict:
    """Kế hoạch glyph cho Unity Editor: chữ thiếu + tham số atlas cần giữ.

    Đây là phần "convert bảng glyph" web làm được: từ bảng glyph đã đọc,
    tính chữ tiếng Việt còn thiếu (134 chữ cái + 8 dấu), xuất chuỗi ký tự
    cho ô "Custom Characters" của TMP Font Asset Creator + tham số atlas
    (kích thước, padding, point size, scale) để script Editor tái dựng
    atlas SDF với CÙNG tham số gốc. Sinh lại atlas SDF thì bắt buộc ở
    Editor — web không làm được (xem module docstring).
    """
    missing_letters = set()
    missing_marks = set()
    targets = []
    for it in items:
        if it.get("status") not in ("parsed", "partial"):
            continue
        vi = it.get("vietnamese")
        if not vi:
            continue
        missing_letters.update(vi.get("missing_codepoints", []))
        missing_marks.update(vi.get("marks", {}).get("missing_codepoints", []))
        fi = it.get("font_info") or {}
        refs = it.get("refs") or {}
        targets.append({
            "name": it.get("name"),
            "source": it.get("source"),
            "path_id": it.get("path_id"),
            "status": it.get("status"),
            "face_name": fi.get("name"),
            "point_size": fi.get("point_size"),
            "scale": fi.get("scale"),
            "padding": fi.get("padding"),
            "atlas_width": fi.get("atlas_width"),
            "atlas_height": fi.get("atlas_height"),
            "atlas_basis": fi.get("atlas_basis"),
            "glyph_count": (it.get("glyph_table") or {}).get("count"),
            "declared_char_count": fi.get("declared_char_count"),
            "vietnamese_covered": vi.get("covered"),
            "vietnamese_required": vi.get("required"),
            "missing_letters": [_hex(c) for c in vi.get("missing_codepoints", [])],
            "missing_marks": [_hex(c) for c in
                              vi.get("marks", {}).get("missing_codepoints", [])],
            "material_refs": refs.get("material", []),
            "texture_refs": refs.get("texture", []),
            "font_refs": refs.get("font", []),
        })
    missing_all = sorted(missing_letters | missing_marks)
    plan = {
        "schema": "tmp_glyph_plan/1",
        "chars_text": vietnamese_chars_text(missing_all),
        "chars_text_all": vietnamese_chars_text(),
        "missing_letters": [_hex(c) for c in sorted(missing_letters)],
        "missing_letters_codepoints": sorted(missing_letters),
        "missing_marks": [_hex(c) for c in sorted(missing_marks)],
        "missing_marks_codepoints": sorted(missing_marks),
        "missing_total": len(missing_all),
        "targets": targets,
        "editor_script": "unity_editor/TMP_VietnameseAtlas.cs",
        "notes": [
            "Chuỗi chars_text dùng cho ô Custom Characters của TMP Font "
            "Asset Creator, hoặc nạp file này vào script Editor.",
            "Atlas SDF chỉ sinh lại được trong Unity Editor (FontEngine) — "
            "web không sinh được SDF, không dùng msdf-atlas-gen.",
            "Mức rẻ nhất: giữ nguyên material (shader + thuộc tính SDF), "
            "chỉ đổi texture atlas + bảng glyph — script Editor làm bước "
            "này khi atlas cũ còn chỗ trống (TryAddCharacters).",
            "Nếu atlas cũ đầy: script tạo atlas mới CÙNG padding/render "
            "mode/point size, copy bảng glyph + face info sang asset gốc "
            "rồi mới đổi material.mainTexture.",
            "Khác với mục C6: C6 tạo TMP_FontAsset MỚI chỉ chứa dấu tiếng "
            "Việt làm fallback, KHÔNG sửa atlas gốc; mục 7 sửa chính atlas "
            "gốc. Xem build_fallback_plan() (mục C6).",
        ],
    }
    return plan


# ------------------------------------------------------------------ ke hoach fallback (muc C6)
FALLBACK_SCHEMA = "tmp_fallback_plan/1"
#: Atlas fallback: chi chua chu thieu (<=142 ky tu) nen khong can atlas
#: to nhu goc; gioi han [256, 1024] moi chieu, giu dung ty le goc khi co
#: the. Script Editor tu tang len toi da 4096 neu TryAddCharacters bao
#: atlas day (xem site/unity_editor/TMP_VietnameseFallback.cs).
FALLBACK_ATLAS_MIN = 256
FALLBACK_ATLAS_MAX = 1024
FALLBACK_ATLAS_DEFAULT = 512
FALLBACK_ASSET_DIR = "Assets/BearTea"


def _fallback_atlas_size(base_w, base_h):
    """Kich thuoc atlas cho asset fallback: goc kep trong [256, 1024].

    Tra (width, height); goc khong co so thi 512x512. Giu ty le goc:
    neu goc la 512x256 thi fallback la 512x256 (khong ep vuong).
    """
    def clamp(v):
        if v is None:
            return FALLBACK_ATLAS_DEFAULT
        try:
            v = int(v)
        except (TypeError, ValueError):
            return FALLBACK_ATLAS_DEFAULT
        return min(max(v, FALLBACK_ATLAS_MIN), FALLBACK_ATLAS_MAX)

    return clamp(base_w), clamp(base_h)


def build_fallback_plan(items, glyph_plan=None) -> dict:
    """Ke hoach font fallback TMP cho Unity Editor (muc C6).

    Tu cac TMP_FontAsset da doc (items cua scan_source / scan_serialized_
    file), lap danh sach TMP_FontAsset MOI chi chua chu tieng Viet con
    thieu, de script Editor tao roi dang ky vao danh sach fallback cua
    TMP_Settings (+ bang fallback cua asset goc).

    RANH GIOI (doc ky):
    - Web CHI LAP KE HOACH (JSON): chu nao thieu, tham so atlas goc nao
      can giu (PointSize, Scale, Padding, render mode doc lai trong
      Editor), ten asset fallback, cac buoc Editor. Web KHONG tao duoc
      TMP_FontAsset that vi atlas SDF chi sinh duoc bang FontEngine
      trong Unity Editor — ke hoach nay khong phai asset dung duoc ngay.
    - Asset fallback CHI cuu chu tieng Viet thieu. Neu asset goc thieu
      ca chu Latin (ascii_missing), fallback khong cuu duoc phan do —
      ghi ro tung target de nguoi dung biet phai sua them atlas goc
      (muc 7) hoac them chu Latin vao fallback.
    - Material/texture/atlas GOC KHONG DOI: ke hoach chi ghi ten tham
      chieu de script Editor tim dung asset goc; script C6 khong gan
      lai material.mainTexture hay atlasTexture cua asset goc.
    - Asset doc khong duoc bang glyph (glyph_table_not_found /
      layout_unknown) thi KHONG lap duoc ke hoach (khong biet thieu chu
      nao) — ghi vao `unplanned`, khong bia danh sach ky tu.
    """
    if glyph_plan is None:
        glyph_plan = build_glyph_plan(items)
    fallbacks = []
    already_full = []
    unplanned = []
    for it in items:
        st = it.get("status")
        if st not in ("parsed", "partial"):
            unplanned.append({
                "name": it.get("name"),
                "source": it.get("source"),
                "path_id": it.get("path_id"),
                "status": st,
                "reason": (it.get("reason")
                           or "Không đọc được bảng glyph nên không biết "
                           "thiếu chữ nào — cần đọc lại trong Editor."),
            })
            continue
        vi = it.get("vietnamese")
        if not vi:
            unplanned.append({
                "name": it.get("name"),
                "source": it.get("source"),
                "path_id": it.get("path_id"),
                "status": st,
                "reason": "Không có dữ liệu độ phủ tiếng Việt.",
            })
            continue
        miss_letters = list(vi.get("missing_codepoints", []))
        miss_marks = list(vi.get("marks", {}).get("missing_codepoints", []))
        fi = it.get("font_info") or {}
        refs = it.get("refs") or {}
        if not miss_letters and not miss_marks:
            already_full.append({
                "name": it.get("name"),
                "source": it.get("source"),
                "path_id": it.get("path_id"),
                "face_name": fi.get("name"),
                "note": "Đã đủ 134 chữ + 8 dấu — không cần fallback.",
            })
            continue
        missing_all = sorted(set(miss_letters) | set(miss_marks))
        base_name = fi.get("name") or it.get("name") or "TMP Font"
        fb_w, fb_h = _fallback_atlas_size(fi.get("atlas_width"),
                                          fi.get("atlas_height"))
        ascii_missing = list(vi.get("ascii_missing", []))
        fallbacks.append({
            "name": it.get("name"),
            "source": it.get("source"),
            "path_id": it.get("path_id"),
            "status": st,
            "fallback_name": "%s Vietnamese Fallback" % base_name,
            "fallback_asset_path": "%s/%s Vietnamese Fallback.asset"
                                   % (FALLBACK_ASSET_DIR, base_name),
            "face_name": fi.get("name"),
            "source_font_hint": fi.get("name"),
            "font_refs": refs.get("font", []),
            "missing_letters": [_hex(c) for c in miss_letters],
            "missing_letters_codepoints": miss_letters,
            "missing_marks": [_hex(c) for c in miss_marks],
            "missing_marks_codepoints": miss_marks,
            "missing_total": len(missing_all),
            "chars_text": vietnamese_chars_text(missing_all),
            "ascii_missing": ascii_missing,
            "ascii_note": ("Asset gốc còn thiếu chữ Latin (%s) — fallback "
                           "chỉ chứa chữ Việt nên KHÔNG cứu được phần này; "
                           "sửa atlas gốc (mục 7) hoặc thêm chữ Latin vào "
                           "fallback trong Editor."
                           % ", ".join(ascii_missing)
                           if ascii_missing else None),
            "base_point_size": fi.get("point_size"),
            "base_scale": fi.get("scale"),
            "base_line_height": fi.get("line_height"),
            "base_padding": fi.get("padding"),
            "base_atlas_width": fi.get("atlas_width"),
            "base_atlas_height": fi.get("atlas_height"),
            "base_atlas_basis": fi.get("atlas_basis"),
            "base_render_mode": None,
            "render_mode_note": "Render mode không nằm trong FaceInfo — "
                                "script Editor đọc lại từ asset gốc "
                                "(fontAsset.atlasRenderMode) và giữ nguyên.",
            "fallback_atlas_width": fb_w,
            "fallback_atlas_height": fb_h,
            "glyph_count": (it.get("glyph_table") or {}).get("count"),
            "declared_char_count": fi.get("declared_char_count"),
            "vietnamese_covered": vi.get("covered"),
            "vietnamese_required": vi.get("required"),
            "material_refs": refs.get("material", []),
            "texture_refs": refs.get("texture", []),
            "material_unchanged": True,
            "material_note": "Material gốc KHÔNG đổi: script C6 chỉ đọc "
                             "tham số từ asset gốc, không gán lại "
                             "material.mainTexture hay atlasTexture của nó.",
            "editor_steps": [
                "Trong Unity Editor: chọn TMP_FontAsset gốc \"%s\" trong "
                "cửa sổ Project." % (it.get("name") or base_name),
                "Menu BearTea → TMP → \"4. Tạo font fallback tiếng Việt "
                "(từ file tmp_fallback_plan.json)\" rồi chọn file kế "
                "hoạch này.",
                "Chọn font nguồn khi script hỏi (font gốc của game, hoặc "
                "Be Vietnam Pro nếu font gốc đã mất) — atlas SDF của "
                "fallback chỉ sinh được trong Editor.",
                "Kiểm tra Console: fallback \"%s\" đã tạo + đã đăng ký "
                "vào TMP_Settings và bảng fallback của asset gốc."
                % ("%s Vietnamese Fallback" % base_name),
                "Bấm Play, mở màn hình có chữ Việt: chữ thiếu trước đây "
                "phải hiện đủ dấu, font gốc không đổi nét/viền.",
            ],
        })
    missing_all = sorted(set(glyph_plan.get("missing_letters_codepoints", []))
                         | set(glyph_plan.get("missing_marks_codepoints", [])))
    plan = {
        "schema": FALLBACK_SCHEMA,
        "chars_text": glyph_plan.get("chars_text", ""),
        "chars_text_all": glyph_plan.get("chars_text_all", ""),
        "missing_letters": glyph_plan.get("missing_letters", []),
        "missing_letters_codepoints": glyph_plan.get(
            "missing_letters_codepoints", []),
        "missing_marks": glyph_plan.get("missing_marks", []),
        "missing_marks_codepoints": glyph_plan.get(
            "missing_marks_codepoints", []),
        "missing_total": len(missing_all),
        "fallbacks": fallbacks,
        "fallbacks_needed": len(fallbacks),
        "already_full": already_full,
        "already_full_count": len(already_full),
        "unplanned": unplanned,
        "unplanned_count": len(unplanned),
        "editor_script": "unity_editor/TMP_VietnameseFallback.cs",
        "editor_script_url": "unity_editor/TMP_VietnameseFallback.cs",
        "steps_global": [
            "Trên web BearTea: chọn file .assets/.unity3d hoặc IPA của "
            "game → tab Font → \"Đọc TMP_FontAsset\" rồi \"Tạo font "
            "fallback\" → tải file tmp_fallback_plan.json này về.",
            "Tải script unity_editor/TMP_VietnameseFallback.cs về (link "
            "trong kết quả trên web) rồi chép vào Assets/Editor/ trong "
            "project Unity của game.",
            "Trong Unity Editor: làm theo editor_steps của từng fallback "
            "trong file này (chọn asset gốc → menu BearTea → TMP → "
            "mục 4).",
            "Kiểm tra: menu BearTea → TMP → \"6. Kiểm tra fallback đã "
            "đăng ký (TMP_Settings)\" phải liệt kê đủ fallback mới.",
            "Thử trong game thật (Play trong Editor rồi build ra iPhone) "
            "— chỉ Joseph làm được bước cuối này.",
        ],
        "notes": [
            "Kế hoạch này KHÔNG phải TMP_FontAsset dùng được ngay: atlas "
            "SDF của fallback chỉ sinh được trong Unity Editor (FontEngine) "
            "— web không sinh được SDF, không dùng msdf-atlas-gen.",
            "Fallback chỉ cứu chữ tiếng Việt thiếu; chữ Latin thiếu (nếu "
            "có, xem ascii_note từng target) cần sửa atlas gốc (mục 7) "
            "hoặc thêm vào fallback trong Editor.",
            "Material/texture/atlas của asset gốc KHÔNG đổi — script C6 "
            "chỉ đọc tham số gốc, không gán lại material.mainTexture hay "
            "atlasTexture của asset gốc (khác mục 7 sửa chính atlas gốc).",
            "Khác với mục 7: mục 7 thêm glyph vào atlas gốc (giữ nguyên "
            "material); C6 tạo asset MỚI chỉ chứa dấu/chữ Việt thiếu rồi "
            "đăng ký fallback — dùng khi atlas gốc không sửa được (mất "
            "font nguồn, không tạo atlas mới được) hoặc muốn giữ nguyên "
            "100% asset gốc.",
        ],
    }
    return plan
