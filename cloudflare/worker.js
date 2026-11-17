// cloudflare/worker.js — Proxy dịch cho BearTea (bản web PB3).
//
// Mục đích: trình duyệt (Safari iPhone) gọi trực tiếp Google Translate hay
// bị chặn CORS / 429 theo IP. Worker này đứng giữa: nhận text từ trình
// duyệt, dịch bằng 1 trong 2 provider, trả bản dịch về.
//
// PROVIDER (chọn bằng biến TRANSLATE_PROVIDER: "auto" | "google" |
// "workers-ai", mặc định "auto"):
//   * "google" — Google Cloud Translation API v2, cần secret
//     GOOGLE_API_KEY (wrangler secret put; KHÔNG BAO GIỜ ghi vào code hay
//     repo). Chất lượng cao nhất, batch thật 1 request/nhiều chuỗi.
//   * "workers-ai" — Cloudflare Workers AI, model @cf/meta/m2m100-1.2b
//     (dịch, 100 ngôn ngữ, có vi) qua binding AI ([ai] trong
//     wrangler.toml). Chỉ cần tài khoản Cloudflare miễn phí (email, KHÔNG
//     cần thẻ, KHÔNG cần key Google, KHÔNG cần billing). Model nhỏ hơn
//     Google nên chất lượng có thể thấp hơn ở câu dài/câu thành ngữ.
//   * "auto" — dùng Google khi secret GOOGLE_API_KEY đã đặt, nếu không thì
//     dùng Workers AI khi có binding AI, nếu không có cả hai thì trả
//     not_configured (503) như trước. Đường Google giữ nguyên khi key đã đặt.
//
// ĐẶC TÍNH m2m100 (Workers AI): gửi đoạn dài nhiều câu trong 1 lần gọi có
// thể bị nuốt mất câu — worker LUÔN gửi từng chuỗi một cho Workers AI
// (batch được tách thành N lần gọi env.AI.run, mỗi lần 1 chuỗi), không
// gộp nhiều chuỗi vào 1 lần gọi như Google. Chuỗi đơn lẻ mà bản thân nó
// có nhiều câu vẫn có thể mất câu — giới hạn đã biết của model, xem
// docs/API_KEY_SETUP.md.
//
// AN TOÀN PHÍ — GIỚI HẠN CỨNG, KHÔNG BAO GIỜ TỰ PHÁT SINH PHÍ:
//   * Hạn mức tháng: tối đa MONTHLY_CHAR_LIMIT ký tự/tháng (mặc định
//     500.000 = đúng phần miễn phí của Google Cloud Translation).
//     Đếm trong KV theo tháng UTC (key usage:YYYY-MM). Hết hạn mức thì trả
//     {ok:false, code:"quota_exceeded"} để client tự rơi về MyMemory +
//     từ điển offline — worker KHÔNG gọi dịch nữa trong tháng đó. Hạn mức
//     tháng áp cho CẢ HAI provider (cùng 1 ngân sách ký tự).
//   * Workers AI: thêm ngân sách ngày ước tính AI_DAILY_NEURON_GUARD
//     neurons/ngày (mặc định 9.000, dưới mức miễn phí 10.000 neurons/ngày
//     của tài khoản). Đếm trong KV theo ngày UTC (key ai-usage:YYYY-MM-DD).
//     Hết ngưỡng ngày thì trả quota_exceeded. Số neurons là ƯỚC TÍNH
//     (xem estimateAiNeurons — Cloudflare không có API đọc ngân sách còn
//     lại, và binding env.AI.run không trả số neurons đã dùng), nên worker
//     dừng sớm hơn 10% để có biên an toàn. Tài khoản Free của Cloudflare
//     vốn không gắn thẻ nên vượt ngưỡng cũng không phát sinh phí —
//     Cloudflare tự trả lỗi 4006 thì worker cũng đổi thành quota_exceeded.
//   * Ký tự được "đặt trước" (reserve) TRƯỚC khi gọi dịch và hoàn lại
//     (refund) nếu dịch lỗi, nên hạn mức không bị vượt vì lỗi (áp cho cả
//     ký tự tháng và neurons ngày của Workers AI).
//   * Cache KV: cùng provider + model + text + ngôn ngữ +
//     glossary_version thì trả lại từ cache, không gọi dịch, không tính
//     ký tự. Cache key GỒM provider + model nên kết quả Google và
//     Workers AI KHÔNG BAO GIỜ lẫn nhau.
//   * Giới hạn theo IP: RATE_LIMIT_PER_MINUTE request/phút/IP.
//   * Ngắt mạch (circuit breaker) RIÊNG TỪNG PROVIDER (key breaker:<tên
//     provider>): provider lỗi liên tiếp đủ BREAKER_THRESHOLD lần thì mở
//     mạch BREAKER_COOLDOWN_S giây, trong thời gian đó trả ngay
//     {code:"breaker_open"} thay vì gọi dịch vô ích.
//   * Tôn trọng Retry-After của Google + backoff cấp số nhân khi 429/5xx.
//
// API:
//   GET  /  -> {ok:true, service:"beartea-translate-proxy", config, ...}
//               (kiểm tra worker đã deploy, không tốn ký tự; config =
//               "ok" | "not_configured" | "kv_not_configured" để tab Dịch
//               hiển thị đúng trạng thái cấu hình — T1; provider =
//               "google" | "workers-ai" | null để tab Dịch cho biết đường
//               dịch đang dùng — T1 Workers AI)
//   POST /  {text, target, source?} ->
//     OK:     {ok:true, translated, provider, chars, cached,
//              month_used, month_limit, ai_daily_used?, ai_daily_limit?}
//     Hết hạn mức: {ok:false, code:"quota_exceeded", month_used, month_limit,
//              fallback:"mymemory+offline"} (HTTP 429)
//     Lỗi khác: {ok:false, code, error, provider:"none",
//              fallback:"mymemory+offline"}
//   POST /  {texts:[...], target, source?} -> batch (mục A, đợt 13:52):
//     OK:     {ok:true, translated:[...], provider, chars, cached,
//              month_used, month_limit, ai_daily_used?, ai_daily_limit?}
//     Google v2 nhận q dạng mảng (tối đa 128 chuỗi) nên batch thật được,
//     khác gtx miễn phí (chỉ dịch q đầu). Workers AI thì batch được tách
//     thành từng lần gọi 1 chuỗi (xem trên). Giới hạn: 1–100 chuỗi, mỗi
//     chuỗi ≤2000 ký tự, tổng ≤5000 ký tự/request (cùng hạn mức tháng
//     như dịch đơn).
//
// Triển khai: xem cloudflare/README.md (cần tài khoản Cloudflare của Joseph
// + API key Google cho đường Google — KHÔNG có key nào trong repo này;
// đường Workers AI không cần key Google).

