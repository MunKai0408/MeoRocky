// verify_analysis_c9.mjs — Kiểm chứng MỤC C9: worker pool batch cấu hình
// được (1–2, mặc định 2, tự động 1 khi RAM ≤ 4 GB, lưu localStorage) +
// sửa câu sai về song song (pool song song THẬT: mỗi worker 1 thread hệ
// điều hành thật; chỉ bên trong 1 worker thì Python tuần tự vì Pyodide
// không có pthreads). (repo MunKai0408/MeoRocky, nhánh main; KHÔNG đẩy
// lên GitHub.)
//
// Chạy: node verify_analysis_c9.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c9_results.json (+ analysis_c9_results.md báo cáo tay).
//
//  A. Tĩnh: node --check (app.js, sw.js, worker-pool.js) +
//     check_analysis_c9_static.py (N cấu hình được, chuỗi cũ đã hết,
//     files.json=105 / tests.json=186, CACHE_VERSION v23).
//  B. Unit WorkerPool thật (site/worker-pool.js) với Worker GIẢ trong Node:
//     B1 size 1 -> đúng 1 Worker, 3 job chậm chạy TUẦN TỰ trên slot 0;
//     B2 size 2 -> đúng 2 Worker, 2 job chậm chạy CHỒNG NHAU (song song);
//     B3 size 1 + runOn(1) -> rơi về slot 0, không treo (bảo vệ C9);
//     B4 hủy pool 2 rồi tạo pool 1 -> đúng 1 Worker mới.
//  C. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local), tab Batch:
//     D0 Boot ("Sẵn sàng — 20 plugin").
//     D1 Mặc định N=2 (không lựa chọn lưu, không hook RAM): ô chọn = "2",
//        ghi chú "Mặc định 2 worker", __PB3_POOL_SIZE = 2.
//     D2 Batch 2 game N=2: "pool 2 worker", worker 0: 1 game · worker 1:
//        1 game, pool-info hiện cả Worker 0 và Worker 1, tải batch.zip.
//     D3 Đổi N sang 1 lúc rảnh: ghi chú "Đã lưu 1 worker", pool cũ bị hủy
//        (pool-info về "Pool chưa khởi động ... pool 1 worker"), batch 2
//        game -> "pool 1 worker", worker 0: 2 game, không có worker 1.
//     D4 Lưu qua reload: reload -> ô chọn vẫn "1"; đổi "2", reload -> "2".
//     D5 Đổi N GIỮA CHỪNG (batch 4 game bigtest đang chạy): không crash,
//        không pageerror, đợt đang chạy giữ nguyên "pool 2 worker", đợt
//        sau dùng "pool 1 worker".
//     D6 RAM thấp tự động về 1 (context mới, __PB3_DEVICE_MEMORY=4, không
//        lựa chọn lưu): ô chọn = "1", ghi chú "tự động ... 4 GB", batch 2
//        game -> "pool 1 worker", worker 0: 2 game.
//     D7 390px không tràn ngang (phiên chính).
//     D8 Không pageerror cả phiên chính.
//  S. Vỏ trang sau C9 (ghi số thật, không đặt ngưỡng mới — FAIL item6 đã biết).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import { execFileSync, execSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const GAME1 = SITE + "/pb3/tests/fixtures/extract/game1.zip";
const BIG = EXP + "/fixtures/bigtest.zip";

