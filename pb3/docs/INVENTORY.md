# INVENTORY - khảo sát mã PB3 cũ (Claude 01)

Nguồn: `pb3_2.zip` (23 mục, bỏ `__MACOSX/` và `__pycache__/`). Đã giải nén ra bản sao
và chép vào `legacy/` nguyên byte (đối chiếu sha256: 15/15 tệp trùng khớp). Không sửa/xóa gì.
Môi trường cũ (theo `legacy/HANDOFF_legacy.md` + `.pyc` cpython-39): iSH, **Python 3.9**,
thư mục dự án cũ, terminal ~48 cột, `import UnityPy` ~45-60 s, người dùng dán lệnh bằng tay.
Không có test, không có requirements, không có git trong bản zip.

## Cây thư mục cũ (phẳng, chạy theo cwd)
| Tệp | Dòng | Vai trò | Phụ thuộc ngoài | Đánh giá |
|---|---|---|---|---|
| `menu_legacy.py` (menu.py cũ) | 86 | menu 4 mục + nhánh Space Marshals 2 | subprocess | **VI PHẠM**: nhánh game riêng; `BASE` là đường dẫn cứng ngoài dự án; gọi `sm2_json.py` (không tồn tại) |
| `pb3_pack.py` | 348 | đóng gói lại UnityFS (LZ4HC + khối thô), kiểm sha256 từng node | `lz4.block` | **GIỮ làm tri thức** cho Claude 05; cổ vật quý nhất; theo handoff cũ đã đạt trên dữ liệu thật (chưa tái kiểm ở đây) |
| `pb3_pack_v2.py` ("pb3 pack v2.py") | 286 | bản cũ của pb3_pack | `lz4.block` | **TRÙNG LẶP, lỗi thời** (thiếu sửa khối thô và thanh tiến độ; tên có dấu cách) |
| `unityfs_inspect.py` | 274 | đọc cấu trúc UnityFS, `--diag-unitypy` | `lz4.block`, `UnityPy` | GIỮ làm tri thức (Claude 05) |
| `unityfs_extract.py` | 88 | giải nén node từ bundle | inspect | GIỮ |
| `pb3_import.py` | 173 | thay cột 0 của bảng I2 rồi ghi lại bằng UnityPy | `UnityPy`, subprocess | **Đặc thù một game** (Path ID 1190, `out_datapack/`, `en.json`) |
| `pb3_tool.py` | 141 | xuất/kiểm JSON bản dịch | - | Đặc thù một game |
| `i2_layout.py` | 42 | parse/serialize MonoBehaviour I2Languages | - | Đặc thù (ứng viên ý tưởng cho plugin unity_serialized) |
| `i2_try2.py` | 15 | thí nghiệm | `UnityPy` | Script dùng một lần |
| `trichxuat.py` | 199 | dò/trích ngôn ngữ đa định dạng bằng regex | - | Ý tưởng cho Claude 06/07; **VI PHẠM**: `BASE` đường dẫn cứng, ghi `trich_xuat/` ngoài gốc; chỉ dựa tên file |
| `scout.py` | 98 | dò magic/cấu trúc zip | - | Ý tưởng cho diagnose (Claude 04+) |
| `patch_pack.py`, `patch_bar_pack.py` | 17/80 | vá một lần cho pb3_pack.py | - | Đã áp dụng (pb3_pack.py chứa `class Bar` và sửa khối thô); không cần chạy lại |
| `HANDOFF_legacy.md` | 131 | ghi chú phiên cũ | - | Nguồn dữ kiện môi trường + định dạng bundle |
| `pb3_times.json.sample` | 1 | thời gian đo các bước (file ẩn `.pb3_times.json`) | - | Tham khảo hiệu năng iSH thật |

Tất cả 13 tệp `.py` biên dịch được ở Python 3.8 (kiểm bằng `ast.parse(feature_version=(3,8))`).
Không phát hiện mã mạng (socket/urllib/http/requests). `subprocess` chỉ dùng để tự gọi script
của chính nó (menu_legacy, pb3_import).

## Dependency thực tế của mã cũ
- `UnityPy` (import lười, rất chậm trên iSH), `lz4` (`lz4.block`).
- Không có: requirements/pyproject. **Chưa có bằng chứng** rằng bản UnityPy/lz4 nào chạy ổn
  trên iSH ngoài lời kể trong handoff cũ (ảnh chụp người dùng); PB3 v3.1 không được coi đó là xác minh.

## Phần giữ / lỗi / trùng / vi phạm iSH-only
- **Giữ (tri thức tham khảo, chưa tích hợp):** pb3_pack, unityfs_inspect, unityfs_extract, i2_layout.
- **Trùng/lỗi thời:** pb3_pack_v2; patch_*.py.
- **Vi phạm đặc tả v3.1:** nhánh Space Marshals 2 (menu_legacy); đường dẫn cứng ngoài dự án (menu_legacy, trichxuat);
  ghi ngoài thư mục dự án (trichxuat); tên game/đường dẫn cứng (datapack, 1190, en.json...);
  phụ thuộc cwd (mọi script).
- **Đã xử lý ở bản mới:** menu.py mới không còn các mục trên; đường dẫn tuyệt đối chỉ còn là ví dụ
  `paths.input_extra` do người dùng chọn.

## Rủi ro đã ghi nhận
1. `legacy/` KHÔNG nằm trong đường import của PB3 mới; đừng import trực tiếp (đường dẫn cwd, tên game).
2. Mã cũ ghi `resources_vi.assets` đè nếu đã có (xem handoff cũ) - trái nguyên tắc "không ghi đè" của v3.1.
3. Tri thức cấu trúc bundle trong HANDOFF_legacy.md đến từ một game cụ thể, một phiên bản Unity (2022.3):
   không được khái quát thành "mọi Unity".
4. Thư mục người dùng khác (File Dịch Game, ESign..., Husk.ipa...) được nhắc trong handoff cũ KHÔNG nằm
   trong zip; PB3 mới không được đụng tới.
5. `import UnityPy` ~1 phút trên iSH: PB3 mới không import khi khởi động (có test chặn).
