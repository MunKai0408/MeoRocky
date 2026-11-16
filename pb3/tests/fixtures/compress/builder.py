"""Builder dựng IPA giả nhỏ cho test nén (không cần IPA thật).

IPA giả gồm: entry nén được (văn bản tiếng Việt lặp), entry đã nén sẵn/khó
nén (byte ngẫu nhiên), entry STORED, thư mục, đường dẫn lồng nhau. Mọi test
dựng fixture trong thư mục tạm, không dùng file nhị phân check-in.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile

COMPRESSIBLE_TEXT = (
    "Người chơi thu thập vàng để nâng cấp tháp phòng thủ. "
    "Nhiệm vụ mới đã mở khóa trong vương quốc phía bắc. "
    "Kẻ địch tấn công từ cổng thành vào lúc nửa đêm. "
    "Hãy nâng cấp vũ khí trước khi bước vào hầm ngục. "
)
DATE_TIME = (2024, 5, 6, 7, 8, 9)


def make_ipa_bytes(specs, *, default_method=zipfile.ZIP_DEFLATED) -> bytes:
    """specs: [(tên, bytes)] hoặc [(tên, bytes, method)]. Thư mục: tên kết thúc '/'."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", default_method) as zf:
        for item in specs:
            name, data = item[0], item[1]
            method = item[2] if len(item) > 2 else default_method
            info = zipfile.ZipInfo(name, DATE_TIME)
            info.compress_type = method
            info.external_attr = (0o40755 << 16) | 0o755 if name.endswith("/") \
                else (0o644 << 16)
            info.create_system = 3
            zf.writestr(info, data)
    return buf.getvalue()


def sample_ipa_bytes() -> bytes:
    """IPA giả mẫu: 2 entry văn bản nén được + 1 entry ngẫu nhiên khó nén +
    1 entry STORED + 1 thư mục + 1 file JSON nhỏ."""
    text = (COMPRESSIBLE_TEXT * 400).encode("utf-8")
    random_blob = os.urandom(64 * 1024)
    return make_ipa_bytes([
        ("Payload/Game.app/Info.plist",
         b'<?xml version="1.0"?><plist><dict><key>CFBundleName</key>'
         b'<string>Game</string></dict></plist>'),
        ("Payload/Game.app/Data/text1.txt", text),
        ("Payload/Game.app/Data/text2.txt", text),
        ("Payload/Game.app/Data/blob.bin", random_blob),
        ("Payload/Game.app/Data/raw.bin", b"ABCD" * 8192, zipfile.ZIP_STORED),
        ("Payload/Game.app/Data/", b""),
        ("config.json", b'{"game": "Game", "version": "1.0.5"}'),
    ])


def compressible_ipa_bytes(n_text: int = 3, repeats: int = 400) -> bytes:
    """IPA chỉ chứa văn bản nén được (để đo tỉ lệ nén solid)."""
    text = (COMPRESSIBLE_TEXT * repeats).encode("utf-8")
    return make_ipa_bytes([
        ("Payload/Game.app/Data/text%d.txt" % i, text) for i in range(n_text)
    ])


def random_ipa_bytes(size: int = 64 * 1024) -> bytes:
    """IPA chỉ chứa byte ngẫu nhiên (khó nén — tỉ lệ ~1:1)."""
    return make_ipa_bytes([("Payload/Game.app/Data/blob.bin", os.urandom(size))])


def empty_ipa_bytes() -> bytes:
    """IPA rỗng (0 entry)."""
    return make_ipa_bytes([])


def corrupt_crc_ipa_bytes() -> bytes:
    """IPA có 1 entry STORED bị lật 1 byte dữ liệu (CRC sai)."""
    data = make_ipa_bytes([("a.txt", b"0123456789abcdef", zipfile.ZIP_STORED)])
    # Local header của entry đầu: tìm offset dữ liệu rồi lật 1 byte.
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo("a.txt")
        off = info.header_offset
    sig, _ver, _flag, _method, _t, _d, _crc, _c, _u, nlen, elen = struct.unpack_from(
        "<IHHHHHIIIHH", data, off)
    assert sig == 0x04034B50
    pos = off + 30 + nlen + elen
    bad = bytearray(data)
    bad[pos] ^= 0xFF
    return bytes(bad)


def encrypted_ipa_bytes() -> bytes:
    """IPA tối thiểu có 1 entry mã hóa (cờ bit 0) — zipfile chuẩn không ghi được."""
    name, data = b"secret.txt", b"abcd"
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0001, 0, 0, 0x21, 0,
                        len(data), len(data), len(name), 0) + name + data
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0001, 0, 0,
                          0x21, 0, len(data), len(data), len(name), 0, 0, 0, 0,
                          0, 0) + name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central),
                       len(local), 0)
    return local + central + eocd


def unsafe_name_ipa_bytes() -> bytes:
    """IPA có entry tên traversal '../evil.txt'."""
    return make_ipa_bytes([("../evil.txt", b"x"), ("ok.txt", b"y")])


def duplicate_ipa_bytes() -> bytes:
    """IPA có 2 entry trùng tên."""
    return make_ipa_bytes([("dup.txt", b"one"), ("dup.txt", b"two")])


__all__ = ["COMPRESSIBLE_TEXT", "DATE_TIME", "make_ipa_bytes", "sample_ipa_bytes",
           "compressible_ipa_bytes", "random_ipa_bytes", "empty_ipa_bytes",
           "corrupt_crc_ipa_bytes", "encrypted_ipa_bytes",
           "unsafe_name_ipa_bytes", "duplicate_ipa_bytes"]
