# Mục 8 — Plan JSON + UI checkbox cho REBUILD_ALL (kết quả)

Ngày kiểm chứng: 2026-11-16. Repo: MunKai0408/MeoRocky, nhánh main.
Head lúc làm: 7f99dd9 (sửa lỗi "Lỗi không rõ" + nút bỏ/chọn tất cả ở 56179e4,
mục 7 font atlas ở 7f99dd9). KHÔNG đẩy lên GitHub — coordinator đẩy sau
khi kiểm chứng.

## 0. Tóm tắt nhanh

| Hạng mục | Kết quả |
|---|---|
| Harness mục 8 (`exp/verify_item8.mjs`) | **16/16 PASS, 0 FAIL** |
| Unit test Python mới (`tests/test_plan.py`) | **14 test, OK** |
| Hồi quy 6 harness liên quan trực tiếp | **enjson 6/6, extract_select 8/8, translate_connect 12/12, item4_pool 28/28, clone_fix 10/10, hotfix_safari2 17/17 — tất cả xanh** |
| Harness mục 7 chạy lại | **11/11 xanh** (sau khi sửa 1 check hình thức, xem §7) |
| Harness mục 6 chạy lại | **15/16** — chỉ còn đúng 1 FAIL có chủ ý từ trước (`shell_raw_under_100kb`, xem §7) |
| Trình duyệt thật 390px | Lập plan hiện 3 entry + checkbox; bỏ tick 1 entry → dry-run báo đúng 2 entry áp; chọn/bỏ chọn tất cả + tải plan JSON đúng; không tràn ngang, không lỗi console |

## 1. Thiết kế — khớp với cơ chế Mod thật

Đọc trước: `mod_patch` (bridge.py) + `sdk.unity.apply_patch` +
`sdk.batch._sdk_resolve_vpath` / `_mod_job` + tab Batch/pool. Plan KHÔNG
vẽ tính năng không làm được:

- **Đầu vào plan là bản dịch dạng key → giá trị mới** (đúng định dạng đầu
  ra tab Dịch, vd `{"hello": "Xin chào"}`). Giá trị cũ lấy từ chính asset
  nên user không nhập lại; asset nào có key đó mới vào plan.
- **Entry = 1 TextAsset JSON** (mỗi entry độc lập theo asset → chia shard
  cho N worker được). Entry mang đủ: `vpath`, `path_id`, `changes`
  `[{key, before, after}]`, `before_sha`/`after_sha` (sha256 script).
  `after_sha` được tính bằng cách mô phỏng đúng `apply_patch` (replace
  chuỗi cũ → mới, lần xuất hiện đầu tiên, theo đúng thứ tự changes) nên
  executor verify khớp byte-for-byte.
- **Executor áp bằng đúng đường patch đã kiểm chứng** (`apply_patch`,
  mặc định `REBUILD_SECTION`, tự leo từ `PATCH_INPLACE` khi chuỗi mới dài
  hơn). Mỗi đơn vị (bundle/CAB) có tính nguyên tử: 1 entry lỗi verify thì
  cả đơn vị không ghi; entry lỗi ghi vào `errors`, không dừng cả plan
  (như batch: lỗi 1 game không dừng cả lô).
- **Kiểm tra placeholder** (kiểu `translation_apply.js` của studio):
  placeholder `{0}`/`%s`/… đổi giữa before/after thì ghi cảnh báo vào
  entry, không chặn.
- **Asset không vào plan thì vào `skipped` kèm lý do** (không phải JSON,
  JSON không phải dict, không có key trùng, không nằm trong danh sách asset
  đã chọn) — không biến mất lặng lẽ.
- **Output là đơn vị nhỏ nhất đã patch** (bundle/.assets), KHÔNG phải IPA
  đóng gói lại — như `mod_patch`.

### REBUILD_ALL — nói rõ 2 tầng (quan trọng, dễ hiểu lầm)

