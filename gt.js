// gt.js — Google Translate miễn phí qua fetch() trong Worker (bản web).
// Endpoint gtx (không key), cùng endpoint bản iSH dùng qua urllib.
// Chỉ dùng cho bản web; bản iSH vẫn dùng urllib trong sdk/translate.py.
//
// KIỂM CHỨNG LIVE 2026-11-16 (từ IP máy chủ, Node fetch, UA Safari iPhone):
//   * gtx KHÔNG nhận nhiều tham số q/request: GET/POST với 3q và 50q đều
//     trả HTTP 200 nhưng chỉ dịch q ĐẦU TIÊN, các q còn lại bị bỏ qua
//     lặng lẽ (response vẫn đúng dạng 1 chuỗi). Vì vậy batch KHÔNG dùng
//     multi-q — batch = gộp N chuỗi thành 1 q duy nhất, nối bằng "\n" với
//     marker đánh số ⟦1⟧, ⟦2⟧... rồi tách lại (googleTranslateBatch).
//     Kiểm chứng: joined 10 chuỗi -> tách lại 10/10, joined 50 -> 50/50.
//   * HTTP 429 + Sorry là NHẤT THỜI (theo đợt gửi dồn), không phải chặn
//     vĩnh viễn: cùng IP đó GET 1q/3q trả 200 sau vài phút. Vì vậy dùng
//     token bucket (tối đa 2 request dồn, nạp 1 token/500ms) + backoff khi
//     429, thay cho sleep 0,4s cố định bên Python (đã bỏ).
//   * POST form-urlencoded cũng trả 200 như GET (đã thử 1q/3q/50q/joined);
//     batch dùng POST để khỏi lo giới hạn độ dài URL.
//
// Lỗi nào cũng ném Error có .kind để Python phân loại và rơi về từ điển
// offline (không bao giờ làm hỏng đợt dịch):
//   "network" — fetch ném TypeError (mất mạng hoặc trình duyệt chặn CORS;
//               thử lại cũng vô ích nên ném ngay, không retry).
//   "timeout" — quá 20 giây.
//   "http"    — HTTP 429/5xx sau hết retry, hoặc HTTP khác 200.
//   "sorry"   — Google trả trang "Sorry..." (IP đang bị Google giới hạn).
//   "bad_response" — HTTP 200 nhưng JSON không đọc được / thiếu bản dịch.
//   "cancelled" — người dùng bấm Hủy (abort fetch đang chạy).
//   "quota"   — proxy báo hết hạn mức tháng (chỉ đường proxy).
//   "all_failed" — cả chuỗi fallback đều lỗi (lỗi này mang tóm tắt cả
//               chuỗi ở dòng đầu message để bridge/UI hiện rõ).
//   "batch_failed" — batch lỗi ở cả proxy + Google (bridge còn dịch lại
//               từng chuỗi, chưa phải hết đường).
//
// CHUỖI FALLBACK (mục C1, 2026-11-16) — translateOneWithFallbacks:
//   proxy (nếu có URL) → Google trực tiếp → MyMemory → LibreTranslate
//   (CHỈ khi đã cấu hình endpoint, mặc định TẮT) → từ điển offline.
//   LibreTranslate public (libretranslate.com) KHÔNG còn dùng được nếu
//   không có key: POST /translate không key trả HTTP 400 "Visit
//   https://portal.libretranslate.com to get an API key" (kiểm chứng độc
//   lập 2026-10-27; gói host từ $29/tháng), mirror argosopentech đã chết.
//   Vì vậy mặc định chuỗi vẫn là 3 bước (proxy/Google/MyMemory) + offline,
//   không thêm request thừa; ai tự host LibreTranslate hoặc có key portal
//   thì dán endpoint + key ở tab Dịch để bật bước thứ 4. Mọi bước đều ghi
//   vào trace (getFallbackTrace) để UI báo rõ bước nào đã dùng.
//
// Lưu ý trung thực: fetch() trong trình duyệt KHÔNG cho đặt User-Agent
// (header cấm — trình duyệt tự gửi UA thật của nó). Google có trả JSON
// hay không còn tùy IP: IP máy chủ (datacenter) thường bị 429 + Sorry;
// IP mạng di động/dân cư thường dịch được. Vì vậy mọi lỗi đều có
// fallback offline, và CORS trên Safari thật vẫn cần thử bằng tay.

export const GT_ENDPOINT = "https://translate.googleapis.com/translate_a/single";
// Ghi đè endpoint Google (chỉ harness kiểm chứng dùng để giả 429/500;
// chạy thật luôn dùng GT_ENDPOINT ở trên).
let googleEndpointOverride = "";
export function setGoogleEndpoint(url) {
  googleEndpointOverride = typeof url === "string" ? url.trim().replace(/\/+$/, "") : "";
}
export function getGoogleEndpoint() { return googleEndpointOverride || GT_ENDPOINT; }
const GT_TIMEOUT_MS = 20000;
const GT_MAX_RETRIES = 3;               // thử lại tối đa 3 lần (như bản iSH)
const GT_BACKOFF_MS = [1000, 2000, 4000];

