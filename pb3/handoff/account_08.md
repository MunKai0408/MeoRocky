# TÀI KHOẢN CLAUDE 08 — PLUGIN ENGINE GAME BỔ SUNG (ĐA ENGINE)

Trạng thái: **SANDBOX DONE (2026-11-16)** — sandbox 1285/1285 OK (1143 cũ + 142
mới); `plugin list` 20 plugin [ok]; `plugin test` PASS cho cả 7 plugin mới qua
CLI thật; iSH NOT RUN. Phạm vi: prompt 08 — Godot, Unreal, Cocos, Defold,
GameMaker (không làm 09–10, không làm nén IPA, không làm font).

**ĐÍNH CHÍNH (§6):** Muse từng nói sai với Joseph (01:44–01:47) rằng engine
khác ngoài phạm vi — thực tế prompt 08 bao gồm Godot/Unreal/Cocos/Defold/
GameMaker. Lần làm 08 đầu tiên (01:50) bị dừng giữa chừng theo lệnh làm lại
của Joseph (01:55); lần này làm đầy đủ từ đầu bằng 3 nhóm song song (Godot /
Unreal / Cocos-Defold-GameMaker) rồi tích hợp.

## 1. Đã làm (sandbox, 2026-11-16)

### Godot — `sdk/godot.py` + `plugins/godot_pck` (M1) + `plugins/godot_translation` (M2)
- `PckVFS(VFS)` thuần Python, chỉ đọc: PCK v1 (Godot 3, offset tuyệt đối) và
  v2 (Godot 4, offset tương đối theo `file_base`); magic "GDPC", pack_version
  1/2; entry: path UTF-8 căn 4 byte + offset uint64 + size uint64 + md5 16
  byte; đường dẫn `res://` bỏ tiền tố; MD5 kiểm khi đọc nguyên entry; tuân
  Limits (`E_ARCHIVE_BOMB`).
- Chỉ entry stored không mã hóa: entry mã hóa/đã xóa/cờ lạ -> loại khỏi VFS +
  `inventory.rejected` kèm lý do, đọc -> `E_ENCRYPTED`; thư mục mã hóa ->
  `E_ENCRYPTED`; pack_version 0/3+ -> `E_UNSUPPORTED_FORMAT`; magic sai ->
  `E_BAD_ARCHIVE`; cắt cụt -> `E_CORRUPT`.
- `godot_pck`: plugin container, `.pck` + magic GDPC, hook detect /
  list_candidates / probe / open_container / selftest (selftest dựng PCK v2
  nhỏ trong RAM bằng builder riêng của plugin).
- `godot_translation`: plugin định dạng CHỈ ĐỌC, kế thừa `FormatPluginBase`,
  đọc `.translation` nhị phân (magic "TR", version 1, locale, bảng
  msgid→msgstr) thành LocTable; build/verify -> `E_WRITE_DISABLED`.
