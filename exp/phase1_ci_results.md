# Phase 1 CI — Kết quả (2026-11-16)

Phạm vi: CI + quyền GitHub cho bản web PB3 (job `unit` + `assets` + `pyodide-node`),
sửa test ngày, marker `needs_fixture`, trang `/test.html`, chuẩn bị `pages.yml`,
và — bổ sung ưu tiên cao — fix picker `.ipa` trên Safari iPhone.
Chỉ làm local + staging; KHÔNG đẩy gì lên GitHub (coordinator đẩy bằng
`gh.py push-tree` theo `phase1_push_manifest.json` sau khi kiểm chứng).

## 1. File đã đổi / mới (xem manifest `phase1_push_manifest.json` để có sha256)

| File (đường dẫn repo) | Thay đổi |
|---|---|
| `pb3/tests/conftest.py` | Hợp nhất conftest mẫu của Joseph vào conftest tiering (giữ nguyên tiering) |
| `pb3/tests/helpers.py` | Thêm `pytest` dùng chung có stub khi thiếu pytest (để iSH chạy `run_tests.py` được) |
| `pb3/tests/test_support_matrix.py` | Test ngày: `setUp` tự đặt mtime corpus về 2026-11-15 12:00 UTC |
| `pb3/tests/test_logging_limits.py` | Đếm chỉ handler do PB3 tạo (pytest gắn thêm handler riêng) |
| 12 file test (xem §3) | Gắn 116 decorator `@pytest.mark.needs_fixture(...)` phủ 126 test |
| `pb3/tests.json` | MỚI — manifest 155 file cho `test.html` (file test + 5 module test cần) |
| `test.html` | MỚI — chạy pytest qua Pyodide trong trình duyệt (debug thủ công) |
| `index.html` | Bỏ `accept` khỏi `#file-input` và `#batch-files` (fix picker .ipa iOS) |
| `app.js` | Kiểm tra đuôi file bằng JS (`GAME_EXTS`, báo lỗi thân thiện) |
| `.github/workflows/test.yml` | Viết lại: 3 job `unit` + `assets` + `pyodide-node` |
| `.github/workflows/pages.yml` | MỚI — job test giống hệt `test.yml`, deploy sau khi test xanh |
| `.github/scripts/pyodide_smoke.mjs` | MỚI — script CI chạy pytest smoke qua Pyodide trong Node |
| `exp/verify_phase1_ci.mjs` | MỚI — harness kiểm chứng Phase 1 (8 mục) |
| `exp/phase1_ci_results.md` | Báo cáo này |
| `exp/phase1_ci_harness.json` | Kết quả harness (máy tạo) |

KHÔNG đụng: `~/workspace/pb3_fix/pb3` (cây gốc chỉ-đọc), các file zip,
8 file nhị phân đã lên main, 20 file chưa lên main (không commit thêm).
`pb3/fonts/BeVietnamPro-Regular.ttf` chỉ khôi phục local từ `.b64` để kiểm
chứng (đúng bước CI làm) — KHÔNG đưa vào manifest, KHÔNG đẩy.

## 2. Test ngày — đã sửa đúng cơ chế

- File: `pb3/tests/test_support_matrix.py::TestGenerate::test_effective_maturity_uses_corpus_not_declared`.
- Cơ chế thật (đã đọc code): `sdk/support_matrix.py::_corpus_date` lấy mtime
  của `tests/corpus/<id>.json` rồi đổi sang UTC (`timezone.utc`); test assert
  `test_date == "2026-11-15"`. Timezone KHÔNG phải nguyên nhân; **git checkout
  đặt lại mtime** mới là nguyên nhân.
- Cách sửa: `MatrixCase.setUp` (sau `copy_project`) tự `os.utime` file
  `tests/corpus/json_text.json` trong bản sao về timestamp cố định
  2026-11-15 12:00 UTC (1794744000). Không nới assertion, không đổi expected,
  không freeze clock/TZ (test này không dùng clock nên freeze không sửa được).
- Kiểm chứng: `touch tests/corpus/json_text.json` về ngày hôm nay (2026-11-16)
  rồi chạy test → **PASS** (1 passed). Đã khôi phục mtime gốc của file sau đó.
- Ghi chú: tác giả gói cũng nói "gần như chắc chắn là timezone" chỉ là phỏng
  đoán — hướng mtime trong `setUp` vẫn giữ nguyên theo quyết định của Joseph.

## 3. Marker `needs_fixture` — 126 test, 116 decorator

