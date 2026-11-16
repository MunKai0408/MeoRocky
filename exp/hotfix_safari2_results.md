# Hotfix Safari đợt 2 — báo cáo phase (Joseph 11:23–11:37)

Gộp 1 lượt: download kẹt (số 1) + inspect ZIP + giải nén sai đuôi/nhãn lỗi +
tên file gốc + bảng vỡ chữ. Kiểm chứng bằng harness mới
`exp/verify_hotfix_safari2.mjs`: **17/17 PASS**. Harness cũ chạy lại giữ xanh:
module-worker 17/17, patch01 8/8, parity 14/14, 2GB 6/6, clone-fix 10/10,
phase1_ci 8/8.

## A. Download kẹt — lỗi số 1 (Joseph 11:32, 11:34, 11:36)

Triệu chứng: out.tar.xz 95,6MB và extract.zip 280KB đều tạo xong, hiện trong
bảng "Bản tải về" của Safari, nhưng không bao giờ tải xong. File CÓ nằm trong
Files > Tải về nhưng là **file dở dang, không dùng được, kẹt mãi mãi**.

Cơ chế gốc (đã rõ): daemon tải của Safari tạo entry rồi đọc blob URL **bất
đồng bộ** để ghi file. Bản cũ hủy URL sau đúng 5 giây tính từ lúc click —
daemon đọc file lớn (95MB) chưa xong thì link đã chết → file dở dang kẹt mãi.
Giả thuyết "bấm Tải về trong 5 giây thì được" đã bị Joseph **bác bỏ 11:34**
(bấm ngay vẫn kẹt) — đường blob `a.click()` trên Safari iPhone này không đáng
tin cậy dù bấm nhanh. File nhỏ 280KB cũng kẹt nên không phải do dung lượng.

Fix (`site/app.js` — `download()`, `canShareFile()`, `fireBlobDownload()`):
- Bỏ revoke 5 giây. URL sống **10 phút** (đủ cho daemon đọc xong file lớn),
  hủy URL lần tải trước khi bắt đầu lần mới (tối đa 1 blob), hủy khi trang
  đóng (`pagehide`). Timer chỉ hủy đúng URL của nó (closure so sánh
  `lastDownloadUrl === url`) — timer cũ không được giết URL mới.
- **navigator.share làm ĐƯỜNG LƯU CHÍNH trên iOS**: khi `canShare({files})`
  trả true thì KHÔNG tự bấm blob; hiện nút **"Lưu / Chia sẻ"** + gợi ý
  "File … đã sẵn sàng — bấm Lưu / Chia sẻ để lưu vào Files (cách chắc chắn
  nhất trên iPhone)", hint tự cuộn vào tầm nhìn. Bảng Chia sẻ → "Lưu vào
  Tệp" là đường native, không qua download manager nên không kẹt.
- Blob download chỉ là **dự phòng**: nút "Tải về (blob)" hiện cùng lúc;
  khi không share được (desktop) thì blob tự bấm như trước. Nút blob sau 10
  phút (URL đã hủy) thì tạo lại URL từ blob đang giữ rồi bấm.
- Mọi thao tác tạo file đều qua `download()` nên đều có nút Share: extract,
  nén, giải nén, mod, batch, KB export, font, dịch, checkpoint.
- Câu kết quả các tab đổi từ "đã tải về …" thành "file … đã sẵn sàng — bấm
  Lưu / Chia sẻ ở mục 1 để lưu về máy" (8 vị trí).
- `AbortError` (user hủy bảng chia sẻ) thì im lặng; lỗi share thật báo
  "Chia sẻ lỗi: …" có nhãn.

Kiểm chứng (fake-DOM Node vm chạy đúng `app.js` thật, đồng hồ giả):
blob không share → bấm ngay, revoke 0 ở 5s/60s/599s, 1 ở 600s; share →
không tự bấm blob, Share gọi đúng tên file; lần tải mới hủy URL cũ ngay,
timer cũ không chạm URL mới; blob sau 10 phút tạo lại URL.
**Hành vi Share thật trên Safari iPhone chỉ Joseph kiểm chứng được** (Node/
Chromium không chứng minh được) — đây là bằng chứng cuối cùng sau khi đẩy.

## B. Inspect Unity trên nguyên IPA/ZIP → E_UNSUPPORTED_FORMAT

Nguyên nhân: `bridge.py unity_inspect` thử `parse_bundle` (fail với ZIP) rồi
rơi xuống `parse_serialized` trên cả file ZIP → lỗi.

