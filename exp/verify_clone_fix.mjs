// verify_clone_fix.mjs — Kiểm chứng HOTFIX bug clone (Safari DataCloneError)
// + mojibake tên entry ZIP kiểu iOS + gợi ý ZIP-chứa-IPA.
//
// Điểm khác các harness cũ: KHÔNG gọi bridge trực tiếp. Harness nạp CHÍNH
// file site/app.js thật vào VM, nối nó với một Worker THẬT (worker_threads,
// file clone_fix_worker.mjs) qua postMessage + transfer list thật — đúng
// chuỗi đã lỗi trên Safari: chọn file -> Danh sách -> Inspect -> Trích xuất
// -> Chẩn đoán -> Font -> Nén. Sau mỗi thao tác kiểm tra buffer gốc vẫn còn
// nguyên (byteLength không đổi) và worker chỉ nhận file đúng 1 lần.
//
// Chạy: node verify_clone_fix.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: clone_fix_results.md + clone_fix_results.json
import { Worker } from "node:worker_threads";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";
import vm from "node:vm";
import fs from "node:fs";
import { execFileSync, spawnSync } from "node:child_process";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"} (${sec(ms)}s)\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- Fixture: ZIP kiểu "Nén" trong Files iOS ----------
// Tên entry ghi bằng byte UTF-8, cờ bit 11 KHÔNG đặt (stored, không nén).
function buildIosZip(entries) {
  const local = [], central = [];
  let off = 0;
  for (const [name, data] of entries) {
    const nb = Buffer.from(name, "utf-8");
    const crc = requireCrc(data);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(0, 6);
    lh.writeUInt16LE(0, 8); lh.writeUInt16LE(0x21, 10); lh.writeUInt16LE(0, 12);
    lh.writeUInt32LE(crc, 14); lh.writeUInt32LE(data.length, 18); lh.writeUInt32LE(data.length, 22);
    lh.writeUInt16LE(nb.length, 26); lh.writeUInt16LE(0, 28);
    local.push(lh, nb, data);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6);
    ch.writeUInt16LE(0, 8); ch.writeUInt16LE(0, 10); ch.writeUInt16LE(0x21, 12); ch.writeUInt16LE(0, 14);
    ch.writeUInt32LE(crc, 16); ch.writeUInt32LE(data.length, 20); ch.writeUInt32LE(data.length, 24);
    ch.writeUInt16LE(nb.length, 28); ch.writeUInt16LE(0, 30); ch.writeUInt16LE(0, 32);
    ch.writeUInt16LE(0, 34); ch.writeUInt16LE(0, 36); ch.writeUInt32LE(0, 38); ch.writeUInt32LE(off, 42);
    central.push(ch, nb);
    off += 30 + nb.length + data.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10); eocd.writeUInt32LE(cdSize, 12);
  eocd.writeUInt32LE(off, 16);
  return Buffer.concat([...local, ...central, eocd]);
}
function requireCrc(data) {
  // CRC32 (bảng chuẩn) — tránh phụ thuộc module ngoài.
  let table = requireCrc._t;
  if (!table) {
    table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    requireCrc._t = table;
  }
  let crc = 0xffffffff;
  for (const b of data) crc = table[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

const IPA_NAME = "\u200e 9 Kings_v1.0.5-AppAssassin.ipa";
const IOS_ZIP = buildIosZip([[IPA_NAME, Buffer.from("PKfake")], ["__MACOSX/._" + IPA_NAME, Buffer.from("x")]]);
const PLAIN_IPA_ZIP = buildIosZip([["game.ipa", Buffer.from("PKfake")]]);
const NORMAL_ZIP = buildIosZip([["a.txt", Buffer.from("hello")], ["b.txt", Buffer.from("world")]]);
const OTHER_ZIP = buildIosZip([["x.txt", Buffer.from("x")]]);
fs.mkdirSync(EXP + "/out", { recursive: true });
fs.writeFileSync(EXP + "/out/ios.zip", IOS_ZIP);

// ---------- A. Đối chứng: logic CŨ (transfer cùng buffer mỗi lần) lỗi thật ----------
{
  const t0 = performance.now();
  let detail = "", ok = false;
  const echo = new Worker(
    `const { parentPort } = require("node:worker_threads");\n` +
    `parentPort.on("message", () => parentPort.postMessage({ ok: true }));\n`,
    { eval: true });
  try {
    const buf = new ArrayBuffer(1024);
    const once = new Promise((res) => echo.once("message", res));
    echo.postMessage({ n: 1 }, [buf]);
    await once;
    const afterFirst = buf.byteLength; // kỳ vọng 0: đã detached sau transfer 1
    let secondErr = "";
    try { echo.postMessage({ n: 2 }, [buf]); } catch (e) { secondErr = String(e && e.message ? e.message : e); }
    await sleep(100);
    ok = afterFirst === 0 && secondErr.length > 0;
    detail = `sau transfer lần 1 byteLength=${afterFirst} (kỳ vọng 0); transfer lần 2 ném: "${secondErr.slice(0, 160)}"`;
  } catch (e) { detail = "control lỗi ngoài dự kiến: " + String(e && e.message ? e.message : e); }
  await echo.terminate();
  rec("control_old_logic_detaches", ok, detail, performance.now() - t0);
}

// ---------- B. Nạp site/app.js THẬT + Worker THẬT ----------
// Stub DOM tối thiểu: mọi getElementById trả cùng 1 stub cho mỗi id.
const elements = new Map();
function makeEl(id) {
  return {
    id, textContent: "", innerHTML: "", hidden: false, value: "", checked: false,
    disabled: false, files: null, style: {}, _listeners: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    click() {}, remove() {},
  };
}
function $(id) {
  if (!elements.has(id)) elements.set(id, makeEl(id));
  return elements.get(id);
}
const fakeWorkers = [];
class FakeWorker {
  constructor(url, opts) {
    this.url = url; this.opts = opts; this.onmessage = null; this.onerror = null;
    this.nodeWorker = new Worker(EXP + "/clone_fix_worker.mjs");
    this.nodeWorker.on("message", (m) => { if (this.onmessage) this.onmessage({ data: m }); });
    this.nodeWorker.on("error", (e) => { if (this.onerror) this.onerror(e); });
    fakeWorkers.push(this);
  }
  postMessage(data, transfer) { this.nodeWorker.postMessage(data, transfer); }
  terminate() { return this.nodeWorker.terminate(); }
}
const sandbox = {
  document: {
    getElementById: $, createElement: () => makeEl("a"),
    body: { appendChild() {}, removeChild() {} },
  },
  Worker: FakeWorker,
  performance, setTimeout, clearTimeout, console,
  localStorage: { _m: {}, getItem(k) { return this._m[k] || null; }, setItem(k, v) { this._m[k] = String(v); } },
  confirm: () => true,
  Blob, URL, TextEncoder,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SITE + "/app.js", "utf8"), sandbox, { filename: "app.js" });
const nodeWorker = fakeWorkers[0].nodeWorker;

let probeSeq = 0;
const probeWaiters = new Map();
nodeWorker.on("message", (m) => {
  if (m && m.__probe && probeWaiters.has(m.__probe)) {
    probeWaiters.get(m.__probe)(m);
    probeWaiters.delete(m.__probe);
  }
});
function probe(op, extra) {
  const id = ++probeSeq;
  return new Promise((res) => {
    probeWaiters.set(id, res);
    nodeWorker.postMessage({ __probe: id, op, ...(extra || {}) });
    setTimeout(() => { if (probeWaiters.has(id)) { probeWaiters.delete(id); res({ timeout: true }); } }, 5000);
  });
}
async function fire(id, type, event) {
  const el = $(id);
  const list = el._listeners[type] || [];
  const out = [];
  for (const fn of list) out.push(await fn(event || { target: el }));
  return out;
}
async function waitFor(fn, timeoutMs, label) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (fn()) return true;
    await sleep(50);
  }
  throw new Error("hết giờ chờ " + label);
}
function fakeFile(name, ab, size) {
  return { name, size: size ?? ab.byteLength, lastModified: 1700000000000, arrayBuffer: async () => ab };
}

