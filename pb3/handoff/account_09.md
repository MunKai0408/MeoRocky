# TÀI KHOẢN CLAUDE 09 — KHO TRI THỨC GAME + XỬ LÝ HÀNG LOẠT + MA TRẬN HỖ TRỢ

Làm theo prompt 09 (`PB3_v3.1_10_Claude_Prompts.txt`) + đặc tả §8–§10, §14.5.
Ba nhóm song song (KB / Batch / Matrix+Docs) rồi tích hợp bởi coordinator.
Sandbox DONE 2026-11-16; iSH NOT RUN (chờ Joseph sáng 2026-11-16).

## 1. Đã làm (sandbox, 2026-11-16)

### KB — `sdk/kb.py` + `tests/test_kb.py` (33 test)
- SQLite WAL `kb/pb3_kb.sqlite`, schema v1: games / files / locales / choices /
  runs / plugin_stats. Không lưu nội dung dịch (chỉ hash/kích thước/vai trò).
- `fingerprint_game(path)`: bundle_id/version từ Info.plist (khi có); hash tối
  đa 5 entry đặc trưng (Info.plist trước, rồi entry nhỏ chứa
  info/version/manifest/config, còn lại theo đường dẫn); KHÔNG hash toàn bộ
  IPA (§8.2). Engine chỉ best effort (unknown/0.0 khi không nhận diện được).
- `remember(fp, files, locales, choices)` ghi đè toàn bộ files/locales/choices
  cũ (ảnh chụp mới nhất), trả game_id int. `lookup(path)` xác minh lại hash
  các file đã lưu với container hiện tại: khớp mới tái dùng, lệch -> None +
  `last_lookup_reason` (không tái dùng im lặng); hash một phần không bao giờ
  là bằng chứng duy nhất về danh tính.
- `InventoryCache` (`workspace/cache/inventory_cache.json`): khóa (đường dẫn,
  mtime, kích thước, sha256), invalidate khi file đổi; `size_bytes()` tính cả
  file `-wal` (sau 50 lần record_run, file chính 4 KB nhưng `-wal` giữ 531 KB
  — đo sai thì `prune` báo 0 byte đã dọn; đã sửa bằng WAL checkpoint trước
  khi đo).
- Profile export/import JSON thuần (game/files với sha256_8/locales), import
  kiểm tra hash khi dùng; profile chỉ là gợi ý, pipeline vẫn probe lại.
  `kb export <game> [--out FILE]`, `kb import <file>`, `kb forget <game>`,
  `kb prune` (dọn cache KB, giới hạn 64 MB, §9.3). KB trống báo rõ "KB trống",
  rc 0, không crash.
