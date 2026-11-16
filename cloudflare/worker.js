// cloudflare/worker.js — Proxy dịch cho BearTea (bản web PB3).
//
// Mục đích: trình duyệt (Safari iPhone) gọi trực tiếp Google Translate hay
// bị chặn CORS / 429 theo IP. Worker này đứng giữa: nhận text từ trình
// duyệt, gọi Google Cloud Translation API v2 bằng API key (lưu trong secret
// của worker, KHÔNG BAO GIỜ ghi vào code hay repo), trả bản dịch về.
//
// AN TOÀN PHÍ — GIỚI HẠN CỨNG, KHÔNG BAO GIỜ TỰ PHÁT SINH PHÍ:
//   * Hạn mức tháng: tối đa MONTHLY_CHAR_LIMIT ký tự/tháng (mặc định
//     500.000 = đúng phần miễn phí của Google Cloud Translation).
//     Đếm trong KV theo tháng UTC (key usage:YYYY-MM). Hết hạn mức thì trả
//     {ok:false, code:"quota_exceeded"} để client tự rơi về MyMemory +
//     từ điển offline — worker KHÔNG gọi Google nữa trong tháng đó.
//   * Ký tự được "đặt trước" (reserve) TRƯỚC khi gọi Google và hoàn lại
//     (refund) nếu Google lỗi, nên hạn mức không bị vượt vì lỗi.
//   * Cache KV: cùng text + ngôn ngữ + glossary_version thì trả lại từ
//     cache, không gọi Google, không tính ký tự.
//   * Giới hạn theo IP: RATE_LIMIT_PER_MINUTE request/phút/IP.
//   * Ngắt mạch (circuit breaker): Google lỗi liên tiếp đủ
//     BREAKER_THRESHOLD lần thì mở mạch BREAKER_COOLDOWN_S giây, trong thời
//     gian đó trả ngay {code:"breaker_open"} thay vì gọi Google vô ích.
//   * Tôn trọng Retry-After của Google + backoff cấp số nhân khi 429/5xx.
//
// API:
//   GET  /  -> {ok:true, service:"beartea-translate-proxy", ...} (kiểm tra
//               worker đã deploy, không tốn ký tự)
//   POST /  {text, target, source?} ->
//     OK:     {ok:true, translated, provider:"google", chars, cached,
//              month_used, month_limit}
//     Hết hạn mức: {ok:false, code:"quota_exceeded", month_used, month_limit,
//              fallback:"mymemory+offline"} (HTTP 429)
//     Lỗi khác: {ok:false, code, error, provider:"none",
//              fallback:"mymemory+offline"}
//
// Triển khai: xem cloudflare/README.md (cần tài khoản Cloudflare của Joseph
// + API key Google — KHÔNG có key nào trong repo này).

const GOOGLE_API = "https://translation.googleapis.com/language/translate/v2";
const MAX_TEXT_CHARS = 5000; // Google v2: tối đa 5000 ký tự/request

