# Mục 2 — Proxy dịch Cloudflare Worker + Trang Kiểm tra dịch (2026-11-16)

## Trạng thái

- **Code + kiểm chứng: XONG.** Harness `exp/verify_item2.mjs`: **17/17 PASS**.
- **CHƯA ĐẨY lên GitHub.** Đang chờ parent báo head mới (hotfix 4 lỗi
  Safari đang sửa trực tiếp trên `app.js`, `worker.js`, `index.html`,
  `bridge.py`). Xem “Merge checklist” bên dưới — KHÔNG đẩy đè bằng bản
  file cũ.
- **Triển khai thật: BLOCKED, chờ Joseph** — cần tài khoản Cloudflare +
  API key Google của anh (xem `exp/cloudflare_setup.md` +
  `cloudflare/README.md`). Muse không tự tạo tài khoản hay key.

## 1. Đã kiểm tra: chưa có code Cloudflare Worker nào

Tìm trong toàn bộ `~/workspace/pb3_web/`: chỉ có nhắc trong
`exp/phase1_ci_results.md` và `upgrade_in/web-upgrade/README.md` (“dựng
một Cloudflare Worker làm proxy” như bước tương lai). Không có
`wrangler.toml`, không có code worker → **viết mới toàn bộ**.

## 2. File mới (đường dẫn repo = đường dẫn local trừ tiền tố `site/`)

| File repo | File local | Nội dung |
|---|---|---|
| `cloudflare/worker.js` | `site/cloudflare/worker.js` | Worker proxy (330 dòng). POST `{text, target, source?}` → Google Cloud Translation API v2 bằng key trong secret env. |
| `cloudflare/wrangler.toml` | `site/cloudflare/wrangler.toml` | Cấu hình deploy; KV id là placeholder `REPLACE_WITH_KV_NAMESPACE_ID`. |
| `cloudflare/README.md` | `site/cloudflare/README.md` | Hướng dẫn từng bước cho Joseph (Cloudflare → key Google → wrangler → deploy → dán URL vào tab Dịch). |
| `diagnostics.html` | `site/diagnostics.html` | Trang Kiểm tra dịch (tự chứa, không import `translate.js` vì file đó chưa có trong `site/`). |
| `exp/cloudflare_setup.md` | `exp/cloudflare_setup.md` | Hướng dẫn ngắn + trạng thái chờ Joseph. |
| `exp/verify_item2.mjs` | `exp/verify_item2.mjs` | Harness kiểm chứng (17 check). |
| `exp/item2_results.md` (+ `.json`) | — | Báo cáo này. |
| `exp/item9_results.md` | — | Đánh giá mục 9. |

## 3. Worker proxy — cơ chế an toàn phí (khóa trong code)

- **Hạn mức cứng 500.000 ký tự/tháng** (đúng phần miễn phí của Google):
  đếm trong KV theo tháng UTC (`usage:YYYY-MM`). Vượt thì trả HTTP 429
  `{code:"quota_exceeded"}` — client rơi về MyMemory + offline.
- **Đặt trước + hoàn lại:** ký tự được cộng vào KV *trước* khi gọi
  Google; Google lỗi thì trừ lại → hạn mức không bị vượt vì lỗi.
- **Cache KV:** key = SHA-256(text + source + target + glossary_version);
  trúng cache thì không gọi Google, không tính ký tự (TTL 30 ngày).
- **Giới hạn IP:** 60 request/phút/IP (KV `rl:<ip>:<phút>`).
- **Ngắt mạch:** Google lỗi liên tiếp 5 lần → mở mạch 60 giây, trả
  `{code:"breaker_open"}` thay vì gọi vô ích.
- **Retry-After + backoff:** 429/5xx thử lại tối đa 2 lần, tôn trọng
  header Retry-After (tối đa 10 giây) + backoff 1s/2s; timeout 15 giây.
