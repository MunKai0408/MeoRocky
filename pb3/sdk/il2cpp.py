# -*- coding: utf-8 -*-
"""Đọc chuỗi literal CHÍNH XÁC từ global-metadata.dat của IL2CPP
(tầng Unity chung — mọi game Unity IL2CPP đều có file này).

Vấn đề nó giải quyết: literal trong metadata nằm LIỀN NHAU (độ dài lưu
ở bảng riêng, không có ký tự NUL ngăn cách) nên quét byte-run ngây thơ
NỐI nhiều literal thành 1 chuỗi dài, và run dài > 4096 ký tự bị bỏ hẳn
(vd Tom: 'Daily challenge!', 'Daily Vault', 'Confirm resetting all local
achievements.' đều nằm trong run dài nên quét thô KHÔNG thấy chúng).
Đọc đúng bảng literal thì mỗi chuỗi tách chính xác từng cái.

Bố cục bảng literal theo version metadata [ĐÃ ĐO THẬT]:
- v31 (Unity 6000.0, Tom / PB3 / EK2): entry 8 byte (length, dataIndex);
  header: [tableOffset, tableByteSize, dataOffset, dataSize]; số entry =
  tableByteSize // 8. Kiểm chứng: Tom 12.090 entry, 11.819 literal sạch
  (97,8%), 3.191 chuỗi có khoảng trắng.
- v39 (9K v1.0.6): entry 4 byte dataIndex (bảng delta — độ dài suy ra từ
  entry kế tiếp); header: [tableOffset, tableByteSize, entryCount,
  dataOffset, dataSize]; literal i = data[idx[i]:idx[i+1]]; idx cuối =
  dataSize. Kiểm chứng: 9K 28.285 literal, 27.939 sạch (98,8%).
Version khác: TỪ CHỐI rõ ràng (không đoán bố cục) — quét thô vẫn chạy
được trên file đó (không phụ thuộc version).

Chỉ đọc, không sửa. File thường đọc bằng seek (không nạp cả file);
entry trong ZIP đọc cả member vào BytesIO (metadata ~7-21 MB ở các
game đã biết; từ chối member > 256 MB).
"""

from __future__ import annotations

import struct

from core.errors import PB3Error

__all__ = ["IL2CPP_MAGIC", "SUPPORTED_VERSIONS", "read_header",
           "iter_literals", "extract_literals", "LABEL"]

LABEL = "il2cpp-literals"

IL2CPP_MAGIC = 0xFAB11BAF
IL2CPP_MAGIC_BYTES = b"\xaf\x1b\xb1\xfa"  # struct.pack("<I", IL2CPP_MAGIC)
# version -> (entry_size, field_count_after_version)
# v31: (length, dataIndex) 8 byte; v39: delta dataIndex 4 byte.
SUPPORTED_VERSIONS = {31: "pairs", 39: "delta"}

MAX_LITERAL_BYTES = 1024 * 1024   # literal dài hơn: bỏ (không phải text)
MAX_MEMBER_BYTES = 256 * 1024 * 1024
_HEADER_READ = 1024               # đủ chứa các field cần đọc (v39: 380)


class MetadataHeader(object):
    """Header global-metadata.dat đã parse (chỉ các field cần dùng)."""

    def __init__(self, version, table_off, table_size, entry_count,
                 data_off, data_size, layout):
        self.version = version
        self.table_off = table_off
        self.table_size = table_size
        self.entry_count = entry_count
        self.data_off = data_off
        self.data_size = data_size
        self.layout = layout


def read_header(fh):
    """Đọc + validate header từ stream seekable (đã mở, ở vị trí bất kỳ).

    Raise PB3Error E_UNSUPPORTED_FORMAT (không phải IL2CPP) hoặc
    E_CORRUPT (header không khớp bố cục đã xác minh của version đó).
    """
    fh.seek(0)
    head = fh.read(_HEADER_READ)
    if len(head) < 28:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "file quá ngắn, không phải global-metadata.dat",
                       where="il2cpp")
    sanity, version = struct.unpack_from("<Ii", head, 0)
    if (sanity & 0xFFFFFFFF) != IL2CPP_MAGIC:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "không phải global-metadata.dat (magic sai)",
                       where="il2cpp")
    layout = SUPPORTED_VERSIONS.get(version)
    if layout is None:
        raise PB3Error(
            "E_UNSUPPORTED_FORMAT",
            "metadata version %d chưa xác minh bố cục bảng literal "
            "(đã xác minh: v31, v39) — dùng quét thô (raw scan) thay thế"
            % version, where="il2cpp")
    if layout == "pairs":
        table_off, table_size, data_off, data_size = struct.unpack_from(
            "<iiii", head, 8)
        if table_size < 0 or table_size % 8 != 0:
            raise PB3Error("E_CORRUPT",
                           "metadata v31: kích thước bảng literal %d "
                           "không chia hết cho 8" % table_size,
                           where="il2cpp")
        entry_count = table_size // 8
    else:  # delta
        table_off, table_size, entry_count, data_off, data_size = \
            struct.unpack_from("<iiiii", head, 8)
        if table_size < 0 or table_size % 4 != 0:
            raise PB3Error("E_CORRUPT",
                           "metadata v39: kích thước bảng literal %d "
                           "không chia hết cho 4" % table_size,
                           where="il2cpp")
        if entry_count != table_size // 4:
            raise PB3Error("E_CORRUPT",
                           "metadata v39: số entry %d không khớp kích "
                           "thước bảng %d byte" % (entry_count, table_size),
                           where="il2cpp")
    if (table_off < 0 or data_off < 0 or data_size < 0
            or table_off + table_size > data_off
            or entry_count < 0):
        raise PB3Error("E_CORRUPT",
                       "metadata v%d: offset bảng/dữ liệu không hợp lệ "
                       "(table=%d+%d, data=%d+%d)"
                       % (version, table_off, table_size,
                          data_off, data_size), where="il2cpp")
    return MetadataHeader(version, table_off, table_size, entry_count,
                          data_off, data_size, layout)


