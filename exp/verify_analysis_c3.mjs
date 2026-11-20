// verify_analysis_c3.mjs — Kiểm chứng mục C3: PWA + persist() +
// nhắc export + Pyodide prefetch sau first paint (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_analysis_c3.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c3_results.md + analysis_c3_results.json
//
//  S. Tĩnh + Node vm:
//     S1. node --check app.js/sw.js; manifest hợp lệ (name, short_name,
//         start_url, display standalone, icons 192+512 có trên đĩa).
//     S2. sw.js: precache có fixtures/selftest.zip (nợ C2), CACHE_VERSION
//         v9, PREFETCH_CACHE + fetch dự phòng prefetch + GET_VERSION trả
//         prefetchCache.
//     S3. app.js tĩnh: beforeinstallprompt + appinstalled + standalone;
//         persist() + estimate() trong refreshKbPersistStatus; nút nhắc
//         export (Đã xuất / Tắt nhắc) + snooze; prefetch sau load/idle
//         (trì hoãn 4000ms, hook __PB3_NO_PREFETCH).
//     S4. Nạp app.js trong vm: không tạo Worker, không fetch Pyodide,
//         prefetch chưa chạy ngay lúc nạp.
//  B. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local):
//     B1. persist()+estimate() sớm: #kb-persist hiện ĐÃ CẤP/CHƯA CẤP thật
//         + "Đã dùng X / hạn mức Y" (số thật từ estimate()).
//     B2. PWA wiring: nút Cài ẩn lúc đầu, hiện khi beforeinstallprompt
//         (giả lập, có prompt thật), bấm → prompt được gọi, nút ẩn;
//         outcome accepted → ghi chú đã cài; dismissed → ghi rõ đã đóng.
//         Headless KHÔNG cài PWA thật được — chỉ kiểm wiring + manifest.
//     B3. Prefetch sau first paint: % tăng thật theo byte, xong 6/6 file,
//         loadedBytes == totalBytes (>10MB), boot vẫn chưa chạy, request
//         Pyodide đầu tiên >3s sau khi bắt đầu tải trang.
//     B4. Boot lần 2 (cache ấm: SW precache + prefetch) so với boot lần 1
//         (lạnh): ghi cả 2 số thật, báo rõ nhanh hơn hay không.
//     B5. Nhắc export: KB có dữ liệu + lần xuất cuối >7 ngày → banner
//         hiện; "Đã xuất" đặt lại mốc + ẩn; đặt lại mốc cũ + tải lại →
//         banner hiện lại (kiểm tra sớm, không cần boot); "Tắt nhắc lần
//         này" → tải lại vẫn ẩn (snooze 7 ngày).
//     B6. Offline sau lần thăm đầu: tự kiểm tra chạy được (5/6, mục Dịch
//         FAIL thật vì không mạng — chứng tỏ selftest.zip đã precache).
//     B7. 390px không tràn ngang, không pageerror cả phiên.
// KHÔNG thử được Safari thật (không có BrowserStack/LambdaTest sẵn):
// nút Cài trên iOS Safari (không có beforeinstallprompt), persist()
// trên Safari thật, prefetch trên mạng thật vẫn cần Joseph thử.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import { execFileSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX_ZIP = EXP + "/fixtures/extract_select_fixture.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtB = (n) => (n / 1048576).toFixed(1) + "MB";

// ================= S. Tĩnh =================
const appSrc = fs.readFileSync(SITE + "/app.js", "utf8");
const swSrc = fs.readFileSync(SITE + "/sw.js", "utf8");
const manifest = JSON.parse(fs.readFileSync(SITE + "/manifest.webmanifest", "utf8"));

{ // S1. node --check + manifest hợp lệ
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  const icons = manifest.icons || [];
  const has192 = icons.some((i) => i.sizes === "192x192" && fs.existsSync(SITE + "/" + i.src));
  const has512 = icons.some((i) => i.sizes === "512x512" && fs.existsSync(SITE + "/" + i.src));
  const ok = bad.length === 0 && manifest.name && manifest.short_name &&
    manifest.start_url === "." && manifest.display === "standalone" && has192 && has512;
  rec("static_node_check_manifest", ok,
    "node --check OK: app.js, sw.js" + (bad.length ? " | LỖI: " + bad.join(", ") : "") +
    "; manifest: name=" + JSON.stringify(manifest.name) + ", short_name=" +
    JSON.stringify(manifest.short_name) + ", start_url=" + JSON.stringify(manifest.start_url) +
    ", display=" + JSON.stringify(manifest.display) + ", icons 192=" + has192 + ", 512=" + has512,
    performance.now() - t0);
}

