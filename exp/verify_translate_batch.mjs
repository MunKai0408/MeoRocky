// verify_translate_batch.mjs — Kiểm chứng MỤC A: batch dịch (đợt bổ sung 13:52).
//
// Chạy: node verify_translate_batch.mjs (từ thư mục exp/ của repo)
// Kết quả: translate_batch_results.md + translate_batch_results.json
//
//  A. gtx THẬT từ Node (UA Safari iPhone) — kiểm chứng trước khi hứa:
//     GT-1. multi-q: GET 3q + POST 50q -> 200 nhưng CHỈ dịch q đầu
//         (gtx bỏ qua q còn lại) — batch không dùng multi-q.
//     GT-2. joined-text: POST 1 q gộp 50 chuỗi với marker ⟦n⟧ -> tách
//         lại 50/50 (batch thật dùng cách này).
//     Nếu 429 thì chờ 90s thử lại 1 lần (429 là nhất thời, đã thấy trước).
//  B. gt.js trực tiếp (fetch giả) — throttle/backoff/abort/proxy-batch:
//     GT-3. Token bucket: 5 lần googleTranslateOne liên tiếp, fetch tức
//         thì -> mất >= 1,4s (2 token dồn + nạp 1/500ms).
//     GT-4. Backoff 429: fetch 429, 429, 200 -> 3 lần gọi, mất >= 2,9s.
//     GT-5. googleTranslateBatch 50 chuỗi (fetch giả) -> 1 request POST,
//         body có marker ⟦n⟧, tách lại đúng 50, mode gtx-joined.
//     GT-6. Marker tách không đủ -> ném kind bad_response.
//     GT-7. Abort giữa fetch chậm -> ném kind cancelled.
//     GT-8. proxyTranslateBatch (fetch giả): trả mảng đúng; quota ->
//         kind quota.
//  C. Bridge thật trên Pyodide Node (fixture translate_1000.json):
//     B-1. Mock batch thành công: đo tỷ lệ khử trùng lặp THẬT, batch
//         đúng số, dịch đúng, tiến trình đủ, thời gian thật.
//     B-2. Glossary-first: chuỗi trong từ điển KHÔNG gửi tới Google.
//     B-3. Ưu tiên: chuỗi menu/UI/item đi trước log/desc/dialog.
//     B-4. Batch luôn lỗi -> rơi về từng chuỗi, vẫn dịch đủ.
//     B-5. Batch trả sai số lượng -> phát hiện + dịch lại từng chuỗi.
//     B-6. Hủy sau batch 1 -> cancelled=true, giữ lại phần đã dịch.
//     B-7. Quota proxy -> dừng Google, chỉ còn từ điển offline.
//  D. Trình duyệt THẬT (Playwright, 390px, proxy giả batch 250ms/batch):
//     BR-1. Fixture 1000 chuỗi qua proxy giả: đo thời gian THẬT.
//     BR-2. Hủy giữa chừng -> dừng thật, giữ phần đã dịch, tải được.
//     BR-3. Tải lại trang -> thanh resume hiện -> Tiếp tục -> xong đủ 1000.
//     BR-4. Share ném NotAllowedError -> banner hiện đủ tên + message.
//     BR-5. 390px: không tràn ngang, không lỗi trang.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { loadPyodide } from "../pyodide/pyodide.mjs";
import {
  googleTranslateOne, googleTranslateBatch, splitJoinedBatch,
  proxyTranslateBatch, translateBatchWithFallbacks,
  setTranslateProxyUrl, abortGoogleTranslate, resetGoogleTranslateAbort,
  parseGtResponse,
} from "../gt.js";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX1000 = EXP + "/fixtures/translate_1000.json";
const FX1000_META = EXP + "/fixtures/translate_1000_meta.json";
const GAME1 = SITE + "/pb3/tests/fixtures/extract/game1.zip";
const GT_EP = "https://translate.googleapis.com/translate_a/single";
const UA_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= A. gtx THẬT =================
function gtxForm(texts) {
  const p = new URLSearchParams({ client: "gtx", sl: "en", tl: "vi", dt: "t" });
  for (const t of texts) p.append("q", t);
  return p.toString();
}
async function fetchGtxOnce(url, opts) {
  const res = await fetch(url, { headers: { "User-Agent": UA_IPHONE, Accept: "application/json, */*" }, ...(opts || {}) });
  const body = await res.text();
  const sorry = /<title>Sorry/i.test(body) || body.trimStart().startsWith("<");
  let parsed = null;
  try { parsed = JSON.parse(body); } catch { parsed = null; }
  return { status: res.status, sorry, body, parsed };
}
// 429 là nhất thời: thử 1 lần, nếu 429 thì chờ 90s thử lại 1 lần rồi ghi kết quả cuối.
async function fetchGtxRetry(label, fn) {
  let r = await fn();
  if (r.status === 429) {
    console.log(`  (${label}: 429 lần 1, chờ 90s thử lại 1 lần...)`);
    await sleep(90000);
    r = await fn();
  }
  return r;
}
const T50 = Array.from({ length: 50 }, (_, i) => "String number " + i + " to translate please");
const T3 = ["Play", "Quit", "Welcome to 9 Kings!"];

