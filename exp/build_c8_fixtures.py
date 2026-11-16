#!/usr/bin/env python3
"""Sinh fixture cho mục C8 (format probe) vào exp/fixtures/.

- Engine đã biết: PCK Godot v2 + .translation "TR" (builder Godot),
  PAK Unreal v8 + .locres v3 (builder Unreal), data.win GameMaker
  (FORM+GEN8), ZIP Cocos/Defold (entry thật từ tests/fixtures).
- File lạ: byte ngẫu nhiên có seed + ZIP thường không phải game.
- Unity: bundle giả chỉ có magic UnityFS (probe chỉ cần head).
Chỉ dùng thư viện chuẩn; dữ liệu giả dùng random.Random với seed
(không dùng os.urandom). Chạy: python3 build_c8_fixtures.py
"""
import io
import random
import sys
import zipfile
from pathlib import Path

SITE_PB3 = Path(__file__).resolve().parent.parent / "site" / "pb3"
sys.path.insert(0, str(SITE_PB3))

from tests.fixtures.godot import builder as godot_b  # noqa: E402
from tests.fixtures.unreal import builder as unreal_b  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries.items()):
            zf.writestr(name, data)
    return buf.getvalue()


def _write(name, data):
    p = OUT / name
    p.write_bytes(data)
    print("%s %dB" % (name, len(data)))


_write("c8_godot.pck", godot_b.build_pck_v2(
    [("res://hello.txt", b"hello pb3")]))
_write("c8_godot_translation.translation",
        godot_b.build_translation([("Hello", "Xin chào")], locale="vi"))
_write("c8_unreal_pak.pak", unreal_b.sample_pak_bytes(version=8))
_write("c8_unreal_locres.locres", unreal_b.sample_locres_bytes(version=3))
_write("c8_gamemaker_data.win",
        b"FORM" + (100).to_bytes(4, "big") + b"GEN8" + b"\x00" * 20)
_write("c8_cocos.zip", _zip_bytes({
    "project.json": b'{"version": "3.8.0", "assets": []}',
    "main.js": b"// Cocos Creator\ncc.game.run();\n",
    "res/icon.png": b"\x89PNG\r\n\x1a\n",
    "assets/scene.fire": b"[]",
}))
_write("c8_defold.zip", _zip_bytes({
    "game.project": b"[project]\ntitle = Test\n",
    "main.collection": b"",
    "main.script": b"function init(self) end\n",
    "assets/atlas.atlas": b"",
}))
_write("c8_unknown.bin",
        bytes(random.Random(7).randrange(256) for _ in range(8192)))
_write("c8_fake.gz",
        bytes(random.Random(8).randrange(256) for _ in range(4096)))
_write("c8_plain.zip", _zip_bytes({
    "readme.txt": b"hello world\n",
    "docs/guide.txt": b"not a game\n",
}))
_write("c8_unity.unity3d", b"UnityFS\x00\x00" + b"\x00" * 200)
print("OK:", sorted(p.name for p in OUT.glob("c8_*")))
