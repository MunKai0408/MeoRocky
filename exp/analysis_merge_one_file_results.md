# Gộp thành 1 file — trích xuất gộp thành đúng 1 file en.json/zh.json (Joseph 23:35)

Ngày: 2026-11-16. Repo: MunKai0408/MeoRocky, branch main. Site live: https://munkai0408.github.io/MeoRocky/

Harness: `exp/verify_analysis_merge_one_file.mjs` — **27/27 PASS, 0 FAIL** (chạy từng section, gộp kết quả; xem `exp/analysis_merge_one_file_results.json`). Driver: `exp/check_merge_fixture.py` (fixture), `exp/merge_5games.py` (5 game).

## 1. Tóm tắt đã làm

Chế độ xuất "Gộp thành 1 file" ở tab Trích xuất (checkbox `#extract-merge-one`, **tick mặc định**):

- Khi chọn 1 ngôn ngữ (en hoặc zh): xuất **đúng 1 file** ngôn ngữ đó — `en.json` / `zh.json` — gộp toàn bộ chuỗi của ngôn ngữ đó từ **mọi nguồn** (mọi `.assets`/`.bundle`/StringTable/CSV) vào một file phẳng `{key: value}`, một ngôn ngữ duy nhất, đúng dạng file `en.json` mẫu của Joseph (giữ thứ tự gốc, mỗi dòng một cặp `"key": "value"`, không `sort_keys`, không hậu tố `#N`).
- Khi chọn "Cả Anh + Trung": ra **2 file** (`en.json` + `zh.json`), mỗi file vẫn gộp đủ.
- Bỏ tick: giữ nguyên file tách per-unit như cũ (`resources.monobehaviour.en.json`…) — chế độ cũ không mất.
- File config/plist không phải chuỗi ngôn ngữ (BillingMode, BuildSettings, Info…) **giữ nguyên như cũ**, xuất riêng.
- Trùng key: đo trước trên 5 game rồi chốt quy tắc — giống hệt giá trị thì gộp 1 bản (đếm `duplicates_identical`); khác giá trị thì **giữ bản xuất hiện đầu tiên + liệt kê toàn bộ xung đột** (key + giá trị + nguồn từng bản) vào `extraction_report.txt`/`.json`.
- Provenance cho (E): nguồn của từng key ghi vào sidecar `<lang>.provenance.json` (`format: "beartea-merge-provenance/1"`, kèm `merged_from` + `conflicts`) trong `extract.zip`; file gộp chính vẫn phẳng sạch, không nhét provenance vào key.

## 2. Tích hợp theo kiến trúc web như thế nào (Joseph 20:50)

Không port nguyên si cách làm nào từ bên ngoài. Gộp làm ở **tầng Python trong worker Pyodide**, ngay sau vòng export của pipeline, trước khi ghi file:

- `sdk/extraction.py`: `ExtractionRequest.merge_one_file` (mặc định `False` để mọi hành vi cũ/test/harness cũ không đổi) + hàm thuần `merge_language_outcomes()` (gộp dict theo thứ tự pipeline, first-wins, đếm trùng giống hệt, liệt kê xung đột) + `_apply_merge()` (chạy trước `_export`) + `_export` ghi file gộp đúng tên `<lang>.json`, bỏ qua checkpoint cho outcome gộp (dẫn xuất, không có file nguồn thật để khóa sha — chạy lại parse lại rồi gộp lại), ghi sidecar provenance + mục `merged_files` vào report; `sdk/exporters.py`: `report_text()` thêm mục gộp.
- `bridge.py`: `extract(..., merge_one_file=None)` — `None` = `False` (giữ API cũ); trả thêm `merged_files` cho UI.
- `app.js` + `index.html`: checkbox mặc định tick, gửi làm tham số thứ 4 cho `bridge.extract`; kết quả hiện dòng "Gộp thành 1 file: en.json — X key từ N file (…)" ngay trên bảng file. File dịch mặc định của tab Dịch (`defaultTranslateFile`) vốn đã chọn file locale en — nay mặc định là `en.json` gộp.
- Định dạng file: giữ `export_key_value_json` sẵn có (indent=2, `ensure_ascii=False`, không sort) — **đúng indent của file mẫu Joseph gửi** (2 khoảng trắng; chữ "indent=1" trong dặn 20:58 chỉ áp cho file ảo trung gian trong `_build_layers`, không phải file xuất cuối).
- RAM tab Safari: gộp là nối dict trong worker (O(tổng key), không copy file game); file gộp lớn nhất đo được 972KB (EK2 en) — dưới giới hạn 4MB của `read_extracted` nên vẫn nạp được vào tab Dịch.

