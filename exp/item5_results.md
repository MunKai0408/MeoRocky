# Mục 5 — streaming reader + copy-through + DecompressionStream + cảnh báo file lớn

Ngày làm: 2026-11-16. Head lúc làm: 4ead066 (chưa đẩy — coordinator đẩy sau khi kiểm chứng).
Harness: `exp/verify_item5.mjs` → **25/25 PASS** (Node vm + Pyodide bridge + trình duyệt thật 390px).
Kết quả JSON: `exp/item5_results.json`. Log chạy cuối: `/tmp/item5_run5.log`.

## 1. File đổi (KHÔNG đẩy lên GitHub)

| File | Đổi gì |
|---|---|
| `site/stream.js` (MỚI, ~330 dòng) | `PB3Stream`: `sizeWarning`, `listZipEntries` (EOCD + central directory), `extractZipEntry` / `extractZipEntryFromBuffer`, `extractGzipMember`, `makeOutputBlob` / `textToBlobParts` / `blobPartsByteLength` |
| `site/app.js` | Ngưỡng cảnh báo 300MB → 600/800MB; cảnh báo trước khi chạy thao tác nặng (`runOp`); `download()` nhận mảng nhiều Blob part; liệt kê nhanh EOCD ở tab Danh sách + tab Trích xuất; nút "Tải" từng entry ZIP; nút tải bản giải nén gzip; tải bản dịch + checkpoint dạng nhiều part 64KB; `.gz` chọn được (xem §5) |
| `site/index.html` | Nạp `stream.js` trước `app.js` |
| `exp/verify_item5.mjs` (MỚI) | Harness mục 5 (25 mục) |
| `exp/fixtures/item5_flat.zip`, `item5_gzip.gz`, `item5_expected.json` (MỚI) | Fixture kiểm chứng (tự sinh bằng Python trong harness) |

KHÔNG đổi: `site/worker.js`, `site/worker-pool.js`, `site/pb3/bridge.py` (không thêm op mới),
`~/workspace/pb3_fix/pb3` (chỉ đọc), zip trong `~/workspace/your_files/`, workflow `.github/`.
Không COI/COOP/COEP/SharedArrayBuffer/pthreads. Mọi chữ Việt giao diện có dấu đầy đủ.

## 2. Làm được gì (theo thứ tự yêu cầu)

