// sw.js — Service Worker BearTea (Mục 6).
//
// Nhiệm vụ: precache shell tĩnh + toàn bộ Pyodide vendored + mã PB3 để
// lần thăm thứ 2 chạy được OFFLINE (không mạng vẫn boot tới
// "Sẵn sàng — 20 plugin" và xử lý file đã có trên máy).
//
// KHÔNG dùng COI/COOP/COEP/SharedArrayBuffer/pthreads: Pyodide chạy không
// cần COI, và SW này cũng phải chạy được khi không có COI (Safari iOS).
// SW CHỈ chặn request GET cùng origin nằm trong danh sách precache (hoặc
// điều hướng về trang chính). Mọi thứ khác — dịch Google/proxy Cloudflare
// (khác origin), MyMemory, file output của user (blob:), IndexedDB —
// đi thẳng ra mạng, SW không chạm vào, không cache nhầm.
//
// Đổi bản deploy: đổi CACHE_VERSION dưới đây (vd v6-NGÀY -> v7-NGÀY).
// Bản SW mới tự kích hoạt (skipWaiting) và xóa cache cũ ở sự kiện
// activate, nên user không bao giờ kẹt bản cũ.
//
// Lưu ý dung lượng (đo thật từ đĩa, cùng phương pháp kiểm A3 của
// harness mục 6: tổng các file trong PRECACHE_URLS, chưa tính "./" là
// alias của index.html): precache 17.770.569 B (16,95 MiB) = Pyodide
// 13.605.176 B (12,97 MiB, 7 file) + PB3 2.949.568 B (2,81 MiB) +
// shell/gốc 703.535 B (gồm lz4js 18.246 B) + font 177.264 B + fzstd 24.407 B +
// icons/fixtures 310.619 B. Nạp 1 lần ở lần thăm đầu (nền, không chặn
// vẽ trang); lần sau đọc từ Cache Storage, không tốn mạng nữa.

// ĐỔI SỐ NÀY MỖI KHI DEPLOY BẢN MỚI (bất kỳ file nào trong PRECACHE_URLS
// đổi nội dung). Bản cũ sẽ bị xóa ở activate.
const CACHE_VERSION = "v75-20261122";
const CACHE_NAME = "beartea-" + CACHE_VERSION;
// Cache riêng cho prefetch Pyodide sau first paint (Mục C3): app.js ghi
// file đã tải trước vào đây; SW dùng làm dự phòng khi cache chính trượt
// (vd precache lúc install thiếu file) rồi mới ra mạng.
const PREFETCH_CACHE = "beartea-prefetch-v1";