// Chờ boot xong ("Sẵn sàng — 20 plugin").
{
  const t0 = performance.now();
  await waitFor(() => $("status").textContent.includes("Sẵn sàng"), 10000, "boot");
  rec("boot_real_appjs", $("status").textContent.includes("20 plugin"),
    `status="${$("status").textContent}"`, performance.now() - t0);
}

// ---------- B1. Chuỗi Safari: list -> inspect -> extract -> diagnose -> font -> nén ----------
const ab = new Uint8Array(IOS_ZIP).buffer; // ArrayBuffer gốc, byteLength = cỡ file
{
  const t0 = performance.now();
  const f = fakeFile("game.zip", ab);
  $("file-input").files = [f];
  await fire("file-input", "change", { target: $("file-input") });
  await sleep(100);
  const infoOk = $("file-info").textContent.includes("game.zip");
  await fire("btn-list-run", "click");
  const listOk = $("list-result").innerHTML.includes("9 Kings") && !$("list-result").innerHTML.includes("ΓÇÄ");
  const hintOk = !$("list-ipa-hint").hidden && $("list-ipa-hint").textContent.includes("đổi tên .ipa thành .zip");
  const intact1 = ab.byteLength === IOS_ZIP.length;
  $("unity-ipa-hint").hidden = true; $("unity-ipa-hint").textContent = ""; // trạng thái ban đầu như HTML thật
  await fire("btn-unity-run", "click");
  // Stub DOM: đặt đúng trạng thái ban đầu như HTML thật (hidden) rồi chờ
  // theo NỘI DUNG gợi ý, vì maybeShowIpaZipHint chạy bất đồng bộ.
  await waitFor(() => $("unity-ipa-hint").textContent.includes("đổi tên .ipa thành .zip"), 3000, "unity hint");
  const unityHintOk = $("unity-ipa-hint").textContent.includes("đổi tên .ipa thành .zip");
  const intact2 = ab.byteLength === IOS_ZIP.length;
  await fire("btn-extract-run", "click");
  const extractOk = $("extract-result").innerHTML.includes("extract");
  const intact3 = ab.byteLength === IOS_ZIP.length;
  await fire("btn-diagnose-run", "click");
  const diagOk = $("diagnose-result").innerHTML.includes("zip");
  await fire("btn-font-scan", "click");
  const fontOk = $("font-result").innerHTML.includes("142");
  await fire("btn-compress-run", "click");
  const compOk = $("compress-result").innerHTML.includes("Gốc");
  const intactFinal = ab.byteLength === IOS_ZIP.length;
  const st = await probe("__state");
  const receivedOnce = st.receivedCount === 1 && st.receivedLog.length === 1 && st.receivedLog[0] === "game.zip";
  rec("sequence_list_inspect_extract_diag_font_compress",
    infoOk && listOk && hintOk && unityHintOk && extractOk && diagOk && fontOk && compOk &&
    intact1 && intact2 && intact3 && intactFinal && receivedOnce,
    `list=${listOk} hint_list=${hintOk} hint_inspect=${unityHintOk} extract=${extractOk} diagnose=${diagOk} font=${fontOk} compress=${compOk} ` +
    `buffer_nguyen_sau_moi_op=${intact1 && intact2 && intact3 && intactFinal} (byteLength=${ab.byteLength}/${IOS_ZIP.length}) ` +
    `worker_nhan_file=${st.receivedCount} lần (kỳ vọng 1): ${JSON.stringify(st.receivedLog)}`,
    performance.now() - t0);
}

