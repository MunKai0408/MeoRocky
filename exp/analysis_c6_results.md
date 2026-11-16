# Mục C6 — Font fallback TMP (kết quả)

Ngày kiểm chứng: 2026-11-16. Repo: MunKai0408/MeoRocky, nhánh main.
Head lúc làm: 8446141 (đã có C5 Zstd/v9, C4 OPFS, C3 PWA/prefetch, C2 chẩn
đoán, C1 fallback dịch, mục 8, mục 7). KHÔNG đẩy lên GitHub — coordinator
đẩy sau khi kiểm chứng.

Lưu ý tên gọi: báo cáo mục 7 gọi công việc này là "C7 (làm sau)"; nhiệm
vụ giao gọi là **C6** — cùng một việc (tạo TMP_FontAsset mới chỉ chứa dấu
tiếng Việt làm fallback cho TMP_Settings). Ghi chú trong `sdk/tmp_font.py`
đã đổi theo tên C6 cho khỏi lẫn.

## 0. Tóm tắt nhanh

| Hạng mục | Kết quả |
|---|---|
| Harness mục C6 (`exp/verify_analysis_c6.mjs`) | **10/10 PASS, 0 FAIL** |
| Kiểm tra tĩnh C6 (`exp/check_analysis_c6_static.py`) | **0 lỗi** (schema, C# khớp Python 134+8, đăng ký fallback, KHÔNG sửa asset gốc) |
| Kế hoạch fallback trên fixture studio (`exp/check_c6_fallback_plan.py`) | **0 lệch**: IPA 15 fallback / 4 unplanned, missing_total 142 = glyph plan mục 7; DATA WING thật 106 thiếu → 1 fallback |
| Unit test Python mới (`tests/test_tmp_fallback.py` + `test_tmp_font`) | **23 test, OK** (6 mới + 17 mục 7) |
| Full suite Python (`unittest discover`) | **1616 test: 1 FAIL có từ trước** (`test_rules_ish_only`, xem §7) + 2 skipped |
| Hồi quy | module-worker **17/17**, C2 **13/13**, item7 **11/11**, item8 **16/16**, item6 **15/16** (chỉ FAIL đã biết `shell_raw_under_100kb`, raw mới 347.244B), phase1_ci **7/8** (FAIL `markers_count` có từ trước do C5, xem §7) |
| Trình duyệt thật 390px | boot "Sẵn sàng — 20 plugin", tab Font → Đọc TMP 134/134 → Tạo font fallback: hiện 13 fallback, tải `tmp_fallback_plan.json` đúng (schema, 142 ký tự, SourceCodePro thiếu 0 chữ + 8 dấu), script C# phục vụ qua HTTP 200, 390px không tràn ngang, không pageerror |

## 1. Ranh giới web vs Unity Editor — đọc trước tiên

Font fallback vốn là việc của **Unity Editor, KHÔNG phải việc chạy trong
trình duyệt**. Mục C6 làm đúng phần thuộc về mỗi bên:

**Web làm được (đã làm + đã kiểm chứng):**
- Đọc TMP_FontAsset thật (dùng lại code mục 7) rồi lập **kế hoạch
  fallback** (`tmp_fallback_plan.json`, schema `tmp_fallback_plan/1`):
  với từng asset gốc còn thiếu chữ Việt — tên asset fallback mới
  (`<Tên font> Vietnamese Fallback`), đúng chữ thiếu (chữ cái + dấu kết
  hợp, hợp nhất toàn bộ 142), chuỗi `chars_text`, tham số atlas gốc cần
  giữ (PointSize, Scale, Padding, kích thước atlas + cơ sở, LineHeight),
  kích thước atlas fallback (atlas gốc kẹp trong [256, 1024] vì chỉ chứa
  ≤142 ký tự), tham chiếu Material/Texture/Font gốc (chỉ để đối chiếu),
  5 bước Editor cho từng target + 5 bước chung.
- Nút "Tạo font fallback" trong tab Font (nối với nút đọc TMP đã có):
  dùng lại kết quả quét TMP lần trước nếu cùng file (cache theo
  đường dẫn + kích thước + mtime trong bridge) nên không quét lại cả
  IPA; nếu chưa đọc TMP thì tự quét mới.
- Link tải script C# kèm theo ngay trong kết quả (phục vụ từ chính trang
  web: `unity_editor/TMP_VietnameseFallback.cs`).

**Web KHÔNG làm được (ghi rõ, không giả vờ):**
- Tạo TMP_FontAsset thật có atlas SDF dùng được ngay: cần FontEngine
  trong Unity Editor. Kế hoạch JSON không phải asset dùng được ngay.
