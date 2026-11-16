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
thiếu wheel etcpak), mọi tính năng cần threading (batch CHỈ chạy tuần tự,
jobs=1 — không có chế độ song song trên web), Google Translate qua urllib
(urllib không gọi mạng được trong Pyodide).
Dịch Google trên web đi đường khác: `gt.js` dùng fetch() trong Worker,
Python gọi hàm JS đó qua module `js` từ hàm async `translate_json_google`
(xem dưới) — Python async await được Promise JS nên event loop vẫn chạy
và nút Hủy vẫn nhận được giữa đợt dịch.

Mod (menu [1] iSH) / Batch (menu [4]) / KB (menu [3]) / ma trận hỗ trợ:
xem mod_patch / batch_run / kb_* / support_matrix ở cuối file — đều gọi
API tầng sdk (sdk.unity.apply_patch, sdk.batch.BatchRunner, sdk.kb.KB,
sdk.support_matrix.generate), không dùng services/ (gắn với menu chữ).
KB sống trong phiên (file SQLite ở /tmp/kb trong RAM của Worker — tải lại
trang là mất, trừ khi tải file KB về bằng kb_export).
"""

import asyncio
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


def unity_inspect(path):
    """Inspect Unity: bundle (.unity3d) hoặc file .assets độc lập.

    Dùng parser thuần của PB3 (sdk/unity.py) + backend NATIVE (binding lz4
    nếu loadPackage('lz4') đã chạy, nếu không thì lz4pure). Không dùng
    UnityPy (không cài được trên Pyodide).
    """
    try:
        from sdk import unity as u

        _check_size(path)
        name = os.path.basename(path)
        data = open(path, "rb").read()
        backend = u.UnityBackend("NATIVE")
        out = {"ok": True, "bundle": False, "files": []}
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
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def extract(path):
    """Trích xuất text đầy đủ (pipeline 7 bước của PB3).

    Kết quả ghi ra /tmp/pb3out rồi nén thành /tmp/extract.zip để JS tải về.
    progress_fn báo tiến độ thật từng bước (inventory/layers/candidates/
    sniff/probe/export) về JS qua _emit.
    """
    try:
        from sdk import extraction as ext

        _check_size(path)
        out_dir = "/tmp/pb3out"
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)

        def _pf(step, done, total):
            _emit("extract:" + str(step), done, total)

        req = ext.ExtractionRequest(
            source=path, registry=_reg, tempdir=str(_app.paths.temp),
            root=WEB_ROOT, out_dir=out_dir, progress_fn=_pf)
        r = ext.run_extraction(req)
        files = [{"path": f.path, "plugin": f.plugin,
                  "entries": int(f.entries), "locale": f.locale,
                  "exported": f.exported} for f in r.files]
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
            "errors": list(r.errors or []), "zip_path": zpath,
            "zip_bytes": os.path.getsize(zpath),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


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


async def translate_json_google(json_text, to="vi", credit=True):
    """Dịch bảng key-value bằng Google Translate thật qua fetch() của JS.

    Luồng: Python (async) gọi `js.pb3_google_translate` (gt.js) cho từng
    đoạn text đã che placeholder — 1 chuỗi/request, cách nhau 0.4 giây
    (luật bản iSH); gt.js tự thử lại tối đa 3 lần với backoff khi 429/5xx.
    Đoạn nào Google lỗi (mất mạng, CORS, 429 hết retry, trang Sorry) thì
    dùng từ điển offline cho đoạn đó rồi dịch tiếp, không crash; lỗi đầu
    tiên ghi vào network_error. Chỉ dịch VALUE, giữ KEY; placeholder
    ({0}, %s), thẻ, xuống dòng được che/khôi phục như bản iSH; credit
    'Việt Hoá Bởi Bear' gắn như translate_json. Nút Hủy: Python kiểm tra
    `js.pb3_google_cancelled()` trước mỗi chuỗi (await fetch nhường lại
    event loop nên worker vẫn nhận được message hủy) — hủy thì trả kết
    quả từng phần với cancelled=True.

    Kết quả thêm: translated_by ("google"/"mixed"/"offline"), google_count,
    glossary_count, network_error, cancelled. Hàm này là async — worker.js
    await Promise trả về trước khi gửi kết quả về main thread.
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
            _cancel_fn = getattr(_js, "pb3_google_cancelled", None)
        except Exception:
            _gt = None
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
        # Ngắt mạch: Google lỗi liên tiếp 3 chuỗi (vd IP bị 429+Sorry) thì
        # dừng gọi Google cho phần còn lại, dùng thẳng từ điển offline —
        # khỏi bắt người dùng chờ mỗi chuỗi thử lại 3 lần vô ích.
        consec_failures = 0
        for idx, (key, line_no, masked, _tokens) in enumerate(segments):
            if _is_cancelled():
                cancelled = True
                break
            if idx:
                await asyncio.sleep(0.4)
            val = None
            origin = ""
            if _gt is not None:
                try:
                    got = await _gt(masked, src, to)
                    val = str(got)
                    origin = "google"
                    consec_failures = 0
                except Exception as exc:  # noqa: BLE001 - rơi về offline
                    if network_error is None:
                        network_error = _gt_error_text(exc)
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
            seg_out[(key, line_no)] = (val, origin)
            if origin == "google":
                google_count += 1
            elif origin == "glossary":
                glossary_count += 1
            _emit("translate", idx + 1, total)

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
            elif n_missing:
                translated[key] = new_value
                untranslated_keys.append(key)
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
        return json.dumps({
            "ok": True, "translated": translated, "total": int(len(kv)),
            "untranslated": int(untranslated),
            "untranslated_keys": untranslated_keys[:50],
            "warnings": warnings[:20], "credit_key": credit_key,
            "translated_by": translated_by,
            "google_count": int(google_count),
            "glossary_count": int(glossary_count),
            "network_error": network_error, "cancelled": bool(cancelled),
        }, ensure_ascii=False)
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
    """Giải nén archive .tar.xz do compress_file tạo ra."""
    try:
        from sdk.compress import decompress_archive

        _check_size(path)
        dest = "/tmp/restored.bin"
        if os.path.exists(dest):
            os.remove(dest)
        rep = decompress_archive(path, dest, force=True)
        d = rep.to_dict()
        d.update({"ok": True, "dest": dest})
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


