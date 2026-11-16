# Kiểm chứng mục C3 — PWA + persist() + nhắc export + Pyodide prefetch sau first paint

Ngày: 2026-11-16T12:19:28.932Z (giờ UTC). Harness: exp/verify_analysis_c3.mjs.

## Kết quả: 16/16 PASS

### static_node_check_manifest: PASS (0.2s)
node --check OK: app.js, sw.js; manifest: name="BearTea — PB3 bản web", short_name="BearTea", start_url=".", display="standalone", icons 192=true, 512=true

### static_sw_selftest_precache_prefetch: PASS (0s)
CACHE_VERSION=v10-20261116 (cần v10-20261116); precache 126 mục, có fixtures/selftest.zip=true (file trên đĩa: 169B); PREFETCH_CACHE const=true; fetch thử prefetch cache khi cache chính trượt=true; GET_VERSION trả prefetchCache=true

### static_c3_wiring: PASS (0s)
kiểm tra wiring C3 trong app.js: beforeinstallprompt=true, appinstalled=true, standalone=true, pwaStateHook=true, persist=true, estimate=true, estimateUi=true, earlyPersist=true, exportedBtn=true, snoozeBtn=true, earlyReminder=true, prefetchUrls=true, prefetchStateHook=true, prefetchNoHook=true, prefetchDelay4000=true, prefetchAfterLoad=true, prefetchSkipCached=true, prefetchCacheCounts=true, prefetchSaveData=true

### vm_no_prefetch_at_load: PASS (0s)
__PB3_BOOT_STATE={"booted":false,"workerCreated":false}; fetch đã gọi=0; __PB3_PREFETCH_STATE={"started":false,"done":false,"loadedBytes":0,"totalBytes":0,"filesDone":0,"filesTotal":6,"cachedFiles":0,"fetchedFiles":0,"failedFiles":[],"status":"idle","pct":0}; __PB3_PWA_STATE={"hasPrompt":false,"installed":false,"standalone":false} (nạp xong chưa Worker, chưa fetch, prefetch chưa chạy — chỉ chạy sau load/idle)

### shell_size_after_c3: PASS (0s)
shell lần tải đầu sau C3: index.html raw=55640B gzip=15854B; kb-store.js raw=8192B gzip=3212B; worker-pool.js raw=14404B gzip=5058B; stream.js raw=18358B gzip=6141B; app.js raw=198447B gzip=55879B; gt.js raw=34379B gzip=9599B | TỔNG raw=329420B (321.7KB), gzip=95743B (93.5KB). Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C3 thêm ~29219B vào app.js, không đổi kết luận.

### browser_persist_estimate_early: PASS (0.2s)
#kb-persist (hiện sớm, chưa boot/chưa chọn file): "Lưu trữ bền vững: CHƯA CẤP — Safari có thể xóa KB sau ~7 ngày không mở trang; hãy cài BearTea vào Màn hình chính (mục dưới) để giữ KB bền hơn. Đã dùng 110 B / hạn mức 458 MB."; __PB3_STORAGE_STATE={"persisted":false,"granted":false,"usage":110,"quota":480478063,"estError":null,"at":1794831531362} (headless Chromium thường CHƯA CẤP vì chưa cài/ưa dùng — ghi đúng sự thật, không PASS giả)

### browser_pwa_install_wiring: PASS (1.8s)
lúc đầu nút Cài ẩn=true (headless không có beforeinstallprompt thật); pwa-note có hướng dẫn tay iOS=true; __PB3_PWA_STATE lúc đầu={"hasPrompt":false,"installed":false,"standalone":false}; sau event giả (dismissed): nút hiện=true; bấm → pwa-note="Bạn đã đóng bảng cài đặt — tải lại trang để thử lại, hoặc cài bằng tay theo hướng dẫn trên (Safari: Chia sẻ → Thêm vào M" (ghi rõ đã đóng bảng cài đặt); sau event giả (accepted): bấm → prompt được gọi 1 lần, nút ẩn lại=true, ghi chú đã cài hiện=true. Headless KHÔNG cài PWA thật được — chỉ kiểm wiring + manifest, không tuyên bố cài thành công.

### browser_prefetch_after_first_paint: PASS (3s)
__PB3_PREFETCH_STATE={"started":true,"done":true,"loadedBytes":13586450,"totalBytes":13586450,"filesDone":6,"filesTotal":6,"cachedFiles":6,"fetchedFiles":0,"failedFiles":[],"status":"done","pct":100}; UI="Pyodide đã tải trước xong (13.0 MB, 6/6 file — 6 file đã có sẵn trong cache, 0 file tải mới) — lần dùng sau sẽ khởi động nhanh hơn."; thanh tiến độ value=100; boot sau prefetch: booted=false, workerCreated=false; request Pyodide từ trang: 0 file, sớm nhất nullms sau khi bắt đầu tải trang (0 request vì cả 6 file đã có sẵn trong cache từ SW precache — không tốn mạng thêm); từ cache=6, tải mới=0

