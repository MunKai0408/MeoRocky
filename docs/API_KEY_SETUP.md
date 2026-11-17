# Hướng dẫn cấu hình dịch qua Cloudflare Worker (Workers AI miễn phí hoặc Google Translate)

Tài liệu này hướng dẫn **người dùng** (chủ tài khoản) triển khai Cloudflare
Worker để tool MeoRocky dịch nhanh và ổn định. AI **không** làm phần này
thay bạn — vì nó cần tài khoản Cloudflare (+ Google nếu dùng đường Google)
của chính bạn, và API key chỉ bạn mới được thấy.

**Có 2 đường dịch — chọn 1:**
- **Đường A — Workers AI (KHUYÊN DÙNG):** miễn phí, **không cần thẻ, không
  cần key Google, không cần billing**. Chỉ cần tài khoản Cloudflare miễn
  phí (email) + deploy worker là dịch được ngay. Model
  `@cf/meta/m2m100-1.2b` (100 ngôn ngữ, có tiếng Việt), miễn phí 10.000
  neurons/ngày.
- **Đường B — Google Translate:** chất lượng cao hơn ở câu dài/câu thành
  ngữ, nhưng cần API key Google + **gắn billing account** (Google bắt gắn
  billing mới cho bật Cloud Translation API — xem lưu ý bên dưới). Giữ
  làm lựa chọn cho sau này; worker tự dùng Google khi key đã đặt
  (`TRANSLATE_PROVIDER=auto`).

## Tại sao cần worker?

Hiện tại tool dịch bằng Google Translate miễn phí (gtx, không key): trình
duyệt gọi thẳng Google nên hay bị chặn CORS / giới hạn tốc độ (HTTP 429)
theo IP, và dịch chậm (~0,9 chuỗi/giây — mỗi chuỗi 1 request, lỗi thì thử
lại từng chuỗi). Worker đứng giữa làm 3 việc mà gtx không làm được:

- **Batch thật:** 1 request dịch tới 100 chuỗi (Google Cloud Translation
  v2 nhận `q` dạng mảng; Workers AI thì worker tự tách thành từng lần gọi
  1 chuỗi), thay vì 1 chuỗi/request.
- **Cache:** chuỗi đã dịch rồi thì trả lại từ KV, không gọi dịch nữa,
  không tốn ký tự. Cache tách riêng theo provider + model nên kết quả
  Google và Workers AI không bao giờ lẫn nhau.
- **Hạn mức cứng:** tối đa **500.000 ký tự/tháng** = đúng phần miễn phí của
  Google Cloud Translation. Hết hạn mức, worker tự trả tín hiệu để tool
  rơi về MyMemory + từ điển offline — **không bao giờ tự phát sinh phí**.
  Workers AI thêm ngân sách ngày: worker tự dừng ở **~9.000 neurons ước
  tính/ngày** (dưới hạn miễn phí 10.000 neurons/ngày của tài khoản).

Sơ đồ: iPhone (tab Dịch) → Cloudflare Worker → Workers AI (m2m100) hoặc
Google Cloud Translation API v2. API key (đường Google) chỉ nằm trong
worker (dạng secret), **không bao giờ** vào code, repo hay trình duyệt.

## Chuẩn bị

Bạn cần: 1 tài khoản Cloudflare (đăng ký miễn phí bằng email) + 1 máy
tính có Node.js (để chạy lệnh `wrangler`). Không cần mua domain.
**Không có máy tính?** Lệnh `wrangler` vẫn chạy được trên trình duyệt
cloud (nhờ Muse chạy hộ sau khi bạn đăng nhập Cloudflare/Google trên
trình duyệt cloud) hoặc trên điện thoại bằng app terminal — không bắt
buộc phải có máy tính riêng.
- **Đường A (Workers AI): chỉ cần bấy nhiêu — không cần thẻ, không cần
  tài khoản Google, không cần billing.** Làm theo "Đường A" bên dưới
  (~15 phút) là dịch được ngay.
- **Đường B (Google):** thêm tài khoản Google + gắn billing account
  (xem lưu ý quan trọng bên dưới).

**Lưu ý quan trọng (chỉ đường Google):** Google yêu cầu **gắn billing
account** mới cho bật Cloud Translation API (màn hình "Billing
required"). Gói dùng thử miễn phí cho $300 tín dụng trong 90 ngày, không
mất phí trong thời gian thử — bạn chỉ cần gắn billing để bật API, worker
có hạn mức cứng 500.000 ký tự/tháng nên mức dùng luôn nằm trong phần miễn
phí của Google.

---

## Đường A — Workers AI (khuyên dùng: miễn phí, không cần thẻ)

### A1. Tài khoản Cloudflare + wrangler

1. Mở https://dash.cloudflare.com/sign-up , đăng ký bằng email (gói
   **Workers Free**: 100.000 request/ngày — đủ cho việc dịch này, dịch 1
   chuỗi/request thì ~100.000 chuỗi/ngày).
