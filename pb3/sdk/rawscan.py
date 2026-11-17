# -*- coding: utf-8 -*-
"""Quét chuỗi thô (raw-string scan, P1-5) — LAST RESORT, labelled unverified.

Đọc theo khối (streaming), KHÔNG BAO GIỜ nạp cả file vào RAM: mỗi lần chỉ
giữ 1 khối (mặc định 1 MB) + chuỗi đang đọc dở. File 200 MB quét được với
RAM đỉnh vài MB (cộng overhead Python) — an toàn cho tab Safari ~1-1,5 GB.

Quy tắc sử dụng (quyết định từ phân tích IPA-LOCALIZATION, A3):
- CHỈ chạy khi user bật tường minh (bridge op riêng `raw_scan`, extract
  không bao giờ gọi).
- Output ghi nhãn "unverified / raw-scan", tách riêng hoàn toàn khỏi file
  locale chính — KHÔNG BAO GIỜ trộn im lặng vào en.json/zh.json.
- Mỗi chuỗi báo rõ nguồn (file + offset byte) để đối chiếu được.
- Chỉ giữ chuỗi giống text hiển thị được; chuỗi kỹ thuật/mã bị lọc
  (đếm số lượng đã lọc để kiểm chứng, không xuất ra).

Hạn chế đã biết [ĐÃ ĐO THẬT trên global-metadata.dat của My Talking Tom]:
- Literal IL2CPP nằm LIỀN NHAU (độ dài lưu ở bảng riêng, không có NUL
  ngăn cách) nên quét byte-run ngây thơ NỐI nhiều literal thành 1 chuỗi
  dài. Scanner cắt chuỗi dài không có khoảng trắng thành mục "merged"
  (không tính là text) thay vì tin nguyên chuỗi dài đó.

Tương thích Python 3.9, stdlib-only.
"""

from __future__ import annotations

import io
import re
import struct

from core.errors import PB3Error

__all__ = ["RawScanner", "scan_bytes", "scan_file", "scan_zip_member",
           "scan_unity_stream", "scan_unity_bytes", "scan_target",
           "scan_macho_stream", "scan_macho_bytes",
           "classify_string", "LABEL"]

LABEL = "unverified-raw-scan"

DEFAULT_CHUNK = 1024 * 1024        # 1 MB mỗi lần đọc
DEFAULT_MIN_LEN = 4                # chuỗi ngắn hơn không tính
MAX_RUN_CHARS = 4096               # chuỗi dài hơn: cắt + gắn cờ merged
MAX_MERGED_WORDLESS = 60           # chuỗi dài hơn mà không có khoảng trắng: merged
DEFAULT_MAX_RESULTS = 10000        # số chuỗi nhiều-từ giữ tối đa
DEFAULT_MAX_SINGLE_WORDS = 1000    # số từ đơn giữ tối đa (bucket riêng)

# Regex tìm run trên bytes (tốc độ C). Độ dài tối thiểu đưa vào pattern
# (vd {4,}) để run ngắn không bao giờ vào danh sách cần sắp xếp/phân
# loại — vòng lặp Python từng byte + sort hàng triệu run ngắn từng làm
# quét 171 MB payload mất ~60 s.
def _ascii_run_re(min_len):
    return re.compile(rb"[\x20-\x7e\t\n]{%d,}" % max(2, int(min_len)))


def _utf16_run_re(min_len):
    return re.compile(rb"(?:[\x20-\x7e\t\n]\x00){%d,}" % max(2, int(min_len)))


_DIGIT_RE = re.compile(r"\d")
_UPPER_RE = re.compile(r"[A-Z]")

_GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NS_RE = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){2,}")
_PATH_RE = re.compile(r"(^|[\s\"'])(Assets/|/|\\|[A-Za-z]:\\)")
_EXT_RE = re.compile(r"\.(png|jpg|jpeg|mat|prefab|uxml|uss|dll|cs|shader"
                     r"|ttf|otf|wav|mp3|ogg|fbx|anim|controller)$",
                     re.IGNORECASE)
_PLACEHOLDER_ONLY_RE = re.compile(r"^(\{[^{}]*\}|%[0-9\.\*]*[sdifxX]|[\s\{\}%"
                                  r"0-9\.\,\:\;\-\+\*\/\\\(\)\[\]])+$")
_MULTI_NL_RE = re.compile(r"\n{2,}")


def _camel_humps(s):
    """Số 'hump' CamelCase (vd OnPlayerIdUpdate = 4, ClickerGame = 2)."""
    humps = 0
    prev_lower = False
    for i, c in enumerate(s):
        if c.isupper():
            if i == 0 or prev_lower:
                humps += 1
        prev_lower = c.islower() or c.isdigit()
    return humps


def _has_long_ascii_run(s, n=10):
    """True nếu có >= n ký tự ASCII liên tiếp tăng dần (bảng charset
    của font, vd '0123456789:;<=>?@ABC...')."""
    run = 1
    for a, b in zip(s, s[1:]):
        if ord(b) == ord(a) + 1:
            run += 1
            if run >= n:
                return True
        else:
            run = 1
    return False