- Sinh atlas SDF bằng Canvas/msdf-atlas-gen: không dùng, không tuyên bố.
- Đọc render mode từ FaceInfo (không nằm trong FaceInfo): kế hoạch ghi
  `base_render_mode: null` + ghi chú script đọc lại trong Editor
  (`fontAsset.atlasRenderMode`) và giữ nguyên.

**Unity Editor làm (script kèm theo `site/unity_editor/TMP_VietnameseFallback.cs`):**
- Menu BearTea → TMP → "4. Tạo font fallback tiếng Việt (từ file
  tmp_fallback_plan.json)": chọn asset gốc → chọn file plan → tạo asset
  mới `Assets/BearTea/<Tên font> Vietnamese Fallback.asset` CHỈ chứa chữ
  thiếu, CÙNG PointSize / Padding / render mode với asset gốc; font
  nguồn lấy từ `creationSettings.sourceFontFile` của asset gốc, nếu mất
  thì hỏi người dùng chọn file .ttf/.otf trong thư mục Assets.
- Đăng ký fallback vào **2 nơi**: danh sách fallback chung của
  `TMP_Settings` (`TMP_Settings.instance.fallbackFontAssets`) + bảng
  fallback riêng của asset gốc (`fontAsset.fallbackFontAssetTable`).
  Cả hai chỉ THÊM tham chiếu.
- **KHÔNG sửa asset gốc**: không gán lại `atlasTexture`, `glyphTable`,
  `characterTable`, `faceInfo` hay `material` của nó — chỉ đọc tham số.
  Material gốc giữ nguyên 100%.
- Menu "5. Tạo font fallback (đủ 134+8)" cho asset đang chọn (không cần
  file plan) và menu "6. Kiểm tra fallback đã đăng ký (TMP_Settings)".
- Script chưa biên dịch kiểm chứng (không có Unity ở đây) — xem §6.

**Khác mục 7:** mục 7 thêm glyph vào atlas GỐC (giữ nguyên material, chỉ
đổi texture atlas khi atlas cũ đầy). C6 tạo asset MỚI, không sửa atlas
gốc. Hai cách bổ sung cho nhau: dùng C6 khi atlas gốc không sửa được
(mất font nguồn, không tạo atlas mới được) hoặc muốn giữ nguyên 100%
asset gốc.

## 2. File đổi

| File | Đổi gì |
|---|---|
| `site/pb3/sdk/tmp_font.py` | Thêm `build_fallback_plan()` + `FALLBACK_SCHEMA = "tmp_fallback_plan/1"` + `_fallback_atlas_size()` (kẹp [256, 1024]); ghi chú C7 → C6; `NOT_DONE` thêm dòng C6. Không sửa logic đọc TMP mục 7 (chỉ sửa 1 chuỗi ghi chú bị ngắt dòng để checker mục 7 vẫn đọc được). |
| `site/pb3/bridge.py` | Thêm op `tmp_font_fallback(path)` (ghi `/tmp/tmp_fallback_plan.json`) + cache `_LAST_TMP_SCAN` (khóa path+size+mtime); `tmp_font_read` dùng chung cache. |
| `site/pb3/tests/test_tmp_fallback.py` (mới, 6 test) | Test tổng hợp: schema/hợp nhất chữ thiếu, kẹp atlas, asset đủ → already_full, asset không đọc được → unplanned (không bịa ký tự), ascii_note khi thiếu cả chữ Latin, tái dùng glyph plan. |
| `site/pb3/tests.json` | Thêm `pb3/tests/test_tmp_fallback.py` (157 → 158 mục). |
| `site/index.html` | Tab Font: thêm nút `btn-font-fallback` + ghi chú về C6. |
| `site/app.js` | Handler `btn-font-fallback`: gọi `tmp_font_fallback`, hiển thị kế hoạch (từng fallback: chữ thiếu, tham số giữ, atlas fallback, font nguồn gợi ý, material không đổi, ascii_note) + link tải script C# + ranh giới. Sửa thêm: chuỗi `chars_text` dài 142 ký tự (cả handler mục 7) bọc `word-break:break-all` — trước đây tràn ngang 390px (scrollWidth 1230). |
| `site/unity_editor/TMP_VietnameseFallback.cs` (mới, ~29KB) | Script Editor: menu 4/5/6, tạo fallback + đăng ký TMP_Settings + bảng riêng asset gốc, chọn font nguồn (creationSettings → hỏi file .ttf/.otf trong Assets), hướng dẫn từng bước cuối file. Mảng 134 + 8 codepoint khớp đúng Python (kiểm bằng máy). |
| `exp/verify_analysis_c6.mjs` (mới) | Harness mục C6: 10 mục (tĩnh + fixture + unit test + trình duyệt 390px). |
| `exp/check_analysis_c6_static.py` (mới) | Kiểm tra tĩnh C6: schema, C# khớp Python, đăng ký fallback, KHÔNG sửa asset gốc, nút/handler/bridge/cache, manifest. |
| `exp/check_c6_fallback_plan.py` (mới) | Đối chiếu kế hoạch fallback với glyph plan mục 7 trên fixture studio + byte thật DATA WING. |
| `exp/analysis_c6_results.json` | Kết quả harness mục C6 (máy đọc). |
| `exp/verify_phase1_ci.mjs` | Cập nhật số lượng hardcode tests.json 157 → 158 (hệ quả trực tiếp của test mới, theo tiền lệ mục 7). |
| `exp/check_item7_static.py` | Bổ sung `"fzstd/index.mjs"` vào tập precache kỳ vọng — thiếu từ đợt C5 (C5 thêm file này vào precache `sw.js` nhưng không cập nhật checker mục 7); không đổi gì khác. |
| `exp/check_item8_static.py` | Cập nhật số lượng hardcode tests.json 157 → 158 (hệ quả trực tiếp của test mới). |

