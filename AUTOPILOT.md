# AUTOPILOT.md — Sổ trạng thái BearTea/PB3 (chế độ tự động)

> ĐẦU MỖI LƯỢT / MỖI CUỘC TRÒ CHUYỆN MỚI: ĐỌC FILE NÀY TRƯỚC, rồi mới đọc phần còn lại.
> Không làm lại việc đã xong. Mỗi kết luận trong file này gắn nhãn [ĐÃ ĐO THẬT] /
> [FIXTURE] / [HYPOTHESIS]. Cập nhật file này SAU MỖI mục backlog.

## Mốc xuất phát [ĐÃ ĐO THẬT]

- Head xuất phát: `3d0c80c66ab4fbfbb329955f9cf241e491146f06` (main, 2026-11-16T20:01:31Z).
  Cây làm việc là bản giải nén zip, KHÔNG có `.git`; bản pristine đối chiếu nằm ngoài repo.
- Mục 2 (15 phút đầu) XONG 2026-11-17 ~08:55, chỉ kiểm tra, không sửa code/push:
  smoke 59/59; full 1719 = 1586 đạt / 102 lỗi / 27 fail / 4 bỏ qua (95,4s);
  harness 34/34 exit 1 (đường dẫn hardcode `pb3_web/site` + thiếu Playwright).
- Rà soát độc lập XONG 2026-11-17 ~09:11 (chỉ đọc, bản copy sạch): số Mục 2 đúng 40/43,
  0 sai; 25 mâu thuẫn M1–M25 (23 đúng, 1 bác bỏ M22, 1 chưa rõ M20); 15 mâu thuẫn mới
  N1–N15; 3 vi phạm thật (ngưỡng gzip, 2 lỗ innerHTML, precache 13 wheel chết).
- Q1/Q2 mặc định (user không trả lời trực tiếp, đã ghi rõ để sửa nếu sai): Q1(a) sinh
  fixture bằng script trong cây; Q2(a) sửa đường dẫn harness về cây repo gốc, chỉ sửa
  đường dẫn. Q1: full 1682 đạt, 34 đỏ do 2 fixture thiếu. Q2: 37 harness sửa đường dẫn,
  `node --check` 38/38, 2 harness mẫu PASS (item2 17/17, extract_select 8/8).

## Mục đang làm

