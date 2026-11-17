// app.js — Giao diện chính (main thread). Mọi xử lý nặng chạy trong worker.js
// (Pyodide + PB3); thread này chỉ vẽ giao diện, thanh tiến độ và tải file về.
//
// Thanh tiến độ (luật PB3 #152-153): % thật theo công việc đã xong, ETA tính
// từ thời gian thực đã chạy, tăng từng 1%, không nhảy cục, không báo láo.
// Khi tổng chưa biết (vd bước inventory của extract) thì chỉ hiện số đã xong
// và thời gian đã chạy, không bịa ETA.

// ---- Worker lười (Mục 6: lazy boot) ----
// Worker (module) + Pyodide CHỈ được tạo khi user chọn file lần đầu hoặc
// gọi thao tác cần PB3 (ensureBoot). Trước đó trang chỉ là shell tĩnh:
// không tải worker.js, pyodide.mjs, Pyodide hay mã PB3.
let worker = null;
let bootPromise = null; // promise boot đang chạy (để 2 lần gọi không boot đúp)
function getWorker() {
  if (worker) return worker;
  worker = new Worker("worker.js", { type: "module" });
  wireWorker(worker);
  // URL proxy dịch (đọc từ localStorage): gửi ngay khi worker vừa tạo,
  // trước cả khi boot — worker nhận được cả trước khi Pyodide boot xong.
  sendProxyUrlToWorker();
  sendLibreTranslateToWorker();
  return worker;
}
const $ = (id) => document.getElementById(id);

const state = {
  booted: false,
  bootInfo: null,
  donor: null,
  file: null,            // {name, size, lastModified, data: ArrayBuffer}
  batchFiles: [],        // [{name, size, lastModified, data}] cho tab Batch (nhiều file)
  // Gửi file sang worker: CHỈ 1 lần/file (cờ fileSentFor/batchSentFor).
  // Gửi bằng structured clone (KHÔNG transfer list, KHÔNG slice(0)):
  // [ĐÃ ĐO THẬT 2026-11-17] tab Safari ~4× dung lượng file + ~250MB nền
  // (Pyodide + trình duyệt); bản copy slice(0) 210MB trên main thread là
  // đỉnh phụ thêm ~1× file ngay lúc trích xuất — IPA Tom 210MB đã sập tab.
  // Structured clone không detach buffer gốc (chỉ transfer mới detach)
  // nên gửi lại vẫn an toàn; cờ fileSentFor/batchSentFor vẫn tránh gửi
  // lại file worker đã giữ trong FS /tmp/input (FS sống suốt phiên).
  // Reset cờ khi chọn file mới và khi một runOp lỗi (để thử lại gửi lại).
  fileSentFor: null,     // "tên|size|lastModified" của file đơn đã gửi
  batchSentFor: null,    // key tương tự cho lô batch
  lastCheckpoint: null,  // chuỗi checkpoint batch mới nhất (để tải về)
  loadedCheckpoint: null, // checkpoint nạp từ localStorage để resume
  lastExtractKv: null,   // JSON text trích xuất được (để dịch tiếp)
  lastExtractFiles: [],  // [{path, plugin, entries, locale, exported, source}] đợt extract vừa rồi (dropdown tab Dịch)
  translateJob: null,    // job dịch Google đang chạy (hàng đợi bền IndexedDB)
  translateCancel: false, // user đã bấm Hủy (dừng sau chunk hiện tại)
  translateChunk: null,  // {idx, total} chunk đang dịch (hiện "đợt X/Y")
  translateResumeJob: null, // job dở dang để nút "Tiếp tục dịch" dùng
  lastTranslated: null, // bản dịch xong giữ trong phiên (mục A): {name, from, to, translated, keys, at, provenance} — nút "Ép lại vào file gốc" (E) lấy thẳng từ đây, khỏi tải lên lại
  translateProvenance: null, // nguồn gốc của nội dung đang nằm trong ô dịch (mục A, cho (E) ép nhiều file): {origin, gameFile, extractName, source, locale} — đặt khi nạp file trích xuất, "upload" khi tải file JSON lên, null khi dán tay không rõ nguồn
  translateCheck: null, // tự kiểm tra lần dịch gần nhất: {complete, missing, wrongChanged, skippedCount, total, done} — nút tải CHẶN khi thiếu/sai
  extractEntries: [],    // entry trích xuất được trong file đang chọn (từ list_entries)
  extractListFor: null,  // "tên|cỡ|lastModified" của file mà extractEntries thuộc về
  extractSelected: null, // Set(vpath) đã tick ở chế độ chọn tay; null = chưa tải danh sách (coi như tự động)
  lastDownload: null,    // file vừa tạo xong (để Chia sẻ / tải blob lại):
                          // {name, blob, via:"memory"} (file nhỏ / fallback)
                          // hoặc {name, via:"opfs", opfsName, size} (file lớn
                          // đã ghi ra OPFS — KHÔNG giữ blob trong RAM, C4)
  lastDownloadPromise: null, // promise của lần download() đang chạy (để nút
                          // Lưu/Chia sẻ đợi ghi OPFS xong mới đọc file ra)
  opfsFallbackReason: null,  // lý do lần ghi OPFS gần nhất rơi về RAM (C4)
  unityBundles: [],      // bundle/.assets trong ZIP/IPA đang inspect
  currentToolTab: "list", // tab công cụ đang mở trong view "Chức năng" (redesign)
  pending: new Map(),    // id -> {resolve, reject, op, t0, lastPct}
  nextId: 1,
  currentOp: null,
  executor: null,        // executor batch hiện tại (pool worker hoặc 1 worker)
  executorNote: "",      // ghi chú executor (hiện ở kết quả batch)
  batchRun: null,        // đợt batch đang/chạy xong gần nhất (Mục 4)
};

const WARN_BYTES = 140 * 1024 * 1024;   // >140MB: cảnh báo rõ, vẫn cho tiếp tục (Mục 5) — [ĐÃ ĐO THẬT 2026-11-17] tab ~4× file + ~250MB nền nên file 140MB đã ~800MB RAM
const STRONG_BYTES = 190 * 1024 * 1024; // >190MB: khuyên mạnh dùng iSH (Mục 5) — ước tính ~1GB, sát giới hạn tab Safari ~1GB
const EXTRACT_WARN_BYTES = 140 * 1024 * 1024; // >140MB: trích xuất hỏi xác nhận trước (đo thật: IPA Tom 210MB sập tab lúc trích xuất; RAM đỉnh ~4× file + ~250MB nền, không phải ~2× như ghi cũ)
const LARGE_FILE_CONFIRM_BYTES = EXTRACT_WARN_BYTES; // >140MB: hộp thoại xác nhận TRƯỚC khi đọc file vào RAM (Mục C4) — nút "Tiếp tục"/"Hủy"
const OPFS_MIN_BYTES = 1 * 1024 * 1024; // output >=1MB thì ghi ra OPFS thay vì ôm Blob trong RAM (Mục C4)
const MAX_BYTES = 2 * 1024 * 1024 * 1024;   // >2GB: từ chối (bridge cũng từ chối)

// Cảnh báo kích thước file (Mục 5 — stream.js, PB3Stream.sizeWarning).
// Trả {level, text}; dùng chung cho chọn file đơn, file batch và trước
// khi chạy thao tác nặng (runOp). Không có PB3Stream thì dùng ngưỡng cũ.
function sizeWarningFor(size) {
  if (typeof PB3Stream !== "undefined" && PB3Stream.sizeWarning) {
    return PB3Stream.sizeWarning(size);
  }
  if (size > MAX_BYTES) return { level: "reject", text: "File quá lớn cho bản web (>2GB). Dùng bản iSH cho file này." };
  if (size > WARN_BYTES) return { level: "warn", text: "Lưu ý: file khá lớn, tab trình duyệt có thể tốn nhiều RAM. Vẫn thử chạy." };
  return { level: "none", text: null };
}

// iOS Safari làm mờ file .ipa trong khung chọn dù input có accept=".ipa"
// (quirk UTI của picker iOS), nên 2 input chính (#file-input, #batch-files)
// KHÔNG dùng accept mà kiểm tra đuôi bằng JS sau khi chọn (không phân biệt
// hoa/thường). File khác đuôi thì báo lỗi thân thiện, không im lặng.
const GAME_EXTS = [".ipa", ".zip", ".unity3d", ".assets", ".ttf", ".json", ".tar.xz"];
// Mục 5: file .gz đơn lẻ cũng chọn được (để tải bản đã giải nén bằng
// DecompressionStream ở tab Danh sách). Giữ riêng ngoài GAME_EXTS vì
// harness phase1_ci kiểm tra đúng từng ký tự mảng GAME_EXTS (fix picker
// .ipa ca3caef) — .gz không phải container game, chỉ là đường gzip nhanh.
const GZIP_EXT = ".gz";
function gameExtOk(name) {
  const lower = (name || "").toLowerCase();
  return GAME_EXTS.some((ext) => lower.endsWith(ext)) || lower.endsWith(GZIP_EXT);
}
function gameExtError(name) {
  return "File \"" + name + "\" không phải định dạng PB3 hỗ trợ (" +
    GAME_EXTS.join(", ") + ", " + GZIP_EXT + "). Hãy chọn file .ipa/.zip/.unity3d/.assets/.ttf/.json/.tar.xz/.gz.";
}

function fmtBytes(n) {
  if (n == null || isNaN(n)) return "?";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + u[i];
}

function fmtTime(s) {
  if (s == null || !isFinite(s)) return "—";
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + " giây";
  return Math.floor(s / 60) + " phút " + (s % 60) + " giây";
}

function setStatus(text) { $("status").textContent = text; }
function showError(text) {
  const el = $("error");
  el.textContent = text;
  el.hidden = false;
}
function clearError() { $("error").hidden = true; $("error").textContent = ""; }

// ---- Chế độ sáng/tối (redesign) ----
// Theme được áp sớm bằng script inline trong <head> của index.html (đọc
// localStorage trước khi vẽ, tránh nháy); ở đây chỉ đồng bộ nút bấm +
// checkbox Cài đặt và đổi theme khi user bấm. document.documentElement
// được guard vì một số harness nạp app.js trong DOM giả không có nó.
const THEME_KEY = "pb3_theme";
function currentTheme() {
  try { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; } catch (e) { return "dark"; }
}
function syncThemeUI() {
  const mode = currentTheme();
  const b = $("btn-theme");
  if (b) b.textContent = mode === "light" ? "☀️" : "🌙";
  const c = $("setting-theme");
  if (c) c.checked = (mode === "light");
}
function setTheme(mode) {
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) { /* localStorage không có */ }
  if (typeof document !== "undefined" && document.documentElement) {
    if (mode === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
  }
  syncThemeUI();
}
$("btn-theme").addEventListener("click", () => setTheme(currentTheme() === "light" ? "dark" : "light"));
$("setting-theme").addEventListener("change", (ev) => setTheme(ev.target.checked ? "light" : "dark"));
syncThemeUI();

// ---- Thanh tiến độ ----
let progT0 = 0, progLastPct = -1, progLabel = "";
function progStart(label) {
  progT0 = performance.now();
  progLastPct = -1;
  progLabel = label;
  $("progress-wrap").hidden = false;
  progSet(0, 0, -1);
}
function progSet(pct, done, total) {
  pct = Math.max(0, Math.min(100, Math.round(pct)));
  if (pct < progLastPct) return;           // không bao giờ lùi
  if (pct === progLastPct) return;         // chỉ vẽ khi đổi % (từng 1%)
  progLastPct = pct;
  $("progress-bar").style.width = pct + "%";
  $("progress-pct").textContent = pct + "%";
  const elapsed = (performance.now() - progT0) / 1000;
  let eta = null;
  if (total > 0 && done > 0) eta = (elapsed / done) * (total - done);
  const totalTxt = total > 0 ? fmtBytes(total) : "?";
  $("progress-detail").textContent =
    progLabel + " · " + done + "/" + (total > 0 ? total : "?") +
    (total > 0 ? "" : " đã xong") +
    " · đã chạy " + fmtTime(elapsed) +
    (eta != null ? " · còn ~" + fmtTime(eta) : "");
}
function progDone(label) {
  progSet(100, 1, 1);
  $("progress-detail").textContent = label + " · xong";
  setTimeout(() => { $("progress-wrap").hidden = true; }, 1200);
}

// ---- Worker ----
// Gắn message/error cho worker vừa tạo (gọi từ getWorker, Mục 6).
function wireWorker(w) {
  w.onmessage = (ev) => {
    const msg = ev.data || {};
    if (msg.type === "status") { setStatus(msg.text); return; }
    if (msg.type === "progress") {
      const p = [...state.pending.values()][0];
      if (msg.kind === "load") {
        progSet(100 * msg.done / Math.max(1, msg.total), msg.done, msg.total);
        return;
      }
      if (!p) return;
      // Tiến trình dịch: hiện số chuỗi đã dịch/tổng số + tốc độ + thời gian
      // còn lại ngay trong tab Dịch (khi tab đang mở), để user thấy tool
      // đang chạy chứ không tưởng đứng im.
      if (msg.kind === "translate" && msg.total > 0) {
        const info = $("translate-info");
        const tab = $("tab-translate");
        if (info && tab && !tab.hidden) {
          const elapsed = (performance.now() - progT0) / 1000;
          const rate = elapsed > 0 ? msg.done / elapsed : 0;
          const eta = rate > 0 ? (msg.total - msg.done) / rate : null;
          info.textContent = "Đang dịch: " + msg.done + "/" + msg.total + " chuỗi" +
            (rate > 0 ? " · ~" + rate.toFixed(1) + " chuỗi/giây" : "") +
            (eta != null ? " · còn khoảng " + fmtTime(eta) : "") +
            (state.translateChunk && state.translateChunk.total > 1
              ? " · đợt " + state.translateChunk.idx + "/" + state.translateChunk.total : "");
        }
      }
      if (msg.total > 0) progSet(100 * msg.done / msg.total, msg.done, msg.total);
      else progSet(progLastPct < 0 ? 0 : progLastPct, msg.done, -1);
      return;
    }
    if (msg.id != null && state.pending.has(msg.id)) {
      const p = state.pending.get(msg.id);
      state.pending.delete(msg.id);
      if (msg.ok) p.resolve(msg);
      else p.reject(new Error(msg.error || "Lỗi không rõ"));
    }
  };

  w.onerror = (e) => {
    showError("Worker lỗi: " + (e.message || e));
  };
}

// Mọi op trừ "boot" đều cần Pyodide đã boot: boot lười ở đây (lần đầu
// user gọi thao tác thì boot, đã boot rồi thì đi thẳng). Op "boot" do
// ensureBoot gọi trực tiếp qua callWorkerRaw để khỏi đệ quy.
async function callWorker(op, payload) {
  if (op !== "boot") await ensureBoot();
  return callWorkerRaw(op, payload);
}

function callWorkerRaw(op, payload) {
  return new Promise((resolve, reject) => {
    const id = state.nextId++;
    state.pending.set(id, { resolve, reject, t0: performance.now(), lastPct: -1 });
    // HOTFIX clone: chỉ kèm file vào postMessage khi worker chưa có file đó.
    // File đơn: so bằng tên|size|lastModified với cờ fileSentFor.
    // Batch: so toàn bộ lô với cờ batchSentFor. File lẻ một lần (vd nhập KB)
    // không có cờ thì gửi kèm luôn (structured clone, không detach buffer gốc).
    let files = payload.files || [];
    let toSend = files;
    const filesKey = (list) => list.map((f) => f.name + "|" + f.size + "|" + (f.lastModified || 0)).join(";");
    try {
      if (files.length === 1 && state.file && files[0].data === state.file.data) {
        const key = filesKey([state.file]);
        if (state.fileSentFor === key) toSend = [];
        else { toSend = [{ name: files[0].name, data: files[0].data }]; state.fileSentFor = key; }
      } else if (files.length && state.batchFiles.length && files.length === state.batchFiles.length &&
                 files.every((f, i) => f.data === state.batchFiles[i].data)) {
        const key = filesKey(state.batchFiles);
        if (state.batchSentFor === key) toSend = [];
        else { toSend = files.map((f) => ({ name: f.name, data: f.data })); state.batchSentFor = key; }
      } else if (files.length) {
        toSend = files.map((f) => ({ name: f.name, data: f.data }));
      }
      // Structured clone (không transfer list): worker nhận 1 bản copy, buffer
      // gốc ở main thread còn nguyên để các tab EOCD nhanh dùng tiếp.
      getWorker().postMessage(Object.assign({ id, op }, payload, { files: toSend }));
    } catch (e) {
      // postMessage ném lỗi (vd transfer thất bại): cờ vừa đặt phải bỏ để
      // lần thử sau gửi lại file; promise reject để runOp báo lỗi.
      state.fileSentFor = null;
      state.batchSentFor = null;
      state.pending.delete(id);
      reject(e);
    }
  });
}

async function runOp(label, fn, args, files, fileOut) {
  clearError();
  // Mục 5: cảnh báo kích thước TRƯỚC khi chạy thao tác nặng (file đã chọn
  // >140MB cảnh báo, >190MB khuyên dùng iSH). Cảnh báo hiện ở ô #error
  // như khi chọn file; lỗi thật của thao tác sẽ ghi đè lên sau.
  if (state.file) {
    const w = sizeWarningFor(state.file.size);
    if (w.text) showError(w.text);
  }
  progStart(label);
  try {
    const msg = await callWorker("run", { fn, args: args || [], files: files || [] });
    const parsed = JSON.parse(msg.result);
    // Lỗi từ bridge (parsed.ok === false) thì ném luôn để catch dưới hiện
    // banner CÓ NHÃN thao tác ("<Tên thao tác> lỗi: ..."). Trước đây các
    // handler tự kiểm tra parsed.ok rồi showError("Lỗi: " + ...) nên banner
    // mất nhãn, không biết thao tác nào lỗi.
    if (parsed && parsed.ok === false) {
      // Hiện lý do THẬT thay vì "Lỗi không rõ" (lỗi 2026-11-16): bridge
      // tổng hợp trường error; cộng thêm từng lỗi trong parsed.errors
      // (code + message) và decision + số mục đã bỏ qua để chẩn đoán
      // được trên máy thật. #error có white-space: pre-wrap nên \n
      // hiện thành từng dòng trong banner.
      let msg = parsed.error || parsed.code || "Lỗi không rõ";
      const details = (parsed.errors || [])
        .map((e) => (e && e.code ? "[" + e.code + "] " : "") + (e && (e.message || e.error || "")))
        .filter((d) => d && msg.indexOf(d) === -1);
      if (details.length) msg += "\n" + details.join("\n");
      if (parsed.decision && msg.indexOf(String(parsed.decision)) === -1) {
        msg += "\n(quyết định: " + parsed.decision + "; đã bỏ qua " +
          ((parsed.skipped || []).length) + " mục — xem extraction_report.txt)";
      }
      throw new Error(msg);
    }
    progDone(label);
    if (fileOut && msg.file) {
      // Mục C4: đợi ghi output xong (OPFS: ghi dần từng phần 8MB) rồi mới
      // trả — xong thì bỏ tham chiếu tới buffer output của worker để input
      // + output không cùng nằm trong RAM sau khi xuất (đường OPFS chỉ giữ
      // tên file, đường RAM giữ Blob đã gói — cả hai đều không giữ thêm
      // bản ArrayBuffer gốc của worker).
      await download(msg.file.name, msg.file.data);
      try { msg.file.data = null; } catch (e) { /* bỏ qua */ }
    }
    return { parsed, file: msg.file || null };
  } catch (e) {
    progDone(label + " — lỗi");
    let msg = label + " lỗi: " + e.message;
    // Mục C8: file không nhận ra (không phải container đã biết) thì gợi ý
    // Probe định dạng thay vì chỉ báo lỗi — probe cho báo cáo có cấu trúc
    // (engine đoán được + mức hỗ trợ thật) thay vì một dòng lỗi.
    if (/E_UNSUPPORTED_FORMAT|không nhận diện được container/.test(e.message)) {
      msg += "\nGợi ý: mở tab Chẩn đoán → bấm “Probe định dạng” để xem báo cáo chi tiết về file này (engine đoán được, thao tác nào được/không được hỗ trợ).";
    }
    showError(msg);
    // HOTFIX clone: thao tác lỗi thì lần sau phải gửi lại file cho worker.
    state.fileSentFor = null;
    state.batchSentFor = null;
    throw e;
  }
}

// ---- KB bền vững: IndexedDB + autosave (Mục 3) ----
// KB trước đây mất khi tải lại trang (file SQLite /tmp/kb trong RAM worker).
// Nay bytes KB được lưu vào IndexedDB sau MỖI lần KB thay đổi (thêm/quên/
// nhập/batch ghi), và nạp lại lúc boot. Nút xuất/nhập .sqlite vẫn giữ
// nguyên làm đường sao lưu/khôi phục chính. KHÔNG dùng OPFS/SAH.
const kbStore = (typeof KBStore !== "undefined")
  ? new KBStore("kb.sqlite", { debounceMs: 0 }) : null;
const KB_LAST_EXPORT_KEY = "pb3_kb_last_export";

// ---- Hàng đợi dịch bền (mục A): TranslateJobStore (kb-store.js) ----
// Job dịch Google lưu vào IndexedDB sau mỗi chunk nên tắt tab vẫn tiếp
// tục được; job đã xong đồng thời là bộ nhớ dịch (TM) cho job sau.
const translateJobStore = (typeof TranslateJobStore !== "undefined")
  ? new TranslateJobStore() : null;
const TRANSLATE_CHUNK_KEYS = 500; // số key mỗi lần gọi worker (lưu job sau mỗi chunk)
const TRANSLATE_BATCH_STRINGS = 100; // chuỗi/request — KHỚP BATCH_MAX_STRINGS bên bridge.py
const KB_EXPORT_REMINDER_DAYS = 7;

function kbBytesFromB64(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function kbB64FromBytes(bytes) {
  let s = "";
  const CH = 8192;
  for (let i = 0; i < bytes.length; i += CH) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(s);
}
if (kbStore) {
  kbStore.attach(async () => {
    const msg = await callWorker("run", { fn: "kb_snapshot", args: [] });
    const p = JSON.parse(msg.result);
    if (!p.ok) throw new Error(p.error || p.code || "kb_snapshot lỗi");
    if (!p.b64) return new Uint8Array(0);
    return kbBytesFromB64(p.b64);
  });
  kbStore.onStatus = (st, detail) => {
    const el = $("kb-status");
    if (!el) return;
    if (st === "saving") el.textContent = "KB: đang lưu tự động…";
    else if (st === "saved") refreshKbStatus();
    else if (st === "error") {
      el.textContent = "KB: lưu tự động LỖI (" +
        (detail && detail.message ? detail.message : detail) +
        ") — hãy xuất KB ra file .sqlite để sao lưu.";
    }
  };
}

// Lưu KB vào IndexedDB NGAY sau 1 lần thay đổi (không đợi user bấm export).
// Lỗi lưu không làm hỏng thao tác chính — chỉ hiện ở dòng trạng thái KB.
async function persistKbNow() {
  if (!kbStore) return;
  try {
    kbStore.markDirty();
    await kbStore.flush();
  } catch (e) { /* onStatus('error') đã báo */ }
}

// Nạp KB đã lưu từ IndexedDB vào worker (gọi 1 lần sau khi boot xong,
// trước khi user dùng KB). Trả {restored, games?, size?, reason?}.
async function restoreKbFromIdb() {
  if (!kbStore) return { restored: false, reason: "Trình duyệt này không có IndexedDB" };
  let saved = null;
  try { saved = await kbStore.load(); }
  catch (e) { return { restored: false, reason: "không đọc được IndexedDB: " + e.message }; }
  if (!saved || !saved.length) return { restored: false, reason: "empty" };
  try {
    const msg = await callWorker("run", { fn: "kb_restore", args: [kbB64FromBytes(saved)] });
    const p = JSON.parse(msg.result);
    if (!p.ok) throw new Error(p.error || p.code || "kb_restore lỗi");
    return { restored: true, games: p.games, size: p.size };
  } catch (e) {
    return { restored: false, reason: e.message };
  }
}

// Xin quyền lưu trữ bền vững rồi hiện trạng thái (đã cấp/chưa cấp) +
// dung lượng ước tính (Mục C3: estimate()). Gọi sớm (sau first paint /
// lần tương tác đầu — xem requestStorageEarly ở cuối file), không đợi
// tới lúc boot, để KB có cơ hội được giữ bền ngay từ đầu. persist() bị
// từ chối thì hiện rõ CHƯA CẤP, không ghi PASS giả; estimate() lỗi thì
// ghi rõ không đọc được, không bịa số.
let lastStorageState = null;
async function refreshKbPersistStatus() {
  const el = $("kb-persist");
  let granted = false, persisted = false, usage = null, quota = null, estError = null;
  try {
    granted = kbStore ? await kbStore.requestPersistence()
      : (typeof navigator !== "undefined" && navigator.storage
        ? await navigator.storage.persist() : false);
  } catch (e) { granted = false; }
  try { persisted = await navigator.storage.persisted(); } catch (e) { persisted = granted; }
  try {
    const est = await navigator.storage.estimate();
    usage = typeof est.usage === "number" ? est.usage : null;
    quota = typeof est.quota === "number" ? est.quota : null;
  } catch (e) { estError = e && e.message ? e.message : String(e); }
  lastStorageState = { persisted: !!(persisted || granted), granted: !!granted,
    usage, quota, estError, at: Date.now() };
  if (typeof window !== "undefined") window.__PB3_STORAGE_STATE = () => ({ ...lastStorageState });
  if (!el) return;
  el.textContent = (persisted || granted)
    ? "Lưu trữ bền vững: ĐÃ CẤP — trình duyệt sẽ không tự xóa KB."
    : "Lưu trữ bền vững: CHƯA CẤP — Safari có thể xóa KB sau ~7 ngày không mở trang; hãy cài BearTea vào Màn hình chính (mục dưới) để giữ KB bền hơn.";
  el.textContent += (usage != null && quota != null)
    ? " Đã dùng " + fmtBytes(usage) + " / hạn mức " + fmtBytes(quota) + "."
    : " Không đọc được dung lượng ước tính" + (estError ? " (" + estError + ")" : "") + ".";
}

// Xin persist() sớm (Mục C3): sau first paint (requestIdleCallback /
// setTimeout khi trang đã tải xong) hoặc ngay lần tương tác đầu tiên của
// user — cái nào tới trước. Chỉ gọi 1 lần; không boot Pyodide, không
// chặn vẽ trang. Hook window.__PB3_NO_EARLY_PERSIST để harness tắt.
let earlyPersistDone = false;
function requestStorageEarly() {
  if (earlyPersistDone) return;
  earlyPersistDone = true;
  if (typeof window !== "undefined" && window.__PB3_NO_EARLY_PERSIST) return;
  refreshKbPersistStatus();
  checkKbExportReminderEarly();
}
if (typeof window !== "undefined" && typeof navigator !== "undefined") {
  const scheduleEarlyPersist = () => {
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(() => requestStorageEarly(), { timeout: 5000 });
    } else {
      setTimeout(requestStorageEarly, 1500);
    }
  };
  if (document.readyState === "complete") scheduleEarlyPersist();
  else window.addEventListener("load", scheduleEarlyPersist);
  const onceInteract = () => { requestStorageEarly(); };
  window.addEventListener("pointerdown", onceInteract, { once: true });
  window.addEventListener("keydown", onceInteract, { once: true });
  // Mục C4: dọn file tạm OPFS quá 7 ngày lúc mở trang (chạy nền, lỗi thì
  // im lặng — không chặn trang, không hiện banner).
  try {
    if (typeof PB3OPFS !== "undefined" && PB3OPFS.cleanupStaleOnBoot) {
      setTimeout(() => { PB3OPFS.cleanupStaleOnBoot().catch(() => {}); }, 5000);
    }
  } catch (e) { /* OPFS chưa nạp — bỏ qua */ }
}

// Dòng trạng thái KB: số game, dung lượng, lần tự lưu gần nhất + nhắc export.
async function refreshKbStatus(note) {
  const el = $("kb-status");
  if (!el || !state.booted) return;
  try {
    const msg = await callWorker("run", { fn: "kb_list", args: [] });
    const p = JSON.parse(msg.result);
    if (!p.ok) return;
    const savedAt = kbStore && kbStore.lastSavedAt
      ? new Date(kbStore.lastSavedAt).toLocaleString("vi-VN") : "chưa lưu lần nào";
    el.textContent = "KB: " + p.games.length + " game · " + fmtBytes(p.size_bytes) +
      " · tự lưu vào IndexedDB sau mỗi thay đổi (lần lưu gần nhất: " + savedAt + ")." +
      (note ? " " + note : "");
    updateKbExportReminder(p.games.length);
  } catch (e) { /* trạng thái KB chỉ là phụ: lỗi thì thôi */ }
}

function kbLastExportAt() {
  try {
    const v = localStorage.getItem(KB_LAST_EXPORT_KEY);
    return v ? parseInt(v, 10) : 0;
  } catch (e) { return 0; }
}
function kbRecordExportNow() {
  try { localStorage.setItem(KB_LAST_EXPORT_KEY, String(Date.now())); } catch (e) { /* localStorage không có */ }
}
// Mốc "tắt nhắc lần này": user bấm thì không nhắc lại trong 7 ngày
// (lưu localStorage; hết hạn thì nhắc lại như thường nếu vẫn quá hạn xuất).
const KB_EXPORT_SNOOZE_KEY = "pb3_kb_export_snooze_until";
const KB_EXPORT_SNOOZE_DAYS = 7;
function kbExportSnoozedUntil() {
  try {
    const v = localStorage.getItem(KB_EXPORT_SNOOZE_KEY);
    return v ? parseInt(v, 10) : 0;
  } catch (e) { return 0; }
}
// Nhắc sao lưu: lần xuất cuối > 7 ngày, hoặc chưa từng xuất mà KB đã có
// dữ liệu. Ghi rõ trong UI; không tự tải file về thay user. Có 2 nút:
// "Đã xuất" (ghi mốc vừa xuất xong, ẩn nhắc) và "Tắt nhắc lần này"
// (im 7 ngày rồi nhắc lại nếu vẫn quá hạn).
function updateKbExportReminder(gameCount) {
  const el = $("kb-export-reminder");
  if (!el) return;
  const txt = $("kb-export-reminder-text");
  const last = kbLastExportAt();
  if (Date.now() < kbExportSnoozedUntil()) { el.hidden = true; return; }
  const over7d = last > 0 && (Date.now() - last > KB_EXPORT_REMINDER_DAYS * 86400000);
  const neverWithData = last === 0 && gameCount > 0;
  if (over7d || neverWithData) {
    if (txt) txt.textContent = last === 0
      ? "Nhắc sao lưu: KB đã có dữ liệu nhưng bạn chưa từng xuất file .sqlite — hãy bấm “Tải KB về (.sqlite)” để sao lưu, phòng khi trình duyệt xóa IndexedDB."
      : "Nhắc sao lưu: lần xuất KB cuối đã hơn 7 ngày trước (" +
        new Date(last).toLocaleDateString("vi-VN") +
        ") — hãy bấm “Tải KB về (.sqlite)” để sao lưu bản mới nhất.";
    el.hidden = false;
  } else el.hidden = true;
}
// Kiểm tra nhắc export sớm (Mục C3): không cần boot Pyodide — đọc thẳng
// kích thước KB trong IndexedDB; có dữ liệu mà quá hạn xuất thì nhắc ngay
// từ lúc mở trang. Gọi cùng lúc với requestStorageEarly.
async function checkKbExportReminderEarly() {
  if (!kbStore) return;
  try {
    const saved = await kbStore.load();
    if (saved && saved.length) updateKbExportReminder(1);
  } catch (e) { /* IndexedDB lỗi: nhắc export là phụ, bỏ qua */ }
}
$("btn-kb-exported").addEventListener("click", () => {
  kbRecordExportNow();
  const el = $("kb-export-reminder");
  if (el) el.hidden = true;
});
$("btn-kb-reminder-snooze").addEventListener("click", () => {
  try { localStorage.setItem(KB_EXPORT_SNOOZE_KEY, String(Date.now() + KB_EXPORT_SNOOZE_DAYS * 86400000)); } catch (e) { /* localStorage không có */ }
  const el = $("kb-export-reminder");
  if (el) el.hidden = true;
});

// ---- PWA: cài vào Màn hình chính (Mục 3 + C3) ----
// beforeinstallprompt chỉ có trên Chrome/Android/desktop — Safari iPhone
// KHÔNG bao giờ bắn sự kiện này, nên hướng dẫn tay cho iOS (Chia sẻ →
// Thêm vào Màn hình chính) luôn hiện sẵn ở tab KB; nút “Cài…” chỉ hiện
// khi trình duyệt bắn beforeinstallprompt (có prompt thật để bấm).
// C3 thêm: (a) đã cài/standalone thì ẩn nút, hiện ghi chú đã cài;
// (b) sự kiện appinstalled (cài xong thật) thì ẩn nút + hiện ghi chú;
// (c) user đóng bảng cài thì ghi rõ, không giả vờ nút còn bấm được
// (prompt thật chỉ dùng được 1 lần — tải lại trang mới có lại).
let deferredPwaPrompt = null;
let pwaInstalled = false;
function pwaIsStandalone() {
  try {
    if (typeof window !== "undefined" && window.matchMedia &&
      window.matchMedia("(display-mode: standalone)").matches) return true;
    if (typeof navigator !== "undefined" && navigator.standalone) return true;
  } catch (e) { /* không đọc được: coi như chưa cài */ }
  return false;
}
function refreshPwaInstallUi() {
  const b = $("btn-pwa-install");
  const note = $("pwa-installed-note");
  if (pwaInstalled || pwaIsStandalone()) {
    if (b) b.hidden = true;
    if (note) note.hidden = false;
  }
}
if (typeof window !== "undefined") {
  window.__PB3_PWA_STATE = () => ({ hasPrompt: !!deferredPwaPrompt,
    installed: pwaInstalled || pwaIsStandalone(), standalone: pwaIsStandalone() });
  refreshPwaInstallUi();
}
window.addEventListener("beforeinstallprompt", (ev) => {
  ev.preventDefault();
  deferredPwaPrompt = ev;
  if (pwaInstalled || pwaIsStandalone()) return;
  const b = $("btn-pwa-install");
  if (b) b.hidden = false;
});
window.addEventListener("appinstalled", () => {
  pwaInstalled = true;
  deferredPwaPrompt = null;
  refreshPwaInstallUi();
});
$("btn-pwa-install").addEventListener("click", async () => {
  if (deferredPwaPrompt && typeof deferredPwaPrompt.prompt === "function") {
    const promptEv = deferredPwaPrompt;
    deferredPwaPrompt = null;
    $("btn-pwa-install").hidden = true;
    try {
      await promptEv.prompt();
      const choice = promptEv.userChoice ? await promptEv.userChoice : null;
      if (choice && choice.outcome === "accepted") { pwaInstalled = true; refreshPwaInstallUi(); }
      else if (choice && choice.outcome === "dismissed") {
        const note = $("pwa-note");
        if (note) note.textContent = "Bạn đã đóng bảng cài đặt — tải lại trang để thử lại, hoặc cài bằng tay theo hướng dẫn trên (Safari: Chia sẻ → Thêm vào Màn hình chính).";
      }
    } catch (e) { /* user hủy bảng cài */ }
  }
  // Không có prompt thật (vd Safari): hướng dẫn tay đã hiện sẵn ở tab KB.
});

// ---- Tải file về (Safari) ----
// NGUYÊN NHÂN GỐC download kẹt trên Safari thật (Joseph 11:32–11:36):
// file CÓ nằm trong Files > Tải về nhưng là file dở dang, kẹt mãi không bao
// giờ xong. Safari tạo entry tải về rồi daemon tải đọc blob URL bất đồng bộ
// để ghi file — bản cũ hủy URL sau đúng 5 giây tính từ lúc click, daemon đọc
// file lớn (95MB) chưa xong thì link đã chết → file dở dang kẹt mãi. Giả
// thuyết "bấm Tải về trong 5 giây thì được" đã bị Joseph bác bỏ 11:34 (bấm
// ngay vẫn kẹt) — đường blob a.click() trên Safari iPhone này không đáng tin
// cậy dù bấm nhanh. Vì vậy: (a) giữ URL sống 10 phút, hủy URL lần tải trước
// khi bắt đầu lần mới (chỉ giữ tối đa 1 blob), hủy khi trang đóng (pagehide);
// (b) Share API là ĐƯỜNG LƯU CHÍNH trên iOS (bảng Chia sẻ → "Lưu vào Tệp"),
// blob chỉ là dự phòng khi Share không có sẵn hoặc user chủ động chọn.
let lastDownloadUrl = null;
function revokeLastDownloadUrl() {
  if (lastDownloadUrl) {
    try { URL.revokeObjectURL(lastDownloadUrl); } catch (e) { /* URL đã hủy */ }
    lastDownloadUrl = null;
  }
}
window.addEventListener("pagehide", revokeLastDownloadUrl);
// Kiểm tra xem file này có chia sẻ được bằng Web Share API không.
// (iOS Safari: canShare({files}) trả true — đường này là native, không qua
// download manager nên không kẹt như blob a.click().)
function canShareFile(blob, name) {
  try {
    const f = new File([blob], name, { type: "application/octet-stream" });
    return !!(navigator.canShare && navigator.canShare({ files: [f] }));
  } catch (e) { return false; }
}

// Bấm anchor blob để trình duyệt tải về (đường dự phòng — Safari iPhone kẹt
// cả khi bấm "Tải về" ngay trong 5 giây, Joseph 11:34; chỉ dùng khi Share
// không có sẵn hoặc user chủ động chọn nút “Tải về (blob)”).
function fireBlobDownload(url, name) {
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
}

// Lấy Blob/File của file vừa xuất để Chia sẻ / tải lại — đường OPFS thì
// đọc lại từ OPFS (File lười, không ôm cả file vào RAM), đường RAM thì
// dùng blob đang giữ. Trả null khi không còn file.
async function lastDownloadFile() {
  const d = state.lastDownload;
  if (!d) return null;
  if (d.via === "opfs") {
    if (typeof PB3OPFS === "undefined") return null;
    try { return await PB3OPFS.readOutput(d.opfsName); } catch (e) { return null; }
  }
  return d.blob || null;
}

// Dọn file tạm OPFS của lần xuất hiện tại (sau khi chia sẻ xong / user bấm
// dọn / bấm ✕): xóa file trong OPFS, quên state, ẩn nút dọn.
async function cleanupLastDownloadOpfs() {
  const d = state.lastDownload;
  if (d && d.via === "opfs" && typeof PB3OPFS !== "undefined") {
    try { await PB3OPFS.deleteOutput(d.opfsName); } catch (e) { /* đã dọn */ }
  }
  state.lastDownload = null;
  state.lastDownloadPromise = null;
  const cleanupBtn = $("btn-opfs-cleanup");
  if (cleanupBtn) cleanupBtn.hidden = true;
}

// Trạng thái OPFS cho harness/chẩn đoán (đồng bộ, không đọc OPFS).
function opfsState() {
  const d = state.lastDownload;
  return {
    supported: (typeof PB3OPFS !== "undefined") ? PB3OPFS.supported() : false,
    minBytes: OPFS_MIN_BYTES,
    lastVia: d ? d.via : null,
    lastName: d ? d.name : null,
    lastSize: d ? (d.size || (d.blob ? d.blob.size : 0)) : 0,
    lastHasBlob: !!(d && d.blob),
    fallbackReason: state.opfsFallbackReason,
  };
}
if (typeof window !== "undefined") window.__PB3_OPFS_STATE = opfsState;

