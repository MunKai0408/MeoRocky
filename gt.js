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
//
// Lưu ý trung thực: fetch() trong trình duyệt KHÔNG cho đặt User-Agent
// (header cấm — trình duyệt tự gửi UA thật của nó). Google có trả JSON
// hay không còn tùy IP: IP máy chủ (datacenter) thường bị 429 + Sorry;
// IP mạng di động/dân cư thường dịch được. Vì vậy mọi lỗi đều có
// fallback offline, và CORS trên Safari thật vẫn cần thử bằng tay.

export const GT_ENDPOINT = "https://translate.googleapis.com/translate_a/single";
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
const MYMEMORY_TIMEOUT_MS = 10000;
const MYMEMORY_MAX_CHARS = 500;
let myMemoryEndpoint = "https://api.mymemory.translated.net/get";
export function setMyMemoryEndpoint(url) { myMemoryEndpoint = url; }

export async function myMemoryTranslateOne(text, src, dst) {
  if ([...text].length > MYMEMORY_MAX_CHARS) {
    throw gtError("mymemory", "Chuỗi quá dài cho MyMemory (>500 ký tự).");
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
    if (e && e.name === "AbortError") {
      throw gtError("mymemory", "MyMemory quá thời gian chờ (10 giây).");
    }
    throw gtError("mymemory", "Không gọi được MyMemory (mất mạng hoặc chặn CORS).");
  }
  if (!res.ok) {
    throw gtError("mymemory", "MyMemory trả HTTP " + res.status + ".");
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw gtError("mymemory", "MyMemory trả về dữ liệu không đọc được.");
  }
  if (Number(data && data.responseStatus) !== 200 ||
      !data || !data.responseData || typeof data.responseData.translatedText !== "string") {
    throw gtError("mymemory",
      "MyMemory lỗi: " + ((data && data.responseDetails) || ("status " + (data && data.responseStatus))));
  }
  return data.responseData.translatedText;
}

// Dịch 1 chuỗi qua proxy Cloudflare Worker (xem cloudflare/worker.js).
// Proxy trả {ok:true, translated} hoặc {ok:false, code}:
//   quota_exceeded -> kind "quota" (hết 500.000 ký tự/tháng, KHÔNG thử
//                     Google trực tiếp nữa — rơi về MyMemory + offline).
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
      throw gtError("proxy", "Proxy dịch quá thời gian chờ (20 giây).");
    }
    throw gtError("proxy", "Không gọi được proxy dịch (mất mạng hoặc sai URL).");
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw gtError("proxy", "Proxy dịch trả về dữ liệu không đọc được (HTTP " + res.status + ").");
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
      ") — tự rơi về MyMemory + từ điển offline, không phát sinh phí.");
  }
  throw gtError("proxy",
    "Proxy dịch lỗi (" + code + "): " + ((data && data.error) || ("HTTP " + res.status)) + ".");
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
      if (consumeUserAbort()) throw gtError("cancelled", "Đã hủy dịch theo yêu cầu.");
      throw gtError("timeout", "Proxy dịch quá thời gian chờ (20 giây).");
    }
    throw gtError("proxy", "Không gọi được proxy dịch (mất mạng hoặc sai URL).");
  }
  let data;
  try {
    data = await res.json();
  } catch (e) {
    throw gtError("proxy", "Proxy dịch trả về dữ liệu không đọc được (HTTP " + res.status + ").");
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
      ") — tự rơi về MyMemory + từ điển offline, không phát sinh phí.");
  }
  throw gtError("proxy",
    "Proxy dịch lỗi (" + code + "): " + ((data && data.error) || ("HTTP " + res.status)) + ".");
}

// Chuỗi fallback đầy đủ mà worker.js gán cho Python (pb3_google_translate):
//   proxy (nếu có URL) -> Google trực tiếp -> MyMemory -> ném lỗi để
//   bridge dùng từ điển offline cho chuỗi đó.
// Riêng quota_exceeded thì BỎ QUA Google trực tiếp (đúng luật: hết hạn mức
// tháng thì chỉ còn MyMemory + offline, không vòng lại Google bằng đường khác).
export async function translateOneWithFallbacks(text, src, dst) {
  let lastErr = null;
  let quotaHit = false;
  if (proxyUrl) {
    try {
      return await proxyTranslateOne(text, src, dst, proxyUrl);
    } catch (e) {
      if (e && e.kind === "quota") quotaHit = true;
      lastErr = e;
    }
  }
  if (!quotaHit) {
    try {
      return await googleTranslateOne(text, src, dst);
    } catch (e) {
      lastErr = e;
    }
  }
  try {
    return await myMemoryTranslateOne(text, src, dst);
  } catch (e) {
    lastErr = e;
  }
  throw lastErr || gtError("network", "Dịch lỗi không rõ.");
}

// Dịch 1 batch (mảng chuỗi) với fallback: proxy (nếu có URL) -> Google
// trực tiếp (gtx, joined-text) -> ném lỗi để Python dịch lại từng chuỗi
// (proxy đơn -> Google đơn -> MyMemory -> offline).
// Trả về CHUỖI JSON {"translations":[...], "mode":"proxy-batch"|"gtx-joined"}
// (không trả mảng JS trực tiếp: chuỗi JSON qua Pyodide thành str Python
// luôn, khỏi phụ thuộc JsProxy/to_py theo phiên bản Pyodide).
export async function translateBatchWithFallbacks(texts, src, dst) {
  const arr = toTextArray(texts);
  if (!arr || !arr.length) {
    return JSON.stringify({ translations: [], mode: "empty" });
  }
  let lastErr = null;
  let quotaHit = false;
  if (proxyUrl) {
    try {
      const out = await proxyTranslateBatch(arr, src, dst, proxyUrl);
      return JSON.stringify({ translations: out, mode: "proxy-batch" });
    } catch (e) {
      if (e && e.kind === "quota") quotaHit = true;
      lastErr = e;
    }
  }
  if (!quotaHit) {
    try {
      const out = await googleTranslateBatch(arr, src, dst);
      return JSON.stringify({ translations: out, mode: "gtx-joined" });
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || gtError("network", "Dịch batch lỗi không rõ.");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function gtError(kind, message) {
  const e = new Error(message);
  e.kind = kind;
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
        res = await fetch(GT_ENDPOINT, {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            Accept: "application/json, */*",
          },
          body: body.toString(),
          signal: ctrl.signal,
        });
      } else {
        const url = GT_ENDPOINT + "?" + new URLSearchParams({
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
      if (consumeUserAbort()) throw gtError("cancelled", "Đã hủy dịch theo yêu cầu.");
      throw gtError("timeout", "Google Translate quá thời gian chờ (20 giây).");
    }
    throw gtError("network",
      "Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS).");
  }
  if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
    const e = gtError("http",
      "Google Translate giới hạn tốc độ (HTTP " + res.status + ").");
    e.retryable = true;
    throw e;
  }
  if (!res.ok) {
    throw gtError("http", "Google Translate trả HTTP " + res.status + ".");
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
  return got;
}

// Thử lại tối đa GT_MAX_RETRIES lần với backoff khi 429/5xx; lỗi khác
// (mất mạng, CORS, Sorry, JSON hỏng, Hủy) ném ngay.
async function gtxWithRetry(attemptFn) {
  let lastErr = null;
  for (let attempt = 0; attempt <= GT_MAX_RETRIES; attempt++) {
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
export async function googleTranslateOne(text, src, dst) {
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