const GOOGLE_API = "https://translation.googleapis.com/language/translate/v2";
const MAX_TEXT_CHARS = 5000; // Google v2: tối đa 5000 ký tự/request
const MAX_BATCH_TEXTS = 100; // batch: tối đa 100 chuỗi/request
const MAX_BATCH_TEXT_CHARS = 2000; // mỗi chuỗi trong batch tối đa 2000 ký tự

const DEFAULT_MONTHLY_LIMIT = 500000;
const DEFAULT_RATE_PER_MINUTE = 60;
const DEFAULT_BREAKER_THRESHOLD = 5;
const DEFAULT_BREAKER_COOLDOWN_S = 60;
const DEFAULT_GLOSSARY_VERSION = "1";

// ---- Workers AI (T1) ----
const AI_MODEL = "@cf/meta/m2m100-1.2b"; // model dịch, 100 ngôn ngữ, có vi
const AI_MODEL_LABEL = "m2m100-1.2b";
const GOOGLE_MODEL_LABEL = "google-v2";
const AI_DAILY_NEURON_LIMIT = 10000; // hạn miễn phí của tài khoản/ngày
const DEFAULT_AI_DAILY_NEURON_GUARD = 9000; // worker tự dừng trước 10%
// ƯỚC TÍNH neurons cho m2m100 [HYPOTHESIS — suy ra từ giá công khai, KHÔNG
// phải số đo thật của Cloudflare]: giá model $0.34/1M token (cả input lẫn
// output) / $0.011/1000 neurons = ~0.0309 neurons/token. Token ước tính
// ~chars/4 (cả chiều vào + ra ≈ 2× số token của text gốc). Ví dụ chuỗi game
// 20 ký tự ≈ 0.3 neurons -> 9.000 neurons ≈ ~29.000 chuỗi ngắn/ngày.
// Vì là ước tính nên ngưỡng dừng đặt thấp hơn hạn thật 10% để có biên an toàn.
const AI_NEURONS_PER_TOKEN = 0.0309;
const AI_CHARS_PER_TOKEN = 4;
const AI_BATCH_CONCURRENCY = 5; // batch Workers AI: tối đa 5 lần gọi song song

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    "access-control-max-age": "86400",
  };
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function fail(code, error, status, extra = {}) {
  return json(
    { ok: false, code, error, provider: "none", fallback: "mymemory+offline", ...extra },
    status,
  );
}