- **BUG#2** (đường merge trích xuất) — XONG 2026-11-17, xem "Đã xong"
  (PR #4, đã gộp vào main `f1a46eb7`).
- **Prefetch Pyodide báo sai "thiếu byte"** — XONG 2026-11-17, xem "Đã xong"
  (PR #5, đã gộp vào main `55a8d162`).
- **P1-1** (mở file theo nội dung, bỏ cổng đuôi) — tiếp theo. P0-4 đã BỎ QUA (chờ
  Joseph, xem "Chờ Joseph" #5): chưa có ảnh Q1/Q2/Q3 bản v4 + xác nhận bản đang
  cài; không đứng chờ, quay lại P0-4 khi có đủ.

## Sửa khẩn cache (2026-11-17) — KHÔNG phải mục backlog, không tính vào thứ tự backlog [ĐÃ ĐO THẬT]

- Nguyên nhân: P0-3 đổi nội dung 3 file precache (`pb3/sdk/unity.py`,
  `extraction.py`, `rebuild.py`) nhưng KHÔNG tăng `CACHE_VERSION` (vẫn
  `v21-20261117`). Theo luật trong `sw.js`, SW mới chỉ kích hoạt khi đổi
  version; trình duyệt đã thăm trước đó tiếp tục phục vụ code cũ cache-first.
  User xác nhận lần chạy lỗi là tool web trên điện thoại; `extract.zip` lúc
  13:18 đúng hành vi trước P0-3 (key `.p12664`, zh trộn, lẫn tiếng Nhật,
  thiếu term, 37 file rác, không có `en.json`).
- Sửa: `CACHE_VERSION` v21-20261117→**v22-20261117** (`sw.js`, precache vẫn
  120 mục, 0 mục chết); đồng bộ kỳ vọng version ở 20 file harness/check
  (8 `exp/check_*_static.py` + 12 `exp/verify_*.mjs`) + 1 dòng mô tả hiện tại
  trong `HANDOVER.md` — chỉ sửa số version, không đổi hành vi kiểm chứng; báo
  cáo lịch sử `exp/*_results.*` và dòng lịch sử P0-2/P0-3 trong file này ĐỂ
  NGUYÊN. Cây đã dọn về đúng trạng thái sau P0-3 trước khi sửa: 32 file P1-1
  sửa dở khôi phục từ snapshot, 4 file P1-1 mới xóa; ghi chú khảo sát P1-1
  trong `work_p11/` giữ nguyên cho lượt sau. Không sửa bug đường bridge (xem
  dưới — đường bridge đã dùng đúng parser P0-3).
- Tự kiểm tra: smoke 59/59; full 1.846 = 1.816 đạt / 25 lỗi / 3 fail / 2 bỏ
  qua, tập 28 đỏ trùng hệt mốc P0-3 (2 fixture thiếu), 0 đỏ mới; 9 check tĩnh
  `fails: []`; harness item6 14/16 (2 fail gzip từ trước, như đã ghi) với cache
  thật `beartea-v22-20261117` đủ 120/120 mục + `browser_cache_version_switch`
  PASS (đổi version xóa cache cũ); harness c5 13/13 PASS. `exp/*.json|*.md`
  bẩn do chạy harness đã khôi phục byte-identical.
- Kiểm chứng end-to-end đường bridge/web [ĐÃ ĐO THẬT] (CPython qua
  `bridge.extract`, đúng mặc định web: locales `["en","zh"]`,
  `merge_one_file=True`, nguồn tự động; `boot` 20 plugin): worker.js tải đúng
  danh sách `pb3/files.json` qua SW rồi `import bridge`, không có parser riêng
  trong JS. IPA 9 Kings v1.0.6: gộp en 6.694 / zh 2.176 (khớp mốc P0-3);
  langtable en/zh mỗi file 2.150 key tên term, 0 key `.pNNNN`/`.sNNNN`/`.xN`;
  zh = đúng 1 cột zh-TW (biến thể đầu theo thứ tự bảng), khớp 2.150/2.150 với
  `zh-TW.json` mẫu P0-3; ST en 100 + zh 26 key; 0 key `.p12664` ở mọi output;
  `Perk_Benevolent` có mặt, `Achievement_FrugalKing` zh = `小氣鬼` (cũ:
  `守銭奴`). File mẫu Build 68: langtable 2.086 key; zh gộp khớp 2.086/2.086
  với `zh-CN.json` mẫu của parent (zh-CN là biến thể đầu ở build này).
  Giới hạn còn tồn tại, KHÔNG đổi bởi sửa khẩn (cần quyết định riêng, không
  phải bug parser P0-3): `en.json` gộp vẫn chứa key generic `.sNNNN`/`.pNNNN`
  của MonoBehaviour không phải bảng ngôn ngữ (IPA 4.445 `.s` / 904 `.p`),
  và output vẫn kèm file locale-None của plugin khác (plist `Info.*`, `.abi`
  21.393 key, `RuntimeInitializeOnLoads`) như trước P0-3; bridge gộp biến thể
  về mã gốc nên không ra file `zh-CN.json`/`zh-TW.json` riêng (chỉ script xuất
  locales mới tách đủ biến thể).
- Sau khi bản v22 lên live, user trên Safari cần: có mạng, tải lại trang 1 lần
  (kích hoạt SW mới: `skipWaiting` + `clients.claim` trong `sw.js`), rồi tải
  lại lần nữa (hoặc đóng tab/mở lại) để tài liệu chạy code mới; nếu vẫn ra kết
  quả cũ thì xóa dữ liệu trang rồi mở lại. Cơ sở code: `sw.js` install/
  activate/fetch + `app.js` chỉ `register("sw.js")`, không có cơ chế báo bản
  mới; hành vi Safari thật [CHƯA KIỂM CHỨNG].
- Chờ file v1.0.5: xem "Chờ Joseph" #6 (giữ nguyên, không trùng lặp). Khi có
  file: chạy lại đúng input v1.0.5 qua bridge với mặc định web; tiêu chí: 0
  key `.pNNNN` trong output bảng ngôn ngữ/zh, zh thuần 1 cột biến thể,
  có `Perk_Benevolent`, không lẫn giá trị cột tiếng Nhật, `en.json` có mặt.

## Đã xong

### BUG#2 (đường merge trích xuất: loại quét phẳng khỏi file locale, tách biến thể zh, vứt rác) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (PR #4, đã gộp vào main `f1a46eb7`)

- Chuẩn do Joseph chốt: 1 file duy nhất cho mỗi locale xin (en → chỉ
  `en.json`; en+zh → `en.json` + `zh-CN.json` + `zh-TW.json`); đủ toàn bộ
  text (term langtable + StringTable gộp `Collection/Key`); 0 key generic
  (`.sNNNN`/`.pNNNN`/`textasset.sN`); rác vứt hết (không giữ
  `_non_localization.json`, không provenance/checkpoint/report/conflicts
  trong output); áp dụng chung mọi game Unity, không hardcode game/build.
- Nguyên nhân gốc: parser P0-3 đúng; `merge_language_outcomes()` gộp chỉ
  theo locale, giữ-bản-đầu, không phân biệt nguồn có cấu trúc hay quét
  phẳng — langtable đúng (2.086 term) bị gộp chung với quét phẳng cùng
  `resources.assets` thành `en.json` 6.140 key (3.955 rác). `_build_layers()`
  không gắn thẻ nguồn; `_language_source_base_columns()` gộp biến thể về
  mã gốc; pipeline dùng `_join_string_tables()` phẳng thay vì
  `join_string_tables_grouped()`; bộ lọc locale thiếu mã bg/fil/nb.
- Sửa (`pb3/sdk/extraction.py`, `discovery.py`, `rebuild.py`, `bridge.py`,
  `plugins/csv_table/plugin.py`): gắn thẻ `origin_kind` cho `FileOutcome`
  (language_source / string_table / generic_flat / text_asset / other);
  merge chỉ gộp outcome có cấu trúc, outcome generic_flat/locale:None
  passthrough (không ghi ra đĩa ở chế độ gộp); một file ảo cho mỗi biến thể
  đầy đủ (zh-CN/zh-TW riêng, mã gốc `zh` chỉ làm fallback sau biến thể);
  StringTable dùng `join_string_tables_grouped()` + điền đủ vũ trụ
  SharedTableData (kể cả tạo bucket collection chưa có cho locale đã có
  StringTable); `_export` chế độ gộp chỉ ghi file locale đã gộp (tên file
  dùng mã chuẩn `zh-CN.json`); `total_entries` chỉ đếm key đã gộp; bản kê
  `language_columns` đủ 30 cột `mLanguages` trong report nội bộ; plugin CSV
  bỏ dòng hoàn toàn trống (không sinh key `row_NNNNNN` rỗng — 2 dòng trống
  trong `PB3_LOC_DEFAULT.csv`); `_collect_mb_keys` thêm dạng
  `Collection/Key` cho ép ngược; `LOCALE_BUILTIN` thêm zh-CN/zh-TW/bg/fil/nb
  riêng; `CACHE_VERSION` v22→**v23-20261117** + đồng bộ mọi kỳ vọng version/
  đếm tests.json (166→167, thêm `pb3/tests/test_bug2_merge.py`, 22 test mới).
- Kiểm chứng [ĐÃ ĐO THẬT] (harness `exp/verify_bug2_merge.py`, PASS 4/4,
  0 bỏ qua; CPython qua `bridge.extract`, locales `["en","zh"]`,
  `merge_one_file=True`): Build 68 `en.json` == `en-build68.json` từng
  key/giá trị/thứ tự (2.086 key, 0 generic), zip chỉ 3 file locale;
  9 Kings v1.0.6 mỗi locale 2.252 key, 0 generic, en == mẫu, zh-TW == mẫu
  trừ 26 key Policies (mẫu để trống, bản gộp giữ giá trị thật từ bảng ST
  `zh` duy nhất của game — fallback mã gốc→biến thể, xem harness);
  PB3 v1.1.7 en/zh-CN 2.348 key/file, 0 generic (game không có zh-TW: chỉ
  có cột `Chinese (Simplified)` trong CSV lẫn langtable); EK2 v0.0.18 chỉ
  `en.json` 1.344 key (không có zh — đúng, không phải lỗi). Smoke 59/59;
  full 1.872 = 1.737 đạt / 102 lỗi / 29 fail / 4 bỏ qua — tập đỏ trùng hệt
  baseline main (2 fixture thiếu), 0 đỏ mới; 9 check tĩnh `fails: []`.
- Probe `sharedassets0.assets` [ĐÃ ĐO THẬT] (9 Kings v1.0.6,
  154.591.236 B; bản Build 68 là 155.232.944 B): SerializedFile, 128 object
  MonoBehaviour toàn generic (1.132 chuỗi), 0 language_source, 0
  StringTable; 81 TextAsset toàn `ChallengeData_*` (dữ liệu game JSON,
  không phải text localizable). Kết luận: file này đóng góp 0 nội dung
  localizable có cấu trúc → KHÔNG làm streaming, giữ giới hạn 64 MB +
  lý do skip trong log. Không nâng `max_entry_bytes`.
- Đánh đổi đã biết: game hoàn toàn không có nguồn ngôn ngữ có cấu trúc sẽ
  không ra file locale nào ở chế độ gộp (rác bị vứt, không còn đường quét
  phẳng làm dự phòng) — P1-5 (raw scan, gắn nhãn, chỉ làm đường dự phòng
  cuối) có thể xem lại sau. Harness Node cũ
  `exp/verify_analysis_merge_one_file.mjs` assert merged==tách 100% — nay
  sai theo thiết kế (gộp loại generic, tách giữ generic); chưa chạy được ở
  đây (cần Playwright/fixtures), để lại làm follow-up, không sửa file lịch
  sử `exp/*_results.*`.
- Sau khi bản v23 lên live, user trên Safari cần: có mạng, tải lại trang 1
  lần (kích hoạt SW mới), rồi tải lại lần nữa (hoặc đóng tab/mở lại) để
  chạy code mới; nếu vẫn ra kết quả cũ thì xóa dữ liệu trang rồi mở lại.

### Prefetch Pyodide báo sai "thiếu byte" (response gzip: Content-Length là kích thước sau nén) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (PR #5, đã gộp vào main `55a8d162`)

- Triệu chứng user báo (ảnh chụp màn hình 2026-11-17 15:55, bản v23):
  "Tải trước Pyodide chưa xong hẳn (3 file lỗi: pyodide/pyodide.mjs
  (thiếu byte), pyodide/pyodide.asm.mjs (thiếu byte),
  pyodide/pyodide.asm.wasm (thiếu byte)) — vẫn khởi động bình thường
  khi bạn chọn file." File KHÔNG hề bị cắt/thiếu thật.
- Nguyên nhân gốc [ĐÃ ĐO THẬT] (curl -I tới
  `munkai0408.github.io/MeoRocky/pyodide/<file>` với
  `Accept-Encoding: gzip`): GitHub Pages trả 5/6 file prefetch với
  `Content-Encoding: gzip`; `Content-Length` là kích thước SAU NÉN còn
  `res.body` cho ra byte SAU GIẢI NÉN — pyodide.mjs 7.089/17.758,
  pyodide.asm.mjs 261.962/1.250.259, pyodide.asm.wasm 3.582.490/9.597.892,
  pyodide-lock.json 26.858/119.077, file `.whl` lz4 55.075/55.914; chỉ
  `python_stdlib.zip` không nén (2.545.550/2.545.550). `prefetchPyodide`
  trong `app.js` so `got !== total` nên báo sai "thiếu byte" cho mọi file
  gzip phải tải qua mạng. 3 file còn lại thoát chỉ vì đã có trong cache
  (nhánh `caches.match` bỏ qua bước kiểm tra này). Lỗi hiện ra sau bản
  v23 vì đổi tên cache làm cache lạnh, tải trước phải tải qua mạng thay
  vì trúng cache.
- Sửa (`app.js`, hàm `prefetchPyodide`, nhánh fetch): đọc
  `Content-Encoding`; khi response có mã hóa thì BỎ QUA bước kiểm tra đếm
  byte theo Content-Length (stream đọc tới `done` + tính toàn vẹn của
  gzip đã đủ bảo đảm file đầy đủ; gzip bị cắt thật thì reader reject và
  rơi vào `catch`, vẫn bị ghi là file lỗi — đúng hành vi). Chỉ ghi
  "thiếu byte" khi `got < total` VÀ không có Content-Encoding. Giữ nguyên
  mọi hành vi khác (cache hit, ghi cache, đếm tiến độ).
  `CACHE_VERSION` v23→**v24-20261117** + đồng bộ mọi kỳ vọng version trong
  20 file `exp/` (12 `verify_*.mjs` + 8 `check_*_static.py`); check tĩnh
  `exp/verify_analysis_c3.mjs` (S3) thêm khóa hành vi mới
  (`prefetchGzipByteCheck`, `prefetchNoStrictByteCheck`).
- Kiểm chứng [ĐÃ ĐO THẬT] (mô phỏng Node tải thật 6 file prefetch từ
  Pages với gzip): logic CŨ báo sai 5/6 file, logic MỚI pass 6/6, 0 báo
  sai; lưới an toàn còn hoạt động (response không nén bị cắt thật
  got=400 < total=1000 vẫn báo; stream gzip cắt giữa chừng reader
  reject → rơi vào catch). Check tĩnh 9/9 `fails: []`; harness
  `exp/verify_analysis_c3.mjs` 5/5 mục tĩnh/vm PASS (mục
  `browser_end_to_end` không chạy được ở đây — thiếu playwright-core +
  Chromium, giới hạn môi trường có từ trước, không do bản sửa này).
  Smoke 59/59; full 1.872 = 1.739 đạt / 102 lỗi / 27 fail / 4 bỏ qua —
  tập đỏ là tập con của baseline BUG#2 (102 lỗi + 29 fail), 0 đỏ mới,
  2 test Apple Strings UTF-16 BOM trước fail nay pass (dao động môi
  trường; bản sửa không đụng code Python).
- Sau khi bản v24 lên live, user trên Safari cần: có mạng, tải lại trang
  1 lần (kích hoạt SW mới), rồi tải lại lần nữa (hoặc đóng tab/mở lại)
  để chạy code mới; dòng "3 file lỗi" phải biến mất ngay từ lần tải
  trước đầu tiên sau khi cache lạnh.

### Sắp thứ tự key StringTable theo asset (khớp file mẫu byte-identical) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `fix/st-order`, chưa gộp vào main — chờ PR/duyệt)

- Vấn đề: sau BUG#2, nội dung 9 Kings v1.0.6 đã khớp mẫu 100% (2.252
  key/locale) nhưng thứ tự 102 key StringTable (dạng `Collection/Key`,
  vị trí 2150–2251) còn lệch mẫu: collection xếp theo chữ cái
  (Buildings, CommonTexts, Kings, Policies, Spells, Troops) thay vì
  (Policies, Buildings, CommonTexts, Kings, Spells, Troops); trong
  Buildings/Spells, nhóm key `_desc` lệch vị trí.
- Nguyên nhân gốc [ĐÃ ĐO THẬT] (đo trên IPA 9K v1.0.6 + file mẫu):
  file mẫu xếp key trong mỗi collection theo **m_Id tăng dần của
  SharedTableData** (đúng cả 6 collection; 2 collection phân biệt
  Buildings/Spells — thứ tự asset của StringTable lẫn SharedTableData
  đều khác mẫu, chỉ sort theo m_Id mới khớp), và xếp collection theo
  **lần gặp đầu trong SharedTableData**. Code cũ giữ thứ tự asset của
  bảng StringTable từng locale, `sorted(grouped.items())` xếp
  collection theo chữ cái, và `_fill_string_table_universe` dồn key
  điền "" xuống cuối bucket (mẫu để `Spells/Cemetery_Desc` giữa khối).
- Sửa (`pb3/sdk/extraction.py`): helper `_st_key_sort_id` /
  `_sort_st_bucket` / `_invert_shared_keys`; `join_string_tables_grouped`
  sắp mỗi bucket theo m_Id (key `id_<m_Id>` sắp theo số trong tên);
  `_fill_string_table_universe` sắp lại bucket theo m_Id sau khi điền
  và xếp collection theo lần gặp đầu trong SharedTableData; vòng duyệt
  grouped trong `_build_layers` bỏ `sorted()` theo chữ cái. Quy tắc
  chung theo dữ liệu asset, không hardcode game/build. Harness
  `exp/verify_bug2_merge.py` bổ sung check thứ tự key + md5 file thô
  (dict == cũ không kiểm thứ tự). `CACHE_VERSION` v23→**v25-20261117**
  (`extraction.py` nằm trong precache) + đồng bộ mọi kỳ vọng version ở
  20 file exp. LƯU Ý: v24 đã bị PR #5 (prefetch gzip, đã gộp vào main `55a8d162`) chiếm —
  nhánh này dùng v25 để tránh hai PR cùng v24 khiến sw.js sau gộp
  giống hệt byte với bản live và trình duyệt không cập nhật code mới;
  khi gộp PR #5 và PR này đã xung đột ở sw.js + file kỳ vọng version —
  đã giải quyết trong commit merge này, giữ v25.
- Kiểm chứng [ĐÃ ĐO THẬT]: trích xuất lại IPA 9K v1.0.6 bằng code sau
  sửa, `en.json` md5 file thô **4c22c9ebcdd04cc21c7f3f4a4952e955** —
  **giống hệt file mẫu** (byte-identical, 142.098 B); zh-TW thứ tự key
  giống mẫu, chỉ khác đúng 26 key Policies fallback; zh-CN 2.252 key
  cùng tập key. Harness `exp/verify_bug2_merge.py` PASS 4/4 (kèm check
  thứ tự/md5 mới). Smoke 59/59; full suite 0 đỏ mới (xem báo cáo cuối
  nhánh). 4 test mới trong `pb3/tests/test_bug2_merge.py`
  (`StringTableOrderCase`) khóa đúng 3 quy tắc thứ tự.
- Sau khi bản v25 lên live, user trên Safari cần tải lại trang 2 lần
  (kích hoạt SW mới rồi chạy code mới) như các bản trước.

### P0-3 (trích xuất cấu trúc bảng ngôn ngữ + StringTable, lọc rác, conflicts) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `f88934c5`, PR #3, gộp vào main `fead921f`)

- Parser cấu trúc bảng ngôn ngữ của game (`pb3/sdk/unity.py`, kind mới
  `"language_source"`): đọc `mLanguages`/`mTerms`, ánh xạ theo chỉ số, giữ
  chuỗi rỗng, kiểm chứng layout rồi mới nhận (fail-closed); đường field-shape
  cho build có typetree là [HYPOTHESIS] (chỉ dict tổng hợp). Định tuyến riêng
  trong pipeline (`pb3/sdk/extraction.py`): file ảo
  `*.langtable.monobehaviour.<mã gốc>.json`, key tên term thật; biến thể gộp về
  mã gốc. Gộp StringTable theo nhóm (`join_string_tables_grouped`, hàm cũ giữ
  nguyên, đã kiểm vi sai byte-identical). Lọc rác Unity ở đường generic (theo
  mẫu chung, có cơ sở đo). `_export` ghi `conflicts.json` khi gộp có xung đột.
  `rebuild.py` cộng tên term vào `mb_terms`. 35 test mới, không file pb3 mới,
  không đổi precache (files.json 95, tests.json 166, CACHE v21).
- Xuất locales [ĐÃ ĐO THẬT] (script ngoài repo, thư mục giao
  `deliver_p03_locales/`): 9 Kings v1.0.6 IPA 2.252 key/file (I2 2.150 =
  mTerms + ST 102), thiếu en 2 / zh-CN 2.218 / zh-TW 106, xung đột 0, rác
  1.006, tự kiểm tra 11/11 PASS (4 giá trị mẫu khớp, rò rỉ cột 0/2.150,
  placeholder/thẻ 0 lệch); đối chiếu file mẫu Build 68: 2.086 key/file, thiếu
  93/38, 14/14 PASS, byte-identical với kết quả mẫu của parent; PB3 v1.1.7
  2.348 key/file, 10/10 PASS; EK2 v0.0.18 1.344 key/file, 10/10 PASS;
  DungeonWithin CHƯA ĐẠT có lý do (0 bảng, thoát 2, không ghi file, không giải
  mã). Cùng parser, không hardcode, đúng trên 4 build (số term/cột/thứ tự cột/
  đuôi khác nhau).
- Hồi quy §4 ĐỔI CÓ LÝ DO (xem bảng mốc bên dưới): key `.sNNNN` thành tên term
  thật; EK2 mất file zh là đúng (7 key cũ là tiếng Nhật gán nhầm). Lỗi va chạm
  tên file ảo (dict ghi đè mất bảng) đã gặp khi kiểm chứng, đã sửa bằng đoạn
  `.langtable` + test khóa. Đường quét phẳng chỉ còn cho object không phải
  bảng ngôn ngữ, ghi rõ lý do trong code.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full 1.846 chạy = 1.816 đạt /
  25 lỗi / 3 fail / 2 bỏ qua (103,2s), tập đỏ trùng hệt mốc trước sửa (2
  fixture thiếu đã biết), không đỏ mới; harness rebuild 13/13 PASS, harness
  cổng 13/13 PASS (Chromium thật); harness gộp §1 đạt, §2–§4 bị chặn từ trước
  (fixture vắng + đường dẫn cũ đã chết), §5: c2 13/13 + item8 đạt, 5 harness
  con FAIL do fixture/đường dẫn vắng từ trước. Chi tiết:
  `exp/p03_language_source_results.md`.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: file 9 Kings v1.0.5 chưa có (mọi số 9 Kings là
  v1.0.6 — xem "Chờ Joseph" #6); EK2 100 bundle mã hóa ngoài phạm vi; đường
  field-shape [HYPOTHESIS]; Safari/iSH [CHƯA KIỂM CHỨNG] (không đổi UI nên
  không có bước thử mới); job CI `unit` exit 1 (nguyên nhân chưa rõ, như cũ).

### P0-2 (cổng kiểm tra trước khi giao, bước 6) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `982174fc`, PR #2, gộp vào main `d93b79a3`)

- Cổng duy nhất: bridge op `delivery_gate` + module mới `pb3/sdk/gate.py`
  (stdlib-only, Python 3.9, chỉ đọc, không ghi file) trả 1 verdict
  PASS/FAIL + 5 kiểm tra (bản dịch đầy đủ, placeholder/thẻ/xuống dòng,
  diffverify theo đúng đường (E) + provenance, độ phủ font bộ 142 mục
  theo từng font, toàn vẹn hash/cấu trúc theo kế hoạch ép) + lỗi dễ hiểu.
  Không verdict thì không giao file. UI: mục cổng trong tab Mod, nút
  "Ép và ghi file" chỉ bật khi cổng ĐẠT cho đúng file + bản dịch + lựa
  chọn hiện tại (khóa theo hash đầu vào); `rebuild_apply` bắt verdict
  PASS khớp sha nguồn + sha bản dịch khi được truyền verdict.
- Quy tắc chặn: FAIL chặn giao tuyệt đối, không có đường giao khi cảnh
  báo — vì file giao sai (thiếu key, mất placeholder, thiếu glyph) hỏng
  game theo cách user khó phát hiện. Áp nhất quán ở UI + bridge.
- Phạm vi + giới hạn có ghi rõ: cổng chỉ áp flow (E); gọi bridge trực
  tiếp không verdict giữ hành vi cũ (UI là đường giao duy nhất, luôn
  truyền verdict). Kiểm pre-write bằng mô phỏng RAM; bundle độc lập
  ≥64MB kiểm file đã ép thật ở chế độ pressed. File đã ép chỉ cần khớp
  nội dung + sha script, không cần byte-identical (khác bytes thì cảnh
  báo). Font nhúng Unity không đọc được bytes → "chưa kiểm được", không
  mặc định tốt/xấu. Placeholder so theo multiset có thứ tự + số lượng
  newline (chặt hơn `placeholders_ok` cũ).
- Đếm file: `files.json` 94→95 (+`pb3/sdk/gate.py`); `tests.json` 165→166
  (+`pb3/tests/test_gate.py`, 52 test); `sw.js` CACHE_VERSION
  v20-20261117→v21-20261117, precache 119→120 (0 mục chết, tổng thật
  16.185.827 B = 15,44 MiB); kỳ vọng đếm/version ở MỌI harness cũ đã
  đồng bộ; driver D1 của harness P0-1 nay chạy cổng (9 key đủ, gồm
  `title`) giữa preview và apply.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover)
  1811 chạy = 1781 đạt / 25 lỗi / 3 fail / 2 bỏ qua (79,6s), tập 28 đỏ
  giống hệt baseline (2 fixture thiếu đã biết), không đỏ mới; harness
  mới `verify_delivery_gate.mjs` 13/13 PASS trên Chromium thật
  (Playwright scratch, C1–C5 + D1–D4 xanh); harness P0-1 chạy lại 13/13
  PASS; item6 giữ 2 fail gzip từ trước, item7 giữ 1 fail fixture từ
  trước. Hồi quy §4 khớp mốc brief (xem bên dưới).
- Game thật [ĐÃ ĐO THẬT]: cổng FAIL đúng trên cả 3 IPA (9K: 13.465 key
  chưa ép, 13.464 MB + 1 non-string; PB3: 14.556 key, 14.517 MB + 39
  not_found + 1 đơn vị CSV bị từ chối; EK2: 17.240 key MB) — (E) v1 vẫn
  phủ 0% key ngôn ngữ thật; font FAIL ở 9K/PB3 (0 font đọc được), PASS
  ở EK2 (Roboto-Bold 139/142 phủ đủ chữ đang dùng). Vòng tròn khép trên
  `resources.assets` 9K (3 key → 5 changes, PASS cả pre-write lẫn
  pressed, sha bản copy không đổi). DungeonWithin probe-only: FAIL
  (key không tìm thấy + font chưa kiểm được), không giải mã.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM + flow Safari thật [CHƯA KIỂM CHỨNG]
  (bước thử trong `exp/delivery_gate_results.md` §6); thử iSH chưa làm;
  đường ghi MonoBehaviour (G1, để mục sau); job CI `unit` exit 1 (nguyên
  nhân chưa rõ, như P0-1).

### P0-1 (E) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `41e98028`, PR #1, gộp vào main `9fb4a2ed`)

- (E) triển khai đủ: bridge op duy nhất `rebuild_apply`, dry-run mặc định + confirm gate,
  nhóm theo provenance, xung đột đúng schema merge (cap 500), plan-per-unit qua executor
  hiện có, khóa REBUILD_ALL giữ nguyên (`MATURITY="M2"`), output = đơn vị nhỏ nhất/ZIP,
  không đóng gói lại IPA, streaming G7 (none/LZ4/LZMA/Zstd→none, verify, progress đơn điệu),
  UI (E) đầy đủ + thông báo theo mã lỗi thật.
- G1 — PHẠM VI THU HẸP CÓ GHI RÕ: (E) v1 chỉ ghi TextAsset JSON dict. Đo thật: cả 3 game
  có 0 key TextAsset JSON dict; 100% key ngôn ngữ là MonoBehaviour (PB3 thêm CSV) → (E) v1
  phủ 0% key ngôn ngữ thật, mọi key MB vào unmatched `monobehaviour` có đếm rõ. Vòng tròn
  chỉ khép cho TextAsset (chân B: `resources.assets` 25.558.592 B, 3 key → 5 changes,
  dict khớp 100% + thứ tự key). Không tuyên bố đủ tiêu chí vòng tròn cho nhóm MB.
- G7 streaming: đạt trên fixture; bundle thật ≥64MB độc lập chưa có để thử.
- Sửa lỗi kèm theo (đợt sửa-lỗi + D2): vá 2 lỗ innerHTML (4/4 `f.source` qua `esc()`);
  precache 131→119 (xóa 13 wheel chết, +1 `rebuild.py`, CACHE `v20-20261117`);
  đính chính gzip 102400B; 3 chốt chặn dest==source (containers/compress/patch_pack);
  sửa lỗi thay-theo-giá-trị-trần ở plan.py + patch_pack.py; bundle mã hóa hiện refused
  `E_ENCRYPTED`; tài liệu M1–M25/N1–N15 phần lớn đã sửa (chi tiết trong HANDOVER §10);
  lỗi D2 (reselect xóa state) đã sửa, driver D3 hoàn thiện theo tiền lệ c4/reselect.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover) 1759 chạy =
  1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua (77,1s), 28 đỏ đều do 2 fixture thiếu đã biết
  (`zip/sample.ipa`, `sqlite_loc/a.sqlite`); harness mới `verify_analysis_rebuild.mjs`
  13/13 PASS trên Chromium thật (Playwright scratch, D1–D4 xanh); harness liên quan khác
  giữ nguyên trạng thái đỏ-từ-trước đã ghi (item6 2 fail gzip, item7 1 fail fixture,
  item8 1 fail import, c9 thiếu bigtest.zip). Hồi quy §4 khớp mốc brief (xem bên dưới).
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM Safari thật [CHƯA KIỂM CHỨNG] (không có Safari thật);
  vòng tròn trên data.unity3d 590MB (file không được cung cấp); đường ghi MonoBehaviour
  (G1, để mục sau); job CI `unit` exit 1 (tái hiện cục bộ xanh, nguyên nhân chưa rõ);
  M20/M23/N5-"1692" còn mở (xem HANDOVER §10).