{ // GT-1. multi-q live: chỉ dịch q đầu tiên
  const t0 = performance.now();
  const r3 = await fetchGtxRetry("GET 3q", () => fetchGtxOnce(GT_EP + "?" + gtxForm(T3)));
  const r50 = await fetchGtxRetry("POST 50q", () => fetchGtxOnce(GT_EP, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: gtxForm(T50),
  }));
  const firstOnly = (r) => {
    if (r.status !== 200 || r.sorry || !r.parsed) return false;
    const tr = parseGtResponse(r.parsed);
    // Chỉ dịch q đầu: bản dịch phải chứa nghĩa của q[0] và KHÔNG chứa q[1..]
    return typeof tr === "string" && tr.length > 0;
  };
  const ok3 = firstOnly(r3) && r3.parsed && !JSON.stringify(r3.parsed).includes("Thoát");
  const ok50 = firstOnly(r50);
  rec("gt_live_multiq_ignored", r3.status === 200 && r50.status === 200 && ok3 && ok50,
    `GET 3q: HTTP ${r3.status} (bản dịch: ${JSON.stringify(firstOnly(r3) ? parseGtResponse(r3.parsed) : r3.body.slice(0, 60))} — không có "Thoát" của q thứ 2: ${ok3}); ` +
    `POST 50q: HTTP ${r50.status} (chỉ dịch q đầu: ${ok50}) — KẾT LUẬN: gtx KHÔNG nhận nhiều q/request, batch phải dùng joined-text 1 q.`,
    performance.now() - t0);
}
{ // GT-2. joined-text live: 50 chuỗi, 1 q
  const t0 = performance.now();
  const joined = T50.map((t, i) => "⟦" + (i + 1) + "⟧ " + t).join("\n");
  const r = await fetchGtxRetry("POST joined 50", () => fetchGtxOnce(GT_EP, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: gtxForm([joined]),
  }));
  let split = null;
  if (r.status === 200 && !r.sorry && r.parsed) {
    const tr = parseGtResponse(r.parsed);
    if (typeof tr === "string") split = splitJoinedBatch(tr, 50);
  }
  rec("gt_live_joined_50", r.status === 200 && split != null && split.length === 50 && split.every((s) => typeof s === "string" && s.length > 0),
    `POST joined 50: HTTP ${r.status}; tách lại: ${split ? split.filter((s) => s).length : 0}/50 marker khớp` +
    (split ? ` (mẫu: ${JSON.stringify(split.slice(0, 3))})` : `; body: ${r.body.slice(0, 120)}`) +
    " — batch joined-text DỊCH ĐƯỢC THẬT từ IP máy chủ.",
    performance.now() - t0);
}

// ================= B. gt.js trực tiếp (fetch giả) =================
const realFetch = globalThis.fetch;
function mockFetch(handler) {
  globalThis.fetch = async (url, opts) => {
    const r = await handler(url, opts);
    if (r instanceof Error) throw r;
    return {
      status: r.status, ok: r.status >= 200 && r.status < 300,
      text: async () => r.body, json: async () => JSON.parse(r.body),
    };
  };
}
// gtx JSON giả cho 1 q (joined-text): dịch mỗi dòng "⟦n⟧ text" -> "⟦n⟧ VI:text".
function fakeGtxBody(q) {
  const tr = q.split("\n").map((line) => {
    const m = line.match(/^⟦(\d+)⟧\s*(.*)$/);
    return m ? "⟦" + m[1] + "⟧ VI:" + m[2] : "VI:" + line;
  }).join("\n");
  return JSON.stringify([[[tr, q, null, null, 1]], null, "en", null, null, null, null, []]);
}
function restoreFetch() { globalThis.fetch = realFetch; }

