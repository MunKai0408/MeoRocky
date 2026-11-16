// app.js — Giao diện chính (main thread). Mọi xử lý nặng chạy trong worker.js
// (Pyodide + PB3); thread này chỉ vẽ giao diện, thanh tiến độ và tải file về.
//
// Thanh tiến độ (luật PB3 #152-153): % thật theo công việc đã xong, ETA tính
// từ thời gian thực đã chạy, tăng từng 1%, không nhảy cục, không báo láo.
// Khi tổng chưa biết (vd bước inventory của extract) thì chỉ hiện số đã xong
// và thời gian đã chạy, không bịa ETA.

const worker = new Worker("worker.js", { type: "module" });
const $ = (id) => document.getElementById(id);

const state = {
  booted: false,
  bootInfo: null,
  donor: null,
  file: null,            // {name, size, lastModified, data: ArrayBuffer}
  batchFiles: [],        // [{name, size, lastModified, data}] cho tab Batch (nhiều file)
  // HOTFIX clone (Safari DataCloneError): file chỉ được gửi sang worker 1 lần.
  // Sau lần transfer đầu, ArrayBuffer trong transfer list bị detached
  // (byteLength = 0); gửi lại cùng buffer thì Safari ném "The object can
  // not be cloned" ở MỌI thao tác sau thao tác đầu tiên. Vì vậy:
  //  - fileSentFor/batchSentFor ghi lại file nào đã gửi (tên + size +
  //    lastModified); thao tác sau không kèm file nữa — worker đã giữ file
  //    trong FS /tmp/input từ lần gửi đầu, FS sống suốt phiên trang.
  //  - Khi gửi, luôn gửi f.data.slice(0) (bản copy) để buffer gốc không
  //    bao giờ bị detached, dù có phải gửi lại.
  //  - Reset cờ khi chọn file mới và khi một runOp lỗi (để thử lại gửi lại).
  fileSentFor: null,     // "tên|size|lastModified" của file đơn đã gửi
  batchSentFor: null,    // key tương tự cho lô batch
  lastCheckpoint: null,  // chuỗi checkpoint batch mới nhất (để tải về)
  loadedCheckpoint: null, // checkpoint nạp từ localStorage để resume
  lastExtractKv: null,   // JSON text trích xuất được (để dịch tiếp)
  lastDownload: null,    // {name, blob} file vừa tạo xong (để Chia sẻ / tải blob lại)
  unityBundles: [],      // bundle/.assets trong ZIP/IPA đang inspect
  pending: new Map(),    // id -> {resolve, reject, op, t0, lastPct}
  nextId: 1,
  currentOp: null,
};

const WARN_BYTES = 300 * 1024 * 1024;   // >300MB: cảnh báo RAM tab trình duyệt
const MAX_BYTES = 2 * 1024 * 1024 * 1024;   // >2GB: từ chối (bridge cũng từ chối)