1. **Chế độ ghi REBUILD_ALL của Unity: VẪN BỊ KHÓA trên web.**
   `sdk/unity.py`: `UnityBackend.MATURITY = "M2"`, REBUILD_ALL cần M4 +
   xác nhận (`confirm`). Mục 8 KHÔNG mở khóa này (mở khóa là tuyên bố
   sai về maturity, sẽ vỡ `test_unity.py` — test đó vẫn bắt REBUILD_ALL
   phải raise khi chưa M4). Entry nào để `op = "REBUILD_ALL"` thì executor
   từ chối riêng entry đó (`E_WRITE_DISABLED`, nhắc M4), các entry khác
   vẫn áp. Harness C7 kiểm chứng đúng hành vi này.
2. **`rebuild_all` ở tầng plan (cái mục 8 làm): cờ plan + checkbox UI
   "Áp dụng toàn bộ (REBUILD_ALL — bỏ qua tick từng dòng)".** Khi bật,
   executor áp TOÀN BỘ entry bất kể tick từng dòng. Khi tắt, chỉ entry
   được tick mới áp. UI + ghi chú trong plan giải thích rõ phân biệt này,
   không giả vờ đó là chế độ ghi REBUILD_ALL của Unity.

## 2. File đổi

| File | Đổi gì |
|---|---|
| `site/pb3/sdk/plan.py` (mới, ~700 dòng) | Planner/executor: `build_plan`, `validate_plan`, `apply_plan` (dry-run / checkpoint / `stop_after` / backup đơn vị gốc / ghi `/tmp/plan_out/` + `/tmp/plan.zip`), `undo_plan` (ưu tiên backup, nếu không còn thì patch ngược + verify `before_sha`). Chỉ stdlib, Python 3.9. |
| `site/pb3/bridge.py` | 3 op mới: `plan_build(path, translations_json, entry, mode, rebuild_all, asset_names_json)`, `plan_apply(plan_json, path, dry_run, checkpoint_json, stop_after)`, `plan_undo(plan_json, path)`. Sửa 1 lỗi biên Pyodide: JS `null` qua thành `JsNull` (chỉ `undefined` mới thành `None`) nên `stop_after` dùng truthiness thay vì `is not None`. |
| `site/pb3/tests/test_plan.py` (mới, 14 test) | Fixture tự dựng bằng builder có sẵn trong repo (`tests/fixtures/unity/build_fixtures.py`): bundle v7 LZ4, 3 TextAsset JSON. Bao phủ: model plan, filter asset, placeholder, dry-run, chỉ áp entry được tick, `rebuild_all`, từ chối op REBUILD_ALL, resume, undo từ backup, undo patch ngược, undo chỉ entry đã chọn. |
| `site/pb3/files.json` | Thêm `pb3/sdk/plan.py` (88 → 89). |
| `site/pb3/tests.json` | Thêm `pb3/tests/test_plan.py` (156 → 157). |
| `site/sw.js` | Thêm `pb3/sdk/plan.py` vào PRECACHE_URLS; `CACHE_VERSION` v7-20261116 → **v8-20261116** (đổi precache thì phải đổi version, theo đúng ghi chú trong file); sửa ghi chú số mục precache 122 → 126. |
| `site/index.html` | Tab Mod: thêm mục "Kế hoạch Mod (plan JSON)" — ô entry (vpath), ô lọc asset, ô bản dịch JSON, checkbox REBUILD_ALL, nút Lập kế hoạch / Lấy từ tab Dịch / Chọn tất cả / Bỏ chọn tất cả / Tải plan / Mở plan, bảng plan, nút Xem trước (dry-run) / Áp dụng / Tiếp tục từ checkpoint / Hoàn tác. |
| `site/app.js` | Handler plan: `renderPlanList` (bảng entry + checkbox từng dòng + diff trước/sau từng key), đồng bộ tick vào `state.planSelected`, lưu plan + checkpoint vào localStorage (tải lại trang vẫn mở lại được — bài học từ `translation_apply` của studio), nút "Lấy từ tab Dịch" chép `translate-output` sang, import plan từ file `.json`, báo lỗi khi chưa tick entry nào. |
| `exp/build_item8_fixture.py` (mới) | Sinh `exp/fixtures/item8_fixture.zip`: bundle v7 LZ4 (en.json/menu.json/dialog.json, key khác nhau theo file) + `resources.assets` (settings, vào skipped). |
| `exp/fixtures/item8_fixture.zip` (mới) | 828 byte, sha256 `30f813bd427afa3f…`. |
| `exp/verify_item8.mjs` (mới) | Harness mục 8: 16 mục (tĩnh + unit test + Node Pyodide + trình duyệt thật 390px). |
| `exp/check_item8_static.py` (mới) | Kiểm tra tĩnh: stdlib-only + Python 3.9, files/tests counts, precache + CACHE_VERSION, 3 op bridge, đủ id/handler UI. |
| `exp/item8_results.json` | Kết quả harness mục 8 (máy đọc). |
| `exp/verify_limit_2gb.mjs`, `exp/verify_module_worker.mjs`, `exp/verify_patch01_web.mjs`, `exp/verify_phase1_ci.mjs` | Cập nhật số lượng hardcode 88 → 89 / 156 → 157 (check hình thức, theo tiền lệ mục 7 đã sửa 87 → 88 / 155 → 156). |
| `exp/check_item7_static.py` | Cập nhật check hình thức CACHE_VERSION v7 → v8 (xem §7). |

