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
- v24/v27/v29/v31 (pairs): entry 8 byte (length, dataIndex); header:
  [tableOffset, tableByteSize, dataOffset, dataSize]; số entry =
  tableByteSize // 8. v31 kiểm chứng trên file thật: Tom 12.090 entry,
  11.819 literal sạch (97,8%), 3.191 chuỗi có khoảng trắng. v24/27/29
  cùng layout theo Il2CppDumper (bảng literal đọc bằng
  ReadArray(stringLiteralOffset, stringLiteralCount / sizeof(
  Il2CppStringLiteral)) với entry 8 byte, không phân biệt version)
  và rea/docs/il2cpp-metadata.md ("Versions 24 to 31 record no
  counts" — header 4 field, không có số entry; v24 header 264 byte,
  Unity 2019.4.41f1; v27 Unity 2020.3.49f1; v29 Unity 2022.3.30f1).
  Chỉ kiểm chứng [FIXTURE] (fixture tổng hợp) cho v24/27/29 — bộ mẫu
  thật chỉ có v31/v39. v32–v38 chưa từng phát hành công khai
  (Il2CppDumper PR #925), v40+ chưa tồn tại — vẫn từ chối.
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

import io
import os
import re
import struct

from core.errors import PB3Error

__all__ = ["IL2CPP_MAGIC", "SUPPORTED_VERSIONS", "read_header",
           "iter_literals", "extract_literals", "relayout_literals",
           "LABEL"]

LABEL = "il2cpp-literals"

IL2CPP_MAGIC = 0xFAB11BAF
IL2CPP_MAGIC_BYTES = b"\xaf\x1b\xb1\xfa"  # struct.pack("<I", IL2CPP_MAGIC)
# version -> (entry_size, field_count_after_version)
# v24/v27/v29/v31: (length, dataIndex) 8 byte; v39: delta dataIndex 4 byte.
SUPPORTED_VERSIONS = {24: "pairs", 27: "pairs", 29: "pairs", 31: "pairs",
                      39: "delta"}

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
            "(đã xác minh: v24, v27, v29, v31, v39) — dùng quét thô "
            "(raw scan) thay thế"
            % version, where="il2cpp")
    if layout == "pairs":
        table_off, table_size, data_off, data_size = struct.unpack_from(
            "<iiii", head, 8)
        if table_size < 0 or table_size % 8 != 0:
            raise PB3Error("E_CORRUPT",
                           "metadata v%d: kích thước bảng literal %d "
                           "không chia hết cho 8" % (version, table_size),
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
                               "metadata v%d: đọc bảng literal thiếu byte"
                               % header.version,
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


# ---------------------------------------------------------------------------
# Bảng dịch literals (IL2CPP literals -> tab Dịch) + vá literal tại chỗ
# ---------------------------------------------------------------------------
# Vấn đề (audit §A1, [ĐÃ ĐO THẬT]): text chính của game kiểu Tom nằm
# trong bảng literal IL2CPP nhưng đường đi bị đứt ở cả 3 khâu — trích chỉ
# ra file xem (không thành file locale), output {"label","strings":[...]}
# không phải dict key->value nên tab Dịch stringify cả list thành 1 chuỗi
# khổng lồ, và không có provenance nên không có đường ghi lại.
#
# Thiết kế:
# - Bảng dịch là dict thuần {LIT_NNNNNN: text} — key mã hoá literal_index
#   nên tab Dịch (translate_json / translate_json_google) ăn được như mọi
#   file locale, không cần sửa đường dịch. Manifest kèm theo ghi rõ từng
#   key ứng với literal nào (index, offset, độ dài byte, text gốc) để
#   bước vá verify đúng file metadata trước khi ghi.
# - Vá TẠI CHỖ (in-place), fail-closed từng literal, KHÔNG đổi kích thước
#   file hay offset nào khác ngoài length của literal được vá (v31):
#   * v31 (pairs length,dataIndex): bản dịch ngắn hơn hoặc bằng gốc
#     (tính theo BYTE UTF-8) thì ghi đè + đệm NUL + sửa length trong
#     bảng; dài hơn thì không ghi được (lý do too_long).
#   * v39 (delta): độ dài suy ra từ index kế tiếp nên chỉ ghi được khi
#     đúng bằng byte; ngắn/dài hơn đều không ghi được (length_must_match).
# - RE-LAYOUT toàn bộ blob literal (relayout_literals, F1): ghi được bản
#   dịch DÀI HƠN gốc ở cả v31 lẫn v39 — dựng lại blob mới (mọi literal
#   xếp theo đúng thứ tự index, literal đã vá mang byte mới), dời NGUYÊN
#   VẸN mọi section sau blob đi delta = span mới - span cũ, sửa dataSize
#   + offset của các section đó trong header, giữ căn lề 4 byte. Xem
#   ghi chú đầu mục re-layout: khảo sát header v31/v39, quy tắc
#   dataSize theo version, và verify 5 mục sau khi dựng lại.
# - Phương án thay thế đã cân nhắc (KHÔNG làm): (a) viết tắt/bảng rút
#   gọn theo glossary — ĐÁNH ĐỔI: bản dịch bị bóp nghĩa, phải duy trì
#   glossary tay từng game, vẫn kẹt khi từ rút gọn còn dài hơn gốc;
#   (b) cắt ngắn (truncate) — ĐÁNH ĐỔI: mất nghĩa, câu dở dang; (c) chỉ
#   vá tại chỗ — ĐÁNH ĐỔI: phần lớn chuỗi tiếng Việt (dài hơn tiếng Anh)
#   ở lại "chưa ghi được". Re-layout là phương án duy nhất giữ nguyên
#   nghĩa bản dịch; rủi ro còn lại là game có chạy được với metadata đã
#   dời section không — KHÔNG có runtime Unity để kiểm chứng nên đây là
#   [HYPOTHESIS] cho tới khi test trên máy thật (xem báo cáo F1).
# - Verify sau khi vá: đọc lại TOÀN BỘ literal từ bytes đã vá — literal
#   đã vá phải đúng text mới, literal còn lại phải giống hệt gốc; header
#   (version, table/data offsets) không đổi; kích thước file không đổi
#   (vá tại chỗ) hoặc đổi đúng delta (re-layout).

TABLE_FILE = "il2cpp-literals.json"
MANIFEST_FILE = "il2cpp-literals.manifest.json"
PATCHED_FILE = "global-metadata.patched.dat"
PATCHED_SIDECAR = "global-metadata.patched.json"

_TABLE_KEY_RE = re.compile(r"^LIT_(\d+)$")


def table_key(index):
    """Key bảng dịch cho 1 literal: mã hoá literal_index, vd LIT_000123."""
    return "LIT_%06d" % int(index)


def literals_to_table(result, include_single_words=False):
    """Kết quả extract_literals -> (bảng dịch dict, manifest dict).

    Bảng chỉ lấy bucket "strings" (chuỗi nhiều từ = text hiển thị chính)
    mặc định; "single_words" phần lớn là tên class/method/field nên chỉ
    thêm khi include_single_words=True (ghi rõ trong manifest). Key theo
    literal_index nên 2 literal trùng text vẫn là 2 key khác nhau.
    Manifest ghi cho mỗi key: literal_index, offset tuyệt đối trong file,
    độ dài BYTE UTF-8 của text gốc, text gốc, encoding.
    """
    table = {}
    manifest = {}
    for item in result.get("strings", []) or []:
        idx = int(item["literal_index"])
        key = table_key(idx)
        text = item["text"]
        table[key] = text
        manifest[key] = {
            "literal_index": idx,
            "offset": int(item["offset"]),
            "byte_length": len(text.encode("utf-8")),
            "source": text,
            "encoding": item.get("encoding", "utf-8"),
        }
    if include_single_words:
        for item in result.get("single_words", []) or []:
            idx = int(item["literal_index"])
            key = table_key(idx)
            text = item["text"]
            table[key] = text
            manifest[key] = {
                "literal_index": idx,
                "offset": int(item["offset"]),
                "byte_length": len(text.encode("utf-8")),
                "source": text,
                "encoding": item.get("encoding", "utf-8"),
                "single_word": True,
            }
    return table, manifest


def read_metadata_bytes(path, entry=None):
    """Đọc bytes global-metadata.dat: file thường (entry=None) hoặc
    member trong ZIP/IPA (entry=đường dẫn member).

    Trả (data, source_label). Member/file > MAX_MEMBER_BYTES thì từ chối
    trước khi đọc (không nạp cả trăm MB). Entry không có trong ZIP thì
    E_INPUT_NOT_FOUND (không để KeyError lọt ra ngoài).
    """
    if entry:
        import zipfile
        try:
            with zipfile.ZipFile(path, "r") as zf:
                try:
                    info = zf.getinfo(entry)
                except KeyError:
                    raise PB3Error(
                        "E_INPUT_NOT_FOUND",
                        "không tìm thấy entry %r trong file ZIP/IPA"
                        % entry, where=str(entry))
                if info.file_size > MAX_MEMBER_BYTES:
                    raise PB3Error(
                        "E_TOO_LARGE",
                        "global-metadata.dat %d byte > giới hạn %d byte "
                        "cho đọc literal IL2CPP"
                        % (info.file_size, MAX_MEMBER_BYTES), where=entry)
                return zf.read(entry), "%s::%s" % (path, entry)
        except PB3Error:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "không mở được file ZIP/IPA (%s)"
                           % type(exc).__name__, where=str(path))
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise PB3Error("E_INPUT_NOT_FOUND",
                       "không đọc được file %r (%s)" % (path, exc),
                       where=str(path))
    if size > MAX_MEMBER_BYTES:
        raise PB3Error("E_TOO_LARGE",
                       "file %d byte > giới hạn %d byte cho đọc literal "
                       "IL2CPP" % (size, MAX_MEMBER_BYTES), where=str(path))
    with open(path, "rb") as fh:
        return fh.read(), str(path)


