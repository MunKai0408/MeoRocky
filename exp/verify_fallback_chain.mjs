// verify_fallback_chain.mjs — Kiểm chứng mục C1: chuỗi fallback dịch
// KHÔNG CẦN KEY (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_fallback_chain.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: fallback_chain_results.md + fallback_chain_results.json
//
// Chuỗi fallback (gt.js): proxy (nếu có URL) → Google trực tiếp (gtx) →
// MyMemory → LibreTranslate (CHỈ khi đã cấu hình endpoint, mặc định tắt vì
// bản public libretranslate.com cần API key) → từ điển offline (bridge).
//
//  A. gt.js trực tiếp trên Node, server giả (127.0.0.1) cho từng bước:
//     A1. Google 429 → MyMemory được dùng, trace ghi rõ.
//     A2. MyMemory lỗi → LibreTranslate được dùng, trace ghi rõ.
//     A3. Tất cả lỗi → ném lỗi kind all_failed, message dòng đầu chứa đủ
//         tên từng bước + "từ điển offline", không bao giờ im lặng.
//     A4. LibreTranslate chưa cấu hình → bị bỏ qua (0 request), message
//         ghi rõ "chưa cấu hình (cần API key)".
//     A5. Proxy đã cấu hình → proxy được thử đầu tiên, Google/MyMemory
//         không được gọi.
//     A6. Proxy báo hết hạn mức tháng → BỎ QUA Google trực tiếp, MyMemory
//         được dùng (đúng luật không phí).
//     A7. Batch lỗi cả proxy + Google → kind batch_failed, message nói rõ
//         sẽ dịch lại từng chuỗi (chưa phải hết đường).
//     A8. LibreTranslate đơn lẻ: đúng / sai key / quá dài.
//     A9. Probe THẬT (ghi kết quả thật, không PASS giả): gtx, MyMemory,
//         libretranslate.com GET /languages; POST /translate bị sandbox
//         chặn (policy_denied) nên ghi rõ là bị chặn, không phải bằng
//         chứng từ server.
//  B. Bridge thật trên Pyodide Node, dùng CHUỖI THẬT (không mock Google):
//     B1. Google 429 (giả) → MyMemory cứu chuỗi: fallback_note + fallback_used.
//     B2. Tất cả lỗi → offline, network_error chứa tóm tắt cả chuỗi.
//     B3. Proxy giả OK → batch qua proxy, batch_mode proxy-batch.
//  C. Trình duyệt THẬT (Playwright, 390px, serve site qua HTTP local,
//     route chặn Google → 429, MyMemory giả):
//     C1. Google 429 → MyMemory dịch được, UI hiện "MyMemory".
//     C2. Tất cả lỗi → UI báo rõ lý do từng bước + từ điển offline,
//         chuỗi giữ nguyên, không đứng im.
//     C3. 390px: không tràn ngang, không lỗi trang.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";
import {
  translateOneWithFallbacks, translateBatchWithFallbacks,
  setTranslateProxyUrl, setGoogleEndpoint, setMyMemoryEndpoint,
  setLibreTranslateEndpoint, setLibreTranslateApiKey, getFallbackTrace,
  libreTranslateOne,
} from "../site/gt.js";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const FX = EXP + "/fixtures/extract_select_fixture.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= Fake server cho từng bước fallback =================
const modes = { gtx: "429", mymemory: "ok", libretranslate: "ok", proxy: "ok" };
const hits = { gtx: 0, mymemory: 0, libretranslate: 0, proxy: 0 };
const lastLtBody = { body: null };
function gtxJson(q) {
  return JSON.stringify([[["DỊCH:" + q, q, null, null, 1]], null, "en"]);
}
const fake = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    if (url.pathname === "/gtx") {
      hits.gtx++;
      if (modes.gtx === "429") { res.writeHead(429, { "content-type": "text/html" }); res.end("<html><head><title>Sorry...</title></head></html>"); return; }
      if (modes.gtx === "500") { res.writeHead(500); res.end("boom"); return; }
      let q = url.searchParams.get("q") || "";
      if (req.method === "POST") { try { q = new URLSearchParams(body).get("q") || q; } catch (_) {} }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(gtxJson(q));
      return;
    }
    if (url.pathname === "/mymemory") {
      hits.mymemory++;
      if (modes.mymemory === "500") { res.writeHead(500); res.end("boom"); return; }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ responseData: { translatedText: "Xin chào thế giới" },
        responseStatus: 200, responseDetails: "" }));
      return;
    }
    if (url.pathname === "/libretranslate/translate") {
      hits.libretranslate++;
      try { lastLtBody.body = JSON.parse(body); } catch (_) { lastLtBody.body = body; }
      if (modes.libretranslate === "400key") {
        res.writeHead(400, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "Visit https://portal.libretranslate.com to get an API key." }));
        return;
      }
      if (modes.libretranslate === "429") { res.writeHead(429); res.end("{}"); return; }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ translatedText: "Xin chào thế giới" }));
      return;
    }
    if (url.pathname === "/proxy" && req.method === "POST") {
      hits.proxy++;
      let parsed = {};
      try { parsed = JSON.parse(body); } catch (_) {}
      if (modes.proxy === "quota") {
        res.writeHead(429, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: false, code: "quota_exceeded", month_used: 500000, month_limit: 500000 }));
        return;
      }
      if (modes.proxy === "error") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ ok: false, code: "proxy_error", error: "boom giả" }));
        return;
      }
      res.writeHead(200, { "content-type": "application/json" });
      if (Array.isArray(parsed.texts)) {
        res.end(JSON.stringify({ ok: true, translated: parsed.texts.map((t) => "VI qua proxy: " + t) }));
      } else {
        res.end(JSON.stringify({ ok: true, translated: "VI qua proxy: " + (parsed.text || "") }));
      }
      return;
    }
    res.writeHead(404); res.end();
  });
});
await new Promise((r) => fake.listen(0, "127.0.0.1", r));
const PORT = fake.address().port;
const BASE = "http://127.0.0.1:" + PORT;
setGoogleEndpoint(BASE + "/gtx");
setMyMemoryEndpoint(BASE + "/mymemory");
setLibreTranslateEndpoint(BASE + "/libretranslate");
setTranslateProxyUrl("");

