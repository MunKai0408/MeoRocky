// verify_item2.mjs — Kiểm chứng Mục 2 (proxy dịch Cloudflare Worker +
// trang Kiểm tra dịch + nối app.js/gt.js) và Mục 9 (ghi chú UI, tĩnh).
//
// Chạy: node verify_item2.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: item2_results.md + item2_results.json
//
// KHÔNG cần mạng thật, KHÔNG cần tài khoản Cloudflare hay API key Google:
//   * Worker Cloudflare được import thật (site/cloudflare/worker.js) và
//     chạy với KV giả (Map) + fetch giả cho Google API.
//   * gt.js được import thật; proxy/MyMemory giả là server HTTP local
//     (127.0.0.1), Google trực tiếp giả bằng stub fetch.
//   * Các kiểm tra tĩnh đọc file local (snapshot lúc chạy — file chung
//     app.js/worker.js/index.html có thể đang được hotfix sửa song song,
//     nếu 1 check tĩnh fail thì chạy lại sau khi merge head mới).
import fs from "node:fs";
import http from "node:http";
import worker from "../site/cloudflare/worker.js";
import {
  googleTranslateOne,
  myMemoryTranslateOne,
  proxyTranslateOne,
  setMyMemoryEndpoint,
  setTranslateProxyUrl,
  translateOneWithFallbacks,
} from "../site/gt.js";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const results = {};
let failures = 0;
function rec(name, ok, detail) {
  results[name] = { ok, detail };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- KV giả ----
class FakeKV {
  constructor() { this.m = new Map(); }
  async get(k) { const v = this.m.get(k); return v === undefined ? null : v; }
  async put(k, v) { this.m.set(k, String(v)); }
  async delete(k) { this.m.delete(k); }
}
function makeEnv(overrides = {}) {
  return {
    GOOGLE_API_KEY: "test-key-khong-that",
    TRANSLATE_CACHE: new FakeKV(),
    TRANSLATE_USAGE: new FakeKV(),
    MONTHLY_CHAR_LIMIT: "500000",
    GLOSSARY_VERSION: "1",
    RATE_LIMIT_PER_MINUTE: "60",
    BREAKER_THRESHOLD: "5",
    BREAKER_COOLDOWN_S: "60",
    ...overrides,
  };
}
const monthKey = () => new Date().toISOString().slice(0, 7);

// ---- fetch giả: localhost đi thật, còn lại qua stub (mặc định chặn) ----
const realFetch = globalThis.fetch;
let fetchStub = null; // async (url, opts) => Response
globalThis.fetch = async (url, opts) => {
  const u = String(url);
  if (u.startsWith("http://127.0.0.1") || u.startsWith("http://localhost")) {
    return realFetch(url, opts);
  }
  if (fetchStub) return fetchStub(u, opts);
  throw new Error("fetch that khong duoc stub (mang that bi chan trong harness): " + u);
};
const jsonRes = (obj, status = 200, headers = {}) =>
  new Response(JSON.stringify(obj), { status, headers });

// ---- Server HTTP local: proxy giả + MyMemory giả ----
let proxyHits = 0;
let myMemoryHits = 0;
const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    const path = new URL(req.url || "/", "http://127.0.0.1").pathname;
    const send = (code, obj) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    if (path === "/proxy-ok" && req.method === "POST") {
      proxyHits++;
      const b = JSON.parse(body || "{}");
      return send(200, {
        ok: true, translated: "Xin chào", provider: "google",
        chars: [...(b.text || "")].length, cached: false,
        month_used: 11, month_limit: 500000,
      });
    }
    if (path === "/proxy-quota" && req.method === "POST") {
      proxyHits++;
      return send(429, {
        ok: false, code: "quota_exceeded",
        error: "Đã hết 500.000 ký tự miễn phí của tháng.",
        month_used: 500000, month_limit: 500000,
        fallback: "mymemory+offline",
      });
    }
    if (path === "/mymemory-ok") {
      myMemoryHits++;
      return send(200, {
        responseData: { translatedText: "Xin chào (MyMemory)", match: 1 },
        responseStatus: "200",
      });
    }
    if (path === "/mymemory-500") {
      myMemoryHits++;
      return send(500, { responseStatus: "500", responseDetails: "MyMemory lỗi" });
    }
    res.writeHead(404);
    res.end();
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const LOCAL = "http://127.0.0.1:" + server.address().port;

// ================= Worker Cloudflare =================
async function callWorker(env, body, method = "POST", ip = "1.2.3.4") {
  const req = new Request("https://proxy.test/", {
    method,
    headers: { "content-type": "application/json", "CF-Connecting-IP": ip },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
  const res = await worker.fetch(req, env, {});
  return { status: res.status, data: await res.json().catch(() => null) };
}

{ // 1. GET / kiểm tra worker
  const env = makeEnv();
  const r = await callWorker(env, null, "GET");
  rec("worker_get_health", r.status === 200 && r.data && r.data.service === "beartea-translate-proxy",
    "GET / -> " + r.status + " " + JSON.stringify(r.data));
}

{ // 2. Dịch OK + đếm ký tự tháng
  let googleCalls = 0;
  fetchStub = async (u) => {
    googleCalls++;
    return jsonRes({ data: { translations: [{ translatedText: "Xin chào thế giới" }] } });
  };
  const env = makeEnv();
  const r = await callWorker(env, { text: "Hello world", target: "vi", source: "en" });
  const used = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  rec("worker_translate_ok", r.status === 200 && r.data && r.data.ok === true &&
    r.data.translated === "Xin chào thế giới" && r.data.provider === "google" &&
    r.data.chars === 11 && r.data.month_used === 11 && r.data.month_limit === 500000 &&
    r.data.cached === false && used === "11" && googleCalls === 1,
    "POST Hello world -> " + r.status + " " + JSON.stringify(r.data) + " | KV usage=" + used + " | googleCalls=" + googleCalls);
  fetchStub = null;
}

{ // 3. Cache KV: gọi lại không gọi Google, không tính ký tự
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes({ data: { translations: [{ translatedText: "Xin chào thế giới" }] } });
  };
  const env = makeEnv();
  await callWorker(env, { text: "Hello world", target: "vi" });
  const r = await callWorker(env, { text: "Hello world", target: "vi" });
  const used = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  rec("worker_cache_hit", r.data && r.data.ok === true && r.data.cached === true &&
    r.data.translated === "Xin chào thế giới" && r.data.month_used === 11 &&
    used === "11" && googleCalls === 1,
    "lần 2 -> cached=" + (r.data && r.data.cached) + " month_used=" + (r.data && r.data.month_used) +
    " KV usage=" + used + " googleCalls=" + googleCalls + " (phải = 1)");
  fetchStub = null;
}

{ // 4. Hạn mức cứng: vượt 500.000 thì chặn, KHÔNG gọi Google, usage không đổi
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes({ data: { translations: [{ translatedText: "?" }] } });
  };
  const env = makeEnv();
  await env.TRANSLATE_USAGE.put("usage:" + monthKey(), "499995");
  const r = await callWorker(env, { text: "Hello worl", target: "vi" }); // 10 ký tự
  const used = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  rec("worker_quota_hard_cap", r.status === 429 && r.data && r.data.code === "quota_exceeded" &&
    r.data.month_used === 499995 && r.data.month_limit === 500000 &&
    googleCalls === 0 && used === "499995",
    "usage 499995 + 10 ký tự -> " + r.status + " " + JSON.stringify(r.data) +
    " | googleCalls=" + googleCalls + " (phải = 0) | KV usage=" + used + " (không đổi)");
  fetchStub = null;
}

{ // 5. Biên đúng 500.000 thì cho qua, ký tự tiếp theo thì chặn
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes({ data: { translations: [{ translatedText: "?" }] } });
  };
  const env = makeEnv();
  await env.TRANSLATE_USAGE.put("usage:" + monthKey(), "499990");
  const r1 = await callWorker(env, { text: "Hello worl", target: "vi" }); // +10 = 500000
  const r2 = await callWorker(env, { text: "x", target: "vi" }, "POST", "5.6.7.8");
  const used = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  rec("worker_quota_boundary", r1.status === 200 && r1.data && r1.data.month_used === 500000 &&
    r2.status === 429 && r2.data && r2.data.code === "quota_exceeded" &&
    used === "500000" && googleCalls === 1,
    "499990+10 -> " + r1.status + " month_used=" + (r1.data && r1.data.month_used) +
    "; +1 ký tự -> " + r2.status + " " + (r2.data && r2.data.code) +
    " | KV usage=" + used + " | googleCalls=" + googleCalls + " (phải = 1)");
  fetchStub = null;
}

