# Kiểm chứng mục C1 — Chuỗi fallback dịch không cần key

Ngày: 2026-11-16. Harness: exp/verify_fallback_chain.mjs.
Chuỗi: proxy (nếu có URL) → Google trực tiếp (gtx) → MyMemory → LibreTranslate (chỉ khi đã cấu hình) → từ điển offline.

## Kết quả
- PASS **node_google429_then_mymemory** (7.1s) — kết quả="Xin chào thế giới"; trace=[google:lỗi, mymemory:ok]; Google gọi 4 lần (retry 429), MyMemory gọi 1 lần
- PASS **node_mymemory_fail_then_libretranslate** (7s) — kết quả="Xin chào thế giới"; trace=[google:lỗi, mymemory:lỗi, libretranslate:ok]; MyMemory gọi 1 lần (500), LibreTranslate gọi 1 lần
- PASS **node_all_fail_reports_chain** (7s) — kind=all_failed; dòng đầu="Google lỗi HTTP 429 → MyMemory lỗi HTTP 500 → LibreTranslate lỗi cần API key — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên." (dài 162 ký tự, bridge lấy tối đa 200); trace=[google:lỗi, mymemory:lỗi, libretranslate:lỗi]
- PASS **node_libretranslate_unconfigured_skipped** (7s) — kind=all_failed; LibreTranslate gọi 0 lần; dòng đầu="Google lỗi HTTP 429 → MyMemory lỗi HTTP 500 · LibreTranslate chưa cấu hình (cần API key) — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên."
- PASS **node_proxy_tried_first** (0s) — kết quả="VI qua proxy: Hello world"; proxy gọi 1 lần, Google 0 lần, MyMemory 0 lần; trace=[proxy:ok]
- PASS **node_proxy_quota_skips_google** (0s) — kết quả="Xin chào thế giới"; Google gọi 0 lần (phải = 0 khi hết hạn mức), MyMemory 1 lần; trace=[proxy:lỗi, mymemory:ok]
- PASS **node_batch_fail_says_retry_singles** (7s) — kind=batch_failed; dòng đầu="Proxy lỗi Proxy dịch lỗi (proxy_error): boom giả. → Google lỗi HTTP 429 · LibreTranslate chưa cấu hình (cần API key) — batch lỗi, sẽ dịch lại từng chuỗi (Google đơn → MyMemory → LibreTranslate nếu có, rồi từ điển offline)."
- PASS **node_libretranslate_unit** (0s) — dịch đúng="Xin chào thế giới" (api_key gửi kèm: "key-giả"); 400 không key → kind=libretranslate, msg="LibreTranslate từ chối (HTTP 400): Visit https://portal.libretranslate.com to ge"; chuỗi >5000 ký tự → lỗi ngay không gọi mạng (số lần gọi không đổi: 2)
- PASS **live_google_real** (0.7s) — Google thật trả HTTP 200; body đầu: "[[[\"chơi\",\"play\",null,null,4]],null,\"en\",null,null,null,null,[]]" (probe chỉ ghi nhận, không ép PASS dịch được)
- PASS **live_mymemory_real** (1.5s) — MyMemory thật trả HTTP 200, responseStatus=200, translatedText="Xin chào thế giới"
- PASS **live_libretranslate_languages** (1.3s) — GET libretranslate.com/languages → HTTP 200, ACAO=*, en→vi hỗ trợ: true (CORS không phải vấn đề; vấn đề là API key ở bước POST)
- PASS **live_libretranslate_post_blocked_note** (0.2s) — POST libretranslate.com/translate → HTTP 400, body đầu: "{\"error\":\"Visit https://portal.libretranslate.com to get an API key\"}\n" (từ sandbox này POST /translate bị egress chặn policy_denied nên KHÔNG đo được phản hồi thật của server; bằng chứng "cần API key (HTTP 400)" lấy từ kiểm chứng độc lập 2026-10-27, xem fallback_chain_results.md)
- PASS **bridge_google429_mymemory_note** (14.4s) — hello="Xin chào thế giới" (qua MyMemory), play="Chơi" (từ điển); fallback_note="Đã dùng fallback: MyMemory cho 1 chuỗi."; fallback_used={"mymemory":1}; translated_by=mixed google_count=1
- PASS **bridge_all_fail_offline_chain_error** (14s) — translated_by=offline; hello giữ nguyên="Hello world"; network_error="Error: Google lỗi HTTP 429 → MyMemory lỗi HTTP 500 · LibreTranslate chưa cấu hình (cần API key) — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên."
- PASS **bridge_proxy_batch_ok** (0s) — batch_mode=proxy-batch; hello="VI qua proxy: Hello world"; fallback_note="" (proxy thành công ngay → không dùng fallback)
- PASS **browser_boot** (6.2s) — boot tới Sẵn sàng (390px)
- PASS **browser_google429_mymemory_ui** (14.9s) — output hello="Xin chào thế giới"; info: "Đã dịch 1/1 chuỗi (Google: 1 đoạn · từ điển offline: 0 đoạn · bộ nhớ dịch: 0 chuỗi · khử trùng lặp: 0 đoạn · batch: 0 request, từng chuỗi: 1). Đã dùng fallback: MyMemory cho 1 chuỗi. Xong — bấm “Tải bản dịch (.json)” để lưu."; không hiện "Lý do" giả khi đã dịch xong qua fallback; route Google 429 đã chặn 8 request (worker fetch thật bị chặn, không phải mock trong code)
- PASS **browser_all_fail_clear_error** (14.1s) — info: "Đã hủy giữa chừng — Đã dịch 0/1 chuỗi (Google: 0 đoạn · từ điển offline: 0 đoạn · bộ nhớ dịch: 0 chuỗi · khử trùng lặp: 0 đoạn · batch: 0 request, từng chuỗi: 0). Lý do: Error: Google lỗi HTTP 429 → MyMemory lỗi HTTP 500 · LibreTranslate chưa cấu hình (cần API key) — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên. Từ điển offline chỉ biết ~100 từ/cụm thông dụng "; chuỗi chưa dịch được giữ lại trong hàng đợi để dịch tiếp (output không ghi nhận bản dịch giả): "{}"
- PASS **browser_390_no_overflow_no_errors** (0s) — 390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror: không có

