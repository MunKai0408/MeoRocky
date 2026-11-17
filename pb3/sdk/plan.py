"""Plan JSON cho Mod — planner/executor tach roi (muc 8, C5).

Thay cho menu [1] tren iSH: planner quet bundle va lap plan, executor ap
plan. Moi entry doc lap theo asset (1 TextAsset) nen chia shard cho N
worker duoc — entry nao cung co du thong tin de ap rieng (vpath, path_id,
changes, before_sha/after_sha).

Model plan (theo phan tich Joseph 13:52, muc C5):
    {"version": 1, "id": "...", "source_bundle": "...",
     "unit_entry": "<vpath toi don vi>", "unit_kind": "bundle"|"assets",
     "mode": "REBUILD_SECTION", "rebuild_all": false,
     "created_at": "<ISO UTC>",
     "entries": [{"id", "asset_path", "asset_type": "TextAsset",
                  "op": "REBUILD_SECTION", "object": <path_id>,
                  "entry": <ten entry trong bundle hoac None>,
                  "vpath": "<vpath day du de giai quyet lai don vi>",
                  "changes": [{"key", "before", "after"}],
                  "preview_before": {key: before}, "preview_after": {key: after},
                  "before_sha": "...", "after_sha": "...",
                  "selected": true, "warnings": [...]}],
     "skipped": [{"asset_path", "reason"}]}

- translations: dict key -> gia tri moi (DUNG dinh dang dau ra cua tab
  Dich: {"hello": "Xin chao"}). Gia tri cu lay tu chinh asset (khong can
  user nhap lai), nen chi asset nao co key do moi duoc dua vao plan.
- Chi TextAsset JSON dang dict moi vao plan. Asset khong phai JSON, JSON
  khong phai dict, hoac khong co key nao trung thi vao `skipped` kem ly do
  (khong bien mat lang le).
- M1 (G1): MonoBehaviour/StringTable cung vao plan (asset_type
  "MonoBehaviour", op REBUILD_SECTION): bang ngon ngu I2 (byte-walk) va
  StringTable co type tree (m_TableData[].m_Localized); key StringTable
  thieu trong bang thi CHEN entry moi (field_path "m_TableData[+]" kem
  "element"). before_sha/after_sha = sha object bytes (khong phai script).
  Generic MonoBehaviour (scan path / field le) chua vao plan o v1.
- `op` mac dinh REBUILD_SECTION (duong patch da kiem chung qua mod_patch /
  apply_patch, tu leo tu PATCH_INPLACE khi chuoi moi dai hon). Ghi CHU:
  op REBUILD_ALL (che do ghi cua Unity) VAN BI KHOA tren web — can
  maturity M4 (hien M2) + xac nhan; executor tu choi entry do kem loi ro
  rang chu khong tu ha xuong. `rebuild_all` o muc plan KHAC: co true thi
  executor ap TOAN BO entry (bo qua tick tung dong) — do moi la
  "REBUILD_ALL" o tang plan (checkbox tren UI).
- Dry-run: chi tinh toan + verify, KHONG ghi file nao, KHONG ghi backup.
- Resume: checkpoint {"plan_id", "completed": [entry id]} — entry da xong
  duoc bo qua o lan chay sau. `stop_after=N` dung de mo phong dung giua
  chung (ap N entry roi dung, tra checkpoint).
- Undo: khoi phuc tu ban sao luu don vi goc
  (/tmp/plan_backup/<plan_id>/<unit>.bin, chi ton tai trong phien worker —
  Pyodide FS nam trong RAM trang) ; neu khong con backup thi patch nguoc
  (new -> old) roi verify before_sha. Undo doi hoi file dau vao la file
  DA AP plan (hoac file goc neu dung backup). Plan JSON mot minh khong
  du de undo neu khong co backup/file goc — ghi ro trong ket qua.

Chi thu vien chuan, Python >= 3.9. Khong goi mang. Khong dong goi lai IPA:
output la don vi nho nhat da patch (bundle/.assets) nhu mod_patch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.limits import Limits
from sdk import unity as _u

PLAN_VERSION = 1
PLAN_BACKUP_ROOT = "/tmp/plan_backup"
PLAN_OUT_DIR = "/tmp/plan_out"
PLAN_ZIP_PATH = "/tmp/plan.zip"
# REBUILD_ALL (che do ghi Unity) can maturity M4; web hien M2 (xem
# sdk/unity.py UnityBackend.MATURITY) nen executor phai tu choi ro rang.
_PLAN_OPS = ("PATCH_INPLACE", "REBUILD_SECTION", "REBUILD_ALL")

_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|\%[sdif]|\$[a-zA-Z_][a-zA-Z0-9_]*")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _placeholders(text: str):
    return set(_PLACEHOLDER_RE.findall(text or ""))


def _top_level_value_spans(script: str):
    """[(key, value_start, value_end)] cho moi member cap cao nhat cua
    JSON object, theo thu tu xuat hien trong script. Span cua value bao
    gom ca dau ngoac kep khi value la chuoi. Dung JSONDecoder that de
    ton trong escape/khoang trang/lon nhau — khong dung regex."""
    dec = json.JSONDecoder()
    n = len(script)

    def _ws(i):
        while i < n and script[i] in " \t\r\n":
            i += 1
        return i

    idx = _ws(0)
    if idx >= n or script[idx] != "{":
        raise ValueError("script khong phai JSON object")
    idx = _ws(idx + 1)
    if idx < n and script[idx] == "}":
        return []
    spans = []
    while True:
        key, end = dec.raw_decode(script, _ws(idx))
        if not isinstance(key, str):
            raise ValueError("key JSON khong phai chuoi")
        idx = _ws(end)
        if idx >= n or script[idx] != ":":
            raise ValueError("thieu ':' sau key JSON")
        val_start = _ws(idx + 1)
        _val, end = dec.raw_decode(script, val_start)
        spans.append((key, val_start, end))
        idx = _ws(end)
        if idx < n and script[idx] == ",":
            idx += 1
            continue
        if idx < n and script[idx] == "}":
            return spans
        raise ValueError("JSON object ket thuc khong hop le")


def _simulate_script(script_b: bytes, changes) -> bytes:
    """Mo phong ap change THEO KEY (khong phai gia tri tran): tim span
    cua literal gia tri ung voi key trong script goc, kiem tra gia tri
    giai ma == before, thay bang literal moi. Giu nguyen moi byte khac
    (khoang trang, thu tu key, escape). Executor phai ap dung dung
    phep bien doi nay thi after_sha moi khop. Gia tri rong, gia tri
    trung nhau giua cac key, gia tri xuat hien o cho khac trong script
    deu ap dung dung (ban cu thay chuoi tran xuat hien dau tien nen
    gia tri "" khong bao gio patch duoc va gia tri trung de patch
    nham cho). Key trung lap trong JSON: lay lan xuat hien CUOI co gia
    tri == before (giong json.loads). Khong xac dinh duoc span thi
    raise ValueError (build_plan se bo qua asset do kem ly do)."""
    script = script_b.decode("utf-8")
    spans = _top_level_value_spans(script)
    by_key = {}
    for (k, s, e) in spans:
        by_key.setdefault(k, []).append((s, e))
    dec = json.JSONDecoder()
    edits = []
    used = set()
    for ch in changes:
        picked = None
        for (s, e) in reversed(by_key.get(ch["key"], [])):
            if (s, e) in used:
                continue
            val, _end = dec.raw_decode(script, s)
            if val == ch["before"]:
                picked = (s, e)
                break
        if picked is None:
            raise ValueError(
                "khong xac dinh duoc vi tri gia tri cua key %r "
                "(before %r) trong script" % (ch["key"], ch["before"]))
        used.add(picked)
        edits.append((picked[0], picked[1],
                      json.dumps(ch["after"], ensure_ascii=False)))
    out = []
    pos = 0
    for (s, e, lit) in sorted(edits):
        out.append(script[pos:s])
        out.append(lit)
        pos = e
    out.append(script[pos:])
    return "".join(out).encode("utf-8")


def _unit_key(vpath, kind, req_entry):
    return "%s|%s|%s" % (vpath or "<file>", kind, req_entry or "")


def _backup_name(entries) -> str:
    """Ten file backup cua 1 don vi: bam theo id cac entry trong don vi
    (on dinh theo plan), KHONG theo vpath — user co the nap file da patch
    duoi dang bundle tran (vpath khac) thi undo van tim thay backup."""
    raw = "|".join(sorted(e["id"] for e in entries))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12] + ".bin"


def _plan_id(source_bundle, vpath, translations) -> str:
    raw = json.dumps([source_bundle, vpath,
                      sorted(translations.items())],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _resolve_unit(source_bytes, vpath, backend, limits):
    """Giai quyet don vi nhu mod_patch: tra (kind, unit_bytes, req_entry,
    label)."""
    from sdk.batch import _sdk_resolve_vpath

    return _sdk_resolve_vpath(source_bytes, vpath, backend, limits)


def _read_unit_text_assets(kind, unit_bytes, req_entry, backend, limits):
    """[(path_id, name, script)] cua don vi."""
    if kind == "bundle":
        bundle = _u.parse_bundle(unit_bytes, backend, limits, where="bundle")
        assets_bytes = bundle.get_entry(req_entry)
        sf = _u.parse_serialized(assets_bytes, limits, where=req_entry)
    elif kind == "assets":
        sf = _u.parse_serialized(unit_bytes, limits, where="assets")
    else:
        raise PB3Error("E_CONFIG_INVALID", "kind phai la bundle|assets",
                       where=kind)
    warnings: List[str] = []
    return sf.text_assets(warnings=warnings), warnings


def build_mb_shared_index(sfs):
    """{collection: {key: m_Id}} tu moi SharedTableData trong sfs.

    Can cho StringTable: tra nguoc m_Id -> (collection, key) de dat ten
    key "Collection/Key", va biet key nao thieu trong bang de chen entry
    moi. sfs: list SerializedFile (co the tu nhieu don vi/bundle khac nhau
    — SharedTableData thuong nam o bundle khac StringTable)."""
    index: Dict[str, Dict[str, int]] = {}
    for sf in sfs or []:
        try:
            objects = list(sf.objects)
        except PB3Error:
            continue
        for o in objects:
            if o.class_id != 114:
                continue
            try:
                typ = sf.types[o.type_id]
            except IndexError:
                continue
            if typ.node is None:
                continue
            try:
                val = _u.read_monobehaviour_object(sf, o.path_id)
            except PB3Error:
                continue
            if "m_Entries" not in val or "m_TableCollectionName" not in val:
                continue
            coll = val.get("m_TableCollectionName")
            if not isinstance(coll, str):
                continue
            d = index.setdefault(coll, {})
            for en in val.get("m_Entries") or []:
                if isinstance(en, dict) \
                        and isinstance(en.get("m_Key"), str) \
                        and isinstance(en.get("m_Id"), int):
                    d[en["m_Key"]] = en["m_Id"]
    return index


def build_plan(source_bytes, translations, *, source_bundle, entry=None,
               mode="REBUILD_SECTION", rebuild_all=False, asset_names=None,
               backend=None, limits=None, source_locale="en", mb_index=None):
    """Lap plan tu bundle/assets: quet TextAsset JSON + MonoBehaviour /
    StringTable, doi key trong `translations` (key -> gia tri moi) thanh
    entry moi asset.

    entry: vpath toi don vi ("Payload/.../x.bundle::CAB-..." hoac None neu
    file dau vao chinh la bundle/.assets). asset_names: chi ap cho cac
    TextAsset co ten nay (vd ["en.json"] — mac dinh: moi asset JSON co key
    trung). source_locale: cot ngon ngu goc de ghi (mac dinh "en" — ghi
    vao cot/bang cua locale nay vi chua co cot vi). mb_index:
    {collection: {key: m_Id}} tu build_mb_shared_index (can de dat ten
    key StringTable va chen entry thieu); None thi StringTable chi ghi
    entry da co (khong chen duoc key thieu)."""
    if not isinstance(translations, dict) or not translations:
        raise ValueError("translations phai la JSON object khong rong "
                         "(key -> gia tri moi, nhu dau ra tab Dich).")
    for k, v in translations.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError("translations: key va gia tri deu phai la chuoi.")
    mode = (mode or "REBUILD_SECTION").upper()
    if mode not in _PLAN_OPS:
        raise ValueError("mode phai thuoc %s." % "|".join(_PLAN_OPS))
    lim = limits or Limits()
    be = backend or _u.UnityBackend("NATIVE")
    kind, unit_bytes, req_entry, label = _resolve_unit(
        bytes(source_bytes), entry, be, lim)
    tas, _warn = _read_unit_text_assets(kind, unit_bytes, req_entry, be, lim)
    wanted = None
    if asset_names:
        wanted = {str(n) for n in asset_names}
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    unit_display = label or (entry or os.path.basename(str(source_bundle)))
    for pid, name, script in tas:
        asset_path = "%s::%s::%s" % (unit_display, req_entry, name) \
            if kind == "bundle" else "%s::%s" % (unit_display, name)
        if wanted is not None and name not in wanted:
            skipped.append({"asset_path": asset_path,
                            "reason": "khong nam trong danh sach asset "
                                      "da chon"})
            continue
        try:
            obj = json.loads(script)
        except (ValueError, UnicodeDecodeError):
            skipped.append({"asset_path": asset_path,
                            "reason": "TextAsset khong phai JSON"})
            continue
        if not isinstance(obj, dict):
            skipped.append({"asset_path": asset_path,
                            "reason": "JSON khong phai object (dict)"})
            continue
        changes = []
        warnings: List[str] = []
        for key in sorted(translations):
            if key not in obj:
                continue
            cur = obj[key]
            new = translations[key]
            if not isinstance(cur, str):
                warnings.append("key %r: gia tri goc khong phai chuoi, bo qua"
                                % key)
                continue
            if cur == new:
                continue
            if _placeholders(cur) != _placeholders(new):
                warnings.append(
                    "key %r: placeholder doi %s -> %s (kiem tra lai ban dich)"
                    % (key, sorted(_placeholders(cur)),
                       sorted(_placeholders(new))))
            changes.append({"key": key, "before": cur, "after": new})
        if not changes:
            skipped.append({"asset_path": asset_path,
                            "reason": "khong co key nao trung voi ban dich"})
            continue
        script_b = script.encode("utf-8")
        try:
            after_b = _simulate_script(script_b, changes)
        except ValueError as exc:
            skipped.append({"asset_path": asset_path,
                            "reason": "khong mo phong duoc patch: %s" % exc})
            continue
        eid = hashlib.sha256(
            ("%s|%s|%d|%s|%s" % (source_bundle, entry or "", pid, name,
                                 json.dumps(changes, ensure_ascii=False,
                                            sort_keys=True))).encode("utf-8")
        ).hexdigest()[:12]
        entries.append({
            "id": eid, "asset_path": asset_path, "asset_type": "TextAsset",
            "op": mode, "object": int(pid), "kind": kind,
            "entry": req_entry, "vpath": entry,
            "changes": changes,
            "preview_before": {c["key"]: c["before"] for c in changes},
            "preview_after": {c["key"]: c["after"] for c in changes},
            "before_sha": _sha(script_b), "after_sha": _sha(after_b),
            "selected": True, "warnings": warnings,
        })
    # --- M1 (G1): MonoBehaviour / StringTable ---
    # Quet SF cua don vi, lay target ghi duoc (LS term, ST entry) roi lap
    # entry MB cho key co trong translations va khac gia tri goc. Key ST
    # thieu trong bang (co trong SharedTableData nhung chua co entry) thi
    # CHEN entry moi (field_path "m_TableData[+]", before "").
    try:
        if kind == "bundle":
            bundle = _u.parse_bundle(unit_bytes, be, lim, where="bundle")
            sf = _u.parse_serialized(bundle.get_entry(req_entry), lim,
                                     where=req_entry)
        else:
            sf = _u.parse_serialized(unit_bytes, lim, where="assets")
    except PB3Error:
        sf = None
    if sf is not None:
        try:
            targets, mb_notes = _u.monobehaviour_write_targets(
                sf, source_locale, mb_index)
        except PB3Error:
            targets, mb_notes = [], []
        for note in mb_notes:
            skipped.append({"asset_path": "%s::%s"
                            % (unit_display, req_entry or ""),
                            "reason": note})
        by_pid: Dict[int, List[Dict[str, Any]]] = {}
        generic_hits: List[Dict[str, Any]] = []
        for t in targets:
            if t.get("kind") == "generic":
                # Generic MB chua vao plan o v1 (key la field path, khong
                # khop output trich xuat da gop) — ghi skipped neu trung
                # translations, khong bo im lang.
                if t.get("key") in translations:
                    generic_hits.append(t)
                continue
            by_pid.setdefault(int(t["path_id"]), []).append(t)
        for t in generic_hits:
            skipped.append({"asset_path": "%s::%s::%s"
                            % (unit_display, req_entry or "", t.get("name") or ""),
                            "reason": "key generic MonoBehaviour %r chua ho tro "
                                      "ghi o v1" % t.get("key")})
        rev: Dict[int, Tuple[str, str]] = {}
        for coll, keys in (mb_index or {}).items():
            for k, mid in (keys or {}).items():
                rev[mid] = (coll, k)
        for pid, tgts in sorted(by_pid.items()):
            name = tgts[0].get("name") or ""
            asset_path = "%s::%s::%s" % (unit_display, req_entry, name) \
                if kind == "bundle" else "%s::%s" % (unit_display, name)
            changes = []
            warnings: List[str] = []
            present_bare = set()
            for t in tgts:
                key = t.get("key")
                if not isinstance(key, str):
                    continue
                if t.get("kind") == "string_table" and "/" in key:
                    present_bare.add(key.split("/", 1)[1])
                if key not in translations:
                    continue
                cur = t.get("before")
                new = translations[key]
                if not isinstance(cur, str):
                    continue
                if cur == new:
                    continue
                if _placeholders(cur) != _placeholders(new):
                    warnings.append(
                        "key %r: placeholder doi %s -> %s (kiem tra lai ban dich)"
                        % (key, sorted(_placeholders(cur)),
                           sorted(_placeholders(new))))
                changes.append({"key": key, "field_path": t["field_path"],
                                "before": cur, "after": new})
            # Key ST thieu: tim collection qua entry da co, chen entry moi.
            kinds = {t.get("kind") for t in tgts}
            if "string_table" in kinds and mb_index:
                coll = None
                try:
                    val = _u.read_monobehaviour_object(sf, pid)
                    rows = val.get("m_TableData") or []
                except PB3Error:
                    rows = []
                for row in rows:
                    if isinstance(row, dict) \
                            and isinstance(row.get("m_Id"), int):
                        hit = rev.get(row["m_Id"])
                        if hit is not None:
                            coll = hit[0]
                            break
                if coll is not None and coll in mb_index:
                    for bare in sorted(mb_index[coll]):
                        if bare in present_bare:
                            continue
                        full_key = "%s/%s" % (coll, bare)
                        if full_key not in translations:
                            continue
                        new = translations[full_key]
                        if new == "":
                            continue  # before "" == after "" -> khong doi
                        el = _u.string_table_element_template(
                            sf, pid, mb_index[coll][bare], new)
                        if el is None:
                            warnings.append(
                                "key %r: khong dung duoc mau entry de chen, "
                                "bo qua" % full_key)
                            continue
                        changes.append({"key": full_key,
                                        "field_path": "m_TableData[+]",
                                        "before": "", "after": new,
                                        "element": el})
            if not changes:
                skipped.append({"asset_path": asset_path,
                                "reason": "khong co key nao trung voi ban dich"})
                continue
            try:
                old_obj = sf.object_bytes(pid)
            except PB3Error as exc:
                skipped.append({"asset_path": asset_path,
                                "reason": "khong doc duoc object bytes: %s"
                                          % _err_text(exc)})
                continue
            try:
                if tgts[0].get("kind") == "language_source":
                    new_obj = _u.write_language_source_bytes(
                        old_obj, changes, sf.endian)
                else:
                    new_obj, _applied = _u.write_monobehaviour_object_bytes(
                        sf, pid, changes)
            except PB3Error as exc:
                skipped.append({"asset_path": asset_path,
                                "reason": "khong mo phong duoc patch MB: %s"
                                          % _err_text(exc)})
                continue
            eid = hashlib.sha256(
                ("%s|%s|%d|%s|%s|MonoBehaviour"
                 % (source_bundle, entry or "", pid, name,
                    json.dumps(changes, ensure_ascii=False,
                               sort_keys=True))).encode("utf-8")
            ).hexdigest()[:12]
            entries.append({
                "id": eid, "asset_path": asset_path,
                "asset_type": "MonoBehaviour",
                # MB v1 chi ho tro REBUILD_SECTION (khong PATCH_INPLACE).
                "op": "REBUILD_SECTION", "object": int(pid), "kind": kind,
                "entry": req_entry, "vpath": entry,
                "changes": changes,
                "preview_before": {c["key"]: c["before"] for c in changes},
                "preview_after": {c["key"]: c["after"] for c in changes},
                "before_sha": _sha(old_obj), "after_sha": _sha(new_obj),
                "selected": True, "warnings": warnings,
            })
    if not entries:
        raise ValueError(
            "Khong lap duoc entry nao: khong co TextAsset JSON hay "
            "MonoBehaviour nao co key trung voi ban dich (%d asset/object "
            "da bo qua, xem skipped)."
            % len(skipped))
    plan = {
        "version": PLAN_VERSION,
        "id": _plan_id(source_bundle, entry, translations),
        "source_bundle": source_bundle,
        "unit_entry": entry, "unit_kind": kind,
        "mode": mode, "rebuild_all": bool(rebuild_all),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries, "skipped": skipped,
    }
    if mode == "REBUILD_ALL":
        plan["warnings"] = [
            "op REBUILD_ALL (che do ghi cua Unity) can maturity M4 "
            "(hien M2) nen executor se tu choi entry nay tren web; "
            "dung PATCH_INPLACE/REBUILD_SECTION de ap that."]
    return plan


def validate_plan(plan):
    """Kiem tra schema plan, tra ban sao da chuan hoa. Loi -> ValueError
    kem thong diep tieng Viet ro rang."""
    if not isinstance(plan, dict):
        raise ValueError("plan phai la JSON object.")
    if plan.get("version") != PLAN_VERSION:
        raise ValueError("plan version %r khong ho tro (can %d)."
                         % (plan.get("version"), PLAN_VERSION))
    if not plan.get("id") or not isinstance(plan["id"], str):
        raise ValueError("plan thieu id.")
    entries = plan.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("plan khong co entry nao.")
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("entry trong plan phai la object.")
        for f in ("id", "asset_path", "asset_type", "op", "object",
                  "changes", "before_sha", "after_sha"):
            if f not in e:
                raise ValueError("entry %s thieu truong %r."
                                 % (e.get("id"), f))
        if e["asset_type"] not in ("TextAsset", "MonoBehaviour"):
            raise ValueError("entry %s: asset_type %r khong ho tro tren web "
                             "(chi TextAsset va MonoBehaviour)."
                             % (e["id"], e["asset_type"]))
        if e["asset_type"] == "MonoBehaviour" \
                and e["op"] != "REBUILD_SECTION":
            raise ValueError("entry %s: MonoBehaviour chi ho tro op "
                             "REBUILD_SECTION (nhan %r)."
                             % (e["id"], e["op"]))
        if e["op"] not in _PLAN_OPS:
            raise ValueError("entry %s: op %r khong ho tro."
                             % (e["id"], e["op"]))
        if not isinstance(e["changes"], list) or not e["changes"]:
            raise ValueError("entry %s khong co change nao." % e["id"])
        for c in e["changes"]:
            if not all(k in c for k in ("key", "before", "after")):
                raise ValueError("entry %s co change thieu key/before/after."
                                 % e["id"])
            if e["asset_type"] == "MonoBehaviour" \
                    and not isinstance(c.get("field_path"), str):
                raise ValueError("entry %s co change MB thieu field_path."
                                 % e["id"])
        e.setdefault("selected", True)
        e.setdefault("warnings", [])
    return plan


def _err_text(exc):
    """Thong diep loi day du: str(PB3Error) chi hien message chung theo ma
    (vd 'Cau hinh khong hop le.') nen phai kem ca detail moi chan doan
    duoc."""
    text = str(exc)
    detail = getattr(exc, "detail", "") or ""
    if detail and detail not in text:
        text = "%s (%s)" % (text, detail)
    return text


def _parse_checkpoint(checkpoint_json):
    if not checkpoint_json:
        return {"completed": []}
    try:
        ck = json.loads(checkpoint_json)
    except ValueError:
        raise ValueError("checkpoint_json khong phai JSON hop le.")
    if not isinstance(ck, dict) or not isinstance(ck.get("completed"), list):
        raise ValueError("checkpoint phai la object co completed (list).")
    return ck


def _apply_entry_changes(kind, unit_bytes, req_entry, entry, backend, lim,
                         progress_fn=None):
    """Ap toan bo changes cua 1 entry len unit bytes.

    TextAsset: mo phong 1 lan bang _simulate_script (thay theo KEY, giu
    nguyen moi byte khac) roi patch toan bo script bang 1 lan apply_patch
    (old = script goc, new = script da mo phong) — dam bao byte ket qua
    dung after_sha theo dinh nghia. Verify script doc lai dung after_sha
    + dung dict mong doi.
    MonoBehaviour (M1): ap qua apply_monobehaviour_patch (REBUILD_SECTION)
    roi verify object bytes sha == after_sha. Tra (unit_bytes_moi,
    write_mode_used). Loi verify -> PB3Error (khong ghi gi).
    progress_fn(done, total) tuy chon (P0-1): goi sau moi change khi mo
    phong (None thi khong goi, test cu khong doi)."""
    cur = bytes(unit_bytes)
    if entry.get("asset_type") == "MonoBehaviour":
        total = len(entry["changes"])
        if progress_fn is not None:
            for idx in range(total):
                try:
                    progress_fn(idx + 1, total)
                except Exception:
                    pass
        req = _u.MbPatchRequest(
            data=cur, kind=kind, entry=req_entry,
            path_id=int(entry["object"]), changes=entry["changes"],
            write_mode=entry["op"], backend=backend, temp_dir=None)
        res = _u.apply_monobehaviour_patch(req)
        cur = res.output_bytes
        last_mode = res.write_mode_used
        if kind == "bundle":
            bundle = _u.parse_bundle(cur, backend, lim, where="bundle::after")
            sf = _u.parse_serialized(bundle.get_entry(req_entry), lim,
                                     where="after")
        else:
            sf = _u.parse_serialized(cur, lim, where="assets::after")
        if _sha(sf.object_bytes(int(entry["object"]))) != entry["after_sha"]:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "entry %s: object sau khi ap khac after_sha trong plan."
                % entry["id"], where=entry["asset_path"])
        return cur, last_mode
    if kind == "bundle":
        bundle = _u.parse_bundle(cur, backend, lim, where="bundle")
        sf = _u.parse_serialized(bundle.get_entry(req_entry), lim,
                                 where=req_entry)
    else:
        sf = _u.parse_serialized(cur, lim, where="assets")
    _pid, _name, script = sf.find_text_asset(path_id=int(entry["object"]))
    try:
        new_script_b = _simulate_script(script.encode("utf-8"),
                                        entry["changes"])
    except ValueError as exc:
        raise PB3Error(
            "E_CONFIG_INVALID",
            "entry %s: khong ap duoc change theo key (%s)"
            % (entry["id"], exc), where=entry["asset_path"])
    total = len(entry["changes"])
    if progress_fn is not None:
        for idx in range(total):
            try:
                progress_fn(idx + 1, total)
            except Exception:
                pass
    req = _u.PatchRequest(
        data=cur, kind=kind, entry=req_entry,
        path_id=int(entry["object"]),
        old_text=script, new_text=new_script_b.decode("utf-8"),
        write_mode=entry["op"], backend=backend, temp_dir=None)
    res = _u.apply_patch(req)
    cur = res.output_bytes
    last_mode = res.write_mode_used
    # Verify lai: script doc duoc phai co sha = after_sha va dict dung.
    if kind == "bundle":
        bundle = _u.parse_bundle(cur, backend, lim, where="bundle::after")
        sf = _u.parse_serialized(bundle.get_entry(req_entry), lim,
                                 where="after")
    else:
        sf = _u.parse_serialized(cur, lim, where="assets::after")
    _pid, _name, script = sf.find_text_asset(path_id=int(entry["object"]))
    if _sha(script.encode("utf-8")) != entry["after_sha"]:
        raise PB3Error(
            "E_VERIFY_FAILED",
            "entry %s: script sau khi ap khac after_sha trong plan "
            "(co the chuoi cu xuat hien nhieu cho) — khong ghi don vi nay."
            % entry["id"], where=entry["asset_path"])
    try:
        obj = json.loads(script)
    except ValueError:
        raise PB3Error("E_VERIFY_FAILED",
                       "entry %s: script sau khi ap khong con la JSON hop le."
                       % entry["id"], where=entry["asset_path"])
    for ch in entry["changes"]:
        if not isinstance(obj, dict) or obj.get(ch["key"]) != ch["after"]:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "entry %s: key %r sau khi ap la %r, khac gia tri mong doi %r."
                % (entry["id"], ch["key"],
                   obj.get(ch["key"]) if isinstance(obj, dict) else None,
                   ch["after"]), where=entry["asset_path"])
    return cur, last_mode


def _output_name(source_bundle, kind, label):
    from sdk.batch import _sdk_default_output_rel

    return os.path.basename(
        _sdk_default_output_rel(source_bundle, kind, label))


def _write_outputs(source_bundle, patched_units, dry_run):
    """Ghi cac don vi da patch ra /tmp/plan_out/ (1 file -> dest, nhieu
    file -> /tmp/plan.zip). Dry-run: khong ghi gi. Tra (outputs, dest,
    zip_path)."""
    if dry_run or not patched_units:
        return [], None, None
    shutil.rmtree(PLAN_OUT_DIR, ignore_errors=True)
    os.makedirs(PLAN_OUT_DIR, exist_ok=True)
    names = []
    for (kind, label, _key), data in patched_units:
        name = _output_name(source_bundle, kind, label)
        dest = os.path.join(PLAN_OUT_DIR, name)
        n = 1
        base, ext = os.path.splitext(name)
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(PLAN_OUT_DIR, "%s_%d%s" % (base, n, ext))
        with open(dest, "wb") as fh:
            fh.write(data)
        names.append(os.path.basename(dest))
    if len(names) == 1:
        return names, os.path.join(PLAN_OUT_DIR, names[0]), None
    zpath = PLAN_ZIP_PATH
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.write(os.path.join(PLAN_OUT_DIR, name), name)
    return names, None, zpath


def apply_plan(source_bytes, plan, *, dry_run=True, checkpoint_json=None,
               stop_after=None, backend=None, limits=None,
               backup_root=PLAN_BACKUP_ROOT, out_dir=PLAN_OUT_DIR,
               progress_fn=None):
    """Executor: ap plan len file goc.

    - Chi entry duoc chon moi ap (tru khi plan.rebuild_all=true: ap toan bo).
    - Entry co op REBUILD_ALL bi tu choi rieng (M4) — cac entry khac van ap.
    - Moi don vi (unit) co tinh nguyen tu: 1 entry trong don vi loi verify
      thi ca don vi do khong ghi; entry loi van ghi vao errors, khong dung
      ca plan (nhu batch: loi 1 game khong dung ca lo).
    - stop_after=N: dung sau N entry ap xong, tra status "partial" +
      checkpoint de resume.
    - progress_fn(done, total) tuy chon (P0-1): goi sau moi entry ap xong
      (done = so entry da xu ly, total = so entry can ap); None thi khong
      goi (test cu khong doi).
    """
    plan = validate_plan(plan)
    ck = _parse_checkpoint(checkpoint_json)
    completed = set(ck.get("completed") or [])
    if ck.get("plan_id") and ck["plan_id"] != plan["id"]:
        raise ValueError("checkpoint thuoc plan khac (%s != %s)."
                         % (ck["plan_id"], plan["id"]))
    lim = limits or Limits()
    be = backend or _u.UnityBackend("NATIVE")
    source_bytes = bytes(source_bytes)
    if plan.get("rebuild_all"):
        todo = [e for e in plan["entries"] if e["id"] not in completed]
    else:
        todo = [e for e in plan["entries"]
                if e.get("selected", True) and e["id"] not in completed]
    skipped = [{"id": e["id"], "asset_path": e["asset_path"],
                "reason": "da xong o checkpoint truoc"}
               for e in plan["entries"] if e["id"] in completed]
    skipped += [{"id": e["id"], "asset_path": e["asset_path"],
                 "reason": "khong duoc tick (bo chon)"}
                for e in plan["entries"]
                if not plan.get("rebuild_all") and not e.get("selected", True)
                and e["id"] not in completed]
    # Nhom theo don vi (nhieu entry co the chung 1 bundle/CAB).
    units: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for e in todo:
        key = _unit_key(e.get("vpath"), e.get("kind")
                        or plan.get("unit_kind") or "bundle",
                        e.get("entry"))
        if key not in units:
            units[key] = {"entries": [], "vpath": e.get("vpath"),
                          "kind": e.get("kind")
                          or plan.get("unit_kind") or "bundle",
                          "req_entry": e.get("entry")}
            order.append(key)
        units[key]["entries"].append(e)
    # Ten backup phai tinh tu TOAN BO entry cua don vi trong plan (khong
    # chi entry duoc chon o lan chay nay) — neu khong undo (tinh tren ca
    # plan) se khong tim thay backup cua lan ap chi chon 1 phan entry.
    plan_units: Dict[str, List[Dict[str, Any]]] = {}
    for e in plan["entries"]:
        key = _unit_key(e.get("vpath"), e.get("kind")
                        or plan.get("unit_kind") or "bundle",
                        e.get("entry"))
        plan_units.setdefault(key, []).append(e)
    applied: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    patched: List[Tuple[Tuple[str, str, str], bytes]] = []
    done_ids: List[str] = list(completed)
    stopped = False
    processed = 0  # so entry da xu ly (de bao progress_fn don dieu)
    for key in order:
        if stop_after is not None and len(applied) >= stop_after:
            stopped = True
            break
        unit = units[key]
        try:
            kind, unit_bytes, req_entry, label = _resolve_unit(
                source_bytes, unit["vpath"], be, lim)
        except Exception as exc:  # noqa: BLE001
            for e in unit["entries"]:
                errors.append({"id": e["id"], "asset_path": e["asset_path"],
                               "code": getattr(exc, "code", "E_INPUT_NOT_FOUND"),
                               "message": _err_text(exc)})
            continue
        cur = bytes(unit_bytes)
        unit_ok = True
        unit_applied: List[Dict[str, Any]] = []
        for e in unit["entries"]:
            if stop_after is not None and len(applied) + len(unit_applied) >= stop_after:
                stopped = True
                break
            if e["op"] == "REBUILD_ALL":
                errors.append({
                    "id": e["id"], "asset_path": e["asset_path"],
                    "code": "E_WRITE_DISABLED",
                    "message": "op REBUILD_ALL can maturity M4 (hien %s) "
                               "nen chua ap duoc tren web; doi op thanh "
                               "REBUILD_SECTION trong plan roi ap lai."
                               % _u.UnityBackend.MATURITY})
                processed += 1
                if progress_fn is not None:
                    try:
                        progress_fn(processed, len(todo))
                    except Exception:
                        pass
                continue
            try:
                cur, mode_used = _apply_entry_changes(
                    kind, cur, req_entry, e, be, lim)
            except Exception as exc:  # noqa: BLE001
                errors.append({"id": e["id"], "asset_path": e["asset_path"],
                               "code": getattr(exc, "code", "E_VERIFY_FAILED"),
                               "message": _err_text(exc)})
                unit_ok = False
                break
            finally:
                processed += 1
                if progress_fn is not None:
                    try:
                        progress_fn(processed, len(todo))
                    except Exception:
                        pass
            unit_applied.append({
                "id": e["id"], "asset_path": e["asset_path"],
                "object": int(e["object"]), "changes": len(e["changes"]),
                "write_mode": mode_used, "verified": True,
                "before_sha": e["before_sha"][:12],
                "after_sha": e["after_sha"][:12]})
        if not unit_ok:
            # Don vi loi: bo ca don vi (ke ca entry da ap xong trong don vi
            # do o vong nay — chung chua duoc ghi ra dia nen an toan).
            for a in unit_applied:
                errors.append({"id": a["id"], "asset_path": a["asset_path"],
                               "code": "E_VERIFY_FAILED",
                               "message": "don vi chung loi verify o entry "
                                          "khac nen khong ghi."})
            continue
        if stopped and unit_applied:
            # Dung giua don vi: giu lai phan da ap xong trong don vi nay
            # (da verify tung entry), phan con lai resume sau.
            applied.extend(unit_applied)
            done_ids.extend(a["id"] for a in unit_applied)
            patched.append(((kind, label, key), cur))
            break
        if unit_applied:
            applied.extend(unit_applied)
            done_ids.extend(a["id"] for a in unit_applied)
            patched.append(((kind, label, key), cur))
        if stopped:
            break
    # Con entry chua ap (do stop_after hoac do loi don vi thi khong tinh
    # la remaining de resume — loi phai sua plan roi chay lai).
    remaining = [e["id"] for e in todo
                 if e["id"] not in done_ids
                 and not any(err["id"] == e["id"] for err in errors)]
    checkpoint = {"plan_id": plan["id"], "completed": done_ids}
    status = "partial" if stopped or remaining else "done"
    outputs: List[str] = []
    dest = None
    zpath = None
    if not dry_run and patched:
        # Backup don vi GOC truoc khi ghi (chi ghi 1 lan — khong ghi de
        # backup cu, de undo luon ve dung ban goc).
        os.makedirs(backup_root, exist_ok=True)
        plan_backup = os.path.join(backup_root, plan["id"])
        os.makedirs(plan_backup, exist_ok=True)
        for (kind, label, key), _data in patched:
            bname = os.path.join(plan_backup,
                                 _backup_name(plan_units[key]))
            if not os.path.exists(bname):
                vpath = units[key]["vpath"]
                _k2, orig_bytes, _r2, _l2 = _resolve_unit(
                    source_bytes, vpath, be, lim)
                with open(bname, "wb") as fh:
                    fh.write(orig_bytes)
        outputs, dest, zpath = _write_outputs(
            plan["source_bundle"], patched, dry_run=False)
    return {
        "ok": True, "dry_run": bool(dry_run), "status": status,
        "plan_id": plan["id"],
        "applied": applied, "skipped": skipped, "errors": errors,
        "checkpoint": checkpoint,
        "remaining": remaining,
        "outputs": outputs, "dest": dest, "zip_path": zpath,
        "note": "Output la don vi nho nhat da patch (bundle/.assets), "
                "KHONG phai IPA dong goi lai. Undo can ban sao luu trong "
                "/tmp/plan_backup/<plan_id>/ (chi trong phien worker) "
                "hoac patch nguoc tu file da ap.",
    }


def undo_plan(source_bytes, plan, *, backend=None, limits=None,
              backup_root=PLAN_BACKUP_ROOT, out_dir=PLAN_OUT_DIR):
    """Hoan tac plan: khoi phuc don vi ve trang thai truoc khi ap.

    Uu tien ban sao luu (/tmp/plan_backup/<plan_id>/); neu khong con thi
    patch nguoc (new -> old) roi verify before_sha. Chi ghi file khi
    moi entry trong don vi verify dung before_sha. Chi hoan tac entry
    DUOC CHON trong plan (nhu apply): entry bi bo tick chua tung duoc ap
    nen patch nguoc no se loi 'khong tim thay chuoi cu' — bo qua cho dung.
    rebuild_all=true thi hoan tac toan bo."""
    plan = validate_plan(plan)
    lim = limits or Limits()
    be = backend or _u.UnityBackend("NATIVE")
    source_bytes = bytes(source_bytes)
    if plan.get("rebuild_all"):
        todo_ids = {e["id"] for e in plan["entries"]}
    else:
        todo_ids = {e["id"] for e in plan["entries"]
                    if e.get("selected", True)}
    units: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for e in plan["entries"]:
        if e["id"] not in todo_ids:
            continue
        key = _unit_key(e.get("vpath"), e.get("kind")
                        or plan.get("unit_kind") or "bundle",
                        e.get("entry"))
        if key not in units:
            units[key] = {"entries": [], "vpath": e.get("vpath"),
                          "kind": e.get("kind")
                          or plan.get("unit_kind") or "bundle",
                          "req_entry": e.get("entry")}
            order.append(key)
        units[key]["entries"].append(e)
    # Ten backup tinh tu TOAN BO entry cua don vi trong plan (nhu apply)
    # — ke ca entry bi bo tick — de undo luon tim thay backup du lua chon
    # tick co doi sau khi ap.
    plan_units: Dict[str, List[Dict[str, Any]]] = {}
    for e in plan["entries"]:
        key = _unit_key(e.get("vpath"), e.get("kind")
                        or plan.get("unit_kind") or "bundle",
                        e.get("entry"))
        plan_units.setdefault(key, []).append(e)
    restored: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    patched: List[Tuple[Tuple[str, str, str], bytes]] = []
    plan_backup = os.path.join(backup_root, plan["id"])
    for key in order:
        unit = units[key]
        try:
            kind, cur_bytes, req_entry, label = _resolve_unit(
                source_bytes, unit["vpath"], be, lim)
        except Exception as exc:  # noqa: BLE001
            for e in unit["entries"]:
                errors.append({"id": e["id"], "asset_path": e["asset_path"],
                               "code": getattr(exc, "code", "E_INPUT_NOT_FOUND"),
                               "message": _err_text(exc)})
            continue
        how = None
        cur = None
        bname = os.path.join(plan_backup, _backup_name(plan_units[key]))
        if os.path.isfile(bname):
            with open(bname, "rb") as fh:
                cur = fh.read()
            how = "backup"
        else:
            # Patch nguoc: ap new -> old, nguoc thu tu change.
            cur = bytes(cur_bytes)
            try:
                for e in unit["entries"]:
                    rev = dict(e)
                    rev["changes"] = [{"key": c["key"], "before": c["after"],
                                       "after": c["before"]}
                                      for c in reversed(e["changes"])]
                    rev["after_sha"] = e["before_sha"]
                    rev["op"] = "REBUILD_SECTION" \
                        if e["op"] == "REBUILD_ALL" else e["op"]
                    cur, _m = _apply_entry_changes(
                        kind, cur, req_entry, rev, be, lim)
                how = "reverse-patch"
            except Exception as exc:  # noqa: BLE001
                for e in unit["entries"]:
                    errors.append({
                        "id": e["id"], "asset_path": e["asset_path"],
                        "code": getattr(exc, "code", "E_VERIFY_FAILED"),
                        "message": "khong con ban sao luu trong phien nay va "
                                   "patch nguoc that bai: " + _err_text(exc)})
                continue
        # Verify: moi entry phai ve dung before_sha.
        ok = True
        try:
            if kind == "bundle":
                bundle = _u.parse_bundle(cur, be, lim, where="bundle::undo")
                sf = _u.parse_serialized(bundle.get_entry(req_entry), lim,
                                         where="undo")
            else:
                sf = _u.parse_serialized(cur, lim, where="assets::undo")
            for e in unit["entries"]:
                _pid, _n, script = sf.find_text_asset(
                    path_id=int(e["object"]))
                if _sha(script.encode("utf-8")) != e["before_sha"]:
                    raise PB3Error(
                        "E_VERIFY_FAILED",
                        "entry %s sau khi hoan tac khac before_sha."
                        % e["id"], where=e["asset_path"])
        except Exception as exc:  # noqa: BLE001
            ok = False
            for e in unit["entries"]:
                errors.append({"id": e["id"], "asset_path": e["asset_path"],
                               "code": getattr(exc, "code", "E_VERIFY_FAILED"),
                               "message": _err_text(exc)})
        if not ok:
            continue
        for e in unit["entries"]:
            restored.append({"id": e["id"], "asset_path": e["asset_path"],
                             "how": how, "verified": True,
                             "before_sha": e["before_sha"][:12]})
        patched.append(((kind, label, key), cur))
    outputs, dest, zpath = _write_outputs(
        plan["source_bundle"], patched, dry_run=False)
    return {
        "ok": not errors, "plan_id": plan["id"],
        "restored": restored, "errors": errors,
        "outputs": outputs, "dest": dest, "zip_path": zpath,
        "note": "Undo khoi phuc don vi ve dung before_sha cua plan. "
                "Neu bao loi 'khong con ban sao luu': tai lai trang thi "
                "ban sao luu trong RAM worker da mat — hay nap file GOC "
                "lam dau vao roi ap lai plan, hoac dung ban goc da luu.",
    }