- Fixture: `tests/fixtures/godot/builder.py` (`build_pck_v1/v2`,
  `build_translation`, `deterministic_bytes` bằng `random.Random` seed, không
  `os.urandom` — decisions #143); không commit file nhị phân lạ.
- Test: `tests/test_godot.py` (25) + `tests/test_godot_translation.py` (21).

### Unreal — `sdk/unreal.py` + `plugins/unreal_pak` (M1) + `plugins/unreal_locres` (M2)
- `PakVFS(VFS)` thuần Python, chỉ đọc: footer cuối file (magic LE
  0x5A6F12E1, version int32, index offset/size int64, index hash); index:
  mount point (FString), entry: path (FString), offset/size/uncompressed size
  int64, compression method int32, hash, flags. Helper FString dùng chung
  (`read_fstring`/`write_fstring`).
- Chỉ entry không nén, không mã hóa: entry nén (kể cả Zlib) -> loại +
  `E_UNSUPPORTED_FORMAT`; entry mã hóa / index mã hóa -> `E_ENCRYPTED`;
  TUYỆT ĐỐI KHÔNG tìm cách vượt mã hóa/Oodle. Version/footer sai ->
  `E_BAD_ARCHIVE`; tuân Limits.
- Version có fixture: **V8B (UE 4.23–4.24) và V11 (UE 4.26–5.3)** — V11 dùng
  entry mã hóa bit-packed (varint u32/u64). V3/V7/V8A/V9 có đường parse + test
  nhưng chưa có fixture (inventory cảnh báo); V1/V2/V10 có đường parse,
  không fixture.
- `unreal_pak`: plugin container, `.pak` (PAK không có magic đầu file nên
  detect dựa vào đuôi + footer), hook detect / list_candidates / probe /
  open_container / selftest.
- `unreal_locres`: plugin định dạng CHỈ ĐỌC, kế thừa `FormatPluginBase`, đọc
  `.locres` v0–v3 thành LocTable (key `namespace/key`, placeholder `{0}` giữ
  nguyên); build/verify -> `E_WRITE_DISABLED`. `.locmeta` nhị phân gốc chưa
  hỗ trợ; `.locmeta` JSON đọc được bằng `json_text` (có test).
- Fixture: `tests/fixtures/unreal/builder.py` (`build_pak`, `build_locres`) +
  file output đã commit (`tests/fixtures/unreal_pak/game_v8.pak`,
  `game_v11.pak`, `tests/fixtures/unreal_locres/vi.locres`,
  `legacy.locres`) — tái sinh bằng
  `python3 -m tests.fixtures.unreal.builder`, theo tiền lệ fixture Unity.
- Test: `tests/test_unreal.py` (38) + `tests/test_unreal_locres.py` (23).

### Cocos / Defold / GameMaker — 3 plugin kind engine chỉ nhận diện + mở rộng `sdk/discovery.py`
- `sdk/discovery.py::detect_engine` nay chấm riêng 6 engine (unity, godot,
  unreal, cocos, defold, gamemaker) rồi chọn engine tốt nhất; bảng dấu hiệu
  dùng chung `engine_signals(engine, names, heads)` là nguồn sự thật duy
  nhất cho cả `detect_engine` và 3 plugin. Magic mạnh 0.9, tên mạnh 0.3 /
  yếu 0.15, ngưỡng như Unity cũ (CHẮC CHẮN chỉ khi có magic; chỉ tên tối đa
  CÓ KHẢ NĂNG; CÓ KHẢ NĂNG >= 0.5); hòa điểm -> ưu tiên Unity; mỗi dấu hiệu
  tên chỉ tính 1 lần.
- Magics đã xác minh bằng tra cứu: GDPC (Godot), LOCRES 16 byte
  `0E147475674A03FC4A15909DC3377F1B` (Unreal, qua .locres), data.win IFF
  `FORM` + chunk đầu `GEN8` ở offset 8 (GameMaker; `FORM` một mình không
  tính vì quá chung). PAK chỉ nhận bằng tên (magic footer không có trong
  64 byte đầu); Cocos cần >= 3 dấu hiệu (project.json + main.js = 0.45 <
  0.5); Defold/Cocos không có magic đã xác minh nên chỉ nhận bằng tên.
- `CONTAINER_EXTENSIONS` += `.pck`, `.pak`; `LOC_EXTENSIONS` +=
  `.translation`, `.locres`, `.locmeta`, `.yy`, `.yyp`, `.gml`; thêm
  `ENGINE_EXTENSIONS`. `sdk/extraction.py`: thu mẫu đầu file cả entry kind
  `container` (1 dòng) để magic GDPC của `.pck` được đọc trong pipeline.
- 3 plugin kind `engine`, capability `["hint"]`, M2, chỉ nhận diện + gợi ý,
  không mở container, không parse localization: `cocos_detect`,
  `defold_detect`, `gamemaker_detect` (hook detect -> Evidence, hints ->
  dict, selftest tự dựng VFS trong RAM). 5 SKIP trong `plugin test` là đúng
  thiết kế kind engine (không candidates/probe/parse/write/container).
- Test: `tests/test_engine_detect.py` (35). Fixture: tên entry + magic byte
  mẫu trong RAM + file mẫu nhỏ `tests/fixtures/<id>/`.

### Tích hợp (decisions #154–163)
- Thay test khóa cũ `test_open_vfs_godot_pak_without_backend_is_clean_error`
  (decisions #148, kỳ vọng lỗi "backend chưa có") bằng
  `test_open_vfs_godot_pck_and_unreal_pak` (mở PCK v2 + PAK V8 thật qua
  `open_vfs`, đọc đúng nội dung; header hỏng vẫn `E_UNSUPPORTED_FORMAT`
  sạch).
- `test_registry.py`: danh sách plugin builtin 13 -> 20 id + khẳng định
  kind/maturity từng plugin mới (container M1: godot_pck, unreal_pak;
  format M2: godot_translation, unreal_locres; engine M2: cocos_detect,
  defold_detect, gamemaker_detect).
- Ma trận hỗ trợ (`sdk/containers.py::support_matrix`): PAK sửa "không
  nén/zlib" (mơ hồ) thành "chỉ entry không nén, không mã hóa". Guard
  `ImportError` trong `open_vfs` giữ lại làm lưới an toàn, message đổi
  thành "không nạp được backend (thiếu sdk.godot/sdk.unreal)".
- `plugins/README.txt`: liệt kê cả 7 plugin mới. `CURRENT_STATE.md`: hàng
  Claude 08 + đính chính.

## 2. Bằng chứng sandbox (2026-11-16)

- **Full suite: `Ran 1285 tests in 54.3s OK (skipped=1, lỗi=0, fail=0)`**
  (1143 cũ + 142 mới: Godot 46 + Unreal 61 + engine-detection 35 + 1 test
  tích hợp thay test khóa cũ; 1284 passed, 1 skipped).
- Mô phỏng gộp điều kiện iSH (pty + `COLUMNS=40` +
  `PB3_TEST_TIMEOUT_SCALE=5`, chạy lúc tích hợp): **1285 test, 1284 đạt,
  0 fail, 0 lỗi, Timeout 0, 55.7s**, chậm nhất 2.6s.
- `menu.py plugin list`: 20 plugin [ok] (13 cũ + 7 mới).
- `plugin test` qua CLI thật: `godot_pck` PASS, `godot_translation` PASS,
  `unreal_pak` PASS (8 PASS, 0 FAIL, 3 SKIP; fixture: 2 file),
  `unreal_locres` PASS (8 PASS, 0 FAIL, 3 SKIP; fixture: 2 file),
  `cocos_detect` / `defold_detect` / `gamemaker_detect` PASS (6 PASS,
  0 FAIL, 5 SKIP — đúng thiết kế kind engine).
- Pipeline end-to-end (nhóm engine-detection): zip chứa `data.pck` (GDPC)
  -> engine `godot` CHẮC CHẮN; zip Cocos -> `cocos` CÓ KHẢ NĂNG; zip
  `data.win` (FORM/GEN8) -> `gamemaker` CHẮC CHẮN.
- **Kiểm chứng độc lập (giải nén zip ra thư mục mới, chạy lại):** spec md5
  khớp; full suite `1284 đạt, 0 fail, 0 lỗi, 1 skipped, Timeout 0, 54.6s`;
  `plugin list` 20 plugin [ok]; `container list` trên
  `tests/fixtures/unreal_pak/game_v8.pak` -> 4 entry đọc được + 5 entry bị
  loại kèm lý do rõ; `container list` trên PCK v2 dựng mới -> 2 entry đúng
  nội dung; `plugin test unreal_pak` PASS (8 PASS, 0 FAIL, 3 SKIP; fixture:
  2 file), `unreal_locres` PASS (8/0/3), `godot_translation` PASS
  (6/0/5), `godot_pck` và `cocos_detect` PASS. (Nhãn "CHƯA KIỂM DỮ LIỆU"
  trên một số `plugin test` là ngữ nghĩa conformance có sẵn — không có
  thư mục fixture trên đĩa / plugin container không có mục parse, giống hệt
  `plugin test zip`; conformance có dữ liệu đã PASS trong test từng plugin.)
- Python 3.9: mọi file mới parse được ở ngữ pháp Python 3.8; stdlib duy
  nhất, không gọi mạng trong code, không `os.urandom` trong fixture.
- Zip: [pb3_handoff_claude08_sandbox.zip](sandbox://workspace/your_files/pb3_handoff_claude08_sandbox.zip) — 839.788 byte, 320 entry, `testzip` OK.

## 3. Ma trận hỗ trợ (engine/version/đọc/ghi)

| Engine | Định dạng | Đọc | Ghi |
|---|---|---|---|
| Godot 3 | PCK v1 | có (chỉ entry stored, không mã hóa) | không |
| Godot 4 | PCK v2 | có (chỉ entry stored, không mã hóa) | không |
| Godot | .translation ("TR" v1) | có (chỉ đọc; xem §4) | không |
| Unreal UE 4.23–4.24 | PAK V8B | có (chỉ entry không nén, không mã hóa) | không |
| Unreal UE 4.26–5.3 | PAK V11 | có (chỉ entry không nén, không mã hóa) | không |
| Unreal | .locres v0–v3 | có (chỉ đọc) | không |
| Unreal | .locmeta JSON | có (qua json_text) | — |
| Unreal | .locmeta nhị phân | không | không |
| Cocos | — | chỉ nhận diện (tên) | không |
| Defold | — | chỉ nhận diện (tên) | không |
| GameMaker | data.win (FORM+GEN8) | chỉ nhận diện (magic) | không |

## 4. Giới hạn trung thực

- **Chưa kiểm chứng trên file game thật nào** (PCK/PAK/.translation/
  .locres đều là fixture tự dựng theo tài liệu + mã nguồn tham chiếu
  GodotPckTool / repak / pylocres). Fixture chứng minh parser đúng theo
  đặc tả đã tra, không chứng minh mọi game thật đều mở được.
- **Godot .translation "TR" v1 CHƯA đối chiếu file thật:** tra cứu công
  khai không xác nhận được magic này đối chiếu với file do Godot thật xuất
  ra — file `.translation` thật là binary resource (RSRC /
  OptimizedTranslation, bảng băm + chuỗi nén, không lưu msgid). Plugin báo
  `E_UNSUPPORTED_FORMAT` rõ ràng khi gặp RSRC thay vì đọc sai. Cần file
  `.translation` thật để đối chiếu trước khi tuyên bố hỗ trợ đầy đủ. Không
  hỗ trợ context/plural trong `.translation`.
- **PCK:** chưa hỗ trợ v0/v3 (Godot 4.5+), PCK nhúng trong file thực thi,
  ghi lại PCK, giải mã. **PAK:** chưa hỗ trợ entry nén (kể cả Zlib),
  Oodle, mã hóa; PAK version V3/V7/V8A/V9 có đường parse nhưng chưa có
  fixture (inventory cảnh báo); V1/V2/V10 có đường parse, không fixture.
  Hash SHA-1 entry không xác thực khi đọc (như UE/repak); hash index
  không khớp footer chỉ cảnh báo. PAK không có entry thư mục (`stat` tiền
  tố -> `E_INPUT_NOT_FOUND`).
- **Cocos/Defold/GameMaker:** chỉ nhận diện, không mở container, không
  parse localization. Cocos/Defold chỉ nhận bằng tên (không magic đã xác
  minh) nên tối đa CÓ KHẢ NĂNG; Cocos cần >= 3 dấu hiệu.
- **iSH: NOT RUN** (chờ Joseph).

## 5. Kiểm chứng trên iSH (chờ Joseph, sáng 2026-11-16)

Sau khi giải nén zip `pb3_handoff_claude08_sandbox.zip` vào `/root/pb3`
(ghi đè lên bản cũ), chạy:

```
cd /root/pb3
python3 run_tests.py --tier full
# kỳ vọng: Ran 1285 tests ... OK (skipped=5, lỗi=0, fail=0), ~20+ phút
python3 menu.py plugin list
# kỳ vọng: 20 plugin [ok], gồm godot_pck, godot_translation, unreal_pak,
#   unreal_locres, cocos_detect, defold_detect, gamemaker_detect
python3 menu.py plugin test godot_pck
python3 menu.py plugin test godot_translation
python3 menu.py plugin test unreal_pak
python3 menu.py plugin test unreal_locres
python3 menu.py plugin test cocos_detect
python3 menu.py plugin test defold_detect
python3 menu.py plugin test gamemaker_detect
# kỳ vọng: mỗi lệnh PASS (unreal_*: 8 PASS, 0 FAIL, 3 SKIP; *_detect:
#   6 PASS, 0 FAIL, 5 SKIP — đúng thiết kế kind engine)
python3 menu.py container list tests/fixtures/unreal_pak/game_v8.pak
# kỳ vọng: liệt kê 4 entry đọc được (hello.txt, menu.txt, vi.locres,
#   thoat.txt) + entry bị loại ghi lý do
python3 menu.py extract tests/fixtures/extract/game1.zip
# kỳ vọng (hồi quy C07): 21 entry, 6 file
```

## 6. Đính chính (ghi để parent nói với Joseph khi giao C08)

Muse từng nói sai với Joseph (01:44–01:47) rằng engine khác (Godot,
Unreal, Cocos, Defold, GameMaker) nằm ngoài phạm vi dự án. Thực tế prompt
08 bao gồm các engine này theo thứ tự ưu tiên trong đặc tả, và Claude 08
đã triển khai chúng như trên. Parent sẽ đính chính khi giao zip C08.
Chi tiết bàn giao nhóm Godot: `handoff/account_08_godot.md`.

## 7. Bổ sung sau Claude 08: credit 'Việt Hoá Bởi Bear' + tên hiển thị BearTea (Joseph 02:47)

- **Credit khi dịch tự động (`sdk/translate.py::apply_credit`, decisions #164):**
  sau khi dịch xong, gắn " — Việt Hoá Bởi Bear" vào 1 entry đã chọn trong
  bảng đã dịch: ưu tiên chuỗi màn hình tiêu đề (key `title`/`game_title`/
  `app_name`/..., hoặc chứa "title"), rồi chuỗi version/copyright; không có
  chỗ phù hợp thì báo rõ "không đặt được credit" (không lặng lẽ bỏ qua).
  Không thêm key mới (apply_edits bỏ qua key lạ); lũy đẳng; không đi vào
  cache (chạy lại với `--no-credit` vẫn ra bản dịch thuần); luật dịch giữ
  nguyên (chỉ dịch value, giữ key/placeholder/thẻ/xuống dòng).
  Cờ `--no-credit` cho cả `translate` và `extract --translate`; menu [2]
  mặc định bật credit. Credit vào game chỉ khi ghi ngược `extract --apply
  --allow-write` (dry-run mặc định); bản xem trước (file JSON + tóm tắt)
  hiện rõ credit đặt ở key nào.
- **Tên hiển thị trong menu là BearTea (decisions #165):**
  `ui/screens.py::banner()` hiện "BearTea"; CHỈ đổi tên hiển thị, tên
  project (PB3), đường dẫn, tên file, tên zip, `--version`, mô tả CLI
  `--help` giữ nguyên. README.txt ghi chú tên hiển thị mới.
- **Bằng chứng sandbox (2026-11-16):** 13 test mới — `TestCredit` (9 test:
  gắn vào title, ưu tiên title hơn version, fallback version/copyright,
  không có chỗ thì báo rõ, --no-credit, lũy đẳng, bỏ qua title rỗng, giữ
  luật dịch + cache thuần, chọn entry) + `test_translate_cli.py` (4 test:
  credit mặc định, --no-credit, không có chỗ thì báo rõ, extract
  --translate --no-credit) + cập nhật 2 test banner. Full suite `Ran 1298
  tests in 56.7s OK (skipped=1, lỗi=0, fail=0)` (1285 cũ + 13 mới); mô
  phỏng gộp iSH (pty + `COLUMNS=40` + `PB3_TEST_TIMEOUT_SCALE=5`): 1298
  test, 1297 đạt, 1 bỏ qua, 0 fail, 0 lỗi, Timeout 0, 57.3s. Zip
  `pb3_handoff_claude08_sandbox.zip` rebuild (cùng tên).
- **Kiểm chứng trên iSH (chờ Joseph):** sau khi giải nén zip mới nhất vào
  `/root/pb3` (ghi đè), chạy:
  ```
  python3 run_tests.py --tier full
  # kỳ vọng: Ran 1298 tests ... OK (skipped=5, lỗi=0, fail=0), ~20+ phút
  python3 menu.py translate tests/fixtures/extract/en.json --to vi --provider offline --no-cache
  # kỳ vọng: exit 0; en.json không có chuỗi tiêu đề/version nên in
  #   "không đặt được credit 'Việt Hoá Bởi Bear'"
  python3 menu.py
  # kỳ vọng: tiêu đề menu hiện "BearTea" (không còn "PB3 TOOLKIT")
  ```