2. Trên máy tính, cài wrangler (công cụ dòng lệnh của Cloudflare):
   ```bash
   npm install -g wrangler
   wrangler login
   ```
   Lệnh `login` mở trình duyệt — đăng nhập Cloudflare và bấm **Allow**
   cho wrangler.

### A2. Tạo 2 KV namespace (cache + đếm ký tự)

Trong thư mục `cloudflare/` của repo này (clone repo về máy, hoặc tải
zip repo từ GitHub):

```bash
cd cloudflare
wrangler kv namespace create TRANSLATE_CACHE
wrangler kv namespace create TRANSLATE_USAGE
```

Mỗi lệnh in ra 1 `id` (dạng chuỗi hex). Mở file `cloudflare/wrangler.toml`,
thay cả 4 chỗ `REPLACE_WITH_KV_NAMESPACE_ID` bằng 2 id thật (mỗi
namespace có 2 chỗ: `id` và `preview_id`).

> File `wrangler.toml` **không** chứa key hay id thật khi chưa cấu hình —
> deploy với id giả sẽ lỗi, đó là cố ý (tránh deploy nhầm khi chưa xong).
> File đã có sẵn `[ai] binding = "AI"` (model m2m100) và
> `TRANSLATE_PROVIDER = "auto"` — không cần sửa gì thêm cho đường A.

### A3. Deploy worker (không cần key gì cả)

```bash
wrangler deploy
```

Lệnh in ra URL dạng:

```text
https://beartea-translate-proxy.<tên-của-bạn>.workers.dev
```

Copy URL này — đó là URL proxy sẽ dán vào tool ở bước A4. Xong bước này
là dịch được ngay bằng Workers AI (không có key Google thì worker tự
dùng Workers AI).

### A4. Nối worker vào tool + kiểm tra

1. Mở https://munkai0408.github.io/MeoRocky/ trên iPhone, vào tab
   **Dịch**.
2. Dán URL worker (bước A3) vào ô **URL proxy dịch** (được lưu trong trình
   duyệt này, không gửi đi đâu khác). Gõ trơn hostname cũng được, tool tự
   thêm `https://`.
3. Bấm **Kiểm tra worker**. Phải thấy:
   > **Worker: đang dùng — provider Workers AI (miễn phí, không cần thẻ)
   > (hạn mức tháng 500.000 ký tự).**
4. Hoặc mở **trang Kiểm tra dịch** (link trong tab Dịch): dán URL proxy,
   bấm **Kiểm tra proxy** — phải thấy `ok: true`,
   `provider: "workers-ai"`.
5. Dịch thử 1 file nhỏ: bản dịch phải về đủ, placeholder (`{VAR1}`,
   `{0}`, `%s`, thẻ) còn nguyên — cổng kiểm tra bước 6 vẫn PASS như
   trước. Dịch lại cùng 1 chuỗi lần nữa: lần 2 trả từ cache (không tốn
   ký tự mới).

Từ lúc này, mọi lần dịch trong tool đi qua worker (cache + hạn mức cứng).
Worker vắng mặt/lỗi thì tool **tự rơi** về Google trực tiếp → MyMemory →
từ điển offline, không chặn việc dịch.

> **Chất lượng Workers AI (nói thật):** m2m100-1.2b là model nhỏ hơn Google
> Translate nên câu ngắn (text UI game) thường dịch ổn, nhưng câu dài/câu
> thành ngữ có thể kém hơn Google. Chuỗi game có nhiều câu trong 1 chuỗi:
> worker gửi từng chuỗi một nên đã hạn chế việc model nuốt mất câu, nhưng
> 1 chuỗi đơn lẻ mà bản thân nó có nhiều câu vẫn có thể mất câu — đó là
> giới hạn đã biết của model. **Luôn chạy cổng kiểm tra bước 6 trước khi
> ép file** — cổng này bắt được placeholder/thẻ/xuống dòng bị lệch. Nếu
> thấy chất lượng chưa đủ, chuyển sang đường B (Google) bên dưới.

---

## Đường B — Google Translate (lựa chọn cho sau này, cần key + billing)

