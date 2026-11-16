# RÀ SOÁT TỔNG CUỐI — NHÓM F2/F5/F6 (hạng mục a)

Nhóm: fix nợ C10 (F6, F5, F2) + rà soát prompt 01–10 đối chiếu mã hiện tại.
Phạm vi file: `sdk/batch.py`, `core/file_dialog.py`, `ui/screens.py` (vùng
`diagnose_menu` + helper liên quan), `services/diagnostics_tool.py`,
`tests/test_final_review.py` (mới). KHÔNG build zip, KHÔNG sửa
`handoff/decisions.md` hay `CURRENT_STATE.md` (coordinator hợp nhất).
Ngày: 2026-11-16 (Joseph đang ngủ; làm việc trong cây
`~/workspace/pb3_fix/pb3`, xuất phát 1395 test, zip C10 đã kiểm chứng).

## 1. Đã làm xong

### 1.1 F6 — Menu [5][3] Inventory/hex/strings: TRIỂN KHAI THẬT (xong)
- `services/diagnostics_tool.py` MỚI (trước đây không tồn tại dù
  `ui/handlers.py:49` MENU[4] đã trỏ tới — đó là lý do [5][3] chỉ hiện stub
  "CHƯA TRIỂN KHAI"): 
  - (a) inventory: mở container qua `sdk/containers.open_vfs` (ZIP/IPA/TAR/
    GZ/XZ/BZ2/PCK/PAK dùng chung một đường), liệt kê tên + kích thước +
    method, kèm entry bị chặn + cảnh báo; không phải container thì báo rõ
    mã lỗi (vd `E_UNSUPPORTED_FORMAT`) + gợi ý `unity inspect` khi là bundle
    UnityFS.
  - (b) hex dump: 8 byte/dòng (`offset + hex + ASCII`, dòng ≤ 43 cột, vừa
    màn hình 30–44 cột), phân trang 16 dòng/trang, hỏi offset bắt đầu.
  - (c) strings: quét theo khối 256KB (không nạp cả file), chuỗi in được
    ≥ 4 ký tự mặc định, có offset, lọc từ khóa (không phân biệt hoa/thường),
    hỗ trợ UTF-8 (giữ tiếng Việt có dấu) và UTF-16 (file ≤ 8MB, lớn hơn báo
    rõ giới hạn); dừng sớm khi đủ limit thì báo trung thực "ít nhất N chuỗi".
  - (d) encoding sơ bộ: BOM (UTF-8/16/32) → thử UTF-8 → thử UTF-16 theo
    tỉ lệ ký tự ASCII + chẵn/lẻ byte NUL; chỉ "chắc chắn" khi có BOM hoặc
    UTF-8 hợp lệ, còn lại ghi "chỉ là phỏng đoán".
  - CHỈ ĐỌC, không ghi gì (không có dry-run vì không có ghi ngược).
  - CLI: `diagnose inventory|hex|strings <file>` (thêm positional `extra` +
    cờ `--offset/--length/--keyword/--min-len/--limit` qua `add_arguments`,
    không sửa `ui/cli.py`); `diagnose <file>` mặc định là inventory;
    `diagnose inventory` không kèm file → `E_CONFIG_INVALID` "thiếu file".
  - Tiến độ: quét strings báo qua `app.report_progress` (byte đã đọc/tổng);
    inventory/hex đọc có giới hạn nên không cần thanh riêng.
- `ui/screens.py::diagnose_menu` [3] nay chạy điều tra thật (xem §1.2).
- Test khóa: 24 test mới trong `tests/test_final_review.py` (xem §3).

### 1.2 F5 — `core/file_dialog.py` đã nối (xong)
- Luồng 1 (dịch vụ): `services/diagnostics_tool.py::run_interactive` chọn
  file bằng `choose_path` (phân trang, gõ số để chọn, `q` hủy).
- Luồng 2 (menu chẩn đoán): `ui/screens.py::_diagnose_investigate` (mới,
  gọi từ `diagnose_menu` [3]) cũng chọn file bằng `choose_path` rồi mới gọi
  `diagnostics_tool.investigate_file`; module thiếu thì rơi về `run_feature`
  để hiện "chưa triển khai" (không giả lập).
