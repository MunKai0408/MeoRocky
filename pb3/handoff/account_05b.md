# TÀI KHOẢN CLAUDE 05b — UNITY PHIÊN BẢN CAO NHẤT (UnityFS v8 + Unity 6)

Trạng thái: **SANDBOX DONE (2026-11-15)** — sandbox 665/665 OK + UnityPy
1.25.4 đọc đúng mọi fixture + output đã patch; iSH NOT RUN. Tiến độ 10 gói
không đổi (05b là phần bổ sung ngoài prompt, như mục 4 trong `PROGRESS.md`).
Phạm vi: nâng backend Unity của Claude 05 lên mức cao nhất mà UnityPy 1.25.4
hỗ trợ (quyết định của Joseph 2026-11-15 21:00, sau khi `unity inspect` IPA
9 Kings thật trả E_UNSUPPORTED_FORMAT vì UnityFS v8 — decision #76).
Không làm 06–10.

## 1. Đã làm (sandbox, 2026-11-15)

### UnityFS v8 — `sdk/unity.py`
- `SUPPORTED_BUNDLE_VERSIONS = (6, 7, 8)`; `parse_bundle` đọc v8 qua cùng
  đường v7 (UnityPy `BundleFile.read_fs` không kiểm version; v8 chỉ khác ở
  căn 16 sau header — đã có từ v7 — và cờ theo unityRevision).
- `build_bundle(entries, ...)`: thêm `unity_revision=None` (mặc định =
  `unity_version`; bundle Unity 6 thật ghi player "5.x.x" + revision
  "6000.0.23f1"), `extra_data_flags=0` (OR thêm vào data_flag, để test cờ
  mã hóa/padding), `pad_block_data=False` (đặt cờ 0x200 + căn đầu vùng
  block data tới 16).
- `apply_patch` khi dựng lại bundle giữ nguyên `unity_revision` của bundle
  gốc (không thì bundle Unity 6 bị mất revision, thành "5.x.x"); plan
  verdict in revision ("UnityFS v8 (6000.0.23f1)").

### Sửa gating cờ theo unityRevision — decision #78 (bug tiềm ẩn)
- `_uses_new_flags`, căn 16 sau header, `BundleFile.uses_padding` trước đây
  dùng `unity_version` (player, thường là "5.x.x" nên parse ra ()) trong khi
  UnityPy (`BundleFile.parse_version`) dùng `version_engine`. Sửa sang
  `unity_revision` ở `parse_bundle`, `uses_padding`, `build_bundle`;
  `_uses_new_flags` đổi tên tham số thành `engine_version`.
- Hậu quả nếu không sửa: bundle Unity 2019 thật bị nhận cờ mới (cờ mã hóa
  0x200 bị bỏ qua, cờ padding 0x200 bị hiểu sai; bundle v6 từ 2019.4.15+
  không được căn 16). Fixture cũ không lộ vì builder ghi cùng một chuỗi
  "2019.4.40f1" cho cả hai trường.

### SerializedFile v23 — cùng file (decisions #79, #80)
- `SUPPORTED_SF_VERSIONS` thêm 23. `_parse_serialized_type`: v23+ đọc
  content hash XXH3 16 byte + i32 serialized size trước blob; size 0 thì
  node = None (không có type tree); kiểm blob đọc đúng bằng size đã khai
  (sai -> E_CORRUPT). `_parse_typetree_blob`: v23+ kiểm magic "mhtt" +
  format version (phải bằng version) trước node_count; node cùng format
  v19+ (32 byte, thêm Q ref hash). `_write_serialized_type`: v23 ghi hash +
  i32 len(raw_typetree) + blob. `SerializedType` thêm trường
  `type_tree_content_hash` (mặc định 16 byte 0).
- **Header mở rộng v22+ LUÔN big-endian (decision #80, bug thật):**
  `parse_serialized` đọc header mở rộng bằng `_Reader(data[20:], ">")`;
  `build_serialized` và `_rebuild_assets_section` ghi bằng `pack_be`.
  Trước đây đọc/ghi bằng endian của file (thường little) — sai, xem §3.

### Fixture — `tests/fixtures/unity/build_fixtures.py` (đã mở rộng)
- `build_serialized_file`: header mở rộng 48 byte cho v>=22 (4 số đầu ghi
  0 + endian flag + extended fields **big-endian**), byte_start u64 từ v22,
  type tree v23 (hash 16 byte 0 + size + blob), type_dependencies rỗng cho
  type thường khi enable_type_tree và version >= 21.
- `typetree_blob(root_type, children, version)`: node 24 byte tới v18,
  32 byte từ v19; v23+ thêm tiền tố "mhtt" + format version.
  `typetree_blob_text_asset()` / `typetree_blob_mono()` dùng chung helper.
- `build_bundle`: thêm `unity_revision`, `extra_data_flags`,
  `pad_block_data` (như `sdk/unity.py`); căn 16 sau header kiểm theo
  revision (trước đây so chuỗi `unity_version >= "2019.4.15"`).
- Fixture mới (Unity 6, revision "6000.0.23f1", player "5.x.x"):
  `sample_v23.assets` (7163B, SF v23, 3 object: TextAsset `greeting`
  "Xin chao Unity 6!" path_id 4001, TextAsset `longtext` 5437 ký tự path_id
  4002, MonoBehaviour mờ path_id 4003; type tree cho cả 2 type),
  `sample_v8.unity3d` (7759B, UnityFS v8 không nén, 2 entry SF v23),
  `sample_v8_lz4.unity3d` (762B, v8 + LZ4 + blocks-info-at-end + cờ
  padding 0x200), `nested_v8.ipa` (1103B, ZIP chứa
  `Payload/Game.app/Data/level.unity3d` là bundle v8).
- Fixture cũ giữ nguyên nội dung (kiểm sha256 với zip Claude 05
  complete sau khi sinh lại: `resources.assets`, `sharedassets0.assets`,
  `sample_typetree.assets` (260B), `sample_v21.assets`,
  `sample.unity3d` (7151B), `sample_lz4.unity3d`, `sample_lzma.unity3d`,
  `sample_v6.unity3d` (7135B) đều SAME; `nested.ipa` khác sha chỉ vì
  timestamp trong ZIP entry (zipfile ghi giờ hiện tại: 12:45 -> 14:11),
  nội dung 3 entry giống hệt).

### Test mới — 22 test (`tests/test_unity.py`, 68 -> 90)
- `TestBundleV8` (11): detect v8 (player "5.x.x" + revision
  "6000.0.23f1"), parse v8 không nén, v8 LZ4 + blocks-at-end + padding,
  gating cờ theo revision (2019.4.40f1/2020.3.33 -> cờ cũ;
  2020.3.34/2022.3.58/6000.0.23f1/"5.x.x" -> cờ mới), 0x200 + revision
  cũ -> E_ENCRYPTED, 0x1400 + revision mới -> E_ENCRYPTED, 0x200 +
  revision mới + pad -> parse đúng, cùng bit 0x200 + revision cũ ->
  E_ENCRYPTED, build round-trip v8 giữ revision, plan v8 (CHẮC CHẮN,
  "UnityFS v8 (6000.0.23f1)", TextAsset 4001/4002/4004), plan
  `nested_v8.ipa`.
- `TestSerializedV23` (8): parse v23, typetree blob (node TextAsset/
  MonoBehaviour, blob mở đầu "mhtt"), extended header big-endian (hồi
  quy #80), build round-trip v23, typetree rỗng (size 0 -> node None,
  object vẫn đọc được), patch inplace v23, patch REBUILD_SECTION v23,
  patch trong bundle v8 (giữ v8 + revision, entry còn lại nguyên).
- Cập nhật 2 test cũ theo thực tế mới (không hạ tiêu chuẩn):
  `test_unsupported_version_raises` đổi v8 -> v9 (v9 không có bằng
  chứng tồn tại, decision #77); `test_support_matrix` đổi chuỗi
  "UnityFS v6/v7/v8 (bundle)" và "SerializedFile v15-23 (.assets)".
- `TestUnityCli` thêm 3 test: inspect `sample_v8.unity3d` (ra "UnityFS
  v8", "6000.0.23f1", TextAsset `greeting`), inspect `nested_v8.ipa`,
  patch bundle v8 (dry-run KHÔNG ghi, `--allow-write` ghi
  `workspace/output/sample_v8/sample_v8.unity3d`, bundle ra vẫn v8 +
  revision "6000.0.23f1", nguồn không đổi).
- Tổng suite: **643 -> 665 test**.

### Không đổi
- Backend hybrid AUTO/LIBRARY/NATIVE + `decisions`, patcher an toàn
  (dry-run mặc định, đơn vị xuất nhỏ nhất, diffverify, output versioned,
  không ghi đè nguồn), REBUILD_ALL vẫn khóa ở M2, CLI `unity`/`mod`
  không đổi giao diện. Mã tương thích Python 3.9 (không cú pháp 3.10+,
  không `tomllib`, không `|=` trên set/dict; đã quét). Không có câu
  `import UnityPy`/`import lz4` nào trong mã (quy tắc iSH, decisions #73).
- `ui/handlers.py`: ghi chú mục mod đổi thành "UnityFS v6/v7/v8 +
  SerializedFile v15-23, patch TextAsset (M2)."

## 2. Định dạng: mức cao nhất ĐÃ KIỂM CHỨNG (từ mã nguồn UnityPy 1.25.4)

Kiểm chứng ngày 2026-11-15 từ `/tmp/upvenv8` (UnityPy 1.25.4, pip cài
mới; venv dùng một lần, không thuộc PB3). Mọi giới hạn dưới đây đều có
căn cứ trong mã nguồn UnityPy, không phải phỏng đoán.

| Định dạng | Max đã kiểm chứng | Đọc | Ghi | Backend | Maturity |
|---|---|---|---|---|---|
| UnityFS (bundle) | **v8** | có (v6/v7/v8) | có (dựng lại bundle, giữ compression gốc) | Python thuần + lz4/lzma | M2 |
| SerializedFile (.assets) | **v23** | có (v15–23: metadata, object table, TextAsset) | có (patch TextAsset: PATCH_INPLACE / REBUILD_SECTION) | Python thuần | M2 |
| Nén bundle | NONE/LZMA/LZ4/LZ4HC | có | có | binding lz4 (ưu tiên) / Python thuần (literal-only khi ghi) | M2 |
| LZ4 frame | — | có | chỉ qua binding lz4 | binding lz4.frame (ưu tiên) / Python thuần (chỉ đọc) | M2 |
| LZMA (Unity raw) | — | có | có | `lzma` (thư viện chuẩn) | M2 |
| UnityPy (object phức tạp) | 1.25.4 | khi cài được | khi cài được | UnityPy 1.25.4 (iSH đã chứng minh) | M2 |
| UnityFS v9+ | **không có bằng chứng tồn tại** | không | không | E_UNSUPPORTED_FORMAT | M0 |
| SerializedFile v<15 / v>23 | — | không | không | E_UNSUPPORTED_FORMAT | M0 |
| UnityWeb/UnityRaw/WebFile, UnityArchive | — | không | không | E_UNSUPPORTED_FORMAT | M0 |
| LZHAM | — | không | không | E_UNSUPPORTED_FORMAT (UnityPy cũng raise NotImplementedError) | M0 |
| Bundle mã hóa | — | không (E_ENCRYPTED) | không | không thử giải mã (chủ ý, xem §4) | M0 |

Căn cứ max version (đọc mã nguồn, không tin mù):
- **UnityFS v8 là max:** `BundleFile.read_fs` không kiểm version (mọi
  version đi cùng một đường đọc; chỉ căn 16 khi version >= 7 và chọn bộ
  cờ theo `version_engine`); bằng chứng công khai chỉ thấy version 6/7/8
  (reverseengineering.wiki: "format version 6, 7, 8 seen in UnityPy");
  game Unity 6 mới nhất vẫn dùng v8; grep toàn bộ mã nguồn UnityPy không
  có nhánh nào cho v9. PB3 chỉ chấp nhận 6/7/8.
- **SerializedFile v23 là max:** nhánh version cao nhất trong
  `SerializedFile.py` / `TypeTreeNode.py` là >= 23 (content hash +
  serialized size + magic "mhtt"); không có nhánh >= 24 nào trong mã
  nguồn. PB3 mở rộng tới 23, có fixture + test.
- **Nén: không có Zstd.** `CompressionFlags` chỉ có NONE/LZMA/LZ4/LZ4HC/
  LZHAM; grep toàn bộ mã nguồn UnityPy: 0 kết quả "zstd"/"zstandard".
  Brotli/gzip trong `CompressionHelper` chỉ dùng cho WebFile
  (UnityWebData — gói ngoài tải về, magic gzip/brotli), không phải
  UnityFS. `decompress_lzham` trong UnityPy raise NotImplementedError
  ("Custom compression or unimplemented LZHAM (removed by Unity)").
- **Cờ mã hóa:** `ArchiveFlagsOld.UsesAssetBundleEncryption = 0x200`;
  `ArchiveFlags.UsesAssetBundleEncryption = 0x1400` (0x400 cũ / 0x1000
  mới); `BlockInfoNeedPaddingAtStart = 0x200` (chỉ có ý nghĩa với cờ
  mới). Ngưỡng cờ mới: 2020.3.34 / 2021.3.2 / 2022.1.1 (theo
  `version_engine`).
- **TypeTree:** v23 là mức cao nhất có hành vi riêng (magic "mhtt");
  node blob cùng format v19+ (32 byte, thêm Q ref hash).

Chỉ tuyên bố hỗ trợ loại/version đã có fixture + test. Không tuyên bố
hỗ trợ mọi phiên bản Unity. `support_matrix()` trong `sdk/unity.py` trả
đúng bảng này.

## 3. Bằng chứng sandbox (Python 3.12.3, 2026-11-15)

- `python3 run_tests.py --tier full`: **`Ran 665 tests in 12.0s OK
  (skipped=0, lỗi=0, fail=0)`** (643 cũ + 22 mới). `--plan`: fast 603,
  ish 11, slow 34, meta 17, smoke 57.
- Môi trường sandbox: UnityPy KHÔNG có trong Python hệ thống; `lz4`
  4.0.2+dfsg có. Vì vậy trong sandbox, AUTO fallback sang NATIVE đối với
  UnityPy (parser thuần), LZ4 dùng binding; LIBRARY báo
  E_UNSUPPORTED_ON_ISH (đúng thiết kế, có test khóa). **Đây là chuyện
  riêng của sandbox; trên iSH UnityPy 1.25.4 + lz4 ĐÃ chứng minh cài được
  ngày 2026-11-15 18:21 — không cần bước cài đặt nào trên iSH.**
- UnityPy cross-check (venv `/tmp/upvenv8`, UnityPy 1.25.4 — dựng mới
  vì venv cũ đã mất): UnityPy đọc đúng mọi fixture mới —
  `sample_v23.assets` (SerializedFile, 3 object: 4001 `greeting` "Xin
  chao Unity 6!", 4002 `longtext`, 4003 MonoBehaviour),
  `sample_v8.unity3d` + `sample_v8_lz4.unity3d` (BundleFile v8, player
  "5.x.x", engine "6000.0.23f1", 2 assets, 4 object), `nested_v8.ipa`
  (UnityPy load trực tiếp trên ZIP trả 0 object — hành vi đã biết của
  UnityPy với ZIP, fixture cũ `nested.ipa` cũng vậy; giải nén entry
  `level.unity3d` ra rồi nạp thì đủ 4 object 4001/4002/4003/4004).
- UnityPy cross-check trên output ĐÃ patch (venv `/tmp/upvenv8`):
  `patched_v23_inplace.assets` đọc lại `greeting` = "Xin chao Unity
  7!"; `patched_v23_rebuild.assets` đọc lại `greeting` = "Xin chao cac
  ban choi game Unity 6 Unity 6!"; `patched_v8.unity3d` nạp được, 4
  object, `greeting` = "Xin chao Unity 7!", `config` giữ nguyên JSON;
  `v23_empty_tt.assets` (type MonoBehaviour có typetree size 0) nạp
  được, đủ 3 object.
- Mô phỏng iSH bằng venv UnityPy 1.25.4: đủ suite **`Ran 665 tests in
  11.7s OK (skipped=1, lỗi=0, fail=0)`** (skip duy nhất:
  `test_library_raises_when_unitypy_missing`, đúng vì UnityPy có mặt);
  `UnityBackend("AUTO")` khởi tạo trong **0.005s**, `UnityPy` không có
  trong `sys.modules` sau khởi tạo, probe báo đúng `unitypy 1.25.4 ok`,
  `lz4 4.4.5 ok`.
- Lỗi tìm được khi viết test/fixture và đã sửa trong mã (không sửa test
  cho qua):
  (1) **type_dependencies thiếu trong builder fixture** — builder
  `build_serialized_file` không ghi `type_dependencies` (v21+) cho type
  thường khi enable_type_tree; reader (đúng theo UnityPy: mục này chỉ
  đọc khi enable_type_tree) đọc nhầm class_id của type sau thành count
  -> lệch toàn bộ metadata, type thứ hai đọc ra serialized size rác
  (-1597806493). Sửa: builder ghi i32 0 sau mỗi blob (v>=21, khi
  enable_type_tree). Fixture v17/v21 cũ không lộ vì v17 < 21 và v21
  không bật type tree.
  (2) **header mở rộng v22+ phải big-endian (decision #80)** — xem §1.
  Trước sửa, UnityPy `check_file_type` nhận nhầm file v23 do PB3 ghi
  thành ResourceFile (metadata_size đọc BE ra 0x15020000 > độ dài file)
  và tìm được 0 object; sau sửa, UnityPy đọc đúng. Bug chưa từng lộ vì
  C05 không có fixture v22/v23 nào; file v17/v21 không bị ảnh hưởng.
  (3) **gating cờ theo player version (decision #78)** — xem §1.
  (4) builder `typetree_blob` ghi trùng chuỗi "string" hai lần trong
  string buffer (làm `sample_typetree.assets` phình 260 -> 276 byte);
  sửa: dedup chuỗi, fixture cũ về đúng 260 byte như trước.
- Quyết định ghi ở `handoff/decisions.md` #77–80.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Chỉ hỗ trợ UnityFS v6/v7/v8, SerializedFile v15–23, TextAsset cho
  patch. Object phức tạp (MonoBehaviour có dữ liệu, Mesh, Texture...)
  chỉ đọc được qua UnityPy khi cài được; parser thuần không hiểu nội
  dung của chúng (đọc raw, giữ nguyên hash khi patch object khác).
- Encoder LZ4 thuần là literal-only (không nén); bundle rebuild bằng
  NATIVE sẽ lớn hơn bản dùng binding (vẫn hợp lệ, UnityPy đọc được). Khi
  có binding lz4 (iSH có), AUTO dùng binding nên không gặp vấn đề này.
- REBUILD_ALL bị khóa ở M2 (cần M4 + dry-run + diff + xác nhận người
  dùng); chuỗi mới dài hơn chỉ leo thang tới REBUILD_SECTION.
- `detect_engine` chỉ gợi ý từ tên entry; file `.assets` lẻ không có tên
  gợi ý vẫn được planner liệt kê nhưng verdict có thể là CHƯA ĐỦ BẰNG
  CHỨNG (inspect không lỗi, chỉ báo).
- **UnityFS v9+ không hỗ trợ** — không có bằng chứng v9 tồn tại (Unity 6
  mới nhất vẫn dùng v8); nếu Unity ra version mới thì cần fixture + test
  mới, không tự nhận hỗ trợ.
- **SerializedFile v24+ không hỗ trợ** — không có nhánh nào cho v24
  trong mã nguồn UnityPy 1.25.4.
- **Zstd không hỗ trợ** — không phải mã nén của UnityFS (UnityPy cũng
  không có); bundle nào dùng mã nén ngoài NONE/LZMA/LZ4/LZ4HC -> 
  E_UNSUPPORTED_FORMAT (LZHAM cũng vậy; Unity đã gỡ LZHAM).
- **UnityWeb/UnityRaw/WebFile/UnityArchive không hỗ trợ** — họ định
  dạng container khác (WebFile dùng gzip/brotli ở lớp ngoài); thuộc
  việc tương lai, không thuộc 05b.
- **Bundle mã hóa -> E_ENCRYPTED, không thử giải mã (chủ ý).** UnityPy
  có `ArchiveStorageDecryptor` cho một số bundle mã hóa theo scheme
  ArchiveStorage, nhưng PB3 từ chối mọi bundle mã hóa theo chính sách
  an toàn (decision #67): không giải mã, không đoán khóa. Game nào mã
  hóa bundle thì inspect/patch sẽ từ chối sạch kèm đúng bundle.
- Mã tương thích Python 3.9 (không dùng cú pháp 3.10+, không `tomllib`,
  không `|=` trên set/dict); đã kiểm trên Python 3.12.3 (sandbox) và
  dưới venv UnityPy 1.25.4 (mô phỏng iSH). Chạy thật trên Python 3.9.16
  (iSH) chưa có — xem mục 5.

## 5. Kiểm chứng trên iSH — NOT RUN (chờ Joseph)

Kỳ vọng sau khi giải nén zip 05b đè lên `/root/pb3` (như mọi lần):

```sh
cd /root/pb3
python3 run_tests.py --tier full
```

Kỳ vọng: `Ran 665 tests ... OK (skipped=4, lỗi=0, fail=0)` — **661 passed,
4 skipped** (2 thiếu `tomllib`, 1 máy có `/dev/location`, +1
`test_library_raises_when_unitypy_missing` skip vì UnityPy có mặt trên
iSH). Thời gian khoảng 12 phút (bản 643 test mất 719,4s ngày 2026-11-15).

Unity CLI (trong `/root/pb3`):

```sh
python3 menu.py unity inspect tests/fixtures/unity/sample_v8.unity3d
python3 menu.py unity inspect tests/fixtures/unity/nested_v8.ipa
python3 menu.py unity read tests/fixtures/unity/sample_v23.assets --object 4001
python3 menu.py unity patch tests/fixtures/unity/sample_v23.assets --object 4001 --old "Unity 6" --new "Unity 7"
python3 menu.py unity patch tests/fixtures/unity/sample_v23.assets --object 4001 --old "Unity 6" --new "Unity 7" --allow-write
python3 menu.py unity inspect workspace/input/game.ipa
```

Kỳ vọng:
- `inspect sample_v8.unity3d`: engine unity (CHẮC CHẮN), lý do
  `UnityFS v8 (6000.0.23f1)`, backend AUTO lz4 4.4.5 + UnityPy 1.25.4,
  plan 6 dòng (bundle + TextAsset `greeting` 4001 / `longtext` 4002 /
  MonoBehaviour 4003 + bundle + `config` 4004).
- `inspect nested_v8.ipa`: unity (CÓ KHẢ NĂNG), plan 8 dòng.
- `read --object 4001`: in ra `Xin chao Unity 6!`.
- `patch` dry-run: patch OK (PATCH_INPLACE), verify OK, KHÔNG ghi file;
  `--allow-write` ghi `workspace/output/sample_v23/sample_v23.assets`
  (nguồn không đổi).
- **`inspect workspace/input/game.ipa` (quan trọng nhất): kỳ vọng mới
  là CHẮC CHẮN với plan đầy đủ (thay vì E_UNSUPPORTED_FORMAT như ngày
  2026-11-15 20:56).** Nếu vẫn lỗi ở bundle nào thì chụp nguyên văn
  lỗi gửi lại — đó là bằng chứng thật, không phải thất bại của gói 05b
  (mỗi bundle trong IPA có thể khác version/compression; 05b chỉ tuyên
  bố hỗ trợ tới mức §2). **Chỉ inspect IPA thật, KHÔNG patch trong bước
  này.**

## 6. Việc cho Claude 06 (chưa làm)
- Dùng `sdk/unity.py` (`plan_bytes`, `apply_patch`, `UnityBackend`) làm
  backend cho format plugin Unity (06); REBUILD_ALL chỉ mở khi có bằng
  chứng M4; object phức tạp ngoài TextAsset cần UnityPy (đã có trên iSH).
- UnityWeb/UnityRaw/WebFile, bundle mã hóa, UnityFS v9+ (nếu xuất hiện)
  là việc tương lai, ngoài phạm vi 05b.
- Không đóng gói lại IPA.
