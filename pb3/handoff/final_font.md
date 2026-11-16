# TÀI KHOẢN NHÓM FONT — ĐỢT TỔNG CUỐI (hạng mục e)

Trạng thái: **SANDBOX DONE (2026-11-16)** — test của nhóm 41/41 xanh,
suite nhanh 1344 test chỉ còn 1 fail (không phải do nhóm font, xem §6).
**iSH: NOT RUN** (chờ Joseph; coordinator hợp nhất rồi mới kiểm chứng).

Phạm vi: công cụ font 4 chế độ, CLI `font` + menu (chỉ nối COMMANDS;
menu và subparser CLI do coordinator nối ở bước tích hợp, xem §5).
Không build zip, không sửa `handoff/decisions.md` hay `CURRENT_STATE.md`.

## 1. Đã làm

### `sdk/font.py` (mới, ~800 dòng, chỉ stdlib, Python 3.8+)
- Trình đọc TTF tối thiểu: bảng offset + `cmap` format 4/12, `head`/
  `hhea`/`maxp`/`hmtx`/`name`, `glyf`/`loca`. `parse_font`, `coverage`,
  `missing_chars`, `verify_font` (checksum từng bảng + checksum toàn
  font theo đúng đặc tả TrueType — xem §4 về 2 lỗi đã bắt được).
- Tập ký tự tiếng Việt bắt buộc: 112 ký tự = ă â đ ê ô ơ ư (thường +
  hoa, 14) + dải ghép sẵn U+1EA0–U+1EF9 (90) + dấu kết hợp U+0300/0301/
  0302/0303/0306/0309/031B/0323 (8).
- Gộp glyph (`merge_glyphs`, `merge_vietnamese`): copy outline glyf +
  loca + hmtx + entry cmap từ font nguồn vào font đích; xử lý cả glyph
  đơn và glyph composite (copy đệ quy toàn bộ component, viết lại id
  component theo bảng gộp); dựng lại cmap (format 4 có `idRangeOffset`
  hoặc format 12 — chọn bản nhỏ hơn khi toàn BMP), `maxp`/`hhea`/
  `loca`/`hmtx` mới, tính lại checksum + `checkSumAdjustment`; tự xác
  minh (parse lại + checksum) trước khi trả. Glyph gốc giữ nguyên byte,
  hmtx gốc giữ nguyên.
- Liệt kê Font object nhúng trong SerializedFile (`list_unity_font_objects`,
  class 128, đọc tên từ `m_Name`); heuristic tên SDF (`is_sdf_name`).

### `services/font_tool.py` (mới)
- `font scan <path>`: quét file/thư mục/ZIP/IPA/bundle Unity; báo từng
  font thiếu bao nhiêu ký tự (đọc cmap thật), font CFF, font collection,
  font nhúng Unity, font nghi là atlas SDF.
- `font rebuild <path> [--font TÊN] [--donor FILE]`: (1) không có
  `--font`: gộp glyph còn thiếu vào MỌI font TTF thiếu chữ; (2) có
  `--font`: dựng đúng 1 font chính rồi dùng đúng 1 font này cho toàn bộ
  game (thay mọi font khác bằng font chính đã dựng).
- `font replace <path> [--font TÊN] --with FILE`: (3) thay bằng font
  TTF người dùng đưa vào; kiểm độ phủ trước — font thay thế thiếu ký tự
  tiếng Việt thì từ chối (E_CORRUPT, liệt kê ký tự thiếu).
- `font bundle <path> [--font TÊN]`: (4) thay bằng font nhập sẵn.
- Ghi ngược: dry-run mặc định, chỉ ghi khi `--allow-write`; không ghi
  đè nguồn (file rời -> `workspace/output/<Game>/`, ZIP/IPA ->
  `rebuild_zip` ra file mới, thư mục -> copy cây rồi thay trong bản
  copy; tên đích versioned `.v1`/`.v2` khi trùng).
