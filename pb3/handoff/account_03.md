# HANDOFF - CLAUDE 03 (đợt 1 / 2)
- Trạng thái: **COMPLETE** (2026-11-15 — C5 phần iSH PASS, xem mục cuối; 5/5 nhiệm vụ nhỏ + C1-C5 đều xong, đủ điều kiện COMPLETE theo H.3)
- Thời điểm cập nhật: 2026-11-15 (C5 phần iSH + COMPLETE, xem mục cuối)
- Commit/hash hoặc patch: không có git trong phiên; bàn giao bằng zip cả cây `pb3/`
- Phạm vi được giao: prompt 03 - loader, registry, arbitration/scoring, isolation/guard, template + conformance kit, test
- Nhiệm vụ nhỏ đã hoàn thành:
  1. Loader + discovery builtin/user, đọc manifest tĩnh, plugin lỗi không làm sập (XONG)
  2. Registry + capability index + enabled/disabled/unavailable + lý do (XONG)
  3. Arbitration + scoring Phụ lục C + tie-break + lý do chọn/loại (XONG)
  4. Isolation: timeout, guard socket/subprocess, chuẩn hóa exception, chặn ghi ngoài workspace (**XONG ở sandbox, xem cập nhật đợt 2**)
  5. Template/skeleton, conformance kit `sdk/testing.py`, `plugin new/test` (**XONG ở sandbox, xem cập nhật đợt 2 - nhiệm vụ B**)
- File đã tạo: `registry/{__init__,compatibility,loader,registry,arbitration}.py`, `services/{__init__,plugin_manager}.py`,
  `plugins/README.txt`, `plugins_user/README.txt`, `tests/test_registry.py`, `handoff/{account_03,review_01_02,TASKS_CLAUDE03}.md`
