// verify_item6.mjs — Kiểm chứng MỤC 6: Service Worker precache Pyodide
// (offline từ lần thăm thứ 2) + lazy boot (chỉ boot khi user chọn file),
// shell tĩnh hiện ngay (repo MunKai0408/MeoRocky, KHÔNG đẩy lên GitHub).
//
// Chạy: node verify_item6.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: item6_results.md + item6_results.json
//
//  A. Tĩnh + Node vm: node --check; shell tĩnh đo thật (raw + gzip);
//     danh sách precache trong sw.js khớp đúng đĩa (shell + Pyodide
//     vendored + pb3/files.json + font); không COI/SAB/pthreads dùng thật;
//     lazy boot trong vm (không Worker/boot lúc nạp app.js; ensureBoot
//     tạo worker + boot 1 lần; boot hỏng thử lại được; callWorker "run"
//     tự boot trước).
//  B. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local): lần thăm 1 chưa chọn file thì không tải
//     Pyodide/worker.js/pb3 (đo qua request thật + __PB3_BOOT_STATE);
//     SW đăng ký được, precache đủ 122 mục; chọn file -> boot tới
//     "Sẵn sàng — 20 plugin" + list được file mẫu; lần thăm 2 OFFLINE
//     (context.setOffline(true)) vẫn boot tới Sẵn sàng + list được;
//     SW không phục vụ request dịch (fetch khác origin bị từ chối khi
//     offline, không có trong cache); SW đăng ký lỗi (chặn sw.js) thì
//     trang vẫn chạy + boot bình thường; đổi CACHE_VERSION -> cache cũ
//     bị xóa, bản mới được dùng; 390px không tràn ngang, không pageerror.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import { execSync, execFileSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const PB3 = "/home/hatch/workspace/pb3_fix/pb3";
const FLAT = EXP + "/fixtures/item5_flat.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- Fixture nhỏ (như item5): ZIP phẳng có data/en.json ----
if (!fs.existsSync(FLAT)) {
  execSync("python3 -c \"import zipfile,json; z=zipfile.ZipFile('" + FLAT + "','w'); " +
    "z.writestr('data/en.json', json.dumps({'hello':'Hello','play':'Play'}, ensure_ascii=False)); " +
    "z.writestr('data/big-stored.txt', 'Xin chào BearTea! '*200, compress_type=zipfile.ZIP_STORED); z.close()\"");
  console.log("built " + FLAT);
}

// ================= A. Tĩnh =================
const swSrc = fs.readFileSync(SITE + "/sw.js", "utf8");
const appSrc = fs.readFileSync(SITE + "/app.js", "utf8");
const verMatch = swSrc.match(/const CACHE_VERSION = "([^"]+)"/);
const CACHE_VERSION = verMatch ? verMatch[1] : null;
const CACHE_NAME = CACHE_VERSION ? "beartea-" + CACHE_VERSION : null;

{ // A1. node --check các file JS chạm tới
  const t0 = performance.now();
  const files = ["sw.js", "app.js", "worker.js", "worker-pool.js", "stream.js", "kb-store.js", "gt.js"];
  const bad = [];
  for (const f of files) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  rec("static_node_check", bad.length === 0 && !!CACHE_VERSION,
    "node --check OK: " + files.join(", ") + (bad.length ? " | LỖI: " + bad.join(", ") : "") +
    "; CACHE_VERSION=" + CACHE_VERSION,
    performance.now() - t0);
}

