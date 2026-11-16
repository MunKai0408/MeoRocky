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
    """Liệt kê entry của container (ZIP/IPA/bundle) — inventory bước 1."""
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
        return json.dumps({"ok": True, "kind": kind, "total": len(entries),
                           "entries": entries}, ensure_ascii=False)
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
        from sdk import containers, discovery as disc

        _check_size(path)
        name = os.path.basename(path)
        data = open(path, "rb").read()
        if containers.detect_container_kind(data) == "zip":
            from core.limits import Limits

            vfs = containers.open_vfs_bytes(data, source_name=name)
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
        out = {"ok": True, "is_container": False}
        out.update(_parse_unity_bytes(data, name))
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def unity_inspect_entry(path, entry):
    """Inspect 1 bundle/.assets BÊN TRONG file ZIP/IPA: đọc entry đó ra
    rồi parse (không parse cả file ZIP). entry là đường dẫn trong
    container (vd Payload/9Kings.app/Data/sharedassets0.assets)."""
    try:
        from sdk import containers

        _check_size(path)
        data = open(path, "rb").read()
        vfs = containers.open_vfs_bytes(
            data, source_name=os.path.basename(path))
        raw = vfs.read(entry)
        out = {"ok": True, "is_container": False, "entry": entry}
        out.update(_parse_unity_bytes(raw, os.path.basename(entry)))
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def extract(path, source_paths=None, locales=None):
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
            source_paths=paths, locales=locs)
        r = ext.run_extraction(req)
        files = [{"path": f.path, "plugin": f.plugin,
                  "entries": int(f.entries), "locale": f.locale,
                  "exported": f.exported,
                  "source": f.source or f.path} for f in r.files]
        zpath = "/tmp/extract.zip"
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, fns in os.walk(out_dir):
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))
        return json.dumps({
            "ok": bool(r.ok), "decision": str(r.decision),
            "total_entries": int(r.total_entries), "files": files,
            "skipped": list(r.skipped or []),
            "log": list((r.report or {}).get("log") or []),
            "errors": list(r.errors or []), "zip_path": zpath,
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


def translate_json(json_text, to="vi", credit=True):
    """Dịch bảng key-value bằng từ điển offline (không cần mạng).

    Chỉ dịch VALUE, giữ nguyên KEY (luật của PB3). credit=True thì gắn
    'Việt Hoá Bởi Bear' vào 1 entry tiêu đề/version nếu tìm được.
    """
    try:
        from sdk.translate import OfflineGlossaryProvider, translate_table

        kv = json.loads(json_text)
        if not isinstance(kv, dict):
            raise ValueError("File JSON phải là object key-value.")
        kv = {str(k): (v if isinstance(v, str)
                       else json.dumps(v, ensure_ascii=False))
              for k, v in kv.items()}
        res = translate_table(
            kv, source_lang="en", target_lang=to,
            provider=OfflineGlossaryProvider(),
            progress_fn=lambda d, t: _emit("translate", d, t),
            credit=bool(credit))
        return json.dumps({
            "ok": True, "translated": res.translated, "total": int(res.total),
            "untranslated": int(res.untranslated),
            "untranslated_keys": list(res.untranslated_keys or [])[:50],
            "warnings": list(res.warnings or [])[:20],
            "credit_key": res.credit_key,
            "translated_by": "offline", "google_count": 0,
            "glossary_count": int(res.glossary),
            "network_error": None, "cancelled": False,
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
    if kind:
        return "Google Translate lỗi (%s): %s" % (kind, msg)
    return "Google Translate lỗi: %s" % msg


# --- Batch dịch (mục A, đợt bổ sung 13:52) ---
# Gộp tối đa 100 chuỗi + 5000 ký tự/batch (khớp giới hạn proxy Cloudflare
# + giữ q joined-text vừa phải cho gtx). Batch lỗi liên tiếp 2 lần thì
# tắt batch, về dịch từng chuỗi cho phần còn lại.
BATCH_MAX_STRINGS = 100
BATCH_MAX_CHARS = 5000
_BATCH_FAIL_LATCH = 2

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


async def translate_json_google(json_text, to="vi", credit=True):
    """Dịch bảng key-value bằng Google Translate thật qua fetch() của JS.

    Luồng (batch, mục A): Python (async) gọi `js.pb3_google_translate_batch`
    (gt.js) cho từng BATCH tối đa 100 chuỗi/5000 ký tự — gtx không nhận
    nhiều q/request (chỉ dịch q đầu, đã kiểm chứng live) nên batch gộp N
    chuỗi thành 1 q với marker ⟦1⟧, ⟦2⟧... rồi tách lại; proxy Cloudflare
    (nếu có URL) thì batch thật bằng q mảng của Translation v2. Trước khi
    gửi: khử trùng lặp (dòng đã che giống nhau chỉ dịch 1 lần) + tra từ
    điển offline TRƯỚC (glossary-first) + sắp theo ưu tiên (menu/UI/tên
    item trước, log/mô tả dài sau). Batch lỗi thì dịch lại từng chuỗi cho
    batch đó (qua `js.pb3_google_translate`); batch lỗi liên tiếp 2 lần
    thì tắt batch cho phần còn lại. Không còn sleep 0,4s cố định — tốc độ
    gọi Google do token bucket bên gt.js giới hạn (2 dồn, nạp 1/500ms) +
    backoff khi 429. Đoạn nào Google lỗi thì dùng từ điển offline cho đoạn
    đó rồi dịch tiếp, không crash; lỗi đầu tiên ghi vào network_error.
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
    """
    try:
        from sdk.translate import (OfflineGlossaryProvider, apply_credit,
                                   check_lang, mask_line, placeholders_ok,
                                   unmask_line)

        kv = json.loads(json_text)
        if not isinstance(kv, dict):
            raise ValueError("File JSON phải là object key-value.")
        kv = {str(k): (v if isinstance(v, str)
                       else json.dumps(v, ensure_ascii=False))
              for k, v in kv.items()}
        check_lang(to, allow_auto=False)
        src = "vi" if to == "en" else "en"

        try:
            import js as _js
            _gt = getattr(_js, "pb3_google_translate", None)
            _gt_batch = getattr(_js, "pb3_google_translate_batch", None)
            _cancel_fn = getattr(_js, "pb3_google_cancelled", None)
        except Exception:
            _gt = None
            _gt_batch = None
            _cancel_fn = None

        def _is_cancelled():
            try:
                return bool(_cancel_fn()) if _cancel_fn is not None else False
            except Exception:
                return False

        fallback = OfflineGlossaryProvider()
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
        for batch in batches:
            if _is_cancelled():
                cancelled = True
                break
            texts = [uniques[uid]["masked"] for uid in batch]
            got_batch = False
            if _gt_batch is not None and not quota_hit:
                try:
                    raw = await _gt_batch(texts, src, to)
                    items, mode = _coerce_batch_result(raw)
                    if items is None or len(items) != len(batch):
                        raise ValueError(
                            "batch trả %s chuỗi, cần %d"
                            % (len(items) if items is not None else "?",
                               len(batch)))
                    for uid, val in zip(batch, items):
                        uniq_val[uid] = val
                        uniq_origin[uid] = "google"
                        google_count += len(uniques[uid]["keys"])
                    batch_requests += 1
                    batch_mode = mode
                    batch_failures = 0
                    consec_failures = 0
                    got_batch = True
                except Exception as exc:  # noqa: BLE001 - rơi về từng chuỗi
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
            if got_batch:
                done_segments += sum(len(uniques[uid]["keys"])
                                     for uid in batch)
                _emit("translate", done_segments, total)
                continue
            # --- Rơi về từng chuỗi cho batch này (batch lỗi / không có
            # hàm batch / hết quota thì _gt đã None -> chỉ còn glossary).
            stop_here = False
            for uid in batch:
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
                    except Exception as exc:  # noqa: BLE001
                        if network_error is None:
                            network_error = _gt_error_text(exc)
                        kind = _gt_kind(exc)
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
        return json.dumps({
            "ok": True, "translated": translated, "total": int(len(kv)),
            "untranslated": int(untranslated),
            "untranslated_keys": untranslated_keys[:50],
            "warnings": warnings[:20], "credit_key": credit_key,
            "translated_by": translated_by,
            "google_count": int(google_count),
            "glossary_count": int(glossary_count),
            "network_error": network_error, "cancelled": bool(cancelled),
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