// ---- Token bucket cho Google trực tiếp (thay sleep 0,4s cố định) ----
// Tối đa 2 request dồn, nạp lại 1 token/500ms. Với batch (1 request cho
// tới 100 chuỗi) thì tốc độ vẫn rất nhanh mà không dồn request gây 429.
// Chỉ áp cho đường Google trực tiếp (proxy/MyMemory có giới hạn riêng).
let gtTokens = 2;
let gtLastRefillMs = Date.now();
const GT_BUCKET_CAPACITY = 2;
const GT_BUCKET_REFILL_MS = 500;
async function gtThrottle() {
  for (;;) {
    const now = Date.now();
    const gained = Math.floor((now - gtLastRefillMs) / GT_BUCKET_REFILL_MS);
    if (gained > 0) {
      gtTokens = Math.min(GT_BUCKET_CAPACITY, gtTokens + gained);
      gtLastRefillMs += gained * GT_BUCKET_REFILL_MS;
    }
    if (gtTokens > 0) { gtTokens--; return; }
    await sleep(Math.max(50, GT_BUCKET_REFILL_MS - (now - gtLastRefillMs)));
  }
}

// ---- Cooldown khi Google 429/5xx liên tiếp (tăng tốc khi bị giới hạn) ----
// Đo thật (bench-gt S6): Google 429 mãi thì mỗi chuỗi tốn 4 lần gọi +
// backoff 1+2+4s (~8,6s/chuỗi) rồi mới rơi xuống MyMemory, mà bridge vẫn
// đếm là "thành công" (qua MyMemory) nên không có circuit breaker nào dừng
// được. 2 lần 429/5xx liên tiếp -> Google tạm nghỉ GT_COOL_MS; các lần gọi
// sau kiểm tra cooldown TRƯỚC khi chờ token bucket -> lỗi nhanh, rơi thẳng
// xuống MyMemory (~0,4s/chuỗi). 429 đơn lẻ vẫn retry bình thường (thành
// công reset streak về 0). Chỉ áp cho Google trực tiếp, proxy có giới
// hạn riêng (quota).
let gt429Streak = 0;
let gtCoolUntilMs = 0;
const GT_COOL_MS = 30000;
const GT_COOL_STREAK = 2;
function gtInCooldown() { return Date.now() < gtCoolUntilMs; }
function gtNoteHttpError() {
  gt429Streak++;
  if (gt429Streak >= GT_COOL_STREAK) gtCoolUntilMs = Date.now() + GT_COOL_MS;
}
function gtNoteSuccess() { gt429Streak = 0; }
function gtThrowIfCoolingDown() {
  if (gtInCooldown()) {
    throw gtError("http",
      "Google Translate giới hạn tốc độ (HTTP 429) — đang tạm nghỉ 30 giây, thử bước sau.",
      { status: 429 });
  }
}

// Reset trạng thái giới hạn tốc độ (cooldown Google + breaker MyMemory) —
// chỉ dùng cho harness/kiểm thử (giữa các scenario cần trạng thái sạch).
// Worker thật KHÔNG gọi: cooldown phải sống qua các chunk/job vì giới hạn
// là theo IP, không theo job.
export function resetGoogleRateLimitState() {
  gt429Streak = 0;
  gtCoolUntilMs = 0;
  mmFailStreak = 0;
  mmCoolUntilMs = 0;
}

// Chuẩn hoá mảng chuỗi từ Python: Pyodide truyền list Python sang JS
// dưới dạng JsProxy (Array.isArray=false nhưng có .length + chỉ số) chứ
// KHÔNG phải Array thật — mọi hàm batch phải Array.from() trước khi
// dùng .map/.length/Array.isArray, nếu không batch trả rỗng/lỗi và
// Python tự rơi về từng chuỗi (đã bắt được bằng harness trình duyệt:
// 100 chuỗi mất 25,7s thay vì ~1 batch).
function toTextArray(texts) {
  if (Array.isArray(texts)) return texts;
  try {
    const arr = Array.from(texts || []);
    return arr.every((t) => typeof t === "string") ? arr : null;
  } catch (_) { return null; }
}
// worker.js gọi abortGoogleTranslate() khi nhận {op:"cancel_translate"}:
// abort controller của fetch đang chạy (nếu có) + đặt cờ để lần AbortError
// đó được báo kind "cancelled" thay vì "timeout". Cờ được tiêu thụ ngay
// khi ném lỗi cancelled, và worker reset khi bắt đầu đợt dịch mới.
let gtAbortCtrl = null;
let gtUserAborted = false;
export function abortGoogleTranslate() {
  gtUserAborted = true;
  if (gtAbortCtrl) { try { gtAbortCtrl.abort(); } catch (_) {} }
}
export function resetGoogleTranslateAbort() { gtUserAborted = false; }
function consumeUserAbort() {
  if (!gtUserAborted) return false;
  gtUserAborted = false;
  return true;
}

