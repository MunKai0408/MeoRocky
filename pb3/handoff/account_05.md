# TÀI KHOẢN CLAUDE 05 — UNITY BACKEND

Trạng thái: **COMPLETE (2026-11-15)** — sandbox 643/643 OK + iSH PASS (suite
20:50 + Unity CLI thật 20:52/20:56). Tiến độ 5/10 gói (01, 02b, 03, 04, 05).
Phạm vi: prompt 05 trong `PB3_v3.1_10_Claude_Prompts.txt`, đặc tả §6.1–6.8 và mục
"CLAUDE 05 - UNITY BACKEND". Gói 04 đã COMPLETE (bằng chứng iSH trong
`handoff/account_04.md`). Không làm 06–10.

## 1. Đã làm (sandbox, 2026-11-15)

### Detector + UnityFS reader — `sdk/unity.py` (mới, ~1700 dòng)
- `detect_bundle(data)`: magic `UnityFS` + version (u32 big-endian ở offset 8)
  + `unity_version`/`unity_revision` (chuỗi NUL). Trả `None` nếu không phải bundle.
- `parse_bundle(data, backend, limits)`: UnityFS **v6/v7**; header, blocks info
  (cả trường hợp blocks-info-at-end, flag 0x80), căn 16 (v7), cờ padding,
  flag mã hóa -> E_ENCRYPTED, compression 0 none / 1 LZMA / 2 LZ4 / 3 LZ4HC
  (4 LZHAM -> E_UNSUPPORTED_FORMAT). Giải nén từng block qua backend, kiểm
  đúng kích thước giải nén từng block; `total_size` lệch chỉ cảnh báo.
- `build_bundle(entries, ...)`: dựng lại bundle giữ đúng layout UnityPy ghi
  (header big-endian, căn 16, blocks info hash 16 byte 0 + block + entry,
  block LZ4 chia 128 KiB, giữ compression gốc khi rebuild; LZ4 ghi bằng
  binding khi có, không thì encoder thuần literal-only).
- `detect_engine(names)`: nhận diện từ danh sách tên entry (đặc tả 6.2):
  tín hiệu mạnh `globalgamemanagers`, `resources.assets`, `.unity3d`,
  `Data/Managed`, `UnityFramework`, `libmono`, `il2cpp`; yếu `sharedassets`,
  `StreamingAssets`, `.assets`, `.resS`, `Data/level`. >=2 mạnh -> CHẮC CHẮN;
  1 mạnh hoặc >=2 yếu -> CÓ KHẢ NĂNG; còn lại -> CHƯA ĐỦ BẰNG CHỨNG.
- `parse_unity_version` / `_uses_new_flags`: cờ mới từ Unity 2020.3.34 /
  2021.3.2 / 2022.1.1 (theo UnityPy).

### SerializedFile adapter — cùng file
- `parse_serialized(data, limits)`: SerializedFile **v15–22**. 4 số đầu header
  LUÔN big-endian, phần sau theo cờ endian; v>=22 có extended header (u64);
  metadata: unity_version, target_platform, enable_type_tree, types (kèm
  typetree blob, `TypeTreeNode.parse_blob` theo UnityPy), object table
  (path_id align + i64 v>=14; byte_start u64 chỉ v>=22, còn lại u32;
  script_type_index 11<=v<17; is_stripped v15/16), script types, externals.
- `SerializedFile.text_assets()` / `find_text_asset()` / `object_bytes()`:
  TextAsset = aligned_string(m_Name) + aligned_string(m_Script); chuỗi ngắn
  và dài đều đọc đúng (fixture có TextAsset 5437 ký tự).
- `build_serialized(sf, object_data)`: dựng lại toàn bộ SerializedFile từ
  model đã parse + dữ liệu object mới (typetree blob giữ nguyên byte gốc).
  Dùng cho REBUILD_ALL (đang bị khóa ở M2, xem dưới).