## Bị chặn / Chờ Joseph (ghi + bỏ qua, không đứng chờ)

1. Tải asset `pb3-fixtures-v1.zip` lên Release `fixtures-v1` (id 446604484, hiện
   `assets: []`) — nguyên nhân job CI `assets` exit 1 đã xác định [ĐÃ ĐO THẬT].
   Không sửa code/workflow nào khắc phục được; tải qua giao diện web GitHub.
2. Thử Safari thật + đo RAM (bước thử trong `exp/analysis_rebuild_results.md` §6
   cho (E) và `exp/delivery_gate_results.md` §6 cho cổng);
   chạy iSH (patch02 suite + extract + font scan) khi tới P2-4.
3. File `data.unity3d` 590MB (brief §4) chưa được cung cấp — cần để đủ tiêu chí vòng tròn
   P0-1 trên file lớn; đã thay bằng bundle lớn nhất có thật + ghi rõ.
4. Cloudflare translate-proxy (cần tài khoản + Google API key, trần 500.000 ký tự/tháng),
   PAT mới — khi tới các mục cần chúng.
5. P0-4 (Space Marshals 2 mất chữ) — BỎ QUA 2026-11-17, chờ Joseph: cần ảnh chụp
   màn hình Q1/Q2/Q3 bản v4 + xác nhận bản đang cài trên máy. Khi có đủ: đối
   chiếu chẩn đoán v1–v4 (ghi trong bộ nhớ của tài khoản này: registry glyph cố
   định, donor-quad squish, 6 slot cursed đã sửa ở v3, probe v4) vào tài liệu
   BearTea + sửa tiếp theo ảnh. Không làm P0-4 khi chưa có ảnh.