- Tiến độ luật #152–153: `SteadyProgress` qua `_ProgressAdapter`
  (thanh Overall khi có, không thì `SteadyProgress` riêng); tổng thật
  (số font khi quét, số glyph sẽ copy — kể cả component composite —
  khi gộp), bước từng 1% qua `step_pcts`, ETA thật.
- `run_interactive`: hỏi chế độ 1–4, đường dẫn, font, font thay thế;
  dry-run trước rồi hỏi mới ghi thật.

### `ui/handlers.py` (chỉ thêm 1 entry COMMANDS `"font"`, neo sau dòng `"support"`)
- Không sửa mục MENU (coordinator nối ở bước tích hợp).

### `fonts/` (mới) — font nhập sẵn chế độ (4)
- `BeVietnamPro-Regular.ttf` (132.948 byte) + `OFL.txt` (SIL OFL 1.1).
- Nguồn tải lúc build: `github.com/google/fonts` (nhánh main,
  `ofl/bevietnampro/`); runtime PB3 không gọi mạng.
- Đã đo bằng trình đọc thật (`sdk/font.py`, không dùng fontTools):
  family "Be Vietnam Pro", 527 glyph, **đủ 112/112 ký tự tiếng Việt**,
  `verify_font` sạch (checksum từng bảng + toàn font đúng).
- Giấy phép: Copyright 2021 The Be Vietnam Pro Project Authors,
  SIL Open Font License 1.1, file `OFL.txt` kèm trong gói.

### `tests/fixtures/font/build_fixtures.py` (mới) + `tests/test_font.py` (mới, 41 test)
- Builder dựng TTF tối thiểu tổng hợp (chỉ stdlib, độc lập với
  `sdk/font.py`): glyph rỗng, glyph composite, cmap format 4/12,
  stub OTF/TTC; KHÔNG dùng font thật của game.
- Test khóa: detect kind, cmap format 4/12 round-trip (kể cả trên map
  thật của Be Vietnam Pro), độ phủ 112 ký tự, font nhập sẵn + giấy phép,
  gộp đơn (105 glyph, glyph gốc byte-identical, hmtx giữ nguyên),
  gộp composite (copy 3 glyph, component viết lại đúng id), bỏ qua/báo
  donor thiếu, từ chối OTF/TTC, verify bắt byte lật, quét thư mục/ZIP/
  bundle Unity, rebuild dry-run/write (cả 4 chế độ), rebuild trong ZIP
  round-trip (entry khác giữ nguyên), tiến độ đơn điệu, CLI + parser +
  handlers + tương tác.

## 2. Từng chế độ làm được gì (đã kiểm chứng trên fixture)

| Chế độ | Font .ttf rời | Entry .ttf trong ZIP/IPA | Font nhúng Unity | Font CFF (.otf) | Atlas SDF |
|---|---|---|---|---|---|
| (1) quét + gộp | có (gộp glyph thiếu) | có (rebuild ZIP) | chỉ liệt kê | quét được, không gộp | báo rõ, không gộp |
| (2) 1 font chính | có | có | chỉ liệt kê | thay bằng font chính được | báo rõ, không gộp |
| (3) thay bằng font người dùng | có | có | không | có (đổi bytes) | thay được (đổi bytes) |
| (4) thay bằng font nhập sẵn | có | có | không | có (đổi bytes) | thay được (đổi bytes) |

Chế độ (3)/(4) thay bằng cách đổi bytes font: với font .ttf rời hoặc
entry .ttf trong container thì dùng được cho mọi loại font đích (kể cả
font SDF, vì không cần tái tạo atlas — chỉ thay file font). Với font
nhúng trong SerializedFile thì cả 4 chế độ đều chưa thay được (xem §3).

## 3. Giới hạn trung thực (bắt buộc đọc)