{ // S2. sw.js: selftest.zip precache + v9 + prefetch cache dự phòng
  const t0 = performance.now();
  const ver = (swSrc.match(/const CACHE_VERSION = "([^"]+)"/) || [])[1];
  const listMatch = swSrc.match(/const PRECACHE_URLS = \[([\s\S]*?)\];/);
  const listed = listMatch ? [...listMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
  const ok = ver === "v62-20261120" && listed.includes("fixtures/selftest.zip") &&
    /const PREFETCH_CACHE = "beartea-prefetch-v1"/.test(swSrc) &&
    /pre\.match\(req\)/.test(swSrc) &&
    /prefetchCache: PREFETCH_CACHE/.test(swSrc) &&
    fs.existsSync(SITE + "/fixtures/selftest.zip");
  rec("static_sw_selftest_precache_prefetch", ok,
    "CACHE_VERSION=" + ver + " (cần v62-20261120); precache " + listed.length +
    " mục, có fixtures/selftest.zip=" + listed.includes("fixtures/selftest.zip") +
    " (file trên đĩa: " + fs.statSync(SITE + "/fixtures/selftest.zip").size +
    "B); PREFETCH_CACHE const=" + /const PREFETCH_CACHE/.test(swSrc) +
    "; fetch thử prefetch cache khi cache chính trượt=" + /pre\.match\(req\)/.test(swSrc) +
    "; GET_VERSION trả prefetchCache=" + /prefetchCache: PREFETCH_CACHE/.test(swSrc),
    performance.now() - t0);
}

{ // S3. app.js tĩnh: PWA + persist/estimate + nhắc export + prefetch
  const t0 = performance.now();
  const checks = {
    beforeinstallprompt: /addEventListener\("beforeinstallprompt"/.test(appSrc),
    appinstalled: /addEventListener\("appinstalled"/.test(appSrc),
    standalone: /display-mode: standalone/.test(appSrc) && /navigator\.standalone/.test(appSrc),
    pwaStateHook: /__PB3_PWA_STATE/.test(appSrc),
    persist: /navigator\.storage\.persist\(\)/.test(appSrc),
    estimate: /navigator\.storage\.estimate\(\)/.test(appSrc),
    estimateUi: /Đã dùng/.test(appSrc) && /hạn mức/.test(appSrc),
    earlyPersist: /requestStorageEarly/.test(appSrc) && /requestIdleCallback/.test(appSrc),
    exportedBtn: /btn-kb-exported/.test(appSrc) && /kbRecordExportNow\(\)/.test(appSrc),
    snoozeBtn: /btn-kb-reminder-snooze/.test(appSrc) && /KB_EXPORT_SNOOZE_KEY/.test(appSrc),
    earlyReminder: /checkKbExportReminderEarly/.test(appSrc),
    prefetchUrls: /PYODIDE_PREFETCH_URLS/.test(appSrc) && /pyodide\.asm\.wasm/.test(appSrc),
    prefetchStateHook: /__PB3_PREFETCH_STATE/.test(appSrc),
    prefetchNoHook: /__PB3_NO_PREFETCH/.test(appSrc),
    prefetchDelay4000: /setTimeout\(\(\) => \{ prefetchPyodide/.test(appSrc) && /, 4000\)/.test(appSrc),
    prefetchAfterLoad: /addEventListener\("load", schedulePrefetch\)/.test(appSrc),
    prefetchSkipCached: /caches\.match\(url\)/.test(appSrc),
    prefetchCacheCounts: /cachedFiles/.test(appSrc) && /fetchedFiles/.test(appSrc),
    prefetchSaveData: /saveData/.test(appSrc),
    prefetchGzipByteCheck: /content-encoding/.test(appSrc) && /!encoded/.test(appSrc) && /got < total/.test(appSrc),
    prefetchNoStrictByteCheck: !/got !== total/.test(appSrc),
  };
  const missing = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_c3_wiring", missing.length === 0,
    "kiểm tra wiring C3 trong app.js: " + Object.entries(checks).map(([k, v]) => k + "=" + v).join(", ") +
    (missing.length ? " | THIẾU: " + missing.join(", ") : ""),
    performance.now() - t0);
}

{ // S4. Nạp app.js trong vm: không Worker, không fetch, prefetch chưa chạy
  const t0 = performance.now();
  const elements = new Map();
  function makeEl(id) {
    return { id, textContent: "", innerHTML: "", hidden: false, value: "", checked: false,
      disabled: false, files: null, style: {}, _listeners: {},
      classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
      addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
      click() {}, remove() {}, scrollIntoView() {},
    };
  }
  const $ = (id) => { if (!elements.has(id)) elements.set(id, makeEl(id)); return elements.get(id); };
  let fetchCalls = 0;
  const sandbox = {
    document: { getElementById: $, createElement: () => makeEl("a"),
      body: { appendChild() {}, removeChild() {} }, readyState: "loading" },
    Worker: class { constructor() { throw new Error("Worker created in vm?!"); } },
    fetch: () => { fetchCalls++; return Promise.reject(new Error("no fetch in vm")); },
    performance, setTimeout, clearTimeout, setInterval, clearInterval, console,
    localStorage: { _m: {}, getItem(k) { return this._m[k] || null; }, setItem(k, v) { this._m[k] = String(v); },
      removeItem(k) { delete this._m[k]; } },
    confirm: () => true, Blob, URL, TextEncoder, TextDecoder, atob, btoa,
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = () => {};
  let ok = false, detail = "";
  try {
    vm.createContext(sandbox);
    vm.runInContext(appSrc, sandbox, { filename: "app.js" });
    const bs = sandbox.__PB3_BOOT_STATE();
    const pf = sandbox.__PB3_PREFETCH_STATE();
    const pwa = sandbox.__PB3_PWA_STATE();
    ok = bs.booted === false && fetchCalls === 0 && pf.started === false &&
      pf.status === "idle" && pwa && pwa.hasPrompt === false;
    detail = "__PB3_BOOT_STATE=" + JSON.stringify(bs) + "; fetch đã gọi=" + fetchCalls +
      "; __PB3_PREFETCH_STATE=" + JSON.stringify(pf) +
      "; __PB3_PWA_STATE=" + JSON.stringify(pwa) +
      " (nạp xong chưa Worker, chưa fetch, prefetch chưa chạy — chỉ chạy sau load/idle)";
  } catch (e) { detail = "lỗi nạp app.js trong vm: " + String(e && e.message ? e.message : e); }
  rec("vm_no_prefetch_at_load", ok, detail, performance.now() - t0);
}

{ // S5. Shell tĩnh sau C3 (ghi số thật; không đặt ngưỡng PASS/FAIL mới)
  const t0 = performance.now();
  const files = ["index.html", "kb-store.js", "worker-pool.js", "stream.js", "app.js", "gt.js"];
  let raw = 0, gz = 0;
  const lines = [];
  for (const f of files) {
    const buf = fs.readFileSync(SITE + "/" + f);
    const g = zlib.gzipSync(buf, { level: 9 }).length;
    raw += buf.length; gz += g;
    lines.push(f + " raw=" + buf.length + "B gzip=" + g + "B");
  }
  rec("shell_size_after_c3", true,
    "shell lần tải đầu sau C3: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C3 thêm ~" +
    (fs.statSync(SITE + "/app.js").size - 169228) + "B vào app.js, không đổi kết luận.",
    performance.now() - t0);
}

// ================= B. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".zip": "application/zip", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css" };
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
  const fp = path.join(SITE, urlPath === "/" ? "index.html" : urlPath);
  if (!fp.startsWith(SITE)) { res.writeHead(403); res.end(); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found"); return; }
    res.writeHead(200, { "Content-Type": MIME[path.extname(fp)] || "application/octet-stream" });
    res.end(data);
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
const BASE = "http://127.0.0.1:" + PORT;

let browser = null;
let coldMs = null, warmMs = null;
const pageErrors = [];
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const elText = (page, id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const waitReady = async (page) => {
    await page.setInputFiles("#file-input", FX_ZIP);
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const waitSwCache = async (page) => {
    await page.waitForFunction(
      () => !!(navigator.serviceWorker && navigator.serviceWorker.controller),
      null, { timeout: 120000 });
    for (let i = 0; i < 60; i++) {
      const n = await page.evaluate(async () => {
        const names = await caches.keys();
        const main = names.find((x) => x.indexOf("beartea-v11-") === 0);
        if (!main) return 0;
        const c = await caches.open(main);
        return (await c.keys()).length;
      });
      if (n >= 125) return n;
      await sleep(2000);
    }
    return await page.evaluate(async () => {
      const names = await caches.keys();
      const main = names.find((x) => x.indexOf("beartea-v11-") === 0);
      if (!main) return 0;
      return (await (await caches.open(main)).keys()).length;
    });
  };

  // ---------- Context A: persist/estimate + PWA wiring + prefetch ----------
  const ctxA = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageA = await ctxA.newPage();
  pageA.on("pageerror", (e) => pageErrors.push("ctxA: " + String(e && e.message ? e.message : e)));
  const pyReqTimesA = [];
  const navStartA = Date.now();
  pageA.on("request", (r) => { if (r.url().includes("/pyodide/")) pyReqTimesA.push(Date.now() - navStartA); });
  { // B1. persist()+estimate() sớm (không cần boot, không cần chọn file)
    const t0 = performance.now();
    await pageA.goto(BASE + "/index.html");
    await pageA.waitForFunction(
      () => { const el = document.getElementById("kb-persist"); return el && el.textContent.includes("Lưu trữ bền vững"); },
      null, { timeout: 60000 });
    const txt = await elText(pageA, "kb-persist");
    const st = await pageA.evaluate(() => (typeof window.__PB3_STORAGE_STATE === "function" ? window.__PB3_STORAGE_STATE() : null));
    const hasVerdict = /ĐÃ CẤP|CHƯA CẤP/.test(txt);
    const hasEstimate = txt.includes("Đã dùng") && txt.includes("hạn mức");
    rec("browser_persist_estimate_early", hasVerdict && hasEstimate && st &&
      typeof st.usage === "number" && typeof st.quota === "number" && st.quota > 0 &&
      typeof st.persisted === "boolean",
      "#kb-persist (hiện sớm, chưa boot/chưa chọn file): " + JSON.stringify(txt) +
      "; __PB3_STORAGE_STATE=" + JSON.stringify(st) +
      " (headless Chromium thường CHƯA CẤP vì chưa cài/ưa dùng — ghi đúng sự thật, không PASS giả)",
      performance.now() - t0);
  }
  { // B2. PWA wiring: ẩn lúc đầu → hiện khi beforeinstallprompt → bấm gọi prompt
    const t0 = performance.now();
    await pageA.click("#btn-nav-kb"); // nút Cài nằm trong tab Kho KB (section ẩn)
    const initiallyHidden = await pageA.evaluate(() => document.getElementById("btn-pwa-install").hidden);
    const pwaNote = await elText(pageA, "pwa-note");
    const pwaState0 = await pageA.evaluate(() => window.__PB3_PWA_STATE());
    // Giả lập beforeinstallprompt có prompt thật. Thử dismissed TRƯỚC:
    // sau accepted thì pwaInstalled=true, handler bỏ qua event sau (đúng
    // hành vi app) nên dismissed phải thử khi chưa cài.
    await pageA.evaluate(() => {
      const ev = new Event("beforeinstallprompt");
      ev.prompt = async () => {};
      ev.userChoice = Promise.resolve({ outcome: "dismissed", platform: "web" });
      window.dispatchEvent(ev);
    });
    await sleep(300);
    const visibleAfterEv = await pageA.evaluate(() => !document.getElementById("btn-pwa-install").hidden);
    await pageA.click("#btn-pwa-install");
    await sleep(500);
    const dismissedNote = await elText(pageA, "pwa-note");
    // Rồi accepted: prompt được gọi, nút ẩn, ghi chú đã cài hiện.
    await pageA.evaluate(() => {
      const ev = new Event("beforeinstallprompt");
      ev.prompt = async () => { window.__c3_prompt_called = (window.__c3_prompt_called || 0) + 1; };
      ev.userChoice = Promise.resolve({ outcome: "accepted", platform: "web" });
      window.__c3_fake_ev = ev;
      window.dispatchEvent(ev);
    });
    await sleep(300);
    await pageA.click("#btn-pwa-install");
    await sleep(500);
    const called = await pageA.evaluate(() => window.__c3_prompt_called || 0);
    const hiddenAfterClick = await pageA.evaluate(() => document.getElementById("btn-pwa-install").hidden);
    const installedNote = await pageA.evaluate(() => !document.getElementById("pwa-installed-note").hidden);
    rec("browser_pwa_install_wiring", initiallyHidden === true && visibleAfterEv === true &&
      called >= 1 && hiddenAfterClick === true && installedNote === true &&
      pwaNote.includes("Chia sẻ") && pwaNote.includes("Thêm vào Màn hình chính") &&
      dismissedNote.includes("đã đóng bảng cài đặt"),
      "lúc đầu nút Cài ẩn=" + initiallyHidden + " (headless không có beforeinstallprompt thật); " +
      "pwa-note có hướng dẫn tay iOS=" + (pwaNote.includes("Chia sẻ") && pwaNote.includes("Thêm vào Màn hình chính")) +
      "; __PB3_PWA_STATE lúc đầu=" + JSON.stringify(pwaState0) +
      "; sau event giả (dismissed): nút hiện=" + visibleAfterEv + "; bấm → pwa-note=" +
      JSON.stringify(dismissedNote.slice(0, 120)) + " (ghi rõ đã đóng bảng cài đặt)" +
      "; sau event giả (accepted): bấm → prompt được gọi " + called +
      " lần, nút ẩn lại=" + hiddenAfterClick + ", ghi chú đã cài hiện=" + installedNote +
      ". Headless KHÔNG cài PWA thật được — chỉ kiểm wiring + manifest, không tuyên bố cài thành công.",
      performance.now() - t0);
  }
  let prefetchDetail = "";
  { // B3. Prefetch sau first paint: % thật theo byte, xong 6/6, chưa boot
    const t0 = performance.now();
    const pf = await pageA.evaluate(async () => {
      const t0 = Date.now();
      while (Date.now() - t0 < 180000) {
        const s = window.__PB3_PREFETCH_STATE();
        if (s.done || s.status === "partial" || s.status === "skipped") return s;
        await new Promise((r) => setTimeout(r, 1000));
      }
      return window.__PB3_PREFETCH_STATE();
    });
    const pfText = await elText(pageA, "pyodide-prefetch-text");
    const barVal = await pageA.evaluate(() => { const b = document.getElementById("pyodide-prefetch-bar"); return b ? b.value : null; });
    const bs = await pageA.evaluate(() => window.__PB3_BOOT_STATE());
    const resTimes = await pageA.evaluate(() =>
      performance.getEntriesByType("resource")
        .filter((e) => e.name.includes("/pyodide/"))
        .map((e) => Math.round(e.startTime)));
    const firstPy = resTimes.length ? Math.min(...resTimes) : null;
    // Khi SW precache đã tải xong hết trước prefetch, prefetch toàn
    // cache-hit nên trang không phát request Pyodide nào — đó là trường
    // hợp TỐT NHẤT (không tốn mạng), không phải lỗi: chấp nhận khi cả 6
    // file đều từ cache. Ngược lại, request đầu tiên phải sau first
    // paint (>3000ms).
    const pyTimingOk = (firstPy === null && pf.cachedFiles === 6) ||
      (firstPy !== null && firstPy > 3000);
    prefetchDetail = "__PB3_PREFETCH_STATE=" + JSON.stringify(pf) +
      "; UI=" + JSON.stringify(pfText) + "; thanh tiến độ value=" + barVal +
      "; boot sau prefetch: booted=" + bs.booted + ", workerCreated=" + bs.workerCreated +
      "; request Pyodide từ trang: " + resTimes.length + " file, sớm nhất " + firstPy +
      "ms sau khi bắt đầu tải trang" +
      (firstPy === null ? " (0 request vì cả 6 file đã có sẵn trong cache từ SW precache — không tốn mạng thêm)" : " (cần >3000ms = sau first paint, không chặn boot)") +
      "; từ cache=" + pf.cachedFiles + ", tải mới=" + pf.fetchedFiles;
    rec("browser_prefetch_after_first_paint", pf.done === true && pf.status === "done" &&
      pf.filesDone === 6 && pf.filesTotal === 6 && pf.failedFiles.length === 0 &&
      pf.loadedBytes === pf.totalBytes && pf.totalBytes > 10 * 1024 * 1024 &&
      pf.cachedFiles + pf.fetchedFiles === 6 &&
      pfText.includes("tải trước xong") && barVal === 100 && bs.booted === false &&
      pyTimingOk,
      prefetchDetail, performance.now() - t0);
  }
  await ctxA.close();

  // ---------- Context B: boot lạnh vs boot ấm (cùng context) ----------
  const ctxB = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageB = await ctxB.newPage();
  pageB.on("pageerror", (e) => pageErrors.push("ctxB: " + String(e && e.message ? e.message : e)));
  await pageB.addInitScript(() => { window.__PB3_NO_PREFETCH = true; });
  { // B4a. Boot lạnh (tắt prefetch để đo sạch; SW precache vẫn chạy nền)
    const t0 = performance.now();
    await pageB.goto(BASE + "/index.html");
    const tBoot0 = performance.now();
    await waitReady(pageB);
    coldMs = Math.round(performance.now() - tBoot0);
    rec("browser_boot_cold_ms", true,
      "boot lạnh (context mới, __PB3_NO_PREFETCH, chọn file fixture ngay sau tải trang): " +
      coldMs + "ms từ lúc chọn file tới “Sẵn sàng — 20 plugin” (đo thật, gồm tải Pyodide qua mạng local + biên dịch wasm)",
      performance.now() - t0);
  }
  { // B4b. Boot ấm: tải lại trang (cache đã ấm: SW precache + lần boot lạnh) rồi boot lại
    const t0 = performance.now();
    await pageB.reload();
    await pageB.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
      null, { timeout: 60000 });
    const tBoot0 = performance.now();
    await waitReady(pageB);
    warmMs = Math.round(performance.now() - tBoot0);
    const faster = warmMs < coldMs;
    rec("browser_boot_warm_vs_cold", warmMs > 0 && coldMs > 0,
      "boot lần 2 (cùng context, cache ấm từ SW precache + prefetch/lần boot trước): " + warmMs +
      "ms; boot lần 1 (lạnh): " + coldMs + "ms → lần 2 " +
      (faster ? ("NHANH HƠN " + Math.round((1 - warmMs / coldMs) * 100) + "% (" + (coldMs - warmMs) + "ms)") :
        ("CHẬM HƠN " + Math.round((warmMs / coldMs - 1) * 100) + "% (" + (warmMs - coldMs) + "ms) — số thật, không ước lượng; " +
         "phần lớn thời gian boot là biên dịch wasm (cache không bỏ qua bước này), chỉ phần tải file là nhanh hơn")) +
      ". Lưu ý: số đo trên máy chủ local (mạng LAN nhanh) nên chênh lệch nhỏ hơn trên mạng thật/Safari.",
      performance.now() - t0);
  }
  await ctxB.close();

  // ---------- Context C: nhắc export ----------
  const ctxC = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageC = await ctxC.newPage();
  pageC.on("pageerror", (e) => pageErrors.push("ctxC: " + String(e && e.message ? e.message : e)));
  const EIGHT_DAYS = 8 * 86400000;
  // Seed 1 lần duy nhất (fresh profile): lần xuất cuối 8 ngày trước.
  // KHÔNG ghi đè ở lần tải lại sau (B5c/B5d tự đặt lại mốc qua evaluate),
  // và KHÔNG xóa snooze (B5d cần snooze còn nguyên sau reload).
  await pageC.addInitScript((ms) => {
    if (!localStorage.getItem("pb3_kb_last_export"))
      localStorage.setItem("pb3_kb_last_export", String(Date.now() - ms));
  }, EIGHT_DAYS);
  { // B5a. KB có dữ liệu + lần xuất cuối >7 ngày → banner hiện
    const t0 = performance.now();
    await pageC.goto(BASE + "/index.html");
    await waitReady(pageC);
    await pageC.click("#btn-nav-kb");
    await pageC.click("#btn-kb-add");
    await pageC.waitForFunction(
      () => { const el = document.getElementById("kb-result"); return el && el.textContent.includes("Đã thêm game vào KB"); },
      null, { timeout: 120000 });
    await pageC.waitForFunction(
      () => { const el = document.getElementById("kb-export-reminder"); return el && !el.hidden; },
      null, { timeout: 60000 });
    const txt = await elText(pageC, "kb-export-reminder-text");
    rec("browser_export_reminder_shows", txt.includes("hơn 7 ngày"),
      "sau khi thêm game vào KB (KB đã có dữ liệu, lần xuất cuối 8 ngày trước): banner nhắc hiện, nội dung=" +
      JSON.stringify(txt), performance.now() - t0);
  }
  { // B5b. "Đã xuất" → ẩn + mốc đặt lại ~bây giờ
    const t0 = performance.now();
    await pageC.click("#btn-kb-exported");
    await sleep(300);
    const hidden = await pageC.evaluate(() => document.getElementById("kb-export-reminder").hidden);
    const last = await pageC.evaluate(() => parseInt(localStorage.getItem("pb3_kb_last_export") || "0", 10));
    const fresh = Date.now() - last < 60000;
    rec("browser_export_reminder_exported_btn", hidden === true && fresh,
      "bấm “Đã xuất”: banner ẩn=" + hidden + "; mốc lần xuất cuối trong localStorage=" +
      new Date(last).toISOString() + " (cách bây giờ " + Math.round((Date.now() - last) / 1000) +
      "s — đặt lại mốc đúng)", performance.now() - t0);
  }
  { // B5c. Đặt lại mốc cũ + tải lại → banner hiện lại (kiểm tra sớm, không cần boot)
    const t0 = performance.now();
    await pageC.evaluate((ms) => {
      localStorage.setItem("pb3_kb_last_export", String(Date.now() - ms));
    }, EIGHT_DAYS);
    await pageC.reload();
    await pageC.waitForFunction(
      () => { const el = document.getElementById("kb-export-reminder"); return el && !el.hidden; },
      null, { timeout: 60000 });
    const txt = await elText(pageC, "kb-export-reminder-text");
    rec("browser_export_reminder_early_reload", txt.includes("hơn 7 ngày"),
      "tải lại trang (chưa boot, chưa chọn file — kiểm tra sớm qua IndexedDB): banner nhắc hiện lại, nội dung=" +
      JSON.stringify(txt), performance.now() - t0);
  }
  { // B5d. "Tắt nhắc lần này" → tải lại vẫn ẩn (snooze 7 ngày)
    const t0 = performance.now();
    await pageC.click("#btn-nav-kb"); // sau B5c chưa mở tab KB (chỉ reload)
    await pageC.click("#btn-kb-reminder-snooze");
    await sleep(300);
    const snooze = await pageC.evaluate(() => parseInt(localStorage.getItem("pb3_kb_export_snooze_until") || "0", 10));
    await pageC.reload();
    await sleep(6000); // đủ lâu để kiểm tra sớm chạy xong (idle ~1,5s + đọc IndexedDB)
    const hidden = await pageC.evaluate(() => document.getElementById("kb-export-reminder").hidden);
    const snoozeDays = Math.round((snooze - Date.now()) / 86400000);
    rec("browser_export_reminder_snooze", hidden === true && snooze > Date.now() && snoozeDays === 7,
      "bấm “Tắt nhắc lần này”: mốc snooze=" + new Date(snooze).toISOString() + " (sau ~" + snoozeDays +
      " ngày nữa mới nhắc lại); tải lại trang rồi đợi 6s: banner vẫn ẩn=" + hidden +
      " (không nhắc liên tục mỗi lần mở trang)", performance.now() - t0);
  }
  await ctxC.close();

  // ---------- Context D: offline sau lần thăm đầu → tự kiểm tra ----------
  const ctxD = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageD = await ctxD.newPage();
  pageD.on("pageerror", (e) => pageErrors.push("ctxD: " + String(e && e.message ? e.message : e)));
  { // B6. Lần thăm 1 online (SW precache đủ, gồm selftest.zip) → offline: tự kiểm tra
    const t0 = performance.now();
    await pageD.goto(BASE + "/index.html");
    const cached = await waitSwCache(pageD);
    const selftestCached = await pageD.evaluate(async () => {
      const names = await caches.keys();
      for (const n of names) {
        const c = await caches.open(n);
        const m = await c.match(new URL("fixtures/selftest.zip", location.href).href);
        if (m) return true;
      }
      return false;
    });
    await ctxD.setOffline(true);
    await pageD.reload();
    await pageD.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
      null, { timeout: 60000 });
    await waitReady(pageD); // boot offline từ cache
    await pageD.click("#btn-nav-tools");
    await pageD.click("#btn-diagnose");
    await pageD.waitForFunction(
      () => { const el = document.getElementById("tab-diagnose"); return el && !el.hidden; },
      null, { timeout: 30000 });
    await pageD.click("#btn-selftest-run");
    await pageD.waitForFunction(
      () => {
        const b = document.getElementById("btn-selftest-run");
        const rows = document.querySelectorAll("#selftest-result table tr");
        return b && !b.disabled && rows.length >= 7;
      }, null, { timeout: 240000 });
    const rows = await pageD.evaluate(() => {
      const rs = [...document.querySelectorAll("#selftest-result table tr")].slice(1);
      return rs.map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent.trim()));
    });
    const passed = rows.filter((r) => r[1] === "PASS").length;
    const tr = rows.find((r) => r[0].includes("Dịch thử"));
    await ctxD.setOffline(false);
    rec("browser_offline_selftest_after_first_visit", cached >= 125 && selftestCached === true &&
      rows.length === 6 && passed === 5 && tr && tr[1] === "FAIL",
      "lần thăm 1 online: cache chính " + cached + "/125+ mục, fixtures/selftest.zip trong cache=" + selftestCached +
      "; offline tải lại → boot tới Sẵn sàng → tự kiểm tra: " + passed + "/6 PASS" +
      " (mục Dịch thử FAIL thật: " + JSON.stringify((tr && tr[2]) || "").slice(0, 160) +
      " — không mạng, không PASS giả); trích xuất thử chạy được offline chứng tỏ selftest.zip đã precache (nợ C2 đã trả)",
      performance.now() - t0);
  }
  await ctxD.close();

  { // B7. 390px không tràn + không pageerror
    const t0 = performance.now();
    // Đo lại trên 1 trang mới (pageA đã đóng cùng ctxA).
    const ctxE = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
    const pageE = await ctxE.newPage();
    pageE.on("pageerror", (e) => pageErrors.push("ctxE: " + String(e && e.message ? e.message : e)));
    await pageE.goto(BASE + "/index.html");
    await sleep(2000);
    const o = await pageE.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    await ctxE.close();
    rec("browser_390_no_overflow_no_errors", o.sw <= o.cw + 1 && pageErrors.length === 0,
      "390px: scrollWidth=" + o.sw + " clientWidth=" + o.cw + " (không tràn); pageerror cả phiên (5 context): " +
      (pageErrors.length ? JSON.stringify(pageErrors) : "không có"),
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
const passed = Object.values(results).filter((v) => v.ok).length;
const total = Object.keys(results).length;
const mdLines = ["# Kiểm chứng mục C3 — PWA + persist() + nhắc export + Pyodide prefetch sau first paint",
  "",
  "Ngày: " + new Date().toISOString() + " (giờ UTC). Harness: exp/verify_analysis_c3.mjs.",
  "",
  "## Kết quả: " + passed + "/" + total + " PASS" + (failures ? " — FAIL: " + Object.keys(results).filter((k) => !results[k].ok).join(", ") : ""),
  "",
  ...Object.entries(results).map(([k, v]) => "### " + k + ": " + (v.ok ? "PASS" : "FAIL") + (v.seconds != null ? " (" + v.seconds + "s)" : "") + "\n" + v.detail + "\n"),
  "## File thay đổi",
  "- `site/sw.js`: CACHE_VERSION v8 → v9; thêm `fixtures/selftest.zip` vào PRECACHE_URLS (nợ C2); thêm PREFETCH_CACHE `beartea-prefetch-v1` + fetch dự phòng khi cache chính trượt; GET_VERSION trả thêm prefetchCache.",
  "- `site/index.html`: nhắc export thành div có nút “Đã xuất” + “Tắt nhắc lần này”; thêm `#pwa-installed-note`; thêm `#pyodide-prefetch` (chữ + thanh tiến độ) dưới dòng trạng thái.",
  "- `site/app.js`: refreshKbPersistStatus() thêm estimate() (Đã dùng/hạn mức) + hook `__PB3_STORAGE_STATE`; xin persist() sớm sau first paint/lần tương tác đầu; PWA appinstalled + standalone + kết quả prompt; nhắc export có snooze 7 ngày + kiểm tra sớm qua IndexedDB (không cần boot); prefetch Pyodide sau first paint (trì hoãn ~4s, % thật theo byte, bỏ qua file đã cache, ghi vào cache SW/prefetch, hook `__PB3_PREFETCH_STATE`/`__PB3_NO_PREFETCH`).",
  "- `exp/verify_item6.mjs`: expected thêm `fixtures/selftest.zip` (theo nợ C2).",
  "- `exp/check_item7_static.py`, `exp/check_item8_static.py`, `exp/verify_item8.mjs`: ghim CACHE_VERSION v8 → v9 (đổi precache thì phải đổi version, theo luật trong sw.js).",
  "- Harness mới: `exp/verify_analysis_c3.mjs` (+ kết quả này).",
  "",
  "## Safari thật — chưa thử được",
  "- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.",
  "- CẦN JOSEPH (Safari thật, iPhone): mở trang → xem dòng “Đang tải trước Pyodide… %” rồi bấm dùng tool (boot có nhanh hơn không); tab Kho KB → xem “Lưu trữ bền vững” + “Đã dùng/hạn mức”; thử “Thêm vào Màn hình chính” bằng tay (Safari không có beforeinstallprompt); để KB quá 7 ngày không xuất → xem banner nhắc + nút “Đã xuất”/“Tắt nhắc”.",
  "",
];
fs.writeFileSync(EXP + "/analysis_c3_results.md", mdLines.join("\n") + "\n");
fs.writeFileSync(EXP + "/analysis_c3_results.json", JSON.stringify(
  { when: new Date().toISOString(), total, passed, failures, cold_boot_ms: coldMs ?? null, warm_boot_ms: warmMs ?? null, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