function download(name, data) {
  // Trả Promise (ghi OPFS là bất đồng bộ); các caller cũ gọi fire-and-
  // forget vẫn chạy như trước, nút Lưu/Chia sẻ đợi qua lastDownloadPromise.
  state.lastDownloadPromise = (async () => {
    try {
      return await downloadNow(name, data);
    } catch (e) {
      showError("Không lưu được file " + name + ": " + (e && e.message ? e.message : e));
      return null;
    }
  })();
  return state.lastDownloadPromise;
}

async function downloadNow(name, data) {
  // Mục 5: data có thể là Blob, ArrayBuffer/Uint8Array, hoặc MẢNG NHIỀU
  // PART (Blob part) — output lớn ghi dạng nhiều part, không concat thành
  // một ArrayBuffer khổng lồ trước khi tải (new Blob(parts) không copy
  // các part lại với nhau).
  const outSize = (typeof PB3OPFS !== "undefined" && PB3OPFS.dataByteLength)
    ? PB3OPFS.dataByteLength(data)
    : (data instanceof Blob ? data.size
      : Array.isArray(data) ? data.reduce((s, p) => s + (p ? (p.size || p.byteLength || p.length || 0) : 0), 0)
      : (data ? (data.byteLength || data.length || 0) : 0));
  // Thanh Lưu file cố định phía trên bottom nav (redesign): hiện lên khi có
  // file xuất, để tới được từ mọi màn (trước đây nút nằm trong thẻ "1. Chọn
  // file" ở đầu trang, phải cuộn lên mới thấy).
  const bar = $("download-bar");
  if (bar) bar.hidden = false;
  const hint = $("download-hint");
  const shareBtn = $("btn-share-download");
  const blobBtn = $("btn-blob-download");
  const cleanupBtn = $("btn-opfs-cleanup");
  // ---- Mục C4: output lớn ghi dần ra OPFS thay vì ôm Blob trong RAM ----
  // Ghi từng phần 8MB (opfs.js toParts: Blob.slice / subarray, không copy
  // toàn bộ); ghi xong thì state chỉ giữ tên file OPFS, KHÔNG giữ blob —
  // input + output không còn cùng nằm trong RAM sau khi xuất.
  if (outSize >= OPFS_MIN_BYTES && typeof PB3OPFS !== "undefined" && PB3OPFS.supported()) {
    if (hint) {
      hint.textContent = "Đang ghi " + name + " (" + fmtBytes(outSize) +
        ") ra bộ nhớ tạm của trình duyệt (OPFS)…";
      hint.hidden = false;
    }
    const res = await PB3OPFS.writeOutput(name, data);
    if (res.ok) {
      state.opfsFallbackReason = null;
      state.lastDownload = { name, via: "opfs", opfsName: res.name, size: res.size };
      if (cleanupBtn) cleanupBtn.hidden = false;
      const opfsFile = await PB3OPFS.readOutput(res.name);
      if (canShareFile(opfsFile, name)) {
        // ĐƯỜNG LƯU CHÍNH trên iOS: Chia sẻ native (bảng Chia sẻ → "Lưu
        // vào Tệp"), không qua download manager. Không tự bấm blob.
        if (hint) {
          hint.textContent = "File " + name + " (" + fmtBytes(res.size) +
            ") đã ghi vào bộ nhớ tạm (OPFS) — bấm “Lưu / Chia sẻ” để lưu vào Files (cách chắc chắn nhất trên iPhone). " +
            "Nếu muốn tải bằng trình duyệt thì bấm “Tải về (blob)” bên dưới. Xong thì bấm “Dọn file tạm” để giải phóng bộ nhớ.";
          hint.hidden = false;
          try { hint.scrollIntoView({ block: "nearest" }); } catch (e) {}
        }
        if (shareBtn) shareBtn.hidden = false;
        if (blobBtn) blobBtn.hidden = false;
      } else {
        // Không chia sẻ được (vd desktop không hỗ trợ share file): đọc
        // File từ OPFS rồi tải bằng blob như trước.
        await downloadOpfsBlob(opfsFile, name);
        if (hint) {
          hint.textContent = "Đang tải về " + name + " (" + fmtBytes(res.size) +
            ") từ bộ nhớ tạm (OPFS) — trình duyệt sẽ hỏi thì bấm Tải về, giữ tab mở; file lớn có thể cần vài phút. Xong thì bấm “Dọn file tạm”.";
          hint.hidden = false;
        }
        if (shareBtn) shareBtn.hidden = true;
        if (blobBtn) blobBtn.hidden = false;
      }
      return state.lastDownload;
    }
    // Ghi OPFS lỗi → rơi về đường RAM dưới đây, ghi rõ lý do (không crash lặng).
    state.opfsFallbackReason = res.error || "ghi OPFS lỗi không rõ";
  } else if (outSize >= OPFS_MIN_BYTES) {
    state.opfsFallbackReason = (typeof PB3OPFS === "undefined")
      ? "opfs.js chưa nạp"
      : PB3OPFS.unsupportedReason();
  } else {
    state.opfsFallbackReason = null;
  }
  // ---- Đường RAM (file nhỏ, hoặc fallback khi không có OPFS / ghi lỗi) ----
  const blob = data instanceof Blob ? data
    : Array.isArray(data) ? new Blob(data, { type: "application/octet-stream" })
    : new Blob([data], { type: "application/octet-stream" });
  revokeLastDownloadUrl();   // chỉ giữ tối đa 1 blob URL sống
  const url = URL.createObjectURL(blob);
  lastDownloadUrl = url;
  // Hủy sau 10 phút — đủ cho daemon tải của Safari đọc xong cả file lớn
  // (95MB); không hủy sau 5 giây như trước (giết link giữa chừng khi daemon
  // đang ghi → file dở dang kẹt mãi trong Files > Tải về, Joseph 11:36).
  // Timer chỉ hủy đúng URL này (nếu đã có lần tải mới thì URL đã bị hủy ở
  // đầu lần tải đó rồi, timer cũ không được chạm vào URL mới).
  setTimeout(() => { if (lastDownloadUrl === url) revokeLastDownloadUrl(); }, 600000);
  state.lastDownload = { name, blob, via: "memory" };
  if (cleanupBtn) cleanupBtn.hidden = true;
  const fallbackNote = state.opfsFallbackReason
    ? " (Trình duyệt này không ghi được OPFS — " + state.opfsFallbackReason +
      " — nên file giữ trong RAM, hãy lưu ngay rồi đóng tab để giải phóng RAM.)"
    : "";
  if (canShareFile(blob, name)) {
    // ĐƯỜNG LƯU CHÍNH trên iOS: Chia sẻ native (bảng Chia sẻ → "Lưu vào
    // Tệp"), không qua download manager. Không tự bấm blob.
    if (hint) {
      hint.textContent = "File " + name + " (" + fmtBytes(blob.size) +
        ") đã sẵn sàng — bấm “Lưu / Chia sẻ” để lưu vào Files (cách chắc chắn nhất trên iPhone). " +
        "Nếu muốn tải bằng trình duyệt thì bấm “Tải về (blob)” bên dưới." + fallbackNote;
      hint.hidden = false;
      try { hint.scrollIntoView({ block: "nearest" }); } catch (e) {}
    }
    if (shareBtn) shareBtn.hidden = false;
    if (blobBtn) blobBtn.hidden = false;
  } else {
    // Không chia sẻ được (vd desktop không hỗ trợ share file): tải bằng blob như trước.
    fireBlobDownload(url, name);
    if (hint) {
      hint.textContent = "Đang tải về " + name + " (" + fmtBytes(blob.size) +
        ") — trình duyệt sẽ hỏi thì bấm Tải về, giữ tab mở; file lớn có thể cần vài phút." + fallbackNote;
      hint.hidden = false;
    }
    if (shareBtn) shareBtn.hidden = true;
    if (blobBtn) blobBtn.hidden = true;
  }
  return state.lastDownload;
}

// Tải 1 File đã đọc từ OPFS bằng blob URL (đường dự phòng khi Share không
// có sẵn): tạo URL từ File, bấm anchor, giữ URL 10 phút như đường RAM.
async function downloadOpfsBlob(file, name) {
  revokeLastDownloadUrl();
  const url = URL.createObjectURL(file);
  lastDownloadUrl = url;
  setTimeout(() => { if (lastDownloadUrl === url) revokeLastDownloadUrl(); }, 600000);
  fireBlobDownload(url, name);
}
// Nút "Lưu / Chia sẻ": dùng Web Share API để lưu thẳng vào Files (hoặc app
// khác) — đường lưu chính trên iPhone, không qua download manager.
// Đường OPFS: đọc File từ OPFS rồi share; chia sẻ xong thì dọn file tạm.
$("btn-share-download").addEventListener("click", async () => {
  if (state.lastDownloadPromise) { try { await state.lastDownloadPromise; } catch (e) { /* lỗi đã báo ở download() */ } }
  const d = state.lastDownload;
  if (!d) return;
  try {
    const blob = await lastDownloadFile();
    if (!blob) {
      showError("Chia sẻ lỗi: Không đọc được file tạm trong OPFS — hãy bấm “Tải về (blob)” để tải bằng trình duyệt.");
      return;
    }
    const file = new File([blob], d.name, { type: "application/octet-stream" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file] });
      // Xuất xong → dọn file tạm OPFS ngay (đừng để đầy bộ nhớ).
      if (d.via === "opfs") {
        await cleanupLastDownloadOpfs();
        const bar = $("download-bar");
        if (bar) bar.hidden = true;
        setStatus("Đã chia sẻ " + d.name + " và dọn file tạm (OPFS).");
      }
    } else {
      showError("Chia sẻ lỗi: Trình duyệt này không hỗ trợ chia sẻ file — hãy bấm “Tải về (blob)” để tải bằng trình duyệt.");
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;   // user hủy bảng chia sẻ
    // Hiện cả TÊN lỗi (vd NotAllowedError) + message, nhưng giữ tiền tố
    // "Chia sẻ lỗi: " (harness hotfix_safari2 kiểm tra startsWith tiền tố
    // này). Giả thuyết "mất user activation" chưa có bằng chứng thiết bị
    // thật (handler này không có await nào trước share) nên cần đúng dòng
    // lỗi này từ Safari thật.
    showError("Chia sẻ lỗi: " + (e && e.name ? e.name + ": " : "") +
      (e && e.message ? e.message : e));
  }
});
// Nút "Tải về (blob)": đường dự phòng — bấm anchor blob để trình duyệt tải
// về. URL có thể đã bị hủy sau 10 phút thì tạo lại từ blob đang giữ.
// Đường OPFS: đọc File từ OPFS rồi tạo URL (không giữ blob trong RAM).
$("btn-blob-download").addEventListener("click", async () => {
  if (state.lastDownloadPromise) { try { await state.lastDownloadPromise; } catch (e) { /* lỗi đã báo ở download() */ } }
  const d = state.lastDownload;
  if (!d) return;
  if (d.via === "opfs") {
    const file = await lastDownloadFile();
    if (!file) {
      showError("Tải về lỗi: Không đọc được file tạm trong OPFS — hãy thử xuất lại.");
      return;
    }
    await downloadOpfsBlob(file, d.name);
    return;
  }
  if (!lastDownloadUrl) {
    lastDownloadUrl = URL.createObjectURL(d.blob);
    const u = lastDownloadUrl;
    setTimeout(() => { if (lastDownloadUrl === u) revokeLastDownloadUrl(); }, 600000);
  }
  fireBlobDownload(lastDownloadUrl, d.name);
});
// Nút "Dọn file tạm": xóa mọi file tạm OPFS (lần xuất hiện tại + file cũ
// còn sót), quên state, ẩn thanh. File đã tải về/chia sẻ xong thì không
// cần giữ trong OPFS nữa.
$("btn-opfs-cleanup").addEventListener("click", async () => {
  let n = 0;
  if (typeof PB3OPFS !== "undefined") {
    try { n = await PB3OPFS.cleanupOutputs(); } catch (e) { n = 0; }
  }
  state.lastDownload = null;
  state.lastDownloadPromise = null;
  const bar = $("download-bar");
  if (bar) bar.hidden = true;
  setStatus(n > 0 ? ("Đã dọn " + n + " file tạm trong OPFS.") : "Không còn file tạm nào trong OPFS.");
});
// Nút ✕ trên thanh Lưu file: ẩn thanh (chỉ ẩn tới lần xuất file sau —
// download() hiện lại thanh mỗi khi có file mới). File OPFS tạm thì dọn
// luôn (đừng để đầy bộ nhớ); file RAM thì vẫn còn trong state.lastDownload
// nên nút Lưu/Chia sẻ hiện lại cùng thanh.
$("btn-download-close").addEventListener("click", () => {
  const d = state.lastDownload;
  if (d && d.via === "opfs") cleanupLastDownloadOpfs();
  const bar = $("download-bar");
  if (bar) bar.hidden = true;
});

function inputFileArgs() {
  if (!state.file) throw new Error("Chưa chọn file. Hãy chọn file ở trên trước.");
  return [{ name: state.file.name, data: state.file.data }];
}
function inputPath() { return "/tmp/input/" + state.file.name; }

// Gợi ý ZIP-chứa-IPA: file Joseph chọn là ZIP do "Nén" trong Files tạo ra
// (1 file .ipa + các entry __MACOSX/._...), trong khi đúng ra chỉ cần đổi
// tên bản copy của IPA thành .zip. Chỉ hiện khi đúng điều kiện này:
// đúng 1 entry .ipa (không phải thư mục) + ít nhất 1 entry khác, và mọi
// entry còn lại đều nằm dưới __MACOSX/. Không hiện cho IPA thật hay ZIP
// thường.
const IPA_ZIP_HINT = "File này là ZIP chứa IPA — đổi tên .ipa thành .zip thay vì nén lại (nhấn giữ file → Đổi tên)";
function isZipWithSingleIpa(entries) {
  if (!entries || !entries.length) return false;
  const isMac = (p) => p === "__MACOSX/" || p.startsWith("__MACOSX/");
  // Entry __MACOSX/._*.ipa là file AppleDouble đi kèm, KHÔNG tính là IPA.
  const ipas = entries.filter((e) => {
    const p = String((e && e.path) || "");
    return p && !isMac(p) && !p.endsWith("/") && /\.ipa$/i.test(p);
  });
  if (ipas.length !== 1) return false;
  const others = entries.filter((e) => e !== ipas[0]);
  if (!others.length) return false;   // chỉ 1 entry .ipa = IPA thật, không gợi ý
  return others.every((e) => isMac(String((e && e.path) || "")));
}
function showIpaZipHint(el, entries) {
  if (!el) return;
  if (isZipWithSingleIpa(entries)) { el.textContent = IPA_ZIP_HINT; el.hidden = false; }
  else el.hidden = true;
}
// Kiểm tra nhanh bằng list_entries (không gửi lại file — worker đã có file
// từ thao tác trước) để hiện gợi ý cả khi inspect lỗi vì file là ZIP.
async function maybeShowIpaZipHint(el) {
  try {
    const msg = await callWorker("run", { fn: "list_entries", args: [inputPath()], files: [] });
    showIpaZipHint(el, JSON.parse(msg.result).entries || []);
  } catch (e) { /* gợi ý chỉ là phụ: lỗi thì thôi, không báo thêm */ }
}

// ---- File gần đây (redesign) ----
// CHỈ metadata trong phiên + localStorage (tên, dung lượng, loại, thời
// gian) — KHÔNG lưu nội dung file (trình duyệt không giữ được file thật
// sau khi tải lại trang).
//
// Nút “Chọn lại file” (mục C) ở MỖI dòng, 2 trường hợp thật:
//  - Còn trong phiên (dữ liệu còn trong state.file / state.batchFiles):
//    mở lại NGAY, không mở hộp chọn file — file game (.ipa/.zip/.unity3d/
//    .assets/.tar.xz/.gz) thành file hiện tại rồi mở tab Danh sách (không
//    boot worker, xem nhanh được ngay); .ttf mở tab Font; .json mở trong
//    tab Mod (đọc từ dữ liệu trong phiên, giữ thứ tự key gốc).
//  - Hết phiên (chỉ còn metadata localStorage): mở hộp chọn file
//    (#reselect-file-input, không accept — quirk picker iOS làm mờ .ipa
//    như input chính) để user chọn lại file đó từ máy; so tên + dung
//    lượng: khớp thì mở tiếp, KHÁC thì báo rõ tên/dung lượng file vừa
//    chọn so với file gần đây rồi VẪN mở file vừa chọn (không im lặng
//    dùng sai file, không giả vờ file cũ còn trong bộ nhớ).
// Nút “Tải lại” giữ nguyên (chỉ hiện khi còn trong phiên): tải 1 bản
// sao của file về máy. Nút “Xóa” giữ nguyên. Không bịa file mẫu.
// Trạng thái lần chọn lại gần nhất để harness/chẩn đoán đọc.
const reselectState = { pending: null, last: null };
if (typeof window !== "undefined") {
  window.__PB3_RESELECT_STATE = () => ({
    pending: reselectState.pending
      ? { name: reselectState.pending.name, size: reselectState.pending.size } : null,
    last: reselectState.last,
  });
}
function reselectRecord(r) {
  reselectState.last = Object.assign({ at: new Date().toISOString() }, r || {});
}
// Tab làm việc của 1 file game/json khi chọn lại (không boot worker:
// tab Danh sách chỉ đọc EOCD nhanh; tab Mod đọc JSON trong RAM).
function reselectTargetTab(name) {
  const n = (name || "").toLowerCase();
  if (n.endsWith(".json")) return "mod";
  if (n.endsWith(".ttf")) return "font";
  return "list";
}
const RECENT_KEY = "pb3_recent_files";
const RECENT_MAX = 8;
let recentFiles = [];   // [{name, size, kind, time, key}]
function historyEnabled() {
  try { return localStorage.getItem("pb3_save_history") !== "0"; } catch (e) { return true; }
}
function recentKind(name) {
  const n = (name || "").toLowerCase();
  if (n.endsWith(".tar.xz")) return "TAR.XZ";
  if (n.endsWith(".ipa")) return "IPA";
  if (n.endsWith(".unity3d")) return "UNITY3D";
  if (n.endsWith(".assets")) return "ASSETS";
  if (n.endsWith(".zip")) return "ZIP";
  if (n.endsWith(".ttf")) return "TTF";
  if (n.endsWith(".json")) return "JSON";
  return "FILE";
}
function recentKeyOf(f) { return f.name + "|" + f.size + "|" + (f.lastModified || 0); }
function saveRecent() {
  if (!historyEnabled()) return;
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recentFiles.map((e) => (
      { name: e.name, size: e.size, kind: e.kind, time: e.time, key: e.key }))));
  } catch (e) { /* localStorage đầy/không có */ }
}
function loadRecent() {
  if (!historyEnabled()) { recentFiles = []; renderRecent(); return; }
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) recentFiles = arr.filter((e) => e && e.name).slice(0, RECENT_MAX);
    }
  } catch (e) { recentFiles = []; }
  renderRecent();
}
function addRecent(f) {
  const key = recentKeyOf(f);
  const entry = { name: f.name, size: f.size, kind: recentKind(f.name), time: Date.now(), key };
  recentFiles = [entry, ...recentFiles.filter((e) => e.key !== key)].slice(0, RECENT_MAX);
  saveRecent();
  renderRecent();
}
function recentInSession(key) {
  if (state.file && recentKeyOf(state.file) === key) return state.file;
  const b = state.batchFiles.find((x) => recentKeyOf(x) === key);
  return b || null;
}
// Như recentInSession nhưng cho biết file lấy từ đâu: "main" (state.file)
// hay "batch" (state.batchFiles) — để chọn lại đi đúng luồng làm việc.
function recentInSessionEntry(key) {
  if (state.file && recentKeyOf(state.file) === key) return { file: state.file, from: "main" };
  const b = state.batchFiles.find((x) => recentKeyOf(x) === key);
  return b ? { file: b, from: "batch" } : null;
}
// Mở 1 file .json trong tab Mod (mục B): f là File (có .text()) hoặc
// object trong phiên {name, data: ArrayBuffer}. Giữ thứ tự key gốc,
// provenance origin "upload" (không rõ trích từ đâu nếu không có sidecar).
async function openJsonInMod(f) {
  const name = f.name;
  const text = typeof f.text === "function"
    ? await f.text()
    : new TextDecoder("utf-8").decode(f.data);
  const { entries, keys } = modParseJsonObject(text);
  modSetJson(name, entries, keys, {
    origin: "upload", gameFile: state.file ? state.file.name : null,
    extractName: name, source: null, locale: null,
    from: null, to: null, keys: keys.length, at: new Date().toISOString(),
  });
  goTool("mod");
  return { name, keys: keys.length };
}
// Chọn lại 1 dòng còn trong phiên: dữ liệu đã có trong RAM nên mở lại
// ngay, không mở hộp chọn file. File game trong batch thì đưa lên làm
// file hiện tại (batch vẫn giữ nguyên); file đã là state.file thì chỉ
// điều hướng, không reset danh sách trích xuất đang có.
async function reselectInSession(entry, found) {
  const f = found.file;
  try {
    if (recentKind(f.name) === "JSON") {
      const opened = await openJsonInMod(f);
      reselectRecord({ name: f.name, inSession: true, from: found.from,
        matched: true, tab: "mod", keys: opened.keys });
      return;
    }
    if (!(found.from === "main" && f === state.file)) setCurrentGameFile(f);
    else { syncFileChips(); }
    const tab = reselectTargetTab(f.name);
    goTool(tab);
    reselectRecord({ name: f.name, inSession: true, from: found.from,
      matched: true, tab });
  } catch (e) {
    showError("Chọn lại file " + f.name + " lỗi: " + (e && e.message ? e.message : e));
    reselectRecord({ name: f.name, inSession: true, from: found.from,
      matched: true, tab: null, error: String(e && e.message ? e.message : e) });
  }
}
// Chọn lại 1 dòng đã hết phiên: user vừa chọn file từ hộp chọn.
// expected = entry gần đây (tên + dung lượng kỳ vọng); khác file thì báo
// rõ rồi vẫn mở file vừa chọn. Trả {name, matched, tab} hoặc null nếu
// file không mở được (đuôi lạ / user huỷ file lớn).
async function openPickedReselect(picked, expected) {
  if (!picked) return null;
  if (!gameExtOk(picked.name)) {
    showError(gameExtError(picked.name));
    reselectRecord({ name: picked.name, inSession: false, matched: false,
      tab: null, error: "đuôi file không hỗ trợ" });
    return null;
  }
  const matched = !!(expected && picked.name === expected.name && picked.size === expected.size);
  try {
    let tab;
    if (recentKind(picked.name) === "JSON") {
      const opened = await openJsonInMod(picked);
      tab = "mod";
      reselectRecord({ name: picked.name, inSession: false, matched, tab, keys: opened.keys });
    } else {
      const opened = await handleChosenFile(picked);
      if (!opened) {
        reselectRecord({ name: picked.name, inSession: false, matched,
          tab: null, error: "đã huỷ mở file" });
        return null;
      }
      tab = reselectTargetTab(picked.name);
      goTool(tab);
      reselectRecord({ name: picked.name, inSession: false, matched, tab });
    }
  } catch (e) {
    showError("Chọn lại file " + picked.name + " lỗi: " + (e && e.message ? e.message : e));
    reselectRecord({ name: picked.name, inSession: false, matched,
      tab: null, error: String(e && e.message ? e.message : e) });
    return null;
  }
  // Báo khác file SAU khi mở xong (handleChosenFile/modSetJson có
  // clearError() nên báo trước sẽ bị xóa mất): user thấy rõ file nào
  // đang mở, không tưởng là file gần đây cũ.
  if (expected && !matched) {
    showError("Bạn chọn file " + picked.name + " (" + fmtBytes(picked.size) + "), khác với file gần đây " +
      expected.name + " (" + fmtBytes(expected.size) + ") — đã mở file vừa chọn thay cho file cũ.");
  }
  return { name: picked.name, matched, tab: reselectTargetTab(picked.name) };
}
function renderRecent() {
  const el = $("recent-list");
  if (!el) return;
  if (!recentFiles.length) {
    el.innerHTML = "<p class=\"note\">Chưa có file nào trong phiên này — chọn 1 file game ở khung trên.</p>";
    return;
  }
  let html = "";
  for (const e of recentFiles) {
    const inSession = recentInSession(e.key);
    const when = new Date(e.time).toLocaleString("vi-VN");
    html += "<div class=\"recent-row\"><span class=\"badge\">" + esc(e.kind) + "</span><span class=\"recent-name\">" + esc(e.name) +
      "</span><span class=\"recent-meta\">" + fmtBytes(e.size) + " · " + esc(when) + "</span>" +
      (inSession
        ? "<button class=\"secondary\" data-act=\"dl\" data-key=\"" + esc(e.key) + "\">Tải lại</button>"
        : "<span class=\"note\">hết phiên — chọn lại file từ máy</span>") +
      "<button class=\"secondary\" data-act=\"reselect\" data-key=\"" + esc(e.key) + "\">Chọn lại file</button>" +
      "<button class=\"secondary\" data-act=\"rm\" data-key=\"" + esc(e.key) + "\">Xóa</button></div>";
  }
  el.innerHTML = html;
}
$("recent-list").addEventListener("click", (ev) => {
  const t = ev && ev.target;
  if (!t || typeof t.closest !== "function") return;
  const btn = t.closest("button");
  if (!btn || typeof btn.getAttribute !== "function") return;
  const key = btn.getAttribute("data-key");
  const act = btn.getAttribute("data-act");
  if (!key) return;
  if (act === "rm") {
    recentFiles = recentFiles.filter((e) => e.key !== key);
    saveRecent();
    renderRecent();
  } else if (act === "dl") {
    // Chỉ tải lại được khi dữ liệu file còn trong bộ nhớ phiên này.
    const f = recentInSession(key);
    if (f) download(f.name, f.data.slice(0));
  } else if (act === "reselect") {
    // Mục (C): còn trong phiên thì mở lại ngay; hết phiên thì mở hộp
    // chọn file (input riêng #reselect-file-input — bấm nút là cử chỉ
    // của user nên trình duyệt cho mở picker; KHÔNG đọc lại đường dẫn
    // cũ vì trình duyệt không cho).
    const entry = recentFiles.find((e) => e.key === key) || null;
    const found = recentInSessionEntry(key);
    if (found) reselectInSession(entry, found);
    else {
      reselectState.pending = entry;
      const inp = $("reselect-file-input");
      if (inp && typeof inp.click === "function") inp.click();
      else showError("Không mở được hộp chọn file — hãy chọn file ở khung trên.");
    }
  }
});
// Hộp chọn file của nút “Chọn lại file” (hết phiên): không có accept
// (quirk picker iOS làm mờ .ipa như input chính) — kiểm tra đuôi bằng JS.
$("reselect-file-input").addEventListener("change", (ev) => {
  const picked = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  const expected = reselectState.pending;
  reselectState.pending = null;
  if (!picked) return;   // user đóng hộp chọn mà không chọn file
  openPickedReselect(picked, expected);
});
// “Lưu lịch sử gần đây” (Cài đặt): tắt thì không lưu metadata mới nữa,
// xóa metadata đã lưu, danh sách chỉ còn trong phiên hiện tại.
$("setting-save-history").addEventListener("change", (ev) => {
  try { localStorage.setItem("pb3_save_history", ev.target.checked ? "1" : "0"); } catch (e) { /* localStorage không có */ }
  if (!ev.target.checked) {
    try { localStorage.removeItem(RECENT_KEY); } catch (e) { /* localStorage không có */ }
    recentFiles = recentFiles.filter((e) => recentInSession(e.key));
  } else saveRecent();
  renderRecent();
});
$("setting-save-history").checked = historyEnabled();
loadRecent();