6. File 9 Kings v1.0.5 để chạy lại đúng bản user nhắc tới — hiện chỉ có v1.0.6
   (173.472.485 B, md5 4f53043c…); mọi số P0-3 của 9 Kings là v1.0.6, không
   tuyên bố số v1.0.5.

## Mốc hồi quy mới nhất [ĐÃ ĐO THẬT] (CPython qua bridge.extract, en+zh, gộp 1 file)

Mốc mới sau P0-3 (2026-11-17) — số cũ trước P0-3 trong ngoặc:

| Game (build) | en / xung đột | zh / xung đột | #N | Gộp khớp tách |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 6.694 / 13 (trước: 13.465 / 17) | 2.176 / 0 (trước: 1.914 / 0) | 0 | 100% |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 11.065 / 126 (trước: 14.556 / 293) | 2.350 / 5 (trước: 2.570 / 224) | 0 | 100% |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 10.468 / 2 (trước: 17.240 / 2) | không còn file (trước: 7 / 0) | 0 | 100% |

Lý do đổi (đã đo bằng diff trực tiếp, xem `exp/p03_language_source_results.md`
§4): key `.sNNNN`/`.pNNNN` được thay bằng tên term thật từ bảng ngôn ngữ có cấu
trúc (mọi term có mặt đầy đủ: 2.150/2.150, 2.348/2.348, 1.344/1.344); bộ lọc rác
loại giá trị Unity khỏi đường generic; trùng lặp giống hệt gộp lại nên xung đột
giảm. EK2 mất file zh là ĐÚNG: 7 key zh cũ là chuỗi tiếng Nhật `日本語` gán
nhầm, EK2 không có dữ liệu tiếng Trung. Mốc cũ (trước P0-3) khớp chính xác mốc
brief §4 → mốc đo trên đúng 3 build này. Phân loại key (trước P0-3): 9K
mb_generic 15.254 + mb_table 125; PB3 mb_generic 12.426 + CSV 4.700; EK2
mb_generic 17.247; TextAsset JSON dict = 0 ở cả 3. DungeonWithin (91.389.555 B):
chỉ quét/probe, 4 bundle Addressables mã hóa AES — KHÔNG giải mã.
Passpartout/Dead Cells/data.unity3d: chưa có file.
Chạy lại sau P0-2 (2026-11-17): khớp chính xác cả 3 game, merged==tách 100%, không lệch.

