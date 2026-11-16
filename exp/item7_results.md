# Mục 7 — Font atlas build-time trong Unity Editor (kết quả)

Ngày kiểm chứng: 2026-11-16. Repo: MunKai0408/MeoRocky, nhánh main.
Head lúc làm: 6a73326 (mục 6 SW precache + lazy boot). KHÔNG đẩy lên GitHub —
coordinator đẩy sau khi kiểm chứng.

## 0. Tóm tắt nhanh

| Hạng mục | Kết quả |
|---|---|
| Harness mục 7 (`exp/verify_item7.mjs`) | **11/11 PASS, 0 FAIL** |
| Bộ test JS tham chiếu của studio (`run_tmp_tests.js`) | **297/297** |
| Python so với đáp án độc lập (`check_tmp_fixture.py`) | **0 lệch** (18 asset + byte thật DATA WING + node bundle) |
| Unit test Python mới (`tests/test_tmp_font.py` + `test_font.py`) | **62 test, OK** (17 mới + 45 font cũ) |
| Hồi quy 18 harness cũ + `tests.test_extraction` | **17/18 xanh hoàn toàn; item6 còn đúng 1 FAIL có chủ ý** (`shell_raw_under_100kb`, đã biết từ trước). 4 FAIL số lượng hardcode (87→88, 155→156) đã sửa theo tiền lệ + chạy lại xanh; item5 chạy lại 25/25 (flake lần 1). `test_extraction` **45/45**. Xem §7 |
| Trình duyệt thật 390px | boot tới "Sẵn sàng — 20 plugin", tab Font đọc TMP 134/134, tải `tmp_glyph_plan.json` đúng, font scan vẫn 142/142, không tràn ngang, không lỗi console |

## 1. Ranh giới web vs Unity Editor — đọc trước tiên

Font atlas build-time vốn là việc của **Unity Editor, KHÔNG phải việc chạy
trong trình duyệt**. Mục 7 làm đúng phần thuộc về mỗi bên:

**Web làm được (đã làm + đã kiểm chứng):**
- Đọc tham số thật từ TMP_FontAsset: FaceInfo (tên font nguồn, PointSize,
  Scale, CharacterCount, LineHeight, Baseline/Ascender/CapHeight/Descender,
  Padding, kích thước atlas + cơ sở suy ra), bảng glyph 36 byte/phần tử
  (id, x, y, width, height, xOffset, yOffset, xAdvance, scale), độ phủ
  tiếng Việt (134 chữ + 8 dấu kết hợp), tham chiếu PPtr tới
  Texture2D/Material/Font/TMP_FontAsset khác.
- Convert bảng glyph ở mức web làm được: từ bảng glyph đã đọc → tính chữ
  thiếu → xuất **glyph plan** (`tmp_glyph_plan.json`): chuỗi ký tự thiếu
  (dán vào ô Custom Characters của TMP Font Asset Creator) + tham số atlas
  cần giữ (kích thước, padding, point size, scale) để Editor tái dựng atlas
  SDF với CÙNG tham số gốc.
- Tab Font tích hợp: nút "Đọc TMP_FontAsset (atlas SDF)" + hiển thị tham
  số/coverage/tham chiếu + tải plan JSON về.

**Web KHÔNG làm được (ghi rõ, không giả vờ):**
- Sinh lại atlas **SDF**: cần FontEngine trong Unity Editor. Trình duyệt
  không có SDF rasterizer; Canvas chỉ vẽ được bitmap, không sinh được SDF.
- Đọc giá trị shader SDF trong material (vd `_FaceDilate`, độ rộng
  outline): các giá trị đó không nằm trong TMP_FontAsset, chỉ đọc được
  trong Editor (script đọc `fontAsset.material` tại đó).
- Vì vậy **KHÔNG port `font_generator.js`** (sinh glyph BMFont `.fnt` +
  atlas PNG bằng Canvas): BMFont không phải SDF, không dùng được cho TMP;
  port nó sẽ cho ra atlas sai loại. Quyết định này có ghi trong §5.2.

**Unity Editor làm (script kèm theo `site/unity_editor/TMP_VietnameseAtlas.cs`):**
- Đọc tham số asset đang chọn (đối chiếu với số web đã đọc).
- Mức rẻ nhất: `TryAddCharacters` thêm glyph vào atlas hiện có, **giữ
  nguyên material** (shader + mọi thuộc tính SDF). Atlas còn chỗ thì xong
  ngay, không đổi gì thêm.