- Cả hai luồng đều có test khóa chạy thật bằng input kịch bản.

### 1.3 F2 — `sdk/batch.py` import lười `services`: SỬA MỘT NỬA (xong phần mod)
- ĐÃ SỬA — `_mod_job` (dòng 794–795, 853 cũ): 4 helper của
  `services.unity_tool` (`_backend`, `_read_file_bytes`, `_resolve_vpath` /
  `_read_segment`, `_default_output_rel`) đã chuyển xuống `sdk/batch.py`
  thành adapter mức sdk (`_sdk_make_backend`, `_sdk_read_file_bytes`,
  `_sdk_resolve_vpath`, `_sdk_read_vpath_segment`, `_sdk_default_output_rel`,
  `_sdk_unity_usage`), import lười `sdk.*` trong hàm theo phong cách sẵn có
  của file. Thông điệp lỗi giữ nguyên từng chữ (cả `message_vi` "Tham số
  lệnh Unity không hợp lệ." và hint "Dùng: unity patch ...") — test khóa so
  từng trường lỗi giữa adapter sdk và `unity_tool` (cả đường đúng lẫn 2
  đường lỗi). Batch tests vẫn xanh (35 test) + test #180 mới qua adapter.
- Giá phải trả (ghi rõ, không giấu): trùng lặp có chủ ý với
  `services/unity_tool.py` — nếu bên kia đổi logic/thông điệp, bản sao ở
  đây phải đổi theo (test khóa pin cả hai phía để bắt lệch). Bước dọn đúng
  là chuyển các helper sang `sdk/unity.py` rồi để `unity_tool` delegate —
  việc đó cần sửa file ngoài phạm vi đợt này, để coordinator xếp lịch.
- CHƯA SỬA, CÓ LÝ DO — `_get_registry` (dòng 427): import lười
  `services.language_tool` được GIỮ LẠI. Lý do kỹ thuật cụ thể:
  1. Registry phải được DỰNG từ root + config của app
     (`registry.registry.build_registry`); sdk KHÔNG được import registry —
     test khóa `tests/test_registry.py::TestArchitectureRules.
     test_core_does_not_import_registry` cấm cả `core/` lẫn `sdk/` import
     `registry` (tiền lệ #129/#175). Chuyển `build_registry` vào sdk sẽ làm
     test kiến trúc đỏ.
  2. Bỏ fallback mà không tiêm registry thì batch extract thật qua CLI sẽ
     lỗi `E_PLUGIN_INVALID` "batch extract cần registry", vì
     `services/batch_tool.py:92,167` dựng `BatchRunner(app=app)` mà không
     truyền registry.
  3. Cách sửa đúng là TIÊM registry từ tầng services (`BatchRunner` đã nhận
     `registry=` ở constructor; `services/batch_tool.py` cần dựng registry
     rồi truyền vào) — việc đó thuộc `services/batch_tool.py`, ngoài phạm
     vi file của nhóm này. Đề nghị coordinator xếp cho nhóm sở hữu
     `batch_tool` (đề xuất quyết định #190 dưới đây).
- Sau sửa: `sdk/` chỉ còn đúng 1 import `services` (lười, trong
  `_get_registry`); `sdk/batch.py` không còn nhắc `unity_tool` ở mã chạy
  (chỉ còn trong ghi chú giải thích).

### 1.4 #180 (C10 đã sửa): XÁC MINH XANH, không làm lại
- `tests/test_unity.py::TestPatcher` (20 test) + 
  `tests/test_batch.py::TestBatchRun.test_mod_longer_within_same_alignment_escalates`
  + test mới `TestF2SdkAdapter.test_180_vẫn_xanh_qua_adapter_sdk_mới`
  (batch mod thật qua adapter sdk mới, "Xin chao" → "Xin chào", file đã
  patch đọc lại đúng) đều xanh.

## 2. Rà soát prompt 01–10: phát hiện còn lại (theo từng acc)

| # | Acc | Mức | Phát hiện (file/dòng) | Trạng thái |
|---|---|-----|------------------------|------------|
| R1 | 04+ | thấp | `services/diagnostics_tool.py` không tồn tại dù `ui/handlers.py:49` đã trỏ tới → [5][3] chỉ hiện stub | ĐÃ SỬA (§1.1) |
| R2 | 10 | thấp | `core/file_dialog.py` chỉ test dùng, không nối sản phẩm (#184) | ĐÃ SỬA (§1.2) |
| R3 | 10 | thấp | `sdk/batch.py` import lười `services` (#183): mod job | ĐÃ SỬA (§1.3) |
| R4 | 10 | thấp | `sdk/batch.py:427` `_get_registry` import lười `services.language_tool` | ĐỂ LẠI, có lý do (§1.3) — cần tiêm registry từ `services/batch_tool.py` |
| R5 | 10 | thấp | `tests/test_app_menu_cli.py:164-175` `test_unimplemented_shows_clear_status_and_changes_nothing` khẳng định [5][3] "CHƯA TRIỂN KHAI" — nay sai tiền đề, là fail duy nhất còn lại (fast 1345 test: 1343 đạt, 1 fail, 0 lỗi) | ĐỂ LẠI cho coordinator (file ngoài phạm vi nhóm; xem §4.1) |
| R6 | 10 | thấp | `ui/handlers.py` MENU[4] Feature diagnose vẫn maturity mặc định `M0 PLANNED` (status tự hiện "đã nối dịch vụ" vì module đã có, nhưng maturity chưa cập nhật M2) | ĐỂ LẠI cho coordinator (§4.2) |
| R7 | 10 | thấp | `README.txt:109` còn ghi "[3] Inventory/hex/strings chưa triển khai"; `CURRENT_STATE.md` dòng Claude 10 còn ghi "[3] ... chưa triển khai" + nợ #183/#184 | ĐỂ LẠI cho coordinator (§4.3) |
| R8 | — | thấp | Heuristic đoán UTF-16 cũ (mọi ký tự `isprintable()`) đoán nhầm dữ liệu nhị phân thành UTF-16LE (ký tự CJK giải mã sai vẫn "in được") | ĐÃ SỬA trong lúc làm (§3, test khóa bắt được) |
| R9 | — | thấp | `_run_to_strings` decode cả dãy UTF-8 một lần: byte rác (`\xff`) làm hỏng cả dãy, tách đôi chuỗi tiếng Việt "Xin chào" | ĐÃ SỬA: tách bằng regex dãy UTF-8 hợp lệ (§3) |
| R10 | — | thấp | `total` khi dừng sớm vì limit bị báo như tổng toàn file | ĐÃ SỬA: báo "ít nhất N chuỗi (dừng sớm...)" (§3) |
| R11 | — | thấp | `/tmp` tmpfs 512M đầy 73% do file bench/IPA + thư mục tạm của các nhóm song song → fast tier chạy với `/tmp` mặc định lỗi 323 lỗi `OSError Errno 28` | ĐỂ LẠI (§4.4): chạy kiểm chứng với `TMPDIR` sang đĩa lớn thì xanh (trừ R5) |
| R12 | 06 | — | Nghi thiếu `.stringsdict`/`.xcstrings` (prompt 06 §2) — kiểm tra thì ĐÃ CÓ trong `plugins/apple_plist` (parse + ghi lại, giữ encoding/BOM) | Không phải lỗi |
| R13 | 08 | thấp | `.locmeta` nhị phân, PCK v0/v3, PAK nén/mã hóa/Oodle vẫn chưa hỗ trợ (đã ghi trong `handoff/account_08.md` + `CURRENT_STATE.md`) | Ngoài phạm vi nhóm (nhóm engine khác) |
| R14 | 10 | — | Tiêu chí 1 (chạy trong iSH) vẫn NOT RUN ON DEVICE — chờ Joseph | Không đổi |
| R15 | — | thấp | `tests.test_runner.TestPlanCounts.test_plan_counts_match_real_load` fail thoáng qua trong full run (`1536 != 1534`): số đếm tĩnh ≠ số nạp thật vì nhóm khác sửa file test giữa lúc chạy; chạy riêng ngay sau đó xanh (`1536 == 1536`) | Không phải lỗi code; coordinator chạy lại full ở bước tích hợp |

Không phát hiện thêm lỗi trong phạm vi rà soát của nhóm (batch / file_dialog /
diagnose / diagnostics). Các acc 01–03, 05, 07, 09 không còn lệch nào thuộc
phạm vi nhóm mà test hiện tại chưa bao phủ (suite fast + full đều xanh trừ R5).

## 3. Kiểm chứng

- Test mới: `python3 -m unittest tests.test_final_review` → **24 test OK**
  (F6: inventory/hex/strings/encoding + CLI; F5: picker 2 luồng; F2: không
  còn import `unity_tool` + thông điệp lỗi giống hệt + #180 qua adapter mới).
- Batch: `tests.test_batch + tests.test_batch_cli + test_unity.TestPatcher`
  → 54 test OK. Luật kiến trúc: `test_rules_ish_only + test_rules_where +
  test_registry.TestArchitectureRules + test_sdk` → 62 test OK.
- Fast tier (`TMPDIR` sang đĩa lớn, xem R11): `Ran 1345 tests ... FAILED
  (skipped=1, lỗi=0, fail=1)` — fail duy nhất là R5 (test cũ kỹ, xem §4.1).
- CLI thật qua subprocess: `diagnose inventory sample.ipa` (3 entry, đúng
  tên/kích thước/method), `diagnose hex ... --length 32`, `diagnose strings
  en.json` (đúng chuỗi, `--keyword` lọc đúng) — rc 0.
- Menu thật: [5] hiện "đã nối dịch vụ (services.diagnostics_tool)", [5][3]
  chạy picker + inventory thật; hủy/EOF thoát sạch, không crash.
- Full tier (`TMPDIR` sang đĩa lớn, chạy trước chỉ thị điều phối "đừng chạy
  full nữa"): `Ran 1508 tests in 225.4s FAILED (skipped=1, lỗi=0, fail=2)`,
  Timeout 0. Tổng 1508 (không phải 1419) vì các nhóm song song đã thêm test
  trong lúc chạy (vd `tests/test_compress.py`). 2 fail:
  - R5 (test menu "CHƯA TRIỂN KHAI" cũ kỹ) — coordinator cập nhật ở bước
    tích hợp (điều phối đã xác nhận).
  - `tests.test_runner.TestPlanCounts.test_plan_counts_match_real_load`
    (`1536 != 1534`): fail THOÁNG QUA do nhóm khác sửa file test giữa lúc
    chạy full (số đếm tĩnh lúc đầu ≠ số nạp thật lúc sau); chạy riêng ngay
    sau đó đã xanh (`1536 == 1536`). Không phải lỗi code của nhóm này.
- Mô phỏng gộp iSH (pty + `COLUMNS=40` + `PB3_TEST_TIMEOUT_SCALE=5`):
  KHÔNG chạy ở nhóm này nữa theo chỉ thị điều phối — coordinator chạy full
  suite + mô phỏng iSH ở bước tích hợp.

## 4. Việc để lại cho coordinator / nhóm khác

### 4.1 Test cũ kỹ cần cập nhật (R5)
`tests/test_app_menu_cli.py:164-175`
`test_unimplemented_shows_clear_status_and_changes_nothing` — tiền đề "mục 5
> [3] ... (services.diagnostics_tool chưa có)" nay sai. Đề xuất sửa theo
tiền lệ #174 (khi batch triển khai, test "chưa triển khai" đã chuyển sang
[5][3]): chuyển khẳng định "CHƯA TRIỂN KHAI/không giả lập" sang Feature giả
`services.khong_co_that` qua `run_feature` trực tiếp (mẫu sẵn ở
`test_module_missing_vs_module_broken_distinguished`), vì nay không còn mục
menu nào chưa triển khai. Nhóm này KHÔNG sửa chéo file ngoài phạm vi.

### 4.2 Maturity menu diagnose (R6)
`ui/handlers.py` MENU[4]: cập nhật Feature diagnose lên maturity M2 +
`note_vi` (inventory/hex/strings/encoding, chỉ đọc) khi hợp nhất.

### 4.3 Tài liệu (R7)
`README.txt:109` và `CURRENT_STATE.md` (dòng Claude 10): đổi "[3]
Inventory/hex/strings chưa triển khai" thành đã triển khai (F6) + cập nhật
nợ #183/#184 (F2 sửa một nửa, F5 xong). `handoff/decisions.md`: đề xuất
thêm các quyết định dưới đây (coordinator đánh số cuối).

### 4.4 Môi trường chung (R11)
`/tmp` tmpfs 512M đang đầy 73% (file bench/IPA 82MB × 2 + nhiều thư mục
`tạm pb3-extract-*` + `pb3test-*` của các nhóm song song). Nhắc các nhóm dọn
file bench/IPA trong `/tmp` sau khi xong việc; không xóa bừa khi họ còn chạy.

### 4.5 Tiêm registry cho batch (R4)
`services/batch_tool.py:92,167`: dựng registry (qua
`services.language_tool._registry(app)`) rồi truyền `registry=` vào
`BatchRunner(...)`; sau đó `_get_registry` trong `sdk/batch.py` mới bỏ được
import lười `services` cuối cùng mà không vỡ batch extract thật.

## 5. Đề xuất quyết định (coordinator đánh số cuối, tiếp sau #186)

- **#187 — F6: menu [5][3] Inventory/hex/strings triển khai thật.**
  `services/diagnostics_tool.py` mới (inventory qua `open_vfs`, hex 8
  byte/dòng phân trang, strings quét theo khối có offset + lọc từ khóa,
  đoán encoding BOM/UTF-8/UTF-16); CLI `diagnose inventory|hex|strings
  <file>`; chỉ đọc, không ghi gì; 24 test khóa trong
  `tests/test_final_review.py`.
- **#188 — F5: `core/file_dialog.py` đã nối.** Picker `choose_path` dùng
  trong `diagnostics_tool.run_interactive` và `ui/screens.py::
  _diagnose_investigate` (menu [5][3]); hủy/EOF thoát sạch.
- **#189 — F2 (mod): adapter Unity mức sdk trong `sdk/batch.py`.**
  4 helper chuyển xuống sdk, thông điệp lỗi CLI giữ nguyên từng chữ (test
  khóa so từng trường); trùng lặp có chủ ý với `services/unity_tool.py`
  (bước dọn đúng: chuyển sang `sdk/unity.py` rồi để `unity_tool` delegate).
- **#190 — F2 (registry): `_get_registry` giữ import lười `services`.**
  Lý do: sdk không được import registry (test kiến trúc); bỏ fallback thì
  batch extract thật lỗi; sửa đúng là tiêm registry từ
  `services/batch_tool.py` (xem §4.5).
- **#191 — Heuristic UTF-16: chỉ tính ký tự ASCII in được + chẵn/lẻ NUL.**
  Heuristic cũ đoán nhầm dữ liệu nhị phân thành UTF-16LE (ký tự CJK giải
  mã sai vẫn `isprintable()`); test khóa bắt được.
- **#192 — Strings: `total` khi dừng sớm chỉ là số đếm tối thiểu.**
  Hiển thị "ít nhất N chuỗi (dừng sớm vì đủ giới hạn...)", không báo như
  tổng toàn file.
- **#193 — Test cũ kỹ R5 cần cập nhật ở bước tích hợp.**
  `test_unimplemented_shows_clear_status_and_changes_nothing` sai tiền đề
  sau F6 (fail duy nhất còn lại); sửa theo §4.1, không phải lỗi sản phẩm.

## 6. Giới hạn trung thực

- F2 chỉ sửa một nửa (registry còn lại, xem §1.3 + §4.5); trùng lặp helper
  Unity giữa `sdk/batch.py` và `services/unity_tool.py` cần bước dọn sau.
- Đoán encoding là sơ bộ (heuristic): UTF-16 không BOM chủ yếu phát hiện
  được với văn bản Latin; văn bản CJK UTF-16 không BOM có thể đoán thành
  "nhị phân/không rõ" — báo rõ "chỉ là phỏng đoán" trong output.
- Strings UTF-16 chỉ quét file ≤ 8MB; lớn hơn báo rõ giới hạn (không quét
  nửa vời).
- `diagnose inventory` in đủ tên entry (theo tiền lệ `container list`) nên
  dòng có thể dài hơn 44 cột với đường dẫn sâu — tên/kích thước/method
  không bị cắt dở.
- Chưa chạy trên iSH thật (chờ Joseph; tiêu chí 1 của prompt 10 vẫn NOT RUN).
