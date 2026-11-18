# Module dò Heaps PAK (port từ IPA-LOCALIZATION js/modules/pak_heads.js).
# Chỉ probe-first/fail-closed: đọc phần ĐẦU của file .pak, thử các bố cục
# cây Heaps đã biết, chỉ nhận bố cục nào kết thúc cây đúng chỗ có "DATA".
# Không giải nén, không đoán mò, không ném lỗi khi đầu vào không khớp.
#
# NHÃN BẰNG CHỨNG: mọi kết luận về định dạng pak là [HYPOTHESIS] cho tới khi
# đối chiếu pak thật. Đuôi .pak KHÔNG chứng minh Unreal — Dead Cells
# (Heaps/HashLink) cũng dùng .pak nhưng magic ở ĐẦU file là "PAK"; Unreal
# pak có magic ở FOOTER (0x5A6F12E1) nên module này chỉ kết luận Heaps khi
# magic + cây khớp, còn lại ghi "unknown"/"unverified".
# Bằng chứng thật đã có phía JS (IPA-LOCALIZATION HANDOFF v0.16.1–v0.16.3):
# cây res4.pak của Dead Cells thật ĐỌC ĐƯỢC nhờ bỏ qua dải hex ASCII 64 ký
# tự sau header 12 byte; data.cdb trong đó cho 3192 dòng localizable.
# Port Python này chưa chạy trên pak thật (tải Dead Cells bị Drive chặn quota
# 00:33 +07) nên giữ nhãn [HYPOTHESIS] tới khi chạy lại được.
from __future__ import annotations

import math
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "pak_heads/1"

HEAD_BYTES = 2 * 1048576
MAX_PAK = 64
MAX_TREE_ENTRIES = 200000
MAX_DEPTH = 32
SAMPLE_PATHS = 2000

_HEX_BYTE = re.compile(r"[0-9a-fA-F]")
_LANG_RE = re.compile(
    r"(?:^|[/_.\-])(en|fr|de|es|ru|zh|ja|ko|pt|it|vi|tr|pl|cs|uk)(?:[/_.\-]|$)",
    re.IGNORECASE)
_TEXT_RE = re.compile(
    r"(lang|locale|locali[sz]|i18n|l10n|string|text|dialog|message|translat"
    r"|subtitle|\.cdb$|\.po$|\.xml$|\.json$|\.csv$|\.txt$)",
    re.IGNORECASE)


def _hex(data: bytes, n: int) -> str:
    return " ".join("%02x" % b for b in data[:n])


def _ascii(data: bytes, off: int, n: int) -> str:
    return "".join(chr(b) for b in data[off:off + n])


def _round2(x: float) -> float:
    """Làm tròn 2 chữ số kiểu Math.round của JS (nửa lên)."""
    return math.floor(x * 100 + 0.5) / 100.0


def _is_hex_byte(b: int) -> bool:
    return (0x30 <= b <= 0x39) or (0x61 <= b <= 0x66) or (0x41 <= b <= 0x46)


class _Walk(object):
    """Kết quả thử 1 bố cục cây (thuần hàm, không I/O)."""

    def __init__(self) -> None:
        self.pos = 0
        self.truncated = False
        self.bad = None  # type: Optional[str]
        self.entries = 0
        self.files = []  # type: List[Dict[str, Any]]
        self.dirs = 0
        self.top = {}  # type: Dict[str, int]


