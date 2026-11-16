# Kiểm chứng mục C4 — Cảnh báo file lớn + xuất ra OPFS + blob concat

Ngày: 2026-11-16T12:17:30.354Z (giờ UTC). Harness: exp/verify_analysis_c4.mjs.

## Kết quả: 14/14 PASS

### static_node_check_sw_modal: PASS (0.3s)
node --check OK: app.js, sw.js, stream.js, opfs.js; CACHE_VERSION=v10-20261116 (cần v10-20261116); precache có opfs.js=true (tổng 126 mục); index.html nạp opfs.js + modal Tiếp tục/Hủy + nút Dọn file tạm + CSS modal: nodeCheck=true, swVersionV10=true, swPrecacheOpfs=true, opfsOnDisk=true, indexLoadsOpfs=true, modalIds=true, modalButtons=true, cleanupBtn=true, modalCss=true

### vm_opfs_fallback_and_parts: PASS (0s)
PB3OPFS API đủ (supported/writeOutput/readOutput/deleteOutput/listOutputs/cleanupOutputs/cleanupStaleOnBoot/CHUNK_BYTES=8MB); trong vm (không có navigator.storage.getDirectory): supported()=false; writeOutput() không ném, trả {"ok":false,"via":"memory","name":"extract.zip","error":"trình duyệt này không có OPFS (navigator.storage.getDirectory)"} (fallback về RAM, kèm lý do thật); toParts(20MB) = 3 part, cỡ 8388608/8388608/4194304 (8MB+8MB+4MB, subarray không copy toàn bộ); safeName: ["extract.zip","a_b_.zip","output.bin","my file.json"]; listOutputs()=[] khi không có OPFS; cleanupOutputs()=0; classic script (không export).

### static_c4_wiring: PASS (0s)
kiểm tra wiring C4: confirmFn=true, threshold400=true, modalStateHook=true, modalStrongIsh=true, gateSingle=true, gateBatch=true, gateBeforeRead=true, opfsMin1MB=true, downloadOpfsWrite=true, downloadOpfsState=true, downloadCleanup=true, shareReadsOpfs=true, runOpAwaitDownload=true, runOpReleaseBuffer=true, staleCleanupBoot=true, streamMakeBlobParts=true, streamStoredSlice=true, streamNoWholeConcat=true, opfsChunkedWrite=true, opfsNoCoi=true. Lưu ý: ngưỡng 600/800MB, blob concat (new Blob(parts)) và copy-through stored (slice) đã có từ mục 5 — C4 chỉ thêm cổng xác nhận + OPFS, không viết lại đường nóng.

### shell_size_after_c4: PASS (0s)
shell lần tải đầu sau C4: index.html raw=55640B gzip=15854B; kb-store.js raw=8192B gzip=3212B; worker-pool.js raw=14404B gzip=5058B; stream.js raw=18358B gzip=6141B; opfs.js raw=12753B gzip=4045B; app.js raw=198447B gzip=55879B; gt.js raw=34379B gzip=9599B | TỔNG raw=342173B (334.2KB), gzip=99788B (97.4KB). opfs.js mới raw=12753B; app.js tăng do download() OPFS + modal xác nhận. Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C4 không đổi kết luận.

### browser_modal_450mb_cancel: PASS (0.6s)
chọn file 450MB → modal hiện, nội dung="• c4_fake450.unity3d (450 MB)\n\nFile lớn (>400MB). Trích xuất/nén đọc từng phần (streaming) nhưng RAM đỉnh ước tính ~2× dung lượng file (file + dữ liệu đang giải nén); trên iPhone vẫn có thể làm chết tab.\n\nBấm “Tiếp tục” "; __PB3_LARGE_FILE_STATE lúc hiện={"visible":true,"files":[{"name":"c4_fake450.unity3d","size":471859200}],"decision":null}; TRƯỚC khi bấm: file-info hiện=false (chưa nạp vào RAM), boot={"booted":false,"workerCreated":false} (chưa boot — cổng chặn đứng trước cả ensureBoot); bấm Hủy → modal ẩn=true, decision=false, file-info hiện=false, boot sau hủy={"booted":false,"workerCreated":false} (dừng hẳn, không chạy ngầm)

### browser_modal_450mb_continue: PASS (2.5s)
chọn lại 450MB → bấm Tiếp tục → file-info="c4_fake450.unity3d · 450 MB" (đã nạp xong 450MB vào RAM sau khi user xác nhận); decision=true