### LZ4 thuần Python — `sdk/lz4pure.py` (mới)
- `decompress_block` (token nibble, match copy, kiểm kích thước; chấp nhận
  `uncompressed_size=None` cho block độc lập trong frame, giới hạn `max_size`),
  `compress_block_literals` (encoder literal-only: hợp lệ nhưng không nén,
  ghi rõ trong log quyết định), `decompress_frame` + `is_lz4_frame`,
  `xxhash32` thuần (đúng vector chuẩn: `""` -> 0x02CC5D05).
- LZMA Unity: LZMA1 thô qua `lzma` chuẩn (props 0x5D, dict 0x800000, đầu 5 byte).

### Hybrid backend — `UnityBackend` (AUTO / LIBRARY / NATIVE) + `decisions`
- AUTO: UnityPy + lz4 binding khi có; fallback Python thuần, mọi lựa chọn ghi
  vào `decisions` (in ra khi patch/inspect).
- LIBRARY: bắt buộc UnityPy + lz4 binding; thiếu -> **E_UNSUPPORTED_ON_ISH**
  ngay lúc khởi tạo. NATIVE: chỉ Python thuần, không gọi thư viện ngoài;
  `unitypy_load` ở NATIVE -> E_UNSUPPORTED_ON_ISH.
- **Không có câu `import UnityPy` / `import lz4` nào trong mã** (quy tắc iSH:
  import nặng không được xuất hiện, kể cả trong hàm — test
  `test_no_heavy_import_at_startup` bắt được; xem decisions #73). Mọi chỗ
  cần binding đều qua `importlib.import_module` sau khi `probe_backends()`
  xác nhận có mặt. `probe_backends()` chỉ báo cáo, không raise.
- `MATURITY = "M2"`.

### Planner — `plan_bytes` / `UnityPlan`
- Bản đồ container -> entry -> object: `(container, entry, kind, object_type,
  path_id, offset, size, guess, name)`. Đi qua container ZIP (qua
  `sdk/containers.py::open_vfs`) và bundle lồng `'::'` (Claude 04).
- `guess`: TextAsset -> `text` (chuỗi dài >1000 -> `text (chuỗi dài)`),
  `.json` -> `json`, `.csv/.tsv` -> `table`, `.txt/.plist/...` -> `text`,
  còn lại `binary`. TextAsset và JSON/CSV trong StreamingAssets được ưu tiên
  (hàng đầu trong plan).

### Patcher + diffverify — `apply_patch` (đặc tả 6.5–6.7)
- `PATCH_INPLACE` (mặc định): cùng độ dài -> thay trực tiếp; ngắn hơn -> đệm
  NUL trong script, giữ nguyên độ dài object (ghi vào `report.warnings`);
  dài hơn -> chuyển `REBUILD_SECTION` nếu `allow_escalate` (mặc định bật),
  không thì từ chối **E_WRITE_DISABLED** (không cắt cụt im lặng).
- `REBUILD_SECTION`: dựng lại section/bảng có offset (đường phẫu thuật giữ
  nguyên metadata ngoài object table, cập nhật mọi offset/size phụ thuộc);
  object không liên quan giữ nguyên hash (kiểm trong verify).
- `REBUILD_ALL`: **bị khóa** — cần maturity M4 (backend đang M2) + dry-run +
  diff + xác nhận người dùng; hiện tại từ chối E_WRITE_DISABLED dù có
  `--confirm` (decisions #69).
- Đơn vị đầu ra nhỏ nhất (6.6): assets độc lập -> chính file đó; trong bundle
  -> chỉ bundle đó; bundle trong IPA -> chỉ bundle, **KHÔNG đóng gói lại IPA**;
  không ghi đè nguồn; output versioned (`.v1`/`.v2`... qua
  `safe_io.write_output_bytes`, policy `version`).
- diffverify (6.7): hash entry/object trước khi sửa; sau khi ghi parse lại
  output và so sánh — tập entry thay đổi ĐÚNG BẰNG tập dự kiến (allow-list),
  object không liên quan giữ nguyên hash, thứ tự/alignment/endian/block
  info/offset/size hợp lệ, dữ liệu đã sửa đọc lại khớp giá trị mong muốn.
  Không chứng minh được -> **KHÔNG ghi file cuối**, giữ bản ứng viên trong
  temp (`patch-failed.bin`, khi có `temp_dir`) và báo E_VERIFY_FAILED.
- `write_patch_output` / `backup_source_file` / `rollback_output` (xóa file
  output vừa ghi).

### CLI — `services/unity_tool.py` (mới), lệnh `unity` + `mod`
- `unity inspect <file>`: in verdict + plan (không lỗi với file lạ, chỉ báo
  CHƯA ĐỦ BẰNG CHỨNG). `unity read <file> [entry] --out <dest>`: đọc entry
  (kể cả `'::'` lồng); `--object <path_id>` với TextAsset thì in script ra
  stdout (không cần `--out`), object khác cần `--out`.
- `unity patch <file> [entry] --object <id> --old <s> --new <s>
  [--mode PATCH_INPLACE|REBUILD_SECTION|REBUILD_ALL] [--confirm]`: chạy
  patch + diffverify; **mặc định chỉ dry-run** (in báo cáo, KHÔNG ghi);
  thêm `--allow-write` mới ghi thật. Output mặc định
  `workspace/output/<GameName>/<tên đơn vị nhỏ nhất>` (vd `nested/level.unity3d`
  cho bundle trong IPA, không bao giờ là `.ipa`).
- `mod <file> [--file <entry>] --object <id> --old <s> --new <s>
  [--allow-write]`: không có `--old/--new` thì chỉ in plan; có thì dry-run
  rồi ghi khi `--allow-write`. Menu mục 1 đã nối dịch vụ thật (maturity M2);
  `run_interactive` hỏi đường dẫn -> plan -> patch từng bước, xác nhận trước
  khi ghi. Nối dây qua `ui/handlers.py` (COMMANDS `unity`, `mod` M2) +
  `ui/cli.py` (subparser `unity`; `--allow-write` định nghĩa ở `cli.py` cho
  cả hai lệnh vì `build_parser()` không `only` phải parse được nó).
- 2 test cũ cập nhật theo thực tế mới (không hạ tiêu chuẩn): menu mục 1 đã
  triển khai nên test "chưa triển khai" giờ kiểm mục 2,4; test phân biệt
  module thiếu/hỏng giờ dùng mục 2 (`language_tool`, Claude 07) thay vì mục 1.

### Fixture — `tests/fixtures/unity/` (tự tạo, độc lập với UnityPy)
- Builder `build_fixtures.py` (stdlib + `lz4` cho fixture LZ4; UnityPy chỉ
  là bên xác minh độc lập, không dùng để xây fixture).
- `resources.assets` (6763B, SF v17, 3 object: TextAsset `greeting`
  "Xin chao 9 Kings!" path_id 1001, TextAsset `longtext` 5437 ký tự path_id
  1002, MonoBehaviour mờ path_id 1003), `sharedassets0.assets` (176B,
  TextAsset `config` JSON path_id 2001), `sample_typetree.assets` (260B, SF
  v17 có typetree blob), `sample_v21.assets` (152B, SF v21),
  `sample.unity3d` (7151B, UnityFS v7 không nén), `sample_lz4.unity3d`
  (592B, LZ4, blocks-info-at-end), `sample_lzma.unity3d` (491B, LZMA),
  `sample_v6.unity3d` (7135B, UnityFS v6, Unity 5.6.7f1),
  `nested.ipa` (969B, ZIP chứa `Payload/Game.app/Data/level.unity3d`).
- **UnityPy 1.25.4 (venv kiểm chứng `/tmp/upvenv`, không thuộc PB3) đọc đúng
  mọi fixture**: 4 object/bundle (kể cả chuỗi ngắn/dài/JSON), typetree + v21
  đúng. Đây là bằng chứng định dạng độc lập cho Claude 05.

### Test mới — 68 test (`tests/test_unity.py`)
Detector (7), LZ4 thuần (6), bundle parse (8), SerializedFile parse (6),
backend modes (8), planner (4), patcher (15), patch IO (3), đăng ký lệnh (1),
CLI thật qua tiến trình con trên bản sao dự án (8: inspect bundle/IPA/file
lạ, read TextAsset/entry, patch dry-run rồi ghi, patch IPA chỉ xuất bundle,
mod dry-run rồi ghi). Tổng suite: **575 -> 643 test**.

## 2. Định dạng: đọc / ghi / không hỗ trợ

| Định dạng | Đọc | Ghi | Backend | Maturity |
|---|---|---|---|---|
| UnityFS v6/v7 (bundle) | có | có (dựng lại bundle, giữ compression gốc) | Python thuần + lz4/lzma | M2 |
| SerializedFile v15–22 (.assets) | có (metadata, object table, TextAsset) | có (patch TextAsset: PATCH_INPLACE / REBUILD_SECTION) | Python thuần | M2 |
| LZ4 block / LZ4HC | có | có | binding lz4 (ưu tiên) / Python thuần (literal-only khi ghi) | M2 |
| LZ4 frame | có | chỉ qua binding lz4 | binding lz4.frame (ưu tiên) / Python thuần (chỉ đọc) | M2 |
| LZMA (Unity raw) | có | có | `lzma` (thư viện chuẩn) | M2 |
| UnityPy (object phức tạp) | khi cài được | khi cài được | UnityPy 1.25.4 (iSH đã chứng minh) | M2 |
| SerializedFile v<15 / v>22, UnityWeb/UnityRaw, LZHAM, bundle mã hóa | không | không | chưa có (E_UNSUPPORTED_FORMAT / E_ENCRYPTED) | M0 |

Chỉ tuyên bố hỗ trợ loại/version đã có fixture + test. Không tuyên bố hỗ trợ
mọi phiên bản Unity. `support_matrix()` trong `sdk/unity.py` trả đúng bảng này.

## 3. Bằng chứng sandbox (Python 3.12.3, 2026-11-15)

- `python3 run_tests.py --tier full`: **`Ran 643 tests in 11.4s OK
  (skipped=0, lỗi=0, fail=0)`** (575 cũ + 68 mới). `--plan`: fast 584, ish 11,
  slow 31, meta 17, smoke 57.
- Môi trường sandbox: `python3 -c "import UnityPy, lz4"` -> `ModuleNotFoundError:
  No module named 'UnityPy'` (UnityPy KHÔNG có trong Python hệ thống của
  sandbox); `lz4` 4.0.2+dfsg có. Vì vậy trong sandbox, AUTO fallback sang
  NATIVE đối với UnityPy (parser thuần), LZ4 dùng binding; LIBRARY báo
  E_UNSUPPORTED_ON_ISH (đúng thiết kế, có test khóa). **Đây là chuyện riêng
  của sandbox; trên iSH UnityPy 1.25.4 + lz4 ĐÃ chứng minh cài được ngày
  2026-11-15 18:21 — không cần bước cài đặt nào trên iSH.**
- UnityPy cross-check trên output ĐÃ patch (venv `/tmp/upvenv`, UnityPy
  1.25.4): `patched_resources.assets` đọc lại `greeting` =
  "Xin chao cac ban choi game 9 Kings!" (rebuild dài hơn), `longtext` giữ
  nguyên; `patched_longtext.assets` giữ `greeting`, `longtext` đã rebuild;
  `patched_lz4.unity3d` nạp được, 4 object, `longtext` bắt đầu
  "Dòng dài để thử chuỗi dài: HELLO " (đã patch trong bundle LZ4).
- Patcher: in-place cùng độ dài (byte-diff đúng 1 byte đổi), ngắn hơn đệm NUL
  (giữ nguyên độ dài object, đọc lại giữ 17 ký tự), dài hơn leo thang
  REBUILD_SECTION (verify OK, object không liên quan giữ nguyên hash),
  từ chối khi cấm leo thang / REBUILD_ALL / không tìm thấy chuỗi/path_id /
  object không phải TextAsset; verify lỗi giả lập -> E_VERIFY_FAILED + giữ
  `patch-failed.bin` trong temp; patch bundle LZ4 rồi patch lại trên output
  (round-trip) đều verify OK.
- CLI thật trên bản sao (sandbox, không phải iSH): `unity inspect`
  sample.unity3d/nested.ipa ra plan đúng; `unity read` TextAsset in script;
  `unity patch` dry-run không tạo file, `--allow-write` ghi
  `workspace/output/resources/resources.assets` và nguồn không đổi;
  `unity patch` trên `nested.ipa` chỉ xuất `nested/level.unity3d`, không có
  `.ipa` nào trong output; `mod` dry-run rồi ghi tương tự.
- Lỗi tìm được khi viết test và đã sửa trong mã (không sửa test cho qua):
  (1) builder fixture ghi 4 u32 đầu header SerializedFile bằng little-endian
  -> UnityPy tìm được 0 object; sửa sang big-endian, UnityPy đọc đúng;
  (2) `_rebuild_assets_section`: `meta_tail` nuốt cả đệm căn 16 tới
  `data_offset` (metadata_size sai -> file hỏng), rồi `meta_head` tính cả
  20 byte header (metadata_size đếm trùng); sửa: metadata chỉ tính từ sau
  header tới `header_size + metadata_size`;
  (3) `_snapshot_assets`: `meta_head` gồm cả header (chứa file_size sẽ đổi
  khi rebuild) -> verify sai; sửa: loại header khỏi so sánh;
  (4) câu `import UnityPy`/`import lz4` trong hàm vẫn vi phạm quy tắc iSH
  (test meta bắt được) -> dùng `importlib.import_module`;
  (5) `|=` trên set/dict là API 3.9, dự án yêu cầu tương thích 3.8 ->
  `.update()`; (6) `_default_output_rel` biến cả vpath thành đường dẫn
  output (sẽ ghi cây thư mục sâu thay vì chỉ bundle) -> dùng `label` đã
  resolve (tên bundle); (7) `unity patch` ban đầu ghi ngay không có dry-run
  -> thêm cổng `--allow-write`, mặc định dry-run; (8) `unity read` đòi
  `--out` cả khi đọc TextAsset -> in script ra stdout khi không có `--out`;
  (9) dòng tiêu đề interactive Unity dài 64 ký tự, vượt 48 ký tự iPhone
  (test menu bắt được) -> tách hai dòng.
- **BUG TREO SUITE TRÊN iSH — phát hiện khi Joseph chạy kiểm chứng
  (2026-11-15 20:22), đã sửa trong đợt này (decision #75):** full suite
  trên iSH treo ở test `[566/641] test_auto_falls_back_to_native_in_sandbox`
  (đã chạy 149s+ và tăng; 565 test trước chạy xong bình thường). Bộ chạy test
  chỉ in cảnh báo treo mỗi 5s sau 15s, KHÔNG tự cắt test nên phải dừng bằng
  Ctrl+C. Nguyên nhân: test gọi `UnityBackend("AUTO")` -> `probe_backends()`
  -> `_try_import("UnityPy")` -> `importlib.import_module("UnityPy")`, lần
  đầu tiên UnityPy được import trong tiến trình; trên iSH UnityPy 1.25.4
  ĐÃ cài nhưng import gói lớn này trên CPU/filesystem giả lập cực kỳ chậm
  (~1 phút theo test `test_no_heavy_import_at_startup`). Trong sandbox
  UnityPy không có nên ImportError xảy ra ngay và test chạy nhanh — bug
  chỉ lộ trên iSH. Cách sửa: `probe_backends()` chỉ kiểm tra sự hiện diện
  bằng `importlib.util.find_spec` + `importlib.metadata.version` (không
  thực thi module), cache kết quả theo tiến trình; import thật vẫn lazy,
  chỉ trong `unitypy_load`/`decompress` khi thật sự cần. Hai test giả
  định chỉ đúng trong sandbox đã sửa thành iSH-aware:
  `test_probe_backends_sandbox` (assert unitypy == "missing") ->
  `test_probe_backends_matches_environment` (probe phải khớp `find_spec`
  của môi trường thật); `test_auto_falls_back_to_native_in_sandbox` ->
  `test_auto_init_logs_unitypy_decision` (khởi tạo AUTO không import
  UnityPy). Test mới: `test_probe_does_not_import_unitypy_when_present`
  (mô phỏng iSH bằng mock find_spec/version: probe báo UnityPy 1.25.4
  có mặt, `UnityBackend("AUTO")` không đưa UnityPy vào `sys.modules`) và
  `test_probe_backends_cached` (probe lần 2 không quét lại sys.path).
- **Mô phỏng iSH bằng venv UnityPy 1.25.4 (`/tmp/upvenv`, dựng lại vì
  venv cũ đã mất):** `UnityBackend("AUTO")` khởi tạo trong **0.114s**,
  `UnityPy` không có trong `sys.modules` sau khởi tạo, probe báo đúng
  `unitypy 1.25.4 ok`, `lz4 4.4.5 ok`, decisions đúng. Chạy test Unity
  dưới venv: **`Ran 68 tests in 2.0s OK (skipped=1, lỗi=0, fail=0)`**
  (skip duy nhất: `test_library_raises_when_unitypy_missing`, đúng vì
  UnityPy có mặt); chạy đủ suite dưới venv: **`Ran 643 tests in 11.2s
  OK (skipped=1, lỗi=0, fail=0)`**. Lỗi thứ hai do mô phỏng này bắt được:
  `test_inspect_bundle`/`test_inspect_nested_ipa` assert `"TextAsset"` có
  trong output của `unity inspect`, nhưng chữ đó trước đây chỉ vô tình có
  mặt nhờ dòng backend khi UnityPy THIẾU; khi UnityPy có mặt thì mất ->
  sửa `_print_plan` in `object_type` của dòng assets (đúng đặc tả 6.4:
  bản đồ dữ liệu phải có type), cả hai test pass ở cả hai môi trường.
- Quyết định ghi ở `handoff/decisions.md` #66–75.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Chỉ hỗ trợ UnityFS v6/v7, SerializedFile v15–22, TextAsset cho patch.
  Object phức tạp (MonoBehaviour có dữ liệu, Mesh, Texture...) chỉ đọc được
  qua UnityPy khi cài được; parser thuần không hiểu nội dung của chúng (đọc
  raw, giữ nguyên hash khi patch object khác).
- Encoder LZ4 thuần là literal-only (không nén); bundle rebuild bằng NATIVE
  sẽ lớn hơn bản dùng binding (vẫn hợp lệ, UnityPy đọc được). Khi có binding
  lz4 (iSH có), AUTO dùng binding nên không gặp vấn đề này.
- REBUILD_ALL bị khóa ở M2 (cần M4 + dry-run + diff + xác nhận người dùng);
  chuỗi mới dài hơn chỉ leo thang tới REBUILD_SECTION.
- `detect_engine` chỉ gợi ý từ tên entry; file `.assets` lẻ không có tên
  gợi ý vẫn được planner liệt kê nhưng verdict có thể là CHƯA ĐỦ BẰNG CHỨNG
  (inspect không lỗi, chỉ báo).
- Bundle mã hóa -> E_ENCRYPTED; LZHAM -> E_UNSUPPORTED_FORMAT; 7z không liên
  quan (thuộc Claude 04, đã ghi unsupported).
- **UnityFS v8 CHƯA hỗ trợ (decision #76) — phát hiện trên IPA game thật:**
  game 9 Kings v1.0.5 (`workspace/input/game.ipa`) dùng bundle UnityFS v8;
  `unity inspect` trên IPA thật trả `E_UNSUPPORTED_FORMAT` ("UnityFS version
  8 chưa hỗ trợ (chỉ 6/7)") tại
  `workspace/input/game.ipa::Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle`
  (2026-11-15 20:56). Đây là từ chối sạch kèm đúng bundle + version, không
  phải crash; phạm vi C05 chỉ là UnityFS v6/v7 (decision #67). Hỗ trợ v8 là
  việc tương lai, ngoài phạm vi C05 — không tuyên bố hỗ trợ v8.
- Mã tương thích Python 3.9 (không dùng cú pháp 3.10+, không `tomllib`,
  không `|=` trên set/dict); đã kiểm trên Python 3.12.3 (sandbox) và chạy
  thật trên Python 3.9.16 (iSH, xem mục 5).

## 5. Kiểm chứng trên iSH — PASS (2026-11-15) — 05 COMPLETE

### 5.1. Suite trên iSH: PASS (20:50)

2026-11-15 20:50 +07, trong `/root/pb3`, `python3 run_tests.py --tier full`
(zip Claude 05 đã sửa probe, decision #75):

```text
Ran 643 tests in 719.4s OK (skipped=4, lỗi=0, fail=0)
```

639 passed, 4 skipped, 0 failed — đúng kỳ vọng mới (2 thiếu `tomllib`, 1 máy
có `/dev/location`, + 1 `test_library_raises_when_unitypy_missing` skip vì
UnityPy có mặt trên iSH). Chậm nhất: 114.7s
`TestImpact::test_indirect_dependency_is_followed`, 69.8s
`TestRealCli::test_disable_survives_new_process`, 35.8s
`TestCliEndToEnd::test_new_and_test_via_menu`, 31.5s
`TestContainerCli::test_container_errors`, 28.5s
`TestMenuSubprocess::test_cli_exit_codes`; 9 test vượt ngân sách 1.0s, tự
chuyển sang tầng chậm từ lần sau (theo thiết kế); đã lập mốc FULL xanh
(dùng cho `--changed`).

**Bản sửa probe chạy đúng trên iSH:** test từng treo
(`test_auto_falls_back_to_native_in_sandbox`, nay là
`test_auto_init_logs_unitypy_decision`) chỉ mất **1.1s** — trước đó treo
> 149s ở test 566/641 (2026-11-15 20:22, phải dừng bằng Ctrl+C; xem §3,
decision #75). Hai test CLI `test_mod_dry_run_and_write` (16s/21s) và
`test_patch_dry_run_then_write` (19s) bị cảnh báo treo nhưng chạy xong.

### 5.2. Unity CLI thật trên iSH (fixture): PASS (20:52)

Trong `/root/pb3`:

- `python3 menu.py unity inspect tests/fixtures/unity/sample.unity3d`:
  `engine: unity (CHẮC CHẮN)`, lý do `UnityFS v7 (2019.4.40f1)`, backend
  `AUTO: LZ4 dùng binding lz4 4.4.5`, `AUTO: UnityPy 1.25.4 có mặt; dùng cho
  object phức tạp`, `bản đồ dữ liệu: 6 dòng` (bundle 6763 byte
  `CAB-0123456789abcdef0123456789abcdef`, assets path_id=1001 36 byte text
  `greeting` TextAsset, path_id=1002 6468 byte text (chuỗi dài) `longtext`
  TextAsset, path_id=1003 59 byte binary MonoBehaviour, bundle 176 byte
  `CAB-0123456789abcdef0123456789abcde0`, assets path_id=2001 64 byte text
  `config` TextAsset). `100% cli:unity xong 1s`.
- `python3 menu.py unity inspect tests/fixtures/unity/nested.ipa`:
  `engine: unity (CÓ KHẢ NĂNG)`, lý do `có '.unity3d'
  (Payload/Game.app/Data/level.unity3d)`, cùng 2 dòng backend AUTO,
  `bản đồ dữ liệu: 8 dòng` (Info.plist file 95 byte, bundle 6763 byte,
  assets 1001 `greeting` / 1002 `longtext` / 1003 MonoBehaviour, bundle
  176 byte, assets 2001 `config`, `config.txt` file 8 byte). `xong 3s`.
- `python3 menu.py unity read tests/fixtures/unity/resources.assets
  --object 1001` in ra: `Xin chao 9 Kings!` (`xong 1s`).
- `python3 menu.py unity patch tests/fixtures/unity/resources.assets
  --object 1001 --old "9 Kings" --new "8 Kings"` (dry-run): `patch OK
  (assets, chế độ PATCH_INPLACE, backend AUTO)`, đích `assets
  path_id=1001 (greeting)`, `chuỗi: 17 -> 17 byte (tìm thấy 1 lần, thay
  lần đầu)`, `kích thước đơn vị: 6763 -> 6763 byte`, backend AUTO (lz4
  4.4.5 + UnityPy 1.25.4), `verify: verify OK: 1 object đổi đúng dự kiến,
  còn lại giữ nguyên hash`, `KHÔNG ghi file: đây là dry-run. Thêm
  --allow-write để ghi thật.` (`xong 1s`).
- Cùng lệnh với `--allow-write`: patch OK như trên + `đã ghi output:
  /root/pb3/workspace/output/resources/resources.assets (không ghi đè
  nguồn)` (`xong 1s`).

### 5.3. Patch lồng trên `nested.ipa`: PASS (20:56)

```sh
python3 menu.py unity patch tests/fixtures/unity/nested.ipa "Payload/Game.app/Data/level.unity3d::CAB-0123456789abcdef0123456789abcdef" --object 1001 --old "9 Kings" --new "8 Kings" --allow-write
```

`patch OK (bundle, chế độ PATCH_INPLACE, backend AUTO)`, đích
`bundle::CAB-0123456789abcdef0123456789abcdef path_id=1001 (greeting)`,
`chuỗi: 17 -> 17 byte (tìm thấy 1 lần, thay lần đầu)`, `kích thước đơn vị:
7151 -> 7151 byte`, backend AUTO (lz4 4.4.5 + UnityPy 1.25.4), `verify:
verify OK: 1 object đổi đúng dự kiến, còn lại giữ nguyên hash`,
`đã ghi output: /root/pb3/workspace/output/nested/level.unity3d (không ghi
đè nguồn)` (`xong 1s`). Chỉ xuất bundle, không có file `.ipa` nào trong
output, nguồn không đổi — đúng quy tắc đơn vị nhỏ nhất (đặc tả 6.6).

### 5.4. Inspect IPA thật: GIỚI HẠN TRUNG THỰC — UnityFS v8 chưa hỗ trợ (20:56)

```sh
python3 menu.py unity inspect workspace/input/game.ipa
```

```text
[E_UNSUPPORTED_FORMAT] Đã nhận diện nhưng chưa có plugin parse được.
Chi tiết: UnityFS version 8 chưa hỗ trợ (chỉ 6/7)
Tại: workspace/input/game.ipa::Payload/9Kings.app/Data/Raw/aa/iOS/localization-locales_assets_all.bundle
Gợi ý: Cần plugin mới cho định dạng này.
```

Game 9 Kings v1.0.5 thật dùng bundle UnityFS v8; phạm vi C05 chỉ là
UnityFS v6/v7 (decision #67). Đây là **từ chối sạch kèm đúng bundle +
version, không phải crash**; hỗ trợ v8 là việc tương lai, ngoài phạm vi
C05 (decision #76). IPA thật chỉ inspect, không patch trong bước này.

## 6. Việc cho Claude 06 (chưa làm)
- Dùng `sdk/unity.py` (`plan_bytes`, `apply_patch`, `UnityBackend`) làm
  backend cho format plugin Unity (06); REBUILD_ALL chỉ mở khi có bằng
  chứng M4; object phức tạp ngoài TextAsset cần UnityPy (đã có trên iSH).
- Hỗ trợ UnityFS v8 (decision #76) đã làm ở Claude 05b
  (`handoff/account_05b.md`): UnityFS v8 + SerializedFile v23 (Unity 6),
  sandbox DONE 2026-11-15 (665/665 OK, UnityPy đọc đúng fixture/output),
  iSH NOT RUN — kiểm chứng iSH theo `handoff/account_05b.md` mục 5.
- Không đóng gói lại IPA.