def _read_literal_entries(fh, header):
    """Đọc bảng literal thành list (byte_length, data_index) theo index.

    v31: đọc trực tiếp từ bảng (length, dataIndex). v39: suy độ dài từ
    bảng delta (index kế tiếp trừ index hiện tại, cuối = dataSize).
    """
    if header.layout == "pairs":
        fh.seek(header.table_off)
        raw = fh.read(header.entry_count * 8)
        if len(raw) < header.entry_count * 8:
            raise PB3Error("E_CORRUPT",
                           "metadata v31: đọc bảng literal thiếu byte",
                           where="il2cpp")
        return [struct.unpack_from("<II", raw, i * 8)
                for i in range(header.entry_count)]
    fh.seek(header.table_off)
    raw = fh.read(header.entry_count * 4)
    if len(raw) < header.entry_count * 4:
        raise PB3Error("E_CORRUPT",
                       "metadata v39: đọc bảng literal thiếu byte",
                       where="il2cpp")
    idxs = struct.unpack("<%dI" % header.entry_count, raw)
    out = []
    for i in range(header.entry_count):
        off = idxs[i]
        ln = (idxs[i + 1] - off) if i + 1 < header.entry_count \
            else header.data_size - off
        out.append((ln, off))
    return out


def _effective_patches(before, patches):
    """Gắn lại whitespace 2 đầu của literal gốc vào bản dịch.

    Bảng dịch giữ text ĐÃ STRIP (extract_literals strip để dịch sạch)
    nên bản dịch chỉ thay phần text hiển thị; khi vá, gắn lại whitespace
    gốc của literal đó để độ dài so theo text đã strip (cái user thấy
    trong bảng) và file giữ nguyên layout. Không làm vậy thì literal có
    thừa whitespace 2 đầu sẽ không bao giờ vá được ở v39 (bản dịch
    19 byte != gốc 20 byte — vd 9K LIT_000042), dù bản dịch đúng
    bằng text đã strip. Literal không có whitespace thừa thì đây
    là no-op (prefix/suffix rỗng). Dùng chung cho vá tại chỗ và
    re-layout để 2 đường cho cùng kết quả.
    """
    eff_patches = {}
    for _idx, _new in patches.items():
        if isinstance(_idx, int) and not isinstance(_idx, bool) \
                and _idx in before and isinstance(_new, str):
            _raw = before[_idx]
            _pre = _raw[:len(_raw) - len(_raw.lstrip())]
            _suf = _raw[len(_raw.rstrip()):]
            eff_patches[_idx] = _pre + _new.strip() + _suf
        else:
            eff_patches[_idx] = _new
    return eff_patches