Đường B chỉ cần làm khi bạn muốn chất lượng Google thay cho Workers AI.
Worker ở chế độ `auto` (mặc định) tự dùng Google ngay khi secret
`GOOGLE_API_KEY` đã đặt — không cần deploy lại code, chỉ cần đặt secret
rồi `wrangler deploy` 1 lần. Muốn quay lại Workers AI thì xóa secret
(`wrangler secret delete GOOGLE_API_KEY`) rồi deploy lại, hoặc đặt
`TRANSLATE_PROVIDER = "workers-ai"` trong `wrangler.toml` rồi deploy.

## Bước 1 — Tạo API key Google (Google Cloud)

1. Mở https://console.cloud.google.com/ , đăng nhập tài khoản Google của
   bạn (tài khoản đang giữ file game trên Drive).
2. Tạo project mới: bấm tên project ở góc trên → **New Project** → đặt tên
   vd `beartea-translate` → **Create**. Chờ project tạo xong rồi chọn nó.
3. Bật API: vào **APIs & Services → Library**, tìm API có tên **Cloud Translation API**, bấm **Enable**.
   Nếu hiện **"Billing required"**: Google bắt gắn billing account trước
   khi bật API — bấm **Enable Billing**, chọn/đăng ký gói dùng thử miễn
   phí ($300 tín dụng, 90 ngày, không mất phí trong thời gian thử). Chưa
   nhập thẻ thì dừng ở đây, chưa mất phí gì, chưa tạo được key — gắn
   billing xong quay lại bấm **Enable** tiếp.
4. Tạo key: vào **APIs & Services → Credentials** → **Create Credentials
   → API key**. Copy key vừa tạo (dạng `AIza...`).
5. **Hạn chế key (nên làm ngay):** bấm vào key vừa tạo → mục **API
   restrictions** → chọn **Restrict key** → tick chỉ **Cloud Translation
   API** → **Save**. Key này sau đó chỉ gọi được đúng API dịch, kể cả khi
   lộ cũng không dùng được cho dịch vụ khác.
6. **KHÔNG dán key vào đâu khác**: không dán vào chat, file, repo, email
   hay `.env`. Key chỉ nhập 1 lần ở Bước 4 bằng lệnh `wrangler secret put`.

> Kiểm tra nhanh: key này tên biến môi trường là **`GOOGLE_API_KEY`**
> (dạng secret của worker, không phải biến của repo). Nếu thiếu key,
> worker trả `503 not_configured` và tool báo rõ "chưa cấu hình
> GOOGLE_API_KEY" — xem bảng lỗi ở cuối.

## Bước 2 — Tài khoản Cloudflare + wrangler (làm giống A1 nếu chưa làm)

Xem mục A1 ở "Đường A" — tài khoản Cloudflare + `wrangler login`. Nếu
đã deploy đường A rồi thì bỏ qua bước này.

## Bước 3 — Tạo 2 KV namespace (làm giống A2 nếu chưa làm)

Xem mục A2 ở "Đường A". Nếu đã deploy đường A rồi thì KV đã có sẵn,
không cần tạo lại.

## Bước 4 — Đặt API key làm secret (KHÔNG ghi vào code)

```bash
wrangler secret put GOOGLE_API_KEY
```

Dán key Google ở Bước 1 khi được hỏi, bấm Enter. Key được lưu mã hóa
trong Cloudflare, **không** nằm trong repo, không hiện trong log, không
ai đọc được từ code.

**Quy tắc an toàn key (bắt buộc):**

- Không ghi key trực tiếp vào source code, không commit file `.env` hay
  bất kỳ file chứa key nào lên repo.
- Không gửi key qua chat/email/ảnh chụp màn hình.
- Kiểm tra trạng thái key mà **không in giá trị key** ra terminal hay log
  (chỉ kiểm tra worker trả `ok:true` hay `not_configured` — xem Bước 6).
- Nếu lộ key: vào Google Cloud Console → Credentials → xóa key cũ, tạo
  key mới, chạy lại `wrangler secret put GOOGLE_API_KEY`, deploy lại.
  Không tự ý thay đổi/xóa key đang chạy khi chưa có lý do.

## Bước 5 — Deploy worker (kích hoạt đường Google)

```bash
wrangler deploy
```

Lệnh in ra URL dạng:

```text
https://beartea-translate-proxy.<tên-của-bạn>.workers.dev
```

Copy URL này — đó là URL proxy sẽ dán vào tool ở Bước 6. Từ lúc deploy
này, worker ở chế độ `auto` sẽ dùng **Google** (vì secret đã đặt) thay
cho Workers AI.