Fix (`site/pb3/bridge.py` + `site/app.js` + `site/worker.js`):
- `unity_inspect` gặp ZIP/IPA thì mở VFS, inventory, trả
  `{ok, is_container: true, kind: "zip", bundles: [{path, size}] (xếp theo
  tên, tối đa 200), bundles_total, total_entries}`.
- `unity_inspect_entry(path, entry)` mới: đọc 1 entry trong container ra
  rồi parse (không parse cả file ZIP).
- UI: có bundle thì hiện dropdown `#unity-bundle-select` + nút "Inspect
  bundle đã chọn"; không có bundle thì hướng dẫn rõ "không chứa bundle
  .unity3d hay file .assets nào — dùng tab Trích xuất, hoặc mở IPA lấy 1
  file .assets rồi chọn file đó".
- **Giữ hint `unity-ipa-hint`**: nhánh container gọi tường minh
  `maybeShowIpaZipHint()` — trước đây hint chỉ hiện ở nhánh catch, nay
  inspect ZIP không ném lỗi nữa nên phải gọi tường minh; hint chỉ hiện khi
  file đúng mẫu ZIP-chứa-IPA (do "Nén" thay vì "Đổi tên"), IPA thật thì ẩn.
- Inspect trực tiếp `.assets`/`.unity3d` không đổi.

Kiểm chứng (Pyodide bridge thật): inspect ZIP chứa bundle → liệt kê đủ 2
bundle; inspect entry bundle → bundle true, 2 file; inspect entry .assets
→ version 17 (2019.4.40f1); ZIP không bundle → `bundles=[]`; inspect trực
tiếp .assets → version 17 không đổi.

## C. Giải nén sai file + banner lỗi mất nhãn

Joseph bấm "Giải nén" khi file đang chọn là `.zip` → `decompress_archive`
trên ZIP → E_CORRUPT.