// iOS Safari làm mờ file .ipa trong khung chọn dù input có accept=".ipa"
// (quirk UTI của picker iOS), nên 2 input chính (#file-input, #batch-files)
// KHÔNG dùng accept mà kiểm tra đuôi bằng JS sau khi chọn (không phân biệt
// hoa/thường). File khác đuôi thì báo lỗi thân thiện, không im lặng.
const GAME_EXTS = [".ipa", ".zip", ".unity3d", ".assets", ".ttf", ".json", ".tar.xz"];
function gameExtOk(name) {
  const lower = (name || "").toLowerCase();
  return GAME_EXTS.some((ext) => lower.endsWith(ext));
}
function gameExtError(name) {
  return "File \"" + name + "\" không phải định dạng PB3 hỗ trợ (" +
    GAME_EXTS.join(", ") + "). Hãy chọn file .ipa/.zip/.unity3d/.assets/.ttf/.json/.tar.xz.";
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
worker.onmessage = (ev) => {
  const msg = ev.data || {};
  if (msg.type === "status") { setStatus(msg.text); return; }
  if (msg.type === "progress") {
    const p = [...state.pending.values()][0];
    if (msg.kind === "load") {
      progSet(100 * msg.done / Math.max(1, msg.total), msg.done, msg.total);
      return;
    }
    if (!p) return;
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

worker.onerror = (e) => {
  showError("Worker lỗi: " + (e.message || e));
};

function callWorker(op, payload) {
  return new Promise((resolve, reject) => {
    const id = state.nextId++;
    state.pending.set(id, { resolve, reject, t0: performance.now(), lastPct: -1 });
    // HOTFIX clone: chỉ kèm file vào postMessage khi worker chưa có file đó.
    // File đơn: so bằng tên|size|lastModified với cờ fileSentFor.
    // Batch: so toàn bộ lô với cờ batchSentFor. File lẻ một lần (vd nhập KB)
    // không có cờ thì luôn gửi bản copy slice(0).
    let files = payload.files || [];
    let toSend = files;
    const filesKey = (list) => list.map((f) => f.name + "|" + f.size + "|" + (f.lastModified || 0)).join(";");
    try {
      if (files.length === 1 && state.file && files[0].data === state.file.data) {
        const key = filesKey([state.file]);
        if (state.fileSentFor === key) toSend = [];
        else { toSend = [{ name: files[0].name, data: files[0].data.slice(0) }]; state.fileSentFor = key; }
      } else if (files.length && state.batchFiles.length && files.length === state.batchFiles.length &&
                 files.every((f, i) => f.data === state.batchFiles[i].data)) {
        const key = filesKey(state.batchFiles);
        if (state.batchSentFor === key) toSend = [];
        else { toSend = files.map((f) => ({ name: f.name, data: f.data.slice(0) })); state.batchSentFor = key; }
      } else if (files.length) {
        toSend = files.map((f) => ({ name: f.name, data: f.data.slice(0) }));
      }
      worker.postMessage(Object.assign({ id, op }, payload, { files: toSend }),
        toSend.map((f) => f.data));
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
  progStart(label);
  try {
    const msg = await callWorker("run", { fn, args: args || [], files: files || [] });
    const parsed = JSON.parse(msg.result);
    // Lỗi từ bridge (parsed.ok === false) thì ném luôn để catch dưới hiện
    // banner CÓ NHÃN thao tác ("<Tên thao tác> lỗi: ..."). Trước đây các
    // handler tự kiểm tra parsed.ok rồi showError("Lỗi: " + ...) nên banner
    // mất nhãn, không biết thao tác nào lỗi.
    if (parsed && parsed.ok === false) {
      throw new Error(parsed.error || parsed.code || "Lỗi không rõ");
    }
    progDone(label);
    if (fileOut && msg.file) download(msg.file.name, msg.file.data);
    return { parsed, file: msg.file || null };
  } catch (e) {
    progDone(label + " — lỗi");
    showError(label + " lỗi: " + e.message);
    // HOTFIX clone: thao tác lỗi thì lần sau phải gửi lại file cho worker.
    state.fileSentFor = null;
    state.batchSentFor = null;
    throw e;
  }
}

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

function download(name, data) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: "application/octet-stream" });
  revokeLastDownloadUrl();   // chỉ giữ tối đa 1 blob URL sống
  const url = URL.createObjectURL(blob);
  lastDownloadUrl = url;
  // Hủy sau 10 phút — đủ cho daemon tải của Safari đọc xong cả file lớn
  // (95MB); không hủy sau 5 giây như trước (giết link giữa chừng khi daemon
  // đang ghi → file dở dang kẹt mãi trong Files > Tải về, Joseph 11:36).
  // Timer chỉ hủy đúng URL này (nếu đã có lần tải mới thì URL đã bị hủy ở
  // đầu lần tải đó rồi, timer cũ không được chạm vào URL mới).
  setTimeout(() => { if (lastDownloadUrl === url) revokeLastDownloadUrl(); }, 600000);
  state.lastDownload = { name, blob };
  const hint = $("download-hint");
  const shareBtn = $("btn-share-download");
  const blobBtn = $("btn-blob-download");
  if (canShareFile(blob, name)) {
    // ĐƯỜNG LƯU CHÍNH trên iOS: Chia sẻ native (bảng Chia sẻ → "Lưu vào
    // Tệp"), không qua download manager. Không tự bấm blob.
    if (hint) {
      hint.textContent = "File " + name + " (" + fmtBytes(blob.size) +
        ") đã sẵn sàng — bấm “Lưu / Chia sẻ” để lưu vào Files (cách chắc chắn nhất trên iPhone). " +
        "Nếu muốn tải bằng trình duyệt thì bấm “Tải về (blob)” bên dưới.";
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
        ") — trình duyệt sẽ hỏi thì bấm Tải về, giữ tab mở; file lớn có thể cần vài phút.";
      hint.hidden = false;
    }
    if (shareBtn) shareBtn.hidden = true;
    if (blobBtn) blobBtn.hidden = true;
  }
}
// Nút "Lưu / Chia sẻ": dùng Web Share API để lưu thẳng vào Files (hoặc app
// khác) — đường lưu chính trên iPhone, không qua download manager.
$("btn-share-download").addEventListener("click", async () => {
  const d = state.lastDownload;
  if (!d) return;
  try {
    const file = new File([d.blob], d.name, { type: "application/octet-stream" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file] });
    } else {
      showError("Chia sẻ lỗi: Trình duyệt này không hỗ trợ chia sẻ file — hãy bấm “Tải về (blob)” để tải bằng trình duyệt.");
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;   // user hủy bảng chia sẻ
    showError("Chia sẻ lỗi: " + (e.message || e));
  }
});
// Nút "Tải về (blob)": đường dự phòng — bấm anchor blob để trình duyệt tải
// về. URL có thể đã bị hủy sau 10 phút thì tạo lại từ blob đang giữ.
$("btn-blob-download").addEventListener("click", () => {
  const d = state.lastDownload;
  if (!d) return;
  if (!lastDownloadUrl) {
    lastDownloadUrl = URL.createObjectURL(d.blob);
    const u = lastDownloadUrl;
    setTimeout(() => { if (lastDownloadUrl === u) revokeLastDownloadUrl(); }, 600000);
  }
  fireBlobDownload(lastDownloadUrl, d.name);
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

// ---- Chọn file ----
$("file-input").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  if (!gameExtOk(f.name)) {
    showError(gameExtError(f.name));
    ev.target.value = "";
    return;
  }
  if (f.size > MAX_BYTES) {
    showError("File " + fmtBytes(f.size) + " quá lớn cho bản web (>2GB). Dùng bản iSH cho file này.");
    return;
  }
  if (f.size > WARN_BYTES) {
    showError("Lưu ý: file " + fmtBytes(f.size) + " khá lớn, tab trình duyệt có thể tốn nhiều RAM. Vẫn thử chạy.");
  }
  f.arrayBuffer().then((buf) => {
    state.file = { name: f.name, size: f.size, lastModified: f.lastModified || 0, data: buf };
    state.fileSentFor = null;   // HOTFIX clone: file mới thì gửi lại từ đầu
    $("file-info").textContent = f.name + " · " + fmtBytes(f.size);
    $("file-info").hidden = false;
    clearError();
  });
});

// ---- Tabs ----
const tabs = ["list", "unity", "extract", "translate", "mod", "batch", "kb", "support", "compress", "font", "diagnose"];
function showTab(name) {
  for (const t of tabs) {
    $("tab-" + t).hidden = (t !== name);
    $("btn-" + t).classList.toggle("active", t === name);
  }
}
for (const t of tabs) {
  $("btn-" + t).addEventListener("click", () => showTab(t));
}

// ---- Danh sách entry ----
$("btn-list-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Liệt kê entry", "list_entries", [inputPath()], inputFileArgs());
    let html = "<p>Loại: <b>" + esc(parsed.kind) + "</b> · " + parsed.total + " entry</p><table><tr><th>Đường dẫn</th><th>Loại</th><th>Kích thước</th></tr>";
    for (const e of parsed.entries.slice(0, 200)) {
      html += "<tr><td>" + esc(e.path) + "</td><td>" + esc(e.kind) + "</td><td>" + fmtBytes(e.size) + "</td></tr>";
    }
    html += "</table>";
    if (parsed.entries.length > 200) html += "<p>…chỉ hiện 200 entry đầu.</p>";
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
  } catch (e) { /* runOp đã báo lỗi có nhãn */ }
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
  } catch (e) { maybeShowIpaZipHint($("unity-ipa-hint")); }
});

