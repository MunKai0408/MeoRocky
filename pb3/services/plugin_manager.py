"""Mục 3 menu / lệnh `plugin` (đặc tả 4.8, 12.2).

ĐÃ CÓ (Claude 03): `plugin list` - bảng id, kind, engine, maturity, trạng thái (ok/unavailable/disabled), lý do;
`plugin new <kind> <id>` - sinh skeleton (registry/template.py); `plugin test <id>` - conformance (sdk/testing.py);
`plugin enable|disable <id>` và menu "Quản lý plugin" - lưu bật/tắt vào `plugins.disabled` của config/local.toml (decisions #57).
ĐÃ CÓ (Claude 09): `support [--write]` - in/ghi ma trận hỗ trợ (sdk/support_matrix.py);
`kb export|import|forget|prune` - kho tri thức game (sdk/kb.py, import lười; KB trống báo rõ, không crash).
Lưu ý: nạp plugin = import mã plugin trong tiến trình này (4.7). Import/khởi tạo và từng hook chạy dưới
`registry.isolation` (guard best effort: chặn socket/tiến trình con/ghi file/ctypes + timeout). KHÔNG phải sandbox.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from core import config as _config
from core.color import paint, paint_first_line, status
from core.errors import EXIT_OK, PB3Error, format_error
from registry import compatibility as _compat
from registry.registry import build_registry

HEADER = ("id", "kind", "engine", "maturity", "trạng thái", "lý do")


def _cell(v, n: int) -> str:
    s = ",".join(v) if isinstance(v, list) else str(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def _reason_width(width: int) -> int:
    """Chiều rộng cắt lý do plugin, DÙNG CHUNG cho list + manage (X6):
    không vượt chiều rộng màn hình, tối thiểu 20."""
    return max(20, width)


def format_rows(rows: List[dict], width: int = 48) -> str:
    """Danh sách theo khối (iSH hẹp ~48 cột): mỗi plugin 2 dòng, không bảng ngang."""
    if not rows:
        return "Chưa có plugin nào trong plugins/ hoặc plugins_user/."
    out = []
    for r in rows:
        mat = r["maturity"]
        eff = r.get("maturity_effective", mat)
        if eff != mat:
            mat = "%s→%s" % (mat, eff)          # R5: khai M4, hiệu lực M2 vì thiếu bằng chứng
        out.append("%s  [%s]  %s" % (_cell(r["id"], 28), r["state"], mat))
        out.append("    %s | engine: %s | %s v%s" % (r["kind"], _cell(r["engines"] or "-", 20), r["scope"], r["version"]))
        if r["reason"]:
            out.append("    lý do: " + _cell(r["reason"], _reason_width(width)))
    return "\n".join(out)


def _registry(app, only_ids=None):
    from registry.isolation import import_guard      # nạp muộn: chỉ khi thật sự cần nạp plugin
    return build_registry(str(app.paths.root), app.config, import_guard=import_guard, only_ids=only_ids)


def _usage(what: str, usage: str) -> PB3Error:
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage, message_vi="Tham số lệnh plugin không hợp lệ.")


def fixture_dir(app, plugin_id: str) -> str:
    return str(app.paths.root / "tests" / "fixtures" / plugin_id)


def cmd_new(app, args: List[str]) -> int:
    from registry import template
    write = "--write" in args
    args = [a for a in args if a != "--write"]
    if len(args) != 2:
        raise _usage("plugin new cần đúng 2 tham số: <kind> <id> [--write]",
                     "python3 menu.py plugin new <kind> <id> [--write]")
    res = template.generate(str(app.paths.root), args[0], args[1], app.config, write=write)
    print(status("pass", "Đã tạo skeleton plugin '%s' (kind=%s%s):"
                 % (args[1], args[0], ", có ghi (build/verify)" if write else "")))
    for f in res["files"]:
        print("  " + f)
    print("Bước tiếp: thêm dữ liệu mẫu nhỏ, tự tạo vào %s/, sửa plugin.py, rồi chạy" % res["fixture_dir"])
    print("  python3 menu.py plugin test %s" % args[1])
    return EXIT_OK


def cmd_test(app, args: List[str]) -> int:
    from registry.conformance import run_conformance
    if len(args) != 1:
        raise _usage("plugin test cần đúng 1 tham số: <id>", "python3 menu.py plugin test <id>")
    reg = _registry(app, only_ids={args[0]})
    rec = reg.get(args[0]) or next((r for r in reg.records() if r.plugin_id == args[0]), None)   # gồm cả plugin bị loại
    if rec is None:
        raise PB3Error("E_INPUT_NOT_FOUND", "không có plugin '%s'" % args[0],
                       hint="Chạy 'python3 menu.py plugin list' để xem id.", message_vi="Không có plugin này.")
    rep = run_conformance(rec, fixture=fixture_dir(app, rec.plugin_id))
    if not rec.enabled:
        rep.notes.append("plugin đang bị tắt (plugins.disabled); kiểm tra vẫn chạy "
                         "nhưng không bật lại plugin — dùng 'plugin enable %s' để bật."
                         % rec.plugin_id)
    print(rep.format())
    for w in reg.warnings:
        print(paint("cảnh báo: " + w, "warn"))
    return EXIT_OK if rep.ok else PB3Error("E_PLUGIN_INVALID").exit_code


# ---- bật/tắt (C3, decisions #57) ----

def user_disabled(app) -> List[str]:
    """Danh sách id người dùng đã tắt (bản sao, an toàn để sửa)."""
    try:
        return list(app.config.get("plugins.disabled"))
    except (KeyError, AttributeError):          # cấu hình cũ/giả không có khóa này: coi như chưa tắt plugin nào
        return []


def set_user_disabled(app, plugin_id: str, disabled: bool) -> bool:
    """Ghi `plugins.disabled` vào config/local.toml (ghi nguyên tử) rồi cập nhật cấu hình trong bộ nhớ.
    Trả True nếu có thay đổi. Ghi lỗi -> PB3Error, cấu hình trong bộ nhớ KHÔNG đổi."""
    cur = user_disabled(app)
    new = sorted(set(x for x in cur if x != plugin_id) | ({plugin_id} if disabled else set()))
    if new == sorted(set(cur)):
        return False
    _config.save_local(app.paths, {"plugins.disabled": new})
    app.config.update("plugins.disabled", new)
    return True


def stale_disabled(app, reg) -> List[str]:
    """id nằm trong `plugins.disabled` nhưng không còn plugin nào mang id đó."""
    known = set(r.plugin_id for r in reg.records())
    return [x for x in user_disabled(app) if x not in known]


def _warnings(app, reg) -> List[str]:
    out = list(reg.warnings)
    for x in stale_disabled(app, reg):
        out.append("plugins.disabled có id '%s' nhưng không có plugin này (xóa bằng: plugin enable %s)" % (x, x))
    return out


def _not_usable_note(rec) -> str:
    if rec is not None and rec.state != _compat.OK:
        return "Lưu ý: plugin này đang không dùng được (%s)." % (rec.reason or rec.state)
    return ""


def cmd_toggle(app, args: List[str], disable: bool) -> int:
    verb = "disable" if disable else "enable"
    if len(args) != 1:
        raise _usage("plugin %s cần đúng 1 tham số: <id>" % verb, "python3 menu.py plugin %s <id>" % verb)
    pid = args[0]
    reg = _registry(app, only_ids={pid})
    rec = reg.get(pid)
    if rec is None:
        if not disable and pid in user_disabled(app):         # id cũ không còn plugin: cho phép dọn khỏi danh sách tắt
            set_user_disabled(app, pid, False)
            print(status("pass", "Đã xóa id '%s' khỏi danh sách tắt (không còn plugin này)." % pid))
            return EXIT_OK
        raise PB3Error("E_INPUT_NOT_FOUND", "không có plugin '%s'" % pid,
                       hint="Chạy 'python3 menu.py plugin list' để xem id.", message_vi="Không có plugin này.")
    changed = set_user_disabled(app, pid, disable)
    if disable:
        print(status("pass", "Đã tắt plugin '%s'. Lưu trong config/local.toml (plugins.disabled)." % pid) if changed
              else "Plugin '%s' đã tắt từ trước; không đổi gì." % pid)
    else:
        print(status("pass", "Đã bật lại plugin '%s'." % pid) if changed
              else "Plugin '%s' không nằm trong danh sách tắt; không đổi gì." % pid)
    note = _not_usable_note(rec)
    if note:
        print(paint(note, "warn"))
    return EXIT_OK


def format_manage(recs, width: int = 44) -> str:
    """Danh sách đánh số cho menu quản lý (iSH hẹp): 2 dòng mỗi plugin."""
    if not recs:
        return "Chưa có plugin nào trong plugins/ hoặc plugins_user/."
    out = []
    for i, rec in enumerate(recs, 1):
        r = rec.to_row()
        out.append("[%d] %s  [%s]" % (i, _cell(r["id"], 24), r["state"]))
        tail = "    %s | %s v%s" % (r["maturity_effective"], r["scope"], r["version"])
        out.append(tail)
        if r["reason"]:
            out.append("    lý do: " + _cell(r["reason"], _reason_width(width)))
    return "\n".join(out)


def toggle_record(app, reg, rec) -> str:
    """Đảo bật/tắt một plugin trong menu. Trả thông báo. Plugin lỗi/không dùng được thì không đảo."""
    if rec.state != _compat.OK or rec.manifest is None:
        return "Không bật/tắt được '%s': %s" % (rec.plugin_id, rec.reason or rec.state)
    disable = bool(rec.enabled)                 # đang bật -> tắt; đang tắt -> bật
    try:
        set_user_disabled(app, rec.plugin_id, disable)      # ghi cấu hình TRƯỚC; lỗi ghi thì không đổi trạng thái
        reg.set_enabled(rec.plugin_id, not disable)
    except PB3Error as err:
        return format_error(err, log_path=getattr(getattr(app, "logsetup", None), "session_log", None))
    return "Đã %s plugin '%s'." % ("tắt" if disable else "bật lại", rec.plugin_id)


def manage_loop(app, reg, ask: Callable[[str], str], say: Callable[[str], None], width: int = 44) -> None:
    while True:
        recs = reg.records()
        say("")
        say("QUẢN LÝ PLUGIN")
        say(format_manage(recs, width))
        for w in _warnings(app, reg):
            say("cảnh báo: " + w)
        if not recs:
            return
        say("Gõ số để bật/tắt, [m] ma trận hỗ trợ, [k] quên tri thức game, 0 để quay lại.")
        try:
            c = ask("Chọn: ").strip()
        except EOFError:
            return
        if c in ("", "0"):
            return
        if c == "m":
            from sdk import support_matrix
            rows, manifests = _matrix_inputs(reg)
            markdown, _data = support_matrix.generate(
                str(app.paths.root), rows=rows, manifests=manifests)
            say(markdown)
            continue
        if c == "k":
            _forget_interactive(app, ask, say)
            continue
        if not c.isdigit() or not (1 <= int(c) <= len(recs)):
            say("Lựa chọn không hợp lệ.")
            continue
        say(toggle_record(app, reg, recs[int(c) - 1]))


def _matrix_inputs(reg):
    """(rows, manifests) cho sdk/support_matrix.generate từ Registry đã dựng.

    sdk không được import registry (decisions #129) nên tầng services dựng
    Registry rồi truyền hàng/manifest vào."""
    rows = reg.rows()
    manifests = {}
    try:
        for rec in reg.records():
            manifests[rec.plugin_id] = getattr(rec, "manifest", None)
    except Exception:  # noqa: BLE001 - không có records: dùng rows() thôi
        manifests = {}
    return rows, manifests


def _kb_path(app):
    """Đường dẫn file KB (sdk/kb.py dùng; chưa có file = KB trống)."""
    return app.paths.kb / "pb3_kb.sqlite"


def _open_kb(app):
    """Mở KB (import lười: nhóm KB viết sdk/kb.py song song).

    Thiếu sdk/kb.py -> E_NOT_IMPLEMENTED (không giả lập KB)."""
    try:
        from sdk.kb import KB
    except ImportError:
        raise PB3Error("E_NOT_IMPLEMENTED", "kho tri thức game chưa triển khai (thiếu sdk/kb.py)",
                       hint="Claude 09 (nhóm KB).")
    app.paths.kb.mkdir(parents=True, exist_ok=True)
    return KB(str(_kb_path(app)))


def _kb_is_empty(app) -> bool:
    """True khi chưa có file KB hoặc KB không có game nào."""
    if not _kb_path(app).is_file():
        return True
    kb = _open_kb(app)
    try:
        return not kb.list_games()
    finally:
        kb.close()


def _forget_interactive(app, ask, say) -> None:
    """Menu [3] > [k]: hỏi bundle id rồi quên tri thức game đó."""
    try:
        game = ask("Game cần quên (bundle id): ").strip()
    except EOFError:
        return
    if not game:
        say("Không quên gì (chưa gõ bundle id).")
        return
    try:
        if _kb_is_empty(app):
            say("KB trống: chưa có game nào trong kho tri thức.")
            return
        kb = _open_kb(app)
        try:
            forgotten = kb.forget(game)
        finally:
            kb.close()
    except PB3Error as err:
        say(format_error(err, log_path=getattr(getattr(app, "logsetup", None), "session_log", None)))
        return
    if forgotten:
        say("Đã quên tri thức game '%s'." % game)
    else:
        say("Không có game '%s' trong KB." % game)


def cmd_support(app, args: List[str]) -> int:
    """Lệnh `support [--write]`: in ma trận hỗ trợ; --write ghi docs/SUPPORT_MATRIX.md."""
    from sdk import support_matrix
    write = "--write" in args
    reg = _registry(app)
    rows, manifests = _matrix_inputs(reg)
    if write:
        markdown, _data = support_matrix.generate(
            str(app.paths.root), rows=rows, manifests=manifests)
        from core import safe_io
        dest = app.paths.root / "docs" / "SUPPORT_MATRIX.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_bytes(dest, markdown.encode("utf-8"))
        print(markdown)
        print("Đã ghi ma trận hỗ trợ: %s" % dest)
    else:
        markdown, _data = support_matrix.generate(
            str(app.paths.root), rows=rows, manifests=manifests)
        print(markdown)
    return EXIT_OK


def cmd_kb(app, ns) -> int:
    """Lệnh `kb export|import|forget|prune`: kho tri thức game (sdk/kb.py).

    KB trống (chưa có file/chưa có game) thì báo rõ "KB trống", không crash.
    `prune` là mở rộng C09 cho §9.3 (dọn cache KB, giới hạn 64 MB)."""
    import json
    action = getattr(ns, "action", None)
    arg = getattr(ns, "arg", None)
    if action == "import":
        if not arg:
            raise _usage("kb import cần 1 tham số: <file.json>", "python3 menu.py kb import <file.json>")
        import os
        if not os.path.isfile(arg):
            raise PB3Error("E_INPUT_NOT_FOUND", "không có file '%s'" % arg,
                           hint="Đường dẫn phải là file JSON profile đã export.",
                           message_vi="Không có file profile này.")
        try:
            with open(arg, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise PB3Error("E_CORRUPT", "file profile không đọc được (%s)" % type(exc).__name__,
                           where=arg, hint="File phải là JSON profile do 'kb export' tạo ra.")
        if not isinstance(data, dict):
            raise PB3Error("E_CORRUPT", "profile phải là object JSON", where=arg)
        kb = _open_kb(app)
        try:
            game_id, warnings = kb.import_profile(data)
        finally:
            kb.close()
        # import_profile trả game_id nội bộ (int); in bundle id cho người dùng nhận ra game.
        shown = data.get("game", {}).get("bundle_id") or game_id
        print(status("pass", "Đã nhập profile game '%s' vào KB." % shown))
        for w in warnings or []:
            print("cảnh báo: " + str(w))
        return EXIT_OK
    if action == "prune":
        if not _kb_path(app).is_file():
            print("KB trống: không có gì để dọn.")
            return EXIT_OK
        kb = _open_kb(app)
        try:
            cleaned = kb.prune(max_mb=64)
        finally:
            kb.close()
        print("Đã dọn %d byte khỏi KB (giới hạn 64 MB)." % int(cleaned))
        return EXIT_OK
    if action == "export":
        if not arg:
            raise _usage("kb export cần 1 tham số: <game>", "python3 menu.py kb export <game> [--out FILE]")
        if _kb_is_empty(app):
            print("KB trống: chưa có game nào trong kho tri thức.")
            return EXIT_OK
        kb = _open_kb(app)
        try:
            try:
                data = kb.export_profile(arg)
            except (KeyError, ValueError):
                raise PB3Error("E_INPUT_NOT_FOUND", "không có game '%s' trong KB" % arg,
                               hint="Chạy 'kb export' với bundle id đã có trong KB.",
                               message_vi="Không có game này trong KB.")
        finally:
            kb.close()
        out = getattr(ns, "out", None)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if out:
            from core import safe_io
            from pathlib import Path as _Path
            safe_io.atomic_write_bytes(_Path(out), text.encode("utf-8"))
            print("Đã xuất profile game '%s': %s" % (arg, out))
        else:
            print(text, end="")
        return EXIT_OK
    if action == "forget":
        if not arg:
            raise _usage("kb forget cần 1 tham số: <game>", "python3 menu.py kb forget <game>")
        if _kb_is_empty(app):
            print("KB trống: chưa có game nào trong kho tri thức.")
            return EXIT_OK
        kb = _open_kb(app)
        try:
            forgotten = kb.forget(arg)
        finally:
            kb.close()
        if forgotten:
            print(status("pass", "Đã quên tri thức game '%s'." % arg))
            return EXIT_OK
        raise PB3Error("E_INPUT_NOT_FOUND", "không có game '%s' trong KB" % arg,
                       hint="KB chỉ quên được game đã có.",
                       message_vi="Không có game này trong KB.")
    raise _usage("kb cần action export|import|forget|prune",
                 "python3 menu.py kb export <game> | kb import <file.json> | kb forget <game> | kb prune")


def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None) -> None:
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else print
    reg = _registry(app)
    manage_loop(app, reg, ask, out)
    out("(Tạo mới / kiểm tra: python3 menu.py plugin new <kind> <id> | plugin test <id>)")


def run_cli(app, ns) -> int:
    cmd = getattr(ns, "command", "plugin")
    action = getattr(ns, "action", None)
    if cmd == "plugin" and action == "list":
        reg = _registry(app)
        print(format_rows(reg.rows()))
        for w in _warnings(app, reg):
            print("cảnh báo: " + w)
        return EXIT_OK
    args = list(getattr(ns, "args", None) or [])
    if getattr(ns, "write", False):
        args.append("--write")
    if cmd == "support":
        try:
            return cmd_support(app, args)
        except PB3Error as err:
            import sys
            log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
            sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
            return err.exit_code
    if cmd == "kb":
        try:
            return cmd_kb(app, ns)
        except PB3Error as err:
            import sys
            log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
            sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
            return err.exit_code
    if cmd == "plugin" and action in ("new", "test", "enable", "disable"):
        try:
            if action in ("enable", "disable"):
                return cmd_toggle(app, args, disable=(action == "disable"))
            return cmd_new(app, args) if action == "new" else cmd_test(app, args)
        except PB3Error as err:
            import sys
            log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
            sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
            return err.exit_code
    what = "plugin %s" % action if cmd == "plugin" else cmd
    err = PB3Error("E_NOT_IMPLEMENTED", "'%s' chưa triển khai" % what,
                   hint="support/kb: Claude 09.")
    import sys
    log_path = getattr(getattr(app, "logsetup", None), "session_log", None)
    sys.stderr.write(paint_first_line(format_error(err, log_path=log_path), "error") + "\n")
    return err.exit_code
