# Kiểm chứng Phần C — Nối tab Dịch với Trích xuất

Ngày: 2026-11-16. Harness: exp/verify_translate_connect.mjs.
A: bridge thật trên Pyodide Node (mock Google + Google thật từ Node).
B: trình duyệt thật (Playwright chromium headless shell, 390px, serve site qua HTTP local + proxy dịch giả delay 250ms/request).

## Kết quả
- PASS **node_google_mock_success_progress** (0.6s) — translated_by=mixed google_count=3 glossary_count=2 play="Chơi" hello="VI[Hello]"; tiến trình translate: 4 event, cuối 5/5 (cần đủ 5 chuỗi)
- PASS **node_google_error_falls_back_offline** (0s) — network_error="Google Translate lỗi (network): Error: Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS)."; translated_by=offline (google_count=0, glossary_count=2); offline vẫn dịch play=Chơi quit=Thoát (không đứng im)
- PASS **node_google_cancel_keeps_partial** (0.1s) — cancelled=true google_count=2 (gọi 2 lần); đã dịch 2/5 chuỗi Google (giữ lại phần đã dịch), chuỗi chưa dịch giữ nguyên (welcome="Welcome to 9 Kings!")
- PASS **node_offline_1000_time** (0s) — dịch offline 1000 chuỗi xong trong 29ms (0s) — total=1000, untranslated=1000 (từ điển offline chỉ biết ~100 từ/cụm nên hầu hết giữ nguyên là ĐÚNG, không phải lỗi)
- PASS **node_live_google_real_result** (0.8s) — Google trả HTTP 200, JSON dịch được: [[["chơi","play",null,null,4]],null,"en",null,null,null,null,[]] — Google dịch được từ IP máy chủ
- PASS **browser_boot_proxy_set** (9.4s) — boot tới Sẵn sàng; proxy dịch giả đặt qua localStorage + ô nhập: http://127.0.0.1:43733/translate-proxy
- PASS **browser_extract_connects_translate** (1.1s) — download extract.zip; chế độ mặc định Tự động: true; locale mặc định: both; kết quả có dòng nguồn bundle: true; có nút "Dịch sang tiếng Việt": true; dropdown Dịch mặc định: English.json; ô dịch nạp sẵn en.json (Welcome to 9 Kings!): true; banner rỗng ẩn khi đã có nội dung: true
- PASS **browser_translate_button_loads_zh** (0.2s) — bấm nút Dịch trên Chinese.json -> tab Dịch mở, ô dịch chứa 你好, dropdown chuyển sang: Chinese.json
- PASS **browser_empty_banner** (0.6s) — banner: "Chưa có nội dung để dịch — hãy chọn file vừa trích xuất hoặc dán JSON vào ô."; bấm Dịch khi ô trống -> banner lỗi hiện: true
- PASS **browser_offline_5strings** (0.4s) — dịch offline 5 chuỗi xong; info: "Dịch bằng từ điển offline: 5 chuỗi. Chưa dịch được 3 chuỗi. Đã gắn credit 'Việt Hoá Bởi Bear' vào key: title."…; output có Chơi/Thoát
- PASS **browser_google_1000_estimate_and_cancel** (0.7s) — dialog ước tính: "File có 1000 chuỗi. Dịch Google theo batch 100 chuỗi/request — ước tính khoảng 15 giây (có thể lâu hơn nếu mạng chậm; nếu Google không nhận batch thì tool tự rơi về từng chuỗi, sẽ chậm hơn nhiều). Bấm OK để dịch tiếp, hoặc Hủy để dịch offline / chia nhỏ file." (cần "1000 chuỗi" + "ước tính khoảng" + "batch" + "15 giây"); tốc độ thật: 100 chuỗi/0.4s ≈ 277.92 chuỗi/giây -> 1000 chuỗi ≈ 3.6 giây; sau Hủy: 100/1000 chuỗi đã dịch (giữ lại phần đã dịch, không mất); tải phần đã dịch: translated.vi.json với 100 chuỗi VI:
- PASS **browser_390_no_overflow_no_errors** (0s) — 390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror: không có; bảng kết quả extract: {"has":true,"display":"block","overflowX":"auto"}

Tổng: 12 mục, 0 FAIL.