- Atlas đầy: tạo atlas mới CÙNG padding/render mode/point size (chỉ lớn
  hơn), copy atlas texture + bảng glyph + face info về asset GỐC (mọi tham
  chiếu trong game giữ nguyên), chỉ đổi `material.mainTexture` — đây chính
  là "mức rẻ nhất: sửa material".
- Script chưa biên dịch kiểm chứng (không có Unity ở đây) — xem §6.

**KHÔNG dùng msdf-atlas-gen import mù:** không dùng tool sinh atlas nào mà
không đọc tham số gốc. Mọi tham số atlas mới đều lấy từ asset gốc đã đọc
(web) hoặc đọc lại trong Editor (script).

## 2. File đổi

| File | Đổi gì |
|---|---|
| `site/pb3/sdk/tmp_font.py` (mới, ~1000 dòng) | Port `tmp_font.js` sang Python (stdlib, Python 3.9): `decode_object`, `find_face_info` (20 + 18 trường), `find_glyph_table` (36 byte), `vietnamese_coverage` (134 + 8 dấu), `script_class_of_object`/`find_tmp_font_assets` (nhận diện TMP qua MonoScript), `scan_serialized_file`, `scan_source` (file .assets/.unity3d/.bundle, thư mục, ZIP/IPA, node bundle), `build_glyph_plan`. Mọi thông điệp tiếng Việt có dấu đầy đủ. |
| `site/pb3/bridge.py` | Thêm op `tmp_font_read(path)` (ghi `/tmp/tmp_glyph_plan.json` để JS tải về). |
| `site/pb3/tests/test_tmp_font.py` (mới, 17 test) | Fixture SerializedFile v17 tự dựng + byte thật DATA WING (Xolonium). |
| `site/pb3/files.json` | Thêm `pb3/sdk/tmp_font.py` (87 → 88 file; worker tải lúc boot). |
| `site/pb3/tests.json` | Thêm `pb3/tests/test_tmp_font.py` (155 → 156 mục). |
| `site/sw.js` | Thêm `pb3/sdk/tmp_font.py` vào PRECACHE_URLS; `CACHE_VERSION` v6-20261116 → **v7-20261116** (đổi nội dung precache thì phải đổi version, theo đúng ghi chú trong file). |
| `site/index.html` | Tab Font: thêm nút `btn-font-tmp` + ghi chú về TMP/Editor. |
| `site/app.js` | Handler `btn-font-tmp`: gọi `tmp_font_read`, hiển thị tham số/coverage/tham chiếu/kế hoạch glyph, tải plan JSON. |
| `site/unity_editor/TMP_VietnameseAtlas.cs` (mới) | Script Editor: menu BearTea → TMP (đọc tham số / thêm glyph giữ material / thêm từ file plan JSON) + hướng dẫn từng bước cuối file. Mảng 134 + 8 codepoint sinh từ Python, không chép tay. |
| `exp/verify_item7.mjs` (mới) | Harness mục 7: 11 mục (tĩnh + fixture + unit test + trình duyệt 390px). |
| `exp/check_tmp_fixture.py` (mới) | So Python với `tmp_expected.json` (tái dùng được). |
| `exp/check_item7_static.py` (mới) | Kiểm tra tĩnh: stdlib/3.9, C#, manifest/precache. |
| `exp/verify_limit_2gb.mjs`, `exp/verify_module_worker.mjs`, `exp/verify_patch01_web.mjs`, `exp/verify_phase1_ci.mjs` | Cập nhật số lượng hardcode 87 → 88 (files.json) / 155 → 156 (tests.json) — hệ quả trực tiếp của file mới, theo tiền lệ phase 3 đã sửa 71 → 87. Xem §7. |
| `exp/item7_results.json` | Kết quả harness mục 7 (máy đọc). |

Không sửa: `~/workspace/pb3_fix/pb3` (chỉ đọc), zip trong
`~/workspace/your_files/`, workflows `.github/` (không sửa, không đẩy).

## 3. Tham số TMP_FontAsset đọc được gì (số đo thật)

Fixture: `vendor/.../tests/make_tmp_ipas.py` → `tmp_all.ipa`
(18 TMP_FontAsset giả lập) + `tmp_real_xolonium.bin` (tái dựng từ byte thật
DATA WING). Python khớp **0 lệch** với đáp án độc lập trên mọi trường:

- 18/18 asset: status, FaceInfo.name, CharacterCount khai báo, atlas
  (W×H + cơ sở `faceinfo_layout` / `faceinfo_layout_18`), glyph count,
  code point ranges, độ phủ Việt, ascii_ok, problem, placeholder_glyphs.