// ---------- B2. Chọn file mới -> cờ reset -> gửi lại được ----------
{
  const t0 = performance.now();
  const ab2 = new Uint8Array(OTHER_ZIP).buffer;
  const f2 = fakeFile("game2.zip", ab2, OTHER_ZIP.length);
  $("file-input").files = [f2];
  await fire("file-input", "change", { target: $("file-input") });
  await sleep(100);
  await fire("btn-list-run", "click");
  const st = await probe("__state");
  const ok = $("list-result").innerHTML.includes("x.txt") && !$("list-result").innerHTML.includes("9 Kings") &&
    st.receivedCount === 2 && st.receivedLog[1] === "game2.zip" && ab2.byteLength === OTHER_ZIP.length;
  rec("reselect_resets_flag", ok,
    `list game2=${$("list-result").innerHTML.includes("x.txt")} worker_nhan=${st.receivedCount} lần: ${JSON.stringify(st.receivedLog)} buffer2_nguyen=${ab2.byteLength === OTHER_ZIP.length}`,
    performance.now() - t0);
  // Giữ ab2/file game2 cho mục lỗi: chọn lại game.zip để chuỗi sau dùng file đầu.
  const f1 = fakeFile("game.zip", ab);
  $("file-input").files = [f1];
  await fire("file-input", "change", { target: $("file-input") });
  await sleep(100);
  await fire("btn-list-run", "click");
}

