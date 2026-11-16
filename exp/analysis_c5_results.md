# Kiểm chứng mục C5 — Zstd đọc qua fzstd + ghi block không nén, UnityFS v9 chỉ đọc

Ngày: 2026-11-16T12:41:40.861Z (giờ UTC). Harness: exp/verify_analysis_c5.mjs.

## Kết quả: 13/13 PASS

### static_fzstd_v9_zstd_markers: PASS (0.5s)
node --check OK: worker.js, sw.js, fzstd/index.mjs; fzstd/index.mjs=24407B, export decompress, LICENSE MIT © 2020 Arjun Barrett + README ghi công 101arrowz/fzstd; CACHE_VERSION=v11-20261116 (cần v11-20261116); precache có fzstd/index.mjs=true (tổng 127 mục, cần 127); worker.js dynamic import fzstd + globalThis.pb3_zstd_decompress + fallback null khi lỗi; sdk/unity.py: nodeCheck=true, fzstdOnDisk=true, fzstdExportsDecompress=true, licenseMIT=true, licenseNote=true, swVersionV11=true, swPrecacheFzstd=true, swPrecacheCount127=true, workerDynamicImport=true, workerExposesBridge=true, workerFallbackNull=true, workerNoStaticFzstdImport=true, unityV9Supported=true, unityZstdCode5=true, unityJsBridge=true, unityJsBridgeClearError=true, unityV9OffsetRead=true, unityV9OffsetUse=true, unityV9WriteGuard=true, unityZstdWriteGuard=true, unityPatchV9Refuse=true, unityPatchZstdToNone=true, unityMatrixV9Zstd=true, bridgeKeepsBundleError=true, indexNoteV9=true

### python_c5_tests_system: PASS (0.3s)
python3 -m unittest tests.test_unity.TestBundleV9Zstd -v: Ran 13 test, 11 ok, skipped=2, exit=0 (2 test parse/patch Zstd đầy đủ SKIP vì hệ thống thiếu gói `zstandard` — ghi rõ, không tính PASS giả; chạy đủ ở S2b)

### python_c5_tests_with_zstandard: PASS (0.4s)
venv /tmp/zstdvenv/bin/python: Ran 13 test, 13 ok, skipped=0, exit=0 (bộ giải `zstandard` đứng chỗ fzstd trong module `js` giả)

### shell_size_after_c5: PASS (0s)
shell lần tải đầu sau C5: index.html raw=55759B gzip=15905B; kb-store.js raw=8192B gzip=3212B; worker-pool.js raw=14404B gzip=5058B; stream.js raw=18358B gzip=6141B; opfs.js raw=12753B gzip=4045B; app.js raw=198447B gzip=55879B; gt.js raw=34379B gzip=9599B | TỔNG raw=342292B (334.3KB), gzip=99839B (97.5KB). fzstd/index.mjs KHÔNG nằm trong shell lần tải đầu (worker import lúc boot): raw=24407B gzip=6843B; cộng vào tổng tải sau boot thì raw=366699B. Ngưỡng raw 100KB vẫn chưa đạt như mục 6 đã ghi (app.js monolith) — C5 không đổi kết luận.

### static_fixture_headers: PASS (0s)
header fixture: zstd=v7/comp5/flags0x45, v9=v9/comp0/flags0x40, v9zstd=v9/comp5/flags0x45, lz4=v7/comp2/flags0xc2, enc=v8/comp0/flags0x1440; blocks-info v9 (không nén): 2 block [{"unc":3774,"comp":3774,"fl":64,"off":0},{"unc":3773,"comp":3773,"fl":64,"off":3776}] (offset block 2 = 3776 = căn 16 của 3774 — chứng minh block không nằm sát nhau, phải đọc bằng offset)

### browser_boot: PASS (9.5s)
boot tới Sẵn sàng (390px)

### browser_fzstd_direct: PASS (0s)
import ./fzstd/index.mjs trong trang -> decompress(c5_zstd_frame.bin 57B) = "Xin chao BearTea - cau noi Zstd thu nghiem 12345" (48 byte, khớp từng byte câu mẫu); dữ liệu rác [1,2,3,4,5] ném lỗi=true (không trả rác)