- Hợp nhất conftest: conftest mẫu của Joseph và conftest tiering hiện có đều
  định nghĩa `pytest_configure` + `pytest_collection_modifyitems` — đã gộp
  thành một hàm mỗi loại (needs_fixture chạy TRƯỚC lọc tầng; tiering giữ
  nguyên logic). Không có xung đột nào khác: `fixed_now`/`fixed_today`/
  `fixture_path` thêm vào dạng fixture opt-in, không đụng code cũ.
- Cách xác định test cần marker (không đoán mò): chạy suite trên bản copy đã
  xóa 20 file → 126 test lỗi (122 failed + 4 errors); đối chiếu từng lỗi với
  `FileNotFoundError` trong log + đọc code (AST: hằng số fixture + helper
  `_fixture()` + hằng module) để gắn đúng file cho từng test, kể cả test lỗi
  gián tiếp qua `E_INPUT_NOT_FOUND` (vd. test CLI chạy qua `copy_project`).
- 5 class gắn marker ở cấp class (mọi test trong class đều lỗi, cùng 1 file):
  `TestContainerCli` (zip/sample.ipa), `TestUnityExtraction`
  (unity/sample.unity3d), `TestContainerProgress` (zip/sample.ipa),
  `TestF5PickerWiring` (zip/sample.ipa), `TestAutoFastPath`
  (unity/sample_v8_lz4.unity3d). Còn lại gắn ở cấp method.
- Test cần 1 trong 8 file ĐÃ lên main thì KHÔNG gắn marker (phải chạy thật).
- Vấn đề phát hiện khi gắn marker: `import pytest` trực tiếp trong 12 file
  test sẽ LÀM VỠ `run_tests.py` trên iSH (iSH không có pytest). Đã xử lý bằng
  cách thêm `pytest` dùng chung vào `tests/helpers.py` với stub tối giản khi
  thiếu pytest thật (marker thành decorator giữ nguyên hàm); 12 file import
  qua `from tests.helpers import pytest`. Kiểm chứng bằng Python hệ thống
  (không có pytest): `run_tests.py --tier full` vẫn xanh — xem §4.
- Fixture tổng hợp: **KHÔNG làm.** Lý do: test assert đúng nội dung fixture
  thật (vd. `sample.unity3d` phải parse ra đúng version/entry, `sample.ipa`
  phải có đúng entry) nên fixture tổng hợp đơn giản sẽ fail assertion chứ
  không cứu được test; fixture Unity bundle/assets không sinh đơn giản được
  bằng script trong test. Skip có điều kiện + job `assets` lo phần này.

## 4. Kết quả chạy suite (sandbox, Python 3.12; CI dùng Python 3.9)

Tất cả các lần chạy dùng `TMPDIR` trỏ ra ổ home (tránh `/tmp` 512M đầy).

| Lần chạy | Kết quả |
|---|---|
| (a) Full local + `STRICT_FIXTURES=1` (đủ 28 file) | **1562 passed**, 0 fail, 0 skip, 66.6s, exit 0 |
| (b) Mô phỏng job `unit`: bản copy xóa 20 file, `pytest -m "not needs_fixture"` | **1436 passed, 126 deselected**, exit 0, 59.8s |
| (b2) Job `unit` trên bản copy xóa CẢ 28 file | 45 failed, 1391 passed — 45 test này cần 8 file ĐÃ lên main (xem §5) |
| (c) Mô phỏng job `assets`: bản copy xóa 20 file + `STRICT_FIXTURES=1` (không tải Release) | **122 failed + 4 errors, 1436 passed, exit 1 — ĐỎ ĐÚNG** (chứng minh STRICT hoạt động, không skip lén) |
| Skip local: bản copy xóa 20 file, không STRICT | 1436 passed, **126 skipped** kèm lý do `thiếu fixture: ...`, exit 0 |
| `run_tests.py --tier full` (Python hệ thống, KHÔNG có pytest — mô phỏng iSH) | **1561 passed, 1 skipped, 0 lỗi, 0 fail**, 81.3s, exit 0 |
| Pytest smoke qua Pyodide (Node, §7) | **58 passed**, 1504 deselected, exit 0 |

Ghi chú: pytest đếm 1562 passed còn `run_tests.py` đếm 1561 passed + 1 skipped
— cùng 1562 test, chỉ khác cách đếm 1 test skip có điều kiện; cả hai đều xanh.

## 5. 45 test của job `unit` vẫn cần 8 file ĐÃ lên main (liệt kê rõ, không giấu)

