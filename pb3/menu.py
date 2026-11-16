#!/usr/bin/env python3
"""PB3 TOOLKIT v3.1 - điểm vào duy nhất (menu tương tác + CLI).

Chạy trong iSH:   cd /root/pb3 && python3 menu.py   (xem README.txt)
File này chỉ điều phối; không chứa logic parse/patch (đặc tả 2.3).
"""
import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("[E_UNSUPPORTED_ON_ISH] Cần Python >= 3.8, đang có %s.\n"
                     "Trong iSH: apk add python3\n" % sys.version.split()[0])
    sys.exit(4)

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _fatal(msg, exc):
    sys.stderr.write("[E_INTERNAL] %s\n" % msg)
    sys.stderr.write("Chi tiết: %s: %s\n" % (type(exc).__name__, str(exc)[:200]))
    sys.stderr.write("Kiểm tra: đủ thư mục core/ ui/ config/ trong %s? Python: %s\n"
                     % (ROOT, sys.version.split()[0]))
    return 4


def _quiet_stdout():
    """Đầu ra bị đóng (vd. '| head'): thoát êm, không in lỗi lúc flush."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from ui import cli, screens
        from core.app import bootstrap
        from core.errors import PB3Error, format_error
    except Exception as exc:  # noqa: BLE001 - báo lỗi môi trường chính xác, không traceback
        return _fatal("Không nạp được mã PB3", exc)
    if argv:
        try:
            return cli.main_cli(argv)
        except BrokenPipeError:
            _quiet_stdout()
            return 0
    try:
        app = bootstrap()
    except PB3Error as err:
        sys.stderr.write(format_error(err) + "\n")
        return err.exit_code
    except Exception as exc:  # noqa: BLE001
        return _fatal("Khởi tạo thất bại", exc)
    from core import color as _color
    _color.configure_from_config(app.config)   # ui.color (auto/on/off) + NO_COLOR
    with app:
        try:
            return screens.run_menu(app)
        except BrokenPipeError:
            _quiet_stdout()
            return 0
        except Exception as exc:  # noqa: BLE001
            from core.logging import log_exception
            log_exception(app.log, exc, "menu")
            return _fatal("Lỗi nội bộ trong menu (đã ghi logs/)", exc)


if __name__ == "__main__":
    sys.exit(main())