// ---------- B3. runOp lỗi -> cờ reset -> thử lại gửi lại file ----------
{
  const t0 = performance.now();
  const before = await probe("__state");
  await probe("__fail_next", { fn: "font_scan" });
  await fire("btn-font-scan", "click");
  const errShown = !$("error").hidden && $("error").textContent.includes("Quét font");
  const intact = ab.byteLength === IOS_ZIP.length;
  await fire("btn-font-scan", "click");
  const after = await probe("__state");
  const ok = errShown && intact && $("font-result").innerHTML.includes("142") &&
    after.receivedCount === before.receivedCount + 1;
  rec("error_resets_flag_retry_resends", ok,
    `loi_hien=${errShown} buffer_nguyen=${intact} thu_lai_thanh_cong=${$("font-result").innerHTML.includes("142")} ` +
    `worker_nhan_truoc=${before.receivedCount} sau=${after.receivedCount} (kỳ vọng +1)`,
    performance.now() - t0);
}

// ---------- B4. Batch: gửi 1 lần, chạy lại lần 2 không gửi nữa ----------
{
  const t0 = performance.now();
  const bb1 = new Uint8Array(PLAIN_IPA_ZIP).buffer;
  const bb2 = new Uint8Array(NORMAL_ZIP).buffer;
  $("batch-files").files = [fakeFile("b1.zip", bb1), fakeFile("b2.zip", bb2)];
  await fire("batch-files", "change", { target: $("batch-files") });
  await sleep(100);
  $("batch-manifest").value = '[{"path": "b1.zip", "mode": "extract"}]';
  $("batch-dryrun").checked = true;
  $("batch-resume").checked = false;
  await fire("btn-batch-run", "click");
  const run1Ok = $("batch-result").innerHTML.includes("Batch");
  const st1 = await probe("__state");
  await fire("btn-batch-run", "click");
  const run2Ok = $("batch-result").innerHTML.includes("Batch");
  const st2 = await probe("__state");
  const ok = run1Ok && run2Ok && st1.receivedCount === st2.receivedCount &&
    st1.receivedLog.filter((n) => n === "b1.zip").length === 1 &&
    st1.receivedLog.filter((n) => n === "b2.zip").length === 1 &&
    bb1.byteLength === PLAIN_IPA_ZIP.length && bb2.byteLength === NORMAL_ZIP.length;
  rec("batch_sent_once_second_run_reuses", ok,
    `batch1=${run1Ok} batch2=${run2Ok} worker_nhan_sau_lan1=${st1.receivedCount} sau_lan2=${st2.receivedCount} (kỳ vọng bằng nhau) ` +
    `log=${JSON.stringify(st1.receivedLog)} buffer_batch_nguyen=${bb1.byteLength === PLAIN_IPA_ZIP.length && bb2.byteLength === NORMAL_ZIP.length}`,
    performance.now() - t0);
}

// ---------- B5. Gợi ý KHÔNG hiện linh tinh ----------
{
  const t0 = performance.now();
  const ab3 = new Uint8Array(PLAIN_IPA_ZIP).buffer;
  $("file-input").files = [fakeFile("plain.zip", ab3)];
  await fire("file-input", "change", { target: $("file-input") });
  await sleep(100);
  await fire("btn-list-run", "click");
  const hidden1 = $("list-ipa-hint").hidden; // ZIP chỉ có 1 entry .ipa = IPA thật
  const ab4 = new Uint8Array(NORMAL_ZIP).buffer;
  $("file-input").files = [fakeFile("normal.zip", ab4)];
  await fire("file-input", "change", { target: $("file-input") });
  await sleep(100);
  await fire("btn-list-run", "click");
  const hidden2 = $("list-ipa-hint").hidden; // ZIP thường, không có .ipa
  rec("ipa_hint_negative_cases", hidden1 && hidden2,
    `zip_1_ipa_thang_hint_an=${hidden1}; zip_thuong_khong_ipa_hint_an=${hidden2}`,
    performance.now() - t0);
}

// ---------- C. Kiểm tra tĩnh code đã sửa ----------
{
  const t0 = performance.now();
  const app = fs.readFileSync(SITE + "/app.js", "utf8");
  const worker = fs.readFileSync(SITE + "/worker.js", "utf8");
  const idx = fs.readFileSync(SITE + "/index.html", "utf8");
  const hasFlag = /fileSentFor/.test(app) && /batchSentFor/.test(app);
  const hasSlice = /\.slice\(0\)/.test(app);
  const noOldTransfer = !/\(payload\.files \|\| \[\]\)\.map\(f => f\.data\)/.test(app);
  const hasHintFn = /isZipWithSingleIpa/.test(app) && /IPA_ZIP_HINT/.test(app);
  const hasHintEls = /id="list-ipa-hint"/.test(idx) && /id="unity-ipa-hint"/.test(idx);
  const workerNote = /chỉ gửi file 1 lần/.test(worker);
  rec("static_hotfix_markers", hasFlag && hasSlice && noOldTransfer && hasHintFn && hasHintEls && workerNote,
    `fileSentFor/batchSentFor=${hasFlag} slice(0)=${hasSlice} khong_con_transfer_goc=${noOldTransfer} ` +
    `hint_fn=${hasHintFn} hint_els_html=${hasHintEls} worker_ghi_chu=${workerNote}`,
    performance.now() - t0);
}

