"""Cong kiem tra truoc khi giao (buoc 6, P0-2) — MOT cong duy nhat.

Gop cac manh kiem tra roi rac hien co thanh 1 verdict PASS/FAIL duy nhat
truoc khi giao file:
  (a) self-check ban dich (bridge._self_check + sdk/translate): moi key
      co ban dich, khong key thieu/trong ngoai danh sach cho phep;
  (b) bao toan placeholder/the dinh dang/newline tung key (mo rong
      sdk/exporters.placeholders + sdk/translate.placeholders_ok: so
      theo THU TU + SO LAN XUAT HIEN, chat hon placeholders_ok von chi
      so danh sach khong trung — mat 1 ban sao {0} cung FAIL);
  (c) diffverify: file da ep trich lai phai khop ban dich, theo dung
      duong (E) (nhom theo provenance -> plan moi don vi -> executor
      hien co), doc lai TU BYTES da ep, khong tin bo nho trong cua
      executor; nhom unmatched/monobehaviour bao ro la chua ep;
  (d) font: moi ky tu dung trong ban dich phai nam trong vung phu cua
      font se hien thi (bo 142 muc cua sdk/font), bao cao theo tung font;
  (e) toan ven: hash/cau truc file da ep khop ke hoach ep (don vi,
      entry, sha), khong ghi de file goc.

QUY TAC CHAN (P0-2, 1 dong ly do): FAIL thi CHAN giao tuyet doi, khong
co duong giao khi canh bao — vi file giao sai (thieu key, mat
placeholder, font thieu chu) hong game ma nguoi dung kho phat hien.
Khong co verdict thi khong giao file. Canh bao (warning) khong chan,
nhung luon hien ro; khong co trang thai "skip": kiem tra nao khong
chay duoc thi FAIL kem ly do (fail-closed).

Che do:
  - pre_write (khong co pressed_bytes): mo phong ep trong RAM theo
    dung duong (E) roi doc lai bytes da mo phong de diffverify. Khong
    ghi file nao.
  - pressed (co pressed_bytes): doc lai file da ep that (1 don vi, hoac
    ZIP cac don vi dang rebuild.zip, hoac container chua don vi o dung
    vpath) roi doi chieu voi ke hoach ep tu file goc.

Chi thu vien chuan, Python >= 3.9. Khong goi mang. Khong ghi file nao
(khong doc/ghi file he thong): moi dau vao la bytes/dict, moi dau ra
la dict. Khong dung thu vien Unity ben ngoai tren web. Duong RAM (nhu
preview (E)); bundle doc lap >= 64MB khi ghi dung duong streaming cua
(E), cong kiem lai file da ep o che do pressed thay vi mo phong.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from core.errors import PB3Error
from core.limits import Limits
from sdk import font as _font
from sdk import plan as _plan
from sdk import rebuild as _rb
from sdk import unity as _u

GATE_VERSION = 1

CHECK_TRANSLATION = "translation_complete"
CHECK_PLACEHOLDERS = "placeholders"
CHECK_DIFFVERIFY = "diffverify"
CHECK_FONTS = "fonts"
CHECK_INTEGRITY = "integrity"
CHECK_IDS = (CHECK_TRANSLATION, CHECK_PLACEHOLDERS, CHECK_DIFFVERIFY,
             CHECK_FONTS, CHECK_INTEGRITY)

_DETAIL_CAP = 50
_UNMATCHED_CAP = 200
_FONT_MISSING_CAP = 50
_ZIP_PRESSED_MAX_ENTRIES = 64

# Placeholder/the dinh dang: {0}, {name}, %s/%d/%i/%f/%1$s, $var,
# the <...>, escape \n \t \\ \' \" — gop mau cua sdk/exporters va
# sdk/plan, so theo thu tu xuat hien (finditer) de bat ca mat ban sao.
_PLACEHOLDER_RE = re.compile(
    r"\{[^}]*\}|\%(\d+\$)?[sdif]|\$[A-Za-z_][A-Za-z0-9_]*|"
    r"<[A-Za-z][^>]*>|\\[nrt\\'\"]")

_REASON_VI = {
    "monobehaviour": "MonoBehaviour/StringTable — (E) v1 chỉ ghi TextAsset JSON, chưa ghi được nhóm này",
    "not_found": "không tìm thấy trong đơn vị nào",
    "not_json_dict": "TextAsset không phải JSON dạng dict",
    "non_string_value": "giá trị gốc không phải chuỗi",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_translations_sha(translations: Dict[str, str]) -> str:
    """SHA-256 cua ban dich o dang chuan (JSON sort_keys) — de bridge
    doi chieu verdict cua cong voi ban dich that khi ghi (E)."""
    raw = json.dumps(translations, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return _sha(raw)


def _placeholders_in_order(text: str) -> List[str]:
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(text or "")]


def _validate_kv(mapping: Any, label: str) -> Dict[str, str]:
    if not isinstance(mapping, dict):
        raise ValueError("%s phải là JSON object (key -> chuỗi)." % label)
    for k, v in mapping.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("%s: key và giá trị đều phải là chuỗi." % label)
    return mapping


def _normalize_allowlist(allowlist: Any) -> Dict[str, Set[str]]:
    """Danh sach cho phep: moi muc la list key (chuoi). Tra dict cac
    set; muc la/bi thieu -> set rong. Sai kieu -> ValueError."""
    out = {"allowed_empty": set(), "allowed_missing": set(),
           "allowed_extra": set(), "allowed_unpressed": set(),
           "allowed_placeholders": set()}
    if allowlist is None:
        return out
    if not isinstance(allowlist, dict):
        raise ValueError("allowlist phải là JSON object.")
    for name in out:
        val = allowlist.get(name)
        if val is None:
            continue
        if not isinstance(val, list):
            raise ValueError("allowlist.%s phải là list key." % name)
        for item in val:
            if not isinstance(item, str):
                raise ValueError("allowlist.%s chứa key không phải chuỗi."
                                 % name)
            out[name].add(item)
    return out


def _check(check_id: str, name: str, status: str, summary: str,
           failures: List[Dict[str, Any]], warnings: List[str],
           counts: Dict[str, Any],
           **extra: Any) -> Dict[str, Any]:
    res = {"id": check_id, "name": name, "status": status,
           "summary_vi": summary, "failures": failures,
           "warnings": warnings, "counts": counts}
    res.update(extra)
    return res


# ---------------------------------------------------------- (a) day du
def check_translation_completeness(
        source: Optional[Dict[str, str]],
        translations: Dict[str, str], *,
        allowed_empty: Any = (), allowed_missing: Any = (),
        allowed_extra: Any = (), allow_partial: bool = False
) -> Dict[str, Any]:
    """(a) Self-check ban dich: moi key nguon deu co ban dich, khong
    key trong; key thua (khong co trong ban goc) cung bao. Thieu ban
    goc (source=None) thi FAIL (fail-closed). allow_partial=True thi
    key thieu chi dem la 'giu nguyen', khong FAIL — lua chon khai bao
    ro, ghi trong ket qua."""
    _validate_kv(translations, "translations")
    if source is None:
        return _check(
            CHECK_TRANSLATION, "Bản dịch đầy đủ", "fail",
            "Chưa có bản gốc để đối chiếu — không kiểm được key thiếu/trống.",
            [{"kind": "no_source",
              "message_vi": "Chưa có bản gốc (file JSON đã trích xuất) để đối chiếu với bản dịch. "
                            "Hãy mở file trích xuất gốc ở tab Mod hoặc nhập file .json bản gốc rồi kiểm lại."}],
            [], {"source_keys": 0, "translation_keys": len(translations),
                 "missing": 0, "empty": 0, "extra": 0, "unchanged": 0})
    _validate_kv(source, "source")
    allow_empty = set(allowed_empty)
    allow_missing = set(allowed_missing)
    allow_extra = set(allowed_extra)
    missing = [k for k in source if k not in translations]
    missing_bad = [k for k in missing if k not in allow_missing]
    if allow_partial:
        missing_bad = []
    empty_bad = []
    empty_excused = []
    for key, value in translations.items():
        if value.strip() != "":
            continue
        if key in source and source[key] == "":
            continue  # ban goc rong, ban dich rong theo — trung thuc
        if key in allow_empty:
            empty_excused.append(key)
        else:
            empty_bad.append(key)
    extra = [k for k in translations if k not in source]
    extra_bad = [k for k in extra if k not in allow_extra]
    unchanged = [k for k in translations
                 if k in source and translations[k] == source[k]
                 and source[k] != ""]
    failures: List[Dict[str, Any]] = []
    for key in missing_bad:
        failures.append({"key": key, "kind": "missing",
                         "message_vi": "Thiếu key %r: có trong bản gốc nhưng không có trong bản dịch." % key})
    for key in empty_bad:
        failures.append({"key": key, "kind": "empty",
                         "message_vi": "Key %r có bản dịch rỗng (bản gốc: %r)." % (key, source.get(key, ""))})
    for key in extra_bad:
        failures.append({"key": key, "kind": "extra",
                         "message_vi": "Key lạ %r: có trong bản dịch nhưng không có trong bản gốc (gõ nhầm key?)." % key})
    failures_total = len(failures)
    failures = failures[:_DETAIL_CAP]
    warnings: List[str] = []
    if unchanged:
        warnings.append("%d key giữ nguyên như bản gốc (chưa dịch hoặc tên/số không cần dịch): %s%s"
                        % (len(unchanged), ", ".join(unchanged[:5]),
                           "…" if len(unchanged) > 5 else ""))
    if allow_partial and missing:
        warnings.append("Bản dịch từng phần (đã khai báo): %d key không có trong bản dịch sẽ giữ nguyên tiếng gốc."
                        % len(missing))
    status = "fail" if failures_total else "pass"
    if status == "pass":
        summary = ("%d/%d key có bản dịch, không key trống/thừa."
                   % (len(translations) - len(extra), len(source)))
        if allow_partial and missing:
            summary += " (%d key giữ nguyên theo chế độ từng phần.)" % len(missing)
    else:
        bits = []
        if missing_bad:
            bits.append("%d key thiếu" % len(missing_bad))
        if empty_bad:
            bits.append("%d key trống" % len(empty_bad))
        if extra_bad:
            bits.append("%d key lạ" % len(extra_bad))
        summary = "Bản dịch chưa đầy đủ: " + ", ".join(bits) + "."
    return _check(
        CHECK_TRANSLATION, "Bản dịch đầy đủ", status, summary,
        failures, warnings,
        {"source_keys": len(source), "translation_keys": len(translations),
         "missing": len(missing), "missing_bad": len(missing_bad),
         "missing_excused": len(missing) - len(missing_bad) if not allow_partial else len(missing),
         "empty": len(empty_bad) + len(empty_excused),
         "empty_bad": len(empty_bad), "empty_excused": len(empty_excused),
         "extra": len(extra), "extra_bad": len(extra_bad),
         "extra_excused": len(extra) - len(extra_bad),
         "unchanged": len(unchanged), "failures_total": failures_total,
         "allow_partial": bool(allow_partial)},
        missing_keys=missing_bad[:_DETAIL_CAP],
        empty_keys=empty_bad[:_DETAIL_CAP],
        extra_keys=extra_bad[:_DETAIL_CAP],
        unchanged_keys=unchanged[:_DETAIL_CAP])


# ------------------------------------------------------ (b) placeholder
def check_placeholders(source: Optional[Dict[str, str]],
                       translations: Dict[str, str], *,
                       allowed: Any = ()) -> Dict[str, Any]:
    """(b) Bao toan placeholder/the/newline tung key so voi ban goc.
    So danh sach placeholder theo thu tu + so lan xuat hien, va so
    ky tu xuong dong. Thieu ban goc thi FAIL (fail-closed)."""
    _validate_kv(translations, "translations")
    if source is None:
        return _check(
            CHECK_PLACEHOLDERS, "Placeholder và định dạng", "fail",
            "Chưa có bản gốc để đối chiếu placeholder.",
            [{"kind": "no_source",
              "message_vi": "Chưa có bản gốc để đối chiếu placeholder/thẻ/xuống dòng. "
                            "Hãy cung cấp file JSON bản gốc rồi kiểm lại."}],
            [], {"checked": 0, "failed": 0, "allowed_skipped": 0})
    _validate_kv(source, "source")
    allow = set(allowed)
    failures: List[Dict[str, Any]] = []
    checked = 0
    skipped = 0
    for key in translations:
        if key not in source:
            continue
        if key in allow:
            skipped += 1
            continue
        checked += 1
        src = source[key]
        got = translations[key]
        want_ph = _placeholders_in_order(src)
        got_ph = _placeholders_in_order(got)
        want_nl = src.count("\n")
        got_nl = got.count("\n")
        if want_ph == got_ph and want_nl == got_nl:
            continue
        bits = []
        if want_ph != got_ph:
            bits.append("placeholder đổi %s → %s" % (want_ph, got_ph))
        if want_nl != got_nl:
            bits.append("số xuống dòng đổi %d → %d" % (want_nl, got_nl))
        failures.append({
            "key": key,
            "source_placeholders": want_ph,
            "translated_placeholders": got_ph,
            "source_newlines": want_nl,
            "translated_newlines": got_nl,
            "message_vi": "Key %r: %s." % (key, "; ".join(bits))})
    failures_total = len(failures)
    failures = failures[:_DETAIL_CAP]
    status = "fail" if failures_total else "pass"
    summary = ("Placeholder/thẻ/xuống dòng giữ nguyên ở %d key."
               % checked) if status == "pass" else (
        "%d/%d key lệch placeholder/thẻ/xuống dòng so với bản gốc."
        % (failures_total, checked))
    return _check(
        CHECK_PLACEHOLDERS, "Placeholder và định dạng", status, summary,
        failures, [],
        {"checked": checked, "failed": failures_total,
         "allowed_skipped": skipped, "failures_total": failures_total})


# ------------------------------------------------------------- (d) font
def font_entry_from_bytes(data: bytes, name: str,
                          source: str = "font do người dùng chỉ định"
                          ) -> Dict[str, Any]:
    """1 font TTF/OTF (bytes) -> muc bao cao dang font_list (co
    missing_codepoints day du de cong tinh missing_used). Font hong
    thi raise PB3Error (bridge bat roi chuyen thanh loi cua kiem tra
    font, khong crash cong)."""
    rep = _font.coverage(data, where=name)
    return {
        "kind": rep.kind, "name": name, "family": rep.family,
        "source": source, "size": len(data), "path_id": None,
        "num_glyphs": int(rep.num_glyphs), "covered": int(rep.covered),
        "total": int(rep.total),
        "missing_codepoints": [int(c) for c in rep.missing],
        "missing": ["U+%04X" % c for c in rep.missing[:_FONT_MISSING_CAP]],
        "missing_total": len(rep.missing),
        "status": "full" if rep.is_full else "partial",
        "reason": rep.summary_vi(),
    }


def _normalize_font_entry(entry: Dict[str, Any],
                          supplied: bool) -> Dict[str, Any]:
    missing = [int(c) for c in (entry.get("missing_codepoints") or [])]
    status = entry.get("status") or "unreadable"
    covered = entry.get("covered")
    readable = status in ("full", "partial") and covered is not None
    return {
        "name": entry.get("name") or entry.get("family") or "?",
        "kind": entry.get("kind") or "?",
        "family": entry.get("family") or "",
        "source": entry.get("source") or "",
        "status": status, "covered": covered,
        "total": entry.get("total") or 142,
        "missing_codepoints": missing,
        "missing_total": len(missing),
        "readable": readable, "supplied": supplied,
        "reason": entry.get("reason") or "",
        "num_glyphs": entry.get("num_glyphs"),
    }


def check_font_coverage(translations: Dict[str, str],
                        fonts: Optional[List[Dict[str, Any]]], *,
                        display_fonts: Any = (),
                        font_error: Optional[str] = None
                        ) -> Dict[str, Any]:
    """(d) Moi ky tu tieng Viet DUNG trong ban dich phai nam trong vung
    phu cua font se hien thi (bo 142 muc). Bao cao theo tung font.

    Quy tac (ghi ro, ap dung nhat quan):
    - Neu co font hien thi do nguoi dung chi dinh (display_fonts): chi
      cac font do quyet dinh — thieu chu nao dang dung thi FAIL; font
      quet trong game chi de tham khao (canh bao neu thieu).
    - Neu khong: MOI font doc duoc trong game phai phu het chu dang
      dung (cong khong biet chu nao hien bang font nao); khong co font
      nao doc duoc thi FAIL "chua kiem duoc" (fail-closed).
    - Font khong doc duoc bytes (font nhung Unity, TTC): liet ke ro
      la "chua kiem duoc", dem vao canh bao, khong tu cho la dat hay
      hong — gioi han doc cua sdk/font (xem font.py).
    - Ban dich khong dung chu Viet nao trong bo 142 thi PASS (ghi ro).
    """
    _validate_kv(translations, "translations")
    used_set = set()
    for value in translations.values():
        for ch in value:
            cp = ord(ch)
            if cp in _font.REQUIRED_VIETNAMESE:
                used_set.add(cp)
    used = sorted(used_set)
    scanned = [_normalize_font_entry(f, False) for f in (fonts or [])]
    supplied = [_normalize_font_entry(f, True)
                for f in (display_fonts or [])]
    entries = supplied + scanned
    for ent in entries:
        missing_used = [cp for cp in ent["missing_codepoints"]
                        if cp in used_set]
        ent["missing_used"] = missing_used
        ent["missing_used_chars"] = [chr(cp) for cp in missing_used]
        ent["missing_used_total"] = len(missing_used)
        ent["missing_chars"] = [chr(cp) for cp in
                                ent["missing_codepoints"][:_FONT_MISSING_CAP]]
    failures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    readable = [e for e in scanned if e["readable"]]
    unreadable = [e for e in scanned if not e["readable"]]
    if used:
        if supplied:
            for ent in supplied:
                if ent["missing_used"]:
                    failures.append({
                        "font": ent["name"], "kind": "display_missing",
                        "missing_chars": ent["missing_used_chars"],
                        "message_vi": "Font hiển thị %r thiếu %d chữ dùng trong bản dịch: %s."
                                      % (ent["name"], len(ent["missing_used"]),
                                         " ".join(ent["missing_used_chars"]))})
            for ent in readable:
                if ent["missing_used"]:
                    warnings.append(
                        "Font trong game %r thiếu %d chữ đang dùng (%s) — cổng kiểm theo font hiển thị đã chỉ định, "
                        "chỉ ĐẠT nếu game thật sự dùng font đó."
                        % (ent["name"], len(ent["missing_used"]),
                           " ".join(ent["missing_used_chars"][:10])))
        elif not readable:
            if font_error:
                failures.append({"kind": "scan_error",
                                 "message_vi": "Không quét được font trong file gốc: %s" % font_error})
            elif not scanned:
                failures.append({"kind": "no_fonts",
                                 "message_vi": "Không tìm thấy font nào trong file gốc để kiểm độ phủ — chưa kiểm được chữ tiếng Việt có hiển thị đúng không."})
            else:
                failures.append({"kind": "no_readable_fonts",
                                 "message_vi": "Không đọc được độ phủ của font nào trong %d font của game (font nhúng trong Unity không đọc được bytes) — chưa kiểm được chữ tiếng Việt có hiển thị đúng không. Hãy chỉ định font sẽ hiển thị (vd font Be Vietnam Pro đi kèm) để kiểm." % len(scanned)})
        else:
            for ent in readable:
                if ent["missing_used"]:
                    failures.append({
                        "font": ent["name"], "kind": "font_missing",
                        "missing_chars": ent["missing_used_chars"],
                        "message_vi": "Font %r thiếu %d chữ dùng trong bản dịch: %s."
                                      % (ent["name"], len(ent["missing_used"]),
                                         " ".join(ent["missing_used_chars"]))})
    for ent in unreadable:
        warnings.append("Font %r (%s): chưa kiểm được độ phủ — %s"
                        % (ent["name"], ent["kind"],
                           ent["reason"] or "không đọc được bytes font."))
    status = "fail" if failures else "pass"
    if not used:
        summary = "Bản dịch không dùng chữ Việt nào trong bộ 142 mục — không cần kiểm độ phủ."
    elif status == "pass":
        if supplied:
            summary = "Font hiển thị đã chỉ định phủ đủ %d chữ Việt đang dùng." % len(used)
        else:
            summary = "%d font đọc được đều phủ đủ %d chữ Việt đang dùng." % (len(readable), len(used))
    else:
        summary = "Font thiếu chữ dùng trong bản dịch hoặc chưa kiểm được độ phủ."
    if unreadable and status == "pass":
        summary += " (%d font chưa kiểm được.)" % len(unreadable)
    report_fonts = []
    for ent in entries:
        report_fonts.append({
            "name": ent["name"], "kind": ent["kind"],
            "family": ent["family"], "source": ent["source"],
            "status": ent["status"], "covered": ent["covered"],
            "total": ent["total"], "missing_total": ent["missing_total"],
            "missing_chars": ent["missing_chars"],
            "missing_used_chars": ent["missing_used_chars"],
            "missing_used_total": ent["missing_used_total"],
            "supplied": ent["supplied"], "reason": ent["reason"],
            "num_glyphs": ent["num_glyphs"],
        })
    return _check(
        CHECK_FONTS, "Font hiển thị", status, summary,
        failures, warnings,
        {"fonts": len(scanned), "readable": len(readable),
         "unreadable": len(unreadable), "display_fonts": len(supplied),
         "used_vietnamese": len(used)},
        fonts=report_fonts, used_chars=[chr(cp) for cp in used])


# ------------------------------------------- (c)+(e) ke hoach + diffverify
def _err_text(exc: Exception) -> str:
    text = str(exc)
    detail = getattr(exc, "detail", "") or ""
    return (text + (" (%s)" % detail if detail else "")) or exc.__class__.__name__


def _build_plans(source_bytes: bytes, source_name: str,
                 translations: Dict[str, str], provenance: Any,
                 choices: Any, be: Any, lim: Limits,
                 source_locale: str = "en"
                 ) -> Dict[str, Any]:
    """Lap plan moi don vi theo DUNG duong (E): group_by_unit ->
    _expand_group_units -> build_plan -> _apply_unit_plan (executor
    hien co), roi phan loai key thua nhu apply_rebuild (gom SF cua
    tat ca don vi voi file ZIP de join StringTable). Tra plans (kem
    bytes da mo phong), refused, unmatched, unchanged. Loi tung don
    vi vao refused, khong fail ca dot (nhu (E)). source_locale: cot
    ngon ngu goc de ghi vao MonoBehaviour (mac dinh "en")."""
    grouped = _rb.group_by_unit(translations, provenance, choices)
    groups = grouped["groups"]
    refused: List[Dict[str, str]] = []
    unit_plans: List[Tuple[Any, Any, Dict[str, Any]]] = []
    planned_keys: Set[str] = set()
    sfs_by_unit: Dict[str, Any] = {}
    total_changes = 0
    # M1 (G1): mb_index nhu apply_rebuild (SharedTableData co the nam o
    # bundle khac StringTable) — quet truoc khi lap plan.
    mb_index: Dict[str, Dict[str, int]] = {}
    try:
        for (vpath, _kind) in _rb._enumerate_units(source_bytes, be, lim):
            key = vpath or "<file>"
            if key not in sfs_by_unit:
                try:
                    sfs_by_unit[key] = _rb._unit_sfs(
                        source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
    except PB3Error:
        pass
    try:
        mb_index = _plan.build_mb_shared_index(
            [sf for lst in sfs_by_unit.values() for sf in lst])
    except Exception:
        mb_index = {}
    for group_unit in sorted(groups, key=lambda g: "" if g is None else str(g)):
        grp = groups[group_unit]
        try:
            unit_list = _rb._expand_group_units(
                source_bytes, group_unit, be, lim, source_name=source_name,
                refused=refused)
        except PB3Error as exc:
            refused.append({"unit": group_unit or "<quét tất cả>",
                            "code": exc.code, "message": _err_text(exc)})
            continue
        for (vpath, _kind) in unit_list:
            if (vpath or "<file>") not in sfs_by_unit:
                try:
                    sfs_by_unit[vpath or "<file>"] = _rb._unit_sfs(
                        source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
            try:
                plan = _plan.build_plan(
                    source_bytes, grp["translations"],
                    source_bundle=source_name, entry=vpath,
                    mode="REBUILD_SECTION", backend=be, limits=lim,
                    source_locale=source_locale, mb_index=mb_index)
            except ValueError:
                continue
            except PB3Error as exc:
                refused.append({"unit": vpath or group_unit or "<file>",
                                "code": exc.code,
                                "message": _err_text(exc)})
                continue
            unit_plans.append((group_unit, vpath, plan))
            total_changes += sum(len(e["changes"]) for e in plan["entries"])
            for e in plan["entries"]:
                for c in e["changes"]:
                    planned_keys.add(c["key"])
    plans: List[Dict[str, Any]] = []
    for group_unit, vpath, plan in unit_plans:
        try:
            cur, kind, label, mode, size_before = _rb._apply_unit_plan(
                source_bytes, plan, be, lim)
        except PB3Error as exc:
            refused.append({"unit": vpath or group_unit or "<file>",
                            "code": exc.code, "message": _err_text(exc)})
            for e in plan["entries"]:
                for c in e["changes"]:
                    planned_keys.discard(c["key"])
            continue
        plans.append({"group_unit": group_unit, "vpath": vpath,
                      "plan": plan, "kind": kind, "label": label,
                      "mode": mode, "size_before": int(size_before),
                      "simulated": cur})
    if zipfile.is_zipfile(io.BytesIO(source_bytes)):
        try:
            for (vpath, _kind) in _rb._enumerate_units(source_bytes, be, lim):
                if (vpath or "<file>") in sfs_by_unit:
                    continue
                try:
                    sfs_by_unit[vpath or "<file>"] = _rb._unit_sfs(
                        source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
        except PB3Error:
            pass
    try:
        all_sfs = [sf for lst in sfs_by_unit.values() for sf in lst]
        mb_terms = _rb._collect_mb_keys(all_sfs)
    except Exception:
        all_sfs, mb_terms = [], set()
    leftover = [k for k in translations if k not in planned_keys]
    unmatched, unchanged = _rb._classify_unmatched(
        leftover, translations, all_sfs, mb_terms)
    for item in unmatched:
        item["unit"] = None
    for grp_unit, grp in groups.items():
        for item in unmatched:
            if item["key"] in grp["translations"]:
                item["unit"] = grp_unit
    return {"grouped": grouped, "plans": plans, "refused": refused,
            "unmatched": unmatched, "unmatched_total": len(unmatched),
            "unchanged": unchanged, "planned_keys": planned_keys,
            "total_changes": sum(len(e["changes"])
                                 for p in plans
                                 for e in p["plan"]["entries"])}


def _parse_unit(kind: str, unit_bytes: bytes, req_entry: Optional[str],
                be: Any, lim: Limits) -> Tuple[Any, Any]:
    """(bundle|None, SerializedFile) cua 1 don vi — doc lai TU BYTES."""
    if kind == "bundle":
        bundle = _u.parse_bundle(unit_bytes, be, lim, where="bundle")
        sf_bytes = bundle.get_entry(req_entry)
        sf = _u.parse_serialized(sf_bytes, lim, where=req_entry or "assets")
        return bundle, sf
    sf = _u.parse_serialized(unit_bytes, lim, where="assets")
    return None, sf


def _sf_has_assets(sf: Any, probes: List[Tuple[Any, ...]]) -> bool:
    """Don vi co chua cac asset cua plan? Probe (pid, asset_type, name)
    (dang cu 2-tuple (pid, name) van chap nhan, mac dinh TextAsset).
    TextAsset: tim dung ten; MonoBehaviour (M1): object phai ton tai,
    class 114, ten m_Name khop neu doc duoc."""
    for probe in probes:
        if len(probe) == 3:
            pid, asset_type, name = probe
        else:
            pid, name = probe
            asset_type = "TextAsset"
        if asset_type == "MonoBehaviour":
            try:
                o = sf.get_object(int(pid))
            except PB3Error:
                return False
            if o.class_id != 114:
                return False
            if name:
                try:
                    raw = sf.object_bytes(int(pid))
                    found_name = _u.read_monobehaviour_name(raw, sf.endian)
                except PB3Error:
                    found_name = None
                if found_name is not None and found_name != name:
                    return False
        else:
            try:
                _pid, found, _script = sf.find_text_asset(path_id=int(pid))
            except PB3Error:
                return False
            if found != name:
                return False
    return True


def _probes_for_plan(plan: Dict[str, Any]) -> List[Tuple[int, str, str]]:
    """Probe (pid, asset_type, name) cho moi entry cua plan."""
    return [(int(e["object"]), str(e.get("asset_type") or "TextAsset"),
             str(e["asset_path"]).rsplit("::", 1)[-1])
            for e in (plan.get("entries") or [])]


def _candidate_matches(candidate: bytes, req_entry: Optional[str],
                       probes: List[Tuple[int, str]], be: Any,
                       lim: Limits) -> bool:
    """File ung vien co phai la don vi chua cac TextAsset cua plan?"""
    try:
        if _u.detect_bundle(candidate) is not None:
            bundle = _u.parse_bundle(candidate, be, lim, where="bundle")
            names = bundle.entry_names()
            ordered = ([req_entry] if req_entry in names else []) + \
                [n for n in names if n != req_entry][:20]
            for entry_name in ordered:
                try:
                    sf = _u.parse_serialized(
                        bundle.get_entry(entry_name), lim, where=entry_name)
                except PB3Error:
                    continue
                if _sf_has_assets(sf, probes):
                    return True
            return False
        sf = _u.parse_serialized(candidate, lim, where="assets")
        return _sf_has_assets(sf, probes)
    except PB3Error:
        return False


def _find_pressed_unit(pressed_bytes: bytes, plan: Dict[str, Any],
                       be: Any, lim: Limits) -> Optional[bytes]:
    """Tim bytes don vi da ep ung voi 1 plan: (1) giai quyet dung
    vpath trong file da ep (container); (2) ban than file da ep la
    don vi; (3) entry trong ZIP cac don vi (dang rebuild.zip, <= 64
    entry — file IPA day du khong di duong nay)."""
    entries = plan.get("entries") or []
    if not entries:
        return None
    probes = _probes_for_plan(plan)
    req_entry = entries[0].get("entry")
    vpath = plan.get("unit_entry")
    try:
        kind, unit_bytes, req2, _label = _plan._resolve_unit(
            pressed_bytes, vpath, be, lim)
        if kind == "bundle":
            bundle = _u.parse_bundle(unit_bytes, be, lim, where="bundle")
            sf = _u.parse_serialized(bundle.get_entry(req2), lim,
                                     where=req2 or "assets")
        else:
            sf = _u.parse_serialized(unit_bytes, lim, where="assets")
        if _sf_has_assets(sf, probes):
            return bytes(unit_bytes)
    except (PB3Error, ValueError, KeyError, IndexError):
        pass
    if _candidate_matches(pressed_bytes, req_entry, probes, be, lim):
        return bytes(pressed_bytes)
    try:
        if zipfile.is_zipfile(io.BytesIO(pressed_bytes)):
            zf = zipfile.ZipFile(io.BytesIO(pressed_bytes))
            with zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) <= _ZIP_PRESSED_MAX_ENTRIES:
                    for info in infos:
                        if info.file_size > lim.max_entry_bytes:
                            continue
                        try:
                            data = zf.read(info.filename)
                        except (KeyError, RuntimeError, zipfile.BadZipFile):
                            continue
                        if _candidate_matches(data, req_entry, probes,
                                              be, lim):
                            return data
    except zipfile.BadZipFile:
        pass
    return None


def _verify_pressed_units(source_bytes: bytes, plan_items: List[Dict[str, Any]],
                          pressed_bytes: Optional[bytes], be: Any,
                          lim: Limits) -> Dict[str, Any]:
    """Diffverify doc lap: doc lai don vi da ep TU BYTES, doi chieu
    tung key voi plan, kiem key khac giu nguyen, sha script == after_sha,
    cau truc (thu tu entry bundle, co entry, danh muc object, bytes cac
    object khong doi) giu nguyen — tuong duong diffverify 6.7 cua
    apply_patch o muc object/entry (khong so byte-layout ngoai entry)."""
    units_out: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    structural: List[Dict[str, Any]] = []
    verified_keys: Set[str] = set()
    for item in plan_items:
        plan = item["plan"]
        vpath = item["vpath"]
        display = vpath or (item["group_unit"] or "<file>")
        unit_mismatch: List[Dict[str, Any]] = []
        try:
            kind, orig_bytes, req_entry, _label = _plan._resolve_unit(
                source_bytes, vpath, be, lim)
            orig_bundle, orig_sf = _parse_unit(kind, orig_bytes, req_entry,
                                               be, lim)
        except PB3Error as exc:
            unit_mismatch.append({"unit": display, "kind": "read_original",
                                  "message_vi": "Không đọc được đơn vị gốc %s: [%s] %s"
                                                % (display, exc.code, _err_text(exc))})
            mismatches.extend(unit_mismatch)
            units_out.append({"unit": display, "kind": item["kind"],
                              "entries": len(plan["entries"]),
                              "changes": sum(len(e["changes"]) for e in plan["entries"]),
                              "verified": False, "mismatches": unit_mismatch})
            continue
        if pressed_bytes is None:
            pressed_unit = item["simulated"]
            pressed_kind = kind
            pressed_req = req_entry
        else:
            pressed_unit = _find_pressed_unit(pressed_bytes, plan, be, lim)
            if pressed_unit is None:
                unit_mismatch.append({"unit": display, "kind": "unit_not_found",
                                      "message_vi": "Không tìm thấy đơn vị %s trong file đã ép." % display})
                mismatches.extend(unit_mismatch)
                units_out.append({"unit": display, "kind": item["kind"],
                                  "entries": len(plan["entries"]),
                                  "changes": sum(len(e["changes"]) for e in plan["entries"]),
                                  "verified": False, "mismatches": unit_mismatch})
                continue
            pressed_kind = "bundle" if _u.detect_bundle(pressed_unit) is not None else "assets"
            pressed_req = req_entry
            if pressed_kind == "bundle" and req_entry is not None:
                try:
                    _pb = _u.parse_bundle(pressed_unit, be, lim, where="bundle")
                    if req_entry not in _pb.entry_names():
                        pressed_req = None
                        for nm in _pb.entry_names()[:20]:
                            try:
                                _sf = _u.parse_serialized(_pb.get_entry(nm), lim, where=nm)
                            except PB3Error:
                                continue
                            probes = _probes_for_plan(plan)
                            if _sf_has_assets(_sf, probes):
                                pressed_req = nm
                                break
                except PB3Error:
                    pass
        try:
            pressed_bundle, pressed_sf = _parse_unit(
                pressed_kind, pressed_unit, pressed_req, be, lim)
        except PB3Error as exc:
            unit_mismatch.append({"unit": display, "kind": "read_pressed",
                                  "message_vi": "Không đọc được đơn vị đã ép %s: [%s] %s"
                                                % (display, exc.code, _err_text(exc))})
            mismatches.extend(unit_mismatch)
            units_out.append({"unit": display, "kind": item["kind"],
                              "entries": len(plan["entries"]),
                              "changes": sum(len(e["changes"]) for e in plan["entries"]),
                              "verified": False, "mismatches": unit_mismatch})
            continue
        # Cau truc bundle: thu tu + co entry giu nguyen; entry khong ep giu nguyen bytes.
        if kind == "bundle" and orig_bundle is not None and pressed_bundle is not None:
            orig_entries = [(e.name, e.flags) for e in orig_bundle.entries]
            new_entries = [(e.name, e.flags) for e in pressed_bundle.entries]
            if orig_entries != new_entries:
                structural.append({"unit": display, "kind": "bundle_entries",
                                   "message_vi": "Đơn vị %s: danh sách/thứ tự/cờ entry bundle đổi so với gốc." % display})
            for e in orig_bundle.entries:
                if e.name == req_entry:
                    continue
                try:
                    if _sha(orig_bundle.get_entry(e.name)) != _sha(pressed_bundle.get_entry(e.name)):
                        structural.append({"unit": display, "kind": "bundle_entry_changed",
                                           "message_vi": "Đơn vị %s: entry %r đổi nhưng không nằm trong kế hoạch ép." % (display, e.name)})
                except PB3Error as exc:
                    structural.append({"unit": display, "kind": "bundle_entry_missing",
                                       "message_vi": "Đơn vị %s: không đọc được entry %r trong file đã ép: %s" % (display, e.name, _err_text(exc))})
        # Danh muc object: path_id/class_id/thu tu giu nguyen; object khong doi giu nguyen bytes.
        expected_pids = {int(e["object"]) for e in plan["entries"]}
        orig_inv = [(o.path_id, o.class_id) for o in orig_sf.objects]
        new_inv = [(o.path_id, o.class_id) for o in pressed_sf.objects]
        if orig_inv != new_inv:
            structural.append({"unit": display, "kind": "object_inventory",
                               "message_vi": "Đơn vị %s: danh mục object (path_id/class/thứ tự) đổi so với gốc." % display})
        for o in orig_sf.objects:
            if o.path_id in expected_pids:
                continue
            try:
                if _sha(orig_sf.object_bytes(o.path_id)) != _sha(pressed_sf.object_bytes(o.path_id)):
                    structural.append({"unit": display, "kind": "object_changed",
                                       "message_vi": "Đơn vị %s: object path_id=%d đổi nhưng không nằm trong kế hoạch ép." % (display, o.path_id)})
            except PB3Error:
                structural.append({"unit": display, "kind": "object_missing",
                                   "message_vi": "Đơn vị %s: thiếu object path_id=%d trong file đã ép." % (display, o.path_id)})
        # Noi dung tung entry: key doi == after, key khac giu nguyen, sha == after_sha.
        for entry in plan["entries"]:
            pid = int(entry["object"])
            if entry.get("asset_type") == "MonoBehaviour":
                # M1: doc lai object MB, doi chieu tung change theo
                # field_path + object sha == after_sha (sha bao ca key
                # khac giu nguyen — doi bat ky byte nao ngoai plan cung
                # lam lech sha, tuong duong kiem extra_change cua TextAsset).
                try:
                    new_obj_b = pressed_sf.object_bytes(pid)
                except PB3Error as exc:
                    unit_mismatch.append({"unit": display, "kind": "asset_missing",
                                          "message_vi": "Đơn vị %s: không tìm thấy MonoBehaviour path_id=%d trong file đã ép: %s" % (display, pid, _err_text(exc))})
                    continue
                ok, details = _u._verify_mb_values(
                    pressed_sf, pid, entry["changes"])
                for d in details:
                    unit_mismatch.append({"unit": display, "kind": "value_mismatch",
                                          "message_vi": "Đơn vị %s: %s" % (display, d)})
                if ok:
                    for change in entry["changes"]:
                        verified_keys.add(change["key"])
                if _sha(new_obj_b) != entry["after_sha"]:
                    unit_mismatch.append({"unit": display, "kind": "after_sha",
                                          "message_vi": "MonoBehaviour path_id=%d trong file đã ép có sha khác after_sha của kế hoạch (đơn vị %s)." % (pid, display)})
                continue
            try:
                _p, _name, orig_script = orig_sf.find_text_asset(path_id=pid)
                _p2, _name2, new_script = pressed_sf.find_text_asset(path_id=pid)
            except PB3Error as exc:
                unit_mismatch.append({"unit": display, "kind": "asset_missing",
                                      "message_vi": "Đơn vị %s: không tìm thấy TextAsset path_id=%d trong file đã ép: %s" % (display, pid, _err_text(exc))})
                continue
            try:
                orig_obj = json.loads(orig_script)
                new_obj = json.loads(new_script)
            except ValueError:
                unit_mismatch.append({"unit": display, "kind": "not_json",
                                      "message_vi": "Đơn vị %s: TextAsset path_id=%d trong file đã ép không còn là JSON." % (display, pid)})
                continue
            if not isinstance(orig_obj, dict) or not isinstance(new_obj, dict):
                unit_mismatch.append({"unit": display, "kind": "not_json_dict",
                                      "message_vi": "Đơn vị %s: TextAsset path_id=%d trong file đã ép không phải JSON dạng dict." % (display, pid)})
                continue
            for change in entry["changes"]:
                key = change["key"]
                if key not in new_obj:
                    unit_mismatch.append({"unit": display, "key": key, "kind": "key_missing",
                                          "message_vi": "Key %r thiếu trong file đã ép (đơn vị %s)." % (key, display)})
                elif new_obj[key] != change["after"]:
                    unit_mismatch.append({"unit": display, "key": key, "kind": "value_mismatch",
                                          "message_vi": "Key %r trong file đã ép là %r, kế hoạch ép là %r (đơn vị %s)."
                                                        % (key, new_obj[key], change["after"], display)})
                else:
                    verified_keys.add(key)
            for key, value in orig_obj.items():
                if key in {c["key"] for c in entry["changes"]}:
                    continue
                if key not in new_obj or new_obj[key] != value:
                    unit_mismatch.append({"unit": display, "key": key, "kind": "extra_change",
                                          "message_vi": "Key %r đổi ngoài kế hoạch trong file đã ép (đơn vị %s): %r → %r."
                                                        % (key, display, value, new_obj.get(key))})
            for key in new_obj:
                if key not in orig_obj:
                    unit_mismatch.append({"unit": display, "key": key, "kind": "extra_key",
                                          "message_vi": "Key lạ %r xuất hiện trong file đã ép (đơn vị %s)." % (key, display)})
            if _sha(new_script.encode("utf-8")) != entry["after_sha"]:
                unit_mismatch.append({"unit": display, "kind": "after_sha",
                                      "message_vi": "TextAsset path_id=%d trong file đã ép có sha khác after_sha của kế hoạch (đơn vị %s)." % (pid, display)})
        mismatches.extend(unit_mismatch)
        units_out.append({
            "unit": display, "kind": kind,
            "plan_id": plan.get("id"),
            "entries": len(plan["entries"]),
            "changes": sum(len(e["changes"]) for e in plan["entries"]),
            "mode": item["mode"], "size_before": int(len(orig_bytes)),
            "size_after": int(len(pressed_unit)),
            "byte_identical_with_simulation": bool(pressed_unit == item["simulated"]),
            "verified": not unit_mismatch,
            "mismatches": unit_mismatch,
        })
    return {"units": units_out, "mismatches": mismatches,
            "structural": structural, "verified_keys": verified_keys}


# ---------------------------------------------------------------- cong
def run_gate(source_bytes: bytes, *, source_name: str,
             translations: Dict[str, str],
             source_texts: Optional[Dict[str, str]] = None,
             provenance: Any = None, choices: Any = None,
             pressed_bytes: Optional[bytes] = None,
             pressed_name: Optional[str] = None,
             allowlist: Any = None,
             fonts: Optional[List[Dict[str, Any]]] = None,
             display_fonts: Any = (), font_error: Optional[str] = None,
             backend: Any = None, limits: Optional[Limits] = None,
             progress_fn: Any = None, src_path: Optional[str] = None,
             pressed_path: Optional[str] = None,
             allow_partial: bool = False,
             source_locale: str = "en") -> Dict[str, Any]:
    """Chay cong kiem tra day du, tra 1 verdict duy nhat.

    Khong ghi file nao. Loi dau vao (translations/source/allowlist sai
    kieu, provenance khong nhan ra) thi raise ValueError nhu (E). Loi
    noi dung file (ma hoa, v9, khong doc duoc don vi) thi tra verdict
    FAIL kem ly do, khong raise — cong luon tra verdict khi dau vao
    hop le."""
    _validate_kv(translations, "translations")
    if not translations:
        raise ValueError("translations phải là JSON object không rỗng "
                         "(key -> giá trị mới, như đầu ra tab Dịch).")
    if source_texts is not None:
        _validate_kv(source_texts, "source")
    allow = _normalize_allowlist(allowlist)
    source_bytes = bytes(source_bytes)
    if pressed_bytes is not None:
        pressed_bytes = bytes(pressed_bytes)
    lim = limits or Limits()
    be = backend or _u.UnityBackend("NATIVE")

    def _prog(done: int, total: int = 5) -> None:
        if progress_fn is not None:
            try:
                progress_fn(done, total)
            except Exception:
                pass

    source_sha = _sha(source_bytes)
    pressed_sha = _sha(pressed_bytes) if pressed_bytes is not None else None
    mode = "pressed" if pressed_bytes is not None else "pre_write"

    # (a) + (b): thuan tuy, khong can file.
    check_a = check_translation_completeness(
        source_texts, translations,
        allowed_empty=allow["allowed_empty"],
        allowed_missing=allow["allowed_missing"],
        allowed_extra=allow["allowed_extra"],
        allow_partial=allow_partial)
    _prog(1)
    check_b = check_placeholders(
        source_texts, translations,
        allowed=allow["allowed_placeholders"])
    _prog(2)

    # Preview (E) de doi chieu + ke hoach rieng cua cong.
    preview = None
    preview_error = None
    try:
        preview = _rb.build_rebuild_preview(
            source_bytes, source_name=source_name,
            translations=translations, provenance=provenance,
            choices=choices, backend=be, limits=lim, src_path=None,
            source_locale=source_locale)
    except ValueError:
        raise
    except PB3Error as exc:
        preview_error = exc
    built = _build_plans(source_bytes, source_name, translations,
                         provenance, choices, be, lim,
                         source_locale=source_locale)
    _prog(3)
    plan_items = built["plans"]
    refused = built["refused"]
    unmatched = built["unmatched"]
    unmatched_total = built["unmatched_total"]
    unchanged = built["unchanged"]
    planned_changes = built["total_changes"]

    verify = _verify_pressed_units(source_bytes, plan_items, pressed_bytes,
                                   be, lim)
    _prog(4)

    # (d) font.
    check_d = check_font_coverage(translations, fonts,
                                  display_fonts=display_fonts,
                                  font_error=font_error)
    _prog(5)

    # (c) diffverify.
    unmatched_by_reason: Dict[str, int] = {}
    for item in unmatched:
        reason = item.get("reason") or "not_found"
        unmatched_by_reason[reason] = unmatched_by_reason.get(reason, 0) + 1
    excused_unpressed = [item["key"] for item in unmatched
                         if item["key"] in allow["allowed_unpressed"]]
    unpressed_bad = [item for item in unmatched
                     if item["key"] not in allow["allowed_unpressed"]]
    dv_failures: List[Dict[str, Any]] = []
    if preview_error is not None:
        dv_failures.append({
            "kind": "preview_error", "code": preview_error.code,
            "message_vi": "Xem trước (E) lỗi: [%s] %s"
                          % (preview_error.code, _err_text(preview_error))})
    for r in refused:
        dv_failures.append({"unit": r.get("unit"), "kind": "refused",
                            "code": r.get("code"),
                            "message_vi": "Đơn vị %s bị từ chối: [%s] %s"
                                          % (r.get("unit"), r.get("code"),
                                             r.get("message"))})
    if planned_changes == 0 and not refused and preview_error is None:
        dv_failures.append({"kind": "no_changes",
                            "message_vi": "Không có thay đổi nào được ép: mọi key hoặc đã giống bản gốc, hoặc không ép được — không có gì để giao."})
    for item in unpressed_bad[:_DETAIL_CAP]:
        reason = item.get("reason") or "not_found"
        dv_failures.append({"key": item["key"], "kind": "unpressed",
                            "reason": reason, "unit": item.get("unit"),
                            "message_vi": "Key %r chưa ép được: %s."
                                          % (item["key"], _REASON_VI.get(reason, reason))})
    for mm in verify["mismatches"][:_DETAIL_CAP]:
        dv_failures.append(mm)
    dv_warnings: List[str] = []
    if excused_unpressed:
        dv_warnings.append("%d key chưa ép nhưng nằm trong danh sách cho phép: %s%s"
                           % (len(excused_unpressed), ", ".join(excused_unpressed[:5]),
                              "…" if len(excused_unpressed) > 5 else ""))
    if unchanged:
        dv_warnings.append("%d key giữ nguyên như bản gốc nên không cần ép." % unchanged)
    if unpressed_bad and len(unpressed_bad) > _DETAIL_CAP:
        dv_warnings.append("Chỉ liệt kê %d/%d key chưa ép đầu tiên — xem unmatched đầy đủ trong kết quả."
                           % (_DETAIL_CAP, len(unpressed_bad)))
    dv_status = "fail" if dv_failures else "pass"
    if dv_status == "pass":
        dv_summary = ("Trích lại file đã ép khớp bản dịch: %d thay đổi ở %d đơn vị đã kiểm, %d key chưa ép (đều trong danh sách cho phép hoặc không có)."
                      % (planned_changes, len(plan_items), len(excused_unpressed)))
    else:
        dv_summary = "File đã ép chưa khớp bản dịch: %d key chưa ép, %d lệch nội dung, %d đơn vị bị từ chối." % (
            len(unpressed_bad), len(verify["mismatches"]), len(refused))
    check_c = _check(
        CHECK_DIFFVERIFY, "Ép lại khớp bản dịch (diffverify)", dv_status,
        dv_summary, dv_failures, dv_warnings,
        {"planned_units": len(plan_items),
         "planned_entries": sum(len(p["plan"]["entries"]) for p in plan_items),
         "planned_changes": planned_changes,
         "verified_keys": len(verify["verified_keys"]),
         "mismatched": len(verify["mismatches"]),
         "unmatched": unmatched_total,
         "unmatched_bad": len(unpressed_bad),
         "unmatched_excused": len(excused_unpressed),
         "unchanged": unchanged, "refused": len(refused)},
        unmatched_by_reason=unmatched_by_reason,
        units=verify["units"])

    # (e) toan ven.
    int_failures: List[Dict[str, Any]] = []
    if preview_error is not None:
        int_failures.append({
            "kind": "preview_error", "code": preview_error.code,
            "message_vi": "Không lập được kế hoạch ép để đối chiếu toàn vẹn: [%s] %s"
                          % (preview_error.code, _err_text(preview_error))})
    if preview is not None:
        totals = preview.get("totals") or {}
        if (totals.get("units") != len(plan_items)
                or totals.get("changed") != planned_changes
                or totals.get("unmatched") != unmatched_total
                or totals.get("refused") != len(refused)):
            int_failures.append({
                "kind": "preview_mismatch",
                "message_vi": "Kế hoạch của cổng lệch với xem trước (E): cổng %d đơn vị/%d thay đổi/%d chưa ép/%d từ chối, xem trước (E) %s/%s/%s/%s."
                              % (len(plan_items), planned_changes, unmatched_total, len(refused),
                                 totals.get("units"), totals.get("changed"),
                                 totals.get("unmatched"), totals.get("refused"))})
    for st in verify["structural"][:_DETAIL_CAP]:
        int_failures.append(st)
    if refused:
        int_failures.append({"kind": "refused",
                             "message_vi": "%d đơn vị bị từ chối nên kế hoạch ép chưa đầy đủ." % len(refused)})
    if src_path and pressed_path:
        try:
            if os.path.realpath(str(src_path)) == os.path.realpath(str(pressed_path)):
                int_failures.append({"kind": "same_path",
                                     "message_vi": "File đã ép trùng đường dẫn file gốc — không ghi đè file gốc, hãy chọn file đã ép khác file gốc."})
        except OSError:
            pass
    if pressed_bytes is not None and pressed_bytes == source_bytes and planned_changes > 0:
        int_failures.append({"kind": "pressed_equals_source",
                             "message_vi": "File đã ép giống hệt file gốc (cùng bytes) trong khi kế hoạch có %d thay đổi — file này chưa được ép." % planned_changes})
    int_warnings: List[str] = []
    n_identical = sum(1 for u in verify["units"] if u.get("byte_identical_with_simulation"))
    if pressed_bytes is not None and plan_items and n_identical < len(plan_items):
        int_warnings.append("%d/%d đơn vị đã ép khác bytes với mô phỏng của cổng (nội dung vẫn khớp kế hoạch thì không sao — khác nhau có thể do công cụ ép khác)."
                            % (len(plan_items) - n_identical, len(plan_items)))
    int_status = "fail" if int_failures else "pass"
    if int_status == "pass":
        int_summary = "Hash/cấu trúc khớp kế hoạch ép: %d đơn vị, %d entry, sha script khớp after_sha." % (
            len(plan_items), sum(len(p["plan"]["entries"]) for p in plan_items))
    else:
        int_summary = "Toàn vẹn chưa đạt: %d vấn đề (xem chi tiết)." % len(int_failures)
    check_e = _check(
        CHECK_INTEGRITY, "Toàn vẹn file", int_status, int_summary,
        int_failures, int_warnings,
        {"units": len(plan_items),
         "entries": sum(len(p["plan"]["entries"]) for p in plan_items),
         "structural_issues": len(verify["structural"]),
         "byte_identical_units": n_identical,
         "source_sha256": source_sha,
         "pressed_sha256": pressed_sha})

    checks = [check_a, check_b, check_c, check_d, check_e]
    verdict = "PASS" if all(c["status"] == "pass" for c in checks) else "FAIL"
    errors: List[str] = []
    for chk in checks:
        if chk["status"] != "pass":
            errors.append("[%s] %s" % (chk["name"], chk["summary_vi"]))
            for f in chk["failures"][:10]:
                msg = f.get("message_vi") or str(f)
                if msg not in errors:
                    errors.append(msg)
    errors_total = sum(len(c["failures"]) for c in checks
                       if c["status"] != "pass")
    if len(errors) > _DETAIL_CAP:
        errors = errors[:_DETAIL_CAP]
        errors.append("… và %d lỗi khác (xem chi tiết từng kiểm tra)."
                      % max(0, errors_total - _DETAIL_CAP))
    warnings: List[str] = []
    for chk in checks:
        warnings.extend(chk["warnings"])
    warnings = warnings[:_DETAIL_CAP]

    grouped = built["grouped"]
    return {
        "ok": True, "gate_version": GATE_VERSION,
        "verdict": verdict, "delivery_allowed": verdict == "PASS",
        "mode": mode,
        "source_name": source_name, "source_sha256": source_sha,
        "pressed_name": pressed_name,
        "pressed_sha256": pressed_sha,
        "translations_sha256": canonical_translations_sha(translations),
        "totals": {
            "keys": len(translations),
            "source_keys": len(source_texts) if source_texts is not None else 0,
            "source_provided": source_texts is not None,
            "changed": planned_changes,
            "units": len(plan_items),
            "entries": sum(len(p["plan"]["entries"]) for p in plan_items),
            "unmatched": unmatched_total,
            "unchanged": unchanged,
            "refused": len(refused),
            "conflicts": grouped["conflicts_total"],
            "verified_keys": len(verify["verified_keys"]),
        },
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "unmatched": unmatched[:_UNMATCHED_CAP],
        "unmatched_total": unmatched_total,
        "unmatched_by_reason": unmatched_by_reason,
        "unmatched_truncated": unmatched_total > _UNMATCHED_CAP,
        "conflicts": grouped["conflicts"],
        "conflicts_total": grouped["conflicts_total"],
        "conflicts_truncated": grouped["conflicts_truncated"],
        "refused": refused,
        "scan_all": grouped["scan_all"],
        "warnings_preview": grouped["warnings"],
        "allowlist_used": {
            "allowed_empty": sorted(allow["allowed_empty"])[:_UNMATCHED_CAP],
            "allowed_missing": sorted(allow["allowed_missing"])[:_UNMATCHED_CAP],
            "allowed_extra": sorted(allow["allowed_extra"])[:_UNMATCHED_CAP],
            "allowed_unpressed": sorted(allow["allowed_unpressed"])[:_UNMATCHED_CAP],
            "allowed_placeholders": sorted(allow["allowed_placeholders"])[:_UNMATCHED_CAP],
            "counts": {name: len(val) for name, val in allow.items()},
            "allow_partial": bool(allow_partial),
            "excused_unpressed": excused_unpressed[:_DETAIL_CAP],
        },
        "partial": bool(allow_partial),
        "fonts": check_d.get("fonts", []),
        "note": "Cổng kiểm tra trước khi giao (bước 6): FAIL thì chặn giao, không có đường giao khi cảnh báo. "
                "Kết quả này chỉ đúng cho đúng file gốc + bản dịch + provenance đã kiểm (xem sha).",
    }
