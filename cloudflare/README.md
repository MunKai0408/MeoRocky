# BearTea translate proxy — Cloudflare Worker

Proxy dịch cho bản web PB3 (BearTea). Trình duyệt gọi trực tiếp Google
Translate hay bị chặn CORS / 429 theo IP; worker này đứng giữa, dịch bằng
1 trong 2 provider — **Workers AI (miễn phí, không cần thẻ)** hoặc **Google
Cloud Translation API v2 (cần key)** — có cache KV + **giới hạn cứng
500.000 ký tự/tháng — hết hạn mức thì trả tín hiệu để client tự rơi về
MyMemory + từ điển offline, KHÔNG BAO GIỜ tự phát sinh phí.**

Provider chọn bằng biến `TRANSLATE_PROVIDER` trong `wrangler.toml`
(`"auto"` mặc định | `"google"` | `"workers-ai"`):
- `auto` = dùng Google khi secret `GOOGLE_API_KEY` đã đặt, nếu không thì
  dùng Workers AI khi có binding AI, nếu không có cả hai thì trả
  `not_configured` (503).
- `workers-ai` = Cloudflare Workers AI, model `@cf/meta/m2m100-1.2b`
  (dịch, 100 ngôn ngữ, có vi) qua binding `[ai]` — chỉ cần tài khoản
  Cloudflare miễn phí (email, không cần thẻ, không cần key Google, không
  cần billing). Miễn phí 10.000 neurons/ngày (tài khoản); worker tự dừng
  ở ngưỡng ước tính 9.000 neurons/ngày (`AI_DAILY_NEURON_GUARD`) nên
  không bao giờ phát sinh phí. Lưu ý: model nhỏ hơn Google, chất lượng có
  thể thấp hơn ở câu dài/câu thành ngữ; đoạn dài nhiều câu trong 1 lần gọi
  có thể bị nuốt mất câu nên worker luôn gửi từng chuỗi một cho Workers AI.
- `google` = Google Cloud Translation API v2 (miễn phí 500.000 ký
  tự/tháng, cần gắn billing account để bật API — xem
  docs/API_KEY_SETUP.md).

## Cần chuẩn bị (Joseph làm, ~15 phút)

### 1. Tài khoản Cloudflare (miễn phí)

1. Mở https://dash.cloudflare.com/sign-up, đăng ký bằng email.
2. Không cần thêm domain, không cần mua gì — Workers Free cho
   100.000 request/ngày (đủ cho việc dịch này).
3. **Chỉ dùng Workers AI thì dừng ở đây** — không cần bước 2 (key
   Google); làm tiếp bước 3–4 rồi deploy là dịch được ngay.

### 2. API key Google Cloud Translation (chỉ cần cho đường Google — miễn phí 500.000 ký tự/tháng)

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

# Đặt API key Google làm secret (không lưu vào code/repo) — CHỈ CẦN cho
# đường Google; dùng Workers AI thì bỏ qua bước này.
wrangler secret put GOOGLE_API_KEY
# dán key khi hỏi, Enter.

wrangler deploy
# In ra URL dạng: https://beartea-translate-proxy.<tên>.workers.dev
# (wrangler.toml đã có sẵn [ai] binding = "AI" cho đường Workers AI.)
```

### 5. Nối vào bản web

1. Mở https://munkai0408.github.io/MeoRocky/ trên iPhone, vào tab **Dịch**.
2. Dán URL worker vào ô **URL proxy dịch** (được lưu trong trình duyệt).
3. Hoặc mở **trang Kiểm tra dịch** (link trong tab Dịch) để thử trước:
   dán URL proxy, bấm **Kiểm tra proxy** — phải thấy `ok: true`,
   `provider: "workers-ai"` (đường miễn phí, không key Google) hoặc
   `provider: "google"` (đã đặt key), `month_used` tăng theo số ký tự đã
   dịch. (GET / của worker trả thêm `config: "ok" | "not_configured" |
   "kv_not_configured"` và `provider: "google" | "workers-ai" | null` để
   tab Dịch hiển thị đúng trạng thái cấu hình + đường dịch đang dùng —
   chưa đặt key/KV thì báo rõ, thay vì báo "đang dùng".)

## Cách hạn mức cứng hoạt động (đã kiểm chứng bằng harness, xem exp/item2_results.md + exp/worker_t1_results.md)

| Tình huống | Worker trả | Client làm gì |
|---|---|---|
| Dịch bình thường | `ok:true, provider:"google"` hoặc `"workers-ai"`, `month_used`, `month_limit` | Hiện bản dịch |
| Trùng chuỗi đã dịch | `ok:true, cached:true, chars` (không tính ký tự; cache tách riêng theo provider + model) | Hiện bản dịch |
| Vượt 500.000 ký tự/tháng (cả 2 provider) | `ok:false, code:"quota_exceeded"` (HTTP 429) | Thử MyMemory (+ LibreTranslate nếu đã cấu hình), rồi từ điển offline |
| Workers AI hết ~9.000 neurons ước tính/ngày, hoặc Cloudflare tự báo 4006 | `ok:false, code:"quota_exceeded"` (HTTP 429, kèm `ai_daily_used`/`ai_daily_limit`) | Thử MyMemory (+ LibreTranslate nếu đã cấu hình), rồi từ điển offline |
| Provider lỗi liên tiếp 5 lần (ngắt mạch riêng từng provider) | `ok:false, code:"breaker_open"` (HTTP 429) | Thử MyMemory (+ LibreTranslate nếu đã cấu hình), rồi offline |
| 1 IP gửi >60 request/phút | `ok:false, code:"rate_limited"` (HTTP 429) | Thử lại sau, rồi MyMemory (+ LibreTranslate nếu đã cấu hình) + offline |
| Chưa đặt GOOGLE_API_KEY và chưa gắn binding AI / chưa gắn KV | `ok:false, code:"not_configured"/"kv_not_configured"` (HTTP 503) | Dịch trực tiếp Google như cũ, rồi MyMemory + offline |

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
  Ngân sách Workers AI tính theo ngày UTC (`ai-usage:YYYY-MM-DD`, số
  neurons là ƯỚC TÍNH từ giá công khai — xem chú thích trong worker.js;
  hết ngày tự reset, nhưng nếu Cloudflare tự chặn (mã 4006) thì lần mở
  chặn có thể là 24 giờ sau lần dùng đầu tiên của ngày đó, không phải
  00:00 UTC).
- 10.000 neurons/ngày là ngân sách CHUNG cả tài khoản Cloudflare (mọi
  Worker, Playground đều dùng chung) — worker này chỉ đếm phần nó dùng;
  nếu tài khoản còn chạy Workers AI khác thì ngân sách thật có thể hết
  sớm hơn số worker đếm.
- URL worker là công khai (ai biết URL cũng gọi được) nhưng đã có giới hạn
  theo IP + hạn mức tháng cứng nên không ai làm bạn tốn phí được.
- Cloudflare Workers Free: 100.000 request/ngày — dịch 1 chuỗi/request
  như PB3 thì đủ cho ~100.000 chuỗi/ngày, vượt xa nhu cầu.

## File trong thư mục này

- `worker.js` — code worker (duy nhất cần deploy).
- `wrangler.toml` — cấu hình (KV id là placeholder, xem bước 4).
- `README.md` — file này.