{ // 6. Google 429 liên tiếp -> ngắt mạch, hoàn ký tự đã đặt trước
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes({ error: { message: "Quota exceeded" } }, 429);
  };
  const env = makeEnv({ BREAKER_THRESHOLD: "2", BREAKER_COOLDOWN_S: "60" });
  const before = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  const r1 = await callWorker(env, { text: "Hello", target: "vi" }, "POST", "9.9.9.1");
  const r2 = await callWorker(env, { text: "Hello", target: "vi" }, "POST", "9.9.9.2");
  const r3 = await callWorker(env, { text: "Hello", target: "vi" }, "POST", "9.9.9.3");
  const used = await env.TRANSLATE_USAGE.get("usage:" + monthKey());
  const breaker = JSON.parse((await env.TRANSLATE_USAGE.get("breaker")) || "{}");
  // before là null (KV chưa có key) -> hoàn lại đúng nghĩa là usage "0".
  const expectedUsed = before === null ? "0" : before;
  rec("worker_circuit_breaker", r1.status === 429 && r1.data && r1.data.code === "google_rate_limited" &&
    r2.status === 429 && r2.data && r2.data.code === "google_rate_limited" &&
    r3.status === 429 && r3.data && r3.data.code === "breaker_open" &&
    googleCalls === 6 && // 2 lần gọi x 3 attempt (retry 429) = 6
    used === expectedUsed && (breaker.failures || 0) >= 2,
    "lần 1,2 -> " + (r1.data && r1.data.code) + "/" + (r2.data && r2.data.code) +
    "; lần 3 -> " + (r3.data && r3.data.code) + " (không gọi Google nữa)" +
    " | googleCalls=" + googleCalls + " | KV usage=" + used + " (hoàn lại, bằng lúc đầu: " + before + ")" +
    " | breaker=" + JSON.stringify(breaker));
  fetchStub = null;
}