## 3. Harness mục 8 — 16/16 PASS

| Check | Kết quả |
|---|---|
| `static_node_py_check` | node --check app.js/sw.js + py_compile bridge.py/sdk/plan.py/tests/test_plan.py — OK |
| `static_item8_checks` | stdlib-only + Python 3.9 + files.json=89 + tests.json=157 + precache có sdk/plan.py + CACHE_VERSION=v8 — OK |
| `fixture_regenerate` | build lại fixture 828B — OK |
| `python_test_plan` | 14 test OK |
| `C1_plan_build_model_3_entries` | plan id `3b573d93739c`; 3 entry (en 3 change, menu 3, dialog 2); `before_sha` khớp script gốc; `preview_after` en.hello = "Xin chào" |
| `C2_dry_run_no_write` | applied=3, file gốc giữ nguyên, không có `/tmp/plan_out` hay `/tmp/plan.zip` |
| `C3_untick_one_apply_selected_only` | Bỏ tick dialog → applied=2; en.hello="Xin chào", menu.start="Bắt đầu chơi", dialog.greeting GIỮ NGUYÊN "Welcome, hero!"; backup `/tmp/plan_backup/3b573d93739c` tồn tại |
| `C4_undo_backup_and_reverse_patch` | 4a backup: restored đúng 2 entry đã áp, en.hello về "Hello"; 4b (xóa backup) reverse-patch: restored đúng 2 entry, verify `before_sha` |
| `C5_resume_after_stop` | Lần 1 `stop_after=1`: partial, applied=1, remaining=2; lần 2 (checkpoint): done, applied=2, checkpoint completed=3; nội dung gộp đủ 3 entry đã dịch |
| `C6_plan_json_roundtrip_stable_id` | Xuất 4.126B JSON, đọc lại đủ 3 entry, id giữ nguyên; build lại cho cùng id |
| `C7_rebuild_all_op_refused_m4` | op REBUILD_ALL: applied=2, errors=1, code `E_WRITE_DISABLED`, nhắc M4 |
| `C8_rebuild_all_flag_applies_all` | Tất cả entry bỏ tick nhưng `rebuild_all=true` → applied=3, skipped=0 |
| `browser_plan_build_3_entries` | Boot "Sẵn sàng — 20 plugin"; tab Mod → Lập kế hoạch hiện đủ 3 asset + diff "Xin chào", 3 checkbox |
| `browser_plan_untick_dryrun` | Bỏ tick dialog → dry-run báo "2 entry đã áp", dialog ở mục "Bỏ qua" |
| `browser_plan_select_all_and_download` | Bỏ chọn tất cả → 3/3 chưa tick; chọn tất cả → 3/3 đã tick; tải `plan-3b573d93739c.json` (4.168B), version 1, 3 entry |
| `browser_390_no_overflow_no_errors` | scrollWidth=390 = clientWidth, không pageerror/console-error |

