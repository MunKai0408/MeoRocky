// verify_analysis_c2.mjs — Kiểm chứng mục C2: trang/tab Chẩn đoán +
// nút "Tự kiểm tra" trong app BearTea (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_analysis_c2.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: analysis_c2_results.md + analysis_c2_results.json
//
//  N. gt.js trực tiếp trên Node, server giả (127.0.0.1):
//     N1. Google 429 → MyMemory được dùng; trace từng bước có ms (số),
//         Google có status 429, MyMemory ok.
//     N2. Proxy OK → trace proxy ok có ms; Google/MyMemory không gọi.
//  B. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local, proxy giả có CORS):
//     B1. Tab Chẩn đoán hiện đủ: trạng thái boot (20 plugin, font
//         142/142), bộ nhớ ước tính, bảng 5 provider.
//     B2. Proxy đã cấu hình (localStorage) → hàng Proxy "Đã cấu hình",
//         lần gọi gần nhất "chưa gọi lần nào" trước khi tự kiểm tra.
//     B3. Nút "Tự kiểm tra" chạy xong: 6/6 PASS, từng mục có chi tiết
//         thật (plugin 20, font 142/142, English.json đủ 3 chuỗi,
//         dịch thử provider proxy, IndexedDB ghi-đọc-xóa).
//     B4. Bấm "Tự kiểm tra" lần 2 vẫn chạy xong 6/6 (không kẹt, nút
//         bấm lại được).
//     B5. Sau tự kiểm tra, hàng Proxy hiện "OK · N ms" (lần gọi gần nhất).
//     B6. Nút "Xuất JSON chẩn đoán" cho ra file JSON đúng schema (when,
//         booted, boot.plugins=20, memory, diag_state.last_trace có
//         proxy ok, selftest.passed=6).
//     B7. KHÔNG có proxy + KHÔNG mạng ngoài (sandbox): dịch thử FAIL
//         thật kèm lỗi nguyên văn (mất mạng/Google/MyMemory), 5 mục còn
//         lại vẫn PASS — không bịa PASS cho mục chưa chạy được.
//     B8. diagnostics.html: nút "Kiểm tra môi trường" ghi env; nút
//         "Xuất JSON" cho ra JSON có env; nút Google trực tiếp ghi lỗi
//         thật (ok:false) thay vì đứng im.
//     B9. 390px: không tràn ngang, không lỗi trang (cả 2 trang).
// KHÔNG thử được Safari thật (không có BrowserStack/LambdaTest sẵn):
// nút Lưu/Chia sẻ và Google/MyMemory thật vẫn cần Joseph thử trên iPhone.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { execSync } from "node:child_process";
import {
  translateOneWithFallbacks, setTranslateProxyUrl, setGoogleEndpoint,
  setMyMemoryEndpoint, setLibreTranslateEndpoint, getFallbackTrace,
} from "../site/gt.js";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const OUT = EXP + "/out/analysis_c2";
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
fs.mkdirSync(OUT, { recursive: true });

