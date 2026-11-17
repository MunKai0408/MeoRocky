# AUTOPILOT.md — Sổ trạng thái BearTea/PB3 (chế độ tự động)

> ĐẦU MỖI LƯỢT / MỖI CUỘC TRÒ CHUYỆN MỚI: ĐỌC FILE NÀY TRƯỚC, rồi mới đọc phần còn lại.
> Không làm lại việc đã xong. Mỗi kết luận trong file này gắn nhãn [ĐÃ ĐO THẬT] /
> [FIXTURE] / [HYPOTHESIS]. Cập nhật file này SAU MỖI mục backlog.

## Mốc xuất phát [ĐÃ ĐO THẬT]

- Head xuất phát: `3d0c80c66ab4fbfbb329955f9cf241e491146f06` (main, 2026-11-16T20:01:31Z).
  Cây làm việc là bản giải nén zip, KHÔNG có `.git`; bản pristine đối chiếu nằm ngoài repo.
- Mục 2 (15 phút đầu) XONG 2026-11-17 ~08:55, chỉ kiểm tra, không sửa code/push:
  smoke 59/59; full 1719 = 1586 đạt / 102 lỗi / 27 fail / 4 bỏ qua (95,4s);
  harness 34/34 exit 1 (đường dẫn hardcode `pb3_web/site` + thiếu Playwright).
- Rà soát độc lập XONG 2026-11-17 ~09:11 (chỉ đọc, bản copy sạch): số Mục 2 đúng 40/43,
  0 sai; 25 mâu thuẫn M1–M25 (23 đúng, 1 bác bỏ M22, 1 chưa rõ M20); 15 mâu thuẫn mới
  N1–N15; 3 vi phạm thật (ngưỡng gzip, 2 lỗ innerHTML, precache 13 wheel chết).
- Q1/Q2 mặc định (user không trả lời trực tiếp, đã ghi rõ để sửa nếu sai): Q1(a) sinh
  fixture bằng script trong cây; Q2(a) sửa đường dẫn harness về cây repo gốc, chỉ sửa
  đường dẫn. Q1: full 1682 đạt, 34 đỏ do 2 fixture thiếu. Q2: 37 harness sửa đường dẫn,
  `node --check` 38/38, 2 harness mẫu PASS (item2 17/17, extract_select 8/8).

## Mục đang làm

- **M1** (đường ghi MonoBehaviour + StringTable) — XONG 2026-11-17, xem "Đã xong"
  (nhánh `feat/mb-write-g1`, gộp theo lệnh tự động của user sau khi verify đạt).