// Danh sách precache: shell tĩnh + Pyodide vendored + mã PB3 (khớp
// pb3/files.json mà worker.js tải lúc boot, CỘNG file shell nạp bằng
// <script> như colab_handoff.js — ngoài files.json từ v50 vì P0 boot
// 404) + font đi kèm + fixture tự kiểm tra (C2/C3:
// fixtures/selftest.zip để lần thăm 2 offline vẫn chạy được
// "Tự kiểm tra"). Sinh từ đĩa lúc viết file này (142 mục);
// thêm/bớt file vendored thì cập nhật cả danh sách này, không thì
// lần thăm 2 offline sẽ thiếu file đó.
const PRECACHE_URLS = [
  "./",
  "index.html",
  "app.js",
  "worker.js",
  "worker-pool.js",
  "stream.js",
  "opfs.js",
  "kb-store.js",
  "colab_handoff.js",
  "tm.js",
  "gt.js",
  "fzstd/index.mjs",
  "lz4js/index.mjs",
  "manifest.webmanifest",
  "diagnostics.html",
  "fixtures/selftest.zip",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon.png",
  "fonts/BeVietnamPro-Regular.ttf.b64",
  "pyodide/lz4-4.4.5-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
  "pyodide/pyodide-lock.json",
  "pyodide/pyodide.asm.mjs",
  "pyodide/pyodide.asm.wasm",
  "pyodide/pyodide.js",
  "pyodide/pyodide.mjs",
  "pyodide/python_stdlib.zip",
  "pb3/files.json",
  "pb3/bridge.py",
  "pb3/config/default.toml",
  "pb3/core/__init__.py",
  "pb3/core/app.py",
  "pb3/core/color.py",
  "pb3/core/config.py",
  "pb3/core/errors.py",
  "pb3/core/file_dialog.py",
  "pb3/core/hashing.py",
  "pb3/core/ish_compat.py",
  "pb3/core/keepalive.py",
  "pb3/core/limits.py",
  "pb3/core/logging.py",
  "pb3/core/model.py",
  "pb3/core/overall.py",
  "pb3/core/paths.py",
  "pb3/core/progress.py",
  "pb3/core/runstate.py",
  "pb3/core/safe_io.py",
  "pb3/core/tomlmini.py",
  "pb3/plugins/README.txt",
  "pb3/plugins/apple_plist/plugin.py",
  "pb3/plugins/apple_strings/plugin.py",
  "pb3/plugins/castle_db/plugin.py",
  "pb3/plugins/cocos_detect/plugin.py",
  "pb3/plugins/csv_table/plugin.py",
  "pb3/plugins/defold_detect/plugin.py",
  "pb3/plugins/gamemaker_detect/plugin.py",
  "pb3/plugins/generic_strings/plugin.py",
  "pb3/plugins/godot_pck/plugin.py",
  "pb3/plugins/godot_translation/plugin.py",
  "pb3/plugins/json_text/plugin.py",
  "pb3/plugins/po_mo/plugin.py",
  "pb3/plugins/properties/plugin.py",
  "pb3/plugins/resx/plugin.py",
  "pb3/plugins/sqlite_loc/plugin.py",
  "pb3/plugins/unreal_locres/plugin.py",
  "pb3/plugins/unreal_pak/plugin.py",
  "pb3/plugins/xliff/plugin.py",
  "pb3/plugins/xml_strings/plugin.py",
  "pb3/plugins/yaml_ini/plugin.py",
  "pb3/plugins/zip/plugin.py",
  "pb3/registry/__init__.py",
  "pb3/registry/arbitration.py",
  "pb3/registry/compatibility.py",
  "pb3/registry/conformance.py",
  "pb3/registry/evidence.py",
  "pb3/registry/isolation.py",
  "pb3/registry/loader.py",
  "pb3/registry/pluginpkg.py",
  "pb3/registry/registry.py",
  "pb3/registry/scan.py",
  "pb3/registry/template.py",
  "pb3/sdk/__init__.py",
  "pb3/sdk/api.py",
  "pb3/sdk/arbitration.py",
  "pb3/sdk/batch.py",
  "pb3/sdk/bmfont.py",
  "pb3/sdk/colab_job.py",
  "pb3/sdk/compress.py",
  "pb3/sdk/containers.py",
  "pb3/sdk/csvutil.py",
  "pb3/sdk/cff.py",
  "pb3/sdk/discovery.py",
  "pb3/sdk/exporters.py",
  "pb3/sdk/extraction.py",
  "pb3/sdk/font.py",
  "pb3/sdk/font_check.py",
  "pb3/sdk/font_list.py",
  "pb3/sdk/font_writeback.py",
  "pb3/sdk/format_probe.py",
  "pb3/sdk/formats.py",
  "pb3/sdk/gate.py",
  "pb3/sdk/glossary.py",
  "pb3/sdk/godot.py",
  "pb3/sdk/heaps.py",
  "pb3/sdk/il2cpp.py",
  "pb3/sdk/inspect.py",
  "pb3/sdk/ir.py",
  "pb3/sdk/kb.py",
  "pb3/sdk/langdetect.py",
  "pb3/sdk/lz4pure.py",
  "pb3/sdk/manifest.py",
  "pb3/sdk/mod_job.py",
  "pb3/sdk/mono_dll.py",
  "pb3/sdk/patch_pack.py",
  "pb3/sdk/plan.py",
  "pb3/sdk/po_write.py",
  "pb3/sdk/rawscan.py",
  "pb3/sdk/rebuild.py",
  "pb3/sdk/repack.py",
  "pb3/sdk/sdf.py",
  "pb3/sdk/support_matrix.py",
  "pb3/sdk/text_data.py",
  "pb3/sdk/tmp_font.py",
  "pb3/sdk/tmp_sdf.py",
  "pb3/sdk/translate.py",
  "pb3/sdk/types.py",
  "pb3/sdk/unity.py",
  "pb3/sdk/unreal.py",
  "pb3/sdk/vfs.py",
  "pb3/services/__init__.py",
  "pb3/services/batch_tool.py",
  "pb3/services/compress_tool.py",
  "pb3/services/container_tool.py",
  "pb3/services/diagnostics_tool.py",
  "pb3/services/font_tool.py",
  "pb3/services/language_tool.py",
  "pb3/services/plugin_manager.py",
  "pb3/services/translate_tool.py",
  "pb3/services/unity_tool.py",
  "pb3/ui/__init__.py",
  "pb3/ui/handlers.py",
  "pb3/ui/screens.py",
];