- **Chế độ (1) nặng nhất.** Gộp glyph vào font cũ trên điện thoại đã
  nặng; **tái tạo atlas SDF của TextMeshPro KHÔNG làm được trong đợt
  này**. Gặp font nghi là SDF (tên chứa "sdf"/"textmeshpro"/"tmp_" hoặc
  Font object nhúng tên SDF) thì tool báo rõ "không hỗ trợ atlas SDF
  của TextMeshPro trong đợt này (không tái tạo được atlas SDF trên điện
  thoại); dùng chế độ (3) thay bằng font TTF có tiếng Việt hoặc chế độ
  (4) dùng font nhập sẵn của PB3" và hướng dùng chế độ (3)/(4).
- **Font nhúng trong SerializedFile (Font object, class 128): đợt này
  chỉ liệt kê** (tên + path_id), KHÔNG sửa được bytes object (chưa có
  parser/writer cho Font object); thay thế chỉ làm được với file .ttf
  rời hoặc entry .ttf trong container ZIP/IPA. Quét bundle Unity thật
  vẫn liệt kê được Font object (đã thử trên fixture `.assets`: 0 Font
  object, không crash).
- **Chỉ gộp được font TrueType có outline glyf** (sfnt 0x00010000/
  "true"). Font CFF (.otf, "OTTO") đọc được cmap nhưng KHÔNG gộp được
  (E_UNSUPPORTED_FORMAT); font collection (.ttc) chưa hỗ trợ.
- Gộp glyph chỉ copy outline + advance/lsb; **không** cập nhật OS/2
  (unicodeRanges), `post` (tên glyph), kerning (`kern`/`GPOS`), hinting
  cho glyph mới; chữ ký số DSIG bị bỏ (font gộp không còn chữ ký gốc);
  cmap dựng lại chỉ giữ 1 subtable (format 4 hoặc 12, chọn bản nhỏ hơn
  khi toàn BMP).
- Ghi ngược file thường (không phải Unity) xuất file/container mới vào
  `workspace/output/<Game>/`, **không** đóng gói lại container ngoài
  (ZIP/IPA chỉ thay entry font, giữ nguyên entry khác); bundle trong
  IPA thì font nhúng vẫn chưa thay được (xem trên).

## 4. Lỗi thật tìm được khi viết code (đã sửa + test khóa)

- `numGlyphs` trong `maxp` là u16, không phải u32: ghi u32 làm hỏng 2
  byte kế tiếp và parse ra 0 glyph (bắt được nhờ smoke test trên font
  thật; test `test_merge_simple_full_coverage` khóa `num_glyphs` đúng).
- Checksum bảng `head` tính với `checkSumAdjustment` = 0, còn checksum
  toàn font là tổng mọi u32 của TOÀN FILE (gồm adjustment đã lưu) —
  bản đầu tính sai cả hai chiều nên font thật Be Vietnam Pro bị báo
  checksum sai; đã sửa theo đặc tả, font thật nay verify sạch.
- Cmap format 4 kiểu delta-only phình to (3.180 byte so với 1.276 gốc):
  đã dùng `idRangeOffset` + `glyphIdArray` cho đoạn delta không đều —
  round-trip đúng 459 entry, cmap gộp nay 1.272 byte.
- Fixture builder có bug `deltas.pop()` chạy trước `len(deltas) != 1`
  làm mọi đoạn cmap thành range-mode (glyph id sai tới 65535): đã sửa,
  test `test_format4_roundtrip` khóa map đúng.
- `PB3Error` mang thông điệp riêng ở `.detail`, không phải
  `.message_vi`: service dùng `_exc_text`/`_format_exc` để hiện detail.

## 5. Việc cho coordinator (tích hợp, ngoài phạm vi nhóm font)

- Thêm subparser `font` vào `ui/cli.py` (cùng mẫu khối `support`:
  `sub.add_parser("font", help="...")`); `add_arguments` của
  `services/font_tool.py` đã sẵn (action + path + `--font`/`--donor`/
  `--with`/`--out`/`--allow-write`/`--chars`). Hiện tại `menu.py font
  ...` bị argparse từ chối (đã kiểm chứng) vì chưa có subparser này —
  đây là bước tích hợp còn thiếu, KHÔNG phải lỗi của nhóm font.