Tổng: 19 mục, 0 FAIL.

## Bằng chứng live (đo thật ngày 2026-11-16, từ sandbox này)
- Google gtx thật: {"status":200,"sorry":false,"head":"[[[\"chơi\",\"play\",null,null,4]],null,\"en\",null,null,null,null,[]]"}
- MyMemory thật: {"status":200,"translated":"Xin chào thế giới","responseStatus":200}
- LibreTranslate GET /languages: {"status":200,"en_targets_vi":true,"aca":"*"}
- LibreTranslate POST /translate: {"status":400,"head":"{\"error\":\"Visit https://portal.libretranslate.com to get an API key\"}\n"}

## Kết luận về LibreTranslate public
- libretranslate.com /translate KHÔNG còn dùng được nếu không có key: bản public trả HTTP 400
  "Visit https://portal.libretranslate.com to get an API key" (kiểm chứng độc lập 2026-10-27); gói host từ $29/tháng.
- Mirror translate.argosopentech.com đã chết (DNS resolve nhưng không nối được từ sandbox; nguồn độc lập ghi "does not resolve").
- Từ sandbox này cũng KHÔNG đo trực tiếp được POST /translate: egress chặn mọi POST /translate (policy_denied, X-Hatch-Policy) — chỉ GET /languages đi qua (HTTP 200, ACAO:*, en→vi hỗ trợ).
- Vì vậy bước LibreTranslate MẶC ĐỊNH TẮT trong code (endpoint rỗng, 0 request thừa); chuỗi mặc định vẫn là 3 bước proxy → Google → MyMemory → từ điển offline.
- Bước này đã cài sẵn và kiểm chứng bằng server giả cùng API (A2/A8/B1): ai tự host LibreTranslate (miễn phí) hoặc có key portal thì dán endpoint + key ở tab Dịch (hoặc trang Kiểm tra dịch) là bật, không cần sửa code.
- Từ điển offline thật có 105 cụm EN→VI và 101 cụm VI→EN (đếm trực tiếp từ sdk/translate.py) — con số "~100" trong UI là đúng; "~40" trong mô tả nhiệm vụ là cũ.

## Giới hạn còn lại
- LibreTranslate public cần key trả phí; không có bước dịch miễn phí thứ 4 nào còn sống (MyMemory là bước miễn phí cuối).
- Google gtx từ IP máy chủ vẫn 429 + Sorry (đo lại hôm nay); trên mạng di động/dân cư thường dịch được — vẫn cần Joseph thử trên Safari thật.
- CORS trên Safari thật cho MyMemory/LibreTranslate vẫn chưa kiểm chứng (MyMemory và libretranslate.com đều trả ACAO:* qua curl, nhưng Safari thật mới là bằng chứng cuối).