- Tích hợp: thêm `KB.plugin_stats()` (tổng `games_seen` theo adapter_id, cộng
  dồn mọi version) để ma trận đọc cột "số game đã test" (decisions #176).

### Batch — `sdk/batch.py` + `services/batch_tool.py` + `tests/test_batch.py` (25) + `tests/test_batch_cli.py` (3)
- Manifest CSV (header bắt buộc có `path`) hoặc JSON list object: path, locale,
  mode (extract|mod), old/new (mod), file, object, profile_override.
- Chạy tuần tự hoặc song song (`--jobs N`, mặc định min(4, cpu); trên iSH nên
  giữ jobs=1 vì GIL + RAM); mỗi game cô lập (lỗi một game không dừng cả lô);
  staging `.staging/game_<nn>_<slug>/` chỉ chuyển thành thư mục game khi chạy
  xong (kể cả failed — còn dấu vết; timeout thì bỏ staging lại).
- Checkpoint `checkpoint.json` (nguyên tử) sau mỗi game; resume bỏ qua game đã
  xong (status ok) theo khóa sha256(path, locale, mode, old, new). Output
  `workspace/output/_batch/<timestamp>/`: `index.csv` + `summary.json` (kèm
  ngân sách §9.2) + `failures.txt` (mã lỗi) + `result.json` từng game. Mã thoát
  0 khi mọi game ok/bỏ qua, 1 khi có game lỗi. Không bao giờ đóng gói lại IPA
  trong batch (chỉ xuất đơn vị tối thiểu). Tiến độ `SteadyProgress` từng 1% +
  ETA thật (luật #152–153). Dry-run mặc định (chỉ ghi thật khi `--allow-write`).
- Mã lỗi mới `E_TIMEOUT` ("Hết thời gian cho phép.", exit 4): timeout từng
  game (`--timeout-game`, best-effort qua ThreadPoolExecutor — worker nền quá
  hạn không bị giết cưỡng bức); game timeout -> failed + `E_TIMEOUT`, lô vẫn
  chạy tiếp.
- KB trong batch: import lười `sdk.kb` (chưa có module thì batch vẫn chạy, chỉ
  không dùng/không ghi tri thức, báo rõ). Sau mỗi lần trích/mod thành công:
  `kb.remember` + `kb.record_run` + `record_plugin_stats`; lỗi KB không làm
  hỏng game. Preset `{file, locale}` từ profile khi không có
  `profile_override`; lần sau in rõ "dùng tri thức từ ..." (decisions #178–179
  về sửa lỗi tích hợp).
- Tích hợp: `--resume` không kèm `--out` nay tự tìm lần chạy cũ gần nhất có
  checkpoint khớp manifest (in "tiếp tục từ: ...", không tìm thấy thì chạy mới
  và báo rõ) + cờ `--out DIR` cho CLI batch + `BatchSummary.resumed_from`
  (ghi vào summary.json) (decisions #177).

### Matrix + Docs + CLI — `sdk/support_matrix.py` + `docs/SUPPORT_MATRIX.md` + `docs/HUONG_DAN.md` + `tests/test_support_matrix.py` (8) + `tests/test_kb_cli.py` (14)
- `sdk/support_matrix.py::generate` / `write_matrix`: nguồn hàng
  `registry.rows()` (dùng maturity HIỆU LỰC, không dùng mức khai), corpus PASS
  read/write/verify + ngày test = mtime UTC của `tests/corpus/<id>.json`
  ("chưa có" nếu không có), `sdk.containers.support_matrix()` +
  `sdk.unity.support_matrix()`, số game đã test từ KB (`plugin_stats()` nếu KB
  tồn tại, không có KB thì 0). Header cảnh báo "Mức hỗ trợ chỉ đúng với những
  gì đã kiểm thử". Không tuyên bố M4 cho bất cứ thứ gì (M4 cần bằng chứng game
  thật + byte-diff sạch — chưa có). Ba danh sách trung thực (§10.3): đã xác
  minh trên game thật = CHỈ Unity inspect trên IPA thật 9 Kings v1.0.5 (nguồn
  `handoff/account_05.md` §5; patch Unity chỉ mới test fixture); mới test
  fixture = mọi plugin/engine còn lại; chưa hỗ trợ kèm lý do (7z, .locmeta nhị
  phân, PCK v0/v3, PAK nén/mã hóa/Oodle, SerializedFile v<15/v>23, bundle mã
  hóa, Cocos/Defold/GameMaker chỉ nhận diện).
- Lệnh `support [--write]` (in ma trận; `--write` ghi `docs/SUPPORT_MATRIX.md`)
  và `kb export|import|forget|prune` trong `services/plugin_manager.py`; menu
  [3] thêm `[m] Ma trận hỗ trợ` và `[k] Quên tri thức game`, hành vi số/0/EOF
  cũ giữ nguyên. `ui/handlers.py`: MENU[3] batch lên maturity M2 + note_vi;
  MENU[2] plugins bỏ `partial_vi` "ma trận hỗ trợ (chưa có)". README.txt:
  "ĐÃ CÓ: batch, support, kb (Claude 09…)".
- `docs/HUONG_DAN.md`: menu [1]–[5], mọi lệnh CLI, manifest CSV/JSON + ví dụ,
  KB reuse/forget/export-import, bảng lỗi, giới hạn iSH (RAM/tốc độ/nền/chữ
  ký, không hướng dẫn vượt bảo vệ bản quyền), luật tiến độ #152–153.
- Tích hợp: `generate()` nhận `rows`/`manifests`/`kb_stats` do tầng services
  truyền vào — sdk KHÔNG được import registry (decisions #129, #175);
  `rows=None` thì bảng plugin trống kèm ghi chú (ma trận vẫn sinh được).

### Tích hợp (decisions #175–180)
- #175: `support_matrix.generate()` bỏ nhánh tự dựng Registry (vi phạm kiến
  trúc `test_core_does_not_import_registry`); `services/plugin_manager.py`
  thêm `_matrix_inputs(reg)`; test khóa `test_generate_without_rows_has_empty_plugin_table_and_note`; `docs/SUPPORT_MATRIX.md` sinh lại — nội dung không đổi.
- #176: thêm `KB.plugin_stats()` + test khóa (getter cộng dồn theo version) +
  test ma trận `test_kb_stats_fill_games_tested` (2 lần ghi -> games_tested = 2).
- #177: sửa `--resume` không `--out` lặng lẽ không làm gì (nay tự tìm lần chạy
  cũ + cờ `--out` + `resumed_from`); 4 test khóa (runner ×2, CLI thật ×2).
- #178: sửa batch ghi KB sai hợp đồng (`remember` cần dict, `record_run` cần
  int game_id) — trước sửa, KB không bao giờ có files/locales/runs/stats
  (chỉ còn games row mồ côi do `KB.close()` commit trước khi đóng); test khóa
  `TestBatchRealKB.test_extract_twice_second_run_uses_kb_profile` (KB thật).