Job `unit` xanh khi 8 file này có mặt (chúng đã commit trên main nên CI luôn
có). Chúng KHÔNG gắn marker vì phải chạy thật. Nếu 8 file này cũng bị xóa thì
45 test dưới đây fail — đó là phụ thuộc đúng, không phải marker thiếu:

- `test_batch.py::TestBatchRealKB::test_kb_preset_file_does_not_leak_across_locales` (kb sqlite)
- `test_batch_cli.py::TestBatchCli::test_cli_batch_dry_run_writes_no_export`
- `test_batch_cli.py::TestBatchCli::test_cli_batch_two_extract_one_error`
- `test_batch_cli.py::TestBatchCli::test_cli_resume_with_out_skips_done_games`
- `test_batch_cli.py::TestBatchCli::test_cli_resume_without_out_finds_previous_run`
- `test_extraction.py::TestApplyEdits::test_apply_dry_run_writes_nothing`
- `test_extraction.py::TestApplyEdits::test_apply_plain_csv_only_matching_locale`
- `test_extraction.py::TestApplyEdits::test_apply_readonly_plugin_refuses`
- `test_extraction.py::TestArbitration::test_ambig_ask_fn_none_skips`
- `test_extraction.py::TestArbitration::test_ambig_ask_fn_picks_second`
- `test_extraction.py::TestArbitration::test_ambig_asks_with_two_options`
- `test_extraction.py::TestCheckpoint::test_checkpoint_corrupt_falls_back`
- `test_extraction.py::TestCheckpoint::test_checkpoint_rebuilds_ir`
- `test_extraction.py::TestCheckpoint::test_checkpoint_saved_and_reused`
- `test_extraction.py::TestDiscovery::test_inventory_excludes_and_scores`
- `test_extraction.py::TestExtractionPipeline::test_csv_collision_resolved_by_stem`
- `test_extraction.py::TestExtractionPipeline::test_dry_run_writes_nothing`
- `test_extraction.py::TestExtractionPipeline::test_duplicate_keys_keep_first`
- `test_extraction.py::TestExtractionPipeline::test_export_verify_roundtrip`
- `test_extraction.py::TestExtractionPipeline::test_game1_export_files_exist`
- `test_extraction.py::TestExtractionPipeline::test_game1_full_extract`
- `test_extraction.py::TestExtractionPipeline::test_game1_skipped_reasons`
- `test_extraction.py::TestExtractionPipeline::test_ir_document_built`
- `test_extraction.py::TestExtractionPipeline::test_locale_filter_en_only`
- `test_extraction.py::TestExtractionPipeline::test_locale_not_found_error`
- `test_extraction.py::TestExtractionPipeline::test_multi_locale_csv_splits`
- `test_extraction.py::TestExtractionPipeline::test_placeholders_and_unicode_preserved`
- `test_extraction.py::TestExtractionPipeline::test_report_json_matches_outcome`
- `test_extraction.py::TestExtractionPipeline::test_unknown_engine_still_extracts`
- `test_final_optimize.py::TestExtractionProgress::test_phases_report_real_counts`
- `test_format_apple_plist.py::TestPlistBuild::test_binary_build_raises_write_disabled`
- `test_format_apple_plist.py::TestPlistConformance::test_detect_and_candidates`
- `test_format_apple_plist.py::TestPlistParse::test_binary_parses_same_keys`
- `test_format_apple_strings.py::TestStringsBuild::test_build_keeps_utf16_bom`
- `test_format_apple_strings.py::TestStringsConformance::test_detect_and_candidates`
- `test_format_apple_strings.py::TestStringsParse::test_utf16le_bom_parse`
- `test_language_cli.py::TestLanguageCli::test_cli_ambig_without_tty_shows_ask`
- `test_language_cli.py::TestLanguageCli::test_cli_apply_dry_run`
- `test_language_cli.py::TestLanguageCli::test_cli_apply_write_plain`
- `test_language_cli.py::TestLanguageCli::test_cli_extract_zip`
- `test_language_cli.py::TestLanguageCli::test_cli_locale_filter`
- `test_language_cli.py::TestMenuExtract::test_menu_2_extracts_fixture`
- `test_translate_cli.py::TestMenuTranslate::test_menu_2_translate_fake`
- `test_translate_cli.py::TestTranslateCli::test_cli_extract_translate_fake`
- `test_translate_cli.py::TestTranslateCli::test_cli_extract_translate_no_credit_flag`

