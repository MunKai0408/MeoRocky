"""Patch pack nhi phan (muc C7, phan tich Joseph).

Web chi doc duoc asset nhi phan (texture/audio read-only, dung iSH) nen
web xuat ra mot "patch pack" — JSON + script Python mo ta asset path ->
change — de user mang sang iSH/desktop ap vao file goc, noi ghi nhi phan
lam duoc.

Mo hinh pack TUONG THICH schema plan JSON muc 8 (sdk/plan.py): manifest
chinh la mot plan (version 1, entries co asset_path/asset_type/op/object/
changes/preview_before/preview_after/before_sha/after_sha/selected) them
lop vo patch-pack (format, source_file, source_sha256, apply_script).
Script ap (apply_patch.py) doc manifest ke ben, kiem tra before_sha roi
moi ap bang dung sdk.unity.apply_patch nhu executor tren web.

An toan:
- Mac dinh dry-run: chi kiem tra + tinh, KHONG ghi file nao.
- Chi ghi that khi co co --allow-write (dung quy tac PB3: patch mac
  dinh dry-run).
- Tu choi toan bo (khong ghi nua voi) khi: file goc khong khop
  source_sha256, script hien tai khong khop before_sha, asset khong tim
  thay, bundle ma hoa / khong doc duoc, bundle v9 (chi doc), entry dung
  op REBUILD_ALL (can maturity M4).
- KHONG bao gio ghi de file goc: output la don vi da patch trong thu muc
  <ten-file>.pb3-patched/ ke ben file goc, kem ban sao luu don vi goc.

Chi thu vien chuan, Python >= 3.9. Khong goi mang. Khong dong goi lai IPA.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.limits import Limits
from sdk import plan as plan_sdk
from sdk import unity as _u

PATCH_PACK_FORMAT = "pb3-patch-pack"
PATCH_PACK_VERSION = 1
PB3_VERSION = "3.1"
MANIFEST_NAME = "patch_manifest.json"
APPLY_SCRIPT_NAME = "apply_patch.py"
README_NAME = "README.txt"
_MODES = ("PATCH_INPLACE", "REBUILD_SECTION")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pack_notes() -> List[str]:
    return [
        "Mac dinh dry-run: script chi kiem tra, khong ghi file nao.",
        "Chi ghi that khi chay voi --allow-write.",
        "Script khong bao gio ghi de file goc; file da patch nam trong "
        "thu muc <ten-file>.pb3-patched/ kem ban sao luu don vi goc.",
        "File da patch la don vi nho nhat (bundle/.assets), khong phai "
        "goi da dong goi lai — tu dong goi lai neu can.",
    ]


def _entry_id(source_name, vpath, pid, name, changes) -> str:
    raw = json.dumps([source_name, vpath or "", pid, name, changes],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _read_script(kind, unit_bytes, req_entry, pid, backend, lim):
    """(name, script) cua TextAsset path_id trong don vi."""
    tas, _warn = plan_sdk._read_unit_text_assets(
        kind, unit_bytes, req_entry, backend, lim)
    for p, n, s in tas:
        if int(p) == int(pid):
            return n, s
    raise ValueError(
        "khong tim thay TextAsset path_id=%d trong don vi %s"
        % (int(pid), req_entry or kind))


def _check_bundle_version(kind, unit_bytes, backend, lim, where):
    """Bundle UnityFS v9 chi doc (C5) — tu choi som, ro ly do."""
    if kind != "bundle":
        return
    bundle = _u.parse_bundle(unit_bytes, backend, lim, where=where)
    if bundle.version >= 9:
        raise ValueError(
            "bundle UnityFS v%d chi doc — chua ho tro patch "
            "(ghi lai bundle v9 chua duoc ho tro)" % bundle.version)


def _apply_entry_changes(kind, unit_bytes, req_entry, entry, backend, lim,
                         source_bytes, vpath):
    """Ap toan bo changes cua 1 entry len unit bytes (trong RAM).

    Dung dung sdk.unity.apply_patch nhu executor plan (sdk/plan.py) nen
    after_sha tinh o day khop voi script ap o may dich. Thay theo KEY
    truoc (plan_sdk._simulate_script, giong executor plan sau P0-1: span
    gia tri cua key, lan xuat hien cuoi thang) — gia tri rong "" va gia
    tri trung nhau giua cac key deu ap dung dung; thay theo gia tri tran
    cu khong bao gio patch duoc "". Neu khong mo phong theo key duoc
    (script khong phai JSON object, edit kieu Mod thay chuoi tran) thi
    roi ve vong lap apply_patch tung change nhu cu. Script ap
    (APPLY_SCRIPT) dung dung logic nay nen after_sha hai ben khop nhau.
    """
    cur = bytes(unit_bytes)
    from_container = bool(vpath) and _u.detect_bundle(source_bytes) is None
    try:
        _name, script = _read_script(
            kind, cur, req_entry, int(entry["object"]), backend, lim)
        new_script_b = plan_sdk._simulate_script(
            script.encode("utf-8"), entry["changes"])
    except Exception:  # noqa: BLE001 - mo phong key that bai -> duong cu
        new_script_b = None
        script = None
    if new_script_b is not None:
        req = _u.PatchRequest(
            data=cur, kind=kind, entry=req_entry,
            path_id=int(entry["object"]),
            old_text=script, new_text=new_script_b.decode("utf-8"),
            write_mode=entry["op"], backend=backend, temp_dir=None,
            from_container=from_container)
        return _u.apply_patch(req).output_bytes
    for ch in entry["changes"]:
        req = _u.PatchRequest(
            data=cur, kind=kind, entry=req_entry,
            path_id=int(entry["object"]),
            old_text=ch["before"], new_text=ch["after"],
            write_mode=entry["op"], backend=backend, temp_dir=None,
            from_container=from_container)
        res = _u.apply_patch(req)
        cur = res.output_bytes
    return cur


def _verify_entry(kind, unit_bytes, req_entry, entry, backend, lim,
                  want_after=True):
    """Doc lai script cua entry, tra (name, script, sha)."""
    name, script = _read_script(kind, unit_bytes, req_entry,
                                int(entry["object"]), backend, lim)
    sha = _sha(script.encode("utf-8"))
    if want_after and sha != entry["after_sha"]:
        raise ValueError(
            "entry %s: script sau khi ap khac after_sha trong pack "
            "(co the chuoi cu xuat hien nhieu cho) — khong xuat pack."
            % entry["id"])
    return name, script, sha


def _manifest_base(source_bytes, source_name, entries, skipped, mode,
                   unit_entry, unit_kind, pack_id):
    return {
        "format": PATCH_PACK_FORMAT,
        "pack_version": PATCH_PACK_VERSION,
        "version": plan_sdk.PLAN_VERSION,
        "id": pack_id,
        "pb3_version": PB3_VERSION,
        "source_file": os.path.basename(str(source_name)),
        "source_bundle": os.path.basename(str(source_name)),
        "source_sha256": _sha(bytes(source_bytes)),
        "unit_entry": unit_entry,
        "unit_kind": unit_kind,
        "mode": mode,
        "rebuild_all": False,
        "created_at": _now_iso(),
        "entries": entries,
        "skipped": skipped,
        "apply_script": APPLY_SCRIPT_NAME,
        "notes": _pack_notes(),
    }


def _selected_entries(plan):
    if plan.get("rebuild_all"):
        return list(plan["entries"])
    return [e for e in plan["entries"] if e.get("selected", True)]


def _prepare_plan_entries(source_bytes, source_name, entries):
    """Kiem tra + tinh lai after_sha that cho tung entry cua plan.

    - before_sha phai khop script hien tai (pack lap tu file khac thi
      tu choi ro rang, khong xuat pack sai).
    - after_sha tinh lai bang apply_patch that (khong dung mo phong):
      PATCH_INPLACE dem NUL khi chuoi moi ngan hon nen mo phong lech.
    Tra (entries_da_chuan_hoa, don_vi_da_giai_quyet).
    """
    source_bytes = bytes(source_bytes)
    be = _u.UnityBackend("NATIVE")
    lim = Limits()
    units: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for entry in entries:
        if entry.get("op") == "REBUILD_ALL":
            raise ValueError(
                "entry %s dung op REBUILD_ALL (che do ghi cua Unity) can "
                "maturity M4 (hien M2) — lap lai plan voi "
                "PATCH_INPLACE/REBUILD_SECTION de xuat pack."
                % entry.get("id"))
        if entry.get("op") not in _MODES:
            raise ValueError(
                "entry %s: op %r khong ho tro trong pack (chi %s)."
                % (entry.get("id"), entry.get("op"), "/".join(_MODES)))
        key = plan_sdk._unit_key(entry.get("vpath"),
                                 entry.get("kind") or "bundle",
                                 entry.get("entry"))
        if key not in units:
            kind, unit_bytes, req_entry, label = plan_sdk._resolve_unit(
                source_bytes, entry.get("vpath"), be, lim)
            _check_bundle_version(kind, unit_bytes, be, lim,
                                  entry.get("asset_path") or key)
            units[key] = {"kind": kind, "bytes": unit_bytes,
                          "req_entry": req_entry, "label": label}
        unit = units[key]
        kind = unit["kind"]
        _name, _script, before = _verify_entry(
            kind, unit["bytes"], unit["req_entry"], entry, be, lim,
            want_after=False)
        if before != entry["before_sha"]:
            raise ValueError(
                "entry %s: script hien tai khac before_sha trong plan "
                "(file goc co the da bi sua tu truoc) — khong xuat pack."
                % entry.get("id"))
        after_bytes = _apply_entry_changes(
            kind, unit["bytes"], unit["req_entry"], entry, be, lim,
            source_bytes, entry.get("vpath"))
        _n2, _s2, after = _verify_entry(
            kind, after_bytes, unit["req_entry"], entry, be, lim,
            want_after=False)
        e = copy.deepcopy(entry)
        if after != entry["after_sha"]:
            e.setdefault("warnings", []).append(
                "after_sha tinh lai bang apply_patch that (%s...) thay cho "
                "gia tri mo phong trong plan (%s...)."
                % (after[:8], (entry["after_sha"] or "")[:8]))
            e["after_sha"] = after
        e["operation"] = "replace_text"
        e.setdefault("selected", True)
        out.append(e)
    return out, units


def build_pack_from_plan(source_bytes, source_name, plan):
    """Xay patch pack tu plan JSON muc 8 (da validate)."""
    plan = plan_sdk.validate_plan(copy.deepcopy(plan))
    entries = _selected_entries(plan)
    if not entries:
        raise ValueError(
            "khong co entry nao duoc chon trong plan — tick it nhat "
            "1 entry, hoac bat REBUILD_ALL o tang plan.")
    entries, _units = _prepare_plan_entries(source_bytes, source_name,
                                            entries)
    modes = {e["op"] for e in entries}
    return _manifest_base(
        source_bytes, source_name, entries,
        list(plan.get("skipped") or []),
        plan.get("mode") or (modes.pop() if len(modes) == 1 else "mixed"),
        plan.get("unit_entry"), plan.get("unit_kind"),
        plan["id"])


def build_pack_from_mod(source_bytes, source_name, edits):
    """Xay patch pack tu edits cua tab Mod (mod_patch).

    edits: list [{"object": path_id, "old": "...", "new": "...",
    "entry": vpath (tuy chon), "mode": "PATCH_INPLACE"|"REBUILD_SECTION"}].
    Moi edit thanh 1 entry (1 change); before_sha/after_sha la sha cua
    TOAN BO script TextAsset truoc/sau edit do (tinh bang apply_patch
    that, noi tiep nhau tren unit bytes nhu mod_patch).
    """
    if isinstance(edits, (str, bytes)):
        edits = json.loads(edits)
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits phai la JSON list khong rong.")
    source_bytes = bytes(source_bytes)
    be = _u.UnityBackend("NATIVE")
    lim = Limits()
    units: Dict[str, Dict[str, Any]] = {}
    entries: List[Dict[str, Any]] = []
    for idx, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError("edit #%d phai la object." % (idx + 1))
        try:
            obj_id = int(edit["object"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("edit #%d thieu object (path_id so)."
                             % (idx + 1))
        old = edit.get("old")
        new = edit.get("new")
        if old is None or new is None:
            raise ValueError("edit #%d thieu old/new." % (idx + 1))
        mode = (edit.get("mode") or "PATCH_INPLACE").upper()
        if mode not in _MODES:
            raise ValueError(
                "edit #%d: mode %r khong ho tro trong pack (chi %s)."
                % (idx + 1, mode, "/".join(_MODES)))
        entry = edit.get("entry") or None
        key = entry or "<file>"
        if key not in units:
            kind, unit_bytes, req_entry, label = plan_sdk._resolve_unit(
                source_bytes, entry, be, lim)
            _check_bundle_version(kind, unit_bytes, be, lim, key)
            units[key] = {"kind": kind, "bytes": unit_bytes,
                          "req_entry": req_entry, "label": label}
        unit = units[key]
        kind = unit["kind"]
        name, _script, before = _verify_entry(
            kind, unit["bytes"], unit["req_entry"],
            {"id": "?", "object": obj_id, "after_sha": ""}, be, lim,
            want_after=False)
        change = {"key": str(old), "before": str(old), "after": str(new)}
        tmp = {"id": "?", "object": obj_id, "op": mode,
               "changes": [change], "after_sha": ""}
        after_bytes = _apply_entry_changes(
            kind, unit["bytes"], unit["req_entry"], tmp, be, lim,
            source_bytes, entry)
        _n2, _s2, after = _verify_entry(
            kind, after_bytes, unit["req_entry"], tmp, be, lim,
            want_after=False)
        unit["bytes"] = after_bytes
        unit_display = unit["label"] or (entry or
                                         os.path.basename(str(source_name)))
        if kind == "bundle":
            asset_path = "%s::%s::%s" % (unit_display, unit["req_entry"],
                                         name)
        else:
            asset_path = "%s::%s" % (unit_display, name)
        eid = _entry_id(source_name, entry, obj_id, name, [change])
        entries.append({
            "id": eid, "asset_path": asset_path,
            "asset_type": "TextAsset", "operation": "replace_text",
            "op": mode, "object": obj_id, "kind": kind,
            "entry": unit["req_entry"], "vpath": entry,
            "changes": [change],
            "preview_before": {str(old): str(old)},
            "preview_after": {str(old): str(new)},
            "before_sha": before, "after_sha": after,
            "selected": True, "warnings": [],
        })
    modes = {e["op"] for e in entries}
    kinds = {e["kind"] for e in entries}
    vpaths = {e["vpath"] for e in entries}
    pack_id = _entry_id(source_name, None, 0, "pack",
                        [e["id"] for e in entries])
    return _manifest_base(
        source_bytes, source_name, entries, [],
        modes.pop() if len(modes) == 1 else "mixed",
        vpaths.pop() if len(vpaths) == 1 else None,
        kinds.pop() if len(kinds) == 1 else "mixed",
        pack_id)


def build_patch_pack(source_bytes, source_name, kind, payload):
    """Diem vao chung: kind "plan" (plan JSON) hoac "mod" (edits JSON)."""
    if kind == "plan":
        plan = payload
        if isinstance(plan, (str, bytes)):
            plan = json.loads(plan)
        return build_pack_from_plan(source_bytes, source_name, plan)
    if kind == "mod":
        return build_pack_from_mod(source_bytes, source_name, payload)
    raise ValueError("kind pack phai la 'plan' hoac 'mod' (nhan %r)."
                     % (kind,))


PACK_README = """PATCH PACK PB3 — CACH DUNG
=========================

