# Chọn file + lọc locale + báo nguồn gốc (Phần B) — kết quả kiểm chứng

Ngày: 2026-11-16. Harness: `exp/verify_extract_select.mjs` (8/8 PASS) +
trình duyệt thật trong `verify_translate_connect.mjs`
(`browser_extract_connects_translate` PASS).
Code **CHƯA ĐẨY** — coordinator đẩy sau khi kiểm chứng.

## 1. Đính chính quan trọng (Joseph 13:28)

**Chọn file chỉ là GỢI Ý, không phải bắt buộc — tìm đúng file vẫn là việc của
tool, phải tự động.** Mặc định (không chọn gì): extract **tự tìm** đúng file
— tự mở bundle localization, tự lấy en.json ra, không cần người dùng chỉ tay.
Fix phần A vẫn là yêu cầu cốt lõi, không được thay bằng "để người dùng tự
chọn". Bộ chọn file chỉ là tùy chọn thu hẹp khi người dùng MUỐN. UI thể hiện
rõ mặc định là **"Tự động (tool tự tìm file)"** (radio đã tick sẵn), chế độ
còn lại là "Chọn tay để thu hẹp".

## 2. File thay đổi

- `site/pb3/sdk/extraction.py`
  - `ExtractionRequest.source_paths` (list vpath hoặc None): pipeline chỉ xét
    các entry đó (+ entry con `w::...` của entry đã chọn, vd file trong ZIP đã
    chọn); entry không được chọn ghi skipped kèm lý do `không được chọn (chỉ
    trích xuất file đã chọn)`. Rỗng/None = tự động (xét hết như cũ).
  - `ExtractionRequest.locales` (set hoặc None): **lọc mềm nhiều locale** ở
    vòng export — chỉ trích xuất entry có locale thuộc tập lọc; locale khác
    hoặc không xác định được thì bỏ qua kèm lý do `locale X không phải en/zh`.
    **Không fail cả đợt** khi một locale vắng mặt (khác `req.locale` cứng +
    `E_LOCALE_NOT_FOUND` — giữ nguyên cho batch/CLI). Lọc áp ở 2 chỗ: trước
    trọng tài (bỏ ứng viên có locale đã rõ mà không thuộc tập lọc — để nhóm
    locale bị loại không gây ASK rồi làm hỏng cả đợt; entry chưa rõ locale vẫn
    đi tiếp) và ở vòng export (theo locale thật của từng entry, vd CSV đa
    locale). Nhánh dùng checkpoint cũng lọc tương tự.
  - `FileOutcome.source` + `_source_of(path)`: TextAsset Unity → đơn vị chứa
    nó (phần trước `::` mà kết thúc bằng đuôi Unity: `...bundle`,
    `game.zip::Data/x.assets`); file thường → chính đường dẫn đó.
  - `_build_report`: mỗi file thêm trường `"source"` (giữ nguyên `"path"`),
    thêm `"locales_requested"` + `"source_paths"`.
- `site/pb3/sdk/exporters.py` — `report_text`: dòng file xuất thành
  `output ← source` (vd `English.json ← Payload/.../localization-locales_assets_all.bundle`).
- `site/pb3/bridge.py` — `extract(path, source_paths=None, locales=None)`:
  chuẩn hóa JsProxy/JsNull (Pyodide đưa JS `null` sang `JsNull`, **không**
  phải `None` — dùng truthiness như `batch_run` vẫn làm); trả thêm `source`
  cho từng file, `skipped`, `log`. Thêm `read_extracted(name)` (phần C).
- `site/index.html` — tab Trích xuất: radio `Tự động (tool tự tìm file)` /
  `Chọn tay để thu hẹp`, select ngôn ngữ **đúng 3 lựa chọn** (Cả Anh + Trung
  mặc định / Tiếng Anh (en) / Tiếng Trung (zh) — **không có "tất cả"**),
  danh sách file `#extract-file-list` + nút tải lại; tab Dịch: dropdown file
  vừa trích xuất + banner rỗng (phần C).
- `site/app.js` — danh sách chọn file (dùng chung `list_entries` với tab Danh
  sách, chỉ hiện entry trích xuất được: kind `unity` + file text, tối đa 200
  dòng); checkbox mặc định tick tất cả ở chế độ chọn tay, vô hiệu ở chế độ tự
  động; trạng thái tick giữ trong `Set` phía JS (không query DOM lúc chạy —
  DOM giả trong harness không có `querySelector`); kết quả extract có dòng
  `từ: <nguồn>` dưới mỗi file + nút `Dịch sang tiếng Việt` + tóm tắt số mục
  bỏ qua; dropdown tab Dịch + nạp file mặc định (phần C).

