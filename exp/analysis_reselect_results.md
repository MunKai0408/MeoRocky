# PHẦN (C) — Nút "Chọn lại file" ở mục "File gần đây" — Kết quả kiểm chứng

Ngày: 2026-11-16. Harness riêng: `exp/verify_analysis_reselect.mjs` —
**26/26 PASS, 0 FAIL** (19 mục (C) + 7 mục hồi quy), Chromium thật 390px,
code trên máy local (chưa đẩy lúc viết bản này — xem §6).

## 1. Đã làm gì

Mục "File gần đây" (Trang chủ) trước đây: mỗi dòng chỉ có nút "Tải lại"
(khi còn trong phiên) + nút "Xóa"; hết phiên thì chỉ còn dòng chữ
"hết phiên — chọn lại file" mà KHÔNG có nút nào để chọn lại — đúng như
ảnh chụp Joseph gửi 20:48.

Nay **mỗi dòng đều có nút "Chọn lại file"** (nút "Tải lại" và "Xóa" giữ
nguyên). Hành vi thật theo từng trường hợp:

| Trường hợp | Hành vi sau khi bấm "Chọn lại file" |
|---|---|
| Còn trong phiên (dữ liệu còn trong `state.file` / `state.batchFiles`) | Mở lại **ngay, không mở hộp chọn file** — file đã có trong RAM nên không đọc lại từ đĩa. File game (`.ipa/.zip/.unity3d/.assets/.tar.xz/.gz`) thành file hiện tại rồi mở **tab Danh sách**; `.ttf` mở **tab Font**; `.json` mở **tab Mod** (đọc từ dữ liệu trong phiên, giữ thứ tự key gốc). File trong batch thì đưa lên làm file hiện tại (batch vẫn giữ nguyên); file đã là `state.file` thì chỉ điều hướng, không reset danh sách trích xuất đang có. |
| Hết phiên (chỉ còn metadata `localStorage`, không còn dữ liệu) | Mở **hộp chọn file** (`#reselect-file-input`, không có `accept` — quirk picker iOS làm mờ `.ipa` giống input chính — kiểm tra đuôi bằng JS). So **tên + dung lượng**: khớp thì mở tiếp; **khác file thì báo rõ** tên/dung lượng file vừa chọn so với file gần đây rồi **vẫn mở file vừa chọn** (không im lặng dùng sai file). Đuôi không hỗ trợ thì báo lỗi, không mở. Đóng hộp chọn mà không chọn file thì không làm gì. |

