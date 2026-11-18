# Handoff Colab — web → Colab → web

> Tóm tắt: chọn file trên web, việc nặng (trích xuất + dịch file IPA lớn)
> chạy trên Google Colab, kết quả tự về tab Mod để sửa — **chỉ nhập mỗi
> file qua Colab**. Không cần máy tính, không cần cài gì trên điện thoại.

## Còn phải bấm tay cái gì (nói thật trước)

Colab **không tự chạy** khi mở link — không có tham số URL nào làm được
việc đó (đã tra cứu 2026-11-18). Trong tab Colab bạn vẫn phải bấm 2 lần:

1. **Cho phép truy cập Drive** — lần đầu chạy ô gắn Drive, Google hiện
   bảng xin quyền; bấm cho phép 1 lần (các lần sau trong cùng phiên
   không hỏi lại).
2. **Run All (Ctrl+F9)** — chạy hết các ô từ trên xuống dưới.

Mẹo 1 lần duy nhất: trong Colab, bấm biểu tượng bánh răng (Notebook
settings) > Initialization > 'Run at startup' > 'First section' — từ
lần sau, mở notebook là phần đầu (gắn Drive + clone repo + đọc job) tự
chạy, bạn chỉ còn bấm Run All cho phần việc nặng. (Chưa xác minh được
cài đặt này có lưu trong file `.ipynb` để ship theo repo hay chỉ lưu
theo tài khoản — nên hướng dẫn này coi nó là cài đặt 1 lần của bạn.)

## Đường A — không cần cài đặt gì (dùng được ngay)

1. Trên web, tab **Trích xuất** > mục **☁️ Chuyển file lớn sang Google
   Colab**: điền **tên game** + **link hoặc ID Drive** của file `.ipa`
   (mở file trên drive.google.com, copy link — file phải ở chế độ ai có
   link cũng xem được, hoặc chính tài khoản Google của bạn).
2. Chọn ngôn ngữ trích (mặc định Anh + Trung), ngôn ngữ dịch (mặc định
   tiếng Việt), tick 'Dịch sau khi trích' nếu muốn dịch luôn.
3. Bấm **📋 Sao chép job** (copy chuỗi job vào clipboard) hoặc **⬇️ Tải
   job.json** (tải file `meorocky-job-<id>.json` về máy).
4. Bấm **☁️ Mở Colab** — notebook mở ở tab mới.
5. Trong Colab: bấm cho phép Drive khi được hỏi; **dán chuỗi job** vào
   ô `JOB_JSON` trong ô Job (ngay sau ô clone repo) — hoặc upload file
   `meorocky-job-<id>.json` vào khung Files bên trái (nó sẽ nằm ở
   `/content/meorocky-job.json`); rồi bấm **Run All (Ctrl+F9)**.
6. Chờ Colab chạy xong (trích file 1,7 GB mất ~15 phút, dịch thêm tùy
   provider). Kết quả lưu về Drive tại
   `MeoRocky-output/<Tên game>/` (vd `en.json`, `translated.vi.json`).
7. Quay lại web, bấm **📥 Nhập kết quả…** trong mục Colab rồi chọn các
   file JSON vừa tải từ Drive — file sẽ vào mục Mod (phần B) để sửa,
   cổng 'Kiểm tra trước khi giao' sẽ lấy file đó làm bản gốc.

## Đường B — cài đặt OAuth 1 lần, gần như tự động

Đường B để web tự làm thêm 3 bước: upload file `.ipa` lên Drive, ghi
job lên Drive, và tự hỏi kết quả rồi nhập về. Cần 1 lần cài đặt:

### Tạo OAuth client ID (5 phút, làm 1 lần)

