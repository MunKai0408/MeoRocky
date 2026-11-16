"""Builder xác định cho fixture Unreal (Claude 08, nhóm Unreal).

Xây file PAK (UE4 V8B / UE5 V11) và file .locres (v0-v3) trong RAM —
KHÔNG commit file nhị phân lạ, test tự dựng fixture khi chạy (builder
cũng dùng để sinh file mẫu cho `plugin test` qua __main__ dưới đây).
Chỉ dùng thư viện chuẩn; dữ liệu mẫu có tiếng Việt có dấu; dữ liệu giả
ngẫu nhiên (khi cần) dùng random.Random với seed, KHÔNG dùng os.urandom
(decisions #143).

Layout PAK (đối chiếu repak / rust-unreal-unpak): xem sdk/unreal.py.
Layout .locres (đối chiếu pylocres): xem plugins/unreal_locres/plugin.py.

Chạy để sinh lại fixture cho CLI `plugin test`:
    python3 -m tests.fixtures.unreal.builder
(đường dẫn ghi: tests/fixtures/unreal_pak/*.pak,
tests/fixtures/unreal_locres/*.locres).
"""
from __future__ import annotations

import os

from plugins.unreal_locres.plugin import build_locres
from sdk.unreal import build_pak

# Dữ liệu text mẫu (tiếng Việt có dấu, có placeholder/thẻ như chuỗi game).
SAMPLE_TEXTS = {
    "TestGame/Content/Text/hello.txt":
        "Xin chào, chào mừng đến với trò chơi!\nChúc bạn chơi vui vẻ.".encode("utf-8"),
    "TestGame/Content/Text/menu.txt":
        "Chơi | Cài đặt | Thoát".encode("utf-8"),
    "TestGame/Content/UI/menu.uasset":
        b"\x00\x01\x02FAKE-UASSET\xff\xfe",
}

SAMPLE_NAMESPACES = [
    ("UI", [("Play", "Chơi", 0x11111111),
            ("Quit", "Thoát", 0x22222222),
            ("Settings", "Cài đặt", 0x33333333)]),
    ("Menu", [("Welcome", "Chào mừng, {0}!", 0x44444444),
              ("Score", "Điểm: {0}", 0x55555555)]),
    ("", [("Hello", "Xin chào", 0x66666666)]),
]


def sample_locres_bytes(version: int = 3) -> bytes:
    """File .locres mẫu: 3 namespace (1 rỗng), 6 key, tiếng Việt có dấu."""
    return build_locres(SAMPLE_NAMESPACES, version=version)


def sample_pak_bytes(*, version: int = 8, v8a: bool = False) -> bytes:
    """File PAK mẫu: 3 entry đọc được (text tiếng Việt + 1 tên có tiền tố
    '../' được chuẩn hóa), 1 entry nén Zlib (bị loại), 1 entry mã hóa
    (bị loại, chỉ mang cờ), 1 entry đã xóa (bị loại; V10+ không có cờ
    deleted nên mẫu V11 bỏ entry này), 1 entry trùng tên (bản sau bị
    loại), 1 entry tên xấu chứa '..' giữa (bị loại), 1 entry .locres."""
    locres = sample_locres_bytes(3)
    entries = [
        ("TestGame/Content/Text/hello.txt", SAMPLE_TEXTS["TestGame/Content/Text/hello.txt"], {}),
        ("TestGame/Content/Text/menu.txt", SAMPLE_TEXTS["TestGame/Content/Text/menu.txt"], {}),
        ("TestGame/Content/Text/vi.locres", locres, {}),
        ("../thoat.txt", "Tên có tiền tố mount point, chuẩn hóa thành thoat.txt".encode("utf-8"), {}),
        ("TestGame/Content/Data/big.bin", b"Du lieu nen " * 100,
         {"compression": "Zlib"}),
        ("TestGame/Content/Data/secret.bin", b"Du lieu ma hoa (chi mang co)",
         {"encrypted": True}),
        ("TestGame/Content/Text/hello.txt", b"Ban sao trung ten (bi loai)", {}),
        ("TestGame/Content/../evil.txt", b"Ten xau", {}),
    ]
    if version < 10:
        entries.append(("TestGame/Content/Data/old.bin", b"Du lieu da xoa",
                        {"deleted": True}))
    return build_pak(entries, version=version, v8a=v8a)


def sample_pak_v11_bytes() -> bytes:
    """Như sample_pak_bytes nhưng footer V11 (UE5, index entry mã hóa)."""
    return sample_pak_bytes(version=11)


def main() -> None:
    """Sinh lại fixture cho `plugin test` (ghi đè file cũ)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    pak_dir = os.path.join(root, "tests", "fixtures", "unreal_pak")
    loc_dir = os.path.join(root, "tests", "fixtures", "unreal_locres")
    os.makedirs(pak_dir, exist_ok=True)
    os.makedirs(loc_dir, exist_ok=True)
    files = {
        os.path.join(pak_dir, "game_v8.pak"): sample_pak_bytes(version=8),
        os.path.join(pak_dir, "game_v11.pak"): sample_pak_v11_bytes(),
        os.path.join(loc_dir, "vi.locres"): sample_locres_bytes(3),
        os.path.join(loc_dir, "legacy.locres"): sample_locres_bytes(0),
    }
    for path, data in files.items():
        with open(path, "wb") as fh:
            fh.write(data)
        print("ghi %s (%d byte)" % (path, len(data)))


if __name__ == "__main__":
    main()
