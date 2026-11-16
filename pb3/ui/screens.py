"""Menu tương tác (tiếng Việt, dòng <= ~44 cột cho iPhone/iSH)."""
from __future__ import annotations

import dataclasses
import inspect
import shutil
import sys
import textwrap
from typing import Callable, List, Optional

from core import VERSION, ish_compat, logging as pb3log
from core.color import paint, paint_first_line, status, term_width, title
from core.errors import PB3Error, ERROR_SPECS, EXIT_OK, error_from_exception, format_error
from core.progress import fmt_bytes
from . import handlers
from .handlers import Feature

InputFn = Callable[[str], str]
PrintFn = Callable[[str], None]


def default_print(s: str = "") -> None:
    try:
        print(s)
    except UnicodeEncodeError:       # terminal không hiển thị được Unicode: thay ký tự, không crash
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(s.encode(enc, "replace").decode(enc))


def wrap(text: str, width: int, indent: str = "") -> List[str]:
    out: List[str] = []
    for para in text.split("\n"):
        out.extend(textwrap.wrap(para, width=width, subsequent_indent=indent,
                                 break_long_words=True) or [""])
    return out


def show_stub(feat: Feature, say: PrintFn, width: int) -> None:
    """Màn hình trạng thái cho chức năng CHƯA có. Tuyệt đối không giả lập kết quả."""
    for ln in wrap("Mục: " + feat.label_vi, width, "  "):
        say(title(ln))
    say(paint("Trạng thái: %s - CHƯA TRIỂN KHAI" % feat.maturity, "warn"))
    for ln in wrap("Dự kiến: " + feat.owner, width, "  "):
        say(ln)
    if feat.note_vi:
        for ln in wrap(feat.note_vi, width):
            say(ln)
    say("PB3 không giả lập chức năng này;")
    say("không có gì được ghi hay thay đổi.")


def show_error(err: PB3Error, say: PrintFn, width: int, log_path=None) -> None:
    lines = wrap(format_error(err, log_path=log_path), width, "  ")
    for i, ln in enumerate(lines):
        say(paint_first_line(ln, "error") if i == 0 else ln)


def _isolated(app, feat: Feature, fn: Callable[[], None], say: PrintFn, width: int) -> None:
    """Chạy một mục; lỗi của mục này KHÔNG làm hỏng menu (đặc tả 12.1)."""
    log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
    try:
        fn()
    except PB3Error as err:
        show_error(err, say, width, log_path=log_path)
    except KeyboardInterrupt:
        say("\nĐã hủy. Quay lại menu.")
    except Exception as exc:  # noqa: BLE001 - cố ý bắt rộng để cô lập mục
        pb3log.log_exception(app.log, exc, "menu:" + feat.key)
        err = PB3Error("E_INTERNAL", type(exc).__name__, where="mục " + feat.key,
                       hint="Chi tiết kỹ thuật nằm trong %s." % (log_path or "logs/"))
        show_error(err, say, width, log_path=log_path)


def run_feature(app, feat: Feature, say: PrintFn, input_fn: InputFn, width: int) -> None:
    def go() -> None:
        mod = handlers.load_module(feat)
        if mod is None:
            show_stub(feat, say, width)
            return
        fn = getattr(mod, "run_interactive", None)
        if fn is None:
            raise PB3Error("E_NOT_IMPLEMENTED", "%s chưa có run_interactive" % feat.module)
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "input_fn" in params:                 # dịch vụ có hỏi đáp: dùng đúng hàm nhập của menu, thanh tiến độ tự ẩn
            kw = {"input_fn": app.pausing_input(input_fn)}
            if "say" in params:
                kw["say"] = say
            fn(app, **kw)
        else:
            fn(app)

    def with_bar() -> None:
        with app.job("menu:" + feat.key):      # mọi chức năng đều chạy dưới thanh tiến độ
            go()
    _isolated(app, feat, with_bar, say, width)