### (1) Cảnh báo kích thước TRƯỚC khi mở + TRƯỚC khi chạy — XONG
- `PB3Stream.sizeWarning(size)`: ≤600MB không cảnh báo; **>600MB** cảnh báo rõ, vẫn cho tiếp tục;
  **>800MB** khuyên mạnh dùng iSH ("có thể hết RAM và bị tắt giữa chừng — nên dùng bản iSH… Vẫn có thể
  thử tiếp trên web, nhưng hãy đóng các tab khác trước"); **>2GB** từ chối như trước.
- Hiện ở: chọn file đơn (`handleChosenFile`), chọn file batch (`handleBatchFiles`, lấy cảnh báo mạnh
  nhất trong lô), và trước MỌI thao tác nặng qua `runOp` (extract/inspect/nén/giải nén…).
- Sửa 1 lỗi cũ phát hiện khi làm: trước đây cảnh báo >300MB lúc chọn file bị `clearError()` xóa ngay
  khi file nạp xong — nay cảnh báo hiện lại sau khi nạp xong (kiểm chứng ở browser D4/D5).
- Kiểm chứng trình duyệt thật bằng file sparse 610MB / 810MB / 2,1GB: cảnh báo hiện đúng chữ, nút vẫn
  tiếp tục được (610/810MB), 2,1GB từ chối TRƯỚC khi đọc file (không tốn RAM).

### (2) Copy-through — XONG (phạm vi hẹp, có đo thật)
- Entry ZIP method **stored**: `Blob.slice` (qua `extractZipEntry`) hoặc `subarray` chia sẻ buffer
  (qua `extractZipEntryFromBuffer`) — không copy qua heap JS hay Python.
- Nút **"Tải"** từng entry ở tab Danh sách dùng đường này (stored) hoặc DecompressionStream (deflate).
- Đo thật (Node): trích entry stored 3.800 byte → byte-identical với Python zipfile, heap delta JS
  −416 KB / +27 KB giữa các lần chạy (dao động do GC — Blob Node vẫn giữ dữ liệu trong bộ nhớ riêng
  của nó, con số này CHỈ là heap JS, không phải RAM tab Safari).
- **Mức RAM tiết kiệm được trên Safari thật: CHƯA ĐO** — cần Joseph đo trên iPhone (xem §6). Không ghi
  số ước tính.
- Đường gửi file sang worker (`callWorker` + `slice(0)`) VẪN COPY — bắt buộc vì hotfix clone
  (giữ buffer gốc để gửi lại, tránh DataCloneError). Copy-through chỉ áp dụng cho trích entry và
  output Blob, không áp dụng cho đường gửi worker.

### (3) DecompressionStream native — XONG (có giới hạn đã biết)
- Entry ZIP method **deflate (8)**: `DecompressionStream('deflate-raw')` trên main thread, KHÔNG dùng
  zlib Python cho đường này. Byte-identical với Python zipfile và với Node `zlib.inflateRawSync`.
- **Gzip member**: `DecompressionStream('gzip')`, byte-identical với Python gzip. File `.gz` đơn lẻ nay
  chọn được và có nút "Tải bản đã giải nén (gzip, DecompressionStream)" ở tab Danh sách.
- Fallback về bridge khi: không phải ZIP, ZIP64, có container lồng (entry `.zip`/`.ipa`/…), method khác
  0/8 (vd bzip2 → lỗi có code `E_UNSUPPORTED_METHOD`, UI gợi ý dùng tab Trích xuất), entry mã hoá,
  header cục bộ hỏng.
- **Giới hạn gzip nhiều member (phát hiện khi kiểm chứng):** `DecompressionStream('gzip')` của Node 24
  ném `ERR_TRAILING_JUNK_AFTER_STREAM_END` dưới dạng lỗi KHÔNG BẮT ĐƯỢC (uncaught — `await` không
  reject mà process chết; đã kiểm chứng bằng tiến trình con riêng). Probe Chromium cho multi-member bị
  treo nên chưa kết luận được phía Chromium — đường nhanh chỉ cam kết gzip **1 member**. File `.gz`
  nhiều member vẫn xem được qua bridge list/diagnose, chỉ nút tải nhanh không dùng được.

### (4) Ghi output streaming (nhiều Blob part) — XONG (phạm vi hẹp)
- `download(name, data)` nay nhận **mảng nhiều part**: `new Blob(parts)` không copy các part lại với
  nhau. Áp dụng cho: file bản dịch (`translated.vi.json`, part 64KB) và checkpoint batch
  (`checkpoint.json`, part 64KB).
- Output từ worker (extract.zip, batch.zip, out.tar.xz, merged.ttf…) vẫn là **một ArrayBuffer duy nhất**
  do worker trả về — chỉ được gói thành Blob (không copy thêm) rồi tải. Không concat thêm.
- Kiểm chứng: JSON 200.030 ký tự → 4 part (mỗi part ≤64KB), Blob round-trip giống hệt.

### (5) KHÔNG viết lại SDK — GIỮ NGUYÊN
- `bridge.py` không thêm op nào; `worker.js`/`worker-pool.js` không đổi; protocol worker giữ nguyên
  (send-once, pool dedup, checkpoint). `stream.js` chỉ chạy ở main thread.

## 3. Cái gì stream được thật / cái gì vẫn giữ RAM (giới hạn thật)

**Stream được thật:**
- Liệt kê ZIP: chỉ đọc EOCD + central directory ở cuối file (vài KB), không đưa cả file qua Python,
  không giải nén toàn bộ file. Đo thật: bigtest.zip 558.899 byte / 4.000 entry → liệt kê EOCD trong
  **16 ms**, heap delta Node **~1,7–2,2 MB** (file đã nằm trong RAM từ trước — chỉ đo phần liệt kê).
- Trích 1 entry ZIP: stored copy-through, deflate inflate native (chỉ vùng nén của entry đó vào RAM).
- Gzip 1 member: inflate native.
- Tải bản dịch / checkpoint: nhiều Blob part 64KB.

**Vẫn giữ RAM (không giấu):**
- File đã chọn vẫn nằm trọn trong `state.file.data` (ArrayBuffer) vì protocol send-once/clone-fix bắt
  buộc giữ bản gốc để gửi lại cho worker — streaming end-to-end là KHÔNG THỂ nếu không viết lại SDK
  (đúng kết luận khảo sát trước).
- `.tar.xz` dùng LZMA — DecompressionStream native KHÔNG thay được, vẫn đi qua bridge
  (`compress.py` đã stream nội bộ qua tarfile `r|xz`/`w|`, chỉ bước load file giữ RAM).
- Output từ worker vẫn là một ArrayBuffer duy nhất (xem §2.4).
- `handleChosenFile` vẫn `f.arrayBuffer()` toàn bộ file khi chọn (kể cả file 610/810MB trong test —
  Chromium đọc được trong ~1–6 giây ở sandbox; trên iPhone thật chưa đo).

**Liệt kê EOCD khớp bridge ở mức nào (parity Pyodide, kiểm chứng thật):**
- Khớp từng entry (path/size/kind) trên: ambig.zip (2 entry), bigtest.zip (4.000 entry),
  item5_flat.zip (7 entry: thư mục, unity, excluded…).
- Bridge KHÔNG liệt kê thư mục và ghi size 0 cho entry excluded — JS đã sửa cho khớp y hệt (lúc đầu
  JS liệt kê cả thư mục + size thật của excluded nên parity FAIL 7≠8; đã sửa rồi chạy lại xanh).
- game1.zip có `inner.zip` → JS báo `nested=true`, app fallback về bridge (bridge liệt kê đệ quy
  dạng `inner.zip::en.json`, tổng 11 entry).
- Giới hạn còn lại: entry có magic container nhưng không có đuôi container thì JS không phát hiện
  được (cần đọc head đã giải nén) — kind có thể lệch thành "file" thay vì "container"; đường trích
  xuất vẫn đi qua bridge nên không sai kết quả, chỉ có thể sai nhãn loại trong danh sách nhanh.

## 4. Harness mục 5: 25/25 PASS (`node verify_item5.mjs`)

| Nhóm | Mục | Kết quả |
|---|---|---|
| A. Unit Node vm | Ngưỡng 600/800MB/2GB; EOCD khớp ambig; nested → fallback; kind flat; stored copy-through; deflate byte-identical; method bzip2 → `E_UNSUPPORTED_METHOD`; gzip 1 member; giới hạn gzip multi-member; output nhiều part; edge case (không phải ZIP/ZIP64/cắt cụt); đo EOCD bigtest | 12/12 |
| B. Parity bridge Pyodide | ambig, bigtest 4.000 entry, flat 7 entry khớp từng entry; game1 lồng → fallback | 4/4 |
| C. Tĩnh | `node --check`, không COI/SAB/pthreads, nối `stream.js` trước `app.js`, bridge không thêm op, worker giữ protocol | 1/1 |
| D. Trình duyệt thật 390px | Boot "Sẵn sàng — 20 plugin", không lỗi console, không tràn ngang; list nhanh EOCD + tải entry deflate; tải stored + deflate; tải gzip; cảnh báo 610MB / 810MB khuyên iSH / từ chối 2,1GB; không tràn cuối phiên | 8/8 |

## 5. Hồi quy toàn bộ harness cũ — XANH HẾT (không skip test nào)

Chạy lại sau khi xong mục 5, log `/tmp/regress5_*.log` (tổng kết `/tmp/regress5_summary.log`):

| Harness | Kết quả |
|---|---|
| enjson | 6/6 |
| extract_select | 8/8 |
| translate_connect | 12/12 |
| translate_batch | 21/21 |
| hotfix_safari2 | 17/17 |
| item3_kb | 27/27 ("TẤT CẢ XANH") |
| item4_pool | 28/28 |
| module_worker | 17/17 |
| patch01 | 8/8 |
| parity | 14/14 |
| limit_2gb | 6/6 |
| clone_fix | 10/10 |
| phase1_ci | 8/8 |
| item2 | 17/17 ("TẤT CẢ XANH") |
| redesign | 10/10 |
| Python `tests.test_extraction` | 45/45 (5,5 giây) |

Lưu ý 1 sự cố hồi quy đã sửa (không giấu): lần chạy đầu `phase1_ci` FAIL 1 mục `picker_ipa`
(`js_ext_check=false`) vì tôi thêm `".gz"` vào mảng `GAME_EXTS`, trong khi harness cũ kiểm tra đúng
từng ký tự mảng đó (fix picker .ipa ca3caef). Đã sửa bằng cách giữ `GAME_EXTS` nguyên và chấp nhận
`.gz` riêng qua hằng `GZIP_EXT` (`.gz` không phải container game, chỉ là đường gzip nhanh) — chạy lại
`phase1_ci` xanh 8/8, harness mục 5 chạy lại lần cuối trên code cuối cùng vẫn 25/25. Không sửa harness cũ.

## 6. Overlap với mục C4 (đợt phân tích mới của Joseph) — KHÔNG làm trong đợt này

- C4 (file lớn: output ghi OPFS rồi export từ đó) là mục LÀM SAU (sau mục 5–9 + audit). Đợt này
  **không dùng OPFS** (cũng như mục 3 đã quyết: KB không dùng OPFS/SAH).
- Overlap đã thấy: C4 và mục 5 cùng nhắm tới "output lớn không giữ hết trong RAM". Mục 5 làm phần
  trước OPFS: output ghi dạng nhiều Blob part + copy-through entry + cảnh báo kích thước. Phần còn
  lại cho C4: ghi output streaming thẳng vào OPFS (đặc biệt extract.zip/batch.zip/out.tar.xz từ
  worker — hiện vẫn là một ArrayBuffer duy nhất), rồi export từ OPFS. Khi làm C4 cần nhớ: worker
  trả file bằng `postMessage` + transfer nên muốn ghi OPFS thật thì phải đổi protocol worker hoặc
  ghi từng chunk từ worker — đó là việc của C4, không làm ở đây.

## 7. Còn chờ Joseph kiểm chứng trên Safari thật

1. **RAM tab iPhone với file lớn** (quan trọng nhất): mức RAM tiết kiệm được của đường nhanh so với
   đường bridge trên Safari thật là CHƯA ĐO. Cần Joseph mở file IPA thật (vd 9 Kings ~141MB, hoặc
   file >600MB nếu có) rồi xem tab có bị tắt/kẹt không, so với trước. Lưu ý file vẫn được đọc trọn
   vào `state.file.data` khi chọn nên tab vẫn tốn RAM — đường nhanh chỉ giảm phần liệt kê/trích entry.
2. **Nút "Tải" entry + nút tải gzip trên Safari thật**: đã xanh trên Chromium headless shell, chưa
   bấm trên Safari iPhone (đường tải dùng chung `download()` + Share/blob như extract.zip nên nhiều
   khả năng ổn, nhưng chưa có bằng chứng thiết bị thật).
3. **Cảnh báo >600MB / >800MB trên Safari thật**: chữ đã hiện đúng trên Chromium; cần Joseph xác nhận
   cảnh báo có dễ thấy không và có muốn đổi ngưỡng không (600/800MB là ngưỡng theo yêu cầu, chưa có
   số đo RAM iPhone để hiệu chỉnh).
4. **Gzip nhiều member**: nếu Joseph có file `.gz` nhiều member, nút tải nhanh sẽ báo lỗi — đó là
   giới hạn đã biết (xem §2.3), không phải bug chưa rõ nguyên nhân.
5. **IPA thật có entry `.zip` lồng hay không**: nếu IPA 9 Kings chứa entry `.zip`/`.ipa` bên trong thì
   đường liệt kê nhanh tự fallback về bridge (đúng thiết kế, chỉ chậm hơn chứ không sai) — chưa kiểm
   chứng trên IPA thật vì sandbox không có IPA 141MB.

## 8. Việc khác gặp trong lúc làm (không thuộc mục 5, để coordinator quyết)

- Trong lúc làm, MEMORY.md cập nhật 2 việc ngoài phạm vi mục 5: (a) Joseph 14:35 yêu cầu nhập trực
  tiếp file `.ipa` không cần đổi tên thành `.zip` (đã giao coordinator khác, có harness
  `verify_ipa_direct.mjs` riêng — tôi KHÔNG chạy harness đó, KHÔNG động vào); (b) Joseph 14:40 gửi
  `IPA-LOCALIZATION-main.zip` ("IPA Localization Studio", tool do chính Joseph viết) để kiểm kê và
  tích hợp sau. Cả hai không ảnh hưởng code mục 5.
