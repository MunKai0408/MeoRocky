"""Dịch vụ nén IPA kiểu WinRAR (đợt rà soát cuối, nhóm nén IPA).

- `compress <file.ipa> [--out FILE] [--preset N|9e] [--force]`: nén solid LZMA
  (tar.xz + manifest) vào workspace/output/ mặc định; không ghi đè file đã có
  trừ --force.
- `decompress <file.tar.xz> [--out DIR] [--force]`: khôi phục IPA gốc vào
  thư mục đích (mặc định workspace/output/<tên archive>), kiểm sha256 từng
  entry + toàn bộ file.

File nén ra là BẢN LƯU TRỮ, KHÔNG phải IPA cài trực tiếp — service báo rõ
điều này sau mỗi lần nén. Tiến độ theo luật #152–153: % thật theo byte đã xử
lý, ETA thật, tăng dần từng 1% (dùng thanh Overall khi có, SteadyProgress
khi không — cùng mẫu như services/translate_tool.py).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from core import safe_io
from core.color import paint, status, title
from core.errors import EXIT_OK, PB3Error
from core.limits import Limits
from core.progress import SteadyProgress, step_pcts
from sdk import compress as sdk_compress

USAGE_COMPRESS = "compress <file.ipa> [--out FILE] [--preset N|9e] [--force]"
USAGE_DECOMPRESS = "decompress <file.tar.xz> [--out DIR] [--force]"
ARCHIVE_SUFFIX = ".tar.xz"


def _usage(what: str, usage: str) -> PB3Error:
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage,
                    message_vi="Tham số lệnh nén không hợp lệ.")


def _limits(app) -> Limits:
    return getattr(app, "limits", None) or Limits()


def _output_root(app) -> Path:
    paths = getattr(app, "paths", None)
    if paths is not None and getattr(paths, "output", None) is not None:
        return Path(paths.output)
    return Path("workspace") / "output"


class _ProgressAdapter:
    """progress_fn(done, total) -> thanh tiến độ, tăng dần từng 1% (luật #152).

    Có thanh Overall (menu/CLI đều có): bước qua từng mốc % trung gian để
    thanh quét dần, không nhảy cục; ghi chú hiện số byte thật đã xong/tổng.
    Không có Overall thì dùng SteadyProgress riêng (cùng mẫu một dòng
    format_progress)."""

    def __init__(self, app, label: str = "nén"):
        self._label = label
        self._ov = getattr(app, "overall", None) if app is not None else None
        self._steady = None
        self._shown_pct = 0

    def __call__(self, done: int, total: int) -> None:
        if total <= 0:
            return
        new_pct = int(min(1.0, max(0.0, done / total)) * 100)
        if self._ov is not None:
            for pct in step_pcts(self._shown_pct, new_pct):
                interp = int(round(pct / 100.0 * total))
                self._ov.update(frac=pct / 100.0,
                                note="%s %d/%d byte" % (self._label, interp, total))
                self._ov.render()
            self._shown_pct = new_pct
            return
        if self._steady is None:
            self._steady = SteadyProgress(self._label, total)
        self._steady.update(done, note=self._label)

    def finish(self) -> None:
        if self._steady is not None:
            self._steady.finish()


def _fmt_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return "%.1fGB" % n


def _default_compress_dest(app, src: str) -> Path:
    stem = Path(src).name
    if stem.lower().endswith(".ipa"):
        stem = stem[:-4]
    elif stem.lower().endswith(".zip"):
        stem = stem[:-4]
    return _output_root(app) / (stem + ARCHIVE_SUFFIX)


def _default_decompress_dir(app, src: str) -> Path:
    stem = Path(src).name
    if stem.lower().endswith(ARCHIVE_SUFFIX):
        stem = stem[:-len(ARCHIVE_SUFFIX)]
    else:
        stem = Path(stem).stem
    return _output_root(app) / stem


def cmd_compress(app, path: str, out: Optional[str] = None,
                 preset="6", force: bool = False) -> int:
    if not path:
        raise _usage("thiếu file IPA", USAGE_COMPRESS)
    preset = sdk_compress.parse_preset(preset if preset is not None else "6")
    dest = Path(out) if out else _default_compress_dest(app, path)
    prog = _ProgressAdapter(app, label="nén")
    try:
        rep = sdk_compress.compress_ipa(
            path, dest, preset=preset, limits=_limits(app),
            progress=prog, force=bool(force))
    finally:
        prog.finish()
    print(title("nén xong: %s -> %s" % (rep.source, rep.dest)))
    print("  gốc: %s (%d entry) | nén: %s | tỉ lệ %.2f:1 (giảm %.1f%%) | %.1fs"
          % (_fmt_size(rep.original_size), rep.entries, _fmt_size(rep.compressed_size),
             rep.ratio, rep.saved_pct, rep.elapsed_s))
    if rep.target_met:
        print(status("pass", "đạt đích ≤400MB."))
    else:
        print(paint("  trượt đích 400MB (file %s, vẫn trong giới hạn 500MB)."
                    % _fmt_size(rep.compressed_size), "warn"))
    if rep.ram_warning:
        print(paint("  cảnh báo RAM: " + rep.ram_warning, "warn"))
    print(paint("  Lưu ý: file nén ra là bản lưu trữ, KHÔNG phải IPA cài trực tiếp — "
                "dùng `decompress %s` để khôi phục IPA gốc." % rep.dest, "dim"))
    return EXIT_OK


def cmd_decompress(app, path: str, out: Optional[str] = None,
                   force: bool = False) -> int:
    if not path:
        raise _usage("thiếu file archive", USAGE_DECOMPRESS)
    manifest = sdk_compress.load_manifest(path)
    out_dir = Path(out) if out else _default_decompress_dir(app, path)
    dest = out_dir / manifest["original_name"]
    prog = _ProgressAdapter(app, label="khôi phục")
    try:
        rep = sdk_compress.decompress_archive(
            path, dest, limits=_limits(app), progress=prog, force=bool(force))
    finally:
        prog.finish()
    print(title("khôi phục xong: %s -> %s" % (rep.source, rep.dest)))
    print("  %d entry | %s | %.1fs" % (rep.entries, _fmt_size(rep.restored_size),
                                      rep.elapsed_s))
    if rep.byte_identical:
        print(status("pass", "IPA khôi phục byte-identical với IPA gốc (sha256 khớp)."))
    else:
        print(paint("  cảnh báo: IPA khôi phục khác byte gốc ở mức container "
                    "(nội dung từng entry đã khớp sha256; thường do mức nén "
                    "DEFLATE của công cụ tạo IPA gốc khác nhau).", "warn"))
    return EXIT_OK


def add_arguments(subparser) -> None:
    """Thêm cờ riêng cho `compress` / `decompress` (gọi từ ui/cli.py).

    cli.py gọi hàm này một lần cho mỗi lệnh (subparser.prog chứa tên lệnh)
    nên nhánh theo tên lệnh để thêm đúng cờ."""
    prog = getattr(subparser, "prog", "") or ""
    subparser.add_argument("path", help="file đầu vào")
    subparser.add_argument("--out", default=None,
                           help="compress: file đích (mặc định workspace/output/<tên>.tar.xz); "
                                "decompress: thư mục đích")
    subparser.add_argument("--force", action="store_true",
                           help="ghi đè file đích đã có")
    if "decompress" not in prog:
        subparser.add_argument("--preset", default="6",
                               help="preset LZMA 0..9 hoặc 9e (mặc định 6; "
                                    "preset cao tốn nhiều RAM, xem cảnh báo)")


def run_cli(app, ns) -> int:
    cmd = getattr(ns, "command", None)
    if cmd == "compress":
        return cmd_compress(app, getattr(ns, "path", None),
                            out=getattr(ns, "out", None),
                            preset=getattr(ns, "preset", "6"),
                            force=getattr(ns, "force", False))
    if cmd == "decompress":
        return cmd_decompress(app, getattr(ns, "path", None),
                              out=getattr(ns, "out", None),
                              force=getattr(ns, "force", False))
    raise _usage("lệnh lạ: %r" % (cmd,), USAGE_COMPRESS + " | " + USAGE_DECOMPRESS)


def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None) -> None:
    """Màn hình tương tác tối thiểu (menu sẽ nối ở bước tích hợp)."""
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else print
    out(title("NÉN IPA KIỂU WINRAR (solid LZMA)"))
    out("File nén ra là bản lưu trữ, KHÔNG phải IPA cài trực tiếp.")
    while True:
        path = safe_io.normalize_typed_path(
            ask("Đường dẫn file IPA (trống để thoát): ").strip())
        if not path:
            return
        preset = ask("Preset LZMA 0..9/9e (mặc định 6): ").strip() or "6"
        try:
            cmd_compress(app, path, preset=preset)
        except PB3Error as exc:
            out("[%s] %s" % (exc.code, exc.message_vi))
            if exc.detail:
                out("Chi tiết: " + exc.detail)
