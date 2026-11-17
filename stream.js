// stream.js — Đọc/ghi streaming phía main thread (Mục 5).
//
// KHÔNG dùng COI/COOP/COEP/SharedArrayBuffer. Không động vào worker.js,
// worker-pool.js hay bridge.py — mọi hàm ở đây chạy ở main thread, chỉ
// đọc đuôi file (EOCD + central directory) và slice/inflate từng entry.
//
// Cái gì stream được thật:
//   - Liệt kê ZIP: chỉ đọc EOCD + central directory ở cuối file (vài KB),
//     không đưa cả file qua Python, không giải nén toàn bộ file.
//   - Entry ZIP method stored (0): Blob.slice — copy-through, không qua
//     heap JS hay Python (slice chia sẻ dữ liệu gốc, không copy).
//   - Entry ZIP method deflate (8): DecompressionStream('deflate-raw')
//     native của trình duyệt, KHÔNG dùng zlib Python cho đường này.
//   - Gzip member: DecompressionStream('gzip') native.
//   - Output: download() nhận mảng nhiều Blob part — không concat thành
//     một ArrayBuffer khổng lồ trước khi tải.
//
// Cái gì VẪN giữ RAM (giới hạn thật, xem exp/item5_results.md):
//   - File đã chọn vẫn nằm trọn trong state.file.data (ArrayBuffer) vì
//     worker protocol send-once/clone-fix bắt buộc giữ bản gốc để gửi
//     lại; streaming end-to-end là KHÔNG THỂ nếu không viết lại SDK.
//   - .tar.xz dùng LZMA — DecompressionStream native KHÔNG thay được,
//     vẫn đi qua bridge (compress.py đã stream nội bộ qua tarfile r|xz).
//   - Output từ worker (extract.zip, batch.zip, out.tar.xz) vẫn là một
//     ArrayBuffer duy nhất do worker trả về; chỉ được gói thành Blob
//     (không copy thêm) rồi tải — MỤC C4 ĐÃ LÀM: output >=1MB được ghi dần
//     ra OPFS từng phần 8MB (opfs.js, app.js download()), xong thì bỏ tham
//     chiếu buffer gốc (runOp) nên input + output không cùng nằm trong RAM
//     sau khi xuất; đường RAM chỉ còn cho file nhỏ / fallback không OPFS.
//
// Fallback về bridge khi: không phải ZIP (không có EOCD), ZIP64, có
// container lồng (entry .zip/.ipa/.tar/.gz/.tgz/.xz/.bz2/.pck/.pak —
// bridge liệt kê đệ quy dạng `a.zip::b/c.json`, EOCD không tái tạo
// được), method khác 0/8, entry mã hoá, header cục bộ hỏng.