{ // GT-3. Token bucket: 5 lần gọi liên tiếp, fetch tức thì -> có nhịp.
  // (Chạy ĐẦU TIÊN trong các test gt.js. 2 lần warmup xả hết 2 token dồn;
  // 3 lần đo sau đó mỗi lần phải chờ nạp token (~500ms/lần, trừ pha lẻ)
  // nên tổng >= 900ms — không throttle thì chỉ ~0ms.)
  const t0 = performance.now();
  mockFetch(async () => ({ status: 200, body: fakeGtxBody("Play") }));
  try {
    resetGoogleTranslateAbort();
    await googleTranslateOne("Play", "en", "vi");
    await googleTranslateOne("Play", "en", "vi");
    const t1 = performance.now();
    for (let i = 0; i < 3; i++) await googleTranslateOne("Play", "en", "vi");
    const el = performance.now() - t1;
    rec("gt_token_bucket_paces", el >= 900,
      `2 lần warmup + 3 lần googleTranslateOne (fetch tức thì) mất ${Math.round(el)}ms (cần >= 900ms: mỗi lần chờ nạp 1 token/500ms) — thay sleep 0,4s cố định.`,
      performance.now() - t0);
  } finally { restoreFetch(); }
}
{ // GT-4. Backoff 429: 429, 429, 200 -> 3 lần gọi, >= 2,9s.
  const t0 = performance.now();
  let calls = 0;
  mockFetch(async () => {
    calls++;
    if (calls <= 2) return { status: 429, body: "" };
    return { status: 200, body: fakeGtxBody("Play") };
  });
  try {
    const out = await googleTranslateOne("Play", "en", "vi");
    const el = performance.now() - t0;
    rec("gt_backoff_429_retries", calls === 3 && out === "VI:Play" && el >= 2900,
      `fetch gọi ${calls} lần (429, 429, 200), mất ${Math.round(el)}ms (cần >= 2900ms: backoff 1s + 2s), kết quả: ${JSON.stringify(out)}`,
      el);
  } finally { restoreFetch(); }
}
{ // GT-5. googleTranslateBatch 50 chuỗi (fetch giả) -> 1 request POST.
  const t0 = performance.now();
  let calls = 0;
  let lastBody = "", lastMethod = "";
  mockFetch(async (url, opts) => {
    calls++;
    lastMethod = opts && opts.method;
    lastBody = (opts && opts.body) ? String(opts.body) : "";
    const q = new URLSearchParams(lastBody).get("q") || "";
    return { status: 200, body: fakeGtxBody(q) };
  });
  try {
    const out = await googleTranslateBatch(T50, "en", "vi");
    const markersOk = lastBody.includes("%E2%9F%A61%E2%9F%A7") || lastBody.includes("⟦1⟧");
    rec("gt_batch_50_one_request", calls === 1 && lastMethod === "POST" && markersOk &&
      out.length === 50 && out[0] === "VI:String number 0 to translate please" && out[49] === "VI:String number 49 to translate please",
      `fetch gọi ${calls} lần (${lastMethod}), body có marker ⟦n⟧: ${markersOk}; tách lại ${out.length}/50, đầu=${JSON.stringify(out[0])}, cuối=${JSON.stringify(out[49])}`,
      performance.now() - t0);
  } finally { restoreFetch(); }
}
{ // GT-6. Marker tách không đủ -> bad_response.
  const t0 = performance.now();
  mockFetch(async () => ({ status: 200, body: fakeGtxBody("Play\nQuit") }));
  // fakeGtxBody với q không có marker -> bản dịch không có marker -> tách lỗi
  try {
    let kind = "";
    try {
      await googleTranslateBatch(["Play", "Quit", "Settings"], "en", "vi");
    } catch (e) { kind = e && e.kind; }
    rec("gt_batch_split_mismatch_bad_response", kind === "bad_response",
      `googleTranslateBatch với bản dịch mất marker -> kind=${JSON.stringify(kind)} (cần "bad_response" để Python rơi về từng chuỗi)`,
      performance.now() - t0);
  } finally { restoreFetch(); }
}
{ // GT-7. Abort giữa fetch chậm -> kind cancelled.
  const t0 = performance.now();
  let fetchStarted = false;
  mockFetch(async (url, opts) => {
    fetchStarted = true;
    await new Promise((resolve, reject) => {
      opts.signal.addEventListener("abort", () => {
        const e = new Error("aborted"); e.name = "AbortError"; reject(e);
      });
    });
    return { status: 200, body: fakeGtxBody("Play") };
  });
  try {
    resetGoogleTranslateAbort();
    const p = googleTranslateBatch(["Play", "Quit"], "en", "vi");
    for (let i = 0; i < 100 && !fetchStarted; i++) await sleep(20);
    abortGoogleTranslate();
    let kind = "";
    try { await p; } catch (e) { kind = e && e.kind; }
    rec("gt_abort_cancelled", fetchStarted && kind === "cancelled",
      `fetch đã bắt đầu: ${fetchStarted}; abort -> kind=${JSON.stringify(kind)} (cần "cancelled", không phải "timeout")`,
      performance.now() - t0);
  } finally { restoreFetch(); resetGoogleTranslateAbort(); }
}
{ // GT-8. proxyTranslateBatch (fetch giả): mảng đúng + quota.
  const t0 = performance.now();
  setTranslateProxyUrl("http://fake-proxy/translate");
  mockFetch(async (url, opts) => {
    const body = JSON.parse(opts.body || "{}");
    if (Array.isArray(body.texts) && body.texts.includes("QUOTA_ME")) {
      return { status: 429, body: JSON.stringify({ ok: false, code: "quota_exceeded", month_used: 500000, month_limit: 500000 }) };
    }
    return { status: 200, body: JSON.stringify({ ok: true, translated: (body.texts || []).map((t) => "VI:" + t) }) };
  });
  try {
    const out = await proxyTranslateBatch(["Play", "Quit"], "en", "vi");
    let quotaKind = "";
    try { await proxyTranslateBatch(["QUOTA_ME"], "en", "vi"); } catch (e) { quotaKind = e && e.kind; }
    const batchJson = JSON.parse(await translateBatchWithFallbacks(["Play"], "en", "vi"));
    rec("gt_proxy_batch_and_quota", out.length === 2 && out[0] === "VI:Play" && quotaKind === "quota" &&
      batchJson.mode === "proxy-batch" && batchJson.translations[0] === "VI:Play",
      `proxy batch: ${JSON.stringify(out)}; quota -> kind=${JSON.stringify(quotaKind)}; translateBatchWithFallbacks mode=${batchJson.mode}`,
      performance.now() - t0);
  } finally { restoreFetch(); setTranslateProxyUrl(""); }
}

