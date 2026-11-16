// opfs.js — Ghi output lớn ra OPFS (Origin Private File System) (Mục C4).
//
// Vấn đề: output lớn (extract.zip, out.tar.xz, batch.zip, file dịch) trước
// đây ôm toàn bộ trong RAM (ArrayBuffer từ worker + Blob) rồi mới tạo blob
// URL để tải/chia sẻ — input + output + dữ liệu đang giải nén cùng lúc
// trong RAM, tab Safari (~1–1,5GB) dễ chết. Nay output >=1MB được ghi DẦN
// ra OPFS từng phần 8MB, rồi mới xuất qua Lưu/Chia sẻ bằng File đọc lại
// từ OPFS (đọc lười, không ôm cả file vào RAM); xong thì dọn file tạm.
//
// KHÔNG dùng COI/COOP/COEP/SharedArrayBuffer. OPFS là API chuẩn của trình
// duyệt (navigator.storage.getDirectory), không cần quyền thêm, không cần
// file picker. File tạm nằm trong thư mục riêng "beartea-outputs" của
// origin, trình duyệt tự dọn khi hết hạn mức — app vẫn tự dọn sau khi xuất
// xong, khi user bấm "Dọn file tạm", và dọn file quá 7 ngày lúc mở trang.
//
// Fallback sạch: trình duyệt không có OPFS (vd bản cũ) thì writeOutput()
// trả {ok:false, via:"memory"} kèm lý do — caller (app.js download())
// rơi về đường Blob trong RAM như trước, KHÔNG crash lặng, và ghi rõ lý
// do vào UI/log. Hook window.__PB3_NO_OPFS để harness ép fallback.
//
// Ghi từng phần, không concat: writeOutput() nhận Blob / ArrayBuffer /
// Uint8Array / mảng part và chỉ ghi từng lát 8MB (Blob.slice / subarray
// đều là view chia sẻ, không copy toàn bộ). FileSystemWritableFileStream
// .write() nhận Blob/BufferSource nên không cần gom thành ArrayBuffer.

