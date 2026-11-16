# TÀI KHOẢN CLAUDE 08 — NHÓM GODOT (PCK + .translation)

Trạng thái: **SANDBOX DONE (2026-11-16)** — sandbox full 1189 test: 1187
đạt, 1 fail DỰ KIẾN (test registry, xem §3), 1 skipped, 0 lỗi, 50,5 s.
iSH NOT RUN. Chỉ làm phần Godot của prompt 08 §1; không đụng Unreal /
Cocos / Defold / GameMaker, không sửa `sdk/containers.py`,
`tests/test_optimize.py`, `test_registry.py`, không build zip.

## 1. Đã làm

### `sdk/godot.py` — `PckVFS(VFS)` thuần Python, chỉ đọc
- Mở PCK từ đường dẫn hoặc bytes (như ZipVFS). Header LE: magic "GDPC",
  pack_version, godot major/minor/patch, v2+: flags + file_base, reserved
  64 byte. Thư mục ngay sau header: count u32 + mỗi entry (path_len u32
  đã căn 4 + path UTF-8 đệm NUL, offset u64, size u64, md5 16 byte,
  v2+: flags u32).
- v1 (Godot 3): offset tuyệt đối. v2 (Godot 4): tuyệt đối = file_base +
  offset (file_base = đầu vùng dữ liệu, căn 32). Đường dẫn "res://..."
  bỏ tiền tố thành đường dẫn ảo tương đối.
- Chỉ entry stored, không mã hóa: entry mã hóa / đã xóa / cờ lạ bị loại
  khỏi VFS + `inventory.rejected` kèm lý do; đọc entry mã hóa -> E_ENCRYPTED.
  Thư mục mã hóa (PACK_DIR_ENCRYPTED) -> E_ENCRYPTED khi mở.
  pack_version 0/3+ -> E_UNSUPPORTED_FORMAT; magic sai -> E_BAD_ARCHIVE;
  cắt cụt -> E_CORRUPT. Giới hạn Limits (số entry, tổng byte ->
  E_ARCHIVE_BOMB). MD5 kiểm khi đọc nguyên entry (md5 toàn 0 thì bỏ qua);
  slice/tiền tố không kiểm (như ZipVFS, crc_verified=False).
- Không sửa `sdk/containers.py`: dispatch GDPC -> PckVFS và dòng
  support-matrix Godot đã có sẵn từ đợt 1, nay thành thật.

### `plugins/godot_pck/plugin.py` — container, M1, list/read
- id "godot_pck", extensions ".pck" + magic GDPC, engines ["godot"],
  manifest đầy đủ (api_version 3, priority 10, true_cost cheap, limits
  16 MiB / 10 s, docs tiếng Việt). Hook detect / list_candidates / probe /
  open_container / selftest (selftest dựng PCK v2 nhỏ trong RAM bằng
  builder tối thiểu của riêng plugin, đọc lại "hello pb3").

### `plugins/godot_translation/plugin.py` — format CHỈ ĐỌC, M2
- Kế thừa `FormatPluginBase`, KHÔNG viết lại parser: `_parse_bytes` gọi
  `parse_translation_bytes` chung (dùng cho cả plugin lẫn test).
  Layout .translation theo nhiệm vụ: magic "TR", version u32 LE (= 1),
  locale (u32 độ dài + UTF-8), count u32, mỗi entry msgid_len + msgid
  UTF-8, msgstr_len + msgstr UTF-8 -> LocTable (key=msgid, value=msgstr,
  locale lấy từ file, fallback ctx.locale).
- `parse()` ghi đè lớp nền để locale bảng lấy từ file (lớp nền dùng
  ctx.locale.code, thường None) — như sqlite_loc. build/verify ->
  E_WRITE_DISABLED (capabilities ["read"], như sqlite_loc).
- File RSRC (binary resource / OptimizedTranslation thật của Godot) ->
  E_UNSUPPORTED_FORMAT rõ ràng thay vì đọc sai (xem §4).

