# Handoff nhóm C — Nâng cấp + tối ưu bộ test (đợt rà soát tổng cuối PB3 v3.1)

Người làm: nhóm bộ test (hạng mục c). Phạm vi file: `run_tests.py`,
`tests/tiering.py`, `tests/conftest.py`, `tests/test_tiering.py`,
`tests/tiers.json` (không đổi), `docs/TEST_STRATEGY.md`,
`tests/test_runner*.py`. Không sửa test sản phẩm khác, không sửa mã sản phẩm.
Không build zip. Ghi chú: cây test tăng trong lúc làm (các nhóm khác thêm
test) nên mọi so sánh trước/sau đều ghi rõ tổng test ở từng thời điểm.

## Quyết định

- **QT-1. Cache `_Facts` trên đĩa (`tests/tiering.py`).** Phân tích 60 file
  test bằng regex tốn ~0,5 s mỗi lần chạy trên sandbox (đắt hơn nhiều trên
  iSH) mà nội dung file hiếm khi đổi. Cache `reports/test_facts_cache.json`
  lưu (tầng, cờ smoke) từng test, khóa bằng sha1 nội dung file + hash luật
  xếp tầng (regex SLOW/META/ISH/SMOKE + tên tầng + `FACTS_CACHE_VERSION`):
  file đổi hay luật đổi thì cache tự hết hiệu lực, không bao giờ cho kết
  quả cũ. Ghi kiểu "bẩn rồi xả một lần" (`flush_facts_cache`, cuối `_build`,
  cuối `plan_counts`, `pytest_sessionfinish`); mọi lỗi đọc/ghi cache đều
  nuốt (hỏng cache thì phân tích lại). `_FactsView` chỉ cài `.blocks`
  (khóa), `.tier()`, `.smoke_marked()` — các nơi dùng `_Facts` bên ngoài
  không chạm gì khác (đã kiểm bằng grep). `reset_cache()` cũng xóa cache
  đĩa đã nạp trong bộ nhớ.
- **QT-2. Nạp chọn lọc module (`run_tests.py::_build`, `T.plan_modules`).**
  Khi không chỉ định file và không dùng `--changed`, chỉ nạp module chứa
  test thuộc tầng đang chạy (tính bằng `_Facts`, không import — smoke chỉ
  nạp 9/60 module). Đếm lại sau khi nạp (`_count_loaded`): lệch khỏi kế
  hoạch nhẹ (test động, lỗi import...) thì nạp đủ rồi mới chọn — không bao
  giờ mất test âm thầm. `--changed` vẫn nạp đủ để đếm đúng số test bị ảnh
  hưởng nằm ngoài tầng đang chạy (cảnh báo "nằm ngoài tầng"). `-k` vẫn đúng
  (lọc theo tầng trước, rồi mới lọc `-k`).
- **QT-3. Bản sao repo ấm (`tests/test_runner.py::_copy_repo`).** Bản sao
  dùng cho test chạy tiến trình con GIỮ `__pycache__` (Python kiểm `.pyc`
  bằng mtime+size nguồn nên không sợ bytecode cũ; file nào test sửa trong
  bản sao thì biên dịch lại) và sao chép cả `test_facts_cache.json` (khóa
  sha1 nội dung nên luôn đúng với bản sao). Trước đây bản sao lạnh phải biên
  dịch lại mọi module (~4,5 s mỗi lần chạy con). Không sao chép
  `test_times.json` (bản sao giữ profile sạch như thiết kế cũ).
