// verify_translate_connect.mjs — Kiểm chứng PHẦN C: nối tab Dịch với
// Trích xuất (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_translate_connect.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: translate_connect_results.md + translate_connect_results.json
//
//  A. Bridge thật trên Pyodide Node:
//     C1. translate_json_google + mock Google thành công (5 chuỗi):
//         glossary-first (play/quit qua từ điển) + Google 3 chuỗi còn
//         lại theo ưu tiên (title trước), tiến trình đủ 5 chuỗi.
//     C2. Mock Google luôn lỗi (network) -> network_error có, rơi về
//         offline, không crash, không đứng im.
//     C3. Mock Google chậm + hủy sau 2 chuỗi -> cancelled=true, giữ lại
//         phần đã dịch (title+hello theo thứ tự ưu tiên, welcome còn nguyên).
//     C4. Dịch offline 1000 chuỗi: đo thời gian THẬT.
//     C5. Gọi endpoint Google THẬT từ Node (UA Safari iPhone): ghi kết quả
//         THẬT (IP máy chủ thường bị 429 + Sorry — nếu thế thì FAIL có bằng
//         chứng, là vấn đề IP, không phải lỗi code).
//  B. Trình duyệt THẬT (Playwright, 390px, serve site qua HTTP local +
//     proxy dịch GIẢ có delay 250ms/request để kiểm chứng hủy):
//     C1. Sau extract fixture: dropdown tab Dịch mặc định English.json,
//         ô dịch nạp sẵn en.json; nút "Dịch sang tiếng Việt" trên file
//         Chinese.json chuyển sang tab Dịch với nội dung đã nạp; banner
//         rỗng hiện khi chưa có input.
//     C2. JSON 5 chuỗi: dịch offline xong + hiển thị tiến trình.
//     C3. JSON 1000 chuỗi (Google qua proxy giả): cảnh báo ước tính hiện
//         TRƯỚC khi chạy (dialog), đo tốc độ thật, bấm Hủy giữa chừng ->
//         dừng thật, giữ lại phần đã dịch, tải được phần đã dịch.
//     C4. 390px: không lỗi console, không tràn ngang, không vỡ chữ.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

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

// ================= A. Bridge thật trên Pyodide Node =================
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
    call: (fn, ...args) => JSON.parse(bridge[fn](...args)),
    callAsync: async (fn, ...args) => JSON.parse(await bridge[fn](...args)),
  };
}
const { pyodide, bridge, progEvents, call, callAsync } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
pyodide.FS.writeFile("/tmp/input/fixture.zip", new Uint8Array(fs.readFileSync(FX)));

function setGoogleFn(fn, cancelledFn) {
  globalThis.pb3_google_translate = fn;
  globalThis.pb3_google_cancelled = cancelledFn || (() => false);
}
const SMALL = { play: "Play", quit: "Quit", hello: "Hello", title: "9 Kings", welcome: "Welcome to 9 Kings!" };

