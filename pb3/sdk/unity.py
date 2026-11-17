"""Backend Unity (Claude 05; đặc tả §6, decisions #66+).

Phạm vi: UnityFS v6/v7/v8/v9 (đọc; v9 CHỈ ĐỌC, không ghi; Zstd chỉ đọc
trên web qua fzstd — xem C5), SerializedFile v15–23 (metadata, object
table, TextAsset, chuỗi ngắn/dài), hybrid backend AUTO / LIBRARY /
NATIVE (UnityPy + LZ4 + Python thuần), planner, patcher
(PATCH_INPLACE / REBUILD_SECTION / REBUILD_ALL), diffverify +
allow-list + backup/versioning/rollback.

Layout đối chiếu UnityPy 1.25.4 (BundleFile.py, SerializedFile.py,
ObjectReader.py, TypeTreeNode.py, CompressionHelper.py):
- UnityFS: big-endian; header = signature, version, unity version,
  unity revision, total size u64, comp/uncomp blocks-info size, flags;
  blocks info = hash 16 byte + số block + (unc, comp, flags u16) +
  số entry + (offset u64, size u64, flags u32, tên).
- SerializedFile: 4 số đầu header LUÔN big-endian; phần sau theo cờ
  endian; object table v15/16 có script_type_index + is_stripped,
  v17+ không; byte_start u64 chỉ từ v22; type tree v23+ có content hash
  16 byte + kích thước blob, blob mở đầu bằng magic "mhtt" + format
  version (size 0 = type đó không có type tree).
- UnityFS v8 cùng layout header với v7 (căn 16 sau header); cờ mã hóa /
  padding chọn theo unityRevision (UnityPy: version_engine), KHÔNG theo
  unityVersion (thường là "5.x.x").
- UnityFS v9 (C5, chỉ đọc): cùng header với v8; khác biệt DUY NHẤT theo
  UnityPy master (helpers/CompressionHelper.py, đối chiếu 2026-11-16):
  mỗi BlockInfo thêm u64 `offset` sau cờ u16, và mỗi block nén được căn
  16 byte trong vùng block data (đọc block bằng offset, không đọc tuần
  tự). Header vẫn căn 16 khi version >= 7; entry vẫn (offset u64, size
  u64, flags u32, tên) — file lớn đã có từ trước nhờ các trường u64.
- Zstd (C5, chỉ đọc trên web): mã nén 5 — mã trống tiếp theo sau
  LZHAM=4. UnityPy master (2026-11-16) và tài liệu Unity (chỉ liệt kê
  LZMA/LZ4/Uncompressed) KHÔNG có cờ Zstd; mã 5 là quy ước mở rộng của
  PB3 để đón bundle dùng Zstd, chưa có bằng chứng bundle Unity thật nào
  dùng mã này — fixture C5 là tổng hợp. Giải nén bằng fzstd (JS thuần,
  vendored ở site/fzstd/) qua cầu nối `js.pb3_zstd_decompress` do
  worker.js công bố (tiền lệ: `js.pb3_google_translate`); ngoài trình
  duyệt thì báo lỗi rõ ràng thay vì crash. Ghi Zstd KHÔNG hỗ trợ:
  dựng lại bundle dùng block không nén (cờ nén 0).
- LZ4 block: token nibble; LZ4HC cùng format block; LZMA Unity: LZMA1
  thô (props 0x5D, dict 0x800000), đầu 5 byte.
- TextAsset = aligned_string(m_Name) + aligned_string(m_Script).

Chỉ tuyên bố hỗ trợ loại/version đã có fixture + test
(handoff/account_05.md, account_05b.md).
Không có câu `import UnityPy`/`import lz4` nào trong file (quy tắc iSH):
chỉ gọi qua importlib.import_module khi thật sự cần, có guard; probe chỉ
dùng find_spec + importlib.metadata (không thực thi module — import thật
UnityPy trên iSH mất ~1 phút và từng treo suite > 149s).
Chạy được trên Python 3.9 (không dùng cú pháp 3.10+, không tomllib).
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import io
import lzma
import os
import re
import struct
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from importlib.util import find_spec
from typing import Dict, List, Optional, Tuple

from core import hashing
from core.errors import PB3Error, error_from_exception
from core.limits import Limits
from sdk import lz4pure
from sdk.manifest import WRITE_MODES

UNITYFS_MAGIC = b"UnityFS"
UNITY_VERSION = "2019.4.40f1"  # mặc định khi dựng bundle mới (giữ nguyên khi rebuild)
UNITY6_PLAYER_VERSION = "5.x.x"     # unityVersion trong header bundle Unity 6
UNITY6_REVISION = "6000.0.23f1"     # unityRevision (engine) của fixture v8/v23
SUPPORTED_BUNDLE_VERSIONS = (6, 7, 8, 9)  # v9: CHỈ ĐỌC (C5), không ghi
# SerializedFile: chỉ 15-23 (object table đã kiểm chứng; <15 quá cũ, >23 chưa có fixture).
SUPPORTED_SF_VERSIONS = (15, 16, 17, 18, 19, 20, 21, 22, 23)

# Mã nén block (bit thấp 6 của flags). 5 = zstd: quy ước mở rộng của PB3
# (xem ghi chú v9/Zstd ở đầu file); UnityPy master không có mã này.
COMPRESSION_NAMES = {0: "none", 1: "lzma", 2: "lz4", 3: "lz4hc", 4: "lzham",
                     5: "zstd"}
COMPRESSION_ZSTD = 5
COMPRESSION_CODES = {v: k for k, v in COMPRESSION_NAMES.items()}
LZ4_CHUNK_SIZE = 0x20000  # 128 KiB (Unity chia block LZ4/LZ4HC theo chunk này)

# Cờ UnityFS (bit thấp 6 = compression; 0x40 combined; 0x80 blocks info ở cuối).
FLAG_COMBINED = 0x40
FLAG_BLOCKS_INFO_AT_END = 0x80
FLAG_OLD_WEB = 0x100
FLAG_PADDING_NEW = 0x200       # cờ mới: block info cần đệm ở đầu
FLAG_ENCRYPTION_OLD = 0x200    # cờ cũ: cùng bit 0x200 là mã hóa
FLAG_ENCRYPTION_NEW = 0x1400

# Directory flag: entry là SerializedFile.
DIR_FLAG_SERIALIZED = 0x04

CLASS_NAMES = {
    1: "GameObject", 2: "Component", 4: "Transform", 20: "Camera",
    21: "Material", 23: "MeshRenderer", 28: "Texture2D", 43: "Mesh",
    48: "Shader", 49: "TextAsset", 114: "MonoBehaviour", 115: "MonoScript",
    142: "AssetBundle", 21: "Material",
}


def class_name(class_id):
    return CLASS_NAMES.get(class_id, "class_%d" % class_id)


def support_matrix():
    """Bảng hỗ trợ Unity (chỉ nâng theo bằng chứng; xem handoff/account_05.md)."""
    return [
        {"format": "UnityFS v6/v7/v8 (bundle)", "read": "có", "write": "có (dựng lại bundle, giữ compression gốc)",
         "backend": "Python thuần + lz4/lzma", "maturity": "M2"},
        {"format": "UnityFS v9 (bundle)", "read": "có (chỉ đọc; BlockInfo có offset u64, block căn 16)",
         "write": "không (v9 chỉ đọc — dựng lại bundle v9 chưa hỗ trợ)",
         "backend": "Python thuần", "maturity": "M2"},
        {"format": "Zstd (block bundle, mã nén 5)", "read": "có — CHỈ trên web BearTea (qua fzstd, cầu nối JS)",
         "write": "không (ghi lại bằng block không nén, cờ 0)",
         "backend": "fzstd 0.1.1 (MIT, vendored site/fzstd/)", "maturity": "M2"},
        {"format": "SerializedFile v15-23 (.assets)", "read": "có (metadata, object table, TextAsset)",
         "write": "có (patch TextAsset: PATCH_INPLACE / REBUILD_SECTION)",
         "backend": "Python thuần", "maturity": "M2"},
        {"format": "LZ4 block / LZ4HC", "read": "có", "write": "có",
         "backend": "binding lz4 (ưu tiên) / Python thuần (literal-only khi ghi)",
         "maturity": "M2"},
        {"format": "LZ4 frame", "read": "có", "write": "chỉ qua binding lz4",
         "backend": "binding lz4.frame (ưu tiên) / Python thuần (chỉ đọc)",
         "maturity": "M2"},
        {"format": "LZMA (Unity raw)", "read": "có", "write": "có",
         "backend": "lzma (thư viện chuẩn)", "maturity": "M2"},
        {"format": "UnityPy (object phức tạp)", "read": "khi cài được",
         "write": "khi cài được", "backend": "UnityPy 1.25.4 (iSH đã chứng minh)",
         "maturity": "M2"},
        {"format": "SerializedFile v<15 / v>23, UnityWeb/UnityRaw, LZHAM, bundle mã hóa",
         "read": "không", "write": "không",
         "backend": "chưa có (E_UNSUPPORTED_FORMAT / E_ENCRYPTED)", "maturity": "M0"},
    ]


# ------------------------------------------------------------------ reader
class _Reader:
    """Đọc nhị phân có kiểm tra giới hạn (không đọc quá buffer)."""

    def __init__(self, data, endian="<", where="unity"):
        self._b = bytes(data)
        self.endian = endian
        self.where = where
        self.pos = 0

    def __len__(self):
        return len(self._b)

    def remaining(self):
        return len(self._b) - self.pos

    def _need(self, n):
        if n < 0 or self.pos + n > len(self._b):
            raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (%d byte tại %d)"
                           % (n, self.pos), where=self.where)

    def read(self, n):
        self._need(n)
        out = self._b[self.pos:self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        self._need(size)
        out = struct.unpack_from(self.endian + fmt, self._b, self.pos)
        self.pos += size
        return out[0] if len(out) == 1 else out

    def u8(self):
        return self.unpack("B")

    def u16(self):
        return self.unpack("H")

    def i16(self):
        return self.unpack("h")

    def i32(self):
        return self.unpack("i")

    def u32(self):
        return self.unpack("I")

    def i64(self):
        return self.unpack("q")

    def u64(self):
        return self.unpack("Q")

    def boolean(self):
        return self.u8() != 0

    def align(self, alignment=4, base=0):
        """Căn vị trí tới bội của alignment (tính từ đầu buffer + base)."""
        target = -((base + self.pos) % alignment) % alignment
        self._need(target)
        self.pos += target

    def string_to_null(self, max_length=32767):
        end = self._b.find(b"\x00", self.pos)
        if end < 0 or end - self.pos > max_length:
            raise PB3Error("E_CORRUPT", "chuỗi không có NUL kết thúc", where=self.where)
        raw = self._b[self.pos:end]
        self.pos = end + 1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise error_from_exception(exc, where=self.where)

    def aligned_string(self):
        """Chuỗi SerializedFile: i32 độ dài + bytes + đệm tới bội 4."""
        size = self.i32()
        if size < 0 or size > self.remaining():
            raise PB3Error("E_CORRUPT", "chuỗi dài %d byte không hợp lệ" % size,
                           where=self.where)
        raw = self.read(size)
        self.align(4)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise error_from_exception(exc, where=self.where)


def _writer(endian="<"):
    return _Writer(endian)


class _Writer:
    def __init__(self, endian="<"):
        self.buf = io.BytesIO()
        self.endian = endian

    def tell(self):
        return self.buf.tell()

    def write(self, data):
        return self.buf.write(data)

    def pack(self, fmt, *values):
        return self.buf.write(struct.pack(self.endian + fmt, *values))

    def pack_be(self, fmt, *values):
        """Ghi big-endian (header SerializedFile luôn big-endian)."""
        return self.buf.write(struct.pack(">" + fmt, *values))

    def align(self, alignment=4, base=0):
        """Đệm NUL để (base + vị trí) chia hết cho alignment."""
        need = (-(base + self.buf.tell())) % alignment
        if need:
            self.buf.write(b"\x00" * need)

    def align16(self):
        self.align(16)

    def string_to_null(self, text):
        self.buf.write(text.encode("utf-8") + b"\x00")

    def aligned_string(self, text):
        raw = text.encode("utf-8")
        self.pack("i", len(raw))
        self.buf.write(raw)
        self.align(4)

    def bytes(self):
        return self.buf.getvalue()


def _align_up(n, alignment):
    return (n + alignment - 1) // alignment * alignment


def parse_unity_version(text):
    """'2019.4.40f1' -> (2019, 4, 40); '5.6.7f1' -> (5, 6, 7); lạ -> ()."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return ()
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _uses_new_flags(engine_version):
    """Cờ mới từ Unity 2020.3.34 / 2021.3.2 / 2022.1.1 (theo UnityPy).

    BẮT BUỘC dùng unityRevision (engine, vd "6000.0.23f1"), KHÔNG dùng
    unityVersion (player, thường là "5.x.x" nên parse ra ()). UnityPy
    (BundleFile.parse_version) cũng lấy version_engine. Dùng nhầm player
    version thì bundle Unity 2019 thật sẽ bị nhận cờ mới: cờ mã hóa 0x200
    bị bỏ qua, cờ padding 0x200 bị hiểu sai (bug tiềm ẩn, decisions #78).
    Không parse được -> True (mặc định cờ mới, đúng với bundle hiện đại)."""
    v = parse_unity_version(engine_version)
    if not v:
        return True
    if v[0] < 2020:
        return False
    if v[0] == 2020 and v < (2020, 3, 34):
        return False
    if v[0] == 2021 and v < (2021, 3, 2):
        return False
    if v[0] == 2022 and v < (2022, 1, 1):
        return False
    return True


# ------------------------------------------------------------------ LZMA Unity
def decompress_lzma_unity(data, where="lzma"):
    """Giải nén LZMA1 thô kiểu Unity (đầu 5 byte: props + dict)."""
    data = bytes(data)
    if len(data) < 5:
        raise PB3Error("E_CORRUPT", "dữ liệu LZMA quá ngắn", where=where)
    props = data[0]
    dict_size = struct.unpack_from("<I", data, 1)[0]
    lc = props % 9
    rem = props // 9
    pb = rem // 5
    lp = rem % 5
    try:
        dec = lzma.LZMADecompressor(
            format=lzma.FORMAT_RAW,
            filters=[{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                      "lc": lc, "lp": lp, "pb": pb}])
        out = dec.decompress(data[5:])
    except (lzma.LZMAError, ValueError) as exc:
        raise PB3Error("E_BAD_ARCHIVE", "LZMA lỗi: %s" % type(exc).__name__, where=where)
    if not dec.eof and dec.unused_data:
        raise PB3Error("E_CORRUPT", "LZMA: dữ liệu thừa sau luồng", where=where)
    return out


def compress_lzma_unity(data):
    """Nén LZMA1 thô kiểu Unity (props 0x5D, dict 0x800000)."""
    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000, "lc": 3, "lp": 0, "pb": 2}]
    comp = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filt)
    return struct.pack("<BI", 0x5D, 0x800000) + comp.compress(bytes(data)) + comp.flush()


# ------------------------------------------------------------- probe backend
def _spec_info(module_name, dist_name):
    """Kiểm tra sự hiện diện bằng find_spec (KHÔNG thực thi module).

    Trả (present, version|None, err|None). find_spec chỉ quét sys.path nên
    nhanh ngay cả với gói lớn như UnityPy: import thật UnityPy trên iSH
    mất ~1 phút và từng treo suite > 149s ở test_auto_init_logs_unitypy_decision
    (handoff/account_05.md §3). Version lấy từ metadata của gói đã cài
    (không thực thi code); thiếu metadata thì version là None.
    """
    try:
        spec = find_spec(module_name)
    except Exception as exc:  # noqa: BLE001 - find_spec lạ (vd parent thiếu)
        return False, None, "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if spec is None:
        return False, None, "không có trên sys.path"
    try:
        version = importlib_metadata.version(dist_name)
    except Exception:  # noqa: BLE001 - không có metadata (vd cài tay)
        version = None
    return True, version, None


def _probe_backends_uncached():
    """Probe thật (không cache): Python thuần + lz4 + UnityPy."""
    out = {"python": {"status": "ok", "detail": "stdlib + parser thuần Python"}}
    present, version, err = _spec_info("lz4.block", "lz4")
    if not present:
        out["lz4"] = {"status": "missing",
                      "detail": "không tìm thấy lz4.block (%s)" % err}
    else:
        frame_present, _, frame_err = _spec_info("lz4.frame", "lz4")
        out["lz4"] = {"status": "ok", "version": version if version is not None else "?",
                      "detail": "lz4.block OK" + ("; lz4.frame OK" if frame_present else
                                                   "; lz4.frame thiếu (%s)" % frame_err)}
    present, version, err = _spec_info("UnityPy", "UnityPy")
    if not present:
        out["unitypy"] = {"status": "missing",
                          "detail": "không tìm thấy UnityPy (%s)" % err}
    else:
        out["unitypy"] = {"status": "ok", "version": version if version is not None else "?",
                          "detail": "UnityPy OK"}
    return out


_PROBE_CACHE = None


def probe_backends():
    """Thăm dò Python thuần + lz4 + UnityPy (không raise; chỉ báo cáo).

    Chỉ kiểm tra sự hiện diện bằng find_spec, KHÔNG import module (import
    UnityPy trên iSH từng treo suite > 149s). Kết quả cache theo tiến trình;
    trả về bản sao để bên gọi không làm bẩn cache.
    """
    global _PROBE_CACHE
    if _PROBE_CACHE is None:
        _PROBE_CACHE = _probe_backends_uncached()
    return copy.deepcopy(_PROBE_CACHE)


def _reset_probe_cache():
    """Xóa cache probe (chỉ dùng trong test để mô phỏng môi trường khác)."""
    global _PROBE_CACHE
    _PROBE_CACHE = None