def print_env_report(app, say: PrintFn, width: int, deep: bool = False) -> None:
    name = "Phát hiện môi trường"
    with app.job("menu:env", [(name, 60.0 if deep else 2.0)]) as ov:
        ov.begin(name)
        total = len(ish_compat.TARGET_MODULES)

        def _on_module(mod_name: str, i: int, n: int) -> None:
            ov.update(done=i, total=total, note="đang kiểm module %s (%d/%d)" % (mod_name, i + 1, n))

        rep = ish_compat.detect_environment(app.paths, deep_modules=deep,
                                            on_module=_on_module if deep else None)
    say(ish_compat.format_report(rep, width))


def _bar(app, name: str, fn) -> None:
    with app.job(name):
        fn()


def keepalive_label(app) -> str:
    ka = app.keepalive
    return ka.status().vi() if ka is not None else "TẮT"


def toggle_keepalive(app, say: PrintFn, width: int) -> None:
    """Bật/tắt chạy nền qua /dev/location. Báo trạng thái trung thực, không hứa iOS giữ nền."""
    ka = app.keepalive
    if ka is not None and ka.status().ok:
        st = app.stop_keepalive()
        say(status("pass", "Đã tắt chạy nền: " + (st.vi() if st else "TẮT")))
        return
    say("Bật chạy nền: iOS sẽ hỏi quyền vị trí cho iSH (lần đầu).")
    st = app.start_keepalive()
    for ln in wrap("Trạng thái: " + st.vi(), width, "  "):
        say(ln)
    if st.ok:
        for ln in wrap("Hãy thử khóa màn hình vài phút rồi kiểm tra; "
                       "PB3 không thể tự chứng minh iOS giữ iSH.", width):
            say(ln)


def diagnose_menu(app, say: PrintFn, input_fn: InputFn, width: int, feat: Feature) -> None:
    """Mục 5: phần có sẵn (môi trường, cách ly temp) + phần chưa có (tiếp tục ở Claude 04+)."""
    bar = "-" * width
    while True:
        say("\n" + bar)
        say(title("CHẨN ĐOÁN"))
        say(bar)
        say("[1] Môi trường iSH (diagnose --ish)")
        say("[2] Cách ly tệp tạm dở dang (%d)" % len(app.stale_temps))
        say("[3] Inventory/hex/strings/encoding")
        say("    · " + handlers.status_vi(dataclasses.replace(feat, builtin="")))
        for ln in wrap("[4] Chạy nền qua vị trí: " + keepalive_label(app), width, "    "):
            say(ln)
        say("[0] Quay lại")
        try:
            c = input_fn("Chọn: ").strip()
        except EOFError:
            return
        if c == "0":
            return
        if c == "1":
            _isolated(app, feat, lambda: print_env_report(app, say, width), say, width)
        elif c == "2":
            _isolated(app, feat, lambda: _bar(app, "menu:quarantine",
                                             lambda: _quarantine_screen(app, say, app.pausing_input(input_fn), width)),
                      say, width)
        elif c == "3":
            _isolated(app, feat, lambda: _bar(app, "menu:investigate",
                                             lambda: _diagnose_investigate(app, feat, say, app.pausing_input(input_fn), width)),
                      say, width)
        elif c == "4":
            _isolated(app, feat, lambda: _bar(app, "menu:keepalive",
                                             lambda: toggle_keepalive(app, say, width)), say, width)
        else:
            say(paint("Lựa chọn không hợp lệ.", "warn"))


def _diagnose_investigate(app, feat: Feature, say: PrintFn, input_fn: InputFn, width: int) -> None:
    """Mục [5][3] (F5/F6, đợt rà soát tổng cuối): chọn file điều tra bằng
    picker có phân trang (core.file_dialog.choose_path) rồi điều tra bằng
    services.diagnostics_tool (inventory/hex/strings/encoding, chỉ đọc).
    Module chưa có -> rơi về run_feature để hiện trạng thái 'chưa triển khai'
    (không giả lập)."""
    try:
        from services import diagnostics_tool
    except ImportError:
        run_feature(app, feat, say, input_fn, width)
        return
    from core.file_dialog import choose_path
    start = app.paths.input if app.paths.input.is_dir() else app.paths.root
    try:
        path = choose_path(start, prompt="Chọn file", input_fn=input_fn,
                           print_fn=say, width=width, allow_dir=False)
    except (EOFError, KeyboardInterrupt):
        return
    if path is None:
        say("Đã hủy, chưa chọn file.")
        return
    diagnostics_tool.investigate_file(app, path, input_fn, say, width)