async function sha256Hex(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function monthKey(d = new Date()) {
  return d.toISOString().slice(0, 7); // "YYYY-MM" (UTC)
}

function parseRetryAfterMs(res) {
  const v = res.headers ? res.headers.get("Retry-After") : null;
  if (!v) return 0;
  const n = Number(v);
  if (Number.isFinite(n)) return n * 1000;
  const t = Date.parse(v);
  return Number.isFinite(t) ? Math.max(0, t - Date.now()) : 0;
}

async function kvInt(kv, key) {
  const raw = await kv.get(key);
  const n = parseInt(raw || "0", 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// Gọi Google Cloud Translation API v2 (có key). 429/5xx thì thử lại tối đa
// 2 lần, tôn trọng Retry-After (tối đa 10 giây/lần) + backoff 1s/2s.
// Trả {translatedText} hoặc ném Error có .status / .retryable.
async function callGoogle(text, source, target, apiKey) {
  const url = GOOGLE_API + "?key=" + encodeURIComponent(apiKey);
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await sleep(1000 * attempt); // backoff 1s rồi 2s (cộng thêm Retry-After bên dưới)
    }
    let res;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ q: text, target, source, format: "text" }),
        signal: ctrl.signal,
      });
    } catch (e) {
      clearTimeout(timer);
      const err = new Error(
        e && e.name === "AbortError"
          ? "Google quá thời gian chờ (15 giây)."
          : "Không gọi được Google (" + (e && e.message ? e.message : e) + ").",
      );
      err.retryable = true;
      throw err; // lỗi mạng: ném ngay, không thử lại vô ích trong worker
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 200) {
      let data;
      try {
        data = await res.json();
      } catch (e) {
        const err = new Error("Google trả về dữ liệu không đọc được.");
        err.retryable = false;
        throw err;
      }
      const t =
        data && data.data && data.data.translations &&
        data.data.translations[0] && data.data.translations[0].translatedText;
      if (typeof t !== "string") {
        const err = new Error("Google trả về thiếu bản dịch.");
        err.retryable = false;
        throw err;
      }
      return t;
    }
    if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
      const wait = parseRetryAfterMs(res);
      lastErr = new Error("Google giới hạn tốc độ (HTTP " + res.status + ").");
      lastErr.status = res.status;
      lastErr.retryable = true;
      if (attempt < 2) {
        await sleep(Math.min(wait, 10000));
        continue;
      }
      throw lastErr;
    }
    const err = new Error("Google trả HTTP " + res.status + ".");
    err.status = res.status;
    err.retryable = false;
    throw err;
  }
  throw lastErr || new Error("Google lỗi không rõ.");
}

// Gọi Google Cloud Translation API v2 với q dạng MẢNG (tối đa 128 chuỗi
// theo tài liệu Google). Trả mảng bản dịch song song với texts (đúng thứ
// tự). Dùng lại đúng luật retry/backoff/Retry-After của callGoogle.
// (gtx miễn phí KHÔNG làm được việc này — nó chỉ dịch q đầu tiên.)
async function callGoogleBatch(texts, source, target, apiKey) {
  const url = GOOGLE_API + "?key=" + encodeURIComponent(apiKey);
  let lastErr = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    if (attempt > 0) {
      await sleep(1000 * attempt);
    }
    let res;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 15000);
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ q: texts, target, source, format: "text" }),
        signal: ctrl.signal,
      });
    } catch (e) {
      clearTimeout(timer);
      const err = new Error(
        e && e.name === "AbortError"
          ? "Google quá thời gian chờ (15 giây)."
          : "Không gọi được Google (" + (e && e.message ? e.message : e) + ").",
      );
      err.retryable = true;
      throw err;
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 200) {
      let data;
      try {
        data = await res.json();
      } catch (e) {
        const err = new Error("Google trả về dữ liệu không đọc được.");
        err.retryable = false;
        throw err;
      }
      const list =
        data && data.data && data.data.translations;
      if (!Array.isArray(list) || list.length !== texts.length ||
          !list.every((t) => t && typeof t.translatedText === "string")) {
        const err = new Error("Google trả về thiếu bản dịch.");
        err.retryable = false;
        throw err;
      }
      return list.map((t) => t.translatedText);
    }
    if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
      const wait = parseRetryAfterMs(res);
      lastErr = new Error("Google giới hạn tốc độ (HTTP " + res.status + ").");
      lastErr.status = res.status;
      lastErr.retryable = true;
      if (attempt < 2) {
        await sleep(Math.min(wait, 10000));
        continue;
      }
      throw lastErr;
    }
    const err = new Error("Google trả HTTP " + res.status + ".");
    err.status = res.status;
    err.retryable = false;
    throw err;
  }
  throw lastErr || new Error("Google lỗi không rõ.");
}

// ---- Provider: Google | Workers AI (T1) ----

