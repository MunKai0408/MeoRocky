# Tài khoản đợt rà soát tổng cuối — PB3 v3.1 (BearTea), sau Claude 10

Ngày: 2026-11-16 (rạng sáng, Joseph đang ngủ). 5 nhóm song song + 1 bước tích hợp.
Zip RIÊNG đợt này: `pb3_handoff_final_sandbox.zip` — 380 entry (kích thước
+ md5 chính xác ở thông báo bàn giao kèm zip). Zip C10 nguyên vẹn
(`pb3_handoff_claude10_sandbox.zip`, 929.829 byte).

## 1. Tổng quan

Phạm vi 6 hạng mục theo lệnh đợt tổng cuối: (a) rà soát 01–10 + xử lý nợ C10
(F2/F5/F6, #180 chỉ xác minh); (b) nâng cấp + tối ưu toàn bộ 01–10;
(c) bộ test làm mạnh tay; (d) nén IPA kiểu WinRAR; (e) font 4 chế độ;
(f) rà soát thanh tiến độ toàn bộ tool.

Kết quả chung: **1539 test (1395 của C10 + 144 mới), full suite xanh trong
sandbox + mô phỏng gộp iSH xanh**. Verdict: **SANDBOX DONE — RELEASE BLOCKED**,
chờ Joseph kiểm chứng trên iSH vào sáng mai (chưa có gì trong đợt này được
chạy trên iSH).

## 2. Kết quả từng hạng mục

### (a) Rà soát Claude 01–10 — F2/F5/F6 xong hết, #180 vẫn xanh

- **F6 (làm thật):** `services/diagnostics_tool.py` mới — menu [5][3]
  Inventory/hex/strings + CLI `diagnose inventory|hex|strings <file>`.
  Inventory qua `open_vfs` (ZIP/IPA/bundle); hex 8 byte/dòng, phân trang,
  offset tiếp nối; strings quét khối 256KB (không nạp cả file lớn), kèm
  offset + lọc từ khóa, đoán encoding (BOM/UTF-8/UTF-16). Chỉ đọc.
  24 test mới trong `tests/test_final_review.py`. Review table R1–R15 ở
  `handoff/final_review.md`, quyết định #187–193.
- **F5 (xong):** `core/file_dialog.choose_path` nối vào cả hai luồng tương
  tác (dịch vụ + menu chẩn đoán), hủy bằng `q` thì không làm gì (#188).
- **F2 (xong ở bước tích hợp):** nửa mod đã chuyển adapter xuống sdk
  (thông điệp lỗi giống hệt `services.unity_tool`, #189); nửa registry hoàn
  tất bằng TIÊM từ `services/batch_tool.py`
  (`registry=language_tool._registry(app)` ở cả `run_cli` và
  `run_interactive`), `sdk/batch.py::_get_registry` chỉ còn
  `return self._registry` — 0 import services (quét AST khóa trong test).
  Kiểm chứng CLI thật: batch extract `en` trên game zip có
  `localization/en.json` → `1 đạt, 0 lỗi, rc=0` (#190).
- **#180 (chỉ xác minh):** vẫn xanh — test khóa `test_180_vẫn_xanh_qua_
  adapter_sdk_mới` + 3 test unity + 1 test batch đều OK.

### (b) Nâng cấp + tối ưu toàn bộ 01–10

Đo A/B trong sandbox (nhóm tối ưu, `handoff/final_optimize.md`): `display_width`
−41,2%, `detect_locale` −68,6%, `engine_signals` (non-Unity) −73,2%,
`read_text_asset_head` −59,1%, plan cache v2 (list rows): blob −45%,
cache-hit ~−33% (73k rows ~0,55s → ~0,37s), extract 300 JSON −8,2%.
Hai thử nghiệm hoàn lại vì không nhanh hơn (wrap_text hoist, placeholder
dedup). `list_candidates` từng đọc head mỗi ứng viên 2 lần — bước tích hợp
sửa thành đọc 1 lần + test khóa đếm VFS. Quyết định #197.

### (c) Bộ test làm mạnh tay (QT-1..QT-10)

Chi tiết `handoff/final_testsuite.md`. Full: 1536 test / 86,5s (nhóm đo;
was 1395 / 92,7s; 66,5 → 56,3 ms/test dù +141 test); fast 1371 / 29,7s;
smoke 59 / 0,71–0,98s; `--changed` bản ấm 7,1s → 3,3s. Thay đổi chính:
cache `_Facts` trên đĩa, nạp module chọn lọc, bản sao ấm, tiến độ pipe theo
bước 1%, watchdog báo cáo theo pha, output chịu được ENOSPC. QT-7: KHÔNG
chạy test song song (từ chối có lý do); QT-8: KHÔNG xóa test nào; QT-10:
sửa lỗi thật (chạy 1 file từng cắt cả profile; profile khôi phục từ backup,
1394 entry). Quirk còn lại GIỮ NGUYÊN: số passed đếm thừa khi setUpClass
fail (không đổi ngữ nghĩa tóm tắt). Quyết định #196.

### (d) Nén IPA kiểu WinRAR — CLI `compress` + `decompress`, menu [6]

`sdk/compress.py` + `services/compress_tool.py` (36 test), menu [6],
CLI `compress <file.ipa> [--out FILE] [--preset 0-9] [--force]` và
`decompress <file.tar.xz> [--out FILE]`. Nén solid: giải nén các entry về
bytes gốc rồi nén solid bằng LZMA (`tar.xz`, `pb3-manifest.json` là member
đầu: tên/kích thước/sha256 từng entry + toàn file).

- Preset mặc định **6, đã ĐO**: 113 MiB RSS, 15,29:1 trên fixture nén được;
  preset 8 nhỏ hơn nhưng 389 MiB RAM; preset 9 không nhỏ hơn, 678 MiB —
  preset 9 extreme KHÔNG dùng mặc định trên iPhone 11 Pro Max 4GB.
- Luật kích thước (khóa bằng test): đầu vào >2GiB từ chối rõ; đích ≤400MB;
  file nén ra >500MB thì xóa + báo "KHÔNG NÉN ĐƯỢC", không xuất file quá
  cỡ; 400–500MB vẫn xuất kèm ghi chú "miss target".
- File nén ra là **ARCHIVE LƯU TRỮ, không phải IPA cài trực tiếp** — thông
  báo ghi rõ + lệnh `decompress`; `decompress` khôi phục byte-identical
  với IPA gốc (kiểm chứng CLI thật: sha256 khớp `ef27b9cf…`).
- Quyết định 4.1 giữ nguyên: sha256 toàn file sau decompress chỉ là báo
  cáo (report-only), sha256 từng entry mới là kiểm cứng.
- Chi tiết `handoff/final_compress.md`, quyết định #194.

### (e) Công cụ font, 4 chế độ — CLI `font`, menu [7]

`sdk/font.py` (~800 dòng: trình đọc TTF cmap fmt 4/12, head/hhea/maxp/hmtx/
name, glyf/loca; gộp glyph kể cả composite) + `services/font_tool.py`
(`font scan|rebuild|replace|bundle`, 41 test). Menu [7], CLI
`font scan|rebuild|replace|bundle <path> [--with FILE] [--out DIR]`.

- (1) rebuild-all: quét 1 loạt, font nào thiếu ký tự Việt thì gộp glyph
  rồi nhập lại. (2) rebuild 1 font chính: chọn 1 font, gộp, dùng cho toàn
  game. (3) replace: phát hiện ký tự thiếu (đọc cmap) + thay bằng TTF có
  tiếng Việt do người dùng đưa (`--with FILE`). (4) bundle: PB3 nhập sẵn
  `fonts/BeVietnamPro-Regular.ttf` (132.948 byte, 527 glyph, **112/112 ký
  tự tiếng Việt đã kiểm chứng bằng trình đọc thật**) + `fonts/OFL.txt`
  (SIL OFL 1.1, Copyright 2021 The Be Vietnam Pro Project Authors; tải lúc
  build, runtime không gọi mạng).
- Giới hạn trung thực từng chế độ: (1) nặng nhất — gộp glyph / tái tạo
  atlas SDF của TextMeshPro trên điện thoại rất nặng, không có fontTools
  trên iSH, làm tối đa trong khả năng; **atlas SDF KHÔNG tái tạo được**
  (font nghi SDF chỉ được cảnh báo, không sửa); Font object nhúng trong
  Unity (class 128) chỉ liệt kê, không sửa bytes; gộp chỉ cho TrueType
  glyf (không gộp OTF, không .ttc).
- Chi tiết `handoff/final_font.md`, quyết định #195.

### (f) Thanh tiến độ toàn bộ tool — `SteppedReporter`, % thật + ETA thật

`SteppedReporter` mới trong `core/progress.py`; sửa tiến độ thật ở
inspect/mod CLI/tương tác, extraction 6 pha, container list/read. Luật
#152–153: % theo công việc đã xong, ETA theo thời gian thực, tăng từng
1%, không báo láo, giao diện PB3 thống nhất (TTY-gated ANSI, vừa màn hình
30–44 cột). Quyết định #197.

## 3. Việc bước tích hợp đã làm (ngoài 5 nhóm)

1. `ui/cli.py`: subparser TRẦN cho `compress`/`decompress`/`font` (sau khối
   `kb`). Snippet trong `handoff/final_compress.md` §6 còn thêm `path` —
   `add_arguments` đã thêm `path` nên thêm 2 lần sẽ raise ArgumentError, bị
   vòng lặp try/except nuốt, lặng lẽ làm mất `--out`/`--force`/`--preset`
   — đã tránh bằng subparser trần. Đã kiểm chứng CLI thật cả 3 lệnh.
2. `ui/handlers.py` MENU: thêm `("6", compress…)` và `("7", font…)` (M2,
   nhãn/ghi chú tiếng Việt); diagnose lên M2 + note_vi; `ui/screens.py`:
   câu báo lựa chọn không hợp lệ "Gõ số 0-5." → "Gõ số 0-7."
3. Lỗi font scan: `is_sdf_name` xét cả đường dẫn đầy đủ nên TMPDIR
   `.tmp_suite` (chứa "tmp_") làm mọi font bị nghi SDF oan (test_scan_dir
   fail 3 != 1) — sửa ở `sdk/font.py` (chỉ xét basename) + test hồi quy;
   file mở không đóng ở `services/font_tool.py:315` sửa bằng `with`.
4. F2: tiêm registry ở `services/batch_tool.py` (2 chỗ) + bỏ fallback lười
   ở `sdk/batch.py` + cập nhật test khóa trạng thái nửa vời
   (`[("language_tool",)]` → `[]`).
5. `sdk/formats.py::list_candidates`: đọc head 1 lần thay vì 2 + test khóa
   đếm VFS.
6. Test hex CLI: từng khóa cả cột ASCII đầy đủ `"|Hello 9 |"` — fail khi mô
   phỏng iSH (COLUMNS=40) vì `hex_lines` cắt ngắn cột ASCII cho vừa màn
   hình (hành vi cắt là ĐÚNG); test nay khóa phần ổn định theo chiều rộng
   (offset + byte hex) + khẳng định mọi dòng ≤44 cột (#193).
7. Quy tắc /tmp: KHÔNG chạy 2 full suite song song trong 1 cây (đã gây
   Errno 28, hàng trăm fail giả); mọi lần chạy tích hợp dùng
   `TMPDIR=~/workspace/.tmp_suite`; dọn `pb3test-*`/`pb3-*` cũ hơn 15 phút
   trước khi chạy (#198).
8. `README.txt`: thêm lệnh CLI mới (compress/decompress/font/diagnose) +
   menu [1]–[7]; `CURRENT_STATE.md`: cập nhật hàng C10 (F2/F5/F6 xong) +
   thêm hàng đợt tổng cuối.

## 4. Kiểm chứng (sandbox — chưa có gì chạy trên iSH trong đợt này)

| Kiểm chứng | Kết quả |
|---|---|
| Full suite trong cây làm việc | `Ran 1539 tests in 100,8s OK (skipped=1, lỗi=0, fail=0)`, chậm nhất 4,1s (`TestPrintSpeed::test_print_plan_matches_old_wrap`) |
| Mô phỏng gộp iSH (pty/TTY, COLUMNS=40, `PB3_TEST_TIMEOUT_SCALE=5`) | `Ran 1539 tests in 100,3s OK (skipped=1, lỗi=0, fail=0)`, chậm nhất 2,6s (lần 1 có 1 fail test hex do COLUMNS=40 — đã sửa test, xem §3.6; lần 2 xanh) |
| Kiểm chứng độc lập (giải nén zip ra thư mục mới) | spec md5 khớp (`8867b7b7…`, `d634a74b…`); full suite `1539 test, 100,6s OK (skipped=1)` |
| `menu.py compress tests/fixtures/zip/sample.ipa` | rc=0; 672B → 976B (fixture nhỏ, tỉ lệ <1 do manifest — báo thật) |
| `menu.py decompress …/sample.tar.xz` | rc=0; sha256 khớp `ef27b9cf…` — byte-identical |
| `menu.py font scan/rebuild/bundle/replace` (fixture) | cả 4 chế độ rc=0, dry-run mặc định; scan đúng (GameFont thiếu 105/112); rebuild gộp +105 glyph; bundle/replace thay bằng Be Vietnam Pro 112/112 |
| `menu.py diagnose inventory/hex/strings` (sample.ipa) | rc=0 cả 3 (inventory 3 entry; hex 8 byte/dòng; strings kèm offset) |
| Menu tương tác | Banner BearTea; [6] Nén IPA, [7] Công cụ font; chọn sai báo "Gõ số 0-7." |
| `menu.py plugin list` | 20 plugin [ok] |
| `menu.py batch <manifest.csv>` (extract en, CLI thật) | `1 đạt, 0 lỗi, rc=0` — chứng minh tiêm registry F2 |
| Zip C10 | Nguyên vẹn (929.829 byte, không build lại) |

So tốc độ: C10 1395 test / 84,7s (coordinator đo) → final 1539 test /
100,8s (lần chạy tích hợp, đĩa lạnh/tải) — nhóm bộ test đo full 86,5s
trên bản ấm; chênh lệch là phương sai lần chạy, không phải chậm lại do
code (code chỉ thêm 2 test nhanh + bớt 1 lần đọc head mỗi ứng viên).
Suite nhanh hơn rõ rệt ở tầng fast/smoke/`--changed` (xem §2c).

## 5. Giới hạn trung thực (không nâng cấp thành đã kiểm chứng)

- **Chưa có gì trong đợt này chạy trên iSH.** Mọi tính năng mới (nén, font,
  diagnose, bộ test mới, tối ưu) chỉ xanh ở sandbox; hành vi thật trên
  iPhone 11 Pro Max (RAM, tốc độ, COLUMNS) vẫn chờ Joseph kiểm chứng.
- **Nén:** IPA vốn đã là ZIP nén sẵn (LZ4/LZMA assets, texture/audio đã
  nén) nên tỉ lệ 5:1 lossless thường KHÔNG đạt — báo thật kích thước đạt
  được, từ chối chứ không xuất file quá cỡ. Preset 6 đo trên fixture nhỏ
  (113 MiB RSS); IPA thật 200MB sẽ tốn RAM/CPU hơn — theo dõi trên iSH,
  hạ preset nếu nặng. Decompress byte-identical chắc chắn với IPA tạo bằng
  zipfile; với IPA khác, kiểm cứng là sha256 từng entry.
- **Font:** atlas SDF của TextMeshPro KHÔNG tái tạo được; Font nhúng Unity
  chỉ liệt kê; gộp chỉ TrueType glyf. Chế độ (1) nặng nhất, trên điện thoại
  có thể chậm — báo rõ khi chạy.
- **Dịch tự động (nhắc lại):** 1 chuỗi/request + 0,4s gap (Google 429 ở
  request thứ 2) ⇒ 16k chuỗi ~2+ giờ; batching nhiều chuỗi/request là tối
  ưu tương lai, chưa làm.
- **UnityFS v9:** không có bằng chứng tồn tại, không hỗ trợ; v9+ vẫn khóa
  `E_UNSUPPORTED_FORMAT` sạch.
- **Bộ test:** quirk đếm passed thừa khi setUpClass fail còn đó (giữ
  nguyên có chủ ý); không chạy test song song (QT-7).

## 6. Đã sửa / đã đóng trong đợt này

- Nợ C10: F2 (#183) xong, F5 (#184) xong, F6 xong. #180 đã sửa ở C10, xác
  minh vẫn xanh.
- Quyết định mới #187–199 trong `handoff/decisions.md`.
- Tài liệu cập nhật: `CURRENT_STATE.md` (hàng C10 + hàng đợt tổng cuối),
  `README.txt` (lệnh + menu mới), `handoff/account_final.md` (file này).

## 7. Checklist iSH cho Joseph (sáng mai — chỉ chạy zip MỚI NHẤT này)

Trong iSH, ở `/root/pb3` (xóa cây cũ, giải nén zip final rồi chạy):

```sh
cd /root/pb3
unzip -o ~/pb3_handoff_final_sandbox.zip
python3 run_tests.py --tier full
```

- Chỉ chạy bộ của zip này (không chạy lại zip C10). Suite đầy đủ trên iSH
  sẽ lâu (C06: 1025 test mất 1958s; 1539 test ước ~45–60 phút) — cắm sạc,
  tắt auto-lock, để iSH ở foreground.
- Sau suite xanh, chạy thử từng tính năng mới (dry-run trước):
  `python3 menu.py plugin list` (20 [ok]);
  menu tương tác: kiểm [6] Nén IPA, [7] Công cụ font, [5][3] Inventory/
  hex/strings trên `workspace/input/game.ipa`;
  `python3 menu.py diagnose inventory workspace/input/game.ipa`;
  `python3 menu.py font scan <thư mục game đã giải nén>`;
  `python3 menu.py compress workspace/input/game.ipa --out /tmp/game.tar.xz`
  (theo dõi RAM; nếu nặng thì `--preset 6` mặc định đã là mức đo được —
  đừng ép preset 9 trên máy thật khi chưa đo);
  `python3 menu.py decompress /tmp/game.tar.xz --out /tmp/khoi-phuc`
  rồi so sha256 với IPA gốc.
- Nếu suite iSH NOT GREEN: chụp màn hình phần fail + gửi lại (như lần C06),
  đừng sửa tay trong cây iSH.

## 8. Nhắc lại khi bàn giao (2 đính chính còn nợ Joseph từ trước)

1. Muse từng nói sai 01:44–01:47 rằng engine khác ngoài Unity là ngoài phạm
   vi — **sai: Claude 08 ĐÃ làm đa engine** (Godot/Unreal/Cocos/Defold/
   GameMaker).
2. Ước tính dịch 16k chuỗi 10–30 phút là **SAI** — thực tế ~2+ giờ với
   1 chuỗi/request + 0,4s gap; batching nhiều chuỗi/request là tối ưu
   tương lai.