Quyết định mặc định (ghi rõ theo yêu cầu): **UI mặc định BẬT** (checkbox tick sẵn khi chọn 1 ngôn ngữ hay cả hai); **API mặc định TẮT** (`merge_one_file=False`) để mọi lời gọi cũ — gồm harness (A)/(B)/(C) và batch — không đổi hành vi. Chỉ gộp khi có tập locale cần gộp (lọc en/zh); không lọc ngôn ngữ thì không gộp.

## 3. Cơ chế gộp

1. Pipeline chạy như cũ, ra các outcome per-unit (file tách (A) + StringTable + CSV + config).
2. `_apply_merge`: với mỗi locale trong tập lọc, nối `kv` của các outcome cùng locale **theo đúng thứ tự outcome trong `result.files`** (thứ tự nguồn phát hiện); key trong mỗi outcome giữ thứ tự chèn gốc. Outcome gộp thay thế các outcome cùng locale tại vị trí outcome đầu tiên của locale đó.
3. Trùng key:
   - Giá trị giống hệt nhau từng byte → gộp 1 bản, đếm `duplicates_identical` (không mất chuỗi).
   - Giá trị khác nhau → giữ bản đầu, ghi `conflicts[]` = `{key, kept: {value, source}, dropped: {value, source}}` (giới hạn 500 mục chi tiết trong report/sidecar, tổng `conflicts_total` giữ đủ).
4. `_export`: outcome gộp xuất thành `<lang>.json` (không qua tên hiển thị `English.json`); `merge_sources` = `{key: nguồn}` ghi vào `<lang>.provenance.json`; `merged_files` vào `extraction_report.json` + mục chữ trong `extraction_report.txt`; `total_entries` tính lại theo file sau gộp (key gộp + config).

## 4. Bảng 5 game (trích xuất locales ["en","zh"], gộp bật — CPython qua `bridge.extract`)

File gộp **khớp 100%** hợp các file tách per-unit ở cả 5 game (thứ tự key + giá trị, đối chiếu bằng code trong `exp/merge_5games.py`); **0 key `#N`** ở mọi file gộp; sidecar + report đều khớp; file config giữ nguyên tên + nội dung.

| Game | en.json (gộp) | zh.json (gộp) | Trùng giống hệt đã gộp | Xung đột (giữ bản đầu) | Config giữ nguyên |
|---|---|---|---|---|---|
| 9 Kings (173MB) | **13.465 key** (647KB, từ 8 file, tổng 13.501) | **1.914 key** (130KB, từ 2 file) | en 19 · zh 0 | en **17** · zh 0 | 40 file |
| data.unity3d (590MB) | **4.963 key** (265KB, từ 78 file, tổng 5.307) | **1 key** (29 byte, từ 1 file) | en 36 · zh 0 | en **308** · zh 0 | 0 file |
| Exploding Kittens 2 (290MB) | **17.240 key** (972KB, từ 3 file, tổng 17.245) | **7 key** (255 byte, từ 1 file) | en 3 · zh 0 | en **2** · zh 0 | 9 file |
| Poly Bridge 3 (482MB) | **14.556 key** (726KB, từ 57 file, tổng 17.010) | **2.570 key** (132KB, từ 2 file, tổng 4.389) | en 2.161 · zh 1.595 | en **293** · zh **224** | 28 file |
| Passpartout (159,7MB) | **2.710 key** (164KB, từ 2 file) | **1.715 key** (117KB, từ 1 file) | 0 · 0 | **0** · **0** | 17 file |

Ghi chú theo từng game (nguyên nhân riêng, không gộp chung):

