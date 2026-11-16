/* kb-store.js — Lưu KB (bytes SQLite) bền vững bằng IndexedDB, tự lưu
 * sau mỗi thay đổi, lưu khẩn khi ẩn tab/đóng trang.
 *
 * Điều chỉnh từ kb-store.js trong gói nâng cấp web-upgrade của Joseph:
 *  - Script classic (KHÔNG phải ES module) để app.js (classic) dùng
 *    trực tiếp qua tên toàn cục KBStore — không đổi app.js thành module
 *    (tránh đổi thứ tự nạp script và vỡ các harness cũ).
 *  - debounceMs mặc định 0: app.js gọi markDirty() rồi flush() ngay sau
 *    mỗi lần KB thay đổi (thêm/quên/nhập/batch) — lưu NGAY, không đợi
 *    1,5 giây như bản gốc của gói.
 *  - Bỏ exportFile(): đường xuất .sqlite của app.js đã có download()
 *    riêng (Share API làm chính, blob dự phòng, giữ URL 10 phút) — giữ
 *    một đường xuất duy nhất, tránh hai bản revoke khác nhau.
 *  - KHÔNG dùng OPFS/SAH ở giai đoạn này (theo kế hoạch mục 3).
 *
 * Cách dùng (xem app.js):
 *   const kb = new KBStore('kb.sqlite', { debounceMs: 0 });
 *   await kb.requestPersistence();          // xin lưu trữ bền vững
 *   const saved = await kb.load();          // Uint8Array | null
 *   kb.attach(async () => exportDbBytes()); // hàm trả bytes KB hiện tại
 *   kb.markDirty(); await kb.flush();       // gọi sau mỗi lần ghi KB
 */
const KBSTORE_DB_NAME = 'pb3-kb-store';
const KBSTORE_STORE = 'blobs';

function kbstoreOpenDB() {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(KBSTORE_DB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(KBSTORE_STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function kbstoreTx(mode, fn) {
  const db = await kbstoreOpenDB();
  try {
    return await new Promise((resolve, reject) => {
      const t = db.transaction(KBSTORE_STORE, mode);
      const req = fn(t.objectStore(KBSTORE_STORE));
      t.oncomplete = () => resolve(req && req.result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  } finally { db.close(); }
}

class KBStore {
  constructor(key = 'kb.sqlite', { debounceMs = 0 } = {}) {
    this.key = key;
    this.debounceMs = debounceMs;
    this._getBytes = null;
    this._timer = null;
    this._saving = null;
    this._dirty = false;
    this.lastSavedAt = 0;
    this.onStatus = null; // (status: 'dirty'|'saving'|'saved'|'error', detail) => void
    this._bindLifecycle();
  }

  /** Xin trình duyệt không tự xóa dữ liệu (Safari có thể dọn sau ~7 ngày
   * không dùng nếu chưa cài vào Màn hình chính). */
  async requestPersistence() {
    try { return await navigator.storage.persist(); } catch (_) { return false; }
  }

  async load() {
    const v = await kbstoreTx('readonly', (s) => s.get(this.key));
    return v ? new Uint8Array(v) : null;
  }

  async saveNow(bytes) {
    await kbstoreTx('readwrite', (s) => s.put(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), this.key));
    this.lastSavedAt = Date.now();
  }

  async clear() { await kbstoreTx('readwrite', (s) => s.delete(this.key)); }

  attach(getBytes) { this._getBytes = getBytes; }

  markDirty() {
    this._dirty = true;
    this._emit('dirty');
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.flush(), this.debounceMs);
  }

  async flush() {
    clearTimeout(this._timer);
    if (!this._dirty || !this._getBytes) return;
    if (this._saving) { await this._saving; if (!this._dirty) return; }
    this._saving = (async () => {
      try {
        this._dirty = false;
        this._emit('saving');
        const bytes = await this._getBytes();
        await this.saveNow(bytes);
        this._emit('saved');
      } catch (err) {
        this._dirty = true;
        this._emit('error', err);
      } finally { this._saving = null; }
    })();
    await this._saving;
  }

  /** Nhập .sqlite: kiểm tra header "SQLite format 3\0" rồi trả bytes. */
  static async importFile(file) {
    const head = new Uint8Array(await file.slice(0, 16).arrayBuffer());
    const magic = 'SQLite format 3\u0000';
    for (let i = 0; i < 16; i++) {
      if (head[i] !== magic.charCodeAt(i)) throw new Error('Không phải file SQLite hợp lệ');
    }
    return new Uint8Array(await file.arrayBuffer());
  }

  _emit(s, d) { if (this.onStatus) try { this.onStatus(s, d); } catch (_) {} }

  _bindLifecycle() {
    if (typeof document === 'undefined') return;
    const urgent = () => { if (this._dirty) this.flush(); };
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') urgent(); });
    addEventListener('pagehide', urgent);
  }
}

if (typeof window !== 'undefined') window.KBStore = KBStore;
