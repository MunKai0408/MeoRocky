# Kiểm chứng mục C2 — Trang Chẩn đoán + nút Tự kiểm tra

Ngày: 2026-11-16. Harness: exp/verify_analysis_c2.mjs.
Trang/tab Chẩn đoán trong app (tab Chẩn đoán, mục "Chẩn đoán ứng dụng") + hoàn thiện diagnostics.html (nút Xuất JSON).

## Kết quả
- PASS **node_trace_ms_status** (7.1s) — kết quả="Xin chào thế giới"; google={ok:false, status:429, ms:7067, kind:http}; mymemory={ok:true, ms:5}; Google gọi 4 lần, MyMemory 1 lần
- PASS **node_trace_proxy_ok** (0s) — kết quả="VI qua proxy: Hello world"; proxy={ok:true, ms:5}; proxy gọi 1 lần, Google 0 lần, MyMemory 0 lần
- PASS **browser_boot** (6s) — boot tới Sẵn sàng (390px, proxy giả đã cấu hình sẵn)
- PASS **browser_diag_tab_full** (0.1s) — appdiag-status: "Khởi động: OK · 20 plugin (20 id) · font đi kèm: đủ 142/142Bộ nhớ ước tính: đã dùng 18 MB / hạn mức 467 MB · RAM thiết bị ~8 GBProviderTrạng tháiLần gọi gần nhấtProxy CloudflareĐã cấu hìnhchưa gọi lần nàoGoogle trực tiếpDùng sau proxychưa gọi lần nàoMyMemorySẵn sàng (không cần key)chưa gọi lần nàoLibreTranslateChưa cấu hình (mặc định tắt)chưa gọi lần nàoTừ điển offlineLuôn sẵn sàng (~100 từ/cụm)chưa gọi lần nào"
- PASS **browser_diag_proxy_configured_uncalled** (0.1s) — hàng Proxy trước tự kiểm tra: "Proxy CloudflareĐã cấu hìnhchưa gọi lần nào"
- PASS **browser_selftest_all_pass** (0.6s) — 6 mục (Khởi động PB3 | Danh sách plugin | Font đi kèm (142 mục) | Trích xuất thử (fixture) | Dịch thử 1 chuỗi | IndexedDB / KB); PASS 6/6; chi tiết: Khởi động PB3=PASS [Pyodide + PB3 đã khởi động, 20 plugin (0 ms)]; Danh sách plugin=PASS [20 plugin: apple_plist, apple_strings, cocos_detect, csv_table, defold_detect, … (0 ms)]; Font đi kèm (142 mục)=PASS [Be Vietnam Pro đủ 142/142 (527 glyph) (3 ms)]; Trích xuất thử (fixture)=PASS [English.json đủ 3 chuỗi (hello/start/settings), quyết định: done (452 ms)]; Dịch thử 1 chuỗi=PASS [provider: proxy — “Hello world” → “VI qua proxy: Hello world” (10 ms)]; IndexedDB / KB=PASS [ghi-đọc-xóa khóa thử __selftest__ trong IndexedDB OK (4 ms)]
- PASS **browser_selftest_repeatable** (0.1s) — lần 2: PASS 6/6 (nút bấm lại được, không treo UI)
- PASS **browser_diag_last_call_after_selftest** (0s) — hàng Proxy sau tự kiểm tra: "Proxy CloudflareĐã cấu hìnhOK · 4 ms"
- PASS **browser_export_diag_json** (0s) — file beartea-chan-doan.json (2483 byte): booted=true, plugins=20, quotaMB=467, proxy trace ok ms=4, selftest passed=6/6
- PASS **browser_390_no_overflow_main** (0s) — 390px: scrollWidth=390 clientWidth=390 (không tràn)
- PASS **browser_selftest_translate_fails_honestly** (6.6s) — không proxy + sandbox không mạng ngoài: PASS 5/6; mục Dịch thử = FAIL thật, lỗi nguyên văn: "Google lỗi mất mạng/CORS → MyMemory lỗi mất mạng/CORS · LibreTranslate chưa cấu hình (cần API key) — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên.\n- Google: Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS).\n- MyMemory: Không gọi được MyMe; không có mục nào bị ghi PASS giả
- PASS **browser_diagnostics_page_export** (0.3s) — xuất JSON có env (indexedDB=true); Google trực tiếp từ sandbox: ok=false, lỗi thật="fetch lỗi (mất mạng hoặc trình duyệt chặn CORS): Failed to fetch" (không đứng im, không PASS giả)
- PASS **browser_390_no_overflow_no_errors** (0s) — diagnostics.html 390px: scrollWidth=390 clientWidth=390; pageerror cả 3 context: không có

Tổng: 13 mục, 13 PASS, 0 FAIL.

## File thay đổi
- `site/gt.js`: trace fallback giàu thêm ms/status/errName/kind từng bước (hành vi fallback không đổi).
- `site/worker.js`: 2 op mới `diag_state` (cấu hình provider + trace lần gần nhất, không cần boot) và `diag_translate_probe` (dịch thử "Hello world" qua đủ chuỗi fallback).
- `site/app.js`: mục "Chẩn đoán ứng dụng" trong tab Chẩn đoán (bảng 5 provider + boot + bộ nhớ), nút "Tự kiểm tra" (6 mục thật), nút "Xuất JSON chẩn đoán" (qua download()/Lưu-Chia sẻ), hook `window.__PB3_LAST_DIAG`.
- `site/index.html`: giao diện mục Chẩn đoán ứng dụng trong #tab-diagnose (3 nút + 2 vùng kết quả).
- `site/diagnostics.html`: nút "Xuất JSON" (Chia sẻ chính, blob dự phòng).
- `site/fixtures/selftest.zip` (169 byte): fixture trích xuất thử — en.json 3 chuỗi, đã kiểm chứng extract ra English.json đủ 3 chuỗi trên Node Pyodide.
- Harness: `exp/verify_analysis_c2.mjs` (+ kết quả này, `exp/out/analysis_c2/*.json`).

## Safari thật — chưa thử được
- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.
- CẦN JOSEPH (Safari thật, iPhone): mở tab Chẩn đoán → bấm "Tự kiểm tra" (dịch thử sẽ đi qua Google/MyMemory thật); bấm "Xuất JSON chẩn đoán" → "Lưu / Chia sẻ" → "Lưu vào Tệp"; mở Trang Kiểm tra dịch → thử từng provider.