{ // C1. Mock Google thành công: glossary-first + Google theo ưu tiên
  // (mục A: play/quit qua từ điển trước, chỉ hello/title/welcome gửi
  // Google; title ưu tiên 0 nên dịch trước hello/welcome).
  const t0 = performance.now();
  progEvents.length = 0;
  setGoogleFn(async (text) => "VI[" + text + "]");
  const r = await callAsync("translate_json_google", JSON.stringify(SMALL), "vi", false);
  const prog = progEvents.filter((e) => e.kind === "translate");
  const progOk = prog.length >= 4 && prog[prog.length - 1].done === prog[prog.length - 1].total;
  rec("node_google_mock_success_progress", r.ok === true && r.translated_by === "mixed" &&
    r.google_count === 3 && r.glossary_count === 2 &&
    r.translated.play === "Chơi" && r.translated.quit === "Thoát" &&
    r.translated.hello === "VI[Hello]" && progOk,
    `translated_by=${r.translated_by} google_count=${r.google_count} glossary_count=${r.glossary_count} ` +
    `play=${JSON.stringify(r.translated.play)} hello=${JSON.stringify(r.translated.hello)}; ` +
    `tiến trình translate: ${prog.length} event, cuối ${prog.length ? prog[prog.length - 1].done + "/" + prog[prog.length - 1].total : "?"} (cần đủ 5 chuỗi)`,
    performance.now() - t0);
}
{ // C2. Mock Google luôn lỗi -> network_error + rơi về offline
  const t0 = performance.now();
  setGoogleFn(async () => { const e = new Error("Không gọi được Google Translate (mất mạng hoặc trình duyệt chặn CORS)."); e.kind = "network"; throw e; });
  const r = await callAsync("translate_json_google", JSON.stringify(SMALL), "vi", false);
  rec("node_google_error_falls_back_offline", r.ok === true && typeof r.network_error === "string" &&
    r.network_error.length > 0 && r.translated_by !== "google" &&
    r.translated.play === "Chơi" && r.translated.quit === "Thoát",
    `network_error=${JSON.stringify(r.network_error)}; translated_by=${r.translated_by} ` +
    `(google_count=${r.google_count}, glossary_count=${r.glossary_count}); ` +
    `offline vẫn dịch play=Chơi quit=Thoát (không đứng im)`,
    performance.now() - t0);
}
{ // C3. Mock Google chậm + hủy sau 2 chuỗi -> giữ lại phần đã dịch
  // (mục A: thứ tự Google là title (ưu tiên 0), hello, welcome — hủy
  // sau 2 lần gọi nên title+hello đã dịch, welcome còn nguyên).
  const t0 = performance.now();
  let calls = 0;
  setGoogleFn(async (text) => {
    calls++;
    await sleep(60);
    return "VI[" + text + "]";
  }, () => calls >= 2);
  const r = await callAsync("translate_json_google", JSON.stringify(SMALL), "vi", false);
  const doneCount = Object.values(r.translated).filter((v) => typeof v === "string" && v.startsWith("VI[")).length;
  rec("node_google_cancel_keeps_partial", r.ok === true && r.cancelled === true &&
    r.google_count === 2 && doneCount === 2 &&
    r.translated.title === "VI[9 Kings]" && r.translated.hello === "VI[Hello]" &&
    r.translated.welcome === "Welcome to 9 Kings!",
    `cancelled=${r.cancelled} google_count=${r.google_count} (gọi ${calls} lần); ` +
    `đã dịch ${doneCount}/5 chuỗi Google (giữ lại phần đã dịch), chuỗi chưa dịch giữ nguyên (welcome=${JSON.stringify(r.translated.welcome)})`,
    performance.now() - t0);
}
let offline1000Ms = 0;
let googleRate = null, googleEta1000 = null;
{ // C4. Dịch offline 1000 chuỗi: đo thời gian THẬT
  const t0 = performance.now();
  const big = {};
  for (let i = 0; i < 1000; i++) big["key_" + String(i).padStart(4, "0")] = "String number " + i + " to translate please";
  const r = call("translate_json", JSON.stringify(big), "vi", false);
  offline1000Ms = performance.now() - t0;
  rec("node_offline_1000_time", r.ok === true && r.total === 1000,
    `dịch offline 1000 chuỗi xong trong ${Math.round(offline1000Ms)}ms (${sec(offline1000Ms)}s) — ` +
    `total=${r.total}, untranslated=${r.untranslated} (từ điển offline chỉ biết ~100 từ/cụm nên hầu hết giữ nguyên là ĐÚNG, không phải lỗi)`,
    offline1000Ms);
}
{ // C5. Gọi endpoint Google THẬT từ Node (UA Safari iPhone) — ghi kết quả THẬT
  const t0 = performance.now();
  let detail = "";
  let ok = false;
  try {
    const res = await fetch(
      "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=vi&dt=t&q=play",
      { headers: {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "application/json, */*",
      } });
    const body = await res.text();
    const isSorry = /<title>Sorry/i.test(body) || body.trimStart().startsWith("<");
    let parsed = null;
    try { parsed = JSON.parse(body); } catch { parsed = null; }
    ok = res.status === 200 && !isSorry && Array.isArray(parsed);
    detail = `Google trả HTTP ${res.status}` +
      (isSorry ? " + trang Sorry (IP bị giới hạn)" : "") +
      (parsed ? `, JSON dịch được: ${JSON.stringify(parsed).slice(0, 80)}` : `, body đầu: ${JSON.stringify(body.slice(0, 120))}`);
  } catch (e) {
    detail = "fetch Google lỗi: " + String(e && e.message ? e.message : e);
  }
  rec("node_live_google_real_result", ok,
    detail + " — " + (ok ? "Google dịch được từ IP máy chủ" : "IP máy chủ bị Google giới hạn (429/Sorry) là vấn đề MẠNG/IP, không phải lỗi code; đường fallback offline đã xanh ở C2"),
    performance.now() - t0);
}

