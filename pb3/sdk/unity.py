"""Backend Unity (Claude 05; đặc tả §6, decisions #66+).

Phạm vi: UnityFS v6/v7/v8/v9 (đọc + ghi; Zstd chỉ đọc
trên web qua fzstd — xem C5), SerializedFile v15–26 (metadata, object
table, TextAsset, chuỗi ngắn/dài), hybrid backend AUTO / LIBRARY /
NATIVE (UnityPy + LZ4 + Python thuần), planner, patcher
(PATCH_INPLACE / REBUILD_SECTION / REBUILD_ALL), diffverify +
allow-list + backup/versioning/rollback.

Layout đối chiếu UnityPy master (BundleFile.py, SerializedFile.py,
ObjectReader.py, TypeTreeNode.py, CompressionHelper.py) — xem thêm ghi chú
v24+ ở SerializedFile (SUPPORTED_SF_VERSIONS):
- UnityFS: big-endian; header = signature, version, unity version,
  unity revision, total size u64, comp/uncomp blocks-info size, flags;
  blocks info = hash 16 byte + số block + (unc, comp, flags u16) +
  số entry + (offset u64, size u64, flags u32, tên).
- SerializedFile: 4 số đầu header LUÔN big-endian; phần sau theo cờ
  endian; object table v15/16 có script_type_index + is_stripped,
  v17+ không; byte_start u64 chỉ từ v22; type tree v23+ có content hash
  16 byte + kích thước blob, blob mở đầu bằng magic "mhtt" + format
  version (size 0 = type đó không có type tree).
- SerializedFile v24+ (đối chiếu UnityPy master, commit af1503d —
  AssetStudio/AssetRipper/UABE đều dừng ở v22, không có spec công khai):
  v24/v25 KHÔNG đổi layout so với v23 (UnityPy không có nhánh 24/25 nào:
  không đổi header, type, object table, script, external, ref type);
  v26 thêm MỘT mục sau ref types: i32 số shared subtree + từng
  TypeTreeNodeInfoHandler (hash 16B + u32 size + info "mhtt" như type
  thường); node có m_TypeFlags & 32 thì children lấy từ subtree chung
  theo ref_hashes[m_RefTypeHash] (xem _resolve_shared_subtrees).
  TypeTreeNodeInfo v23+ (UnityPy TypeTreeNodeInfo.from_reader): sau node
  blob LUÔN có u32 ref_hash_count + ref_hashes 16 byte (kể cả count = 0);
  fixture v23 cũ của repo thiếu phần đuôi này nên parser chấp nhận cả 2
  dạng (phân biệt bằng serialized_size, kiểm tra chặt) — xem
  _parse_typetree_blob. v27+ chưa có bằng chứng layout -> fail-closed.
- UnityFS v8 cùng layout header với v7 (căn 16 sau header); cờ mã hóa /
  padding chọn theo unityRevision (UnityPy: version_engine), KHÔNG theo
  unityVersion (thường là "5.x.x").
- UnityFS v9 (C5): cùng header với v8; khác biệt DUY NHẤT theo
  UnityPy master (helpers/CompressionHelper.py, đối chiếu 2026-11-16):
  mỗi BlockInfo thêm u64 `offset` sau cờ u16, và mỗi block nén được căn
  16 byte trong vùng block data (đọc block bằng offset, không đọc tuần
  tự). Header vẫn căn 16 khi version >= 7; entry vẫn (offset u64, size
  u64, flags u32, tên) — file lớn đã có từ trước nhờ các trường u64.
  Ghi v9 (M-formats, 2026-11-17): build_bundle / rebuild_bundle_streaming
  ghi offset u64 + căn 16 từng block; patch dựng lại bundle v9 như v7/v8.
- Zstd (C5, chỉ đọc trên web): mã nén 5 — mã trống tiếp theo sau
  LZHAM=4. UnityPy master (2026-11-16) và tài liệu Unity (chỉ liệt kê
  LZMA/LZ4/Uncompressed) KHÔNG có cờ Zstd; mã 5 là quy ước mở rộng của
  PB3 để đón bundle dùng Zstd, chưa có bằng chứng bundle Unity thật nào
  dùng mã này — fixture C5 là tổng hợp. Giải nén bằng fzstd (JS thuần,
  vendored ở site/fzstd/) qua cầu nối `js.pb3_zstd_decompress` do
  worker.js công bố (tiền lệ: `js.pb3_google_translate`); ngoài trình
  duyệt thì báo lỗi rõ ràng thay vì crash. Ghi Zstd KHÔNG hỗ trợ:
  dựng lại bundle dùng block không nén (cờ nén 0).
  Đánh giá F3 (22/11) [ĐÃ ĐO THẬT]: khảo sát 1.904 bundle UnityFS thật
  (EK2 100 + 9K 7 + Tom 1 + PB3 297 + Knightica 15 + Kusa 1.484) — TẤT CẢ
  dùng nén LZ4HC (mã 3), 0 bundle dùng mã 5; fzstd vendored CHỈ export
  `decompress`/`Decompress` (không có encoder — kiểm chứng bằng import
  trong Node: `compress` không tồn tại) nên cài ghi Zstd cần vendor thêm
  encoder + đường cầu nối JS riêng, trong khi chưa có bundle Zstd thật
  nào để kiểm chứng — KHÔNG cài; giữ ghi lại bằng block không nén.
  Build_bundle đã giữ nén gốc LZMA/LZ4/LZ4HC (mọi mã nén gặp trong dữ
  liệu thật); chỉ Zstd rơi về không nén.
- Mã hóa bundle (F3): bit 0x200 là HAI nghĩa — cờ mã hóa (cờ CŨ, Unity
  < 2020.3.34/2021.3.2/2022.1.1) hoặc cờ padding đầu vùng block data
  (cờ MỚI); chọn theo unityRevision. Revision "0.0.0" (toàn 0) coi như
  không xác định -> cờ mới (không có bản Unity thật nào mang 0.0.0).
  EK2 v0.0.18 [ĐÃ ĐO THẬT 22/11]: 100/100 bundle flag 0x243, revision
  "0.0.0" — blocks-info giải LZ4HC bình thường (block_count 452/368...),
  parse toàn bộ được 197 entry, payload 1.125.039.389 B, 105 file SF
  trong payload TẤT CẢ v22 — KHÔNG hề mã hóa, chỉ bị nhận nhầm trước
  đây vì (0, 0, 0) < 2020. `classify_bundle_encryption(data)` phân loại
  theo dữ liệu thật: none / padding_flag / xor_single_byte (magic hoặc
  blocks-info XOR 1 byte — chỉ chẩn đoán, chưa cài giải mã vì chưa có
  mẫu thật) / header_obscured / encrypted (blocks-info không giải được
  plain + XOR 1 byte — mã hóa thật, giữ E_ENCRYPTED) / unparseable /
  not_a_bundle. Lỗi E_ENCRYPTED nay ghi rõ flag + revision + bộ cờ áp
  dụng; magic bị XOR 1 byte thì báo E_ENCRYPTED kèm key thay vì
  E_UNSUPPORTED_FORMAT chung chung.
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

import base64
import copy
import hashlib
import importlib
import io
import json
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
from sdk import text_data
from sdk.manifest import WRITE_MODES

UNITYFS_MAGIC = b"UnityFS"
UNITY_VERSION = "2019.4.40f1"  # mặc định khi dựng bundle mới (giữ nguyên khi rebuild)
UNITY6_PLAYER_VERSION = "5.x.x"     # unityVersion trong header bundle Unity 6
UNITY6_REVISION = "6000.0.23f1"     # unityRevision (engine) của fixture v8/v23
SUPPORTED_BUNDLE_VERSIONS = (6, 7, 8, 9)  # v9: đọc + ghi (M-formats)
# SerializedFile: 15-26 (object table đã kiểm chứng; <15 quá cũ, >26 chưa
# có bằng chứng layout — fail-closed). v24/25 layout = v23, v26 thêm mục
# shared subtrees sau ref types (đối chiếu UnityPy master af1503d).
SUPPORTED_SF_VERSIONS = (15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26)

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
        {"format": "UnityFS v9 (bundle)", "read": "có (BlockInfo có offset u64, block căn 16)",
         "write": "có (dựng lại bundle, giữ compression gốc; offset u64 + căn 16)",
         "backend": "Python thuần", "maturity": "M2"},
        {"format": "UnityFS v8 cờ 0x200 + revision 0.0.0 (EK2)",
         "read": "có — bit 0x200 là cờ padding (cờ mới), KHÔNG phải mã hoá "
                 "(100/100 bundle EK2 v0.0.18 parse được [ĐÃ ĐO THẬT 22/11])",
         "write": "có (giữ nén LZ4HC gốc; cờ padding đặt lại theo layout ghi)",
         "backend": "Python thuần", "maturity": "M2"},
        {"format": "Zstd (block bundle, mã nén 5)", "read": "có — CHỈ trên web BearTea (qua fzstd, cầu nối JS)",
         "write": "không (ghi lại bằng block không nén, cờ 0) — đánh giá F3 "
                  "22/11: 0/1904 bundle thật dùng mã 5; fzstd vendored CHỈ "
                  "giải nén, không có encoder nên chưa cài ghi Zstd",
         "backend": "fzstd 0.1.1 (MIT, vendored site/fzstd/)", "maturity": "M2"},
        {"format": "SerializedFile v15-26 (.assets)", "read": "có (metadata, object table, TextAsset; v23+ type tree có ref hashes, v26 shared subtrees)",
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
        {"format": "SerializedFile v<15 / v>26, UnityWeb/UnityRaw, LZHAM, bundle mã hóa thật",
         "read": "không",
         "write": "không",
         "backend": "chưa có (E_UNSUPPORTED_FORMAT / E_ENCRYPTED — xem "
                    "classify_bundle_encryption để phân loại: padding_flag / "
                    "xor_single_byte / header_obscured / encrypted)", "maturity": "M0"},
    ]


# ------------------------------------------------------------------ reader
_READER_STRUCT_CACHE: Dict[Tuple[str, str], "struct.Struct"] = {}


def _reader_struct(endian: str, fmt: str) -> "struct.Struct":
    """struct.Struct biên dịch sẵn cho (endian, fmt) — xem _Reader.unpack."""
    key = (endian, fmt)
    st = _READER_STRUCT_CACHE.get(key)
    if st is None:
        st = struct.Struct(endian + fmt)
        _READER_STRUCT_CACHE[key] = st
    return st


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
        # SPEED-EXTRACT (2026-11-18): struct Struct biên dịch sẵn theo
        # (endian, fmt) thay vì struct.calcsize(fmt) + nối chuỗi endian +
        # struct.unpack_from MỖI lần gọi (9K: 108.868 lần unpack, 0,22 s
        # tottime [ĐÃ ĐO THẬT] bằng profile). Ngữ nghĩa giữ nguyên:
        # Struct(endian+fmt).size == calcsize(fmt) với mọi fmt đang dùng
        # (chỉ kiểu số nguyên, không có đệm căn khác nhau giữa native và
        # standard), unpack_from đọc đúng như cũ.
        st = _reader_struct(self.endian, fmt)
        self._need(st.size)
        out = st.unpack_from(self._b, self.pos)
        self.pos += st.size
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
    Không parse được -> True (mặc định cờ mới, đúng với bundle hiện đại).
    Revision "0.0.0" (toàn 0) cũng coi như không xác định -> True: không
    có bản Unity thật nào mang số 0.0.0 — đó là trường bị xóa/scrubbed
    (EK2 v0.0.18: cả 100 bundle ghi revision "0.0.0" nhưng blocks-info
    giải LZ4HC bình thường, bit 0x200 là cờ padding — xem F3
    classify_bundle_encryption; trước fix này EK2 bị nhận nhầm là mã hóa
    vì (0, 0, 0) < 2020)."""
    v = parse_unity_version(engine_version)
    if not v:
        return True
    if v == (0, 0, 0):
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
        # Cầu nối JS lz4js (chỉ có trên web): thử 1 lần, nếu lỗi không
        # phải do input hỏng thì tắt cho backend này (rơi về lz4pure).
        self._lz4_js_broken = False
        # Cầu nối Rust/WASM (chỉ có trên web): thử 1 lần, nếu lỗi không
        # phải do input hỏng thì tắt cho backend này (rơi về lz4js/lz4pure).
        self._lz4_rs_broken = False
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

    def _decompress_lz4_js(self, data, uncompressed_size, where="unity"):
        """Giải nén 1 block LZ4 qua lz4js bên JS (tốc độ, mục C1).

        Cầu nối theo đúng tiền lệ Zstd/fzstd: worker.js công bố
        `globalThis.pb3_lz4_decompress(data, size)` (hàm JS đồng bộ,
        trả Uint8Array); Python gọi qua module `js`. Chỉ có trên web
        (CPython/iSH: không có module `js`, hoặc worker chưa nạp được
        lz4js) thì trả None để bên gọi rơi về binding/lz4pure — không
        raise, không crash. Lỗi input hỏng (JS ném LZ4_CORRUPT) thì
        raise E_CORRUPT như lz4pure; lỗi JS khác thì đánh dấu cầu nối
        hỏng cho backend này rồi trả None (bên gọi dùng lz4pure).
        Pyodide chuyển bytes Python -> Uint8Array cho JS, và trả
        Uint8Array về dưới dạng JsProxy (có buffer protocol nên
        bytes(...) dùng được; dự phòng to_py())."""
        if self._lz4_js_broken:
            return None
        try:
            import js as _js
            _lz4 = getattr(_js, "pb3_lz4_decompress", None)
        except Exception:
            _lz4 = None
        if _lz4 is None:
            return None
        try:
            out = _lz4(bytes(data), int(uncompressed_size))
        except PB3Error:
            raise
        except Exception as exc:  # noqa: BLE001 - lỗi JS -> fallback hoặc E_CORRUPT
            if "LZ4_CORRUPT" in str(exc):
                detail = str(exc).split("LZ4_CORRUPT", 1)[1].lstrip(": ").strip()
                raise PB3Error("E_CORRUPT", "LZ4 (JS): %s" % (detail or "block hỏng"),
                               where=where)
            self._lz4_js_broken = True
            return None
        try:
            raw = bytes(out)
        except Exception:
            to_py = getattr(out, "to_py", None)
            if not callable(to_py):
                raise PB3Error("E_CORRUPT",
                               "pb3_lz4_decompress trả về kiểu không đọc được: %s"
                               % type(out).__name__, where=where)
            raw = bytes(to_py())
        if len(raw) != uncompressed_size:
            raise PB3Error("E_CORRUPT", "LZ4 (JS) giải nén được %d/%d byte"
                           % (len(raw), uncompressed_size), where=where)
        return raw

    def _decompress_lz4_rs(self, data, uncompressed_size, where="unity"):
        """Giải nén 1 block LZ4 qua Rust/WASM (cầu nối pb3_rust_lz4_decompress).

        Nhanh hơn lz4js (mã native qua WASM) và lz4pure (hàng chục lần).
        Theo đúng mẫu _decompress_lz4_js: vắng cầu nối thì trả None để bên
        gọi rơi về lz4js/lz4pure — không raise, không crash. Lỗi input hỏng
        (WASM ném RUST_LZ4_CORRUPT) thì raise E_CORRUPT như lz4pure; lỗi JS
        khác thì đánh dấu cầu nối hỏng cho backend này rồi trả None.
        """
        if self._lz4_rs_broken:
            return None
        try:
            import js as _js
            _rs = getattr(_js, "pb3_rust_lz4_decompress", None)
        except Exception:
            _rs = None
        if _rs is None:
            return None
        try:
            out = _rs(bytes(data), int(uncompressed_size))
        except PB3Error:
            raise
        except Exception as exc:  # noqa: BLE001 - lỗi JS -> fallback hoặc E_CORRUPT
            if "RUST_LZ4_CORRUPT" in str(exc):
                detail = str(exc).split("RUST_LZ4_CORRUPT", 1)[1].lstrip(": ").strip()
                raise PB3Error("E_CORRUPT", "LZ4 (Rust): %s" % (detail or "block hỏng"),
                               where=where)
            self._lz4_rs_broken = True
            return None
        try:
            raw = bytes(out)
        except Exception:
            to_py = getattr(out, "to_py", None)
            if not callable(to_py):
                raise PB3Error("E_CORRUPT",
                               "pb3_rust_lz4_decompress trả về kiểu không đọc được: %s"
                               % type(out).__name__, where=where)
            raw = bytes(to_py())
        if len(raw) != uncompressed_size:
            raise PB3Error("E_CORRUPT", "LZ4 (Rust) giải nén được %d/%d byte"
                           % (len(raw), uncompressed_size), where=where)
        return raw

    def _decompress_frame_rs(self, data, where="lz4-frame"):
        """Giải nén 1 LZ4 frame qua Rust/WASM (pb3_rust_lz4_decompress_frame).

        Mẫu như _decompress_lz4_rs: vắng cầu nối thì trả None (bên gọi rơi
        về lz4pure); input hỏng (RUST_LZ4_CORRUPT) thì raise E_CORRUPT.
        """
        if self._lz4_rs_broken:
            return None
        try:
            import js as _js
            _rs = getattr(_js, "pb3_rust_lz4_decompress_frame", None)
        except Exception:
            _rs = None
        if _rs is None:
            return None
        try:
            out = _rs(bytes(data))
        except PB3Error:
            raise
        except Exception as exc:  # noqa: BLE001
            if "RUST_LZ4_CORRUPT" in str(exc):
                detail = str(exc).split("RUST_LZ4_CORRUPT", 1)[1].lstrip(": ").strip()
                raise PB3Error("E_CORRUPT",
                               "LZ4 frame (Rust): %s" % (detail or "frame hỏng"),
                               where=where)
            self._lz4_rs_broken = True
            return None
        try:
            return bytes(out)
        except Exception:
            to_py = getattr(out, "to_py", None)
            if not callable(to_py):
                raise PB3Error("E_CORRUPT",
                               "pb3_rust_lz4_decompress_frame trả về kiểu không đọc được: %s"
                               % type(out).__name__, where=where)
            return bytes(to_py())

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
            # Web (mọi mode, kể cả NATIVE): thử Rust/WASM trước lz4js —
            # mã native nhanh hơn JS; vắng cầu nối thì trả None và rơi về
            # lz4js rồi lz4pure như trước, không đổi hành vi.
            rs_out = self._decompress_lz4_rs(data, uncompressed_size, where=where)
            if rs_out is not None:
                return rs_out
            js_out = self._decompress_lz4_js(data, uncompressed_size, where=where)
            if js_out is not None:
                return js_out
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
        """Giải nén LZ4 frame: binding khi có, không thì Rust/WASM rồi Python thuần."""
        if self.mode != "NATIVE" and self.has_lz4_binding:
            lz4_frame = importlib.import_module("lz4.frame")
            try:
                return lz4_frame.decompress(bytes(data))
            except Exception as exc:  # noqa: BLE001
                raise PB3Error("E_CORRUPT", "lz4.frame lỗi: %s" % type(exc).__name__,
                               where=where)
        rs_out = self._decompress_frame_rs(data, where=where)
        if rs_out is not None:
            return rs_out
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


