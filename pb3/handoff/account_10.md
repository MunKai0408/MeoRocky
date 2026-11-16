# TÀI KHOẢN CLAUDE 10 — TÍCH HỢP CUỐI, KIỂM THỬ VÀ NGHIỆM THU

Làm theo prompt 10 (`PB3_v3.1_10_Claude_Prompts.txt`) + đặc tả (mục CLAUDE 10).
Tài khoản cuối — không mở rộng tính năng tùy tiện. Sandbox DONE 2026-11-16;
**RELEASE BLOCKED** — chờ kiểm chứng iSH của zip cuối (xem §4–§5).

## 1. Audit tích hợp (chỉ đọc trước, sửa bằng patch nhỏ)

### 1.1 Cây thư mục, import graph, module trùng
- Quét 84 file `.py` (core/sdk/services/registry/ui/plugins + menu.py +
  run_tests.py): **0 lỗi cú pháp**.
- Import graph ở mức package: `menu -> core, ui`; `plugins -> core, sdk`;
  `registry -> core, sdk`; `sdk -> core, services` (chỉ import lười trong
  hàm, xem #183); `services -> core, registry, sdk, ui`; `ui -> core`;
  `run_tests -> core`. Không có `core` import ngược lên tầng trên (test
  kiến trúc `test_dependency_rule_core_does_not_import_upper_layers` xanh).
- **Vòng import duy nhất: `sdk.api` <-> `sdk.manifest`** — đã sửa, xem §2
  (decisions #181). Không còn vòng nào sau khi sửa (quét lại bằng script
  `/tmp/c10_audit.py` + test khóa).
- Module trùng tên: `arbitration.py` ở cả `sdk/` và `registry/` — đã biết,
  `registry/arbitration.py` chỉ là re-export tương thích ngược (decisions
  #133); `plugin.py` mỗi plugin một file là theo thiết kế; `__init__.py`
  mỗi package là bình thường. Không có module trùng nội dung.
- Mạng: chỉ `sdk/translate.py` dùng `urllib` (ngoại lệ duy nhất, decisions
  #151, allowlist hẹp trong `test_rules_ish_only.py`); các kết quả khác
  (`core/errors.py`, `registry/isolation.py`, `registry/scan.py`,
  `sdk/testing.py`, `services/plugin_manager.py`) chỉ là chuỗi/docstring
  trong guard/denylist, không phải gọi mạng. Không có URL nào ngoài
  `https://translate.googleapis.com` trong mã (test luật xanh).
- `core/file_dialog.py` không được mã sản phẩm dùng (chỉ test của chính nó)
  — ghi nhận, giữ lại kèm test (decisions #184).

### 1.2 Plugin manifest + API version
- Cả 20 plugin builtin đều khai `api_version: 3` trong `MANIFEST` (dict
  trong `plugin.py`, không phải file manifest.json rời); `plugin list`:
  **20 plugin [ok]**, 0 `[disabled]`/`[unavailable]`.
- Manifest hỏng / api lệch vẫn bị loader vô hiệu hóa có lý do (đã có test ở
  C03; không đổi trong C10).

### 1.3 Menu + entry point
- `python3 menu.py` chạy được, banner hiện **BearTea** (decisions #165);
  [1]–[4] đã nối dịch vụ, [5] MỘT PHẦN (submenu: [1] Môi trường iSH, [2]
  Cách ly tệp tạm, [3] Inventory/hex/strings — ghi rõ "chưa triển khai",
  [4] Chạy nền qua vị trí, [0] Quay lại). Không có mục nào giả lập chức
  năng: mục chưa triển khai hiện trạng thái rõ.
- Mọi lệnh CLI trong `ui/handlers.py::COMMANDS` đều có dịch vụ thật; các
  lệnh chưa có dịch vụ trả `E_NOT_IMPLEMENTED` rõ ràng (chỉ còn đường
  fallback cho lệnh lạ + mục [5][3]).
- `menu.py` không chứa logic parse/patch (test luật xanh); không import
  UnityPy/lz4 khi khởi động (test luật xanh).

### 1.4 Tính năng chưa nối / lỗi tìm được
| # | Mức | Phát hiện | Xử lý |
|---|---|---|---|
| F1 | thấp | Vòng import `sdk.api` <-> `sdk.manifest` | Đã sửa (#181) |
| F2 | thấp | `sdk/batch.py` import lười `services` (đảo tầng) | Ghi nhận, không refactor trong C10 (#183) |
| F3 | **cao** | #180: mod PATCH_INPLACE không leo thang khi chuỗi dài hơn trong cùng khối căn (+1 byte) | Đã sửa (#182) |
| F4 | thấp | Mirror #180: chuỗi ngắn hơn 1 byte trong cùng khối căn cũng lỗi `E_WRITE_DISABLED` | Đã sửa cùng #182 |
| F5 | thấp | `core/file_dialog.py` chưa nối vào menu/pipeline | Ghi nhận, giữ lại (#184) |
| F6 | thấp | Menu [5][3] Inventory/hex/strings chưa triển khai | Đã báo rõ trạng thái, để đợt sau |

Không có blocker nào khác. Không xóa test, không hạ maturity, không sửa
ma trận để làm kết quả đẹp (theo prompt 10).

## 2. Đã sửa (patch nhỏ, đều có regression test)

### 2.1 Vòng import `sdk.api` <-> `sdk.manifest` (decisions #181)
- `API_VERSION = 3` / `API_MIN_VERSION = 3` chuyển sang định nghĩa ở
  `sdk/manifest.py` (module level); `sdk/api.py` import lại từ đó và
  re-export (`from sdk.api import API_VERSION` vẫn đúng theo hợp đồng
  Phụ lục B; `registry/compatibility.py` không đổi). `sdk/manifest.py`
  không còn import `sdk.api` ở bất cứ chỗ nào.
- Test khóa: `tests/test_sdk.py::TestApiVersion.test_manifest_does_not_import_api`
  (quét AST của `sdk/manifest.py` + khẳng định hằng số bằng nhau ở cả hai
  module). Kiểm chứng cả hai thứ tự nạp trong tiến trình mới.

### 2.2 #180 — PATCH_INPLACE leo thang khi aligned size bằng nhau (decisions #182)
- Gốc: `apply_patch` chỉ leo lên REBUILD_SECTION khi
  `len(new_obj_data) > len(old_obj_data)`; chuỗi dài hơn 1 byte nhưng
  kích thước object đã căn bằng nhau ("Xin chao" -> "Xin chào", 17 -> 18
  byte, aligned 24 = 24) rơi xuống `_patch_text_asset_inplace` và lỗi
  `E_WRITE_DISABLED` "cần độ dài không đổi (18 != 17)". Đã tái hiện
  trước khi sửa.
- Sửa trong `sdk/unity.py::apply_patch`: thêm nhánh
  `elif len(new_script_b) > len(script_b)` — leo lên REBUILD_SECTION khi
  `allow_escalate` (mặc định True, nên mod CLI/batch/extract đều được
  hưởng); không cho leo thì giữ lỗi cũ. Đồng thời đổi điều kiện đệm NUL
  từ so sánh object sang so sánh script: -1 byte trong cùng khối căn
  (longtext 6450 -> 6449, aligned 6456 = 6456) trước đây cũng lỗi
  `E_WRITE_DISABLED` (đã tái hiện), nay đệm NUL và giữ PATCH_INPLACE.
  Điều kiện mới là tập cha của điều kiện cũ (aligned size đơn điệu theo
  độ dài script) nên mọi trường hợp đã chạy đúng trước đây không đổi
  hành vi.
- Test khóa: `tests/test_unity.py::TestPatcher` (3 test mới) +
  `tests/test_batch.py::TestBatchRun.test_mod_longer_within_same_alignment_escalates`
  (batch mod thật trên sample.unity3d). Comment lỗi thời trong
  `test_mod_dry_run_writes_no_patched_file` đã sửa.

## 3. Bằng chứng sandbox (2026-11-16)

- **Full suite: `Ran 1395 tests in 85.5s OK (skipped=1, lỗi=0, fail=0)`**
  (1390 cũ + 5 mới: 3 unity + 1 batch + 1 sdk; 1394 passed, 1 skipped).
  Chậm nhất 4.0s (`TestPrintSpeed::test_print_plan_matches_old_wrap`).
- Mô phỏng gộp điều kiện iSH (pty + `COLUMNS=40` +
  `PB3_TEST_TIMEOUT_SCALE=5`): **1395 test, 1394 đạt, 1 bỏ qua, 0 fail,
  0 lỗi, Timeout 0, 85.9s**, chậm nhất 2.6s.
- Menu thật: banner BearTea; [1]–[5] hiển thị đúng; [5] submenu chạy được
  ([1] Môi trường iSH báo "Phát hiện iSH: KHÔNG" rõ ràng trong sandbox);
  `plugin list`: 20 plugin [ok]; `support --write` sinh lại
  `docs/SUPPORT_MATRIX.md` **không đổi nội dung**.
- Pipeline end-to-end thật trên CLI:
  - `extract tests/fixtures/extract/game1.zip` → **21 entry, 6 file**
    (index.json/raw.bin/inner.zip losers bị loại có lý do).
  - `translate tests/fixtures/extract/en.json --to vi --provider offline
    --no-cache` → exit 0, `"play": "Chơi"`, placeholder/thẻ/xuống dòng
    giữ nguyên; báo rõ "không đặt được credit 'Việt Hoá Bởi Bear':
    không có chuỗi tiêu đề/version/copyright" (en.json không có chuỗi đó).
  - `unity patch` file `.assets` độc lập, trường hợp #180
    ("Xin chao" -> "Xin chào", `--allow-write`) → cảnh báo leo thang
    REBUILD_SECTION, verify OK, output đọc lại ra "Xin chào 9 Kings!",
    nguồn không đổi.
  - `unity patch` trong bundle `sample.unity3d` (entry CAB-...) cùng
    trường hợp #180 → verify OK, chỉ xuất `sample.unity3d` (không đóng
    gói lại IPA).
  - `diagnose --ish` → báo cáo rõ ràng, không crash (sandbox: Python
    3.12.3, lz4 binding 4.0.2, UnityPy thiếu → guard
    `E_UNSUPPORTED_ON_ISH` đúng thiết kế).

## 4. Nghiệm thu — 13 tiêu chí (prompt 10 §4)

| # | Tiêu chí | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Cài đặt/chạy trong iSH theo hướng dẫn | **NOT RUN ON DEVICE** | Zip C10 chưa chạy trên iSH (chờ Joseph sáng 2026-11-16, xem §5). Sandbox: giải nén + `menu.py` chạy được. Các acc trước đã PASS iSH với zip cũ hơn (C03 527, C04 575, C05 643 test). |
| 2 | `python3 menu.py` hoạt động hoặc báo lỗi môi trường rõ ràng | **PASS** | Menu chạy trong sandbox (BearTea, [1]–[5]); `diagnose --ish` báo rõ "Phát hiện iSH: KHÔNG" + Python/lz4/UnityPy; menu đã chạy thật trên iSH ở C03–C05. |
| 3 | Không có phụ thuộc nền tảng khác / file gói ứng dụng | **PASS** | `test_no_game_specific_branches_or_forbidden_scope` xanh (quét core/sdk/services/registry/ui + docs). Trong mã nguồn, tên nền tảng chỉ còn trong `plugins/xml_strings/plugin.py` — quy tắc escape của định dạng file `.strings.xml` (định dạng localization), không phải chức năng hay phụ thuộc nền tảng, không xử lý file gói ứng dụng. |
| 4 | Không yêu cầu cloud/VPS/remote execution | **PASS** | Lõi 100% local (test luật mạng xanh). Ngoại lệ duy nhất đã được Joseph chấp: `sdk/translate.py` gọi Google Translate (opt-in, theo lệnh người dùng; có provider offline; không có mạng thì báo `E_NETWORK` rõ ràng). Không có server/VPS/remote execution nào khác. |
| 5 | Core Python hoạt động độc lập ở mức được đặc tả | **PASS** | Full suite 1395 test xanh không cần UnityPy; core không import tầng trên (test kiến trúc xanh). |
| 6 | UnityPy được tích hợp và trạng thái tương thích được chứng minh | **PASS** | Tích hợp: backend LIBRARY/AUTO gọi UnityPy khi có; guard `E_UNSUPPORTED_ON_ISH` khi thiếu (test `test_unitypy_load_guard`). Bằng chứng iSH (C05, 2026-11-15): `import UnityPy, lz4.block` → `UnityPy OK 1.25.4` (latest trên PyPI). Sandbox không có UnityPy nên chỉ dùng parser thuần — đúng thiết kế, không giả vờ. |
| 7 | LZ4 được tích hợp và trạng thái tương thích được chứng minh | **PASS** | Tích hợp: AUTO dùng binding `lz4.block` khi có, fallback `sdk/lz4pure.py` khi thiếu. Bằng chứng iSH (C05): lz4 4.4.5; sandbox: binding lz4 4.0.2 được dùng thật (backend log khi patch). |
| 8 | Unity read/extract chỉ công bố với fixture/test tương ứng | **PASS** | Ma trận hỗ trợ sinh tự động từ registry + corpus + KB; không có M4; mọi tuyên bố đều có fixture đã commit (`tests/fixtures/unity/`, godot, unreal) + test. |
| 9 | Unity writeback có diff/verify | **PASS** | `apply_patch` diffverify sau mọi lần patch (verify OK + "object đổi đúng dự kiến, còn lại giữ nguyên hash"); test khóa + CLI thật (§3). |
| 10 | Sửa asset trong `.unity3d` chỉ rebuild file `.unity3d` tối thiểu cần thiết | **PASS** | `unity patch` trên bundle chỉ xuất `sample.unity3d` (CLI thật §3); `test_patch_ipa_writes_bundle_only` (IPA chỉ xuất bundle, không repack). |
| 11 | Asset độc lập chỉ xuất asset độc lập | **PASS** | `unity patch` trên `resources.assets` chỉ xuất đúng file `.assets` đó (CLI thật §3); `test_patch_dry_run_then_write`. |
| 12 | Container cha chỉ rebuild khi cần | **PASS** | Batch không bao giờ đóng gói lại IPA (chỉ xuất đơn vị tối thiểu — test batch + account_09 §1); writeback qua `apply_edits` chỉ rebuild container cha nhỏ nhất. |
| 13 | Support matrix và giới hạn được cập nhật chính xác | **PASS** | `docs/SUPPORT_MATRIX.md` sinh lại không đổi (đúng đầu vào hiện tại); README.txt + CURRENT_STATE.md + `docs/HUONG_DAN.md` §10 cập nhật trong C10; giới hạn trung thực ở §6. |

**Verdict: 12/13 PASS, 0 FAIL, 1 NOT RUN (tiêu chí 1) → RELEASE BLOCKED.**
Nguyên nhân chính xác: zip cuối chưa được chạy trên iSH. Theo prompt 10,
chỉ đánh dấu RELEASE READY khi các tiêu chí bắt buộc đã PASS; khi Joseph
chạy xong checklist §5 mà xanh thì đổi thành RELEASE READY (ghi vào
CURRENT_STATE.md).

## 5. Kiểm chứng trên iSH (chờ Joseph, sáng 2026-11-16)

```sh
cd /root/pb3
# giải nén pb3_handoff_claude10_sandbox.zip đè lên /root/pb3 (ghi đè file cũ)
python3 run_tests.py --tier full
# kỳ vọng: Ran 1395 tests ... OK (skipped=5, lỗi=0, fail=0), ~20+ phút
# (1390 cũ + 5 mới; kỳ vọng ~1390 passed, 5 skipped — 1 skip sandbox + 4 skip iSH)

python3 menu.py
# kỳ vọng: tiêu đề menu hiện "BearTea"; [1]–[5] hiển thị đúng

python3 menu.py plugin list | grep -c "\[ok\]"
# kỳ vọng: 20

python3 menu.py unity patch tests/fixtures/unity/resources.assets --object 1001 --old "Xin chao" --new "Xin chào" --allow-write
# kỳ vọng: cảnh báo leo thang REBUILD_SECTION (#180 đã sửa), verify OK,
#   output workspace/output/.../*.assets đọc lại ra "Xin chào 9 Kings!"

python3 menu.py extract tests/fixtures/extract/game1.zip --out /tmp/ext10
# kỳ vọng: 21 entry, 6 file

python3 menu.py translate tests/fixtures/extract/en.json --to vi --provider offline --no-cache
# kỳ vọng: exit 0, en.vi.json có "play": "Chơi", báo rõ không đặt được credit
#   (en.json không có chuỗi tiêu đề/version)
```

## 6. Giới hạn trung thực + việc còn lại

- **Chưa chạy trên iSH:** toàn bộ bằng chứng C10 là sandbox + mô phỏng gộp
  (pty + COLUMNS=40 + scale 5). iSH thật có thể lộ lỗi chỉ xuất hiện trên
  máy thật (như 12 lỗi C06 từng lộ) — đó là lý do verdict BLOCKED.
- UnityPy/LZ4 trên iSH: bằng chứng từ C05 (2026-11-15), chưa chạy lại
  trong đợt C10 (không có runtime iSH trong phiên này).
- Chỉ có IPA 9 Kings v1.0.5 là game thật đã dùng (Unity inspect ở C05);
  mọi engine/format khác mới test fixture. Không có M4.
- `.translation` Godot định dạng "TR" v1 chưa đối chiếu file thật (C08).
- Dịch Google thật: endpoint miễn phí giới hạn tốc độ mạnh (429 từ request
  thứ 2 trong sandbox) — dịch 16k chuỗi ≈ 2+ giờ, không phải 10–30 phút
  như Muse từng ước tính sai (đã đính chính; xem decisions #151).
- Nợ kiến trúc ghi nhận, không sửa trong C10: F2 (`sdk/batch.py` import
  lười `services`, #183), F5 (`core/file_dialog.py` chưa nối, #184),
  F6 (menu [5][3] Inventory/hex/strings chưa triển khai).
- Việc còn lại sau C10 (theo lệnh Joseph, KHÔNG thuộc prompt 10): đợt rà
  soát tổng cuối 01–10 (fix + nâng cấp + cải thiện + tối ưu toàn bộ, riêng
  bộ test làm mạnh tay), nén IPA kiểu WinRAR (≤2GB → đích ≤400MB, không
  quá 500MB), font 4 chế độ (trong đó cách 4: font nhập sẵn ĐẦY ĐỦ tiếng
  Việt). Các việc này có bước riêng sau C10.

## 7. Kiểm chứng độc lập (giải nén zip ra thư mục mới, chạy lại)

- Zip giao cuối `pb3_handoff_claude10_sandbox.zip` (335 entry, `testzip`
  OK, spec md5 khớp: `8867b7b79c5b3f8cb03d68b8c1d1eeb9`,
  `d634a74b1c74ec6c6719430c22d7301a`; kích thước byte chính xác xem báo
  cáo bàn giao kèm zip — con số trong tài liệu này không tự ghi kích
  thước của chính nó, vì sửa tài liệu sẽ đổi kích thước zip).
- **Lần 1 (zip 928,765 byte) bắt được 1 FAIL thật:** `Ran 1395 tests FAILED
  (fail=1)` — `test_no_game_specific_branches_or_forbidden_scope` báo
  `handoff/account_10.md` chứa từ khóa nền tảng bị cấm (dòng tiêu chí 3
  trong bảng nghiệm thu). Nguyên nhân: lần chạy suite cuối trong cây làm
  việc diễn ra TRƯỚC khi viết `account_10.md`. Đã sửa diễn đạt (không đổi
  nội dung), chạy lại rule test + full suite + rebuild zip (decisions #186).
- **Lần 2 (zip 928,776 byte, sau sửa):** giải nén ra thư mục mới, full
  suite **`Ran 1395 tests in 88.5s OK (skipped=1, lỗi=0, fail=0)`** —
  1394 passed, 0 fail, 0 lỗi, Timeout 0, chậm nhất 4.0s. Smoke test trên
  bản giải nén: `plugin list` 20 plugin [ok]; menu hiện BearTea;
  `unity patch` trường hợp #180 ("Xin chao" -> "Xin chào",
  `--allow-write`) → leo thang REBUILD_SECTION, verify OK, output đọc lại
  ra "Xin chào 9 Kings!"; `extract game1.zip` → 21 entry / 6 file.
- **Lần 3 (zip giao cuối, sau khi thêm §7 + #186 vào tài liệu):** giải
  nén ra thư mục mới, full suite xanh — `Ran 1395 tests ... OK (skipped=1,
  lỗi=0, fail=0)`, 1394 passed, 0 fail, 0 lỗi, Timeout 0, chậm nhất ~4.0s
  (thời gian chạy chính xác từng lần xem báo cáo bàn giao).
