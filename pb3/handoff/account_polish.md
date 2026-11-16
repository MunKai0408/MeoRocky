# TÀI KHOẢN ĐỢT POLISH — WAVE 1/2/3 + MÀU SẮC + GIAO DIỆN CHẠY TEST + TIẾNG VIỆT CÓ DẤU

Đợt cải thiện toàn diện cho PB3 v3.1 (Claude 01–05b + bộ chạy test), làm theo
`~/workspace/polish_impl_brief.md` (6 audit + công việc dấu tiếng Việt), theo thứ tự
Wave 1 (bug thật) -> Wave 2 (UX) -> Wave 3 (runner), cộng 3 yêu cầu mới của Joseph:
màu sắc CLI, giao diện chạy test, làm đẹp toàn bộ bề mặt người dùng. Sandbox DONE
2026-11-15: **756 test OK** (`Ran 756 tests in 29.8s OK (skipped=1, lỗi=0, fail=0)`,
755 passed, 1 skipped). iSH: NOT RUN — zip này THAY zip 05b sandbox, Joseph chỉ
kiểm chứng 1 lần (mục 10).

Quy tắc xuyên suốt (D-POLISH-1..5): không bịa UnityFS v9, không plugin định dạng mới,
không mở REBUILD_ALL, không nâng dependency, không breaking API/hành vi; code tương
thích Python 3.9 (không `match`, không `tomllib`); không có câu `import UnityPy`/
`import lz4` trực tiếp trong mã PB3 (chỉ importlib); mọi lệnh dài cho Joseph đều có
thanh tiến độ. Mục nào không làm được an toàn + có test thì BỎ và ghi rõ (mục 9).

## 1. Đã làm (sandbox, 2026-11-15)