def _xor_magic_key(data):
    """Key XOR 1 byte khoi phuc duoc magic "UnityFS", hoac None.

    Chi thu 7 byte dau (magic), re va khong giai ma gi them."""
    if len(data) < 7:
        return None
    for key in range(1, 256):
        if bytes(b ^ key for b in bytes(data[:7])) == UNITYFS_MAGIC:
            return key
    return None


def _blocks_info_bytes(data, version, unity_revision, data_flags,
                       comp_info_size, uncomp_info_size, header_end):
    """Lay khoi blocks-info (dang nen) tu bundle, hoac None neu vi tri
    khong hop le. Chi cat bytes, khong giai nen (dung chung cho
    classify_bundle_encryption)."""
    if version >= 7 or parse_unity_version(unity_revision) >= (2019, 4, 15):
        start = _align_up(header_end, 16)
    else:
        start = header_end
    if comp_info_size > len(data) or uncomp_info_size < 0:
        return None
    if data_flags & FLAG_BLOCKS_INFO_AT_END:
        info_start = len(data) - comp_info_size
        if info_start < start:
            return None
        return data[info_start:]
    info = data[start:start + comp_info_size]
    if len(info) != comp_info_size:
        return None
    return info


def _try_decompress_blocks_info(info_comp, uncomp_size, compression, where):
    """Thu giai nen blocks-info; tra bytes neu giai duoc VA co dang
    blocks-info hop le (hash 16 byte + block_count 0..100000), khong thi
    None. Chi la probe chan doan (backend NATIVE: khong import UnityPy)."""
    if info_comp is None or uncomp_size < 20:
        return None
    try:
        backend = UnityBackend("NATIVE")
        raw = backend.decompress(info_comp, uncomp_size, compression,
                                 where=where)
    except Exception:  # noqa: BLE001 - probe that bai la ket qua, khong phai loi
        return None
    if len(raw) < 20:
        return None
    block_count = struct.unpack(">i", raw[16:20])[0]
    if block_count < 0 or block_count > 100000:
        return None
    return raw


def classify_bundle_encryption(data):
    """Phan loai bundle ma hoa theo DU LIEU THAT, khong theo suy doan (F3).

    Tra dict {"kind", "detail", "flags", "unity_revision", "xor_key"}.
    kind:
      "none" — khong co bit ma hoa nao bat theo cach doc co ap dung duoc.
      "padding_flag" — bit 0x200 chi la co padding (co moi), KHONG phai ma
        hoa: revision 0.0.0/khong xac dinh + parse_bundle doc duoc toan bo.
        (EK2 v0.0.18: 100/100 bundle kieu nay [DA DO THAT].)
      "xor_single_byte" — magic "UnityFS" bi XOR 1 byte (khoi phuc duoc
        bang xor_key), hoac blocks-info XOR 1 byte thi giai duoc. Chi la
        chan doan: CHUA cai giai ma vi chua co mau that de kiem chung
        toan bo block data — giu E_ENCRYPTED kem key de buoc sau cai dat.
      "header_obscured" — magic sai va khong co key XOR 1 byte nao khoi
        phuc duoc "UnityFS": header bi che dang khac hoac khong phai
        bundle Unity.
      "encrypted" — bit ma hoa bat va blocks-info khong giai duoc (thu
        plain + XOR 1 byte deu that bai): ma hoa that, chua co bo giai
        ma; giu E_ENCRYPTED kem giai thich cu the.
      "unparseable" — header doc duoc nhung phan loai khong ket luan duoc
        (version chua ho tro / parse that bai vi ly do khac ma hoa).
      "not_a_bundle" — qua ngan hoac header cat cut.
    Ham nay CHI chan doan: khong giai ma, khong ghi gi, khong doi du lieu.
    """
    data = bytes(data)
    result = {"kind": "not_a_bundle", "detail": "", "flags": None,
              "unity_revision": None, "xor_key": None}
    if len(data) < 7:
        result.update({
            "kind": "not_a_bundle",
            "detail": "file qua ngan (< 7 byte), khong phai bundle Unity."})
        return result
    if bytes(data[:7]) != UNITYFS_MAGIC:
        key = _xor_magic_key(data)
        if key is not None:
            result.update({
                "kind": "xor_single_byte", "xor_key": key,
                "detail": ("magic 'UnityFS' bi XOR 1 byte (key 0x%02x) — "
                           "co the la bundle XOR don gian; chua cai giai ma "
                           "vi chua co mau that de kiem chung toan bo block "
                           "data." % key)})
            return result
        result.update({
            "kind": "header_obscured",
            "detail": ("khong phai UnityFS (magic sai) va khong co key XOR "
                       "1 byte nao khoi phuc duoc magic — header bi che "
                       "dang khac hoac khong phai bundle Unity.")})
        return result
    try:
        r = _Reader(data, ">", where="bundle")
        r.read(8)
        version = r.u32()
        player = r.string_to_null()
        engine = r.string_to_null()
        total_size, comp_info_size, uncomp_info_size, data_flags = r.unpack("QIII")
        header_end = r.pos
    except PB3Error:
        result.update({
            "kind": "not_a_bundle",
            "detail": "magic UnityFS dung nhung header doc khong duoc (cat cut)."})
        return result
    result["flags"] = data_flags
    result["unity_revision"] = engine
    if version not in SUPPORTED_BUNDLE_VERSIONS:
        result.update({
            "kind": "unparseable",
            "detail": ("UnityFS version %d chua ho tro (chi %s) — khong phan "
                       "loai ma hoa duoc." %
                       (version, "/".join(map(str, SUPPORTED_BUNDLE_VERSIONS))))})
        return result
    new_flags = _uses_new_flags(engine)
    encryption_flag = FLAG_ENCRYPTION_NEW if new_flags else FLAG_ENCRYPTION_OLD
    compression = data_flags & 0x3F
    if not (data_flags & encryption_flag) and not (data_flags & 0x200):
        result.update({"kind": "none",
                       "detail": "khong co bit ma hoa nao bat."})
        return result
    if not (data_flags & encryption_flag):
        # Chi bit 0x200 bat ma theo co moi do la padding -> thu parse that.
        try:
            bundle = parse_bundle(data, where="bundle")
        except PB3Error as exc:
            result.update({
                "kind": "unparseable",
                "detail": ("bit 0x200 chi la co padding (co moi) nhung parse "
                           "that bai (%s) — khong ket luan duoc, giu "
                           "fail-closed." % exc.code)})
            return result
        result.update({
            "kind": "padding_flag",
            "detail": ("bit 0x200 la co padding (co moi), KHONG phai ma hoa: "
                       "revision %r, parse duoc %d entry, %d block." %
                       (engine, len(bundle.entries), len(bundle.blocks)))})
        return result
    # Bit ma hoa bat theo cach doc co ap dung duoc: co la tham quyen
    # (khop parse_bundle + loader Unity + test hien co) — chi thu xem
    # blocks-info co phai dang XOR 1 byte khong; khong thu "giai plain"
    # o day vi voi nen 0 thi plain luon "giai duoc" nen khong chung minh
    # duoc gi.
    info_comp = _blocks_info_bytes(data, version, engine, data_flags,
                                   comp_info_size, uncomp_info_size, header_end)
    if info_comp is not None and len(info_comp) <= 1024 * 1024:
        for key in range(1, 256):
            dec = bytes(b ^ key for b in info_comp)
            if _try_decompress_blocks_info(dec, uncomp_info_size, compression,
                                           "bundle::blocks-info") is not None:
                result.update({
                    "kind": "xor_single_byte", "xor_key": key,
                    "detail": ("blocks-info bi XOR 1 byte (key 0x%02x): giai "
                               "duoc sau khi XOR — dang XOR don gian; chua cai "
                               "giai ma vi chua co mau that de kiem chung "
                               "toan bo block data." % key)})
                return result
    result.update({
        "kind": "encrypted",
        "detail": ("ma hoa that: bit ma hoa 0x%x bat (revision %r, co %s), "
                   "blocks-info khong giai duoc (thu plain + XOR 1 byte deu "
                   "that bai) — can khoa/giai ma cua nha phat hanh; MeoRocky "
                   "khong giai ma, khong ghi de file goc." %
                   (encryption_flag, engine, "moi" if new_flags else "cu"))})
    return result


