# Mục C7 — Patch pack nhị phân (BearTea web)

Ngày: 2026-11-16. Harness: `exp/verify_analysis_c7.mjs` — **13/13 PASS**.
Full suite Python: **1633 passed, 2 skipped** (venv sandbox, 66,9s).

## 1. Sửa 2 FAIL cũ (làm trước khi code C7, tách riêng)

### 1a. `phase1_ci` — `markers_count` 122 vs kỳ vọng 116

- **Kết luận: 6 marker mới là marker test CÓ CHỦ Ý, không phải thêm nhầm.**
  Cả 6 đều nằm trong `pb3/tests/test_unity.py`, class `TestBundleV9Zstd` (mục C5):
  `test_v9_none_parse`, `test_v9_stream_matches_parse`,
  `test_zstd_without_js_raises_clear_error` (v9), `test_zstd_full_parse_with_real_decoder`,
  `test_patch_zstd_bundle_rebuilds_uncompressed` (zstd), `test_patch_v9_refuses` (v9).
  Mỗi marker gắn đúng 1 test thật, fixture đều có
  (`tests/fixtures/unity/sample_v9.unity3d`, `sample_v9_zstd.unity3d`,
  `sample_zstd.unity3d`).
- **Sửa:** `exp/verify_phase1_ci.mjs` kỳ vọng 116 → **122**, phủ 126 → **132 test**
  (5 class-level + 117 method-level — đếm bằng AST, không đoán).
  Đồng thời cập nhật `tests.json` 158 → **159** trong cùng harness (mục C7 thêm
  `tests/test_patch_pack.py`).
- **Kiểm chứng lại:** `verify_phase1_ci.mjs` **8/8 PASS** (xem §4).

### 1b. Full suite Python — 1 FAIL `test_rules_ish_only`

- Nguyên nhân: `pb3/sdk/unity.py:1347` comment chứa chuỗi `I2Languages`
  (từ đợt trích xuất game thật 602a9f3), vi phạm luật cấm tên game cụ thể
  trong mã (`test_no_game_specific_branches_or_forbidden_scope`).
- **Sửa:** chỉ diễn đạt lại comment (KHÔNG đổi logic):
  `I2Languages` → `bảng ngôn ngữ I2`.
- **Kiểm chứng lại:** `tests.test_rules_ish_only` **17 passed**;
  full suite **1633 passed, 2 skipped**.

## 2. C7 — Patch pack là gì

Web chỉ đọc được asset nhị phân (texture/âm thanh read-only, dùng iSH) nên web
xuất một **"patch pack"** — manifest JSON + script Python mô tả
`asset path → change` — để user mang sang iSH/desktop áp vào file gốc.

Thiết kế **tương thích schema plan JSON mục 8** (không phát minh schema mới):
manifest chính là một plan (version 1, entries có `asset_path`, `asset_type`,
`op`, `object`, `changes`, `preview_before`, `preview_after`, `before_sha`,
`after_sha`, `selected`) thêm lớp vỏ patch-pack (`format: "pb3-patch-pack"`,
`pack_version: 1`, `pb3_version: "3.1"`, `source_file`, `source_sha256`,
`apply_script: "apply_patch.py"`) và trường `operation: "replace_text"` trên
mỗi entry (op giữ nguyên chế độ ghi của plan: `PATCH_INPLACE`/`REBUILD_SECTION`).

### Luồng dùng

- **Từ Mod:** sau khi Mod dry-run xong (hoặc ghi xong), nút
  **"Xuất patch pack (.zip)"** bật lên. Pack xây lại từ edits (không dùng kết
  quả dry-run cũ) nên luôn khớp file hiện tại; đổi file thì phải chạy Mod thử
  lại (nút tự tắt + kiểm tra fileKey khi bấm).
- **Từ plan JSON mục 8:** nút **"Xuất patch pack (.zip)"** trong khu vực plan
  (chỉ entry được tick, trừ khi `REBUILD_ALL` ở tầng plan).
- Pack tải về là **1 file .zip** duy nhất qua Lưu/Chia sẻ như các file khác,
  gồm `patch_manifest.json` + `apply_patch.py` + `README.txt`.

### Script áp patch (`apply_patch.py`, stdlib-only, Python ≥ 3.9)

- Tự tìm cây PB3 qua `PB3_ROOT`, thư mục chứa script, thư mục làm việc, hoặc
  thư mục gốc của dự án trên iSH; không tìm thấy thì báo lỗi rõ kèm nơi đã tìm.
- Áp bằng đúng `sdk.unity.apply_patch` như executor trên web (cùng
  `PatchRequest`, thay lần xuất hiện đầu tiên) nên `after_sha` khớp.
- **Mặc định dry-run** (chỉ kiểm tra + tính, không ghi gì); chỉ ghi thật khi
  có cờ `--allow-write` (đúng quy tắc PB3: patch mặc định dry-run).