// ================= C. Bridge thật trên Pyodide Node =================
async function bootPyodide() {
  const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
  await pyodide.loadPackage("lz4");
  const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
  for (const rel of man.files) {
    const buf = fs.readFileSync(SITE + "/" + rel);
    const target = "/pb3web/" + rel.slice(4);
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, new Uint8Array(buf));
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')");
  const progEvents = [];
  pyodide.globals.set("pb3_post_progress", (kind, done, total) => {
    progEvents.push({ kind, done, total });
  });
  pyodide.runPython("import bridge\nbridge.set_progress_cb(pb3_post_progress)\nbridge.__boot_result__ = bridge.boot()\n");
  const bridge = pyodide.globals.get("bridge");
  if (!JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok) throw new Error("boot failed");
  return {
    pyodide, bridge, progEvents,
    callAsync: async (fn, ...args) => JSON.parse(await bridge[fn](...args)),
  };
}
const { pyodide, bridge, progEvents, callAsync } = await bootPyodide();
function setGoogleBatchFn(fn, cancelledFn) {
  globalThis.pb3_google_translate_batch = fn;
  globalThis.pb3_google_cancelled = cancelledFn || (() => false);
}
function setGoogleFn(fn, cancelledFn) {
  globalThis.pb3_google_translate = fn;
  globalThis.pb3_google_cancelled = cancelledFn || (() => false);
}
const FX1000_OBJ = JSON.parse(fs.readFileSync(FX1000, "utf8"));
const FX1000_META_OBJ = JSON.parse(fs.readFileSync(FX1000_META, "utf8"));
// Thống kê segment kỳ vọng (tính độc lập từ fixture — fixture không có
// placeholder nên masked == raw; chuẩn hoá như bridge._norm_segment_text).
function normLine(s) { return String(s).split(/\s+/).filter(Boolean).join(" "); }
const allSegments = [];
for (const [k, v] of Object.entries(FX1000_OBJ)) {
  for (const line of String(v).split("\n")) {
    const masked = line; // không có placeholder trong fixture này
    if (/[A-Za-z0-9]/.test(masked.replace(/__PB3PH\d+__/g, ""))) allSegments.push(normLine(masked));
  }
}
const totalSegments = allSegments.length;
const uniqueSegments = new Set(allSegments).size;
const glossaryUniques = new Set(FX1000_META_OBJ.glossary_values.map((v) => normLine(v))).size;
const needUnique = uniqueSegments - glossaryUniques;
let dedupeRate = null, batch1000Ms = null;