- Ví dụ thật: `SourceCodePro SDF` — font "Source Code Pro", PointSize 36,
  Scale 1.0, LineHeight 48, Padding 6.0, atlas 1024×1024
  (`faceinfo_layout`), 325 glyph, tiếng Việt **134/134 full** (dấu kết hợp
  0/8 — atlas giả lập này không có dấu rời); tham chiếu: texture
  "SourceCodePro Atlas", material "SourceCodePro Material", font
  "SourceCodePro-Regular" (khớp tên FaceInfo).
- `OneMissing SDF`: thiếu đúng 1 ký tự **U+1EF9 (ỹ)** → partial 133/134,
  KHÔNG được coi là đủ.
- `CJK SDF`: atlas 4096×4096, 3095 glyph, tiếng Việt 0/134 (none).
- 4 asset `glyph_table_not_found` (NoGlyphs, OutOfAtlas, ManyDup,
  TooManyPlaceholders): KHÔNG bịa danh sách ký tự; có diagnostics
  (`hex_head` + `count_hits` + `best_table_check`, vd OutOfAtlas: vi phạm
  đầu tiên index 50, id 115, `outside_atlas`).
- `Legacy18 SDF`: FaceInfo 18 trường → `faceinfo_layout_18`, atlas đúng
  1024×1024.
- `NoHeader SDF` (script PPtr = 0): header null + problem "Header
  MonoBehaviour không khớp", vẫn đọc đủ FaceInfo/glyph (nhận diện qua bảng
  type/script, không qua header).
- `BE SDF` (big-endian, level0): parsed, full 134/134.
- Byte thật DATA WING (`European Chars - Xolonium`): parsed, PointSize 22,
  atlas 512×256, 254 glyph, tiếng Việt **36/134 partial**, 1 glyph vô hình
  (khoảng trắng y=259 > cao atlas 256), ranges [[32, 285]] — khớp hết.
- **Hơn studio:** BearTea đọc được TMP trong **node bundle UnityFS**
  (`InBundle SDF`, parsed, nguồn `b.bundle::CAB-1`, bundle_nodes=1) —
  studio chỉ cảnh báo "chưa được đọc", không đọc node. Tổng 19 asset
  (18 kỳ vọng + 1 InBundle).

Bộ 134 chữ Việt: Python (`sdk/font.py`) == studio (`tmp_font.js`
`viRequired()`) == đáp án fixture — đã kiểm bằng test, 134/134, không lệch
ký tự nào. Font scan cũ giữ nguyên: Be Vietnam Pro **142/142** (527 glyph)
trên trình duyệt thật.

## 4. Convert bảng glyph làm được gì (số đo thật)

- Glyph plan cho cả IPA fixture: `missing_total` **142** (134 chữ + 8 dấu
  — hợp nhất chữ thiếu của mọi asset; asset CJK thiếu toàn bộ nên hợp nhất
  vẫn là toàn bộ), `chars_text` dài 142 ký tự, `targets` 15 (IPA) / 13
  (file resources.assets rời).
- Plan cho 1 asset thiếu đúng 1 chữ (test): `missing_letters_codepoints`
  = [0x1EF9], `missing_marks_codepoints` = 8 dấu, `chars_text` chứa "ỹ";
  target ghi đủ atlas 1024×1024, point_size 36, padding 6.0, material ref
  "SourceCodePro Material".
- File plan JSON tải về từ trình duyệt thật: `tmp_glyph_plan.json`
  (35.713 byte), schema `tmp_glyph_plan/1`, `missing_total` 142,
  `targets` 13 — khớp Python.
- Script Editor đọc được file plan này (menu 3) hoặc dùng bộ 134 + 8
  nhúng sẵn (menu 2). Mảng codepoint trong `.cs` đã kiểm bằng máy là khớp
  đúng tập Python (134 + 8, không thừa không thiếu).

## 5. Cái gì KHÔNG làm trong mục 7 (và vì sao)

- **Không port `font_generator.js`** (sinh glyph BMFont `.fnt` + atlas PNG
  bằng Canvas): BMFont không phải SDF, không dùng được cho TMP. Port nó sẽ
  cho ra atlas sai loại mà trông như "đã xong". Ghi rõ trong báo cáo kiểm kê
  §5.2: engine có đọc trang atlas thêm/atlas dựng lại không là
  [HYPOTHESIS] chưa biết — với TMP thì câu trả lời đúng là phải sinh lại
  SDF bằng FontEngine trong Editor, không phải BMFont.
- **Không đọc giá trị shader SDF trong material** (Face Dilate, Outline…):
  không nằm trong TMP_FontAsset; chỉ đọc được trong Editor. Web chỉ đọc
  được *tham chiếu* material (tên + path_id) — đủ để script Editor tìm
  đúng material cần giữ.