### browser_inspect_zstd_v7: PASS (0.8s)
Inspect c5_bundle_zstd.unity3d (v7, mã nén 5) -> unity-result="Bundle Unity: 2019.4.40f1 · nén zstd · 2 entryCAB-0123456789abcdef0123456789abcdef — Unity 2019.4.40f1 · 3 object · 2 TextAsset#1001 greeting: Xin chao 9 Kings!#1002 longtext: Dòng dài để thử chuỗi dài: xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xi"; thẻ Trang chủ="File: c5_bundle_zstd.unity3dUnity Version: 2019.4.40f1 · bundle zstd4 object · 3 TextAssetĐã sẵn sàng An toàn & Riêng tưXử lý 100% trên thiết bị của bạn — file "; #error="" (TextAsset đọc được = block Zstd đã qua fzstd trong worker rồi về Python qua js.pb3_zstd_decompress)

### browser_inspect_v9_none: PASS (0.2s)
Inspect c5_bundle_v9.unity3d (v9, mã nén 0, blocks-info có offset u64) -> unity-result="Bundle Unity: 5.x.x · nén none · 2 entryCAB-0123456789abcdef0123456789abcdef — Unity 6000.0.23f1 · 3 object · 2 TextAsset#4001 greeting: Xin chao Unity 6!#4002 longtext: Dòng dài để thử chuỗi dài: xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào"; #error="" (fixture header version=9 đã kiểm ở S4; parse thành công trên trình duyệt thật chứng minh v9 đọc được, trước C5 bị từ chối E_UNSUPPORTED_FORMAT)

### browser_inspect_v9_zstd: PASS (0.2s)
Inspect c5_bundle_v9_zstd.unity3d (v9 + mã nén 5) -> unity-result="Bundle Unity: 5.x.x · nén zstd · 2 entryCAB-0123456789abcdef0123456789abcdef — Unity 6000.0.23f1 · 3 object · 2 TextAsset#4001 greeting: Xin chao Unity 6!#4002 longtext: Dòng dài để thử chuỗi dài: xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào thế giới! xin chào"; #error=""

### browser_inspect_lz4_control: PASS (0.1s)
Inspect c5_bundle_lz4_control.unity3d (v7, LZ4) -> unity-result="Bundle Unity: 2019.4.40f1 · nén lz4 · 2 entryCAB-0123456789abcdef0123456789abcdef — Unity 2019.4.40f1 · 3 object · 2 TextAsset#1001 greeting: Xin chao 9 Kings!#1002 longtext: Dòng dài để thử chuỗi dài"; #error="" (hồi quy: đường đọc cũ không vỡ)

### browser_encrypted_refused_clearly: PASS (0.1s)
Inspect c5_bundle_encrypted.unity3d (cờ 0x1400) -> banner #error="Inspect Unity lỗi: E_ENCRYPTED: Dữ liệu hoặc binary được mã hóa/bảo vệ."; unity-result trống (không parse được gì — từ chối sạch, không crash, không "Lỗi không rõ")

### browser_390_no_overflow_no_errors: PASS (0s)
390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror cả phiên: không có