def _quarantine_screen(app, say: PrintFn, input_fn: InputFn, width: int) -> None:
    if not app.stale_temps:
        say("Không có tệp tạm dở dang.")
        return
    say("Tệp tạm dở dang (không xóa, chỉ di chuyển):")
    for p in app.stale_temps[:10]:
        say("  " + app.paths.rel(p)[-(width - 2):])
    if len(app.stale_temps) > 10:
        say("  ... và %d tệp nữa" % (len(app.stale_temps) - 10))
    ans = input_fn("Cách ly vào workspace/temp/_stale? gõ 'co': ").strip().lower()
    if ans != "co":
        say("Không làm gì.")
        return
    moved = app.quarantine_stale()
    say(status("pass", "Đã cách ly %d tệp." % len(moved)))


def banner(say: PrintFn, width: int) -> None:
    """Tiêu đề menu: tên hiển thị là BearTea (Joseph 02:47).

    CHỈ đổi tên hiển thị; tên project (PB3), đường dẫn, tên file, tên zip
    bên trong giữ nguyên."""
    bar = "=" * min(42, width)
    say(bar)
    say(title("BearTea".center(min(42, width)).rstrip()))
    say(bar)


def _status_role(status_text: str):
    """Vai trò màu cho CẢ trạng thái của một mục (tính trên toàn bộ chuỗi,
    không tính từng dòng đã ngắt — tránh một trạng thái nửa xanh nửa vàng)."""
    if "chưa triển khai" in status_text or "chưa có" in status_text or "MỘT PHẦN" in status_text:
        return "warn"
    if "đã nối dịch vụ" in status_text:
        return "pass"
    return None


def render_menu(say: PrintFn, width: int) -> None:
    banner(say, width)
    for no, feat in handlers.MENU:
        for i, ln in enumerate(wrap("[%s] %s" % (no, feat.label_vi), width, "    ")):
            say(ln)
        role = _status_role(handlers.status_vi(feat))
        for ln in wrap("    · " + handlers.status_vi(feat), width, "      "):
            say(paint(ln, role) if role else ln)
    say("[0] Thoát")
    say("=" * min(42, width))


def run_menu(app, input_fn: InputFn = input, say: PrintFn = default_print) -> int:
    width = term_width()
    if app.problems:
        say(paint("Cảnh báo khởi động: %d (xem Chẩn đoán/log)" % len(app.problems), "warn"))
        for p in app.problems[:3]:
            for ln in wrap("! " + p, width, "  "):
                say(paint(ln, "warn"))
        if len(app.problems) > 3:
            say(paint("... còn %d vấn đề nữa (xem Chẩn đoán/log)" % (len(app.problems) - 3), "warn"))
    while True:
        say("")
        render_menu(say, width)
        try:
            choice = input_fn("Chọn: ").strip()
        except (EOFError, KeyboardInterrupt):
            say("")
            return EXIT_OK
        if choice == "0":
            say("Tạm biệt.")
            return EXIT_OK
        item = dict(handlers.MENU).get(choice)
        if item is None:
            say(paint("Lựa chọn không hợp lệ. Gõ số 0-7.", "warn"))
            continue
        if item.key == "diagnose":
            _isolated(app, item, lambda: diagnose_menu(app, say, input_fn, width, item), say, width)
            continue
        run_feature(app, item, say, input_fn, width)
        try:
            input_fn("\n[Enter] quay lại menu ")
        except (EOFError, KeyboardInterrupt):
            return EXIT_OK
