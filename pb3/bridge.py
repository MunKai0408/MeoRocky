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

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import time
import zipfile

WEB_ROOT = "/pb3web"
# Giới hạn riêng của bản web (RAM tab trình duyệt, không phải giới hạn PB3):
# file đầu vào >2 GiB thì từ chối rõ thay vì để trình duyệt crash.
# Lưu ý: file gần 2GB vẫn có thể làm tab Safari trên iPhone văng vì RAM.
MAX_WEB_INPUT = 2 * 1024 * 1024 * 1024
MAX_TEXT_HEAD = 200          # số ký tự TextAsset hiển thị mỗi object
MAX_LIST_TEXT_ASSETS = 50    # như _COMPACT_MAX_TEXT_ASSETS của PB3
MAX_STRINGS = 200
STRINGS_SCAN_BYTES = 1024 * 1024


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

    Dùng parser thuần của PB3 (sdk/unity.py) + backend NATIVE (binding lz4
    nếu loadPackage('lz4') đã chạy, nếu không thì lz4pure). Không dùng
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
            "skipped": list(r.skipped or []),
            "log": list((r.report or {}).get("log") or []),
            "errors": list(r.errors or []), "zip_path": zpath,
            "error": err_text,
            "zip_bytes": os.path.getsize(zpath),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


MAX_READ_EXTRACTED = 4 * 1024 * 1024   # read_extracted: tối đa 4MB nạp vào ô dịch


def read_extracted(name):
    """Đọc 1 file JSON đã trích xuất trong /tmp/pb3out (để nạp vào tab Dịch).

    Chỉ nhận tên file trần (không đường dẫn — chống directory traversal
    trong FS worker); file phải là JSON hợp lệ và <= 4MB (ô dịch + bộ nhớ
    tab iPhone). Lớn hơn thì báo rõ, user tải extract.zip về rồi dán
    từng phần. Không cần gửi lại file game (worker đã có /tmp/pb3out từ
    đợt extract vừa rồi)."""
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
        if code == src:
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


