"""Dịch vụ chẩn đoán/điều tra dữ liệu (Claude 04+; menu [5][3], đợt rà soát tổng cuối F6/F5).

- CLI: `diagnose inventory|hex|strings <file>` (mặc định: inventory).
- Menu [5] > [3] (tương tác): chọn file bằng core.file_dialog.choose_path
  (phân trang, không gõ tay) rồi điều tra: inventory / hex dump / strings /
  đoán encoding, có phân trang, dòng <= width điện thoại.
- CHỈ ĐỌC: không ghi bất cứ gì (không có dry-run vì không có ghi ngược).
- Không giả lập: mục nào không làm được thì báo rõ lý do (vd 7z chưa có
  backend, file quá lớn, entry mã hóa). Container mở qua
  sdk/containers.open_vfs nên ZIP/IPA/TAR/GZ/XZ/BZ2/PCK/PAK đều dùng chung
  một đường; Unity bundle thì báo rõ "dùng `unity inspect`".

Tiến độ: quét strings báo tiến độ thật qua app.report_progress (số byte đã
đọc/tổng, ETA theo thanh Overall); inventory/hex đọc có giới hạn nên không
cần thanh riêng. Màu qua core/color.py; dấu tiếng Việt đầy đủ.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core import safe_io
from core.color import paint, status, term_width, title
from core.errors import EXIT_OK, PB3Error
from core.file_dialog import choose_path
from core.limits import Limits
from core.progress import fmt_bytes, truncate_display
from sdk.containers import detect_container_kind, open_vfs
from ui.screens import default_print

_PLUGIN = "services.diagnostics_tool (không plugin, đọc trực tiếp)"
_MODES = ("inventory", "hex", "strings")

# Quét strings: đọc theo khối, giữ chuỗi dở dang qua ranh giới khối.
_STRINGS_CHUNK = 256 * 1024
# Strings UTF-16: chỉ giải mã toàn bộ khi file <= ngưỡng này; lớn hơn thì
# báo rõ giới hạn thay vì đọc ồ ạt vào RAM (iPhone 4GB).
_UTF16_MAX_BYTES = 8 * 1024 * 1024
_HEX_BYTES_PER_LINE = 8
_HEX_LINES_PER_PAGE = 16
_PAGE_SIZE = 15


def _usage(what, usage):
    return PB3Error("E_CONFIG_INVALID", what, hint="Dùng: " + usage,
                    message_vi="Tham số lệnh diagnose không hợp lệ.")


def _limits(app) -> Limits:
    return getattr(app, "limits", None) or Limits()


# ------------------------------------------------------------------ encoding
_BOMS = (
    (b"\xef\xbb\xbf", "UTF-8"),
    (b"\xff\xfe\x00\x00", "UTF-32LE"),
    (b"\x00\x00\xfe\xff", "UTF-32BE"),
    (b"\xff\xfe", "UTF-16LE"),
    (b"\xfe\xff", "UTF-16BE"),
)


def guess_encoding(data: bytes) -> dict:
    """Đoán encoding sơ bộ từ BOM + mẫu đầu file.

    Trả dict {encoding, certain, bom, detail}: encoding là tên đoán được
    ("UTF-8", "UTF-16LE", "UTF-16BE", "ASCII", "nhị phân/không rõ");
    certain True chỉ khi có BOM hoặc UTF-8 hợp lệ không NUL; còn lại là
    "có thể". Không bao giờ khẳng định chắc chắn chỉ từ heuristic."""
    head = bytes(data[:65536])
    for bom, name in _BOMS:
        if head.startswith(bom):
            return {"encoding": name, "certain": True, "bom": name,
                    "detail": "có BOM %s" % name}
    if not head:
        return {"encoding": "trống", "certain": True, "bom": None,
                "detail": "file rỗng (0 byte)"}
    if b"\x00" in head:
        # NUL xen kẽ đều ở vị trí lẻ/chẵn -> UTF-16. Chỉ tính ký tự ASCII in
        # được (không tính mọi ký tự "in được" theo Unicode: dữ liệu nhị
        # phân giải mã sai thành UTF-16 toàn ký tự CJK — vẫn isprintable()
        # nhưng không phải văn bản; đó từng là lỗi đoán nhầm UTF-16).
        for enc, parity in (("UTF-16LE", 1), ("UTF-16BE", 0)):
            try:
                text = head.decode(enc, errors="strict")
            except (UnicodeDecodeError, ValueError):
                continue
            if len(text) < 4:
                continue
            ascii_n = sum(1 for c in text if 0x20 <= ord(c) <= 0x7E or c in "\n\r\t")
            nuls = [i for i, b in enumerate(head) if b == 0]
            parity_ok = (not nuls or
                         sum(1 for i in nuls if i % 2 == parity) / len(nuls) >= 0.8)
            if ascii_n / len(text) >= 0.8 and parity_ok:
                return {"encoding": enc, "certain": False, "bom": None,
                        "detail": "có byte NUL xen kẽ, giải mã %s được %.0f%% ký tự ASCII in được"
                                  % (enc, 100.0 * ascii_n / len(text))}
        return {"encoding": "nhị phân/không rõ", "certain": False, "bom": None,
                "detail": "có byte NUL nhưng không khớp UTF-16 (có thể là dữ liệu nhị phân)"}
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return {"encoding": "nhị phân/không rõ", "certain": False, "bom": None,
                "detail": "không phải UTF-8 hợp lệ, không có BOM (có thể là encoding khác hoặc nhị phân)"}
    if all(b < 0x80 for b in head):
        return {"encoding": "ASCII (tương thích UTF-8)", "certain": True, "bom": None,
                "detail": "toàn ký tự ASCII trong mẫu đầu"}
    return {"encoding": "UTF-8", "certain": True, "bom": None,
            "detail": "mẫu đầu giải mã UTF-8 hợp lệ, không có byte NUL"}


def _read_head(path: Path, n: int = 65536) -> bytes:
    with open(str(path), "rb") as fh:
        return fh.read(n)


# ------------------------------------------------------------------ inventory
def inventory_of(path, limits: Optional[Limits] = None) -> dict:
    """Mở container qua open_vfs, trả mục lục.

    Trả dict {kind, source_name, entries: [{name, size, compress_type,
    is_dir}], rejected: [(name, reason)], warnings: [str]}. Không phải
    container đã hỗ trợ -> PB3Error (E_UNSUPPORTED_FORMAT / E_BAD_ARCHIVE /
    E_ENCRYPTED...) từ open_vfs, giữ nguyên mã + thông điệp."""
    lim = limits or Limits()
    vfs = open_vfs(path, limits=lim, source_name=str(path))
    try:
        inv = vfs.inventory()
    finally:
        close = getattr(vfs, "close", None)
        if close is not None:
            close()
    return {"kind": inv.kind, "source_name": inv.source_name,
            "entries": [{"name": e.name, "size": e.size,
                         "compress_type": e.compress_type, "is_dir": e.is_dir}
                        for e in inv.entries],
            "rejected": list(inv.rejected), "warnings": list(inv.warnings)}


def _unity_hint(path: Path) -> Optional[str]:
    """Ghi chú khi file là bundle UnityFS (không phải container ZIP)."""
    try:
        from sdk import unity as unity_sdk
        det = unity_sdk.detect_bundle(_read_head(path, 4096))
    except Exception:
        return None
    if det is None:
        return None
    return ("file là bundle UnityFS v%d (Unity %s) — không phải container ZIP; "
            "dùng `unity inspect` để xem plan/object" % (det["version"], det.get("unity_version") or "?"))


# ------------------------------------------------------------------ hex dump
def hex_lines(data: bytes, offset: int = 0, width: int = 44) -> List[str]:
    """Một dòng = offset (8 hex) + 8 byte hex + ASCII; <= 43 cột, vừa màn
    hình điện thoại 30-44 cột. `data` là lát cắt đã đọc (không đọc cả file)."""
    out: List[str] = []
    for i in range(0, len(data), _HEX_BYTES_PER_LINE):
        chunk = data[i:i + _HEX_BYTES_PER_LINE]
        hexpart = " ".join("%02x" % b for b in chunk)
        asciipart = "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in chunk)
        out.append(truncate_display("%08x  %s|%s|" % (offset + i, hexpart, asciipart), width))
    return out


def _hex_slice(path: Path, offset: int, length: int) -> bytes:
    if offset < 0:
        raise PB3Error("E_CONFIG_INVALID", "--offset không được âm", where=str(path))
    if length <= 0:
        raise PB3Error("E_CONFIG_INVALID", "--length phải > 0", where=str(path))
    with open(str(path), "rb") as fh:
        fh.seek(offset)
        return fh.read(length)


# ------------------------------------------------------------------ strings
_STR_RUN = re.compile(rb"[\x09\x20-\xff]+")
# Dãy UTF-8 HỢP LỆ (ASCII + chuỗi đa byte đúng dạng): dùng để tách chuỗi
# trong một dãy byte thô — byte rác (vd \xff lẻ) chỉ cắt dãy, không làm
# hỏng cả dãy như decode() cả khối (từng làm "Xin chào" tách đôi).
_UTF8_RUN = re.compile(rb"(?:[\x09\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}"
                        rb"|[\xf0-\xf4][\x80-\xbf]{3})+")


def _is_str_byte(b: int) -> bool:
    return b == 0x09 or 0x20 <= b <= 0xFF


def _run_to_strings(run: bytes, run_start: int, min_len: int,
                    keyword: Optional[str]) -> List[Tuple[int, str]]:
    """Một dãy byte liên tục -> 0..n chuỗi (offset, text).

    Tách dãy thành các dãy con UTF-8 hợp lệ (giữ được tiếng Việt có dấu);
    byte rác chỉ cắt dãy chứ không làm hỏng chuỗi kề. Chuỗi chỉ tính khi đủ
    dài và còn ký tự thật sau khi bỏ khoảng trắng hai đầu (dãy toàn khoảng
    trắng không phải chuỗi)."""
    out: List[Tuple[int, str]] = []
    if len(run) < min_len:
        return out
    for m in _UTF8_RUN.finditer(run):
        text = m.group().decode("utf-8")
        off = run_start + m.start()
        if len(text) < min_len or not text.strip():
            continue
        if not all(c.isprintable() or c in "\n\t" for c in text):
            continue
        if keyword and keyword.lower() not in text.lower():
            continue
        out.append((off, text))
    return out


def scan_strings(path, *, min_len: int = 4, keyword: Optional[str] = None,
                 limit: Optional[int] = None, limits: Optional[Limits] = None,
                 progress_cb: Optional[Callable[[int, int], None]] = None,
                 encoding: Optional[str] = None) -> Tuple[List[Tuple[int, str]], dict]:
    """Quét chuỗi in được trong file, đọc theo khối (không nạp cả file).

    Trả (danh sách (offset, text), info {total, truncated, encoding}).
    total = số chuỗi đã khớp TỚI LÚC DỪNG; khi truncated (dừng sớm vì đủ
    limit) thì total chỉ là số đếm tối thiểu của phần đã quét, KHÔNG phải
    tổng toàn file — thông báo hiển thị phải ghi rõ "ít nhất"."""
    lim = limits or Limits()
    p = safe_io.check_readable_input(path, lim, allow_dir=False)
    size = p.stat().st_size
    if min_len < 1:
        raise PB3Error("E_CONFIG_INVALID", "--min-len phải >= 1", where=str(path))
    if encoding is None:
        encoding = guess_encoding(_read_head(p))["encoding"]
    if encoding in ("UTF-16LE", "UTF-16BE"):
        return _scan_strings_utf16(p, size, encoding, min_len, keyword, limit, progress_cb)
    results: List[Tuple[int, str]] = []
    total = 0
    truncated = False
    pending = bytearray()
    pending_start = 0
    read_so_far = 0
    with open(str(p), "rb") as fh:
        while True:
            chunk = fh.read(_STRINGS_CHUNK)
            if not chunk:
                break
            base = read_so_far
            read_so_far += len(chunk)
            # Các dãy trong khối; dãy chạm biên khối nối tiếp pending.
            for m in _STR_RUN.finditer(chunk):
                s, e = m.start(), m.end()
                if pending:
                    if s == 0:
                        pending += m.group()
                        if e < len(chunk):
                            total, truncated = _collect(
                                bytes(pending), pending_start, min_len, keyword,
                                limit, results, total)
                            pending = bytearray()
                            if truncated:
                                break
                        continue
                    total, truncated = _collect(
                        bytes(pending), pending_start, min_len, keyword,
                        limit, results, total)
                    pending = bytearray()
                    if truncated:
                        break
                if e == len(chunk):
                    pending = bytearray(m.group())
                    pending_start = base + s
                else:
                    total, truncated = _collect(
                        m.group(), base + s, min_len, keyword, limit, results, total)
                if truncated:
                    break
            if progress_cb is not None:
                progress_cb(read_so_far, size)
            if truncated:
                break
        if not truncated and pending:
            total, truncated = _collect(
                bytes(pending), pending_start, min_len, keyword, limit, results, total)
    return results, {"total": total, "truncated": truncated, "encoding": encoding}


def _collect(run: bytes, run_start: int, min_len: int, keyword: Optional[str],
             limit: Optional[int], results: List[Tuple[int, str]], total: int):
    """Thêm chuỗi từ một dãy đã kết thúc; trả (total, truncated)."""
    for off, text in _run_to_strings(run, run_start, min_len, keyword):
        total += 1
        if limit is None or len(results) < limit:
            results.append((off, text))
    truncated = limit is not None and total > len(results)
    return total, truncated


def _scan_strings_utf16(path: Path, size: int, encoding: str, min_len: int,
                        keyword: Optional[str], limit: Optional[int],
                        progress_cb) -> Tuple[List[Tuple[int, str]], dict]:
    if size > _UTF16_MAX_BYTES:
        return [], {"total": 0, "truncated": False, "encoding": encoding,
                    "note": "file UTF-16 quá lớn (%s > %s) nên không quét strings toàn bộ; "
                            "dùng hex để xem từng đoạn" % (fmt_bytes(size), fmt_bytes(_UTF16_MAX_BYTES))}
    raw = path.read_bytes()
    text = raw.decode(encoding, errors="replace")
    results: List[Tuple[int, str]] = []
    total = 0
    cur: List[str] = []
    cur_start = 0
    for i, c in enumerate(text):
        if c.isprintable():
            if not cur:
                cur_start = i
            cur.append(c)
        else:
            if cur:
                total, done = _collect_text("".join(cur), cur_start * 2, min_len,
                                            keyword, limit, results, total)
                cur = []
                if done:
                    break
    if cur:
        total, _ = _collect_text("".join(cur), cur_start * 2, min_len,
                                 keyword, limit, results, total)
    if progress_cb is not None:
        progress_cb(size, size)
    return results, {"total": total, "truncated": limit is not None and total > len(results),
                     "encoding": encoding}


def _collect_text(text: str, byte_off: int, min_len: int, keyword: Optional[str],
                  limit: Optional[int], results: List[Tuple[int, str]], total: int):
    if len(text) >= min_len and text.strip():
        if keyword is None or keyword.lower() in text.lower():
            total += 1
            if limit is None or len(results) < limit:
                results.append((byte_off, text))
    return total, (limit is not None and total > len(results))


# ------------------------------------------------------------------ hiển thị
def _page_through(lines: List[str], ask, out, width: int, page_size: int = _PAGE_SIZE,
                  truncate: bool = True) -> None:
    """In danh sách theo trang; Enter=trang sau, p=trang trước, q=dừng, EOF=dừng.

    truncate=True (mặc định): cắt mỗi dòng theo width (dòng menu). False:
    in đủ dòng (vd tên entry container dài — theo tiền lệ `container list`,
    không cắt dở giữa chừng làm mất kích thước/method ở cuối dòng)."""
    if not lines:
        out("(không có gì để hiện)")
        return
    pages = max(1, (len(lines) + page_size - 1) // page_size)
    page = 0
    while True:
        for ln in lines[page * page_size:(page + 1) * page_size]:
            out(truncate_display(ln, width) if truncate else ln)
        if pages <= 1:
            return
        out("--- trang %d/%d (Enter=trang sau, p=trang trước, q=dừng)" % (page + 1, pages))
        try:
            c = ask("").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if c == "q":
            return
        if c == "p":
            page = max(0, page - 1)
            continue
        page += 1
        if page >= pages:
            return


def _print_inventory(inv: dict, out, width: int, ask, limit: Optional[int],
                     interactive: bool = True) -> None:
    files = [e for e in inv["entries"] if not e["is_dir"]]
    dirs = [e for e in inv["entries"] if e["is_dir"]]
    out(title("container: %s (%s, %d entry, %d thư mục)"
              % (inv["source_name"], inv["kind"], len(files), len(dirs))))
    lines = ["  %s  %s  %s" % (e["name"], fmt_bytes(e["size"]), e["compress_type"])
             for e in files]
    shown = lines if limit is None else lines[:limit]
    if interactive:
        _page_through(shown, ask, out, width, truncate=False)
    else:
        for ln in shown:
            out(ln)
    if limit is not None and len(lines) > limit:
        out(paint("  …còn %d entry nữa (dùng CLI --limit lớn hơn hoặc --verbose)"
                  % (len(lines) - limit), "dim"))
    for name, reason in inv["rejected"]:
        out(paint("  [chặn] %s: %s" % (name, reason), "warn"))
    for w in inv["warnings"]:
        out(paint("  cảnh báo: " + w, "warn"))


def _print_encoding(path: Path, out, width: int) -> None:
    g = guess_encoding(_read_head(path))
    out(title("encoding (đoán sơ bộ): %s" % g["encoding"]))
    for ln in _wrap("  " + g["detail"] + (" — chắc chắn (BOM/UTF-8 hợp lệ)."
                                          if g["certain"] else " — chỉ là phỏng đoán, "
                                          "không chắc chắn."), width):
        out(ln)
    kind = detect_container_kind(_read_head(path, 2048))
    if kind != "unknown":
        out("  container (magic): %s" % kind)
    hint = _unity_hint(path)
    if hint:
        for ln in _wrap("  " + hint, width):
            out(ln)


def _wrap(text: str, width: int) -> List[str]:
    import textwrap
    return textwrap.wrap(text, width=width, break_long_words=True) or [""]


# ------------------------------------------------------------------ điều tra tương tác
def _start_dir(app) -> Path:
    try:
        d = app.paths.input
        if d.is_dir():
            return d
    except Exception:
        pass
    return Path(str(getattr(app.paths, "root", ".")))


def investigate_file(app, path, ask, out, width: int) -> None:
    """Vòng lặp điều tra một file đã chọn: [1] inventory [2] hex [3] strings
    [4] encoding [0] quay lại. Chỉ đọc, không ghi gì."""
    p = Path(str(path))
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    out(title("ĐIỀU TRA: %s (%s)" % (p.name, fmt_bytes(size))))
    while True:
        out("")
        out("[1] Inventory (mục lục container)")
        out("[2] Hex dump (phân trang)")
        out("[3] Strings (chuỗi in được)")
        out("[4] Encoding (đoán sơ bộ)")
        out("[0] Quay lại (chọn file khác)")
        try:
            c = ask("Chọn: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if c == "0":
            return
        if c == "1":
            try:
                inv = inventory_of(p, _limits(app))
            except PB3Error as exc:
                out(paint("Không mở được container: [%s] %s"
                          % (exc.code, exc.detail or exc.message_vi), "warn"))
                hint = _unity_hint(p)
                if hint:
                    for ln in _wrap(hint, width):
                        out(ln)
                _print_encoding(p, out, width)
                continue
            _print_inventory(inv, out, width, ask, limit=None)
        elif c == "2":
            try:
                off_s = ask("Offset bắt đầu (Enter=0): ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            try:
                off = int(off_s) if off_s else 0
            except ValueError:
                out(paint("Offset phải là số.", "warn"))
                continue
            if off < 0 or off >= max(size, 1):
                out(paint("Offset ngoài file (kích thước %s)." % fmt_bytes(size), "warn"))
                continue
            page_bytes = _HEX_BYTES_PER_LINE * _HEX_LINES_PER_PAGE
            cur = off
            while cur < size:
                data = _hex_slice(p, cur, page_bytes)
                for ln in hex_lines(data, cur, width):
                    out(ln)
                cur += len(data)
                if cur >= size:
                    break
                out("--- %s/%s (Enter=trang sau, q=dừng)"
                    % (fmt_bytes(cur), fmt_bytes(size)))
                try:
                    nxt = ask("").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break
                if nxt == "q":
                    break
        elif c == "3":
            try:
                kw = ask("Từ khóa lọc (Enter=tất cả): ").strip() or None
            except (EOFError, KeyboardInterrupt):
                return
            out("Đang quét strings…")
            results, info = scan_strings(
                p, min_len=4, keyword=kw, limit=2000, limits=_limits(app),
                progress_cb=lambda d, t: _report(app, d, t))
            if info.get("note"):
                out(paint(info["note"], "warn"))
            if info["truncated"]:
                out("Tìm thấy ít nhất %d chuỗi (dừng sớm vì đủ giới hạn %d; "
                    "encoding: %s)" % (info["total"], len(results), info["encoding"]))
            else:
                out("Tìm thấy %d chuỗi (encoding: %s)" % (info["total"], info["encoding"]))
            _page_through(["  %08x  %s" % (off, txt) for off, txt in results],
                          ask, out, width)
        elif c == "4":
            _print_encoding(p, out, width)
        else:
            out(paint("Lựa chọn không hợp lệ.", "warn"))


def _report(app, done: int, total: int) -> None:
    try:
        app.report_progress(done / total if total else 1.0,
                            note="quét strings %s/%s" % (fmt_bytes(done), fmt_bytes(total)))
    except Exception:
        pass


def run_interactive(app, input_fn: Optional[Callable[[str], str]] = None,
                    say: Optional[Callable[[str], None]] = None) -> None:
    """Menu [5] > [3] qua run_feature: chọn file bằng picker có phân trang,
    rồi điều tra. Hủy (q/EOF) thì quay lại, không ghi gì."""
    ask = input_fn if input_fn is not None else (lambda prompt="": input(prompt))
    out = say if say is not None else default_print
    width = term_width()
    out(title("ĐIỀU TRA DỮ LIỆU (inventory/hex/strings)"))
    out("chỉ đọc, không ghi gì.")
    while True:
        try:
            path = choose_path(_start_dir(app), prompt="Chọn file",
                               input_fn=ask, print_fn=out, width=width,
                               allow_dir=False)
        except (EOFError, KeyboardInterrupt):
            return
        if path is None:
            return
        investigate_file(app, path, ask, out, width)


# ------------------------------------------------------------------ CLI
def add_arguments(subparser) -> None:
    """Cờ riêng cho `diagnose` (ui/cli.py gọi cho mọi lệnh có add_arguments).

    Thêm positional thứ hai `extra`: `diagnose inventory|hex|strings <file>`
    -> path=từ chế độ, extra=file; `diagnose <file>` -> path=file, chế độ
    mặc định là inventory. Không đổi parser sẵn có (path/--ish/--deep)."""
    subparser.add_argument("extra", nargs="?", default=None, metavar="FILE",
                           help="file cần điều tra (dùng cùng inventory|hex|strings)")
    subparser.add_argument("--offset", type=int, default=0,
                           help="hex: offset bắt đầu (mặc định 0)")
    subparser.add_argument("--length", type=int, default=256,
                           help="hex: số byte in (mặc định 256)")
    subparser.add_argument("--keyword", default=None,
                           help="strings: chỉ chuỗi chứa từ khóa (không phân biệt hoa/thường)")
    subparser.add_argument("--min-len", type=int, default=4,
                           help="strings: độ dài tối thiểu (mặc định 4)")
    subparser.add_argument("--limit", type=int, default=50,
                           help="số dòng tối đa (mặc định 50; --verbose: không giới hạn)")


def run_cli(app, ns) -> int:
    """`diagnose inventory|hex|strings <file>`; `diagnose <file>` = inventory."""
    path = getattr(ns, "path", None)
    extra = getattr(ns, "extra", None)
    if path is None:
        raise _usage("thiếu file", "diagnose inventory|hex|strings <file>")
    mode = "inventory"
    if path in _MODES and extra is not None:
        mode, path = path, extra
    elif path in _MODES:
        # `diagnose inventory` (không kèm file): từ chế độ mà thiếu file.
        # (File thật tên đúng "inventory"/"hex"/"strings" vẫn mở được bằng
        # `diagnose ./inventory` — path "./inventory" không trùng từ chế độ.)
        raise _usage("thiếu file", "diagnose %s <file>" % path)
    elif extra is not None:
        raise _usage("tham số thừa %r" % extra, "diagnose inventory|hex|strings <file>")
    lim = _limits(app)
    p = safe_io.check_readable_input(path, lim, allow_dir=False)
    verbose = bool(getattr(ns, "verbose", False))
    limit = None if verbose else max(0, int(getattr(ns, "limit", 50) or 0))
    width = term_width()
    if mode == "inventory":
        try:
            inv = inventory_of(p, lim)
        except PB3Error as exc:
            print(paint("Không phải container đã hỗ trợ: [%s] %s"
                        % (exc.code, exc.detail or exc.message_vi), "warn"))
            hint = _unity_hint(p)
            if hint:
                print(hint)
            _print_encoding(p, print, width)
            return EXIT_OK
        _print_inventory(inv, print, width,
                         lambda prompt="": input(prompt), limit=limit,
                         interactive=False)
        return EXIT_OK
    if mode == "hex":
        offset = int(getattr(ns, "offset", 0) or 0)
        length = int(getattr(ns, "length", 256) or 0)
        data = _hex_slice(p, offset, length)
        if not data:
            print("(hết file tại offset %d)" % offset)
            return EXIT_OK
        for ln in hex_lines(data, offset, width):
            print(ln)
        print("đã in %d byte từ offset %d (file %s)"
              % (len(data), offset, fmt_bytes(p.stat().st_size)))
        return EXIT_OK
    # mode == "strings"
    keyword = getattr(ns, "keyword", None) or None
    min_len = int(getattr(ns, "min_len", 4) or 4)
    results, info = scan_strings(
        p, min_len=min_len, keyword=keyword, limit=limit, limits=lim,
        progress_cb=lambda d, t: _report(app, d, t))
    if info.get("note"):
        print(paint(info["note"], "warn"))
    for off, txt in results:
        print("%08x  %s" % (off, txt))
    if info["truncated"]:
        print("tìm thấy ít nhất %d chuỗi (dừng sớm vì đủ giới hạn %d; "
              "dùng --limit lớn hơn hoặc --verbose — encoding: %s)"
              % (info["total"], len(results), info["encoding"]))
    else:
        print("tìm thấy %d chuỗi (encoding: %s)" % (info["total"], info["encoding"]))
    return EXIT_OK