def patch_literals(data, patches, relayout=False):
    """Vá literal IL2CPP trong bytes metadata.

    patches: {literal_index (int): text_mới (str)}. Trả
    (patched_bytes, report) với report = {metadata_version,
    literals_total, requested, patched: [{index, old_len, new_len}],
    skipped: [{index, reason}], verified: True}.

    relayout=False (mặc định): vá TẠI CHỖ như trước — v31 ghi được khi
    bản dịch ngắn hơn hoặc bằng gốc (byte); v39 chỉ khi đúng bằng byte.
    relayout=True: dựng lại toàn bộ blob literal (relayout_literals)
    nên bản dịch DÀI HƠN gốc vẫn ghi được ở cả 2 version; file đổi
    kích thước đúng delta, mọi section sau blob dời nguyên vẹn.

    Quy tắc (vá tại chỗ): v31 ghi được khi bản dịch ngắn hơn
    hoặc bằng gốc (byte); v39 chỉ khi đúng bằng byte. Literal nào không
    vá được thì bỏ qua kèm lý do, KHÔNG raise cả đợt. Verify sau khi vá
    bằng cách đọc lại toàn bộ literal: đã vá đúng text mới, còn lại
    giống hệt gốc, header + kích thước file không đổi (vá tại chỗ) — sai
    thì raise E_VERIFY_FAILED (không trả bytes đã vá).
    """
    if relayout:
        return relayout_literals(data, patches)
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data phải là bytes của global-metadata.dat.")
    data = bytes(data)
    fh = io.BytesIO(data)
    header = read_header(fh)
    before = {}
    for idx, text, _off, _ln in iter_literals(fh, header):
        before[idx] = text
    entries = _read_literal_entries(fh, header)
    patches = _effective_patches(before, patches)
    out = bytearray(data)
    patched = []
    skipped = []
    for idx in sorted(patches, key=lambda x: str(x)):
        new_text = patches[idx]
        if not isinstance(idx, int) or isinstance(idx, bool) \
                or idx < 0 or idx >= header.entry_count:
            skipped.append({"index": idx,
                            "reason": "index ngoài bảng literal"})
            continue
        if not isinstance(new_text, str):
            skipped.append({"index": idx,
                            "reason": "bản dịch không phải chuỗi"})
            continue
        old_len, data_idx = entries[idx]
        old_text = before.get(idx)
        if old_text is None:
            skipped.append({"index": idx,
                            "reason": "literal gốc không đọc được "
                                      "(không phải text sạch)"})
            continue
        new_raw = new_text.encode("utf-8")
        if new_raw == old_text.encode("utf-8"):
            skipped.append({"index": idx,
                            "reason": "unchanged (bản dịch giống gốc)"})
            continue
        if header.layout == "pairs":
            if len(new_raw) > old_len:
                skipped.append({"index": idx,
                                "reason": "too_long: bản dịch %d byte > "
                                          "gốc %d byte (v31 chỉ ghi được "
                                          "khi ngắn hơn hoặc bằng)"
                                          % (len(new_raw), old_len)})
                continue
            pos = header.data_off + data_idx
            out[pos:pos + len(new_raw)] = new_raw
            for p in range(pos + len(new_raw), pos + old_len):
                out[p] = 0
            struct.pack_into("<I", out, header.table_off + idx * 8,
                             len(new_raw))
            patched.append({"index": idx, "old_len": int(old_len),
                            "new_len": len(new_raw)})
        else:  # delta
            if len(new_raw) != old_len:
                skipped.append({"index": idx,
                                "reason": "length_must_match: bản dịch "
                                          "%d byte != gốc %d byte (v39 "
                                          "delta chỉ ghi được khi đúng "
                                          "bằng byte)"
                                          % (len(new_raw), old_len)})
                continue
            pos = header.data_off + data_idx
            out[pos:pos + len(new_raw)] = new_raw
            patched.append({"index": idx, "old_len": int(old_len),
                            "new_len": len(new_raw)})
    # Verify: đọc lại toàn bộ literal từ bytes đã vá.
    patched_bytes = bytes(out)
    if len(patched_bytes) != len(data):
        raise PB3Error("E_VERIFY_FAILED",
                       "kích thước file đổi sau khi vá (%d -> %d) — "
                       "không trả bytes đã vá" % (len(data),
                                                  len(patched_bytes)),
                       where="il2cpp")
    fh2 = io.BytesIO(patched_bytes)
    header2 = read_header(fh2)
    if (header2.version, header2.table_off, header2.table_size,
            header2.data_off, header2.data_size,
            header2.entry_count) != (
            header.version, header.table_off, header.table_size,
            header.data_off, header.data_size, header.entry_count):
        raise PB3Error("E_VERIFY_FAILED",
                       "header metadata đổi sau khi vá — không trả bytes "
                       "đã vá", where="il2cpp")
    after = {}
    for idx, text, _off, _ln in iter_literals(fh2, header2):
        after[idx] = text
    patched_idx = {p["index"] for p in patched}
    for idx, text in before.items():
        want = patches[idx] if idx in patched_idx else text
        if after.get(idx) != want:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "literal %d đọc lại sai sau khi vá (muốn %r, được %r) — "
                "không trả bytes đã vá"
                % (idx, want, after.get(idx)), where="il2cpp")
    if set(after) != set(before):
        raise PB3Error("E_VERIFY_FAILED",
                       "tập literal đọc được đổi sau khi vá — không trả "
                       "bytes đã vá", where="il2cpp")
    return patched_bytes, {
        "mode": "inplace",
        "metadata_version": int(header.version),
        "literals_total": int(header.entry_count),
        "requested": len(patches),
        "patched": patched,
        "skipped": skipped,
        "verified": True,
    }


