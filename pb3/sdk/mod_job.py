"""Mod job: cong kiem tra + ep ban dich + repack IPA — chay tren may chu
(Colab / GitHub Actions), khong chay tren dien thoai.

Luong (dung dung SDK, khong viet lai logic):
1. doc IPA goc + JSON goc (source_texts) + JSON ban dich (translations);
2. chay cong kiem tra pre-write (`sdk.gate.run_gate`) -> ghi
   gate-report.json; verdict != PASS thi DUNG LAI (khong ep) — cong
   FAIL la KET QUA, khong phai loi (runner van thoat 0, summary ghi
   ro verdict);
3. neu gate_only thi dung sau cong; neu khong thi ep that
   (`sdk.rebuild.apply_rebuild`, dry_run=False, confirm=True,
   mode=apply_mode) -> ghi cac don vi da patch ra out/applied;
4. neu repack (chi khi file goc la IPA): dong goi lai
   (`sdk.repack.repack_from_rebuild_units`, kem kiem tra 6 muc) ->
   out/repack/<ten>-mod.ipa;
5. kiem lai che do pressed (`run_gate` voi pressed_bytes = file da ep)
   khi co file dich duy nhat cung loai voi file goc (IPA da repack,
   hoac .assets don khi goc la .assets); IPA goc + khong repack thi bo
   qua buoc nay (ghi ro ly do trong summary);
6. ghi summary.json + manifest Mod (`sdk.colab_job.build_mod_manifest` /
   `write_mod_manifest`) de web poll theo job_id roi nhap ket qua ve.
   summary["steps"] (F5) bao cao tung buoc: file nao DA GHI (ten +
   byte) va muc nao BI TU CHOI + vi sao (don vi bi tu choi, key chua
   ep, key bi tu choi do do dai, atlas thieu glyph, file vuot ngan
   sach artifact).

Gioi han that — artifact GitHub mien phi chi 500 MB:
`ARTIFACT_BUDGET_BYTES` = 400 MB (chua bien cho zip + cac artifact
khac). File nao vuot ngan sach thi KHONG dua vao artifact (ghi ro
trong summary["artifact_plan"] + ly do) — duong Actions chi tra
bao cao cong + file nho; IPA da repack >400 MB thi lay qua Colab
(Drive, khong gioi han 500 MB). Runner KHONG tu y bo file: moi file
bi loai deu co ten + dung luong + ly do trong summary.

Chi stdlib + SDK cua repo. Python >= 3.9. Khong goi mang, khong doc
token/key nao.

Thiet ke MO (mo rong engine sau nay — Unreal, Native iOS):
buoc dac thu Unity (cong/ep/repack) nam sau interface ModEngine
(xem ENGINES/get_engine): run_mod_job chi goi
engine.run_gate/apply/repack, khong goi truc tiep
sdk.gate/rebuild/repack. Muon them engine moi: hien thuc ModEngine +
dang ky vao ENGINES + them ten vao colab_job.JOB_ENGINES + tuy chon
UI. Hien chi co "unity" — engine khac fail-closed (E_CONFIG_INVALID)
truoc khi doc file nao. Job/UI/nhap ket qua khong gan Unity: job
mang truong "engine", manifest + summary ghi engine, web hien
verdict/file ket qua ma khong can biet engine nao.
Thoi gian tung buoc ghi trong summary["timings"] (gate/apply/repack/
pressed_gate + total) de do toc do ep tren game that.
Khong ghi thua: apply_rebuild ghi thang zip ket qua vao
out/applied.zip (khong ghi rebuild.zip rieng roi zip lai); truong
hop chi 1 don vi thi runner moi zip tay (apply_rebuild khong ghi
zip khi 1 don vi).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
try:
    import resource  # khong co tren Pyodide -> peak_ram_mb = 0.0
except ImportError:
    resource = None
import shutil
import time
import zipfile
from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from sdk import colab_job as _cj
from sdk import gate as _gate
from sdk import rebuild as _rb
from sdk import repack as _rp

# Ngan sach artifact cho duong Actions (mien phi 500 MB): chi giu
# 400 MB de chua bien cho nen zip + cac artifact nho khac.
ARTIFACT_BUDGET_BYTES = 400 * 1024 * 1024

GATE_REPORT_NAME = "gate-report.json"
PRESSED_GATE_REPORT_NAME = "pressed-gate-report.json"
SUMMARY_NAME = "summary.json"
APPLIED_ZIP_NAME = "applied.zip"

# Ly do key chua ep (giong sdk.gate._REASON_VI — ghi ngan cho step report).
_UNMATCHED_REASONS = {
    "monobehaviour": "key MonoBehaviour co trong game nhung chua ep duoc",
    "string_table_other_unit": "StringTable o don vi khac trong IPA",
    "not_found": "khong tim thay trong don vi nao",
    "not_json_dict": "TextAsset khong phai JSON dang dict",
    "non_string_value": "gia tri goc khong phai chuoi",
}

# Moi buoc chi liet ke toi da 50 muc bi tu choi (con lai dem vao
# rejected_total) — step report phai doc duoc, khong tran JSON.
_STEP_REJECTED_CAP = 50


def _step_file(path):
    if path and os.path.isfile(path):
        return {"name": os.path.basename(path),
                "bytes": os.path.getsize(path)}
    return None


def build_step_report(*, gate, out_dir, summary):
    """Bao cao tung buoc cua 1 Mod job (F5 — 1 luong duy nhat): moi buoc
    ghi ro file nao DA GHI (ten + byte) va muc nao BI TU CHOI (ten +
    ly do tieng Viet). Buoc nao chua chay thi status "skipped" kem ly
    do (vd cong FAIL nen khong ep). Doc gate-report.json /
    pressed-gate-report.json tu dia (da ghi truoc do), khong doi hoi
    caller truyen dict pressed.
    Tra list 5 buoc: gate, apply, repack, pressed, artifact.
    """
    gate = gate or {}
    steps = []
    # 1. Cong kiem tra.
    rejected = []
    for r in (gate.get("refused") or []):
        rejected.append({
            "item": "don vi %s" % (r.get("unit") or "?"),
            "reason": "[%s] %s" % (r.get("code"), r.get("message"))})
    for item in (gate.get("unmatched") or []):
        reason = item.get("reason") or "not_found"
        rejected.append({
            "item": "key %r" % (item.get("key") or "?"),
            "reason": _UNMATCHED_REASONS.get(reason, reason)})
    for chk in (gate.get("checks") or []):
        chk_id = chk.get("id")
        # (a)/(b)/(d)/(f)/(g): lay het failure (vd key thieu/trong,
        # placeholder lech, font thieu chu, qua dai, atlas thieu glyph).
        # (c)/(e): chi lay failure KHONG trung voi refused/unmatched da
        # liet ke o tren (vd preview_error, value_mismatch, loi cau
        # truc); kind unpressed/refused thi bo (da co o tren).
        if chk_id in ("translation_complete", "placeholders", "fonts",
                      "translation_length", "vietnamese_atlas_glyphs"):
            wanted = chk.get("failures") or []
        elif chk_id == "diffverify":
            wanted = [f for f in (chk.get("failures") or [])
                      if f.get("kind") not in ("unpressed", "refused")]
        elif chk_id == "integrity":
            wanted = [f for f in (chk.get("failures") or [])
                      if f.get("kind") != "refused"]
        else:
            wanted = []
        for f in wanted:
            if f.get("key"):
                item = "key %r" % f["key"]
            elif f.get("font"):
                item = "atlas %s" % f["font"]
            else:
                item = "%s: %s" % (chk_id or "?",
                                   f.get("kind") or "?")
            rejected.append({
                "item": item,
                "reason": f.get("message_vi") or f.get("kind") or "?"})
    gate_verdict = gate.get("verdict")
    gate_files = []
    entry = _step_file(os.path.join(out_dir, GATE_REPORT_NAME))
    if entry is not None:
        gate_files.append(entry)
    gate_step = {
        "step": "gate", "name_vi": "Cong kiem tra truoc khi giao",
        "status": "pass" if gate_verdict == "PASS" else "failed",
        "written": gate_files,
        "rejected": rejected[:_STEP_REJECTED_CAP],
        "rejected_total": len(rejected),
    }
    # Cong cat danh sach unmatched o 200 muc — neu tong that lon hon
    # so liet ke thi ghi ro de khong ai tuong chi co bay nhieu muc bi
    # tu choi (vd EK2: 1344 key chua ep, chi liet ke 200).
    if (len(rejected) > _STEP_REJECTED_CAP
            or (gate.get("unmatched_total") or 0)
            > len(gate.get("unmatched") or [])):
        gate_step["note"] = (
            "Chi liet ke %d muc bi tu choi dau tien — cong ghi tong "
            "%d key chua ep / %d don vi bi tu choi trong "
            "gate-report.json."
            % (min(_STEP_REJECTED_CAP, len(rejected)),
               gate.get("unmatched_total") or 0,
               len(gate.get("refused") or [])))
    steps.append(gate_step)
    # 2. Ep that.
    if summary.get("applied"):
        applied_files = []
        entry = _step_file(os.path.join(out_dir, APPLIED_ZIP_NAME))
        if entry is not None:
            applied_files.append(entry)
        applied_dir = os.path.join(out_dir, "applied")
        for u in (summary.get("applied_units") or []):
            out = u.get("output")
            if out:
                entry = _step_file(os.path.join(applied_dir, out))
                if entry is not None:
                    applied_files.append(entry)
        steps.append({
            "step": "apply", "name_vi": "Ep ban dich",
            "status": "done", "written": applied_files, "rejected": [],
            "rejected_total": 0,
        })
    else:
        reason = ("Cong kiem tra CHUA DAT nen khong ep."
                  if summary.get("status") == "gate_failed" else
                  "Chi chay cong (gate_only) nen khong ep."
                  if summary.get("status") == "gate_only_pass" else
                  "Chua ep.")
        steps.append({
            "step": "apply", "name_vi": "Ep ban dich",
            "status": "skipped", "written": [], "rejected": [],
            "rejected_total": 0, "skipped_reason": reason,
        })
    # 3. Repack.
    if summary.get("repacked"):
        repacked_files = []
        entry = _step_file(os.path.join(
            out_dir, "repack", summary.get("repacked_name") or ""))
        if entry is not None:
            repacked_files.append(entry)
        steps.append({
            "step": "repack", "name_vi": "Dong goi lai IPA",
            "status": "done", "written": repacked_files, "rejected": [],
            "rejected_total": 0,
        })
    else:
        reason = (summary.get("pressed_gate_skipped_reason")
                  or "Khong bat repack hoac cong chua dat.")
        if not summary.get("repack"):
            reason = "Khong bat repack (repack=false)."
        elif summary.get("status") == "gate_only_pass":
            reason = "Chi chay cong (gate_only) nen khong repack."
        elif summary.get("status") == "gate_failed":
            reason = "Cong kiem tra CHUA DAT nen khong repack."
        steps.append({
            "step": "repack", "name_vi": "Dong goi lai IPA",
            "status": "skipped", "written": [], "rejected": [],
            "rejected_total": 0, "skipped_reason": reason,
        })
    # 4. Kiem lai che do pressed.
    pressed_verdict = summary.get("pressed_gate_verdict")
    if pressed_verdict is not None:
        pressed_rejected = []
        pressed_path = os.path.join(out_dir, PRESSED_GATE_REPORT_NAME)
        try:
            with open(pressed_path, encoding="utf-8") as fh:
                pressed_gate = json.load(fh)
            for chk in (pressed_gate.get("checks") or []):
                for f in (chk.get("failures") or []):
                    pressed_rejected.append({
                        "item": ("key %r" % f.get("key")
                                 if f.get("key") else
                                 "%s: %s" % (chk.get("id") or "?",
                                             f.get("kind") or "?")),
                        "reason": f.get("message_vi") or "?"})
        except (OSError, ValueError):
            pressed_gate = None
        pressed_files = []
        entry = _step_file(pressed_path)
        if entry is not None:
            pressed_files.append(entry)
        steps.append({
            "step": "pressed", "name_vi": "Kiem lai file da ep",
            "status": ("pass" if pressed_verdict == "PASS" else "failed"),
            "written": pressed_files,
            "rejected": pressed_rejected[:_STEP_REJECTED_CAP],
            "rejected_total": len(pressed_rejected),
        })
    else:
        steps.append({
            "step": "pressed", "name_vi": "Kiem lai file da ep",
            "status": "skipped", "written": [], "rejected": [],
            "rejected_total": 0,
            "skipped_reason": (summary.get("pressed_gate_skipped_reason")
                               or "Khong co file dich duy nhat de kiem lai."),
        })
    # 5. Artifact (theo ngan sach Actions 400 MB).
    plan = summary.get("artifact_plan") or {}
    steps.append({
        "step": "artifact", "name_vi": "Chia file theo ngan sach artifact",
        "status": "done",
        "written": [{"name": e["name"], "bytes": e["bytes"]}
                    for e in (plan.get("included") or [])],
        "rejected": [{"item": e["name"], "reason": e.get("reason") or ""}
                     for e in (plan.get("excluded") or [])],
        "rejected_total": len(plan.get("excluded") or []),
    })
    return steps


def _err(code, message, hint=""):
    return PB3Error(code, message, hint=hint)


class ModEngine:
    """Interface chung cho 1 engine game trong Mod job.

    Moi engine phai hien thuc 3 buoc: cong kiem tra (run_gate),
    ep ban dich (apply), dong goi lai (repack). Chu ky ham + y nghia
    tra ve giong het ham SDK Unity ben duoi — engine moi chi can goi
    dung cac ham do, khong can biet runner lam gi voi ket qua (ghi
    report/manifest/artifact_plan o run_mod_job, khong gan engine).
    """

    name = "unity"

    def run_gate(self, source_bytes, *, source_name, translations,
                 source_texts, display_fonts, target_lang,
                 pressed_bytes=None, pressed_name=None):
        """Cong kiem tra (pre-write, hoac pressed khi co pressed_bytes).
        Tra dict giong sdk.gate.run_gate (verdict/totals/checks)."""
        raise NotImplementedError

    def apply(self, source_bytes, *, source_name, translations,
              out_dir, zip_path, mode):
        """Ep that (dry_run=False, confirm=True). Tra dict giong
        sdk.rebuild.apply_rebuild (ok/units/outputs/totals)."""
        raise NotImplementedError

    def repack(self, ipa_path, units, *, out_dir, units_dir):
        """Dong goi lai IPA tu cac don vi da ep. Tra dict giong
        sdk.repack.repack_from_rebuild_units (filename/size_after/
        dest/checks)."""
        raise NotImplementedError


class UnityModEngine(ModEngine):
    """Engine Unity: uy thac cho SDK hien co, khong viet lai logic."""

    name = "unity"

    def run_gate(self, source_bytes, *, source_name, translations,
                 source_texts, display_fonts, target_lang,
                 pressed_bytes=None, pressed_name=None):
        return _gate.run_gate(
            source_bytes, source_name=source_name,
            translations=translations, source_texts=source_texts,
            pressed_bytes=pressed_bytes, pressed_name=pressed_name,
            display_fonts=display_fonts, target_lang=target_lang)

    def apply(self, source_bytes, *, source_name, translations,
              out_dir, zip_path, mode):
        return _rb.apply_rebuild(
            source_bytes, source_name=source_name,
            translations=translations, dry_run=False, confirm=True,
            mode=mode, out_dir=out_dir, zip_path=zip_path,
            source_locale="en")

    def repack(self, ipa_path, units, *, out_dir, units_dir):
        return _rp.repack_from_rebuild_units(
            ipa_path, units, out_dir=out_dir, units_dir=units_dir)


# Registry engine Mod job (ten chu thuong -> ModEngine). Hien chi co
# Unity; them engine moi (Unreal, Native iOS) thi dang ky o day +
# them ten vao colab_job.JOB_ENGINES (co test khoa 2 ben khop nhau).
ENGINES = {"unity": UnityModEngine()}


def get_engine(name):
    """Lay engine theo ten (mac dinh "unity"). Ten la -> E_CONFIG_INVALID
    truoc khi doc file nao (fail-closed, khong lam do viec)."""
    key = str(name or "unity").strip().lower()
    try:
        return ENGINES[key]
    except KeyError:
        raise _err("E_CONFIG_INVALID",
                   "Engine %r chua ho tro — hien chi co %s."
                   % (name, ", ".join(sorted(ENGINES))),
                   "Dung engine unity (Unreal/Native iOS se them sau).")


def _md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path


def _load_json_dict(path, label):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise _err("E_INPUT_NOT_FOUND", "Khong tim thay file %s: %s."
                   % (label, os.path.basename(path)),
                   "Kiem tra lai duong dan file %s." % label)
    except ValueError as exc:
        raise _err("E_CORRUPT", "File %s khong phai JSON hop le: %s."
                   % (label, exc),
                   "Kiem tra lai file %s (phai la JSON object)." % label)
    if not isinstance(data, dict) or not data:
        raise _err("E_CONFIG_INVALID",
                   "File %s phai la JSON object khong rong (key -> chuoi)."
                   % label,
                   "Dung file JSON da trich/dich cua tool (vd en.json).")
    bad = [k for k, v in data.items() if not isinstance(v, str)]
    if bad:
        raise _err("E_CONFIG_INVALID",
                   "File %s co %d key ma gia tri khong phai chuoi "
                   "(vd %r)." % (label, len(bad), bad[0]),
                   "Ban dich phai la JSON object key -> chuoi.")
    return data


def _is_zip(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def _is_ipa(path):
    """File co phai IPA that khong: la ZIP va co dung 1
    Payload/*.app/ + Info.plist (cung quy tac nhu sdk.repack_ipa).
    Zip thuong (vd fixture zip 2 bundle) khong phai IPA."""
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    app = _rp._ipa_app_dir(names)
    return app is not None and ("Payload/%s/Info.plist" % app) in names


def _donor_font_bytes():
    """Font Be Vietnam Pro đi kèm trong repo (fonts/*.b64) — giai ma
    base64 (stdlib). Khong co file -> None (caller roi ve che do
    quet)."""
    here = os.path.dirname(os.path.abspath(__file__))
    b64_path = os.path.join(here, "..", "..", "fonts",
                            "BeVietnamPro-Regular.ttf.b64")
    try:
        with open(b64_path, "rb") as fh:
            return base64.b64decode(fh.read())
    except (OSError, ValueError):
        return None


def _display_fonts(display_font_path):
    """Giai quyet font hien thi cho cong (d), tra list truyen vao
    run_gate(display_fonts=...):
    - None (mac dinh): dung font di kem trong repo (donor); neu repo
      khong co file .b64 thi [] (= quet font trong file goc).
    - "scan": [] (quet nhu mac dinh web).
    - duong dan file .ttf: [entry cua font do].
    Ly do mac dinh donor: da do that — IPA 9K khong co font doc duoc
    nao nen che do quet luon FAIL check (d) du cac check khac deu DAT
    (cong 9K 2252 key: 35,7 giay, chi FAIL fonts)."""
    if display_font_path is None:
        data = _donor_font_bytes()
        if data is None:
            return []
        return [_gate.font_entry_from_bytes(data,
                                            "BeVietnamPro-Regular.ttf")]
    if str(display_font_path).strip().lower() == "scan":
        return []
    try:
        with open(str(display_font_path), "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise _err("E_INPUT_NOT_FOUND",
                   "Khong tim thay file font hien thi: %s (%s)."
                   % (display_font_path, exc),
                   "Dung font mac dinh (bo trong) hoac 'scan'.")
    return [_gate.font_entry_from_bytes(
        data, os.path.basename(str(display_font_path)))]


def artifact_plan(entries):
    """Chia file ket qua theo ngan sach artifact (400 MB).

    `entries`: [{name, bytes, kind, required}] theo thu tu uu tien
    (bao cao cong + summary luon required; applied.zip roi den IPA da
    repack). File required vuot ngan sach thi van included (bao cao
    phai ve duoc — no nho, tren thuc te khong bao gio vuot); file
    khong required vuot phan con lai thi excluded kem ly do tieng
    Viet. Tra {"included": [...], "excluded": [{name, bytes,
    reason}], "budget_bytes": ...}."""
    included = []
    excluded = []
    used = 0
    for e in entries:
        size = int(e.get("bytes") or 0)
        if e.get("required") or used + size <= ARTIFACT_BUDGET_BYTES:
            included.append({"name": e["name"], "bytes": size,
                             "kind": e.get("kind", "file")})
            used += size
        else:
            excluded.append({
                "name": e["name"], "bytes": size,
                "kind": e.get("kind", "file"),
                "reason": ("Vuot ngan sach artifact Actions (%d MB): file "
                           "%d MB, chi con %d MB trong ngan sach — lay "
                           "qua duong Colab (Drive) thay vi Actions."
                           % (ARTIFACT_BUDGET_BYTES // (1024 * 1024),
                              size // (1024 * 1024),
                              max(0, ARTIFACT_BUDGET_BYTES - used)
                              // (1024 * 1024))),
            })
    return {"included": included, "excluded": excluded,
            "budget_bytes": ARTIFACT_BUDGET_BYTES,
            "total_bytes": used}


def run_mod_job(*, ipa_path, original_path, translation_path, out_dir,
                game="", target_lang="vi", apply_mode="REBUILD_SECTION",
                repack=True, gate_only=False, job=None, progress_fn=None,
                display_font_path=None, engine="unity"):
    """Chay 1 Mod job (xem docstring module). Tra summary dict (da ghi
    ra out_dir/summary.json + manifest Mod). Cong FAIL -> summary
    status "gate_failed", khong ep, khong raise. Loi runner (file
    hong/thieu, ep loi, repack loi) -> raise PB3Error/ValueError (caller
    ghi manifest "failed" + thoat 1).
    display_font_path: None = font Be Vietnam Pro di kem trong repo
    (mac dinh — IPA 9K khong co font doc duoc nen quet luon FAIL);
    "scan" = quet font trong file goc nhu mac dinh web; duong dan
    file .ttf = dung font do.
    engine: ten engine ("unity" — duy nhat hien co; ten la thi
    E_CONFIG_INVALID truoc khi doc file). Buoc Unity chay qua
    UnityModEngine (xem ENGINES), khong goi truc tiep SDK o day.
    summary["timings"] ghi so giay tung buoc (gate/apply/repack/
    pressed_gate/total) de do toc do ep."""
    # Engine truoc tien — ten la thi loi ngay, chua doc file nao.
    eng = get_engine(engine)
    t0 = time.monotonic()
    timings = {}
    ipa_path = os.path.abspath(str(ipa_path))
    out_dir = os.path.abspath(str(out_dir))
    os.makedirs(out_dir, exist_ok=True)
    if apply_mode not in _cj.APPLY_MODES:
        raise _err("E_CONFIG_INVALID",
                   "Che do ep khong ho tro: %r." % apply_mode,
                   "Chon REBUILD_SECTION hoac PATCH_INPLACE.")
    if not os.path.isfile(ipa_path):
        raise _err("E_INPUT_NOT_FOUND",
                   "Khong tim thay file goc: %s." % ipa_path,
                   "Kiem tra lai duong dan file IPA/.assets.")
    source_name = os.path.basename(ipa_path)
    game = (game or "").strip() or os.path.splitext(source_name)[0]
    source_texts = _load_json_dict(original_path, "JSON goc")
    translations = _load_json_dict(translation_path, "JSON ban dich")
    display_fonts = _display_fonts(display_font_path)

    def _prog(done, total=5):
        if progress_fn is not None:
            try:
                progress_fn(done, total)
            except Exception:
                pass

    with open(ipa_path, "rb") as fh:
        source_bytes = fh.read()
    ipa_size = len(source_bytes)
    ipa_md5 = hashlib.md5(source_bytes).hexdigest()
    is_ipa = _is_ipa(ipa_path)

    # 1. Cong pre-write.
    t_gate0 = time.monotonic()
    gate = eng.run_gate(source_bytes, source_name=source_name,
                        translations=translations,
                        source_texts=source_texts,
                        display_fonts=display_fonts,
                        target_lang=target_lang)
    timings["gate_seconds"] = round(time.monotonic() - t_gate0, 1)
    gate_path = _write_json(os.path.join(out_dir, GATE_REPORT_NAME), gate)
    verdict = gate.get("verdict")
    _prog(1)

    applied_dir = os.path.join(out_dir, "applied")
    repack_dir = os.path.join(out_dir, "repack")
    summary = {
        "kind": "mod",
        "engine": eng.name,
        "game": game,
        "target_lang": target_lang,
        "apply_mode": apply_mode,
        "repack": bool(repack),
        "gate_only": bool(gate_only),
        "source_name": source_name,
        "ipa_size": ipa_size,
        "ipa_md5": ipa_md5,
        "gate_verdict": verdict,
        "gate_totals": gate.get("totals") or {},
        "gate_report": GATE_REPORT_NAME,
        "applied": False,
        "applied_units": [],
        "applied_zip": None,
        "repacked": False,
        "repacked_name": None,
        "repacked_bytes": 0,
        "repacked_md5": None,
        "pressed_gate_verdict": None,
        "pressed_gate_report": None,
        "pressed_gate_skipped_reason": None,
        "status": "gate_failed",
        "ok": False,
        "note": "",
    }

    def _finish(status, note=""):
        summary["status"] = status
        summary["ok"] = status in ("done", "gate_only_pass")
        summary["note"] = note
        elapsed = time.monotonic() - t0
        if resource is not None:
            peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        else:
            peak_mb = 0.0
        summary["elapsed_seconds"] = round(elapsed, 1)
        summary["timings"] = dict(timings)
        summary["timings"]["total_seconds"] = round(elapsed, 1)
        summary["peak_ram_mb"] = round(peak_mb, 1)
        # Manifest Mod: files[] — gate-report.json dau tien.
        entries = []
        if os.path.isfile(gate_path):
            entries.append(_cj.manifest_binary_entry(gate_path, "gate-report"))
        pressed_path = os.path.join(out_dir, PRESSED_GATE_REPORT_NAME)
        if os.path.isfile(pressed_path):
            entries.append(_cj.manifest_binary_entry(pressed_path, "gate-report"))
        applied_zip = os.path.join(out_dir, APPLIED_ZIP_NAME)
        if os.path.isfile(applied_zip):
            entries.append(_cj.manifest_binary_entry(applied_zip, "applied"))
        repacked_path = (os.path.join(repack_dir, summary["repacked_name"])
                         if summary.get("repacked_name") else None)
        if repacked_path and os.path.isfile(repacked_path):
            entries.append(_cj.manifest_binary_entry(repacked_path, "repacked"))
        # Plan artifact 2-pass: ghi summary (chua co plan) de do dung
        # luong that, roi tinh plan voi so do + 2 KB du phong cho chinh
        # doan plan, ghi lai.
        summary_path = os.path.join(out_dir, SUMMARY_NAME)
        summary.pop("artifact_plan", None)
        _write_json(summary_path, summary)
        summary_size = os.path.getsize(summary_path) + 2048
        summary["artifact_plan"] = artifact_plan([
            {"name": e["name"], "bytes": e["bytes"], "kind": e["kind"],
             "required": e["kind"] == "gate-report"}
            for e in entries
        ] + [{"name": SUMMARY_NAME, "bytes": summary_size,
              "kind": "summary", "required": True}])
        # Bao cao tung buoc (F5): file nao da ghi + muc nao bi tu choi
        # + vi sao — ghi vao summary de web/Actions hien thang.
        summary["steps"] = build_step_report(
            gate=gate, out_dir=out_dir, summary=summary)
        _write_json(summary_path, summary)
        # Manifest "done" khi da co bao cao cong (ket qua da ghi ro,
        # ke ca cong FAIL hay kiem lai pressed FAIL); "failed" chi khi
        # runner loi truoc khi co bao cao cong.
        manifest_status = "done" if os.path.isfile(gate_path) else "failed"
        manifest = _cj.build_mod_manifest(
            job, manifest_status, entries,
            gate=gate if manifest_status == "done" else None, note=note,
            job_id="mod-%s-%s" % (_cj.slugify_game(game),
                                  time.strftime("%Y%m%d-%H%M%S",
                                                time.gmtime())),
            game=game, engine=eng.name,
            out_dir=_cj.job_output_dir(source_name, game, suffix="-mod"))
        _cj.write_mod_manifest(out_dir, manifest)
        return summary

    # 2. Cong FAIL -> dung lai, khong ep.
    if verdict != "PASS":
        return _finish("gate_failed",
                       "Cong kiem tra CHUA DAT (verdict %s) — dung lai, "
                       "khong ep, khong repack. Sua ban dich theo "
                       "gate-report.json roi chay lai."
                       % verdict)
    if gate_only:
        return _finish("gate_only_pass",
                       "Chi chay cong (gate_only) — cong DAT, khong ep.")
    # Fail-fast TRUOC KHI EP (sau cong PASS): bat repack ma file goc
    # khong phai IPA thi loi ngay — truoc day check nay nam sau buoc
    # ep (chay het buoc ep ton phut roi moi bao loi, summary cung
    # khong duoc ghi). Dat sau cong de cong FAIL / gate_only van tra
    # ket qua binh thuong (khong ep thi khong can repack).
    if repack and not is_ipa:
        raise _err("E_CONFIG_INVALID",
                   "Da bat repack nhung file goc %r khong phai IPA "
                   "(khong co dau ZIP) — chi dong goi lai IPA duoc."
                   % source_name,
                   "Tat repack (repack=false) khi ep file "
                   ".assets/bundle don, hoac chon file IPA.")
    _prog(2)

    # 3. Ep that. apply_rebuild ghi thang zip ket qua vao
    # out/applied.zip (chi khi >1 don vi — 1 don vi thi khong ghi zip,
    # runner zip tay o duoi); khong ghi rebuild.zip rieng roi zip lai
    # nhu truoc (2 file zip giong het nhau, phi 1 lan ghi + 1 lan doc).
    t_apply0 = time.monotonic()
    applied_zip_path = os.path.join(out_dir, APPLIED_ZIP_NAME)
    applied = eng.apply(
        source_bytes, source_name=source_name, translations=translations,
        out_dir=applied_dir, zip_path=applied_zip_path, mode=apply_mode)
    timings["apply_seconds"] = round(time.monotonic() - t_apply0, 1)
    if not applied.get("ok"):
        raise _err("E_VERIFY_FAILED", "Ep that khong tra ok.",
                   "Xem gate-report.json roi chay lai.")
    units = applied.get("units") or []
    summary["applied"] = True
    summary["applied_units"] = [
        {"unit": u.get("unit"), "output": u.get("output"),
         "changes": u.get("changes"), "mode": u.get("mode")}
        for u in units
    ]
    summary["apply_totals"] = applied.get("totals") or {}
    _prog(3)
    # Chi zip tay khi apply_rebuild khong ghi zip (1 don vi) — neu
    # applied.zip da co (apply_rebuild vua ghi) thi dung luon, khong
    # ghi lai.
    applied_files = [os.path.join(applied_dir, o)
                     for o in (applied.get("outputs") or [])
                     if os.path.isfile(os.path.join(applied_dir, o))]
    if applied_files and not os.path.isfile(applied_zip_path):
        with zipfile.ZipFile(applied_zip_path, "w",
                             zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(applied_files):
                zf.write(p, os.path.basename(p))
    if os.path.isfile(applied_zip_path):
        summary["applied_zip"] = APPLIED_ZIP_NAME

    # 4. Repack (chi khi goc la IPA — da fail-fast o tren neu khong
    # phai IPA ma repack=true).
    pressed_bytes = None
    pressed_name = None
    if repack:
        t_repack0 = time.monotonic()
        repacked = eng.repack(
            ipa_path, units, out_dir=repack_dir, units_dir=applied_dir)
        timings["repack_seconds"] = round(time.monotonic() - t_repack0, 1)
        summary["repacked"] = True
        summary["repacked_name"] = repacked["filename"]
        summary["repacked_bytes"] = int(repacked["size_after"])
        summary["repacked_md5"] = _md5_of(repacked["dest"])
        summary["repack_checks"] = repacked.get("checks") or []
        with open(repacked["dest"], "rb") as fh:
            pressed_bytes = fh.read()
        pressed_name = repacked["filename"]
    else:
        # Khong repack: chi kiem pressed duoc khi goc la 1 file don
        # (.assets/bundle) va chi co 1 don vi da patch.
        if not is_ipa and len(applied_files) == 1:
            with open(applied_files[0], "rb") as fh:
                pressed_bytes = fh.read()
            pressed_name = os.path.basename(applied_files[0])
        else:
            summary["pressed_gate_skipped_reason"] = (
                "Khong repack IPA nen khong co 1 file dich duy nhat de "
                "kiem che do pressed — cong pre-write o tren da DAT.")
    _prog(4)

    # 5. Kiem lai che do pressed tren file da ep.
    if pressed_bytes is not None:
        t_pressed0 = time.monotonic()
        pressed_gate = eng.run_gate(
            source_bytes, source_name=source_name,
            translations=translations, source_texts=source_texts,
            pressed_bytes=pressed_bytes, pressed_name=pressed_name,
            display_fonts=display_fonts,
            target_lang=target_lang)
        timings["pressed_gate_seconds"] = round(
            time.monotonic() - t_pressed0, 1)
        _write_json(os.path.join(out_dir, PRESSED_GATE_REPORT_NAME),
                    pressed_gate)
        summary["pressed_gate_verdict"] = pressed_gate.get("verdict")
        summary["pressed_gate_report"] = PRESSED_GATE_REPORT_NAME
        if pressed_gate.get("verdict") != "PASS":
            return _finish("pressed_gate_failed",
                           "File da ep KIEM LAI KHONG DAT (pressed verdict "
                           "%s) — file da ep van nam trong out/repack "
                           "nhung khong nen giao; xem %s."
                           % (pressed_gate.get("verdict"),
                              PRESSED_GATE_REPORT_NAME))
    _prog(5)
    return _finish("done",
                   "Cong DAT, da ep %d thay doi o %d don vi%s."
                   % (summary["apply_totals"].get("changed", 0),
                      summary["apply_totals"].get("units", 0),
                      ", da dong goi lai IPA" if summary["repacked"]
                      else ", khong repack"))