// ---------- D. Bridge THẬT trong Pyodide: list_entries trên ZIP kiểu iOS ----------
{
  const t0 = performance.now();
  let httpFetchCount = 0;
  const _fetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = String(typeof input === "string" ? input : input.url);
    if (/^https?:\/\//.test(url)) { httpFetchCount++; throw new Error("BLOCKED http fetch: " + url); }
    return _fetch(input, init);
  };
  try {
    const pyodide = await loadPyodide({ indexURL: SITE + "/pyodide/" });
    await pyodide.loadPackage("lz4");
    const man = JSON.parse(fs.readFileSync(SITE + "/pb3/files.json", "utf8"));
    for (const rel of man.files) {
      const buf = fs.readFileSync(SITE + "/" + rel);
      const target = "/pb3web/" + rel.slice(4);
      pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
      pyodide.FS.writeFile(target, new Uint8Array(buf));
    }
    const b64 = fs.readFileSync(SITE + "/fonts/BeVietnamPro-Regular.ttf.b64", "utf8").replace(/\s+/g, "");
    pyodide.FS.mkdirTree("/pb3web/fonts");
    pyodide.FS.writeFile("/pb3web/fonts/BeVietnamPro-Regular.ttf", new Uint8Array(Buffer.from(b64, "base64")));
    pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')\nimport bridge\n");
    pyodide.FS.mkdirTree("/tmp/input");
    pyodide.FS.writeFile("/tmp/input/game.zip", new Uint8Array(IOS_ZIP));
    const b = pyodide.globals.get("bridge");
    const r = JSON.parse(b.list_entries("/tmp/input/game.zip"));
    const names = (r.entries || []).map((e) => e.path);
    const ok = r.ok === true && names[0] === IPA_NAME && names[1] === "__MACOSX/._" + IPA_NAME &&
      !JSON.stringify(r).includes("ΓÇÄ") && httpFetchCount === 0;
    rec("pyodide_bridge_mojibake", ok,
      `entries=${JSON.stringify(names)} http_fetch=${httpFetchCount} (bridge thật trong Pyodide, file site/pb3 đã sửa)`,
      performance.now() - t0);
  } catch (e) {
    rec("pyodide_bridge_mojibake", false, "lỗi: " + String(e && e.message ? e.message : e).slice(0, 300), performance.now() - t0);
  } finally {
    globalThis.fetch = _fetch;
  }
}

// ---------- E. Test hồi quy Python (4 test mojibake mới) ----------
{
  const t0 = performance.now();
  try {
    // unittest -v ghi ra STDERR nên dùng spawnSync để đọc cả stderr.
    const out = spawnSync("python3", ["-m", "unittest",
      "tests.test_container.TestZipVFS.test_zip_name_utf8_without_flag_ios_mojibake",
      "tests.test_container.TestZipVFS.test_zip_name_utf8_with_flag_and_ascii_unchanged",
      "tests.test_container.TestZipVFS.test_compress_manifest_fixes_ios_mojibake_name",
      "tests.test_container.TestRebuildZip.test_rebuild_fixes_ios_mojibake_name",
      "-v"], { cwd: SITE + "/pb3", encoding: "utf8", timeout: 120000 });
    const text = (out.stdout || "") + (out.stderr || "");
    const n = (text.match(/ \.\.\. ok$/gm) || []).length;
    rec("python_mojibake_regression", out.status === 0 && n === 4 && /OK/.test(text),
      `status=${out.status}, 4/4 test ok trong output unittest (${n} dòng "... ok")`, performance.now() - t0);
  } catch (e) {
    rec("python_mojibake_regression", false, "unittest lỗi: " + String(e.message || e).slice(0, 400), performance.now() - t0);
  }
}

await fakeWorkers[0].terminate();