- **9 Kings:** 17 xung đột en toàn key chung chung `textasset.sN` (vd `textasset.s2`: giữ `"Selected"`, bỏ `"Highlighted"` từ `sharedassets2.monobehaviour.en.json`) — các object MonoBehaviour không tên ở nhiều bundle khác nhau. File gộp en chứa **2.032/2.086 key mẫu** của Joseph (như (A) đã đo), key đầu file gộp là `FullScreenPassRendererFeature.s1` (thứ tự nguồn phát hiện), key mẫu đầu tiên có mặt ở vị trí 391.
- **data.unity3d:** 308 xung đột en toàn `textasset.sN` (190 key khác nhau; `textasset.s1` xung đột 66 lần) — cùng key ở hàng chục bundle level (`level100…level105…`, vd giữ `"Horizontal"`, bỏ `"This door can be opened elsewhere."`, `"wind"`). Game này gần như toàn tiếng Anh (zh chỉ 1 key).
- **EK2:** chỉ 2 xung đột en (`textasset.s1`, `textasset.s4`); zh chỉ 7 key (game gần như không có tiếng Trung).
- **PB3:** trùng giống hệt nhiều nhất (en 2.161, zh 1.595 — cùng key CSV xuất hiện ở nhiều bundle với cùng giá trị). 293 xung đột en chủ yếu là tên field âm thanh trùng nhau giữa các bundle xe (`textasset.StartSound`: giữ `"sfx_vehicle_ambulance_start"`, bỏ `"sfx_vehicle_bigwheelbuggy_start"`…). 224 xung đột zh là key CSV (`BINDING_SPLIT_JOINT`, `KEY_UP_ARROW`…): bản giữ lấy từ file CSV (`PB3_LOC_DEFAULT.csv`, vd `"分离接点"`, `"上方向键"` — đúng tiếng Trung), bản bỏ lấy từ `resources.assets` lại là **tiếng Nhật gán nhầm vào zh** (`"分割点"`? không — vd `"複数選択"`, `"上矢印"`) — tức thứ tự pipeline (CSV trước) đã cứu đúng bản ở đây, nhưng đó là may mắn của thứ tự, không phải luật: xung đột CSV-khác-nguồn vẫn cần (E) xem lại khi ép (xem §7.3).
- **Passpartout:** sạch hoàn toàn — 0 trùng, 0 xung đột, gộp = nối thẳng 2 file en (2.707 + 3) và 1 file zh.

## 5. Tiêu chí đạt — fixture resources.assets của Joseph (23:38)

Chạy trích xuất trên đúng file fixture, chọn tiếng Anh → **đúng 1 file `en.json`** (9.753 key, 435KB, 0 key `#N`, key đầu `AfterGameUISO.s1`, 5 file config giữ nguyên, sidecar 9.753 key). Chọn tiếng Trung → **đúng 1 file `zh.json`** (3.380 key, 245KB, 0 `#N`, 3.355/3.380 giá trị chứa chữ Hán, key đầu `Achievement_HisWinningness`).

**Về tiêu chí "đúng 2.086 key, khớp 100% với file mẫu": KHÔNG ĐẠT — và đã chứng minh bằng code là không thể đạt từ fixture này, không phải do bước gộp làm mất:**

| Đo bằng code | Kết quả |
|---|---|
| Key mẫu có trong file gộp en.json | **560 / 2.086**, thứ tự tương đối giữ nguyên (key mẫu đầu tiên có mặt `Achievement_KingoftheHill` ở vị trí 231) |
| Chuỗi tiếng Anh của term mẫu có trong file gốc không? (trích xuất **không lọc**, 66.011 chuỗi thô) | `"His Winningness"`, `"Win one game."`, `"A Little Treat"` đều **KHÔNG có** trong file |
| Term mẫu có trong file dưới bất kỳ ngôn ngữ nào? (hợp term qua **mọi** file tách 29 ngôn ngữ) | 2.064 / 2.086 — **22 key vắng mặt hoàn toàn** trong file |
| Term có trong file nhưng không có bản tiếng Anh | **1.504 key** (560 + 1.504 + 22 = 2.086) |
| Nguyên nhân (đọc thẳng cấu trúc I2Languages) | Bảng I2Languages trong build này (Unity 6000.3.8f1, 20MB) gồm các chunk `[term + 30 bản dịch]` — **30 khe dịch, không khe nào là tiếng Anh** (vd chunk đầu: s2 tiếng Việt, s3/s4 Bồ Đào Nha, s5 Nhật, s6/s7 Trung, s8 Đức… s31 Thụy Điển). Tiếng Anh là ngôn ngữ gốc, không lưu trong bảng ở build này; file mẫu 2.086 key của Joseph lấy từ **build khác** (bản IPA 1.0.8 / nguồn khác) nên không thể đối chiếu tuyệt đối bằng file này. |
| Bước gộp có làm mất chuỗi nào không? | **Không** — file gộp en.json trùng khớp 100% key + giá trị với file tách per-unit cùng đợt (đối chiếu bằng code); 0 key mất thêm. |

