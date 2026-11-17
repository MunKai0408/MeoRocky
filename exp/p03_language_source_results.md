# Trích xuất cấu trúc bảng ngôn ngữ game (P0-3, BearTea/PB3)

Ngày: 2026-11-17. Lượt này sửa cách công cụ đọc dữ liệu ngôn ngữ trong file
Unity: từ quét chuỗi phẳng (key `.sNNNN`, mọi ngôn ngữ trộn lẫn, chuỗi rỗng bị
bỏ gây lệch cột) sang đọc đúng cấu trúc bảng ngôn ngữ của game
(`mLanguages`/`mTerms`, ánh xạ theo chỉ số, giữ chuỗi rỗng) + gộp StringTable
theo nhóm + lọc rác Unity + ghi xung đột rõ ràng.

Nhãn trung thực dùng trong toàn báo cáo: **[ĐÃ ĐO THẬT]** = số đo trực tiếp
trong lượt này; **[FIXTURE]** = fixture tự sinh, không phải game thật;
**[HYPOTHESIS]** = suy luận chưa kiểm chứng. Mọi số đo trên game thật ghi rõ
build. Tên sản phẩm của bảng ngôn ngữ không xuất hiện trong code/tài liệu repo
theo quy tắc `test_no_game_specific_branches_or_forbidden_scope` — gọi chung là
"bảng ngôn ngữ của game"; tên thật chỉ xuất hiện trong thư mục giao hàng ngoài
repo.

## 1. Vấn đề cũ (đã đo, không đoán)

Trước sửa, MonoBehaviour không có typetree được đọc bằng quét chuỗi phẳng
(`scan_monobehaviour_strings`): chuỗi rỗng bị bỏ (`size <= 0`), key đặt theo vị
trí (`.sNNNN`/`.pNNNN`/`.xN`), ngôn ngữ của mỗi chuỗi được đoán theo chunk.
Hậu quả đã đo trên 9 Kings v1.0.6: `en.json` gộp chứa giá trị tiếng Đức/Tây Ban
Nha/Đan Mạch ở key term thật (vd `UI_War` = "Guerra", `Skin_MoneySigns` =
"**Dollartegn**"), zh chứa tiếng Nhật (vd `UI_Connecting` = "接続中"), và nhiều
term vắng mặt hoàn toàn. Mốc 347/560 trong brief gốc đo trên file mẫu 20 MB
không còn trên máy, không tái hiện được — không dùng làm căn cứ. [ĐÃ ĐO THẬT:
diff file gộp cũ/mới, §4]

## 2. Thay đổi code (6 file sửa, không file pb3 mới; +2 file exp mới là báo cáo này và JSON kèm theo)

- `pb3/sdk/unity.py` — parser cấu trúc bảng ngôn ngữ, 2 đường:
  (a) byte parser `parse_language_source_bytes` cho build strip typetree: đi
  bộ có kiểm chứng (số cột mỗi term bằng nhau và bằng số ngôn ngữ cuối bảng;
  số cờ bằng số cột; term duy nhất; mã ngôn ngữ đúng dạng; ≥1 cột có mã; đuôi
  ≤65.536 byte chỉ đếm, không parse), fail-closed trả `None` khi bất kỳ kiểm
  chứng nào trượt; (b) `language_source_from_fields` cho build có typetree
  [HYPOTHESIS — chỉ kiểm chứng trên dict tổng hợp, chưa có build thật nào có
  typetree cho bảng này]. Nối vào `monobehaviour_texts()` với kind mới
  `"language_source"`; object qua kiểm chứng không bao giờ đi qua quét phẳng.
  [ĐÃ ĐO THẬT: đúng 1 object/bảng ở cả 4 build thật, 0 false positive trên
  9.662 object generic; output byte-identical với dump độc lập + với kết quả
  mẫu của parent trên file Build 68]
- `pb3/sdk/extraction.py` — định tuyến `language_sources` riêng trong
  `_expand_serialized`/`_expand_unity`/`_expand_unity_stream`; `_build_layers`
  xuất file ảo `*.langtable.monobehaviour.<mã gốc>.json` (key = tên term thật,
  giữ chuỗi rỗng) qua helper `_language_source_virtual_files`; gộp biến thể về
  mã gốc theo `_language_source_base_columns` (mã đúng thắng, nếu không lấy
  biến thể đầu tiên theo thứ tự bảng; cột không-mã bỏ qua có đếm, ghi log);
  `join_string_tables_grouped` (gộp theo (bundle, locale, collection), xung đột
  ghi rõ, kind `value`/`shared_key`) đặt cạnh `_join_string_tables` cũ
  (không đụng — đã kiểm vi sai: output byte-identical trên dữ liệu 9K thật);
  `_mb_string_is_noise` mở rộng (chuỗi `UnityEngine.`, namespace `Unity.*`,
  class USS `unity-*` toàn giá trị, namespace 3 đoạn, tài liệu UXML, đuôi
  `.uxml`/`.uss`) — chỉ đường generic, có cơ sở đo trong docstring, KHÔNG lọc
  CamelCase trần (đo: 1.971/396/566 giá trị, lẫn text thật); `_export` ghi
  `conflicts.json` (`beartea-merge-conflicts/1`) khi gộp có xung đột > 0.