## 4. Lỗi thật tìm được khi làm (đã sửa, có test bao phủ)

1. **Pyodide 314: JS `null` → Python `JsNull`, không phải `None`.**
   `bridge.plan_apply(..., null, null)` nổ `int() argument ... not 'JsNull'`
   ở `stop_after`. Các bridge cũ không dính vì chúng chỉ dùng truthiness
   (`if not x`). Sửa ở biên bridge (`stop_after if stop_after else None`)
   + ghi chú trong code. Bài học cho các op sau: đừng bao giờ kiểm
   `is not None` với giá trị từ JS.
2. **Tên file backup lệch nhau giữa apply và undo.** Ban đầu backup đặt
   tên theo các entry *được chọn ở lần chạy đó*; undo tính trên cả plan
   nên không tìm thấy backup và rơi xuống patch ngược. Đổi sang đặt tên
   theo toàn bộ entry của đơn vị trong plan (ổn định theo plan, không
   theo vpath hay lựa chọn tick — user nạp file đã patch dưới dạng bundle
   trần thì undo vẫn tìm thấy backup).
3. **Undo patch ngược cả entry chưa từng được áp.** Entry bị bỏ tick
   không có chuỗi mới trong file nên patch ngược lỗi "không tìm thấy
   chuỗi cũ". Undo nay chỉ hoàn tác entry được chọn trong plan (như
   apply); `rebuild_all=true` thì hoàn tác toàn bộ. Thêm unit test
   `test_undo_only_selected_entries`.
4. **`str(PB3Error)` giấu detail.** `str(exc)` chỉ hiện message chung
   theo mã (vd "Cấu hình không hợp lệ.") nên lỗi undo ban đầu không chẩn
   đoán được. Thêm `_err_text()` (kèm `exc.detail`) cho mọi lỗi
   apply/undo trong `sdk/plan.py`.
5. **Hợp nhất output resume trong harness:** spread `{...s1, ...s2}` ghi
   đè ngược (mỗi output chứa cả 3 asset, asset chưa áp ở lần đó vẫn là
   bản gốc). Hợp nhất đúng phải theo từng key — đã sửa trong harness và
   trong `test_plan.py` (đã đúng từ đầu ở đó).

## 5. Giới hạn còn lại (nói rõ, không giấu)

- **Undo chỉ chắc chắn trong cùng phiên trang** khi dùng backup (backup
  nằm trong FS worker = RAM trang; tải lại trang là mất). Khi đó undo
  tự thử patch ngược và verify `before_sha`; patch ngược chỉ đúng khi
  chuỗi mới không trùng chuỗi khác trong cùng asset (verify sẽ bắt lỗi
  nếu sai). Muốn undo chắc chắn sau khi tải lại trang: giữ file gốc
  (hoặc file output đã áp) rồi áp lại plan / dùng file gốc làm đầu vào.
- **Plan chỉ làm 1 đơn vị (1 bundle/CAB hoặc 1 .assets) mỗi lần.**
  Muốn plan nhiều bundle trong 1 IPA thì lập nhiều plan (mỗi plan 1
  entry vpath) — executor đã nhóm theo đơn vị nên mở rộng sau không khó,
  nhưng UI hiện tại chưa có.
- **Chỉ TextAsset JSON dạng dict.** TextAsset text thường, JSON list,
  plist/XML… không vào plan (vào `skipped` kèm lý do). Muốn áp các dạng
  đó thì cần planner riêng theo format (như `translation_apply.js` làm
  cho nhiều format) — ngoài phạm vi mục 8.
- **Op REBUILD_ALL (chế độ ghi Unity) vẫn khóa** (cần maturity M4, xem
  §1). Checkbox REBUILD_ALL trên UI là cờ plan (áp toàn bộ entry), không
  phải chế độ ghi đó.
- **Chưa chia shard cho N worker thật:** entry đã độc lập theo asset
  (đủ thông tin để áp riêng) nhưng executor hiện tại chạy tuần tự trong
  1 worker — pool 2 worker của tab Batch chưa được nối vào plan (để đợt
  sau, cùng đợt tích hợp IPA Studio B3 nếu coordinator quyết).