**Độ nhiễm thừa hưởng từ bước tách (A) trên build này (ghi rõ, không giấu):** trong 560 key mẫu có mặt ở file gộp en, **347 key có giá trị bằng đúng bản dịch tiếng Việt của Joseph** (vd `Achievement_TowerDefense` → `"Thủ Tháp"`, `Achievement_BeefedUp` → `"Todo Poderoso"` tiếng Tây Ban Nha) — tức chuỗi tiếng Việt/Tây Ban Nha bị gán nhầm vào file en ở bước tách per-string, do các chunk I2Languages dài ngắn khác nhau (29/30/31 chuỗi) làm căn chỉnh vị trí (`align_to_modal`) lệch ở một số chunk. Bước gộp giữ nguyên 100% nội dung file tách nên giữ luôn độ nhiễm này; sửa nó là việc của bước tách (A), không thuộc phạm vi gộp — xem §7.2.

## 6. Harness (27/27 PASS) + hồi quy

| Section | Kết quả |
|---|---|
| 1. Unit Python + tĩnh | 10/10 test `test_merge_one_file` OK; files.json=92, tests.json=163, sw.js v18-20261116, không còn v17/162 sót trong harness cũ |
| 2. Fixture acceptance | 6/6 PASS — en 1 file 9.753 key, zh 1 file 3.380 key, mẫu 560/2.086 + lý do không thể 100%, độ nhiễm 347 key ghi rõ, mặc định không merge vẫn ra file tách |
| 3. 5 game thật | 6/6 PASS — 5 game ok, file gộp khớp hợp file tách, 0 `#N`, sidecar + report khớp, config giữ nguyên |
| 4. UI Chromium thật 390px | 4/4 PASS — checkbox có sẵn + tick mặc định; trích xuất en trên fixture thật ra `en.json` + dòng "Gộp thành 1 file", file dịch mặc định là `en.json`; bỏ tick trích xuất lại ra file tách per-unit, không có `en.json`; 0 page error, không tràn ngang ở cấp document |
| 5. Hồi quy | module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33, (B) **25/25** (xem §6.1), (C) 26/26, item8 static 0 fails, full suite Python **1.706 test OK** (tăng từ 1.696 nhờ 10 test mới) |

### 6.1 (B) 24/24 → 25/25 (thay đổi số đếm, ghi rõ)

Lúc chạy hồi quy lần đầu, (B) rớt 1 mục `e2e_extract_tiny_zip` — **không phải do code gộp**: fixture e2e `/tmp/mod_tiny.zip` (zip nhỏ `game.json` 3 entry, tạo tay từ đợt (B)) đã mất khi `/tmp` bị dọn sau khởi động lại, mà harness (B) chỉ trông chờ file có sẵn. Đào sâu thêm thấy 2 lỗi harness: (1) file `/tmp` không được tạo lại khi thiếu; (2) mục `e2e_extract_tiny_zip` **chỉ được ghi khi FAIL, không bao giờ được ghi PASS** — nên kết quả FAIL cũ bị giữ lại mãi trong `results.json` do cơ chế gộp kết quả cũ, dù file đã có lại. Đã sửa cả hai trong `verify_analysis_mod_search.mjs`: tự tạo lại fixture đúng hình dạng đã ghi (`game.json` = `{"setting": "Cài đặt", "play": "Chơi", "quit": "Thoát"}`) khi thiếu, và ghi PASS cho mục này khi trích xuất zip nhỏ chạy được. (B) chạy lại đầy đủ: **25/25** (25 = 24 mục cũ + mục này nay được ghi cả khi PASS). Kỳ vọng (B) trong harness (C) và harness gộp cập nhật 24/24 → 25/25 theo. Hành vi sản phẩm của (B) không đổi.

## 7. File đã đổi (đợt này)

Site (map tới gốc repo): `pb3/sdk/extraction.py` (merge_one_file + sidecar + report), `pb3/sdk/exporters.py` (mục gộp trong `report_text`), `pb3/bridge.py` (tham số `merge_one_file`, trả `merged_files`), `app.js` (checkbox + dòng báo gộp), `index.html` (checkbox + ghi chú), `sw.js` (CACHE_VERSION v17 → v18), `pb3/tests.json` (162 → 163), `pb3/tests/test_merge_one_file.py` (mới, 10 test). Cập nhật kỳ vọng đếm 162 → 163, v17 → v18 trong toàn bộ harness cũ (verify_limit_2gb, verify_patch01_web, verify_phase1_ci, check_item8_static, check_analysis_c6/c7/c8/c9_static, verify_analysis_c8, verify_analysis_c9, verify_analysis_translate_full, verify_analysis_mod_search, verify_analysis_reselect — trong đó verify_limit_2gb / verify_patch01_web chỉ kiểm files.json=92 nên không đổi).

