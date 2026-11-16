# TÀI KHOẢN CLAUDE 07 — TRÍCH XUẤT NGÔN NGỮ

Trạng thái: **SANDBOX DONE (2026-11-16)** — full suite xanh. **iSH: NOT RUN**
(chờ Joseph sáng 2026-11-16, xem §5). Không đánh dấu COMPLETE cho tới khi
Joseph kiểm chứng trên iSH.

## 1. Đã làm (sandbox, 2026-11-16)

### Discovery — `sdk/discovery.py` (decisions #129–141)
- Bảng locale (`config/locales.toml` + builtin): `load_locale_table`,
  `detect_locale` (từ tên/đường dẫn/nội dung), `export_filename`
  (`en` -> `English.json`, `zh-Hans` -> `Chinese (Simplified).json`).
- `detect_engine`: Unity (magic `UnityFS` / SerializedFile) -> `CHẮC CHẮN`,
  ngược lại `unknown` / `CHƯA ĐỦ BẰNG CHỨNG` — engine không nhận diện được
  vẫn trích xuất bằng parser độc lập engine (đặc tả 7.2).
- `inventory`: đi cây VFS (kể cả container lồng `::`, sâu tối đa 4),
  phân loại `file`/`container`/`unity`/`excluded`, tính `text_score`
  (tỉ lệ ký tự in được ở 4KB đầu). Loại trừ: font/atlas, log/cache/crash,
  file nhị phân rõ ràng.
- `find_referenced_paths`: file JSON nào trỏ tới >= 2 file localization
  khác thì là index/catalog — bỏ qua làm ứng viên, giữ lại tham chiếu.

### Pipeline 7 bước — `sdk/extraction.py`
1. Mở nguồn (file/thư mục/ZIP/IPA/container lồng) qua `open_source_vfs`.
2. Inventory + exclusions (lý do loại trừ ghi vào report).
3. Discovery: engine + locale cho từng file.
4. Ứng viên: mỗi plugin format (Claude 06) qua `list_candidates` +
   `probe` của SDK — **không viết lại parser**; thêm **sniff pass** cho file
   không có đuôi khớp (TextAsset Unity không đuôi): dùng `_sniff` của chính
   plugin, chỉ khi mẫu đầu gần như toàn text (tỉ lệ >= 0.9 hoặc BOM UTF-16).
   `generic_strings` là lưới cuối, giới hạn 25 ứng viên/lớp theo text-score.
5. Scoring + arbitration theo Phụ lục C (`sdk/arbitration.py`, xem dưới):
   theo từng nhóm locale; file không mang locale mỗi file một nhóm riêng
   (không gom chung thành một nhóm "None"); nhóm chỉ có một lựa chọn đạt
   ngưỡng thì tự chọn (không có gì để hỏi); `--file`/preset ép chọn đúng
   file, bỏ qua trọng tài; ứng viên nhận diện bằng nội dung (sniff >= 0.7)
   được tính `ext_match = 1.0` (định dạng đã xác định bằng nội dung, không
   phải bằng đuôi file).
6. Parse -> IR (`sdk/ir.py`): chuẩn hóa toàn bộ bảng, giữ key/value/
   locale/status/source; key trùng chỉ giữ bản ghi đầu (bản sau status
   `conflict`); entry unparsed (markup XLIFF/RESX) bỏ qua khỏi file xuất
   kèm warning; key sinh (`row_000001`, `off_00000000`) ổn định giữa các
   lần chạy.
7. Export + verify: JSON key-value theo locale + `extraction_report.txt`/
   `.json` + `extraction_checkpoint.json`. Trùng tên file xuất thì chèn
   stem nguồn (`English.json` + `English.en.json`); đường dẫn ảo dùng tên
   TextAsset làm stem (`config.json`, không phải `sample.json`).

### Ghi ngược — `sdk/extraction.apply_edits` (đặc tả 7.6 + 6.7)
- Dry-run mặc định; `--allow-write` mới ghi thật, versioned vào
  `workspace/output/<Game>/`, không ghi đè nguồn.