## File thay đổi
- `site/fzstd/index.mjs` (mới, vendored): fzstd 0.1.1, bản ESM `esm/index.mjs` của gói npm (24.407 byte, sha256 1a901fc5…), export `decompress`; MIT © 2020 Arjun Barrett — xem `site/fzstd/LICENSE` (nguyên văn) + `site/fzstd/README.md` (ghi công + nguồn + cách dùng). Same-origin như Pyodide, không tải từ CDN lúc chạy.
- `site/pb3/sdk/unity.py`: `SUPPORTED_BUNDLE_VERSIONS` thêm 9 (chỉ đọc); `COMPRESSION_NAMES` thêm 5="zstd" (quy ước mở rộng của PB3 — UnityPy master 2026-11-16 và tài liệu Unity không có cờ Zstd; mã 5 là mã trống tiếp theo sau LZHAM=4); `BundleBlock.offset` (u64, v9); `UnityBackend.decompress` thêm nhánh Zstd qua `js.pb3_zstd_decompress` (ngoài trình duyệt báo E_UNSUPPORTED_FORMAT rõ ràng "cần fzstd trên web"); `compress`/`build_bundle` từ chối ghi Zstd và ghi v9 với lý do rõ ràng; `parse_bundle` + `stream_bundle_entries` đọc v9 bằng offset u64 (block căn 16, không đọc tuần tự); `apply_patch` từ chối patch bundle v9 sớm, bundle gốc Zstd thì dựng lại bằng compression "none" (cờ nén 0); `support_matrix()` thêm 2 hàng v9/Zstd.
- `site/pb3/bridge.py`: `_parse_unity_bytes` không còn nuốt lỗi của bundle UnityFS thật — file có magic UnityFS mà `parse_bundle` lỗi (mã hoá / version / nén chưa hỗ trợ / hỏng) thì giữ nguyên lỗi thật thay vì rơi xuống `parse_serialized` rồi báo sai (lỗi C5 phát hiện: bundle mã hoá bị báo E_UNSUPPORTED_FORMAT chung chung thay vì E_ENCRYPTED). Chỉ rơi xuống khi không phải UnityFS (file .assets độc lập).
- `site/pb3/sdk/support_matrix.py`: dòng fixture-only ghi "UnityFS v6/v7/v8/v9 + SerializedFile v15–23 (v9 chỉ đọc, Zstd chỉ đọc trên web, fixture)".
- `site/worker.js`: boot dynamic import `./fzstd/index.mjs` rồi công bố `globalThis.pb3_zstd_decompress(data, expectedSize)` (đúng tiền lệ `js.pb3_google_translate`: phải gán globalThis thật); lỗi nạp thì gán null + báo trạng thái, boot không chết; frame không kèm content size thì thử lại với buffer đúng cỡ kỳ vọng.
- `site/sw.js`: CACHE_VERSION v10 → v11-20261116; thêm `fzstd/index.mjs` vào PRECACHE_URLS (126 → 127 mục; sửa luôn comment cũ ghi sai 127 khi mới có 126).
- `site/index.html`: ghi chú tab Inspect "UnityFS v6–v9 — v9 chỉ đọc; block nén Zstd đọc được qua fzstd".
- `site/pb3/tests/test_unity.py`: lớp mới `TestBundleV9Zstd` (13 test) + `test_unsupported_version_raises` đổi v9 → v10 (v9 nay đã hỗ trợ).
- `site/pb3/tests/test_polish.py`: `test_u1_container_per_entry_resilient_direct_hard_abort` — file v8 đổi nhãn thành v9 nay là v9 SAI CẤU TRÚC nên kỳ vọng E_CORRUPT thay vì E_UNSUPPORTED_FORMAT (v9 thật đã hỗ trợ đọc từ C5; hard-abort và dòng "unsupported" trong container giữ nguyên).
- `exp/verify_item6.mjs`: danh sách precache kỳ vọng thêm `fzstd/index.mjs` (126 → 127 mục) — không thì A3 báo thừa; `shell_raw_under_100kb` vẫn FAIL đã biết (xem hồi quy).
- `site/pb3/tests/fixtures/unity/build_fixtures.py`: builder thêm compression "zstd" (qua gói `zstandard`, chỉ lúc sinh) + version 9 (offset u64, block căn 16) + `block_split`; `main()` sinh thêm `sample_v9.unity3d`, `sample_zstd.unity3d`, `sample_v9_zstd.unity3d` (thiếu `zstandard` thì bỏ qua Zstd và in rõ lý do).
- `site/pb3/tests/fixtures/unity/sample_v9.unity3d` (7.790 byte), `sample_zstd.unity3d` (532 byte), `sample_v9_zstd.unity3d` (842 byte) (mới); `nested.ipa`/`nested_v8.ipa` sinh lại (chỉ khác timestamp ZIP, nội dung bundle giữ nguyên — đã kiểm chứng sha256 các fixture cũ khác không đổi).
- Harness + fixture mới: `exp/verify_analysis_c5.mjs`, `exp/build_c5_fixtures.py`, `exp/fixtures/c5_bundle_zstd.unity3d`, `c5_bundle_v9.unity3d`, `c5_bundle_v9_zstd.unity3d`, `c5_bundle_lz4_control.unity3d`, `c5_bundle_encrypted.unity3d`, `c5_zstd_frame.bin` (frame Zstd thật của câu mẫu, 57 byte).
- Harness cũ ghim version SW v10 → v11 (theo luật đổi precache phải đổi version): `exp/verify_analysis_c3.mjs`, `exp/verify_analysis_c4.mjs`, `exp/check_item7_static.py`, `exp/check_item8_static.py`, `exp/verify_item8.mjs` (chuỗi ghi chi tiết).

