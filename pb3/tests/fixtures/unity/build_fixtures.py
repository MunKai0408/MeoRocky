#!/usr/bin/env python3
"""Sinh fixture Unity hợp lệ cho Claude 05/05b (chạy 1 lần, file sinh ra được commit).

- UnityFS v6/v7/v8 (không nén / LZ4 / LZMA), SerializedFile v17/v21/v23
  (không type tree / có type tree blob), bundle lồng trong IPA.
- UnityFS v9 + Zstd (mục C5): v9 chỉ đọc — mỗi BlockInfo thêm u64
  offset, block nén căn 16; Zstd (mã nén 5) qua gói `zstandard` (chỉ
  cần lúc SINH fixture, không cần lúc chạy test — file sinh ra được
  commit). Thiếu `zstandard` thì main() bỏ qua fixture Zstd và in rõ
  lý do, các fixture khác vẫn sinh đủ.
- Layout đối chiếu UnityPy 1.25.4 (files/BundleFile.py,
  files/SerializedFile.py, files/ObjectReader.py,
  helpers/TypeTreeNode.py): bundle big-endian, SerializedFile
  little-endian, TextAsset = aligned_string(m_Name) + aligned_string(m_Script).
- v23: type tree có content hash 16 byte + kích thước blob; blob mở đầu
  bằng magic "mhtt" + format version, node 32 byte (thêm Q ref hash từ v19).
  Bundle v8: player "5.x.x" + revision "6000.0.23f1" (Unity 6).
- Chỉ dùng thư viện chuẩn + `lz4` (cho fixture LZ4); không dùng UnityPy
  để sinh (để UnityPy đóng vai trò kiểm chứng độc lập sau đó).

Chạy:  python3 tests/fixtures/unity/build_fixtures.py
"""
from __future__ import annotations

import hashlib
import io
import lzma
import os
import struct
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

UNITY_VERSION = "2019.4.40f1"
OLD_UNITY_VERSION = "5.6.7f1"
UNITY6_PLAYER_VERSION = "5.x.x"   # unityVersion trong header bundle Unity 6
UNITY6_REVISION = "6000.0.23f1"   # unityRevision (engine) của fixture v8/v23


def _parse_version(text):
    """'6000.0.23f1' -> (6000, 0, 23); lạ -> ()."""
    import re
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ()

LONG_TEXT = (
    "Dòng dài để thử chuỗi dài: " + "xin chào thế giới! " * 200
    + "kết thúc. " + "0123456789abcdef" * 100
)
JSON_TEXT = '{"level": 1, "lang": "vi", "title": "9 Kings"}'


# ------------------------------------------------------------------ tiện ích
def _align_up(n, alignment):
    return (n + alignment - 1) // alignment * alignment


def _pad_to(buf, alignment, base=0):
    """Đệm NUL để (base + len(buf)) chia hết cho alignment."""
    need = (-(base + len(buf))) % alignment
    if need:
        buf += b"\x00" * need
    return buf


def aligned_string(text):
    """Chuỗi SerializedFile: i32 độ dài + UTF-8 + đệm tới bội 4 (tương đối)."""
    raw = text.encode("utf-8")
    out = struct.pack("<i", len(raw)) + raw
    return _pad_to(out, 4)


def text_asset_data(name, script):
    return aligned_string(name) + aligned_string(script)