class UnityBackend:
    """Hybrid backend Unity (đặc tả 6.3): AUTO / LIBRARY / NATIVE.

    - AUTO: UnityPy + lz4 binding khi có; fallback Python thuần có log lý do.
    - LIBRARY: bắt buộc UnityPy + lz4 binding; thiếu -> E_UNSUPPORTED_ON_ISH.
    - NATIVE: chỉ Python thuần, không gọi thư viện ngoài.
    `decisions` ghi lại mọi lựa chọn/fallback (để in ra + ghi report).
    """

    MODES = ("AUTO", "LIBRARY", "NATIVE")
    MATURITY = "M2"  # bằng chứng: fixture sandbox; REBUILD_ALL cần M4 nên bị khóa

    def __init__(self, mode="AUTO", limits=None):
        if mode not in self.MODES:
            raise PB3Error("E_CONFIG_INVALID", "mode backend phải thuộc %s" % "|".join(self.MODES),
                           where="UnityBackend")
        self.mode = mode
        self.limits = limits or Limits()
        self.decisions = []
        probe = probe_backends()
        self.probe = probe
        self.has_lz4_binding = probe["lz4"]["status"] == "ok"
        self.has_unitypy = probe["unitypy"]["status"] == "ok"
        if mode == "LIBRARY":
            missing = [k for k in ("lz4", "unitypy") if probe[k]["status"] != "ok"]
            if missing:
                raise PB3Error(
                    "E_UNSUPPORTED_ON_ISH",
                    "chế độ LIBRARY cần %s nhưng %s"
                    % (" + ".join(missing), "; ".join(probe[k]["detail"] for k in missing)),
                    hint="Dùng AUTO để fallback Python thuần, hoặc cài gói còn thiếu.",
                    where="UnityBackend")
            self.decisions.append("LIBRARY: dùng UnityPy + lz4 binding (đã kiểm tra có mặt).")
        elif mode == "NATIVE":
            self.decisions.append("NATIVE: chỉ dùng Python thuần (không gọi UnityPy/lz4).")
        else:
            if self.has_lz4_binding:
                self.decisions.append("AUTO: LZ4 dùng binding lz4 %s."
                                      % probe["lz4"].get("version", "?"))
            else:
                self.decisions.append("AUTO: lz4 binding thiếu (%s); LZ4 block dùng "
                                      "decoder/encoder thuần Python."
                                      % probe["lz4"]["detail"])
            if self.has_unitypy:
                self.decisions.append("AUTO: UnityPy %s có mặt; dùng cho object phức tạp."
                                      % probe["unitypy"].get("version", "?"))
            else:
                self.decisions.append("AUTO: UnityPy thiếu (%s); chỉ dùng parser thuần "
                                      "cho object đã hiểu (TextAsset); object phức tạp "
                                      "sẽ báo E_UNSUPPORTED_ON_ISH."
                                      % probe["unitypy"]["detail"])

    # ---- nén/giải nén ------------------------------------------------
    def _decompress_zstd(self, data, uncompressed_size, where="unity"):
        """Giải nén 1 block Zstd qua fzstd bên JS (C5, chỉ có trên web).

        Cầu nối theo đúng tiền lệ Google Translate (bridge.py):
        worker.js công bố `globalThis.pb3_zstd_decompress(data, size)`
        (hàm JS đồng bộ, trả Uint8Array); Python gọi qua module `js`.
        Ngoài trình duyệt (CPython/iSH: không có module `js`, hoặc worker
        chưa nạp được fzstd) thì báo E_UNSUPPORTED_FORMAT với lý do rõ
        ràng — không crash khó hiểu, không giả vờ giải nén được.
        Pyodide chuyển bytes Python -> Uint8Array cho JS, và trả
        Uint8Array về dưới dạng JsProxy (có buffer protocol nên
        bytes(...) dùng được; dự phòng to_py())."""
        try:
            import js as _js
            _zstd = getattr(_js, "pb3_zstd_decompress", None)
        except Exception:
            _zstd = None
        if _zstd is None:
            raise PB3Error(
                "E_UNSUPPORTED_FORMAT",
                "block Zstd cần bộ giải nén fzstd — chỉ có trên web BearTea "
                "(mở trang web để đọc bundle này; Python ngoài trình duyệt "
                "không giải nén được Zstd)",
                message_vi="Block Zstd cần bộ giải nén fzstd — chỉ có trên "
                           "web BearTea.",
                where=where)
        try:
            out = _zstd(bytes(data), int(uncompressed_size))
        except PB3Error:
            raise
        except Exception as exc:  # noqa: BLE001 - lỗi fzstd -> E_CORRUPT
            raise PB3Error("E_CORRUPT", "fzstd lỗi: %s" % type(exc).__name__,
                           where=where)
        try:
            raw = bytes(out)
        except Exception:
            to_py = getattr(out, "to_py", None)
            if not callable(to_py):
                raise PB3Error("E_CORRUPT",
                               "fzstd trả về kiểu không đọc được: %s"
                               % type(out).__name__, where=where)
            raw = bytes(to_py())
        if len(raw) != uncompressed_size:
            raise PB3Error("E_CORRUPT", "Zstd giải nén được %d/%d byte"
                           % (len(raw), uncompressed_size), where=where)
        return raw

    def decompress(self, data, uncompressed_size, compression, where="unity"):
        """Giải nén một khối theo mã compression (0 none, 1 lzma, 2 lz4, 3 lz4hc, 5 zstd)."""
        if compression == 0:
            if len(data) != uncompressed_size:
                raise PB3Error("E_CORRUPT", "khối không nén dài %d, khai %d"
                               % (len(data), uncompressed_size), where=where)
            return bytes(data)
        if compression == 1:
            out = decompress_lzma_unity(data, where=where)
            if len(out) != uncompressed_size:
                raise PB3Error("E_CORRUPT", "LZMA giải nén được %d/%d byte"
                               % (len(out), uncompressed_size), where=where)
            return out
        if compression in (2, 3):
            if self.mode != "NATIVE" and self.has_lz4_binding:
                # Không dùng câu `import lz4...` (quy tắc iSH: import nặng
                # không được xuất hiện trong mã, kể cả trong hàm) mà dùng
                # importlib.import_module.
                lz4_block = importlib.import_module("lz4.block")
                try:
                    out = lz4_block.decompress(bytes(data), uncompressed_size)
                except Exception as exc:  # noqa: BLE001 - lỗi binding -> E_CORRUPT
                    raise PB3Error("E_CORRUPT", "lz4.block lỗi: %s" % type(exc).__name__,
                                   where=where)
                return bytes(out)
            return lz4pure.decompress_block(data, uncompressed_size, where=where)
        if compression == COMPRESSION_ZSTD:
            return self._decompress_zstd(data, uncompressed_size, where=where)
        raise PB3Error("E_UNSUPPORTED_FORMAT", "compression %d (%s) chưa hỗ trợ"
                       % (compression, COMPRESSION_NAMES.get(compression, "?")), where=where)

    def compress(self, data, compression, where="unity"):
        """Nén một khối; LZ4/LZ4HC dùng binding khi có, không thì literal-only.

        Zstd KHÔNG ghi được (không có encoder): gọi với compression 5 thì
        báo rõ, bên gọi dựng lại bundle bằng block không nén (cờ 0)."""
        if compression == 0:
            return bytes(data)
        if compression == 1:
            return compress_lzma_unity(data)
        if compression in (2, 3):
            if self.mode != "NATIVE" and self.has_lz4_binding:
                lz4_block = importlib.import_module("lz4.block")
                mode = "high_compression" if compression == 3 else "default"
                return lz4_block.compress(bytes(data), mode=mode, store_size=False)
            self.decisions.append("LZ4 ghi bằng encoder thuần Python (literal-only, "
                                  "không nén) vì binding lz4 không có/không dùng.")
            return lz4pure.compress_block_literals(data)
        if compression == COMPRESSION_ZSTD:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "ghi block Zstd chưa hỗ trợ (không có encoder) — "
                           "dựng lại bundle bằng block không nén "
                           "(compression='none', cờ nén 0)",
                           message_vi="Ghi block Zstd chưa hỗ trợ — dựng lại "
                                      "bundle bằng block không nén.",
                           where=where)
        raise PB3Error("E_UNSUPPORTED_FORMAT", "compression %d chưa hỗ trợ" % compression,
                       where=where)

    def decompress_frame(self, data, where="lz4-frame"):
        """Giải nén LZ4 frame: binding khi có, không thì Python thuần."""
        if self.mode != "NATIVE" and self.has_lz4_binding:
            lz4_frame = importlib.import_module("lz4.frame")
            try:
                return lz4_frame.decompress(bytes(data))
            except Exception as exc:  # noqa: BLE001
                raise PB3Error("E_CORRUPT", "lz4.frame lỗi: %s" % type(exc).__name__,
                               where=where)
        return lz4pure.decompress_frame(data, where=where)

    def unitypy_load(self, data, where="unitypy"):
        """Mở dữ liệu bằng UnityPy (chỉ AUTO/LIBRARY và khi import được)."""
        if self.mode == "NATIVE":
            raise PB3Error("E_UNSUPPORTED_ON_ISH",
                           "chế độ NATIVE không gọi UnityPy", where=where)
        if not self.has_unitypy:
            raise PB3Error("E_UNSUPPORTED_ON_ISH",
                           "UnityPy không tìm thấy (%s); object phức tạp cần UnityPy"
                           % self.probe["unitypy"]["detail"], where=where)
        unitypy = importlib.import_module("UnityPy")
        try:
            return unitypy.load(bytes(data))
        except Exception as exc:  # noqa: BLE001 - lỗi UnityPy -> E_CORRUPT
            raise PB3Error("E_CORRUPT", "UnityPy không đọc được: %s" % type(exc).__name__,
                           where=where)


# ------------------------------------------------------------------ UnityFS
@dataclass
class BundleBlock:
    uncompressed_size: int
    compressed_size: int
    flags: int
    offset: Optional[int] = None  # v9: u64 — vị trí block trong vùng block data (C5)

    @property
    def compression(self):
        return self.flags & 0x3F


@dataclass
class BundleEntry:
    name: str
    offset: int
    size: int
    flags: int

    @property
    def is_serialized(self):
        return bool(self.flags & DIR_FLAG_SERIALIZED)


@dataclass
class BundleFile:
    signature: str
    version: int
    unity_version: str
    unity_revision: str
    total_size: int
    data_flags: int
    blocks: List[BundleBlock] = field(default_factory=list)
    entries: List[BundleEntry] = field(default_factory=list)
    payload: bytes = b""
    source_hash: str = ""
    where: str = field(default="bundle", repr=False, compare=False)

    @property
    def compression(self):
        return self.data_flags & 0x3F

    @property
    def compression_name(self):
        return COMPRESSION_NAMES.get(self.compression, "?")

    @property
    def blocks_info_at_end(self):
        return bool(self.data_flags & FLAG_BLOCKS_INFO_AT_END)

    @property
    def uses_padding(self):
        new_flags = _uses_new_flags(self.unity_revision)
        return bool(self.data_flags & FLAG_PADDING_NEW) if new_flags else False

    def entry_names(self):
        return [e.name for e in self.entries]

    def get_entry(self, name):
        for e in self.entries:
            if e.name == name:
                if e.offset < 0 or e.size < 0 or e.offset + e.size > len(self.payload):
                    raise PB3Error("E_CORRUPT", "entry %r vượt quá payload" % name,
                                   where=self.where + "::" + name)
                return self.payload[e.offset:e.offset + e.size]
        raise PB3Error("E_INPUT_NOT_FOUND", "không có entry %r trong bundle" % name,
                       where=self.where + "::" + name)


def detect_bundle(data):
    """Nhận diện UnityFS: trả dict (version, unity_version) hoặc None."""
    if len(data) < 8 or bytes(data[:7]) != UNITYFS_MAGIC:
        return None
    try:
        r = _Reader(data, ">", where="bundle")
        r.read(8)  # signature + NUL
        version = r.u32()
        player = r.string_to_null()
        engine = r.string_to_null()
    except PB3Error:
        return None
    return {"signature": "UnityFS", "version": version,
            "unity_version": player, "unity_revision": engine}


def parse_bundle(data, backend=None, limits=None, where="bundle"):
    """Đọc UnityFS v6/v7/v8/v9: header, blocks info, giải nén từng block, kiểm size.

    v8 cùng layout với v7 (UnityPy không kiểm version, chỉ căn 16 khi
    version >= 7); cờ mã hóa/padding chọn theo unityRevision (#78).
    v9 (C5, chỉ đọc): mỗi BlockInfo thêm u64 `offset` sau cờ u16; block
    data đọc bằng offset (tính từ đầu vùng block data đã căn 16), vì các
    block nén được căn 16 nên không đọc tuần tự được nữa. Các trường
    total size / entry offset / entry size vốn đã là u64 nên file lớn
    không cần đổi gì thêm."""

    data = bytes(data)
    backend = backend or UnityBackend("AUTO", limits)
    lim = limits or backend.limits
    if len(data) < 8 or data[:7] != UNITYFS_MAGIC:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "không phải UnityFS (magic sai)",
                       where=where)
    r = _Reader(data, ">", where=where)
    r.read(8)
    version = r.u32()
    if version not in SUPPORTED_BUNDLE_VERSIONS:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "UnityFS version %d chưa hỗ trợ (chỉ %s)"
                       % (version, "/".join(map(str, SUPPORTED_BUNDLE_VERSIONS))),
                       where=where)
    unity_version = r.string_to_null()
    unity_revision = r.string_to_null()
    total_size, comp_info_size, uncomp_info_size, data_flags = r.unpack("QIII")
    new_flags = _uses_new_flags(unity_revision)
    encryption_flag = FLAG_ENCRYPTION_NEW if new_flags else FLAG_ENCRYPTION_OLD
    if data_flags & encryption_flag:
        raise PB3Error("E_ENCRYPTED", "bundle mã hóa (flag 0x%x)" % data_flags,
                       where=where)
    compression = data_flags & 0x3F
    if compression == 4 or compression > COMPRESSION_ZSTD:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "compression %d (%s) chưa hỗ trợ"
                       % (compression, COMPRESSION_NAMES.get(compression, "?")),
                       where=where)
    if total_size != len(data):
        # Chỉ cảnh báo: một số file ghi size 0 hoặc lệch; cấu trúc mới quyết định.
        backend.decisions.append("cảnh báo: total size khai %d, file thật %d."
                                 % (total_size, len(data)))
    if version >= 7 or parse_unity_version(unity_revision) >= (2019, 4, 15):
        r.align(16)
    blocks_info_at_end = bool(data_flags & FLAG_BLOCKS_INFO_AT_END)
    if comp_info_size > len(data) or uncomp_info_size > lim.max_entry_bytes:
        raise PB3Error("E_CORRUPT", "kích thước blocks info không hợp lệ", where=where)
    if blocks_info_at_end:
        info_start = len(data) - comp_info_size
        if info_start < r.pos:
            raise PB3Error("E_CORRUPT", "blocks info ở cuối vượt ra ngoài file",
                           where=where)
        info_comp = data[info_start:]
        block_data_start = r.pos
        block_data_end = info_start
    else:
        info_comp = data[r.pos:r.pos + comp_info_size]
        if len(info_comp) != comp_info_size:
            raise PB3Error("E_CORRUPT", "blocks info cắt cụt", where=where)
        block_data_start = r.pos + comp_info_size
        block_data_end = len(data)
    info_raw = backend.decompress(info_comp, uncomp_info_size, compression,
                                  where=where + "::blocks-info")
    ir = _Reader(info_raw, ">", where=where + "::blocks-info")
    ir.read(16)  # hash dữ liệu (không kiểm ở đây)
    block_count = ir.i32()
    if block_count < 0 or block_count > 100000:
        raise PB3Error("E_CORRUPT", "số block %d không hợp lệ" % block_count,
                       where=where)
    blocks = []
    for _ in range(block_count):
        unc, comp_size, flags = ir.unpack("IIH")
        # v9 (C5): mỗi BlockInfo thêm u64 offset sau cờ u16.
        offset = ir.u64() if version >= 9 else None
        blocks.append(BundleBlock(unc, comp_size, flags, offset))
    entry_count = ir.i32()
    if entry_count < 0:
        raise PB3Error("E_CORRUPT", "số entry âm", where=where)
    lim.check_entries(entry_count, where)
    entries = []
    for _ in range(entry_count):
        offset, size, flags = ir.unpack("qqi")
        name = ir.string_to_null()
        entries.append(BundleEntry(name, offset, size, flags))
    # giải nén block data (kiểm kích thước TRƯỚC khi giải nén: tránh
    # zip-bomb — một block khai uncompressed_size khổng lồ không được giải
    # nén trước khi kiểm; tổng cũng bị chặn bởi max_total_uncompressed).
    lim.check_total(sum(b.uncompressed_size for b in blocks), where)
    pos = block_data_start
    if new_flags and (data_flags & FLAG_PADDING_NEW):
        pos = _align_up(pos, 16)
    block_base = pos  # đầu vùng block data (đã căn 16 nếu có cờ padding)
    payload = bytearray()
    end = pos
    for i, block in enumerate(blocks):
        lim.check_entry_size(block.uncompressed_size, "%s::block%d" % (where, i))
        if version >= 9:
            # v9 (C5): block nằm ở block_base + offset (các block được
            # căn 16 nên không đọc tuần tự được); offset thiếu thì lỗi rõ.
            if block.offset is None or block.offset < 0:
                raise PB3Error("E_CORRUPT", "block %d (v9) thiếu offset" % i,
                               where=where)
            start = block_base + block.offset
        else:
            start = pos
        chunk = data[start:start + block.compressed_size]
        if len(chunk) != block.compressed_size:
            raise PB3Error("E_CORRUPT", "block %d cắt cụt" % i, where=where)
        pos = start + block.compressed_size
        end = max(end, pos)
        payload += backend.decompress(chunk, block.uncompressed_size,
                                      block.compression, where="%s::block%d" % (where, i))
    if end > block_data_end:
        raise PB3Error("E_CORRUPT", "block data vượt quá vùng cho phép", where=where)
    payload = bytes(payload)
    for e in entries:
        if e.offset < 0 or e.size < 0 or e.offset + e.size > len(payload):
            raise PB3Error("E_CORRUPT", "entry %r vượt quá payload" % e.name,
                           where=where)
    return BundleFile(
        signature="UnityFS", version=version, unity_version=unity_version,
        unity_revision=unity_revision, total_size=total_size,
        data_flags=data_flags, blocks=blocks, entries=entries,
        payload=payload, source_hash=hashing.sha256_bytes(data), where=where)


