// clone_fix_worker.mjs — Worker THẬT (worker_threads) dùng riêng cho harness
// exp/verify_clone_fix.mjs. Mô phỏng đúng giao thức của site/worker.js:
//   main -> worker: {id, op:"boot"} | {id, op:"run", fn, args, files:[{name, data}]}
//   worker -> main: {id, ok:true, result: string(JSON)} | {id, ok:false, error}
// File nhận được giữ trong Map (mô phỏng FS /tmp/input của worker.js) và
// KHÔNG bị xóa giữa các lần gọi — giống FS worker thật sống suốt phiên.
// Vì vậy harness kiểm chứng được đúng hành vi hotfix clone: gửi file 1 lần,
// các thao tác sau dùng lại file đã lưu, buffer gốc không bị detached.
//
// KHÔNG PHẢI code chạy thật: tên entry ZIP ở đây được giải bằng cùng quy tắc
// như bridge thật đã sửa (UTF-8 không cờ bit 11 -> giải lại UTF-8), để khu
// vực list/hint đi qua đúng dữ liệu; bằng chứng cho bridge THẬT nằm ở mục
// pyodide_bridge_mojibake của harness (chạy bridge.py thật trong Pyodide).
import { parentPort } from "node:worker_threads";

const stored = new Map(); // name -> ArrayBuffer (file đã nhận)
let receivedCount = 0;
const receivedLog = [];
const failNext = new Map(); // fn -> số lần sẽ fail (harness đặt qua __fail_next)

function listZipEntries(buf) {
  const b = Buffer.from(buf);
  // Tìm EOCD trong 64KB cuối.
  let eocd = -1;
  for (let i = b.length - 22; i >= Math.max(0, b.length - 65557); i--) {
    if (b.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) return [{ path: "(không phải zip)", size: b.length, kind: "file" }];
  const count = b.readUInt16LE(eocd + 10);
  let off = b.readUInt32LE(eocd + 16);
  const out = [];
  for (let n = 0; n < count; n++) {
    if (b.readUInt32LE(off) !== 0x02014b50) break;
    const flags = b.readUInt16LE(off + 8);
    const size = b.readUInt32LE(off + 24);
    const nameLen = b.readUInt16LE(off + 28);
    const extraLen = b.readUInt16LE(off + 30);
    const commentLen = b.readUInt16LE(off + 32);
    const raw = b.subarray(off + 46, off + 46 + nameLen);
    let name;
    if (flags & 0x800) name = new TextDecoder("utf-8").decode(raw);
    else {
      // Mô phỏng bridge thật đã sửa: byte UTF-8 không cờ -> giải lại UTF-8.
      try { name = new TextDecoder("utf-8", { fatal: true }).decode(raw); }
      catch { name = new TextDecoder("latin1").decode(raw); }
    }
    out.push({ path: name, size, kind: name.endsWith("/") ? "dir" : "file" });
    off += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

function resultFor(fn, args) {
  const inputName = args && args[0] ? String(args[0]).split("/").pop() : "";
  const data = stored.get(inputName);
  switch (fn) {
    case "list_entries": {
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      const entries = listZipEntries(data);
      return { ok: true, kind: "zip", total: entries.length, entries };
    }
    case "unity_inspect":
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      // Mô phỏng bridge thật (hotfix Safari đợt 2): ZIP/IPA không còn lỗi
      // E_UNSUPPORTED_FORMAT mà trả container + danh sách bundle/.assets để
      // UI cho user chọn inspect trực tiếp. Fixture ZIP ở đây không chứa
      // bundle nào nên bundles=[] (UI hiện hướng dẫn trích xuất trước).
      if (/\.zip$/i.test(inputName)) {
        const entries = listZipEntries(data);
        return { ok: true, is_container: true, kind: "zip", bundles: [], bundles_total: 0, total_entries: entries.length };
      }
      return { ok: true, bundle: false, files: [{ entry: inputName, version: 17, unity_version: "2019.4.40f1", objects: 3, text_assets: [{ path_id: 1001, name: "en", head: "Play" }] }] };
    case "extract":
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      return { ok: true, decision: "extract", total_entries: 2, files: [{ path: "en.json", plugin: "json_text", entries: 2, locale: "en", exported: "{\"play\":\"Play\"}" }], errors: [] };
    case "diagnose":
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      return { ok: true, kind: "zip", size: data.byteLength, entries: listZipEntries(data), hex: ["0000: 50 4b 03 04"], strings: [{ offset: 0, text: "PK" }], strings_note: "" };
    case "font_scan":
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      return { ok: true, family: "TestFont", kind: "TTF", num_glyphs: 100, covered: 142, total: 142, pct: 100, summary: "đủ tiếng Việt", missing_chars: [] };
    case "compress_file":
      if (!data) return { ok: false, error: "worker chưa có file " + inputName };
      return { ok: true, original_size: data.byteLength, compressed_size: 10, ratio: 1, saved_pct: 0, entries: 2, target_met: true, ram_warning: "" };
    case "batch_run": {
      if (!stored.size) return { ok: false, error: "worker chưa có file batch nào" };
      return { ok: true, dry_run: true, resumed: false, checkpoint: "{\"done\":[]}", note: "batch test", summary: { ok: 1, failed: 0, skipped: 0, total: 1, elapsed_s: 0.1, games: [{ index: 0, path: "b1.zip", mode: "extract", status: "ok", error_code: "", error_message: "", elapsed_s: 0.1, note: "" }] } };
    }
    default:
      return { ok: false, error: "fn không có trong worker test: " + fn };
  }
}

parentPort.on("message", (msg) => {
  if (!msg || typeof msg !== "object") return;
  if (msg.__probe) { // truy vấn nội bộ của harness, app.js bỏ qua
    if (msg.op === "__state") {
      parentPort.postMessage({ __probe: msg.__probe, receivedCount, receivedLog: [...receivedLog], stored: [...stored.keys()] });
    } else if (msg.op === "__fail_next") {
      failNext.set(msg.fn, (failNext.get(msg.fn) || 0) + 1);
      parentPort.postMessage({ __probe: msg.__probe, ok: true });
    }
    return;
  }
  if (msg.op === "boot") {
    parentPort.postMessage({ id: msg.id, ok: true, result: JSON.stringify({ boot: { ok: true, plugins: "20 plugin", problems: [] }, donor: { is_full: true } }) });
    return;
  }
  if (msg.op === "run") {
    for (const f of (msg.files || [])) {
      stored.set(f.name, f.data);
      receivedCount++;
      receivedLog.push(f.name);
    }
    if (failNext.get(msg.fn)) {
      failNext.set(msg.fn, failNext.get(msg.fn) - 1);
      parentPort.postMessage({ id: msg.id, ok: false, error: "lỗi giả lập cho " + msg.fn });
      return;
    }
    // Bridge thật trả lỗi ứng dụng (vd inspect ZIP) dưới dạng JSON
    // {ok:false} trong `result`, còn reply worker vẫn ok:true — runOp chỉ
    // reset cờ gửi-file khi worker NÉM lỗi (reply ok:false). Giữ đúng vậy
    // ở đây, nếu không harness sẽ đo sai hành vi reset cờ.
    const r = resultFor(msg.fn, msg.args || []);
    parentPort.postMessage({ id: msg.id, ok: true, result: JSON.stringify(r) });
    return;
  }
});