### browser_modal_850mb_ish_hint: PASS (0.6s)
chọn file 850MB → modal hiện, nội dung="• c4_fake850.unity3d (850 MB)\n\nFile rất lớn (>800MB). Tab trình duyệt trên iPhone chỉ có khoảng 1–1,5GB RAM — file này cộng với dữ liệu đang giải nén có thể làm tab bị tắt giữa chừng. Nên dùng bản iSH cho file này.\n\nBấm “Tiếp tục” để mở file trên web (nên đóng các tab khác trước), hoặc bấm “Hủy” để " (có mốc >800MB + gợi ý dùng bản iSH + lý do RAM tab ~1–1,5GB); trước/sau khi bấm Hủy: file-info hiện=false/false (không nạp 850MB vào RAM — chỉ kiểm tra cảnh báo + Hủy, không chạy tiếp)

### browser_drop_small_no_modal: PASS (0s)
thả file 2MB vào dropzone → file-info="c4_entry_2mb.zip · 9 B"; modal hiện=false (file <=400MB không hỏi — hành vi cũ giữ nguyên)

### browser_drop_450mb_cancel: PASS (2.5s)
thả file 450MB vào dropzone → modal hiện, nội dung nhắc tên file="• drop450.unity3d (450 MB)\n\nFile lớn (>400MB). Trích xuất/nén đọc từng phần (streaming) nhưng RAM đỉnh ước tính ~2× dung"; trước/sau Hủy: file-info hiện=false/false (không nạp)

### browser_batch_450mb_modal: PASS (1.6s)
chọn lô [c4_entry_2mb.zip 2MB + file 450MB] → 1 modal liệt kê file lớn="• c4_fake450.unity3d (450 MB)\n\nFile lớn (>400MB). Trích xuất/nén đọc từng phần (streaming) nhưng RAM đỉnh ước tính ~2× dung lượng file (file + dữ liệu đang giải"; bấm Hủy → nút chạy batch="Chạy batch" (lô cũ rỗng giữ nguyên, chưa đọc file nào); chọn lại → Tiếp tục → nút chạy batch="Chạy batch (2 file)" (cả 2 file đã nạp)

### browser_opfs_write_read_download_cleanup: PASS (1.7s)
chọn ZIP fixture (2MB, entry stored big.bin) → Liệt kê nhanh (EOCD, không boot) → bấm Tải entry → __PB3_OPFS_STATE={"supported":true,"minBytes":1048576,"lastVia":"opfs","lastName":"big.bin","lastSize":2097152,"lastHasBlob":false,"fallbackReason":null} (ghi OPFS, KHÔNG giữ blob trong RAM: lastHasBlob=false); hint="Đang tải về big.bin (2.0 MB) từ bộ nhớ tạm (OPFS) — trình duyệt sẽ hỏi thì bấm Tải về, giữ tab mở; file lớn có thể cần vài phút. Xong thì bấm “Dọn file tạm”."; đọc lại từ OPFS: {"size":2097152,"name":"big.bin","list":[{"name":"big.bin","size":2097152}]}; sha256 nội dung đọc từ OPFS=b03652b11512eb21116c6dee23443d6edf4ad2536f41b32a576bb268ab4f9ea8 (khớp file gốc b03652b11512eb21…); file tải về thật từ OPFS: tên=big.bin, sha256=b03652b11512eb21116c6dee23443d6edf4ad2536f41b32a576bb268ab4f9ea8 (khớp); nút Dọn file tạm hiện=true; sau khi bấm Dọn: listOutputs=[], lastVia=null (OPFS trống)

### browser_opfs_fallback_no_crash: PASS (0.7s)
__PB3_NO_OPFS=true → __PB3_OPFS_STATE={"supported":false,"minBytes":1048576,"lastVia":"memory","lastName":"big.bin","lastSize":2097152,"lastHasBlob":true,"fallbackReason":"đã tắt OPFS để kiểm tra fallback (__PB3_NO_OPFS)"} (supported=false, rơi về đường RAM kèm lý do thật); hint="Đang tải về big.bin (2.0 MB) — trình duyệt sẽ hỏi thì bấm Tải về, giữ tab mở; file lớn có thể cần vài phút. (Trình duyệt này không ghi được OPFS — đã tắt OPFS để kiểm tra fallback (__PB3_NO_OPFS) — nê" (ghi rõ không ghi được OPFS, khuyên lưu ngay); file tải về: sha256=b03652b11512eb21116c6dee23443d6edf4ad2536f41b32a576bb268ab4f9ea8 (khớp file gốc); không pageerror ở context này

### browser_blob_concat_parts: PASS (0s)
PB3Stream.makeOutputBlob([BEAR-, TEA-, C4]) → size=11, nội dung="BEAR-TEA-C4" (new Blob(parts) nối đúng, không copy ArrayBuffer); textToBlobParts(150000 ký tự) = 3 part 65536/65536/18928, tổng 150000 byte (chia 64KB bằng subarray). Đường copy-through stored (Blob.slice) đã kiểm chứng nội dung ở B5 (sha256 khớp).

### browser_390_no_overflow_no_errors: PASS (0s)
390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror cả phiên (4 context): không có

