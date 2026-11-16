# Rà soát Claude 01 / 02 / 02b (thực hiện đầu phiên Claude 03, 2026-11-14)

Phương pháp: giải nén `pb3_for_claude03.zip`, đọc START_HERE + đặc tả + handoff/decisions + `sdk/api.py`, `sdk/manifest.py`,
`sdk/types.py`, `core/errors.py`, `core/config.py`; chạy suite; đối chiếu đặc tả mục 2.2, 3, 4, Phụ lục A-D.

## Kết quả chạy
| Hạng mục | Môi trường | Kết quả |
|---|---|---|
| `python3 -m unittest discover -s tests -t .` (gói gốc, chưa sửa) | sandbox Python 3.13.18 | **240 test OK** (PASS) |
| Cùng lệnh khi chạy như tiến trình nền (`&`) | sandbox | 1 FAIL `test_first_sigint_sets_flag_second_raises`: tiến trình nền thừa hưởng SIGINT=SIG_IGN. Không phải lỗi mã; chạy tiền cảnh PASS |
| Số liệu iSH (237 passed/3 skipped/0 failed, 204 s) | iSH | **Không kiểm chứng được ở đây** (chỉ đọc lại từ CURRENT_STATE/handoff). Chưa chạy lại trên iSH sau phần 03: **NOT RUN ON DEVICE** |

## Đánh giá
- Claude 01 (nền móng): khớp phạm vi. Không thấy lỗi mới. Đủ: errors/paths/config/limits/safe_io/progress/menu/CLI.
- Claude 02/02b (SDK): khớp 4.2-4.4, Phụ lục A/B/D; Phụ lục B chạy nguyên văn. Không tìm ra lỗi chứng minh được cần viết lại SDK.
  Không đổi chữ ký SDK ở đợt 1.

## Điểm lệch / rủi ro cần người dùng biết (không sửa, chỉ ghi)
| # | Mức | Nội dung | Xử lý |
|---|---|---|---|
| R1 | thấp | Test SIGINT giả định tiến trình khởi chạy không bỏ qua SIGINT. Chạy suite bằng `&`/`nohup` trên iSH sẽ báo FAIL giả | **ĐÃ SỬA (decisions #55):** test tự đặt lại handler, chạy nền cũng PASS |
| R2 | thấp | `CURRENT_STATE.md` còn mục "Bổ sung ngoài prompt" ghi số cũ (sandbox 211, iSH 186) mâu thuẫn bảng trên (240/237) | Đã sửa trong bản cập nhật của 03 |
| R3 | trung bình | Đặc tả 4.5 vs 4.9 mâu thuẫn về api_version mới hơn lõi (vô hiệu hóa vs chỉ cảnh báo). 02b chọn vô hiệu hóa hai chiều (#25b); loader 03 theo đó | **ĐÃ CHỐT 2026-11-15:** người dùng xác nhận vô hiệu hóa cả hai chiều; không đổi mã (decisions #52) |
| R4 | trung bình | `write_modes`: đặc tả 6.5 (`PATCH_INPLACE/REBUILD_SECTION/REBUILD_ALL`) khác prompt Claude 05 (`IN_PLACE/REBUILD`) | Đã chốt dùng 6.5 (#25a). **Phải nhắc Claude 05** |
| R5 | trung bình | Phụ lục A: maturity M3/M4 "bắt buộc có test evidence" nhưng manifest/loader chưa kiểm bằng chứng (chưa có `tests/corpus/`) | **ĐÃ LÀM 2026-11-15 (hướng 1, decisions #51):** thiếu `tests/corpus/<id>.json` hợp lệ thì vẫn nạp nhưng hiệu lực hạ (sàn M2) + cảnh báo. Chỉ kiểm hình thức; đối chiếu chạy lại thuộc 09 (SUPPORT_MATRIX) |
| R6 | thấp | Đặc tả 2.2 liệt kê nhiều thư mục chưa có: `archive/ unity/ language/ modding/ kb/store batch/ config/locales.toml core/encoding.py core/textdetect.py sdk/testing.py` | Đúng kế hoạch (thuộc 04-09); `registry/ plugins/ plugins_user/ services/` đã tạo ở đợt 1 của 03 |
| R7 | thấp | `decisions #15` ghi "không thêm mã lỗi mới" nhưng SDK dùng `E_CORRUPT/E_NOT_IMPLEMENTED/E_CONFIG_INVALID/E_TOO_LARGE` (mã mở rộng của #1) | Nhất quán nếu hiểu "ngoài 6 mã mở rộng của #1"; không cần sửa |
| R8 | thông tin | Gói thứ hai `pb3.zip` là bản CŨ (HANDOFF "Claude 02 tiểu phần 0", 155 test, công cụ Unity `pb3_tool.py`, đường dẫn cứng cũ). Không phải nguồn của v3.1 | Không dùng, không sửa. Nguồn chuẩn: `pb3_for_claude03.zip` |
| R9 | trung bình | VFS thật chưa có (Claude 04): `read(path, max_bytes)` quy ước ở `sdk/vfs.py::read_policy` | Claude 04 phải dùng lại `read_policy` |