@dataclass
class _BundleLayout:
    """Layout UnityFS doc tu header + blocks-info (khong payload).

    Dung chung cho duong doc streaming (stream_bundle_entries) va duong
    ghi streaming (rebuild_bundle_streaming): 1 logic duy nhat cho
    header/cac co ma hoa/blocks-info, tranh lech nhau giua doc va ghi.
    """
    version: int
    unity_version: str
    unity_revision: str
    total_size: int
    comp_info_size: int
    uncomp_info_size: int
    data_flags: int
    new_flags: bool
    compression: int
    blocks_info_at_end: bool
    header_end: int
    block_data_start: int
    block_data_end: Optional[int]
    file_size: int
    blocks: List[BundleBlock]
    entries: List[BundleEntry]


def _read_bundle_layout(fh, backend, lim, where="bundle"):
    """Doc header + blocks-info cua UnityFS tu file-like seekable `fh`.

    fh duoc seek ve 0 truoc khi doc; khi tra ve, vi tri con tro khong
    dam bao (ben goi tu seek theo block_data_start/offset). Loi giong
    het parse_bundle/stream_bundle_entries cu (E_UNSUPPORTED_FORMAT /
    E_ENCRYPTED / E_CORRUPT). Khong giu payload trong RAM.
    """
    fh.seek(0)
    head = fh.read(4096)
    if len(head) < 8 or head[:7] != UNITYFS_MAGIC:
        raise PB3Error("E_UNSUPPORTED_FORMAT", "không phải UnityFS (magic sai)",
                       where=where)
    r = _Reader(head, ">", where=where)
    r.read(8)
    version = r.u32()
    if version not in SUPPORTED_BUNDLE_VERSIONS:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "UnityFS version %d chưa hỗ trợ (chỉ %s)"
                       % (version, "/".join(map(str, SUPPORTED_BUNDLE_VERSIONS))),
                       where=where)
    unity_version = r.string_to_null()
    unity_revision = r.string_to_null()
    total_size, comp_info_size, uncomp_info_size, data_flags = r.unpack("QIII")
    new_flags = _uses_new_flags(unity_revision)
    encryption_flag = FLAG_ENCRYPTION_NEW if new_flags else FLAG_ENCRYPTION_OLD
    if data_flags & encryption_flag:
        raise PB3Error("E_ENCRYPTED", "bundle mã hóa (flag 0x%x)" % data_flags,
                       where=where)
    compression = data_flags & 0x3F
    if compression == 4 or compression > COMPRESSION_ZSTD:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "compression %d (%s) chưa hỗ trợ"
                       % (compression, COMPRESSION_NAMES.get(compression, "?")),
                       where=where)
    if version >= 7 or parse_unity_version(unity_revision) >= (2019, 4, 15):
        r.align(16)
    header_end = r.pos
    blocks_info_at_end = bool(data_flags & FLAG_BLOCKS_INFO_AT_END)
    fh.seek(0, 2)
    file_size = fh.tell()
    if comp_info_size > file_size or uncomp_info_size > lim.max_entry_bytes:
        raise PB3Error("E_CORRUPT", "kích thước blocks info không hợp lệ", where=where)
    if blocks_info_at_end:
        info_start = file_size - comp_info_size
        if info_start < header_end:
            raise PB3Error("E_CORRUPT", "blocks info ở cuối vượt ra ngoài file",
                           where=where)
        fh.seek(info_start)
        info_comp = fh.read(comp_info_size)
        block_data_start = header_end
        block_data_end = info_start
    else:
        if header_end + comp_info_size > len(head):
            fh.seek(header_end)
            info_comp = fh.read(comp_info_size)
        else:
            info_comp = head[header_end:header_end + comp_info_size]
        block_data_start = header_end + comp_info_size
        block_data_end = None  # không cần (đọc tuần tự tới hết block)
    info_raw = backend.decompress(info_comp, uncomp_info_size, compression,
                                  where=where + "::blocks-info")
    ir = _Reader(info_raw, ">", where=where + "::blocks-info")
    ir.read(16)
    block_count = ir.i32()
    if block_count < 0 or block_count > 100000:
        raise PB3Error("E_CORRUPT", "số block %d không hợp lệ" % block_count,
                       where=where)
    blocks = []
    for _ in range(block_count):
        unc, comp_size, flags = ir.unpack("IIH")
        # v9 (C5): mỗi BlockInfo thêm u64 offset sau cờ u16.
        offset = ir.u64() if version >= 9 else None
        blocks.append(BundleBlock(unc, comp_size, flags, offset))
    entry_count = ir.i32()
    if entry_count < 0:
        raise PB3Error("E_CORRUPT", "số entry âm", where=where)
    lim.check_entries(entry_count, where)
    entries = []
    for _ in range(entry_count):
        offset, size, flags = ir.unpack("qqi")
        name = ir.string_to_null()
        entries.append(BundleEntry(name, offset, size, flags))
    lim.check_total(sum(b.uncompressed_size for b in blocks), where)
    total_payload = sum(b.uncompressed_size for b in blocks)
    for e in entries:
        if e.offset < 0 or e.size < 0 or e.offset + e.size > total_payload:
            raise PB3Error("E_CORRUPT", "entry %r vượt quá payload" % e.name,
                           where=where)
    return _BundleLayout(
        version=version, unity_version=unity_version,
        unity_revision=unity_revision, total_size=total_size,
        comp_info_size=comp_info_size, uncomp_info_size=uncomp_info_size,
        data_flags=data_flags, new_flags=new_flags, compression=compression,
        blocks_info_at_end=blocks_info_at_end, header_end=header_end,
        block_data_start=block_data_start, block_data_end=block_data_end,
        file_size=file_size, blocks=blocks, entries=entries)


def stream_bundle_entries(stream_or_path, backend=None, limits=None, where="bundle"):
    """Yield (tên_entry, bytes_entry) từng entry của UnityFS, không giữ cả payload.

    Đọc header + blocks-info bằng lát cắt nhỏ, rồi giải nén từng block theo
    thứ tự, giữ cửa sổ trượt chỉ đủ chứa entry đang đọc dở (entry đã yield
    xong được bỏ khỏi cửa sổ ngay). File 590MB/236 entry (payload giải nén
    ~996MB) xử lý được với RAM đỉnh ~2× entry lớn nhất, thay vì ôm cả
    payload + nhiều bản copy như parse_bundle (đủ làm chết tab trình duyệt).
    stream_or_path: đường dẫn file hoặc đối tượng nhị phân đã mở (seekable;
    hàm tự mở/đóng khi nhận đường dẫn, không đóng stream của người gọi).
    Entry yield theo thứ tự offset trong payload (không phải thứ tự khai
    trong blocks-info). Bundle mã hóa / version không hỗ trợ / entry vượt
    payload -> raise PB3Error như parse_bundle (bên gọi ghi skipped)."""
    backend = backend or UnityBackend("AUTO", limits)
    lim = limits or backend.limits
    own = isinstance(stream_or_path, (str, bytes, os.PathLike))
    fh = open(stream_or_path, "rb") if own else stream_or_path
    try:
        layout = _read_bundle_layout(fh, backend, lim, where=where)
        version = layout.version
        data_flags = layout.data_flags
        new_flags = layout.new_flags
        blocks_info_at_end = layout.blocks_info_at_end
        blocks = layout.blocks
        entries = layout.entries
        block_data_start = layout.block_data_start
        block_data_end = layout.block_data_end
        # Giải nén từng block, giữ cửa sổ trượt; yield entry theo thứ tự offset.
        ordered = sorted(entries, key=lambda e: (e.offset, e.size))
        pos = block_data_start
        if new_flags and (data_flags & FLAG_PADDING_NEW):
            pos = _align_up(pos, 16)
        block_base = pos  # đầu vùng block data (đã căn 16 nếu có cờ padding)
        window = bytearray()
        window_start = 0
        next_i = 0
        for i, block in enumerate(blocks):
            lim.check_entry_size(block.uncompressed_size, "%s::block%d" % (where, i))
            if version >= 9:
                # v9 (C5): đọc block bằng offset, không đọc tuần tự.
                if block.offset is None or block.offset < 0:
                    raise PB3Error("E_CORRUPT", "block %d (v9) thiếu offset" % i,
                                   where=where)
                start = block_base + block.offset
            else:
                start = pos
            fh.seek(start)
            chunk = fh.read(block.compressed_size)
            if len(chunk) != block.compressed_size:
                raise PB3Error("E_CORRUPT", "block %d cắt cụt" % i, where=where)
            pos = start + block.compressed_size
            if block_data_end is not None and pos > block_data_end:
                raise PB3Error("E_CORRUPT", "block data vượt quá vùng cho phép",
                               where=where)
            window += backend.decompress(chunk, block.uncompressed_size,
                                         block.compression,
                                         where="%s::block%d" % (where, i))
            window_end = window_start + len(window)
            while next_i < len(ordered):
                e = ordered[next_i]
                if e.offset + e.size > window_end:
                    break
                start = e.offset - window_start
                entry_bytes = bytes(window[start:start + e.size])
                next_i += 1
                # Bỏ phần cửa sổ trước entry kế tiếp NGAY (giữ lại byte
                # chung nếu 2 entry chồng nhau cùng offset) — bản copy
                # entry_bytes đã độc lập nên yield sau khi bỏ vẫn an toàn,
                # và RAM đỉnh trong lúc caller xử lý entry chỉ còn ~1×
                # entry thay vì 2× (cửa sổ + copy).
                if next_i < len(ordered):
                    drop_to = ordered[next_i].offset
                else:
                    drop_to = window_end
                if drop_to > window_start:
                    del window[:drop_to - window_start]
                    window_start = drop_to
                yield e.name, entry_bytes
        if next_i < len(ordered):
            raise PB3Error("E_CORRUPT", "còn %d entry chưa đọc đủ dữ liệu"
                           % (len(ordered) - next_i), where=where)
    finally:
        if own:
            fh.close()


def build_bundle(entries, *, version=7, unity_version=UNITY_VERSION,
                 unity_revision=None, compression="none", blocks_info_at_end=False,
                 extra_data_flags=0, pad_block_data=False, backend=None,
                 limits=None):
    """Dựng UnityFS từ entries [(name, flags, data)].

    Giữ đúng layout UnityPy ghi: header big-endian, căn 16 (v7+),
    blocks info (hash 16 byte 0 + block + entry), block LZ4 chia 128 KiB,
    LZMA/none một block. Dùng cho patcher (dựng lại bundle) và test.
    `unity_revision` mặc định = `unity_version` (fixture cũ ghi cùng một
    chuỗi cho cả hai); bundle Unity 6 thật ghi player "5.x.x" + revision
    "6000.0.23f1". `extra_data_flags` OR thêm vào data_flag (để test cờ
    mã hóa/padding); `pad_block_data` đặt cờ 0x200 và căn đầu vùng block
    data tới 16 (chỉ có ý nghĩa với cờ mới).
    RANH GIỚI GHI (C5): version 9 CHỈ ĐỌC — dựng bundle v9 thì từ chối
    với lý do rõ ràng; compression "zstd" cũng từ chối (không có
    encoder) — bên gọi (apply_patch) dựng lại bằng compression "none"
    (block không nén, cờ nén 0)."""
    backend = backend or UnityBackend("AUTO", limits)
    if version == 9:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "UnityFS v9 chỉ đọc — dựng lại bundle v9 chưa hỗ trợ "
                       "(không ghi v9)",
                       message_vi="UnityFS v9 chỉ đọc — không ghi được "
                                  "bundle v9.",
                       where="bundle")
    if compression == "zstd":
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "ghi block Zstd chưa hỗ trợ (không có encoder) — "
                       "dựng lại bundle bằng block không nén "
                       "(compression='none', cờ nén 0)",
                       message_vi="Ghi block Zstd chưa hỗ trợ — dựng lại "
                                  "bundle bằng block không nén.",
                       where="bundle")
    if unity_revision is None:
        unity_revision = unity_version
    comp_code = COMPRESSION_CODES[compression]
    data_flag = FLAG_COMBINED | comp_code | extra_data_flags
    if blocks_info_at_end:
        data_flag |= FLAG_BLOCKS_INFO_AT_END
    if pad_block_data:
        data_flag |= FLAG_PADDING_NEW
    payload = b"".join(d for _, _, d in entries)
    if compression == "lz4":
        chunks = [payload[i:i + LZ4_CHUNK_SIZE]
                  for i in range(0, len(payload), LZ4_CHUNK_SIZE)] or [b""]
    else:
        chunks = [payload]
    blocks = []
    block_data = io.BytesIO()
    for chunk in chunks:
        comp = backend.compress(chunk, comp_code)
        if compression != "none" and len(comp) >= len(chunk):
            block_data.write(chunk)
            blocks.append(BundleBlock(len(chunk), len(chunk), 0))
        else:
            block_data.write(comp)
            flag = 64 if compression == "none" else comp_code
            blocks.append(BundleBlock(len(chunk), len(comp), flag))
    block_bytes = block_data.getvalue()
    info = _Writer(">")
    info.write(b"\x00" * 16)
    info.pack("i", len(blocks))
    for b in blocks:
        info.pack("IIH", b.uncompressed_size, b.compressed_size, b.flags)
    info.pack("i", len(entries))
    offset = 0
    for name, flags, data in entries:
        info.pack("qqi", offset, len(data), flags)
        info.string_to_null(name)
        offset += len(data)
    info_raw = info.bytes()
    info_comp = backend.compress(info_raw, comp_code)
    out = _Writer(">")
    out.write(b"UnityFS\x00")
    out.pack("I", version)
    out.string_to_null(unity_version)
    out.string_to_null(unity_revision)
    header_pos = out.tell()
    out.pack("QIII", 0, len(info_comp), len(info_raw), data_flag)
    if version >= 7 or parse_unity_version(unity_revision) >= (2019, 4, 15):
        out.align16()
    if blocks_info_at_end:
        if pad_block_data:
            out.align16()
        out.write(block_bytes)
        out.write(info_comp)
    else:
        out.write(info_comp)
        if pad_block_data:
            out.align16()
        out.write(block_bytes)
    total = out.tell()
    raw = bytearray(out.bytes())
    struct.pack_into(">Q", raw, header_pos, total)
    return bytes(raw)


