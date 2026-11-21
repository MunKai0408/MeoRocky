# MA TRẬN HỖ TRỢ — PB3 v3.1

> **Cảnh báo: Mức hỗ trợ chỉ đúng với những gì đã kiểm thử.**
> Không có mục nào đạt M4 (M4 cần bằng chứng game thật + byte-diff sạch — chưa có).
> Mức hiệu lực (maturity hiệu lực) tính theo bằng chứng tests/corpus/<id>.json (decisions #51): plugin khai M3/M4 mà thiếu bằng chứng thì bị hạ, sàn M2.

## Bảng plugin

| Plugin | Loại | Engine | Maturity hiệu lực | Số game đã test | Ngày test | Giới hạn đã biết |
|---|---|---|---|---|---|---|
| apple_plist | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| apple_strings | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| cocos_detect | engine | cocos | M2 | 0 | chưa có | chỉ nhận diện (chưa trích xuất) |
| csv_table | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| defold_detect | engine | defold | M2 | 0 | chưa có | chỉ nhận diện (chưa trích xuất) |
| gamemaker_detect | engine | gamemaker | M2 | 0 | chưa có | chỉ nhận diện (chưa trích xuất) |
| generic_strings | extractor | generic | M2 | 0 | chưa có | chỉ đọc |
| godot_pck | container | godot | M1 | 0 | chưa có | chỉ đọc |
| godot_translation | format | godot | M2 | 0 | chưa có | chỉ đọc |
| json_text | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| po_mo | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| properties | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| resx | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| sqlite_loc | format | generic | M2 | 0 | chưa có | chỉ đọc |
| unreal_locres | format | unreal | M2 | 0 | chưa có | chỉ đọc |
| unreal_pak | container | unreal | M1 | 0 | chưa có | chỉ đọc |
| xliff | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| xml_strings | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| yaml_ini | format | generic | M3 | 0 | 2026-11-15 | ghi ngược (mới test fixture) |
| zip | container | generic | M2 | 0 | chưa có | chỉ đọc |

## Bảng engine / định dạng

### Container / archive

| Định dạng | Đọc | Ghi | Backend |
|---|---|---|---|
| ZIP / IPA | có | có (rebuild_zip) | zipfile (thư viện chuẩn) |
| TAR | có | không | tarfile (thư viện chuẩn) |
| TAR.GZ / TGZ | có | không | tarfile + gzip (thư viện chuẩn) |
| TAR.XZ | có | không | tarfile + lzma (thư viện chuẩn) |
| TAR.BZ2 | có | không | tarfile + bzip2 (thư viện chuẩn) |
| GZ (một luồng) | có | không | gzip (thư viện chuẩn) |
| XZ (một luồng) | có | không | lzma (thư viện chuẩn) |
| BZ2 (một luồng) | có | không | bzip2 (thư viện chuẩn) |
| 7z | không | không | chưa có (không thêm phụ thuộc chưa kiểm chứng) |
| Godot PCK (3/4) | có (chỉ đọc mục lục + entry không nén) | không | sdk.godot.PckVFS (thuần Python) |
| Unreal PAK (UE4/UE5) | có (chỉ entry không nén, không mã hóa) | không | sdk.unreal.PakVFS (thuần Python) |

### Unity

| Định dạng | Đọc | Ghi | Backend | Maturity |
|---|---|---|---|---|
| UnityFS v6/v7/v8 (bundle) | có | có (dựng lại bundle, giữ compression gốc) | Python thuần + lz4/lzma | M2 |
| UnityFS v9 (bundle) | có (chỉ đọc; BlockInfo có offset u64, block căn 16) | không (v9 chỉ đọc — dựng lại bundle v9 chưa hỗ trợ) | Python thuần | M2 |
| UnityFS v8 cờ 0x200 + revision 0.0.0 (EK2) | có — bit 0x200 là cờ padding (cờ mới), KHÔNG phải mã hoá (100/100 bundle EK2 v0.0.18 parse được [ĐÃ ĐO THẬT 22/11]) | có (giữ nén LZ4HC gốc; cờ padding đặt lại theo layout ghi) | Python thuần | M2 |
| Zstd (block bundle, mã nén 5) | có — CHỈ trên web BearTea (qua fzstd, cầu nối JS) | không (ghi lại bằng block không nén, cờ 0) — đánh giá F3 22/11: 0/1904 bundle thật dùng mã 5; fzstd vendored CHỈ giải nén, không có encoder nên chưa cài ghi Zstd | fzstd 0.1.1 (MIT, vendored site/fzstd/) | M2 |
| SerializedFile v15-26 (.assets) | có (metadata, object table, TextAsset; v23+ type tree có ref hashes, v26 shared subtrees) | có (patch TextAsset: PATCH_INPLACE / REBUILD_SECTION) | Python thuần | M2 |
| LZ4 block / LZ4HC | có | có | binding lz4 (ưu tiên) / Python thuần (literal-only khi ghi) | M2 |
| LZ4 frame | có | chỉ qua binding lz4 | binding lz4.frame (ưu tiên) / Python thuần (chỉ đọc) | M2 |
| LZMA (Unity raw) | có | có | lzma (thư viện chuẩn) | M2 |
| UnityPy (object phức tạp) | khi cài được | khi cài được | UnityPy 1.25.4 (iSH đã chứng minh) | M2 |
| SerializedFile v<15 / v>26, UnityWeb/UnityRaw, LZHAM, bundle mã hóa thật | không | không | chưa có (E_UNSUPPORTED_FORMAT / E_ENCRYPTED — xem classify_bundle_encryption để phân loại) | M0 |

## Đã xác minh trên game thật

- **Unity inspect (đọc + bản đồ dữ liệu) trên IPA thật 9 Kings v1.0.5** — Unity inspect đã chạy trên IPA game thật; hỗ trợ UnityFS v8 bổ sung ở Claude 05b. Patch Unity chỉ mới test fixture (xem danh sách fixture). (nguồn: handoff/account_05.md §5).

## Mới test fixture

Mọi plugin/engine còn lại có fixture (chưa test game thật):

- apple_plist
- apple_strings
- cocos_detect
- csv_table
- defold_detect
- gamemaker_detect
- generic_strings
- godot_pck
- godot_translation
- json_text
- po_mo
- properties
- resx
- sqlite_loc
- unreal_locres
- unreal_pak
- xliff
- xml_strings
- yaml_ini
- zip
- UnityFS v6/v7/v8/v9 + SerializedFile v15–26 (v9 chỉ đọc, Zstd chỉ đọc trên web, fixture)
- Godot PCK v1/v2 (đọc, fixture)
- Unreal PAK UE4/UE5 (đọc, fixture)

## Chưa hỗ trợ

- **7z** — chưa có backend (không thêm phụ thuộc chưa kiểm chứng).
- **.locmeta nhị phân (Unreal)** — chưa có parser; chỉ .locres được đọc.
- **Godot PCK v0/v3** — chỉ v1/v2 có fixture.
- **Unreal PAK entry nén/mã hóa/Oodle** — chỉ đọc entry không nén, không mã hóa.
- **SerializedFile v<15 / v>26** — chỉ v15–26 có fixture.
- **Bundle Unity mã hóa** — báo E_ENCRYPTED, không giải mã.
- **Cocos/Defold/GameMaker** — chỉ nhận diện, chưa trích xuất.

Khi gặp định dạng chưa hỗ trợ: cần plugin mới cho định dạng đó; có thể dùng extractor `generic_strings` để dò chuỗi thô (chỉ đọc chuỗi, không phải bảng localization đã xác minh).