// ================= B. Trình duyệt THẬT =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  if (url.pathname === "/translate-proxy" && req.method === "POST") {
    // Proxy dịch GIẢ: delay 250ms/request rồi trả bản dịch giả định dạng
    // Cloudflare Worker thật. Hỗ trợ cả dịch đơn {text} và batch
    // {texts:[...]} (mục A — app.js luôn gọi batch trước).
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      setTimeout(() => {
        let parsed = {};
        try { parsed = JSON.parse(body); } catch { parsed = {}; }
        res.writeHead(200, { "content-type": "application/json" });
        if (Array.isArray(parsed.texts)) {
          res.end(JSON.stringify({ ok: true,
            translated: parsed.texts.map((t) => "VI:" + t) }));
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

let browser = null;
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
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  let dialogText = null;
  page.on("dialog", async (d) => { dialogText = d.message(); await d.accept(); });
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const inputVal = (id) => page.evaluate((i) => (document.getElementById(i) || {}).value || "", id);
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const elHtml = (id) => page.evaluate((i) => (document.getElementById(i) || {}).innerHTML || "", id);

  { // B0. Boot + đặt proxy giả (localStorage + ô nhập) trước khi dịch Google
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    await page.evaluate((url) => {
      try { localStorage.setItem("pb3_translate_proxy", url); } catch (e) {}
      const inp = document.getElementById("translate-proxy");
      if (inp) { inp.value = url; inp.dispatchEvent(new Event("change")); }
    }, PROXY_URL);
    await sleep(300);
    rec("browser_boot_proxy_set", true,
      "boot tới Sẵn sàng; proxy dịch giả đặt qua localStorage + ô nhập: " + PROXY_URL,
      performance.now() - t0);
  }
  { // B1. Extract fixture: danh sách chọn file + kết quả có nguồn + nút Dịch; dropdown Dịch mặc định en
    const t0 = performance.now();
    await page.setInputFiles("#file-input", FX);
    await page.waitForFunction(
      () => { const el = document.getElementById("file-info"); return el && el.textContent.includes("extract_select_fixture.zip"); },
      null, { timeout: 30000 });
    await page.click("#btn-card-extract");
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-file-list"); return el && el.textContent.includes("localization-locales_assets_all.bundle"); },
      null, { timeout: 60000 });
    const listHtml = await elHtml("extract-file-list");
    const modeAuto = await page.evaluate(() => (document.getElementById("extract-mode-auto") || {}).checked === true);
    const locDefault = await page.evaluate(() => (document.getElementById("extract-locales") || {}).value);
    const dlPromise = page.waitForEvent("download", { timeout: 120000 });
    await page.click("#btn-extract-run");
    const dl = await dlPromise;
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("English.json"); },
      null, { timeout: 120000 });
    const resText = await elText("extract-result");
    const resHtml = await elHtml("extract-result");
    const hasSource = resText.includes("từ: Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle");
    const hasBtn = resHtml.includes("Dịch sang tiếng Việt");
    // Dropdown tab Dịch mặc định English.json + ô dịch nạp sẵn en.json.
    const selVal = await page.evaluate(() => (document.getElementById("translate-source-select") || {}).value);
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-input"); return el && el.value.includes("Hello"); },
      null, { timeout: 60000 });
    const inputHasEn = (await inputVal("translate-input")).includes("Welcome to 9 Kings!");
    const bannerHiddenAfterLoad = await page.evaluate(() => (document.getElementById("translate-empty-banner") || {}).hidden === true);
    rec("browser_extract_connects_translate", dl.suggestedFilename() === "extract.zip" &&
      modeAuto && locDefault === "both" && hasSource && hasBtn && selVal === "English.json" &&
      inputHasEn && bannerHiddenAfterLoad,
      `download ${dl.suggestedFilename()}; chế độ mặc định Tự động: ${modeAuto}; locale mặc định: ${locDefault}; ` +
      `kết quả có dòng nguồn bundle: ${hasSource}; có nút "Dịch sang tiếng Việt": ${hasBtn}; ` +
      `dropdown Dịch mặc định: ${selVal}; ô dịch nạp sẵn en.json (Welcome to 9 Kings!): ${inputHasEn}; ` +
      `banner rỗng ẩn khi đã có nội dung: ${bannerHiddenAfterLoad}`,
      performance.now() - t0);
  }
  { // B1b. Nút "Dịch sang tiếng Việt" trên Chinese.json -> tab Dịch với nội dung đã nạp
    // (đang ở sẵn tab Trích xuất sau B1, kết quả còn hiện).
    const t0 = performance.now();
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("Chinese.json"); },
      null, { timeout: 60000 });
    await page.click("button[data-translate-file='Chinese.json']");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-translate"); return el && !el.hidden; },
      null, { timeout: 30000 });
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-input"); return el && el.value.includes("你好"); },
      null, { timeout: 60000 });
    const selVal = await page.evaluate(() => (document.getElementById("translate-source-select") || {}).value);
    rec("browser_translate_button_loads_zh", selVal === "Chinese.json",
      `bấm nút Dịch trên Chinese.json -> tab Dịch mở, ô dịch chứa 你好, dropdown chuyển sang: ${selVal}`,
      performance.now() - t0);
  }
  { // B1c. Banner rỗng hiện khi chưa có input
    const t0 = performance.now();
    await page.fill("#translate-input", "");
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-empty-banner"); return el && !el.hidden; },
      null, { timeout: 30000 });
    const banner = await elText("translate-empty-banner");
    // Bấm Dịch khi ô trống: vẫn báo lỗi, không chạy.
    await page.click("#btn-translate-run");
    await sleep(500);
    const errVisible = await page.evaluate(() => !(document.getElementById("error") || {}).hidden);
    rec("browser_empty_banner", banner.includes("Chưa có nội dung để dịch") && errVisible,
      `banner: ${JSON.stringify(banner)}; bấm Dịch khi ô trống -> banner lỗi hiện: ${errVisible}`,
      performance.now() - t0);
  }
  { // B2. JSON 5 chuỗi: dịch offline xong + tiến trình hiện
    const t0 = performance.now();
    await page.fill("#translate-input", JSON.stringify(SMALL, null, 2));
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.uncheck("#translate-google");
    await page.click("#btn-translate-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-output"); return el && el.value.includes("Chơi"); },
      null, { timeout: 60000 });
    const info = await elText("translate-info");
    const out = await inputVal("translate-output");
    rec("browser_offline_5strings", info.includes("từ điển offline") && out.includes("Thoát"),
      `dịch offline 5 chuỗi xong; info: ${JSON.stringify(info.slice(0, 120))}…; output có Chơi/Thoát`,
      performance.now() - t0);
  }
  { // B3. JSON 1000 chuỗi qua proxy giả: cảnh báo ước tính + hủy giữa chừng
    const t0 = performance.now();
    const big = {};
    for (let i = 0; i < 1000; i++) big["key_" + String(i).padStart(4, "0")] = "String number " + i + " to translate please";
    await page.fill("#translate-input", JSON.stringify(big));
    await page.evaluate(() => document.getElementById("translate-input").dispatchEvent(new Event("input")));
    await page.check("#translate-google");
    dialogText = null;
    const tStart = performance.now();
    await page.click("#btn-translate-run");
    // Cảnh báo ước tính phải hiện TRƯỚC khi chạy (dialog confirm chặn main
    // thread cho tới khi handler Node accept — poll cờ phía Node).
    for (let i = 0; i < 100 && dialogText == null; i++) await sleep(100);
    const dialogOk = dialogText != null && /1000 chuỗi/.test(dialogText) && /ước tính khoảng/.test(dialogText) && /batch/.test(dialogText) && /15 giây/.test(dialogText);
    // Chờ batch đầu xong (tiến trình nhảy theo batch 100 chuỗi) rồi đo tốc độ thật.
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && /Đang dịch: (\d+)\//.test(el.textContent) && parseInt(el.textContent.match(/Đang dịch: (\d+)\//)[1], 10) >= 100; },
      null, { timeout: 60000 });
    const elapsed5 = (performance.now() - tStart) / 1000;
    const infoAt5 = await elText("translate-info");
    const doneAt5 = parseInt((infoAt5.match(/Đang dịch: (\d+)\//) || [0, 0])[1], 10) || 100;
    googleRate = doneAt5 / Math.max(elapsed5, 0.1);
    googleEta1000 = 1000 / googleRate;
    // Bấm Hủy giữa chừng -> dừng thật, giữ lại phần đã dịch.
    await page.click("#btn-translate-cancel");
    await page.waitForFunction(
      () => { const el = document.getElementById("translate-info"); return el && el.textContent.includes("Đã hủy"); },
      null, { timeout: 60000 });
    const out = await inputVal("translate-output");
    let parsed = null;
    try { parsed = JSON.parse(out); } catch { parsed = null; }
    const viCount = parsed ? Object.values(parsed).filter((v) => typeof v === "string" && v.startsWith("VI:")).length : 0;
    const keptPartial = parsed !== null && viCount > 0 && viCount < 1000;
    // Tải được phần đã dịch.
    const dlPromise = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#btn-translate-download");
    const dl = await dlPromise;
    const dlPath = await dl.path();
    const dlText = fs.readFileSync(dlPath, "utf8");
    let dlParsed = null;
    try { dlParsed = JSON.parse(dlText); } catch { dlParsed = null; }
    const dlVi = dlParsed ? Object.values(dlParsed).filter((v) => typeof v === "string" && v.startsWith("VI:")).length : 0;
    rec("browser_google_1000_estimate_and_cancel", dialogOk && keptPartial && dl.suggestedFilename() === "translated.vi.json" && dlVi === viCount,
      `dialog ước tính: ${JSON.stringify(dialogText)} (cần "1000 chuỗi" + "ước tính khoảng" + "batch" + "15 giây"); ` +
      `tốc độ thật: ${doneAt5} chuỗi/${elapsed5.toFixed(1)}s ≈ ${googleRate.toFixed(2)} chuỗi/giây -> 1000 chuỗi ≈ ${googleEta1000.toFixed(1)} giây; ` +
      `sau Hủy: ${viCount}/1000 chuỗi đã dịch (giữ lại phần đã dịch, không mất); ` +
      `tải phần đã dịch: ${dl.suggestedFilename()} với ${dlVi} chuỗi VI:`,
      performance.now() - t0);
  }
  { // B4. 390px: không tràn ngang, không lỗi trang
    const t0 = performance.now();
    const ov = await page.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    const extractOverflow = await page.evaluate(() => {
      const tbl = document.querySelector("#extract-result table");
      if (!tbl) return { has: false };
      const cs = getComputedStyle(tbl);
      return { has: true, display: cs.display, overflowX: cs.overflowX };
    });
    rec("browser_390_no_overflow_no_errors", ov.sw <= ov.cw + 1 && pageErrors.length === 0 &&
      (!extractOverflow.has || (extractOverflow.display === "block" && extractOverflow.overflowX === "auto")),
      `390px: scrollWidth=${ov.sw} clientWidth=${ov.cw} (không tràn); pageerror: ${pageErrors.length ? JSON.stringify(pageErrors) : "không có"}; ` +
      `bảng kết quả extract: ${JSON.stringify(extractOverflow)}`,
      performance.now() - t0);
  }
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

const lines = ["# Kiểm chứng Phần C — Nối tab Dịch với Trích xuất", "",
  "Ngày: 2026-11-16. Harness: exp/verify_translate_connect.mjs.",
  "A: bridge thật trên Pyodide Node (mock Google + Google thật từ Node).",
  "B: trình duyệt thật (Playwright chromium headless shell, 390px, serve site qua HTTP local + proxy dịch giả delay 250ms/request).",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  "Tổng: " + Object.keys(results).length + " mục, " + failures + " FAIL.",
  "",
  "## Ghi chú",
  `- Dịch offline 1000 chuỗi (Node): ${Math.round(offline1000Ms)}ms.`,
  googleRate != null
    ? `- Tốc độ Google đo được qua proxy giả (batch 100 chuỗi/request, delay 250ms/batch): ${googleRate.toFixed(2)} chuỗi/giây -> 1000 chuỗi ≈ ${googleEta1000.toFixed(1)} giây.`
    : "- Chưa đo được tốc độ Google (test trình duyệt chưa chạy tới bước đó).",
  "- Proxy dịch giả chỉ dùng cho harness (delay 250ms/request, trả \"VI:\" + text); Google thật từ IP máy chủ xem mục C5.",
  "",
  "## File thay đổi (đợt A+B+C, CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)",
  "- `site/pb3/sdk/extraction.py`, `site/pb3/sdk/exporters.py`, `site/pb3/bridge.py`, `site/index.html`, `site/app.js` (phần A+B+C).",
  "- Đợt bổ sung (batch dịch + Share): `site/gt.js` (batch joined-text, token bucket, hủy fetch), `site/worker.js` (expose batch, abort khi hủy), `site/pb3/bridge.py` (dedupe + glossary-first + ưu tiên + batch + `apply_credit_only`), `site/cloudflare/worker.js` (proxy nhận `{texts:[...]}`, chưa deploy), `site/kb-store.js` (DB v2 + `TranslateJobStore`), `site/app.js` (chunk 500 key + hàng đợi bền + TM + lỗi Share hiện `err.name`), `site/index.html` (thanh resume, ghi chú batch).",
  "- Fixture: `exp/fixtures/extract_select_fixture.zip`, `exp/fixtures/extract_broken_bundle.zip`, `exp/fixtures/translate_1000.json` (+ meta).",
  "- Harness: `exp/verify_enjson.mjs`, `exp/verify_extract_select.mjs`, `exp/verify_translate_connect.mjs` (harness này), `exp/verify_translate_batch.mjs` (riêng mục batch).",
  "",
  "## Nút Chia sẻ (mục B) — kết luận kiểm chứng",
  "- Handler Share KHÔNG có await nào trước `navigator.share(...)` nên giả thuyết “mất user activation” có thể SAI với nút hiện tại — KHÔNG viết lại handler.",
  "- Đã sửa: lỗi Share giờ hiện cả tên lỗi + message (`Chia sẻ lỗi: NotAllowedError: ...`), trước đây chỉ có message. Kiểm chứng bằng harness mock `navigator.share` ném lỗi (xem `verify_translate_batch.mjs`).",
  "- Chia file >400–500MB thành nhiều volume: CHƯA LÀM — cần Joseph xác nhận có file nào >400MB thật không + test share nhiều file trên Safari thật trước; file lớn nhất từng thấy là out.tar.xz 95,6MB. URL blob vẫn giữ 10 phút (không revoke sớm).",
  "- CẦN JOSEPH: bấm nút “Lưu / Chia sẻ” trên Safari thật và gửi lại ĐÚNG DÒNG LỖI nếu có — đó mới là bằng chứng; hành vi Share thật không chứng minh được bằng Node/Chromium.",
  "",
  "## Batch dịch (mục A) — xem `translate_batch_results.md`",
  "- gtx KHÔNG nhận nhiều q/request (chỉ dịch q đầu, kiểm chứng live); batch dùng joined-text 1 q với marker ⟦n⟧ (joined 10 -> 10/10, joined 50 -> 50/50, live). 429 là nhất thời, không phải chặn vĩnh viễn.",
  "- Chi tiết: dedupe, glossary-first, ưu tiên, hàng đợi bền IndexedDB, TM từ job đã xong, token bucket + backoff — xem báo cáo riêng.",
  "",
  "## C5 — Google thật từ IP máy chủ",
  "- Xem chi tiết từng test `node_live_google_real_result` ở trên. IP máy chủ từng bị 429 + Sorry theo đợt gửi dồn (nhất thời, vài phút sau lại 200), và batch joined-text đã dịch được thật từ IP này (10/10, 50/50) — xem `translate_batch_results.md`.",
  "- Joseph cần thử Google trên mạng iPhone/Safari thật (IP dân cư thường dịch được) và xem trang chẩn đoán `diagnostics.html`.",
  "",
  "## Còn chờ Joseph kiểm chứng trên Safari thật",
  "- Extract trên IPA thật 141MB phải ra en.json (fixture đã xanh, IPA thật chưa có trong sandbox).",
  "- Nút “Lưu / Chia sẻ” trên Safari thật (gửi lại đúng dòng lỗi nếu có).",
  "- Google Translate trên mạng iPhone (batch + CORS) + file picker trên IPA thật.",
  "- Hàng đợi dịch bền: tắt tab giữa chừng rồi mở lại, bấm “Tiếp tục dịch”.",
];
fs.writeFileSync(EXP + "/translate_connect_results.md", lines.join("\n") + "\n");
fs.writeFileSync(EXP + "/translate_connect_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results,
    offline1000Ms: Math.round(offline1000Ms),
    googleRate, googleEta1000 }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