def _walk_tree(head: bytes, csum: int, root_named: bool,
               start: Optional[int] = None) -> _Walk:
    r = _Walk()
    r.pos = start if start is not None else 12

    def need(n: int) -> bool:
        if r.pos + n > len(head):
            r.truncated = True
            return False
        return True

    def walk(prefix: str, depth: int) -> None:
        if r.truncated or r.bad is not None:
            return
        if depth > MAX_DEPTH:
            r.bad = "cây quá sâu"
            return
        name = ""
        if depth > 0 or root_named:
            if not need(1):
                return
            name_len = head[r.pos]
            r.pos += 1
            if not need(name_len):
                return
            try:
                name = head[r.pos:r.pos + name_len].decode("utf-8")
            except UnicodeDecodeError:
                r.bad = "tên entry không phải UTF-8 tại offset %d" % r.pos
                return
            r.pos += name_len
            if "\x00" in name or "/" in name:
                r.bad = "tên entry đáng ngờ"
                return
        if not need(1):
            return
        flags = head[r.pos]
        r.pos += 1
        full = "" if depth == 0 else (prefix + "/" + name if prefix else name)
        if r.entries >= MAX_TREE_ENTRIES:
            r.bad = "vượt giới hạn số entry"
            return
        r.entries += 1
        if flags & 1:
            if not need(4):
                return
            count = struct.unpack_from("<I", head, r.pos)[0]
            r.pos += 4
            if count > MAX_TREE_ENTRIES:
                r.bad = "số con thư mục bất thường (%d)" % count
                return
            r.dirs += 1
            for _ in range(count):
                walk(full, depth + 1)
                if r.truncated or r.bad is not None:
                    return
        else:
            if not need(8 + csum):
                return
            pos, size = struct.unpack_from("<II", head, r.pos)
            r.pos += 8 + csum
            r.files.append({"path": full, "pos": pos, "size": size})
            top = full.split("/")[0]
            r.top[top] = r.top.get(top, 0) + 1

    walk("", 0)
    return r


def probe_header(head: bytes, header_size: Optional[int]) -> Dict[str, Any]:
    """Chẩn đoán header khi không khớp bố cục: hex sau header 12 byte,
    entropy Shannon và tỉ lệ ASCII in được trên vùng header đã đọc."""
    n = max(0, min(len(head), header_size or len(head)) - 12)
    counts = [0] * 256
    printable = 0
    for i in range(12, 12 + n):
        counts[head[i]] += 1
        if 32 <= head[i] < 127:
            printable += 1
    ent = 0.0
    for c in counts:
        if c:
            q = c / n
            ent -= q * math.log2(q)
    hx = 12
    while hx < len(head) and _is_hex_byte(head[hx]):
        hx += 1
    if not n:
        interpretation = "không đủ dữ liệu"
    elif ent > 7.5:
        interpretation = ("[HYPOTHESIS] entropy cao: header có thể bị "
                          "nén/mã hóa/xáo trộn")
    elif ent < 5:
        interpretation = ("[HYPOTHESIS] entropy thấp: cấu trúc thô, có thể "
                          "là cây với bố cục khác")
    else:
        interpretation = "[HYPOTHESIS] entropy trung bình: chưa kết luận"
    hs = header_size or 0
    return {
        "hex_prefix_len": hx - 12,
        "bytes_after_hex_hex": _hex(head[hx:hx + 96], 96),
        "bytes_before_header_end_hex": _hex(
            head[max(0, hs - 16):hs], 16),
        "data_marker_at_header_size": (
            _ascii(head, hs, 4) if hs and hs + 4 <= len(head) else None),
        "bytes_12_76_hex": _hex(head[12:76], 64),
        "sampled_bytes": n,
        "entropy_bits_per_byte": _round2(ent) if n else None,
        "printable_ratio": _round2(printable / n) if n else None,
        "interpretation": interpretation,
    }


