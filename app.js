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
  file: null,            // {name, size, data: ArrayBuffer}
  batchFiles: [],        // [{name, size, data}] cho tab Batch (nhiều file)
  lastCheckpoint: null,  // chuỗi checkpoint batch mới nhất (để tải về)
  loadedCheckpoint: null, // checkpoint nạp từ localStorage để resume
  lastExtractKv: null,   // JSON text trích xuất được (để dịch tiếp)
  pending: new Map(),    // id -> {resolve, reject, op, t0, lastPct}
  nextId: 1,
  currentOp: null,
};

const WARN_BYTES = 300 * 1024 * 1024;   // >300MB: cảnh báo RAM tab trình duyệt
const MAX_BYTES = 2 * 1024 * 1024 * 1024;   // >2GB: từ chối (bridge cũng từ chối)

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
    showError("File " + fmtBytes(f.size) + " quá lớn cho bản web (>2GB). Dùng bản iSH cho file này.");
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
  if ((parsed.outputs || []).length) html += "<p>File đã ghi, đã tải về: <b>" + esc(parsed.outputs.join(", ")) + "</b></p>";
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("mod-result").innerHTML = modResultHtml(parsed);
  } catch (e) { /* đã báo lỗi */ }
}
$("btn-mod-dryrun").addEventListener("click", () => runMod(false));
$("btn-mod-write").addEventListener("click", () => runMod(true));

// ---- Batch ----
$("batch-files").addEventListener("change", async (ev) => {
  const list = [...(ev.target.files || [])];
  state.batchFiles = [];
  for (const f of list) {
    if (f.size > MAX_BYTES) { showError("File " + f.name + " quá lớn cho bản web (>2GB)."); continue; }
    state.batchFiles.push({ name: f.name, size: f.size, data: await f.arrayBuffer() });
  }
  if (state.batchFiles.length) {
    $("batch-files-info").textContent = state.batchFiles.length + " file: " +
      state.batchFiles.map(f => f.name + " (" + fmtBytes(f.size) + ")").join(", ");
    $("batch-files-info").hidden = false;
    clearError();
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    const s = parsed.summary;
    if (parsed.checkpoint) {
      state.lastCheckpoint = parsed.checkpoint;
      try { localStorage.setItem("pb3_batch_checkpoint", parsed.checkpoint); } catch (e) { /* localStorage đầy/không có: vẫn tải file được */ }
    }
    let html = "<p>Batch " + (parsed.dry_run ? "<b>dry-run</b>" : "<b>chạy thật</b>") +
      " · tuần tự (jobs=1) · " + s.ok + " ok / " + s.failed + " lỗi / " + s.skipped +
      " bỏ qua / " + s.total + " game · " + s.elapsed_s + " giây" +
      (parsed.resumed ? " · đã tiếp tục từ checkpoint" : "") +
      " · đã tải về <b>batch.zip</b> (summary, checkpoint, index, từng game)</p>";
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("kb-result").innerHTML = "<p>Đã tải về <b>pb3_kb.sqlite</b> (" + fmtBytes(parsed.size) +
      ", " + parsed.games + " game). Giữ file này — tải lại trang thì KB trong trình duyệt trống lại; nhập lại bằng nút “Nhập KB (.sqlite)” trên tab này.</p>";
  } catch (e) { /* đã báo lỗi */ }
});
$("btn-kb-import").addEventListener("click", async () => {
  const f = $("kb-import-file").files && $("kb-import-file").files[0];
  if (!f) { showError("Chưa chọn file KB. Hãy chọn file .sqlite đã tải về trước."); return; }
  try {
    const buf = await f.arrayBuffer();
    const { parsed } = await runOp("Nhập KB", "kb_import", ["/tmp/input/" + f.name], [{ name: f.name, data: buf }]);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
    $("kb-result").innerHTML = "<p>" + (parsed.forgotten ? "Đã quên game '" + esc(id) + "'." : "Không có game '" + esc(id) + "' trong KB.") + "</p>";
  } catch (e) { /* đã báo lỗi */ }
});

// ---- Hỗ trợ ----
$("btn-support-run").addEventListener("click", async () => {
  try {
    const { parsed } = await runOp("Ma trận hỗ trợ", "support_matrix", [], null);
    if (!parsed.ok) { showError("Lỗi: " + parsed.error); return; }
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