# ---------------------------------------------------------------------------
# Re-layout blob literal (F1): ghi bản dịch DÀI HƠN gốc
# ---------------------------------------------------------------------------
# Khảo sát header global-metadata.dat trên file thật [ĐÃ ĐO THẬT]:
# - Cả v31 (Tom, 7.147.868 B) lẫn v39 (9K, 20.881.624 B) đều có đúng 31
#   section xếp LIỀN NHAU từ sau header tới cuối file, theo đúng thứ tự
#   (stringLiterals, stringLiteralData, strings, events, properties,
#   methods, parameterDefaultValues, fieldDefaultValues,
#   fieldAndParameterDefaultValueData, fieldMarshaledSizes, parameters,
#   fields, genericParameters, genericParameterConstraints,
#   genericContainers, nestedTypes, interfaces, vtableMethods,
#   interfaceOffsets, typeDefinitions, images, assemblies, fieldRefs,
#   referencedAssemblies, attributesInfo, attributeTypes,
#   attributeTypeRanges, unresolvedVirtualCallParameterTypes,
#   unresolvedVirtualCallParameterRanges, windowsRuntimeTypeNames,
#   exportedTypeDefinitions). Section cuối kết thúc đúng EOF cả 2 file.
# - v31: mỗi section là cặp (offset, size) 8 byte trong header 256 byte;
#   mọi section bắt đầu đúng cuối section trước (mọi cuối đều căn 4).
# - v39: mỗi section là bộ ba (offset, size, count) 12 byte trong header
#   380 byte; section bắt đầu ở vị trí căn 4 tiếp theo (blob literal
#   kết thúc 1.025.867, strings bắt đầu 1.025.868 — 1 byte đệm NUL;
#   strings kết thúc 3.904.551, events bắt đầu 3.904.552 — 1 byte đệm
#   NUL); count của strings = 153.447 đúng số chuỗi không rỗng đếm được
#   trong blob, count của bảng cố định = size / kích thước entry
#   (vd events 10.296 / 429 = 24 byte, typeDefinitions 1.952.420 /
#   23.810 = 82 byte).
# - Blob literal: Tom 298.169/298.172 byte thuộc về literal nào đó (3
#   byte cuối là đệm trong dataSize); 9K 912.343/912.343 byte phủ kín.
#   Literal v31 xếp đơn điệu theo index, không chồng lấn; v39 delta
#   đơn điệu, 2 literal rỗng (đầu + cuối, độ dài 0).
# - dataSize theo version: v31 pad-inclusive (align4 nội dung — Tom
#   298.172 = align4(298.169)); v39 exact (9K 912.343, cuối blob không
#   căn 4). Re-layout GIỮ ĐÚNG quy ước từng version: v31 dataSize mới =
#   align4(nội dung mới); v39 dataSize mới = đúng nội dung mới (vì độ
#   dài literal cuối suy ra từ dataSize — pad-inclusive sẽ biến literal
#   rỗng cuối thành "\x00").
# - Vì sao phải dựng lại TOÀN BỘ blob (không nối thêm từng literal):
#   v39 delta suy độ dài từ index kế tiếp nên 1 literal dời chỗ sẽ phá
#   tính đơn điệu của bảng delta; v31 tuy có offset tường minh nhưng
#   dựng lại toàn bộ cho 1 đường code duy nhất cho cả 2 version, verify
#   giống hệt nhau. Đánh đổi: diff byte lớn hơn vá tại chỗ (mọi literal
#   sau literal đầu tiên bị dài ra đều đổi offset) — verify 5 mục dưới
#   chứng minh mọi byte khác đều đúng chỗ.
# - An toàn của việc dời section: mọi tham chiếu trong các section sau
#   blob đều là INDEX (index vào bảng khác / byte-offset tương đối so
#   với đầu section strings của chính nó), không phải offset tuyệt đối
#   trong file — nên dời NGUYÊN VẸN cả section giữ nguyên mọi tham
#   chiếu; chỉ header (offset tuyệt đối) phải sửa. Đây là kết luận từ
#   bố cục Il2CppDumper/libil2cpp + kiểm chứng byte (section sau blob
#   giữ nguyên từng byte sau khi dời), KHÔNG phải bằng chứng runtime.
# - GIỚI HẠN: re-layout CHỈ xác minh trên v31/v39. v24/v27/v29 tuy đọc
#   được literal (cùng layout pairs) nhưng bố cục section chưa xác minh
#   (v24 có thêm rgctx/metadata-usage theo version phụ) nên TỪ CHỐI rõ
#   ràng thay vì đoán — fail-closed.
# - Game còn chạy được với metadata đã dời section không: [HYPOTHESIS]
#   cho tới khi test trên máy thật. Không có runtime Unity ở đây; bằng
#   chứng mạnh nhất có được là verify 5 mục (đọc lại toàn bộ literal
#   theo byte thô + section sau blob giống hệt từng byte + tiling
#   header/file hợp lệ + header chỉ đổi đúng vùng cho phép).