def rebuild_bundle_streaming(src_path, dst_path, replacements, backend=None,
                             limits=None, progress_fn=None, on_entry=None):
    """Dựng lại bundle UnityFS bằng streaming, không ôm cả payload trong RAM.

    Pass 1 quét + định cỡ (hash sha256 từng entry khi stream qua, entry
    thay thế lấy bytes đã patch sẵn trong `replacements`); pass 2 ghi
    1 lần (block data ra file tạm rồi ráp file đích, seek-patch
    blocks-info + total_size cuối); pass 3 đọc lại output bằng
    stream_bundle_entries và kiểm tập entry + thứ tự + flags + hash
    entry không đổi + hash entry đã patch. Sai -> E_VERIFY_FAILED và
    xóa file đích dở.

    `replacements`: {tên_entry: bytes_mới}. Entry không có trong
    replacements được copy nguyên vẹn (giải nén rồi nén lại theo đúng
    luật của build_bundle: LZ4/LZ4HC từng chunk 128KiB + fallback ghi
    thô khi nén dài hơn; LZMA = MỘT LZMACompressor FORMAT_RAW/FILTER_LZMA1
    duy nhất suốt payload, 1 block [HYPOTHESIS cho tới khi test
    round-trip qua — payload LZMA > max_entry_bytes thì từ chối
    E_TOO_LARGE vì block đơn đó không đọc lại được với giới hạn mặc
    định]; Zstd -> ghi block không nén + warning, như apply_patch).
    Chỉ bundle ĐỘC LẬP (đường dẫn file) mới dùng đường này; bundle
    trong ZIP/IPA (bytes đã trong RAM) giữ đường RAM.
    Giới hạn: entry đơn > max_memory_read_bytes (128MB) -> E_TOO_LARGE
    (hướng iSH); bundle v9 / mã hóa / mã nén 4,>5 -> từ chối sớm ở
    pass 1, không ghi gì. Verify ở đây YẾU HƠN diffverify toàn phần
    ở 1 điểm: không kiểm byte-layout ngoài entry bằng snapshot (bù
    bằng test parse_bundle trên fixture nhỏ + round-trip ở tầng plan).
    `progress_fn(done, total)` đơn điệu theo byte payload
    (total = payload cũ + payload mới); `on_entry(name, sha256, size,
    replaced)` gọi mỗi entry ở pass 1. Tuyệt đối không ghi đè file
    gốc: dst_path trùng src_path (theo realpath) thì từ chối trước
    khi đọc/ghi gì.
    Trả dict {size_before, size_after, warnings, entries, compression,
    blocks}. Stdlib-only, Python 3.9.
    """
    import tempfile

    src_real = os.path.realpath(str(src_path))
    dst_real = os.path.realpath(str(dst_path))
    if src_real == dst_real:
        raise PB3Error("E_CONFIG_INVALID",
                       "không ghi đè file gốc: đích trùng nguồn (%s)"
                       % src_path, where=str(dst_path))
    if os.path.exists(dst_path):
        try:
            if os.path.samefile(src_path, dst_path):
                raise PB3Error("E_CONFIG_INVALID",
                               "không ghi đè file gốc: đích trùng nguồn (%s)"
                               % src_path, where=str(dst_path))
        except OSError:
            pass
    backend = backend or UnityBackend("AUTO", limits)
    lim = limits or backend.limits
    replacements = dict(replacements or {})
    warnings: List[str] = []
    # ---- Pass 1: layout + quét entry (không giữ payload) ----
    with open(src_path, "rb") as fh:
        layout = _read_bundle_layout(fh, backend, lim, where="bundle")
    if layout.version >= 9:
        raise PB3Error(
            "E_UNSUPPORTED_FORMAT",
            "bundle UnityFS v9 chỉ đọc — chưa hỗ trợ ghi streaming "
            "(ghi lại bundle v9 chưa được hỗ trợ)",
            message_vi="Bundle UnityFS v9 chỉ đọc — không ghi được.",
            where="bundle")
    src_compression = COMPRESSION_NAMES.get(layout.compression, "?")
    if layout.compression == COMPRESSION_ZSTD:
        write_compression = "none"
        warnings.append("bundle gốc nén Zstd: ghi lại bằng block không nén "
                        "(không có encoder Zstd) nên file có thể to hơn.")
    else:
        write_compression = src_compression
    comp_code = COMPRESSION_CODES[write_compression]
    names = [e.name for e in layout.entries]
    if len(set(names)) != len(names):
        raise PB3Error("E_CORRUPT", "bundle có entry trùng tên — không ghi "
                       "streaming được", where="bundle")
    unknown = sorted(set(replacements) - set(names))
    if unknown:
        raise PB3Error("E_CONFIG_INVALID",
                       "replacements có entry không có trong bundle: %s"
                       % ", ".join(unknown), where="bundle")
    for e in layout.entries:
        lim.check_memory_read(e.size, "bundle::%s" % e.name)
    orig_info = {}  # name -> (flags, size, sha256)
    ordered = sorted(layout.entries, key=lambda e: (e.offset, e.size))
    old_payload = sum(e.size for e in layout.entries)
    new_sizes = {e.name: (len(replacements[e.name]) if e.name in replacements
                          else e.size) for e in layout.entries}
    new_payload = sum(new_sizes.values())
    if write_compression == "lzma" and new_payload > lim.max_entry_bytes:
        raise PB3Error(
            "E_TOO_LARGE",
            "payload LZMA %d byte > giới hạn block %d byte (LZMA ghi 1 block "
            "duy nhất nên không đọc lại được với giới hạn mặc định); "
            "dùng bản iSH với giới hạn cao hơn cho bundle này."
            % (new_payload, lim.max_entry_bytes), where="bundle")
    total_prog = old_payload + new_payload
    done_prog = 0

    def _prog(done):
        if progress_fn is not None:
            try:
                progress_fn(int(done), int(total_prog))
            except Exception:
                pass

    for name, data in stream_bundle_entries(src_path, backend, lim,
                                            where="bundle"):
        sha = hashlib.sha256(data).hexdigest()
        ent = next(e for e in layout.entries if e.name == name)
        orig_info[name] = (ent.flags, len(data), sha)
        done_prog += len(data)
        _prog(done_prog)
        if on_entry is not None:
            try:
                on_entry(name, sha, len(data), name in replacements)
            except Exception:
                pass
    if len(orig_info) != len(layout.entries):
        raise PB3Error("E_CORRUPT", "quét pass 1 thiếu entry", where="bundle")
    # Offset mới theo thứ tự payload; bảng entry giữ thứ tự khai gốc.
    new_offsets = {}
    pos = 0
    for e in ordered:
        new_offsets[e.name] = pos
        pos += new_sizes[e.name]
    size_before = os.path.getsize(src_path)
    dst_dir = os.path.dirname(os.path.abspath(str(dst_path))) or "."
    os.makedirs(dst_dir, exist_ok=True)
    blocks_tmp = None
    staging_tmp = None
    try:
        # ---- Pass 2a: nén block data ra file tạm ----
        fd, blocks_path = tempfile.mkstemp(prefix=".rebuild-blocks-",
                                           dir=dst_dir)
        blocks_tmp = blocks_path
        blocks: List[BundleBlock] = []
        with os.fdopen(fd, "wb") as bf:
            if write_compression == "lzma":
                comp = lzma.LZMACompressor(
                    format=lzma.FORMAT_RAW,
                    filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000,
                              "lc": 3, "lp": 0, "pb": 2}])
                bf.write(struct.pack("<BI", 0x5D, 0x800000))
                written = 5
                consumed = 0
                for name, data in _stream_payload_entries(
                        src_path, replacements, backend, lim, ordered):
                    buf = bytes(data)
                    consumed += len(buf)
                    for i in range(0, len(buf), 1 << 20):
                        out = comp.compress(buf[i:i + (1 << 20)])
                        bf.write(out)
                        written += len(out)
                    _prog(old_payload + consumed)
                tail = comp.flush()
                bf.write(tail)
                written += len(tail)
                blocks.append(BundleBlock(new_payload, written, comp_code))
                _prog(old_payload + new_payload)
            elif write_compression in ("lz4", "lz4hc"):
                buf = bytearray()

                def _flush_chunk(chunk):
                    comp = backend.compress(bytes(chunk), comp_code)
                    if len(comp) >= len(chunk):
                        bf.write(bytes(chunk))
                        blocks.append(BundleBlock(len(chunk), len(chunk), 0))
                    else:
                        bf.write(comp)
                        blocks.append(BundleBlock(len(chunk), len(comp),
                                                  comp_code))

                consumed = 0
                for name, data in _stream_payload_entries(
                        src_path, replacements, backend, lim, ordered):
                    buf += data
                    consumed += len(data)
                    while len(buf) >= LZ4_CHUNK_SIZE:
                        _flush_chunk(buf[:LZ4_CHUNK_SIZE])
                        del buf[:LZ4_CHUNK_SIZE]
                    _prog(old_payload + consumed)
                if buf or not blocks:
                    _flush_chunk(bytes(buf))
                _prog(old_payload + new_payload)
            else:  # none: block 32MiB (duoi gioi han entry 64MB khi doc lai)
                step = 32 * 1024 * 1024
                buf = bytearray()
                consumed = 0
                for name, data in _stream_payload_entries(
                        src_path, replacements, backend, lim, ordered):
                    buf += data
                    consumed += len(data)
                    while len(buf) >= step:
                        bf.write(bytes(buf[:step]))
                        blocks.append(BundleBlock(step, step, 64))
                        del buf[:step]
                    _prog(old_payload + consumed)
                if buf or not blocks:
                    bf.write(bytes(buf))
                    blocks.append(BundleBlock(len(buf), len(buf), 64))
                _prog(old_payload + new_payload)
        # ---- Pass 2b: blocks-info + ráp file đích ----
        info = _Writer(">")
        info.write(b"\x00" * 16)
        info.pack("i", len(blocks))
        for b in blocks:
            info.pack("IIH", b.uncompressed_size, b.compressed_size, b.flags)
        info.pack("i", len(layout.entries))
        for e in layout.entries:
            info.pack("qqi", new_offsets[e.name], new_sizes[e.name], e.flags)
            info.string_to_null(e.name)
        info_raw = info.bytes()
        info_comp = backend.compress(info_raw, comp_code)
        new_flags = (layout.data_flags & ~0x3F) | comp_code
        fd2, staging_path = tempfile.mkstemp(prefix=".rebuild-staging-",
                                             dir=dst_dir)
        staging_tmp = staging_path
        with os.fdopen(fd2, "wb") as out:
            head = _Writer(">")
            head.write(b"UnityFS\x00")
            head.pack("I", layout.version)
            head.string_to_null(layout.unity_version)
            head.string_to_null(layout.unity_revision)
            header_pos = head.tell()
            head.pack("QIII", 0, len(info_comp), len(info_raw), new_flags)
            if layout.version >= 7 or parse_unity_version(
                    layout.unity_revision) >= (2019, 4, 15):
                head.align16()
            out.write(head.bytes())
            pad_block_data = bool(layout.new_flags
                                  and (layout.data_flags & FLAG_PADDING_NEW))

            def _pad16():
                cur = out.tell()
                pad = (-cur) % 16
                if pad:
                    out.write(b"\x00" * pad)

            if layout.blocks_info_at_end:
                if pad_block_data:
                    _pad16()
                _copy_file(blocks_path, out)
                out.write(info_comp)
            else:
                out.write(info_comp)
                if pad_block_data:
                    _pad16()
                _copy_file(blocks_path, out)
            total = out.tell()
            out.seek(header_pos)
            out.write(struct.pack(">Q", total))
            out.flush()
        os.replace(staging_path, dst_path)
        staging_tmp = None
    finally:
        for tmp in (blocks_tmp, staging_tmp):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    # ---- Pass 3: verify streaming ----
    expected = []
    for e in ordered:
        if e.name in replacements:
            sha = hashlib.sha256(replacements[e.name]).hexdigest()
        else:
            sha = orig_info[e.name][2]
        expected.append((e.name, e.flags, new_sizes[e.name], sha))
    got = []
    for name, data in stream_bundle_entries(dst_path, backend, lim,
                                            where="bundle::verify"):
        got.append((name, None, len(data), hashlib.sha256(data).hexdigest()))
    # Flags lay tu layout cua file dich (stream khong tra flags).
    with open(dst_path, "rb") as fh:
        out_layout = _read_bundle_layout(fh, backend, lim, where="bundle::verify")
    out_flags = {e.name: e.flags for e in out_layout.entries}
    got = [(n, out_flags.get(n), s, h) for (n, _f, s, h) in got]
    if got != expected or out_layout.version != layout.version:
        try:
            os.unlink(dst_path)
        except OSError:
            pass
        raise PB3Error(
            "E_VERIFY_FAILED",
            "xác minh sau khi ghi streaming không đạt: entry đọc lại khác "
            "dự kiến (tập/thứ tự/flags/hash) — đã xóa file đích dở.",
            where="bundle")
    return {"size_before": int(size_before),
            "size_after": int(os.path.getsize(dst_path)),
            "warnings": warnings, "entries": names,
            "compression": write_compression, "blocks": len(blocks)}


def _stream_payload_entries(src_path, replacements, backend, lim, ordered):
    """Yield (name, bytes) từng entry theo thứ tự payload, thay thế entry
    có trong `replacements` bằng bytes đã patch sẵn. Entry gốc vẫn được
    đọc qua stream (giới hạn 1 entry trong RAM tại 1 thời điểm)."""
    wanted = [e.name for e in ordered]
    seen = []
    for name, data in stream_bundle_entries(src_path, backend, lim,
                                            where="bundle"):
        seen.append(name)
        yield name, (replacements[name] if name in replacements else data)
    if seen != wanted:
        raise PB3Error("E_CORRUPT", "thứ tự entry ở pass 2 khác pass 1",
                       where="bundle")


def _copy_file(src, out_fh):
    with open(src, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            out_fh.write(chunk)


# ------------------------------------------------------------ SerializedFile
@dataclass
class TypeTreeNode:
    type: str
    name: str
    byte_size: int
    index: int
    level: int
    meta_flag: int
    children: List["TypeTreeNode"] = field(default_factory=list)


@dataclass
class SerializedType:
    class_id: int
    script_type_index: int = -1
    script_id: Optional[bytes] = None
    old_type_hash: bytes = b"\x00" * 16
    is_stripped: bool = False
    node: Optional[TypeTreeNode] = None
    raw_typetree: bytes = b""  # blob gốc (ghi lại nguyên vẹn khi rebuild)
    type_dependencies: Optional[List[int]] = None
    ref_names: Optional[Tuple[str, str, str]] = None
    # v23+: content hash của type tree (XXH3, 16 byte); ghi lại nguyên vẹn.
    type_tree_content_hash: bytes = b"\x00" * 16


@dataclass
class SerializedObject:
    path_id: int
    byte_start: int  # tương đối so với data_offset
    byte_size: int
    type_id: int
    class_id: int
    script_type_index: Optional[int] = None
    is_stripped: Optional[bool] = None


@dataclass
class SerializedFile:
    version: int  # header version (15-23)
    unity_version: str = ""
    target_platform: int = 0
    enable_type_tree: bool = False
    types: List[SerializedType] = field(default_factory=list)
    objects: List[SerializedObject] = field(default_factory=list)
    script_types: List[Tuple[int, int]] = field(default_factory=list)
    externals: List[Dict] = field(default_factory=list)
    ref_types: List[SerializedType] = field(default_factory=list)
    user_information: str = ""
    data_offset: int = 0
    metadata_size: int = 0
    file_size: int = 0
    endian: str = "<"
    source_hash: str = ""
    raw: bytes = b""
    # vị trí vùng object table trong file (để rebuild giữ nguyên metadata còn lại)
    object_table_offset: int = 0
    object_table_end: int = 0
    # cache text_assets() (lazy; SerializedFile không đổi sau khi parse)
    _text_cache: Optional[List] = field(default=None, repr=False, compare=False)
    _by_path_id: Optional[dict] = field(default=None, repr=False, compare=False)
    where: str = field(default="assets", repr=False, compare=False)

    def get_object(self, path_id):
        # Chỉ mục lười theo path_id: tra cứu O(1) thay vì quét tuyến tính
        # cả bảng object mỗi lần (plan một file có hàng nghìn object và
        # hàng trăm TextAsset thì quét tuyến tính chiếm phần lớn thời gian).
        if self._by_path_id is None:
            self._by_path_id = {o.path_id: o for o in self.objects}
        try:
            return self._by_path_id[path_id]
        except KeyError:
            raise PB3Error("E_INPUT_NOT_FOUND", "không có object path_id=%d" % path_id,
                           where=self.where + "::%d" % path_id)

    def object_bytes(self, path_id):
        o = self.get_object(path_id)
        start = self.data_offset + o.byte_start
        end = start + o.byte_size
        if end > len(self.raw):
            raise PB3Error("E_CORRUPT", "object %d vượt quá file" % path_id,
                           where=self.where + "::%d" % path_id)
        return self.raw[start:end]

    def text_assets(self, warnings=None):
        """[(path_id, name, script)] cho mọi object TextAsset (class 49).

        Kết quả cache theo instance (SerializedFile không đổi sau khi parse).
        Object TextAsset hỏng (không đọc được) bị bỏ qua kèm ghi chú vào
        `warnings` (nếu có) thay vì làm hỏng cả file; tìm đúng object đích
        bị hỏng thì xem `find_text_asset` (raise kèm path_id)."""
        if self._text_cache is not None:
            return list(self._text_cache)
        out = []
        notes = []
        for o in self.objects:
            if o.class_id == 49:
                try:
                    name, script = read_text_asset_bytes(
                        self.object_bytes(o.path_id), self.endian)
                except PB3Error as exc:
                    notes.append("bỏ qua TextAsset path_id=%d (%s)"
                                 % (o.path_id, exc.code))
                    continue
                out.append((o.path_id, name, script))
        if warnings is not None:
            warnings.extend(notes)
        self._text_cache = out
        return list(out)

    def monobehaviour_texts(self, warnings=None):
        """Text trong mọi object MonoBehaviour (class 114).

        Trả list dict, mỗi object một mục:
        - kind "string_table": Unity Localization StringTable — entries
          {m_Id: chuỗi localized}, locale từ m_LocaleId.m_Code (vd "en").
        - kind "shared_table": SharedTableData — entries {m_Id: key},
          collection từ m_TableCollectionName (để join với StringTable).
        - kind "locale": đối tượng Locale (chỉ có m_Identifier/m_LocaleName,
          không phải text dịch) — bên gọi bỏ qua.
        - kind "language_source": bảng ngôn ngữ có cấu trúc — languages
          [{name, code}], terms [{term, values}] theo đúng thứ tự cột
          (values[i] thuộc languages[i], GIỮ chuỗi rỗng). Từ type tree
          (nếu có) hoặc byte-walk đã validate (nếu không); object không
          khớp layout thì rơi về generic như trước.
        - kind "generic": MonoBehaviour khác — fields {đường_dẫn: chuỗi}
          nếu có type tree, strings [chuỗi...] nếu không (quét đã xác minh).
        Object đọc lỗi bị bỏ qua kèm ghi chú vào `warnings` (nếu có) thay
        vì làm hỏng cả file, như text_assets()."""
        out = []
        notes = []
        for o in self.objects:
            if o.class_id != 114:
                continue
            try:
                typ = self.types[o.type_id]
            except IndexError:
                notes.append("bỏ qua MonoBehaviour path_id=%d (type_id lạ)" % o.path_id)
                continue
            if typ.node is not None:
                try:
                    val = read_monobehaviour_object(self, o.path_id)
                except PB3Error as exc:
                    notes.append("bỏ qua MonoBehaviour path_id=%d (%s)"
                                 % (o.path_id, exc.code))
                    continue
                name = val.get("m_Name") if isinstance(val.get("m_Name"), str) else ""
                if "m_TableData" in val and "m_LocaleId" in val:
                    loc = val.get("m_LocaleId")
                    code = loc.get("m_Code") if isinstance(loc, dict) else None
                    entries = {}
                    for e in val.get("m_TableData") or []:
                        if (isinstance(e, dict) and isinstance(e.get("m_Id"), int)
                                and isinstance(e.get("m_Localized"), str)):
                            entries[e["m_Id"]] = e["m_Localized"]
                    out.append({"path_id": o.path_id, "name": name or "",
                                "kind": "string_table",
                                "locale": code if isinstance(code, str) else None,
                                "entries": entries})
                elif "m_Entries" in val and "m_TableCollectionName" in val:
                    entries = {}
                    for e in val.get("m_Entries") or []:
                        if (isinstance(e, dict) and isinstance(e.get("m_Id"), int)
                                and isinstance(e.get("m_Key"), str)):
                            entries[e["m_Id"]] = e["m_Key"]
                    out.append({"path_id": o.path_id, "name": name or "",
                                "kind": "shared_table",
                                "collection": val.get("m_TableCollectionName") or "",
                                "entries": entries})
                elif "m_Identifier" in val:
                    ident = val.get("m_Identifier")
                    code = ident.get("m_Code") if isinstance(ident, dict) else None
                    out.append({"path_id": o.path_id, "name": name or "",
                                "kind": "locale",
                                "locale": code if isinstance(code, str) else None})
                else:
                    ls = language_source_from_fields(val)
                    if ls is not None:
                        out.append({"path_id": o.path_id, "name": name or "",
                                    "kind": "language_source",
                                    "languages": ls["languages"],
                                    "terms": ls["terms"],
                                    "trailing_bytes": ls["trailing_bytes"]})
                    else:
                        fields = {}
                        _flatten_typetree_strings(val, "", fields)
                        out.append({"path_id": o.path_id, "name": name or "",
                                    "kind": "generic", "locale": None,
                                    "fields": fields})
            else:
                try:
                    raw = self.object_bytes(o.path_id)
                except PB3Error as exc:
                    notes.append("bỏ qua MonoBehaviour path_id=%d (%s)"
                                 % (o.path_id, exc.code))
                    continue
                ls = parse_language_source_bytes(raw, self.endian)
                if ls is not None:
                    out.append({"path_id": o.path_id, "name": ls["name"],
                                "kind": "language_source",
                                "languages": ls["languages"],
                                "terms": ls["terms"],
                                "trailing_bytes": ls["trailing_bytes"]})
                    continue
                try:
                    name, strings = scan_monobehaviour_strings(raw, self.endian)
                except PB3Error as exc:
                    notes.append("bỏ qua MonoBehaviour path_id=%d (%s)"
                                 % (o.path_id, exc.code))
                    continue
                out.append({"path_id": o.path_id, "name": name or "",
                            "kind": "generic", "locale": None,
                            "strings": strings})
        if warnings is not None:
            warnings.extend(notes)
        return out

    def find_text_asset(self, name=None, path_id=None):
        if path_id is not None:
            # tìm đúng object đích: hỏng thì raise kèm path_id (không bỏ qua).
            for o in self.objects:
                if o.class_id == 49 and o.path_id == path_id:
                    try:
                        n, script = read_text_asset_bytes(
                            self.object_bytes(o.path_id), self.endian)
                    except PB3Error as exc:
                        raise PB3Error("E_CORRUPT",
                                       "TextAsset path_id=%d đọc lỗi: %s"
                                       % (path_id, exc.code),
                                       where="path_id=%d" % path_id)
                    return o.path_id, n, script
            raise PB3Error("E_INPUT_NOT_FOUND", "không có TextAsset path_id=%d"
                           % path_id, where=self.where + "::%d" % path_id)
        for pid, n, script in self.text_assets():
            if name is not None and n == name:
                return pid, n, script
        raise PB3Error("E_INPUT_NOT_FOUND", "không có TextAsset %r" % name,
                       where=self.where + "::" + name)


def read_text_asset_bytes(data, endian="<"):
    """TextAsset = aligned_string(m_Name) + aligned_string(m_Script)."""
    r = _Reader(data, endian, where="TextAsset")
    name = r.aligned_string()
    script = r.aligned_string()
    return name, script


def read_text_asset_head(data, endian="<"):
    """Chỉ đọc tên + độ dài script (số ký tự) của TextAsset, không giữ
    lại chuỗi script sau khi đếm xong.

    Dùng cho lập plan: plan chỉ cần tên (đoán dạng file) và độ dài script
    (>1000 thì đoán "text (chuỗi dài)"), nên giữ cả script 5MB trong bộ
    nhớ chỉ để lấy 2 số đó là phí. Kết quả y hệt read_text_asset_bytes:
    độ dài là số KÝ TỰ (script decode UTF-8), script ASCII thì đếm bằng
    số byte không cần decode, script không phải UTF-8 hợp lệ thì raise
    PB3Error như bản đọc đủ, đệm căn 4 cuối script cũng kiểm tra y hệt
    (đọc tới mà thiếu đệm thì raise, không trả kết quả nửa vời).

    Đọc trực tiếp bằng struct.unpack_from trên bytes (không dựng _Reader):
    cùng thứ tự kiểm tra và cùng thông điệp lỗi như aligned_string/i32/
    align (kể cả lỗi "đọc vượt quá dữ liệu (N byte tại P)"), chỉ rẻ hơn
    vì khỏi tạo đối tượng + gọi hàm từng trường."""
    b = bytes(data)
    n = len(b)
    where = "TextAsset"
    if n < 4:
        raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (4 byte tại 0)",
                       where=where)
    (size,) = struct.unpack_from(endian + "i", b, 0)
    if size < 0 or size > n - 4:
        raise PB3Error("E_CORRUPT", "chuỗi dài %d byte không hợp lệ" % size,
                       where=where)
    pos = 4 + size
    pad = (-pos) % 4
    if pos + pad > n:
        raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (%d byte tại %d)"
                       % (pad, pos), where=where)
    pos += pad
    try:
        name = b[4:4 + size].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error_from_exception(exc, where=where)
    if pos + 4 > n:
        raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (4 byte tại %d)" % pos,
                       where=where)
    (size,) = struct.unpack_from(endian + "i", b, pos)
    pos += 4
    if size < 0 or size > n - pos:
        raise PB3Error("E_CORRUPT", "chuỗi dài %d byte không hợp lệ" % size,
                       where=where)
    raw = b[pos:pos + size]
    pos += size
    pad = (-pos) % 4
    if pos + pad > n:
        raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (%d byte tại %d)"
                       % (pad, pos), where=where)
    if raw.isascii():
        return name, size
    try:
        return name, len(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise error_from_exception(exc, where=where)


# ------------------------------------------------------- MonoBehaviour text
# Đọc text trong MonoBehaviour (class 114) — text chính của game thường nằm
# đây (Unity Localization StringTable/SharedTableData, bảng ngôn ngữ I2, ...),
# không phải trong TextAsset (class 49). Hai đường:
# - Có type tree (vd bundle Addressables của 9 Kings): đọc theo cây kiểu,
#   biết tên field -> StringTable join key/value chính xác theo m_Id.
# - Không có type tree (mọi .assets trong build player: type tree bị strip):
#   quét chuỗi aligned_string đã xác minh bằng length-prefix trong từng
#   object MonoBehaviour (đầu object luôn là m_GameObject/m_Enabled/
#   m_Script/m_Name nên m_Name đọc được theo vị trí cố định).
# Tên type/field trong type tree thường là "common_NNN" (chỉ số vào bảng
# chuỗi chung của engine) nên reader suy theo HÌNH DẠNG node, không theo tên:
# node Array có đúng 2 con tên common_795 (size) + common_106 (data); string
# là node có 1 con Array mà element là lá 1 byte; struct là các con còn lại;
# mọi field căn 4 (đã kiểm chứng: parse bundle localization 9 Kings thật
# cho end == đúng kích thước object, join SharedTableData 100/100).
_MB_RUN_RE = re.compile(
    rb"(?:[\x20-\x7E]|[\xC2-\xDF][\x80-\xBF]|[\xE0-\xEF][\x80-\xBF]{2}"
    rb"|[\xF0-\xF4][\x80-\xBF]{3}){3,}")
_MB_HEADER_SIZE = 28  # PPtr m_GameObject (12) + m_Enabled (1) + đệm 3 + PPtr m_Script (12)


def _typetree_align4(pos):
    return (pos + 3) & ~3


def _typetree_is_array(node):
    """Node Array: đúng 1 con, con đó có đúng 2 con tên common_795 + common_106."""
    if len(node.children) != 1:
        return False
    arr = node.children[0]
    if len(arr.children) != 2:
        return False
    size_node, _elem = arr.children
    return (size_node.byte_size == 4 and not size_node.children
            and arr.children[0].name == "common_795"
            and arr.children[1].name == "common_106")


def read_typetree_value(data, node, pos, endian="<"):
    """Đọc 1 giá trị theo node type tree tại pos (tương đối đầu object).

    Trả (giá_trị, vị_trị_mới). dict = struct, list = vector, str = chuỗi
    (byte[] giải UTF-8 lỗi thì trả bytes để bên gọi bỏ qua, không hỏng cả
    object). Hỏng cấu trúc -> PB3Error E_CORRUPT."""
    where = "MonoBehaviour"
    n = len(data)
    if node.children:
        if _typetree_is_array(node):
            _size_node, elem = node.children[0].children
            pos = _typetree_align4(pos)
            if pos + 4 > n:
                raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (4 byte tại %d)"
                               % pos, where=where)
            (count,) = struct.unpack_from(endian + "i", data, pos)
            pos += 4
            if count < 0 or count > 1000000:
                raise PB3Error("E_CORRUPT", "vector %r dài %d không hợp lệ"
                               % (node.name, count), where=where)
            if elem.byte_size == 1 and not elem.children:
                if pos + count > n:
                    raise PB3Error("E_CORRUPT", "chuỗi dài %d vượt quá dữ liệu"
                                   % count, where=where)
                raw = data[pos:pos + count]
                pos = _typetree_align4(pos + count)
                try:
                    return raw.decode("utf-8"), pos
                except UnicodeDecodeError:
                    return bytes(raw), pos
            vals = []
            for _ in range(count):
                pos = _typetree_align4(pos)
                v, pos = read_typetree_value(data, elem, pos, endian)
                vals.append(v)
            return vals, pos
        out = {}
        for c in node.children:
            pos = _typetree_align4(pos)
            v, pos = read_typetree_value(data, c, pos, endian)
            out[c.name] = v
        return out, pos
    if node.byte_size == 0:
        return None, pos
    if node.byte_size == 1:
        if pos + 1 > n:
            raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (1 byte tại %d)"
                           % pos, where=where)
        return data[pos], pos + 1
    if node.byte_size == 2:
        if pos + 2 > n:
            raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (2 byte tại %d)"
                           % pos, where=where)
        return struct.unpack_from(endian + "h", data, pos)[0], pos + 2
    if node.byte_size == 4:
        if pos + 4 > n:
            raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (4 byte tại %d)"
                           % pos, where=where)
        return struct.unpack_from(endian + "i", data, pos)[0], pos + 4
    if node.byte_size == 8:
        if pos + 8 > n:
            raise PB3Error("E_CORRUPT", "đọc vượt quá dữ liệu (8 byte tại %d)"
                           % pos, where=where)
        return struct.unpack_from(endian + "q", data, pos)[0], pos + 8
    raise PB3Error("E_CORRUPT", "node lá byte_size=%d (%s) chưa hiểu"
                   % (node.byte_size, node.name), where=where)