### Unity — `sdk/unity.py`, `sdk/lz4pure.py`, `services/unity_tool.py` (U1–U15)
- **U1 (decision #81):** `unity inspect` trên container bền bỉ từng entry: bundle lỗi
  -> dòng PlanRow "unsupported" + warning + cờ partial (E_PARSE_PARTIAL), exit OK;
  inspect trực tiếp một file bundle đơn lẻ vẫn hard-abort E_UNSUPPORTED_FORMAT;
  patch vẫn từ chối entry không hỗ trợ.
- **U2:** `unity patch` + `mod` in `write_mode_used` (biết patch dùng PATCH_INPLACE hay
  REBUILD_SECTION).
- **U3:** `parse_bundle` kiểm kích thước từng block + tổng (`check_entry_size`,
  `check_total`) trước khi đọc — chống bom.
- **U4:** LZ4 frame `dict_id != 0` -> E_UNSUPPORTED_FORMAT; bound block
  `max_block + max_block//255 + 16`.
- **U5:** `build_serialized` + `_rebuild_assets_section` giữ NGUYÊN thứ tự object gốc.
- **U6:** typetree stack underflow -> E_CORRUPT (không crash).
- **U7:** `_verify_assets` kiểm byte_start%4 + data_offset, chỉ khi
  `write_mode_used == "REBUILD_SECTION"`.
- **U8:** `PatchRequest.from_container` + cảnh báo re-sign khi unit là bundle trong
  container.
- **U9:** `_resolve_vpath` phát hiện đoạn bundle và gợi ý `'<entry>::<tên entry>'`.
- **U10:** `apply_patch` giữ `extra_data_flags & FLAG_OLD_WEB` + `pad_block_data`
  (chỉ an toàn với revision mới, vd Unity 6 "6000.0.23f1" — cờ pad 0x200 trùng cờ
  mã hóa cũ ở revision 2019).
- **U11:** `SerializedFile._text_cache` lazy (không parse TextAsset khi không cần).
- **U12:** `text_assets(warnings=None)` bỏ qua object hỏng không phải đích + ghi chú;
  `find_text_asset(path_id=...)` raise E_CORRUPT kèm path_id.
- **U13:** LZ4 pure slice-copy cho match không chồng lấp (offset >= match_len).
- **U14:** dedupe plan qua `_dedupe()` + `_level_rank()`.
- **U15:** round-trip SerializedFile v15/16/18/19/20/22 qua `build_serialized` từ mẫu
  `sample_v21.assets` (parse lại, assert version + thứ tự object + nội dung TextAsset).

### Plugin system — `registry/` (C1–C11)
- **C1:** `_import_instance` + bước 4 `discover` bắt BaseException (trừ
  KeyboardInterrupt) -> E_PLUGIN_INVALID + disabled.
- **C2:** bước 1–2 `discover` defensive + `_proven_level` bắt Exception.
- **C3:** `Registry.add_report` copy record trước khi sửa, giữ lý do cũ.
- **C4:** `_path_wrapper` kiểm kwargs `path`/`src`/`dst`.
- **C5:** guard vá `os.kill`/`killpg`, `socket.socketpair`/`fromfd`, `pty.spawn`/`fork`.
- **C6:** scan theo dõi `from os import system`, alias, `__import__` qua attribute;
  STRING_MODULES thu hẹp (chỉ hằng bare mới flag).
- **C7:** `name_signal` dùng `@lru_cache(maxsize=128)` (cũng là cache regex cho X14).
- **C8:** conformance — `_check_table` kiểm mọi entry.source là SourceRef; kiểm
  container iter+read; `_check_write` kiểm âm (lật 1 byte -> verify PHẢI fail; bắt
  được test cũ dùng verify giả `{"ok": True}` — đã sửa test dùng verify thật).
- **C9:** `arbitration.contender_from_record`.
- **C10:** `Weights.from_config` bọc KeyError/TypeError/ValueError/AttributeError ->
  E_CONFIG_INVALID.
- **C11:** `plugin test` trên plugin đang tắt thêm ghi chú vào report (không đổi
  verdict); `load_fixture(..., notes=...)` ghi tên file xấu bị bỏ qua.

### Container/VFS — `sdk/containers.py`, `services/container_tool.py` (V1–V23)
- **V1:** TarVFS gmtime wrap -> date_time=None. **V2:** `_unsafe_name_reason` từ chối
  `a//b`, `a:b`, `a/./b`. **V3:** `_rejected` dọn stale + dedupe theo tên.
- **V4:** `NestedVFS.inventory(path)` giải quyết FULL path thành container con.
- **V5:** `rebuild_zip` giữ flag_bits, create/extract_version, comment; entry thay thế
  được clear bit mã hóa. **V6:** lỗi tar/zip kèm `str(exc)` cắt ngắn.
- **V7:** `_ROStream.seek` kiểm offset/whence. **V8:** `_raw_at` dùng `self._data` đã giữ.
- **V9:** slice cắt cụt -> E_CORRUPT. **V10:** SingleStreamVFS đọc tiền tố bỏ qua
  `_count_size`; tar probe chỉ đọc 4 member đầu. **V11:** TarVFS lazy iteration + abort
  sớm.
- **V12:** rebuild_zip giới hạn replacements, trùng tên chỉ thay lần đầu + cảnh báo,
  `expected_size` đúng, `_verify` CRC streaming. **V13:** `stat()` trả `VStat(size=0)`
  cho thư mục. **V14:** slice BZIP2/LZMA thô qua decompressor stdlib.
- **V15:** `support_matrix` + lệnh `container formats`. **V16:** hỗ trợ `.tar.bz2`.
- **V17:** `container list [entry-or-prefix] --limit/--prefix` + "…còn N entry" +
  cảnh báo tỉ lệ gộp. **V18:** CLI container dùng MỘT Limits từ config app.
- **V19:** ZIP có dữ liệu prepended vẫn mở được (fallback). **V20:** tar.gz hỏng vs
  không-phải-tar (probe 64KiB đầu). **V21:** cache NestedVFS key `(id(parent), seg)` ->
  (child, digest), chỉ hash khi miss. **V22:** `container read` copy chunked qua
  `open_stream` vào `AtomicWriter` (exclusive). **V23:** `Limits.check_ratio` nối vào
  đường cảnh báo tỉ lệ (chỉ cảnh báo).

### Core + SDK + model (F1–F31, chọn lọc)
- **F1:** `LimitedVFS.open_stream` đếm byte (`_CountingStream`, quá
  max_entry_bytes -> E_TOO_LARGE). **F2:** `_ReadOnlyStream.getbuffer()` bị chặn
  (E_PLUGIN_FORBIDDEN).
- **F3:** tomlmini lỗi `chr()` -> TomlError; `config.parse_toml` parity (tomllib cũng
  từ chối datetime/inf/nan/hex). **F4:** `AtomicWriter` disk_factor mặc định 1.0 -> 2.5.
  **F5:** `run_tests.py` `_mod_name()` giải quyết đường dẫn tuyệt đối.
- **F6:** `arbitration.thresholds_from_config()` (cặp sai -> cảnh báo + mặc định
  0.80/0.55). **F7:** `load_history` cap 64KiB + bắt OverflowError. **F8:** session log
  dùng RotatingFileHandler. **F9:** `find_stale_temps` tìm cả thư mục `run-*` theo
  mtime (chỉ cách ly). **F10:** local.toml hỏng salvage từng phần (bỏ tối đa 10 dòng
  lỗi theo số dòng, cảnh báo kèm số dòng). **F11:** `list_dir_entries` xếp TRƯỚC rồi
  mới cắt 500. **F12:** đã thỏa sẵn (decision #96). **F13:** `describe_bytes` chỉ
  hash 64KiB đầu (`sha8-prefix=`). **F14:** `save_history` tên tmp duy nhất (pid +
  token). **F15:** `setup_logging` chỉ đóng handler PB3 sở hữu. **F16:** KeepAlive
  trạng thái trung thực (thêm S_STOPPING, `stopping`/`thread_alive`).
  **F17:** probe ghi dùng tempfile + try/finally. **F18:** `detect_environment(...,
  on_module=...)` + `print_env_report` báo tiến độ từng module khi deep.
- **F19:** `LocTable.validate()` + gọi trong `sdk/testing.py::_check_table`.
  **F20:** `to_key_values(on_conflict="last"|"first"|"error")` + `to_key_values_strict()`.
  **F21:** `LocTable.from_key_values`, `SourceRef.from_vpath`, `Candidate.for_path`,
  `PluginContext.tempfile()`. **F22:** `Locale.compile()`/`validate()` + chuẩn hóa
  alias (lowercase/strip/dedupe). **F23:** `InnerPath.container` kiểm traversal,
  `FileRef` từ chối size âm/sha256 không hex, `Project.validate` cảnh báo trùng id
  chéo nhóm. **F24:** input_extra ghi rõ RESERVED trong OPTIONS + default.toml.
  **F25:** manifest — limits lạ -> `ignored_fields` (decision #97); magic offset <=
  1 MiB; `requires` validate theo đặc tả module. **F26:** conformance kit — `_Runner.call`
  lỗi -> FAIL (không crash), báo số ứng viên chưa kiểm, `run_checks` tránh copy
  `dict(files)`, tempdir qua `safe_io.make_tempdir`, `format()` dùng `display_width`.
  **F27:** helper `format_bar()` chung cho `format_progress` + `overall.format_overall`.
  **F28:** `App.job` stage mặc định 30.0 -> 3.0s. **F29:** `choose_path` input_fn mặc
  định None (giải quyết lúc gọi). **F30:** dùng `MAX_DETAIL_CHARS` từ core.errors.
  **F31:** `diag()` severity error log đúng mức error; `for_plugin()` copy
  LocaleRequest; MemoryVFS/LimitedVFS slice thống nhất (byte thực đọc);
  `example_kv` probe/parse thống nhất; `dec()` giữ escape lạ; `verify()` `is` -> `==`.

### Wave 2 — UX (X1–X16)
- **X1:** `TestRealCli` 6 spawn -> 3 + bản sao dùng chung (setUpClass); new/disable/
  enable chạy in-process qua bootstrap thật; subprocess chỉ giữ cho 2 lần `plugin
  list` (chứng minh state sống qua tiến trình mới) + case lỗi. 37 test/1.7s.
- **X2:** `format_error` nhận `log_path` -> in dòng "Nhật ký: <đường đầy đủ>"; site
  còn lại ở `plugin_manager.toggle_record` đã nối log path. (2 handler bootstrap ở
  `menu.py`/`ui/cli.py` chưa có app nên không có log — không áp dụng được.)
- **X3:** sửa 4 gợi ý sai: E_NO_CANDIDATE, E_UNSUPPORTED_FORMAT (nói rõ v9+/mã hóa),
  E_WRITE_DISABLED (chỉ rõ `--allow-write`), E_NOT_IMPLEMENTED (trỏ PROGRESS.md thay
  vì file không có).
- **X4:** mọi raise trong unity/containers có `where=` (decision #89) + test meta
  `tests/test_rules_where.py`.
- **X5:** `plan_bytes(..., on_entry)` báo tiến độ từng entry; `unity inspect` +
  `container list` nối vào `app.report_progress` (không-op khi không có overall;
  không đổi output khi PB3_PROGRESS=0).
- **X6:** `term_width()` về `core/color.py` (điện thoại 30..44 cột); `_print_plan`
  wrap mọi dòng theo width, giữ đầu+đuôi đường dẫn dài (`_head_tail`, 13+…+14) thay
  vì chỉ đuôi; cắt ngắn lý do plugin thống nhất (`_reason_width`).
- **X7:** help: support/kb ghi rõ "(chưa triển khai)", diagnose có metavar ĐƯỜNG_DẪN,
  cờ riêng unity/mod (`--backend/--object/...`) thấy trong `--help` cấp cao.
- **X8:** menu [3] hiện "đã nối dịch vụ…; ma trận hỗ trợ (chưa có)" (decision: nhãn
  giữ nguyên theo đặc tả 12.1, test khóa).
- **X9:** đồng bộ tài liệu (PROGRESS.md, TIEN_DO_VA_NHIEM_VU.md, README.txt,
  docs/TEST_STRATEGY.md, CHANGELOG.txt) lên 756 test + v8/v23 + polish;
  `CLAUDE03_CONTINUE_HERE.md` chuyển từ gốc vào `handoff/`.
- **X10:** test còn thiếu (xem mục 7): fixture IPA lớn 50 entry/bundle v8 ~5MB, test
  message op="write", test help text, test traversal file_dialog, mở rộng danh sách
  API Python 3.9 cấm. (Test timeout --deep ĐÃ CÓ sẵn:
  `test_deep_probe_timeout_and_crash`.)
- **X11:** CHANGELOG gọn (mục polish ngắn: đổi gì + decisions; chi tiết ở đây);
  timeout isolation dùng `subprocess_timeout`; `_shared_proj` cho mọi lớp RealCli.
- **X12:** prefix-probe + `plan_max_entry_bytes` (decision #90).
- **X13 B1:** cache duyệt manifest tĩnh (decision #91). **B2:** nạp có đích
  (decision #92).
- **X14:** ĐO RỒI BỎ pre-index (decision #93).
- **X15:** skeleton ghi được `--write` (decision #94).
- **X16:** evidence version (decision #95).

## 2. Bộ chạy test (riêng — Wave 3, `run_tests.py` + `tests/tiering.py`)

- **Timeout = ERROR, không phải skip** (decision #85): test chạy trong daemon thread
  riêng, `join(timeout)` hết hạn -> `addError` TimeoutError + `timeouts += 1`, thread
  bị bỏ lại, pipeline chạy tiếp. Không lưu baseline khi timeouts>0; `PB3_FAST=1`
  cũng không lưu baseline. Timeout mặc định 180s, x5 trên iSH.
- **Hysteresis tầng** (decision #87): ngân sách giữ 1.0s; vượt 2 lần liên tiếp mới
  promote fast->slow, slow dưới ngân sách 2 lần full liên tiếp mới demote; profile
  cũ (số trơn) vẫn đọc được; full run tỉa key cũ + ghi `_meta`.
- **Tiến độ + Ctrl+C:** `--plan` nhẹ (không nạp test, đếm qua `_Facts`); `--changed`
  (deleted file -> không lọc, chạy đủ tầng đang chọn — decision #88; cảnh báo khi
  test ảnh hưởng nằm ngoài tầng); Ctrl+C lưu mốc từng phần (chỉ test đã pass) + in
  "Đã hủy ở test:…" + "Đã chạy X/Y test, còn lại Z test." + rc 130.
- **Phân loại:** `plan_counts()` đếm không cần nạp test; `_deps` nhận cả
  `importlib.import_module("...")` literal.
- **Test:** `tests/test_runner.py` (20 test: 15 meta + 5 slow) — hysteresis
  promote/demote/format cũ/prune+_meta, timeout mặc định/scale, ThreadedSuite
  timeout-is-error + pipeline tiếp tục, merge success/failure/skip/error, Ran line
  byte-identical + bảng tóm tắt, timeout->ERROR không lưu mốc, Ctrl+C rc 130,
  --changed (file xóa + cảnh báo slow/meta ngoài tầng).

## 3. Tiếng Việt có dấu (riêng)

- Công việc dấu ĐÃ XONG từ trước đợt này (`core/model.py` + guard test
  `tests/test_vietnamese_diacritics.py`, 3 test, meta tier) — đợt này KHÔNG làm lại,
  chỉ kiểm tra guard test vẫn xanh và giữ dấu đúng trong mọi message đã chạm vào
  (mọi chuỗi mới viết trong đợt polish đều có dấu đàng hoàng, kể cả gợi ý lỗi X3,
  ghi chú evidence X16, cảnh báo runner).
- Mọi test mới trong đợt này assert bằng chuỗi có dấu (vd "không có quyền ghi",
  "không nằm trong phạm vi nạp", "không ghi version").

## 4. Màu sắc CLI (riêng)

- **Một module duy nhất `core/color.py`** (decision #82): palette PASS xanh / FAIL
  đỏ / SKIP vàng / cảnh báo vàng / lỗi đỏ đậm / tiêu đề cyan; ký hiệu ✓/✗/↷/!/•;
  `display_width` (ANSI-aware, CJK=2), `wrap_text`, `truncate_text`, `kv_table`,
  `term_width`. Không mã ANSI rải rác (test meta `TestNoStrayAnsi` khóa).
- **Bật/tắt** (decision #83): `NO_COLOR` (có mặt và không rỗng) thắng mọi thứ;
  khóa `ui.color` = auto (mặc định, chỉ TTY) / on (ép cả khi pipe) / off (không bao
  giờ). Khi tắt màu, helper trả chuỗi nguyên vẹn — output thuần byte-identical,
  log/report file giữ chữ thuần.
- **Phạm vi tô màu:** menu (`menu.py`, `ui/screens.py`, `ui/handlers.py`,
  `ui/cli.py`), service in màn hình (`services/unity_tool.py`,
  `services/container_tool.py`, `services/plugin_manager.py`), thanh tiến trình
  (`core/progress.py`, `core/overall.py`, lớp `R` trong `run_tests.py`), tóm tắt
  cuối `run_tests.py`, `sdk/testing.py` (dòng KẾT QUẢ theo verdict).
- **Kiểm chứng TTY thật** (qua `script -qec`): menu tiêu đề cyan, dịch vụ đã nối
  xanh, chưa triển khai vàng; `NO_COLOR=1` trên TTY -> 0 mã escape; output pipe
  không có escape.
- **Test:** `tests/test_color_display.py` (13 test, meta): TTY giả có màu / không
  TTY sạch / NO_COLOR thắng `on` / auto cần TTY / 5 role code khác nhau / rule rỗng
  khi tắt / `configure_from_config` / CJK width / wrap 30 cột / kv_table thuần == strip
  bản màu / không mã ANSI lạc.

## 5. Giao diện chạy test (riêng)

- Thanh tiến trình có màu + số đếm (`[123/665] 18%`, % xanh), trạng thái từng test
  gọn (✓ pass / ✗ fail / ↷ skip, màu tương ứng), tên tier đang chạy hiển thị rõ,
  ETA từ hồ sơ thời gian, tên test cắt gọn giữ phần tên test (bỏ class khi hẹp).
- Cuối suite: bảng tóm tắt gọn đẹp (kv_table, rộng 40, vừa màn hình điện thoại):
  Tầng/Tổng/Đạt/Lỗi/Fail/Bỏ qua/Thời gian/Timeout/Chậm nhất + dòng "MỚI CHẬM (n)"
  / "ĐÃ HỒI PHỤC (n)" + khối "VƯỢT NGÂN SÁCH" cũ giữ nguyên.
- **Giữ nguyên quy tắc cũ** (decision #84): chỉ đẹp khi TTY; pipe/log thì thuần
  không escape; dòng `Ran …` giữ nguyên y hệt ở chế độ thuần (bảng là output BỔ
  SUNG); tôn trọng NO_COLOR. Khi không phải TTY: dòng thưa mỗi 50 test, không escape.
- Test meta trong `tests/test_runner.py`: Ran line byte-identical + bảng có mặt +
  dòng bảng <= 48 display width; TTY check bằng `script -qec` (`[1/19] 5%` % xanh,
  ✓ xanh sau mỗi test, bảng cyan).

## 6. Menu + toàn bộ bề mặt (riêng)

- **Một bộ helper dùng chung** cho màu + bố cục (`core/color.py` — cùng module với
  mục 4, không bộ thứ hai): mọi màn hình dùng `title`/`rule`/`status`/`kv_table`/
  `wrap_text`/`term_width` chung; bảng màu + kiểu hiển thị nhất quán toàn dự án.
- **Phạm vi:** `menu.py` (menu chính, menu con, prompt, help), TẤT CẢ lệnh CLI
  (`unity`, `container`, `plugin`, `mod`, …), lỗi/cảnh báo/xác nhận, mọi chỗ in ra
  màn hình. Tiêu đề mục rõ, trạng thái/trạng thái lỗi dễ nhìn, dòng ngắn không tràn
  ngang (wrap theo `term_width`, 30..44 cột trên điện thoại), trạng thái menu [3]
  tính role trên TOÀN chuỗi trạng thái (không tô lẫn màu khi wrap).
- Giữ nguyên quy tắc: chỉ tô màu khi TTY, NO_COLOR, log/file/pipe thuần, không đổi
  ý nghĩa thông báo, test assert output vẫn pass (test chạy với màu tắt), Python 3.9.
- Test meta: `tests/test_color_display.py` (helper) + `tests/test_app_menu_cli.py`
  (help text: support/kb "(chưa triển khai)", `--write`, `ĐƯỜNG_DẪN`,
  `--allow-write`, `--backend`, `--deep` đều thấy trong `--help`).

## 7. Test mới — 88 test (668 -> 756)

| Nhóm | File | Số test mới |
|---|---|---|
| Wave 1 Unity/C03/C04/F | `tests/test_polish.py` (28) + `tests/test_sdk.py` (2) + 1 | 31 |
| Màu sắc | `tests/test_color_display.py` | 13 |
| Runner | `tests/test_runner.py` | 20 |
| Wave 2 (X4/X12/X10) | `tests/test_rules_where.py` (2) + `tests/test_polish.py` probe (4) + big IPA (2) | 8 |
| Wave 2 (X13/X15/X16/X10) | `tests/test_registry.py` (10) + `tests/test_conformance.py` (3) + `tests/test_errors.py` (1) + `tests/test_app_menu_cli.py` (1) + `tests/test_progress_env_dialog.py` (1) | 16 |
| **Tổng** | | **88** |

Test mới đáng chú ý: fixture IPA lớn tự sinh (50 entry, bundle UnityFS v8 ~5MB,
`TestPolishBigIpa`, slow) — inspect xong hết 50 entry, không partial, TextAsset
1001 tìm thấy; test cache hit/miss/tắt/hỏng/đổi code phụ (B1); test nạp có đích +
dedupe user thắng (B2); test skeleton --write PASS mục write thật (X15); test
evidence version khớp/lệch/thiếu (X16); test message op="write"; test help text;
test traversal file_dialog; mở rộng danh sách API Python 3.9 cấm (zoneinfo,
graphlib, dict union, `X | None` tổng quát, TaskGroup/ExceptionGroup, `except*`,
`sys.exception()`).

## 8. Bằng chứng sandbox (Python 3.12.3, 2026-11-15)

- Full suite sau cùng (sau đợt sửa lỗi iSH, mục 12): `python3 run_tests.py --tier full` ->
  `Ran 765 tests in 24.8s OK (skipped=1, lỗi=0, fail=0)` (764 passed, 1 skipped —
  test tín hiệu, bỏ qua ngoài main thread có chủ đích, decision #86). Trước đợt sửa:
  `Ran 756 tests in 29.8s OK (skipped=1, lỗi=0, fail=0)`. Mốc FULL xanh mới đã lưu
  (`reports/test_baseline.json`, `reports/test_times.json` — file sinh khi chạy test,
  KHÔNG đưa vào zip).
- Tầng (`--plan` nhẹ): tổng 765 — fast 655, ish 11, slow 46, meta 53, smoke 59
  (tăng so với 756: fast +4, slow +2, meta +3 — 9 test mới, xem mục 7 + mục 12).
- Kiểm chứng độc lập (giải nén zip ra thư mục mới, chạy full suite, md5 2 file đặc tả):
  md5 khớp gốc (`8867b7b7…` và `d634a74b…`); `Ran 765 tests … OK (skipped=1, lỗi=0,
  fail=0)` (764 passed, 1 skipped, 0 timeout; thời gian ~25s, dao động theo lần chạy).
  Zip: 157 entry, ~527KB. Zip được build lại sau lần sửa tài khoản cuối và kiểm chứng lại xanh.
- Lưu ý: lần kiểm chứng đầu tiên trong thư mục mới FAIL (229 lỗi) — nguyên nhân là
  `/tmp` (tmpfs 512MB) đầy do 274 thư mục `pb3test-*` cũ từ các lần chạy test trong
  phiên này chưa được dọn, KHÔNG phải do mã hay zip. Sau khi dọn `/tmp` (về 1%),
  chạy lại kiểm chứng độc lập PASS như trên. Vết dọn dẹp này ĐÃ SỬA trong đợt sửa lỗi
  iSH (mục 12, decisions #105–#106): atexit dọn thư mục TmpCase + thư mục dùng chung
  (setUpClass), `run_tests.py` quét thư mục cũ >1 giờ lúc khởi động; kiểm chứng: dọn
  sạch /tmp rồi chạy full suite, sau khi chạy xong còn 0 thư mục pb3*.
- TTY thật (pty qua `script -qec`): menu/runner tô màu đúng, NO_COLOR/pipe sạch
  escape. CLI `--write` end-to-end trên bản sao: `plugin new format demo_w --write`
  tạo plugin.py + kv.py, `plugin test demo_w` PASS (CHƯA KIỂM DỮ LIỆU, fixture rỗng).

## 9. Giới hạn + CỐ TÌNH KHÔNG LÀM (đã xem xét, có lý do)

- **Tối ưu TestImpact 114.7s** (D-POLISH-4): cần đo lại trên iSH; sandbox chỉ 0.01s
  — không tối ưu mù.
- **Pre-index `candidates_for`** (X14, decision #93): đo 5,8 µs/lần — không đáng.
- **Streaming mở container lồng / plugin probe (C04 #8/#9):** thay đổi kiến trúc,
  ngoài phạm vi polish.
- **Unity B1 full streaming `plan_bytes`:** chỉ thêm kiểm >128MB/cảnh báo theo audit.
- **UnityFS v9** (D-POLISH-5): không có bằng chứng v9 tồn tại (max đã kiểm chứng là
  v8); v9+ vẫn E_UNSUPPORTED_FORMAT.
- **Zstd/LZHAM, bundle mã hóa, 7z backend:** như cũ — không giải mã, không thêm backend.
- **REBUILD_ALL, plugin định dạng mới (Claude 06), nâng dependency, breaking
  changes:** ngoài phạm vi (D-POLISH-5).
- **Thiết kế lại isolation:** ngoài phạm vi.
- **C03 D5:** `plugin test` trên plugin đang tắt chỉ thêm ghi chú, không đổi verdict
  (cần quyết định người dùng).
- **Unity E3 verify-hash reuse:** chỉ làm nếu đo thấy chậm trên bundle lớn.
- **X2 (2 site):** handler bootstrap ở `menu.py:53`/`ui/cli.py:192` chưa có app nên
  không có log path — không áp dụng được, để nguyên.
- **X14 (đã đo), X10 --deep timeout test:** test ĐÃ CÓ sẵn từ trước
  (`test_deep_probe_timeout_and_crash`), không viết thêm.

## 10. Kiểm chứng trên iSH — ĐÃ CHẠY 23:36, CHƯA XANH (9 fail + 1 lỗi, chỉ ở test infra)

Zip này **THAY** zip 05b sandbox (`pb3_handoff_claude05b_sandbox.zip`) — Joseph chỉ
cần kiểm chứng 1 lần. Không bịa kết quả iSH.

**Kết quả lần 1 (2026-11-15 23:36):** `Ran 756 tests in 1221.9s FAILED
(skipped=5, lỗi=1, fail=9)` — 741 passed, 5 skipped, Timeout 0. Toàn bộ 10 lỗi đều
ở test hạ tầng (runner/màu/menu/pid-marker), KHÔNG có lỗi sản phẩm nào
(Unity/container/plugin đều pass). Danh sách + cách sửa: mục 12. Đợt sửa đã xong
trong sandbox (765 test xanh, mục 8); **Joseph chạy lại full suite với zip mới**
để xác nhận xanh trên iSH.

**Tin tốt — inspect IPA thật ĐÃ CHẠY (23:52):** `unity inspect
workspace/input/game.ipa` trên iSH đang liệt kê plan đầy đủ (GameObjects trong
level4, path_id 1341–1359+...). Đây là lần đầu inspect IPA thật 9 Kings thành công
(hồi C05 báo E_UNSUPPORTED_FORMAT vì v8) — nâng cấp v8 đã có tác dụng thật trên máy.

```sh
cd /root
# Giải nén zip polish đè lên /root/pb3 (giữ lại workspace/input/game.ipa nếu đã có)
unzip -o pb3_handoff_polish_sandbox.zip
cd /root/pb3
python3 run_tests.py --tier full
# Kỳ vọng (zip đã sửa lỗi iSH): Ran 765 tests ... OK (skipped=1, lỗi=0, fail=0) — 764 passed, 1 skipped
# (test tín hiệu bỏ qua ngoài main thread, có chủ đích).
# Nếu còn lỗi: cat reports/failures.txt (mới — bộ chạy test nay lưu danh sách lỗi ra file)
```

Lệnh Unity (05b, kiểm lại bằng zip này — v8 nay đã hỗ trợ, khác C05):

```sh
cd /root/pb3
python3 menu.py unity inspect tests/fixtures/unity/sample_v8.unity3d
python3 menu.py unity inspect tests/fixtures/unity/nested_v8.ipa
python3 menu.py unity inspect workspace/input/game.ipa
# Kỳ vọng mới (thay lỗi v8 của C05): CHẮC CHẮN với plan đầy đủ trên IPA thật 9 Kings.
python3 menu.py unity read tests/fixtures/unity/sample.unity3d resources.assets --object 1001
python3 menu.py plugin test zip
python3 menu.py plugin new format thu_nghiem --write
python3 menu.py plugin test thu_nghiem
# Dọn sau khi thử: rm -rf plugins_user/thu_nghiem tests/fixtures/thu_nghiem
```

## 11. Việc cho Claude 06 (chưa làm)

- Plugin định dạng văn bản + localization (prompt 06), dùng skeleton `--write` và
  conformance kit đã có.
- Support matrix + kho tri thức (Claude 09): `support`/`kb` hiện vẫn E_NOT_IMPLEMENTED
  (help đã ghi rõ).
- C03 D5: quyết định verdict `plugin test` trên plugin đang tắt (hiện chỉ ghi chú).
- Unity E3: đo verify-hash trên bundle lớn rồi quyết có reuse không.
- Đo lại TestImpact trên iSH (D-POLISH-4) rồi mới quyết có tối ưu không.

## 12. Sửa lỗi chỉ fail trên iSH (2026-11-15, sau kiểm chứng 23:36)

Kết quả iSH lần 1: `Ran 756 tests in 1221.9s FAILED (skipped=5, lỗi=1, fail=9)`,
Timeout 0. Toàn bộ 10 lỗi ở test hạ tầng, không lỗi sản phẩm. Đã sửa hết trong
sandbox; full suite nay 765 test xanh (mục 8). Decisions #99–#106.

### 1. `test_default_180s` — test sai, code đúng (decision #99)
- Lỗi: `900.0 != 180.0`. Timeout mặc định 180s nhân hệ số máy: x5 trên iSH
  (`/proc/ish`) là đúng thiết kế. Test cũ đòi đúng 180.0 dù ở đâu.
- Sửa test: assert `runner_timeout_s() == 180.0 * timeout_scale()` + test mới
  `test_default_base_180s_when_scale_1` (ép scale=1 thì đúng 180.0).

### 2. `test_ctrl_c_saves_partial_and_reports_130` — SIGINT giữa import giết tiến trình (decision #100)
- Lỗi: returncode `-2 != 130`. Con bị SIGINT lúc còn import (typing trên iSH rất
  chậm), trước khi handler Ctrl+C được cài.
- Đã kiểm chứng bằng thực nghiệm (sandbox): SIGINT giữa import -> traceback
  KeyboardInterrupt + returncode -2; handler ép `SystemExit(130)` -> 130.
  Nguyên nhân: KeyboardInterrupt không bắt trong lúc import làm tiến trình chết
  bởi tín hiệu thay vì thoát 130.
- Sửa: `run_tests.py` cài handler SIGINT sớm (chỉ khi chạy dạng `__main__`, trước
  import nặng) ép thoát 130; `main()` trả lại handler mặc định khi vào vùng chạy
  test để `except KeyboardInterrupt` còn lưu được mốc một phần; dòng kế hoạch in
  `flush=True` làm dấu hiệu sẵn sàng; test chờ con in dòng `TẦNG` (qua `select`,
  hạn `subprocess_timeout(300)`) rồi mới gửi SIGINT thay vì ngủ cố định 3s.

### 3. `test_slow_meta_excluded_warning` — timeout subprocess không scale (decision #101)
- Lỗi: subprocess `run_tests.py --changed` bị `TimeoutExpired` sau 120s trên iSH chậm.
- Sửa: mọi `timeout=` trong `tests/test_runner.py` (120/60) đổi sang
  `helpers.subprocess_timeout(...)` (x5 trên iSH). Lưu ý: hàm này đọc env của
  tiến trình cha nên không bị env `PB3_TEST_TIMEOUT_SCALE=0.01` của test
  timeout làm lệch.

### 4–7. Nhóm pid-marker — `acquire()` trả `interrupted` thay vì `other_running` (decision #102)
- Lỗi (4 test): `test_live_pid_with_same_start_is_other_running`,
  `test_old_marker_without_pstart_keeps_old_behavior`,
  `test_unreadable_proc_keeps_old_behavior` (`tests/test_hardening.py`,
  TestPidReuse), `test_other_running_does_not_steal_marker`
  (`tests/test_app_menu_cli.py`, TestBootstrap) — cùng triệu chứng
  `'interrupted' != 'other_running'`.
- Điều tra: KHÔNG phải do `/proc`/`pstart` (test 1 chứng minh `/proc/<ppid>/stat`
  đọc được trên iSH; test 2–4 không hề dùng `pstart`). Nguyên nhân duy nhất còn
  lại là cửa `pid_alive(os.getppid())` trong `acquire()`: các test này pass trên
  iSH trước đợt polish (chạy trên main thread) và chỉ fail sau khi runner mới
  (ThreadedSuite, decision #85) đưa test vào daemon thread — trên kernel usermode
  của iSH, `getppid()`/`kill(pid, 0)` từ luồng không phải main không còn trỏ đúng
  tiến trình cha. Không xác định được (A) hay (B) từ sandbox (cần probe 30s trên
  iSH, xem decision #102); cả hai đều được xử lý bởi cách sửa dưới đây.
- Sửa test: mock `runstate.pid_alive` -> `True` trong `TestPidReuse` (setUp) và
  trong `test_other_running_does_not_steal_marker` — các test này kiểm logic
  `pstart`, không kiểm `kill(2)`; KHÔNG skip trên iSH.
- Củng cố sản phẩm (lỗ hổng thật, một dòng): `pid_alive()` trước đây coi MỌI
  `OSError` (trừ `PermissionError`) là "đã chết" — chỉ `ESRCH`
  (`ProcessLookupError`) mới chắc chắn chết; errno lạ nay coi như còn sống để
  khỏi giành marker của phiên đang chạy. Test mới `TestPidAlive` (4 test):
  ESRCH -> chết, `PermissionError`/errno lạ -> sống, pid <= 0 -> chết.

### 8. `test_module_helpers_use_default` — test không hề tắt màu (decision #103)
- Lỗi: `C.paint("x", "pass")` trả `'\x1b[32mx\x1b[0m'` thay vì `'x'`. Trên iSH
  test chạy trong TTY nên màu bật theo thiết kế.
- Sửa test: bản cũ gọi `C.Color(force=False)` rồi bỏ — không hề đổi bảng mặc
  định. Nay dùng `C.set_mode("off")`/`("on")` (khôi phục mode cũ trong
  `finally`, xóa `NO_COLOR` khi kiểm) để kiểm thật rằng helper cấp module dùng
  bảng mặc định.

### 9. `test_width_never_exceeded_and_content` — mã ANSI trong thanh tiến độ (decision #103)
- Lỗi: `assertIn("#####-----", ln)` trượt vì dòng có mã ANSI xen vào
  (`'[\x1b[32m#####\x1b[0m\x1b[2m-----\x1b[0m] 50% ...'`) khi chạy trên TTY.
- Sửa test: `strip_ansi(ln)` trước khi assert nội dung (độ rộng đã dùng
  `display_width` vốn bỏ qua ANSI nên không đổi).

### 10. `test_help_text` — help bị wrap ở terminal hẹp (decision #104)
- Lỗi: needle `'kho tri thức game (chưa triển khai)'` không tìm thấy vì help
  wrap giữa chừng (`"triển\nkhai"`).
- Sửa test: chuẩn hoá khoảng trắng (`" ".join(txt.split())`) rồi mới so khớp,
  không đổi nội dung help. Mô phỏng `COLUMNS=40` lộ thêm test cùng họ
  `test_menu_has_exact_spec_items` (nhãn menu gãy dòng khác nhau theo chiều rộng)
  — sửa cùng cách (nhãn `[1]` gộp thành một cụm sau chuẩn hoá).

### Tính năng thêm cùng đợt
- **`reports/failures.txt` (decision #105):** bộ chạy test lưu danh sách lỗi ra
  file mỗi lần chạy (dòng tổng kết + từng test `FAIL:`/`ERROR:` + dòng ngoại lệ
  cuối). Khi xanh, file vẫn ghi dòng tổng kết + "OK — không có test lỗi." để khỏi
  nhầm file cũ. Test: `TestFailuresFile.test_failures_saved_to_file` (chạy bản sao
  có 1 test cố tình fail, kiểm file có đủ tên test + `AssertionError` + dòng Ran).
- **Dọn thư mục tạm (decision #106):** `TmpCase` đăng ký thư mục vào sổ atexit
  (`tests/helpers.py`: thoát bình thường/Ctrl+C thì dọn nốt); thư mục dùng chung
  của 4 lớp slow (`pb3test-unity-`, `pb3test-cont-`, `pb3test-cls-`,
  `pb3-realcli-`) đăng ký qua `register_tmp_dir`/`unregister_tmp_dir`;
  `run_tests.py` quét thư mục cũ >1 giờ lúc khởi động
  (`_sweep_stale_tmp_dirs`, tiền tố `pb3test-`/`pb3-runner-`/`pb3-realcli-`).
  Hai `tempfile.mkdtemp()` trần trong `tests/test_conformance.py` nay có
  `addCleanup`. Kiểm chứng: dọn sạch /tmp rồi chạy full suite, sau khi chạy xong
  còn 0 thư mục pb3*. Test: `TestSweepStaleTmpDirs` (2 test),
  `TestTmpCleanup` (1 test: tiến trình con thoát không tearDown thì thư mục vẫn mất).

### Kiểm chứng mô phỏng iSH (sandbox, Python 3.12.3)
- pty thật (`script -qec`, có TTY): `test_color_display` + `test_overall_keepalive`
  + `test_hardening` — 63 test OK.
- `COLUMNS=40`: `test_app_menu_cli` — 30 test OK (sau khi sửa cả menu test).
- `PB3_TEST_TIMEOUT_SCALE=5`: `test_runner` — 24 test OK (gồm `test_default_180s`
  assert 900.0 và test Ctrl+C).
- Full suite: `Ran 765 tests in 24.8s OK (skipped=1, lỗi=0, fail=0)`, Timeout 0.
- Chưa kiểm mô phỏng: cơ chế (A)/(B) của nhóm pid-marker (cần probe trên iSH);
  thời gian chạy thực trên iSH (suite 765 test sẽ lâu hơn 1221.9s lần trước).

## 13. Tối ưu tốc độ quét/inspect/mod (2026-11-16, decision #107–#116)

Bằng chứng chậm (đo thật trên iPhone 11 Pro Max, iSH): `unity inspect
workspace/input/game.ipa` in ra màn hình đi từ path_id 1359 (23:52) tới 6438
(00:01) — ~9 object/giây, vẫn ở level4, hết cả IPA ước tính 30+ phút; menu Mod
đứng ở 98% 1/1 "còn ~0s". Nguyên nhân đo được trong sandbox (không đoán mò):
in plan tốn gấp 8–15 lần lập plan; thanh tiến độ bị tắt khi chuyển hướng ra
file nên `wc -l` không thấy gì trong lúc quét; lần mod thứ hai quét lại từ đầu.

### Đo baseline (sandbox, Python 3.12.3; fixture IPA 21,9MB, 27 entry, 73.025 dòng plan)
- `plan_bytes` (chỉ lập plan): 0,481s. `_print_plan` -> StringIO: 7,413s.
- `parse_serialized` (1 file level, 300 type + 7.000 object): 0,052s.
- `_plan_assets_rows`: 0,022s. `sha256` (2,2MB): 0,002s.
- cProfile `_print_plan`: `display_width` 10,66s tottime / 1.752.661 lần gọi ->
  `east_asian_width` 46,7 triệu lần. Gốc: `wrap_text` gọi `display_width` trên
  cả dòng ứng viên sau mỗi từ (O(số từ x độ dài dòng)), `display_width` gọi
  `east_asian_width` từng ký tự bằng vòng lặp Python.
- Giải nén đã nhanh sẵn (không sửa): AUTO dùng C `lz4.block` khi có binding
  (sandbox lz4 4.0.2; iSH lz4 4.4.5), LZMA dùng C `lzma`; plan không dùng
  UnityPy. Kiểm chứng: tắt `lz4pure.decompress_block` thì plan fixture LZ4
  vẫn xong (decision #112).

### Đã làm + số đo sau (cùng fixture, sandbox)
| Chặng | Trước | Sau | Ghi chú |
|---|---|---|---|
| `plan_bytes` (chỉ lập plan) | 0,481s | 0,256s | 1,9x |
| `_print_plan` -> StringIO | 7,413s | 0,698s | 10,6x, byte y hệt |
| `parse_serialized` (1 level) | 0,052s | 0,032s | 1,6x |
| `_plan_assets_rows` (1 level) | 0,022s | 0,006s | 3,7x |
| Tổng inspect (lập + in) | ~7,9s | ~0,95s | ~8,3x |
| Cache plan: lần 1 (miss) / lần 2 (hit) | 0,73s / — | 0,73s / 0,34s | hit 2,1x (sandbox); trên iSH chênh lệch lớn hơn nhiều vì quét đủ mất hàng phút còn hit chỉ còn sha256 + đọc JSON |

1. **In plan nhanh (`core/color.py`, decision #107):** `display_width` có
   nhánh nhanh (chuỗi ASCII thuần không mã ANSI -> `len(text)`; ký tự
   < U+1100 không bao giờ là W/F nên trả 1 ngay, chỉ CJK/emoji mới tra
   `east_asian_width` có cache); `wrap_text` cộng dồn độ rộng từng từ thay
   vì quét lại cả dòng ứng viên (dòng có mã ANSI giữ vòng lặp cũ y hệt);
   `truncate_text` dùng chung `_char_width`. Kết quả byte y hệt: kiểm vi sai
   với bản cũ trên corpus (ASCII, tiếng Việt, CJK, emoji, ANSI) x 9 chiều
   rộng + trên đủ plan 73.025 dòng (6,5MB output, byte-identical).
   Test: `TestPrintDifferential` (3 test), `TestPrintSpeed` (guard < 1,2s
   cho 24k dòng; bản cũ ~2,4s).
2. **Bảng object đọc hàng loạt (`sdk/unity.py`, decision #108):**
   `struct.iter_unpack` (C) thay cho ~8 lần gọi `_Reader` mỗi entry, cho dải
   version mà kích thước entry == stride (v<11, v17–21: 20 byte; v>=22:
   24 byte). v11–16 giữ vòng lặp cũ (entry có script_idx + đệm, entry cuối
   không có đệm đuôi nên vùng bảng không thẳng hàng). Kiểm tra (type_id lạ,
   path_id trùng, vượt data section) y hệt vòng lặp cũ, cùng thông điệp lỗi;
   `r.pos` cuối y hệt. Test vi sai fast-vs-slow trên bảng đóng gói tay
   (v17/v22 + các case lỗi + bảng cắt cụt) và parse đủ file v17/v21/v22/v23.
3. **Chỉ mục `path_id` (`SerializedFile.get_object`, decision #109):** dict
   lười `_by_path_id`, tra cứu O(1) thay vì quét tuyến tính cả bảng object
   mỗi TextAsset. Test: cùng object với quét tuyến tính, thiếu -> E_INPUT_NOT_FOUND.
4. **Đọc đầu TextAsset (`read_text_asset_head`, decision #110):** plan chỉ
   cần tên + độ dài script (số ký tự, vì script decode UTF-8) nên không giữ
   cả script trong bộ nhớ; script ASCII đếm bằng số byte không cần decode,
   script không phải UTF-8 hợp lệ thì raise PB3Error như bản đọc đủ, đệm căn
   4 cuối script kiểm tra y hệt. Test: head == (tên, độ dài) của bản đọc đủ
   trên 6 case (gồm script 5MB, script rỗng, tên tiếng Việt) + 7 case lỗi
   (cắt cụt, độ dài âm/vượt, tên/script không UTF-8) cho cùng mã lỗi.
5. **Cache plan (`services/unity_tool.py`, decision #111):**
   `workspace/cache/plan_cache.json`, khóa = sha256 toàn bộ file + kích thước
   + chế độ backend + phiên bản PB3 + phiên bản khuôn dạng cache; file đổi
   một byte cũng trượt cache. Chỉ cache file >= 1MB; tối đa 8 plan / 32MB,
   đuổi mục ít dùng nhất (LRU theo lần ghi). Hit không ghi lại file cache
   (ghi lại 17MB JSON chỉ để cập nhật last_used thì mất gần bằng quét lại).
   Cả ba chỗ gọi plan (`unity inspect`, `mod` CLI, menu Mod) đều dùng chung
   `_cached_plan`; khi hit thì in dòng mờ "dùng plan đã lưu (cache) — file
   không đổi." Cache hỏng/sai version/thiếu thư mục -> bỏ qua, quét lại,
   không làm hỏng lệnh. Test: `TestPlanCache` (hit không gọi lại `plan_bytes`,
   đổi 1 byte giữa file cùng kích thước -> miss, cache hỏng -> quét lại,
   round-trip giữ nguyên plan, đuổi LRU, file nhỏ không cache, hit < 2s).
6. **Tiến độ khi chuyển hướng (decision #113):** thanh Overall bị tắt khi
   stderr không phải TTY (theo thiết kế), nên lúc quét file chuyển hướng
   `> /tmp/inspect.txt 2>&1` thì file không có dòng nào cho tới khi in plan
   (đó là lý do `wc -l` chỉ thấy 3 dòng sau ~3 phút — không phải máy đứng,
   mà là không có gì để đếm). Nay `_inspect_progress` in 1 dòng ra stderr
   mỗi entry ("quét i/N: <entry>") khi thanh tắt; khi thanh bật (TTY) thì
   không in thêm. Plan vẫn ra stdout, không lẫn dòng tiến độ.
7. **Menu gọn (decision #114):** menu Mod chỉ in verdict + tổng số dòng +
   các hàng TextAsset (thứ duy nhất menu patch được), tối đa 50 hàng, kèm
   dòng "… và N TextAsset khác (xem đủ: unity inspect <file>)". `unity
   inspect` CLI vẫn in đủ plan (không đổi). Lý do: in đủ 30.000 dòng lên
   màn hình điện thoại không dùng được (kết xuất terminal trên iSH
   ~27 dòng/giây). Hàng TextAsset dùng chung `_plan_row_lines` với bản đủ
   nên định dạng giống hệt. Test: `TestCompactPlan` (4 test).
8. **Thanh 98% ở menu (giải thích, decision #115):** thanh Overall ước tính
   theo thời gian và chặn ở 98% khi quá thời gian dự kiến — công việc thật
   (in 30k dòng) vẫn chạy phía sau. Không phải treo; menu gọn + in nhanh
   nay làm bước này nhanh hơn hẳn.

### ĐÃ ĐO NHƯNG KHÔNG LÀM (ghi rõ lý do)
- **Lazy type tree:** chỉ ~0,024s/file — không đáng.
- **Lazy `source_hash`:** sha256 không nằm trong top hotspot.
- **Tái dùng verify-hash (Unity E3):** verify chỉ ở đường patch, không phải
  nỗi đau lúc quét — chưa làm.
- **Streaming `plan_bytes` (B1):** không giảm CPU, chỉ đổi cảm nhận tiến độ;
  sửa tiến độ chuyển hướng (mục 6) đã đủ.
- **Lưu cache dạng mảng thay vì dict JSON:** hit còn tốn đọc JSON 17MB
  (~0,34s sandbox cho 73k dòng); dạng mảng nhanh hơn ~30% nhưng khó đọc/khó
  gỡ lỗi — chưa làm, để lần sau nếu hit trên iSH vẫn chậm.

### Test mới — 30 test (765 -> 795)
`tests/test_optimize.py`: `TestPrintDifferential` (3), `TestPrintSpeed` (2),
`TestTextAssetHead` (2), `TestObjectIndex` (2), `TestObjectTable` (5),
`TestAutoFastPath` (2), `TestCompactPlan` (4), `TestPlanCache` (7),
`TestInspectProgress` (2), `TestCmdInspectUsesCache` (1).

### Kiểm chứng sandbox (Python 3.12.3, 2026-11-16)
- Full suite: `Ran 795 tests in 25.3s OK (skipped=1, lỗi=0, fail=0)`,
  Timeout 0; `reports/failures.txt` ghi OK.
- `bench/check_wrap.py`: corpus 0 lỗi + plan 73.025 dòng byte-identical,
  `_print_plan` mới 0,687s/lần.
- Chưa kiểm chứng trên iSH (cần Joseph đo lại — xem §10, lệnh kiểm chứng mới).

### Cần đo lại trên iSH (số sandbox chỉ để so sánh tương đối)
- `time python3 menu.py unity inspect workspace/input/game.ipa > /tmp/inspect.txt 2>&1`
  rồi `wc -l /tmp/inspect.txt` lúc đang chạy (nay thấy dòng "quét i/N" tăng
  dần) + thời gian xong hết (kỳ vọng: dưới 1 phút cho quét + in ra file,
  thay vì 30+ phút in ra màn hình; lần 2 cùng file: vài giây nhờ cache).
- Menu Mod [1] trên cả IPA: kỳ vọng tới câu hỏi patch trong vài chục giây
  (lần đầu) / vài giây (lần sau, cache hit), thay vì kẹt ở 98%.
- Full suite zip mới: kỳ vọng 795 test, 794 passed, 1 skipped, ~20+ phút;
  nếu lỗi: `cat reports/failures.txt`.
