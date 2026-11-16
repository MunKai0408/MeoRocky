# Mục phân tích C9 — Worker pool batch cấu hình được + sửa câu sai về song song (BearTea web)

Ngày: 2026-11-16. Repo MunKai0408/MeoRocky, head main trước C9 là 3c7c789
(sau C8 0a27bf3 + follow-up cập nhật harness). Commit đẩy C9: 465e862ed157285660045319e6d279b5a834fe48. Harness: `exp/verify_analysis_c9.mjs` —
**16/16 PASS**. Full suite Python: **1657 test, OK (skipped=2)**. Hồi quy:
module-worker 17/17, C2 13/13, item8 static 0 fails, C6/C7/C8 static
0 fails.

## 1. Việc cần làm (theo yêu cầu)

1. **Worker N cấu hình được:** pool batch trước đây cố định 2 worker
   (`const POOL_SIZE = 2` trong `app.js`). Thêm cài đặt cho người dùng
   chọn số worker, mặc định 2, tự động 1 khi RAM thiết bị thấp, lưu qua
   lần tải lại; pool batch phải tôn trọng N.
2. **Sửa câu sai về song song:** rà soát `app.js`, `index.html`,
   `README.md` và mọi UI còn câu "không song song thật trong Python"
   hoặc cách diễn đạt tương đương sai. Sự thật đã kiểm chứng: 2 worker
   LÀ 2 thread hệ điều hành thật, chạy SONG SONG THẬT (batch 2 game xong
   24,6s trên file thật); cái KHÔNG song song là BÊN TRONG một instance
   Pyodide (không có pthreads).

## 2. Khảo sát trước khi làm