(function (global) {
  "use strict";

  var MB = 1024 * 1024;
  // [ĐÃ ĐO THẬT 2026-11-17] tab Safari ~4× dung lượng file + ~250MB nền
  // (Pyodide + trình duyệt) nên IPA Tom 210MB (~860-940MB) đã sập tab:
  // cảnh báo từ file >140MB (~800MB RAM), khuyên mạnh iSH từ >190MB (~1GB).
  var WARN_140MB = 140 * MB;    // >140MB: cảnh báo rõ, vẫn cho tiếp tục
  var STRONG_190MB = 190 * MB;  // >190MB: khuyên mạnh dùng iSH
  var MAX_2GB = 2 * 1024 * MB;  // >2GB: từ chối (khớp bridge MAX_WEB_INPUT)

  function fmtBytes(n) {
    if (n == null || isNaN(n)) return "?";
    var u = ["B", "KB", "MB", "GB"];
    var i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + u[i];
  }

  // Cảnh báo kích thước TRƯỚC khi mở / TRƯỚC khi chạy thao tác nặng.
  // level: 'none' | 'warn' (>140MB) | 'strong' (>190MB) | 'reject' (>2GB).
  function sizeWarning(size) {
    if (typeof size !== "number" || isNaN(size) || size <= 0) {
      return { level: "none", text: null };
    }
    if (size > MAX_2GB) {
      return { level: "reject",
        text: "File " + fmtBytes(size) + " quá lớn cho bản web (>2GB). Dùng bản iSH cho file này." };
    }
    if (size > STRONG_190MB) {
      return { level: "strong",
        text: "File " + fmtBytes(size) + " rất lớn (>190MB). Tab trình duyệt trên iPhone chỉ có khoảng 1–1,5GB RAM và cần ~4× dung lượng file + ~250MB nền (đo thật 2026-11-17) — có thể hết RAM và bị tắt giữa chừng, nên dùng bản iSH cho file này. Vẫn có thể thử tiếp trên web, nhưng hãy đóng các tab khác trước." };
    }
    if (size > WARN_140MB) {
      return { level: "warn",
        text: "Lưu ý: file " + fmtBytes(size) + " khá lớn (>140MB), tab trình duyệt có thể tốn ~" + fmtBytes(size * 4 + 250 * MB) + " RAM (đo thật 2026-11-17). Vẫn thử chạy." };
    }
    return { level: "none", text: null };
  }

  // ---- Liệt kê ZIP từ EOCD + central directory ----
  // Khớp định dạng entry của bridge list_entries: {path, size, kind}.
  // kind bắt chước sdk/discovery.py inventory() cho ZIP phẳng (không lồng):
  // excluded trước (font/log/test/subtitle), rồi unity (.assets/.unity3d/
  // .bundle/.resS), rồi container (đuôi container — cũng là dấu hiệu lồng),
  // còn lại là file. Thư mục (tên kết thúc "/") bridge KHÔNG liệt kê
  // (ZipVFS bỏ qua is_dir) và entry excluded bridge ghi size 0 — JS làm
  // y hệt (đã kiểm chứng bằng parity bridge B3).
  // không có đuôi container thì JS không phát hiện được (cần đọc head đã
  // giải nén) — giới hạn đã ghi, gặp file như vậy kind có thể lệch thành
  // "file" thay vì "container"; đường trích xuất vẫn đi qua bridge.
  var EXCLUDED_RES = [
    /\.(ttf|otf|woff2?|fnt|atlas)$/i,
    /\.(log|cache|crash|dmp)$/i,
    /(^|\/)(test|tests|__tests__|debug|dbg)(\/|$)/i,
    /\.(srt|vtt|ass|ssa)$/i,
  ];
  var UNITY_EXTS = [".assets", ".unity3d", ".bundle", ".ress"];
  var CONTAINER_EXTS = [".zip", ".ipa", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".pck", ".pak"];

  function endsWithAny(lower, exts) {
    for (var i = 0; i < exts.length; i++) {
      if (lower.slice(-exts[i].length) === exts[i]) return true;
    }
    return false;
  }

  function entryKind(name) {
    var lower = String(name || "").toLowerCase();
    for (var i = 0; i < EXCLUDED_RES.length; i++) {
      if (EXCLUDED_RES[i].test(name)) return "excluded";
    }
    if (endsWithAny(lower, UNITY_EXTS)) return "unity";
    if (endsWithAny(lower, CONTAINER_EXTS)) return "container";
    return "file";
  }

  function toU8(data) {
    // ArrayBuffer.isView thay vì instanceof Uint8Array: an toàn cross-realm
    // (Buffer của Node truyền qua vm, hoặc view từ worker khác).
    if (data && typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView(data)) return data;
    if (data instanceof ArrayBuffer) return new Uint8Array(data);
    return null;
  }

  // Tìm End Of Central Directory: quét ngược tối đa 64KB comment + 22 byte.
  // Trả null khi không phải ZIP; {zip64:true} khi EOCD báo ZIP64.
  function findEocd(u8) {
    var len = u8.length;
    if (len < 22) return null;
    var start = Math.max(0, len - 22 - 65536);
    for (var off = len - 22; off >= start; off--) {
      if (u8[off] === 0x50 && u8[off + 1] === 0x4b && u8[off + 2] === 0x05 && u8[off + 3] === 0x06) {
        var view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
        var commentLen = view.getUint16(off + 20, true);
        if (off + 22 + commentLen !== len) continue; // EOCD giả trong dữ liệu
        var cdSize = view.getUint32(off + 12, true);
        var cdOffset = view.getUint32(off + 16, true);
        var total = view.getUint16(off + 10, true);
        if (cdSize === 0xffffffff || cdOffset === 0xffffffff || total === 0xffff) {
          return { zip64: true };
        }
        return { offset: off, total: total, cdSize: cdSize, cdOffset: cdOffset };
      }
    }
    return null;
  }

  function listZipEntries(data) {
    var u8 = toU8(data);
    if (!u8) return { ok: false, reason: "not-bytes", nested: false, entries: [] };
    var eocd = findEocd(u8);
    if (!eocd) return { ok: false, reason: "not-zip", nested: false, entries: [] };
    if (eocd.zip64) return { ok: false, reason: "zip64", nested: false, entries: [] };
    if (eocd.cdOffset + eocd.cdSize > u8.length) {
      return { ok: false, reason: "bad-central-directory", nested: false, entries: [] };
    }
    var view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var decoder = (typeof TextDecoder !== "undefined")
      ? new TextDecoder("utf-8") : null;
    var entries = [];
    var nested = false;
    var off = eocd.cdOffset;
    var end = eocd.cdOffset + eocd.cdSize;
    while (off + 46 <= end) {
      if (view.getUint32(off, true) !== 0x02014b50) break; // PK\x01\x02
      var flags = view.getUint16(off + 8, true);
      var method = view.getUint16(off + 10, true);
      var crc = view.getUint32(off + 16, true);
      var compSize = view.getUint32(off + 20, true);
      var size = view.getUint32(off + 24, true);
      var nameLen = view.getUint16(off + 28, true);
      var extraLen = view.getUint16(off + 30, true);
      var commentLen = view.getUint16(off + 32, true);
      var localOffset = view.getUint32(off + 42, true);
      if (off + 46 + nameLen + extraLen + commentLen > u8.length) {
        return { ok: false, reason: "bad-central-directory", nested: false, entries: [] };
      }
      var nameBytes = u8.subarray(off + 46, off + 46 + nameLen);
      var name = decoder
        ? decoder.decode(nameBytes)
        : String.fromCharCode.apply(null, nameBytes);
      // Sửa mojibake U+200E ở đầu tên entry (đã sửa phía web trước đây).
      if (name.charCodeAt(0) === 0x200e) name = name.slice(1);
      // Bridge (ZipVFS) không liệt kê đường dẫn tuyệt đối / leo thư mục,
      // và KHÔNG liệt kê thư mục (is_dir) — JS bỏ qua y hệt để khớp
      // list_entries (đã kiểm chứng bằng parity bridge B3).
      if (name.endsWith("/")) { off += 46 + nameLen + extraLen + commentLen; continue; }
      var unsafe = name.charAt(0) === "/" ||
        name.split("/").indexOf("..") !== -1;
      if (!unsafe && name.length) {
        var kind = entryKind(name);
        if (kind === "container") nested = true;
        entries.push({
          name: name, path: name, method: method,
          methodName: method === 0 ? "stored" : method === 8 ? "deflate" : "method-" + method,
          // Bridge ghi size 0 cho entry excluded (xem inventory()).
          size: kind === "excluded" ? 0 : size, compressedSize: compSize,
          offset: localOffset, crc: crc, flags: flags, kind: kind,
        });
      }
      off += 46 + nameLen + extraLen + commentLen;
    }
    return { ok: true, kind: "zip", total: entries.length,
             entries: entries, nested: nested, zip64: false };
  }

  // Vị trí bắt đầu dữ liệu nén trong local file header của 1 entry.
  function localDataOffset(u8, entry) {
    var view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    var off = entry.offset;
    if (off + 30 > u8.length) return -1;
    if (view.getUint32(off, true) !== 0x04034b50) return -1; // PK\x03\x04
    var nameLen = view.getUint16(off + 26, true);
    var extraLen = view.getUint16(off + 28, true);
    var start = off + 30 + nameLen + extraLen;
    if (start + entry.compressedSize > u8.length) return -1;
    return start;
  }

  // Trích 1 entry ZIP từ Blob/File (copy-through stored, DecompressionStream
  // deflate). Trả Blob — stored là slice chia sẻ dữ liệu gốc (không copy
  // qua heap), deflate là Blob do trình duyệt gom từ stream (không concat
  // ArrayBuffer bằng tay). Ném lỗi có .code để caller fallback về bridge:
  // E_UNSUPPORTED_METHOD / E_ENCRYPTED / E_BAD_LOCAL_HEADER.
  async function extractZipEntry(file, entry) {
    if (!entry) throw Object.assign(new Error("Thiếu entry ZIP."), { code: "E_BAD_ENTRY" });
    if ((entry.flags & 0x1) !== 0) {
      throw Object.assign(new Error("Entry đã mã hoá, bản web chưa giải mã được."), { code: "E_ENCRYPTED" });
    }
    if (entry.method === 0) {
      // Copy-through: slice trực tiếp trên Blob gốc, không đọc vào RAM JS.
      var start = null;
      if (typeof file.arrayBuffer === "function") {
        // Cần local header để biết data bắt đầu ở đâu: chỉ đọc tối đa
        // 64KB đầu của entry (header + tên + extra), không đọc cả file.
        var headEnd = Math.min(file.size, entry.offset + 65536);
        var head = new Uint8Array(await file.slice(entry.offset, headEnd).arrayBuffer());
        var dataOff = localDataOffset(head, { offset: 0, compressedSize: entry.compressedSize });
        if (dataOff < 0) {
          throw Object.assign(new Error("Header cục bộ của entry ZIP hỏng."), { code: "E_BAD_LOCAL_HEADER" });
        }
        start = entry.offset + dataOff;
      }
      var blob = file.slice(start, start + entry.size);
      return { blob: blob, method: "stored", bytes: entry.size, copiedThrough: true };
    }
    if (entry.method === 8) {
      if (typeof DecompressionStream === "undefined") {
        throw Object.assign(new Error("Trình duyệt này không có DecompressionStream."), { code: "E_NO_DECOMPRESSION_STREAM" });
      }
      // Đọc đúng vùng dữ liệu nén (local header + tên + extra) rồi inflate
      // native; chỉ vùng nén này vào RAM, không phải cả file.
      var headBuf = new Uint8Array(await file.slice(entry.offset, Math.min(file.size, entry.offset + 65536)).arrayBuffer());
      var dataStart = localDataOffset(headBuf, { offset: 0, compressedSize: entry.compressedSize });
      if (dataStart < 0) {
        throw Object.assign(new Error("Header cục bộ của entry ZIP hỏng."), { code: "E_BAD_LOCAL_HEADER" });
      }
      var comp = file.slice(entry.offset + dataStart, entry.offset + dataStart + entry.compressedSize);
      var stream = comp.stream().pipeThrough(new DecompressionStream("deflate-raw"));
      var out = await new Response(stream).blob();
      return { blob: out, method: "deflate", bytes: out.size, copiedThrough: false };
    }
    throw Object.assign(new Error("Method ZIP " + entry.method + " chưa hỗ trợ ở đường nhanh — dùng bridge."), { code: "E_UNSUPPORTED_METHOD" });
  }

  // Trích 1 entry ZIP từ ArrayBuffer/Uint8Array đã có trong RAM (dùng cho
  // test Node và khi file đã nằm trong state.file.data). stored dùng
  // subarray (view chia sẻ buffer, không copy), deflate qua DecompressionStream.
  async function extractZipEntryFromBuffer(data, entry) {
    var u8 = toU8(data);
    if (!u8) throw Object.assign(new Error("Dữ liệu không phải bytes."), { code: "E_BAD_ENTRY" });
    if ((entry.flags & 0x1) !== 0) {
      throw Object.assign(new Error("Entry đã mã hoá, bản web chưa giải mã được."), { code: "E_ENCRYPTED" });
    }
    var dataStart = localDataOffset(u8, entry);
    if (dataStart < 0) {
      throw Object.assign(new Error("Header cục bộ của entry ZIP hỏng."), { code: "E_BAD_LOCAL_HEADER" });
    }
    if (entry.method === 0) {
      // Copy-through: subarray là view chia sẻ buffer gốc (không copy
      // qua heap JS); Blob gói view này, không concat thêm.
      var view = u8.subarray(dataStart, dataStart + entry.size);
      return { blob: new Blob([view]), method: "stored", bytes: entry.size, copiedThrough: true };
    }
    if (entry.method === 8) {
      if (typeof DecompressionStream === "undefined") {
        throw Object.assign(new Error("Trình duyệt này không có DecompressionStream."), { code: "E_NO_DECOMPRESSION_STREAM" });
      }
      var comp = u8.subarray(dataStart, dataStart + entry.compressedSize);
      var stream = new Blob([comp]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
      var out = await new Response(stream).blob();
      return { blob: out, method: "deflate", bytes: out.size, copiedThrough: false };
    }
    throw Object.assign(new Error("Method ZIP " + entry.method + " chưa hỗ trợ ở đường nhanh — dùng bridge."), { code: "E_UNSUPPORTED_METHOD" });
  }

  // Giải nén 1 gzip member bằng DecompressionStream native.
  // data: Blob/File/ArrayBuffer/Uint8Array. Trả Blob đã giải nén.
  // GIỚI HẠN: chỉ 1 member — gzip nhiều member nối nhau làm
  // DecompressionStream('gzip') lỗi (Node 24 ném
  // ERR_TRAILING_JUNK_AFTER_STREAM_END; Chromium chưa kết luận được);
  // gặp file đó thì nút tải nhanh báo lỗi, xem qua bridge list/diagnose.
  async function extractGzipMember(data) {
    if (typeof DecompressionStream === "undefined") {
      throw Object.assign(new Error("Trình duyệt này không có DecompressionStream."), { code: "E_NO_DECOMPRESSION_STREAM" });
    }
    var blob = (typeof Blob !== "undefined" && data instanceof Blob)
      ? data : new Blob([toU8(data) || data]);
    var stream = blob.stream().pipeThrough(new DecompressionStream("gzip"));
    return new Response(stream).blob();
  }

  // ---- Output streaming: nhiều Blob part ----
  // Ghi output lớn dạng nhiều part thay vì concat thành một ArrayBuffer
  // khổng lồ: new Blob(parts) không copy dữ liệu các part lại với nhau.
  function makeOutputBlob(parts, type) {
    return new Blob(parts, { type: type || "application/octet-stream" });
  }

  function blobPartsByteLength(parts) {
    var n = 0;
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p == null) continue;
      if (typeof p.size === "number") n += p.size;
      else if (typeof p.byteLength === "number") n += p.byteLength;
      else if (typeof p.length === "number") n += p.length;
    }
    return n;
  }

  // Chia text (vd JSON bản dịch đã có trong RAM) thành nhiều part 64KB để
  // tải về dạng nhiều Blob part thay vì một ArrayBuffer duy nhất.
  function textToBlobParts(text, chunkSize) {
    var size = chunkSize && chunkSize > 0 ? chunkSize : 65536;
    var enc = new TextEncoder();
    var bytes = enc.encode(String(text == null ? "" : text));
    var parts = [];
    for (var i = 0; i < bytes.length; i += size) {
      parts.push(bytes.subarray(i, Math.min(bytes.length, i + size)));
    }
    if (!parts.length) parts.push(new Uint8Array(0));
    return parts;
  }

  var api = {
    WARN_600MB: WARN_600MB, STRONG_800MB: STRONG_800MB, MAX_2GB: MAX_2GB,
    fmtBytes: fmtBytes, sizeWarning: sizeWarning,
    findEocd: findEocd, listZipEntries: listZipEntries,
    localDataOffset: localDataOffset,
    extractZipEntry: extractZipEntry,
    extractZipEntryFromBuffer: extractZipEntryFromBuffer,
    extractGzipMember: extractGzipMember,
    makeOutputBlob: makeOutputBlob,
    blobPartsByteLength: blobPartsByteLength,
    textToBlobParts: textToBlobParts,
  };
  global.PB3Stream = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