Mốc mới sau BUG#2 (2026-11-17) — harness `exp/verify_bug2_merge.py` PASS 4/4,
output zip CHỈ chứa file locale (không còn rác/provenance/report/conflicts):

| Game (build) | en | zh-CN | zh-TW | Generic |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 2.252 (== mẫu) | 2.252 | 2.252 (== mẫu trừ 26 key Policies fallback ST zh) | 0 |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 2.348 | 2.348 | — (game không có zh-TW) | 0 |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 1.344 | — (không có zh) | — | 0 |
| Build 68 `resources.assets` (20.046.080 B) | 2.086 (== `en-build68.json`) | 2.086 | 2.086 | 0 |

Lưu ý mốc: số PB3 2.350 trong bảng P0-3 đã gồm 2 key rác `row_001064`/
`row_001065` từ 2 dòng trống trong `PB3_LOC_DEFAULT.csv` (nay bỏ hẳn) —
số đúng sau BUG#2 là 2.348. Số 9K zh 2.176 trước BUG#2 là langtable zh-TW
(2.150) + 26 key ST phẳng từ bảng zh; thiếu 76 key ST (điền "" từ vũ trụ
SharedTableData) và key ST chưa gộp nhóm `Collection/Key`.

## Lệnh đã chạy (mốc + kiểm tra)