- Cụm nguyên văn "không song song thật trong Python" **không còn tồn tại**
  ở đâu trong cây (đã grep toàn bộ `site/`): mục "Giới hạn" trên live site
  đã được sửa đúng ở commit 56179e4 ("Batch chạy song song trên pool
  2 worker (2 thread hệ điều hành thật, ...)") — không sửa lại chỗ này
  ngoài việc cập nhật "pool 2 worker" thành "pool worker" cho khớp N cấu
  hình được.
- Những cách diễn đạt tương đương còn sót, dễ đọc sai thành "không song
  song thật":
  - `index.html` chân trang: "Batch trên web nay cũng chạy song song bằng
    pool 2 worker (không dùng threading)." — ngoặc "(không dùng threading)"
    dễ hiểu là "không song song thật".
  - `README.md` mục Giới hạn: "Pyodide trên GitHub Pages không có threading
    nên song song đi bằng 2 worker độc lập" — đúng nhưng không nói rõ
    worker LÀ thread hệ điều hành thật.
  - `README.md` bảng so sánh + `index.html` ghi chú tab Batch: "2 worker
    độc lập (không threading/pthreads)", "Pool 2 worker song song".
  - Cả ba đã được viết lại (xem §3).
- `navigator.deviceMemory` trong Chromium headless shell đo được **8**
  (không phải undefined như đo thử trên `about:blank` trước đó) — trên
  trang thật, headless shell báo 8 GB nên mặc định 2, không ép về 1.
  Safari không có `navigator.deviceMemory` (undefined) nên mặc định vẫn 2.
- `finalizeBatchZip` chỉ gộp khi `br.gamesPerSlot[1] > 0` — với N=1 thì
  slot 1 không bao giờ có game nên tự bỏ bước gộp, không cần sửa.
- `runBatchFlow` khi không resume thì reset checkpoint (`merged =
  emptyCkpt()`) nên chạy lại cùng manifest vẫn chạy đủ game — harness
  chạy batch lặp không bị checkpoint chặn.
- C9 **không thêm/bớt file Python hay test** nên `files.json` vẫn 91,
  `tests.json` vẫn 160 (đã kiểm chứng, không đổi kỳ vọng đếm).

## 3. Thay đổi

### 3.1 Worker N cấu hình được (`app.js`, `worker-pool.js`, `index.html`)

- `app.js`: bỏ `const POOL_SIZE = 2`; thêm khối C9:
  - `POOL_SIZE_MIN = 1`, `POOL_SIZE_MAX = 2`, `POOL_SIZE_DEFAULT = 2`,
    `POOL_SIZE_LS_KEY = "pb3_pool_size"`, `POOL_SIZE_LOW_MEMORY_GB = 4`.
  - `deviceMemoryGB()` — đọc `navigator.deviceMemory`, có hook harness
    `window.__PB3_DEVICE_MEMORY` (giống tiền lệ
    `window.__PB3_WATCHDOG_SECONDS`); trình duyệt không báo RAM thì trả
    `null`, không ép về 1.
  - `defaultPoolSize()` — 1 khi RAM ≤ 4 GB, còn lại 2.
  - `loadPoolSize()` / `savePoolSize(n)` / `clampPoolSize(n)` /
    `getPoolSize()` — lựa chọn đã lưu trong localStorage thắng mặc định
    tự động; `state.poolSize` khởi tạo bằng `loadPoolSize()` lúc tải trang.
  - `poolLabel(n)` — "pool 1 worker" / "pool 2 worker".
  - `makePoolExecutor()` tạo `WorkerPool` với `size: getPoolSize()`,
    lưu `poolSize` lên executor, ghi hook `window.__PB3_POOL_SIZE = n`.
  - `ensureBatchExecutor()` tạo lại pool khi `executor.poolSize !==
    getPoolSize()` (đổi N thì pool cũ bị hủy để đợt sau tạo pool mới
    đúng N).
  - `combinedSummary()` dùng `br.poolSize` cho `jobs`; `renderBatchResult`,
    `syncPoolUI`, `progStart`, ghi chú cuối đợt đều dùng nhãn động theo N.
- `index.html` tab Batch: thêm ô chọn `#batch-pool-size` (1 worker nhẹ
  RAM / 2 worker song song thật, mặc định 2) + dòng ghi chú
  `#batch-pool-size-note` (do `syncPoolSizeUI()` điền: mặc định, RAM
  thiết bị, tự động về 1, "áp dụng từ đợt sau" khi đổi giữa chừng).
- Đổi N giữa chừng **không crash**: handler `change` lưu lựa chọn ngay;
  nếu đang có đợt batch chạy thì pool cũ chạy nốt đợt này (ghi chú rõ
  "đợt đang chạy giữ nguyên pool cũ, áp dụng từ đợt batch sau"); nếu
  không chạy gì thì hủy pool cũ để đợt sau tạo pool mới đúng N.
- `worker-pool.js`: thêm bảo vệ `runOn` ghim slot không tồn tại
  (`slotIdx >= this.size` → rơi về hàng đợi chung thay vì treo mãi —
  vd job cũ ghim slot 1 sau khi đổi N xuống 1); cập nhật ghi chú đầu
  file (C9, ~Nx RAM).

### 3.2 Sửa câu sai về song song

Viết lại đúng ý "song song thật, mỗi worker 1 thread hệ điều hành thật;
chỉ bên trong một worker thì Python tuần tự vì Pyodide không có pthreads"
tại:

- `index.html`: mục Giới hạn ("Batch chạy song song thật trên pool
  worker (mặc định 2 worker — 2 thread hệ điều hành thật...)"), ghi chú
  tab Batch ("Pool worker song song thật — mỗi worker là 1 thread hệ điều
  hành thật..."), chân trang, thẻ feature Batch, checkbox "Dùng pool worker".
- `README.md`: mục Batch ở đầu, mục Giới hạn thật, bảng so sánh
  ("pool worker song song thật", "mỗi worker 1 thread hệ điều hành thật
  nên pool chạy song song thật (batch 2 game xong 24,6s trên file thật)").
- `app.js` ghi chú kiến trúc đầu mục Batch + `worker-pool.js` ghi chú đầu
  file. Chuỗi cũ `"pool 2 worker"` không còn xuất hiện trong bất kỳ chuỗi
  động nào của UI (chỉ còn trong câu giải thích "~2x RAM" và ghi chú
  code — đúng ngữ cảnh, không sai ý).

### 3.3 `sw.js`

- `CACHE_VERSION` v13-20261116 → **v14-20261116** (app.js/index.html/
  README.md/worker-pool.js đổi nội dung). Không thêm file precache nào
  (C9 không thêm file site).

### 3.4 Harness cũ cập nhật theo

- `check_item8_static.py`, `check_analysis_c7_static.py`,
  `check_analysis_c8_static.py`, `verify_analysis_c8.mjs`: kỳ vọng
  CACHE_VERSION v13 → v14 (đã chạy lại C6/C7/C8 static: 0 fails).
- `verify_limit_2gb.mjs`, `verify_patch01_web.mjs`,
  `verify_phase1_ci.mjs`, `check_analysis_c6_static.py`: đếm 91/160
  KHÔNG đổi (C9 không thêm/bớt file Python) nên không cần sửa; đã grep
  toàn bộ `exp/` — không còn `v13` trong bất kỳ harness nào.

## 4. Kiểm chứng (harness C9: 16/16 PASS)

`node exp/verify_analysis_c9.mjs` — Chromium headless shell thật, 390px,
serve site qua HTTP local; fixture game thật `game1.zip`
(`pb3_fix/pb3/tests/fixtures/extract/game1.zip`) + `bigtest.zip`
(4000 file text, đã có sẵn trong `exp/fixtures/`).

| Mục | Kết quả |
|---|---|
| `static_node_check` | PASS — `node --check` app.js, sw.js, worker-pool.js |
| `static_c9_checks` | PASS — `check_analysis_c9_static.py` xanh (N 1–2, tự động ≤ 4 GB, localStorage, chuỗi cũ hết, 91/160, CACHE v14, đủ dấu) |
| `pool_size1_one_worker_sequential` | PASS — size 1 tạo đúng 1 Worker; 3 job slow đều slot 0, 903ms (tuần tự 3×300ms) |
| `pool_size2_two_workers_parallel` | PASS — size 2 tạo đúng 2 Worker; cả 2 slot busy cùng lúc, 301ms (chồng nhau) |
| `pool_size1_runon_missing_slot_falls_back` | PASS — runOn(1) trên pool 1 worker về slot 0, không treo |
| `pool_recreate_size_change` | PASS — hủy pool 2 rồi tạo pool 1: đúng 1 Worker mới |
| `browser_boot_ready` | PASS — "Sẵn sàng — 20 plugin · font 142/142"; deviceMemory thật = 8 |
| `browser_default_pool_size_2` | PASS — ô chọn "2", `__PB3_POOL_SIZE`=2, ghi chú "Mặc định 2 worker" |
| `browser_batch_n2_both_workers` | PASS — 2 ok qua pool 2 worker, worker 0: 1 game · worker 1: 1 game, tải batch.zip |
| `browser_switch_to_n1_idle` | PASS — đổi sang 1 lúc rảnh: pool cũ hủy, batch sau pool 1 worker, worker 0: 2 game, không worker 1, 0 pageerror |
| `browser_pool_size_persists_reload` | PASS — reload giữ "1"; đổi "2", reload giữ "2" |
| `browser_switch_n_mid_batch_safe` | PASS — đổi sang 1 giữa batch 4 game bigtest đang chạy: không crash, 0 pageerror mới, đợt đang chạy giữ pool 2 worker, đợt sau pool 1 worker |
| `browser_390_no_overflow_main` | PASS — scrollWidth=390 = clientWidth=390 |
| `browser_low_memory_auto_n1` | PASS — `__PB3_DEVICE_MEMORY=4`, chưa có lựa chọn lưu (localStorage null) → ô chọn "1", ghi chú "tự động ... 4 GB", batch pool 1 worker, worker 0: 2 game, không tràn, 0 pageerror |
| `browser_no_errors_main_session` | PASS — 0 pageerror/console-error thật suốt phiên chính |
| `shell_size_after_c9` | Ghi số thật — raw 363.672B (355,1KB), gzip 105.617B (103,1KB); xem §6 |

## 5. Hồi quy

- `verify_module_worker.mjs`: **17/17 PASS**.
- `verify_analysis_c2.mjs`: **13/13 PASS**.
- `check_item8_static.py`: **0 fails**; `check_analysis_c6/c7/c8_static.py`:
  **0 fails** (sau khi cập nhật v14).
- Full suite Python (`unittest discover` trong `site/pb3`):
  **Ran 1657 tests in 55,3s — OK (skipped=2)**, exit 0.

## 6. Điểm chưa đạt / việc còn lại

1. **Vỏ trang raw 363.672B > 100KB** (FAIL item6 đã biết, không phải do
   C9 gây ra; tăng từ 356.866B sau C8 lên 363.672B — app.js 211.161B →
   216.652B do thêm logic C9; gzip 103,1KB). Ghi nhận như các mục trước,
   chưa sửa.
2. **Tự động về 1 chưa kiểm chứng trên thiết bị thật:** đường tự động
   dựa vào `navigator.deviceMemory` — Safari không có API này (undefined)
   nên trên iPhone thật mặc định vẫn 2; cần Joseph thử trên máy Android
   RAM ≤ 4 GB (Chrome báo deviceMemory) hoặc máy RAM thấp để thấy ô chọn
   tự về 1. Đường giả lập `__PB3_DEVICE_MEMORY` chỉ dùng cho harness.
3. **Chưa có game thật nào để đo lại song song với N=1 vs N=2 trên cùng
   máy:** con số 24,6s (batch 2 game) là từ đợt kiểm chứng trước; so sánh
   N=1/N=2 trên Safari thật vẫn cần đo khi Joseph chạy batch trên iPhone.

## 7. Link kiểm chứng

- Commit đẩy C9: 465e862ed157285660045319e6d279b5a834fe48 (https://github.com/MunKai0408/MeoRocky/commit/465e862ed157285660045319e6d279b5a834fe48)
- Kiểm chứng trên main (raw, không có `site/`):
  `https://raw.githubusercontent.com/MunKai0408/MeoRocky/main/app.js`
- Trang live: https://munkai0408.github.io/MeoRocky/
- Thử nhanh trên Safari thật: mở trang → tab Batch → ô "Số worker trong
  pool" phải hiện 2 (mặc định); đổi sang 1 → tải lại trang → ô vẫn 1;
  chạy batch 2 game với N=1 thì kết quả ghi "pool 1 worker, worker 0:
  2 game".