- **QT-4. Tiến độ pipe bước từng 1% (luật #152).** Khi không phải TTY, bộ
  chạy in một dòng mỗi khi % nguyên đổi (tối đa ~100 dòng), thay vì in mỗi
  50 test (% nhảy ~4% giữa 2 dòng). Khi TTY vẫn cập nhật từng test qua `\r`
  (đã đúng luật từ trước).
- **QT-5. Watchdog nêu rõ pha treo + ngưỡng theo hệ số máy.** `ThreadedSuite`
  ghi pha (`setUpClass`/`setUpModule`/`tearDownClass` + tên test) để luồng
  watch báo rõ khi fixture treo — fixture chạy trên main thread nên timeout
  của test không áp dụng được; treo ở đây chỉ báo rõ + Ctrl+C vẫn thoát 130
  (không giả vờ kill được, như D-POLISH-2). Ngưỡng cảnh báo treo 15 s nhân
  theo `timeout_scale()` (75 s trên iSH) để test khỏe trên iSH không spam
  cảnh báo oan — đây là ngưỡng cứng sandbox còn sót lại duy nhất tìm thấy
  (đã rà: `budget_s`, `runner_timeout_s`, mọi `subprocess_timeout` đều đã
  scale; `_STALE_TMP_MAX_AGE_S` là tuổi file, không liên quan tốc độ máy).
- **QT-6. Đĩa đầy không giết bộ chạy.** Mọi ghi tiến độ/báo cáo qua
  `_emit`/`_print` đều nuốt lỗi ghi (ENOSPC/pipe vỡ): suite vẫn chạy hết,
  mã thoát vẫn theo kết quả test, `reports/failures.txt` vẫn cứu được danh
  sách lỗi. Bài học 2026-11-16: hai lần full chạy song song làm đầy /tmp
  512 MB, lần chạy viết log ra /tmp chết ở test ~151 vì `write()` tiến độ
  không được bắt. **Quy tắc vận hành: không bao giờ chạy hai lần full song
  song trong cùng một cây** (chung cả `reports/` lẫn /tmp).
- **QT-7. KHÔNG chạy song song test.** Đã cân nhắc kỹ và từ chối: nhiều test
  đổi trạng thái toàn cục dùng chung (biến môi trường, chế độ màu, registry)
  và test tiến trình con ghi `reports/` trong cây thật; trên iSH chỉ có 1
  CPU giả lập + bug `getppid()` trong daemon thread nên song song còn chậm
  hơn và rủi ro hơn. Tốc độ lấy từ cache, nạp chọn lọc, bản sao ấm — không
  từ song song. (Nếu sau này muốn song song thì phải tách theo tiến trình +
  thư mục tạm + `reports/` riêng từng worker — việc lớn, không làm trong
  đợt này.)
- **QT-8. Không xóa test trùng lặp nào.** Quét toàn cây bằng AST: 4 nhóm
  "thân test giống hệt nhau" đều là cùng mẫu test chạy trên plugin khác nhau
  (`test_selftest_ok` × 10 format plugin, `test_verify_accepts_good_rejects_tampered`
  × 6, `test_binary_garbage_corrupt` × 3, `test_selftest_ok` sqlite/locres)
  — mỗi cái kiểm một parser riêng, không phải trùng lặp thật.
- **QT-10. Chạy một file không được tỉa profile (bug có sẵn, phát hiện khi
  kiểm chứng).** `main()` truyền `T.is_full(want)` cho `record_times` —
  mà chạy 1 file thì tier mặc định là "full" nên `is_full=True`, và
  `record_times` tỉa mọi entry profile không thuộc file đó: mỗi lần chạy
  `run_tests.py tests/test_x.py` (thao tác thường ngày nhất) đều xóa lịch
  sử đo + hysteresis của cả cây. Đã kiểm bằng repro trong bản sao (seed 2
  entry, chạy 1 file, còn 20 entry — entry ngoài file mất). Nay truyền
  `not filtered` (chỉ lần full thật, không lọc file, mới tỉa/demote — khớp
  luật lập mốc). `--changed` cũng không tỉa/demote nữa (đúng luật "2 lần
  FULL liên tiếp" cho demote; tỉa là việc dọn dẹp, chờ lần full thật).
  **Khôi phục:** profile `reports/test_times.json` đã bị tỉa trong lúc rà
  soát (còn ~67 entry) — đã khôi phục từ bản sao lưu đầu phiên (1394
  entry, số đo thật); lần full xanh tới sẽ ghi lại đầy đủ.
- **QT-9. Rút timeout test treo 1,8 s → 0,9 s** (`TestTimeoutSubprocess`,
  `PB3_TEST_TIMEOUT_SCALE=0.005`): test treo 30 s nên biên vẫn lớn; timeout
  tính từ lúc thân test bắt đầu, không tính thời gian khởi động con.

## Số đo (sandbox này; dao động ±5–7 s giữa các lần chạy do CPU dùng chung)

| | Trước | Sau |
|---|---|---|
| Full | 1395 test / 92,7 s, chậm nhất 4,1 s | **1536 test / 86,5 s**, chậm nhất 4,3 s |
| Fast (mặc định) | 1241 test / 29,6 s | 1371 test / 29,7 s |
| Smoke | 59 test / 1,13 s | 59 test / **0,71–0,98 s** |
| `_build` (nạp + xếp tầng) | 1,08 s | ~0,3 s (smoke); `plan_modules` ấm 0,02 s, lạnh 0,74 s |
| Chạy con `--changed` trong bản sao | 6,5–7,1 s (bản sao lạnh) | **3,3 s** |
| `test_runner.py` (file) | ~8,1 s tổng thời gian test | 37 test / ~13–19 s (thêm 12 test mới + cây lớn hơn) |
| Chậm nhất | 4,1 s `TestPrintSpeed` | 4,3 s `TestPrintSpeed::test_print_plan_matches_old_wrap` |

- Full sau nhanh hơn tuyệt đối dù thêm 141 test (các nhóm khác thêm trong
  lúc rà soát): 66,5 ms/test → 56,3 ms/test.
- Chi phí cố định đã đo (profile thật): nạp/import module test 0,51 s;
  phân tích `_Facts` 0,51 s → cache ấm 0,02 s; `setUpClass` toàn suite
  ~1,8 s (lớn nhất `TestPrintSpeed` 0,50 s — mã test của nhóm khác, chỉ ghi
  nhận); `record_times` 0,01 s; `file_hashes` (`--changed`) 0,02 s.
- Tổng test: 1395 → 1536 (+141: nhóm này +15 test mới, các nhóm khác +126).
  Test mới của nhóm: `TestFactsCache` (4), `TestModuleSelection` (3),
  `TestProgressPipeSteps` (1), `TestWatchdogPhase` (2),
  `TestOutputFailure` (2), `TestCopyRepoWarm` (1),
  `TestFileRunKeepsProfile` (1) trong `tests/test_runner.py`;
  `TestFactsView` (1) trong `tests/test_tiering.py`.

## Kiểm chứng đã chạy

- `test_runner.py` + `test_tiering.py`: 67–68 test xanh (chạy nhiều lần;
  1 lần fail thoáng qua không tái hiện được — nghi cây đổi giữa chừng do
  nhóm khác đang sửa; đã làm `test_build_selective_matches_plan` bỏ qua so
  khớp số khi phát hiện cây đổi giữa chừng thay vì fail oan). Sau khi sửa
  QT-10: file `test_runner.py` chạy 5,2 s xanh, profile giữ nguyên 1408
  entry (không bị tỉa nữa).
- Mô phỏng gộp iSH trên 2 file của nhóm (pty + `COLUMNS=40` +
  `PB3_TEST_TIMEOUT_SCALE=5`): 67 test, 0 fail, 0 lỗi, 5,2 s, chậm nhất
  1,1 s.
- Smoke ×2: 0,71 s / 0,98 s, xanh. Fast ×2: 29,7 s / 29,8 s — nhưng có
  1 fail lặp lại (xem dưới).
- **Full MỘT lần (theo điều phối): 1536 test / 86,5 s, 1533 đạt, 1 bỏ qua,
  1 lỗi, 1 fail, Timeout 0.** Hai lỗi đều ngoài phạm vi nhóm (xem dưới);
  vì fail nên lần full này KHÔNG lập mốc (đúng luật) — cần sửa test menu
  rồi chạy lại full xanh ở bước tích hợp (coordinator chạy full + mô phỏng
  iSH cuối để chốt số đo).

## Vấn đề ngoài phạm vi, coordinator cần chuyển nhóm khác

1. **`test_unimplemented_shows_clear_status_and_changes_nothing`
   (tests/test_app_menu_cli.py) FAIL xác định** — test đòi menu [5][3] hiện
   "CHƯA TRIỂN KHAI" (nợ F6 ở decisions #180), nhưng menu [5][3]
   Inventory/hex/strings nay ĐÃ được triển khai
   (`services/diagnostics_tool.py:439` có Inventory; menu hiện "đã nối dịch
   vụ") nên test cũ fail. Thuộc nhóm menu/diagnostics: cập nhật test theo
   hành vi mới (hoặc xác nhận triển khai [5][3] đã xong thì sửa test).
   Test này thuộc tầng fast nên mọi lần chạy fast/full hiện đều fail 1.
2. **`TestUnrealCli.setUpClass` lỗi ENOSPC thoáng qua trong lần full** —
   một suite khác chạy song song trên /tmp đúng lúc đó (lỗi đĩa tmpfs đã
   biết theo điều phối, không phải lỗi code). Đã dọn /tmp trước lần chạy;
   lần sau tránh chạy full song song.
3. **Chậm nhất toàn suite vẫn là mã sản phẩm:** `TestPrintSpeed::
   test_print_plan_matches_old_wrap` ~4,3 s (`tests/test_optimize.py`,
   `_print_plan`/`wrap_text`) — nhóm tối ưu xử lý, không thuộc hạng mục test.
4. **Quirk kế toán có sẵn (không sửa trong đợt này):** khi `setUpClass`
   fail, các test của lớp đó vẫn được tính vào `testsRun` nhưng không chạy,
   nên dòng "Đạt" trong Tóm tắt tính thừa chúng là đạt (đã kiểm bằng repro
   tối thiểu: 2 test + 1 test khỏe → `testsRun=3`, 2 lỗi setUpClass, "đạt"
   tính ra 1 trong khi chỉ test khỏe chạy thật). Sửa đúng thì đếm số
   `addSuccess` thật thay vì suy ra từ `testsRun` — đổi ngữ nghĩa bảng tóm
   tắt nên để coordinator quyết, không tự sửa.

## Giới hạn trung thực

- Song song test: từ chối có lý do (QT-7). Trên iSH 1 CPU giả lập, song
  song không giúp; trên sandbox 1,5 CPU dùng chung, lợi ích nhỏ so với rủi
  ro trạng thái dùng chung.
- Tốc độ full suite vẫn do thân test quyết (~83 s tổng thời gian test, chủ
  yếu là test CLI mở tiến trình con `menu.py` ~0,34 s/lần trong file test
  của nhóm khác) — nhóm này không được sửa các file đó nên không bóc được
  thêm. Muốn nhanh hơn nữa thì gộp lệnh CLI theo quy tắc 3 ở
  `TEST_STRATEGY.md` (một lần chạy, nhiều assert) — việc của nhóm sở hữu
  từng file test.
- Mô phỏng iSH toàn suite chưa chạy ở nhóm này (theo điều phối, coordinator
  chạy ở bước tích hợp); nhóm chỉ mô phỏng trên 2 file của mình.
- `pytest` không có trong sandbox nên `conftest.py::pytest_sessionfinish`
  chỉ được kiểm bằng `py_compile`, chưa chạy thật bằng pytest.
- Số đo sandbox dao động mạnh (±5–7 s); số đo iSH thật vẫn phải chờ Joseph
  chạy trên máy (đặc biệt hiệu quả cache `_Facts` trên CPU giả lập — kỳ
  vọng lợi hơn sandbox nhiều vì phân tích regex đắt hơn trên iSH, nhưng
  chưa có số đo thật).