Fix (`site/app.js`):
- Kiểm tra đuôi phía JS trước khi gọi: Giải nén yêu cầu `.tar.xz`
  ("File giải nén phải là .tar.xz — hãy chọn file out.tar.xz đã nén ra"),
  Nén từ chối file `.tar.xz` ("File .tar.xz đã là file nén — hãy chọn file
  .ipa/.zip gốc…"). Cả hai đều kiểm tra "Chưa chọn file" trước.
- **Mọi banner lỗi có nhãn thao tác**: `runOp` ném lỗi khi
  `parsed.ok === false`, catch hiện `"<Tên thao tác> lỗi: <message>"`
  (vd "Giải nén lỗi: …", "Inspect Unity lỗi: …"). 18 dòng
  `showError("Lỗi: " + parsed.error)` cũ đã xóa. Kiểm tra tĩnh xác nhận
  không còn `showError("Lỗi: " + parsed.error)` nào.

Kiểm chứng: giải nén trên file ZIP → JSON `{ok: false, error:
"E_BAD_ARCHIVE: …"}` sạch, không traceback, không crash (bridge thật).

## D. Giải nén trả tên file gốc thay vì restored.bin

Fix (`site/pb3/bridge.py` `decompress_file` + `site/worker.js`):
bridge đọc `original_name` từ tar, trả thêm `"filename"` (vd
`9Kings_v1.0.5.ipa`); không lấy được thì lấy tên input bỏ đuôi `.tar.xz`.
Worker/app dùng `parsed.filename` làm tên download (fallback tên dest).
Chữ tab Nén đã nói rõ: "Giải nén file .tar.xz để khôi phục file gốc
(giữ nguyên tên gốc)".

Kiểm chứng (bridge thật): compress sample.ipa → decompress →
`filename=sample.ipa`, `byte_identical=true`.

## E. Bảng vỡ chữ (Joseph 11:37, ảnh tab Danh sách)

Nguyên nhân: `index.html` dòng 31–32, CSS `th, td { … word-break:
break-all; }` áp cho MỌI ô — cột hẹp bị bóp còn 1 ký tự rồi break-all ngắt
từng chữ xuống dòng riêng ("Loại" → "L o ạ i", "Kích thước" →
"K í c h t h ư ớ c").

Fix (`site/index.html`, CSS chung — áp cho mọi bảng: Danh sách, plugin,
KB, Batch, Mod, Chẩn đoán…):
- `th, td { white-space: nowrap; }` — cột ngắn không bao giờ vỡ chữ.
- `td:first-child { white-space: normal; word-break: break-all; }` — chỉ
  cột đầu (thường là đường dẫn/file dài) được ngắt chữ.
- `table { display: block; overflow-x: auto; }` — bảng cuộn ngang thay vì
  bị bóp khi màn hình hẹp.

Kiểm chứng (Chromium headless shell, viewport 390×844, đo bằng
`Range.getClientRects()` — đếm hộp dòng thật của chữ, vì đo chiều cao ô
thì sai: chiều cao ô = chiều cao hàng, hàng cao do cột đường dẫn ngắt
nhiều dòng): đối chứng âm CSS cũ vỡ thật (tiêu đề "Loại"/"Kích thước"
2 dòng); CSS mới tiêu đề/Loại/Kích thước đúng **1 dòng**, cột đường dẫn
ngắt 3/2/1/1 dòng hợp lý, bảng plugin cuộn ngang (scrollW 354 >
clientW 340). Ảnh: `exp/hotfix_table_390.png` (mới) và
`exp/hotfix_table_390_old.png` (cũ, để đối chiếu).

## Sửa thêm trong đợt (phát hiện khi chạy lại harness cũ)

- `index.html:135`: bỏ `https://` khỏi placeholder ví dụ URL proxy
  (`vd: beartea-translate-proxy.<tên>.workers.dev`) — chỉ là chữ ví dụ,
  không phải URL fetch; module-worker kiểm tra "không URL ngoài" nên bị
  FAIL. (Đã xếp trong checklist merge của agent mục 2; làm luôn ở đây vì
  đang đẩy index.html.)
- `exp/verify_parity.mjs`: cập nhật regex kiểm tra dịch theo tên mới mục 2
  (`translateOneWithFallbacks`, vẫn expose
  `globalThis.pb3_google_translate`) — worker.js vẫn expose đúng, chỉ đổi
  tên import; không phải regression.
- `exp/verify_clone_fix.mjs`: thêm stub `window` cho fake-DOM (app.js dòng
  221 dùng `window.addEventListener("pagehide", …)` ở top-level).
- `exp/clone_fix_worker.mjs` (stub worker của harness): `unity_inspect`
  trên `.zip` giờ trả container `{ok, is_container, bundles: []}` như
  bridge thật, thay vì lỗi cũ "không phải bundle Unity". Trước khi sửa,
  stub cũ làm `runOp` ném lỗi → reset cờ gửi-file → worker nhận file 2
  lần (clone-fix 8/10); sau khi sửa 10/10.
- Lỗi phát hiện khi viết harness (không phải lỗi sản phẩm): Part C quên
  gọi `boot()` trước test Unity → `_app` là None →
  `'NoneType' object has no attribute 'limits'` (trang thật luôn boot
  trước, module-worker cũng gọi boot trước).

## File thay đổi (đợt này)

- `site/pb3/bridge.py` — `unity_inspect` container, `unity_inspect_entry`,
  `decompress_file` trả `filename`.
- `site/worker.js` — tên download ưu tiên `parsed.filename`.
- `site/app.js` — `download()` Share-chính + revoke 10 phút + timer
  closure, `runOp` ném lỗi có nhãn, Unity picker + `maybeShowIpaZipHint`
  ở nhánh container, guard nén/giải nén, 8 câu kết quả "đã sẵn sàng".
- `site/index.html` — nút "Lưu / Chia sẻ" + "Tải về (blob)", gợi ý tải,
  ghi chú tab Nén, CSS bảng, placeholder proxy bỏ scheme.
- `site/gt.js`, `site/diagnostics.html`, `site/cloudflare/`,
  `site/README.md` — code mục 2 của agent con (proxy dịch + trang Kiểm
  tra dịch, xem `exp/item2_results.md`); gộp chung đợt đẩy này để khỏi
  chia nhỏ, KHÔNG triển khai Cloudflare (chờ tài khoản + key của Joseph).
- `exp/verify_hotfix_safari2.mjs` (+ kết quả JSON/MD, ảnh 390px,
  `unity_container.zip`), `exp/clone_fix_worker.mjs`,
  `exp/verify_parity.mjs`, `exp/verify_clone_fix.mjs`.

## Còn chờ Joseph (sau khi đẩy)

1. Thử trên Safari thật: nút **"Lưu / Chia sẻ"** lưu extract.zip và
   out.tar.xz vào Files (bằng chứng cuối cùng cho lỗi download — Node/
   Chromium không chứng minh được); Unity inspect bundle picker trên IPA
   9 Kings thật; bảng Danh sách không còn vỡ chữ.
2. Tài khoản Cloudflare + API key Google để deploy proxy dịch (hướng dẫn
   trong `site/cloudflare/README.md`).
3. Tải lại cứng Safari (hard reload) sau khi đẩy để nhận code mới.
4. 2 file dở dang trong Files > Tải về: Joseph tự xóa, không cần làm gì thêm.