// ---- Hộp thoại cảnh báo file lớn (Mục C4) ----
// File >140MB thì hỏi TRƯỚC KHI đọc vào RAM (khác với cảnh báo chữ của
// mục 5 chỉ hiện rồi vẫn đọc tiếp): user bấm "Tiếp tục" mới mở file,
// bấm "Hủy" thì dừng hẳn, file cũ (nếu có) vẫn giữ nguyên. >190MB thì
// cảnh báo mạnh hơn + gợi ý dùng bản iSH (tab Safari chỉ ~1–1,5GB RAM;
// đo thật 2026-11-17: tab ~4× file + ~250MB nền).
// Trả Promise<boolean>; file <=140MB thì không hiện hộp thoại, trả true ngay.
// state: để harness/chẩn đoán đọc (visible, files, decision).
const largeFileModalState = { visible: false, files: [], decision: null };
if (typeof window !== "undefined") {
  window.__PB3_LARGE_FILE_STATE = () => ({
    visible: largeFileModalState.visible,
    files: largeFileModalState.files.map((f) => ({ name: f.name, size: f.size })),
    decision: largeFileModalState.decision,
  });
}
function confirmLargeFile(files) {
  const large = (files || []).filter((f) => f && f.size > LARGE_FILE_CONFIRM_BYTES);
  if (!large.length) return Promise.resolve(true);
  const modal = $("large-file-modal");
  const textEl = $("large-file-text");
  const btnOk = $("large-file-continue");
  const btnCancel = $("large-file-cancel");
  if (!modal || !textEl || !btnOk || !btnCancel) {
    // Không có hộp thoại trong DOM (vd trang cũ): rơi về confirm() native
    // — vẫn chặn trước khi đọc file, không đọc lén.
    const lines = large.map((f) => "• " + f.name + " (" + fmtBytes(f.size) + ")").join("\n");
    return Promise.resolve(confirm("File lớn:\n" + lines + "\n\nBấm OK để tiếp tục, hoặc Huỷ để dùng bản iSH cho file này."));
  }
  const strongest = large.some((f) => f.size > STRONG_BYTES);
  const lines = large.map((f) => "• " + f.name + " (" + fmtBytes(f.size) + ")").join("\n");
  const ramNote = strongest
    ? "File rất lớn (>190MB). Tab trình duyệt trên iPhone chỉ có khoảng 1–1,5GB RAM — file này cộng với dữ liệu đang giải nén có thể làm tab bị tắt giữa chừng. Nên dùng bản iSH cho file này."
    : "File lớn (>140MB). Trích xuất/nén đọc từng phần (streaming) nhưng RAM đỉnh ước tính ~4× dung lượng file + ~250MB nền (đo thật 2026-11-17); trên iPhone vẫn có thể làm chết tab.";
  textEl.textContent = lines + "\n\n" + ramNote + "\n\nBấm “Tiếp tục” để mở file trên web (nên đóng các tab khác trước), hoặc bấm “Hủy” để dùng bản iSH cho file này.";
  largeFileModalState.visible = true;
  largeFileModalState.files = large.map((f) => ({ name: f.name, size: f.size }));
  largeFileModalState.decision = null;
  modal.hidden = false;
  return new Promise((resolve) => {
    const done = (decision) => {
      modal.hidden = true;
      largeFileModalState.visible = false;
      largeFileModalState.decision = decision;
      btnOk.removeEventListener("click", onOk);
      btnCancel.removeEventListener("click", onCancel);
      resolve(decision);
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    btnOk.addEventListener("click", onOk);
    btnCancel.addEventListener("click", onCancel);
  });
}

// ---- Chọn file ----
// Đặt 1 file đã có dữ liệu trong RAM (ArrayBuffer) làm file hiện tại —
// dùng chung cho chọn file mới (handleChosenFile, sau khi đọc xong) và
// chọn lại file game còn trong phiên (mục C, không đọc lại từ đĩa).
// Trả state.file vừa đặt.
function setCurrentGameFile(obj) {
  state.file = { name: obj.name, size: obj.size, lastModified: obj.lastModified || 0, data: obj.data };
  state.fileSentFor = null;   // HOTFIX clone: file mới thì gửi lại từ đầu
  state.extractListFor = null;  // file mới -> danh sách chọn file cũ hết hạn
  state.extractSelected = null;
  state.extractEntries = [];
  state.lastMod = null;       // file mới -> kết quả Mod cũ hết hạn (mục C7)
  { const ppb = $("btn-mod-patch-pack"); if (ppb) ppb.disabled = true; }
  if (typeof rebuildResetState === "function") rebuildResetState();

  $("file-info").textContent = obj.name + " · " + fmtBytes(obj.size);
  $("file-info").hidden = false;
  $("btn-clear-file").hidden = false;
  clearError();
  addRecent(state.file);
  syncFileChips();
  return state.file;
}
async function handleChosenFile(f) {
  if (!f) return undefined;
  if (f.size > MAX_BYTES) {
    showError("File " + fmtBytes(f.size) + " quá lớn cho bản web (>2GB). Dùng bản iSH cho file này.");
    return undefined;
  }
  // Mục C4: file >140MB thì hỏi TRƯỚC KHI đọc vào RAM — "Hủy" thì dừng
  // hẳn (file cũ vẫn giữ nguyên, không boot, không đọc); "Tiếp tục" mới đọc.
  if (!(await confirmLargeFile([f]))) {
    showError("Đã huỷ mở file lớn — dùng bản iSH cho file này nếu cần.");
    return undefined;
  }
  // Mục 5: >140MB cảnh báo rõ, >190MB khuyên mạnh dùng iSH — vẫn cho tiếp tục.
  const warn = sizeWarningFor(f.size);
  if (warn.text) showError(warn.text);
  // Mục 6 (lazy boot): chọn file lần đầu thì bắt đầu boot Pyodide ngay
  // (chạy nền trong lúc đọc file); đã boot rồi thì không làm gì thêm.
  ensureBoot().catch(() => { /* lỗi boot đã hiện ở banner + trạng thái */ });
  const buf = await f.arrayBuffer();
  const opened = setCurrentGameFile({ name: f.name, size: f.size, lastModified: f.lastModified || 0, data: buf });
  // Cảnh báo kích thước phải còn hiện sau khi file đã nạp xong (trước
  // đây clearError() ở đây xóa luôn cảnh báo vừa hiện).
  if (warn.text) showError(warn.text);
  return opened;
}
$("file-input").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  if (!gameExtOk(f.name)) {
    showError(gameExtError(f.name));
    ev.target.value = "";
    return;
  }
  handleChosenFile(f);
});
// Khung thả file (Trang chủ): bấm để chọn + kéo-thả; input vẫn không có
// accept (quirk picker iOS làm mờ .ipa) — kiểm tra đuôi bằng JS như trước.
$("dropzone").addEventListener("click", () => {
  const inp = $("file-input");
  if (inp && typeof inp.click === "function") inp.click();
});
$("dropzone").addEventListener("dragover", (ev) => {
  ev.preventDefault();
  $("dropzone").classList.add("drag");
});
$("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("drag"));
$("dropzone").addEventListener("drop", (ev) => {
  ev.preventDefault();
  $("dropzone").classList.remove("drag");
  const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
  if (f && !gameExtOk(f.name)) { showError(gameExtError(f.name)); return; }
  handleChosenFile(f);
});
function clearFile() {
  state.file = null;
  state.fileSentFor = null;
  state.extractListFor = null;
  state.extractSelected = null;
  state.extractEntries = [];
  state.lastMod = null;        // bỏ file -> kết quả Mod cũ hết hạn (mục C7)
  { const ppb = $("btn-mod-patch-pack"); if (ppb) ppb.disabled = true; }
  if (typeof rebuildResetState === "function") rebuildResetState();
  const inp = $("file-input");
  if (inp) inp.value = "";
  $("file-info").hidden = true;
  $("btn-clear-file").hidden = true;
  syncFileChips();
  renderRecent();
}
$("btn-clear-file").addEventListener("click", clearFile);
$("btn-unity-clear").addEventListener("click", clearFile);
// Chip file đã chọn ở tab Unity (tên + dung lượng + nút X xóa lựa chọn).
function syncFileChips() {
  const chip = $("unity-file-chip");
  if (chip) {
    chip.textContent = state.file
      ? "Đã chọn: " + state.file.name + " · " + fmtBytes(state.file.size)
      : "Chưa chọn file — chọn file ở Trang chủ (khung thả file) rồi quay lại đây.";
  }
  const x = $("btn-unity-clear");
  if (x) x.hidden = !state.file;
}
syncFileChips();

// ---- Tabs ----
const tabs = ["list", "unity", "extract", "translate", "mod", "batch", "kb", "support", "compress", "font", "diagnose"];
function showTab(name) {
  if (name !== "kb") state.currentToolTab = name;
  for (const t of tabs) {
    $("tab-" + t).hidden = (t !== name);
    $("btn-" + t).classList.toggle("active", t === name);
  }
  // Mở tab Trích xuất thì tải danh sách file để chọn tay (nếu đã chọn
  // file và danh sách chưa có); mở tab Dịch thì đồng bộ banner rỗng.
  // Mở tab Chẩn đoán thì làm mới trạng thái ứng dụng (mục C2).
  if (name === "extract") maybeRefreshExtractFileList();
  if (name === "translate") { syncTranslateEmptyBanner(); refreshTranslateResumeBar(); }
  if (name === "diagnose") refreshAppDiag();
}
for (const t of tabs) {
  $("btn-" + t).addEventListener("click", () => {
    // Tab KB sống ở view "Kho KB" riêng (bottom nav), không nằm trong view
    // "Chức năng" cùng các tab còn lại.
    if (t === "kb") { showView("kb"); return; }
    showView("tools");
    showTab(t);
  });
}

// ---- Bottom nav + các view (redesign) ----
const views = ["home", "tools", "kb", "settings", "about"];
function showView(name) {
  for (const v of views) {
    const el = $("view-" + v);
    if (el) el.hidden = (v !== name);
    const b = $("btn-nav-" + v);
    if (b) b.classList.toggle("active", v === name);
  }
  const dd = $("menu-dropdown");
  if (dd) dd.hidden = true;
  if (name === "tools") showTab(state.currentToolTab || "list");
  if (name === "kb") showTab("kb");
}
$("btn-nav-home").addEventListener("click", () => showView("home"));
$("btn-nav-tools").addEventListener("click", () => showView("tools"));
$("btn-nav-kb").addEventListener("click", () => showView("kb"));
$("btn-nav-settings").addEventListener("click", () => showView("settings"));
$("btn-nav-about").addEventListener("click", () => showView("about"));
// 6 thẻ chức năng ở Trang chủ -> tab công cụ tương ứng.
function goTool(tab) { showView("tools"); showTab(tab); }
$("btn-card-unity").addEventListener("click", () => goTool("unity"));
$("btn-card-extract").addEventListener("click", () => goTool("extract"));
$("btn-card-translate").addEventListener("click", () => goTool("translate"));
$("btn-card-mod").addEventListener("click", () => goTool("mod"));
$("btn-card-batch").addEventListener("click", () => goTool("batch"));
$("btn-card-kb").addEventListener("click", () => showView("kb"));
// Nút "Mở cài đặt dịch" ở Cài đặt: tùy chọn dịch (proxy, Google Translate)
// vẫn nằm ở tab Dịch, ngay chỗ dùng — đây chỉ là điều hướng thật tới đó.
$("btn-settings-open-translate").addEventListener("click", () => goTool("translate"));
// Menu hamburger: điều hướng thật tới các view + mục Giới hạn bản web.
$("btn-menu").addEventListener("click", () => {
  const dd = $("menu-dropdown");
  if (dd) dd.hidden = !dd.hidden;
});
$("menu-go-home").addEventListener("click", () => showView("home"));
$("menu-go-tools").addEventListener("click", () => showView("tools"));
$("menu-go-kb").addEventListener("click", () => showView("kb"));
$("menu-go-settings").addEventListener("click", () => showView("settings"));
$("menu-go-about").addEventListener("click", () => showView("about"));
$("menu-go-limits").addEventListener("click", () => {
  showView("home");
  const el = $("limits-card");
  if (el && typeof el.scrollIntoView === "function") {
    try { el.scrollIntoView(); } catch (e) { /* cuộn chỉ là phụ */ }
  }
});

// ---- Liệt kê nhanh bằng EOCD (Mục 5, stream.js) ----
// Chỉ đọc EOCD + central directory ở cuối file, không đưa cả file qua
// Python. Chỉ dùng khi ZIP phẳng (không container lồng, không ZIP64,
// không phải ZIP thì trả null để caller fallback về bridge như trước).
// Entry trả về khớp định dạng bridge list_entries ({path, size, kind}).
function fastListEntries() {
  if (typeof PB3Stream === "undefined" || !PB3Stream.listZipEntries || !state.file) return null;
  try {
    const r = PB3Stream.listZipEntries(state.file.data);
    if (!r.ok || r.nested || r.zip64) return null;
    return { ok: true, kind: "zip", total: r.entries.length,
      entries: r.entries.map((e) => ({ path: e.name, size: e.size, kind: e.kind })),
      fast: true, rawEntries: r.entries };
  } catch (e) { return null; }
}

// Tải 1 entry trong ZIP/IPA bằng đường nhanh (Mục 5): stored thì
// copy-through (subarray chia sẻ buffer gốc, không copy qua heap),
// deflate thì DecompressionStream native. Không hỗ trợ (method khác,
// entry mã hoá, container lồng) thì báo rõ và gợi ý tab Trích xuất.
async function downloadZipEntry(path) {
  if (!state.file) return;
  if (typeof PB3Stream === "undefined" || !PB3Stream.listZipEntries) {
    showError("Trình duyệt này không hỗ trợ tải entry nhanh — hãy dùng tab Trích xuất.");
    return;
  }
  const r = PB3Stream.listZipEntries(state.file.data);
  if (!r.ok || r.nested) {
    showError("File này có container lồng hoặc không phải ZIP phẳng — hãy dùng tab Trích xuất để lấy file ra.");
    return;
  }
  const entry = r.entries.find((e) => e.name === path);
  if (!entry) { showError("Không thấy entry " + path + " trong file."); return; }
  try {
    const out = await PB3Stream.extractZipEntryFromBuffer(state.file.data, entry);
    const name = path.split("/").pop() || "entry";
    download(name, out.blob);
  } catch (e) {
    showError("Tải entry lỗi: " + (e && e.message ? e.message : e) + " — hãy dùng tab Trích xuất.");
  }
}

// Tải bản đã giải nén của 1 file .gz đơn lẻ bằng DecompressionStream
// native (Mục 5). Không dùng cho .tar.xz (LZMA, native không thay được).
async function downloadGzipMember() {
  if (!state.file) return;
  if (typeof PB3Stream === "undefined" || !PB3Stream.extractGzipMember) {
    showError("Trình duyệt này không hỗ trợ giải nén gzip nhanh.");
    return;
  }
  try {
    const blob = await PB3Stream.extractGzipMember(state.file.data);
    const name = state.file.name.replace(/\.gz$/i, "") || "file";
    download(name, blob);
  } catch (e) {
    showError("Giải nén gzip lỗi: " + (e && e.message ? e.message : e));
  }
}
// Nút "Tải" từng entry + nút tải bản gzip trong kết quả tab Danh sách.
$("list-result").addEventListener("click", (ev) => {
  const t = ev && ev.target;
  if (!t || typeof t.getAttribute !== "function") return;
  const entry = t.getAttribute("data-entry");
  if (entry) { downloadZipEntry(entry); return; }
  if (t.getAttribute("data-act") === "gzip-dl") downloadGzipMember();
});

// ---- Danh sách entry ----
$("btn-list-run").addEventListener("click", async () => {
  try {
    const fast = fastListEntries();
    const parsed = fast || (await runOp("Liệt kê entry", "list_entries", [inputPath()], inputFileArgs())).parsed;
    let html = "<p>Loại: <b>" + esc(parsed.kind) + "</b> · " + parsed.total + " entry" +
      (parsed.fast ? " · liệt kê nhanh bằng EOCD (không cần giải nén toàn bộ file)" : "") +
      "</p><table><tr><th>Đường dẫn</th><th>Loại</th><th>Kích thước</th>" +
      (parsed.fast ? "<th></th>" : "") + "</tr>";
    for (const e of parsed.entries.slice(0, 200)) {
      html += "<tr><td>" + esc(e.path) + "</td><td>" + esc(e.kind) + "</td><td>" + fmtBytes(e.size) + "</td>" +
        (parsed.fast && !e.path.endsWith("/")
          ? "<td><button class=\"secondary\" data-entry=\"" + esc(e.path) + "\">Tải</button></td>" : "") +
        "</tr>";
    }
    html += "</table>";
    if (parsed.entries.length > 200) html += "<p>…chỉ hiện 200 entry đầu.</p>";
    if (/\.gz$/i.test(state.file.name) && typeof PB3Stream !== "undefined" && PB3Stream.extractGzipMember) {
      html += "<p><button class=\"secondary\" data-act=\"gzip-dl\">Tải bản đã giải nén (gzip, DecompressionStream)</button></p>";
    }
    $("list-result").innerHTML = html;
    showIpaZipHint($("list-ipa-hint"), parsed.entries);
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Unity inspect ----
// File ZIP/IPA: bridge trả danh sách bundle/.assets bên trong để user chọn
// 1 cái inspect trực tiếp (không parse cả file ZIP — trước đây lỗi
// E_UNSUPPORTED_FORMAT). Không có bundle nào thì hiện hướng dẫn trích xuất.
function renderUnityResult(parsed) {
  let html = "";
  if (parsed.entry) html += "<p>Entry trong IPA/ZIP: <b>" + esc(parsed.entry) + "</b></p>";
  if (parsed.bundle) {
    html += "<p>Bundle Unity: <b>" + esc(parsed.unity_version || "?") + "</b> · nén " + esc(parsed.compression || "?") + " · " + (parsed.entries || []).length + " entry</p>";
  } else {
    html += "<p>File .assets độc lập (không phải bundle).</p>";
  }
  for (const f of parsed.files || []) {
    if (f.error) { html += "<p>Entry " + esc(f.entry) + ": lỗi " + esc(f.error) + "</p>"; continue; }
    html += "<h3>" + esc(f.entry) + " — Unity " + esc(f.unity_version || "?") + " · " + f.objects + " object · " + f.text_assets.length + " TextAsset</h3><ul>";
    for (const t of f.text_assets) {
      html += "<li>#" + t.path_id + " <b>" + esc(t.name) + "</b>: " + esc(t.head) + "</li>";
    }
    html += "</ul>";
  }
  return html || "<p>Không thấy TextAsset nào.</p>";
}
async function inspectUnityEntry() {
  const sel = $("unity-bundle-select");
  const entry = sel && sel.value;
  if (!entry) return;
  try {
    const { parsed } = await runOp("Inspect Unity", "unity_inspect_entry", [inputPath(), entry], inputFileArgs());
    $("unity-result").innerHTML = renderUnityResult(parsed);
    updateHomeUnityInfo(parsed);
  } catch (e) { /* runOp đã báo lỗi có nhãn */ }
}
// ---- Thẻ "Thông tin Unity" ở Trang chủ: CHỈ hiện sau khi inspect thật ----
// Unity Version / số object / TextAsset lấy từ kết quả inspect; bridge
// không trả Build Target nên thẻ không hiện mục đó (không bịa).
function updateHomeUnityInfo(parsed) {
  const el = $("unity-info");
  if (!el) return;
  const ver = parsed.unity_version ||
    (parsed.files && parsed.files[0] && parsed.files[0].unity_version) || "?";
  const objs = (parsed.files || []).reduce((n, f) => n + (f.objects || 0), 0);
  const tas = (parsed.files || []).reduce((n, f) => n + (f.text_assets || []).length, 0);
  const name = state.file ? esc(state.file.name) : "?";
  el.innerHTML = "<p>File: <b>" + name + "</b></p>" +
    "<p>Unity Version: <b>" + esc(ver) + "</b>" + (parsed.bundle ? " · bundle " + esc(parsed.compression || "?") : " · file .assets độc lập") + "</p>" +
    "<p>" + objs + " object · " + tas + " TextAsset</p>" +
    "<p><span class=\"badge\">Đã sẵn sàng</span> <span class=\"badge\">An toàn &amp; Riêng tư</span></p>" +
    "<p class=\"note\">Xử lý 100% trên thiết bị của bạn — file không được upload đi đâu.</p>";
}
$("btn-unity-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Inspect Unity", "unity_inspect", [inputPath()], inputFileArgs());
    if (parsed.is_container) {
      // Giữ hint unity-ipa-hint: file ZIP-chứa-IPA (do "Nén" thay vì "Đổi tên")
      // thì gợi ý đổi tên — trước đây hint chỉ hiện ở nhánh catch, nay inspect
      // ZIP không còn ném lỗi nữa nên phải gọi tường minh ở nhánh container.
      // maybeShowIpaZipHint tự ẩn hint khi file không khớp mẫu (IPA thật).
      maybeShowIpaZipHint($("unity-ipa-hint"));
      const bundles = parsed.bundles || [];
      if (!bundles.length) {
        $("unity-result").innerHTML =
          "<p>File này là ZIP/IPA và <b>không chứa bundle .unity3d hay file .assets nào</b> để inspect trực tiếp.</p>" +
          "<p class=note>Inspect Unity chỉ đọc trực tiếp bundle .unity3d hoặc file .assets độc lập. " +
          "Hãy dùng tab Trích xuất để lấy text ra, hoặc mở IPA ra lấy 1 file .assets rồi chọn file đó để inspect.</p>";
      } else {
        let html = "<p>File này là ZIP/IPA (" + parsed.total_entries + " entry) — chọn 1 bundle/.assets bên trong để inspect" +
          (parsed.bundles_total > bundles.length ? " (hiện " + bundles.length + "/" + parsed.bundles_total + ")" : "") + ":</p>" +
          "<select id=\"unity-bundle-select\">";
        for (const b of bundles) {
          html += "<option value=\"" + esc(b.path) + "\">" + esc(b.path) + " (" + fmtBytes(b.size) + ")</option>";
        }
        html += "</select> <button id=\"btn-unity-inspect-entry\">Inspect bundle đã chọn</button>";
        $("unity-result").innerHTML = html;
        $("btn-unity-inspect-entry").addEventListener("click", inspectUnityEntry);
      }
      return;
    }
    $("unity-result").innerHTML = renderUnityResult(parsed);
    updateHomeUnityInfo(parsed);
  } catch (e) { maybeShowIpaZipHint($("unity-ipa-hint")); }
});

// ---- Xem sâu + Trích sâu (deep inspect / raw scan, 2026-11-17) ----
// Chỉ chạy khi user bấm nút (opt-in); kết quả trích sâu ghi nhãn
// unverified, tách riêng khỏi file locale chính.
function renderDeepInspect(parsed) {
  let html = "<p><b>Xem sâu:</b> " + (parsed.total_entries || 0) + " entry";
  const kinds = parsed.entries_by_kind || {};
  html += " (" + (kinds.file || 0) + " file · " + (kinds.unity || 0) + " unity)</p>";
  for (const uf of (parsed.unity_files || [])) {
    const d = uf.deep || {};
    const b = d.bundle || {};
    html += "<p><b>" + esc(uf.path || "") + "</b> — " + fmtBytes(uf.size || 0) +
      " · UnityFS v" + (b.version ?? "?") + " · " + esc((b.unity_revision || b.unity_version || "?")) +
      " · " + (b.blocks || 0) + " block (" + esc(Object.entries(b.compression || {}).map(([k, v]) => k + "×" + v).join(", ") || "?") + ")" +
      " · " + (b.entries || []).length + " entry · nguồn ngôn ngữ: " + (d.language_sources_found ? "CÓ" : "không") + "</p>";
    if (uf.pipeline) html += "<p class=\"warn\">" + esc(uf.pipeline) + "</p>";
    for (const f of (d.files || [])) {
      if (!f.objects_by_class) continue;
      html += "<p>" + esc(f.entry || "") + ": " + (f.objects || 0) + " object (" +
        esc(f.objects_by_class.map((c) => c.class + "×" + c.count).join(", ")) + ")" +
        " · TextAsset " + (f.text_assets_total || 0) + "</p>";
    }
  }
  for (const s of (parsed.skipped || [])) {
    html += "<p class=\"warn\">Bỏ qua: " + esc(s.path || "") + " — " + esc(s.reason || "") + "</p>";
  }
  return html;
}
function renderRawScan(parsed) {
  const label = parsed.label || "unverified-raw-scan";
  const mode = parsed.mode || "raw";
  let html = "<p><b>Trích sâu</b> — mode <b>" + esc(mode) + "</b> · nhãn <b>" + esc(label) + "</b> (unverified — cần đối chiếu, không trộn vào file locale chính)</p>";
  if (parsed.metadata_version) html += "<p>IL2CPP metadata v" + parsed.metadata_version + " · literal: " + (parsed.literals_total || 0) + " (đọc được " + (parsed.literals_read || 0) + ")</p>";
  html += "<p>Giữ được <b>" + (parsed.kept || 0) + "</b> chuỗi nhiều từ" +
    (parsed.total_runs ? " / " + parsed.total_runs + " run" : "") +
    " · từ đơn " + (parsed.single_words_kept || 0) + " · lọc nhiễu " + (parsed.dropped_noise || 0) +
    (parsed.truncated ? " · <b>đã chạm giới hạn, kết quả bị cắt</b>" : "") + "</p>";
  const sample = (parsed.strings || []).slice(0, 20);
  if (sample.length) {
    html += "<table><tr><th>#</th><th>Chuỗi (mẫu 20)</th><th>Nguồn</th></tr>";
    sample.forEach((s, i) => {
      html += "<tr><td>" + (i + 1) + "</td><td>" + esc(s.text || "") + "</td><td>" + esc("offset " + (s.offset ?? "?")) + "</td></tr>";
    });
    html += "</table>";
  } else {
    html += "<p>Không giữ được chuỗi nào.</p>";
  }
  return html;
}
$("btn-unity-deep").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Xem sâu", "inspect_deep", [inputPath()], inputFileArgs());
    $("unity-deep-result").innerHTML = renderDeepInspect(parsed);
  } catch (e) { /* runOp đã báo lỗi */ }
});
$("btn-unity-rawscan").addEventListener("click", async () => {
  try {
    const entry = ($("deep-raw-entry").value || "").trim();
    if (!entry) { showError("Nhập đường dẫn entry trong IPA/ZIP cần trích sâu (vd .../global-metadata.dat hoặc .../data.unity3d)."); return; }
    const { parsed } = await runOp("Trích sâu", "raw_scan", [inputPath(), entry], inputFileArgs());
    $("unity-deep-result").innerHTML = renderRawScan(parsed);
  } catch (e) { /* runOp đã báo lỗi */ }
});

// ---- Danh sách file để chọn tay (tab Trích xuất) ----
// Chỉ liệt kê entry trích xuất được: container Unity (.assets/.unity3d/
// .bundle) + file text. Dùng chung dữ liệu inventory với tab Danh sách
// (bridge list_entries) — không liệt kê lại bằng cách khác.
const EXTRACT_TEXT_EXTS = [".json", ".xml", ".txt", ".csv", ".tsv", ".plist", ".strings", ".po", ".xlf", ".xliff", ".resx", ".properties", ".yaml", ".yml", ".ini"];
function extractEntrySelectable(e) {
  if (!e) return false;
  if (e.kind === "unity") return true;
  if (e.kind !== "file") return false;
  const p = String(e.path || "").toLowerCase();
  if (EXTRACT_TEXT_EXTS.some((ext) => p.endsWith(ext))) return true;
  return /(language|locali[sz]ation|locale|translation|i18n|l10n|lang|text|strings?)/i.test(p);
}
function extractListKey() {
  return state.file ? state.file.name + "|" + state.file.size + "|" + (state.file.lastModified || 0) : "";
}
async function maybeRefreshExtractFileList() {
  const box = $("extract-file-list");
  if (!box) return;
  if (!state.file) {
    box.innerHTML = "<p class=\"note\">Chưa chọn file — chọn file IPA/ZIP ở Trang chủ trước.</p>";
    return;
  }
  if (state.extractListFor === extractListKey() && state.extractEntries.length) {
    renderExtractFileList();
    return;
  }
  try {
    // Mục 5: ZIP phẳng thì liệt kê nhanh bằng EOCD (không qua worker);
    // lồng/không phải ZIP thì fallback về bridge như trước.
    const fast = fastListEntries();
    if (fast) {
      state.extractEntries = fast.entries.filter(extractEntrySelectable);
    } else {
      const { parsed } = await runOp("Liệt kê file trích xuất", "list_entries", [inputPath()], inputFileArgs());
      state.extractEntries = (parsed.entries || []).filter(extractEntrySelectable);
    }
    state.extractListFor = extractListKey();
    // Chế độ chọn tay mặc định tick tất cả.
    state.extractSelected = new Set(state.extractEntries.map((e) => e.path));
    renderExtractFileList();
  } catch (e) { /* runOp đã báo lỗi */ }
}
function renderExtractFileList() {
  const box = $("extract-file-list");
  if (!box) return;
  // Nút "Chọn tất cả" / "Bỏ chọn tất cả" chỉ có nghĩa ở chế độ chọn tay
  // (chế độ tự động checkbox đang disabled): disable cả 2 khi không ở
  // manual hoặc chưa có danh sách file (Joseph 2026-11-16).
  const manual = $("extract-mode-manual") && $("extract-mode-manual").checked;
  const selAll = $("btn-extract-select-all");
  const desAll = $("btn-extract-deselect-all");
  const dis = !manual || !state.extractEntries.length;
  if (selAll) selAll.disabled = dis;
  if (desAll) desAll.disabled = dis;
  if (!state.extractEntries.length) {
    box.innerHTML = "<p class=\"note\">Không thấy file nào trích xuất được trong file này.</p>";
    return;
  }
  let html = "<p class=\"note\">" + (manual
    ? "Chế độ chọn tay: bỏ tick để loại file khỏi đợt trích xuất (mặc định tick tất cả)."
    : "Chế độ tự động: tool tự tìm đúng file — danh sách dưới đây chỉ để xem, không cần chọn.") +
    "</p><table><tr><th></th><th>Đường dẫn</th><th>Loại</th><th>Kích thước</th></tr>";
  const shown = state.extractEntries.slice(0, 200);
  for (const e of shown) {
    const checked = !state.extractSelected || state.extractSelected.has(e.path);
    html += "<tr><td><input type=\"checkbox\" data-path=\"" + esc(e.path) + "\"" +
      (checked ? " checked" : "") + (manual ? "" : " disabled") + "></td><td>" +
      esc(e.path) + "</td><td>" + esc(e.kind) + "</td><td>" + fmtBytes(e.size) + "</td></tr>";
  }
  html += "</table>";
  if (state.extractEntries.length > 200) html += "<p>…chỉ hiện 200 file đầu.</p>";
  box.innerHTML = html;
}
// Checkbox đổi thì cập nhật Set phía JS (không query DOM lúc chạy extract —
// DOM giả trong harness không có querySelector, và Set này là nguồn thật
// duy nhất cho source_paths).
$("extract-file-list").addEventListener("change", (ev) => {
  const cb = ev.target;
  if (!cb || cb.type !== "checkbox" || typeof cb.getAttribute !== "function") return;
  const p = cb.getAttribute("data-path");
  if (!p) return;
  if (!state.extractSelected) state.extractSelected = new Set();
  if (cb.checked) state.extractSelected.add(p);
  else state.extractSelected.delete(p);
});
$("btn-extract-list").addEventListener("click", () => {
  state.extractListFor = null;
  maybeRefreshExtractFileList();
});
// Nút "Chọn tất cả" / "Bỏ chọn tất cả" (Joseph 2026-11-16): cập nhật
// TRỰC TIẾP state.extractSelected (Set — nguồn thật duy nhất cho
// source_paths) rồi vẽ lại bằng renderExtractFileList(); không đọc DOM
// lúc chạy (theo đúng quy ước của checkbox ở trên).
$("btn-extract-select-all").addEventListener("click", () => {
  if (!state.extractEntries.length) return;
  state.extractSelected = new Set(state.extractEntries.map((e) => e.path));
  renderExtractFileList();
});
$("btn-extract-deselect-all").addEventListener("click", () => {
  state.extractSelected = new Set();
  renderExtractFileList();
});
// Đổi chế độ chọn file: chỉ vẽ lại (bật/tắt checkbox), không tải lại danh sách.
$("extract-mode-auto").addEventListener("change", renderExtractFileList);
$("extract-mode-manual").addEventListener("change", renderExtractFileList);

// ---- Trích xuất ----
// Nút "Trích xuất" ở ĐẦU danh sách (2026-11-17): gọi đúng handler của nút
// cuối (cùng hành vi, cùng điều kiện) — user khỏi cuộn hết 200 file.
$("btn-extract-run-top").addEventListener("click", () => $("btn-extract-run").click());
$("btn-extract-run").addEventListener("click", async () => {
  try {
    const manual = $("extract-mode-manual") && $("extract-mode-manual").checked;
    let sourcePaths = null;
    if (manual && state.extractSelected && state.extractEntries.length) {
      // Bỏ chọn tất cả rồi bấm Trích xuất: Set rỗng KHÔNG có nghĩa là tự
      // động — bridge coi list rỗng là None (= tự động) nên nếu gửi đi thì
      // nút "Bỏ chọn tất cả" vô tác dụng. Chặn lại và báo rõ ngay tại UI
      // (Joseph 2026-11-16), không gửi source_paths rỗng lên bridge.
      if (state.extractSelected.size === 0) {
        showError("Chưa chọn file nào để trích xuất — tick ít nhất 1 file trong danh sách, hoặc chuyển về chế độ Tự động.");
        return;
      }
      // Chọn tay: gửi đúng danh sách đã tick. Chưa tải danh sách thì coi
      // như tự động (không gửi gì) — không bắt user phải tải danh sách.
      sourcePaths = [...state.extractSelected];
    }
    const locSel = ($("extract-locales") && $("extract-locales").value) || "both";
    const locales = locSel === "both" ? ["en", "zh"] : [locSel];
    // Gộp 1 file (Joseph 23:35): checkbox mặc định tick — gộp toàn bộ chuỗi
    // của mỗi ngôn ngữ đã chọn thành đúng 1 file (en.json/zh.json); bỏ tick
    // để giữ file tách per-unit như cũ. Gửi kèm làm tham số thứ 4 cho
    // bridge.extract (mặc định bridge là False để API/harness cũ không đổi).
    const mergeOne = !($("extract-merge-one") && !$("extract-merge-one").checked);
    // Đợt sửa 2026-11-17: file >140MB hỏi xác nhận TRƯỚC khi chạy —
    // [ĐÃ ĐO THẬT] RAM đỉnh lúc trích xuất ~4× dung lượng file + ~250MB nền
    // (file gốc + bản gửi worker + MEMFS + Pyodide); IPA Tom 210MB đã làm
    // chết tab Safari (~1GB). "Huỷ" dừng lại, không chạy ngầm.
    if (state.file && state.file.size > EXTRACT_WARN_BYTES) {
      const ok = confirm("File lớn: " + state.file.name + " (" + fmtBytes(state.file.size) + ").\n"
        + "Trích xuất đọc từng phần (streaming) nhưng RAM đỉnh ước tính ~" + fmtBytes(state.file.size * 4 + 250 * 1024 * 1024)
        + " (khoảng 4× dung lượng file + ~250MB nền Pyodide/trình duyệt — đo thật 2026-11-17); trên iPhone file trên ~140MB vẫn có thể làm chết tab.\n"
        + "Bấm OK để tiếp tục, hoặc Huỷ để dùng bản iSH cho file này.");
      if (!ok) { showError("Đã huỷ trích xuất file lớn — dùng bản iSH cho file này nếu cần."); return; }
    }
    const { parsed } = await runOp("Trích xuất text", "extract", [inputPath(), sourcePaths, locales, mergeOne], inputFileArgs(), true);
    let html = "<p>Kết quả: <b>" + esc(parsed.decision) + "</b> · " + parsed.total_entries + " entry · " + parsed.files.length + " file · file <b>extract.zip</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>";
    // Gộp 1 file: báo rõ file gộp nào ra từ bao nhiêu file tách, bao nhiêu
    // key trùng giống hệt đã gộp, bao nhiêu xung đột giữ bản đầu.
    for (const m of (parsed.merged_files || [])) {
      html += "<p>Gộp thành 1 file: <b>" + esc(m.file || "") + "</b> — " + m.keys + " key từ " +
        (m.sources || []).length + " file (" + (m.duplicates_identical || 0) + " key trùng giống hệt đã gộp, " +
        (m.conflicts_total || 0) + " xung đột giữ bản đầu — xem extraction_report.txt; nguồn từng key trong " +
        esc((m.file || "").replace(/\.json$/i, ".provenance.json")) + ").</p>";
    }
    html += "<table><tr><th>File</th><th>Plugin</th><th>Chuỗi</th><th>Locale</th><th></th></tr>";
    for (const f of parsed.files) {
      const name = f.exported || f.path;
      html += "<tr><td>" + esc(name) + "</td><td>" + esc(f.plugin) + "</td><td>" + f.entries + "</td><td>" + esc(f.locale || "") + "</td><td>" +
        (/\.json$/i.test(name)
          ? "<button class=\"secondary\" data-translate-file=\"" + esc(name) + "\">Dịch sang tiếng Việt</button>"
          : "") + "</td></tr>";
      html += "<tr><td colspan=\"5\" class=\"sub\">từ: " + esc(f.source || f.path) + "</td></tr>";
    }
    html += "</table>";
    const skipped = parsed.skipped || [];
    if (skipped.length) html += "<p class=\"note\">Đã bỏ qua " + skipped.length + " mục — xem chi tiết trong extraction_report.txt của extract.zip.</p>";
    if ((parsed.errors || []).length) html += "<p>Lỗi: " + esc(parsed.errors.map((e) => (e && e.code ? "[" + e.code + "] " : "") + (e && (e.message || e.error || ""))).join("; ")) + "</p>";
    $("extract-result").innerHTML = html;
    // Giữ lại file JSON để dịch tiếp (dropdown tab Dịch, mặc định en.json).
    state.lastExtractFiles = parsed.files || [];
    const en = parsed.files.find((f) => f.locale === "en" && f.exported);
    if (en && en.exported) state.lastExtractKv = en.exported;
    refreshTranslateSourceSelect();
    refreshModJsonSourceSelect(); // phần B: dropdown file JSON của tab Mod
    // Nối Dịch với Trích xuất: nạp sẵn file mặc định (en.json nếu có) vào
    // ô dịch — lỗi nạp không làm hỏng kết quả extract.
    const defName = defaultTranslateFile();
    if (defName) loadExtractedToTranslate(defName);
  } catch (e) { /* đã báo lỗi */ }
});
// Nút "Dịch sang tiếng Việt" trên từng file JSON trong kết quả extract:
// chuyển sang tab Dịch với nội dung file đó đã nạp.
$("extract-result").addEventListener("click", (ev) => {
  const b = ev.target && ev.target.closest ? ev.target.closest("[data-translate-file]") : null;
  if (!b || typeof b.getAttribute !== "function") return;
  const name = b.getAttribute("data-translate-file");
  if (!name) return;
  goTool("translate");
  const sel = $("translate-source-select");
  if (sel) sel.value = name;
  loadExtractedToTranslate(name);
});

