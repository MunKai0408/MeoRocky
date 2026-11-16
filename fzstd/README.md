# fzstd (vendored) — bộ giải nén Zstandard thuần JavaScript

Dùng để đọc block nén Zstd trong bundle UnityFS (mục phân tích C5).
Bản web BearTea chỉ ĐỌC Zstd (không ghi): bundle ghi lại dùng block
không nén (cờ nén 0).

- Gói gốc: `fzstd` 0.1.1 — https://github.com/101arrowz/fzstd
  (npm: https://www.npmjs.com/package/fzstd)
- Tác giả: Arjun Barrett — Giấy phép: **MIT** (xem file `LICENSE` kèm
  theo trong thư mục này; bản quyền "Copyright (c) 2020 Arjun Barrett").
- File đã lấy: `esm/index.mjs` của gói npm 0.1.1 (bản ESM, 24.407 byte,
  sha256 `1a901fc5a58c349bbdb4f2aa1e688637ef56891ec58243a9f0cd863166b0d2a4`),
  đổi tên thành `index.mjs` để worker.js import tương đối
  (`./fzstd/index.mjs`). Không sửa nội dung.
- Chỉ dùng hàm `decompress(data, out?)`: giải nén 1 frame Zstd thành
  Uint8Array; khi không truyền `out` thì cỡ giải nén đọc từ header của
  frame (frame do encoder Zstd chuẩn ghi luôn kèm content size).
- Vendored cùng nguồn (same-origin) như Pyodide — không tải từ CDN lúc
  chạy; `sw.js` precache file này để lần thăm sau vẫn chạy offline.
