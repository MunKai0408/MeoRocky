Plugin của bạn. Thư mục con <id>/plugin.py có MANIFEST (xem docs/SDK_CONTRACT.md, đặc tả Phụ lục A-B).
CẢNH BÁO: thêm plugin = chạy code trên máy bạn. Chỉ dùng plugin bạn tin cậy; PB3 không tải plugin từ Internet.
Trùng id với builtin: plugin ở đây thắng (có cảnh báo).
Tạo mới: python3 menu.py plugin new <kind> <id>; kiểm tra: python3 menu.py plugin test <id> (fixture ở tests/fixtures/<id>/).
Tắt/bật: python3 menu.py plugin disable <id> | plugin enable <id> (hoặc menu mục 3); lưu ở config/local.toml, khóa plugins.disabled.
Nhiều file: đặt file phụ cạnh plugin.py và import TƯƠNG ĐỐI (from .helper import X; thư mục con cần __init__.py).
`import helper` không chạy. Mọi .py trong thư mục plugin đều bị quét tĩnh (tối đa 64 file, sâu 4 cấp, 4 MiB; không symlink).
