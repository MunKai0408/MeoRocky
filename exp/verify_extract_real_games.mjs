// verify_extract_real_games.mjs — Kiểm chứng đợt sửa trích xuất 2026-11-16
// (MonoBehaviour resources.assets + .unity3d 590MB streaming + CSV locale)
// trên 4 file game THẬT của Joseph, cây site/ hiện tại (chưa đẩy).
// Chạy: node verify_extract_real_games.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: extract_real_games_fix_results.md + extract_real_games_fix_results.json
//   + out/extract_real_games/*.zip|png
//
// Trình duyệt THẬT (Playwright chromium headless shell, 390px). Chromium trong
// sandbox này KHÔNG có HTTPS ngoài nên phục vụ cây site/ qua HTTP local
// (127.0.0.1, nằm trong NO_PROXY) — ĐÚNG code sẽ đẩy, chỉ khác host.
// KHÔNG phải Safari thật — Safari vẫn cần Joseph thử.
// File thật: ~/workspace/real_ipa/{9kings,polybridge3,exploding_kittens2}.ipa
//   + data.unity3d (590.760.029 byte). Chỉ test đọc — KHÔNG đẩy, KHÔNG sửa site.
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { execSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const OUT = EXP + "/out/extract_real_games";
const IPA9K = "/home/hatch/workspace/real_ipa/9kings.ipa";
const IPAPB3 = "/home/hatch/workspace/real_ipa/polybridge3.ipa";
const IPAEK2 = "/home/hatch/workspace/real_ipa/exploding_kittens2.ipa";
const U3D = "/home/hatch/workspace/real_ipa/data.unity3d";
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
const BASE = "http://127.0.0.1:" + server.address().port;
fs.mkdirSync(OUT, { recursive: true });

const sec = (ms) => Math.round(ms / 100) / 10;
// Chạy từng phần: ONLY=1,2,3 (chỉ test 1-3) hoặc ONLY=4,5,6 — để mỗi lượt
// chạy vừa trong giới hạn thời gian phiên exec (~10 phút).
const ONLY = process.env.ONLY ? process.env.ONLY.split(",").map((s) => s.trim()) : null;
const want = (n) => !ONLY || ONLY.includes(String(n));
const results = {};
// Gộp kết quả lượt chạy trước (khi chạy từng phần bằng ONLY=...) để file
// tổng hợp luôn đủ các test đã chạy.
try {
  const prev = JSON.parse(fs.readFileSync(EXP + "/extract_real_games_fix_results.json", "utf8"));
  if (prev && prev.results) Object.assign(results, prev.results);
} catch (_) {}
const pageErrors = [];
let failures = Object.values(results).filter((v) => !v.ok).length;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  failures = Object.values(results).filter((v) => !v.ok).length;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
  fs.writeFileSync(EXP + "/extract_real_games_fix_results.json", JSON.stringify(
    { when: new Date().toISOString(), head: "site/ working tree (unpushed)", failures, results }, null, 2));
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function zipMembers(fp) {
  const out = execSync("python3 " + JSON.stringify(EXP + "/zip_members.py") + " " + JSON.stringify(fp), { encoding: "utf8" });
  return JSON.parse(out);
}
function zipRead(fp, member) {
  return execSync("python3 -c \"import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); print(z.read(sys.argv[2]).decode('utf-8','replace'))\" "
    + JSON.stringify(fp) + " " + JSON.stringify(member), { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

let browser = null;
async function newPage() {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  page.on("crash", () => pageErrors.push("PAGE CRASHED"));
  return { ctx, page };
}
const elText = (page, id) => page.evaluate((i) => (document.getElementById(i) || {}).textContent || "", id);
async function waitReady(page, timeout = 300000) {
  await page.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
    null, { timeout });
  return elText(page, "status");
}
async function selectFile(page, fp, timeout = 300000) {
  await page.setInputFiles("#file-input", fp);
  await page.waitForFunction(
    (n) => { const el = document.getElementById("file-info"); return el && el.textContent.includes(n); },
    path.basename(fp), { timeout: 60000 });
}
// Chạy extract trên tab Trích xuất; dialogHandler: "accept" | "dismiss" | null
// (null = không có dialog nào kỳ vọng). Trả { text, dlPath, members, dialogSeen }.
async function runExtract(page, locale, dlName, timeout = 600000, dialogHandler = null) {
  await page.click("#btn-nav-tools");
  await page.click("#btn-extract");
  await page.selectOption("#extract-locales", locale);
  await page.waitForFunction(
    () => { const el = document.getElementById("extract-file-list"); return el && !el.textContent.includes("Chưa có danh sách file"); },
    null, { timeout: 120000 }).catch(() => {});
  let dialogSeen = null;
  const onDialog = async (d) => {
    dialogSeen = d.message();
    if (dialogHandler === "accept") await d.accept();
    else await d.dismiss();
  };
  if (dialogHandler) page.on("dialog", onDialog);
  const dlPromise = page.waitForEvent("download", { timeout }).catch(() => null);
  await page.click("#btn-extract-run");
  const dl = await dlPromise;
  if (dialogHandler) page.off("dialog", onDialog);
  let text = "";
  try {
    await page.waitForFunction(
      () => { const el = document.getElementById("extract-result"); return el && el.textContent.includes("Kết quả:"); },
      null, { timeout });
    text = await elText(page, "extract-result");
  } catch (_) {
    text = await elText(page, "extract-result");
  }
  if (!dl) return { text, dlPath: null, members: [], dialogSeen };
  const dlPath = OUT + "/" + dlName;
  await dl.saveAs(dlPath);
  return { text, dlPath, members: zipMembers(dlPath), dialogSeen };
}

try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  console.log("Chromium:", exe, "| site:", SITE);

  // ---------- 1: 9 Kings, lọc en — StringTable + MonoBehaviour ----------
  let p1 = null;
  if (want(1)) {
  try {
    const t0 = performance.now();
    p1 = await newPage();
    const { page } = p1;
    await page.goto(BASE + "/index.html", { timeout: 120000 });
    await selectFile(page, IPA9K);
    const status = await waitReady(page);
    const r = await runExtract(page, "en", "extract_9k_en.zip");
    const names = r.members.map((m) => m[0]);
    const hasEnglish = names.includes("English.json");
    const hasMB = names.includes("resources.monobehaviour.json");
    let enContent = "";
    try { enContent = zipRead(r.dlPath, "English.json"); } catch (_) {}
    const farm = /"Farm":\s*"Farm"/.test(enContent) || /"Farm"/.test(enContent);
    const castle = /"Castle"/.test(enContent);
    const longSent = enContent.includes("At the end of your turn, add +1 unit to the one adjacent army.");
    const srcBundle = /localization-string-tables-english/.test(r.text);
    await page.screenshot({ path: OUT + "/01_9k_extract_en.png" });
    rec("extract_9kings_en", hasEnglish && farm && castle && longSent && hasMB && srcBundle && names.length >= 45,
      `boot: ${JSON.stringify(status)}; extract.zip ${names.length} file; English.json: ${hasEnglish} ` +
      `(Farm: ${farm}, Castle: ${castle}, câu dài: ${longSent}); resources.monobehaviour.json: ${hasMB}; ` +
      `nguồn bundle localization hiện trong kết quả: ${srcBundle}; ` +
      `kết quả: ${JSON.stringify(r.text.slice(0, 300))}…`,
      performance.now() - t0);
  } catch (e) {
    rec("extract_9kings_en", false, "LỖI: " + String(e && e.message ? e.message : e));
  }
  } // want(1)
  if (p1) { try { await p1.ctx.close(); } catch (_) {} p1 = null; }

  // ---------- 2: Poly Bridge 3, lọc en — CSV English + MonoBehaviour ----------
  let p2 = null;
  if (want(2)) {
  try {
    const t0 = performance.now();
    p2 = await newPage();
    const { page } = p2;
    await page.goto(BASE + "/index.html", { timeout: 120000 });
    await selectFile(page, IPAPB3);
    await waitReady(page);
    // PB3 482MB > ngưỡng cảnh báo 400MB nên dialog xác nhận sẽ hiện:
    // bấm Tiếp tục (accept) để chạy extract thật.
    const r = await runExtract(page, "en", "extract_pb3_en.zip", 600000, "accept");
    const names = r.members.map((m) => m[0]);
    let enContent = "", mbContent = "";
    try { enContent = zipRead(r.dlPath, "English.json"); } catch (_) {}
    try { mbContent = zipRead(r.dlPath, "resources.monobehaviour.json"); } catch (_) {}
    const csvOk = /"BINDING_START_SIM":\s*"Start Simulation"/.test(enContent);
    const csvSrc = /PB3_LOC_DEFAULT\.csv/.test(r.text);
    const simPause = mbContent.includes("sim_pause");
    const replay = mbContent.includes("replay");
    await page.screenshot({ path: OUT + "/02_pb3_extract_en.png" });
    rec("extract_pb3_csv_en", csvOk && csvSrc && simPause && replay,
      `extract.zip ${names.length} file; English.json từ CSV: ${csvOk} (BINDING_START_SIM → 'Start Simulation'); ` +
      `nguồn PB3_LOC_DEFAULT.csv hiện: ${csvSrc}; resources.monobehaviour.json có sim_pause: ${simPause}, replay: ${replay}; ` +
      `kết quả: ${JSON.stringify(r.text.slice(0, 300))}…`,
      performance.now() - t0);
  } catch (e) {
    rec("extract_pb3_csv_en", false, "LỖI: " + String(e && e.message ? e.message : e));
  }
  } // want(2)
  if (p2) { try { await p2.ctx.close(); } catch (_) {} p2 = null; }

  // ---------- 3: EK2 — sharedassets0.assets được probe + text ra ----------
  let p3 = null;
  if (want(3)) {
  try {
    const t0 = performance.now();
    p3 = await newPage();
    const { page } = p3;
    await page.goto(BASE + "/index.html", { timeout: 120000 });
    await selectFile(page, IPAEK2);
    await waitReady(page);
    const r = await runExtract(page, "en", "extract_ek2_en.zip");
    const names = r.members.map((m) => m[0]);
    let report = "";
    try { report = zipRead(r.dlPath, "extraction_report.txt"); } catch (_) {}
    const probed = report.includes("sharedassets0.assets");
    const hasMB = names.includes("sharedassets0.monobehaviour.json");
    const hasAtlas = names.includes("EK_Avatar_Animation.json");
    await page.screenshot({ path: OUT + "/03_ek2_extract_en.png" });
    rec("extract_ek2_probe", probed && (hasMB || hasAtlas),
      `extract.zip ${names.length} file; sharedassets0.assets xuất hiện trong report (log/skipped/files): ${probed}; ` +
      `sharedassets0.monobehaviour.json: ${hasMB}; EK_Avatar_Animation.json: ${hasAtlas}; ` +
      `kết quả: ${JSON.stringify(r.text.slice(0, 300))}…`,
      performance.now() - t0);
  } catch (e) {
    rec("extract_ek2_probe", false, "LỖI: " + String(e && e.message ? e.message : e));
  }
  } // want(3)
  if (p3) { try { await p3.ctx.close(); } catch (_) {} p3 = null; }

  // ---------- 4+5: data.unity3d — cảnh báo >400MB, Huỷ rồi Tiếp tục ----------
  let p4 = null;
  if (want(4)) {
  try {
    const t0 = performance.now();
    p4 = await newPage();
    const { page } = p4;
    await page.goto(BASE + "/index.html", { timeout: 120000 });
    await selectFile(page, U3D);
    await waitReady(page);
    // 4: Huỷ — dialog hiện, bấm Huỷ thì không chạy.
    const rCancel = await runExtract(page, "en", "extract_u3d_cancel.zip", 60000, "dismiss");
    const cancelOk = rCancel.dialogSeen && /590|400|iSH|RAM/i.test(rCancel.dialogSeen)
      && !rCancel.text.includes("Kết quả:") && !pageErrors.includes("PAGE CRASHED");
    await page.screenshot({ path: OUT + "/04_u3d_warning_cancel.png" });
    rec("extract_u3d_warning_cancel", !!cancelOk,
      `dialog cảnh báo hiện: ${rCancel.dialogSeen ? JSON.stringify(rCancel.dialogSeen.slice(0, 300)) : "(không thấy dialog)"}; ` +
      `sau Huỷ, kết quả extract: ${JSON.stringify(rCancel.text.slice(0, 200))} (phải KHÔNG có "Kết quả:"); ` +
      `trang còn sống: ${!pageErrors.includes("PAGE CRASHED")}`,
      performance.now() - t0);
    // 5: Tiếp tục — dialog hiện, bấm OK thì chạy tới cùng, không crash.
    const t1 = performance.now();
    const rGo = await runExtract(page, "en", "extract_u3d_en.zip", 900000, "accept");
    const names = rGo.members.map((m) => m[0]);
    const hasLevel = names.includes("level0.monobehaviour.json");
    const hasRes = names.includes("resources.monobehaviour.json");
    const done = /Kết quả:\s*done/.test(rGo.text) || /Kết quả:/.test(rGo.text);
    await page.screenshot({ path: OUT + "/05_u3d_extract_continue.png" });
    rec("extract_u3d_continue_streaming", done && hasLevel && hasRes && !pageErrors.includes("PAGE CRASHED"),
      `sau Tiếp tục: ${names.length} file trong ${sec(performance.now() - t1)}s; ` +
      `level0.monobehaviour.json: ${hasLevel}; resources.monobehaviour.json: ${hasRes}; ` +
      `decision: ${JSON.stringify(rGo.text.slice(0, 200))}…; tab còn sống (không crash): ${!pageErrors.includes("PAGE CRASHED")}`,
      performance.now() - t1);
  } catch (e) {
    rec("extract_u3d_warning_cancel", false, "LỖI: " + String(e && e.message ? e.message : e));
    rec("extract_u3d_continue_streaming", false, "LỖI (kéo theo): " + String(e && e.message ? e.message : e));
  }
  } // want(4)
  if (p4) { try { await p4.ctx.close(); } catch (_) {} p4 = null; }

  // ---------- 6: Batch data.unity3d — không còn fail nhanh E_NO_CANDIDATE ----------
  let p6 = null;
  if (want(6)) {
  try {
    const t0 = performance.now();
    p6 = await newPage();
    const { page } = p6;
    await page.goto(BASE + "/index.html", { timeout: 120000 });
    await page.click("#btn-card-batch");
    await page.setInputFiles("#batch-files", [U3D]);
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-files-info"); return el && el.textContent.includes("data.unity3d"); },
      null, { timeout: 180000 });
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "data.unity3d", mode: "extract", locale: "en" },
    ]));
    await page.uncheck("#batch-dryrun");
    await page.click("#btn-batch-run");
    let resumes = 0, finished = false;
    const tEnd = Date.now() + 1800000;
    while (Date.now() < tEnd && !finished) {
      await sleep(5000);
      const st = await page.evaluate(() => {
        const br = (typeof state !== "undefined" && state.batchRun) ? state.batchRun : null;
        const cont = document.getElementById("btn-batch-continue");
        const res = document.getElementById("batch-result");
        return {
          running: !!(br && !br.finished),
          doneCount: br ? br.doneCount : 0, totalTodo: br ? br.totalTodo : 0,
          continueVisible: !!(cont && !cont.hidden),
          resultHasSummary: !!(res && /Batch/.test(res.textContent) && /game/.test(res.textContent)),
          results: br ? br.results : null,
        };
      }).catch(() => null);
      if (!st) break;
      if (st.continueVisible && st.doneCount < st.totalTodo) {
        resumes++;
        if (resumes > 25) break;
        await page.click("#btn-batch-continue").catch(() => {});
        continue;
      }
      if ((!st.running && st.resultHasSummary) || (!st.running && st.doneCount >= st.totalTodo && st.totalTodo > 0)) { finished = true; break; }
    }
    const final = await page.evaluate(() => {
      const br = (typeof state !== "undefined" && state.batchRun) ? state.batchRun : null;
      const res = document.getElementById("batch-result");
      return { doneCount: br ? br.doneCount : 0, totalTodo: br ? br.totalTodo : 0, results: br ? br.results : null, resultText: res ? res.textContent.slice(0, 800) : "" };
    }).catch(() => null);
    await page.screenshot({ path: OUT + "/06_batch_u3d.png" });
    const g = final && final.results && final.results[0];
    const ok = finished && g && (g.status === "ok" || g.status === "skipped") && !/E_NO_CANDIDATE/.test(JSON.stringify(final));
    rec("batch_u3d_no_fastfail", !!ok,
      `xong ${final && final.doneCount}/${final && final.totalTodo} game, số lần bấm "Tiếp tục" (watchdog): ${resumes}; ` +
      `kết quả game: ${JSON.stringify(g)}; batch-result: ${JSON.stringify((final && final.resultText) || "")}`,
      performance.now() - t0);
  } catch (e) {
    rec("batch_u3d_no_fastfail", false, "LỖI: " + String(e && e.message ? e.message : e));
  }
  } // want(6)
  if (p6) { try { await p6.ctx.close(); } catch (_) {} p6 = null; }
} catch (e) {
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e));
}
if (browser) { try { await browser.close(); } catch (_) {} }
server.close();

