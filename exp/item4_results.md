# Kiểm chứng Mục 4 — Pool 2 worker + checkpoint từng game + watchdog hợp tác

Ngày: 2026-11-16. Harness: exp/verify_item4_pool.mjs.
A: unit WorkerPool với Worker giả. B: bridge thật trên Pyodide Node (1 + 2 instance).
C: kiểm tra tĩnh. D: trình duyệt thật (Playwright chromium headless shell, serve site qua HTTP local).

## Kết quả
- PASS **pool_runall_2slots_order** (0s) — Promise.all 4 job -> echo theo đúng thứ tự a,b,c,d; slot đã dùng: 0,1 (cần cả 2 slot)
- PASS **pool_status_busy_label** (0.3s) — đang chạy: status có slot busy + label 'game-lau.zip': true; xong: mọi slot rảnh + label trống: true
- PASS **pool_runon_pins_slot** (0.2s) — runOn(1) -> reply slot=1; worker[1] nhận 1 job run, worker[0] nhận 0
- PASS **pool_cancel_queued_safe** (0.3s) — 4 job slow (2 slot): xong 2 (đang chạy được chạy hết), hủy chờ 2 với E_BATCH_STOPPED
- PASS **pool_construct_throws_fallback** (0s) — Worker constructor ném -> _spawn bắt gọn, slot chết (không crash pool); ready()=false, aliveCount=0 -> app rơi về 1 worker
- PASS **pool_worker_error_respawn** (0.2s) — job đầu lỗi worker: "fake worker crash"; job sau chạy được (echo=y); slot sống sau respawn: 2/2
- PASS **pool_broadcast_begin_stop** (0.2s) — batch_begin(25) tới 2/2 worker; batch_stop tới 2/2 worker
- PASS **pool_ready_false_when_all_boot_fail** (0.2s) — cả 2 slot boot-fail (kể cả respawn, MAX 2 lần) -> ready()=false (app rơi về 1 worker)
- PASS **pool_dedup_file_per_slot** (0.5s) — slot 0 lần 1 gửi g.zip: true; lần 2 cùng file bỏ qua (files=[]): true; slot 1 (FS riêng) nhận 1 lần: true; crash slot 0: "fake worker crash"; respawn tạo worker mới: true; sau respawn gửi lại: true
- PASS **bridge_batch_split_keys** (0.1s) — split 2 item (game1 null/en) -> key[0]=f875111249d4… khớp sha256 Python-style: true; key[1] khác key[0]: true
- PASS **bridge_batch_run_per_game** (0.6s) — 1 game extract ok (0.4s) vào out_dir run-b2; make_zip=false -> zip_path=null; checkpoint sau game 1 có 1 game ok
- PASS **bridge_batch_resume_skip** (0s) — chạy lại game1 với checkpoint gộp -> skipped=1, note="đã xong (checkpoint)"
- PASS **bridge_batch_merge_finalize_2workers** (5.2s) — A(game1 null) ok + B(game1 en) ok; zip B 1428B -> merge vào A: 1 file; finalize batch.zip 1610B chứa cả 2 thư mục game (game_01_game1, game_02_game1) + checkpoint gộp 2 game + index.csv 3 dòng
- PASS **bridge_batch_run_legacy_compat** (0s) — batch_run(manifest, true, null, false, null, false, true) vẫn chạy: total=2 ok=2, zip 1852B, checkpoint 2 game (harness cũ verify_parity dùng đường này)
- PASS **bridge_batch_split_csv** (0s) — CSV 1 dòng -> 1 item path=game1.zip
- PASS **bridge_batch_bad_outdir_fallback** (0s) — out_dir_name '../evil' bị từ chối, dùng thư mục theo giờ /tmp/batch/20261116-072611, vẫn ok=1
- PASS **bridge_bigtest_extract_time** (2.1s) — bigtest (4000 file text) extract -> failed/E_NO_CANDIDATE sau 2098ms (>800ms: đủ chậm để watchdog 0,5s ở phần D bắt được game thứ 3)
- PASS **bridge_batch_split_mod_fields** (0s) — item mod giữ đủ object=1001 old='Xin chao' new='Chào'
- PASS **static_node_check** (0.5s) — node --check xanh: app.js, worker.js, worker-pool.js, kb-store.js, gt.js
- PASS **static_no_coi_no_pthreads** (0s) — không có new SharedArrayBuffer / header COOP-COEP / coi-serviceworker / Atomics trong app.js, worker.js, worker-pool.js, index.html
- PASS **static_wiring** (0s) — 16 điểm nối đều đúng (script, ID, hằng số, hàm mới)
- PASS **browser_boot_batch_tab** (5.8s) — status=Sẵn sàng; tab Batch mở được; #batch-pool-info ban đầu: "Pool chưa khởi động (bấm “Chạy batch” để khởi động pool 2 worker)."
- PASS **browser_pool_2games_ckpt_zip** (7.8s) — kết quả: 2 ok qua pool 2 worker, phân công worker 0: 1 game · worker 1: 1 game; #batch-pool-info hiện cả Worker 0 và Worker 1; checkpoint IndexedDB: 2 game, 2 ok; download batch.zip: batch.zip
- PASS **browser_ckpt_survives_reload_resume** (5.6s) — sau reload: "Có checkpoint đã lưu từ lần trước (IndexedDB, 2 game đã xong) — tick “Tiếp tục từ checkpoint” rồi bấm Chạy bat"…; chạy lại với resume -> "Batch dry-run · không cần chạy (đã xong hết theo checkpoint) · 2 ok / 0 lỗi / 2 bỏ qua / 2"…
- PASS **browser_stop_safe_and_continue** (17.8s) — dừng giữa chừng: kết quả có 'dừng an toàn', checkpoint IndexedDB giữ 4 game (2 ok + 2 failed bigtest, 4 game chưa chạy); Tiếp tục -> xong hết 0 ok / 4 lỗi / 2 bỏ qua / 8 game (2 game ok cũ tính skipped theo semantics resume), 2 dòng incomplete (ambig), nút Tiếp tục đã ẩn
- PASS **browser_watchdog_stop_and_continue** (22.2s) — ngưỡng 0,5s (test hook): 2 game bigtest chạy song song ~2s, game thứ 3 bị từ chối E_WATCHDOG_STOP -> '0 ok / 2 lỗi / 1 bỏ qua / 3 game' + 'watchdog đã dừng an toàn', checkpoint giữ 2 game; Tiếp tục (ngưỡng trả về 25s như production, không thì hook 0,5s dừng lại lần nữa) -> game 3 chạy hết: "Batch dry-run · pool 2 worker · 0 ok / 3 lỗi / 0 bỏ qua / 3 game · 4.8 giây · đã"…, nút Tiếp tục đã ẩn
- PASS **browser_fallback_single_worker** (0.9s) — bỏ chọn pool -> kết quả ghi '1 worker', phân công worker 0: 2 game, vẫn 2 ok; "Batch dry-run · 1 worker (pool tắt theo lựa chọn). · 2 ok / 0 lỗi / 0 bỏ qua / 2 game · 0.8 giây · file batch.zip đã sẵn"…
- PASS **browser_no_pageerrors** (0s) — pageerror trong cả phiên (boot + pool + reload + resume + dừng/Tiếp tục + watchdog + fallback): không có

Tổng: 28 mục, 0 FAIL.

## Ghi chú
- Watchdog ở phần D dùng hook test `window.__PB3_WATCHDOG_SECONDS = 0.5` (app.js chỉ đọc khi có, production dùng hằng số 25 giây).
- Fixture bigtest.zip (4000 file text, extract ~2 giây rồi E_NO_CANDIDATE) do harness tự tạo ở exp/fixtures/bigtest.zip.
- Pool 2 worker tốn ~2x RAM, chưa đo trên iPhone thật — cần Joseph đo trên Safari thật.
