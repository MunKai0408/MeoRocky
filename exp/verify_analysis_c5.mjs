// verify_analysis_c5.mjs — Kiểm chứng mục C5: Zstd đọc qua fzstd +
// ghi block không nén, UnityFS v9 chỉ đọc (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_analysis_c5.mjs (từ thư mục exp/ của repo)
// Kết quả: analysis_c5_results.md + analysis_c5_results.json
//
//  S. Tĩnh + CPython:
//     S1. node --check worker.js/sw.js/fzstd/index.mjs; fzstd/LICENSE là
//         MIT © 2020 Arjun Barrett; sw.js CACHE_VERSION v11 + precache
//         có fzstd/index.mjs (128 mục); sdk/unity.py có đủ mốc C5
//         (v9 trong SUPPORTED_BUNDLE_VERSIONS, COMPRESSION_ZSTD=5,
//         js.pb3_zstd_decompress, đọc offset u64 khi version>=9, chặn
//         ghi v9/zstd, apply_patch v9 từ chối sớm + zstd -> rebuild
//         "none"); worker.js dynamic import fzstd + công bố
//         globalThis.pb3_zstd_decompress (có fallback null để boot
//         không chết); index.html ghi chú UnityFS v6–v9 (v9 chỉ đọc).
//     S2a. CPython hệ thống: unittest tests.test_unity.TestBundleV9Zstd
//         (13 test) — 0 FAIL; 2 test cần gói `zstandard` sẽ SKIP và
//         được ghi rõ (không tính là PASS giả).
//     S2b. Venv có `zstandard` (+lz4): dùng lại /tmp/zstdvenv nếu có,
//         không thì tạo /tmp/c5_zstdvenv + pip install — chạy lại cùng
//         13 test, kỳ vọng 13/13, 0 skip (bộ giải thật đứng chỗ fzstd).
//     S3. Shell tĩnh sau C5 (ghi số thật; không đặt ngưỡng PASS/FAIL mới).
//     S4. Header fixture (đọc bằng Node): version + mã nén trong
//         data_flags của 5 bundle; blocks-info của v9 (không nén) có
//         offset u64 [0, 3776] (block 2 căn 16).
//  B. Trình duyệt THẬT (Playwright chromium headless shell, 390px, serve
//     site qua HTTP local):
//     B0. Boot tới "Sẵn sàng" (chọn file ZIP nhỏ).
//     B1. fzstd trực tiếp trong trang: import ./fzstd/index.mjs rồi
//         giải nén c5_zstd_frame.bin -> đúng từng byte câu mẫu; dữ
//         liệu rác thì ném lỗi (không trả rác).
//     B2. Inspect bundle Zstd v7 qua UI: hiện "nén zstd" + TextAsset
//         "Xin chao 9 Kings!" (giải nén thật qua cầu nối JS->Python).
//     B3. Inspect bundle v9 (không nén): hiện "nén none" + TextAsset
//         "Xin chao Unity 6!" (đọc bằng offset u64 + căn 16).
//     B4. Inspect bundle v9 + Zstd: hiện "nén zstd" + "Xin chao Unity 6!".
//     B5. Đối chứng LZ4 v7 vẫn chạy: hiện "nén lz4" + "Xin chao 9 Kings!".
//     B6. Bundle mã hoá bị từ chối: banner #error hiện "Inspect Unity
//         lỗi: E_ENCRYPTED: ..." kèm "mã hóa", không crash, không "Lỗi
//         không rõ".
//     B7. 390px không tràn ngang, không pageerror cả phiên.
//  KHÔNG thử được Safari thật (không có BrowserStack/LambdaTest sẵn):
//  fzstd + v9 + Zstd trên Safari thật, nút Chia sẻ/Lưu sau Inspect,
//  vẫn cần Joseph thử.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import zlib from "node:zlib";
import { execFileSync, spawnSync } from "node:child_process";

const ROOT = decodeURIComponent(new URL("..", import.meta.url).pathname).replace(/\/$/, "");
const SITE = ROOT;
const PB3 = SITE + "/pb3";
const EXP = ROOT + "/exp";
const FX_ZIP = EXP + "/fixtures/extract_select_fixture.zip";
const FX_FRAME = EXP + "/fixtures/c5_zstd_frame.bin";
const FX_ZSTD = EXP + "/fixtures/c5_bundle_zstd.unity3d";
const FX_V9 = EXP + "/fixtures/c5_bundle_v9.unity3d";
const FX_V9_ZSTD = EXP + "/fixtures/c5_bundle_v9_zstd.unity3d";
const FX_LZ4 = EXP + "/fixtures/c5_bundle_lz4_control.unity3d";
const FX_ENC = EXP + "/fixtures/c5_bundle_encrypted.unity3d";
const FRAME_PLAIN = "Xin chao BearTea - cau noi Zstd thu nghiem 12345";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ================= S. Tĩnh + CPython =================
const unitySrc = fs.readFileSync(PB3 + "/sdk/unity.py", "utf8");
const bridgeSrc = fs.readFileSync(PB3 + "/bridge.py", "utf8");
const workerSrc = fs.readFileSync(SITE + "/worker.js", "utf8");
const swSrc = fs.readFileSync(SITE + "/sw.js", "utf8");
const indexSrc = fs.readFileSync(SITE + "/index.html", "utf8");
const fzstdSrc = fs.readFileSync(SITE + "/fzstd/index.mjs", "utf8");
const fzstdLicense = fs.readFileSync(SITE + "/fzstd/LICENSE", "utf8");

