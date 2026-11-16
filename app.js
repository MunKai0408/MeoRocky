// app.js — Giao diện chính (main thread). Mọi xử lý nặng chạy trong worker.js
// (Pyodide + PB3); thread này chỉ vẽ giao diện, thanh tiến độ và tải file về.
//
// Thanh tiến độ (luật PB3 #152-153): % thật theo công việc đã xong, ETA tính
// từ thời gian thực đã chạy, tăng từng 1%, không nhảy cục, không báo láo.
// Khi tổng chưa biết (vd bước inventory của extract) thì chỉ hiện số đã xong
// và thời gian đã chạy, không bịa ETA.

const worker = new Worker("worker.js");
const $ = (id) => document.getElementById(id);

const state = {
  booted: false,
  bootInfo: null,
  donor: null,
  file: null,            // {name, size, data: ArrayBuffer}
  lastExtractKv: null,   // JSON text trích xuất được (để dịch tiếp)
  pending: new Map(),    // id -> {resolve, reject, op, t0, lastPct}
  nextId: 1,
  currentOp: null,
};

const WARN_BYTES = 300 * 1024 * 1024;   // >300MB: cảnh báo RAM tab trình duyệt
const MAX_BYTES = 1024 * 1024 * 1024;   // >1GB: từ chối (bridge cũng từ chối)

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
    worker.postMessage(Object.assign({ id, op }, payload), (payload.files || []).map(f => f.data));
  });
}

async function runOp(label, fn, args, files, fileOut) {
  clearError();
  progStart(label);
  try {
    const msg = await callWorker("run", { fn, args: args || [], files: files || [] });
    const parsed = JSON.parse(msg.result);
    progDone(label);
    if (fileOut && msg.file) download(msg.file.name, msg.file.data);
    return { parsed, file: msg.file || null };
  } catch (e) {
    progDone(label + " — lỗi");
    showError(label + " lỗi: " + e.message);
    throw e;
  }
}

function download(name, data) {
  const blob = new Blob([data], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function inputFileArgs() {
  if (!state.file) throw new Error("Chưa chọn file. Hãy chọn file ở trên trước.");
  return [{ name: state.file.name, data: state.file.data }];
}
function inputPath() { return "/tmp/input/" + state.file.name; }

// ---- Chọn file ----
$("file-input").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  if (f.size > MAX_BYTES) {
    showError("File " + fmtBytes(f.size) + " quá lớn cho bản web (>1GB). Dùng bản iSH cho file này.");
    return;
  }
  if (f.size > WARN_BYTES) {
    showError("Lưu ý: file " + fmtBytes(f.size) + " khá lớn, tab trình duyệt có thể tốn nhiều RAM. Vẫn thử chạy.");
  }
  f.arrayBuffer().then((buf) => {
    state.file = { name: f.name, size: f.size, data: buf };
    $("file-info").textContent = f.name + " · " + fmtBytes(f.size);
    $("file-info").hidden = false;
    clearError();
  });
});

// ---- Tabs ----
const tabs = ["list", "unity", "extract", "translate", "compress", "font", "diagnose"];
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    let html = "<p>Loại: <b>" + esc(parsed.kind) + "</b> · " + parsed.total + " entry</p><table><tr><th>Đường dẫn</th><th>Loại</th><th>Kích thước</th></tr>";
    for (const e of parsed.entries.slice(0, 200)) {
      html += "<tr><td>" + esc(e.path) + "</td><td>" + esc(e.kind) + "</td><td>" + fmtBytes(e.size) + "</td></tr>";
    }
    html += "</table>";
    if (parsed.entries.length > 200) html += "<p>…chỉ hiện 200 entry đầu.</p>";
    $("list-result").innerHTML = html;
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Unity inspect ----
$("btn-unity-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Inspect Unity", "unity_inspect", [inputPath()], inputFileArgs());
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    let html = "";
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
    $("unity-result").innerHTML = html || "<p>Không thấy TextAsset nào.</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Trích xuất ----
