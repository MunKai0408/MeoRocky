// worker.js — Chạy Pyodide + PB3 trong Web Worker.
// Lý do dùng Worker: Pyodide chạy đồng bộ, nếu chạy ở main thread thì giao
// diện sẽ đơ trong lúc xử lý file. Trong Worker, Python vẫn chạy, còn main
// thread nhận message tiến độ và vẽ thanh % + ETA theo thời gian thực.
//
// Giao thức:
//   main -> worker: {id, op:"boot"} | {id, op:"run", fn, args, files:[{name, data:ArrayBuffer}]}
//   worker -> main: {type:"status", text} | {type:"progress", kind, done, total}
//                  | {id, ok:true, result: string(JSON), file?: {name, data:ArrayBuffer}}
//                  | {id, ok:false, error: string}

// Pyodide được vendor ngay trong repo (thư mục pyodide/) để tránh lỗi CORS
// của Safari khi tải cross-origin từ CDN trong Worker.
// Worker này PHẢI là module worker (app.js tạo bằng { type: "module" }):
// Pyodide yêu cầu module worker vì pyodide.asm.mjs là ES module — worker
// classic (nạp script kiểu cũ) bị từ chối ("Classic web workers are not
// supported"). Vì vậy file này dùng import ES module, không dùng cách nạp
// script của worker classic.
import { loadPyodide } from "./pyodide/pyodide.mjs";
import { googleTranslateOne } from "./gt.js";

const PYODIDE_INDEX = new URL("pyodide/", self.location.href).href;
const PB3_ROOT = "/pb3web";

let pyodide = null;
let bridge = null;
// Cờ hủy dịch Google: main thread gửi {op:"cancel_translate"} thì bật.
// Python (async) kiểm tra cờ này trước mỗi chuỗi — vì await fetch nhường
// lại event loop nên worker vẫn nhận được message hủy giữa đợt dịch.
let googleCancelled = false;

function status(text) {
  postMessage({ type: "status", text });
}

function fail(id, error) {
  postMessage({ id, ok: false, error: String(error && error.message ? error.message : error) });
}

async function boot() {
  status("Đang tải Pyodide…");
  pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });
  status("Đang tải binding lz4…");
  try {
    await pyodide.loadPackage("lz4");
  } catch (e) {
    // Không có binding lz4 vẫn chạy được bằng lz4pure (chậm hơn).
    status("Không tải được binding lz4, dùng lz4pure (chậm hơn).");
  }

  status("Đang tải mã PB3…");
  const man = await (await fetch("pb3/files.json")).json();
  const files = man.files;
  let done = 0;
  const BATCH = 8;
  for (let i = 0; i < files.length; i += BATCH) {
    const chunk = files.slice(i, i + BATCH);
    await Promise.all(chunk.map(async (rel) => {
      const res = await fetch(rel);
      if (!res.ok) throw new Error("Không tải được " + rel + " (HTTP " + res.status + ")");
      const buf = new Uint8Array(await res.arrayBuffer());
      const target = PB3_ROOT + "/" + rel.slice(4); // bỏ tiền tố "pb3/"
      const dir = target.slice(0, target.lastIndexOf("/"));
      pyodide.FS.mkdirTree(dir);
      pyodide.FS.writeFile(target, buf);
    }));
    done += chunk.length;
    postMessage({ type: "progress", kind: "load", done, total: files.length });
  }

  // Font đi kèm: base64 -> bytes, ghi vào FS để bridge dùng khi cần.
  status("Đang chuẩn bị font Be Vietnam Pro…");
  const b64 = await (await fetch("fonts/BeVietnamPro-Regular.ttf.b64")).text();
  const bin = atob(b64.replace(/\s+/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  pyodide.FS.mkdirTree(PB3_ROOT + "/fonts");
  pyodide.FS.writeFile(PB3_ROOT + "/fonts/BeVietnamPro-Regular.ttf", bytes);

  status("Đang khởi động PB3…");
  pyodide.runPython("import sys; sys.path.insert(0, '/pb3web')");
  pyodide.globals.set("pb3_post_progress", (kind, done, total) => {
    postMessage({ type: "progress", kind, done, total });
  });
  // Dịch Google: Python gọi hàm JS này qua module `js` (xem gt.js).
  // QUAN TRỌNG: phải gán vào globalThis thật — `pyodide.globals.set`
  // chỉ đặt biến toàn cục PYTHON (__main__), module `js` không thấy nó.
  // Python async await được Promise JS nên event loop vẫn chạy, nút Hủy
  // vẫn nhận được giữa đợt dịch.
  globalThis.pb3_google_translate = googleTranslateOne;
  globalThis.pb3_google_cancelled = () => googleCancelled;
  pyodide.runPython(
    "import bridge\n" +
    "bridge.set_progress_cb(pb3_post_progress)\n" +
    "bridge.__boot_result__ = bridge.boot()\n"
  );
  bridge = pyodide.globals.get("bridge");
  const bootResult = pyodide.globals.get("bridge").__boot_result__;
  const donor = JSON.parse(bridge.font_donor_info(PB3_ROOT + "/fonts/BeVietnamPro-Regular.ttf"));
  return { boot: JSON.parse(bootResult), donor };
}

self.onmessage = async (ev) => {
  const msg = ev.data || {};
  try {
    if (msg.op === "cancel_translate") {
      googleCancelled = true;
      postMessage({ type: "cancelled" });
      return;
    }
    if (msg.op === "boot") {
      const r = await boot();
      postMessage({ id: msg.id, ok: true, result: JSON.stringify(r) });
      return;
    }
    if (msg.op === "run") {
      if (!bridge) throw new Error("PB3 chưa khởi động xong.");
      // Đợt dịch mới thì reset cờ hủy.
      if (typeof msg.fn === "string" && msg.fn.indexOf("translate") === 0) {
        googleCancelled = false;
      }
      for (const f of (msg.files || [])) {
        pyodide.FS.mkdirTree("/tmp/input");
        pyodide.FS.writeFile("/tmp/input/" + f.name, new Uint8Array(f.data));
      }
      const fn = bridge[msg.fn];
      if (typeof fn !== "function") throw new Error("Không có hàm bridge." + msg.fn);
      let out = fn.apply(bridge, msg.args || []);
      // Hàm bridge async (vd translate_json_google) trả về Promise —
      // await để lấy chuỗi JSON thật thay vì "[object Promise]".
      if (out && typeof out.then === "function") out = await out;
      const result = (typeof out === "string") ? out : String(out);
      let file = null;
      let parsed = null;
      try { parsed = JSON.parse(result); } catch (e) { /* không phải JSON */ }
      // Nếu kết quả chỉ tới 1 file output trong FS thì gửi kèm bytes để tải về.
      if (parsed && parsed.ok && typeof parsed.dest === "string") {
        try {
          const data = pyodide.FS.readFile(parsed.dest);
          file = { name: parsed.dest.split("/").pop(), data: data.buffer };
        } catch (e) { /* dest không phải file (vd extract dùng zip_path) */ }
      }
      if (parsed && parsed.ok && typeof parsed.zip_path === "string") {
        try {
          const data = pyodide.FS.readFile(parsed.zip_path);
          file = { name: "extract.zip", data: data.buffer };
        } catch (e) { /* bỏ qua */ }
      }
      const reply = { id: msg.id, ok: true, result, file };
      postMessage(reply, file ? [file.data] : []);
      return;
    }
    throw new Error("Lệnh không rõ: " + msg.op);
  } catch (e) {
    fail(msg.id, e);
  }
};
