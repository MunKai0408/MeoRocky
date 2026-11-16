"""Dịch vụ trích xuất ngôn ngữ (Claude 07): lệnh `extract` và menu mục 2.

- `extract <path> [--locale L] [--out DIR] [--dry-run] [--file VPATH]
  [--resume] [--preset FILE] [--save-preset FILE]`:
  chạy pipeline 7 bước (sdk.extraction), xuất JSON key-value theo locale
  (English.json/Chinese.json/...) + extraction_report.txt/.json vào
  workspace/output/<GameName>/ (hoặc --out). Mặc định không ghi đè nguồn.
  Thêm `--translate <lang>`: dịch tự động các file JSON vừa xuất bằng
  Google Translate (cần mạng; lỗi mạng thì dùng từ điển offline), ghi
  `<tên>.<lang>.json` cùng thư mục. Mặc định gắn credit 'Việt Hoá Bởi
  Bear' vào 1 entry tiêu đề/version trong file đã dịch (--no-credit để
  tắt); credit vào game cùng lúc ghi ngược (`--apply`, dry-run mặc định).
- `extract --apply <json-đã-sửa> [--allow-write]`:
  ghi ngược JSON đã sửa vào file nguồn qua plugin build/verify; dry-run
  mặc định (chỉ báo sẽ đổi gì), --allow-write mới ghi thật (versioned).
- Menu [2] tương tác: hỏi đường dẫn, hỏi chọn file khi có nhiều ứng viên
  ngang điểm, in tóm tắt + cảnh báo.

Không giả lập: không ứng viên -> E_NO_CANDIDATE; locale không có ->
E_LOCALE_NOT_FOUND kèm danh sách locale có sẵn; engine không nhận diện
được vẫn trích xuất bằng parser độc lập engine (đặc tả 7.2).
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable, List, Optional

from core import VERSION
from core import safe_io
from core.color import paint, status, title, wrap_text
from core.errors import EXIT_OK, PB3Error
from core.limits import Limits
from core.progress import SteadyProgress, step_pcts
from sdk import discovery as disc
from sdk import extraction as ext
from ui.screens import default_print, term_width

_PRESET_VERSION = 1

# Tên bước pipeline cho thanh tiến độ (sdk.extraction báo progress_fn theo
# từng bước với số lượng thật đã xong/tổng).
_PHASE_LABELS = {
    "inventory": "liệt kê file",
    "layers": "mở lớp container",
    "candidates": "tìm ứng viên",
    "sniff": "dò nội dung",
    "probe": "kiểm tra ứng viên",
    "export": "xuất bảng",
}


class _ExtractProgressAdapter:
    """progress_fn(bước, đã_xong, tổng) cho ExtractionRequest (luật #152).

    Có thanh Overall (menu/CLI đều có): mỗi bước thành một stage riêng
    (ov.begin) rồi quét dần từng 1% trong bước (ov.update + render), ghi
    chú hiện số lượng thật đã xong/tổng. Bước "inventory" chưa biết tổng
    trước nên chỉ báo số lượng thật (không bịa %): stage tiến theo thời
    gian như bước "mờ" (không chạm 100% trước khi xong) kèm ghi chú số
    entry đã liệt kê, vẽ lại tối đa 1 lần/giây. Không có Overall thì dùng
    SteadyProgress riêng cho từng bước (cùng mẫu một dòng)."""

    def __init__(self, app):
        self._app = app
        self._ov = getattr(app, "overall", None) if app is not None else None
        self._phase = None            # type: Optional[str]
        self._shown_pct = 0
        self._steady = None           # type: Optional[SteadyProgress]
        self._last_render = 0.0

    def __call__(self, phase: str, done: int, total: Optional[int]) -> None:
        label = _PHASE_LABELS.get(phase, phase)
        if phase != self._phase:
            self._phase = phase
            self._shown_pct = 0
            self._last_render = 0.0
            if self._ov is not None:
                self._ov.begin(label)
            else:
                if self._steady is not None:
                    self._steady.finish()
                self._steady = SteadyProgress(label, total)
        if self._ov is not None:
            if total:
                new_pct = int(min(1.0, max(0.0, done / total)) * 100 + 1e-9)
                for pct in step_pcts(self._shown_pct, new_pct):
                    interp = int(round(pct / 100.0 * total))
                    self._ov.update(frac=pct / 100.0,
                                    note="%s %d/%d" % (label, interp, total))
                    self._ov.render()
                self._shown_pct = new_pct
            else:
                now = time.monotonic()
                self._ov.update(note="%s: %d entry" % (label, done))
                if now - self._last_render >= 1.0:
                    self._last_render = now
                    self._ov.render()
            return
        if self._steady is not None:
            self._steady.update(done, note=label)

    def finish(self) -> None:
        if self._steady is not None:
            self._steady.finish()


def _registry(app, only_ids=None):
    from registry.isolation import import_guard
    from registry.registry import build_registry
    return build_registry(str(app.paths.root), app.config, import_guard=import_guard,
                          only_ids=only_ids)


def _out_dir(app, ns) -> str:
    if getattr(ns, "out", None):
        return str(ns.out)
    return str(app.paths.output / ext._game_name(ns.path))


def _load_preset(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise PB3Error("E_CORRUPT", "preset hỏng: %s" % type(exc).__name__, where=path)
    if not isinstance(data, dict) or data.get("version") != _PRESET_VERSION:
        raise PB3Error("E_CORRUPT", "preset không đúng khuôn dạng (cần version %d)" % _PRESET_VERSION,
                       where=path)
    return data


def _save_preset(path: str, source: str, file: Optional[str], locale: Optional[str]) -> None:
    blob = json.dumps({"version": _PRESET_VERSION, "source": source,
                       "file": file, "locale": locale},
                      ensure_ascii=False, indent=1).encode("utf-8")
    safe_io.atomic_write_bytes(path, blob)


def _print_candidates(groups, out) -> None:
    for grp in groups:
        out("  locale %s: %s" % (grp.get("locale") or "?",
                                 grp.get("reason", "")))
        for i, opt in enumerate(grp.get("options", []), 1):
            out("    [%d] %s @ %s (%.2f)" % (i, opt["plugin"], opt["path"], opt["score"]))
            for r in opt.get("reasons", []):
                out("        - " + r)


def _print_summary(result: ext.ExtractionResult, out=print, width=None) -> None:
    w = width or term_width()
    out(title("engine: %s (%s, tin cậy %.2f)"
              % (result.engine, result.engine_level, result.engine_confidence)))
    for reason in result.engine_reasons:
        for ln in wrap_text("  lý do: " + reason, w):
            out(ln)
    if result.decision == "ask":
        out(paint("CHƯA TRÍCH XUẤT — nhiều ứng viên ngang điểm, cần chọn một:", "warn"))
        _print_candidates(result.candidates, out)
        out("Chạy lại với --file <đường dẫn> để chọn, hoặc chọn trong menu tương tác.")
        return
    if result.decision == "none":
        out(paint("Không trích xuất được file nào.", "warn"))
    for f in result.files:
        line = ("  - %s [%s, locale %s]: %d entry (%d ok, %d rỗng, %d trùng, %d chưa parse)"
                % (f.path, f.plugin, f.locale or "?", f.entries, f.ok, f.empty,
                   f.conflicts, f.unparsed))
        for ln in wrap_text(line, w):
            out(ln)
        if f.source_kind == "raw_scan":
            out(paint("      dò chuỗi thô, chưa xác minh — có thể có false positive", "warn"))
        if not f.editable:
            out("      chỉ đọc (không ghi ngược được)")
        if f.exported:
            out("      -> %s" % f.exported)
        for warn in f.warnings:
            out(paint("      cảnh báo: " + warn, "warn"))
    for s in result.skipped:
        out(paint("  bỏ qua %s: %s" % (s.get("path", "?"), s.get("reason", "?")), "warn"))
    if result.candidates:
        # Nhóm người dùng bỏ qua (đã trích phần đã chọn).
        out(paint("Còn nhóm chưa chọn:", "warn"))
        _print_candidates(result.candidates, out)
        out("Chạy lại với --file <đường dẫn> để chọn nhóm còn lại.")
    for e in result.errors:
        out(paint("  lỗi %s: [%s] %s" % (e.get("path", "?"), e.get("code", "?"),
                                         e.get("message", "?")), "error"))
    if result.generated_keys:
        out("  key sinh (%d): %s" % (len(result.generated_keys),
                                      ", ".join(result.generated_keys[:8])))
    out(status("pass" if result.ok else "fail",
               "tổng: %d entry, %d file, %.1f giây%s"
               % (result.total_entries, len(result.files), result.elapsed_s,
                  " (dùng checkpoint)" if result.checkpoint_used else "")))
    if result.out_dir and result.files:
        out("  output: %s" % result.out_dir)


def _cli_ask_fn():
    """Hỏi chọn ứng viên khi chạy CLI tương tác (stdin là TTY mới hỏi)."""

    def ask(options: List[dict]) -> Optional[int]:
        if not sys.stdin.isatty():
            return None
        print("Nhiều ứng viên ngang điểm, chọn một:")
        for i, opt in enumerate(options, 1):
            print("  [%d] %s @ %s (%.2f)" % (i, opt["plugin"], opt["path"], opt["score"]))
        try:
            raw = input("Chọn [1-%d] (trống để bỏ qua): " % len(options)).strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            return None
        try:
            idx = int(raw) - 1
        except ValueError:
            return None
        return idx if 0 <= idx < len(options) else None

    return ask


def _run_extract(app, ns) -> int:
    preset = _load_preset(ns.preset) if getattr(ns, "preset", None) else None
    reg = _registry(app)
    out_dir = None if ns.dry_run else _out_dir(app, ns)
    req = ext.ExtractionRequest(
        source=ns.path, registry=reg, limits=getattr(app, "limits", None) or Limits(),
        locale=getattr(ns, "locale", None), pin_file=getattr(ns, "file", None),
        out_dir=out_dir, dry_run=bool(ns.dry_run),
        resume=bool(getattr(ns, "resume", False)), preset=preset,
        ask_fn=_cli_ask_fn(), tempdir=str(app.paths.temp),
        root=str(app.paths.root),
        progress_fn=_ExtractProgressAdapter(app))
    result = ext.run_extraction(req)
    _print_summary(result)
    if result.decision == "none":
        for e in result.errors:
            if e.get("code") == "E_LOCALE_NOT_FOUND":
                raise PB3Error("E_LOCALE_NOT_FOUND", e["message"])
        raise PB3Error("E_NO_CANDIDATE",
                       "không tìm được file ngôn ngữ để trích xuất",
                       hint="Kiểm tra lại file/thư mục đầu vào; xem report để biết file nào bị bỏ qua.")
    if getattr(ns, "save_preset", None) and result.files:
        first = result.files[0]
        _save_preset(ns.save_preset, str(ns.path), first.path, first.locale)
        print(status("pass", "đã lưu preset: %s" % ns.save_preset))
    if getattr(ns, "translate", None) and result.decision == "done":
        _translate_after_extract(app, ns, result)
    return EXIT_OK


def _translate_after_extract(app, ns, result) -> None:
    """Dịch các file JSON vừa trích xuất (`extract --translate <lang>`)."""
    from services import translate_tool as tr
    target = str(ns.translate)
    source_lang = str(getattr(ns, "translate_from", "auto") or "auto")
    provider_name = str(getattr(ns, "translate_provider", "google") or "google")
    credit = not bool(getattr(ns, "no_credit", False))
    out_dir = result.out_dir
    if not out_dir:
        return
    for f in result.files:
        if not f.exported:
            continue
        src_path = os.path.join(out_dir, f.exported)
        try:
            out_path, res = tr.translate_exported_file(
                src_path, to=target, source_lang=source_lang,
                provider_name=provider_name, app=app, credit=credit)
        except PB3Error as exc:
            print(paint("  dịch %s lỗi [%s]: %s" % (f.exported, exc.code, exc.message_vi),
                        "error"))
            continue
        tr.print_summary(res, out_path)


def _run_apply(app, ns) -> int:
    edited = str(ns.apply)
    report_path = os.path.join(os.path.dirname(os.path.abspath(edited)),
                               ext.REPORT_JSON)
    out_dir = str(app.paths.output)
    reg = _registry(app)
    res = ext.apply_edits(edited_json=edited, report_path=report_path, registry=reg,
                          limits=getattr(app, "limits", None) or Limits(),
                          out_dir=out_dir, allow_write=bool(getattr(ns, "allow_write", False)),
                          tempdir=str(app.paths.temp),
                          progress_fn=_ExtractProgressAdapter(app))
    print(status("pass", "ghi ngược: %s [%s]" % (res["source"], res["plugin"])))
    print("  đổi %d key; kích thước %d -> %d byte"
          % (res["changed"], res["size_before"], res["size_after"]))
    for key in res["skipped_keys"]:
        print(paint("  bỏ qua key không có trong file gốc: %s" % key, "warn"))
    if res["dry_run"]:
        print(paint("KHÔNG ghi file: đây là dry-run. Thêm --allow-write để ghi thật.", "warn"))
        return EXIT_OK
    print(status("pass", "đã ghi output: %s (không ghi đè nguồn)" % res["written"]))
    return EXIT_OK


def run_cli(app, ns) -> int:
    if getattr(ns, "apply", None):
        return _run_apply(app, ns)
    return _run_extract(app, ns)


def add_arguments(subparser) -> None:
    """Cờ riêng cho `extract` (gọi từ ui/cli.py)."""
    subparser.add_argument("--file", default=None, metavar="VPATH",
                           help="ép chọn đúng file nguồn (vpath trong container)")
    subparser.add_argument("--apply", default=None, metavar="JSON",
                           help="ghi ngược JSON đã sửa vào file nguồn (cần report cùng đợt)")
    subparser.add_argument("--allow-write", action="store_true", default=False,
                           help="ghi thật khi --apply (mặc định chỉ dry-run)")
    subparser.add_argument("--resume", action="store_true", default=False,
                           help="dùng checkpoint: bỏ qua file đã trích xuất xong")
    subparser.add_argument("--preset", default=None, metavar="FILE",
                           help="dùng preset đã lưu (file + locale)")
    subparser.add_argument("--save-preset", default=None, metavar="FILE",
                           help="lưu lựa chọn lần này thành preset")
    subparser.add_argument("--translate", default=None, metavar="LANG",
                           help="dịch tự động các file JSON vừa xuất sang ngôn ngữ này "
                                "(vd vi); cần mạng, lỗi mạng thì dùng từ điển offline")
    subparser.add_argument("--translate-from", default="auto", metavar="LANG",
                           help="ngôn ngữ nguồn khi --translate (mặc định auto)")
    subparser.add_argument("--translate-provider", default="google",
                           choices=["google", "offline", "fake"],
                           help="provider dịch khi --translate (mặc định google; "
                                "fake chỉ dùng cho test)")
    subparser.add_argument("--no-credit", action="store_true", default=False,
                           help="không thêm dòng credit 'Việt Hoá Bởi Bear' "
                                "khi --translate (mặc định gắn vào 1 entry "
                                "tiêu đề/version)")


# ------------------------------------------------------------------ tương tác
def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None):
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else default_print
    out(title("TRÍCH XUẤT NGÔN NGỮ (đa engine)"))
    out("quét file game, trích chuỗi localization ra JSON key-value.")
    while True:
        path = safe_io.normalize_typed_path(
            ask("Đường dẫn file/thư mục (trống để thoát): ").strip())
        if not path:
            return
        locale = ask("Locale (en, zh, all, trống = preset en+zh): ").strip() or None
        if locale == "all":
            locale = "all"
        reg = _registry(app)
        out_dir = str(app.paths.output / ext._game_name(path))

        def ask_choice(options: List[dict]) -> Optional[int]:
            out("Nhiều ứng viên ngang điểm, chọn một:")
            for i, opt in enumerate(options, 1):
                out("  [%d] %s @ %s (%.2f)" % (i, opt["plugin"], opt["path"], opt["score"]))
            raw = ask("Chọn [1-%d] (trống để bỏ qua): " % len(options)).strip()
            if not raw:
                return None
            try:
                idx = int(raw) - 1
            except ValueError:
                return None
            return idx if 0 <= idx < len(options) else None

        req = ext.ExtractionRequest(
            source=path, registry=reg, limits=getattr(app, "limits", None) or Limits(),
            locale=locale, out_dir=out_dir, ask_fn=ask_choice,
            tempdir=str(app.paths.temp), root=str(app.paths.root),
            progress_fn=_ExtractProgressAdapter(app))
        try:
            result = ext.run_extraction(req)
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        _print_summary(result, out)
        if result.decision != "done":
            continue
        lang = ask("Dịch tự động sang ngôn ngữ nào? (vd vi, trống để thôi): ").strip() or None
        if lang:
            from services import translate_tool as tr
            # Test: PB3_TRANSLATE_FAKE=1 dùng provider giả (không gọi mạng).
            provider_name = "fake" if os.environ.get("PB3_TRANSLATE_FAKE") else "google"
            for f in result.files:
                if not f.exported:
                    continue
                src_path = os.path.join(out_dir, f.exported)
                try:
                    out_path, res = tr.translate_exported_file(
                        src_path, to=lang, source_lang="auto",
                        provider_name=provider_name, app=app)
                except PB3Error as exc:
                    out("[%s] %s" % (exc.code, exc.message_vi))
                    continue
                tr.print_summary(res, out_path, out)
        ans = ask("Ghi ngược JSON đã sửa? (nhập đường dẫn file JSON, trống để thôi): ").strip()
        if not ans:
            continue
        try:
            res = ext.apply_edits(edited_json=ans,
                                  report_path=os.path.join(out_dir, ext.REPORT_JSON),
                                  registry=reg, limits=getattr(app, "limits", None) or Limits(),
                                  out_dir=str(app.paths.output),
                                  allow_write=False, tempdir=str(app.paths.temp),
                                  progress_fn=_ExtractProgressAdapter(app))
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        out("Dry-run ghi ngược: %s [%s], đổi %d key"
            % (res["source"], res["plugin"], res["changed"]))
        ok = ask("Ghi thật? (y/N): ").strip().lower()
        if ok != "y":
            out("Đã hủy, chưa ghi gì.")
            continue
        try:
            res = ext.apply_edits(edited_json=ans,
                                  report_path=os.path.join(out_dir, ext.REPORT_JSON),
                                  registry=reg, limits=getattr(app, "limits", None) or Limits(),
                                  out_dir=str(app.paths.output),
                                  allow_write=True, tempdir=str(app.paths.temp),
                                  progress_fn=_ExtractProgressAdapter(app))
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        out("Đã ghi: %s" % res["written"])