// Fixture bigtest.zip (4000 file text, extract ~2s rồi E_NO_CANDIDATE):
// dùng cho D5 (đổi N giữa chừng khi batch đang chạy) — tạo lại nếu chưa có,
// y hệt verify_item4_pool.mjs (file này KHÔNG commit lên repo — xem
// exp/item4_report.md: "Chỉ local, không đẩy"). Trước đây c9 crash ENOENT
// ở setInputFiles khi chạy trên clone mới (chưa từng chạy item4).
if (!fs.existsSync(BIG)) {
  fs.mkdirSync(path.dirname(BIG), { recursive: true });
  execSync("python3 -c \"import zipfile\nwith zipfile.ZipFile('" + BIG + "','w',zipfile.ZIP_DEFLATED) as z:\n    [z.writestr('assets/text%04d.txt' % i, ('hello world %d\\n' % i) * 40) for i in range(4000)]\n\"");
  console.log("created fixture " + BIG + " (" + fs.statSync(BIG).size + " bytes)");
}
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= A. Tĩnh =================
{ // A1. node --check
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js", "worker-pool.js"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  rec("static_node_check", bad.length === 0,
    "node --check OK: app.js, sw.js, worker-pool.js" + (bad.length ? " | LỖI: " + bad.join(", ") : ""),
    performance.now() - t0);
}
{ // A2. Kiểm tra tĩnh chuyên mục C9
  const t0 = performance.now();
  const r = spawnSync("python3", [EXP + "/check_analysis_c9_static.py"], { encoding: "utf8" });
  let detail = (r.stdout || "").trim();
  let ok = r.status === 0;
  try {
    const j = JSON.parse(r.stdout || "{}");
    if (j.fails && j.fails.length) { ok = false; detail = j.fails.join(" | "); }
    else detail = "check_analysis_c9_static.py xanh: N cấu hình được 1–2 " +
      "(mặc định 2, tự động 1 khi deviceMemory ≤ 4, localStorage " +
      "pb3_pool_size, pool tôn trọng N, chuỗi cũ đã hết) + files.json=105 / " +
      "tests.json=186 + CACHE_VERSION v59-20261119 + tiếng Việt đủ dấu";
  } catch (e) { ok = false; detail = "không đọc được JSON: " + (r.stdout || "") + (r.stderr || ""); }
  rec("static_c9_checks", ok, detail, performance.now() - t0);
}

// ================= B. Unit WorkerPool với Worker giả =================
const poolSrc = fs.readFileSync(SITE + "/worker-pool.js", "utf8");
let fakeWorkers = [];
class FakeWorker {
  constructor(url, opts) {
    this.url = url; this.opts = opts; this.terminated = false;
    this.onmessage = null; this.onerror = null;
    this.received = [];
    fakeWorkers.push(this);
  }
  postMessage(msg, _transfer) {
    if (this.terminated) return;
    this.received.push(msg);
    const self = this;
    if (msg.op === "boot") {
      setTimeout(() => {
        if (self.terminated) return;
        self.onmessage && self.onmessage({ data: { id: msg.id, ok: true, result: "{}" } });
      }, 5);
      return;
    }
    if (msg.op === "run") {
      const delay = msg.fn === "slow" ? 300 : 15;
      setTimeout(() => {
        if (self.terminated) return;
        self.onmessage && self.onmessage({ data: { id: msg.id, ok: true, result: JSON.stringify({ ok: true, echo: msg.fn }) } });
      }, delay);
      return;
    }
  }
  terminate() { this.terminated = true; }
}
const poolSandbox = { Worker: FakeWorker, setTimeout, clearTimeout, console, window: null };
poolSandbox.window = poolSandbox;
vm.createContext(poolSandbox);
const WorkerPool = vm.runInContext(poolSrc + "\n;WorkerPool;", poolSandbox);