## 3. Fixture

`exp/fixtures/extract_select_fixture.zip` (xem `enjson_fix_results.md` mục 3):
bundle v7 LZ4 `localization-locales_assets_all.bundle` (en/zh/fr.json),
bundle v8 `level1.unity3d` (de.json), `resources.assets` (TextAsset `en`),
`settings.json` (locale không xác định), `readme.txt`.

## 4. Harness + kết quả (`verify_extract_select.mjs`, 8/8 PASS)

| Mục | Kết quả |
|---|---|
| `B1a_locale_filter_en_only` | PASS — lọc en → chỉ `English.json`; fr/zh.json skipped `không phải en` |
| `B1b_locale_filter_zh_only` | PASS — lọc zh → chỉ `Chinese.json`; en.json skipped `không phải zh` |
| `B1c_locale_filter_both` | PASS — lọc cả hai → đủ `English+Chinese.json`, **không** có fr/de/settings; skipped: fr `locale fr không phải en/zh`, de `locale de không phải en/zh`, settings `locale không xác định không phải en/zh` |
| `B2a_select_bundle_only_en` | PASS — chọn chỉ bundle + lọc en → chỉ `English.json`, `source` đúng bundle; resources/level1/settings/readme đều skipped `không được chọn` |
| `B2b_select_resources_zh_none` | PASS — chọn resources.assets + lọc zh → không ra file nào, decision `none`; bundle skipped `không được chọn`, resources en skipped `không phải zh` |
| `B3_unselected_bundle_skipped` | PASS — chọn tay nhưng bỏ chọn bundle → bundle skipped `không được chọn (chỉ trích xuất file đã chọn)` |
| `B4_source_reporting` | PASS — bridge `files[].source` có đủ; report JSON `English.json source` đúng bundle; report txt có `English.json ← ...bundle` |
| `B5_read_extracted` | PASS — đọc `English.json` đã trích xuất (hello=Hello, 117 byte); file chưa trích xuất → lỗi rõ; `../secret.json` → lỗi tên không hợp lệ; file 5MB → lỗi quá lớn (giới hạn 4MB); file không phải JSON → lỗi |

Trình duyệt thật (390px, `verify_translate_connect.mjs`):
`browser_extract_connects_translate` PASS — chế độ mặc định Tự động (radio
đã tick), locale mặc định `both`; danh sách chọn file hiện bundle; kết quả
có dòng nguồn `từ: Payload/.../localization-locales_assets_all.bundle` và nút
`Dịch sang tiếng Việt`; dropdown Dịch mặc định `English.json`, ô dịch nạp sẵn
en.json; không tràn ngang, không lỗi console.

## 5. Lưu ý hành vi

- **Trọng tài chọn 1 nguồn cho mỗi locale**: bundle `en.json` (điểm 0,83 —
  nhờ tín hiệu tên `localization`) thắng TextAsset `en` trong
  `resources.assets` (0,71) nên `resources.assets::en` nằm trong skipped với
  lý do `điểm thấp hơn`. Đó là hành vi trọng tài có sẵn (Phụ lục C), không
  phải lỗi: mỗi locale chỉ xuất 1 file tốt nhất, nguồn còn lại vẫn được ghi
  rõ lý do.
- **TextAsset đơn lẻ kẹt ASK**: chỉ còn 1 TextAsset `en` (vd B3, sau khi bỏ
  chọn bundle) thì `json_text` 0,71 / `csv_table` 0,61 cùng nằm khoảng ASK →
  decision `none` (web không có `ask_fn`). Hành vi có sẵn, xem
  `enjson_fix_results.md` mục 5.
- **Lọc locale và entry không rõ locale**: `settings.json` (không có locale
  trong tên/nội dung) bị loại khi lọc en/zh — đúng đặc tả phần B
  ("entry locale khác hoặc không xác định được thì bỏ qua").
- **Tương thích ngược**: `locales=None` (mặc định bridge) = không lọc —
  batch/CLI/harness cũ không đổi hành vi. `req.locale` cứng + lỗi
  `E_LOCALE_NOT_FOUND` giữ nguyên (test `test_locale_filter_en_only` /
  `test_locale_not_found_error` vẫn xanh, 45/45 `tests.test_extraction`).
- **Bảng không vỡ chữ ở 390px**: dùng chung CSS bảng đã sửa (cột ngắn
  `nowrap`, chỉ cột đường dẫn `break-all`, bảng cuộn ngang) — không đổi CSS.

## 6. Nợ patch03 (port sang iSH)