_SECTION_NAMES = (
    "stringLiterals", "stringLiteralData", "strings", "events",
    "properties", "methods", "parameterDefaultValues",
    "fieldDefaultValues", "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes", "parameters", "fields",
    "genericParameters", "genericParameterConstraints",
    "genericContainers", "nestedTypes", "interfaces",
    "vtableMethods", "interfaceOffsets", "typeDefinitions",
    "images", "assemblies", "fieldRefs", "referencedAssemblies",
    "attributesInfo", "attributeTypes", "attributeTypeRanges",
    "unresolvedVirtualCallParameterTypes",
    "unresolvedVirtualCallParameterRanges",
    "windowsRuntimeTypeNames", "exportedTypeDefinitions",
)
_RELAYOUT_VERSIONS = (31, 39)


def _align4(n):
    """Căn lên bội số 4 (mọi section metadata bắt đầu ở biên 4 byte)."""
    return (n + 3) & ~3


def _read_sections(data, header):
    """Đọc + validate toàn bộ 31 section từ header metadata.

    Trả list dict {name, offset, size, count, off_pos} (off_pos = vị
    trí byte của field offset trong header, để ghi lại khi dời).
    Raise E_UNSUPPORTED_FORMAT khi version chưa xác minh bố cục section
    (chỉ v31/v39), E_CORRUPT khi section không xếp liền nhau / byte đệm
    không phải NUL / section cuối không kết thúc đúng EOF.
    """
    if header.version not in _RELAYOUT_VERSIONS:
        raise PB3Error(
            "E_UNSUPPORTED_FORMAT",
            "re-layout chỉ xác minh trên metadata v31/v39 (file này "
            "v%d: bố cục section chưa xác minh — dùng vá tại chỗ thay "
            "thế)" % header.version, where="il2cpp")
    header_size = 256 if header.layout == "pairs" else 380
    step = 8 if header.layout == "pairs" else 12
    sections = []
    for k, name in enumerate(_SECTION_NAMES):
        base = 8 + step * k
        if header.layout == "pairs":
            off, size = struct.unpack_from("<ii", data, base)
            count = None
        else:
            off, size, count = struct.unpack_from("<iii", data, base)
        sections.append({"name": name, "offset": int(off),
                         "size": int(size), "count": count,
                         "off_pos": base})
    if sections[0]["offset"] != header_size \
            or sections[0]["size"] != header.table_size:
        raise PB3Error("E_CORRUPT",
                       "metadata v%d: bảng literal trong header section "
                       "(%d+%d) không khớp bảng đã đọc (%d+%d)"
                       % (header.version, sections[0]["offset"],
                          sections[0]["size"], header.table_off,
                          header.table_size), where="il2cpp")
    if sections[1]["offset"] != header.data_off \
            or sections[1]["size"] != header.data_size:
        raise PB3Error("E_CORRUPT",
                       "metadata v%d: blob literal trong header section "
                       "(%d+%d) không khớp blob đã đọc (%d+%d)"
                       % (header.version, sections[1]["offset"],
                          sections[1]["size"], header.data_off,
                          header.data_size), where="il2cpp")
    prev_end = None
    for sec in sections:
        off, size = sec["offset"], sec["size"]
        if off < 0 or size < 0:
            raise PB3Error("E_CORRUPT",
                           "metadata v%d: section %s offset/size âm "
                           "(%d+%d)" % (header.version, sec["name"],
                                        off, size), where="il2cpp")
        if prev_end is not None:
            want = _align4(prev_end)
            if off != want:
                raise PB3Error(
                    "E_CORRUPT",
                    "metadata v%d: section %s bắt đầu %d, chờ %d (cuối "
                    "section trước căn 4) — bố cục không khớp mẫu đã "
                    "xác minh, không dời" % (header.version,
                                             sec["name"], off, want),
                    where="il2cpp")
            if any(data[prev_end:off]):
                raise PB3Error(
                    "E_CORRUPT",
                    "metadata v%d: byte đệm trước section %s không phải "
                    "NUL — bố cục không khớp mẫu đã xác minh"
                    % (header.version, sec["name"]), where="il2cpp")
        prev_end = off + size
    if prev_end != len(data):
        raise PB3Error("E_CORRUPT",
                       "metadata v%d: section cuối kết thúc %d != kích "
                       "thước file %d — bố cục không khớp mẫu đã xác "
                       "minh" % (header.version, prev_end, len(data)),
                       where="il2cpp")
    return sections