{ // B1. size 1 -> đúng 1 Worker, 3 job chậm chạy TUẦN TỰ trên slot 0
  const t0 = performance.now();
  fakeWorkers = [];
  const pool = new WorkerPool("worker.js", { size: 1 });
  await pool.ready();
  const t1 = performance.now();
  const out = await Promise.all([pool.run({ fn: "slow" }), pool.run({ fn: "slow" }), pool.run({ fn: "slow" })]);
  const elapsed = performance.now() - t1;
  const ok = fakeWorkers.length === 1 && out.every((r) => r.slot === 0) && elapsed >= 800;
  rec("pool_size1_one_worker_sequential", ok,
    "size 1 -> Worker đã tạo: " + fakeWorkers.length + " (kỳ vọng 1); 3 job slow " +
    "đều slot 0: " + out.every((r) => r.slot === 0) + "; tổng thời gian " +
    Math.round(elapsed) + "ms (tuần tự 3×300ms, kỳ vọng ≥800ms)",
    performance.now() - t0);
  pool.destroy();
}
{ // B2. size 2 -> đúng 2 Worker, 2 job chậm chạy CHỒNG NHAU
  const t0 = performance.now();
  fakeWorkers = [];
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  const t1 = performance.now();
  const p1 = pool.run({ fn: "slow" }, { label: "s1" });
  const p2 = pool.run({ fn: "slow" }, { label: "s2" });
  await sleep(100);   // lúc này cả 2 job phải đang chạy cùng lúc
  const st = pool.status();
  const bothBusy = st.filter((s) => s.busy).length === 2;
  const out = await Promise.all([p1, p2]);
  const elapsed = performance.now() - t1;
  const slots = new Set(out.map((r) => r.slot));
  const ok = fakeWorkers.length === 2 && bothBusy && slots.size === 2 && elapsed < 700;
  rec("pool_size2_two_workers_parallel", ok,
    "size 2 -> Worker đã tạo: " + fakeWorkers.length + " (kỳ vọng 2); cùng lúc " +
    "busy: " + st.filter((s) => s.busy).length + "/2 slot; slot đã dùng: " +
    [...slots].join(",") + "; tổng thời gian " + Math.round(elapsed) +
    "ms (chồng nhau 300ms, kỳ vọng <700ms; tuần tự sẽ ~600ms+)",
    performance.now() - t0);
  pool.destroy();
}
{ // B3. size 1 + runOn(1) -> rơi về slot 0, không treo
  const t0 = performance.now();
  fakeWorkers = [];
  const pool = new WorkerPool("worker.js", { size: 1 });
  await pool.ready();
  const r = await Promise.race([
    pool.runOn(1, { fn: "pinned" }, { label: "g1" }),
    sleep(5000).then(() => { throw new Error("timeout: runOn(1) treo quá 5s"); }),
  ]);
  const w0Runs = fakeWorkers[0].received.filter((m) => m.op === "run").length;
  rec("pool_size1_runon_missing_slot_falls_back", r.slot === 0 && w0Runs === 1,
    "runOn(1) trên pool 1 worker -> reply slot=" + r.slot + " (kỳ vọng 0, " +
    "không treo); worker[0] nhận " + w0Runs + " job run",
    performance.now() - t0);
  pool.destroy();
}
{ // B4. Hủy pool 2 rồi tạo pool 1 -> đúng 1 Worker mới
  const t0 = performance.now();
  fakeWorkers = [];
  const p2 = new WorkerPool("worker.js", { size: 2 });
  await p2.ready();
  const created2 = fakeWorkers.length;
  p2.destroy();
  fakeWorkers = [];
  const p1 = new WorkerPool("worker.js", { size: 1 });
  await p1.ready();
  const created1 = fakeWorkers.length;
  const ok = created2 === 2 && created1 === 1 && p1.aliveCount() === 1;
  rec("pool_recreate_size_change", ok,
    "pool 2 tạo " + created2 + " Worker; hủy rồi tạo pool 1 -> " + created1 +
    " Worker mới, aliveCount=" + p1.aliveCount(),
    performance.now() - t0);
  p1.destroy();
}