def parse_heaps_pak(head: bytes,
                    pak_size: Optional[int] = None) -> Dict[str, Any]:
    """Đọc thử header Heaps PAK từ phần đầu. Thử vài bố cục; chỉ nhận bố
    cục nào kết thúc cây đúng chỗ có "DATA". Thuần hàm (không I/O)."""
    out = {
        "format": "heaps_pak", "status": "unverified",
        "evidence": "HYPOTHESIS", "version": None, "header_size": None,
        "data_size": None, "layout": None, "tree_complete": False,
        "entries_total": 0, "files_total": 0, "dirs_total": 0,
        "languages_hint": [], "ext_histogram": {}, "top_dirs": [],
        "sample_paths": [], "text_like_paths": [], "notes": [],
    }  # type: Dict[str, Any]
    if len(head) < 12 or _ascii(head, 0, 3) != "PAK":
        out["status"] = "not_heaps_pak"
        out["notes"].append('Không có magic "PAK" ở đầu file.')
        return out
    out["version"] = head[3]
    header_size, data_size = struct.unpack_from("<II", head, 4)
    out["header_size"] = header_size
    out["data_size"] = data_size
    if header_size < 12 or (pak_size is not None and header_size > pak_size):
        out["notes"].append("header_size ngoài phạm vi file → không đọc cây.")
        return out
    if (pak_size is not None
            and header_size + data_size > pak_size + 16):
        out["notes"].append(
            "header_size + data_size lớn hơn kích thước file (lệch %d byte)."
            % (header_size + data_size - pak_size))

    def at(off: int) -> Optional[str]:
        return _ascii(head, off, 4) if off + 4 <= len(head) else None

    layouts = [
        {"csum": 4, "root_named": True, "start": None},
        {"csum": 4, "root_named": False, "start": None},
        {"csum": 0, "root_named": True, "start": None},
        {"csum": 0, "root_named": False, "start": None},
        {"csum": 8, "root_named": True, "start": None},
        {"csum": 8, "root_named": False, "start": None},
        {"csum": 16, "root_named": True, "start": None},
        {"csum": 32, "root_named": True, "start": None},
        # [HYPOTHESIS] pak Dead Cells thật có 4 byte lạ ngay sau header
        # 12 byte → thử cây bắt đầu ở offset 16.
        {"csum": 4, "root_named": True, "start": 16},
        {"csum": 4, "root_named": False, "start": 16},
        {"csum": 0, "root_named": True, "start": 16},
        {"csum": 0, "root_named": False, "start": 16},
    ]  # type: List[Dict[str, Any]]
    # [HYPOTHESIS] Dead Cells 3.5.9 thật (log): sau header 12 byte là chuỗi
    # hex ASCII 64 ký tự (giống nhau ở mọi pak, dạng hash) → cây bắt đầu
    # SAU dải hex này.
    hx = 12
    while hx < len(head) and _is_hex_byte(head[hx]):
        hx += 1
    if hx - 12 >= 32:
        out["hex_prefix"] = {
            "length": hx - 12,
            "ascii": _ascii(head, 12, min(hx - 12, 128)),
        }
        dyn = []  # type: List[Dict[str, Any]]
        for st in (hx, hx + 1, hx + 2, hx + 4):
            for cs in (4, 0):
                for rn in (True, False):
                    dyn.append({"csum": cs, "root_named": rn, "start": st})
        layouts = dyn + layouts
    else:
        out["hex_prefix"] = None
    out["data_marker_at_header_size"] = at(header_size)
    best = None  # type: Optional[_Walk]
    best_lay = None  # type: Optional[Dict[str, Any]]
    data_start = None  # type: Optional[int]
    tried = []  # type: List[str]
    for lay in layouts:
        w = _walk_tree(head, lay["csum"], lay["root_named"], lay["start"])
        ds = None
        if not w.truncated and w.bad is None:
            ds = w.pos + 4 if at(w.pos) == "DATA" else None
        tried.append("csum=%d%s%s: %s" % (
            lay["csum"],
            "" if lay["root_named"] else ",gốc không tên",
            (",bắt đầu@%d" % lay["start"]) if lay["start"] else "",
            w.bad or ("cụt" if w.truncated else (
                "OK" if ds is not None
                else "không thấy DATA tại offset %d" % w.pos))))
        if ds is not None:
            best = w
            best_lay = lay
            data_start = ds
            break
        if best is None:
            best = w
            best_lay = lay
        if w.truncated and pak_size is not None and len(head) < pak_size:
            best = w
            best_lay = lay
            break  # header dài hơn phần đã đọc: dừng thử, cần đọc thêm
    assert best is not None and best_lay is not None
    out["layout"] = {
        "checksum_bytes": best_lay["csum"],
        "root_named": best_lay["root_named"],
        "tree_start": best_lay["start"] or 12,
    }
    out["layouts_tried"] = tried
    w = best
    out["entries_total"] = w.entries
    out["dirs_total"] = w.dirs
    out["files_total"] = len(w.files)
    if data_start is not None:
        out["status"] = "ok"
        out["tree_complete"] = True
        out["tree_end"] = w.pos
        out["data_start"] = data_start
    elif w.truncated:
        out["status"] = "partial"
        out["notes"].append(
            "Header dài hơn phần đầu đã đọc (%d byte); cần đọc ≥ %d byte "
            "để thấy đủ cây." % (len(head), header_size))
    else:
        out["status"] = "unverified"
        out["header_probe"] = probe_header(head, header_size)
        out["notes"].append(
            "Không bố cục nào khớp \"DATA\": " + " | ".join(tried))
    if out["status"] == "ok":
        oob = sum(1 for f in w.files
                  if f["pos"] + f["size"] > data_size)
        if oob:
            out["notes"].append(
                "%d file có pos+size vượt data_size → bố cục có thể sai."
                % oob)
    files = w.files
    hist = {}  # type: Dict[str, int]
    langs = {}  # type: Dict[str, int]
    for f in files:
        ext = f["path"].rsplit(".", 1)[-1].lower() if "." in f["path"] else "(không đuôi)"
        if "." not in f["path"]:
            ext = "(không đuôi)"
        hist[ext] = hist.get(ext, 0) + 1
        m = _LANG_RE.search(f["path"])
        if m:
            langs[m.group(1).lower()] = 1
    out["_files"] = files
    out["ext_histogram"] = hist
    out["languages_hint"] = sorted(langs)
    out["top_dirs"] = [
        {"name": k or "(gốc)", "files": v}
        for k, v in sorted(w.top.items(), key=lambda kv: -kv[1])[:20]
    ]
    text_like = [f for f in files if _TEXT_RE.search(f["path"])]
    out["text_like_paths"] = [f["path"] for f in text_like[:300]]
    out["text_like_total"] = len(text_like)
    out["sample_paths"] = [f["path"] for f in files[:SAMPLE_PATHS]]
    out["notes"].append("[HYPOTHESIS] Tên file không phải bằng chứng file "
                        "chứa text hiển thị; chưa qua Cổng 0.")
    return out


