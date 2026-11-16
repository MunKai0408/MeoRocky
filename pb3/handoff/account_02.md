> LƯU Ý (02b): bản này mô tả SDK 1.0 đã bị thay bằng SDK v3 — xem handoff/account_02b.md và docs/SDK_CONTRACT.md.

# Bàn giao Claude 02 -> Claude 03

- Trạng thái: **COMPLETE trong phạm vi Claude 02** (sandbox PASS; iSH: xem bảng bằng chứng).
- Ngày: 2026-11-14. Đặc tả `PB3_v3.1_iSH_10Claude.txt` KHÔNG có trong gói chuyển sang phiên này; Claude 02 làm theo prompt 02 + `decisions.md`.
  Claude 03 phải đối chiếu hợp đồng bên dưới với đặc tả và báo lệch.

## Đã làm
1. Việc mở số 1 của Claude 01: `errors=1` trên iSH ở lần đầu KHÔNG tái hiện (3 lần sau: OK, skipped=2 do thiếu tomllib). Nghi timeout test
   subprocess khi iSH chậm (ước đoán). Đã thêm `tests/helpers.py::subprocess_timeout` (x5 trên iSH, `PB3_TEST_TIMEOUT_SCALE`).
2. Tiểu phần 1: `core/model.py` + `tests/test_model.py` (7 test).
3. Tiểu phần 2: `sdk/ir.py` + `tests/test_ir.py` (7 test); thêm `sdk/` vào `tests/test_rules_ish_only.py`.
4. Tiểu phần 3-4: `sdk/types.py`, `sdk/vfs.py`, `sdk/manifest.py`, `sdk/plugin.py`.
5. Tiểu phần 5: `sdk/example_kv.py` (plugin ví dụ), `tests/test_sdk.py` (17 test), `docs/SDK_CONTRACT.md`, `decisions.md` #14-19.

## Hợp đồng cho Claude 03 trở đi
Xem `docs/SDK_CONTRACT.md` (nguồn chính). Tóm tắt: plugin khai manifest (`parse_manifest`), cài `do_probe/do_extract/do_writeback/do_validate`,
chỉ truy cập file qua `VFS` bằng `InnerPath`; read-only = không khai `writeback`; mã lỗi dùng `core.errors.ERROR_SPECS`; IR 1.0 / API 1.0.

## Bằng chứng
| Hạng mục | Môi trường | Kết quả |
|---|---|---|
| Suite 186 test | sandbox Python 3.12 | PASS |
| Suite 155 test (trước Claude 02) | iSH 3.9.16 | PASS (OK, skipped=2) x3; 1 lần `errors=1` không tái hiện |
| `test_model`, `test_ir`, `test_rules_ish_only` (28 test) | iSH 3.9.16 | PASS (50 s) |
| `test_sdk` (17 test) và suite đầy đủ 186 test sau khi cài SDK | iSH | **NOT RUN ON DEVICE** (người dùng cần chạy, lệnh dưới) |
| UnityPy / lz4 trên iSH | iSH | **NOT RUN** (ngoài phạm vi Claude 02; Claude 04-05) |

Lệnh người dùng chạy trong iSH: `cd /root/pb3 && python3 -m unittest discover -s tests -t . -v > /root/t.log 2>&1; tail -4 /root/t.log`
(kỳ vọng `Ran 186 tests`, `OK (skipped=2)`).

## Hạn chế / còn tồn tại
- VFS thật chưa có (chỉ `MemoryVFS`); loader, timeout hook, cấm socket/subprocess ở plugin chưa có (Claude 03).
- Chính sách phiên bản chặt: IR minor mới hơn bị từ chối. Chưa có migration IR (chưa cần với 1.0).
- `from_dict` từ chối trường lạ; nếu cần đọc xuôi (forward-compat) phải đổi chính sách và tăng version.
- `kv` xử lý CRLF chưa đặc biệt (ký tự `\r` nằm trong value). Chỉ là ví dụ.
- Lỗi `errors=1` lần đầu trên iSH chưa rõ nguyên nhân; nếu quay lại hãy chạy với `-v` và lưu log.

## Việc kế tiếp (Claude 03: plugin loader + ma trận hỗ trợ)
1. Đọc `docs/SDK_CONTRACT.md`, `decisions.md`, `CURRENT_STATE.md`. Chạy suite trên iSH, xác nhận 186 test.
2. Viết `registry/`: nạp manifest từ thư mục `plugins/`, import `entry`, tạo `Plugin`, ép timeout và cấm socket/subprocess.
3. Cài VFS thật (filesystem + container) dùng `core.safe_io`, `core.limits`; nối vào menu mục [3] qua `services/<tên>.py` (decisions #9).
4. Không đổi `API_VERSION`/IR nếu không có lý do tương thích; nếu đổi, ghi vào `decisions.md`.