$("btn-extract-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Trích xuất text", "extract", [inputPath()], inputFileArgs(), true);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    let html = "<p>Kết quả: <b>" + esc(parsed.decision) + "</b> · " + parsed.total_entries + " entry · " + parsed.files.length + " file · đã tải về <b>extract.zip</b></p><table><tr><th>File</th><th>Plugin</th><th>Chuỗi</th><th>Locale</th></tr>";
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
$("translate-json-file").addEventListener("change", (ev) => {
  const f = ev.target.files && ev.target.files[0];
  if (!f) return;
  f.text().then((t) => { $("translate-input").value = t; });
});
$("btn-translate-run").addEventListener("click", async () => {
  const text = $("translate-input").value.trim();
  if (!text) { showError("Hãy dán JSON key-value vào ô, hoặc tải file JSON lên."); return; }
  try {
    const to = $("translate-to").value;
    const credit = $("translate-credit").checked;
    const { parsed } = await runOp("Dịch offline", "translate_json", [text, to, credit]);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("translate-output").value = JSON.stringify(parsed.translated, null, 2);
    let info = "Đã dịch " + parsed.total + " chuỗi · chưa dịch được " + parsed.untranslated + " chuỗi.";
    if (parsed.credit_key) info += " Đã gắn credit 'Việt Hoá Bởi Bear' vào key: " + parsed.credit_key + ".";
    if ((parsed.warnings || []).length) info += " Cảnh báo: " + parsed.warnings.join("; ");
    $("translate-info").textContent = info;
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-translate-download").addEventListener("click", () => {
  const t = $("translate-output").value;
  if (!t) { showError("Chưa có bản dịch để tải."); return; }
  download("translated.vi.json", new TextEncoder().encode(t).buffer);
});

// ---- Nén / giải nén ----
$("btn-compress-run").addEventListener("click", async () => {
  try {
    const preset = parseInt($("compress-preset").value, 10) || 6;
    const { parsed } = await runOp("Nén IPA", "compress_file", [inputPath(), preset], inputFileArgs(), true);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("compress-result").innerHTML =
      "<p>Gốc: <b>" + fmtBytes(parsed.original_size) + "</b> → nén: <b>" + fmtBytes(parsed.compressed_size) +
      "</b> · tỉ lệ " + parsed.ratio + ":1 (giảm " + parsed.saved_pct + "%) · " + parsed.entries +
      " entry · đích ≤400MB: " + (parsed.target_met ? "ĐẠT" : "KHÔNG ĐẠT — file vẫn xuất kèm ghi chú") +
      " · đã tải về <b>out.tar.xz</b></p>" +
      (parsed.ram_warning ? "<p>Cảnh báo RAM: " + esc(parsed.ram_warning) + "</p>" : "") +
      "<p>File nén ra là bản lưu trữ, KHÔNG phải IPA cài trực tiếp — dùng tab Giải nén để khôi phục.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-decompress-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Giải nén", "decompress_file", [inputPath()], inputFileArgs(), true);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("compress-result").innerHTML =
      "<p>Khôi phục: <b>" + esc(parsed.original_name || "file gốc") + "</b> · " + fmtBytes(parsed.restored_size) +
      " · " + parsed.entries + " entry · byte-identical với gốc: <b>" + (parsed.byte_identical ? "ĐÚNG" : "SAI") +
      "</b> · đã tải về <b>restored.bin</b> (đổi tên thành .ipa để dùng).</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Font ----
$("btn-font-scan").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Quét font", "font_scan", [inputPath()], inputFileArgs());
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("font-result").innerHTML =
      "<p>Đã gộp: thêm <b>" + parsed.added + "</b> ký tự · đã có sẵn " + parsed.already +
      " · font nguồn cũng thiếu " + parsed.donor_missing + " · copy " + parsed.glyphs_copied +
      " glyph · đã tải về <b>merged.ttf</b></p>" +
      ((parsed.warnings || []).length ? "<p>Cảnh báo: " + esc(parsed.warnings.join("; ")) + "</p>" : "");
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-font-replace").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Lấy font Be Vietnam Pro", "font_donor_file", [], null, true);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("font-result").innerHTML =
      "<p>Đã tải về <b>BeVietnamPro-Regular.ttf</b> (" + fmtBytes(parsed.size) +
      ") — font đầy đủ tiếng Việt, dùng thay cho font của game.</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Chẩn đoán ----
$("btn-diagnose-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Chẩn đoán", "diagnose", [inputPath()], inputFileArgs());
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    (r.donor && r.donor.is_full ? "đủ 112/112 ký tự Việt" : "xem chi tiết ở tab Font"));
  if ((r.boot.problems || []).length) {
    $("boot-problems").textContent = "Lưu ý khởi động: " + r.boot.problems.join("; ");
    $("boot-problems").hidden = false;
  }
}).catch((e) => {
  progDone("Khởi động — lỗi");
  setStatus("Khởi động lỗi.");
  showError("Không khởi động được PB3: " + e.message + " Kiểm tra mạng rồi tải lại trang.");
});
