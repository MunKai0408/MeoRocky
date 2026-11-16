/* worker-pool.js — Pool Web Worker cho Batch (Mục 4; C9: số worker cấu
 * hình được 1–2, mặc định 2, tự động 1 khi RAM thiết bị ≤ 4 GB).
 *
 * Điều chỉnh từ worker-pool.js trong gói nâng cấp web-upgrade của Joseph:
 *  - Script classic (KHÔNG phải ES module) để app.js (classic) dùng trực
 *    tiếp qua tên toàn cục WorkerPool — không đổi app.js thành module.
 *  - BỎ timeout từng job kiểu terminate worker: terminate giữa chừng sẽ
 *    mất hết tiến độ game đang chạy (FS worker + checkpoint bay màu).
 *    Thay vào đó watchdog HỢP TÁC nằm trong worker.js (batch_begin đặt
 *    hạn, worker tự từ chối game mới khi quá hạn — xem WATCHDOG_SECONDS
 *    ở đầu worker.js). Pool chỉ terminate khi worker BỊ LỖI (onerror) hoặc
 *    boot thất bại, rồi tạo lại slot mới cho các job còn lại.
 *  - Worker phải là module worker (Pyodide yêu cầu), mỗi worker 1 instance
 *    Pyodide + PB3 riêng — KHÔNG dùng pthreads, KHÔNG SharedArrayBuffer,
 *    KHÔNG COOP/COEP. Giá phải trả: mỗi worker thêm tốn ~1x RAM (mỗi
 *    Pyodide + PB3) — pool 2 worker tốn ~2x RAM so với 1 worker; trên
 *    iPhone RAM thấp có thể nặng — chưa đo trên iPhone thật.
 *    Pool lỗi thì app.js rơi về 1 worker (executor đơn, xem app.js).
 *  - Giao thức worker (worker.js đã tuân theo):
 *      worker -> {type:'ready'} KHÔNG dùng; pool coi reply của op "boot"
 *        ({id:'boot-<i>', ok:true}) là "worker đã sẵn sàng".
 *      main   -> {id, op:'run', fn, args, files:[{name, data:ArrayBuffer}]}
 *      worker -> {id, ok:true, result, file?} | {id, ok:false, error}
 *      worker -> {type:'status'|'progress', ...} (không có id: báo tiến độ)
 *  - Mỗi job có thể ghim vào 1 slot (runOn) — cần cho bước gộp zip cuối
 *    đợt (batch_zip phải chạy đúng worker giữ output, batch_merge_zip phải
 *    chạy đúng worker giữ thư mục đích).
 *  - Dedup file theo slot (giữ đảm bảo "gửi 1 lần" của hotfix clone):
 *    FS của worker sống suốt phiên nên mỗi file chỉ gửi 1 lần cho mỗi slot;
 *    game sau trên cùng slot dùng lại file đã có trong /tmp/input.
 *    Slot respawn (worker mới, FS trống) thì sentFiles reset theo.
 *    Khóa = tên|cỡ|lastModified (khớp quy ước fileSentFor của app.js).
 *
 * Cách dùng (xem app.js):
 *   const pool = new WorkerPool('worker.js', { size: 2, onSlotChange });
 *   const ready = await pool.ready();   // true nếu >=1 slot boot xong
 *   pool.beginBatch(25);                // đặt hạn watchdog 2 worker
 *   const msg = await pool.run({fn:'batch_run', args:[...], files:[...]},
 *                              {label:'game1.zip'});
 *   pool.requestStop(); pool.cancelQueued(); pool.destroy();
 */