def _decode_literal(raw):
    """Giải mã 1 literal UTF-8 strict; trả None nếu không phải text sạch.

    Chấp nhận Unicode in được (vd 'Café', tiếng Việt, CJK) — literal
    IL2CPP là chuỗi UTF-8 thật, không chỉ ASCII. Ký tự điều khiển
    (trừ \n, \t) và byte nhị phân không giải mã được -> None.
    """
    if not raw:
        return ""
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if all(c.isprintable() or c in "\n\t" for c in s):
        return s
    return None


def _decode_one(fh, header, idx, idxs=None):
    """Giải mã 1 literal theo chỉ số (dùng cho kiểm chứng mẫu)."""
    if header.layout == "pairs":
        fh.seek(header.table_off + idx * 8)
        chunk = fh.read(8)
        if len(chunk) < 8:
            return None
        ln, off = struct.unpack("<II", chunk)
    else:  # delta
        if idxs is None:
            fh.seek(header.table_off)
            raw_table = fh.read(header.entry_count * 4)
            idxs = struct.unpack("<%dI" % header.entry_count, raw_table)
        off = idxs[idx]
        ln = (idxs[idx + 1] - off) if idx + 1 < header.entry_count \
            else header.data_size - off
    if ln < 0 or ln > MAX_LITERAL_BYTES or off + ln > header.data_size:
        return None
    fh.seek(header.data_off + off)
    return _decode_literal(fh.read(ln))


def _validate_sample(fh, header, count=20):
    """Kiểm chứng bố cục bảng bằng cách giải mã mẫu literal RẢI ĐỀU
    khắp bảng (không lấy đầu bảng: vài trăm literal đầu của Tom là
    byte nhị phân, không phải text). Ít nhất một nửa mẫu phải là text
    sạch, nếu không thì bố cục đã đoán sai — raise thay vì trả chuỗi rác.
    """
    if header.entry_count == 0:
        return
    idxs = None
    if header.layout == "delta":
        fh.seek(header.table_off)
        raw_table = fh.read(header.entry_count * 4)
        if len(raw_table) < header.entry_count * 4:
            raise PB3Error("E_CORRUPT",
                           "metadata v39: đọc bảng literal thiếu byte",
                           where="il2cpp")
        idxs = struct.unpack("<%dI" % header.entry_count, raw_table)
    n = min(count, header.entry_count)
    ok = 0
    tried = 0
    for k in range(n):
        idx = k * header.entry_count // n
        text = _decode_one(fh, header, idx, idxs)
        tried += 1
        # Chỉ tính literal dài >= 2 ký tự: bố cục sai vẫn có thể cho ra
        # lát cắt rỗng/ngắn tình cờ giải mã được, còn layout đúng thì
        # phần lớn literal là chuỗi thật dài hơn.
        if text is not None and len(text) >= 2:
            ok += 1
    if tried and ok * 2 < tried:
        raise PB3Error(
            "E_CORRUPT",
            "metadata v%d: mẫu %d literal rải đều chỉ có %d sạch — bố "
            "cục bảng không khớp mẫu đã xác minh, không đoán tiếp"
            % (header.version, tried, ok), where="il2cpp")


