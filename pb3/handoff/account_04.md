# TÀI KHOẢN CLAUDE 04 — CONTAINER VÀ VIRTUAL FILESYSTEM

Trạng thái: **COMPLETE (2026-11-15; sandbox + iSH PASS)**.
Phạm vi: prompt 04 trong `PB3_v3.1_10_Claude_Prompts.txt`, đặc tả §5.1–5.4 và mục
"CLAUDE 04 - CONTAINER VÀ VIRTUAL FILESYSTEM". Gói 03 đã COMPLETE (bằng chứng iSH
trong `handoff/account_03.md`). Không làm 05–10.

## 1. Đã làm (sandbox, 2026-11-15)

### VFS thật — `sdk/containers.py` (mới; `sdk/vfs.py` KHÔNG đổi)
- `ZipVFS`: VFS đầy đủ 5 hàm trên `zipfile` chuẩn. Mở kiểm EOCD, số entry
  (`max_entries`), tổng giải nén (`max_total_uncompressed_bytes`) theo `Limits`;
  tỉ lệ nén bất thường chỉ ghi cảnh báo (đặc tả 5.2). Entry tên không an toàn
  (tuyệt đối, `..`, NUL, `\`, symlink, mã hóa) bị loại khỏi VFS, ghi vào
  `inventory().rejected`; trùng tên chỉ dùng lần đầu. Đọc toàn bộ entry kiểm
  CRC (sai -> E_BAD_ARCHIVE); slice/đọc tiền tố dùng giải nén thô (STORED /
  DEFLATED, `zlib`), KHÔNG kiểm CRC; `open_stream` streaming, không nạp cả file.
  Giữ metadata phục vụ ghi lại: method, date_time, external_attr, extra,
  comment, CRC, thứ tự entry (`EntryInfo`/`ContainerInventory`).
- `TarVFS`: TAR/TAR.GZ/TAR.XZ chỉ đọc qua `tarfile` chuẩn (không dùng `filter=`
  vì iSH dùng Python 3.9; kiểm tên bằng tay). Chỉ file thường được đọc;
  symlink/hardlink, tên không an toàn bị chặn. TAR không có CRC từng entry.
- `SingleStreamVFS`: file `.gz`/`.xz` lẻ (một luồng): một entry duy nhất, kích
  thước đếm bằng một lượt giải nén streaming rồi ghi nhớ.
- `open_vfs(path|bytes)` + `detect_container_kind(magic)`: ZIP -> ZipVFS;
  TAR -> TarVFS; GZ/XZ -> TarVFS nếu là tar nén, không thì luồng đơn; 7z ->
  E_UNSUPPORTED_FORMAT (chưa có backend, không thêm phụ thuộc chưa kiểm chứng);
  không nhận diện được -> E_UNSUPPORTED_FORMAT (thử TAR v7 không magic trước).
- `NestedVFS`: container lồng qua `'::'` (`Payload/Game.app/Data/xxx.unity3d::resources.assets`),
  mở lazy khi yêu cầu, cache theo SHA-256 (tối đa 16), độ sâu `limits.max_depth`
  (mặc định 4), phát hiện vòng lặp bằng chuỗi hash tổ tiên, entry lồng bị giới
  hạn bởi `read_policy` của tầng cha (E_ARCHIVE_BOMB / E_TOO_LARGE).
- `rebuild_zip`: dựng lại ZIP giữ thứ tự/method/metadata entry không đổi;
  thay entry trong `replacements`, thêm entry mới vào cuối, đổi method chỉ khi
  `method_overrides` yêu cầu (ghi vào `ZipRebuildReport.method_changes`).
  Ghi bằng `safe_io.AtomicWriter` (temp -> verify mở lại + kiểm CRC từng entry
  -> fsync -> os.replace): lỗi giữa chừng thì file đích cũ còn nguyên. Entry mã
  hóa mà không được thay -> E_ENCRYPTED. Chỉ thao tác trên file container được
  đưa vào (đơn vị nhỏ nhất có thể thay thế), không bao giờ đóng gói lại toàn
  bộ gói ngoài. Kiểm capability `write`/`write_modes` thuộc về lớp gọi
  (planner, Claude 05); hàm ghi nhận `write_mode` (`PATCH_INPLACE` /
  `REBUILD_SECTION` / `REBUILD_ALL`, mặc định `REBUILD_ALL`).

### Đăng ký container — plugin builtin `plugins/zip/` (mới)
- `ZipPlugin` (kind=`container`, capabilities=`["container"]`, maturity **M2**,
  priority 10, match `.zip`/`.ipa` + magic `PK`, `container_kind="zip"`):
  `detect` / `list_candidates` / `probe` / `open_container(ctx, path)` (đọc
  bytes qua `ctx.vfs` rồi mở bằng `open_vfs`) / `selftest` (zip trong RAM).
  `plugin list` hiện `zip [ok] M2`; `plugin test zip` PASS (xem mục 3).
  Đây là plugin builtin ĐẦU TIÊN của dự án (trước đó `plugins/` rỗng);
  2 test cũ assert "chưa có plugin" đã cập nhật theo thực tế mới
  (`tests/test_registry.py`).

### CLI — `services/container_tool.py` (mới), lệnh `container`
- `container list <file>`: bảng kê entry (tên, kích thước, method) + entry bị
  chặn + cảnh báo. `container read <file> <entry> --out <dest>`: đọc một entry
  (kể cả đường dẫn lồng `'::'`) ra file; không ghi đè file đã có
  (E_OUTPUT_EXISTS). Nối dây qua `ui/handlers.py` + `ui/cli.py`; menu tương
  tác 5 mục không đổi.

### Test mới — 48 test (`tests/test_container.py` 38, `tests/test_vfs_real.py` 10)
CRC, zip bomb (tổng/số entry/tỉ lệ), path traversal, symlink, entry mã hóa,
trùng tên, metadata, slice/stream, TAR/GZ/XZ, luồng đơn, container lồng
(3 tầng, giới hạn độ sâu, vòng lặp, cache), writer round-trip (giữ thứ tự/
method/metadata, đổi method có báo cáo, thêm entry, entry mã hóa bị từ chối,
rollback giữ file đích cũ khi lỗi), 7z unsupported, plugin zip
(detect/candidates/probe/open_container/selftest + conformance),
CLI `container list/read` thật qua tiến trình con.
Tổng suite: **527 -> 575 test**.

## 2. Định dạng: đọc / ghi / không hỗ trợ

| Định dạng | Đọc | Ghi | Backend |
|---|---|---|---|
| ZIP / IPA | có | có (`rebuild_zip`) | `zipfile` (thư viện chuẩn) |
| TAR | có | không | `tarfile` (thư viện chuẩn) |
| TAR.GZ / TGZ | có | không | `tarfile` + `gzip` |
| TAR.XZ | có | không | `tarfile` + `lzma` |
| GZ (một luồng) | có | không | `gzip` |
| XZ (một luồng) | có | không | `lzma` |
| 7z | không | không | chưa có (không thêm phụ thuộc chưa kiểm chứng) |

Ghi chú: "mở được zip" không có nghĩa "nội dung hợp lệ" (đặc tả 5.4). Định dạng
chưa có container plugin vẫn dùng được bằng quét thư mục đã giải nén hoặc scan
raw (M1, chỉ nhận diện) — phần đó thuộc Claude 06/07, không phải 04.

## 3. Bằng chứng sandbox (Python 3.12.3, 2026-11-15)

- `python3 run_tests.py --tier full` trên bản làm việc: **`Ran 575 tests in 9.1s
  OK (skipped=0, lỗi=0, fail=0)`** (527 cũ + 48 mới; 2 test cũ sửa theo thực
  tế có plugin zip, không hạ tiêu chuẩn). `--plan`: fast 524, ish 11, slow 23,
  meta 17, smoke 57.
- Fixture `tests/fixtures/zip/sample.ipa` (672 byte, IPA thật về cấu trúc:
  `Payload/Game.app/Info.plist`, `Payload/Game.app/Data/level.unity3d` (zip
  lồng chứa `resources.assets` + `sharedassets0.assets`), `config.txt`):
  `container list` ra 3 entry; `container read ... level.unity3d::resources.assets`
  ra đúng 223 byte bắt đầu `UNITYFS-fake-resources-`.
- `plugin test zip`: **KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (8 PASS, 0 FAIL,
  3 SKIP; fixture: 1 file)** — `container` PASS (mở `sample.ipa`), `parse` SKIP
  (plugin container không khai `read`, đúng thiết kế; mục kiểm dữ liệu có ý
  nghĩa ở đây là mục `container`).
- `rebuild_zip` trên `sample.ipa`: thay `config.txt` -> `level=2`, giữ đúng
  thứ tự 3 entry, method DEFLATED, đọc lại đúng; thay entry hỏng CRC -> đích
  cũ còn nguyên (rollback).
- 2 lỗi tìm được khi viết test và đã sửa trong mã (không sửa test cho qua):
  (1) `error_from_exception` không biết lỗi `tarfile` nên quy về E_INTERNAL —
  thêm `_tar_error` dịch sang E_BAD_ARCHIVE; (2) `zipfile` kiểm CRC ngay cả
  khi đọc từng phần nên slice muốn đúng đặc tả ("không xác thực CRC") phải
  giải nén thô — thêm `_slice_raw` (STORED/DEFLATED; BZIP2/LZMA dùng lại
  luồng zipfile, có thể báo CRC sai, đã ghi rõ); (3) `NestedVFS.iter_entries`
  lỗi với tiền tố kết thúc bằng `::` (đoạn rỗng) — đã sửa.
- Quyết định ghi ở `handoff/decisions.md` #61–65.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Slice/stream BZIP2/LZMA trong ZIP dùng lại luồng `zipfile` nên vẫn có thể
  báo CRC sai (chỉ STORED/DEFLATED có slice thô không kiểm CRC). Ghi lại
  entry DEFLATE nén lại bằng cùng method (nội dung + metadata giống, byte nén
  có thể khác byte gốc); entry STORED giữ nguyên byte.
- Phát hiện vòng lặp container dựa trên hash byte giống nhau ở hai tầng; chu
  trình lồng "thật" bằng zip hợp lệ không dựng được bằng zipfile chuẩn nên
  nhánh này test bằng `open_vfs` giả lập (mock), còn chống lặp vô hạn thật
  được bảo vệ bởi giới hạn độ sâu (đã test bằng zip lồng 5 tầng).
- `open_container` của plugin zip đọc toàn bộ container qua `ctx.vfs.read`
  nên container trong VFS bị giới hạn bởi `max_entry_bytes` /
  `max_memory_read_bytes`; container lớn trên đĩa nên mở trực tiếp bằng
  `open_vfs(đường_dẫn)` (streaming, lazy) — đó là đường 05/07 dùng.
- `SingleStreamVFS` mở lazy: file `.gz`/`.xz` hỏng chỉ lộ khi thật sự đọc
  (stat/read), không lộ lúc mở.
- Chưa có inventory tương tác trong menu (mục Chẩn đoán 5); xem container
  bằng lệnh CLI `container list/read`.
- Mã chỉ dùng thư viện chuẩn + Python 3.9 tương thích (không dùng `filter=`
  của tarfile, không dùng cú pháp 3.10+); đã kiểm trên Python 3.9.16 thật
  (suite 575 test PASS trên iSH 19:20, xem mục 5).

## 5. Bằng chứng iSH — 04 COMPLETE (2026-11-15)

- **Suite trên iSH: PASS.** 2026-11-15 19:20 +07, trong `/root/pb3`,
  `python3 run_tests.py --tier full`: `Ran 575 tests in 449.0s OK
  (skipped=3, lỗi=0, fail=0)` — 572 passed, 3 skipped, 0 failed. Chậm nhất
  47.6s `TestRealCli::test_disable_survives_new_process`; 34.0s
  `TestCliEndToEnd::test_new_and_test_via_menu`; 28.1s
  `TestContainerCli::test_container_errors`; 42 test vượt ngân sách 1.0s,
  tự chuyển sang tầng chậm từ lần sau; đã lập mốc FULL xanh (dùng cho
  `--changed`). 3 skip cũ: 2 thiếu `tomllib`, 1 máy có `/dev/location`.
- **Container CLI thật trên iSH: PASS.** 2026-11-15 19:21 +07, trong
  `/root/pb3`: `python3 menu.py container list tests/fixtures/zip/sample.ipa`
  ra `container: tests/fixtures/zip/sample.ipa (zip, 3 entry)`:
  `Payload/Game.app/Info.plist 93 byte deflated`,
  `Payload/Game.app/Data/level.unity3d 300 byte deflated`,
  `Payload/Game.app/Data/config.txt 8 byte deflated`.
- **`plugin test zip` thật trên iSH: PASS.** Cùng lúc: `KẾT QUẢ: PASS
  (CHƯA KIỂM DỮ LIỆU) (8 PASS, 0 FAIL, 3 SKIP; fixture: 1 file)` — PASS:
  manifest (`id=zip kind=container maturity=M2 caps=container`), readonly,
  detect (1 bằng chứng), candidates (1 ứng viên), probe (1 ứng viên),
  container (mở `sample.ipa`), selftest, guard (7 hook, 0 vi phạm); SKIP:
  parse (không khai `read`), write (chỉ đọc), hints.
- **`plugin list` thật trên iSH:** `zip [ok] M2`, `container | engine:
  generic | builtin v0.1.0`.
- **Ghi chú trung thực:** `container list` trên iSH chạy trên file fixture
  `tests/fixtures/zip/sample.ipa` trong zip, KHÔNG phải file IPA game thật
  của người dùng (lúc này không có IPA thật trên máy). Chưa thử `container
  read` lồng `'::'` trên iSH (đã thử trong sandbox: đọc đúng 223 byte).
  Gói 04 COMPLETE; gói 05 làm tiếp sau.

## 6. Việc cho Claude 05 (chưa làm)
- Dùng `sdk/containers.py::open_vfs` / `rebuild_zip` + `sdk/vfs.py::read_policy`
  để mở IPA/`xxx.unity3d` và ghi lại đơn vị nhỏ nhất.
- Plugin zip đã đăng ký qua `load_plugin_class` / thư mục `plugins/zip/`;
  format plugin (06) dùng `open_container` khi cần mở container trong VFS.
- Unity backend (UnityPy/LZ4 đã cài trên iSH 2026-11-15, 1.25.4) và ghi ngược
  tối thiểu thuộc 05, không thuộc 04.