// ================= Fake server: proxy + gtx + mymemory =================
// Proxy giả cần CORS (trang serve ở port khác): ACAO:* + OPTIONS.
const hits = { gtx: 0, mymemory: 0, proxy: 0 };
const modes = { gtx: "429", mymemory: "ok", proxy: "ok" };
function gtxJson(q) {
  return JSON.stringify([[[ "DỊCH:" + q, q, null, null, 1 ]], null, "en"]);
}
const fake = http.createServer((req, res) => {
  const url = new URL(req.url || "/", "http://127.0.0.1");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "content-type, accept");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    if (url.pathname === "/gtx") {
      hits.gtx++;
      if (modes.gtx === "429") { res.writeHead(429, { "content-type": "text/html" }); res.end("<html><head><title>Sorry...</title></head></html>"); return; }
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
    if (url.pathname === "/proxy" && req.method === "POST") {
      hits.proxy++;
      let parsed = {};
      try { parsed = JSON.parse(body); } catch (_) {}
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
const FAKE_PORT = fake.address().port;
const FAKE = "http://127.0.0.1:" + FAKE_PORT;

// ================= N. gt.js trực tiếp trên Node =================
setGoogleEndpoint(FAKE + "/gtx");
setMyMemoryEndpoint(FAKE + "/mymemory");
setLibreTranslateEndpoint("");
{ // N1. Google 429 → MyMemory; trace có ms + status 429
  const t0 = performance.now();
  modes.gtx = "429"; modes.mymemory = "ok"; modes.proxy = "ok";
  setTranslateProxyUrl("");
  hits.gtx = 0; hits.mymemory = 0;
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  const g = tr.find((e) => e.step === "google");
  const m = tr.find((e) => e.step === "mymemory");
  rec("node_trace_ms_status", out === "Xin chào thế giới" &&
    g && !g.ok && g.status === 429 && typeof g.ms === "number" && g.ms >= 0 &&
    m && m.ok && typeof m.ms === "number" && m.ms >= 0,
    `kết quả=${JSON.stringify(out)}; google={ok:${g && g.ok}, status:${g && g.status}, ms:${g && g.ms}, kind:${g && g.kind}}; ` +
    `mymemory={ok:${m && m.ok}, ms:${m && m.ms}}; Google gọi ${hits.gtx} lần, MyMemory ${hits.mymemory} lần`,
    performance.now() - t0);
}
{ // N2. Proxy OK → trace proxy ok có ms; Google/MyMemory 0 lần
  const t0 = performance.now();
  modes.proxy = "ok";
  setTranslateProxyUrl(FAKE + "/proxy");
  hits.gtx = 0; hits.mymemory = 0; hits.proxy = 0;
  const out = await translateOneWithFallbacks("Hello world", "en", "vi");
  const tr = getFallbackTrace();
  const p = tr.find((e) => e.step === "proxy");
  rec("node_trace_proxy_ok", out === "VI qua proxy: Hello world" &&
    p && p.ok && typeof p.ms === "number" && p.ms >= 0 &&
    hits.proxy === 1 && hits.gtx === 0 && hits.mymemory === 0,
    `kết quả=${JSON.stringify(out)}; proxy={ok:${p && p.ok}, ms:${p && p.ms}}; ` +
    `proxy gọi ${hits.proxy} lần, Google ${hits.gtx} lần, MyMemory ${hits.mymemory} lần`,
    performance.now() - t0);
}
setTranslateProxyUrl("");

// ================= B. Trình duyệt thật =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
  ".zip": "application/zip",
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
const PORT = server.address().port;

let browser = null;
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
  const openDiagnose = async (page) => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-diagnose");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-diagnose"); return el && !el.hidden; },
      null, { timeout: 30000 });
  };
  const waitReady = async (page) => {
    // Mục 6 (lazy boot): boot chỉ chạy khi chọn file — chọn 1 file nhỏ
    // trước rồi mới chờ "Sẵn sàng".
    try {
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 180000 });
    } catch (e) {
      await page.goto("http://127.0.0.1:" + PORT + "/index.html");
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 240000 });
    }
  };
  const readSelfTestRows = (page) => page.evaluate(() => {
    const rows = [...document.querySelectorAll("#selftest-result table tr")].slice(1);
    return rows.map((r) => [...r.querySelectorAll("td")].map((c) => c.textContent.trim()));
  });
  const waitSelfTestDone = (page) => page.waitForFunction(
    () => {
      const b = document.getElementById("btn-selftest-run");
      const rows = document.querySelectorAll("#selftest-result table tr");
      return b && !b.disabled && rows.length >= 7;
    }, null, { timeout: 240000 });

  // ---------- Context 1: có proxy giả ----------
  const ctx1 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page1 = await ctx1.newPage();
  page1.on("pageerror", (e) => pageErrors.push("ctx1: " + String(e && e.message ? e.message : e)));
  await page1.addInitScript((url) => { localStorage.setItem("pb3_translate_proxy", url); }, FAKE + "/proxy");
  { // B0. Boot
    const t0 = performance.now();
    await page1.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady(page1);
    rec("browser_boot", true, "boot tới Sẵn sàng (390px, proxy giả đã cấu hình sẵn)", performance.now() - t0);
  }
  { // B1+B2. Tab Chẩn đoán hiện đủ + proxy đã cấu hình, chưa gọi lần nào
    const t0 = performance.now();
    await openDiagnose(page1);
    await page1.waitForFunction(
      () => { const el = document.getElementById("appdiag-status"); return el && el.textContent.includes("Proxy Cloudflare"); },
      null, { timeout: 60000 });
    const txt = await elText(page1, "appdiag-status");
    rec("browser_diag_tab_full", txt.includes("Khởi động: OK") && txt.includes("20 plugin") &&
      txt.includes("142/142") && txt.includes("Bộ nhớ ước tính") &&
      txt.includes("Proxy Cloudflare") && txt.includes("Google trực tiếp") &&
      txt.includes("MyMemory") && txt.includes("LibreTranslate") && txt.includes("Từ điển offline"),
      `appdiag-status: ${JSON.stringify(txt.slice(0, 600))}`, performance.now() - t0);
    const proxyRow = await page1.evaluate(() => {
      const rows = [...document.querySelectorAll("#appdiag-status table tr")];
      const r = rows.find((x) => x.textContent.includes("Proxy Cloudflare"));
      return r ? r.textContent : "";
    });
    rec("browser_diag_proxy_configured_uncalled", proxyRow.includes("Đã cấu hình") && proxyRow.includes("chưa gọi lần nào"),
      `hàng Proxy trước tự kiểm tra: ${JSON.stringify(proxyRow)}`, performance.now() - t0);
  }
  let rows1 = [];
  { // B3. Tự kiểm tra lần 1: 6/6 PASS
    const t0 = performance.now();
    await page1.click("#btn-selftest-run");
    await waitSelfTestDone(page1);
    rows1 = await readSelfTestRows(page1);
    const passed = rows1.filter((r) => r[1] === "PASS").length;
    const names = rows1.map((r) => r[0]).join(" | ");
    rec("browser_selftest_all_pass", rows1.length === 6 && passed === 6,
      `6 mục (${names}); PASS ${passed}/6; chi tiết: ` +
      rows1.map((r) => r[0] + "=" + r[1] + " [" + (r[2] || "").slice(0, 120) + "]").join("; "),
      performance.now() - t0);
  }
  { // B4. Tự kiểm tra lần 2: vẫn 6/6 (bấm lại được, không kẹt)
    const t0 = performance.now();
    await page1.click("#btn-selftest-run");
    await waitSelfTestDone(page1);
    const rows = await readSelfTestRows(page1);
    const passed = rows.filter((r) => r[1] === "PASS").length;
    rec("browser_selftest_repeatable", rows.length === 6 && passed === 6,
      `lần 2: PASS ${passed}/6 (nút bấm lại được, không treo UI)`, performance.now() - t0);
  }
  { // B5. Sau tự kiểm tra, hàng Proxy hiện lần gọi gần nhất OK · ms
    const t0 = performance.now();
    const txt = await elText(page1, "appdiag-status");
    const proxyRow = await page1.evaluate(() => {
      const rows = [...document.querySelectorAll("#appdiag-status table tr")];
      const r = rows.find((x) => x.textContent.includes("Proxy Cloudflare"));
      return r ? r.textContent : "";
    });
    rec("browser_diag_last_call_after_selftest", /OK · \d+ ms/.test(proxyRow),
      `hàng Proxy sau tự kiểm tra: ${JSON.stringify(proxyRow)}`, performance.now() - t0);
  }
  { // B6. Xuất JSON chẩn đoán: đúng schema
    const t0 = performance.now();
    let dl = null;
    try {
      const dlPromise = page1.waitForEvent("download", { timeout: 15000 });
      await page1.click("#btn-appdiag-export");
      dl = await dlPromise;
    } catch (e) {
      // Headless có thể hiện thanh Lưu file thay vì tải ngay (canShare):
      // bấm nút dự phòng "Tải về (blob)".
      await page1.click("#btn-blob-download");
      dl = await page1.waitForEvent("download", { timeout: 15000 });
    }
    const dlPath = OUT + "/beartea-chan-doan.json";
    await dl.saveAs(dlPath);
    const data = JSON.parse(fs.readFileSync(dlPath, "utf8"));
    const trace = (data.diag_state && data.diag_state.last_trace) || [];
    const proxyOk = trace.find((e) => e.step === "proxy" && e.ok);
    rec("browser_export_diag_json", data.booted === true && data.boot && data.boot.plugins === 20 &&
      data.memory && typeof data.memory.quotaMB === "number" && proxyOk &&
      typeof proxyOk.ms === "number" && data.selftest && data.selftest.passed === 6 &&
      Array.isArray(data.selftest.items) && data.selftest.items.length === 6,
      `file ${path.basename(dlPath)} (${fs.statSync(dlPath).size} byte): booted=${data.booted}, ` +
      `plugins=${data.boot && data.boot.plugins}, quotaMB=${data.memory && data.memory.quotaMB}, ` +
      `proxy trace ok ms=${proxyOk && proxyOk.ms}, selftest passed=${data.selftest && data.selftest.passed}/6`,
      performance.now() - t0);
  }
  { // B9a. 390px không tràn (trang chính)
    const t0 = performance.now();
    const ov = await page1.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    rec("browser_390_no_overflow_main", ov.sw <= ov.cw + 1,
      `390px: scrollWidth=${ov.sw} clientWidth=${ov.cw} (không tràn)`, performance.now() - t0);
  }
  await ctx1.close();

  // ---------- Context 2: KHÔNG proxy, KHÔNG mạng ngoài ----------
  const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page2 = await ctx2.newPage();
  page2.on("pageerror", (e) => pageErrors.push("ctx2: " + String(e && e.message ? e.message : e)));
  { // B7. Dịch thử FAIL thật, 5 mục còn lại PASS
    const t0 = performance.now();
    await page2.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady(page2);
    await openDiagnose(page2);
    await page2.click("#btn-selftest-run");
    await waitSelfTestDone(page2);
    const rows = await readSelfTestRows(page2);
    const tr = rows.find((r) => r[0].includes("Dịch thử"));
    const passed = rows.filter((r) => r[1] === "PASS").length;
    rec("browser_selftest_translate_fails_honestly", rows.length === 6 && passed === 5 &&
      tr && tr[1] === "FAIL" && /Google|MyMemory|mất mạng|từ điển offline/i.test(tr[2] || ""),
      `không proxy + sandbox không mạng ngoài: PASS ${passed}/6; ` +
      `mục Dịch thử = FAIL thật, lỗi nguyên văn: ${JSON.stringify((tr && tr[2]) || "").slice(0, 300)}; ` +
      `không có mục nào bị ghi PASS giả`,
      performance.now() - t0);
  }
  await ctx2.close();

  // ---------- diagnostics.html ----------
  const ctx3 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page3 = await ctx3.newPage();
  page3.on("pageerror", (e) => pageErrors.push("ctx3: " + String(e && e.message ? e.message : e)));
  { // B8. Trang Kiểm tra dịch: env + xuất JSON + Google trực tiếp lỗi thật
    const t0 = performance.now();
    await page3.goto("http://127.0.0.1:" + PORT + "/diagnostics.html");
    await page3.click("#btn-env");
    await page3.waitForFunction(
      () => { const el = document.getElementById("out"); return el && el.textContent.includes("deviceMemoryGB"); },
      null, { timeout: 30000 });
    const dlPromise = page3.waitForEvent("download", { timeout: 15000 });
    await page3.click("#btn-export");
    const dl = await dlPromise;
    const dlPath = OUT + "/beartea-kiem-tra-dich.json";
    await dl.saveAs(dlPath);
    const data = JSON.parse(fs.readFileSync(dlPath, "utf8"));
    const envOk = data.env && typeof data.env.indexedDB === "boolean";
    await page3.click("#btn-direct");
    await page3.waitForFunction(
      () => { const el = document.getElementById("out"); return el && el.textContent.includes("google_direct"); },
      null, { timeout: 60000 });
    const outTxt = await elText(page3, "out");
    const outJson = JSON.parse(outTxt);
    const g = outJson.google_direct || {};
    rec("browser_diagnostics_page_export", envOk && g.ok === false && typeof g.error === "string" && g.error.length > 0,
      `xuất JSON có env (indexedDB=${data.env && data.env.indexedDB}); ` +
      `Google trực tiếp từ sandbox: ok=${g.ok}, lỗi thật=${JSON.stringify(g.error || "").slice(0, 160)} (không đứng im, không PASS giả)`,
      performance.now() - t0);
  }
  { // B9b. 390px không tràn (diagnostics.html) + không lỗi trang
    const t0 = performance.now();
    const ov = await page3.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    rec("browser_390_no_overflow_no_errors", ov.sw <= ov.cw + 1 && pageErrors.length === 0,
      `diagnostics.html 390px: scrollWidth=${ov.sw} clientWidth=${ov.cw}; ` +
      `pageerror cả 3 context: ${pageErrors.length ? JSON.stringify(pageErrors) : "không có"}`,
      performance.now() - t0);
  }
  await ctx3.close();
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();
fake.close();

