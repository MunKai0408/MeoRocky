# Kết quả kiểm chứng giới hạn 2GB — bản web PB3

Ngày: 2026-11-16. Pyodide 314.0.8. Chạy bridge.py trên bản vendor trong site/ (không dùng cây PB3 gốc).

## Cách kiểm biên

- Không tạo file 2GB thật. Trong Pyodide, monkeypatch `os.path.getsize` để hàm thật `bridge._check_size` nhận kích thước giả lập (1GiB, 1.5GiB, đúng 2GiB, 2GiB+1, 2.5GiB).
- Ngoài ra kiểm bằng file sparse thật trên host (truncate, ~0 byte đĩa) qua python3 hệ thống — xem mục file sparse bên dưới.

## Kết quả

- **no_http_fetch_during_load**: PASS — http fetch trong lúc loadPyodide: 0 (0s)
- **write_site_files**: PASS — 87 file pb3/ ghi vào /pb3web (0s)
- **max_web_input_value**: PASS — bridge.MAX_WEB_INPUT = 2147483648 (mong đợi 2147483648) (0s)
- **check_size_boundary**: PASS — 1GiB (1024MB): qua — mong đợi qua / 1.5GiB (1536MB): qua — mong đợi qua / 2GiB_chinh_xac (2048MB): qua — mong đợi qua / 2GiB_cong_1 (2048MB): chặn — mong đợi chặn, lỗi nhắc 2GB=true / 2.5GiB (2560MB): chặn — mong đợi chặn, lỗi nhắc 2GB=true (0s)
- **app_js_max_bytes**: PASS — MAX_BYTES = 2147483648 (mong đợi 2147483648); cổng f.size > MAX_BYTES=true; thông báo nhắc >2GB=true (0s)
- **texts_2gb_warning**: PASS — không còn "1GB" trong 4 file=true; index.html có ≤2GB + cảnh báo 1–1,5GB=true; README có tối đa 2GB + cảnh báo=true (0s)

Tổng: 6 mục, 0 FAIL.
