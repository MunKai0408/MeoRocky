// verify_item4_pool.mjs — Kiểm chứng MỤC 4: pool 2 worker + checkpoint
// từng game vào IndexedDB + watchdog hợp tác (repo MunKai0408/MeoRocky).
//
// Chạy: node verify_item4_pool.mjs (từ /home/hatch/workspace/pb3_web/exp)
// Kết quả: item4_results.md + item4_results.json
//
//  A. Unit WorkerPool thật (site/worker-pool.js) với Worker GIẢ trong Node
//     (chia job cho 2 slot, runOn ghim slot, cancelQueued, worker lỗi thì
//     respawn, boot hỏng hết thì ready()=false, broadcast begin/stop,
//     dedup file theo slot — gửi 1 lần/slot, respawn gửi lại).
//  B. Bridge thật trên Pyodide Node: batch_split (khóa checkpoint khớp công
//     thức Python), batch_run từng game + out_dir_name + record_kb/make_zip,
//     resume bỏ qua game đã xong, batch_zip/merge/finalize gộp 2 worker
//     (2 instance Pyodide độc lập), tương thích ngược batch_run cũ, CSV.
//  C. Kiểm tra tĩnh: node --check, không COI/pthreads/SharedArrayBuffer,
//     nối index.html/app.js/worker.js/bridge.py.
//  D. Trình duyệt THẬT (Playwright chromium headless shell, serve site qua
//     HTTP local): boot -> batch 2 game qua pool (cả 2 worker đều làm,
//     checkpoint vào IndexedDB, tải batch.zip) -> reload trang -> checkpoint
//     còn -> chạy lại resume thì bỏ qua hết -> Dừng an toàn + Tiếp tục ->
//     watchdog 0,5 giây dừng an toàn + Tiếp tục chạy hết -> fallback 1 worker.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import vm from "node:vm";
import { execFileSync, execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { loadPyodide } from "../site/pyodide/pyodide.mjs";

const SITE = "/home/hatch/workspace/pb3_web/site";
const EXP = "/home/hatch/workspace/pb3_web/exp";
const PB3 = "/home/hatch/workspace/pb3_fix/pb3";
const GAME1 = PB3 + "/tests/fixtures/extract/game1.zip";
const AMBIG = PB3 + "/tests/fixtures/extract/ambig.zip";
const BIG = EXP + "/fixtures/bigtest.zip";
const sec = (ms) => Math.round(ms / 100) / 10;
const results = {};
let failures = 0;
function rec(name, ok, detail, ms) {
  results[name] = { ok, detail, seconds: ms == null ? undefined : sec(ms) };
  if (!ok) failures++;
  console.log(`\n### ${name}: ${ok ? "PASS" : "FAIL"}${ms == null ? "" : " (" + sec(ms) + "s)"}\n${detail}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Fixture bigtest.zip (4000 file text, extract ~2s rồi E_NO_CANDIDATE):
// dùng cho test timing (Dừng an toàn / watchdog) — tạo lại nếu chưa có.
if (!fs.existsSync(BIG)) {
  fs.mkdirSync(path.dirname(BIG), { recursive: true });
  execSync("python3 -c \"import zipfile\nwith zipfile.ZipFile('" + BIG + "','w',zipfile.ZIP_DEFLATED) as z:\n    [z.writestr('assets/text%04d.txt' % i, ('hello world %d\\n' % i) * 40) for i in range(4000)]\n\"");
  console.log("created fixture " + BIG + " (" + fs.statSync(BIG).size + " bytes)");
}

// ================= A. Unit WorkerPool với Worker giả =================
const poolSrc = fs.readFileSync(SITE + "/worker-pool.js", "utf8");
let fakeWorkers = [];
class FakeWorker {
  constructor(url, opts) {
    if (FakeWorker.throwOnConstruct) throw new Error("Worker constructor boom");
    this.url = url; this.opts = opts; this.terminated = false;
    this.onmessage = null; this.onerror = null;
    this.received = [];
    this.behavior = FakeWorker.behavior;
    this._errored = false;
    fakeWorkers.push(this);
  }
  postMessage(msg, _transfer) {
    if (this.terminated) return;
    this.received.push(msg);
    const self = this;
    if (msg.op === "boot") {
      setTimeout(() => {
        if (self.terminated) return;
        if (self.behavior === "boot-fail") {
          self.onmessage && self.onmessage({ data: { id: msg.id, ok: false, error: "boot boom" } });
          return;
        }
        self.onmessage && self.onmessage({ data: { id: msg.id, ok: true, result: "{}" } });
      }, 5);
      return;
    }
    if (msg.op === "run") {
      const delay = msg.fn === "slow" ? 120 : 15;
      setTimeout(() => {
        if (self.terminated) return;
        if (self.behavior === "error-once" && !FakeWorker._erroredOnce) {
          FakeWorker._erroredOnce = true;
          self.onerror && self.onerror({ message: "fake worker crash" });
          return;
        }
        self.onmessage && self.onmessage({ data: { id: msg.id, ok: true, result: JSON.stringify({ ok: true, echo: msg.fn }) } });
      }, delay);
      return;
    }
    // batch_begin / batch_stop: không reply (như worker.js thật).
  }
  terminate() { this.terminated = true; }
}
FakeWorker.behavior = "ok";
FakeWorker.throwOnConstruct = false;
const poolSandbox = {
  Worker: FakeWorker, setTimeout, clearTimeout, console,
  window: null,
};
poolSandbox.window = poolSandbox;
vm.createContext(poolSandbox);
const WorkerPool = vm.runInContext(poolSrc + "\n;WorkerPool;", poolSandbox);

{ // A1. 4 job chia cho 2 slot, Promise.all giữ đúng thứ tự
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  const out = await Promise.all([
    pool.run({ fn: "a" }), pool.run({ fn: "b" }),
    pool.run({ fn: "c" }), pool.run({ fn: "d" }),
  ]);
  const echoes = out.map((r) => JSON.parse(r.msg.result).echo).join(",");
  const slots = new Set(out.map((r) => r.slot));
  const ok = out.length === 4 && echoes === "a,b,c,d" && slots.size === 2;
  rec("pool_runall_2slots_order", ok,
    "Promise.all 4 job -> echo theo đúng thứ tự " + echoes + "; slot đã dùng: " +
    [...slots].join(",") + " (cần cả 2 slot)", performance.now() - t0);
  pool.destroy();
}
{ // A2. status() hiện busy/label trong lúc chạy
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  const p = pool.run({ fn: "slow" }, { label: "game-lau.zip" });
  await sleep(30);
  const st = pool.status();
  const busySeen = st.some((s) => s.busy && s.label === "game-lau.zip");
  const r = await p;
  const st2 = pool.status();
  const idleAfter = st2.every((s) => !s.busy && s.label === "");
  rec("pool_status_busy_label", busySeen && idleAfter && JSON.parse(r.msg.result).echo === "slow",
    "đang chạy: status có slot busy + label 'game-lau.zip': " + busySeen +
    "; xong: mọi slot rảnh + label trống: " + idleAfter, performance.now() - t0);
  pool.destroy();
}
{ // A3. runOn ghim đúng slot 1
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  const r = await pool.runOn(1, { fn: "pinned" }, { label: "g1" });
  const w1Runs = fakeWorkers[1].received.filter((m) => m.op === "run").length;
  const w0Runs = fakeWorkers[0].received.filter((m) => m.op === "run").length;
  rec("pool_runon_pins_slot", r.slot === 1 && w1Runs === 1 && w0Runs === 0,
    "runOn(1) -> reply slot=" + r.slot + "; worker[1] nhận " + w1Runs +
    " job run, worker[0] nhận " + w0Runs, performance.now() - t0);
  pool.destroy();
}
{ // A4. cancelQueued: job đang chạy xong, job chờ bị hủy E_BATCH_STOPPED
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  const promises = [
    pool.run({ fn: "slow" }, { label: "s1" }),
    pool.run({ fn: "slow" }, { label: "s2" }),
    pool.run({ fn: "slow" }, { label: "s3" }),
    pool.run({ fn: "slow" }, { label: "s4" }),
  ];
  await sleep(20);   // lúc này 2 job đang chạy, 2 job còn xếp hàng
  pool.cancelQueued();
  const settled = await Promise.allSettled(promises);
  const done = settled.filter((s) => s.status === "fulfilled").length;
  const cancelled = settled.filter((s) => s.status === "rejected" &&
    String(s.reason && s.reason.message).startsWith("E_BATCH_STOPPED")).length;
  rec("pool_cancel_queued_safe", done === 2 && cancelled === 2,
    "4 job slow (2 slot): xong " + done + " (đang chạy được chạy hết), hủy chờ " +
    cancelled + " với E_BATCH_STOPPED", performance.now() - t0);
  pool.destroy();
}
{ // A5. Worker constructor ném lỗi -> slot chết ngay, ready()=false để app fallback 1 worker
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.throwOnConstruct = true;
  const pool = new WorkerPool("worker.js", { size: 2 });
  FakeWorker.throwOnConstruct = false;
  const ok = await pool.ready();
  const alive = pool.aliveCount();
  rec("pool_construct_throws_fallback", ok === false && alive === 0,
    "Worker constructor ném -> _spawn bắt gọn, slot chết (không crash pool); " +
    "ready()=" + ok + ", aliveCount=" + alive + " -> app rơi về 1 worker",
    performance.now() - t0);
  pool.destroy();
}
{ // A6. Worker lỗi giữa job -> job báo lỗi, slot respawn, job sau chạy được
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "error-once"; FakeWorker._erroredOnce = false;
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  let firstErr = "";
  try { await pool.run({ fn: "x" }, { label: "g-err" }); } catch (e) { firstErr = e.message; }
  const second = await pool.run({ fn: "y" }, { label: "g-ok" });
  const alive = pool.aliveCount();
  rec("pool_worker_error_respawn", firstErr === "fake worker crash" &&
    JSON.parse(second.msg.result).echo === "y" && alive === 2,
    "job đầu lỗi worker: " + JSON.stringify(firstErr) + "; job sau chạy được (echo=y); " +
    "slot sống sau respawn: " + alive + "/2", performance.now() - t0);
  pool.destroy();
}
{ // A7. beginBatch/requestStop broadcast tới cả 2 worker (không reply)
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  pool.beginBatch(25);
  pool.requestStop();
  await sleep(10);
  const begins = fakeWorkers.filter((w) => w.received.some((m) => m.op === "batch_begin" && m.budgetSeconds === 25)).length;
  const stops = fakeWorkers.filter((w) => w.received.some((m) => m.op === "batch_stop")).length;
  rec("pool_broadcast_begin_stop", begins === 2 && stops === 2,
    "batch_begin(25) tới " + begins + "/2 worker; batch_stop tới " + stops + "/2 worker",
    performance.now() - t0);
  pool.destroy();
}
{ // A8. Boot hỏng hết (cả lần respawn) -> ready()=false để app fallback
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "boot-fail";
  const pool = new WorkerPool("worker.js", { size: 2 });
  const ok = await pool.ready();
  rec("pool_ready_false_when_all_boot_fail", ok === false,
    "cả 2 slot boot-fail (kể cả respawn, MAX 2 lần) -> ready()=" + ok +
    " (app rơi về 1 worker)", performance.now() - t0);
  pool.destroy();
}

{ // A9. Dedup file theo slot (giữ đảm bảo "gửi 1 lần" của hotfix clone):
  // file chỉ gửi 1 lần cho mỗi slot; game sau trên cùng slot không gửi lại;
  // slot khác (FS riêng) vẫn nhận 1 lần; slot respawn (FS trống) gửi lại.
  const t0 = performance.now();
  fakeWorkers = []; FakeWorker.behavior = "ok";
  const pool = new WorkerPool("worker.js", { size: 2 });
  await pool.ready();
  const buf1 = new ArrayBuffer(8);
  const buf2 = new ArrayBuffer(8); // bản copy khác, cùng tên|cỡ|lastModified
  const f1 = { name: "g.zip", data: buf1, size: 8, lastModified: 1700000000000 };
  const f2 = { name: "g.zip", data: buf2, size: 8, lastModified: 1700000000000 };
  await pool.runOn(0, { fn: "batch_run", files: [f1] }, { label: "g1", transfer: [buf1] });
  await pool.runOn(0, { fn: "batch_run", files: [f2] }, { label: "g1-lai", transfer: [buf2] });
  await pool.runOn(1, { fn: "batch_run", files: [f2] }, { label: "g1-slot1", transfer: [buf2] });
  const w0 = fakeWorkers[0].received.filter((m) => m.op === "run");
  const w1 = fakeWorkers[1].received.filter((m) => m.op === "run");
  const firstSent = w0[0].files && w0[0].files.length === 1 && w0[0].files[0].name === "g.zip";
  const secondSkipped = w0[1].files && w0[1].files.length === 0;
  const slot1Sent = w1[0].files && w1[0].files.length === 1 && w1[0].files[0].name === "g.zip";
  // Respawn: slot 0 lỗi -> worker mới (FS trống) -> gửi lại file.
  // Lưu ý: behavior của FakeWorker đọc lúc CONSTRUCT (this.behavior =
  // FakeWorker.behavior) nên phải đặt trên instance đang sống, không phải
  // chỉ đổi static (đổi static chỉ ảnh hưởng worker tạo SAU đó).
  fakeWorkers[0].behavior = "error-once"; FakeWorker._erroredOnce = false;
  let err = "";
  try { await pool.runOn(0, { fn: "batch_run", files: [f2] }, { label: "g-crash", transfer: [buf2] }); } catch (e) { err = e.message; }
  const respawned = fakeWorkers.length === 3; // slot 0 tạo lại worker mới
  await pool.ready();
  if (respawned) fakeWorkers[2].behavior = "ok"; // worker mới không lỗi lại
  await pool.runOn(0, { fn: "batch_run", files: [f2] }, { label: "g-sau-respawn", transfer: [buf2] });
  const wNew = respawned ? fakeWorkers[2].received.filter((m) => m.op === "run") : [];
  const respawnResent = respawned && wNew.length === 1 && wNew[0].files && wNew[0].files.length === 1 && wNew[0].files[0].name === "g.zip";
  rec("pool_dedup_file_per_slot", firstSent && secondSkipped && slot1Sent &&
    err === "fake worker crash" && respawned && respawnResent,
    "slot 0 lần 1 gửi g.zip: " + firstSent + "; lần 2 cùng file bỏ qua (files=[]): " + secondSkipped +
    "; slot 1 (FS riêng) nhận 1 lần: " + slot1Sent + "; crash slot 0: " + JSON.stringify(err) +
    "; respawn tạo worker mới: " + respawned + "; sau respawn gửi lại: " + respawnResent, performance.now() - t0);
  pool.destroy();
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
  pyodide.globals.set("pb3_post_progress", () => {});
  pyodide.runPython("import bridge\nbridge.set_progress_cb(pb3_post_progress)\nbridge.__boot_result__ = bridge.boot()\n");
  const bridge = pyodide.globals.get("bridge");
  const bootOk = JSON.parse(pyodide.globals.get("bridge").__boot_result__).ok;
  if (!bootOk) throw new Error("Pyodide bridge boot failed");
  return { pyodide, bridge, call: (fn, ...args) => JSON.parse(bridge[fn](...args)) };
}
function pyStyleKey(path, locale, mode, oldV, newV) {
  // Khóa checkpoint của Python: sha256(json.dumps([...], ensure_ascii=False))
  // — json.dumps MẶC ĐỊNH dùng dấu phẩy + khoảng trắng (", "), khác
  // JSON.stringify của JS (không khoảng trắng). JS không tự tính khóa này
  // (batch_split trả sẵn), test này khóa đúng công thức để khỏi lệch.
  const s = '["' + path + '", ' + (locale == null ? "null" : '"' + locale + '"') +
    ', "' + mode + '", ' + (oldV == null ? "null" : '"' + oldV + '"') +
    ', ' + (newV == null ? "null" : '"' + newV + '"') + ']';
  return createHash("sha256").update(s, "utf8").digest("hex");
}

const { pyodide, bridge, call } = await bootPyodide();
pyodide.FS.mkdirTree("/tmp/input");
for (const [name, src] of [["game1.zip", GAME1], ["ambig.zip", AMBIG], ["bigtest.zip", BIG]]) {
  pyodide.FS.writeFile("/tmp/input/" + name, new Uint8Array(fs.readFileSync(src)));
}
const MAN2 = JSON.stringify([
  { path: "game1.zip", mode: "extract" },
  { path: "game1.zip", mode: "extract", locale: "en" },
]);

{ // B1. batch_split: 2 item + khóa khớp công thức Python
  const t0 = performance.now();
  const s = call("batch_split", MAN2);
  const k0 = pyStyleKey("game1.zip", null, "extract", null, null);
  const k1 = pyStyleKey("game1.zip", "en", "extract", null, null);
  rec("bridge_batch_split_keys", s.ok === true && s.total === 2 &&
    s.items[0].key === k0 && s.items[1].key === k1 && k0 !== k1,
    "split 2 item (game1 null/en) -> key[0]=" + (s.items[0] && s.items[0].key.slice(0, 12)) +
    "… khớp sha256 Python-style: " + (s.items[0].key === k0) + "; key[1] khác key[0]: " +
    (k0 !== k1), performance.now() - t0);
}
let ckAfterG1 = null;
{ // B2. batch_run từng game: out_dir_name cố định + record_kb=false + make_zip=false
  const t0 = performance.now();
  const r = call("batch_run", JSON.stringify([{ path: "game1.zip", mode: "extract" }]),
    true, null, false, "run-b2", false, false);
  const ck = call("batch_checkpoint", "run-b2");
  const games = ck.checkpoint ? JSON.parse(ck.checkpoint).games : {};
  ckAfterG1 = ck.checkpoint;
  rec("bridge_batch_run_per_game", r.ok === true && r.summary.ok === 1 &&
    r.out_dir_name === "run-b2" && r.zip_path === null && r.zip_bytes === 0 &&
    Object.keys(games).length === 1 && games[Object.keys(games)[0]].status === "ok",
    "1 game extract ok (0.4s) vào out_dir run-b2; make_zip=false -> zip_path=null; " +
    "checkpoint sau game 1 có 1 game ok", performance.now() - t0);
}
{ // B3. Resume: chạy lại game đã xong với checkpoint gộp -> skipped (checkpoint)
  const t0 = performance.now();
  const r = call("batch_run", JSON.stringify([{ path: "game1.zip", mode: "extract" }]),
    true, ckAfterG1, true, "run-b2", false, false);
  const g = (r.summary.games || [])[0] || {};
  rec("bridge_batch_resume_skip", r.ok === true && r.summary.skipped === 1 &&
    g.status === "skipped" && (g.note || "").includes("checkpoint"),
    "chạy lại game1 với checkpoint gộp -> skipped=1, note=" + JSON.stringify(g.note),
    performance.now() - t0);
}
{ // B4. Hai worker (2 instance Pyodide) -> zip + merge + finalize gộp
  const t0 = performance.now();
  const second = await bootPyodide();
  second.pyodide.FS.mkdirTree("/tmp/input");
  second.pyodide.FS.writeFile("/tmp/input/game1.zip", new Uint8Array(fs.readFileSync(GAME1)));
  // Worker A: game1 locale null (index 1); worker B: game1 locale en (index 2) — cùng run-merge.
  const rA = call("batch_run", JSON.stringify([{ path: "game1.zip", mode: "extract" }]),
    true, null, false, "run-merge", false, false, 1);
  const rB = second.call("batch_run", JSON.stringify([{ path: "game1.zip", mode: "extract", locale: "en" }]),
    true, null, false, "run-merge", false, false, 2);
  const zB = second.call("batch_zip", "run-merge");
  const zBBytes = second.pyodide.FS.readFile(zB.zip_path);
  pyodide.FS.writeFile("/tmp/input/merge-run-merge.zip", zBBytes);
  const mg = call("batch_merge_zip", "run-merge", "/tmp/input/merge-run-merge.zip");
  const mergedCkpt = JSON.stringify({ version: 1, games: {
    [pyStyleKey("game1.zip", null, "extract", null, null)]: { path: "game1.zip", mode: "extract", locale: null, status: "ok", elapsed_s: 0.4, game_dir: "g0", error_code: "" },
    [pyStyleKey("game1.zip", "en", "extract", null, null)]: { path: "game1.zip", mode: "extract", locale: "en", status: "ok", elapsed_s: 0.4, game_dir: "g1", error_code: "" },
  } });
  const summary = { total: 2, ok: 2, failed: 0, skipped: 0, incomplete: 0, elapsed_s: 1.0,
    out_dir: "run-merge", jobs: 2, dry_run: true, budgets: {},
    games: [
      { index: 1, path: "game1.zip", mode: "extract", locale: null, status: "ok", note: "", elapsed_s: 0.4, game_dir: "g0", key: "k0", error_code: "", error_message: "" },
      { index: 2, path: "game1.zip", mode: "extract", locale: "en", status: "ok", note: "", elapsed_s: 0.4, game_dir: "g1", key: "k1", error_code: "", error_message: "" },
    ] };
  const fin = call("batch_finalize", "run-merge", JSON.stringify(summary), mergedCkpt);
  const names = pyodide.runPython(
    "import zipfile; '\\n'.join(sorted(zipfile.ZipFile('" + fin.zip_path + "').namelist()))");
  const ckInZip = pyodide.runPython(
    "import zipfile; zipfile.ZipFile('" + fin.zip_path + "').read('checkpoint.json').decode()");
  const ckObj = JSON.parse(ckInZip);
  const idxCsv = pyodide.runPython(
    "import zipfile; zipfile.ZipFile('" + fin.zip_path + "').read('index.csv').decode()");
  rec("bridge_batch_merge_finalize_2workers", rA.ok && rB.ok && zB.ok && zB.zip_bytes > 0 &&
    mg.ok && mg.files >= 1 && fin.ok && fin.filename === "batch.zip" &&
    names.includes("game_01_game1") && names.includes("game_02_game1") &&
    Object.keys(ckObj.games || {}).length === 2 && idxCsv.split("\n").length >= 3,
    "A(game1 null) ok + B(game1 en) ok; zip B " + zB.zip_bytes + "B -> merge vào A: " +
    mg.files + " file; finalize batch.zip " + fin.zip_bytes + "B chứa cả 2 thư mục game " +
    "(game_01_game1, game_02_game1) + checkpoint gộp 2 game + index.csv " +
    (idxCsv.split("\n").length - 1) + " dòng", performance.now() - t0);
}
{ // B5. Tương thích ngược: batch_run cũ 4 tham số vị trí (whole-lot 2 game).
  // Dùng record_kb=false: whole-lot + KB + cùng file 2 lần với locale ép
  // ("en") là quirk CÓ SẴN của sdk (game thứ 2 failed E_LOCALE_NOT_FOUND
  // vì profile KB game 1 không có locale en — đã kiểm chứng riêng, không
  // do Mục 4; đường pool dùng record_kb=false nên không gặp).
  const t0 = performance.now();
  const r = call("batch_run", MAN2, true, null, false, null, false, true);
  rec("bridge_batch_run_legacy_compat", r.ok === true && r.summary.total === 2 &&
    r.summary.ok === 2 && r.zip_path === "/tmp/batch.zip" && r.zip_bytes > 0 &&
    r.checkpoint && Object.keys(JSON.parse(r.checkpoint).games).length === 2,
    "batch_run(manifest, true, null, false, null, false, true) vẫn chạy: total=2 ok=2, zip " +
    r.zip_bytes + "B, checkpoint 2 game (harness cũ verify_parity dùng đường này)",
    performance.now() - t0);
}
{ // B6. batch_split với manifest CSV
  const t0 = performance.now();
  const s = call("batch_split", "path,mode\n game1.zip,extract\n");
  rec("bridge_batch_split_csv", s.ok === true && s.total === 1 && s.items[0].path === "game1.zip",
    "CSV 1 dòng -> 1 item path=game1.zip", performance.now() - t0);
}
{ // B7. out_dir_name xấu ("../evil") -> rơi về thư mục theo giờ, vẫn chạy
  const t0 = performance.now();
  const r = call("batch_run", JSON.stringify([{ path: "game1.zip", mode: "extract" }]),
    true, null, false, "../evil", false, false);
  rec("bridge_batch_bad_outdir_fallback", r.ok === true && r.summary.ok === 1 &&
    !String(r.out_dir).includes("evil"),
    "out_dir_name '../evil' bị từ chối, dùng thư mục theo giờ " + r.out_dir + ", vẫn ok=1",
    performance.now() - t0);
}
let bigMs = 0;
{ // B8. Đo thời gian extract bigtest (căn cứ chọn ngưỡng watchdog 0,5s ở phần D)
  const t0 = performance.now();
  const r = call("batch_run", JSON.stringify([{ path: "bigtest.zip", mode: "extract" }]),
    true, null, false, "run-big4", false, false);
  bigMs = performance.now() - t0;
  const g = (r.summary.games || [])[0] || {};
  rec("bridge_bigtest_extract_time", r.ok === true && g.status === "failed" &&
    g.error_code === "E_NO_CANDIDATE" && bigMs > 800,
    "bigtest (4000 file text) extract -> failed/E_NO_CANDIDATE sau " + Math.round(bigMs) +
    "ms (>800ms: đủ chậm để watchdog 0,5s ở phần D bắt được game thứ 3)",
    bigMs);
}
{ // B9. batch_split giữ đủ trường mod (old/new/file/object)
  const t0 = performance.now();
  const s = call("batch_split", JSON.stringify([
    { path: "resources.assets", mode: "mod", object: "1001", old: "Xin chao", new: "Chào" }]));
  const it = s.items[0] || {};
  rec("bridge_batch_split_mod_fields", s.ok === true && it.mode === "mod" &&
    it.object === "1001" && it.old === "Xin chao" && it.new === "Chào",
    "item mod giữ đủ object=1001 old='Xin chao' new='Chào'", performance.now() - t0);
}

// ================= C. Kiểm tra tĩnh =================
{ // C1. node --check mọi file JS sửa/thêm
  const t0 = performance.now();
  const files = ["app.js", "worker.js", "worker-pool.js", "kb-store.js", "gt.js"];
  const bad = [];
  for (const f of files) {
    try { execFileSync("node", ["--check", SITE + "/" + f], { stdio: "pipe" }); }
    catch (e) { bad.push(f); }
  }
  rec("static_node_check", bad.length === 0,
    bad.length ? "node --check lỗi: " + bad.join(", ") : "node --check xanh: " + files.join(", "),
    performance.now() - t0);
}
{ // C2. Không COOP/COEP/pthreads/SharedArrayBuffer thật (chỉ được nhắc trong comment "KHÔNG dùng")
  const t0 = performance.now();
  const hay = ["app.js", "worker.js", "worker-pool.js", "index.html"].map((f) => fs.readFileSync(SITE + "/" + f, "utf8")).join("\n");
  const bad = [];
  if (/new SharedArrayBuffer/.test(hay)) bad.push("new SharedArrayBuffer");
  if (/Cross-Origin-Embedder-Policy|Cross-Origin-Opener-Policy/i.test(hay)) bad.push("COOP/COEP header");
  if (/coi-serviceworker/i.test(hay)) bad.push("coi-serviceworker");
  if (/Atomics\./.test(hay)) bad.push("Atomics");
  rec("static_no_coi_no_pthreads", bad.length === 0,
    bad.length ? "còn dùng: " + bad.join(", ") : "không có new SharedArrayBuffer / header COOP-COEP / coi-serviceworker / Atomics trong app.js, worker.js, worker-pool.js, index.html",
    performance.now() - t0);
}
{ // C3. Nối index.html/app.js/worker.js/bridge.py
  const t0 = performance.now();
  const html = fs.readFileSync(SITE + "/index.html", "utf8");
  const app = fs.readFileSync(SITE + "/app.js", "utf8");
  const worker = fs.readFileSync(SITE + "/worker.js", "utf8");
  const bridgeSrc = fs.readFileSync(SITE + "/pb3/bridge.py", "utf8");
  const checks = {
    "index.html nạp worker-pool.js trước app.js": html.indexOf("worker-pool.js") !== -1 && html.indexOf("worker-pool.js") < html.indexOf("app.js"),
    "index.html có #batch-use-pool": html.includes('id="batch-use-pool"'),
    "index.html có #batch-pool-info": html.includes('id="batch-pool-info"'),
    "index.html có #btn-batch-continue": html.includes('id="btn-batch-continue"'),
    "index.html có #btn-batch-stop-safe": html.includes('id="btn-batch-stop-safe"'),
    "index.html note pool 2 worker + watchdog 25s": html.includes("Pool 2 worker") && html.includes("25 giây"),
    "app.js có POOL_SIZE=2": /const POOL_SIZE = 2/.test(app),
    "app.js có BATCH_WATCHDOG_SECONDS=25": /const BATCH_WATCHDOG_SECONDS = 25/.test(app),
    "app.js có ckptStore (tái dùng KBStore)": app.includes('new KBStore("batch-checkpoint.json"'),
    "app.js gọi batch_split": app.includes('"batch_split"'),
    "app.js có runBatchFlow + Tiếp tục + Dừng an toàn": app.includes("runBatchFlow") && app.includes("btn-batch-continue") && app.includes("requestBatchStop"),
    "worker.js có WATCHDOG_SECONDS=25": /const WATCHDOG_SECONDS = 25/.test(worker),
    "worker.js có batch_begin/batch_stop": worker.includes('"batch_begin"') && worker.includes('"batch_stop"'),
    "worker.js từ chối E_WATCHDOG_STOP/E_BATCH_STOPPED": worker.includes("E_WATCHDOG_STOP") && worker.includes("E_BATCH_STOPPED"),
    "bridge.py có batch_split/batch_zip/batch_merge_zip/batch_finalize/batch_checkpoint": ["batch_split", "batch_zip", "batch_merge_zip", "batch_finalize", "batch_checkpoint"].every((f) => bridgeSrc.includes("def " + f)),
    "bridge.py batch_run có out_dir_name/record_kb/make_zip/index_base": bridgeSrc.includes("out_dir_name=None, record_kb=True, make_zip=True, index_base=1"),
  };
  const failed = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  rec("static_wiring", failed.length === 0,
    failed.length ? "thiếu/sai: " + failed.join(" | ") : Object.keys(checks).length + " điểm nối đều đúng (script, ID, hằng số, hàm mới)",
    performance.now() - t0);
}

// ================= D. Trình duyệt THẬT (Playwright headless shell) =================
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm", ".png": "image/png", ".b64": "text/plain",
  ".ttf": "font/ttf", ".txt": "text/plain", ".md": "text/plain", ".css": "text/css",
};
const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(new URL(req.url || "/", "http://127.0.0.1").pathname);
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

const readCkptFromIdb = () => page.evaluate(async () => {
  const db = await new Promise((res, rej) => {
    const r = indexedDB.open("pb3-kb-store", 1);
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
  const v = await new Promise((res, rej) => {
    const t = db.transaction("blobs", "readonly");
    const q = t.objectStore("blobs").get("batch-checkpoint.json");
    q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error);
  });
  db.close();
  if (!v) return null;
  return new TextDecoder().decode(v);
});

let browser = null;
let page = null;
try {
  const { chromium } = await import("playwright-core");
  const candidates = [
    process.env.HOME + "/.cache/ms-playwright/chromium_headless_shell-1148/chrome-linux/headless_shell",
    process.env.HOME + "/.cache/ms-playwright/chromium-1148/chrome-linux/chrome",
  ];
  const exe = candidates.find((c) => fs.existsSync(c));
  if (!exe) throw new Error("không tìm thấy Chromium headless shell ở ~/.cache/ms-playwright");
  browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
  const ctx = await browser.newContext({ acceptDownloads: true });
  page = await ctx.newPage();
  // Ép canShare -> false để đi đường blob-download (headless shell không có
  // Share sheet thật; trên iPhone thật đường Share mới là chính).
  await page.addInitScript(() => {
    try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
  });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  const waitReady = async () => {
    await page.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
  };
  const goBatchTab = async () => {
    await page.click("#btn-card-batch");
    await page.waitForFunction(
      () => { const el = document.getElementById("tab-batch"); return el && !el.hidden; },
      null, { timeout: 30000 });
  };
  const batchResultText = () => page.evaluate(() => (document.getElementById("batch-result") || {}).textContent || "");
  const poolInfoText = () => page.evaluate(() => (document.getElementById("batch-pool-info") || {}).textContent || "");
  const ckptInfoText = () => page.evaluate(() => (document.getElementById("batch-ckpt-info") || {}).textContent || "");

  { // D1. Boot thật trong trình duyệt + mở tab Batch
    const t0 = performance.now();
    await page.goto("http://127.0.0.1:" + PORT + "/index.html");
    await waitReady();
    await goBatchTab();
    const poolTxt = await poolInfoText();
    rec("browser_boot_batch_tab", poolTxt.includes("Pool chưa khởi động"),
      "status=Sẵn sàng; tab Batch mở được; #batch-pool-info ban đầu: " + JSON.stringify(poolTxt),
      performance.now() - t0);
  }
  { // D2. Batch 2 game qua pool: cả 2 worker đều làm, checkpoint vào IndexedDB, tải batch.zip
    const t0 = performance.now();
    await page.setInputFiles("#batch-files", [GAME1, GAME1]);
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]));
    const dlPromise = page.waitForEvent("download", { timeout: 300000 });
    await page.click("#btn-batch-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("2 ok / 0 lỗi / 0 bỏ qua / 2 game"); },
      null, { timeout: 300000 });
    const res = await batchResultText();
    const poolTxt = await poolInfoText();
    const ckptTxt = await readCkptFromIdb();
    const ckpt = ckptTxt ? JSON.parse(ckptTxt) : null;
    const ckptGames = ckpt && ckpt.games ? Object.keys(ckpt.games).length : 0;
    const ckptOk = ckpt && ckpt.games ? Object.values(ckpt.games).filter((g) => g.status === "ok").length : 0;
    const dl = await dlPromise;
    const dlName = dl.suggestedFilename();
    rec("browser_pool_2games_ckpt_zip", res.includes("pool 2 worker") &&
      res.includes("worker 0: 1 game") && res.includes("worker 1: 1 game") &&
      poolTxt.includes("Worker 0:") && poolTxt.includes("Worker 1:") &&
      ckptGames === 2 && ckptOk === 2 && dlName === "batch.zip",
      "kết quả: 2 ok qua pool 2 worker, phân công worker 0: 1 game · worker 1: 1 game; " +
      "#batch-pool-info hiện cả Worker 0 và Worker 1; checkpoint IndexedDB: " + ckptGames +
      " game, " + ckptOk + " ok; download batch.zip: " + dlName, performance.now() - t0);
  }
  { // D3. Reload trang -> checkpoint còn (preload) -> chạy lại resume thì bỏ qua hết
    const t0 = performance.now();
    await page.reload();
    await waitReady();
    await goBatchTab();
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-ckpt-info"); return el && el.textContent.includes("Có checkpoint đã lưu từ lần trước"); },
      null, { timeout: 60000 });
    const info = await ckptInfoText();
    await page.setInputFiles("#batch-files", [GAME1, GAME1]);
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]));
    await page.check("#batch-resume");
    await page.click("#btn-batch-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("đã xong hết theo checkpoint"); },
      null, { timeout: 120000 });
    const res = await batchResultText();
    rec("browser_ckpt_survives_reload_resume", info.includes("IndexedDB") && info.includes("2 game đã xong") &&
      res.includes("2 ok / 0 lỗi / 2 bỏ qua / 2 game"),
      "sau reload: " + JSON.stringify(info.slice(0, 110)) + "…; chạy lại với resume -> " +
      JSON.stringify(res.slice(0, 90)) + "…", performance.now() - t0);
  }
  { // D4. Dừng an toàn giữa chừng + Tiếp tục chạy hết (8 game).
    // 8 game để có cửa sổ thời gian bấm Dừng: vòng 1 (2 game1, ~0,7s)
    // xong thì checkpoint hiện "đã xong 2/8 game" và GIỮ NGUYÊN suốt 2 vòng
    // bigtest (~7s, vì bigtest failed nên số ok không tăng) — đủ lâu để
    // bấm Dừng an toàn trong lúc 4 game còn xếp hàng.
    const t0 = performance.now();
    await page.uncheck("#batch-resume");
    await page.setInputFiles("#batch-files", [GAME1, BIG, AMBIG]);
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
      { path: "bigtest.zip", mode: "extract" },
      { path: "bigtest.zip", mode: "extract", locale: "en" },
      { path: "bigtest.zip", mode: "extract", locale: "vi" },
      { path: "bigtest.zip", mode: "extract", locale: "fr" },
      { path: "ambig.zip", mode: "extract" },
      { path: "ambig.zip", mode: "extract", locale: "en" },
    ]));
    await page.click("#btn-batch-run");
    // Chờ 2 game đầu xong ("đã xong 2/8") rồi bấm Dừng an toàn ngay —
    // lúc này 2 game bigtest đang chạy, 4 game còn xếp hàng.
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-ckpt-info"); return el && el.textContent.includes("đã xong 2/8 game"); },
      null, { timeout: 300000 });
    await page.click("#btn-batch-stop-safe");
    await page.waitForFunction(
      () => { const b = document.getElementById("btn-batch-continue"); return b && !b.hidden; },
      null, { timeout: 180000 });
    const resStop = await batchResultText();
    const ckptTxtStop = await readCkptFromIdb();
    const ckptStop = ckptTxtStop ? JSON.parse(ckptTxtStop) : null;
    const ckptStopN = ckptStop && ckptStop.games ? Object.keys(ckptStop.games).length : 0;
    const stoppedOk = resStop.includes("dừng an toàn") && ckptStopN === 4;
    // Tiếp tục: 4 game còn lại (2 bigtest vi/fr + 2 ambig) chạy hết.
    // Lưu ý semantics resume (như BatchRunner bản iSH): 2 game đã ok ở
    // đợt trước được tính là "skipped" trong đợt này, nên tổng cuối là
    // 0 ok / 4 lỗi / 2 bỏ qua / 8 game (2 ambig ở cột incomplete).
    await page.click("#btn-batch-continue");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("0 ok / 4 lỗi / 2 bỏ qua / 8 game"); },
      null, { timeout: 300000 });
    const resFinal = await batchResultText();
    const incompleteRows = await page.evaluate(() => {
      const rows = [...document.querySelectorAll("#batch-result table tr")];
      return rows.filter((tr) => tr.textContent.includes("incomplete")).length;
    });
    const continueHidden = await page.evaluate(() => (document.getElementById("btn-batch-continue") || {}).hidden);
    rec("browser_stop_safe_and_continue", stoppedOk && incompleteRows === 2 && continueHidden === true,
      "dừng giữa chừng: kết quả có 'dừng an toàn', checkpoint IndexedDB giữ " + ckptStopN +
      " game (2 ok + 2 failed bigtest, 4 game chưa chạy); Tiếp tục -> xong hết " +
      "0 ok / 4 lỗi / 2 bỏ qua / 8 game (2 game ok cũ tính skipped theo semantics resume), " +
      "2 dòng incomplete (ambig), nút Tiếp tục đã ẩn",
      performance.now() - t0);
  }
  { // D5. Watchdog 0,5 giây: dừng an toàn + Tiếp tục (3 game bigtest)
    const t0 = performance.now();
    const ctx2 = await browser.newContext({ acceptDownloads: true });
    const page2 = await ctx2.newPage();
    page2.on("pageerror", (e) => pageErrors.push("watchdog-page: " + String(e && e.message ? e.message : e)));
    await page2.addInitScript(() => {
      try { Object.defineProperty(navigator, "canShare", { value: () => false }); } catch (e) {}
      window.__PB3_WATCHDOG_SECONDS = 0.5;
    });
    await page2.goto("http://127.0.0.1:" + PORT + "/index.html");
    await page2.waitForFunction(
      () => { const el = document.getElementById("status"); return el && el.textContent.includes("Sẵn sàng"); },
      null, { timeout: 240000 });
    await page2.click("#btn-card-batch");
    await page2.waitForFunction(
      () => { const el = document.getElementById("tab-batch"); return el && !el.hidden; },
      null, { timeout: 30000 });
    await page2.setInputFiles("#batch-files", [BIG]);
    await page2.fill("#batch-manifest", JSON.stringify([
      { path: "bigtest.zip", mode: "extract" },
      { path: "bigtest.zip", mode: "extract", locale: "en" },
      { path: "bigtest.zip", mode: "extract", locale: "vi" },
    ]));
    await page2.click("#btn-batch-run");
    await page2.waitForFunction(
      () => { const b = document.getElementById("btn-batch-continue"); return b && !b.hidden; },
      null, { timeout: 300000 });
    const resW = await page2.evaluate(() => (document.getElementById("batch-result") || {}).textContent || "");
    const ckptW = await page2.evaluate(async () => {
      const db = await new Promise((res, rej) => {
        const r = indexedDB.open("pb3-kb-store", 1);
        r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
      });
      const v = await new Promise((res, rej) => {
        const t = db.transaction("blobs", "readonly");
        const q = t.objectStore("blobs").get("batch-checkpoint.json");
        q.onsuccess = () => res(q.result); q.onerror = () => rej(q.error);
      });
      db.close();
      return v ? JSON.parse(new TextDecoder().decode(v)) : null;
    });
    const ckptWN = ckptW && ckptW.games ? Object.keys(ckptW.games).length : 0;
    const stoppedOk = resW.includes("watchdog đã dừng an toàn") &&
      resW.includes("0 ok / 2 lỗi / 1 bỏ qua / 3 game") && ckptWN === 2;
    // Tiếp tục với ngưỡng watchdog BÌNH THƯỜNG (25 giây, như production):
    // hook 0,5 giây mà còn hiệu lực thì đợt Tiếp tục (3 game, 2 lượt ~4s)
    // sẽ bị watchdog dừng lại lần nữa và không bao giờ ra "0 ok / 3 lỗi".
    await page2.evaluate(() => { window.__PB3_WATCHDOG_SECONDS = 25; });
    await page2.click("#btn-batch-continue");
    await page2.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("0 ok / 3 lỗi / 0 bỏ qua / 3 game"); },
      null, { timeout: 300000 });
    const resW2 = await page2.evaluate(() => (document.getElementById("batch-result") || {}).textContent || "");
    const contHidden = await page2.evaluate(() => (document.getElementById("btn-batch-continue") || {}).hidden);
    await ctx2.close();
    rec("browser_watchdog_stop_and_continue", stoppedOk && contHidden === true,
      "ngưỡng 0,5s (test hook): 2 game bigtest chạy song song ~2s, game thứ 3 bị " +
      "từ chối E_WATCHDOG_STOP -> '0 ok / 2 lỗi / 1 bỏ qua / 3 game' + 'watchdog đã dừng " +
      "an toàn', checkpoint giữ 2 game; Tiếp tục (ngưỡng trả về 25s như production, " +
      "không thì hook 0,5s dừng lại lần nữa) -> game 3 chạy hết: " +
      JSON.stringify(resW2.slice(0, 80)) + "…, nút Tiếp tục đã ẩn",
      performance.now() - t0);
  }
  { // D6. Fallback 1 worker: bỏ chọn pool -> chạy bằng worker đơn, vẫn 2 ok
    const t0 = performance.now();
    await page.uncheck("#batch-use-pool");
    await page.setInputFiles("#batch-files", [GAME1, GAME1]);
    await page.fill("#batch-manifest", JSON.stringify([
      { path: "game1.zip", mode: "extract" },
      { path: "game1.zip", mode: "extract", locale: "en" },
    ]));
    await page.click("#btn-batch-run");
    await page.waitForFunction(
      () => { const el = document.getElementById("batch-result"); return el && el.textContent.includes("2 ok / 0 lỗi / 0 bỏ qua / 2 game"); },
      null, { timeout: 300000 });
    const res = await batchResultText();
    rec("browser_fallback_single_worker", res.includes("1 worker") && res.includes("worker 0: 2 game"),
      "bỏ chọn pool -> kết quả ghi '1 worker', phân công worker 0: 2 game, vẫn 2 ok; " +
      JSON.stringify(res.slice(0, 120)) + "…", performance.now() - t0);
  }
  { // D7. Không lỗi trang trong suốt phiên trình duyệt
    const t0 = performance.now();
    rec("browser_no_pageerrors", pageErrors.length === 0,
      "pageerror trong cả phiên (boot + pool + reload + resume + dừng/Tiếp tục + watchdog + fallback): " +
      (pageErrors.length ? JSON.stringify(pageErrors) : "không có"), performance.now() - t0);
  }
  await browser.close();
  browser = null;
} catch (e) {
  if (browser) { try { await browser.close(); } catch (_) {} }
  rec("browser_end_to_end", false, "LỖI trình duyệt thật: " + String(e && e.message ? e.message : e), undefined);
}
server.close();

fs.writeFileSync(EXP + "/item4_results.json", JSON.stringify(
  { when: new Date().toISOString(), failures, results }, null, 2));
const lines = ["# Kiểm chứng Mục 4 — Pool 2 worker + checkpoint từng game + watchdog hợp tác", "",
  "Ngày: 2026-11-16. Harness: exp/verify_item4_pool.mjs.",
  "A: unit WorkerPool với Worker giả. B: bridge thật trên Pyodide Node (1 + 2 instance).",
  "C: kiểm tra tĩnh. D: trình duyệt thật (Playwright chromium headless shell, serve site qua HTTP local).",
  "",
  "## Kết quả",
  ...Object.entries(results).map(([k, v]) => `- ${v.ok ? "PASS" : "FAIL"} **${k}**${v.seconds != null ? " (" + v.seconds + "s)" : ""} — ${v.detail}`),
  "",
  "Tổng: " + Object.keys(results).length + " mục, " + failures + " FAIL.",
  "",
  "## Ghi chú",
  "- Watchdog ở phần D dùng hook test `window.__PB3_WATCHDOG_SECONDS = 0.5` (app.js chỉ đọc khi có, production dùng hằng số 25 giây).",
  "- Fixture bigtest.zip (4000 file text, extract ~2 giây rồi E_NO_CANDIDATE) do harness tự tạo ở exp/fixtures/bigtest.zip.",
  "- Pool 2 worker tốn ~2x RAM, chưa đo trên iPhone thật — cần Joseph đo trên Safari thật.",
];
fs.writeFileSync(EXP + "/item4_results.md", lines.join("\n") + "\n");
console.log("\n==== TỔNG: " + Object.keys(results).length + " mục, " + failures + " FAIL ====");
process.exit(failures ? 1 : 0);
