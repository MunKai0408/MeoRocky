Plugin dựng sẵn (builtin). Mỗi plugin = một thư mục con: <id>/plugin.py (hoặc plugin.toml) + code.
Không đặt plugin mẫu/test vào đây: nó sẽ lọt vào bảng hỗ trợ.

- zip (Claude 04): container ZIP/IPA, M2.
- Định dạng / localization (Claude 06, xem handoff/account_06.md):
  json_text, csv_table, xml_strings, yaml_ini, properties,
  apple_plist, apple_strings, sqlite_loc (chỉ đọc),
  po_mo, xliff, resx, generic_strings (extractor, chỉ đọc).
  10 plugin ghi được ở M3 (có tests/corpus/<id>.json), 2 plugin chỉ đọc ở M2.
- Unreal (Claude 08, nhóm Unreal): unreal_pak (container PAK UE4/UE5,
  M1, chỉ đọc entry không nén/không mã hóa), unreal_locres (định dạng
  .locres v0-v3, M2, chỉ đọc). Fixture dựng bằng builder
  tests/fixtures/unreal/builder.py (xem file đó để sinh lại).
- Godot (Claude 08, nhóm Godot, xem handoff/account_08_godot.md):
  godot_pck (container PCK v1/v2, M1, chỉ đọc), godot_translation
  (định dạng .translation nhị phân "TR" v1, M2, chỉ đọc).
- Nhận diện engine (Claude 08, nhóm engine-detection, xem handoff/account_08.md):
  cocos_detect, defold_detect, gamemaker_detect (kind engine, M2, chỉ
  nhận diện qua tên/magic + gợi ý, không mở container, không parse).
  Nhận diện Godot/Unreal/Cocos/Defold/GameMaker trong sdk/discovery.py
  (detect_engine) dùng chung bảng dấu hiệu với 3 plugin này.