Goi nay gom:
  patch_manifest.json — danh sach thay doi (asset path -> change) + sha
    truoc/sau de kiem tra.
  apply_patch.py — script ap pack vao file game goc (chay tren iSH hoac
    may tinh co cay PB3).

1. Giai nen file .zip nay ra mot thu muc.
2. Dat file game GOC (file da dung de tao pack — xem source_file trong
   patch_manifest.json) vao cung thu muc, hoac chi duong dan toi no.
3. Chay kiem tra truoc (dry-run, KHONG ghi file nao):
     python3 apply_patch.py <duong-dan-file-goc>
   Script kiem tra: file goc co dung ban da dung de tao pack khong
   (source_sha256), tung asset co dung noi dung truoc khi ap khong
   (before_sha), roi tinh truoc ket qua va doi chieu after_sha.
4. Neu bao OK het thi ap that:
     python3 apply_patch.py <duong-dan-file-goc> --allow-write
   File da patch nam trong thu muc <ten-file>.pb3-patched/ ke ben file
   goc, kem ban sao luu don vi goc trong thu muc con backup-goc/.
   Script KHONG BAO GIO ghi de file goc.

Yeu cau: may chay script phai co cay PB3 (thu muc co sdk/unity.py).
Script tu tim cay PB3 qua bien moi truong PB3_ROOT, thu muc chua script,
thu muc lam viec, hoac thu muc goc cua du an tren iSH. Neu khong tim
thay, script bao loi ro rang kem cac noi da tim.