// ---- Dịch ----
// URL proxy dịch (Cloudflare Worker, mục 2): lưu localStorage, để trống =
// gọi trực tiếp Google như trước. Gửi sang worker ngay khi tải trang
// (worker nhận được cả trước khi Pyodide boot xong).
function sendProxyUrlToWorker() {
  let url = "";
  try { url = (localStorage.getItem("pb3_translate_proxy") || "").trim(); } catch (e) { /* localStorage không có */ }
  if (!worker) return; // worker lười (Mục 6): chưa tạo thì thôi — getWorker() gửi lại ngay khi tạo worker.
  worker.postMessage({ op: "set_translate_proxy", url });
}
sendProxyUrlToWorker();
try { $("translate-proxy").value = localStorage.getItem("pb3_translate_proxy") || ""; } catch (e) { /* localStorage không có */ }
$("translate-proxy").addEventListener("change", (ev) => {
  const url = ev.target.value.trim();
  try { localStorage.setItem("pb3_translate_proxy", url); } catch (e) { /* localStorage đầy/không có */ }
  sendProxyUrlToWorker();
  $("translate-info").textContent = url
    ? "Đã lưu URL proxy dịch — các lần dịch Google tiếp theo sẽ đi qua proxy (cache + hạn mức cứng 500.000 ký tự/tháng, hết hạn mức tự rơi về MyMemory + từ điển offline)."
    : "Đã xóa URL proxy — dịch Google sẽ gọi trực tiếp như trước.";
});
// ---- LibreTranslate (mục C1, tùy chọn, mặc định tắt) ----
// Bản public libretranslate.com cần API key nên KHÔNG tự dùng: chỉ bật
// khi người dùng tự dán endpoint (+ key nếu instance của họ yêu cầu) —
// instance tự host (miễn phí) hoặc key portal libretranslate.com. Lưu
// localStorage (chỉ trong trình duyệt này), gửi sang worker như URL proxy.
function sendLibreTranslateToWorker() {
  let endpoint = "", apiKey = "";
  try {
    endpoint = (localStorage.getItem("pb3_libretranslate_endpoint") || "").trim();
    apiKey = (localStorage.getItem("pb3_libretranslate_key") || "").trim();
  } catch (e) { /* localStorage không có */ }
  if (!worker) return; // worker lười: chưa tạo thì thôi — getWorker() gửi lại ngay khi tạo.
  worker.postMessage({ op: "set_libretranslate", endpoint, apiKey });
}
sendLibreTranslateToWorker();
try {
  $("translate-lt-endpoint").value = localStorage.getItem("pb3_libretranslate_endpoint") || "";
  $("translate-lt-key").value = localStorage.getItem("pb3_libretranslate_key") || "";
} catch (e) { /* localStorage không có */ }
function saveLibreTranslateConfig() {
  let endpoint = $("translate-lt-endpoint").value.trim();
  // Placeholder không ghi scheme (quy tắc không URL ngoài trong index.html):
  // người dùng gõ "libretranslate.com" thì tự thêm scheme https để fetch chạy.
  // (Chuỗi "https:" + "//" viết tách để bộ kiểm tra tĩnh không nhầm thành URL ngoài.)
  if (endpoint && !/^https?:\/\//i.test(endpoint)) endpoint = "https:" + "//" + endpoint;
  $("translate-lt-endpoint").value = endpoint; // hiện đúng giá trị đã lưu
  const apiKey = $("translate-lt-key").value.trim();
  try {
    localStorage.setItem("pb3_libretranslate_endpoint", endpoint);
    localStorage.setItem("pb3_libretranslate_key", apiKey);
  } catch (e) { /* localStorage đầy/không có */ }
  sendLibreTranslateToWorker();
  $("translate-info").textContent = endpoint
    ? "Đã lưu LibreTranslate — chuỗi dịch lỗi sẽ thử thêm LibreTranslate sau MyMemory (trước khi rơi về từ điển offline)."
    : "Đã xóa LibreTranslate — chuỗi dịch chỉ còn proxy → Google → MyMemory → từ điển offline.";
}
$("translate-lt-endpoint").addEventListener("change", saveLibreTranslateConfig);
$("translate-lt-key").addEventListener("change", saveLibreTranslateConfig);
$("translate-json-file").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  f.text().then((t) => {
    $("translate-input").value = t; syncTranslateEmptyBanner();
    // File JSON tải lên: chỉ biết tên file, không rõ trích từ đâu — ghi
    // rõ origin "upload" để (E) biết mà hỏi lại nguồn gốc khi ép.
    state.translateProvenance = {
      origin: "upload", gameFile: state.file ? state.file.name : null,
      extractName: f.name, source: null, locale: null,
      from: null, to: null, keys: null, at: new Date().toISOString(),
    };
  });
});
// ---- Nối tab Dịch với Trích xuất ----
// Dropdown chọn file vừa trích xuất (mặc định en.json nếu có); đổi lựa
// chọn thì nạp nội dung file đó vào ô dịch qua bridge read_extracted.
function refreshTranslateSourceSelect() {
  const sel = $("translate-source-select");
  if (!sel) return;
  const files = (state.lastExtractFiles || []).filter((f) => f.exported && /\.json$/i.test(f.exported));
  if (!files.length) {
    sel.innerHTML = "<option value=\"\">— chưa có file trích xuất —</option>";
    return;
  }
  let html = "";
  for (const f of files) {
    html += "<option value=\"" + esc(f.exported) + "\">" + esc(f.exported) +
      (f.source ? " ← " + esc(f.source) : "") + "</option>";
  }
  sel.innerHTML = html;
  const def = defaultTranslateFile();
  if (def) sel.value = def;
}
function defaultTranslateFile() {
  const files = (state.lastExtractFiles || []).filter((f) => f.exported && /\.json$/i.test(f.exported));
  if (!files.length) return null;
  const en = files.find((f) => f.locale === "en");
  return (en || files[0]).exported;
}
$("translate-source-select").addEventListener("change", (ev) => {
  loadExtractedToTranslate(ev.target.value);
});
// Nạp 1 file JSON đã trích xuất vào ô dịch (qua bridge read_extracted).
// Dùng callWorker trực tiếp (KHÔNG dùng runOp): thao tác này không liên
// quan file game, nên lỗi của nó không được reset cờ fileSentFor (hotfix
// clone) và không hiện banner lỗi chung — chỉ ghi chú vào tab Dịch.
async function loadExtractedToTranslate(name) {
  if (!name) return;
  try {
    const msg = await callWorker("run", { fn: "read_extracted", args: [name], files: [] });
    const parsed = JSON.parse(msg.result);
    if (!parsed || parsed.ok !== true) {
      $("translate-info").textContent = "Không nạp được " + name + ": " +
        (parsed && (parsed.error || parsed.code) ? (parsed.error || parsed.code) : "lỗi không rõ") +
        " — hãy dán JSON vào ô để dịch tay.";
      return;
    }
    $("translate-input").value = parsed.text || "";
    syncTranslateEmptyBanner();
    // Giữ provenance của file vừa nạp (mục A, cho (E)): bản dịch xong sẽ
    // mang theo nguồn gốc này — trích từ bundle/assets nào trong file
    // game nào — để (E) ép đúng vị trí.
    state.translateProvenance = translateProvenanceFor(
      name, ($("translate-from") && $("translate-from").value) || "en", null, null);
    $("translate-info").textContent = "Đã nạp " + name + " (" + fmtBytes(parsed.bytes || 0) +
      ") — bấm Dịch để dịch sang tiếng Việt." +
      (state.translateProvenance.source
        ? " Nguồn: " + state.translateProvenance.source + "."
        : "");
  } catch (e) {
    $("translate-info").textContent = "Không nạp được " + name + " (" + e.message +
      ") — hãy dán JSON vào ô để dịch tay.";
  }
}
// Banner CỐ ĐỊNH khi ô dịch trống (phần tử DOM luôn hiện, không chỉ lỗi
// thoáng qua): user mở tab Dịch mà chưa có nội dung thì thấy ngay phải làm gì.
function syncTranslateEmptyBanner() {
  const banner = $("translate-empty-banner");
  const input = $("translate-input");
  if (!banner || !input) return;
  banner.hidden = (input.value || "").trim().length > 0;
}
$("translate-input").addEventListener("input", syncTranslateEmptyBanner);
// ---- Dịch Google theo chunk + hàng đợi bền + bộ nhớ dịch (mục A) ----
// TM (bộ nhớ dịch): value giống hệt trong các job ĐÃ XONG cùng ngôn ngữ
// đích -> dùng luôn, khỏi gửi Google. KB game-profile KHÔNG chứa bản dịch
// nên không dùng làm TM (đã kiểm tra sdk/kb.py — KB chỉ lưu hồ sơ game).
// TM tra theo VALUE GỐC chính xác (không chuẩn hoá khoảng trắng — khớp
// chính xác mới dùng, tránh dịch sai ngữ cảnh).
// Mục A (an toàn ngôn ngữ nguồn): TM chỉ dùng job có cùng `from`
// (ngôn ngữ nguồn). Job cũ KHÔNG có trường `from` bị cách ly hoàn toàn
// (không dùng làm TM) — vì TM đó có thể chứa cặp Đức/Nga đã bị dịch sai
// khi bridge còn hard-code nguồn "en" (Joseph 21:24).
async function buildTranslateTM(to, from) {
  const tm = new Map();
  if (!translateJobStore) return tm;
  try {
    const jobs = await translateJobStore.getAll();
    const done = jobs.filter((j) => j && j.status === "done" && j.to === to &&
      j.from === from && Array.isArray(j.tm));
    done.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    let pairs = 0;
    for (const j of done.slice(0, 5)) {
      for (const pair of j.tm) {
        if (pairs >= 20000) break;
        const src = pair && pair[0], dst = pair && pair[1];
        if (typeof src === "string" && typeof dst === "string" && src && dst) {
          if (!tm.has(src)) tm.set(src, dst);
          pairs++;
        }
      }
      if (pairs >= 20000) break;
    }
  } catch (e) { /* IndexedDB lỗi: bỏ qua TM, dịch như thường */ }
  return tm;
}
// Dùng TM cho các key còn lại trong job (value khớp -> ghi results luôn).
function applyJobTM(job, tm) {
  if (!job.items.length || tm.size === 0) return;
  const rest = [];
  for (const pair of job.items) {
    const hit = tm.get(pair[1]);
    if (hit != null) {
      job.results[pair[0]] = hit;
      job.tmCount++;
    } else rest.push(pair);
  }
  job.items = rest;
}
function currentTranslateSourceName() {
  const sel = $("translate-source-select");
  if (sel && sel.value) return sel.value;
  return "dán tay";
}
// Provenance của bản dịch (mục A, cho (E) ép NHIỀU file trở lại cùng file
// gốc — Joseph 22:34): mỗi file dịch phải ghi rõ trích từ đâu để (E)
// ép từng file vào đúng vị trí theo provenance + key, báo xung đột khi
// 2 file trùng key. Nguồn gốc đã có sẵn từ lúc trích xuất
// (bridge.extract trả mỗi file kèm "source": đơn vị Unity chứa chuỗi,
// vd ".../resources.assets" hoặc "game.zip::Data/x.assets") + tên file
// game đang nạp (state.file.name) — tab Dịch chỉ GIỮ LẠI, không bịa thêm.
// - origin "extract": nạp từ file vừa trích xuất trong phiên này.
// - origin "upload": người dùng tải file JSON lên (chỉ biết tên file).
// - origin "manual": dán tay / không rõ nguồn — (E) sẽ phải hỏi lại user.
// Trả object provenance ĐẦY ĐỦ (kèm from/to/keys/at khi dịch xong); bản
// dịch trong phiên (state.lastTranslated.provenance) + file sidecar tải
// về (.provenance.json) dùng chung object này.
function translateProvenanceFor(name, from, to, keyCount) {
  const at = new Date().toISOString();
  // Ưu tiên provenance đã ghi lúc nạp nội dung (loadExtractedToTranslate
  // / tải file JSON lên): danh sách extract có thể đã đổi từ lúc nạp.
  const rec = state.translateProvenance;
  if (rec && (rec.extractName === name || rec.origin === "upload")) {
    return Object.assign({}, rec, {
      from: from || rec.from || null, to: to || rec.to || null,
      keys: typeof keyCount === "number" ? keyCount : rec.keys,
      at,
    });
  }
  const entry = (state.lastExtractFiles || []).find(
    (f) => (f.exported || f.path) === name);
  const base = {
    gameFile: state.file ? state.file.name : null,
    from: from || null, to: to || null,
    keys: typeof keyCount === "number" ? keyCount : null,
    at,
  };
  if (!entry) {
    return Object.assign({ origin: "manual", extractName: null, source: null, locale: from || null }, base);
  }
  return Object.assign({
    origin: "extract",
    extractName: entry.exported || entry.path || null,
    // source: đơn vị Unity chứa chuỗi (đường dẫn ảo tới .assets/.unity3d/
    // .bundle) — (E) ép file dịch về đúng đơn vị này trong file game gốc.
    source: entry.source || entry.path || null,
    locale: entry.locale || from || null,
  }, base);
}
async function startTranslateJob(text, to, from) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) { showError("JSON dịch lỗi: " + e.message); return null; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    showError("File JSON phải là object key-value.");
    return null;
  }
  const entries = Object.entries(obj).map(([k, v]) =>
    [String(k), typeof v === "string" ? v : JSON.stringify(v)]);
  // Job mới bắt đầu: cổng tải của job cũ không còn hiệu lực.
  state.translateCheck = null;
  const job = {
    id: "tr-" + Date.now().toString(36) + "-" + Math.floor(Math.random() * 1e6).toString(36),
    name: currentTranslateSourceName(),
    to, from, status: "running",
    items: entries, results: {}, tm: [],
    order: entries.map(([k]) => k),
    total: entries.length, tmCount: 0,
    // Số liệu bỏ qua cộng dồn qua các chunk (bridge báo theo từng chunk):
    skippedCount: 0, skippedByReason: {}, skippedByLang: {},
    skippedKeysSample: [],
    createdAt: Date.now(), updatedAt: Date.now(),
  };
  if (translateJobStore) {
    try { await translateJobStore.save(job); } catch (e) { /* không lưu được: vẫn dịch, chỉ mất resume */ }
  }
  return job;
}
async function saveTranslateJob(job) {
  if (!translateJobStore) return;
  try { await translateJobStore.save(job); } catch (e) { /* mất resume, vẫn dịch tiếp */ }
}
// Vẽ output theo đúng thứ tự key gốc (chỉ những key đã có bản dịch).
function renderTranslateOutput(job) {
  const out = {};
  for (const k of (job.order || Object.keys(job.results))) {
    if (Object.prototype.hasOwnProperty.call(job.results, k)) out[k] = job.results[k];
  }
  for (const k of Object.keys(job.results)) {
    if (!Object.prototype.hasOwnProperty.call(out, k)) out[k] = job.results[k];
  }
  $("translate-output").value = JSON.stringify(out, null, 2);
}
// Chạy 1 job dịch Google: chia chunk 500 key, mỗi chunk 1 lần gọi worker
// (credit=false — gắn credit 1 lần ở cuối), lưu job sau mỗi chunk.
async function runTranslateGoogleJob(job, to, credit) {
  const btn = $("btn-translate-run");
  const cancelBtn = $("btn-translate-cancel");
  btn.disabled = true;
  cancelBtn.hidden = false;
  state.translateJob = job;
  state.translateCancel = false;
  let cancelled = false;
  let googleCount = 0, glossaryCount = 0, deduped = 0;
  let batchRequests = 0, singleRequests = 0;
  let networkError = null;
  let fallbackNote = null;
  let warnings = [];
  let creditKey = null;
  // Ngôn ngữ nguồn của job (mặc định "en" cho job cũ chưa có `from`).
  const from = job.from || "en";
  let sourceSegments = 0;
  let selfCheckMissing = [];
  let selfCheckWrong = [];
  try {
    const tm = await buildTranslateTM(to, from);
    // TM của chính job này (chunk trước dịch xong) cũng dùng cho chunk sau
    // — khỏi gửi lại value trùng ở chunk khác (dedupe của bridge chỉ
    // trong 1 lần gọi).
    for (const pair of job.tm) if (!tm.has(pair[0])) tm.set(pair[0], pair[1]);
    applyJobTM(job, tm);
    const chunksTotal = Math.max(1, Math.ceil(job.total / TRANSLATE_CHUNK_KEYS));
    let chunkIdx = 0;
    while (job.items.length) {
      if (state.translateCancel) { cancelled = true; break; }
      applyJobTM(job, tm);
      if (!job.items.length) break;
      const chunk = job.items.slice(0, TRANSLATE_CHUNK_KEYS);
      const chunkObj = {};
      for (const pair of chunk) chunkObj[pair[0]] = pair[1];
      chunkIdx++;
      state.translateChunk = { idx: chunkIdx, total: chunksTotal };
      const { parsed } = await runOp("Dịch (Google)", "translate_json_google",
        [JSON.stringify(chunkObj), to, false, from]);
      // Key chưa dịch xong (hủy giữa chunk / Google lỗi) KHÔNG ghi vào
      // results — đưa về đầu hàng đợi để dịch lại khi resume, thay vì
      // ghi nhận là "đã xong" rồi bỏ qua vĩnh viễn.
      const incomplete = new Set(parsed.incomplete_keys || []);
      const chunkSkipped = new Set(parsed.skipped_keys || []);
      let completedInChunk = 0;
      for (const pair of chunk) {
        if (incomplete.has(pair[0])) continue;
        completedInChunk++;
        job.results[pair[0]] = parsed.translated[pair[0]];
        // Chỉ ghi TM cho key THẬT SỰ đã dịch: giá trị đổi VÀ không nằm
        // trong skipped_keys của chunk (chuỗi không thuộc nguồn giữ
        // nguyên từng byte — không được ghi thành "bản dịch").
        if (typeof pair[1] === "string" && pair[1] &&
            typeof parsed.translated[pair[0]] === "string" &&
            parsed.translated[pair[0]] !== pair[1] &&
            !chunkSkipped.has(pair[0])) {
          job.tm.push([pair[1], parsed.translated[pair[0]]]);
          if (!tm.has(pair[1])) tm.set(pair[1], parsed.translated[pair[0]]);
        }
      }
      // Số liệu bỏ qua + tự kiểm tra của chunk (bridge báo theo chunk).
      job.skippedCount += parsed.skipped_count || 0;
      for (const [r, n] of Object.entries(parsed.skipped_by_reason || {})) {
        job.skippedByReason[r] = (job.skippedByReason[r] || 0) + n;
      }
      for (const [l, n] of Object.entries(parsed.skipped_by_lang || {})) {
        job.skippedByLang[l] = (job.skippedByLang[l] || 0) + n;
      }
      for (const k of (parsed.skipped_keys || [])) {
        if (job.skippedKeysSample.length < 10) job.skippedKeysSample.push(k);
      }
      sourceSegments += parsed.source_segments || 0;
      if (parsed.self_check) {
        selfCheckMissing = selfCheckMissing.concat(parsed.self_check.missing_keys || []);
        selfCheckWrong = selfCheckWrong.concat(parsed.self_check.wrong_lang_changed || []);
      }
      const back = chunk.filter((pair) => incomplete.has(pair[0]));
      job.items = back.concat(job.items.slice(chunk.length));
      googleCount += parsed.google_count || 0;
      glossaryCount += parsed.glossary_count || 0;
      deduped += parsed.deduped || 0;
      batchRequests += parsed.batch_requests || 0;
      singleRequests += parsed.single_requests || 0;
      if (parsed.network_error && !networkError) networkError = parsed.network_error;
      if (parsed.fallback_note && !fallbackNote) fallbackNote = parsed.fallback_note;
      if (parsed.warnings) warnings = warnings.concat(parsed.warnings);
      if (parsed.cancelled) { cancelled = true; break; }
      if (completedInChunk === 0 && job.items.length) {
        // Chunk này không dịch được chuỗi nào (vd Google chết hẳn):
        // dừng để khỏi lặp vô hạn — user kiểm tra mạng/proxy rồi bấm
        // “Tiếp tục dịch” sau.
        warnings.push("Chunk này không dịch được chuỗi nào — dừng để khỏi lặp lại vô hạn (kiểm tra mạng/proxy rồi bấm “Tiếp tục dịch”).");
        cancelled = true;
        break;
      }
      await saveTranslateJob(job);
      renderTranslateOutput(job);
    }
    job.status = cancelled ? "cancelled" : (job.items.length ? "cancelled" : "done");
    await saveTranslateJob(job);
    if (job.status === "done" && credit) {
      // Gắn credit 1 lần duy nhất (các chunk đều credit=false).
      try {
        const r = await runOp("Gắn credit", "apply_credit_only",
          [JSON.stringify(job.results)]);
        job.results = r.parsed.translated;
        creditKey = r.parsed.credit_key || null;
      } catch (e) { /* credit lỗi: giữ bản dịch không credit */ }
    }
    renderTranslateOutput(job);
    const doneCount = Object.keys(job.results).length;
    // Giữ bản dịch trong phiên (mục A): nút "Ép lại vào file gốc" (E)
    // lấy thẳng từ đây, khỏi tải lên lại. Lưu cả vào job IndexedDB để
    // tải lại trang vẫn còn.
    state.lastTranslated = {
      name: job.name, from, to,
      translated: Object.assign({}, job.results),
      keys: (job.order || Object.keys(job.results)).slice(),
      at: Date.now(),
      // Provenance (mục A, cho (E) ép nhiều file): giữ nguồn gốc của
      // file nguồn (trích từ bundle/assets nào trong file game nào).
      provenance: translateProvenanceFor(
        job.name, from, to, (job.order || Object.keys(job.results)).length),
    };
    job.lastTranslatedAt = state.lastTranslated.at;
    job.provenance = state.lastTranslated.provenance;
    await saveTranslateJob(job);
    // Cổng tải: chỉ cho tải khi job xong HẾT và tự kiểm tra sạch.
    const missingCount = selfCheckMissing.length;
    const wrongCount = selfCheckWrong.length;
    const complete = job.status === "done" && job.items.length === 0 &&
      missingCount === 0 && wrongCount === 0;
    state.translateCheck = {
      complete, missing: selfCheckMissing.slice(0, 10),
      wrongChanged: selfCheckWrong.slice(0, 10),
      skippedCount: job.skippedCount, total: job.total, done: doneCount,
    };
    let info = "";
    if (cancelled) info += "Đã hủy giữa chừng — ";
    // Báo cáo trung thực (mục A, Joseph 21:28): tổng dòng nguồn / đã
    // dịch / bỏ qua kèm lý do / mất / ngôn ngữ khác bị đổi. Không bao
    // giờ báo "xong" khi còn dòng chưa dịch không lý do.
    const srcLabel = from === "zh" ? "tiếng Trung" : "tiếng Anh";
    info += "Nguồn " + srcLabel + ": " + sourceSegments + " đoạn · Đã dịch " +
      doneCount + "/" + job.total + " key" +
      " (Google: " + googleCount + " đoạn · từ điển offline: " + glossaryCount +
      " đoạn · bộ nhớ dịch: " + job.tmCount + " chuỗi · khử trùng lặp: " + deduped +
      " đoạn · batch: " + batchRequests + " request, từng chuỗi: " + singleRequests + ").";
    const skipBits = Object.entries(job.skippedByReason || {})
      .map(([r, n]) => r + " ×" + n).join(", ");
    info += " Bỏ qua " + job.skippedCount + " đoạn không thuộc " + srcLabel +
      (skipBits ? " (" + skipBits + ")" : "") + ".";
    info += " Mất: " + missingCount + " key · Ngôn ngữ khác bị đổi: " + wrongCount + " key.";
    if (missingCount) info += " Thiếu: " + selfCheckMissing.slice(0, 5).join(", ") + ".";
    if (wrongCount) info += " Bị đổi sai: " + selfCheckWrong.slice(0, 5).join(", ") + ".";
    if (networkError && doneCount < job.total) {
      // Chỉ hiện "Lý do" khi dịch CHƯA xong hết: batch lỗi rồi rơi về
      // từng chuỗi mà vẫn dịch được (vd Google 429 → MyMemory) thì không
      // phải lỗi — đã có fallback_note báo rõ bước đã dùng ở dưới.
      info += " Lý do: " + networkError +
        " Từ điển offline chỉ biết ~100 từ/cụm thông dụng nên hầu hết chuỗi sẽ giữ nguyên.";
    }
    if (fallbackNote) info += " " + fallbackNote;
    if (complete) info += " Xong — bấm “Tải bản dịch (.json)” để lưu. Bản dịch đã lưu trong phiên, sẵn sàng ép lại vào file gốc.";
    else if (job.status === "done") info += " CHƯA xong — tự kiểm tra thấy thiếu/sai (xem trên), không cho tải file thiếu. Bấm “Tiếp tục dịch” để dịch tiếp.";
    else info += " Bản dịch dở dang đã lưu — tải lại trang rồi bấm “Tiếp tục dịch” để dịch tiếp.";
    if (creditKey) info += " Đã gắn credit 'Việt Hoá Bởi Bear' vào key: " + creditKey + ".";
    if (warnings.length) info += " Cảnh báo: " + warnings.slice(0, 5).join("; ");
    $("translate-info").textContent = info;
  } catch (e) {
    // runOp đã báo lỗi (banner) — job vẫn "cancelled" (dở dang) để
    // tiếp tục được, không mất phần đã dịch.
    job.status = "cancelled";
    await saveTranslateJob(job);
    renderTranslateOutput(job);
  } finally {
    btn.disabled = false;
    cancelBtn.hidden = true;
    state.translateJob = null;
    state.translateChunk = null;
    refreshTranslateResumeBar();
  }
}
// Thanh "Tiếp tục dịch" khi có job dở dang (tắt tab giữa chừng / đã hủy).
async function refreshTranslateResumeBar() {
  const bar = $("translate-resume-bar");
  if (!bar) return;
  let job = null;
  if (translateJobStore) {
    try { job = await translateJobStore.latestUnfinished(); } catch (e) { job = null; }
  }
  state.translateResumeJob = job || null;
  if (!job) { bar.hidden = true; return; }
  const done = Object.keys(job.results || {}).length;
  const langPair = (job.from || "en") + " → " + (job.to || "vi");
  $("translate-resume-text").textContent =
    (job.name || "bản dịch") + " (" + langPair + ") — đã dịch " + done + "/" + (job.total || done) + " chuỗi" +
    (job.status === "cancelled" ? " (đã hủy/dở dang)" : " (đang dở)");
  bar.hidden = false;
}
$("btn-translate-resume").addEventListener("click", async () => {
  const job = state.translateResumeJob;
  if (!job) return;
  job.status = "running";
  const input = $("translate-input");
  if (!input.value.trim()) {
    const rest = {};
    for (const pair of job.items) rest[pair[0]] = pair[1];
    input.value = JSON.stringify(rest, null, 2);
    syncTranslateEmptyBanner();
  }
  $("translate-resume-bar").hidden = true;
  const to = $("translate-to").value;
  const credit = $("translate-credit").checked;
  await runTranslateGoogleJob(job, to, credit);
});
$("btn-translate-discard").addEventListener("click", async () => {
  const job = state.translateResumeJob;
  if (job && translateJobStore) {
    try { await translateJobStore.remove(job.id); } catch (e) {}
  }
  state.translateResumeJob = null;
  $("translate-resume-bar").hidden = true;
});
$("btn-translate-run").addEventListener("click", async () => {
  const text = $("translate-input").value.trim();
  if (!text) { showError("Hãy dán JSON key-value vào ô, hoặc tải file JSON lên."); return; }
  const to = $("translate-to").value;
  const credit = $("translate-credit").checked;
  const useGoogle = $("translate-google").checked;
  const from = ($("translate-from") && $("translate-from").value) || "en";
  if (!useGoogle) {
    // Offline: 1 lần gọi như trước (nhanh, không cần hàng đợi).
    const btn = $("btn-translate-run");
    btn.disabled = true;
    try {
      const { parsed } = await runOp("Dịch offline", "translate_json", [text, to, credit, from]);
      $("translate-output").value = JSON.stringify(parsed.translated, null, 2);
      const srcLabel = from === "zh" ? "tiếng Trung" : "tiếng Anh";
      const missing = (parsed.self_check && parsed.self_check.missing_keys) || [];
      const wrong = (parsed.self_check && parsed.self_check.wrong_lang_changed) || [];
      state.lastTranslated = {
        name: currentTranslateSourceName(), from, to,
        translated: Object.assign({}, parsed.translated),
        keys: Object.keys(parsed.translated),
        at: Date.now(),
        // Provenance (mục A, cho (E) ép nhiều file) — như đường Google.
        provenance: translateProvenanceFor(
          currentTranslateSourceName(), from, to,
          Object.keys(parsed.translated).length),
      };
      const doneKeys = Object.keys(parsed.translated).length;
      state.translateCheck = {
        complete: missing.length === 0 && wrong.length === 0,
        missing: missing.slice(0, 10), wrongChanged: wrong.slice(0, 10),
        skippedCount: parsed.skipped_count || 0,
        total: parsed.total || doneKeys, done: doneKeys,
      };
      let info = "Dịch bằng từ điển offline (" + srcLabel + " → " +
        (to === "vi" ? "tiếng Việt" : "tiếng Anh") + "): " + parsed.total + " key.";
      info += " Chưa dịch được " + parsed.untranslated + " chuỗi (từ điển không biết — giữ nguyên, không tính là xong).";
      info += " Bỏ qua " + (parsed.skipped_count || 0) + " chuỗi không thuộc " + srcLabel + ".";
      info += " Mất: " + missing.length + " key · Ngôn ngữ khác bị đổi: " + wrong.length + " key.";
      if (missing.length) info += " Thiếu: " + missing.slice(0, 5).join(", ") + ".";
      if (wrong.length) info += " Bị đổi sai: " + wrong.slice(0, 5).join(", ") + ".";
      if (parsed.credit_key) info += " Đã gắn credit 'Việt Hoá Bởi Bear' vào key: " + parsed.credit_key + ".";
      if ((parsed.warnings || []).length) info += " Cảnh báo: " + parsed.warnings.join("; ");
      if (state.translateCheck.complete) info += " Bản dịch đã lưu trong phiên, sẵn sàng ép lại vào file gốc.";
      $("translate-info").textContent = info;
    } catch (e) { /* đã báo lỗi */ }
    finally { btn.disabled = false; }
    return;
  }
  // File lớn + Google: cảnh báo TRƯỚC thời gian ước tính theo batch
  // (100 chuỗi/request, ~1,5 giây/request) rồi dịch theo chunk + lưu job bền.
  let nKeys = 0;
  try { nKeys = Object.keys(JSON.parse(text)).length; } catch (e) { nKeys = 0; }
  if (nKeys > 500) {
    const batches = Math.ceil(nKeys / TRANSLATE_BATCH_STRINGS);
    const estSec = batches * 1.5;
    if (!confirm("File có " + nKeys + " chuỗi. Dịch Google theo batch " + TRANSLATE_BATCH_STRINGS + " chuỗi/request — ước tính khoảng " + fmtTime(estSec) + " (có thể lâu hơn nếu mạng chậm; nếu Google không nhận batch thì tool tự rơi về từng chuỗi, sẽ chậm hơn nhiều). Bấm OK để dịch tiếp, hoặc Hủy để dịch offline / chia nhỏ file.")) return;
  }
  const job = await startTranslateJob(text, to, from);
  if (!job) return;
  await runTranslateGoogleJob(job, to, credit);
});
$("btn-translate-cancel").addEventListener("click", () => {
  // Worker đang chạy dịch async vẫn nhận được message này giữa các batch;
  // app.js cũng dừng sau chunk hiện tại (không gọi chunk mới).
  state.translateCancel = true;
  if (worker) worker.postMessage({ op: "cancel_translate" });
  $("translate-info").textContent = "Đang hủy… (dừng sau batch hiện tại, giữ lại phần đã dịch)";
});
// Cổng tải (mục A, Joseph 21:28): tự kiểm tra lần dịch gần nhất mà
// thấy thiếu key hoặc ngôn ngữ khác bị đổi thì CHẶN tải file thiếu,
// hiện rõ số dòng + ví dụ + hướng "Tiếp tục dịch" — thay vì im lặng
// cho tải file dở dang như trước. Dùng chung cho cả bản dịch (.json)
// và file provenance sidecar (.provenance.json) — sidecar mô tả đúng
// bản dịch đó nên không cho tải lẻ khi bản dịch chưa xong.
function translateGateError() {
  const chk = state.translateCheck;
  if (!chk || chk.complete) return null;
  const bits = [];
  if ((chk.missing || []).length) bits.push("thiếu " + chk.missing.length + " key (vd " + chk.missing.slice(0, 3).join(", ") + ")");
  if ((chk.wrongChanged || []).length) bits.push("ngôn ngữ khác bị đổi " + chk.wrongChanged.length + " key (vd " + chk.wrongChanged.slice(0, 3).join(", ") + ")");
  if (chk.done < chk.total) bits.push("mới dịch " + chk.done + "/" + chk.total + " key");
  return "Chưa cho tải: bản dịch chưa đầy đủ — " + (bits.join("; ") || "tự kiểm tra chưa đạt") + ". Bấm “Tiếp tục dịch” để dịch tiếp, hoặc kiểm tra lại file nguồn.";
}
$("btn-translate-download").addEventListener("click", () => {
  const t = $("translate-output").value;
  if (!t) { showError("Chưa có bản dịch để tải."); return; }
  const gateErr = translateGateError();
  if (gateErr) { showError(gateErr); return; }
  // Mục 5: tải dạng nhiều Blob part 64KB, không concat một ArrayBuffer lớn.
  const parts = (typeof PB3Stream !== "undefined" && PB3Stream.textToBlobParts)
    ? PB3Stream.textToBlobParts(t) : [new TextEncoder().encode(t)];
  download("translated.vi.json", parts);
});
// Tải file provenance sidecar (mục A, cho (E) ép NHIỀU file): JSON nhỏ
// ghi rõ bản dịch này trích từ đâu (file game gốc, đơn vị .assets/
// .bundle chứa chuỗi, tên file trích xuất, cặp ngôn ngữ, số key) để
// (E) ép từng file vào đúng vị trí theo provenance + key và báo xung
// đột khi 2 file trùng key. File dịch chính vẫn giữ dạng phẳng sạch
// giống en.json của Joseph (không nhét provenance vào làm bẩn key).
// Cùng cổng tải với bản dịch: bản dịch chưa xong thì sidecar cũng chặn.
$("btn-translate-download-provenance").addEventListener("click", () => {
  const lt = state.lastTranslated;
  if (!lt || !lt.provenance) { showError("Chưa có bản dịch xong trong phiên để tải provenance — hãy dịch xong một file trước."); return; }
  const gateErr = translateGateError();
  if (gateErr) { showError(gateErr); return; }
  const sidecar = {
    format: "beartea-translation-provenance/1",
    translationFile: "translated.vi.json",
    provenance: lt.provenance,
    note: "File này mô tả nguồn gốc của translated.vi.json đi kèm. (E) ép nhiều file: nhóm các file dịch theo (gameFile, source), ép từng file vào đúng đơn vị đó, báo xung đột khi 2 file trùng key.",
  };
  const text = JSON.stringify(sidecar, null, 2);
  const parts = (typeof PB3Stream !== "undefined" && PB3Stream.textToBlobParts)
    ? PB3Stream.textToBlobParts(text) : [new TextEncoder().encode(text)];
  download("translated.vi.provenance.json", parts);
});

// ---- Mod ----
function modEditsJson() {
  const obj = $("mod-object").value.trim();
  if (!obj || isNaN(parseInt(obj, 10))) throw new Error("Hãy nhập path_id TextAsset (số).");
  const oldS = $("mod-old").value;
  const newS = $("mod-new").value;
  if (!oldS) throw new Error("Hãy nhập chuỗi cũ.");
  return JSON.stringify([{
    object: parseInt(obj, 10),
    old: oldS, new: newS,
    entry: $("mod-entry").value.trim() || null,
    mode: $("mod-mode").value,
  }]);
}
function modResultHtml(parsed) {
  let html = "<p>Kết quả: <b>" + (parsed.dry_run ? "dry-run (CHƯA ghi file nào)" : "ĐÃ GHI") +
    "</b> · " + parsed.patched + " edit · file gốc không đổi (sha256 " +
    esc((parsed.source_sha256 || "").slice(0, 12)) + "…)</p><table><tr><th>Đích</th><th>Chế độ</th><th>Tìm thấy</th><th>Chuỗi (byte)</th><th>Đơn vị (byte)</th><th>Verify</th></tr>";
  for (const e of parsed.edits || []) {
    html += "<tr><td>" + esc(e.target) + "</td><td>" + esc(e.write_mode) + "</td><td>" + e.occurrences +
      "</td><td>" + e.old_script_len + " → " + e.new_script_len + "</td><td>" + e.size_before +
      " → " + e.size_after + "</td><td>" + (e.verified ? "OK" : "LỖI") + "</td></tr>";
    if ((e.warnings || []).length) html += "<tr><td colspan=6>Cảnh báo: " + esc(e.warnings.join("; ")) + "</td></tr>";
    if ((e.verify || []).length) html += "<tr><td colspan=6>" + esc(e.verify.join("; ")) + "</td></tr>";
  }
  html += "</table>";
  if ((parsed.outputs || []).length) html += "<p>File đã ghi, đã sẵn sàng để lưu: <b>" + esc(parsed.outputs.join(", ")) + "</b> — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>";
  html += "<p class=note>" + esc(parsed.note || "") + "</p>";
  return html;
}
async function runMod(allowWrite) {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  let edits;
  try { edits = modEditsJson(); } catch (e) { showError(e.message); return; }
  if (allowWrite && !confirm("Ghi đơn vị đã patch ra file tải về? File gốc KHÔNG bị ghi đè; output chỉ là bundle/.assets, không phải IPA đóng gói lại.")) return;
  try {
    const label = allowWrite ? "Mod (ghi file)" : "Mod thử (dry-run)";
    const { parsed } = await runOp(label, "mod_patch", [inputPath(), edits, allowWrite], inputFileArgs(), allowWrite);
    $("mod-result").innerHTML = modResultHtml(parsed);
    // Mục C7: giữ lại edits + file của lần Mod vừa chạy để xuất patch pack.
    state.lastMod = { editsJson: edits, fileKey: recentKeyOf(state.file) };
    const ppb = $("btn-mod-patch-pack"); if (ppb) ppb.disabled = false;
  } catch (e) { /* đã báo lỗi */ }
}
$("btn-mod-dryrun").addEventListener("click", () => runMod(false));
$("btn-mod-write").addEventListener("click", () => runMod(true));
// Mục C7: xuất patch pack (.zip: manifest JSON + script Python áp patch)
// từ lần Mod vừa chạy thử xong. Pack xây lại từ edits (không dùng kết
// quả dry-run cũ) nên luôn khớp file hiện tại; đổi file thì phải chạy
// Mod thử lại.
$("btn-mod-patch-pack").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (!state.lastMod || state.lastMod.fileKey !== recentKeyOf(state.file)) {
    showError("Chưa có kết quả Mod thử cho file này — bấm “Patch thử (dry-run)” trước rồi mới xuất pack.");
    return;
  }
  try {
    const { parsed } = await runOp("Xuất patch pack", "patch_pack",
      [inputPath(), "mod", state.lastMod.editsJson], inputFileArgs(), true);
    $("mod-result").innerHTML += "<p>Patch pack <b>" + esc(parsed.pack_id) +
      "</b>: " + parsed.entries + " thay đổi · file <b>" + esc(parsed.filename) +
      "</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy. " +
      esc(parsed.note || "") + "</p>";
  } catch (e) { /* đã báo lỗi */ }
});
// Thẻ "Thay Text" (thao tác Mod thật duy nhất trên web): cuộn tới ô nhập
// chuỗi cũ. Các thẻ còn lại là nút disabled, không có handler.
$("btn-mod-op-text").addEventListener("click", () => {
  // Form "Thay 1 chuỗi" nằm trong mục "Nâng cao" (details, mặc định đóng):
  // mở ra trước rồi mới cuộn tới ô nhập, không thì scrollIntoView không thấy gì.
  const adv = $("mod-advanced");
  if (adv && !adv.open) adv.open = true;
  const m = $("mod-old");
  if (m) {
    try { if (typeof m.scrollIntoView === "function") m.scrollIntoView({ block: "center" }); } catch (e) { /* cuộn chỉ là phụ */ }
    try { if (typeof m.focus === "function") m.focus(); } catch (e) { /* focus chỉ là phụ */ }
  }
});

// ---- Mod: tìm theo chữ + nhập file .json (phần B) ----
// Kiến trúc web (không port nguyên si cách làm ngoài): toàn bộ tìm/sửa
// chạy ở MAIN THREAD trên object JSON đã nằm trong bộ nhớ phiên — KHÔNG
// qua worker/Pyodide, KHÔNG đọc lại file game. Lý do: file JSON trích
// xuất/dịch/nhập đã là text phẳng trong RAM (kể cả file lớn nhất thật:
// resources.monobehaviour.en.json của EK2 ~15k entry vẫn nhẹ); đưa qua
// worker chỉ thêm chuyển buffer + boot Pyodide mà không thêm gì. Chỉ
// bước NẠP file trích xuất mới qua worker (read_extracted — file đó nằm
// trong FS worker sau extract). Sửa xong thì đưa vào ô Bản dịch của plan
// Mod (mục 8) hoặc tải file .json đã sửa — plan/patch pack (C7) vẫn đi
// đường cũ đã kiểm chứng, không viết lại.
// state.modJson = {name, entries, keys, provenance, origin} — entries là
// object phẳng {key: value} giữ đúng thứ tự key gốc (không sort_keys,
// giống file en.json mẫu của Joseph, phần A); value giữ nguyên kiểu
// (string/number/bool/null), chỉ ép sang string khi tìm/hiển thị.
state.modJson = null;
state.modSearchResults = []; // [{key, value}] của lần tìm gần nhất
state.modEdited = new Set(); // key đã sửa trong phiên này
const MOD_SEARCH_SHOW_MAX = 200; // chỉ hiện 200 kết quả đầu, vẫn đếm đủ

// Parse JSON phẳng {key: value} (không nhận mảng / kiểu khác / JSON hỏng).
// Trả {entries, keys} — keys giữ đúng thứ tự gốc trong file.
function modParseJsonObject(text) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) { throw new Error("File .json không hợp lệ: " + e.message); }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    throw new Error("File .json phải là object phẳng {key: value}, không phải mảng hay giá trị đơn.");
  }
  return { entries: obj, keys: Object.keys(obj) };
}

// Tìm chuỗi con trong key và/hoặc value của object phẳng.
// Mặc định KHÔNG phân biệt hoa/thường (query/key/value đều toLowerCase).
// scope: "all" | "key" | "value". Trả [{key, value}] đủ mọi kết quả
// (không cắt ở đây — UI chỉ hiện 200 đầu nhưng đếm đủ tổng).
function modSearchEntries(entries, query, opts) {
  const o = opts || {};
  const q = o.caseSensitive ? String(query) : String(query).toLowerCase();
  if (!q) return [];
  const scope = o.scope || "all";
  const out = [];
  for (const key of Object.keys(entries || {})) {
    const val = entries[key];
    const valStr = val == null ? "" : String(val);
    const k = o.caseSensitive ? key : key.toLowerCase();
    const v = o.caseSensitive ? valStr : valStr.toLowerCase();
    const inKey = k.indexOf(q) !== -1;
    const inVal = v.indexOf(q) !== -1;
    if ((scope === "key" && inKey) || (scope === "value" && inVal) ||
        (scope === "all" && (inKey || inVal))) {
      out.push({ key, value: valStr });
    }
  }
  return out;
}

// Đọc provenance từ file sidecar beartea-translation-provenance/1 (mục A).
// Trả object provenance, hoặc null nếu không phải sidecar hợp lệ.
function modProvenanceFromSidecar(text) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) { return null; }
  if (!obj || obj.format !== "beartea-translation-provenance/1" ||
      !obj.provenance || typeof obj.provenance !== "object") return null;
  return obj.provenance;
}

// Nguồn gốc ngắn để hiện trong bảng kết quả: lấy đoạn cuối của source
// (vd ".../resources.assets" -> "resources.assets").
function modShortSource(prov) {
  const s = (prov && (prov.source || prov.extractName)) || "";
  if (!s) return "—";
  const parts = s.split(/::|\//).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : s;
}
function modJsonInfoText() {
  const m = state.modJson;
  if (!m) return "Chưa mở file JSON nào — lấy từ file trích xuất, bản dịch, hoặc nhập file .json từ máy.";
  const prov = m.provenance;
  const src = prov
    ? (prov.origin === "extract" ? "trích xuất từ " + (prov.source || prov.extractName || "?")
      : prov.origin === "upload" ? "file tải lên" + (prov.extractName ? " (" + prov.extractName + ")" : "") + " — không rõ nguồn gốc (không có provenance đi kèm)"
      : prov.origin === "manual" ? "dán tay / bản dịch trong phiên — không rõ nguồn gốc"
      : (prov.source || prov.extractName || "có provenance"))
    : "không rõ nguồn gốc";
  return "Đã mở " + m.name + " · " + m.keys.length + " entry · Nguồn: " + src +
    (state.modEdited.size ? " · đã sửa " + state.modEdited.size + " entry" : "") +
    (m.keys.length > 50000 ? " · file rất lớn, tìm kiếm vẫn quét đủ mọi entry nhưng chỉ hiện 200 kết quả đầu" : "");
}
function modSetJson(name, entries, keys, provenance) {
  state.modJson = { name, entries, keys: keys || Object.keys(entries), provenance: provenance || null };
  state.modSearchResults = [];
  state.modEdited = new Set();
  $("mod-json-info").textContent = modJsonInfoText();
  $("mod-search-count").textContent = "";
  $("mod-search-results").innerHTML = "";
  for (const id of ["btn-mod-to-plan", "btn-mod-download-json"]) {
    const b = $(id); if (b) b.disabled = false;
  }
  clearError();
}
function refreshModJsonSourceSelect() {
  const sel = $("mod-json-source");
  if (!sel) return;
  const files = (state.lastExtractFiles || []).filter((f) => f.exported && /\.json$/i.test(f.exported));
  if (!files.length) {
    sel.innerHTML = "<option value=\"\">— chưa có file trích xuất —</option>";
    return;
  }
  let html = "";
  for (const f of files) {
    html += "<option value=\"" + esc(f.exported) + "\">" + esc(f.exported) +
      (f.source ? " ← " + esc(f.source) : "") + "</option>";
  }
  sel.innerHTML = html;
  const def = defaultTranslateFile();
  if (def) sel.value = def;
}
function modRenderSearchResults() {
  const box = $("mod-search-results");
  const res = state.modSearchResults || [];
  if (!res.length) { box.innerHTML = ""; return; }
  const prov = state.modJson && state.modJson.provenance;
  const src = modShortSource(prov);
  // Thẻ xếp dọc (không phải bảng): vừa 390px, không cuộn ngang, nút
  // "Lưu" không bị bóp thành chữ xếp dọc. Mỗi thẻ: key + value + nguồn.
  let html = "";
  for (const r of res.slice(0, MOD_SEARCH_SHOW_MAX)) {
    const edited = state.modEdited.has(r.key);
    html += "<div class=\"mod-result-item\"><div><b>" + esc(r.key) + "</b>" +
      (edited ? " <b>(đã sửa)</b>" : "") +
      " <span class=\"sub\">· nguồn: " + esc(src) + "</span></div>" +
      "<div>Giá trị hiện tại: " + esc(r.value) + "</div>" +
      "<label>Sửa thành: <input type=\"text\" data-mod-edit=\"" + esc(r.key) +
      "\" value=\"" + esc(r.value) + "\"></label> " +
      "<button class=\"secondary\" data-mod-save=\"" + esc(r.key) + "\">Lưu</button></div>";
  }
  if (res.length > MOD_SEARCH_SHOW_MAX) {
    html += "<p class=\"note\">Chỉ hiện " + MOD_SEARCH_SHOW_MAX + " kết quả đầu trong tổng " +
      res.length + " — gõ chữ cụ thể hơn để thu hẹp.</p>";
  }
  box.innerHTML = html;
}
function modRunSearch() {
  if (!state.modJson) { showError("Chưa mở file JSON nào — lấy từ file trích xuất, bản dịch, hoặc nhập file .json từ máy rồi tìm."); return; }
  const q = ($("mod-search").value || "").trim();
  if (!q) { showError("Hãy nhập chữ cần tìm (vd: setting)."); return; }
  const scope = ($("mod-search-scope") && $("mod-search-scope").value) || "all";
  const caseSensitive = !!($("mod-search-case") && $("mod-search-case").checked);
  const res = modSearchEntries(state.modJson.entries, q, { scope, caseSensitive });
  state.modSearchResults = res;
  $("mod-search-count").textContent = res.length
    ? "Tìm thấy " + res.length + " kết quả cho “" + q + "” trong " + state.modJson.keys.length +
      " entry" + (caseSensitive ? " (phân biệt hoa/thường)" : " (không phân biệt hoa/thường)") +
      " — sửa trực tiếp trong cột “Sửa thành” rồi bấm Lưu từng dòng."
    : "Không tìm thấy “" + q + "” trong " + state.modJson.keys.length + " entry" +
      (scope === "key" ? " (chỉ tìm trong key)" : scope === "value" ? " (chỉ tìm trong value)" : "") + ".";
  modRenderSearchResults();
  clearError();
}
$("btn-mod-search").addEventListener("click", modRunSearch);
$("mod-search").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); modRunSearch(); }
});
// Lưu 1 entry đã sửa ngay trong danh sách kết quả (không cần nhớ id).
$("mod-search-results").addEventListener("click", (ev) => {
  const b = ev.target && ev.target.closest ? ev.target.closest("[data-mod-save]") : null;
  if (!b || typeof b.getAttribute !== "function" || !state.modJson) return;
  const key = b.getAttribute("data-mod-save");
  const input = $("mod-search-results").querySelector("[data-mod-edit=\"" + CSS.escape(key) + "\"]");
  if (!input) return;
  const newVal = input.value;
  state.modJson.entries[key] = newVal;
  state.modEdited.add(key);
  const r = (state.modSearchResults || []).find((x) => x.key === key);
  if (r) r.value = newVal;
  $("mod-json-info").textContent = modJsonInfoText();
  modRenderSearchResults();
  clearError();
});
// Lấy file JSON từ đợt trích xuất vừa rồi (qua worker read_extracted —
// nội dung file nằm trong FS worker sau extract; provenance giữ nguyên
// từ lúc trích xuất, giống tab Dịch).
$("btn-mod-load-extract").addEventListener("click", async () => {
  refreshModJsonSourceSelect();
  const sel = $("mod-json-source");
  const name = sel && sel.value;
  if (!name) { showError("Chưa có file trích xuất — chạy Trích xuất ở tab Trích xuất trước, hoặc nhập file .json từ máy."); return; }
  try {
    const msg = await callWorker("run", { fn: "read_extracted", args: [name], files: [] });
    const parsed = JSON.parse(msg.result);
    if (!parsed || parsed.ok !== true) {
      showError("Không nạp được " + name + ": " + ((parsed && (parsed.error || parsed.code)) || "lỗi không rõ") + " — hãy nhập file .json từ máy.");
      return;
    }
    const { entries, keys } = modParseJsonObject(parsed.text || "");
    const entry = (state.lastExtractFiles || []).find((f) => f.exported === name);
    const prov = translateProvenanceFor(name, (entry && entry.locale) || null, null, keys.length);
    modSetJson(name, entries, keys, prov);
  } catch (e) { showError("Không nạp được " + name + " (" + e.message + ") — hãy nhập file .json từ máy."); }
});
// Lấy bản dịch xong đang giữ trong phiên (tab Dịch, mục A) kèm provenance.
$("btn-mod-load-translate").addEventListener("click", () => {
  const lt = state.lastTranslated;
  if (!lt || !lt.translated || typeof lt.translated !== "object") {
    showError("Chưa có bản dịch xong trong phiên — dịch xong ở tab Dịch rồi bấm lại, hoặc nhập file .json từ máy.");
    return;
  }
  modSetJson(lt.name || "translated.vi.json", lt.translated, lt.keys || Object.keys(lt.translated), lt.provenance || null);
});
// Nhập file .json từ máy (chọn được NHIỀU file cùng lúc): file .json
// chính + file sidecar .provenance.json (nếu chọn kèm) thì giữ provenance;
// không có sidecar thì ghi rõ "không rõ nguồn gốc", không bịa nguồn.
$("btn-mod-import").addEventListener("click", () => { $("mod-json-file").click(); });
$("mod-json-file").addEventListener("change", async (ev) => {
  const files = [...(ev.target.files || [])];
  ev.target.value = "";
  if (!files.length) return;
  const sidecars = files.filter((f) => /\.provenance\.json$/i.test(f.name));
  const mains = files.filter((f) => !/\.provenance\.json$/i.test(f.name));
  if (!mains.length) { showError("Chưa chọn file .json nào — file .provenance.json chỉ là sidecar, cần chọn kèm 1 file .json chính."); return; }
  if (mains.length > 1) {
    // Chỉ mở file đầu tiên; báo rõ để user không tưởng đã mở hết.
    showError("Đã chọn " + mains.length + " file .json — chỉ mở file đầu tiên (" + mains[0].name + "); mở từng file một nếu cần sửa nhiều file.");
  }
  const main = mains[0];
  try {
    const text = await main.text();
    if (text.length > 20 * 1024 * 1024) {
      if (!confirm("File " + main.name + " lớn (" + fmtBytes(text.length) + "). Mở file này giữ toàn bộ JSON trong RAM của tab — trên iPhone có thể nặng. Bấm OK để tiếp tục, hoặc Huỷ để chia nhỏ file trước.")) return;
    }
    const { entries, keys } = modParseJsonObject(text);
    let prov = null;
    // Sidecar đi kèm: khớp theo tên (vd translated.vi.provenance.json
    // đi với translated.vi.json) hoặc lấy sidecar duy nhất đã chọn.
    const base = main.name.replace(/\.json$/i, "");
    const match = sidecars.find((f) => f.name.replace(/\.provenance\.json$/i, "") === base) || sidecars[0];
    if (match) prov = modProvenanceFromSidecar(await match.text());
    else {
      // Không có sidecar: chỉ biết tên file — origin "upload", giống
      // tab Dịch khi tải file JSON lên (mục A). Không bịa source.
      prov = {
        origin: "upload", gameFile: state.file ? state.file.name : null,
        extractName: main.name, source: null, locale: null,
        from: null, to: null, keys: keys.length, at: new Date().toISOString(),
      };
    }
    modSetJson(main.name, entries, keys, prov);
  } catch (e) { showError("Nhập file .json lỗi: " + e.message); }
});
// Đưa toàn bộ JSON đang mở (kèm các entry đã sửa) vào ô Bản dịch của
// plan Mod (mục 8) để lập kế hoạch — giữ đúng thứ tự key gốc, indent 1
// như file en.json mẫu.
$("btn-mod-to-plan").addEventListener("click", () => {
  if (!state.modJson) { showError("Chưa mở file JSON nào."); return; }
  $("plan-translations").value = JSON.stringify(state.modJson.entries, null, 1);
  $("mod-search-count").textContent = "Đã đưa " + state.modJson.keys.length + " entry" +
    (state.modEdited.size ? " (gồm " + state.modEdited.size + " entry đã sửa)" : "") +
    " vào ô Bản dịch bên dưới — bấm “Lập kế hoạch” để lập plan Mod.";
  clearError();
});
// Tải file .json đã sửa về (tên gốc + hậu tố .mod, giữ thứ tự key gốc).
$("btn-mod-download-json").addEventListener("click", () => {
  if (!state.modJson) { showError("Chưa mở file JSON nào."); return; }
  const name = state.modJson.name.replace(/\.json$/i, "") + ".mod.json";
  download(name, [new TextEncoder().encode(JSON.stringify(state.modJson.entries, null, 1))]);
});
refreshModJsonSourceSelect();