def parse_bundle(data, backend=None, limits=None, where="bundle"):
    """Đọc UnityFS v6/v7/v8/v9: header, blocks info, giải nén từng block, kiểm size.

    v8 cùng layout với v7 (UnityPy không kiểm version, chỉ căn 16 khi
    version >= 7); cờ mã hóa/padding chọn theo unityRevision (#78).
    v9 (C5): mỗi BlockInfo thêm u64 `offset` sau cờ u16; block
    data đọc bằng offset (tính từ đầu vùng block data đã căn 16), vì các
    block nén được căn 16 nên không đọc tuần tự được nữa. Các trường
    total size / entry offset / entry size vốn đã là u64 nên file lớn
    không cần đổi gì thêm. Ghi v9 xem build_bundle (offset u64 + căn 16
    từng block)."""

    data = bytes(data)
    backend = backend or UnityBackend("AUTO", limits)
    lim = limits or backend.limits
    if len(data) < 8 or data[:7] != UNITYFS_MAGIC:
        key = _xor_magic_key(data)
        if key is not None:
            raise PB3Error(
                "E_ENCRYPTED",
                "bundle co the bi XOR 1 byte (magic 'UnityFS' khoi phuc duoc "
                "bang key 0x%02x) — chua cai giai ma, xem "
                "classify_bundle_encryption" % key,
                message_vi=("Bundle có thể bị XOR 1 byte (key 0x%02x) — chưa "
                            "cài giải mã, cần mẫu thật để kiểm chứng." % key),
                where=where)
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
        raise PB3Error(
            "E_ENCRYPTED",
            "bundle mã hóa (flag 0x%x, revision %r, cờ %s)"
            % (data_flags, unity_revision, "mới" if new_flags else "cũ"),
            message_vi=("Bundle mã hoá (cờ 0x%x, Unity %s) — cần khoá giải mã "
                        "của nhà phát hành; MeoRocky không giải mã và không "
                        "ghi đè file gốc." % (data_flags, unity_revision)),
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
        key = _xor_magic_key(head)
        if key is not None:
            raise PB3Error(
                "E_ENCRYPTED",
                "bundle co the bi XOR 1 byte (magic 'UnityFS' khoi phuc duoc "
                "bang key 0x%02x) — chua cai giai ma, xem "
                "classify_bundle_encryption" % key,
                message_vi=("Bundle có thể bị XOR 1 byte (key 0x%02x) — chưa "
                            "cài giải mã, cần mẫu thật để kiểm chứng." % key),
                where=where)
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
        raise PB3Error(
            "E_ENCRYPTED",
            "bundle mã hóa (flag 0x%x, revision %r, cờ %s)"
            % (data_flags, unity_revision, "mới" if new_flags else "cũ"),
            message_vi=("Bundle mã hoá (cờ 0x%x, Unity %s) — cần khoá giải mã "
                        "của nhà phát hành; MeoRocky không giải mã và không "
                        "ghi đè file gốc." % (data_flags, unity_revision)),
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
    Ghi v9 (M-formats): mỗi BlockInfo thêm u64 `offset` sau cờ u16, mỗi
    block trong vùng block data được căn 16 (đệm NUL sau từng block) —
    port từ build_fixtures.build_bundle (đối chiếu UnityPy
    chunk_based_compress). compression "zstd" vẫn từ chối (không có
    encoder) — bên gọi (apply_patch) dựng lại bằng compression "none"
    (block không nén, cờ nén 0)."""
    backend = backend or UnityBackend("AUTO", limits)
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
        off = block_data.tell()
        comp = backend.compress(chunk, comp_code)
        if compression != "none" and len(comp) >= len(chunk):
            block_data.write(chunk)
            blocks.append(BundleBlock(len(chunk), len(chunk), 0, off))
        else:
            block_data.write(comp)
            flag = 64 if compression == "none" else comp_code
            blocks.append(BundleBlock(len(chunk), len(comp), flag, off))
        if version >= 9:
            # v9: căn từng block tới 16 trong vùng block data (đệm NUL).
            block_data.write(b"\x00" * ((-block_data.tell()) % 16))
    block_bytes = block_data.getvalue()
    info = _Writer(">")
    info.write(b"\x00" * 16)
    info.pack("i", len(blocks))
    for b in blocks:
        info.pack("IIH", b.uncompressed_size, b.compressed_size, b.flags)
        if version >= 9:
            info.pack("Q", b.offset if b.offset is not None else 0)
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
    (hướng iSH); bundle mã hóa / mã nén 4,>5 -> từ chối sớm ở
    pass 1, không ghi gì. v9 ghi được (M-formats): mỗi BlockInfo thêm
    u64 offset, mỗi block trong vùng block data được căn 16 (đệm NUL),
    như build_bundle. Verify ở đây YẾU HƠN diffverify toàn phần
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
            def _pad_block():
                # v9: căn từng block tới 16 trong vùng block data (đệm NUL).
                if layout.version >= 9:
                    pad = (-bf.tell()) % 16
                    if pad:
                        bf.write(b"\x00" * pad)

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
                blocks.append(BundleBlock(new_payload, written, comp_code, 0))
                _pad_block()
                _prog(old_payload + new_payload)
            elif write_compression in ("lz4", "lz4hc"):
                buf = bytearray()

                def _flush_chunk(chunk):
                    comp = backend.compress(bytes(chunk), comp_code)
                    off = bf.tell()
                    if len(comp) >= len(chunk):
                        bf.write(bytes(chunk))
                        blocks.append(BundleBlock(len(chunk), len(chunk), 0, off))
                    else:
                        bf.write(comp)
                        blocks.append(BundleBlock(len(chunk), len(comp),
                                                  comp_code, off))
                    _pad_block()

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
                        off = bf.tell()
                        bf.write(bytes(buf[:step]))
                        blocks.append(BundleBlock(step, step, 64, off))
                        _pad_block()
                        del buf[:step]
                    _prog(old_payload + consumed)
                if buf or not blocks:
                    off = bf.tell()
                    bf.write(bytes(buf))
                    blocks.append(BundleBlock(len(buf), len(buf), 64, off))
                    _pad_block()
                _prog(old_payload + new_payload)
        # ---- Pass 2b: blocks-info + ráp file đích ----
        info = _Writer(">")
        info.write(b"\x00" * 16)
        info.pack("i", len(blocks))
        for b in blocks:
            info.pack("IIH", b.uncompressed_size, b.compressed_size, b.flags)
            if layout.version >= 9:
                info.pack("Q", b.offset if b.offset is not None else 0)
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
    # v19+: m_TypeFlags (bit 32 = node tham chiếu shared subtree) và
    # m_RefTypeHash (chỉ số vào ref_hashes của type chứa node). Chỉ dùng để
    # resolve shared subtree v26 (xem _resolve_shared_subtrees); ghi lại
    # type tree luôn dùng raw_typetree nguyên vẹn nên 2 trường này không
    # ảnh hưởng đường ghi.
    type_flags: int = 0
    ref_type_hash: Optional[int] = None


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
    # v23+: ref hashes sau node blob trong TypeTreeNodeInfo (UnityPy) —
    # hash các shared subtree mà node flag-32 của type này tham chiếu.
    ref_hashes: List[bytes] = field(default_factory=list)


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
    version: int  # header version (15-26)
    unity_version: str = ""
    target_platform: int = 0
    enable_type_tree: bool = False
    types: List[SerializedType] = field(default_factory=list)
    objects: List[SerializedObject] = field(default_factory=list)
    script_types: List[Tuple[int, int]] = field(default_factory=list)
    externals: List[Dict] = field(default_factory=list)
    ref_types: List[SerializedType] = field(default_factory=list)
    # v26+: mục shared subtrees — handler bytes nguyên vẹn (hash 16B +
    # u32 size + info), theo thứ tự trong file; ghi lại nguyên vẹn khi
    # rebuild (xem build_serialized). Bảng đã parse phục vụ resolve nằm ở
    # _shared_subtree_map (hash -> (node|None, ref_hashes)).
    shared_subtrees: List[bytes] = field(default_factory=list)
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
    # v26+: bảng shared subtree đã parse (hash -> (node|None, ref_hashes)),
    # chỉ dùng để resolve node flag-32; không dùng khi ghi (ghi dùng
    # shared_subtrees nguyên vẹn).
    _shared_subtree_map: Optional[dict] = field(default=None, repr=False,
                                                compare=False)
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

    def textures(self, warnings=None):
        """[(path_id, name)] cho moi object Texture2D (class 28).

        Ten = aligned_string dau tien trong du lieu object (m_Name).
        Texture co the chua chu ve trong anh (F4) — chi liet ke ten de
        canh bao nguoi dung, KHONG OCR, KHONG giai ma anh. Object
        Texture2D doc loi bi bo qua kem ghi chu vao `warnings` (neu co)
        thay vi lam hong ca file, nhu text_assets()."""
        out = []
        notes = []
        for o in self.objects:
            if o.class_id != 28:
                continue
            try:
                raw = self.object_bytes(o.path_id)
                name = _Reader(raw, self.endian).aligned_string()
            except PB3Error as exc:
                notes.append("bỏ qua Texture2D path_id=%d (%s)"
                             % (o.path_id, exc.code))
                continue
            out.append((o.path_id, name))
        if warnings is not None:
            warnings.extend(notes)
        return out

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
        - kind "text_data_list": bảng text dạng danh sách (phương pháp
          5 bước 2026-11-19, game Nhiệt Huyết Sinh Tồn): list entry có
          key chuỗi + >= 2 cột ngôn ngữ (tên field là mã locale) + tùy
          chọn trường nhóm fileName — xem sdk/text_data.py. Nhận diện
          bằng cấu trúc, không hardcode tên game/bundle/class/field;
          list dữ liệu thuần (vd Master*Data, entry m_Id số) không khớp
          và rơi về generic. Trả {list_field, key_field, group_field,
          columns (mã canonical theo thứ tự gặp đầu), entries [{key,
          group, values}], groups {nhóm: số entry}}.
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
                        td = text_data.text_data_list_from_fields(val)
                        if td is not None:
                            out.append({"path_id": o.path_id, "name": name or "",
                                        "kind": "text_data_list",
                                        "list_field": td["list_field"],
                                        "key_field": td["key_field"],
                                        "group_field": td["group_field"],
                                        "columns": td["columns"],
                                        "entries": td["entries"],
                                        "groups": td["groups"]})
                            continue
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
# Đường dẫn field bảng I2 dạng "terms[i].values[j]" (dùng cho writer LS).
_MB_PATH_RE = re.compile(r"^(\w+)\[(\d+)\]\.(\w+)\[(\d+)\]$")


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


# Cầu nối Rust/WASM cho read_typetree_value (chỉ có trên web khi worker.js
# đã nạp wasm): pb3_rust_typetree_value(node_json, data, pos, little_endian)
# -> JSON '{"value": ..., "end": pos}'. Vắng cầu nối (CPython/iSH) hoặc cầu
# nối hỏng -> rơi về _read_typetree_value_py như trước, không đổi hành vi.
# Node JSON chỉ gồm {name, byte_size, children} (3 trường reader dùng),
# cache theo id(node) để không serialize lại mỗi object cùng type; cache
# giữ tham chiếu mạnh tới node nên id không bị tái dùng. Giới hạn 512 mục
# để không phình RAM trên điện thoại (đầy thì xóa, serialize lại sau).
_TYPETREE_RS_BROKEN = False
_TYPETREE_RS_NODE_CACHE = {}
_TYPETREE_RS_NODE_CACHE_MAX = 512


def _typetree_node_json(node):
    """JSON của cây node ({name, byte_size, children}), có cache theo id."""
    key = id(node)
    ent = _TYPETREE_RS_NODE_CACHE.get(key)
    if ent is not None and ent[0] is node:
        return ent[1]
    js = json.dumps({"name": node.name, "byte_size": node.byte_size,
                     "children": [_typetree_node_dict(c) for c in node.children]},
                    separators=(",", ":"))
    if len(_TYPETREE_RS_NODE_CACHE) >= _TYPETREE_RS_NODE_CACHE_MAX:
        _TYPETREE_RS_NODE_CACHE.clear()
    _TYPETREE_RS_NODE_CACHE[key] = (node, js)
    return js


def _typetree_node_dict(node):
    return {"name": node.name, "byte_size": node.byte_size,
            "children": [_typetree_node_dict(c) for c in node.children]}


def _typetree_value_from_json(obj):
    """Khôi phục giá trị Python từ JSON của Rust (dict/list/str/int/None/
    bytes dạng {"$bytes": base64})."""
    if isinstance(obj, dict):
        if set(obj.keys()) == {"$bytes"}:
            return base64.b64decode(obj["$bytes"])
        return {k: _typetree_value_from_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_typetree_value_from_json(v) for v in obj]
    return obj


def _read_typetree_value_rs(data, node, pos, endian):
    """(giá_trị, vị_trị_mới) qua Rust/WASM, hoặc None khi vắng/hỏng cầu nối.
    Input hỏng (WASM ném RUST_TYPETREE_CORRUPT) thì raise E_CORRUPT."""
    global _TYPETREE_RS_BROKEN
    if _TYPETREE_RS_BROKEN:
        return None
    try:
        import js as _js
        fn = getattr(_js, "pb3_rust_typetree_value", None)
    except Exception:
        fn = None
    if fn is None:
        return None
    try:
        out = fn(_typetree_node_json(node), bytes(data), int(pos), endian == "<")
    except Exception as exc:  # noqa: BLE001 - lỗi JS -> E_CORRUPT hoặc fallback
        if "RUST_TYPETREE_CORRUPT" in str(exc):
            detail = str(exc).split("RUST_TYPETREE_CORRUPT", 1)[1].lstrip(": ").strip()
            raise PB3Error("E_CORRUPT", "typetree (Rust): %s" % (detail or "object hỏng"),
                           where="MonoBehaviour")
        _TYPETREE_RS_BROKEN = True
        return None
    try:
        parsed = json.loads(str(out))
        value = _typetree_value_from_json(parsed["value"])
        end = int(parsed["end"])
    except (KeyError, TypeError, ValueError, AttributeError):
        _TYPETREE_RS_BROKEN = True
        return None
    return value, end


def read_typetree_value(data, node, pos, endian="<"):
    """Đọc 1 giá trị theo node type tree — thử Rust/WASM trước, rơi về
    Python thuần khi vắng cầu nối. Cùng chữ ký + cùng lỗi như trước."""
    rs = _read_typetree_value_rs(data, node, pos, endian)
    if rs is not None:
        return rs
    return _read_typetree_value_py(data, node, pos, endian)


def _read_typetree_value_py(data, node, pos, endian="<"):
    """Đọc 1 giá trị theo node type tree tại pos (tương đối đầu object).

    Bản Python thuần (đệ quy). Trả (giá_trị, vị_trị_mới). dict = struct,
    list = vector, str = chuỗi (byte[] giải UTF-8 lỗi thì trả bytes để bên
    gọi bỏ qua, không hỏng cả object). Hỏng cấu trúc -> PB3Error E_CORRUPT.
    Điểm vào công khai là read_typetree_value() (thử Rust/WASM trước)."""
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
                v, pos = _read_typetree_value_py(data, elem, pos, endian)
                vals.append(v)
            return vals, pos
        out = {}
        for c in node.children:
            pos = _typetree_align4(pos)
            v, pos = _read_typetree_value_py(data, c, pos, endian)
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


def write_typetree_value(writer, node, value, endian="<"):
    """Ghi 1 giá trị theo node type tree — ảnh gương của read_typetree_value.

    writer: _Writer (đang ở vị trí tương đối đầu object). value phải đúng
    hình dạng read_typetree_value trả (dict/list/str/bytes/int/None):
    struct -> dict, mảng -> list, chuỗi/byte[] -> str/bytes, lá số -> int
    (float/double giữ nguyên bit pattern dạng int như lúc đọc).
    Ghi đúng thứ tự + căn 4 như lúc đọc nên round-trip (đọc -> ghi, không
    đổi) cho ra đúng bytes gốc. Không ghi trailing bytes (bên gọi tự giữ
    raw[end:] sau khi ghi). Sai hình dạng -> PB3Error E_CORRUPT (không
    đoán, không cắt cụt)."""
    where = "MonoBehaviour"
    if node.children:
        if _typetree_is_array(node):
            _size_node, elem = node.children[0].children
            writer.align(4)
            if elem.byte_size == 1 and not elem.children:
                if isinstance(value, str):
                    raw = value.encode("utf-8")
                elif isinstance(value, (bytes, bytearray)):
                    raw = bytes(value)
                else:
                    raise PB3Error("E_CORRUPT",
                                   "node %r: mảng byte cần str/bytes, nhận %s"
                                   % (node.name, type(value).__name__),
                                   where=where)
                writer.pack("i", len(raw))
                writer.write(raw)
                writer.align(4)
                return
            if not isinstance(value, list):
                raise PB3Error("E_CORRUPT",
                               "node %r: mảng cần list, nhận %s"
                               % (node.name, type(value).__name__),
                               where=where)
            writer.pack("i", len(value))
            for item in value:
                writer.align(4)
                write_typetree_value(writer, elem, item, endian)
            return
        if not isinstance(value, dict):
            raise PB3Error("E_CORRUPT",
                           "node %r: struct cần dict, nhận %s"
                           % (node.name, type(value).__name__), where=where)
        for c in node.children:
            writer.align(4)
            if c.name not in value:
                raise PB3Error("E_CORRUPT",
                               "node %r: thiếu field %r khi ghi"
                               % (node.name, c.name), where=where)
            write_typetree_value(writer, c, value[c.name], endian)
        return
    if node.byte_size == 0:
        return
    if node.byte_size == 1:
        if isinstance(value, bool):
            value = 1 if value else 0
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise PB3Error("E_CORRUPT",
                           "node %r: byte cần int 0-255, nhận %r"
                           % (node.name, value), where=where)
        writer.write(bytes([value]))
        return
    if node.byte_size == 2:
        if not isinstance(value, int):
            raise PB3Error("E_CORRUPT",
                           "node %r: i16 cần int, nhận %r"
                           % (node.name, value), where=where)
        writer.pack("h", value)
        return
    if node.byte_size == 4:
        if not isinstance(value, int):
            raise PB3Error("E_CORRUPT",
                           "node %r: i32 cần int, nhận %r"
                           % (node.name, value), where=where)
        writer.pack("i", value)
        return
    if node.byte_size == 8:
        if not isinstance(value, int):
            raise PB3Error("E_CORRUPT",
                           "node %r: i64 cần int, nhận %r"
                           % (node.name, value), where=where)
        writer.pack("q", value)
        return
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


def _ls_value_slots(raw, endian="<"):
    """Vị trí từng chuỗi giá trị trong bảng I2 (byte-walk).

    Đi đúng layout của parse_language_source_bytes, trả list slot theo
    thứ tự xuất hiện: (term_index, lang_index, len_pos, str_start,
    str_end_padded) với len_pos là vị trí i32 độ dài, str_start là đầu
    chuỗi, str_end_padded là cuối vùng chuỗi đã căn 4 (đầu slot sau).
    Không khớp layout -> None (bên gọi từ chối ghi)."""
    try:
        data = bytes(raw)
        name = read_monobehaviour_name(data, endian)
        if name is None:
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
        slots = []
        n_langs = None
        for ti in range(n_terms):
            _term = r.aligned_string()
            r.i32()  # TermType
            lang_count = r.i32()
            if n_langs is None:
                if lang_count < 1 or lang_count > _LS_MAX_LANGS:
                    return None
                n_langs = lang_count
            elif lang_count != n_langs:
                return None
            for li in range(n_langs):
                len_pos = r.pos
                v = r.aligned_string()
                # aligned_string đọc i32 + bytes + đệm: cuối vùng = pos sau khi đọc.
                slots.append((ti, li, len_pos, len_pos + 4, r.pos))
            flag_count = r.i32()
            if flag_count != n_langs:
                return None
            r.read(flag_count)
            r.align(4)
            touch_count = r.i32()
            if touch_count < 0 or touch_count > _LS_MAX_TOUCH:
                return None
            for _i in range(touch_count):
                r.aligned_string()
        return slots
    except (PB3Error, struct.error, ValueError, UnicodeDecodeError,
            IndexError, TypeError):
        return None


def write_language_source_bytes(raw, changes, endian="<"):
    """Ghi lại bảng ngôn ngữ I2 KHÔNG có type tree (byte-walk).

    changes: [{field_path, before, after}] với field_path dạng
    "terms[i].values[j]" (i = chỉ số term, j = chỉ số cột ngôn ngữ).
    Chỉ thay đúng các slot chuỗi đã chỉ định bằng segment-splice (giữ
    nguyên mọi byte khác: flags, touch, ngôn ngữ, trailing). Chuỗi mới
    phải là str, không chứa NUL, không vượt _LS_MAX_VALUE_CHARS.
    Trả bytes mới; verify bằng cách parse lại (parse_language_source_bytes)
    và đối chiếu từng giá trị đổi + toàn bộ giá trị không đổi.
    Không khớp layout / term/cột lạ / before không khớp -> PB3Error."""
    data = bytes(raw)
    info = parse_language_source_bytes(data, endian)
    if info is None:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "object không phải bảng ngôn ngữ I2 (byte-walk)",
                       where="language_source")
    slots = _ls_value_slots(data, endian)
    if slots is None:
        raise PB3Error("E_CORRUPT",
                       "không xác định được vị trí chuỗi giá trị trong bảng I2",
                       where="language_source")
    by_slot = {(ti, li): (lp, ss, se) for (ti, li, lp, ss, se) in slots}
    terms = info["terms"]
    # Kiểm tra + chuẩn bị thay thế (theo thứ tự vị trí để splice 1 lần).
    repl = {}  # len_pos -> bytes mới của cả slot (i32 + chuỗi + đệm)
    for ch in changes or []:
        fp = ch.get("field_path") or ""
        m = _MB_PATH_RE.match(fp)
        if not m or m.group(1) != "terms" or m.group(3) != "values":
            raise PB3Error("E_CONFIG_INVALID",
                           "field_path bảng I2 phải dạng terms[i].values[j], nhận %r"
                           % fp, where="language_source")
        ti, li = int(m.group(2)), int(m.group(4))
        if ti < 0 or ti >= len(terms):
            raise PB3Error("E_CONFIG_INVALID",
                           "terms[%d] ngoài bảng I2 (%d term)"
                           % (ti, len(terms)), where="language_source")
        n_langs = len(info["languages"])
        if li < 0 or li >= n_langs:
            raise PB3Error("E_CONFIG_INVALID",
                           "values[%d] ngoài bảng I2 (%d cột ngôn ngữ)"
                           % (li, n_langs), where="language_source")
        cur = terms[ti]["values"][li]
        if ch.get("before") is not None and ch["before"] != cur:
            raise PB3Error("E_CONFIG_INVALID",
                           "terms[%d].values[%d] hiện là %r, khác before %r "
                           "(dữ liệu đã đổi so với lúc lập plan)"
                           % (ti, li, cur, ch["before"]),
                           where="language_source")
        new = ch.get("after")
        if not isinstance(new, str) or "\x00" in new:
            raise PB3Error("E_CONFIG_INVALID",
                           "giá trị mới cho terms[%d].values[%d] phải là "
                           "chuỗi không chứa NUL" % (ti, li),
                           where="language_source")
        raw_b = new.encode("utf-8")
        if len(new) > _LS_MAX_VALUE_CHARS:
            raise PB3Error("E_CONFIG_INVALID",
                           "giá trị mới cho terms[%d].values[%d] dài %d ký tự, "
                           "vượt giới hạn %d"
                           % (ti, li, len(new), _LS_MAX_VALUE_CHARS),
                           where="language_source")
        lp, _ss, _se = by_slot[(ti, li)]
        w = _Writer(endian)
        w.aligned_string(new)
        repl[lp] = w.bytes()
    if not repl:
        return data
    out = bytearray()
    pos = 0
    for (ti, li, lp, ss, se) in slots:
        if lp not in repl:
            continue
        out += data[pos:lp]
        out += repl[lp]
        pos = se
    out += data[pos:]
    new_data = bytes(out)
    # Verify: parse lại + đối chiếu.
    after = parse_language_source_bytes(new_data, endian)
    if after is None:
        raise PB3Error("E_VERIFY_FAILED",
                       "bảng I2 sau khi ghi parse lại lỗi",
                       where="language_source")
    if after["languages"] != info["languages"] or len(after["terms"]) != len(terms):
        raise PB3Error("E_VERIFY_FAILED",
                       "bảng I2 sau khi ghi đổi cấu trúc (ngôn ngữ/số term)",
                       where="language_source")
    for ch in changes or []:
        m = _MB_PATH_RE.match(ch["field_path"])
        ti, li = int(m.group(2)), int(m.group(4))
        if after["terms"][ti]["values"][li] != ch["after"]:
            raise PB3Error("E_VERIFY_FAILED",
                           "terms[%d].values[%d] sau khi ghi là %r, khác %r"
                           % (ti, li, after["terms"][ti]["values"][li],
                              ch["after"]), where="language_source")
    return new_data


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



def _ls_field_name(d, *names):
    """(tên field thật đã dùng, giá trị) — tên đầu tiên trong names có trong d."""
    for n in names:
        if isinstance(d, dict) and n in d:
            return n, d[n]
    return None, None


def language_source_tt_targets(val, source_locale="en"):
    """Target ghi cho bảng ngôn ngữ I2 CÓ type tree.

    Trả [(term, field_path, before)] hoặc None khi không dò được tên field
    thật (fail-closed — caller giữ hành vi cũ: ghi note + bỏ qua object).
    field_path dạng "mSource.mTerms[i].Languages[j]" (tiền tố rỗng nếu bảng
    ở gốc dict), dùng đúng tên field trong dict đã parse để
    write_monobehaviour_object_bytes + _verify_mb_values đi theo được.
    source_locale: cột ngôn ngữ gốc để ghi (mặc định "en"). Chỉ dùng khi
    language_source_from_fields đã validate cấu trúc bảng."""
    ls = language_source_from_fields(val)
    if ls is None:
        return None
    src = val.get("mSource") if isinstance(val.get("mSource"), dict) else val
    prefix = "mSource." if src is not val else ""
    langs_key, _langs = _ls_field_name(src, "mLanguages", "Languages")
    terms_key, terms_raw = _ls_field_name(src, "mTerms", "Terms")
    if langs_key is None or terms_key is None or not isinstance(terms_raw, list):
        return None
    col = None
    for idx, lang in enumerate(ls["languages"]):
        if lang.get("code") == source_locale:
            col = idx
            break
    if col is None:
        return None
    out = []
    for i, entry in enumerate(terms_raw):
        if not isinstance(entry, dict):
            return None
        _tk, term = _ls_field_name(entry, "Term", "m_Term", "term")
        values_key, values = _ls_field_name(entry, "Languages", "m_Languages",
                                            "languages")
        if not isinstance(term, str) or not isinstance(values, list) \
                or col >= len(values) or not isinstance(values[col], str):
            return None
        out.append((term, "%s%s[%d].%s[%d]" % (prefix, terms_key, i, values_key, col),
                    values[col]))
    return out


def _scan_monobehaviour_slots(data, endian="<"):
    """Quét chuỗi trong 1 object MonoBehaviour KHÔNG có type tree, trả slot.

    Trả [(len_pos, str_start, slot_end, value)] theo thứ tự quét (thứ tự
    byte tăng dần, ổn định cho cùng 1 object): mỗi slot là 1 aligned_string
    đã xác minh (i32 độ dài L ở vị trí căn 4 ngay trước chuỗi, L byte là
    text UTF-8 in được, đệm sau (nếu có) toàn 0). slot_end = cuối chuỗi +
    đệm căn 4 (vị trí slot tiếp theo có thể bắt đầu). `scan_monobehaviour_strings`
    dùng hàm này (chỉ lấy value) nên thứ tự/số lượng khớp nhau tuyệt đối —
    writer scan-path (write_scanned_monobehaviour_bytes) và extraction dùng
    chung 1 thứ tự slot, không lệch chỉ số."""
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
        pad = (-(lp + 4 + size)) % 4
        se = lp + 4 + size + pad
        if size == len(run):
            s = _mb_text_ok(run)
            if s is None:
                continue
            if pad and any(data[lp + 4 + size:se]):
                continue
            out.append((lp, s0, se, s))
        elif size > len(run):
            # Chuỗi chứa \n (regex cắt) hoặc ký tự regex không nhận: lấy đúng size byte.
            s = _mb_text_ok(data[s0:s0 + size])
            if s is None:
                continue
            if pad and any(data[s0 + size:s0 + size + pad]):
                continue
            out.append((lp, s0, se, s))
        else:  # size < len(run): run gộp nhiều chuỗi — lấy đúng size byte đầu.
            s = _mb_text_ok(run[:size])
            if s is None:
                continue
            out.append((lp, s0, se, s))
    return name, out


def scan_monobehaviour_strings(data, endian="<"):
    """Quét chuỗi trong 1 object MonoBehaviour KHÔNG có type tree.

    Trả (m_Name|None, [chuỗi đã xác minh]). Mỗi chuỗi phải là aligned_string
    thật: i32 độ dài L ở vị trí căn 4 ngay trước chuỗi, L byte là text UTF-8
    in được, đệm sau (nếu có) toàn 0. Tìm ứng viên bằng regex chạy bằng C
    trên bytes rồi chỉ xác minh những ứng viên đó — chạy 59MB trong ~0,2s,
    không quét từng offset bằng Python. Chuỗi chứa \\n (regex cắt làm đôi)
    vẫn nhận được bằng cách lấy đúng L byte từ đầu chuỗi."""
    name, slots = _scan_monobehaviour_slots(data, endian)
    return name, [s for (_lp, _ss, _se, s) in slots]


def write_scanned_monobehaviour_bytes(raw, changes, endian="<"):
    """Ghi lại chuỗi quét được trong 1 MonoBehaviour KHÔNG có type tree.

    changes: [{field_path, before, after}] với field_path dạng "scan[i]"
    (i = chỉ số slot theo thứ tự của _scan_monobehaviour_slots — cùng thứ tự
    extraction dùng để đặt tên key generic "<Tên>.s<i+1>"). Chỉ thay đúng
    slot đã chỉ định bằng segment-splice (giữ nguyên mọi byte khác).
    Chuỗi mới phải là str, không chứa NUL, mã UTF-8 <= 65536 byte (giới hạn
    của bộ quét); before phải khớp giá trị hiện tại của slot (không khớp ->
    từ chối, không đoán). Trả bytes mới; verify bằng cách quét lại: số slot
    không đổi, slot đổi == after, mọi slot khác giống hệt. Không khớp ->
    PB3Error. Chuỗi mới ngắn hơn 3 ký tự sẽ bị bộ quét bỏ qua nên verify
    thất bại — đó là giới hạn có chủ ý (fail-closed), không tự bịa slot."""
    data = bytes(raw)
    _name, slots = _scan_monobehaviour_slots(data, endian)
    by_index = {i: (lp, ss, se, v) for i, (lp, ss, se, v) in enumerate(slots)}
    repl = {}  # len_pos -> bytes mới của cả slot (i32 + chuỗi + đệm)
    for ch in changes or []:
        fp = ch.get("field_path") or ""
        m = re.match(r"^scan\[(\d+)\]$", fp)
        if not m:
            raise PB3Error("E_CONFIG_INVALID",
                           "field_path scan-path phải dạng scan[i], nhận %r"
                           % fp, where="scanned_monobehaviour")
        i = int(m.group(1))
        if i not in by_index:
            raise PB3Error("E_CONFIG_INVALID",
                           "scan[%d] ngoài object (%d slot quét được)"
                           % (i, len(slots)), where="scanned_monobehaviour")
        lp, _ss, _se, cur = by_index[i]
        if ch.get("before") is not None and ch["before"] != cur:
            raise PB3Error("E_CONFIG_INVALID",
                           "scan[%d] hiện là %r, khác before %r (dữ liệu đã "
                           "đổi so với lúc lập plan)" % (i, cur, ch["before"]),
                           where="scanned_monobehaviour")
        new = ch.get("after")
        if not isinstance(new, str) or "\x00" in new:
            raise PB3Error("E_CONFIG_INVALID",
                           "giá trị mới cho scan[%d] phải là chuỗi không chứa NUL"
                           % i, where="scanned_monobehaviour")
        raw_b = new.encode("utf-8")
        if len(raw_b) > 65536:
            raise PB3Error("E_CONFIG_INVALID",
                           "giá trị mới cho scan[%d] dài %d byte, vượt giới hạn "
                           "quét 65536" % (i, len(raw_b)),
                           where="scanned_monobehaviour")
        w = _Writer(endian)
        w.aligned_string(new)
        repl[lp] = w.bytes()
    if not repl:
        return data
    out = bytearray()
    pos = 0
    for (lp, _ss, se, _v) in slots:
        if lp not in repl:
            continue
        out += data[pos:lp]
        out += repl[lp]
        pos = se
    out += data[pos:]
    new_data = bytes(out)
    # Verify: quét lại + đối chiếu toàn bộ slot.
    _n2, slots2 = _scan_monobehaviour_slots(new_data, endian)
    if len(slots2) != len(slots):
        raise PB3Error("E_VERIFY_FAILED",
                       "quét lại sau khi ghi được %d slot, khác %d slot gốc "
                       "(giá trị mới có thể quá ngắn < 3 ký tự nên bộ quét "
                       "không nhận diện được, hoặc đã tạo slot giả)"
                       % (len(slots2), len(slots)),
                       where="scanned_monobehaviour")
    for ch in changes or []:
        i = int(re.match(r"^scan\[(\d+)\]$", ch["field_path"]).group(1))
        if slots2[i][3] != ch["after"]:
            raise PB3Error("E_VERIFY_FAILED",
                           "scan[%d] sau khi ghi là %r, khác %r"
                           % (i, slots2[i][3], ch["after"]),
                           where="scanned_monobehaviour")
    for i, (_lp, _ss, _se, v) in enumerate(slots):
        if i in {int(re.match(r"^scan\[(\d+)\]$", ch["field_path"]).group(1))
                 for ch in changes}:
            continue
        if slots2[i][3] != v:
            raise PB3Error("E_VERIFY_FAILED",
                           "scan[%d] không đổi bị đổi %r -> %r sau khi ghi"
                           % (i, v, slots2[i][3]),
                           where="scanned_monobehaviour")
    return new_data


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


def _mb_path_parts(path):
    """'a.b[2].c' -> ['a', 'b', 2, 'c']; '[+]' cuối = append phần tử mới.
    Sai cú pháp -> ValueError."""
    if not isinstance(path, str) or not path:
        raise ValueError("field_path phải là chuỗi không rỗng")
    parts = []
    i = 0
    n = len(path)
    while i < n:
        ch = path[i]
        if ch == ".":
            i += 1
            continue
        if ch == "[":
            j = path.index("]", i)
            inner = path[i + 1:j]
            if inner == "+":
                parts.append("+")
            else:
                try:
                    parts.append(int(inner))
                except ValueError:
                    raise ValueError("field_path %r: chỉ số mảng %r không hợp lệ"
                                     % (path, inner))
            i = j + 1
            continue
        j = i
        while j < n and path[j] not in ".[":
            j += 1
        if j == i:
            raise ValueError("field_path %r sai cú pháp" % path)
        parts.append(path[i:j])
        i = j
    if not parts:
        raise ValueError("field_path %r sai cú pháp" % path)
    return parts


def _mb_path_get(container, parts, where="MonoBehaviour"):
    """Lấy giá trị lá theo đường dẫn (dict/list). Không tìm thấy -> PB3Error."""
    cur = container
    for p in parts:
        try:
            if isinstance(p, int):
                if not isinstance(cur, list) or p < 0 or p >= len(cur):
                    raise IndexError
                cur = cur[p]
            else:
                if not isinstance(cur, dict) or p not in cur:
                    raise KeyError(p)
                cur = cur[p]
        except (IndexError, KeyError, TypeError):
            raise PB3Error("E_CONFIG_INVALID",
                           "không tìm thấy field %r trong object MonoBehaviour"
                           % ".".join(str(x) for x in parts), where=where)
    return cur


def _mb_path_set(container, parts, new_value, where="MonoBehaviour"):
    """Đặt giá trị lá theo đường dẫn (đã đi qua _mb_path_get để kiểm tra)."""
    cur = container
    for p in parts[:-1]:
        cur = cur[p] if isinstance(p, int) else cur[p]
    last = parts[-1]
    if isinstance(last, int):
        cur[last] = new_value
    else:
        cur[last] = new_value


def _typetree_default(node):
    """Giá trị mặc định cho 1 node type tree (dùng khi chèn phần tử mảng mới):
    chuỗi/byte[] -> "", mảng khác -> [], struct -> dict con, lá số -> 0,
    lá byte_size 0 -> None."""
    if node.children:
        if _typetree_is_array(node):
            _size_node, elem = node.children[0].children
            if elem.byte_size == 1 and not elem.children:
                return ""
            return []
        return {c.name: _typetree_default(c) for c in node.children}
    if node.byte_size == 0:
        return None
    return 0


def write_monobehaviour_object_bytes(sf, path_id, changes):
    """Trả bytes mới của 1 MonoBehaviour CÓ type tree sau khi áp changes.

    changes: [{field_path, before, after, element?}] với field_path theo
    dạng _flatten_typetree_strings ("a.b[2].c"; chấp nhận cả tên chuẩn
    m_GameObject/m_Enabled/m_Script/m_Name lẫn tên gốc của 4 field đầu).
    Chỉ cho phép đổi lá chuỗi (str/bytes); before phải khớp giá trị hiện
    tại (không khớp -> từ chối, không đoán).
    field_path dạng "arr[+]" (kèm "element" là dict phần tử đầy đủ) sẽ
    CHÈN 1 phần tử mới vào cuối mảng (dùng cho StringTable thiếu entry).
    Trả (new_bytes, applied) với applied = [{field_path, before, after}].
    Round-trip không đổi (changes rỗng) cho ra đúng bytes gốc (giữ nguyên
    trailing bytes raw[end:]). Lỗi -> PB3Error."""
    o = sf.get_object(path_id)
    if o.class_id != 114:
        raise PB3Error("E_CONFIG_INVALID", "object %d không phải MonoBehaviour"
                       % path_id, where=sf.where + "::%d" % path_id)
    typ = sf.types[o.type_id]
    if typ.node is None:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "MonoBehaviour path_id=%d không có type tree (dùng "
                       "đường byte-walk riêng cho bảng I2, hoặc scan-path "
                       "chưa hỗ trợ ghi)" % path_id,
                       where=sf.where + "::%d" % path_id)
    raw = sf.object_bytes(path_id)
    val, end = read_typetree_value(raw, typ.node, 0, sf.endian)
    if not isinstance(val, dict):
        raise PB3Error("E_CORRUPT",
                       "MonoBehaviour path_id=%d: gốc type tree không phải struct"
                       % path_id, where=sf.where + "::%d" % path_id)
    # read_monobehaviour_object đổi tên 4 field đầu thành m_GameObject/
    # m_Enabled/m_Script/m_Name; writer đọc bằng tên GỐC nên dịch ngược
    # các tên chuẩn đó về tên gốc (đường dẫn từ monobehaviour_write_targets
    # dùng tên chuẩn; các field khác không đổi).
    rename = {}
    children = typ.node.children
    if len(children) >= 4:
        c0, c1, c2, c3 = children[0], children[1], children[2], children[3]
        pptr = (lambda c: len(c.children) == 2
                and [g.name for g in c.children] == ["m_FileID", "m_PathID"])
        if (pptr(c0) and c1.byte_size == 1 and not c1.children
                and pptr(c2) and isinstance(val.get(c3.name), str)):
            rename = {"m_GameObject": c0.name, "m_Enabled": c1.name,
                      "m_Script": c2.name, "m_Name": c3.name}
    applied = []
    for ch in changes or []:
        fp = ch.get("field_path") or ""
        try:
            parts = _mb_path_parts(fp)
        except ValueError as exc:
            raise PB3Error("E_CONFIG_INVALID", str(exc),
                           where=sf.where + "::%d" % path_id)
        if rename:
            parts = [rename.get(p, p) if isinstance(p, str) else p
                     for p in parts]
        if parts and parts[-1] == "+":
            # Chèn phần tử mới vào cuối mảng.
            arr = _mb_path_get(val, parts[:-1],
                               where=sf.where + "::%d" % path_id)
            if not isinstance(arr, list):
                raise PB3Error("E_CONFIG_INVALID",
                               "field_path %r: đích chèn không phải mảng"
                               % fp, where=sf.where + "::%d" % path_id)
            element = ch.get("element")
            if not isinstance(element, dict):
                raise PB3Error("E_CONFIG_INVALID",
                               "field_path %r: chèn phần tử cần 'element' "
                               "là dict đầy đủ" % fp,
                               where=sf.where + "::%d" % path_id)
            arr.append(copy.deepcopy(element))
            applied.append({"field_path": fp, "before": None,
                            "after": ch.get("after")})
            continue
        cur = _mb_path_get(val, parts, where=sf.where + "::%d" % path_id)
        if not isinstance(cur, (str, bytes)):
            raise PB3Error("E_CONFIG_INVALID",
                           "field_path %r: giá trị hiện tại không phải chuỗi "
                           "(%s) — chỉ ghi được lá chuỗi"
                           % (fp, type(cur).__name__),
                           where=sf.where + "::%d" % path_id)
        if ch.get("before") is not None and ch["before"] != cur:
            raise PB3Error("E_CONFIG_INVALID",
                           "field_path %r hiện là %r, khác before %r (dữ liệu "
                           "đã đổi so với lúc lập plan)"
                           % (fp, cur, ch["before"]),
                           where=sf.where + "::%d" % path_id)
        new = ch.get("after")
        if not isinstance(new, str):
            raise PB3Error("E_CONFIG_INVALID",
                           "field_path %r: giá trị mới phải là chuỗi, nhận %s"
                           % (fp, type(new).__name__),
                           where=sf.where + "::%d" % path_id)
        if isinstance(cur, bytes):
            new_val = new.encode("utf-8")
        else:
            new_val = new
        _mb_path_set(val, parts, new_val)
        applied.append({"field_path": fp, "before": cur, "after": new})
    w = _Writer(sf.endian)
    write_typetree_value(w, typ.node, val, sf.endian)
    head = w.bytes()
    # Verify trong writer: parse lại head, đối chiếu toàn bộ dict đã sửa
    # (kiểm tra layout/căn của writer khớp reader; độ dài head được phép
    # khác end gốc khi chuỗi đổi độ dài — trailing giữ nguyên sau head mới).
    check, check_end = read_typetree_value(head, typ.node, 0, sf.endian)
    if check != val or check_end != len(head):
        raise PB3Error("E_CORRUPT",
                       "ghi MonoBehaviour path_id=%d: parse lại head sau khi "
                       "ghi không khớp dict đã sửa (writer lệch layout)"
                       % path_id, where=sf.where + "::%d" % path_id)
    return head + raw[end:], applied


def monobehaviour_write_targets(sf, source_locale="en", shared_index=None,
                                generic_fn=None):
    """Liệt kê chuỗi ghi được trong mọi MonoBehaviour của 1 SerializedFile.

    Trả list {path_id, kind, name, key, field_path, before, element?}:
    - language_source (không type tree, byte-walk): mỗi term -> values[cột
      của source_locale]; key = tên term; field_path "terms[i].values[j]".
      Không có cột source_locale -> bỏ qua object kèm lý do trong notes.
    - language_source_tt (có type tree): bảng I2 — mỗi term -> cột của
      source_locale; key = tên term; field_path dùng đúng tên field thật
      (vd "mSource.mTerms[i].Languages[j]"); không dò được tên field thật
      thì bỏ qua object kèm lý do trong notes (fail-closed).
    - string_table (có type tree): mỗi entry -> m_Localized; key =
      "Collection/Key" (tra ngược từ shared_index {collection: {key: m_Id}});
      field_path "m_TableData[i].m_Localized". Entry thiếu trong shared_index
      thì bỏ qua (không đặt được tên key).
    - generic: mọi chuỗi của object không phải nguồn ngôn ngữ, ánh xạ đúng
      key trích xuất qua generic_fn (mặc định None = bỏ qua generic).
      Hàm generic sống ở sdk.extraction (generic_write_targets) vì nó dùng
      _mb_generic_kv của extraction; unity KHÔNG import extraction (tránh
      import vòng unity <-> extraction — extraction đã import unity) nên
      bên gọi nào cần target generic phải truyền generic_fn vào.
      field_path "scan[j]" nếu không type tree, field_path thật nếu có;
      key mơ hồ >1 vị trí thì bỏ qua, không đoán.
    - SharedTableData / Locale: bỏ qua (key, không phải text dịch).
    shared_index: {collection: {key: m_Id}} (từ mọi SharedTableData trong
    source); thiếu thì StringTable vẫn liệt kê nhưng key là None (plan bỏ qua).
    Trả (targets, notes) với notes = [lý do bỏ qua]."""
    targets = []
    notes = []
    rev = {}
    if shared_index:
        for coll, keys in (shared_index or {}).items():
            for k, mid in (keys or {}).items():
                rev[mid] = (coll, k)
    for o in sf.objects:
        if o.class_id != 114:
            continue
        try:
            typ = sf.types[o.type_id]
        except IndexError:
            continue
        if typ.node is not None:
            try:
                val = read_monobehaviour_object(sf, o.path_id)
            except PB3Error:
                continue
            name = val.get("m_Name") if isinstance(val.get("m_Name"), str) else ""
            if "m_TableData" in val and "m_LocaleId" in val:
                loc = val.get("m_LocaleId")
                code = loc.get("m_Code") if isinstance(loc, dict) else None
                if code != source_locale:
                    continue
                for i, e in enumerate(val.get("m_TableData") or []):
                    if not (isinstance(e, dict)
                            and isinstance(e.get("m_Id"), int)
                            and isinstance(e.get("m_Localized"), str)):
                        continue
                    hit = rev.get(e["m_Id"])
                    if hit is None:
                        continue
                    coll, key = hit
                    targets.append({"path_id": o.path_id, "kind": "string_table",
                                    "name": name or "", "key": "%s/%s" % (coll, key),
                                    "field_path": "m_TableData[%d].m_Localized" % i,
                                    "before": e["m_Localized"]})
                continue
            if "m_Entries" in val and "m_TableCollectionName" in val:
                continue  # SharedTableData: key, không phải text dịch
            if "m_Identifier" in val:
                continue  # Locale: không phải text dịch
            ls = language_source_from_fields(val)
            if ls is not None:
                tt_targets = language_source_tt_targets(val, source_locale)
                if tt_targets is None:
                    notes.append("MonoBehaviour path_id=%d: bảng ngôn ngữ CÓ "
                                 "type tree — không dò được tên field thật nên "
                                 "không ghi (fail-closed)" % o.path_id)
                    continue
                for term, fp, before in tt_targets:
                    targets.append({"path_id": o.path_id,
                                    "kind": "language_source_tt",
                                    "name": name or "", "key": term,
                                    "field_path": fp, "before": before})
                continue
            # Generic có type tree: target lấy từ generic_fn ở cuối
            # (key khớp output trích xuất, không phải field_path thô).
        else:
            try:
                raw = sf.object_bytes(o.path_id)
            except PB3Error:
                continue
            ls = parse_language_source_bytes(raw, sf.endian)
            if ls is None:
                continue
            name = ls.get("name") or ""
            langs = ls.get("languages") or []
            col = None
            for idx, lang in enumerate(langs):
                if lang.get("code") == source_locale:
                    col = idx
                    break
            if col is None:
                notes.append("MonoBehaviour path_id=%d (%s): bảng I2 không có "
                             "cột %r — không ghi được" % (o.path_id, name, source_locale))
                continue
            for i, term in enumerate(ls.get("terms") or []):
                vals = term.get("values") or []
                if col >= len(vals):
                    continue
                targets.append({"path_id": o.path_id, "kind": "language_source",
                                "name": name, "key": term.get("term"),
                                "field_path": "terms[%d].values[%d]" % (i, col),
                                "before": vals[col]})
    # Generic (cả có/không type tree): key khớp output trích xuất; hàm
    # generic sống ở sdk.extraction, bên gọi truyền qua generic_fn.
    # Object nguồn ngôn ngữ đã có target riêng ở trên.
    if generic_fn is not None:
        targets.extend(generic_fn(sf))
    return targets, notes


def string_table_element_template(sf, path_id, m_id, localized):
    """Dict phần tử m_TableData mới cho 1 StringTable (để chèn entry thiếu).

    Mẫu: deep-copy entry đầu tiên (giữ nguyên cấu trúc m_Metadata...),
    đổi m_Id/m_Localized. Bảng rỗng (0 entry): dựng mặc định từ node
    phần tử của m_TableData (_typetree_default). Không đúng cấu trúc
    (không phải StringTable / thiếu m_Id/m_Localized) -> None."""
    try:
        o = sf.get_object(path_id)
        typ = sf.types[o.type_id]
    except (PB3Error, IndexError):
        return None
    if typ.node is None:
        return None
    try:
        val = read_monobehaviour_object(sf, path_id)
    except PB3Error:
        return None
    rows = val.get("m_TableData")
    if isinstance(rows, list) and rows:
        el = copy.deepcopy(rows[0])
        if not isinstance(el, dict) or "m_Id" not in el \
                or "m_Localized" not in el:
            return None
        el["m_Id"] = m_id
        el["m_Localized"] = localized
        return el
    for c in typ.node.children:
        if c.name == "m_TableData" and _typetree_is_array(c):
            elem_node = c.children[0].children[1]
            el = _typetree_default(elem_node)
            if not isinstance(el, dict) or "m_Id" not in el \
                    or "m_Localized" not in el:
                return None
            el["m_Id"] = m_id
            el["m_Localized"] = localized
            return el
    return None


def _parse_typetree_blob(reader, version, where, serialized_size=None):
    """Blob type tree (v>=12): node_count, stringbuffer, node 24/32 byte.

    v23+ (UnityPy master TypeTreeNodeInfo): blob mở đầu bằng magic
    "mhtt" + format version (i32, phải bằng version) trước node_count, và
    KẾT THÚC bằng u32 ref_hash_count + ref_hashes 16 byte (kể cả count=0).
    `serialized_size` (độ dài info đã khai ở handler) dùng để kiểm tra
    chặt phần đuôi: sau node blob, phần còn lại phải là đúng 0 byte (dạng
    legacy của fixture v23 cũ trong repo — builder cũ không ghi đuôi ref
    hashes) hoặc đúng 4 + 16*count byte; còn lại -> E_CORRUPT.
    Trả (root|None, raw_blob, ref_hashes). raw_blob gồm toàn bộ info từ
    "mhtt" tới hết ref hashes (để ghi lại nguyên vẹn)."""
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
    ref_hashes = []
    if version >= 23 and serialized_size is not None:
        remaining = serialized_size - (reader.pos - start)
        if remaining == 0:
            # Dạng legacy (fixture v23 cũ của repo): không có đuôi ref hash.
            pass
        elif remaining >= 4 and (remaining - 4) % 16 == 0:
            count = reader.i32()
            if count != (remaining - 4) // 16:
                raise PB3Error("E_CORRUPT",
                               "type tree v23: ref_hash_count %d không khớp "
                               "phần còn lại %d byte" % (count, remaining),
                               where=where)
            ref_hashes = [reader.read(16) for _ in range(count)]
        else:
            raise PB3Error("E_CORRUPT",
                           "type tree v23: phần còn lại %d byte sau node blob "
                           "không phải ref hashes" % remaining, where=where)
    raw_blob = reader._b[start:reader.pos]
    nodes = []
    for values in struct.iter_unpack("<" + fmt, raw_nodes):
        ver, level, tflags, type_off, name_off, bsize, index, meta = values[:8]
        ref_idx = values[8] if len(values) > 8 else None
        nodes.append((ver, level, tflags, type_off, name_off, bsize, index,
                      meta, ref_idx))

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
    for ver, level, tflags, type_off, name_off, bsize, index, meta, ref_idx in nodes:
        node = TypeTreeNode(_str(type_off), _str(name_off), bsize, index,
                            level, meta, [], type_flags=tflags,
                            ref_type_hash=ref_idx)
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
    return root, raw_blob, ref_hashes


def _parse_typetree_info(reader, version, where):
    """Đọc 1 TypeTreeNodeInfoHandler (v23+): hash 16B + u32 size + info.

    Trả (hash, raw_handler_bytes, node|None, ref_hashes). size < 8 nghĩa
    là handler không mang info (node = None — subtree của nó nằm ở type
    khác / bảng shared v26); raw_handler_bytes giữ nguyên vẹn để ghi lại
    byte-identical (gồm cả hash + size)."""
    start = reader.pos
    h = reader.read(16)
    size = reader.i32()
    if size < 0 or size > 10000000:
        raise PB3Error("E_CORRUPT", "type tree handler: size %d lạ" % size,
                       where=where)
    if size >= 8:
        info_start = reader.pos
        node, _raw_info, ref_hashes = _parse_typetree_blob(
            reader, version, where, size)
        if reader.pos - info_start != size:
            raise PB3Error("E_CORRUPT",
                           "type tree handler: info dài %d, khai %d"
                           % (reader.pos - info_start, size), where=where)
    else:
        node, ref_hashes = None, []
    return h, reader._b[start:reader.pos], node, ref_hashes


def _resolve_shared_subtrees(sf, where):
    """Resolve node flag-32 ở v26 — ảnh gương UnityPy
    SerializedFile._resolve_shared_subtrees.

    Mỗi type/ref-type có info: duyệt cây node; node có type_flags & 32 thì
    children = children của subtree chung có hash =
    ref_hashes[ref_type_hash], tra trong bảng shared subtrees của file
    (subtree tự nó lại resolve bằng ref_hashes của chính nó). Thiếu hash,
    chỉ số ngoài khoảng, hoặc chu trình tham chiếu -> E_CORRUPT fail-closed
    (không lặng lẽ để children rỗng). Chỉ gọi ở v26 (UnityPy cũng vậy);
    v23-25 không có bảng shared nên node flag-32 giữ children inline."""
    table = sf._shared_subtree_map or {}
    cache = {}

    def resolve_node(node, ref_hashes, resolving):
        for child in list(node.children):
            resolve_node(child, ref_hashes, resolving)
        if node.type_flags & 32:
            idx = node.ref_type_hash
            if idx is None or idx < 0 or idx >= len(ref_hashes):
                raise PB3Error("E_CORRUPT",
                               "shared subtree v26: node %r tham chiếu "
                               "ref_hashes[%s] không hợp lệ"
                               % (node.name, idx), where=where)
            h = ref_hashes[idx]
            if h in resolving:
                raise PB3Error("E_CORRUPT",
                               "shared subtree v26: chu trình tham chiếu "
                               "hash %s" % h.hex(), where=where)
            if h not in cache:
                entry = table.get(h)
                if entry is None:
                    raise PB3Error("E_CORRUPT",
                                   "shared subtree v26: hash %s không có "
                                   "trong bảng shared subtrees"
                                   % h.hex(), where=where)
                sub_node, sub_hashes = entry
                if sub_node is None:
                    cache[h] = []
                else:
                    cache[h] = resolve_node(sub_node, sub_hashes,
                                            resolving | {h}).children
            node.children = cache[h]
        return node

    for typ in list(sf.types) + list(sf.ref_types):
        if typ.node is not None:
            resolve_node(typ.node, typ.ref_hashes, set())


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
            # UnityPy master (SerializedType + TypeTreeNodeInfoHandler):
            # content hash (16 byte) + kích thước info; size >= 8 mới có
            # info (tối thiểu 4 magic + 4 version), size < 8 nghĩa là type
            # này không mang type tree (node = None). raw_typetree = info
            # bytes (từ "mhtt" tới hết ref hashes) để ghi lại nguyên vẹn.
            h, raw_handler, node, ref_hashes = _parse_typetree_info(
                reader, version, where)
            typ.type_tree_content_hash = h
            typ.node = node
            # info bytes = handler trừ 16 hash + 4 size (rỗng khi size < 8).
            typ.raw_typetree = (raw_handler[20:] if len(raw_handler) > 20
                                else b"")
            typ.ref_hashes = ref_hashes
        elif version >= 12 or version == 10:
            typ.node, typ.raw_typetree, _refs = _parse_typetree_blob(
                reader, version, where)
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
    """Đọc SerializedFile v15-26: header, metadata, object table.

    Header 4 số đầu LUÔN big-endian; phần sau theo cờ endian. v24/25
    layout = v23; v26 thêm mục shared subtrees sau ref types (xem ghi chú
    v24+ ở đầu file)."""
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
    if version >= 26:
        # Mục shared subtrees (UnityPy master, chỉ v26+): i32 count +
        # từng handler; userInformation nằm SAU mục này nên phải đọc
        # đúng thứ tự, nếu không userInformation sẽ đọc sai.
        shared_count = r.i32()
        if shared_count < 0 or shared_count > 100000:
            raise PB3Error("E_CORRUPT", "số shared subtree lạ", where=where)
        sf.shared_subtrees = []
        sf._shared_subtree_map = {}
        for _ in range(shared_count):
            h, raw_handler, node, ref_hashes = _parse_typetree_info(
                r, version, where)
            sf.shared_subtrees.append(raw_handler)
            sf._shared_subtree_map[h] = (node, ref_hashes)
        _resolve_shared_subtrees(sf, where)
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
    if version >= 26:
        # Mục shared subtrees: ghi lại handler bytes nguyên vẹn (hash +
        # size + info) theo thứ tự gốc; count 0 vẫn ghi i32 0.
        meta2.pack("i", len(sf.shared_subtrees))
        for handler in sf.shared_subtrees:
            meta2.write(handler)
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


def write_monobehaviour_new_bytes(sf, pid, changes):
    """Tính object bytes mới cho 1 MonoBehaviour sau khi áp dụng changes.

    Dùng CHUNG cho plan mô phỏng (after_sha) + apply (ghi thật) để hai bên
    luôn đồng ý. Có type tree -> write_monobehaviour_object_bytes (cả ST,
    I2-tt, generic-tt); không type tree -> scan writer nếu changes toàn
    scan[i], byte-walk I2 nếu toàn terms[i].values[j]; trộn 2 loại trong 1
    object -> E_CONFIG_INVALID (không thể xảy ra: 1 object hoặc là bảng I2
    hoặc là generic). Trả (new_obj_bytes, applied)."""
    o = sf.get_object(pid)
    old = sf.object_bytes(pid)
    if sf.types[o.type_id].node is not None:
        return write_monobehaviour_object_bytes(sf, pid, changes)
    fps = [(ch.get("field_path") or "") for ch in changes or []]
    if not fps:
        raise PB3Error("E_CONFIG_INVALID",
                       "patch MonoBehaviour cần changes không rỗng",
                       where=sf.where + "::%d" % pid)
    all_scan = all(re.match(r"^scan\[\d+\]$", fp) for fp in fps)
    all_ls = all(_MB_PATH_RE.match(fp) for fp in fps)
    if all_scan:
        new_obj = write_scanned_monobehaviour_bytes(old, changes, sf.endian)
    elif all_ls:
        new_obj = write_language_source_bytes(old, changes, sf.endian)
    else:
        raise PB3Error("E_CONFIG_INVALID",
                       "changes cho MonoBehaviour không type tree phải toàn "
                       "scan[i] hoặc toàn terms[i].values[j], nhận %r"
                       % (fps,), where=sf.where + "::%d" % pid)
    applied = [{"field_path": ch.get("field_path"), "before": ch.get("before"),
                "after": ch.get("after")} for ch in changes]
    return new_obj, applied


def _patch_monobehaviour_inplace(sf_bytes, sf, path_id, new_obj_data):
    """PATCH_INPLACE cho MonoBehaviour: thay đúng object bytes trong file
    gốc; bắt buộc độ dài không đổi (đệm NUL cuối object do phía gọi thực
    hiện). Header 28 byte đầu (m_GameObject/m_Enabled/m_Script) phải giữ
    nguyên — writer không động vào; lệch thì từ chối thay vì ghi sai."""
    o = sf.get_object(path_id)
    old_obj = sf.object_bytes(path_id)
    if len(new_obj_data) != len(old_obj):
        raise PB3Error("E_WRITE_DISABLED",
                       "PATCH_INPLACE cần độ dài không đổi (%d != %d)"
                       % (len(new_obj_data), len(old_obj)),
                       where=sf.where + "::%d" % path_id)
    if new_obj_data[:_MB_HEADER_SIZE] != old_obj[:_MB_HEADER_SIZE]:
        raise PB3Error("E_CORRUPT",
                       "object mới đổi header MonoBehaviour (28 byte đầu)",
                       where=sf.where + "::%d" % path_id)
    obj_start = sf.data_offset + o.byte_start
    out = bytearray(sf_bytes)
    out[obj_start:obj_start + len(new_obj_data)] = new_obj_data
    return bytes(out)


@dataclass
class MbPatchRequest:
    """Yêu cầu patch 1 MonoBehaviour (language_source / string_table / generic).

    data: bytes file gốc (bundle hoặc assets độc lập). kind: "bundle"|"assets".
    entry: tên entry trong bundle (bắt buộc khi kind="bundle").
    path_id: path_id của MonoBehaviour cần ghi.
    changes: [{field_path, before, after, element?}] — field_path theo
      monobehaviour_write_targets ("terms[i].values[j]" cho bảng I2,
      "m_TableData[i].m_Localized" cho StringTable, "a.b[2].c" cho generic
      có type tree, "scan[i]" cho generic không type tree;
      "arr[+]" kèm "element" để chèn phần tử mảng mới).
    write_mode: "REBUILD_SECTION" (mặc định) hoặc "PATCH_INPLACE" (object
      mới <= object cũ thì đệm NUL cuối object rồi nối tại chỗ, giữ nguyên
      độ dài + offset; dài hơn thì leo thang REBUILD_SECTION nếu
      allow_escalate, không thì từ chối).
    allow_escalate: True (mặc định, như PatchRequest) — PATCH_INPLACE
      không chứa được thì leo REBUILD_SECTION thay vì lỗi.
    backend/limits/temp_dir/from_container/apply/verify: như PatchRequest."""
    data: bytes
    kind: str
    entry: Optional[str] = None
    path_id: int = 0
    changes: Optional[List[dict]] = None
    write_mode: str = "REBUILD_SECTION"
    allow_escalate: bool = True
    backend: Optional[UnityBackend] = None
    limits: Optional[Limits] = None
    temp_dir: Optional[str] = None
    from_container: bool = False
    apply: bool = True
    verify: bool = True


def _verify_mb_values(sf, path_id, changes):
    """Kiểm tra giá trị từng change sau khi ghi (đọc lại object, đối chiếu).

    Typetree: đọc bằng read_monobehaviour_object (tên chuẩn) rồi đi theo
    field_path; "arr[+]" kiểm tra phần tử cuối mảng khớp subset "element".
    Bảng I2: parse_language_source_bytes, đối chiếu terms[i].values[j].
    Trả (ok, [chi tiết])."""
    details = []
    ok = True
    o = sf.get_object(path_id)
    typ = sf.types[o.type_id]
    raw = sf.object_bytes(path_id)
    if typ.node is not None:
        try:
            val = read_monobehaviour_object(sf, path_id)
        except PB3Error as exc:
            return False, ["object %d đọc lại lỗi: %s" % (path_id, exc.code)]
        # Đếm trước tổng số append theo từng mảng: phần tử của change append
        # thứ j (theo thứ tự changes) nằm ở vị trí len(arr)-total+j.
        append_total = {}
        for ch in changes or []:
            fp = ch.get("field_path") or ""
            try:
                parts = _mb_path_parts(fp)
            except ValueError:
                continue
            if parts and parts[-1] == "+":
                key = tuple(str(p) for p in parts[:-1])
                append_total[key] = append_total.get(key, 0) + 1
        append_seen = {}
        for ch in changes or []:
            fp = ch.get("field_path") or ""
            try:
                parts = _mb_path_parts(fp)
            except ValueError as exc:
                return False, ["field_path %r sai cú pháp: %s" % (fp, exc)]
            if parts and parts[-1] == "+":
                try:
                    arr = _mb_path_get(val, parts[:-1],
                                       where=sf.where + "::%d" % path_id)
                except PB3Error:
                    ok = False
                    details.append("field_path %r: không tìm thấy mảng đích "
                                   "sau khi ghi" % fp)
                    continue
                if not isinstance(arr, list) or not arr:
                    ok = False
                    details.append("field_path %r: mảng đích rỗng sau khi ghi"
                                   % fp)
                    continue
                key = tuple(str(p) for p in parts[:-1])
                idx = append_seen.get(key, 0)
                append_seen[key] = idx + 1
                pos = len(arr) - append_total[key] + idx
                last = arr[pos] if 0 <= pos < len(arr) else None
                if last is None:
                    ok = False
                    details.append("field_path %r: không tìm thấy phần tử chèn "
                                   "thứ %d sau khi ghi" % (fp, idx))
                    continue
                element = ch.get("element") or {}
                bad = [k for k, v in element.items()
                       if not (isinstance(last, dict) and last.get(k) == v)]
                if bad:
                    ok = False
                    details.append("field_path %r: phần tử chèn sau khi ghi "
                                   "khác ở field %s" % (fp, bad))
                elif ch.get("after") is not None and isinstance(last, dict):
                    loc = last.get("m_Localized")
                    if loc is not None and loc != ch["after"]:
                        ok = False
                        details.append("field_path %r: m_Localized sau khi ghi "
                                       "là %r, khác %r" % (fp, loc, ch["after"]))
                continue
            try:
                cur = _mb_path_get(val, parts,
                                   where=sf.where + "::%d" % path_id)
            except PB3Error:
                ok = False
                details.append("field_path %r: không tìm thấy sau khi ghi" % fp)
                continue
            want = ch.get("after")
            if isinstance(cur, bytes):
                cur = cur.decode("utf-8", "replace")
            if cur != want:
                ok = False
                details.append("field_path %r sau khi ghi là %r, khác %r"
                               % (fp, cur, want))
        return ok, details
    info = parse_language_source_bytes(raw, sf.endian)
    if info is None:
        # Không phải bảng I2 -> generic scan-path: quét lại, đối chiếu từng slot.
        _n, slots = _scan_monobehaviour_slots(raw, sf.endian)
        for ch in changes or []:
            fp = ch.get("field_path") or ""
            m = re.match(r"^scan\[(\d+)\]$", fp)
            if not m:
                ok = False
                details.append("field_path %r không đúng dạng scan[i]" % fp)
                continue
            i = int(m.group(1))
            if i >= len(slots):
                ok = False
                details.append("field_path %r ngoài object sau khi ghi" % fp)
                continue
            cur = slots[i][3]
            if cur != ch.get("after"):
                ok = False
                details.append("scan[%d] sau khi ghi là %r, khác %r"
                               % (i, cur, ch.get("after")))
        return ok, details
    for ch in changes or []:
        fp = ch.get("field_path") or ""
        m = _MB_PATH_RE.match(fp)
        if not m:
            ok = False
            details.append("field_path %r không đúng dạng terms[i].values[j]"
                           % fp)
            continue
        ti, li = int(m.group(2)), int(m.group(4))
        terms = info.get("terms") or []
        if ti >= len(terms) or li >= len(terms[ti].get("values") or []):
            ok = False
            details.append("field_path %r ngoài bảng I2 sau khi ghi" % fp)
            continue
        cur = terms[ti]["values"][li]
        if cur != ch.get("after"):
            ok = False
            details.append("terms[%d].values[%d] sau khi ghi là %r, khác %r"
                           % (ti, li, cur, ch.get("after")))
    return ok, details


def _mb_object_kind(sf, path_id):
    """Phân loại MonoBehaviour: language_source / language_source_tt /
    string_table / generic."""
    o = sf.get_object(path_id)
    typ = sf.types[o.type_id]
    raw = sf.object_bytes(path_id)
    if typ.node is None:
        if parse_language_source_bytes(raw, sf.endian) is not None:
            return "language_source"
        return "generic"
    try:
        val = read_monobehaviour_object(sf, path_id)
    except PB3Error:
        return "generic"
    if "m_TableData" in val and "m_LocaleId" in val:
        return "string_table"
    if language_source_from_fields(val) is not None:
        return "language_source_tt"
    return "generic"


def apply_monobehaviour_patch(req):
    """Patch 1 MonoBehaviour bằng REBUILD_SECTION hoặc PATCH_INPLACE; trả
    PatchResult (đã verify).

    Đơn vị đầu ra nhỏ nhất như apply_patch: assets độc lập -> chính file
    đó; trong bundle -> chỉ bundle đó (KHÔNG đóng gói lại IPA ngoài).
    PATCH_INPLACE: object mới <= object cũ thì đệm NUL cuối object rồi nối
    tại chỗ (giữ nguyên độ dài + offset + object table); dài hơn thì leo
    thang REBUILD_SECTION nếu allow_escalate, không thì từ chối. REBUILD_ALL
    bị từ chối với lý do rõ ràng. Verify 2 lớp: cấu trúc (_verify_assets:
    chỉ đúng object đổi, metadata giữ nguyên, thứ tự giữ nguyên) + giá trị
    từng change (_verify_mb_values: đọc lại object, đối chiếu từng field).
    Không đạt -> E_VERIFY_FAILED, bản ứng viên giữ tại temp_dir nếu có."""
    if req.write_mode not in ("REBUILD_SECTION", "PATCH_INPLACE"):
        raise PB3Error("E_WRITE_DISABLED",
                       "MonoBehaviour chỉ hỗ trợ REBUILD_SECTION/PATCH_INPLACE "
                       "(nhận %s); REBUILD_ALL chưa hỗ trợ cho MonoBehaviour"
                       % req.write_mode,
                       where=req.entry or req.kind)
    backend = req.backend or UnityBackend("AUTO", req.limits)
    lim = req.limits or backend.limits
    data = bytes(req.data)
    changes = req.changes or []
    if not changes:
        raise PB3Error("E_CONFIG_INVALID", "patch MonoBehaviour cần changes "
                       "không rỗng", where=req.entry or req.kind)
    if req.path_id is None:
        raise PB3Error("E_CONFIG_INVALID", "patch MonoBehaviour cần path_id "
                       "(path_id là i64 có dấu, âm vẫn hợp lệ)",
                       where=req.entry or req.kind)
    if req.kind == "bundle":
        if not req.entry:
            raise PB3Error("E_CONFIG_INVALID", "patch trong bundle cần tên entry",
                           where=req.kind)
        bundle = parse_bundle(data, backend, lim, where="bundle")
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
    pid = req.path_id
    try:
        o = sf.get_object(pid)
    except PB3Error:
        raise PB3Error("E_CONFIG_INVALID", "không có object path_id=%d" % pid,
                       where=req.entry or req.kind)
    if o.class_id != 114:
        raise PB3Error("E_CONFIG_INVALID",
                       "object path_id=%d không phải MonoBehaviour (class %d)"
                       % (pid, o.class_id), where=req.entry or req.kind)
    old_obj_data = sf.object_bytes(pid)
    name = read_monobehaviour_name(old_obj_data, sf.endian) or ""
    kind = _mb_object_kind(sf, pid)
    new_obj_data, applied = write_monobehaviour_new_bytes(sf, pid, changes)
    pad_note = []
    mode = req.write_mode
    if mode == "PATCH_INPLACE" and len(new_obj_data) > len(old_obj_data):
        if req.allow_escalate:
            mode = "REBUILD_SECTION"
            pad_note.append("object mới dài hơn %d byte: PATCH_INPLACE không "
                            "chứa được, chuyển REBUILD_SECTION."
                            % (len(new_obj_data) - len(old_obj_data)))
        else:
            raise PB3Error("E_WRITE_DISABLED",
                           "object mới dài hơn %d byte; PATCH_INPLACE từ chối "
                           "(không cắt cụt). Bật REBUILD_SECTION."
                           % (len(new_obj_data) - len(old_obj_data)),
                           where=req.entry or req.kind)
    if mode == "PATCH_INPLACE":
        pad = len(old_obj_data) - len(new_obj_data)
        padded = new_obj_data + b"\x00" * pad
        if (kind == "language_source"
                and parse_language_source_bytes(padded, sf.endian) is None):
            # Bảng I2 byte-walk giới hạn trailing <= 65536: đệm quá lớn thì
            # parse lại sẽ lỗi — leo thang thay vì để verify thất bại.
            if req.allow_escalate:
                mode = "REBUILD_SECTION"
                pad_note.append("đệm NUL %d byte làm bảng I2 vượt giới hạn "
                                "trailing: chuyển REBUILD_SECTION." % pad)
            else:
                raise PB3Error("E_WRITE_DISABLED",
                               "PATCH_INPLACE cần đệm %d byte, vượt giới hạn "
                               "trailing của bảng I2; bật REBUILD_SECTION."
                               % pad, where=req.entry or req.kind)
        if mode == "PATCH_INPLACE":
            if pad:
                pad_note.append("object mới ngắn hơn %d byte: đệm NUL cuối "
                                "object (giữ nguyên độ dài + offset)" % pad)
            new_obj_data = padded
    if req.apply:
        if mode == "PATCH_INPLACE":
            new_assets = _patch_monobehaviour_inplace(assets_bytes, sf, pid,
                                                      new_obj_data)
        else:
            new_assets = _rebuild_assets_section(sf, pid, {pid: new_obj_data})
    else:
        new_assets = assets_bytes
    if unit == "bundle":
        new_entries = []
        for e in bundle.entries:
            new_entries.append((e.name, e.flags,
                                new_assets if e.name == req.entry
                                else bundle.get_entry(e.name)))
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
    # ---- diffverify: cấu trúc + giá trị (chỉ khi đã áp dụng; dry-run
    # apply=False thì output = input, không có gì để verify)
    verify_details = []
    verified = True
    if req.verify and req.apply:
        if unit == "assets":
            after_sf = parse_serialized(out_bytes, lim, where="assets::after")
        else:
            after_bundle = parse_bundle(out_bytes, backend, lim,
                                        where="bundle::after")
            after_assets = after_bundle.get_entry(req.entry)
            after_sf = parse_serialized(after_assets, lim,
                                        where="bundle::after::assets")
        ok, details = _verify_assets(_snapshot_assets(sf), after_sf, {pid},
                                     {}, mode)
        verify_details.extend(details)
        verified = ok
        ok2, details2 = _verify_mb_values(after_sf, pid, changes)
        verify_details.extend(details2)
        verified = verified and ok2
        if unit == "bundle":
            after_snap = _snapshot_bundle(after_bundle)
            changed_entries = {n for n in before_snap["entries"]
                               if before_snap["entries"].get(n)
                               != after_snap["entries"].get(n)}
            changed_entries.update(n for n in after_snap["entries"]
                                   if n not in before_snap["entries"])
            if changed_entries != {req.entry}:
                verified = False
                verify_details.append("entry bundle đổi %s != dự kiến {%s}"
                                      % (sorted(changed_entries), req.entry))
            if before_snap["order"] != after_snap["order"]:
                verified = False
                verify_details.append("thứ tự entry bundle thay đổi")
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
    if not req.apply:
        verify_details.append("dry-run: chưa áp dụng (output = input)")
        verified = False
    if req.from_container and unit == "bundle":
        pad_note.append("đơn vị là bundle trong container/IPA: sau khi thay file "
                        "cần ký lại (re-sign) gói ngoài thì game mới nhận "
                        "(đặc tả 11.1); PB3 không tự ký lại.")
    report = PatchReport(
        target="%s%s path_id=%d (%s, %s)" % (unit, ("::" + req.entry) if req.entry else "",
                                            pid, name, kind),
        write_mode=req.write_mode, write_mode_used=mode,
        backend_mode=backend.mode,
        backend_decisions=_dedupe(backend.decisions),
        warnings=pad_note,
        changed=[req.entry] if unit == "bundle" else ["path_id=%d" % pid],
        size_before=len(data), size_after=len(out_bytes),
        old_script_len=len(old_obj_data), new_script_len=len(new_obj_data),
        occurrences=len(applied))
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
