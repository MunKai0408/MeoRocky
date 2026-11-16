# NHÓM NÉN IPA — BÀN GIAO (đợt rà soát tổng cuối PB3 v3.1, hạng mục d)

Trạng thái: **XONG (sandbox, 2026-11-16)** — 36 test mới, suite fast + full xem cuối file.
Phạm vi: CHỈ các file thuộc nhóm (xem mục 5). Không build zip, không sửa
`handoff/decisions.md` / `CURRENT_STATE.md`. Coordinator hợp nhất.

## 1. Thiết kế

**Định dạng đầu ra:** `tar.xz` chuẩn (đọc được bằng `tarfile` thường) mà member
đầu tiên là `pb3-manifest.json`:

```json
{"format": "pb3-solid-xz", "version": 1, "original_name": "game.ipa",
 "original_size": 100786, "original_sha256": "…",
 "created_by": "PB3 v3.1 (BearTea) solid-xz", "preset": 6,
 "entries": [{"name": "…", "size": 108800, "sha256": "…", "crc32": 123,
              "compress_size": 456, "method": "deflated",
              "date_time": [2024, 5, 6, 7, 8, 9], "external_attr": 420,
              "create_system": 3, "create_version": 20, "extract_version": 20,
              "extra": "", "comment": "", "is_dir": false}]}
```

**Vì sao solid:** `compress` đọc từng entry IPA, giải nén về bytes gốc
(streaming theo khối 1 MiB, kiểm CRC32 từng entry với central directory),
rồi nén SOLID toàn bộ thành MỘT luồng LZMA trong tar.xz. LZMA thấy dữ liệu
gốc (không phải byte đã nén DEFLATE) nên tận dụng trùng lặp xuyên entry —
khác hẳn nén lại file IPA nguyên vẹn (byte đã nén sẵn gần như không nén thêm
được). Hai lượt đọc IPA: lượt 1 băm sha256 + kiểm CRC (lỗi thì dừng trước khi
ghi gì), lượt 2 ghi tar.xz streaming (manifest ghi ĐẦU, nên `decompress`
chỉ cần một lượt đọc). Không giữ toàn bộ dữ liệu trong RAM, không giải nén
toàn bộ ra đĩa tạm.