def _iter_literals_raw(fh, header, limit=None):
    """Yield (index, text_or_None, data_offset, length) thô, chưa lọc.

    Đọc bảng literal theo khối, seek từng literal để đọc đúng số byte.
    """
    fh.seek(header.table_off)
    if header.layout == "pairs":
        entry_size = 8
        remaining = header.entry_count
        idx = 0
        while remaining > 0:
            take = min(4096, remaining)
            # Seek tường minh về vị trí chunk bảng: vòng lặp trước đã
            # seek đi đọc literal nên con trỏ không còn ở bảng nữa.
            fh.seek(header.table_off + idx * entry_size)
            chunk = fh.read(take * entry_size)
            if len(chunk) < take * entry_size:
                raise PB3Error("E_CORRUPT",
                               "metadata v31: đọc bảng literal thiếu byte",
                               where="il2cpp")
            for j in range(take):
                ln, off = struct.unpack_from("<II", chunk, j * 8)
                if ln > MAX_LITERAL_BYTES or off + ln > header.data_size:
                    yield idx, None, off, ln
                else:
                    fh.seek(header.data_off + off)
                    raw = fh.read(ln)
                    yield idx, _decode_literal(raw), off, ln
                idx += 1
                if limit is not None and idx >= limit:
                    return
            remaining -= take
    else:  # delta
        # Đọc hết bảng delta (vài trăm KB) rồi seek từng literal.
        raw_table = fh.read(header.entry_count * 4)
        if len(raw_table) < header.entry_count * 4:
            raise PB3Error("E_CORRUPT",
                           "metadata v39: đọc bảng literal thiếu byte",
                           where="il2cpp")
        idxs = struct.unpack("<%dI" % header.entry_count, raw_table)
        for i in range(header.entry_count):
            if i + 1 < header.entry_count:
                off = idxs[i]
                ln = idxs[i + 1] - idxs[i]
            else:
                off = idxs[i]
                ln = header.data_size - idxs[i]
            if ln < 0 or ln > MAX_LITERAL_BYTES \
                    or off + ln > header.data_size:
                yield i, None, off, ln
            else:
                fh.seek(header.data_off + off)
                raw = fh.read(ln)
                yield i, _decode_literal(raw), off, ln
            if limit is not None and i + 1 >= limit:
                return


def iter_literals(fh, header):
    """Yield (index, text, data_offset, length) cho literal text sạch.

    Validate mẫu trước khi duyệt hết (fail-closed nếu bố cục sai).
    """
    _validate_sample(fh, header)
    for idx, text, off, ln in _iter_literals_raw(fh, header):
        if text is None:
            continue
        yield idx, text, off, ln


def extract_literals(stream_or_path, source, min_len=4,
                     max_results=10000, max_single_words=1000):
    """Trích literal IL2CPP từ 1 file/bundle đã mở (stream seekable) hoặc
    đường dẫn file. Lọc bằng cùng bộ lọc chuỗi hiển thị của rawscan
    (2 bucket strings/single_words), gắn nhãn il2cpp-literals.
    """
    from sdk.rawscan import LABEL as RAW_LABEL, classify_string
    import os

    own = isinstance(stream_or_path, (str, os.PathLike))
    fh = open(stream_or_path, "rb") if own else stream_or_path
    try:
        header = read_header(fh)
        strings = []
        single_words = []
        total = 0
        undecodable = 0
        dropped_noise = 0
        merged_long = 0
        truncated = False
        sw_trunc = False
        for idx, text, off, ln in iter_literals(fh, header):
            total += 1
            if text is None:
                undecodable += 1
                continue
            seg = text.strip()
            if not seg:
                continue
            if len(seg) < min_len:
                continue
            kind = classify_string(seg)
            if kind == "text":
                item = {"text": seg, "offset": int(header.data_off + off),
                        "length": len(seg), "encoding": "utf-8",
                        "literal_index": int(idx)}
                if " " in seg or "\n" in seg or "\t" in seg:
                    if len(strings) >= max_results:
                        truncated = True
                        continue
                    strings.append(item)
                else:
                    if len(single_words) >= max_single_words:
                        sw_trunc = True
                        continue
                    single_words.append(item)
            elif kind == "merged":
                merged_long += 1
            else:
                dropped_noise += 1
        return {
            "label": LABEL,
            "mode": "il2cpp",
            "source": str(source),
            "metadata_version": int(header.version),
            "literals_total": int(header.entry_count),
            "literals_read": total,
            "undecodable": undecodable,
            "strings": strings,
            "kept": len(strings),
            "single_words": single_words,
            "single_words_kept": len(single_words),
            "single_words_note": ("từ đơn — phần lớn là tên class/method/"
                                  "field, cần đối chiếu, không tính là "
                                  "text hiển thị"),
            "dropped_noise": dropped_noise,
            "merged_long": merged_long,
            "truncated": truncated,
            "single_words_truncated": sw_trunc,
            "raw_scan_label_note": ("khác quét thô (%s): mỗi chuỗi tách "
                                    "chính xác theo bảng literal, không "
                                    "nối literal kề nhau" % RAW_LABEL),
        }
    finally:
        if own:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