- Plugin/định dạng con chỉ đọc (sqlite_loc, generic_strings, plist nhị
  phân, MO) từ chối bằng `E_WRITE_DISABLED`; entry unparsed và key không
  có trong file gốc bỏ qua kèm warning; key trùng chỉ ghi bản ghi đầu.
- **Bảng đa locale: chỉ sửa đúng locale của file đã xuất.** JSON en sửa
  vào CSV en/zh trước đây ghi đè cả cột zh bằng giá trị en (bug tìm thấy
  khi kiểm thử, đã sửa + có test khóa).
- **TextAsset Unity: patch lại vào bundle/.assets** qua
  `sdk.unity.apply_patch` (PATCH_INPLACE, leo thang REBUILD_SECTION khi
  dài hơn; diffverify theo 6.7), xuất chính đơn vị đó (6.6: bundle trong
  IPA -> chỉ bundle đó, không đóng gói lại IPA, kèm nhắc re-sign). Trước
  đây ghi file script rời (không dùng được) — đã sửa.
- Checkpoint: khóa theo hash nguồn; resume dùng lại kv theo từng file
  (hash sha256 khớp) và dựng lại IR từ kv. IR dựng lại từ checkpoint chỉ
  có key duy nhất (bản ghi trùng thứ hai không lưu trong checkpoint kv) —
  không ảnh hưởng dịch vì bản đầu đã thắng.

### Exporters — `sdk/exporters.py`
- `export_key_value_json`, `verify_export_bytes` (đọc lại + so sánh),
  `placeholders` / `check_placeholders` (`{name}`, `%s`, `%d` giữ nguyên),
  `check_mojibake` (dấu hiệu UTF-8 đọc sai encoding), `report_text` /
  `report_json`.

### Dịch vụ — `services/language_tool.py`
- Lệnh `extract <path> [--locale L] [--out DIR] [--dry-run] [--file VPATH]
  [--resume] [--preset FILE] [--save-preset FILE]`; `extract <path>
  --apply <json-đã-sửa> [--allow-write]`.
- Menu [2] tương tác: hỏi đường dẫn, hỏi chọn file khi nhiều ứng viên
  ngang điểm, in tóm tắt + cảnh báo, hỏi ghi ngược sau khi trích xong.
- Preset: file JSON `{"version": 1, "source", "file", "locale"}`.

### Test mới — 55 test (1025 -> 1080)
- `tests/test_extraction.py` (45 test, tầng fast): discovery/locale/
  engine/inventory, pipeline game1 (21 entry / 6 file), trọng tài
  (auto/ask/pin/single-option), Unity (config auto-extract qua sniff,
  raw-scan bị loại mặc định), checkpoint/resume, ghi ngược (CSV đúng
  locale, Unity bundle round-trip + đọc lại, chỉ đọc từ chối, unparsed
  giữ nguyên), IR, key sinh ổn định, garbage/empty -> none.
- `tests/test_language_cli.py` (10 test, tầng slow): CLI thật trên bản
  sao dự án (extract zip/Unity/pin/locale/ambig/garbage, apply dry-run +
  write thường + write Unity), menu [2] tương tác qua script.
- `tests/fixtures/extract/`: `build_fixtures.py` (dựng lại cho cùng kết
  quả) + `game1.zip`, `ambig.zip`.
- Cập nhật test cũ theo hành vi mới: `test_app_menu_cli.py`
  (chỉ "4" còn chưa triển khai; `extract x.ipa` giờ trả E_NO_CANDIDATE
  thay vì E_NOT_IMPLEMENTED; test module-missing chuyển sang mục 4 batch).

### Sửa kiến trúc — trọng tài chuyển xuống SDK (decision #129)
- `sdk/extraction.py` import `registry.arbitration` làm vỡ test kiến trúc
  `test_core_does_not_import_registry` (sdk không được import registry).
  Logic chấm điểm thuần túy đã chuyển sang `sdk/arbitration.py` (nội dung
  giữ nguyên); `registry/arbitration.py` chỉ còn re-export để tương thích
  ngược (`from registry import arbitration` vẫn chạy, test_registry xanh).

