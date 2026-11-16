# PB3 v3.1 - BÁO CÁO TIẾN ĐỘ (cập nhật 2026-11-15)

Nguồn sự thật ngắn gọn hơn: `CURRENT_STATE.md`. Quyết định kỹ thuật: `handoff/decisions.md`. Môi trường chuẩn: iSH, `/root/pb3` (xem `handoff/decisions.md` #40), `python3 menu.py`.

## 1. Tiến độ theo gói
Hoàn tất **5/10 gói (50%)**; gói 03 COMPLETE 2026-11-15 (C5 phần iSH PASS, xem mục 2); gói 04 COMPLETE 2026-11-15 (iSH PASS bản 575 test, xem mục 2); gói 05 COMPLETE 2026-11-15 (iSH PASS bản 643 test + Unity CLI thật, xem mục 2). Phần bổ sung 05b (UnityFS v8 + SerializedFile v23, Unity 6) sandbox DONE 2026-11-15 (665/665 OK, iSH NOT RUN, xem `handoff/account_05b.md`). Đợt polish (Wave 1–3 + màu sắc + giao diện chạy test + làm đẹp toàn bề mặt) sandbox DONE 2026-11-15 (756/756 OK, iSH NOT RUN, xem `handoff/account_polish.md`). Phần bổ sung ngoài prompt (mục 4) không tính vào 10 gói.

| Gói | Nội dung | Trạng thái | Bằng chứng |
|---|---|---|---|
| 01 | Nền móng (core, menu, CLI, cấu hình, log, an toàn ghi) | HOÀN TẤT | iSH: 155 test OK (skipped=2) |
| 02 / 02b | Mô hình dữ liệu, IR, Plugin SDK | HOÀN TẤT (02b; 02 đã bị thay) | 02b iSH: 237 passed, 3 skipped, 0 failed, 204,32 s. Gói 02 gốc chỉ PASS sandbox 186 test, chưa chạy đủ trên iSH thì đã bị 02b thay thế (SDK 1.0 lệch đặc tả) |
| 03 | Plugin loader, registry, điều phối | **HOÀN TẤT (2026-11-15, C5 phần iSH PASS)** | sandbox 527 test OK (524 + 3 hồi quy trùng id, decisions #60); iSH 2026-11-15 18:28: 524 passed, 3 skipped, 0 failed, 264,9 s; plugin `new/test/disable/enable thu_nghiem` thật PASS 18:29 |
| 04 | Container/archive/VFS thật | **HOÀN TẤT (2026-11-15, iSH PASS)** | sandbox 575 test OK (527 cũ + 48 mới); iSH 2026-11-15 19:20: 572 passed, 3 skipped, 0 failed, 449,0 s; `container list` + `plugin test zip` PASS 19:21 (trên fixture `sample.ipa`) |
| 05 | Unity (UnityPy, LZ4, ghi ngược) | **HOÀN TẤT (2026-11-15, iSH PASS)** | sandbox 643 test OK (575 cũ + 68 mới: UnityFS v6/v7, SerializedFile v15–22, backend AUTO/LIBRARY/NATIVE, planner, patcher + diffverify, lệnh `unity`/`mod`); UnityPy 1.25.4 (venv kiểm chứng) đọc đúng mọi fixture + output đã patch; sửa bug treo suite trên iSH do probe import UnityPy (decision #75); iSH 2026-11-15 20:50: 639 passed, 4 skipped, 0 failed, 719,4 s; Unity CLI thật 20:52/20:56 (inspect/read/patch trên fixture + patch lồng `nested.ipa`); giới hạn: UnityFS v8 chưa hỗ trợ (game 9 Kings thật dùng bundle v8, decision #76) |
| 06-10 | Định dạng, trích xuất, mod, batch, hỗ trợ/KB, hoàn thiện | chưa bắt đầu | |

## 2. Bằng chứng kiểm thử
- Sandbox (Python 3.12.3): **665 test OK** (643 như trước + 22 test Claude 05b: `test_unity.py` 68 -> 90; theo số đếm unittest; `python3 run_tests.py --tier full`: `Ran 665 tests in 12.0s OK`). Gói 05b sandbox xong 2026-11-15 (UnityFS v8 + SerializedFile v23, Unity 6; UnityPy 1.25.4 đọc đúng mọi fixture mới + output đã patch; đủ suite dưới venv UnityPy 665 test OK skipped=1; xem `handoff/account_05b.md`, decisions #77–80). Gói 05 sandbox xong 2026-11-15 (CLI thật trên bản sao: `unity inspect/read/patch`, `mod` dry-run + ghi, patch IPA chỉ xuất bundle; UnityPy 1.25.4 đọc đúng mọi fixture + output đã patch; xem `handoff/account_05.md`). Gói 04 sandbox xong 2026-11-15 (CLI thật trên bản sao: `container list/read`, `plugin test zip` PASS, writer round-trip; xem `handoff/account_04.md`). iSH (Python 3.9.16, thư mục cũ, 2026-11-15): bản 393 test **PASS - 390 passed, 3 skipped, 0 failed, 541,08 s**.
- **iSH 2026-11-15 (bản 527 test, trong `/root/pb3`, 18:28): PASS — 524 passed, 3 skipped, 0 failed, 264,9 s** (134 test thêm sau bản 393 đã chạy xong, 0 FAIL). 3 skip trên iSH: 2 thiếu `tomllib`, 1 máy có `/dev/location`. Plugin thật 18:29: `plugin new format thu_nghiem` -> `plugin test` `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (6 PASS, 0 FAIL, 5 SKIP)` -> `disable` (lưu `config/local.toml`) -> `list` `[disabled]` -> `enable` (`[ok]`); 2 thư mục thu_nghiem đã dọn 18:31.
- **iSH 2026-11-15 (bản 575 test, trong `/root/pb3`, 19:20): PASS — 572 passed, 3 skipped, 0 failed, 449,0 s** (48 test Claude 04 đã chạy xong trên Python 3.9.16, 0 FAIL; chậm nhất 47,6s `TestRealCli::test_disable_survives_new_process`; 42 test vượt ngân sách 1.0s, tự chuyển tầng chậm lần sau). Container CLI thật 19:21: `container list tests/fixtures/zip/sample.ipa` ra 3 entry (Info.plist 93 byte, level.unity3d 300 byte, config.txt 8 byte, đều deflated); `plugin test zip` = `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (8 PASS, 0 FAIL, 3 SKIP)`; `plugin list` ra `zip [ok] M2`. Lưu ý: `container list` chạy trên file fixture `sample.ipa`, không phải IPA game thật.
- **iSH 2026-11-15 (bản 643 test, trong `/root/pb3`, 20:50): PASS — 639 passed, 4 skipped, 0 failed, 719,4 s** (68 test Claude 05 đã chạy xong trên Python 3.9.16, 0 FAIL; chậm nhất 114,7s `TestImpact::test_indirect_dependency_is_followed`; 9 test vượt ngân sách 1.0s, tự chuyển tầng chậm lần sau; test từng treo nay chỉ 1,1s — bản sửa probe decision #75 chạy đúng). Unity CLI thật 20:52: `unity inspect` sample.unity3d (CHẮC CHẮN, UnityFS v7 2019.4.40f1, plan 6 dòng) + nested.ipa (CÓ KHẢ NĂNG, plan 8 dòng), `unity read` in `Xin chao 9 Kings!`, `unity patch` dry-run KHÔNG ghi file rồi `--allow-write` ghi `workspace/output/resources/resources.assets`; patch lồng trên `nested.ipa` 20:56 chỉ xuất `workspace/output/nested/level.unity3d`. Inspect IPA thật 20:56 trả `E_UNSUPPORTED_FORMAT` — UnityFS v8 chưa hỗ trợ (game 9 Kings thật dùng bundle v8, decision #76); gói 05 COMPLETE.
- Lệnh kiểm tra trên iSH: `cd /root/pb3 && python3 run_tests.py --tier full` (`python3 -m pytest tests -v` cũng kỳ vọng `collected 643 items`). Bản 643 test đã PASS trên iSH 2026-11-15 (639 passed, 4 skipped); gói 05 COMPLETE.

## 3. Sự cố đã xử lý
Lần dán heredoc làm hỏng 4 file (`sdk/plugin.py`, `sdk/example_kv.py`, `tests/test_sdk.py`, `docs/SDK_CONTRACT.md`) do câu lệnh `cd` lọt vào giữa nội dung.
Phần mất được dựng lại từ test và hợp đồng (không phải bản gốc). Từ nay chuyển file bằng zip + `unzip`, không dán file dài vào terminal.

## 4. Bổ sung phiên này
### 4.1 Thanh tiến độ tổng 0 -> 100% + thời gian còn lại (`core/overall.py`)
- Hiển thị: `[██████░░░░]  63% 2/3 Tên bước còn ~2m10s`; xong: `[██████████] 100% tên-việc xong 1m05s`.
- Mỗi bước có thời lượng dự kiến, tự học từ các lần chạy trước (`workspace/cache/overall_times.json`); lần đầu chỉ là ước đoán nên "còn ~" có thể lệch.
- Lệnh chạy "mờ" (không báo tiến độ): thanh tiến theo thời gian tới 90% rồi nhích dần, không chạm 100% cho tới khi xong; không bao giờ lùi.
- Luồng nhịp vẽ lại mỗi giây nên đồng hồ vẫn chạy khi lệnh đang treo. `print()` và `sys.stderr.write()` trong lúc chạy không làm vỡ thanh.
- Chỉ hiện khi là terminal thật (tty). Ép: `PB3_PROGRESS=1` bật, `PB3_PROGRESS=0` tắt; hoặc `progress.overall = false` trong `config/local.toml`.
- Đã gắn: mọi lệnh CLI (Khởi động -> Chạy lệnh) và mục Chẩn đoán > Môi trường iSH. Menu tương tác không gắn thanh cho màn hình có nhập liệu.
- Cách dùng trong dịch vụ mới (Claude 03 trở đi):
```
with app.make_overall("ten-viec", [("Doc", 5), ("Ghi", 3)]) as ov:
    ov.begin("Doc")
    ...                      # trong vong lap: app.report_progress(da_xong / tong, note="file x")
    ov.begin("Ghi")
    ...
```
  Hoặc `with ov.stage("Doc"): ...`. Cờ hủy (`app.cancel`) được kiểm khi báo tiến độ.

### 4.2 Chạy nền qua vị trí (`core/keepalive.py`)
- Cơ chế: một luồng nền đọc `/dev/location` của iSH để iOS coi iSH đang dùng vị trí (tương đương `cat /dev/location > /dev/null &`).
- Bật: `python3 menu.py --keepalive <lệnh>`, hoặc Chẩn đoán > [4], hoặc `keepalive.enabled = true` trong `config/local.toml`. Mặc định TẮT.
- Điều kiện: iOS sẽ hỏi quyền vị trí cho iSH khi bật lần đầu; muốn chạy khi khóa màn hình nên cho phép ở mức luôn. Tốn pin hơn.
- **Giới hạn trung thực:** trạng thái "ĐANG ĐỌC" chỉ chứng minh đã mở được thiết bị. PB3 không thể tự chứng minh iOS giữ iSH. Cần thử trên máy: bật, chạy lệnh dài, khóa màn hình vài phút, mở lại xem tiến trình còn chạy.
- Không có `/dev/location` thì báo "KHÔNG KHẢ DỤNG", không giả lập.

## 5. Việc kế tiếp
1. Claude 06 sandbox DONE (2026-11-16, 1025/1025 OK; xem `handoff/account_06.md`) + Claude 07 sandbox DONE (2026-11-16, 1080/1080 OK; xem `handoff/account_07.md`). Chờ Joseph kiểm chứng iSH 1 lần cho cả hai (full suite + extract CLI, xem `handoff/account_07.md` §5). Sau đó tài khoản mới đọc prompt Claude 08 rồi làm tiếp gói 08.
2. Thử thật hai tính năng mới trên iPhone: `python3 menu.py --keepalive diagnose --ish` (xem thanh chạy) và kiểm tra chạy nền như mục 4.2.
3. Dọn dòng `disabled` của thu_nghiem trong `config/local.toml` trên iSH (2 thư mục thu_nghiem đã xóa 18:31).

## 6. Hạn chế còn tồn tại
- VFS thật: đã có (Claude 04 COMPLETE 2026-11-15, iSH PASS). Loader + isolation (timeout hook, guard socket/subprocess/ghi file) đã có ở mức best effort, KHÔNG phải sandbox (decisions #42).
- Đặc tả `PB3_v3.1_iSH_10Claude.txt` và bộ prompt `PB3_v3.1_10_Claude_Prompts.txt` đã có trong zip này (nhận 2026-11-15, nằm ngoài thư mục `pb3/`); tài khoản mới đọc kèm khi tiếp quản.
- UnityPy 1.25.4 + lz4 đã cài trên iSH (import OK 2026-11-15 18:21); gói 05 COMPLETE 2026-11-15 (iSH PASS bản 643 test + Unity CLI thật). Gói 05b (UnityFS v8 + SerializedFile v23) sandbox DONE 2026-11-15, iSH NOT RUN (decisions #77–80). Đợt polish sandbox DONE 2026-11-15 (756 test OK; decisions #81+; iSH NOT RUN). Đợt tối ưu sandbox DONE 2026-11-16 (795 test OK; decisions #107–116; iSH NOT RUN). Claude 06 sandbox DONE 2026-11-16 (1025 test OK; decisions #117–128; iSH NOT RUN). Claude 07 sandbox DONE 2026-11-16 (1080 test OK; decisions #129–141; iSH NOT RUN).
- Dự kiến "còn ~" chỉ là ước đoán, đặc biệt ở lần chạy đầu của mỗi loại việc.
