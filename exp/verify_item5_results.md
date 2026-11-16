# Mục 5 — streaming reader + copy-through + DecompressionStream + cảnh báo file lớn

Ngày kiểm chứng: 2026-11-16T08:24:34.856Z (giờ UTC). Chạy: `node verify_item5.mjs`.

## Kết quả: 25/25 PASS

### node_size_warning_thresholds: PASS (0s)
ngưỡng: 599.9999990463257MB->ok, 600MB->ok, 600.0000009536743MB->ok, 800MB->ok, 800.0000009536743MB->ok, 2048MB->ok, 2048.0000009536743MB->ok; >600MB: "Lưu ý: file 650 MB khá lớn (>600MB), tab trình duyệt có thể tốn nhiều RAM. Vẫn thử chạy."; >800MB: "File 900 MB rất lớn (>800MB). Tab trình duyệt trên iPhone có thể hết RAM và bị tắt giữa chừng — nên dùng bản iSH cho file này. Vẫn có thể thử tiếp trên web, nhưng hãy đóng các tab khác trước."; >2GB: "File 3.0 GB quá lớn cho bản web (>2GB). Dùng bản iSH cho file này."

### node_eocd_ambig_matches: PASS (0.1s)
JS: a/en.json 40B m8 file | b/en.json 40B m8 file; kỳ vọng (python zipfile): a/en.json 40B m8 | b/en.json 40B m8

### node_eocd_nested_fallback: PASS (0s)
game1.zip: nested=true, inner.zip kind=container, tổng entry JS=10 (bridge sẽ liệt kê đệ quy dạng inner.zip::... nên JS KHÔNG dùng danh sách này)

### node_eocd_flat_kinds: PASS (0s)
kinds JS: data/big-stored.txt=file, data/en.json=file, data/sharedassets0.assets=unity, data/font.ttf=excluded, logs/debug.log=excluded, test/x.txt=excluded, data/bz2.bin=file; kỳ vọng: data/big-stored.txt=file, data/en.json=file, data/sharedassets0.assets=unity, data/font.ttf=excluded, logs/debug.log=excluded, test/x.txt=excluded, data/bz2.bin=file; methods stored/deflate/bzip2: big-stored.txt=m0, en.json=m8, bz2.bin=m12

### node_copythrough_stored: PASS (0s)
stored 3800 byte, byte-identical với Python zipfile: true; copiedThrough=true; heap delta Node khi trích: -573 KB (Blob Node vẫn giữ dữ liệu trong bộ nhớ riêng — con số này chỉ là heap JS, không phải RAM tab Safari)

### node_decompress_deflate: PASS (0s)
deflate en.json 34 byte, khớp Python zipfile và khớp zlib.inflateRawSync: true

### node_unsupported_method_fallback: PASS (0s)
bzip2 (method 12) ném code=E_UNSUPPORTED_METHOD (app.js bắt và gợi ý dùng tab Trích xuất/bridge)

### node_decompress_gzip: PASS (0s)
gzip 120 byte nén -> 12900 byte, khớp Python gzip và Python gzip.open đọc lại: true

### node_gzip_multimember_limit: PASS (0.1s)
gzip 2 member nối nhau, tiến trình con: REJECTED ERR_TRAILING_JUNK_AFTER_STREAM_END (giới hạn đã biết của đường nhanh; file .gz nhiều member vẫn xem được qua bridge list/diagnose, chỉ nút tải nhanh không dùng được)

### node_output_multipart_blob: PASS (0s)
JSON 200030 ký tự -> 4 part (mỗi part ≤64KB), Blob 200031 byte, round-trip giống hệt; blobPartsByteLength khớp tổng byte

### node_eocd_edge_cases: PASS (0s)
bytes lạ -> {"ok":false,"reason":"not-zip"}; ZIP cắt cụt 100 byte -> ok=false reason=not-zip; EOCD báo ZIP64 -> zip64=true (fallback bridge)

### node_eocd_bigtest_measure: PASS (0s)
bigtest.zip 558899 byte, 4000 entry deflate: EOCD liệt kê trong 17ms, heap delta 2169 KB (file đã nằm trong RAM từ trước — chỉ đo phần liệt kê, không đo phần đọc file; mức RAM tiết kiệm so với đường bridge trên Safari thật: CHƯA ĐO — cần Joseph đo trên iPhone)

### bridge_parity_ambig: PASS (0s)
bridge kind=zip total=2; JS total=2, nested=false; khớp path/size/kind từng entry: true

### bridge_parity_bigtest_4000: PASS (0.3s)
bridge total=4000; JS total=4000; khớp từng entry: true

### bridge_parity_flat_kinds: PASS (0s)
bridge total=7; JS total=7; khớp từng entry (path/size/kind): true

### bridge_parity_nested_fallback: PASS (0s)
bridge total=11 (có entry lồng dạng :: : true, vd inner.zip::en.json); JS nested=true -> app.js fallback về bridge, không dùng danh sách JS

### static_checks: PASS (0.2s)
✓ node --check stream.js + app.js
✓ stream.js không có new SharedArrayBuffer/Atomics/pthreads
✓ stream.js không bật COOP/COEP
✓ index.html nạp stream.js trước app.js
✓ app.js dùng PB3Stream.sizeWarning + fastListEntries + download(parts)
✓ bridge.py không thêm op mới (không có def liên quan stream)
✓ worker.js không đổi protocol (vẫn send-once/files + dest/zip_path)

### browser_boot_390: PASS (5.8s)
status: "Sẵn sàng — 20 plugin đang chạy · font đi kèm: đủ 142/142 (134 chữ cái + 8 dấu)"; pageerror: không có; scrollWidth=390 clientWidth=390 (không tràn ngang)

### browser_fast_list_and_entry_download: PASS (0.3s)
ghi chú EOCD: true; tải a/en.json: 40 byte, byte-identical với Python zipfile: true (deflate qua DecompressionStream, nút Tải trong tab Danh sách)

### browser_entry_stored_and_deflate: PASS (0.2s)
ghi chú EOCD: true; stored big-stored.txt: 3800 byte, khớp: true (copy-through Blob.slice); deflate en.json: 34 byte, khớp: true

### browser_gzip_member_download: PASS (1s)
nút gzip hiện: true; bản giải nén: 12900 byte, byte-identical với Python gzip: true

### browser_warn_600mb: PASS (0.7s)
file sparse 610MB: #error hiện=true, text: "Lưu ý: file 610 MB khá lớn (>600MB), tab trình duyệt có thể tốn nhiều RAM. Vẫn thử chạy."; file-info: "item5_610m.zip · 610 MB" (cảnh báo còn hiện sau khi file đã nạp — trước đây bị clearError xóa)

### browser_warn_800mb_ish: PASS (5.9s)
file sparse 810MB: #error hiện=true, text: "File 810 MB rất lớn (>800MB). Tab trình duyệt trên iPhone có thể hết RAM và bị tắt giữa chừng — nên dùng bản iSH cho file này. Vẫn có thể thử tiếp trên web, nhưng hãy đóng các tab khác trước."

### browser_reject_2gb: PASS (0.6s)
file sparse 2,1GB: #error hiện=true, text: "File 2.1 GB quá lớn cho bản web (>2GB). Dùng bản iSH cho file này." (từ chối trước khi đọc — không tốn RAM)

### browser_390_no_overflow_final: PASS (0s)
scrollWidth=390 clientWidth=390; pageerror cả phiên: không có

## Ghi chú

- Xem chi tiết thay đổi + giới hạn trong `exp/item5_results.md` (báo cáo tay, kèm số đo).