// Tập URL tuyệt đối (theo scope đăng ký) để tra nhanh ở fetch.
function precacheSet() {
  const scope = self.registration.scope;
  return new Set(PRECACHE_URLS.map((u) => new URL(u, scope).href));
}
let PRECACHE_SET = null;

// ---- Cài đặt: nạp từng file vào cache (lỗi 1 file không làm hỏng cả
// đợt — file thiếu sẽ được bù lại lúc fetch online rồi ghi vào cache). ----
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      PRECACHE_SET = precacheSet();
      const cache = await caches.open(CACHE_NAME);
      let failed = 0;
      for (const url of PRECACHE_URLS) {
        try {
          await cache.add(url);
        } catch (e) {
          failed++;
          console.warn("[sw] precache lỗi, sẽ bù lúc fetch online:", url, e);
        }
      }
      if (failed) console.warn("[sw] precache thiếu " + failed + "/" + PRECACHE_URLS.length + " file (sẽ bù khi online).");
      await self.skipWaiting();
    })()
  );
});

// ---- Kích hoạt: xóa MỌI cache bearTea cũ (tránh kẹt bản cũ) rồi
// nhận điều khiển các tab đang mở ngay, khỏi cần tải lại trang. ----
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.map((n) => (n !== CACHE_NAME && n.indexOf("beartea-") === 0)
          ? caches.delete(n) : Promise.resolve(false))
      );
      await self.clients.claim();
    })()
  );
});

// ---- Fetch: chỉ chặn GET cùng origin. ----
//  - Điều hướng (navigate): trả bản cache của đúng URL, không có thì trả
//    index.html trong cache (shell offline), cuối cùng mới ra mạng.
//  - URL trong danh sách precache: cache-first; cache trượt thì ra mạng
//    rồi ghi đè vào cache (tự bù file precache lỗi + tự cập nhật).
//  - Mọi request khác (dịch Google/proxy, file user, Range, POST...):
//    KHÔNG gọi respondWith — trình duyệt tự fetch như không có SW.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return; // khác origin: dịch/proxy đi thẳng
  if (req.headers.has("range")) return;            // Range: để trình duyệt tự xử
  if (!PRECACHE_SET) PRECACHE_SET = precacheSet();
  const isPrecache = PRECACHE_SET.has(req.url);
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      const hit = await cache.match(req);
      if (hit) return hit;
      const index = await cache.match(new URL("index.html", self.registration.scope).href);
      if (index) return index;
      return fetch(req);
    })());
    return;
  }
  if (!isPrecache) return; // không phải file tĩnh đã biết: để mạng tự xử
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const hit = await cache.match(req);
    if (hit) return hit;
    // Dự phòng C3: file prefetch sau first paint ghi vào PREFETCH_CACHE
    // (app.js) — dùng được ngay cả khi precache lúc install thiếu file;
    // đồng thời ghi bù vào cache chính để lần sau khỏi tra 2 chỗ.
    try {
      const pre = await caches.open(PREFETCH_CACHE);
      const preHit = await pre.match(req);
      if (preHit) {
        try { await cache.put(req, preHit.clone()); } catch (e) { /* cache đầy: bỏ qua */ }
        return preHit;
      }
    } catch (e) { /* không có cache prefetch: ra mạng như thường */ }
    const res = await fetch(req);
    if (res && res.ok) {
      try { await cache.put(req, res.clone()); } catch (e) { /* cache đầy: bỏ qua */ }
    }
    return res;
  })());
});

// ---- Message: SKIP_WAITING (kích hoạt ngay) + GET_VERSION (hỏi bản). ----
self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "SKIP_WAITING") { self.skipWaiting(); return; }
  if (data.type === "GET_VERSION" && event.ports && event.ports[0]) {
    event.ports[0].postMessage({ version: CACHE_VERSION, cache: CACHE_NAME,
      prefetchCache: PREFETCH_CACHE });
  }
});