// ---- Proxy dịch qua Cloudflare Worker (mục 2) ----
// URL worker do người dùng dán ở tab Dịch (app.js lưu localStorage
// "pb3_translate_proxy"). Để trống = gọi trực tiếp Google như trước.
let proxyUrl = "";
export function setTranslateProxyUrl(url) {
  proxyUrl = typeof url === "string" ? url.trim().replace(/\/+$/, "") : "";
}
export function getTranslateProxyUrl() { return proxyUrl; }

// MyMemory: dịch miễn phí, không key (giới hạn ~500 ký tự/request ẩn danh,
// chất lượng thấp hơn Google). Chỉ là tầng fallback giữa Google và từ điển
// offline — lỗi thì ném để bridge dùng từ điển offline. Endpoint cho phép
// ghi đè để harness kiểm chứng bằng server giả.
// Fail-fast: timeout 10s -> 5s (MyMemory thường trả <2s; treo 10s/chuỗi
// nhân 500 chuỗi là hàng giờ) + breaker: 3 lỗi liên tiếp -> bỏ qua
// MyMemory 60s, về offline nhanh (MyMemory sập thì chờ cũng vô ích).
const MYMEMORY_TIMEOUT_MS = 5000;
const MYMEMORY_MAX_CHARS = 500;
let mmFailStreak = 0;
let mmCoolUntilMs = 0;
const MM_COOL_MS = 60000;
const MM_COOL_STREAK = 3;
function mmNoteFail() {
  mmFailStreak++;
  if (mmFailStreak >= MM_COOL_STREAK) mmCoolUntilMs = Date.now() + MM_COOL_MS;
}
let myMemoryEndpoint = "https://api.mymemory.translated.net/get";
export function setMyMemoryEndpoint(url) { myMemoryEndpoint = url; }

// ---- LibreTranslate (mục C1, bước dự phòng sau MyMemory) ----
// KIỂM CHỨNG 2026-11-16 — bản public KHÔNG còn dùng được nếu không có key:
//   * GET https://libretranslate.com/languages từ Node: HTTP 200, có
//     ACAO:* (CORS không phải vấn đề), en→vi được hỗ trợ.
//   * POST /translate không key: bản public trả HTTP 400 "Visit
//     https://portal.libretranslate.com to get an API key" (kiểm chứng độc
//     lập 2026-10-27); gói host trả phí từ $29/tháng. Mirror
//     translate.argosopentech.com đã chết (không resolve/không nối được).
//   * Từ sandbox này cũng KHÔNG đo trực tiếp được: egress chặn mọi
//     POST /translate (policy_denied) — chỉ GET /languages là đi qua.
// Vì vậy bước này MẶC ĐỊNH TẮT (endpoint rỗng): chuỗi mặc định vẫn là
// proxy → Google → MyMemory → từ điển offline, không thêm request thừa.
// Chỉ bật khi người dùng tự cấu hình endpoint (+ key nếu instance của họ
// yêu cầu): instance LibreTranslate tự host (miễn phí, mã nguồn mở) hoặc
// key portal libretranslate.com. Cấu hình lưu ở localStorage tab Dịch
// (pb3_libretranslate_endpoint / pb3_libretranslate_key), gửi sang worker
// như URL proxy. Harness kiểm chứng bước này bằng server giả cùng API.
const LIBRETRANSLATE_TIMEOUT_MS = 15000;
const LIBRETRANSLATE_MAX_CHARS = 5000;
let libreTranslateEndpoint = "";
let libreTranslateApiKey = "";
export function setLibreTranslateEndpoint(url) {
  libreTranslateEndpoint = typeof url === "string" ? url.trim().replace(/\/+$/, "") : "";
}
export function getLibreTranslateEndpoint() { return libreTranslateEndpoint; }
export function setLibreTranslateApiKey(key) {
  libreTranslateApiKey = typeof key === "string" ? key.trim() : "";
}
export function getLibreTranslateApiKey() { return libreTranslateApiKey; }

export async function libreTranslateOne(text, src, dst) {
  if (!libreTranslateEndpoint) {
    throw gtError("libretranslate",
      "LibreTranslate chưa cấu hình (bản public cần API key — xem mục LibreTranslate trong gt.js).");
  }
  if ([...text].length > LIBRETRANSLATE_MAX_CHARS) {
    throw gtError("libretranslate", "Chuỗi quá dài cho LibreTranslate (>5000 ký tự).");
  }
  const body = { q: text, source: src, target: dst, format: "text" };
  if (libreTranslateApiKey) body.api_key = libreTranslateApiKey;
  let res;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), LIBRETRANSLATE_TIMEOUT_MS);
    try {
      res = await fetch(libreTranslateEndpoint + "/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw gtError("libretranslate", "LibreTranslate quá thời gian chờ (15 giây).", { errName: e.name });
    }
    throw gtError("libretranslate", "Không gọi được LibreTranslate (mất mạng hoặc chặn CORS).", { errName: e.name });
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* body không phải JSON */ }
  if (res.status === 400) {
    throw gtError("libretranslate",
      "LibreTranslate từ chối (HTTP 400): " +
      ((data && data.error) || "bản public cần API key — lấy key ở portal.libretranslate.com hoặc tự host instance riêng.") + ".",
      { status: res.status });
  }
  if (res.status === 403) {
    throw gtError("libretranslate",
      "LibreTranslate từ chối (HTTP 403): " +
      ((data && data.error) || "API key sai hoặc IP bị chặn.") + ".",
      { status: res.status });
  }
  if (res.status === 429) {
    const e = gtError("libretranslate", "LibreTranslate giới hạn tốc độ (HTTP 429).", { status: res.status });
    e.retryable = true;
    throw e;
  }
  if (!res.ok) {
    throw gtError("libretranslate", "LibreTranslate trả HTTP " + res.status + ".", { status: res.status });
  }
  if (!data || typeof data.translatedText !== "string") {
    throw gtError("libretranslate", "LibreTranslate trả về thiếu bản dịch.");
  }
  return data.translatedText;
}

