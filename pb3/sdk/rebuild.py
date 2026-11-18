"""(E) Ep ban dich lai vao file goc [buoc 5] — dieu phoi rebuild.

Nhom ban dich theo provenance -> chon ban thang cho xung dot -> 1 plan
moi don vi (sdk/plan.py build_plan) -> ap bang executor hien co
(plan._apply_entry_changes -> sdk/unity.apply_patch, PATCH_INPLACE /
REBUILD_SECTION) + duong rebuild streaming MOI cho bundle .unity3d doc
lap lon (sdk/unity.rebuild_bundle_streaming). Duong RAM hien co giu
nguyen cho file nho; bundle trong ZIP/IPA (bytes da trong RAM) giu
duong RAM.

PHAM VI v1 (G1 — quyet dinh theo bang chung, xem bao cao P0-1) + M1:
ghi TextAsset JSON dang dict VA MonoBehaviour/StringTable co duong ghi
kiem chung (bang ngon ngu I2 byte-walk + StringTable co type tree, qua
apply_monobehaviour_patch REBUILD_SECTION). Generic MonoBehaviour (scan
path / field le, khong co trong output trich xuat da gop) van vao
`unmatched` voi reason `monobehaviour`, dem ro, KHONG im lang bo,
KHONG tuyen bo du tieu chi vong tron cho nhom key nay.

KHOA REBUILD_ALL: giu nguyen — sdk/unity.py MATURITY="M2", apply_patch
tu choi REBUILD_ALL du confirm=True. (E) chi dung REBUILD_SECTION.
KHONG dong goi lai IPA o day (decision #70): output = don vi nho nhat da patch
(bundle/.assets), nhieu don vi thi ZIP lai (mau plan._write_outputs).
Dong goi lai IPA la buoc 5 RIENG (M2, sdk/repack.py) dung ket qua cua
(E) — khong doi hanh vi mac dinh cua (E).
KHONG expose sdk/containers.py rebuild_zip vao bridge.

Chi thu vien chuan, Python >= 3.9. Khong goi mang. Dry-run mac dinh o
tang bridge (confirm bat buoc khi ghi that). Khong bao gio ghi de file
goc: moi duong ghi deu co chot dest != source.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.limits import Limits
from sdk import plan as _plan
from sdk import unity as _u
from sdk.extraction import MERGE_CONFLICTS_CAP

REBUILD_OUT_DIR = "/tmp/rebuild_out"
REBUILD_ZIP_PATH = "/tmp/rebuild.zip"
# Bundle doc lap >= nguong nay thi dung duong streaming (thay vi om ca
# payload trong RAM); nho hon thi duong RAM hien co.
STREAMING_MIN_BYTES = 64 * 1024 * 1024
UNMATCHED_CAP = 2000  # so key unmatched liet ke chi tiet toi da (con lai chi dem)

_MERGE_FORMAT = "beartea-merge-provenance/1"
_TRANSLATION_FORMAT = "beartea-translation-provenance/1"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_translations(translations):
    if not isinstance(translations, dict) or not translations:
        raise ValueError("translations phai la JSON object khong rong "
                         "(key -> gia tri moi, nhu dau ra tab Dich).")
    for k, v in translations.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("translations: key va gia tri deu phai la chuoi.")


def resolve_conflict_choices(conflicts, choices):
    """Ham thuan: giai quyet lua chon ban thang cho tung xung dot.

    conflicts: schema cua merge_language_outcomes —
    [{key, kept:{value,source}, dropped:{value,source}}].
    choices: {key: "kept"|"dropped"}; thieu -> mac dinh "kept";
    key la (khong co trong conflicts) -> ValueError; gia tri la ->
    ValueError. Chon "dropped" thi kem warning: ban dich dich tu ban
    kept (gia tri goc khac nhau) nen can kiem tra lai.
    Tra list {key, kept, dropped, choice, warning?} giu thu tu goc.
    """
    choices = choices or {}
    if not isinstance(choices, dict):
        raise ValueError("choices phai la object {key: kept|dropped}.")
    known = set()
    resolved = []
    for c in (conflicts or []):
        if not isinstance(c, dict) or "key" not in c:
            continue
        key = c["key"]
        known.add(key)
        choice = choices.get(key, "kept")
        if choice not in ("kept", "dropped"):
            raise ValueError("choice cho key %r phai la kept|dropped, "
                             "nhan %r." % (key, choice))
        item = {"key": key,
                "kept": c.get("kept"), "dropped": c.get("dropped"),
                "choice": choice}
        if choice == "dropped":
            item["warning"] = (
                "key %r: chon ban dropped nhung ban dich dich tu ban kept "
                "(gia tri goc khac nhau) — kiem tra lai ban dich cho key "
                "nay truoc khi ghi." % key)
        resolved.append(item)
    for key in choices:
        if key not in known:
            raise ValueError("choice cho key la %r (khong co trong conflicts)."
                             % key)
    return resolved


def group_by_unit(translations, provenance=None, choices=None):
    """Nhom ban dich theo don vi nguon (provenance).

    Uu tien sidecar merge beartea-merge-provenance/1 (keys{} + conflicts[]);
    sidecar dich beartea-translation-provenance/1 chi 1 source -> fallback
    (tat ca key ve 1 don vi); khong provenance -> nhom "quet tat ca" +
    co canh bao (scan_all). Khong strip hau to #N/.p<path_id> de doan
    anh xa — anh xa chi lay tu provenance hoac quet that don vi.
    Tra {"groups": {unit_source: {"unit", "translations", "sources"}},
    "conflicts", "conflicts_total", "conflicts_truncated", "warnings",
    "scan_all", "provenance_format"}.
    """
    _validate_translations(translations)
    warnings: List[str] = []
    groups: Dict[str, Dict[str, Any]] = {}
    conflicts: List[Dict[str, Any]] = []
    conflicts_total = 0
    conflicts_truncated = False
    scan_all = False
    prov_format = None

    def _add(unit, key, value, source):
        g = groups.setdefault(unit, {"unit": unit, "translations": {},
                                     "sources": {}})
        g["translations"][key] = value
        g["sources"][key] = source

    if provenance is None:
        scan_all = True
        warnings.append("khong co provenance: quet tat ca don vi trong file "
                        "de tim key (cham hon va key trung o nhieu don vi "
                        "chi ep vao don vi tim thay dau tien).")
        for k, v in translations.items():
            _add(None, k, v, None)
        return {"groups": groups, "conflicts": [], "conflicts_total": 0,
                "conflicts_truncated": False, "warnings": warnings,
                "scan_all": True, "provenance_format": None}
    if not isinstance(provenance, dict):
        raise ValueError("provenance phai la JSON object (sidecar merge/dich).")
    prov_format = provenance.get("format")
    if prov_format == _MERGE_FORMAT or isinstance(provenance.get("keys"), dict):
        prov_format = _MERGE_FORMAT
        keys_map = provenance.get("keys") or {}
        raw_conflicts = provenance.get("conflicts") or []
        resolved = resolve_conflict_choices(raw_conflicts, choices)
        choice_by_key = {c["key"]: c for c in resolved}
        conflicts = resolved
        conflicts_total = int(provenance.get("conflicts_total")
                              or len(raw_conflicts))
        conflicts_truncated = bool(
            provenance.get("conflicts_truncated")
            or conflicts_total > MERGE_CONFLICTS_CAP)
        for c in resolved:
            if c.get("warning"):
                warnings.append(c["warning"])
        missing = []
        for k, v in translations.items():
            src = keys_map.get(k)
            picked = choice_by_key.get(k)
            if picked is not None and picked["choice"] == "dropped":
                dropped = picked.get("dropped") or {}
                src = dropped.get("source") or src
            if src is None:
                missing.append(k)
                _add(None, k, v, None)
            else:
                _add(str(src), k, v, str(src))
        if missing:
            scan_all = True
            warnings.append("%d key khong co trong provenance (quet tat ca "
                            "don vi de tim): %s%s"
                            % (len(missing), ", ".join(missing[:5]),
                               "…" if len(missing) > 5 else ""))
        return {"groups": groups, "conflicts": conflicts,
                "conflicts_total": conflicts_total,
                "conflicts_truncated": conflicts_truncated,
                "warnings": warnings, "scan_all": scan_all,
                "provenance_format": prov_format}
    # Sidecar dich (1 source) hoac object provenance tran.
    inner = provenance.get("provenance") \
        if isinstance(provenance.get("provenance"), dict) else provenance
    if prov_format == _TRANSLATION_FORMAT or "source" in inner \
            or "origin" in inner:
        prov_format = prov_format or _TRANSLATION_FORMAT
        src = inner.get("source")
        if not src:
            scan_all = True
            warnings.append("provenance dich khong co source (origin %s): "
                            "quet tat ca don vi de tim key."
                            % (inner.get("origin") or "khong ro"))
            for k, v in translations.items():
                _add(None, k, v, None)
        else:
            warnings.append("provenance dich chi co 1 source (%s): tat ca "
                            "key ep ve don vi nay." % src)
            for k, v in translations.items():
                _add(str(src), k, v, str(src))
        # choices ma khong co conflicts -> key la (resolve se bao).
        if choices:
            resolve_conflict_choices([], choices)
        return {"groups": groups, "conflicts": [], "conflicts_total": 0,
                "conflicts_truncated": False, "warnings": warnings,
                "scan_all": scan_all, "provenance_format": prov_format}
    raise ValueError("provenance khong nhan ra: can sidecar %s hoac %s."
                     % (_MERGE_FORMAT, _TRANSLATION_FORMAT))


# ------------------------------------------------------------- giai quyet don vi
def _read_vpath_bytes(source_bytes, vpath, backend, lim):
    """Doc bytes tai vpath (di qua container/bundle long nhau)."""
    from sdk.batch import _sdk_read_vpath_segment
    from sdk.vfs import vpath_split

    segments = vpath_split(vpath)
    current = bytes(source_bytes)
    for seg in segments:
        current = _sdk_read_vpath_segment(current, seg, backend, lim)
    return current


def _sf_entry_names(unit_bytes, backend, lim):
    """Ten entry trong bundle ma parse duoc thanh SerializedFile.

    Entry khong phai SerializedFile thi bo qua lang le (vd texture);
    nhung neu KHONG entry nao parse duoc ma co loi doc entry
    (get_entry: block hong/qua gioi han) thi raise loi dau tien len
    thay vi tra [] im lang — neu khong, bundle v9/ma hoa/hong se
    hien thanh "key not_found" thay vi refused co ma ro rang.
    """
    bundle = _u.parse_bundle(unit_bytes, backend, lim, where="bundle")
    names = []
    first_get_error = None
    for name in bundle.entry_names():
        try:
            data = bundle.get_entry(name)
        except PB3Error as exc:
            if first_get_error is None:
                first_get_error = exc
            continue
        try:
            _u.parse_serialized(data, lim, where=name)
        except PB3Error:
            continue
        names.append(name)
    if not names and first_get_error is not None:
        raise first_get_error
    return names


def _expand_prefix_units(source_bytes, prefix, backend, lim):
    """Tien to don vi (provenance source) -> list (vpath_day_du, kind).

    Tien to co the la bundle chua co CAB (vd ".../x.bundle") -> mo rong
    thanh tung entry SerializedFile; hoac .assets / vpath day du -> 1
    don vi. Loi doc (ma hoa/v9/qua lon) truyen thang len de ghi refused.
    """
    kind, _unit_bytes, _req, _label = _plan._resolve_unit(
        bytes(source_bytes), prefix, backend, lim)
    return [(prefix, kind)]


def _expand_group_units(source_bytes, prefix, backend, lim,
                        source_name=None, refused=None):
    if prefix is None:
        return _enumerate_units(source_bytes, backend, lim,
                                refused=refused)
    # Prefix chi toi CHINH file dau vao (vd trich tu bundle doc lap roi
    # ep lai chinh file do: provenance source = ten file): nhan ra bang
    # ten file + magic, mo rong don vi cua chinh file.
    if source_name:
        base = prefix.rsplit("::", 1)[-1].rsplit("/", 1)[-1]
        if base == source_name or prefix == source_name:
            if _u.detect_bundle(source_bytes) is not None:
                names = _sf_entry_names(source_bytes, backend, lim)
                if names:
                    return [(n, "bundle") for n in names]
            try:
                _u.parse_serialized(bytes(source_bytes), lim, where="assets")
                return [(None, "assets")]
            except PB3Error:
                pass
            # M3: file dau vao chinh la CSV doc lap.
            from sdk.csvutil import is_localization_csv
            if is_localization_csv(bytes(source_bytes), source_name or ""):
                return [(None, "csv")]
    try:
        return _expand_prefix_units(source_bytes, prefix, backend, lim)
    except PB3Error as first_exc:
        # Co the prefix chi toi bundle ma chua co CAB: doc bytes tai
        # prefix, neu la bundle thi mo rong entry.
        try:
            data = _read_vpath_bytes(source_bytes, prefix, backend, lim)
        except PB3Error:
            # Prefix co the thua doan dau la ten file nguon
            # (vd "game.zip::Data/x.assets" khi dau vao chinh la game.zip):
            # thu lai sau khi cat doan dau trung ten file.
            if "::" in prefix and source_name:
                head = prefix.split("::", 1)[0]
                if head.rsplit("/", 1)[-1] == source_name:
                    rest = prefix.split("::", 1)[1]
                    try:
                        return _expand_prefix_units(
                            source_bytes, rest, backend, lim)
                    except PB3Error:
                        pass
            raise first_exc
        if _u.detect_bundle(data) is not None:
            names = _sf_entry_names(data, backend, lim)
            if not names:
                raise PB3Error("E_INPUT_NOT_FOUND",
                               "bundle %s khong co entry SerializedFile nao"
                               % prefix, where=prefix)
            return [("%s::%s" % (prefix, n), "bundle") for n in names]
        raise first_exc


def _enumerate_units(source_bytes, backend, lim, refused=None):
    """Liet ke moi don vi Unity trong file (fallback quet-tat-ca).

    File doc lap bundle/.assets -> don vi cua chinh no; ZIP/IPA -> moi
    bundle/.assets o cap 1 (entry > max_entry_bytes thi bo qua, ghi chu
    trong warnings cua ket qua). Entry > max_memory_read_bytes (duong ep
    doc tron vao RAM) cung bo qua. Khong mo container long sau hon 1 cap.
    Bundle ma hoa trong ZIP: ghi vao danh sach `refused` (neu duoc truyen
    vao) voi ma E_ENCRYPTED thay vi bo qua im lang; loi doc khac (bundle
    hong/khong phai Unity) van bo qua vi khong phan biet duoc voi du
    lieu khong phai Unity.
    """
    data = bytes(source_bytes)
    if _u.detect_bundle(data) is not None:
        names = _sf_entry_names(data, backend, lim)
        if not names:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "bundle khong co entry SerializedFile nao — "
                           "khong ep ban dich duoc", where="rebuild")
        return [(n, "bundle") for n in names]
    try:
        _u.parse_serialized(data, lim, where="assets")
        return [(None, "assets")]
    except PB3Error:
        pass
    # M3: file CSV/TSV doc lap — don vi kind "csv".
    from sdk.csvutil import is_localization_csv
    if is_localization_csv(data, ""):
        return [(None, "csv")]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "file khong phai bundle/.assets/ZIP — khong quet "
                       "don vi duoc", where="rebuild")
    units = []
    with zf:
        for info in zf.infolist():
            # Don vi ep phai doc tron vao RAM (max_memory_read_bytes,
            # 128 MiB); entry lon hon chi gay refused gia (E_TOO_LARGE bi
            # doi thanh "khong tim thay" o _sdk_resolve_vpath) — bo qua
            # nhu truoc caps-po (tran 64 MiB). Duong trich xuat streaming
            # (extraction.py) khong anh huong.
            if (info.is_dir() or info.file_size > lim.max_entry_bytes
                    or info.file_size > lim.max_memory_read_bytes):
                continue
            try:
                entry_data = zf.read(info.filename)
            except (KeyError, RuntimeError, zipfile.BadZipFile):
                continue
            if _u.detect_bundle(entry_data) is not None:
                try:
                    names = _sf_entry_names(entry_data, backend, lim)
                except PB3Error as exc:
                    if exc.code == "E_ENCRYPTED" and refused is not None:
                        refused.append({"unit": info.filename,
                                        "code": exc.code,
                                        "message": _err_text(exc)})
                    continue
                for n in names:
                    units.append(("%s::%s" % (info.filename, n), "bundle"))
            else:
                try:
                    _u.parse_serialized(entry_data, lim, where=info.filename)
                except PB3Error:
                    # M3: entry CSV/TSV localization — don vi kind "csv".
                    if is_localization_csv(entry_data, info.filename):
                        units.append((info.filename, "csv"))
                    continue
                units.append((info.filename, "assets"))
    if not units:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "khong tim thay don vi Unity (bundle/.assets) nao "
                       "trong file", where="rebuild")
    return units


def _unit_sf(source_bytes, vpath, backend, lim):
    """SerializedFile cua 1 don vi co '::entry' (de phan loai key)."""
    kind, unit_bytes, req_entry, _label = _plan._resolve_unit(
        bytes(source_bytes), vpath, backend, lim)
    if kind == "bundle":
        bundle = _u.parse_bundle(unit_bytes, backend, lim, where="bundle")
        sf_bytes = bundle.get_entry(req_entry)
    else:
        sf_bytes = unit_bytes
    return _u.parse_serialized(sf_bytes, lim, where=req_entry or "assets")


def _unit_sfs(source_bytes, vpath, backend, lim):
    """[SerializedFile] cua 1 don vi (de phan loai key + gom term MB).

    _resolve_unit yeu cau '::entry' nen khong dung duoc cho bundle
    tran: bundle khong co '::entry' thi doc TAT CA entry (moi entry 1
    SF, bo qua entry khong parse duoc). Don vi .assets -> 1 SF."""
    source_bytes = bytes(source_bytes)
    if vpath and "::" in vpath:
        return [_unit_sf(source_bytes, vpath, backend, lim)]
    if vpath:
        try:
            zf = zipfile.ZipFile(io.BytesIO(source_bytes))
        except zipfile.BadZipFile:
            unit_bytes = source_bytes
        else:
            try:
                unit_bytes = zf.read(vpath)
            except KeyError:
                raise PB3Error("E_INPUT_NOT_FOUND",
                               "khong thay don vi %s trong file" % vpath,
                               where="rebuild")
    else:
        unit_bytes = source_bytes
    if _u.detect_bundle(unit_bytes) is not None:
        bundle = _u.parse_bundle(unit_bytes, backend, lim,
                                 where=vpath or "bundle")
        out = []
        for e in bundle.entries:
            try:
                out.append(_u.parse_serialized(
                    bundle.get_entry(e.name), lim, where=e.name))
            except PB3Error:
                continue
        if not out:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "bundle %s khong co entry SerializedFile nao"
                           % (vpath or "<file>"), where="rebuild")
        return out
    return [_u.parse_serialized(unit_bytes, lim, where=vpath or "assets")]


def _collect_mb_info(sfs, source_locale="en", mb_index=None):
    """1 lan quet SF: tra (terms, values).

    terms: tap key MonoBehaviour co the xuat hien trong ban dich — term
      SharedTableData (ca dang phang + "Collection/Key"), ten term cua
      bang ngon ngu co cau truc, key generic cua _mb_generic_kv (ca 2 che
      do split), key StringTable "Collection/Key" tu mb_index (ke ca khi
      bang ST khong nam trong cac sf nay — de phan loai chinh xac).
    values: {key: {gia tri goc}} — term I2 o cot source_locale, entry ST o
      locale source_locale (key "Collection/Key" tra nguoc qua mb_index),
      gia tri generic (ca 2 che do split). Chi dung de PHAN LOAI unmatched:
      key ma moi gia tri goc da biet deu bang ban dich -> unchanged.
    """
    from sdk.extraction import _mb_generic_kv

    terms = set()
    values = {}
    rev = {}
    for coll, keys in (mb_index or {}).items():
        for k, mid in (keys or {}).items():
            rev[mid] = (coll, k)
            terms.add("%s/%s" % (coll, k))
    for sf in sfs:
        try:
            items = sf.monobehaviour_texts()
        except PB3Error:
            continue
        generic = []
        for item in items:
            kind = item.get("kind")
            if kind == "shared_table":
                coll = item.get("collection") or ""
                for term in (item.get("entries") or {}).values():
                    if isinstance(term, str):
                        terms.add(term)
                        # BUG#2: StringTable xuất dạng nhóm Collection/Key
                        # (vd Policies/ArcanePinnacle) — ép ngược phải
                        # nhận cả dạng nhóm, không chỉ term phẳng.
                        if coll:
                            terms.add("%s/%s" % (coll, term))
            elif kind == "language_source":
                langs = item.get("languages") or []
                col = None
                for idx, lang in enumerate(langs):
                    if lang.get("code") == source_locale:
                        col = idx
                        break
                for term in (item.get("terms") or []):
                    t = term.get("term")
                    if isinstance(t, str):
                        terms.add(t)
                    if col is None:
                        continue
                    vals = term.get("values") or []
                    if (isinstance(t, str) and col < len(vals)
                            and isinstance(vals[col], str)):
                        values.setdefault(t, set()).add(vals[col])
            elif kind == "string_table":
                for mid, v in (item.get("entries") or {}).items():
                    hit = rev.get(mid)
                    if hit is None:
                        continue
                    coll, k = hit
                    terms.add("%s/%s" % (coll, k))
                    if (item.get("locale") == source_locale
                            and isinstance(v, str)):
                        values.setdefault("%s/%s" % (coll, k), set()).add(v)
            elif kind == "generic":
                generic.append(item)
        if generic:
            for split in (False, True):
                try:
                    res = _mb_generic_kv(generic, split_languages=split)
                except Exception:
                    continue
                if split:
                    for lkv in (res.get("by_lang") or {}).values():
                        terms.update(lkv.keys())
                        for k, v in lkv.items():
                            values.setdefault(k, set()).add(v)
                else:
                    terms.update((res.get("kv") or {}).keys())
                    for k, v in (res.get("kv") or {}).items():
                        values.setdefault(k, set()).add(v)
    return terms, values


def _collect_mb_keys(sfs, mb_index=None):
    """Tap key MonoBehaviour co the xuat hien trong ban dich (xem
    _collect_mb_info). Chi dung de PHAN LOAI unmatched."""
    terms, _values = _collect_mb_info(sfs, mb_index=mb_index)
    return terms


def _collect_csv_values(source_bytes, backend, lim, source_locale):
    """{key: value} cot nguon cua moi file CSV doc lap trong input (M3).

    Quet _enumerate_units; don vi kind "csv" thi doc bytes truc tiep tu
    ZIP (hoac chinh file dau vao khi vpath None) roi lay source_map.
    Loi doc/parse thi bo qua don vi do (khong fail ca dot). Key trung
    giua nhieu file CSV: file xuat hien truoc thang."""
    out: Dict[str, str] = {}
    try:
        units = _enumerate_units(source_bytes, backend, lim)
    except PB3Error:
        return out
    from sdk.csvutil import csv_values as _csv_values
    data = bytes(source_bytes)
    zf = None
    try:
        if zipfile.is_zipfile(io.BytesIO(data)):
            zf = zipfile.ZipFile(io.BytesIO(data))
        for (vpath, kind) in units:
            if kind != "csv":
                continue
            try:
                if vpath is None:
                    csv_data, name = data, "csv"
                else:
                    csv_data = zf.read(vpath) if zf is not None else data
                    name = vpath.rsplit("/", 1)[-1]
                for k, v in _csv_values(csv_data, name,
                                        source_locale).items():
                    out.setdefault(k, v)
            except Exception:
                continue
    finally:
        if zf is not None:
            zf.close()
    return out


def _collect_mb_values(sfs, source_locale="en", mb_index=None):
    """{key: {gia tri goc}} cho moi key MonoBehaviour (xem _collect_mb_info).
    Chi dung de PHAN LOAI unmatched (unchanged)."""
    _terms, values = _collect_mb_info(sfs, source_locale=source_locale,
                                      mb_index=mb_index)
    return values


def _st_key_in_index(key, mb_index):
    """Key co phai "Collection/Key" nam trong mb_index (SharedTableData)?"""
    if "/" not in key:
        return False
    coll, bare = key.split("/", 1)
    keys = (mb_index or {}).get(coll)
    return isinstance(keys, dict) and bare in keys


def _classify_unmatched(keys, translations, sfs, mb_terms, mb_values=None,
                        mb_index=None, csv_values=None,
                        source_locale="en"):
    """Phan loai key khong vao plan: unchanged / non_string_value /
    not_json_dict / string_table_other_unit / monobehaviour / not_found.
    Tra (unmatched, unchanged). mb_values: {key: {gia tri goc}} — key ma
    moi gia tri goc da biet deu bang ban dich thi unchanged (khong can ep,
    khong phai loi). mb_index: key ST nam trong SharedTableData nhung bang
    ST khong co trong file dang chon -> string_table_other_unit.
    csv_values (M3): {key: value} cot nguon cua file CSV doc lap — uu
    tien cao nhat (nhu extraction: CSV la nguon dau). TextAsset CSV
    trong SF cung duoc doc o day (cot source_locale)."""
    dict_values = {}  # key -> (value, is_str)
    nondict_scripts = []
    for k, v in (csv_values or {}).items():
        dict_values[k] = v
    for sf in sfs:
        for _pid, _name, script in sf.text_assets():
            try:
                obj = json.loads(script)
            except (ValueError, UnicodeDecodeError):
                # M3: TextAsset CSV trong SF — lay gia tri cot nguon.
                try:
                    from sdk.csvutil import csv_values as _csv_values
                    from sdk.csvutil import is_csv_text_asset
                    if is_csv_text_asset(script, _name):
                        for k, v in _csv_values(
                                script.encode("utf-8"), _name,
                                source_locale).items():
                            dict_values.setdefault(k, v)
                except Exception:
                    pass
                continue
            if isinstance(obj, dict):
                for k, v in obj.items():
                    dict_values.setdefault(k, v)
            else:
                nondict_scripts.append(script)
    unmatched = []
    unchanged = 0
    for key in keys:
        if key in dict_values:
            cur = dict_values[key]
            if not isinstance(cur, str):
                unmatched.append({"key": key, "reason": "non_string_value"})
            elif cur == translations[key]:
                unchanged += 1
            else:
                # Co trong dict dang chuoi nhung khong vao plan: chi xay
                # ra khi plan bi tu choi o tang tren (da ghi refused).
                unmatched.append({"key": key, "reason": "not_found"})
            continue
        if any(key in s for s in nondict_scripts):
            unmatched.append({"key": key, "reason": "not_json_dict"})
        elif mb_values and key in mb_values and mb_values[key] \
                and all(v == translations[key] for v in mb_values[key]):
            unchanged += 1
        elif _st_key_in_index(key, mb_index):
            unmatched.append({"key": key, "reason": "string_table_other_unit"})
        elif key in mb_terms:
            unmatched.append({"key": key, "reason": "monobehaviour"})
        else:
            unmatched.append({"key": key, "reason": "not_found"})
    return unmatched, unchanged


# ------------------------------------------------------------- ap plan 1 don vi
def _apply_unit_plan(source_bytes, plan, backend, lim, progress_fn=None,
                     progress_base=0, progress_total=0):
    """Ap 1 plan (1 don vi) len bytes, tra (unit_bytes_moi, kind, label,
    mode, size_before). Dung chung executor cua plan_apply
    (_apply_entry_changes -> apply_patch). Entry op REBUILD_ALL bi tu
    choi rieng (khoa M4) — o day plan do build_plan tu lap nen khong
    bao gio co op do, chot chi de phong plan la."""
    vpath = plan.get("unit_entry")
    kind, unit_bytes, req_entry, label = _plan._resolve_unit(
        bytes(source_bytes), vpath, backend, lim)
    size_before = len(unit_bytes)
    cur = bytes(unit_bytes)
    last_mode = "REBUILD_SECTION"
    done = progress_base
    for entry in plan["entries"]:
        if entry.get("op") == "REBUILD_ALL":
            raise PB3Error(
                "E_WRITE_DISABLED",
                "op REBUILD_ALL can maturity M4 (hien %s) nen chua ap "
                "duoc; doi op thanh REBUILD_SECTION roi ap lai."
                % _u.UnityBackend.MATURITY, where=entry.get("asset_path"))
        n_changes = len(entry["changes"])

        def _pf(d, t, _base=done, _n=n_changes):
            if progress_fn is not None:
                try:
                    progress_fn(_base + d, progress_total or _n)
                except Exception:
                    pass

        cur, last_mode = _plan._apply_entry_changes(
            kind, cur, req_entry, entry, backend, lim, progress_fn=_pf)
        done += n_changes
    return cur, kind, label, last_mode, size_before


def _write_rebuild_outputs(source_name, patched_units, out_dir, zip_path,
                           src_path=None):
    """Ghi don vi da patch (mau plan._write_outputs): 1 file -> dest,
    nhieu file -> zip. Co chot dest != source."""
    if not patched_units:
        return [], None, None
    if src_path:
        src_real = os.path.realpath(str(src_path))
    else:
        src_real = None
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    names = []
    for (kind, label, _key), data in patched_units:
        name = _plan._output_name(source_name, kind, label)
        dest = os.path.join(out_dir, name)
        n = 1
        base, ext = os.path.splitext(name)
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(out_dir, "%s_%d%s" % (base, n, ext))
        if src_real is not None and os.path.realpath(dest) == src_real:
            raise PB3Error("E_CONFIG_INVALID",
                           "khong ghi de file goc: dich %s trung nguon"
                           % dest, where=dest)
        with open(dest, "wb") as fh:
            fh.write(data)
        names.append(os.path.basename(dest))
    if len(names) == 1:
        return names, os.path.join(out_dir, names[0]), None
    if os.path.exists(zip_path):
        os.remove(zip_path)
    if src_real is not None and os.path.realpath(zip_path) == src_real:
        raise PB3Error("E_CONFIG_INVALID",
                       "khong ghi de file goc: zip dich trung nguon",
                       where=zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.write(os.path.join(out_dir, name), name)
    return names, None, zip_path


# ------------------------------------------------------------- dieu phoi (E)
def build_rebuild_preview(source_bytes, *, source_name, translations,
                          provenance=None, choices=None, backend=None,
                          limits=None, progress_fn=None, src_path=None,
                          force_streaming=False, source_locale="en",
                          mode="REBUILD_SECTION", mb_index=None):
    """Xem truoc (E): nhom -> plan moi don vi -> ap thu trong RAM (hoac
    tinh replacements cho duong streaming) -> phan loai key khong ep
    duoc. Khong ghi file nao. Ket qua cung shape voi apply_rebuild.
    source_locale: cot ngon ngu goc de ghi vao MonoBehaviour (mac dinh
    "en"). mode: che do ghi mac dinh cho entry MB/TextAsset
    ("REBUILD_SECTION" mac dinh; "PATCH_INPLACE" thi entry nao vua se
    noi tai cho, dai hon thi leo thang REBUILD_SECTION). mb_index:
    {collection: {key: m_Id}} tu trich xuat IPA day du (tuy chon) — gop
    voi index tu file dang chon (file thang khi trung) de phan loai key
    StringTable o don vi khac."""
    return apply_rebuild(
        source_bytes, source_name=source_name, translations=translations,
        provenance=provenance, choices=choices, dry_run=True, confirm=False,
        backend=backend, limits=limits, progress_fn=progress_fn,
        src_path=src_path, force_streaming=force_streaming,
        source_locale=source_locale, mode=mode, mb_index=mb_index)


def apply_rebuild(source_bytes, *, source_name, translations,
                  provenance=None, choices=None, dry_run=True, confirm=False,
                  backend=None, limits=None, progress_fn=None, src_path=None,
                  force_streaming=False, out_dir=REBUILD_OUT_DIR,
                  zip_path=REBUILD_ZIP_PATH, source_locale="en",
                  mode="REBUILD_SECTION", mb_index=None):
    """Dieu phoi (E): preview (dry_run) hoac ap that (dry_run=False +
    confirm=True). Loi toan cuc raise (bridge tra _err); loi tung don
    vi vao refused, khong fail ca dot."""
    if not dry_run and not confirm:
        raise PB3Error("E_CONFIG_INVALID",
                       "ghi that can confirm=True (dry_run=False ma thieu "
                       "xac nhan) — chay preview truoc roi moi ghi.",
                       where="rebuild")
    _validate_translations(translations)
    lim = limits or Limits()
    be = backend or _u.UnityBackend("NATIVE")
    source_bytes = bytes(source_bytes)
    grouped = group_by_unit(translations, provenance, choices)
    groups = grouped["groups"]
    warnings: List[str] = list(grouped["warnings"])

    # Duong streaming? Chi khi file dau vao CHINH LA bundle doc lap.
    streaming = False
    if src_path is not None and force_streaming:
        streaming = _u.detect_bundle(source_bytes) is not None
    elif src_path is not None:
        try:
            fsize = os.path.getsize(src_path)
        except OSError:
            fsize = len(source_bytes)
        streaming = (fsize >= STREAMING_MIN_BYTES
                     and _u.detect_bundle(source_bytes) is not None)
    if streaming:
        return _apply_streaming(
            source_bytes, source_name=source_name, grouped=grouped,
            dry_run=dry_run, be=be, lim=lim, progress_fn=progress_fn,
            src_path=src_path, out_dir=out_dir, zip_path=zip_path,
            warnings=warnings, mode=mode, source_locale=source_locale)

    units_out: List[Dict[str, Any]] = []
    refused: List[Dict[str, str]] = []
    patched: List[Tuple[Tuple[str, str, str], bytes]] = []
    planned_keys = set()
    sfs_by_unit: Dict[str, Any] = {}
    total_changes = 0
    # M1 (G1): build mb_index 1 lan truoc Vong 1 tu SF cua TAT CA don vi
    # (SharedTableData thuong nam o bundle khac StringTable — vd 9 Kings:
    # shared o localization-assets-shared, ST o localization-string-tables
    # — nen phai quet truoc, khong doi toi khi gap tung don vi). Liet ke
    # that bai thi mb_index = {} (ST chi ghi entry da co, khong chen
    # duoc key thieu; LS khong can mb_index). mb_index ngoai (tu trich
    # xuat IPA day du) gop them: du lieu cua file dang chon thang khi
    # trung (collection, key) — de phan loai key ST o don vi khac khi chi
    # chon 1 file .assets le.
    mb_index_self: Dict[str, Dict[str, int]] = {}
    try:
        for (vpath, _kind) in _enumerate_units(source_bytes, be, lim):
            key = vpath or "<file>"
            if key not in sfs_by_unit:
                try:
                    sfs_by_unit[key] = _unit_sfs(source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
    except PB3Error:
        pass
    try:
        mb_index_self = _plan.build_mb_shared_index(
            [sf for lst in sfs_by_unit.values() for sf in lst])
    except Exception:
        mb_index_self = {}
    if mb_index:
        merged_index: Dict[str, Dict[str, int]] = {}
        for coll, keys in (mb_index or {}).items():
            merged_index[coll] = dict(keys or {})
        for coll, keys in (mb_index_self or {}).items():
            merged_index.setdefault(coll, {}).update(keys or {})
        mb_index = merged_index
    else:
        mb_index = mb_index_self
    # Vong 1: lap plan moi don vi.
    unit_plans = []  # (group_unit, vpath, kind, plan)
    for group_unit in sorted(groups, key=lambda g: "" if g is None else str(g)):
        grp = groups[group_unit]
        try:
            unit_list = _expand_group_units(
                source_bytes, group_unit, be, lim, source_name=source_name,
                refused=refused)
        except PB3Error as exc:
            refused.append({"unit": group_unit or "<quet tat ca>",
                            "code": exc.code,
                            "message": _err_text(exc)})
            continue
        for (vpath, _kind) in unit_list:
            # Thu SF cho MOI don vi da mo rong (ke ca don vi khong lap
            # duoc plan): phan loai key thua can term MB cua tat ca don
            # vi — neu khong, key MB o don vi khong co TextAsset JSON
            # se bi gan nham not_found thay vi monobehaviour (G1).
            if (vpath or "<file>") not in sfs_by_unit:
                try:
                    sfs_by_unit[vpath or "<file>"] = _unit_sfs(
                        source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
            try:
                plan = _plan.build_plan(
                    source_bytes, grp["translations"],
                    source_bundle=source_name, entry=vpath,
                    mode=mode, backend=be, limits=lim,
                    source_locale=source_locale, mb_index=mb_index)
            except ValueError:
                continue  # don vi nay khong co key nao trung -> bo qua lang le o day, key se phan loai sau
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
    # Vong 2: ap tung plan (ke ca dry-run: ap trong RAM de verify + do size).
    done_changes = 0
    for group_unit, vpath, plan in unit_plans:
        unit_warnings: List[str] = []
        try:
            cur, kind, label, mode, size_before = _apply_unit_plan(
                source_bytes, plan, be, lim, progress_fn=progress_fn,
                progress_base=done_changes, progress_total=total_changes)
        except PB3Error as exc:
            refused.append({"unit": vpath or group_unit or "<file>",
                            "code": exc.code,
                            "message": _err_text(exc)})
            for e in plan["entries"]:
                for c in e["changes"]:
                    planned_keys.discard(c["key"])
            continue
        done_changes += sum(len(e["changes"]) for e in plan["entries"])
        for e in plan["entries"]:
            unit_warnings.extend(e.get("warnings") or [])
        units_out.append({
            "unit": vpath or (group_unit or "<file>"), "kind": kind,
            "plan_id": plan["id"], "entries": len(plan["entries"]),
            "changes": sum(len(e["changes"]) for e in plan["entries"]),
            "mode": mode, "streaming": False,
            "size_before": int(size_before), "size_after": int(len(cur)),
            "warnings": unit_warnings, "unchanged": 0,
            # M2: ten file da ghi cho don vi nay (gan sau khi ghi o duoi;
            # None khi dry-run vi chua ghi file nao) — buoc 5 dung de doi
            # chieu don vi -> file -> zip entry trong IPA goc.
            "output": None,
        })
        patched.append(((kind, label,
                         "%s|%s" % (vpath, plan["id"])), cur))
        try:
            sfs_by_unit[vpath or "<file>"] = _unit_sfs(
                source_bytes, vpath, be, lim)
        except PB3Error:
            pass
    # Phan loai key chua vao plan (quet sf cua don vi da thay + term MB).
    # Term StringTable can join SharedTable o bundle KHAC (vd 9 Kings:
    # shared_table nam o localization-assets-shared bundle, string
    # table o localization-string-tables bundle): voi file ZIP/IPA thi
    # gom them SF cua TAT CA don vi da liet ke, khong chi don vi cua
    # nhom provenance — neu khong term se thieu va key MB bi gan nham
    # not_found.
    if zipfile.is_zipfile(io.BytesIO(source_bytes)):
        try:
            for (vpath, _kind) in _enumerate_units(source_bytes, be, lim):
                if (vpath or "<file>") in sfs_by_unit:
                    continue
                try:
                    sfs_by_unit[vpath or "<file>"] = _unit_sfs(
                        source_bytes, vpath, be, lim)
                except PB3Error:
                    pass
        except PB3Error:
            pass
    try:
        all_sfs = [sf for lst in sfs_by_unit.values() for sf in lst]
        mb_terms, mb_values = _collect_mb_info(
            all_sfs, source_locale=source_locale, mb_index=mb_index)
    except Exception:
        mb_terms = set()
        mb_values = {}
    leftover = [k for k in translations if k not in planned_keys]
    unmatched, unchanged = _classify_unmatched(
        leftover, translations, all_sfs, mb_terms, mb_values=mb_values,
        mb_index=mb_index,
        csv_values=_collect_csv_values(source_bytes, be, lim, source_locale),
        source_locale=source_locale)
    for u in unmatched:
        u["unit"] = None
    # Gan unit cho unmatched theo nhom provenance (de UI hien dung cho).
    for grp_unit, grp in groups.items():
        for u in unmatched:
            if u["key"] in grp["translations"]:
                u["unit"] = grp_unit
    unmatched_total = len(unmatched)
    unmatched_out = unmatched[:UNMATCHED_CAP]
    outputs: List[str] = []
    dest = None
    zpath = None
    if not dry_run and patched:
        outputs, dest, zpath = _write_rebuild_outputs(
            source_name, patched, out_dir, zip_path, src_path=src_path)
        # M2: gan ten file da ghi cho tung don vi (cung thu tu voi
        # `patched`/`units_out` — cung vong lap o tren).
        for u, name in zip(units_out, outputs):
            u["output"] = name
    return {
        "ok": True, "dry_run": bool(dry_run),
        "units": units_out,
        "totals": {
            "keys": len(translations),
            "changed": sum(u["changes"] for u in units_out),
            "units": len(units_out),
            "unmatched": unmatched_total,
            "unchanged": unchanged,
            "conflicts": grouped["conflicts_total"],
            "refused": len(refused),
        },
        "unmatched": unmatched_out,
        "unmatched_total": unmatched_total,
        "unmatched_truncated": unmatched_total > UNMATCHED_CAP,
        "conflicts": grouped["conflicts"],
        "conflicts_total": grouped["conflicts_total"],
        "conflicts_truncated": grouped["conflicts_truncated"],
        "refused": refused,
        "outputs": outputs, "dest": dest, "zip_path": zpath,
        "warnings": warnings, "scan_all": grouped["scan_all"],
        "note": "Output la don vi nho nhat da patch (bundle/.assets), "
                "KHONG phai IPA dong goi lai. Thay file nay vao game va "
                "can re-sign goi ngoai thi game moi nhan. Muon IPA cai "
                "duoc, dung buoc 5 'Dong goi lai IPA' trong tab Mod.",
    }


def _err_text(exc):
    text = str(exc)
    detail = getattr(exc, "detail", "") or ""
    if detail and detail not in text:
        text = "%s (%s)" % (text, detail)
    return text


def _apply_streaming(source_bytes, *, source_name, grouped, dry_run, be, lim,
                     progress_fn, src_path, out_dir, zip_path, warnings,
                     mode="REBUILD_SECTION", source_locale="en"):
    """Duong streaming cho bundle .unity3d doc lap lon: liet ke entry
    SerializedFile bang stream (khong om payload) -> lap plan moi entry
    tu bytes entry (kind assets) -> patch tung entry CAB trong RAM
    (entry <=128MB) -> rebuild_bundle_streaming -> verify after_sha
    bang cach doc lai entry da patch.

    Gioi han rieng cho duong nay: block/entry toi max_memory_read_bytes
    (128MB) thay vi max_entry_bytes (64MB) — RAM dinh ~2x entry lon
    nhat nhu docstring stream_bundle_entries; duong RAM giu 64MB.
    Entry don >128MB -> E_TOO_LARGE + huong iSH (kiem tu layout truoc
    khi doc payload, tranh giu entry khung trong RAM)."""
    groups = grouped["groups"]
    refused: List[Dict[str, str]] = []
    # Hop tat ca ban dich theo CAB: lap plan moi CAB voi hop key.
    all_translations: Dict[str, str] = {}
    for grp in groups.values():
        all_translations.update(grp["translations"])
    lim_stream = lim.with_overrides(
        max_entry_bytes=lim.max_memory_read_bytes)

    def _refuse_early(exc):
        refused.append({"unit": source_name, "code": exc.code,
                        "message": _err_text(exc)})
        return _streaming_result(
            source_name, grouped, [], refused, [], None, None, warnings,
            all_translations, {}, 0, dry_run)

    # Pass 0: layout (khong payload) — tu choi som ma hoa/dinh dang
    # la/entry >128MB truoc khi doc payload.
    try:
        with open(src_path, "rb") as fh:
            layout = _u._read_bundle_layout(fh, be, lim_stream,
                                            where="bundle")
    except PB3Error as exc:
        return _refuse_early(exc)
    oversized = [e for e in layout.entries
                 if e.size > lim.max_memory_read_bytes]
    if oversized:
        return _refuse_early(PB3Error(
            "E_TOO_LARGE",
            "entry %s %d byte > gioi han 128MB cho duong streaming; "
            "dung ban iSH voi gioi han cao hon cho bundle nay."
            % (oversized[0].name, oversized[0].size), where="bundle"))
    # Liet ke entry SerializedFile bang streaming (khong om payload).
    sf_entries: List[Tuple[str, bytes, Any]] = []
    try:
        for name, data in _u.stream_bundle_entries(
                src_path, be, lim_stream, where="bundle"):
            try:
                sf = _u.parse_serialized(data, lim_stream, where=name)
            except PB3Error:
                continue
            sf_entries.append((name, bytes(data), sf))
    except PB3Error as exc:
        return _refuse_early(exc)
    if not sf_entries:
        return _refuse_early(PB3Error(
            "E_INPUT_NOT_FOUND",
            "bundle khong co entry SerializedFile nao — khong ep ban "
            "dich duoc", where="bundle"))
    plans_by_entry: Dict[str, Any] = {}
    entry_bytes_by_name: Dict[str, bytes] = {}
    planned_keys = set()
    total_changes = 0
    # M1 (G1): mb_index cho duong streaming (SharedTableData co the nam o
    # entry khac StringTable trong cung bundle) — de ST chen key thieu +
    # phan loai string_table_other_unit.
    try:
        mb_index_stream = _plan.build_mb_shared_index(
            [sf for (_n, _d, sf) in sf_entries])
    except Exception:
        mb_index_stream = {}
    for (vpath, entry_bytes, _sf) in sf_entries:
        try:
            plan = _plan.build_plan(
                entry_bytes, all_translations, source_bundle=source_name,
                entry=None, mode=mode, backend=be,
                limits=lim_stream, source_locale=source_locale,
                mb_index=mb_index_stream)
        except ValueError:
            continue  # entry nay khong co key nao trung
        except PB3Error as exc:
            refused.append({"unit": vpath, "code": exc.code,
                            "message": _err_text(exc)})
            continue
        plans_by_entry[vpath] = plan
        entry_bytes_by_name[vpath] = entry_bytes
        total_changes += sum(len(e["changes"]) for e in plan["entries"])
        for e in plan["entries"]:
            for c in e["changes"]:
                planned_keys.add(c["key"])
    # Patch tung entry CAB (kind=assets tren bytes entry).
    replacements: Dict[str, bytes] = {}
    entry_sizes: Dict[str, Tuple[int, int]] = {}
    done_changes = 0
    for vpath, plan in plans_by_entry.items():
        try:
            entry_bytes = entry_bytes_by_name[vpath]
            cur = bytes(entry_bytes)
            for entry in plan["entries"]:
                if entry.get("op") == "REBUILD_ALL":
                    raise PB3Error("E_WRITE_DISABLED",
                                   "op REBUILD_ALL bi khoa (M4).",
                                   where=entry.get("asset_path"))
                n_changes = len(entry["changes"])

                def _pf(d, t, _base=done_changes, _n=n_changes):
                    if progress_fn is not None:
                        try:
                            progress_fn(_base + d,
                                        total_changes or _n)
                        except Exception:
                            pass

                cur, _mode = _plan._apply_entry_changes(
                    "assets", cur, None, entry, be, lim_stream,
                    progress_fn=_pf)
                done_changes += n_changes
        except PB3Error as exc:
            refused.append({"unit": vpath, "code": exc.code,
                            "message": _err_text(exc)})
            for e in plan["entries"]:
                for c in e["changes"]:
                    planned_keys.discard(c["key"])
            continue
        replacements[vpath] = cur
        entry_sizes[vpath] = (len(entry_bytes), len(cur))
    size_before = len(source_bytes)
    size_after = None
    dest = None
    zpath = None
    outputs: List[str] = []
    stream_warnings: List[str] = []
    if replacements and not dry_run:
        out_name = _plan._output_name(source_name, "bundle", "bundle")
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, out_name)
        if os.path.realpath(dest) == os.path.realpath(str(src_path)):
            raise PB3Error("E_CONFIG_INVALID",
                           "khong ghi de file goc: dich trung nguon",
                           where=dest)
        try:
            info = _u.rebuild_bundle_streaming(
                src_path, dest, replacements, backend=be, limits=lim_stream,
                progress_fn=None, on_entry=None)
        except PB3Error as exc:
            refused.append({"unit": source_name, "code": exc.code,
                            "message": _err_text(exc)})
            return _streaming_result(
                source_name, grouped, [], refused, [], None, None, warnings,
                all_translations, planned_keys, 0, dry_run)
        stream_warnings.extend(info.get("warnings") or [])
        size_after = int(info["size_after"])
        outputs = [os.path.basename(dest)]
        # Verify after_sha: doc lai tung entry da patch tu file dich.
        try:
            _verify_streaming_after(dest, plans_by_entry, replacements,
                                    be, lim_stream)
        except PB3Error as exc:
            try:
                os.unlink(dest)
            except OSError:
                pass
            refused.append({"unit": source_name, "code": exc.code,
                            "message": _err_text(exc)})
            return _streaming_result(
                source_name, grouped, [], refused, [], None, None, warnings,
                all_translations, set(), 0, dry_run)
    elif replacements and dry_run:
        # Uoc tinh (chua ghi): tru payload cu, cong payload moi; bo qua
        # chenh lech nen — ghi ro la uoc tinh.
        delta = sum(n - o for (o, n) in entry_sizes.values())
        size_after = size_before + delta
        stream_warnings.append(
            "size_after la UOC TINH (chua ghi that, bo qua chenh lech "
            "nen); kich thuoc that co sau khi ghi.")
    units_out = []
    if plans_by_entry and replacements:
        unit_warnings: List[str] = list(stream_warnings)
        for plan in plans_by_entry.values():
            for e in plan["entries"]:
                unit_warnings.extend(e.get("warnings") or [])
        units_out.append({
            "unit": source_name, "kind": "bundle",
            "plan_id": next(iter(plans_by_entry.values()))["id"],
            "entries": sum(len(p["entries"]) for p in plans_by_entry.values()),
            "changes": sum(len(e["changes"]) for p in plans_by_entry.values()
                           for e in p["entries"]),
            "mode": "REBUILD_SECTION", "streaming": True,
            "size_before": int(size_before),
            "size_after": size_after,
            "warnings": unit_warnings, "unchanged": 0,
            # M2: ten file da ghi (None khi dry-run) — xem ghi chu o
            # duong RAM tren.
            "output": outputs[0] if outputs else None,
        })
    return _streaming_result(
        source_name, grouped, units_out, refused, outputs, dest, zpath,
        warnings, all_translations, planned_keys,
        len(plans_by_entry), dry_run,
        sfs=[sf for (_n, _d, sf) in sf_entries],
        mb_index=mb_index_stream, source_locale=source_locale)


def _streaming_result(source_name, grouped, units_out, refused, outputs,
                      dest, zpath, warnings, all_translations, planned_keys,
                      _n_plans, dry_run, sfs=None, mb_index=None,
                      source_locale="en"):
    leftover = [k for k in all_translations if k not in planned_keys]
    unmatched: List[Dict[str, Any]] = []
    unchanged = 0
    if leftover and sfs:
        try:
            mb_terms, mb_values = _collect_mb_info(
                sfs, source_locale=source_locale, mb_index=mb_index)
        except Exception:
            mb_terms = set()
            mb_values = {}
        unmatched, unchanged = _classify_unmatched(
            leftover, all_translations, sfs, mb_terms, mb_values=mb_values,
            mb_index=mb_index)
        for u in unmatched:
            u["unit"] = source_name
    elif leftover:
        unmatched = [{"key": k, "reason": "not_found", "unit": source_name}
                     for k in leftover]
    return {
        "ok": True, "dry_run": bool(dry_run),
        "units": units_out,
        "totals": {
            "keys": len(all_translations),
            "changed": sum(u["changes"] for u in units_out),
            "units": len(units_out),
            "unmatched": len(unmatched),
            "unchanged": unchanged,
            "conflicts": grouped["conflicts_total"],
            "refused": len(refused),
        },
        "unmatched": unmatched[:UNMATCHED_CAP],
        "unmatched_total": len(unmatched),
        "unmatched_truncated": len(unmatched) > UNMATCHED_CAP,
        "conflicts": grouped["conflicts"],
        "conflicts_total": grouped["conflicts_total"],
        "conflicts_truncated": grouped["conflicts_truncated"],
        "refused": refused,
        "outputs": outputs, "dest": dest, "zip_path": zpath,
        "warnings": warnings, "scan_all": grouped["scan_all"],
        "note": "Output la don vi nho nhat da patch (bundle/.assets), "
                "KHONG phai IPA dong goi lai. Thay file nay vao game va "
                "can re-sign goi ngoai thi game moi nhan. Muon IPA cai "
                "duoc, dung buoc 5 'Dong goi lai IPA' trong tab Mod.",
    }


def _verify_streaming_after(dst_path, plans_by_entry, replacements, be, lim):
    """Verify sau ghi streaming: entry da patch doc lai duoc SerializedFile
    khop after_sha + dict dung (kiem o tang plan, nhu _apply_entry_changes)."""
    for vpath, plan in plans_by_entry.items():
        if vpath not in replacements:
            continue
        entry_bytes = None
        for name, data in _u.stream_bundle_entries(dst_path, be, lim,
                                                   where="bundle::verify"):
            if name == vpath:
                entry_bytes = data
                break
        if entry_bytes is None:
            raise PB3Error("E_VERIFY_FAILED",
                           "entry %s khong doc lai duoc sau khi ghi streaming."
                           % vpath, where=vpath)
        sf = _u.parse_serialized(entry_bytes, lim, where=vpath)
        for entry in plan["entries"]:
            _pid, _name, script = sf.find_text_asset(
                path_id=int(entry["object"]))
            if _sha(script.encode("utf-8")) != entry["after_sha"]:
                raise PB3Error(
                    "E_VERIFY_FAILED",
                    "entry %s: script sau khi ghi streaming khac after_sha."
                    % entry["id"], where=entry["asset_path"])
            obj = json.loads(script)
            for ch in entry["changes"]:
                if not isinstance(obj, dict) or obj.get(ch["key"]) != ch["after"]:
                    raise PB3Error(
                        "E_VERIFY_FAILED",
                        "entry %s: key %r sau khi ghi la %r, khac %r."
                        % (entry["id"], ch["key"],
                           obj.get(ch["key"]) if isinstance(obj, dict) else None,
                           ch["after"]), where=entry["asset_path"])