**Khôi phục:** `decompress` đọc manifest trước, rồi stream từng member ra
file ZIP qua `ZipFile.open(zinfo, "w")` (ghi theo khối, không nạp entry vào
RAM), kiểm sha256 + CRC32 từng entry (sai -> `E_VERIFY_FAILED`, giữ bản ứng
viên `<đích>.failed` để điều tra, theo mẫu #72). Cuối cùng kiểm sha256 toàn
bộ file IPA khôi phục so với bản gốc, báo rõ `byte_identical`.

**File nén ra KHÔNG phải IPA cài trực tiếp** — chỉ là bản lưu trữ; service in
rõ điều này sau mỗi lần nén.

## 2. Preset đã chọn: mặc định 6 (đo thật, không đoán)

Fixture đo: `bench.ipa` 85.797.122 byte = 4 entry text 20 MiB (câu tiếng Việt
lặp, thứ tự ngẫu nhiên) + 1 entry 4 MiB byte ngẫu nhiên; entry STORED (đại
diện cho luồng dữ liệu gốc mà solid LZMA thấy). Chạy trong tiến trình con,
đo thời gian tường + RSS đỉnh (`resource.getrusage(RUSAGE_CHILDREN)`),
sandbox Python 3.12.3:

| preset | dict | thời gian | kích thước ra | tỉ lệ | RSS đỉnh | ước tính trong mã |
|---|---|---|---|---|---|---|
| 6 | 8 MiB | 6,8 s | 5.612.172 | 15,29:1 | **113 MiB** | 108 MiB |
| 7 | 16 MiB | 7,2 s | 5.612.172 | 15,29:1 | 205 MiB | 196 MiB |
| 8 | 32 MiB | 7,5 s | 4.559.456 | 18,82:1 | 389 MiB | 372 MiB |
| 9 | 64 MiB | 9,6 s | 4.559.460 | 18,82:1 | 678 MiB | 724 MiB |

Nhận xét:
- **Mặc định preset 6**: RSS đỉnh chỉ 113 MiB — an toàn trên iPhone 11 Pro Max
  4 GiB (còn chỗ cho iSH + hệ thống). Preset 7 không nhỏ hơn preset 6 trên
  fixture này (dữ liệu lặp nằm gọn trong dict 8 MiB) mà tốn gấp đôi RAM.
- Preset 8 nhỏ hơn 23% so với preset 6 nhưng tốn 389 MiB RAM; cho phép qua
  `--preset 8` kèm cảnh báo RAM. Preset 9 không nhỏ hơn preset 8 (chênh 4
  byte) mà tốn 678 MiB — chỉ nên dùng khi thật cần, có cảnh báo mạnh.
  Preset 9e không đo (chậm hơn nữa, ước tính ~1024 MiB); mã vẫn cho chọn,
  kèm cảnh báo mạnh nhất.
- Ước tính RAM trong mã (`preset_ram_estimate_mb`: dict x 11 + 20 MiB) khớp
  số đo thật trong ~5% ở mọi preset đã đo.
- Giải nén preset 8 trên cùng fixture: **1,5 s, byte-identical = True**.

Tỉ lệ đo được trên fixture NHỎ (test `tests/test_compress.py`):
- IPA mẫu hỗn hợp 100.786 byte (text + blob ngẫu nhiên + STORED + thư mục):
  nén còn 67.900 byte, tỉ lệ **1,48:1**, giảm 32,6%.
- Chỉ text giống nhau (3 entry y hệt): 2.590 -> 908 byte, tỉ lệ 2,85:1
  (solid thắng ZIP vì nén 1 lần thay vì 3 lần).
- Chỉ byte ngẫu nhiên: 65.714 -> 67.032 byte, tỉ lệ 0,98:1 (phí overhead ~2%).
- **Báo thật:** IPA game thật chủ yếu là texture/âm thanh đã nén + entry
  DEFLATE nên tỉ lệ 5:1 thường KHÔNG đạt được; tỉ lệ 15:1 ở trên chỉ đúng
  với dữ liệu text lặp. Mã báo rõ điều này trong lỗi "KHÔNG NÉN ĐƯỢC".

## 3. Luật kích thước (đã khóa bằng test)

- Đầu vào > 2 GiB -> `E_TOO_LARGE`, từ chối trước khi bắt đầu nén (test bằng
  `max_input_bytes` nhỏ, không cần file thật 2 GiB).
- Đích ≤ 400 MiB; 400 MiB < ra ≤ 500 MiB: thành công, in rõ "trượt đích 400MB".
- Ra > 500 MiB: **xóa output**, báo `E_TOO_LARGE` "KHÔNG NÉN ĐƯỢC" kèm kích
  thước tốt nhất đạt được + lý do (test bằng `max_output_bytes` nhỏ).
- Nguồn không bao giờ bị sửa (test so sha256 trước/sau). Output mặc định
  `workspace/output/<tên>.tar.xz`; đã tồn tại -> `E_OUTPUT_EXISTS` trừ
  `--force`. Ghi qua file tạm cùng thư mục + `os.link` độc quyền rồi mới
  đặt thành đích (không ghi đè do race).
- Chống bom giải nén: số entry + tổng giải nén theo `core.limits.Limits`
  (`E_ARCHIVE_BOMB`); entry mã hóa -> `E_ENCRYPTED`; tên không an toàn /
  symlink / trùng tên -> từ chối rõ (trùng tên bị từ chối vì khôi phục sẽ
  mơ hồ, khác với `ZipVFS` chỉ giữ bản đầu).

## 4. Quyết định kỹ thuật (ghi để coordinator + Joseph xét)

1. **Kiểm sha256 toàn bộ file sau giải nén là "mềm", kiểm từng entry là
   "cứng".** `decompress` bắt buộc từng entry khớp sha256 (sai là lỗi), còn
   sha256 toàn bộ file IPA chỉ BÁO (`byte_identical: true/false`) chứ không
   làm lỗi khi lệch. Lý do: IPA do công cụ khác tạo (vd mức nén DEFLATE khác
   với `zipfile` mức 6) sẽ khác byte ở mức container dù nội dung từng entry
   vẫn khớp sha256 — làm lỗi cứng sẽ khiến `decompress` thất bại vô ích trên
   IPA thật. Đã kiểm chứng: IPA do `zipfile` tạo thì khôi phục **byte-identical
   thật** (cả fixture nhỏ lẫn bench 85 MiB). Nếu Joseph muốn lỗi cứng, chỉ cần
   đổi một chỗ ở cuối `decompress_archive` (raise `E_VERIFY_FAILED` khi
   `not byte_identical`).
2. **`ZipFile.open(zinfo, "w")` streaming vẫn byte-identical** (đã kiểm chứng
   bằng thử nghiệm): khi ghi ra file seekable, `zipfile` tự quay lại vá
   local header, không dùng data descriptor. Nhờ đó `decompress` stream được
   mà không cần đệm entry vào RAM hay file tạm.
3. **Bẫy `tarfile` ở chế độ stream:** `next(iter(tf))` phát lại từ
   `self.members` nên mỗi lần gọi lại trả member đầu (đã bắt được khi test
   fail). Mã dùng `tf.next()` trực tiếp ở mọi chỗ đọc stream. Ghi chú trong
   mã tại `_read_manifest_member`.
4. **Tên thư mục:** `tarfile` lược dấu `/` cuối khi đọc lại (`Data/` ->
   `Data`) nên `decompress` so sánh tên đã chuẩn hóa với entry thư mục.
5. **Tiến độ (luật #152–153):** `progress(done, total)` theo byte thật
   (lượt băm + lượt nén = 2x tổng giải nén; lượt khôi phục = tổng entry);
   service dùng lại mẫu `_ProgressAdapter` của `translate_tool` (Overall khi
   có, `SteadyProgress` khi không), bước từng 1% qua `step_pcts`.
6. **CLI có HAI động từ** (`compress` + `decompress`) nên `ui/handlers.py`
   thêm HAI entry COMMANDS (cùng trỏ `services.compress_tool`); nhiệm vụ ghi
   "thêm 1 entry" được hiểu là 1 entry cho mỗi động từ. Xem mục 6 để nối
   `ui/cli.py` ở bước tích hợp.

## 5. File thuộc nhóm (đã tạo/sửa)

- `sdk/compress.py` (MỚI): `compress_ipa`, `decompress_archive`,
  `load_manifest`, `parse_preset`, `preset_dict_size`,
  `preset_ram_estimate_mb`, `preset_ram_warning`, `CompressReport`,
  `DecompressReport`, `EntryRecord`; hằng `MAX_INPUT_BYTES` (2 GiB),
  `TARGET_BYTES` (400 MiB), `MAX_OUTPUT_BYTES` (500 MiB).
- `services/compress_tool.py` (MỚI): `run_cli`, `add_arguments`,
  `run_interactive`, `cmd_compress`, `cmd_decompress`, `_ProgressAdapter`.
- `tests/test_compress.py` (MỚI): **36 test**, tầng fast, ~1,6 s.
- `tests/fixtures/compress/builder.py` + `__init__.py` (MỚI): builder dựng
  IPA giả (text nén được, blob ngẫu nhiên, STORED, thư mục, IPA rỗng, CRC
  hỏng, entry mã hóa, tên traversal, trùng tên).
- `ui/handlers.py` (CHỈ thêm 2 dòng COMMANDS `compress`/`decompress`, neo
  sau dòng `"kb"`; không sửa MENU hay mục khác).

## 6. Việc còn lại cho coordinator (bước tích hợp — ngoài phạm vi nhóm)

`ui/cli.py` chưa có subparser cho 2 lệnh mới (nhóm không được sửa file này):
cần thêm, ngay sau khối `kb`:

```python
s = sub.add_parser("compress", help="nén IPA kiểu WinRAR (solid LZMA)")
s.add_argument("path")
s = sub.add_parser("decompress", help="khôi phục IPA từ archive nén PB3")
s.add_argument("path")
```

(`services/compress_tool.add_arguments` đã thêm `--out`/`--force`/`--preset`
qua hook có sẵn trong `build_parser`; `dispatch` đã dùng
`handlers.COMMANDS[cmd]` nên không cần sửa gì thêm.) Sau khi thêm, kiểm thử
bằng lệnh thật:

```sh
python3 menu.py compress tests/fixtures/zip/sample.ipa
python3 menu.py decompress workspace/output/sample.tar.xz
```

Chưa có test subprocess CLI cho 2 lệnh này vì parser chưa nhận lệnh (test
CLI hiện chạy `run_cli` trực tiếp + `add_arguments` trên subparser tự tạo).
Menu tương tác (mục MENU) do coordinator nối.

## 7. Kiểm chứng (cập nhật 2026-11-16 04:54, theo điều phối: KHÔNG chạy full
suite nữa — coordinator chạy ở bước tích hợp)

- `python3 -m unittest tests.test_compress`: **36 test OK** (chạy lại lần cuối
  04:54: 3,8 s).
- `python3 run_tests.py --tier fast` (nhóm tự chạy lần cuối 04:56, TMPDIR
  riêng ngoài `/tmp` để khỏi đè tmpfs): **1371 test trong 66,3 s, 0 lỗi,
  2 fail**. Cả 2 fail đều ngoài phạm vi nhóm nén:
  (1) `test_unimplemented_shows_clear_status_and_changes_nothing` (xem trên);
  (2) `tests.test_font.TestScan.test_scan_dir` (`AssertionError: 3 != 1`) —
  test đếm của nhóm font, nhóm nén không chạm tới font; để nhóm font +
  coordinator xử lý ở bước hợp nhất.
- Full suite: nhóm đã chạy 1 lần nhưng KHÔNG có kết quả sạch để báo — 5 nhóm
  chạy song song + `/tmp` tmpfs 512 MiB đầy (`Errno 28`, xem dưới) làm hàng
  trăm test ghi file lỗi giả; theo điều phối, nhóm đã DỪNG full suite đang
  chạy dở (kill lúc 73%, không có tóm tắt) và không chạy lại. Coordinator
  chạy full suite + mô phỏng iSH ở bước tích hợp.
- Thử thật trên fixture: `compress -> decompress -> sha256` byte-identical
  (IPA mẫu 7 entry, IPA rỗng, IPA 5 MiB 1 entry, IPA ngẫu nhiên, bench
  85 MiB preset 8). Luật từ chối đã thử bằng cap nhỏ (>2 GiB mock,
  output >500 MiB mock, trượt đích 400 MiB).

### Lưu ý về nhiễu khi chạy suite song song (để coordinator biết)

- `/tmp` là tmpfs chỉ 512 MiB: 5 nhóm chạy suite cùng lúc làm đầy đĩa
  (`OSError: [Errno 28] No space left on device`), gây lỗi hàng loạt ở test
  ghi file — đó là lỗi đĩa, KHÔNG phải lỗi mã. Nhóm đã dọn file bench của
  mình (`bench.ipa` 82 MiB + `bench_restored.ipa` 82 MiB + 4 archive preset
  ~20 MiB + thư mục smoke) và coordinator đã dọn `pb3test-*` cũ; `/tmp` về
  74% (138 MiB trống). File bench đã xóa nên số đo preset ở mục 2 chỉ còn
  trong handoff này (không chạy lại được từ `/tmp/bench.ipa`).
- Hai lần chạy fast/full chồng nhau còn làm một lần fast bị cắt ngang ở 72%
  và 27 lỗi `TestLoader`/`TestExtraction` giả — chạy lại sạch thì hết
  (lỗi=0). Kết luận: chỉ tin kết quả suite khi chạy MỘT MÌNH, không chồng
  chéo với nhóm khác.

## 8. Giới hạn trung thực

- Tỉ lệ nén thực tế trên IPA game thật (texture/âm thanh đã nén) gần 1:1;
  đừng hứa 5:1. Archive quá 500 MiB thì từ chối thay vì tạo file lớn.
- Khôi phục byte-identical chỉ đảm bảo với IPA do `zipfile` (mức DEFLATE
  mặc định) tạo ra; IPA do công cụ khác tạo có thể lệch byte container
  (nội dung từng entry vẫn khớp sha256) — mã báo rõ, không giấu.
- Entry BZIP2/LZMA trong ZIP: ghi lại được qua `zipfile` (đã map method),
  nhưng chưa có fixture round-trip riêng cho 2 method này.
- `decompress` chỉ đọc archive do chính PB3 tạo (manifest `pb3-solid-xz`
  v1); tar.xz thường bị từ chối rõ, không đoán.
- Chưa đo trên iSH/iPhone thật: preset mặc định 6 dựa trên RSS đo được
  (113 MiB) + dư địa RAM iPhone 11 Pro Max 4 GiB; cần Joseph đo lại trên máy
  khi nén IPA thật (đặc biệt preset 8/9).
