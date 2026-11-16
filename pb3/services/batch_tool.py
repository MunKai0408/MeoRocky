"""Dịch vụ xử lý hàng loạt (Claude 09): lệnh `batch` và menu mục 4.

- `batch <manifest.csv|json> [--locale L] [--jobs N] [--resume] [--dry-run]
  [--limit N] [--only PATTERN] [--timeout-game S] [--out DIR] [--allow-write]`:
  chạy sdk.batch.BatchRunner; dry-run mặc định (chỉ ghi thật khi
  --allow-write); mỗi game cô lập, checkpoint sau mỗi game, resume bỏ qua
  game đã xong; không bao giờ đóng gói lại IPA (chỉ xuất đơn vị tối thiểu).
  --resume không kèm --out thì tự tìm lần chạy cũ gần nhất có checkpoint
  khớp manifest; --out chỉ rõ thư mục output (dùng cùng --resume để tiếp
  tục đúng lần chạy cũ). Mã thoát 0 khi mọi game ok/bỏ qua; 1 khi có game
  lỗi (xem failures.txt).
- Menu [4] tương tác: hỏi đường dẫn manifest, locale/chế độ mặc định,
  xác nhận dry-run; chạy rồi in thư mục output + tóm tắt.

KB (sdk/kb.py) là import lười: chưa có module thì batch vẫn chạy, chỉ
không dùng/không ghi tri thức (ghi rõ trong tóm tắt).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from core.color import paint, title
from core.errors import EXIT_OK, EXIT_USER, PB3Error
from sdk import batch as batch_sdk
from ui.screens import default_print

_PLUGIN = "sdk.batch.BatchRunner + sdk.extraction/sdk.unity"
_MATURITY = "M2"


def _announce():
    """Đặc tả 12.2: in 'plugin được dùng + maturity + lý do' (mẫu ui/cli.py)."""
    from ui.cli import _announce as announce
    announce(_PLUGIN, _MATURITY,
             "batch: mỗi game cô lập, checkpoint sau mỗi game, dry-run mặc định")


def _open_kb(app):
    """Mở KB nếu module sdk.kb đã có; chưa có thì None (batch vẫn chạy)."""
    try:
        from sdk.kb import KB
    except ImportError:
        return None
    kb_dir = getattr(getattr(app, "paths", None), "kb", None)
    if kb_dir is None:
        return None
    try:
        return KB(str(Path(str(kb_dir)) / "pb3_kb.sqlite"))
    except Exception:
        return None


def _print_summary(summary: batch_sdk.BatchSummary, out=print) -> None:
    out(title("BATCH: %d game (%d đạt, %d lỗi, %d bỏ qua, %d chưa xong)"
              % (summary.total, summary.ok, summary.failed,
                 summary.skipped, summary.incomplete)))
    out("  output: %s" % summary.out_dir)
    if getattr(summary, "resumed_from", None):
        out("  tiếp tục từ: %s (bỏ qua game đã xong)" % summary.resumed_from)
    out("  thời gian: %.1f giây (jobs=%d, %s)"
        % (summary.elapsed_s, summary.jobs,
           "dry-run" if summary.dry_run else "ghi thật"))
    over = summary.budgets.get("over_budget") or []
    if over:
        out(paint("  chưa đạt ngân sách §9.2: %s" % ", ".join(over), "warn"))
    for g in summary.games:
        line = "  - %s [%s]: %s" % (os.path.basename(g.path.rstrip("/")), g.mode, g.status)
        if g.error_code:
            line += " [%s] %s" % (g.error_code, g.error_message)
        if g.kb_note:
            line += " (%s)" % g.kb_note
        if g.note:
            line += " — %s" % g.note
        out(line)
        if g.game_dir:
            out("      -> %s" % g.game_dir)
    if summary.failed:
        out(paint("  xem lỗi từng game: %s"
                  % os.path.join(summary.out_dir, batch_sdk.FAILURES_FILENAME), "warn"))


def run_cli(app, ns) -> int:
    _announce()
    manifest = str(ns.manifest)
    allow_write = bool(getattr(ns, "allow_write", False))
    dry_run = bool(getattr(ns, "dry_run", False)) or not allow_write
    kb = _open_kb(app)
    if kb is None:
        print(paint("KB chưa có (sdk/kb.py): batch chạy không dùng tri thức.", "warn"))
    # F2 (đợt tổng cuối): tiêm registry từ tầng services để sdk/batch.py
    # không còn import lười services (xem sdk/batch.py::_get_registry).
    from services import language_tool
    runner = batch_sdk.BatchRunner(app=app, registry=language_tool._registry(app))
    resume = bool(getattr(ns, "resume", False))
    try:
        summary = runner.run(
            manifest,
            out_dir=getattr(ns, "out", None),
            jobs=int(getattr(ns, "jobs", 1) or 1),
            resume=resume,
            dry_run=dry_run,
            limit=getattr(ns, "limit", None),
            only=getattr(ns, "only", None),
            timeout_game=getattr(ns, "timeout_game", None),
            kb=kb)
    finally:
        close = getattr(kb, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    _print_summary(summary)
    if resume and not getattr(summary, "resumed_from", None):
        print(paint("không tìm thấy lần chạy cũ để tiếp tục — chạy mới từ đầu.",
                    "warn"))
    if summary.failed:
        return EXIT_USER
    return EXIT_OK


def add_arguments(subparser) -> None:
    """Cờ riêng cho `batch` (gọi từ ui/cli.py; các cờ khác đã có sẵn)."""
    subparser.add_argument("--allow-write", action="store_true", default=False,
                           help="ghi thật (mặc định chỉ dry-run)")
    subparser.add_argument("--out", default=None, metavar="DIR",
                           help="thư mục output (mặc định tạo mới theo giờ; "
                                "dùng cùng --resume để tiếp tục đúng lần chạy cũ)")


# ------------------------------------------------------------------ tương tác
def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None):
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else default_print
    out(title("XỬ LÝ HÀNG LOẠT (batch)"))
    out("plugin: %s | maturity: %s | batch: mỗi game cô lập, checkpoint sau mỗi game."
        % (_PLUGIN, _MATURITY))
    out("chạy nhiều game theo manifest CSV/JSON, không đóng gói lại IPA.")
    while True:
        manifest = ask("Đường dẫn manifest CSV/JSON (trống để thoát): ").strip()
        if not manifest:
            return
        try:
            items = batch_sdk.parse_manifest(manifest)
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        out("%d game trong manifest." % len(items))
        locale = ask("Locale mặc định cho game chưa ghi locale (trống = giữ nguyên): ").strip() or None
        mode = ask("Chế độ mặc định cho game chưa ghi mode (extract/mod, trống = extract): ").strip().lower() or "extract"
        if mode not in ("extract", "mod"):
            out("Chế độ lạ: chỉ extract/mod.")
            continue
        for it in items:
            if locale and not it.locale:
                it.locale = locale
            if not it.mode:
                it.mode = mode
        dry = ask("Chạy thử (dry-run, không ghi file)? (Y/n): ").strip().lower()
        dry_run = dry != "n"
        if not dry_run:
            ok = ask("Ghi thật (mod ghi patch, extract ghi JSON)? (y/N): ").strip().lower()
            if ok != "y":
                out("Đã hủy, chưa ghi gì.")
                continue
        kb = _open_kb(app)
        # F2 (đợt tổng cuối): tiêm registry từ tầng services (như run_cli).
        from services import language_tool
        runner = batch_sdk.BatchRunner(app=app, registry=language_tool._registry(app))
        try:
            summary = runner.run(items, dry_run=dry_run, kb=kb)
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            continue
        finally:
            close = getattr(kb, "close", None) if kb is not None else None
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        _print_summary(summary, out)
        if summary.failed:
            out(paint("Có game lỗi — xem failures.txt trong thư mục output.", "warn"))