def read_monobehaviour_name(data, endian="<"):
    """Tên MonoBehaviour (m_Name) theo vị trí cố định ở đầu object.

    Đầu object MonoBehaviour luôn là m_GameObject (PPtr 12 byte) +
    m_Enabled (1 byte) + đệm 3 + m_Script (PPtr 12 byte) = 28 byte, rồi
    chuỗi aligned_string m_Name. Lỗi/đọc không được -> None (không raise)."""
    try:
        if len(data) < _MB_HEADER_SIZE + 4:
            return None
        (size,) = struct.unpack_from(endian + "i", data, _MB_HEADER_SIZE)
        if size < 0 or size > 4096 or _MB_HEADER_SIZE + 4 + size > len(data):
            return None
        return data[_MB_HEADER_SIZE + 4:_MB_HEADER_SIZE + 4 + size].decode("utf-8")
    except (struct.error, UnicodeDecodeError):
        return None


def _mb_text_ok(raw):
    """bytes -> str nếu toàn ký tự in được/khoảng trắng (\n\r\t) hợp lệ UTF-8."""
    try:
        s = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None
    for ch in s:
        o = ord(ch)
        if o in (9, 10, 13) or 0x20 <= o <= 0x7E or o >= 0xA0:
            continue
        return None
    return s


# ------------------------------------------------------------------ bảng ngôn ngữ
# Bảng ngôn ngữ của game (vd plugin localization lưu mọi term + mọi bản dịch
# trong 1 object MonoBehaviour): cấu trúc nhị phân khi build player đã lược
# type tree — đọc bằng cách đi bộ theo layout đã kiểm chứng, KHÔNG quét chuỗi
# phẳng (quét phẳng bỏ chuỗi rỗng nên lệch cột, gán nhầm ngôn ngữ — lỗi
# en.json 2026-11-17). Layout [ĐÃ ĐO THẬT] trên 4 build thật (9 Kings IPA
# v1.0.6, 9 Kings build riêng BuildNumber 68, Poly Bridge 3 v1.1.7,
# Exploding Kittens 2 v0.0.18): header 28 byte + m_Name, rồi 3 int32 (KHÔNG
# khẳng định ngữ nghĩa — đo được 0/1 khác nhau giữa các build) + int32 số
# term; mỗi term: chuỗi Term, int32 TermType, int32 L + L chuỗi bản dịch
# (GIỮ chuỗi rỗng), int32 F == L + F byte cờ, int32 T + T chuỗi; rồi 3
# int32 + int32 số ngôn ngữ == L, mỗi ngôn ngữ: chuỗi Name, chuỗi Code (có
# thể rỗng — cột không phải ngôn ngữ như Notes/Status), 4 byte (1 byte cờ +
# đệm); phần đuôi dài tùy build, chỉ giới hạn độ dài, không parse. Thứ tự
# cột ngôn ngữ KHÁC NHAU giữa các build (vd zh-CN/zh-TW đổi chỗ) nên ánh xạ
# luôn đọc từ bảng ngôn ngữ của chính file, không giả định. Parser validate
# nghiêm ngặt và FAIL-CLOSED: bất kỳ điều kiện nào sai -> None để caller
# dùng đường quét chuỗi cũ, không bao giờ trả bảng sai. Không khớp theo tên
# object/tên game: chỉ cấu trúc.
_LS_MAX_TERMS = 200000
_LS_MAX_LANGS = 64
_LS_MAX_TOUCH = 1024
_LS_MAX_VALUE_CHARS = 1048576
_LS_MAX_TRAILING = 65536
_LS_CODE_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})?$")


def _ls_no_control(s):
    """Chuỗi không chứa ký tự điều khiển (kể cả NUL/DEL)."""
    for ch in s:
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            return False
    return True


def parse_language_source_bytes(raw, endian="<"):
    """Parse 1 object MonoBehaviour thành bảng ngôn ngữ có cấu trúc.

    Trả {"name", "languages": [{"name", "code"}], "terms": [{"term",
    "values": [chuỗi theo đúng thứ tự languages, GIỮ chuỗi rỗng]}],
    "trailing_bytes": int} hoặc None khi dữ liệu không khớp layout (kể cả
    object không phải bảng ngôn ngữ). Không raise với dữ liệu xấu.
    values[i] thuộc languages[i] — ánh xạ theo CHỈ SỐ, không đoán."""
    try:
        data = bytes(raw)
        name = read_monobehaviour_name(data, endian)
        if name is None or len(name) > 256 or not _ls_no_control(name):
            return None
        body_start = _MB_HEADER_SIZE + 4 + len(name.encode("utf-8"))
        body_start += (-body_start) % 4
        r = _Reader(data, endian, where="language_source")
        r.pos = body_start
        r.i32()
        r.i32()
        r.i32()
        n_terms = r.i32()
        if n_terms < 1 or n_terms > _LS_MAX_TERMS:
            return None
        terms = []
        seen = set()
        n_langs = None
        for _ in range(n_terms):
            term = r.aligned_string()
            if (not term or len(term) > 512 or not _ls_no_control(term)
                    or term in seen):
                return None
            seen.add(term)
            r.i32()  # TermType (kiểu term: text/audio/...); không ràng buộc giá trị
            lang_count = r.i32()
            if n_langs is None:
                if lang_count < 1 or lang_count > _LS_MAX_LANGS:
                    return None
                n_langs = lang_count
            elif lang_count != n_langs:
                return None
            values = []
            for _i in range(n_langs):
                v = r.aligned_string()
                if len(v) > _LS_MAX_VALUE_CHARS or "\x00" in v:
                    return None
                values.append(v)
            flag_count = r.i32()
            if flag_count != n_langs:
                return None
            r.read(flag_count)
            r.align(4)
            touch_count = r.i32()
            if touch_count < 0 or touch_count > _LS_MAX_TOUCH:
                return None
            for _i in range(touch_count):
                v = r.aligned_string()
                if len(v) > _LS_MAX_VALUE_CHARS or "\x00" in v:
                    return None
            terms.append({"term": term, "values": values})
        r.i32()
        r.i32()
        r.i32()
        n_lang2 = r.i32()
        if n_lang2 != n_langs:
            return None
        languages = []
        any_code = False
        for _ in range(n_langs):
            lname = r.aligned_string()
            code = r.aligned_string()
            r.i32()
            if (not lname or len(lname) > 256 or not _ls_no_control(lname)
                    or len(code) > 32):
                return None
            if code:
                if _LS_CODE_RE.match(code) is None:
                    return None
                any_code = True
            languages.append({"name": lname, "code": code})
        if not any_code:
            return None
        trailing = r.remaining()
        if trailing < 0 or trailing > _LS_MAX_TRAILING:
            return None
        return {"name": name, "languages": languages, "terms": terms,
                "trailing_bytes": trailing}
    except (PB3Error, struct.error, ValueError, UnicodeDecodeError,
            IndexError, TypeError):
        return None


def _ls_field(d, *names):
    for n in names:
        if n in d:
            return d[n]
    return None


def language_source_from_fields(val):
    """Bảng ngôn ngữ từ giá trị type-tree đã parse (build còn type tree).

    [HYPOTHESIS]: tên trường (mSource/mLanguages/mTerms, Name/Code,
    Term/Languages) suy từ layout nhị phân đã đo + quy ước đặt tên trường
    Unity; CHƯA có build thật nào còn type tree để kiểm chứng — chỉ có test
    trên dict dựng sẵn. Sai hình dạng -> None, caller dùng đường generic.
    Validate giống bản byte-walk: L == số ngôn ngữ, tên term duy nhất,
    ít nhất 1 mã ngôn ngữ hợp lệ, giữ chuỗi rỗng."""
    try:
        if not isinstance(val, dict):
            return None
        src = val.get("mSource")
        if not isinstance(src, dict):
            src = val
        langs_raw = _ls_field(src, "mLanguages", "Languages")
        terms_raw = _ls_field(src, "mTerms", "Terms")
        if not isinstance(langs_raw, list) or not isinstance(terms_raw, list):
            return None
        if not 1 <= len(langs_raw) <= _LS_MAX_LANGS:
            return None
        if not 1 <= len(terms_raw) <= _LS_MAX_TERMS:
            return None
        languages = []
        any_code = False
        for entry in langs_raw:
            if not isinstance(entry, dict):
                return None
            lname = _ls_field(entry, "Name", "m_Name", "name")
            code = _ls_field(entry, "Code", "m_Code", "code")
            if (not isinstance(lname, str) or not lname or len(lname) > 256
                    or not _ls_no_control(lname)
                    or not isinstance(code, str) or len(code) > 32):
                return None
            if code:
                if _LS_CODE_RE.match(code) is None:
                    return None
                any_code = True
            languages.append({"name": lname, "code": code})
        if not any_code:
            return None
        n_langs = len(languages)
        terms = []
        seen = set()
        for entry in terms_raw:
            if not isinstance(entry, dict):
                return None
            term = _ls_field(entry, "Term", "m_Term", "term")
            values = _ls_field(entry, "Languages", "m_Languages", "languages")
            if (not isinstance(term, str) or not term or len(term) > 512
                    or not _ls_no_control(term) or term in seen
                    or not isinstance(values, list) or len(values) != n_langs):
                return None
            seen.add(term)
            clean = []
            for v in values:
                if (not isinstance(v, str) or len(v) > _LS_MAX_VALUE_CHARS
                        or "\x00" in v):
                    return None
                clean.append(v)
            terms.append({"term": term, "values": clean})
        return {"languages": languages, "terms": terms,
                "trailing_bytes": None}
    except (ValueError, TypeError, AttributeError):
        return None