- **Chưa kiểm chứng trên Safari thật / iPhone thật** (xem §8).

## 6. Hồi quy

| Harness | Kết quả |
|---|---|
| `verify_enjson.mjs` | 6/6 |
| `verify_extract_select.mjs` | 8/8 |
| `verify_translate_connect.mjs` | 12/12 |
| `verify_item4_pool.mjs` | 28/28 |
| `verify_clone_fix.mjs` | 10/10 |
| `verify_hotfix_safari2.mjs` | 17/17 |
| `verify_item7.mjs` | 11/11 (sau khi sửa check hình thức CACHE_VERSION, xem §7) |
| `verify_item6.mjs` | 15/16 — chỉ `shell_raw_under_100kb` FAIL, đã biết từ trước (xem §7) |

## 7. Harness cũ đã sửa (chỉ check hình thức, ghi rõ lý do)

1. **4 harness đếm file** (`verify_limit_2gb`, `verify_module_worker`,
   `verify_patch01_web`, `verify_phase1_ci`): 88 → 89 / 156 → 157 — hệ
   quả trực tiếp của file mới (`sdk/plan.py`, `tests/test_plan.py`),
   theo tiền lệ mục 7 đã sửa 87 → 88 / 155 → 156.
2. **`exp/check_item7_static.py`**: check `CACHE_VERSION == "v7-20261116"`
   rớt khi chạy lại mục 7 (chỉ 1 check này rớt, 10/11 còn lại xanh) vì
   mục 8 thêm `sdk/plan.py` vào precache nên **bắt buộc** tăng version
   lên v8 theo đúng luật ghi trong `sw.js` ("đổi precache thì phải đổi
   version"). Đây là check hình thức (chuỗi version), không kiểm hành vi
   — đã sửa thành v8, chạy lại mục 7 **11/11 xanh**.
3. **`verify_item6.mjs` — `shell_raw_under_100kb` FAIL (đã biết từ
   trước, KHÔNG sửa):** raw shell 261,2KB > 100KB vì app.js là monolith
   154.466B (đã 8 tab trước mục 8) + index.html 51.571B. Mục 8 thêm
   ~9KB app.js + ~2KB index.html nhưng check này đã FAIL từ trước mục 8
   (báo cáo mục 7 đã ghi rõ); cách sửa là tách app.js thành chunk lazy
   theo tab — refactor lớn, để làm riêng sau mục 5–9 + audit. Shell gzip
   vẫn 77,6KB < 100KB, lần thăm 2 SW phục vụ từ cache.

## 8. Việc chờ Joseph thử trên Safari thật

1. Mở tab Mod trên iPhone thật, chọn file IPA 9 Kings thật → nhập bản
   dịch JSON (hoặc "Lấy từ tab Dịch") → nhập entry bundle localization
   (vd `Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle::CAB-…`
   — lấy đúng tên CAB từ tab Unity/Danh sách) → "Lập kế hoạch" phải hiện
   đủ entry en.json (và các file JSON khác trong bundle đó).
2. Bỏ tick 1 entry → "Xem trước (dry-run)" phải báo đúng số entry áp.
3. "Áp dụng plan" (bấm OK ở hộp xác nhận) → tải bundle đã patch về bằng
   nút "Lưu / Chia sẻ" (kiểm tra luôn đường lưu Share trên Safari thật,
   vốn đang kẹt).
4. "Hoàn tác" ngay trong cùng phiên (chưa tải lại trang) phải khôi phục
   đúng; thử tải lại trang rồi "Hoàn tác" tiếp để xem đường patch ngược
   có hoạt động trên file thật không.
5. "Tải plan (.json)" rồi "Mở plan (.json)" lại — plan có mở lại đúng
   không, tick có giữ nguyên không.
6. Kiểm tra checkbox "Áp dụng toàn bộ (REBUILD_ALL)" có dễ hiểu không
   (nó áp toàn bộ entry, KHÔNG phải chế độ ghi REBUILD_ALL của Unity —
   nếu Joseph thấy nhãn gây nhầm thì đổi nhãn ở đợt sau).
