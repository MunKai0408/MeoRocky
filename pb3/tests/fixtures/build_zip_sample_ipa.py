#!/usr/bin/env python3
"""Sinh fixture tests/fixtures/zip/sample.ipa (chay 1 lan, file sinh ra duoc commit).

Dac ta goc (Claude 04, 2026-11-15, ghi trong pb3/handoff/account_04.md,
CHANGELOG.txt, CURRENT_STATE.md): IPA that ve cau truc, 672 byte, 3 entry
DEFLATED, timestamp co dinh (2024-05-06 07:08:09) de sinh lai byte-identical:
- Payload/Game.app/Info.plist (93 byte): plist toi thieu.
- Payload/Game.app/Data/level.unity3d (zip long): chua resources.assets
  (223 byte, bat dau b"UNITYFS-fake-resources-") + sharedassets0.assets.
- Payload/Game.app/Data/config.txt (8 byte): b"level=1\\n".

KHONG dung tests/fixtures/compress/builder.py::sample_ipa_bytes() o day:
builder do sinh IPA 7 entry ~100 KB cho test nen (blob ngau nhien) — dung
no se lam 3 test test_container.py FAIL (test_sample_ipa_fixture,
test_nested_missing_and_not_archive, test_detect_candidates_probe_open
deu doi dung 3 entry tren). Loi nay tung xay ra 2026-11-17 khi commit nham
file do builder nen sinh ra; sua bang cach dung builder nay.

Chay:  python3 tests/fixtures/build_zip_sample_ipa.py
# (KHONG dat file nay trong tests/fixtures/zip/: run_conformance dem moi
# file trong thu muc fixture — test_conformance_pass_on_fixture doi dung
# 1 file; them .py vao do se dem thanh 2 va FAIL.)
"""
from __future__ import annotations

import hashlib
import io
import os
import zipfile

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zip")
DATE_TIME = (2024, 5, 6, 7, 8, 9)

INFO_PLIST = (b'<?xml version="1.0"?>'
              b'<plist><dict><key>CFBundleName</key>'
              b'<string>Game</string></dict></plist>')
assert len(INFO_PLIST) == 93, len(INFO_PLIST)

# resources.assets gia: 223 byte, bat dau bang tien to fake; test chi kiem
# tra startswith + size 223 + slice(0, 7) == b"UNITYFS".
RESOURCES_ASSETS = b"UNITYFS-fake-resources-" + b"A" * 200
assert len(RESOURCES_ASSETS) == 223, len(RESOURCES_ASSETS)

# sharedassets0.assets: chi ten duoc test kiem tra (khong co assert noi
# dung). Chon 27 byte sao cho zip long level.unity3d vua dung 300 byte
# (dac ta goc, test_final_review.py assert "300" trong inventory) — do
# that 2026-11-17: prefix 22 byte + run "A"*5 -> level = 300 dung.
SHARED_ASSETS = b"UNITYFS-sharedassets0-" + b"A" * 5
CONFIG_TXT = b"level=1\n"


def _zip_bytes(specs) -> bytes:
    """specs: [(ten, bytes)]; tat ca DEFLATED, timestamp co dinh."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in specs:
            info = zipfile.ZipInfo(name, DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            zf.writestr(info, data)
    return buf.getvalue()


def sample_ipa_bytes() -> bytes:
    """IPA 3 entry theo dac ta goc (xem docstring module)."""
    level = _zip_bytes([
        ("resources.assets", RESOURCES_ASSETS),
        ("sharedassets0.assets", SHARED_ASSETS),
    ])
    return _zip_bytes([
        ("Payload/Game.app/Info.plist", INFO_PLIST),
        ("Payload/Game.app/Data/level.unity3d", level),
        ("Payload/Game.app/Data/config.txt", CONFIG_TXT),
    ])


def main() -> None:
    data = sample_ipa_bytes()
    path = os.path.join(HERE, "sample.ipa")
    with open(path, "wb") as fh:
        fh.write(data)
    zf = zipfile.ZipFile(io.BytesIO(data))
    print("%-12s %7d byte  sha256=%s" % (
        "sample.ipa", len(data), hashlib.sha256(data).hexdigest()))
    for info in zf.infolist():
        print("  %-42s %5d byte  %s" % (
            info.filename, info.file_size,
            "deflated" if info.compress_type == zipfile.ZIP_DEFLATED else info.compress_type))


if __name__ == "__main__":
    main()