- #179: sửa preset file rò rỉ qua locale (job en ghi profile rồi job zh-Hans
  bị ép file en -> `E_LOCALE_NOT_FOUND`; suite gộp bắt được `(3, 1, 2)` thay
  vì `(3, 2, 1)`); nay chỉ ép file khi locale file khớp locale job; test khóa
  `test_kb_preset_file_does_not_leak_across_locales` (game1.zip thật).
- #180: phát hiện ngoài phạm vi (để đợt tổng cuối): mod PATCH_INPLACE không leo
  lên REBUILD_SECTION khi chuỗi dài hơn 1 byte (`Xin chao` -> `Xin chào`,
  17 -> 18 byte, `E_WRITE_DISABLED` dù aligned size bằng nhau).

## 2. Bằng chứng sandbox (2026-11-16)

- **Full suite: `Ran 1390 tests in 68.0s OK (skipped=1, lỗi=0, fail=0)`**
  (1298 cũ + 92 mới: KB 33 + Batch 28 + Matrix/Docs 22 + tích hợp 9 — 3 sửa
  kiến trúc/KB/matrix + 6 resume/KB thật; 1389 passed, 1 skipped). Chậm nhất
  3.2s (`TestPrintSpeed::test_print_plan_matches_old_wrap`).
- Mô phỏng gộp điều kiện iSH (pty + `COLUMNS=40` +
  `PB3_TEST_TIMEOUT_SCALE=5`, chạy lúc tích hợp): **1390 test, 1389 đạt,
  0 fail, 0 lỗi, Timeout 0, 68.9s**, chậm nhất 2.6s.
- Batch thật trên bản sao dự án (CLI, game1.zip): manifest 3 game (en ok,
  vi `E_LOCALE_NOT_FOUND`, file thiếu `E_INPUT_NOT_FOUND`) -> lô chạy hết,
  `index.csv` + `summary.json` + `failures.txt` đúng mã lỗi; `--resume` không
  `--out` tái dùng đúng thư mục cũ (in "tiếp tục từ: ...", 1 bỏ qua, chỉ chạy
  lại 2 game lỗi); `--resume --out` tương tự.
- KB thật qua CLI: sau batch lần 1, KB có 1 game + 5 file inventory + 1 locale
  (en.json, json_text, 6 key) + 1 choice + 2 runs + plugin_stats
  (csv_table 1, json_text 2); batch lần 2 không ghi locale -> preset từ KB,
  in "dùng tri thức từ KB", chỉ trích 1 file (6 entry) thay vì toàn bộ
  (21 entry, 6 file).
- `support --write` ghi `docs/SUPPORT_MATRIX.md` (20 plugin; 10 × M3 có bằng
  chứng corpus ngày 2026-11-15, còn lại M1/M2, 0 game đã test); `kb import` ->
  `kb export --out` round-trip giữ game/files/locales (sha256_8 `deadbeef`) ->
  `kb prune` (0 byte) -> `kb forget` -> export báo "KB trống"; menu `3 -> m`
  hiện ma trận, `3 -> k` báo "KB trống". Tất cả rc 0.
- **Kiểm chứng độc lập (giải nén zip ra thư mục mới, chạy lại):** xem §6.

## 3. Ma trận hỗ trợ (plugin/engine/đọc/ghi)