Như phần A: port `source_paths`/`locales`/`source` sang cây iSH ở patch03 để
bản iSH cũng chọn file + lọc locale + báo nguồn gốc. UI chọn file/lọc locale
là web-only (iSH dùng cờ dòng lệnh tương ứng khi port).

## 7. Còn chờ Joseph kiểm chứng trên Safari thật

- Mở tab Trích xuất trên IPA thật 141MB: danh sách file hiện đủ bundle +
  `.assets` (tối đa 200 dòng), chế độ mặc định Tự động, locale mặc định Cả
  Anh + Trung; extract xong kết quả có dòng `từ: ...` dưới mỗi file.
- Chọn tay: bỏ tick bundle localization → extract xong bundle phải nằm trong
  skipped của `extraction_report.txt` với lý do `không được chọn`.

## 8. Bổ sung đợt 13:52 (batch dịch + Share) — không đổi phần B

- Phần B (chọn file + lọc locale + báo nguồn) không đổi hành vi trong đợt
  bổ sung. Batch dịch dùng chung đường `translate_json_google` (nay có thêm
  trường `incomplete_keys` trong kết quả) và nút “Dịch sang tiếng Việt” trên
  từng file vẫn gọi đúng file đã trích xuất.
- Batch dịch (mục A, xem `translate_batch_results.md`): gtx KHÔNG nhận
  nhiều q/request (chỉ dịch q đầu, kiểm chứng live); batch dùng joined-text
  1 q với marker ⟦n⟧ (joined 50 tách lại 50/50 live); khử trùng lặp 37,0%
  trên fixture 1000 chuỗi; 1000 chuỗi qua proxy giả ở trình duyệt thật
  mất 3,1 giây ở lần chạy cuối (≈ 322 chuỗi/giây; các lần chạy dao động
  2,4–3,3 giây do nhiễu thời gian trình duyệt — không phải số cố định);
  hàng đợi bền IndexedDB + resume sau reload đã xanh.
- Nút Chia sẻ (mục B): handler KHÔNG viết lại (không có await trước share
  — giả thuyết mất user activation có thể sai); lỗi Share giờ hiện cả tên +
  message theo dạng `Chia sẻ lỗi: <Tên>: <message>`; chia file >400–500MB
  thành nhiều volume CHƯA LÀM (cần Joseph xác nhận có file >400MB thật +
  test share nhiều file trên Safari thật).

## 9. Hồi quy toàn bộ — có 3 FAIL, đã sửa và chạy lại xanh (coordinator chạy lại)

- Harness mới đợt này (chạy lại lần cuối với code cuối, log
  `/tmp/regress3_<tên>.log`): `verify_enjson.mjs` 6/6,
  `verify_extract_select.mjs` 8/8, `verify_translate_connect.mjs` 12/12,
  `verify_translate_batch.mjs` 21/21.
- Hồi quy với code batch lộ 3 harness cũ FAIL (do thay đổi của đợt batch,
  không phải do phần B):
  1. `verify_hotfix_safari2` 16/17 — format lỗi Share mới (`Chia sẻ lỗi
     (Error): …`) không khớp assertion cũ (cần tiền tố `Chia sẻ lỗi:`).
     Đã sửa app.js về dạng `Chia sẻ lỗi: <Tên>: <message>`.
  2. `verify_item3_kb` crash — nâng DB KB `pb3-kb-store` lên v2 +
     `objectStoreNames.contains()` làm vỡ fake indexedDB của harness.
     Đã sửa: hoàn nguyên DB KB về v1 nguyên bản.
  3. `verify_item4_pool` 1 FAIL (`browser_end_to_end`, VersionError:
     harness mở `pb3-kb-store` ở v1 sau khi code đã tạo v2). Đã sửa: job
     dịch chuyển sang DB riêng `pb3-translate-jobs` v1, không đụng DB KB nữa.
- Chạy lại sau 3 fix (coordinator, log `/tmp/regress3_<tên>.log`):
  module-worker 17/17, patch01 8/8, parity 14/14, 2GB 6/6, clone-fix 10/10,
  phase1_ci 8/8, hotfix safari2 17/17, item2 17/17, item3_kb 27/27,
  redesign 10/10, item4_pool 28/28; Python `tests.test_extraction` 45/45
  (lần chạy trước đợt batch; 3 fix sau đó chỉ chạm app.js/kb-store.js,
  không chạm Python).
- LƯU Ý: mục 8–9 bản trước của báo cáo này viết từ lần hồi quy TRƯỚC đợt
  batch (lúc đó 11/11 xanh) nên đã lỗi thời — đã sửa lại theo kết quả chạy
  cuối ở trên.