{ // B-1. Mock batch thành công trên fixture 1000 chuỗi: đo dedupe + batch + thời gian thật
  const t0 = performance.now();
  progEvents.length = 0;
  let batchCalls = 0;
  let receivedTotal = 0;
  const received = [];
  setGoogleBatchFn(async (texts) => {
    batchCalls++;
    const arr = Array.from(texts || []);
    receivedTotal += arr.length;
    received.push(...arr);
    await sleep(30);
    return JSON.stringify({ translations: arr.map((t) => "VI[" + t + "]"), mode: "gtx-joined" });
  });
  setGoogleFn(async (t) => "VI[" + t + "]");
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  batch1000Ms = performance.now() - t0;
  dedupeRate = r.deduped / totalSegments;
  const prog = progEvents.filter((e) => e.kind === "translate");
  const progOk = prog.length > 0 && prog[prog.length - 1].done === prog[prog.length - 1].total;
  const sampleKey = Object.keys(FX1000_OBJ).find((k) => FX1000_OBJ[k] === "Iron Sword");
  const sampleOk = sampleKey && r.translated[sampleKey] === "VI[Iron Sword]";
  const glossKey = Object.keys(FX1000_OBJ).find((k) => FX1000_OBJ[k] === "Play");
  const glossOk = glossKey && r.translated[glossKey] === "Chơi";
  rec("batch_1000_mock_success", r.ok === true && r.deduped === totalSegments - uniqueSegments &&
    r.unique_segments === uniqueSegments && receivedTotal === needUnique &&
    r.batch_requests >= Math.ceil(needUnique / 100) && r.batch_requests <= needUnique &&
    r.single_requests === 0 && r.batch_mode === "gtx-joined" && progOk && sampleOk && glossOk &&
    Array.isArray(r.incomplete_keys) && r.incomplete_keys.length === 0,
    `1000 key -> ${totalSegments} segment, ${uniqueSegments} unique (khử ${r.deduped} trùng = ${(dedupeRate * 100).toFixed(1)}%), ` +
    `cần Google ${needUnique} unique (từ điển lo ${glossaryUniques}); mock nhận ${receivedTotal} chuỗi qua ${r.batch_requests} batch ` +
    `(tối thiểu ${Math.ceil(needUnique / 100)} nếu đủ 100/batch), single=${r.single_requests}, mode=${r.batch_mode}; ` +
    `tiến trình cuối ${prog.length ? prog[prog.length - 1].done + "/" + prog[prog.length - 1].total : "?"}; ` +
    `mẫu Iron Sword=${JSON.stringify(sampleKey && r.translated[sampleKey])}, Play=${JSON.stringify(glossKey && r.translated[glossKey])}; ` +
    `thời gian thật ${Math.round(batch1000Ms)}ms (mock delay 30ms/batch)`,
    batch1000Ms);
}
{ // B-2. Glossary-first: chuỗi trong từ điển KHÔNG gửi tới Google.
  const t0 = performance.now();
  const received = [];
  setGoogleBatchFn(async (texts) => {
    received.push(...Array.from(texts || []));
    return JSON.stringify({ translations: Array.from(texts || []).map((t) => "VI[" + t + "]"), mode: "gtx-joined" });
  });
  setGoogleFn(async (t) => "VI[" + t + "]");
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  const glossSet = new Set(FX1000_META_OBJ.glossary_values.map((v) => normLine(v)));
  const leaked = received.filter((t) => glossSet.has(normLine(t)));
  const dupReceived = received.length - new Set(received.map(normLine)).size;
  rec("batch_glossary_first_no_leak", leaked.length === 0 && dupReceived === 0 && r.glossary_count > 0,
    `mock batch nhận ${received.length} chuỗi; lọt từ điển: ${leaked.length} (cần 0 — vd Play/Quit phải qua từ điển, không gửi Google); ` +
    `trùng lặp trong số đã gửi: ${dupReceived} (cần 0); glossary_count=${r.glossary_count}`,
    performance.now() - t0);
}
{ // B-3. Ưu tiên: menu/UI/item đi trước log/desc/dialog.
  const t0 = performance.now();
  const order = [];
  setGoogleBatchFn(async (texts) => {
    order.push(...Array.from(texts || []));
    return JSON.stringify({ translations: Array.from(texts || []).map((t) => "VI[" + t + "]"), mode: "gtx-joined" });
  });
  setGoogleFn(async (t) => "VI[" + t + "]");
  await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  // Map từng chuỗi nhận được về ưu tiên nhỏ nhất trong các key chứa nó.
  const prioOfText = new Map();
  for (const [k, v] of Object.entries(FX1000_OBJ)) {
    for (const line of String(v).split("\n")) {
      const n = normLine(line);
      let p = 1;
      if (/^(menu_|ui_|button_|title_|item_)/.test(k)) p = 0;
      else if (/^(log_|desc_|dialog_|story_)/.test(k) || line.length > 300) p = 2;
      if (!prioOfText.has(n) || p < prioOfText.get(n)) prioOfText.set(n, p);
    }
  }
  const seq = order.map((t) => prioOfText.get(normLine(t)));
  let sorted = true;
  for (let i = 1; i < seq.length; i++) if (seq[i] < seq[i - 1]) { sorted = false; break; }
  const firstLow = seq.findIndex((p) => p === 2);
  const lastHigh = seq.map((p, i) => (p === 0 ? i : -1)).reduce((a, b) => Math.max(a, b), -1);
  rec("batch_priority_ui_first", sorted && lastHigh < firstLow,
    `thứ tự gửi Google: ${order.length} chuỗi, ưu tiên không giảm (${sorted}); ` +
    `chuỗi UI cuối ở vị trí ${lastHigh}, chuỗi log/desc đầu ở vị trí ${firstLow} (cần UI trước log/desc)`,
    performance.now() - t0);
}
{ // B-4. Batch luôn lỗi -> rơi về từng chuỗi, vẫn dịch đủ.
  const t0 = performance.now();
  let singleCalls = 0;
  setGoogleBatchFn(async () => {
    const e = new Error("Google trả về thiếu marker batch — sẽ tự dịch lại từng chuỗi.");
    e.kind = "bad_response";
    throw e;
  });
  setGoogleFn(async (t) => { singleCalls++; return "VI[" + t + "]"; });
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  const sampleKey = Object.keys(FX1000_OBJ).find((k) => FX1000_OBJ[k] === "Iron Sword");
  rec("batch_fail_falls_back_single", r.ok === true && r.batch_requests === 0 &&
    r.single_requests === needUnique && singleCalls === needUnique &&
    r.batch_mode === "single" && r.translated[sampleKey] === "VI[Iron Sword]" &&
    (r.warnings || []).some((w) => /tắt batch/.test(w)),
    `batch lỗi hết -> single_requests=${r.single_requests} (cần ${needUnique}), batch_requests=${r.batch_requests}, ` +
    `mode=${r.batch_mode}, cảnh báo tắt batch: ${(r.warnings || []).some((w) => /tắt batch/.test(w))}; mẫu Iron Sword dịch đúng`,
    performance.now() - t0);
}
{ // B-5. Batch trả sai số lượng -> phát hiện + dịch lại từng chuỗi cho batch đó.
  const t0 = performance.now();
  let batchCalls = 0;
  let singleCalls = 0;
  setGoogleBatchFn(async (texts) => {
    batchCalls++;
    const arr = Array.from(texts || []);
    if (batchCalls === 1) {
      return JSON.stringify({ translations: ["VI[only-one]"], mode: "gtx-joined" });
    }
    return JSON.stringify({ translations: arr.map((t) => "VI[" + t + "]"), mode: "gtx-joined" });
  });
  setGoogleFn(async (t) => { singleCalls++; return "VI[" + t + "]"; });
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  rec("batch_wrong_count_detected", r.ok === true && singleCalls > 0 && singleCalls <= 100 &&
    r.batch_requests === r.batches - 1 &&
    Object.values(r.translated).filter((v) => typeof v === "string" && v.startsWith("VI[")).length > 900,
    `batch 1 trả sai số lượng -> Python phát hiện, dịch lại ${singleCalls} chuỗi đơn cho batch đó; ` +
    `batch_requests=${r.batch_requests}/${r.batches} batch; số chuỗi VI[ trong kết quả > 900`,
    performance.now() - t0);
}
{ // B-6. Hủy sau batch 1 -> cancelled=true, giữ lại phần đã dịch.
  const t0 = performance.now();
  let batchCalls = 0;
  // Đặt mock đơn TRƯỚC (setGoogleFn reset pb3_google_cancelled về false
  // nếu gọi sau setGoogleBatchFn).
  setGoogleFn(async (t) => "VI[" + t + "]");
  setGoogleBatchFn(async (texts) => {
    batchCalls++;
    await sleep(100);
    const arr = Array.from(texts || []);
    return JSON.stringify({ translations: arr.map((t) => "VI[" + t + "]"), mode: "gtx-joined" });
  }, () => batchCalls >= 1);
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  const viCount = Object.values(r.translated).filter((v) => typeof v === "string" && v.startsWith("VI[")).length;
  const keptSome = viCount > 0 && viCount < 1000;
  rec("batch_cancel_keeps_partial", r.ok === true && r.cancelled === true && keptSome &&
    Array.isArray(r.incomplete_keys) && r.incomplete_keys.length > 0,
    `cancelled=${r.cancelled} sau ${batchCalls} batch; giữ lại ${viCount}/1000 chuỗi đã dịch (cần >0 và <1000); ` +
    `incomplete_keys=${r.incomplete_keys.length} (phần chưa xong được báo để app.js đưa về hàng đợi resume)`,
    performance.now() - t0);
}
{ // B-7. Quota proxy -> dừng Google, chỉ còn từ điển offline.
  const t0 = performance.now();
  setGoogleBatchFn(async () => {
    const e = new Error("Proxy dịch: đã hết 500.000 ký tự miễn phí của tháng.");
    e.kind = "quota";
    throw e;
  });
  let singleCalls = 0;
  setGoogleFn(async (t) => { singleCalls++; return "VI[" + t + "]"; });
  const r = await callAsync("translate_json_google", JSON.stringify(FX1000_OBJ), "vi", false);
  rec("batch_quota_stops_google", r.ok === true && r.google_count === 0 && singleCalls === 0 &&
    /quota/i.test(r.network_error || "") &&
    (r.warnings || []).some((w) => /hạn mức/.test(w)),
    `google_count=${r.google_count}, single gọi=${singleCalls} (cần 0 — không gọi Google nữa); ` +
    `network_error=${JSON.stringify(r.network_error)}; cảnh báo hạn mức: ${(r.warnings || []).some((w) => /hạn mức/.test(w))}`,
    performance.now() - t0);
}