Xem `docs/SUPPORT_MATRIX.md` (sinh tự động, đã kiểm chứng nội dung không đổi
sau sửa tích hợp #175). Tóm tắt: 20 plugin builtin; Unity inspect trên IPA
thật 9 Kings v1.0.5 là bằng chứng game thật DUY NHẤT; mọi thứ khác mới test
fixture; không có M4. KB plugin_stats hiện 0 game đã test cho mọi plugin
(chưa có lần chạy nào ghi stats vào KB trong cây này — stats chỉ tăng khi
batch/extract ghi qua `record_plugin_stats`).

## 4. Giới hạn trung thực

- Chưa có file game thật nào cho KB/batch/matrix ngoài fixture tự dựng và IPA
  9 Kings (chỉ dùng cho Unity inspect ở C05, chưa dùng cho KB/batch).
- Fingerprint chỉ hash tối đa 5 entry đặc trưng (§8.2) — KHÔNG phải hash toàn
  bộ IPA; hai game khác nhau nhưng trùng 5 entry đặc trưng + cùng bundle_id/
  version/size có thể trùng fingerprint (đặc tả chấp nhận; `lookup` vẫn xác
  minh hash các file đã lưu).
- `remember()` ghi đè toàn bộ files/locales/choices cũ bằng ảnh chụp mới nhất
  (theo thiết kế KB) — trích locale khác sau đó sẽ thay locales đã lưu.
- Batch `mod` trong lô: PATCH_INPLACE không leo lên REBUILD_SECTION khi chuỗi
  mới dài hơn 1 byte (decisions #180) — game đó fail với `E_WRITE_DISABLED`,
  lô vẫn chạy tiếp.
- `--jobs > 1` dùng ThreadPoolExecutor (GIL + RAM); trên iSH nên giữ jobs=1.
- `support`/`kb`/`batch` chưa chạy trên iSH (chỉ sandbox + mô phỏng gộp).
- Timeout game là best-effort (worker nền quá hạn không bị giết cưỡng bức).

## 5. Kiểm chứng trên iSH (chờ Joseph, sáng 2026-11-16)

```sh
cd /root/pb3
# giải nén pb3_handoff_claude09_sandbox.zip đè lên /root/pb3 (ghi đè file cũ)
python3 run_tests.py --tier full
# kỳ vọng: Ran 1384 tests ... OK (skipped=5, lỗi=0, fail=0), ~20+ phút
# (1298 cũ + 86 mới; kỳ vọng ~1379 passed, 5 skipped — 1 skip sandbox + 4 skip iSH)

python3 menu.py support | head -30
# kỳ vọng: 20 plugin, header cảnh báo "Mức hỗ trợ chỉ đúng với những gì đã kiểm thử", không có M4

python3 menu.py batch tests/fixtures/extract/game1.zip --help
# (batch cần manifest; dùng lệnh dưới)
printf 'path,locale,mode\ntests/fixtures/extract/game1.zip,en,extract\ntests/fixtures/extract/game1.zip,zh-Hans,extract\n' > /tmp/batch09.csv
python3 menu.py batch /tmp/batch09.csv --allow-write
# kỳ vọng: BATCH: 2 game (2 đạt, 0 lỗi); output workspace/output/_batch/<timestamp>/
# có index.csv + summary.json + failures.txt (trống) + 2 thư mục game_0x_game1
python3 menu.py batch /tmp/batch09.csv --resume --allow-write
# kỳ vọng: in "tiếp tục từ: ..." + cùng thư mục output cũ, 2 game bỏ qua ("đã xong (checkpoint)")

python3 menu.py kb export --help
# kỳ vọng: kb export cần 1 tham số <game>; KB có game sau batch (bundle_id None vì game1.zip không có Info.plist)
```

## 6. Kiểm chứng độc lập (Muse, sau khi build zip)

- Zip `pb3_handoff_claude09_sandbox.zip` (**917,032 byte, 332 entry**),
  `testzip` OK, spec md5 khớp (`8867b7b79c5b3f8cb03d68b8c1d1eeb9`,
  `d634a74b1c74ec6c6719430c22d7301a`).
- Giải nén ra `~/workspace/c09_verify/pb3` (thư mục mới), chạy full suite:
  **`Ran 1390 tests in 69.2s OK (skipped=1, lỗi=0, fail=0)`** — 1389 passed,
  0 fail, 0 lỗi, Timeout 0, chậm nhất 2.8s.
- Batch thật trên bản giải nén: manifest 3 game (game1.zip en + zh-Hans +
  file thiếu) -> `BATCH: 3 game (2 đạt, 1 lỗi)`, game lỗi
  `E_INPUT_NOT_FOUND`, `index.csv` + `summary.json` + `failures.txt` đủ;
  `--resume` (không `--out`) tái dùng đúng thư mục cũ, in "tiếp tục từ: ...",
  2 game bỏ qua ("đã xong (checkpoint)"), chỉ chạy lại game lỗi.
- `support`: header cảnh báo + "Không có mục nào đạt M4"; `json_text` M3,
  "2" game đã test (KB trong bản giải nén đã ghi stats từ 2 lần extract —
  getter #176 chạy thật); `godot_pck` M1.
- `kb export com.example.game` -> `E_INPUT_NOT_FOUND` sạch ("không có game
  'com.example.game' trong KB") — đúng thiết kế (game1.zip không có
  Info.plist nên không có bundle_id đó).
- `plugin list`: 20 plugin [ok].
