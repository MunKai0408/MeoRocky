# Mục phân tích C8 — Format probe cho engine lạ (BearTea web)

Ngày: 2026-11-16. Repo MunKai0408/MeoRocky, head main trước C8 là a12eac9
(đã có C7 patch pack, C6 font fallback, C5 Zstd/v9, C4 OPFS, C3 PWA, C2
chẩn đoán, C1 fallback dịch, mục 8 plan JSON). Harness:
`exp/verify_analysis_c8.mjs` — **22/22 PASS**. Full suite Python:
**1657 test, OK (skipped=2)**. Hồi quy: module-worker 17/17, C2 13/13.

## 1. Hành vi CŨ trước khi sửa (đã kiểm chứng, không đoán)

Với file engine lạ / không nhận ra, bản web chỉ báo một dòng lỗi, không
có báo cáo nào:

- `list_entries(c8_unknown.bin)` (byte ngẫu nhiên 8KB) →
  `ok:false, code=E_UNSUPPORTED_FORMAT`,
  `error="E_UNSUPPORTED_FORMAT: Đã nhận diện nhưng chưa có plugin parse
  được."` (harness C0 — hành vi này vẫn giữ nguyên cho op cũ).
- Tương tự cho Extract/Inspect: banner đỏ `"<Tên thao tác> lỗi: ..."` rồi
  đứng im; user không biết file là engine gì, thao tác nào được/không
  được, làm gì tiếp theo.
- Ô chọn file chính còn chặn luôn đuôi engine lạ: `gameExtOk` chỉ nhận
  `.ipa/.zip/.unity3d/.assets/.ttf/.json/.tar.xz/.gz` nên file `.pck`,
  `.pak`, `.locres`, `.win`, `.translation` không chọn được từ đầu
  (không sửa `GAME_EXTS` vì harness phase1_ci kiểm tra đúng từng ký tự
  mảng này — probe dùng ô chọn file riêng, xem §2).

## 2. Khảo sát trước khi làm (đọc code + test, không đoán)

Nhận diện dùng một nguồn sự thật duy nhất `sdk/discovery.py`:
`engine_signals()` / `detect_engine()` / `engine_level()` cho 6 engine
(unity, godot, unreal, cocos, defold, gamemaker). Magic đã xác minh:
UnityFS, GDPC (Godot PCK), LOCRES 16 byte (Unreal Compact+), FORM+GEN8
(GameMaker data.win). Cocos/Defold KHÔNG có magic đầu file đã xác minh
nên tối đa CÓ KHẢ NĂNG. Một dấu hiệu tên mạnh = 0.3 < 0.5 nên chưa đủ
bằng chứng (vd 1 file `.locres` Legacy đơn lẻ, hay 1 file
`resources.assets` đơn lẻ, đều là "unknown" theo đúng luật này —
`tests/test_engine_detect.py::test_unreal_legacy_locres_name_only`).

| Plugin | kind / maturity | Làm được thật (theo code + test) |
|---|---|---|
| godot_pck | container / M1 | Mở/lấy entry PCK v1/v2, chỉ đọc, entry không nén/không mã hoá. Test: `test_godot.py` 27. Chưa có PCK game thật. |
| godot_translation | format / M2 | Đọc `.translation` layout "TR" (chỉ đọc). Layout này theo nhiệm vụ, CHƯA đối chiếu file Godot thật (file thật dạng RSRC → báo E_UNSUPPORTED_FORMAT). Test: `test_godot_translation.py` 19. |
| unreal_pak | container / M1 | Mở/lấy entry PAK UE4/UE5, chỉ entry không nén/không mã hoá; magic ở footer cuối file. Test: `test_unreal.py` 38. Chưa có PAK game thật. |
| unreal_locres | format / M2 | Đọc `.locres` v0–v3 (chỉ đọc); `.locmeta` nhị phân chưa có parser. Test: `test_unreal_locres.py` 23. |
| cocos_detect | engine / M2 | CHỈ nhận diện qua tên entry (project.json, main.js, .ccb, .fire). Không mở container, không trích xuất. |
| defold_detect | engine / M2 | CHỈ nhận diện qua tên entry (game.project, .archive, .collection, .atlas). Không mở container, không trích xuất. |
| gamemaker_detect | engine / M2 | CHỈ nhận diện: magic FORM+GEN8 trong data.win + tên .yyp/.yy/.gml. Không mở data.win, không trích xuất. |
| (Unity, đối chứng) | — | Inspect/trích xuất/dịch/mod đầy đủ; đã kiểm chứng trên IPA thật 9 Kings. |

