// gt.js — Google Translate miễn phí qua fetch() trong Worker (bản web).
// Endpoint gtx (không key), cùng endpoint bản iSH dùng qua urllib.
// Chỉ dùng cho bản web; bản iSH vẫn dùng urllib trong sdk/translate.py.
//
// Lỗi nào cũng ném Error có .kind để Python phân loại và rơi về từ điển
// offline (không bao giờ làm hỏng đợt dịch):
//   "network" — fetch ném TypeError (mất mạng hoặc trình duyệt chặn CORS;
//               thử lại cũng vô ích nên ném ngay, không retry).
//   "timeout" — quá 20 giây.
//   "http"    — HTTP 429/5xx sau hết retry, hoặc HTTP khác 200.
//   "sorry"   — Google trả trang "Sorry..." (IP đang bị Google giới hạn).
//   "bad_response" — HTTP 200 nhưng JSON không đọc được / thiếu bản dịch.
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

// Dịch 1 chuỗi (1 request). 429/5xx thì thử lại với backoff; lỗi khác
// (mất mạng, CORS, Sorry, JSON hỏng) ném ngay để Python dùng offline.
export async function googleTranslateOne(text, src, dst) {
  const url = GT_ENDPOINT + "?" + new URLSearchParams({
    client: "gtx", sl: src, tl: dst, dt: "t", q: text,
  }).toString();
  let lastErr = null;
  for (let attempt = 0; attempt <= GT_MAX_RETRIES; attempt++) {
    if (attempt > 0) {
      await sleep(GT_BACKOFF_MS[Math.min(attempt - 1, GT_BACKOFF_MS.length - 1)]);
    }
    let res;
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), GT_TIMEOUT_MS);
      try {
        res = await fetch(url, {
          headers: { Accept: "application/json, */*" },
          signal: ctrl.signal,
        });
      } finally {
        clearTimeout(timer);
      }
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw gtError("timeout", "Google Translate quá thời gian chờ (20 giây).");
      }
      throw gtError("network",
        "Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS).");
    }
    if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
      lastErr = gtError("http",
        "Google Translate giới hạn tốc độ (HTTP " + res.status + ").");
      if (attempt < GT_MAX_RETRIES) continue;
      throw lastErr;
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
  throw lastErr || gtError("http", "Google Translate giới hạn tốc độ, thử lại sau.");
}
