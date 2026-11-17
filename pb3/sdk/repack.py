"""(M2) Dong goi lai IPA tu cac don vi da mod [buoc 5].

Sau (E) "Ep va ghi file" (sdk/rebuild.py) chi xuat don vi nho nhat da
patch (bundle/.assets) — quyet dinh #70, van giu nguyen cho output
mac dinh cua (E) — buoc nay dong goi cac don vi do TRO LAI file IPA
goc de co file IPA cai duoc: thay tung don vi da patch vao dung vi tri
zip entry cua no, giu nguyen moi entry khac tung byte (noi dung),
giu thu tu/method/metadata nho sdk/containers.rebuild_zip (da co san,
duoc kiem chung rieng), roi KIEM TRA lai truoc khi cho tai ve.

Gioi han (fail-closed, ghi ro chu khong im lang):
- Chi nhan file IPA that (dung 1 Payload/*.app/ + Info.plist).
  File khong phai IPA/ZIP, bundle doc lap, entry ma hoa khong duoc thay
  (E_ENCRYPTED tu rebuild_zip) deu bi tu choi co ma ro rang.
- 2 don vi cung 1 file zip entry (vd 2 CAB trong cung 1 bundle) thi
  tu choi (E_CONFIG_INVALID): moi ban patch cua don vi la toan bo file
  bundle chi chua 1 CAB duoc patch nen khong ghep duoc neu khong ap
  lai plan — can ap lai theo thu tu, chua ho tro o buoc nay.
- Replacement > max_entry_bytes (64MB) thi tu choi theo gioi han (E).
- Chu ky cu (_CodeSignature) GIU NGUYEN tung byte nen da het hieu luc
  sau khi thay file — IPA moi can ky lai bang chung chi cua user truoc
  khi cai. Tool KHONG tu ky gia, KHONG be khoa, KHONG vuot DRM/FairPlay.

Chi thu vien chuan, Python >= 3.9. Khong goi mang. Khong bao gio ghi
de file goc: moi duong ghi deu co chot dest != source (rebuild_zip
kiem tra realpath/samefile + kiem tra them o day).
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import zipfile
from typing import Any, Dict, List, Optional, Union

from core.errors import PB3Error
from core.limits import Limits
from sdk import containers as _containers
from sdk.rebuild import REBUILD_OUT_DIR

REPACK_OUT_DIR = "/tmp/repack_out"

_CHUNK = 1024 * 1024


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_stream(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    """sha256 noi dung giai nen cua 1 entry, doc theo khoi (khong om
    ca entry vao RAM)."""
    h = hashlib.sha256()
    with zf.open(info, "r") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _ipa_app_dir(names: List[str]) -> Optional[str]:
    """Ten .app duy nhat duoi Payload/, hoac None neu khong dung 1 cai.

    Suy tu ten entry (ca entry thu muc "Payload/X.app/" lan entry file
    "Payload/X.app/...") vi IPA co the khong co entry thu muc rieng.
    """
    apps = set()
    for name in names:
        parts = name.split("/")
        if len(parts) >= 2 and parts[0] == "Payload" \
                and parts[1].endswith(".app"):
            apps.add(parts[1])
    if len(apps) == 1:
        return next(iter(apps))
    return None


def zip_entry_for_unit(vpath: Optional[str]) -> str:
    """Zip entry cua 1 don vi (E): cat phan "::entry" (CAB trong bundle).

    vpath dang "Payload/.../x.bundle::CAB-..." -> "Payload/.../x.bundle";
    dang "Payload/.../resources.assets" -> giu nguyen. vpath None /
    "<file>" (don vi la chinh file dau vao, khong nam trong ZIP) thi
    ValueError — khong doan vi tri.
    """
    if not vpath or vpath == "<file>":
        raise ValueError(
            "don vi %r khong nam trong file ZIP/IPA (la chinh file dau "
            "vao) — khong dong goi lai duoc." % (vpath,))
    return vpath.split("::", 1)[0]


def verify_repacked_ipa(orig_source: Union[str, os.PathLike, bytes, bytearray],
                        new_path: Union[str, os.PathLike],
                        replacements: Dict[str, bytes]) -> Dict[str, Any]:
    """Kiem tra IPA da dong goi lai (chi doc, khong ghi).

    Tra {"passed": bool, "checks": [{name, passed, detail}]} — that bai
    kiem tra nao cung tra passed=False (khong raise) de phia goi bao
    ro tung muc; chi raise khi khong mo duoc file de kiem.
    Cac muc: zip_integrity, entry_list, payload_structure,
    info_plist_intact, replaced_entries, unchanged_entries.
    """
    checks: List[Dict[str, Any]] = []

    def _check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed),
                       "detail": str(detail)})

    if isinstance(orig_source, (str, os.PathLike)):
        orig_zf = zipfile.ZipFile(str(orig_source), "r")
    else:
        orig_zf = zipfile.ZipFile(io.BytesIO(bytes(orig_source)), "r")
    try:
        new_zf = zipfile.ZipFile(str(new_path), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        orig_zf.close()
        raise PB3Error("E_VERIFY_FAILED",
                       "khong mo duoc IPA da dong goi de kiem: %s"
                       % type(exc).__name__, where=str(new_path))
    try:
        # 1. Zip toan ven.
        try:
            bad = new_zf.testzip()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError) as exc:
            bad = "loi doc: %s" % type(exc).__name__
        _check("zip_integrity", bad is None,
               "testzip() = %r (None la toan ven)" % (bad,))
        # 2. Danh sach + thu tu entry giong het goc.
        orig_names = [i.filename for i in orig_zf.infolist()]
        new_names = [i.filename for i in new_zf.infolist()]
        _check("entry_list", orig_names == new_names,
               "%d entry goc, %d entry moi%s"
               % (len(orig_names), len(new_names),
                  "" if orig_names == new_names
                  else " — KHAC (thu tu/ten lech)"))
        # 3. Cau truc Payload.
        app = _ipa_app_dir(new_names)
        plist = ("Payload/%s/Info.plist" % app) if app else None
        _check("payload_structure",
               app is not None and plist in new_names,
               "app = %r, Info.plist %s"
               % (app, "co mat" if plist in new_names else "THIEU"))
        # 4. Info.plist tung byte giong goc.
        if plist and plist in new_names:
            try:
                same = (_sha_stream(new_zf, new_zf.getinfo(plist))
                        == _sha_stream(orig_zf, orig_zf.getinfo(plist)))
            except KeyError:
                same = False
            _check("info_plist_intact", same,
                   "sha256 Info.plist %s giong goc"
                   % ("" if same else "KHONG"))
        else:
            _check("info_plist_intact", False,
                   "khong tim thay Info.plist de doi chieu")
        # 5. Entry da thay: co mat + sha == bytes da va.
        repl_ok = True
        repl_detail = []
        for name, data in sorted(replacements.items()):
            try:
                info = new_zf.getinfo(name)
            except KeyError:
                repl_ok = False
                repl_detail.append("%s: THIEU trong IPA moi" % name)
                continue
            got = _sha_stream(new_zf, info)
            want = _sha_bytes(bytes(data))
            if got != want:
                repl_ok = False
                repl_detail.append("%s: sha lech" % name)
            else:
                repl_detail.append("%s: khop (%d byte)"
                                   % (name, len(data)))
        _check("replaced_entries", repl_ok,
               "%d/%d entry da thay khop; %s"
               % (sum(1 for d in repl_detail if "khop" in d),
                  len(replacements), "; ".join(repl_detail)))
        # 6. Moi entry khac (khong phai thu muc): noi dung giong goc.
        orig_by_name = {i.filename: i for i in orig_zf.infolist()}
        diff = []
        checked = 0
        for info in new_zf.infolist():
            if info.filename in replacements or info.is_dir():
                continue
            checked += 1
            orig_info = orig_by_name.get(info.filename)
            if orig_info is None:
                diff.append("%s: them moi (la)" % info.filename)
                continue
            if _sha_stream(new_zf, info) != _sha_stream(orig_zf, orig_info):
                diff.append("%s: noi dung lech" % info.filename)
        _check("unchanged_entries", not diff,
               "%d entry doi chieu, %d lech%s"
               % (checked, len(diff),
                  (": " + "; ".join(diff[:5])) if diff else ""))
    finally:
        orig_zf.close()
        new_zf.close()
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def repack_ipa(source: Union[str, os.PathLike, bytes, bytearray],
               replacements: Dict[str, bytes], *,
               out_dir: str = REPACK_OUT_DIR,
               limits: Optional[Limits] = None) -> Dict[str, Any]:
    """Dong goi lai IPA: thay entry trong `replacements` roi kiem tra.

    source: duong dan file IPA hoac bytes. replacements: {zip entry
    path: bytes moi}. Tra dict ket qua (ok/dest/filename/size_*/replaced/
    entries/checks/warnings/note). Loi toan cuc raise PB3Error/ValueError;
    kiem tra sau dong goi FAIL thi xoa output + raise E_VERIFY_FAILED.
    """
    if not isinstance(replacements, dict) or not replacements:
        raise ValueError("replacements phai la dict {zip entry: bytes} "
                         "khong rong.")
    for name, data in replacements.items():
        if not isinstance(name, str) or not name:
            raise ValueError("ten entry phai la chuoi khong rong.")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("replacement cho %r phai la bytes." % name)
    lim = limits or Limits()
    if isinstance(source, (str, os.PathLike)):
        src_name = str(source)
        try:
            size_before = os.path.getsize(src_name)
        except OSError:
            size_before = 0
        src_bytes: Optional[bytes] = None
    else:
        src_name = "<bytes>"
        src_bytes = bytes(source)
        size_before = len(src_bytes)
    # Kiem tra truoc: phai la ZIP + phai la IPA (1 Payload/*.app +
    # Info.plist); moi replacement phai co trong archive.
    try:
        zf = (zipfile.ZipFile(src_name, "r") if src_bytes is None
              else zipfile.ZipFile(io.BytesIO(src_bytes), "r"))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "file goc khong phai ZIP/IPA (%s) — khong dong goi "
                       "lai duoc." % type(exc).__name__, where=src_name)
    try:
        names = [i.filename for i in zf.infolist()]
    finally:
        zf.close()
    app = _ipa_app_dir(names)
    if app is None or ("Payload/%s/Info.plist" % app) not in names:
        raise PB3Error(
            "E_UNSUPPORTED_FORMAT",
            "file goc khong phai IPA (khong co dung 1 Payload/*.app/ + "
            "Info.plist) — chi dong goi lai IPA.", where=src_name)
    for name in replacements:
        if name not in names:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "entry %r khong co trong IPA goc — khong thay "
                           "duoc." % name, where=name)
    stem = os.path.basename(src_name) if src_name != "<bytes>" else "game"
    for suffix in (".ipa", ".IPA", ".zip", ".ZIP"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    out_name = "%s-mod.ipa" % (stem or "game")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, out_name)
    if isinstance(source, (str, os.PathLike)) \
            and os.path.realpath(dest) == os.path.realpath(src_name):
        raise PB3Error("E_CONFIG_INVALID",
                       "khong ghi de file goc: dich trung nguon",
                       where=dest)
    report = _containers.rebuild_zip(
        source, dest, dict(replacements), limits=lim, verify=True)
    verified = verify_repacked_ipa(source, dest, dict(replacements))
    if not verified["passed"]:
        try:
            os.unlink(dest)
        except OSError:
            pass
        bad = [c for c in verified["checks"] if not c["passed"]]
        raise PB3Error(
            "E_VERIFY_FAILED",
            "IPA dong goi lai KHONG DAT kiem tra (%s) — da xoa file loi, "
            "khong cho tai ve."
            % "; ".join("%s: %s" % (c["name"], c["detail"]) for c in bad),
            where=dest)
    return {
        "ok": True, "dest": dest, "filename": out_name,
        "source_name": os.path.basename(src_name),
        "size_before": int(size_before),
        "size_after": int(report.bytes_written),
        "replaced": sorted(replacements),
        "entries": int(report.entries),
        "checks": verified["checks"],
        "warnings": list(report.warnings or []),
        "note": "IPA da dong goi lai tu %d don vi da patch; chu ky cu "
                "(_CodeSignature) giu nguyen nen da het hieu luc — can "
                "ky lai bang chung chi cua ban truoc khi cai."
                % len(replacements),
    }


def repack_from_rebuild_units(source: Union[str, os.PathLike, bytes, bytearray],
                              units: List[Dict[str, Any]], *,
                              out_dir: str = REPACK_OUT_DIR,
                              units_dir: str = REBUILD_OUT_DIR,
                              limits: Optional[Limits] = None) -> Dict[str, Any]:
    """Dong goi lai IPA tu ket qua (E): units = [{"unit": vpath,
    "output": ten file da ghi}] (truong "output" do sdk/rebuild.py gan
    sau khi ap that). Doc bytes da va tu units_dir, dung vao entry zip,
    kiem tra va cham (2 don vi cung 1 entry -> tu choi)."""
    if not isinstance(units, list) or not units:
        raise ValueError("units phai la list [{unit, output}] khong rong "
                         "(lay tu ket qua ap (E) — chay 'Ep va ghi file' "
                         "truoc).")
    replacements: Dict[str, bytes] = {}
    unit_rows = []
    seen_entry: Dict[str, str] = {}
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("moi unit phai la object {unit, output}.")
        vpath = unit.get("unit")
        output = unit.get("output")
        try:
            entry = zip_entry_for_unit(vpath)
        except ValueError as exc:
            raise PB3Error("E_CONFIG_INVALID", str(exc), where=str(vpath))
        if not output:
            raise PB3Error(
                "E_INPUT_NOT_FOUND",
                "don vi %r chua duoc ghi ra file (output trong) — chay "
                "'Ep va ghi file' truoc roi moi dong goi lai."
                % (vpath,), where=str(vpath))
        if entry in seen_entry:
            raise PB3Error(
                "E_CONFIG_INVALID",
                "2 don vi cung thay 1 file %r (%r va %r) — moi ban patch "
                "la toan bo file chi chua 1 don vi duoc patch nen khong "
                "ghep duoc; can ap lai theo thu tu, chua ho tro o buoc "
                "nay." % (entry, seen_entry[entry], vpath),
                where=entry)
        seen_entry[entry] = str(vpath)
        fpath = os.path.join(units_dir, os.path.basename(str(output)))
        try:
            with open(fpath, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise PB3Error(
                "E_INPUT_NOT_FOUND",
                "khong doc duoc file don vi da patch %r (%s) — chay "
                "'Ep va ghi file' lai truoc roi moi dong goi."
                % (fpath, exc), where=fpath)
        replacements[entry] = data
        unit_rows.append({"unit": vpath, "entry": entry,
                          "output": os.path.basename(str(output)),
                          "size": len(data)})
    res = repack_ipa(source, replacements, out_dir=out_dir, limits=limits)
    res["units"] = unit_rows
    return res