def classify_string(s):
    """Phân loại 1 chuỗi: "text" (giữ) / "noise" (lọc) / "merged" (nối literal).

    Giữ chuỗi giống text hiển thị được; bỏ chuỗi kỹ thuật/mã. Không raise.
    """
    if not s or len(s) < 2:
        return "noise"
    if _GUID_RE.match(s):
        return "noise"
    # Chuỗi dài mà không có khoảng trắng: gần như chắc chắn là nhiều literal
    # bị nối (IL2CPP) hoặc mã/hash — không tin là 1 câu text.
    if len(s) > MAX_MERGED_WORDLESS and " " not in s and "\n" not in s:
        return "merged"
    # Chỉ còn placeholder/format (vd "{VAR1}", "%s %d"): không phải text.
    if _PLACEHOLDER_ONLY_RE.match(s):
        return "noise"
    # Đếm chữ cái (map C, không cấp phát list): quá ít chữ cái so với ký
    # tự (dãy số/ký hiệu của font, mã...) -> noise. Kiểm tra sớm vì đây
    # là cổng lọc rẻ và phân biệt rõ nhất với rác nhị phân.
    n_letters = sum(map(str.isalpha, s))
    if not n_letters:
        return "noise"
    if n_letters * 2 < len(s):
        return "noise"
    # Bảng charset của font (dãy ASCII tăng dần dài) — chỉ kiểm tra khi
    # chuỗi dài và có chữ số (vòng lặp Python, đắt nên phải có cổng).
    if len(s) > 40 and _DIGIT_RE.search(s) and _has_long_ascii_run(s):
        return "noise"
    # Namespace 3+ đoạn (vd MGS.Debugging.UILogHandler), tên kiểu kèm
    # assembly (UnityEngine.…), đường dẫn asset, đuôi file nhị phân.
    if _NS_RE.search(s) or s.startswith(("UnityEngine.", "Unity.")):
        return "noise"
    if _PATH_RE.search(s) or _EXT_RE.search(s):
        return "noise"
    # 1 token duy nhất (không khoảng trắng): tên mã/key, không phải text
    # hiển thị — snake_case có gạch dưới, hoặc CamelCase 3+ hump
    # (vd OnPlayerIdUpdate, UserStateSaveManager, MyTalkingTom).
    if " " not in s and "\n" not in s and "\t" not in s:
        if "_" in s:
            return "noise"
        if _UPPER_RE.search(s) and _camel_humps(s) >= 3:
            return "noise"
    return "text"