// Chọn provider theo biến TRANSLATE_PROVIDER ("auto" | "google" |
// "workers-ai", mặc định "auto").
//   auto: Google khi có secret GOOGLE_API_KEY, nếu không Workers AI khi có
//         binding AI, nếu không có cả hai thì provider=null.
//   google / workers-ai (chỉ định rõ): provider yêu cầu không sẵn sàng thì
//         provider=null + missing cho biết thiếu cái gì.
// Trả {provider: "google" | "workers-ai" | null, missing: null |
// "google_key" | "ai_binding" | "both", want: <giá trị TRANSLATE_PROVIDER>}.
function resolveProvider(env) {
  const want = String(env.TRANSLATE_PROVIDER || "auto").trim().toLowerCase();
  const hasGoogle = !!env.GOOGLE_API_KEY;
  const hasAI = !!env.AI;
  if (want === "workers-ai") {
    return { provider: hasAI ? "workers-ai" : null,
      missing: hasAI ? null : "ai_binding", want };
  }
  if (want === "google") {
    return { provider: hasGoogle ? "google" : null,
      missing: hasGoogle ? null : "google_key", want };
  }
  if (hasGoogle) return { provider: "google", missing: null, want };
  if (hasAI) return { provider: "workers-ai", missing: null, want };
  return { provider: null, missing: "both", want };
}

function modelFor(provider) {
  return provider === "workers-ai" ? AI_MODEL : GOOGLE_MODEL_LABEL;
}

function dayKey(d = new Date()) {
  return d.toISOString().slice(0, 10); // "YYYY-MM-DD" (UTC)
}

async function kvFloat(kv, key) {
  const raw = await kv.get(key);
  const n = parseFloat(raw || "0");
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// Ước tính neurons Workers AI cho một lượng ký tự chưa cache.
// [HYPOTHESIS] xem chú thích ở AI_NEURONS_PER_TOKEN — không phải số đo thật.
function estimateAiNeurons(chars) {
  const tokens = (chars / AI_CHARS_PER_TOKEN) * 2; // vào + ra
  return tokens * AI_NEURONS_PER_TOKEN;
}

// Gọi Workers AI (m2m100) dịch 1 chuỗi. env.AI.run trả {translated_text}.
// Thử lại tối đa 1 lần khi lỗi mạng/5xx. Cloudflare hết ngân sách ngày thì
// trả lỗi mã 4006 ("used up your daily free allocation") — đổi thành Error
// có .code = "ai_quota" để tầng trên trả quota_exceeded (429) cho client
// rơi về MyMemory + offline, không thử lại vô ích.
async function callWorkersAI(text, source, target, env) {
  let lastErr = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0) await sleep(1000);
    let res;
    try {
      res = await env.AI.run(AI_MODEL, {
        text, source_lang: source, target_lang: target,
      });
    } catch (e) {
      const msg = (e && e.message) ? e.message : String(e);
      if (/4006|daily free allocation/i.test(msg)) {
        const err = new Error(
          "Workers AI đã hết 10.000 neurons miễn phí của ngày (mã 4006).");
        err.code = "ai_quota";
        err.retryable = false;
        throw err;
      }
      lastErr = new Error("Workers AI lỗi: " + msg + ".");
      lastErr.retryable = true;
      continue;
    }
    const t = res && typeof res.translated_text === "string"
      ? res.translated_text : null;
    if (t == null) {
      lastErr = new Error("Workers AI trả về thiếu bản dịch.");
      lastErr.retryable = false;
      continue;
    }
    return t;
  }
  throw lastErr || new Error("Workers AI lỗi không rõ.");
}

// Dịch 1 chuỗi qua provider đã chọn.
async function translateOne(provider, text, source, target, env) {
  if (provider === "workers-ai") return callWorkersAI(text, source, target, env);
  return callGoogle(text, source, target, env.GOOGLE_API_KEY);
}

// Dịch batch qua provider đã chọn.
// Google: 1 lần gọi với q dạng mảng (như trước).
// Workers AI: TÁCH thành từng lần gọi 1 chuỗi (đặc tính m2m100: đoạn dài
// nhiều câu trong 1 lần gọi có thể bị nuốt mất câu) — chạy theo cụm tối đa
// AI_BATCH_CONCURRENCY lần gọi song song, giữ đúng thứ tự chuỗi.
async function translateBatch(provider, texts, source, target, env) {
  if (provider !== "workers-ai") {
    return callGoogleBatch(texts, source, target, env.GOOGLE_API_KEY);
  }
  const out = new Array(texts.length);
  for (let i = 0; i < texts.length; i += AI_BATCH_CONCURRENCY) {
    const chunk = texts.slice(i, i + AI_BATCH_CONCURRENCY);
    const got = await Promise.all(
      chunk.map((t) => callWorkersAI(t, source, target, env)));
    for (let j = 0; j < chunk.length; j++) out[i + j] = got[j];
  }
  return out;
}