Không có test nào trong job `unit` cần 1 trong 20 file chưa lên main mà thiếu
marker — đã chứng minh bằng lần chạy (b): 1436 passed, 0 fail khi xóa 20 file.

## 6. CI mới — `test.yml` (3 job) + Release asset fixture

- Job `unit`: Python 3.9 + `pip install lz4 pytest` + khôi phục font + kiểm tra
  đủ 8 file trên main → `pytest tests -q -rs -m "not needs_fixture"`.
  Luôn required, không cần file nhị phân nào trong 20 file chưa lên main.
- Job `assets`: như trên + `actions/cache` cho fixture + `gh release download
  fixtures-v1 --pattern pb3-fixtures-v1.zip` (dùng `GITHUB_TOKEN` mặc định,
  đủ quyền đọc release cùng repo) + giải nén + kiểm tra đủ 20 file → toàn bộ
  suite với `STRICT_FIXTURES=1` (`pytest tests -q -rs`). Thiếu file là LỖI.
- Job `pyodide-node`: xem §7.
- **Release asset (chưa tồn tại — job `assets` sẽ ĐỎ tới khi có):**
  Tag `fixtures-v1`, asset `pb3-fixtures-v1.zip` — file zip chứa đúng 20 file
  dưới đây, giữ nguyên đường dẫn tương đối từ `pb3/`:
  `tests/fixtures/sqlite_loc/a.sqlite`;
  `tests/fixtures/unity/nested.ipa`, `nested_v8.ipa`, `resources.assets`,
  `sample.unity3d`, `sample_lz4.unity3d`, `sample_lzma.unity3d`,
  `sample_typetree.assets`, `sample_v21.assets`, `sample_v23.assets`,
  `sample_v6.unity3d`, `sample_v8.unity3d`, `sample_v8_lz4.unity3d`,
  `sharedassets0.assets`; `tests/fixtures/unreal_locres/legacy.locres`,
  `vi.locres`; `tests/fixtures/unreal_pak/game_v11.pak`, `game_v8.pak`;
  `tests/fixtures/xml_strings/unicode.xml`; `tests/fixtures/zip/sample.ipa`.
  **Ai upload: coordinator, bằng PAT (quyền đã sửa xong), sau khi Phase 1 xong.**
  Khi đổi tag phải đổi cả key `actions/cache` (`pb3-fixtures-<tag>`).
- Header `test.yml` đã viết lại đúng trạng thái mới (xóa đoạn lỗi thời về
  "28 file thiếu" và "1 test sẽ đỏ"); YAML parse được.

## 7. `/test.html` + job `pyodide-node` (kiểm chứng runtime trình duyệt)

- `test.html` (mới): chạy pytest qua Pyodide đã vendor (`./pyodide/pyodide.mjs`,
  cùng origin, không CDN), `micropip install pytest`, fetch `pb3/files.json` +
  `pb3/tests.json` (manifest mới, 155 file: file test + corpus + fixture có
  trên main + 5 module Python mà test import nhưng `files.json` của app không
  có: `menu.py`, `run_tests.py`, `sdk/example_kv.py`, `sdk/testing.py`,
  `ui/cli.py`) ghi vào FS ảo, khôi phục font từ `.b64`, chọn tier smoke
  (mặc định) / fast, hiện tiến độ + kết quả từng test + nút sao chép kết quả.
  Ghi rõ trên trang: chỉ chạy smoke/fast trong trình duyệt, full suite chạy ở
  CI; `/test.html` là công cụ debug thủ công, **không headless trong CI,
  không dùng làm gate**.
- Probe Pyodide trong Node (harness): **58 passed, 1504 deselected, exit 0**.
  Ban đầu probe fail 2 lần, đã sửa thật: (1) 25 lỗi collection vì `files.json`
  thiếu 5 module → thêm vào `tests.json`; (2) 1 test fail
  `test_detects_interrupted_previous_run` (`'other_running' != 'interrupted'`).
  Nguyên nhân (đã xác minh bằng probe riêng): wasm không có bảng process —
  `os.kill(pid, 0)` dưới Pyodide không bao giờ raise `ProcessLookupError` nên
  `core/runstate.py::pid_alive()` luôn trả True (mặc định an toàn khi errno
  lạ). Đây là giới hạn môi trường wasm, không phải lỗi sản phẩm; test này vẫn
  chạy thật ở job `unit`/`assets` trên Linux. Vì vậy cả `test.html` và script
  CI đều `--deselect` test này kèm chú thích + ghi chú trên trang.
