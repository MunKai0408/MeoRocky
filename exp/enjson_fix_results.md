# Fix trích xuất thiếu en.json (Phần A) — kết quả kiểm chứng

Ngày: 2026-11-16. Harness: `exp/verify_enjson.mjs` (6/6 PASS).
Code **CHƯA ĐẨY** — coordinator đẩy sau khi kiểm chứng.
Phạm vi: chỉ cây web `site/pb3/` + `site/pb3/bridge.py`; **không** sửa cây iSH
(`~/workspace/pb3_fix/pb3` chỉ đọc) — port sang iSH là nợ patch03 (xem cuối).

## 1. Nguyên nhân (đã kiểm chứng lại từng đường code)

### Bằng chứng thật (file `extract.zip` Joseph gửi, `~/workspace/user/files/extract.zip`)
- Extract chạy được: 36 file, 23.741 entry, 105 skipped, 0 error — toàn file
  config Unity/plist, **không có text game, không có en.json**.
- Bundle Addressables `Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle`
  (UnityFS) **không xuất hiện ở đâu**: không trong files, không trong skipped
  (105 mục), không trong candidates, không có dòng log nào.
- `extraction_report.json` thật: **`"log": []` (rỗng)**. Checkpoint
  (`extraction_checkpoint.json`, 36 file) cũng không chứa đường dẫn bundle.
  Report txt không có dòng `Unity:... -> N TextAsset` nào.
- Extract **có** vào được `resources.assets` (`resources.assets::BillingMode`,
  `::BuildSettings`…) và `sharedassets2/9.assets` (có trong skipped) — chỉ
  riêng bundle localization biến mất.

### Các đường code gây mất bundle (đã đọc + kiểm chứng bằng fixture)
1. **`site/pb3/sdk/extraction.py` `_build_layers` (nhánh unity)** — entry kind
   `"unity"` mà `entry.size > limits.max_entry_bytes` (64MB) thì `continue`
   **lặng lẽ** (không log, không skipped); `nested.read(...)` lỗi cũng
   `continue` lặng. Đây là đường bỏ qua đầu tiên.
2. **`_expand_unity`** — `parse_bundle`/`parse_serialized` lỗi `PB3Error`
   thì `return {}` lặng lẽ; entry bundle nào không phải SerializedFile thì bị
   bỏ qua; trả `{}` thì caller cũng không log gì. `exclusion_reason` không
   loại bundle này (đã kiểm tra).
3. **`site/pb3/sdk/unity.py` `parse_bundle`** — chỉ hỗ trợ UnityFS v6/v7/v8,
   compression 0–3 (4 = lzham → `E_UNSUPPORTED_FORMAT`), `uncomp_info_size >
   max_entry_bytes` → `E_CORRUPT`, `lim.check_entry_size` từng block
   (block >64MB → lỗi), `lim.check_total` tổng. Một block LZ4 lớn của bundle
   thật có thể vượt 64MB — **nghi phạm nhưng CHƯA chứng minh được** vì không
   có IPA thật 141MB trong sandbox.
4. **Lỗi log (đã tìm ra, đã sửa)** — `_write_reports` gọi
   `_build_report(req, result, [], 0.0)` — truyền logs **rỗng** — nên file
   `extraction_report.json`/`.txt` trên đĩa **LUÔN** có `"log": []`, dù
   `result.report` trong bộ nhớ có log thật. Đây là lý do Joseph không thấy
   bất kỳ dấu vết nào của bundle trong report thật.

### Trigger thật nào đã xảy ra với IPA thật?
**Không xác định được chắc chắn** (không có IPA thật trong sandbox để chạy).
Các ứng viên theo thứ tự khả năng, đã kiểm chứng từng cái có thể gây mất
bundle **im lặng** bằng fixture (xem A2):
- Bundle là UnityFS v9 / SerializedFile v24 / compression lzham (4) / mã hóa
  → `parse_bundle`/`parse_serialized` raise → `_expand_unity` trả `{}` lặng.
- Bundle/block >64MB → `check_entry_size` raise (block) hoặc `continue` lặng
  (entry) → biến mất.
- Bundle cắt cụt/hỏng → `E_CORRUPT` → trả `{}` lặng.
- Đợt này **bịt tất cả các đường trên**: mọi lỗi đều ghi vào `skipped` kèm mã
  + lý do + đường dẫn đầy đủ, và log pipeline được giữ lại trên đĩa. Lần chạy
  tới trên IPA thật, nếu bundle vẫn không ra en.json thì report sẽ **nói rõ
  lý do** (vd `parse bundle lỗi [E_UNSUPPORTED_FORMAT]: UnityFS version 9 chưa
  hỗ trợ`) thay vì biến mất.