- **Chưa cấu hình thì từ chối dịch:** thiếu `GOOGLE_API_KEY` → 503
  `not_configured`; thiếu KV → 503 `kv_not_configured` (không dịch
  “chay” ngoài hạn mức).
- **Không key trong code/repo:** key chỉ vào bằng `wrangler secret put
  GOOGLE_API_KEY`. `wrangler.toml` không chứa id thật.
- GET `/` trả thông tin worker (kiểm tra deploy, không tốn ký tự).
- Lưu ý trung thực (ghi trong README): KV không có phép tăng nguyên tử
  nên 2 request cùng lúc có thể vượt hạn mức vài trăm ký tự — không đáng
  kể so với 500.000, và Google tính phí theo ký tự *thực tế đã dịch*.

## 4. Trang Kiểm tra dịch (`diagnostics.html`)

- **Môi trường:** UA, WASM, module worker, IndexedDB, RAM/cores, quota.
- **Google trực tiếp:** fetch gtx “Hello world” en→vi, hiện status/ms/
  bản dịch hoặc lỗi (429/Sorry/CORS/timeout) — phân biệt được.
- **MyMemory trực tiếp:** tương tự.
- **Proxy:** nhập URL worker, POST `{text, source, target}`, hiện
  `ok/provider/chars/cached/month_used/month_limit` hoặc `code` lỗi.
- **Sao chép kết quả** (clipboard + fallback chọn tay); **Lưu URL proxy**
  vào localStorage để tab Dịch dùng luôn; link về `index.html`.

## 5. Nối app.js / gt.js / worker.js

Chuỗi fallback mới (trong `gt.js:translateOneWithFallbacks`, worker.js
gán cho Python qua `globalThis.pb3_google_translate`, thay cho
`googleTranslateOne` trực tiếp):

```
proxy Cloudflare (nếu có URL) -> Google trực tiếp -> MyMemory -> ném lỗi
-> bridge dùng từ điển offline cho chuỗi đó
```

Riêng proxy báo `quota_exceeded` thì **bỏ qua Google trực tiếp**
(đúng luật: hết hạn mức tháng thì chỉ còn MyMemory + offline).

- `gt.js`: thêm `setTranslateProxyUrl` / `proxyTranslateOne`
  (timeout 20s, `quota` vs `proxy` kind riêng) / `myMemoryTranslateOne`
  (tối đa 500 ký tự/request, timeout 10s, `setMyMemoryEndpoint` chỉ để
  harness test) / `translateOneWithFallbacks`. `googleTranslateOne`
  **không đổi** (harness cũ vẫn dùng được).
- `worker.js` (site): import `translateOneWithFallbacks` +
  `setTranslateProxyUrl`; thêm op `set_translate_proxy`.
- `app.js`: ô `#translate-proxy` ở tab Dịch — đọc/ghi localStorage
  `pb3_translate_proxy`, gửi sang worker ngay khi tải trang và mỗi khi
  đổi. Để trống = gọi trực tiếp như trước.
- `index.html`: ô nhập URL proxy + ghi chú + link `diagnostics.html`
  trong tab Dịch.
- `site/README.md`: cập nhật mục Dịch + thêm mục proxy + mục asset nhị
  phân (mục 9).
- `bridge.py`: **không cần sửa** — vẫn gọi `js.pb3_google_translate`
  như cũ; `network_error` giờ có thể mang kind `quota`/`proxy`/
  `mymemory` và được hiện ở tab Dịch như trước.

## 6. Kiểm chứng — `exp/verify_item2.mjs`: 17/17 PASS (~6 giây)

Không mạng thật, không tài khoản Cloudflare, không key Google: worker
chạy thật với KV giả (Map) + fetch giả; gt.js chạy thật với proxy/
MyMemory giả là server HTTP local.