// ---- Trích xuất ----
$("btn-extract-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Trích xuất text", "extract", [inputPath()], inputFileArgs(), true);
    let html = "<p>Kết quả: <b>" + esc(parsed.decision) + "</b> · " + parsed.total_entries + " entry · " + parsed.files.length + " file · file <b>extract.zip</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy</p><table><tr><th>File</th><th>Plugin</th><th>Chuỗi</th><th>Locale</th></tr>";
    for (const f of parsed.files) {
      html += "<tr><td>" + esc(f.path) + "</td><td>" + esc(f.plugin) + "</td><td>" + f.entries + "</td><td>" + esc(f.locale || "") + "</td></tr>";
    }
    html += "</table>";
    if ((parsed.errors || []).length) html += "<p>Lỗi: " + esc(parsed.errors.join("; ")) + "</p>";
    $("extract-result").innerHTML = html;
    // Giữ lại 1 file JSON để dịch tiếp (lấy file en.json nếu có).
    const en = parsed.files.find(f => /en\.json$/i.test(f.path || ""));
    if (en && en.exported) state.lastExtractKv = en.exported;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Dịch ----
// URL proxy dịch (Cloudflare Worker, mục 2): lưu localStorage, để trống =
// gọi trực tiếp Google như trước. Gửi sang worker ngay khi tải trang
// (worker nhận được cả trước khi Pyodide boot xong).
function sendProxyUrlToWorker() {
  let url = "";
  try { url = (localStorage.getItem("pb3_translate_proxy") || "").trim(); } catch (e) { /* localStorage không có */ }
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
$("translate-json-file").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  f.text().then((t) => { $("translate-input").value = t; });
});
$("btn-translate-run").addEventListener("click", async () => {
  const text = $("translate-input").value.trim();
  if (!text) { showError("Hãy dán JSON key-value vào ô, hoặc tải file JSON lên."); return; }
  const to = $("translate-to").value;
  const credit = $("translate-credit").checked;
  const useGoogle = $("translate-google").checked;
  const btn = $("btn-translate-run");
  const cancelBtn = $("btn-translate-cancel");
  btn.disabled = true;
  if (useGoogle) cancelBtn.hidden = false;
  try {
    const fn = useGoogle ? "translate_json_google" : "translate_json";
    const label = useGoogle ? "Dịch (Google)" : "Dịch offline";
    const { parsed } = await runOp(label, fn, [text, to, credit]);
    $("translate-output").value = JSON.stringify(parsed.translated, null, 2);
    let info = "";
    if (useGoogle) {
      if (parsed.cancelled) info += "Đã hủy giữa chừng — ";
      if (parsed.translated_by === "google") {
        info += "Dịch bằng Google: " + parsed.google_count + " chuỗi.";
      } else if (parsed.translated_by === "mixed") {
        info += "Dịch bằng Google: " + parsed.google_count + " chuỗi · từ điển offline: " +
          parsed.glossary_count + " chuỗi.";
      } else {
        info += "Google không dùng được — đã dịch bằng từ điển offline (" +
          parsed.glossary_count + " chuỗi).";
      }
      if (parsed.network_error) info += " Lý do: " + parsed.network_error;
    } else {
      info += "Dịch bằng từ điển offline: " + parsed.total + " chuỗi.";
    }
    info += " Chưa dịch được " + parsed.untranslated + " chuỗi.";
    if (parsed.credit_key) info += " Đã gắn credit 'Việt Hoá Bởi Bear' vào key: " + parsed.credit_key + ".";
    if ((parsed.warnings || []).length) info += " Cảnh báo: " + parsed.warnings.join("; ");
    $("translate-info").textContent = info;
  } catch (e) { /* đã báo lỗi */ }
  finally {
    btn.disabled = false;
    cancelBtn.hidden = true;
  }
});
$("btn-translate-cancel").addEventListener("click", () => {
  // Worker đang chạy dịch async vẫn nhận được message này giữa các chuỗi.
  worker.postMessage({ op: "cancel_translate" });
  $("translate-info").textContent = "Đang hủy… (dừng sau chuỗi hiện tại, giữ lại phần đã dịch)";
});
$("btn-translate-download").addEventListener("click", () => {
  const t = $("translate-output").value;
  if (!t) { showError("Chưa có bản dịch để tải."); return; }
  download("translated.vi.json", new TextEncoder().encode(t).buffer);
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
  if ((parsed.outputs || []).length) html += "<p>File đã ghi, đã sẵn sàng để lưu: <b>" + esc(parsed.outputs.join(", ")) + "</b> — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy</p>";
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
  } catch (e) { /* đã báo lỗi */ }
}
$("btn-mod-dryrun").addEventListener("click", () => runMod(false));
$("btn-mod-write").addEventListener("click", () => runMod(true));