def _header_at(data: bytes, start: int) -> Optional[Tuple[int, int, int]]:
    """Đọc header PAK tại `start`: (version, header_size, data_size)
    hoặc None nếu không hợp lệ (magic sai, version > 16, header_size
    quá nhỏ/lớn, header_size + data_size vượt quá file)."""
    if len(data) < start + 12 or _ascii(data, start, 3) != "PAK":
        return None
    version = data[start + 3]
    if version > 16:
        return None
    header_size = struct.unpack_from("<I", data, start + 4)[0]
    data_size = struct.unpack_from("<I", data, start + 8)[0]
    if header_size < 12 or header_size > len(data):
        return None
    if start + header_size + data_size > len(data):
        return None
    return version, header_size, data_size


def is_heaps_pak_head(head: bytes) -> bool:
    """Kiểm tra nhanh phần đầu có phải Heaps PAK không (dùng cho nhận diện
    engine sau này): magic "PAK" + version ≤ 16 + header_size hợp lệ,
    ở offset 0 hoặc offset 64 (layout Dead Cells: 64 ký tự hex ASCII rồi
    mới tới "PAK"). Lưu ý: head 64 byte như discovery/format_probe đang
    đọc thì KHÔNG thấy được layout offset 64 (cần ≥ 76 byte) — việc nối
    vào nhận diện engine + tăng kích thước head là bước tiếp theo, ngoài
    phạm vi nhánh này (engine detection không nằm trong scope)."""
    if _header_at(head, 0) is not None:
        return True
    if (len(head) >= 64 and all(_is_hex_byte(b) for b in head[:64])
            and _header_at(head, 64) is not None):
        return True
    return False


def pak_files(head: bytes,
              pak_size: Optional[int] = None) -> List[Dict[str, Any]]:
    """Danh sách file trong pak khi cây đọc được (status ok), rỗng nếu không.
    Mỗi mục {path, pos, size} với pos/size tính từ đầu vùng DATA."""
    res = parse_heaps_pak(head, pak_size)
    if res["status"] != "ok":
        return []
    return [{"path": f["path"], "pos": f["pos"], "size": f["size"]}
            for f in res["_files"]]