// ---- Kế hoạch Mod (plan JSON — mục 8) ----
// Planner (bridge.plan_build) quét bundle và lập plan; executor
// (bridge.plan_apply) áp plan; bridge.plan_undo hoàn tác theo before_sha.
// Entry = 1 TextAsset, độc lập theo asset. Plan + checkpoint lưu vào
// localStorage (tải lại trang vẫn mở lại được — bài học từ
// translation_apply của studio: bản dịch chỉ trong bộ nhớ trang thì tải
// lại là mất). Backup đơn vị gốc nằm trong FS worker (/tmp/plan_backup/)
// nên chỉ tồn tại trong bộ nhớ trang — tải lại trang thì undo phải dùng
// patch ngược (bridge tự thử khi không còn backup).
const PLAN_LS_KEY = "pb3_plan_json";
const PLAN_CKPT_LS_KEY = "pb3_plan_checkpoint";
state.plan = null;            // plan JSON đang mở
state.lastMod = null;         // {editsJson, fileKey} của lần Mod vừa chạy (mục C7)
state.planSelected = null;    // Set(id entry) đã tick; null = chưa có plan
state.planCheckpoint = null;  // checkpoint {plan_id, completed: [id]}
function planSaveLs() {
  try {
    if (state.plan) localStorage.setItem(PLAN_LS_KEY, JSON.stringify(state.plan));
    else localStorage.removeItem(PLAN_LS_KEY);
    if (state.planCheckpoint) localStorage.setItem(PLAN_CKPT_LS_KEY, JSON.stringify(state.planCheckpoint));
    else localStorage.removeItem(PLAN_CKPT_LS_KEY);
  } catch (e) { /* localStorage không có: plan không giữ được qua lần tải lại trang */ }
}
function planLoadLs() {
  try {
    const t = localStorage.getItem(PLAN_LS_KEY);
    if (!t) return;
    const p = JSON.parse(t);
    if (p && p.version === 1 && Array.isArray(p.entries) && p.entries.length) {
      state.plan = p;
      state.planSelected = new Set(p.entries.filter((e) => e.selected !== false).map((e) => e.id));
      const c = localStorage.getItem(PLAN_CKPT_LS_KEY);
      if (c) { try { state.planCheckpoint = JSON.parse(c); } catch (e) { state.planCheckpoint = null; } }
    }
  } catch (e) { /* plan lưu hỏng: bỏ qua, không crash */ }
}
function planEntryDiffHtml(e) {
  const rows = (e.changes || []).slice(0, 3).map((c) =>
    "<tr><td>" + esc(c.key) + "</td><td>" + esc(c.before) + "</td><td>→</td><td>" +
    esc(c.after) + "</td></tr>").join("");
  const more = (e.changes || []).length > 3
    ? "<tr><td colspan=4>…còn " + ((e.changes || []).length - 3) + " thay đổi nữa</td></tr>" : "";
  return "<table><tr><th>Key</th><th>Trước</th><th></th><th>Sau</th></tr>" + rows + more + "</table>";
}
function renderPlanList() {
  const box = $("plan-list");
  const has = !!(state.plan && state.plan.entries && state.plan.entries.length);
  for (const id of ["btn-plan-select-all", "btn-plan-deselect-all", "btn-plan-download",
                    "btn-plan-patch-pack",
                    "btn-plan-dryrun", "btn-plan-apply", "btn-plan-undo"]) {
    const b = $(id); if (b) b.disabled = !has;
  }
  const rb = $("btn-plan-resume");
  if (rb) rb.disabled = !(has && state.planCheckpoint && state.planCheckpoint.plan_id === state.plan.id &&
    (state.planCheckpoint.completed || []).length > 0 &&
    (state.planCheckpoint.completed || []).length < state.plan.entries.length);
  if (!box) return;
  if (!has) { box.innerHTML = "<p class=\"note\">Chưa có plan — nhập bản dịch rồi bấm “Lập kế hoạch”.</p>"; return; }
  let html = "<p class=\"note\">Plan <b>" + esc(state.plan.id) + "</b> · từ <b>" +
    esc(state.plan.source_bundle || "") + "</b> · " + state.plan.entries.length +
    " entry · tick để chọn entry áp (mặc định tick tất cả)" +
    (state.plan.rebuild_all ? " · <b>REBUILD_ALL: áp toàn bộ, bỏ qua tick</b>" : "") +
    "</p><table><tr><th></th><th>Asset</th><th>Thay đổi</th><th>Diff trước → sau</th><th>SHA</th></tr>";
  for (const e of state.plan.entries) {
    const checked = !state.planSelected || state.planSelected.has(e.id);
    html += "<tr><td><input type=\"checkbox\" data-plan-id=\"" + esc(e.id) + "\"" +
      (checked ? " checked" : "") + "></td><td>" + esc(e.asset_path) +
      "<br><span class=\"sub\">" + esc(e.op) + " · path_id " + e.object + "</span></td><td>" +
      (e.changes || []).length + "</td><td>" + planEntryDiffHtml(e) + "</td><td class=\"sub\">" +
      esc((e.before_sha || "").slice(0, 8)) + " →<br>" + esc((e.after_sha || "").slice(0, 8)) + "</td></tr>";
    if ((e.warnings || []).length) html += "<tr><td colspan=5>Cảnh báo: " + esc(e.warnings.join("; ")) + "</td></tr>";
  }
  html += "</table>";
  if ((state.plan.skipped || []).length) {
    html += "<p class=\"note\">Đã bỏ qua " + state.plan.skipped.length + " asset: " +
      esc(state.plan.skipped.map((s) => s.asset_path + " (" + s.reason + ")").join("; ")) + "</p>";
  }
  box.innerHTML = html;
}
function planSyncSelectedToPlan() {
  if (!state.plan) return;
  for (const e of state.plan.entries) e.selected = !state.planSelected || state.planSelected.has(e.id);
  state.plan.rebuild_all = !!($("plan-rebuild-all") && $("plan-rebuild-all").checked);
}
function planApplyResultHtml(parsed) {
  let html = "<p>Kết quả: <b>" + (parsed.dry_run ? "dry-run (CHƯA ghi file nào)" :
    (parsed.status === "partial" ? "CHẠY DỞ (partial — còn checkpoint để tiếp tục)" : "ĐÃ GHI")) +
    "</b> · " + (parsed.applied || []).length + " entry đã áp · " +
    (parsed.errors || []).length + " lỗi</p>";
  if ((parsed.applied || []).length) {
    html += "<table><tr><th>Asset</th><th>Thay đổi</th><th>Chế độ</th><th>Verify</th></tr>";
    for (const a of parsed.applied) html += "<tr><td>" + esc(a.asset_path) + "</td><td>" + a.changes +
      "</td><td>" + esc(a.write_mode || "") + "</td><td>" + (a.verified ? "OK" : "LỖI") + "</td></tr>";
    html += "</table>";
  }
  if ((parsed.skipped || []).length) html += "<p class=\"note\">Bỏ qua: " +
    esc(parsed.skipped.map((s) => s.asset_path + " (" + s.reason + ")").join("; ")) + "</p>";
  if ((parsed.errors || []).length) html += "<p>Lỗi: " + esc(parsed.errors.map((e) =>
    "[" + (e.code || "") + "] " + e.asset_path + ": " + e.message).join("; ")) + "</p>";
  if ((parsed.outputs || []).length) html += "<p>File đã ghi, đã sẵn sàng để lưu: <b>" + esc(parsed.outputs.join(", ")) + "</b> — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>";
  if (parsed.checkpoint) html += "<p class=\"sub\">Checkpoint: " + (parsed.checkpoint.completed || []).length +
    " entry xong" + (parsed.remaining && parsed.remaining.length ? " · còn " + parsed.remaining.length + " entry chưa xong — bấm “Tiếp tục từ checkpoint”" : "") + "</p>";
  html += "<p class=note>" + esc(parsed.note || "") + "</p>";
  return html;
}
$("plan-list").addEventListener("change", (ev) => {
  const cb = ev.target;
  if (!cb || cb.type !== "checkbox" || typeof cb.getAttribute !== "function") return;
  const id = cb.getAttribute("data-plan-id");
  if (!id) return;
  if (!state.planSelected) state.planSelected = new Set();
  if (cb.checked) state.planSelected.add(id);
  else state.planSelected.delete(id);
  planSyncSelectedToPlan();
  planSaveLs();
  renderPlanList();
});
$("btn-plan-select-all").addEventListener("click", () => {
  if (!state.plan) return;
  state.planSelected = new Set(state.plan.entries.map((e) => e.id));
  planSyncSelectedToPlan(); planSaveLs(); renderPlanList();
});
$("btn-plan-deselect-all").addEventListener("click", () => {
  if (!state.plan) return;
  state.planSelected = new Set();
  planSyncSelectedToPlan(); planSaveLs(); renderPlanList();
});
$("btn-plan-load-translate").addEventListener("click", () => {
  const t = $("translate-output") && $("translate-output").value.trim();
  if (!t) { showError("Tab Dịch chưa có bản dịch — dịch xong ở tab Dịch rồi bấm lại."); return; }
  $("plan-translations").value = t;
  clearError();
});
$("btn-plan-build").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  const text = $("plan-translations").value.trim();
  if (!text) { showError("Chưa có bản dịch — nhập JSON key → giá trị mới, hoặc bấm “Lấy từ tab Dịch”."); return; }
  try { JSON.parse(text); } catch (e) { showError("Bản dịch không phải JSON hợp lệ: " + e.message); return; }
  const assets = $("plan-assets").value.trim();
  try {
    const { parsed } = await runOp("Lập kế hoạch", "plan_build",
      [inputPath(), text, $("plan-entry").value.trim() || null, "REBUILD_SECTION",
       !!($("plan-rebuild-all") && $("plan-rebuild-all").checked),
       assets ? JSON.stringify(assets.split(",").map((s) => s.trim()).filter(Boolean)) : null],
      inputFileArgs());
    state.plan = parsed.plan;
    state.planSelected = new Set(state.plan.entries.filter((e) => e.selected !== false).map((e) => e.id));
    state.planCheckpoint = null;
    planSaveLs();
    renderPlanList();
    $("plan-result").innerHTML = "<p>Kế hoạch <b>" + esc(state.plan.id) + "</b>: " +
      state.plan.entries.length + " entry · " + (state.plan.skipped || []).length +
      " asset bỏ qua (xem dưới bảng).</p>";
  } catch (e) { /* đã báo lỗi */ }
});
async function runPlanApply(dryRun, checkpointJson) {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (!state.plan) { showError("Chưa có plan — lập kế hoạch trước."); return; }
  planSyncSelectedToPlan();
  if (!dryRun && !state.plan.rebuild_all &&
      (!state.planSelected || state.planSelected.size === 0)) {
    showError("Chưa chọn entry nào để áp — tick ít nhất 1 entry, hoặc tick “Áp dụng toàn bộ (REBUILD_ALL)” để áp hết.");
    return;
  }
  if (!dryRun && !confirm("Áp plan lên file đã chọn? File gốc KHÔNG bị ghi đè; output chỉ là bundle/.assets, không phải IPA đóng gói lại.")) return;
  try {
    const label = dryRun ? "Xem trước plan (dry-run)" : "Áp dụng plan";
    const { parsed } = await runOp(label, "plan_apply",
      [JSON.stringify(state.plan), inputPath(), dryRun, checkpointJson || null, null],
      inputFileArgs(), !dryRun);
    state.planCheckpoint = parsed.checkpoint || null;
    if (parsed.status === "done") state.planCheckpoint = null;
    planSaveLs();
    renderPlanList();
    $("plan-result").innerHTML = planApplyResultHtml(parsed);
  } catch (e) { /* đã báo lỗi */ }
}
$("btn-plan-dryrun").addEventListener("click", () => runPlanApply(true, null));
$("btn-plan-apply").addEventListener("click", () => runPlanApply(false, null));
$("btn-plan-resume").addEventListener("click", () => {
  if (!state.planCheckpoint) { showError("Không có checkpoint để tiếp tục."); return; }
  runPlanApply(false, JSON.stringify(state.planCheckpoint));
});
$("btn-plan-undo").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (!state.plan) { showError("Chưa có plan — không biết hoàn tác về đâu."); return; }
  if (!confirm("Hoàn tác plan (khôi phục đơn vị về đúng trước khi áp)? Cần file đầu vào là file ĐÃ ÁP plan, hoặc bản sao lưu còn trong phiên trang.")) return;
  try {
    const { parsed } = await runOp("Hoàn tác plan", "plan_undo",
      [JSON.stringify(state.plan), inputPath()], inputFileArgs(), true);
    let html = "<p>Hoàn tác: <b>" + (parsed.ok ? "XONG" : "CÓ LỖI") + "</b> · " +
      (parsed.restored || []).length + " entry đã về trước khi áp</p>";
    if ((parsed.restored || []).length) {
      html += "<table><tr><th>Asset</th><th>Cách khôi phục</th><th>Verify</th></tr>";
      for (const r of parsed.restored) html += "<tr><td>" + esc(r.asset_path) + "</td><td>" +
        esc(r.how === "backup" ? "bản sao lưu" : "patch ngược") + "</td><td>" +
        (r.verified ? "OK (đúng before_sha)" : "LỖI") + "</td></tr>";
      html += "</table>";
    }
    if ((parsed.errors || []).length) html += "<p>Lỗi: " + esc(parsed.errors.map((e) =>
      "[" + (e.code || "") + "] " + e.asset_path + ": " + e.message).join("; ")) + "</p>";
    if ((parsed.outputs || []).length) html += "<p>File đã khôi phục, đã sẵn sàng để lưu: <b>" + esc(parsed.outputs.join(", ")) + "</b> — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>";
    html += "<p class=note>" + esc(parsed.note || "") + "</p>";
    $("plan-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-plan-download").addEventListener("click", () => {
  if (!state.plan) { showError("Chưa có plan để tải."); return; }
  planSyncSelectedToPlan();
  download("plan-" + state.plan.id + ".json",
    [new TextEncoder().encode(JSON.stringify(state.plan, null, 2))]);
});
// Mục C7: xuất patch pack (.zip: manifest JSON + script Python áp patch)
// từ plan đang mở (chỉ entry được tick, trừ khi REBUILD_ALL ở tầng plan).
$("btn-plan-patch-pack").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (!state.plan) { showError("Chưa có plan — lập kế hoạch trước."); return; }
  planSyncSelectedToPlan();
  try {
    const { parsed } = await runOp("Xuất patch pack", "patch_pack",
      [inputPath(), "plan", JSON.stringify(state.plan)], inputFileArgs(), true);
    $("plan-result").innerHTML = "<p>Patch pack <b>" + esc(parsed.pack_id) +
      "</b>: " + parsed.entries + " thay đổi · file <b>" + esc(parsed.filename) +
      "</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy. " +
      esc(parsed.note || "") + "</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-plan-import").addEventListener("click", () => { $("plan-file").click(); });
$("plan-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  try {
    const p = JSON.parse(await f.text());
    if (!p || p.version !== 1 || !Array.isArray(p.entries) || !p.entries.length) throw new Error("plan phải có version 1 và ít nhất 1 entry.");
    state.plan = p;
    state.planSelected = new Set(p.entries.filter((e) => e.selected !== false).map((e) => e.id));
    state.planCheckpoint = null;
    planSaveLs();
    renderPlanList();
    $("plan-result").innerHTML = "<p>Đã mở plan <b>" + esc(p.id) + "</b>: " + p.entries.length + " entry.</p>";
  } catch (e) { showError("Mở plan lỗi: " + e.message); }
  ev.target.value = "";
});
// Khôi phục plan đã lưu (localStorage) khi tải trang.
planLoadLs();
renderPlanList();

// ---- Ép lại vào file gốc (E) — P0-1, bước 5 ----
// Nguồn bản dịch 3 lựa chọn: bản dịch xong trong phiên (lastTranslated),
// file JSON đang mở ở tab Mod (modJson), hoặc nhập file .json + sidecar
// .provenance.json từ máy. Preview và ghi là cùng 1 op bridge
// rebuild_apply với cờ dry_run (tiền lệ plan_apply/mod_patch); nút ghi
// disabled tới khi preview xong; ghi cần confirm() + confirm riêng cho
// file >140MB. Mọi dữ liệu từ file game/bản dịch đều qua esc() khi vẽ
// HTML (luật textContent/esc của brief — 2 lỗ innerHTML ở dropdown
// nguồn đã vá ở P0-1, xem refreshTranslateSourceSelect /
// refreshModJsonSourceSelect). File gốc KHÔNG bị ghi đè; output không
// phải IPA đóng gói lại; cần re-sign gói ngoài.
state.rebuild = null;           // {translations, provenance, preview, applied, fileKey}
state.rebuildFile = null;       // {translations, provenance, name} khi nhập file từ máy
function rebuildFreshState() {
  return { translations: null, provenance: null, preview: null,
           applied: false, fileKey: null };
}
function rebuildResetState() {
  // Reset khi đổi/bỏ file: chỉ hủy kết quả xem trước của file cũ
  // (preview/applied/fileKey + nút Ghi) — KHÔNG xóa bản dịch + sidecar đã
  // nhập (state.rebuildFile). Dữ liệu nhập là đầu vào của user, độc lập
  // với file game (2 nguồn còn lại lastTranslated/modJson cũng không bị
  // xóa khi đổi file); xóa nó làm luồng chọn lại file → cảnh báo khác tên
  // không bao giờ chạy được ở chế độ nhập file (lỗi D2). An toàn vẫn giữ:
  // preview cũ hết hạn, nút Ghi tắt tới khi xem trước lại, và rebuildRun
  // vẫn confirm khi tên file khác tên lúc trích xuất.
  state.rebuild = rebuildFreshState();
  const r = $("rebuild-result");
  if (r) r.innerHTML = "";
  const info = $("rebuild-source-info");
  // Chế độ nhập file + còn dữ liệu đã nhập: giữ nguyên dòng mô tả nguồn
  // (nó mô tả dữ liệu đã nhập, không mô tả file game); các trường hợp
  // còn lại về trạng thái trung lập như trước.
  const sel = $("rebuild-source");
  const keepInfo = info && sel && sel.value === "file" && state.rebuildFile;
  if (info && !keepInfo) info.textContent = "Chưa chọn nguồn bản dịch.";
  for (const id of ["btn-rebuild-apply", "btn-rebuild-reextract",
                    "btn-rebuild-choose-all-kept",
                    "btn-rebuild-choose-all-dropped"]) {
    const b = $(id); if (b) b.disabled = true;
  }
  refreshRebuildFileInfo();
  // P0-2: đổi/bỏ file thì verdict cổng cũ hết hạn (cổng gắn với đúng
  // file + bản dịch đã kiểm) — hủy verdict, nút Ghi tắt tới khi kiểm lại.
  if (typeof gateInvalidate === "function") gateInvalidate();
}
function rebuildFileKey() {
  return state.file ? (state.file.name + "|" + state.file.size + "|" +
    (state.file.lastModified || 0)) : null;
}
function rebuildExpectedGameFile(prov) {
  if (!prov) return null;
  if (prov.provenance && typeof prov.provenance === "object")
    return prov.provenance.gameFile || null;
  return prov.gameFile || prov.source || null;
}
// Provenance dùng để đối chiếu tên file: ưu tiên provenance của lần chạy
// (E) gần nhất; nếu chưa chạy lần nào sau khi đổi file thì dùng sidecar đã
// nhập ở chế độ nhập file — để cảnh báo khác tên hiện ngay khi chọn lại
// file, không phải đợi tới lúc bấm Xem trước.
function rebuildEffectiveProvenance() {
  if (state.rebuild && state.rebuild.provenance) return state.rebuild.provenance;
  const sel = $("rebuild-source");
  if (sel && sel.value === "file" && state.rebuildFile) return state.rebuildFile.provenance;
  return null;
}
function refreshRebuildFileInfo() {
  const el = $("rebuild-file-info");
  if (!el) return;
  const cur = state.file ? state.file.name : null;
  const prov = rebuildEffectiveProvenance();
  const expected = rebuildExpectedGameFile(prov);
  let text = cur ? ("File gốc đang chọn: " + cur + " (" + fmtBytes(state.file.size) + ").")
    : "Chưa chọn file gốc — chọn lại file game đã trích xuất ở trên.";
  if (expected && cur && expected !== cur)
    text += " CẢNH BÁO: tên khác lúc trích xuất (" + expected +
      ") — ép nhầm file sẽ bị từ chối ở bước kiểm tra, hoặc ép sai vị trí. Hãy chọn lại đúng file gốc.";
  else if (expected && cur)
    text += " Khớp tên file lúc trích xuất.";
  el.textContent = text;
}
function rebuildSourceData() {
  const sel = $("rebuild-source");
  const mode = sel ? sel.value : "session";
  if (mode === "session") {
    const lt = state.lastTranslated;
    if (!lt || !lt.translated) return { error: "Chưa có bản dịch xong trong phiên — dịch xong một file ở tab Dịch trước, hoặc chọn nguồn khác." };
    const prov = lt.provenance ? { format: "beartea-translation-provenance/1",
      translationFile: lt.name || "translated.vi.json",
      provenance: lt.provenance } : null;
    return { translations: lt.translated, provenance: prov,
             label: "bản dịch trong phiên (" + (lt.name || "") + ")" };
  }
  if (mode === "modjson") {
    const m = state.modJson;
    if (!m || !m.entries) return { error: "Chưa mở file JSON nào ở tab Mod — lấy từ file trích xuất, bản dịch, hoặc nhập file .json rồi quay lại." };
    return { translations: m.entries, provenance: m.provenance || null,
             label: "file JSON đang mở (" + m.name + ")" };
  }
  const rf = state.rebuildFile;
  if (!rf || !rf.translations) return { error: "Chưa nhập file .json nào — bấm “Nhập file .json…” (kèm sidecar .provenance.json nếu có)." };
  return { translations: rf.translations, provenance: rf.provenance || null,
           label: "file đã nhập (" + rf.name + ")" };
}
function rebuildErrorText(msg) {
  const m = String(msg || "");
  if (/E_ENCRYPTED/.test(m)) return "File/bundle MÃ HÓA — BearTea không giải mã, không vượt DRM/mã hóa. Dùng bản game không mã hóa (decrypted) hoặc file ngôn ngữ từ nguồn hợp lệ khác.";
  if (/v9|UnityFS v9/.test(m) && /E_UNSUPPORTED_FORMAT/.test(m)) return "Bundle UnityFS v9 CHỈ ĐỌC trên bản web — chưa ghi được bundle v9. Dùng bản iSH/desktop hoặc bundle phiên bản cũ hơn.";
  if (/E_UNSUPPORTED_FORMAT/.test(m)) return "Định dạng chưa hỗ trợ ghi (" + m + "). Thử Probe định dạng ở tab Chẩn đoán để xem engine/mức hỗ trợ thật.";
  if (/E_TOO_LARGE/.test(m)) return "File/entry QUÁ LỚN cho bản web (" + m + "). Dùng bản iSH cho file này — iSH đọc/ghi streaming với giới hạn cao hơn.";
  if (/E_VERIFY_FAILED/.test(m)) return "Kiểm tra sau khi ghi KHÔNG ĐẠT (before_sha lệch — file gốc có thể khác lúc lập kế hoạch, hoặc chuỗi gốc đã đổi). Không ghi file nào. Hãy chọn lại đúng file gốc rồi xem trước lại.";
  if (/E_INPUT_NOT_FOUND|không nhận diện được container|không phải bundle/.test(m)) return "Không mở được file — file có thể bị mã hóa/FairPlay, bị hỏng, hoặc không phải định dạng BearTea hỗ trợ (" + m + "). BearTea không khẳng định chắc chắn khi chưa có bằng chứng; thử Probe định dạng ở tab Chẩn đoán.";
  if (/Zstd/i.test(m)) return m + " Ghi Zstd chưa hỗ trợ — bundle sẽ ghi lại bằng block không nén nên file có thể to hơn.";
  return m;
}
async function rebuildConfirmLargeFile() {
  if (!state.file || state.file.size <= EXTRACT_WARN_BYTES) return true;
  return confirm("File lớn: " + state.file.name + " (" + fmtBytes(state.file.size) + ").\n" +
    "Ép lại đọc/ghi theo luồng (streaming) với bundle .unity3d độc lập lớn, nhưng RAM đỉnh ước tính ~" + fmtBytes(state.file.size * 4 + 250 * 1024 * 1024) +
    " (khoảng 4× dung lượng file + ~250MB nền — đo thật 2026-11-17); trên iPhone file trên ~140MB vẫn có thể làm chết tab.\n" +
    "Bấm OK để tiếp tục, hoặc Huỷ để dùng bản iSH cho file này.");
}
function rebuildConflictChoices() {
  const choices = {};
  const box = $("rebuild-result");
  if (!box) return choices;
  const radios = box.querySelectorAll("input[data-rebuild-conflict]");
  const seen = {};
  radios.forEach((r) => {
    const key = r.getAttribute("data-rebuild-conflict");
    if (r.checked) choices[key] = r.value;
    seen[key] = true;
  });
  return choices;
}
function rebuildRenderResult(parsed) {
  const t = parsed.totals || {};
  let html = "<p>Kết quả (E): <b>" + (parsed.dry_run ? "XEM TRƯỚC (dry-run — CHƯA ghi file nào)" : "ĐÃ GHI") +
    "</b> · " + (t.changed || 0) + "/" + (t.keys || 0) + " key đã đổi · " +
    (t.units || 0) + " đơn vị · " + (t.unmatched || 0) + " key không ép được · " +
    (t.unchanged || 0) + " key giữ nguyên · " + (t.refused || 0) + " đơn vị bị từ chối</p>";
  if ((parsed.warnings || []).length) html += "<p class=\"note\">Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>";
  if (parsed.scan_all) html += "<p class=\"note\">Không có provenance đầy đủ nên đã quét tất cả đơn vị để tìm key.</p>";
  if ((parsed.units || []).length) {
    html += "<table><tr><th>Đơn vị</th><th>Loại</th><th>Entry</th><th>Thay đổi</th><th>Chế độ</th><th>Kích thước</th></tr>";
    for (const u of parsed.units) {
      html += "<tr><td>" + esc(u.unit) + "</td><td>" + esc(u.kind) + "</td><td>" + u.entries +
        "</td><td>" + u.changes + "</td><td>" + esc(u.mode) + (u.streaming ? " · streaming" : "") +
        "</td><td>" + fmtBytes(u.size_before) + " → " + (u.size_after == null ? "?" : fmtBytes(u.size_after)) + "</td></tr>";
      if ((u.warnings || []).length) html += "<tr><td colspan=\"6\" class=\"sub\">Cảnh báo: " + esc(u.warnings.join("; ")) + "</td></tr>";
    }
    html += "</table>";
  }
  const unmatched = parsed.unmatched || [];
  if (unmatched.length) {
    const REASONS = { monobehaviour: "MonoBehaviour generic (scan path) / bảng ngôn ngữ CÓ type tree — chưa có đường ghi",
      not_found: "không tìm thấy trong đơn vị nào", not_json_dict: "TextAsset không phải JSON dạng dict",
      non_string_value: "giá trị gốc không phải chuỗi" };
    html += "<p>Không ép được (" + (parsed.unmatched_total || unmatched.length) + " key" +
      (parsed.unmatched_truncated ? ", chỉ hiện " + unmatched.length + " key đầu" : "") + "):</p>" +
      "<table><tr><th>Key</th><th>Lý do</th><th>Đơn vị</th></tr>";
    for (const m of unmatched.slice(0, 200)) {
      html += "<tr><td>" + esc(m.key) + "</td><td>" + esc(REASONS[m.reason] || m.reason) +
        "</td><td>" + esc(m.unit || "") + "</td></tr>";
    }
    html += "</table>";
  }
  const conflicts = parsed.conflicts || [];
  if (conflicts.length) {
    html += "<p>Xung đột key (" + (parsed.conflicts_total || conflicts.length) + " — chọn bản thắng cho mỗi key; mặc định giữ bản đầu):</p>" +
      "<table><tr><th>Key</th><th>Bản giữ (kept)</th><th>Bản loại (dropped)</th><th>Chọn</th></tr>";
    for (const c of conflicts) {
      const kept = c.kept || {}, dropped = c.dropped || {};
      html += "<tr><td>" + esc(c.key) + "</td><td>" + esc(kept.value) +
        "<br><span class=\"sub\">" + esc(kept.source || "") + "</span></td><td>" + esc(dropped.value) +
        "<br><span class=\"sub\">" + esc(dropped.source || "") + "</span></td><td>" +
        "<label><input type=\"radio\" data-rebuild-conflict=\"" + esc(c.key) + "\" value=\"kept\"" +
        (c.choice !== "dropped" ? " checked" : "") + "> Giữ</label> " +
        "<label><input type=\"radio\" data-rebuild-conflict=\"" + esc(c.key) + "\" value=\"dropped\"" +
        (c.choice === "dropped" ? " checked" : "") + "> Loại</label></td></tr>";
      if (c.warning) html += "<tr><td colspan=\"4\" class=\"sub\">" + esc(c.warning) + "</td></tr>";
    }
    html += "</table>";
    if (parsed.conflicts_truncated) html += "<p class=\"note\">Chỉ hiện " + conflicts.length + " xung đột đầu trong tổng " + parsed.conflicts_total + " (giới hạn 500 của bước gộp).</p>";
  }
  if ((parsed.refused || []).length) {
    html += "<p>Đơn vị bị từ chối:</p><table><tr><th>Đơn vị</th><th>Mã</th><th>Lý do</th></tr>";
    for (const r of parsed.refused) html += "<tr><td>" + esc(r.unit) + "</td><td>" + esc(r.code) +
      "</td><td>" + esc(rebuildErrorText(r.message)) + "</td></tr>";
    html += "</table>";
  }
  if ((parsed.outputs || []).length) html += "<p>File đã ghi, đã sẵn sàng để lưu: <b>" + esc(parsed.outputs.join(", ")) + "</b> — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>";
  html += "<p class=\"note\">" + esc(parsed.note || "") + "</p>";
  $("rebuild-result").innerHTML = html;
  const hasConf = conflicts.length > 0;
  for (const id of ["btn-rebuild-choose-all-kept", "btn-rebuild-choose-all-dropped"]) {
    const b = $(id); if (b) b.disabled = !hasConf;
  }
}
async function rebuildRun(dryRun) {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  const src = rebuildSourceData();
  if (src.error) { showError(src.error); return; }
  const st = state.rebuild || rebuildFreshState();
  st.translations = src.translations;
  st.provenance = src.provenance;
  st.fileKey = rebuildFileKey();
  state.rebuild = st;
  refreshRebuildFileInfo();
  const expected = rebuildExpectedGameFile(src.provenance);
  if (expected && expected !== state.file.name) {
    if (!confirm("Tên file gốc đang chọn (" + state.file.name + ") khác tên lúc trích xuất (" + expected + "). Tiếp tục ép vào file này? (Nên bấm Huỷ rồi chọn lại đúng file gốc.)")) return;
  }
  if (!dryRun) {
    if (!st.preview) { showError("Chưa xem trước — bấm “Xem trước (dry-run)” trước khi ghi."); return; }
    // P0-2: cổng kiểm tra (bước 6) phải ĐẠT cho đúng file + bản dịch +
    // lựa chọn xung đột hiện tại thì mới ghi; FAIL hoặc chưa kiểm thì
    // chặn (không có đường giao khi cảnh báo).
    if (!state.gate || state.gate.verdict !== "PASS" || state.gate.key !== gateKey()) {
      showError("Chưa qua cổng kiểm tra (bước 6) — chạy “Kiểm tra trước khi giao” ở mục bên dưới và phải ĐẠT thì mới ghi được file.");
      return;
    }
    if (!confirm("Ghi bản dịch vào file đã chọn? File gốc KHÔNG bị ghi đè; output chỉ là bundle/.assets, không phải IPA đóng gói lại; cần re-sign gói ngoài thì game mới nhận.")) return;
  }
  if (!(await rebuildConfirmLargeFile())) { showError("Đã huỷ — dùng bản iSH cho file này nếu cần."); return; }
  const choices = dryRun ? null : rebuildConflictChoices();
  try {
    const label = dryRun ? "Xem trước ép lại (E)" : "Ép lại vào file gốc (E)";
    const { parsed } = await runOp(label, "rebuild_apply",
      [inputPath(), JSON.stringify(src.translations),
       src.provenance ? JSON.stringify(src.provenance) : null,
       choices ? JSON.stringify(choices) : null, dryRun, !dryRun,
       dryRun ? null : JSON.stringify(state.gate.result)],
      inputFileArgs(), !dryRun);
    if (dryRun) {
      st.preview = parsed;
      // P0-2: nút Ghi chỉ bật khi đã xem trước VÀ cổng ĐẠT.
      refreshRebuildApplyEnabled();
    } else {
      st.applied = true;
      const rb2 = $("btn-rebuild-reextract"); if (rb2) rb2.disabled = false;
    }
    rebuildRenderResult(parsed);
  } catch (e) {
    showError("Ép lại (E) lỗi: " + rebuildErrorText(e.message));
  }
}
$("btn-translate-goto-rebuild").addEventListener("click", () => { goTool("mod"); });
$("btn-rebuild-reselect-file").addEventListener("click", () => { const inp = $("file-input"); if (inp) inp.click(); });
$("rebuild-source").addEventListener("change", () => {
  const src = rebuildSourceData();
  $("rebuild-source-info").textContent = src.error ? src.error : ("Nguồn: " + src.label + " · " + Object.keys(src.translations).length + " key.");
  refreshRebuildFileInfo();
  if (typeof gateInvalidate === "function") gateInvalidate();
});
$("btn-rebuild-import-json").addEventListener("click", () => { $("rebuild-json-file").click(); });
$("rebuild-json-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const obj = JSON.parse(await f.text());
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) throw new Error("file phải là JSON object key → giá trị.");
    for (const [k, v] of Object.entries(obj)) if (typeof v !== "string") throw new Error("giá trị của key " + JSON.stringify(k) + " không phải chuỗi.");
    state.rebuildFile = { translations: obj, provenance: (state.rebuildFile && state.rebuildFile.provenance) || null, name: f.name };
    $("rebuild-source").value = "file";
    $("rebuild-source-info").textContent = "Nguồn: file đã nhập (" + f.name + ") · " + Object.keys(obj).length + " key" + (state.rebuildFile.provenance ? " · đã có sidecar." : " · chưa có sidecar — sẽ quét tất cả đơn vị.");
    clearError();
    if (typeof gateInvalidate === "function") gateInvalidate();
  } catch (e) { showError("Nhập file .json lỗi: " + e.message); }
});
$("btn-rebuild-import-sidecar").addEventListener("click", () => { $("rebuild-sidecar-file").click(); });
$("rebuild-sidecar-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const obj = JSON.parse(await f.text());
    if (!obj || (obj.format !== "beartea-merge-provenance/1" && obj.format !== "beartea-translation-provenance/1")) throw new Error("sidecar phải có format beartea-merge-provenance/1 hoặc beartea-translation-provenance/1.");
    if (!state.rebuildFile) state.rebuildFile = { translations: null, provenance: obj, name: "(chưa có file .json chính)" };
    else state.rebuildFile.provenance = obj;
    $("rebuild-source").value = "file";
    $("rebuild-source-info").textContent = "Đã nhận sidecar " + obj.format + " (" + f.name + ")." + (state.rebuildFile.translations ? " · " + Object.keys(state.rebuildFile.translations).length + " key." : " Hãy nhập thêm file .json chính.");
    clearError();
    if (typeof gateInvalidate === "function") gateInvalidate();
  } catch (e) { showError("Nhập sidecar lỗi: " + e.message); }
});
$("btn-rebuild-preview").addEventListener("click", () => rebuildRun(true));
$("btn-rebuild-apply").addEventListener("click", () => rebuildRun(false));
$("btn-rebuild-choose-all-kept").addEventListener("click", () => {
  document.querySelectorAll("input[data-rebuild-conflict][value=\"kept\"]").forEach((r) => { r.checked = true; });
});
$("btn-rebuild-choose-all-dropped").addEventListener("click", () => {
  document.querySelectorAll("input[data-rebuild-conflict][value=\"dropped\"]").forEach((r) => { r.checked = true; });
});
$("btn-rebuild-reextract").addEventListener("click", async () => {
  // Nạp lại file vừa ép (đang giữ trong lastDownload) làm file hiện tại
  // rồi sang tab Trích xuất để kiểm vòng tròn. File lớn ghi qua OPFS
  // thì hướng dẫn tải về rồi chọn lại (không ôm blob trong RAM).
  const ld = state.lastDownload;
  if (!ld || ld.via !== "memory" || !ld.blob) {
    showError("Không còn file vừa ép trong bộ nhớ trang (file lớn ghi qua OPFS) — hãy tải file vừa ép về máy, chọn lại nó làm file gốc, rồi bấm Trích xuất để kiểm.");
    return;
  }
  try {
    const buf = await ld.blob.arrayBuffer();
    setCurrentGameFile({ name: ld.name, size: buf.byteLength, lastModified: Date.now(), data: buf });
    goTool("extract");
    showError("Đã nạp file vừa ép (" + ld.name + ") — bấm “Trích xuất text” để kiểm vòng tròn.");
  } catch (e) { showError("Nạp lại file vừa ép lỗi: " + e.message); }
});
rebuildResetState();