- `pb3/sdk/rebuild.py` — `_collect_mb_keys` cộng tên term của bảng ngôn ngữ vào
  `mb_terms` để phân loại unmatched của (E) đếm đúng nhóm `monobehaviour`.
- Test mới: 35 test trong 3 file hiện có (`test_unity.py` +18 parser byte/field,
  `test_extraction.py` +15 gộp nhóm/cột mã gốc/lọc rác/file ảo, gồm test khóa
  lỗi va chạm tên file ảo; `test_merge_one_file.py` +2 `conflicts.json`).
  Không file mới, không đổi precache: `files.json` 95, `tests.json` 166,
  CACHE_VERSION v21-20261117 giữ nguyên; không đổi UI.

## 3. Quyết định thiết kế (mỗi quyết định 1 dòng lý do)

- **Parser fail-closed, không fallback âm thầm** — vì đoán sai layout sẽ gán
  nhầm ngôn ngữ cho hàng nghìn term mà không ai phát hiện; object trượt kiểm
  chứng thì đi đường generic cũ (có ghi log) ở pipeline, còn script xuất
  locales thì báo CHƯA ĐẠT và không ghi file.
- **Pipeline gộp biến thể về mã gốc, xuất locales giữ đủ biến thể** — vì bảng
  locale hiện có gộp biến thể (`zh-cn`/`zh-tw` → `zh`) và mốc §4 cần so được;
  độ trung thực biến thể đầy đủ nằm ở artifact locales, không ép vào pipeline.
- **Đường quét phẳng chỉ còn cho object không phải bảng ngôn ngữ, ghi rõ lý do
  trong code** — vì xóa hẳn sẽ mất hàng nghìn chuỗi game thật không có nguồn
  thay thế và phá test hiện có khẳng định key có hậu tố; ranh giới này là quyết
  định có ghi rõ, không phải sót.
- **File ảo bảng ngôn ngữ mang đoạn `.langtable`** — vì tên cũ đụng file generic
  cùng đơn vị + ngôn ngữ, dict ảo ghi đè làm mất bảng (lỗi thật gặp khi kiểm
  chứng, đã sửa + test khóa); hậu tố `.monobehaviour.<lang>.json` giữ nguyên để
  nhóm `__mono__` và đặt tên xuất vẫn nhận ra, không cần sửa code khác.
- **`_join_string_tables` cũ giữ nguyên, hàm nhóm mới đặt cạnh** — vì tránh mọi
  rủi ro đổi thứ tự gộp ở đường pipeline; đã kiểm vi sai byte-identical.
- **Không harness `.mjs` mới** — vì không đổi UI/thuật toán web; kiểm chứng
  bằng test Python + xuất trên game thật + harness cũ chạy lại.

## 4. Kiểm chứng trên game thật [ĐÃ ĐO THẬT]

Xuất locales bằng `export_locales.py` + tự kiểm tra bằng `selfcheck.py` (cùng
code SDK, đọc lại input và đối chiếu từng giá trị). Chi tiết đầy đủ trong
`report.md` của thư mục giao hàng `deliver_p03_locales/` (ngoài repo).

| Game (build) | Bảng ngôn ngữ | StringTable | Key/file | Thiếu en / zh-CN / zh-TW | Xung đột / Rác | Tự kiểm tra |
|---|---|---|---|---|---|---|
| 9 Kings v1.0.6 IPA (173.472.485 B, md5 4f53043c…) | 2.150 term, 30 cột (en c0, zh-TW c4, zh-CN c29) | 102 key, 6 collection | 2.252 | 2 / 2.218 / 106 | 0 / 1.006 | 11/11 PASS |
| File mẫu Build 68 (20.046.080 B, md5 04646b0b…) | 2.086 term, 30 cột (en c0, zh-CN c4, zh-TW c5) | 0 | 2.086 | 0 / 93 / 38 | 0 / 117 | 14/14 PASS, khớp byte-identical với kết quả mẫu của parent (3 file locale + conflicts) |
| Poly Bridge 3 v1.1.7 (482.039.645 B, md5 065efc5f…) | 2.348 term, 12 cột (en c0, zh-CN c7, không zh-TW) | 0 | 2.348 | 0 / 2 / 2.348 | 0 / 450 | 10/10 PASS |
| Exploding Kittens 2 v0.0.18 (290.547.090 B, md5 ba98dd3d…) | 1.344 term, 9 cột (en-GB c2, en-US c3, không cột zh) | 0 | 1.344 | 19 / 1.344 / 1.344 | 0 / 82 | 10/10 PASS |
| DungeonWithin 0.6.3 (91.389.555 B, md5 2940f75a…) | không tìm thấy | 0 | — | — | — | CHƯA ĐẠT, thoát 2, không ghi file (0 bảng trong 11 đơn vị; không giải mã) |