def scan_monobehaviour_strings(data, endian="<"):
    """Quét chuỗi trong 1 object MonoBehaviour KHÔNG có type tree.

    Trả (m_Name|None, [chuỗi đã xác minh]). Mỗi chuỗi phải là aligned_string
    thật: i32 độ dài L ở vị trí căn 4 ngay trước chuỗi, L byte là text UTF-8
    in được, đệm sau (nếu có) toàn 0. Tìm ứng viên bằng regex chạy bằng C
    trên bytes rồi chỉ xác minh những ứng viên đó — chạy 59MB trong ~0,2s,
    không quét từng offset bằng Python. Chuỗi chứa \n (regex cắt làm đôi)
    vẫn nhận được bằng cách lấy đúng L byte từ đầu chuỗi."""
    data = bytes(data)
    n = len(data)
    name = read_monobehaviour_name(data, endian)
    body_start = _MB_HEADER_SIZE
    if name is not None:
        body_start = _MB_HEADER_SIZE + 4 + len(name.encode("utf-8"))
        body_start += (-body_start) % 4
    out = []
    for m in _MB_RUN_RE.finditer(data, body_start):
        s0 = m.start()
        run = m.group()
        lp = s0 - 4
        if lp < body_start or lp % 4 != 0:
            continue
        try:
            (size,) = struct.unpack_from(endian + "i", data, lp)
        except struct.error:
            continue
        if size <= 0 or size > 65536 or lp + 4 + size > n:
            continue
        if size == len(run):
            s = _mb_text_ok(run)
            if s is None:
                continue
            pad = (-(lp + 4 + size)) % 4
            if pad and any(data[lp + 4 + size:lp + 4 + size + pad]):
                continue
            out.append(s)
        elif size > len(run):
            # Chuỗi chứa \n (regex cắt) hoặc ký tự regex không nhận: lấy đúng size byte.
            s = _mb_text_ok(data[s0:s0 + size])
            if s is None:
                continue
            pad = (-(lp + 4 + size)) % 4
            if pad and any(data[s0 + size:s0 + size + pad]):
                continue
            out.append(s)
        else:  # size < len(run): run gộp nhiều chuỗi (không có byte 0 ngăn) — lấy đúng size byte đầu.
            s = _mb_text_ok(run[:size])
            if s is None:
                continue
            out.append(s)
    return name, out