## Khảo sát trước khi làm (trạng thái cũ)
- `sdk/unity.py` trước C5: chỉ hỗ trợ UnityFS v6/v7/v8, nén NONE/LZMA/LZ4/LZ4HC (mã 0–3) + LZHAM liệt kê nhưng không giải được (mã 4); version 9 và mã nén 5 đều bị từ chối E_UNSUPPORTED_FORMAT; đường ghi `build_bundle` giữ compression gốc của bundle (patch bundle LZ4/LZMA ghi lại bằng LZ4/LZMA — KHÔNG phải cờ 0); bundle mã hoá ĐÃ từ chối E_ENCRYPTED kèm flag (cả `parse_bundle` lẫn `stream_bundle_entries`) — C5 chỉ kiểm chứng lại, không sửa.
- Khác biệt v9 (đối chiếu UnityPy master ngày 2026-11-16, `helpers/CompressionHelper.py` + `files/BundleFile.py`): CHỈ có BlockInfo thêm u64 `offset` sau cờ u16 và mỗi block nén căn 16 byte trong vùng block data (đọc bằng `base_offset + offset`); header vẫn căn 16 từ v7, entry vẫn (offset u64, size u64, flags u32, tên) nên file lớn đã có từ trước. UnityPy master KHÔNG có cờ nén Zstd nào (CompressionFlags chỉ 0–4) và tài liệu Unity 6000 chỉ liệt kê LZMA/LZ4/Uncompressed — mã 5 là quy ước mở rộng của PB3, chưa có bundle Unity thật nào được thấy dùng Zstd.
- Cầu nối JS→Python: đúng tiền lệ Google Translate — worker.js gán hàm vào `globalThis`, Python gọi qua `import js` + `getattr(js, ...)`. Khác biệt: fzstd là hàm ĐỒNG BỘ (không Promise như translate), Pyodide chuyển bytes Python → Uint8Array cho JS và trả Uint8Array về dạng JsProxy (dùng `bytes(...)`, dự phòng `to_py()`).

## Ranh giới v9 / Zstd (đã thực thi trong code)
- v9 CHỈ ĐỌC: parse + stream được; `build_bundle(version=9)` từ chối E_UNSUPPORTED_FORMAT; `apply_patch` trên bundle v9 từ chối sớm (vì patch luôn dựng lại container, kể cả PATCH_INPLACE).
- Zstd CHỈ ĐỌC trên web: parse/stream qua fzstd; ngoài trình duyệt (CPython/iSH — không có module `js` hoặc worker chưa nạp được fzstd) báo E_UNSUPPORTED_FORMAT "Block Zstd cần bộ giải nén fzstd — chỉ có trên web BearTea."; ghi Zstd từ chối, bên gọi dựng lại bằng block không nén (cờ nén 0) — đã kiểm chứng bằng test `test_patch_zstd_bundle_rebuilds_uncompressed` (bundle ra compression "none", mọi block flags & 0x3F == 0).
- Đường ghi bundle thường (LZMA/LZ4/none) KHÔNG đổi: vẫn giữ compression gốc như trước C5 (chỉ riêng nguồn Zstd mới ép về "none" vì không có encoder).
- Bundle mã hoá: từ chối E_ENCRYPTED kèm flag (đã có từ trước, C5 kiểm chứng qua UI: banner "Inspect Unity lỗi: E_ENCRYPTED: Dữ liệu hoặc binary được mã hóa/bảo vệ.").

## Giới hạn thật còn lại
- Mã nén Zstd = 5 là quy ước của PB3, chưa có bằng chứng bundle Unity thật nào dùng; mọi fixture Zstd/v9 đều TỔNG HỢP (sinh bằng builder đối chiếu UnityPy), chưa kiểm chứng trên bundle v9/Zstd thật — cần Joseph hoặc game thật dùng Unity 6.2+ mới có.
- Giải nén Zstd trên web phụ thuộc file `fzstd/index.mjs` nạp được lúc boot; nếu file thiếu (vd cache cũ, deploy lỗi) thì worker vẫn boot nhưng bundle Zstd báo lỗi rõ ràng thay vì đọc được — harness B1/B2 sẽ FAIL trong trường hợp đó (đúng hành vi).
- Patch bundle v9 hoàn toàn chưa làm được (từ chối) — muốn patch v9 thì cần dựng lại bundle v9 (ghi offset u64 + căn 16), ngoài phạm vi C5.
- Shell lần tải đầu vẫn vượt 100KB raw như mục 6 đã ghi (xem S3) — fzstd (24KB) nạp lúc boot nên không tính vào shell đầu, nhưng tổng tải sau boot tăng tương ứng.

## Safari thật — chưa thử được
- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace; KHÔNG tự đăng ký tài khoản hay trả phí.
- CẦN JOSEPH (Safari thật, iPhone): mở trang sau khi lên live → Inspect 1 bundle (bất kỳ) để xem boot + fzstd có nạp được không; nếu có bundle Unity 6 mới (v9) thì thử Inspect xem có đọc được không; thử bundle mã hoá (nếu có) xem banner lỗi có hiện rõ "E_ENCRYPTED" không.