Không sửa: `~/workspace/pb3_fix/pb3` (cây iSH, chỉ đọc), zip trong
`~/workspace/your_files/`, workflows `.github/` (không sửa, không đẩy),
`site/sw.js` (không thêm file precache — không có module Python mới;
script C# phục vụ như file tĩnh, không precache giống script mục 7),
`site/pb3/files.json` (vẫn 89 file).

## 3. Kế hoạch fallback làm được gì (số đo thật)

Fixture studio `tmp_all.ipa` (18 TMP_FontAsset giả lập + 1 node bundle)
+ byte thật DATA WING (`tmp_real_xolonium.bin`):

- `missing_total` **142** — khớp đúng glyph plan mục 7 (cùng hợp nhất
  134 chữ + 8 dấu); `chars_text` dài đúng 142 ký tự.
- **15 fallback** trên IPA (19 asset): mỗi target của glyph plan có chữ
  thiếu đều có đúng 1 fallback tương ứng (cùng tên, cùng PointSize /
  Padding / atlas gốc, cùng `material_refs`, `material_unchanged: true`,
  atlas fallback trong [256, 1024], đủ 5 bước Editor).
- **4 unplanned** (`NoGlyphs`, `OutOfAtlas`, `ManyDup`,
  `TooManyPlaceholders` — `glyph_table_not_found`): không lập được kế
  hoạch vì không biết thiếu chữ nào; KHÔNG bịa danh sách ký tự.
- **0 already_full** trên fixture (không asset nào đủ cả 134 chữ + 8
  dấu cùng lúc).
- Ví dụ thật: `SourceCodePro SDF` — đủ 134 chữ cái, chỉ thiếu 8 dấu →
  fallback "Source Code Pro Vietnamese Fallback" chỉ chứa 8 dấu
  (U+0300–U+0323), PointSize 36, Padding 6.0, atlas gốc 1024×1024 →
  fallback 1024×1024, material "SourceCodePro Material" ghi rõ không đổi.
- `CJK SDF`: thiếu toàn bộ 142 → fallback 142 ký tự, atlas gốc
  4096×4096 → fallback kẹp về 1024×1024.
- DATA WING thật (`Xolonium`, PointSize 22, atlas 512×256): thiếu 98
  chữ + 8 dấu = **106** → 1 fallback "Xolonium Vietnamese Fallback",
  atlas fallback 512×256 (giữ đúng tỷ lệ gốc, không ép vuông).

Trình duyệt thật 390px (file `.assets` rời, 17 asset → 13 fallback):
- File `tmp_fallback_plan.json` tải về: 79.698 byte, schema
  `tmp_fallback_plan/1`, `missing_total` 142, `fallbacks` 13,
  `chars_text.length` 142, SourceCodePro thiếu 0 chữ + 8 dấu, atlas
  fallback 1024×1024, mọi fallback có atlas trong [256, 1024].
- Kết quả hiện "dùng lại kết quả quét TMP lần trước (không quét lại
  file)" — cache bridge hoạt động (nút fallback bấm sau nút đọc TMP).
- Script C# phục vụ qua HTTP 200 (29.439 ký tự), chứa đủ
  `TMP_Settings` / `fallbackFontAssets` / `fallbackFontAssetTable` /
  `CreateFontAsset` / `TryAddCharacters`, không gán atlas/material asset
  gốc (kiểm bằng regex trên nội dung đã tải).