# ------------------------------------------------------- SerializedFile
def build_serialized_file(version=17, unity_version=UNITY_VERSION,
                          objects=(), types=(), enable_type_tree=False,
                          typetree_blobs=(), target_platform=9,
                          shared_subtrees=(), user_information=b""):
    """objects: [(path_id, type_id, data)]; types: [(class_id, script_id_or_None)].
    typetree_blobs: {type_index: bytes} khi enable_type_tree.
    shared_subtrees: [handler bytes] chi cho version >= 26 (muc sau ref types,
    theo UnityPy master: i32 count + count x (hash 16B + u32 size + info)).
    user_information: bytes (ghi sau muc shared subtrees, truoc khi ket thuc
    metadata) — mac dinh b"" (1 byte NUL)."""
    header_size = 48 if version >= 22 else 20
    meta = io.BytesIO()
    meta.write(unity_version.encode("utf-8") + b"\x00")
    meta.write(struct.pack("<i", target_platform))
    if version >= 13:
        meta.write(b"\x01" if enable_type_tree else b"\x00")
    meta.write(struct.pack("<i", len(types)))
    for type_index, (class_id, script_id) in enumerate(types):
        meta.write(struct.pack("<i", class_id))
        if version >= 16:
            meta.write(b"\x00")  # is_stripped_type
        if version >= 17:
            meta.write(struct.pack("<h", -1))  # script_type_index
        if version >= 13:
            if (version >= 16 and class_id == 114) and script_id is not None:
                meta.write(script_id)
            meta.write(b"\x00" * 16)  # old_type_hash
        if enable_type_tree:
            blob = typetree_blobs.get(type_index)
            if blob is not None:
                if version >= 23:
                    # v23: content hash (XXH3, 16 byte 0) + kích thước blob
                    meta.write(b"\x00" * 16)
                    meta.write(struct.pack("<i", len(blob)))
                meta.write(blob)
            elif version >= 23:
                # type khong co type tree: hash + size 0 (info None).
                meta.write(b"\x00" * 16)
                meta.write(struct.pack("<i", 0))
            if version >= 21:
                # type_dependencies (rỗng) cho type thường; reader chỉ đọc
                # mục này khi enable_type_tree (theo UnityPy).
                meta.write(struct.pack("<i", 0))
    # object table: cần byte_start/byte_size -> dựng data trước
    data = io.BytesIO()
    entries = []
    for path_id, type_id, payload in sorted(objects, key=lambda o: o[0]):
        data.seek(_align_up(data.tell(), 8))
        start = data.tell()
        data.write(payload)
        entries.append((path_id, start, len(payload), type_id))
    data_bytes = data.getvalue()
    meta.write(struct.pack("<i", len(entries)))
    for path_id, start, size, type_id in entries:
        # căn 4 theo vị trí tuyệt đối (header + vị trí trong meta)
        meta.seek(_align_up(meta.tell() + header_size, 4) - header_size)
        meta.write(struct.pack("<q", path_id))
        if version >= 22:
            meta.write(struct.pack("<Q", start))  # byte_start u64 từ v22
        else:
            meta.write(struct.pack("<I", start))
        meta.write(struct.pack("<I", size))
        meta.write(struct.pack("<i", type_id))
    if version >= 11:
        meta.write(struct.pack("<i", 0))  # script_types
    meta.write(struct.pack("<i", 0))  # externals
    if version >= 20:
        meta.write(struct.pack("<i", 0))  # ref_types
    if version >= 26:
        # muc shared subtrees (UnityPy master, chi v26+): i32 count +
        # count x handler (hash 16B + u32 size + info bytes).
        meta.write(struct.pack("<i", len(shared_subtrees)))
        for handler in shared_subtrees:
            meta.write(handler)
    meta.write(user_information + b"\x00")  # userInformation
    meta_bytes = meta.getvalue()
    data_offset = _align_up(header_size + len(meta_bytes), 16)
    file_size = data_offset + len(data_bytes)
    out = io.BytesIO()
    # 4 số đầu của header LUÔN big-endian (reader mặc định của Unity là '>');
    # phần sau header mới theo cờ endian (0 = little).
    if version >= 22:
        # extended header: 4 số đầu ghi 0 (giá trị thật nằm ở phần mở rộng).
        # CẢ 48 byte đầu đều big-endian (theo UnityPy: SerializedFile.save
        # ghi header bằng EndianBinaryWriter() mặc định ">"); chỉ metadata
        # + data mới theo cờ endian (little ở đây).
        out.write(struct.pack(">IIII", 0, 0, version, 0))
        out.write(b"\x00" + b"\x00" * 3)  # endian little + reserved
        out.write(struct.pack(">I", len(meta_bytes)))
        out.write(struct.pack(">Q", file_size))
        out.write(struct.pack(">Q", data_offset))
        out.write(struct.pack(">Q", 0))  # unknown
    else:
        out.write(struct.pack(">IIII", len(meta_bytes), file_size, version,
                               data_offset))
        out.write(b"\x00" + b"\x00" * 3)  # endian little + reserved
    out.write(meta_bytes)
    out.seek(data_offset)
    out.write(data_bytes)
    return out.getvalue()