{ // 7. Giới hạn theo IP: 3/phút thì request thứ 4 bị chặn
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes({ data: { translations: [{ translatedText: "?" }] } });
  };
  const env = makeEnv({ RATE_LIMIT_PER_MINUTE: "3" });
  const rs = [];
  for (let i = 0; i < 4; i++) {
    rs.push(await callWorker(env, { text: "chuỗi " + i, target: "vi" }, "POST", "7.7.7.7"));
  }
  rec("worker_rate_limit_ip", rs[0].status === 200 && rs[1].status === 200 && rs[2].status === 200 &&
    rs[3].status === 429 && rs[3].data && rs[3].data.code === "rate_limited" &&
    googleCalls === 3,
    "4 request cùng IP (giới hạn 3/phút) -> " + rs.map((r) => r.status).join(",") +
    " | lần 4 code=" + (rs[3].data && rs[3].data.code) + " | googleCalls=" + googleCalls);
  fetchStub = null;
}

{ // 8. Validation + chưa cấu hình
  const env = makeEnv();
  fetchStub = async () => jsonRes({ data: { translations: [{ translatedText: "?" }] } });
  const badBody = await callWorker(env, {}, "POST", "2.2.2.2");
  const tooLong = await callWorker(env, { text: "x".repeat(5001), target: "vi" }, "POST", "2.2.2.3");
  const badTarget = await callWorker(env, { text: "Hi", target: "vietnam" }, "POST", "2.2.2.4");
  const put = await callWorker(env, null, "PUT", "2.2.2.5");
  const noKey = await callWorker(makeEnv({ GOOGLE_API_KEY: "" }), { text: "Hi", target: "vi" }, "POST", "2.2.2.6");
  const noKv = await callWorker(
    { GOOGLE_API_KEY: "k", MONTHLY_CHAR_LIMIT: "500000" },
    { text: "Hi", target: "vi" }, "POST", "2.2.2.7");
  rec("worker_validation_config",
    badBody.status === 400 && badBody.data.code === "bad_request" &&
    tooLong.status === 400 && badTarget.status === 400 && put.status === 405 &&
    noKey.status === 503 && noKey.data.code === "not_configured" &&
    noKv.status === 503 && noKv.data.code === "kv_not_configured",
    "thiếu text -> " + badBody.status + "; text 5001 ký tự -> " + tooLong.status +
    "; target sai -> " + badTarget.status + "; PUT -> " + put.status +
    "; thiếu key -> " + noKey.status + "/" + noKey.data.code +
    "; thiếu KV -> " + noKv.status + "/" + noKv.data.code);
  fetchStub = null;
}

// ================= gt.js: proxy + fallback =================
{ // 9. Có proxy: dịch qua proxy, không gọi Google trực tiếp
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes([[[ "KHONG-DUOC-GOI", "Hello world", null, null, 1 ]], null, "en"]);
  };
  proxyHits = 0;
  setTranslateProxyUrl(LOCAL + "/proxy-ok");
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("gt_proxy_path", got === "Xin chào" && proxyHits === 1 && googleCalls === 0,
    "translateOneWithFallbacks -> " + JSON.stringify(got) +
    " | proxyHits=" + proxyHits + " (phải = 1) | googleDirectCalls=" + googleCalls + " (phải = 0)");
  setTranslateProxyUrl("");
  fetchStub = null;
}

