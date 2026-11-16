"""Liệt kê font của game + gom về 1 font đủ dấu tiếng Việt — phần (D).

Tái dùng, không tự định nghĩa lại:
- Bộ 142 mục (134 chữ cái + 8 dấu kết hợp): sdk/font.py REQUIRED_VIETNAMESE.
- Đọc cmap TTF/OTF + gộp glyph: sdk/font.py (coverage, merge_vietnamese).
- Đọc TMP_FontAsset (FaceInfo, bảng glyph, độ phủ 134+8): sdk/tmp_font.py
  (scan_serialized_file, build_fallback_plan, build_glyph_plan).
- Liệt kê Font object nhúng (class 128): sdk/font.py list_unity_font_objects.

Kiến trúc web (khác cách gốc iSH): module chạy trong worker Pyodide qua
bridge (font_list / font_unify), không dùng services/font_tool.py (lớp
dịch vụ CLI của iSH: tiến độ terminal + ghi ngược vào ZIP/IPA đã dựng
lại — không hợp luồng file/lưu/chia sẻ của web). Quét ở đây CHỈ ĐỌC;
thao tác "dùng chung 1 font" trả về file font_unified.zip để tải về
(TTF đã gộp + kế hoạch JSON + font Be Vietnam Pro), không ghi đè file
gốc trong tab trình duyệt.

Điều tra trung thực trước khi code (xem exp/analysis_font_unify_results.md):
- TMP_FontAsset: tạo atlas SDF chỉ làm được trong Unity Editor
  (Font Asset Creator + FontEngine; docs Unity TextMeshPro). Web chỉ
  lập được kế hoạch fallback (C6) + glyph plan (mục 7) — tái dùng.
- Font TTF rời (file .ttf hoặc entry trong IPA/ZIP): web gộp ĐƯỢC
  thật bằng sdk/font.merge_vietnamese (copy outline glyf + cmap).
- Font OTF/CFF: chỉ đọc được cmap, KHÔNG gộp được (giới hạn sdk/font).
- Font TTC: chưa hỗ trợ đọc/gộp.
- Font nhúng trong SerializedFile (Font object, class 128): chỉ liệt
  kê được tên/path_id, KHÔNG đọc được bytes font, KHÔNG sửa được
  object — đường làm là thay bằng font đủ dấu trong Unity Editor.
- Đưa font đã gộp trở lại vào .assets/IPA: web KHÔNG đóng gói lại
  file gốc; người dùng thay trong Unity Editor hoặc dùng bản iSH.

Giới hạn RAM tab (giống sdk/tmp_font): file Unity > 256MB hoặc entry
> 32MB thì bỏ qua kèm cảnh báo — data.unity3d 590MB vì vậy không
liệt kê được font trên web (ghi rõ trong báo cáo 5 game).
"""
from __future__ import annotations

import os
import zipfile
from typing import Dict, List, Optional

from core.errors import PB3Error
from sdk import font as font_sdk
from sdk import tmp_font as tf

#: Schema danh sách font / kế hoạch gom 1 font.
SCHEMA_LIST = "font_list/1"
SCHEMA_PLAN = "font_unify_plan/1"
#: Font đi kèm, đủ 142/142 — font duy nhất mọi font game được gom về.
DONOR_NAME = "Be Vietnam Pro"
DONOR_FILE = "BeVietnamPro-Regular.ttf"
#: Giới hạn (giống sdk/tmp_font để nhất quán RAM tab Safari).
MAX_FONTS = 200
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024 * 1024
#: Tối đa chữ thiếu liệt kê chi tiết trong 1 mục font (còn lại đếm tổng).
MAX_LISTED_MISSING = 150

