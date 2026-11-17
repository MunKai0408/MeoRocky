# Kết quả kiểm chứng T1 (Cloudflare Worker + key Google + Workers AI)

Chạy: `node verify_worker_t1.mjs` (không cần mạng thật, key thật hay tài khoản Cloudflare).
Worker Cloudflare import thật + KV giả + Google stub (trả `VI: ` + text gốc) + AI giả (trả `VI-AI: ` + text gốc).
Chất lượng dùng chuỗi 9K thật + gate check_placeholders thật — kết quả là [FIXTURE] (mock, không đo được chất lượng model thật).

| Kiểm tra | Kết quả | Chi tiết |
|---|---|---|
| `status_ok` | PASS | GET http://127.0.0.1:37429/w-ok -> {"state":"ok","provider":"google","ai_daily_used":null,"ai_daily_limit":10000,"monthly_limit":500000,"version":3} (phải state=ok, monthly_limit=500000, provider=google, version=3) |
| `status_provider_workers_ai` | PASS | GET http://127.0.0.1:37429/w-ai -> {"state":"ok","provider":"workers-ai","ai_daily_used":0,"ai_daily_limit":10000,"monthly_limit":500000,"version":3} (phải state=ok, provider=workers-ai) |
| `status_not_configured` | PASS | GET /w-nokey -> {"state":"not_configured"} (phải state=not_configured) |
| `status_kv_missing` | PASS | GET /w-nokv -> {"state":"kv_missing"} (phải state=kv_missing) |
| `status_unreachable` | PASS | GET cổng đóng -> {"state":"unreachable","reason":"network"} (phải state=unreachable) |
| `status_not_set` | PASS | URL rỗng -> {"state":"not_set"}; không truyền URL -> {"state":"not_set"} |
| `status_normalize_url` | PASS | gõ trơn -> "https://beartea-translate-proxy.x.workers.dev"; có scheme + / cuối -> "https://beartea-translate-proxy.x.workers.dev" (phải cùng = https://beartea-translate-proxy.x.workers.dev) |
| `worker_batch_sample_placeholders` | PASS | mode=proxy-batch (phải proxy-batch) | translations=["VI: Play","VI: Hello {VAR1}, you have {0} new messages","VI: Press {KINGCOLOR}Start{/KINGCOLOR} to continue","VI: Line one\nLine two","VI: You earned %s gold"] | placeholder từng chuỗi khớp gốc: true | googleCalls=1 (phải = 1: 1 batch request) |
| `worker_batch_cache` | PASS | batch lần 2 -> translations đúng | googleCalls=1 (phải = 1, không tăng: worker trả từ KV cache) |
| `worker_batch_sample_file` | PASS | keys giữ nguyên: true | placeholder còn nguyên: true | mẫu: "VI: Hello {VAR1}, you have {0} new messages" |
| `worker_proxy_batch_direct` | PASS | proxyTranslateBatch 1 chuỗi -> ["VI: Hello world"] |
| `fallback_no_proxy_google` | PASS | không proxy -> gtx giả -> "VI: Hello world" (phải = 'VI: Hello world') |
| `fallback_proxy_down_google` | PASS | proxy chết -> gtx giả -> "VI: Hello world" |
| `fallback_proxy_quota_mymemory` | PASS | proxy quota_exceeded -> "Xin chào (MyMemory)" | googleCalls=0 (phải = 0: hết hạn mức thì không vòng lại Google) |
| `ai_provider_auto_no_key` | PASS | POST env AI-only -> {"ok":true,"translated":"VI-AI: Hello world","provider":"workers-ai","chars":11,"cached":false,"month_used":11,"month_limit":500000,"ai_daily_used":0.16995,"ai_daily_limit":10000} | aiCalls=1 (phải = 1) | googleCalls=0 (phải = 0) |
| `ai_provider_google_preferred` | PASS | POST env key+AI -> provider=google (phải google) | aiCalls không tăng: 0 (phải = 0) |
| `ai_provider_explicit_workers_ai` | PASS | POST TRANSLATE_PROVIDER=workers-ai (có key) -> provider=workers-ai (phải workers-ai) |
| `ai_provider_explicit_google_no_key` | PASS | POST TRANSLATE_PROVIDER=google (không key) -> status=503, code=not_configured (phải 503 not_configured) |
| `ai_provider_none` | PASS | POST không key, không AI -> status=503, code=not_configured (phải 503 not_configured) |
| `ai_get_health_provider` | PASS | GET AI-only -> config=ok, provider=workers-ai, version=3, ai_daily_limit=10000 | GET key+AI -> provider=google | GET không gì -> config=not_configured, provider=null |
| `ai_cache_separate` | PASS | Google lần 1 -> "VI: Hello world" | AI lần 1 -> "VI-AI: Hello world" (phải khác nhau: cache không lẫn) | lần 2 cả hai cached=true | googleCalls tăng 1 (2), aiCalls=1 (1) |
| `ai_batch_one_by_one` | PASS | batch 3 chuỗi -> ["VI-AI: One","VI-AI: Two","VI-AI: Three"] | aiCalls=3 (phải = 3: từng chuỗi một) | googleCalls không tăng | model=@cf/meta/m2m100-1.2b (phải @cf/meta/m2m100-1.2b), source_lang=en, target_lang=vi |
| `ai_daily_quota` | PASS | seed ai-usage hôm nay = 9000 -> status=429, code=quota_exceeded (phải 429 quota_exceeded) | aiCalls=0 (phải = 0: không gọi AI) | month_used không tăng: null |
| `ai_daily_quota_cloudflare_4006` | PASS | AI ném 4006 -> status=429, code=quota_exceeded (phải 429 quota_exceeded) | aiCalls=1 (phải = 1: không thử lại) |
| `ai_error_refund` | PASS | AI lỗi -> status=502, code=ai_error (phải 502 ai_error) | month_used sau lỗi: 0 (phải 0/null: đã hoàn lại) | ai-usage hôm nay: 0 (phải 0/null: đã hoàn lại) |
| `quality_ai_vs_google_mock` | PASS | [FIXTURE] 10 chuỗi 9K thật: Google mock gate=pass (checked=10, failed=0) | Workers AI mock gate=pass (checked=10, failed=0) | Google: 1 batch request, AI: 10 lần gọi 1 chuỗi | KẾT LUẬN TRUNG THỰC: mock chỉ thêm tiền tố nên 2 gate đều PASS — chứng minh pipeline (đủ chuỗi, đúng thứ tự, placeholder còn nguyên), KHÔNG chứng minh chất lượng dịch thật của m2m100 so với Google; chất lượng thật chỉ đo được trên bản deploy thật. |
| `ai_quirk_documented` | PASS | [FIXTURE] mock quirk (nuốt câu sau khi gặp đoạn nhiều câu): 8 chuỗi 1 câu qua worker -> đầy đủ: true (worker gửi từng chuỗi một nên không mất câu) | 2 chuỗi đơn lẻ có 2 câu -> mất câu sau: 2/2 | gate trên output mất câu: fail (failed=2) — ĐÂY LÀ GIỚI HẠN ĐÃ BIẾT của m2m100: 1 chuỗi game có nhiều câu vẫn có thể mất câu; cổng kiểm tra bước 6 bắt được trường hợp này trước khi ép file. |
| `static_docs_api_key_setup` | PASS | docs/API_KEY_SETUP.md có đủ: tạo key Google + restrict key, KV, secret, deploy, kiểm tra worker, bảng lỗi quota/not_configured, lưu ý Billing required; đường Workers AI (model m2m100-1.2b, TRANSLATE_PROVIDER, 10.000 neurons/ngày, không cần thẻ); không chuỗi AIza (key thật). |
| `static_wiring_t1` | PASS | thiếu: không có | gt.js có checkProxyStatus + normalizeProxyUrl + provider; worker.js có op check_translate_proxy; app.js có nút + hàm trạng thái + nhãn Workers AI; index.html có nút + dòng trạng thái + trỏ docs/API_KEY_SETUP.md; cloudflare/worker.js có m2m100 + resolveProvider + callWorkersAI + translateBatch + guard neurons; wrangler.toml có [ai] binding + TRANSLATE_PROVIDER. |
| `static_no_key_leak_t1` | PASS | file chứa key Google (AIza...): không có |

## Kết luận
TẤT CẢ XANH.