### Quyết định về giới hạn 64MB (có kiểm soát, không nâng mù)
- **Giữ nguyên 64MB/entry và 64MB/block.** Lý do: (a) bundle localization
  chỉ chứa JSON text — 9 Kings có ~20 locale thì cũng chỉ vài MB, vượt 64MB
  là không tưởng; (b) giới hạn này bảo vệ RAM tab iPhone/Safari — nới ra mà
  không có bằng chứng thì yếu đi khả năng chống zip-bomb trên thiết bị yếu;
  (c) không có IPA thật nên không có bằng chứng nào cho thấy 64MB là trigger.
- Thay vào đó, vượt giới hạn nay **báo rõ**: skipped
  `file Unity 73400320 byte > giới hạn entry 67108864 byte — không mở
  bundle/.assets (cần nâng giới hạn Unity nếu bundle thật vượt ngưỡng này)`
  + log tương ứng. Nếu lần chạy thật trên IPA cho thấy đúng lý do này, bước
  tiếp theo là nâng riêng đường Unity (vd `Limits.with_overrides` chỉ cho
  `parse_bundle`) — có bằng chứng rồi mới nâng.

## 2. File thay đổi

- `site/pb3/sdk/extraction.py`
  - `_expand_unity` trả `(out, notes)`: mọi lỗi parse (kèm mã `PB3Error`),
    entry không phải SerializedFile, bundle cho 0 TextAsset đều ghi vào
    `notes` thay vì `return {}` lặng.
  - `_build_layers(..., skipped=None)`: container/Unity đọc lỗi, quá giới
    hạn, 0 TextAsset đều ghi `skipped` + log. Giữ log
    `Unity: <path> -> N TextAsset` khi mở được.
  - `_write_reports(req, result, logs)`: **sửa lỗi log rỗng** — nhận `logs`
    thật từ `_export` thay vì `[]`.
  - `ExtractionRequest`: thêm `source_paths`, `locales` (phần B).
  - `FileOutcome.source` + `_source_of()` (phần B).
- `site/pb3/sdk/exporters.py` — `report_text`: dòng file xuất thành
  `output ← source` (phần B).
- `site/pb3/bridge.py` — `extract(path, source_paths, locales)` + trả thêm
  `source`/`skipped`/`log`; thêm `read_extracted(name)` (phần B/C).

## 3. Fixture

`exp/build_extract_select_fixture.py` (dùng builder có sẵn ở cây iSH, chỉ
đọc — không sửa `build_fixtures.py`):
- `exp/fixtures/extract_select_fixture.zip` (1.645 byte): bundle UnityFS v7
  LZ4 tại đúng đường dẫn thật
  `Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle`
  chứa TextAsset `en.json`/`zh.json`/`fr.json` (JSON key-value thật, khác nhau
  theo locale); bundle v8 không nén `level1.unity3d` chứa `de.json`;
  `resources.assets` chứa TextAsset tên `en`; `settings.json` thường (locale
  không xác định); `readme.txt`.
- `exp/fixtures/extract_broken_bundle.zip` (72.349 byte): bundle localization
  **cắt cụt còn 40%** (header + blocks info còn nguyên, block data thiếu →
  `E_CORRUPT` "block 0 cắt cụt") + `resources.assets` tốt + `settings.json` +
  `huge.bundle` 70MB byte 0 (ZIP nén còn ~70KB — kiểm giới hạn 64MB mà không
  cần giải nén 70MB).

Fixture đã kiểm tra bằng chính parser web (`sdk.unity.parse_bundle` +
`parse_serialized` qua Pyodide-in-Node): bundle v7 lz4 → TextAsset
`en.json`/`fr.json`/`zh.json`; level1 v8 → `de.json`; resources.assets → `en`.

## 4. Harness + kết quả (`verify_enjson.mjs`, 6/6 PASS)