// ================= C. Trình duyệt THẬT 390px =================
const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".css": "text/css", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain", ".cs": "text/plain" };
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
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("console", (m) => { if (m.type() === "error") pageErrors.push("console: " + m.text()); });
  const elText = (id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
  const poolSizeVal = () => page.evaluate(() => (document.getElementById("batch-pool-size") || {}).value || "");
  const poolSizeHook = () => page.evaluate(() => window.__PB3_POOL_SIZE);
  const deviceMemReal = () => page.evaluate(() => navigator.deviceMemory);
  const batchResultText = () => page.evaluate(() => (document.getElementById("batch-result") || {}).textContent || "");
  const poolInfoText = () => page.evaluate(() => (document.getElementById("batch-pool-info") || {}).textContent || "");
  const goBatchTab = async () => {
    await page.click("#btn-card-batch");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-batch"); return el && !el.hidden; },
      null, { timeout: 30000 });
  };
  const waitReady = async () => {
    await page.setInputFiles("#file-input", GAME1);
    await page.waitForFunction(
      (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
      "game1.zip", { timeout: 120000 });
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const runBatch2Games = async (manifest) => {
    await page.setInputFiles("#batch-files", [GAME1, GAME1]);
    await page.fill("#batch-manifest", JSON.stringify(manifest));
    const dlPromise = page.waitForEvent("download", { timeout: 300000 }).catch(() => null);
    await page.click("#btn-batch-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("/ 2 game"); },
      null, { timeout: 300000 });
    const dl = await dlPromise;
    return { res: await batchResultText(), dlName: dl ? dl.suggestedFilename() : null };
  };

  { // D0. Mở trang + boot
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    const status = await elText("status");
    const dm = await deviceMemReal();
    rec("browser_boot_ready", status.includes("Sẵn sàng") && status.includes("20 plugin"),
      "status: " + JSON.stringify(status) + "; navigator.deviceMemory thật trong " +
      "headless shell: " + String(dm) + " (> 4 GB nên mặc định 2, không ép về 1)",
      performance.now() - t0);
  }

  { // D1. Mặc định N=2
    const t0 = performance.now();
    await goBatchTab();
    const val = await poolSizeVal();
    const hook = await poolSizeHook();
    const note = await elText("batch-pool-size-note");
    rec("browser_default_pool_size_2", val === "2" && hook === 2 && note.includes("Mặc định 2 worker"),
      "ô chọn #batch-pool-size=" + JSON.stringify(val) + "; window.__PB3_POOL_SIZE=" +
      hook + "; ghi chú: " + JSON.stringify(note.slice(0, 120)) + "…",
      performance.now() - t0);
  }

  { // D2. Batch 2 game N=2: cả 2 worker đều làm
    const t0 = performance.now();
    const { res, dlName } = await runBatch2Games([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]);
    const poolTxt = await poolInfoText();
    rec("browser_batch_n2_both_workers", res.includes("pool 2 worker") &&
      res.includes("2 ok / 0 lỗi / 0 bỏ qua / 2 game") &&
      res.includes("worker 0: 1 game") && res.includes("worker 1: 1 game") &&
      poolTxt.includes("Worker 0:") && poolTxt.includes("Worker 1:") && dlName === "batch.zip",
      "kết quả: 2 ok qua pool 2 worker, phân công worker 0: 1 game · worker 1: 1 game; " +
      "#batch-pool-info hiện cả Worker 0 và Worker 1; download batch.zip: " + dlName,
      performance.now() - t0);
  }

  { // D3. Đổi N sang 1 lúc rảnh -> pool cũ hủy, batch sau chạy đúng 1 worker
    const t0 = performance.now();
    const errsBefore = pageErrors.length;
    await page.selectOption("#batch-pool-size", "1");
    const note = await elText("batch-pool-size-note");
    const poolTxt = await poolInfoText();
    const hook = await poolSizeHook();
    const { res } = await runBatch2Games([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]);
    const poolTxt2 = await poolInfoText();
    rec("browser_switch_to_n1_idle", note.includes("Đã lưu 1 worker") &&
      poolTxt.includes("pool 1 worker") && hook === 1 &&
      res.includes("pool 1 worker") && res.includes("2 ok / 0 lỗi / 0 bỏ qua / 2 game") &&
      res.includes("worker 0: 2 game") && !res.includes("worker 1:") &&
      !poolTxt2.includes("Worker 1:") && pageErrors.length === errsBefore,
      "đổi sang 1: ghi chú " + JSON.stringify(note.slice(0, 80)) + "…; pool-info về " +
      JSON.stringify(poolTxt.slice(0, 70)) + "…; batch sau: pool 1 worker, " +
      "worker 0: 2 game, không có worker 1; pageerror mới: " + (pageErrors.length - errsBefore),
      performance.now() - t0);
  }

  { // D4. Lưu qua reload: 1 giữ nguyên; đổi 2, reload -> 2
    const t0 = performance.now();
    await page.reload();
    await waitReady();
    await goBatchTab();
    const v1 = await poolSizeVal();
    await page.selectOption("#batch-pool-size", "2");
    await page.reload();
    await waitReady();
    await goBatchTab();
    const v2 = await poolSizeVal();
    const hook = await poolSizeHook();
    rec("browser_pool_size_persists_reload", v1 === "1" && v2 === "2" && hook === 2,
      "sau reload (đã lưu 1): ô chọn=" + JSON.stringify(v1) + "; đổi 2 rồi reload: " +
      "ô chọn=" + JSON.stringify(v2) + ", __PB3_POOL_SIZE=" + hook,
      performance.now() - t0);
  }

  { // D5. Đổi N GIỮA CHỪNG (batch 4 game bigtest đang chạy, N=2)
    const t0 = performance.now();
    const errsBefore = pageErrors.length;
    await page.setInputFiles("#batch-files", [BIG, BIG]);
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "bigtest.zip", mode: "extract" },
      { path: "bigtest.zip", mode: "extract", locale: "en" },
      { path: "bigtest.zip", mode: "extract", locale: "vi" },
      { path: "bigtest.zip", mode: "extract", locale: "zh" },
    ]));
    await page.click("#btn-batch-run");
    // Đợi tới khi có worker đang chạy game thì đổi N sang 1.
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-pool-info"); return el && el.textContent.includes("đang chạy"); },
      null, { timeout: 300000 });
    await page.selectOption("#batch-pool-size", "1");
    const note = await elText("batch-pool-size-note");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("/ 4 game"); },
      null, { timeout: 300000 });
    const res = await batchResultText();
    const keptOld = res.includes("pool 2 worker");
    // Đợt sau phải dùng N mới (1).
    const next = await runBatch2Games([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]);
    rec("browser_switch_n_mid_batch_safe", note.includes("giữ nguyên") && keptOld &&
      next.res.includes("pool 1 worker") && next.res.includes("worker 0: 2 game") &&
      pageErrors.length === errsBefore,
      "đổi sang 1 giữa đợt đang chạy: ghi chú " + JSON.stringify(note.slice(0, 90)) +
      "…; đợt đang chạy giữ nguyên pool 2 worker: " + keptOld + "; đợt sau: pool 1 " +
      "worker, worker 0: 2 game; pageerror mới: " + (pageErrors.length - errsBefore),
      performance.now() - t0);
  }

  { // D7. 390px không tràn ngang (phiên chính, cuối D5)
    const t0 = performance.now();
    const overflow = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    rec("browser_390_no_overflow_main", overflow.sw <= overflow.cw,
      "390px: scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      " (ô chọn #batch-pool-size + ghi chú không tràn)",
      performance.now() - t0);
  }
} finally {
  if (browser) await browser.close().catch(() => {});
  server.close();
}