- Nối menu (mục MENU trong `ui/handlers.py` chưa sửa theo yêu cầu).
- Sau tích hợp, chạy lại `python3 -m unittest tests.test_font` +
  full suite rồi mới kiểm chứng iSH.

## 6. Bằng chứng sandbox (Python 3.12.3, 2026-11-16)

- `python3 -m unittest tests.test_font`: **Ran 41 tests in ~1s OK**.
- `python3 run_tests.py --tier fast`: `Ran 1344 tests ... FAILED
  (skipped=1, lỗi=0, fail=1)` — 1 fail duy nhất là
  `tests.test_app_menu_cli.TestMenuInProcess.test_unimplemented_shows_clear_status_and_changes_nothing`:
  test này kỳ vọng menu [5]→[3] còn "CHƯA TRIỂN KHAI" vì
  `services.diagnostics_tool` "chưa có", nhưng nhóm song song khác đã
  tạo `services/diagnostics_tool.py` (21:41) trong cùng cây làm việc
  nên mục đó nay "đã nối dịch vụ" — **không phải do nhóm font**
  (nhóm font không sửa menu, không tạo file đó). Coordinator hợp nhất
  sẽ cập nhật test này theo thực tế mới.
- `python3 run_tests.py --tier full` (chạy 2 lần): lần 1 `Ran 1508
  tests ... FAILED (skipped=7, lỗi=292, fail=62)`, lần 2 `Ran 1508
  tests in 140.8s FAILED (skipped=2, lỗi=104, fail=12)`. **Cả hai lần
  đều hỏng do môi trường, không phải do code**: `/tmp` là tmpfs 512M,
  5 nhóm chạy song song cùng copy toàn bộ dự án ra `/tmp` (test CLI
  chậm) làm đầy đĩa — 86/104 lỗi lần 2 ghi rõ `[Errno 28] No space
  left on device`; 12 fail còn lại đều là hệ quả (copy dự án dở dang
  -> `None/menu.py`, cache plan ghi không được, đếm plan lệch 1536 !=
  1534) + 1 fail menu đã biết ở trên. **Cả hai lần full đều có 0 fail /
  0 lỗi thuộc `tests.test_font`.** Kết quả full này không dùng được làm
  bằng chứng xanh — cần chạy lại khi các nhóm không chạy suite cùng
  lúc (hoặc dọn `/tmp`), coordinator quyết định.
- Kiểm thử thật trên fixture (không chỉ test): scan thư mục 2 font
  (thiếu 105/112 + đủ); rebuild mode 1 ghi 2 font đủ 112/112, verify
  sạch, nguồn không đổi; mode 2: font còn lại bằng đúng bytes font
  chính; rebuild trong ZIP: entry font mới đủ chữ, entry `data.txt`
  giữ nguyên; replace/bundle: file đích đọc ra family "Be Vietnam Pro".
- Font nhập sẵn: parse + coverage + verify bằng `sdk/font.py` trên
  file thật trong `fonts/` (xem §1).
- Test luật: `tests.test_rules_ish_only`, `test_rules_where`,
  `test_color_display` (32 test) OK.

## 7. Kiểm chứng trên iSH — NOT RUN (chờ Joseph)

Sau khi coordinator hợp nhất + build zip mới, trong `/root/pb3`:

```sh
python3 run_tests.py --tier full
python3 menu.py font scan tests/fixtures/unity/sample.unity3d
```

Kỳ vọng: suite xanh (trừ fail đã biết ở §6 nếu coordinator chưa cập
nhật test menu); scan fixture Unity báo 0 font (không crash). Thử tay
trên thư mục font thật (copy font game ra thư mục riêng, không sửa
nguồn): `font scan` phải liệt kê đúng font thiếu chữ; `font rebuild`
dry-run không ghi gì; `--allow-write` ghi vào `workspace/output/`.