#: Giới hạn thật của thao tác "dùng chung 1 font" (hiển thị trong UI + plan).
LIMITS = [
    "Atlas SDF của TextMeshPro KHÔNG tạo được trên web — chỉ sinh được "
    "trong Unity Editor (Font Asset Creator + FontEngine). Web chỉ lập "
    "kế hoạch fallback (mục C6) + glyph plan (mục 7) để nạp vào Editor.",
    "Font TTF rời (file .ttf hoặc entry trong IPA/ZIP) gộp ĐƯỢC thật "
    "trên web: copy glyph tiếng Việt còn thiếu từ Be Vietnam Pro vào "
    "font gốc (sdk/font.merge_vietnamese) — file đã gộp nằm trong "
    "font_unified.zip.",
    "Font OTF/CFF chỉ đọc được cmap, KHÔNG gộp được glyph; font TTC "
    "chưa hỗ trợ — hai loại này chỉ thay bằng Be Vietnam Pro rồi trỏ "
    "lại tham chiếu trong Unity Editor.",
    "Font nhúng trong SerializedFile (Font object, class 128) chỉ liệt "
    "kê được tên/path_id — không đọc được bytes font, không sửa được "
    "object; thay bằng font đủ dấu trong Unity Editor.",
    "Web KHÔNG đóng gói font đã gộp trở lại vào .assets/IPA gốc — "
    "người dùng thay trong Unity Editor (hoặc dùng bản iSH để ghi "
    "ngược vào file đã dựng lại).",
]

EDITOR_STEPS = [
    "1. Tải font_unified.zip về: trong đó có font_unify_plan.json (kế "
    "hoạch), các file *.vi.ttf (TTF đã gộp đủ dấu), tmp_fallback_plan.json "
    "(nếu game có TMP_FontAsset) và BeVietnamPro-Regular.ttf (font đủ "
    "142/142, OFL).",
    "2. Font TTF rời: chép file *.vi.ttf đè lên font gốc trong project "
    "Unity (hoặc đổi tên về tên gốc), build lại.",
    "3. Font TMP: trong Unity Editor, chạy script "
    "unity_editor/TMP_VietnameseFallback.cs với file "
    "tmp_fallback_plan.json (menu BearTea → TMP → tạo font fallback) — "
    "script tạo TMP_FontAsset fallback rồi đăng ký vào TMP_Settings; "
    "atlas SDF của fallback chỉ sinh được ở bước này.",
    "4. Font OTF/TTC/nhúng: thay tham chiếu font trong project bằng "
    "BeVietnamPro-Regular.ttf (kèm trong zip), build lại rồi kiểm tra "
    "lại bằng nút “Liệt kê font của game”.",
]


def _hex(cp: int) -> str:
    return "U+%04X" % cp


def _missing_display(missing: List[int]) -> List[str]:
    return [_hex(c) for c in missing[:MAX_LISTED_MISSING]]


def _tmp_entry(item: dict) -> dict:
    """Đổi 1 TMP_FontAsset đã đọc (sdk/tmp_font) thành 1 mục font /142."""
    vi = item.get("vietnamese") or {}
    fi = item.get("font_info") or {}
    status = item.get("status")
    letters_missing = list(vi.get("missing_codepoints", []))
    marks = vi.get("marks") or {}
    marks_missing = list(marks.get("missing_codepoints", []))
    missing = sorted(set(letters_missing) | set(marks_missing))
    covered = None
    if vi.get("required") is not None:
        covered = (int(vi.get("covered", 0))
                   + int(marks.get("required", 0))
                   - int(marks.get("missing_count", 0)))
    if status in ("parsed", "partial"):
        verdict = "full" if not missing else "partial"
    else:
        verdict = "unreadable"
        covered = None
        missing = []
    return {
        "kind": "tmp_font_asset",
        "name": item.get("name") or fi.get("name") or ("TMP #%s" % item.get("path_id")),
        "family": fi.get("name") or "",
        "source": item.get("source") or "",
        "size": item.get("size"),
        "path_id": item.get("path_id"),
        "num_glyphs": (item.get("glyph_table") or {}).get("count"),
        "covered": covered,
        "total": 142,
        "missing_codepoints": missing,
        "missing": _missing_display(missing),
        "missing_total": len(missing),
        "status": verdict,
        "reason": item.get("reason") or "",
        "face_name": fi.get("name"),
        "point_size": fi.get("point_size"),
        "atlas_width": fi.get("atlas_width"),
        "atlas_height": fi.get("atlas_height"),
        "tmp_status": status,
        "problems": list(item.get("problems") or []),
    }