// Lỗi từ provider dịch -> Response fail cho client (đồng nhất 2 provider).
// ai_quota (Cloudflare tự báo hết ngân sách ngày) thì trả quota_exceeded
// như hết hạn mức tháng để client rơi về MyMemory + offline.
function failProviderError(e, provider, monthUsed, monthlyLimit, aiExtra) {
  if (e && e.code === "ai_quota") {
    return fail(
      "quota_exceeded",
      e.message + " Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
      429,
      { month_used: monthUsed, month_limit: monthlyLimit, ...aiExtra },
    );
  }
  const label = provider === "workers-ai" ? "Workers AI" : "Google";
  const prefix = provider === "workers-ai" ? "ai" : "google";
  const status = (e && e.status === 429) ? 429 : 502;
  return fail(
    e && e.status === 429 ? prefix + "_rate_limited" : prefix + "_error",
    label + " lỗi: " + (e && e.message ? e.message : e) +
      " Client tự dùng MyMemory + từ điển offline.",
    status,
    { month_used: monthUsed, month_limit: monthlyLimit, ...aiExtra },
  );
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method === "GET") {
      // Kiểm tra nhanh worker đã deploy chưa (không tốn ký tự, không cần key).
      // Trả kèm trạng thái cấu hình để tab Dịch hiển thị đúng: worker đã
      // deploy nhưng chưa đặt key/KV thì config != "ok" (T1) — trước đây
      // GET luôn trả ok:true nên client không phân biệt được worker "đang
      // dùng" với worker "triển khai rồi nhưng chưa cấu hình xong".
      // provider = provider sẽ dùng khi dịch ("google" | "workers-ai" |
      // null) để tab Dịch cho biết đường dịch đang dùng (T1 Workers AI).
      const prov = resolveProvider(env);
      const config = !prov.provider ? "not_configured"
        : (!env.TRANSLATE_CACHE || !env.TRANSLATE_USAGE) ? "kv_not_configured" : "ok";
      let aiDailyUsed = null;
      if (prov.provider === "workers-ai" && env.TRANSLATE_USAGE) {
        try { aiDailyUsed = await kvFloat(env.TRANSLATE_USAGE, "ai-usage:" + dayKey()); }
        catch (e) { aiDailyUsed = null; }
      }
      return json({
        ok: true,
        service: "beartea-translate-proxy",
        version: 3,
        config,
        configured: config === "ok",
        provider: prov.provider,
        translate_provider: prov.want,
        monthly_limit: parseInt(env.MONTHLY_CHAR_LIMIT || String(DEFAULT_MONTHLY_LIMIT), 10),
        ai_daily_limit: AI_DAILY_NEURON_LIMIT,
        ai_daily_guard: parseInt(env.AI_DAILY_NEURON_GUARD || String(DEFAULT_AI_DAILY_NEURON_GUARD), 10) || DEFAULT_AI_DAILY_NEURON_GUARD,
        ai_daily_used: aiDailyUsed,
        note: "POST {text, target, source?} để dịch 1 chuỗi; POST {texts:[...], target, source?} để dịch batch (tối đa 100 chuỗi, tổng 5000 ký tự). Provider: TRANSLATE_PROVIDER=auto (Google nếu có key, nếu không Workers AI) | google | workers-ai.",
      });
    }
    if (request.method !== "POST") {
      return fail("method_not_allowed", "Chỉ hỗ trợ GET (kiểm tra) và POST (dịch).", 405);
    }
    // Chọn provider trước khi kiểm tra cấu hình: không có provider nào sẵn
    // sàng thì trả not_configured (503) kèm lý do rõ ràng cho từng đường.
    const resolved = resolveProvider(env);
    const provider = resolved.provider;
    if (!provider) {
      const detail = resolved.missing === "google_key"
        ? "TRANSLATE_PROVIDER=google nhưng chưa đặt secret GOOGLE_API_KEY (wrangler secret put GOOGLE_API_KEY)."
        : resolved.missing === "ai_binding"
        ? "TRANSLATE_PROVIDER=workers-ai nhưng worker chưa gắn binding AI ([ai] trong wrangler.toml) — deploy lại."
        : "Worker chưa cấu hình provider dịch: chưa đặt secret GOOGLE_API_KEY và chưa gắn binding AI. Đặt key Google hoặc deploy lại với [ai] trong wrangler.toml (xem docs/API_KEY_SETUP.md).";
      return fail("not_configured", detail, 503);
    }
    if (!env.TRANSLATE_CACHE || !env.TRANSLATE_USAGE) {
      return fail(
        "kv_not_configured",
        "Worker chưa gắn KV (TRANSLATE_CACHE / TRANSLATE_USAGE) — từ chối dịch để khỏi vượt hạn mức không kiểm soát.",
        503,
      );
    }
    const cache = env.TRANSLATE_CACHE;
    const usageKv = env.TRANSLATE_USAGE;
    const monthlyLimit = parseInt(
      env.MONTHLY_CHAR_LIMIT || String(DEFAULT_MONTHLY_LIMIT), 10) || DEFAULT_MONTHLY_LIMIT;
    const ratePerMinute = parseInt(
      env.RATE_LIMIT_PER_MINUTE || String(DEFAULT_RATE_PER_MINUTE), 10) || DEFAULT_RATE_PER_MINUTE;
    const breakerThreshold = parseInt(
      env.BREAKER_THRESHOLD || String(DEFAULT_BREAKER_THRESHOLD), 10) || DEFAULT_BREAKER_THRESHOLD;
    const breakerCooldownS = parseInt(
      env.BREAKER_COOLDOWN_S || String(DEFAULT_BREAKER_COOLDOWN_S), 10) || DEFAULT_BREAKER_COOLDOWN_S;
    const glossaryVersion = String(env.GLOSSARY_VERSION || DEFAULT_GLOSSARY_VERSION);
    // Workers AI: ngưỡng neurons/ngày worker tự dừng (mặc định 9.000, dưới
    // hạn miễn phí 10.000 của tài khoản). Chỉ đọc KV ngày khi provider là
    // workers-ai — đường Google không tốn thêm lần đọc nào.
    const aiGuard = parseInt(
      env.AI_DAILY_NEURON_GUARD || String(DEFAULT_AI_DAILY_NEURON_GUARD), 10) || DEFAULT_AI_DAILY_NEURON_GUARD;
    const aiUsageKey = "ai-usage:" + dayKey();
    let aiUsedToday = 0;
    if (provider === "workers-ai") {
      aiUsedToday = await kvFloat(usageKv, aiUsageKey);
    }
    const aiExtra = provider === "workers-ai"
      ? { ai_daily_used: aiUsedToday, ai_daily_limit: AI_DAILY_NEURON_LIMIT }
      : {};

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return fail("bad_request", "Body phải là JSON {text, target, source?} hoặc {texts:[...], target, source?}.", 400);
    }
    const target = body && body.target;
    const source = (body && body.source) || "en";
    if (typeof target !== "string" || !/^[a-z]{2,5}$/i.test(target)) {
      return fail("bad_request", "target phải là mã ngôn ngữ (vd: vi, en).", 400);
    }
    if (typeof source !== "string" || !/^[a-z]{2,5}$/i.test(source)) {
      return fail("bad_request", "source phải là mã ngôn ngữ (vd: en, vi).", 400);
    }
    // Batch: {texts:[...]} — mỗi chuỗi ≤2000 ký tự, tổng ≤5000 ký tự.
    const isBatch = Array.isArray(body && body.texts);
    let texts = null;
    let text = null;
    let chars = 0;
    if (isBatch) {
      texts = body.texts;
      if (!texts.length || texts.length > MAX_BATCH_TEXTS) {
        return fail("bad_request", "Batch cần 1–" + MAX_BATCH_TEXTS + " chuỗi.", 400);
      }
      for (const t of texts) {
        if (typeof t !== "string" || !t.trim()) {
          return fail("bad_request", "Mỗi chuỗi trong batch phải là text không rỗng.", 400);
        }
        if ([...t].length > MAX_BATCH_TEXT_CHARS) {
          return fail("bad_request", "Mỗi chuỗi trong batch tối đa " + MAX_BATCH_TEXT_CHARS + " ký tự.", 400);
        }
      }
      chars = texts.reduce((n, t) => n + [...t].length, 0);
      if (chars > MAX_TEXT_CHARS) {
        return fail("bad_request", "Batch tối đa " + MAX_TEXT_CHARS + " ký tự tổng cộng.", 400);
      }
    } else {
      text = body && body.text;
      if (typeof text !== "string" || !text.trim()) {
        return fail("bad_request", "Thiếu text (chuỗi cần dịch).", 400);
      }
      if ([...text].length > MAX_TEXT_CHARS) {
        return fail("bad_request", "Text quá dài (tối đa " + MAX_TEXT_CHARS + " ký tự/request).", 400);
      }
      chars = [...text].length;
    }

    // Giới hạn theo IP (chống 1 IP spam hết hạn mức tháng của mọi người).
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    const minuteBucket = Math.floor(Date.now() / 60000);
    const rlKey = "rl:" + ip + ":" + minuteBucket;
    const rlCount = await kvInt(usageKv, rlKey);
    if (rlCount >= ratePerMinute) {
      const retryAfterS = 60 - Math.floor((Date.now() % 60000) / 1000);
      return fail(
        "rate_limited",
        "Quá nhiều request từ IP này (" + ratePerMinute + "/phút) — thử lại sau " + retryAfterS + " giây.",
        429,
        { retry_after_s: retryAfterS },
      );
    }
    await usageKv.put(rlKey, String(rlCount + 1), { expirationTtl: 120 });

    // Ngắt mạch RIÊNG TỪNG PROVIDER: provider đang lỗi liên tiếp thì trả
    // ngay, khỏi gọi vô ích (Google lỗi không chặn Workers AI và ngược lại).
    const breakerKey = "breaker:" + provider;
    let breaker = { failures: 0, open_until: 0 };
    try {
      breaker = JSON.parse((await usageKv.get(breakerKey)) || "null") || breaker;
    } catch (e) { /* KV hỏng: coi như mạch đóng */ }
    if (Date.now() < (breaker.open_until || 0)) {
      const retryAfterS = Math.ceil(((breaker.open_until || 0) - Date.now()) / 1000);
      return fail(
        "breaker_open",
        (provider === "workers-ai" ? "Workers AI" : "Google") +
          " đang lỗi liên tiếp — ngắt mạch " + retryAfterS +
          " giây nữa, client tự dùng MyMemory + offline.",
        429,
        { retry_after_s: retryAfterS },
      );
    }

    const usageKey = "usage:" + monthKey();
    const monthUsed = await kvInt(usageKv, usageKey);

    // ---- Batch {texts:[...]}: cache từng chuỗi, gọi provider 1 lần cho
    // phần chưa cache (Google: 1 request mảng; Workers AI: N lần gọi 1
    // chuỗi), ghi cache từng kết quả. Hạn mức tháng chỉ tính ký tự CHƯA
    // cache (phần đã cache không tốn ký tự mới). Cache key GỒM provider +
    // model nên kết quả Google và Workers AI không bao giờ lẫn nhau.
    if (isBatch) {
      const keys = [];
      for (const t of texts) {
        keys.push("t:" + (await sha256Hex(
          [provider, modelFor(provider), t, source, target, glossaryVersion].join("\n"))));
      }
      const cachedVals = [];
      for (const k of keys) cachedVals.push(await cache.get(k));
      const needIdx = [];
      let uncachedChars = 0;
      for (let i = 0; i < texts.length; i++) {
        if (cachedVals[i] == null) {
          needIdx.push(i);
          uncachedChars += [...texts[i]].length;
        }
      }
      if (monthUsed + uncachedChars > monthlyLimit) {
        return fail(
          "quota_exceeded",
          "Đã hết " + monthlyLimit.toLocaleString("en-US") +
            " ký tự miễn phí của tháng " + monthKey() +
            " (đã dùng " + monthUsed.toLocaleString("en-US") +
            "). Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
          429,
          { month_used: monthUsed, month_limit: monthlyLimit, ...aiExtra },
        );
      }
      // Workers AI: ngân sách ngày (ước tính) — hết thì dừng trước khi gọi.
      const aiEstimate = provider === "workers-ai"
        ? estimateAiNeurons(uncachedChars) : 0;
      if (provider === "workers-ai" && aiUsedToday + aiEstimate > aiGuard) {
        return fail(
          "quota_exceeded",
          "Workers AI đã dùng ~" + Math.round(aiUsedToday).toLocaleString("en-US") +
            " neurons ước tính hôm nay (ngưỡng " + aiGuard.toLocaleString("en-US") +
            "/" + AI_DAILY_NEURON_LIMIT.toLocaleString("en-US") +
            " neurons/ngày). Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
          429,
          { month_used: monthUsed, month_limit: monthlyLimit,
            ai_daily_used: aiUsedToday, ai_daily_limit: AI_DAILY_NEURON_LIMIT },
        );
      }
      const out = cachedVals.slice();
      if (needIdx.length) {
        await usageKv.put(usageKey, String(monthUsed + uncachedChars));
        if (provider === "workers-ai") {
          await usageKv.put(aiUsageKey, String(aiUsedToday + aiEstimate),
            { expirationTtl: 2 * 24 * 3600 });
        }
        let got;
        try {
          got = await translateBatch(provider,
            needIdx.map((i) => texts[i]), source, target, env);
        } catch (e) {
          await usageKv.put(usageKey, String(monthUsed)); // hoàn lại
          if (provider === "workers-ai") {
            await usageKv.put(aiUsageKey, String(aiUsedToday),
              { expirationTtl: 2 * 24 * 3600 }); // hoàn lại neurons ước tính
          }
          const failures = (breaker.failures || 0) + 1;
          const openUntil = failures >= breakerThreshold
            ? Date.now() + breakerCooldownS * 1000 : 0;
          await usageKv.put(breakerKey,
            JSON.stringify({ failures, open_until: openUntil }));
          return failProviderError(e, provider, monthUsed, monthlyLimit, {
            ...aiExtra,
            ...(provider === "workers-ai"
              ? { ai_daily_used: aiUsedToday } : {}),
          });
        }
        await usageKv.put(breakerKey, JSON.stringify({ failures: 0, open_until: 0 }));
        for (let j = 0; j < needIdx.length; j++) {
          out[needIdx[j]] = got[j];
          await cache.put(keys[needIdx[j]], got[j],
            { expirationTtl: 30 * 24 * 3600 });
        }
      }
      return json({
        ok: true, translated: out, provider, chars,
        cached: needIdx.length === 0,
        month_used: monthUsed + uncachedChars, month_limit: monthlyLimit,
        ...(provider === "workers-ai"
          ? { ai_daily_used: aiUsedToday + aiEstimate,
              ai_daily_limit: AI_DAILY_NEURON_LIMIT } : {}),
      });
    }

    // Cache KV: cùng provider + model + text + ngôn ngữ + glossary_version
    // thì không gọi dịch, không tính ký tự.
    const cacheKey = "t:" + (await sha256Hex(
      [provider, modelFor(provider), text, source, target, glossaryVersion].join("\n")));
    const cached = await cache.get(cacheKey);
    if (cached != null) {
      return json({
        ok: true, translated: cached, provider, chars, cached: true,
        month_used: monthUsed, month_limit: monthlyLimit,
        ...(provider === "workers-ai"
          ? { ai_daily_used: aiUsedToday,
              ai_daily_limit: AI_DAILY_NEURON_LIMIT } : {}),
      });
    }

    // HẠN MỨC CỨNG: vượt 500.000 ký tự/tháng thì DỪNG, không gọi dịch.
    if (monthUsed + chars > monthlyLimit) {
      return fail(
        "quota_exceeded",
        "Đã hết " + monthlyLimit.toLocaleString("en-US") +
          " ký tự miễn phí của tháng " + monthKey() +
          " (đã dùng " + monthUsed.toLocaleString("en-US") +
          "). Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
        429,
        { month_used: monthUsed, month_limit: monthlyLimit, ...aiExtra },
      );
    }

    // Workers AI: ngân sách ngày (ước tính) — hết thì dừng trước khi gọi.
    const aiEstimate = provider === "workers-ai"
      ? estimateAiNeurons(chars) : 0;
    if (provider === "workers-ai" && aiUsedToday + aiEstimate > aiGuard) {
      return fail(
        "quota_exceeded",
        "Workers AI đã dùng ~" + Math.round(aiUsedToday).toLocaleString("en-US") +
          " neurons ước tính hôm nay (ngưỡng " + aiGuard.toLocaleString("en-US") +
          "/" + AI_DAILY_NEURON_LIMIT.toLocaleString("en-US") +
          " neurons/ngày). Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
        429,
        { month_used: monthUsed, month_limit: monthlyLimit,
          ai_daily_used: aiUsedToday, ai_daily_limit: AI_DAILY_NEURON_LIMIT },
      );
    }

    // Đặt trước ký tự (và neurons ước tính của Workers AI) rồi mới gọi
    // dịch; dịch lỗi thì hoàn lại.
    await usageKv.put(usageKey, String(monthUsed + chars));
    if (provider === "workers-ai") {
      await usageKv.put(aiUsageKey, String(aiUsedToday + aiEstimate),
        { expirationTtl: 2 * 24 * 3600 });
    }
    let translated;
    try {
      translated = await translateOne(provider, text, source, target, env);
    } catch (e) {
      await usageKv.put(usageKey, String(monthUsed)); // hoàn lại
      if (provider === "workers-ai") {
        await usageKv.put(aiUsageKey, String(aiUsedToday),
          { expirationTtl: 2 * 24 * 3600 }); // hoàn lại neurons ước tính
      }
      const failures = (breaker.failures || 0) + 1;
      const openUntil = failures >= breakerThreshold
        ? Date.now() + breakerCooldownS * 1000 : 0;
      await usageKv.put(breakerKey,
        JSON.stringify({ failures, open_until: openUntil }));
      return failProviderError(e, provider, monthUsed, monthlyLimit, {
        ...aiExtra,
        ...(provider === "workers-ai" ? { ai_daily_used: aiUsedToday } : {}),
      });
    }

    // Thành công: reset ngắt mạch, ghi cache (giữ 30 ngày).
    await usageKv.put(breakerKey, JSON.stringify({ failures: 0, open_until: 0 }));
    await cache.put(cacheKey, translated, { expirationTtl: 30 * 24 * 3600 });
    return json({
      ok: true, translated, provider, chars, cached: false,
      month_used: monthUsed + chars, month_limit: monthlyLimit,
      ...(provider === "workers-ai"
        ? { ai_daily_used: aiUsedToday + aiEstimate,
            ai_daily_limit: AI_DAILY_NEURON_LIMIT } : {}),
    });
  },
};