// ---- Cổng kiểm tra trước khi giao (bước 6) — P0-2 ----
// Một điểm vào duy nhất (bridge op delivery_gate + UI này): nhận file
// gốc (+ file đã ép nếu có) + bản dịch (chung nguồn với (E)) + bản gốc
// đã trích xuất + provenance, trả 1 verdict PASS/FAIL duy nhất. FAIL
// thì CHẶN giao: nút "Ép và ghi file" của (E) chỉ bật khi đã xem trước
// VÀ cổng ĐẠT cho đúng file + bản dịch + lựa chọn xung đột hiện tại
// (khóa bằng gateKey()); rebuildRun(false) kiểm lại trước khi ghi và
// truyền verdict sang bridge để bridge đối chiếu sha. Không có đường
// giao khi cảnh báo. Mọi dữ liệu từ file game/bản dịch đều qua esc().
// state.gate = {verdict, key, result}; đổi bất kỳ đầu vào nào cũng hủy
// verdict cũ (gateInvalidate) và tắt nút Ghi tới khi kiểm lại.
state.gate = null;
state.gateSource = null;       // {entries, name} khi nhập file bản gốc
state.gatePressed = null;      // {name, data, size, lastModified}
state.gateFont = null;         // {name, data, size, lastModified}
function gateSimpleHash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return ("0000000" + h.toString(16)).slice(-8);
}
function gateSourceData() {
  const sel = $("gate-source-mode");
  const mode = sel ? sel.value : "modjson";
  if (mode === "file") {
    const gs = state.gateSource;
    if (!gs || !gs.entries) return { source: null, label: "chưa nhập file bản gốc" };
    return { source: gs.entries, label: "file bản gốc đã nhập (" + gs.name + ")" };
  }
  const m = state.modJson;
  if (!m || !m.entries) return { source: null, label: "chưa mở file JSON nào ở phần B" };
  return { source: m.entries, label: "file JSON đang mở (" + m.name + ")" };
}
function gateKey() {
  // Khóa verdict vào đúng đầu vào đã kiểm; lệch bất kỳ thứ gì thì
  // verdict cũ không còn hiệu lực (nút Ghi tắt, phải kiểm lại).
  const src = rebuildSourceData();
  const gs = gateSourceData();
  const allow = ($("gate-allowlist") && $("gate-allowlist").value) || "";
  const partial = !!($("gate-partial") && $("gate-partial").checked);
  const fontSel = $("gate-font-mode");
  const fontMode = fontSel ? fontSel.value : "scan";
  const parts = [
    rebuildFileKey() || "",
    src.error ? ("ERR:" + src.error) : JSON.stringify(src.translations || {}),
    src.provenance ? JSON.stringify(src.provenance) : "",
    gs.source ? JSON.stringify(gs.source) : "",
    JSON.stringify(rebuildConflictChoices() || {}),
    state.gatePressed ? (state.gatePressed.name + "|" + state.gatePressed.size) : "",
    fontMode,
    state.gateFont ? (state.gateFont.name + "|" + state.gateFont.size) : "",
    allow, partial ? "partial" : "full",
  ];
  return gateSimpleHash(parts.join("\n"));
}
function refreshRebuildApplyEnabled() {
  const b = $("btn-rebuild-apply");
  if (!b) return;
  const st = state.rebuild;
  const ok = !!(st && st.preview && state.gate && state.gate.verdict === "PASS" &&
    state.gate.key === gateKey());
  b.disabled = !ok;
}
function gateInvalidate() {
  state.gate = null;
  const r = $("gate-result");
  if (r) r.innerHTML = "";
  refreshRebuildApplyEnabled();
}
function gateRenderResult(parsed) {
  const t = parsed.totals || {};
  const pass = parsed.verdict === "PASS";
  let html = "<p>Kết quả cổng kiểm tra (bước 6): <b>" +
    (pass ? "ĐẠT — được giao file" : "CHƯA ĐẠT — chưa được giao file") +
    "</b> · chế độ " + esc(parsed.mode === "pressed" ? "kiểm file đã ép" : "kiểm trước khi ghi") +
    " · " + (t.changed || 0) + "/" + (t.keys || 0) + " key đã đổi · " +
    (t.units || 0) + " đơn vị · " + (t.unmatched || 0) + " key chưa ép · " +
    (t.refused || 0) + " đơn vị bị từ chối</p>";
  if ((parsed.checks || []).length) {
    html += "<table><tr><th>Kiểm tra</th><th>Kết quả</th><th>Chi tiết</th></tr>";
    for (const c of parsed.checks) {
      html += "<tr><td>" + esc(c.name) + "</td><td><b>" + (c.status === "pass" ? "Đạt" : "Chưa đạt") +
        "</b></td><td>" + esc(c.summary_vi || "") + "</td></tr>";
    }
    html += "</table>";
  }
  if ((parsed.errors || []).length) {
    html += "<p>Lỗi cần sửa (" + parsed.errors.length + "):</p><ul>";
    for (const e of parsed.errors) html += "<li>" + esc(e) + "</li>";
    html += "</ul>";
  }
  if ((parsed.warnings || []).length) {
    html += "<p class=\"note\">Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>";
  }
  const fonts = parsed.fonts || [];
  if (fonts.length) {
    html += "<p>Font (" + fonts.length + "):</p><table><tr><th>Font</th><th>Loại</th><th>Độ phủ /142</th><th>Chữ đang dùng còn thiếu</th></tr>";
    for (const f of fonts) {
      html += "<tr><td>" + esc(f.name) + (f.supplied ? " (đã chỉ định)" : "") + "</td><td>" + esc(f.kind) +
        "</td><td>" + (f.covered == null ? "chưa kiểm được" : (f.covered + "/142")) +
        "</td><td>" + esc((f.missing_used_chars || []).join(" ") || "—") + "</td></tr>";
    }
    html += "</table>";
  }
  const unmatched = parsed.unmatched || [];
  if (unmatched.length || (parsed.unmatched_total || 0) > 0) {
    const REASONS = { monobehaviour: "MonoBehaviour/StringTable — chưa ép được",
      not_found: "không tìm thấy trong đơn vị nào", not_json_dict: "TextAsset không phải JSON dạng dict",
      non_string_value: "giá trị gốc không phải chuỗi" };
    html += "<p>Key chưa ép (" + (parsed.unmatched_total || unmatched.length) +
      (parsed.unmatched_truncated ? ", chỉ hiện " + unmatched.length + " key đầu" : "") + "):</p>" +
      "<table><tr><th>Key</th><th>Lý do</th><th>Đơn vị</th></tr>";
    for (const m of unmatched.slice(0, 50)) {
      html += "<tr><td>" + esc(m.key) + "</td><td>" + esc(REASONS[m.reason] || m.reason) +
        "</td><td>" + esc(m.unit || "") + "</td></tr>";
    }
    html += "</table>";
  }
  const allow = parsed.allowlist_used || {};
  const allowCounts = allow.counts || {};
  const allowTotal = Object.values(allowCounts).reduce((a, b) => a + (b || 0), 0);
  if (allowTotal > 0 || parsed.partial) {
    html += "<p class=\"note\">Danh sách cho phép đã dùng: " + esc(JSON.stringify(allowCounts)) +
      (parsed.partial ? " · chế độ bản dịch từng phần." : "") + "</p>";
  }
  html += "<p class=\"note\">" + esc(parsed.note || "") + "</p>";
  $("gate-result").innerHTML = html;
}
async function gateRun() {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  const src = rebuildSourceData();
  if (src.error) { showError(src.error); return; }
  const gs = gateSourceData();
  const fontSel = $("gate-font-mode");
  const fontMode = fontSel ? fontSel.value : "scan";
  let fontPath = null;
  if (fontMode === "donor") fontPath = "/pb3web/fonts/BeVietnamPro-Regular.ttf";
  else if (fontMode === "file") {
    if (!state.gateFont) { showError("Chưa nhập file font — bấm “Nhập file font…” hoặc chọn chế độ font khác."); return; }
    fontPath = "/tmp/input/gate-font__" + state.gateFont.name;
  }
  let allowlist = null;
  const allowText = ($("gate-allowlist") && $("gate-allowlist").value || "").trim();
  if (allowText) {
    try {
      allowlist = JSON.parse(allowText);
      if (!allowlist || typeof allowlist !== "object" || Array.isArray(allowlist)) throw new Error("phải là JSON object.");
    } catch (e) { showError("Danh sách cho phép không phải JSON object hợp lệ: " + e.message); return; }
  }
  const partial = !!($("gate-partial") && $("gate-partial").checked);
  const choices = rebuildConflictChoices();
  const key = gateKey();
  // File gửi kèm: file gốc (nếu worker chưa có) + file đã ép/font phụ
  // (tên có tiền tố gate- để không đè file gốc trong /tmp/input).
  const extras = [];
  if (state.gatePressed) extras.push({ name: "gate-pressed__" + state.gatePressed.name, data: state.gatePressed.data });
  if (state.gateFont) extras.push({ name: "gate-font__" + state.gateFont.name, data: state.gateFont.data });
  const fileKey = rebuildFileKey();
  const files = (fileKey && state.fileSentFor === fileKey)
    ? extras : inputFileArgs().concat(extras);
  const pressedPath = state.gatePressed ? ("/tmp/input/gate-pressed__" + state.gatePressed.name) : null;
  try {
    const { parsed } = await runOp("Kiểm tra trước khi giao", "delivery_gate",
      [inputPath(), JSON.stringify(src.translations),
       gs.source ? JSON.stringify(gs.source) : null,
       src.provenance ? JSON.stringify(src.provenance) : null,
       pressedPath, allowlist ? JSON.stringify(allowlist) : null,
       fontPath, Object.keys(choices).length ? JSON.stringify(choices) : null,
       partial],
      files);
    state.gate = { verdict: parsed.verdict, key: key, result: parsed };
    gateRenderResult(parsed);
    refreshRebuildApplyEnabled();
    if (parsed.verdict !== "PASS") {
      showError("Cổng kiểm tra CHƯA ĐẠT — chưa được giao file. Xem lỗi ở kết quả bên dưới, sửa rồi kiểm lại.");
    }
  } catch (e) {
    state.gate = null;
    refreshRebuildApplyEnabled();
    showError("Kiểm tra trước khi giao lỗi: " + rebuildErrorText(e.message));
  }
}
function refreshGateSourceInfo() {
  const el = $("gate-source-info");
  if (!el) return;
  const gs = gateSourceData();
  el.textContent = gs.source
    ? ("Bản gốc: " + gs.label + " · " + Object.keys(gs.source).length + " key.")
    : ("Chưa có bản gốc (" + gs.label + ") — cổng sẽ CHƯA ĐẠT ở 2 kiểm tra đầu. Lấy file trích xuất ở phần B, hoặc nhập file .json bản gốc.");
}
$("gate-source-mode").addEventListener("change", () => { refreshGateSourceInfo(); gateInvalidate(); });
$("btn-gate-import-source").addEventListener("click", () => { $("gate-source-file").click(); });
$("gate-source-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const obj = JSON.parse(await f.text());
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) throw new Error("file phải là JSON object key → giá trị.");
    for (const [k, v] of Object.entries(obj)) if (typeof v !== "string") throw new Error("giá trị của key " + JSON.stringify(k) + " không phải chuỗi.");
    state.gateSource = { entries: obj, name: f.name };
    $("gate-source-mode").value = "file";
    refreshGateSourceInfo();
    clearError();
    gateInvalidate();
  } catch (e) { showError("Nhập file .json bản gốc lỗi: " + e.message); }
});
$("btn-gate-import-pressed").addEventListener("click", () => { $("gate-pressed-file").click(); });
$("gate-pressed-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const data = await f.arrayBuffer();
    state.gatePressed = { name: f.name, data: data, size: f.size, lastModified: f.lastModified || 0 };
    $("gate-pressed-info").textContent = "File đã ép: " + f.name + " (" + fmtBytes(f.size) + ") — cổng sẽ kiểm file này thay vì mô phỏng.";
    clearError();
    gateInvalidate();
  } catch (e) { showError("Đọc file đã ép lỗi: " + e.message); }
});
$("btn-gate-clear-pressed").addEventListener("click", () => {
  state.gatePressed = null;
  $("gate-pressed-info").textContent = "Chưa chọn file đã ép — cổng sẽ mô phỏng ép trong RAM rồi kiểm (kiểm trước khi ghi).";
  gateInvalidate();
});
$("gate-font-mode").addEventListener("change", () => {
  const mode = $("gate-font-mode").value;
  $("gate-font-info").textContent = mode === "donor"
    ? "Dùng font đi kèm Be Vietnam Pro (đủ 142/142) làm font hiển thị."
    : mode === "file"
      ? (state.gateFont ? ("File font: " + state.gateFont.name) : "Chưa chọn file font — bấm “Nhập file font…”.")
      : "Chưa chọn file font — đang quét font trong file gốc.";
  gateInvalidate();
});
$("btn-gate-import-font").addEventListener("click", () => { $("gate-font-file").click(); });
$("gate-font-file").addEventListener("change", async (ev) => {
  const f = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const data = await f.arrayBuffer();
    state.gateFont = { name: f.name, data: data, size: f.size, lastModified: f.lastModified || 0 };
    $("gate-font-mode").value = "file";
    $("gate-font-info").textContent = "File font: " + f.name + " (" + fmtBytes(f.size) + ") — cổng kiểm độ phủ theo font này.";
    clearError();
    gateInvalidate();
  } catch (e) { showError("Đọc file font lỗi: " + e.message); }
});
$("gate-allowlist").addEventListener("change", () => gateInvalidate());
$("gate-partial").addEventListener("change", () => gateInvalidate());
$("btn-gate-run").addEventListener("click", () => gateRun());
refreshGateSourceInfo();

// ---- Batch: pool worker + checkpoint từng game vào IndexedDB + watchdog hợp tác (Mục 4) ----
// Kiến trúc:
//  - app.js tách manifest bằng bridge.batch_split (1 lần, worker đơn) rồi
//    giao TỪNG GAME cho executor: pool N worker (mặc định 2, chọn được 1–2,
//    Mục C9) hoặc 1 worker (khi pool tắt/lỗi — fallback tự động, không cần
//    user làm gì).
//  - Mỗi worker là 1 thread hệ điều hành thật, chạy 1 Pyodide + PB3 riêng
//    (KHÔNG pthreads, KHÔNG SharedArrayBuffer, KHÔNG COI) — pool N worker
//    chạy SONG SONG THẬT (batch 2 game xong 24,6s trên file thật — con số
//    này không kèm log trong cây [CHƯA KIỂM CHỨNG]); chỉ BÊN
//    TRONG một worker thì Python chạy tuần tự vì Pyodide không có pthreads.
//    Thao tác đơn (list/inspect/extract/...) vẫn đi qua worker đơn cũ như
//    trước — pool chỉ phục vụ batch (chọn cách đơn giản, ít rủi ro nhất:
//    không xáo trộn đường thao tác đơn đã kiểm chứng). Giá phải trả: lúc
//    batch chạy có 3 Pyodide cùng sống (1 worker đơn + 2 worker pool) —
//    ~2x RAM so với batch 1 worker trước đây; trên iPhone RAM thấp có thể
//    nặng, CHƯA ĐO trên iPhone thật.
//  - Sau MỖI game xong, main thread ghi checkpoint gộp vào IndexedDB ngay
//    (ckptStore — tái dùng lớp KBStore của Mục 3, khóa khác
//    "batch-checkpoint.json", cùng DB "pb3-kb-store"; KHÔNG viết store thứ
//    hai) + localStorage (tương thích cũ). Tải lại trang / tab bị kill giữa
//    chừng thì "Nạp checkpoint" / "Tiếp tục từ checkpoint" chạy tiếp phần
//    chưa xong, không làm lại game đã xong.
//  - Watchdog HỢP TÁC (không terminate): worker.js tự kiểm tra đồng hồ
//    trước mỗi game (batch_begin đặt hạn, xem WATCHDOG_SECONDS ở đầu
//    worker.js); quá hạn hoặc user bấm "Dừng an toàn" thì worker từ chối
//    game mới (E_WATCHDOG_STOP / E_BATCH_STOPPED), game đang chạy vẫn xong,
//    checkpoint đã lưu. Nút "Tiếp tục" chạy tiếp phần còn lại.
//  - KB: batch ở chế độ pool KHÔNG ghi vào KB (record_kb=false — KB của các
//    worker trong pool khác nhau, file SQLite không gộp được; chỉ worker đơn
//    mới ghi KB rồi persistKbNow). Game vẫn thêm vào KB được bằng tab Kho KB.
//  - Output cuối đợt: worker 1 zip thư mục của nó -> gửi sang worker 0
//    (batch_merge_zip) -> worker 0 viết summary + checkpoint GỘP rồi zip
//    thành batch.zip duy nhất (batch_finalize). Nếu chỉ 1 worker có game
//    thì bỏ bước gộp.

// ---- Số worker pool batch cấu hình được (Mục C9) ----
// Mặc định 2 worker; TỰ ĐỘNG 1 worker khi RAM thiết bị thấp
// (navigator.deviceMemory ≤ 4 GB — mỗi worker 1 Pyodide + PB3 riêng nên
// pool 2 worker tốn ~2x RAM so với 1 worker). Trình duyệt không báo RAM
// (vd Safari: deviceMemory là undefined) thì giữ mặc định 2.
// Người dùng đổi được 1–2 ở tab Batch, lưu bằng localStorage để giữ qua
// lần tải lại; lựa chọn đã lưu thắng mặc định tự động.
// Hook window.__PB3_DEVICE_MEMORY CHỈ cho harness kiểm chứng (giống
// window.__PB3_WATCHDOG_SECONDS): đặt số GB giả để thử đường tự động về 1;
// production không đặt thì dùng navigator.deviceMemory thật.
const POOL_SIZE_MIN = 1;
const POOL_SIZE_MAX = 2;
const POOL_SIZE_DEFAULT = 2;
const POOL_SIZE_LS_KEY = "pb3_pool_size";
const POOL_SIZE_LOW_MEMORY_GB = 4;
function deviceMemoryGB() {
  if (typeof window !== "undefined" && typeof window.__PB3_DEVICE_MEMORY === "number")
    return window.__PB3_DEVICE_MEMORY;
  if (typeof navigator !== "undefined" && typeof navigator.deviceMemory === "number")
    return navigator.deviceMemory;
  return null;   // trình duyệt không báo RAM: không ép về 1
}
function defaultPoolSize() {
  const mem = deviceMemoryGB();
  return (mem !== null && mem <= POOL_SIZE_LOW_MEMORY_GB) ? POOL_SIZE_MIN : POOL_SIZE_DEFAULT;
}
function clampPoolSize(n) {
  n = Math.floor(Number(n));
  if (!isFinite(n)) return defaultPoolSize();
  return Math.min(POOL_SIZE_MAX, Math.max(POOL_SIZE_MIN, n));
}
function loadPoolSize() {
  try {
    const raw = localStorage.getItem(POOL_SIZE_LS_KEY);
    if (raw !== null) return clampPoolSize(raw);
  } catch (e) { /* localStorage không có: dùng mặc định */ }
  return defaultPoolSize();
}
function savePoolSize(n) {
  n = clampPoolSize(n);
  try { localStorage.setItem(POOL_SIZE_LS_KEY, String(n)); } catch (e) { /* bỏ qua */ }
  return n;
}
function getPoolSize() {
  return clampPoolSize((state && typeof state.poolSize === "number")
    ? state.poolSize : loadPoolSize());
}
// Nhãn hiển thị cho pool N worker ("pool 1 worker" / "pool 2 worker").
function poolLabel(n) { return "pool " + n + " worker"; }
// Số worker lúc tải trang: lựa chọn đã lưu, nếu chưa có thì mặc định
// (tự động 1 khi RAM thiết bị ≤ 4 GB).
state.poolSize = loadPoolSize();

const BATCH_WATCHDOG_SECONDS = 25;   // GIỮ ĐỒNG BỘ với WATCHDOG_SECONDS trong worker.js

// Checkpoint batch: cùng lớp KBStore của Mục 3, khóa riêng trong cùng DB.
const ckptStore = (typeof KBStore !== "undefined")
  ? new KBStore("batch-checkpoint.json", { debounceMs: 0 }) : null;
const CKPT_LS_KEY = "pb3_batch_checkpoint";

function ckptToJson(ckpt) { return JSON.stringify(ckpt); }
function emptyCkpt() { return { version: 1, games: {} }; }
function parseCkptText(text) {
  try {
    const c = JSON.parse(text);
    if (c && c.version === 1 && c.games && typeof c.games === "object") return c;
  } catch (e) { /* checkpoint hỏng: coi như trống, không crash */ }
  return null;
}
// Nạp checkpoint gộp: IndexedDB trước (Mục 4), localStorage sau (tương thích cũ).
async function loadMergedCkpt() {
  let text = null, from = "";
  if (ckptStore) {
    try {
      const bytes = await ckptStore.load();
      if (bytes && bytes.length) { text = new TextDecoder().decode(bytes); from = "IndexedDB"; }
    } catch (e) { /* IndexedDB lỗi: rơi về localStorage */ }
  }
  if (!text) {
    try { text = localStorage.getItem(CKPT_LS_KEY); if (text) from = "localStorage"; } catch (e) { /* không có */ }
  }
  const ckpt = text ? parseCkptText(text) : null;
  return { ckpt: ckpt || emptyCkpt(), from, found: !!ckpt };
}
// Lưu checkpoint gộp vào IndexedDB + localStorage. Các lần lưu nối đuôi
// nhau bằng chuỗi promise để 2 game xong cùng lúc không ghi đè lẫn nhau.
let ckptSaveChain = Promise.resolve();
async function saveMergedCkpt(ckpt) {
  state.lastCheckpoint = ckptToJson(ckpt);
  const bytes = new TextEncoder().encode(state.lastCheckpoint);
  if (ckptStore) {
    try { await ckptStore.saveNow(bytes); } catch (e) { /* localStorage dưới vẫn lưu */ }
  }
  try { localStorage.setItem(CKPT_LS_KEY, state.lastCheckpoint); } catch (e) { /* localStorage đầy/không có */ }
}
function saveMergedCkptSoon(ckpt) {
  ckptSaveChain = ckptSaveChain.then(() => saveMergedCkpt(ckpt)).catch(() => {});
  return ckptSaveChain;
}

// Ngưỡng watchdog cho đợt batch. Hook window.__PB3_WATCHDOG_SECONDS CHỈ
// cho harness kiểm chứng (đặt ngưỡng nhỏ để thử cơ chế dừng); production
// không đặt thì dùng BATCH_WATCHDOG_SECONDS ở trên.
function batchWatchdogSeconds() {
  if (typeof window !== "undefined" && typeof window.__PB3_WATCHDOG_SECONDS === "number" &&
      window.__PB3_WATCHDOG_SECONDS > 0) {
    return window.__PB3_WATCHDOG_SECONDS;
  }
  return BATCH_WATCHDOG_SECONDS;
}

// ---- Executor: cùng giao diện tối thiểu cho pool worker và worker đơn ----
// run(payload, opts) / runOn(slot, payload, opts) -> Promise<{parsed, file, slot}>
// beginBatch(budgetSeconds) / requestStop() / cancelQueued() / status() / destroy()
function unwrapPoolReply(r) {
  const msg = r.msg;
  if (!msg.ok) throw new Error(msg.error || "Lỗi không rõ");
  const parsed = JSON.parse(msg.result);
  if (parsed && parsed.ok === false) throw new Error(parsed.error || parsed.code || "Lỗi không rõ");
  return { parsed, file: msg.file || null, slot: r.slot };
}
function makePoolExecutor() {
  const n = getPoolSize();
  const pool = new WorkerPool("worker.js", {
    size: n,
    onSlotChange: () => syncPoolUI(),
    onStatus: (t) => setStatus(t),
  });
  // Hook cho harness kiểm chứng (giống __PB3_LAST_PROBE): số worker của
  // pool vừa tạo — đọc được từ ngoài mà không cần chạm state nội bộ.
  try { window.__PB3_POOL_SIZE = n; } catch (e) { /* bỏ qua */ }
  return {
    kind: "pool", pool, poolSize: n,
    status: () => pool.status(),
    beginBatch: (b) => pool.beginBatch(b),
    requestStop: () => pool.requestStop(),
    cancelQueued: () => pool.cancelQueued(),
    run: (payload, opts) => pool.run(payload, opts).then(unwrapPoolReply),
    runOn: (i, payload, opts) => pool.runOn(i, payload, opts).then(unwrapPoolReply),
    destroy: () => pool.destroy(),
  };
}
// Khóa định danh 1 file cho dedup ở executor đơn — cùng quy ước với
// fileKey() trong worker-pool.js (tên|cỡ|lastModified). Thiếu trường nào
// thì dùng byteLength của buffer làm cỡ, lastModified = 0.
function batchFileKey(f) {
  const size = (typeof f.size === "number") ? f.size
    : (f.data && typeof f.data.byteLength === "number" ? f.data.byteLength : 0);
  const lm = (typeof f.lastModified === "number") ? f.lastModified : 0;
  return (f.name || "") + "|" + size + "|" + lm;
}
function makeSingleExecutor() {
  // Hàng đợi 1 job trên worker đơn cũ (callWorker): cùng giao diện như pool
  // để vòng batch không cần rẽ nhánh. File gửi qua callWorker (đã có logic
  // chống clone DataCloneError: gửi bằng structured clone, 1 lần/file).
  const ex = {
    kind: "single",
    _sentKeys: null,   // file đã gửi tới worker đơn này (FS sống suốt phiên)
    _queue: [], _busy: false, _currentLabel: "",
    status() {
      return [{ index: 0, alive: true, ready: state.booted, busy: this._busy, label: this._currentLabel || "" }];
    },
    beginBatch(budgetSeconds) { getWorker().postMessage({ op: "batch_begin", budgetSeconds }); },
    requestStop() { getWorker().postMessage({ op: "batch_stop" }); },
    cancelQueued() {
      const q = this._queue.splice(0);
      for (const j of q) j.reject(new Error("E_BATCH_STOPPED: job chờ đã bị hủy (dừng an toàn)."));
    },
    destroy() { this.cancelQueued(); },
    run(payload, opts) { return this._enqueue(payload, opts || {}); },
    runOn(_i, payload, opts) { return this._enqueue(payload, opts || {}); },
    _enqueue(payload, opts) {
      return new Promise((resolve, reject) => {
        this._queue.push({ payload, opts, resolve, reject });
        this._pump();
      });
    },
    async _pump() {
      if (this._busy) return;
      const job = this._queue.shift();
      if (!job) return;
      this._busy = true;
      this._currentLabel = job.opts.label || "";
      syncPoolUI();
      try {
        // Dedup như pool (mục 4): worker đơn giữ FS suốt phiên nên mỗi file
        // chỉ gửi 1 lần; game sau dùng lại file đã có trong /tmp/input.
        // (Nhánh fallback của callWorker gửi lại mỗi lần cho job từng game
        // vì data là bản copy mới — lọc ở đây trước khi gọi callWorker.)
        let payload = job.payload;
        if (payload.files && payload.files.length) {
          this._sentKeys = this._sentKeys || {};
          const keep = [];
          for (const ff of payload.files) {
            const k = batchFileKey(ff);
            if (this._sentKeys[k]) continue;
            this._sentKeys[k] = true;
            keep.push(ff);
          }
          payload = Object.assign({}, payload, { files: keep });
        }
        const msg = await callWorker("run", payload);
        const parsed = JSON.parse(msg.result);
        if (parsed && parsed.ok === false) throw new Error(parsed.error || parsed.code || "Lỗi không rõ");
        job.resolve({ parsed, file: msg.file || null, slot: 0 });
      } catch (e) { job.reject(e); }
      finally { this._busy = false; this._currentLabel = ""; this._pump(); syncPoolUI(); }
    },
  };
  return ex;
}
// Lấy executor cho đợt batch: pool N worker nếu bật + boot được, ngược lại
// rơi về 1 worker (ghi rõ lý do vào state.executorNote để hiện ở kết quả).
// Đổi số worker (Mục C9) thì pool cũ bị hủy để đợt sau tạo pool mới đúng N.
async function ensureBatchExecutor() {
  const usePool = $("batch-use-pool") ? $("batch-use-pool").checked : true;
  if (state.executor) {
    const sizeChanged = usePool && state.executor.kind === "pool" &&
      state.executor.poolSize !== getPoolSize();
    if (usePool && state.executor.kind === "pool" && !sizeChanged) return state.executor;
    if (!usePool && state.executor.kind === "single") return state.executor;
    try { if (state.executor.destroy) state.executor.destroy(); } catch (e) { /* bỏ qua */ }
    state.executor = null;
    state.executorNote = "";
  }
  if (usePool && typeof WorkerPool !== "undefined") {
    try {
      const ex = makePoolExecutor();
      setStatus("Đang khởi động " + poolLabel(ex.poolSize) +
        " (mỗi worker 1 Pyodide + PB3 riêng, lần đầu hơi lâu)…");
      syncPoolUI();
      const ok = await ex.pool.ready();
      if (ok) {
        state.executor = ex;
        state.executorNote = poolLabel(ex.poolSize);
        return ex;
      }
      ex.destroy();
      state.executorNote = "pool khởi động thất bại (không worker nào sẵn sàng) — đã rơi về 1 worker.";
    } catch (e) {
      state.executorNote = "không tạo được pool (" + e.message + ") — đã rơi về 1 worker.";
    }
  } else if (usePool) {
    state.executorNote = "trình duyệt không nạp được worker-pool.js — đã rơi về 1 worker.";
  }
  state.executor = makeSingleExecutor();
  if (!state.executorNote) state.executorNote = "1 worker (pool tắt theo lựa chọn).";
  return state.executor;
}

// ---- UI trạng thái pool + checkpoint (tab Batch) ----
function syncPoolUI() {
  const el = $("batch-pool-info");
  if (!el) return;
  const ex = state.executor;
  if (!ex) {
    el.textContent = "Pool chưa khởi động (bấm “Chạy batch” để khởi động " +
      poolLabel(getPoolSize()) + ").";
    return;
  }
  const slots = ex.status();
  let lines = ex.kind === "pool"
    ? poolLabel(ex.poolSize) + " (mỗi worker 1 Pyodide + PB3 riêng" +
      (ex.poolSize > 1 ? ", ~" + ex.poolSize + "x RAM so với 1 worker" : "") + "):"
    : "Đang chạy bằng 1 worker (pool tắt/lỗi):";
  for (const s of slots) {
    lines += "\nWorker " + s.index + ": " +
      (!s.alive ? "LỖI/chết" : !s.ready ? "đang khởi động…" :
       s.busy ? "đang chạy " + (s.label || "?") : "rảnh");
  }
  const br = state.batchRun;
  if (br && !br.finished) {
    lines += "\nĐã xong " + br.doneCount + "/" + br.totalTodo + " game · đã chạy " +
      fmtTime((performance.now() - br.startTime) / 1000);
  }
  el.textContent = lines;
}
function syncBatchCkptInfo() {
  const el = $("batch-ckpt-info");
  if (!el) return;
  const br = state.batchRun;
  if (!br) {
    el.textContent = state.lastCheckpoint
      ? "Có checkpoint đã lưu — tick “Tiếp tục từ checkpoint” rồi bấm Chạy batch để bỏ qua game đã xong."
      : "";
    return;
  }
  const done = Object.keys(br.merged.games).filter((k) => br.merged.games[k].status === "ok").length;
  const remain = br.items.length - done;
  const savedAt = ckptStore && ckptStore.lastSavedAt
    ? new Date(ckptStore.lastSavedAt).toLocaleTimeString("vi-VN") : "chưa lưu";
  el.textContent = "Checkpoint từng game → IndexedDB (lần lưu gần nhất: " + savedAt +
    ") · đã xong " + done + "/" + br.items.length + " game · còn " + remain + " game." +
    (br.watchdogStopped ? " Watchdog đã dừng an toàn — bấm “Tiếp tục” để chạy tiếp." :
     br.stopRequested ? " Đã dừng an toàn — bấm “Tiếp tục” để chạy tiếp." : "");
}

function renderBatchFiles() {
  const el = $("batch-files-info");
  if (!el) return;
  if (!state.batchFiles.length) {
    el.innerHTML = "Chưa chọn file nào.";
    el.hidden = false;
    return;
  }
  let html = state.batchFiles.length + " file:<br>";
  state.batchFiles.forEach((f, i) => {
    html += "<span class=\"batch-file-row\">" + esc(f.name) + " (" + fmtBytes(f.size) + ") " +
      "<button class=\"secondary\" data-batch-rm=\"" + i + "\">X</button></span><br>";
  });
  el.innerHTML = html;
  el.hidden = false;
}
function syncBatchRunLabel() {
  const b = $("btn-batch-run");
  if (!b) return;
  b.textContent = state.batchFiles.length
    ? "Chạy batch (" + state.batchFiles.length + " file)"
    : "Chạy batch";
}
$("batch-files-info").addEventListener("click", (ev) => {
  const t = ev && ev.target;
  if (!t || typeof t.closest !== "function") return;
  const btn = t.closest("button");
  if (!btn || typeof btn.getAttribute !== "function") return;
  const idx = btn.getAttribute("data-batch-rm");
  if (idx == null || idx === "") return;
  state.batchFiles.splice(parseInt(idx, 10), 1);
  state.batchSentFor = null;   // HOTFIX clone: lô đổi thì gửi lại từ đầu
  renderBatchFiles();
  syncBatchRunLabel();
  renderRecent();
});
async function handleBatchFiles(list) {
  let rejected = 0;
  let batchWarn = null;   // Mục 5: cảnh báo file lớn nhất trong lô
  const valid = [];
  for (const f of list) {
    if (!gameExtOk(f.name)) { showError(gameExtError(f.name)); rejected++; continue; }
    if (f.size > MAX_BYTES) { showError("File " + f.name + " quá lớn cho bản web (>2GB)."); continue; }
    const w = sizeWarningFor(f.size);
    if (w.text && (!batchWarn || w.level === "strong")) batchWarn = { name: f.name, w };
    valid.push(f);
  }
  // Mục C4: lô có file >140MB thì hỏi TRƯỚC KHI đọc file nào vào RAM —
  // "Hủy" thì dừng hẳn, lô cũ vẫn giữ nguyên, không đọc file nào.
  if (valid.length && !(await confirmLargeFile(valid))) {
    showError("Đã huỷ mở lô file lớn — dùng bản iSH cho các file này nếu cần.");
    return;
  }
  state.batchFiles = [];
  for (const f of valid) {
    state.batchFiles.push({ name: f.name, size: f.size, lastModified: f.lastModified || 0, data: await f.arrayBuffer() });
  }
  state.batchSentFor = null;   // HOTFIX clone: lô mới thì gửi lại từ đầu
  renderBatchFiles();
  syncBatchRunLabel();
  if (state.batchFiles.length) {
    // Mục 6 (lazy boot): đã có file batch hợp lệ thì boot Pyodide nền.
    ensureBoot().catch(() => { /* lỗi boot đã hiện ở banner + trạng thái */ });
    if (!rejected) clearError();
    if (batchWarn) showError(batchWarn.w.text);
    for (const f of state.batchFiles) addRecent(f);
  }
}
$("batch-files").addEventListener("change", async (ev) => {
  const list = [...(ev.target.files || [])];
  ev.target.value = "";
  await handleBatchFiles(list);
});
// Khung thả nhiều file ở tab Batch: bấm để chọn + kéo-thả.
$("batch-dropzone").addEventListener("click", () => {
  const inp = $("batch-files");
  if (inp && typeof inp.click === "function") inp.click();
});
$("batch-dropzone").addEventListener("dragover", (ev) => {
  ev.preventDefault();
  $("batch-dropzone").classList.add("drag");
});
$("batch-dropzone").addEventListener("dragleave", () => $("batch-dropzone").classList.remove("drag"));
$("batch-dropzone").addEventListener("drop", (ev) => {
  ev.preventDefault();
  $("batch-dropzone").classList.remove("drag");
  const list = ev.dataTransfer && ev.dataTransfer.files ? [...ev.dataTransfer.files] : [];
  handleBatchFiles(list);
});
$("btn-batch-load-ckpt").addEventListener("click", async () => {
  // Nạp checkpoint: IndexedDB trước (Mục 4), localStorage sau (tương thích cũ).
  const loaded = await loadMergedCkpt();
  if (!loaded.found) {
    $("batch-ckpt-info").textContent = "Chưa có checkpoint nào lưu trong trình duyệt này (IndexedDB/localStorage đều trống).";
    return;
  }
  state.loadedCheckpoint = ckptToJson(loaded.ckpt);
  state.lastCheckpoint = state.loadedCheckpoint;
  const done = Object.keys(loaded.ckpt.games).filter((k) => loaded.ckpt.games[k].status === "ok").length;
  $("batch-ckpt-info").textContent = "Đã nạp checkpoint từ " + loaded.from + " (" + done +
    " game đã xong). Tick “Tiếp tục từ checkpoint” rồi bấm Chạy batch.";
});
$("btn-batch-dl-ckpt").addEventListener("click", () => {
  if (!state.lastCheckpoint) { showError("Chưa có checkpoint (chạy batch 1 lần trước đã)."); return; }
  // Mục 5: tải dạng nhiều Blob part 64KB.
  const parts = (typeof PB3Stream !== "undefined" && PB3Stream.textToBlobParts)
    ? PB3Stream.textToBlobParts(state.lastCheckpoint) : [new TextEncoder().encode(state.lastCheckpoint)];
  download("checkpoint.json", parts);
});