class RawScanner(object):
    """Quét chuỗi ASCII + UTF-16LE từ luồng byte, theo khối.

    Cách dùng: scanner = RawScanner(min_len=4); for chunk in ...:
    scanner.feed(chunk); ...; result = scanner.finish().
    Mỗi chuỗi trả về: {"text", "offset", "length", "encoding"}.
    Hai bucket riêng (chuỗi UI thật không bị chìm giữa hàng nghìn tên mã):
    - strings: chuỗi có khoảng trắng/xuống dòng (câu, nhãn, menu...) —
      đây là text hiển thị chính; cap bởi max_results.
    - single_words: 1 token duy nhất (không khoảng trắng) — phần lớn là
      tên class/method/field (vd CampaignFormatter); giữ riêng, cap bởi
      max_single_words, gắn nhãn "có thể là tên mã — cần đối chiếu".
    """

    def __init__(self, min_len=DEFAULT_MIN_LEN,
                 max_results=DEFAULT_MAX_RESULTS,
                 max_single_words=DEFAULT_MAX_SINGLE_WORDS):
        self.min_len = max(2, int(min_len))
        self.max_results = int(max_results)
        self.max_single_words = int(max_single_words)
        self._ascii_re = _ascii_run_re(self.min_len)
        self._utf16_re = _utf16_run_re(self.min_len)
        self._buf = bytearray()      # khối hiện tại + chuỗi dở đầu khối
        self._base = 0               # offset tuyệt đối của _buf[0]
        self._consumed = 0           # số byte đã chốt (đã quét xong)
        self.strings = []            # chuỗi text đã giữ (nhiều từ)
        self.single_words = []       # từ đơn đã giữ (có thể là tên mã)
        self.total_runs = 0          # tổng số run ứng viên >= min_len
        self.dropped_noise = 0
        self.merged_long = 0
        self.truncated = False       # đã vượt max_results
        self.single_words_truncated = False
        self._closed = False

    # -- tiện ích nội bộ -------------------------------------------------
    def _emit_run(self, text, offset, encoding):
        """Xử lý 1 run hoàn chỉnh: tách literal nối nhau (IL2CPP lưu liền
        nhau, thường cách bởi 2+ newline), phân loại từng đoạn, giữ nếu
        là text. Run quá dài mà không tách được: đếm merged, không tin."""
        self.total_runs += 1
        if len(text) > MAX_RUN_CHARS:
            # Run quá dài: gần như chắc chắn là dữ liệu nhị phân dính
            # nhau — cắt + đếm riêng, không tin nguyên chuỗi.
            self.merged_long += 1
            return
        spans = [(m.start(), m.end())
                 for m in _MULTI_NL_RE.finditer(text)]
        starts = [0] + [e for _s, e in spans]
        ends = [s for s, _e in spans] + [len(text)]
        for seg_start, seg_end in zip(starts, ends):
            seg = text[seg_start:seg_end]
            seg_off = offset + seg_start
            seg = seg.strip()
            if not seg:
                continue
            kind = classify_string(seg)
            if kind == "text":
                if " " in seg or "\n" in seg or "\t" in seg:
                    if len(self.strings) >= self.max_results:
                        self.truncated = True
                        continue
                    self.strings.append({"text": seg, "offset": int(seg_off),
                                         "length": len(seg),
                                         "encoding": encoding})
                else:
                    if len(self.single_words) >= self.max_single_words:
                        self.single_words_truncated = True
                        continue
                    self.single_words.append(
                        {"text": seg, "offset": int(seg_off),
                         "length": len(seg), "encoding": encoding})
            elif kind == "merged":
                self.merged_long += 1
            else:
                self.dropped_noise += 1

    def _scan_buffer(self, final):
        """Quét _buf, chốt mọi run hoàn chỉnh; giữ run dở cuối (nếu chưa final).

        Tìm run bằng regex trên bytes (tốc độ C, độ dài tối thiểu đã nằm
        trong pattern nên run ngắn không vào danh sách). Chỉ giữ lại byte
        chưa xử lý xong: run cuối có thể tiếp tục ở khối sau + tối đa
        2*min_len byte cuối (run ngắn hơn min_len ở cuối buffer có thể
        dài thêm ở khối sau — vd run UTF-16LE 3 ký tự + 1 byte lẻ).
        """
        buf = bytes(self._buf)
        n = len(buf)
        if n == 0:
            return
        runs = []  # (start, end, encoding)
        for m in self._ascii_re.finditer(buf):
            runs.append((m.start(), m.end(), "ascii"))
        for m in self._utf16_re.finditer(buf):
            runs.append((m.start(), m.end(), "utf-16le"))
        for m in self._utf16_re.finditer(buf[1:]):
            runs.append((m.start() + 1, m.end() + 1, "utf-16le"))
        # Sắp xếp theo vị trí; run ASCII và UTF-16LE trùng nhau (vd text
        # ASCII "A\x00B\x00" vừa là run ASCII ngắn vừa là run UTF-16LE):
        # ưu tiên run dài hơn (theo số ký tự), bỏ run bị chứa trong run khác.
        runs.sort(key=lambda r: (r[0], -(r[1] - r[0])))
        kept = []
        last_end = -1
        for start, end, enc in runs:
            if start < last_end:
                continue
            kept.append((start, end, enc))
            last_end = end
        # Chốt các run, trừ run cuối nếu nó có thể còn tiếp tục ở khối sau:
        # - ASCII: run chạm cuối buffer (byte tiếp theo có thể nối thêm).
        # - UTF-16LE: run kết thúc cách cuối buffer <= 1 byte (byte cuối
        #   có thể là nửa đầu của 1 cặp với byte đầu khối sau).
        held_start = None
        processed_upto = 0
        for idx, (start, end, enc) in enumerate(kept):
            is_last = (idx == len(kept) - 1)
            may_continue = (end >= n) if enc == "ascii" else (end >= n - 1)
            if is_last and may_continue and not final:
                held_start = start
                break
            if enc == "ascii":
                text = buf[start:end].decode("ascii", "replace")
            else:
                raw = buf[start:end]
                text = raw[0::2].decode("ascii", "replace")
            self._emit_run(text, self._base + start, enc)
            if end > processed_upto:
                processed_upto = end
        if final:
            keep_from = n
        elif held_start is not None:
            keep_from = held_start
        else:
            # Không có run dở: chỉ giữ đuôi buffer có thể là đầu của run
            # ngắn ở khối sau — tối đa 2*min_len byte (run UTF-16LE
            # min_len-1 cặp + 1 byte lẻ đầu cặp tiếp theo).
            keep_from = max(processed_upto, n - 2 * self.min_len)
            if keep_from > n:
                keep_from = n
        self._base += keep_from
        del self._buf[:keep_from]
        self._consumed = self._base

    # -- API công khai ----------------------------------------------------
    def feed(self, chunk):
        """Nạp thêm 1 khối byte (bytes/bytearray)."""
        if self._closed:
            raise ValueError("RawScanner đã finish, không feed thêm được")
        if not chunk:
            return
        self._buf.extend(chunk)
        self._scan_buffer(final=False)

    def finish(self):
        """Chốt quét, trả dict kết quả (đã gắn nhãn unverified)."""
        if not self._closed:
            self._closed = True
            self._scan_buffer(final=True)
        return {
            "label": LABEL,
            "strings": list(self.strings),
            "kept": len(self.strings),
            "single_words": list(self.single_words),
            "single_words_kept": len(self.single_words),
            "single_words_note": ("từ đơn — phần lớn là tên class/method/"
                                  "field, cần đối chiếu, không tính là "
                                  "text hiển thị"),
            "total_runs": self.total_runs,
            "dropped_noise": self.dropped_noise,
            "merged_long": self.merged_long,
            "truncated": self.truncated,
            "single_words_truncated": self.single_words_truncated,
        }


