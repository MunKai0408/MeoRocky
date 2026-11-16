# PB3 v3.1 — TIẾP TỤC Ở ĐÂY (Claude 03, đợt 2)

Đọc file này trước. Trả lời tiếng Việt, ngắn gọn.

## Trạng thái
- Claude 01 (nền móng): COMPLETE.
- Claude 02: đã bị thay bằng 02b (SDK v3). 02b: COMPLETE.
- Claude 03 (loader, registry, isolation, conformance kit): **COMPLETE (2026-11-15)**.
- Claude 04-10: chưa bắt đầu.

A (isolation), B (conformance kit, `plugin new/test`), C1 (plugin nhiều file),
C2 (R5, bằng chứng M3/M4), C3 (lưu bật/tắt + menu), C4 (R3) và C5 đã xong.
Sandbox: **527/527 test OK** (524 + 3 test hồi quy trùng id, decisions #60).
Trên iSH: **PASS bản 527 test 2026-11-15 18:28** (524 passed, 3 skipped, 0 failed,
264,9 s, trong `/root/pb3`) + plugin `new/test/disable/enable thu_nghiem` thật
PASS 18:29 (`KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU)`, 6 PASS, 0 FAIL, 5 SKIP).

## Đọc theo thứ tự
1. File này (`CLAUDE03_CONTINUE_HERE.md`)
2. `TIEN_DO_VA_NHIEM_VU.md`
3. `handoff/account_03.md`
4. `handoff/TASKS_CLAUDE03.md`
5. `handoff/decisions.md`
6. Đặc tả `PB3_v3.1_iSH_10Claude.txt` và bộ prompt `PB3_v3.1_10_Claude_Prompts.txt`
   (đã có trong zip này, nhận 2026-11-15, nằm ngoài thư mục `pb3/`).

Thư mục dự án trên iSH: `/root/pb3`. Mã không ghi cứng đường dẫn này.

## Đã xong: C5 phần iSH (2026-11-15) — 03 COMPLETE

Suite trong `/root/pb3`: `Ran 527 tests in 264.9s OK (skipped=3, lỗi=0, fail=0)`
(524 passed, 3 skipped). Plugin thật: `plugin new format thu_nghiem` ->
`plugin test` PASS (CHƯA KIỂM DỮ LIỆU) -> `disable` (lưu `config/local.toml`) ->
`list` `[disabled]` -> `enable` (`[ok]`). 2 thư mục thu_nghiem đã dọn trên iSH
18:31 (`rm -rf` chạy sạch); dòng `disabled` trong `config/local.toml` chưa có
bằng chứng đã xóa. Menu-toggle bằng số KHÔNG chạy riêng trên iSH (chỉ sandbox
+ suite C3 đã PASS trên iSH).

Bằng chứng đầy đủ đã ghi vào `handoff/account_03.md` (mục cuối),
`CURRENT_STATE.md`, `PROGRESS.md`.

## Việc tiếp theo: Claude 04
- Tài khoản mới đọc prompt Claude 04 trong `PB3_v3.1_10_Claude_Prompts.txt`
  và đặc tả §5, làm container/ZIP/VFS thật.
- File này chỉ còn là bản ghi bàn giao của gói 03; đừng sửa trạng thái 03 nữa.