// ================= D6. RAM thấp tự động về 1 (context mới) =================
{
  const t0 = performance.now();
  const server2 = makeServer(SITE);
  await new Promise((r) => server2.listen(0, "127.0.0.1", r));
  const PORT2 = server2.address().port;
  let browser2 = null;
  const errs2 = [];
  try {
    const { chromium } = await import("playwright-core");
    const exe = [
      process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
      process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/chrome",
    ].find((c) => fs.existsSync(c));
    browser2 = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
    const ctx2 = await browser2.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
    const page2 = await ctx2.newPage();
    await page2.addInitScript(() => {
      try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
      // Giả lập thiết bị RAM thấp 4 GB (hook harness của app.js).
      window.__PB3_DEVICE_MEMORY = 4;
    });
    page2.on("pageerror", (e) => errs2.push(String(e && e.message ? e.message : e)));
    page2.on("console", (m) => { if (m.type() === "error") errs2.push("console: " + m.text()); });
    await page2.goto("http://127.0.0.1:" + PORT2 + "/index.html");
    await page2.setInputFiles("#file-input", GAME1);
    await page2.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    await page2.click("#btn-card-batch");
    await page2.waitForFunction(
      () => { const el = document.getElementById("tab-batch"); return el && !el.hidden; },
      null, { timeout: 30000 });
    const val = await page2.evaluate(() => (document.getElementById("batch-pool-size") || {}).value || "");
    const hook = await page2.evaluate(() => window.__PB3_POOL_SIZE);
    const note = await page2.evaluate(() => (document.getElementById("batch-pool-size-note") || {}).textContent || "");
    const stored = await page2.evaluate(() => { try { return localStorage.getItem("pb3_pool_size"); } catch (e) { return "ERR"; } });
    await page2.setInputFiles("#batch-files", [GAME1, GAME1]);
    await page2.fill("#batch-manifest", JSON.stringify([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]));
    await page2.click("#btn-batch-run");
    await page2.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("/ 2 game"); },
      null, { timeout: 300000 });
    const res = await page2.evaluate(() => (document.getElementById("batch-result") || {}).textContent || "");
    const overflow = await page2.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    const realErrs = errs2.filter((e) => !e.includes("Failed to load resource"));
    rec("browser_low_memory_auto_n1", val === "1" && hook === 1 && stored === null &&
      note.includes("tự động") && note.includes("4 GB") &&
      res.includes("pool 1 worker") && res.includes("worker 0: 2 game") &&
      !res.includes("worker 1:") && overflow.sw <= overflow.cw && realErrs.length === 0,
      "__PB3_DEVICE_MEMORY=4, chưa có lựa chọn lưu (localStorage pb3_pool_size=" +
      JSON.stringify(stored) + ") -> ô chọn=" + JSON.stringify(val) + ", hook=" + hook +
      "; ghi chú: " + JSON.stringify(note.slice(0, 110)) + "…; batch 2 game: pool 1 worker, " +
      "worker 0: 2 game; 390px scrollWidth=" + overflow.sw + " clientWidth=" + overflow.cw +
      "; pageerror thật: " + (realErrs.join("; ") || "không có"),
      performance.now() - t0);
  } finally {
    if (browser2) await browser2.close().catch(() => {});
    server2.close();
  }
}