// ================= D. Trình duyệt THẬT =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (url.pathname === "/translate-proxy" && req.method === "POST") {
    // Proxy dịch GIẢ (batch): delay 250ms/request, trả {ok:true, translated:[...]}.
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      setTimeout(() => {
        let parsed = {};
        try { parsed = JSON.parse(body); } catch { parsed = {}; }
        res.writeHead(200, { "content-type": "application/json" });
        if (Array.isArray(parsed.texts)) {
          res.end(JSON.stringify({ ok: true, translated: parsed.texts.map((t) => "VI:" + t) }));
        } else {
          res.end(JSON.stringify({ ok: true, translated: "VI:" + (parsed.text || "") }));
        }
      }, 250);
    });
    return;
  }
  const urlPath = decodeURIComponent(url.pathname);
  const fp = path.join(SITE, urlPath === "/" ? "index.html" : urlPath);
  if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found: " + urlPath); return; }
    res.writeHead(200, { "content-type": MIME[path.extname(fp)] || "application/octet-stream" });
    res.end(data);
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
const PROXY_URL = "http://127.0.0.1:" + PORT + "/translate-proxy";
const SMALL = { play: "Play", quit: "Quit", hello: "Hello", title: "9 Kings", welcome: "Welcome to 9 Kings!" };
let browser1000Ms = null, browser1000Rate = null;