- File đã sửa: `core/config.py` (+`paths.plugins`, `paths.plugins_user`), `config/default.toml`, `tests/test_app_menu_cli.py`
  (mục 3 menu đã nối), `tests/test_rules_ish_only.py` (kiểm cả `registry/`, `services/`), `docs/SDK_CONTRACT.md` (mục phụ), `CURRENT_STATE.md`,
  `PROGRESS.md`, `CHANGELOG.txt`, `handoff/decisions.md` (#32-39)
- File không được sửa: `sdk/*` (hợp đồng 02b; đợt này không đổi chữ ký), `core/model.py`, `core/errors.py`
- Test đã chạy:
  - `python3 -m unittest discover -s tests -t .` (sandbox Python 3.13.18): **PASS - Ran 303 tests, OK** (240 cũ + 63 mới)
  - `python3 menu.py plugin list` (sandbox): PASS, in "Chưa có plugin..." (rc=0); `plugin test x`: E_NOT_IMPLEMENTED (rc=2) đúng thiết kế
  - Kiểm cú pháp Python 3.8 cho `registry/` và `services/` (`ast.parse(feature_version=(3,8))`): PASS
- Kết quả test thực tế / NOT RUN:
  - **iSH (Python 3.9.16): NOT RUN ON DEVICE.** Lệnh người dùng chạy: `cd /root/pb3 && python3 -m py_compile registry/*.py services/*.py && python3 run_tests.py` (kỳ vọng `Ran 303`, `OK` + skipped=3: 2 thiếu tomllib + 1 máy có /dev/location)
  - Plugin crash/timeout/guard: (đợt 1) chưa có test -> làm ở đợt 2 (32 test, mục A bên dưới)
  - Plugin hợp lệ, manifest sai, API lệch, thiếu dependency, trùng id, import lỗi, symlink thoát, không ứng viên, xung đột probe/tie-break: **PASS** (sandbox)
- Log/report liên quan: `handoff/review_01_02.md` (rà soát 01/02, 9 điểm); không có log iSH mới
- Lỗi còn tồn tại / hạn chế đã biết:
  - Nạp plugin chạy mã plugin trong tiến trình. (Đợt 1 CHƯA có timeout/guard; từ đợt 2 đã có guard best effort, decisions #41-45.) Không được tuyên bố an toàn.
  - Import plugin nhiều file chưa hỗ trợ (decisions #35).
  - (Đợt 1) Chưa kiểm bằng chứng maturity M3/M4 (R5): ĐÃ LÀM 2026-11-15, xem mục cuối. Chưa lưu trạng thái bật/tắt xuống config. Chưa ghi lựa chọn người dùng vào KB (4.6, chờ Claude 09).
  - Regex `name_regex` của plugin chạy trên đường dẫn người dùng (đã cắt 512 ký tự) - chưa chống catastrophic backtracking triệt để.
  - Chưa có plugin sản phẩm nào để thử registry với dữ liệu thật; tất cả test dùng plugin dựng trong thư mục tạm.
- Quyết định kỹ thuật mới và lý do: `handoff/decisions.md` #32-39 (manifest tĩnh bằng ast; trạng thái ok/disabled/unavailable; trùng id; tín hiệu do host tính; `conflict_margin`)
- Phụ thuộc đã đáp ứng: Claude 02b COMPLETE (SDK v3, 240 test)
- Việc tài khoản kế tiếp phải làm: xem `handoff/TASKS_CLAUDE03.md` (isolation -> conformance kit -> template -> test chéo -> chạy trên iSH). Chỉ sau đó Claude 04.
- Lệnh/đường dẫn để tiếp tục:
  - Giải nén zip vào `/root/pb3` (KHÔNG dán heredoc), `python3 -m py_compile` các file mới, rồi `python3 run_tests.py`
  - Điểm vào mã: `registry/loader.py::discover`, `registry/registry.py::build_registry`, `registry/arbitration.py::arbitrate`


## Cập nhật đợt 2 - nhiệm vụ A (isolation) - 2026-11-14
- Nhiệm vụ nhỏ 4/5 (Isolation): **XONG ở sandbox**. File mới: `registry/isolation.py`, `tests/test_isolation.py` (32 test). Sửa: `services/plugin_manager.py` (nạp plugin dưới `import_guard`), chú thích `registry/loader.py`.
- Test: sandbox Python 3.12.3 `python3 run_tests.py`: **PASS - Ran 338 tests, OK** (306 + 32). `python3 menu.py plugin list`: PASS. Cú pháp 3.8: PASS (test_grammar_is_python38 trong suite).
- **iSH: PASS** - iSH (Python 3.9.16, thư mục cũ, `python3 -m pytest tests -v`, 2026-11-15, người dùng chụp màn hình): **PASS - 335 passed, 3 skipped (2 thiếu tomllib + 1 máy có /dev/location), 0 failed, 530,70 s**. Có `ctypes` (test timeout import đã chạy và qua). (đoạn sau là rủi ro đã dự kiến trước khi chạy, nay đã được chứng minh không xảy ra): Rủi ro riêng iSH cần xem khi chạy: (a) có `ctypes` không (nếu không, test timeout import tự SKIP, kỳ vọng thêm 1 skip); (b) vá `builtins.open` có ảnh hưởng thời gian suite; (c) test timeout dùng thời gian thật 0.2-0.3 s nên có thể chậm/không ổn trên máy yếu.
- Quyết định: `decisions.md` #41-45. Hạn chế: đã ghi ở đầu `registry/isolation.py` và #42.
- Còn lại của 03: **B** (`sdk/testing.py` conformance kit, `registry/template.py`, `plugin new/test`) và **C** (xem `TASKS_CLAUDE03.md`). Chưa đủ điều kiện COMPLETE.
- Chưa có plugin sản phẩm nào để thử guard với mã thật; mọi test dùng plugin dựng trong test.

## Cập nhật đợt 2 - nhiệm vụ B (conformance kit + template + plugin new/test) - 2026-11-15
- Nhiệm vụ nhỏ 5/5: **XONG ở sandbox**. File mới: `sdk/testing.py`, `registry/conformance.py`, `registry/template.py`, `tests/test_conformance.py` (55 test). Sửa: `services/plugin_manager.py`, `tests/test_registry.py` (1 test đổi kỳ vọng: `plugin test <id lạ>` rc 1 E_INPUT_NOT_FOUND), `docs/SDK_CONTRACT.md` (mục Phụ), `plugins_user/README.txt`, `CURRENT_STATE.md`, `PROGRESS.md`, `CHANGELOG.txt`, `handoff/{decisions,TASKS_CLAUDE03}.md`. Không đổi `sdk/api.py`, `sdk/types.py`, `sdk/manifest.py`, `registry/{loader,registry,arbitration,isolation}.py`.
- Test đã chạy (sandbox Python 3.12.3): `python3 -m unittest discover -s tests -t .` -> **PASS - Ran 393 tests, OK** (338 + 55). `python3 menu.py plugin new format demo_x / plugin test demo_x / plugin list / new lần 2 (E_OUTPUT_EXISTS, rc 1) / id sai (rc 1) / profile (E_NOT_IMPLEMENTED, rc 2)` chạy thật qua subprocess trên bản sao dự án: PASS.
- B4 đã chứng minh bằng test: (a) skeleton format/extractor/container/engine sinh ra đều PASS conformance ngay qua loader thật (fixture rỗng); (b) plugin cố ý hỏng FAIL đúng mục: selftest (None/False), detect (crash/sai kiểu), candidates (path ngoài VFS, adapter_id sai), probe, parse (sai kiểu, adapter_id sai, không round-trip), readonly (build giả vờ ghi), write (verify ok=None, build crash/không phải bytes, verify False), container/hints (sai kiểu), manifest (api_version 99, fixture quá lớn), guard (socket, subprocess, ghi ngoài tempdir, nuốt ngoại lệ, treo -> E_PLUGIN_TIMEOUT).
- **iSH: suite 393 test PASS** (xem mục cuối: 390 passed, 3 skipped, 0 failed, 541,08 s). Riêng `plugin new/test thu_nghiem` thật: NOT RUN. Lệnh người dùng chạy: `cd /root/pb3 && python3 -m py_compile sdk/testing.py registry/conformance.py registry/template.py services/plugin_manager.py && python3 run_tests.py` (kỳ vọng `Ran 393`, `OK`, skipped=3: 2 thiếu tomllib + 1 máy có /dev/location) rồi `python3 menu.py plugin new format thu_nghiem && python3 menu.py plugin test thu_nghiem` (kỳ vọng `KẾT QUẢ: PASS (6 PASS, 0 FAIL, 5 SKIP; fixture: 0 file)`); xóa `plugins_user/thu_nghiem` và `tests/fixtures/thu_nghiem` sau khi thử.
- Rủi ro riêng iSH chưa kiểm: `tests/test_conformance.py` có 1 test subprocess (`menu.py` trên bản sao dự án) và test treo dùng thời gian thật 0,3 s (timeout hook ngắn) nên có thể chậm/flaky trên máy yếu; suite lần trước 530 s, nay thêm ~55 test; test `tests/fixtures` và thư mục tạm `pb3conf-*` ghi vào thư mục tạm hệ thống chứ không phải trong thư mục dự án.
- Lỗi còn tồn tại / hạn chế mới: (1) skeleton PASS với fixture rỗng chỉ chứng minh hợp đồng + selftest; (2) conformance chưa chạy với VFS thật/container lồng (Claude 04); (3) conformance FAIL thoát mã 4 (mã của E_PLUGIN_INVALID) vì không thêm mã mới vào Phụ lục D (decisions #50); (4) `profile` chưa có skeleton (JSON dữ liệu, Claude 09); (5) guard vẫn best effort (decisions #42).
- Còn lại của 03: **C1-C5** trong `handoff/TASKS_CLAUDE03.md`. C2 (bằng chứng M3/M4) và C4 (R3): đã chốt và làm xong 2026-11-15 (mục cuối). Chưa đủ điều kiện COMPLETE.

## Cập nhật đợt 2 - kết quả iSH, C2 (R5), C4 (R3) - 2026-11-15
- **iSH: PASS cho bản 393 test.** iSH (Python 3.9.16, thư mục cũ, pytest 6.2.4, 2026-11-15, ảnh chụp màn hình): **PASS - collected 393, 390 passed, 3 skipped, 0 failed, 541,08 s (0:09:01)**. Vị trí 3 skip theo ảnh: 1 trong `test_overall_keepalive.py`, 2 trong `test_tomlmini_config.py`; lý do suy từ mã test: 2 skip do thiếu `tomllib` (`@unittest.skipIf`), 1 skip do máy có `/dev/location` (`test_overall_keepalive.py:372`). Trước đây ghi chung "3 skip do thiếu tomllib" là chưa chính xác. Test symlink của `test_paths_safe.py` chạy qua (không bị skip) ở thư mục cũ; suite chỉ chứng minh những gì test kiểm, ghi nguyên tử vẫn chỉ có bằng chứng gián tiếp (không test nào FAIL). Chưa chạy lại ở `/root/pb3`: NOT RUN.
- **C4 / R3: người dùng xác nhận giữ vô hiệu hóa cả hai chiều.** Không đổi mã (decisions #52).
- **C2 / R5: người dùng chốt hướng 1.** Thêm `registry/evidence.py`; sửa `registry/loader.py` (`PluginRecord.effective_maturity`, `discover(corpus_dir=)`), `registry/registry.py` (xếp hạng theo hiệu lực, `build_registry` truyền `tests/corpus`), `registry/arbitration.py` (`contender_from_candidate(maturity=)`), `services/plugin_manager.py` (`M4→M2`), `docs/SDK_CONTRACT.md`. `tests/test_registry.py::TestMaturityEvidence` (+12 test). Quyết định: decisions #51.
- Test (sandbox Python 3.12.3, `python3 run_tests.py`): **PASS - Ran 405 tests, OK** (393 + 12). **iSH: NOT RUN cho 12 test R5.** Lệnh: `cd /root/pb3 && python3 -m pytest tests -v` (kỳ vọng collected 405, 402 passed, 3 skipped).
- Giới hạn: bằng chứng M3/M4 chỉ kiểm hình thức (file có, đúng id, kết quả "PASS"); chưa có file `tests/corpus/<id>.json` nào trong dự án (thư mục `tests/corpus/` nay đã có, chỉ gồm `.gitkeep` + `README.txt`) vì chưa có plugin sản phẩm khai M3/M4.
- Còn lại của 03: **C1** (plugin nhiều file), **C3** (lưu bật/tắt + menu), **C5 phần còn lại** (chạy lại 405 test, `plugin new/test thu_nghiem` thật). Chưa đủ điều kiện COMPLETE.

## Cập nhật đợt 2 - C1 plugin nhiều file - 2026-11-15
- **C1: XONG ở sandbox.** Quyết định `decisions.md` #56: package riêng + import tương đối, KHÔNG thêm vào `sys.path`; quét tĩnh cả cây; không dùng bytecode đệm. File mới: `registry/pluginpkg.py`, `tests/test_multifile.py` (25 test). Sửa: `registry/loader.py`, `registry/scan.py` (chú thích), `docs/SDK_CONTRACT.md`, `plugins_user/README.txt`, `CURRENT_STATE.md`, `CHANGELOG.txt`, `handoff/{decisions,TASKS_CLAUDE03}.md`.
- Test (sandbox Python 3.12.3): **PASS - Ran 463 tests, OK** (438 + 25). Chạy thật qua CLI trên bản sao: plugin `demo_mf` 2 file -> `plugin list` [ok], `plugin test` PASS (CHƯA KIỂM DỮ LIỆU), không có `__pycache__`; đổi sang `import helper` -> [disabled] kèm gợi ý. Đã đảo thử: bỏ `get_code` của bộ nạp thì test `.pyc` UNCHECKED_HASH FAIL (999 != 1), nên test có bắt được lỗi.
- **iSH: NOT RUN ON DEVICE** cho 25 test mới. Lệnh: `cd <thư mục pb3 trong iSH> && python3 -m pytest tests` (kỳ vọng collected 463, 460 passed, 3 skipped; số collected của pytest trước đây khớp số `unittest` ở sandbox). Rủi ro riêng 3.9/iSH chưa kiểm: meta-path finder, `ModuleSpec(..., is_package=True)` không loader, `PycInvalidationMode.UNCHECKED_HASH` trong test.
- Hạn chế: lint chứ không phải sandbox; tên package plugin khác đoán được (test `TestKnownLimit`); `plugin new` chưa sinh skeleton nhiều file.
- Còn lại của 03: **C3** (lưu bật/tắt + menu), **C5 phần còn lại**.

## Cập nhật đợt 2 - C3 lưu bật/tắt plugin + menu quản lý - 2026-11-15
- **C3: XONG ở sandbox.** Quyết định `decisions.md` #57: khóa `plugins.disabled` (kiểu mới `strlist`) trong `config/local.toml`; `build_registry` gộp danh sách tắt từ cấu hình; `plugin disable|enable <id>`; menu mục 3 \"QUẢN LÝ PLUGIN\" (gõ số để đảo). File mới: `tests/test_plugin_toggle.py` (34 test). Sửa: `core/config.py` (+`strlist`, `Config.update`), `config/default.toml` (+`[plugins]`), `registry/registry.py`, `services/plugin_manager.py`, `ui/screens.py` (`run_feature` truyền `input_fn`/`say` khi dịch vụ khai báo), `ui/cli.py`, `ui/handlers.py`, `README.txt`, `CURRENT_STATE.md`, `PROGRESS.md`, `CHANGELOG.txt`, `handoff/{decisions,TASKS_CLAUDE03}.md`, `TIEN_DO_VA_NHIEM_VU.md`.
- Cùng phiên: đưa thư mục dự án về `/root/pb3` (người dùng chốt), bỏ đường dẫn cũ khỏi toàn bộ tài liệu/mã/test (decisions #40 viết lại; `test_rules_ish_only` cấm đường dẫn ngoài dự án và kiểm tài liệu không còn nhắc đường dẫn cũ).
- Test (sandbox Python 3.12.3): `python3 -m unittest discover -s tests -t .` -> **PASS - Ran 497 tests, OK** (463 + 34). Chạy thật qua tiến trình con `menu.py` trên bản sao dự án: `plugin new format demo_tg` -> `plugin disable demo_tg` -> `plugin list` (`[disabled]`) -> `plugin enable demo_tg` -> `plugin list` (`[ok]`); `plugin disable khong_co` -> E_INPUT_NOT_FOUND rc 1 (nằm trong `TestRealCli`). Đảo thử: bỏ đoạn gộp `plugins.disabled` trong `build_registry` -> 6 test FAIL; đảo thứ tự \"ghi cấu hình / đổi trạng thái\" trong menu -> 1 test FAIL; khôi phục -> PASS.
- **iSH: NOT RUN ON DEVICE** cho 134 test thêm sau bản 393 (R5 12, quét tĩnh 14, nhãn conformance 3, sửa hạn chế 01/02 16, nhiều file 25, bật/tắt 34, chia tầng 27, hồi quy trùng id 3). Lệnh người dùng chạy: `cd /root/pb3 && python3 run_tests.py --tier full` (kỳ vọng `collected 527`, `524 passed, 3 skipped`; 3 skip = 2 thiếu tomllib + 1 máy có /dev/location). Rồi thử thật: `python3 menu.py plugin new format thu_nghiem && python3 menu.py plugin test thu_nghiem && python3 menu.py plugin disable thu_nghiem && python3 menu.py plugin list && python3 menu.py plugin enable thu_nghiem` (kỳ vọng `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU)`, sau `disable` thấy `thu_nghiem  [disabled]` + \"người dùng đã tắt\", sau `enable` thấy `[ok]`); mở menu mục 3 thử đảo bằng số; xóa `plugins_user/thu_nghiem`, `tests/fixtures/thu_nghiem` và dòng `disabled` trong `config/local.toml` sau khi thử.
- Rủi ro riêng iSH chưa kiểm: ghi mảng `plugins.disabled` vào `config/local.toml` trên Python 3.9 (bộ đọc TOML tối giản, không phải tomllib; đã kiểm ở sandbox bằng tomlmini qua test đối chiếu), nhập liệu menu trên terminal thật, thanh tiến độ ẩn khi chờ gõ.
- Hạn chế mới: bật/tắt chỉ hiệu lực ở lần dựng Registry sau (menu đảo ngay trong bộ nhớ); plugin manifest hỏng có id `?<tên>` không bật/tắt được (đã bị loại sẵn); chưa có plugin sản phẩm nào để thử.
- Còn lại của 03: **C5 phần iSH** (kết quả iSH thật cho 527 test và các lệnh trên; phần sandbox đã xong 2026-11-15, xem mục mới bên dưới). Khi có bằng chứng thật: ghi vào file này, đổi gói 03 sang COMPLETE; chưa có thì giữ IN_PROGRESS.

## Cập nhật sửa tài liệu 2026-11-15 (Muse)
- Sửa lệch số test 497 -> 524 ở các tài liệu hiện hành (`CURRENT_STATE.md`, `PROGRESS.md`,
  `TIEN_DO_VA_NHIEM_VU.md`, `handoff/TASKS_CLAUDE03.md`, `README.txt` và file này).
  Tổng hiện tại là 524 = 497 + 27 test chia tầng (`tests/test_tiering.py`).
  Kỳ vọng trên iSH cho bản hiện tại: collected 527, 524 passed, 3 skipped (cập nhật 2026-11-15: +3 test hồi quy trùng id, decisions #60).
  Các số 497 ở mốc lịch sử (ví dụ C3: `Ran 497 tests ... OK`) giữ nguyên vì đúng ở thời điểm đó.
- `PROGRESS.md` §1 đã sửa: gói 02 đã bị 02b thay thế; iSH của 02b là 237 passed, 3 skipped, 0 failed.
- Tạo `CLAUDE03_CONTINUE_HERE.md` ở gốc dự án: đọc trước, chỉ còn C5, có lệnh iSH đầy đủ.
- Tạo `tests/corpus/` (`.gitkeep` + `README.txt`): chỗ để bằng chứng M3/M4 (R5).
  Hiện chưa có file bằng chứng `<id>.json` nào; không tạo file giả mạo.
- Đặc tả `PB3_v3.1_iSH_10Claude.txt` và bộ prompt `PB3_v3.1_10_Claude_Prompts.txt`
  đã có trong zip này (nhận 2026-11-15), nằm ngoài thư mục `pb3/`.
- Trạng thái gói 03 vẫn **IN_PROGRESS**. Vẫn **NOT RUN** trên iSH: C5
  (131 test thêm sau bản 393 + `plugin new/test/disable/enable thu_nghiem` thật).
  Không bịa kết quả iSH. Không làm việc của Claude 04-10.

## Cập nhật C5 phần sandbox + sửa trùng id (Muse) - 2026-11-15
- **Phần sandbox của C5 đã xong.** Kiểm lại prompt 03 + đặc tả §4 (loader, registry, arbitration Phụ lục C, isolation, conformance kit, R5, C1, C3) trên bản sao mới của dự án: mọi mục đều có mã + test, trừ 1 lỗi thật tìm được khi thử trùng id bằng CLI (xem decisions #60).
- **Lỗi đã sửa (decisions #60):** `Registry.add()` giữ bản plugin đến trước (builtin) nên `plugin list` hiện cả hai bản trùng id đều `[disabled]`, bản user thắng bị ghi đè ngược. Sửa: bản ok thắng bản không ok; cả hai đều không ok thì bản user thắng bản builtin. Thêm 3 test hồi quy trong `tests/test_registry.py`. Tổng sandbox: **527 test OK** (`python3 run_tests.py --tier full`, 7,7 s, Python 3.12.3).
- **CLI thật trên bản sao (sandbox, KHÔNG phải iSH):**
  - `plugin new format thu_nghiem` -> `plugin test thu_nghiem`: `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU)` (6 PASS, 0 FAIL, 5 SKIP; fixture: 0 file), rc=0.
  - `plugin disable thu_nghiem` -> `plugin list` ra `[disabled]` (\"người dùng đã tắt\") -> `plugin enable` -> `list` ra `[ok]`.
  - Menu mục 3, gõ số `1`: đảo được thu_nghiem sang `[disabled]`, `plugin list` sau đó xác nhận.
  - Plugin 2 file `plugin.py` + `helper.py` (import tương đối): `plugin list` `[ok]`, `plugin test` PASS, 0 `__pycache__` trong thư mục plugin. (Lần thử đầu thiếu `parse()` thì loader vô hiệu hóa đúng kèm lý do — đó là lỗi của plugin thử, không phải lỗi mã.)
  - R5: plugin khai M4 không có `tests/corpus/m4_demo.json` -> `list` hiện `M4→M2` kèm cảnh báo \"khai M4 nhưng hiệu lực M2: thiếu tests/corpus/m4_demo.json\"; có file bằng chứng hợp lệ (read/write/verify PASS) thì giữ M4.
  - Plugin thiếu `requires` -> `[unavailable]` kèm lý do \"thiếu thư viện: ...\"; trùng id hợp lệ -> user `[ok]`, builtin `[disabled]` (\"bị plugin cùng id ở user ghi đè\").
  - Sau khi thử đã dọn sạch: `plugins_user/`, `plugins/` trống, không còn `config/local.toml` dư.
- **iSH: vẫn NOT RUN ON DEVICE** ở thời điểm viết mục này (trước 18:28). Còn lại duy nhất để 03 COMPLETE: chạy `cd /root/pb3 && python3 run_tests.py --tier full` (kỳ vọng collected 527, 524 passed, 3 skipped) + thử `plugin new/test/disable/enable thu_nghiem` thật trên iSH, rồi ghi kết quả vào file này. Xem mục cuối: đã có bằng chứng thật, gói đã COMPLETE.

## Cập nhật C5 phần iSH — Claude 03 COMPLETE - 2026-11-15
- **Suite C5 trên iSH: PASS.** 2026-11-15 18:28 +07, trong `/root/pb3`, `python3 run_tests.py --tier full`: `Ran 527 tests in 264.9s OK (skipped=3, lỗi=0, fail=0)` — tức 524 passed, 3 skipped, 0 failed. Chậm nhất 44.0s `TestRealCli::test_disable_survives_new_process`; 27 test vượt ngân sách 1.0s, tự chuyển sang tầng chậm từ lần sau; đã lập mốc FULL xanh (dùng cho `--changed`). Không có test nào FAIL nên các test symlink và ghi nguyên tử trong suite đều qua. 3 skip đúng thiết kế (2 thiếu `tomllib`, 1 máy có `/dev/location`).
- **Plugin thật trên iSH: PASS.** 2026-11-15 18:29 +07, trong `/root/pb3`, lệnh: `python3 menu.py plugin new format thu_nghiem && python3 menu.py plugin test thu_nghiem && python3 menu.py plugin disable thu_nghiem && python3 menu.py plugin list && python3 menu.py plugin enable thu_nghiem`. Đã tạo skeleton `plugins_user/thu_nghiem/plugin.py` và `tests/fixtures/thu_nghiem/.gitkeep`; `plugin test` cho `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (6 PASS, 0 FAIL, 5 SKIP; fixture: 0 file)`; `disable` lưu vào `config/local.toml (plugins.disabled)` — chứng minh `save_local` ghi mảng `plugins.disabled` chạy trên Python 3.9; `list` hiện `thu_nghiem [disabled] M1 format | engine: generic | user v0.1.0, lý do: người dùng đã tắt`; `enable` báo `Đã bật lại plugin 'thu_nghiem'`.
- **Menu-toggle bằng số: KHÔNG chạy riêng trên iSH.** Chỉ có bằng chứng sandbox (đảo bằng số ở menu mục 3) và suite C3 (34 test, gồm test menu) đã PASS trong suite 527 trên iSH. Không ghi là đã chạy menu-toggle riêng trên iSH.
- **Dọn dẹp trên iSH: 2 thư mục đã xóa 2026-11-15 18:31** — `rm -rf plugins_user/thu_nghiem tests/fixtures/thu_nghiem` chạy sạch trong `/root/pb3`. Riêng dòng `disabled` của thu_nghiem trong `config/local.toml` chưa có bằng chứng đã xóa (lệnh trên không chạm tới file đó; chỉ là mục thừa, không ảnh hưởng).
- **Trạng thái: Claude 03 COMPLETE (2026-11-15).** C1-C5 đều xong, bằng chứng iSH đủ theo H.3. Claude 04-10 vẫn NOT_STARTED; không làm thêm ở đây.