Tất cả plugin engine lạ đều chỉ kiểm chứng trên fixture tự dựng, chưa có
file game thật nào. Ma trận `sdk/support_matrix.py` cũng ghi rõ:
"Cocos/Defold/GameMaker: chỉ nhận diện, chưa trích xuất".

## 3. Thay đổi

- **Mới `pb3/sdk/format_probe.py`** (stdlib-only, Python 3.8+): hàm
  `probe_data(data, name, active_plugins)` trả báo cáo schema
  `pb3-format-probe` v1: `file` (tên, kích thước, SHA-256, hex 64 byte
  đầu, container_kind, magic đầu file, có footer PAK không, có mở được
  container không), `engine` (id/label/mức/confidence/evidence/note),
  `candidates` (mọi engine có bằng chứng), `entries` (tổng + mẫu 20),
  `open_error` (nếu không mở được container — vd 7z), `supported` /
  `unsupported` (kèm lý do), `next_steps`, `strings` (quét ASCII 256KB
  đầu), `plugins_checked`, `notes`.
  - Nhận diện dùng `sdk.discovery` + 2 bằng chứng thêm của probe, ghi
    rõ trong từng reason: (1) magic footer PAK (discovery chỉ xét mẫu
    đầu file; footer này cũng là thứ `PakVFS` xác minh khi mở);
    (2) magic "TR" ở đầu file `.translation` (định dạng theo nhiệm vụ,
    chưa đối chiếu file Godot thật — file RSRC vẫn báo unknown).
  - Mức hỗ trợ lấy từ `ENGINE_SUPPORT` (diễn đạt lại docs/maturity của
    manifest, không tuyên bố thêm) + hạ cấp theo plugin đang bật: thao
    tác nào cần plugin chưa bật thì rơi xuống unsupported kèm lý do
    "plugin X chưa bật trong phiên này".
  - File lạ → `engine="unknown"`, `supported=[]`, vẫn là probe THÀNH
    CÔNG (ok:true) — không raise, không PASS giả.
- **Mới `pb3/tests/test_format_probe.py`** (22 test, xem §4).
- **`pb3/bridge.py`**: thêm op `format_probe(path)` — đọc file, truyền
  plugin đang bật vào probe, trả `{"ok": true, "report": ...}`. Chỉ lỗi
  đọc file / lỗi hệ thống mới trả ok:false.
- **UI (tab Chẩn đoán, `index.html` + `app.js`)**: mục "Probe định dạng
  (engine lạ — mục C8)" với ô chọn file riêng `#probe-file-input`
  (nhận mọi đuôi, KHÔNG qua `gameExtOk`), nút `#btn-probe-run`, nút
  `#btn-probe-download` ("Tải báo cáo JSON", tải
  `beartea-probe-<tên file>.json`), thẻ kết quả `#probe-result`
  (engine + mức + bằng chứng, ứng viên khác, Hỗ trợ được / Chưa hỗ trợ
  kèm lý do, Bước tiếp theo, ghi chú). Không chọn file riêng thì probe
  dùng file đã chọn ở trang chính. Báo cáo gần nhất lưu ở
  `window.__PB3_LAST_PROBE` (cho harness/debug).
  - Gợi ý khi thao tác cũ lỗi: `runOp` gặp `E_UNSUPPORTED_FORMAT` /
    "không nhận diện được container" thì banner lỗi thêm dòng
    "Gợi ý: mở tab Chẩn đoán → bấm “Probe định dạng” để xem báo cáo
    chi tiết về file này…".
  - Mọi chữ tiếng Việt mới đều đủ dấu. Không dùng COI.
- **Manifest + cache**: `pb3/files.json` 90→91 (+`sdk/format_probe.py`),
  `pb3/tests.json` 159→160 (+`tests/test_format_probe.py`), `sw.js`
  precache +`sdk/format_probe.py`, `CACHE_VERSION` v12→**v13-20261116**.
- **Harness**: `exp/verify_analysis_c8.mjs` (22 mục),
  `exp/check_analysis_c8_static.py`, `exp/build_c8_fixtures.py` +
  11 fixture nhỏ trong `exp/fixtures/c8_*` (tổng ~15KB, đều tổng hợp
  từ builder/magic thật — ghi rõ trong từng mục; không có file game thật).

## 4. Kết quả kiểm chứng

### Harness C8: 22/22 PASS (`analysis_c8_results.json`)