| Mục | Kết quả |
|---|---|
| `old_evidence_log_empty_bundle_missing` | PASS — extract.zip thật: `log=[]`, 36 file, 105 skipped, 23.741 entry; bundle không có trong files/skipped/candidates (khóa đúng lỗi cần sửa) |
| `fixture_parses_with_web_parser` | PASS — fixture parse được bằng parser web |
| `A1_auto_extract_enjson_present` | **PASS** — auto extract (không chọn file, locales en+zh) → `English.json` (hello=Hello) + `Chinese.json` (hello=你好); `source` của cả hai đúng bundle; log 13 dòng, có `Unity: ...localization-locales_assets_all.bundle -> 3 TextAsset`; report txt có `English.json ← ...bundle`; `extract.zip` chứa report; không có French/German.json |
| `A2a_broken_bundle_skipped_with_reason` | PASS — bundle hỏng trong skipped: `parse bundle lỗi [E_CORRUPT]: Tệp hỏng hoặc bị cắt cụt.`; `huge.bundle` trong skipped kèm lý do vượt 64MB; log bridge 10 dòng, có `Unity: ... -> 0 TextAsset (parse bundle lỗi [E_CORRUPT]...)` + `resources.assets -> 1 TextAsset` |
| `A2b_broken_bundle_report_on_disk` | PASS — không lọc locale → decision `done`; report JSON **trên đĩa** log 10 dòng (trước đây luôn `[]`); txt có `settings.json ← ...settings.json`; bundle trong skipped kèm `E_CORRUPT` |
| `A3_no_filter_back_compat` | PASS — không lọc → đủ `Chinese/English/French/German/settings.json`, nội dung đúng (tương thích ngược) |

Hồi quy Python (hệ thống, `python3 -m unittest tests.test_extraction`):
45/45 xanh trước và sau thay đổi.

## 5. Hành vi phát hiện thêm (có sẵn, không thuộc đợt này — ghi lại để khỏi nhầm)

- **Trọng tài ASK kẹt TextAsset đơn lẻ**: `resources.assets` chỉ có 1 TextAsset
  `en` thì `json_text` (0,71) và `csv_table` (0,61) cùng nằm trong khoảng
  ASK [0,55–0,80) → decision `ask` (web không có `ask_fn` nên không xuất file
  nào). Đây là lý do fixture bundle-hỏng + lọc en/zh cho decision `none`
  (không phải lỗi mới): TextAsset `en` duy nhất kẹt ASK, `settings.json` bị
  lọc locale, nên không còn file nào. Trên IPA thật, 36 file config vẫn xuất
  bình thường nên không gặp trường hợp này. Không sửa trọng tài trong đợt này
  (đặc tả Phụ lục C: không tự đoán khi mơ hồ).
- **Skipped trùng đường dẫn**: cùng 1 TextAsset có thể xuất hiện nhiều lần
  trong skipped với lý do khác nhau (mỗi plugin ứng viên bị loại một lần) —
  hành vi có sẵn của trọng tài, không phải lỗi mới.

## 6. Nợ patch03 (port sang iSH)

Code web và iSH là IDENTICAL nên iSH cũng thiếu en.json y hệt. Cần port sang
`~/workspace/pb3_fix/pb3` ở patch03 (KHÔNG tự sửa cây iSH trong đợt này):
`sdk/extraction.py` (`_expand_unity` trả notes, `_build_layers` ghi skipped,
`_write_reports` truyền logs thật, `ExtractionRequest.source_paths/locales`,
`FileOutcome.source`, `_source_of`), `sdk/exporters.py` (dòng `←`),
và kiểm chứng trên iSH bằng suite thật + IPA thật 141MB.

## 7. Còn chờ Joseph kiểm chứng trên Safari thật

- Chạy extract trên **IPA thật 141MB** (không chọn file, mặc định Cả Anh +
  Trung): phải ra `en.json` (English.json). Nếu không ra, mở
  `extraction_report.txt` trong `extract.zip` — nay sẽ ghi rõ lý do bundle
  (vd version/compression/block vượt giới hạn) thay vì im lặng.
- Nút Share/lưu `extract.zip` trên Safari thật (đã có từ trước, không thuộc
  đợt này).

## 8. Bổ sung đợt 13:52 (batch dịch + Share) — không đổi phần A

- Phần A (fix en.json) không đổi hành vi trong đợt bổ sung; chỉ có
  `bridge.py` thêm hàm `apply_credit_only` (dịch theo chunk) và trường
  `incomplete_keys` (key chưa dịch xong, để app.js đưa về hàng đợi resume)
  trong kết quả `translate_json_google` — không ảnh hưởng extract.
- Batch dịch (mục A, xem `translate_batch_results.md`): gtx KHÔNG nhận
  nhiều q/request (chỉ dịch q đầu, kiểm chứng live); batch dùng joined-text
  1 q với marker ⟦n⟧ (joined 50 tách lại 50/50 live); khử trùng lặp 37,0%
  trên fixture 1000 chuỗi; 1000 chuỗi qua proxy giả ở trình duyệt thật
  mất 3,1 giây ở lần chạy cuối (≈ 322 chuỗi/giây; các lần chạy dao động
  2,4–3,3 giây do nhiễu thời gian trình duyệt — không phải số cố định).
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
  không phải do phần A):
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