// ---- Dấu vết chuỗi fallback (mục C1) ----
// Mỗi lần translateOne/BatchWithFallbacks chạy đều ghi lại từng bước đã
// thử (bước nào, thành công hay lỗi gì, có bị bỏ qua không) để UI/log báo
// rõ cho người dùng thay vì im lặng. worker.js công bố qua
// globalThis.pb3_fallback_trace để bridge.py đọc sau mỗi chuỗi.
// Lỗi cuối cùng (cả chuỗi đều fail) có kind "all_failed" (trừ quota thì
// giữ kind "quota" để bridge khóa Google) và message: dòng ĐẦU là tóm tắt
// gọn cả chuỗi (bridge chỉ lấy dòng đầu, tối đa 200 ký tự), các dòng sau
// là chi tiết từng bước.
let lastFallbackTrace = [];
export function getFallbackTrace() {
  return lastFallbackTrace.map((e) => ({ ...e }));
}
const FALLBACK_STEP_LABEL = {
  proxy: "Proxy", google: "Google", mymemory: "MyMemory",
  libretranslate: "LibreTranslate",
};
function formatFallbackTrace(trace) {
  const tried = (trace || []).filter((e) => !e.skipped);
  const parts = tried.map((e) =>
    FALLBACK_STEP_LABEL[e.step] + (e.ok ? " OK" : " lỗi " + (e.short || e.detail || "")));
  let line = parts.join(" → ");
  const ltSkipped = (trace || []).find((e) => e.skipped && e.step === "libretranslate" &&
    /chưa cấu hình/.test(e.detail || ""));
  if (ltSkipped) {
    line += " · LibreTranslate chưa cấu hình (cần API key)";
  }
  return line;
}
function fallbackSummary(trace) {
  const line = formatFallbackTrace(trace);
  return line +
    " — tất cả đều lỗi, dùng từ điển offline (~100 từ/cụm) nên hầu hết chuỗi sẽ giữ nguyên.";
}
function fallbackAllFailedError(trace, quotaHit, batch) {
  // Batch: lỗi batch CHƯA phải là hết đường — bridge còn dịch lại từng
  // chuỗi (đủ chuỗi fallback) nên tóm tắt phải nói rõ điều đó, không được
  // ghi "dùng từ điển offline" như thể đã hết cách.
  const summary = batch
    ? formatFallbackTrace(trace) +
      " — batch lỗi, sẽ dịch lại từng chuỗi (Google đơn → MyMemory → LibreTranslate nếu có, rồi từ điển offline)."
    : fallbackSummary(trace);
  const kind = quotaHit ? "quota" : (batch ? "batch_failed" : "all_failed");
  const e = gtError(kind, summary +
    "\n" + (trace || []).map((t) =>
      "- " + (FALLBACK_STEP_LABEL[t.step] || t.step) + ": " +
      (t.skipped ? "bỏ qua (" + t.detail + ")" : (t.ok ? "thành công" : t.detail))).join("\n"));
  e.fallbackTrace = trace;
  if (batch) {
    // Python chia đôi batch thử lại (split-on-failure) CHỈ khi lỗi là mất
    // marker (bad_response) — 429/quota thì chia cũng lỗi như cũ, không
    // chia. Đọc kind của bước google trong trace (noteErr đã ghi kind).
    const g = (trace || []).find((t) => t.step === "google");
    e.splitRetryable = !!(g && g.kind === "bad_response");
  }
  return e;
}