## File thay đổi
- `site/opfs.js` (mới): PB3OPFS — ghi output ra OPFS từng phần 8MB (Blob.slice/subarray, không concat toàn bộ), đọc lại dạng File, xóa/liệt kê/dọn file tạm, dọn file quá 7 ngày lúc mở trang, fallback {ok:false, via:"memory"} kèm lý do; hook `__PB3_NO_OPFS` để ép fallback.
- `site/index.html`: nạp `opfs.js`; hộp thoại `#large-file-modal` (nút “Tiếp tục”/“Hủy”); nút “Dọn file tạm” trên thanh Lưu file; CSS `.modal`/`.modal-card`.
- `site/app.js`: `confirmLargeFile()` — hỏi TRƯỚC khi đọc file >400MB (đơn, kéo-thả, batch), >800MB cảnh báo mạnh + gợi ý iSH + lý do RAM tab ~1–1,5GB; `download()` ghi output >=1MB ra OPFS rồi mới share/tải (đọc File từ OPFS), fallback RAM ghi rõ lý do; dọn OPFS sau khi chia sẻ xong / bấm ✕ / bấm “Dọn file tạm” / file quá 7 ngày lúc mở trang; `runOp()` đợi ghi xong rồi bỏ tham chiếu buffer output; hook `__PB3_OPFS_STATE`/`__PB3_LARGE_FILE_STATE`.
- `site/stream.js`: chỉ cập nhật comment đầu file (OPFS đã làm ở C4 — trước đây ghi “làm SAU đợt này”).
- `site/sw.js`: CACHE_VERSION v9 → v10; thêm `opfs.js` vào PRECACHE_URLS (theo luật đổi precache phải đổi version).
- Harness pin version v9 → v10 (kèm theo luật trên): `exp/verify_item6.mjs` (SHELL_FILES thêm `opfs.js`, “5 JS” → “6 JS”), `exp/verify_analysis_c3.mjs`, `exp/verify_item8.mjs`, `exp/check_item7_static.py`, `exp/check_item8_static.py`.
- Harness + fixture mới: `exp/verify_analysis_c4.mjs`, `exp/fixtures/c4_entry_2mb.zip` (entry stored `big.bin` 2MB, sha256 b03652b1…).

## Đã đúng từ mục 5 — C4 KHÔNG sửa lại
- Ngưỡng cảnh báo 600MB/800MB/2GB (`PB3Stream.sizeWarning`, `sizeWarningFor`) và câu chữ cảnh báo — giữ nguyên; C4 chỉ thêm cổng xác nhận chặn TRƯỚC khi đọc (400MB) + mốc 800MB đã có sẵn.
- Blob concat: `makeOutputBlob(new Blob(parts))`, `download()` nhận mảng part, `textToBlobParts` chia 64KB bằng subarray — đã đúng, chỉ kiểm chứng lại (B7).
- Copy-through stored: `extractZipEntry` dùng `file.slice` (không qua heap), `extractZipEntryFromBuffer` dùng subarray — đã đúng, kiểm chứng bằng nội dung (B5 sha256 khớp).
- Xác nhận trích xuất >400MB bằng `confirm()` native trước khi chạy (đợt sửa 2026-11-16) — giữ nguyên, là cổng thứ hai sau cổng chọn file của C4.

## Giới hạn thật còn lại (mục 4)
- Input vẫn giữ 3 bản trong lúc chạy op qua worker (state.file.data + bản slice(0) gửi qua postMessage transfer + bản trong Pyodide FS của worker) — giao thức send-once/clone-fix bắt buộc giữ bản gốc để gửi lại khi lỗi; sửa triệt để cần viết lại SDK, ngoài phạm vi C4. C4 chỉ gỡ được phía output: sau khi ghi OPFS xong, buffer output của worker được bỏ tham chiếu (runOp) và state không giữ Blob (chỉ giữ tên file OPFS).
- Output từ worker vẫn về main thread dưới dạng 1 ArrayBuffer duy nhất rồi mới ghi OPFS từng phần — ghi OPFS không copy thêm, nhưng bản ArrayBuffer gốc vẫn tồn tại trong lúc ghi (giới hạn của worker protocol hiện tại).
- `extractZipEntryFromBuffer` đường deflate gói vùng nén (subarray) vào `new Blob([comp])` — Blob copy vùng nén đó (chỉ bằng cỡ entry nén, không phải cả file); đường stored thì không copy.

## Safari thật — chưa thử được
- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.
- CẦN JOSEPH (Safari thật, iPhone): chọn file >400MB → xem hộp thoại “Tiếp tục”/“Hủy”; file >800MB → xem gợi ý iSH; trích xuất file lớn → bấm “Lưu / Chia sẻ” sau khi ghi OPFS (xem file có lưu vào Files được không, file tạm có dọn sau khi share xong không); thử “Dọn file tạm”.

