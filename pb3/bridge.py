"""bridge.py — Cầu nối web cho PB3 (BearTea) chạy trong Pyodide.

Chạy trong trình duyệt (GitHub Pages), không phải iSH. Chỉ gọi API tầng sdk
của PB3 (không dùng services/ vì nó gắn với menu chữ/terminal). Mọi hàm trả
về chuỗi JSON để JS đọc; lỗi cũng trả JSON {"ok": False, ...} chứ không raise
qua biên Python/JS.

Hai điều chỉnh bắt buộc trên Pyodide (đã thử thật ở giai đoạn 1, xem
../exp/phase1_results.md — KHÔNG sửa mã PB3):
  1. Pyodide bản mặc định không start được thread -> import_guard của
     registry (chạy import plugin trong thread riêng) làm toàn bộ 20 plugin
     bị disabled. Khởi động bằng build_registry(..., import_guard=None).
  2. Pyodide/Emscripten không có os.link -> shim os.link = os.replace
     (compress_ipa đã tự kiểm tra E_OUTPUT_EXISTS trước nên tương đương).

Không dùng được trên web (ghi rõ, đừng gọi): UnityPy (micropip fail vì
thiếu wheel etcpak), mọi tính năng cần threading trong MỘT worker
(batch trong một worker vẫn jobs=1 — song song trên web đi bằng POOL
2 WORKER độc lập, mỗi worker 1 Pyodide + PB3 riêng, xem batch_split /
batch_run out_dir_name; KHÔNG dùng pthreads/SharedArrayBuffer/COI),
Google Translate qua urllib (urllib không gọi mạng được trong Pyodide).
Dịch Google trên web đi đường khác: `gt.js` dùng fetch() trong Worker,
Python gọi hàm JS đó qua module `js` từ hàm async `translate_json_google`
(xem dưới) — Python async await được Promise JS nên event loop vẫn chạy
và nút Hủy vẫn nhận được giữa đợt dịch.

Mod (menu [1] iSH) / Batch (menu [4]) / KB (menu [3]) / ma trận hỗ trợ:
xem mod_patch / batch_run / kb_* / support_matrix ở cuối file — đều gọi
API tầng sdk (sdk.unity.apply_patch, sdk.batch.BatchRunner, sdk.kb.KB,
sdk.support_matrix.generate), không dùng services/ (gắn với menu chữ).
KB tự lưu vào IndexedDB sau mỗi thay đổi (app.js gọi kb_snapshot sau
mỗi lần KB đổi, kb_restore nạp lại lúc boot); tải lại trang thì khôi
phục lại. Nút xuất .sqlite (kb_export) vẫn là đường sao lưu chính.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import plistlib
import re
import shutil
import struct
import sys
import time
import zipfile

from core.errors import PB3Error

WEB_ROOT = "/pb3web"
# Giới hạn riêng của bản web (RAM tab trình duyệt, không phải giới hạn PB3):
# file đầu vào >2 GiB thì từ chối rõ thay vì để trình duyệt crash.
# Lưu ý: file gần 2GB vẫn có thể làm tab Safari trên iPhone văng vì RAM.
MAX_WEB_INPUT = 2 * 1024 * 1024 * 1024
# Ngân sách RAM tab web cho cổng kiểm tra / ép file (gate-crash-fix,
# [ĐÃ ĐO THẬT] trên VM — xem _check_web_memory): tab Safari ~1 GB.
# File gốc tốn: Python ~GATE_PY_PEAK_SLOPE × cỡ file + GATE_PY_PEAK_FIXED
# (mô phỏng ép + quét đơn vị) + MEMFS 1× + ArrayBuffer JS 1× + nền
# ~250 MB (Pyodide + trình duyệt). File đã ép kèm theo (cổng chế độ
# pressed): thêm ~3× cỡ file đó (MEMFS + Python + JS). Vượt ngân sách
# thì từ chối lịch sự kèm hướng xử lý, thay vì để Safari sập tab.
WEB_TAB_BUDGET_BYTES = 950 * 1024 * 1024
WEB_TAB_BASELINE_BYTES = 250 * 1024 * 1024
GATE_PY_PEAK_SLOPE = 4.0  # [ĐÃ ĐO THẬT] sau sửa: (19 MB -> 148 MB,
# 165 MB -> 714 MB) cho ra slope ~3.9, intercept ~74 MB — làm tròn lên
# 4.0 / 75 MB để có biên an toàn. Trước sửa slope là ~5.3.
GATE_PY_PEAK_FIXED = 75 * 1024 * 1024
MAX_TEXT_HEAD = 200          # số ký tự TextAsset hiển thị mỗi object
MAX_LIST_TEXT_ASSETS = 50    # như _COMPACT_MAX_TEXT_ASSETS của PB3
MAX_STRINGS = 200
STRINGS_SCAN_BYTES = 1024 * 1024


def _check_web_memory(path, what, extra_bytes=0):
    """Ước tính đỉnh RAM tab rồi từ chối lịch sự nếu vượt ngân sách.

    Gọi TRƯỚC khi đọc/mô phỏng file trong delivery_gate / rebuild_apply.
    what: "kiểm tra trước khi giao" / "ép file" (hiện trong thông báo).
    extra_bytes: cỡ file đã ép kèm theo (chế độ pressed) — tính thêm 3×.
    Không raise khi file nhỏ/vừa; raise ValueError kèm số ước tính +
    hướng xử lý khi ước tính vượt WEB_TAB_BUDGET_BYTES.
    """
    try:
        n = os.path.getsize(path)
    except OSError:
        return
    py_peak = GATE_PY_PEAK_SLOPE * n + GATE_PY_PEAK_FIXED
    tab_peak = py_peak + 2 * n + 3 * extra_bytes + WEB_TAB_BASELINE_BYTES
    if tab_peak <= WEB_TAB_BUDGET_BYTES:
        return
    def _mb(b):
        return "%.0f MB" % (b / 1024.0 / 1024.0)
    raise ValueError(
        "File %s (%s) cần khoảng %s RAM để %s trên bản web "
        "(Python ~%s + file trong tab ~%s + nền ~250 MB), vượt giới hạn "
        "tab Safari (~950 MB) — trình duyệt sẽ sập tab thay vì báo lỗi. "
        "Hãy chọn file .assets lẻ ở bước 1 thay vì cả IPA (chỉ ép đơn vị "
        "cần dịch), hoặc dùng bản iSH cho file này."
        % (os.path.basename(path), _mb(n), _mb(tab_peak), what,
           _mb(py_peak), _mb(2 * n + 3 * extra_bytes)))


def _apply_shims():
    # Pyodide/Emscripten không có os.link (sdk/compress.py, core/safe_io.py
    # dùng để publish độc quyền). Shim bằng os.replace.
    if not hasattr(os, "link"):
        def _link(src, dst, *, follow_symlinks=True):
            os.replace(src, dst)

        os.link = _link


_apply_shims()
if WEB_ROOT not in sys.path:
    sys.path.insert(0, WEB_ROOT)

_app = None
_reg = None
_progress_cb = None


def set_progress_cb(cb):
    """JS (worker) đăng ký callback tiến độ: cb(kind, done, total)."""
    global _progress_cb
    _progress_cb = cb


def _emit(kind, done, total):
    cb = _progress_cb
    if cb is None:
        return
    try:
        cb(kind, int(done), int(total) if total is not None else -1)
    except Exception:
        pass


def _err(e):
    return json.dumps(
        {"ok": False, "error": str(e), "code": getattr(e, "code", None)},
        ensure_ascii=False)


def _check_size(path):
    n = os.path.getsize(path)
    if n > MAX_WEB_INPUT:
        raise ValueError(
            "File quá lớn cho bản web (>2GB). Bản web giới hạn 2GB vì RAM "
            "tab trình duyệt; file lớn hơn hãy dùng bản iSH.")
    return n


def boot():
    """Khởi động PB3 trong Pyodide: bootstrap + registry không guard."""
    global _app, _reg
    try:
        from core.app import bootstrap
        from registry.registry import build_registry

        _app = bootstrap(WEB_ROOT, keepalive=False)
        _reg = build_registry(str(_app.paths.root), _app.config,
                              import_guard=None)
        ids = sorted({r.plugin_id for r in _reg.active()})
        return json.dumps({
            "ok": True,
            "plugins": len(ids),
            "plugin_ids": ids,
            "problems": list(getattr(_app, "problems", []) or []),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 - trả lỗi về JS thay vì raise
        return _err(e)


def list_entries(path):
    """Liệt kê entry của container (ZIP/IPA/bundle) — inventory bước 1.

    LƯỜI (lazy): chỉ đọc 2 KB đầu để nhận diện + central directory của
    ZIP — KHÔNG nạp cả file vào RAM như trước (file Tom 210 MB từng làm
    RAM đỉnh Python vọt lên 227 MB chỉ để liệt kê; đo [ĐÃ ĐO THẬT] bằng
    ru_maxrss: lazy còn ~29 MB). VFS mở theo đường dẫn (ZipVFS lười);
    file Unity độc lập >64 MB dùng SingleFileVFS lười (đã có từ trước).
    Output giữ nguyên định dạng cũ ({path, size, kind}).
    """
    try:
        from core.limits import Limits
        from sdk import containers, discovery as disc

        _check_size(path)
        with open(path, "rb") as fh:
            head = fh.read(2048)
        kind = containers.detect_container_kind(head)
        vfs = disc.open_source_vfs(path, Limits())
        inv = disc.inventory(vfs, Limits())
        entries = [{"path": e.path, "size": int(e.size), "kind": e.kind}
                   for e in inv]
        return json.dumps({"ok": True, "kind": kind, "total": len(entries),
                           "entries": entries}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def inspect_deep(path, entry=None):
    """Xem sâu (deep inspect): liệt kê từng bundle/.assets trong file
    (tên, kích thước, UnityFS version, compression, số object theo loại,
    nguồn ngôn ngữ tìm thấy, lý do pipeline trích xuất bỏ qua nếu có).

    Đọc bundle theo luồng (không ôm cả payload); file trong ZIP/IPA đọc
    theo từng entry. Chỉ đọc, không sửa. entry: chỉ xem sâu 1 entry Unity
    (None = xem tất cả).
    """
    try:
        from sdk import inspect as insp

        _check_size(path)
        out = insp.inspect_container(path, entry=entry)
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def raw_scan(path, entry=None, min_len=None, max_results=None):
    """Trích sâu (raw-string scan, P1-5) — LAST RESORT, labelled unverified.

    Quét chuỗi thô theo khối (streaming, không nạp cả file vào RAM).
    Chỉ chạy khi user bật tường minh; output ghi nhãn unverified và
    TÁCH RIÊNG khỏi file locale chính (extract không bao giờ gọi op này).
    entry: quét 1 entry trong ZIP/IPA; None = quét cả file. Mục tiêu
    được tự nhận diện bằng magic (không hardcode theo game):
    - bundle UnityFS -> quét payload đã giải nén (mode "unity-bundle");
    - global-metadata.dat IL2CPP -> đọc literal chính xác theo bảng
      literal (mode "il2cpp", metadata v31/v39 đã xác minh);
    - Mach-O (thin 64-bit / FAT) -> quét theo section __TEXT,__cstring
      + __TEXT,__ustring (mode "macho");
    - còn lại -> quét byte thô (mode "raw").
    min_len: độ dài chuỗi tối thiểu (mặc định 4).
    """
    try:
        from sdk import rawscan

        _check_size(path)
        kw = {}
        if min_len is not None:
            kw["min_len"] = int(min_len)
        if max_results is not None:
            kw["max_results"] = int(max_results)
        kw["progress_fn"] = lambda done, total: _emit(
            "raw_scan", done, total if total is not None else -1)
        out = rawscan.scan_target(path, entry=(str(entry) if entry else None),
                                  **kw)
        out["ok"] = True
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def il2cpp_table(path, entry=None, include_single_words=None):
    """Xuất bảng dịch literals IL2CPP (đường literals -> tab Dịch).

    Đọc literal chính xác từ global-metadata.dat (entry trong IPA/ZIP
    hoặc file thường), xuất thành dict key->value thuần
    {LIT_NNNNNN: text} + manifest {key: {literal_index, offset,
    byte_length, source}} rồi ghi 2 file vào /tmp/pb3out để tab Dịch
    nạp qua read_extracted như mọi file trích xuất (il2cpp-literals.json
    + il2cpp-literals.manifest.json). Bảng là dict thuần nên
    translate_json/translate_json_google ăn được — không còn hiện tượng
    stringify cả list như output raw_scan. Chỉ lấy chuỗi nhiều từ mặc
    định (single_words là tên mã, chỉ thêm khi xin tường minh).
    """
    try:
        from sdk import il2cpp as il2cpp_sdk
        from sdk import rawscan

        _check_size(path)
        out = rawscan.scan_target(
            path, entry=(str(entry) if entry else None))
        if out.get("mode") != "il2cpp":
            raise ValueError(
                "entry không phải global-metadata.dat IL2CPP (mode=%s) — "
                "chọn đúng entry global-metadata.dat (vd .../Data/Managed/"
                "Metadata/global-metadata.dat)." % (out.get("mode"),))
        table, manifest = il2cpp_sdk.literals_to_table(
            out, include_single_words=bool(include_single_words))
        out_dir = "/tmp/pb3out"
        os.makedirs(out_dir, exist_ok=True)
        table_text = json.dumps(table, ensure_ascii=False, indent=1,
                                sort_keys=True)
        manifest_text = json.dumps(
            {"format": "beartea-il2cpp-literals-manifest/1",
             "source": out.get("source"),
             "metadata_version": out.get("metadata_version"),
             "literals_total": out.get("literals_total"),
             "keys": manifest},
            ensure_ascii=False, indent=1, sort_keys=True)
        with open(os.path.join(out_dir, il2cpp_sdk.TABLE_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write(table_text)
        with open(os.path.join(out_dir, il2cpp_sdk.MANIFEST_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write(manifest_text)
        return json.dumps({
            "ok": True, "source": out.get("source"),
            "metadata_version": out.get("metadata_version"),
            "literals_total": int(out.get("literals_total") or 0),
            "literals_read": int(out.get("literals_read") or 0),
            "table_keys": len(table),
            "manifest_keys": len(manifest),
            "single_words_kept": int(out.get("single_words_kept") or 0),
            "single_words_included": bool(include_single_words),
            "truncated": bool(out.get("truncated")),
            "table_file": il2cpp_sdk.TABLE_FILE,
            "manifest_file": il2cpp_sdk.MANIFEST_FILE,
            "table_bytes": len(table_text.encode("utf-8")),
            "note": ("Bảng dịch literals: %d key (chuỗi nhiều từ). "
                     "Từ đơn (%d) không đưa vào bảng mặc định — phần lớn "
                     "là tên class/method/field, không phải text hiển thị."
                     % (len(table), out.get("single_words_kept") or 0)),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def patch_il2cpp_literals(path, entry, table_json, manifest_json=None,
                          relayout=False):
    """Ép bản dịch literals VÀO global-metadata.dat.

    table_json: dict {LIT_NNNNNN: text_đã_dịch} (lấy từ output tab Dịch
    sau khi dịch il2cpp-literals.json). manifest_json: manifest kèm
    theo bảng (để verify text gốc từng key trước khi vá — sai file
    metadata thì bỏ qua key đó, không vá mù). relayout: True thì dựng
    lại blob literal (dời các section sau blob) nên bản dịch DÀI HƠN
    gốc vẫn ghi được ở cả v31 lẫn v39; False (mặc định, giữ hành vi
    cũ) thì vá tại chỗ, fail-closed từng literal (v31: bản dịch <= gốc
    theo byte; v39: phải đúng bằng byte; dài hơn thì bỏ qua kèm lý
    do) rồi VERIFY bằng cách đọc lại toàn bộ literal. File gốc KHÔNG
    bị ghi đè (xác minh sha sau khi chạy); metadata đã vá ghi ra
    /tmp/pb3out/global-metadata.patched.dat + sidecar JSON, trả `dest`
    để worker gửi file về tải (như mọi op có output). Game có chạy
    được với metadata re-layout không là [HYPOTHESIS] — chưa test trên
    máy thật, chỉ verify được cấu trúc file.
    """
    try:
        from sdk import il2cpp as il2cpp_sdk

        _check_size(path)
        try:
            table = json.loads(table_json)
        except ValueError:
            raise ValueError("bảng dịch không phải JSON hợp lệ "
                             "(cần dict {LIT_NNNNNN: text}).")
        if not isinstance(table, dict) or not table:
            raise ValueError("bảng dịch phải là object key-value không "
                             "rỗng (LIT_NNNNNN -> text đã dịch).")
        table = {str(k): (v if isinstance(v, str)
                          else json.dumps(v, ensure_ascii=False))
                 for k, v in table.items()}
        manifest = None
        if manifest_json:
            try:
                m = json.loads(manifest_json)
            except ValueError:
                raise ValueError("manifest không phải JSON hợp lệ.")
            if isinstance(m, dict) and isinstance(m.get("keys"), dict):
                manifest = m["keys"]
            elif isinstance(m, dict):
                manifest = m
            else:
                raise ValueError("manifest phải là object JSON.")
        data, source_label = il2cpp_sdk.read_metadata_bytes(
            path, entry=(str(entry) if entry else None))
        if isinstance(relayout, str):
            relayout = relayout.strip().lower() in (
                "1", "true", "yes", "on")
        relayout = bool(relayout)
        # sha FILE GỐC (cả IPA — để sidecar/repack đối chiếu) khác sha
        # MEMBER metadata (chỉ để ghi nhận): đừng lẫn 2 cái.
        file_sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        meta_sha = hashlib.sha256(data).hexdigest()
        patched, report = il2cpp_sdk.apply_literal_patches(
            data, table, manifest, relayout=relayout)
        out_dir = "/tmp/pb3out"
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, il2cpp_sdk.PATCHED_FILE)
        with open(dest, "wb") as fh:
            fh.write(patched)
        patched_sha = hashlib.sha256(patched).hexdigest()
        sidecar = {
            "format": "beartea-il2cpp-patched/1",
            "entry": (str(entry) if entry else None),
            "source": source_label,
            "source_file": os.path.basename(path),
            "source_sha256": file_sha,
            "metadata_sha256": meta_sha,
            "patched_sha256": patched_sha,
            "metadata_version": report["metadata_version"],
            "patched_count": report["patched_count"],
            "skipped_count": report["skipped_count"],
            "mode": report.get("mode", "inplace"),
            "size_delta": report.get("size_delta", 0),
        }
        with open(os.path.join(out_dir, il2cpp_sdk.PATCHED_SIDECAR),
                  "w", encoding="utf-8") as fh:
            fh.write(json.dumps(sidecar, ensure_ascii=False, indent=1))
        # File gốc không đổi (như repack_ipa): xác minh sha sau khi chạy.
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != file_sha:
            raise ValueError("file gốc đã đổi trong lúc vá — từ chối "
                             "kết quả.")
        report.update({
            "ok": True, "source": source_label,
            "source_sha256": file_sha, "metadata_sha256": meta_sha,
            "patched_sha256": patched_sha,
            "dest": dest, "filename": il2cpp_sdk.PATCHED_FILE,
            "patched_bytes": len(patched),
            "note": (("Đã vá %d/%d key bằng re-layout (file %d -> %d "
                      "byte, %+d byte; %d section sau blob dời nguyên "
                      "vẹn). Game có chạy được với metadata re-layout "
                      "không thì CHƯA kiểm chứng trên máy thật — chỉ "
                      "verify được cấu trúc file. "
                      % (report["patched_count"], len(table), len(data),
                         len(patched), report.get("size_delta", 0),
                         report.get("sections_shifted", 0)))
                     if relayout else
                     ("Đã vá %d/%d key tại chỗ (verify đọc lại đạt). "
                      % (report["patched_count"], len(table))))
                    + ("%d key chưa vá được — xem key_results từng key "
                       "kèm lý do (không giả vờ ghi được)."
                       % report["skipped_count"]),
        })
        return json.dumps(report, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def il2cpp_repack(path, entry=None, out_dir=None):
    """Đóng gói lại IPA với global-metadata.dat đã vá (bước sau patch).

    Đọc file metadata đã vá + sidecar trong /tmp/pb3out (do op
    patch_il2cpp_literals ghi), kiểm tra entry + sha file gốc + sha file
    đã vá khớp sidecar, rồi dùng lại sdk.repack.repack_ipa (M2 — kiểm
    tra 6 mục sau đóng gói, FAIL thì xóa file lỗi). File gốc KHÔNG bị
    ghi đè; output "<game>-mod.ipa", chữ ký cũ giữ nguyên nên cần ký lại
    như M2. out_dir chỉ để test truyền thư mục đĩa lớn (mặc định
    /tmp/repack_out như M2 — UI không truyền).
    """
    try:
        from sdk import il2cpp as il2cpp_sdk
        from sdk import repack as _repack

        _check_size(path)
        pb3out_dir = "/tmp/pb3out"
        sidecar_path = os.path.join(pb3out_dir, il2cpp_sdk.PATCHED_SIDECAR)
        patched_path = os.path.join(pb3out_dir, il2cpp_sdk.PATCHED_FILE)
        if not os.path.isfile(sidecar_path) \
                or not os.path.isfile(patched_path):
            raise ValueError(
                "chưa có metadata đã vá — bấm 'Ép bản dịch literals' "
                "trước rồi mới đóng gói lại.")
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            sidecar = json.load(fh)
        entry = (str(entry) if entry else None) or sidecar.get("entry")
        if not entry:
            raise ValueError("sidecar metadata đã vá không ghi entry — "
                             "ép lại trước khi đóng gói.")
        if sidecar.get("entry") != entry:
            raise ValueError(
                "entry metadata đã vá (%r) khác entry đang chọn (%r) — "
                "ép lại đúng entry trước khi đóng gói."
                % (sidecar.get("entry"), entry))
        if sidecar.get("source_file") != os.path.basename(path):
            raise ValueError(
                "file IPA đã đổi sau khi ép metadata — ép lại trước khi "
                "đóng gói.")
        with open(patched_path, "rb") as fh:
            patched = fh.read()
        if hashlib.sha256(patched).hexdigest() \
                != sidecar.get("patched_sha256"):
            raise ValueError("file metadata đã vá đã đổi sau khi ép — "
                             "ép lại trước khi đóng gói.")
        data = open(path, "rb").read()
        orig_sha = hashlib.sha256(data).hexdigest()
        if orig_sha != sidecar.get("source_sha256"):
            raise ValueError("file IPA gốc đã đổi sau khi ép metadata — "
                             "ép lại trước khi đóng gói.")
        res = _repack.repack_ipa(path, {entry: patched},
                                 out_dir=(out_dir or _repack.REPACK_OUT_DIR))
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != orig_sha:
            raise ValueError("file gốc đã đổi trong lúc đóng gói — từ "
                             "chối kết quả.")
        res["source_sha256"] = orig_sha
        res["patched_entry"] = entry
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def mono_dll_table(path, entry=None):
    """Xuất bảng dịch chuỗi literal Mono (F4, đường Mono DLL -> tab Dịch).

    Đọc chuỗi ldstr CHÍNH XÁC từ Assembly-CSharp.dll (entry trong
    IPA/ZIP hoặc file thường), xuất thành dict key->value thuần
    {MONO_NNNNNN: text} + manifest {key: {us_offset, source, sites}}
    rồi ghi 2 file vào /tmp/pb3out để tab Dịch nạp qua read_extracted
    như mọi file trích xuất (mono-strings.json +
    mono-strings.manifest.json). Bảng là dict thuần nên translate_json
    ăn được. Chuỗi rỗng (blob dự trữ offset 0) không đưa vào bảng.
    DLL ký strong-name vẫn TRÍCH được (chỉ CẤM vá — xem patch_mono_dll).
    """
    try:
        from sdk import mono_dll as mono_sdk

        _check_size(path)
        data, source_label = mono_sdk.read_dll_bytes(
            path, entry=(str(entry) if entry else None))
        result = mono_sdk.extract_mono_strings(data)
        table, manifest = mono_sdk.strings_to_table(result)
        out_dir = "/tmp/pb3out"
        os.makedirs(out_dir, exist_ok=True)
        table_text = json.dumps(table, ensure_ascii=False, indent=1,
                                sort_keys=True)
        manifest_text = json.dumps(
            {"format": "beartea-mono-strings-manifest/1",
             "source": source_label,
             "metadata_version": result["info"]["metadata_version"],
             "ldstr_sites": result["info"]["ldstr_sites"],
             "unique_strings": result["info"]["unique_strings"],
             "strong_name_signed": result["info"]["strong_name_signed"],
             "keys": manifest},
            ensure_ascii=False, indent=1, sort_keys=True)
        with open(os.path.join(out_dir, mono_sdk.TABLE_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write(table_text)
        with open(os.path.join(out_dir, mono_sdk.MANIFEST_FILE),
                  "w", encoding="utf-8") as fh:
            fh.write(manifest_text)
        return json.dumps({
            "ok": True, "source": source_label,
            "metadata_version": result["info"]["metadata_version"],
            "ldstr_sites": result["info"]["ldstr_sites"],
            "unique_strings": result["info"]["unique_strings"],
            "strong_name_signed": result["info"]["strong_name_signed"],
            "table_keys": len(table),
            "manifest_keys": len(manifest),
            "table_file": mono_sdk.TABLE_FILE,
            "manifest_file": mono_sdk.MANIFEST_FILE,
            "table_bytes": len(table_text.encode("utf-8")),
            "note": ("Bảng dịch Mono: %d chuỗi duy nhất (%d site ldstr). "
                     "DLL ký strong-name thì CHỈ trích được, KHÔNG vá "
                     "được (vá sẽ phá chữ ký)." % (len(table),
                     result["info"]["ldstr_sites"])),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def patch_mono_dll(path, entry, table_json, manifest_json=None):
    """Ép bản dịch chuỗi Mono VÀO Assembly-CSharp.dll (F4).

    table_json: dict {MONO_NNNNNN: text_đã_dịch} (lấy từ output tab
    Dịch sau khi dịch mono-strings.json). manifest_json: manifest kèm
    theo bảng (để verify text gốc từng key trước khi vá — sai file DLL
    thì bỏ qua key đó, không vá mù).
    Vá trong heap #US, fail-closed từng chuỗi: bản dịch ngắn hơn/bằng
    thì vá tại chỗ; dài hơn thì dời blob tới slot trống trong heap
    (giải phóng từ chuỗi đã rút ngắn); dài hơn mà không còn slot thì
    bỏ qua kèm mã E_TOO_LONG. DLL ký strong-name thì TỪ CHỐI toàn bộ.
    Kích thước file không đổi; verify bằng cách đọc lại toàn bộ
    literal. File gốc KHÔNG bị ghi đè; DLL đã vá ghi ra
    /tmp/pb3out/Assembly-CSharp.patched.dll + sidecar JSON.
    """
    try:
        from sdk import mono_dll as mono_sdk

        _check_size(path)
        try:
            table = json.loads(table_json)
        except ValueError:
            raise ValueError("bảng dịch không phải JSON hợp lệ "
                             "(cần dict {MONO_NNNNNN: text}).")
        if not isinstance(table, dict) or not table:
            raise ValueError("bảng dịch phải là object key-value không "
                             "rỗng (MONO_NNNNNN -> text đã dịch).")
        table = {str(k): (v if isinstance(v, str)
                          else json.dumps(v, ensure_ascii=False))
                 for k, v in table.items()}
        manifest = None
        if manifest_json:
            try:
                m = json.loads(manifest_json)
            except ValueError:
                raise ValueError("manifest không phải JSON hợp lệ.")
            if isinstance(m, dict) and isinstance(m.get("keys"), dict):
                manifest = m["keys"]
            elif isinstance(m, dict):
                manifest = m
            else:
                raise ValueError("manifest phải là object JSON.")
        data, source_label = mono_sdk.read_dll_bytes(
            path, entry=(str(entry) if entry else None))
        file_sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        dll_sha = hashlib.sha256(data).hexdigest()
        patches = []
        by_off = None
        for key, after in table.items():
            if manifest is not None and key in manifest:
                before = manifest[key].get("source")
                us_off = manifest[key].get("us_offset")
            else:
                try:
                    us_off = int(str(key).split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                # Không có manifest: lấy before từ DLL hiện tại (1 lần).
                if by_off is None:
                    cur = mono_sdk.extract_mono_strings(data)
                    by_off = {s["us_offset"]: s["text"]
                              for s in cur["strings"]}
                before = by_off.get(us_off)
                if before is None:
                    continue
            patches.append({"us_offset": us_off, "before": before,
                            "after": after})
        patched, report = mono_sdk.patch_mono_strings(data, patches)
        out_dir = "/tmp/pb3out"
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, mono_sdk.PATCHED_FILE)
        with open(dest, "wb") as fh:
            fh.write(patched)
        patched_sha = hashlib.sha256(patched).hexdigest()
        sidecar = {
            "format": "beartea-mono-patched/1",
            "entry": (str(entry) if entry else None),
            "source": source_label,
            "source_file": os.path.basename(path),
            "source_sha256": file_sha,
            "dll_sha256": dll_sha,
            "patched_sha256": patched_sha,
            "patched_count": report["patched_count"],
            "relocated_count": report["relocated_count"],
            "skipped_count": report["skipped_count"],
        }
        with open(os.path.join(out_dir, mono_sdk.PATCHED_SIDECAR),
                  "w", encoding="utf-8") as fh:
            fh.write(json.dumps(sidecar, ensure_ascii=False, indent=1))
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != file_sha:
            raise ValueError("file gốc đã đổi trong lúc vá — từ chối "
                             "kết quả.")
        report.update({
            "ok": True, "source": source_label,
            "source_sha256": file_sha, "dll_sha256": dll_sha,
            "patched_sha256": patched_sha,
            "dest": dest, "filename": mono_sdk.PATCHED_FILE,
            "patched_bytes": len(patched),
            "note": ("Đã vá %d/%d chuỗi (trong đó dời heap %d, verify "
                     "đọc lại đạt). %d chuỗi chưa vá được — xem skipped: "
                     "bản dịch dài hơn gốc mà heap #US không còn slot "
                     "trống thì không vá được tại chỗ."
                     % (report["patched_count"], len(table),
                        report["relocated_count"],
                        report["skipped_count"])),
        })
        return json.dumps(report, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _sf_summary(name, sf):
    tas = []
    for pid, nm, script in sf.text_assets():
        tas.append({"path_id": int(pid), "name": nm,
                    "head": script[:MAX_TEXT_HEAD]})
        if len(tas) >= MAX_LIST_TEXT_ASSETS:
            break
    return {"entry": name, "version": int(sf.version),
            "unity_version": sf.unity_version,
            "objects": len(getattr(sf, "objects", []) or []),
            "text_assets": tas}


def _parse_unity_bytes(data, name):
    """Parse 1 đơn vị Unity (bundle .unity3d hoặc file .assets độc lập).

    Trả dict (chưa có "ok"): bundle=True + entries/files nếu là bundle,
    bundle=False + 1 file nếu là .assets độc lập. Raise khi cả 2 đều
    không parse được (vd file ZIP đưa thẳng vào — phải đi qua
    unity_inspect/unity_inspect_entry để chọn entry bên trong trước).
    """
    from sdk import unity as u

    backend = u.UnityBackend("NATIVE")
    out = {"bundle": False, "files": []}
    try:
        bf = u.parse_bundle(data, backend, _app.limits, where=name)
    except Exception:
        # File đúng là UnityFS nhưng parse lỗi (mã hoá / version /
        # nén chưa hỗ trợ / hỏng): giữ nguyên lỗi thật, đừng rơi xuống
        # parse_serialized rồi báo lỗi sai — lỗi C5: bundle mã hoá bị
        # báo E_UNSUPPORTED_FORMAT chung chung thay vì E_ENCRYPTED.
        # Chỉ rơi xuống parse_serialized khi KHÔNG phải UnityFS (vd
        # file .assets độc lập, cũng bắt đầu bằng magic khác).
        if bytes(data[:7]) == u.UNITYFS_MAGIC:
            raise
        bf = None
    if bf is not None:
        out["bundle"] = True
        out["signature"] = bf.signature
        out["version"] = int(bf.version)
        out["unity_version"] = bf.unity_version
        out["compression"] = bf.compression_name
        out["entries"] = [{"name": e.name, "size": int(e.size),
                           "serialized": bool(e.is_serialized)}
                          for e in bf.entries]
        for e in bf.entries:
            if not e.is_serialized:
                continue
            try:
                sf = u.parse_serialized(bf.get_entry(e.name),
                                        _app.limits, where=e.name)
                out["files"].append(_sf_summary(e.name, sf))
            except Exception as ex:  # noqa: BLE001 - ghi lỗi từng entry
                out["files"].append({"entry": e.name, "error": str(ex)})
    else:
        sf = u.parse_serialized(data, _app.limits, where=name)
        out["files"].append(_sf_summary(name, sf))
    return out


def unity_inspect(path):
    """Inspect Unity: bundle (.unity3d) hoặc file .assets độc lập.

    Dùng parser thuần của PB3 (sdk/unity.py) + backend NATIVE (cầu nối JS
    lz4js nếu worker đã nạp, nếu không thì lz4pure). Không dùng
    UnityPy (không cài được trên Pyodide).

    File ZIP/IPA thì KHÔNG parse thẳng cả file (trước đây rơi xuống
    parse_serialized rồi lỗi E_UNSUPPORTED_FORMAT) mà trả danh sách các
    entry .unity3d/.assets bên trong để UI cho user chọn 1 cái inspect
    trực tiếp qua unity_inspect_entry. Không có bundle nào thì bundles=[]
    (UI hiện hướng dẫn trích xuất trước).
    """
    try:
        from core.limits import Limits
        from sdk import containers, discovery as disc

        _check_size(path)
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            head = fh.read(2048)
        if containers.detect_container_kind(head) == "zip":
            vfs = containers.open_vfs(path, source_name=name)
            inv = disc.inventory(vfs, Limits())
            bundles = [{"path": e.path, "size": int(e.size)}
                       for e in inv
                       if not e.path.endswith("/")
                       and e.path.lower().endswith((".unity3d", ".assets"))]
            bundles.sort(key=lambda b: b["path"])
            return json.dumps({
                "ok": True, "is_container": True, "kind": "zip",
                "bundles": bundles[:200], "bundles_total": len(bundles),
                "total_entries": len(inv),
            }, ensure_ascii=False)
        data = open(path, "rb").read()
        out = {"ok": True, "is_container": False}
        out.update(_parse_unity_bytes(data, name))
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def unity_inspect_entry(path, entry):
    """Inspect 1 bundle/.assets BÊN TRONG file ZIP/IPA: đọc entry đó ra
    rồi parse (không parse cả file ZIP; cũng không nạp cả file IPA vào
    RAM như trước — VFS lười chỉ đọc đúng entry cần). entry là đường dẫn
    trong container (vd Payload/9Kings.app/Data/sharedassets0.assets)."""
    try:
        from core.limits import Limits
        from sdk import containers, discovery as disc

        _check_size(path)
        vfs = disc.open_source_vfs(path, Limits())
        nested = containers.NestedVFS(vfs, limits=Limits())
        raw = bytes(nested.read(entry, None))
        out = {"ok": True, "is_container": False, "entry": entry}
        out.update(_parse_unity_bytes(raw, os.path.basename(entry)))
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def extract(path, source_paths=None, locales=None, merge_one_file=None):
    """Trích xuất text đầy đủ (pipeline 7 bước của PB3).

    Kết quả ghi ra /tmp/pb3out rồi nén thành /tmp/extract.zip để JS tải về.
    progress_fn báo tiến độ thật từng bước (inventory/layers/candidates/
    sniff/probe/export) về JS qua _emit.

    source_paths: danh sách vpath chỉ trích xuất các entry đó (None/rỗng
    = tự động, tool tự tìm file). locales: danh sách locale lọc mềm
    (vd ["en", "zh"]; None = không lọc). Mỗi file trả kèm "source"
    (nguồn gốc: `en.json ← ...localization-locales_assets_all.bundle`),
    "skipped" (mọi file bỏ qua kèm lý do — không bao giờ im lặng) và
    "log" (log pipeline, vd "Unity: ... -> N TextAsset").
    merge_one_file: True = gộp thành 1 file mỗi ngôn ngữ đã chọn
    (en.json/zh-CN.json/zh-TW.json; Joseph 23:35). None/False = giữ file tách per-unit
    như cũ (mặc định False để API/harness cũ không đổi; bản web bật bằng
    checkbox ở tab Trích xuất, mặc định tick).
    """
    try:
        from sdk import extraction as ext

        _check_size(path)
        out_dir = "/tmp/pb3out"
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)

        def _pf(step, done, total):
            _emit("extract:" + str(step), done, total)

        # JS (Pyodide) đưa list sang dạng JsProxy, và null sang JsNull
        # (KHÔNG phải None — `is not None` sai). Dùng truthiness như
        # batch_run vẫn làm: JsNull falsy; list rỗng -> không lọc.
        paths = None
        if source_paths:
            paths = [str(p) for p in list(source_paths)]
            if not paths:
                paths = None
        locs = None
        if locales:
            locs = {str(c).lower() for c in list(locales) if str(c).strip()}
            if not locs:
                locs = None
        req = ext.ExtractionRequest(
            source=path, registry=_reg, tempdir=str(_app.paths.temp),
            root=WEB_ROOT, out_dir=out_dir, progress_fn=_pf,
            source_paths=paths, locales=locs,
            merge_one_file=bool(merge_one_file))
        r = ext.run_extraction(req)
        # BUG#2: chế độ gộp chỉ liệt kê file locale đã xuất (đã ghi ra
        # đĩa); outcome passthrough (rác/config) không nằm trong output
        # nên không liệt kê — khớp đúng với nội dung extract.zip.
        _files = [f for f in r.files
                  if not req.merge_one_file or f.exported]
        files = [{"path": f.path, "plugin": f.plugin,
                  "entries": int(f.entries), "locale": f.locale,
                  "exported": f.exported,
                  "source": f.source or f.path} for f in _files]
        zpath = "/tmp/extract.zip"
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, fns in os.walk(out_dir):
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))
        # Lỗi 2026-11-16 ("Trích xuất text lỗi: Lỗi không rõ"): bridge
        # TRƯỚC ĐÂY trả ok:false mà không có trường error/code khi
        # run_extraction return theo đường thành công (0 file, kẹt ASK...),
        # nên runOp bên app.js chỉ còn "Lỗi không rõ" để hiện. Nay tổng
        # hợp error từ r.errors; khi errors rỗng thì dựng từ decision +
        # số skipped (trong đó bao nhiêu do bộ lọc ngôn ngữ) để banner
        # luôn đọc được lý do thật.
        err_text = ""
        if not r.ok:
            errs = list(r.errors or [])
            if errs:
                first = errs[0] or {}
                err_text = ((("[" + str(first.get("code")) + "] ")
                             if first.get("code") else "")
                            + str(first.get("message") or first.get("error") or ""))
                if len(errs) > 1:
                    err_text += " (+%d lỗi khác)" % (len(errs) - 1)
            else:
                n_sk = len(r.skipped or [])
                n_loc = sum(1 for s in (r.skipped or [])
                            if "không phải" in str((s or {}).get("reason") or ""))
                err_text = ("Không trích xuất được file nào (quyết định: %s; "
                            "đã bỏ qua %d mục, trong đó %d mục do bộ lọc ngôn ngữ; "
                            "xem danh sách đã bỏ qua / log trong kết quả trích xuất)"
                            % (r.decision, n_sk, n_loc))
                if n_loc:
                    err_text += " — thử tắt bộ lọc ngôn ngữ rồi trích xuất lại."
        return json.dumps({
            "ok": bool(r.ok), "decision": str(r.decision),
            "total_entries": int(r.total_entries), "files": files,
            "merged_files": list(r.merged_files or []),
            "language_columns": list(r.language_columns or []),
            "shared_index": dict(r.shared_index or {}),
            "skipped": list(r.skipped or []),
            "log": list((r.report or {}).get("log") or []),
            "errors": list(r.errors or []), "zip_path": zpath,
            "error": err_text,
            "zip_bytes": os.path.getsize(zpath),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


MAX_READ_EXTRACTED = 4 * 1024 * 1024   # read_extracted: tối đa 4MB nạp vào ô dịch
MAX_READ_EXTRACTED_CHUNKED = 64 * 1024 * 1024  # chế độ chunk: tối đa 64MB


def _coerce_chunk_int(v):
    """Số nguyên chunk từ JS (Pyodide có thể đưa float nguyên) -> int/None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def read_extracted(name, offset=None, length=None):
    """Đọc 1 file JSON đã trích xuất trong /tmp/pb3out (để nạp vào tab Dịch).

    Chỉ nhận tên file trần (không đường dẫn — chống directory traversal
    trong FS worker); file phải là JSON hợp lệ và <= 4MB (ô dịch + bộ nhớ
    tab iPhone). Lớn hơn thì báo rõ, user tải extract.zip về rồi dán
    từng phần. Không cần gửi lại file game (worker đã có /tmp/pb3out từ
    đợt extract vừa rồi).

    Chế độ chunk (caps-po): truyền offset/length (số KÝ TỰ, tính theo
    code point Python) để nạp file dịch lớn (vd vi.json của DST ~9 MB,
    ~87,8k key) theo từng đoạn thay vì chặn cứng 4 MB. File <= 64 MB;
    chỉ chunk đầu (offset==0) kiểm JSON hợp lệ (chunk sau là đoạn cắt,
    không parse được); JS ghép toàn văn rồi JSON.parse 1 lần. Trả thêm
    end (offset tiếp theo — JS dùng `end` server trả về, không tự cộng,
    tránh lệch với ký tự ngoài BMP mà JS đếm 2 đơn vị UTF-16),
    total_chars, done, chunked=True. Không truyền offset/length thì
    hành vi cũ (<= 4 MB, lỗi rõ nếu lớn hơn).
    """
    try:
        if not isinstance(name, str) or not name or "/" in name or "\\" in name \
                or name in (".", ".."):
            raise ValueError("Tên file không hợp lệ: %r." % (name,))
        fp = os.path.join("/tmp/pb3out", name)
        if not os.path.isfile(fp):
            raise ValueError(
                "Không tìm thấy %s trong kết quả trích xuất vừa rồi. "
                "Hãy chạy Trích xuất trước." % name)
        n = os.path.getsize(fp)
        if offset is not None or length is not None:
            offset = _coerce_chunk_int(offset)
            length = _coerce_chunk_int(length)
            if offset is None or offset < 0:
                raise ValueError("offset phải là số nguyên >= 0.")
            if length is None or length <= 0:
                raise ValueError("length phải là số nguyên > 0.")
            if n > MAX_READ_EXTRACTED_CHUNKED:
                raise ValueError(
                    "File %s quá lớn (%s) để nạp theo chunk (giới hạn %s). "
                    "Hãy tải extract.zip về rồi dán từng phần vào ô dịch."
                    % (name, _fmt_bytes(n), _fmt_bytes(MAX_READ_EXTRACTED_CHUNKED)))
            with open(fp, "r", encoding="utf-8") as fh:
                text = fh.read()
            if offset == 0:
                json.loads(text)   # chỉ chunk đầu kiểm JSON hợp lệ
            total = len(text)
            part = text[offset:offset + length]
            end = offset + len(part)
            return json.dumps({"ok": True, "name": name, "text": part,
                               "bytes": int(n), "chars": len(part),
                               "offset": int(offset), "end": int(end),
                               "total_chars": int(total),
                               "done": end >= total, "chunked": True},
                              ensure_ascii=False)
        if n > MAX_READ_EXTRACTED:
            raise ValueError(
                "File %s quá lớn (%s) để nạp vào ô dịch (giới hạn %s). "
                "Hãy tải extract.zip về rồi dán từng phần vào ô dịch."
                % (name, _fmt_bytes(n), _fmt_bytes(MAX_READ_EXTRACTED)))
        with open(fp, "r", encoding="utf-8") as fh:
            text = fh.read()
        json.loads(text)   # phải là JSON hợp lệ mới nạp vào ô dịch
        return json.dumps({"ok": True, "name": name, "text": text,
                           "bytes": int(n)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _fmt_bytes(n):
    """Số byte -> chuỗi gọn (dùng cho thông báo read_extracted)."""
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return "%d %s" % (n, unit) if unit == "B" else "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%d B" % n


# Dấu Latin không có trong từ tiếng Anh bản xứ (ä/ö/ü/é/è/à/ç/á...):
# chuỗi Latin trơn mà chứa dấu này thì CHẮC CHẮN không phải tiếng Anh
# (vd "Unerhört!", "Extraordinário!") — dùng làm lưới an toàn ở mức
# lọc dịch: detect_language trả weak-en cho các chuỗi ngắn không có từ
# chức năng, nhưng dấu này đủ để loại chúng khỏi hàng đợi tiếng Anh.
# Từ mượn có dấu trong tiếng Anh ("café", "Pokémon") cũng bị loại theo
# — chấp nhận được: đó thường là tên riêng, giữ nguyên là đúng.
_NON_EN_DIACRITICS = set(
    "àáâãäåçèéêëìíîïñòóôõöøùúûüýÿß")


def _source_match(text, src):
    """Chuỗi này có thuộc ngôn ngữ nguồn `src` không?

    Dùng sdk.langdetect.detect_language (rule-based, không mạng):
    - phát hiện mạnh == src -> (True, "", code)
    - phát hiện mạnh != src -> (False, "other_lang:<code>", code)
    - không có chữ cái -> (False, "no_letters", None)
    - phát hiện yếu: chỉ src == "en" mới nhận (Latin trơn không dấu/từ
      chức năng mặc định là tiếng Anh — xem langdetect); mọi src khác
      (vd "zh") thì Latin yếu KHÔNG tính là nguồn ->
      (False, "weak_latin_not_<src>", "en").
    Ngoại lệ lưới an toàn: chuỗi yếu mà chứa dấu Latin không phải tiếng
    Anh (vd "Unerhört!") thì KHÔNG tính là tiếng Anh dù src == "en" ->
    (False, "non_en_diacritics", None).
    Trả (is_source, reason, detected_code).
    Không nạp được langdetect thì RAISE (fail-closed): im lặng nhận hết
    sẽ dịch nhầm ngôn ngữ khác như trước — thà báo lỗi còn hơn.
    """
    from sdk.langdetect import detect_language
    code, conf = detect_language(text or "")
    if code is None:
        return False, "no_letters", None
    if conf == "strong":
        # Họ ms/id: tiếng Mã Lai và Indonesia gần như trùng từ vựng,
        # phát hiện không tách reliably được (chuỗi chỉ có từ chung thì
        # detect trả "id") — chấp nhận lẫn nhau khi lọc nguồn (T2).
        if code == src or (src in ("ms", "id") and code in ("ms", "id")):
            return True, "", code
        return False, "other_lang:%s" % code, code
    # weak: chỉ Latin trơn, mặc định là tiếng Anh.
    if src == "en":
        if any(ch in _NON_EN_DIACRITICS for ch in (text or "")):
            return False, "non_en_diacritics", None
        return True, "", "en"
    return False, "weak_latin_not_%s" % src, code


def _self_check(kv, translated, skipped_keys, credit_key):
    """Tự kiểm tra output trước khi trả về (mục A, Joseph 21:28).

    - missing_keys: key trong input mà output thiếu — phải rỗng (bridge
      dựng lại dict theo đúng thứ tự key input nên luôn rỗng nếu không
      có lỗi lập trình).
    - wrong_lang_changed: key bị bỏ qua vì không thuộc ngôn ngữ nguồn
      (skipped_keys) mà giá trị lại đổi — phải rỗng; ngoại lệ duy nhất
      là credit_key (gắn 'Việt Hoá Bởi Bear' có chủ đích, đã báo riêng).
    Trả {"input_keys", "output_keys", "missing_keys",
    "wrong_lang_changed", "ok"}.
    """
    missing = [k for k in kv if k not in translated]
    wrong = [k for k in skipped_keys
             if k in translated and translated[k] != kv[k]
             and k != credit_key]
    return {"input_keys": len(kv), "output_keys": len(translated),
            "missing_keys": missing,
            "wrong_lang_changed": wrong,
            "ok": not missing and not wrong}


def translate_json(json_text, to="vi", credit=True, src="en",
                   glossary_json=None):
    """Dịch bảng key-value bằng từ điển offline (không cần mạng).

    Chỉ dịch VALUE, giữ nguyên KEY (luật của PB3). credit=True thì gắn
    'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/version nếu tìm được.
    src: ngôn ngữ nguồn phải dịch (mặc định "en"); chuỗi phát hiện mạnh
    thuộc ngôn ngữ khác bị BỎ QUA (giữ nguyên từng byte, ghi lý do vào
    skipped) — không dịch nhầm như trước (bridge từng hard-code "en").
    Lọc ở mức VALUE (cả giá trị): giá trị nào phát hiện mạnh không phải
    src thì giữ nguyên toàn bộ.
    glossary_json (T2): chuỗi JSON từ điển game (sdk.glossary) — áp dụng
    TRƯỚC từ điển offline, dòng khớp thì dùng luôn, không bị ghi đè.
    """
    try:
        from sdk.translate import (OfflineGlossaryProvider, apply_credit,
                                   check_lang, mask_line, translate_table)

        kv = json.loads(json_text)
        if not isinstance(kv, dict):
            raise ValueError("File JSON phải là object key-value.")
        kv = {str(k): (v if isinstance(v, str)
                       else json.dumps(v, ensure_ascii=False))
              for k, v in kv.items()}
        check_lang(to, allow_auto=False)
        check_lang(src, allow_auto=False)
        if src == to:
            raise ValueError(
                "ngôn ngữ nguồn và đích trùng nhau: %s" % src)
        # Từ điển game (T2): parse sớm để lỗi định dạng fail-closed
        # trước khi dịch (không lặng lẽ bỏ qua từ điển của user).
        game_glossary = None
        if glossary_json:
            from sdk.glossary import GameGlossary
            game_glossary = GameGlossary.from_json(glossary_json)
        # Lọc theo ngôn ngữ nguồn ở mức value (xem _source_match).
        kv_src = {}
        skipped = {}  # key -> {"reason", "detected"}
        skipped_by_reason = {}
        skipped_by_lang = {}
        for key, value in kv.items():
            if value == "":
                kv_src[key] = value
                continue
            # Phát hiện trên giá trị ĐÃ CHE thẻ/placeholder (như đường
            # Google): chữ trong thẻ (<size>, </b>) không phải ngôn ngữ
            # của chuỗi — giá trị rich-text CJK chỉ còn thẻ sẽ không bị
            # coi là tiếng Anh yếu.
            masked_value = "\n".join(
                mask_line(ln)[0] for ln in value.split("\n"))
            is_src, reason, det = _source_match(masked_value, src)
            if is_src:
                kv_src[key] = value
            else:
                skipped[key] = {"reason": reason, "detected": det}
                skipped_by_reason[reason] = \
                    skipped_by_reason.get(reason, 0) + 1
                if det:
                    skipped_by_lang[det] = skipped_by_lang.get(det, 0) + 1
        res = translate_table(
            kv_src, source_lang=src, target_lang=to,
            provider=OfflineGlossaryProvider(),
            progress_fn=lambda d, t: _emit("translate", d, t),
            credit=False, game_glossary=game_glossary)
        # Trải lại đủ mọi key theo đúng thứ tự input: key bị bỏ qua giữ
        # nguyên giá trị gốc (dedupe/lọc không bao giờ làm mất key).
        translated = {key: (res.translated[key] if key in res.translated
                            else kv[key]) for key in kv}
        credit_key = None
        if credit:
            credit_key = apply_credit(translated)
        self_check = _self_check(kv, translated, set(skipped), credit_key)
        return json.dumps({
            "ok": True, "translated": translated, "total": int(len(kv)),
            "source_total": int(len(kv_src)),
            "untranslated": int(res.untranslated),
            "untranslated_keys": list(res.untranslated_keys or [])[:50],
            "warnings": list(res.warnings or [])[:20],
            "credit_key": credit_key,
            "translated_by": "offline", "google_count": 0,
            "glossary_count": int(res.glossary),
            "network_error": None, "cancelled": False,
            "source_lang": src,
            "skipped_count": len(skipped),
            "skipped_by_reason": skipped_by_reason,
            "skipped_by_lang": skipped_by_lang,
            "skipped_keys": sorted(skipped)[:50],
            "self_check": self_check,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _gt_error_text(exc):
    """Tin lỗi ngắn gọn từ lỗi JS (JsException bọc Error của gt.js)."""
    kind = None
    try:
        kind = getattr(exc, "kind", None)
    except Exception:
        kind = None
    msg = str(exc).split("\n")[0][:200]
    if kind == "all_failed":
        # Lỗi cuối chuỗi fallback (mục C1): message đã là tóm tắt đầy đủ
        # cả chuỗi ("Google lỗi 429 → MyMemory lỗi … — tất cả đều lỗi…"),
        # trả nguyên văn, không gắn thêm tiền tố "Google Translate lỗi".
        return msg
    if kind:
        return "Google Translate lỗi (%s): %s" % (kind, msg)
    return "Google Translate lỗi: %s" % msg


# --- Batch dịch (mục A, đợt bổ sung 13:52) ---
# Gộp tối đa 25 chuỗi + 5000 ký tự/batch (khớp giới hạn proxy Cloudflare
# + giữ q joined-text vừa phải cho gtx). Batch lỗi liên tiếp 2 lần thì
# tắt batch, về dịch từng chuỗi cho phần còn lại.
# 25 (không phải 100) vì Workers AI: đo live 2026-11-18 [ĐÃ ĐO THẬT] batch
# 10 chuỗi chưa cache mất 6,3–12,5s (worker tách 1 chuỗi/lần gọi AI, song
# song 5) — batch 100 chuỗi ≈ 60s+, vượt timeout 20s của proxyTranslateBatch
# bên gt.js nên rớt về chia đôi liên tục. Batch 25 + chạy song song 3 batch
# (xem _BATCH_CONCURRENCY) vừa dưới timeout, vừa nhanh hơn hẳn.
# Split-on-failure (tăng tốc dịch): batch lỗi do mất marker thì chia đôi
# đệ quy thay vì rớt về dịch đơn cả batch — đo thật: 1 batch 100 chuỗi rớt
# marker = 52s thay vì 2,3s. Batch <= 10 chuỗi thì không chia nữa, về đơn.
BATCH_MAX_STRINGS = 25
BATCH_MAX_CHARS = 5000
_BATCH_FAIL_LATCH = 2
_BATCH_SPLIT_MIN = 10
# Số batch dịch chạy song song (asyncio.gather, xem vòng wave trong
# translate_json_google): đo live 2026-11-18 [ĐÃ ĐO THẬT] 3 request x 10
# chuỗi song song xong 30 chuỗi trong 14,8s, còn 3 request tuần tự hết
# 25,6s (~1,7x); worker chịu được 15 lần gọi AI đồng thời mà không lỗi.
# 3 là mức an toàn (rate limit proxy 60 req/phút/IP; tổng số request
# không đổi, chỉ dồn lại). Hủy/quota/latch giữ nguyên ngữ nghĩa như chạy
# tuần tự (xem _run_one_batch).
_BATCH_CONCURRENCY = 3
# Số chuỗi tối đa gom cho 1 lần dịch lại ở bước sửa placeholder (PR #9):
# sửa đơn từng dòng tốn ~0,5s/dòng (đo thật: 28 dòng lệch = ~14s); gom
# batch <=25 qua _gt_batch thì ~1 request cho 25 dòng.
_REPAIR_BATCH_STRINGS = 25

_HIGH_PRIO_TOKENS = ("menu", "ui", "button", "btn", "title", "item",
                     "name", "label", "tab", "setting", "option",
                     "header", "footer", "hud")
_LOW_PRIO_TOKENS = ("log", "desc", "description", "dialogue", "dialog",
                    "story", "lore", "flavor", "note")
_LOW_PRIO_MAX_CHARS = 300


def _norm_segment_text(text):
    """Chuẩn hoá để khử trùng lặp: gộp khoảng trắng, bỏ trắng đầu/cuối."""
    return " ".join(str(text).split())


def _priority_of(key, text):
    """Thứ tự dịch: 0 = menu/UI/tên item trước; 1 = thường; 2 = log/mô tả
    dài sau. Khớp theo tên key (chuỗi con, chữ thường) + độ dài text."""
    low_key = str(key).lower()
    if len(text) > _LOW_PRIO_MAX_CHARS:
        return 2
    if any(tok in low_key for tok in _LOW_PRIO_TOKENS):
        return 2
    if any(tok in low_key for tok in _HIGH_PRIO_TOKENS):
        return 0
    return 1


def _batch_split_retryable(exc):
    """Batch lỗi này có nên chia đôi thử lại không (mất marker)?

    Chỉ chia khi lỗi là bad_response/mất marker — gt.js gắn cờ
    splitRetryable=true vào lỗi batch_failed khi bước google lỗi kind
    bad_response. 429/quota/cancelled thì chia cũng lỗi như cũ nên không
    chia. Thuộc tính JS có thể không qua được ranh giới Pyodide nên
    fallback: nhận diện qua chuỗi "thiếu marker" trong message (gt.js
    ghi đúng chuỗi này khi tách marker lỗi).
    """
    try:
        flag = getattr(exc, "splitRetryable", None)
    except Exception:
        flag = None
    if isinstance(flag, bool):
        return flag
    try:
        return "thiếu marker" in str(exc)
    except Exception:
        return False


def _gt_kind(exc):
    """Kind lỗi JS (vd 'network'/'cancelled'/'quota') từ lỗi JS bọc trong
    Python. Thử đọc thuộc tính kind (như _gt_error_text), nếu không được
    thì bóc từ chuỗi lỗi dạng '(kind): ...'; không bóc được thì ''."""
    try:
        kind = getattr(exc, "kind", None)
    except Exception:
        kind = None
    if isinstance(kind, str) and kind:
        return kind
    try:
        msg = str(exc)
    except Exception:
        return ""
    if "(" in msg and ")" in msg:
        inner = msg.split("(", 1)[1].split(")", 1)[0].strip()
        if inner and " " not in inner and len(inner) <= 32:
            return inner
    return ""


def _coerce_batch_result(got):
    """Chuẩn hoá kết quả js.pb3_google_translate_batch về
    (translations, mode). Hợp đồng JS: chuỗi JSON
    {"translations":[...], "mode":"proxy-batch"|"gtx-joined"}; chấp nhận
    cả mảng JS trực tiếp (mode "batch") để khỏi vỡ nếu worker trả khác.
    Trả (None, "") khi không đọc được."""
    mode = "batch"
    data = got
    if isinstance(got, str):
        try:
            parsed = json.loads(got)
        except Exception:
            return None, ""
        if not isinstance(parsed, dict):
            return None, ""
        mode = str(parsed.get("mode") or "batch")
        data = parsed.get("translations")
    else:
        try:
            to_py = getattr(data, "to_py", None)
            if callable(to_py):
                data = to_py()
        except Exception:
            pass
    if data is None:
        return None, ""
    try:
        items = list(data)
    except Exception:
        return None, ""
    if not all(isinstance(x, str) for x in items):
        return None, ""
    return items, mode


async def translate_json_google(json_text, to="vi", credit=True, src="en",
                              glossary_json=None):
    """Dịch bảng key-value bằng Google Translate thật qua fetch() của JS.

    Luồng (batch, mục A): Python (async) gọi `js.pb3_google_translate_batch`
    (gt.js) cho từng BATCH tối đa 25 chuỗi/5000 ký tự, chạy song song tối đa 3 batch (xem _BATCH_CONCURRENCY) — gtx không nhận
    nhiều q/request (chỉ dịch q đầu, đã kiểm chứng live) nên batch gộp N
    chuỗi thành 1 q với marker ⟦1⟧, ⟦2⟧... rồi tách lại; proxy Cloudflare
    (nếu có URL) thì batch thật bằng q mảng của Translation v2. Trước khi
    gửi: khử trùng lặp (dòng đã che giống nhau chỉ dịch 1 lần) + tra từ
    điển offline TRƯỚC (glossary-first) + sắp theo ưu tiên (menu/UI/tên
    item trước, log/mô tả dài sau). Batch lỗi do mất marker thì chia đôi
    đệ quy thử lại qua batch (split-on-failure) thay vì rớt cả batch về
    dịch đơn; chỉ khi chia cũng thất bại hoàn toàn mới dịch đơn lại các
    chuỗi chưa xong. Batch lỗi liên tiếp 2 lần (không cứu được chuỗi nào)
    thì tắt batch cho phần còn lại. Bước sửa placeholder (PR #9) dịch lại
    theo batch <=25 dòng lệch thay vì từng dòng. Không còn sleep 0,4s cố
    định — tốc độ gọi Google do token bucket bên gt.js giới hạn (2 dồn,
    nạp 1/500ms) + backoff khi 429 + cooldown 30s khi 429 liên tiếp. Đoạn
    nào Google lỗi thì dùng từ điển offline cho đoạn đó rồi dịch tiếp,
    không crash; lỗi đầu tiên ghi vào network_error.
    Proxy báo hết hạn mức tháng (quota) thì dừng gọi Google luôn. Chỉ
    dịch VALUE, giữ KEY; placeholder ({0}, %s), thẻ, xuống dòng được
    che/khôi phục như bản iSH; credit 'Việt Hoá Bởi Bear' gắn như
    translate_json. Nút Hủy: Python kiểm tra `js.pb3_google_cancelled()`
    trước mỗi batch/chuỗi, JS abort cả fetch đang chạy — hủy thì trả kết
    quả từng phần với cancelled=True.

    Kết quả thêm: translated_by ("google"/"mixed"/"offline"), google_count,
    glossary_count, network_error, cancelled, deduped (số đoạn trùng đã
    khử), unique_segments, batches, batch_requests, single_requests,
    batch_mode ("gtx-joined"/"proxy-batch"/"single"/"none"). Hàm này là
    async — worker.js await Promise trả về trước khi gửi kết quả về main
    thread.

    src (mục A): ngôn ngữ nguồn phải dịch (mặc định "en"). Đoạn phát
    hiện mạnh thuộc ngôn ngữ khác bị bỏ qua (giữ nguyên từng byte, đếm
    vào skipped_count + skipped_by_reason/by_lang) — không gửi Google
    như tiếng Anh nữa. Kết quả kèm self_check (input_keys/output_keys/
    missing_keys/wrong_lang_changed) — bridge tự đối chiếu output với
    input trước khi trả về; missing và wrong_lang_changed phải rỗng.
    """
    try:
        from sdk.translate import (OfflineGlossaryProvider, apply_credit,
                                   check_lang, mask_line, placeholders_ok,
                                   placeholders_match_strict, unmask_line)

        kv = json.loads(json_text)
        if not isinstance(kv, dict):
            raise ValueError("File JSON phải là object key-value.")
        kv = {str(k): (v if isinstance(v, str)
                       else json.dumps(v, ensure_ascii=False))
              for k, v in kv.items()}
        check_lang(to, allow_auto=False)
        check_lang(src, allow_auto=False)
        if src == to:
            raise ValueError(
                "ngôn ngữ nguồn và đích trùng nhau: %s" % src)
        # Từ điển game (T2): parse sớm để lỗi định dạng fail-closed
        # trước khi dịch (không lặng lẽ bỏ qua từ điển của user).
        game_glossary = None
        if glossary_json:
            from sdk.glossary import GameGlossary
            game_glossary = GameGlossary.from_json(glossary_json)
        # src do người dùng chọn (mặc định "en"); trước đây hard-code
        # "en" nên chọn dịch zh vẫn gửi chuỗi đi như tiếng Anh (Joseph
        # 21:24: câu Nga/Đức bị "dịch" hỏng). Lọc từng ĐOẠN theo ngôn
        # ngữ phát hiện (xem _source_match): đoạn không thuộc nguồn thì
        # giữ nguyên, ghi lý do vào skipped — KHÔNG tính là incomplete
        # (app.js khỏi đưa lại vào hàng đợi vô hạn).

        try:
            import js as _js
            _gt = getattr(_js, "pb3_google_translate", None)
            _gt_batch = getattr(_js, "pb3_google_translate_batch", None)
            _cancel_fn = getattr(_js, "pb3_google_cancelled", None)
            _trace_fn = getattr(_js, "pb3_fallback_trace", None)
        except Exception:
            _gt = None
            _gt_batch = None
            _cancel_fn = None
            _trace_fn = None

        def _is_cancelled():
            try:
                return bool(_cancel_fn()) if _cancel_fn is not None else False
            except Exception:
                return False

        def _gt_fallback_step():
            """Bước fallback đã DÙNG cho chuỗi vừa dịch xong (mục C1).

            Đọc trace JS (worker.js công bố pb3_fallback_trace): trả tên
            bước thành công ("google"/"mymemory"/"libretranslate"/"proxy")
            CHỈ khi bước đầu đã lỗi (trace có >1 mục) — tức là thật sự
            phải dùng fallback; bước đầu thành công ngay thì trả "".
            Không có hàm trace (mock cũ trong harness) thì trả "".
            """
            try:
                if _trace_fn is None:
                    return ""
                raw = _trace_fn()
                trace = json.loads(str(raw))
                if not isinstance(trace, list) or len(trace) < 2:
                    return ""
                for entry in trace:
                    if isinstance(entry, dict) and entry.get("ok"):
                        return str(entry.get("step") or "")
                return ""
            except Exception:
                return ""

        fallback = OfflineGlossaryProvider()
        translated = {}
        warnings = []
        untranslated_keys = []
        google_count = glossary_count = 0
        untranslated = empty = unchanged = 0
        network_error = None
        cancelled = False
        # Số chuỗi mỗi bước fallback đã cứu (mục C1): {"mymemory": 12, ...}
        # — hiện ở UI qua fallback_note để user thấy bước nào đã dùng.
        fallback_used = {}
        translated = {}
        warnings = []
        untranslated_keys = []
        google_count = glossary_count = 0
        untranslated = empty = unchanged = 0
        network_error = None
        cancelled = False

        # Tách từng VALUE thành đoạn cần dịch (che placeholder trước),
        # cùng cách sdk.translate.translate_table làm.
        segments = []  # (key, line_no, masked, tokens, lines)
        pending = {}
        # Đoạn bị bỏ qua vì không thuộc ngôn ngữ nguồn (giữ nguyên từng
        # byte, không gửi Google): {"key","line","reason","detected"}.
        skipped_segments = []
        skipped_key_set = set()
        for key, value in kv.items():
            if value == "":
                translated[key] = ""
                empty += 1
                continue
            lines = value.split("\n")
            segs = []
            for i, line in enumerate(lines):
                masked, tokens = mask_line(line)
                # Dòng chỉ còn token che (__PB3PHn__) thì không có chữ
                # thật để dịch — giữ nguyên, khỏi gửi Google.
                bare = masked
                for j in range(len(tokens)):
                    bare = bare.replace("__PB3PH%d__" % j, "")
                if not any(ch.isalnum() for ch in bare):
                    continue
                # Lọc theo ngôn ngữ nguồn ở mức đoạn (mục A): đoạn phát
                # hiện mạnh thuộc ngôn ngữ khác thì KHÔNG dịch. Phát hiện
                # trên dòng ĐÃ CHE (masked) chứ không phải dòng thô: chữ
                # trong thẻ (<size>, </b>) không phải ngôn ngữ của chuỗi.
                is_src, reason, det = _source_match(masked, src)
                if not is_src:
                    skipped_segments.append(
                        {"key": key, "line": i, "reason": reason,
                         "detected": det})
                    skipped_key_set.add(key)
                    continue
                segs.append((i, masked, tokens))
            if not segs:
                translated[key] = value
                unchanged += 1
                continue
            pending[key] = (value, lines, segs)
            for (i, masked, tokens) in segs:
                segments.append((key, i, masked, tokens))
        total = len(segments)

        seg_out = {}  # (key, line_no) -> (translated_masked_or_None, origin)
        # --- Khử trùng lặp: gom các dòng đã che giống nhau (chuẩn hoá
        # khoảng trắng) về 1 unique; dịch 1 lần rồi map lại cho mọi key.
        # An toàn: unmask dùng tokens riêng của từng dòng (xem mask_line).
        uniques = []  # {"masked", "tokens", "keys":[(key,line_no)], "prio"}
        uid_of_norm = {}
        for (key, line_no, masked, _tokens) in segments:
            norm = _norm_segment_text(masked)
            uid = uid_of_norm.get(norm)
            if uid is None:
                uid = len(uniques)
                uid_of_norm[norm] = uid
                uniques.append({"masked": masked, "tokens": _tokens,
                                "keys": [(key, line_no)],
                                "prio": _priority_of(key, masked)})
            else:
                uniques[uid]["keys"].append((key, line_no))
                p = _priority_of(key, masked)
                if p < uniques[uid]["prio"]:
                    uniques[uid]["prio"] = p
        deduped = len(segments) - len(uniques)

        # --- Tra từ điển game TRƯỚC từ điển offline (T2): dòng nào
        # khớp chính xác thì dùng luôn, không gửi Google, không bị từ
        # điển offline hay bước sửa placeholder ghi đè. Tra trên dòng
        # GỐC (bỏ che token) để entry có placeholder ("Save {0} gold")
        # vẫn khớp; bản dịch giữ nguyên placeholder đó.
        uniq_val = [None] * len(uniques)   # masked đã dịch / None
        uniq_origin = [""] * len(uniques)
        if game_glossary is not None and uniques:
            for uid, u in enumerate(uniques):
                orig = unmask_line(u["masked"], u["tokens"])
                hit = game_glossary.lookup(orig)
                if hit is not None:
                    uniq_val[uid] = hit
                    uniq_origin[uid] = "glossary"
                    glossary_count += len(u["keys"])

        # --- Tra từ điển offline TRƯỚC (glossary-first): chuỗi nào từ
        # điển biết thì không gửi Google (tiết kiệm request + không tốn
        # hạn mức proxy).
        if uniques:
            fb_all = fallback.translate_batch(
                [u["masked"] for u in uniques], src, to)
            for uid, val in enumerate(fb_all or []):
                if val is not None and uniq_val[uid] is None:
                    uniq_val[uid] = val
                    uniq_origin[uid] = "glossary"
                    glossary_count += len(uniques[uid]["keys"])
        done_segments = sum(len(uniques[uid]["keys"])
                            for uid in range(len(uniques))
                            if uniq_val[uid] is not None)
        if done_segments:
            _emit("translate", done_segments, total)

        # --- Phần còn lại: sắp theo ưu tiên rồi chia batch.
        need = [uid for uid in range(len(uniques))
                if uniq_val[uid] is None]
        need.sort(key=lambda uid: (uniques[uid]["prio"], uid))
        batches = []
        cur, cur_chars = [], 0
        for uid in need:
            m = uniques[uid]["masked"]
            if cur and (len(cur) >= BATCH_MAX_STRINGS
                        or cur_chars + len(m) > BATCH_MAX_CHARS):
                batches.append(cur)
                cur, cur_chars = [], 0
            cur.append(uid)
            cur_chars += len(m)
        if cur:
            batches.append(cur)

        batch_requests = single_requests = 0
        batch_failures = 0
        batch_mode = None
        quota_hit = False
        # Ngắt mạch: Google lỗi liên tiếp 3 chuỗi (vd IP bị 429+Sorry) thì
        # dừng gọi Google cho phần còn lại, dùng thẳng từ điển offline —
        # khỏi bắt người dùng chờ mỗi chuỗi thử lại 3 lần vô ích.
        consec_failures = 0

        async def _translate_batch_split(texts, uids, errbox):
            """Dịch 1 batch qua _gt_batch; lỗi marker thì chia đôi đệ quy.

            Trả danh sách uid CHƯA dịch được qua batch (rỗng = xong hết).
            Uid nào dịch xong qua batch thì ghi uniq_val/uniq_origin ngay
            (kể cả khi chỉ 1 nửa batch thành công) — chỉ phần chưa xong
            mới rơi về dịch đơn. Batch trả sai số chuỗi cũng tính là batch
            hỏng (như mất marker) và được chia. Lỗi cancelled/quota thì
            ném lên để caller xử lý như cũ (không chia). Lỗi đầu tiên ghi
            vào errbox (hộp lỗi riêng mỗi batch — batch chạy song song không
            được dùng chung một hộp) để caller báo network_error.
            """
            nonlocal batch_requests, batch_mode, google_count
            if _gt_batch is None:
                return list(uids)
            if _is_cancelled():
                raise Exception("(cancelled): người dùng đã hủy dịch.")
            splittable = False
            try:
                raw = await _gt_batch(texts, src, to)
                items, mode = _coerce_batch_result(raw)
                if items is None or len(items) != len(uids):
                    splittable = True
                    raise ValueError(
                        "batch trả %s chuỗi, cần %d"
                        % (len(items) if items is not None else "?",
                           len(uids)))
                for uid, val in zip(uids, items):
                    uniq_val[uid] = val
                    uniq_origin[uid] = "google"
                    google_count += len(uniques[uid]["keys"])
                batch_requests += 1
                batch_mode = mode
                return []
            except Exception as exc:  # noqa: BLE001
                kind = _gt_kind(exc)
                if kind in ("cancelled", "quota"):
                    raise
                if errbox[0] is None:
                    errbox[0] = exc
                if (splittable or _batch_split_retryable(exc)) \
                        and len(uids) > _BATCH_SPLIT_MIN:
                    mid = len(uids) // 2
                    left = await _translate_batch_split(
                        texts[:mid], uids[:mid], errbox)
                    right = await _translate_batch_split(
                        texts[mid:], uids[mid:], errbox)
                    return left + right
                return list(uids)

        async def _run_one_batch(batch):
            """Dịch xong 1 batch (batch + rơi về dịch đơn nếu cần).

            Chạy song song tối đa _BATCH_CONCURRENCY batch trong 1 wave
            (asyncio.gather) — các batch chạm uid rời nhau nên không ghi
            đè nhau; mọi đếm (batch_requests, google_count, done_segments,
            ...) chỉ tăng bằng phép += đồng bộ, an toàn trong asyncio.
            Hủy/quota/latch: giữ nguyên ngữ nghĩa như vòng tuần tự cũ
            (hủy thì dừng sau wave hiện tại, quota thì wave sau chỉ còn
            offline, latch tắt batch sau 2 lần lỗi).
            """
            nonlocal batch_failures, network_error, quota_hit, _gt, \
                _gt_batch, done_segments, cancelled, consec_failures, \
                single_requests, google_count, glossary_count
            if _is_cancelled():
                cancelled = True
                return
            texts = [uniques[uid]["masked"] for uid in batch]
            got_batch = False
            failed_uids = list(batch)
            if _gt_batch is not None and not quota_hit:
                _errbox = [None]
                try:
                    failed_uids = await _translate_batch_split(
                        texts, batch, _errbox)
                except Exception as exc:  # noqa: BLE001 - cancelled/quota
                    kind = _gt_kind(exc)
                    if kind == "cancelled":
                        cancelled = True
                        return
                    if network_error is None:
                        network_error = _gt_error_text(exc)
                    if kind == "quota":
                        quota_hit = True
                        _gt = None
                        _gt_batch = None
                        warnings.append(
                            "Proxy dịch hết hạn mức tháng — dừng gọi Google, "
                            "dùng từ điển offline cho phần còn lại.")
                    else:
                        batch_failures += 1
                        if batch_failures >= _BATCH_FAIL_LATCH:
                            _gt_batch = None
                            warnings.append(
                                "Batch dịch lỗi liên tiếp %d lần — tắt batch, "
                                "dịch từng chuỗi cho phần còn lại."
                                % _BATCH_FAIL_LATCH)
                    # Chỉ dịch đơn lại các uid chưa có giá trị (uid nào
                    # chia đôi đã xong thì giữ bản batch).
                    failed_uids = [uid for uid in batch
                                   if uniq_val[uid] is None]
                else:
                    if not failed_uids:
                        batch_failures = 0
                        consec_failures = 0
                        got_batch = True
                    else:
                        if network_error is None and _errbox[0] is not None:
                            network_error = _gt_error_text(_errbox[0])
                        # Tiến độ cho các uid chia đôi đã cứu được (nếu
                        # có); vòng đơn bên dưới đếm nốt phần còn lại.
                        done_segments += sum(
                            len(uniques[uid]["keys"]) for uid in batch
                            if uniq_val[uid] is not None)
                        if len(failed_uids) == len(batch):
                            # Không cứu được chuỗi nào qua batch — tính
                            # như batch lỗi (đếm latch như trước).
                            batch_failures += 1
                            if batch_failures >= _BATCH_FAIL_LATCH:
                                _gt_batch = None
                                warnings.append(
                                    "Batch dịch lỗi liên tiếp %d lần — tắt "
                                    "batch, dịch từng chuỗi cho phần còn lại."
                                    % _BATCH_FAIL_LATCH)
                        else:
                            # Chia đôi cứu được 1 phần — batch vẫn chạy,
                            # không đếm latch (lỗi nằm ở vài chuỗi xấu,
                            # không phải batch hỏng toàn cục).
                            batch_failures = 0
                            consec_failures = 0
            if got_batch:
                done_segments += sum(len(uniques[uid]["keys"])
                                     for uid in batch)
                _emit("translate", done_segments, total)
                return
            # --- Rơi về từng chuỗi cho các uid chưa xong (batch lỗi /
            # không có hàm batch / hết quota thì _gt đã None -> chỉ còn
            # glossary). Uid nào chia đôi đã dịch xong thì không dịch lại.
            stop_here = False
            for uid in failed_uids:
                if _is_cancelled():
                    cancelled = True
                    stop_here = True
                    break
                masked = uniques[uid]["masked"]
                val = None
                origin = ""
                if _gt is not None and not quota_hit:
                    try:
                        got = await _gt(masked, src, to)
                        val = str(got)
                        origin = "google"
                        single_requests += 1
                        consec_failures = 0
                        # Mục C1: ghi nhận bước fallback đã cứu chuỗi này
                        # (vd Google 429 → MyMemory) để báo rõ ở UI.
                        _step = _gt_fallback_step()
                        if _step:
                            fallback_used[_step] = fallback_used.get(_step, 0) + 1
                    except Exception as exc:  # noqa: BLE001
                        kind = _gt_kind(exc)
                        if kind in ("all_failed", "quota"):
                            # Chẩn đoán cuối cùng, rõ hơn lỗi batch đầu
                            # tiên (vd "batch lỗi, sẽ dịch lại từng chuỗi"):
                            # ghi đè để UI hiện đúng lý do thật.
                            network_error = _gt_error_text(exc)
                        elif network_error is None:
                            network_error = _gt_error_text(exc)
                        if kind == "cancelled":
                            cancelled = True
                            stop_here = True
                            break
                        if kind == "quota":
                            quota_hit = True
                            _gt = None
                            warnings.append(
                                "Proxy dịch hết hạn mức tháng — dừng gọi "
                                "Google, dùng từ điển offline cho phần còn lại.")
                        consec_failures += 1
                        if consec_failures >= 3:
                            _gt = None
                            warnings.append(
                                "Google lỗi liên tiếp 3 chuỗi — dừng gọi Google, "
                                "dùng từ điển offline cho phần còn lại.")
                if val is None:
                    fb = fallback.translate_batch([masked], src, to)
                    if fb and fb[0] is not None:
                        val = fb[0]
                        origin = "glossary"
                uniq_val[uid] = val
                uniq_origin[uid] = origin
                if origin == "google":
                    google_count += len(uniques[uid]["keys"])
                elif origin == "glossary":
                    glossary_count += len(uniques[uid]["keys"])
                done_segments += len(uniques[uid]["keys"])
                _emit("translate", done_segments, total)
            if stop_here:
                return

        for _w in range(0, len(batches), _BATCH_CONCURRENCY):
            if _is_cancelled():
                cancelled = True
                break
            _wave = batches[_w:_w + _BATCH_CONCURRENCY]
            await asyncio.gather(*(_run_one_batch(b) for b in _wave))
            if cancelled:
                break

        for uid, u in enumerate(uniques):
            for (key, line_no) in u["keys"]:
                seg_out[(key, line_no)] = (uniq_val[uid], uniq_origin[uid])

        # Sửa placeholder (lâu dài): dòng nào batch gộp làm lệch
        # placeholder (thứ tự + số lần, như cổng kiểm tra) thì dịch đơn
        # lại 1 lần bằng _gt — dịch đơn thường giữ token tốt hơn batch
        # gộp. Vẫn lệch thì giữ kết quả cũ + cảnh báo như trước (không
        # giấu lỗi). Bỏ qua khi đã hết hạn mức hoặc người dùng đã hủy.
        if _gt is not None and not quota_hit:
            _seg_tokens = {}
            for _key, _line_no, _masked, _tokens in segments:
                _seg_tokens[(_key, _line_no)] = (_masked, _tokens)
            _by_masked = {}
            for (_key, _line_no), (_val, _origin) in list(seg_out.items()):
                if _val is None:
                    continue
                # Đoạn từ từ điển game: KHÔNG dịch lại bằng máy (xem
                # translate_table — entry của user không bị ghi đè).
                if _origin == "glossary":
                    continue
                _masked, _tokens = _seg_tokens.get((_key, _line_no),
                                                   (None, None))
                if _masked is None:
                    continue
                _src_line = pending[_key][1][_line_no]
                if placeholders_match_strict(
                        _src_line, unmask_line(_val, _tokens)):
                    continue
                _by_masked.setdefault(_masked, []).append(
                    (_key, _line_no, _tokens))
            # Dịch lại theo BATCH (<=25 masked/batch) thay vì 1 lần _gt mỗi
            # dòng — đo thật: 28 dòng lệch tốn ~14s khi dịch đơn, gom batch
            # chỉ ~1-2 request. Tiêu chuẩn khớp giữ nguyên
            # (placeholders_match_strict); dòng nào batch sửa vẫn lệch thì
            # dịch đơn lại 1 lần như trước (dịch đơn thường giữ token tốt
            # hơn batch gộp); vẫn lệch thì giữ bản cũ + cảnh báo như trước.
            _masked_list = list(_by_masked)
            for _i in range(0, len(_masked_list), _REPAIR_BATCH_STRINGS):
                if _is_cancelled():
                    break
                _chunk = _masked_list[_i:_i + _REPAIR_BATCH_STRINGS]
                _retries = {}
                if _gt_batch is not None:
                    try:
                        _raw = await _gt_batch(_chunk, src, to)
                        _items, _mode = _coerce_batch_result(_raw)
                        if _items is not None and len(_items) == len(_chunk):
                            batch_requests += 1
                            for _m, _v in zip(_chunk, _items):
                                _retries[_m] = _v
                    except Exception:
                        _retries = {}
                _need_single = []
                for _masked in _chunk:
                    _retry = _retries.get(_masked)
                    _all_fixed = _retry is not None
                    if _retry is not None:
                        for _key, _line_no, _tokens in _by_masked[_masked]:
                            _src_line = pending[_key][1][_line_no]
                            if placeholders_match_strict(
                                    _src_line, unmask_line(_retry, _tokens)):
                                seg_out[(_key, _line_no)] = (_retry, "google")
                            else:
                                _all_fixed = False
                    if not _all_fixed:
                        _need_single.append(_masked)
                for _masked in _need_single:
                    if _is_cancelled():
                        break
                    try:
                        _retry = str(await _gt(_masked, src, to))
                        single_requests += 1
                    except Exception:
                        continue
                    for _key, _line_no, _tokens in _by_masked[_masked]:
                        _src_line = pending[_key][1][_line_no]
                        if placeholders_match_strict(
                                _src_line, unmask_line(_retry, _tokens)):
                            seg_out[(_key, _line_no)] = (_retry, "google")

        incomplete_keys = []
        for key, (value, lines, segs) in pending.items():
            out_lines = list(lines)
            n_missing = n_glossary = 0
            for (i, _masked, tokens) in segs:
                val, origin = seg_out.get((key, i), (None, ""))
                if val is None:
                    n_missing += 1
                    continue
                out_lines[i] = unmask_line(val, tokens)
                if origin == "glossary":
                    n_glossary += 1
            new_value = "\n".join(out_lines)
            if n_missing == len(segs):
                translated[key] = value
                untranslated += 1
                untranslated_keys.append(key)
                incomplete_keys.append(key)
            elif n_missing:
                translated[key] = new_value
                untranslated_keys.append(key)
                incomplete_keys.append(key)
                warnings.append("key %s: %d/%d dòng chưa dịch (giữ nguyên)"
                                % (key, n_missing, len(segs)))
            else:
                translated[key] = new_value
                if n_missing == 0 and not placeholders_ok(value, new_value):
                    warnings.append(
                        "key %s: placeholder/thẻ/xuống dòng thay đổi sau khi "
                        "dịch (đã giữ bản dịch, kiểm tra lại trước khi ghi "
                        "ngược)" % key)
        translated = {key: translated[key] for key in kv}
        credit_key = None
        if credit:
            credit_key = apply_credit(translated)
        # Key bị bỏ qua TOÀN BỘ (không có đoạn nào thuộc nguồn): giá trị
        # phải giữ nguyên từng byte — tự kiểm tra trước khi trả về.
        fully_skipped_keys = sorted(
            k for k in skipped_key_set if k not in pending)
        skipped_by_reason = {}
        skipped_by_lang = {}
        for s in skipped_segments:
            skipped_by_reason[s["reason"]] = \
                skipped_by_reason.get(s["reason"], 0) + 1
            if s["detected"]:
                skipped_by_lang[s["detected"]] = \
                    skipped_by_lang.get(s["detected"], 0) + 1
        self_check = _self_check(kv, translated, set(fully_skipped_keys),
                                 credit_key)
        if google_count == 0:
            translated_by = "offline"
        elif glossary_count == 0 and untranslated == 0:
            translated_by = "google"
        else:
            translated_by = "mixed"
        if batch_requests:
            batch_mode_out = batch_mode or "batch"
        elif single_requests:
            batch_mode_out = "single"
        else:
            batch_mode_out = "none"
        # Mục C1: ghi chú bước fallback đã dùng, để app.js hiện rõ ở UI
        # (vd "Đã dùng MyMemory cho 12 chuỗi (Google lỗi HTTP 429)").
        # google_count vẫn đếm chung mọi chuỗi dịch trực tuyến thành công
        # (giữ tương thích harness cũ); chi tiết từng bước ở đây.
        _fallback_labels = {"proxy": "Proxy", "google": "Google",
                            "mymemory": "MyMemory",
                            "libretranslate": "LibreTranslate"}
        fallback_note = ""
        if fallback_used:
            _bits = ["%s cho %d chuỗi" % (_fallback_labels.get(k, k), v)
                     for k, v in sorted(fallback_used.items())]
            fallback_note = "Đã dùng fallback: " + ", ".join(_bits) + "."
        return json.dumps({
            "ok": True, "translated": translated, "total": int(len(kv)),
            "source_lang": src,
            "source_keys": int(len(pending)),
            "source_segments": int(total),
            "skipped_count": len(skipped_segments),
            "skipped_by_reason": skipped_by_reason,
            "skipped_by_lang": skipped_by_lang,
            "skipped_keys": fully_skipped_keys[:50],
            "self_check": self_check,
            "untranslated": int(untranslated),
            "untranslated_keys": untranslated_keys[:50],
            "warnings": warnings[:20], "credit_key": credit_key,
            "translated_by": translated_by,
            "google_count": int(google_count),
            "glossary_count": int(glossary_count),
            "network_error": network_error, "cancelled": bool(cancelled),
            "fallback_used": fallback_used,
            "fallback_note": fallback_note,
            "deduped": int(deduped),
            "unique_segments": int(len(uniques)),
            "batches": int(len(batches)),
            "batch_requests": int(batch_requests),
            "batch_max_strings": int(BATCH_MAX_STRINGS),
            "batch_concurrency": int(_BATCH_CONCURRENCY),
            "single_requests": int(single_requests),
            "batch_mode": batch_mode_out,
            # Key chưa dịch xong (đầy đủ, không cắt 50 như
            # untranslated_keys) — app.js đưa chúng về lại hàng đợi để
            # dịch tiếp khi resume, thay vì ghi nhận là "đã xong".
            "incomplete_keys": incomplete_keys,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def apply_credit_only(json_text):
    """Chỉ gắn credit 'Việt Hoá Bởi Bear' vào bảng đã dịch (không dịch).

    Dùng sau khi app.js dịch theo từng chunk với credit=False để khỏi gắn
    credit nhiều lần (mỗi chunk gắn 1 lần thì credit xuất hiện nhiều chỗ).
    """
    try:
        from sdk.translate import apply_credit
        kv = json.loads(json_text)
        if not isinstance(kv, dict):
            raise ValueError("File JSON phải là object key-value.")
        translated = {str(k): (v if isinstance(v, str)
                              else json.dumps(v, ensure_ascii=False))
                      for k, v in kv.items()}
        credit_key = apply_credit(translated)
        return json.dumps({"ok": True, "translated": translated,
                           "credit_key": credit_key}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def compress_file(path, preset=6):
    """Nén IPA kiểu WinRAR: solid LZMA tar.xz (preset mặc định 6)."""
    try:
        from sdk.compress import compress_ipa

        _check_size(path)
        dest = "/tmp/out.tar.xz"
        if os.path.exists(dest):
            os.remove(dest)
        rep = compress_ipa(path, dest, preset=int(preset),
                           progress=lambda d, t: _emit("compress", d, t),
                           force=True)
        d = rep.to_dict()
        d.update({"ok": True, "dest": dest})
        return json.dumps(d, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def decompress_file(path):
    """Giải nén archive .tar.xz do compress_file tạo ra.

    Trả thêm "filename": tên file gốc trong tar (vd 9Kings_v1.0.5.ipa)
    để JS tải về ĐÚNG tên đó thay vì tên cố định restored.bin. Không
    lấy được tên thì lấy tên input bỏ đuôi .tar.xz."""
    try:
        from sdk.compress import decompress_archive

        _check_size(path)
        dest = "/tmp/restored.bin"
        if os.path.exists(dest):
            os.remove(dest)
        rep = decompress_archive(path, dest, force=True)
        d = rep.to_dict()
        orig = str(d.get("original_name") or "")
        fname = os.path.basename(orig)
        if not fname:
            base = os.path.basename(path)
            fname = (base[:-7] if base.lower().endswith(".tar.xz")
                     else base + ".bin")
        d.update({"ok": True, "dest": dest, "filename": fname})
        return json.dumps(d, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_scan(path, face_index=None):
    """Quét 1 font TTF/OTF: đọc cmap, báo độ phủ ký tự tiếng Việt.

    TTC mà không cho face_index thì trả danh sách face để người gọi chọn
    (ok=True, kind="ttc", faces=[...]) thay vì lỗi.
    """
    try:
        from sdk import font as f

        _check_size(path)
        data = open(path, "rb").read()
        name = os.path.basename(path)
        if f.detect_font_kind(data) == "ttc" and face_index is None:
            faces = f.list_ttc_faces(data, where=name)
            return json.dumps({
                "ok": True, "kind": "ttc", "faces": faces,
                "summary": ("font collection (.ttc) có %d face — chọn 1 face "
                            "để quét/gộp" % len(faces)),
            }, ensure_ascii=False)
        rep = f.coverage(data, where=name, face_index=face_index)
        info = f.parse_font(data, where=name, face_index=face_index)
        return json.dumps({
            "ok": True, "family": rep.family, "kind": rep.kind,
            "num_glyphs": int(rep.num_glyphs), "total": int(rep.total),
            "covered": int(rep.covered), "pct": int(rep.pct),
            "missing": [int(c) for c in rep.missing[:50]],
            "missing_total": len(rep.missing),
            "missing_chars": [chr(c) for c in rep.missing[:50]],
            "summary": rep.summary_vi(),
            "warnings": list(info.warnings or []),
            "face_index": int(info.face_index),
            "face_count": int(info.face_count),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_donor_info(donor_path):
    """Kiểm chứng font đi kèm (Be Vietnam Pro): phải đủ 142/142 mục (134 chữ cái + 8 dấu)."""
    try:
        from sdk import font as f

        data = open(donor_path, "rb").read()
        rep = f.coverage(data, where="BeVietnamPro-Regular.ttf")
        return json.dumps({
            "ok": True, "family": rep.family, "kind": rep.kind,
            "num_glyphs": int(rep.num_glyphs), "total": int(rep.total),
            "covered": int(rep.covered), "pct": int(rep.pct),
            "is_full": bool(rep.is_full), "summary": rep.summary_vi(),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_merge(path, donor_path, target_face=0, donor_face=0):
    """Gộp glyph tiếng Việt còn thiếu từ font nguồn vào font đích.

    Trả font mới ra /tmp/merged.ttf (hoặc /tmp/merged.ttc khi đích là
    .ttc) để JS tải về. Font TrueType glyf và font CFF (.otf) không
    CID đều gộp được; font CFF CID-keyed (Noto CJK) thì báo lỗi rõ,
    không ghi đè. Atlas SDF của TextMeshPro tái tạo bằng tính năng
    thử nghiệm tmp_sdf_regen (nút "Tái tạo atlas SDF" ở mục TMP). TTC: chọn
    face bằng target_face/donor_face (mặc định face 0).
    """
    try:
        from sdk import font as f

        _check_size(path)
        target = open(path, "rb").read()
        donor = open(donor_path, "rb").read()
        res = f.merge_vietnamese(
            target, donor,
            progress_fn=lambda d, t: _emit("font", d, t),
            target_face=target_face, donor_face=donor_face)
        out = ("/tmp/merged.ttc"
               if f.detect_font_kind(target) == "ttc" else "/tmp/merged.ttf")
        open(out, "wb").write(res.data)
        return json.dumps({
            "ok": True, "dest": out,
            "added": len(res.added), "already": len(res.already),
            "donor_missing": len(res.donor_missing),
            "glyphs_copied": int(res.glyphs_copied),
            "glyphs_total": int(res.glyphs_total),
            "face_index": res.face_index,
            "warnings": list(res.warnings or [])[:20],
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def tmp_font_read(path):
    """Đọc tham số TMP_FontAsset (TextMeshPro) — mục 7.

    Chỉ đọc: FaceInfo (tên font, PointSize, Scale, CharacterCount,
    LineHeight, Padding, kích thước atlas), bảng glyph 36 byte, độ phủ
    tiếng Việt (134 chữ + 8 dấu), tham chiếu Material/Texture2D/Font.
    Ghi kế hoạch glyph ra /tmp/tmp_glyph_plan.json để JS tải về nạp vào
    Unity Editor. Atlas SDF tái tạo được bằng tính năng thử nghiệm
    tmp_sdf_regen (nút "Tái tạo atlas SDF" ở mục TMP — đo MAE so với
    atlas gốc); script Unity Editor (site/unity_editor/) vẫn là
    phương án dự phòng.
    """
    try:
        from sdk import tmp_font as tf

        _check_size(path)
        res, _reused = _tmp_scan_cached(path)
        plan = res["glyph_plan"]
        out = "/tmp/tmp_glyph_plan.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        return json.dumps({
            "ok": True, "dest": out, "filename": "tmp_glyph_plan.json",
            "assets": res["items"],
            "candidates": res["candidates"],
            "summary": res["summary"],
            "warnings": list(res["warnings"] or []),
            "files_scanned": list(res["files_scanned"] or []),
            "bundle_nodes": int(res["bundle_nodes"]),
            "glyph_plan": plan,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# Cache lần quét TMP gần nhất (mục C6): nút "Tạo font fallback" dùng lại
# kết quả của nút "Đọc TMP_FontAsset" thay vì quét lại cả file/IPA.
# Khóa = (đường dẫn, kích thước, mtime); khác file thì quét lại.
_LAST_TMP_SCAN = {"path": None, "size": None, "mtime": None, "res": None}


def _tmp_scan_cached(path):
    """Quét TMP, dùng lại kết quả lần trước nếu đúng cùng file."""
    from sdk import tmp_font as tf

    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if (key is not None and _LAST_TMP_SCAN["res"] is not None
            and (_LAST_TMP_SCAN["path"], _LAST_TMP_SCAN["size"],
                 _LAST_TMP_SCAN["mtime"]) == key):
        return _LAST_TMP_SCAN["res"], True
    res = tf.scan_source(path)
    if key is not None:
        _LAST_TMP_SCAN.update({"path": key[0], "size": key[1],
                               "mtime": key[2], "res": res})
    return res, False


def tmp_font_fallback(path):
    """Lập kế hoạch font fallback TMP (mục C6).

    Đọc lại kết quả quét TMP (dùng cache của tmp_font_read nếu cùng file,
    không thì quét mới) rồi ghi kế hoạch fallback ra
    /tmp/tmp_fallback_plan.json để JS tải về. Kế hoạch ghi: chữ Việt nào
    thiếu, tham số atlas gốc cần giữ, tên asset fallback mới, các bước
    Editor. Web KHÔNG tạo được TMP_FontAsset thật — atlas SDF của
    fallback chỉ sinh được trong Unity Editor (script
    site/unity_editor/TMP_VietnameseFallback.cs).
    """
    try:
        from sdk import tmp_font as tf

        _check_size(path)
        res, reused = _tmp_scan_cached(path)
        plan = tf.build_fallback_plan(res["items"], res["glyph_plan"])
        out = "/tmp/tmp_fallback_plan.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        return json.dumps({
            "ok": True, "dest": out, "filename": "tmp_fallback_plan.json",
            "scan_reused": reused,
            "assets": res["items"],
            "candidates": res["candidates"],
            "summary": res["summary"],
            "warnings": list(res["warnings"] or []),
            "files_scanned": list(res["files_scanned"] or []),
            "bundle_nodes": int(res["bundle_nodes"]),
            "fallback_plan": plan,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _parse_extra_codepoints(extra):
    """Chuoi ky tu can them -> list codepoint (sap xep, khong trung).

    Nhan: chuoi ky tu thuong ("aàáâ...") hoac "U+1EA0,U+1EA1" / "768,769".
    """
    if not extra:
        return []
    extra = str(extra).strip()
    if not extra:
        return []
    if "U+" in extra.upper() or "," in extra:
        out = []
        for part in extra.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if part.upper().startswith("U+"):
                out.append(int(part[2:], 16))
            else:
                out.append(int(part, 0))
        return sorted(set(out))
    return sorted(set(ord(ch) for ch in extra))


def _iter_tmp_sf(path):
    """Yield (display, data, sf, ress_slice, ress_full) cho moi
    SerializedFile trong nguon.

    Nguon: thu muc / ZIP/IPA / file .assets/.unity3d doc lap (nhu
    tmp_font.scan_source). ress_slice(path, offset, size) doc 1 lat cat
    cua file .resS tuong ung (trong ZIP hoac roi tren dia);
    ress_full(path=None) doc TOAN BO file .resS tuong ung (None neu
    khong co — vd clone can append atlas, tmp_sdf can patch atlas).
    data la bytes goc cua SerializedFile (de ghi nguoc).
    """
    from sdk import containers as cont
    from sdk import tmp_font as tf
    from sdk import unity as uni

    src = str(path)
    if os.path.isdir(src):
        for dirpath, _dirs, files in os.walk(src):
            for fn in sorted(files):
                if not tf.is_unity_entry(fn):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    data = open(full, "rb").read()
                    sf = uni.parse_serialized(data, where=full)
                except Exception:
                    continue

                def _slice(p, off, size, _full=full):
                    ress_path = _full + ".resS"
                    if not os.path.exists(ress_path):
                        # resS co the nam canh file goc theo ten stream
                        base = os.path.dirname(_full)
                        cand = os.path.join(base, os.path.basename(p))
                        ress_path = cand
                    with open(ress_path, "rb") as fh:
                        fh.seek(off)
                        return fh.read(size)

                def _full(p=None, _full=full):
                    ress_path = _full + ".resS"
                    if not os.path.exists(ress_path) and p:
                        base = os.path.dirname(_full)
                        ress_path = os.path.join(base,
                                                 os.path.basename(p))
                    if not os.path.exists(ress_path):
                        return None
                    with open(ress_path, "rb") as fh:
                        return fh.read()

                yield full, data, sf, _slice, _full
        return
    if zipfile.is_zipfile(src):
        vfs = cont.open_vfs(src)
        entries = list(vfs.iter_entries())
        by_suffix = {}
        for name in entries:
            by_suffix[name] = name
            base = name.rsplit("/", 1)[-1]
            by_suffix.setdefault(base, name)
        for name in entries:
            if not tf.is_unity_entry(name):
                continue
            try:
                data = vfs.read(name)
                sf = uni.parse_serialized(data, where=name)
            except Exception:
                continue

            def _slice(p, off, size, _vfs=vfs, _map=by_suffix):
                entry = _map.get(p) or _map.get(p.rsplit("/", 1)[-1])
                if entry is None:
                    for cand in _map.values():
                        if cand.endswith("/" + p) or cand.endswith(p):
                            entry = cand
                            break
                if entry is None:
                    raise ValueError("khong co file resS %s" % p)
                return bytes(_vfs.slice(entry, off, size))

            def _full(p=None, _vfs=vfs, _map=by_suffix, _name=name):
                entry = _map.get(_name + ".resS")
                if entry is None and p:
                    entry = _map.get(p) or _map.get(p.rsplit("/", 1)[-1])
                if entry is None:
                    for cand in _map.values():
                        if cand.endswith("/" + _name + ".resS") \
                                or cand.endswith(_name + ".resS"):
                            entry = cand
                            break
                if entry is None:
                    return None
                return bytes(_vfs.read(entry))

            yield name, data, sf, _slice, _full
        return
    # File .assets/.unity3d doc lap.
    data = open(src, "rb").read()
    sf = uni.parse_serialized(data, where=src)

    def _slice(p, off, size, _src=src):
        ress_path = _src + ".resS"
        if not os.path.exists(ress_path):
            base = os.path.dirname(_src)
            ress_path = os.path.join(base, os.path.basename(p))
        with open(ress_path, "rb") as fh:
            fh.seek(off)
            return fh.read(size)

    def _full(p=None, _src=src):
        ress_path = _src + ".resS"
        if not os.path.exists(ress_path) and p:
            base = os.path.dirname(_src)
            ress_path = os.path.join(base, os.path.basename(p))
        if not os.path.exists(ress_path):
            return None
        with open(ress_path, "rb") as fh:
            return fh.read()

    yield src, data, sf, _slice, _full


def tmp_sdf_regen(path, asset_name=None, extra=""):
    """Tai tao atlas SDF cho 1 TMP_FontAsset (THU NGHIEM, dry-run).

    path: file game (IPA/.assets/.unity3d/thu muc). asset_name: ten TMP
    asset (vd "Aniara_Light SDF") hoac so thu tu trong danh sach quet;
    None = asset dau tien co font nguon nhung. extra: chuoi ky tu can
    them (vd chu Viet thieu) hoac "U+1EA0,U+1EA1".
    Chi tai tao trong cung kich thuoc atlas (fail-closed neu khong xep
    vua). KHONG ghi vao file game — tra atlas moi (.raw) + bang glyph
    moi + bao cao MAE de tai ve; ghi nguoc vao game bang op
    font_writeback mode tmp_sdf (nut "Ghi nguoc font Viet").
    """
    try:
        from sdk import tmp_font as tf
        from sdk import tmp_sdf as ts

        _check_size(path)
        extra_cps = _parse_extra_codepoints(extra)
        # Tim asset: quet cac SerializedFile, khop ten chinh xac truoc
        # (ke ca bo khoang trang/chu hoa), roi chuoi con, roi so thu tu.
        # KHONG loc is_tmp: tren file khong typetree (vd PB3) script
        # nam ngoai file nen is_tmp luon False — xac nhan bang decode
        # duoc + co bang glyph thay vi script [DA DO THAT: PB3 43 TMP
        # deu is_tmp False, op cu tra "da quet: khong co"].
        found = None
        sub = None
        scanned_names = []
        for display, _data, sf, ress_slice, _ress_full in _iter_tmp_sf(path):
            for f in tf.find_tmp_font_assets(sf):
                try:
                    raw = sf.object_bytes(f["path_id"])
                    dec = tf.decode_object(raw, sf.endian)
                except Exception:
                    continue
                nm = dec.get("name") or ""
                scanned_names.append(nm)
                if not dec.get("glyph_table"):
                    continue
                kind = _match_kind(nm, asset_name,
                                   len(scanned_names) - 1)
                if kind == 2:
                    found = (display, sf, f["path_id"], ress_slice, dec)
                    break
                if kind == 1 and sub is None:
                    sub = (display, sf, f["path_id"], ress_slice, dec)
            if found:
                break
        if found is None:
            found = sub
        if found is None:
            return _err(ValueError(
                "khong tim thay TMP asset %r (da quet: %s)"
                % (asset_name, ", ".join(scanned_names) or "khong co")))
        display, sf, pid, ress_slice, dec = found
        rep = ts.regen_tmp_asset(sf, pid, extra_cps, ress_slice)
        # Ghi atlas moi + ke hoach JSON ra /tmp de JS tai ve.
        safe = "".join(c if c.isalnum() or c in ("-", "_") else "_"
                       for c in rep["name"])
        atlas_out = "/tmp/tmp_sdf_%s.raw" % safe
        with open(atlas_out, "wb") as fh:
            fh.write(rep["new_atlas"])
        plan = {k: v for k, v in rep.items()
                if k not in ("new_atlas", "new_table")}
        plan["atlas_file"] = atlas_out
        plan["source"] = display
        plan["table_bytes"] = len(rep["new_table"])
        plan_out = "/tmp/tmp_sdf_%s.json" % safe
        with open(plan_out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        return json.dumps({
            "ok": True, "dest": atlas_out, "plan": plan_out,
            "filename": os.path.basename(atlas_out),
            "name": rep["name"], "layout": rep["layout"],
            "point_size": rep["point_size"], "padding": rep["padding"],
            "atlas": rep["atlas"], "font_source": rep["font_source"],
            "glyphs_old": rep["glyphs_old"],
            "glyphs_new": rep["glyphs_new"], "added": len(rep["added"]),
            "added_cps": rep["added"],
            "unfitted": rep["unfitted"], "empty": rep["empty"],
            "median_mae": rep["median_mae"], "mean_mae": rep["mean_mae"],
            "pct_lt3": rep["pct_lt3"], "pass": rep["pass"],
            "worst": rep["worst"], "note": rep["note"],
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _donor_font_bytes(donor_path):
    """Doc font donor Be Vietnam Pro (du 142/142).

    donor_path: duong dan do JS truyen (tren web:
    /pb3web/fonts/BeVietnamPro-Regular.ttf). None = tim canh bridge.py
    (pb3/fonts/BeVietnamPro-Regular.ttf, dung cho test CPython).
    """
    if donor_path:
        with open(donor_path, "rb") as fh:
            return fh.read()
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "BeVietnamPro-Regular.ttf")
    if os.path.exists(cand):
        with open(cand, "rb") as fh:
            return fh.read()
    raise ValueError("khong tim thay font donor BeVietnamPro-Regular.ttf")


def _match_kind(nm, asset_name, index):
    """Muc do khop ten asset: 2 = chinh xac, 1 = chuoi con, 0 = khong.

    Chinh xac ke ca khi chi khac khoang trang / chu hoa — vd ten ke
    hoach "Perfect DOS VGA 437" (family trong font) phai khop Font
    object "PerfectDOSVGA437" (m_Name). None / so thu tu: khop nhu cu
    (2). Khop chinh xac LUON thang chuoi con: "LiberationSans SDF"
    khong duoc roi vao "LiberationSans SDF - Fallback" chi vi Fallback
    dung truoc trong file [DA DO THAT tren PB3: pid 1206 < 1207].
    """
    if asset_name is None:
        return 2
    if str(asset_name).isdigit():
        return 2 if index == int(asset_name) else 0
    nm = nm or ""
    want = str(asset_name)
    if nm == want:
        return 2
    norm = lambda s: "".join(str(s).split()).lower()
    if norm(nm) == norm(want):
        return 2
    if want in nm or norm(want) in norm(nm):
        return 1
    return 0


def _tmp_population_from_name(name):
    """AtlasPopulationMode suy tu ten asset (quy uoc dat ten cua TMP):
    "<Font> SDF - DYNAMIC" = Dynamic, "<Font> SDF" = Static. Chi dung
    khi khong doc duoc APM tu bo cuc byte (PB3 khong typetree, bo cuc
    nhi phan Kusa khong dung tren PB3) — ke hoach ghi ro la suy tu ten
    [HYPOTHESIS, ten do TMP dat luc tao asset]."""
    if not name:
        return None
    if name.endswith("SDF - DYNAMIC"):
        return "Dynamic"
    if name.endswith("SDF"):
        return "Static"
    return None


def _tmp_source_font_name(name):
    """Ten font nguon suy tu ten TMP: bo hau to " SDF" / " SDF - DYNAMIC"."""
    if not name:
        return None
    if name.endswith("SDF - DYNAMIC"):
        return name[:-len(" SDF - DYNAMIC")]
    if name.endswith(" SDF"):
        return name[:-len(" SDF")]
    return None


def _plan_tmp_entry_fw(name, path_id, display, raw, endian, fonts,
                       all_font_names=None):
    """1 muc TMP trong ke hoach auto, tu decode truc tiep (khong qua
    font_check — font_check danh dau moi TMP PB3 la unconfirmed vi
    khong co typetree/script, nen ke hoach tu no toan route "none").

    fonts: muc unity_font cung file (tu scan_serialized_file) de tra
    cmap font nguon theo ten; all_font_names: ten font o MOI file de
    ghi chu khi font nguon chi co o file khac (ghi nguoc chi dung
    duoc font trong cung file). Do phu atlas doc tu bang character
    (locate_char_table, dung ca 2 layout); % dung atlas uoc tu dien
    tich rect glyph (chi layout moi co rect — layout cu de None).
    """
    from sdk import font_writeback as fw
    from sdk import tmp_font as tf

    dec = tf.decode_object(raw, endian)
    gt = dec.get("glyph_table")
    fi = dec.get("font_info") or {}
    population = _tmp_population_from_name(name)
    atlas_letters = atlas_marks = None
    used_pct = None
    if gt:
        try:
            loc = fw.locate_char_table(raw, endian, gt)
            unis = {r["unicode"] for r in loc["records"]}
            letters = set(tf.vietnamese_letters_134())
            marks = set(tf.combining_marks_8())
            atlas_letters = len(letters & unis)
            atlas_marks = len(marks & unis)
        except Exception:
            pass
        if gt.get("layout") == "new" and fi.get("atlas_width") \
                and fi.get("atlas_height"):
            try:
                recs = tf.read_glyph_records(raw, gt["offset"],
                                             gt["count"], gt["stride"],
                                             endian)
                area = sum(max(0, r["rect"][2]) * max(0, r["rect"][3])
                           for r in recs if r.get("rect"))
                used_pct = round(
                    100.0 * area / (fi["atlas_width"] * fi["atlas_height"]),
                    1)
            except Exception:
                pass
    base = _tmp_source_font_name(name)
    src = None
    if base:
        norm = lambda s: "".join(str(s).split()).lower()
        for f in fonts:
            if f.get("name") == base or f.get("family") == base \
                    or norm(f.get("name") or "") == norm(base) \
                    or norm(f.get("family") or "") == norm(base):
                src = f
                break
    src_present = src is not None
    notes = []
    if not src_present and base and all_font_names:
        norm = lambda s: "".join(str(s).split()).lower()
        if base in all_font_names or norm(base) in (
                norm(n) for n in all_font_names):
            notes.append("Font nguon %s co o file .assets khac, khong co "
                         "trong cung file nay — ghi nguoc chi dung duoc "
                         "font trong cung file." % base)
    src_letters = (src or {}).get("cmap_letters")
    src_marks = (src or {}).get("cmap_marks")
    classify = {"kind": "tmp", "name": name, "confirmed": True,
                "population": population,
                "atlas_letters": atlas_letters, "atlas_marks": atlas_marks,
                "source_present": src_present,
                "source_letters": src_letters, "source_marks": src_marks,
                "atlas_is_bitmap": None, "atlas_used_pct": used_pct,
                "fallback_count": None}
    route = fw.classify_case(classify)
    reason = route["reason_vi"]
    if population is not None:
        notes.append("Population suy tu ten asset [HYPOTHESIS].")
    if src_present:
        notes.append("Font nguon suy tu ten asset [HYPOTHESIS].")
    if (route["route"] == "tmp_dynamic_source_merge" and src_present
            and (src_letters is None or src_marks is None)):
        reason = ("TMP %s Dynamic, font nguon co mat nhung chua doc duoc "
                  "cmap — gop glyph Viet vao font nguon roi kiem tra lai."
                  % name)
    if route["route"] == "tmp_dynamic_fallback" and used_pct is None:
        reason = reason.replace(
            "atlas dùng None%",
            "atlas khong con cho (chua do duoc % dung)")
    if route["route"] == "tmp_static_sdf":
        notes.append("Chua do pixel atlas — neu la bitmap (khong phai SDF), "
                     "buoc ghi se tu choi E_BITMAP_ATLAS.")
    if route["route"] == "tmp_dynamic_fallback":
        notes.append("Clone giu APM cua asset goc + m_SourceFontFile goc "
                     "[HYPOTHESIS, can kiem chung tren game that].")
    missing = None
    if atlas_letters is not None and atlas_marks is not None:
        missing = 142 - atlas_letters - atlas_marks
    return {"kind": "tmp", "name": name, "path_id": path_id,
            "source": display, "confirmed": True,
            "population": population, "atlas": "%sx%s" % (
                fi.get("atlas_width"), fi.get("atlas_height"))
            if fi.get("atlas_width") else None,
            "missing_total": missing, "fallback_count": None,
            "source_present": src_present,
            "source_cmap_known": src_letters is not None,
            "source_letters": src_letters, "source_marks": src_marks,
            "route": route["route"], "reason": reason,
            "action": route["action"], "notes": notes}


def _font_writeback_plan(path):
    """Mode auto: chi lap ke hoach (khong ghi gi) — phan loai tung Font
    object / TMP asset sang duong ghi nguoc F2. Ghi ke hoach ra
    /tmp/font_writeback_plan.json de JS tai ve.

    TMP lay tu decode truc tiep (sdk/font_writeback xac nhan), khong
    qua font_check.scan_source — scan_source danh dau moi TMP PB3 la
    unconfirmed (khong typetree/script) nen ke hoach tu no toan route
    "none" [DA DO THAT: 43/43 unconfirmed, atlas 0/0]. Font object lay
    tu font_check.scan_serialized_file (cmap doc duoc binh thuong).
    """
    from sdk import font_check as fc
    from sdk import font_writeback as fw

    plan = []
    warnings = []
    files_scanned = 0
    tmp_cands = []
    all_font_names = set()
    for display, _data, sf, _slice, _full in _iter_tmp_sf(path):
        files_scanned += 1
        try:
            scan = fc.scan_serialized_file(sf, display)
        except Exception as exc:  # noqa: BLE001
            warnings.append("%s: quet font loi: %s" % (display, exc))
            continue
        warnings.extend(scan.get("warnings") or [])
        fonts = [f for f in scan.get("fonts") or []
                 if f.get("kind") == "unity_font"]
        for f in fonts:
            all_font_names.add(f.get("name") or "")
            all_font_names.add(f.get("family") or "")
            classify = {"kind": "font", "name": f.get("name"),
                        "source_letters": f.get("cmap_letters"),
                        "source_marks": f.get("cmap_marks")}
            route = fw.classify_case(classify)
            plan.append({"kind": "font", "name": f.get("name"),
                         "path_id": f.get("path_id"), "source": display,
                         "confirmed": True, "population": None,
                         "atlas": None,
                         "missing_total": f.get("missing_total"),
                         "fallback_count": None, "source_present": True,
                         "source_cmap_known": True,
                         "source_letters": f.get("cmap_letters"),
                         "source_marks": f.get("cmap_marks"),
                         "route": route["route"],
                         "reason": route["reason_vi"],
                         "action": route["action"], "notes": []})
        try:
            _ids, names = fw._confirmed_tmp_ids(sf)
        except Exception as exc:  # noqa: BLE001
            warnings.append("%s: xac nhan TMP loi: %s" % (display, exc))
            continue
        for pid, nm in names.items():
            try:
                raw = sf.object_bytes(pid)
            except Exception as exc:  # noqa: BLE001
                warnings.append("TMP #%s: khong doc duoc bytes: %s"
                                % (pid, exc))
                continue
            tmp_cands.append((display, pid, nm, raw, sf.endian, fonts))
    # Lap muc TMP SAU khi quet het file — can biet ten font o MOI file
    # de ghi chu khi font nguon chi co o file khac.
    for display, pid, nm, raw, endian, fonts in tmp_cands:
        try:
            plan.append(_plan_tmp_entry_fw(nm, pid, display, raw, endian,
                                           fonts, all_font_names))
        except Exception as exc:  # noqa: BLE001
            warnings.append("TMP %s: lap ke hoach loi: %s" % (nm, exc))
    routes = {}
    for p in plan:
        routes[p["route"]] = routes.get(p["route"], 0) + 1
    out = "/tmp/font_writeback_plan.json"
    payload = {"schema": "font_writeback_plan/1", "source": str(path),
               "plan": plan, "routes": routes,
               "files_scanned": files_scanned, "warnings": warnings}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    payload.update({"ok": True, "dest": out,
                    "filename": "font_writeback_plan.json"})
    return json.dumps(payload, ensure_ascii=False)


def _font_writeback_run(path, asset_name, mode, extra_cps, dry_run,
                        donor_path, donor_tmp):
    """Mode co dich: tim asset trong file game roi goi sdk/font_writeback.

    dry_run=True (mac dinh): tinh ban ghi trong RAM roi CHI tra bao cao
    (/tmp/font_writeback.json), khong ghi file game. dry_run=False: ghi
    ban .assets da sua (+ .resS da sua neu co) ra /tmp/font_writeback.zip
    de tai ve — file goc KHONG bi dong vao; nguoi dung tu chen lai vao
    IPA (tool khong ky lai IPA).
    """
    from sdk import font as _fontmod
    from sdk import font_writeback as fw

    donor = _donor_font_bytes(donor_path)
    found = None
    fallback = None
    scanned = []
    for display, data, sf, ress_slice, ress_full in _iter_tmp_sf(path):
        if mode == "font_merge":
            try:
                cands = list(_fontmod.list_unity_font_objects(sf))
            except Exception:
                continue
        else:
            _ids, names = fw._confirmed_tmp_ids(sf)
            cands = [(pid, nm) for pid, nm in names.items()]
        for pid, nm in cands:
            scanned.append(nm)
            kind = _match_kind(nm, asset_name, len(scanned) - 1)
            if kind == 2:
                found = ("font" if mode == "font_merge" else "tmp",
                         display, data, sf, ress_slice, ress_full, pid, nm)
                break
            if kind == 1 and fallback is None:
                fallback = ("font" if mode == "font_merge" else "tmp",
                            display, data, sf, ress_slice, ress_full,
                            pid, nm)
        if found:
            break
    if found is None:
        found = fallback
    if found is None:
        raise ValueError("khong tim thay asset %r (da quet: %s)"
                       % (asset_name, ", ".join(scanned) or "khong co"))
    kind, display, data, sf, ress_slice, ress_full, pid, nm = found
    rep = None
    extra_files = {}
    if mode == "tmp_sdf":
        rep = fw.writeback_tmp_sdf(data, pid, extra_cps, ress_slice)
        if not dry_run:
            ress = ress_full()
            if ress is None:
                raise ValueError("khong co file .resS de patch atlas")
            ap = rep["atlas_patch"]
            new_atlas = rep["new_atlas_bytes"]
            if len(new_atlas) != ap["size"]:
                raise ValueError("atlas moi %d byte, khac patch %d byte"
                               % (len(new_atlas), ap["size"]))
            patched_ress = bytearray(ress)
            patched_ress[ap["offset"]:ap["offset"] + ap["size"]] = new_atlas
            extra_files[os.path.basename(display) + ".resS"] = bytes(
                patched_ress)
    elif mode == "font_merge":
        rep = fw.writeback_font_merge(data, pid, donor, extra=extra_cps)
    elif mode == "fallback_clone":
        ress = ress_full()
        if ress is None:
            raise ValueError("khong co file .resS (clone can append atlas)")
        rep = fw.clone_fallback_asset(data, pid, donor, ress,
                                      extra_cps or None)
        extra_files[os.path.basename(display) + ".resS"] = \
            rep["new_ress_bytes"]
    elif mode == "link":
        if not donor_tmp:
            raise ValueError("mode link can ten TMP donor (donor_tmp)")
        _ids, names = fw._confirmed_tmp_ids(sf)
        donor_pid = None
        for cand_pid, cand_nm in names.items():
            if cand_nm == donor_tmp or donor_tmp in cand_nm:
                donor_pid = cand_pid
                break
        if donor_pid is None:
            raise ValueError("khong tim thay TMP donor %r trong cung file"
                           % donor_tmp)
        rep = fw.link_fallback(data, pid, donor_pid)
    elif mode == "global_link":
        rep = fw.link_global_fallback(data, pid)
    else:  # noqa: SIM108 - khong toi duoc (mode da kiem o tren)
        raise ValueError("mode %r khong ho tro" % mode)
    report = {k: v for k, v in rep.items()
              if k not in ("patched_sf_bytes", "new_atlas_bytes",
                           "new_ress_bytes")}
    report.update({"mode": mode, "asset": nm, "path_id": pid,
                   "source": display, "dry_run": bool(dry_run),
                   "extra_codepoints": list(extra_cps)})
    changed = rep.get("status") != "already_linked"
    out = "/tmp/font_writeback.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    if dry_run or not changed:
        return json.dumps(dict(report, ok=True, dest=out,
                               filename="font_writeback.json"),
                          ensure_ascii=False)
    zpath = "/tmp/font_writeback.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(os.path.basename(display), rep["patched_sf_bytes"])
        for name, blob in extra_files.items():
            zf.writestr(name, blob)
        zf.writestr("font_writeback.json",
                    json.dumps(report, ensure_ascii=False, indent=1))
    return json.dumps(dict(report, ok=True, dest=zpath,
                           filename="font_writeback.zip"),
                      ensure_ascii=False)


def font_writeback(path, asset_name=None, mode="auto", extra="",
                   dry_run=True, donor_path=None, donor_tmp=None):
    """Ghi nguoc font tieng Viet vao file game (F2, THU NGHIEM).

    path: file game (IPA/.assets/.unity3d/thu muc). asset_name: ten TMP
    asset / Font object (vd "Aniara_Light SDF", "Perfect DOS VGA 437";
    de trong = asset dau tien) hoac so thu tu. mode: auto (chi lap ke
    hoach, khong ghi) / tmp_sdf (TMP Static SDF: tai tao atlas + bang
    glyph roi ghi) / font_merge (Font object TTF/OTF: gop glyph roi
    chen) / fallback_clone (nhan ban TMP fallback tu donor Be Vietnam
    Pro) / link (them 1 TMP vao bang fallback cua TMP dich) /
    global_link (them TMP dich vao danh sach fallback toan cuc TMP
    Settings). extra: ky tu can them (vd "aàáâ..." hoac "U+1EA0").
    dry_run=True (mac dinh): chi tinh + bao cao, khong ghi file game.
    dry_run=False: ghi ban .assets/.resS da sua ra /tmp/font_writeback
    .zip de tai ve (file goc khong bi dong vao). donor_tmp: ten TMP
    donor cho mode link. Ap dung gate + verify trong sdk truoc khi tra.
    """
    try:
        _check_size(path)
        extra_cps = _parse_extra_codepoints(extra)
        mode = str(mode or "auto").strip().lower()
        if mode not in ("auto", "tmp_sdf", "font_merge", "fallback_clone",
                        "link", "global_link"):
            return _err(ValueError(
                "mode %r khong ho tro (auto/tmp_sdf/font_merge/"
                "fallback_clone/link/global_link)" % mode))
        if isinstance(dry_run, str):
            dry_run = dry_run.strip().lower() in ("1", "true", "yes", "on")
        if mode == "auto":
            return _font_writeback_plan(path)
        return _font_writeback_run(path, asset_name, mode, extra_cps,
                                   dry_run, donor_path, donor_tmp)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# Cache lần liệt kê font gần nhất (phần D): nút "Dùng chung 1 font"
# dùng lại kết quả của nút "Liệt kê font của game" thay vì quét lại cả
# file/IPA. Khóa = (đường dẫn, kích thước, mtime); khác file thì quét lại.
_LAST_FONT_SCAN = {"path": None, "size": None, "mtime": None, "res": None}


def _font_scan_cached(path):
    """Liệt kê font, dùng lại kết quả lần trước nếu đúng cùng file."""
    from sdk import font_list as fl

    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
    except OSError:
        key = None
    if (key is not None and _LAST_FONT_SCAN["res"] is not None
            and (_LAST_FONT_SCAN["path"], _LAST_FONT_SCAN["size"],
                 _LAST_FONT_SCAN["mtime"]) == key):
        return _LAST_FONT_SCAN["res"], True
    res = fl.scan_source(path)
    if key is not None:
        _LAST_FONT_SCAN.update({"path": key[0], "size": key[1],
                               "mtime": key[2], "res": res})
    return res, False


def _font_scan_public(res):
    """Bản scan lược bỏ bytes font (không JSON được) để trả về JS."""
    return {k: v for k, v in res.items() if k not in ("font_bytes",)}


def font_list(path):
    """Liệt kê font của game + kiểm từng font đủ/thiếu dấu tiếng Việt (phần D).

    Quét TTF/OTF/TTC rời, Font object nhúng (class 128) và TMP_FontAsset
    trong file/IPA/.assets/.unity3d đã chọn; mỗi font báo đủ bao nhiêu
    trên 142 mục + chữ/dấu nào thiếu. Chỉ đọc; ghi danh sách ra
    /tmp/font_list.json để JS tải về.
    """
    try:
        from sdk import font_list as fl  # noqa: F401 - giữ import rõ ràng

        _check_size(path)
        res, reused = _font_scan_cached(path)
        out = "/tmp/font_list.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(_font_scan_public(res), fh, ensure_ascii=False, indent=1)
        pub = _font_scan_public(res)
        pub.update({"ok": True, "dest": out, "filename": "font_list.json",
                    "scan_reused": reused})
        return json.dumps(pub, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_check_3step(path):
    """Kiem tra font 3 buoc cua anh Duy (sdk/font_check) cho ca game.

    Buoc 1 tim Font object + TMP_FontAsset; Buoc 2 check atlas SDF
    (m_CharacterTable vs 134 chu + 8 dau); Buoc 3 check cmap TTF nguon
    (m_FontData) + AtlasPopulationMode + fallback. Chi doc; ghi bao cao
    ra /tmp/font_check_3step.json de JS tai ve.
    """
    try:
        from sdk import font_check as fc

        _check_size(path)
        res = fc.scan_source(path)
        res["report"] = fc.build_gate_report(res["fonts"]
                                             + res["tmp_assets"])
        out = "/tmp/font_check_3step.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)
        res.update({"ok": True, "dest": out,
                    "filename": "font_check_3step.json"})
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_unify(path, donor_path):
    """"Dùng chung 1 font": gom mọi font game về Be Vietnam Pro (phần D).

    Dùng lại kết quả liệt kê (cache) rồi lập kế hoạch + làm thật phần
    web làm được: gộp glyph tiếng Việt còn thiếu vào từng font TTF/OTF
    không CID rời (sdk/font.merge_vietnamese). Trả file font_unified.zip
    để JS tải về: các *.vi.ttf / *.vi.otf đã gộp + font_unify_plan.json
    + tmp_fallback_plan.json (nếu có TMP) + BeVietnamPro-Regular.ttf
    (đủ 142/142, OFL). Font gộp lỗi (vd OTF CID) nằm trong danh sách
    `merged` với trường `error`, không có trong zip. TMP atlas SDF,
    font TTC/nhúng: chỉ có kế hoạch + đường Editor trong plan (xem
    LIMITS của sdk/font_list).
    """
    try:
        from sdk import font_list as fl

        _check_size(path)
        res, reused = _font_scan_cached(path)
        plan = fl.build_unify_plan(res)
        donor = open(donor_path, "rb").read()
        merged = fl.merge_standalone_ttfs(res, donor)
        plan_out = "/tmp/font_unify_plan.json"
        with open(plan_out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, ensure_ascii=False, indent=1)
        zpath = "/tmp/font_unified.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("font_unify_plan.json",
                        json.dumps(plan, ensure_ascii=False, indent=1))
            if plan.get("fallback_plan"):
                zf.writestr("tmp_fallback_plan.json",
                            json.dumps(plan["fallback_plan"],
                                     ensure_ascii=False, indent=1))
            for m in merged:
                if "data" not in m:
                    continue  # font gop loi (vd OTF CID): chi bao loi
                zf.writestr(m["output"], m["data"])
            zf.writestr(fl.DONOR_FILE, donor)
        return json.dumps({
            "ok": True, "dest": zpath, "filename": "font_unified.zip",
            "plan_dest": plan_out, "scan_reused": reused,
            "plan": plan,
            "merged": [{k: v for k, v in m.items() if k != "data"}
                       for m in merged],
            "summary": res["summary"],
            "warnings": list(res["warnings"] or []),
            "files_scanned": list(res["files_scanned"] or []),
            "bundle_nodes": int(res["bundle_nodes"]),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def diagnose(path):
    """Chẩn đoán chỉ đọc: inventory + hex dump + quét strings (ASCII, v1).

    Giới hạn v1: strings chỉ quét ASCII in được (không quét UTF-16);
    chỉ quét 1MB đầu để khỏi nặng RAM tab trình duyệt.
    """
    try:
        from core.limits import Limits
        from sdk import containers, discovery as disc

        _check_size(path)
        data = open(path, "rb").read()
        kind = containers.detect_container_kind(data)
        vfs = containers.open_vfs_bytes(data, source_name=os.path.basename(path))
        inv = disc.inventory(vfs, Limits())
        entries = [{"path": e.path, "size": int(e.size), "kind": e.kind}
                   for e in inv]
        head = data[:512]
        hex_lines = []
        for off in range(0, len(head), 16):
            chunk = head[off:off + 16]
            hex_lines.append("%08x  %s" % (
                off, " ".join("%02x" % b for b in chunk)))
        strings = []
        buf = bytearray()
        start = 0
        for i, b in enumerate(data[:STRINGS_SCAN_BYTES]):
            if 32 <= b < 127:
                if not buf:
                    start = i
                buf.append(b)
            else:
                if len(buf) >= 4:
                    strings.append({"offset": start,
                                    "text": buf.decode("ascii")})
                    if len(strings) >= MAX_STRINGS:
                        break
                buf = bytearray()
        return json.dumps({
            "ok": True, "kind": kind, "size": len(data),
            "entries": entries, "hex": hex_lines, "strings": strings,
            "strings_note": "v1 chỉ quét chuỗi ASCII (tối thiểu 4 ký tự), "
                            "1MB đầu, tối đa %d chuỗi." % MAX_STRINGS,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def format_probe(path):
    """Format probe cho engine lạ (mục phân tích C8): probe 1 file bất kỳ
    và trả báo cáo có cấu trúc (sdk.format_probe.probe_data).

    File hoàn toàn lạ KHÔNG phải lỗi: probe trả {"ok": True, "report":
    {...engine "unknown"...}} — chỉ lỗi đọc file / lỗi hệ thống mới trả
    ok False. Mức hỗ trợ thật lấy từ plugin đang bật trong phiên
    (plugin engine nào chưa bật thì thao tác của nó rơi xuống
    "không hỗ trợ" kèm lý do).
    """
    try:
        from sdk import format_probe as fp

        _check_size(path)
        data = open(path, "rb").read()
        active = set()
        try:
            active = {r.plugin_id for r in _reg.active()} if _reg else set()
        except Exception:
            active = set()
        report = fp.probe_data(data, os.path.basename(path),
                               active_plugins=active)
        return json.dumps({"ok": True, "report": report}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _pak_tail_bytes(zf, info, tail_n=4096):
    """Byte cuối của 1 entry .pak trong ZIP (để tìm magic footer Unreal).

    Entry nhỏ (≤8MB): đọc hết rồi cắt đuôi. Entry lớn chỉ đọc đuôi khi
    lưu thô (stored) — tính vị trí data qua local header; entry nén lớn
    thì trả b"" (không đọc cả entry chỉ để lấy đuôi).
    """
    try:
        if info.file_size <= 8 * 1024 * 1024:
            with zf.open(info) as fh:
                return fh.read()[-tail_n:]
        if info.compress_type != zipfile.ZIP_STORED:
            return b""
        with open(zf.filename, "rb") as fh:
            fh.seek(info.header_offset)
            lh = fh.read(30)
            if len(lh) < 30 or lh[:4] != b"PK\x03\x04":
                return b""
            nlen, elen = struct.unpack_from("<HH", lh, 26)
            data_start = info.header_offset + 30 + nlen + elen
            data_end = data_start + info.compress_size
            fh.seek(max(data_start, data_end - tail_n))
            return fh.read(tail_n)
    except (OSError, KeyError, struct.error, ValueError):
        return b""


def detect_game_engine(path):
    """Nhận diện engine game cho tab Tổng quan (v65, mở rộng v67): mở
    file IPA/ZIP, liệt kê tên entry (chỉ đọc central directory, không
    giải nén) rồi đọc lười vài byte đầu của entry ứng viên
    (global-metadata.dat, .locres, .pak, .pck, .xnb, game.love, file
    thực thi .app) + byte cuối của .pak để
    sdk.discovery.detect_game_engine chấm đa tín hiệu.

    Không đủ tín hiệu thì engine "unknown" kèm lý do (ok True) — chỉ
    lỗi đọc file / lỗi hệ thống mới trả ok False.
    """
    try:
        from sdk import discovery as disc

        _check_size(path)
        notes = []
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            # File đơn lẻ (.assets/.unity3d/...): chỉ có tên + byte đầu
            with open(path, "rb") as fh:
                head = fh.read(16)
            result = disc.detect_game_engine(
                [os.path.basename(path)],
                {os.path.basename(path): head})
            return json.dumps({"ok": True, "engine": result, "notes": notes},
                              ensure_ascii=False)
        with zf:
            names = zf.namelist()
            lower = [n.lower() for n in names]
            heads = {}
            footers = {}

            def _first(pred):
                for i, n in enumerate(lower):
                    if pred(n):
                        return names[i]
                return None

            def _read_head(name, nbytes):
                if name is None:
                    return
                try:
                    with zf.open(name) as fh:
                        heads[name] = fh.read(nbytes)
                except (KeyError, OSError, RuntimeError):
                    pass

            gmd = _first(lambda n: n.endswith("global-metadata.dat"))
            _read_head(gmd, 4)
            locres = _first(lambda n: n.endswith(".locres"))
            _read_head(locres, 16)
            pck = _first(lambda n: n.endswith(".pck"))
            _read_head(pck, 4)
            xnb = _first(lambda n: n.endswith(".xnb"))
            _read_head(xnb, 6)
            love = _first(lambda n: n.endswith("game.love"))
            _read_head(love, 4)
            paks = [n for n in names if n.lower().endswith(".pak")][:3]
            for pak in paks:
                _read_head(pak, 128)
                try:
                    info = zf.getinfo(pak)
                except KeyError:
                    continue
                tail = _pak_tail_bytes(zf, info)
                if tail:
                    footers[pak] = tail
                elif info.file_size > 8 * 1024 * 1024:
                    notes.append("không đọc được footer .pak %s (entry "
                                 "nén, quá lớn)" % pak)
            # File thực thi .app: tên lấy từ Info.plist, fallback = tên .app
            app_prefix = None
            for n in names:
                m = re.match(r"^(payload/[^/]+\.app/|[^/]+\.app/)", n, re.I)
                if m:
                    app_prefix = m.group(1)
                    break
            if app_prefix:
                exe = None
                try:
                    pl = plistlib.loads(zf.read(app_prefix + "Info.plist"))
                    exe = pl.get("CFBundleExecutable")
                except Exception:
                    exe = None
                if not exe:
                    exe = app_prefix.rstrip("/").split("/")[-1][:-4]
                exe_path = app_prefix + exe
                if exe_path in names:
                    _read_head(exe_path, 4)
            result = disc.detect_game_engine(names, heads, footers)
        return json.dumps({"ok": True, "engine": result, "notes": notes},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def font_donor_file():
    """Đưa font đi kèm ra /tmp để JS tải về (chế độ 4: thay bằng font có sẵn)."""
    try:
        src = "/pb3web/fonts/BeVietnamPro-Regular.ttf"
        dest = "/tmp/BeVietnamPro-Regular.ttf"
        shutil.copyfile(src, dest)
        return json.dumps({"ok": True, "dest": dest,
                           "size": os.path.getsize(dest)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def file_sha256(path):
    """SHA-256 của 1 file trong FS ảo (để JS đối chiếu sau giải nén)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return json.dumps({"ok": True, "sha256": h.hexdigest(),
                           "size": os.path.getsize(path)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# MOD — menu [1] iSH (lệnh `mod` / `unity patch`).
# ======================================================================
def _mod_output_name(input_path, kind, label):
    """Tên file output cho 1 đơn vị đã patch (như
    sdk.batch._sdk_default_output_rel): <Game>/<tên đơn vị nhỏ nhất>.
    Không bao giờ là cả IPA — đơn vị trong container/IPA chỉ xuất ra
    bundle/assets đó, người dùng tự đóng gói lại nếu cần (giới hạn 6.6)."""
    from sdk.batch import _sdk_default_output_rel

    rel = _sdk_default_output_rel(input_path, kind, label)
    return os.path.basename(rel)


def mod_patch(path, edits_json, allow_write=False):
    """Patch TextAsset trong file Unity (bundle .unity3d, .assets độc lập,
    hoặc entry trong container/IPA qua vpath 'a::b').

    edits_json: JSON list [{"object": path_id, "old": "...", "new": "...",
    "entry": vpath (tùy chọn), "mode": "PATCH_INPLACE"|"REBUILD_SECTION"}].
    Nhiều edit trên cùng một đơn vị được áp nối tiếp trên bytes đã patch.

    MẶC ĐỊNH dry-run (allow_write=False): chỉ tính toán + verify, KHÔNG
    ghi file nào. allow_write=True (người dùng bấm nút ghi rõ ràng, tương
    đương --allow-write) thì ghi từng đơn vị đã patch ra /tmp/mod_out/
    (không ghi đè file gốc); 1 file -> dest, nhiều file -> /tmp/mod.zip.
    REBUILD_ALL không hỗ trợ trên web (cần maturity M4 + xác nhận).
    """
    try:
        from sdk import unity as u
        from sdk.batch import _sdk_resolve_vpath

        _check_size(path)
        edits = json.loads(edits_json)
        if not isinstance(edits, list) or not edits:
            raise ValueError("edits phải là JSON list không rỗng.")
        data = open(path, "rb").read()
        orig_sha = hashlib.sha256(data).hexdigest()
        backend = u.UnityBackend("NATIVE")
        limits = _app.limits
        from_container = u.detect_bundle(data) is None
        # Đơn vị đã patch theo khóa entry (nhiều edit cùng đơn vị nối tiếp).
        units = {}   # key -> {"kind", "req_entry", "label", "bytes"}
        results = []
        for idx, edit in enumerate(edits):
            if not isinstance(edit, dict):
                raise ValueError("edit #%d phải là object." % (idx + 1))
            try:
                obj_id = int(edit["object"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("edit #%d thiếu object (path_id số)."
                                 % (idx + 1))
            old = edit.get("old")
            new = edit.get("new")
            if old is None or new is None:
                raise ValueError("edit #%d thiếu old/new." % (idx + 1))
            mode = (edit.get("mode") or "PATCH_INPLACE").upper()
            if mode not in ("PATCH_INPLACE", "REBUILD_SECTION"):
                raise ValueError(
                    "edit #%d: mode %r không hỗ trợ trên web "
                    "(chỉ PATCH_INPLACE/REBUILD_SECTION; REBUILD_ALL cần "
                    "maturity M4, chưa có)." % (idx + 1, mode))
            entry = edit.get("entry") or None
            key = entry or "<file>"
            if key not in units:
                kind, unit_data, req_entry, label = _sdk_resolve_vpath(
                    data, entry, backend, limits)
                units[key] = {"kind": kind, "req_entry": req_entry,
                              "label": label, "bytes": unit_data}
            unit = units[key]
            req = u.PatchRequest(
                data=unit["bytes"], kind=unit["kind"],
                entry=unit["req_entry"], path_id=obj_id,
                old_text=str(old), new_text=str(new), write_mode=mode,
                backend=backend, temp_dir=str(_app.paths.temp),
                from_container=bool(entry) and from_container)
            res = u.apply_patch(req)
            unit["bytes"] = res.output_bytes
            rep = res.report
            results.append({
                "entry": entry, "object": obj_id,
                "target": rep.target, "unit": res.unit,
                "write_mode": rep.write_mode_used,
                "occurrences": int(rep.occurrences),
                "old_script_len": int(rep.old_script_len),
                "new_script_len": int(rep.new_script_len),
                "size_before": int(rep.size_before),
                "size_after": int(rep.size_after),
                "verified": bool(res.verified),
                "verify": list(res.verify_details or []),
                "warnings": list(rep.warnings or []) + list(rep.backend_decisions or []),
            })
            _emit("mod", idx + 1, len(edits))
        out = {"ok": True, "dry_run": not bool(allow_write),
               "patched": len(results), "edits": results,
               "source_sha256": orig_sha,
               "outputs": [], "dest": None, "zip_path": None,
               "note": "Output là đơn vị nhỏ nhất đã patch (bundle/.assets), "
                       "KHÔNG phải IPA đóng gói lại — bản web không đóng gói "
                       "lại IPA (giới hạn 6.6 của PB3)."}
        if allow_write:
            out_dir = "/tmp/mod_out"
            shutil.rmtree(out_dir, ignore_errors=True)
            os.makedirs(out_dir, exist_ok=True)
            names = []
            for key, unit in units.items():
                name = _mod_output_name(path, unit["kind"], unit["label"])
                dest = os.path.join(out_dir, name)
                # Tránh ghi đè lẫn nhau khi 2 đơn vị trùng tên file.
                n = 1
                base, ext = os.path.splitext(name)
                while os.path.exists(dest):
                    n += 1
                    dest = os.path.join(out_dir, "%s_%d%s" % (base, n, ext))
                open(dest, "wb").write(unit["bytes"])
                names.append(os.path.basename(dest))
            out["outputs"] = names
            if len(names) == 1:
                out["dest"] = os.path.join(out_dir, names[0])
            else:
                zpath = "/tmp/mod.zip"
                if os.path.exists(zpath):
                    os.remove(zpath)
                with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                    for name in names:
                        zf.write(os.path.join(out_dir, name), name)
                out["zip_path"] = zpath
                out["zip_bytes"] = os.path.getsize(zpath)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# PLAN — mục 8 (sdk/plan.py): planner/executor tách rời cho Mod.
# ======================================================================
def plan_build(path, translations_json, entry=None, mode="REBUILD_SECTION",
               rebuild_all=False, asset_names_json=None,
               mb_index_json=None):
    """Lập plan JSON từ bundle/assets: quét TextAsset JSON, đối key trong
    translations (key -> giá trị mới, như đầu ra tab Dịch) thành entry
    mỗi asset. Không ghi file nào. mb_index_json (tùy chọn):
    {collection: {key: m_Id}} từ trích xuất IPA đầy đủ — để plan chen
    key StringTable thiếu + đặt tên key đúng khi chỉ lập plan trên 1
    file .assets lẻ."""
    try:
        from sdk import plan as plan_sdk

        _check_size(path)
        try:
            translations = json.loads(translations_json)
        except ValueError:
            raise ValueError("translations phải là JSON object "
                             "(key -> giá trị mới).")
        asset_names = None
        if asset_names_json:
            asset_names = json.loads(asset_names_json)
            if not isinstance(asset_names, list):
                raise ValueError("asset_names phải là JSON list tên asset.")
        mb_index = None
        if mb_index_json:
            try:
                mb_index = json.loads(mb_index_json)
            except ValueError:
                raise ValueError("mb_index_json không phải JSON hợp lệ.")
            if not isinstance(mb_index, dict):
                raise ValueError("mb_index_json phải là JSON object "
                                 "{collection: {key: m_Id}}.")
        data = open(path, "rb").read()
        plan = plan_sdk.build_plan(
            data, translations, source_bundle=os.path.basename(path),
            entry=entry or None, mode=mode or "REBUILD_SECTION",
            rebuild_all=bool(rebuild_all), asset_names=asset_names,
            mb_index=mb_index)
        return json.dumps({"ok": True, "plan": plan}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def plan_apply(plan_json, path, dry_run=True, checkpoint_json=None,
               stop_after=None):
    """Executor: áp plan lên file gốc. Mặc định dry-run (chỉ tính + verify,
    không ghi). dry_run=False thì ghi đơn vị đã patch ra /tmp/plan_out/
    (1 file -> dest, nhiều file -> /tmp/plan.zip) + backup đơn vị gốc vào
    /tmp/plan_backup/<plan_id>/ để undo. checkpoint_json để resume;
    stop_after=N để dừng sau N entry (trả checkpoint)."""
    try:
        from sdk import plan as plan_sdk

        _check_size(path)
        try:
            plan = json.loads(plan_json)
        except ValueError:
            raise ValueError("plan_json không phải JSON hợp lệ.")
        data = open(path, "rb").read()
        # Pyodide 314: JS null qua bien thanh JsNull (chi undefined moi
        # thanh None) nen KHONG duoc kiem `is not None` — JsNull la falsy
        # nen dung truthiness.
        stop_n = int(stop_after) if stop_after else None
        res = plan_sdk.apply_plan(
            data, plan, dry_run=bool(dry_run),
            checkpoint_json=checkpoint_json or None,
            stop_after=stop_n)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def plan_undo(plan_json, path):
    """Hoàn tác plan: khôi phục đơn vị về đúng before_sha (ưu tiên bản sao
    lưu trong phiên worker, nếu không còn thì patch ngược rồi verify).
    Đầu vào phải là file ĐÃ ÁP plan (hoặc file gốc nếu dùng backup)."""
    try:
        from sdk import plan as plan_sdk

        _check_size(path)
        try:
            plan = json.loads(plan_json)
        except ValueError:
            raise ValueError("plan_json không phải JSON hợp lệ.")
        data = open(path, "rb").read()
        res = plan_sdk.undo_plan(data, plan)
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def rebuild_apply(path, translations_json, provenance_json=None,
                  choices_json=None, dry_run=True, confirm=False,
                  gate_verdict_json=None, source_locale="en",
                  mode="REBUILD_SECTION", mb_index_json=None):
    """(E) Ép bản dịch lại vào file gốc [bước 5, P0-1].

    Preview và apply là CÙNG 1 op với cờ dry_run (tiền lệ plan_apply /
    mod_patch): dry_run=True (mặc định) chỉ tính toán + verify, không
    ghi file nào; dry_run=False BẮT BUỘC confirm=True, nếu không trả
    lỗi và không ghi gì. Nhóm bản dịch theo provenance (merge sidecar
    ưu tiên, translation sidecar fallback, không provenance thì quét
    tất cả), chọn bản thắng cho xung đột, 1 plan mỗi đơn vị, áp bằng
    executor hiện có + đường rebuild streaming cho bundle .unity3d độc
    lập lớn. Ghi TextAsset JSON dạng dict + TextAsset CSV (M3: file CSV
    độc lập trong ZIP + TextAsset CSV trong bundle/.assets, ghi đúng ô
    theo note vị trí) + MonoBehaviour/StringTable
    (M1: bảng I2 byte-walk + StringTable có type tree, REBUILD_SECTION;
    mb-write-rest: thêm bảng I2 có type tree, MonoBehaviour generic
    scan-path, PATCH_INPLACE khi vừa); key MonoBehaviour không ép được
    vào unmatched với reason rõ ràng (unchanged khi bản dịch bằng gốc,
    string_table_other_unit khi bảng ST ở đơn vị khác trong IPA).
    source_locale: cột ngôn ngữ gốc để ghi (mặc định "en"). mode: chế độ
    ghi mặc định ("REBUILD_SECTION" mặc định; "PATCH_INPLACE" thì entry
    nào vừa sẽ nối tại chỗ, dài hơn thì leo thang REBUILD_SECTION).
    mb_index_json (tùy chọn): {collection: {key: m_Id}} từ trích xuất
    IPA đầy đủ — để phân loại key StringTable ở đơn vị khác khi chỉ ép
    trên 1 file .assets lẻ. File gốc KHÔNG bị ghi đè; output là đơn vị
    nhỏ nhất đã patch, KHÔNG phải IPA đóng gói lại.
    Tiến độ thật kind "rebuild" qua _emit. Response: units[]/totals/
    unmatched[]/conflicts[]/refused[]/outputs/dest/zip_path + note.
    gate_verdict_json (P0-2, tùy chọn): verdict của cổng kiểm tra
    (bước 6) mà UI bắt buộc phải có trước khi ghi. Khi được truyền,
    verdict phải là PASS ở chế độ pre_write, đúng file gốc (sha) và
    đúng bản dịch (translations_sha256) — nếu không thì từ chối ghi
    với E_CONFIG_INVALID, không ghi gì. Không truyền thì giữ hành vi
    cũ (đường gọi bridge trực tiếp tự chịu trách nhiệm; UI luôn truyền).
    """
    try:
        from sdk import rebuild as rb

        _check_size(path)
        try:
            translations = json.loads(translations_json)
        except ValueError:
            raise ValueError("translations phải là JSON object "
                             "(key -> giá trị mới).")
        provenance = None
        if provenance_json:
            try:
                provenance = json.loads(provenance_json)
            except ValueError:
                raise ValueError("provenance_json không phải JSON hợp lệ.")
        choices = None
        if choices_json:
            try:
                choices = json.loads(choices_json)
            except ValueError:
                raise ValueError("choices_json không phải JSON hợp lệ.")
        mb_index = None
        if mb_index_json:
            try:
                mb_index = json.loads(mb_index_json)
            except ValueError:
                raise ValueError("mb_index_json không phải JSON hợp lệ.")
            if not isinstance(mb_index, dict):
                raise ValueError("mb_index_json phải là JSON object "
                                 "{collection: {key: m_Id}}.")
        # JsNull (Pyodide) là falsy — dùng truthiness như plan_apply.
        dry = bool(dry_run) if dry_run is not None else True
        # dry_run=False + confirm=False: rebuild.apply_rebuild sẽ từ chối
        # với E_CONFIG_INVALID, không ghi gì.
        # Gate-crash-fix: ước tính RAM tab trước khi ép — từ chối lịch sự
        # thay vì để Safari sập tab.
        _check_web_memory(path, "ép file")
        data = open(path, "rb").read()
        orig_sha = hashlib.sha256(data).hexdigest()
        # P0-2: verdict cổng (nếu UI truyền) phải PASS + đúng file + đúng
        # bản dịch thì mới ghi; FAIL/lệch thì từ chối trước khi ép.
        if gate_verdict_json and not dry:
            try:
                verdict = json.loads(gate_verdict_json)
            except ValueError:
                raise ValueError("gate_verdict_json không phải JSON hợp lệ.")
            if not isinstance(verdict, dict):
                raise ValueError("gate_verdict_json phải là JSON object.")
            from sdk import gate as _gate
            from core.errors import PB3Error as _PB3Error
            problems = []
            if verdict.get("verdict") != "PASS" or not verdict.get("delivery_allowed"):
                problems.append("cổng kiểm tra (bước 6) chưa ĐẠT")
            if verdict.get("mode") != "pre_write":
                problems.append("verdict cổng không phải chế độ pre_write")
            if verdict.get("source_sha256") != orig_sha:
                problems.append("verdict cổng không đúng file gốc đang chọn")
            if verdict.get("translations_sha256") != _gate.canonical_translations_sha(translations):
                problems.append("verdict cổng không đúng bản dịch đang ép")
            if problems:
                raise _PB3Error(
                    "E_CONFIG_INVALID",
                    "không ghi được: %s — chạy lại cổng kiểm tra (bước 6) cho đúng file + bản dịch này."
                    % "; ".join(problems), where="rebuild")

        def _pf(done, total):
            _emit("rebuild", done, total)

        res = rb.apply_rebuild(
            data, source_name=os.path.basename(path),
            translations=translations, provenance=provenance,
            choices=choices, dry_run=dry, confirm=bool(confirm),
            progress_fn=_pf, src_path=path,
            source_locale=source_locale or "en",
            mode=mode or "REBUILD_SECTION", mb_index=mb_index)
        # File gốc không đổi: xác minh sha sau khi chạy (mọi đường ghi
        # đều ra /tmp/rebuild_out, không bao giờ ghi đè nguồn).
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != orig_sha:
            raise ValueError("file gốc đã đổi trong lúc ép — từ chối kết quả.")
        res["source_sha256"] = orig_sha
        res["filename"] = None
        if res.get("dest"):
            res["filename"] = os.path.basename(res["dest"])
        elif res.get("zip_path"):
            res["filename"] = "rebuild-%s.zip" % (
                res["units"][0]["plan_id"] if res.get("units") else "out")
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def repack_ipa(path, units_json):
    """(M2) Dong goi lai IPA [buoc 5] — tu ket qua (E) da ap that.

    units_json: JSON list [{unit: vpath, output: ten file da ghi}] lay
    tu `units` trong ket qua cua rebuild_apply (dry_run=False). Doc
    tung don vi da patch trong /tmp/rebuild_out, thay vao dung zip
    entry trong file IPA goc (sdk/repack.py), kiem tra lai toan bo
    (zip toan ven, thu tu entry, cau truc Payload, Info.plist nguyen
    ven, entry da thay khop sha, entry khac giu nguyen) roi moi tra
    `dest` de worker gui file ve tai (nhu moi op co output). File goc
    KHONG bi ghi de; output ten "<game>-mod.ipa". Khong can verdict
    cong o day: noi dung da va da qua cong PASS luc ap (E); buoc nay
    chi kiem tra tinh toan ven cua viec dong goi lai.
    Loi toan cuc tra _err; kiem tra sau dong goi FAIL thi file loi da
    bi xoa va tra E_VERIFY_FAILED.
    """
    try:
        from sdk import repack as _repack

        _check_size(path)
        try:
            units = json.loads(units_json)
        except ValueError:
            raise ValueError("units_json khong phai JSON hop le "
                             "(can list [{unit, output}] tu ket qua "
                             "ap (E)).")
        data = open(path, "rb").read()
        orig_sha = hashlib.sha256(data).hexdigest()
        res = _repack.repack_from_rebuild_units(
            path, units, limits=None)
        # File goc khong doi: xac minh sha sau khi chay (nhu rebuild_apply).
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != orig_sha:
            raise ValueError("file goc da doi trong luc dong goi — "
                             "tu choi ket qua.")
        res["source_sha256"] = orig_sha
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def po_apply(path, po_vpath, translations_json, dry_run=True, confirm=False):
    """(caps-po) Ghi lại file .po trong IPA/ZIP — sửa msgstr rồi đóng gói lại.

    path: file nguồn (IPA/ZIP chứa .po, hoặc chính file .po).
    po_vpath: đường dẫn ảo tới .po, vd "Payload/.../scripts.zip::"
      "scripts/languages/vietnamese.po" (tối đa 4 đoạn, xem sdk/po_write).
    translations_json: JSON object {msgid: msgstr mới}.
    dry_run=True (mặc định): chỉ preview, không ghi file. dry_run=False
    + confirm=True mới ghi thật ra "<stem>-po-mod.<ext>" cạnh file gốc
    (KHÔNG ghi đè gốc). File gốc được xác minh sha trong sdk/po_write
    (đọc theo chunk, không nạp cả IPA lớn vào RAM). Không qua (E)/gate:
    .po là game không-Unity; verify 2 lớp của riêng nó (plugin.build +
    re-parse sau ghi) là đủ. Lỗi toàn cục trả _err.
    """
    try:
        from sdk import po_write as _po

        _check_size(path)
        try:
            translations = json.loads(translations_json)
        except ValueError:
            raise ValueError("translations_json không phải JSON object "
                             "(key -> giá trị mới).")
        res = _po.po_write(path, po_vpath, translations,
                           dry_run=bool(dry_run), confirm=bool(confirm))
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def delivery_gate(path, translations_json, source_json=None,
                  provenance_json=None, pressed_path=None,
                  allowlist_json=None, font_path=None, choices_json=None,
                  allow_partial=False, source_locale="en",
                  target_lang="vi"):
    """Cổng kiểm tra trước khi giao (bước 6, P0-2) — ĐIỂM VÀO DUY NHẤT.

    Nhận file gốc (+ file đã ép nếu có) + bản dịch + provenance (+ bản
    gốc đã trích xuất + danh sách cho phép + font hiển thị) và trả 1
    verdict PASS/FAIL duy nhất + chi tiết từng kiểm tra + danh sách lỗi
    bằng ngôn ngữ dễ hiểu. FAIL thì delivery_allowed=False: UI chặn
    giao file, không có đường giao khi cảnh báo. Op này CHỈ ĐỌC, không
    ghi file nào; file gốc được xác minh sha sau khi chạy (như
    rebuild_apply). Không có verdict thì không giao file.
    pressed_path: file đã ép cần kiểm (trống = kiểm trước khi ghi bằng
    mô phỏng trong RAM theo đúng đường (E)). font_path: file font sẽ
    hiển thị (trống = quét font trong file gốc; font đi kèm dùng đường
    dẫn /pb3web/fonts/BeVietnamPro-Regular.ttf). Tiến độ thật kind
    "gate" qua _emit.
    target_lang (T2): ngôn ngữ đích của bản dịch (mặc định "vi") — cổng
    font kiểm độ phủ theo bộ chữ của đích đó; đích khác vi mà không có
    font hiển thị thì FAIL "chưa kiểm được" (fail-closed, không tự cho
    là đạt).
    """
    try:
        from sdk import gate as _gate

        _check_size(path)
        try:
            translations = json.loads(translations_json)
        except ValueError:
            raise ValueError("translations phải là JSON object "
                             "(key -> giá trị mới).")
        source_texts = None
        if source_json:
            try:
                source_texts = json.loads(source_json)
            except ValueError:
                raise ValueError("source_json không phải JSON hợp lệ.")
        provenance = None
        if provenance_json:
            try:
                provenance = json.loads(provenance_json)
            except ValueError:
                raise ValueError("provenance_json không phải JSON hợp lệ.")
        choices = None
        if choices_json:
            try:
                choices = json.loads(choices_json)
            except ValueError:
                raise ValueError("choices_json không phải JSON hợp lệ.")
        allowlist = None
        if allowlist_json:
            try:
                allowlist = json.loads(allowlist_json)
            except ValueError:
                raise ValueError("allowlist_json không phải JSON hợp lệ.")
        # Gate-crash-fix: ước tính RAM tab trước khi mô phỏng nặng —
        # từ chối lịch sự thay vì để Safari sập tab (tính cả file đã ép
        # kèm theo nếu có).
        _pressed_n = 0
        if pressed_path:
            try:
                _pressed_n = os.path.getsize(str(pressed_path))
            except OSError:
                _pressed_n = 0
        _check_web_memory(path, "kiểm tra trước khi giao",
                          extra_bytes=_pressed_n)
        data = open(path, "rb").read()
        orig_sha = hashlib.sha256(data).hexdigest()
        # JsNull (Pyodide) là falsy — dùng truthiness như plan_apply.
        pressed_bytes = None
        pressed_name = None
        pressed_arg = None
        if pressed_path:
            pressed_arg = str(pressed_path)
            _check_size(pressed_arg)
            pressed_bytes = open(pressed_arg, "rb").read()
            pressed_name = os.path.basename(pressed_arg)
        # Font: font hiển thị do user chỉ định (nếu có) + quét font
        # trong file gốc (dùng cache của font_list như font_list()).
        # Font hiển thị quét theo bộ chữ của NGÔN NGỮ ĐÍCH (T2) để cổng
        # font kiểm đúng chữ đang dùng (vd ä/ö/ü của tiếng Đức).
        display_fonts = []
        font_error = None
        if font_path:
            try:
                from sdk.font import required_chars as _req_chars
                fdata = open(str(font_path), "rb").read()
                display_fonts.append(_gate.font_entry_from_bytes(
                    fdata, os.path.basename(str(font_path)),
                    required=_req_chars(target_lang or "vi")))
            except Exception as exc:  # noqa: BLE001 - thành lỗi kiểm tra font
                font_error = "không đọc được font đã chỉ định (%s): %s" % (
                    os.path.basename(str(font_path)), exc)
        fonts = []
        try:
            res, _reused = _font_scan_cached(path)
            fonts = _font_scan_public(res).get("fonts") or []
        except Exception as exc:  # noqa: BLE001 - thành lỗi kiểm tra font
            if font_error is None:
                font_error = "không quét được font trong file gốc: %s" % exc
        # Check font 3 buoc (sdk/font_check): bo sung du lieu atlas SDF
        # + cmap TTF nguon + population mode + fallback vao danh sach
        # font (chi thong tin trong bao cao 3 tang; loi thi bo qua,
        # verdict cua cong khong doi).
        font_check_report = None
        try:
            from sdk import font_check as _fc
            fonts, font_check_report = _fc.enrich_fonts(path, fonts)
        except Exception:  # noqa: BLE001 - best effort
            font_check_report = None

        def _pf(done, total):
            _emit("gate", done, total)

        res = _gate.run_gate(
            data, source_name=os.path.basename(path),
            translations=translations, source_texts=source_texts,
            provenance=provenance, choices=choices,
            pressed_bytes=pressed_bytes, pressed_name=pressed_name,
            allowlist=allowlist, fonts=fonts,
            display_fonts=display_fonts, font_error=font_error,
            progress_fn=_pf, src_path=path, pressed_path=pressed_arg,
            allow_partial=bool(allow_partial),
            source_locale=source_locale or "en",
            target_lang=target_lang or "vi",
            font_check=font_check_report)
        # File gốc không đổi: xác minh sha sau khi chạy (cổng chỉ đọc).
        if hashlib.sha256(open(path, "rb").read()).hexdigest() != orig_sha:
            raise ValueError("file gốc đã đổi trong lúc kiểm — từ chối kết quả.")
        return json.dumps(res, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def patch_pack(path, kind, payload_json):
    """Xuat patch pack nhi phan (muc C7): manifest JSON + script Python
    ap patch + README, dong goi 1 file .zip (/tmp/patch_pack.zip).

    kind "plan": payload_json la plan JSON muc 8 (chi entry duoc tick).
    kind "mod": payload_json la edits JSON cua tab Mod (nhu mod_patch).
    Khong ghi file goc, khong ghi don vi da patch — chi xuat pack de
    user mang sang iSH/desktop ap (script mac dinh dry-run, chi ghi
    that voi --allow-write).
    """
    try:
        from sdk import patch_pack as pp

        _check_size(path)
        data = open(path, "rb").read()
        manifest = pp.build_patch_pack(data, os.path.basename(path),
                                       kind, payload_json)
        zpath = "/tmp/patch_pack.zip"
        if os.path.exists(zpath):
            os.remove(zpath)
        info = pp.make_pack_zip(manifest, zpath)
        return json.dumps({
            "ok": True, "pack_id": manifest["id"],
            "kind": kind, "entries": len(manifest["entries"]),
            "source_file": manifest["source_file"],
            "source_sha256": manifest["source_sha256"],
            "zip_path": zpath,
            "filename": "patch-pack-%s.zip" % manifest["id"],
            "zip_bytes": info["size"],
            "manifest": manifest,
            "note": "Pack gom patch_manifest.json + apply_patch.py + "
                    "README.txt. Mang sang iSH/desktop, chay script "
                    "(mac dinh dry-run, --allow-write moi ghi that); "
                    "script khong bao gio ghi de file goc.",
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# BATCH — menu [4] iSH (sdk/batch.py).
# ======================================================================
_BATCH_ROOT = "/tmp/batch"


def _resolve_batch_path(p):
    """Đường dẫn game trong manifest: dùng nguyên nếu có trong FS ảo,
    nếu không thì tìm theo tên file trong /tmp/input/ (nơi worker ghi
    file người dùng tải lên). Không tìm thấy thì giữ nguyên — BatchRunner
    sẽ ghi lỗi E_INPUT_NOT_FOUND cho riêng game đó, không dừng cả lô."""
    if os.path.isfile(p):
        return p
    cand = os.path.join("/tmp/input", os.path.basename(str(p)))
    if os.path.isfile(cand):
        return cand
    return p


def _batch_out_dir(out_dir_name):
    """Thư mục batch cố định cho một lần chạy pool (để nhiều game/worker
    chia sẻ cùng out_dir): chỉ nhận tên an toàn ([A-Za-z0-9][A-Za-z0-9_.-]*,
    không '..'), tên khác thì trả None (dùng thư mục theo giờ như cũ)."""
    if not out_dir_name or not isinstance(out_dir_name, str):
        return None
    if ".." in out_dir_name or "/" in out_dir_name or "\\" in out_dir_name:
        return None
    if not out_dir_name[0].isalnum():
        return None
    if any((not ch.isalnum()) and ch not in "_-." for ch in out_dir_name):
        return None
    return os.path.join(_BATCH_ROOT, out_dir_name)


def batch_split(manifest_text):
    """Tách manifest thành từng game + khóa checkpoint của mỗi game (Mục 4:
    pool chia game cho 2 worker; main thread lọc game đã xong bằng khóa này
    TRƯỚC khi giao việc, không cần đoán ở phía JS). Trả
    {ok, total, items: [{index, path, mode, locale, old, new, file, object,
    key}]}. key = sha256(path, locale, mode, old, new) như sdk/batch.py."""
    try:
        from sdk.batch import parse_manifest

        items = parse_manifest(manifest_text)
        out = []
        for i, it in enumerate(items):
            out.append({"index": i + 1, "path": it.path, "mode": it.mode,
                        "locale": it.locale, "old": it.old, "new": it.new,
                        "file": it.file, "object": it.object,
                        "key": it.key()})
        return json.dumps({"ok": True, "total": len(out), "items": out},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def batch_run(manifest_text, dry_run=True, checkpoint_json=None, resume=False,
              out_dir_name=None, record_kb=True, make_zip=True, index_base=1):
    """Chạy một lô game theo manifest (JSON list hoặc CSV text, cùng dạng
    bản iSH: path/mode/locale/old/new/file/object/profile_override).

    CHẠY TUẦN TỰ (jobs=1) TRONG MỘT WORKER — Pyodide trên GitHub Pages
    không có threading. Song song trên web đi bằng POOL 2 WORKER (Mục 4):
    app.js tách manifest bằng batch_split rồi gọi batch_run cho TỪNG GAME
    (manifest 1 game) trên 2 worker độc lập, mỗi lần gọi dùng chung
    out_dir_name để checkpoint/output dồn về một thư mục. Mỗi game cô lập:
    lỗi một game không dừng cả lô. dry_run=True mặc định (chạy thử trước,
    chưa ghi). checkpoint_json: chuỗi checkpoint lần chạy trước (UI lưu ở
    IndexedDB hoặc file) + resume=True để bỏ qua game đã xong.
    out_dir_name: tên thư mục cố định dưới /tmp/batch (chỉ tên an toàn);
    không cho thì dùng thư mục theo giờ như trước. record_kb=False: batch
    không ghi vào KB (pool dùng: KB của 2 worker khác nhau, không gộp được
    file SQLite — xem báo cáo Mục 4). make_zip=False: không zip /tmp/batch.zip
    sau mỗi game (pool gọi nhiều game, chỉ zip 1 lần cuối bằng batch_finalize).
    Trả summary + checkpoint mới (UI lưu lại) + zip kết quả (nếu make_zip).
    """
    try:
        from sdk.batch import BatchRunner, parse_manifest

        items = parse_manifest(manifest_text)
        for it in items:
            it.path = _resolve_batch_path(it.path)
        out_dir = _batch_out_dir(out_dir_name)
        if out_dir is None:
            out_dir = os.path.join(
                _BATCH_ROOT, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(out_dir, exist_ok=True)
        if resume and checkpoint_json:
            with open(os.path.join(out_dir, "checkpoint.json"),
                      "w", encoding="utf-8") as fh:
                fh.write(checkpoint_json)
        runner = BatchRunner(registry=_reg, app=_app,
                             stream=io.StringIO())
        kb = None
        if record_kb:
            try:
                kb = _get_kb()
            except Exception:
                kb = None  # KB lỗi thì batch vẫn chạy (như bản iSH: kb=None chạy được)
        summary = runner.run(
            items, out_dir=out_dir, jobs=1, resume=bool(resume),
            dry_run=bool(dry_run), kb=kb,
            progress_cb=lambda d, t, name: _emit("batch", d, t))
        ck_path = os.path.join(out_dir, "checkpoint.json")
        # Mục 4 (pool): mỗi game chạy bằng manifest 1 game nên _batch_index
        # luôn = 1 -> thư mục game_01_<slug> đụng nhau khi 2 worker gộp
        # (vd 2 game cùng file game1.zip khác locale). Đổi tên thư mục +
        # game_dir theo vị trí thật trong manifest gốc (index_base) để gộp
        # không đè nhau; viết lại checkpoint.json cho khớp game_dir mới.
        if index_base and int(index_base) != 1:
            for g in summary.games:
                if not g.game_dir:
                    continue
                src = g.game_dir
                base = os.path.basename(src)
                if not base.startswith("game_01_"):
                    continue
                dest = os.path.join(
                    os.path.dirname(src),
                    "game_%02d_%s" % (int(index_base), base[len("game_01_"):]))
                try:
                    if os.path.isdir(src) and not os.path.exists(dest):
                        os.rename(src, dest)
                        g.game_dir = dest
                except OSError:
                    pass
            try:
                with open(ck_path, "r", encoding="utf-8") as fh:
                    ckdata = json.load(fh)
                for _k, _v in (ckdata.get("games") or {}).items():
                    for _g in summary.games:
                        if _g.key == _k and _g.game_dir:
                            _v["game_dir"] = _g.game_dir
                with open(ck_path, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(ckdata, ensure_ascii=False, indent=1))
            except (OSError, ValueError):
                pass
        checkpoint = None
        if os.path.isfile(ck_path):
            with open(ck_path, "r", encoding="utf-8") as fh:
                checkpoint = fh.read()
        zpath = None
        zbytes = 0
        if make_zip:
            zpath = "/tmp/batch.zip"
            if os.path.exists(zpath):
                os.remove(zpath)
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, fns in os.walk(out_dir):
                    dirs[:] = [d for d in dirs if d != ".staging"]
                    for fn in sorted(fns):
                        fp = os.path.join(root, fn)
                        zf.write(fp, os.path.relpath(fp, out_dir))
            zbytes = os.path.getsize(zpath)
        return json.dumps({
            "ok": True, "jobs": 1, "sequential": True,
            "dry_run": bool(dry_run), "resumed": bool(resume),
            "summary": summary.to_dict(), "checkpoint": checkpoint,
            "zip_path": zpath, "zip_bytes": zbytes,
            "out_dir_name": os.path.basename(out_dir),
            "out_dir": out_dir,
            "note": "Batch trong một worker chạy TUẦN TỰ từng game "
                    "(không có threading trên GitHub Pages, không có chế độ "
                    "song song trong worker). Song song đi bằng pool 2 worker "
                    "(Mục 4), mỗi worker gọi batch_run cho từng game riêng.",
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def batch_checkpoint(out_dir_name):
    """Đọc checkpoint.json hiện tại của một thư mục batch (Mục 4: main
    thread hỏi lại checkpoint sau mỗi game để lưu vào IndexedDB). Trả
    {ok, checkpoint: <chuỗi JSON hoặc None>, out_dir_name}."""
    try:
        out_dir = _batch_out_dir(out_dir_name)
        if out_dir is None:
            raise ValueError("out_dir_name không hợp lệ: %r" % (out_dir_name,))
        ck_path = os.path.join(out_dir, "checkpoint.json")
        checkpoint = None
        if os.path.isfile(ck_path):
            with open(ck_path, "r", encoding="utf-8") as fh:
                checkpoint = fh.read()
        return json.dumps({"ok": True, "checkpoint": checkpoint,
                           "out_dir_name": os.path.basename(out_dir)},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def batch_zip(out_dir_name):
    """Zip thư mục batch thành /tmp/batch-<tên>.zip (Mục 4: lấy zip output
    của worker 1 để gộp sang worker 0; worker.js đính kèm file zip vào
    reply khi parsed có zip_path). Trả {ok, zip_path, zip_bytes}."""
    try:
        out_dir = _batch_out_dir(out_dir_name)
        if out_dir is None:
            raise ValueError("out_dir_name không hợp lệ: %r" % (out_dir_name,))
        zpath = "/tmp/batch-%s.zip" % os.path.basename(out_dir)
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, fns in os.walk(out_dir):
                dirs[:] = [d for d in dirs if d != ".staging"]
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))
        return json.dumps({"ok": True, "zip_path": zpath,
                           "zip_bytes": os.path.getsize(zpath),
                           "filename": "batch.zip",
                           "out_dir_name": os.path.basename(out_dir)},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def batch_merge_zip(out_dir_name, zip_path):
    """Gộp file batch.zip của worker kia vào thư mục batch của worker này
    (Mục 4: 2 worker có FS riêng, cuối đợt phải gộp output về 1 zip duy
    nhất). Bỏ qua checkpoint.json / summary.json / index.csv / failures.txt
    trong zip nguồn (những file này được batch_finalize viết lại từ bản
    gộp của main thread). Trả {ok, files, out_dir_name}."""
    try:
        out_dir = _batch_out_dir(out_dir_name)
        if out_dir is None:
            raise ValueError("out_dir_name không hợp lệ: %r" % (out_dir_name,))
        if not os.path.isfile(zip_path):
            raise ValueError("không có file zip để gộp: %s" % zip_path)
        os.makedirs(out_dir, exist_ok=True)
        skip = {"checkpoint.json", "summary.json", "index.csv",
                "failures.txt"}
        n = 0
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                name = info.filename
                if name in skip or name.endswith("/"):
                    continue
                dest = os.path.join(out_dir, name)
                if not os.path.realpath(dest).startswith(
                        os.path.realpath(out_dir) + os.sep):
                    continue  # chặn entry zip thoát khỏi out_dir
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(info, "r") as src, open(dest, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                n += 1
        return json.dumps({"ok": True, "files": n,
                           "out_dir_name": os.path.basename(out_dir)},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def batch_finalize(out_dir_name, summary_json, checkpoint_json):
    """Viết summary.json + checkpoint.json + index.csv + failures.txt GỘP
    (main thread gộp từ kết quả từng game của 2 worker) rồi zip toàn bộ
    thư mục batch thành /tmp/batch-<tên>.zip. Trả
    {ok, zip_path, zip_bytes, out_dir_name}. Đây là file batch.zip duy
    nhất user tải về ở cuối đợt pool."""
    try:
        from sdk.batch import (CHECKPOINT_FILENAME, FAILURES_FILENAME,
                               INDEX_FILENAME, SUMMARY_FILENAME)

        out_dir = _batch_out_dir(out_dir_name)
        if out_dir is None:
            raise ValueError("out_dir_name không hợp lệ: %r" % (out_dir_name,))
        os.makedirs(out_dir, exist_ok=True)
        summary = json.loads(summary_json) if summary_json else {}
        with open(os.path.join(out_dir, SUMMARY_FILENAME),
                  "w", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False, indent=1))
        if checkpoint_json:
            with open(os.path.join(out_dir, CHECKPOINT_FILENAME),
                      "w", encoding="utf-8") as fh:
                fh.write(checkpoint_json)
        import csv as _csv

        games = summary.get("games") or []
        with open(os.path.join(out_dir, INDEX_FILENAME),
                  "w", encoding="utf-8", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["game", "mode", "locale", "kết quả", "mã lỗi",
                        "thời gian (s)", "thư mục"])
            for g in games:
                name = os.path.basename(g.get("game_dir") or "") or \
                    os.path.basename((g.get("path") or "").rstrip("/"))
                w.writerow([name, g.get("mode") or "", g.get("locale") or "",
                            g.get("status") or "", g.get("error_code") or "",
                            "%.2f" % float(g.get("elapsed_s") or 0.0),
                            g.get("game_dir") or ""])
        lines = []
        for g in games:
            if g.get("status") != "failed":
                continue
            name = os.path.basename(g.get("game_dir") or "") or \
                os.path.basename((g.get("path") or "").rstrip("/"))
            lines.append("%s [%s] %s" % (name, g.get("error_code") or "?",
                                         g.get("error_message") or
                                         g.get("note") or "lỗi"))
        with open(os.path.join(out_dir, FAILURES_FILENAME),
                  "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        zpath = "/tmp/batch-%s.zip" % os.path.basename(out_dir)
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, fns in os.walk(out_dir):
                dirs[:] = [d for d in dirs if d != ".staging"]
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))
        return json.dumps({"ok": True, "zip_path": zpath,
                           "zip_bytes": os.path.getsize(zpath),
                           "filename": "batch.zip",
                           "out_dir_name": os.path.basename(out_dir)},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# KB — menu [3] iSH (sdk/kb.py). SQLite TRONG PHIÊN.
# ======================================================================
_KB_PATH = "/tmp/kb/pb3_kb.sqlite"
_kb = None


def _get_kb():
    """KB của web: file SQLite trong RAM của Worker (/tmp/kb, MEMFS).

    Mất khi tải lại trang NẾU không có bản lưu — nhưng app.js đã tự lưu
    bytes KB vào IndexedDB sau mỗi thay đổi (kb_snapshot) và nạp lại
    lúc boot (kb_restore), nên thực tế KB còn qua lần tải lại trang.
    Nút xuất .sqlite (kb_export) vẫn là đường sao lưu chính."""
    global _kb
    if _kb is None:
        from sdk.kb import KB

        _kb = KB(_KB_PATH)
    return _kb


def kb_add(path):
    """Thêm game đã chọn vào KB: fingerprint + nhớ profile (chỉ lưu
    hash/kích thước/vai trò — KHÔNG lưu nội dung game hay bản dịch)."""
    try:
        from sdk.kb import fingerprint_game

        _check_size(path)
        kb = _get_kb()
        fp = fingerprint_game(path)
        gid = kb.remember(fp, list(fp.get("files") or []), [], {})
        return json.dumps({
            "ok": True, "game_id": int(gid),
            "bundle_id": fp.get("bundle_id"), "version": fp.get("version"),
            "engine": fp.get("engine"),
            "engine_conf": fp.get("engine_conf"),
            "fingerprint": (fp.get("fingerprint") or "")[:16],
            "size": int(fp.get("size") or 0),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_query(path):
    """Tra cứu profile game đã chọn trong KB (fingerprint khớp mới tái
    dùng; lệch hash thì trả found=False + lý do, không tái dùng im lặng)."""
    try:
        _check_size(path)
        kb = _get_kb()
        prof = kb.lookup(path)
        if prof is None:
            return json.dumps({
                "ok": True, "found": False,
                "reason": kb.last_lookup_reason,
            }, ensure_ascii=False)
        return json.dumps({"ok": True, "found": True, "profile": prof},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_list():
    """Danh sách game đã biết trong KB (đã tự lưu vào IndexedDB)."""
    try:
        kb = _get_kb()
        return json.dumps({
            "ok": True, "games": kb.list_games(),
            "size_bytes": int(kb.size_bytes()),
            # Đổi ở Mục 3: KB không còn chỉ sống trong phiên nữa — app.js
            # tự lưu bytes KB vào IndexedDB sau mỗi thay đổi (kb_snapshot)
            # và nạp lại lúc boot (kb_restore). Giữ khóa session_only
            # (giờ False) để code cũ đọc vẫn hiểu, thêm persisted: True.
            "session_only": False,
            "persisted": True,
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_forget(bundle_id):
    """Quên tri thức một game (theo bundle id hoặc id số)."""
    try:
        kb = _get_kb()
        return json.dumps({"ok": True,
                           "forgotten": bool(kb.forget(bundle_id))},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_export():
    """Xuất file KB của phiên ra /tmp/pb3_kb.sqlite để tải về.

    Đóng kết nối trước khi copy (SQLite checkpoint WAL vào file chính khi
    đóng kết nối cuối — file tải về mới đầy đủ, tự chứa). File này là
    bản sao lưu chính của KB: KB trong trình duyệt đã tự lưu vào
    IndexedDB (kb_snapshot), nhưng file .sqlite vẫn là bản sao lưu chắc
    chắn nhất — phòng khi trình duyệt xóa dữ liệu web. Nhập lại bằng
    kb_import trong phiên mới nếu cần."""
    global _kb
    try:
        kb = _get_kb()
        n = len(kb.list_games())
        kb.close()
        _kb = None
        dest = "/tmp/pb3_kb.sqlite"
        if os.path.exists(dest):
            os.remove(dest)
        shutil.copyfile(_KB_PATH, dest)
        return json.dumps({
            "ok": True, "dest": dest, "size": os.path.getsize(dest),
            "games": int(n),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_snapshot():
    """Bytes hiện tại của file KB (base64) để app.js lưu vào IndexedDB.

    Đóng kết nối trước khi đọc (như kb_export: checkpoint WAL vào file
    chính để bytes đọc được là đầy đủ, tự chứa), rồi mở lại ngay để KB
    tiếp tục dùng được. KB chỉ chứa fingerprint/profile (nhỏ) nên
    base64 qua postMessage là đủ, không cần đường file."""
    global _kb
    try:
        kb = _get_kb()
        n = len(kb.list_games())
        kb.close()
        _kb = None
        if not os.path.exists(_KB_PATH):
            return json.dumps({"ok": True, "b64": None, "size": 0,
                               "games": 0}, ensure_ascii=False)
        with open(_KB_PATH, "rb") as f:
            data = f.read()
        _get_kb()  # mở lại để các thao tác KB sau vẫn dùng được
        return json.dumps({
            "ok": True, "b64": base64.b64encode(data).decode("ascii"),
            "size": len(data), "games": int(n),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_restore(b64):
    """Khôi phục KB từ bytes base64 (app.js đọc từ IndexedDB lúc boot).

    Kiểm tra magic SQLite TRƯỚC khi chạm vào KB hiện tại; chỉ khi magic
    đúng mới đóng KB, ghi đè file (xóa -wal/-shm cũ để không lẫn dữ liệu
    cũ) rồi mở lại và đếm game. Bytes sai/không phải SQLite thì trả lỗi
    JSON, KB cũ giữ nguyên."""
    global _kb
    try:
        if not isinstance(b64, str) or not b64:
            raise ValueError("Không có dữ liệu KB để khôi phục.")
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:
            raise ValueError("Dữ liệu KB không phải base64 hợp lệ.")
        if data[:16] != b"SQLite format 3\x00":
            raise ValueError("Dữ liệu KB không phải file SQLite hợp lệ.")
        kb = _get_kb()
        kb.close()
        _kb = None
        os.makedirs(os.path.dirname(_KB_PATH), exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            p = _KB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)
        with open(_KB_PATH, "wb") as f:
            f.write(data)
        kb = _get_kb()
        return json.dumps({"ok": True, "games": len(kb.list_games()),
                           "size": len(data)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def kb_import(path):
    """Nhập file KB (.sqlite) đã xuất trước đó (kb_export) vào KB của
    phiên hiện tại: mở bản sao của file tải lên làm KB nguồn, xuất từng
    profile (export_profile) rồi nhập vào KB phiên (import_profile,
    không có file game để đối chiếu hash nên mỗi profile kèm cảnh báo
    'chưa kiểm hash' — profile chỉ là gợi ý cho tới khi dò lại). Game
    đã có trong KB phiên (cùng bundle_id) sẽ được ghi đè bằng dữ liệu
    nhập (hành vi của import_profile). File tải lên không bị sửa (mở
    trên bản sao; KB nguồn dùng WAL/migrate nên phải mở bản sao, không
    mở trực tiếp file người dùng). Game không có bundle id thì không
    nhập được (import_profile lấy bundle id làm khóa) — game đó bị bỏ
    qua, ghi vào 'skipped' + cảnh báo, các game khác vẫn nhập bình
    thường; một profile lỗi cũng không làm hỏng cả lần nhập."""
    try:
        from sdk.kb import KB

        _check_size(path)
        src_path = "/tmp/kb_import.sqlite"
        if os.path.exists(src_path):
            os.remove(src_path)
        shutil.copyfile(path, src_path)
        src = KB(src_path)
        try:
            games = src.list_games()
            kb = _get_kb()
            imported = []
            skipped = 0
            warnings = []
            for g in games:
                bundle_id = g.get("bundle_id")
                if not bundle_id:
                    skipped += 1
                    warnings.append(
                        "bỏ qua game id %s: không có bundle id nên "
                        "không nhập được (cần bundle id làm khóa)"
                        % (g.get("id"),))
                    continue
                try:
                    profile = src.export_profile(g["id"])
                    gid, warns = kb.import_profile(profile)
                except Exception as e:  # noqa: BLE001 - 1 game lỗi, nhập tiếp
                    skipped += 1
                    warnings.append("bỏ qua game %r: %s"
                                    % (bundle_id, e))
                    continue
                imported.append({
                    "game_id": int(gid), "bundle_id": bundle_id,
                    "version": g.get("version"),
                    "engine": g.get("engine")})
                warnings.extend(warns or [])
            return json.dumps({
                "ok": True, "imported": len(imported),
                "skipped": skipped, "games": imported,
                "warnings": warnings,
            }, ensure_ascii=False)
        finally:
            src.close()
            if os.path.exists(src_path):
                os.remove(src_path)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# MA TRẬN HỖ TRỢ — lệnh `support` iSH (sdk/support_matrix.py).
# ======================================================================
def support_matrix():
    """Ma trận hỗ trợ sinh từ dữ liệu thật của PB3: hàng registry.rows()
    (maturity hiệu lực), sdk.containers.support_matrix(),
    sdk.unity.support_matrix(), cột 'số game đã test' từ KB của phiên.
    Không hardcode bảng. Lưu ý: ngày test hiện 'chưa có' vì bản web
    không vendor tests/corpus/*.json (chỉ có trên cây iSH)."""
    try:
        from sdk import support_matrix as sm

        rows = _reg.rows()
        manifests = {}
        for rec in _reg.records():
            manifests[rec.plugin_id] = getattr(rec, "manifest", None)
        kb_stats = {}
        try:
            kb_stats = _get_kb().plugin_stats()
        except Exception:
            kb_stats = {}
        markdown, data = sm.generate(
            WEB_ROOT, rows=rows, manifests=manifests, kb_stats=kb_stats)
        return json.dumps({"ok": True, "markdown": markdown, "data": data},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)