def typetree_blob(root_type, children, version=17, ref_hashes=(),
                  node_flags=None, byte_sizes=None):
    """Blob type tree: nút gốc root_type/Base; con là (type, name).

    Node '<hBBIIiii' (24 byte) tới v18, thêm Q ref hash (32 byte) từ v19
    (theo UnityPy helpers/TypeTreeNode._get_blob_node_struct); v23+ mở đầu
    bằng magic "mhtt" + format version (TypeTreeNode.parse_blob) và KẾT
    THÚC bằng u32 ref_hash_count + ref_hashes 16 byte (UnityPy
    TypeTreeNodeInfo — kể cả khi count = 0; fixture v23 cũ của repo thiếu
    phần đuôi này, parser mới chấp nhận cả 2 dạng nhưng builder này luôn
    ghi dạng chuẩn UnityPy).
    ref_hashes: [bytes 16] — hash các subtree mà node flag-32 tham chiếu.
    node_flags: {node_index: (type_flags, ref_type_hash_index)} — node_index
    0 = gốc, 1.. = con theo thứ tự `children`; mặc định (0, 0).
    byte_sizes: {node_index: byte_size} — mặc định -1 (struct/chưa rõ)."""
    names = [root_type, "Base"]
    for t, n in children:
        names += [t, n]
    names = list(dict.fromkeys(names))  # mỗi chuỗi một lần (off() tìm lần đầu)
    strings = b"".join(s.encode("utf-8") + b"\x00" for s in names)

    def off(name):
        return strings.index(name.encode("utf-8"))

    fmt = "<hBBIIiii" + ("Q" if version >= 19 else "")
    node_flags = node_flags or {}
    byte_sizes = byte_sizes or {}

    def node_tuple(idx, level, t, n, index=0):
        flags, ref_idx = node_flags.get(idx, (0, 0))
        base = (1, level, flags, off(t), off(n), byte_sizes.get(idx, -1),
                index, 0)
        if version >= 19:
            base = base + (ref_idx,)
        return base

    nodes = [node_tuple(0, 0, root_type, "Base")]
    for i, (t, n) in enumerate(children):
        nodes.append(node_tuple(i + 1, 1, t, n, index=i))
    body = b"".join(struct.pack(fmt, *n) for n in nodes)
    blob = struct.pack("<ii", len(nodes), len(strings)) + body + strings
    if version >= 23:
        blob = b"mhtt" + struct.pack("<i", version) + blob
        blob += struct.pack("<I", len(ref_hashes))
        for h in ref_hashes:
            assert len(h) == 16, "ref hash phai dai 16 byte"
            blob += h
    return blob


def typetree_handler(hash_bytes, info_bytes):
    """Handler v23+/v26 (UnityPy TypeTreeNodeInfoHandler): hash 16B + u32
    size + info bytes (None -> size 0, type khong co type tree)."""
    assert len(hash_bytes) == 16, "handler hash phai dai 16 byte"
    if info_bytes is None:
        return hash_bytes + struct.pack("<I", 0)
    return hash_bytes + struct.pack("<I", len(info_bytes)) + info_bytes


def typetree_blob_text_asset():
    """Blob type tree cho TextAsset (SF v17, struct '<hBBIIiii', 24 byte/node).

    Nút gốc TextAsset/Base; con m_Name (string), m_Script (string)."""
    return typetree_blob("TextAsset",
                         [("string", "m_Name"), ("string", "m_Script")],
                         version=17)


def typetree_blob_mono():
    """Blob type tree cho MonoBehaviour (dùng cho fixture v23)."""
    return typetree_blob("MonoBehaviour",
                         [("string", "m_Name"),
                          ("PPtr<MonoScript>", "m_Script")],
                         version=23)


# ------------------------------------------------------------- Unity LZMA
def compress_lzma_unity(data):
    """LZMA1 thô kiểu Unity: 0x5D + dict 0x800000 + dữ liệu nén."""
    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": 0x800000,
             "lc": 3, "lp": 0, "pb": 2}]
    comp = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filt)
    return struct.pack("<BI", 0x5D, 0x800000) + comp.compress(data) + comp.flush()


# ---------------------------------------------------------------- UnityFS
def _lz4_block(data):
    import lz4.block
    return lz4.block.compress(data, store_size=False)


def _compress_zstd(data):
    """Nén Zstd bằng gói `zstandard` (chỉ có lúc sinh fixture)."""
    try:
        import zstandard
    except ImportError:
        raise RuntimeError("sinh fixture Zstd cần gói `zstandard` "
                           "(pip install zstandard); chưa cài nên bỏ qua")
    return zstandard.ZstdCompressor(level=3).compress(data)


def _compress(data, kind):
    if kind == "none":
        return data
    if kind == "lz4":
        return _lz4_block(data)
    if kind == "lzma":
        return compress_lzma_unity(data)
    if kind == "zstd":
        return _compress_zstd(data)
    raise ValueError(kind)


