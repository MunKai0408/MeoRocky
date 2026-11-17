# Mục (E) — Ép bản dịch lại vào file gốc (P0-1, BearTea web)

Ngày: 2026-11-17. Harness: `exp/verify_analysis_rebuild.mjs` — **13/13 PASS** trên Chromium headless thật (lần chạy sau khi sửa lỗi D2 + hoàn thiện driver D3, log `~/workspace/beartea/work_p01/fix_d2/harness_run2_complete.log`; xem §3, §4d, §6).
Full suite Python: **1759 chạy: 1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua** (77,6s, `unittest discover`, log `~/workspace/beartea/work_p01/fix_d2/full_discover.log`); tập 28 đỏ trùng hệt baseline kiểm chứng, tất cả do 2 fixture thiếu đã biết (`zip/sample.ipa`, `sqlite_loc/a.sqlite`), không có test đỏ mới (xem §5).
Kết quả JSON: `exp/analysis_rebuild_results.json`, viết từ CÙNG lần chạy harness run 2 sau sửa D2 (không gộp kết quả cũ; JSON cũ run 4 đã thay bằng JSON run 2, sha256 `ac5bb3c7…`).

Nhãn trung thực dùng trong toàn báo cáo: **[ĐÃ ĐO THẬT]** = số đo trực tiếp trong lượt này; **[FIXTURE]** = fixture tự sinh, không phải game thật; **[HYPOTHESIS]** = suy luận chưa kiểm chứng. Mọi số đo trên game thật ghi rõ build.

## 1. Tính năng (E) và file thay đổi