1. Mở [Google Cloud Console](https://console.cloud.google.com/) bằng
   tài khoản Google của bạn (cùng tài khoản có file IPA trên Drive).
2. Tạo project mới (tên tùy ý, vd `meorocky-colab`) — nếu đã có project
   `beartea-translate` thì dùng luôn cũng được.
3. Vào **APIs & Services > OAuth consent screen**: chọn **External**,
   điền tên app + email của bạn, bấm Save. (Không cần điền gì thêm,
   không cần xác minh — vì chỉ chính bạn dùng.)
4. Vào **APIs & Services > Credentials > Create Credentials > OAuth
   client ID**: loại **Web application**; mục **Authorized JavaScript
   origins** thêm `https://munkai0408.github.io` (không thêm gì khác).
5. Copy **Client ID** (dạng `...apps.googleusercontent.com`).
6. Bật **Google Drive API**: **APIs & Services > Library**, tìm
   'Google Drive API', bấm Enable.

### Dùng trên web

1. Trong mục Colab, mở chi tiết **'Tải file lên Drive từ web'**, dán
   Client ID vào ô rồi bấm **Lưu client ID** (chỉ lưu trong trình duyệt
   này, không gửi đi đâu ngoài googleapis.com).
2. Bấm **📁 Chọn file .ipa…** rồi bấm **⬆️ Tải file lên Drive** — web
   upload theo từng chunk 8 MB (Drive API resumable). Tắt tab giữa chừng
   cũng không mất: mở lại web, chọn lại đúng file đó, web sẽ tiếp tục
   từ chỗ đã tải (phiên upload lưu trong trình duyệt).
3. Tải xong, web tự ghi job lên Drive (`MeoRocky/jobs/<id>.json` +
   `latest.json`). Bấm **☁️ Mở Colab** — notebook tự đọc job trên Drive,
   không cần dán gì.
4. Sau khi bấm Run All, web tự hỏi Drive mỗi phút; khi Colab xong, web
   tải kết quả về và nhập thẳng vào mục Mod (phần B). Bạn cũng có thể
   bấm **🔄 Kiểm tra kết quả** để hỏi ngay.

### Quyền Drive xin cấp (để bạn biết rõ)

Web xin 2 quyền: `drive.file` (tạo/upload file — job, file IPA) và
`drive.readonly` (đọc kết quả do Colab ghi — vì file đó không phải web
tạo nên quyền `drive.file` không thấy được). **Access token chỉ giữ
trong bộ nhớ của tab, không lưu lại** — tắt tab là hết, lần sau bấm
'Kết nối Drive' lại. Client ID không phải bí mật (nó nằm công khai
trong code web của Google) — thứ cần giữ kín là token, và token không
bao giờ được lưu hay gửi đi đâu ngoài `googleapis.com`.

## Job JSON là gì

Một job là file JSON nhỏ (schema v1, code ở `pb3/sdk/colab_job.py`):

```json
{
  "version": 1,
  "job_id": "dead-cells-20261118-090000",
  "game": "Dead Cells",
  "drive_file_id": "18zGyS-P9VHY-sOCdOHOw70msSzm6_eqM",
  "game_file_name": "Dead Cells 3.5.15 VH.ipa",
  "locales": ["en", "zh"],
  "from_lang": "en",
  "to_lang": "vi",
  "translate": true,
  "worker_url": "https://beartea-translate-proxy.<tên>.workers.dev",
  "created_at": "2026-11-18T09:00:00+00:00",
  "created_by": "MeoRocky web"
}
```

Notebook đọc job theo thứ tự ưu tiên: chuỗi `JOB_JSON` dán trong ô Job
→ file `/content/meorocky-job.json` (upload tay) → `MeoRocky/jobs/
latest.json` trên Drive → `MeoRocky/jobs/<job_id>.json` → không có job
nào thì dùng biến điền tay cũ trong ô Bước 1 (hành vi cũ giữ nguyên).
File job hỏng thì notebook **báo lỗi luôn**, không lặng lẽ chạy bằng
biến cũ (tránh trích nhầm game).

Sau bước trích, notebook ghi `handoff-manifest.json` trong thư mục kết
quả (`status: 'extracted'`, kèm tên/md5/số key từng file locale); sau
bước dịch, cập nhật thành `status: 'done'` + danh sách file đã dịch.
Web (đường B) poll chính manifest này theo `job_id`.

## Lựa chọn bên cạnh: GitHub Actions

Ngay dưới mục Colab là mục **🖥️ Xử lý file lớn trên máy chủ (GitHub
Actions)** — cùng 1 việc (trích IPA lớn) nhưng chạy trên máy chủ GitHub
thay vì Colab: không cần mở tab mới, chỉ cần file IPA trên Drive ở chế
độ 'ai có link cũng xem được' + GitHub token có quyền Actions. Xem
`docs/ACTIONS_EXTRACT.md`. Hai lựa chọn đứng kề nhau để bạn chọn đường
nào tiện hơn: Colab mạnh hơn (RAM ~11 GB, dịch luôn được), Actions gọn
hơn (không cần mở tab khác, nhưng giới hạn 500 MB artifact — chỉ nhận
file JSON kết quả, không nhận file IPA).

## Sự cố thường gặp

- **Colab báo 'Chưa điền game nào'**: job chưa tới notebook — kiểm tra
  đã dán chuỗi job vào ô `JOB_JSON` (đúng ô Job, ngay sau ô clone repo)
  hoặc đã upload file job vào khung Files chưa; hoặc job trên Drive
  chưa được ghi (đường B: xem lại bước ghi job).
- **'Không tự tìm/tải được file IPA'**: link Drive sai (dán nhầm link
  thư mục — cần link của chính file `.ipa`), hoặc file chưa chia sẻ
  'ai có link cũng xem được'. Lấy lại link: mở file trên
  drive.google.com > Share > 'Anyone with the link'.
- **Drive báo hết quota tải (`downloadQuotaExceeded`)**: file bị Google
  khóa tải tạm thời do quá nhiều lượt tải — chờ vài giờ rồi thử lại,
  hoặc copy file sang tài khoản Drive khác.
- **Upload từ web bị ngắt giữa chừng**: mở lại web, chọn lại đúng file
  đó, bấm tải lại — web tiếp tục từ chunk đã tải (phiên upload lưu
  trong trình duyệt). Nếu báo 'phiên upload hết hạn', web sẽ tạo phiên
  mới và tải lại từ đầu.
- **Web báo 'Token Drive hết hạn'**: token Google chỉ sống ~1 giờ và
  không được lưu — bấm 'Kiểm tra kết quả' để kết nối lại.
- **Notebook chạy ra 0 file**: game không phải Unity (vd Don't Starve
  Together dùng engine riêng) hoặc game không có text trích được — xem
  lại quyết định 'decision' trong output ô trích xuất.
- **Kết quả không tự về web**: đường A không tự poll (chưa có OAuth) —
  dùng nút 'Nhập kết quả…' và chọn file tay. Đường B: bấm 'Kiểm tra kết
  quả' để hỏi ngay; kiểm tra manifest `handoff-manifest.json` trong
  thư mục kết quả trên Drive có `status: 'done'` chưa.