{ // S1. node --check + vendoring fzstd + mốc C5 trong code
  const t0 = performance.now();
  const bad = [];
  for (const f of ["worker.js", "sw.js", "fzstd/index.mjs"]) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  const ver = (swSrc.match(/const CACHE_VERSION = "([^"]+)"/) || [])[1];
  const listMatch = swSrc.match(/const PRECACHE_URLS = \[([\s\S]*?)\];/);
  const listed = listMatch ? [...listMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : [];
  const checks = {
    nodeCheck: bad.length === 0,
    fzstdOnDisk: fs.existsSync(SITE + "/fzstd/index.mjs") && fs.statSync(SITE + "/fzstd/index.mjs").size === 24407,
    fzstdExportsDecompress: /export function decompress\(/.test(fzstdSrc),
    licenseMIT: fzstdLicense.startsWith("MIT License") && fzstdLicense.includes("Copyright (c) 2020 Arjun Barrett"),
    licenseNote: fs.existsSync(SITE + "/fzstd/README.md") && /MIT/.test(fs.readFileSync(SITE + "/fzstd/README.md", "utf8")) &&
      /101arrowz\/fzstd/.test(fs.readFileSync(SITE + "/fzstd/README.md", "utf8")),
    swVersionV48: ver === "v48-20261118",
    swPrecacheFzstd: listed.includes("fzstd/index.mjs"),
    swPrecacheCount130: listed.length === 130,
    workerDynamicImport: /await import\("\.\/fzstd\/index\.mjs"\)/.test(workerSrc),
    workerExposesBridge: /globalThis\.pb3_zstd_decompress = \(data, expectedSize\)/.test(workerSrc),
    workerFallbackNull: /globalThis\.pb3_zstd_decompress = null/.test(workerSrc),
    workerNoStaticFzstdImport: !/^import .* from "\.\/fzstd\//m.test(workerSrc),
    unityV9Supported: /SUPPORTED_BUNDLE_VERSIONS = \(6, 7, 8, 9\)/.test(unitySrc),
    unityZstdCode5: /COMPRESSION_ZSTD = 5/.test(unitySrc) && /5: "zstd"/.test(unitySrc),
    unityJsBridge: /getattr\(_js, "pb3_zstd_decompress", None\)/.test(unitySrc),
    unityJsBridgeClearError: /chỉ có trên web BearTea/.test(unitySrc),
    unityV9OffsetRead: /offset = ir\.u64\(\) if version >= 9 else None/.test(unitySrc),
    unityV9OffsetUse: /start = block_base \+ block\.offset/.test(unitySrc),
    unityV9WriteGuard: /if version == 9:\s*\n\s*raise PB3Error\("E_UNSUPPORTED_FORMAT"/.test(unitySrc),
    unityZstdWriteGuard: /if compression == "zstd":\s*\n\s*raise PB3Error\("E_UNSUPPORTED_FORMAT"/.test(unitySrc),
    unityPatchV9Refuse: /bundle UnityFS v9 chỉ đọc — chưa hỗ trợ patch/.test(unitySrc),
    unityPatchZstdToNone: /rebuild_compression = \("none" if bundle\.compression_name == "zstd"/.test(unitySrc),
    unityMatrixV9Zstd: /UnityFS v9 \(bundle\)/.test(unitySrc) && /Zstd \(block bundle, mã nén 5\)/.test(unitySrc),
    bridgeKeepsBundleError: /bytes\(data\[:7\]\) == u\.UNITYFS_MAGIC/.test(bridgeSrc) &&
      /giữ nguyên lỗi thật/.test(bridgeSrc),
    indexNoteV9: /UnityFS v6–v9 — v9 chỉ đọc/.test(indexSrc) && /block nén Zstd đọc được qua fzstd/.test(indexSrc),
  };
  const missing = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_fzstd_v9_zstd_markers", missing.length === 0,
    "node --check OK: worker.js, sw.js, fzstd/index.mjs" + (bad.length ? " | LỖI: " + bad.join(", ") : "") +
    "; fzstd/index.mjs=24407B, export decompress, LICENSE MIT © 2020 Arjun Barrett + README ghi công 101arrowz/fzstd; " +
    "CACHE_VERSION=" + ver + " (cần v48-20261118); precache có fzstd/index.mjs=" + listed.includes("fzstd/index.mjs") +
    " (tổng " + listed.length + " mục, cần 130); worker.js dynamic import fzstd + globalThis.pb3_zstd_decompress + fallback null khi lỗi; " +
    "sdk/unity.py: " + Object.entries(checks).map(([k, v]) => k + "=" + v).join(", ") +
    (missing.length ? " | THIẾU: " + missing.join(", ") : ""),
    performance.now() - t0);
}

function runUnityTests(pythonExe) {
  const r = spawnSync(pythonExe, ["-m", "unittest", "tests.test_unity.TestBundleV9Zstd", "-v"],
    { encoding: "utf8", cwd: PB3, timeout: 300000 });
  const out = (r.stdout || "") + (r.stderr || "");
  const ran = (out.match(/Ran (\d+) tests?/) || [])[1];
  const skipped = (out.match(/OK \(skipped=(\d+)\)/) || [])[1] || (out.includes("OK") ? "0" : null);
  const failed = /FAILED/.test(out);
  const oks = (out.match(/ \.\.\. ok/g) || []).length;
  return { status: r.status, out, ran: ran ? Number(ran) : null,
    skipped: skipped != null ? Number(skipped) : null, failed, oks };
}

{ // S2a. CPython hệ thống: 13 test C5, 0 FAIL (2 test cần zstandard sẽ SKIP)
  const t0 = performance.now();
  let detail = "", ok = false;
  try {
    const r = runUnityTests("python3");
    ok = r.status === 0 && !r.failed && r.ran === 13;
    detail = "python3 -m unittest tests.test_unity.TestBundleV9Zstd -v: Ran " + r.ran +
      " test, " + r.oks + " ok, skipped=" + r.skipped + ", exit=" + r.status +
      (r.skipped ? " (2 test parse/patch Zstd đầy đủ SKIP vì hệ thống thiếu gói `zstandard` — ghi rõ, không tính PASS giả; chạy đủ ở S2b)" : "") +
      (r.failed ? " | OUTPUT:\n" + r.out.slice(-2000) : "");
  } catch (e) { detail = "lỗi chạy unittest: " + String(e && e.message ? e.message : e); }
  rec("python_c5_tests_system", ok, detail, performance.now() - t0);
}

function findZstdPython() {
  for (const p of ["/tmp/zstdvenv/bin/python", "/tmp/c5_zstdvenv/bin/python"]) {
    try {
      execFileSync(p, ["-c", "import zstandard, lz4"], { stdio: "pipe" });
      return p;
    } catch (e) { /* thử tiếp */ }
  }
  try {
    execFileSync("python3", ["-m", "venv", "/tmp/c5_zstdvenv"], { stdio: "pipe", timeout: 180000 });
    execFileSync("/tmp/c5_zstdvenv/bin/pip", ["install", "-q", "zstandard", "lz4"],
      { stdio: "pipe", timeout: 300000 });
    execFileSync("/tmp/c5_zstdvenv/bin/python", ["-c", "import zstandard, lz4"], { stdio: "pipe" });
    return "/tmp/c5_zstdvenv/bin/python";
  } catch (e) { return null; }
}

{ // S2b. Venv có zstandard: cùng 13 test, kỳ vọng 13/13, 0 skip
  const t0 = performance.now();
  let detail = "", ok = false;
  const py = findZstdPython();
  if (!py) {
    detail = "KHÔNG tạo được venv có zstandard (không có /tmp/zstdvenv và pip install thất bại — có thể mất mạng): " +
      "2 test parse/patch Zstd đầy đủ CHƯA KIỂM CHỨNG bằng CPython ở đây (chỉ có browser B2/B4 chứng minh đường giải nén thật).";
  } else {
    try {
      const r = runUnityTests(py);
      ok = r.status === 0 && !r.failed && r.ran === 13 && r.skipped === 0;
      detail = "venv " + py + ": Ran " + r.ran + " test, " + r.oks + " ok, skipped=" + r.skipped +
        ", exit=" + r.status + " (bộ giải `zstandard` đứng chỗ fzstd trong module `js` giả)" +
        (r.failed ? " | OUTPUT:\n" + r.out.slice(-2000) : "");
    } catch (e) { detail = "lỗi chạy unittest bằng " + py + ": " + String(e && e.message ? e.message : e); }
  }
  rec("python_c5_tests_with_zstandard", ok, detail, performance.now() - t0);
}

{ // S3. Shell tĩnh sau C5 (ghi số thật; không đặt ngưỡng PASS/FAIL mới)
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
  const fz = fs.readFileSync(SITE + "/fzstd/index.mjs");
  const fzGz = zlib.gzipSync(fz, { level: 9 }).length;
  rec("shell_size_after_c5", true,
    "shell lần tải đầu sau C5: " + lines.join("; ") + " | TỔNG raw=" + raw + "B (" +
    (raw / 1024).toFixed(1) + "KB), gzip=" + gz + "B (" + (gz / 1024).toFixed(1) +
    "KB). fzstd/index.mjs KHÔNG nằm trong shell lần tải đầu (worker import lúc boot): raw=" +
    fz.length + "B gzip=" + fzGz + "B; cộng vào tổng tải sau boot thì raw=" + (raw + fz.length) +
    "B. Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C5 không đổi kết luận.",
    performance.now() - t0);
}

function readBundleHeader(buf) {
  // UnityFS: "UnityFS\0" + version u32 BE + 2 cstring + total u64 + comp/uncomp u32 + flags u32.
  if (buf.subarray(0, 7).toString() !== "UnityFS") return null;
  const version = buf.readUInt32BE(8);
  let p = 12;
  const cstr = () => { const e = buf.indexOf(0, p); const s = buf.subarray(p, e).toString(); p = e + 1; return s; };
  const player = cstr(), engine = cstr();
  const total = buf.readBigUInt64BE(p);
  const compInfo = buf.readUInt32BE(p + 8), uncompInfo = buf.readUInt32BE(p + 12);
  const flags = buf.readUInt32BE(p + 16);
  return { version, player, engine, total: Number(total), compInfo, uncompInfo,
    flags, compression: flags & 0x3F, headerEnd: p + 20 };
}

{ // S4. Header fixture (đọc bằng Node) + blocks-info v9 có offset u64
  const t0 = performance.now();
  const hs = {};
  for (const [k, f] of [["zstd", FX_ZSTD], ["v9", FX_V9], ["v9zstd", FX_V9_ZSTD],
                        ["lz4", FX_LZ4], ["enc", FX_ENC]]) {
    hs[k] = readBundleHeader(fs.readFileSync(f));
  }
  let v9offsets = null, v9detail = "";
  try {
    const buf = fs.readFileSync(FX_V9);
    const h = hs.v9;
    const infoStart = (h.headerEnd + 15) & ~15; // header căn 16 (v>=7)
    const info = buf.subarray(infoStart, infoStart + h.compInfo); // comp 0: info thô
    const nBlocks = info.readInt32BE(16);
    v9offsets = [];
    let q = 20;
    for (let i = 0; i < nBlocks; i++) {
      const unc = info.readUInt32BE(q), comp = info.readUInt32BE(q + 4);
      const fl = info.readUInt16BE(q + 8), off = Number(info.readBigUInt64BE(q + 10));
      v9offsets.push({ unc, comp, fl, off });
      q += 18;
    }
    v9detail = "blocks-info v9 (không nén): " + nBlocks + " block " + JSON.stringify(v9offsets);
  } catch (e) { v9detail = "lỗi đọc blocks-info v9: " + String(e && e.message ? e.message : e); }
  const checks = {
    zstdV7Comp5: hs.zstd && hs.zstd.version === 7 && hs.zstd.compression === 5,
    v9Version9Comp0: hs.v9 && hs.v9.version === 9 && hs.v9.compression === 0,
    v9zstdVersion9Comp5: hs.v9zstd && hs.v9zstd.version === 9 && hs.v9zstd.compression === 5,
    lz4Control: hs.lz4 && hs.lz4.version === 7 && hs.lz4.compression === 2,
    encFlag1400: hs.enc && hs.enc.version === 8 && (hs.enc.flags & 0x1400) !== 0,
    v9Offsets: Array.isArray(v9offsets) && v9offsets.length === 2 &&
      v9offsets[0].off === 0 && v9offsets[1].off === 3776 &&
      v9offsets[1].off > v9offsets[0].comp,
  };
  const missing = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_fixture_headers", missing.length === 0,
    "header fixture: " + Object.entries(hs).map(([k, h]) => k + "=v" + (h && h.version) +
      "/comp" + (h && h.compression) + "/flags0x" + (h && h.flags.toString(16))).join(", ") +
    "; " + v9detail + " (offset block 2 = 3776 = căn 16 của 3774 — chứng minh block không nằm sát nhau, phải đọc bằng offset)" +
    (missing.length ? " | SAI: " + missing.join(", ") : ""),
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
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  await page.goto(BASE + "/index.html");
  await page.waitForFunction(
    () => { const el = document.getElementById("status"); return el && el.textContent.includes("Chưa khởi động"); },
    null, { timeout: 60000 });
  const openUnityTab = async () => {
    await page.click("#btn-nav-tools");
    await page.click("#btn-unity");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-unity"); return el && !el.hidden; },
      null, { timeout: 30000 });
  };
  const inspectFile = async (filePath) => {
    await page.evaluate(() => {
      document.getElementById("unity-result").innerHTML = "";
      document.getElementById("error").hidden = true;
    });
    await page.setInputFiles("#file-input", filePath);
    await page.waitForFunction(() => !document.getElementById("file-info").hidden, null, { timeout: 60000 });
    await page.click("#btn-unity-run");
    await page.waitForFunction(
      () => document.getElementById("unity-result").textContent.trim().length > 0 ||
        !document.getElementById("error").hidden,
      null, { timeout: 120000 });
    return {
      result: await elText(page, "unity-result"),
      home: await elText(page, "unity-info"),
      error: await page.evaluate(() => {
        const el = document.getElementById("error");
        return el.hidden ? "" : el.textContent;
      }),
    };
  };

  { // B0. Boot tới "Sẵn sàng"
    const t0 = performance.now();
    try {
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 180000 });
    } catch (e) {
      await page.goto(BASE + "/index.html");
      await page.setInputFiles("#file-input", FX_ZIP);
      await page.waitForFunction(
        () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
        null, { timeout: 240000 });
    }
    rec("browser_boot", true, "boot tới Sẵn sàng (390px)", performance.now() - t0);
  }
  { // B1. fzstd trực tiếp trong trang: giải nén frame mẫu đúng từng byte; rác thì ném lỗi
    const t0 = performance.now();
    const b64 = fs.readFileSync(FX_FRAME).toString("base64");
    const r = await page.evaluate(async (b64frame) => {
      const { decompress } = await import("./fzstd/index.mjs");
      const bin = atob(b64frame);
      const frame = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) frame[i] = bin.charCodeAt(i);
      const out = decompress(frame);
      let garbageThrows = false;
      try { decompress(new Uint8Array([1, 2, 3, 4, 5])); } catch (e) { garbageThrows = true; }
      return { text: new TextDecoder().decode(out), len: out.length, garbageThrows };
    }, b64);
    rec("browser_fzstd_direct", r.text === FRAME_PLAIN && r.len === FRAME_PLAIN.length && r.garbageThrows === true,
      "import ./fzstd/index.mjs trong trang -> decompress(c5_zstd_frame.bin 57B) = " + JSON.stringify(r.text) +
      " (" + r.len + " byte, khớp từng byte câu mẫu); dữ liệu rác [1,2,3,4,5] ném lỗi=" + r.garbageThrows + " (không trả rác)",
      performance.now() - t0);
  }
  await openUnityTab();
  { // B2. Inspect bundle Zstd v7 qua UI (cầu nối JS->Python thật)
    const t0 = performance.now();
    const r = await inspectFile(FX_ZSTD);
    rec("browser_inspect_zstd_v7", r.result.includes("nén zstd") && r.result.includes("Xin chao 9 Kings!") &&
      r.result.includes("2 entry") && r.home.includes("bundle zstd") && r.error === "",
      "Inspect c5_bundle_zstd.unity3d (v7, mã nén 5) -> unity-result=" + JSON.stringify(r.result.slice(0, 300)) +
      "; thẻ Trang chủ=" + JSON.stringify(r.home.slice(0, 160)) + "; #error=" + JSON.stringify(r.error) +
      " (TextAsset đọc được = block Zstd đã qua fzstd trong worker rồi về Python qua js.pb3_zstd_decompress)",
      performance.now() - t0);
  }
  { // B3. Inspect bundle v9 (không nén, đọc bằng offset)
    const t0 = performance.now();
    const r = await inspectFile(FX_V9);
    rec("browser_inspect_v9_none", r.result.includes("nén none") && r.result.includes("Xin chao Unity 6!") &&
      r.result.includes("2 entry") && r.error === "",
      "Inspect c5_bundle_v9.unity3d (v9, mã nén 0, blocks-info có offset u64) -> unity-result=" +
      JSON.stringify(r.result.slice(0, 300)) + "; #error=" + JSON.stringify(r.error) +
      " (fixture header version=9 đã kiểm ở S4; parse thành công trên trình duyệt thật chứng minh v9 đọc được, trước C5 bị từ chối E_UNSUPPORTED_FORMAT)",
      performance.now() - t0);
  }
  { // B4. Inspect bundle v9 + Zstd (cả hai tính năng mới cùng lúc)
    const t0 = performance.now();
    const r = await inspectFile(FX_V9_ZSTD);
    rec("browser_inspect_v9_zstd", r.result.includes("nén zstd") && r.result.includes("Xin chao Unity 6!") &&
      r.result.includes("2 entry") && r.error === "",
      "Inspect c5_bundle_v9_zstd.unity3d (v9 + mã nén 5) -> unity-result=" + JSON.stringify(r.result.slice(0, 300)) +
      "; #error=" + JSON.stringify(r.error),
      performance.now() - t0);
  }
  { // B5. Đối chứng LZ4 v7 vẫn chạy như cũ
    const t0 = performance.now();
    const r = await inspectFile(FX_LZ4);
    rec("browser_inspect_lz4_control", r.result.includes("nén lz4") && r.result.includes("Xin chao 9 Kings!") &&
      r.result.includes("2 entry") && r.error === "",
      "Inspect c5_bundle_lz4_control.unity3d (v7, LZ4) -> unity-result=" + JSON.stringify(r.result.slice(0, 200)) +
      "; #error=" + JSON.stringify(r.error) + " (hồi quy: đường đọc cũ không vỡ)",
      performance.now() - t0);
  }
  { // B6. Bundle mã hoá bị từ chối với lý do rõ ràng
    const t0 = performance.now();
    const r = await inspectFile(FX_ENC);
    rec("browser_encrypted_refused_clearly", r.error.includes("E_ENCRYPTED") && r.error.includes("mã hóa") &&
      !r.error.includes("Lỗi không rõ") && r.result.trim() === "",
      "Inspect c5_bundle_encrypted.unity3d (cờ 0x1400) -> banner #error=" + JSON.stringify(r.error) +
      "; unity-result trống (không parse được gì — từ chối sạch, không crash, không \"Lỗi không rõ\")",
      performance.now() - t0);
  }
  { // B7. 390px không tràn + không pageerror
    const t0 = performance.now();
    const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    rec("browser_390_no_overflow_no_errors", o.sw <= o.cw + 1 && pageErrors.length === 0,
      "390px: scrollWidth=" + o.sw + " clientWidth=" + o.cw + " (không tràn); pageerror cả phiên: " +
      (pageErrors.length ? JSON.stringify(pageErrors) : "không có"),
      performance.now() - t0);
  }
  await ctx.close();

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
const mdLines = ["# Kiểm chứng mục C5 — Zstd đọc qua fzstd + ghi block không nén, UnityFS v9 chỉ đọc",
  "",
  "Ngày: " + new Date().toISOString() + " (giờ UTC). Harness: exp/verify_analysis_c5.mjs.",
  "",
  "## Kết quả: " + passed + "/" + total + " PASS" + (failures ? " — FAIL: " + Object.keys(results).filter((k) => !results[k].ok).join(", ") : ""),
  "",
  ...Object.entries(results).map(([k, v]) => "### " + k + ": " + (v.ok ? "PASS" : "FAIL") + (v.seconds != null ? " (" + v.seconds + "s)" : "") + "\n" + v.detail + "\n"),
  "## File thay đổi",
  "- `site/fzstd/index.mjs` (mới, vendored): fzstd 0.1.1, bản ESM `esm/index.mjs` của gói npm (24.407 byte, sha256 1a901fc5…), export `decompress`; MIT © 2020 Arjun Barrett — xem `site/fzstd/LICENSE` (nguyên văn) + `site/fzstd/README.md` (ghi công + nguồn + cách dùng). Same-origin như Pyodide, không tải từ CDN lúc chạy.",
  "- `site/pb3/sdk/unity.py`: `SUPPORTED_BUNDLE_VERSIONS` thêm 9 (chỉ đọc); `COMPRESSION_NAMES` thêm 5=\"zstd\" (quy ước mở rộng của PB3 — UnityPy master 2026-11-16 và tài liệu Unity không có cờ Zstd; mã 5 là mã trống tiếp theo sau LZHAM=4); `BundleBlock.offset` (u64, v9); `UnityBackend.decompress` thêm nhánh Zstd qua `js.pb3_zstd_decompress` (ngoài trình duyệt báo E_UNSUPPORTED_FORMAT rõ ràng \"cần fzstd trên web\"); `compress`/`build_bundle` từ chối ghi Zstd và ghi v9 với lý do rõ ràng; `parse_bundle` + `stream_bundle_entries` đọc v9 bằng offset u64 (block căn 16, không đọc tuần tự); `apply_patch` từ chối patch bundle v9 sớm, bundle gốc Zstd thì dựng lại bằng compression \"none\" (cờ nén 0); `support_matrix()` thêm 2 hàng v9/Zstd.",
  "- `site/pb3/bridge.py`: `_parse_unity_bytes` không còn nuốt lỗi của bundle UnityFS thật — file có magic UnityFS mà `parse_bundle` lỗi (mã hoá / version / nén chưa hỗ trợ / hỏng) thì giữ nguyên lỗi thật thay vì rơi xuống `parse_serialized` rồi báo sai (lỗi C5 phát hiện: bundle mã hoá bị báo E_UNSUPPORTED_FORMAT chung chung thay vì E_ENCRYPTED). Chỉ rơi xuống khi không phải UnityFS (file .assets độc lập).",
  "- `site/pb3/sdk/support_matrix.py`: dòng fixture-only ghi \"UnityFS v6/v7/v8/v9 + SerializedFile v15–23 (v9 chỉ đọc, Zstd chỉ đọc trên web, fixture)\".",
  "- `site/worker.js`: boot dynamic import `./fzstd/index.mjs` rồi công bố `globalThis.pb3_zstd_decompress(data, expectedSize)` (đúng tiền lệ `js.pb3_google_translate`: phải gán globalThis thật); lỗi nạp thì gán null + báo trạng thái, boot không chết; frame không kèm content size thì thử lại với buffer đúng cỡ kỳ vọng.",
  "- `site/sw.js`: CACHE_VERSION v10 → v11-20261116; thêm `fzstd/index.mjs` vào PRECACHE_URLS (126 → 127 mục; sửa luôn comment cũ ghi sai 127 khi mới có 126).",
  "- `site/index.html`: ghi chú tab Inspect \"UnityFS v6–v9 — v9 chỉ đọc; block nén Zstd đọc được qua fzstd\".",
  "- `site/pb3/tests/test_unity.py`: lớp mới `TestBundleV9Zstd` (13 test) + `test_unsupported_version_raises` đổi v9 → v10 (v9 nay đã hỗ trợ).",
  "- `site/pb3/tests/test_polish.py`: `test_u1_container_per_entry_resilient_direct_hard_abort` — file v8 đổi nhãn thành v9 nay là v9 SAI CẤU TRÚC nên kỳ vọng E_CORRUPT thay vì E_UNSUPPORTED_FORMAT (v9 thật đã hỗ trợ đọc từ C5; hard-abort và dòng \"unsupported\" trong container giữ nguyên).",
  "- `exp/verify_item6.mjs`: danh sách precache kỳ vọng thêm `fzstd/index.mjs` (126 → 127 mục) — không thì A3 báo thừa; `shell_raw_under_100kb` vẫn FAIL đã biết (xem hồi quy).",
  "- `site/pb3/tests/fixtures/unity/build_fixtures.py`: builder thêm compression \"zstd\" (qua gói `zstandard`, chỉ lúc sinh) + version 9 (offset u64, block căn 16) + `block_split`; `main()` sinh thêm `sample_v9.unity3d`, `sample_zstd.unity3d`, `sample_v9_zstd.unity3d` (thiếu `zstandard` thì bỏ qua Zstd và in rõ lý do).",
  "- `site/pb3/tests/fixtures/unity/sample_v9.unity3d` (7.790 byte), `sample_zstd.unity3d` (532 byte), `sample_v9_zstd.unity3d` (842 byte) (mới); `nested.ipa`/`nested_v8.ipa` sinh lại (chỉ khác timestamp ZIP, nội dung bundle giữ nguyên — đã kiểm chứng sha256 các fixture cũ khác không đổi).",
  "- Harness + fixture mới: `exp/verify_analysis_c5.mjs`, `exp/build_c5_fixtures.py`, `exp/fixtures/c5_bundle_zstd.unity3d`, `c5_bundle_v9.unity3d`, `c5_bundle_v9_zstd.unity3d`, `c5_bundle_lz4_control.unity3d`, `c5_bundle_encrypted.unity3d`, `c5_zstd_frame.bin` (frame Zstd thật của câu mẫu, 57 byte).",
  "- Harness cũ ghim version SW v10 → v11 (theo luật đổi precache phải đổi version): `exp/verify_analysis_c3.mjs`, `exp/verify_analysis_c4.mjs`, `exp/check_item7_static.py`, `exp/check_item8_static.py`, `exp/verify_item8.mjs` (chuỗi ghi chi tiết).",
  "",
  "## Khảo sát trước khi làm (trạng thái cũ)",
  "- `sdk/unity.py` trước C5: chỉ hỗ trợ UnityFS v6/v7/v8, nén NONE/LZMA/LZ4/LZ4HC (mã 0–3) + LZHAM liệt kê nhưng không giải được (mã 4); version 9 và mã nén 5 đều bị từ chối E_UNSUPPORTED_FORMAT; đường ghi `build_bundle` giữ compression gốc của bundle (patch bundle LZ4/LZMA ghi lại bằng LZ4/LZMA — KHÔNG phải cờ 0); bundle mã hoá ĐÃ từ chối E_ENCRYPTED kèm flag (cả `parse_bundle` lẫn `stream_bundle_entries`) — C5 chỉ kiểm chứng lại, không sửa.",
  "- Khác biệt v9 (đối chiếu UnityPy master ngày 2026-11-16, `helpers/CompressionHelper.py` + `files/BundleFile.py`): CHỈ có BlockInfo thêm u64 `offset` sau cờ u16 và mỗi block nén căn 16 byte trong vùng block data (đọc bằng `base_offset + offset`); header vẫn căn 16 từ v7, entry vẫn (offset u64, size u64, flags u32, tên) nên file lớn đã có từ trước. UnityPy master KHÔNG có cờ nén Zstd nào (CompressionFlags chỉ 0–4) và tài liệu Unity 6000 chỉ liệt kê LZMA/LZ4/Uncompressed — mã 5 là quy ước mở rộng của PB3, chưa có bundle Unity thật nào được thấy dùng Zstd.",
  "- Cầu nối JS→Python: đúng tiền lệ Google Translate — worker.js gán hàm vào `globalThis`, Python gọi qua `import js` + `getattr(js, ...)`. Khác biệt: fzstd là hàm ĐỒNG BỘ (không Promise như translate), Pyodide chuyển bytes Python → Uint8Array cho JS và trả Uint8Array về dạng JsProxy (dùng `bytes(...)`, dự phòng `to_py()`).",
  "",
  "## Ranh giới v9 / Zstd (đã thực thi trong code)",
  "- v9 CHỈ ĐỌC: parse + stream được; `build_bundle(version=9)` từ chối E_UNSUPPORTED_FORMAT; `apply_patch` trên bundle v9 từ chối sớm (vì patch luôn dựng lại container, kể cả PATCH_INPLACE).",
  "- Zstd CHỈ ĐỌC trên web: parse/stream qua fzstd; ngoài trình duyệt (CPython/iSH — không có module `js` hoặc worker chưa nạp được fzstd) báo E_UNSUPPORTED_FORMAT \"Block Zstd cần bộ giải nén fzstd — chỉ có trên web BearTea.\"; ghi Zstd từ chối, bên gọi dựng lại bằng block không nén (cờ nén 0) — đã kiểm chứng bằng test `test_patch_zstd_bundle_rebuilds_uncompressed` (bundle ra compression \"none\", mọi block flags & 0x3F == 0).",
  "- Đường ghi bundle thường (LZMA/LZ4/none) KHÔNG đổi: vẫn giữ compression gốc như trước C5 (chỉ riêng nguồn Zstd mới ép về \"none\" vì không có encoder).",
  "- Bundle mã hoá: từ chối E_ENCRYPTED kèm flag (đã có từ trước, C5 kiểm chứng qua UI: banner \"Inspect Unity lỗi: E_ENCRYPTED: Dữ liệu hoặc binary được mã hóa/bảo vệ.\").",
  "",
  "## Giới hạn thật còn lại",
  "- Mã nén Zstd = 5 là quy ước của PB3, chưa có bằng chứng bundle Unity thật nào dùng; mọi fixture Zstd/v9 đều TỔNG HỢP (sinh bằng builder đối chiếu UnityPy), chưa kiểm chứng trên bundle v9/Zstd thật — cần Joseph hoặc game thật dùng Unity 6.2+ mới có.",
  "- Giải nén Zstd trên web phụ thuộc file `fzstd/index.mjs` nạp được lúc boot; nếu file thiếu (vd cache cũ, deploy lỗi) thì worker vẫn boot nhưng bundle Zstd báo lỗi rõ ràng thay vì đọc được — harness B1/B2 sẽ FAIL trong trường hợp đó (đúng hành vi).",
  "- Patch bundle v9 hoàn toàn chưa làm được (từ chối) — muốn patch v9 thì cần dựng lại bundle v9 (ghi offset u64 + căn 16), ngoài phạm vi C5.",
  "- Shell lần tải đầu vẫn vượt 100KB raw như mục 6 đã ghi (xem S3) — fzstd (24KB) nạp lúc boot nên không tính vào shell đầu, nhưng tổng tải sau boot tăng tương ứng.",
  "",
  "## Safari thật — chưa thử được",
  "- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.",
  "- CẦN JOSEPH (Safari thật, iPhone): mở trang sau khi lên live → Inspect 1 bundle (bất kỳ) để xem boot + fzstd có nạp được không; nếu có bundle Unity 6 mới (v9) thì thử Inspect xem có đọc được không; thử bundle mã hoá (nếu có) xem banner lỗi có hiện rõ \"E_ENCRYPTED\" không.",
  "",
];
fs.writeFileSync(EXP + "/analysis_c5_results.md", mdLines.join("\n") + "\n");
fs.writeFileSync(EXP + "/analysis_c5_results.json", JSON.stringify(
  { when: new Date().toISOString(), total, passed, failures, results }, null, 2));
console.log("\n==== TỔNG: " + total + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