// "Dừng an toàn": báo worker dừng sau game hiện tại + hủy job đang chờ.
// Game đang chạy vẫn chạy xong và checkpoint của nó vẫn được lưu.
function requestBatchStop() {
  const br = state.batchRun;
  if (!br || br.finished) return;
  br.stopRequested = true;
  const ex = state.executor;
  if (ex) {
    try { ex.requestStop(); } catch (e) { /* bỏ qua */ }
    try { ex.cancelQueued(); } catch (e) { /* bỏ qua */ }
  }
  const b = $("btn-batch-stop-safe");
  if (b) b.disabled = true;
  setStatus("Đang dừng an toàn sau game hiện tại…");
  syncBatchCkptInfo();
}
$("btn-batch-stop-safe").addEventListener("click", () => requestBatchStop());

function combinedSummary(br) {
  const games = br.results.filter(Boolean);
  return {
    total: br.items.length,
    ok: games.filter((g) => g.status === "ok").length,
    failed: games.filter((g) => g.status === "failed").length,
    skipped: games.filter((g) => g.status === "skipped").length,
    incomplete: games.filter((g) => g.status === "incomplete").length,
    elapsed_s: Math.round(((performance.now() - br.startTime) / 1000) * 10) / 10,
    out_dir: br.runId,
    jobs: br.executorKind === "pool" ? (br.poolSize || 2) : 1,
    dry_run: br.dryRun,
    budgets: {},
    games,
  };
}
function renderBatchResult(summary, meta) {
  const s = summary;
  let html = "<p>Batch " + (s.dry_run ? "<b>dry-run</b>" : "<b>chạy thật</b>") +
    " · " + (meta.executorNote || (s.jobs > 1 ? poolLabel(s.jobs) : "1 worker")) +
    " · " + s.ok + " ok / " + s.failed + " lỗi / " + s.skipped +
    " bỏ qua / " + s.total + " game · " + s.elapsed_s + " giây" +
    (meta.resumed ? " · đã tiếp tục từ checkpoint" : "") +
    (meta.watchdogStopped ? " · <b>watchdog đã dừng an toàn</b> (quá " + meta.watchdogSeconds +
      " giây) — bấm “Tiếp tục” để chạy tiếp phần còn lại" : "") +
    (meta.stopRequested && !meta.watchdogStopped ? " · <b>đã dừng an toàn</b> — bấm “Tiếp tục” để chạy tiếp phần còn lại" : "") +
    (meta.zipName ? " · file <b>" + esc(meta.zipName) + "</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy (summary, checkpoint, index, từng game)" : "") +
    "</p>";
  html += "<table><tr><th>#</th><th>Game</th><th>Mode</th><th>Kết quả</th><th>Lỗi</th><th>Giây</th><th>Ghi chú</th></tr>";
  for (const g of s.games || []) {
    html += "<tr><td>" + g.index + "</td><td>" + esc((g.path || "").split("/").pop()) + "</td><td>" + esc(g.mode) +
      "</td><td>" + esc(g.status) + "</td><td>" + esc(g.error_code || "") + " " + esc(g.error_message || "") +
      "</td><td>" + g.elapsed_s + "</td><td>" + esc(g.note || "") + "</td></tr>";
  }
  html += "</table>";
  if (meta.note) html += "<p class=note>" + esc(meta.note) + "</p>";
  $("batch-result").innerHTML = html;
}

// Gộp output 2 worker về 1 file batch.zip duy nhất (chạy trên worker 0).
async function finalizeBatchZip(executor, br) {
  if (executor.kind === "pool" && (br.gamesPerSlot[1] || 0) > 0) {
    const z1 = await executor.runOn(1, { fn: "batch_zip", args: [br.runId], files: [] }, { label: "zip-w1" });
    if (!z1.parsed.ok) throw new Error(z1.parsed.error || z1.parsed.code || "batch_zip worker 1 lỗi");
    if (!z1.file) throw new Error("worker 1 không trả file zip để gộp");
    const mg = await executor.runOn(0, {
      fn: "batch_merge_zip",
      args: [br.runId, "/tmp/input/merge-" + br.runId + ".zip"],
      files: [{ name: "merge-" + br.runId + ".zip", data: z1.file.data,
                size: z1.file.data.byteLength, lastModified: 0 }],
    }, { label: "merge", transfer: [z1.file.data] });
    if (!mg.parsed.ok) throw new Error(mg.parsed.error || mg.parsed.code || "batch_merge_zip lỗi");
  }
  const summary = combinedSummary(br);
  const fin = await executor.runOn(0, {
    fn: "batch_finalize",
    args: [br.runId, JSON.stringify(summary), ckptToJson(br.merged)],
    files: [],
  }, { label: "zip-final" });
  if (!fin.parsed.ok) throw new Error(fin.parsed.error || fin.parsed.code || "batch_finalize lỗi");
  return fin.file; // {name, data} — batch.zip
}

// Vòng chạy batch chính (dùng cho cả "Chạy batch" và "Tiếp tục").
// opts: {manifest, dryRun, resume}
async function runBatchFlow(opts) {
  if (state.batchRun && !state.batchRun.finished) {
    showError("Batch đang chạy — bấm “Dừng an toàn” trước khi chạy đợt mới.");
    return;
  }
  clearError();
  // 1. Tách manifest thành từng game + khóa checkpoint (worker đơn đã boot).
  let items;
  try {
    const msg = await callWorker("run", { fn: "batch_split", args: [opts.manifest], files: [] });
    const p = JSON.parse(msg.result);
    if (!p.ok) throw new Error(p.error || p.code || "batch_split lỗi");
    items = p.items || [];
  } catch (e) { showError("Tách manifest lỗi: " + e.message); return; }
  if (!items.length) { showError("Manifest không có game nào."); return; }
  // 2. Checkpoint gộp (IndexedDB trước, localStorage sau).
  const loaded = await loadMergedCkpt();
  let merged = loaded.ckpt;
  if (opts.resume && !loaded.found) {
    showError("Đã tick “Tiếp tục từ checkpoint” nhưng chưa có checkpoint nào lưu (IndexedDB/localStorage đều trống). Chạy batch mới trước đã.");
    return;
  }
  if (!opts.resume) merged = emptyCkpt();
  const doneKeys = new Set(Object.keys(merged.games).filter((k) => merged.games[k] && merged.games[k].status === "ok"));
  const results = items.map((it) => {
    if (!doneKeys.has(it.key)) return null;
    const prev = merged.games[it.key];
    return { index: it.index, path: it.path, mode: it.mode, locale: it.locale, key: it.key,
             status: "skipped", note: "đã xong (checkpoint)", elapsed_s: prev.elapsed_s || 0,
             game_dir: prev.game_dir || "", error_code: "", error_message: "" };
  });
  const todo = items.filter((it) => !doneKeys.has(it.key));
  if (!todo.length) {
    renderBatchResult({
      total: items.length, ok: doneKeys.size, failed: 0, skipped: items.length,
      incomplete: 0, elapsed_s: 0, out_dir: "", jobs: 1, dry_run: opts.dryRun,
      budgets: {}, games: results,
    }, { resumed: true, executorNote: "không cần chạy (đã xong hết theo checkpoint)" });
    $("batch-ckpt-info").textContent = "Tất cả " + items.length + " game đã xong theo checkpoint — không còn game nào để chạy.";
    return;
  }
  // 3. Executor: pool N worker, fallback 1 worker nếu pool lỗi/tắt.
  const executor = await ensureBatchExecutor();
  const runId = (state.batchRun && state.batchRun.runId) ? state.batchRun.runId
    : ("batch-" + Date.now().toString(36));
  const budget = batchWatchdogSeconds();
  const br = state.batchRun = {
    runId, manifest: opts.manifest, dryRun: opts.dryRun, items, results, merged,
    todo, doneCount: 0, totalTodo: todo.length, startTime: performance.now(),
    stopRequested: false, watchdogStopped: false, finished: false,
    executorKind: executor.kind,
    poolSize: executor.kind === "pool" ? (executor.poolSize || 2) : 1,
    gamesPerSlot: {},
  };
  executor.beginBatch(budget);
  $("btn-batch-run").disabled = true;
  $("btn-batch-continue").hidden = true;
  $("btn-batch-stop-safe").hidden = false;
  $("btn-batch-stop-safe").disabled = false;
  progStart(executor.kind === "pool"
    ? "Chạy batch (" + poolLabel(executor.poolSize) + ")"
    : "Chạy batch (1 worker)");
  setStatus("Batch đang chạy: " + todo.length + " game còn lại (" + state.executorNote +
    ", watchdog " + budget + " giây)…");
  syncPoolUI();
  syncBatchCkptInfo();
  const uiTimer = setInterval(syncPoolUI, 500);
  const fileByBase = new Map(state.batchFiles.map((f) => [f.name.toLowerCase(), f]));

  // Ghi kết quả 1 game NGAY khi game đó xong (kèm lưu checkpoint vào
  // IndexedDB) — không đợi cả lô xong mới ghi (đó mới là "checkpoint
  // từng game": tab bị kill giữa chừng vẫn giữ được game đã xong).
  const recordGameDone = async (it, reply) => {
    const parsed = reply.parsed;
    const g = (parsed.summary && parsed.summary.games && parsed.summary.games[0]) || null;
    const slot = (typeof reply.slot === "number") ? reply.slot : 0;
    br.gamesPerSlot[slot] = (br.gamesPerSlot[slot] || 0) + 1;
    br.doneCount++;
    if (!g) {
      br.results[it.index - 1] = { index: it.index, path: it.path, mode: it.mode, locale: it.locale,
        key: it.key, status: "failed", note: "", elapsed_s: 0, game_dir: "",
        error_code: "E_INTERNAL", error_message: "worker không trả kết quả game" };
      progSet(100 * br.doneCount / br.totalTodo, br.doneCount, br.totalTodo);
      syncBatchCkptInfo(); syncPoolUI();
      return;
    }
    // index/path/mode/locale lấy từ it (vị trí thật trong manifest): mỗi lần
    // chạy 1 game thì g.index của worker luôn = 1, không dùng được.
    br.results[it.index - 1] = { index: it.index, path: it.path,
      mode: it.mode, locale: it.locale, key: it.key, status: g.status,
      note: g.note || "", elapsed_s: g.elapsed_s || 0, game_dir: g.game_dir || "",
      error_code: g.error_code || "", error_message: g.error_message || "" };
    if (g.status === "ok" || g.status === "failed") {
      merged.games[it.key] = { path: it.path, mode: it.mode, locale: it.locale,
        status: g.status, elapsed_s: Math.round((g.elapsed_s || 0) * 100) / 100,
        game_dir: g.game_dir || "", error_code: g.error_code || "" };
      await saveMergedCkptSoon(merged);
    }
    progSet(100 * br.doneCount / br.totalTodo, br.doneCount, br.totalTodo);
    syncBatchCkptInfo(); syncPoolUI();
    // Lưới an toàn thứ hai (ngoài watchdog trong worker.js): đã quá hạn
    // mà vẫn còn game chưa xong thì dừng giao việc tiếp ngay sau game này.
    // (Game cuối cùng vừa xong thì không báo "đã dừng" — đợt đã xong hết.)
    if (!br.stopRequested && br.doneCount < br.totalTodo &&
        (performance.now() - br.startTime) > budget * 1000) {
      br.watchdogStopped = true;
      requestBatchStop();
    }
  };
  const recordGameError = (it, err) => {
    const msg = String((err && err.message) || err || "");
    br.doneCount++;
    if (msg.indexOf("E_WATCHDOG_STOP") === 0 || msg.indexOf("E_BATCH_STOPPED") === 0) {
      if (msg.indexOf("E_WATCHDOG_STOP") === 0) br.watchdogStopped = true;
      br.stopRequested = true;
      br.results[it.index - 1] = { index: it.index, path: it.path, mode: it.mode,
        locale: it.locale, key: it.key, status: "skipped",
        note: "chưa chạy (đã dừng an toàn)", elapsed_s: 0, game_dir: "",
        error_code: "", error_message: "" };
      requestBatchStop();
    } else {
      // Lỗi thật của 1 game: ghi failed, lô vẫn tiếp tục (game khác đã chạy).
      br.results[it.index - 1] = { index: it.index, path: it.path, mode: it.mode,
        locale: it.locale, key: it.key, status: "failed", note: "",
        elapsed_s: 0, game_dir: "", error_code: "E_INTERNAL", error_message: msg };
    }
    progSet(100 * br.doneCount / br.totalTodo, br.doneCount, br.totalTodo);
    syncBatchCkptInfo(); syncPoolUI();
  };

  // 4. Giao từng game cho executor (pool chia đều các worker, hàng đợi tự
  // bơm game tiếp theo khi có worker rảnh).
  const jobs = todo.map((it) => {
    const base = (it.path || "").split("/").pop().toLowerCase();
    const f = fileByBase.get(base) || fileByBase.get((it.path || "").toLowerCase());
    if (!f) {
      br.results[it.index - 1] = { index: it.index, path: it.path, mode: it.mode,
        locale: it.locale, key: it.key, status: "failed", note: "", elapsed_s: 0,
        game_dir: "", error_code: "E_INPUT_NOT_FOUND",
        error_message: "không có file “" + base + "” trong lô đã chọn" };
      br.doneCount++;
      progSet(100 * br.doneCount / br.totalTodo, br.doneCount, br.totalTodo);
      return Promise.resolve();
    }
    const singleManifest = JSON.stringify([{ path: it.path, mode: it.mode, locale: it.locale,
      old: it.old, new: it.new, file: it.file, object: it.object }]);
    // Gửi game bằng structured clone (không slice(0), không transfer):
    // tránh đỉnh phụ ~1× file trên main thread lúc gửi game lớn (đo thật
    // 2026-11-17: tab ~4× file + ~250MB nền). Buffer gốc không bị detach
    // nên gửi lại cho worker khác vẫn an toàn.
    const payload = { fn: "batch_run",
      args: [singleManifest, opts.dryRun, ckptToJson(merged), true, runId,
             executor.kind === "single", false, it.index],
      files: [{ name: f.name, data: f.data, size: f.size, lastModified: f.lastModified || 0 }] };
    return executor.run(payload, { label: base })
      .then((reply) => recordGameDone(it, reply))
      .catch((err) => recordGameError(it, err));
  });
  await Promise.all(jobs);
  await ckptSaveChain;   // chờ lần lưu checkpoint cuối xong hẳn
  clearInterval(uiTimer);

  // 5. Gộp zip cuối đợt (chỉ khi có game thật sự chạy trong đợt này).
  const processedAny = Object.keys(br.gamesPerSlot).length > 0;
  let finalZip = null;
  let zipError = "";
  if (processedAny) {
    try { finalZip = await finalizeBatchZip(executor, br); }
    catch (e) { zipError = e.message; }
  }
  br.finished = true;
  progDone("Chạy batch — xong");
  $("btn-batch-run").disabled = false;
  $("btn-batch-stop-safe").hidden = true;
  const remain = br.items.length -
    Object.keys(br.merged.games).filter((k) => br.merged.games[k].status === "ok").length;
  const canContinue = remain > 0 && (br.stopRequested || br.watchdogStopped);
  $("btn-batch-continue").hidden = !canContinue;
  if (finalZip) download(finalZip.name || "batch.zip", finalZip.data);
  if (executor.kind === "single") persistKbNow();   // chỉ worker đơn mới ghi KB trong batch
  renderBatchResult(combinedSummary(br), {
    resumed: opts.resume, executorNote: state.executorNote,
    watchdogStopped: br.watchdogStopped, watchdogSeconds: budget,
    stopRequested: br.stopRequested, zipName: finalZip ? (finalZip.name || "batch.zip") : "",
    note: (zipError ? "Gộp file batch.zip lỗi: " + zipError +
             " — kết quả từng game vẫn còn trong checkpoint (đã lưu vào IndexedDB). " : "") +
      (executor.kind === "pool"
        ? poolLabel(executor.poolSize) + ": mỗi worker 1 Pyodide + PB3 riêng" +
          (executor.poolSize > 1 ? " (~" + executor.poolSize + "x RAM so với 1 worker)" : "") +
          "; thao tác đơn vẫn dùng worker riêng như trước. "
        : "Chạy bằng 1 worker (" + state.executorNote + "). ") +
      (executor.kind === "pool"
        ? "Lưu ý: batch ở chế độ pool không ghi kết quả chạy vào KB (KB của các worker trong pool không gộp được file SQLite) — thêm game vào KB bằng tab Kho KB."
        : "") +
      (canContinue ? "Còn " + remain + " game chưa xong — bấm “Tiếp tục” để chạy tiếp." : "") +
      " Phân công: " + Object.keys(br.gamesPerSlot).sort().map((k) => "worker " + k + ": " + br.gamesPerSlot[k] + " game").join(" · ") + ".",
  });
  syncBatchCkptInfo();
  syncPoolUI();
  setStatus(canContinue
    ? "Batch đã dừng an toàn — còn " + remain + " game chưa xong, bấm “Tiếp tục” để chạy tiếp."
    : "Batch xong.");
}

// ---- Chọn số worker pool (Mục C9): đồng bộ ô chọn + ghi chú, lưu lựa chọn.
// Đổi N giữa chừng KHÔNG crash: nếu đang có đợt batch chạy thì pool cũ chạy
// nốt đợt này (ghi chú rõ "áp dụng từ đợt sau"); nếu không chạy gì thì hủy
// pool cũ để đợt sau tạo pool mới đúng N.
function syncPoolSizeUI(pendingNote) {
  const sel = $("batch-pool-size");
  const note = $("batch-pool-size-note");
  const n = getPoolSize();
  if (sel) sel.value = String(n);
  try { window.__PB3_POOL_SIZE = n; } catch (e) { /* bỏ qua */ }
  if (!note) return;
  const mem = deviceMemoryGB();
  const autoOne = mem !== null && mem <= POOL_SIZE_LOW_MEMORY_GB;
  let txt = "Mặc định 2 worker, song song thật (mỗi worker 1 thread hệ điều hành thật).";
  if (autoOne) txt += " Thiết bị này báo RAM " + mem + " GB nên tự động dùng 1 worker cho nhẹ RAM — đổi được ở ô trên.";
  else if (mem !== null) txt += " Thiết bị này báo RAM " + mem + " GB.";
  txt += " Đổi số worker lưu lại qua lần tải trang sau.";
  if (pendingNote) txt += " " + pendingNote;
  note.textContent = txt;
}
$("batch-pool-size").addEventListener("change", () => {
  const n = savePoolSize($("batch-pool-size").value);
  state.poolSize = n;
  const running = state.batchRun && !state.batchRun.finished;
  if (state.executor && !running) {
    // Không chạy gì: hủy pool cũ để đợt sau tạo pool mới đúng N.
    try { if (state.executor.destroy) state.executor.destroy(); } catch (e) { /* bỏ qua */ }
    state.executor = null;
    state.executorNote = "";
  }
  syncPoolSizeUI(running
    ? "Đã lưu " + n + " worker — đợt đang chạy giữ nguyên pool cũ, áp dụng từ đợt batch sau."
    : "Đã lưu " + n + " worker — áp dụng từ đợt batch sau.");
  syncPoolUI();
});
syncPoolSizeUI();

$("btn-batch-run").addEventListener("click", async () => {
  const manifest = $("batch-manifest").value.trim();
  if (!manifest) { showError("Hãy dán manifest JSON/CSV vào ô."); return; }
  if (!state.batchFiles.length) { showError("Hãy chọn các file game trước (nút chọn file ở trên)."); return; }
  const dryRun = $("batch-dryrun").checked;
  const resume = $("batch-resume").checked;
  if (resume) {
    const loaded = await loadMergedCkpt();
    if (!loaded.found) { showError("Đã tick “Tiếp tục từ checkpoint” nhưng chưa có checkpoint nào lưu (bấm “Nạp checkpoint đã lưu” trước)."); return; }
  }
  await runBatchFlow({ manifest, dryRun, resume });
});
$("btn-batch-continue").addEventListener("click", async () => {
  // "Tiếp tục": chạy tiếp phần chưa xong từ checkpoint IndexedDB, cùng
  // manifest/dry-run và cùng runId (thư mục batch cũ còn trong FS phiên).
  const br = state.batchRun;
  if (!br) { showError("Chưa có đợt batch nào để tiếp tục."); return; }
  await runBatchFlow({ manifest: br.manifest, dryRun: br.dryRun, resume: true });
});

