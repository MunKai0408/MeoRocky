# PB3 v3.1 - TIẾN ĐỘ VÀ NHIỆM VỤ (bàn giao, cập nhật 2026-11-15)

Ngôn ngữ làm việc: **tiếng Việt**. Người dùng chạy mọi thứ trên iPhone (iSH). Đọc file này trước, rồi `handoff/account_05.md`, `handoff/decisions.md` (đặc biệt #66-76).

## 0. Cấu trúc gói (gọn từ 2026-11-15)
- Zip chỉ có MỘT thư mục `pb3/` = thư mục dự án (trên iSH là `/root/pb3`) và hai file đặc tả nằm ngoài thư mục đó (xem mục 10). Trong `pb3/` chỉ có file ở gốc (`menu.py`, `run_tests.py`, tài liệu) và các thư mục con (`core ui sdk registry services config docs handoff tests plugins plugins_user kb reports`) chỉ chứa file, không có thư mục cháu — ngoại lệ: `tests/corpus/` (thư mục bằng chứng M3/M4, R5), `plugins/zip/` (plugin container builtin đầu tiên, Claude 04) và `tests/fixtures/zip/` (fixture IPA mẫu của Claude 04).
- Đã bỏ khỏi gói: bản `pb3_tool.py`/bộ cài/handoff cũ ở thư mục ngoài (bản CŨ, R8), `.git` (1 commit), `logs/`, `workspace/`, `__pycache__`. `logs/` và `workspace/{input,temp,cache,output}` do chương trình tự tạo khi chạy. `plugin new <kind> <id>` cũng tự tạo `plugins_user/<id>/` và `tests/fixtures/<id>/` lúc chạy (đúng thiết kế, không có trong zip).
- `tests/fixtures/appendix_b_plugin.py` đã chuyển thành `tests/appendix_b_plugin.py` (test_appendix_b đã sửa đường dẫn).

## 1. Môi trường chuẩn
- Thư mục dự án: **`/root/pb3`** (quyết định của người dùng, ghi ở `decisions.md` #40).
- Mã nguồn **KHÔNG được ghi cứng đường dẫn dự án**: gốc suy ra từ vị trí file (`core/paths.py`). Test `test_no_hardcoded_external_paths` cấm đường dẫn ngoài dự án trong mã đang dùng.
- `EXPECTED_ROOT = "/root/pb3"` trong `core/paths.py` chỉ để ghi chú: `python3 menu.py diagnose --ish` báo nếu thư mục dự án khác `/root/pb3` (vẫn chạy được). Ghi chú này CHỈ nằm trong báo cáo diagnose, không nằm ở banner khởi động (`App.problems`).
- iSH của người dùng: Python 3.9.16, pytest 6.2.4. Suite có 3 test bị skip: 2 do không có `tomllib`, 1 do máy có `/dev/location` (`test_overall_keepalive.py`); đều đúng thiết kế.
- Suite 527 test đã PASS trên iSH trong `/root/pb3` (2026-11-15 18:28: 524 passed, 3 skipped, 0 failed, 264,9 s; test symlink chạy qua, không test nào FAIL). Trước đó bản 393 test PASS ở thư mục cũ (541 s).
- Suite 575 test (527 + 48 test Claude 04) đã PASS sandbox 2026-11-15 (9,1 s) và PASS trên iSH 2026-11-15 19:20 (572 passed, 3 skipped, 0 failed, 449,0 s).
- Suite 643 test (575 + 68 test Claude 05) đã PASS sandbox 2026-11-15 (11,4 s) và PASS trên iSH 2026-11-15 20:50 (639 passed, 4 skipped, 0 failed, 719,4 s); Unity CLI thật PASS 20:52/20:56 (xem `handoff/account_05.md` mục 5). Gói 05b (UnityFS v8 + SerializedFile v23, Unity 6) sandbox DONE 2026-11-15: suite 665 test PASS (12,0 s), UnityPy 1.25.4 đọc đúng mọi fixture mới + output đã patch; iSH NOT RUN (xem `handoff/account_05b.md` mục 5). Đợt polish (Wave 1–3 + màu sắc + giao diện chạy test + làm đẹp toàn bề mặt) sandbox DONE 2026-11-15: suite 756 test PASS (29,8 s; 755 passed, 1 skipped); iSH NOT RUN — zip polish này THAY zip 05b, Joseph chỉ kiểm chứng 1 lần (xem `handoff/account_polish.md`).

## 2. Tiến độ 10 gói
| Gói | Trạng thái | Ghi chú |
|---|---|---|
| 01 Nền móng | XONG | rà soát lại: không có lỗi mới |
| 02 / 02b SDK | XONG | SDK v3 khớp đặc tả 4.2-4.4, Phụ lục A/B/D |
| **03 Plugin system** | **XONG (COMPLETE 2026-11-15)** | loader, registry, arbitration (+ `plugin list`), isolation, conformance kit/template (`plugin new/test`), R5 (bằng chứng M3/M4); R3 đã chốt; C1 (plugin nhiều file) và C3 (lưu bật/tắt + menu) xong; C5 phần iSH PASS 2026-11-15 (suite 527 test + plugin thật) |
| **04 Container/VFS** | **XONG (COMPLETE 2026-11-15)** | VFS thật ZIP/TAR/GZ/XZ (stdlib), container lồng `'::'`, ZIP writer nguyên tử, plugin container builtin `zip` (M2), CLI `container list/read`; sandbox 575 test OK; iSH PASS 19:20 (572 passed, 3 skipped) + container CLI thật 19:21 |
| **05 Unity backend** | **XONG (COMPLETE 2026-11-15)** | UnityFS v6/v7 + SerializedFile v15–22, backend AUTO/LIBRARY/NATIVE, planner, patcher PATCH_INPLACE/REBUILD_SECTION + diffverify, lệnh `unity inspect/read/patch` + `mod`; sandbox 643 test OK; iSH PASS 20:50 (639 passed, 4 skipped) + Unity CLI thật 20:52/20:56; 05b bổ sung UnityFS v8 + SerializedFile v23 (sandbox DONE, xem `handoff/account_05b.md`) |
| 06-10 | chưa bắt đầu | |

Tiến độ tổng ước tính: 5 gói xong = 50% (theo số gói, không phải đo chính xác); gói 05 COMPLETE 2026-11-15; gói 05b (bổ sung ngoài prompt, không tính vào 10 gói) sandbox DONE 2026-11-15, iSH NOT RUN. Đợt polish sandbox DONE 2026-11-15 (756 test OK), iSH NOT RUN — zip polish THAY zip 05b.

## 2b. Quy trình chạy test (BẮT BUỘC cho mọi tài khoản; chi tiết `docs/TEST_STRATEGY.md`, decisions #58)
1. Trong lúc làm: `python3 run_tests.py --tier smoke` (vài giây) sau mỗi lần sửa; `python3 run_tests.py tests/test_<gói của mình>.py` (chạy ĐỦ file test của gói, kể cả test chậm), và `python3 run_tests.py` (bộ NHANH) hoặc `--changed` để biết có làm vỡ chỗ khác không.
2. KHÔNG chạy toàn bộ mỗi lần sửa. Chạy `python3 run_tests.py --tier full` đúng MỘT lần khi bàn giao gói; chỉ ghi "PASS" vào tài liệu khi đó là lần chạy full. Kết quả bộ nhanh/`--changed`/một file ghi rõ là "bộ nhanh" hoặc "một phần", không gọi là PASS toàn suite.
3. Test mới không cần đánh dấu tầng (tự xếp). Viết test rẻ: xem 5 quy tắc trong `docs/TEST_STRATEGY.md`.

## 3. Kết quả test THẬT (PASS / FAIL / NOT RUN)
| Môi trường | Kết quả | Ghi chú |
|---|---|---|
| Sandbox Linux, Python 3.12.3, `python3 run_tests.py --tier full` | **PASS: 575 test, OK** (skipped=0) | 527 như trước + 48 test Claude 04 (`test_container.py` 38 + `test_vfs_real.py` 10; decisions #61-65). Sandbox không phải iSH, không chứng minh được hành vi trên iSH |
| iSH, bản 575 test (Claude 04) | **PASS: 572 passed, 3 skipped, 0 failed, 449,0 s (2026-11-15 19:20)** | người dùng chụp màn hình; chậm nhất 47,6s; 42 test vượt ngân sách 1.0s (tự chuyển tầng chậm lần sau). Container CLI thật 19:21: `container list tests/fixtures/zip/sample.ipa` ra 3 entry; `plugin test zip` PASS (8 PASS, 0 FAIL, 3 SKIP); `plugin list` ra `zip [ok] M2`. Lưu ý: chạy trên fixture `sample.ipa`, không phải IPA game thật |
| iSH, thư mục cũ: cài đặt + `py_compile registry/ services/` | **PASS** | người dùng chụp màn hình, bộ cài đã chạy |
| iSH, thư mục cũ: chạy đủ bộ test, bản 393 test | **PASS: 390 passed, 3 skipped, 0 failed, 541,08 s (0:09:01)** | người dùng chụp màn hình 2026-11-15 (Python 3.9.16, pytest 6.2.4, `pytest tests`). Bản này chưa có 12 test R5 |
| iSH, `/root/pb3`: bản 527 test + `plugin new/test/disable/enable thu_nghiem` thật | **PASS: 524 passed, 3 skipped, 0 failed, 264,9 s (2026-11-15 18:28)**; plugin: `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (6 PASS, 0 FAIL, 5 SKIP)` (18:29) | người dùng chụp màn hình; 2 thư mục thu_nghiem đã dọn 18:31 |
| iSH, bản cũ 240 test | PASS: 237 passed, 3 skipped, 204,32 s | LỊCH SỬ, trước 66 test mới. Không tính cho bản hiện tại |

Lệnh chạy test trên iSH (có thanh phần trăm, KHÔNG chuyển hướng ra file, KHÔNG bấm Ctrl-C giữa chừng):
```
cd /root/pb3
python3 run_tests.py --tier full
```
Kỳ vọng: `collected 575`, khoảng `572 passed, 3 skipped`. Nếu một test đứng yên hơn ~5 phút hoặc có FAIL/LỖI, ghi lại tên test.

## 4. Đã làm hộ Claude 03 (đợt 1)
`registry/` (compatibility, loader, registry, arbitration), `services/plugin_manager.py` (`python3 menu.py plugin list`), thư mục `plugins/` và `plugins_user/` (rỗng), cấu hình 2 đường dẫn plugin, 63 test mới. Rà soát 01/02: không cần viết lại SDK; 9 điểm lệch/rủi ro ở `handoff/review_01_02.md`.

## 5. Nhiệm vụ của Claude 04 - chi tiết `handoff/account_04.md` (decisions #61-65)
- **VFS thật** (`sdk/containers.py`): XONG sandbox (ZipVFS/TarVFS/SingleStreamVFS/NestedVFS, `open_vfs`, `rebuild_zip`). `sdk/vfs.py` KHÔNG đổi.
- **Plugin container builtin** `plugins/zip/` (M2) + CLI `container list/read`: XONG sandbox (`plugin test zip` PASS; `container list/read` trên IPA mẫu thật).
- **Test mới**: 48 test (`tests/test_container.py` + `tests/test_vfs_real.py`); tổng sandbox 575 test OK.
- **Gói 04 COMPLETE 2026-11-15** (bằng chứng iSH trong mục 3 và `handoff/account_04.md` mục 5): suite 575 test PASS trên iSH 19:20 + `container list` / `plugin test zip` thật 19:21.

## 5b. Gói Claude 05 — COMPLETE (2026-11-15)
- **Unity backend** (`sdk/unity.py`, `sdk/lz4pure.py`, `services/unity_tool.py`): XONG sandbox (detector, UnityFS v6/v7, SerializedFile v15–22, backend AUTO/LIBRARY/NATIVE + `decisions`, planner, patcher PATCH_INPLACE/REBUILD_SECTION + diffverify, lệnh `unity inspect/read/patch` + `mod` M2, dry-run mặc định).
- **Test mới**: 68 test (`tests/test_unity.py`); tổng sandbox 643 test OK.
- **Gói 05 COMPLETE 2026-11-15** (bằng chứng iSH trong `handoff/account_05.md` mục 5): suite 643 test PASS trên iSH 20:50 (639 passed, 4 skipped) + Unity CLI thật 20:52/20:56 (inspect/read/patch trên fixture + patch lồng `nested.ipa`); inspect IPA thật 20:56 trả E_UNSUPPORTED_FORMAT — UnityFS v8 chưa hỗ trợ (decision #76).
- Sửa bug treo suite trên iSH do probe import UnityPy (decision #75): probe dùng `find_spec` + metadata, cache theo tiến trình; test từng treo nay chỉ 1,1s trên iSH.

## 5c. Gói Claude 05b — SANDBOX DONE (2026-11-15), iSH NOT RUN
- **Unity phiên bản cao nhất** (`sdk/unity.py`, `tests/fixtures/unity/build_fixtures.py`): UnityFS v8 + SerializedFile v23 (Unity 6, revision "6000.0.23f1", player "5.x.x"); sửa gating cờ theo unityRevision (decision #78) và header mở rộng v22+ big-endian (decision #80); max version đã kiểm chứng từ mã nguồn UnityPy 1.25.4 (UnityFS v8, SF v23, nén NONE/LZMA/LZ4/LZ4HC — không có Zstd; LZHAM/UnityWeb/bundle mã hóa vẫn không hỗ trợ, decisions #77).
- **Test mới**: 22 test (`tests/test_unity.py` 68 -> 90); tổng sandbox 665 test OK (12,0 s); đủ suite dưới venv UnityPy 1.25.4: 665 test OK (skipped=1).
- **iSH NOT RUN**: chờ Joseph giải nén zip 05b đè lên `/root/pb3` rồi chạy full suite (kỳ vọng 665 test, 661 passed, 4 skipped) + Unity CLI trên fixture v8/v23 + `unity inspect workspace/input/game.ipa` (kỳ vọng mới: CHẮC CHẮN với plan đầy đủ thay vì E_UNSUPPORTED_FORMAT). Xem `handoff/account_05b.md` mục 5.

Tài khoản nhận kiểm chứng iSH cho gói 05b trước (xem `handoff/account_05b.md` mục 5), rồi đọc prompt Claude 06 làm tiếp gói 06 (plugin định dạng văn bản và localization). Gói 05 đã COMPLETE (bằng chứng iSH bản 643 test trong `CURRENT_STATE.md` và `PROGRESS.md`).

## 6. Việc cần người dùng quyết / xác nhận
- **R3**: ĐÃ CHỐT 2026-11-15: `api_version` lệch (cũ hơn tối thiểu hoặc mới hơn lõi) bị vô hiệu hóa cả hai chiều (decisions #52). Không còn cần xác nhận.
- **R4**: tên `write_modes` của Claude 05 phải theo đặc tả 6.5 (`PATCH_INPLACE|REBUILD_SECTION|REBUILD_ALL`), không theo prompt.
- **R5**: ĐÃ CHỐT 2026-11-15 (hướng 1) và ĐÃ LÀM: khai M3/M4 thiếu `tests/corpus/<id>.json` hợp lệ thì vẫn nạp nhưng hiệu lực hạ (sàn M2) + cảnh báo (decisions #51). Chỉ kiểm hình thức, chưa chứng minh test đã chạy thật (việc của Claude 09).

## 7. Việc còn tồn đọng / lưu ý
- **`handoff/CLAUDE03_CONTINUE_HERE.md` đã có trong zip này** (tạo 2026-11-15, đã chuyển từ gốc vào `handoff/` trong đợt polish): tài khoản mới đọc file đó trước, rồi đọc file này (mục 5 và 9).
- Nạp plugin chạy mã plugin trong tiến trình, có guard + timeout best effort từ đợt 2 (decisions #41-45), KHÔNG phải sandbox: **không được xem là an toàn**. Đã có plugin sản phẩm đầu tiên (`zip`, M2, Claude 04) và đã thử với fixture IPA trong sandbox và trên iSH 19:21 (`container list` + `plugin test zip` PASS); chưa có plugin định dạng để thử trích xuất ngôn ngữ thật.
- `pb3.zip` (gói thứ hai cũ, 155 test) là bản CŨ: không dùng.

## 8. Quy ước làm việc với người dùng
- Trả lời tiếng Việt, ngắn gọn (màn hình điện thoại).
- **Mọi script/lệnh dài giao cho người dùng chạy PHẢI có thanh tiến độ** (người dùng yêu cầu "luôn luôn"). Đừng đưa lệnh chuyển hướng `> file.log` làm mất thanh tiến độ.
- Giao mã bằng **zip hoặc file tự giải nén** (stdlib, có thanh tiến độ, giữ lại `config/local.toml`, `workspace`, `logs`, `reports`, `kb`, `plugins_user`, bản cũ đổi tên `pb3.bak-<giờ>`), KHÔNG dán heredoc dài vào terminal (lần trước làm hỏng 4 file). Tên file lưu qua Files có thể bị đổi (gạch dưới thành dấu cách): dùng `ls pb3*` để xem tên thật, đặt trong ngoặc kép nếu có dấu cách.
- Báo trung thực PASS / FAIL / NOT RUN; kết quả sandbox không thay cho iSH.
- Khi sửa mã, không hạ tiêu chuẩn test, không ghi cứng đường dẫn, không dùng tính năng Python mới hơn 3.8.

## 9. Prompt tiếp tục cho tài khoản mới (bản cập nhật 2026-11-15: gói 05 COMPLETE + 05b sandbox DONE)
> Bạn tiếp quản dự án PB3 v3.1 (iSH-only, iPhone) ở Claude 05b (kiểm chứng iSH) rồi Claude 06. Trả lời tiếng Việt. Đọc theo thứ tự: `TIEN_DO_VA_NHIEM_VU.md`, `handoff/account_05b.md`, `handoff/decisions.md` (đặc biệt #77-80), đặc tả `PB3_v3.1_iSH_10Claude.txt`, prompt Claude 06 trong `PB3_v3.1_10_Claude_Prompts.txt`. Thư mục dự án là `/root/pb3` (mã KHÔNG ghi cứng). Gói 04 đã COMPLETE 2026-11-15 (iSH PASS bản 575 test + container CLI thật; bằng chứng trong `handoff/account_04.md` mục 5). Gói 05 đã COMPLETE 2026-11-15 (iSH PASS bản 643 test + Unity CLI thật; bằng chứng trong `handoff/account_05.md` mục 5). Gói 05b sandbox DONE 2026-11-15 (UnityFS v8 + SerializedFile v23, Unity 6; suite 665 test OK; bằng chứng sandbox + lệnh kiểm chứng iSH trong `handoff/account_05b.md` mục 3 và 5; iSH NOT RUN). Kiểm chứng iSH cho 05b trước, ghi bằng chứng vào `handoff/account_05b.md` mục 5, rồi làm Claude 06 (plugin định dạng văn bản và localization). Mọi lệnh giao người dùng phải có thanh tiến độ. Cuối phiên cập nhật `CURRENT_STATE.md`, `PROGRESS.md`, `handoff/account_05b.md` / `handoff/account_06.md`, `decisions.md` và bàn giao zip cả cây `pb3/` cùng file tự giải nén.

## 10. Gói cần đem sang tài khoản mới
1. `pb3_handoff.zip` (cả cây `pb3/`, mới nhất, sandbox 665 test OK; iSH đã PASS bản 643 test 2026-11-15, gói 03, 04 và 05 COMPLETE; gói 05b sandbox DONE, iSH NOT RUN). Không kèm bộ cài tự giải nén: giải nén bằng `unzip` rồi đặt thư mục `pb3` vào `/root/pb3`.
2. File này (`TIEN_DO_VA_NHIEM_VU.md`).
3. Đặc tả `PB3_v3.1_iSH_10Claude.txt` và bộ prompt `PB3_v3.1_10_Claude_Prompts.txt` — đã có trong zip này (nhận 2026-11-15), nằm ngoài thư mục `pb3/`.
4. Ảnh/kết quả chạy test trên iSH khi có.