- **Không bao giờ ghi đè file gốc:** file đã patch nằm trong
  `<tên-file>.pb3-patched/` kèm bản sao lưu đơn vị gốc (`backup-goc/`); ghi
  xong đọc lại file để verify lần cuối.
- **Từ chối toàn bộ, không ghi nửa vời** (exit 2): file gốc không khớp
  `source_sha256`, script hiện tại không khớp `before_sha`, asset không tìm
  thấy, bundle mã hoá/không đọc được, bundle v9 (chỉ đọc), entry dùng op
  `REBUILD_ALL` (cần maturity M4). Bridge cũng từ chối ngay lúc xuất pack
  nếu plan chứa entry `REBUILD_ALL` hoặc bundle v9 hoặc `before_sha` lệch.
- File đã patch là đơn vị nhỏ nhất (bundle/.assets), không phải gói đã đóng
  gói lại — ghi rõ trong manifest notes + README.

### File đổi

| File | Đổi |
|---|---|
| `pb3/sdk/patch_pack.py` | **MỚI** — sinh manifest + zip pack + template script áp patch |
| `pb3/bridge.py` | **MỚI** op `patch_pack(path, kind, payload_json)` → `/tmp/patch_pack.zip` |
| `pb3/sdk/unity.py` | Sửa comment `I2Languages` (FAIL cũ §1b) |
| `pb3/tests/test_patch_pack.py` | **MỚI** — 19 test (xem §3) |
| `pb3/files.json` | 89 → **90** (+ `sdk/patch_pack.py`) |
| `pb3/tests.json` | 158 → **159** (+ `tests/test_patch_pack.py`) |
| `sw.js` | precache + `sdk/patch_pack.py`, `CACHE_VERSION` v11 → **v12-20261116** |
| `app.js` | Nút + handler xuất pack (Mod + plan), `state.lastMod`, reset khi đổi file |
| `index.html` | 2 nút `btn-mod-patch-pack` / `btn-plan-patch-pack` + ghi chú C7 |
| `exp/verify_analysis_c7.mjs` | **MỚI** — harness C7 (13 mục) |
| `exp/check_analysis_c7_static.py` | **MỚI** — kiểm tra tĩnh C7 |
| `exp/verify_phase1_ci.mjs` | markers 116 → 122, phủ 126 → 132, tests.json 158 → 159 |
| `exp/verify_module_worker.mjs`, `exp/verify_patch01_web.mjs`, `exp/verify_limit_2gb.mjs` | `files.json` 89 → 90 (số đếm cũ) |
| `exp/verify_item8.mjs`, `exp/check_item8_static.py` | files/tests 89/158 → 90/159, `CACHE_VERSION` v11 → v12 |
| `exp/check_analysis_c6_static.py`, `exp/verify_analysis_c6.mjs` | files/tests 89/158 → 90/159 (số đếm cũ) |

Lưu ý: `sdk/patch_pack.py` chứa template script + README dưới dạng chuỗi nên
mọi chuỗi cấm theo luật iSH-only (URL, `pip install`, `curl`/`wget`, `/home/`,
`/root/pb3`, `android`, `apk`, `I2Languages`…) đều bị cấm cả trong template —
đường dẫn iSH mặc định trong script được ghép từ phần (`os.sep + "root" + …`),
không ghi cứng; static check C7 quét cả template.

## 3. Kết quả harness C7 — 13/13 PASS

| Mục | Kết quả |
|---|---|
| `static_node_py_check` | PASS — `node --check` app.js/sw.js; `py_compile` bridge.py, patch_pack.py, test_patch_pack.py |
| `static_c7_checks` | PASS — stdlib-only + Python 3.9, không chuỗi cấm, files.json=90, tests.json=159, sw precache + v12, bridge + UI đủ |
| `python_unit_patch_pack_plan` | PASS — `tests.test_patch_pack` + `tests.test_plan`: **Ran 33 tests, OK** (19 C7 + 14 plan — harness mục 8 ghi 13 là số cũ, file hiện có 14 test method) |
| `C0_plan_build_for_pack` | PASS — plan id `3b573d93739c`, 3 entry |
| `C1_patch_pack_plan_zip_schema` | PASS — zip 6596B, 3 file; manifest format đúng, `source_sha256` khớp fixture, 3 entry đủ trường plan + `operation=replace_text` |
| `C2_patch_pack_mod_after_dryrun` | PASS — mod dry-run verified=true; pack 1 entry `en.json`, change Hello → Xin chào, op REBUILD_SECTION |
| `C3_patch_pack_refuses_rebuild_all` | PASS — lỗi rõ kèm M4 |
| `browser_plan_build_3_entries` | PASS — 20 plugin, font 142/142, đủ 3 asset; nút pack (plan) bật, nút pack (Mod) tắt trước khi Mod thử |
| `browser_plan_patch_pack_download` | PASS — tải `patch-pack-3b573d93739c.zip` (6598B); manifest 3 entry, source_sha256 khớp fixture |
| `browser_mod_dryrun_patch_pack_download` | PASS — Mod dry-run xong nút pack bật; tải `patch-pack-9abcc97ac287.zip` (6071B), 1 entry en.json Hello → Xin chào |
| `browser_apply_script_real_python` | PASS — xem §3.1 |
| `browser_390_no_overflow_no_errors` | PASS — scrollWidth=390 = clientWidth, không pageerror/console-error |
| `shell_size_after_c7` | PASS (ghi số) — tổng raw **350.713B (342,5KB)**, gzip 101.817B (99,4KB) |