const SHELL_FILES = ["index.html", "kb-store.js", "worker-pool.js", "stream.js", "app.js", "gt.js"];
let shellRaw = 0, shellGzip = 0;
const shellLines = [];
for (const f of SHELL_FILES) {
  const buf = fs.readFileSync(SITE + "/" + f);
  const gz = zlib.gzipSync(buf, { level: 9 }).length;
  shellRaw += buf.length; shellGzip += gz;
  shellLines.push(f + " raw=" + buf.length + "B gzip=" + gz + "B");
}
{ // A2a. Shell tĩnh gzip <100KB (số thật, đo từ đĩa)
  const t0 = performance.now();
  rec("shell_gzip_under_100kb", shellGzip < 100 * 1024,
    "shell lần tải đầu (index.html + 5 JS giao diện, KHÔNG tính Pyodide): " +
    shellLines.join("; ") + " | TỔNG gzip=" + shellGzip + "B (" + (shellGzip / 1024).toFixed(1) +
    "KB) < 100KB; index.html riêng raw=" + fs.statSync(SITE + "/index.html").size +
    "B (" + (fs.statSync(SITE + "/index.html").size / 1024).toFixed(1) + "KB) < 100KB",
    performance.now() - t0);
}
{ // A2b. Shell tĩnh raw (không nén) — GHI RÕ: chưa đạt 100KB
  const t0 = performance.now();
  rec("shell_raw_under_100kb", shellRaw < 100 * 1024,
    "TỔNG raw=" + shellRaw + "B (" + (shellRaw / 1024).toFixed(1) + "KB) > 100KB — KHÔNG ĐẠT " +
    "ngưỡng raw. Nguyên nhân: app.js là monolith " + fs.statSync(SITE + "/app.js").size +
    "B (handler cả 8 tab) + index.html " + fs.statSync(SITE + "/index.html").size +
    "B (CSS inline + HTML 8 tab). Tách app.js thành chunk lazy theo tab mới đạt ngưỡng raw, " +
    "nhưng đó là refactor lớn chạm mọi harness (17 harness nạp app.js trực tiếp) nên " +
    "để làm riêng sau mục 5–9 + audit; trên dây (gzip, GitHub Pages nén) shell chỉ " +
    (shellGzip / 1024).toFixed(1) + "KB và lần thăm 2 SW phục vụ từ cache nên không tốn mạng.",
    performance.now() - t0);
}

