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
thiếu wheel etcpak), mọi tính năng cần threading (batch song song),
Google Translate qua urllib (urllib không gọi mạng được trong Pyodide).
Dịch Google trên web đi đường khác: `gt.js` dùng fetch() trong Worker,
Python gọi hàm JS đó qua module `js` từ hàm async `translate_json_google`
(xem dưới) — Python async await được Promise JS nên event loop vẫn chạy
và nút Hủy vẫn nhận được giữa đợt dịch.
"""

import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
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
