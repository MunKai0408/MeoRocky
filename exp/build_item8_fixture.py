#!/usr/bin/env python3
"""Sinh fixture cho muc 8 (plan JSON + REBUILD_ALL).

Dung builder CO SAN trong repo (chi doc, khong sua):
  pb3/tests/fixtures/unity/build_fixtures.py
  - build_serialized_file(version, objects, types)
  - text_asset_data(name, script)
  - build_bundle(version, entries, compression)

item8_fixture.zip (layout IPA thu nho, dung layout 9 Kings):
  Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle
      UnityFS v7, LZ4, 1 CAB SerializedFile v17, 3 TextAsset JSON tieng Anh
      (en.json / menu.json / dialog.json — key KHAC NHAU theo file de plan
      co 3 entry doc lap).
  Payload/9Kings.app/Data/resources.assets
      SerializedFile v17, TextAsset "settings" (JSON {"volume": "80"} —
      khong co key nao trung ban dich -> vao skipped khi lap plan ca file).

Chay: python3 exp/build_item8_fixture.py
"""
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pb3", "tests",
                                "fixtures", "unity"))
from build_fixtures import build_bundle, build_serialized_file, text_asset_data

OUT = os.path.join(HERE, "fixtures")
os.makedirs(OUT, exist_ok=True)

EN_JSON = {"hello": "Hello", "play": "Play", "quit": "Quit",
           "title": "9 Kings"}
MENU_JSON = {"start": "Start Game", "options": "Options", "exit": "Exit"}
DIALOG_JSON = {"greeting": "Welcome, hero!", "farewell": "Goodbye!"}
SETTINGS_JSON = {"volume": "80", "quality": "high"}

BUNDLE_PATH = ("Payload/9Kings.app/Data/Raw/aa/iOS/"
               "localization-locales_assets_all.bundle")
CAB = "CAB-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TEXT_ASSET = (49, None)


def _sf_text_assets(items):
    return build_serialized_file(
        version=17,
        objects=[(pid, 0, text_asset_data(name, script))
                 for pid, name, script in items],
        types=[TEXT_ASSET])


def main():
    loc_sf = _sf_text_assets([
        (1001, "en.json", json.dumps(EN_JSON, ensure_ascii=False)),
        (1002, "menu.json", json.dumps(MENU_JSON, ensure_ascii=False)),
        (1003, "dialog.json", json.dumps(DIALOG_JSON, ensure_ascii=False)),
    ])
    bundle_v7_lz4 = build_bundle(
        version=7, entries=[(CAB, 4, loc_sf)], compression="lz4")
    resources = _sf_text_assets([
        (2001, "settings", json.dumps(SETTINGS_JSON, ensure_ascii=False)),
    ])
    path = os.path.join(OUT, "item8_fixture.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(BUNDLE_PATH, bundle_v7_lz4)
        zf.writestr("Payload/9Kings.app/Data/resources.assets", resources)
    data = open(path, "rb").read()
    print("%-24s %8d byte  sha256=%s"
          % (os.path.basename(path), len(data),
             hashlib.sha256(data).hexdigest()[:16]))
    print("bundle v7 LZ4: %d byte; CAB %s: en.json/menu.json/dialog.json"
          % (len(bundle_v7_lz4), CAB))


if __name__ == "__main__":
    main()