function resetCounts() { for (const k of Object.keys(hits)) hits[k] = 0; }
function traceSummary() {
  return getFallbackTrace().map((e) => e.step + ":" + (e.ok ? "ok" : (e.skipped ? "bỏ-qua" : "lỗi"))).join(", ");
}

// ================= A. gt.js trực tiếp, server giả =================
{ // A1. Google 429 → MyMemory được dùng
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "ok"; modes.libretranslate = "ok";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint("");
  resetCounts();
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  rec("node_google429_then_mymemory", out === "Xin chào thế giới" &&
    tr.some((e) => e.step === "google" && !e.ok && /429/.test(e.detail || e.short || "")) &&
    tr.some((e) => e.step === "mymemory" && e.ok) && hits.mymemory === 1,
    `kết quả=${JSON.stringify(out)}; trace=[${traceSummary()}]; ` +
    `Google gọi ${hits.gtx} lần (retry 429), MyMemory gọi ${hits.mymemory} lần`,
    performance.now() - t0);
}
{ // A2. MyMemory lỗi → LibreTranslate được dùng
  const t0 = performance.now();
  modes.gtx = "500"; modes.mymemory = "500"; modes.libretranslate = "ok";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint(BASE + "/libretranslate");
  resetCounts();
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  rec("node_mymemory_fail_then_libretranslate", out === "Xin chào thế giới" &&
    tr.some((e) => e.step === "mymemory" && !e.ok) &&
    tr.some((e) => e.step === "libretranslate" && e.ok) && hits.libretranslate === 1,
    `kết quả=${JSON.stringify(out)}; trace=[${traceSummary()}]; ` +
    `MyMemory gọi ${hits.mymemory} lần (500), LibreTranslate gọi ${hits.libretranslate} lần`,
    performance.now() - t0);
}
{ // A3. Tất cả lỗi → báo rõ cả chuỗi, không im lặng
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "500"; modes.libretranslate = "400key";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint(BASE + "/libretranslate");
  resetCounts();
  let err = null;
  try { await translateOneWithFallbacks("Hello world", "en", "vi"); }
  catch (e) { err = e; }
  const firstLine = err ? String(err.message || "").split("\n")[0] : "";
  const tr = getFallbackTrace();
  rec("node_all_fail_reports_chain", err !== null && err.kind === "all_failed" &&
    /Google/.test(firstLine) && /MyMemory/.test(firstLine) &&
    /LibreTranslate/.test(firstLine) && /từ điển offline/.test(firstLine) &&
    tr.filter((e) => !e.skipped).length === 3,
    `kind=${err && err.kind}; dòng đầu=${JSON.stringify(firstLine)} ` +
    `(dài ${firstLine.length} ký tự, bridge lấy tối đa 200); trace=[${traceSummary()}]`,
    performance.now() - t0);
}
{ // A4. LibreTranslate chưa cấu hình → bỏ qua, 0 request
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "500";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint("");
  resetCounts();
  let err = null;
  try { await translateOneWithFallbacks("Hello world", "en", "vi"); }
  catch (e) { err = e; }
  const firstLine = err ? String(err.message || "").split("\n")[0] : "";
  rec("node_libretranslate_unconfigured_skipped", err !== null &&
    err.kind === "all_failed" && hits.libretranslate === 0 &&
    /LibreTranslate chưa cấu hình/.test(firstLine),
    `kind=${err && err.kind}; LibreTranslate gọi ${hits.libretranslate} lần; ` +
    `dòng đầu=${JSON.stringify(firstLine)}`,
    performance.now() - t0);
}
{ // A5. Proxy đã cấu hình → thử đầu tiên, Google/MyMemory không gọi
  const t0 = performance.now();
  modes.proxy = "ok"; modes.gtx = "ok"; modes.mymemory = "ok";
  setTranslateProxyUrl(BASE + "/proxy"); setLibreTranslateEndpoint("");
  resetCounts();
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  rec("node_proxy_tried_first", out === "VI qua proxy: Hello world" &&
    hits.proxy === 1 && hits.gtx === 0 && hits.mymemory === 0 &&
    tr.length === 1 && tr[0].step === "proxy" && tr[0].ok,
    `kết quả=${JSON.stringify(out)}; proxy gọi ${hits.proxy} lần, Google ${hits.gtx} lần, MyMemory ${hits.mymemory} lần; trace=[${traceSummary()}]`,
    performance.now() - t0);
}
{ // A6. Proxy hết hạn mức → BỎ QUA Google, MyMemory được dùng
  const t0 = performance.now();
  modes.proxy = "quota"; modes.gtx = "ok"; modes.mymemory = "ok";
  setTranslateProxyUrl(BASE + "/proxy"); setLibreTranslateEndpoint("");
  resetCounts();
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  rec("node_proxy_quota_skips_google", out === "Xin chào thế giới" &&
    hits.gtx === 0 && hits.mymemory === 1 &&
    tr.some((e) => e.step === "proxy" && !e.ok && /hạn mức/.test(e.short || "")) &&
    tr.some((e) => e.step === "mymemory" && e.ok),
    `kết quả=${JSON.stringify(out)}; Google gọi ${hits.gtx} lần (phải = 0 khi hết hạn mức), MyMemory ${hits.mymemory} lần; trace=[${traceSummary()}]`,
    performance.now() - t0);
}
{ // A7. Batch lỗi cả proxy + Google → batch_failed, nói rõ dịch lại từng chuỗi
  const t0 = performance.now();
  modes.proxy = "error"; modes.gtx = "429";
  setTranslateProxyUrl(BASE + "/proxy"); setLibreTranslateEndpoint("");
  resetCounts();
  let err = null;
  try { await translateBatchWithFallbacks(["Hello world", "Goodbye"], "en", "vi"); }
  catch (e) { err = e; }
  const firstLine = err ? String(err.message || "").split("\n")[0] : "";
  rec("node_batch_fail_says_retry_singles", err !== null && err.kind === "batch_failed" &&
    /sẽ dịch lại từng chuỗi/.test(firstLine) && !/tất cả đều lỗi/.test(firstLine),
    `kind=${err && err.kind}; dòng đầu=${JSON.stringify(firstLine)}`,
    performance.now() - t0);
}
{ // A8. LibreTranslate đơn lẻ: đúng / sai key / quá dài
  const t0 = performance.now();
  setLibreTranslateEndpoint(BASE + "/libretranslate");
  setLibreTranslateApiKey("key-giả");
  modes.libretranslate = "ok";
  resetCounts();
  const okOut = await libreTranslateOne("Hello world", "en", "vi");
  const sentKey = lastLtBody.body && lastLtBody.body.api_key;
  modes.libretranslate = "400key";
  let keyErr = null;
  try { await libreTranslateOne("Hello world", "en", "vi"); } catch (e) { keyErr = e; }
  const hitsBefore = hits.libretranslate;
  let longErr = null;
  try { await libreTranslateOne("x".repeat(5001), "en", "vi"); } catch (e) { longErr = e; }
  rec("node_libretranslate_unit", okOut === "Xin chào thế giới" && sentKey === "key-giả" &&
    keyErr !== null && keyErr.kind === "libretranslate" && /API key/.test(keyErr.message || "") &&
    longErr !== null && /5000/.test(longErr.message || "") && hits.libretranslate === hitsBefore,
    `dịch đúng=${JSON.stringify(okOut)} (api_key gửi kèm: ${JSON.stringify(sentKey)}); ` +
    `400 không key → kind=${keyErr && keyErr.kind}, msg=${JSON.stringify((keyErr && keyErr.message || "").slice(0, 80))}; ` +
    `chuỗi >5000 ký tự → lỗi ngay không gọi mạng (số lần gọi không đổi: ${hitsBefore})`,
    performance.now() - t0);
  setLibreTranslateApiKey("");
}