def _scan_stream(fh, source, min_len=DEFAULT_MIN_LEN,
                 max_results=DEFAULT_MAX_RESULTS,
                 max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                 chunk_size=DEFAULT_CHUNK):
    """Quét 1 luồng nhị phân đã mở (đọc theo khối)."""
    sc = RawScanner(min_len=min_len, max_results=max_results,
                    max_single_words=max_single_words)
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            break
        sc.feed(chunk)
    out = sc.finish()
    out["source"] = source
    out["mode"] = "raw"
    return out


def scan_bytes(data, source="<bytes>", min_len=DEFAULT_MIN_LEN,
               max_results=DEFAULT_MAX_RESULTS,
               max_single_words=DEFAULT_MAX_SINGLE_WORDS):
    """Quét bytes trong RAM (chỉ dùng cho input nhỏ / test)."""
    sc = RawScanner(min_len=min_len, max_results=max_results,
                    max_single_words=max_single_words)
    sc.feed(bytes(data))
    out = sc.finish()
    out["source"] = source
    out["mode"] = "raw"
    return out


def scan_file(path, min_len=DEFAULT_MIN_LEN,
              max_results=DEFAULT_MAX_RESULTS,
              max_single_words=DEFAULT_MAX_SINGLE_WORDS,
              chunk_size=DEFAULT_CHUNK):
    """Quét 1 file trên đĩa theo khối — không nạp cả file vào RAM."""
    with open(path, "rb") as fh:
        return _scan_stream(fh, str(path), min_len=min_len,
                            max_results=max_results,
                            max_single_words=max_single_words,
                            chunk_size=chunk_size)