- Job `pyodide-node` (mới, trong cả `test.yml` và `pages.yml`): Node 20 +
  `node .github/scripts/pyodide_smoke.mjs`. Chạy script thật ở sandbox:
  **58 passed, 1504 deselected, exit 0.** Job này KHÔNG gate deploy (deploy
  chỉ cần `unit` + `assets` xanh) — nó chỉ kiểm chứng thêm runtime trình duyệt.

## 8. `pages.yml` (mới)

- Job test giống hệt `test.yml` (đã diff kiểm tra: job `unit` và `assets`
  giống từng dòng; thêm `pyodide-node` giống hệt).
- Job `build-deploy` chỉ chạy khi push main (`github.ref == 'refs/heads/main'`,
  không chạy ở pull request) và sau khi `unit` + `assets` xanh
  (`needs: [unit, assets]`). KHÔNG tải Pyodide (đã vendor sẵn `pyodide/`
  314.0.8), chỉ đóng gói đúng file web root vào artifact rồi `deploy-pages`.
  File phase sau chưa có trong repo (kb-store.js, translate.js, file-reader.js,
  worker-pool.js, sw.js, register-sw.js, diagnostics.html) thì staging chỉ
  copy file đang có + cảnh báo, không fail; fail chỉ khi thiếu file lõi
  (index.html, app.js, worker.js, fonts/, pb3/, pyodide/).
- Ghi rõ trong header: **CẦN Joseph đổi Pages source sang GitHub Actions**
  (Settings → Pages → Build and deployment → Source: GitHub Actions); tới khi
  đổi thì deploy theo branch cũ vẫn chạy.
- YAML parse được; không chứa `0.26.4`, không chứa bước tải Pyodide.

## 9. Fix picker `.ipa` (bổ sung ưu tiên cao)

- Triệu chứng: trên Safari iPhone, khung chọn file làm mờ file `.ipa`
  (xám, không chọn được) dù `accept=".ipa"` đã khai báo — quirk UTI của
  picker iOS. `app.js` trước đây không kiểm tra đuôi file (chỉ kiểm tra dung
  lượng) nên lỗi nằm ở picker, không phải JS.
- Fix (chỉ 2 file): (1) `index.html`: bỏ thuộc tính `accept` khỏi cả 2 input
  `#file-input` và `#batch-files`; (2) `app.js`: thêm `GAME_EXTS` +
  `gameExtOk()`/`gameExtError()` — kiểm tra đuôi sau khi chọn, chấp nhận
  `.ipa/.zip/.unity3d/.assets/.ttf/.json/.tar.xz` (không phân biệt hoa/thường),
  file khác thì `showError` thân thiện (không im lặng, không crash); reset
  `input.value` khi từ chối để chọn lại được; ở batch, file bị từ chối không
  làm mất thông báo lỗi khi vẫn có file hợp lệ. Giữ nguyên
  `accept=".json"` cho `#translate-json-file` và `accept=".sqlite,.sqlite3,.db"`
  cho `#kb-import-file` (2 loại này iOS chọn được bình thường).
- Kiểm chứng (harness `picker_ipa`): tĩnh — `#file-input` và `#batch-files`
  không còn `accept`; 2 input kia giữ nguyên; chạy — copy `game1.zip` thành
  `game1.ipa` rồi extract qua bridge trong Pyodide Node: **list 11 entry,
  extract 21 entry / 6 file** (bridge sniff nội dung ZIP, không dựa vào đuôi).
- **Chưa kiểm chứng hành vi picker thật trên Safari** — Joseph kiểm chứng sau
  khi đẩy (cần iPhone thật). Không tuyên bố đã kiểm chứng trên Safari.

## 10. Harness

- Mới `exp/verify_phase1_ci.mjs` — **8/8 PASS** (`phase1_ci_harness.json`):
  `conftest_merged`, `markers_count` (116 decorator), `date_test_after_touch`,
  `pages_yml`, `test_html`, `test_yml_two_jobs`, `pyodide_pytest_smoke`,
  `picker_ipa`.
- Cũ (chạy lại, không vỡ gì): `verify_module_worker.mjs` **17/17**,
  `verify_patch01_web.mjs` **8/8**, `verify_parity.mjs` **14/14**,
  `verify_limit_2gb.mjs` **6/6**.

## 11. Việc Joseph / coordinator cần làm (sau Phase 1)