export async function myMemoryTranslateOne(text, src, dst) {
  if ([...text].length > MYMEMORY_MAX_CHARS) {
    throw gtError("mymemory", "Chuỗi quá dài cho MyMemory (>500 ký tự).");
  }
  if (Date.now() < mmCoolUntilMs) {
    throw gtError("mymemory",
      "MyMemory tạm nghỉ sau 3 lỗi liên tiếp — thử bước sau.");
  }
  const url = myMemoryEndpoint + "?" + new URLSearchParams({
    q: text, langpair: src + "|" + dst,
  }).toString();
  let res;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), MYMEMORY_TIMEOUT_MS);
    try {
      res = await fetch(url, { signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    mmNoteFail();
    if (e && e.name === "AbortError") {
      throw gtError("mymemory", "MyMemory quá thời gian chờ (5 giây).", { errName: e.name });
    }
    throw gtError("mymemory", "Không gọi được MyMemory (mất mạng hoặc chặn CORS).", { errName: e.name });
  }
  if (!res.ok) {
    mmNoteFail();
    throw gtError("mymemory", "MyMemory trả HTTP " + res.status + ".", { status: res.status });
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    mmNoteFail();
    throw gtError("mymemory", "MyMemory trả về dữ liệu không đọc được.");
  }
  if (Number(data && data.responseStatus) !== 200 ||
      !data || !data.responseData || typeof data.responseData.translatedText !== "string") {
    mmNoteFail();
    throw gtError("mymemory",
      "MyMemory lỗi: " + ((data && data.responseDetails) || ("status " + (data && data.responseStatus))));
  }
  mmFailStreak = 0;
  return data.responseData.translatedText;
}

// Dịch 1 chuỗi qua proxy Cloudflare Worker (xem cloudflare/worker.js).
// Proxy trả {ok:true, translated} hoặc {ok:false, code}:
//   quota_exceeded -> kind "quota" (hết 500.000 ký tự/tháng, KHÔNG thử
//                     Google trực tiếp nữa — rơi về MyMemory (+ LibreTranslate
//                     nếu có) + offline).
//   lỗi khác       -> kind "proxy" (gọi thử Google trực tiếp rồi MyMemory).
export async function proxyTranslateOne(text, src, dst, url) {
  const target = url || proxyUrl;
  if (!target) throw gtError("proxy", "Chưa cấu hình URL proxy dịch.");
  let res;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), GT_TIMEOUT_MS);
    try {
      res = await fetch(target, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, source: src, target: dst }),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw gtError("proxy", "Proxy dịch quá thời gian chờ (20 giây).", { errName: e.name });
    }
    throw gtError("proxy", "Không gọi được proxy dịch (mất mạng hoặc sai URL).", { errName: e.name });
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw gtError("proxy", "Proxy dịch trả về dữ liệu không đọc được (HTTP " + res.status + ").", { status: res.status });
  }
  if (data && data.ok === true && typeof data.translated === "string") {
    return data.translated;
  }
  const code = (data && data.code) || "proxy_error";
  if (code === "quota_exceeded") {
    throw gtError("quota",
      "Proxy dịch: đã hết 500.000 ký tự miễn phí của tháng " +
      "(đã dùng " + (data.month_used != null ? data.month_used : "?") +
      "/" + (data.month_limit != null ? data.month_limit : "?") +
      ") — tự rơi về MyMemory (+ LibreTranslate nếu đã cấu hình) + từ điển offline, không phát sinh phí.",
      { status: res.status });
  }
  throw gtError("proxy",
    "Proxy dịch lỗi (" + code + "): " + ((data && data.error) || ("HTTP " + res.status)) + ".",
    { status: res.status });
}

// Dịch 1 batch qua proxy Cloudflare Worker (xem cloudflare/worker.js):
// POST {texts:[...], source, target} -> {ok:true, translated:[...]}.
// Proxy (Google Cloud Translation v2) nhận q dạng mảng nên batch thật
// được — khác gtx miễn phí (chỉ dịch q đầu, xem googleTranslateBatch).
// Chưa deploy thì không dùng được (app.js chỉ gọi khi đã dán URL proxy);
// harness dùng proxy giả có cùng API.
export async function proxyTranslateBatch(texts, src, dst, url) {
  const target = url || proxyUrl;
  if (!target) throw gtError("proxy", "Chưa cấu hình URL proxy dịch.");
  const arr = toTextArray(texts);
  if (!arr || !arr.length || arr.length > 100) {
    throw gtError("proxy", "Batch proxy cần 1–100 chuỗi.");
  }
  const textsArr = arr;
  let res;
  try {
    const ctrl = new AbortController();
    gtAbortCtrl = ctrl;
    const timer = setTimeout(() => ctrl.abort(), GT_TIMEOUT_MS);
    try {
      res = await fetch(target, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ texts: textsArr, source: src, target: dst }),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
      if (gtAbortCtrl === ctrl) gtAbortCtrl = null;
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      if (consumeUserAbort()) throw gtError("cancelled", "Đã hủy dịch theo yêu cầu.", { errName: e.name });
      throw gtError("timeout", "Proxy dịch quá thời gian chờ (20 giây).", { errName: e.name });
    }
    throw gtError("proxy", "Không gọi được proxy dịch (mất mạng hoặc sai URL).", { errName: e.name });
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw gtError("proxy", "Proxy dịch trả về dữ liệu không đọc được (HTTP " + res.status + ").", { status: res.status });
  }
  if (data && data.ok === true && Array.isArray(data.translated) &&
      data.translated.length === textsArr.length &&
      data.translated.every((t) => typeof t === "string")) {
    return data.translated;
  }
  const code = (data && data.code) || "proxy_error";
  if (code === "quota_exceeded") {
    throw gtError("quota",
      "Proxy dịch: đã hết 500.000 ký tự miễn phí của tháng " +
      "(đã dùng " + (data.month_used != null ? data.month_used : "?") +
      "/" + (data.month_limit != null ? data.month_limit : "?") +
      ") — tự rơi về MyMemory (+ LibreTranslate nếu đã cấu hình) + từ điển offline, không phát sinh phí.",
      { status: res.status });
  }
  throw gtError("proxy",
    "Proxy dịch lỗi (" + code + "): " + ((data && data.error) || ("HTTP " + res.status)) + ".",
    { status: res.status });
}