Dòng chữ trong UI đã sửa cho đúng hành vi thật (trước đây hứa "chọn lại
file" nhưng không có nút): *"Nút 'Chọn lại file' ở mỗi dòng: file còn
trong phiên thì mở lại ngay đúng tab làm việc (file game → tab Danh sách,
file .json → tab Mod); hết phiên thì mở hộp chọn file để bạn chọn lại
file đó từ máy — nếu chọn nhầm file khác, trang báo rõ rồi vẫn mở file
vừa chọn."* Dòng hết phiên ghi rõ *"hết phiên — chọn lại file từ máy"*.
Mọi chữ tiếng Việt mới đều đủ dấu.

## 2. Cách tích hợp trên web (không port nguyên si)

Không có "cách gốc" ngoài web để port — nút này chỉ có thể làm theo
kiến trúc web hiện tại, vì trình duyệt **không cho trang web đọc lại
đường dẫn file cũ** (không có File System Access API bền trên Safari):
metadata chỉ sống trong `localStorage`, dữ liệu file chỉ sống trong RAM
của phiên. Vì vậy thiết kế 2 nhánh trên là bắt buộc, không phải lựa chọn:

- **Nhánh còn trong phiên** tái dùng đúng luồng file sẵn có: tách
  `setCurrentGameFile(obj)` ra khỏi `handleChosenFile` (trước đây đọc
  `arrayBuffer()` rồi set state trong `.then` không trả promise) để
  chọn lại dùng chung code đặt file hiện tại mà không đọc lại đĩa;
  `handleChosenFile` nay `await` đọc xong rồi trả về `state.file`
  (hoặc `undefined` khi bị huỷ/quá lớn) để bên gọi biết mà điều hướng.
  Nhờ đó cảnh báo file lớn >400MB (C4), giới hạn 2GB, lazy boot
  Pyodide (mục 6) đều chạy y như chọn file mới.
- **Nhánh hết phiên** dùng input file ẩn riêng `#reselect-file-input`
  (bấm nút là cử chỉ của user nên trình duyệt cho mở picker), rồi mở
  file vừa chọn qua đúng `handleChosenFile` / `openJsonInMod` như file
  mới — không có đường tắt nào khác trên web.
- **File `.json` → tab Mod** (không phải tab Dịch): Mod là nơi mở/sửa
  file JSON (mục B: tìm theo chữ, giữ thứ tự key, provenance
  `origin: "upload"` khi không có sidecar); tab Dịch chỉ nhận JSON để
  dán vào ô dịch, không phải nơi "mở lại" file.
- **File game → tab Danh sách** (không phải tab Trích xuất): Danh sách
  là bước đầu của luồng làm việc và chỉ đọc EOCD nhanh (mục 5), không
  boot worker — bấm chọn lại là thấy ngay file đã chọn; từ đó user bấm
  sang Trích xuất như thường lệ.
- Trạng thái lần chọn lại gần nhất phơi ra `window.__PB3_RESELECT_STATE()`
  (`{pending, last: {name, inSession, from, matched, tab, keys?, error?}}`)
  theo đúng mẫu `__PB3_LARGE_FILE_STATE` (C4) để harness/chẩn đoán đọc.
- CSS: `.recent-row button { white-space: nowrap; }` để nút không vỡ
  chữ ở 390px (hàng đã `flex-wrap: wrap`).

## 3. Bảng 5 game (Chromium thật, 390px, đo bằng harness)

Cả 5 game đều PASS cả hai trường hợp. "Còn trong phiên" = mở file rồi
bấm nút ngay; "hết phiên" = tải lại trang (chỉ còn metadata
`localStorage`) rồi bấm nút → chọn lại đúng file đó.

| Game | File | Còn trong phiên → tab Danh sách | Hết phiên → chọn lại đúng file → tab Danh sách |
|---|---|---|---|
| 9 Kings | `9kings.ipa` 173MB | PASS (3,3s) | PASS — khớp tên+dung lượng (6,4s) |
| Passpartout | `passpartout.ipa` 159MB | PASS (3,1s) | PASS — khớp tên+dung lượng (6,1s) |
| Exploding Kittens 2 | `exploding_kittens2.ipa` 290MB | PASS (3,1s) | PASS — khớp tên+dung lượng (6,2s) |
| Poly Bridge 3 | `polybridge3.ipa` 482MB | PASS (3,6s) | PASS — khớp tên+dung lượng, có hộp thoại file lớn >400MB (C4), bấm Tiếp tục (4,8s) |
| data.unity3d | `data.unity3d` 590MB | PASS (9,9s) | PASS — khớp tên+dung lượng, có hộp thoại file lớn >400MB (C4), bấm Tiếp tục (5,2s) |

Không game nào FAIL. Không tràn ngang ở 390px (`scrollWidth = 390` mọi
lần đo), không `pageerror` nào.

Trường hợp khác (cùng harness, 390px):

- **Chọn nhầm file:** recent `9kings.ipa`, chọn `passpartout.ipa` →
  banner hiện *"Bạn chọn file passpartout.ipa (152 MB), khác với file gần
  đây 9kings.ipa (165 MB) — đã mở file vừa chọn thay cho file cũ."*,
  `state.file = passpartout.ipa`, tab Danh sách mở — PASS.
- **Nút "Xóa" vẫn hoạt động:** xóa dòng `passpartout.ipa`, tải lại
  trang → dòng vẫn mất — PASS.
- **File `.json`:** `resources.monobehaviour.en.json` (9 Kings, 11.356
  entry) còn trong phiên → tab Mod, *"Đã mở ... · 11356 entry · Nguồn:
  file tải lên — không rõ nguồn gốc"* — PASS; hết phiên → chọn lại đúng
  file → tab Mod, đủ 11.356 entry — PASS.
- **Tĩnh/thuần:** đủ hàm (`reselectTargetTab`, `openJsonInMod`,
  `reselectInSession`, `openPickedReselect`, `setCurrentGameFile`,
  `recentInSessionEntry`); định tuyến `.ipa/.zip/.unity3d/.assets/
  .tar.xz/.gz → list`, `.json → mod`, `.ttf → font`; dòng hết phiên có
  nút Chọn lại + ghi chú, KHÔNG có nút Tải lại; dòng còn trong phiên có
  cả Tải lại + Chọn lại — PASS.

## 4. Harness (C): 26/26 PASS

`node verify_analysis_reselect.mjs` (kết quả chi tiết:
`exp/analysis_reselect_results.json`):

- Tĩnh: `static_counts_and_cache`, `static_js_syntax`,
  `pure_reselect_fns_and_rows`, `static_home_note_honest` — 4/4.
- Còn trong phiên 5 game: `game_{9kings,passpartout,ek2,pb3,data}_reselect_insession` — 5/5.
- Hết phiên 5 game: `game_{...}_reselect_outsession` — 5/5.
- Khác: `mismatch_reports_and_opens_new`, `delete_button_still_works`,
  `json_reselect_insession`, `json_reselect_outsession`,
  `ui_no_page_errors` — 5/5.
- Hồi quy 7/7: module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33,
  (B) 24/24, item8 static 0 fails (`{"fails": []}`), full suite Python
  **1.696 test OK** (1.694 passed + 2 skipped, 0 fail).
  - Lưu ý: (A) và (B) được chạy lại **đầy đủ** (không `ONLY`, không gộp
    kết quả cũ — harness (C) đã sửa để ép `env -u ONLY` khi gọi harness
    con, vì lần chạy hồi quy đầu tiên vô tình thừa hưởng `ONLY=5`).

Ảnh 390px: `exp/out/reselect/recent_insession_390px.png`,
`recent_outsession_390px.png`, `reselect_json_mod_390px.png`.

## 5. File đổi trong đợt này

- `app.js` — nút Chọn lại file + 2 nhánh (còn/hết phiên) + tách
  `setCurrentGameFile` + `handleChosenFile` trả promise +
  `window.__PB3_RESELECT_STATE`.
- `index.html` — input ẩn `#reselect-file-input`, ghi chú "File gần
  đây" viết lại cho đúng hành vi thật, CSS nút không vỡ chữ.
- `sw.js` — `CACHE_VERSION` v16 → **v17-20261116** (đổi file nên phải
  bump theo quy ước; precache không thêm file mới).
- `exp/verify_analysis_reselect.mjs` (mới) +
  `exp/analysis_reselect_results.md` (file này) +
  `exp/analysis_reselect_results.json` (mới).
- `exp/check_item8_static.py`, `exp/check_analysis_c7_static.py`,
  `exp/check_analysis_c8_static.py`, `exp/check_analysis_c9_static.py`,
  `exp/verify_analysis_c8.mjs`, `exp/verify_analysis_c9.mjs` — cập nhật
  kỳ vọng `CACHE_VERSION` v16 → v17 (số đếm file KHÔNG đổi:
  `files.json` vẫn 92, `tests.json` vẫn 162 — phần (C) không thêm/bớt
  file Python hay test nào; đã grep toàn bộ `exp/` kiểm tra, không còn
  sót `v16-20261116`).
- Không đụng `.github/workflows/`, không đụng cây iSH.

## 6. Đẩy live

- Commit: `10e22a0544c1bfa7ddafaa4280d41aa740b00b9c` —
  "Phan (C): nut Chon lai file o File gan day (harness 26/26, full suite
  Python 1696 passed, (A) 33/33, (B) 24/24)".
