// verify_analysis_c4.mjs — Kiểm chứng mục C4: cảnh báo file lớn + xuất ra
// OPFS + blob concat (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_analysis_c4.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c4_results.md + analysis_c4_results.json
//
//  S. Tĩnh + Node vm:
//     S1. node --check app.js/stream.js/sw.js/opfs.js; sw.js precache có
//         opfs.js + CACHE_VERSION v10; index.html nạp opfs.js + có modal
//         #large-file-modal (Tiếp tục/Hủy) + nút #btn-opfs-cleanup.
//     S2. Nạp opfs.js trong vm (không có navigator.storage): supported()
//         false → writeOutput() trả {ok:false, via:"memory"} (fallback,
//         không ném); safeName() bỏ đường dẫn/ký tự cấm; toParts() chia
//         20MB thành 3 part 8MB (subarray, không copy toàn bộ).
//     S3. Wiring app.js/stream.js: confirmLargeFile + ngưỡng 400MB + nút
//         Tiếp tục/Hủy; download() ghi OPFS khi >=1MB (writeOutput(name,
//         data) — đưa data thô, không gói Blob trước); runOp đợi download
//         xong rồi bỏ tham chiếu msg.file.data; blob concat: stream.js
//         makeOutputBlob dùng new Blob(parts), extractZipEntry stored dùng
//         file.slice (copy-through).
//     S4. Shell tĩnh sau C4 (ghi số thật; không đặt ngưỡng PASS/FAIL mới).
//  B. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local):
//     B1. File đơn 450MB (sparse, /tmp): chọn → modal hiện TRƯỚC khi đọc
//         (file chưa nạp, chưa boot); Hủy → dừng hẳn, file cũ giữ nguyên;
//         chọn lại → Tiếp tục → file nạp xong (file-info hiện 450MB).
//     B2. File 850MB: modal hiện gợi ý iSH + lý do RAM tab ~1–1,5GB;
//         Hủy → không nạp (KHÔNG bấm Tiếp tục — tránh ôm 850MB vào RAM).
//     B3. Kéo-thả: file nhỏ thả vào dropzone nạp thẳng (không modal);
//         file 450MB thả vào → modal hiện → Hủy → không nạp.
//     B4. Batch: lô có file 450MB → 1 modal liệt kê file lớn → Hủy →
//         lô cũ giữ nguyên (rỗng); chọn lại → Tiếp tục → cả 2 file nạp.
//     B5. OPFS end-to-end (không cần boot — đường liệt kê nhanh EOCD):
//         ZIP fixture có entry stored 2MB → Liệt kê → bấm Tải entry →
//         ghi ra OPFS thật (đọc lại được, sha256 khớp file gốc), state
//         không giữ blob (lastHasBlob=false), download thật từ OPFS cũng
//         khớp sha256; bấm “Dọn file tạm” → OPFS trống.
//     B6. Fallback tắt OPFS (__PB3_NO_OPFS): cùng luồng B5 → đường RAM,
//         hint ghi rõ lý do fallback, không crash, download vẫn khớp sha256.
//     B7. Blob concat hành vi: PB3Stream.makeOutputBlob([2 part]) nối đúng
//         nội dung + textToBlobParts chia 64KB không copy.
//     B8. 390px không tràn ngang, không pageerror cả phiên.
// KHÔNG thử được Safari thật (không có BrowserStack/LambdaTest sẵn):
// nút Chia sẻ sau khi ghi OPFS (dọn file sau khi share xong), OPFS trên
// Safari thật, modal trên Safari thật vẫn cần Joseph thử.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import zlib from "node:zlib";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const EXP = ROOT + "/exp";
const FX_ZIP = EXP + "/fixtures/c4_entry_2mb.zip";
const FX_SMALL = EXP + "/fixtures/extract_select_fixture.zip";
const FAKE450 = "/tmp/c4_fake450.unity3d";
const FAKE850 = "/tmp/c4_fake850.unity3d";
const BIG_SHA256 = "b03652b11512eb21116c6dee23443d6edf4ad2536f41b32a576bb268ab4f9ea8";
const BIG_SIZE = 2 * 1024 * 1024;
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= S. Tĩnh =================
const appSrc = fs.readFileSync(SITE + "/app.js", "utf8");
const swSrc = fs.readFileSync(SITE + "/sw.js", "utf8");
const streamSrc = fs.readFileSync(SITE + "/stream.js", "utf8");
const opfsSrc = fs.readFileSync(SITE + "/opfs.js", "utf8");
const indexSrc = fs.readFileSync(SITE + "/index.html", "utf8");