// Chuỗi fallback đầy đủ mà worker.js gán cho Python (pb3_google_translate):
//   proxy (nếu có URL) -> Google trực tiếp -> MyMemory ->
//   LibreTranslate (CHỈ khi đã cấu hình endpoint, mặc định tắt vì bản
//   public cần API key — xem mục LibreTranslate ở trên) -> ném lỗi để
//   bridge dùng từ điển offline cho chuỗi đó.
// Riêng quota_exceeded thì BỎ QUA Google trực tiếp (đúng luật: hết hạn mức
// tháng thì chỉ còn MyMemory (+ LibreTranslate nếu có) + offline, không
// vòng lại Google bằng đường khác).
// Mỗi bước đều ghi vào lastFallbackTrace (xem getFallbackTrace): bước nào
// được dùng/thành công/lỗi gì đều đọc được từ worker/bridge để hiện UI.
export async function translateOneWithFallbacks(text, src, dst) {
  const trace = [];
  lastFallbackTrace = trace;
  let quotaHit = false;
  const note = (step, ok, detail, extra) => {
    const e = { step, ok, detail };
    if (extra) Object.assign(e, extra);
    trace.push(e);
  };
  // Mục C2: mỗi bước ghi thêm thời gian (ms), HTTP status và tên lỗi gốc
  // (errName) / kind để trang Chẩn đoán hiện lần gọi gần nhất.
  const noteOk = (step, detail, t0) =>
    note(step, true, detail, { ms: Math.round(performance.now() - t0) });
  const noteErr = (step, e, t0, shortText) =>
    note(step, false, e.message, { short: shortText,
      ms: Math.round(performance.now() - t0),
      status: e.status, errName: e.errName, kind: e.kind });
  const short = (msg) => {
    if (/API key/i.test(String(msg))) return "cần API key";
    const m = String(msg || "").match(/HTTP \d+/);
    if (m) return m[0];
    if (/Sorry/i.test(String(msg))) return "trang Sorry";
    if (/quá thời gian/i.test(String(msg))) return "quá thời gian chờ";
    if (/mất mạng|CORS/i.test(String(msg))) return "mất mạng/CORS";
    return String(msg || "").split("\n")[0].slice(0, 40) || "?";
  };
  if (proxyUrl) {
    const t0 = performance.now();
    try {
      const out = await proxyTranslateOne(text, src, dst, proxyUrl);
      noteOk("proxy", "dịch qua proxy", t0);
      return out;
    } catch (e) {
      if (e && e.kind === "quota") quotaHit = true;
      noteErr("proxy", e, t0, e.kind === "quota" ? "hết hạn mức tháng" : short(e.message));
    }
  }
  if (!quotaHit) {
    const t0 = performance.now();
    try {
      const out = await googleTranslateOne(text, src, dst);
      noteOk("google", "dịch qua Google", t0);
      return out;
    } catch (e) {
      noteErr("google", e, t0, short(e.message));
    }
  }
  {
    const t0 = performance.now();
    try {
      const out = await myMemoryTranslateOne(text, src, dst);
      noteOk("mymemory", "dịch qua MyMemory", t0);
      return out;
    } catch (e) {
      noteErr("mymemory", e, t0, short(e.message));
    }
  }
  if (libreTranslateEndpoint) {
    const t0 = performance.now();
    try {
      const out = await libreTranslateOne(text, src, dst);
      noteOk("libretranslate", "dịch qua LibreTranslate", t0);
      return out;
    } catch (e) {
      noteErr("libretranslate", e, t0, short(e.message));
    }
  } else {
    note("libretranslate", false,
      "chưa cấu hình endpoint (bản public cần API key)", { skipped: true });
  }
  throw fallbackAllFailedError(trace, quotaHit);
}