results._failures = failures;
fs.writeFileSync(EXP + "/clone_fix_results.json", JSON.stringify(results, null, 2));
const lines = ["# Hotfix bug clone (Safari DataCloneError) + mojibake + gợi ý ZIP-chứa-IPA", "",
  "Ngày: 2026-11-16. Harness mới exp/verify_clone_fix.mjs: nạp CHÍNH site/app.js thật,",
  "nối với Worker THẬT (worker_threads, exp/clone_fix_worker.mjs) qua postMessage +",
  "transfer list thật — đúng chuỗi đã lỗi trên Safari (list → inspect → extract …).",
  "",
  "## Nguyên nhân gốc (đã chứng minh bằng đối chứng, không phải đoán)",
  "- app.js cũ: callWorker() truyền state.file.data làm transferable trong MỌI lần gọi.",
  "  Sau transfer đầu, ArrayBuffer bị detached (byteLength = 0); lần gọi thứ 2 trở đi",
  "  postMessage với buffer đã detached trong transfer list thì Safari ném DataCloneError",
  '  "The object can not be cloned". Mục control_old_logic_detaches chứng minh lại bằng',
  "  Node worker_threads: sau transfer lần 1 byteLength = 0, transfer lần 2 ném lỗi.",
  "- Mojibake: \"Nén\" trong Files iOS ghi tên entry bằng byte UTF-8 (tên bắt đầu bằng",
  "  U+200E LEFT-TO-RIGHT MARK, byte E2 80 8E) nhưng KHÔNG đặt cờ UTF-8 bit 11, nên",
  "  zipfile chuẩn giải tên theo cp437 thành \"ΓÇÄ …\". Fix: mã hóa ngược tên sai thành",
  "  byte cp437 rồi giải lại UTF-8 (sdk/containers.py fix_zip_entry_name).",
  "",
  "## File đổi trong đợt này",
  "- site/app.js: cờ fileSentFor/batchSentFor (chỉ gửi file 1 lần/file đã chọn), gửi bản",
  "  copy slice(0), reset cờ khi chọn file mới và khi runOp lỗi; gợi ý ZIP-chứa-IPA.",
  "- site/worker.js: chỉ ghi chú (worker dùng lại file trong FS /tmp/input, không đổi logic).",
  "- site/index.html: 2 phần tử gợi ý list-ipa-hint / unity-ipa-hint.",
  "- site/pb3/sdk/containers.py: fix_zip_entry_name + dùng trong ZipVFS, rebuild_zip,",
  "  _zip_info_copy (ghi lại bằng tên đã sửa để không \"đóng đinh\" mojibake vào file đích).",
  "- site/pb3/sdk/compress.py: _inventory_zip dùng tên đã sửa (khôi phục đúng tên gốc).",
  "- site/pb3/tests/test_container.py: 4 test hồi quy mojibake (ZipVFS, rebuild, compress).",
  "- exp/verify_clone_fix.mjs + exp/clone_fix_worker.mjs: harness mới (Worker thật).",
  "",
  "## Kết quả harness",
  "",
  "| Mục | Kết quả | Giây | Chi tiết |", "|---|---|---|---|"];
for (const [k, v] of Object.entries(results)) {
  if (k.startsWith("_")) continue;
  lines.push(`| ${k} | ${v.ok ? "PASS" : "FAIL"} | ${v.seconds} | ${String(v.detail).replace(/\|/g, "/").slice(0, 300)} |`);
}
lines.push("", `Thất bại: ${failures}`, "",
  "## Lưu ý",
  "- Hành vi picker/clone THẬT trên Safari iPhone thì Joseph kiểm chứng sau khi đẩy",
  "  (harness này chứng minh bằng Worker thật trên Node, không phải Safari thật).",
  "- Tên entry sau khi sửa vẫn giữ ký tự U+200E (không nhìn thấy) ở đầu tên — hiển thị",
  "  đúng \" 9 Kings…\", chỉ là ký tự điều khiển hướng chữ do iOS chèn, không phải lỗi.",
  "- Fix mojibake nằm ở bản vendor site/pb3 (web); cây iSH ~/workspace/pb3_fix/pb3 chưa",
  "  được sửa — cần port cùng fix sang đó ở đợt vá iSH sau (patch03).");
fs.writeFileSync(EXP + "/clone_fix_results.md", lines.join("\n"));
console.log(`\n=== XONG: ${Object.keys(results).length - 1} mục, ${failures} FAIL — xem clone_fix_results.md ===`);
process.exit(failures ? 1 : 0);