{ // A3. Danh sách precache trong sw.js khớp đúng đĩa
  const t0 = performance.now();
  const listMatch = swSrc.match(/const PRECACHE_URLS = \[([\s\S]*?)\];/);
  const listed = listMatch ? [...listMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
  const expected = ["./", ...SHELL_FILES, "worker.js", "manifest.webmanifest", "diagnostics.html",
    "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png",
    "fonts/BeVietnamPro-Regular.ttf.b64",
    ...fs.readdirSync(SITE + "/pyodide").sort().map((f) => "pyodide/" + f),
    "pb3/files.json",
    ...JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8")).files];
  const missing = expected.filter((f) => !listed.includes(f));
  const extra = listed.filter((f) => !expected.includes(f));
  const notOnDisk = listed.filter((f) => f !== "./" && !fs.existsSync(SITE + "/" + f));
  const totalBytes = listed.filter((f) => f !== "./")
    .reduce((s, f) => s + fs.statSync(SITE + "/" + f).size, 0);
  const core = ["pyodide/pyodide.mjs", "pyodide/pyodide.asm.mjs", "pyodide/pyodide.asm.wasm",
    "pyodide/python_stdlib.zip", "pyodide/pyodide-lock.json",
    "pyodide/lz4-4.4.5-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
    "pb3/files.json", "pb3/bridge.py", "index.html", "app.js", "worker.js"];
  const coreMissing = core.filter((f) => !listed.includes(f));
  rec("sw_precache_list_matches_disk", missing.length === 0 && extra.length === 0 &&
    notOnDisk.length === 0 && coreMissing.length === 0,
    "precache " + listed.length + " mục (kỳ vọng " + expected.length + ", gồm './' trỏ scope root), " +
    "tổng " + totalBytes + "B (" + (totalBytes / 1048576).toFixed(1) + "MiB: Pyodide ~16,5MB + PB3 ~1,4MB + " +
    "shell ~0,24MB + font 0,18MB); thiếu so với đĩa: " + (missing.join(", ") || "không có") +
    "; thừa: " + (extra.join(", ") || "không có") + "; không có trên đĩa: " +
    (notOnDisk.join(", ") || "không có") + "; file lõi thiếu: " + (coreMissing.join(", ") || "không có"),
    performance.now() - t0);
}

{ // A4. Không COI/SAB/pthreads dùng thật; SW chỉ chặn GET cùng origin trong danh sách
  const t0 = performance.now();
  const workerSrc = fs.readFileSync(SITE + "/worker.js", "utf8");
  const poolSrc = fs.readFileSync(SITE + "/worker-pool.js", "utf8");
  const all = swSrc + appSrc + workerSrc + poolSrc;
  const bad = [];
  if (/new\s+SharedArrayBuffer/.test(all)) bad.push("new SharedArrayBuffer");
  if (/Cross-Origin-Opener-Policy|Cross-Origin-Embedder-Policy/.test(all)) bad.push("COOP/COEP header");
  if (/require\s*\(\s*["']worker_threads["']|new\s+Worker\(.*pthreads|SharedArrayBuffer\s*\(/.test(all)) bad.push("pthreads/SAB dùng thật");
  const swGuards = /req\.method !== "GET"/.test(swSrc) &&
    /url\.origin !== self\.location\.origin/.test(swSrc) &&
    /headers\.has\("range"\)/.test(swSrc) &&
    /PRECACHE_SET\.has\(req\.url\)/.test(swSrc) &&
    /skipWaiting/.test(swSrc) &&
    /caches\.delete/.test(swSrc) &&
    /GET_VERSION/.test(swSrc);
  rec("static_no_coi_passthrough", bad.length === 0 && swGuards,
    "dùng thật bị cấm: " + (bad.length ? bad.join(", ") : "không có") +
    " (chữ COI/SAB/pthreads chỉ còn trong comment 'KHÔNG dùng'); SW có đủ guard: " +
    "chỉ GET + cùng origin + không Range + chỉ URL precache + skipWaiting + xóa cache cũ + GET_VERSION = " +
    (swGuards ? "đủ" : "THIẾU"),
    performance.now() - t0);
}

// ================= A5/A6. Lazy boot trong Node vm (DOM giả) =================
function loadAppVm(bootBehavior) {
  // bootBehavior: "ok" | "fail-once" — FakeWorker trả lời op boot.
  const elements = new Map();
  function makeEl(id) {
    return {
      id, textContent: "", innerHTML: "", hidden: false, value: "", checked: false,
      disabled: false, files: null, style: {}, _listeners: {},
      classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
      addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
      click() {}, remove() {}, scrollIntoView() {},
    };
  }
  const $ = (id) => { if (!elements.has(id)) elements.set(id, makeEl(id)); return elements.get(id); };
  const fakeWorkers = [];
  const posted = [];
  let bootAttempts = 0;
  class FakeWorker {
    constructor(url, opts) {
      this.url = url; this.opts = opts; this.onmessage = null; this.onerror = null;
      fakeWorkers.push(this);
    }
    postMessage(m, _t) {
      posted.push(m);
      if (m.op === "boot") {
        bootAttempts++;
        const id = m.id;
        const failThis = bootBehavior === "fail-once" && bootAttempts === 1;
        setTimeout(() => {
          if (!this.onmessage) return;
          if (failThis) this.onmessage({ data: { id, ok: false, error: "boot boom" } });
          else this.onmessage({ data: { id, ok: true, result: JSON.stringify({
            boot: { ok: true, plugins: 20, problems: [] }, donor: { is_full: true } }) } });
        }, 0);
      } else if (m.op === "run") {
        const id = m.id;
        setTimeout(() => {
          if (this.onmessage) this.onmessage({ data: { id, ok: true,
            result: JSON.stringify({ ok: true, games: [], size_bytes: 0, entries: [] }) } });
        }, 0);
      }
      // set_translate_proxy: không id, không cần trả lời.
    }
  }
  const sandbox = {
    document: { getElementById: $, createElement: () => makeEl("a"),
      body: { appendChild() {}, removeChild() {} } },
    Worker: FakeWorker,
    performance, setTimeout, clearTimeout, setInterval, clearInterval, console,
    localStorage: { _m: {}, getItem(k) { return this._m[k] || null; }, setItem(k, v) { this._m[k] = String(v); },
      removeItem(k) { delete this._m[k]; } },
    confirm: () => true, Blob, URL, TextEncoder, TextDecoder, atob, btoa,
  };
  sandbox.window = sandbox;
  sandbox.addEventListener = () => {};
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SITE + "/app.js", "utf8"), sandbox, { filename: "app.js" });
  return { ctx: sandbox, $, fakeWorkers, posted, bootAttempts: () => bootAttempts };
}
async function waitForVm(fn, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) { if (fn()) return true; await sleep(50); }
  throw new Error("hết giờ chờ " + label);
}

{ // A5a. Nạp app.js: KHÔNG tạo Worker, KHÔNG post boot, status "Chưa khởi động"
  const t0 = performance.now();
  const t = loadAppVm("ok");
  const bootState = t.ctx.__PB3_BOOT_STATE();
  const ok = t.fakeWorkers.length === 0 && t.posted.length === 0 &&
    bootState.booted === false && bootState.workerCreated === false &&
    t.$("status").textContent.includes("Chưa khởi động");
  rec("vm_no_boot_at_load", ok,
    "sau khi nạp app.js: Worker đã tạo=" + t.fakeWorkers.length + ", message đã post=" +
    t.posted.length + ", __PB3_BOOT_STATE=" + JSON.stringify(bootState) +
    ", #status=" + JSON.stringify(t.$("status").textContent),
    performance.now() - t0);
  // A5b. ensureBoot(): tạo đúng 1 worker, post boot, tới "Sẵn sàng — 20 plugin"
  const t1 = performance.now();
  let okB = false, detailB = "";
  try {
    const p = t.ctx.ensureBoot();
    await sleep(50);
    const createdOnce = t.fakeWorkers.length === 1 &&
      t.posted.filter((m) => m.op === "boot").length === 1;
    await p;
    await waitForVm(() => t.$("status").textContent.includes("Sẵn sàng"), 5000, "Sẵn sàng (vm)");
    // Gọi ensureBoot lần nữa: không tạo worker mới, không post boot nữa.
    await t.ctx.ensureBoot();
    await sleep(50);
    const stillOnce = t.fakeWorkers.length === 1 &&
      t.posted.filter((m) => m.op === "boot").length === 1;
    okB = createdOnce && stillOnce && t.$("status").textContent.includes("20 plugin") &&
      t.ctx.__PB3_BOOT_STATE().booted === true;
    detailB = "ensureBoot lần 1: worker=" + t.fakeWorkers.length + ", boot đã post=" +
      t.posted.filter((m) => m.op === "boot").length + "; status=" +
      JSON.stringify(t.$("status").textContent) + "; gọi lại: worker vẫn " +
      t.fakeWorkers.length + ", boot vẫn " + t.posted.filter((m) => m.op === "boot").length +
      " (không boot đúp)";
  } catch (e) { detailB = "lỗi: " + String(e && e.message ? e.message : e); }
  rec("vm_lazy_boot_once", okB, detailB, performance.now() - t1);
  // A5c. callWorker "run" khi chưa boot: tự boot trước rồi mới chạy op
  const t2 = performance.now();
  const t3 = loadAppVm("ok");
  let okC = false, detailC = "";
  try {
    const p = t3.ctx.callWorker("run", { fn: "list_entries", args: ["x"], files: [] });
    await sleep(50);
    const order = t3.posted.map((m) => m.op);
    const msg = await p;
    // getWorker gửi set_translate_proxy + set_libretranslate ngay khi tạo
    // worker, rồi mới boot, rồi mới chạy op run; boot đúng 1 lần. (C1 thêm
    // set_libretranslate vào bước gửi cấu hình lúc tạo worker.)
    okC = order[0] === "set_translate_proxy" && order[1] === "set_libretranslate" &&
      order[2] === "boot" &&
      order.indexOf("run") > order.indexOf("boot") &&
      order.filter((o) => o === "boot").length === 1 && msg.ok === true &&
      t3.ctx.__PB3_BOOT_STATE().booted === true;
    detailC = "thứ tự message đã post: " + JSON.stringify(order) +
      "; callWorker run resolve ok=" + msg.ok + "; booted=" + t3.ctx.__PB3_BOOT_STATE().booted;
  } catch (e) { detailC = "lỗi: " + String(e && e.message ? e.message : e); }
  rec("vm_callworker_bootstraps", okC, detailC, performance.now() - t2);
  // A5d. Boot hỏng lần 1 -> thử lại được (không kẹt bootPromise)
  const t4 = performance.now();
  const t5 = loadAppVm("fail-once");
  let okD = false, detailD = "";
  try {
    let err1 = null;
    try { await t5.ctx.ensureBoot(); } catch (e) { err1 = e; }
    const afterFail = t5.$("status").textContent;
    await t5.ctx.ensureBoot();
    await waitForVm(() => t5.$("status").textContent.includes("Sẵn sàng"), 5000, "Sẵn sàng sau thử lại");
    okD = !!err1 && afterFail.includes("Khởi động lỗi") &&
      t5.bootAttempts() === 2 && t5.ctx.__PB3_BOOT_STATE().booted === true;
    detailD = "lần 1 lỗi: " + (err1 ? JSON.stringify(String(err1.message).slice(0, 80)) : "KHÔNG LỖI (sai)") +
      "; status sau lần 1: " + JSON.stringify(afterFail) + "; số lần boot đã post: " +
      t5.bootAttempts() + "; lần 2 tới Sẵn sàng, booted=" + t5.ctx.__PB3_BOOT_STATE().booted;
  } catch (e) { detailD = "lỗi: " + String(e && e.message ? e.message : e); }
  rec("vm_boot_retry_after_fail", okD, detailD, performance.now() - t4);
}

{ // A6. Đăng ký SW thất bại thì im lặng (tĩnh: try/catch + .catch trong app.js)
  const t0 = performance.now();
  const fn = appSrc.match(/function registerServiceWorker\(\) \{[\s\S]*?\n\}/);
  const ok = !!fn && /try \{/.test(fn[0]) && /catch\(\(\) =>/.test(fn[0]) &&
    /catch \(e\) \{/.test(fn[0]) && /"serviceWorker" in navigator/.test(fn[0]) &&
    /__PB3_NO_SW/.test(fn[0]);
  rec("static_sw_register_silent_fail", ok,
    "registerServiceWorker có: guard \"serviceWorker\" in navigator + try/catch + " +
    ".catch(()=>{}) im lặng + hook __PB3_NO_SW; " + (fn ? "tìm thấy hàm" : "KHÔNG tìm thấy hàm"),
    performance.now() - t0);
}

// ================= B. Trình duyệt thật 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain" };
function makeServer(root) {
  return http.createServer((req, res) => {
    const urlPath = decodeURIComponent(req.url.split("?")[0]);
    const fp = path.join(root, urlPath === "/" ? "index.html" : urlPath);
    if (!fp.startsWith(root)) { res.writeHead(403); res.end(); return; }
    fs.readFile(fp, (err, data) => {
      if (err) { res.writeHead(404); res.end("not found"); return; }
      res.writeHead(200, { "Content-Type": MIME[path.extname(fp)] || "application/octet-stream" });
      res.end(data);
    });
  });
}
const server = makeServer(SITE);
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
const listMatchB = swSrc.match(/const PRECACHE_URLS = \[([\s\S]*?)\];/);
const listedCount = listMatchB ? [...listMatchB[1].matchAll(/"([^"]+)"/g)].length : 0;

let browser = null;
const pageErrors = [];
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  const reqUrls = [];
  page.on("request", (r) => reqUrls.push(r.url()));
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text()); });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const bootState = () => page.evaluate(() => (typeof window.__PB3_BOOT_STATE === "function"
    ? window.__PB3_BOOT_STATE() : null));
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const selectFile = async (file) => {
    const name = file.split("/").pop();
    await page.setInputFiles("#file-input", file);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      name, { timeout: 120000 });
  };
  const openListTab = async () => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-list");
    await page.click("#btn-list-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("list-result"); return el && el.textContent.includes("entry"); },
      null, { timeout: 60000 });
  };

  { // B1. Lần thăm 1: chưa chọn file -> không tải Pyodide; SW đăng ký + precache đủ
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await sleep(2000); // shell hiện + SW bắt đầu đăng ký/precache nền
    const status = await elText("status");
    const bs = await bootState();
    const earlyPy = reqUrls.filter((u) => u.includes("/pyodide/") || u.includes("worker.js") || u.includes("/pb3/"));
    let swOk = false, cacheDetail = "", ctrl = false;
    try {
      await page.waitForFunction(
        () => !!(navigator.serviceWorker && navigator.serviceWorker.controller),
        null, { timeout: 120000 });
      ctrl = true;
      // Chờ precache đủ số mục (poll Cache Storage).
      let keys = [];
      for (let i = 0; i < 60; i++) {
        keys = await page.evaluate(async (name) => {
          const c = await caches.open(name);
          return (await c.keys()).map((r) => r.url);
        }, CACHE_NAME);
        if (keys.length >= listedCount) break;
        await sleep(2000);
      }
      const need = ["pyodide/pyodide.asm.wasm", "pyodide/python_stdlib.zip", "pyodide/pyodide.mjs",
        "pb3/bridge.py", "pb3/files.json", "index.html", "app.js", "worker.js",
        "fonts/BeVietnamPro-Regular.ttf.b64"];
      const missingKeys = need.filter((n) => !keys.some((u) => u.endsWith("/" + n)));
      swOk = keys.length >= listedCount && missingKeys.length === 0;
      cacheDetail = "cache " + CACHE_NAME + ": " + keys.length + "/" + listedCount + " mục" +
        (missingKeys.length ? "; thiếu key: " + missingKeys.join(", ") : "; đủ key lõi (asm.wasm, stdlib, pyodide.mjs, bridge.py, files.json, index.html, app.js, worker.js, font)");
    } catch (e) { cacheDetail = "chờ SW/precache lỗi: " + String(e && e.message ? e.message : e); }
    const ok = status.includes("Chưa khởi động") && bs && bs.booted === false &&
      bs.workerCreated === false && earlyPy.length === 0 && swOk && ctrl;
    rec("browser_visit1_lazy_no_pyodide_sw_precache", ok,
      "status sau tải trang: " + JSON.stringify(status) + "; __PB3_BOOT_STATE=" + JSON.stringify(bs) +
      "; request Pyodide/worker.js/pb3 trước khi chọn file: " + (earlyPy.join(", ") || "KHÔNG CÓ") +
      " (tổng request lúc đó: " + reqUrls.length + "); SW controlling=" + ctrl + "; " + cacheDetail,
      performance.now() - t0);
  }

  { // B2. Chọn file -> boot tới Sẵn sàng + list được file mẫu (lần thăm 1, online)
    const t0 = performance.now();
    await selectFile(FLAT);
    await waitReady();
    const status = await elText("status");
    await openListTab();
    const listTxt = await elText("list-result");
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    rec("browser_visit1_boot_and_list", status.includes("Sẵn sàng") && status.includes("20 plugin") &&
      listTxt.includes("data/en.json") && overflow.sw <= overflow.cw,
      "status: " + JSON.stringify(status) + "; list-result có data/en.json: " + listTxt.includes("data/en.json") +
      "; scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw + " (không tràn ngang)",
      performance.now() - t0);
  }

  { // B3. Lần thăm 2 OFFLINE: reload -> shell từ cache -> boot tới Sẵn sàng + list được
    const t0 = performance.now();
    await ctx.setOffline(true);
    await page.reload();
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
      null, { timeout: 60000 });
    const bsOff = await bootState();
    await selectFile(FLAT);
    await waitReady();
    const status = await elText("status");
    await openListTab();
    const listTxt = await elText("list-result");
    // SW không phục vụ request dịch: khác origin, offline phải bị từ chối (không có trong cache).
    const probe = await page.evaluate(async () => {
      const m = await caches.match("https://example.invalid/pb3-translate-probe");
      let fetchRes = "";
      try { await fetch("https://example.invalid/pb3-translate-probe"); fetchRes = "served?!"; }
      catch (e) { fetchRes = "rejected:" + e.name; }
      return { cacheMatch: m ? "HIT?!" : "miss", fetchRes };
    });
    await ctx.setOffline(false);
    const ok = bsOff && bsOff.booted === false && status.includes("Sẵn sàng") &&
      status.includes("20 plugin") && listTxt.includes("data/en.json") &&
      probe.cacheMatch === "miss" && probe.fetchRes.startsWith("rejected");
    rec("browser_visit2_offline_boot_and_list", ok,
      "offline reload: status về " + JSON.stringify("Chưa khởi động") + ", booted=" + (bsOff && bsOff.booted) +
      "; chọn file offline -> status: " + JSON.stringify(status) + "; list có data/en.json: " +
      listTxt.includes("data/en.json") + "; probe dịch khác origin khi offline: cache=" +
      probe.cacheMatch + ", fetch=" + probe.fetchRes + " (SW không chặn/cache request dịch)",
      performance.now() - t0);
  }

  { // B4. SW đăng ký lỗi (sw.js hỏng cú pháp) -> trang vẫn chạy + boot bình thường
    // Lưu ý: page.route KHÔNG chặn được request tải script SW (browser tự
    // tải, ngoài tầm route của Playwright — đã kiểm chứng: route abort
    // nhưng SW vẫn đăng ký được), nên mô phỏng lỗi đăng ký bằng bản sao
    // site có sw.js hỏng cú pháp: register() phải reject.
    const t0 = performance.now();
    const TMP2 = "/tmp/item6_site_broken_sw";
    fs.rmSync(TMP2, { recursive: true, force: true });
    fs.cpSync(SITE, TMP2, { recursive: true });
    fs.writeFileSync(TMP2 + "/sw.js", "// sw.js hỏng cố ý (kiểm chứng fallback)\nthis is {{{ not valid javascript\n");
    const server3 = makeServer(TMP2);
    await new Promise((r) => server3.listen(0, "127.0.0.1", r));
    const PORT3 = server3.address().port;
    const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page2 = await ctx2.newPage();
    const errs2 = [];
    page2.on("pageerror", (e) => errs2.push(String(e && e.message ? e.message : e)));
    let status0 = "", swState = null, status = "";
    try {
      await page2.goto("http://127.0.0.1:" + PORT3 + "/index.html");
      await sleep(2000);
      status0 = await page2.evaluate(() => (document.getElementById("status") || {}).textContent || "");
      swState = await page2.evaluate(async () => {
        const reg = await navigator.serviceWorker.getRegistration().catch(() => null);
        return { hasReg: !!reg, controller: !!navigator.serviceWorker.controller };
      });
      await page2.setInputFiles("#file-input", FLAT);
      await page2.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 240000 });
      status = await page2.evaluate(() => (document.getElementById("status") || {}).textContent || "");
    } finally {
      await ctx2.close().catch(() => {});
      server3.close();
      fs.rmSync(TMP2, { recursive: true, force: true });
    }
    rec("browser_sw_register_fail_fallback", status0.includes("Chưa khởi động") &&
      swState && swState.hasReg === false && status.includes("Sẵn sàng") && errs2.length === 0,
      "sw.js hỏng cú pháp: status ban đầu " + JSON.stringify(status0) + "; SW registration: " +
      JSON.stringify(swState) + " (cần hasReg=false = đăng ký lỗi, bắt im lặng); chọn file vẫn boot tới " +
      JSON.stringify(status) + "; pageerror: " + (errs2.join("; ") || "không có"),
      performance.now() - t0);
  }

  { // B5. Đổi CACHE_VERSION -> cache cũ bị xóa, bản mới được dùng
    const t0 = performance.now();
    const TMP = "/tmp/item6_site_v7";
    const NEW_VER = "v7-20261116-test";
    fs.rmSync(TMP, { recursive: true, force: true });
    fs.cpSync(SITE, TMP, { recursive: true });
    fs.writeFileSync(TMP + "/sw.js",
      fs.readFileSync(TMP + "/sw.js", "utf8").replace(
        'const CACHE_VERSION = "' + CACHE_VERSION + '"',
        'const CACHE_VERSION = "' + NEW_VER + '"'));
    fs.writeFileSync(TMP + "/index.html",
      fs.readFileSync(TMP + "/index.html", "utf8").replace("</body>", "<!-- sw-v7-marker --></body>"));
    const server2 = makeServer(TMP);
    await new Promise((r) => server2.listen(0, "127.0.0.1", r));
    const PORT2 = server2.address().port;
    const ctx5 = await browser.newContext({ viewport: { width: 390, height: 844 } });
    let keys = [], hasMarker = false, detail5 = "";
    try {
      // Trang gieo cache cũ (tắt SW bằng hook để chưa đăng ký bản mới).
      const pageA = await ctx5.newPage();
      await pageA.addInitScript(() => { window.__PB3_NO_SW = true; });
      await pageA.goto("http://127.0.0.1:" + PORT2 + "/index.html");
      await pageA.evaluate(async (oldName) => {
        const c = await caches.open(oldName);
        await c.put("/old-marker", new Response("old"));
      }, CACHE_NAME);
      await pageA.close();
      // Trang thật: đăng ký SW v7 -> activate phải xóa cache v6.
      const pageB = await ctx5.newPage();
      await pageB.goto("http://127.0.0.1:" + PORT2 + "/index.html");
      await pageB.waitForFunction(
        () => !!(navigator.serviceWorker && navigator.serviceWorker.controller),
        null, { timeout: 120000 });
      await pageB.reload();
      await pageB.waitForFunction(
        () => !!(navigator.serviceWorker && navigator.serviceWorker.controller),
        null, { timeout: 120000 });
      keys = await pageB.evaluate(async () => await caches.keys());
      hasMarker = await pageB.evaluate(() => document.documentElement.outerHTML.includes("sw-v7-marker"));
      detail5 = "caches.keys sau khi SW v7 kích hoạt: " + JSON.stringify(keys) +
        "; trang có marker v7: " + hasMarker;
    } finally {
      await ctx5.close().catch(() => {});
      server2.close();
      fs.rmSync(TMP, { recursive: true, force: true });
    }
    rec("browser_cache_version_switch", keys.includes("beartea-" + NEW_VER) &&
      !keys.includes(CACHE_NAME) && hasMarker,
      detail5 + " (cần: có bear tea-" + NEW_VER + ", KHÔNG còn " + CACHE_NAME + ", marker bản mới hiện)",
      performance.now() - t0);
  }

  { // B6. 390px: không tràn ngang + không lỗi trang cả phiên
    // (lọc "Failed to load resource" — đó là tiếng ồn mạng của probe
    // fetch offline cố ý ở B3, không phải lỗi JS của trang).
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const realErrors = pageErrors.filter((e) => !e.includes("Failed to load resource"));
    rec("browser_390_no_overflow_no_errors", overflow.sw <= overflow.cw && realErrors.length === 0,
      "scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      "; pageerror/console-error thật cả phiên (thăm 1 + offline thăm 2): " + (realErrors.join("; ") || "không có") +
      (realErrors.length === pageErrors.length ? "" : " (đã lọc " + (pageErrors.length - realErrors.length) + " dòng Failed to load resource do probe offline cố ý)"),
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const failed = Object.keys(results).filter((k) => !results[k].ok);
const md = "# Mục 6 — Service Worker precache Pyodide + lazy boot (kết quả harness)\\n\\n" +
  "Ngày kiểm chứng: " + new Date().toISOString() + " (giờ UTC). Chạy: `node verify_item6.mjs`.\\n\\n" +
  "## Kết quả: " + (total - failed.length) + "/" + total + " PASS" + (failed.length ? " — FAIL: " + failed.join(", ") : "") + "\\n\\n" +
  Object.entries(results).map(([k, v]) => "### " + k + ": " + (v.ok ? "PASS" : "FAIL") + (v.seconds != null ? " (" + v.seconds + "s)" : "") + "\\n" + v.detail + "\\n").join("\\n") +
  "\\n## Ghi chú\\n\\n" +
  "- Shell tĩnh: gzip " + shellGzip + "B (" + (shellGzip / 1024).toFixed(1) + "KB) < 100KB; raw " + shellRaw + "B (" + (shellRaw / 1024).toFixed(1) + "KB) > 100KB — xem `shell_raw_under_100kb` (tách app.js monolith là việc riêng sau audit).\\n" +
  "- Precache: " + listedCount + " mục, CACHE_VERSION=" + CACHE_VERSION + " (" + CACHE_NAME + "). Đổi version khi deploy bản mới.\\n" +
  "- Xem phân tích đầy đủ (file đổi, overlap C3/C12, chờ Joseph kiểm chứng Safari thật) trong `exp/item6_results.md` (báo cáo tay).\\n";
fs.writeFileSync(EXP + "/item6_results.json", JSON.stringify({ total, failures: failed, results }, null, 2));
fs.writeFileSync(EXP + "/item6_results.md", md);
console.log("\\n==== TỔNG: " + total + " mục, " + failed.length + " FAIL ====");
if (failed.length) { console.log("FAIL: " + failed.join(", ")); process.exit(1); }