def scan_zip_member(zip_path, member, min_len=DEFAULT_MIN_LEN,
                    max_results=DEFAULT_MAX_RESULTS,
                    max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                    chunk_size=DEFAULT_CHUNK):
    """Quét 1 entry trong ZIP/IPA theo khối (ZipExtFile stream, không nạp
    cả entry vào RAM). Dùng cho global-metadata.dat trong IPA Tom."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open(member, "r") as fh:
            return _scan_stream(fh, "%s::%s" % (zip_path, member),
                                min_len=min_len, max_results=max_results,
                                max_single_words=max_single_words,
                                chunk_size=chunk_size)


# ---------------------------------------------------------------------------
# Mach-O: quét theo section (__cstring + __ustring)
# ---------------------------------------------------------------------------
# Lý do: quét thô cả file Mach-O vô dụng [ĐÃ ĐO THẬT — Tom 89 MB]:
# 381.172 run, kept 10.000 (chạm cap) nhưng mẫu đầu toàn byte code
# ('AA9\ta'...) vì \t lọt qua kiểm tra whitespace. Quét đúng 2 section
# chứa chuỗi: __TEXT,__cstring (ASCII/UTF-8, chuỗi C) và
# __TEXT,__ustring (UTF-16LE). __objc_methname/__objc_classname chỉ có
# tên selector/class, __const chủ yếu là con trỏ/dữ liệu — không quét
# (đo trên Tom: methname 0 chuỗi nhiều từ).
# Đọc 1 lượt xuôi (streaming, không tua lại, không nạp cả file);
# section bắt đầu trước vị trí đọc thì bỏ qua kèm lý do. FAT: chọn slice
# arm64 (hoặc slice đầu nếu không có arm64). Mach-O 32-bit: từ chối rõ.

MACHO_MAGIC_64 = 0xFEEDFACF   # thin 64-bit, bytes cf fa ed fe
MACHO_MAGIC_32 = 0xFEEDFACE   # thin 32-bit, bytes ce fa ed fe
MACHO_FAT_MAGIC = 0xCAFEBABE  # FAT (big-endian), bytes ca fe ba be
MACHO_FAT_MAGIC_REV = 0xBEBAFECA  # FAT đọc little-endian, bytes be ba fe ca
MACHO_CPU_ARM64 = 0x0100000C
MACHO_CPU_ARM64_32 = 0x0000000C
LC_SEGMENT_64 = 0x19
MACHO_TEXT_SECTIONS = (("__TEXT", "__cstring"), ("__TEXT", "__ustring"))
_MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
                 b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
_MAX_MACHO_NCMDS = 10000
_MAX_MACHO_CMDSIZE = 1024 * 1024
_MAX_MACHO_NFAT = 1000
_MAX_MACHO_NSECTS = 1000


def _is_macho_magic(head):
    """4 byte đầu có phải magic Mach-O (thin 32/64 hoặc FAT) không."""
    return bytes(head[:4]) in _MACHO_MAGICS


def _read_exact(stream, n):
    """Đọc đúng n byte (lặp nếu stream trả ngắn, vd ZipExtFile); hết file
    giữa chừng thì E_CORRUPT."""
    parts = []
    got = 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            raise PB3Error("E_CORRUPT", "Mach-O: file ngắn hơn dự kiến "
                           "(cần %d byte, đọc được %d)" % (n, got))
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def _read_macho_sections(stream):
    """Parse header + load commands của Mach-O (thin 64-bit hoặc FAT).

    stream phải đứng ở byte 0 của file Mach-O (thin) hoặc ở byte 0 của
    file FAT. Đọc 1 lượt xuôi; khi trả về, stream đứng ngay sau header +
    load commands (data_start) — phần còn lại để scan_macho_stream đọc
    tiếp, không tua lại.

    Trả về (sections, info):
    - sections: list dict {segment, section, offset, size} (offset TUYỆT
      ĐỐI trong file, đã cộng slice_offset nếu FAT).
    - info: {kind: "thin"|"fat", cputype, slice_offset, data_start,
      sections_total}.
    """
    magic_b = _read_exact(stream, 4)
    if magic_b == b"\xca\xfe\xba\xbe":
        endian = ">"
    elif magic_b in (b"\xbe\xba\xfe\xca", b"\xcf\xfa\xed\xfe",
                     b"\xce\xfa\xed\xfe"):
        endian = "<"
    else:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "không phải Mach-O (magic %s)" % magic_b.hex())
    magic = struct.unpack(endian + "I", magic_b)[0]
    if magic in (MACHO_FAT_MAGIC, MACHO_FAT_MAGIC_REV):
        (nfat,) = struct.unpack(endian + "I", _read_exact(stream, 4))
        if nfat == 0 or nfat > _MAX_MACHO_NFAT:
            raise PB3Error("E_CORRUPT",
                           "Mach-O FAT: nfat_arch %d bất thường" % nfat)
        archs = []
        for _ in range(nfat):
            cpu, _sub, off, size, _al = struct.unpack(
                endian + "5I", _read_exact(stream, 20))
            archs.append((cpu, off, size))
        chosen = None
        for cpu, off, size in archs:
            if cpu in (MACHO_CPU_ARM64, MACHO_CPU_ARM64_32):
                chosen = (cpu, off, size)
                break
        if chosen is None:
            chosen = archs[0]
        _cpu, slice_offset, _size = chosen
        # Nhảy tới đầu slice (đọc + bỏ, không seek — giữ streaming).
        remaining = slice_offset - (8 + 20 * nfat)
        if remaining < 0:
            raise PB3Error("E_CORRUPT",
                           "Mach-O FAT: slice offset %d nằm trước header"
                           % slice_offset)
        while remaining > 0:
            step = stream.read(min(remaining, DEFAULT_CHUNK))
            if not step:
                raise PB3Error("E_CORRUPT",
                               "Mach-O FAT: file ngắn, không tới được slice")
            remaining -= len(step)
        # Đọc magic của slice (stream đang đứng ở đầu slice, chưa qua
        # 4 byte magic như đường thin) rồi mới parse header 64-bit.
        slice_magic = _read_exact(stream, 4)
        if slice_magic != b"\xcf\xfa\xed\xfe":
            if slice_magic == b"\xce\xfa\xed\xfe":
                raise PB3Error(
                    "E_UNSUPPORTED_FORMAT",
                    "Mach-O 32-bit chưa hỗ trợ (chỉ 64-bit và FAT/arm64)")
            raise PB3Error(
                "E_UNSUPPORTED_FORMAT",
                "slice FAT không phải Mach-O 64-bit (magic %s)"
                % slice_magic.hex())
        return _read_macho_thin(stream, slice_offset, kind="fat")
    if magic == MACHO_MAGIC_32:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "Mach-O 32-bit chưa hỗ trợ (chỉ 64-bit và FAT/arm64)")
    if magic != MACHO_MAGIC_64:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "Mach-O magic 0x%08x không hỗ trợ" % magic)
    return _read_macho_thin(stream, 0, kind="thin")


def _read_macho_thin(stream, slice_offset, kind):
    """Parse header 64-bit + load commands. stream đứng sau 4 byte magic.
    Trả về như _read_macho_sections."""
    rest = _read_exact(stream, 28)  # hết header 32 byte
    cputype, _sub, _ftype, ncmds, sizeofcmds, _flags, _res = \
        struct.unpack("<7I", rest)
    if ncmds > _MAX_MACHO_NCMDS:
        raise PB3Error("E_CORRUPT", "Mach-O: ncmds %d bất thường" % ncmds)
    if sizeofcmds > _MAX_MACHO_CMDSIZE:
        raise PB3Error("E_CORRUPT",
                       "Mach-O: sizeofcmds %d byte bất thường" % sizeofcmds)
    cmds = _read_exact(stream, sizeofcmds)
    sections = []
    pos = 0
    for _ in range(ncmds):
        if pos + 8 > len(cmds):
            raise PB3Error("E_CORRUPT",
                           "Mach-O: load command vượt sizeofcmds")
        cmd, cmdsize = struct.unpack_from("<2I", cmds, pos)
        if cmdsize < 8 or pos + cmdsize > len(cmds):
            raise PB3Error("E_CORRUPT",
                           "Mach-O: load command size %d bất thường"
                           % cmdsize)
        if cmd == LC_SEGMENT_64:
            if cmdsize < 72:
                raise PB3Error("E_CORRUPT",
                               "Mach-O: LC_SEGMENT_64 quá ngắn (%d)"
                               % cmdsize)
            segname = cmds[pos + 8:pos + 24].split(
                b"\x00", 1)[0].decode("ascii", "replace")
            nsects = struct.unpack_from("<I", cmds, pos + 64)[0]
            if nsects > _MAX_MACHO_NSECTS:
                raise PB3Error("E_CORRUPT",
                               "Mach-O: nsects %d bất thường" % nsects)
            for i in range(nsects):
                off = pos + 72 + 80 * i
                if off + 80 > pos + cmdsize:
                    raise PB3Error("E_CORRUPT",
                                   "Mach-O: section header vượt command")
                sectname = cmds[off:off + 16].split(
                    b"\x00", 1)[0].decode("ascii", "replace")
                seg2 = cmds[off + 16:off + 32].split(
                    b"\x00", 1)[0].decode("ascii", "replace")
                _addr, size, fileoff = struct.unpack_from(
                    "<QQI", cmds, off + 32)
                sections.append({"segment": seg2 or segname,
                                 "section": sectname,
                                 "offset": slice_offset + fileoff,
                                 "size": size})
        pos += cmdsize
    data_start = slice_offset + 32 + sizeofcmds
    return sections, {"kind": kind, "cputype": cputype,
                      "slice_offset": slice_offset,
                      "data_start": data_start,
                      "sections_total": len(sections)}


def scan_macho_stream(stream, source, min_len=DEFAULT_MIN_LEN,
                      max_results=DEFAULT_MAX_RESULTS,
                      max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                      chunk_size=DEFAULT_CHUNK, progress_fn=None):
    """Quét chuỗi trong Mach-O theo section (__cstring + __ustring).

    Đọc 1 lượt xuôi: header + load commands trước, rồi stream nốt phần
    còn lại theo chunk; mỗi chunk chỉ đưa phần chồng với section mục tiêu
    vào scanner của section đó (mỗi section 1 RawScanner, cap RIÊNG
    max_results — xem ghi chú ở chỗ tạo scanner). Không nạp cả file vào
    RAM, không tua lại. Section mục tiêu bắt đầu trước vị trí đọc thì bỏ
    qua (không quét nửa section — offset sẽ sai). Trả về dict như
    scan_unity_stream + mục sections (thống kê từng section),
    skipped_sections, kind (thin/fat)."""
    sections, info = _read_macho_sections(stream)
    targets = [s for s in sections
               if (s["segment"], s["section"]) in MACHO_TEXT_SECTIONS]
    targets.sort(key=lambda s: s["offset"])
    pos = info["data_start"]
    active = []
    skipped = []
    for s in targets:
        if s["size"] == 0:
            skipped.append(dict(s, reason="section rỗng (size 0)"))
        elif s["offset"] < pos:
            skipped.append(dict(
                s, reason="section bắt đầu trước vị trí đọc, "
                          "không tua lại được"))
        else:
            active.append(s)
    scanners = {}
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        cstart, cend = pos, pos + len(chunk)
        for s in active:
            lo = max(cstart, s["offset"])
            hi = min(cend, s["offset"] + s["size"])
            if lo >= hi:
                continue
            key = (s["segment"], s["section"])
            sc = scanners.get(key)
            if sc is None:
                # Cap RIÊNG theo section (không chia chung max_results):
                # section đầu (__cstring, ~10.000 chuỗi) mà ăn hết ngân
                # sách chung thì section sau (__ustring) còn 0 — đo trên
                # Tom: __ustring kept 0 thay vì 498. Mỗi section giữ tối
                # đa max_results chuỗi; tổng kept có thể vượt max_results,
                # xem rõ ở mục sections.
                sc = RawScanner(min_len=min_len, max_results=max_results,
                                max_single_words=max_single_words)
                scanners[key] = sc
            sc.feed(chunk[lo - cstart:hi - cstart])
        pos = cend
        if progress_fn is not None:
            progress_fn(pos, None)
    strings = []
    single_words = []
    total_runs = dropped_noise = merged_long = 0
    kept_total = 0
    truncated = False
    single_words_truncated = False
    section_stats = []
    for s in active:
        key = (s["segment"], s["section"])
        sc = scanners.get(key)
        stat = {"segment": s["segment"], "section": s["section"],
                "offset": s["offset"], "size": s["size"],
                "runs": 0, "kept": 0, "single_words": 0}
        if sc is not None:
            out = sc.finish()
            total_runs += out["total_runs"]
            dropped_noise += out["dropped_noise"]
            merged_long += out["merged_long"]
            truncated = truncated or out["truncated"]
            single_words_truncated = (single_words_truncated
                                      or out["single_words_truncated"])
            for st in out["strings"]:
                st["section"] = "%s,%s" % key
                st["offset"] = s["offset"] + st["offset"]
                strings.append(st)
            kept_total += len(out["strings"])
            single_words.extend(out["single_words"])
            stat["runs"] = out["total_runs"]
            stat["kept"] = len(out["strings"])
            stat["single_words"] = len(out["single_words"])
        section_stats.append(stat)
    single_words = single_words[:max_single_words]
    note = ("Mach-O %s: quét theo section __TEXT,__cstring + "
            "__TEXT,__ustring; __objc_methname/__objc_classname/__const "
            "không quét (chỉ tên selector/class/con trỏ, không phải text "
            "người dùng); mỗi section giữ tối đa %d chuỗi nhiều từ "
            "(cap riêng theo section)" % (info["kind"], max_results))
    return {"label": "macho", "mode": "macho", "source": source,
            "strings": strings, "kept": kept_total,
            "single_words": single_words,
            "single_words_kept": len(single_words),
            "single_words_note": "chuỗi 1 từ",
            "total_runs": total_runs, "dropped_noise": dropped_noise,
            "merged_long": merged_long, "truncated": truncated,
            "single_words_truncated": single_words_truncated,
            "sections": section_stats,
            "scanned_sections": ["%s,%s" % (s["segment"], s["section"])
                                 for s in active],
            "skipped_sections": skipped,
            "sections_total": info["sections_total"],
            "kind": info["kind"], "note": note}


def scan_macho_bytes(data, source, min_len=DEFAULT_MIN_LEN,
                     max_results=DEFAULT_MAX_RESULTS,
                     max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                     chunk_size=DEFAULT_CHUNK, progress_fn=None):
    """Quét Mach-O từ bytes đã có sẵn (vd member trong container không
    phải ZIP). File thường và member ZIP dùng scan_macho_stream để khỏi
    nạp cả file."""
    return scan_macho_stream(io.BytesIO(data), source, min_len=min_len,
                             max_results=max_results,
                             max_single_words=max_single_words,
                             chunk_size=chunk_size, progress_fn=progress_fn)


def scan_unity_stream(stream_or_path, source, min_len=DEFAULT_MIN_LEN,
                     max_results=DEFAULT_MAX_RESULTS,
                     max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                     chunk_size=DEFAULT_CHUNK, progress_fn=None):
    """Quét chuỗi thô trong PAYLOAD ĐÃ GIẢI NÉN của 1 bundle UnityFS
    (tầng Unity chung — mọi game Unity đều có cấu trúc này).

    Stream từng entry qua stream_bundle_entries: không ôm cả payload
    (vd Tom: payload 171 MB) vào RAM; mỗi entry quét bằng 1 RawScanner
    riêng (chuỗi không bao giờ nối xuyên qua ranh giới 2 entry) rồi
    gộp lại. Mỗi chuỗi ghi rõ entry .assets nguồn (trường "entry").
    Entry không phải SerializedFile (.resS/texture/âm thanh) cũng quét
    như chuỗi thô — phần lớn bị lọc là noise.
    progress_fn(done_entries, total_entries_or_None): báo sau mỗi entry.
    """
    from sdk import unity as unity_sdk
    all_strings = []
    all_single = []
    total_runs = 0
    dropped = 0
    merged = 0
    truncated = False
    sw_trunc = False
    per_entry = []
    done = 0
    for entry_name, entry_data in unity_sdk.stream_bundle_entries(
            stream_or_path, where=str(source)):
        sc = RawScanner(
            min_len=min_len,
            max_results=max(0, max_results - len(all_strings)),
            max_single_words=max(0, max_single_words - len(all_single)))
        for i in range(0, len(entry_data), chunk_size):
            sc.feed(entry_data[i:i + chunk_size])
        out = sc.finish()
        for s in out["strings"]:
            s["entry"] = entry_name
            all_strings.append(s)
        for s in out["single_words"]:
            s["entry"] = entry_name
            all_single.append(s)
        total_runs += out["total_runs"]
        dropped += out["dropped_noise"]
        merged += out["merged_long"]
        truncated = truncated or out["truncated"]
        sw_trunc = sw_trunc or out["single_words_truncated"]
        per_entry.append({"entry": entry_name, "size": len(entry_data),
                          "strings": len(out["strings"]),
                          "single_words": len(out["single_words"]),
                          "runs": out["total_runs"]})
        done += 1
        if progress_fn is not None:
            try:
                progress_fn(done, None)
            except Exception:  # noqa: BLE001 - tiến độ không làm hỏng quét
                pass
    return {
        "label": LABEL,
        "mode": "unity-bundle",
        "source": str(source),
        "strings": all_strings,
        "kept": len(all_strings),
        "single_words": all_single,
        "single_words_kept": len(all_single),
        "single_words_note": ("từ đơn — phần lớn là tên class/method/"
                              "field, cần đối chiếu, không tính là "
                              "text hiển thị"),
        "total_runs": total_runs,
        "dropped_noise": dropped,
        "merged_long": merged,
        "truncated": truncated,
        "single_words_truncated": sw_trunc,
        "entries": per_entry,
    }


def scan_unity_bytes(data, source, min_len=DEFAULT_MIN_LEN,
                    max_results=DEFAULT_MAX_RESULTS,
                    max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                    chunk_size=DEFAULT_CHUNK, progress_fn=None):
    """Quét payload bundle UnityFS đã ở trong RAM (vd entry trong ZIP)."""
    import io
    return scan_unity_stream(io.BytesIO(bytes(data)), source,
                             min_len=min_len, max_results=max_results,
                             max_single_words=max_single_words,
                             chunk_size=chunk_size, progress_fn=progress_fn)


def scan_target(path, entry=None, min_len=DEFAULT_MIN_LEN,
                max_results=DEFAULT_MAX_RESULTS,
                max_single_words=DEFAULT_MAX_SINGLE_WORDS,
                chunk_size=DEFAULT_CHUNK, progress_fn=None):
    """Điểm vào chung cho trích sâu: tự nhận diện mục tiêu rồi chọn chế độ.

    - File/bundle UnityFS (magic UnityFS) -> quét payload đã giải nén
      (mode "unity-bundle"): byte nén không có chuỗi đọc được nên quét
      thẳng file nén là vô nghĩa.
    - global-metadata.dat của IL2CPP (magic 0xFAB11BAF) -> đọc literal
      CHÍNH XÁC theo bảng literal (mode "il2cpp"): literal nằm liền nhau
      nên quét byte-run nối chúng lại và bỏ run dài.
    - Mach-O (magic 0xFEEDFACF thin 64-bit / 0xCAFEBABE FAT) -> quét
      theo section __TEXT,__cstring + __TEXT,__ustring (mode "macho"):
      quét thô cả file chỉ ra byte code, không ra text người dùng.
    - File thường / entry trong ZIP -> quét byte thô theo khối
      (mode "raw").
    Không hardcode theo game/build: chỉ dựa vào magic + cấu trúc Unity.
    """
    from sdk import il2cpp as il2cpp_sdk
    from sdk import unity as unity_sdk
    kw = {"min_len": min_len, "max_results": max_results,
          "max_single_words": max_single_words, "chunk_size": chunk_size,
          "progress_fn": progress_fn}
    kw_raw = {k: v for k, v in kw.items() if k != "progress_fn"}
    kw_il2cpp = {"min_len": min_len, "max_results": max_results,
                 "max_single_words": max_single_words}
    if entry is None:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if _is_macho_magic(head[:4]):
                # Streaming ngay trên handle đã mở (seek về 0) — khỏi
                # nạp cả file 89 MB vào RAM.
                fh.seek(0)
                return scan_macho_stream(fh, str(path), **kw)
        if head[:7] == unity_sdk.UNITYFS_MAGIC:
            out = scan_unity_stream(path, str(path), **kw)
        elif head[:4] == il2cpp_sdk.IL2CPP_MAGIC_BYTES:
            out = il2cpp_sdk.extract_literals(path, str(path), **kw_il2cpp)
        else:
            out = scan_file(path, **kw_raw)
            out["mode"] = "raw"
        return out
    # Entry trong container: thử ZIP trước (IPA là ZIP).
    with open(path, "rb") as fh:
        head = fh.read(4)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        import zipfile
        with zipfile.ZipFile(path, "r") as zf:
            with zf.open(entry, "r") as fh:
                mhead = fh.read(8)
            if mhead[:7] == unity_sdk.UNITYFS_MAGIC:
                data = zf.read(entry)
                return scan_unity_bytes(data, "%s::%s" % (path, entry), **kw)
            if mhead[:4] == il2cpp_sdk.IL2CPP_MAGIC_BYTES:
                info = zf.getinfo(entry)
                if info.file_size > il2cpp_sdk.MAX_MEMBER_BYTES:
                    raise PB3Error(
                        "E_TOO_LARGE",
                        "global-metadata.dat %d byte > giới hạn %d byte "
                        "cho đọc literal IL2CPP" % (
                            info.file_size, il2cpp_sdk.MAX_MEMBER_BYTES),
                        where=entry)
                import io
                data = zf.read(entry)
                return il2cpp_sdk.extract_literals(
                    io.BytesIO(data), "%s::%s" % (path, entry), **kw_il2cpp)
            if _is_macho_magic(mhead[:4]):
                # Streaming member ZIP (không zf.read cả 89 MB).
                with zf.open(entry, "r") as fh:
                    return scan_macho_stream(
                        fh, "%s::%s" % (path, entry), **kw)
        out = scan_zip_member(path, entry, **kw_raw)
        out["mode"] = "raw"
        return out
    # Container khác ZIP: qua VFS (có thể raise E_TOO_LARGE -> báo rõ).
    from core.limits import Limits
    from sdk import containers, discovery as disc
    vfs = disc.open_source_vfs(path, Limits())
    nested = containers.NestedVFS(vfs, limits=Limits())
    data = bytes(nested.read(entry, None))
    src = "%s::%s" % (path, entry)
    if data[:7] == unity_sdk.UNITYFS_MAGIC:
        return scan_unity_bytes(data, src, **kw)
    if data[:4] == il2cpp_sdk.IL2CPP_MAGIC_BYTES:
        import io
        return il2cpp_sdk.extract_literals(io.BytesIO(data), src,
                                           **kw_il2cpp)
    if _is_macho_magic(data[:4]):
        return scan_macho_bytes(data, src, **kw)
    out = scan_bytes(data, src, **kw_raw)
    out["mode"] = "raw"
    return out