// ================= Ghi kết quả =================
const passed = Object.values(results).filter((v) => v.ok).length;
const mdLines = ["# Kiểm chứng mục C2 — Trang Chẩn đoán + nút Tự kiểm tra",
  "",
  "Ngày: 2026-11-16. Harness: exp/verify_analysis_c2.mjs.",
  "Trang/tab Chẩn đoán trong app (tab Chẩn đoán, mục \"Chẩn đoán ứng dụng\") + hoàn thiện diagnostics.html (nút Xuất JSON).",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  `Tổng: ${Object.keys(results).length} mục, ${passed} PASS, ${failures} FAIL.`,
  "",
  "## File thay đổi",
  "- `site/gt.js`: trace fallback giàu thêm ms/status/errName/kind từng bước (hành vi fallback không đổi).",
  "- `site/worker.js`: 2 op mới `diag_state` (cấu hình provider + trace lần gần nhất, không cần boot) và `diag_translate_probe` (dịch thử \"Hello world\" qua đủ chuỗi fallback).",
  "- `site/app.js`: mục \"Chẩn đoán ứng dụng\" trong tab Chẩn đoán (bảng 5 provider + boot + bộ nhớ), nút \"Tự kiểm tra\" (6 mục thật), nút \"Xuất JSON chẩn đoán\" (qua download()/Lưu-Chia sẻ), hook `window.__PB3_LAST_DIAG`.",
  "- `site/index.html`: giao diện mục Chẩn đoán ứng dụng trong #tab-diagnose (3 nút + 2 vùng kết quả).",
  "- `site/diagnostics.html`: nút \"Xuất JSON\" (Chia sẻ chính, blob dự phòng).",
  "- `site/fixtures/selftest.zip` (169 byte): fixture trích xuất thử — en.json 3 chuỗi, đã kiểm chứng extract ra English.json đủ 3 chuỗi trên Node Pyodide.",
  "- Harness: `exp/verify_analysis_c2.mjs` (+ kết quả này, `exp/out/analysis_c2/*.json`).",
  "",
  "## Safari thật — chưa thử được",
  "- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.",
  "- CẦN JOSEPH (Safari thật, iPhone): mở tab Chẩn đoán → bấm \"Tự kiểm tra\" (dịch thử sẽ đi qua Google/MyMemory thật); bấm \"Xuất JSON chẩn đoán\" → \"Lưu / Chia sẻ\" → \"Lưu vào Tệp\"; mở Trang Kiểm tra dịch → thử từng provider.",
  "",
];
fs.writeFileSync(EXP + "/analysis_c2_results.md", mdLines.join("\n") + "\n");
fs.writeFileSync(EXP + "/analysis_c2_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