| # | Check | Kết quả |
|---|---|---|
| 1 | `worker_get_health` | GET / → 200, service + monthly_limit 500000 |
| 2 | `worker_translate_ok` | POST “Hello world” → ok, provider google, chars 11, month_used 11, KV usage=11, Google gọi 1 lần |
| 3 | `worker_cache_hit` | Gọi lại → cached=true, month_used vẫn 11, Google vẫn 1 lần |
| 4 | `worker_quota_hard_cap` | usage 499995 + 10 ký tự → 429 quota_exceeded, Google 0 lần, usage không đổi |
| 5 | `worker_quota_boundary` | 499990 + 10 → 200 (đúng 500000); +1 ký tự → 429 quota_exceeded |
| 6 | `worker_circuit_breaker` | Google 429 liên tiếp 2 lần (threshold test = 2) → lần 3 breaker_open, không gọi Google; ký tự đã đặt trước được hoàn lại |
| 7 | `worker_rate_limit_ip` | Giới hạn 3/phút: 4 request cùng IP → 200,200,200,429 rate_limited |
| 8 | `worker_validation_config` | Thiếu text / text 5001 ký tự / target sai → 400; PUT → 405; thiếu key → 503 not_configured; thiếu KV → 503 kv_not_configured |
| 9 | `gt_proxy_path` | Có proxy → dịch qua proxy 1 lần, Google trực tiếp 0 lần |
| 10 | `gt_quota_falls_to_mymemory` | Proxy quota_exceeded → MyMemory, Google trực tiếp 0 lần (không vòng lại Google) |
| 11 | `gt_proxy_down_falls_to_direct` | Proxy chết (ECONNREFUSED) → Google trực tiếp |
| 12 | `gt_all_fail_throws_for_offline` | Google Sorry + MyMemory 500 → ném kind=mymemory (bridge dùng offline) |
| 13 | `gt_helpers_direct` | `proxyTranslateOne` / `myMemoryTranslateOne` trả đúng |
| 14 | `static_cloudflare_files` | Đủ 3 file; KV id placeholder; không chuỗi AIza; worker có quota/cache/breaker/Retry-After |
| 15 | `static_diagnostics_page` | Đủ 3 kiểm tra + sao chép + lưu proxy + link về index; không import translate.js |
| 16 | `static_wiring` | Đủ nối gt/worker/app/index; ghi chú mục 9 có 4 chỗ trong index.html |
| 17 | `static_no_coi_no_key` | Không COI (Cross-Origin-Opener/Embedder-Policy, coi-serviceworker), không key AIza trong 8 file mục 2+9 |

Chi tiết đầy đủ: `exp/item2_results.json`.

## 7. Hồi quy cũ

- `verify_translate_web.mjs`: **8/10 — đúng như trước khi sửa.**
  2 FAIL là do Google live đổi output: `googleTranslateOne("play")`
  giờ trả `"chơi"` (chữ thường) trong khi harness assert `"Chơi"`.
  `googleTranslateOne` không bị sửa trong đợt này; đây là độ nhạy của
  harness với output live của Google (trước đây 2 FAIL này là do
  429+Sorry từ IP máy chủ). Không phải regression.
- `verify_module_worker.mjs`: **16/17 — 1 FAIL đã biết nguyên nhân,
  KHÔNG phải lỗi sản phẩm:** check `module_worker_static` cấm mọi chuỗi
  `https://` trong worker.js+app.js+index.html (heuristic “không dùng
  CDN”), và placeholder tôi thêm ở `index.html:128` chứa
  `https://beartea-translate-proxy.<tên>.workers.dev` (chỉ là chữ ví dụ
  trong ô nhập, không phải URL được fetch). **Fix đã xếp vào merge:**
  bỏ scheme khỏi placeholder → `beartea-translate-proxy.<tên>.workers.dev`.
  (Không sửa ngay vì index.html đang bị freeze chờ hotfix.)