### Hai bug thật tìm thấy khi kiểm thử (đã sửa + test khóa)
- **Hỏi mà người dùng bỏ qua thì decision thành "none"** (sai: có ứng viên,
  chỉ là chưa chọn; CLI báo E_NO_CANDIDATE gây hiểu lầm). Nay: nhóm chưa
  trả lời giữ decision "ask" + candidates; trả lời một phần thì trích phần
  đã chọn, nhóm còn lại ghi vào `result.candidates` để report/CLI hiển thị.
- **Lỗi locale không tồn tại báo "có sẵn: không có"**: danh sách locale
  có sẵn trước đây lấy từ `result.files` (đã bị lọc nên luôn rỗng). Nay thu
  thập `all_locales` trước khi lọc (cả nhánh checkpoint, nhánh này trước
  đây còn bỏ qua luôn bộ lọc `--locale`).

## 2. Định dạng: mức hỗ trợ

Trích xuất dùng đúng 12 plugin Claude 06 qua `parse`/`build` của SDK:

| Plugin | Trích xuất | Ghi ngược |
|---|---|---|
| json_text, csv_table, xml_strings, yaml_ini, properties, apple_plist (XML), apple_strings, po_mo (PO), xliff, resx | có | có (build/verify của plugin) |
| apple_plist (binary), po_mo (MO) | có (đọc) | **không** — E_WRITE_DISABLED |
| sqlite_loc | có (đọc) | **không** — E_WRITE_DISABLED |
| generic_strings | có (lưới cuối, cảnh báo false positive) | **không** — E_WRITE_DISABLED |

- XLIFF/RESX: entry có markup (`<g>`, `<mrk>`, `<x/>`...) -> status
  `unparsed`, bỏ qua khỏi file xuất, giữ nguyên khi ghi ngược.
- Key trùng: chỉ ghi bản ghi đầu; bản sau giữ nguyên khi ghi ngược.
- Giới hạn kế thừa từ Claude 06: ghi ngược kiểu rebuild làm mất
  comment/khoảng trắng gốc; YAML là tập con (không có PyYAML trên iSH).

## 3. Bằng chứng sandbox (Python 3.12.3, 2026-11-16)

Full suite: **`Ran 1080 tests in 44.3s OK (skipped=1, lỗi=0, fail=0)`**
(1025 cũ + 55 mới). Tầng: fast 955, slow 61, ish 11, meta 53.

### game1.zip — `extract tests/fixtures/extract/game1.zip`
- Decision `done`, engine `unknown` (CHƯA ĐỦ BẰNG CHỨNG — đúng, không có
  dấu hiệu Unity), **21 entry, 6 file**:
  `localization/app.properties` (properties, 4 entry -> `app.json`,
  key `dup` giữ bản đầu), `localization/strings.csv` (csv_table, en 2 ->
  `English.json`, zh 2 -> `Chinese.json`), `localization/ui.strings`
  (apple_strings, 2 -> `ui.json`), `localization/en.json` (json_text, en,
  6 -> `English.en.json` vì `English.json` đã bị CSV chiếm),
  `localization/zh-Hans.json` (json_text, zh-Hans, 6 ->
  `Chinese (Simplified).json`).
- Bỏ qua có lý do: `data/font.ttf` (font/atlas), `logs/debug.log`
  (log/cache), `localization/index.json` (index/catalog, tham chiếu 2
  file), `raw.bin` (điểm 0.16 dưới ngưỡng 0.55), `inner.zip::en.json`
  (thua trọng tài 0.71 < 0.91). Không lỗi.

### sample.unity3d — `extract tests/fixtures/unity/sample.unity3d`
- Decision `done`, engine `unity` **CHẮC CHẮN**, **3 entry, 1 file**:
  TextAsset `config` (JSON, không đuôi) tự trích qua sniff ->
  `config.json` = `{"level": "1", "lang": "vi", "title": "9 Kings"}`.
- `greeting` / `longtext` (chuỗi thô) bị loại mặc định (0.16 / 0.28 dưới
  ngưỡng); `--file` ép chọn `greeting` -> 1 entry, cảnh báo dò chuỗi thô.

