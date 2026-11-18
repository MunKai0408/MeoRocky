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
import { setTranslateProxyUrl, getTranslateProxyUrl, checkProxyStatus, translateOneWithFallbacks,
         translateBatchWithFallbacks, abortGoogleTranslate,
         resetGoogleTranslateAbort, getFallbackTrace,
         setLibreTranslateEndpoint, getLibreTranslateEndpoint, setLibreTranslateApiKey } from "./gt.js";

const PYODIDE_INDEX = new URL("pyodide/", self.location.href).href;
const PB3_ROOT = "/pb3web";

let pyodide = null;
let bridge = null;
// Cờ hủy dịch Google: main thread gửi {op:"cancel_translate"} thì bật.
// Python (async) kiểm tra cờ này trước mỗi chuỗi — vì await fetch nhường
// lại event loop nên worker vẫn nhận được message hủy giữa đợt dịch.
let googleCancelled = false;

// ---- Watchdog HỢP TÁC cho batch (Mục 4) ----
// KHÔNG dùng Promise.race + terminate trần: terminate giữa chừng sẽ mất
// hết tiến độ game đang chạy (FS worker + checkpoint trong RAM worker bay
// màu). Thay vào đó worker TỰ kiểm tra đồng hồ GIỮA các game: trước mỗi
// lệnh batch_run (1 game), nếu đã quá hạn hoặc main thread đã yêu cầu dừng
// an toàn thì từ chối chạy game mới (lỗi E_WATCHDOG_STOP / E_BATCH_STOPPED)
// — game đang chạy vẫn chạy xong bình thường, checkpoint của nó đã được
// BatchRunner lưu sau mỗi game nên không mất gì. Main thread nhận lỗi thì
// dừng giao việc tiếp, lưu checkpoint gộp vào IndexedDB và hiện nút
// "Tiếp tục" cho user. Ngưỡng mặc định 25 giây — ngắn hơn giới hạn iOS
// kill tab nền (thường ~30 giây khi tab bị ẩn), để còn kịp lưu checkpoint
// và dừng có trật tự. Main thread có hằng số gương BATCH_WATCHDOG_SECONDS
// trong app.js (giữ đồng bộ); batch_begin có thể truyền budgetSeconds khác.
const WATCHDOG_SECONDS = 25;
let batchDeadlineMs = 0;      // 0 = chưa batch_begin, không giới hạn
let batchBudgetSeconds = WATCHDOG_SECONDS;
let batchStopRequested = false;

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

  // Zstd qua fzstd (mục C5): bộ giải nén Zstd thuần JS, vendored cùng
  // nguồn ở fzstd/index.mjs (MIT, Arjun Barrett). Python (sdk/unity.py)
  // gọi hàm này qua module `js` — ĐÚNG tiền lệ Google Translate ở dưới:
  // phải gán vào globalThis thật, `pyodide.globals.set` chỉ đặt biến
  // toàn cục PYTHON, module `js` không thấy nó.
  // Dùng dynamic import + try/catch để boot KHÔNG chết nếu file fzstd
  // thiếu/lỗi: khi đó globalThis.pb3_zstd_decompress = null và Python
  // báo lỗi rõ ràng "cần fzstd trên web" khi gặp block Zstd.
  // fzstd.decompress(data): không truyền out thì cỡ giải nén đọc từ
  // header frame; frame không kèm content size thì thử lại với buffer
  // đúng cỡ kỳ vọng (expectedSize từ header block của bundle).
  status("Đang tải bộ giải nén Zstd (fzstd)…");
  try {
    const fzstd = await import("./fzstd/index.mjs");
    const fzstdDecompress = fzstd.decompress;
    globalThis.pb3_zstd_decompress = (data, expectedSize) => {
      const input = data instanceof Uint8Array ? data : new Uint8Array(data);
      try {
        return fzstdDecompress(input);
      } catch (e) {
        if (typeof expectedSize === "number" && expectedSize > 0) {
          return fzstdDecompress(input, new Uint8Array(expectedSize));
        }
        throw e;
      }
    };
  } catch (e) {
    globalThis.pb3_zstd_decompress = null;
    status("Không tải được fzstd — bundle nén Zstd sẽ báo lỗi rõ ràng.");
  }

  // files.json CHỈ liệt kê file worker CẦN trong FS Pyodide; vòng lặp
  // dưới fail-fast theo thiết kế (1 file thiếu là boot chết — đúng cho
  // mã PB3). Tài sản web (script UI nạp bằng <script> ở main thread)
  // KHÔNG được đưa vào đây: P0 2026-11-18, colab_handoff.js 404 do
  // cache deploy đã giết toàn bộ boot. File shell như vậy ở trong
  // sw.js precache + tự xử lý lỗi nạp ở chỗ dùng nó.
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
  // translateOneWithFallbacks: proxy Cloudflare (nếu app.js đã gửi URL) ->
  // Google trực tiếp -> MyMemory -> LibreTranslate (nếu đã cấu hình) ->
  // ném lỗi để Python dùng từ điển offline.
  // Riêng proxy báo hết hạn mức tháng (quota) thì bỏ qua Google trực tiếp.
  // translateBatchWithFallbacks: như trên nhưng cho cả batch (proxy-batch
  // hoặc gtx joined-text), trả chuỗi JSON {"translations":[...], "mode":...}.
  // pb3_fallback_trace: dấu vết bước fallback của lần dịch gần nhất
  // (mục C1) để bridge.py đọc sau mỗi chuỗi và báo rõ bước nào đã dùng.
  globalThis.pb3_google_translate = translateOneWithFallbacks;
  globalThis.pb3_google_translate_batch = translateBatchWithFallbacks;
  globalThis.pb3_fallback_trace = () => JSON.stringify(getFallbackTrace());
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
      // Hủy cả fetch đang chạy trong gt.js (batch đang dịch dở sẽ ném
      // lỗi kind "cancelled" để Python dừng và giữ phần đã dịch).
      abortGoogleTranslate();
      postMessage({ type: "cancelled" });
      return;
    }
    if (msg.op === "batch_begin") {
      // Bắt đầu một đợt batch: đặt hạn watchdog + bỏ cờ dừng cũ.
      // budgetSeconds không truyền thì dùng WATCHDOG_SECONDS ở đầu file.
      batchBudgetSeconds = (typeof msg.budgetSeconds === "number" && msg.budgetSeconds > 0)
        ? msg.budgetSeconds : WATCHDOG_SECONDS;
      batchDeadlineMs = Date.now() + batchBudgetSeconds * 1000;
      batchStopRequested = false;
      if (msg.id != null) {
        postMessage({ id: msg.id, ok: true,
          result: JSON.stringify({ watchdog_seconds: batchBudgetSeconds }) });
      }
      return;
    }
    if (msg.op === "batch_stop") {
      // "Dừng an toàn": game đang chạy vẫn xong, game sau không chạy nữa.
      batchStopRequested = true;
      if (msg.id != null) {
        postMessage({ id: msg.id, ok: true, result: JSON.stringify({ stopped: true }) });
      }
      return;
    }
    if (msg.op === "set_translate_proxy") {
      // URL proxy dịch Cloudflare Worker (app.js gửi từ localStorage).
      // Để trống = gọi trực tiếp Google như trước. Chỉ đặt biến JS trong
      // worker, không cần Pyodide đã boot xong.
      setTranslateProxyUrl(typeof msg.url === "string" ? msg.url : "");
      postMessage({ id: msg.id, ok: true, result: JSON.stringify({ proxy: !!msg.url }) });
      return;
    }
    if (msg.op === "check_translate_proxy") {
      // T1: kiểm tra trạng thái Cloudflare Worker (GET /) để tab Dịch hiển
      // thị "Worker: đang dùng / chưa triển khai / lỗi". Chỉ fetch JS thuần,
      // không cần Pyodide đã boot — như diag_state. Không chặn luồng dịch:
      // worker lỗi/vắng thì dịch vẫn chạy qua chuỗi fallback như trước.
      const st = await checkProxyStatus(getTranslateProxyUrl());
      postMessage({ id: msg.id, ok: true, result: JSON.stringify(st) });
      return;
    }
    if (msg.op === "set_libretranslate") {
      // LibreTranslate (mục C1, tùy chọn): endpoint + key (nếu có) do
      // người dùng dán ở tab Dịch, lưu localStorage. Để trống = tắt bước
      // này (mặc định), chuỗi fallback vẫn proxy → Google → MyMemory →
      // từ điển offline. Chỉ đặt biến JS trong worker.
      setLibreTranslateEndpoint(typeof msg.endpoint === "string" ? msg.endpoint : "");
      setLibreTranslateApiKey(typeof msg.apiKey === "string" ? msg.apiKey : "");
      postMessage({ id: msg.id, ok: true,
        result: JSON.stringify({ libretranslate: !!msg.endpoint }) });
      return;
    }
    if (msg.op === "diag_state") {
      // Mục C2: trạng thái chẩn đoán cho tab Chẩn đoán (không cần Pyodide
      // đã boot — chỉ đọc cấu hình provider + dấu vết lần dịch gần nhất).
      postMessage({ id: msg.id, ok: true, result: JSON.stringify({
        proxy_configured: !!getTranslateProxyUrl(),
        libretranslate_configured: !!getLibreTranslateEndpoint(),
        last_trace: getFallbackTrace(),
        pyodide_loaded: !!pyodide, bridge_ready: !!bridge,
      }) });
      return;
    }
    if (msg.op === "diag_translate_probe") {
      // Mục C2: dịch thử 1 chuỗi "Hello world" qua đủ chuỗi fallback C1
      // (proxy -> Google -> MyMemory -> LibreTranslate nếu có). Trả ok:true
      // ở tầng message CẢ KHI dịch thất bại để app.js hiện FAIL kèm lỗi
      // thật + trace từng bước, thay vì ném exception chung chung.
      const t0 = performance.now();
      try {
        const text = await translateOneWithFallbacks("Hello world", "en", "vi");
        const trace = getFallbackTrace();
        const used = trace.find((e) => e.ok && !e.skipped);
        postMessage({ id: msg.id, ok: true, result: JSON.stringify({
          ok: true, text, provider: used ? used.step : null,
          ms: Math.round(performance.now() - t0), trace }) });
      } catch (e) {
        postMessage({ id: msg.id, ok: true, result: JSON.stringify({
          ok: false, error: String(e && e.message || e), kind: e && e.kind,
          ms: Math.round(performance.now() - t0), trace: getFallbackTrace() }) });
      }
      return;
    }
    if (msg.op === "boot") {
      const r = await boot();
      postMessage({ id: msg.id, ok: true, result: JSON.stringify(r) });
      return;
    }
    if (msg.op === "run") {
      if (!bridge) throw new Error("PB3 chưa khởi động xong.");
      // Watchdog hợp tác (Mục 4): chỉ kiểm tra TRƯỚC KHI chạy game mới —
      // không bao giờ ngắt ngang game đang chạy. Game đã xong thì checkpoint
      // của nó đã lưu, main thread sẽ lưu tiếp vào IndexedDB rồi hiện nút
      // "Tiếp tục".
      if (msg.fn === "batch_run") {
        if (batchStopRequested) {
          postMessage({ id: msg.id, ok: false,
            error: "E_BATCH_STOPPED: đã dừng an toàn theo yêu cầu (game đang chạy đã xong, checkpoint đã lưu)." });
          return;
        }
        if (batchDeadlineMs && Date.now() > batchDeadlineMs) {
          postMessage({ id: msg.id, ok: false,
            error: "E_WATCHDOG_STOP: đã chạy quá " + batchBudgetSeconds +
              " giây (ngưỡng watchdog) nên dừng an toàn trước khi iOS kill tab — checkpoint đã lưu, bấm “Tiếp tục” để chạy tiếp." });
          return;
        }
      }
      // Đợt dịch mới thì reset cờ hủy.
      if (typeof msg.fn === "string" && msg.fn.indexOf("translate") === 0) {
        googleCancelled = false;
        resetGoogleTranslateAbort();
      }
      // HOTFIX clone: main thread chỉ gửi file 1 lần cho mỗi file đã chọn
      // (xem fileSentFor/batchSentFor trong app.js) — các lần sau msg.files
      // có thể RỖNG vì file đã nằm trong FS /tmp/input từ lần gửi đầu.
      // FS của worker sống suốt phiên trang nên cứ dùng lại, không đòi gửi lại.
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
      // Tên tải về: bridge trả "filename" thì dùng đúng tên đó (vd giải nén
      // .tar.xz trả tên file gốc 9Kings_v1.0.5.ipa thay vì restored.bin);
      // không có thì lấy tên file đích trong FS.
      if (parsed && parsed.ok && typeof parsed.dest === "string") {
        try {
          const data = pyodide.FS.readFile(parsed.dest);
          file = { name: (typeof parsed.filename === "string" && parsed.filename) ||
                          parsed.dest.split("/").pop(), data: data.buffer };
        } catch (e) { /* dest không phải file (vd extract dùng zip_path) */ }
      }
      if (parsed && parsed.ok && typeof parsed.zip_path === "string") {
        try {
          const data = pyodide.FS.readFile(parsed.zip_path);
          file = { name: (typeof parsed.filename === "string" && parsed.filename) ||
                          "extract.zip", data: data.buffer };
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