## Ghi chú
- Dịch offline 1000 chuỗi (Node): 29ms.
- Tốc độ Google đo được qua proxy giả (batch 100 chuỗi/request, delay 250ms/batch): 277.92 chuỗi/giây -> 1000 chuỗi ≈ 3.6 giây.
- Proxy dịch giả chỉ dùng cho harness (delay 250ms/request, trả "VI:" + text); Google thật từ IP máy chủ xem mục C5.

## File thay đổi (đợt A+B+C, CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)
- `site/pb3/sdk/extraction.py`, `site/pb3/sdk/exporters.py`, `site/pb3/bridge.py`, `site/index.html`, `site/app.js` (phần A+B+C).
- Đợt bổ sung (batch dịch + Share): `site/gt.js` (batch joined-text, token bucket, hủy fetch), `site/worker.js` (expose batch, abort khi hủy), `site/pb3/bridge.py` (dedupe + glossary-first + ưu tiên + batch + `apply_credit_only`), `site/cloudflare/worker.js` (proxy nhận `{texts:[...]}`, chưa deploy), `site/kb-store.js` (DB v2 + `TranslateJobStore`), `site/app.js` (chunk 500 key + hàng đợi bền + TM + lỗi Share hiện `err.name`), `site/index.html` (thanh resume, ghi chú batch).
- Fixture: `exp/fixtures/extract_select_fixture.zip`, `exp/fixtures/extract_broken_bundle.zip`, `exp/fixtures/translate_1000.json` (+ meta).
- Harness: `exp/verify_enjson.mjs`, `exp/verify_extract_select.mjs`, `exp/verify_translate_connect.mjs` (harness này), `exp/verify_translate_batch.mjs` (riêng mục batch).

## Nút Chia sẻ (mục B) — kết luận kiểm chứng
- Handler Share KHÔNG có await nào trước `navigator.share(...)` nên giả thuyết “mất user activation” có thể SAI với nút hiện tại — KHÔNG viết lại handler.
- Đã sửa: lỗi Share giờ hiện cả tên lỗi + message (`Chia sẻ lỗi: NotAllowedError: ...`), trước đây chỉ có message. Kiểm chứng bằng harness mock `navigator.share` ném lỗi (xem `verify_translate_batch.mjs`).
- Chia file >400–500MB thành nhiều volume: CHƯA LÀM — cần Joseph xác nhận có file nào >400MB thật không + test share nhiều file trên Safari thật trước; file lớn nhất từng thấy là out.tar.xz 95,6MB. URL blob vẫn giữ 10 phút (không revoke sớm).
- CẦN JOSEPH: bấm nút “Lưu / Chia sẻ” trên Safari thật và gửi lại ĐÚNG DÒNG LỖI nếu có — đó mới là bằng chứng; hành vi Share thật không chứng minh được bằng Node/Chromium.

## Batch dịch (mục A) — xem `translate_batch_results.md`
- gtx KHÔNG nhận nhiều q/request (chỉ dịch q đầu, kiểm chứng live); batch dùng joined-text 1 q với marker ⟦n⟧ (joined 10 -> 10/10, joined 50 -> 50/50, live). 429 là nhất thời, không phải chặn vĩnh viễn.
- Chi tiết: dedupe, glossary-first, ưu tiên, hàng đợi bền IndexedDB, TM từ job đã xong, token bucket + backoff — xem báo cáo riêng.

## C5 — Google thật từ IP máy chủ
- Xem chi tiết từng test `node_live_google_real_result` ở trên. IP máy chủ từng bị 429 + Sorry theo đợt gửi dồn (nhất thời, vài phút sau lại 200), và batch joined-text đã dịch được thật từ IP này (10/10, 50/50) — xem `translate_batch_results.md`.
- Joseph cần thử Google trên mạng iPhone/Safari thật (IP dân cư thường dịch được) và xem trang chẩn đoán `diagnostics.html`.

## Còn chờ Joseph kiểm chứng trên Safari thật
- Extract trên IPA thật 141MB phải ra en.json (fixture đã xanh, IPA thật chưa có trong sandbox).
- Nút “Lưu / Chia sẻ” trên Safari thật (gửi lại đúng dòng lỗi nếu có).
- Google Translate trên mạng iPhone (batch + CORS) + file picker trên IPA thật.
- Hàng đợi dịch bền: tắt tab giữa chừng rồi mở lại, bấm “Tiếp tục dịch”.