### ambig.zip — hỏi người dùng
- Decision `ask`, 2 lựa chọn `a/en.json` / `b/en.json` (cùng 0.71);
  chọn [2] -> trích `b/en.json`, `English.json` = `{"hello": "Hello B"}`.
  Bỏ qua (trống) -> vẫn `ask`, không báo sai thành "không có ứng viên".

### Ghi ngược (round-trip, đã kiểm bằng tay + test)
- CSV: sửa `play` -> `Chơi` trong `English.json`, apply -> chỉ cột en đổi
  (`play,Chơi,开始`), cột zh nguyên; key lạ `khong_co` bỏ qua.
- Unity: sửa `title` -> `9 Kings VIET NAM` (dài hơn) -> dry-run báo
  `REBUILD_SECTION`, 7151 -> 7167 byte, không ghi; `--allow-write` ghi
  `workspace/output/sample/sample.unity3d`; đọc lại bundle: config mới,
  greeting/longtext nguyên, verify "1 object đổi đúng dự kiến".
- Chỉ đọc: report giả trỏ `sqlite_loc` -> `E_WRITE_DISABLED`.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Fixture only, chưa có test trên game thật (M3 ≠ M4): trích xuất trên
  IPA 9 Kings thật chưa chạy (xem §5, bước tùy chọn).
- Ghi ngược file thường (không phải Unity) xuất file đã build lại vào
  `workspace/output/<Game>/`, **không** đóng gói lại container/ZIP/IPA
  ngoài. Ghi ngược bundle trong IPA xuất đúng bundle đó; cần ký lại
  (re-sign) gói ngoài thì game mới nhận (đặc tả 11.1), PB3 không tự ký.
- Trích xuất toàn bộ IPA ~200MB trên iSH sẽ chậm (quét từng entry +
  TextAsset, như inspect trước đây ~9 object/giây trên màn hình); nên
  chuyển hướng output ra file hoặc trích một bundle đơn.
- `generic_strings` chỉ dò chuỗi thô: có thể có false positive (luôn kèm
  cảnh báo trong report + khi in tóm tắt).
- Checkpoint resume: IR dựng lại chỉ có key duy nhất (xem §1).
- YAML subset, mất comment/khoảng trắng khi ghi ngược (kế thừa C06).

## 5. Kiểm chứng trên iSH — NOT RUN (chờ Joseph, sáng 2026-11-16)

**Đợt fix iSH 2026-11-16 (sau sandbox C07):** 12 lỗi trên iSH đã sửa hết
(decisions #142–150: cache EIO do `os.urandom(1MB)` trong fixture test,
pid-marker do `getppid()` sai trong daemon thread trên iSH, ngưỡng in plan
nhân hệ số máy + cache độ rộng từ, ngân sách tầng fast nhân hệ số máy).
Sandbox: `Ran 1084 tests in 44.3s OK (skipped=1, lỗi=0, fail=0)`; mô phỏng
gộp iSH (pty + `COLUMNS=40` + `PB3_TEST_TIMEOUT_SCALE=5`): 1084 test, 0 fail,
0 lỗi. Zip đã rebuild (cùng tên, snapshot mới nhất).

Kỳ vọng sau khi giải nén zip Claude 07 đè lên `/root/pb3` (như mọi lần):

```sh
cd /root/pb3
python3 run_tests.py --tier full
```

Kỳ vọng: `Ran 1084 tests ... OK (skipped=5, lỗi=0, fail=0)` — **1079 passed,
5 skipped** (2 thiếu `tomllib`, 1 máy có `/dev/location`, 1 UnityPy có mặt,
1 test signal ngoài main thread; số skip có thể lệch 1, quan trọng là 0 fail
0 lỗi). Thời gian khoảng 20 phút (đợt trước 1958s vì riêng test so sánh
đường in cũ đã mất 722s — nay đã thu nhỏ trên iSH). Nếu fail, xem
`reports/failures.txt` thay vì chụp màn hình.

Trích xuất CLI (trong `/root/pb3`):

