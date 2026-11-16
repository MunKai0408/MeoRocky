# Mục 4 — Pool 2 worker + checkpoint từng game + watchdog hợp tác

Ngày: 2026-11-16. Harness: `exp/verify_item4_pool.mjs` (kết quả chi tiết dạng máy:
`exp/item4_results.md` + `exp/item4_results.json` — file này là bản prose).

## 1. Vấn đề

Trước mục 4, batch chạy tuần tự trên 1 worker: N game × (boot + chạy) nối đuôi
nhau, game nào lỗi thì checkpoint chỉ lưu ở cuối đợt — dừng giữa chừng là mất
hết tiến độ, và không có cơ chế nào ngăn một game treo làm kẹt cả lô (iOS có
thể kill tab nền bất cứ lúc nào). Pyodide không có pthreads/SharedArrayBuffer
nên không thể chia luồng trong 1 worker; song song chỉ làm được bằng pool
nhiều worker, mỗi worker 1 instance Pyodide + PB3 riêng.

## 2. Kiến trúc

```
app.js  runBatchFlow()
  ├─ batch_split (worker đơn) → items + khóa checkpoint (sha256)
  ├─ executor: pool 2 worker (mặc định, checkbox #batch-use-pool) hoặc
  │   worker đơn (fallback khi pool boot hỏng / user bỏ tick)
  ├─ từng game → batch_run 1 game trên 1 slot (giao khi slot rảnh)
  │   └─ recordGameDone → gộp checkpoint → saveMergedCkptSoon (IndexedDB)
  ├─ watchdog hợp tác: worker.js batch_begin đặt hạn; trước mỗi game mới,
  │   worker tự từ chối bằng E_WATCHDOG_STOP / E_BATCH_STOPPED
  └─ finalizeBatchZip: batch_zip (worker 1) + batch_merge_zip (worker 0)
      + batch_finalize → 1 file batch.zip duy nhất
```

- `site/worker-pool.js` (**mới**, classic script): `WorkerPool` — `run` (hàng
  đợi chung), `runOn` (ghim slot), `cancelQueued` (chỉ hủy job CHỜ, job đang
  chạy được chạy hết — đó mới là "dừng an toàn"), `beginBatch`/`requestStop`
  broadcast, `ready()` chờ mọi slot settle, respawn slot lỗi (tối đa
  `MAX_BOOT_FAILURES = 2` lần boot hỏng thì slot chết hẳn).
- `site/pb3/bridge.py`: `batch_split` (khóa `key = sha256(path, locale, mode,
  old, new)` như `sdk/batch.py`), `batch_run` thêm tham số `out_dir_name`
  (dồn output về 1 thư mục), `record_kb`, `make_zip`, `index_base` (2 worker
  chạy manifest 1 game thì `_batch_index` không được đụng nhau),
  `batch_checkpoint`, `batch_zip`, `batch_merge_zip`, `batch_finalize`.
  `batch_run` cũ (4 tham số) vẫn chạy được — `verify_parity` dùng đường này.
- `site/worker.js`: `WATCHDOG_SECONDS = 25` (production); op `batch_begin`
  (đặt hạn) / `batch_stop`; trước mỗi `batch_run` kiểm tra hạn rồi mới chạy.
- `site/app.js`: executor pool/đơn cùng giao diện (`run`/`runOn`/`beginBatch`/
  `requestStop`/`cancelQueued`/`status`/`destroy`); `runBatchFlow` giao từng
  game, checkpoint gộp → IndexedDB sau mỗi game; UI tab Batch: trạng thái
  pool, thông tin checkpoint, nút "Tiếp tục"/"Dừng an toàn".
- `site/index.html`: script `worker-pool.js`, ghi chú pool, checkbox
  `#batch-use-pool` (mặc định bật), `#batch-pool-info`,
  `#btn-batch-continue`, `#btn-batch-stop-safe`.

## 3. Checkpoint + resume (ngữ nghĩa)

- Checkpoint là JSON gộp `{games: {key: {status, ...}}}` lưu vào IndexedDB
  (tái dùng lớp `KBStore` của mục 3, khóa riêng `batch-checkpoint.json`) +
  localStorage dự phòng; lưu sau mỗi game xong (chuỗi `ckptSaveChain` để
  không chồng chéo).