const passed = Object.values(results).filter((v) => v.ok).length;
const total = Object.keys(results).length;
const lines = ["# Kiểm chứng đợt sửa trích xuất trên 4 file game THẬT",
  "",
  "Ngày: 2026-11-16. Harness: exp/verify_extract_real_games.mjs. Cây: ~/workspace/pb3_web/site/ (chưa đẩy — đúng code sẽ đẩy).",
  "Trình duyệt THẬT: Playwright chromium headless shell, viewport 390px. Chromium trong sandbox này không có HTTPS ngoài nên test chạy trên HTTP local (127.0.0.1) phục vụ cây site/ — ĐÚNG code sẽ đẩy, chỉ khác host. KHÔNG phải Safari thật — Safari vẫn cần Joseph thử.",
  "File thật: `~/workspace/real_ipa/9kings.ipa` (173.472.485 byte), `polybridge3.ipa` (482.039.645 byte), `exploding_kittens2.ipa` (290.547.090 byte), `data.unity3d` (590.760.029 byte). Chỉ test đọc — KHÔNG đẩy code, KHÔNG sửa file site.",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  `Tổng: ${total} mục, ${passed} PASS, ${failures} FAIL.`,
  "",
  "## Lỗi trang (pageerror) ghi được trong suốt đợt test",
  pageErrors.length ? pageErrors.map((e) => "- " + e).join("\n") : "Không có pageerror nào.",
  "",
  "## File kèm theo",
  "- Extract.zip đã tải: `exp/out/extract_real_games/extract_9k_en.zip`, `extract_pb3_en.zip`, `extract_ek2_en.zip`, `extract_u3d_en.zip`.",
  "- Ảnh chụp: `exp/out/extract_real_games/01_9k_extract_en.png`, `02_pb3_extract_en.png`, `03_ek2_extract_en.png`, `04_u3d_warning_cancel.png`, `05_u3d_extract_continue.png`, `06_batch_u3d.png`.",
];
fs.writeFileSync(EXP + "/extract_real_games_fix_results.md", lines.join("\n") + "\n");
fs.writeFileSync(EXP + "/extract_real_games_fix_results.json", JSON.stringify(
  { when: new Date().toISOString(), head: "site/ working tree (unpushed)", failures, results }, null, 2));
console.log(`\n==== TỔNG: ${total} mục, ${passed} PASS, ${failures} FAIL ====`);
process.exit(failures ? 1 : 0);