### Fixture bằng builder xác định — `tests/fixtures/godot/builder.py`
- `build_pck_v1` / `build_pck_v2` / `build_pck` (cờ entry, cờ pack),
  `build_translation(pairs, locale)`, `deterministic_bytes(n, seed)` bằng
  `random.Random` — KHÔNG dùng `os.urandom` (decisions #143), có test khóa.
  Không commit file nhị phân lạ; test dựng fixture trong RAM/tmp.

### Test mới — 46 test (1143 -> 1189)
| File | Số test |
|---|---|
| tests/test_godot.py (v1/v2 list/read/slice/stat/inventory, entry mã hóa/xóa/cờ lạ, header hỏng, limits, plugin, conformance, CLI thật) | 25 |
| tests/test_godot_translation.py (parse đúng, locale, trùng key, file hỏng/RSRC/version, ghi bị từ chối, plugin, conformance, CLI thật) | 21 |
- `plugin list`: 15 plugin [ok] (13 cũ + 2 mới: godot_pck M1 container,
  godot_translation M2 format). `plugin test godot_pck` và
  `plugin test godot_translation`: PASS (rc 0; nhãn "CHƯA KIỂM DỮ LIỆU"
  vì chưa có thư mục fixture trên đĩa — conformance có dữ liệu đã PASS
  trong bộ nhớ ở test từng plugin).

## 2. Bằng chứng sandbox (Python 3.12.3, 2026-11-16)
- `python3 -m unittest tests.test_godot tests.test_godot_translation`:
  `Ran 46 tests OK`.
- `python3 run_tests.py --tier full`: 1189 test, 1187 đạt, 1 fail (dự
  kiến, §3), 1 skipped, 0 lỗi, 50,5 s, Timeout 0, chậm nhất 2,8 s.
- Kiểm AST `feature_version=(3, 8)` cho mọi file mới: đạt (Python 3.9).
  Không thêm dependency, không gọi mạng trong code, không có câu
  `import UnityPy` / `import lz4`.

## 3. Việc cho parent lúc tích hợp (ĐÃ DỰ KIẾN, không phải lỗi nhóm)
1. **`tests/test_registry.py::test_real_project_has_first_builtin_plugin_zip`
   FAIL** — kỳ vọng cũ đúng 13 id, nay có thêm godot_pck +
   godot_translation (15 id). Cập nhật như Claude 06 đã làm ở decisions
   #125: assert đủ 15 id, godot_pck ok/container/M1, godot_translation
   ok/format/M2.
2. **`tests/test_container.py::test_open_vfs_godot_pak_without_backend_is_clean_error`
   vẫn PASS (tình cờ)** — fixture GDPC của nó có pack_version 0 (byte
   version toàn 0) nên vẫn ra E_UNSUPPORTED_FORMAT, và message_vi của mã
   đó chứa "chưa có". Ý định của test đã lỗi thời (backend Godot nay đã
   có); parent nên thay nó bằng test mở PCK thật như chú thích trong test
   đã dặn. Nhóm đã có test thay thế: `TestPckV2.test_open_vfs_dispatch_gdpc`
   trong tests/test_godot.py (dispatch GDPC -> PckVFS, đọc đúng entry).
   Phần PAK của test khóa vẫn đúng (Unreal chưa có backend).
3. Không cập nhật `handoff/decisions.md` (tránh xung đột với các nhóm
   Unreal/Cocos đang chạy song song): parent gộp các quyết định dưới đây
   vào decisions.md lúc tích hợp.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Chỉ hỗ trợ PCK v1/v2 + entry stored, có fixture. Chưa hỗ trợ: v0/v3
  (Godot 4.5+), PCK nhúng trong file thực thi, ghi lại PCK, giải mã.
  PCK không có cờ nén entry trong đặc tả — entry mang cờ lạ bị loại.
- Chưa kiểm chứng trên PCK của game Godot thật (chưa có file thật):
  fixture tự dựng theo tài liệu GodotPckTool / Godot file_access_pack.
- Layout .translation "TR" v1 là định dạng theo nhiệm vụ Claude 08;
  tra cứu công khai không xác nhận được magic này đối chiếu với file do
  Godot thật xuất ra — file .translation thật là binary resource
  (RSRC / OptimizedTranslation, bảng băm + chuỗi nén, không lưu msgid)
  nên plugin báo E_UNSUPPORTED_FORMAT rõ ràng khi gặp RSRC. Cần đối
  chiếu với file .translation thật của game trước khi tuyên bố hỗ trợ
  đầy đủ (việc cho đợt tổng cuối / Claude 09-10).
- Không hỗ trợ context/plural trong .translation (msgid có context nối
  bằng U+0004 thì key giữ nguyên cả chuỗi).

## 5. Quyết định đề xuất ghi vào decisions.md (parent gộp)
- **Godot PCK v1/v2 stored-only**: `sdk/godot.py::PckVFS`, offset v1
  tuyệt đối / v2 tương đối theo file_base, đường dẫn res:// bỏ tiền tố,
  entry mã hóa/xóa/cờ lạ loại + rejected, MD5 chỉ kiểm khi đọc nguyên
  entry. Chỉ tuyên bố biến thể có fixture (v1+v2, stored).
- **.translation "TR" v1 chỉ đọc (M2)**: parser dùng chung
  `parse_translation_bytes`, locale bảng lấy từ file, RSRC ->
  E_UNSUPPORTED_FORMAT; build/verify E_WRITE_DISABLED như sqlite_loc.
- **Test khóa cũ test_open_vfs_godot_pak_without_backend_is_clean_error**:
  phần GDPC lỗi thời sau khi backend có (vẫn PASS tình cờ vì fixture
  version 0); parent thay bằng test mở PCK thật lúc tích hợp.