def relayout_literals(data, patches):
    """Dựng lại blob literal để ghi bản dịch DÀI HƠN gốc (F1).

    patches: {literal_index (int): text_mới (str)}. Trả
    (new_bytes, report) với report = {mode: "relayout",
    metadata_version, literals_total, requested,
    patched: [{index, old_len, new_len, old_offset, new_offset}],
    skipped: [{index, reason}], verified: True,
    old_size, new_size, size_delta, blob_old_size, blob_new_size,
    sections_shifted}.

    Cách làm: blob mới = mọi literal xếp theo đúng thứ tự index
    (literal đã vá mang byte mới, literal chưa vá + literal nhị phân
    giữ nguyên từng byte); bảng literal viết lại toàn bộ (v31:
    length+dataIndex; v39: delta offset tích lũy, giữ đơn điệu);
    dataSize theo quy ước từng version (v31 align4, v39 exact);
    mọi section sau blob dời nguyên vẹn đi delta, sửa offset của
    chúng trong header, đệm NUL tới biên 4 byte. Entry count và count
    của mọi section giữ nguyên (không thêm/bớt literal nào).

    Fail-closed: version khác v31/v39, section không xếp liền nhau,
    byte đệm không phải NUL, literal vượt blob -> raise rõ ràng
    (E_UNSUPPORTED_FORMAT / E_CORRUPT), không trả bytes nửa vời.
    Literal không vá được (index lạ, không phải chuỗi, gốc không đọc
    được, unchanged) thì bỏ qua kèm lý do như vá tại chỗ. Verify 5
    mục sau khi dựng (sai mục nào raise E_VERIFY_FAILED, không trả
    bytes): (1) kích thước file = cũ + delta; (2) header đọc lại hợp
    lệ, dataSize + offset các section sau blob đúng delta, count giữ
    nguyên; (3) mọi section sau blob giống hệt từng byte với gốc
    (chỉ dời chỗ); (4) mọi literal đọc lại theo byte thô đúng byte
    mong muốn (đã vá = mới, còn lại = gốc, kể cả literal nhị phân);
    (5) header chỉ đổi đúng vùng cho phép (bảng literal, field
    dataSize, field offset các section sau blob).
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data phải là bytes của global-metadata.dat.")
    if not isinstance(patches, dict):
        raise ValueError("patches phải là dict {literal_index: text}.")
    data = bytes(data)
    fh = io.BytesIO(data)
    header = read_header(fh)
    sections = _read_sections(data, header)
    before = {}
    for idx, text, _off, _ln in iter_literals(fh, header):
        before[idx] = text
    entries = _read_literal_entries(fh, header)
    blob = data[header.data_off:header.data_off + header.data_size]
    raw = []
    for ln, off in entries:
        if off < 0 or ln < 0 or off + ln > len(blob):
            raise PB3Error("E_CORRUPT",
                           "metadata v%d: literal vượt khỏi blob "
                           "(offset %d + độ dài %d > %d)"
                           % (header.version, off, ln, len(blob)),
                           where="il2cpp")
        raw.append(blob[off:off + ln])
    eff = _effective_patches(before, patches)
    new_parts = list(raw)
    patched = []
    skipped = []
    for idx in sorted(eff, key=lambda x: str(x)):
        new_text = eff[idx]
        if not isinstance(idx, int) or isinstance(idx, bool) \
                or idx < 0 or idx >= header.entry_count:
            skipped.append({"index": idx,
                            "reason": "index ngoài bảng literal"})
            continue
        if not isinstance(new_text, str):
            skipped.append({"index": idx,
                            "reason": "bản dịch không phải chuỗi"})
            continue
        old_len, old_off = entries[idx]
        old_text = before.get(idx)
        if old_text is None:
            skipped.append({"index": idx,
                            "reason": "literal gốc không đọc được "
                                      "(không phải text sạch)"})
            continue
        new_raw = new_text.encode("utf-8")
        if new_raw == old_text.encode("utf-8"):
            skipped.append({"index": idx,
                            "reason": "unchanged (bản dịch giống gốc)"})
            continue
        new_parts[idx] = new_raw
        patched.append({"index": idx, "old_len": int(old_len),
                        "new_len": len(new_raw),
                        "old_offset": int(old_off), "new_offset": None})
    new_offsets = []
    pos = 0
    for part in new_parts:
        new_offsets.append(pos)
        pos += len(part)
    content_size = pos
    if header.layout == "pairs":
        new_data_size = _align4(content_size)
    else:  # delta: độ dài literal cuối suy ra từ dataSize
        new_data_size = content_size
    new_span = _align4(content_size)
    old_span = sections[2]["offset"] - header.data_off
    delta = new_span - old_span
    for p in patched:
        p["new_offset"] = int(new_offsets[p["index"]])
    out = bytearray(data[:header.data_off])
    if header.layout == "pairs":
        for i in range(header.entry_count):
            struct.pack_into("<II", out, header.table_off + i * 8,
                             len(new_parts[i]), new_offsets[i])
    else:
        for i in range(header.entry_count):
            struct.pack_into("<I", out, header.table_off + i * 4,
                             new_offsets[i])
    data_size_pos = 20 if header.layout == "pairs" else 24
    struct.pack_into("<i", out, data_size_pos, new_data_size)
    for sec in sections[2:]:
        struct.pack_into("<i", out, sec["off_pos"],
                         sec["offset"] + delta)
    out += b"".join(new_parts)
    out += b"\x00" * (new_span - content_size)
    out += data[sections[2]["offset"]:]
    new_data = bytes(out)
    # ---- Verify 5 mục (fail-closed, không trả bytes sai) ----
    if len(new_data) != len(data) + delta:
        raise PB3Error("E_VERIFY_FAILED",
                       "re-layout: kích thước file %d != cũ %d + delta "
                       "%d" % (len(new_data), len(data), delta),
                       where="il2cpp")
    fh2 = io.BytesIO(new_data)
    header2 = read_header(fh2)
    if (header2.version, header2.table_off, header2.table_size,
            header2.data_off, header2.entry_count) != (
            header.version, header.table_off, header.table_size,
            header.data_off, header.entry_count):
        raise PB3Error("E_VERIFY_FAILED",
                       "re-layout: header đổi ngoài dự kiến (version / "
                       "table / data offset / entry count)", where="il2cpp")
    if header2.data_size != new_data_size:
        raise PB3Error("E_VERIFY_FAILED",
                       "re-layout: dataSize đọc lại %d != %d"
                       % (header2.data_size, new_data_size),
                       where="il2cpp")
    sections2 = _read_sections(new_data, header2)
    for old, new in zip(sections[2:], sections2[2:]):
        if new["offset"] != old["offset"] + delta \
                or new["size"] != old["size"] \
                or new["count"] != old["count"]:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "re-layout: section %s đổi ngoài dự kiến (offset %d -> "
                "%d, chờ +%d)" % (old["name"], old["offset"],
                                 new["offset"], delta), where="il2cpp")
    if new_data[sections2[2]["offset"]:] \
            != data[sections[2]["offset"]:]:
        raise PB3Error("E_VERIFY_FAILED",
                       "re-layout: section sau blob đổi byte (phải giữ "
                       "nguyên từng byte, chỉ dời chỗ)", where="il2cpp")
    entries2 = _read_literal_entries(fh2, header2)
    blob2 = new_data[header2.data_off:
                     header2.data_off + header2.data_size]
    for i in range(header.entry_count):
        ln, off = entries2[i]
        if off < 0 or ln < 0 or off + ln > len(blob2) \
                or blob2[off:off + ln] != new_parts[i]:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "re-layout: literal %d đọc lại sai sau khi dựng lại "
                "(không trả bytes đã vá)" % i, where="il2cpp")
    allowed = set()
    for p in range(header.table_off,
                   header.table_off + header.table_size, 4):
        allowed.add(p)
    allowed.add(data_size_pos)
    for sec in sections[2:]:
        allowed.add(sec["off_pos"])
    header_size = 256 if header.layout == "pairs" else 380
    for p in range(0, header_size, 4):
        if p in allowed:
            continue
        if data[p:p + 4] != new_data[p:p + 4]:
            raise PB3Error(
                "E_VERIFY_FAILED",
                "re-layout: header đổi ở byte %d ngoài vùng cho phép "
                "(chỉ bảng literal, dataSize, offset section sau blob "
                "được đổi)" % p, where="il2cpp")
    return new_data, {
        "mode": "relayout",
        "metadata_version": int(header.version),
        "literals_total": int(header.entry_count),
        "requested": len(eff),
        "patched": patched,
        "skipped": skipped,
        "verified": True,
        "old_size": len(data),
        "new_size": len(new_data),
        "size_delta": int(delta),
        "blob_old_size": int(header.data_size),
        "blob_new_size": int(new_data_size),
        "sections_shifted": len(sections) - 2,
    }


def apply_literal_patches(data, table, manifest=None, relayout=False):
    """Vá theo BẢNG DỊCH {key: text_mới} (+ manifest tuỳ chọn).

    Key suy ra literal_index từ manifest (ưu tiên — kèm verify text
    gốc) hoặc từ dạng LIT_NNNNNN. Key không suy được index, key trùng
    index, hoặc text gốc khác manifest (sai file metadata) thì bỏ qua
    kèm lý do, không vá. relayout=True: dựng lại blob (xem
    relayout_literals) nên bản dịch dài hơn gốc vẫn ghi được; mặc định
    False giữ vá tại chỗ cũ. Trả (patched_bytes, report) — report có thêm
    "key_results" [{key, index, outcome, reason}] để UI hiện từng key.
    """
    if not isinstance(table, dict):
        raise ValueError("bảng dịch phải là dict {key: text}.")
    manifest = manifest if isinstance(manifest, dict) else {}
    fh = io.BytesIO(bytes(data))
    header = read_header(fh)
    before = {}
    for idx, text, _off, _ln in iter_literals(fh, header):
        before[idx] = text
    patches = {}
    key_of_index = {}
    key_results = []
    for key in sorted(table):
        new_text = table[key]
        m = manifest.get(key) if isinstance(manifest, dict) else None
        idx = None
        if isinstance(m, dict) and "literal_index" in m:
            try:
                idx = int(m["literal_index"])
            except (TypeError, ValueError):
                idx = None
        if idx is None:
            mm = _TABLE_KEY_RE.match(str(key))
            if mm:
                idx = int(mm.group(1))
        if idx is None:
            key_results.append({"key": str(key), "index": None,
                                "outcome": "skipped",
                                "reason": "key không rõ dạng LIT_NNNNNN "
                                          "và không có manifest"})
            continue
        if idx in patches:
            key_results.append({"key": str(key), "index": idx,
                                "outcome": "skipped",
                                "reason": "trùng literal_index với key %s"
                                          % key_of_index[idx]})
            continue
        if idx not in before:
            key_results.append({"key": str(key), "index": idx,
                                "outcome": "skipped",
                                "reason": "literal gốc không đọc được "
                                          "(không phải text sạch)"})
            continue
        if isinstance(m, dict) and "source" in m:
            # Bang dich giu text DA STRIP (extract_literals strip de
            # dich sach), con literal goc trong file co the thua khoang
            # trang/xuong dong o 2 dau — so ca raw lan strip, khong thi
            # literal co thua whitespace se bi loai oan (vd 9K
            # '-\t Event Count: {0}\n' — bang la '...{0}' da strip).
            _raw = before[idx]
            if m["source"] != _raw and m["source"] != _raw.strip():
                key_results.append({"key": str(key), "index": idx,
                                    "outcome": "skipped",
                                    "reason": "source_mismatch: text gốc ở "
                                              "index này khác manifest — sai "
                                              "file metadata, không vá"})
                continue
        patches[idx] = new_text
        key_of_index[idx] = str(key)
    patched_bytes, report = patch_literals(data, patches,
                                           relayout=relayout)
    patched_idx = {p["index"] for p in report["patched"]}
    skipped_by_idx = {s["index"]: s["reason"]
                      for s in report["skipped"]}
    for key in sorted(table):
        idx = None
        m = manifest.get(key) if isinstance(manifest, dict) else None
        if isinstance(m, dict) and "literal_index" in m:
            try:
                idx = int(m["literal_index"])
            except (TypeError, ValueError):
                idx = None
        if idx is None:
            mm = _TABLE_KEY_RE.match(str(key))
            if mm:
                idx = int(mm.group(1))
        if idx is None or key_of_index.get(idx) != str(key):
            continue  # đã ghi key_results ở vòng trên
        if idx in patched_idx:
            key_results.append({"key": str(key), "index": idx,
                                "outcome": "patched", "reason": ""})
        else:
            key_results.append({"key": str(key), "index": idx,
                                "outcome": "skipped",
                                "reason": skipped_by_idx.get(
                                    idx, "không vá được")})
    report["key_results"] = sorted(key_results, key=lambda r: str(r["key"]))
    report["patched_count"] = len(report["patched"])
    report["skipped_count"] = len(report["skipped"])
    return patched_bytes, report