def build_bundle(version=7, unity_version=UNITY_VERSION, entries=(),
                 compression="none", blocks_info_at_end=False,
                 unity_revision=None, extra_data_flags=0, pad_block_data=False,
                 block_split=None):
    """entries: [(name, flags, data)]. compression: none|lz4|lzma|zstd.

    Trả bytes UnityFS. data_flag: 0x40 (combined) | comp; +0x80 nếu
    blocks info ở cuối. Block flags: 64 (không nén, theo UnityPy) / 1 / 2 / 5.
    `unity_revision` mặc định = `unity_version`; `pad_block_data` đặt cờ
    0x200 và căn đầu vùng block data tới 16 (cờ mới, Unity 6).
    v9 (C5): mỗi BlockInfo thêm u64 offset; mỗi block trong vùng block
    data được căn 16 (đệm NUL sau từng block). `block_split` (byte) chia
    payload thành nhiều block để thử offset/căn — mặc định 1 block
    (trừ LZ4 chia 128 KiB như trước)."""

    if unity_revision is None:
        unity_revision = unity_version
    comp_code = {"none": 0, "lz4": 2, "lzma": 1, "zstd": 5}[compression]
    data_flag = 0x40 | comp_code | extra_data_flags
    if blocks_info_at_end:
        data_flag |= 0x80
    if pad_block_data:
        data_flag |= 0x200
    payload = b"".join(d for _, _, d in entries)
    # chia block: LZ4 theo chunk 128 KiB, còn lại 1 block (hoặc chia theo
    # block_split khi gọi tường minh — dùng cho fixture v9/Zstd).
    raw_blocks = []
    if compression == "lz4":
        step = 0x20000
        for i in range(0, len(payload), step):
            raw_blocks.append(payload[i:i + step])
    elif block_split:
        for i in range(0, len(payload), block_split):
            raw_blocks.append(payload[i:i + block_split])
    else:
        raw_blocks = [payload] if payload else []
    blocks = []
    block_data = io.BytesIO()
    for chunk in raw_blocks:
        comp = _compress(chunk, compression)
        if len(comp) >= len(chunk) and compression != "none":
            block_data.write(chunk)
            off = block_data.tell() - len(chunk)
            blocks.append((len(chunk), len(chunk), 0, off))
        else:
            off = block_data.tell()
            block_data.write(comp)
            flag = 64 if compression == "none" else comp_code
            blocks.append((len(chunk), len(comp), flag, off))
        if version >= 9:
            # v9 (C5, theo UnityPy chunk_based_compress): căn từng block
            # nén tới 16 byte trong vùng block data.
            block_data.write(b"\x00" * ((-block_data.tell()) % 16))
    block_bytes = block_data.getvalue()
    # blocks info (chưa nén)
    info = io.BytesIO()
    info.write(b"\x00" * 16)  # hash
    info.write(struct.pack(">i", len(blocks)))
    for unc, comp_size, flag, off in blocks:
        info.write(struct.pack(">IIH", unc, comp_size, flag))
        if version >= 9:
            info.write(struct.pack(">Q", off))
    info.write(struct.pack(">i", len(entries)))
    offset = 0
    for name, flags, data in entries:
        info.write(struct.pack(">qqi", offset, len(data), flags))
        info.write(name.encode("utf-8") + b"\x00")
        offset += len(data)
    info_raw = info.getvalue()
    info_comp = _compress(info_raw, compression)
    # header
    out = io.BytesIO()
    out.write(b"UnityFS\x00")
    out.write(struct.pack(">I", version))
    out.write(unity_version.encode("utf-8") + b"\x00")
    out.write(unity_revision.encode("utf-8") + b"\x00")
    header_pos = out.tell()
    out.write(struct.pack(">QIII", 0, len(info_comp), len(info_raw), data_flag))
    if version >= 7 or _parse_version(unity_revision) >= (2019, 4, 15):
        out.seek(_align_up(out.tell(), 16))
    if blocks_info_at_end:
        if pad_block_data:
            out.seek(_align_up(out.tell(), 16))
        out.write(block_bytes)
        out.write(info_comp)
    else:
        out.write(info_comp)
        if pad_block_data:
            out.seek(_align_up(out.tell(), 16))
        out.write(block_bytes)
    total = out.tell()
    out.seek(header_pos)
    out.write(struct.pack(">Q", total))
    return out.getvalue()