| Mục | Kết quả |
|---|---|
| static_node_py_check | PASS — node --check app.js/sw.js; py_compile bridge.py, format_probe.py, test_format_probe.py |
| static_c8_checks | PASS — stdlib-only + Python 3.9, không gọi mạng, ENGINE_SUPPORT trung thực, manifest 91/160, sw.js v13, bridge + UI đủ |
| python_unit_format_probe_engine_detect | PASS — 57 test OK (22 probe + 35 engine_detect) |
| C0 baseline (hành vi cũ) | PASS — list_entries(file lạ) → ok:false E_UNSUPPORTED_FORMAT (chỉ dòng lỗi, không báo cáo) |
| C1 Godot PCK v2 | PASS — engine godot, CHẮC CHẮN, conf=1.0; hỗ trợ: mở PCK v1/v2; chưa hỗ trợ: trích xuất text game Godot |
| C2 Godot .translation TR | PASS — godot, CÓ KHẢ NĂNG, conf=0.6; hỗ trợ: đọc .translation "TR"; chưa hỗ trợ: file RSRC |
| C3 Unreal PAK v8 | PASS — unreal, CHẮC CHẮN (qua footer PAK), conf=1.0, mở được container, 4 entry; chưa hỗ trợ: entry nén/mã hoá/Oodle |
| C4 Unreal .locres v3 | PASS — unreal, CHẮC CHẮN, conf=1.0 |
| C5 GameMaker data.win | PASS — gamemaker, CHẮC CHẮN; supported CHỈ có 2 mục "Nhận diện" |
| C6 Cocos ZIP | PASS — cocos, CÓ KHẢ NĂNG, conf=0.9; chỉ nhận diện; chưa hỗ trợ: "chưa có backend Cocos" |
| C7 Defold ZIP | PASS — defold, CÓ KHẢ NĂNG, conf=1.0; chỉ nhận diện; chưa hỗ trợ: "chưa có backend Defold" |
| C8 Byte ngẫu nhiên | PASS — ok:true, engine unknown, supported=[], unsupported + next_steps đủ, SHA-256 64 ký tự |
| C9 ZIP thường (không phải game) | PASS — unknown, mở được container, 2 entry, supported=[] |
| C10 UnityFS head | PASS — unity, CHẮC CHẮN, conf=1.0; hỗ trợ trích xuất |
| browser_boot_ready | PASS — "Sẵn sàng — 20 plugin đang chạy · font đi kèm: đủ 142/142" |
| browser_probe_godot_pck | PASS — thẻ hiện Godot + CHẮC CHẮN + "Hỗ trợ được (pck v1/v2)" |
| browser_probe_unknown_structured_download | PASS — thẻ "Chưa rõ / CHƯA ĐỦ BẰNG CHỨNG / Không có thao tác nào được hỗ trợ"; tải `beartea-probe-c8_unknown.bin.json` (1901B), đủ 11 trường schema, engine=unknown, supported=[] |
| browser_probe_unreal_pak | PASS — thẻ Unreal + CHẮC CHẮN + ".pak UE4/UE5" |
| browser_probe_main_file_cocos | PASS — không chọn file riêng → probe file chính c8_cocos.zip → Cocos CÓ KHẢ NĂNG |
| browser_list_unknown_suggests_probe | PASS — Liệt kê file .gz lạ → banner lỗi kèm gợi ý "Probe định dạng" + "tab Chẩn đoán" |
| browser_390_no_overflow_no_errors | PASS — scrollWidth=390 = clientWidth; không pageerror/console-error |
| shell_size_after_c8 | raw **356.866B (348,5KB)**, gzip **103.681B (101,3KB)** — xem §5 |

Trong lúc chạy harness có 2 FAIL thoáng qua ở C6/C7 do assertion của
harness chỉ xét trường `op` mà lý do "chưa có backend Cocos/Defold" nằm
ở trường `reason` — lỗi harness, không phải lỗi probe; đã sửa harness
xét cả `op + reason` rồi chạy lại: 22/22 xanh.

### Unit test Python (riêng)

- `tests.test_format_probe`: **22/22 OK** — gồm cả trường hợp biên trung
  thực: `.translation` RSRC → unknown (không nhận bừa); `FORM` không có
  `GEN8` → không CHẮC CHẮN; `.locres` Legacy 1 tên → unknown nhưng hiện
  ứng viên unreal conf 0.3; `resources.assets` đơn lẻ → unknown nhưng
  hiện ứng viên unity conf 0.3; plugin chưa bật → thao tác rơi xuống
  unsupported ("plugin godot_pck chưa bật trong phiên này"); file rỗng;
  7z (open_error rõ ràng, vẫn trả báo cáo).
- Full suite Python (`unittest discover`): **1657 test, OK
  (skipped=2)** trong 55,3s (trước C8: 1633 passed + 2 skipped theo
  báo cáo C6–C7; +22 test probe mới + 2 test chênh do cách đếm
  discover — không có FAIL mới).

### Hồi quy

- `verify_module_worker.mjs`: **17/17 PASS** (lần chạy đầu FAIL 1 mục
  `write_site_files` vì harness hardcode `files.json == 90` — C8 thêm
  file thứ 91 hợp lệ; đã cập nhật kỳ vọng lên 91 kèm chú thích, chạy
  lại xanh).