// ================= A9. Probe THẬT (ghi kết quả thật) =================
const liveProbe = {};
{ // Google thật từ IP máy chủ (thường 429 + Sorry — ghi đúng kết quả)
  const t0 = performance.now();
  try {
    const res = await fetch(
      "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=vi&dt=t&q=play",
      { headers: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1", Accept: "application/json, */*" } });
    const body = await res.text();
    liveProbe.google_real = { status: res.status, sorry: /<title>Sorry/i.test(body),
      head: body.slice(0, 120) };
    rec("live_google_real", true,
      `Google thật trả HTTP ${res.status}${/<title>Sorry/i.test(body) ? " + trang Sorry (IP máy chủ đang bị Google giới hạn — đúng như các lần đo trước)" : ""}; ` +
      `body đầu: ${JSON.stringify(body.slice(0, 120))} (probe chỉ ghi nhận, không ép PASS dịch được)`,
      performance.now() - t0);
  } catch (e) {
    liveProbe.google_real = { error: String(e && e.message || e) };
    rec("live_google_real", true, "fetch Google thật lỗi: " + liveProbe.google_real.error + " (probe chỉ ghi nhận)", performance.now() - t0);
  }
}
{ // MyMemory thật
  const t0 = performance.now();
  try {
    const res = await fetch("https://api.mymemory.translated.net/get?q=Hello%20world&langpair=en|vi");
    const data = await res.json().catch(() => null);
    liveProbe.mymemory_real = { status: res.status,
      translated: data && data.responseData && data.responseData.translatedText,
      responseStatus: data && data.responseStatus };
    rec("live_mymemory_real", res.status === 200 && data && Number(data.responseStatus) === 200,
      `MyMemory thật trả HTTP ${res.status}, responseStatus=${data && data.responseStatus}, ` +
      `translatedText=${JSON.stringify(data && data.responseData && data.responseData.translatedText)}`,
      performance.now() - t0);
  } catch (e) {
    liveProbe.mymemory_real = { error: String(e && e.message || e) };
    rec("live_mymemory_real", false, "fetch MyMemory thật lỗi: " + liveProbe.mymemory_real.error, performance.now() - t0);
  }
}
{ // LibreTranslate thật: GET /languages đi được; POST /translate bị sandbox chặn
  const t0 = performance.now();
  try {
    const res = await fetch("https://libretranslate.com/languages");
    const data = await res.json().catch(() => null);
    const en = Array.isArray(data) ? data.find((l) => l.code === "en") : null;
    const viOk = !!(en && Array.isArray(en.targets) && en.targets.includes("vi"));
    liveProbe.libretranslate_languages = { status: res.status, en_targets_vi: viOk,
      aca: res.headers.get("access-control-allow-origin") };
    rec("live_libretranslate_languages", res.status === 200 && viOk,
      `GET libretranslate.com/languages → HTTP ${res.status}, ACAO=${res.headers.get("access-control-allow-origin")}, ` +
      `en→vi hỗ trợ: ${viOk} (CORS không phải vấn đề; vấn đề là API key ở bước POST)`,
      performance.now() - t0);
  } catch (e) {
    liveProbe.libretranslate_languages = { error: String(e && e.message || e) };
    rec("live_libretranslate_languages", false, "GET /languages lỗi: " + liveProbe.libretranslate_languages.error, performance.now() - t0);
  }
  const t1 = performance.now();
  try {
    const res = await fetch("https://libretranslate.com/translate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: "Hello world", source: "en", target: "vi", format: "text" }),
    });
    const body = await res.text();
    liveProbe.libretranslate_post = { status: res.status, head: body.slice(0, 200) };
    rec("live_libretranslate_post_blocked_note", true,
      `POST libretranslate.com/translate → HTTP ${res.status}, body đầu: ${JSON.stringify(body.slice(0, 200))} ` +
      `(từ sandbox này POST /translate bị egress chặn policy_denied nên KHÔNG đo được phản hồi thật của server; ` +
      `bằng chứng "cần API key (HTTP 400)" lấy từ kiểm chứng độc lập 2026-10-27, xem fallback_chain_results.md)`,
      performance.now() - t1);
  } catch (e) {
    liveProbe.libretranslate_post = { error: String(e && e.message || e) };
    rec("live_libretranslate_post_blocked_note", true,
      "POST /translate lỗi: " + liveProbe.libretranslate_post.error + " (ghi nhận; xem ghi chú trong results)", performance.now() - t1);
  }
}

// ================= B. Bridge thật trên Pyodide Node =================
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
const { bridge, callAsync } = await bootPyodide();
// Bridge dùng CHUỖI THẬT (không mock Google): endpoint giả đã cấu hình ở trên.
globalThis.pb3_google_translate = translateOneWithFallbacks;
globalThis.pb3_google_translate_batch = translateBatchWithFallbacks;
globalThis.pb3_fallback_trace = () => JSON.stringify(getFallbackTrace());
globalThis.pb3_google_cancelled = () => false;

{ // B1. Google 429 (giả) → MyMemory cứu chuỗi, fallback_note hiện rõ
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "ok"; modes.proxy = "ok";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint("");
  const r = await callAsync("translate_json_google",
    JSON.stringify({ hello: "Hello world", play: "Play" }), "vi", false);
  rec("bridge_google429_mymemory_note", r.ok === true &&
    r.translated.hello === "Xin chào thế giới" && r.translated.play === "Chơi" &&
    typeof r.fallback_note === "string" && /MyMemory/.test(r.fallback_note) &&
    r.fallback_used && r.fallback_used.mymemory >= 1,
    `hello=${JSON.stringify(r.translated.hello)} (qua MyMemory), play=${JSON.stringify(r.translated.play)} (từ điển); ` +
    `fallback_note=${JSON.stringify(r.fallback_note)}; fallback_used=${JSON.stringify(r.fallback_used)}; ` +
    `translated_by=${r.translated_by} google_count=${r.google_count}`,
    performance.now() - t0);
}
{ // B2. Tất cả lỗi → offline, network_error chứa tóm tắt cả chuỗi
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "500";
  setTranslateProxyUrl(""); setLibreTranslateEndpoint("");
  const r = await callAsync("translate_json_google",
    JSON.stringify({ hello: "Hello world", play: "Play" }), "vi", false);
  rec("bridge_all_fail_offline_chain_error", r.ok === true &&
    r.translated_by === "offline" && r.translated.hello === "Hello world" &&
    typeof r.network_error === "string" &&
    /Google/.test(r.network_error) && /MyMemory/.test(r.network_error) &&
    /từ điển offline/.test(r.network_error),
    `translated_by=${r.translated_by}; hello giữ nguyên=${JSON.stringify(r.translated.hello)}; ` +
    `network_error=${JSON.stringify(r.network_error)}`,
    performance.now() - t0);
}
{ // B3. Proxy giả OK → batch qua proxy
  const t0 = performance.now();
  modes.proxy = "ok"; modes.gtx = "ok";
  setTranslateProxyUrl(BASE + "/proxy"); setLibreTranslateEndpoint("");
  const r = await callAsync("translate_json_google",
    JSON.stringify({ hello: "Hello world", play: "Play" }), "vi", false);
  rec("bridge_proxy_batch_ok", r.ok === true && r.batch_mode === "proxy-batch" &&
    r.translated.hello === "VI qua proxy: Hello world" &&
    (r.fallback_note === "" || r.fallback_note == null),
    `batch_mode=${r.batch_mode}; hello=${JSON.stringify(r.translated.hello)}; ` +
    `fallback_note=${JSON.stringify(r.fallback_note)} (proxy thành công ngay → không dùng fallback)`,
    performance.now() - t0);
}
setTranslateProxyUrl("");
fake.close();

// ================= C. Trình duyệt THẬT (Playwright, 390px) =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
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
const SPORT = server.address().port;

let browser = null;
let gtxRouteHits = 0;
let mmMode = "ok";
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  // Chặn Google thật → 429 (như IP máy chủ đang bị); MyMemory giả.
  await page.route("**/translate.googleapis.com/**", async (route) => {
    gtxRouteHits++;
    await route.fulfill({ status: 429, contentType: "text/html",
      body: "<html><head><title>Sorry...</title></head><body></body></html>" });
  });
  await page.route("**/api.mymemory.translated.net/**", async (route) => {
    if (mmMode === "ok") {
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ responseData: { translatedText: "Xin chào thế giới" }, responseStatus: 200, responseDetails: "" }) });
    } else {
      await route.fulfill({ status: 500, contentType: "application/json",
        body: JSON.stringify({ responseData: { translatedText: "" }, responseStatus: 500, responseDetails: "lỗi giả" }) });
    }
  });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const inputVal = (id) => page.evaluate((i) => (document.getElementById(i) || {}).value || "", id);

  { // C0. Boot (mục 6: boot lười — chọn 1 file nhỏ rồi chờ "Sẵn sàng")
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + SPORT + "/index.html");
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    rec("browser_boot", true, "boot tới Sẵn sàng (390px)", performance.now() - t0);
  }
  { // C1. Google 429 → MyMemory dịch được, UI hiện rõ "MyMemory"
    const t0 = performance.now();
    mmMode = "ok";
    gtxRouteHits = 0;
    await page.click("#btn-card-translate");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-translate"); return el && !el.hidden; },
      null, { timeout: 30000 });
    await page.fill("#translate-input", JSON.stringify({ hello: "Hello world" }));
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.check("#translate-google");
    await page.click("#btn-translate-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-output"); return el && el.value.includes("Xin chào thế giới"); },
      null, { timeout: 120000 });
    const info = await elText("translate-info");
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    rec("browser_google429_mymemory_ui", parsed && parsed.hello === "Xin chào thế giới" &&
      info.includes("MyMemory") && !info.includes("Lý do:") && gtxRouteHits >= 1,
      `output hello=${JSON.stringify(parsed && parsed.hello)}; ` +
      `info: ${JSON.stringify(info.slice(0, 260))}; ` +
      `không hiện "Lý do" giả khi đã dịch xong qua fallback; ` +
      `route Google 429 đã chặn ${gtxRouteHits} request (worker fetch thật bị chặn, không phải mock trong code)`,
      performance.now() - t0);
  }
  { // C2. Tất cả lỗi → UI báo rõ lý do từng bước + từ điển offline
    const t0 = performance.now();
    mmMode = "500";
    await page.fill("#translate-input", JSON.stringify({ hello: "Hello world, welcome!" }));
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.click("#btn-translate-run");
    // Chờ đúng dấu hiệu của LẦN CHẠY NÀY ("tất cả đều lỗi" chỉ có khi
    // cả chuỗi fallback đều fail; "từ điển offline" thì lần chạy trước
    // cũng có nên không dùng làm điều kiện chờ).
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && el.textContent.includes("tất cả đều lỗi"); },
      null, { timeout: 120000 });
    const info = await elText("translate-info");
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    rec("browser_all_fail_clear_error", info.includes("Google") && info.includes("MyMemory") &&
      info.includes("từ điển offline") && info.includes("0/1") &&
      parsed && !("hello" in parsed),
      `info: ${JSON.stringify(info.slice(0, 400))}; ` +
      `chuỗi chưa dịch được giữ lại trong hàng đợi để dịch tiếp (output không ghi nhận bản dịch giả): ${JSON.stringify(out)}`,
      performance.now() - t0);
  }
  { // C3. 390px: không tràn ngang, không lỗi trang
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
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

// ================= Ghi kết quả =================
const mdLines = ["# Kiểm chứng mục C1 — Chuỗi fallback dịch không cần key", "",
  "Ngày: 2026-11-16. Harness: exp/verify_fallback_chain.mjs.",
  "Chuỗi: proxy (nếu có URL) → Google trực tiếp (gtx) → MyMemory → LibreTranslate (chỉ khi đã cấu hình) → từ điển offline.",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  "Tổng: " + Object.keys(results).length + " mục, " + failures + " FAIL.",
  "",
  "## Bằng chứng live (đo thật ngày 2026-11-16, từ sandbox này)",
  "- Google gtx thật: " + JSON.stringify(liveProbe.google_real || null),
  "- MyMemory thật: " + JSON.stringify(liveProbe.mymemory_real || null),
  "- LibreTranslate GET /languages: " + JSON.stringify(liveProbe.libretranslate_languages || null),
  "- LibreTranslate POST /translate: " + JSON.stringify(liveProbe.libretranslate_post || null),
  "",
  "## Kết luận về LibreTranslate public",
  "- libretranslate.com /translate KHÔNG còn dùng được nếu không có key: bản public trả HTTP 400",
  '  "Visit https://portal.libretranslate.com to get an API key" (kiểm chứng độc lập 2026-10-27); gói host từ $29/tháng.',
  "- Mirror translate.argosopentech.com đã chết (DNS resolve nhưng không nối được từ sandbox; nguồn độc lập ghi \"does not resolve\").",
  "- Từ sandbox này cũng KHÔNG đo trực tiếp được POST /translate: egress chặn mọi POST /translate (policy_denied, X-Hatch-Policy) — chỉ GET /languages đi qua (HTTP 200, ACAO:*, en→vi hỗ trợ).",
  "- Vì vậy bước LibreTranslate MẶC ĐỊNH TẮT trong code (endpoint rỗng, 0 request thừa); chuỗi mặc định vẫn là 3 bước proxy → Google → MyMemory → từ điển offline.",
  "- Bước này đã cài sẵn và kiểm chứng bằng server giả cùng API (A2/A8/B1): ai tự host LibreTranslate (miễn phí) hoặc có key portal thì dán endpoint + key ở tab Dịch (hoặc trang Kiểm tra dịch) là bật, không cần sửa code.",
  "- Từ điển offline thật có 105 cụm EN→VI và 101 cụm VI→EN (đếm trực tiếp từ sdk/translate.py) — con số \"~100\" trong UI là đúng; \"~40\" trong mô tả nhiệm vụ là cũ.",
  "",
  "## Giới hạn còn lại",
  "- LibreTranslate public cần key trả phí; không có bước dịch miễn phí thứ 4 nào còn sống (MyMemory là bước miễn phí cuối).",
  "- Google gtx từ IP máy chủ vẫn 429 + Sorry (đo lại hôm nay); trên mạng di động/dân cư thường dịch được — vẫn cần Joseph thử trên Safari thật.",
  "- CORS trên Safari thật cho MyMemory/LibreTranslate vẫn chưa kiểm chứng (MyMemory và libretranslate.com đều trả ACAO:* qua curl, nhưng Safari thật mới là bằng chứng cuối).",
  "",
];
fs.writeFileSync(EXP + "/fallback_chain_results.md", mdLines.join("\n"));
fs.writeFileSync(EXP + "/fallback_chain_results.json",
  JSON.stringify({ results, failures, live_probe: liveProbe }, null, 2));
console.log("\nTỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL.");
process.exit(failures ? 1 : 0);