(E) = nhóm bản dịch theo provenance → chọn bản thắng cho xung đột → 1 plan mỗi đơn vị → áp bằng executor hiện có (`plan_build`/`plan_apply` + `apply_patch`) + đường rebuild streaming mới cho bundle `.unity3d` độc lập lớn. Đường RAM hiện có giữ nguyên cho file nhỏ. **Không đóng gói lại IPA** (decision #70): output là đơn vị nhỏ nhất đã patch (bundle/.assets), nhiều đơn vị thì ZIP lại.

- Bridge op mới duy nhất: `rebuild_apply(path, translations_json, provenance_json=None, choices_json=None, dry_run=True, confirm=False)` trong `pb3/bridge.py`. Preview và apply là cùng 1 op với cờ `dry_run`; `dry_run=False` bắt buộc `confirm=True`. Response: `units[]`, `totals`, `unmatched[]` (key + reason `monobehaviour|not_found|not_json_dict|non_string_value`), `conflicts[]` + `conflicts_total`/`truncated`, `refused[]` (lỗi từng đơn vị, không fail cả đợt), `outputs`/`dest`/`zip_path` + `filename`, note "không phải IPA đóng gói lại". [ĐÃ ĐO THẬT: harness C1–C3, §3]
- Module mới `pb3/sdk/rebuild.py`: `group_by_unit()` (ưu tiên merge sidecar `beartea-merge-provenance/1` với `keys{}` + `conflicts[]`; translation sidecar `beartea-translation-provenance/1` chỉ 1 source → fallback; không provenance → nhóm "quét tất cả" + cờ cảnh báo), `resolve_conflict_choices()` (hàm thuần; thiếu → mặc định kept; key lạ → ValueError; chọn dropped khi bản dịch dịch từ kept → warning), `build_rebuild_preview()`/`apply_rebuild()` (điều phối plan-per-unit + chọn đường RAM/streaming + gom kết quả). Stdlib-only, Python 3.9. Xung đột dùng ĐÚNG conflicts của merge (`sdk/extraction.py` `merge_language_outcomes`, cap 500); không dùng `sdk/arbitration.py`; không strip hậu tố `#N`/`.p<path_id>`.
- Streaming G7 trong `sdk/unity.py`: `_read_bundle_layout(fh,...)` tách từ `stream_bundle_entries` (đường đọc/ghi chung 1 logic header/blocks-info/cờ mã hóa); `rebuild_bundle_streaming(src_path, dst_path, replacements, backend, limits, progress_fn, on_entry)` — pass 1 quét/định cỡ không giữ payload (từ chối sớm E_ENCRYPTED/v9/mã nén 4,>5; sha256 từng entry khi stream qua), pass 2 ghi 1 lần nén theo luồng (none ghi thẳng; LZ4/LZ4HC từng chunk 128KiB + fallback ghi thô khi nén dài hơn, y hệt `build_bundle`; LZMA = MỘT `LZMACompressor` FORMAT_RAW/FILTER_LZMA1 duy nhất suốt payload — đã qua round-trip [FIXTURE], xem §3 C4 và test LZMA; Zstd → none + warning), seek-patch blocks-info + total_size cuối, progress đơn điệu theo byte (total = input + payload mới). Pass 3 verify streaming: đọc output bằng `stream_bundle_entries`, kiểm tập entry + thứ tự + flags + hash entry không đổi + entry đã patch đọc lại được khớp after_sha (ở tầng plan); sai → E_VERIFY_FAILED, xóa file đích dở. Verify này yếu hơn diffverify toàn phần ở 1 điểm (không kiểm byte-layout ngoài entry bằng snapshot), bù bằng test `parse_bundle` trên fixture + round-trip. Streaming chỉ cho bundle/.assets ĐỘC LẬP; bundle trong ZIP/IPA giữ đường RAM. Entry đơn >128MB → E_TOO_LARGE + hướng iSH (`_rebuild_assets_section_to_file` KHÔNG triển khai — ghi rõ, không tuyên bố ép được `sharedassets0.assets` 154MB).
- Khóa REBUILD_ALL GIỮ NGUYÊN: `MATURITY="M2"`, `E_WRITE_DISABLED` trong `apply_patch` dù `confirm=True`; không đụng `build_serialized`. (E) chỉ PATCH_INPLACE/REBUILD_SECTION. [ĐÃ ĐO THẬT: test khóa an toàn trong `test_rebuild.py`]
- Mọi hàm ghi mới có chốt dest != source (realpath + samefile) + test cho chốt đó. 3 nghi ngờ thiếu chốt cũ (`containers.py:1304`, `patch_pack.py:708-709`, `compress.py:407`) KHÔNG sửa trong lượt này (phạm vi agent tiếp nối).
- UI (E): khu vực `<h3>Ép lại vào file gốc (E)</h3>` trong `#tab-mod` sau `#plan-result`; nút điều hướng ở `#tab-translate` cạnh nút tải provenance (chỉ chuyển tab). Nguồn bản dịch 3 lựa chọn (lastTranslated trong phiên / modJson đang mở / nhập file `.json` + sidecar). Re-select file gốc + cảnh báo nếu tên khác lúc trích xuất. Preview dry-run: bảng đơn vị/key sẽ đổi/không ép được (kèm lý do từng nhóm)/xung đột với radio chọn bản thắng + nút chọn tất cả. Nút ghi disabled tới khi preview xong; `confirm()` theo mẫu plan (file gốc không bị ghi đè; output không phải IPA; cần re-sign). Progress thật kind `rebuild` qua `_emit`/`progSet` hiện có. Nút "Trích lại file vừa ép để kiểm". Thông báo theo mã lỗi thật (E_ENCRYPTED, v9, Zstd, E_TOO_LARGE + hướng iSH, IPA khóa nói "có thể", E_VERIFY_FAILED). `confirm()` riêng cho file >400MB với câu chữ streaming thật (RAM ~2× file + entry lớn nhất). `state.rebuild` (preview/applied) reset khi đổi file, nhưng bản dịch + sidecar đã nhập (`state.rebuildFile`) được GIỮ — sửa lỗi D2, xem §4d. Dữ liệu game chỉ qua `esc()`/`textContent`. Flow trình duyệt đã kiểm trên Chromium thật 13/13 (§3, §6).
- Đếm file: `files.json` 93→94 (+`pb3/sdk/rebuild.py`, đúng vị trí bảng chữ cái); `tests.json` 164→165 (+`pb3/tests/test_rebuild.py`); `sw.js` CACHE_VERSION v19-20261116→v20-20261117, precache 131→119 (xem §2).

File đổi/mới do P0-1 (đối chiếu `diff -rq` với pristine, loại `__pycache__`/logs/reports/workspace + fixture Q1 + file Q2; danh sách đầy đủ trong `~/workspace/beartea/work_p01/diff_pristine_p01.txt`):
đổi: `README.md`, `HANDOVER.md` (chỉ đúng chỗ P0-1), `app.js`, `index.html`, `sw.js`, `pb3/bridge.py`, `pb3/files.json`, `pb3/tests.json`, `pb3/sdk/plan.py`, `pb3/sdk/unity.py`, `pb3/tests/test_plan.py`, 7 check script `exp/check_analysis_{c6,c7,c8,c9,font_unify}_static.py` + `exp/check_item{7,8}_static.py`, 37 harness `.mjs` cũ lệch so với pristine (phần lớn sửa đường dẫn Q2 + đồng bộ kỳ vọng đếm — đếm từ `diff -rq`: 37 dòng `differ`, file `~/workspace/beartea/work_p01/diff_pristine_p01.txt` liệt kê đủ; con số 15 trước đây đếm thiếu, đính chính tại đây);
mới: `pb3/sdk/rebuild.py`, `pb3/tests/test_rebuild.py` (32 test — 31 gốc P0-1 + 1 test 2b thêm trong đợt sửa-lỗi), `exp/check_analysis_rebuild_static.py`, `exp/verify_analysis_rebuild.mjs`, `exp/analysis_rebuild_results.json` (+ báo cáo này).
Đợt sửa-lỗi + sửa D2 sau đó chạm thêm: `pb3/docs/SUPPORT_MATRIX.md` (2 hàng Unity + dòng fixture v9 khớp generator), `exp/check_analysis_rebuild_static.py` (§9 chốt hồi quy D2), `app.js` (sửa D2, §4d) — diff đầy đủ trong `~/workspace/beartea/work_p01/fix_d2/diff_after.txt`.
Không đổi: `.github/workflows/`, iSH, các báo cáo `exp/` cũ, `deliver_section2/`, `deliver_audit/`, file gốc trong `real_games/`.

## 2. Bước 0 — vá innerHTML, quyết định precache, đính chính gzip

### 2a. Vá 2 lỗ innerHTML [ĐÃ ĐO THẬT]

Bằng chứng TRƯỚC (log `~/workspace/beartea/work_p01/p01_evidence_before.log`, 2026-11-17 02:16 UTC, `app.js` khi đó byte-identical với pristine):

```
1804: html += ... + esc(f.source || f.path) + ...        (đã escape đúng)
1922: (f.source ? " ← " + f.source : "") + "</option>";  (KHÔNG escape)
2647: (f.source ? " ← " + f.source : "") + "</option>";  (KHÔNG escape)
4160: html += "<p>Nguồn: " + esc(f.source || "?") + ...  (đã escape đúng)
```

Bằng chứng SAU (cùng lệnh grep, sau khi vá):

```
1806: ... + esc(f.source || f.path) + ...
1924: (f.source ? " ← " + esc(f.source) : "") + "</option>";
2649: (f.source ? " ← " + esc(f.source) : "") + "</option>";
4438: html += "<p>Nguồn: " + esc(f.source || "?") + ...
```
(đo lại sau sửa D2, 2026-11-17: chỗ thứ 4 nay ở dòng 4461 do thêm code (E), nội dung như cũ; 3 chỗ đầu vẫn 1806/1924/2649.)

Cả 4 chỗ dùng `f.source` trong `app.js` nay đều qua `esc()`; không còn mẫu `+ f.source` không esc. Quét thêm: code render (E) mới dùng `esc()` cho mọi dữ liệu game/bản dịch (chỉ tổng số đếm nội suy trực tiếp, đã quét). Assertion tĩnh trong `exp/check_analysis_rebuild_static.py` ngăn tái phạm (kiểm `esc(f.source` ≥3, không còn dòng `+ f.source`/`f.source +` không esc, render (E) dùng `esc(u.unit)`/`esc(m.key)`/`esc(c.key)`). [ĐÃ ĐO THẬT: check tĩnh exit 0]

### 2b. Precache: xóa 13 wheel chết [ĐÃ ĐO THẬT]

Trước: precache 131 mục, liệt kê 14 wheel nhưng trên đĩa chỉ có 1 wheel (lz4). 13 wheel chết (đã kiểm từng tên trong log trước): atomicwrites-1.4.1, attrs-26.1.0, exceptiongroup-1.3.1, iniconfig-2.3.0, micropip-0.11.1, more_itertools-11.0.1, pluggy-1.6.0, py-1.11.0, pygments-2.20.0, pyparsing-3.3.2, pytest-9.0.2, setuptools-82.0.1, six-1.17.0 (đều dưới `pyodide/`).
Quyết định theo bằng chứng: XÓA 13 mục chết, vì `check_item7_static.py` khẳng định tập precache == files.json ∪ shell ∪ danh sách thư mục pyodide, và `verify_item6.mjs` A3 tính kỳ vọng từ đĩa động — xóa xong cả hai khớp.
Sau: precache 119 mục = 131 − 13 + 1 (`pb3/sdk/rebuild.py`); kiểm tra: precache == files.json ∪ shell ∪ danh sách đĩa CHÍNH XÁC (0 thiếu, 0 thừa), 0 mục chết; tổng precache thật 16086906 B = 15,34 MiB. Lưu ý: comment "~18.5MB" trong `sw.js` là có từ trước, để nguyên — số thật đo được là 15,34 MiB, chênh lệch ghi rõ ở đây cho agent tiếp nối, không sửa lan man.
Đồng bộ kỳ vọng đếm cũ lệch từ trước (theo hướng dẫn Q2, ghi rõ từng chỗ là "đồng bộ kỳ vọng cũ theo giá trị thật hiện tại của cây", không đổi assertion hành vi): c5 ghim precache `===127` (thực tế trước P0-1 là 131) → `===119`, key kiểm đổi tên `swPrecacheCount127`→`swPrecacheCount119`; c3/c4/c5 ghim `CACHE_VERSION v11` (thực tế v19) → v20. Dòng lịch sử trong c5 (mô tả lần đổi 126→127 của chính nó) và text lịch sử c3/c4 giữ nguyên có chủ ý. Bằng chứng trước/sau từng file: `~/workspace/beartea/work_p01/harness_sync_evidence_p01.log` (tái tạo từ diff với pristine vì log sync gốc bị ghi đè khi chạy lại script — ghi rõ).
Sự cố trong lượt: harness `verify_phase1_ci.mjs` (mục `pyodide_pytest_smoke`) đã tải 13 wheel pytest vào `pyodide/` lúc 02:52 UTC 2026-11-17, làm `check_item7_static.py` fail tạm thời ("precache lệch"). 13 file tải về KHÔNG thuộc repo (pristine không có); đã xóa, chỉ giữ wheel lz4 gốc, check lại exit 0. Ghi rõ để lần chạy harness sau không nhầm là hồi quy.

### 2c. Đính chính ngưỡng gzip [ĐÃ ĐO THẬT]

Ngưỡng gzip đã chốt 102400B (`verify_item6.mjs:86,95` = 100×1024). Harness mới + báo cáo này dùng đúng 102400B. **Đính chính: nhãn "(gzip đạt)" ở báo cáo (B)/(C)/(D) là SAI** — số đo thật lần tải đầu sau sửa D2 (harness mục S, run 2): raw 435379 B (425,2KB), gzip 124243 B (121,3KB) — CẢ HAI đều trên ngưỡng 102400B (raw CHƯA đạt, gzip CHƯA đạt). Chi tiết từng file: index.html 66689/18802, kb-store.js 8192/3212, worker-pool.js 14840/5227, stream.js 18358/6141, opfs.js 12753/4045, app.js 280168/77217, gt.js 34379/9599 (raw/gzip, byte). P0-1 thêm code UI (E) nên app.js tăng so với trước; sửa D2 thêm 1649B nữa (278519→280168) — kết luận ngưỡng không đổi. 3 báo cáo cũ KHÔNG sửa (lịch sử).

## 3. Kết quả harness `verify_analysis_rebuild.mjs` (run 2 sau sửa D2, 2026-11-17)

| Mục | Kết quả | Bằng chứng số |
|---|---|---|
| A1 `static_node_py_check` | PASS (0,5s) | `node --check` app.js/sw.js/harness mới; `py_compile` bridge.py, sdk/rebuild.py, sdk/unity.py, sdk/plan.py, tests/test_rebuild.py |
| A2 `static_rebuild_checks` | PASS (0,1s) | check tĩnh exit 0: rebuild.py stdlib-only/Py3.9/không UnityPy/lz4/không expose rebuild_zip/chốt dest!=source; files 94/tests 165; precache 119 có sdk/rebuild.py, không wheel chết, CACHE v20; bridge có rebuild_apply + gate; plan.py progress_fn; unity.py streaming + khóa REBUILD_ALL; đủ id UI (E); 2 chỗ innerHTML đã vá |
| B1 `python_unit_rebuild_plan_unity` | PASS (7,3s) | Ran 157 tests OK (test_rebuild 32 + test_plan 19 + test_unity 106) |
| C1 `preview_scan_all` | PASS (0,5s) | Preview không provenance: 1 đơn vị, 8 changes, refused=0, không ghi file (dest/zip_path false), có cảnh báo quét-tất-cả |
| C2 `conflict_choices` | PASS | Mặc định kept, warning=false; chọn dropped → choice=dropped + warning "chọn bản dropped nhưng bản dịch dịch từ bản kept"; key lạ → ok=false |
| C3 `apply_roundtrip_confirm_gate` | PASS (0,2s) | dry_run=False + confirm=False → E_CONFIG_INVALID; apply ghi dest đúng tên; round-trip en.hello/menu.start/dialog.greeting khớp; sha file gốc sau apply khớp trước; note có "KHONG phai IPA" |
| C4 `streaming_large_bundle` | PASS (4,2s) | Bundle tự sinh 68158149 B (≥64MiB, 1 CAB TextAsset + 13 filler 5MiB): streaming=true, size_before=68158149, size_after=68158173, apply 2,5s, round-trip khớp [FIXTURE] |
| C5 `refusals_v9_encrypted` | PASS (0,1s) | v9 → refused E_UNSUPPORTED_FORMAT; mã hóa (flag 0x240) → refused E_ENCRYPTED; fixture Zstd không sinh được (thiếu gói `zstandard`) — Zstd→none+warning chỉ kiểm ở unit test (fake bridge) [FIXTURE] |
| D1 `browser_rebuild_preview_apply` | PASS (7,5s) | Nút Ghi tắt trước preview, bật sau preview; preview XEM TRƯỚC 8/8 key, apply ĐÃ GHI, download nhận được |
| D2 `browser_rebuild_reselect_mismatch` | PASS (0,1s) | Sau sửa D2: chọn lại `ten-khac.zip` → `rebuild-file-info` hiện CẢNH BÁO khác tên + confirm khác tên hiện, preview XEM TRƯỚC chạy |
| D3 `browser_rebuild_over_400mb_confirm` | PASS (15,9s) | File 401MB → confirm khác tên + confirm streaming/RAM (~802MB) hiện khi preview (driver D3 hoàn thiện theo tiền lệ c4/reselect, xem §4d; assertion không đổi) |
| D4 `browser_390_no_overflow_no_errors` | PASS (0s) | scrollWidth=390=clientWidth; không pageerror/console-error thật |
| S `shell_size_after_rebuild` | PASS (0,1s) | Số đo §2c (raw 435379B/gzip 124243B); ngưỡng 102400B; cả raw và gzip đều CHƯA đạt |

Tổng: 13 mục, 13 PASS, 0 FAIL. JSON `exp/analysis_rebuild_results.json` đúng định dạng `{"total","failures":[],"results":{name:{ok,detail,seconds}}}`.

Test `pb3/tests/test_rebuild.py` (32 test = 31 gốc P0-1 + 1 test 2b thêm trong đợt sửa-lỗi, chỉ dùng fixture builder sẵn có, không file fixture rời, không marker `needs_fixture` — `phase1_ci` markers_count 122 không đổi): grouping/provenance 6, choices 4, plan-per-unit 4, streaming 9 (LZ4 nhiều entry + entry ~5MB, none, LZMA single-compressor, v9 từ chối, mã hóa từ chối, Zstd-flag→none+warning qua fake bridge, blocks_info_at_end, progress đơn điệu + on_entry, chốt dest==source), round-trip + an toàn 8 (gồm test 2b: bundle mã hóa trong ZIP ở bước quét-tất-cả → refused; và 2 hồi quy: entry none-block >64MB streaming; v9/mã hóa độc lập → refused), phân loại MB 1 (key MB ở đơn vị không có plan → `monobehaviour`, hồi quy cho lỗi §4c).

## 4. Bốn lỗi thật tìm được (đều đã sửa + có test/chốt)

### 4a. Executor plan.py thay theo giá trị trần — giá trị rỗng không bao giờ patch được (ĐỘ LỆCH KHỎI BRIEF, BÁO RÕ)

Brief gốc dặn `plan.py`: "chỉ thêm tham số tùy chọn progress_fn". Trong vòng tròn chân B, executor hiện có đã **thất bại trên dữ liệu game thật**: asset `BuildSettings` của 9 Kings có key `Version` với giá trị rỗng `""`; `_apply_entry_changes` truyền `old_text=ch["before"]` (giá trị trần) vào `apply_patch`, mà `apply_patch` đếm `script_b.count(old_b) if old_b else 0` → 0 occurrence → E_CONFIG_INVALID, từ chối cả đơn vị, 3 key đều `not_found`. Cùng cơ chế này còn patch nhầm chỗ khi giá trị `before` xuất hiện ở vị trí khác trong script trước (chỉ bị bắt bởi verify after_sha → E_VERIFY_FAILED).
Sửa (vượt brief gốc, báo rõ ở đây + trong báo cáo giao việc): `_scan_top_level_spans`/`_top_level_value_spans` quét object JSON cấp cao nhất bằng `json.JSONDecoder().raw_decode`, ghi span của literal giá trị từng member; `_simulate_script` mới thay theo KEY (chọn lần xuất hiện CUỐI có giá trị giải mã == `before`, giống ngữ nghĩa `json.loads` với key trùng), giữ nguyên mọi byte khác; `build_plan` bắt ValueError từ simulation → đưa asset vào `skipped` kèm lý do thay vì crash cả plan; `_apply_entry_changes` mới: mô phỏng 1 lần rồi patch TOÀN BỘ script bằng 1 lần `apply_patch` (old = script gốc, new = script đã mô phỏng) — byte kết quả đúng after_sha theo định nghĩa; giữ nguyên khối verify after_sha + dict và `progress_fn`. Không đổi schema plan, không đổi khóa REBUILD_ALL, không đụng `mod_patch`.
Bằng chứng trước/sau: trước — dry-run trên bản copy `resources.assets` trả `units=[]`, `refused=[E_CONFIG_INVALID "không tìm thấy chuỗi cũ trong TextAsset 'BuildSettings'"]`; sau — cùng lệnh trả 1 đơn vị, 5 changes, 0 unmatched, 0 refused (vòng tròn chân B §5). Test mới trong `test_plan.py` (5 test, class `TestKeyAnchoredApply`): giá trị rỗng, giá trị trùng nhau giữa các key, giá trị xuất hiện trước ở chỗ khác, key JSON trùng (lần cuối thắng), giữ nguyên indent/escape. Điểm mở liên quan: logic giá trị trần tương tự trong `sdk/patch_pack.py` (C7) có cùng lỗi tiềm ẩn — NGOÀI phạm vi, không đụng, ghi rõ cho agent tiếp nối.

### 4b. Streaming enumeration đọc toàn bộ payload vào RAM + nuốt lỗi entry

Harness run 1 tìm ra 2 lỗi trong `sdk/rebuild.py`, đã sửa trước run 2: (1) `_sf_entry_names` nuốt mọi lỗi từng entry → bundle v9/mã hóa độc lập trả ok:true với units=[] thay vì refused — sửa bằng cách tách lỗi `get_entry` khỏi lỗi `parse_serialized`, raise lỗi `get_entry` đầu tiên khi không entry nào parse được; `_enumerate_units` nhánh bundle độc lập raise E_INPUT_NOT_FOUND khi bundle không cho entry SerializedFile nào. (2) `_apply_streaming` dùng `_enumerate_units` → `parse_bundle` (giữ toàn bộ payload) + `build_plan` trên toàn bộ bytes → bundle 68MB single-block fail E_TOO_LARGE trước khi streaming bắt đầu — sửa bằng pre-check layout qua `_read_bundle_layout` (từ chối sớm mã hóa/định dạng xấu/entry >128MB), enumeration streaming qua `stream_bundle_entries` với giới hạn nâng (`max_entry_bytes` = 128MB cho kiểm tra block/entry, vì 1 block chưa nén có thể >64MB; đường RAM giữ 64MB), plan từng entry, patch từng entry, verify streaming với cùng giới hạn nâng. Khoảng trống còn lại: bundle mã hóa NẰM TRONG ZIP vẫn bị bỏ qua im lặng ở bước liệt kê quét-tất-cả (`except PB3Error: continue` nhánh ZIP); bundle mã hóa được provenance chỉ tới thì vẫn cho refused — ghi rõ, xem §7.

### 4c. Phân loại key MB sai: `not_found` thay vì `monobehaviour`

Vòng tròn chân A2 trên IPA 9 Kings đầy đủ tìm ra 2 lỗi phân loại, đã sửa: (1) SF chỉ thu thập cho đơn vị CÓ plan → key MB ở đơn vị không có TextAsset JSON bị gán `not_found` — sửa: vòng 1 thu SF cho MỌI đơn vị đã mở rộng (test hồi quy `TestMBClassification`). (2) Term StringTable cần join SharedTable ở bundle KHÁC (9 Kings: shared_table ở `localization-assets-shared` bundle, string table ở `localization-string-tables` bundle) nhưng nhóm provenance chỉ mở rộng đơn vị được tham chiếu → 98 key StringTable bị `not_found` — sửa: với file ZIP/IPA, gom thêm SF của TẤT CẢ đơn vị đã liệt kê trước bước phân loại; `_unit_sf` → `_unit_sfs` (bundle trần không `::entry` đọc tất cả entry). Bằng chứng: trước sửa, histogram đầy đủ 13465 key = 13366 monobehaviour + 98 not_found + 1 non_string_value; sau sửa = **13464 monobehaviour + 1 non_string_value** (`Kings` — key này đồng thời có trong 1 dict với giá trị không phải chuỗi nên theo thứ tự ưu tiên phân loại dict-trước thì `non_string_value` là đúng quy tắc). Chi phí: quét MB trên tất cả đơn vị (tương đương 1 lần trích xuất; chân A2 rebuild 9,0s cho IPA 173MB [ĐÃ ĐO THẬT]).

### 4d. Chọn lại file xóa bản dịch + sidecar đã nhập (lỗi D2) + driver D3 thiếu (sửa-lỗi 2026-11-17, sau kiểm chứng độc lập)

Kiểm chứng độc lập tái hiện 3 lần: harness mục D2 timeout 120s tất định, không phải flake. Nguyên nhân: `setCurrentGameFile()` (`app.js:1298`) gọi `rebuildResetState()` vô điều kiện → `state.rebuildFile = null`, xóa bản dịch + sidecar đã nhập; luồng D2 (nhập sidecar → chọn lại file tên khác → preview) luôn rơi vào lỗi "Chưa nhập file .json nào" và không bao giờ vẽ XEM TRƯỚC, nên cảnh báo + confirm "khác tên lúc trích xuất" không bao giờ kích hoạt được ở chế độ nhập file. Fail-closed (không ghi gì) nhưng tính năng reselect + cảnh báo của (E) không hoạt động ở chế độ này.
Sửa (tối thiểu, đúng chỗ): `rebuildResetState()` (`app.js:3070`) chỉ hủy preview/applied/fileKey + tắt nút Ghi, GIỮ `state.rebuildFile`; dòng mô tả nguồn được giữ nguyên khi đang ở chế độ nhập file và còn dữ liệu đã nhập; `refreshRebuildFileInfo()` (`app.js:3116`) qua `rebuildEffectiveProvenance()` (`app.js:3110`) để cảnh báo khác tên hiện ngay sau khi chọn lại file (dùng sidecar đã nhập khi chưa chạy lần nào sau khi đổi file). Lý do giữ dữ liệu: dữ liệu nhập là đầu vào của user, độc lập với file game (2 nguồn còn lại `lastTranslated`/`modJson` cũng không bị xóa khi đổi file); an toàn vẫn giữ bằng preview hết hạn + nút Ghi tắt tới khi xem trước lại + confirm khác tên — xóa đầu vào không tăng thêm an toàn. Chốt hồi quy tĩnh trong `exp/check_analysis_rebuild_static.py` (§9: thân `rebuildResetState` không được gán `state.rebuildFile = null`, và phải có `rebuildEffectiveProvenance`).
D3 lộ thêm 2 khiếm khuyết LÁI HARNESS (không phải lỗi app): file test đuôi `.bin` bị cổng đuôi từ chối trước khi đọc, và harness không bấm "Tiếp tục" ở modal xác nhận file lớn (C4) — cả hai đều là hành vi đúng của app, đã kiểm ở harness c4/reselect. Driver D3 hoàn thiện theo đúng tiền lệ đó (đuôi `.unity3d` + chờ modal rồi bấm `#large-file-continue`); assertion của D3 không đổi. Bằng chứng: lần chạy nguyên trạng `~/workspace/beartea/work_p01/fix_d2/harness_run1_asis.log` (D1/D2 PASS, D3 timeout 180s tại dòng 486) → lần chạy hoàn tất `harness_run2_complete.log` (13/13 PASS). JSON tái sinh từ lần chạy 2.

## 5. Hồi quy + vòng tròn trên game thật [ĐÃ ĐO THẬT]

Chạy bằng CPython qua bridge (locales en+zh, merged + split, `bridge.WEB_ROOT` patch về cây làm việc để đủ 20/20 plugin — `boot()` mặc định dùng `/pb3web` của Pyodide nên 0 plugin nếu không patch; lần chạy đầu 0 plugin đã loại bỏ). Log đầy đủ: `~/workspace/beartea/work_p01/regression_p01.log` (+ `regression/*/summary.json`, log quét, `regression/circle/circle.json`). RAM đỉnh đo bằng VmHWM (`/proc/<pid>/status`, script `run_measured.py`; không có `/usr/bin/time`). File gốc không bị sửa; mọi patch thử trên COPY.

File game (đã kiểm lại size+md5 trước khi dùng, đều khớp): 9 Kings v1.0.6 173472485 B md5 `4f53043c…`; DungeonWithin 0.6.3 91389555 B md5 `2940f75a…` (chỉ probe); Poly Bridge 3 v1.1.7 482039645 B md5 `065efc5f…`; Exploding Kittens 2 v0.0.18 290547090 B md5 `ba98dd3d…`. File `data.unity3d` 590MB trong brief KHÔNG được cung cấp — không tuyên bố gì về file đó. Cảnh báo build: 9 Kings ở đây là v1.0.6, khác cả mốc v1.0.5 (141865757 B, iSH) và mốc cần build 1.0.8 mới có khe tiếng Anh trong brief — mọi số đo ghi rõ build này.

### 5a. Bảng brief §4 chạy lại — KHỚP CHÍNH XÁC cả 3 game

| Game (build) | en keys / xung đột | zh keys / xung đột | #N | Gộp khớp tách? | Thời gian merged/split | RAM đỉnh |
|---|---|---|---|---|---|---|
| 9 Kings v1.0.6 | 13465 / 17 | 1914 / 0 | 0 | Có (keys+thứ tự+values+conflicts khớp 100% khi replay) | 9,2s / 9,0s | 112,7 MiB |
| Poly Bridge 3 v1.1.7 | 14556 / 293 | 2570 / 224 | 0 | Có | 12,9s / 11,9s | 247,3 MiB |
| Exploding Kittens 2 v0.0.18 | 17240 / 2 | 7 / 0 | 0 | Có | 8,2s / 7,5s | 168,2 MiB |

Chi tiết: 9K entries_before 13501/1914, duplicates_identical 19/0, sources 8/2, total_entries 41074, skipped 141/141, errors []. PB3 entries_before 17010/4389, dups 2161/1595, sources 57/2, total_entries 32975, skipped 244/235, errors []. EK2 entries_before 17245/7, dups 3/0, sources 3/1, total_entries 39640, skipped 483/483, errors []. Mốc brief §4 khớp chính xác → các mốc đó được đo trên đúng 3 build này. Mốc `data.unity3d` (en 4963/zh 1, 308 xung đột) và Passpartout (en 2710/zh 1715) không so được — file không được cung cấp. Không trích dẫn con số "batch 2 game 24,6s" của C9 (lời kể, [CHƯA KIỂM CHỨNG]).

### 5b. Đếm key theo loại — (E) v1 phủ 0% key ngôn ngữ thật (G1)

Phương pháp: replay merge từ split outcomes theo đúng thứ tự pipeline bridge trả về (xuất hiện đầu thắng, trùng giống hệt đếm dup, khác nhau = xung đột), đối chiếu file gộp; phân loại key thắng theo đường dẫn nguồn: `.monobehaviour.` → mb_generic; đường dẫn trong tập parse từ log `-> StringTable... (xuất VPATH)` → mb_table; chứa `::` → textasset; còn lại → other.

| Game | TextAsset JSON dict | mb_generic | mb_table | other |
|---|---|---|---|---|
| 9 Kings (en+zh) | 0 | 15254 | 125 | 0 |
| Poly Bridge 3 (en+zh) | 0 | 12426 | 0 | 4700 (CSV) |
| Exploding Kittens 2 (en+zh) | 0 | 17247 | 0 | 0 |

Quét bundle chỉ-đọc: 9 Kings 7 bundle UnityFS (đều ≤5277 B, v8 lz4hc, chỉ MB, 0 TextAsset) + 12 `.assets`; `resources.assets` 25558592 B có 10 TextAssets, 2 dict JSON toàn chuỗi (Version, BillingMode — CONFIG, không phải ngôn ngữ). PB3 297 bundle, tất cả đọc được v8 lz4hc, 0 TextAsset trong mọi bundle, 0 JSON-dict; `resources.assets` 59285312 B có 5 TextAssets, 1 JSON-dict (BillingMode). EK2 100 bundle, TẤT CẢ E_ENCRYPTED khi đọc layout (chỉ probe, không giải mã); `resources.assets` 1832616 B có 35 TextAssets, 1 JSON-dict (BillingMode). **Tổng số JSON-dict TextAsset trong bundle: 0 ở cả 3 game.**
"Other" của PB3 = file CSV rời `Payload/ipa.store.app/Data/Raw/Localization/PB3_LOC_DEFAULT.csv` (plugin csv_table, 2350 key gộp mỗi locale) — ngoài phạm vi (E) v1 (không có CSV writer).
**Kết luận G1 [ĐÃ ĐO THẬT]: (E) v1 (chỉ ghi TextAsset JSON dict) phủ 0 trong số key ngôn ngữ en/zh trích được ở cả 3 game; 100% là MonoBehaviour (hoặc CSV ở PB3).** Đánh giá `apply_edits` + `services/language_tool.py` trước khi code: không có đường ghi MonoBehaviour an toàn + kiểm chứng được (không field writer, không verify tương đương diffverify) nên đã THU HẸP PHẠM VI CÓ GHI RÕ: mọi key MonoBehaviour/StringTable vào unmatched với reason `monobehaviour`, đếm rõ, không im lặng bỏ, không tuyên bố đủ tiêu chí vòng tròn cho nhóm key này. Không viết writer MonoBehaviour mới (rủi ro corrupt cao, typetree/alignment).

### 5c. Vòng tròn

- **Chân A (pipeline, bundle lớn nhất có thật):** PB3 `.../themes/newdesert/newdesert.asset_6cfa3f604c220ba4b052c85542a7563d.bundle`, 22525697 B, v8 lz4hc, flags 0x243, 2 entry, 37 MB item, 0 TextAsset. Trích có phạm vi (en+zh, gộp) → decision `none`, 0 entry, 0 file — bundle này không có text trích được nên không có gì để ép; sha bản copy không đổi. Ghi rõ: chân A không chứng minh được gì ngoài việc bundle lớn nhất không chứa text.
- **Chân A2 (toàn IPA 9 Kings v1.0.6, 173472485 B):** trích gộp en → 13465 key + sidecar thật → giả dịch tiền tố `[VI]` (deterministic marker, KHÔNG phải dịch thật) → `rebuild_apply` trên COPY toàn IPA (dry_run=False, confirm=True) → **0 đơn vị, 0 changed, 13465 unmatched (13464 monobehaviour + 1 non_string_value, đếm đủ bằng cap nâng — response bridge cắt ở 2000), 0 refused, không sinh dest/zip, sha copy không đổi**, rebuild 9,0s, tổng chân 22,0s. Vòng tròn KHÔNG khép cho key MB (đúng phạm vi v1 đã thu hẹp).
- **Chân B (bytes thật):** 9 Kings `resources.assets` 25558592 B, 19754 object, 10 TextAssets. 3 key dịch từ 2 dict all-string (Version: Version/CreatedAt; BillingMode: androidStore — CONFIG, không phải ngôn ngữ, nói thẳng). `rebuild_apply` trên COPY với translation sidecar → 1 đơn vị, 4 entry, 5 changes (key `Version` có ở 3 asset: Version, Time_Wise_Amplifier_and_Troops_Glitch_Test, BuildSettings — gồm cả giá trị rỗng `""` ở BuildSettings, trường hợp §4a), 0 unmatched, 0 refused, dest 25479784 B. Đọc lại: dict khớp 100% + thứ tự key giữ nguyên; **chỉ đúng 4 object kỳ vọng đổi hash (pid 565/566/571/572), 0 object thiếu/thừa, nội dung 4 asset đổi đều đúng (key dịch = giá trị mới, key khác giữ nguyên), TextAsset không-phải-dict giữ nguyên script**, sha copy không đổi, rebuild 9,8s. Tổng cả 3 chân: wall 34,1s, RAM đỉnh 538,0 MiB (CPython, đo VmHWM).
- **DungeonWithin 0.6.3 (chỉ probe, không extract/ép/giải mã):** 78 member ZIP, 9 đơn vị Unity. 4 bundle Addressables đều bắt đầu bằng 16 byte giống hệt nhau `030b6458b63fc434d5eb687dbd36dd60`, không có magic UnityFS/UnityRaw ở bất kỳ vị trí nào → dữ liệu đã mã hóa/bọc ngoài (khớp mô tả AES-encrypted Addressables); bundle lớn nhất `defaultlocalgroup_assets_all` 23203144 B. 5 file `.assets` parse được (v22): resources.assets 2562 object, globalgamemanagers.assets 4172 object, sharedassets0.assets 260 object. Không tuyên bố ép/ghi được file này.

### 5d. Full suite + harness hồi quy

Full suite (lệnh `python3 -m unittest discover -s tests -t .`, TMPDIR home; đo lại sau sửa D2, log `~/workspace/beartea/work_p01/fix_d2/full_discover.log`): **Ran 1759 test (77,6s): 1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua**. Tập 28 đỏ trùng hệt từng tên với baseline kiểm chứng độc lập, tất cả do 2 fixture thiếu đã biết (`zip/sample.ipa`, `sqlite_loc/a.sqlite` — `FileNotFoundError` trong log); không có test đỏ mới. (Lượt P0-1: 1751 chạy, 1721 đạt, log `p01_full.log`.) Smoke: 59/59 OK (0,5s, log `fix_d2/smoke.log`). Test đích danh `test_rebuild + test_plan + test_patch_pack`: 74 OK (log `fix_d2/targeted_rebuild_plan_patchpack.log`).
Harness hồi quy chạy lại sau P0-1 (log `~/workspace/beartea/work_p01/harness_*_p01.log`): C7 static + unit PASS (38 test gồm test_plan mới), sau đó crash ở import top-level `playwright-core` (thiếu gói — môi trường, có từ trước); item8 static PASS + `python_test_plan` PASS (19 test), `fixture_regenerate` FAIL do `build_item8_fixture.py` thiếu PYTHONPATH (ModuleNotFoundError `build_fixtures` — lỗi gọi harness có từ trước, không do P0-1), sau đó crash ở import `playwright-core`; C4 4/5 PASS, browser FAIL (thiếu Playwright); phase1_ci 6/8 PASS (`markers_count` 122 không đổi), `date_test_after_touch` FAIL (thiếu pytest), `pyodide_pytest_smoke` FAIL (thiếu fixture `sqlite_loc/a.sqlite`). Không có hồi quy nào do code P0-1. Sau sửa D2: `verify_item6.mjs` 14/16 (2 fail ngưỡng shell như trước), `verify_item7.mjs` 10/11 (1 fail `python_tmp_fixture_match` có từ trước) — không đỏ mới; check tĩnh `check_analysis_rebuild_static` + `check_item7_static` + `check_item8_static` đều exit 0; log `fix_d2/harness_item6.log`, `fix_d2/harness_item7.log`; file kết quả item6/item7 đã khôi phục byte-identical sau khi chạy.
Lưu ý Q1: script sinh fixture đã ghi đè `nested.ipa`/`nested_v8.ipa` khác byte so với pristine — GIỮ NGUYÊN, không "khôi phục".

## 6. Điểm chưa đạt + bước thử Safari/iSH cho user

- **Flow trình duyệt (E) = [ĐÃ ĐO THẬT] trên Chromium headless shell** (390px, Playwright 1.49.1): D1 preview/apply, D2 reselect + cảnh báo khác tên, D3 confirm streaming file 401MB, D4 không tràn/không lỗi trang — 13/13 PASS, log `fix_d2/harness_run2_complete.log`. Safari thật / RAM Safari vẫn [CHƯA KIỂM CHỨNG] — bước thử Safari (iPad/iPhone, Safari thật) để xác nhận trên thiết bị thật:
  1. Mở bản web đã deploy từ cây này (CACHE v20). Mở DevTools qua Mac (Safari → Develop → [thiết bị] → trang web) để xem console + Network.
  2. Tải file `9_Kings_v1.0.6_Decrypted.ipa` (173472485 B, md5 `4f53043c57b2ceac8590770a4c3aed07`) vào tab trích xuất, trích en gộp, tải `en.json` + `en.provenance.json`.
  3. Sang tab Mod → mục (E): chọn nguồn "nhập file", nhập 2 file vừa tải, bấm Preview. ĐẠT nếu: bảng hiện 0 đơn vị đổi, 13465 key unmatched với lý do `monobehaviour` (trừ 1), không lỗi console, nút Ghi bật sau preview. Bấm Ghi → ĐẠT nếu: confirm hiện đúng câu chữ, sau ghi báo 0 đơn vị/không có file tải xuống (vì không có gì ép được), file gốc không đổi.
  4. Thử đường TextAsset: dùng file `resources.assets` (lấy từ IPA 9 Kings, 25558592 B) + bản dịch 3 key (Version/CreatedAt/androidStore) + translation sidecar `{"format":"beartea-translation-provenance/1","source":"resources.assets"}`. ĐẠT nếu: preview 1 đơn vị/5 changes; ghi xong tải được `resources.assets` ~25,5MB; trích lại file vừa ép (nút "Trích lại") thấy 3 key đã đổi.
  5. Thử >400MB: tải PB3 IPA (482039645 B) → ĐẠT nếu confirm streaming hiện trước khi xử lý, ghi rõ RAM ~2× file + entry lớn nhất.
  6. RAM Safari: đo bằng Xcode Instruments (Allocations) khi chạy trên thiết bị thật, hoặc Safari Develop → Timelines (Memory) trên Mac; ngưỡng: không crash tab, RAM đỉnh < ~1,5GB cho file 173MB là chấp nhận được [HYPOTHESIS — chưa có số đo Safari thật, không đặt ngưỡng PASS giả]. Ghi số đo thật vào báo cáo tiếp theo.
- **RAM Safari thật = [CHƯA KIỂM CHỨNG]** (không có trong sandbox). Số RAM trong báo cáo này là RAM tiến trình CPython, không phải Safari.
- **iSH:** entry đơn >128MB và bundle LZMA lớn (nếu round-trip LZMA thất bại trong tương lai) → E_TOO_LARGE + hướng iSH. Chưa có bước thử iSH trong lượt này (ngoài phạm vi; không chạm iSH theo brief).
- **Zstd:** fixture Zstd không sinh được trong harness (thiếu gói `zstandard`); Zstd→none+warning chỉ kiểm ở unit test qua fake bridge [FIXTURE]. Bundle Zstd thật chưa thử.
- **G1:** không ghi MonoBehaviour/StringTable/CSV (xem §5b). Vòng tròn chỉ khép cho TextAsset JSON dict.
- **Bundle mã hóa trong ZIP:** bị bỏ qua im lặng ở bước liệt kê quét-tất-cả (§4b). Bundle mã hóa được provenance chỉ tới thì refused đúng.
- **`sdk/patch_pack.py`:** logic giá trị trần có cùng lỗi tiềm ẩn như §4a — ngoài phạm vi, để agent tiếp nối.
- **Comment "~18.5MB" trong `sw.js`:** số thật 15,34 MiB (§2b) — để agent tiếp nối sửa cùng các mâu thuẫn tài liệu còn lại (N1–N15/M1–M25 ngoài phạm vi P0-1).

## 7. Tiêu chí P0-1

| Tiêu chí | Kết quả |
|---|---|
| Bridge op `rebuild_apply` duy nhất, dry-run mặc định, confirm gate | ĐẠT [ĐÃ ĐO THẬT] |
| Nhóm theo provenance + xung đột đúng schema merge, cap 500 | ĐẠT [ĐÃ ĐO THẬT] |
| Plan-per-unit qua executor hiện có, khóa REBUILD_ALL giữ nguyên | ĐẠT [ĐÃ ĐO THẬT] |
| Không đóng gói lại IPA; output = đơn vị nhỏ nhất/ZIP | ĐẠT [ĐÃ ĐO THẬT] |
| Streaming G7 cho bundle độc lập lớn (none/LZ4/LZMA/Zstd→none, verify, progress) | ĐẠT trên fixture [FIXTURE]; bundle thật ≥64MB độc lập chưa có để thử (bundle lớn nhất trong IPA là 22,5MB, nằm trong ZIP → đường RAM) |
| Vá 2 lỗ innerHTML + quét code mới | ĐẠT [ĐÃ ĐO THẬT] |
| Quyết định precache theo bằng chứng + đồng bộ mọi kỳ vọng đếm | ĐẠT [ĐÃ ĐO THẬT] |
| G1: quyết định theo bằng chứng + đo phủ trên game thật | ĐẠT (thu hẹp phạm vi có ghi rõ; phủ 0% key ngôn ngữ thật) [ĐÃ ĐO THẬT] |
| UI (E) đầy đủ + thông báo theo mã lỗi thật | ĐẠT [ĐÃ ĐO THẬT] (D1–D4 trên Chromium thật, 13/13) |
| Test mới + harness mới + báo cáo từ cùng 1 lần chạy | ĐẠT (32 + 5 test; harness 13/13 từ cùng 1 lần chạy sau sửa D2) |
| Hồi quy brief §4 + vòng tròn trên game thật | ĐẠT (khớp chính xác §4; vòng tròn khép cho TextAsset, không khép cho MB — đúng phạm vi) |
| Full suite không đỏ mới | ĐẠT (1759 chạy: 1729 đạt; 28 đỏ trùng baseline, đều do fixture thiếu) |
| RAM Safari thật | CHƯA KIỂM CHỨNG — xem §6 |
