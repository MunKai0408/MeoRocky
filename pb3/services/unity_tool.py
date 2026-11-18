"""Dịch vụ Unity (Claude 05): lệnh `unity` và `mod` (menu mục 1).

- `unity inspect <file>`: nhận diện engine + bản đồ dữ liệu (planner).
- `unity read <file> <entry> [--object <path_id>] --out <dest>`: đọc entry
  / object ra file (không ghi đè file đã có).
- `unity patch <file> [--entry <vpath>] --object <path_id> --old <s> --new <s>
  [--mode PATCH_INPLACE|REBUILD_SECTION] [--confirm] [--out <dest>]`: patch
  TextAsset; đơn vị đầu ra nhỏ nhất (bundle, KHÔNG đóng gói lại IPA);
  mặc định ghi vào workspace/output/ với versioning.
- `mod <path>`: inspect/plan; có --old/--new thì dry-run (không --allow-write)
  hoặc ghi thật (--allow-write).

Đường dẫn lồng dùng '::' (Claude 04): file IPA + entry
"Payload/Game.app/Data/level.unity3d::CAB-...". Không ghi đè file nguồn.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from core import VERSION
from core import safe_io
from core.color import paint, status, term_width, title, wrap_text
from core.errors import EXIT_OK, PB3Error
from core.limits import Limits
from core.progress import SteppedReporter
from sdk import unity as unity_sdk
from sdk.containers import NestedVFS, open_vfs
from sdk.vfs import vpath_split


def _usage(what, usage):
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage,
                    message_vi="Tham số lệnh Unity không hợp lệ.")


def _backend(app, mode):
    limits = getattr(app, "limits", None) or Limits()
    return unity_sdk.UnityBackend(mode or "AUTO", limits)


def _read_file_bytes(path, limits):
    p = safe_io.check_readable_input(path, limits, allow_dir=False)
    with open(str(p), "rb") as fh:
        return fh.read()


def _head_tail(s: str, n: int = 28) -> str:
    """Cắt đường dẫn dài giữ ĐẦU + CUỐI (X6): 'Payload/9Kings.../level.unity3d'."""
    if len(s) <= n:
        return s
    return s[:13] + "…" + s[-(n - 14):]


# ------------------------------------------------------------------ cache plan
# Cache kết quả inspect (plan) xuống đĩa để lần mod/patch sau khỏi quét lại
# cả file 200MB. Khóa = sha256 toàn bộ file + kích thước + chế độ backend
# + phiên bản PB3 + phiên bản khuôn dạng cache: file đổi một byte cũng
# trượt cache. Chỉ cache file >= 1MB (file nhỏ quét đã nhanh hơn đọc cache).
PLAN_CACHE_FILENAME = "plan_cache.json"
PLAN_CACHE_VERSION = 2  # đổi khi khuôn dạng plan lưu cache thay đổi
PLAN_CACHE_MIN_BYTES = 1024 * 1024
PLAN_CACHE_MAX_ENTRIES = 8
PLAN_CACHE_MAX_BYTES = 32 * 1024 * 1024
# Cache v1 (đọc để tương thích ngược): hàng là dict {"container": ...}.
PLAN_CACHE_VERSIONS_READABLE = (1, 2)


def _plan_cache_path(app):
    """Đường dẫn file cache plan (workspace/cache/plan_cache.json)."""
    paths = getattr(app, "paths", None)
    cache_dir = getattr(paths, "cache", None)
    if cache_dir is None:
        return None
    return Path(str(cache_dir)) / PLAN_CACHE_FILENAME


def _plan_to_dict(plan):
    """Hàng plan lưu dạng list gọn [container, entry, kind, object_type,
    path_id, offset, size, guess, name] (v2): file cache nhỏ hơn ~40% so
    với dict lặp 9 khóa mỗi hàng (v1), json.loads + dựng lại plan nhanh
    hơn rõ rệt ở plan 73k dòng; _plan_from_dict vẫn đọc được hàng dict v1."""
    return {
        "verdict": {"engine": plan.verdict.engine, "level": plan.verdict.level,
                    "reasons": list(plan.verdict.reasons)},
        "rows": [[r.container, r.entry, r.kind, r.object_type, r.path_id,
                  r.offset, r.size, r.guess, r.name] for r in plan.rows],
        "warnings": list(plan.warnings),
        "decisions": list(plan.decisions),
        "partial": bool(plan.partial),
    }


def _plan_from_dict(d):
    v = d["verdict"]
    verdict = unity_sdk.EngineVerdict(engine=v["engine"], level=v["level"],
                                      reasons=list(v.get("reasons", [])))
    rows = []
    for r in d["rows"]:
        if isinstance(r, (list, tuple)):   # v2: hàng gọn theo thứ tự cố định
            (container, entry, kind, object_type, path_id, offset, size,
             guess, name) = r
            rows.append(unity_sdk.PlanRow(container, entry, kind, object_type,
                                          path_id, offset, size, guess, name))
        else:                              # v1: hàng dict (cache cũ)
            rows.append(unity_sdk.PlanRow(container=r["container"], entry=r["entry"],
                                          kind=r["kind"], object_type=r["object_type"],
                                          path_id=r["path_id"], offset=r["offset"],
                                          size=r["size"], guess=r["guess"],
                                          name=r.get("name", "")))
    return unity_sdk.UnityPlan(verdict=verdict, rows=rows,
                               warnings=list(d.get("warnings", [])),
                               decisions=list(d.get("decisions", [])),
                               partial=bool(d.get("partial", False)))


def _load_plan_cache(app):
    """Đọc cache plan; file hỏng/sai phiên bản -> cache rỗng (không raise)."""
    path = _plan_cache_path(app)
    if path is None:
        return {}
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") not in PLAN_CACHE_VERSIONS_READABLE:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _cache_blob(entries):
    return json.dumps({"version": PLAN_CACHE_VERSION, "entries": entries},
                      ensure_ascii=False).encode("utf-8")


def _save_plan_cache(app, entries):
    """Ghi cache plan (ghi nguyên tử; lỗi thì bỏ qua, không làm hỏng lệnh)."""
    path = _plan_cache_path(app)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_bytes(str(path), _cache_blob(entries))
    except Exception:
        pass


def _evict_oldest(entries):
    oldest = min(entries, key=lambda k: entries[k].get("last_used", 0)
                 if isinstance(entries.get(k), dict) else 0)
    del entries[oldest]


def _cached_plan(app, data, name, backend, on_entry=None):
    """Trả (plan, đã_dùng_cache). File < 1MB quét trực tiếp, không cache.

    Lần đầu vẫn quét đủ (cache không làm lần đầu nhanh hơn); lần sau trên
    cùng file không đổi thì trả ngay, không đọc lại bundle nào."""
    if len(data) < PLAN_CACHE_MIN_BYTES:
        return unity_sdk.plan_bytes(data, name=name, backend=backend,
                                    on_entry=on_entry), False
    key = hashlib.sha256(data).hexdigest()
    mode = getattr(backend, "mode", "AUTO")
    entries = _load_plan_cache(app)
    ent = entries.get(key)
    if isinstance(ent, dict) and ent.get("size") == len(data) \
            and ent.get("backend") == mode \
            and ent.get("pb3_version") == VERSION \
            and isinstance(ent.get("plan"), dict):
        try:
            plan = _plan_from_dict(ent["plan"])
        except (KeyError, TypeError, AttributeError):
            plan = None
        if plan is not None:
            # hit: trả ngay, KHÔNG ghi lại file cache (ghi lại 17MB JSON chỉ
            # để cập nhật last_used thì mất gần bằng quét lại; LRU theo lần
            # ghi gần nhất vẫn đủ tốt với 8 mục).
            return plan, True
    plan = unity_sdk.plan_bytes(data, name=name, backend=backend,
                                on_entry=on_entry)
    entries[key] = {"size": len(data), "backend": mode,
                    "pb3_version": VERSION, "last_used": time.time(),
                    "plan": _plan_to_dict(plan)}
    while len(entries) > PLAN_CACHE_MAX_ENTRIES:
        _evict_oldest(entries)
    while len(_cache_blob(entries)) > PLAN_CACHE_MAX_BYTES and len(entries) > 1:
        _evict_oldest(entries)
    _save_plan_cache(app, entries)
    return plan, False


def _inspect_progress(app, done, total, name, stepper=None):
    """Tiến độ quét từng entry: thanh Overall khi stderr là TTY (mặc định);
    khi chuyển hướng ra file thì thanh bị tắt, nên in 1 dòng ra stderr để
    `wc -l`/`tail` vẫn thấy máy đang chạy tới đâu (không in ra stdout để
    plan không lẫn dòng tiến độ). `stepper` (SteppedReporter) quét dần từng
    1% thay vì nhảy một bước dài mỗi entry (luật #152) và là kênh tiến độ
    duy nhất khi có mặt (bar hoặc dòng SteadyProgress riêng); không có
    stepper thì báo một lần như cũ (để test cũ + đường gọi khác không đổi)
    kèm dòng "quét i/N" ra stderr khi thanh tắt."""
    if stepper is not None:
        stepper.report(done, total, note=name)
        return
    app.report_progress(done / total if total else 1.0, note=name)
    ov = getattr(app, "overall", None)
    if ov is not None and ov.enabled:
        return
    try:
        sys.stderr.write("quét %d/%d: %s\n" % (done, total, name))
        sys.stderr.flush()
    except Exception:
        pass


def _plan_row_lines(row, w):
    """Các dòng đã wrap của 1 PlanRow (định dạng dùng chung cho bản đủ
    _print_plan và bản gọn _print_plan_compact)."""
    where = row.container
    if row.entry:
        where += "::" + row.entry
    obj = ""
    if row.path_id is not None:
        obj = " path_id=%d" % row.path_id
    # In loại object cho dòng assets (vd TextAsset): trước đây chuỗi này
    # chỉ vô tình có mặt nhờ dòng backend khi UnityPy thiếu, nên `unity
    # inspect` mất chữ TextAsset khi UnityPy có mặt (iSH/venv).
    line = ("  %-28s %-12s%s %7d byte  %s%s%s" % (
        _head_tail(where), row.kind, obj, row.size, row.guess,
        ("  " + row.name) if row.name else "",
        ("  " + row.object_type) if row.kind == "assets" else ""))
    out_lines = []
    for j, ln in enumerate(wrap_text(line, max(10, w - 2))):
        shown = ("  " + ln) if j > 0 else ln
        out_lines.append(paint(shown, "warn")
                         if row.object_type == "unsupported" else shown)
    return out_lines


def _print_plan(plan, out=print, width=None):
    """In plan gọn theo chiều rộng terminal (X6): mọi dòng wrap theo width,
    đường dẫn dài giữ đầu + cuối. width=None -> term_width() (30..44 cột)."""
    w = width or term_width()
    v = plan.verdict
    for ln in wrap_text("engine: %s (%s)" % (v.engine, v.level), w):
        out(title(ln))
    for reason in v.reasons:
        for ln in wrap_text("  lý do: " + reason, w):
            out(ln)
    for d in plan.decisions:
        for ln in wrap_text("  backend: " + d, w):
            out(ln)
    if not plan.rows:
        out("(không có entry/object nào)")
        return
    out(title("bản đồ dữ liệu: %d dòng" % len(plan.rows)))
    for row in plan.rows:
        for ln in _plan_row_lines(row, w):
            out(ln)
    for wmsg in plan.warnings:
        for ln in wrap_text("  cảnh báo: " + wmsg, w):
            out(paint(ln, "warn"))
    if getattr(plan, "partial", False):
        for ln in wrap_text("  kết quả: CHỈ PARSE ĐƯỢC MỘT PHẦN (E_PARSE_PARTIAL) — "
                            "có entry lỗi/bỏ qua, xem các dòng 'unsupported'.", w):
            out(paint(ln, "warn"))


_COMPACT_MAX_TEXT_ASSETS = 50


def _print_plan_compact(plan, out=print, width=None, source=None):
    """Bản gọn của plan cho menu tương tác: verdict + tổng số dòng + chỉ
    các hàng TextAsset (thứ duy nhất menu patch được), tối đa 50 hàng.

    Menu in đủ 30.000 dòng plan lên màn hình điện thoại thì không dùng
    được (kết xuất terminal trên iSH ~27 dòng/giây, in đủ plan mất hàng
    chục phút); dữ liệu plan không đổi, plan đầy đủ vẫn xem bằng
    `unity inspect <file>`. Hàng TextAsset dùng cùng định dạng với
    _print_plan (qua _plan_row_lines)."""
    w = width or term_width()
    v = plan.verdict
    for ln in wrap_text("engine: %s (%s)" % (v.engine, v.level), w):
        out(title(ln))
    for reason in v.reasons:
        for ln in wrap_text("  lý do: " + reason, w):
            out(ln)
    for d in plan.decisions:
        for ln in wrap_text("  backend: " + d, w):
            out(ln)
    text_assets = [r for r in plan.rows
                   if r.kind == "assets" and r.object_type == "TextAsset"]
    unsupported = sum(1 for r in plan.rows if r.object_type == "unsupported")
    out(title("bản đồ dữ liệu: %d dòng (%d TextAsset%s)" % (
        len(plan.rows), len(text_assets),
        (", %d lỗi" % unsupported) if unsupported else "")))
    for row in text_assets[:_COMPACT_MAX_TEXT_ASSETS]:
        for ln in _plan_row_lines(row, w):
            out(ln)
    if len(text_assets) > _COMPACT_MAX_TEXT_ASSETS:
        hint = ("unity inspect %s" % source) if source else "unity inspect"
        out("  … và %d TextAsset khác (xem đủ: %s)."
            % (len(text_assets) - _COMPACT_MAX_TEXT_ASSETS, hint))
    if not text_assets:
        out("  (không có TextAsset nào để patch)")
    for wmsg in plan.warnings:
        for ln in wrap_text("  cảnh báo: " + wmsg, w):
            out(paint(ln, "warn"))
    if getattr(plan, "partial", False):
        for ln in wrap_text("  kết quả: CHỈ PARSE ĐƯỢC MỘT PHẦN (E_PARSE_PARTIAL) — "
                            "có entry lỗi/bỏ qua, xem các dòng 'unsupported'.", w):
            out(paint(ln, "warn"))


def _resolve_vpath(file_bytes, entry, backend, limits):
    """Đi theo vpath 'a::b::c' từ bytes gốc.

    Trả (kind, data, req_entry, label):
    - kind "bundle": data = bytes bundle, req_entry = entry trong bundle.
    - kind "assets": data = bytes SerializedFile (độc lập hoặc trong container).
    """
    if not entry:
        det = unity_sdk.detect_bundle(file_bytes)
        if det is not None:
            return "bundle", file_bytes, None, "bundle"
        try:
            unity_sdk.parse_serialized(file_bytes, limits, where="assets")
            return "assets", file_bytes, None, "assets"
        except PB3Error:
            raise _usage("file không phải bundle/assets; cần --entry chỉ tới "
                         "bundle/assets bên trong",
                         "unity patch <file> --entry <vpath> --object <id> ...")
    segments = vpath_split(entry)
    current = file_bytes
    label = ""
    for seg in segments[:-1]:
        nxt = _read_segment(current, seg, backend, limits)
        current = nxt
        label = seg
    last = segments[-1]
    det = unity_sdk.detect_bundle(current)
    if det is not None:
        return "bundle", current, last, label or "bundle"
    # current là container: đọc entry cuối
    try:
        data = _read_segment(current, last, backend, limits)
    except PB3Error as exc:
        if exc.code == "E_TOO_LARGE":
            raise  # giu nguyen ma loi that, dung doi thanh "khong tim thay"
        raise _usage("không tìm thấy %r trong container/bundle" % last,
                     "unity patch <file> --entry <vpath> --object <id> ...")
    det_last = unity_sdk.detect_bundle(data)
    if det_last is not None:
        raise _usage("entry %r là bundle UnityFS v%d; chỉ tới entry bên trong "
                     "bằng '%s::<tên entry>'" % (last, det_last["version"], last),
                     "unity patch <file> --entry '%s::<tên entry>' --object <id> ..."
                     % last)
    try:
        unity_sdk.parse_serialized(data, limits, where=last)
        return "assets", data, None, last
    except PB3Error:
        raise _usage("entry %r không phải SerializedFile" % last,
                     "unity patch <file> --entry <vpath> --object <id> ...")


def _read_segment(data, seg, backend, limits):
    """Đọc một đoạn vpath từ bytes (container ZIP hoặc bundle UnityFS)."""
    det = unity_sdk.detect_bundle(data)
    if det is not None:
        bundle = unity_sdk.parse_bundle(data, backend, limits, where="bundle")
        return bundle.get_entry(seg)
    vfs = open_vfs(data, limits=limits, source_name="<bytes>")
    try:
        return vfs.read(seg, None)
    finally:
        close = getattr(vfs, "close", None)
        if close is not None:
            close()


def _default_output_rel(input_path, kind, label):
    """Đường dẫn output mặc định (6.6): <GameName>/<tên đơn vị nhỏ nhất>.

    `label` là đoạn vpath chứa đơn vị cần ghi (vd "Payload/.../level.unity3d"
    cho bundle trong IPA, "resources.assets" cho assets trong container);
    với file độc lập thì dùng tên file gốc. Không bao giờ xuất cả IPA.
    """
    stem = Path(str(input_path)).stem
    game = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stem) or "game"
    name = None
    if label and label not in ("bundle", "assets"):
        name = label.rsplit("/", 1)[-1]
    if not name:
        name = Path(str(input_path)).name
    return "%s/%s" % (game, name)


def _write_output(app, rel, data, explicit_out):
    if explicit_out:
        dest = Path(explicit_out)
        safe_io.atomic_write_bytes(dest, data, exclusive=True)
        return dest
    return unity_sdk.write_patch_output(app.paths.output, rel, data)


# ------------------------------------------------------------------ lệnh unity
def cmd_inspect(app, ns):
    backend = _backend(app, getattr(ns, "backend", None))
    data = _read_file_bytes(ns.path, backend.limits)
    stepper = SteppedReporter(app, "quét")
    plan, cached = _cached_plan(
        app, data, str(ns.path), backend,
        on_entry=lambda done, total, name: _inspect_progress(
            app, done, total, name, stepper=stepper))
    if cached:
        print(paint("dùng plan đã lưu (cache) — file không đổi.", "dim"))
    _print_plan(plan)
    return EXIT_OK


def cmd_read(app, ns):
    backend = _backend(app, getattr(ns, "backend", None))
    data = _read_file_bytes(ns.path, backend.limits)
    entry = getattr(ns, "entry", None)
    # entry bỏ trống = chính file đó (bundle/assets độc lập)
    segments = vpath_split(entry) if entry else []
    current = data
    for seg in segments:
        current = _read_segment(current, seg, backend, backend.limits)
    obj_id = getattr(ns, "object", None)
    if obj_id is not None:
        # --object <path_id>: TextAsset thì in script ra stdout; object khác
        # cần --out (dữ liệu nhị phân).
        sf = unity_sdk.parse_serialized(current, backend.limits, where=entry)
        for pid, name, script in sf.text_assets():
            if pid == int(obj_id):
                print(script)
                return EXIT_OK
        if not getattr(ns, "out", None):
            raise _usage("object path_id=%s không phải TextAsset; cần --out"
                         % obj_id, "unity read <file> <entry> --object <id> --out <dest>")
        current = sf.object_bytes(int(obj_id))
        print("đã đọc object path_id=%s (%d byte)" % (obj_id, len(current)))
    if not getattr(ns, "out", None):
        raise _usage("thiếu --out", "unity read <file> <entry> --out <dest>")
    safe_io.atomic_write_bytes(ns.out, current, exclusive=True)
    print(status("pass", "đã ghi %d byte: %s" % (len(current), ns.out)))
    return EXIT_OK


def cmd_patch(app, ns):
    backend = _backend(app, getattr(ns, "backend", None))
    obj_id = getattr(ns, "object", None)
    if obj_id is None:
        raise _usage("thiếu --object <path_id>",
                     "unity patch <file> --object <id> --old <s> --new <s>")
    if getattr(ns, "old", None) is None or getattr(ns, "new", None) is None:
        raise _usage("thiếu --old/--new",
                     "unity patch <file> --object <id> --old <s> --new <s>")
    data = _read_file_bytes(ns.path, backend.limits)
    kind, unit_data, req_entry, label = _resolve_vpath(
        data, getattr(ns, "entry", None), backend, backend.limits)
    mode = getattr(ns, "mode", None) or "PATCH_INPLACE"
    req = unity_sdk.PatchRequest(
        data=unit_data, kind=kind, entry=req_entry, path_id=int(obj_id),
        old_text=ns.old, new_text=ns.new, write_mode=mode,
        confirm=bool(getattr(ns, "confirm", False)),
        backend=backend, temp_dir=str(app.paths.temp),
        from_container=getattr(ns, "entry", None) is not None
        and unity_sdk.detect_bundle(data) is None)
    result = unity_sdk.apply_patch(req)
    rep = result.report
    print(status("pass", "patch OK (%s, chế độ %s, backend %s)"
                 % (result.unit, rep.write_mode_used, backend.mode)))
    print("  đích: %s" % rep.target)
    print("  chuỗi: %d -> %d byte (tìm thấy %d lần, thay lần đầu)"
          % (rep.old_script_len, rep.new_script_len, rep.occurrences))
    print("  kích thước đơn vị: %d -> %d byte" % (rep.size_before, rep.size_after))
    for w in rep.warnings:
        print(paint("  cảnh báo: " + w, "warn"))
    for d in rep.backend_decisions:
        print("  backend: " + d)
    for d in result.verify_details:
        print("  verify: " + d)
    if not getattr(ns, "allow_write", False):
        print(paint("KHÔNG ghi file: đây là dry-run. Thêm --allow-write để ghi thật.", "warn"))
        return EXIT_OK
    rel = _default_output_rel(ns.path, kind, label)
    dest = _write_output(app, rel, result.output_bytes, getattr(ns, "out", None))
    print(status("pass", "đã ghi output: %s (không ghi đè nguồn)" % dest))
    return EXIT_OK


def run_cli(app, ns):
    cmd = getattr(ns, "command", "unity")
    if cmd == "mod":
        return _run_mod_cli(app, ns)
    action = getattr(ns, "action", None)
    if action == "inspect":
        return cmd_inspect(app, ns)
    if action == "read":
        return cmd_read(app, ns)
    if action == "patch":
        return cmd_patch(app, ns)
    raise _usage("hành động lạ: %r" % (action,), "unity inspect|read|patch <file>")


# ------------------------------------------------------------------ lệnh mod
def _run_mod_cli(app, ns):
    """`mod <path>`: plan mặc định; có --old/--new thì dry-run hoặc ghi thật."""
    backend = _backend(app, getattr(ns, "backend", None))
    data = _read_file_bytes(ns.path, backend.limits)
    old = getattr(ns, "old", None)
    new = getattr(ns, "new", None)
    if old is None or new is None:
        # Đường plan của `mod` trước đây không báo tiến độ quét gì cả (chỉ
        # `unity inspect` có on_entry): gắn cùng stepper để quét IPA lớn
        # cũng hiện thanh thật thay vì im lặng tới lúc in plan.
        stepper = SteppedReporter(app, "quét")
        plan, cached = _cached_plan(
            app, data, str(ns.path), backend,
            on_entry=lambda done, total, name: _inspect_progress(
                app, done, total, name, stepper=stepper))
        if cached:
            print(paint("dùng plan đã lưu (cache) — file không đổi.", "dim"))
        _print_plan(plan)
        print(paint("Muốn patch: thêm --file <entry> --object <id> --old <s> --new <s> "
                    "(dry-run mặc định; --allow-write để ghi thật).", "info"))
        return EXIT_OK
    obj_id = getattr(ns, "object", None)
    if obj_id is None:
        raise _usage("thiếu --object <path_id>",
                     "mod <path> --file <entry> --object <id> --old <s> --new <s>")
    kind, unit_data, req_entry, label = _resolve_vpath(
        data, getattr(ns, "rel_file", None), backend, backend.limits)
    mode = getattr(ns, "mode", None) or "PATCH_INPLACE"
    req = unity_sdk.PatchRequest(
        data=unit_data, kind=kind, entry=req_entry, path_id=int(obj_id),
        old_text=old, new_text=new, write_mode=mode,
        confirm=bool(getattr(ns, "confirm", False)),
        backend=backend, temp_dir=str(app.paths.temp),
        from_container=getattr(ns, "rel_file", None) is not None
        and unity_sdk.detect_bundle(data) is None)
    result = unity_sdk.apply_patch(req)
    rep = result.report
    print(status("pass", "dry-run patch OK (%s, chế độ %s): %s"
                 % (result.unit, rep.write_mode_used, rep.target)))
    print("  chuỗi: %d -> %d byte; đơn vị: %d -> %d byte"
          % (rep.old_script_len, rep.new_script_len, rep.size_before, rep.size_after))
    for w in rep.warnings:
        print(paint("  cảnh báo: " + w, "warn"))
    for d in result.verify_details:
        print("  verify: " + d)
    if not getattr(ns, "allow_write", False):
        print(paint("Chưa ghi file (dry-run). Thêm --allow-write để ghi thật.", "warn"))
        return EXIT_OK
    rel = _default_output_rel(ns.path, kind, label)
    dest = _write_output(app, rel, result.output_bytes, getattr(ns, "out", None))
    print(status("pass", "đã ghi output: %s (không ghi đè nguồn)" % dest))
    return EXIT_OK


def add_arguments(subparser):
    """Thêm cờ riêng cho `unity` và `mod` (gọi từ ui/cli.py)."""
    subparser.add_argument("--backend", default=None, choices=["AUTO", "LIBRARY", "NATIVE"])
    subparser.add_argument("--object", default=None, metavar="PATH_ID")
    subparser.add_argument("--old", default=None)
    subparser.add_argument("--new", default=None)
    subparser.add_argument("--mode", default=None,
                           choices=["PATCH_INPLACE", "REBUILD_SECTION", "REBUILD_ALL"])
    subparser.add_argument("--confirm", action="store_true")
    subparser.add_argument("--out", default=None)


# ------------------------------------------------------------------ tương tác
def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None):
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else print
    out(title("MOD VÀ GHI NGƯỢC (Unity)"))
    out("chỉ patch TextAsset, đơn vị nhỏ nhất.")
    while True:
        path = safe_io.normalize_typed_path(
            ask("Đường dẫn file (trống để thoát): ").strip())
        if not path:
            return
        try:
            backend = _backend(app, None)
            data = _read_file_bytes(path, backend.limits)
            stepper = SteppedReporter(app, "quét")
            plan, cached = _cached_plan(
                app, data, path, backend,
                on_entry=lambda done, total, name: _inspect_progress(
                    app, done, total, name, stepper=stepper))
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        if cached:
            out(paint("dùng plan đã lưu (cache) — file không đổi.", "dim"))
        _print_plan_compact(plan, out, source=path)
        ans = ask("Patch một TextAsset? (y/N): ").strip().lower()
        if ans != "y":
            continue
        entry = ask("Entry (vpath '::', trống nếu file là .assets): ").strip() or None
        obj_id = ask("path_id của TextAsset: ").strip()
        old = ask("Chuỗi cũ: ")
        new = ask("Chuỗi mới: ")
        try:
            kind, unit_data, req_entry, label = _resolve_vpath(
                data, entry, backend, backend.limits)
            req = unity_sdk.PatchRequest(
                data=unit_data, kind=kind, entry=req_entry, path_id=int(obj_id),
                old_text=old, new_text=new, backend=backend,
                temp_dir=str(app.paths.temp))
            result = unity_sdk.apply_patch(req)
        except (PB3Error, ValueError) as exc:
            if isinstance(exc, PB3Error):
                out("[%s] %s" % (exc.code, exc.message_vi))
            else:
                out("path_id phải là số.")
            continue
        rep = result.report
        out("Dry-run OK: %s" % rep.target)
        out("chuỗi %d -> %d byte; đơn vị %d -> %d byte"
            % (rep.old_script_len, rep.new_script_len,
               rep.size_before, rep.size_after))
        for w in rep.warnings:
            out("  cảnh báo: " + w)
        ok = ask("Ghi output thật? (y/N): ").strip().lower()
        if ok != "y":
            out("Đã hủy, chưa ghi gì.")
            continue
        rel = _default_output_rel(path, kind, label)
        dest = _write_output(app, rel, result.output_bytes, None)
        out("Đã ghi: %s" % dest)