> Muốn đổi hạn mức tháng / giới hạn IP / ngưỡng ngắt mạch thì sửa trong
> `cloudflare/wrangler.toml` (`MONTHLY_CHAR_LIMIT`, `RATE_LIMIT_PER_MINUTE`,
> `BREAKER_THRESHOLD`, `BREAKER_COOLDOWN_S`) rồi chạy lại
> `wrangler deploy`. Mặc định `MONTHLY_CHAR_LIMIT = 500000` (phần miễn
> phí) — **đừng tăng quá 500.000 nếu không muốn phát sinh phí Google**.
> Muốn đổi provider thì sửa `TRANSLATE_PROVIDER` (`auto` | `google` |
> `workers-ai`) rồi deploy lại; muốn đổi ngưỡng neurons ngày của Workers
> AI thì sửa `AI_DAILY_NEURON_GUARD` (mặc định 9000, dưới hạn miễn phí
> 10.000/ngày).

## Bước 6 — Nối worker vào tool + kiểm tra

1. Mở https://munkai0408.github.io/MeoRocky/ trên iPhone, vào tab
   **Dịch**.
2. Dán URL worker (Bước 5) vào ô **URL proxy dịch** (được lưu trong trình
   duyệt này, không gửi đi đâu khác). Gõ trơn hostname cũng được, tool tự
   thêm `https://`.
3. Bấm **Kiểm tra worker**. Phải thấy:
   > **Worker: đang dùng — provider Google (hạn mức tháng
   > 500.000 ký tự).**
4. Hoặc mở **trang Kiểm tra dịch** (link trong tab Dịch): dán URL proxy,
   bấm **Kiểm tra proxy** — phải thấy `ok: true`, `provider: "google"`.
5. Dịch thử 1 file nhỏ: bản dịch phải về đủ, placeholder (`{VAR1}`,
   `{0}`, `%s`, thẻ) còn nguyên — cổng kiểm tra bước 6 vẫn PASS như
   trước. Dịch lại cùng 1 chuỗi lần nữa: lần 2 trả từ cache (không tốn
   ký tự mới).

Từ lúc này, mọi lần dịch Google trong tool đi qua worker (cache + hạn
mức cứng). Worker vắng mặt/lỗi thì tool **tự rơi** về Google trực tiếp →
MyMemory → từ điển offline, không chặn việc dịch.

## Bảng xử lý lỗi (trạng thái worker trong tab Dịch)

| Trạng thái hiển thị | Nghĩa | Cách sửa |
|---|---|---|
| `Worker: chưa triển khai` | Chưa dán URL proxy | Làm từ Đường A, hoặc cứ để trống — dịch vẫn chạy qua Google trực tiếp |
| `Worker: đang dùng — provider Workers AI` | OK, đang dịch bằng Workers AI (miễn phí, không cần thẻ) | Không cần làm gì |
| `Worker: đang dùng — provider Google` | OK, đang dịch bằng Google (đã đặt key) | Không cần làm gì |
| `Worker: đã triển khai nhưng chưa cấu hình` | Worker deploy rồi nhưng chưa có key Google VÀ chưa bật Workers AI (HTTP 503 `not_configured`) | Đặt secret Google (Bước 4) hoặc deploy lại với `[ai]` binding trong `wrangler.toml`, rồi `wrangler deploy` |
| Bật API báo "You are not an administrator of any billing accounts" | Đăng ký dùng thử chưa hoàn tất (thường kẹt ở bước nhập phương thức thanh toán) — tài khoản billing chưa được tạo | Quay lại hoàn tất đăng ký dùng thử (nhập phương thức thanh toán), vào mục **Billing** kiểm tra đã có tài khoản billing chưa, rồi mới bật API |
| `Worker: đã triển khai nhưng chưa gắn KV` | Chưa tạo/gắn KV namespace (HTTP 503 `kv_not_configured`) | Chạy lại A2, chép id thật vào `wrangler.toml`, `wrangler deploy` |
| `Worker: lỗi (quota_exceeded)` | Hết 500.000 ký tự miễn phí của tháng (HTTP 429) | Đợi sang tháng mới (tự reset theo tháng UTC), hoặc dịch tiếp bằng MyMemory + offline — tool tự chuyển, không phát sinh phí |
| `Worker: lỗi (quota_exceeded)` + provider Workers AI | Hết ~9.000 neurons ước tính của ngày, hoặc Cloudflare tự báo hết 10.000 neurons/ngày (mã 4006) | Đợi sang ngày mới (reset 00:00 UTC; nếu bị Cloudflare chặn thì lần mở chặn có thể là 24 giờ sau lần dùng đầu tiên của ngày đó). Trong lúc đó tool tự dùng MyMemory + offline |
| `Worker: lỗi (rate_limited)` | 1 IP gửi >60 request/phút (HTTP 429) | Chờ 1 phút rồi dịch tiếp |
| `Worker: lỗi (breaker_open)` | Provider lỗi liên tiếp 5 lần, worker đang ngắt mạch 60 giây (ngắt mạch riêng từng provider) | Chờ 1 phút, tool tự dùng MyMemory + offline trong lúc đó |
| `Worker: không gọi được` | Sai URL / mất mạng / worker bị xóa | Kiểm tra lại URL đã dán (đúng dạng `https://....workers.dev`), kiểm tra mạng, hoặc deploy lại |

