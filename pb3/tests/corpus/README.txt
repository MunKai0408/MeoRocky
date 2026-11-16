Thư mục bằng chứng M3/M4 (R5, decisions #51)
============================================

Thư mục này chứa bằng chứng test cho plugin khai maturity M3/M4.
Mỗi plugin như vậy cần một file: tests/corpus/<id>.json
(<id> là id của plugin, đúng như trong manifest của nó).

Định dạng file (JSON UTF-8, tối đa 64 KiB, chỉ đọc tĩnh, không chạy mã):
    {"id": "<id plugin>", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}}

Quy tắc hiệu lực (xem registry/evidence.py):
- M3 cần read + write đều "PASS".
- M4 cần thêm verify "PASS".
- Không đủ bằng chứng thì hiệu lực hạ xuống mức cao nhất được chứng minh,
  sàn là M2; plugin vẫn nạp, chỉ bị hạ hiệu lực + cảnh báo.
- Plugin khai M0-M2 không bị kiểm.
- Giá trị phải đúng chuỗi "PASS" (true hay "FAIL" đều không tính).
- File sai dạng sẽ bị tính như không có bằng chứng: id không khớp,
  results không phải object, không phải JSON, quá lớn,
  hoặc là symlink thoát khỏi thư mục này.

Ví dụ mẫu (CHỈ LÀ VÍ DỤ TRONG FILE NÀY, không phải file thật):
    {"id": "vi_du_plugin", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}}
File trên, nếu tồn tại thật, sẽ giữ M4 cho plugin "vi_du_plugin".

AI ĐƯỢC TẠO FILE NÀY:
- Chỉ tạo file <id>.json SAU KHI đã chạy thật test read/write/verify
  của plugin đó trên dữ liệu thật và đều PASS.
- Ghi rõ trong file bàn giao ngày chạy, lệnh chạy và kết quả.

HIỆN TRẠNG (2026-11-15):
- Chưa có file bằng chứng <id>.json nào trong thư mục này.
- Chưa có plugin sản phẩm nào khai M3/M4.
- KHÔNG được tạo file giả mạo nhận một plugin đã PASS khi chưa chạy thật.
  Loader chỉ kiểm HÌNH THỨC (file có, đúng id, đúng kết quả),
  không chứng minh test đã chạy thật; việc chạy lại để đối chiếu
  thuộc Claude 09 (SUPPORT_MATRIX).