def _standalone_entry(data: bytes, display: str, source: str) -> Optional[dict]:
    """1 font TTF/OTF/TTC rời (file hoặc entry ZIP/IPA) -> mục font /142."""
    kind = font_sdk.detect_font_kind(data)
    base = {
        "kind": kind if kind in ("ttf", "otf", "ttc") else "unknown",
        "name": os.path.basename(display),
        "family": "",
        "source": source,
        "size": len(data),
        "path_id": None,
        "num_glyphs": 0,
        "covered": None,
        "total": 142,
        "missing_codepoints": [],
        "missing": [],
        "missing_total": 0,
        "status": "unreadable",
        "reason": "",
    }
    if kind == "ttc":
        base.update({
            "status": "unsupported",
            "reason": "Font collection (.ttc) chưa hỗ trợ — tách từng font "
                      "con ra file .ttf riêng rồi mới kiểm/gộp được.",
        })
        return base
    if kind == "unknown":
        base.update({"reason": "Không phải font TrueType/OpenType (magic lạ)."})
        return base
    try:
        rep = font_sdk.coverage(data, where=os.path.basename(display))
    except PB3Error as exc:
        base.update({"reason": "%s: %s" % (exc.code, exc.message_vi)})
        return base
    base.update({
        "family": rep.family,
        "num_glyphs": int(rep.num_glyphs),
        "covered": int(rep.covered),
        "missing_codepoints": [int(c) for c in rep.missing],
        "missing": _missing_display([int(c) for c in rep.missing]),
        "missing_total": len(rep.missing),
        "status": "full" if rep.is_full else "partial",
        "reason": rep.summary_vi(),
    })
    return base