- **BUG#2** (đường merge trích xuất) — XONG 2026-11-17, xem "Đã xong"
  (PR #4, đã gộp vào main `f1a46eb7`).
- **Prefetch Pyodide báo sai "thiếu byte"** — XONG 2026-11-17, xem "Đã xong"
  (PR #5, đã gộp vào main `55a8d162`).
- **P1-1** (mở file theo nội dung, bỏ cổng đuôi) — tiếp theo. P0-4 đã BỎ QUA (chờ
  Joseph, xem "Chờ Joseph" #5): chưa có ảnh Q1/Q2/Q3 bản v4 + xác nhận bản đang
  cài; không đứng chờ, quay lại P0-4 khi có đủ.

## Sửa khẩn cache (2026-11-17) — KHÔNG phải mục backlog, không tính vào thứ tự backlog [ĐÃ ĐO THẬT]

- Nguyên nhân: P0-3 đổi nội dung 3 file precache (`pb3/sdk/unity.py`,
  `extraction.py`, `rebuild.py`) nhưng KHÔNG tăng `CACHE_VERSION` (vẫn
  `v21-20261117`). Theo luật trong `sw.js`, SW mới chỉ kích hoạt khi đổi
  version; trình duyệt đã thăm trước đó tiếp tục phục vụ code cũ cache-first.
  User xác nhận lần chạy lỗi là tool web trên điện thoại; `extract.zip` lúc
  13:18 đúng hành vi trước P0-3 (key `.p12664`, zh trộn, lẫn tiếng Nhật,
  thiếu term, 37 file rác, không có `en.json`).
- Sửa: `CACHE_VERSION` v21-20261117→**v22-20261117** (`sw.js`, precache vẫn
  120 mục, 0 mục chết); đồng bộ kỳ vọng version ở 20 file harness/check
  (8 `exp/check_*_static.py` + 12 `exp/verify_*.mjs`) + 1 dòng mô tả hiện tại
  trong `HANDOVER.md` — chỉ sửa số version, không đổi hành vi kiểm chứng; báo
  cáo lịch sử `exp/*_results.*` và dòng lịch sử P0-2/P0-3 trong file này ĐỂ
  NGUYÊN. Cây đã dọn về đúng trạng thái sau P0-3 trước khi sửa: 32 file P1-1
  sửa dở khôi phục từ snapshot, 4 file P1-1 mới xóa; ghi chú khảo sát P1-1
  trong `work_p11/` giữ nguyên cho lượt sau. Không sửa bug đường bridge (xem
  dưới — đường bridge đã dùng đúng parser P0-3).
- Tự kiểm tra: smoke 59/59; full 1.846 = 1.816 đạt / 25 lỗi / 3 fail / 2 bỏ
  qua, tập 28 đỏ trùng hệt mốc P0-3 (2 fixture thiếu), 0 đỏ mới; 9 check tĩnh
  `fails: []`; harness item6 14/16 (2 fail gzip từ trước, như đã ghi) với cache
  thật `beartea-v22-20261117` đủ 120/120 mục + `browser_cache_version_switch`
  PASS (đổi version xóa cache cũ); harness c5 13/13 PASS. `exp/*.json|*.md`
  bẩn do chạy harness đã khôi phục byte-identical.
- Kiểm chứng end-to-end đường bridge/web [ĐÃ ĐO THẬT] (CPython qua
  `bridge.extract`, đúng mặc định web: locales `["en","zh"]`,
  `merge_one_file=True`, nguồn tự động; `boot` 20 plugin): worker.js tải đúng
  danh sách `pb3/files.json` qua SW rồi `import bridge`, không có parser riêng
  trong JS. IPA 9 Kings v1.0.6: gộp en 6.694 / zh 2.176 (khớp mốc P0-3);
  langtable en/zh mỗi file 2.150 key tên term, 0 key `.pNNNN`/`.sNNNN`/`.xN`;
  zh = đúng 1 cột zh-TW (biến thể đầu theo thứ tự bảng), khớp 2.150/2.150 với
  `zh-TW.json` mẫu P0-3; ST en 100 + zh 26 key; 0 key `.p12664` ở mọi output;
  `Perk_Benevolent` có mặt, `Achievement_FrugalKing` zh = `小氣鬼` (cũ:
  `守銭奴`). File mẫu Build 68: langtable 2.086 key; zh gộp khớp 2.086/2.086
  với `zh-CN.json` mẫu của parent (zh-CN là biến thể đầu ở build này).
  Giới hạn còn tồn tại, KHÔNG đổi bởi sửa khẩn (cần quyết định riêng, không
  phải bug parser P0-3): `en.json` gộp vẫn chứa key generic `.sNNNN`/`.pNNNN`
  của MonoBehaviour không phải bảng ngôn ngữ (IPA 4.445 `.s` / 904 `.p`),
  và output vẫn kèm file locale-None của plugin khác (plist `Info.*`, `.abi`
  21.393 key, `RuntimeInitializeOnLoads`) như trước P0-3; bridge gộp biến thể
  về mã gốc nên không ra file `zh-CN.json`/`zh-TW.json` riêng (chỉ script xuất
  locales mới tách đủ biến thể).
- Sau khi bản v22 lên live, user trên Safari cần: có mạng, tải lại trang 1 lần
  (kích hoạt SW mới: `skipWaiting` + `clients.claim` trong `sw.js`), rồi tải
  lại lần nữa (hoặc đóng tab/mở lại) để tài liệu chạy code mới; nếu vẫn ra kết
  quả cũ thì xóa dữ liệu trang rồi mở lại. Cơ sở code: `sw.js` install/
  activate/fetch + `app.js` chỉ `register("sw.js")`, không có cơ chế báo bản
  mới; hành vi Safari thật [CHƯA KIỂM CHỨNG].
- Chờ file v1.0.5: xem "Chờ Joseph" #6 (giữ nguyên, không trùng lặp). Khi có
  file: chạy lại đúng input v1.0.5 qua bridge với mặc định web; tiêu chí: 0
  key `.pNNNN` trong output bảng ngôn ngữ/zh, zh thuần 1 cột biến thể,
  có `Perk_Benevolent`, không lẫn giá trị cột tiếng Nhật, `en.json` có mặt.

## Đã xong

### Đường ghi còn lại của M1 (generic scan-path, I2 có type tree, PATCH_INPLACE cho MB) — XONG 2026-11-18 [ĐÃ ĐO THẬT] (nhánh `feat/mb-write-rest`)

- Vấn đề: dry-run của user trên Build 68 (translated.vi.json 2188 key): 1985/2188 ép được qua M1, còn 203 key chưa có đường ghi. Đo thật: 203 = 101 key unchanged (bản dịch vi == gốc en, vd Achievement_Alibaba 'Alibaba'→'Alibaba' — không cần ép, code cũ báo sai thành unmatched) + 102 key StringTable ở đơn vị khác trong IPA (Policies/* v.v., 0/102 có trong en-build68.json; resources.assets Build 68 có 0 StringTable/SharedTableData — chỉ ép được khi dry-run trên IPA đầy đủ, đã chứng minh bằng 9K: 2252/2252). KHÔNG phải key generic/type-tree như brief ban đầu nghĩ.
- Sửa: `pb3/sdk/unity.py` — `write_scanned_monobehaviour_bytes` (ghi theo slot scan, verify 2 lớp), `language_source_tt_targets` (tên field thật, vd mSource.mTerms[i].Languages[j]), `monobehaviour_write_targets` (thêm generic + tt-I2), `write_monobehaviour_new_bytes` (dispatcher chung), `_patch_monobehaviour_inplace` (nối cùng độ dài, dài hơn leo thang REBUILD_SECTION nếu cho phép), `apply_monobehaviour_patch` chấp nhận PATCH_INPLACE. `pb3/sdk/extraction.py` — `_mb_generic_kv(..., key_map_out)`, `ExtractionResult.shared_index`, sidecar thêm shared_index; `generic_write_targets` sống ở đây (xem dưới). `pb3/sdk/plan.py` — bỏ skip generic, simulation dùng dispatcher, op PATCH_INPLACE/REBUILD_SECTION theo độ dài, `validate_plan` chấp nhận PATCH_INPLACE cho MB, truyền generic_fn. `pb3/sdk/rebuild.py` — `_collect_mb_info` (1 lần quét), `_classify_unmatched` (unchanged trước, rồi string_table_other_unit, monobehaviour, not_found; nhận thêm csv_values của M3 sau rebase), `apply_rebuild`/`build_rebuild_preview` thêm mode + mb_index. `pb3/sdk/gate.py` — lý do monobehaviour viết lại + thêm string_table_other_unit. `pb3/bridge.py` — plan_build thêm mb_index_json, rebuild_apply thêm mode + mb_index_json, extract trả shared_index. `app.js` — state.extractSharedIndex, rebuildSharedIndex, rebuildRun truyền mb_index; UI giữ REBUILD_SECTION mặc định (PATCH_INPLACE chưa có toggle — follow-up).
- Sửa thêm sau kiểm chứng: (1) test cũ mã hoá hành vi cũ được cập nhật đúng nghĩa gốc — test_gate dùng fixture mơ hồ (2 object cùng tên, chuỗi en/vi strong), test_rebuild dùng fixture mơ hồ + thêm test_mb_generic_key_now_pressable, test_mb_write chấp nhận PATCH_INPLACE; (2) GỠ IMPORT VÒNG unity<->extraction: `from sdk import extraction` (lazy) trong unity.py làm test_tiering test_source_change_selects_only_dependents fail (đồ thị tĩnh: registry → unity → extraction → ir) — chuyển generic_write_targets sang extraction.py, monobehaviour_write_targets nhận generic_fn (None = bỏ qua generic), plan.py truyền vào; test_tiering 28/28 pass sau sửa.
- Kiểm chứng [ĐÃ ĐO THẬT] (trên cây cuối v41, sau rebase lần 3): 24 test mới pass; bộ 11 file liên quan 359 passed / 77 skipped / 2 subtests / 0 failed (10 file cũ 340 + 19 test SF v24 của PR #23 — gồm test dry-run Build 68 assert đúng 1985/101/102/0 và bản fixed 1984/102/102, test 9K IPA đầy đủ 2252/2252); 9 check tĩnh fails: []; smoke 59/59; harness exp/verify_bug2_merge.py PASS 4/4 (build68, 9kings, pb3, ek2). Full suite pytest nhánh trên cây v39 (3 commit code giống hệt cây v41, rebase chỉ đổi file version): 6 failed / 1908 passed / 125 skipped / 73 subtests (25:30), 0 marker EPERM — 6 fail = 5 font fixture thiếu (BeVietnamPro-Regular.ttf, y hệt main) + batch-timeout flaky (đã biết, fail cả trên main). So sánh main c3855f4: bộ 8 file liên quan 285 passed / 77 skipped / 0 failed (nhánh 312 = main 285 + đúng 27 test mới); full suite main 2 lần đều bị EPERM phá (388 và 376 fail, ~1000 marker) nên không dùng làm baseline — nhưng tập fail của nhánh chỉ gồm fail đã biết trên main nên 0 đỏ mới. Dry-run Build 68: 1985 changed / 101 unchanged / 102 string_table_other_unit / 0 refused; 9K IPA đầy đủ: 2252/2252 changed; trích xuất 9K en.json md5 byte-identical với mẫu. Hạ tầng: đợt lỗi ghi EPERM (write() → errno=1, cả tmpfs lẫn overlay) làm nhiễu các lần chạy full suite; VM restart 1 lần giữa chừng (waiter bị SIGTERM ở 14%, cây làm việc còn nguyên); số sạch ở trên chạy qua waiter chờ cửa sổ ghi tốt + pytest --tb=line.
- Rebase 3 lần: lên main `c3855f4` (PR #19 formats-v9, v38), rồi `fc1b217` (PR #20 T2, v39 — T2 lấy v39 trong lúc nhánh đang chạy), rồi `3ecf64f` (PR #23 serializedfile-v24, v40 — cũng lấy v40, trùng số với nhánh nên phải bump tiếp). Xung đột rebase 1 giải quyết bằng gộp cả 2 phía — bridge.py (docstring: giữ câu CSV M3 + thêm mb-write-rest), plan.py (giữ đoạn CSV M3, dòng MB "op theo plan"), rebuild.py (giữ _collect_csv_values M3 + _collect_mb_values/_st_key_in_index của nhánh, gộp chữ ký _classify_unmatched nhận cả mb_values/mb_index/csv_values/source_locale, call site truyền cả 4). Commit version cũ bị skip 3 lần (v34, v39, v40) — làm lại version v41 ở đây. `CACHE_VERSION` v40→**v41-20261118**; tests.json 176→177 (thêm test_mb_write_rest.py; T2 đã thêm test_more_langs_glossary.py, PR #23 đã thêm test_sf_v24.py); files.json giữ 101; precache đo lại 16.575.072 B (126 mục; Pyodide 13.605.176 B, PB3 2.228.170 B, shell/gốc 529.506 B, font 177.264 B, fzstd 24.407 B, icons/fixtures 10.549 B).
- Còn lại (follow-up, không chặn gộp): toggle PATCH_INPLACE trên tab Mod (hiện chỉ có qua bridge/plan API); merge-mode sidecar chưa mang shared_index (chỉ non-merge mode có; web cùng phiên lấy qua bridge extract response).

### Hỗ trợ SerializedFile v24+ (đọc + ghi round-trip; v27+ fail-closed) — XONG 2026-11-18 [ĐÃ ĐO THẬT] (nhánh `feat/serializedfile-v24`)

- Vấn đề: PR #19 kết luận SF v24+ "bị chặn" (UnityPy không có nhánh ≥24, không có spec, mọi .assets mẫu đều v22). User bác bỏ 00:36 +07 2026-11-18 ("Không có thì nghĩ cách tạo ra đi... Cứ lấy file ipa tôi gửi mà test") — nhiệm vụ là tự tạo đường đi, không dừng ở "không có spec".
- Khảo sát [ĐÃ ĐO THẬT] (script `survey_sf.py`, đọc header từng .assets trong IPA): 10/11 IPA đã đo — 5 game Unity có tổng 448 file SF, TẤT CẢ v22 (9K v1.0.6: 30 file, Unity 6000.3.8f1; Tom v26.5.1: 104 file, Unity 6000.0.79f1; PB3 v1.1.7: 304 file; EK2 v0.0.18: 5 file; PvZ Fusion: 5 file); 5 game không phải Unity (DST 2.1.0, balatro Löve2D, Carrion, Dead Cells Heaps 1.277.726.051 B md5 bb2cfd91…, fortune-mill Godot .pck 131.610.046 B md5 57f7d366…); Afterimage 1.0.7 VH (1,20 GB) chưa tải được — Drive báo "download quota exceeded" (thử lại 2 lần, lần cuối 01:11 +07 2026-11-18, đúng cú pháp; quota hồi sau ~24h). Kết luận: KHÔNG có file SF v24+ thật nào trong tay — Unity 6.3 vẫn ghi v22.
- Đọc mã nguồn mở [ĐÃ ĐO THẬT] (clone read-only về vendor-src/, chỉ đọc để học): UnityPy master (af1503d) là implementation công khai DUY NHẤT có xử lý v23+: cổng cao nhất là `>= 23` (TypeTreeNodeInfoHandler) và `>= 26` (shared subtrees), KHÔNG có nhánh 24/25 → v24/v25 parse giống hệt v23; v26 thêm mục shared subtrees sau ref types. AssetStudio/AssetRipper enum dừng ở v22; UABE không có code mhtt/shared-subtree; không có spec công khai v23–26, không có map Unity version → SF version.
- Lỗ hổng parser cũ [ĐÃ ĐO THẬT]: `_parse_typetree_blob` không đọc trailing ref_hash_count + ref_hashes sau node blob → file v23 thật (UnityPy layout, count luôn có mặt kể cả =0) sẽ rớt check `blob dài X, khai Y` → E_CORRUPT; fixture v23 của repo tự dựng KHÔNG có đuôi này nên test vẫn pass — fixture không phải file Unity thật. Mục shared subtrees v26 chưa xử lý → userInformation đọc sai. Ngưỡng size dùng `!= 0`, UnityPy dùng `>= 8`.
- Sửa: `pb3/sdk/unity.py` — `SUPPORTED_SF_VERSIONS` 15..26 (v27+ fail-closed E_UNSUPPORTED_FORMAT); `_parse_typetree_blob` đọc ref_hashes (chấp nhận cả 2 dạng: UnityPy chuẩn + legacy fixture cũ, phân biệt bằng serialized_size, kiểm tra chặt); `_parse_typetree_info` mới (1 handler: hash 16B + u32 size + info; size < 8 → node None); `_resolve_shared_subtrees` mới (ảnh gương UnityPy + guard chu trình + thiếu hash/chỉ số ngoài khoảng → E_CORRUPT); `parse_serialized` đọc mục shared subtrees v26 sau ref types, trước userInformation; `build_serialized` ghi lại mục này nguyên vẹn (handler bytes); `raw_typetree` = toàn bộ info (mhtt … hết ref hashes) nên round-trip byte-identical; `support_matrix()` + `format_probe.py` + `support_matrix.py` + `handlers.py` + README/HUONG_DAN/SUPPORT_MATRIX.md đổi v15–23 → v15–26. Fixture: `build_fixtures.py` — blob v23+ có ref_hash_count, hỗ trợ node flags/ref idx + shared_subtrees v26 + userInformation; sinh thêm sample_v24/v25/v23_sharedref/v26 (fixture .assets không commit — từ zip Release fixtures-v1).
- Kiểm chứng [ĐÃ ĐO THẬT]: 19 test mới (`pb3/tests/test_sf_v24.py`, fixture build IN-MEMORY) pass — v24/v25 parse + round-trip byte-identical + PATCH_INPLACE + REBUILD_SECTION, v23 ref hashes (cả 2 dạng) + bad count → E_CORRUPT, v26 parse/resolve/read_typetree_value/MB patch qua shared subtree/TextAsset REBUILD_SECTION giữ nguyên mục shared + missing hash/cycle → E_CORRUPT, v27 → E_UNSUPPORTED_FORMAT; 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4 (trong đó case 9K kiểm tra en.json md5 thô `4c22c9ebcdd04cc21c7f3f4a4952e955`, 2252 key — hồi quy v22 ĐẠT); full suite nhánh 2031 test: Đạt 1456 / Lỗi 510 / Fail 56 / Bỏ qua 9 (1021,2s) vs baseline main sạch `c3855f4c` cùng môi trường 2012 test: Đạt 1506 / Lỗi 441 / Fail 56 / Bỏ qua 9 (1080,9s) — tập FAIL giống hệt nhau (56=56, 0 chênh); 40 tên lỗi chỉ-nhánh đều ở `test_extraction`/`test_engine_detect`, traceback đều có EPERM (sóng chặn ghi của môi trường: log nhánh 37.366 lần "Operation not permitted" vs baseline 24.959), chạy lẻ lại trên cây nhánh: 95 passed + 51 subtests passed — 0 đỏ mới. `CACHE_VERSION` v39→**v40-20261118** (sau rebase lên main `fc1b2173` — PR #20 T2 đã lấy v39 lúc 02:06 +07; nhánh rebase 1 lần, xung đột ở comment precache trong sw.js + docstring/count 8 file `exp/check_*_static.py` + tests.json, đã giải quyết bằng cách lấy bản T2 rồi cộng thêm phần của nhánh) + đồng bộ 8 file `exp/check_*_static.py` (c6 chỉ ghim số test 176, không ghim version); precache đo lại 16.543.839 B (126 mục; Pyodide 13.605.176 B, PB3 2.198.951 B, shell/gốc 527.492 B, font 177.264 B, fzstd 24.407 B, icons/fixtures 10.549 B) — so với main v39 chỉ `pb3/sdk/unity.py` đổi kích thước trong precache (+8.632 B). Lưu ý: comment precache v38 ghi 16.479.509 B, đo lại trên main sạch bằng cùng phương pháp ra 16.478.511 B (lệch 998 B ở mục shell/gốc, có từ trước nhánh này). `tests.json` 175→176 (T2 thêm test_more_langs_glossary.py, nhánh thêm test_sf_v24.py). Full suite đo trước rebase (base `c3855f4c`); sau rebase kiểm nhanh lại trên cây cuối: 9 check tĩnh `fails: []`, smoke 59/59, harness PASS 4/4, test đích 29 passed (19 test_sf_v24 + 10 v23/support-matrix).
- Còn lại / thành thật: v24/v25/v26 CHỈ kiểm chứng trên fixture tổng hợp theo layout UnityPy master — chưa có file SF v24+ thật nào để đối chiếu (không game nào trong tay có; Unity 6.3 vẫn ghi v22); nếu Unity đổi layout ở v24+ thật (không khớp UnityPy), parser sẽ fail-closed E_CORRUPT chứ không đọc sai lặng lẽ. Afterimage chưa khảo sát được (Drive quota). Chạy trích xuất 9K độc lập qua bridge bị nhiễu do /tmp/pb3out là thư mục chung — agent khác chạy harness cùng lúc ghi đè (en.json 1344 key = EK2 lẫn vào); lấy kết quả harness làm bằng chứng hồi quy, không chạy extract/harness song song với agent khác.
- Sau khi bản v39 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Mở rộng định dạng: ghi UnityFS v9, IL2CPP metadata v24/27/29, hỗ trợ font .ttc — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/formats-v9-meta-font`)

- Vấn đề: kiểm tra toàn diện (audit 2026-11-17 §A5) ghi 4 giới hạn định dạng: (1) UnityFS v9 chỉ đọc, không ghi; (2) SerializedFile chỉ v15–v23; (3) IL2CPP metadata chỉ v31/v39 — game dùng metadata khác không dò được; (4) Font: chỉ TrueType có glyph outline mới gộp được; CFF/.otf đọc được không gộp; .ttc không hỗ trợ; TextMeshPro SDF atlas không tái tạo được. User lệnh "Fix hết toàn bộ".
- Sửa (ghi được): `pb3/sdk/unity.py` — `build_bundle` + `rebuild_bundle_streaming` (pass 2a/2b) ghi v9: offset u64 sau cờ u16 từng BlockInfo + đệm NUL căn 16 sau từng block trong vùng block data; gỡ từ chối v9 ở `apply_patch`, `apply_monobehaviour_patch`, `patch_pack.py` (xóa `_check_bundle_version` + 2 call site + block dry-run); cập nhật chữ ở `support_matrix()`, `format_probe.py`, `inspect.py`, `gate.py`, `index.html`, `app.js`, `README.md`. `pb3/sdk/il2cpp.py` — mở metadata v24/27/29 (entry literal 8 byte như v31, theo Il2CppDumper + rea/docs/il2cpp-metadata.md: v24–v31 không có count; v30/v32/v40+ vẫn từ chối fail-closed). `pb3/sdk/font.py` + `pb3/bridge.py` + `pb3/sdk/gate.py` — hỗ trợ `.ttc`: `parse_font(..., face_index)`, `list_ttc_faces`, `verify_ttc`, `merge_glyphs/merge_vietnamese(..., target_face/donor_face)` (face đích gộp xong nối thêm ở cuối file, offset tuyệt đối, tính lại checkSumAdjustment riêng face đó; các face khác giữ nguyên bytes tại offset cũ); bridge `font_scan(path, face_index)` trả danh sách face khi chưa chọn face, `font_merge(..., target_face, donor_face)` trả `/tmp/merged.ttc` khi đích là TTC; `app.js` hiển thị tên file đích từ `parsed.dest`.
- Không làm được (đã đo, có lý do): SerializedFile v24+ — UnityPy master không có bất kỳ nhánh `version >= 24` nào (cổng cao nhất v23), web-search không ra spec v24, mọi `.assets` rời trong 9K/PB3/EK2 đều là SF v22 → không có SF v24 nào phát hành công khai để mở rộng theo; giữ fail-closed. Font CFF/.otf merge — cần chuyển outline donor TTF (quadratic) sang CFF Type 2 charstring (cubic) hoặc copy CFF kèm remap subroutine, lớn + rủi ro cao, bộ mẫu không có font CFF thật để kiểm chứng → giữ nguyên: .otf đọc được cmap nhưng merge từ chối `E_UNSUPPORTED_FORMAT`. TextMeshPro SDF tái tạo — cần Unity Editor FontEngine, web không làm được; đo/liệt kê đã có sẵn (`tmp_font.py`): trên 9K `resources.assets` 4.467 candidates, 0 asset xác nhận, probe chặt (FaceInfo + glyph table count khớp) 0 candidate → không dùng decode cấu trúc làm fallback.
- Kiểm chứng [ĐÃ ĐO THẬT]: UnityFS v9 — `test_build_bundle_v9_roundtrip` (none+lz4, entry byte-identical), `test_build_bundle_v9_multi_block_offsets` (LZ4 300 KiB → 3 block, offset 0/131072/262144), `test_patch_v9_bundle` (entry sửa đổi nội dung, entry còn lại giữ nguyên bytes), `test_v9_round_trip` streaming, `test_v9_standalone_rebuilds`, `test_v9_passes` (gate PASS), `test_v9_bundle_builds_pack`. IL2CPP — `test_v24_v27_v29_exact_split` (fixture tổng hợp từng version; v24 header 264 byte theo rea), `test_unsupported_version` mở rộng thêm v30/v32. Font TTC — đo trên NotoSansCJK-Regular.ttc thật (19.484.784 B, 10 face): table offset TUYỆT ĐỐI từ đầu file, 11 bảng chia sẻ, checksum từng bảng khớp 0 lỗi (head tính với adjustment = 0), tổng u32 toàn file (C8E61C62) và tổng từng face (B8DDF9DE) đều KHÔNG bằng magic 0xB1B0AFBA — font TTC thật không giữ tổng này; TestTTC 14 test + gate `test_font_entry_from_ttc_face` đều pass (list/parse/merge face 0 giữ nguyên face 1/merge donor TTC/merge face CFF thật bị từ chối/verify bắt checksum hỏng). 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite nhánh (trên main `47b4f714`, sau rebase 2) 1855 đạt / 1 fail / 125 bỏ qua / 73 subtests passed (17:50) vs baseline main sạch `6f8dacd1` cùng môi trường 1835 đạt / 3 fail / 124 bỏ qua / 68 subtests (24:51) — tập FAIL nhánh là tập con của baseline (chỉ còn `test_print_plan_is_fast`, ngưỡng tốc độ cứng 1,2s, fail cả trên main sạch: 1,28–1,53s); 0 đỏ mới. Trước rebase (trên main `6f8dacd1`): nhánh 1845/2 vs baseline 1835/3 — nhánh-only là heartbeat (PASS riêng → flaky), baseline-only là `test_temp_dir_is_cleaned` (PASS riêng) + `test_print_plan_is_fast`. Sau rebase 4 (lên main `eb5c820c`, chỉ đổi version/precache + code PR #18 đã gộp): kiểm nhanh lại trên nhánh cuối — 9 check tĩnh `fails: []`, smoke 59/59, harness PASS 4/4, test đích 290 passed (gồm test_csv_write mới của main + test_colab_translate). `CACHE_VERSION` v37→**v38-20261117** (main đã lên v37 nhờ PR #18 csv-write-m3 gộp trong lúc làm nhánh, sau PR #16/#17 lấy v36, PR #15 lấy v34 và PR #14 lấy v33; nhánh rebase 4 lần lên `eb5c820c`, xung đột ở comment precache trong sw.js + docstring file exp + note (E) trong index.html + vị trí chèn mục AUTOPILOT, đã giải quyết bằng đo lại + gộp cả 2 phía) + đồng bộ 9 file `exp/check_*_static.py`; precache đo lại 16.479.509 B (125 mục; Pyodide 13.605.176 B, PB3 2.151.198 B, shell/gốc 510.915 B, font 177.264 B, fzstd 24.407 B, icons/fixtures 10.549 B). Lưu ý: comment precache trên main v37 ghi 16.459.516 B (icons/fixtures 10.380 B) — thiếu 169 B của fixtures/selftest.zip (có trong PRECACHE_URLS); số đo lại ở đây tính đủ theo phương pháp đã nêu.
- Còn lại: UI chọn face trên web chưa làm (app.js chưa có dropdown chọn face cho file .ttc — SDK + gate + bridge đã đủ đường); test batch timeout `test_timeout_slow_job_is_e_timeout_and_batch_finishes` fail cả trên main lẫn nhánh, chạy riêng cũng fail `(0, 2)` thay vì `(1, 1)` — lỗi có sẵn trong môi trường này, ngoài phạm vi nhánh (PR #14 đã ghi nhận cùng hiện tượng); fixture font `pb3/fonts/BeVietnamPro-Regular.ttf` thiếu trên clone mới (chỉ có `.b64` trong repo) — đã khôi phục bằng bước decode của CI trong clone nhánh + worktree baseline, file untracked, KHÔNG commit.
- Sau khi bản v38 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.
### Đường ghi TextAsset CSV cho PB3 (file CSV độc lập + TextAsset CSV trong bundle) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/csv-write-m3`)

- Vấn đề: plugin csv_table đọc/ghi được CSV nhưng build_plan chỉ xử lý TextAsset JSON dict (non-JSON → skipped) nên text dạng CSV của PB3 trích được mà không ghi lại được. CSV của PB3 là file độc lập trong IPA (`Payload/ipa.store.app/Data/Raw/Localization/PB3_LOC_DEFAULT.csv`, 966.957 B, 3511 dòng), không phải TextAsset trong bundle.
- Sửa: `pb3/sdk/csvutil.py` (mới, wrapper quanh plugin csv_table: parse/source_map/simulate/verify theo ô `csv:r:c`, giữ header/thứ tự/delimiter/quote; nhận diện theo tên .csv/.tsv hoặc header có cột key; ≥2 cột) + `sdk/batch.py` (định tuyến đơn vị kind csv cho file độc lập + entry trong ZIP; output giữ tên .csv) + `sdk/plan.py` (build_plan lập entry CSV: file độc lập object 0 + TextAsset CSV trong bundle/assets, change mang cell; apply simulate + verify sha + từng ô; undo giữ cell + verify before_sha) + `sdk/rebuild.py` (liệt kê đơn vị csv; classify_unmatched biết giá trị CSV) + `sdk/gate.py` (parse_unit trả (None, None) cho csv; find_pressed_unit tìm CSV trong IPA/ZIP/file lẻ; verify_pressed_units thêm nhánh verify CSV + guard khối SF khi SF None) + UI (note (E) thêm TextAsset CSV).
- Kiểm chứng [ĐÃ ĐO THẬT]: 14 test mới (`pb3/tests/test_csv_write.py`) pass (fixture tự dựng: csvutil, build_plan, apply_rebuild, undo reverse-patch byte-identical, gate PASS cả 2 dạng pressed); 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; E2E trên IPA PB3 thật: build_plan 1 entry/3 change, áp xong 966.957 → 966.980 B, đọc lại đúng 3/3 giá trị mới, 2345/2345 ô khác giữ nguyên, gate verify PASS cả pressed=file CSV và pressed=ZIP. Full suite 0 đỏ mới do nhánh gây ra — nhánh Tổng 1985 / Đạt 1544 / Lỗi 367 / Fail 64 / Bỏ qua 10 / Timeout 4 (1575,4s) vs main `47b4f71` Tổng 1971 / Đạt 1507 / Lỗi 438 / Fail 20 / Bỏ qua 6 (983,6s); tập đỏ chỉ-nhánh 175 tên chạy lẻ lại trên cây cuối: 163 pass / 11 skip / 1 fail duy nhất là test timeout batch đã biết flaky (fail lẻ cả trên main). Môi trường nhiễu nặng lúc chạy: fsync bị chặn (EPERM) theo đợt + nhiều suite chạy song song (load tới 13,66) gây 413× PermissionError trong log nhánh; 5 font test + fixture thiếu là đỏ cũ (zip fixtures đã upload lên Release fixtures-v1 sau đó). `CACHE_VERSION` v36→**v37-20261117** + đồng bộ 9 file `exp/check_*_static.py`; precache đo lại 16.459.516 B (125 mục; PB3 2.136.414 B, shell/gốc 505.875 B); `files.json` 100, `tests.json` 174. Nhánh rebase 1 lần lên main `47b4f71` (PR #15 lấy v34), rồi gộp tay lên main `7fd5c5d7` (PR #16 T1 lấy v35) rồi lên main `fa492ff9` (PR #17 Colab translate lấy v36) qua `git show` + ghi file vì git rebase/apply kẹt do fsync EPERM; giữa chừng `git apply` cắt AUTOPILOT.md về 0 byte và 4 file (app.js, index.html, cloudflare/README.md, cloudflare/worker.js) biến mất khỏi cây làm việc (chưa xác định nguyên nhân — có thể do test suite hoặc cơn EPERM) — đã khôi phục đủ từ git objects và kiểm tra status sạch trước khi đẩy.
- Còn lại: `patch_pack.py` chưa xuất được entry CSV (hàm `_read_script`/`_verify_entry` chỉ làm việc với SF) — fail rõ ràng, chưa lặng lẽ bỏ qua; chạy (E)/gate toàn IPA PB3 482 MB OOM trong VM 6 GB (nặng toàn file có từ trước, không phải do M3) nên đường CSV chỉ kiểm chứng phẫu thuật trên IPA thật + fixture cho pipeline đầy đủ; `plan.apply_plan` vẫn bỏ qua tham số `out_dir` (dùng `PLAN_OUT_DIR` module-level) — test dùng context manager `_patched_outdir`.

### Tăng tốc dịch (batch rớt → chia nhỏ thay vì dịch đơn; cooldown 429; sửa placeholder theo batch) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/translate-speed`)

- Vấn đề: user báo dịch 507 chuỗi mất 5m24s mới được 282 chuỗi (~0,9 chuỗi/giây) qua Google miễn phí (gtx, vì worker chưa triển khai + chưa có GOOGLE_API_KEY), trong khi UI ghi batch 100 chuỗi/request thì 1000 chuỗi chỉ mất chục giây.
- Chẩn đoán [ĐÃ ĐO THẬT] (fixture 507 chuỗi thật từ en-build68.json, mock Google, không spam Google thật): nút thắt không phải token bucket (2 req/giây) mà là **batch rớt → dịch đơn**: 1 batch 100 chuỗi rớt marker → 100 chuỗi dịch đơn tuần tự → job 507 chuỗi từ 2,3s thành 52,3s (9,70 chuỗi/giây); Google 429 liên tục → mỗi chuỗi đốt 4 lần gọi Google + backoff 1+2+4s trước khi rớt sang MyMemory → 8,9s/chuỗi (0,11 chuỗi/giây), circuit breaker không ngắt vì MyMemory vẫn thành công; bước sửa placeholder PR #9 dịch đơn từng dòng lệch (28 dòng ≈ 14s).
- Sửa (không cần tài khoản user; giữ nguyên masking + verify+repair PR #9, cổng kiểm tra, chất lượng bản dịch): (1) `pb3/bridge.py` — batch lỗi marker (`bad_response`) được chia đôi đệ quy rồi thử lại (chỉ khi >10 chuỗi), latch chỉ đếm khi chia cũng rớt hết; batch trả sai số chuỗi cũng được chia; (2) `gt.js` — 429/5xx 2 lần liên tiếp → Google nghỉ 30s (`GT_COOL_MS`, kiểm tra trước throttle và trước mỗi lần retry), lần gọi sau fail-fast sang MyMemory; `splitRetryable` gắn vào lỗi batch (bad_response → true, 429 → false); (3) `pb3/bridge.py` — sửa placeholder theo batch ≤25 thay vì dịch đơn từng dòng (vẫn 1 lần thử đơn sau đó nếu còn lệch); (4) `gt.js` — MyMemory timeout 10s→5s, breaker 3 lỗi liên tiếp → nghỉ 60s. Không làm: song song batch (token bucket là trần thật — song song chỉ đổi thứ tự hàng đợi và tăng nguy cơ 429); worker pool cho dịch (dịch chạy 1 worker theo thiết kế).
- Đo trước/sau [ĐÃ ĐO THẬT] (fixture 507 chuỗi, mock): đường tốt giữ 2,3s/221 chuỗi/giây; 1 batch rớt marker 52,3s/9,70 → **3,3s/154 (15,8×)**; marker rớt 30% batch 205,3s/2,47 → **7,3s/70 (28×)**; Google 429 mọi chuỗi (25 chuỗi) 223,5s/0,11 → **11,7s/2,13 (19×, gtx 104→2)**; placeholder lệch 30% 16,3s/31,13 → **7,8s/65 (2,1×, warnings 8→3)**; 429-batch 269,8s → 241,3s (gtx 517→363). Output trên fixture giống hệt trước sửa (ok == drop-marker == drop-marker-30, byte-identical); ngoại lệ có chủ đích: khi Google 429 kéo dài, một số chuỗi đi MyMemory thay vì Google (hành vi fallback có sẵn); khi placeholder lệch 30%, 5 key được sửa đúng hướng (trước hỏng 8 key, sau hỏng 3 key).
- Kiểm chứng [ĐÃ ĐO THẬT]: 9 test mới (`pb3/tests/test_translate_speed.py`) pass; 1 assertion cũ cập nhật theo hợp đồng mới (`test_translate_source.py`: batch_calls 1→2 do sửa theo batch); 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới — full-tier (pytest runner) nhánh Tổng 1971 / Đạt 1836 / Fail 28 / Bỏ qua 4 / Timeout 2 vs main `6f8dacd1` Tổng 1962 / Đạt 1745 / Fail 28 / Bỏ qua 5 / Timeout 2 (tập fail chỉ chênh 2 mục do môi trường: nhánh-only là test timeout batch flaky đã biết, pass lẻ 0,62s; main-only là `test_plan_modules_matches_plan_counts` do 1 file tạm 0 byte `test_tmp_facts.py` do chính lần chạy test tạo ra trong worktree); discover nhánh trước rebase Ran 1949 / 25 fail / 99 lỗi vs main 1958 / 24 fail / 99 lỗi (tập lỗi nhánh ⊆ tập lỗi main + đúng test timeout flaky). `CACHE_VERSION` v33→**v34-20261117** + đồng bộ 9 file `exp/check_*_static.py`; precache đo lại 16.429.381 B (124 mục; PB3 2.110.504 B, shell/gốc 501.481 B); `tests.json` 171→172. Nhánh rebase 2 lần (PR #13 M2 lấy v32, PR #14 Colab lấy v33).
- Còn lại: T1 (worker + key) vẫn là cách tăng tốc lớn nhất còn lại — nhưng user đã chọn hướng Workers AI miễn phí không cần thẻ (21:22 +07, model @cf/meta/m2m100-1.2b) và đang triển khai riêng; Google path (cần billing account, đang kẹt ở bước tạo billing) là lựa chọn sau.

### Thêm ngôn ngữ dịch + từ điển theo game (T2) — XONG 2026-11-18 [ĐÃ ĐO THẬT] (nhánh `feat/more-langs-glossary`)

- Vấn đề: UI chỉ có nguồn en/zh, đích vi/en; text nguồn khác bị `_source_match` lọc; từ điển offline chỉ 105 EN_VI / 101 VI_EN; TM có sẵn không theo game, không sửa/xuất/nhập được; cổng font chỉ kiểm chữ Việt nên đích khác vi luôn PASS mà không kiểm gì.
- Sửa: 30 nguồn + 19 đích (chỉ đích nào font Be Vietnam Pro phủ đủ, đo bằng `sdk/font.py coverage()`); cổng font mở rộng theo `target_lang` với fail-closed `font_unknown_target`/`font_unverifiable_target`, không làm yếu cổng; `pb3/sdk/glossary.py` mới (từ điển theo game, JSON version 1, cap 5.000, lookup chuẩn hoá strip+casefold) + UI tab Dịch (thêm/sửa/xoá, học từ bản dịch vừa xong, xuất/nhập JSON, localStorage theo game+cặp); ưu tiên từ điển game > TM > Google; 17 từ điển offline mới (44 cụm/ngôn ngữ, registry 36 cặp, 1.676 cụm).
- Kiểm chứng [ĐÃ ĐO THẬT]: 28 test mới (`pb3/tests/test_more_langs_glossary.py`) + bộ liên quan 464 test pass (skipped=2: test_translate/translate_source/translate_speed/gate/langdetect/font/rebuild/unity/deep_extract/patch_pack/csv_write/colab_translate); 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4 (lần 2; lần 1 ek2 FAIL flaky dưới tải, chạy lại PASS); E2E 9K en.json 2252 keys → tiếng Đức qua FakeProvider (Google không tới được từ VM, `E_NETWORK`): dịch đủ 2252, từ điển game dùng đúng 6 cụm, 0 lỗi placeholder; gate de PASS với allowlist 4 key generic MonoBehaviour/type-tree chưa ép được (thuộc nhánh mb-write-rest đang chạy song song), gate ro FAIL `font_unknown_target` fail-closed. Full suite nhánh 2004 test (44 fail + 266 lỗi, skipped=8, 1032,8s) vs baseline main `c3855f4c` 1919 test (47 fail + 355 lỗi, skipped=8, 941,8s) — tập đỏ nhánh là tập con của baseline trừ 2 đỏ nhánh-only, cả hai chạy lại riêng đều PASS (1 flaky luồng/timing dưới tải, 1 kẹt tạo thư mục tạm do sóng EPERM lúc chạy) → 0 đỏ mới; 0 test T2 đỏ; nhánh có thêm 29 test case (28 test T2 mới + 1 test_translate); `CACHE_VERSION` v38→**v39-20261117** (main đã lên v38 nhờ PR #19 A5 gộp trong lúc làm nhánh, sau PR #18 lấy v37, PR #17 lấy v36; nhánh rebase 4 lần, lần cuối lên `c3855f4c`, xung đột ở `gate.py` — gộp cả `face_index` của A5 + `required` của T2 vào `font_entry_from_bytes` — và docstring 7 file `exp/check_*_static.py` + comment precache trong sw.js, đã giải quyết bằng merge-file 3 chiều + đo lại) + đồng bộ 9 file `exp/check_*_static.py` (files.json 101, tests.json 175, precache 126 mục); precache đo lại **16.535.207 B** (15,77 MiB: Pyodide 13.605.176 B, PB3 2.190.319 B, shell/gốc 527.492 B, font 177.264 B, fzstd 24.407 B, icons/fixtures 10.549 B).
- Lưu ý: khôi phục mục T1 (Cloudflare Worker) mà PR #18 vô tình xoá khỏi AUTOPILOT.md — nội dung lấy nguyên từ `fa492ff9`, vị trí cũ (sau detect-followup, trước M2). Trong lúc rebase, script giải quyết xung đột (regex DOTALL `.*` tham lam) từng cắt cụt 7 file `exp/check_*_static.py` còn 4–11 dòng, đã commit nhầm rồi khôi phục bằng `git merge-file` + sửa assertion count (files 101, precache 126); bài học: không dùng `.*` với re.S khi match tới cuối dòng. Kiểm chứng local cần giải nén `pb3-fixtures-v1.zip` (Release fixtures-v1, sha256 `ddb70ac6…`) vào `pb3/tests/fixtures/` + decode `fonts/BeVietnamPro-Regular.ttf.b64` — nếu không, 72 test phụ thuộc fixture sẽ đỏ giả (FileNotFoundError); fixture + .ttf giải nén KHÔNG commit (giữ untracked).
- Sau khi bản v39 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Trích xuất file IPA lớn bằng Google Colab + nút mở Colab trên web — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/colab-extract`)

- Vấn đề: user dùng iPhone, Safari hay sập khi trích file IPA lớn (>140 MB cảnh báo, >190 MB khuyên mạnh dùng cách khác) vì tab chỉ dùng được ~1–1,5 GB RAM. User chọn Google Colab (miễn phí ~11 GB RAM dùng được, phiên tối đa 12 tiếng) thay vì GitHub Actions (artifact miễn phí chỉ 500 MB, không chứa nổi IPA 2 GB) và muốn tích hợp thẳng vào web tool.
- Sửa: `colab/extract-large-ipa.ipynb` (mới, 8 cell): gắn Drive → clone repo main → **Bước 1 tự động tìm file IPA** theo tên trong Drive đã mount (`find` ở `MyDrive` + `Shareddrives`), không thấy thì tự tải theo file ID qua Drive API (`auth.authenticate_user` + `googleapiclient`, tải theo chunk 32 MB, kiểm tra đủ dung lượng), chỉ khi cả 2 cách đều thất bại mới bảo user điền tay `MANUAL_IPA_PATH` → trích xuất bằng đúng `bridge.extract` của repo (locales en+zh, gộp 1 file/locale; nới `MAX_WEB_INPUT` 2 GB → 64 GB chỉ trong phiên Colab) → lưu các file `.json` về `Drive/MeoRocky-output/<tên game>/` + in số key/md5/thời gian/RAM đỉnh. Cell "⚠️ Lưu ý" ghi đúng các con số: phiên tối đa 12 tiếng, ~90 phút không động tự ngắt, giờ cao điểm có thể ngắt ngẫu nhiên; kết quả PHẢI lưu về Drive ngay (file trên máy ảo mất khi phiên tắt); đừng tắt tab khi đang chạy; RAM ~11 GB, xử lý streaming, không đọc cả file IPA vào RAM; file IPA phải nằm sẵn trên Drive; lần đầu chạy phải cho phép truy cập Drive; bị ngắt thì chạy lại từ đầu. Game mặc định: Don't Starve Together 2.1.0 VH (file ID `1DfcaEn0hEfs88qv2pkO4ed6n4546TsP1`). UI: `index.html` thêm nút "☁️ Xử lý file lớn bằng Colab" (id `btn-large-file-colab`, mở tab mới tới `colab.research.google.com/github/MunKai0408/MeoRocky/blob/main/colab/extract-large-ipa.ipynb`) + ghi chú ngắn trong hộp thoại cảnh báo file lớn (`#large-file-modal`); chỉ thêm UI, không đổi logic trích xuất/dịch/mod.
- Kiểm chứng [ĐÃ ĐO THẬT]: code cell notebook chạy local đúng từng cell: 9K v1.0.6 en.json 2252 key, md5 `4c22c9ebcdd04cc21c7f3f4a4952e955` khớp mẫu, thứ tự key khớp, zh-CN/zh-TW 2252 key, 7,7 s, RAM đỉnh 96,5 MB; PB3 482 MB: en/zh-CN 2348 key, 19,7 s, 191,2 MB; Tom 210 MB: 0 file locale trên main mới (32 key quảng cáo `.strings` cũ đã bị PR #12 giữ ngoài file locale chính — đúng hành vi mới, không phải lỗi notebook). Cell Bước 1 test bằng cây Drive giả + service Drive giả: tìm thấy file ở thư mục sâu, không thấy thì tự chuyển sang tải theo file ID, tải đủ byte + raise IOError khi thiếu byte — tất cả PASS. **Don't Starve Together 2.1.0 VH (1.686.259.493 B, md5 `facd553d2d82996963e64d6a7c5e9c90`): trích bằng SDK Unity cho 0 file, 0 key (275 mục bỏ qua), 67,6 s, RAM đỉnh 1.180,3 MB — đây là kết quả ĐÚNG**: file không phải game Unity (8.654 entry, 0 hit UnityFramework/global-metadata/.unity3d/.assets/libil2cpp; dữ liệu engine riêng của Klei như `data/movies/*.ogv`, `data/sound/*.fsb`); text thật nằm trong `data/databundles/scripts.zip` (4.067 entry: 4.024 file Lua + 11 file `.po` + `strings.pot`), và `vietnamese.po` (18.383.078 B) đã dịch đầy đủ 100% (87.862 entry, 87.829 `msgstr` có nội dung) — game đã có sẵn bản dịch Việt hoàn chỉnh, chỉ cần bật tiếng Việt trong game, không cần dịch lại (ghi rõ trong mục Lưu ý của notebook; đường đọc `.po` của game Klei KHÔNG thuộc phạm vi nhánh này). 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; HTML parse sạch (267 id duy nhất, không trùng); full suite 0 đỏ mới do nhánh gây ra (nhánh 1958 test = 1831 đạt / 25 fail / 99 lỗi / 3 bỏ qua vs main cùng môi trường 1958 = 1832 / 24 / 99 / 3 — 1 fail chênh là `test_timeout_slow_job_is_e_timeout_and_batch_finishes`, test timeout batch đã biết flaky: chạy lẻ FAIL cả trên cây main (5,5 s) lẫn cây nhánh (6,8 s) dưới tải nặng hiện tại, `(0, 2)` thay vì `(1, 1)` — job nhanh cũng quá hạn; nhánh không đổi file Python nào nên không thể gây ra). `CACHE_VERSION` v32→**v33-20261117** + đồng bộ 8 file `exp/check_*_static.py`; precache đo lại 16.418.383 B (124 mục, có `pb3/sdk/repack.py` của PR #13; PB3 2.102.735 B, shell/gốc 498.252 B). Nhánh rebase lên main `6f8dacd1` (PR #13 M2 đã lấy v32) — lúc rebase đã phát hiện và sửa 1 lỗi tự gây: `git checkout --theirs` trong rebase lấy bản của commit đang replay (không phải của main) nên suýt làm mất `repack.py` khỏi precache; đã khôi phục bản main rồi mới bump v33.
- Sau khi bản v33 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Xem sâu + trích sâu (dò được text chính My Talking Tom bằng bảng literal IL2CPP) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/deep-extract-tom`)

- Vấn đề: Tom v26.5.1 chỉ trích được 32 key quảng cáo (ad SDK), 0 chữ game; `data.unity3d` 78 MB > giới hạn entry 64 MB nên pipeline bỏ qua cả game; game không có nguồn ngôn ngữ có cấu trúc nào (0 I2 language_source, 0 StringTable). User yêu cầu dò bằng được text chính của game, chung cho mọi game Unity (không hardcode theo game). Web còn sập lúc trích xuất IPA Tom 210 MB trên Safari.
- Sửa: `pb3/sdk/inspect.py` (mới, op `inspect_deep`: liệt kê bundle/.assets + nguồn ngôn ngữ + lý do bỏ qua, streaming) + `pb3/sdk/rawscan.py` (mới, op `raw_scan`: quét chuỗi thô streaming, lọc nhiễu, 2 bucket strings/single_words, nhãn `unverified-raw-scan`, tách riêng khỏi file locale) + `pb3/sdk/il2cpp.py` (mới: đọc literal CHÍNH XÁC theo bảng literal `global-metadata.dat` — v31 entry 8 byte `(length, dataIndex)`, v39 entry 4 byte delta-index; nhận diện bằng magic `0xFAB11BAF`; version khác từ chối rõ ràng; kiểm chứng mẫu rải đều fail-closed) + `pb3/bridge.py` (`list_entries` lười — chỉ đọc head 2 KB + inventory, không nạp cả file; op `inspect_deep`/`raw_scan`; `scan_target` tự nhận diện UnityFS/IL2CPP/raw bằng magic) + UI (`index.html`: nút "Trích xuất" thêm ở ĐẦU danh sách file, cùng handler nút cuối; tab Inspect thêm "Xem sâu toàn bộ file" + "Trích sâu entry" với nhãn unverified) + giảm sập (`app.js`/`stream.js`: gửi file sang worker bằng structured clone thay cho `slice(0)` + transfer list — bỏ đỉnh phụ ~1× file trên main thread; hiệu chỉnh ngưỡng cảnh báo theo hệ số đo thật tab ~4× file + ~250 MB nền: cảnh báo từ >140 MB, khuyên mạnh iSH từ >190 MB, sửa text "~2×" sai thành "~4× + ~250 MB").
- Kiểm chứng [ĐÃ ĐO THẬT]: Tom — `inspect_deep` 1.693 entry (1.692 file + 1 unity), `data.unity3d` UnityFS v8 / Unity 6000.0.79f1 / 1.307 block LZ4HC / 141 entry, pipeline bỏ qua vì > 64 MB, 0 nguồn ngôn ngữ; IL2CPP v31: 12.090 literal, 11.820 đọc được, 2.904 chuỗi nhiều từ (quét thô chỉ 476 — gấp 6,1×), `'Daily challenge!'`/`'Daily Vault Challenge'`/`'Change lang'`/`'Cut it out!'`/`'Confirm resetting all local achievements.'` tách chính xác từng literal; IL2CPP chạy được trên cả 4 game kia (9K v39: 28.286 literal; PB3 v31: 39.694; EK2 v31: 20.678). 17 test mới trong `pb3/tests/test_deep_extract.py` đều pass (fixture + IPA thật). 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới (branch 1.791 đạt / 5 fail / 124 bỏ qua vs main 1.774 đạt / 5 fail / 124 bỏ qua — tập FAIL giống hệt, +17 đúng 17 test mới; 5 fail cũ là test font thiếu fixture). `CACHE_VERSION` v28→**v29-20261117** + đồng bộ kỳ vọng version; `files.json` 95→98, `tests.json` 168→169, precache 120→123 mục, dung lượng precache đo lại: 16.366.908 B.
- Còn lại: bảng dịch đầy đủ lúc chạy (nếu tải từ server Outfit7) nằm ngoài file IPA — [HYPOTHESIS], chưa kiểm chứng; `.strings` bplist (595 key / 14 locale vỏ app) chưa đọc được cặp key→value; binary app Mach-O 89 MB chưa quét; follow-up giảm sập tiếp theo (chưa làm): chỉ đọc đuôi file qua `File.slice` cho EOCD thay vì giữ cả ArrayBuffer, OPFS thay MEMFS phía worker.

### Dọn gọn giao diện tab Mod (luồng chính 4 bước + "Nâng cao") — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `fix/mod-ui-tidy`)

- Vấn đề: user 2026-11-17 19:30 +07: tab Mod nhiều quá, dễ loạn. Tab cũ xếp 7 khối nối nhau (patch 1 chuỗi + op-grid 6 thẻ + phần B tìm/sửa JSON + plan JSON + (E) + cổng bước 6), nút "Ép và ghi file" nằm TRƯỚC cổng kiểm tra trong khi luật mở nút lại phụ thuộc cổng.
- Sửa (CHỈ UI, không đổi logic): tab Mod xếp lại thành luồng chính 4 bước theo đúng thứ tự dùng thật — Bước 1 "Ép lại vào file gốc (E)" (chọn nguồn dịch + chọn lại file gốc) → Bước 2 "Xem trước (dry-run)" (nút Xem trước + chọn tất cả bản giữ/loại + kết quả) → Bước 3 "Cổng kiểm tra trước khi giao (bước 6)" (nguồn gốc + file đã ép + font + nút Kiểm tra + kết quả; danh sách cho phép + bản dịch từng phần gộp vào `<details id="gate-advanced">` "Tùy chọn cổng", mặc định đóng) → Bước 4 "Ép và ghi file" (nút Ghi + trích lại để kiểm). Các công cụ còn lại gộp vào `<details id="mod-advanced">` "Nâng cao" (mặc định đóng): Thay 1 chuỗi trong TextAsset (note + op-grid 6 thẻ + form + kết quả + note patch pack C7) + phần B + plan JSON. Mỗi bước đúng 1 nút chính nổi bật, còn lại là `.secondary`. Toàn bộ id (75 id trong tab) + handler + hành vi giữ nguyên (chỉ app.js thêm 4 dòng: handler thẻ "Thay Text" mở `#mod-advanced` trước khi cuộn tới ô nhập, vì form nằm trong details đóng); note mục (E) giữ nguyên từng chữ (PR #8); chỉ sửa 1 cụm chỉ đường đã sai do đổi thứ tự — "phần B bên trên" → "phần B trong mục Nâng cao" (note cổng + 2 option trong select nguồn gốc/bản dịch). Không đổi logic gate/extraction/translate; `refreshRebuildApplyEnabled` không đụng (nút Ghi vẫn chỉ bật khi dry-run + cổng PASS cho đúng file + bản dịch).
- Kiểm chứng [ĐÃ ĐO THẬT]: 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; đối chiếu id cũ/mới: đủ 75 id, không mất id nào, không trùng id toàn file (265 id duy nhất); HTML parse sạch, không thẻ đóng thừa/thiếu; đọc lại bằng mắt: đủ 4 bước đúng thứ tự, 2 `<details>` mặc định đóng, nút Ghi vẫn `disabled` trong HTML. Full suite 0 đỏ mới (branch 1916 test = 1790 đạt / 24 fail / 99 lỗi / 3 bỏ qua vs main cùng môi trường 1916 = 1790 / 24 / 99 / 3 — tập FAIL/ERROR giống hệt, diff rỗng; đỏ cũ do 2 fixture thiếu). `CACHE_VERSION` v29→**v30-20261117** + đồng bộ 8 file `exp/check_*_static.py`; precache đo lại 16.440.016 B (123 mục; PB3 2.058.158 B, shell/gốc 492.402 B).
- Lưu ý: 3 harness browser (.mjs, cần Playwright — không chạy được ở đây) bấm bằng `page.click` vào nút nay nằm trong `<details>` đóng (mod_search: btn-mod-search/to-plan/download-json/load-extract/load-translate; item8/c7: btn-plan-build/patch-pack, btn-mod-dryrun/patch-pack) — khi chạy trên Chromium thật cần mở `#mod-advanced` trước (follow-up cập nhật harness, không thuộc lượt này). Harness rebuild/delivery_gate bấm nút luồng chính (vẫn hiển thị) nên không ảnh hưởng. Các file `exp/verify_*.mjs` còn pin `v28-20261117` là stale có từ trước PR #10 (đã đỏ trên main v29) — không đụng tới.
- Sau khi bản v30 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Hoàn thiện dò text: parser `.strings` bplist + quét Mach-O theo section — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/detect-text-followup`)

- Vấn đề: sau PR #10 còn 2 mục: (a) file `.strings` bplist (595 key / 14 locale vỏ app iOS) chưa đọc được cặp key→value; (b) binary app Mach-O 89 MB (`MyTalkingTom-row`) chưa quét.
- Sửa (a): `pb3/plugins/apple_strings/plugin.py` đọc bplist bằng `plistlib` (stdlib, không thêm dependency): sniff magic `bplist00` 0.9, parse key→value, ghi ngược bằng plistlib round-trip (`sort_keys=False` giữ thứ tự key gốc), MANIFEST 1.0.0→1.2.0; hàm public `parse_strings_bytes` dùng chung cho plugin + `sdk/inspect.py` (mục `apple_strings` trong `inspect_deep`: mọi file `.strings` — path, locale từ `.lproj`, format text/bplist, số key, 3 cặp mẫu — tách riêng khỏi file locale chính). `pb3/sdk/extraction.py`: `_origin_kind_for(..., flavor)` — flavor `"strings-bplist"` → `"other"`, KHÔNG gộp vào en.json/zh.json (bplist là chuỗi vỏ app iOS: xin quyền, Game Center, IAP — gộp sẽ làm 9K en.json 2252→2253 key, vỡ md5 byte-identical vs mẫu); `.strings` text giữ nguyên `"text_asset"` (vẫn gộp như trước, vd CSJ 32 key của Tom). UI: `app.js` `renderDeepInspect` hiển thị mục `.strings` (tối đa 20 file + số file còn lại).
- Sửa (b): `pb3/sdk/rawscan.py` quét Mach-O theo section `__TEXT,__cstring` + `__TEXT,__ustring` (streaming 1 lượt xuôi, không nạp cả file, không tua lại; offset tuyệt đối trong file; mỗi section 1 RawScanner, cap RIÊNG theo section; FAT chọn slice arm64, Mach-O 32-bit từ chối rõ `E_UNSUPPORTED_FORMAT`); `scan_target` nhận diện qua magic (thin 64-bit / FAT) ở cả 3 đường (file thường, member ZIP, VFS); mode mới `"macho"`, nhãn `"macho"` (UI `renderRawScan` hiển thị generic nên không cần sửa thêm). Lý do không quét thô cả file [ĐÃ ĐO THẬT]: quét thô 89 MB ra 381.172 run, kept 10.000 (chạm cap) nhưng mẫu đầu toàn byte code (`'AA9\ta'`...) vì `\t` lọt qua kiểm tra whitespace; `__objc_methname`/`__objc_classname`/`__const` không quét (chỉ tên selector/class/con trỏ — đo trên Tom: methname 0 chuỗi nhiều từ).
- Bug tìm thấy khi đo (đã sửa): ngân sách chung giữa các section làm `__ustring` kept 0 (scanner `__cstring` ăn hết max_results=10000 trước, scanner sau còn 0); FAT slice: stream đứng ở ĐẦU slice chưa qua 4 byte magic nên `_read_macho_thin` đọc lệch 4 byte → `E_CORRUPT` (đã đọc + kiểm tra magic slice trước khi parse header); `rawscan.py` thiếu `import struct` + `PB3Error` ở đầu module.
- Kiểm chứng [ĐÃ ĐO THẬT]: (a) Tom: 14 locale Localizable.strings chính đủ 595 key (en 64, vi 36, zh-Hans 64; cặp mẫu `Camera`→`Máy ảnh` vi, `Allow Notifications`→`Cho phép thông báo` vi); `inspect_deep` mục `apple_strings`: 159 file / 1.404 key (156 bplist + 3 text), 0 bỏ qua. 9K: 62 file bplist, 31 key (1 key/locale); PB3: 22 file, 11 key; EK2: 52 file / 425 key (38 text + 7 bplist + 7 XML plist — InfoPlist.strings dạng XML, mỗi file 1 key `NSUserTrackingUsageDescription`), 0 bỏ qua. Ngoài ra: plugin đọc cả 3 dạng `.strings` (text / bplist / XML plist) qua `plistlib`, sniff XML 0.9, ghi ngược XML round-trip giữ đủ key. (b) Mach-O Tom qua `scan_target` (IPA + entry) và qua file thường: mode `macho`, kind `thin`, kept **10.498** (10.000 `__cstring` + 498 `__ustring`), 54.706 run, lọc nhiễu 23.973, merged 2.069, 2,2 s, đỉnh RSS 37,8 MB (streaming — file 89 MB); mẫu thật: `'Unity startup flow already started'`, `'Running Unity Framework'`, `'Splash Screen animation finished. Start Unity and set the root view controller'`, `'Native video ad: JS impression tracker at index %ld succeeded'` (ustring). 24 test mới trong `pb3/tests/test_detect_followup.py` đều pass (fixture bplist + XML plist + Mach-O 64-bit/FAT tổng hợp + IPA thật). 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới (branch 1.815 đạt / 5 fail / 124 bỏ qua vs main cùng môi trường 1.791 / 5 / 124 — tập FAIL giống hệt, 5 fail cũ là test font thiếu fixture; +24 đúng 24 test mới). `CACHE_VERSION` v30→**v31-20261117** + đồng bộ kỳ vọng version; `tests.json` 169→170, `files.json` giữ 98, precache đo lại: 16.393.331 B (123 mục; PB3 2.082.672 B, shell/gốc 493.263 B). Lưu ý: tổng v30 ghi trong sw.js (16.440.016 B) đã đếm trùng index.html 1 lần (+72.082 B) — đo lại trên cây v30 bằng worktree: 16.367.934 B.
- Sau khi bản v31 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Triển khai Cloudflare Worker + khóa Google (T1: trạng thái worker + hướng dẫn API key) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/worker-t1`)

- Vấn đề: dịch chạy bằng Google Translate miễn phí (gtx, không key) vì worker chưa triển khai và chưa có GOOGLE_API_KEY (wrangler.toml còn placeholder KV id, worker trả not_configured 503, client fallback) — dịch chậm (~0,9 chuỗi/giây) và hay bị CORS/429. Tab Dịch không hề hiển thị trạng thái worker: chỉ có text "Đã lưu URL proxy..." khi đổi URL, không biết worker đã deploy chưa, đã cấu hình key/KV chưa.
- Sửa: `gt.js` thêm `checkProxyStatus(url)` (GET / worker, timeout 8 giây, KHÔNG ném lỗi — luôn trả object trạng thái: not_set/ok/not_configured/kv_missing/error/unreachable) + `normalizeProxyUrl` (thêm `https://` khi gõ trơn hostname — trước đây URL không scheme làm fetch lỗi lặng lẽ vì fetch coi là đường dẫn tương đối trên trang) + `setTranslateProxyUrl` dùng normalize; `cloudflare/worker.js`: GET / trả thêm `config` ("ok" | "not_configured" | "kv_not_configured") + `configured` — phát hiện khi kiểm chứng: trước đây GET luôn trả ok:true nên client không phân biệt được worker "đang dùng" với worker "triển khai rồi nhưng chưa cấu hình xong" (POST không đổi); `worker.js` (web worker): op mới `check_translate_proxy` (không cần Pyodide boot, như diag_state); `app.js` + `index.html`: tab Dịch thêm nút "Kiểm tra worker" (`btn-translate-worker-check`) + dòng trạng thái `#translate-worker-status` ("Worker: đang dùng / chưa triển khai / chưa cấu hình key/KV / lỗi / không gọi được"); tự kiểm tra khi mở tab Dịch (chưa có URL thì hiện "chưa triển khai" không tốn request); đổi URL thì kiểm tra lại — chỉ là thông tin, worker vắng/lỗi KHÔNG chặn luồng dịch (vẫn proxy → Google → MyMemory → LibreTranslate nếu có → offline); `docs/API_KEY_SETUP.md` (mới): hướng dẫn tiếng Việt đầy đủ cho user tự triển khai — tạo key Google (project + bật Cloud Translation API + restrict key chỉ API dịch) → tài khoản Cloudflare + wrangler → tạo 2 KV namespace → `wrangler secret put GOOGLE_API_KEY` (key KHÔNG vào code/.env/repo/chat) → `wrangler deploy` → dán URL vào tab Dịch → bấm Kiểm tra worker → bảng xử lý lỗi + FAQ + quy tắc an toàn key (10 yêu cầu của user).
- Kiểm chứng [ĐÃ ĐO THẬT]: harness mới `exp/verify_worker_t1.mjs` 16/16 PASS (worker thật import trực tiếp + KV giả + Google stub trả "VI: " + text gốc qua HTTP local): trạng thái ok/not_configured/kv_missing/unreachable/not_set + chuẩn hoá URL; batch 5 chuỗi mẫu qua worker thật: mode proxy-batch, placeholder ({VAR1}/{0}/{KINGCOLOR}/%s/xuống dòng) còn nguyên từng byte, 1 lần gọi Google cho cả batch; batch giống hệt lần 2: Google stub không gọi thêm (cache KV worker); file JSON mẫu 5 key: key giữ nguyên, value dịch qua worker; fallback khi worker vắng: không proxy → gtx giả dịch được, proxy chết → rơi về Google, proxy quota → BỎ QUA Google (0 lần gọi) → MyMemory; static: docs đủ bước + không chứa key AIza, wiring đủ, không rò key. Harness cũ `exp/verify_item2.mjs` vẫn TẤT CẢ XANH (17/17) sau khi GET thêm trường config (item2_results.json cập nhật theo). 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới (xem báo cáo cuối nhánh). `CACHE_VERSION` v34→**v35-20261117** (rebase lần 2 lên main `47b4f71` — PR #15 translate-speed đã lấy v34 trước, nên T1 bump lên v35; rebase lần 1 lên `bbc4dd8` đã lấy v33 nên bump lên v34) + đồng bộ kỳ vọng version ở 8 file `exp/check_*_static.py` (c6 không pin version); `files.json` giữ 99, `tests.json` 171→172 do PR #15 thêm `test_translate_speed.py` (T1 không thêm file Python); precache 124 mục, dung lượng precache đo lại trên cây đã gộp: **16.438.013 B** (PB3 2.110.504 B, shell/gốc 510.113 B) — phương pháp đo tái hiện đúng tổng v34 của main (16.429.381 B) và khớp từng byte delta của 4 file T1 đổi nội dung (index.html +337, app.js +4423, gt.js +3290, worker.js +582 = +8.632; gt.js đã gồm cả logic tốc độ của PR #15).
- **BỔ SUNG — Provider Workers AI (miễn phí, không cần thẻ) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (cùng nhánh `feat/worker-t1`):** triển khai Google kẹt ở billing (user đã thử đăng ký trial nhưng chưa tạo được tài khoản thanh toán), nên thêm đường Workers AI làm đường free chính; đường Google giữ nguyên làm lựa chọn cho sau này. Sửa: `cloudflare/worker.js` thêm provider Workers AI — model `@cf/meta/m2m100-1.2b` qua `env.AI.run({text, source_lang, target_lang})` → `{translated_text}`; chọn provider bằng biến `TRANSLATE_PROVIDER` (`auto` mặc định = Google khi có key, nếu không Workers AI khi có binding, nếu không có cả hai thì `not_configured` 503; `google`/`workers-ai` chỉ định rõ thì thiếu cái yêu cầu cũng 503 kèm lý do); KV cache + usage tháng + `MONTHLY_CHAR_LIMIT` chạy cho CẢ HAI provider, cache key GỒM provider + model nên Google và Workers AI không bao giờ lẫn nhau; ngân sách ngày Workers AI: đếm ước tính trong KV (`ai-usage:YYYY-MM-DD`, ~0,0309 neurons/token suy ra từ giá công khai $0.34/1M token [HYPOTHESIS], token ≈ ký tự/4) — hết ngưỡng `AI_DAILY_NEURON_GUARD` (mặc định 9.000, dưới hạn miễn phí 10.000 neurons/ngày) thì trả `quota_exceeded`, không bao giờ phát sinh phí (tài khoản Free vốn không gắn thẻ; Cloudflare tự báo 4006 thì worker cũng đổi thành `quota_exceeded`); m2m100: batch được tách thành từng lần gọi 1 chuỗi (cụm tối đa 5 song song, giữ đúng thứ tự) vì đoạn dài nhiều câu trong 1 lần gọi có thể bị nuốt mất câu — Google vẫn batch thật 1 request; ngắt mạch RIÊNG TỪNG PROVIDER (key `breaker:google` / `breaker:workers-ai`); GET / trả thêm `provider` + `ai_daily_used/limit` + `translate_provider`, version worker 2→3; `wrangler.toml` thêm `[ai] binding = "AI"` + `TRANSLATE_PROVIDER = "auto"` + `AI_DAILY_NEURON_GUARD = 9000`; `gt.js` `checkProxyStatus` trả thêm provider; `app.js` trạng thái tab Dịch cho biết provider đang dùng ("provider Workers AI (miễn phí, không cần thẻ)" / "provider Google") + số neurons ước tính đã dùng trong ngày; `docs/API_KEY_SETUP.md` viết lại: Đường A Workers AI lên trước (chỉ cần tài khoản Cloudflare miễn phí + wrangler deploy, không cần key/billing), Đường B Google làm lựa chọn sau này, cách đổi provider, giới hạn 10k neurons/ngày, lưu ý chất lượng + không có máy tính vẫn deploy được qua trình duyệt cloud/điện thoại.
- Kiểm chứng Workers AI [ĐÃ ĐO THẬT]: harness T1 mở rộng **30/30 PASS** (thêm 14 kiểm tra): auto không key → workers-ai; auto có key + AI → Google; `TRANSLATE_PROVIDER=workers-ai` có key → workers-ai; `=google` không key → 503; không có gì → 503; GET cho biết provider cả 3 trường hợp (version 3, `ai_daily_limit` 10000); cache tách riêng (cùng text: Google "VI: …", AI "VI-AI: …", lần 2 cả hai cached=true, không gọi thêm); batch 3 chuỗi qua AI = đúng 3 lần gọi `env.AI.run` model `@cf/meta/m2m100-1.2b`, source_lang=en, target_lang=vi; hết ngân sách ngày (seed 9000) → 429 quota_exceeded, aiCalls=0, month_used không tăng; Cloudflare 4006 → 429 quota_exceeded, không thử lại (aiCalls=1); AI lỗi thường → hoàn lại ký tự tháng + neurons (cả hai về 0), 502 ai_error. Chất lượng [FIXTURE] (mock, KHÔNG đo được chất lượng model thật): 10 chuỗi 9K thật (en.json v1.0.6) qua mock Google và mock Workers AI, gate check_placeholders THẬT (pb3/sdk/gate.py) cả hai đều PASS (checked=10, failed=0) — chứng minh pipeline (đủ chuỗi, đúng thứ tự, placeholder còn nguyên), KHÔNG chứng minh m2m100 ngang Google; Google 1 batch request, AI 10 lần gọi 1 chuỗi. Đặc tính m2m100 [FIXTURE] (mock quirk nuốt câu sau): 8 chuỗi 1 câu qua worker → đầy đủ (gửi từng chuỗi một có tác dụng); 2 chuỗi đơn lẻ có 2 câu → mất câu sau 2/2, gate FAIL (failed=2) — giới hạn đã biết, cổng kiểm tra bước 6 bắt được trước khi ép file. Harness `exp/verify_item2.mjs` vẫn TẤT CẢ XANH (17/17) sau khi sửa key ngắt mạch trong harness từ `"breaker"` sang `"breaker:google"` (hành vi không đổi: breaker mở sau ngưỡng, Google không gọi nữa, ký tự hoàn lại).
- Còn lại (user làm): triển khai worker theo `docs/API_KEY_SETUP.md` — đường A chỉ cần tài khoản Cloudflare (user đã xác nhận có tài khoản, đăng nhập nhocthu890@gmail.com, 21:34 +07) + `wrangler deploy`, không cần key Google/billing; user không có máy tính nên deploy qua trình duyệt cloud (Muse chạy hộ) hoặc điện thoại. Sau khi deploy, bấm "Kiểm tra worker" trong tab Dịch phải thấy "Worker: đang dùng — provider Workers AI (miễn phí, không cần thẻ)". Đường Google vẫn kẹt ở billing (cần tạo được billing account rồi gắn vào project beartea-translate — xem bảng lỗi trong docs) — làm sau nếu muốn chất lượng Google.
- Sau khi bản v35 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Đóng gói lại IPA (M2: từ unit đã ép → IPA cài được, bước 5) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/repack-ipa-m2`)

- Vấn đề: luồng Mod (E) chỉ xuất đơn vị nhỏ nhất đã patch (bundle/.assets, quyết định #70) — user phải tự đóng gói + ký lại bên ngoài, tool chưa đóng gói lại thành IPA cài được. User chọn làm M2 (20:07 +07), đồng thời bỏ phần hướng dẫn ký lại ("Không cần hướng dẫn đâu" — user đã biết tự ký).
- Sửa: `pb3/sdk/repack.py` (mới: `zip_entry_for_unit` cắt `::CAB`; `repack_ipa` kiểm tra IPA trước → `containers.rebuild_zip` có sẵn → verify lại, FAIL thì xóa output + `E_VERIFY_FAILED`; `verify_repacked_ipa` 6 mục zip_integrity/entry_list/payload_structure/info_plist_intact/replaced_entries/unchanged_entries; `repack_from_rebuild_units` đọc bytes đã vá từ `REBUILD_OUT_DIR`, 2 unit chung 1 entry → `E_CONFIG_INVALID`, output trống → `E_INPUT_NOT_FOUND`) + `pb3/sdk/rebuild.py` (thêm trường `"output"` vào từng unit — RAM + streaming, None khi dry-run — + note (E) chỉ sang bước 5; output (E) mặc định KHÔNG đổi, vẫn là đơn vị nhỏ nhất) + `pb3/bridge.py` (op mới `repack_ipa(path, units_json)`, verify sha file gốc sau khi chạy, trả `dest`+`filename` để worker tự attach file tải về) + UI (`index.html`: Bước 5 "Đóng gói lại IPA" sau bước 4, intro 4→5 bước; `app.js`: `state.rebuild.appliedResult` riêng với preview dry-run — phát hiện: sau apply, `st.preview` vẫn là kết quả dry-run có output=null — `refreshRepackEnabled()` chỉ bật khi applied + có output + đúng fileKey + .ipa, `repackRun()` hiện đủ 6 mục kiểm tra ✅/❌ trước khi tải về). `_CodeSignature` cũ giữ nguyên từng byte (đã hết hiệu lực — cần ký lại bằng chứng chỉ của user trước khi cài); tool không tự ký giả, không bypass DRM/FairPlay. Không hướng dẫn ký lại trong tool.
- Kiểm chứng [ĐÃ ĐO THẬT]: 18 test mới trong `pb3/tests/test_repack_ipa.py` đều pass (IPA tổng hợp: round-trip bytes/path, từ chối non-IPA/non-ZIP/entry thiếu/replacements rỗng, verify phát hiện entry chưa sửa bị sửa + replacement sai, collision/missing output/file-unit/missing unit file; bridge op synthetic; 9K thật). 9K v1.0.6 en→vi 2252 key: apply (E) 2252 changed → 2 unit có output → repack → `9kings-v1.0.6-mod.ipa`, 6/6 check PASS, 325 entry giữ nguyên thứ tự, entry chưa sửa sha giống hệt gốc, resources.assets chứa "Tiếng Việt: His Winningness", bundle ST chứa "Tiếng Việt: Farm". 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới (branch 1833 đạt / 5 fail / 124 bỏ qua vs main cùng môi trường 1814 / 6 / 124 — tập FAIL của branch là tập con của baseline: 5 fail font cũ; test batch timeout fail trên baseline nhưng pass khi chạy riêng 1,52s → flaky do tải máy, không liên quan M2; +18 đúng 18 test mới). `CACHE_VERSION` v31→**v32-20261117** + đồng bộ kỳ vọng version; `files.json` 98→99, `tests.json` 170→171, precache 123→124 mục, dung lượng precache đo lại: 16.417.804 B (PB3 2.102.735 B, shell/gốc 497.673 B).
- Còn lại: 2 unit chung 1 zip entry vẫn fail-closed (chưa gộp); replacement > 64 MB bị từ chối theo giới hạn (E); IPA rất lớn vẫn nặng RAM web khi đóng gói (đọc toàn bộ file để hash + rebuild).
- Sau khi bản v32 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### M1 — Đường ghi MonoBehaviour + StringTable (G1: mod phủ 0% → 100% key thật) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `feat/mb-write-g1`)

- Vấn đề: nút "Ép lại vào file gốc" chỉ ghi được TextAsset JSON dict; cả 3 game đã đo (9K, PB3, EK2) có 0 key dạng này — 100% text thật nằm ở MonoBehaviour (bảng ngôn ngữ I2 + StringTable) nên mod phủ 0% key thật.
- Sửa (`pb3/sdk/unity.py`, `plan.py`, `rebuild.py`, `gate.py`, `pb3/bridge.py`): writer trường string MonoBehaviour có type tree (`write_typetree_value` — ảnh gương `read_typetree_value`, giữ trailing bytes) + writer bảng I2 không type tree (`write_language_source_bytes`, segment-splice) + `apply_monobehaviour_patch` (chỉ REBUILD_SECTION, verify 2 lớp: cấu trúc + giá trị từng field) + `monobehaviour_write_targets` (LS term / ST entry; key ST thiếu trong bảng thì CHÈN entry mới `m_TableData[+]`). `build_plan` lập entry `MonoBehaviour` (before_sha/after_sha = sha object bytes); `rebuild.py` build `mb_index` {collection: {key: m_Id}} 1 lần trước Vòng 1 (SharedTableData ở bundle khác StringTable); `gate.py` mở rộng probes + verify pressed cho MB; `bridge.py` thread `source_locale` (mặc định "en" — ghi vào cột/bảng en vì chưa có cột vi). Generic MonoBehaviour (scan path) vẫn unmatched reason `monobehaviour`. Gộp: theo lệnh tự động của user sau khi verify đạt (PR + merge commit xem trên GitHub).
- Kiểm chứng [ĐÃ ĐO THẬT]: 9K v1.0.6 en→vi ("Tiếng Việt: " + en, 2.252 key) → ép thật → vá IPA (chỉ để kiểm chứng, /tmp) → trích lại đúng 2.252 key, giá trị khớp 100%, thứ tự key khớp mẫu; delivery_gate PASS cả pre_write lẫn pressed (font hiển thị Be Vietnam Pro); file gốc sha không đổi. Harness `exp/verify_bug2_merge.py` PASS 4/4. Smoke 59/59; full suite 0 đỏ mới (branch 1.761 đạt / 102 lỗi / 27 fail / 4 bỏ qua vs main 1.743 đạt / 102 lỗi / 27 fail / 4 bỏ qua — +18 đúng 18 test mới, tập FAIL giống hệt). 18 test mới trong `pb3/tests/test_mb_write.py` (round-trip trên fixture + 1 test trên dữ liệu game thật 9K). `CACHE_VERSION` v25→**v26-20261117** + đồng bộ mọi kỳ vọng version ở 21 file exp + `tests.json` 167→168 (thêm test mới).
- Sau khi bản v26 lên live, user trên Safari cần tải lại trang 2 lần (kích hoạt SW mới rồi chạy code mới) như các bản trước.

### Sửa text mục (E) sau M1 (ghi chú UI nói sai phạm vi mod) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `fix/e-section-text-v27`)

- Vấn đề: sau M1 (PR #7), giao diện tab Mod vẫn ghi "(E) v1 chỉ ghi TextAsset JSON dạng dict — key MonoBehaviour/StringTable sẽ liệt kê rõ là không ép được" nên user tưởng mod vẫn không ghi được MB.
- Sửa (`index.html`, `app.js`, `pb3/sdk/gate.py`): dòng mô tả mục (E) thành "(E) ghi được TextAsset JSON dạng dict + MonoBehaviour/StringTable"; lý do unmatched `monobehaviour` (cả ở gate lẫn UI) thành "MonoBehaviour generic (scan path) / bảng ngôn ngữ CÓ type tree — chưa có đường ghi". Giữ nguyên mọi cảnh báo an toàn khác (dry-run, cổng bước 6, không ghi đè file gốc, không phải IPA, v9 chỉ đọc, bundle mã hóa không giải mã).
- Kiểm chứng [ĐÃ ĐO THẬT]: 9 check tĩnh `exp/check_*_static.py` đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4. `CACHE_VERSION` v26→**v27-20261117** + đồng bộ kỳ vọng version ở 21 file exp; ghi chú dung lượng precache trong `sw.js` đo lại: 16.295.629 B.
- Sau khi bản v27 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### Sửa masking placeholder bên dịch (cổng bước 6 chặn vì placeholder lệch) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (nhánh `fix/placeholder-mask`)

- Vấn đề: user dịch Build 68 en→vi (2188 key); dry-run đổi được 1985 key nhưng cổng bước 6 chặn không cho ghi vì placeholder lệch: `Chaos_IconDescription` gốc có `[{VAR1}]` mà bản dịch mất; `...UnlockLevelSubtitle` mất placeholder; `Diplom..._Multiplayer` bị đổi thành `[{KINGCOLOR}]` khác gốc.
- Chẩn đoán [ĐÃ ĐO THẬT]: `mask_line` vốn đã che được `{VAR1}`/`{KINGCOLOR}`/`{0}`/`%s`/thẻ — nhưng THIẾU 2 dạng cổng đếm là `$VAR` và `{nội dung bất kỳ}` (`{foo-bar}`, `{a.b}`, `{}`), nên máy dịch nhìn thấy chúng như chữ thường và làm mất; `unmask_line` không chịu được token bị lột gạch dưới (`PB3PH0` trần, `__PB3PH0`, `PB3PH1__`) hay bị đánh lại số; và không có bước verify + sửa sau khi bỏ che nên bản dịch hỏng được giữ nguyên. `gt.js` không có masking nhưng đường web đã che ở Python trước khi gọi JS nên không cần sửa.
- Sửa (`pb3/sdk/translate.py`, `pb3/sdk/exporters.py`, `pb3/bridge.py`): mở rộng `_PLACEHOLDER_RE` thành siêu tập của cổng (`\{[^}]*\}` + `\$[A-Za-z_][A-Za-z0-9_]*`, giữ che thẻ đóng) và `_PLACEHOLDER_RES` tương tự (để `placeholders_ok` thấy `$var`/`{...}` mà cảnh báo + không cache bản dịch hỏng); cứng `_TOKEN_RE` thành `(?:_{1,2}\s*)?PB3PH\s*(\d+)(?:\s*_{1,2})?` (mỗi bên 0–2 gạch dưới, khoảng trắng chỉ nuốt khi có gạch dưới kề bên); thêm `placeholders_in_order`/`placeholders_match_strict` (thứ tự + số lần + xuống dòng, như cổng); verify + dịch lại 1 lần sau khi bỏ che ở cả `translate_table` (`_repair_placeholder_lines`) và `translate_json_google` (dịch đơn `_gt`, gom theo masked text, bỏ qua khi hết hạn mức/hủy) — chỉ nhận bản dịch lại khi strict pass, vẫn lệch thì giữ bản cũ + cảnh báo như trước. Hành vi đã pin (giữ bản dịch hỏng + cảnh báo + không cache) giữ nguyên. `sdk/gate.py`/`sdk/plan.py` không đổi (đã đếm đủ, là chuẩn để mask bám theo).
- Kiểm chứng [ĐÃ ĐO THẬT]: 9 test mới đều pass (che `$var`/`{...}` bất kỳ + round-trip, unmask lột gạch dưới, retry khôi phục sau khi làm rơi token, bridge batch-làm-rơi → dịch đơn cứu); 5 file test liên quan 157 đạt / 2 bỏ qua; 9 check tĩnh đều `fails: []`; smoke 59/59; harness `exp/verify_bug2_merge.py` PASS 4/4; full suite 0 đỏ mới (branch 1899 test = 1773 đạt / 99 lỗi / 24 fail / 3 bỏ qua vs main 1890 = 1764 đạt / 99 / 24 / 3 — tập FAIL/ERROR giống hệt, +9 đúng 9 test mới); E2E fixture 7 key mẫu (gồm key thật của user) qua mock làm rơi token → gate `check_placeholders` PASS, placeholder còn nguyên đúng thứ tự + số lần, warnings rỗng. `CACHE_VERSION` v27→**v28-20261117** + đồng bộ kỳ vọng version ở 21 file exp; ghi chú dung lượng precache trong `sw.js` đo lại: 16.302.642 B.
- Lưu ý: fix này chỉ giúp các bản dịch TRONG tool về sau; file `translated.vi.json` hiện tại của user vẫn cần sửa tay các key bị cờ (khôi phục placeholder gốc chính xác) rồi import lại, chạy lại dry-run, kiểm tra cổng bước 6. Sau khi bản v28 lên live, user trên Safari cần tải lại trang 2 lần như các bản trước.

### BUG#2 (đường merge trích xuất: loại quét phẳng khỏi file locale, tách biến thể zh, vứt rác) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (PR #4, đã gộp vào main `f1a46eb7`)

- Chuẩn do Joseph chốt: 1 file duy nhất cho mỗi locale xin (en → chỉ
  `en.json`; en+zh → `en.json` + `zh-CN.json` + `zh-TW.json`); đủ toàn bộ
  text (term langtable + StringTable gộp `Collection/Key`); 0 key generic
  (`.sNNNN`/`.pNNNN`/`textasset.sN`); rác vứt hết (không giữ
  `_non_localization.json`, không provenance/checkpoint/report/conflicts
  trong output); áp dụng chung mọi game Unity, không hardcode game/build.
- Nguyên nhân gốc: parser P0-3 đúng; `merge_language_outcomes()` gộp chỉ
  theo locale, giữ-bản-đầu, không phân biệt nguồn có cấu trúc hay quét
  phẳng — langtable đúng (2.086 term) bị gộp chung với quét phẳng cùng
  `resources.assets` thành `en.json` 6.140 key (3.955 rác). `_build_layers()`
  không gắn thẻ nguồn; `_language_source_base_columns()` gộp biến thể về
  mã gốc; pipeline dùng `_join_string_tables()` phẳng thay vì
  `join_string_tables_grouped()`; bộ lọc locale thiếu mã bg/fil/nb.
- Sửa (`pb3/sdk/extraction.py`, `discovery.py`, `rebuild.py`, `bridge.py`,
  `plugins/csv_table/plugin.py`): gắn thẻ `origin_kind` cho `FileOutcome`
  (language_source / string_table / generic_flat / text_asset / other);
  merge chỉ gộp outcome có cấu trúc, outcome generic_flat/locale:None
  passthrough (không ghi ra đĩa ở chế độ gộp); một file ảo cho mỗi biến thể
  đầy đủ (zh-CN/zh-TW riêng, mã gốc `zh` chỉ làm fallback sau biến thể);
  StringTable dùng `join_string_tables_grouped()` + điền đủ vũ trụ
  SharedTableData (kể cả tạo bucket collection chưa có cho locale đã có
  StringTable); `_export` chế độ gộp chỉ ghi file locale đã gộp (tên file
  dùng mã chuẩn `zh-CN.json`); `total_entries` chỉ đếm key đã gộp; bản kê
  `language_columns` đủ 30 cột `mLanguages` trong report nội bộ; plugin CSV
  bỏ dòng hoàn toàn trống (không sinh key `row_NNNNNN` rỗng — 2 dòng trống
  trong `PB3_LOC_DEFAULT.csv`); `_collect_mb_keys` thêm dạng
  `Collection/Key` cho ép ngược; `LOCALE_BUILTIN` thêm zh-CN/zh-TW/bg/fil/nb
  riêng; `CACHE_VERSION` v22→**v23-20261117** + đồng bộ mọi kỳ vọng version/
  đếm tests.json (166→167, thêm `pb3/tests/test_bug2_merge.py`, 22 test mới).
- Kiểm chứng [ĐÃ ĐO THẬT] (harness `exp/verify_bug2_merge.py`, PASS 4/4,
  0 bỏ qua; CPython qua `bridge.extract`, locales `["en","zh"]`,
  `merge_one_file=True`): Build 68 `en.json` == `en-build68.json` từng
  key/giá trị/thứ tự (2.086 key, 0 generic), zip chỉ 3 file locale;
  9 Kings v1.0.6 mỗi locale 2.252 key, 0 generic, en == mẫu, zh-TW == mẫu
  trừ 26 key Policies (mẫu để trống, bản gộp giữ giá trị thật từ bảng ST
  `zh` duy nhất của game — fallback mã gốc→biến thể, xem harness);
  PB3 v1.1.7 en/zh-CN 2.348 key/file, 0 generic (game không có zh-TW: chỉ
  có cột `Chinese (Simplified)` trong CSV lẫn langtable); EK2 v0.0.18 chỉ
  `en.json` 1.344 key (không có zh — đúng, không phải lỗi). Smoke 59/59;
  full 1.872 = 1.737 đạt / 102 lỗi / 29 fail / 4 bỏ qua — tập đỏ trùng hệt
  baseline main (2 fixture thiếu), 0 đỏ mới; 9 check tĩnh `fails: []`.
- Probe `sharedassets0.assets` [ĐÃ ĐO THẬT] (9 Kings v1.0.6,
  154.591.236 B; bản Build 68 là 155.232.944 B): SerializedFile, 128 object
  MonoBehaviour toàn generic (1.132 chuỗi), 0 language_source, 0
  StringTable; 81 TextAsset toàn `ChallengeData_*` (dữ liệu game JSON,
  không phải text localizable). Kết luận: file này đóng góp 0 nội dung
  localizable có cấu trúc → KHÔNG làm streaming, giữ giới hạn 64 MB +
  lý do skip trong log. Không nâng `max_entry_bytes`.
- Đánh đổi đã biết: game hoàn toàn không có nguồn ngôn ngữ có cấu trúc sẽ
  không ra file locale nào ở chế độ gộp (rác bị vứt, không còn đường quét
  phẳng làm dự phòng) — P1-5 (raw scan, gắn nhãn, chỉ làm đường dự phòng
  cuối) có thể xem lại sau. Harness Node cũ
  `exp/verify_analysis_merge_one_file.mjs` assert merged==tách 100% — nay
  sai theo thiết kế (gộp loại generic, tách giữ generic); chưa chạy được ở
  đây (cần Playwright/fixtures), để lại làm follow-up, không sửa file lịch
  sử `exp/*_results.*`.
- Sau khi bản v23 lên live, user trên Safari cần: có mạng, tải lại trang 1
  lần (kích hoạt SW mới), rồi tải lại lần nữa (hoặc đóng tab/mở lại) để
  chạy code mới; nếu vẫn ra kết quả cũ thì xóa dữ liệu trang rồi mở lại.

### Prefetch Pyodide báo sai "thiếu byte" (response gzip: Content-Length là kích thước sau nén) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (PR #5, đã gộp vào main `55a8d162`)

- Triệu chứng user báo (ảnh chụp màn hình 2026-11-17 15:55, bản v23):
  "Tải trước Pyodide chưa xong hẳn (3 file lỗi: pyodide/pyodide.mjs
  (thiếu byte), pyodide/pyodide.asm.mjs (thiếu byte),
  pyodide/pyodide.asm.wasm (thiếu byte)) — vẫn khởi động bình thường
  khi bạn chọn file." File KHÔNG hề bị cắt/thiếu thật.
- Nguyên nhân gốc [ĐÃ ĐO THẬT] (curl -I tới
  `munkai0408.github.io/MeoRocky/pyodide/<file>` với
  `Accept-Encoding: gzip`): GitHub Pages trả 5/6 file prefetch với
  `Content-Encoding: gzip`; `Content-Length` là kích thước SAU NÉN còn
  `res.body` cho ra byte SAU GIẢI NÉN — pyodide.mjs 7.089/17.758,
  pyodide.asm.mjs 261.962/1.250.259, pyodide.asm.wasm 3.582.490/9.597.892,
  pyodide-lock.json 26.858/119.077, file `.whl` lz4 55.075/55.914; chỉ
  `python_stdlib.zip` không nén (2.545.550/2.545.550). `prefetchPyodide`
  trong `app.js` so `got !== total` nên báo sai "thiếu byte" cho mọi file
  gzip phải tải qua mạng. 3 file còn lại thoát chỉ vì đã có trong cache
  (nhánh `caches.match` bỏ qua bước kiểm tra này). Lỗi hiện ra sau bản
  v23 vì đổi tên cache làm cache lạnh, tải trước phải tải qua mạng thay
  vì trúng cache.
- Sửa (`app.js`, hàm `prefetchPyodide`, nhánh fetch): đọc
  `Content-Encoding`; khi response có mã hóa thì BỎ QUA bước kiểm tra đếm
  byte theo Content-Length (stream đọc tới `done` + tính toàn vẹn của
  gzip đã đủ bảo đảm file đầy đủ; gzip bị cắt thật thì reader reject và
  rơi vào `catch`, vẫn bị ghi là file lỗi — đúng hành vi). Chỉ ghi
  "thiếu byte" khi `got < total` VÀ không có Content-Encoding. Giữ nguyên
  mọi hành vi khác (cache hit, ghi cache, đếm tiến độ).
  `CACHE_VERSION` v23→**v24-20261117** + đồng bộ mọi kỳ vọng version trong
  20 file `exp/` (12 `verify_*.mjs` + 8 `check_*_static.py`); check tĩnh
  `exp/verify_analysis_c3.mjs` (S3) thêm khóa hành vi mới
  (`prefetchGzipByteCheck`, `prefetchNoStrictByteCheck`).
- Kiểm chứng [ĐÃ ĐO THẬT] (mô phỏng Node tải thật 6 file prefetch từ
  Pages với gzip): logic CŨ báo sai 5/6 file, logic MỚI pass 6/6, 0 báo
  sai; lưới an toàn còn hoạt động (response không nén bị cắt thật
  got=400 < total=1000 vẫn báo; stream gzip cắt giữa chừng reader
  reject → rơi vào catch). Check tĩnh 9/9 `fails: []`; harness
  `exp/verify_analysis_c3.mjs` 5/5 mục tĩnh/vm PASS (mục
  `browser_end_to_end` không chạy được ở đây — thiếu playwright-core +
  Chromium, giới hạn môi trường có từ trước, không do bản sửa này).
  Smoke 59/59; full 1.872 = 1.739 đạt / 102 lỗi / 27 fail / 4 bỏ qua —
  tập đỏ là tập con của baseline BUG#2 (102 lỗi + 29 fail), 0 đỏ mới,
  2 test Apple Strings UTF-16 BOM trước fail nay pass (dao động môi
  trường; bản sửa không đụng code Python).
- Sau khi bản v24 lên live, user trên Safari cần: có mạng, tải lại trang
  1 lần (kích hoạt SW mới), rồi tải lại lần nữa (hoặc đóng tab/mở lại)
  để chạy code mới; dòng "3 file lỗi" phải biến mất ngay từ lần tải
  trước đầu tiên sau khi cache lạnh.

### Sắp thứ tự key StringTable theo asset (khớp file mẫu byte-identical) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (PR #6, đã gộp vào main `0f41fad4`)

- Vấn đề: sau BUG#2, nội dung 9 Kings v1.0.6 đã khớp mẫu 100% (2.252
  key/locale) nhưng thứ tự 102 key StringTable (dạng `Collection/Key`,
  vị trí 2150–2251) còn lệch mẫu: collection xếp theo chữ cái
  (Buildings, CommonTexts, Kings, Policies, Spells, Troops) thay vì
  (Policies, Buildings, CommonTexts, Kings, Spells, Troops); trong
  Buildings/Spells, nhóm key `_desc` lệch vị trí.
- Nguyên nhân gốc [ĐÃ ĐO THẬT] (đo trên IPA 9K v1.0.6 + file mẫu):
  file mẫu xếp key trong mỗi collection theo **m_Id tăng dần của
  SharedTableData** (đúng cả 6 collection; 2 collection phân biệt
  Buildings/Spells — thứ tự asset của StringTable lẫn SharedTableData
  đều khác mẫu, chỉ sort theo m_Id mới khớp), và xếp collection theo
  **lần gặp đầu trong SharedTableData**. Code cũ giữ thứ tự asset của
  bảng StringTable từng locale, `sorted(grouped.items())` xếp
  collection theo chữ cái, và `_fill_string_table_universe` dồn key
  điền "" xuống cuối bucket (mẫu để `Spells/Cemetery_Desc` giữa khối).
- Sửa (`pb3/sdk/extraction.py`): helper `_st_key_sort_id` /
  `_sort_st_bucket` / `_invert_shared_keys`; `join_string_tables_grouped`
  sắp mỗi bucket theo m_Id (key `id_<m_Id>` sắp theo số trong tên);
  `_fill_string_table_universe` sắp lại bucket theo m_Id sau khi điền
  và xếp collection theo lần gặp đầu trong SharedTableData; vòng duyệt
  grouped trong `_build_layers` bỏ `sorted()` theo chữ cái. Quy tắc
  chung theo dữ liệu asset, không hardcode game/build. Harness
  `exp/verify_bug2_merge.py` bổ sung check thứ tự key + md5 file thô
  (dict == cũ không kiểm thứ tự). `CACHE_VERSION` v23→**v25-20261117**
  (`extraction.py` nằm trong precache) + đồng bộ mọi kỳ vọng version ở
  20 file exp. LƯU Ý: v24 đã bị PR #5 (prefetch gzip, đã gộp vào main `55a8d162`) chiếm —
  nhánh này dùng v25 để tránh hai PR cùng v24 khiến sw.js sau gộp
  giống hệt byte với bản live và trình duyệt không cập nhật code mới;
  khi gộp PR #5 và PR này đã xung đột ở sw.js + file kỳ vọng version —
  đã giải quyết trong commit merge này, giữ v25.
- Kiểm chứng [ĐÃ ĐO THẬT]: trích xuất lại IPA 9K v1.0.6 bằng code sau
  sửa, `en.json` md5 file thô **4c22c9ebcdd04cc21c7f3f4a4952e955** —
  **giống hệt file mẫu** (byte-identical, 142.098 B); zh-TW thứ tự key
  giống mẫu, chỉ khác đúng 26 key Policies fallback; zh-CN 2.252 key
  cùng tập key. Harness `exp/verify_bug2_merge.py` PASS 4/4 (kèm check
  thứ tự/md5 mới). Smoke 59/59; full suite 0 đỏ mới (xem báo cáo cuối
  nhánh). 4 test mới trong `pb3/tests/test_bug2_merge.py`
  (`StringTableOrderCase`) khóa đúng 3 quy tắc thứ tự.
- Sau khi bản v25 lên live, user trên Safari cần tải lại trang 2 lần
  (kích hoạt SW mới rồi chạy code mới) như các bản trước.

### P0-3 (trích xuất cấu trúc bảng ngôn ngữ + StringTable, lọc rác, conflicts) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `f88934c5`, PR #3, gộp vào main `fead921f`)

- Parser cấu trúc bảng ngôn ngữ của game (`pb3/sdk/unity.py`, kind mới
  `"language_source"`): đọc `mLanguages`/`mTerms`, ánh xạ theo chỉ số, giữ
  chuỗi rỗng, kiểm chứng layout rồi mới nhận (fail-closed); đường field-shape
  cho build có typetree là [HYPOTHESIS] (chỉ dict tổng hợp). Định tuyến riêng
  trong pipeline (`pb3/sdk/extraction.py`): file ảo
  `*.langtable.monobehaviour.<mã gốc>.json`, key tên term thật; biến thể gộp về
  mã gốc. Gộp StringTable theo nhóm (`join_string_tables_grouped`, hàm cũ giữ
  nguyên, đã kiểm vi sai byte-identical). Lọc rác Unity ở đường generic (theo
  mẫu chung, có cơ sở đo). `_export` ghi `conflicts.json` khi gộp có xung đột.
  `rebuild.py` cộng tên term vào `mb_terms`. 35 test mới, không file pb3 mới,
  không đổi precache (files.json 95, tests.json 166, CACHE v21).
- Xuất locales [ĐÃ ĐO THẬT] (script ngoài repo, thư mục giao
  `deliver_p03_locales/`): 9 Kings v1.0.6 IPA 2.252 key/file (I2 2.150 =
  mTerms + ST 102), thiếu en 2 / zh-CN 2.218 / zh-TW 106, xung đột 0, rác
  1.006, tự kiểm tra 11/11 PASS (4 giá trị mẫu khớp, rò rỉ cột 0/2.150,
  placeholder/thẻ 0 lệch); đối chiếu file mẫu Build 68: 2.086 key/file, thiếu
  93/38, 14/14 PASS, byte-identical với kết quả mẫu của parent; PB3 v1.1.7
  2.348 key/file, 10/10 PASS; EK2 v0.0.18 1.344 key/file, 10/10 PASS;
  DungeonWithin CHƯA ĐẠT có lý do (0 bảng, thoát 2, không ghi file, không giải
  mã). Cùng parser, không hardcode, đúng trên 4 build (số term/cột/thứ tự cột/
  đuôi khác nhau).
- Hồi quy §4 ĐỔI CÓ LÝ DO (xem bảng mốc bên dưới): key `.sNNNN` thành tên term
  thật; EK2 mất file zh là đúng (7 key cũ là tiếng Nhật gán nhầm). Lỗi va chạm
  tên file ảo (dict ghi đè mất bảng) đã gặp khi kiểm chứng, đã sửa bằng đoạn
  `.langtable` + test khóa. Đường quét phẳng chỉ còn cho object không phải
  bảng ngôn ngữ, ghi rõ lý do trong code.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full 1.846 chạy = 1.816 đạt /
  25 lỗi / 3 fail / 2 bỏ qua (103,2s), tập đỏ trùng hệt mốc trước sửa (2
  fixture thiếu đã biết), không đỏ mới; harness rebuild 13/13 PASS, harness
  cổng 13/13 PASS (Chromium thật); harness gộp §1 đạt, §2–§4 bị chặn từ trước
  (fixture vắng + đường dẫn cũ đã chết), §5: c2 13/13 + item8 đạt, 5 harness
  con FAIL do fixture/đường dẫn vắng từ trước. Chi tiết:
  `exp/p03_language_source_results.md`.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: file 9 Kings v1.0.5 chưa có (mọi số 9 Kings là
  v1.0.6 — xem "Chờ Joseph" #6); EK2 100 bundle mã hóa ngoài phạm vi; đường
  field-shape [HYPOTHESIS]; Safari/iSH [CHƯA KIỂM CHỨNG] (không đổi UI nên
  không có bước thử mới); job CI `unit` exit 1 (nguyên nhân chưa rõ, như cũ).

### P0-2 (cổng kiểm tra trước khi giao, bước 6) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `982174fc`, PR #2, gộp vào main `d93b79a3`)

- Cổng duy nhất: bridge op `delivery_gate` + module mới `pb3/sdk/gate.py`
  (stdlib-only, Python 3.9, chỉ đọc, không ghi file) trả 1 verdict
  PASS/FAIL + 5 kiểm tra (bản dịch đầy đủ, placeholder/thẻ/xuống dòng,
  diffverify theo đúng đường (E) + provenance, độ phủ font bộ 142 mục
  theo từng font, toàn vẹn hash/cấu trúc theo kế hoạch ép) + lỗi dễ hiểu.
  Không verdict thì không giao file. UI: mục cổng trong tab Mod, nút
  "Ép và ghi file" chỉ bật khi cổng ĐẠT cho đúng file + bản dịch + lựa
  chọn hiện tại (khóa theo hash đầu vào); `rebuild_apply` bắt verdict
  PASS khớp sha nguồn + sha bản dịch khi được truyền verdict.
- Quy tắc chặn: FAIL chặn giao tuyệt đối, không có đường giao khi cảnh
  báo — vì file giao sai (thiếu key, mất placeholder, thiếu glyph) hỏng
  game theo cách user khó phát hiện. Áp nhất quán ở UI + bridge.
- Phạm vi + giới hạn có ghi rõ: cổng chỉ áp flow (E); gọi bridge trực
  tiếp không verdict giữ hành vi cũ (UI là đường giao duy nhất, luôn
  truyền verdict). Kiểm pre-write bằng mô phỏng RAM; bundle độc lập
  ≥64MB kiểm file đã ép thật ở chế độ pressed. File đã ép chỉ cần khớp
  nội dung + sha script, không cần byte-identical (khác bytes thì cảnh
  báo). Font nhúng Unity không đọc được bytes → "chưa kiểm được", không
  mặc định tốt/xấu. Placeholder so theo multiset có thứ tự + số lượng
  newline (chặt hơn `placeholders_ok` cũ).
- Đếm file: `files.json` 94→95 (+`pb3/sdk/gate.py`); `tests.json` 165→166
  (+`pb3/tests/test_gate.py`, 52 test); `sw.js` CACHE_VERSION
  v20-20261117→v21-20261117, precache 119→120 (0 mục chết, tổng thật
  16.185.827 B = 15,44 MiB); kỳ vọng đếm/version ở MỌI harness cũ đã
  đồng bộ; driver D1 của harness P0-1 nay chạy cổng (9 key đủ, gồm
  `title`) giữa preview và apply.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover)
  1811 chạy = 1781 đạt / 25 lỗi / 3 fail / 2 bỏ qua (79,6s), tập 28 đỏ
  giống hệt baseline (2 fixture thiếu đã biết), không đỏ mới; harness
  mới `verify_delivery_gate.mjs` 13/13 PASS trên Chromium thật
  (Playwright scratch, C1–C5 + D1–D4 xanh); harness P0-1 chạy lại 13/13
  PASS; item6 giữ 2 fail gzip từ trước, item7 giữ 1 fail fixture từ
  trước. Hồi quy §4 khớp mốc brief (xem bên dưới).
- Game thật [ĐÃ ĐO THẬT]: cổng FAIL đúng trên cả 3 IPA (9K: 13.465 key
  chưa ép, 13.464 MB + 1 non-string; PB3: 14.556 key, 14.517 MB + 39
  not_found + 1 đơn vị CSV bị từ chối; EK2: 17.240 key MB) — (E) v1 vẫn
  phủ 0% key ngôn ngữ thật; font FAIL ở 9K/PB3 (0 font đọc được), PASS
  ở EK2 (Roboto-Bold 139/142 phủ đủ chữ đang dùng). Vòng tròn khép trên
  `resources.assets` 9K (3 key → 5 changes, PASS cả pre-write lẫn
  pressed, sha bản copy không đổi). DungeonWithin probe-only: FAIL
  (key không tìm thấy + font chưa kiểm được), không giải mã.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM + flow Safari thật [CHƯA KIỂM CHỨNG]
  (bước thử trong `exp/delivery_gate_results.md` §6); thử iSH chưa làm;
  đường ghi MonoBehaviour (G1, để mục sau); job CI `unit` exit 1 (nguyên
  nhân chưa rõ, như P0-1).

### P0-1 (E) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `41e98028`, PR #1, gộp vào main `9fb4a2ed`)

- (E) triển khai đủ: bridge op duy nhất `rebuild_apply`, dry-run mặc định + confirm gate,
  nhóm theo provenance, xung đột đúng schema merge (cap 500), plan-per-unit qua executor
  hiện có, khóa REBUILD_ALL giữ nguyên (`MATURITY="M2"`), output = đơn vị nhỏ nhất/ZIP,
  không đóng gói lại IPA, streaming G7 (none/LZ4/LZMA/Zstd→none, verify, progress đơn điệu),
  UI (E) đầy đủ + thông báo theo mã lỗi thật.
- G1 — PHẠM VI THU HẸP CÓ GHI RÕ: (E) v1 chỉ ghi TextAsset JSON dict. Đo thật: cả 3 game
  có 0 key TextAsset JSON dict; 100% key ngôn ngữ là MonoBehaviour (PB3 thêm CSV) → (E) v1
  phủ 0% key ngôn ngữ thật, mọi key MB vào unmatched `monobehaviour` có đếm rõ. Vòng tròn
  chỉ khép cho TextAsset (chân B: `resources.assets` 25.558.592 B, 3 key → 5 changes,
  dict khớp 100% + thứ tự key). Không tuyên bố đủ tiêu chí vòng tròn cho nhóm MB.
- G7 streaming: đạt trên fixture; bundle thật ≥64MB độc lập chưa có để thử.
- Sửa lỗi kèm theo (đợt sửa-lỗi + D2): vá 2 lỗ innerHTML (4/4 `f.source` qua `esc()`);
  precache 131→119 (xóa 13 wheel chết, +1 `rebuild.py`, CACHE `v20-20261117`);
  đính chính gzip 102400B; 3 chốt chặn dest==source (containers/compress/patch_pack);
  sửa lỗi thay-theo-giá-trị-trần ở plan.py + patch_pack.py; bundle mã hóa hiện refused
  `E_ENCRYPTED`; tài liệu M1–M25/N1–N15 phần lớn đã sửa (chi tiết trong HANDOVER §10);
  lỗi D2 (reselect xóa state) đã sửa, driver D3 hoàn thiện theo tiền lệ c4/reselect.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover) 1759 chạy =
  1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua (77,1s), 28 đỏ đều do 2 fixture thiếu đã biết
  (`zip/sample.ipa`, `sqlite_loc/a.sqlite`); harness mới `verify_analysis_rebuild.mjs`
  13/13 PASS trên Chromium thật (Playwright scratch, D1–D4 xanh); harness liên quan khác
  giữ nguyên trạng thái đỏ-từ-trước đã ghi (item6 2 fail gzip, item7 1 fail fixture,
  item8 1 fail import, c9 thiếu bigtest.zip). Hồi quy §4 khớp mốc brief (xem bên dưới).
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM Safari thật [CHƯA KIỂM CHỨNG] (không có Safari thật);
  vòng tròn trên data.unity3d 590MB (file không được cung cấp); đường ghi MonoBehaviour
  (G1, để mục sau); job CI `unit` exit 1 (tái hiện cục bộ xanh, nguyên nhân chưa rõ);
  M20/M23/N5-"1692" còn mở (xem HANDOVER §10).

## Bị chặn / Chờ Joseph (ghi + bỏ qua, không đứng chờ)

1. Tải asset `pb3-fixtures-v1.zip` lên Release `fixtures-v1` (id 446604484, hiện
   `assets: []`) — nguyên nhân job CI `assets` exit 1 đã xác định [ĐÃ ĐO THẬT].
   Không sửa code/workflow nào khắc phục được; tải qua giao diện web GitHub.
2. Thử Safari thật + đo RAM (bước thử trong `exp/analysis_rebuild_results.md` §6
   cho (E) và `exp/delivery_gate_results.md` §6 cho cổng);
   chạy iSH (patch02 suite + extract + font scan) khi tới P2-4.
3. File `data.unity3d` 590MB (brief §4) chưa được cung cấp — cần để đủ tiêu chí vòng tròn
   P0-1 trên file lớn; đã thay bằng bundle lớn nhất có thật + ghi rõ.
4. Cloudflare translate-proxy (cần tài khoản + Google API key, trần 500.000 ký tự/tháng),
   PAT mới — khi tới các mục cần chúng.
5. P0-4 (Space Marshals 2 mất chữ) — BỎ QUA 2026-11-17, chờ Joseph: cần ảnh chụp
   màn hình Q1/Q2/Q3 bản v4 + xác nhận bản đang cài trên máy. Khi có đủ: đối
   chiếu chẩn đoán v1–v4 (ghi trong bộ nhớ của tài khoản này: registry glyph cố
   định, donor-quad squish, 6 slot cursed đã sửa ở v3, probe v4) vào tài liệu
   BearTea + sửa tiếp theo ảnh. Không làm P0-4 khi chưa có ảnh.
6. File 9 Kings v1.0.5 để chạy lại đúng bản user nhắc tới — hiện chỉ có v1.0.6
   (173.472.485 B, md5 4f53043c…); mọi số P0-3 của 9 Kings là v1.0.6, không
   tuyên bố số v1.0.5.

## Mốc hồi quy mới nhất [ĐÃ ĐO THẬT] (CPython qua bridge.extract, en+zh, gộp 1 file)

Mốc mới sau P0-3 (2026-11-17) — số cũ trước P0-3 trong ngoặc:

| Game (build) | en / xung đột | zh / xung đột | #N | Gộp khớp tách |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 6.694 / 13 (trước: 13.465 / 17) | 2.176 / 0 (trước: 1.914 / 0) | 0 | 100% |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 11.065 / 126 (trước: 14.556 / 293) | 2.350 / 5 (trước: 2.570 / 224) | 0 | 100% |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 10.468 / 2 (trước: 17.240 / 2) | không còn file (trước: 7 / 0) | 0 | 100% |

Lý do đổi (đã đo bằng diff trực tiếp, xem `exp/p03_language_source_results.md`
§4): key `.sNNNN`/`.pNNNN` được thay bằng tên term thật từ bảng ngôn ngữ có cấu
trúc (mọi term có mặt đầy đủ: 2.150/2.150, 2.348/2.348, 1.344/1.344); bộ lọc rác
loại giá trị Unity khỏi đường generic; trùng lặp giống hệt gộp lại nên xung đột
giảm. EK2 mất file zh là ĐÚNG: 7 key zh cũ là chuỗi tiếng Nhật `日本語` gán
nhầm, EK2 không có dữ liệu tiếng Trung. Mốc cũ (trước P0-3) khớp chính xác mốc
brief §4 → mốc đo trên đúng 3 build này. Phân loại key (trước P0-3): 9K
mb_generic 15.254 + mb_table 125; PB3 mb_generic 12.426 + CSV 4.700; EK2
mb_generic 17.247; TextAsset JSON dict = 0 ở cả 3. DungeonWithin (91.389.555 B):
chỉ quét/probe, 4 bundle Addressables mã hóa AES — KHÔNG giải mã.
Passpartout/Dead Cells/data.unity3d: chưa có file.
Chạy lại sau P0-2 (2026-11-17): khớp chính xác cả 3 game, merged==tách 100%, không lệch.

Mốc mới sau BUG#2 (2026-11-17) — harness `exp/verify_bug2_merge.py` PASS 4/4,
output zip CHỈ chứa file locale (không còn rác/provenance/report/conflicts):

| Game (build) | en | zh-CN | zh-TW | Generic |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 2.252 (== mẫu) | 2.252 | 2.252 (== mẫu trừ 26 key Policies fallback ST zh) | 0 |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 2.348 | 2.348 | — (game không có zh-TW) | 0 |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 1.344 | — (không có zh) | — | 0 |
| Build 68 `resources.assets` (20.046.080 B) | 2.086 (== `en-build68.json`) | 2.086 | 2.086 | 0 |

Lưu ý mốc: số PB3 2.350 trong bảng P0-3 đã gồm 2 key rác `row_001064`/
`row_001065` từ 2 dòng trống trong `PB3_LOC_DEFAULT.csv` (nay bỏ hẳn) —
số đúng sau BUG#2 là 2.348. Số 9K zh 2.176 trước BUG#2 là langtable zh-TW
(2.150) + 26 key ST phẳng từ bảng zh; thiếu 76 key ST (điền "" từ vũ trụ
SharedTableData) và key ST chưa gộp nhóm `Collection/Key`.

## Lệnh đã chạy (mốc + kiểm tra)

```bash
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 run_tests.py --tier smoke
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 -m unittest discover -s tests -t .
cd exp && node verify_analysis_rebuild.mjs   # D1-D4 cần Playwright (scratch ngoài repo)
cd exp && node verify_delivery_gate.mjs      # P0-2, D1-D4 cần Playwright (scratch ngoài repo)
diff -rq -x __pycache__ -x logs -x reports -x workspace -x .pytest_cache <pristine> <cây>
```

## Quyết định đã chọn (1 dòng lý do mỗi quyết định)

- G1 thu hẹp: chỉ ghi TextAsset JSON dict, MB vào unmatched có đếm — vì không có nền tảng
  ghi MB an toàn + đã kiểm chứng được; viết mới từ đầu rủi ro hỏng file game.
- Precache xóa 13 wheel chết thay vì bổ sung wheel — vì đĩa chỉ có 1 wheel, danh sách cũ
  theo cây staging cũ, không theo head.
- D3 hoàn thiện driver (đuôi `.unity3d` + bấm modal) thay vì sửa app — vì cổng đuôi/modal
  là hành vi đúng đã có tiền lệ ở harness c4/reselect; assertion D3 không đổi.
- Đẩy P0-1 lên nhánh `auto/p0-1` thay vì main — vì là thay đổi rủi ro cao, cần CI/harness
  trên nhánh trước khi gộp; gộp chỉ khi test xanh.
- Tài liệu theo code, không sửa test cho vừa tài liệu — trừ đồng bộ kỳ vọng đếm/version
  đã lệch từ trước theo giá trị thật của cây (ghi rõ từng chỗ).
- Cổng FAIL chặn giao tuyệt đối, không đường giao khi cảnh báo — vì file giao sai hỏng
  game theo cách user khó phát hiện; cảnh báo không bao giờ chặn nhưng luôn hiện.
- File đã ép chỉ cần khớp nội dung + sha script, không cần byte-identical với mô phỏng
  — vì file ép bằng công cụ khác vẫn hợp lệ; khác bytes thì cảnh báo rõ.
- Font nhúng Unity không đọc được bytes thì "chưa kiểm được", không mặc định tốt/xấu
  — vì reader hiện tại không trích được bytes font; kết luận chắc chắn cần font chỉ định.
- Parser bảng ngôn ngữ fail-closed, không fallback âm thầm về quét phẳng — vì
  đoán sai layout gán nhầm ngôn ngữ cho hàng nghìn term mà không ai phát hiện.
- Pipeline gộp biến thể về mã gốc, xuất locales giữ đủ biến thể — vì bảng locale
  hiện có gộp biến thể và mốc §4 cần so được; độ trung thực biến thể nằm ở
  artifact locales.
- Đường quét phẳng chỉ còn cho object không phải bảng ngôn ngữ, ghi rõ lý do —
  vì xóa hẳn mất hàng nghìn chuỗi thật không có nguồn thay thế + phá test hiện có.
- File ảo bảng ngôn ngữ mang đoạn `.langtable` — vì tên cũ đụng file generic
  cùng đơn vị, dict ảo ghi đè làm mất bảng (lỗi thật, đã có test khóa).
- QUY TẮC BẮT BUỘC: MỌI lần đổi nội dung file precache đều phải tăng
  CACHE_VERSION + đồng bộ mọi kỳ vọng version, và tự kiểm tra mỗi mục phải
  grep xác nhận không còn pin version cũ — vì P0-3 quên bump khiến trình duyệt
  của user tiếp tục chạy code cũ dù main đã có code mới (sửa khẩn 2026-11-17).

## Đẩy code

- P0-1 + đợt sửa-lỗi: nhánh `auto/p0-1`, commit snapshot `41e98028d3c5daf8cbb49152c9d6405a93ef3872`
  (70 file text, từ head `3d0c80c`, sync-base đã kiểm khớp), PR #1, ĐÃ GỘP vào main
  (`9fb4a2edf004b10d97d2fc95d3fc08df6fec9a13`) 2026-11-17. CI trên main: job `assets` vẫn
  đỏ-từ-trước do Release `fixtures-v1` rỗng (xem "Chờ Joseph" #1); job `unit` tái hiện
  cục bộ xanh, nguyên nhân đỏ trên CI [CHƯA KIỂM CHỨNG].
- Gói nhị phân chưa lên repo: `handoff_p01.zip` (18 fixture/font) — Joseph đẩy theo
  `HUONG_DAN.txt` (iSH hoặc web). Nhánh `auto/p0-1` giữ nguyên, không xóa.
- P0-2: nhánh `auto/p0-2`, commit snapshot `982174fc9c7b8bc0ef2f7bfc86a4a52e44a74c74`
  (39 file text, từ head `37a1133f`, sync-base đã kiểm khớp), PR #2, ĐÃ GỘP vào main
  (`d93b79a3272d98b1580d88a7d5ff7dc83c45f17a`) 2026-11-17. Nhánh `auto/p0-2` giữ nguyên,
  không xóa. Không có gói nhị phân cho P0-2.
- P0-3: nhánh `auto/p0-3`, commit snapshot `f88934c542fecb050b21172e93b0ca66caee2912`
  (11 file text, từ head `9c66fc53`, sync-base đã kiểm khớp), PR #3, ĐÃ GỘP vào main
  (`fead921f06a63d07ece4ad1f567906e9c9337dbb`) 2026-11-17. Nhánh `auto/p0-3` giữ nguyên,
  không xóa. Dữ liệu trích từ game (deliver_p03_locales/) KHÔNG commit vào repo.
- Sửa khẩn cache: đẩy thẳng main (sửa nhỏ, khẩn, đã kiểm xanh), commit
  `88e077ef46bb60eaa9a5afdff590343ffaef35bb` (23 file, từ head `c642b4ad`)
  2026-11-17. Không qua nhánh/PR vì cần lên live ngay để SW mới kích hoạt.

## Backlog còn lại (thứ tự)

P0-4 (BỎ QUA, chờ Joseph — xem "Chờ Joseph" #5; quay lại khi có đủ ảnh) → P1-1 → P1-2 → P1-3 (I1) → P1-4 (I2) →
P1-5 (I3) → P2-1 → P2-2 → P2-3 → P2-4 → P3. Tiêu chí XONG từng mục theo brief mục 7.