```sh
python3 menu.py extract tests/fixtures/extract/game1.zip --out /tmp/ext-game1
python3 menu.py extract tests/fixtures/unity/sample.unity3d --out /tmp/ext-unity
python3 menu.py extract tests/fixtures/extract/ambig.zip --out /tmp/ext-ambig
```

Kỳ vọng:
- game1: `tổng: 21 entry, 6 file`; đủ 6 file JSON + report trong
  `/tmp/ext-game1` (kể cả `English.en.json` và `Chinese (Simplified).json`).
- Unity: `engine: unity (CHẮC CHẮN ...)`; `config.json` có
  `{"level": "1", "lang": "vi", "title": "9 Kings"}`.
- ambig: `CHƯA TRÍCH XUẤT — nhiều ứng viên ngang điểm`, liệt kê `a/en.json`
  và `b/en.json`; gõ `2` rồi Enter để chọn file b (trên iSH stdin là TTY
  nên hỏi thật được).

Ghi ngược trên fixture (trong `/root/pb3`):

```sh
python3 menu.py extract tests/fixtures/unity/sample.unity3d --out /tmp/ext-unity
# sửa /tmp/ext-unity/config.json: "title": "9 Kings" -> "9 Kings VIET NAM"
python3 menu.py extract tests/fixtures/unity/sample.unity3d --apply /tmp/ext-unity/config.json
python3 menu.py extract tests/fixtures/unity/sample.unity3d --apply /tmp/ext-unity/config.json --allow-write
```

Kỳ vọng: lần đầu (dry-run) báo `đổi 1 key` + `KHÔNG ghi file: đây là
dry-run`; lần hai báo `đã ghi output: workspace/output/sample/sample.unity3d
(không ghi đè nguồn)`. Kiểm tra lại bằng:

```sh
python3 menu.py unity inspect workspace/output/sample/sample.unity3d
```

Kỳ vọng: TextAsset `config` đọc được giá trị mới, các TextAsset khác nguyên.

Menu [2] tương tác (trong `/root/pb3`):

```sh
python3 menu.py
```

Chọn `2`, nhập `tests/fixtures/extract/game1.zip`, locale để trống,
không ghi ngược, rồi `0` để thoát. Kỳ vọng: `tổng: 21 entry` và
`workspace/output/game1/English.json` tồn tại.

Tùy chọn — game thật (chỉ khi rảnh, vì chậm): trích xuất IPA 9 Kings
chuyển hướng ra file, để chạy nền rồi xem kết quả sau:

```sh
python3 menu.py extract workspace/input/game.ipa --out /tmp/ext-9kings > /tmp/ext-9kings.log 2>&1
```

Kỳ vọng: engine `unity` CHẮC CHẮN, TextAsset JSON trong game được trích
ra JSON; file chuỗi thô bị loại mặc định (dùng `--file` để ép chọn nếu cần).
Không patch IPA thật ở bước này.

## 6. Việc cho Claude 08 (chưa làm)
- Xem `PB3_v3.1_10_Claude_Prompts.txt` prompt 08 + đặc tả §8 (diagnostics)
  và `handoff/account_07.md` §4 (giới hạn): ghi ngược file thường chưa đóng
  gói lại container; trích xuất IPA lớn còn chậm trên iSH.
- `batch` (menu [4]) vẫn là Claude 09; `support`/`kb` (ma trận hỗ trợ)
  vẫn là Claude 09.

## 7. Dịch tự động (sau đợt fix, trước Claude 08 — Joseph chốt 02:13)