def _flatten_typetree_strings(value, prefix, out):
    """Thu mọi chuỗi lá trong dict type tree đã đọc -> {đường_dẫn_field: chuỗi}.

    Bỏ cây `references` + `m_Metadata` (tên kiểu/assembly, không phải text
    game) và các field gốc m_GameObject/m_Enabled/m_Script/m_Name (PPtr
    không có chuỗi; m_Name đã làm tên object). bytes (byte[] giải UTF-8 lỗi)
    thì bỏ qua."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("references", "m_Metadata", "m_GameObject", "m_Enabled",
                     "m_Script", "m_Name"):
                continue
            _flatten_typetree_strings(v, prefix + "." + k if prefix else k, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten_typetree_strings(v, "%s[%d]" % (prefix, i), out)
    elif isinstance(value, str):
        out[prefix] = value


def read_monobehaviour_object(sf, path_id):
    """Đọc 1 MonoBehaviour CÓ type tree thành dict (tên field -> giá trị).

    Tên 4 field gốc (m_GameObject/m_Enabled/m_Script/m_Name) trong type tree
    lưu bằng chỉ số common-string nên đổi theo vị trí (chỉ khi hình dạng
    khớp: con 0/2 là PPtr, con 1 là bool 1 byte, con 3 là chuỗi). Không có
    type tree -> PB3Error E_UNSUPPORTED_FORMAT (dùng scan_monobehaviour_strings)."""
    o = sf.get_object(path_id)
    if o.class_id != 114:
        raise PB3Error("E_CONFIG_INVALID", "object %d không phải MonoBehaviour" % path_id,
                       where=sf.where + "::%d" % path_id)
    typ = sf.types[o.type_id]
    if typ.node is None:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "MonoBehaviour path_id=%d không có type tree (bị strip ở build player)"
                       % path_id, where=sf.where + "::%d" % path_id)
    raw = sf.object_bytes(path_id)
    val, _end = read_typetree_value(raw, typ.node, 0, sf.endian)
    if not isinstance(val, dict):
        raise PB3Error("E_CORRUPT", "MonoBehaviour path_id=%d: gốc type tree không phải struct"
                       % path_id, where=sf.where + "::%d" % path_id)
    children = typ.node.children
    if len(children) >= 4:
        c0, c1, c2, c3 = children[0], children[1], children[2], children[3]
        pptr = (lambda c: len(c.children) == 2
                and [g.name for g in c.children] == ["m_FileID", "m_PathID"])
        if (pptr(c0) and c1.byte_size == 1 and not c1.children
                and pptr(c2) and isinstance(val.get(c3.name), str)):
            names = [c.name for c in children[:4]]
            val["m_GameObject"] = val.pop(names[0])
            val["m_Enabled"] = val.pop(names[1])
            val["m_Script"] = val.pop(names[2])
            val["m_Name"] = val.pop(names[3])
    return val


def _parse_typetree_blob(reader, version, where):
    """Blob type tree (v>=12): node_count, stringbuffer, node 24/32 byte.

    v23+ (UnityPy 1.25.4 TypeTreeNode.parse_blob): blob mở đầu bằng magic
    "mhtt" + format version (i32, phải bằng version) trước node_count."""
    start = reader.pos
    if version >= 23:
        magic = reader.read(4)
        if magic != b"mhtt":
            raise PB3Error("E_CORRUPT", "type tree blob v23 thiếu magic 'mhtt'",
                           where=where)
        fmt_version = reader.i32()
        if fmt_version != version:
            raise PB3Error("E_CORRUPT",
                           "type tree blob v23: format version %d != %d"
                           % (fmt_version, version), where=where)
    node_count = reader.i32()
    buf_size = reader.i32()
    if node_count < 0 or node_count > 100000 or buf_size < 0:
        raise PB3Error("E_CORRUPT", "type tree blob: số node/buffer lạ", where=where)
    fmt = "hBBIIiii" + ("Q" if version >= 19 else "")
    node_size = struct.calcsize("<" + fmt)
    raw_nodes = reader.read(node_size * node_count)
    strbuf = reader.read(buf_size)
    raw_blob = reader._b[start:reader.pos]
    nodes = []
    for values in struct.iter_unpack("<" + fmt, raw_nodes):
        ver, level, tflags, type_off, name_off, bsize, index, meta = values[:8]
        nodes.append((ver, level, tflags, type_off, name_off, bsize, index, meta))

    def _str(off):
        if off & 0x80000000:
            return "common_%d" % (off & 0x7FFFFFFF)
        end = strbuf.find(b"\x00", off)
        if end < 0:
            raise PB3Error("E_CORRUPT", "type tree blob: chuỗi không kết thúc",
                           where=where)
        return strbuf[off:end].decode("utf-8", "replace")

    fake = TypeTreeNode("", "", 0, 0, -1, 0, [])
    stack = [fake]
    parent = fake
    prev = fake
    for ver, level, tflags, type_off, name_off, bsize, index, meta in nodes:
        node = TypeTreeNode(_str(type_off), _str(name_off), bsize, index,
                            level, meta, [])
        if level > prev.level:
            stack.append(parent)
            parent = prev
        elif level < prev.level:
            while level <= parent.level:
                if len(stack) <= 1:
                    raise PB3Error("E_CORRUPT",
                                   "type tree blob: cấp node thụt lùi không hợp lệ "
                                   "(level %d)" % level, where=where)
                parent = stack.pop()
        parent.children.append(node)
        prev = node
    root = fake.children[0] if fake.children else None
    return root, raw_blob


def _parse_serialized_type(reader, version, enable_type_tree, is_ref_type, where):
    class_id = reader.i32()
    typ = SerializedType(class_id=class_id)
    if version >= 16:
        typ.is_stripped = reader.boolean()
    if version >= 17:
        typ.script_type_index = reader.i16()
    if version >= 13:
        if ((is_ref_type and typ.script_type_index >= 0)
                or (version < 16 and class_id < 0)
                or (version >= 16 and class_id == 114)):
            typ.script_id = reader.read(16)
        typ.old_type_hash = reader.read(16)
    if enable_type_tree:
        if version >= 23:
            # UnityPy 1.25.4 (SerializedType.read): v23+ có content hash
            # (XXH3, 16 byte) + kích thước blob đã serialize; size 0 nghĩa
            # là type này không có type tree (node = None).
            typ.type_tree_content_hash = reader.read(16)
            serialized_size = reader.i32()
            if serialized_size < 0 or serialized_size > 10000000:
                raise PB3Error("E_CORRUPT",
                               "type tree v23: serialized size %d lạ"
                               % serialized_size, where=where)
            if serialized_size:
                blob_start = reader.pos
                typ.node, typ.raw_typetree = _parse_typetree_blob(reader, version,
                                                                  where)
                if reader.pos - blob_start != serialized_size:
                    raise PB3Error("E_CORRUPT",
                                   "type tree v23: blob dài %d, khai %d"
                                   % (reader.pos - blob_start, serialized_size),
                                   where=where)
            else:
                typ.node = None
                typ.raw_typetree = b""
        elif version >= 12 or version == 10:
            typ.node, typ.raw_typetree = _parse_typetree_blob(reader, version, where)
        else:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "type tree kiểu cũ (v<12) chưa hỗ trợ", where=where)
        if version >= 21:
            if is_ref_type:
                cls = reader.string_to_null()
                ns = reader.string_to_null()
                asm = reader.string_to_null()
                typ.ref_names = (cls, ns, asm)
            else:
                count = reader.i32()
                if count < 0 or count > 100000:
                    raise PB3Error("E_CORRUPT", "type_dependencies lạ", where=where)
                typ.type_dependencies = [reader.i32() for _ in range(count)]
    return typ


def _parse_object_table_slow(r, sf, version, object_count, data, data_offset, where):
    """Vòng lặp đọc bảng object gốc (dùng _Reader từng trường).

    Dùng cho v11-16 (entry có script_idx i16 + is_stripped u8 rồi đệm tới
    bội 4; entry cuối không có đệm đuôi nên vùng bảng không thẳng hàng)
    và cho bảng rỗng (không align khi không có entry nào)."""
    seen = set()
    for _ in range(object_count):
        r.align(4)
        path_id = r.i64()
        byte_start = r.u64() if version >= 22 else r.u32()
        byte_size = r.u32()
        type_id = r.i32()
        script_idx = None
        is_stripped = None
        if 11 <= version < 17:
            script_idx = r.i16()
        if version in (15, 16):
            is_stripped = r.u8()
        if type_id < 0 or type_id >= len(sf.types):
            raise PB3Error("E_CORRUPT", "object %d: type_id %d lạ"
                           % (path_id, type_id), where=where)
        if path_id in seen:
            raise PB3Error("E_CORRUPT", "object path_id %d trùng" % path_id,
                           where=where)
        seen.add(path_id)
        if byte_start + byte_size > len(data) - data_offset:
            raise PB3Error("E_CORRUPT", "object %d vượt quá data section" % path_id,
                           where=where)
        sf.objects.append(SerializedObject(
            path_id=path_id, byte_start=byte_start, byte_size=byte_size,
            type_id=type_id, class_id=sf.types[type_id].class_id,
            script_type_index=script_idx, is_stripped=is_stripped))


def _parse_object_table_fast(r, sf, version, object_count, data, data_offset, where):
    """Đọc bảng object bằng struct.iter_unpack (C) thay vì gọi _Reader
    ~8 lần mỗi entry.

    Chỉ dùng khi kích thước entry == stride (không có đệm trong entry):
    v<11 và v17-21 (20 byte: path_id i64 + byte_start u32 + byte_size u32
    + type_id i32), v>=22 (24 byte: byte_start u64). v11-16 có script_idx
    i16 (+ is_stripped u8 ở v15-16) rồi đệm tới bội 4 — entry cuối không
    có đệm đuôi nên vùng bảng không phải bội của stride, giữ vòng lặp
    _Reader cũ cho đúng từng byte. Kiểm tra (type_id lạ, path_id trùng,
    vượt data section) y hệt vòng lặp cũ, cùng thông điệp lỗi; r.pos cuối
    cũng y hệt (bắt đầu sau align(4) đầu bảng, kết thúc đúng cuối entry)."""
    r.align(4)
    fmt = (r.endian + "qQII") if version >= 22 else (r.endian + "qIII")
    stride = struct.calcsize(fmt)
    r._need(object_count * stride)
    start = r.pos
    end = start + object_count * stride
    seen = set()
    types = sf.types
    ntypes = len(types)
    limit = len(data) - data_offset
    unpack = struct.Struct(fmt).iter_unpack
    for path_id, byte_start, byte_size, type_id in unpack(data[start:end]):
        if type_id < 0 or type_id >= ntypes:
            raise PB3Error("E_CORRUPT", "object %d: type_id %d lạ"
                           % (path_id, type_id), where=where)
        if path_id in seen:
            raise PB3Error("E_CORRUPT", "object path_id %d trùng" % path_id,
                           where=where)
        seen.add(path_id)
        if byte_start + byte_size > limit:
            raise PB3Error("E_CORRUPT", "object %d vượt quá data section" % path_id,
                           where=where)
        sf.objects.append(SerializedObject(
            path_id=path_id, byte_start=byte_start, byte_size=byte_size,
            type_id=type_id, class_id=types[type_id].class_id,
            script_type_index=None, is_stripped=None))
    r.pos = end


def parse_serialized(data, limits=None, where="assets"):
    """Đọc SerializedFile v15-23: header, metadata, object table.

    Header 4 số đầu LUÔN big-endian; phần sau theo cờ endian."""
    data = bytes(data)
    lim = limits or Limits()
    if len(data) < 20:
        raise PB3Error("E_CORRUPT", "SerializedFile quá ngắn", where=where)
    hr = _Reader(data, ">", where=where)
    metadata_size, file_size, version, data_offset = hr.unpack("IIII")
    if version not in SUPPORTED_SF_VERSIONS:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "SerializedFile version %d chưa hỗ trợ (chỉ %s)"
                       % (version, "/".join(map(str, SUPPORTED_SF_VERSIONS))),
                       where=where)
    endian_flag = data[16] != 0
    endian = ">" if endian_flag else "<"
    if version >= 22:
        # header mở rộng: CẢ 48 byte đầu đều big-endian (theo UnityPy:
        # SerializedFile.save ghi bằng EndianBinaryWriter() mặc định ">";
        # chỉ metadata + data mới theo cờ endian). Đọc bằng endian của
        # file là BUG (ghi chú #80): metadata_size/file_size/data_offset
        # bị đảo byte, UnityPy từ chối file v22/v23 do PB3 ghi.
        xr = _Reader(data[20:], ">", where=where)
        metadata_size = xr.u32()
        file_size = xr.u64()
        data_offset = xr.u64()
        xr.read(8)  # unknown
    if data_offset < 20 or data_offset > len(data):
        raise PB3Error("E_CORRUPT", "data_offset %d không hợp lệ" % data_offset,
                       where=where)
    if file_size != len(data):
        # Chỉ cảnh báo: một số file ghi file_size 0; cấu trúc mới quyết định.
        pass
    r = _Reader(data, endian, where=where)
    r.pos = 20 if version < 22 else 48
    sf = SerializedFile(version=version, endian=endian, raw=data,
                        data_offset=data_offset, metadata_size=metadata_size,
                        file_size=file_size,
                        source_hash=hashing.sha256_bytes(data), where=where)
    if version >= 7:
        sf.unity_version = r.string_to_null()
    if version >= 8:
        sf.target_platform = r.i32()
    if version >= 13:
        sf.enable_type_tree = r.boolean()
    type_count = r.i32()
    if type_count < 0 or type_count > 100000:
        raise PB3Error("E_CORRUPT", "số type %d không hợp lệ" % type_count, where=where)
    for _ in range(type_count):
        sf.types.append(_parse_serialized_type(r, version, sf.enable_type_tree,
                                               False, where))
    object_count = r.i32()
    if object_count < 0:
        raise PB3Error("E_CORRUPT", "số object âm", where=where)
    lim.check_entries(object_count, where)
    sf.object_table_offset = r.pos
    if object_count and (version < 11 or 17 <= version):
        # v<11, v17-21, v>=22: entry không đệm -> đọc hàng loạt bằng C.
        _parse_object_table_fast(r, sf, version, object_count, data,
                                 data_offset, where)
    else:
        _parse_object_table_slow(r, sf, version, object_count, data,
                                 data_offset, where)
    sf.object_table_end = r.pos
    if version >= 11:
        script_count = r.i32()
        if script_count < 0 or script_count > 100000:
            raise PB3Error("E_CORRUPT", "số script type lạ", where=where)
        for _ in range(script_count):
            idx = r.i32()
            if version < 14:
                local = r.i32()
            else:
                r.align(4)
                local = r.i64()
            sf.script_types.append((idx, local))
    ext_count = r.i32()
    if ext_count < 0 or ext_count > 100000:
        raise PB3Error("E_CORRUPT", "số external lạ", where=where)
    for _ in range(ext_count):
        ext = {}
        if version >= 6:
            ext["temp_empty"] = r.string_to_null()
        if version >= 5:
            ext["guid"] = r.read(16)
            ext["type"] = r.i32()
        ext["path"] = r.string_to_null()
        sf.externals.append(ext)
    if version >= 20:
        ref_count = r.i32()
        if ref_count < 0 or ref_count > 100000:
            raise PB3Error("E_CORRUPT", "số ref type lạ", where=where)
        for _ in range(ref_count):
            sf.ref_types.append(_parse_serialized_type(r, version,
                                                       sf.enable_type_tree, True,
                                                       where))
    if version >= 5:
        sf.user_information = r.string_to_null()
    return sf


def build_serialized(sf, object_data, *, version=None, unity_version=None):
    """Dựng lại toàn bộ SerializedFile từ model đã parse + dữ liệu object mới.

    object_data: {path_id: bytes}. Metadata ghi lại từ model (type tree blob
    giữ nguyên byte gốc). Dùng cho REBUILD_ALL; REBUILD_SECTION dùng đường
    phẫu thuật nhanh hơn (giữ nguyên metadata gốc ngoài object table)."""
    version = version if version is not None else sf.version
    unity_version = unity_version if unity_version is not None else sf.unity_version
    meta = _Writer(sf.endian)
    if version >= 7:
        meta.string_to_null(unity_version)
    if version >= 8:
        meta.pack("i", sf.target_platform)
    if version >= 13:
        meta.write(b"\x01" if sf.enable_type_tree else b"\x00")
    meta.pack("i", len(sf.types))
    for typ in sf.types:
        _write_serialized_type(meta, sf, typ, False, version)
    meta.pack("i", len(sf.objects))
    data = _Writer(sf.endian)
    entries = []
    for o in sf.objects:   # giữ NGUYÊN thứ tự object gốc (không sort theo path_id)
        payload = object_data.get(o.path_id)
        if payload is None:
            payload = sf.object_bytes(o.path_id)
        data.align(8)
        start = data.tell()
        data.write(payload)
        entries.append((o, start, len(payload)))
    # object table cần byte_start -> ghi sau khi dựng data (ghi đè vùng đã chừa)
    meta_bytes_first = meta.bytes()
    meta2 = _Writer(sf.endian)
    meta2.write(meta_bytes_first)
    # object table: vị trí hiện tại của meta2 = sau types
    for o, start, size in entries:
        meta2.align(4)
        meta2.pack("q", o.path_id)
        if version >= 22:
            meta2.pack("Q", start)
        else:
            meta2.pack("I", start)
        meta2.pack("I", size)
        meta2.pack("i", o.type_id)
        if 11 <= version < 17:
            meta2.pack("h", o.script_type_index if o.script_type_index is not None else -1)
        if version in (15, 16):
            meta2.write(bytes([o.is_stripped if o.is_stripped is not None else 0]))
    if version >= 11:
        meta2.pack("i", len(sf.script_types))
        for idx, local in sf.script_types:
            meta2.pack("i", idx)
            if version < 14:
                meta2.pack("i", local)
            else:
                meta2.align(4)
                meta2.pack("q", local)
    meta2.pack("i", len(sf.externals))
    for ext in sf.externals:
        if version >= 6:
            meta2.string_to_null(ext.get("temp_empty", ""))
        if version >= 5:
            meta2.write(ext.get("guid", b"\x00" * 16))
            meta2.pack("i", ext.get("type", 0))
        meta2.string_to_null(ext.get("path", ""))
    if version >= 20:
        meta2.pack("i", len(sf.ref_types))
        for typ in sf.ref_types:
            _write_serialized_type(meta2, sf, typ, True, version)
    if version >= 5:
        meta2.string_to_null(sf.user_information)
    meta_bytes = meta2.bytes()
    data_bytes = data.bytes()
    header_size = 20 if version < 22 else 48
    data_offset = _align_up(header_size + len(meta_bytes), 16)
    file_size = data_offset + len(data_bytes)
    out = _Writer(sf.endian)
    if version < 22:
        out.pack_be("IIII", len(meta_bytes), file_size, version, data_offset)
    else:
        out.pack_be("IIII", 0, 0, version, 0)
    out.write(b"\x01" if sf.endian == ">" else b"\x00")
    out.write(b"\x00" * 3)
    if version >= 22:
        # header mở rộng big-endian (như 4 số đầu), không theo endian file.
        out.pack_be("I", len(meta_bytes))
        out.pack_be("Q", file_size)
        out.pack_be("Q", data_offset)
        out.pack_be("Q", 0)
    out.write(meta_bytes)
    out.align16()
    out.write(data_bytes)
    return out.bytes()


def _write_serialized_type(writer, sf, typ, is_ref_type, version):
    writer.pack("i", typ.class_id)
    if version >= 16:
        writer.write(b"\x01" if typ.is_stripped else b"\x00")
    if version >= 17:
        writer.pack("h", typ.script_type_index)
    if version >= 13:
        if ((is_ref_type and typ.script_type_index >= 0)
                or (version < 16 and typ.class_id < 0)
                or (version >= 16 and typ.class_id == 114)):
            writer.write(typ.script_id if typ.script_id is not None else b"\x00" * 16)
        writer.write(typ.old_type_hash)
    if sf.enable_type_tree:
        if version >= 23:
            # UnityPy 1.25.4 (SerializedType.write): hash + độ dài blob + blob.
            writer.write(typ.type_tree_content_hash or b"\x00" * 16)
            writer.pack("i", len(typ.raw_typetree))
        writer.write(typ.raw_typetree)
        if version >= 21:
            if is_ref_type:
                cls, ns, asm = typ.ref_names or ("", "", "")
                writer.string_to_null(cls)
                writer.string_to_null(ns)
                writer.string_to_null(asm)
            else:
                deps = typ.type_dependencies or []
                writer.pack("i", len(deps))
                for d in deps:
                    writer.pack("i", d)


# ------------------------------------------------------------ engine detector
@dataclass
class EngineVerdict:
    engine: str  # "unity" | "unknown"
    level: str   # "CHẮC CHẮN" | "CÓ KHẢ NĂNG" | "CHƯA ĐỦ BẰNG CHỨNG"
    reasons: List[str] = field(default_factory=list)


_UNITY_STRONG = (
    "globalgamemanagers", "resources.assets", ".unity3d", "Data/Managed",
    "UnityFramework", "libmono", "il2cpp",
)
_UNITY_WEAK = ("sharedassets", "StreamingAssets", ".assets", ".resS", "Data/level")


def detect_engine(names):
    """Nhận diện engine từ danh sách tên entry (đặc tả 6.2; chỉ gợi ý)."""
    names = [str(n) for n in names]
    strong, weak, reasons = [], [], []
    for name in names:
        low = name.lower()
        for sig in _UNITY_STRONG:
            if sig.lower() in low and sig not in strong:
                strong.append(sig)
                reasons.append("có %r (%s)" % (sig, name))
        for sig in _UNITY_WEAK:
            if sig.lower() in low and sig not in weak:
                weak.append(sig)
    if len(strong) >= 2:
        level = "CHẮC CHẮN"
    elif len(strong) == 1 or len(weak) >= 2:
        level = "CÓ KHẢ NĂNG"
    else:
        level = "CHƯA ĐỦ BẰNG CHỨNG"
    engine = "unity" if level != "CHƯA ĐỦ BẰNG CHỨNG" else "unknown"
    if engine == "unknown":
        reasons.append("không thấy dấu hiệu Unity (cần globalgamemanagers, "
                       "resources.assets, *.unity3d, Data/Managed...)")
    return EngineVerdict(engine=engine, level=level, reasons=reasons)


# ------------------------------------------------------------------ planner
@dataclass
class PlanRow:
    container: str   # file/container chứa
    entry: str       # entry trong container ("" nếu chính file đó)
    kind: str        # "bundle" | "assets" | "file"
    object_type: str
    path_id: Optional[int]
    offset: int
    size: int
    guess: str       # "text" | "json" | "table" | "binary" | ...
    name: str = ""


@dataclass
class UnityPlan:
    verdict: EngineVerdict
    rows: List[PlanRow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    partial: bool = False  # True khi có entry lỗi/bỏ qua (E_PARSE_PARTIAL)


def _guess_for(entry_name, object_type, script_len=0):
    low = entry_name.lower()
    if object_type == "TextAsset":
        if low.endswith(".json"):
            return "json"
        if low.endswith((".csv", ".tsv")):
            return "table"
        if script_len > 1000:
            return "text (chuỗi dài)"
        return "text"
    if low.endswith(".json"):
        return "json"
    if low.endswith((".csv", ".tsv")):
        return "table"
    if low.endswith((".txt", ".strings", ".xml", ".plist")):
        return "text"
    return "binary"


def _dedupe(seq):
    """Giữ thứ tự, mỗi giá trị một lần (quyết định backend lặp lại gây nhiễu)."""
    return list(dict.fromkeys(seq))


def _level_rank(level):
    return {"CHƯA ĐỦ BẰNG CHỨNG": 0, "CÓ KHẢ NĂNG": 1, "CHẮC CHẮN": 2}.get(level, 0)


# Magic của định dạng chắc chắn KHÔNG phải Unity (ảnh/nhạc/font): prefix-probe
# 64 byte mà khớp thì plan bỏ qua luôn, không đọc cả entry (X12).
def _is_plain_media(prefix: bytes) -> bool:
    """64 byte đầu có phải ảnh/nhạc/font chắc chắn không phải Unity không.
    'RIFF' cần kiểm tra thêm WEBP/WAVE ở offset 8; MP4 ('ftyp' ở offset 4)
    và MP3 frame-sync cũng tính. SerializedFile/UnityFS/TAR/ZIP không có
    magic này nên không bao giờ bị bỏ qua nhầm."""
    if prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a",
                          b"GIF89a", b"BM", b"\x00\x00\x01\x00", b"OggS",
                          b"ID3", b"\x00\x01\x00\x00", b"OTTO")):
        return True
    if prefix.startswith(b"RIFF") and prefix[8:12] in (b"WEBP", b"WAVE"):
        return True
    if len(prefix) >= 8 and prefix[4:8] == b"ftyp":
        return True
    if len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xE0) == 0xE0:
        return True
    return False


def plan_bytes(data, *, name="<bytes>", backend=None, limits=None,
               max_entry_bytes=None, on_entry=None):
    """Lập bản đồ dữ liệu cho bytes (container/bundle/assets/file).
    on_entry(done, total, name): callback sau mỗi entry (X5, cho thanh tiến độ;
    None = không báo). max_entry_bytes=None -> limits.plan_max_entry_bytes (X12)."""
    data = bytes(data)
    backend = backend or UnityBackend("AUTO", limits)
    lim = limits or backend.limits
    plan_max = max_entry_bytes if max_entry_bytes is not None else lim.plan_max_entry_bytes
    rows, warnings = [], []
    verdict = EngineVerdict("unknown", "CHƯA ĐỦ BẰNG CHỨNG", [])
    det = detect_bundle(data)
    if det is not None:
        # File bundle ĐƠN LẺ: hard-abort giữ nguyên (v9 -> E_UNSUPPORTED_FORMAT,
        # decision #81); chỉ container mới bền bỉ từng entry.
        bundle = parse_bundle(data, backend, lim, where=name)
        verdict = EngineVerdict("unity", "CHẮC CHẮN",
                                ["UnityFS v%d (%s)" % (bundle.version,
                                                       bundle.unity_revision
                                                       or bundle.unity_version)])
        partial = False
        entries = list(bundle.entries)
        for i, e in enumerate(entries, 1):
            rows.append(PlanRow(name, e.name, "bundle", "bundle-entry", None,
                                e.offset, e.size,
                                "assets" if e.name.lower().endswith((".assets", ".ress"))
                                else "binary", e.name))
            entry_bytes = bundle.get_entry(e.name)
            if e.is_serialized or e.name.lower().endswith(".assets"):
                if not _plan_assets_bytes(entry_bytes, name, e.name, backend, lim,
                                          rows, warnings):
                    partial = True
            if on_entry is not None:
                on_entry(i, len(entries), e.name)
        return UnityPlan(verdict, rows, warnings, _dedupe(backend.decisions),
                         partial=partial)
    # thử container (ZIP/IPA/TAR...) qua Claude 04
    from sdk.containers import open_vfs
    try:
        vfs = open_vfs(data, limits=lim, source_name=name)
    except PB3Error:
        vfs = None
    if vfs is not None:
        try:
            inv = vfs.inventory()
            names = [e.name for e in inv.entries if not e.is_dir]
            verdict = detect_engine(names)
            partial = False
            entries = [e for e in inv.entries if not e.is_dir]
            total = len(entries)
            for i, e in enumerate(entries, 1):
                if e.size > plan_max:
                    rows.append(PlanRow(name, e.name, "file", "entry", None, 0,
                                        e.size, "binary (lớn, chưa đọc)", e.name))
                    warnings.append("entry %s %d byte > giới hạn plan, chưa đọc"
                                    % (e.name, e.size))
                    if on_entry is not None:
                        on_entry(i, total, e.name)
                    continue
                # Prefix-probe (X12): 64 byte đầu mà là ảnh/nhạc/font thì bỏ
                # qua luôn — dòng kết quả giống hệt như khi đọc đủ rồi đệ quy
                # (plan_bytes đệ quy cho dữ liệu lạ chỉ trả 1 dòng "file").
                try:
                    _prefix = vfs.slice(e.name, 0, 64)
                except (PB3Error, AttributeError):
                    _prefix = None
                if _prefix is not None and _is_plain_media(_prefix):
                    vpath = name + "::" + e.name
                    rows.append(PlanRow(vpath, "", "file", "file", None, 0, e.size,
                                        _guess_for(vpath, ""), vpath))
                    if on_entry is not None:
                        on_entry(i, total, e.name)
                    continue
                try:
                    entry_bytes = vfs.read(e.name, None)
                except PB3Error as exc:
                    rows.append(PlanRow(name, e.name, "unsupported", "unsupported",
                                        None, 0, e.size,
                                        "không hỗ trợ (%s)" % exc.code, e.name))
                    warnings.append("không đọc được %s: %s" % (e.name, exc.code))
                    partial = True
                    if on_entry is not None:
                        on_entry(i, total, e.name)
                    continue
                try:
                    sub = plan_bytes(entry_bytes, name=name + "::" + e.name,
                                     backend=backend, limits=lim,
                                     max_entry_bytes=plan_max)
                except PB3Error as exc:
                    # Entry lỗi (vd bundle UnityFS v9) không dừng cả container:
                    # thành dòng "unsupported" + cảnh báo kèm mã/gợi ý (D-POLISH-1).
                    rows.append(PlanRow(name, e.name, "unsupported", "unsupported",
                                        None, 0, e.size,
                                        "không hỗ trợ (%s)" % exc.code, e.name))
                    warnings.append("bỏ qua %s: %s (%s); cần plugin hỗ trợ "
                                    "định dạng này nếu muốn xem tiếp"
                                    % (e.name, exc.code, exc.detail or exc.message_vi))
                    partial = True
                    if on_entry is not None:
                        on_entry(i, total, e.name)
                    continue
                for row in sub.rows:
                    rows.append(row)
                warnings.extend(sub.warnings)
                if sub.partial:
                    partial = True
                if on_entry is not None:
                    on_entry(i, total, e.name)
                if sub.verdict.engine == "unity" and (
                        verdict.engine != "unity"
                        or _level_rank(sub.verdict.level) > _level_rank(verdict.level)):
                    # Nâng verdict theo sub-plan mạnh hơn, nhưng GIỮ lý do của
                    # container (tên entry chứa '.unity3d'...) để báo cáo đủ bằng chứng.
                    verdict = EngineVerdict(
                        "unity", sub.verdict.level,
                        _dedupe(verdict.reasons + sub.verdict.reasons))
            if partial:
                warnings.append("chỉ parse được một phần (E_PARSE_PARTIAL): "
                                "có entry lỗi/bỏ qua, xem các dòng 'unsupported'.")
            return UnityPlan(verdict, rows, warnings, _dedupe(backend.decisions),
                             partial=partial)
        finally:
            close = getattr(vfs, "close", None)
            if close is not None:
                close()
    # thử SerializedFile trực tiếp
    try:
        sf = parse_serialized(data, lim, where=name)
    except PB3Error:
        sf = None
    if sf is not None:
        verdict = EngineVerdict("unity", "CÓ KHẢ NĂNG",
                                ["SerializedFile v%d (%s)" % (sf.version,
                                                              sf.unity_version)])
        _plan_assets_rows(sf, name, "", rows)
        return UnityPlan(verdict, rows, warnings, _dedupe(backend.decisions))
    rows.append(PlanRow(name, "", "file", "file", None, 0, len(data),
                        _guess_for(name, ""), name))
    return UnityPlan(verdict, rows, warnings, _dedupe(backend.decisions))


def _plan_assets_bytes(data, container, entry, backend, lim, rows, warnings):
    """Parse một .assets trong bundle/container; trả False nếu không parse được."""
    try:
        sf = parse_serialized(data, lim, where=entry)
    except PB3Error as exc:
        hint = ""
        if exc.code == "E_UNSUPPORTED_FORMAT":
            hint = "; cần plugin hỗ trợ SerializedFile version này"
        elif exc.code == "E_ENCRYPTED":
            hint = "; PB3 không giải mã dữ liệu mã hóa"
        warnings.append("không parse được %s: %s%s" % (entry, exc.code, hint))
        return False
    _plan_assets_rows(sf, container, entry, rows)
    return True


def _plan_assets_rows(sf, container, entry, rows):
    for o in sf.objects:
        guess = "binary"
        name = ""
        if o.class_id == 49:
            try:
                # chỉ cần tên + độ dài script để đoán dạng: không đọc thân
                name, script_len = read_text_asset_head(
                    sf.object_bytes(o.path_id), sf.endian)
                guess = _guess_for(name, "TextAsset", script_len)
            except PB3Error:
                guess = "text (đọc lỗi)"
        rows.append(PlanRow(container, entry, "assets", class_name(o.class_id),
                            o.path_id, o.byte_start, o.byte_size, guess, name))


# ------------------------------------------------------------------ patcher
@dataclass
class PatchReport:
    target: str
    write_mode: str
    write_mode_used: str
    backend_mode: str
    backend_decisions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    size_before: int = 0
    size_after: int = 0
    old_script_len: int = 0
    new_script_len: int = 0
    occurrences: int = 0


@dataclass
class PatchResult:
    output_bytes: bytes
    unit: str  # "bundle" | "assets"
    write_mode_used: str
    report: PatchReport
    verified: bool
    verify_details: List[str] = field(default_factory=list)


@dataclass
class PatchRequest:
    data: bytes
    kind: str  # "bundle" | "assets"
    entry: Optional[str] = None  # entry trong bundle (None nếu assets độc lập)
    path_id: Optional[int] = None
    object_name: Optional[str] = None
    old_text: str = ""
    new_text: str = ""
    write_mode: str = "PATCH_INPLACE"
    allow_escalate: bool = True
    confirm: bool = False  # bắt buộc cho REBUILD_ALL
    backend: Optional[UnityBackend] = None
    limits: Optional[Limits] = None
    temp_dir: Optional[str] = None  # giữ bản ứng viên khi verify lỗi
    from_container: bool = False  # đơn vị nằm trong container/IPA (cần ký lại gói ngoài)


def _header_size(version):
    return 20 if version < 22 else 48


def _meta_end(sf):
    """Cuối metadata thật (không tính đệm căn 16 tới data_offset)."""
    return _header_size(sf.version) + sf.metadata_size


def _snapshot_assets(sf):
    return {
        # metadata chia 3 vùng: trước object table / object table / sau table.
        # PATCH_INPLACE không được đổi vùng nào; REBUILD_SECTION/ALL chỉ được
        # đổi vùng object table.
        "meta_head": hashing.sha256_bytes(
            sf.raw[_header_size(sf.version):sf.object_table_offset]),
        "meta_table": hashing.sha256_bytes(
            sf.raw[sf.object_table_offset:sf.object_table_end]),
        "meta_tail": hashing.sha256_bytes(
            sf.raw[sf.object_table_end:_meta_end(sf)]),
        "objects": {o.path_id: hashing.sha256_bytes(sf.object_bytes(o.path_id))
                    for o in sf.objects},
        "order": [o.path_id for o in sf.objects],
        "data_offset": sf.data_offset,
    }


def _snapshot_bundle(bundle):
    return {
        "entries": {e.name: hashing.sha256_bytes(bundle.get_entry(e.name))
                    for e in bundle.entries},
        "order": [e.name for e in bundle.entries],
        "flags": [e.flags for e in bundle.entries],
    }


def _verify_assets(before, after_sf, expected_changed, expected_scripts,
                   write_mode_used):
    """Kiểm tra bảo toàn sau khi ghi (đặc tả 6.7). Trả (ok, [chi tiết])."""
    details = []
    ok = True
    after = _snapshot_assets(after_sf)
    changed = {pid for pid in before["objects"]
               if before["objects"].get(pid) != after["objects"].get(pid)}
    changed.update(pid for pid in after["objects"] if pid not in before["objects"])
    if changed != set(expected_changed):
        ok = False
        details.append("tập object thay đổi %s != dự kiến %s"
                       % (sorted(changed), sorted(expected_changed)))
    if before["meta_head"] != after["meta_head"]:
        ok = False
        details.append("metadata trước object table thay đổi")
    if before["meta_tail"] != after["meta_tail"]:
        ok = False
        details.append("metadata sau object table thay đổi")
    if write_mode_used == "PATCH_INPLACE" and before["meta_table"] != after["meta_table"]:
        ok = False
        details.append("PATCH_INPLACE làm đổi object table")
    if before["order"] != after["order"]:
        ok = False
        details.append("thứ tự object thay đổi")
    if write_mode_used == "REBUILD_SECTION" and before["data_offset"] != after["data_offset"]:
        ok = False
        details.append("data_offset đổi %d -> %d"
                       % (before["data_offset"], after["data_offset"]))
    for pid, expect in expected_scripts.items():
        try:
            _name, script = read_text_asset_bytes(after_sf.object_bytes(pid),
                                                  after_sf.endian)
        except PB3Error as exc:
            ok = False
            details.append("object %d đọc lại lỗi: %s" % (pid, exc.code))
            continue
        if script.encode("utf-8") != expect:
            ok = False
            details.append("object %d đọc lại khác giá trị mong muốn" % pid)
    if write_mode_used == "REBUILD_SECTION":
        # Chỉ REBUILD_SECTION mới dựng lại data section nên mới được ép các
        # bất biến của bản dựng lại; PATCH_INPLACE giữ nguyên layout gốc
        # (file thật có thể có byte_start lẻ — vẫn hợp lệ, UnityPy đọc được).
        for o in after_sf.objects:
            if o.byte_start % 4 != 0:
                ok = False
                details.append("object %d: byte_start %d không chia hết cho 4"
                               % (o.path_id, o.byte_start))
    if ok:
        details.append("verify OK: %d object đổi đúng dự kiến, còn lại giữ nguyên hash"
                       % len(changed))
    return ok, details


def _patch_text_asset_inplace(sf_bytes, sf, path_id, old_script_b, new_script_b):
    """PATCH_INPLACE: thay bytes trong file gốc; bắt buộc độ dài không đổi
    (đệm NUL do phía gọi thực hiện và ghi vào report)."""
    if len(new_script_b) != len(old_script_b):
        raise PB3Error("E_WRITE_DISABLED",
                       "PATCH_INPLACE cần độ dài không đổi (%d != %d)"
                       % (len(new_script_b), len(old_script_b)),
                       where=sf.where + "::%d" % path_id)
    o = sf.get_object(path_id)
    obj_start = sf.data_offset + o.byte_start
    obj = sf.raw[obj_start:obj_start + o.byte_size]
    # vị trí script trong object: sau aligned_string(name)
    probe = _Reader(obj, sf.endian, where="TextAsset")
    probe.aligned_string()
    script_pos = probe.pos
    if obj[script_pos:script_pos + 4] != struct.pack(sf.endian + "i", len(old_script_b)):
        raise PB3Error("E_CORRUPT", "không tìm thấy trường script ở vị trí dự kiến",
                       where=sf.where + "::%d" % path_id)
    if obj[script_pos + 4:script_pos + 4 + len(old_script_b)] != old_script_b:
        raise PB3Error("E_CORRUPT", "nội dung script đã đổi so với lúc đọc",
                       where=sf.where + "::%d" % path_id)
    out = bytearray(sf_bytes)
    out[obj_start + script_pos + 4:obj_start + script_pos + 4 + len(new_script_b)] = new_script_b
    return bytes(out)


def _rebuild_assets_section(sf, path_id, new_object_data):
    """REBUILD_SECTION: giữ metadata gốc ngoài object table, dựng lại data."""
    raw = sf.raw
    hs = _header_size(sf.version)
    meta_head = raw[hs:sf.object_table_offset]
    meta_tail = raw[sf.object_table_end:_meta_end(sf)]
    data = _Writer(sf.endian)
    entries = []
    for o in sf.objects:   # giữ NGUYÊN thứ tự object gốc (không sort theo path_id)
        payload = new_object_data.get(o.path_id)
        if payload is None:
            payload = sf.object_bytes(o.path_id)
        data.align(8)
        start = data.tell()
        data.write(payload)
        entries.append((o, start, len(payload)))
    table = _Writer(sf.endian)
    for o, start, size in entries:
        # căn 4 theo vị trí tuyệt đối: đầu table nằm ở object_table_offset
        table.align(4, base=sf.object_table_offset)
        table.pack("q", o.path_id)
        if sf.version >= 22:
            table.pack("Q", start)
        else:
            table.pack("I", start)
        table.pack("I", size)
        table.pack("i", o.type_id)
        if 11 <= sf.version < 17:
            table.pack("h", o.script_type_index if o.script_type_index is not None else -1)
        if sf.version in (15, 16):
            table.write(bytes([o.is_stripped if o.is_stripped is not None else 0]))
    data_bytes = data.bytes()
    header_size = _header_size(sf.version)
    data_offset = _align_up(header_size + len(meta_head) + len(table.bytes())
                            + len(meta_tail), 16)
    file_size = data_offset + len(data_bytes)
    out = _Writer(sf.endian)
    if sf.version < 22:
        out.pack_be("IIII", len(meta_head) + len(table.bytes()) + len(meta_tail),
                    file_size, sf.version, data_offset)
    else:
        out.pack_be("IIII", 0, 0, sf.version, 0)
    out.write(b"\x01" if sf.endian == ">" else b"\x00")
    out.write(b"\x00" * 3)
    if sf.version >= 22:
        # header mở rộng big-endian (như 4 số đầu), không theo endian file.
        out.pack_be("I", len(meta_head) + len(table.bytes()) + len(meta_tail))
        out.pack_be("Q", file_size)
        out.pack_be("Q", data_offset)
        out.pack_be("Q", 0)
    out.write(meta_head)
    out.write(table.bytes())
    out.write(meta_tail)
    out.align16()
    out.write(data_bytes)
    return out.bytes()


def apply_patch(req):
    """Patch một TextAsset; trả PatchResult (đã diffverify).

    Đơn vị đầu ra nhỏ nhất (6.6): assets độc lập -> chính file đó;
    trong bundle -> chỉ bundle đó (KHÔNG đóng gói lại IPA ngoài)."""
    if req.write_mode not in WRITE_MODES:
        raise PB3Error("E_CONFIG_INVALID", "write_mode phải thuộc %s"
                       % "|".join(WRITE_MODES), where=req.entry or req.kind)
    backend = req.backend or UnityBackend("AUTO", req.limits)
    lim = req.limits or backend.limits
    data = bytes(req.data)
    if req.kind == "bundle":
        if not req.entry:
            raise PB3Error("E_CONFIG_INVALID", "patch trong bundle cần tên entry",
                           where=req.kind)
        bundle = parse_bundle(data, backend, lim, where="bundle")
        if bundle.version >= 9:
            # v9 CHỈ ĐỌC (C5): patch luôn dựng lại bundle (kể cả
            # PATCH_INPLACE cũng đóng gói lại container) nên từ chối
            # sớm với lý do rõ ràng thay vì lỗi khó hiểu ở bước ghi.
            raise PB3Error(
                "E_UNSUPPORTED_FORMAT",
                "bundle UnityFS v9 chỉ đọc — chưa hỗ trợ patch (ghi lại "
                "bundle v9 chưa được hỗ trợ)",
                message_vi="Bundle UnityFS v9 chỉ đọc — chưa hỗ trợ patch.",
                where="bundle")
        before_snap = _snapshot_bundle(bundle)
        assets_bytes = bundle.get_entry(req.entry)
        sf = parse_serialized(assets_bytes, lim, where=req.entry)
        unit = "bundle"
    elif req.kind == "assets":
        bundle = None
        before_snap = None
        sf = parse_serialized(data, lim, where="assets")
        assets_bytes = data
        unit = "assets"
    else:
        raise PB3Error("E_CONFIG_INVALID", "kind phải là bundle|assets", where=req.kind)
    if req.path_id is not None:
        pid, name, script = sf.find_text_asset(path_id=req.path_id)
    elif req.object_name is not None:
        pid, name, script = sf.find_text_asset(name=req.object_name)
    else:
        raise PB3Error("E_CONFIG_INVALID", "patch cần path_id hoặc tên TextAsset",
                       where=req.entry or req.kind)
    old_b = req.old_text.encode("utf-8")
    new_b = req.new_text.encode("utf-8")
    script_b = script.encode("utf-8")
    occurrences = script_b.count(old_b) if old_b else 0
    if occurrences == 0:
        raise PB3Error("E_CONFIG_INVALID",
                       "không tìm thấy chuỗi cũ trong TextAsset %r" % name,
                       where="path_id=%d" % pid)
    new_script_b = script_b.replace(old_b, new_b, 1)
    new_obj_data = aligned_string_bytes(name, new_script_b, sf.endian)
    old_obj_data = sf.object_bytes(pid)
    mode = req.write_mode
    pad_note = []
    expected_script = new_script_b
    if mode == "PATCH_INPLACE":
        if len(new_obj_data) > len(old_obj_data):
            if req.allow_escalate:
                mode = "REBUILD_SECTION"
                pad_note.append("chuỗi mới dài hơn: PATCH_INPLACE không chứa được, "
                                "chuyển REBUILD_SECTION (theo 6.5).")
            else:
                raise PB3Error("E_WRITE_DISABLED",
                               "chuỗi mới dài hơn %d byte; PATCH_INPLACE từ chối "
                               "(không cắt cụt). Bật REBUILD_SECTION."
                               % (len(new_obj_data) - len(old_obj_data)),
                               where="path_id=%d" % pid)
        elif len(new_script_b) > len(script_b):
            # #180 (C10): chuỗi mới dài hơn nhưng kích thước object đã căn
            # bằng nhau (vd +1 byte trong cùng khối căn 4): PATCH_INPLACE chỉ
            # ghi đè đúng độ dài nên không chứa được — leo lên REBUILD_SECTION.
            if req.allow_escalate:
                mode = "REBUILD_SECTION"
                pad_note.append("chuỗi mới dài hơn trong cùng khối căn: PATCH_INPLACE "
                                "không chứa được, chuyển REBUILD_SECTION (theo 6.5, #180).")
            # Không cho leo: rơi xuống _patch_text_asset_inplace -> E_WRITE_DISABLED như cũ.
        if mode == "PATCH_INPLACE":
            if len(new_script_b) < len(script_b):
                # đệm NUL trong script để giữ nguyên độ dài object (theo 6.5).
                # So theo độ dài script (không phải object): -1 byte trong cùng
                # khối căn trước đây cũng lỗi E_WRITE_DISABLED (#180 mirror, C10).
                pad = len(script_b) - len(new_script_b)
                new_script_b = new_script_b + b"\x00" * pad
                expected_script = new_script_b
                new_obj_data = aligned_string_bytes(name, new_script_b, sf.endian)
                pad_note.append("chuỗi mới ngắn hơn %d byte: đệm NUL trong script "
                                "(giữ nguyên độ dài object)" % pad)
            new_assets = _patch_text_asset_inplace(assets_bytes, sf, pid,
                                                   script_b, new_script_b)
    if mode == "REBUILD_SECTION":
        new_assets = _rebuild_assets_section(sf, pid, {pid: new_obj_data})
    elif mode == "REBUILD_ALL":
        if UnityBackend.MATURITY < "M4":
            raise PB3Error("E_WRITE_DISABLED",
                           "REBUILD_ALL cần maturity M4 (hiện %s); dùng "
                           "PATCH_INPLACE/REBUILD_SECTION."
                           % UnityBackend.MATURITY, where=req.entry or req.kind)
        if not req.confirm:
            raise PB3Error("E_WRITE_DISABLED",
                           "REBUILD_ALL cần xác nhận người dùng (dry-run + diff).",
                           where=req.entry or req.kind)
        new_assets = build_serialized(sf, {pid: new_obj_data})
    if unit == "bundle":
        new_entries = []
        for e in bundle.entries:
            new_entries.append((e.name, e.flags,
                                new_assets if e.name == req.entry
                                else bundle.get_entry(e.name)))
        # Đường ghi (C5): bundle gốc nén Zstd thì không ghi lại được
        # bằng Zstd (không có encoder) — dựng lại bằng block không nén
        # (cờ nén 0). Bundle LZMA/LZ4/none vẫn giữ compression gốc.
        rebuild_compression = ("none" if bundle.compression_name == "zstd"
                               else bundle.compression_name)
        out_bytes = build_bundle(new_entries, version=bundle.version,
                                 unity_version=bundle.unity_version,
                                 unity_revision=bundle.unity_revision,
                                 compression=rebuild_compression,
                                 blocks_info_at_end=bundle.blocks_info_at_end,
                                 extra_data_flags=bundle.data_flags & FLAG_OLD_WEB,
                                 pad_block_data=bundle.uses_padding,
                                 backend=backend, limits=lim)
    else:
        out_bytes = new_assets
    # ---- diffverify (6.7): parse lại + so sánh
    verify_details = []
    verified = True
    if unit == "assets":
        after_sf = parse_serialized(out_bytes, lim, where="assets::after")
        ok, details = _verify_assets(_snapshot_assets(sf), after_sf, {pid},
                                     {pid: expected_script}, mode)
        verify_details.extend(details)
        verified = ok
    else:
        after_bundle = parse_bundle(out_bytes, backend, lim, where="bundle::after")
        after_assets = after_bundle.get_entry(req.entry)
        after_sf = parse_serialized(after_assets, lim, where="bundle::after::assets")
        ok, details = _verify_assets(_snapshot_assets(sf), after_sf, {pid},
                                     {pid: expected_script}, mode)
        verify_details.extend(details)
        after_snap = _snapshot_bundle(after_bundle)
        changed_entries = {n for n in before_snap["entries"]
                           if before_snap["entries"].get(n) != after_snap["entries"].get(n)}
        changed_entries.update(n for n in after_snap["entries"]
                               if n not in before_snap["entries"])
        if changed_entries != {req.entry}:
            verified = False
            verify_details.append("entry bundle đổi %s != dự kiến {%s}"
                                  % (sorted(changed_entries), req.entry))
        if before_snap["order"] != after_snap["order"]:
            verified = False
            verify_details.append("thứ tự entry bundle thay đổi")
        verified = verified and ok
    if not verified:
        if req.temp_dir:
            import os
            os.makedirs(req.temp_dir, exist_ok=True)
            tmp_path = os.path.join(req.temp_dir, "patch-failed.bin")
            with open(tmp_path, "wb") as fh:
                fh.write(out_bytes)
            verify_details.append("bản ứng viên giữ tại %s" % tmp_path)
        raise PB3Error("E_VERIFY_FAILED",
                       "xác minh sau khi ghi không đạt: %s" % "; ".join(verify_details),
                       where="path_id=%d" % pid)
    if req.from_container and unit == "bundle":
        pad_note.append("đơn vị là bundle trong container/IPA: sau khi thay file "
                        "cần ký lại (re-sign) gói ngoài thì game mới nhận "
                        "(đặc tả 11.1); PB3 không tự ký lại.")
    report = PatchReport(
        target="%s%s path_id=%d (%s)" % (unit, ("::" + req.entry) if req.entry else "",
                                        pid, name),
        write_mode=req.write_mode, write_mode_used=mode,
        backend_mode=backend.mode,
        backend_decisions=_dedupe(backend.decisions),
        warnings=pad_note,
        changed=[req.entry] if unit == "bundle" else ["path_id=%d" % pid],
        size_before=len(data), size_after=len(out_bytes),
        old_script_len=len(script_b), new_script_len=len(new_script_b),
        occurrences=occurrences)
    return PatchResult(output_bytes=out_bytes, unit=unit, write_mode_used=mode,
                       report=report, verified=True, verify_details=verify_details)


def aligned_string_bytes(name, script_b, endian="<"):
    """Dựng lại bytes của một TextAsset từ tên + script bytes."""
    w = _Writer(endian)
    w.aligned_string(name)
    raw = bytes(script_b)
    w.pack("i", len(raw))
    w.write(raw)
    w.align(4)
    return w.bytes()


# ------------------------------------------------- output / backup / rollback
def write_patch_output(output_root, rel, data, *, policy="version"):
    """Ghi output patch: không ghi đè (version .v1/.v2...), trả đường dẫn cuối."""
    from core import safe_io
    return safe_io.write_output_bytes(output_root, rel, data, policy=policy)


def backup_source_file(src_path, backup_dir=None):
    """Sao lưu file nguồn trước khi patch (bản gốc không bao giờ bị sửa)."""
    from core import safe_io
    return safe_io.backup_file(src_path, backup_dir)


def rollback_output(path):
    """Xóa file output vừa ghi (khôi phục trạng thái trước patch)."""
    import os
    try:
        os.unlink(str(path))
        return True
    except OSError:
        return False