- Resume (`resume=true`, nút "Tiếp tục" hoặc tick "Tiếp tục từ checkpoint"):
  game đã `ok` trong checkpoint được **bỏ qua và tính là `skipped`** trong
  đợt chạy tiếp (giống `BatchRunner` iSH) — kết quả đợt tiếp tục hiển thị
  dạng "0 ok / 4 lỗi / 2 bỏ qua / 8 game" (2 game ok cũ tính skipped, không
  tính lại ok). Đây là ngữ nghĩa đúng, không phải lỗi.
- Dừng an toàn (`requestStop` → `batch_stop`): worker đang chạy game hiện
  tại chạy hết, game chờ bị hủy bằng `E_BATCH_STOPPED`; checkpoint giữ đúng
  trạng thái tới lúc dừng; nút "Tiếp tục" hiện để chạy phần còn lại.
- Batch ở chế độ pool **không ghi kết quả vào KB** (KB của 2 worker là 2 file
  SQLite riêng, không gộp được) — ghi rõ trong kết quả batch; thêm game vào
  KB bằng tab Kho KB như trước.

## 4. Watchdog hợp tác (không terminate trần)

- Ngưỡng production: **25 giây** cho cả đợt batch (không phải từng game).
  Test hook duy nhất cho harness: `window.__PB3_WATCHDOG_SECONDS` (đặt
  0,5 giây trong harness để thử cơ chế dừng).
- Cơ chế: `batch_begin` đặt `batchDeadlineMs`; trước mỗi game mới, worker
  kiểm tra đồng hồ — quá hạn thì từ chối game đó bằng `E_WATCHDOG_STOP`,
  lưu checkpoint rồi dừng; app hiện "watchdog đã dừng an toàn — bấm Tiếp
  tục để chạy tiếp phần còn lại".
- KHÔNG dùng `Promise.race` + `terminate` giữa chừng: terminate sẽ mất FS
  worker + checkpoint chưa lưu của game đang chạy. Pool chỉ terminate khi
  worker BỊ LỖI (onerror) hoặc boot thất bại, rồi tạo lại slot mới.
- Căn cứ chọn ngưỡng test 0,5s: fixture `bigtest.zip` (4000 file text)
  extract mất ~2,2 giây rồi `E_NO_CANDIDATE` — đủ chậm để watchdog 0,5s
  bắt được game thứ 3 đang chạy.

## 5. Dedup file theo slot (giữ đảm bảo "gửi 1 lần" của hotfix clone)

Luồng batch mới gửi file theo từng game qua pool, bypass logic send-once
của `callWorker` — ban đầu mỗi game gửi lại file (bản copy mới), phá vỡ
đảm bảo hotfix clone và tốn băng thông (IPA 135MB × N game). Đã sửa:

- `worker-pool.js`: mỗi slot giữ `sentFiles` (khóa `tên|cỡ|lastModified`);
  `_pump` lọc file đã gửi khỏi message + transfer list trước khi post;
  đánh dấu trước khi post, bỏ đánh dấu nếu post thất bại; slot respawn
  (worker mới, FS trống) thì `sentFiles` reset theo (slot object mới).
- `app.js`: executor đơn cũng dedup tương tự (`_sentKeys` trong
  `makeSingleExecutor._pump`, trước khi gọi `callWorker` — nhánh fallback
  của `callWorker` gửi lại mỗi lần cho job từng game vì data là bản copy
  mới); job từng game và job merge gửi kèm `size`/`lastModified` để làm
  khóa (`batchFileKey`, cùng quy ước với `fileKey` trong worker-pool.js).
- FS của worker (`/tmp/input`) sống suốt phiên, không xóa giữa các job —
  nên file đã gửi dùng lại được. Chỉ file trong manifest mới được gửi
  (file đã chọn nhưng không nằm trong manifest thì không gửi — khác luồng
  cũ gửi cả lô đã chọn).
- Kiểm chứng: test A9 `pool_dedup_file_per_slot` (unit, fake worker) +
  test B4 `batch_sent_once_second_run_reuses` trong `verify_clone_fix.mjs`
  (đã cập nhật: probe gộp mọi worker, b1.zip gửi đúng 1 lần, b2.zip không
  trong manifest nên 0 lần, chạy lại lần 2 không gửi thêm).