```bash
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 run_tests.py --tier smoke
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 -m unittest discover -s tests -t .
cd exp && node verify_analysis_rebuild.mjs   # D1-D4 cần Playwright (scratch ngoài repo)
cd exp && node verify_delivery_gate.mjs      # P0-2, D1-D4 cần Playwright (scratch ngoài repo)
diff -rq -x __pycache__ -x logs -x reports -x workspace -x .pytest_cache <pristine> <cây>
```

## Quyết định đã chọn (1 dòng lý do mỗi quyết định)

- G1 thu hẹp: chỉ ghi TextAsset JSON dict, MB vào unmatched có đếm — vì không có nền tảng
  ghi MB an toàn + đã kiểm chứng được; viết mới từ đầu rủi ro hỏng file game.
- Precache xóa 13 wheel chết thay vì bổ sung wheel — vì đĩa chỉ có 1 wheel, danh sách cũ
  theo cây staging cũ, không theo head.
- D3 hoàn thiện driver (đuôi `.unity3d` + bấm modal) thay vì sửa app — vì cổng đuôi/modal
  là hành vi đúng đã có tiền lệ ở harness c4/reselect; assertion D3 không đổi.
- Đẩy P0-1 lên nhánh `auto/p0-1` thay vì main — vì là thay đổi rủi ro cao, cần CI/harness
  trên nhánh trước khi gộp; gộp chỉ khi test xanh.