// ---- Batch ----
$("batch-files").addEventListener("change", async (ev) => {
  const list = [...(ev.target.files || [])];
  state.batchFiles = [];
  let rejected = 0;
  for (const f of list) {
    if (!gameExtOk(f.name)) { showError(gameExtError(f.name)); rejected++; continue; }
    if (f.size > MAX_BYTES) { showError("File " + f.name + " quá lớn cho bản web (>2GB)."); continue; }
    state.batchFiles.push({ name: f.name, size: f.size, lastModified: f.lastModified || 0, data: await f.arrayBuffer() });
  }
  state.batchSentFor = null;   // HOTFIX clone: lô mới thì gửi lại từ đầu
  ev.target.value = "";
  if (state.batchFiles.length) {
    $("batch-files-info").textContent = state.batchFiles.length + " file: " +
      state.batchFiles.map(f => f.name + " (" + fmtBytes(f.size) + ")").join(", ");
    $("batch-files-info").hidden = false;
    if (!rejected) clearError();
  }
});
$("btn-batch-load-ckpt").addEventListener("click", () => {
  try {
    const saved = localStorage.getItem("pb3_batch_checkpoint");
    if (!saved) { $("batch-ckpt-info").textContent = "Chưa có checkpoint nào lưu trong trình duyệt này."; return; }
    state.loadedCheckpoint = saved;
    $("batch-ckpt-info").textContent = "Đã nạp checkpoint đã lưu (" + saved.length + " ký tự). Tick “Tiếp tục từ checkpoint” rồi bấm Chạy batch.";
  } catch (e) {
    $("batch-ckpt-info").textContent = "Không đọc được localStorage: " + e.message;
  }
});
$("btn-batch-dl-ckpt").addEventListener("click", () => {
  if (!state.lastCheckpoint) { showError("Chưa có checkpoint (chạy batch 1 lần trước đã)."); return; }
  download("checkpoint.json", new TextEncoder().encode(state.lastCheckpoint).buffer);
});
$("btn-batch-run").addEventListener("click", async () => {
  const manifest = $("batch-manifest").value.trim();
  if (!manifest) { showError("Hãy dán manifest JSON/CSV vào ô."); return; }
  if (!state.batchFiles.length) { showError("Hãy chọn các file game trước (nút chọn file ở trên)."); return; }
  const dryRun = $("batch-dryrun").checked;
  const resume = $("batch-resume").checked;
  const ckpt = resume ? state.loadedCheckpoint : null;
  if (resume && !ckpt) { showError("Đã tick “Tiếp tục từ checkpoint” nhưng chưa nạp checkpoint (bấm “Nạp checkpoint đã lưu” trước)."); return; }
  try {
    const { parsed } = await runOp("Chạy batch (tuần tự)", "batch_run",
      [manifest, dryRun, ckpt, resume], state.batchFiles, true);
    const s = parsed.summary;
    if (parsed.checkpoint) {
      state.lastCheckpoint = parsed.checkpoint;
      try { localStorage.setItem("pb3_batch_checkpoint", parsed.checkpoint); } catch (e) { /* localStorage đầy/không có: vẫn tải file được */ }
    }
    let html = "<p>Batch " + (parsed.dry_run ? "<b>dry-run</b>" : "<b>chạy thật</b>") +
      " · tuần tự (jobs=1) · " + s.ok + " ok / " + s.failed + " lỗi / " + s.skipped +
      " bỏ qua / " + s.total + " game · " + s.elapsed_s + " giây" +
      (parsed.resumed ? " · đã tiếp tục từ checkpoint" : "") +
      " · file <b>batch.zip</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy (summary, checkpoint, index, từng game)</p>";
    html += "<table><tr><th>#</th><th>Game</th><th>Mode</th><th>Kết quả</th><th>Lỗi</th><th>Giây</th><th>Ghi chú</th></tr>";
    for (const g of s.games || []) {
      html += "<tr><td>" + g.index + "</td><td>" + esc(g.path.split("/").pop()) + "</td><td>" + esc(g.mode) +
        "</td><td>" + esc(g.status) + "</td><td>" + esc(g.error_code || "") + " " + esc(g.error_message || "") +
        "</td><td>" + g.elapsed_s + "</td><td>" + esc(g.note || "") + "</td></tr>";
    }
    html += "</table><p class=note>" + esc(parsed.note || "") + "</p>";
    $("batch-result").innerHTML = html;
    $("batch-ckpt-info").textContent = parsed.checkpoint
      ? "Checkpoint mới đã lưu vào trình duyệt (" + parsed.checkpoint.length + " ký tự) — chạy lại với “Tiếp tục từ checkpoint” để bỏ qua game đã xong."
      : "";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- KB ----
$("btn-kb-add").addEventListener("click", async () => {
  if (!state.file) { showError("Chưa chọn file. Hãy chọn file ở trên trước."); return; }
  try {
    const { parsed } = await runOp("Thêm game vào KB", "kb_add", [inputPath()], inputFileArgs());
    $("kb-result").innerHTML = "<p>Đã thêm game vào KB: id <b>" + parsed.game_id + "</b> · bundle <b>" +
      esc(parsed.bundle_id || "?") + "</b> · version " + esc(parsed.version || "?") + " · engine " +
      esc(parsed.engine || "?") + " · fingerprint " + esc(parsed.fingerprint) + "…</p>" +
      "<p class=note>KB chỉ sống trong phiên này — tải lại trang là mất, trừ khi tải file KB về.</p>";
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
    let html = "<p>" + parsed.games.length + " game trong KB · dung lượng " + fmtBytes(parsed.size_bytes) + " · chỉ trong phiên này.</p>";
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
    $("kb-result").innerHTML = "<p>File <b>pb3_kb.sqlite</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy (" + fmtBytes(parsed.size) +
      ", " + parsed.games + " game). Giữ file này — tải lại trang thì KB trong trình duyệt trống lại; nhập lại bằng nút “Nhập KB (.sqlite)” trên tab này.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-import").addEventListener("click", async () => {
  const f = $("kb-import-file").files && $("kb-import-file").files[0];
  if (!f) { showError("Chưa chọn file KB. Hãy chọn file .sqlite đã tải về trước."); return; }
  try {
    const buf = await f.arrayBuffer();
    const { parsed } = await runOp("Nhập KB", "kb_import", ["/tmp/input/" + f.name], [{ name: f.name, data: buf }]);
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
      " · file <b>out.tar.xz</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy</p>" +
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
      "</b> · file gốc <b>" + esc(fname) + "</b> đã sẵn sàng để lưu — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy.</p>";
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
      " glyph · file <b>merged.ttf</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy</p>" +
      ((parsed.warnings || []).length ? "<p>Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>" : "");
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-font-replace").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Lấy font Be Vietnam Pro", "font_donor_file", [], null, true);
    $("font-result").innerHTML =
      "<p>File <b>BeVietnamPro-Regular.ttf</b> đã sẵn sàng — bấm “Lưu / Chia sẻ” ở mục 1 để lưu về máy (" + fmtBytes(parsed.size) +
      ") — font đầy đủ tiếng Việt, dùng thay cho font của game.</p>";
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

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Khởi động ----
setStatus("Đang khởi động… (tải Pyodide + PB3, lần đầu cần mạng)");
progStart("Khởi động PB3");
callWorker("boot", {}).then((msg) => {
  const r = JSON.parse(msg.result);
  progDone("Khởi động xong");
  if (!r.boot.ok) { showError("Khởi động lỗi: " + r.boot.error); setStatus("Khởi động lỗi."); return; }
  state.booted = true;
  state.bootInfo = r.boot;
  state.donor = r.donor;
  setStatus("Sẵn sàng — " + r.boot.plugins + " plugin đang chạy · font đi kèm: " +
    (r.donor && r.donor.is_full ? "đủ 142/142 (134 chữ cái + 8 dấu)" : "xem chi tiết ở tab Font"));
  if ((r.boot.problems || []).length) {
    $("boot-problems").textContent = "Lưu ý khởi động: " + r.boot.problems.join("; ");
    $("boot-problems").hidden = false;
  }
}).catch((e) => {
  progDone("Khởi động — lỗi");
  setStatus("Khởi động lỗi.");
  showError("Không khởi động được PB3: " + e.message + " Kiểm tra mạng rồi tải lại trang.");
});
