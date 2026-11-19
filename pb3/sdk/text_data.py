"""Bang text dang danh sach (text-data list) — co che chung tim text
chinh cua game Unity khi duong chinh thong (StringTable / I2 / MB
generic / IL2CPP literals) cho qua it hoac khong co text.

Phuong phap 5 buoc cua anh Duy (14:00-14:01 +07 2026-11-19, do that
tren game Nhiet Huyet Sinh Ton v1.0.3): StringTable chi co 2 key (ten
game, ngo cut); quet toan bo 1.484 bundle thi chi 1 cho chua text lon —
MonoBehaviour LocalizationTextData trong bundle 6d3223da...bundle,
truong m_DataList 2.279 entry, moi entry co `key` + `fileName` (nhom
Scenario/UI/Skill...) + 15 cot ngon ngu (ja, en, fr, de, zhs, ko...);
en.json la cot `en` trich rieng, key–value phang.

Co che CHUNG (khong hardcode ten game / hash bundle / ten class /
ten field duy nhat): nhan dien bang CAU TRUC — mot truong list ma moi
entry la dict co:
  - truong key chuoi (ten: key / Key / m_Key),
  - it nhat 2 cot ngon ngu: ten field la ma locale (en, ja, fr, pt-br,
    frc, zhs, zht...) va gia tri la chuoi o moi entry,
  - tuy chon truong nhom (fileName / file_name / group / category)
    — chi lam metadata, khong lam ban key.
List du lieu thuan (vd Master*Data: entry co m_Id so, khong co key
chuoi) KHONG khop. Hinh dang mo ho -> None (caller roi ve generic),
fail-closed.

Gioi han (de phong nhan nham list cau hinh nho):
  - it nhat TEXT_DATA_MIN_ENTRIES entry (>= 10),
  - toi da TEXT_DATA_MAX_ENTRIES entry (100.000),
  - key khong rong, <= 512 ky tu, khong ky tu dieu khien,
  - gia tri cot ngon ngu la chuoi, <= 100.000 ky tu, khong NUL.

Kiem chung 2 chieu (buoc 5 cua phuong phap) thuc hien o tang tren:
  (a) luc trich, _text_data_list_virtual_files (sdk/extraction) assert
      0 key trung + 0 gia tri rong moi cot, fail-closed neu sai;
  (b) sau dich, gate.check_placeholders doi chieu placeholder
      {0}/{0:0.##}, so lan xuong dong, the <move>/<punch> tung key.

Chi stdlib + Python 3.9. Khong import sdk.unity (tranh vong tron):
module nay chi xu ly dict da parse san tu type tree.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Nguon co cau truc dang danh sach text (xem sdk/extraction:
# MERGE_ORIGINS / STRUCTURED_ORIGINS).
ORIGIN = "text_data_list"

TEXT_DATA_MIN_ENTRIES = 10
TEXT_DATA_MAX_ENTRIES = 100000
TEXT_DATA_MAX_KEY_CHARS = 512
TEXT_DATA_MAX_VALUE_CHARS = 100000

# Ten field key / nhom chap nhan (ten chung cua Unity, khong rieng game
# nao). Chi xet cac ten nay — khong doan them ten khac.
KEY_FIELD_NAMES = ("key", "Key", "m_Key")
GROUP_FIELD_NAMES = ("fileName", "file_name", "group", "Group",
                     "category", "Category")

# Viet tat cot ngon ngu kieu Unity Localization (khong co trong bang
# locale chuan): zhs = tieng Trung gian the, zht = tieng Trung phon the.
# Sau khi chuan hoa (_ -> -, chu thuong) thi anh xa ve ma canonical.
LOCALE_COLUMN_ALIASES = {
    "zhs": "zh-CN",
    "zht": "zh-TW",
    "zh-hans": "zh-CN",
    "zh-hant": "zh-TW",
}

# Ma locale dang "xx" / "xxx" / "xx-YYYY" (vd en, frc, pt-br, zh-cn).
# Gioi han 2-3 ky tu goc + bien the 2-4 ky tu de loai ten field thuong
# (vd "name", "status", "notes" dai hon hoac khong phai dang ma).
_LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")


def normalize_text_column_code(code: Any) -> Optional[str]:
    """Ten cot tho -> ma locale chuan hoa (vd "zhs" -> "zh-CN",
    "zh_CN" -> "zh-cn", "en" -> "en"). Ten khong phai dang ma locale
    (vd "Notes", "Status", "") tra None."""
    if not isinstance(code, str):
        return None
    norm = code.strip().lower().replace("_", "-")
    if not norm:
        return None
    if norm in LOCALE_COLUMN_ALIASES:
        return LOCALE_COLUMN_ALIASES[norm]
    if _LOCALE_CODE_RE.match(norm) is None:
        return None
    return norm


def _no_control(s: str) -> bool:
    return all(ord(c) >= 0x20 and ord(c) != 0x7F for c in s)


def text_data_list_from_fields(val: Any) -> Optional[Dict[str, Any]]:
    """Nhan dien bang text dang danh sach trong dict type tree da parse.

    Quet moi truong cap cao nhat cua dict; truong dau tien co dang
    hop le thi tra mo ta bang, khong thi None (caller roi ve generic).
    Tra {"list_field", "key_field", "group_field", "columns" (ma
    canonical theo thu tu gap dau), "entries" [{key, group, values}],
    "groups" {nhom: so_entry}}. Key trung / gia tri rong KHONG loai o
    day — tang trich xuat se assert va fail-closed (xem mo ta module).
    """
    if not isinstance(val, dict):
        return None
    for list_field, raw in val.items():
        if not isinstance(raw, list):
            continue
        found = _validate_list(list_field, raw)
        if found is not None:
            return found
    return None


def _validate_list(list_field: str, raw: List[Any]) -> Optional[Dict[str, Any]]:
    if not (TEXT_DATA_MIN_ENTRIES <= len(raw) <= TEXT_DATA_MAX_ENTRIES):
        return None
    if not all(isinstance(e, dict) for e in raw):
        return None
    first = raw[0]
    key_field = None
    for name in KEY_FIELD_NAMES:
        if name in first:
            key_field = name
            break
    if key_field is None:
        return None
    group_field = None
    for name in GROUP_FIELD_NAMES:
        if name in first and isinstance(first[name], str):
            group_field = name
            break
    # Cot ngon ngu: field (khac key/nhom) co ten la ma locale. Nhieu ten
    # tho co the anh xa cung ma canonical (vd "zhs" + "zh-hans" deu ve
    # "zh-CN"): chi giu ten gap dau tien cho moi ma, ten sau bi bo (khong
    # ghi de im lang).
    col_fields: Dict[str, str] = {}
    for name in first:
        if name in (key_field, group_field):
            continue
        code = normalize_text_column_code(name)
        if code is None or code in col_fields:
            continue
        col_fields[code] = name
    columns = list(col_fields)
    if len(columns) < 2:
        return None
    # Moi entry: key chuoi hop le + moi cot ngon ngu la chuoi hop le;
    # truong nhom (neu co) la chuoi o moi entry.
    entries: List[Dict[str, Any]] = []
    groups: Dict[str, int] = {}
    for entry in raw:
        key = entry.get(key_field)
        if (not isinstance(key, str) or not key
                or len(key) > TEXT_DATA_MAX_KEY_CHARS
                or not _no_control(key)):
            return None
        values: Dict[str, str] = {}
        for code, name in col_fields.items():
            value = entry.get(name)
            if (not isinstance(value, str)
                    or len(value) > TEXT_DATA_MAX_VALUE_CHARS
                    or "\x00" in value):
                return None
            values[code] = value
        group = None
        if group_field is not None:
            group = entry.get(group_field)
            if not isinstance(group, str):
                return None
            groups[group] = groups.get(group, 0) + 1
        entries.append({"key": key, "group": group, "values": values})
    return {"list_field": list_field, "key_field": key_field,
            "group_field": group_field, "columns": columns,
            "entries": entries, "groups": groups}