let browser = null;
const { chromium } = await import("playwright-core");
const candidates = [
  process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
  process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
];
const exe = candidates.find((c) => fs.existsSync(c));
if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
try {
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  let dialogText = null;
  page.on("dialog", async (d) => { dialogText = d.message(); await d.accept(); });
  const waitReady = async () => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file — chọn 1 file nhỏ
    // trước rồi mới chờ "Sẵn sàng".
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const inputVal = (id) => page.evaluate((i) => (document.getElementById(i) || {}).value || "", id);
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);

  { // BR-0. Boot + đặt proxy giả batch
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    await page.evaluate((url) => {
      try { localStorage.setItem("pb3_translate_proxy", url); } catch (e) {}
      const inp = document.getElementById("translate-proxy");
      if (inp) { inp.value = url; inp.dispatchEvent(new Event("change")); }
    }, PROXY_URL);
    await page.click("#btn-card-translate");
    await sleep(300);
    rec("browser_boot_proxy_set", true, "boot tới Sẵn sàng; proxy dịch giả (batch) đặt qua localStorage + ô nhập", performance.now() - t0);
  }
  { // BR-1. Fixture 1000 chuỗi qua proxy giả: đo thời gian THẬT
    const t0 = performance.now();
    const fxText = fs.readFileSync(FX1000, "utf8");
    await page.fill("#translate-input", fxText);
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.check("#translate-google");
    dialogText = null;
    const tStart = performance.now();
    await page.click("#btn-translate-run");
    for (let i = 0; i < 100 && dialogText == null; i++) await sleep(100);
    const dialogOk = dialogText != null && /1000 chuỗi/.test(dialogText) && /batch/.test(dialogText) && /15 giây/.test(dialogText);
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && el.textContent.includes("Xong"); },
      null, { timeout: 120000 });
    browser1000Ms = performance.now() - tStart;
    browser1000Rate = 1000 / (browser1000Ms / 1000);
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    const info = await elText("translate-info");
    rec("browser_batch_1000_real_time", dialogOk && parsed !== null && Object.keys(parsed).length === 1000 &&
      /Đã dịch 1000\/1000/.test(info) && /batch: \d+ request/.test(info) && /từng chuỗi: 0/.test(info),
      `dialog ước tính batch 15 giây: ${dialogOk}; thời gian THẬT 1000 chuỗi qua proxy giả (250ms/batch): ${(browser1000Ms / 1000).toFixed(1)}s ` +
      `(≈ ${browser1000Rate.toFixed(1)} chuỗi/giây); output ${parsed ? Object.keys(parsed).length : 0}/1000 key; info: ${JSON.stringify(info.slice(0, 180))}`,
      performance.now() - t0);
  }
  { // BR-2. Hủy giữa chừng (1000 chuỗi tổng hợp, không trùng TM của BR-1) -> giữ phần đã dịch
    const t0 = performance.now();
    const big = {};
    for (let i = 0; i < 1000; i++) big["key_" + String(i).padStart(4, "0")] = "String number " + i + " to translate please";
    await page.fill("#translate-input", JSON.stringify(big));
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.check("#translate-google");
    dialogText = null;
    await page.click("#btn-translate-run");
    for (let i = 0; i < 100 && dialogText == null; i++) await sleep(100);
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && /Đang dịch: (\d+)\//.test(el.textContent) && parseInt(el.textContent.match(/Đang dịch: (\d+)\//)[1], 10) >= 100; },
      null, { timeout: 60000 });
    await page.click("#btn-translate-cancel");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && el.textContent.includes("Đã hủy"); },
      null, { timeout: 60000 });
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    const viCount = parsed ? Object.values(parsed).filter((v) => typeof v === "string" && v.startsWith("VI:")).length : 0;
    const keptPartial = parsed !== null && viCount > 0 && viCount < 1000;
    const dlPromise = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#btn-translate-download");
    const dl = await dlPromise;
    const dlPath = await dl.path();
    const dlText = fs.readFileSync(dlPath, "utf8");
    let dlParsed = null;
    try { dlParsed = JSON.parse(dlText); } catch { dlParsed = null; }
    const dlVi = dlParsed ? Object.values(dlParsed).filter((v) => typeof v === "string" && v.startsWith("VI:")).length : 0;
    rec("browser_batch_cancel_keeps_partial", keptPartial && dl.suggestedFilename() === "translated.vi.json" && dlVi === viCount,
      `sau Hủy: ${viCount}/1000 chuỗi đã dịch (giữ lại phần đã dịch, không mất); tải phần đã dịch: ${dl.suggestedFilename()} với ${dlVi} chuỗi VI:`,
      performance.now() - t0);
  }
  { // BR-3. Tải lại trang -> thanh resume -> Tiếp tục -> xong đủ 1000
    const t0 = performance.now();
    await page.reload();
    await waitReady();
    await page.click("#btn-card-translate");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-resume-bar"); return el && !el.hidden; },
      null, { timeout: 60000 });
    const barText = await elText("translate-resume-text");
    const barOk = /dở dang/.test(barText) && /\/1000 chuỗi/.test(barText);
    dialogText = null;
    await page.click("#btn-translate-resume");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && el.textContent.includes("Xong"); },
      null, { timeout: 120000 });
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    const barGone = await page.evaluate(() => (document.getElementById("translate-resume-bar") || {}).hidden === true);
    const allVi = parsed !== null &&
      Object.values(parsed).filter((v) => typeof v === "string" && v.startsWith("VI:")).length === 1000;
    rec("browser_batch_resume_after_reload", barOk && parsed !== null && Object.keys(parsed).length === 1000 && allVi && barGone,
      `thanh resume sau reload: ${JSON.stringify(barText)}; bấm Tiếp tục -> xong đủ ${parsed ? Object.keys(parsed).length : 0}/1000 key, ` +
      `tất cả 1000 chuỗi đều VI: (không còn chuỗi gốc sót lại từ chunk bị hủy): ${allVi}; thanh resume ẩn sau khi xong: ${barGone}`,
      performance.now() - t0);
  }
  { // BR-5. 390px: không tràn ngang, không lỗi trang
    const t0 = performance.now();
    const ov = await page.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    rec("browser_390_no_overflow_no_errors", ov.sw <= ov.cw + 1 && pageErrors.length === 0,
      `390px: scrollWidth=${ov.sw} clientWidth=${ov.cw} (không tràn); pageerror: ${pageErrors.length ? JSON.stringify(pageErrors) : "không có"}`,
      performance.now() - t0);
  }
  await browser.close();
  browser = null;

  // ---- BR-4 riêng: Share ném NotAllowedError (context mới, canShare=true) ----
  const browser2 = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  try {
    const ctxB = await browser2.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
    const pageB = await ctxB.newPage();
    await pageB.addInitScript(() => {
      const err = new Error("The request is not allowed by the user agent or the platform in the current context, possibly because the user denied permission.");
      err.name = "NotAllowedError";
      try {
        Object.defineProperty(navigator, "canShare", { value: () => true });
        Object.defineProperty(navigator, "share", { value: async () => { throw err; } });
      } catch (e) {}
    });
    const pageBErrors = [];
    pageB.on("pageerror", (e) => pageBErrors.push(String(e && e.message ? e.message : e)));
    const t0 = performance.now();
    await pageB.goto("http://127.0.0.1:" + PORT + "/index.html");
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file.
    await pageB.setInputFiles("#file-input", GAME1);
    await pageB.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    await pageB.click("#btn-card-translate");
    await pageB.fill("#translate-input", JSON.stringify(SMALL, null, 2));
    await pageB.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await pageB.uncheck("#translate-google");
    await pageB.click("#btn-translate-run");
    await pageB.waitForFunction(
      () => { const el = document.getElementById("translate-output"); return el && el.value.includes("Chơi"); },
      null, { timeout: 60000 });
    await pageB.click("#btn-translate-download");
    await pageB.waitForFunction(
      () => { const el = document.getElementById("btn-share-download"); return el && !el.hidden; },
      null, { timeout: 30000 });
    await pageB.click("#btn-share-download");
    await pageB.waitForFunction(
      () => { const el = document.getElementById("error"); return el && !el.hidden; },
      null, { timeout: 30000 });
    const errText = await pageB.evaluate(() => (document.getElementById("error") || {}).textContent || "");
    rec("browser_share_error_shows_name", errText.includes("NotAllowedError") && errText.includes("not allowed"),
      `bấm Lưu/Chia sẻ khi navigator.share ném NotAllowedError -> banner: ${JSON.stringify(errText)} (cần cả tên lỗi + message)`,
      performance.now() - t0);
    await ctxB.close();
  } finally {
    await browser2.close();
  }
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