1. **Coordinator: upload Release asset** `pb3-fixtures-v1.zip` (đúng 20 file
   ở §6, giữ đường dẫn tương đối từ `pb3/`) lên tag `fixtures-v1` của repo
   MunKai0408/MeoRocky bằng PAT (quyền Contents đã sửa xong; coordinator đã
   kiểm chứng ghi thành công trước đó). Tới khi Release này tồn tại, job
   `assets` sẽ ĐỎ — đó là trạng thái đúng, không phải lỗi workflow.
2. **Joseph: đổi Pages source sang GitHub Actions** trên github.com
   (Settings → Pages → Build and deployment → Source: GitHub Actions) để
   `pages.yml` có tác dụng; trước lúc đó deploy branch cũ vẫn chạy.
3. **Joseph (sau khi đẩy): thử picker `.ipa` trên Safari iPhone thật** với
   IPA 9 Kings (xem §9) và thử boot/extract trên trang live.

## 12. Việc chưa xong và vì sao

- **Fixture tổng hợp:** chưa làm (xem §3) — fixture Unity không sinh đơn giản
  được, fixture tổng hợp đơn giản sẽ fail assertion nội dung. Job `assets` +
  Release lo phần này; skip local lo phần còn lại.
- **Upload Release `fixtures-v1`:** không phải việc của Phase 1 (coordinator
  làm sau, xem §11.1). Không tự upload, không tự đẩy.
- **Kiểm chứng picker trên Safari thật:** cần iPhone của Joseph sau khi đẩy
  (xem §9, §11.3).
- **Chuyển hosting sang Cloudflare Pages:** cuối cùng, cần quyết định tài khoản
  của Joseph — không làm trong đợt này; `pages.yml` vẫn là GitHub Actions.
- Thứ tự phase sau (theo quyết định mới của Joseph): tích hợp worker pool +
  router dịch + diagnostics, rồi (a) tắt COI mặc định trong `sw.js`, (b) tách
  planner khỏi executor, (c) KB sang OPFS, (d) reader đọc ngẫu nhiên theo
  block, (e) Cloudflare Pages cuối cùng.

## Bổ sung: CI dùng Python 3.12, không phải 3.9 (2026-11-16 11:09)

**Lỗi gốc đã xác minh trực tiếp bằng API GitHub (parent, 11:07):** run
43799087701 (commit 0ab145b) — job "test" chết ở bước "Set up Python
3.9" = failure, mọi bước sau (cài lz4, kiểm tra fixture, chạy suite) đều
skipped, job chỉ chạy 6 giây; các run #1–#10 đều fail trong 8–11 giây.
**Chẩn đoán cũ "CI đỏ là đúng vì thiếu 28 fixture + 1 test ngày" là
SAI — test chưa bao giờ chạy.** Nguyên nhân: workflow chạy
`runs-on: ubuntu-latest` (nay là Ubuntu 26.04), `setup-python` không
còn Python 3.9 cho Ubuntu 26.04. File trên main lúc đó: dòng 54
`runs-on: ubuntu-latest`, dòng 62 `python-version: "3.9"`.

**Fix (áp dụng cho workflow 2 job mới của mục 1, cả 2 job đều dùng):**
- `test.yml`: job `unit` + job `assets` dùng `python-version: "3.12"`
  (job `pyodide-node` chỉ dùng Node, không đổi). Header file đã ghi rõ
  lý do: CI dùng 3.12 vì setup-python bỏ 3.9 trên Ubuntu 26.04; iSH
  của Joseph vẫn chạy 3.9 bình thường (Alpine 3.14).
- `pages.yml`: job `unit` + job `assets` trong workflow test-and-deploy
  cũng lên `"3.12"` (giống hệt test.yml), comment dòng 10 cập nhật.
- Giữ nguyên `runs-on: ubuntu-latest`, không pin ubuntu-24.04.
- Suite patch02 đã xanh 1562 test trên Python 3.12 trong sandbox nên
  đổi phiên bản này không làm suite đỏ thêm.

**Trạng thái CI dự kiến sau khi đẩy Phase 1:** job `unit` xanh;
job `assets` ĐỎ (đúng lý do đã biết: thiếu 20 file nhị phân cho tới
khi Release asset `fixtures-v1` tồn tại — test ngày đã sửa bằng
`os.utime` trong setUp nên không còn là lý do đỏ); job
`pyodide-node` xanh. **Run tiếp theo phải chạy trên 60 giây và sinh
artifact `test-full.log` — nếu vẫn fail dưới 15 giây thì đọc lại
annotation của run đó** (đó sẽ là lỗi setup mới, không phải lỗi
fixture/test).