## 6. Bốn bug sản phẩm thật tìm được trong lúc kiểm chứng (đã sửa)

1. `_pump` kết luận `pool-dead` khi slot đang boot (chưa ready) — sửa thành
   chỉ hủy job khi mọi slot đã chết; slot đang boot thì job cứ chờ.
2. `bootFailures` đếm trên từng object slot nên respawn reset về 0 → slot
   boot hỏng mãi respawn vô hạn (test A8 tốn 185 giây = đúng deadline; sau
   sửa còn 0,2 giây). Đếm theo CHỈ SỐ slot (`this.bootFailures[i]`).
3. 2 worker chạy manifest 1 game thì `_batch_index` đều = 1 → thư mục
   `game_01_*` đụng nhau khi gộp — thêm `index_base` cho `batch_run` +
   đổi `g.index` trong `recordGameDone` thành `it.index`.
4. **KB preset ghim sai file cho job ép locale (lỗi D6, tìm ra khi kiểm
   chứng lại sau lệnh dừng).** Cùng file chạy 2 lần trên worker đơn
   (record_kb=true): job 1 (không ép locale) ghi profile vào KB; job 2
   (ép locale "en") đọc profile qua `_kb_preset` và bị preset một file
   chưa rõ locale (vd font — không có trong danh sách locales của
   profile) → extraction chỉ parse file đó → không thấy locale "en" →
   `E_LOCALE_NOT_FOUND "game không có locale en; có sẵn: ?"` dù game CÓ
   locale "en" ở file khác. Qua pool thì không lỗi vì mỗi worker có KB
   riêng còn trống (không có preset). Sửa ở `site/pb3/sdk/batch.py`
   (`_kb_preset`): khi job ép locale, chỉ preset file khi locale của file
   đã BIẾT và KHỚP (`file_locale == item.locale`); file chưa rõ locale
   thì bỏ preset để extraction tự quét và tìm đúng file (preset chỉ là
   tối ưu, không preset thì vẫn chạy đúng). Tái hiện trực tiếp bằng
   Pyodide-Node (`dbg_locale.mjs`, đã xóa sau khi xong): trước sửa game 2
   failed `E_LOCALE_NOT_FOUND` (kb_used=true), sau sửa game 2 ok
   ("6 entry, 1 file", vẫn dùng KB). **Nợ port sang iSH:** `sdk/batch.py`
   là code chung với cây iSH — sửa này phải port sang cây iSH ở patch03
   (cùng với fix mojibake ZIP đã nợ trước đó) và chạy lại suite iSH.

Quirk có sẵn, không do mục 4: whole-lot + KB + cùng file 2 lần với locale
ép → game 2 failed `E_LOCALE_NOT_FOUND` (harness B5 đã né bằng
`record_kb=false`). — **Đã sửa ở bug 4 trên nên quirk này không còn nữa
trên bản web** (B5 vẫn giữ `record_kb=false` vì pool không gộp được KB,
không phải vì quirk).

## 7. Kết quả harness (chi tiết trong `item4_results.md` / `.json`)

**Lần chạy cuối: 28/28 PASS, 0 FAIL** (chạy 2026-11-16 ~13:15 +07, ~78 giây
— nhanh hơn các lần trước vì không còn timeout 300s).

- A (unit WorkerPool, fake worker): 9 test — chia job 2 slot, status
  busy/label, runOn ghim slot, cancelQueued an toàn, constructor ném lỗi →
  fallback, worker lỗi → respawn, broadcast begin/stop, boot hỏng hết →
  ready()=false, dedup file theo slot (A9, mới).
- B (bridge thật, Pyodide Node): batch_split khóa khớp sha256, batch_run
  từng game + out_dir_name + record_kb/make_zip, resume bỏ qua game đã
  xong, batch_zip/merge/finalize gộp 2 worker (2 instance Pyodide độc
  lập), tương thích ngược batch_run cũ, CSV, out_dir_name xấu → fallback,
  timing bigtest, batch_split giữ đủ field mod.
- C (tĩnh): node --check, không COI/pthreads/SharedArrayBuffer, wiring
  index.html/app.js/worker.js/bridge.py.