def build_nested_ipa(bundle_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Payload/Game.app/Info.plist",
                    b'<?xml version="1.0"?><plist><dict><key>CFBundleName</key>'
                    b'<string>9Kings</string></dict></plist>')
        zf.writestr("Payload/Game.app/Data/level.unity3d", bundle_bytes)
        zf.writestr("Payload/Game.app/Data/config.txt", b"level=1\n")
    return buf.getvalue()


def main():
    os.makedirs(HERE, exist_ok=True)
    text_asset = (49, None)
    mono = (114, b"\x00" * 16)
    # resources.assets: 2 TextAsset (ngắn + dài) + 1 MonoBehaviour mờ
    resources = build_serialized_file(
        version=17, objects=[
            (1001, 0, text_asset_data("greeting", "Xin chao 9 Kings!")),
            (1002, 0, text_asset_data("longtext", LONG_TEXT)),
            (1003, 1, aligned_string("DummyBehaviour") + b"MB-DATA" + bytes(range(32))),
        ], types=[text_asset, mono])
    shared = build_serialized_file(
        version=17, objects=[
            (2001, 0, text_asset_data("config", JSON_TEXT)),
        ], types=[text_asset])
    # typetree: cùng nội dung, có blob type tree cho TextAsset
    blob = typetree_blob_text_asset()
    resources_tt = build_serialized_file(
        version=17, enable_type_tree=True,
        objects=[(1001, 0, text_asset_data("greeting", "Xin chao 9 Kings!"))],
        types=[text_asset], typetree_blobs={0: blob})
    v21 = build_serialized_file(
        version=21, objects=[
            (3001, 0, text_asset_data("v21text", "SerializedFile v21 ok")),
        ], types=[text_asset])
    # v23 (Unity 6): type tree có hash + size, blob mở đầu "mhtt"
    blob_v23_text = typetree_blob(
        "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
        version=23)
    blob_v23_mono = typetree_blob_mono()
    resources_v23 = build_serialized_file(
        version=23, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao Unity 6!")),
            (4002, 0, text_asset_data("longtext", LONG_TEXT)),
            (4003, 1, aligned_string("DummyBehaviour6") + b"MB-DATA"
             + bytes(range(32))),
        ], types=[text_asset, mono],
        typetree_blobs={0: blob_v23_text, 1: blob_v23_mono})
    shared_v23 = build_serialized_file(
        version=23, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4004, 0, text_asset_data("config", JSON_TEXT)),
        ], types=[text_asset], typetree_blobs={0: blob_v23_text})
    # v24/v25: layout giống hệt v23 (UnityPy master không có nhánh 24/25).
    resources_v24 = build_serialized_file(
        version=24, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao Unity 6!")),
            (4002, 0, text_asset_data("longtext", LONG_TEXT)),
        ], types=[text_asset],
        typetree_blobs={0: typetree_blob(
            "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
            version=24)})
    resources_v25 = build_serialized_file(
        version=25, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao Unity 6!")),
        ], types=[text_asset],
        typetree_blobs={0: typetree_blob(
            "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
            version=25)})
    # v23 với ref_hashes (dạng chuẩn UnityPy: type tham chiếu 1 subtree;
    # node flag-32 vẫn giữ con inline ở v23 — bảng shared chỉ có từ v26).
    blob_v23_sharedref = typetree_blob(
        "MonoBehaviour", [("string", "m_Name")], version=23,
        ref_hashes=[b"shared-subtree#1"], node_flags={1: (32, 0)})
    resources_v23_sharedref = build_serialized_file(
        version=23, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao Unity 6!")),
            (4005, 1, aligned_string("SharedRefBehaviour")),
        ], types=[text_asset, mono],
        typetree_blobs={0: blob_v23_text, 1: blob_v23_sharedref})
    # v26: shared subtree "SharedData" (m_Score/m_Level int) + type
    # MonoBehaviour có node flag-32 tham chiếu nó (không con inline).
    shared_data_info = typetree_blob(
        "SharedData", [("int", "m_Score"), ("int", "m_Level")], version=26,
        byte_sizes={1: 4, 2: 4})
    shared_handler = typetree_handler(b"shared-subtree#1", shared_data_info)
    blob_v26_mono = typetree_blob(
        "MonoBehaviour", [("SharedData", "m_Shared")], version=26,
        ref_hashes=[b"shared-subtree#1"], node_flags={1: (32, 0)})
    resources_v26 = build_serialized_file(
        version=26, unity_version=UNITY6_REVISION, enable_type_tree=True,
        objects=[
            (4001, 0, text_asset_data("greeting", "Xin chao Unity 6!")),
            (4006, 1, struct.pack("<ii", 100, 5)),
        ], types=[text_asset, mono],
        typetree_blobs={0: typetree_blob(
            "TextAsset", [("string", "m_Name"), ("string", "m_Script")],
            version=26), 1: blob_v26_mono},
        shared_subtrees=[shared_handler],
        user_information=b"v26-fixture")
    entries = [
        ("CAB-0123456789abcdef0123456789abcdef", 4, resources),
        ("CAB-0123456789abcdef0123456789abcde0", 4, shared),
    ]
    entries_v8 = [
        ("CAB-0123456789abcdef0123456789abcdef", 4, resources_v23),
        ("CAB-0123456789abcdef0123456789abcde0", 4, shared_v23),
    ]
    bundle = build_bundle(version=7, entries=entries, compression="none")
    bundle_lz4 = build_bundle(version=7, entries=entries, compression="lz4",
                              blocks_info_at_end=True)
    bundle_lzma = build_bundle(version=7, entries=entries, compression="lzma")
    bundle_v6 = build_bundle(version=6, unity_version=OLD_UNITY_VERSION,
                             entries=entries, compression="none")
    bundle_v8 = build_bundle(version=8, unity_version=UNITY6_PLAYER_VERSION,
                             unity_revision=UNITY6_REVISION,
                             entries=entries_v8, compression="none")
    bundle_v8_lz4 = build_bundle(
        version=8, unity_version=UNITY6_PLAYER_VERSION,
        unity_revision=UNITY6_REVISION, entries=entries_v8, compression="lz4",
        blocks_info_at_end=True, pad_block_data=True)
    # v9 (C5, chỉ đọc): player "5.x.x" + revision Unity 6, 2 block không
    # nén — block thứ 2 bắt đầu ở offset đã căn 16 (thử đọc bằng offset,
    # không đọc tuần tự). Payload v23 để Inspect hiện đủ TextAsset.
    payload_v8 = b"".join(d for _, _, d in entries_v8)
    bundle_v9 = build_bundle(
        version=9, unity_version=UNITY6_PLAYER_VERSION,
        unity_revision=UNITY6_REVISION, entries=entries_v8, compression="none",
        block_split=(len(payload_v8) + 1) // 2)
    files = {
        "resources.assets": resources,
        "sharedassets0.assets": shared,
        "sample_typetree.assets": resources_tt,
        "sample_v21.assets": v21,
        "sample_v23.assets": resources_v23,
        "sample_v24.assets": resources_v24,
        "sample_v25.assets": resources_v25,
        "sample_v23_sharedref.assets": resources_v23_sharedref,
        "sample_v26.assets": resources_v26,
        "sample.unity3d": bundle,
        "sample_lz4.unity3d": bundle_lz4,
        "sample_lzma.unity3d": bundle_lzma,
        "sample_v6.unity3d": bundle_v6,
        "sample_v8.unity3d": bundle_v8,
        "sample_v8_lz4.unity3d": bundle_v8_lz4,
        "sample_v9.unity3d": bundle_v9,
        "nested.ipa": build_nested_ipa(bundle),
        "nested_v8.ipa": build_nested_ipa(bundle_v8),
    }
    # Zstd (C5): cần gói `zstandard` lúc sinh; thiếu thì bỏ qua 2 file
    # này và in rõ lý do (không làm hỏng các fixture khác).
    try:
        import zstandard  # noqa: F401 - chỉ kiểm tra có mặt
        bundle_zstd = build_bundle(version=7, entries=entries,
                                   compression="zstd")
        bundle_v9_zstd = build_bundle(
            version=9, unity_version=UNITY6_PLAYER_VERSION,
            unity_revision=UNITY6_REVISION, entries=entries_v8,
            compression="zstd", block_split=(len(payload_v8) + 1) // 2)
        files["sample_zstd.unity3d"] = bundle_zstd
        files["sample_v9_zstd.unity3d"] = bundle_v9_zstd
    except ImportError:
        print("BO QUA fixture Zstd: chua cai goi `zstandard` "
              "(pip install zstandard) — sample_zstd.unity3d / "
              "sample_v9_zstd.unity3d khong duoc sinh lai.")
    for name, data in files.items():
        with open(os.path.join(HERE, name), "wb") as fh:
            fh.write(data)
        print("%-24s %7d byte  sha256=%s" % (
            name, len(data), hashlib.sha256(data).hexdigest()[:16]))


if __name__ == "__main__":
    main()