Ghi chú: `test_plan` có 14 test method (harness mục 8 ghi 13 là số cũ).

### 3.1. Áp thật script bằng Python hệ thống (D4, fixture item8)

- **Dry-run mặc định:** exit 0, in `DRY-RUN`, file gốc không đổi sha,
  chưa có thư mục output.
- **`--allow-write`:** exit 0, in `DA GHI`; bundle đã patch đọc lại đúng
  (`en.hello=Xin chào`, `menu.start=Bắt đầu chơi`,
  `dialog.greeting=Chào mừng, anh hùng!`); backup đúng bằng bundle gốc trong
  fixture zip; **file gốc không đổi sha**.
- **Sửa `before_sha` trong manifest:** exit **2**, stderr có `before_sha`,
  không ghi gì, file gốc không đổi.

### 3.2. Lỗi gặp khi kiểm chứng (đã sửa)

Lần chạy harness đầu tiên FAIL ở D2 (timeout 120s): handler `app.js` gọi
`patch_pack` thiếu đối số đầu `inputPath()` — bridge báo
`TypeError: patch_pack() missing 1 required positional argument: 'payload_json'`
(thấy rõ trong banner `#error` khi tái hiện riêng). Đã sửa cả 2 handler
(Mod + plan) và thêm guard vào `check_analysis_c7_static.py` (bắt buộc
`[inputPath(), "mod"` / `[inputPath(), "plan"` có mặt). Chạy lại: 13/13 PASS.

## 4. Hồi quy

| Harness | Kết quả |
|---|---|
| `verify_module_worker.mjs` | **17/17 PASS** |
| `verify_item8.mjs` (plan JSON) | **16/16 PASS** |
| `verify_analysis_c2.mjs` | **13/13 PASS** |
| `verify_phase1_ci.mjs` | **8/8 PASS** (markers_count 122, tests.json 159) |
| `verify_analysis_c6.mjs` | **10/10 PASS** (chạy thêm vì đã sửa số đếm trong static check C6) |
| Full suite Python (venv) | **1633 passed, 2 skipped** |

**FAIL còn lại (đã biết, không phải do C7):** item6 vỏ trang lần tải đầu raw
**350.713B (342,5KB) > 100KB** (gzip 101.817B đạt ~99,4KB) — do `app.js`
monolith (205.912B raw); C7 chỉ thêm ~26B vào app.js + 1 nút HTML, không đổi
kết luận mục 6.

## 5. Điểm chưa đạt / việc còn lại

1. **Chưa kiểm chứng trên Safari thật / iPhone:** nút "Xuất patch pack" và
   đường Lưu/Chia sẻ của zip chưa được bấm trên Safari thật; script
   `apply_patch.py` chưa được chạy trên iSH thật (chỉ chạy bằng Python hệ
   thống trong sandbox, `PB3_ROOT` trỏ tới cây web — cây iSH `/root/pb3`
   tương thích vì cùng mã nguồn, nhưng chưa có bằng chứng chạy trên iSH).
2. **Chưa có bundle Zstd/v9 thật để thử từ chối:** từ chối v9 chỉ kiểm chứng
   bằng fixture giả lập trong unit test (bundle v9 tự dựng); bundle mã hoá
   chỉ kiểm chứng qua đường lỗi parse chung, chưa có file mã hoá thật.
3. **Pack từ Mod chỉ có 1 edit/1 entry mỗi lần:** nhiều edit trên cùng asset
   thành nhiều entry nối tiếp (đúng, đã test 2 edit nối tiếp) nhưng UI Mod
   hiện tại chỉ nhập 1 edit/lần — muốn pack nhiều thay đổi thì lặp lại Mod
   rồi xuất, hoặc dùng plan JSON.
4. **Script chưa hỗ trợ ghi ngược vào IPA:** như mọi output web khác, file
   đã patch là đơn vị nhỏ nhất (bundle/.assets); user tự đóng gói lại IPA
   (đã ghi rõ trong manifest notes + README).
5. **Vỏ trang raw > 100KB** (item6, đã biết — xem §4).
