#!/usr/bin/env python3
"""Sinh fixture cho đợt gộp en.json + chọn file + nối Dịch (2026-11-16).

Dùng builder CÓ SẴN ở cây iSH (chỉ đọc, không sửa):
  ~/workspace/pb3_fix/pb3/tests/fixtures/unity/build_fixtures.py
  - build_serialized_file(version, objects, types)
  - text_asset_data(name, script)
  - build_bundle(version, entries, compression)

Fixture 1 — extract_select_fixture.zip (IPA thu nhỏ, đúng layout 9 Kings):
  Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle
      UnityFS v7, LZ4, 1 CAB SerializedFile v17, TextAsset en.json / zh.json /
      fr.json (JSON key-value thật, nội dung khác nhau theo locale).
  Payload/9Kings.app/Data/level1.unity3d
      UnityFS v8, không nén, TextAsset de.json (kiểm v8 + locale ngoài en/zh).
  Payload/9Kings.app/Data/resources.assets
      SerializedFile v17, TextAsset tên "en" (JSON, khác nội dung en.json
      trong bundle — để phân biệt nguồn khi trọng tài chọn).
  Payload/9Kings.app/Data/Raw/aa/settings.json — JSON thường, locale không
      xác định (kiểm lọc locale bỏ qua entry không rõ locale).
  Payload/9Kings.app/Data/Raw/readme.txt — file text thường.

Fixture 2 — extract_broken_bundle.zip:
  - bundle localization BỊ CẮT CỤT (bundle v7 LZ4 hợp lệ cắt còn 40%:
    header + blocks info còn nguyên, block data thiếu -> E_CORRUPT
    "block 0 cắt cụt") + resources.assets tốt (extract vẫn ra file).
  - Payload/9Kings.app/Data/huge.bundle: 70MB byte 0 (ZIP nén còn ~70KB)
    -> kiểm giới hạn 64MB/entry phải báo rõ, không biến mất lặng lẽ.
    Inventory chỉ đọc 64 byte đầu + stat kích thước nên không giải nén
    70MB.

Chạy: python3 exp/build_extract_select_fixture.py
"""
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ISH_FIX = "/home/hatch/workspace/pb3_fix/pb3/tests/fixtures/unity"
sys.path.insert(0, ISH_FIX)
from build_fixtures import build_bundle, build_serialized_file, text_asset_data

OUT = os.path.join(HERE, "fixtures")
os.makedirs(OUT, exist_ok=True)

EN_JSON = {"hello": "Hello", "play": "Play", "quit": "Quit",
           "title": "9 Kings", "welcome": "Welcome to 9 Kings!"}
ZH_JSON = {"hello": "你好", "play": "开始游戏", "quit": "退出",
           "title": "9 Kings", "welcome": "欢迎来到 9 Kings！"}
FR_JSON = {"hello": "Bonjour", "play": "Jouer", "quit": "Quitter",
           "title": "9 Kings", "welcome": "Bienvenue !"}
DE_JSON = {"hello": "Hallo", "play": "Spielen", "quit": "Beenden",
           "title": "9 Kings"}
RES_EN_JSON = {"from_resources": "yes", "greeting": "Hello from resources.assets"}
SETTINGS_JSON = {"volume": "80", "quality": "high"}
README_TXT = "9 Kings test fixture.\nThis file is not a localization table.\n"

BUNDLE_PATH = "Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle"
TEXT_ASSET = (49, None)


def _sf_text_assets(items):
    """items: [(path_id, name, script)] -> bytes SerializedFile v17."""
    return build_serialized_file(
        version=17,
        objects=[(pid, 0, text_asset_data(name, script))
                 for pid, name, script in items],
        types=[TEXT_ASSET])


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    print("%-28s %8d byte  sha256=%s" % (
        os.path.basename(path), os.path.getsize(path),
        hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]))


def main():
    loc_sf = _sf_text_assets([
        (1001, "en.json", json.dumps(EN_JSON, ensure_ascii=False, indent=2)),
        (1002, "zh.json", json.dumps(ZH_JSON, ensure_ascii=False, indent=2)),
        (1003, "fr.json", json.dumps(FR_JSON, ensure_ascii=False, indent=2)),
    ])
    bundle_v7_lz4 = build_bundle(
        version=7,
        entries=[("CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 4, loc_sf)],
        compression="lz4")
    level_sf = _sf_text_assets([
        (2001, "de.json", json.dumps(DE_JSON, ensure_ascii=False, indent=2)),
    ])
    bundle_v8_none = build_bundle(
        version=8, unity_version="5.x.x", unity_revision="6000.0.23f1",
        entries=[("CAB-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", 4, level_sf)],
        compression="none")
    resources = _sf_text_assets([
        (1001, "en", json.dumps(RES_EN_JSON, ensure_ascii=False, indent=2)),
    ])
    _write_zip(os.path.join(OUT, "extract_select_fixture.zip"), [
        (BUNDLE_PATH, bundle_v7_lz4),
        ("Payload/9Kings.app/Data/level1.unity3d", bundle_v8_none),
        ("Payload/9Kings.app/Data/resources.assets", resources),
        ("Payload/9Kings.app/Data/Raw/aa/settings.json",
         json.dumps(SETTINGS_JSON, ensure_ascii=False, indent=2).encode("utf-8")),
        ("Payload/9Kings.app/Data/Raw/readme.txt", README_TXT.encode("utf-8")),
    ])
    # Fixture 2: bundle localization cắt cụt + resources tốt + huge.bundle
    # + settings.json không mơ hồ (để đợt chạy vẫn decision "done" và
    # report ghi ra đĩa: chỉ 1 TextAsset "en" trong resources.assets thì
    # trọng tài ASK giữa json_text/generic_strings (hành vi có sẵn, không
    # thuộc đợt này) rồi không xuất gì cả — report trên đĩa sẽ không có).
    broken = bundle_v7_lz4[:int(len(bundle_v7_lz4) * 0.4)]
    huge = b"\x00" * (70 * 1024 * 1024)
    _write_zip(os.path.join(OUT, "extract_broken_bundle.zip"), [
        (BUNDLE_PATH, broken),
        ("Payload/9Kings.app/Data/resources.assets", resources),
        ("Payload/9Kings.app/Data/Raw/aa/settings.json",
         json.dumps(SETTINGS_JSON, ensure_ascii=False, indent=2).encode("utf-8")),
        ("Payload/9Kings.app/Data/huge.bundle", huge),
    ])
    print("bundle v7 LZ4: %d byte; bản cắt cụt: %d byte" % (
        len(bundle_v7_lz4), len(broken)))


if __name__ == "__main__":
    main()
