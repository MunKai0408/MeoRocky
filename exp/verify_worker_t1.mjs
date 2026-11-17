// verify_worker_t1.mjs — Kiểm chứng T1 (Cloudflare Worker + key Google +
// Workers AI miễn phí): trạng thái worker trong tab Dịch + đường worker
// qua mock + fallback khi worker vắng mặt + provider Workers AI.
//
// Chạy: node verify_worker_t1.mjs (từ thư mục exp/ của repo)
// Kết quả: worker_t1_results.md + worker_t1_results.json
//
// KHÔNG cần mạng thật, KHÔNG cần tài khoản Cloudflare hay API key Google:
//   * Worker Cloudflare được import thật (../cloudflare/worker.js), chạy
//     qua HTTP server local với KV giả (Map) + fetch giả cho Google API
//     (trả "VI: " + text gốc — giữ nguyên placeholder từng byte) + AI giả
//     (trả "VI-AI: " + text gốc; bản quirk mô phỏng đặc tính m2m100: đoạn
//     nhiều câu trong 1 lần gọi bị nuốt mất câu sau câu đầu).
//   * gt.js được import thật; Google trực tiếp + MyMemory giả là server
//     HTTP local; proxy giả = worker thật qua HTTP.
//   * Kiểm tra chất lượng dùng chuỗi 9K THẬT (en.json v1.0.6) + gate
//     check_placeholders THẬT (pb3/sdk/gate.py qua python3) trên output
//     mock — kết quả là [FIXTURE] (mock, không phải chất lượng model thật).
//   * Các kiểm tra tĩnh đọc file local.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import worker from "../cloudflare/worker.js";
import {
  checkProxyStatus,
  getTranslateProxyUrl,
  myMemoryTranslateOne,
  proxyTranslateBatch,
  setGoogleEndpoint,
  setMyMemoryEndpoint,
  setTranslateProxyUrl,
  translateBatchWithFallbacks,
  translateOneWithFallbacks,
} from "../gt.js";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const results = {};
let failures = 0;
function rec(name, ok, detail) {
  results[name] = { ok, detail };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- KV giả (như verify_item2.mjs) ----
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
const envOK = makeEnv();
const envNoKey = makeEnv({ GOOGLE_API_KEY: "" });
const envNoKv = { GOOGLE_API_KEY: "test-key-khong-that", MONTHLY_CHAR_LIMIT: "500000" };

// ---- AI giả (Workers AI m2m100): trả "VI-AI: " + text gốc (giữ nguyên
// placeholder từng byte, như Google stub). Bản quirk=true mô phỏng đặc
// tính m2m100: đoạn nhiều câu trong 1 lần gọi bị nuốt mất câu sau câu đầu.
class FakeAI {
  constructor(opts = {}) {
    this.calls = [];
    this.quirk = !!opts.quirk;
    this.failAlways = opts.failAlways || null;
  }
  async run(model, input) {
    this.calls.push({ model, input });
    if (this.failAlways) throw new Error(this.failAlways);
    const text = input.text;
    if (this.quirk) {
      const parts = text.split(/(?<=[.!?])\s+/);
      if (parts.length > 1) return { translated_text: "VI-AI: " + parts[0] };
    }
    return { translated_text: "VI-AI: " + text };
  }
}
const aiOK = new FakeAI();
const envAI = makeEnv({ GOOGLE_API_KEY: "", AI: aiOK });
const aiBoth = new FakeAI();
const envBoth = makeEnv({ AI: aiBoth });
const aiExplicit = new FakeAI();
const envAIExplicit = makeEnv({ AI: aiExplicit, TRANSLATE_PROVIDER: "workers-ai" });
const envGoogleExplicitNoKey = makeEnv({ GOOGLE_API_KEY: "", TRANSLATE_PROVIDER: "google" });
const envNone = makeEnv({ GOOGLE_API_KEY: "" });

// Gọi worker thật trực tiếp (không qua HTTP) — cho các kiểm tra provider.
async function workerPost(env, body) {
  const req = new Request("https://proxy.test/", {
    method: "POST",
    headers: { "content-type": "application/json", "CF-Connecting-IP": "1.2.3.4" },
    body: JSON.stringify(body),
  });
  const res = await worker.fetch(req, env, {});
  return { status: res.status, data: await res.json() };
}
async function workerGet(env) {
  const req = new Request("https://proxy.test/", { method: "GET" });
  const res = await worker.fetch(req, env, {});
  return { status: res.status, data: await res.json() };
}
// Gate check_placeholders THẬT (pb3/sdk/gate.py) qua python3 — đối chiếu
// source + translations, trả {status, counts, failures}.
function gateCheckPlaceholders(source, translations) {
  const py = [
    "import sys, json",
    "sys.path.insert(0, " + JSON.stringify(ROOT + "/pb3") + ")",
    "from sdk.gate import check_placeholders",
    "d = json.load(sys.stdin)",
    "r = check_placeholders(d['source'], d['translations'])",
    "print(json.dumps({'status': r['status'], 'counts': r['counts'], 'failures': r['failures'][:5]}))",
  ].join("\n");
  const out = execFileSync("python3", ["-c", py], {
    input: JSON.stringify({ source, translations }), encoding: "utf8", timeout: 60000,
  });
  return JSON.parse(out);
}

// ---- fetch giả: localhost đi thật, Google API qua stub, còn lại chặn ----
const realFetch = globalThis.fetch;
let googleCalls = 0;
let fetchStub = null;
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
// Google stub: dịch = "VI: " + text gốc (giữ nguyên placeholder từng byte).
async function googleStub(u, opts) {
  if (!u.includes("translation.googleapis.com")) {
    throw new Error("fetchStub chi phuc vu Google API, gap: " + u);
  }
  googleCalls++;
  let body = {};
  try { body = JSON.parse((opts && opts.body) || "{}"); } catch (e) { /* ignore */ }
  const q = body.q;
  const tr = (t) => ({ translatedText: "VI: " + t });
  const list = Array.isArray(q) ? q.map(tr) : [tr(typeof q === "string" ? q : "")];
  return jsonRes({ data: { translations: list } });
}
fetchStub = googleStub;

// ---- Server HTTP local: gateway tới worker thật + mock gtx/MyMemory/quota ----
const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", async () => {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    const path = url.pathname;
    const send = (code, obj) => {
      res.writeHead(code, { "content-type": "application/json" });
      res.end(JSON.stringify(obj));
    };
    // Gateway: /w-ok, /w-nokey, /w-nokv, /w-ai -> worker thật với env tương ứng.
    if (path.startsWith("/w-ok") || path.startsWith("/w-nokey") || path.startsWith("/w-nokv") || path.startsWith("/w-ai")) {
      const env = path.startsWith("/w-ok") ? envOK
        : path.startsWith("/w-nokey") ? envNoKey
        : path.startsWith("/w-nokv") ? envNoKv : envAI;
      const wreq = new Request("https://proxy.test/", {
        method: req.method,
        headers: { "content-type": "application/json", "CF-Connecting-IP": "1.2.3.4" },
        body: req.method === "POST" ? (body || "{}") : undefined,
      });
      const wres = await worker.fetch(wreq, env, {});
      res.writeHead(wres.status, { "content-type": "application/json" });
      res.end(await wres.text());
      return;
    }
    if (path === "/proxy-quota" && req.method === "POST") {
      return send(429, {
        ok: false, code: "quota_exceeded",
        error: "Đã hết 500.000 ký tự miễn phí của tháng.",
        month_used: 500000, month_limit: 500000,
        fallback: "mymemory+offline",
      });
    }
    if (path === "/mymemory-ok") {
      return send(200, {
        responseData: { translatedText: "Xin chào (MyMemory)", match: 1 },
        responseStatus: "200",
      });
    }
    // gtx giả: GET ?q=... hoặc POST form q=... -> dạng JSON gtx 1 khối.
    if (path === "/gtx-ok") {
      let q = url.searchParams.get("q") || "";
      if (req.method === "POST") {
        q = new URLSearchParams(body).get("q") || "";
      }
      return send(200, [[[ "VI: " + q, q, null, null, 1 ]], null, "en"]);
    }
    res.writeHead(404);
    res.end();
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const LOCAL = "http://127.0.0.1:" + server.address().port;

// ================= 1. Trạng thái worker (checkProxyStatus) =================
{ // 1a. Worker OK qua HTTP thật (đường Google: có key, auto -> google)
  const st = await checkProxyStatus(LOCAL + "/w-ok");
  rec("status_ok", st.state === "ok" && st.monthly_limit === 500000 &&
    st.provider === "google" && st.version === 3,
    "GET " + LOCAL + "/w-ok -> " + JSON.stringify(st) + " (phải state=ok, monthly_limit=500000, provider=google, version=3)");
}
{ // 1g. Worker AI-only qua HTTP thật -> provider workers-ai
  const st = await checkProxyStatus(LOCAL + "/w-ai");
  rec("status_provider_workers_ai", st.state === "ok" && st.provider === "workers-ai",
    "GET " + LOCAL + "/w-ai -> " + JSON.stringify(st) + " (phải state=ok, provider=workers-ai)");
}
{ // 1b. Worker chưa cấu hình key -> not_configured
  const st = await checkProxyStatus(LOCAL + "/w-nokey");
  rec("status_not_configured", st.state === "not_configured",
    "GET /w-nokey -> " + JSON.stringify(st) + " (phải state=not_configured)");
}
{ // 1c. Worker chưa gắn KV -> kv_missing
  const st = await checkProxyStatus(LOCAL + "/w-nokv");
  rec("status_kv_missing", st.state === "kv_missing",
    "GET /w-nokv -> " + JSON.stringify(st) + " (phải state=kv_missing)");
}
{ // 1d. Worker không gọi được (cổng đóng) -> unreachable
  const st = await checkProxyStatus("http://127.0.0.1:9/khong-co");
  rec("status_unreachable", st.state === "unreachable",
    "GET cổng đóng -> " + JSON.stringify(st) + " (phải state=unreachable)");
}
{ // 1e. Chưa dán URL -> not_set
  setTranslateProxyUrl("");
  const st = await checkProxyStatus("");
  const st2 = await checkProxyStatus();
  rec("status_not_set", st.state === "not_set" && st2.state === "not_set",
    "URL rỗng -> " + JSON.stringify(st) + "; không truyền URL -> " + JSON.stringify(st2));
}
{ // 1f. Chuẩn hoá URL: gõ trơn hostname thì tự thêm https://
  setTranslateProxyUrl("beartea-translate-proxy.x.workers.dev");
  const got = getTranslateProxyUrl();
  setTranslateProxyUrl("https://beartea-translate-proxy.x.workers.dev/");
  const got2 = getTranslateProxyUrl();
  setTranslateProxyUrl("");
  rec("status_normalize_url", got === "https://beartea-translate-proxy.x.workers.dev" &&
    got2 === "https://beartea-translate-proxy.x.workers.dev",
    "gõ trơn -> " + JSON.stringify(got) + "; có scheme + / cuối -> " + JSON.stringify(got2) +
    " (phải cùng = https://beartea-translate-proxy.x.workers.dev)");
}

// ================= 2. Đường worker: file mẫu + cache =================
// Mẫu [FIXTURE]: 5 chuỗi như file game thật (placeholder, thẻ, xuống dòng).
const SAMPLE = [
  "Play",
  "Hello {VAR1}, you have {0} new messages",
  "Press {KINGCOLOR}Start{/KINGCOLOR} to continue",
  "Line one\nLine two",
  "You earned %s gold",
];
const SAMPLE_KEYS = ["Play", "Hello_World", "Press_Start", "Two_Lines", "Earned_Gold"];
function placeholdersOf(s) {
  return (s.match(/\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|%s|<\/?[a-zA-Z][^>]*>/g) || []).sort();
}

{ // 2a. Batch qua worker thật: dịch đúng, placeholder còn nguyên từng byte
  googleCalls = 0;
  setTranslateProxyUrl(LOCAL + "/w-ok");
  const out = JSON.parse(await translateBatchWithFallbacks(SAMPLE, "en", "vi"));
  const okMode = out.mode === "proxy-batch";
  const okVals = Array.isArray(out.translations) && out.translations.length === SAMPLE.length &&
    out.translations.every((t, i) => t === "VI: " + SAMPLE[i]);
  const okPh = out.translations.every((t, i) =>
    JSON.stringify(placeholdersOf(t)) === JSON.stringify(placeholdersOf(SAMPLE[i])));
  rec("worker_batch_sample_placeholders", okMode && okVals && okPh && googleCalls === 1,
    "mode=" + out.mode + " (phải proxy-batch) | translations=" + JSON.stringify(out.translations) +
    " | placeholder từng chuỗi khớp gốc: " + okPh + " | googleCalls=" + googleCalls + " (phải = 1: 1 batch request)");
  setTranslateProxyUrl("");
}
{ // 2b. Cache worker: batch giống hệt lần 2 không gọi Google nữa
  const before = googleCalls; // = 1 sau test 2a (cùng envOK, cùng KV)
  setTranslateProxyUrl(LOCAL + "/w-ok");
  const out = JSON.parse(await translateBatchWithFallbacks(SAMPLE, "en", "vi"));
  const okVals = Array.isArray(out.translations) &&
    out.translations.every((t, i) => t === "VI: " + SAMPLE[i]);
  rec("worker_batch_cache", okVals && googleCalls === before,
    "batch lần 2 -> translations đúng | googleCalls=" + googleCalls +
    " (phải = " + before + ", không tăng: worker trả từ KV cache)");
  setTranslateProxyUrl("");
}
{ // 2c. Mức file JSON: key giữ nguyên, value dịch qua worker, placeholder còn nguyên
  setTranslateProxyUrl(LOCAL + "/w-ok");
  const input = {};
  SAMPLE_KEYS.forEach((k, i) => { input[k] = SAMPLE[i]; });
  const values = SAMPLE_KEYS.map((k) => input[k]);
  const out = JSON.parse(await translateBatchWithFallbacks(values, "en", "vi"));
  const translated = {};
  SAMPLE_KEYS.forEach((k, i) => { translated[k] = out.translations[i]; });
  const okKeys = JSON.stringify(Object.keys(translated)) === JSON.stringify(SAMPLE_KEYS);
  const okPh = SAMPLE_KEYS.every((k) =>
    JSON.stringify(placeholdersOf(translated[k])) === JSON.stringify(placeholdersOf(input[k])));
  rec("worker_batch_sample_file", out.mode === "proxy-batch" && okKeys && okPh,
    "keys giữ nguyên: " + okKeys + " | placeholder còn nguyên: " + okPh +
    " | mẫu: " + JSON.stringify(translated.Hello_World));
  setTranslateProxyUrl("");
}
{ // 2d. proxyTranslateBatch trực tiếp qua worker thật (đơn lẻ)
  const out = await proxyTranslateBatch(["Hello world"], "en", "vi", LOCAL + "/w-ok");
  rec("worker_proxy_batch_direct", Array.isArray(out) && out.length === 1 && out[0] === "VI: Hello world",
    "proxyTranslateBatch 1 chuỗi -> " + JSON.stringify(out));
}

// ================= 3. Fallback khi worker vắng mặt =================
{ // 3a. Không có proxy: Google trực tiếp (gtx giả) vẫn dịch được
  setTranslateProxyUrl("");
  setGoogleEndpoint(LOCAL + "/gtx-ok");
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("fallback_no_proxy_google", got === "VI: Hello world",
    "không proxy -> gtx giả -> " + JSON.stringify(got) + " (phải = 'VI: Hello world')");
  setGoogleEndpoint("");
}
{ // 3b. Proxy chết (cổng đóng): rơi về Google trực tiếp
  setTranslateProxyUrl("http://127.0.0.1:9/khong-co");
  setGoogleEndpoint(LOCAL + "/gtx-ok");
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("fallback_proxy_down_google", got === "VI: Hello world",
    "proxy chết -> gtx giả -> " + JSON.stringify(got));
  setTranslateProxyUrl("");
  setGoogleEndpoint("");
}
{ // 3c. Proxy hết hạn mức: BỎ QUA Google, rơi về MyMemory (Google không gọi)
  googleCalls = 0;
  setTranslateProxyUrl(LOCAL + "/proxy-quota");
  setMyMemoryEndpoint(LOCAL + "/mymemory-ok");
  const got = await translateOneWithFallbacks("Hello world", "en", "vi");
  rec("fallback_proxy_quota_mymemory", got === "Xin chào (MyMemory)" && googleCalls === 0,
    "proxy quota_exceeded -> " + JSON.stringify(got) +
    " | googleCalls=" + googleCalls + " (phải = 0: hết hạn mức thì không vòng lại Google)");
  setTranslateProxyUrl("");
  setMyMemoryEndpoint("https://api.mymemory.translated.net/get");
  fetchStub = null;
}

// ================= 4. Provider Workers AI (mock) =================
// Mục 3 đã tắt fetchStub (fetchStub = null) — khôi phục stub Google vì
// các kiểm tra dưới đây vẫn cần đường Google (so sánh provider, cache).
fetchStub = googleStub;
{ // 4a. auto + không key + có AI -> workers-ai
  const r = await workerPost(envAI, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_provider_auto_no_key", r.status === 200 && r.data.ok === true &&
    r.data.provider === "workers-ai" && r.data.translated === "VI-AI: Hello world" &&
    aiOK.calls.length === 1 && googleCalls === 0,
    "POST env AI-only -> " + JSON.stringify(r.data) + " | aiCalls=" + aiOK.calls.length +
    " (phải = 1) | googleCalls=" + googleCalls + " (phải = 0)");
}
{ // 4b. auto + có key + có AI -> Google được ưu tiên
  const before = aiBoth.calls.length;
  const r = await workerPost(envBoth, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_provider_google_preferred", r.status === 200 && r.data.provider === "google" &&
    r.data.translated === "VI: Hello world" && aiBoth.calls.length === before,
    "POST env key+AI -> provider=" + r.data.provider + " (phải google) | aiCalls không tăng: " +
    aiBoth.calls.length + " (phải = " + before + ")");
}
{ // 4c. TRANSLATE_PROVIDER=workers-ai + có key -> vẫn workers-ai
  const r = await workerPost(envAIExplicit, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_provider_explicit_workers_ai", r.status === 200 && r.data.provider === "workers-ai" &&
    r.data.translated === "VI-AI: Hello world",
    "POST TRANSLATE_PROVIDER=workers-ai (có key) -> provider=" + r.data.provider + " (phải workers-ai)");
}
{ // 4d. TRANSLATE_PROVIDER=google + không key -> 503 not_configured
  const r = await workerPost(envGoogleExplicitNoKey, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_provider_explicit_google_no_key", r.status === 503 && r.data.code === "not_configured",
    "POST TRANSLATE_PROVIDER=google (không key) -> status=" + r.status + ", code=" + r.data.code + " (phải 503 not_configured)");
}
{ // 4e. Không key, không AI -> 503 not_configured
  const r = await workerPost(envNone, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_provider_none", r.status === 503 && r.data.code === "not_configured",
    "POST không key, không AI -> status=" + r.status + ", code=" + r.data.code + " (phải 503 not_configured)");
}
{ // 4f. GET / cho biết provider: AI-only -> workers-ai, key+AI -> google, không có gì -> null
  const gAI = await workerGet(envAI);
  const gBoth = await workerGet(envBoth);
  const gNone = await workerGet(envNone);
  rec("ai_get_health_provider",
    gAI.data.config === "ok" && gAI.data.provider === "workers-ai" && gAI.data.version === 3 &&
    gAI.data.ai_daily_limit === 10000 &&
    gBoth.data.config === "ok" && gBoth.data.provider === "google" &&
    gNone.data.config === "not_configured" && gNone.data.provider === null,
    "GET AI-only -> config=" + gAI.data.config + ", provider=" + gAI.data.provider +
    ", version=" + gAI.data.version + ", ai_daily_limit=" + gAI.data.ai_daily_limit +
    " | GET key+AI -> provider=" + gBoth.data.provider +
    " | GET không gì -> config=" + gNone.data.config + ", provider=" + gNone.data.provider);
}
{ // 4g. Cache tách riêng theo provider + model: cùng text, Google và AI
  // trả khác nhau, lần 2 không gọi thêm bên nào.
  const sepCache = new FakeKV();
  const sepUsage = new FakeKV();
  const envG = makeEnv({ TRANSLATE_CACHE: sepCache, TRANSLATE_USAGE: sepUsage });
  const aiSep = new FakeAI();
  const envA = makeEnv({ GOOGLE_API_KEY: "", AI: aiSep, TRANSLATE_CACHE: sepCache, TRANSLATE_USAGE: sepUsage });
  const gCallsBefore = googleCalls;
  const rG1 = await workerPost(envG, { text: "Hello world", target: "vi", source: "en" });
  const rA1 = await workerPost(envA, { text: "Hello world", target: "vi", source: "en" });
  const rG2 = await workerPost(envG, { text: "Hello world", target: "vi", source: "en" });
  const rA2 = await workerPost(envA, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_cache_separate",
    rG1.data.translated === "VI: Hello world" && rA1.data.translated === "VI-AI: Hello world" &&
    rG2.data.translated === "VI: Hello world" && rA2.data.translated === "VI-AI: Hello world" &&
    rG2.data.cached === true && rA2.data.cached === true &&
    googleCalls === gCallsBefore + 1 && aiSep.calls.length === 1,
    "Google lần 1 -> " + JSON.stringify(rG1.data.translated) + " | AI lần 1 -> " +
    JSON.stringify(rA1.data.translated) + " (phải khác nhau: cache không lẫn) | " +
    "lần 2 cả hai cached=true | googleCalls tăng 1 (" + googleCalls + "), aiCalls=1 (" + aiSep.calls.length + ")");
}
{ // 4h. Batch qua Workers AI: tách thành từng lần gọi 1 chuỗi (3 chuỗi -> 3 lần gọi AI)
  const aiBatch = new FakeAI();
  const envB = makeEnv({ GOOGLE_API_KEY: "", AI: aiBatch });
  const gBefore = googleCalls;
  const r = await workerPost(envB, { texts: ["One", "Two", "Three"], target: "vi", source: "en" });
  rec("ai_batch_one_by_one",
    r.status === 200 && r.data.provider === "workers-ai" &&
    JSON.stringify(r.data.translated) === JSON.stringify(["VI-AI: One", "VI-AI: Two", "VI-AI: Three"]) &&
    aiBatch.calls.length === 3 && googleCalls === gBefore &&
    aiBatch.calls.every((c) => c.model === "@cf/meta/m2m100-1.2b" && c.input.source_lang === "en" && c.input.target_lang === "vi"),
    "batch 3 chuỗi -> " + JSON.stringify(r.data.translated) + " | aiCalls=" + aiBatch.calls.length +
    " (phải = 3: từng chuỗi một) | googleCalls không tăng | model=" + aiBatch.calls[0].model +
    " (phải @cf/meta/m2m100-1.2b), source_lang=en, target_lang=vi");
}
{ // 4i. Hết ngân sách ngày (ước tính) -> 429 quota_exceeded, không gọi AI
  const aiQ = new FakeAI();
  const envQ = makeEnv({ GOOGLE_API_KEY: "", AI: aiQ });
  const today = new Date().toISOString().slice(0, 10);
  await envQ.TRANSLATE_USAGE.put("ai-usage:" + today, "9000");
  const r = await workerPost(envQ, { text: "Hello world", target: "vi", source: "en" });
  const monthUsed = await envQ.TRANSLATE_USAGE.get("usage:" + new Date().toISOString().slice(0, 7));
  rec("ai_daily_quota",
    r.status === 429 && r.data.code === "quota_exceeded" && aiQ.calls.length === 0 &&
    (monthUsed === null || monthUsed === "0"),
    "seed ai-usage hôm nay = 9000 -> status=" + r.status + ", code=" + r.data.code +
    " (phải 429 quota_exceeded) | aiCalls=" + aiQ.calls.length + " (phải = 0: không gọi AI) | " +
    "month_used không tăng: " + monthUsed);
}
{ // 4j. Cloudflare tự báo hết ngân sách (4006) -> quota_exceeded, không thử lại vô ích
  const ai4006 = new FakeAI({ failAlways: "4006: you have used up your daily free allocation of 10,000 neurons" });
  const envF = makeEnv({ GOOGLE_API_KEY: "", AI: ai4006 });
  const r = await workerPost(envF, { text: "Hello world", target: "vi", source: "en" });
  rec("ai_daily_quota_cloudflare_4006",
    r.status === 429 && r.data.code === "quota_exceeded" && ai4006.calls.length === 1,
    "AI ném 4006 -> status=" + r.status + ", code=" + r.data.code +
    " (phải 429 quota_exceeded) | aiCalls=" + ai4006.calls.length + " (phải = 1: không thử lại)");
}
{ // 4k. AI lỗi thường -> hoàn lại ký tự tháng + neurons ước tính
  const aiErr = new FakeAI({ failAlways: "Workers AI timeout (giả)" });
  const envE = makeEnv({ GOOGLE_API_KEY: "", AI: aiErr });
  const today = new Date().toISOString().slice(0, 10);
  const month = new Date().toISOString().slice(0, 7);
  const r = await workerPost(envE, { text: "Hello world", target: "vi", source: "en" });
  const monthAfter = await envE.TRANSLATE_USAGE.get("usage:" + month);
  const aiAfter = await envE.TRANSLATE_USAGE.get("ai-usage:" + today);
  rec("ai_error_refund",
    r.status === 502 && (r.data.code === "ai_error") &&
    (monthAfter === null || monthAfter === "0") &&
    (aiAfter === null || aiAfter === "0"),
    "AI lỗi -> status=" + r.status + ", code=" + r.data.code + " (phải 502 ai_error) | " +
    "month_used sau lỗi: " + monthAfter + " (phải 0/null: đã hoàn lại) | " +
    "ai-usage hôm nay: " + aiAfter + " (phải 0/null: đã hoàn lại)");
}

// Chuỗi 9K THẬT (en.json v1.0.6, 2252 key) — dùng cho kiểm tra chất lượng
// [FIXTURE]: mock chỉ thêm tiền tố nên KHÔNG đo được chất lượng dịch thật
// của model; kiểm tra này chứng minh pipeline đúng (đủ chuỗi, đúng thứ tự,
// placeholder còn nguyên qua gate THẬT) cho cả 2 provider.
const QUALITY_KEYS = [
  "Achievement_HisWinningness", "Achievement_HisWinningness_Desc",
  "Achievement_TrueRoyalty", "Blessing_BabyBoom_Desc2",
  "Blessing_Illuminism_Desc", "Blessing_Prosperity_Desc2",
  "Building_DemonsAltar_desc", "Building_Mangler_desc",
  "Challenge_BloodRevolt_Cond", "Demo_WIP",
];
const QUALITY_EN = {
  Achievement_HisWinningness: "His Winningness",
  Achievement_HisWinningness_Desc: "Win one game.",
  Achievement_TrueRoyalty: "True Royalty",
  Blessing_BabyBoom_Desc2: "A troop card on this plot will have \nits unit number doubled by year {VAR1}.",
  Blessing_Illuminism_Desc: "The cards on the target plots will \nlevel up in the year of the prophecy.",
  Blessing_Prosperity_Desc2: "If you have 30 gold or less by year {VAR1}, \nmultiplies your gold by 9.",
  Building_DemonsAltar_desc: "Summons one demon per level. For each dead ally, grows permanently {VAR1} stronger.",
  Building_Mangler_desc: "Summons meat trolls. Every year, sacrifices an adjacent unit to gain {VAR1} damage and HP.",
  Challenge_BloodRevolt_Cond: "- Fight only against the <color=#{KINGCOLOR}>Blood Rebellion.</color>",
  Demo_WIP: "pre-release version: \nthe game is still a work in progress and subject to change",
};
const QUALITY_SINGLE = QUALITY_KEYS.filter((k) =>
  !QUALITY_EN[k].includes(". ") || k === "Achievement_HisWinningness_Desc");
const QUALITY_MULTI = QUALITY_KEYS.filter((k) => !QUALITY_SINGLE.includes(k));

{ // 4l. So sánh mock Google vs mock Workers AI trên 10 chuỗi 9K thật +
  // gate check_placeholders THẬT trên cả 2 output [FIXTURE]
  const values = QUALITY_KEYS.map((k) => QUALITY_EN[k]);
  const gCallsBefore = googleCalls;
  const aiQ2 = new FakeAI();
  const envQ2 = makeEnv({ GOOGLE_API_KEY: "", AI: aiQ2 });
  const envG2 = makeEnv();
  const rG = await workerPost(envG2, { texts: values, target: "vi", source: "en" });
  const rA = await workerPost(envQ2, { texts: values, target: "vi", source: "en" });
  if (!Array.isArray(rG.data.translated) || !Array.isArray(rA.data.translated)) {
    rec("quality_ai_vs_google_mock", false,
      "[FIXTURE] worker trả lỗi, không có translations: Google -> " + JSON.stringify(rG.data) +
      " | AI -> " + JSON.stringify(rA.data));
  } else {
  const srcObj = {};
  const gObj = {};
  const aObj = {};
  QUALITY_KEYS.forEach((k, i) => {
    srcObj[k] = QUALITY_EN[k];
    gObj[k] = rG.data.translated[i];
    aObj[k] = rA.data.translated[i];
  });
  const gateG = gateCheckPlaceholders(srcObj, gObj);
  const gateA = gateCheckPlaceholders(srcObj, aObj);
  const okOrder = rG.data.translated.length === 10 && rA.data.translated.length === 10;
  rec("quality_ai_vs_google_mock",
    rG.data.provider === "google" && rA.data.provider === "workers-ai" && okOrder &&
    gateG.status === "pass" && gateA.status === "pass" &&
    googleCalls === gCallsBefore + 1 && aiQ2.calls.length === 10,
    "[FIXTURE] 10 chuỗi 9K thật: Google mock gate=" + gateG.status + " (checked=" +
    gateG.counts.checked + ", failed=" + gateG.counts.failed + ") | Workers AI mock gate=" +
    gateA.status + " (checked=" + gateA.counts.checked + ", failed=" + gateA.counts.failed +
    ") | Google: 1 batch request, AI: " + aiQ2.calls.length + " lần gọi 1 chuỗi | " +
    "KẾT LUẬN TRUNG THỰC: mock chỉ thêm tiền tố nên 2 gate đều PASS — chứng minh pipeline " +
    "(đủ chuỗi, đúng thứ tự, placeholder còn nguyên), KHÔNG chứng minh chất lượng dịch thật " +
    "của m2m100 so với Google; chất lượng thật chỉ đo được trên bản deploy thật.");
  }
}
{ // 4m. Đặc tính m2m100 [FIXTURE]: mock quirk nuốt câu sau khi gặp đoạn
  // nhiều câu — worker gửi từng chuỗi một nên chuỗi 1 câu AN TOÀN, nhưng
  // chuỗi đơn lẻ có nhiều câu VẪN mất câu (giới hạn đã biết, ghi nhận).
  const aiQuirk = new FakeAI({ quirk: true });
  const envK = makeEnv({ GOOGLE_API_KEY: "", AI: aiQuirk });
  const singleVals = QUALITY_SINGLE.map((k) => QUALITY_EN[k]);
  const multiVals = QUALITY_MULTI.map((k) => QUALITY_EN[k]);
  const rS = await workerPost(envK, { texts: singleVals, target: "vi", source: "en" });
  const rM = await workerPost(envK, { texts: multiVals, target: "vi", source: "en" });
  if (!Array.isArray(rS.data.translated) || !Array.isArray(rM.data.translated)) {
    rec("ai_quirk_documented", false,
      "[FIXTURE] worker trả lỗi, không có translations: single -> " + JSON.stringify(rS.data) +
      " | multi -> " + JSON.stringify(rM.data));
  } else {
  const okSingle = rS.data.translated.every((t, i) =>
    t === "VI-AI: " + singleVals[i]);
  const dropped = rM.data.translated.filter((t, i) =>
    t !== "VI-AI: " + multiVals[i]).length;
  const srcM = {};
  const outM = {};
  QUALITY_MULTI.forEach((k, i) => { srcM[k] = QUALITY_EN[k]; outM[k] = rM.data.translated[i]; });
  const gateM = gateCheckPlaceholders(srcM, outM);
  rec("ai_quirk_documented",
    okSingle && dropped === QUALITY_MULTI.length && gateM.status === "fail",
    "[FIXTURE] mock quirk (nuốt câu sau khi gặp đoạn nhiều câu): " +
    QUALITY_SINGLE.length + " chuỗi 1 câu qua worker -> đầy đủ: " + okSingle +
    " (worker gửi từng chuỗi một nên không mất câu) | " + QUALITY_MULTI.length +
    " chuỗi đơn lẻ có 2 câu -> mất câu sau: " + dropped + "/" + QUALITY_MULTI.length +
    " | gate trên output mất câu: " + gateM.status + " (failed=" + gateM.counts.failed +
    ") — ĐÂY LÀ GIỚI HẠN ĐÃ BIẾT của m2m100: 1 chuỗi game có nhiều câu vẫn có " +
    "thể mất câu; cổng kiểm tra bước 6 bắt được trường hợp này trước khi ép file.");
  }
}

// ================= 5. Kiểm tra tĩnh =================
function read(p) { return fs.readFileSync(p, "utf8"); }
function has(p, s) { try { return read(p).includes(s); } catch (e) { return false; } }

{ // 5a. docs/API_KEY_SETUP.md đầy đủ bước, không chứa key thật
  const p = SITE + "/docs/API_KEY_SETUP.md";
  const ok = fs.existsSync(p) &&
    has(p, "GOOGLE_API_KEY") && has(p, "Cloud Translation API") &&
    has(p, "wrangler secret put GOOGLE_API_KEY") &&
    has(p, "wrangler kv namespace create TRANSLATE_CACHE") &&
    has(p, "wrangler kv namespace create TRANSLATE_USAGE") &&
    has(p, "wrangler deploy") && has(p, "500.000") &&
    has(p, "Kiểm tra worker") && has(p, "quota_exceeded") &&
    has(p, "not_configured") && has(p, "Restrict key") &&
    has(p, "Billing") &&
    has(p, "Workers AI") && has(p, "@cf/meta/m2m100-1.2b") &&
    has(p, "TRANSLATE_PROVIDER") && has(p, "10.000 neurons") &&
    has(p, "không cần thẻ") &&
    !/AIza[0-9A-Za-z_-]{10,}/.test(read(p));
  rec("static_docs_api_key_setup", ok,
    "docs/API_KEY_SETUP.md có đủ: tạo key Google + restrict key, KV, secret, deploy, " +
    "kiểm tra worker, bảng lỗi quota/not_configured, lưu ý Billing required; " +
    "đường Workers AI (model m2m100-1.2b, TRANSLATE_PROVIDER, 10.000 neurons/ngày, không cần thẻ); " +
    "không chuỗi AIza (key thật).");
}
{ // 5b. Nối gt.js / worker.js / app.js / index.html / wrangler.toml (T1)
  const checks = [
    ["gt.js", "export async function checkProxyStatus"],
    ["gt.js", "normalizeProxyUrl"],
    ["gt.js", "PROXY_CHECK_TIMEOUT_MS"],
    ["gt.js", "provider"],
    ["worker.js", "check_translate_proxy"],
    ["worker.js", "checkProxyStatus"],
    ["app.js", "checkTranslateWorkerStatus"],
    ["app.js", "btn-translate-worker-check"],
    ["app.js", "proxyStatusText"],
    ["app.js", "refreshTranslateWorkerStatus"],
    ["app.js", "Workers AI"],
    ["index.html", 'id="btn-translate-worker-check"'],
    ["index.html", 'id="translate-worker-status"'],
    ["index.html", "docs/API_KEY_SETUP.md"],
    ["cloudflare/worker.js", "@cf/meta/m2m100-1.2b"],
    ["cloudflare/worker.js", "resolveProvider"],
    ["cloudflare/worker.js", "callWorkersAI"],
    ["cloudflare/worker.js", "translateBatch"],
    ["cloudflare/worker.js", "AI_DAILY_NEURON_GUARD"],
    ["cloudflare/wrangler.toml", "[ai]"],
    ["cloudflare/wrangler.toml", 'binding = "AI"'],
    ["cloudflare/wrangler.toml", "TRANSLATE_PROVIDER"],
  ];
  const missing = checks.filter(([f, s]) => !has(SITE + "/" + f, s));
  rec("static_wiring_t1", missing.length === 0,
    "thiếu: " + (missing.length ? JSON.stringify(missing) : "không có") +
    " | gt.js có checkProxyStatus + normalizeProxyUrl + provider; worker.js có op " +
    "check_translate_proxy; app.js có nút + hàm trạng thái + nhãn Workers AI; index.html có " +
    "nút + dòng trạng thái + trỏ docs/API_KEY_SETUP.md; cloudflare/worker.js có " +
    "m2m100 + resolveProvider + callWorkersAI + translateBatch + guard neurons; " +
    "wrangler.toml có [ai] binding + TRANSLATE_PROVIDER.");
}
{ // 5c. Không rò key Google trong file mới/sửa của T1
  const files = [
    SITE + "/gt.js", SITE + "/worker.js", SITE + "/app.js", SITE + "/index.html",
    SITE + "/docs/API_KEY_SETUP.md", SITE + "/cloudflare/worker.js",
    SITE + "/cloudflare/wrangler.toml",
  ];
  const bad = files.filter((f) => /AIza[0-9A-Za-z_-]{10,}/.test(read(f)));
  rec("static_no_key_leak_t1", bad.length === 0,
    "file chứa key Google (AIza...): " + (bad.length ? bad.join(", ") : "không có"));
}

server.close();
globalThis.fetch = realFetch;

fs.writeFileSync(EXP + "/worker_t1_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
const md = ["# Kết quả kiểm chứng T1 (Cloudflare Worker + key Google + Workers AI)", "",
  "Chạy: `node verify_worker_t1.mjs` (không cần mạng thật, key thật hay tài khoản Cloudflare).",
  "Worker Cloudflare import thật + KV giả + Google stub (trả `VI: ` + text gốc) + AI giả (trả `VI-AI: ` + text gốc).",
  "Chất lượng dùng chuỗi 9K thật + gate check_placeholders thật — kết quả là [FIXTURE] (mock, không đo được chất lượng model thật).", "",
  "| Kiểm tra | Kết quả | Chi tiết |",
  "|---|---|---|",
  ...Object.entries(results).map(([k, v]) =>
    "| `" + k + "` | " + (v.ok ? "PASS" : "FAIL") + " | " + String(v.detail).replace(/\n/g, " ") + " |"),
  "",
  "## Kết luận",
  failures === 0 ? "TẤT CẢ XANH." : failures + " FAIL.",
  "",
].join("\n");
fs.writeFileSync(EXP + "/worker_t1_results.md", md);
console.log("\n==== KẾT LUẬN: " + (failures === 0 ? "TẤT CẢ XANH" : failures + " FAIL") + " ====");
process.exit(failures === 0 ? 0 : 1);
