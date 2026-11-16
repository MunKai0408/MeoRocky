# Kiểm chứng Mục A — Batch dịch (đợt bổ sung 13:52)

Ngày: 2026-11-16. Harness: exp/verify_translate_batch.mjs.
A: gtx thật từ Node. B: gt.js trực tiếp (fetch giả). C: bridge thật trên Pyodide Node (fixture translate_1000.json).
D: trình duyệt thật (Playwright chromium headless shell, 390px, proxy giả batch 250ms/batch).

## Kết quả
- PASS **gt_live_multiq_ignored** (0.5s) — GET 3q: HTTP 200 (bản dịch: "Chơi" — không có "Thoát" của q thứ 2: true); POST 50q: HTTP 200 (chỉ dịch q đầu: true) — KẾT LUẬN: gtx KHÔNG nhận nhiều q/request, batch phải dùng joined-text 1 q.
- PASS **gt_live_joined_50** (0.1s) — POST joined 50: HTTP 200; tách lại: 50/50 marker khớp (mẫu: ["Xin chuỗi số 0 để dịch","Xin chuỗi số 1 để dịch","Chuỗi số 2 để dịch nhé"]) — batch joined-text DỊCH ĐƯỢC THẬT từ IP máy chủ.
- PASS **gt_token_bucket_paces** (1.3s) — 2 lần warmup + 3 lần googleTranslateOne (fetch tức thì) mất 1312ms (cần >= 900ms: mỗi lần chờ nạp 1 token/500ms) — thay sleep 0,4s cố định.
- PASS **gt_backoff_429_retries** (3.5s) — fetch gọi 3 lần (429, 429, 200), mất 3504ms (cần >= 2900ms: backoff 1s + 2s), kết quả: "VI:Play"
- PASS **gt_batch_50_one_request** (0s) — fetch gọi 1 lần (POST), body có marker ⟦n⟧: true; tách lại 50/50, đầu="VI:String number 0 to translate please", cuối="VI:String number 49 to translate please"
- PASS **gt_batch_split_mismatch_bad_response** (0s) — googleTranslateBatch với bản dịch mất marker -> kind="bad_response" (cần "bad_response" để Python rơi về từng chuỗi)
- PASS **gt_abort_cancelled** (0.5s) — fetch đã bắt đầu: true; abort -> kind="cancelled" (cần "cancelled", không phải "timeout")
- PASS **gt_proxy_batch_and_quota** (0s) — proxy batch: ["VI:Play","VI:Quit"]; quota -> kind="quota"; translateBatchWithFallbacks mode=proxy-batch
- PASS **batch_1000_mock_success** (0.6s) — 1000 key -> 1035 segment, 652 unique (khử 383 trùng = 37.0%), cần Google 625 unique (từ điển lo 27); mock nhận 625 chuỗi qua 7 batch (tối thiểu 7 nếu đủ 100/batch), single=0, mode=gtx-joined; tiến trình cuối 1035/1035; mẫu Iron Sword="VI[Iron Sword]", Play="Chơi"; thời gian thật 644ms (mock delay 30ms/batch)
- PASS **batch_glossary_first_no_leak** (0s) — mock batch nhận 625 chuỗi; lọt từ điển: 0 (cần 0 — vd Play/Quit phải qua từ điển, không gửi Google); trùng lặp trong số đã gửi: 0 (cần 0); glossary_count=44
- PASS **batch_priority_ui_first** (0s) — thứ tự gửi Google: 625 chuỗi, ưu tiên không giảm (true); chuỗi UI cuối ở vị trí 375, chuỗi log/desc đầu ở vị trí 411 (cần UI trước log/desc)
- PASS **batch_fail_falls_back_single** (0.1s) — batch lỗi hết -> single_requests=625 (cần 625), batch_requests=0, mode=single, cảnh báo tắt batch: true; mẫu Iron Sword dịch đúng
- PASS **batch_wrong_count_detected** (0s) — batch 1 trả sai số lượng -> Python phát hiện, dịch lại 100 chuỗi đơn cho batch đó; batch_requests=6/7 batch; số chuỗi VI[ trong kết quả > 900
- PASS **batch_cancel_keeps_partial** (0.1s) — cancelled=true sau 1 batch; giữ lại 194/1000 chuỗi đã dịch (cần >0 và <1000); incomplete_keys=762 (phần chưa xong được báo để app.js đưa về hàng đợi resume)
- PASS **batch_quota_stops_google** (0s) — google_count=0, single gọi=0 (cần 0 — không gọi Google nữa); network_error="Google Translate lỗi (quota): Error: Proxy dịch: đã hết 500.000 ký tự miễn phí của tháng."; cảnh báo hạn mức: true
- PASS **browser_boot_proxy_set** (5.8s) — boot tới Sẵn sàng; proxy dịch giả (batch) đặt qua localStorage + ô nhập
- PASS **browser_batch_1000_real_time** (17.8s) — dialog ước tính batch 15 giây: true; thời gian THẬT 1000 chuỗi qua proxy giả (250ms/batch): 2.5s (≈ 398.8 chuỗi/giây); output 1000/1000 key; info: "Đã dịch 1000/1000 chuỗi (Google: 804 đoạn · từ điển offline: 36 đoạn · bộ nhớ dịch: 187 chuỗi · khử trùng lặp: 186 đoạn · batch: 7 request, từng chuỗi: 0). Xong — bấm “Tải bản dịch"
- PASS **browser_batch_cancel_keeps_partial** (1.4s) — sau Hủy: 100/1000 chuỗi đã dịch (giữ lại phần đã dịch, không mất); tải phần đã dịch: translated.vi.json với 100 chuỗi VI:
- PASS **browser_batch_resume_after_reload** (8.5s) — thanh resume sau reload: "dán tay — đã dịch 100/1000 chuỗi (đã hủy/dở dang)"; bấm Tiếp tục -> xong đủ 1000/1000 key, tất cả 1000 chuỗi đều VI: (không còn chuỗi gốc sót lại từ chunk bị hủy): true; thanh resume ẩn sau khi xong: true
- PASS **browser_390_no_overflow_no_errors** (0s) — 390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror: không có
- PASS **browser_share_error_shows_name** (5.8s) — bấm Lưu/Chia sẻ khi navigator.share ném NotAllowedError -> banner: "Chia sẻ lỗi: NotAllowedError: The request is not allowed by the user agent or the platform in the current context, possibly because the user denied permission." (cần cả tên lỗi + message)

Tổng: 21 mục, 0 FAIL.

## Số đo thật (không ước tính)
- Tỷ lệ khử trùng lặp trên fixture 1000 chuỗi: 37.0% (segment).
- Dịch 1000 chuỗi qua mock batch (Node, delay 30ms/batch): 644ms.
- Dịch 1000 chuỗi qua proxy giả (trình duyệt thật, delay 250ms/batch): 2.5s (≈ 398.8 chuỗi/giây).
- Tốc độ Google thật trên mạng iPhone chưa đo được ở đây (cần Joseph thử trên Safari thật); không ghi số ước tính.

## Kết luận kiểm chứng gtx (A.7)
- gtx KHÔNG nhận nhiều tham số q/request: GET/POST 3q và 50q đều trả HTTP 200 nhưng chỉ dịch q đầu tiên (xem gt_live_multiq_ignored).
- Batch dùng joined-text 1 q với marker ⟦n⟧: joined 50 tách lại 50/50, live từ IP máy chủ (xem gt_live_joined_50).
- HTTP 429 + Sorry là nhất thời (theo đợt gửi dồn), không phải chặn vĩnh viễn: cùng IP trả 200 sau vài phút; token bucket + backoff xử lý.
- Batch qua proxy Cloudflare (q mảng của Translation v2) là phương án dự phòng — code đã có (cloudflare/worker.js nhận {texts:[...]}), CHƯA DEPLOY (cần tài khoản Cloudflare + API key Google của Joseph).

## File thay đổi (CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)
- `site/gt.js` (batch joined-text, token bucket, hủy fetch, proxy batch, toTextArray cho JsProxy).
- `site/worker.js` (expose `pb3_google_translate_batch`, abort khi hủy).
- `site/pb3/bridge.py` (dedupe + glossary-first + ưu tiên + batch + `apply_credit_only`, bỏ sleep 0,4s).
- `site/cloudflare/worker.js` (proxy nhận `{texts:[...]}`, chưa deploy).
- `site/kb-store.js` (DB v2 + `TranslateJobStore`).
- `site/app.js` (chunk 500 key + hàng đợi bền + TM + lỗi Share hiện `err.name`), `site/index.html` (thanh resume, ghi chú batch).

## Nút Chia sẻ (mục B)
- Handler KHÔNG viết lại (không có await trước share — giả thuyết mất activation có thể sai).
- Lỗi Share giờ hiện cả tên + message (xem browser_share_error_shows_name).
- Chia file >400–500MB thành nhiều volume: CHƯA LÀM (cần Joseph xác nhận có file >400MB thật + test share nhiều file trên Safari thật; file lớn nhất từng thấy là out.tar.xz 95,6MB). URL blob vẫn giữ 10 phút.
- CẦN JOSEPH: bấm “Lưu / Chia sẻ” trên Safari thật và gửi lại ĐÚNG DÒNG LỖI nếu có.

## Còn chờ Joseph kiểm chứng trên Safari thật
- Google Translate trên mạng iPhone (batch + CORS) — đo thời gian thật với file 1000 chuỗi.
- Hàng đợi dịch bền: tắt tab giữa chừng rồi mở lại, bấm “Tiếp tục dịch”.
- Nút “Lưu / Chia sẻ” (gửi lại đúng dòng lỗi nếu có).