An toan: neu file goc khong khop, asset khong tim thay, bundle ma hoa
hoac chi doc (v9), script tu choi toan bo va khong ghi nua voi.
File da patch la don vi nho nhat (bundle/.assets), khong phai goi da
dong goi lai — tu dong goi lai neu can.
"""


def make_pack_zip(manifest, zip_path):
    """Ghi patch pack ra file .zip: manifest + script + README."""
    names = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2))
        names.append(MANIFEST_NAME)
        zf.writestr(APPLY_SCRIPT_NAME, APPLY_SCRIPT)
        names.append(APPLY_SCRIPT_NAME)
        zf.writestr(README_NAME, PACK_README)
        names.append(README_NAME)
    return {"zip_path": zip_path, "size": os.path.getsize(zip_path),
            "files": names}


APPLY_SCRIPT = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ap patch pack cua PB3/BearTea vao file game goc (iSH/desktop).

Cach dung:
    python3 apply_patch.py <file-goc> [--manifest patch_manifest.json]
    python3 apply_patch.py <file-goc> --allow-write [--out-dir <thu-muc>]

Mac dinh la DRY-RUN: chi kiem tra + tinh truoc, KHONG ghi file nao.
Chi ghi that khi co co --allow-write (dung quy tac PB3: patch mac
dinh dry-run). Script KHONG BAO GIO ghi de file goc: file da patch
nam trong thu muc <ten-file>.pb3-patched/ ke ben file goc, kem ban
sao luu don vi goc trong thu muc con backup-goc/.

Script tu choi toan bo (khong ghi nua voi) khi: file goc khong khop
source_sha256 trong manifest, script hien tai cua asset khong khop
before_sha, asset khong tim thay, bundle ma hoa / khong doc duoc,
bundle v9 (chi doc), entry dung op REBUILD_ALL (can maturity M4).

Yeu cau: Python >= 3.9 + cay PB3 (thu muc co sdk/unity.py). Script tu
tim cay PB3 qua bien moi truong PB3_ROOT, thu muc chua script, thu muc
lam viec, hoac thu muc goc cua du an tren iSH. Neu khong tim thay,
script bao loi ro rang kem cac noi da tim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

MANIFEST_NAME = "patch_manifest.json"
PACK_FORMAT = "pb3-patch-pack"
PACK_VERSION = 1
_REFUSE = 2  # ma thoat khi tu choi ap (kiem tra khong dat)


def _err(msg):
    print("LOI: " + msg, file=sys.stderr)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _find_pb3_root():
    """Tim thu muc goc cua cay PB3, tra None neu da import duoc san."""
    try:
        import sdk.unity  # noqa: F401
        return None
    except ImportError:
        pass
    cands = []
    env = os.environ.get("PB3_ROOT")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parent)
    cands.extend(here.parents)
    cands.append(Path.cwd())
    # Thu muc goc mac dinh cua du an tren iSH (ghep tu phan, khong ghi
    # cung duong dan tuyet doi trong ma nguon).
    cands.append(Path(os.sep + "root" + os.sep + "pb3"))
    seen = []
    for c in cands:
        try:
            c = Path(c)
        except (TypeError, ValueError):
            continue
        if c in seen:
            continue
        seen.append(c)
        if (c / "sdk" / "unity.py").is_file():
            return c
    return False


def _load_pb3():
    found = _find_pb3_root()
    if found is False:
        _err("khong tim thay cay PB3 (can thu muc co sdk/unity.py). "
             "Dat bien moi truong PB3_ROOT tro toi thu muc goc cua PB3, "
             "hoac dat script + manifest cung thu muc voi cay PB3, "
             "hoac chay script tu trong thu muc goc cua PB3.")
        raise SystemExit(_REFUSE)
    if found is not None:
        sys.path.insert(0, str(found))
    try:
        from core.limits import Limits  # noqa: F401
        from sdk import plan as plan_sdk  # noqa: F401
        from sdk import unity as unity_sdk  # noqa: F401
    except ImportError as exc:
        _err("tim thay thu muc PB3 nhung khong import duoc sdk (%s). "
             "Kiem tra lai cay PB3 (co the thieu file hoac sai phien ban)."
             % exc)
        raise SystemExit(_REFUSE)
    return Limits, plan_sdk, unity_sdk


def _unit_key(vpath, kind, req_entry):
    return "%s|%s|%s" % (vpath or "<file>", kind, req_entry or "")


def _read_script(plan_sdk, kind, unit_bytes, req_entry, pid, backend, lim):
    tas, _warn = plan_sdk._read_unit_text_assets(
        kind, unit_bytes, req_entry, backend, lim)
    for p, n, s in tas:
        if int(p) == int(pid):
            return n, s
    return None, None


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Ap patch pack PB3 vao file game goc (mac dinh dry-run).")
    ap.add_argument("target", help="file game goc (vd file .ipa/.unity3d/.assets)")
    ap.add_argument("--manifest", default=None,
                    help="duong dan patch_manifest.json "
                         "(mac dinh: cung thu muc voi script)")
    ap.add_argument("--allow-write", action="store_true",
                    help="ghi that file da patch (khong co co nay thi chi dry-run)")
    ap.add_argument("--out-dir", default=None,
                    help="thu muc ghi file da patch "
                         "(mac dinh: <ten-file>.pb3-patched/ ke ben file goc)")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.is_file():
        _err("khong tim thay file goc: %s" % target)
        return 1
    manifest_path = Path(args.manifest) if args.manifest else (
        Path(__file__).resolve().parent / MANIFEST_NAME)
    if not manifest_path.is_file():
        _err("khong tim thay manifest: %s (dat patch_manifest.json cung "
             "thu muc voi script, hoac chi --manifest)" % manifest_path)
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        _err("manifest khong doc duoc (%s): %s" % (manifest_path, exc))
        return 1
    if not isinstance(manifest, dict) or manifest.get("format") != PACK_FORMAT:
        _err("manifest khong phai patch pack PB3 (thieu format %r)."
             % PACK_FORMAT)
        return 1
    if manifest.get("pack_version") != PACK_VERSION:
        _err("pack_version %r khong ho tro (can %d)."
             % (manifest.get("pack_version"), PACK_VERSION))
        return 1
    entries = manifest.get("entries") or []
    if not isinstance(entries, list) or not entries:
        _err("manifest khong co entry nao.")
        return 1
    todo = [e for e in entries
            if manifest.get("rebuild_all") or e.get("selected", True)]
    if not todo:
        _err("khong co entry nao duoc chon trong pack.")
        return 1
    for e in todo:
        if e.get("op") == "REBUILD_ALL":
            _err("entry %s dung op REBUILD_ALL (che do ghi cua Unity) can "
                 "maturity M4 (hien M2) — khong ap pack nay."
                 % e.get("id"))
            return _REFUSE

    Limits, plan_sdk, unity_sdk = _load_pb3()
    try:
        data = target.read_bytes()
    except OSError as exc:
        _err("khong doc duoc file goc: %s" % exc)
        return 1
    source_sha = _sha(data)
    if manifest.get("source_sha256") and source_sha != manifest["source_sha256"]:
        _err("file goc khong khop pack: sha256 hien tai %s... khac "
             "source_sha256 trong manifest %s... — file co the da bi sua "
             "tu truoc. Tu choi ap, khong ghi gi."
             % (source_sha[:12], (manifest["source_sha256"] or "")[:12]))
        return _REFUSE

    backend = unity_sdk.UnityBackend("NATIVE")
    lim = Limits()
    # Nhom entry theo don vi (nhieu entry co the chung 1 bundle/CAB).
    units = {}
    order = []
    for e in todo:
        key = _unit_key(e.get("vpath"), e.get("kind") or "bundle",
                        e.get("entry"))
        if key not in units:
            units[key] = {"entries": [], "vpath": e.get("vpath"),
                          "kind": e.get("kind") or "bundle",
                          "req_entry": e.get("entry")}
            order.append(key)
        units[key]["entries"].append(e)

    patched = []  # (ten_file, bytes_don_vi_da_patch, [(id, asset_path)])
    problems = []
    for key in order:
        unit = units[key]
        try:
            kind, unit_bytes, req_entry, label = plan_sdk._resolve_unit(
                data, unit["vpath"], backend, lim)
        except Exception as exc:  # noqa: BLE001 - bao loi ro, khong ghi gi
            problems.append("%s: khong giai quyet duoc don vi (%s)"
                            % (key, exc))
            continue
        if kind == "bundle":
            try:
                bundle = unity_sdk.parse_bundle(unit_bytes, backend, lim,
                                                where="bundle")
            except Exception as exc:  # noqa: BLE001
                problems.append("%s: khong doc duoc bundle (%s) — co the "
                                "bundle ma hoa hoac hong." % (key, exc))
                continue
            if bundle.version >= 9:
                problems.append("%s: bundle UnityFS v%d chi doc — chua ho "
                                "tro patch." % (key, bundle.version))
                continue
        cur = bytes(unit_bytes)
        unit_ok = True
        for e in unit["entries"]:
            name, script = _read_script(plan_sdk, kind, cur, req_entry,
                                        int(e["object"]), backend, lim)
            if script is None:
                problems.append("%s: khong tim thay asset %s (path_id=%s) "
                                "trong don vi."
                                % (e.get("id"), e.get("asset_path"),
                                   e.get("object")))
                unit_ok = False
                break
            if _sha(script.encode("utf-8")) != e.get("before_sha"):
                problems.append(
                    "%s: script hien tai cua %s khac before_sha trong pack "
                    "— file co the da bi sua tu truoc. Tu choi ap."
                    % (e.get("id"), e.get("asset_path")))
                unit_ok = False
                break
            from_container = bool(unit["vpath"]) and \
                unity_sdk.detect_bundle(data) is None
            # Thay theo KEY truoc (giong executor plan sau P0-1 va giong
            # phia xay pack): mo phong toan bo script theo span gia tri
            # cua key, roi patch 1 lan. Gia tri rong "" ap dung dung.
            # Neu khong mo phong duoc (edit kieu Mod) thi roi ve vong
            # lap thay chuoi tran tung change nhu cu.
            try:
                _sim_b = plan_sdk._simulate_script(
                    script.encode("utf-8"), e["changes"])
            except Exception:  # noqa: BLE001 - mo phong key that bai
                _sim_b = None
            try:
                if _sim_b is not None:
                    req = unity_sdk.PatchRequest(
                        data=cur, kind=kind, entry=req_entry,
                        path_id=int(e["object"]),
                        old_text=script,
                        new_text=_sim_b.decode("utf-8"),
                        write_mode=e["op"], backend=backend, temp_dir=None,
                        from_container=from_container)
                    cur = unity_sdk.apply_patch(req).output_bytes
                else:
                    for ch in e["changes"]:
                        req = unity_sdk.PatchRequest(
                            data=cur, kind=kind, entry=req_entry,
                            path_id=int(e["object"]),
                            old_text=ch["before"], new_text=ch["after"],
                            write_mode=e["op"], backend=backend,
                            temp_dir=None,
                            from_container=from_container)
                        cur = unity_sdk.apply_patch(req).output_bytes
            except Exception as exc:  # noqa: BLE001
                problems.append("%s: ap that bai (%s)."
                                % (e.get("id"), exc))
                unit_ok = False
                break
            _n2, script2 = _read_script(plan_sdk, kind, cur, req_entry,
                                        int(e["object"]), backend, lim)
            if script2 is None or _sha(script2.encode("utf-8")) != e.get("after_sha"):
                problems.append(
                    "%s: script sau khi ap khac after_sha trong pack — "
                    "khong ghi don vi nay." % e.get("id"))
                unit_ok = False
                break
        if not unit_ok:
            continue
        # Ten file output: nhu web (don vi nho nhat, khong dong goi lai).
        try:
            from sdk.batch import _sdk_default_output_rel
            out_name = os.path.basename(_sdk_default_output_rel(
                str(target), kind, label))
        except Exception:  # noqa: BLE001 - ten du phong
            out_name = os.path.basename(label or "patched.bin")
        patched.append((out_name, bytes(unit_bytes), cur,
                        [(e["id"], e["asset_path"])
                         for e in unit["entries"]]))

    if problems:
        for p in problems:
            _err(p)
        _err("tu choi ap pack: %d loi — KHONG ghi file nao." % len(problems))
        return _REFUSE

    print("Kiem tra xong: %d entry tren %d don vi, tat ca khop "
          "before_sha/after_sha." % (len(todo), len(patched)))
    if not args.allow_write:
        print("DRY-RUN — chua ghi file nao. Chay lai voi --allow-write "
              "de ghi that.")
        return 0

    out_dir = Path(args.out_dir) if args.out_dir else (
        target.parent / (target.stem + ".pb3-patched"))
    # Chot dest != source: --out-dir do user chi co the trung thu muc
    # chua file goc, va ten don vi co the trung ten file goc. Neu dich
    # (hoac ban sao luu) trung file goc thi tu choi toan bo truoc khi
    # ghi gi — neu khong, tuyen bo "KHONG BAO GIO ghi de file goc" se
    # sai trong truong hop nay.
    try:
        _target_real = target.resolve()
    except OSError:
        _target_real = target.absolute()
    for _out_name, _o, _n, _e in patched:
        for _cand in (out_dir / _out_name,
                      out_dir / "backup-goc" / _out_name):
            try:
                _cand_real = _cand.resolve()
            except OSError:
                _cand_real = _cand.absolute()
            if _cand_real == _target_real:
                _err("thu muc output %s se ghi de file goc %s (ten don "
                     "vi %s trung ten file goc) — tu choi ap, khong ghi "
                     "gi. Chon --out-dir khac." % (out_dir, target, _out_name))
                return _REFUSE
    backup_dir = out_dir / "backup-goc"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _err("khong tao duoc thu muc output %s: %s" % (out_dir, exc))
        return 1
    written = []
    for out_name, orig_bytes, new_bytes, ents in patched:
        dest = out_dir / out_name
        bak = backup_dir / out_name
        n = 1
        while bak.exists() and bak.read_bytes() != orig_bytes:
            n += 1
            bak = backup_dir / ("%s.%d" % (out_name, n))
        try:
            bak.write_bytes(orig_bytes)
            dest.write_bytes(new_bytes)
        except OSError as exc:
            _err("khong ghi duoc %s: %s" % (dest, exc))
            return 1
        # Doc lai file vua ghi de verify lan cuoi.
        try:
            back = dest.read_bytes()
        except OSError as exc:
            _err("khong doc lai duoc file vua ghi %s: %s" % (dest, exc))
            return 1
        if back != new_bytes:
            _err("file vua ghi %s khac voi noi dung da tinh — dung lai, "
                 "kiem tra lai o dia." % dest)
            return 1
        written.append(str(dest))
        print("Da ghi: %s (%d entry: %s)"
              % (dest, len(ents), ", ".join(a for _i, a in ents)))
    print("DA GHI: %d file trong %s (ban sao luu don vi goc o %s). "
          "File goc KHONG bi thay doi."
          % (len(written), out_dir, backup_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