{ // 10. Proxy hết hạn mức -> BỎ QUA Google trực tiếp, rơi về MyMemory
  let googleCalls = 0;
  fetchStub = async () => {
    googleCalls++;
    return jsonRes([[[ "KHONG-DUOC-GOI", "Hello world", null, null, 1 ]], null, "en"]);
  };
  proxyHits = 0; myMemoryHits = 0;
  setTranslateProxyUrl(LOCAL + "/proxy-quota");
  setMyMemoryEndpoint(LOCAL + "/mymemory-ok");
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("gt_quota_falls_to_mymemory", got === "Xin chào (MyMemory)" &&
    proxyHits === 1 && myMemoryHits === 1 && googleCalls === 0,
    "proxy quota_exceeded -> " + JSON.stringify(got) +
    " | proxyHits=" + proxyHits + " | myMemoryHits=" + myMemoryHits +
    " | googleDirectCalls=" + googleCalls + " (phải = 0: hết hạn mức thì không vòng lại Google)");
  setTranslateProxyUrl("");
  setMyMemoryEndpoint("https://api.mymemory.translated.net/get");
  fetchStub = null;
}

{ // 11. Proxy chết (không gọi được) -> rơi về Google trực tiếp
  let googleCalls = 0;
  fetchStub = async (u) => {
    googleCalls++;
    return jsonRes([[[ "Xin chào", "Hello world", null, null, 1 ]], null, "en"]);
  };
  setTranslateProxyUrl("http://127.0.0.1:9/khong-co"); // cổng đóng -> ECONNREFUSED ngay
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("gt_proxy_down_falls_to_direct", got === "Xin chào" && googleCalls === 1,
    "proxy chết -> " + JSON.stringify(got) + " | googleDirectCalls=" + googleCalls + " (phải = 1)");
  setTranslateProxyUrl("");
  fetchStub = null;
}

{ // 12. Google Sorry + MyMemory lỗi -> ném lỗi kind mymemory (bridge dùng offline)
  fetchStub = async (u) => {
    if (u.includes("translate.googleapis.com")) {
      return new Response("<html><head><title>Sorry...</title></head></html>", { status: 200 });
    }
    throw new Error("khong duoc goi: " + u);
  };
  setTranslateProxyUrl("");
  setMyMemoryEndpoint(LOCAL + "/mymemory-500");
  let kind = null;
  try {
    await translateOneWithFallbacks("Hello world", "en", "vi");
  } catch (e) {
    kind = e && e.kind;
  }
  rec("gt_all_fail_throws_for_offline", kind === "mymemory",
    "Google Sorry + MyMemory 500 -> ném kind=" + JSON.stringify(kind) +
    " (phải = 'mymemory': bridge bắt lỗi này rồi dùng từ điển offline)");
  setMyMemoryEndpoint("https://api.mymemory.translated.net/get");
  fetchStub = null;
}

{ // 13. proxyTranslateOne / myMemoryTranslateOne trực tiếp
  const viaProxy = await proxyTranslateOne("Hello world", "en", "vi", LOCAL + "/proxy-ok");
  // MyMemory endpoint mặc định là mạng thật (harness chặn) nên kiểm tra
  // bằng endpoint local; endpoint mặc định chỉ đổi để test rồi trả lại.
  setMyMemoryEndpoint(LOCAL + "/mymemory-ok");
  const viaMmLocal = await myMemoryTranslateOne("Hello world", "en", "vi");
  setMyMemoryEndpoint("https://api.mymemory.translated.net/get");
  rec("gt_helpers_direct", viaProxy === "Xin chào" && viaMmLocal === "Xin chào (MyMemory)",
    "proxyTranslateOne -> " + JSON.stringify(viaProxy) +
    " | myMemoryTranslateOne(local) -> " + JSON.stringify(viaMmLocal));
}

// ================= Kiểm tra tĩnh =================
function read(p) { return fs.readFileSync(p, "utf8"); }
function has(p, s) { try { return read(p).includes(s); } catch (e) { return false; } }
function count(p, s) {
  try { return read(p).split(s).length - 1; } catch (e) { return 0; }
}