// ---- KB ----
$("btn-kb-add").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  try {
    const { parsed } = await runOp("Thêm game vào KB", "kb_add", [inputPath()], inputFileArgs());
    await persistKbNow();
    refreshKbStatus();
    $("kb-result").innerHTML = "<p>Đã thêm game vào KB: id <b>" + parsed.game_id + "</b> · bundle <b>" +
      esc(parsed.bundle_id || "?") + "</b> · version " + esc(parsed.version || "?") + " · engine " +
      esc(parsed.engine || "?") + " · fingerprint " + esc(parsed.fingerprint) + "…</p>" +
      "<p class=note>KB đã tự lưu vào IndexedDB — tải lại trang vẫn còn. Nút “Tải KB về (.sqlite)” dưới đây để sao lưu ra file.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-query").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  try {
    const { parsed } = await runOp("Tra cứu KB", "kb_query", [inputPath()], inputFileArgs());
    if (!parsed.found) {
      $("kb-result").innerHTML = "<p>Chưa có profile cho game này trong KB. Lý do: " + esc(parsed.reason || "?") + "</p>";
      return;
    }
    const p = parsed.profile;
    $("kb-result").innerHTML = "<p>Tìm thấy profile: bundle <b>" + esc(p.game.bundle_id || "?") +
      "</b> · version " + esc(p.game.version || "?") + " · engine " + esc(p.game.engine || "?") +
      " · " + p.files.length + " file đã lưu · " + p.locales.length + " locale · " +
      Object.keys(p.choices || {}).length + " lựa chọn.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-list").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Liệt kê KB", "kb_list", [], null);
    let html = "<p>" + parsed.games.length + " game trong KB · dung lượng " + fmtBytes(parsed.size_bytes) + " · tự lưu vào IndexedDB.</p>";
    if (parsed.games.length) {
      html += "<table><tr><th>ID</th><th>Bundle</th><th>Version</th><th>Engine</th><th>Lần thấy cuối</th></tr>";
      for (const g of parsed.games) {
        html += "<tr><td>" + g.id + "</td><td>" + esc(g.bundle_id || "?") + "</td><td>" + esc(g.version || "?") +
          "</td><td>" + esc(g.engine || "?") + "</td><td>" + esc(g.last_seen || "") + "</td></tr>";
      }
      html += "</table>";
    }
    $("kb-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-export").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Xuất file KB", "kb_export", [], null, true);
    kbRecordExportNow();
    refreshKbStatus();
    $("kb-result").innerHTML = "<p>File <b>pb3_kb.sqlite</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy (" + fmtBytes(parsed.size) +
      ", " + parsed.games + " game). Giữ file này làm bản sao lưu — KB trong trình duyệt đã tự lưu vào IndexedDB, nhưng file .sqlite vẫn là bản sao lưu chắc chắn nhất (phòng khi trình duyệt xóa dữ liệu web).</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-import").addEventListener("click", async () => {
  const f = $("kb-import-file").files && $("kb-import-file").files[0];
  if (!f) { showError("Chưa chọn file KB. Hãy chọn file .sqlite đã tải về trước."); return; }
  try {
    const buf = await f.arrayBuffer();
    const { parsed } = await runOp("Nhập KB", "kb_import", ["/tmp/input/" + f.name], [{ name: f.name, data: buf }]);
    await persistKbNow();
    refreshKbStatus();
    const names = parsed.games.map((g) => esc(g.bundle_id || "?")).join(", ");
    $("kb-result").innerHTML = "<p>Đã nhập <b>" + parsed.imported + "</b> game vào KB: " + (names || "—") + "." +
      (parsed.skipped ? " Bỏ qua <b>" + parsed.skipped + "</b> game (xem cảnh báo dưới)." : "") + "</p>" +
      (parsed.warnings && parsed.warnings.length ? "<p class=note>Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>" : "");
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-forget").addEventListener("click", async () => {
  const id = $("kb-forget-id").value.trim();
  if (!id) { showError("Hãy nhập bundle id (hoặc id số) của game cần quên."); return; }
  try {
    const { parsed } = await runOp("Quên game", "kb_forget", [id], null);
    await persistKbNow();
    refreshKbStatus();
    $("kb-result").innerHTML = "<p>" + (parsed.forgotten ? "Đã quên game '" + esc(id) + "'." : "Không có game '" + esc(id) + "' trong KB.") + "</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Hỗ trợ ----
$("btn-support-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Ma trận hỗ trợ", "support_matrix", [], null);
    const d = parsed.data;
    let html = "<h3>Bảng plugin (" + d.plugins.length + ")</h3><table><tr><th>Plugin</th><th>Loại</th><th>Engine</th><th>Maturity hiệu lực</th><th>Game đã test</th><th>Ngày test</th><th>Giới hạn</th></tr>";
    for (const p of d.plugins) {
      html += "<tr><td>" + esc(p.id) + "</td><td>" + esc(p.kind) + "</td><td>" + esc(p.engine) +
        "</td><td>" + esc(p.maturity_effective) + "</td><td>" + p.games_tested + "</td><td>" + esc(p.test_date) +
        "</td><td>" + esc(p.limit) + "</td></tr>";
    }
    html += "</table><h3>Container / archive</h3><table><tr><th>Định dạng</th><th>Đọc</th><th>Ghi</th><th>Backend</th></tr>";
    for (const c of d.containers) {
      html += "<tr><td>" + esc(c.format) + "</td><td>" + esc(c.read) + "</td><td>" + esc(c.write) + "</td><td>" + esc(c.backend) + "</td></tr>";
    }
    html += "</table><h3>Unity</h3><table><tr><th>Định dạng</th><th>Đọc</th><th>Ghi</th><th>Backend</th><th>Maturity</th></tr>";
    for (const u of d.unity) {
      html += "<tr><td>" + esc(u.format) + "</td><td>" + esc(u.read) + "</td><td>" + esc(u.write) + "</td><td>" + esc(u.backend) + "</td><td>" + esc(u.maturity || "—") + "</td></tr>";
    }
    html += "</table><h3>Đã xác minh trên game thật</h3><ul>";
    for (const r of d.real_game) html += "<li><b>" + esc(r.muc) + "</b> — " + esc(r.chi_tiet) + " (nguồn: " + esc(r.nguon) + ")</li>";
    html += "</ul><h3>Chưa hỗ trợ</h3><ul>";
    for (const u of d.unsupported) html += "<li><b>" + esc(u.muc) + "</b> — " + esc(u.ly_do) + "</li>";
    html += "</ul><p class=note>Không có mục nào đạt M4. “Ngày test” hiện “chưa có” vì bản web không kèm file bằng chứng tests/corpus/*.json.</p>";
    $("support-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Nén / giải nén ----
// Chặn sai đuôi TRƯỚC khi gọi bridge (trước đây bấm Giải nén khi file đang
// chọn là .zip thì bridge gọi decompress_archive trên ZIP rồi lỗi E_CORRUPT;
// bấm Nén khi file đang chọn đã là .tar.xz cũng vô nghĩa).
$("btn-compress-run").addEventListener("click", async () => {
  if (!state.file) { showError("Nén lỗi: Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (/\.tar\.xz$/i.test(state.file.name)) {
    showError("Nén lỗi: File .tar.xz đã là file nén — hãy chọn file .ipa/.zip gốc để nén (hoặc bấm Giải nén để khôi phục file gốc).");
    return;
  }
  try {
    const preset = parseInt($("compress-preset").value, 10) || 6;
    const { parsed } = await runOp("Nén IPA", "compress_file", [inputPath(), preset], inputFileArgs(), true);
    $("compress-result").innerHTML =
      "<p>Gốc: <b>" + fmtBytes(parsed.original_size) + "</b> → nén: <b>" + fmtBytes(parsed.compressed_size) +
      "</b> · tỉ lệ " + parsed.ratio + ":1 (giảm " + parsed.saved_pct + "%) · " + parsed.entries +
      " entry · đích ≤400MB: " + (parsed.target_met ? "ĐẠT" : "KHÔNG ĐẠT — file vẫn xuất kèm ghi chú") +
      " · file <b>out.tar.xz</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>" +
      (parsed.ram_warning ? "<p>Cảnh báo RAM: " + esc(parsed.ram_warning) + "</p>" : "") +
      "<p>File nén ra là bản lưu trữ, KHÔNG phải IPA cài trực tiếp — dùng nút Giải nén để khôi phục.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-decompress-run").addEventListener("click", async () => {
  if (!state.file) { showError("Giải nén lỗi: Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  if (!/\.tar\.xz$/i.test(state.file.name)) {
    showError("Giải nén lỗi: File giải nén phải là .tar.xz — hãy chọn file out.tar.xz đã nén ra (nút Nén tạo ra file đó).");
    return;
  }
  try {
    const { parsed } = await runOp("Giải nén", "decompress_file", [inputPath()], inputFileArgs(), true);
    const fname = parsed.filename || parsed.original_name || "file gốc";
    $("compress-result").innerHTML =
      "<p>Khôi phục: <b>" + esc(fname) + "</b> · " + fmtBytes(parsed.restored_size) +
      " · " + parsed.entries + " entry · byte-identical với gốc: <b>" + (parsed.byte_identical ? "ĐÚNG" : "SAI") +
      "</b> · file gốc <b>" + esc(fname) + "</b> đã sẵn sàng để lưu — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy.</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Font ----
$("btn-font-scan").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Quét font", "font_scan", [inputPath()], inputFileArgs());
    $("font-result").innerHTML =
      "<p><b>" + esc(parsed.family || "?") + "</b> (" + esc(parsed.kind) + ") · " + parsed.num_glyphs +
      " glyph · tiếng Việt: <b>" + parsed.covered + "/" + parsed.total + " (" + parsed.pct + "%)</b></p>" +
      "<p>" + esc(parsed.summary) + "</p>" +
      (parsed.missing_chars && parsed.missing_chars.length
        ? "<p>Thiếu (hiện tối đa 50): " + esc(parsed.missing_chars.join(" ")) + "</p>" : "");
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-font-merge").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Gộp glyph tiếng Việt", "font_merge",
      [inputPath(), "/pb3web/fonts/BeVietnamPro-Regular.ttf"], inputFileArgs(), true);
    $("font-result").innerHTML =
      "<p>Đã gộp: thêm <b>" + parsed.added + "</b> ký tự · đã có sẵn " + parsed.already +
      " · font nguồn cũng thiếu " + parsed.donor_missing + " · copy " + parsed.glyphs_copied +
      " glyph · file <b>merged.ttf</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy</p>" +
      ((parsed.warnings || []).length ? "<p>Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>" : "");
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-font-replace").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Lấy font Be Vietnam Pro", "font_donor_file", [], null, true);
    $("font-result").innerHTML =
      "<p>File <b>BeVietnamPro-Regular.ttf</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy (" + fmtBytes(parsed.size) +
      ") — font đầy đủ tiếng Việt, dùng thay cho font của game.</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Đọc TMP_FontAsset (mục 7) ----
// Chỉ đọc tham số atlas SDF từ file .assets/.unity3d/IPA đã chọn: FaceInfo,
// bảng glyph, độ phủ tiếng Việt, tham chiếu Material/Texture. Kế hoạch glyph
// (chuỗi ký tự thiếu + tham số atlas cần giữ) được tải về dạng
// tmp_glyph_plan.json để nạp vào Unity Editor — atlas SDF chỉ tái tạo
// được trong Editor, web không sinh được SDF.
$("btn-font-tmp").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Đọc TMP_FontAsset", "tmp_font_read",
      [inputPath()], inputFileArgs(), true);
    const s = parsed.summary || {};
    let html = "<p><b>" + (s.assets || 0) + " TMP_FontAsset</b> (đọc đủ " +
      (s.parsed || 0) + ", một phần " + (s.partial || 0) + ", không đọc được bảng glyph " +
      (s.not_found || 0) + ") · tiếng Việt đủ " + (s.vietnamese_full || 0) +
      ", thiếu " + (s.vietnamese_partial || 0) + ", không có " + (s.vietnamese_none || 0) +
      " · file đã quét: " + (parsed.files_scanned || []).map(esc).join(", ") +
      (parsed.bundle_nodes ? " · node bundle đã đọc: " + parsed.bundle_nodes : "") + "</p>";
    for (const it of (parsed.assets || [])) {
      const fi = it.font_info || {};
      const vi = it.vietnamese || {};
      const gt = it.glyph_table || {};
      html += "<h3>" + esc(it.name || ("TMP #" + it.path_id)) + " — " + esc(it.status || "?") + "</h3>";
      if (fi.name != null) {
        html += "<p>Font nguồn: <b>" + esc(fi.name) + "</b> · PointSize " + fi.point_size +
          " · Scale " + fi.scale + " · LineHeight " + fi.line_height +
          " · Padding " + (fi.padding != null ? fi.padding : "?") +
          " · atlas <b>" + fi.atlas_width + "×" + fi.atlas_height + "</b>" +
          (fi.atlas_basis ? " (" + esc(fi.atlas_basis) + ")" : "") +
          " · glyph " + (gt.count != null ? gt.count : "?") + "/" + fi.declared_char_count + "</p>";
      } else {
        html += "<p>" + esc(it.reason || "Không đọc được FaceInfo.") + "</p>";
      }
      if (vi.required) {
        html += "<p>Tiếng Việt: <b>" + vi.covered + "/" + vi.required + " (" + vi.level + ")</b>" +
          " · dấu kết hợp: " + (vi.marks ? vi.marks.covered + "/" + vi.marks.required : "?") +
          (vi.missing && vi.missing.length ? " · thiếu: " + esc(vi.missing.join(" ")) : "") + "</p>";
      }
      const refs = it.refs || {};
      const refTxt = ["texture", "material", "font", "tmp_font_asset"]
        .map((k) => (refs[k] || []).map((r) => esc(r.name || ("#" + r.path_id))).join(", "))
        .filter((t) => t);
      if (refTxt.length) html += "<p>Tham chiếu: " + refTxt.join(" · ") + "</p>";
      if (it.problems && it.problems.length) {
        html += "<p class=warn>Vấn đề: " + it.problems.map(esc).join("; ") + "</p>";
      }
      if (it.status === "glyph_table_not_found" || it.status === "layout_unknown") {
        html += "<p class=warn>Bố cục TMP của bản này có thể khác giả định — xem chẩn đoán trong file kế hoạch glyph; không bịa danh sách ký tự.</p>";
      }
    }
    if (parsed.candidates && parsed.candidates.length) {
      html += "<p class=warn>Ứng viên chưa xác nhận class script (" + parsed.candidates.length +
        " MonoBehaviour, script ngoài file hoặc không đọc được) — chưa đưa vào kết quả, cần đối chiếu thủ công.</p>";
    }
    const plan = parsed.glyph_plan || {};
    if (plan.targets && plan.targets.length) {
      html += "<h3>Kế hoạch glyph cho Unity Editor</h3><p>Còn thiếu (gộp mọi asset): <b>" +
        (plan.missing_total || 0) + " ký tự</b> (" +
        (plan.missing_letters || []).join(" ") +
        (plan.missing_marks && plan.missing_marks.length ? " · dấu: " + plan.missing_marks.join(" ") : "") +
        ")</p><p>Chuỗi ký tự thiếu (dán vào ô Custom Characters của TMP Font Asset Creator):<br><b style=\"word-break:break-all\">" +
        esc(plan.chars_text || "") + "</b></p>" +
        "<p>File <b>tmp_glyph_plan.json</b> đã sẵn sàng để tải về — nạp vào script " +
        "<b>unity_editor/TMP_VietnameseAtlas.cs</b> (chạy trong Unity Editor, không chạy trên web). " +
        "Mức rẻ nhất: giữ nguyên material, chỉ đổi texture atlas + bảng glyph.</p>";
    } else {
      html += "<p>Không có asset nào đủ dữ liệu để lập kế hoạch glyph.</p>";
    }
    if (parsed.warnings && parsed.warnings.length) {
      html += "<p class=warn>Cảnh báo: " + parsed.warnings.map(esc).join("; ") + "</p>";
    }
    $("font-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Tạo font fallback TMP (mục C6) ----
// Kế hoạch fallback (JSON) từ TMP đã đọc: TMP_FontAsset MỚI chỉ chứa chữ
// Việt thiếu + đăng ký vào danh sách fallback của TMP_Settings. Web chỉ
// lập kế hoạch — atlas SDF của fallback chỉ sinh được trong Unity Editor
// (script C# kèm theo, tải về qua link dưới). Asset gốc KHÔNG đổi gì:
// script C6 không gán lại material/atlas của asset gốc (khác mục 7 sửa
// chính atlas gốc). Dùng lại kết quả quét TMP lần trước nếu cùng file
// (bridge cache) nên không quét lại cả IPA.
$("btn-font-fallback").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Tạo font fallback", "tmp_font_fallback",
      [inputPath()], inputFileArgs(), true);
    const plan = parsed.fallback_plan || {};
    const fbs = plan.fallbacks || [];
    let html = "<p><b>Kế hoạch font fallback</b> (mục C6) · cần tạo <b>" + fbs.length +
      " fallback</b> · đã đủ chữ Việt " + (plan.already_full_count || 0) +
      " asset · không lập được kế hoạch " + (plan.unplanned_count || 0) + " asset" +
      (parsed.scan_reused ? " · dùng lại kết quả quét TMP lần trước (không quét lại file)" : "") + "</p>";
    html += "<p>Chuỗi ký tự thiếu (gộp mọi asset, " + (plan.missing_total || 0) + " ký tự): <b style=\"word-break:break-all\">" +
      esc(plan.chars_text || "") + "</b></p>";
    for (const fb of fbs) {
      html += "<h3>" + esc(fb.name || "?") + " → " + esc(fb.fallback_name || "?") + "</h3>";
      html += "<p>Thiếu: <b>" + fb.missing_total + " ký tự</b>" +
        (fb.missing_letters && fb.missing_letters.length ? " · chữ: " + esc(fb.missing_letters.join(" ")) : "") +
        (fb.missing_marks && fb.missing_marks.length ? " · dấu: " + esc(fb.missing_marks.join(" ")) : "") + "</p>";
      html += "<p>Giữ tham số gốc: PointSize " + fb.base_point_size + " · Padding " +
        (fb.base_padding != null ? fb.base_padding : "?") + " · atlas gốc <b>" +
        fb.base_atlas_width + "×" + fb.base_atlas_height + "</b> → atlas fallback <b>" +
        fb.fallback_atlas_width + "×" + fb.fallback_atlas_height + "</b>" +
        " · font nguồn gợi ý: <b>" + esc(fb.source_font_hint || "?") + "</b>" +
        " · render mode: giữ nguyên của asset gốc (script đọc lại trong Editor)</p>";
      html += "<p>Material gốc: <b>không đổi</b>" +
        (fb.material_refs && fb.material_refs.length
          ? " (" + fb.material_refs.map((r) => esc(r.name || ("#" + r.path_id))).join(", ") + ")" : "") +
        " · file asset mới: <b>" + esc(fb.fallback_asset_path || "") + "</b></p>";
      if (fb.ascii_note) html += "<p class=warn>" + esc(fb.ascii_note) + "</p>";
    }
    if (plan.already_full && plan.already_full.length) {
      html += "<p>Đã đủ chữ Việt, không cần fallback: " +
        plan.already_full.map((a) => esc(a.name || "?")).join(", ") + "</p>";
    }
    if (plan.unplanned && plan.unplanned.length) {
      html += "<p class=warn>Không lập được kế hoạch (" + plan.unplanned.length + " asset — không đọc được bảng glyph nên không biết thiếu chữ nào): " +
        plan.unplanned.map((a) => esc(a.name || "?")).join(", ") + "</p>";
    }
    html += "<p>File <b>tmp_fallback_plan.json</b> đã sẵn sàng để tải về — nạp vào script " +
      "<b>unity_editor/TMP_VietnameseFallback.cs</b> (chạy trong Unity Editor, không chạy trên web). " +
      "<a href=\"unity_editor/TMP_VietnameseFallback.cs\" download=\"TMP_VietnameseFallback.cs\">Tải script C# về</a> " +
      "(hoặc chép từ repo: unity_editor/TMP_VietnameseFallback.cs). " +
      "Trong Editor: chọn asset gốc → menu BearTea → TMP → “4. Tạo font fallback tiếng Việt (từ file tmp_fallback_plan.json)” — " +
      "script tạo asset mới rồi đăng ký vào danh sách fallback của TMP_Settings.</p>";
    html += "<p class=warn>Ranh giới thật: kế hoạch này KHÔNG phải TMP_FontAsset dùng được ngay — atlas SDF của fallback chỉ sinh được trong Unity Editor (FontEngine). Fallback chỉ cứu chữ Việt thiếu.</p>";
    if (parsed.warnings && parsed.warnings.length) {
      html += "<p class=warn>Cảnh báo: " + parsed.warnings.map(esc).join("; ") + "</p>";
    }
    $("font-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Liệt kê font của game (phần D) ----
// Quét MỌI font trong file đã chọn (TTF/OTF/TTC rời, Font object nhúng
// class 128, TMP_FontAsset) qua bridge.font_list; mỗi font báo đủ bao
// nhiêu trên 142 mục + chữ/dấu nào thiếu. Chỉ đọc; danh sách đầy đủ
// nằm trong font_list.json để tải về qua thanh Lưu file.
$("btn-font-list").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Liệt kê font của game", "font_list",
      [inputPath()], inputFileArgs(), true);
    const s = parsed.summary || {};
    let html = "<p>Tìm được <b>" + (s.fonts || 0) + " font</b> · đủ tiếng Việt <b>" +
      (s.full || 0) + "</b> · thiếu " + (s.partial || 0) + " · không hỗ trợ " +
      (s.unsupported || 0) + " · không đọc được " + (s.unreadable || 0) +
      (parsed.scan_reused ? " · dùng lại kết quả liệt kê lần trước (không quét lại file)" : "") + "</p>";
    for (const f of (parsed.fonts || [])) {
      html += "<h3>" + esc(f.name || "?") + " — " + esc(f.kind) + " · " +
        (f.status === "full" ? "ĐỦ" : f.status === "partial" ? "THIẾU" :
         f.status === "unsupported" ? "KHÔNG HỖ TRỢ" : "KHÔNG ĐỌC ĐƯỢC") + "</h3>";
      html += "<p>Nguồn: " + esc(f.source || "?") +
        (f.size != null ? " · " + fmtBytes(f.size) : "") +
        (f.path_id != null ? " · path_id " + f.path_id : "") +
        (f.num_glyphs != null ? " · " + f.num_glyphs + " glyph" : "") + "</p>";
      if (f.covered != null) {
        html += "<p>Tiếng Việt: <b>" + f.covered + "/" + f.total + "</b>" +
          (f.status === "full" ? " · <b>ĐỦ</b>"
            : " · thiếu <b>" + f.missing_total + " ký tự</b>" +
              (f.missing && f.missing.length ? ": " + esc(f.missing.join(" ")) : "")) + "</p>";
      } else {
        html += "<p>" + esc(f.reason || "Không đọc được font.") + "</p>";
      }
      if (f.atlas_width != null) {
        html += "<p>Atlas: <b>" + f.atlas_width + "×" + f.atlas_height + "</b>" +
          (f.point_size != null ? " · PointSize " + f.point_size : "") + "</p>";
      }
    }
    if (!(parsed.fonts || []).length) {
      html += "<p>Không tìm được font nào trong file này.</p>";
    }
    if (parsed.tmp_candidates && parsed.tmp_candidates.length) {
      html += "<p class=warn>Có " + parsed.tmp_candidates.length +
        " MonoBehaviour chưa xác nhận được class script (script nằm ngoài file) — trong số đó có thể có TMP_FontAsset chưa liệt kê được; cần đối chiếu thủ công trong Unity Editor.</p>";
    }
    html += "<p>File <b>font_list.json</b> đã sẵn sàng để tải về — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy.</p>";
    if (parsed.warnings && parsed.warnings.length) {
      html += "<p class=warn>Cảnh báo: " + parsed.warnings.map(esc).join("; ") + "</p>";
    }
    $("font-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Dùng chung 1 font Be Vietnam Pro (phần D) ----
// Gom mọi font game về font Be Vietnam Pro đủ 142/142. Web làm ĐƯỢC thật:
// gộp glyph còn thiếu vào từng font TTF rời (file *.vi.ttf trong zip).
// TMP chỉ có kế hoạch fallback (atlas SDF chỉ sinh được trong Unity
// Editor); OTF/TTC/font nhúng chỉ thay được trong Editor. Kết quả tải về
// dạng font_unified.zip (plan JSON + TTF đã gộp + fallback plan + font donor).
$("btn-font-unify").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Dùng chung 1 font", "font_unify",
      [inputPath(), "/pb3web/fonts/BeVietnamPro-Regular.ttf"], inputFileArgs(), true);
    const plan = parsed.plan || {};
    const c = plan.counts || {};
    let html = "<p><b>Dùng chung 1 font: Be Vietnam Pro (đủ 142/142)</b> · gộp thật trên web <b>" +
      (c.merge_glyphs || 0) + " TTF</b> · fallback TMP " + (c.tmp_fallback || 0) +
      " · thay bằng font donor trong Editor " + (c.replace_with_donor || 0) +
      " · đã đủ " + (c.none_full || 0) + " · không đọc được " + (c.unreadable || 0) +
      (parsed.scan_reused ? " · dùng lại kết quả liệt kê lần trước (không quét lại file)" : "") + "</p>";
    for (const m of (parsed.merged || [])) {
      html += "<p>Đã gộp: <b>" + esc(m.output || "?") + "</b> (từ " + esc(m.name || "?") +
        ") · thêm " + m.added + " ký tự · copy " + m.glyphs_copied + " glyph" +
        (m.warnings && m.warnings.length ? " · cảnh báo: " + esc(m.warnings.join("; ")) : "") + "</p>";
    }
    for (const f of (plan.fonts || [])) {
      html += "<h3>" + esc(f.name || "?") + " — " + esc(f.kind) + "</h3>";
      html += "<p>" + esc(f.detail || "") + "</p>";
    }
    if (!(plan.fonts || []).length) {
      html += "<p>Không tìm được font nào trong file này nên không có gì để gom.</p>";
    }
    html += "<p>File <b>font_unified.zip</b> đã sẵn sàng để tải về — bấm “Lưu / Chia sẻ” ở thanh Lưu file bên dưới để lưu về máy. " +
      "Trong zip có: font_unify_plan.json (kế hoạch đầy đủ), các file *.vi.ttf đã gộp, " +
      "tmp_fallback_plan.json (nếu game có TMP_FontAsset) và BeVietnamPro-Regular.ttf.</p>";
    html += "<p>Các bước trong Unity Editor: " + (plan.editor_steps || []).map(esc).join(" ") + "</p>";
    html += "<p class=warn>Giới hạn thật: " + (plan.limits || []).map(esc).join(" ") + "</p>";
    if (parsed.warnings && parsed.warnings.length) {
      html += "<p class=warn>Cảnh báo: " + parsed.warnings.map(esc).join("; ") + "</p>";
    }
    $("font-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Chẩn đoán ----
$("btn-diagnose-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Chẩn đoán", "diagnose", [inputPath()], inputFileArgs());
    let html = "<p>Loại: <b>" + esc(parsed.kind) + "</b> · " + fmtBytes(parsed.size) + " · " + parsed.entries.length + " entry</p>";
    html += "<h3>Hex (512 byte đầu)</h3><pre>" + esc(parsed.hex.join("\n")) + "</pre>";
    html += "<h3>Strings (ASCII, tối đa 200)</h3><ul>";
    for (const s of parsed.strings) html += "<li>0x" + s.offset.toString(16) + ": " + esc(s.text) + "</li>";
    html += "</ul><p>" + esc(parsed.strings_note || "") + "</p>";
    $("diagnose-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Probe định dạng (mục C8) ----
// Probe file bất kỳ qua bridge.format_probe: trả báo cáo có cấu trúc
// (engine + độ tin cậy + bằng chứng, thao tác được/không được hỗ trợ,
// bước tiếp theo). Ô chọn file riêng ở đây KHÔNG qua gameExtOk — nhận
// mọi đuôi (.pck/.pak/.locres/.win/.translation/byte lạ...); nếu không
// chọn file riêng thì probe file đã chọn ở trang chính. File hoàn toàn
// lạ vẫn ra báo cáo “chưa hỗ trợ” (ok:true), không phải lỗi.
function probeReportHtml(r) {
  const f = r.file, e = r.engine;
  let html = "<p><b>File:</b> " + esc(f.name) + " · " + esc(f.size_human) +
    " · SHA-256: <code>" + esc(f.sha256.slice(0, 16)) + "…</code></p>";
  html += "<p><b>Định dạng:</b> " + esc(f.container_kind) +
    (f.pak_footer ? " (có magic footer PAK)" : "") +
    " · <b>Magic đầu file:</b> " + esc(f.magic_head || "không khớp magic nào đã biết") + "</p>";
  html += "<p><b>Engine:</b> " + esc(e.label) + " — <b>" + esc(e.level) + "</b>" +
    " (độ tin cậy " + e.confidence + ")";
  if (e.note) html += "<br><span class=note>" + esc(e.note) + "</span>";
  html += "</p>";
  if (e.evidence && e.evidence.length) {
    html += "<h3>Bằng chứng</h3><ul>";
    for (const ev of e.evidence) html += "<li>" + esc(ev.reason) + " (trọng số " + ev.weight + ")</li>";
    html += "</ul>";
  }
  const others = (r.candidates || []).filter((c) => c.engine !== e.id && c.evidence && c.evidence.length);
  if (others.length) {
    html += "<h3>Ứng viên khác</h3><ul>";
    for (const c of others) html += "<li>" + esc(c.label) + ": " + esc(c.level) +
      " (độ tin cậy " + c.confidence + ") — " + esc(c.evidence.join("; ")) + "</li>";
    html += "</ul>";
  }
  if (r.open_error) {
    html += "<p class=warn>Không mở được container: [" + esc(r.open_error.code) + "] " +
      esc(r.open_error.message) + " — báo cáo chỉ dựa trên tên + byte đầu của file ngoài.</p>";
  }
  if (r.entries && r.entries.total) {
    html += "<p><b>Entry trong container:</b> " + r.entries.total +
      (r.entries.truncated ? " (chỉ xét 400 tên đầu)" : "") + "</p>";
  }
  html += "<h3>Hỗ trợ được</h3>";
  if (r.supported && r.supported.length) {
    html += "<ul>";
    for (const s of r.supported) html += "<li>✅ " + esc(s.op) + "</li>";
    html += "</ul>";
  } else {
    html += "<p>Không có thao tác nào được hỗ trợ cho file này.</p>";
  }
  html += "<h3>Chưa hỗ trợ</h3><ul>";
  for (const u of (r.unsupported || [])) html += "<li>❌ " + esc(u.op) + " — " + esc(u.reason) + "</li>";
  html += "</ul><h3>Bước tiếp theo</h3><ul>";
  for (const s of (r.next_steps || [])) html += "<li>" + esc(s) + "</li>";
  html += "</ul>";
  if (r.notes && r.notes.length) {
    html += "<p class=note>" + r.notes.map((n) => esc(n)).join("<br>") + "</p>";
  }
  if (r.strings && r.strings.sample && r.strings.sample.length) {
    html += "<p class=note>Chuỗi ASCII tìm thấy trong " + fmtBytes(r.strings.scanned_bytes) +
      " đầu: " + r.strings.count + " chuỗi, vd: “" + esc(r.strings.sample.slice(0, 3).join("”, “")) + "”.</p>";
  }
  return html;
}
$("btn-probe-run").addEventListener("click", async () => {
  try {
    const probeInput = $("probe-file-input");
    const probeFile = probeInput && probeInput.files && probeInput.files[0];
    let args, files;
    if (probeFile) {
      const buf = await probeFile.arrayBuffer();
      args = ["/tmp/input/" + probeFile.name];
      files = [{ name: probeFile.name, data: buf }];
    } else if (state.file) {
      args = [inputPath()];
      files = inputFileArgs();
    } else {
      showError("Chưa chọn file — chọn file trong mục Probe định dạng hoặc chọn file game ở trang chính trước.");
      return;
    }
    const { parsed } = await runOp("Probe định dạng", "format_probe", args, files);
    const report = parsed.report;
    state.lastProbe = report;
    if (typeof window !== "undefined") window.__PB3_LAST_PROBE = report;
    $("probe-result").innerHTML = probeReportHtml(report);
    $("btn-probe-download").disabled = false;
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-probe-download").addEventListener("click", () => {
  if (!state.lastProbe) { showError("Chưa có báo cáo probe — bấm “Probe định dạng” trước."); return; }
  const safe = String(state.lastProbe.file.name || "file").replace(/[^A-Za-z0-9._-]+/g, "_");
  download("beartea-probe-" + safe + ".json", JSON.stringify(state.lastProbe, null, 2));
});

// ---- Chẩn đoán ứng dụng (mục C2) ----
// Trạng thái provider dịch lấy từ worker (op diag_state: cấu hình +
// dấu vết lần dịch gần nhất của chuỗi fallback C1, đã giàu ms/status/
// errName/kind từ gt.js); boot/font từ state.bootInfo; bộ nhớ từ
// navigator.storage.estimate(). "Tự kiểm tra" chạy 6 mục THẬT (boot,
// plugin, font, trích xuất fixture, dịch thử, IndexedDB) — mục nào chưa
// chạy được thì ghi "chưa kiểm chứng", không ghi PASS giả.
const APPDIAG_PROVIDERS = [
  { id: "proxy", label: "Proxy Cloudflare" },
  { id: "google", label: "Google trực tiếp" },
  { id: "mymemory", label: "MyMemory" },
  { id: "libretranslate", label: "LibreTranslate" },
  { id: "offline", label: "Từ điển offline" },
];
const SELFTEST_FIXTURE = "fixtures/selftest.zip";
const SELFTEST_TEXT = { hello: "Hello world", start: "Start game", settings: "Settings" };

function appdiagTraceEntry(trace, step) {
  const tr = Array.isArray(trace) ? trace : [];
  return tr.find((e) => e && e.step === step) || null;
}

function appdiagProviderRows(diagState) {
  const trace = diagState ? diagState.last_trace : null;
  let html = "<table><tr><th>Provider</th><th>Trạng thái</th><th>Lần gọi gần nhất</th></tr>";
  for (const p of APPDIAG_PROVIDERS) {
    let status;
    const e = appdiagTraceEntry(trace, p.id);
    if (p.id === "proxy") {
      status = diagState && diagState.proxy_configured ? "Đã cấu hình" : "Chưa cấu hình";
    } else if (p.id === "libretranslate") {
      status = diagState && diagState.libretranslate_configured ? "Đã cấu hình" : "Chưa cấu hình (mặc định tắt)";
    } else if (p.id === "google") {
      status = diagState && diagState.proxy_configured ? "Dùng sau proxy" : "Sẵn sàng (gọi trực tiếp)";
    } else if (p.id === "mymemory") {
      status = "Sẵn sàng (không cần key)";
    } else {
      status = "Luôn sẵn sàng (~100 từ/cụm)";
    }
    let last;
    if (!e) {
      last = "chưa gọi lần nào";
    } else if (e.skipped) {
      last = "bỏ qua — " + (e.detail || "");
    } else if (e.ok) {
      last = "OK" + (e.ms != null ? " · " + e.ms + " ms" : "");
    } else {
      last = "lỗi" + (e.status != null ? " · HTTP " + e.status : "") +
        (e.ms != null ? " · " + e.ms + " ms" : "") +
        (e.errName ? " · " + e.errName : "") +
        (e.kind ? " (" + e.kind + ")" : "") +
        (e.detail ? " — " + e.detail : "");
    }
    html += "<tr><td>" + esc(p.label) + "</td><td>" + esc(status) + "</td><td>" + esc(last) + "</td></tr>";
  }
  html += "</table>";
  return html;
}

async function refreshAppDiag() {
  const box = $("appdiag-status");
  if (!box) return;
  let diagState = null, diagError = null;
  try {
    const msg = await callWorkerRaw("diag_state", {});
    diagState = JSON.parse(msg.result);
  } catch (e) { diagError = e; }
  let mem = null;
  try {
    const s = await navigator.storage.estimate();
    mem = { quotaMB: Math.round(s.quota / 1048576), usageMB: Math.round(s.usage / 1048576) };
  } catch (e) { /* không đọc được quota */ }
  let html = "";
  if (state.booted && state.bootInfo) {
    const b = state.bootInfo, d = state.donor;
    html += "<p>Khởi động: <b>OK</b> · " + b.plugins + " plugin" +
      (Array.isArray(b.plugin_ids) ? " (" + b.plugin_ids.length + " id)" : "") +
      " · font đi kèm: " + (d && d.is_full ? "đủ " + d.covered + "/" + d.total : "chưa đủ 142/142 — xem tab Font") + "</p>";
  } else {
    html += "<p>Khởi động: <b>chưa khởi động</b> — Pyodide + PB3 chỉ tải khi cần (chọn file hoặc bấm “Tự kiểm tra”).</p>";
  }
  html += "<p>Bộ nhớ ước tính: " +
    (mem ? "đã dùng " + mem.usageMB + " MB / hạn mức " + mem.quotaMB + " MB" : "không đọc được") +
    (navigator.deviceMemory ? " · RAM thiết bị ~" + navigator.deviceMemory + " GB" : "") + "</p>";
  if (diagError) {
    html += "<p class=warn>Không đọc được trạng thái worker: " + esc(diagError.message) + "</p>";
  } else {
    html += appdiagProviderRows(diagState);
  }
  box.innerHTML = html;
  state.lastDiag = {
    when: new Date().toISOString(),
    booted: state.booted,
    boot: state.bootInfo || null,
    font_donor: state.donor || null,
    memory: mem,
    device_memory_gb: navigator.deviceMemory || null,
    diag_state: diagState,
    selftest: state.lastSelfTest || null,
  };
}

async function runSelfTest() {
  const btn = $("btn-selftest-run");
  const box = $("selftest-result");
  if (!btn || !box || btn.disabled) return;
  btn.disabled = true;
  const items = [];
  const render = () => {
    let html = "<h3>Kết quả tự kiểm tra</h3><table><tr><th>Mục</th><th>Kết quả</th><th>Chi tiết</th></tr>";
    for (const it of items) {
      html += "<tr><td>" + esc(it.name) + "</td><td><b>" + esc(it.status) + "</b></td><td>" + esc(it.detail || "") + "</td></tr>";
    }
    html += "</table>";
    box.innerHTML = html;
  };
  const check = async (name, fn) => {
    const it = { name, status: "ĐANG CHẠY…", detail: "" };
    items.push(it); render();
    const t0 = performance.now();
    try {
      it.detail = await fn();
      it.status = "PASS";
    } catch (e) {
      it.status = "FAIL";
      it.detail = String(e && e.message ? e.message : e);
    }
    it.detail += " (" + Math.round(performance.now() - t0) + " ms)";
    render();
  };
  try {
    await check("Khởi động PB3", async () => {
      const info = await ensureBoot();
      if (!info || !info.ok) throw new Error("boot trả về không OK");
      return "Pyodide + PB3 đã khởi động, " + info.plugins + " plugin";
    });
    await check("Danh sách plugin", async () => {
      const b = state.bootInfo;
      if (!b) throw new Error("chưa có thông tin boot");
      if (!Array.isArray(b.plugin_ids) || !b.plugin_ids.length) throw new Error("không đọc được danh sách plugin");
      if (b.plugin_ids.length !== b.plugins) throw new Error("số plugin (" + b.plugins + ") khác số id (" + b.plugin_ids.length + ")");
      return b.plugins + " plugin: " + b.plugin_ids.slice(0, 5).join(", ") + (b.plugin_ids.length > 5 ? ", …" : "");
    });
    await check("Font đi kèm (142 mục)", async () => {
      const msg = await callWorker("run", { fn: "font_donor_info", args: ["/pb3web/fonts/BeVietnamPro-Regular.ttf"] });
      const p = JSON.parse(msg.result);
      if (p && p.ok === false) throw new Error(p.error || "font_donor_info lỗi");
      if (!p.is_full) throw new Error("font thiếu: " + p.covered + "/" + p.total);
      return "Be Vietnam Pro đủ " + p.covered + "/" + p.total + " (" + p.num_glyphs + " glyph)";
    });
    await check("Trích xuất thử (fixture)", async () => {
      const res = await fetch(SELFTEST_FIXTURE);
      if (!res.ok) throw new Error("không tải được fixture tự kiểm tra (HTTP " + res.status + ")");
      const data = await res.arrayBuffer();
      const msg = await callWorker("run", { fn: "extract", args: ["/tmp/input/selftest.zip", null, ["en"]], files: [{ name: "selftest.zip", data }] });
      const parsed = JSON.parse(msg.result);
      if (parsed && parsed.ok === false) throw new Error(parsed.error || "extract lỗi");
      const f = (parsed.files || []).find((x) => x && x.exported === "English.json");
      if (!f) throw new Error("extract không ra English.json (được " + (parsed.files || []).length + " file)");
      const got = await callWorker("run", { fn: "read_extracted", args: ["English.json"] });
      const rp = JSON.parse(got.result);
      if (rp && rp.ok === false) throw new Error(rp.error || "không đọc được English.json");
      const kv = JSON.parse(rp.text);
      for (const [k, v] of Object.entries(SELFTEST_TEXT)) {
        if (kv[k] !== v) throw new Error("English.json thiếu/sai key " + k + " (được " + JSON.stringify(kv[k]) + ")");
      }
      return "English.json đủ 3 chuỗi (hello/start/settings), quyết định: " + parsed.decision;
    });
    await check("Dịch thử 1 chuỗi", async () => {
      const msg = await callWorkerRaw("diag_translate_probe", {});
      const p = JSON.parse(msg.result);
      if (!p.ok) throw new Error(p.error || "dịch thử lỗi");
      return "provider: " + (p.provider || "?") + " — “Hello world” → “" + p.text + "”";
    });
    await check("IndexedDB / KB", async () => {
      if (typeof KBStore === "undefined") throw new Error("KBStore chưa nạp (kb-store.js)");
      const probe = new KBStore("__selftest__", { debounceMs: 0 });
      const bytes = new Uint8Array([66, 101, 97, 114, 84, 101, 97]);
      try {
        await probe.saveNow(bytes);
        const back = await probe.load();
        if (!back || back.length !== bytes.length) throw new Error("đọc lại được " + (back ? back.length : 0) + " byte, cần " + bytes.length);
        for (let i = 0; i < bytes.length; i++) if (back[i] !== bytes[i]) throw new Error("byte " + i + " khác nhau khi đọc lại");
        return "ghi-đọc-xóa khóa thử __selftest__ trong IndexedDB OK";
      } finally {
        try { await probe.clear(); } catch (e) { /* dọn dẹp thất bại thì thôi */ }
      }
    });
  } finally {
    btn.disabled = false;
  }
  const passed = items.filter((i) => i.status === "PASS").length;
  state.lastSelfTest = { when: new Date().toISOString(), passed, total: items.length,
    items: items.map((i) => ({ name: i.name, status: i.status, detail: i.detail })) };
  await refreshAppDiag();
}

$("btn-appdiag-refresh").addEventListener("click", () => { refreshAppDiag(); });
$("btn-selftest-run").addEventListener("click", () => { runSelfTest(); });
$("btn-appdiag-export").addEventListener("click", async () => {
  if (!state.lastDiag) await refreshAppDiag();
  const payload = Object.assign({}, state.lastDiag, {
    when: new Date().toISOString(),
    selftest: state.lastSelfTest || null,
    ua: navigator.userAgent,
  });
  download("beartea-chan-doan.json", JSON.stringify(payload, null, 2));
});

// Hook cho harness/debug: trạng thái chẩn đoán gần nhất để kiểm chứng
// xuất JSON mà không cần đoán qua nút Lưu/Chia sẻ.
if (typeof window !== "undefined") {
  window.__PB3_LAST_DIAG = () => state.lastDiag || null;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Khởi động lười (Mục 6) ----
// Pyodide + PB3 CHỈ boot khi user chọn file lần đầu (handleChosenFile /
// handleBatchFiles gọi ensureBoot) hoặc khi gọi thao tác cần PB3
// (callWorker/runOp đều đi qua ensureBoot). Trước đó trang chỉ hiện
// shell tĩnh + dòng trạng thái dưới đây. Boot hỏng thì lần gọi sau thử
// lại được (bootPromise reset về null); đã boot rồi thì gọi lại không
// tốn gì.
async function ensureBoot() {
  if (state.booted) return state.bootInfo;
  if (bootPromise) return bootPromise;
  bootPromise = (async () => {
    setStatus("Đang khởi động… (tải Pyodide + PB3, lần đầu cần mạng)");
    progStart("Khởi động PB3");
    let msg;
    try {
      msg = await callWorkerRaw("boot", {});
    } catch (e) {
      bootPromise = null;
      progDone("Khởi động — lỗi");
      setStatus("Khởi động lỗi.");
      showError("Không khởi động được PB3: " + e.message + " Kiểm tra mạng rồi tải lại trang.");
      throw e;
    }
    const r = JSON.parse(msg.result);
    progDone("Khởi động xong");
    if (!r.boot.ok) {
      bootPromise = null;
      showError("Khởi động lỗi: " + r.boot.error);
      setStatus("Khởi động lỗi.");
      throw new Error("Khởi động lỗi: " + r.boot.error);
    }
    state.booted = true;
    state.bootInfo = r.boot;
    state.donor = r.donor;
    setStatus("Sẵn sàng — " + r.boot.plugins + " plugin đang chạy · font đi kèm: " +
      (r.donor && r.donor.is_full ? "đủ 142/142 (134 chữ cái + 8 dấu)" : "xem chi tiết ở tab Font"));
    // Mục 3: nạp KB đã lưu từ IndexedDB (nếu có), xin lưu trữ bền vững,
    // rồi cập nhật dòng trạng thái KB + nhắc sao lưu.
    restoreKbFromIdb().then((kb) => {
      refreshKbPersistStatus();
      refreshKbStatus(kb.restored
        ? "Đã khôi phục " + kb.games + " game từ lần dùng trước."
        : (kb.reason && kb.reason !== "empty"
          ? "Không khôi phục được KB đã lưu (" + kb.reason + ") — bắt đầu với KB trống; nút “Nhập KB (.sqlite)” vẫn khôi phục được từ file sao lưu."
          : null));
    });
    // Mục 4: preload checkpoint batch từ IndexedDB (nếu tab bị kill giữa
    // chừng lần trước) để user thấy ngay là có thể "Tiếp tục từ checkpoint".
    loadMergedCkpt().then((loaded) => {
      if (loaded.found) {
        state.loadedCheckpoint = ckptToJson(loaded.ckpt);
        state.lastCheckpoint = state.loadedCheckpoint;
        const done = Object.keys(loaded.ckpt.games).filter((k) => loaded.ckpt.games[k].status === "ok").length;
        $("batch-ckpt-info").textContent = "Có checkpoint đã lưu từ lần trước (" + loaded.from + ", " +
          done + " game đã xong) — tick “Tiếp tục từ checkpoint” rồi bấm Chạy batch để chạy tiếp, không làm lại từ đầu.";
      }
    });
    if ((r.boot.problems || []).length) {
      $("boot-problems").textContent = "Lưu ý khởi động: " + r.boot.problems.join("; ");
      $("boot-problems").hidden = false;
    }
    return state.bootInfo;
  })();
  return bootPromise;
}

// Hook cho harness/debug (giống window.__PB3_WATCHDOG_SECONDS): trạng
// thái boot để kiểm chứng lazy boot mà không cần đoán qua mạng.
if (typeof window !== "undefined") {
  window.__PB3_BOOT_STATE = () => ({ booted: state.booted, workerCreated: worker !== null });
}

// Trạng thái ban đầu: CHƯA boot — chờ user chọn file (Mục 6).
setStatus("Chưa khởi động — chọn file để bắt đầu (Pyodide + PB3 chỉ tải khi cần; lần đầu cần mạng, lần sau dùng được offline).");

// ---- Service Worker (Mục 6) ----
// Precache Pyodide + shell để lần thăm sau chạy offline. Đăng ký lỗi
// (trình duyệt không hỗ trợ / sw.js bị chặn) thì IM LẶNG — trang vẫn
// chạy bình thường, chỉ không có offline. Hook window.__PB3_NO_SW
// (đặt trước khi app.js chạy) để tắt hẳn SW khi cần kiểm chứng.
function registerServiceWorker() {
  try {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    if (typeof window !== "undefined" && window.__PB3_NO_SW) return;
    const p = navigator.serviceWorker.register("sw.js");
    if (p && typeof p.catch === "function") p.catch(() => { /* im lặng: không SW vẫn chạy */ });
  } catch (e) { /* im lặng: không SW vẫn chạy */ }
}
registerServiceWorker();

// ---- Tải trước Pyodide sau first paint (Mục C3) ----
// Sau khi vỏ trang đã hiện (first paint + load), tải trước các file
// Pyodide nặng ở chế độ nền, KHÔNG chặn boot, KHÔNG tạo worker:
// % thật theo byte đã tải/tổng (đọc stream từng file). File đã có trong
// Cache Storage (SW precache lúc install đã tải) thì bỏ qua, không tải
// đúp. File tải xong ghi vào cache của SW (hỏi qua GET_VERSION) hoặc
// cache prefetch riêng — SW dùng làm dự phòng khi cache chính trượt
// (xem sw.js), nên lần user bấm dùng tool thì boot đọc từ cache.
// Không chạy khi: trình duyệt bật tiết kiệm dữ liệu, đang offline,
// không có Cache Storage, hoặc hook window.__PB3_NO_PREFETCH được đặt
// (harness tắt để đo boot lạnh). Trì hoãn ~4 giây sau load để không
// tranh băng thông với SW precache + không phá kiểm chứng lazy boot
// (mục 6: 2 giây đầu sau tải trang không có request Pyodide nào).
const PYODIDE_PREFETCH_URLS = [
  "pyodide/pyodide.mjs",
  "pyodide/pyodide.asm.mjs",
  "pyodide/pyodide.asm.wasm",
  "pyodide/python_stdlib.zip",
  "pyodide/pyodide-lock.json",
  "pyodide/lz4-4.4.5-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
];
const PREFETCH_CACHE_FALLBACK = "beartea-prefetch-v1";
const prefetchState = { started: false, done: false, loadedBytes: 0,
  totalBytes: 0, filesDone: 0, filesTotal: PYODIDE_PREFETCH_URLS.length,
  cachedFiles: 0, fetchedFiles: 0,
  failedFiles: [], status: "idle" };
if (typeof window !== "undefined") {
  window.__PB3_PREFETCH_STATE = () => ({ ...prefetchState,
    pct: prefetchState.totalBytes > 0
      ? Math.round(prefetchState.loadedBytes / prefetchState.totalBytes * 100)
      : Math.round(prefetchState.filesDone / prefetchState.filesTotal * 100) });
}
function prefetchUpdateUi() {
  const box = $("pyodide-prefetch");
  const txt = $("pyodide-prefetch-text");
  const bar = $("pyodide-prefetch-bar");
  if (!box || !txt) return;
  const pct = prefetchState.totalBytes > 0
    ? Math.round(prefetchState.loadedBytes / prefetchState.totalBytes * 100)
    : Math.round(prefetchState.filesDone / prefetchState.filesTotal * 100);
  if (prefetchState.status === "done") {
    txt.textContent = "Pyodide đã tải trước xong (" + fmtBytes(prefetchState.totalBytes) +
      ", " + prefetchState.filesDone + "/" + prefetchState.filesTotal + " file — " +
      prefetchState.cachedFiles + " file đã có sẵn trong cache, " +
      prefetchState.fetchedFiles + " file tải mới) — lần dùng sau sẽ khởi động nhanh hơn.";
  } else if (prefetchState.status === "partial") {
    txt.textContent = "Tải trước Pyodide chưa xong hẳn (" + prefetchState.failedFiles.length +
      " file lỗi: " + prefetchState.failedFiles.join(", ") +
      ") — vẫn khởi động bình thường khi bạn chọn file.";
  } else if (prefetchState.status === "skipped") {
    txt.textContent = "Không tải trước Pyodide (" + (prefetchState.error || "trình duyệt không hỗ trợ") +
      ") — vẫn khởi động bình thường khi bạn chọn file.";
  } else {
    txt.textContent = "Đang tải trước Pyodide… " + pct + "% (" +
      fmtBytes(prefetchState.loadedBytes) + "/" +
      (prefetchState.totalBytes > 0 ? fmtBytes(prefetchState.totalBytes) : "?") +
      ") — chạy nền, không chặn khởi động.";
  }
  if (bar) { bar.value = pct; bar.hidden = prefetchState.status !== "running"; }
  box.hidden = false;
}
async function swCacheNameForPrefetch() {
  try {
    if (typeof navigator === "undefined" || !navigator.serviceWorker) return null;
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg || !reg.active) return null;
    const ver = await new Promise((resolve) => {
      const ch = new MessageChannel();
      const timer = setTimeout(() => resolve(null), 1000);
      ch.port1.onmessage = (ev) => { clearTimeout(timer); resolve(ev.data); };
      try { reg.active.postMessage({ type: "GET_VERSION" }, [ch.port2]); }
      catch (e) { clearTimeout(timer); resolve(null); }
    });
    return ver && ver.cache ? ver.cache : null;
  } catch (e) { return null; }
}
async function prefetchPyodide() {
  if (prefetchState.started) return { ...prefetchState };
  prefetchState.started = true;
  prefetchState.status = "running";
  if (typeof window !== "undefined" && window.__PB3_NO_PREFETCH) {
    prefetchState.status = "skipped"; prefetchState.error = "đã tắt bằng hook kiểm chứng";
    return { ...prefetchState };
  }
  if (typeof caches === "undefined" || typeof fetch === "undefined") {
    prefetchState.status = "skipped"; prefetchState.error = "trình duyệt không có Cache Storage";
    prefetchUpdateUi(); return { ...prefetchState };
  }
  try {
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      prefetchState.status = "skipped"; prefetchState.error = "đang offline";
      prefetchUpdateUi(); return { ...prefetchState };
    }
    if (typeof navigator !== "undefined" && navigator.connection && navigator.connection.saveData) {
      prefetchState.status = "skipped"; prefetchState.error = "đang bật tiết kiệm dữ liệu";
      prefetchUpdateUi(); return { ...prefetchState };
    }
  } catch (e) { /* không đọc được trạng thái mạng: cứ thử tải */ }
  prefetchUpdateUi();
  const cacheName = (await swCacheNameForPrefetch()) || PREFETCH_CACHE_FALLBACK;
  let cache = null;
  try { cache = await caches.open(cacheName); } catch (e) { cache = null; }
  for (const rel of PYODIDE_PREFETCH_URLS) {
    const url = new URL(rel, location.href).href;
    try {
      const cached = await caches.match(url);
      if (cached) {
        // Máy chủ có thể trả chunked (không có Content-Length — cả server
        // thử local lẫn GitHub Pages đều có thể như vậy): đếm byte thật
        // qua blob().size thay vì tin header.
        let len = parseInt(cached.headers.get("content-length") || "0", 10);
        if (!len) {
          try { len = (await cached.clone().blob()).size; } catch (e) { len = 0; }
        }
        if (len > 0) { prefetchState.loadedBytes += len; prefetchState.totalBytes += len; }
        prefetchState.filesDone++;
        prefetchState.cachedFiles++;
        prefetchUpdateUi();
        continue;
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const total = parseInt(res.headers.get("content-length") || "0", 10);
      // GitHub Pages (và nhiều CDN) nén gzip: Content-Length là kích thước
      // SAU NÉN nhưng res.body cho ra byte SAU GIẢI NÉN (vd pyodide.mjs:
      // 7.089 B nén / 17.758 B thật), nên got > total là bình thường — KHÔNG
      // được so byte với Content-Length khi response có Content-Encoding.
      // Stream đọc tới done + tính toàn vẹn của gzip đã đủ bảo đảm file
      // đầy đủ; gzip bị cắt thật thì reader reject và rơi vào catch dưới.
      const encoded = (res.headers.get("content-encoding") || "").trim() !== "";
      if (total > 0) prefetchState.totalBytes += total;
      const chunks = [];
      let got = 0;
      if (res.body && typeof res.body.getReader === "function") {
        const reader = res.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          got += value.byteLength;
          prefetchState.loadedBytes += value.byteLength;
          prefetchUpdateUi();
        }
      } else {
        const buf = new Uint8Array(await res.arrayBuffer());
        chunks.push(buf);
        got += buf.byteLength;
        prefetchState.loadedBytes += buf.byteLength;
      }
      if (!encoded && total > 0 && got < total) {
        // Máy chủ trả thiếu so với Content-Length (response không nén):
        // vẫn ghi những gì đã có, nhưng không tính là đủ file (để lần
        // sau tải lại). Khi có Content-Encoding thì không so byte (xem
        // ghi chú ở trên) nên nhánh này không bao giờ chạy nhầm do gzip.
        prefetchState.totalBytes -= total;
        prefetchState.failedFiles.push(rel + " (thiếu byte)");
        prefetchUpdateUi();
        continue;
      }
      if (cache) {
        try {
          await cache.put(url, new Response(new Blob(chunks), {
            headers: { "Content-Type": res.headers.get("Content-Type") || "application/octet-stream" },
          }));
        } catch (e) { /* cache đầy: boot vẫn tải lại từ mạng được */ }
      }
      prefetchState.filesDone++;
      prefetchState.fetchedFiles++;
      prefetchUpdateUi();
    } catch (e) {
      prefetchState.failedFiles.push(rel);
      prefetchUpdateUi();
    }
  }
  prefetchState.done = prefetchState.failedFiles.length === 0;
  prefetchState.status = prefetchState.done ? "done" : "partial";
  // Máy chủ trả chunked thì totalBytes có thể vẫn 0 dù đã đếm đủ byte
  // thật qua stream: chốt tổng bằng số byte đã tải để UI hiện đúng MB.
  if (prefetchState.totalBytes === 0 && prefetchState.loadedBytes > 0) {
    prefetchState.totalBytes = prefetchState.loadedBytes;
  }
  prefetchUpdateUi();
  return { ...prefetchState };
}
if (typeof window !== "undefined") {
  window.__PB3_START_PREFETCH = prefetchPyodide;
  const schedulePrefetch = () => {
    const start = () => setTimeout(() => { prefetchPyodide().catch(() => {}); }, 4000);
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(start, { timeout: 8000 });
    } else start();
  };
  if (document.readyState === "complete") schedulePrefetch();
  else window.addEventListener("load", schedulePrefetch);
}