- Chưa chạy lại: `verify_clone_fix` (10/10), `verify_patch01_web`
  (8/8), `verify_parity` (14/14), `verify_limit_2gb` (6/6),
  `verify_phase1_ci` (8/8) — sẽ chạy lại sau khi merge head mới, vì
  parent đang sửa `app.js`/`worker.js`/`index.html`/`bridge.py` song
  song, chạy lúc này có thể cho kết quả sai do file đang sửa dở.
- `node --check` xanh: `gt.js`, `worker.js`, `app.js`,
  `cloudflare/worker.js`.

## 8. Merge checklist — LÀM TRƯỚC KHI ĐẨY (khi parent báo head mới)

1. `gh.py read` bản mới nhất trên main của mọi file chung đã sửa:
   `app.js`, `worker.js`, `index.html`, `gt.js`, `README.md`
   (repo root = local `site/`).
2. Áp lại thay đổi của mục 2 lên trên bản đó (thay đổi nhỏ, liệt kê
   chính xác để áp lại):
   - `gt.js`: chèn 1 khối sau dòng `GT_BACKOFF_MS` (proxy + MyMemory +
     `translateOneWithFallbacks`, ~120 dòng); không sửa `googleTranslateOne`.
   - `worker.js`: đổi import `googleTranslateOne` →
     `setTranslateProxyUrl, translateOneWithFallbacks`; đổi
     `globalThis.pb3_google_translate = googleTranslateOne` →
     `= translateOneWithFallbacks` (+ comment); thêm op
     `set_translate_proxy` sau op `cancel_translate`.
   - `app.js`: chèn 1 khối ở đầu mục Dịch (`sendProxyUrlToWorker` +
     prefill + listener `#translate-proxy`).
   - `index.html`: tab Dịch (ô `#translate-proxy` + ghi chú + link
     diagnostics.html); thêm câu mục 9 vào tab Unity / Mod / Chẩn đoán;
     2 bullet giới hạn (proxy + asset nhị phân). **Đồng thời sửa
     placeholder thành không có scheme** (mục 7) để
     `module_worker_static` xanh lại.
   - `README.md`: mục Dịch + mục proxy mới + mục asset nhị phân + 1
     dòng bảng so sánh.
3. File mới, không xung đột (parent không đụng): `cloudflare/worker.js`,
   `cloudflare/wrangler.toml`, `cloudflare/README.md`,
   `diagnostics.html`, `exp/verify_item2.mjs`, `exp/item2_results.*`,
   `exp/item9_results.md`, `exp/cloudflare_setup.md`.
4. Chạy lại: `verify_item2.mjs` (phải 17/17) + `verify_module_worker`
   (phải 17/17 sau fix placeholder) + `verify_clone_fix` (10/10) +
   `verify_patch01_web` (8/8) + `verify_parity` (14/14) +
   `verify_limit_2gb` (6/6) + `verify_phase1_ci` (8/8) +
   `verify_translate_web` (8/10 như trước, 2 fail live-Google).
5. Đẩy bằng `gh.py push-tree` với manifest (file `.github/workflows/`
   KHÔNG đụng trong đợt này nên không lo 403). Sau đó kiểm chứng bằng
   `gh.py commits` + `gh.py read` trên main + curl trang live (200).

## 9. Còn chờ Joseph (sau khi code lên main)

1. Tài khoản Cloudflare + API key Google — làm theo
   `cloudflare/README.md` (đã có hướng dẫn từng bước, ~15 phút).
   Deploy xong, dán URL worker vào ô **URL proxy dịch** ở tab Dịch.
2. Mở **trang Kiểm tra dịch** (`diagnostics.html`, link trong tab Dịch)
   trên Safari iPhone thật, bấm 4 nút kiểm tra, gửi lại kết quả — đây
   mới là bằng chứng dịch Google/proxy chạy được trên máy thật (VM này
   không chứng minh được: IP máy chủ từng bị Google 429+Sorry).