def translate_json(json_text, to="vi", credit=True, src="en"):
    """Dịch bảng key-value bằng từ điển offline (không cần mạng).

    Chỉ dịch VALUE, giữ nguyên KEY (luật của PB3). credit=True thì gắn
    'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/version nếu tìm được.
    src: ngôn ngữ nguồn phải dịch (mặc định "en"); chuỗi phát hiện mạnh
    thuộc ngôn ngữ khác bị BỎ QUA (giữ nguyên từng byte, ghi lý do vào
    skipped) — không dịch nhầm như trước (bridge từng hard-code "en").
    Lọc ở mức VALUE (cả giá trị): giá trị nào phát hiện mạnh không phải
    src thì giữ nguyên toàn bộ.
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
            credit=False)
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
# Gộp tối đa 100 chuỗi + 5000 ký tự/batch (khớp giới hạn proxy Cloudflare
# + giữ q joined-text vừa phải cho gtx). Batch lỗi liên tiếp 2 lần thì
# tắt batch, về dịch từng chuỗi cho phần còn lại.
# Split-on-failure (tăng tốc dịch): batch lỗi do mất marker thì chia đôi
# đệ quy thay vì rớt về dịch đơn cả batch — đo thật: 1 batch 100 chuỗi rớt
# marker = 52s thay vì 2,3s. Batch <= 10 chuỗi thì không chia nữa, về đơn.
BATCH_MAX_STRINGS = 100
BATCH_MAX_CHARS = 5000
_BATCH_FAIL_LATCH = 2
_BATCH_SPLIT_MIN = 10
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


async def translate_json_google(json_text, to="vi", credit=True, src="en"):
    """Dịch bảng key-value bằng Google Translate thật qua fetch() của JS.

    Luồng (batch, mục A): Python (async) gọi `js.pb3_google_translate_batch`
    (gt.js) cho từng BATCH tối đa 100 chuỗi/5000 ký tự — gtx không nhận
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
        uniques = []  # {"masked", "keys":[(key,line_no)], "prio"}
        uid_of_norm = {}
        for (key, line_no, masked, _tokens) in segments:
            norm = _norm_segment_text(masked)
            uid = uid_of_norm.get(norm)
            if uid is None:
                uid = len(uniques)
                uid_of_norm[norm] = uid
                uniques.append({"masked": masked,
                                "keys": [(key, line_no)],
                                "prio": _priority_of(key, masked)})
            else:
                uniques[uid]["keys"].append((key, line_no))
                p = _priority_of(key, masked)
                if p < uniques[uid]["prio"]:
                    uniques[uid]["prio"] = p
        deduped = len(segments) - len(uniques)

        # --- Tra từ điển offline TRƯỚC (glossary-first): chuỗi nào từ
        # điển biết thì không gửi Google (tiết kiệm request + không tốn
        # hạn mức proxy).
        uniq_val = [None] * len(uniques)   # masked đã dịch / None
        uniq_origin = [""] * len(uniques)
        if uniques:
            fb_all = fallback.translate_batch(
                [u["masked"] for u in uniques], src, to)
            for uid, val in enumerate(fb_all or []):
                if val is not None:
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
        _split_error = [None]

        async def _translate_batch_split(texts, uids):
            """Dịch 1 batch qua _gt_batch; lỗi marker thì chia đôi đệ quy.

            Trả danh sách uid CHƯA dịch được qua batch (rỗng = xong hết).
            Uid nào dịch xong qua batch thì ghi uniq_val/uniq_origin ngay
            (kể cả khi chỉ 1 nửa batch thành công) — chỉ phần chưa xong
            mới rơi về dịch đơn. Batch trả sai số chuỗi cũng tính là batch
            hỏng (như mất marker) và được chia. Lỗi cancelled/quota thì
            ném lên để caller xử lý như cũ (không chia). Lỗi đầu tiên ghi
            vào _split_error để caller báo network_error.
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
                if _split_error[0] is None:
                    _split_error[0] = exc
                if (splittable or _batch_split_retryable(exc)) \
                        and len(uids) > _BATCH_SPLIT_MIN:
                    mid = len(uids) // 2
                    left = await _translate_batch_split(
                        texts[:mid], uids[:mid])
                    right = await _translate_batch_split(
                        texts[mid:], uids[mid:])
                    return left + right
                return list(uids)

        for batch in batches:
            if _is_cancelled():
                cancelled = True
                break
            texts = [uniques[uid]["masked"] for uid in batch]
            got_batch = False
            failed_uids = list(batch)
            if _gt_batch is not None and not quota_hit:
                _split_error[0] = None
                try:
                    failed_uids = await _translate_batch_split(texts, batch)
                except Exception as exc:  # noqa: BLE001 - cancelled/quota
                    kind = _gt_kind(exc)
                    if kind == "cancelled":
                        cancelled = True
                        break
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
                        if network_error is None and _split_error[0] is not None:
                            network_error = _gt_error_text(_split_error[0])
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
                continue
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


def font_scan(path):
    """Quét 1 font TTF: đọc cmap, báo độ phủ ký tự tiếng Việt."""
    try:
        from sdk import font as f

        _check_size(path)
        data = open(path, "rb").read()
        rep = f.coverage(data, where=os.path.basename(path))
        info = f.parse_font(data, where=os.path.basename(path))
        return json.dumps({
            "ok": True, "family": rep.family, "kind": rep.kind,
            "num_glyphs": int(rep.num_glyphs), "total": int(rep.total),
            "covered": int(rep.covered), "pct": int(rep.pct),
            "missing": [int(c) for c in rep.missing[:50]],
            "missing_total": len(rep.missing),
            "missing_chars": [chr(c) for c in rep.missing[:50]],
            "summary": rep.summary_vi(),
            "warnings": list(info.warnings or []),
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


def font_merge(path, donor_path):
    """Gộp glyph tiếng Việt còn thiếu từ font nguồn vào font đích.

    Trả font mới ra /tmp/merged.ttf để JS tải về. Chỉ font TrueType glyf;
    atlas SDF của TextMeshPro không tái tạo được (giới hạn thật của PB3).
    """
    try:
        from sdk import font as f

        _check_size(path)
        target = open(path, "rb").read()
        donor = open(donor_path, "rb").read()
        res = f.merge_vietnamese(
            target, donor,
            progress_fn=lambda d, t: _emit("font", d, t))
        out = "/tmp/merged.ttf"
        open(out, "wb").write(res.data)
        return json.dumps({
            "ok": True, "dest": out,
            "added": len(res.added), "already": len(res.already),
            "donor_missing": len(res.donor_missing),
            "glyphs_copied": int(res.glyphs_copied),
            "glyphs_total": int(res.glyphs_total),
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
    Unity Editor. Atlas SDF KHÔNG tái tạo được trên web — sinh lại atlas
    bắt buộc làm trong Unity Editor (script site/unity_editor/).
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


def font_unify(path, donor_path):
    """"Dùng chung 1 font": gom mọi font game về Be Vietnam Pro (phần D).

    Dùng lại kết quả liệt kê (cache) rồi lập kế hoạch + làm thật phần
    web làm được: gộp glyph tiếng Việt còn thiếu vào từng font TTF rời
    (sdk/font.merge_vietnamese). Trả file font_unified.zip để JS tải về:
    các *.vi.ttf đã gộp + font_unify_plan.json + tmp_fallback_plan.json
    (nếu có TMP) + BeVietnamPro-Regular.ttf (đủ 142/142, OFL).
    TMP atlas SDF, font OTF/TTC/nhúng: chỉ có kế hoạch + đường Editor
    trong plan (xem LIMITS của sdk/font_list).
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
               rebuild_all=False, asset_names_json=None):
    """Lập plan JSON từ bundle/assets: quét TextAsset JSON, đối key trong
    translations (key -> giá trị mới, như đầu ra tab Dịch) thành entry
    mỗi asset. Không ghi file nào."""
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
        data = open(path, "rb").read()
        plan = plan_sdk.build_plan(
            data, translations, source_bundle=os.path.basename(path),
            entry=entry or None, mode=mode or "REBUILD_SECTION",
            rebuild_all=bool(rebuild_all), asset_names=asset_names)
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
                  gate_verdict_json=None, source_locale="en"):
    """(E) Ép bản dịch lại vào file gốc [bước 5, P0-1].

    Preview và apply là CÙNG 1 op với cờ dry_run (tiền lệ plan_apply /
    mod_patch): dry_run=True (mặc định) chỉ tính toán + verify, không
    ghi file nào; dry_run=False BẮT BUỘC confirm=True, nếu không trả
    lỗi và không ghi gì. Nhóm bản dịch theo provenance (merge sidecar
    ưu tiên, translation sidecar fallback, không provenance thì quét
    tất cả), chọn bản thắng cho xung đột, 1 plan mỗi đơn vị, áp bằng
    executor hiện có + đường rebuild streaming cho bundle .unity3d độc
    lập lớn. Ghi TextAsset JSON dạng dict + MonoBehaviour/StringTable
    (M1: bảng I2 byte-walk + StringTable co type tree, REBUILD_SECTION);
    key MonoBehaviour generic (scan path) vao unmatched voi reason ro
    rang. source_locale: cot ngon ngu goc de ghi (mac dinh "en"). File
    gốc KHÔNG bị ghi đè; output là đơn vị nhỏ nhất đã patch, KHÔNG
    phải IPA đóng gói lại.
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
        # JsNull (Pyodide) là falsy — dùng truthiness như plan_apply.
        dry = bool(dry_run) if dry_run is not None else True
        # dry_run=False + confirm=False: rebuild.apply_rebuild sẽ từ chối
        # với E_CONFIG_INVALID, không ghi gì.
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
            source_locale=source_locale or "en")
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


def delivery_gate(path, translations_json, source_json=None,
                  provenance_json=None, pressed_path=None,
                  allowlist_json=None, font_path=None, choices_json=None,
                  allow_partial=False, source_locale="en"):
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
        display_fonts = []
        font_error = None
        if font_path:
            try:
                fdata = open(str(font_path), "rb").read()
                display_fonts.append(_gate.font_entry_from_bytes(
                    fdata, os.path.basename(str(font_path))))
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
            source_locale=source_locale or "en")
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
