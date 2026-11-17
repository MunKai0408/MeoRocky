"""CSV localization cho Mod (M3) — wrapper mong quanh plugin csv_table.

Dung chung cho ca 2 dang CSV trong (E):
- File CSV/TSV doc lap trong ZIP/IPA (vd PB3_LOC_DEFAULT.csv): don vi kind "csv",
  bytes cua don vi CHINH LA file CSV.
- TextAsset CSV trong bundle/.assets: script cua asset la noi dung CSV.

Moi phep doc/ghi deu qua plugin csv_table (lazy import, tien le
sdk/inspect.py:159) de giu dung 1 ngu nghia parse/build voi duong trich xuat:
delimiter doan, header (cot key + cot locale), note vi tri o "csv:<hang>:<cot>"
(0-based tinh tu dong du lieu), ghi nguoc giu delimiter/terminator/encoding,
quoting co the doi nhung noi dung parse lai giu nguyen.

Chi thu vien chuan + plugin. Python >= 3.9. Khong goi mang.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.types import LocEntry, LocTable, SourceRef

_CELL_RE = re.compile(r"^csv:(\d+):(\d+)$")
_CSV_EXTS = (".csv", ".tsv")


def _plugin():
    """CsvPlugin (lazy import — tranh vong lap import o tang sdk)."""
    from plugins.csv_table.plugin import CsvPlugin

    return CsvPlugin()


def parse_csv(data: bytes, name: str) -> Dict[str, Any]:
    """Parse CSV qua plugin: tra dict {entries, schema_hint, stats,
    conflicts, warnings}. Loi parse -> PB3Error (E_CORRUPT /
    E_UNSUPPORTED_FORMAT). entries la LocEntry (key, value, locale, notes
    "csv:<hang>:<cot>", status ok/empty/conflict). Ten rong thi dung
    "csv" vi SourceRef cua plugin bat buoc ten khong rong."""
    return _plugin()._parse_bytes(bytes(data), str(name) or "csv", None)


def is_localization_csv(data: bytes, name: str) -> bool:
    """True khi data la bang CSV localization.

    - Ten .csv/.tsv + parse duoc + co it nhat 1 entry (key khong rong).
    - Ten khong co duoi CSV (vd TextAsset trong bundle, file dau vao
      khong ten): chi nhan khi CO header dat ten cot key (Key/ID/Name/...)
      + co entry — de khoi nhan nham text thuong co dau phay.
    Dung cho resolver (batch.py) de dinh tuyen don vi CSV thay vi bao
    'khong phai SerializedFile'. Kiem tra cot nguon nam o tang plan."""
    try:
        res = parse_csv(data, name)
    except PB3Error:
        return False
    if not res.get("entries"):
        return False
    # Bang localization that co cot key + it nhat 1 cot gia tri (JSON 1
    # cot, text 1 cot khong phai bang — du ten .csv cung loai).
    if (res.get("stats") or {}).get("columns", 0) < 2:
        return False
    low = str(name).lower()
    if low.endswith(_CSV_EXTS):
        return True
    stats = res.get("stats") or {}
    return bool(stats.get("header"))


def is_csv_text_asset(script: str, name: str) -> bool:
    """True khi script TextAsset la bang CSV localization (xem
    is_localization_csv — TextAsset thuong khong mang duoi .csv nen
    chi nhan bang co header)."""
    try:
        return is_localization_csv(script.encode("utf-8"), name)
    except UnicodeEncodeError:
        return False


def source_map(data: bytes, name: str, source_locale: str):
    """Ban do cot nguon: {key: {"cell": "csv:r:c"|None, "value": str}}.

    Chon cot co locale == source_locale; neu bang khong co cot locale nao
    (bang key,value thuan) thi dung cot value locale None; bang co cot locale
    ma khong co cot source_locale thi tra mapping rong (plan se bo qua kem
    ly do). Ban ghi dau thang khi trung key (nhu extraction); key trung
    sau vao `duplicates`. Tra (mapping, duplicates, effective_locale) —
    effective_locale la locale thuc dung de ghi (source_locale hoac None)."""
    res = parse_csv(data, name)
    entries = res.get("entries") or []
    locales_present = {e.locale for e in entries if e.locale is not None}
    if source_locale in locales_present:
        picked = [e for e in entries if e.locale == source_locale]
        effective = source_locale
    elif not locales_present:
        picked = [e for e in entries if e.locale is None]
        effective = None
    else:
        return {}, [], source_locale
    mapping: Dict[str, Dict[str, Any]] = {}
    duplicates: List[str] = []
    for e in picked:
        if e.status == "conflict":
            duplicates.append(e.key)
            continue
        if e.key in mapping:
            duplicates.append(e.key)
            continue
        cell = None
        for n in (e.notes or []):
            if _CELL_RE.match(n or ""):
                cell = n
                break
        mapping[e.key] = {"cell": cell, "value": e.value}
    return mapping, duplicates, effective


def csv_values(data: bytes, name: str, source_locale: str) -> Dict[str, str]:
    """{key: value} cot nguon — dung de phan loai unmatched (nhu dict_values
    cua TextAsset JSON)."""
    mapping, _dups, _eff = source_map(data, name, source_locale)
    return {k: v["value"] for k, v in mapping.items()}


def simulate_csv(data: bytes, name: str, changes, source_locale: str) -> bytes:
    """Ap changes [{key, before, after, cell?}] len CSV, tra bytes moi.

    Dung plugin _build_bytes (ghi theo note vi tri o; change khong co cell
    thi plugin fallback tim theo key+locale). Verify truoc khi ghi: key
    phai co trong cot nguon va gia tri hien tai == before — lech thi
    raise ValueError (plan se bo qua asset kem ly do). Giu delimiter,
    terminator, encoding goc; quoting co the doi nhung noi dung parse lai
    giu nguyen."""
    data = bytes(data)
    mapping, _dups, effective = source_map(data, name, source_locale)
    if not mapping and changes:
        raise ValueError(
            "CSV %r khong co cot %r de ghi" % (name, source_locale))
    src = SourceRef([str(name)], str(name), None, len(data), "utf-8", {})
    entries: List[LocEntry] = []
    for ch in changes:
        key = ch["key"]
        if key not in mapping:
            raise ValueError(
                "key %r khong co trong cot %r cua CSV %r"
                % (key, source_locale, name))
        if mapping[key]["value"] != ch["before"]:
            raise ValueError(
                "key %r: gia tri hien tai %r khac before %r trong CSV %r"
                % (key, mapping[key]["value"], ch["before"], name))
        cell = ch.get("cell") or mapping[key]["cell"]
        notes = [cell] if cell else []
        entries.append(LocEntry(key, ch["after"], effective, "native", "ok",
                                src, notes))
    table = LocTable(entries=entries, locale=effective,
                     adapter_id="csv_table")
    return _plugin()._build_bytes(table, data)


def verify_csv_values(orig_data: bytes, new_data: bytes, name: str,
                      changes, source_locale: str):
    """Kiem tra 2 lop sau khi ghi CSV: (1) tung change: o moi == after;
    (2) cac key khac trong cot nguon giu nguyen gia tri, khong them key
    la. Tra (ok, [detail])."""
    try:
        orig_map, _d1, _e1 = source_map(orig_data, name, source_locale)
        new_map, _d2, _e2 = source_map(new_data, name, source_locale)
    except PB3Error as exc:
        return False, ["khong doc duoc CSV de kiem: %s" % exc]
    details: List[str] = []
    changed = {c["key"]: c["after"] for c in changes}
    for c in changes:
        got = (new_map.get(c["key"]) or {}).get("value")
        if got is None:
            details.append("key %r mat khoi CSV sau khi ghi" % c["key"])
        elif got != c["after"]:
            details.append("key %r sau khi ghi la %r, mong doi %r"
                           % (c["key"], got, c["after"]))
    for key, cell in orig_map.items():
        if key in changed:
            continue
        got = (new_map.get(key) or {}).get("value")
        if got != cell["value"]:
            details.append("key %r doi ngoai ke hoach: %r -> %r"
                           % (key, cell["value"], got))
    for key in new_map:
        if key not in orig_map:
            details.append("key la %r xuat hien trong CSV sau khi ghi" % key)
    return (not details), details