Kiểm rò rỉ ngôn ngữ: đối chiếu cột toàn diện (mỗi giá trị en phải bằng đúng cột
en và không trùng bất kỳ cột nào khác) — 0 trùng ở cả 4 build. Giới hạn: kiểm
đúng cột, không phải nhận diện ngữ nghĩa; ground truth độc lập là 4 giá trị mẫu
của nhiệm vụ (đều khớp). Heuristic `detect_language` không dùng làm bằng chứng:
nó gắn cờ nhầm 36 giá trị en thật ("War", "Top Hat"…) và từng bỏ sót rò rỉ thật
("Guerra"). Placeholder/thẻ: 0 lệch ở cả 4 build (785/208, 1.118/311, 424/590,
182/93). EK2: 100 bundle `Raw/ios` mã hóa không đọc được (không giải mã) — xuất
chỉ bao phủ đơn vị không mã hóa.

Hồi quy pipeline §4 (bridge.extract, en+zh, gộp 1 file; merged==tách 100%, #N=0
ở cả 3 game):

| Game | en trước → sau (xung đột) | zh trước → sau (xung đột) |
|---|---|---|
| 9 Kings v1.0.6 | 13.465 (17) → 6.694 (13) | 1.914 (0) → 2.176 (0) |
| Poly Bridge 3 v1.1.7 | 14.556 (293) → 11.065 (126) | 2.570 (224) → 2.350 (5) |
| Exploding Kittens 2 v0.0.18 | 17.240 (2) → 10.468 (2) | 7 (0) → không còn file zh (đúng: 7 key cũ là chuỗi tiếng Nhật `日本語` gán nhầm; EK2 không có dữ liệu zh) |

Nguyên nhân mọi lệch: key `.sNNNN` được thay bằng tên term thật (diff cũ→mới:
9K en mất 6.827 key/6.826 có hậu tố, đổi 15 giá trị gồm sửa rò rỉ `Nein`→`No`,
`Dollartegn`→`$$$`; EK2 en mất 8.116 key toàn hậu tố, thêm đúng 1.344 term);
lọc rác loại giá trị Unity khỏi đường generic (key `textasset.sN` vị trí dịch
chuyển, giá trị cũ `UnityEngine...` → chuỗi thật); trùng lặp giống hệt gộp lại
nên xung đột giảm (PB3 zh 224→5). Mọi term đều có mặt trong file gộp mới
(2.150/2.150, 2.348/2.348, 1.344/1.344).

## 5. Test + harness [ĐÃ ĐO THẬT]

- Smoke: 59/59. Full unittest: 1.846 chạy = 1.816 đạt / 25 lỗi / 3 fail / 2 bỏ
  qua (103,2s) — tập đỏ trùng hệt mốc trước sửa (2 fixture vắng đã biết),
  không đỏ mới; 35 test mới đều đạt.
- Harness rebuild 13/13 PASS, harness cổng giao hàng 13/13 PASS (Chromium thật,
  khớp mốc P0-1/P0-2). Harness gộp 1 file: §1 đạt; §2/§3/§4 không chạy được do
  fixture vắng (`EAC193F5…-resources.assets`, `en.json` của Joseph) và đường
  dẫn cũ đã chết (`/home/hatch/workspace/pb3_web/…`, `merge_5games.py`) — có sẵn
  từ trước, không liên quan sửa này; §5: c2 13/13, item8 đạt, 5 harness con
  FAIL do fixture/đường dẫn vắng từ trước (`zip/sample.ipa`, `bigtest.zip`,
  `/home/hatch/workspace/real_ipa/…`, output `extract_5games` chưa từng tạo),
  full-suite FAIL do đúng 28 đỏ đã biết.
- `exp/*.json|*.md`, `pb3/logs`, `pb3/reports` đã khôi phục byte-identical sau
  mọi lần chạy (kiểm bằng md5sum/diff với snapshot pristine).
- Safari/iSH: [CHƯA KIỂM CHỨNG] — không đổi UI nên không có bước thử mới.

## 6. Giới hạn còn lại

- Đường field-shape cho build có typetree là [HYPOTHESIS], chưa có build thật
  để kiểm chứng.
- Layout bảng khác chưa từng gặp sẽ bị từ chối (fail-closed); cần bằng chứng
  layout mới mới hỗ trợ.
- DungeonWithin: không trích được từ dữ liệu quét được; EK2: bundle mã hóa
  ngoài phạm vi (không giải mã).
- File 9 Kings v1.0.5 chưa có — mọi số 9 Kings là v1.0.6 (Chờ Joseph).