// Dịch 1 batch (mảng chuỗi) với fallback: proxy (nếu có URL) -> Google
// trực tiếp (gtx, joined-text) -> ném lỗi để Python dịch lại từng chuỗi
// (proxy đơn -> Google đơn -> MyMemory -> LibreTranslate nếu có -> offline).
// MyMemory/LibreTranslate không có API batch nên KHÔNG thử ở tầng batch:
// bridge rơi về từng chuỗi cho batch đó, và từng chuỗi đó đi qua đủ chuỗi
// fallback (kể cả LibreTranslate). Trace batch ghi rõ điều này.
// Trả về CHUỖI JSON {"translations":[...], "mode":"proxy-batch"|"gtx-joined"}
// (không trả mảng JS trực tiếp: chuỗi JSON qua Pyodide thành str Python
// luôn, khỏi phụ thuộc JsProxy/to_py theo phiên bản Pyodide).
export async function translateBatchWithFallbacks(texts, src, dst) {
  const arr = toTextArray(texts);
  if (!arr || !arr.length) {
    return JSON.stringify({ translations: [], mode: "empty" });
  }
  const trace = [];
  lastFallbackTrace = trace;
  let quotaHit = false;
  const note = (step, ok, detail, extra) => {
    const e = { step, ok, detail };
    if (extra) Object.assign(e, extra);
    trace.push(e);
  };
  // Mục C2: như bản đơn — mỗi bước ghi thêm ms / status / errName / kind.
  const noteOk = (step, detail, t0) =>
    note(step, true, detail, { ms: Math.round(performance.now() - t0) });
  const noteErr = (step, e, t0, shortText) =>
    note(step, false, e.message, { short: shortText,
      ms: Math.round(performance.now() - t0),
      status: e.status, errName: e.errName, kind: e.kind });
  const short = (msg) => {
    if (/API key/i.test(String(msg))) return "cần API key";
    const m = String(msg || "").match(/HTTP \d+/);
    if (m) return m[0];
    if (/Sorry/i.test(String(msg))) return "trang Sorry";
    return String(msg || "").split("\n")[0].slice(0, 40) || "?";
  };
  if (proxyUrl) {
    const t0 = performance.now();
    try {
      const out = await proxyTranslateBatch(arr, src, dst, proxyUrl);
      noteOk("proxy", "dịch batch qua proxy", t0);
      return JSON.stringify({ translations: out, mode: "proxy-batch" });
    } catch (e) {
      if (e && e.kind === "quota") quotaHit = true;
      noteErr("proxy", e, t0, e.kind === "quota" ? "hết hạn mức tháng" : short(e.message));
    }
  }
  if (!quotaHit) {
    const t0 = performance.now();
    try {
      const out = await googleTranslateBatch(arr, src, dst);
      noteOk("google", "dịch batch qua Google", t0);
      return JSON.stringify({ translations: out, mode: "gtx-joined" });
    } catch (e) {
      noteErr("google", e, t0, short(e.message));
    }
  }
  note("mymemory", false,
    "không thử ở tầng batch — sẽ thử theo từng chuỗi ở bước sau", { skipped: true });
  note("libretranslate", false,
    libreTranslateEndpoint
      ? "không thử ở tầng batch — sẽ thử theo từng chuỗi ở bước sau"
      : "chưa cấu hình endpoint (bản public cần API key)",
    { skipped: true });
  throw fallbackAllFailedError(trace, quotaHit, true);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function gtError(kind, message, extra) {
  const e = new Error(message);
  e.kind = kind;
  // Mục C2: gắn thêm thông tin để trace chẩn đoán ghi được HTTP status /
  // tên lỗi gốc (errName) — bridge/UI chỉ đọc kind/message nên không đổi
  // hành vi fallback.
  if (extra) Object.assign(e, extra);
  return e;
}

// Đọc bản dịch từ JSON gtx (1 q -> đúng 1 khối), cùng logic với
// sdk/translate.py::_parse_gt_response của PB3.
export function parseGtResponse(data) {
  if (!Array.isArray(data) || !data.length) return null;
  const parts = [];
  for (const block of data) {
    if (!Array.isArray(block) || !block.length) break;
    const first = block[0];
    if (!Array.isArray(first) || !first.length || typeof first[0] !== "string") break;
    parts.push(block
      .filter((s) => Array.isArray(s) && s.length && typeof s[0] === "string")
      .map((s) => s[0]).join(""));
  }
  if (parts.length !== 1) return null;
  return parts[0];
}

// Một lần gọi gtx (GET cho 1 chuỗi, POST form cho batch joined-text).
// Ném gtError có .kind; lỗi 429/5xx gắn .retryable để thử lại.
// AbortError: phân biệt Hủy của user (kind "cancelled") với hết 20 giây
// (kind "timeout") bằng cờ gtUserAborted.
async function gtxFetchOnce(q, usePost, src, dst) {
  let res;
  try {
    const ctrl = new AbortController();
    gtAbortCtrl = ctrl;
    const timer = setTimeout(() => ctrl.abort(), GT_TIMEOUT_MS);
    try {
      if (usePost) {
        const body = new URLSearchParams({
          client: "gtx", sl: src, tl: dst, dt: "t",
        });
        body.append("q", q);
        res = await fetch(getGoogleEndpoint(), {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            Accept: "application/json, */*",
          },
          body: body.toString(),
          signal: ctrl.signal,
        });
      } else {
        const url = getGoogleEndpoint() + "?" + new URLSearchParams({
          client: "gtx", sl: src, tl: dst, dt: "t", q,
        }).toString();
        res = await fetch(url, {
          headers: { Accept: "application/json, */*" },
          signal: ctrl.signal,
        });
      }
    } finally {
      clearTimeout(timer);
      if (gtAbortCtrl === ctrl) gtAbortCtrl = null;
    }
  } catch (e) {
    if (e && e.name === "AbortError") {
      if (consumeUserAbort()) throw gtError("cancelled", "Đã hủy dịch theo yêu cầu.", { errName: e.name });
      throw gtError("timeout", "Google Translate quá thời gian chờ (20 giây).", { errName: e.name });
    }
    throw gtError("network",
      "Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS).",
      { errName: e.name });
  }
  if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
    gtNoteHttpError();
    const e = gtError("http",
      "Google Translate giới hạn tốc độ (HTTP " + res.status + ").",
      { status: res.status });
    e.retryable = true;
    throw e;
  }
  if (!res.ok) {
    throw gtError("http", "Google Translate trả HTTP " + res.status + ".",
      { status: res.status });
  }
  const body = await res.text();
  const trimmed = body.trimStart();
  if (trimmed.startsWith("<") || /<title>Sorry/i.test(body)) {
    throw gtError("sorry",
      "Google trả trang 'Sorry...' — IP này đang bị Google giới hạn, không dịch được.");
  }
  let data;
  try {
    data = JSON.parse(body);
  } catch (e) {
    throw gtError("bad_response",
      "Google Translate trả về dữ liệu không đọc được.");
  }
  const got = parseGtResponse(data);
  if (got == null) {
    throw gtError("bad_response", "Google Translate trả về thiếu bản dịch.");
  }
  gtNoteSuccess();
  return got;
}