(function (global) {
  "use strict";

  var DIR_NAME = "beartea-outputs";
  var CHUNK_BYTES = 8 * 1024 * 1024; // ghi từng phần 8MB
  var STALE_MS = 7 * 24 * 3600 * 1000; // file tạm quá 7 ngày thì dọn
  var NAMES_KEY = "pb3_opfs_output_names"; // tên file tạm đã ghi (để dọn)

  function supported() {
    // Hook kiểm tra: harness đặt window.__PB3_NO_OPFS = true để giả lập
    // trình duyệt không có OPFS (kiểm tra fallback) mà không cần stub
    // navigator.storage.
    if (typeof global.__PB3_NO_OPFS !== "undefined" && global.__PB3_NO_OPFS) return false;
    try {
      return !!(global.navigator && global.navigator.storage &&
        typeof global.navigator.storage.getDirectory === "function");
    } catch (e) { return false; }
  }

  function unsupportedReason() {
    if (typeof global.__PB3_NO_OPFS !== "undefined" && global.__PB3_NO_OPFS) {
      return "đã tắt OPFS để kiểm tra fallback (__PB3_NO_OPFS)";
    }
    return "trình duyệt này không có OPFS (navigator.storage.getDirectory)";
  }

  // Tên file an toàn trong OPFS: chỉ lấy tên gốc, bỏ đường dẫn, bỏ ký tự
  // cấm; tên rỗng thì dùng "output.bin".
  function safeName(name) {
    var n = String(name == null ? "" : name).replace(/\\/g, "/");
    var parts = n.split("/");
    n = parts[parts.length - 1] || "";
    n = n.replace(/[\x00-\x1f<>:"|?*]/g, "_").trim();
    if (!n) n = "output.bin";
    return n;
  }

  // Tách data thành nhiều part <=8MB mà KHÔNG copy toàn bộ: Blob.slice
  // và Uint8Array.subarray đều là view/lát chia sẻ dữ liệu gốc.
  function toParts(data) {
    var parts = [];
    if (typeof Blob !== "undefined" && data instanceof Blob) {
      var size = data.size;
      if (size === 0) { parts.push(data); return parts; }
      for (var off = 0; off < size; off += CHUNK_BYTES) {
        parts.push(data.slice(off, Math.min(size, off + CHUNK_BYTES)));
      }
      return parts;
    }
    if (Array.isArray(data)) {
      for (var i = 0; i < data.length; i++) {
        var p = data[i];
        if (p == null) continue;
        if (typeof Blob !== "undefined" && p instanceof Blob) {
          var ps = p.size;
          for (var o2 = 0; o2 < ps; o2 += CHUNK_BYTES) {
            parts.push(p.slice(o2, Math.min(ps, o2 + CHUNK_BYTES)));
          }
        } else if (p instanceof ArrayBuffer) {
          var u = new Uint8Array(p);
          for (var o3 = 0; o3 < u.length; o3 += CHUNK_BYTES) {
            parts.push(u.subarray(o3, Math.min(u.length, o3 + CHUNK_BYTES)));
          }
        } else if (ArrayBuffer.isView(p)) {
          for (var o4 = 0; o4 < p.byteLength; o4 += CHUNK_BYTES) {
            parts.push(new Uint8Array(p.buffer, p.byteOffset + o4,
              Math.min(CHUNK_BYTES, p.byteLength - o4)));
          }
        } else if (typeof p === "string") {
          var enc = new TextEncoder();
          var b = enc.encode(p);
          for (var o5 = 0; o5 < b.length; o5 += CHUNK_BYTES) {
            parts.push(b.subarray(o5, Math.min(b.length, o5 + CHUNK_BYTES)));
          }
        }
      }
      if (!parts.length) parts.push(new Uint8Array(0));
      return parts;
    }
    if (data instanceof ArrayBuffer) {
      var u8 = new Uint8Array(data);
      for (var o6 = 0; o6 < u8.length; o6 += CHUNK_BYTES) {
        parts.push(u8.subarray(o6, Math.min(u8.length, o6 + CHUNK_BYTES)));
      }
      if (!parts.length) parts.push(new Uint8Array(0));
      return parts;
    }
    if (ArrayBuffer.isView(data)) {
      for (var o7 = 0; o7 < data.byteLength; o7 += CHUNK_BYTES) {
        parts.push(new Uint8Array(data.buffer, data.byteOffset + o7,
          Math.min(CHUNK_BYTES, data.byteLength - o7)));
      }
      if (!parts.length) parts.push(new Uint8Array(0));
      return parts;
    }
    if (typeof data === "string") {
      var bytes = new TextEncoder().encode(data);
      for (var o8 = 0; o8 < bytes.length; o8 += CHUNK_BYTES) {
        parts.push(bytes.subarray(o8, Math.min(bytes.length, o8 + CHUNK_BYTES)));
      }
      if (!parts.length) parts.push(new Uint8Array(0));
      return parts;
    }
    return [new Uint8Array(0)];
  }

  function dataByteLength(data) {
    if (data == null) return 0;
    if (typeof Blob !== "undefined" && data instanceof Blob) return data.size;
    if (data instanceof ArrayBuffer) return data.byteLength;
    if (ArrayBuffer.isView(data)) return data.byteLength;
    if (typeof data === "string") return new TextEncoder().encode(data).length;
    if (Array.isArray(data)) {
      var n = 0;
      for (var i = 0; i < data.length; i++) n += dataByteLength(data[i]);
      return n;
    }
    return 0;
  }

  async function getDir(create) {
    var root = await global.navigator.storage.getDirectory();
    return root.getDirectoryHandle(DIR_NAME, { create: !!create });
  }

  function recordedNames() {
    try {
      var raw = global.localStorage && global.localStorage.getItem(NAMES_KEY);
      var arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr.filter(function (n) { return typeof n === "string"; }) : [];
    } catch (e) { return []; }
  }

  function recordName(name) {
    try {
      var arr = recordedNames();
      if (arr.indexOf(name) === -1) arr.push(name);
      if (global.localStorage) global.localStorage.setItem(NAMES_KEY, JSON.stringify(arr));
    } catch (e) { /* localStorage không có — chỉ mất đường dọn dự phòng */ }
  }

  function unrecordName(name) {
    try {
      var arr = recordedNames().filter(function (n) { return n !== name; });
      if (global.localStorage) global.localStorage.setItem(NAMES_KEY, JSON.stringify(arr));
    } catch (e) { /* bỏ qua */ }
  }

  // Ghi output ra OPFS từng phần 8MB. Trả {ok:true, via:"opfs", name, size}
  // hoặc {ok:false, via:"memory", name, error} để caller fallback về RAM.
  // Không ném lỗi ra ngoài (trừ khi caller cố tình bắt) — mọi lỗi ghi đều
  // trả về dạng {ok:false} kèm lý do thật.
  async function writeOutput(name, data) {
    var safe = safeName(name);
    if (!supported()) {
      return { ok: false, via: "memory", name: safe, error: unsupportedReason() };
    }
    var writer = null;
    try {
      var dir = await getDir(true);
      var handle = await dir.getFileHandle(safe, { create: true });
      writer = await handle.createWritable();
      var parts = toParts(data);
      for (var i = 0; i < parts.length; i++) {
        await writer.write(parts[i]);
      }
      await writer.close();
      writer = null;
      var file = await handle.getFile();
      recordName(safe);
      return { ok: true, via: "opfs", name: safe, size: file.size };
    } catch (e) {
      if (writer) {
        try { await writer.abort(); } catch (_) { /* đã đóng/lỗi */ }
      }
      return { ok: false, via: "memory", name: safe,
        error: (e && e.message) ? e.message : String(e) };
    }
  }

  // Đọc lại file tạm từ OPFS dưới dạng File (đọc lười — Share/download
  // dùng File này, không ôm cả file vào RAM).
  async function readOutput(name) {
    var safe = safeName(name);
    var dir = await getDir(false);
    var handle = await dir.getFileHandle(safe);
    return handle.getFile();
  }

  // Xóa 1 file tạm (sau khi xuất xong / user bấm dọn). Trả true nếu đã xóa
  // (hoặc vốn không có), false khi lỗi.
  async function deleteOutput(name) {
    var safe = safeName(name);
    try {
      var dir = await getDir(false);
      await dir.removeEntry(safe);
      unrecordName(safe);
      return true;
    } catch (e) {
      // File vốn không có cũng coi như đã dọn xong.
      if (e && e.name === "NotFoundError") { unrecordName(safe); return true; }
      return false;
    }
  }

  // Liệt kê file tạm trong OPFS: [{name, size, lastModified}]. Directory
  // iteration (values()) không có ở mọi trình duyệt thì rơi về danh sách
  // tên đã ghi trong localStorage (ghi lúc writeOutput).
  async function listOutputs() {
    if (!supported()) return [];
    try {
      var dir = await getDir(false);
      var out = [];
      if (typeof dir.values === "function") {
        var it = dir.values();
        var step = await it.next();
        while (!step.done) {
          var h = step.value;
          try {
            var f = await h.getFile();
            out.push({ name: h.name, size: f.size, lastModified: f.lastModified || 0 });
          } catch (_) { out.push({ name: h.name, size: 0, lastModified: 0 }); }
          step = await it.next();
        }
        return out;
      }
    } catch (e) { /* rơi về danh sách đã ghi */ }
    var names = recordedNames();
    var listed = [];
    for (var i = 0; i < names.length; i++) {
      try {
        var dir2 = await getDir(false);
        var h2 = await dir2.getFileHandle(names[i]);
        var f2 = await h2.getFile();
        listed.push({ name: names[i], size: f2.size, lastModified: f2.lastModified || 0 });
      } catch (_) { /* file đã mất — bỏ qua */ }
    }
    return listed;
  }

  // Dọn file tạm: xóa toàn bộ file trong thư mục OPFS (trả số file đã xóa).
  async function cleanupOutputs() {
    var items = await listOutputs();
    var deleted = 0;
    for (var i = 0; i < items.length; i++) {
      if (await deleteOutput(items[i].name)) deleted++;
    }
    // Dọn cả tên đã ghi nhưng file đã mất (tránh danh sách phình mãi).
    try {
      if (global.localStorage) global.localStorage.setItem(NAMES_KEY, JSON.stringify([]));
    } catch (e) { /* bỏ qua */ }
    return deleted;
  }

  // Dọn file tạm quá hạn (mặc định 7 ngày) lúc mở trang — chạy nền, lỗi
  // thì im lặng (không chặn trang, không hiện banner). Trả số file đã xóa.
  async function cleanupStaleOnBoot(maxAgeMs) {
    if (!supported()) return 0;
    try {
      var maxAge = (typeof maxAgeMs === "number" && maxAgeMs > 0) ? maxAgeMs : STALE_MS;
      var items = await listOutputs();
      var now = Date.now();
      var deleted = 0;
      for (var i = 0; i < items.length; i++) {
        var age = items[i].lastModified ? (now - items[i].lastModified) : 0;
        if (items[i].lastModified && age > maxAge) {
          if (await deleteOutput(items[i].name)) deleted++;
        }
      }
      return deleted;
    } catch (e) { return 0; }
  }

  var api = {
    DIR_NAME: DIR_NAME, CHUNK_BYTES: CHUNK_BYTES, STALE_MS: STALE_MS,
    supported: supported, unsupportedReason: unsupportedReason,
    safeName: safeName, toParts: toParts, dataByteLength: dataByteLength,
    writeOutput: writeOutput, readOutput: readOutput,
    deleteOutput: deleteOutput, listOutputs: listOutputs,
    cleanupOutputs: cleanupOutputs, cleanupStaleOnBoot: cleanupStaleOnBoot,
  };
  global.PB3OPFS = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