- **Provider:** Google Translate endpoint miễn phí `translate.googleapis.com`
  (client=gtx, không key, cần mạng), stdlib duy nhất (`urllib`), Python 3.9.
  `sdk/translate.py` là file DUY NHẤT được dùng mạng (ngoại lệ duy nhất cho
  luật 100%-local, decisions #151; `test_rules_ish_only` có allowlist hẹp).
  Một `q` mỗi request; gap 0,4s giữa chuỗi; retry 3 lần (lùi 1s/2s/4s) khi
  429; lỗi mạng -> `E_NETWORK`, báo rõ, không crash.
- **Quy tắc dịch (Joseph 02:15):** chỉ dịch VALUE, giữ nguyên KEY + thứ tự
  key; mask placeholder (`{0}`, `%s`), thẻ (`<b>`, `<color>...`) và `\n`
  trước khi gửi, khôi phục sau; kiểm tra placeholder sau dịch (mất thì cảnh
  báo + không cache).
- **Cache:** theo hash(text + ngôn ngữ nguồn + đích) tại
  `workspace/cache/translation_cache.json` (tối đa 5000 câu); câu đã dịch
  rồi không dịch lại.
- **Từ điển offline dự phòng:** từ/cụm game thông dụng en<->vi (~40 mục);
  khi không có mạng thì dịch được gì dùng nấy, còn lại giữ nguyên + đánh dấu
  cần dịch tay.
- **Dùng:** `translate <json> [--to vi] [--from auto] [--provider
  google|offline|fake] [--out FILE] [--no-cache]`; `extract --translate
  <lang>`; menu [2] hỏi dịch sau khi trích xong. Ít nhất en<->vi; dry-run
  mặc định khi ghi ngược.
- **Tiến độ (luật #152/#153):** báo sau MỖI chuỗi đã dịch xong (không đợi
  hết lô), % thật + ETA thật, tăng dần từng 1%, không nhảy cục; cùng mẫu một
  dòng với thanh Overall (vạch + % + đã xong/tổng + ETA), vừa màn hình
  30-44 cột, màu chỉ khi TTY thật.
- **Bằng chứng sandbox (2026-11-16):** 59 test mới (translate, translate
  CLI, progress steady) — full suite `Ran 1143 tests in 45.3s OK (skipped=1,
  lỗi=0, fail=0)`; mô phỏng gộp iSH (pty + `COLUMNS=40` +
  `PB3_TEST_TIMEOUT_SCALE=5`): 1143 test, 0 fail, 0 lỗi, Timeout 0, 47.3s.
  Zip `pb3_handoff_claude07_sandbox.zip` rebuild (cùng tên).
- **Kiểm chứng trên iSH (chờ Joseph, sáng 2026-11-16):** sau khi giải nén
  zip mới nhất vào `/root/pb3` (ghi đè), chạy:
  ```
  python3 run_tests.py --tier full
  # kỳ vọng: Ran 1143 tests ... OK (skipped=5, lỗi=0, fail=0), ~20 phút
  python3 menu.py translate tests/fixtures/extract/en.json --to vi --provider offline --no-cache
  # kỳ vọng: exit 0, en.vi.json có "play": "Chơi", "hello": "Hello" (chưa dịch, giữ nguyên)
  python3 menu.py extract tests/fixtures/extract/game1.zip --translate vi --translate-provider offline
  # kỳ vọng: trích 21 entry / 6 file rồi dịch, file .vi.json xuất kèm
  ```
  Lệnh dịch bằng Google thật cần mạng trên iPhone (không có trong test):
  `python3 menu.py translate <file.json> --to vi` (mặc định provider google).
  Lưu ý: Google Translate miễn phí giới hạn tốc độ mạnh (sandbox: request
  thứ hai đã bị 429) nên dịch IPA lớn sẽ chậm do giãn cách + retry — cache
  giúp lần sau không dịch lại.
- **Giới hạn trung thực:** chất lượng dịch máy (câu game dài, tên riêng có
  thể dịch sai); endpoint miễn phí không có SLA, có thể 429/chặn IP khi
  dịch nhiều; từ điển offline chỉ ~40 cụm; dịch `extract --translate` chỉ
  dịch bản xuất, ghi ngược vào game vẫn cần `extract --apply` + `--allow-write`.
- **Bổ sung sau Claude 08 (Joseph 02:47, decisions #164–165, xem
  `handoff/account_08.md` §7):** mặc định gắn credit 'Việt Hoá Bởi Bear'
  vào 1 entry tiêu đề/version trong file đã dịch (`--no-credit` để tắt);
  tên hiển thị trong menu là BearTea.