- Kiểm chứng trên main (raw.githubusercontent.com, không có `site/`):
  `sw.js` có `CACHE_VERSION = "v17-20261116"`; `app.js` có
  `__PB3_RESELECT_STATE` + `reselectTargetTab` (5 chỗ nhắc "Chọn lại
  file"); `exp/analysis_reselect_results.json` ghi 26/26 PASS.
- Live: https://munkai0408.github.io/MeoRocky/ (SW v17 tự kích hoạt
  và xóa cache cũ khi Joseph mở lại trang).

## 7. Còn kẹt / chưa kiểm chứng

- **Chưa thử trên Safari thật (iPhone):** toàn bộ trên là Chromium
  trên máy chủ qua HTTP local. Trên Safari thật vẫn cần Joseph thử:
  bấm "Chọn lại file" ở cả hai trường hợp (còn trong phiên / hết
  phiên, gồm cả hộp chọn file `.ipa` — picker iOS có thể làm mờ
  `.ipa` dù input không có `accept`, như đã gặp ở input chính; đã có
  ghi chú dự phòng đổi tên thành `.zip` ở khung chính).
- FAIL duy nhất còn lại (đã biết từ trước, không thuộc phần này):
  item6 vỏ trang raw ~372KB > 100KB (gzip đạt).
