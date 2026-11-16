# Mục 2 — Hướng dẫn triển khai proxy dịch Cloudflare (cho Joseph)

**Trạng thái: code worker + trang kiểm tra + nối app/gt.js ĐÃ XONG và đã
kiểm chứng bằng harness (xem `exp/item2_results.md`). Phần triển khai thật
đang CHỜ Joseph vì cần tài khoản Cloudflare + API key Google của anh —
Muse không tự tạo tài khoản hay key.**

## Làm theo từng bước

Đọc file **`cloudflare/README.md`** trong repo (đã đẩy lên main) — hướng
dẫn đầy đủ từng bước, tóm tắt:

1. **Tài khoản Cloudflare (miễn phí):** https://dash.cloudflare.com/sign-up
   — không cần domain, không cần mua gì (Workers Free: 100.000 req/ngày).
2. **API key Google:** https://console.cloud.google.com/ → tạo project →
   bật **Cloud Translation API** → Credentials → Create API key (nên giới
   hạn key chỉ cho Cloud Translation API). **Không dán key vào repo/chat.**
3. **Cài wrangler:** `npm install -g wrangler` rồi `wrangler login`.
4. **Deploy (trong thư mục `cloudflare/` của repo):**
   ```bash
   wrangler kv namespace create TRANSLATE_CACHE
   wrangler kv namespace create TRANSLATE_USAGE
   # chép 2 id thật vào wrangler.toml thay REPLACE_WITH_KV_NAMESPACE_ID
   wrangler secret put GOOGLE_API_KEY   # dán key khi hỏi
   wrangler deploy
   ```
5. **Nối vào bản web:** mở trang BearTea → tab **Dịch** → dán URL worker
   (dạng `https://beartea-translate-proxy.<tên>.workers.dev`) vào ô
   **URL proxy dịch**. Hoặc thử trước ở **trang Kiểm tra dịch** (link trong
   tab Dịch): dán URL, bấm **Kiểm tra proxy** — phải thấy `ok: true`,
   `provider: "google"`.

## Cam kết phí (đã khóa trong code worker)

- Tối đa **500.000 ký tự/tháng** = đúng phần miễn phí của Google. Hết hạn
  mức worker trả `quota_exceeded`, trang tự rơi về **MyMemory + từ điển
  offline** — không tự động tốn phí, không tự nâng hạn mức.
- Cache KV: chuỗi đã dịch rồi thì không gọi Google lại, không tính ký tự.
- Giới hạn 60 request/phút/IP + ngắt mạch sau 5 lỗi Google liên tiếp.