- **Không giải mã ảnh atlas (Texture2D)**, không kiểm glyph có hình thật
  hay ô trống, không liên kết TextMeshPro component → font asset đang dùng
  thật (giống giới hạn của studio).
- **Không làm C7** (tạo TMP_FontAsset MỚI chỉ chứa dấu tiếng Việt làm
  fallback cho TMP_Settings): đó là mục làm sau (sau mục 5–9 + audit).
  Xem §8 để phân biệt.

## 6. [HYPOTHESIS] chưa kiểm chứng — không bịa

1. **Bố cục TMP trên Unity 6 / TMP mới (9 Kings thật):** FaceInfo 20
   trường + bảng glyph 36 byte đã xác nhận trên DATA WING (Unity 5.6, TMP
   cũ). 9 Kings dùng Unity 6 + TMP mới hơn — bố cục CÓ THỂ khác (thêm
   trường, đổi thứ tự). Nếu khác, web sẽ trả `glyph_table_not_found` kèm
   chẩn đoán chứ không bịa — nhưng lúc đó cần Joseph gửi file `.assets`
   thật (hoặc log chẩn đoán) để chỉnh neo. Câu hỏi 12–14 trong §10 báo cáo
   kiểm kê vẫn mở.
2. **Script C# chưa biên dịch kiểm chứng:** không có Unity ở đây. Tên API
   dùng là API công khai của TextMeshPro 3.x/4.x (`TryAddCharacters`,
   `CreateFontAsset`, `faceInfo`, `atlasTexture`, `material.mainTexture`…).
   Nếu bản TMP của project khác tên/chữ ký, Editor sẽ báo lỗi ở đúng bước
   đó (mỗi bước có try/catch + hướng dẫn làm tay bằng Font Asset Creator).
3. **Engine có đọc atlas đã thêm glyph không:** với TMP, `TryAddCharacters`
   cập nhật đúng atlas texture mà material đang trỏ tới nên về lý thuyết
   game đọc ngay; nhưng kết luận cuối chỉ có sau khi Joseph thử trong game
   thật (Play trong Editor rồi build ra iPhone).
4. **Dấu kết hợp (8 dấu) có render đúng không:** TMP cần glyph dấu trong
   atlas; script thêm cả 8 dấu. Chữ Việt trong game 9 Kings dùng dạng ghép
   sẵn (NFC) là chính; dấu rời chỉ cần khi text dùng dạng tổ hợp — vẫn thêm
   cho đủ, không hại.
5. **Số đo trên chỉ là fixture giả lập + 1 bản tái dựng byte thật:** không
   chứng minh đúng trên 9 Kings thật. Cửa kiểm chứng thật duy nhất vẫn là
   IPA 9 Kings của Joseph.

## 7. Hồi quy 18 harness cũ + test_extraction

Chạy lại toàn bộ sau mục 7 (log `/tmp/regress7_*.log`, chạy lại lần 2 cho
5 harness có FAIL ở `/tmp/regress7b_*.log`):

| Harness | Lần 1 | Ghi chú |
|---|---|---|
| item6 | 15/16 | 1 FAIL có chủ ý `shell_raw_under_100kb` (đã biết từ trước, KHÔNG phải do mục 7; shell gzip 74.175B < 100KB vẫn PASS) |
| item5 | 24/25 → **25/25 sau khi chạy lại** | FAIL `browser_gzip_member_download` (nút gzip không hiện) ở lần 1 là **flake** — chạy lại đạt 25/25, không đổi code. Code đó (tab Danh sách/stream.js, nút gzip) mục 7 không chạm tới; lần 1 chạy ngay sau item6 nặng, có thể do timing/CPU |
| enjson | 6/6 | |
| extract_select | 8/8 | |
| translate_connect | 12/12 | |
| translate_batch | 21/21 | |
| hotfix_safari2 | 17/17 | |
| item3_kb | 27/27 | |
| item4_pool | 28/28 | |
| module_worker | 16/17 → **17/17 sau khi chạy lại** | FAIL `write_site_files`: hardcode 87, files.json nay 88 do thêm `sdk/tmp_font.py` |
| patch01 | 7/8 → **8/8 sau khi chạy lại** | như trên |
| parity | 14/14 | |
| limit_2gb | 5/6 → **6/6 sau khi chạy lại** | như trên (harness exit 0 nhưng ghi 1 FAIL — chính là `write_site_files`) |
| clone_fix | 10/10 | |
| phase1_ci | 7/8 → **8/8 sau khi chạy lại** | FAIL `test_html`: hardcode tests.json 155, nay 156 do thêm `tests/test_tmp_font.py` |
| item2 | 17/17 | |
| redesign | 10/10 | |
| ipa_direct | 8/8 | |
| `tests.test_extraction` | **45/45** | |