{ // 14. File cloudflare đầy đủ, không chứa key thật, KV id là placeholder
  const w = SITE + "/cloudflare/worker.js";
  const t = SITE + "/cloudflare/wrangler.toml";
  const r = SITE + "/cloudflare/README.md";
  const ok = fs.existsSync(w) && fs.existsSync(t) && fs.existsSync(r) &&
    has(t, "REPLACE_WITH_KV_NAMESPACE_ID") &&
    !/AIza[0-9A-Za-z_-]{10,}/.test(read(w) + read(t) + read(r)) &&
    has(w, "MONTHLY_CHAR_LIMIT") && has(w, "quota_exceeded") &&
    has(w, "TRANSLATE_CACHE") && has(w, "TRANSLATE_USAGE") &&
    has(w, "breaker") && has(w, "Retry-After") &&
    has(t, "MONTHLY_CHAR_LIMIT = 500000");
  rec("static_cloudflare_files", ok,
    "worker.js + wrangler.toml + README.md có đủ; wrangler.toml giữ placeholder KV id; " +
    "không chuỗi AIza (key Google) trong 3 file; worker có quota/cache/breaker/Retry-After.");
}

{ // 15. diagnostics.html tự chứa đủ 3 kiểm tra + sao chép + lưu proxy
  const p = SITE + "/diagnostics.html";
  const ok = fs.existsSync(p) &&
    has(p, "translate.googleapis.com") && has(p, "mymemory") &&
    has(p, "proxy") && has(p, "Sao chép kết quả") &&
    has(p, "pb3_translate_proxy") && has(p, "index.html") &&
    !has(p, "from './translate.js'") && !has(p, 'from "./translate.js"');
  rec("static_diagnostics_page", ok,
    "diagnostics.html có kiểm tra Google trực tiếp + MyMemory + proxy, nút sao chép, " +
    "lưu URL proxy vào localStorage, link về index.html; không import translate.js (chưa có trong site/).");
}

{ // 16. Nối gt.js/worker.js/app.js/index.html (snapshot local lúc chạy)
  const checks = [
    ["site/gt.js", "translateOneWithFallbacks"],
    ["site/gt.js", "proxyTranslateOne"],
    ["site/gt.js", "myMemoryTranslateOne"],
    ["site/gt.js", "quota"],
    ["site/worker.js", "translateOneWithFallbacks"],
    ["site/worker.js", "set_translate_proxy"],
    ["site/app.js", "pb3_translate_proxy"],
    ["site/app.js", "set_translate_proxy"],
    ["site/app.js", "translate-proxy"],
    ["site/index.html", 'id="translate-proxy"'],
    ["site/index.html", "diagnostics.html"],
    ["site/index.html", "Asset nhị phân (texture/âm thanh): chỉ đọc, dùng bản iSH"],
  ];
  const missing = checks.filter(([f, s]) => !has(SITE + "/" + f.replace("site/", ""), s));
  const noteCount = count(SITE + "/index.html", "Asset nhị phân (texture/âm thanh): chỉ đọc, dùng bản iSH");
  rec("static_wiring", missing.length === 0 && noteCount >= 4,
    "thiếu: " + (missing.length ? JSON.stringify(missing) : "không có") +
    " | ghi chú item 9 trong index.html: " + noteCount + " chỗ (Unity + Mod + Chẩn đoán + giới hạn, phải >= 4).");
}

{ // 17. Không COI, không key trong file mới/sửa của mục 2+9
  const files = [
    SITE + "/cloudflare/worker.js", SITE + "/cloudflare/wrangler.toml",
    SITE + "/cloudflare/README.md", SITE + "/diagnostics.html",
    SITE + "/gt.js", SITE + "/worker.js", SITE + "/app.js", SITE + "/index.html",
  ];
  const bad = files.filter((f) =>
    /Cross-Origin-Opener-Policy|Cross-Origin-Embedder-Policy|coi-serviceworker/i.test(read(f)));
  const keyLeak = files.filter((f) => /AIza[0-9A-Za-z_-]{10,}/.test(read(f)));
  rec("static_no_coi_no_key", bad.length === 0 && keyLeak.length === 0,
    "file chứa COI: " + (bad.length ? bad.join(", ") : "không có") +
    " | file chứa key Google (AIza...): " + (keyLeak.length ? keyLeak.join(", ") : "không có"));
}

server.close();
globalThis.fetch = realFetch;

fs.writeFileSync(EXP + "/item2_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
console.log("\n==== KẾT LUẬN: " + (failures === 0 ? "TẤT CẢ XANH" : failures + " FAIL") + " ====");
process.exit(failures === 0 ? 0 : 1);
