"""Dịch vụ container (Claude 04): `container list|read <file>`.

- `container list <file>`: bảng kê entry (tên, kích thước, method) + entry
  bị chặn + cảnh báo. Chỉ liệt kê tầng ngoài; entry lồng xem bằng `read`.
- `container read <file> <entry> --out <dest>`: đọc một entry (kể cả đường
  dẫn lồng dạng `a.ipa::b.unity3d::resources.assets`) ra file. Không ghi đè
  file đã có (E_OUTPUT_EXISTS). Entry quá giới hạn đọc vào RAM thì báo
  E_TOO_LARGE (dùng slice/stream qua SDK, đặc tả 5.1).

Chỉ đọc container; không giải nén toàn bộ ra đĩa.
"""
from __future__ import annotations

from pathlib import Path

from core import safe_io
from core.color import paint, status, title
from core.errors import EXIT_OK, PB3Error
from core.limits import Limits
from core.progress import SteppedReporter
from sdk.containers import NestedVFS, open_vfs, support_matrix
from sdk.vfs import SEP


def _usage(what: str, usage: str) -> PB3Error:
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage,
                    message_vi="Tham số lệnh container không hợp lệ.")


def _limits(app) -> Limits:
    """MỘT Limits duy nhất cho lệnh container (từ cấu hình app, fallback mặc định)."""
    return getattr(app, "limits", None) or Limits()


def _open(path: str, limits: Limits):
    try:
        return open_vfs(path, limits=limits, source_name=path)
    except PB3Error:
        raise
    except Exception as exc:  # noqa: BLE001 - lạ: quy về nội bộ, không traceback
        raise PB3Error("E_INTERNAL", type(exc).__name__, where=path)


def cmd_formats(app) -> int:
    """`container formats`: bảng định dạng container được hỗ trợ (V15)."""
    print(title("định dạng container được hỗ trợ:"))
    for row in support_matrix():
        line = "  %-16s đọc: %-4s ghi: %-18s (%s)" % (
            row["format"], row["read"], row["write"], row["backend"])
        print(line)
        if row.get("note"):
            print("    " + row["note"])
    return EXIT_OK


def cmd_list(app, path: str, entry: str = None, limit: int = None, prefix: str = None) -> int:
    limits = _limits(app)
    vfs = _open(path, limits)
    try:
        inv = vfs.inventory()
    finally:
        close = getattr(vfs, "close", None)
        if close is not None:
            close()
    # Lọc: vị trí `entry` hoặc --prefix đều là tiền tố tên entry.
    want = prefix or entry
    shown = [e for e in inv.entries if not e.is_dir
             and (want is None or e.name.startswith(want))]
    total = len(shown)
    if limit is not None and limit >= 0:
        shown = shown[:limit]
    print(title("container: %s (%s, %d entry)" % (inv.source_name, inv.kind, len(inv.entries))))
    # Báo tiến độ thật từng entry, quét dần từng 1% (luật #152): trước đây
    # gọi report_progress thẳng (i+1)/N nên entry ít thì thanh nhảy cục
    # (vd 3 entry: 33% -> 67% -> 100% trong 3 lần cập nhật).
    stepper = SteppedReporter(app, "liệt kê")
    for i, e in enumerate(shown):
        stepper.report(i + 1, len(shown), note=e.name)
        print("  %s  %d byte  %s" % (e.name, e.size, e.compress_type))
    if total > len(shown):
        print(paint("  …còn %d entry nữa (dùng --limit để xem thêm)" % (total - len(shown)), "dim"))
    for name, reason in inv.rejected:
        print(paint("  [chặn] %s: %s" % (name, reason), "warn"))
    # Cảnh báo tỉ lệ nén gộp lại (nhiều entry cùng vượt giới hạn thì chỉ in tóm tắt).
    ratio = [w for w in inv.warnings if "tỉ lệ nén" in w or "tỉ lệ" in w]
    other = [w for w in inv.warnings if w not in ratio]
    for w in other:
        print(paint("  cảnh báo: " + w, "warn"))
    if ratio:
        if len(ratio) <= 3:
            for w in ratio:
                print(paint("  cảnh báo: " + w, "warn"))
        else:
            print(paint("  cảnh báo: %d entry có tỉ lệ nén vượt giới hạn (vd: %s)" % (len(ratio), ratio[0]), "warn"))
    return EXIT_OK


def cmd_read(app, path: str, entry: str, out: str) -> int:
    if not entry:
        raise _usage("thiếu entry", "container read <file> <entry> --out <dest>")
    if not out:
        raise _usage("thiếu --out", "container read <file> <entry> --out <dest>")
    limits = _limits(app)
    vfs = _open(path, limits)
    try:
        container = NestedVFS(vfs, limits=limits) if SEP in entry else vfs
        try:
            size = container.stat(entry).size
            stream = container.open_stream(entry)
            # Sao chép theo khối (không nạp cả entry vào RAM) + ghi nguyên tử độc quyền.
            # Báo tiến độ thật theo số byte đã sao chép/tổng (luật #152):
            # trước đây đọc entry lớn im lặng tới lúc xong.
            stepper = SteppedReporter(app, "đọc entry")
            done = 0
            with safe_io.AtomicWriter(Path(out), expected_size=size,
                                     disk_factor=limits.disk_factor,
                                     exclusive=True) as writer:
                with stream:
                    while True:
                        chunk = stream.read(limits.chunk_size)
                        if not chunk:
                            break
                        writer.write(chunk)
                        done += len(chunk)
                        stepper.report(done, size, note=entry)
            stepper.finish()
        finally:
            if container is not vfs:
                container.close()
    finally:
        close = getattr(vfs, "close", None)
        if close is not None:
            close()
    print(status("pass", "đã ghi %d byte: %s" % (size, out)))
    return EXIT_OK


def run_cli(app, ns) -> int:
    action = getattr(ns, "action", None)
    if action == "formats":
        return cmd_formats(app)
    path = getattr(ns, "path", None)
    if not path:
        raise _usage("thiếu file", "container list <file> | container read <file> <entry> --out <dest>")
    if action == "list":
        return cmd_list(app, path, entry=getattr(ns, "entry", None),
                        limit=getattr(ns, "limit", None),
                        prefix=getattr(ns, "prefix", None))
    if action == "read":
        return cmd_read(app, path, getattr(ns, "entry", None), getattr(ns, "out", None))
    raise _usage("hành động lạ: %r" % (action,), "container list|read|formats <file>")