## 4. Cái gì KHÔNG làm trong mục C6 (và vì sao)

- **Không tạo TMP_FontAsset thật trên web:** atlas SDF chỉ sinh được
  bằng FontEngine trong Unity Editor. Kế hoạch JSON là đầu vào cho
  script Editor, không phải asset dùng được ngay.
- **Không cứu chữ Latin thiếu:** fallback chỉ chứa chữ Việt thiếu. Nếu
  asset gốc thiếu cả chữ Latin (kiểm tra `ascii_missing` từng target),
  kế hoạch ghi rõ `ascii_note` ("KHÔNG cứu được phần này") — sửa atlas
  gốc (mục 7) hoặc thêm chữ Latin vào fallback trong Editor.
- **Không sửa asset gốc dưới mọi hình thức:** script C6 không động tới
  atlas/texture/material/bảng glyph của asset gốc (kiểm tra tĩnh cấm
  các phép gán đó; khác mục 7 sửa chính atlas gốc).
- **Không đọc render mode / giá trị shader SDF trên web:** render mode
  không nằm trong FaceInfo; script đọc lại trong Editor. Giá trị shader
  (Face Dilate, Outline…) chỉ đọc được trong Editor, không nằm trong
  TMP_FontAsset.
- **Không làm C7–C12:** các mục phân tích còn lại làm sau, theo thứ tự
  coordinator giao.

## 5. Sửa tràn ngang 390px (phát hiện nhờ harness C6)

Chuỗi `chars_text` 142 ký tự không có khoảng trắng hiển thị trong `<b>`
làm trang tràn ngang ở 390px (scrollWidth 1230 > clientWidth 390) — lỗi
này có từ handler mục 7 nhưng harness mục 7 không đo được (bước kiểm
tra tràn chạy sau khi nội dung đã bị thay bằng kết quả quét font). Đã
bọc chuỗi trong `word-break:break-all` ở cả hai handler (mục 7 + C6);
harness C6 đo lại scrollWidth = clientWidth = 390. Harness mục 7 chạy
lại sau sửa vẫn 11/11.

## 6. [HYPOTHESIS] chưa kiểm chứng — không bịa

1. **Script C# chưa biên dịch kiểm chứng:** không có Unity ở đây. Tên
   API dùng là API công khai của TextMeshPro 3.x/4.x
   (`CreateFontAsset`, `TryAddCharacters`, `TMP_Settings.instance.
   fallbackFontAssets`, `TMP_FontAsset.fallbackFontAssetTable`,
   `creationSettings.sourceFontFile`, `atlasRenderMode`). Nếu bản TMP
   của project khác tên/chữ ký, Editor sẽ báo lỗi ở đúng bước đó (mỗi
   bước có try/catch + hướng dẫn làm tay bằng Font Asset Creator và
   đăng ký tay trong Project Settings → TextMesh Pro).
2. **TMP_Settings có đọc fallback không:** về lý thuyết TMP tra
   `fallbackFontAssets` toàn cục rồi tới `fallbackFontAssetTable` của
   asset khi thiếu glyph; nhưng kết luận cuối chỉ có sau khi Joseph thử
   trong game thật (Play trong Editor rồi build ra iPhone).
3. **Bố cục TMP trên Unity 6 / TMP mới (9 Kings thật):** FaceInfo 20
   trường + bảng glyph 36 byte đã xác nhận trên DATA WING (Unity 5.6,
   TMP cũ). 9 Kings dùng Unity 6 + TMP mới hơn — bố cục CÓ THỂ khác;
   khi đó web trả `glyph_table_not_found` kèm chẩn đoán, asset đó vào
   `unplanned` (không bịa kế hoạch) — cần Joseph gửi file `.assets`
   thật (hoặc log chẩn đoán) để chỉnh neo.
4. **Font nguồn trong Editor:** nếu asset gốc mất
   `creationSettings.sourceFontFile`, script hỏi chọn file .ttf/.otf —
   file phải nằm trong thư mục Assets thì Unity mới load được; nếu
   Joseph không còn font gốc của game thì dùng Be Vietnam Pro đi kèm
   BearTea (đủ tiếng Việt) — nét chữ fallback có thể khác font gốc
   đôi chút (đó là bản chất của fallback).
5. **Số đo trên chỉ là fixture giả lập + 1 bản tái dựng byte thật:**
   không chứng minh đúng trên 9 Kings thật. Cửa kiểm chứng thật duy
   nhất vẫn là IPA 9 Kings của Joseph.