- Tài liệu theo code, không sửa test cho vừa tài liệu — trừ đồng bộ kỳ vọng đếm/version
  đã lệch từ trước theo giá trị thật của cây (ghi rõ từng chỗ).
- Cổng FAIL chặn giao tuyệt đối, không đường giao khi cảnh báo — vì file giao sai hỏng
  game theo cách user khó phát hiện; cảnh báo không bao giờ chặn nhưng luôn hiện.
- File đã ép chỉ cần khớp nội dung + sha script, không cần byte-identical với mô phỏng
  — vì file ép bằng công cụ khác vẫn hợp lệ; khác bytes thì cảnh báo rõ.
- Font nhúng Unity không đọc được bytes thì "chưa kiểm được", không mặc định tốt/xấu
  — vì reader hiện tại không trích được bytes font; kết luận chắc chắn cần font chỉ định.
- Parser bảng ngôn ngữ fail-closed, không fallback âm thầm về quét phẳng — vì
  đoán sai layout gán nhầm ngôn ngữ cho hàng nghìn term mà không ai phát hiện.
- Pipeline gộp biến thể về mã gốc, xuất locales giữ đủ biến thể — vì bảng locale
  hiện có gộp biến thể và mốc §4 cần so được; độ trung thực biến thể nằm ở
  artifact locales.
