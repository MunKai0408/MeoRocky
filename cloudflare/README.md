# BearTea translate proxy — Cloudflare Worker

Proxy dịch cho bản web PB3 (BearTea). Trình duyệt gọi trực tiếp Google
Translate hay bị chặn CORS / 429 theo IP; worker này đứng giữa, gọi Google
Cloud Translation API v2 bằng API key, có cache KV + **giới hạn cứng
500.000 ký tự/tháng (đúng phần miễn phí của Google) — hết hạn mức thì trả
tín hiệu để client tự rơi về MyMemory + từ điển offline, KHÔNG BAO GIỜ tự
phát sinh phí.**

## Cần chuẩn bị (Joseph làm, ~15 phút)

### 1. Tài khoản Cloudflare (miễn phí)

1. Mở https://dash.cloudflare.com/sign-up, đăng ký bằng email.
2. Không cần thêm domain, không cần mua gì — Workers Free cho
   100.000 request/ngày (đủ cho việc dịch này).

### 2. API key Google Cloud Translation (miễn phí 500.000 ký tự/tháng)

1. Mở https://console.cloud.google.com/ (đăng nhập tài khoản Google).
2. Tạo project mới (vd tên `beartea-translate`).
3. Vào **APIs & Services → Library**, tìm **Cloud Translation API**,
   bấm **Enable**.
4. Vào **APIs & Services → Credentials → Create Credentials → API key**,
   copy key. (Nên bấm **Restrict key** → chỉ cho **Cloud Translation API**.)
5. **KHÔNG dán key vào đâu trong repo, chat hay file nào** — key chỉ nhập
   1 lần bằng lệnh `wrangler secret put` ở bước 4.

### 3. Cài wrangler (máy tính, 1 lần)

```bash
npm install -g wrangler
wrangler login   # mở trình duyệt, đăng nhập Cloudflare, cho phép wrangler
```

### 4. Deploy worker

Trong thư mục `cloudflare/` của repo này:

```bash
# Tạo 2 KV namespace (cache bản dịch + đếm ký tự theo tháng)
wrangler kv namespace create TRANSLATE_CACHE
wrangler kv namespace create TRANSLATE_USAGE
# Mỗi lệnh in ra 1 id — chép 2 id thật vào wrangler.toml thay cho
# REPLACE_WITH_KV_NAMESPACE_ID (cả id và preview_id, 4 chỗ).

# Đặt API key Google làm secret (không lưu vào code/repo)
wrangler secret put GOOGLE_API_KEY
# dán key khi hỏi, Enter.

wrangler deploy
# In ra URL dạng: https://beartea-translate-proxy.<tên>.workers.dev
```

### 5. Nối vào bản web

1. Mở https://munkai0408.github.io/MeoRocky/ trên iPhone, vào tab **Dịch**.
2. Dán URL worker vào ô **URL proxy dịch** (được lưu trong trình duyệt).
3. Hoặc mở **trang Kiểm tra dịch** (link trong tab Dịch) để thử trước:
   dán URL proxy, bấm **Kiểm tra proxy** — phải thấy `ok: true`,
   `provider: "google"`, `month_used` tăng theo số ký tự đã dịch.

## Cách hạn mức cứng hoạt động (đã kiểm chứng bằng harness, xem exp/item2_results.md)

| Tình huống | Worker trả | Client làm gì |
|---|---|---|
| Dịch bình thường | `ok:true, provider:"google", month_used, month_limit` | Hiện bản dịch |
| Trùng chuỗi đã dịch | `ok:true, cached:true, chars` (không tính ký tự) | Hiện bản dịch |
| Vượt 500.000 ký tự/tháng | `ok:false, code:"quota_exceeded"` (HTTP 429) | Thử MyMemory (+ LibreTranslate nếu đã cấu hình), rồi từ điển offline |
| Google 429/5xx liên tiếp 5 lần | `ok:false, code:"breaker_open"` (HTTP 429) | Thử MyMemory (+ LibreTranslate nếu đã cấu hình), rồi offline |
| 1 IP gửi >60 request/phút | `ok:false, code:"rate_limited"` (HTTP 429) | Thử lại sau, rồi MyMemory (+ LibreTranslate nếu đã cấu hình) + offline |
| Chưa đặt GOOGLE_API_KEY / KV | `ok:false, code:"not_configured"/"kv_not_configured"` (HTTP 503) | Dịch trực tiếp Google như cũ, rồi MyMemory + offline |

Lưu ý trung thực:

- Chuỗi fallback đầy đủ (mục C1): proxy → Google trực tiếp → MyMemory →
  LibreTranslate (chỉ khi đã cấu hình endpoint + key ở tab Dịch, mặc định
  tắt vì bản public libretranslate.com cần API key trả phí) → từ điển
  offline (~100 từ/cụm). Mỗi bước đều ghi dấu vết để tab Dịch báo rõ
  bước nào đã dùng; cả chuỗi đều lỗi thì báo rõ lý do từng bước.
- KV không có phép tăng nguyên tử nên 2 request cùng lúc có thể vượt hạn
  mức vài trăm ký tự (không đáng kể so với 500.000, và Google tính phí
  theo ký tự *thực tế đã dịch* — vượt vài trăm ký tự vẫn nằm trong phần
  miễn phí nếu tháng đó chưa dùng hết; worker đặt trước ký tự trước khi
  gọi Google nên không vượt nhiều).
- Hạn mức tính theo tháng UTC (`usage:YYYY-MM`); sang tháng mới tự reset.
- URL worker là công khai (ai biết URL cũng gọi được) nhưng đã có giới hạn
  theo IP + hạn mức tháng cứng nên không ai làm bạn tốn phí được.
- Cloudflare Workers Free: 100.000 request/ngày — dịch 1 chuỗi/request
  như PB3 thì đủ cho ~100.000 chuỗi/ngày, vượt xa nhu cầu.

## File trong thư mục này

- `worker.js` — code worker (duy nhất cần deploy).
- `wrangler.toml` — cấu hình (KV id là placeholder, xem bước 4).
- `README.md` — file này.