def _scan_unity_bytes(data: bytes, display: str, out: dict) -> None:
    """Quét 1 blob Unity: TMP_FontAsset + Font object nhúng (class 128)."""
    from sdk import unity as uni

    if len(data) > MAX_FILE_BYTES:
        out["warnings"].append(
            "%s: %d byte vượt giới hạn %d — bỏ qua (bảo vệ RAM tab)"
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
                try:
                    inner = uni.parse_serialized(
                        payload, where=display + "::" + entry.name)
                except PB3Error as exc:
                    out["warnings"].append(
                        "%s::%s: không đọc được SerializedFile (%s)"
                        % (display, entry.name, getattr(exc, "code", "?")))
                    continue
                _scan_serialized(inner, display + "::" + entry.name, out)
                out["bundle_nodes"] += 1
            out["files_scanned"].append(display)
            return
        sf = uni.parse_serialized(data, where=display)
    except PB3Error as exc:
        out["warnings"].append("%s: không đọc được Unity (%s)"
                               % (display, getattr(exc, "code", "?")))
        return
    _scan_serialized(sf, display, out)
    out["files_scanned"].append(display)


def _scan_serialized(sf, source: str, out: dict) -> None:
    """Quét 1 SerializedFile: TMP items + Font object (class 128)."""
    scanned = tf.scan_serialized_file(sf, source)
    out["tmp_items"].extend(scanned["items"])
    out["tmp_candidates"].extend(scanned["candidates"])
    out["warnings"].extend(scanned["warnings"])
    for item in scanned["items"]:
        out["fonts"].append(_tmp_entry(item))
    for path_id, name in font_sdk.list_unity_font_objects(sf):
        out["fonts"].append({
            "kind": "unity_font",
            "name": name or ("Font #%s" % path_id),
            "family": name or "",
            "source": source,
            "size": None,
            "path_id": path_id,
            "num_glyphs": None,
            "covered": None,
            "total": 142,
            "missing_codepoints": [],
            "missing": [],
            "missing_total": 0,
            "status": "unreadable",
            "reason": "Font nhúng trong SerializedFile (Font object, class "
                      "128): chỉ liệt kê được tên/path_id, không đọc được "
                      "bytes font nên không kiểm được độ phủ.",
        })


def _is_unity_entry(name: str) -> bool:
    low = name.lower()
    if low.endswith((".assets", ".unity3d", ".bundle")):
        return True
    # level/sharedassets KHÔNG đuôi: chỉ nhận đúng tên trần (level0,
    # sharedassets0), KHÔNG nhận sharedassets0.assets.resS hay
    # sharedassets0.resource (đó là dữ liệu thô kèm theo, không phải
    # SerializedFile — nhận nhầm chỉ thêm cảnh báo nhiễu).
    base = os.path.basename(low)
    for prefix in ("level", "sharedassets"):
        if base.startswith(prefix):
            rest = base[len(prefix):]
            if rest.isdigit():
                return True
    return False


def _is_font_entry(name: str) -> bool:
    return name.lower().endswith((".ttf", ".otf", ".ttc"))


def _summarize(fonts: List[dict]) -> dict:
    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for fnt in fonts:
        by_kind[fnt["kind"]] = by_kind.get(fnt["kind"], 0) + 1
        by_status[fnt["status"]] = by_status.get(fnt["status"], 0) + 1
    return {
        "fonts": len(fonts),
        "by_kind": by_kind,
        "by_status": by_status,
        "full": by_status.get("full", 0),
        "partial": by_status.get("partial", 0),
        "unsupported": by_status.get("unsupported", 0),
        "unreadable": by_status.get("unreadable", 0),
    }


def scan_source(path: str) -> dict:
    """Liệt kê MỌI font trong 1 nguồn game (chỉ đọc).

    Nguồn: thư mục, file .ttf/.otf/.ttc rời, .assets/.unity3d/bundle,
    ZIP/IPA. Trả {schema, source, fonts, summary, warnings,
    files_scanned, bundle_nodes, tmp_items, tmp_candidates, glyph_plan,
    font_bytes}: `font_bytes` là {id_font: bytes} CHỈ cho font TTF rời
    (để bridge gộp thật) — phải lược bỏ trước khi dump JSON.
    """
    from sdk import containers as cont

    src = str(path)
    if not os.path.exists(src):
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không có file/thư mục: %s" % src, where=src)
    out: dict = {"schema": SCHEMA_LIST, "source": src, "fonts": [],
                 "warnings": [], "files_scanned": [], "bundle_nodes": 0,
                 "tmp_items": [], "tmp_candidates": [],
                 "glyph_plan": None, "font_bytes": {}}
    targets = []  # (display, kind, data|None)
    if os.path.isdir(src):
        for dirpath, _dirs, files in os.walk(src):
            for fn in sorted(files):
                full = os.path.join(dirpath, fn)
                if _is_font_entry(fn):
                    targets.append((full, "file", None))
                elif _is_unity_entry(fn):
                    targets.append((full, "unity", None))
    elif os.path.isfile(src):
        if zipfile.is_zipfile(src):
            vfs = cont.open_vfs(src)
            for name in vfs.iter_entries():
                if _is_font_entry(name):
                    targets.append((name, "zip-entry", None))
                elif _is_unity_entry(name):
                    targets.append((name, "unity-zip", None))
            for i, (display, kind, _d) in enumerate(list(targets)):
                try:
                    data = vfs.read(display)
                except PB3Error as exc:
                    out["warnings"].append(
                        "%s: không đọc được (%s)"
                        % (display, getattr(exc, "code", "?")))
                    targets[i] = (display, "skip", None)
                    continue
                if len(data) > MAX_ENTRY_BYTES and kind in ("zip-entry",):
                    out["warnings"].append(
                        "%s: quá lớn (%d byte), bỏ qua"
                        % (display, len(data)))
                    targets[i] = (display, "skip", None)
                    continue
                targets[i] = (display, kind, data)
        elif _is_font_entry(src):
            targets.append((src, "file", None))
        elif _is_unity_entry(src):
            targets.append((src, "unity", None))
        else:
            raise PB3Error(
                "E_INPUT_NOT_FOUND",
                "không nhận diện được nguồn font: %s (cần .ttf/.otf/.ttc, "
                "thư mục, ZIP/IPA, .assets/.unity3d)" % src, where=src)
    for display, kind, data in targets:
        if len(out["fonts"]) >= MAX_FONTS:
            out["warnings"].append(
                "Quá %d font, dừng quét" % MAX_FONTS)
            break
        if kind == "skip":
            continue
        try:
            if data is None:
                with open(display, "rb") as fh:
                    data = fh.read()
            if kind in ("unity", "unity-zip"):
                _scan_unity_bytes(data, display, out)
            else:
                entry = _standalone_entry(data, display, display)
                if entry is not None:
                    entry["id"] = len(out["fonts"])
                    out["fonts"].append(entry)
                    if entry["kind"] == "ttf" and entry["status"] == "partial":
                        out["font_bytes"][entry["id"]] = data
        except PB3Error as exc:
            out["warnings"].append("%s: %s"
                                  % (display, getattr(exc, "code", "?")))
    for i, fnt in enumerate(out["fonts"]):
        fnt.setdefault("id", i)
    out["glyph_plan"] = tf.build_glyph_plan(out["tmp_items"])
    out["summary"] = _summarize(out["fonts"])
    return out


# ------------------------------------------------------------------ gom 1 font
def _merged_filename(name: str) -> str:
    base = os.path.splitext(os.path.basename(name or "font.ttf"))[0]
    return "%s.vi.ttf" % (base or "font")


def build_unify_plan(scan: dict, donor_covered: int = 142) -> dict:
    """Kế hoạch "dùng chung 1 font" từ kết quả scan_source.

    Mỗi font có đúng 1 hành động: merge_glyphs (TTF rời, làm được thật
    trên web) | tmp_fallback (TMP, kế hoạch C6 + script Editor) |
    replace_with_donor (OTF/TTC/nhúng — thay trong Editor) |
    none_full (đã đủ 142/142) | unreadable (không đọc được nên không
    biết thiếu gì). Không bịa hành động cho font không đọc được.
    """
    fonts = scan.get("fonts") or []
    tmp_items = scan.get("tmp_items") or []
    glyph_plan = scan.get("glyph_plan") or tf.build_glyph_plan(tmp_items)
    fallback_plan = None
    fallback_by_path: Dict[int, dict] = {}
    if tmp_items:
        fallback_plan = tf.build_fallback_plan(tmp_items, glyph_plan)
        for fb in fallback_plan.get("fallbacks") or []:
            fallback_by_path[fb.get("path_id")] = fb
    entries = []
    counts = {"merge_glyphs": 0, "tmp_fallback": 0, "replace_with_donor": 0,
              "none_full": 0, "unreadable": 0}
    missing_all = set()
    for fnt in fonts:
        missing_all.update(fnt.get("missing_codepoints") or [])
        kind = fnt["kind"]
        status = fnt["status"]
        entry = {
            "id": fnt.get("id"),
            "kind": kind,
            "name": fnt.get("name"),
            "source": fnt.get("source"),
            "path_id": fnt.get("path_id"),
            "covered": fnt.get("covered"),
            "total": 142,
            "missing_total": fnt.get("missing_total", 0),
            "status": status,
            "action": "unreadable",
            "detail": "",
        }
        if status == "full":
            entry.update({
                "action": "none_full",
                "detail": "Đã đủ 142/142 — không cần làm gì, giữ nguyên.",
            })
        elif kind == "ttf" and status == "partial":
            entry.update({
                "action": "merge_glyphs",
                "detail": "Gộp %d ký tự còn thiếu từ %s vào font này ngay "
                          "trên web → file %s trong font_unified.zip."
                          % (fnt.get("missing_total", 0), DONOR_NAME,
                             _merged_filename(fnt.get("name"))),
                "output": _merged_filename(fnt.get("name")),
            })
        elif kind == "tmp_font_asset" and status == "partial":
            fb = fallback_by_path.get(fnt.get("path_id"))
            entry.update({
                "action": "tmp_fallback",
                "detail": "Tạo TMP_FontAsset fallback mới chỉ chứa %d ký "
                          "tự Việt thiếu rồi đăng ký vào TMP_Settings — "
                          "kế hoạch trong tmp_fallback_plan.json, atlas "
                          "SDF chỉ sinh được trong Unity Editor (script "
                          "TMP_VietnameseFallback.cs)."
                          % fnt.get("missing_total", 0),
                "fallback_name": (fb or {}).get("fallback_name"),
                "fallback_asset_path": (fb or {}).get("fallback_asset_path"),
            })
        elif kind in ("otf", "ttc", "unity_font") and status in ("partial", "unreadable"):
            why = {
                "otf": "OTF/CFF chỉ đọc được cmap, không gộp được glyph "
                       "trên web.",
                "ttc": "Font collection (.ttc) chưa hỗ trợ gộp.",
                "unity_font": "Font nhúng trong SerializedFile không đọc "
                             "được bytes, không sửa được object.",
            }[kind]
            entry.update({
                "action": "replace_with_donor",
                "detail": "%s Thay bằng %s (%s, đủ 142/142, kèm trong "
                          "font_unified.zip) rồi trỏ lại tham chiếu font "
                          "trong Unity Editor." % (why, DONOR_NAME, DONOR_FILE),
            })
        elif status == "unsupported":
            entry.update({
                "action": "replace_with_donor",
                "detail": "Chưa hỗ trợ kiểm/gộp loại này — thay bằng %s "
                          "(đủ 142/142, kèm trong font_unified.zip) rồi "
                          "trỏ lại tham chiếu trong Unity Editor."
                          % DONOR_NAME,
            })
        else:
            entry.update({
                "action": "unreadable",
                "detail": "Không đọc được font nên không biết thiếu chữ "
                          "nào — %s" % (fnt.get("reason") or "cần đối chiếu "
                          "thủ công trong Unity Editor."),
            })
        counts[entry["action"]] = counts.get(entry["action"], 0) + 1
        entries.append(entry)
    return {
        "schema": SCHEMA_PLAN,
        "donor": {"name": DONOR_NAME, "file": DONOR_FILE,
                  "covered": donor_covered, "total": 142,
                  "note": "Font Be Vietnam Pro đi kèm PB3 (OFL), đủ "
                          "142/142 — mọi font game được gom về font này."},
        "fonts": entries,
        "counts": counts,
        "missing_total_union": len(missing_all),
        "missing_union": [_hex(c) for c in sorted(missing_all)[:MAX_LISTED_MISSING]],
        "chars_text": tf.vietnamese_chars_text(sorted(missing_all)),
        "fallback_plan": fallback_plan,
        "limits": list(LIMITS),
        "editor_steps": list(EDITOR_STEPS),
    }


def merge_standalone_ttfs(scan: dict, donor_bytes: bytes) -> List[dict]:
    """Gộp thật các font TTF rời còn thiếu với font donor.

    Trả [{font_id, name, output, data, added, already, glyphs_copied}].
    Chỉ TTF có outline glyf; OTF/TTC/TMP/nhúng không vào đây (xem plan).
    """
    from sdk import font as f

    out = []
    font_bytes = scan.get("font_bytes") or {}
    by_id = {fnt.get("id"): fnt for fnt in scan.get("fonts") or []}
    for fid, data in sorted(font_bytes.items()):
        fnt = by_id.get(fid)
        if fnt is None:
            continue
        res = f.merge_vietnamese(data, donor_bytes,
                                 where=fnt.get("name") or "font")
        out.append({
            "font_id": fid,
            "name": fnt.get("name"),
            "source": fnt.get("source"),
            "output": _merged_filename(fnt.get("name")),
            "data": res.data,
            "added": len(res.added),
            "already": len(res.already),
            "donor_missing": len(res.donor_missing),
            "glyphs_copied": int(res.glyphs_copied),
            "warnings": list(res.warnings or []),
        })
    return out
