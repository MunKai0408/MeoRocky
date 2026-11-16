"""CLI cùng một entrypoint với menu (đặc tả 12.2).

Mã thoát: 0 thành công, 1 lỗi người dùng, 2 định dạng/chức năng chưa hỗ trợ,
3 an toàn/từ chối ghi, 4 lỗi nội bộ. argparse mặc định thoát bằng 2 khi sai cú pháp;
ở đây đổi thành 1 (lỗi người dùng) cho đúng đặc tả.
"""
from __future__ import annotations

import argparse
import builtins
import sys
import time
from typing import List, Optional

from core import VERSION, ish_compat, logging as pb3log
from core.color import paint_err, paint_first_line
from core.errors import (EXIT_INTERNAL, EXIT_OK, EXIT_UNSUPPORTED, EXIT_USER, PB3Error,
                         error_from_exception, format_error)
from . import handlers
from .screens import default_print, term_width, wrap


class VNParser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write("Lỗi cú pháp: %s\n" % message)
        raise SystemExit(EXIT_USER)


def build_parser(only: Optional[str] = None) -> argparse.ArgumentParser:
    """only=<lệnh>: sau khi dựng cờ chuẩn, cho dịch vụ của lệnh đó thêm cờ riêng (add_arguments)."""
    p = VNParser(prog="python3 menu.py",
                 description="PB3 TOOLKIT v3 - chạy 100%% trong iSH. Không có đối số: mở menu.")
    p.add_argument("-V", "--version", action="version", version="PB3 " + VERSION)
    p.add_argument("--debug", action="store_true", help="giữ thư mục tạm, log DEBUG ra stderr")
    p.add_argument("--keepalive", action="store_true",
                   help="giữ iSH chạy nền qua /dev/location (cần cấp quyền vị trí cho iSH)")
    sub = p.add_subparsers(dest="command", metavar="LỆNH", parser_class=VNParser)

    s = sub.add_parser("extract", help="trích xuất ngôn ngữ")
    s.add_argument("path")
    s.add_argument("--locale", default=None)
    s.add_argument("--out", default=None)
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("translate", help="dịch tự động bảng JSON đã trích xuất")
    s.add_argument("json", help="file JSON key-value đã trích xuất (vd English.json)")

    s = sub.add_parser("mod", help="mod và ghi ngược")
    s.add_argument("path")
    s.add_argument("--file", dest="rel_file", default=None, metavar="REL_PATH")
    s.add_argument("--diff", action="store_true")
    s.add_argument("--allow-write", action="store_true", default=False,
                   help="ghi thật (mặc định chỉ dry-run)")

    s = sub.add_parser("batch", help="xử lý hàng loạt")
    s.add_argument("manifest")
    s.add_argument("--locale", default=None)
    s.add_argument("--jobs", type=int, default=1)
    s.add_argument("--resume", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--only", default=None, metavar="PATTERN")
    s.add_argument("--timeout-game", type=float, default=None, metavar="GIÂY")

    s = sub.add_parser("plugin", help="plugin list|test <id>|new <kind> <id> [--write]|enable <id>|disable <id>")
    s.add_argument("action", choices=["list", "test", "new", "enable", "disable"])
    s.add_argument("args", nargs="*")
    s.add_argument("--write", action="store_true", default=False,
                   help="skeleton ghi được (build/verify, nhiều file); chỉ dùng với 'plugin new format|extractor <id>'")

    s = sub.add_parser("container", help="container list <file>|read <file> <entry> --out <dest>|formats")
    s.add_argument("action", choices=["list", "read", "formats"])
    s.add_argument("path", nargs="?")
    s.add_argument("entry", nargs="?")
    s.add_argument("--out", default=None)
    s.add_argument("--limit", type=int, default=None, help="chỉ in tối đa N entry (container list)")
    s.add_argument("--prefix", default=None, help="chỉ liệt kê entry có tiền tố này (container list)")

    s = sub.add_parser("unity", help="unity inspect|read|patch <file> (UnityFS/SerializedFile)")
    s.add_argument("action", choices=["inspect", "read", "patch"])
    s.add_argument("path")
    s.add_argument("entry", nargs="?")
    s.add_argument("--allow-write", action="store_true", default=False,
                   help="ghi thật (mặc định chỉ dry-run)")

    s = sub.add_parser("support", help="ma trận hỗ trợ")
    s.add_argument("--write", action="store_true")

    s = sub.add_parser("diagnose", help="chẩn đoán")
    s.add_argument("path", nargs="?", metavar="ĐƯỜNG_DẪN",
                   help="file/thư mục cần chẩn đoán (mặc định: thư mục hiện tại)")
    s.add_argument("--verbose", action="store_true")
    s.add_argument("--ish", action="store_true", help="báo cáo môi trường/tương thích iSH")
    s.add_argument("--deep", action="store_true",
                   help="với --ish: thử import lz4/UnityPy trong tiến trình con (có thể >1 phút)")

    s = sub.add_parser("kb", help="kho tri thức game (export/import/forget/prune; prune dọn cache KB)")
    s.add_argument("action", choices=["export", "import", "forget", "prune"])
    s.add_argument("arg", nargs="?")
    s.add_argument("--out", default=None, help="file xuất profile (chỉ dùng với 'kb export')")
    # Lệnh mới đợt tổng cuối: cờ riêng do add_arguments của từng dịch vụ thêm
    # (path/cờ) qua vòng lặp dưới — ở đây chỉ tạo subparser, không thêm
    # positional trùng (thêm 'path' ở đây sẽ làm argparse báo trùng argument).
    s = sub.add_parser("compress", help="nén IPA kiểu WinRAR (solid LZMA)")
    s = sub.add_parser("decompress", help="khôi phục IPA từ archive nén PB3")
    s = sub.add_parser("font", help="công cụ font 4 chế độ (quét/gộp/thay font)")

    # Cờ riêng của dịch vụ (vd --object/--old/--new của unity/mod) phải thấy
    # trong --help ngay cả khi chưa gõ tên lệnh (X7): thêm cho MỌI lệnh có
    # add_arguments, không chỉ lệnh đang gõ.
    targets = [only] if only and only in handlers.COMMANDS else list(handlers.COMMANDS)
    for name in targets:
        try:
            mod = handlers.load_module(handlers.COMMANDS[name])
            extra = getattr(mod, "add_arguments", None) if mod is not None else None
            if extra is not None:
                extra(sub.choices[name])
        except Exception:      # dịch vụ lỗi không được làm hỏng parser; lỗi sẽ lộ ở bước chạy
            pass
    return p


def _announce(plugin: str, maturity: str, reason: str) -> None:
    """Đặc tả 12.2: mọi lệnh in 'plugin được dùng + maturity + lý do'."""
    sys.stderr.write(paint_err("plugin: %s | maturity: %s | lý do: %s"
                               % (plugin, maturity, reason), "dim") + "\n")


def _not_implemented(cmd: str, feat: handlers.Feature, log_path=None) -> int:
    err = PB3Error("E_NOT_IMPLEMENTED", "lệnh '%s' (%s) chưa có dịch vụ %s"
                   % (cmd, feat.label_vi, feat.module),
                   hint="Dự kiến: %s. Xem CURRENT_STATE.md." % feat.owner)
    _announce("(không có)", feat.maturity, "chức năng chưa triển khai; không giả lập")
    sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
    return err.exit_code


def _diagnose_ish(app, ns) -> int:
    _announce("core.ish_compat (hạ tầng)", "n/a", "kiểm tra môi trường, không phải plugin")
    if ns.deep:
        if app.overall is not None:
            app.overall.expect(60.0)
        sys.stderr.write(paint_err("Đang thử import lz4/UnityPy trong tiến trình con (có thể mất >1 phút)...",
                                   "info") + "\n")
    rep = ish_compat.detect_environment(app.paths, deep_modules=ns.deep)
    default_print(ish_compat.format_report(rep, term_width()))
    return EXIT_OK if (rep.python_ok and rep.workspace_writable) else EXIT_UNSUPPORTED


def dispatch(app, ns) -> int:
    cmd = ns.command
    if cmd == "diagnose" and ns.ish:
        return _diagnose_ish(app, ns)
    feat = handlers.COMMANDS[cmd]
    mod = handlers.load_module(feat)
    log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
    if mod is None:
        return _not_implemented(cmd, feat, log_path=log_path)
    fn = getattr(mod, "run_cli", None)
    if fn is None:
        return _not_implemented(cmd, feat, log_path=log_path)
    return int(fn(app, ns))


def _run_with_bar(app, ns, boot_s: float) -> int:
    """Chạy lệnh dưới thanh tiến độ TỔNG (Khởi động -> Chạy lệnh) 0-100% + còn lại.
    Thanh chỉ hiện khi stderr là tty; lỗi/hủy -> xuống dòng, không ép 100%."""
    ov = app.make_overall("cli:" + str(ns.command), [("Khởi động", 1.0), ("Chạy lệnh", 5.0)])
    ov.mark_done("Khởi động", boot_s)
    app.overall = ov
    real_input = builtins.input
    builtins.input = app.pausing_input(real_input)      # lệnh có hỏi đáp: thanh ẩn khi chờ gõ
    try:
        with ov.guard_stdout():
            ov.begin("Chạy lệnh")
            rc = dispatch(app, ns)
    except BaseException:
        ov.abort()
        raise
    finally:
        builtins.input = real_input
        app.overall = None
    if rc == 0:
        ov.finish()
    else:
        ov.abort()
    return rc


def main_cli(argv: List[str]) -> int:
    from core.app import bootstrap
    only = next((a for a in argv if a in handlers.COMMANDS), None)
    try:
        ns = build_parser(only).parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)
    if not ns.command:
        build_parser().print_help()
        return EXIT_USER
    t_boot = time.monotonic()
    try:
        app = bootstrap(debug=True if ns.debug else None, keepalive=True if ns.keepalive else None)
    except PB3Error as err:
        sys.stderr.write(format_error(err) + "\n")
        return err.exit_code
    from core import color as _color
    _color.configure_from_config(app.config)   # ui.color (auto/on/off) + NO_COLOR
    boot_s = time.monotonic() - t_boot
    with app:
        if ns.keepalive and app.keepalive is not None:
            sys.stderr.write("Chạy nền (vị trí): %s\n" % app.keepalive.status().vi())
        log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
        try:
            return _run_with_bar(app, ns, boot_s)
        except PB3Error as err:
            sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
            return err.exit_code
        except BrokenPipeError:       # đầu ra bị đóng (vd. '| head'): không phải lỗi nội bộ
            raise
        except KeyboardInterrupt:
            sys.stderr.write(paint_first_line(
                format_error(PB3Error("E_CANCELLED"), log_path=log_path), "error") + "\n")
            return EXIT_USER
        except Exception as exc:  # noqa: BLE001
            pb3log.log_exception(app.log, exc, "cli:" + str(ns.command))
            err = PB3Error("E_INTERNAL", type(exc).__name__, where="lệnh " + str(ns.command),
                           hint="Chi tiết kỹ thuật nằm trong %s." % (log_path or "logs/session-%s.log" % app.session_id))
            sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
            return EXIT_INTERNAL
