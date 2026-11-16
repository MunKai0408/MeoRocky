# Nút "Chọn tất cả" / "Bỏ chọn tất cả" ở tab Trích xuất — kết quả kiểm chứng

Ngày: 2026-11-16. Harness: `exp/verify_extract_deselect_all.mjs` (**5/5 PASS**,
trình duyệt thật 390px, fixture `extract_select_fixture.zip`).
Code **CHƯA ĐẨY** — đẩy cùng đợt fix "Lỗi không rõ" (báo cáo chính:
`exp/extract_unknown_error_results.md` §7), coordinator đẩy sau khi kiểm chứng.

## 1. Yêu cầu (Joseph 15:48)

"Thế nút bỏ hết toàn bộ tích nữa nhé." Trước đây danh sách `#extract-file-list` chỉ
có checkbox từng entry; chế độ chọn tay mặc định tick TẤT CẢ; không có nút chọn/bỏ
chọn tất cả — chỉ có "Tải lại danh sách file" và "Trích xuất".

## 2. Thiết kế đã chọn

- **2 nút ngay trên danh sách file** (`index.html`, trên `#extract-file-list`):
  `btn-extract-select-all` ("Chọn tất cả") + `btn-extract-deselect-all`
  ("Bỏ chọn tất cả"). Thêm cả "Chọn tất cả" cho đủ cặp với nút Joseph hỏi.
- **Chỉ bật ở chế độ chọn tay (manual); ở chế độ tự động thì `disabled` cả 2.**
  Không chọn cách "bấm thì tự chuyển sang manual" — để user chủ động đổi chế độ,
  tránh đổi chế độ lén sau lưng user. Trạng thái `disabled` đồng bộ trong
  `renderExtractFileList()` (được gọi cả khi đổi radio), mặc định `disabled` trong HTML;
  nút cũng disabled khi chưa có danh sách file.
- **Bấm thì cập nhật TRỰC TIẾP `state.extractSelected` (Set)** rồi vẽ lại bằng
  `renderExtractFileList()` — đúng quy ước hiện tại (Set là nguồn thật duy nhất cho
  `source_paths`, không đọc DOM lúc chạy).
- **Bỏ chọn hết rồi bấm "Trích xuất":** đã đọc code `btn-extract-run` — `sourcePaths =
  [...state.extractSelected]` = `[]` khi Set rỗng, và bridge coi list rỗng là `None`
  (= tự động) nên nút "Bỏ chọn tất cả" sẽ vô tác dụng nếu không chặn. Nay **chặn ngay
  tại UI**: manual + Set rỗng → banner "Chưa chọn file nào để trích xuất — tick ít
  nhất 1 file trong danh sách, hoặc chuyển về chế độ Tự động." và **không gửi gì lên
  bridge** (không chạy ngầm, `extract-result` vẫn trống). Rỗng nghĩa là không chọn
  gì, không được tự động trích hết.
- Ghi chú chế độ chọn tay trong `index.html` có thêm câu về 2 nút này.

## 3. Kết quả harness (5/5 PASS)

| Ca | Nội dung | Kết quả |
|---|---|---|
| S1 | Chế độ tự động | cả 2 nút `disabled=true` |
| S2 | Chuyển sang chọn tay | cả 2 nút bật, checkbox 5/5 tick (mặc định tick tất cả) |
| S3 | Bỏ tick tay 1 file → "Bỏ chọn tất cả" → "Chọn tất cả" | 4/5 → 0/5 → 5/5 |
| S4 | Bỏ chọn hết rồi bấm "Trích xuất" | banner "Chưa chọn file nào để trích xuất — tick ít nhất 1 file trong danh sách, hoặc chuyển về chế độ Tự động.", không "Lỗi không rõ", `extract-result` vẫn trống |
| S5 | "Chọn tất cả" rồi bấm "Trích xuất" | ra `English.json`, banner ẩn |

Kết quả JSON: `exp/extract_deselect_all_results.json` (harness tự ghi).

## 4. File thay đổi (chưa đẩy)

- `site/index.html` — 2 nút trên `#extract-file-list`; ghi chú chế độ chọn tay.
- `site/app.js` — đồng bộ `disabled` trong `renderExtractFileList()`; 2 handler nút
  (cập nhật `state.extractSelected` + `renderExtractFileList()`); chặn Trích xuất khi
  manual + Set rỗng trong handler `btn-extract-run`. Phẫu thuật bằng `muse.edit`,
  không ghi đè file, không đụng tab Font hay logic font.

## 5. Còn chờ Joseph kiểm chứng trên Safari thật

- Bấm "Bỏ chọn tất cả" / "Chọn tất cả" ở chế độ chọn tay trên IPA thật (danh sách
  dài) + bấm Trích xuất khi rỗng (kỳ vọng banner "Chưa chọn file nào…").