- D (trình duyệt thật, Playwright): boot (5,9s) → batch 2 game qua pool
  (8,2s: cả 2 worker đều làm, checkpoint IndexedDB, tải batch.zip) →
  reload → checkpoint còn → resume bỏ qua hết (5,5s) → Dừng an toàn +
  Tiếp tục (17,4s) → watchdog 0,5s dừng an toàn + Tiếp tục chạy hết
  (23,6s) → fallback 1 worker (0,7s: 2 ok) → không pageerror cả phiên.

Lưu ý về các lần FAIL trước (đều đã sửa):
- D4 từng FAIL 2 lần do **lỗi kỳ vọng của harness, không phải lỗi sản
  phẩm** — lần 1 chờ text `"đã xong 1/6 game"` chỉ tồn tại ~10ms (2 game
  song song xong gần như cùng lúc); lần 2 chờ `"2 ok / ..."` trong khi
  ngữ nghĩa resume đúng là `"0 ok / 4 lỗi / 2 bỏ qua / 8 game"` (2 game
  ok cũ tính skipped). Đã sửa kỳ vọng.
- D5 từng FAIL do **thiết kế test sai**: sau khi bấm Tiếp tục, hook
  watchdog 0,5 giây vẫn còn hiệu lực nên đợt Tiếp tục (3 game, 2 lượt
  ~4s) bị watchdog dừng lại lần nữa, không bao giờ ra `"0 ok / 3 lỗi"`.
  Sản phẩm đúng (dừng an toàn lần nữa); test đã sửa: trước khi bấm Tiếp
  tục thì trả ngưỡng về 25 giây (như production) rồi mới đợi kết quả cuối.
- D6 từng FAIL do **bug sản phẩm thật** (mục 6, bug 4: KB preset ghim
  sai file → `E_LOCALE_NOT_FOUND`); đã sửa ở `sdk/batch.py` và D6 giờ
  PASS ("2 ok / 0 lỗi / 0 bỏ qua / 2 game", 0,7s).
- A9 từng FAIL do **lỗi test**: đổi `FakeWorker.behavior` static sau khi
  pool đã tạo worker (behavior đọc lúc construct) nên worker không bao giờ
  lỗi; đã sửa thành đặt behavior trên instance + assert respawn thật.

## 8. Giới hạn thật

- Pool 2 worker tốn ~2x RAM so với 1 worker (mỗi Pyodide + PB3 riêng).
  **Chưa đo trên iPhone thật** — cần Joseph đo trên Safari thật (RAM thấp
  có thể nặng).
- Batch ở chế độ pool không ghi KB (mục 3 trên).
- Checkpoint lưu toàn bộ file JSON gộp sau mỗi game (chưa làm lưu tăng
  dần từng game riêng lẻ).
- Watchdog 25 giây là ngưỡng cho cả đợt batch, chưa đo trên iPhone thật
  (đo lại sau khi Joseph thử với IPA thật).
- Harness cũ chạy lại sau mục 4: hotfix_safari2 17/17, module_worker 17/17,
  patch01 8/8, parity 14/14 (chạy lại SAU sửa `_kb_preset` — đường batch cũ
  không đổi), limit_2gb 6/6, clone_fix 10/10 (sau sửa dedup + stub batch
  mới), phase1_ci 8/8, item2 17/17, item3_kb 27/27, redesign 10/10.

## 9. File thay đổi (đẩy lên main)

| Local | Repo path |
|---|---|
| `site/pb3/bridge.py` | `pb3/bridge.py` |
| `site/pb3/sdk/batch.py` | `pb3/sdk/batch.py` (sửa `_kb_preset`, bug D6) |
| `site/worker.js` | `worker.js` |
| `site/app.js` | `app.js` |
| `site/index.html` | `index.html` |
| `site/README.md` | `README.md` |
| `site/worker-pool.js` | `worker-pool.js` (mới) |

Chỉ local, không đẩy: `exp/verify_item4_pool.mjs` (harness),
`exp/item4_results.md` + `item4_results.json` (output harness),
`exp/item4_report.md` (bản prose này),
`exp/fixtures/bigtest.zip` (fixture, harness tự tạo lại nếu thiếu).