def batch_run(manifest_text, dry_run=True, checkpoint_json=None, resume=False):
    """Chạy một lô game theo manifest (JSON list hoặc CSV text, cùng dạng
    bản iSH: path/mode/locale/old/new/file/object/profile_override).

    CHẠY TUẦN TỰ (jobs=1) — Pyodide trên GitHub Pages không có threading,
    không có chế độ song song trên web. Mỗi game cô lập: lỗi một game
    không dừng cả lô. dry_run=True mặc định (chạy thử trước, chưa ghi).
    checkpoint_json: chuỗi checkpoint lần chạy trước (UI lưu ở localStorage
    hoặc file) + resume=True để bỏ qua game đã xong. Trả summary +
    checkpoint mới (UI lưu lại) + zip kết quả (/tmp/batch.zip: summary.json,
    checkpoint.json, index.csv, failures.txt, từng thư mục game).
    """
    try:
        from sdk.batch import BatchRunner, parse_manifest

        items = parse_manifest(manifest_text)
        for it in items:
            it.path = _resolve_batch_path(it.path)
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
        try:
            kb = _get_kb()
        except Exception:
            kb = None  # KB lỗi thì batch vẫn chạy (như bản iSH: kb=None chạy được)
        summary = runner.run(
            items, out_dir=out_dir, jobs=1, resume=bool(resume),
            dry_run=bool(dry_run), kb=kb,
            progress_cb=lambda d, t, name: _emit("batch", d, t))
        ck_path = os.path.join(out_dir, "checkpoint.json")
        checkpoint = None
        if os.path.isfile(ck_path):
            with open(ck_path, "r", encoding="utf-8") as fh:
                checkpoint = fh.read()
        zpath = "/tmp/batch.zip"
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, fns in os.walk(out_dir):
                dirs[:] = [d for d in dirs if d != ".staging"]
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, out_dir))
        return json.dumps({
            "ok": True, "jobs": 1, "sequential": True,
            "dry_run": bool(dry_run), "resumed": bool(resume),
            "summary": summary.to_dict(), "checkpoint": checkpoint,
            "zip_path": zpath, "zip_bytes": os.path.getsize(zpath),
            "note": "Batch web chạy TUẦN TỰ từng game (không có threading "
                    "trên GitHub Pages, không có chế độ song song).",
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ======================================================================
# KB — menu [3] iSH (sdk/kb.py). SQLite TRONG PHIÊN.
# ======================================================================
_KB_PATH = "/tmp/kb/pb3_kb.sqlite"
_kb = None


def _get_kb():
    """KB của phiên: file SQLite trong RAM của Worker (/tmp/kb, MEMFS).
    Tải lại trang là mất — chỉ giữ được khi tải file KB về (kb_export)
    rồi nhập lại bằng kb_import trong phiên mới."""
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
    """Danh sách game đã biết trong KB của phiên."""
    try:
        kb = _get_kb()
        return json.dumps({
            "ok": True, "games": kb.list_games(),
            "size_bytes": int(kb.size_bytes()),
            "session_only": True,
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
    đóng kết nối cuối — file tải về mới đầy đủ, tự chứa). File này chỉ
    lưu được tri thức TRONG PHIÊN: tải lại trang thì KB trống lại, trừ
    khi nhập lại file đã tải về bằng kb_import (nút 'Nhập KB' trên tab
    KB)."""
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
