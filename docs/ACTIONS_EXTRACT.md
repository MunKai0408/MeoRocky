# Xử lý file lớn trên máy chủ (GitHub Actions)

File IPA >140 MB hay làm sập tab Safari trên iPhone (tab chỉ dùng được
~1–1,5 GB RAM). Ngoài cách dùng Colab (xem nút "Xử lý file lớn bằng Colab"
trong hộp cảnh báo file lớn), bạn có thể trích xuất **ngay trong giao diện
web MeoRocky**, việc trích chạy ngầm trên máy chủ GitHub — xong thì tải các
file `.json` về, không cần mở Colab.

## Cách dùng (tab Trích xuất → mục "Xử lý file lớn trên máy chủ")

1. **Chia sẻ file IPA trên Drive:** mở file IPA trong Google Drive → Chia sẻ
   → "Bất kỳ ai có đường liên kết cũng xem được" (quyền Người xem). Máy chủ
   GitHub tải file bằng link công khai, không có tài khoản Google nào trên
   máy chủ — chưa chia sẻ thì bước tải sẽ lỗi.
2. **Lấy file ID:** mở file trên Drive, link có dạng
   `drive.google.com/file/d/<FILE_ID>/view` — copy đoạn `<FILE_ID>` (hoặc
   dán nguyên link vào ô nhập, web tự tách ID).
3. **Token GitHub:** tab Trích xuất có ô nhập token. Token cần quyền
   **Actions (Read and write)** trên repo `MunKai0408/MeoRocky` để bấm chạy
   workflow và đọc kết quả. Token chỉ lưu trong localStorage của trình duyệt
   này, chỉ gửi tới `api.github.com`, không gửi đi đâu khác, không commit.
   - Kiểm tra/thêm quyền: GitHub → Settings → Developer settings →
     Personal access tokens → Fine-grained tokens → chọn token của bạn →
     Repository permissions → **Actions: Read and write** → Save.
   - Token báo `401` là sai token; `403` là thiếu quyền Actions; `404` khi
     bấm chạy là workflow chưa có trên nhánh main (cần gộp PR chứa file
     `.github/workflows/extract-large.yml` trước).
4. **Bấm "Chạy trên máy chủ"**, nhập tên game + file ID/link rồi chờ. Web
   hỏi trạng thái mỗi 15 giây; bạn được đóng tab — run ID đã lưu trong
   trình duyệt nên mở lại web vẫn thấy trạng thái và link tải.
5. **Tải kết quả:** khi chạy xong, mỗi file locale (en.json, zh-CN.json,
   zh-TW.json) có 1 nút tải riêng (file tải về là `.zip` chứa đúng 1 file
   JSON đó), cộng 1 nút "Tải tất cả" và 1 file `summary.json` ghi số key
   mỗi locale, md5, thời gian tải/trích. Artifact lưu 30 ngày.

## Chạy tay từ tab Actions (dự phòng)

Nếu web không bấm chạy được (vd token thiếu quyền), chạy tay vẫn được:
GitHub → repo `MunKai0408/MeoRocky` → tab **Actions** → workflow
**"Trich xuat IPA lon tren may chu"** → **Run workflow** → điền `file_id`,
`game_name`, `locales` → **Run workflow**. Xong thì vào run đó → mục
**Artifacts** ở cuối trang → tải từng file.

## Giới hạn đã biết

- Máy chủ GitHub miễn phí (repo public, không tốn phút Actions):
  16 GB RAM, 14 GB disk — đủ cho IPA ~2 GB. Trích xuất streaming theo
  entry nên RAM đỉnh phụ thuộc entry lớn nhất, không phải tổng dung lượng.
- Giới hạn artifact 500 MB của gói miễn phí không cản: máy chủ tải IPA
  thẳng từ Drive, chỉ file JSON kết quả (vài trăm KB) mới đi qua artifact.
- File không phải game Unity (vd Don't Starve Together) trích ra 0 file
  locale — đó là kết quả đúng (như notebook Colab), workflow vẫn xanh và
  `summary.json` ghi rõ 0 file + số mục bỏ qua.
- Link công khai của Drive có giới hạn lượt tải (đã gặp thật 2026-11-17
  với Dead Cells: gdown báo `Too many users have viewed or downloaded
  this file recently`): file bị tải quá nhiều gần đây thì Google chặn
  tải công khai tối đa 24 giờ. Workflow fail ở bước tải với thông báo
  riêng, file không hề hỏng — chỉ cần chờ rồi bấm chạy lại. Cách tránh:
  đừng bấm chạy thử liên tục nhiều lần cùng một file; đường tải có xác
  thực (tài khoản Google / service account qua Drive API) là hướng theo
  dõi sau, chưa có trong bản này.
- Thời gian: tải 1,28 GB + trích đo thật trên máy chủ trong lần chạy thử
  đầu tiên (xem `summary.json` của run đó và AUTOPILOT.md); tải 1,69 GB
  từ Drive về máy test qua Drive CLI từng mất ~11,6 phút — tốc độ tải của
  runner GitHub chưa đo trước lần chạy thử, phải đọc số thật trong log.