## Câu hỏi thường gặp

- **Workers AI dịch có tốt bằng Google không?** Không khẳng định được —
  m2m100-1.2b là model nhỏ hơn Google Translate. Câu ngắn (text UI game)
  thường ổn, câu dài/câu thành ngữ có thể kém hơn. Kiểm chứng bằng mock
  (xem `exp/worker_t1_results.md`) chỉ chứng minh được pipeline đúng
  (placeholder còn nguyên, không mất chuỗi, không lẫn cache 2 provider),
  KHÔNG đo được chất lượng dịch thật của model — phải thử trên bản deploy
  thật mới biết. Luôn chạy cổng kiểm tra bước 6 trước khi ép file.
- **Đổi provider thì làm gì?** Sửa `TRANSLATE_PROVIDER` trong
  `cloudflare/wrangler.toml` (`auto` | `google` | `workers-ai`) rồi
  `wrangler deploy`. `auto` = Google nếu có key, nếu không Workers AI.
  Muốn quay lại Workers AI sau khi đã đặt key Google thì xóa secret
  (`wrangler secret delete GOOGLE_API_KEY`) rồi deploy lại, hoặc đặt
  `TRANSLATE_PROVIDER = "workers-ai"`.
- **10.000 neurons/ngày dịch được bao nhiêu chuỗi?** Con số chính xác do
  Cloudflare đo (không có API đọc ngân sách còn lại). Worker ước tính
  ~0,03 neurons/token (suy ra từ giá công khai $0.34/1M token), token
  ~ký tự/4 — chuỗi game ngắn ~20 ký tự tốn ~0,3 neurons, tức ~29.000
  chuỗi/ngày ở ngưỡng dừng 9.000 neurons. Đây là ƯỚC TÍNH, không phải số
  đo thật; số thật xem trong Cloudflare Dashboard. Lưu ý 10.000
  neurons/ngày là ngân sách CHUNG cả tài khoản (mọi Worker, Playground
  dùng chung).
- **Bật API báo "Billing required" thì sao?** Google bắt gắn billing
  account trước khi bật Cloud Translation API. Gắn gói dùng thử miễn phí
  ($300 tín dụng, 90 ngày) là đủ — không mất phí trong thời gian thử,
  worker có hạn mức cứng 500.000 ký tự/tháng nên mức dùng luôn nằm trong
  phần miễn phí. **Hoặc cứ dùng đường A (Workers AI) — không cần billing.**
- **Worker có đọc được file game của tôi không?** Không. Worker chỉ
  nhận từng chuỗi text cần dịch, không nhận file, không lưu text (chỉ
  cache cặp text→bản dịch trong KV 30 ngày để khỏi dịch lại).
- **Ai biết URL worker cũng gọi được à?** Được, nhưng đã có giới hạn
  theo IP (60 request/phút/IP) + hạn mức tháng cứng (500.000 ký tự)
  nên không ai làm bạn tốn phí được.
- **Không dùng worker nữa thì sao?** Xóa URL trong ô proxy ở tab Dịch
  → tool tự về Google trực tiếp như trước. Không cần xóa worker trên
  Cloudflare (để đó cũng không tốn phí).
- **Đổi key Google thì làm gì?** Chạy lại Bước 4
  (`wrangler secret put GOOGLE_API_KEY`) rồi `wrangler deploy`.
- **Muốn xem đã dùng bao nhiêu ký tự tháng này?** Bấm **Kiểm tra
  worker** sau khi dịch — worker trả `month_used` trong lần dịch; hoặc
  vào Cloudflare Dashboard → Workers → KV → xem key `usage:YYYY-MM`.

## Tài liệu liên quan

- `cloudflare/README.md` — mô tả kỹ thuật worker (API, hạn mức, cache,
  ngắt mạch) + bảng hành vi fallback.
- `cloudflare/worker.js` — code worker (duy nhất cần deploy).
- `cloudflare/wrangler.toml` — cấu hình deploy (KV id là placeholder
  cho tới khi làm xong Bước 3).