### browser_boot_cold_ms: PASS (5.8s)
boot lạnh (context mới, __PB3_NO_PREFETCH, chọn file fixture ngay sau tải trang): 5721ms từ lúc chọn file tới “Sẵn sàng — 20 plugin” (đo thật, gồm tải Pyodide qua mạng local + biên dịch wasm)

### browser_boot_warm_vs_cold: PASS (5.2s)
boot lần 2 (cùng context, cache ấm từ SW precache + prefetch/lần boot trước): 5090ms; boot lần 1 (lạnh): 5721ms → lần 2 NHANH HƠN 11% (631ms). Lưu ý: số đo trên máy chủ local (mạng LAN nhanh) nên chênh lệch nhỏ hơn trên mạng thật/Safari.

### browser_export_reminder_shows: PASS (6.2s)
sau khi thêm game vào KB (KB đã có dữ liệu, lần xuất cuối 8 ngày trước): banner nhắc hiện, nội dung="Nhắc sao lưu: lần xuất KB cuối đã hơn 7 ngày trước (8/11/2026) — hãy bấm “Tải KB về (.sqlite)” để sao lưu bản mới nhất."

### browser_export_reminder_exported_btn: PASS (0.3s)
bấm “Đã xuất”: banner ẩn=true; mốc lần xuất cuối trong localStorage=2026-11-16T12:19:13.521Z (cách bây giờ 0s — đặt lại mốc đúng)

### browser_export_reminder_early_reload: PASS (0.1s)
tải lại trang (chưa boot, chưa chọn file — kiểm tra sớm qua IndexedDB): banner nhắc hiện lại, nội dung="Nhắc sao lưu: lần xuất KB cuối đã hơn 7 ngày trước (8/11/2026) — hãy bấm “Tải KB về (.sqlite)” để sao lưu bản mới nhất."

### browser_export_reminder_snooze: PASS (6.5s)
bấm “Tắt nhắc lần này”: mốc snooze=2026-11-23T12:19:14.005Z (sau ~7 ngày nữa mới nhắc lại); tải lại trang rồi đợi 6s: banner vẫn ẩn=true (không nhắc liên tục mỗi lần mở trang)

### browser_offline_selftest_after_first_visit: PASS (6.4s)
lần thăm 1 online: cache chính 126/125+ mục, fixtures/selftest.zip trong cache=true; offline tải lại → boot tới Sẵn sàng → tự kiểm tra: 5/6 PASS (mục Dịch thử FAIL thật: "Google lỗi mất mạng/CORS → MyMemory lỗi mất mạng/CORS · LibreTranslate chưa cấu hình (cần API key) — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu — không mạng, không PASS giả); trích xuất thử chạy được offline chứng tỏ selftest.zip đã precache (nợ C2 đã trả)

### browser_390_no_overflow_no_errors: PASS (2.2s)
390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror cả phiên (5 context): không có

## File thay đổi
- `site/sw.js`: CACHE_VERSION v8 → v9; thêm `fixtures/selftest.zip` vào PRECACHE_URLS (nợ C2); thêm PREFETCH_CACHE `beartea-prefetch-v1` + fetch dự phòng khi cache chính trượt; GET_VERSION trả thêm prefetchCache.
- `site/index.html`: nhắc export thành div có nút “Đã xuất” + “Tắt nhắc lần này”; thêm `#pwa-installed-note`; thêm `#pyodide-prefetch` (chữ + thanh tiến độ) dưới dòng trạng thái.
- `site/app.js`: refreshKbPersistStatus() thêm estimate() (Đã dùng/hạn mức) + hook `__PB3_STORAGE_STATE`; xin persist() sớm sau first paint/lần tương tác đầu; PWA appinstalled + standalone + kết quả prompt; nhắc export có snooze 7 ngày + kiểm tra sớm qua IndexedDB (không cần boot); prefetch Pyodide sau first paint (trì hoãn ~4s, % thật theo byte, bỏ qua file đã cache, ghi vào cache SW/prefetch, hook `__PB3_PREFETCH_STATE`/`__PB3_NO_PREFETCH`).
- `exp/verify_item6.mjs`: expected thêm `fixtures/selftest.zip` (theo nợ C2).
- `exp/check_item7_static.py`, `exp/check_item8_static.py`, `exp/verify_item8.mjs`: ghim CACHE_VERSION v8 → v9 (đổi precache thì phải đổi version, theo luật trong sw.js).
- Harness mới: `exp/verify_analysis_c3.mjs` (+ kết quả này).

## Safari thật — chưa thử được
- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.
- CẦN JOSEPH (Safari thật, iPhone): mở trang → xem dòng “Đang tải trước Pyodide… %” rồi bấm dùng tool (boot có nhanh hơn không); tab Kho KB → xem “Lưu trữ bền vững” + “Đã dùng/hạn mức”; thử “Thêm vào Màn hình chính” bằng tay (Safari không có beforeinstallprompt); để KB quá 7 ngày không xuất → xem banner nhắc + nút “Đã xuất”/“Tắt nhắc”.