(function (global) {
  "use strict";

  var BOOT_TIMEOUT_MS = 180000;   // mỗi worker tối đa 3 phút để boot Pyodide
  var MAX_BOOT_FAILURES = 2;      // boot hỏng 2 lần thì slot chết hẳn

  // Khóa định danh 1 file để dedup theo slot: tên + cỡ + lastModified
  // (khớp quy ước fileSentFor/batchSentFor của app.js). Thiếu trường nào
  // thì dùng byteLength của buffer làm cỡ, lastModified = 0.
  function fileKey(f) {
    var size = (typeof f.size === "number") ? f.size
      : (f.data && typeof f.data.byteLength === "number" ? f.data.byteLength : 0);
    var lm = (typeof f.lastModified === "number") ? f.lastModified : 0;
    return (f.name || "") + "|" + size + "|" + lm;
  }

  function WorkerPool(url, opts) {
    opts = opts || {};
    this.url = url;
    this.size = opts.size || 2;
    this.onSlotChange = opts.onSlotChange || null;
    this.onStatus = opts.onStatus || null;
    this.nextId = 1;
    this.queue = [];              // job chờ slot rảnh (job.slotIdx==null)
    this.pinned = {};             // slotIdx -> [job chờ slot đó]
    this.slots = [];
    this.closed = false;
    // Số lần boot hỏng theo CHỈ SỐ slot — phải giữ qua respawn (trước đây
    // đếm trên từng object slot nên respawn reset về 0, slot boot hỏng mãi
    // sẽ respawn vô hạn và ready() treo tới hết deadline).
    this.bootFailures = {};
    for (var i = 0; i < this.size; i++) this.slots.push(this._spawn(i));
  }

  WorkerPool.prototype._emitChange = function () {
    if (this.onSlotChange) {
      try { this.onSlotChange(this.status()); } catch (_) { /* UI lỗi thì thôi */ }
    }
  };

  WorkerPool.prototype._spawn = function (i) {
    var self = this;
    var slot = { i: i, w: null, busy: false, job: null, label: "",
                 ready: false, dead: false, sentFiles: {},
                 bootFailures: this.bootFailures[i] || 0 };
    try {
      var w = new Worker(this.url, { type: "module" });
      slot.w = w;
      w.onmessage = function (ev) { self._onMessage(slot, ev.data || {}); };
      w.onerror = function (ev) {
        self._fail(slot, new Error(ev.message || "worker-error"), true);
      };
      // Boot: gửi op boot, reply {id:'boot-i', ok} nghĩa là sẵn sàng.
      var bootTimer = setTimeout(function () {
        self._fail(slot, new Error("worker-ready-timeout"), true);
      }, BOOT_TIMEOUT_MS);
      slot._bootTimer = bootTimer;
      w.postMessage({ id: "boot-" + i, op: "boot" });
    } catch (e) {
      slot.dead = true;
      slot.bootFailures = MAX_BOOT_FAILURES;
      this.bootFailures[i] = MAX_BOOT_FAILURES;
    }
    return slot;
  };

  WorkerPool.prototype._onMessage = function (slot, d) {
    if (d.type === "status") {
      if (this.onStatus) { try { this.onStatus(d.text); } catch (_) {} }
      return;
    }
    if (d.type === "progress") {
      var cur = slot.job;
      if (cur && cur.onProgress) { try { cur.onProgress(d); } catch (_) {} }
      return;
    }
    if (d.id === "boot-" + slot.i) {
      clearTimeout(slot._bootTimer);
      if (d.ok) {
        slot.ready = true;
      } else {
        this._fail(slot, new Error(d.error || "worker-boot-failed"), true);
      }
      this._emitChange();
      this._pump();
      return;
    }
    var job = slot.job;
    if (!job || d.id !== job.id) return; // reply lạ: bỏ qua
    slot.busy = false;
    slot.job = null;
    slot.label = "";
    if (d.ok) job.resolve({ msg: d, slot: slot.i });
    else job.reject(new Error(d.error || "job-failed"));
    this._emitChange();
    this._pump();
  };

  // Worker lỗi/hết hạn boot: job đang chạy (nếu có) báo lỗi, slot tạo lại
  // (trừ khi đã hỏng boot quá MAX_BOOT_FAILURES lần thì chết hẳn).
  // KHÔNG terminate job đang chạy khỏe mạnh — chỉ dọn worker đã lỗi.
  WorkerPool.prototype._fail = function (slot, err, respawn) {
    if (slot.dead) return;
    clearTimeout(slot._bootTimer);
    var job = slot.job;
    slot.job = null;
    slot.busy = false;
    slot.label = "";
    if (!slot.ready) {
      slot.bootFailures++;
      this.bootFailures[slot.i] = slot.bootFailures;
    }
    try { if (slot.w) slot.w.terminate(); } catch (_) {}
    slot.w = null;
    slot.ready = false;
    if (respawn && !this.closed && slot.bootFailures < MAX_BOOT_FAILURES) {
      this.slots[slot.i] = this._spawn(slot.i);
    } else {
      slot.dead = true;
    }
    if (job) job.reject(err);
    this._emitChange();
    this._pump();
  };

  // true khi >=1 slot boot xong; false khi mọi slot đã chết/hỏng boot.
  // Poll tới khi mọi slot settle (ready hoặc dead) — slot respawn trong
  // lúc boot hỏng vẫn được chờ, không kết luận vội là pool chết.
  WorkerPool.prototype.ready = function () {
    var self = this;
    var deadline = Date.now() + BOOT_TIMEOUT_MS + 5000;
    return new Promise(function (resolve) {
      (function check() {
        var settled = self.slots.every(function (s) { return s.ready || s.dead; });
        if (settled || Date.now() > deadline || self.closed) {
          resolve(self.slots.some(function (s) { return s.ready && !s.dead; }));
          return;
        }
        setTimeout(check, 200);
      })();
    });
  };

  WorkerPool.prototype.status = function () {
    return this.slots.map(function (s) {
      return { index: s.i, alive: !!s.w && !s.dead, ready: s.ready,
               busy: s.busy, label: s.label || "" };
    });
  };

  WorkerPool.prototype.aliveCount = function () {
    return this.slots.filter(function (s) { return s.w && !s.dead; }).length;
  };

  // Gửi op không cần reply (batch_begin/batch_stop) tới mọi worker sống.
  WorkerPool.prototype._broadcast = function (msg) {
    for (var i = 0; i < this.slots.length; i++) {
      var s = this.slots[i];
      if (s.w && !s.dead) {
        try { s.w.postMessage(msg); } catch (_) { /* worker chết: bỏ qua */ }
      }
    }
  };

  WorkerPool.prototype.beginBatch = function (budgetSeconds) {
    this._broadcast({ op: "batch_begin", budgetSeconds: budgetSeconds });
  };

  WorkerPool.prototype.requestStop = function () {
    this._broadcast({ op: "batch_stop" });
  };

  WorkerPool.prototype.run = function (payload, opts) {
    return this._enqueue(payload, opts || {}, null);
  };

  WorkerPool.prototype.runOn = function (slotIdx, payload, opts) {
    return this._enqueue(payload, opts || {}, slotIdx);
  };

  WorkerPool.prototype._enqueue = function (payload, opts, slotIdx) {
    var self = this;
    if (this.closed) return Promise.reject(new Error("pool-closed"));
    // Ghim vào slot không tồn tại (vd pool 1 worker mà job cũ ghim slot 1
    // sau khi đổi N — Mục C9): rơi về hàng đợi chung thay vì treo mãi.
    if (typeof slotIdx === "number" && (slotIdx < 0 || slotIdx >= this.size)) slotIdx = null;
    return new Promise(function (resolve, reject) {
      var job = { id: self.nextId++, payload: payload, transfer: opts.transfer || [],
                  label: opts.label || "", onProgress: opts.onProgress || null,
                  slotIdx: (typeof slotIdx === "number" ? slotIdx : null),
                  resolve: resolve, reject: reject };
      if (job.slotIdx == null) self.queue.push(job);
      else {
        if (!self.pinned[job.slotIdx]) self.pinned[job.slotIdx] = [];
        self.pinned[job.slotIdx].push(job);
      }
      self._pump();
    });
  };

  // Hủy job ĐANG CHỜ (chưa bắt đầu chạy): job đang chạy vẫn chạy xong
  // bình thường — đó mới là "dừng an toàn".
  WorkerPool.prototype.cancelQueued = function () {
    var q = this.queue.splice(0);
    for (var k in this.pinned) {
      q = q.concat(this.pinned[k].splice(0));
    }
    for (var i = 0; i < q.length; i++) {
      q[i].reject(new Error("E_BATCH_STOPPED: job chờ đã bị hủy (dừng an toàn)."));
    }
    this._emitChange();
  };

  WorkerPool.prototype._pump = function () {
    if (this.closed) return;
    for (var i = 0; i < this.slots.length; i++) {
      var slot = this.slots[i];
      if (slot.busy || !slot.ready || slot.dead || !slot.w) continue;
      var job = null;
      var pinnedQ = this.pinned[i];
      if (pinnedQ && pinnedQ.length) job = pinnedQ.shift();
      else if (this.queue.length) job = this.queue.shift();
      if (!job) continue;
      slot.busy = true;
      slot.job = job;
      slot.label = job.label;
      var msg = { id: job.id, op: "run" };
      for (var key in job.payload) msg[key] = job.payload[key];
      // Dedup file theo slot: chỉ gửi file slot này chưa có trong FS.
      // Đánh dấu TRƯỚC khi post, bỏ đánh dấu nếu post thất bại (file chưa
      // tới worker thì lần thử sau phải gửi lại). Transfer list lọc theo
      // đúng file được gửi; buffer nào không thuộc file nào thì giữ nguyên.
      var transfer = job.transfer || [];
      var newlySent = null;
      if (msg.files && msg.files.length) {
        var sent = slot.sentFiles || (slot.sentFiles = {});
        var toSend = [];
        var toTransfer = [];
        newlySent = [];
        for (var fi = 0; fi < msg.files.length; fi++) {
          var ff = msg.files[fi];
          var fk = fileKey(ff);
          if (sent[fk]) continue;   // slot này đã có file trong /tmp/input
          sent[fk] = true;
          newlySent.push(fk);
          toSend.push(ff);
          if (ff.data && transfer.indexOf(ff.data) !== -1) toTransfer.push(ff.data);
        }
        for (var ti = 0; ti < transfer.length; ti++) {
          var tb = transfer[ti];
          var isFileBuf = false;
          for (var fj = 0; fj < msg.files.length; fj++) {
            if (msg.files[fj].data === tb) { isFileBuf = true; break; }
          }
          if (!isFileBuf) toTransfer.push(tb);
        }
        msg.files = toSend;
        transfer = toTransfer;
      }
      try {
        slot.w.postMessage(msg, transfer);
      } catch (e) {
        if (newlySent) {
          var sent2 = slot.sentFiles || {};
          for (var si = 0; si < newlySent.length; si++) delete sent2[newlySent[si]];
        }
        slot.busy = false;
        slot.job = null;
        slot.label = "";
        job.reject(e);
        continue;
      }
      this._emitChange();
    }
    // Job ghim vào slot đã chết thì rơi về hàng đợi chung (chạy slot khác)
    // — trừ khi mọi slot đều chết thì báo lỗi luôn để app fallback.
    for (var j = 0; j < this.slots.length; j++) {
      var pq = this.pinned[j];
      if (pq && pq.length && (this.slots[j].dead || !this.slots[j].w)) {
        var moved = pq.splice(0);
        for (var m = 0; m < moved.length; m++) {
          moved[m].slotIdx = null;
          this.queue.push(moved[m]);
        }
      }
    }
    // Chỉ báo pool-dead khi MỌI slot đều đã chết (không còn worker sống).
    // Slot đang boot (chưa ready nhưng còn sống) thì job cứ chờ — khi boot
    // reply về, _onMessage sẽ _pump lại và giao việc. (Trước đây điều kiện
    // sai: hủy job ngay khi chưa slot nào ready, kể cả lúc đang boot.)
    if (this.queue.length && this.slots.every(function (s) { return s.dead || !s.w; })) {
      var dead = this.queue.splice(0);
      for (var d = 0; d < dead.length; d++) {
        dead[d].reject(new Error("pool-dead: không còn worker sống."));
      }
    }
  };

  WorkerPool.prototype.destroy = function () {
    this.closed = true;
    for (var i = 0; i < this.slots.length; i++) {
      var s = this.slots[i];
      clearTimeout(s._bootTimer);
      try { if (s.w) s.w.terminate(); } catch (_) {}
      s.w = null;
    }
    this.cancelQueued();
  };

  global.WorkerPool = WorkerPool;
})(typeof window !== "undefined" ? window : globalThis);