// Thử lại tối đa GT_MAX_RETRIES lần với backoff khi 429/5xx; lỗi khác
// (mất mạng, CORS, Sorry, JSON hỏng, Hủy) ném ngay. Cooldown: nếu Google
// đang tạm nghỉ (2 lần 429 liên tiếp trước đó) thì lỗi ngay KHÔNG gọi,
// để caller rơi nhanh xuống MyMemory thay vì đốt 4 lần gọi + backoff.
async function gtxWithRetry(attemptFn) {
  let lastErr = null;
  for (let attempt = 0; attempt <= GT_MAX_RETRIES; attempt++) {
    gtThrowIfCoolingDown();
    if (attempt > 0) {
      await sleep(GT_BACKOFF_MS[Math.min(attempt - 1, GT_BACKOFF_MS.length - 1)]);
    }
    try {
      return await attemptFn();
    } catch (e) {
      if (e && e.retryable && attempt < GT_MAX_RETRIES) {
        lastErr = e;
        continue;
      }
      throw e;
    }
  }
  throw lastErr || gtError("http", "Google Translate giới hạn tốc độ, thử lại sau.");
}

// Dịch 1 chuỗi (1 request). Token bucket giới hạn tốc độ gọi; 429/5xx
// thì thử lại với backoff; lỗi khác ném ngay để Python dùng offline.
// Cooldown được kiểm tra TRƯỚC khi chờ token (khỏi đốt 500ms vô ích).
export async function googleTranslateOne(text, src, dst) {
  gtThrowIfCoolingDown();
  await gtThrottle();
  return gtxWithRetry(() => gtxFetchOnce(text, false, src, dst));
}

// Tách bản dịch joined-text về đúng N chuỗi theo marker ⟦n⟧.
// Trả mảng N chuỗi, hoặc null khi marker thiếu/trùng/rỗng (Google làm
// mất marker) — caller ném bad_response để Python dịch lại từng chuỗi.
export function splitJoinedBatch(translated, n) {
  if (typeof translated !== "string" || n <= 0) return null;
  const parts = translated.split(/⟦\s*(\d+)\s*⟧/);
  const out = new Array(n).fill(null);
  let seen = 0;
  for (let i = 1; i + 1 < parts.length; i += 2) {
    const idx = parseInt(parts[i], 10) - 1;
    if (idx < 0 || idx >= n || out[idx] !== null) return null;
    const text = parts[i + 1].trim();
    if (!text) return null;
    out[idx] = text;
    seen++;
  }
  if (seen !== n) return null;
  return out;
}

// Dịch 1 batch N chuỗi bằng 1 request gtx duy nhất: gộp các chuỗi thành
// 1 q ("⟦1⟧ chuỗi1\n⟦2⟧ chuỗi2..."), dịch 1 lần, tách lại theo marker.
// (gtx bỏ qua q thứ 2 trở đi nên KHÔNG dùng multi-q — đã kiểm chứng live
// 2026-11-16; joined 10 -> 10/10, joined 50 -> 50/50.)
// 1 chuỗi thì dịch thẳng như googleTranslateOne. Marker tách không đủ
// thì ném bad_response để Python rơi về từng chuỗi cho batch đó.
export async function googleTranslateBatch(texts, src, dst) {
  const arr = toTextArray(texts);
  if (!arr || !arr.length) return [];
  gtThrowIfCoolingDown();
  await gtThrottle();
  if (arr.length === 1) {
    const one = await gtxWithRetry(() => gtxFetchOnce(arr[0], false, src, dst));
    return [one];
  }
  const joined = arr.map((t, i) => "⟦" + (i + 1) + "⟧ " + t).join("\n");
  const got = await gtxWithRetry(() => gtxFetchOnce(joined, true, src, dst));
  const out = splitJoinedBatch(got, arr.length);
  if (!out) {
    throw gtError("bad_response",
      "Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.");
  }
  return out;
}