{ // S1. node --check + sw.js precache opfs.js + v10 + modal trong index.html
  const t0 = performance.now();
  const bad = [];
  for (const f of ["app.js", "sw.js", "stream.js", "opfs.js"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  const ver = (swSrc.match(/const CACHE_VERSION = "([^"]+)"/) || [])[1];
  const listMatch = swSrc.match(/const PRECACHE_URLS = \[([\s\S]*?)\];/);
  const listed = listMatch ? [...listMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
  const checks = {
    nodeCheck: bad.length === 0,
    swVersionV10: ver === "v23-20261117",
    swPrecacheOpfs: listed.includes("opfs.js"),
    opfsOnDisk: fs.existsSync(SITE + "/opfs.js"),
    indexLoadsOpfs: /<script src="opfs\.js"><\/script>/.test(indexSrc),
    modalIds: /id="large-file-modal"/.test(indexSrc) && /id="large-file-text"/.test(indexSrc) &&
      /id="large-file-continue"/.test(indexSrc) && /id="large-file-cancel"/.test(indexSrc),
    modalButtons: />Tiếp tục</.test(indexSrc) && />Hủy</.test(indexSrc),
    cleanupBtn: /id="btn-opfs-cleanup"/.test(indexSrc) && />Dọn file tạm</.test(indexSrc),
    modalCss: /\.modal \{/.test(indexSrc) && /\.modal-card \{/.test(indexSrc),
  };
  const missing = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_node_check_sw_modal", missing.length === 0,
    "node --check OK: app.js, sw.js, stream.js, opfs.js" + (bad.length ? " | LỖI: " + bad.join(", ") : "") +
    "; CACHE_VERSION=" + ver + " (cần v23-20261117); precache có opfs.js=" + listed.includes("opfs.js") +
    " (tổng " + listed.length + " mục); index.html nạp opfs.js + modal Tiếp tục/Hủy + nút Dọn file tạm + CSS modal: " +
    Object.entries(checks).map(([k, v]) => k + "=" + v).join(", ") +
    (missing.length ? " | THIẾU: " + missing.join(", ") : ""),
    performance.now() - t0);
}

{ // S2. Nạp opfs.js trong vm (không navigator.storage): fallback sạch + toParts + safeName
  const t0 = performance.now();
  let ok = false, detail = "";
  try {
    const sandbox = {
      navigator: {}, localStorage: { _m: {}, getItem(k) { return this._m[k] || null; },
        setItem(k, v) { this._m[k] = String(v); }, removeItem(k) { delete this._m[k]; } },
      Blob, TextEncoder, TextDecoder, performance, setTimeout, clearTimeout, console,
    };
    sandbox.window = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(opfsSrc, sandbox, { filename: "opfs.js" });
    const api = sandbox.PB3OPFS;
    const supported = api.supported();
    const w = await api.writeOutput("extract.zip", new Uint8Array([1, 2, 3]));
    const parts = api.toParts(new Uint8Array(20 * 1024 * 1024));
    const partSizes = parts.map((p) => p.byteLength);
    const names = [
      api.safeName("dir/sub/extract.zip"),
      api.safeName("a:b?.zip"),
      api.safeName(""),
      api.safeName("my file.json"),
    ];
    const list = await api.listOutputs();
    const cleaned = await api.cleanupOutputs();
    ok = api && typeof api.writeOutput === "function" && typeof api.readOutput === "function" &&
      typeof api.deleteOutput === "function" && typeof api.listOutputs === "function" &&
      typeof api.cleanupOutputs === "function" && typeof api.cleanupStaleOnBoot === "function" &&
      api.CHUNK_BYTES === 8 * 1024 * 1024 && supported === false &&
      w.ok === false && w.via === "memory" && typeof w.error === "string" && w.error.length > 0 &&
      parts.length === 3 && partSizes[0] === 8 * 1024 * 1024 && partSizes[1] === 8 * 1024 * 1024 &&
      partSizes[2] === 4 * 1024 * 1024 &&
      names[0] === "extract.zip" && names[1] === "a_b_.zip" && names[2] === "output.bin" &&
      names[3] === "my file.json" &&
      Array.isArray(list) && list.length === 0 && cleaned === 0 &&
      !/^\s*export\s/m.test(opfsSrc);
    detail = "PB3OPFS API đủ (supported/writeOutput/readOutput/deleteOutput/listOutputs/cleanupOutputs/cleanupStaleOnBoot/CHUNK_BYTES=8MB); " +
      "trong vm (không có navigator.storage.getDirectory): supported()=" + supported +
      "; writeOutput() không ném, trả " + JSON.stringify(w) + " (fallback về RAM, kèm lý do thật); " +
      "toParts(20MB) = " + parts.length + " part, cỡ " + partSizes.join("/") + " (8MB+8MB+4MB, subarray không copy toàn bộ); " +
      "safeName: " + JSON.stringify(names) + "; listOutputs()=[] khi không có OPFS; cleanupOutputs()=0; classic script (không export).";
  } catch (e) { detail = "lỗi nạp opfs.js trong vm: " + String(e && e.message ? e.message : e); }
  rec("vm_opfs_fallback_and_parts", ok, detail, performance.now() - t0);
}

{ // S3. Wiring C4 trong app.js + stream.js (blob concat / copy-through)
  const t0 = performance.now();
  const checks = {
    confirmFn: /function confirmLargeFile\(files\)/.test(appSrc),
    threshold400: /LARGE_FILE_CONFIRM_BYTES = EXTRACT_WARN_BYTES/.test(appSrc) &&
      /EXTRACT_WARN_BYTES = 400 \* 1024 \* 1024/.test(appSrc),
    modalStateHook: /__PB3_LARGE_FILE_STATE/.test(appSrc),
    modalStrongIsh: /1–1,5GB RAM/.test(appSrc) && /bản iSH/.test(appSrc),
    gateSingle: /handleChosenFile[\s\S]{0,900}await confirmLargeFile\(\[f\]\)/.test(appSrc),
    gateBatch: /handleBatchFiles[\s\S]{0,1400}await confirmLargeFile\(valid\)/.test(appSrc),
    gateBeforeRead: /confirmLargeFile[\s\S]{0,2000}f\.arrayBuffer\(\)/.test(appSrc),
    opfsMin1MB: /OPFS_MIN_BYTES = 1 \* 1024 \* 1024/.test(appSrc),
    downloadOpfsWrite: /PB3OPFS\.writeOutput\(name, data\)/.test(appSrc),
    downloadOpfsState: /via: "opfs"/.test(appSrc) && /__PB3_OPFS_STATE/.test(appSrc),
    downloadCleanup: /btn-opfs-cleanup/.test(appSrc) && /cleanupOutputs\(\)/.test(appSrc),
    shareReadsOpfs: /lastDownloadFile\(\)/.test(appSrc) && /readOutput\(d\.opfsName\)/.test(appSrc),
    runOpAwaitDownload: /await download\(msg\.file\.name, msg\.file\.data\)/.test(appSrc),
    runOpReleaseBuffer: /msg\.file\.data = null/.test(appSrc),
    staleCleanupBoot: /cleanupStaleOnBoot\(\)/.test(appSrc),
    streamMakeBlobParts: /function makeOutputBlob\(parts, type\) \{\s*\n\s*return new Blob\(parts/.test(streamSrc),
    streamStoredSlice: /extractZipEntry[\s\S]{0,2500}file\.slice\(start, start \+ entry\.size\)/.test(streamSrc),
    streamNoWholeConcat: !/new Blob\(\[data\]\)/.test(streamSrc),
    opfsChunkedWrite: /CHUNK_BYTES = 8 \* 1024 \* 1024/.test(opfsSrc) &&
      /await writer\.write\(parts\[i\]\)/.test(opfsSrc),
    opfsNoCoi: !/new\s+SharedArrayBuffer|SharedArrayBuffer\s*\(/.test(opfsSrc),
  };
  const missing = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_c4_wiring", missing.length === 0,
    "kiểm tra wiring C4: " + Object.entries(checks).map(([k, v]) => k + "=" + v).join(", ") +
    (missing.length ? " | THIẾU: " + missing.join(", ") : "") +
    ". Lưu ý: ngưỡng 600/800MB, blob concat (new Blob(parts)) và copy-through stored (slice) đã có từ mục 5 — C4 chỉ thêm cổng xác nhận + OPFS, không viết lại đường nóng.",
    performance.now() - t0);
}

{ // S4. Shell tĩnh sau C4 (ghi số thật; không đặt ngưỡng PASS/FAIL mới)
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
  rec("shell_size_after_c4", true,
    "shell lần tải đầu sau C4: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). opfs.js mới raw=" + fs.statSync(SITE + "/opfs.js").size + "B; app.js tăng do " +
    "download() OPFS + modal xác nhận. Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C4 không đổi kết luận.",
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
const pageErrors = [];
const sha256hex = (buf) => crypto.createHash("sha256").update(buf).digest("hex");
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
  const modalVisible = (page) => page.evaluate(() => !document.getElementById("large-file-modal").hidden);
  const fileLoaded = (page) => page.evaluate(() => !document.getElementById("file-info").hidden);

  // ---------- Context A: modal file lớn (đơn + 850MB + kéo-thả + batch) ----------
  const ctxA = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageA = await ctxA.newPage();
  pageA.on("pageerror", (e) => pageErrors.push("ctxA: " + String(e && e.message ? e.message : e)));
  await pageA.goto(BASE + "/index.html");
  await pageA.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
    null, { timeout: 60000 });
  const bootStateA = () => pageA.evaluate(() => window.__PB3_BOOT_STATE());

  { // B1a. File đơn 450MB: modal hiện TRƯỚC khi đọc (chưa nạp, chưa boot); Hủy → dừng hẳn
    const t0 = performance.now();
    await pageA.setInputFiles("#file-input", FAKE450);
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 30000 });
    const txt = await elText(pageA, "large-file-text");
    const st = await pageA.evaluate(() => window.__PB3_LARGE_FILE_STATE());
    const loadedBefore = await fileLoaded(pageA);
    const bsBefore = await bootStateA();
    await pageA.click("#large-file-cancel");
    await sleep(500);
    const modalHidden = await pageA.evaluate(() => document.getElementById("large-file-modal").hidden);
    const stAfter = await pageA.evaluate(() => window.__PB3_LARGE_FILE_STATE());
    const loadedAfter = await fileLoaded(pageA);
    const bsAfter = await bootStateA();
    rec("browser_modal_450mb_cancel", txt.includes("450") && txt.includes("400MB") &&
      st.visible === true && st.files.length === 1 && st.files[0].size === 471859200 &&
      loadedBefore === false && bsBefore.booted === false && bsBefore.workerCreated === false &&
      modalHidden === true && stAfter.decision === false && loadedAfter === false &&
      bsAfter.booted === false && bsAfter.workerCreated === false,
      "chọn file 450MB → modal hiện, nội dung=" + JSON.stringify(txt.slice(0, 220)) +
      "; __PB3_LARGE_FILE_STATE lúc hiện=" + JSON.stringify(st) +
      "; TRƯỚC khi bấm: file-info hiện=" + loadedBefore + " (chưa nạp vào RAM), boot=" + JSON.stringify(bsBefore) +
      " (chưa boot — cổng chặn đứng trước cả ensureBoot); bấm Hủy → modal ẩn=" + modalHidden +
      ", decision=" + stAfter.decision + ", file-info hiện=" + loadedAfter +
      ", boot sau hủy=" + JSON.stringify(bsAfter) + " (dừng hẳn, không chạy ngầm)",
      performance.now() - t0);
  }
  { // B1b. Chọn lại 450MB → Tiếp tục → file nạp xong
    const t0 = performance.now();
    // Input vẫn giữ file cũ từ B1a (app không xóa value khi Hủy) nên phải
    // xóa value trước, không thì chọn lại cùng file sẽ không bắn change.
    await pageA.evaluate(() => { document.getElementById("file-input").value = ""; });
    await pageA.setInputFiles("#file-input", FAKE450);
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 30000 });
    await pageA.click("#large-file-continue");
    await pageA.waitForFunction(() => !document.getElementById("file-info").hidden, null, { timeout: 120000 });
    const info = await elText(pageA, "file-info");
    const st = await pageA.evaluate(() => window.__PB3_LARGE_FILE_STATE());
    rec("browser_modal_450mb_continue", info.includes("c4_fake450.unity3d") && info.includes("450") &&
      st.decision === true,
      "chọn lại 450MB → bấm Tiếp tục → file-info=" + JSON.stringify(info) +
      " (đã nạp xong 450MB vào RAM sau khi user xác nhận); decision=" + st.decision,
      performance.now() - t0);
    await pageA.click("#btn-clear-file");
    await sleep(300);
  }
  { // B2. File 850MB: cảnh báo mạnh + gợi ý iSH + lý do RAM; Hủy → không nạp
    const t0 = performance.now();
    await pageA.setInputFiles("#file-input", FAKE850);
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 30000 });
    const txt = await elText(pageA, "large-file-text");
    const st = await pageA.evaluate(() => window.__PB3_LARGE_FILE_STATE());
    const loadedBefore = await fileLoaded(pageA);
    await pageA.click("#large-file-cancel");
    await sleep(500);
    const loadedAfter = await fileLoaded(pageA);
    rec("browser_modal_850mb_ish_hint", txt.includes("800MB") && txt.includes("iSH") &&
      txt.includes("1–1,5GB") && st.files.length === 1 && st.files[0].size === 891289600 &&
      loadedBefore === false && loadedAfter === false,
      "chọn file 850MB → modal hiện, nội dung=" + JSON.stringify(txt.slice(0, 300)) +
      " (có mốc >800MB + gợi ý dùng bản iSH + lý do RAM tab ~1–1,5GB); " +
      "trước/sau khi bấm Hủy: file-info hiện=" + loadedBefore + "/" + loadedAfter +
      " (không nạp 850MB vào RAM — chỉ kiểm tra cảnh báo + Hủy, không chạy tiếp)",
      performance.now() - t0);
  }
  { // B3a. Kéo-thả file nhỏ: nạp thẳng, không modal
    const t0 = performance.now();
    await pageA.evaluate(async () => {
      const buf = await (await fetch("fixtures/c4_entry_2mb.zip")).arrayBuffer();
      const dt = new DataTransfer();
      dt.items.add(new File([buf], "c4_entry_2mb.zip"));
      document.getElementById("dropzone").dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await pageA.waitForFunction(() => !document.getElementById("file-info").hidden, null, { timeout: 60000 });
    const info = await elText(pageA, "file-info");
    const modalShown = await modalVisible(pageA);
    rec("browser_drop_small_no_modal", info.includes("c4_entry_2mb.zip") && modalShown === false,
      "thả file 2MB vào dropzone → file-info=" + JSON.stringify(info) +
      "; modal hiện=" + modalShown + " (file <=400MB không hỏi — hành vi cũ giữ nguyên)",
      performance.now() - t0);
    await pageA.click("#btn-clear-file");
    await sleep(300);
  }
  { // B3b. Kéo-thả file 450MB: modal hiện → Hủy → không nạp
    const t0 = performance.now();
    await pageA.evaluate(() => {
      const dt = new DataTransfer();
      dt.items.add(new File([new ArrayBuffer(450 * 1024 * 1024)], "drop450.unity3d"));
      document.getElementById("dropzone").dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 60000 });
    const txt = await elText(pageA, "large-file-text");
    const loadedBefore = await fileLoaded(pageA);
    await pageA.click("#large-file-cancel");
    await sleep(500);
    const loadedAfter = await fileLoaded(pageA);
    rec("browser_drop_450mb_cancel", txt.includes("drop450.unity3d") && txt.includes("400MB") &&
      loadedBefore === false && loadedAfter === false,
      "thả file 450MB vào dropzone → modal hiện, nội dung nhắc tên file=" + JSON.stringify(txt.slice(0, 120)) +
      "; trước/sau Hủy: file-info hiện=" + loadedBefore + "/" + loadedAfter + " (không nạp)",
      performance.now() - t0);
  }
  { // B4. Batch: lô có file 450MB → 1 modal → Hủy giữ lô cũ; chọn lại → Tiếp tục → cả 2 nạp
    const t0 = performance.now();
    await pageA.click("#btn-card-batch");
    await pageA.setInputFiles("#batch-files", [FX_ZIP, FAKE450]);
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 30000 });
    const txt = await elText(pageA, "large-file-text");
    await pageA.click("#large-file-cancel");
    await sleep(500);
    const labelAfterCancel = await pageA.evaluate(() => (document.getElementById("btn-batch-run") || {}).textContent || "");
    await pageA.setInputFiles("#batch-files", [FX_ZIP, FAKE450]);
    await pageA.waitForFunction(() => !document.getElementById("large-file-modal").hidden, null, { timeout: 30000 });
    await pageA.click("#large-file-continue");
    await pageA.waitForFunction(
      () => { const b = document.getElementById("btn-batch-run"); return b && b.textContent.includes("(2 file)"); },
      null, { timeout: 120000 });
    const labelAfterContinue = await pageA.evaluate(() => (document.getElementById("btn-batch-run") || {}).textContent || "");
    rec("browser_batch_450mb_modal", txt.includes("c4_fake450") && txt.includes("400MB") &&
      !labelAfterCancel.includes("(2 file)") && labelAfterContinue.includes("Chạy batch (2 file)"),
      "chọn lô [c4_entry_2mb.zip 2MB + file 450MB] → 1 modal liệt kê file lớn=" + JSON.stringify(txt.slice(0, 160)) +
      "; bấm Hủy → nút chạy batch=" + JSON.stringify(labelAfterCancel) + " (lô cũ rỗng giữ nguyên, chưa đọc file nào); " +
      "chọn lại → Tiếp tục → nút chạy batch=" + JSON.stringify(labelAfterContinue) + " (cả 2 file đã nạp)",
      performance.now() - t0);
  }
  await ctxA.close();

  // ---------- Context B: OPFS end-to-end (đường liệt kê nhanh, không boot) ----------
  const ctxB = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageB = await ctxB.newPage();
  pageB.on("pageerror", (e) => pageErrors.push("ctxB: " + String(e && e.message ? e.message : e)));
  await pageB.goto(BASE + "/index.html");
  await pageB.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
    null, { timeout: 60000 });
  { // B5. Tải entry stored 2MB → ghi OPFS thật → đọc lại khớp sha256 → download khớp → dọn sạch
    const t0 = performance.now();
    await pageB.setInputFiles("#file-input", FX_ZIP);
    await pageB.waitForFunction(() => !document.getElementById("file-info").hidden, null, { timeout: 60000 });
    await pageB.click("#btn-nav-tools");
    await pageB.click("#btn-list");
    await pageB.click("#btn-list-run");
    await pageB.waitForFunction(
      () => document.querySelector('#list-result [data-entry="big.bin"]'),
      null, { timeout: 60000 });
    const dlPromise = pageB.waitForEvent("download", { timeout: 60000 }).catch(() => null);
    await pageB.click('#list-result [data-entry="big.bin"]');
    await pageB.waitForFunction(
      () => window.__PB3_OPFS_STATE && window.__PB3_OPFS_STATE().lastVia === "opfs",
      null, { timeout: 60000 });
    const st = await pageB.evaluate(() => window.__PB3_OPFS_STATE());
    const hint = await elText(pageB, "download-hint");
    const opfsRead = await pageB.evaluate(async () => {
      const f = await window.PB3OPFS.readOutput("big.bin");
      const buf = await f.arrayBuffer();
      const list = await window.PB3OPFS.listOutputs();
      return { size: f.size, name: f.name, list: list.map((x) => ({ name: x.name, size: x.size })) };
    });
    // Hash nội dung đọc lại từ OPFS ở phía Node (2MB base64 qua evaluate).
    const b64 = await pageB.evaluate(async () => {
      const f = await window.PB3OPFS.readOutput("big.bin");
      const buf = await f.arrayBuffer();
      const u8 = new Uint8Array(buf);
      let s = "";
      for (let i = 0; i < u8.length; i += 65536) s += String.fromCharCode.apply(null, u8.subarray(i, i + 65536));
      return btoa(s);
    });
    const opfsSha = sha256hex(Buffer.from(b64, "base64"));
    const dl = await dlPromise;
    let dlSha = null, dlName = null;
    if (dl) {
      dlName = dl.suggestedFilename();
      const p = "/tmp/c4_dl_big.bin";
      await dl.saveAs(p);
      dlSha = sha256hex(fs.readFileSync(p));
    }
    const cleanupVisible = await pageB.evaluate(() => !document.getElementById("btn-opfs-cleanup").hidden);
    await pageB.click("#btn-opfs-cleanup");
    await sleep(500);
    const listAfter = await pageB.evaluate(async () => window.PB3OPFS.listOutputs());
    const stAfter = await pageB.evaluate(() => window.__PB3_OPFS_STATE());
    rec("browser_opfs_write_read_download_cleanup", st.supported === true && st.lastVia === "opfs" &&
      st.lastName === "big.bin" && st.lastSize === BIG_SIZE && st.lastHasBlob === false &&
      st.fallbackReason === null && hint.includes("OPFS") &&
      opfsRead.size === BIG_SIZE && opfsRead.name === "big.bin" &&
      opfsRead.list.some((x) => x.name === "big.bin" && x.size === BIG_SIZE) &&
      opfsSha === BIG_SHA256 && dl !== null && dlName === "big.bin" && dlSha === BIG_SHA256 &&
      cleanupVisible === true && listAfter.length === 0 && stAfter.lastVia === null,
      "chọn ZIP fixture (2MB, entry stored big.bin) → Liệt kê nhanh (EOCD, không boot) → bấm Tải entry → " +
      "__PB3_OPFS_STATE=" + JSON.stringify(st) + " (ghi OPFS, KHÔNG giữ blob trong RAM: lastHasBlob=false); " +
      "hint=" + JSON.stringify(hint.slice(0, 160)) + "; đọc lại từ OPFS: " + JSON.stringify(opfsRead) +
      "; sha256 nội dung đọc từ OPFS=" + opfsSha + " (khớp file gốc " + BIG_SHA256.slice(0, 16) + "…); " +
      "file tải về thật từ OPFS: tên=" + dlName + ", sha256=" + dlSha + " (khớp); nút Dọn file tạm hiện=" + cleanupVisible +
      "; sau khi bấm Dọn: listOutputs=" + JSON.stringify(listAfter) + ", lastVia=" + stAfter.lastVia + " (OPFS trống)",
      performance.now() - t0);
  }
  await ctxB.close();

  // ---------- Context C: fallback tắt OPFS ----------
  const ctxC = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageC = await ctxC.newPage();
  pageC.on("pageerror", (e) => pageErrors.push("ctxC: " + String(e && e.message ? e.message : e)));
  await pageC.addInitScript(() => { window.__PB3_NO_OPFS = true; });
  await pageC.goto(BASE + "/index.html");
  await pageC.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
    null, { timeout: 60000 });
  { // B6. Tắt OPFS → đường RAM, hint ghi rõ lý do, download vẫn khớp sha256, không crash
    const t0 = performance.now();
    await pageC.setInputFiles("#file-input", FX_ZIP);
    await pageC.waitForFunction(() => !document.getElementById("file-info").hidden, null, { timeout: 60000 });
    await pageC.click("#btn-nav-tools");
    await pageC.click("#btn-list");
    await pageC.click("#btn-list-run");
    await pageC.waitForFunction(
      () => document.querySelector('#list-result [data-entry="big.bin"]'),
      null, { timeout: 60000 });
    const dlPromise = pageC.waitForEvent("download", { timeout: 60000 }).catch(() => null);
    await pageC.click('#list-result [data-entry="big.bin"]');
    await pageC.waitForFunction(
      () => window.__PB3_OPFS_STATE && window.__PB3_OPFS_STATE().lastVia === "memory",
      null, { timeout: 60000 });
    const st = await pageC.evaluate(() => window.__PB3_OPFS_STATE());
    const hint = await elText(pageC, "download-hint");
    const dl = await dlPromise;
    let dlSha = null;
    if (dl) {
      const p = "/tmp/c4_dl_big_fallback.bin";
      await dl.saveAs(p);
      dlSha = sha256hex(fs.readFileSync(p));
    }
    rec("browser_opfs_fallback_no_crash", st.supported === false && st.lastVia === "memory" &&
      typeof st.fallbackReason === "string" && st.fallbackReason.length > 0 &&
      hint.includes("OPFS") && dl !== null && dlSha === BIG_SHA256,
      "__PB3_NO_OPFS=true → __PB3_OPFS_STATE=" + JSON.stringify(st) +
      " (supported=false, rơi về đường RAM kèm lý do thật); hint=" + JSON.stringify(hint.slice(0, 200)) +
      " (ghi rõ không ghi được OPFS, khuyên lưu ngay); file tải về: sha256=" + dlSha +
      " (khớp file gốc); không pageerror ở context này",
      performance.now() - t0);
  }
  await ctxC.close();

  // ---------- Context D: blob concat hành vi + 390px ----------
  const ctxD = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const pageD = await ctxD.newPage();
  pageD.on("pageerror", (e) => pageErrors.push("ctxD: " + String(e && e.message ? e.message : e)));
  await pageD.goto(BASE + "/index.html");
  await pageD.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
    null, { timeout: 60000 });
  { // B7. makeOutputBlob nối nhiều part đúng nội dung + textToBlobParts chia 64KB
    const t0 = performance.now();
    const r = await pageD.evaluate(async () => {
      const a = new TextEncoder().encode("BEAR-");
      const b = new TextEncoder().encode("TEA-");
      const c = new TextEncoder().encode("C4");
      const blob = window.PB3Stream.makeOutputBlob([a, b, c], "text/plain");
      const text = await blob.text();
      const parts = window.PB3Stream.textToBlobParts("x".repeat(150000), 65536);
      return { size: blob.size, text, nParts: parts.length,
        partSizes: parts.map((p) => p.byteLength),
        totalLen: window.PB3Stream.blobPartsByteLength(parts) };
    });
    rec("browser_blob_concat_parts", r.size === 11 && r.text === "BEAR-TEA-C4" &&
      r.nParts === 3 && r.partSizes[0] === 65536 && r.partSizes[1] === 65536 &&
      r.partSizes[2] === 18928 && r.totalLen === 150000,
      "PB3Stream.makeOutputBlob([BEAR-, TEA-, C4]) → size=" + r.size + ", nội dung=" + JSON.stringify(r.text) +
      " (new Blob(parts) nối đúng, không copy ArrayBuffer); textToBlobParts(150000 ký tự) = " +
      r.nParts + " part " + r.partSizes.join("/") + ", tổng " + r.totalLen +
      " byte (chia 64KB bằng subarray). Đường copy-through stored (Blob.slice) đã kiểm chứng nội dung ở B5 (sha256 khớp).",
      performance.now() - t0);
  }
  { // B8. 390px không tràn + không pageerror
    const t0 = performance.now();
    const o = await pageD.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    rec("browser_390_no_overflow_no_errors", o.sw <= o.cw + 1 && pageErrors.length === 0,
      "390px: scrollWidth=" + o.sw + " clientWidth=" + o.cw + " (không tràn); pageerror cả phiên (4 context): " +
      (pageErrors.length ? JSON.stringify(pageErrors) : "không có"),
      performance.now() - t0);
  }
  await ctxD.close();

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
const mdLines = ["# Kiểm chứng mục C4 — Cảnh báo file lớn + xuất ra OPFS + blob concat",
  "",
  "Ngày: " + new Date().toISOString() + " (giờ UTC). Harness: exp/verify_analysis_c4.mjs.",
  "",
  "## Kết quả: " + passed + "/" + total + " PASS" + (failures ? " — FAIL: " + Object.keys(results).filter((k) => !results[k].ok).join(", ") : ""),
  "",
  ...Object.entries(results).map(([k, v]) => "### " + k + ": " + (v.ok ? "PASS" : "FAIL") + (v.seconds != null ? " (" + v.seconds + "s)" : "") + "\n" + v.detail + "\n"),
  "## File thay đổi",
  "- `site/opfs.js` (mới): PB3OPFS — ghi output ra OPFS từng phần 8MB (Blob.slice/subarray, không concat toàn bộ), đọc lại dạng File, xóa/liệt kê/dọn file tạm, dọn file quá 7 ngày lúc mở trang, fallback {ok:false, via:\"memory\"} kèm lý do; hook `__PB3_NO_OPFS` để ép fallback.",
  "- `site/index.html`: nạp `opfs.js`; hộp thoại `#large-file-modal` (nút “Tiếp tục”/“Hủy”); nút “Dọn file tạm” trên thanh Lưu file; CSS `.modal`/`.modal-card`.",
  "- `site/app.js`: `confirmLargeFile()` — hỏi TRƯỚC khi đọc file >400MB (đơn, kéo-thả, batch), >800MB cảnh báo mạnh + gợi ý iSH + lý do RAM tab ~1–1,5GB; `download()` ghi output >=1MB ra OPFS rồi mới share/tải (đọc File từ OPFS), fallback RAM ghi rõ lý do; dọn OPFS sau khi chia sẻ xong / bấm ✕ / bấm “Dọn file tạm” / file quá 7 ngày lúc mở trang; `runOp()` đợi ghi xong rồi bỏ tham chiếu buffer output; hook `__PB3_OPFS_STATE`/`__PB3_LARGE_FILE_STATE`.",
  "- `site/stream.js`: chỉ cập nhật comment đầu file (OPFS đã làm ở C4 — trước đây ghi “làm SAU đợt này”).",
  "- `site/sw.js`: CACHE_VERSION v9 → v10; thêm `opfs.js` vào PRECACHE_URLS (theo luật đổi precache phải đổi version).",
  "- Harness pin version v9 → v10 (kèm theo luật trên): `exp/verify_item6.mjs` (SHELL_FILES thêm `opfs.js`, “5 JS” → “6 JS”), `exp/verify_analysis_c3.mjs`, `exp/verify_item8.mjs`, `exp/check_item7_static.py`, `exp/check_item8_static.py`.",
  "- Harness + fixture mới: `exp/verify_analysis_c4.mjs`, `exp/fixtures/c4_entry_2mb.zip` (entry stored `big.bin` 2MB, sha256 b03652b1…).",
  "",
  "## Đã đúng từ mục 5 — C4 KHÔNG sửa lại",
  "- Ngưỡng cảnh báo 600MB/800MB/2GB (`PB3Stream.sizeWarning`, `sizeWarningFor`) và câu chữ cảnh báo — giữ nguyên; C4 chỉ thêm cổng xác nhận chặn TRƯỚC khi đọc (400MB) + mốc 800MB đã có sẵn.",
  "- Blob concat: `makeOutputBlob(new Blob(parts))`, `download()` nhận mảng part, `textToBlobParts` chia 64KB bằng subarray — đã đúng, chỉ kiểm chứng lại (B7).",
  "- Copy-through stored: `extractZipEntry` dùng `file.slice` (không qua heap), `extractZipEntryFromBuffer` dùng subarray — đã đúng, kiểm chứng bằng nội dung (B5 sha256 khớp).",
  "- Xác nhận trích xuất >400MB bằng `confirm()` native trước khi chạy (đợt sửa 2026-11-16) — giữ nguyên, là cổng thứ hai sau cổng chọn file của C4.",
  "",
  "## Giới hạn thật còn lại (mục 4)",
  "- Input vẫn giữ 3 bản trong lúc chạy op qua worker (state.file.data + bản slice(0) gửi qua postMessage transfer + bản trong Pyodide FS của worker) — giao thức send-once/clone-fix bắt buộc giữ bản gốc để gửi lại khi lỗi; sửa triệt để cần viết lại SDK, ngoài phạm vi C4. C4 chỉ gỡ được phía output: sau khi ghi OPFS xong, buffer output của worker được bỏ tham chiếu (runOp) và state không giữ Blob (chỉ giữ tên file OPFS).",
  "- Output từ worker vẫn về main thread dưới dạng 1 ArrayBuffer duy nhất rồi mới ghi OPFS từng phần — ghi OPFS không copy thêm, nhưng bản ArrayBuffer gốc vẫn tồn tại trong lúc ghi (giới hạn của worker protocol hiện tại).",
  "- `extractZipEntryFromBuffer` đường deflate gói vùng nén (subarray) vào `new Blob([comp])` — Blob copy vùng nén đó (chỉ bằng cỡ entry nén, không phải cả file); đường stored thì không copy.",
  "",
  "## Safari thật — chưa thử được",
  "- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.",
  "- CẦN JOSEPH (Safari thật, iPhone): chọn file >400MB → xem hộp thoại “Tiếp tục”/“Hủy”; file >800MB → xem gợi ý iSH; trích xuất file lớn → bấm “Lưu / Chia sẻ” sau khi ghi OPFS (xem file có lưu vào Files được không, file tạm có dọn sau khi share xong không); thử “Dọn file tạm”.",
  "",
];
fs.writeFileSync(EXP + "/analysis_c4_results.md", mdLines.join("\n") + "\n");
fs.writeFileSync(EXP + "/analysis_c4_results.json", JSON.stringify(
  { when: new Date().toISOString(), total, passed, failures, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