Mới trong `exp/`: `verify_analysis_merge_one_file.mjs` (harness gộp, 27 mục), `check_merge_fixture.py` (kiểm chứng fixture), `merge_5games.py` (driver 5 game), `analysis_merge_one_file_results.json` (kết quả harness), `analysis_merge_one_file_results.md` (báo cáo này), `out/merge_one_file/` (fixture.json, summary.json, output gộp/tách từng game, `ui_merge_390px.png`).

KHÔNG commit: file game trong `~/workspace/real_ipa/`, 2 file fixture trong `~/workspace/user/files/`, `/tmp/mod_tiny.zip` (fixture tạm của harness (B), nay harness tự tạo lại khi thiếu).

## 8. Việc còn kẹt / chưa kiểm chứng (không giấu)

1. **Tiêu chí 2.086 key khớp 100% không đạt được từ fixture này** (xem §5): file gốc thiếu bản tiếng Anh của 1.504 term + thiếu hẳn 22 term. Muốn đối chiếu tuyệt đối cần file `resources.assets` đúng build với file mẫu (bản IPA 1.0.8) hoặc file mẫu lấy từ chính build này — cần Joseph xác nhận build nào là chuẩn.
2. **Độ nhiễm 347 key tiếng Việt/Tây Ban Nha trong file en gộp của fixture** (xem §5): lỗi căn chỉnh vị trí của bước tách (A) trên chunk dài ngắn khác nhau; cần sửa (A) (học vị trí theo từng độ dài chunk / dùng thứ tự khe cố định của I2Languages) ở đợt sau — ngoài phạm vi gộp.
3. **Xung đột key chung chung (`textasset.sN`, tên field âm thanh, key CSV trùng nguồn)** — quy tắc giữ-bản-đầu là xác định nhưng bản giữ phụ thuộc thứ tự pipeline; (E) khi ép cần đọc `conflicts` trong report/sidecar để quyết bản nào đúng cho từng game (đặc biệt PB3 zh: bản giữ từ CSV là đúng, bản bỏ là tiếng Nhật gán nhầm).
4. Vỏ trang raw ~372KB vẫn vượt 100KB (item6, đã biết từ trước — đợt này tăng thêm chút do code mới).
5. Bảng kết quả trích xuất ở 390px vẫn bóp chữ/nút khi tên file dài (xem ảnh `ui_merge_390px.png` — nút "Dịch sang tiếng Việt" bị cắt ở mép phải): **có từ trước**, không do đợt này (không đổi markup/CSS của bảng, chỉ thêm 1 dòng `<p>` phía trên); chưa sửa.
6. Google Translate trên mạng thật + nút Lưu/Chia sẻ trên Safari thật vẫn cần Joseph thử trên thiết bị (sandbox Chromium không có mạng ngoài; Chromium không phải Safari).
7. Checkpoint không lưu bản gộp (cố ý — xem §2): chạy lại với merge bật sẽ parse lại từ đầu rồi gộp lại, chậm hơn chút nhưng đúng.

## 9. Link kiểm chứng

- Commit (đợt này): **d0defb87ec29e324360225a4f650083e2b719933** — "Gộp thành 1 file: trích xuất gộp thành đúng 1 file en.json/zh.json mỗi ngôn ngữ (harness 27/27, full suite Python 1706 passed, (A) 33/33, (B) 25/25, (C) 26/26)" — https://github.com/MunKai0408/MeoRocky/commit/d0defb87ec29e324360225a4f650083e2b719933 (24 file; báo cáo .md này đẩy sau ở commit tiếp theo).
- Site live: https://munkai0408.github.io/MeoRocky/ (GitHub Pages build lại từ main sau khi đẩy — nếu vừa đẩy xong mà trang chưa đổi thì chờ 1–2 phút rồi tải lại, hoặc kiểm tra raw bên dưới).
- Kiểm chứng trên main (raw.githubusercontent.com/MunKai0408/MeoRocky/main/…): `pb3/sdk/extraction.py`, `pb3/bridge.py`, `app.js`, `index.html`, `exp/verify_analysis_merge_one_file.mjs`, `exp/analysis_merge_one_file_results.json` (total 27, passed 27), `pb3/tests.json` (163 file, có `test_merge_one_file.py`), `sw.js` (`CACHE_VERSION = "v18-20261116"`).