4 FAIL số lượng là hệ quả trực tiếp và đúng đắn của file mới (worker
buộc phải có `sdk/tmp_font.py` trong files.json mới import được; tests.json
buộc phải liệt kê test mới). Đã cập nhật số lượng trong 4 harness theo tiền
lệ phase 3 (71 → 87) và chạy lại — kết quả chạy lại (log
`/tmp/regress7b_*.log`): **item5 25/25, module_worker 17/17, patch01 8/8,
phase1_ci 8/8, limit_2gb 6/6 — tất cả xanh**. Chỉ còn item6 với 1 FAIL có
chủ ý `shell_raw_under_100kb` (đã biết từ trước).

## 8. Overlap / phân biệt với C7 (mục làm sau)

| | Mục 7 (đợt này) | C7 (làm sau) |
|---|---|---|
| Cách làm | Sửa **chính atlas gốc**: thêm glyph Việt vào atlas SDF của TMP_FontAsset đang dùng (trong Unity Editor) | Tạo TMP_FontAsset **MỚI chỉ chứa dấu tiếng Việt**, cùng tham số SDF, thêm vào fallback của TMP_Settings |
| Sửa atlas gốc? | Có | Không |
| Sửa bảng glyph? | Có (thêm glyph) | Không |
| Sửa material? | Mức rẻ nhất: giữ nguyên material, chỉ đổi `mainTexture` khi atlas mới | Không động tới material gốc |
| Khi nào dùng | Font nguồn còn giữ được + atlas còn chỗ hoặc tạo atlas mới được | Atlas gốc không sửa được (mất font nguồn, không tạo atlas mới được) hoặc muốn giữ nguyên 100% asset gốc |
| Web làm được | Đọc tham số + kế hoạch glyph (đã xong) | Chưa làm — cần tạo asset mới (cũng là việc Editor) |
| Rủi ro | Atlas mới có thể lệch nét/viền nếu tham số không khớp — giảm thiểu bằng cách giữ đúng tham số gốc | Fallback chỉ cứu dấu, không cứu chữ cái thiếu; cần kiểm TMP_Settings |

Hai cách bổ sung cho nhau, không thay thế nhau. Kế hoạch glyph của mục 7
ghi rõ danh sách dấu thiếu (8 dấu) nên C7 sau này dùng lại được luôn.

## 9. Còn chờ Joseph kiểm chứng (thử trên máy thật)

1. **Trên Unity Editor:** chép `site/unity_editor/TMP_VietnameseAtlas.cs`
   vào `Assets/Editor/`, chọn TMP_FontAsset của 9 Kings, chạy menu
   BearTea → TMP → "1. Đọc tham số" — đối chiếu số với web (PointSize,
   Padding, atlas, render mode). Nếu số không khớp, dừng lại và gửi log
   Console (bố cục TMP bản này lạ — xem §6.1).
2. **Thêm glyph:** menu "2. Thêm glyph tiếng Việt (giữ nguyên material)"
   (hoặc "3. … từ file tmp_glyph_plan.json" sau khi tải plan từ web).
   Kiểm tra Console: độ phủ sau khi thêm phải là 134/134 chữ + 8/8 dấu.
3. **Trong game thật:** bấm Play trong Editor, mở màn hình có chữ Việt —
   chữ phải hiện đủ dấu, không ô vuông, không mất nét/viền font gốc. Rồi
   build ra iPhone thử lại (chỉ Joseph làm được).
4. **Trên web (Safari thật):** chọn file `.assets` hoặc IPA của 9 Kings →
   tab Font → "Đọc TMP_FontAsset" — xem atlas thật của game đủ/thiếu bao
   nhiêu chữ Việt (fixture chỉ là giả lập; số thật của 9 Kings chưa biết).
5. **36 câu hỏi §10 báo cáo kiểm kê** vẫn mở, đặc biệt câu 12–14 (TMP) và
   18–20 (font_generator): hỏi dần khi tích hợp tới từng module.

## 10. Ghi công

Logic đọc TMP port từ IPA Localization Studio (`js/modules/tmp_font.js`,
MIT, tác giả Joseph) — giữ file `LICENSE` gốc trong
`vendor/ipa-localization-studio/`; file Python mới ghi rõ nguồn + tác giả
trong docstring. Bộ 134 chữ Việt dùng chung với `sdk/font.py` của PB3.
