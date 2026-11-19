# lz4js (vendored) — bộ giải nén LZ4 block thuần JavaScript

Dùng để đọc block nén LZ4/LZ4HC trong bundle UnityFS trên web (mục C1,
chương trình cải thiện tốc độ): thay đường chậm `lz4pure` (Python thuần)
bằng decoder JS khi backend chạy ở chế độ NATIVE (inspect / plan / gate /
mod) hoặc khi binding `lz4` không tải được. Binding `lz4` (C/wasm) vẫn
đứng trước khi có mặt ở chế độ AUTO — xem `pb3/sdk/unity.py::decompress`.

- Gói gốc: `lz4js` 0.2.0 — npm `lz4js` (Benzinga/lz4js)
- Tác giả: John Chadwick — Giấy phép: **ISC** (xem file `LICENSE` kèm
  theo trong thư mục này; gói npm gốc không kèm file LICENSE nên bản
  ISC chuẩn được chép lại với tên tác giả theo `package.json`).
- File nguồn đã lấy (npm pack lz4js 0.2.0):
  - `lz4.js` 13.035 byte, sha256
    `16a1df1eb50e74c1cbb0c4f4e947385b4918910a8081cd4bac90318b1052f2bb`
  - `util.js` 1.420 byte, sha256
    `7932a139124a291b71a073228e2651720c5cf0c83cfb4a52fc5eff0b8b5c0e18`
  - `xxh32.js` 1.972 byte, sha256
    `a9cf1e92ce4c3d837c118faae2a0c689c000477422d582c8bcb4fc800bddb130`
- File chạy: `index.mjs` (18.246 byte) — bundle ESM duy nhất bằng esbuild
  0.28.2 từ `lz4js-entry.mjs` (nằm ngoài repo, xem lệnh dưới) + wrapper
  PB3 `pb3DecompressBlock` ở cuối file. Không sửa logic lz4js gốc.
  Lệnh build lại (từ thư mục làm việc có `lz4js-pkg/package/`):
  `npx esbuild lz4js-entry.mjs --bundle --format=esm
  --outfile=lz4js/index.mjs --legal-comments=none`
- Chỉ dùng hàm `pb3DecompressBlock(data, expectedSize)`: giải nén 1 block
  LZ4 thô (không frame) thành Uint8Array đúng `expectedSize` byte.
  `decompressBlock` gốc KHÔNG kiểm tra offset/độ dài (block hỏng có thể
  trả về byte sai mà không ném lỗi) nên wrapper quét token stream trước
  (kiểm tra y hệt `pb3/sdk/lz4pure.py::decompress_block`) và ném
  `Error("LZ4_CORRUPT: ...")` khi input hỏng; Python chuyển thành
  `E_CORRUPT`. Đo thật (Node 24, block 128 KiB): wrapper 134–370 MB/s
  (tùy tải máy lúc đo), `lz4pure` (CPython) ~7,9 MB/s — nhanh hơn
  hàng chục lần.
- Vendored cùng nguồn (same-origin) như Pyodide — không tải từ CDN lúc
  chạy; `sw.js` precache file này để lần thăm sau vẫn chạy offline.
- `worker.js` import động `./lz4js/index.mjs` lúc boot và công bố
  `globalThis.pb3_lz4_decompress`; tải lỗi thì đặt `null` và Python
  rơi về binding/lz4pure như trước (boot không chết).