- Đường quét phẳng chỉ còn cho object không phải bảng ngôn ngữ, ghi rõ lý do —
  vì xóa hẳn mất hàng nghìn chuỗi thật không có nguồn thay thế + phá test hiện có.
- File ảo bảng ngôn ngữ mang đoạn `.langtable` — vì tên cũ đụng file generic
  cùng đơn vị, dict ảo ghi đè làm mất bảng (lỗi thật, đã có test khóa).
- QUY TẮC BẮT BUỘC: MỌI lần đổi nội dung file precache đều phải tăng
  CACHE_VERSION + đồng bộ mọi kỳ vọng version, và tự kiểm tra mỗi mục phải
  grep xác nhận không còn pin version cũ — vì P0-3 quên bump khiến trình duyệt
  của user tiếp tục chạy code cũ dù main đã có code mới (sửa khẩn 2026-11-17).

## Đẩy code

- P0-1 + đợt sửa-lỗi: nhánh `auto/p0-1`, commit snapshot `41e98028d3c5daf8cbb49152c9d6405a93ef3872`
  (70 file text, từ head `3d0c80c`, sync-base đã kiểm khớp), PR #1, ĐÃ GỘP vào main
  (`9fb4a2edf004b10d97d2fc95d3fc08df6fec9a13`) 2026-11-17. CI trên main: job `assets` vẫn
  đỏ-từ-trước do Release `fixtures-v1` rỗng (xem "Chờ Joseph" #1); job `unit` tái hiện
  cục bộ xanh, nguyên nhân đỏ trên CI [CHƯA KIỂM CHỨNG].
- Gói nhị phân chưa lên repo: `handoff_p01.zip` (18 fixture/font) — Joseph đẩy theo
  `HUONG_DAN.txt` (iSH hoặc web). Nhánh `auto/p0-1` giữ nguyên, không xóa.
- P0-2: nhánh `auto/p0-2`, commit snapshot `982174fc9c7b8bc0ef2f7bfc86a4a52e44a74c74`
  (39 file text, từ head `37a1133f`, sync-base đã kiểm khớp), PR #2, ĐÃ GỘP vào main
  (`d93b79a3272d98b1580d88a7d5ff7dc83c45f17a`) 2026-11-17. Nhánh `auto/p0-2` giữ nguyên,
  không xóa. Không có gói nhị phân cho P0-2.
- P0-3: nhánh `auto/p0-3`, commit snapshot `f88934c542fecb050b21172e93b0ca66caee2912`
  (11 file text, từ head `9c66fc53`, sync-base đã kiểm khớp), PR #3, ĐÃ GỘP vào main
  (`fead921f06a63d07ece4ad1f567906e9c9337dbb`) 2026-11-17. Nhánh `auto/p0-3` giữ nguyên,
  không xóa. Dữ liệu trích từ game (deliver_p03_locales/) KHÔNG commit vào repo.
- Sửa khẩn cache: đẩy thẳng main (sửa nhỏ, khẩn, đã kiểm xanh), commit
  `88e077ef46bb60eaa9a5afdff590343ffaef35bb` (23 file, từ head `c642b4ad`)
  2026-11-17. Không qua nhánh/PR vì cần lên live ngay để SW mới kích hoạt.

## Backlog còn lại (thứ tự)

P0-4 (BỎ QUA, chờ Joseph — xem "Chờ Joseph" #5; quay lại khi có đủ ảnh) → P1-1 → P1-2 → P1-3 (I1) → P1-4 (I2) →
P1-5 (I3) → P2-1 → P2-2 → P2-3 → P2-4 → P3. Tiêu chí XONG từng mục theo brief mục 7.