// ================= D8. Không pageerror (phiên chính) =================
// (pageErrors của phiên chính đã thu suốt D0–D5; kiểm tra lại ở đây.)
{
  const t0 = performance.now();
  const realErrors = pageErrors.filter((e) => !e.includes("Failed to load resource"));
  rec("browser_no_errors_main_session", realErrors.length === 0,
    "pageerror/console-error thật suốt phiên chính D0–D5 + D7: " + (realErrors.join("; ") || "không có"),
    performance.now() - t0);
}

// ================= S. Vỏ trang sau C9 (ghi số thật) =================
{ // S1. Shell tĩnh (cùng cách đo mục 6: tổng raw + gzip 7 file lần tải đầu)
  const t0 = performance.now();
  const files = ["index.html", "kb-store.js", "worker-pool.js", "stream.js", "opfs.js", "app.js", "gt.js"];
  let raw = 0, gz = 0;
  const lines = [];
  for (const f of files) {
    const buf = fs.readFileSync(SITE + "/" + f);
    const g = zlib.gzipSync(buf, { level: 9 }).length;
    raw += buf.length; gz += g;
    lines.push(f + " raw=" + buf.length + "B gzip=" + g + "B");
  }
  rec("shell_size_after_c9", true,
    "shell lần tải đầu sau C9: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C9 không đổi kết luận.",
    performance.now() - t0);
}

// ================= TỔNG KẾT =================
const total = Object.keys(results).length;
const passed = Object.values(results).filter((r) => r.ok).length;
console.log(`\n===== KẾT QUẢ C9: ${passed}/${total} PASS (${failures} FAIL) =====`);
for (const [k, v] of Object.entries(results)) console.log(`- ${k}: ${v.ok ? "PASS" : "FAIL"}`);
fs.writeFileSync(EXP + "/analysis_c9_results.json",
  JSON.stringify({ total, passed, failures, results }, null, 2) + "\n");
process.exit(failures ? 1 : 0);