## 7. Hồi quy + FAIL còn lại (nói rõ từng cái)

| Harness | Kết quả | Ghi chú |
|---|---|---|
| C6 (mới) | **10/10** | |
| module-worker | **17/17** | |
| C2 | **13/13** | |
| item7 | **11/11** | Sau 2 sửa bảo trì checker: (a) checker mục 7 thiếu `"fzstd/index.mjs"` trong tập precache kỳ vọng — thiếu từ đợt C5, không phải do C6; (b) 1 chuỗi ghi chú của C6 trong `tmp_font.py` bị ngắt dòng làm checker mục 7 đọc trượt cụm "không dùng msdf-atlas-gen" — đã gộp lại 1 dòng. |
| item8 | **16/16** | Sau cập nhật số lượng tests.json 157 → 158 (hệ quả trực tiếp của test mới). |
| item6 | **15/16** | Chỉ FAIL đã biết `shell_raw_under_100kb`: tổng raw mới **347.244B (339,1KB)** > 100KB (284KB là số ở đợt 18:36, trước khi C2–C5 + mục 8 lên live — tăng do các đợt đó + C6 đều thêm code vào `app.js`/`index.html`); gzip **98,7KB < 100KB** vẫn đạt (tăng từ 81,7KB ở đợt 18:36, còn cách ngưỡng 1,3KB). Cần tách `app.js` thành chunk lazy theo tab — refactor lớn, làm riêng sau (đã ghi từ mục 6). |
| phase1_ci | **7/8** | FAIL `markers_count`: decorator `needs_fixture` đếm được **122**, kỳ vọng 116. **Không phải do C6** — file test mới của C6 có 0 marker; 6 marker tăng thêm nằm ở `test_unity.py` (75 marker) do đợt C5 thêm test Zstd/v9. Cần cập nhật kỳ vọng 116 → 122 (hoặc kiểm lại phủ 126 test) ở đợt sau — C6 không tự ý đổi số của C5. |
| Full suite Python | **1616 test: 1 FAIL** | `test_rules_ish_only.test_no_game_specific_branches_or_forbidden_scope`: `sdk/unity.py` chứa `'I2Languages'` (comment ở dòng 1347, sửa lần cuối 12:30 — từ đợt sửa trích xuất game thật, trước C6). File C6 không chứa chuỗi này. FAIL có từ trước, không liên quan C6; cần đợt sau xử lý (đổi comment hoặc nới quy tắc). 2 skipped như cũ. |

## 8. Còn chờ Joseph kiểm chứng (thử trên máy thật)

1. **Trên web (Safari thật):** chọn file `.assets` hoặc IPA của 9 Kings →
   tab Font → "Đọc TMP_FontAsset" → "Tạo font fallback" — xem atlas thật
   của game thiếu bao nhiêu chữ Việt (fixture chỉ là giả lập; số thật
   của 9 Kings chưa biết). Tải `tmp_fallback_plan.json` về.
2. **Trên Unity Editor:** tải script `unity_editor/
   TMP_VietnameseFallback.cs` về (link trong kết quả trên web), chép vào
   `Assets/Editor/`, chọn TMP_FontAsset của 9 Kings, chạy menu BearTea →
   TMP → "4. Tạo font fallback tiếng Việt (từ file tmp_fallback_plan.json)"
   rồi chọn file plan đã tải. Nếu script hỏi font nguồn: chọn font gốc
   của game (hoặc Be Vietnam Pro nếu font gốc đã mất).
3. **Kiểm tra đăng ký:** menu "6. Kiểm tra fallback đã đăng ký
   (TMP_Settings)" — Console phải liệt kê đủ fallback mới; asset gốc
   không đổi gì (so sánh tham số trước/sau).
4. **Trong game thật:** bấm Play trong Editor, mở màn hình có chữ Việt —
   chữ thiếu trước đây phải hiện đủ dấu, font gốc không đổi nét/viền.
   Rồi build ra iPhone thử lại (chỉ Joseph làm được).
5. Nếu script báo lỗi API (bản TMP khác) hoặc chữ vẫn thiếu: ghi lại tên
   font asset + dòng log Console gửi cho Joseph (xem §6.1–6.2).

## 9. Ghi công

Logic đọc TMP port từ IPA Localization Studio (`js/modules/tmp_font.js`,
MIT, tác giả Joseph) — giữ file `LICENSE` gốc trong
`vendor/ipa-localization-studio/`; file Python mới ghi rõ nguồn + tác giả
trong docstring. Bộ 134 chữ Việt dùng chung với `sdk/font.py` của PB3.