- `verify_analysis_c2.mjs`: **13/13 PASS**.
- C6–C7 trước đó: C6 10/10, C7 13/13 (theo MEMORY.md 20:22).

## 5. Bảng mức hỗ trợ THẬT từng engine (kết luận của probe)

| Engine | Probe nhận diện | Mức hỗ trợ thật trên web |
|---|---|---|
| Unity | CHẮC CHẮN (magic UnityFS) / CÓ KHẢ NĂNG (tên) | Đầy đủ: List, Inspect, trích xuất (TextAsset + MonoBehaviour), dịch, Mod dry-run + patch pack. Chưa: UnityFS v9 chỉ đọc, bundle mã hoá. Đã kiểm chứng trên IPA thật 9 Kings. |
| Godot | CHẮC CHẮN (GDPC / PCK) | Mở/lấy entry PCK v1/v2 (chỉ đọc, không nén/không mã hoá); đọc `.translation` dạng "TR" (chỉ đọc). Chưa: trích xuất text game, PCK v0/v3+/mã hoá/nhúng, `.translation` RSRC thật, mod PCK. Chỉ fixture, chưa có game thật. |
| Unreal | CHẮC CHẮN (magic LOCRES / footer PAK) | Mở/lấy entry PAK UE4/UE5 (chỉ không nén/không mã hoá, chỉ đọc); đọc `.locres` v0–v3 (chỉ đọc). Chưa: trích xuất text game, entry nén/mã hoá/Oodle, `.locmeta` nhị phân, mod PAK. Chỉ fixture, chưa có game thật. |
| Cocos | Tối đa CÓ KHẢ NĂNG (chỉ tên entry, không có magic) | CHỈ NHẬN DIỆN. Chưa: mở container, trích xuất, dịch, mod. Chỉ fixture. |
| Defold | Tối đa CÓ KHẢ NĂNG (chỉ tên entry, không có magic) | CHỈ NHẬN DIỆN. Chưa: mở container, trích xuất, dịch, mod. Chỉ fixture. |
| GameMaker | CHẮC CHẮN (FORM+GEN8 trong data.win) | CHỈ NHẬN DIỆN. Chưa: mở data.win, trích xuất, dịch, mod. Chỉ fixture. |
| File lạ hoàn toàn | CHƯA ĐỦ BẰNG CHỨNG (unknown) | Không có thao tác nào. Báo cáo ghi rõ những gì đã đo (kích thước, SHA-256, magic, container) + bước tiếp theo (gửi file mẫu). |

## 6. Điểm chưa đạt / việc còn lại

1. **Vỏ trang raw 348,5KB > 100KB** (FAIL item6 đã biết, không phải do
   C8 gây ra; tổng raw tăng từ ~347KB lúc C6–C7 lên 356.866B sau C8,
   trong đó app.js hiện 211.161B raw). Ghi nhận như các mục trước, chưa sửa.
2. **Chưa có file game thật nào cho engine lạ**: mọi kết luận Godot /
   Unreal / Cocos / Defold / GameMaker đều trên fixture tổng hợp từ
   builder/magic thật. Cần Joseph gửi game thật (đặc biệt file `.pck` /
   `.pak` / `.translation` RSRC thật) để kiểm chứng lại.
3. **Một dấu hiệu tên mạnh đơn lẻ chưa đủ bằng chứng** (luật discovery):
   vd 1 file `.locres` Legacy, 1 file `resources.assets`, 1 file `.yyp`
   đơn lẻ → probe báo unknown nhưng hiện ứng viên gần nhất (conf 0.3)
   kèm bằng chứng — đúng luật, nhưng user có thể thấy "unknown" dù tên
   file rất gợi ý. Cải tiến sau (nếu Joseph đồng ý): nới luật riêng cho
   probe, không sửa discovery chung.
4. **Harness cũ còn hardcode số file 90/159** (`verify_limit_2gb.mjs`,
   `verify_patch01_web.mjs`, `verify_phase1_ci.mjs`,
   `check_item8_static.py`, `check_analysis_c6_static.py`) — sẽ FAIL
   phần đếm file khi chạy lại sau C8 (không phải lỗi sản phẩm; chỉ cần
   cập nhật kỳ vọng lên 91/160 như đã làm cho `verify_module_worker.mjs`
   trong đợt này).
5. Probe chưa quét chuỗi UTF-16 (chỉ ASCII, 256KB đầu) và chưa xét entry
   lồng sâu quá 1 cấp container — đủ cho báo cáo định dạng, chưa phải
   chẩn đoán sâu.