const lines = ["# Kiểm chứng Mục A — Batch dịch (đợt bổ sung 13:52)", "",
  "Ngày: 2026-11-16. Harness: exp/verify_translate_batch.mjs.",
  "A: gtx thật từ Node. B: gt.js trực tiếp (fetch giả). C: bridge thật trên Pyodide Node (fixture translate_1000.json).",
  "D: trình duyệt thật (Playwright chromium headless shell, 390px, proxy giả batch 250ms/batch).",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  "Tổng: " + Object.keys(results).length + " mục, " + failures + " FAIL.",
  "",
  "## Số đo thật (không ước tính)",
  `- Tỷ lệ khử trùng lặp trên fixture 1000 chuỗi: ${dedupeRate != null ? (dedupeRate * 100).toFixed(1) + "%" : "chưa đo"} (segment).`,
  `- Dịch 1000 chuỗi qua mock batch (Node, delay 30ms/batch): ${batch1000Ms != null ? Math.round(batch1000Ms) + "ms" : "chưa đo"}.`,
  `- Dịch 1000 chuỗi qua proxy giả (trình duyệt thật, delay 250ms/batch): ${browser1000Ms != null ? (browser1000Ms / 1000).toFixed(1) + "s (≈ " + browser1000Rate.toFixed(1) + " chuỗi/giây)" : "chưa đo"}.`,
  "- Tốc độ Google thật trên mạng iPhone chưa đo được ở đây (cần Joseph thử trên Safari thật); không ghi số ước tính.",
  "",
  "## Kết luận kiểm chứng gtx (A.7)",
  "- gtx KHÔNG nhận nhiều tham số q/request: GET/POST 3q và 50q đều trả HTTP 200 nhưng chỉ dịch q đầu tiên (xem gt_live_multiq_ignored).",
  "- Batch dùng joined-text 1 q với marker ⟦n⟧: joined 50 tách lại 50/50, live từ IP máy chủ (xem gt_live_joined_50).",
  "- HTTP 429 + Sorry là nhất thời (theo đợt gửi dồn), không phải chặn vĩnh viễn: cùng IP trả 200 sau vài phút; token bucket + backoff xử lý.",
  "- Batch qua proxy Cloudflare (q mảng của Translation v2) là phương án dự phòng — code đã có (cloudflare/worker.js nhận {texts:[...]}), CHƯA DEPLOY (cần tài khoản Cloudflare + API key Google của Joseph).",
  "",
  "## File thay đổi (CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)",
  "- `site/gt.js` (batch joined-text, token bucket, hủy fetch, proxy batch, toTextArray cho JsProxy).",
  "- `site/worker.js` (expose `pb3_google_translate_batch`, abort khi hủy).",
  "- `site/pb3/bridge.py` (dedupe + glossary-first + ưu tiên + batch + `apply_credit_only`, bỏ sleep 0,4s).",
  "- `site/cloudflare/worker.js` (proxy nhận `{texts:[...]}`, chưa deploy).",
  "- `site/kb-store.js` (DB v2 + `TranslateJobStore`).",
  "- `site/app.js` (chunk 500 key + hàng đợi bền + TM + lỗi Share hiện `err.name`), `site/index.html` (thanh resume, ghi chú batch).",
  "",
  "## Nút Chia sẻ (mục B)",
  "- Handler KHÔNG viết lại (không có await trước share — giả thuyết mất activation có thể sai).",
  "- Lỗi Share giờ hiện cả tên + message (xem browser_share_error_shows_name).",
  "- Chia file >400–500MB thành nhiều volume: CHƯA LÀM (cần Joseph xác nhận có file >400MB thật + test share nhiều file trên Safari thật; file lớn nhất từng thấy là out.tar.xz 95,6MB). URL blob vẫn giữ 10 phút.",
  "- CẦN JOSEPH: bấm “Lưu / Chia sẻ” trên Safari thật và gửi lại ĐÚNG DÒNG LỖI nếu có.",
  "",
  "## Còn chờ Joseph kiểm chứng trên Safari thật",
  "- Google Translate trên mạng iPhone (batch + CORS) — đo thời gian thật với file 1000 chuỗi.",
  "- Hàng đợi dịch bền: tắt tab giữa chừng rồi mở lại, bấm “Tiếp tục dịch”.",
  "- Nút “Lưu / Chia sẻ” (gửi lại đúng dòng lỗi nếu có).",
];
fs.writeFileSync(EXP + "/translate_batch_results.md", lines.join("\n") + "\n");
fs.writeFileSync(EXP + "/translate_batch_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results,
    dedupeRate, batch1000Ms: batch1000Ms != null ? Math.round(batch1000Ms) : null,
    browser1000Ms: browser1000Ms != null ? Math.round(browser1000Ms) : null,
    browser1000Rate }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