const DEFAULT_MONTHLY_LIMIT = 500000;
const DEFAULT_RATE_PER_MINUTE = 60;
const DEFAULT_BREAKER_THRESHOLD = 5;
const DEFAULT_BREAKER_COOLDOWN_S = 60;
const DEFAULT_GLOSSARY_VERSION = "1";

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

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method === "GET") {
      // Kiểm tra nhanh worker đã deploy chưa (không tốn ký tự, không cần key).
      return json({
        ok: true,
        service: "beartea-translate-proxy",
        version: 1,
        monthly_limit: parseInt(env.MONTHLY_CHAR_LIMIT || String(DEFAULT_MONTHLY_LIMIT), 10),
        note: "POST {text, target, source?} để dịch.",
      });
    }
    if (request.method !== "POST") {
      return fail("method_not_allowed", "Chỉ hỗ trợ GET (kiểm tra) và POST (dịch).", 405);
    }
    if (!env.GOOGLE_API_KEY) {
      return fail(
        "not_configured",
        "Worker chưa cấu hình GOOGLE_API_KEY (wrangler secret put GOOGLE_API_KEY).",
        503,
      );
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

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return fail("bad_request", "Body phải là JSON {text, target, source?}.", 400);
    }
    const text = body && body.text;
    const target = body && body.target;
    const source = (body && body.source) || "en";
    if (typeof text !== "string" || !text.trim()) {
      return fail("bad_request", "Thiếu text (chuỗi cần dịch).", 400);
    }
    if ([...text].length > MAX_TEXT_CHARS) {
      return fail("bad_request", "Text quá dài (tối đa " + MAX_TEXT_CHARS + " ký tự/request).", 400);
    }
    if (typeof target !== "string" || !/^[a-z]{2,5}$/i.test(target)) {
      return fail("bad_request", "target phải là mã ngôn ngữ (vd: vi, en).", 400);
    }
    if (typeof source !== "string" || !/^[a-z]{2,5}$/i.test(source)) {
      return fail("bad_request", "source phải là mã ngôn ngữ (vd: en, vi).", 400);
    }
    const chars = [...text].length;

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

    // Ngắt mạch: Google đang lỗi liên tiếp thì trả ngay, khỏi gọi vô ích.
    let breaker = { failures: 0, open_until: 0 };
    try {
      breaker = JSON.parse((await usageKv.get("breaker")) || "null") || breaker;
    } catch (e) { /* KV hỏng: coi như mạch đóng */ }
    if (Date.now() < (breaker.open_until || 0)) {
      const retryAfterS = Math.ceil(((breaker.open_until || 0) - Date.now()) / 1000);
      return fail(
        "breaker_open",
        "Google đang lỗi liên tiếp — ngắt mạch " + retryAfterS + " giây nữa, client tự dùng MyMemory + offline.",
        429,
        { retry_after_s: retryAfterS },
      );
    }

    const usageKey = "usage:" + monthKey();
    const monthUsed = await kvInt(usageKv, usageKey);

    // Cache KV: cùng text + ngôn ngữ + glossary_version thì không gọi
    // Google, không tính ký tự.
    const cacheKey = "t:" + (await sha256Hex(
      [text, source, target, glossaryVersion].join("\n")));
    const cached = await cache.get(cacheKey);
    if (cached != null) {
      return json({
        ok: true, translated: cached, provider: "google", chars, cached: true,
        month_used: monthUsed, month_limit: monthlyLimit,
      });
    }

    // HẠN MỨC CỨNG: vượt 500.000 ký tự/tháng thì DỪNG, không gọi Google.
    if (monthUsed + chars > monthlyLimit) {
      return fail(
        "quota_exceeded",
        "Đã hết " + monthlyLimit.toLocaleString("en-US") +
          " ký tự miễn phí của tháng " + monthKey() +
          " (đã dùng " + monthUsed.toLocaleString("en-US") +
          "). Client tự rơi về MyMemory + từ điển offline, không phát sinh phí.",
        429,
        { month_used: monthUsed, month_limit: monthlyLimit },
      );
    }

    // Đặt trước ký tự rồi mới gọi Google; Google lỗi thì hoàn lại.
    await usageKv.put(usageKey, String(monthUsed + chars));
    let translated;
    try {
      translated = await callGoogle(text, source, target, env.GOOGLE_API_KEY);
    } catch (e) {
      await usageKv.put(usageKey, String(monthUsed)); // hoàn lại
      const failures = (breaker.failures || 0) + 1;
      const openUntil = failures >= breakerThreshold
        ? Date.now() + breakerCooldownS * 1000 : 0;
      await usageKv.put("breaker",
        JSON.stringify({ failures, open_until: openUntil }));
      const status = (e && e.status === 429) ? 429 : 502;
      return fail(
        e && e.status === 429 ? "google_rate_limited" : "google_error",
        "Google lỗi: " + (e && e.message ? e.message : e) +
          " Client tự dùng MyMemory + từ điển offline.",
        status,
        { month_used: monthUsed, month_limit: monthlyLimit },
      );
    }

    // Thành công: reset ngắt mạch, ghi cache (giữ 30 ngày).
    await usageKv.put("breaker", JSON.stringify({ failures: 0, open_until: 0 }));
    await cache.put(cacheKey, translated, { expirationTtl: 30 * 24 * 3600 });
    return json({
      ok: true, translated, provider: "google", chars, cached: false,
      month_used: monthUsed + chars, month_limit: monthlyLimit,
    });
  },
};
