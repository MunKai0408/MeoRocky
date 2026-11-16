# Phần (A) — Tách ngôn ngữ theo từng chuỗi + file sạch en.json/zh.json + sửa tab Dịch + dịch full không sót

Ngày: 2026-11-16. Repo: MunKai0408/MeoRocky, branch main. Site live: https://munkai0408.github.io/MeoRocky/

Harness: `exp/verify_analysis_translate_full.mjs` — **33/33 PASS, 0 FAIL** (một lần chạy sạch, 279,1 giây). Kết quả chi tiết: `exp/analysis_translate_full_results.json`. Đối chiếu file dịch thật: `exp/translate_recheck_results.md`. Rà soát end-to-end: `exp/translate_audit_results.md`.

## 1. Tóm tắt đã làm

1. **Tách ngôn ngữ theo từng chuỗi** — module mới `pb3/sdk/langdetect.py` (rule-based, chỉ thư viện chuẩn, chạy trong worker Pyodide, không mạng) + viết lại `_mb_generic_kv` trong `pb3/sdk/extraction.py`: mỗi chuỗi trong MonoBehaviour được gán đúng 1 ngôn ngữ (phát hiện mạnh → học vị trí trong nhóm lặp → mặc định en yếu), xuất 1 file riêng mỗi ngôn ngữ (`<đơn vị>.monobehaviour.<lang>.json`), key sạch không hậu tố `#N`. File gộp cũ vẫn xuất được khi chọn "tất cả ngôn ngữ" (ghi rõ trong UI đó là file gộp đa ngôn ngữ).
2. **Sửa tab Dịch** — thêm tham số ngôn ngữ nguồn `src` cho `bridge.translate_json` / `translate_json_google` (trước đây hard-code `en`); thêm select `#translate-from` (en mặc định / zh); lọc từng đoạn theo ngôn ngữ phát hiện (`_source_match`); TM chỉ dùng job cùng `from`, job cũ không `from` bị cách ly; chỉ ghi TM cho key thật sự đã dịch.
3. **Dịch full không sót** — tự kiểm tra trước khi cho tải (`_self_check` ở bridge + `state.translateCheck` ở UI, cổng tải chặn file thiếu/sai); báo cáo cuối mỗi lần dịch trung thực (tổng dòng nguồn / đã dịch / bỏ qua kèm lý do từng dòng / mất / ngôn ngữ khác bị đổi); dedupe chỉ gộp để gọi API, khi ghi output trải lại đủ mọi key.
4. **Định dạng file như en.json của Joseph (ý 7)** — bỏ `sort_keys=True` ở 2 chỗ dump file ảo trong `extraction.py`, giữ đúng thứ tự gốc trong game; đối chiếu thứ tự key với file mẫu (2.032 term chung, key term đầu tiên `Achievement_HisWinningness` khớp).
5. **Repack-ready + provenance cho (E)** — output key sạch không `#N`, 1 ngôn ngữ/file; bản dịch giữ trong phiên (`state.lastTranslated`, cả trong job IndexedDB) để nút "Ép lại vào file gốc" lấy thẳng; provenance đầy đủ (file game gốc, đơn vị `.assets`/`.bundle` chứa chuỗi, tên file trích xuất, cặp ngôn ngữ, số key) trong phiên + file sidecar `translated.vi.provenance.json` (xem §7).

## 2. Tích hợp theo kiến trúc web như thế nào (Joseph 20:50)

Không port nguyên si cách làm nào từ bên ngoài. Tách per-string và lọc dịch đều làm ở **tầng Python trong worker Pyodide** (cùng tiến trình với pipeline trích xuất) — module `sdk/langdetect.py` mới chỉ dùng thư viện chuẩn, không mạng, không model ngoài, RAM O(len(text)), phù hợp giới hạn RAM tab Safari. Lọc hàng đợi dịch cũng ở bridge Python, **trước khi gọi `gt.js`** — `gt.js` không đổi (vẫn nhận `sl` qua tham số như cũ). Tab Dịch giữ nguyên kiến trúc chunk 500 key + job IndexedDB + TM + resume, chỉ thêm trường `from` và cổng tải. Lựa chọn này khác với cách làm gốc của IPA Studio (parser UnityFS + SerializedFile bằng JS thuần, chạy trên main thread): phần (A) không dùng JS để tách ngôn ngữ vì pipeline trích xuất đã là Python trong worker — tách ở đó thì file sạch ra ngay từ lúc trích xuất, không cần một bước xử lý riêng trên main thread, và cùng một bộ phát hiện phục vụ cả hai việc (tách file + lọc dịch).

## 3. Cơ chế tách per-string

- **Phát hiện từng chuỗi** (`sdk/langdetect.py`): (1) kana dù ít vẫn → ja (kiểm tra TRƯỚC luật đa số Han); (2) script không phải Latin có ≥2 ký tự và ≥20% chữ cái → ngôn ngữ của script đó (mạnh); (3) script chiếm đa số tuyệt đối → mạnh; (4) dấu Latin đặc trưng; (5) từ chức năng; (6) còn lại Latin trơn → mặc định "en" (yếu, ghi rõ là heuristic); (7) không chữ cái → (None, "none"). Hỗ trợ 29 mã (en, zh, ja, ko, ru, uk, es, fr, pt, it, de, nl, pl, cs, sk, ro, sv, da, no, fi, tr, vi, id, el, ar, he, th, tl, hu).
- **Học vị trí** (`infer_positions`, `align_to_modal`): trong nhóm term của từng object và trong các lần xuất hiện của cùng key ở nhiều object — chỉ học từ phiếu mạnh, `min_votes=5`, `min_share=0.60`; nhóm ngắn hơn modal thì căn từng phần (vị trí nào MỌI cách xóa hợp lệ đều đồng ý thì gán, vị trí mơ hồ thì None — không đoán mò).
- **Term làm key** (giống file en.json của Joseph): object có cấu trúc [term + N bản dịch] hệ thống (học vị trí thành công) thì bản dịch lấy TERM làm key (vd `Achievement_HisWinningness` → `His Winningness` en, `陛下的勝利` zh); term ở slot 0 được tiêu thụ làm key, không xuất thành giá trị; chỉ áp khi chunk có 2 ≤ len ≤ modal_len.
- **Key trùng thật trong cùng ngôn ngữ:** hậu tố ổn định `.p<path_id>` (repack-ready cho (E)); giá trị giống hệt nhau thì gộp 1 bản, đếm `identical_dropped` (không mất chuỗi).
- **Phạm vi:** tách per-string chỉ áp cho MonoBehaviour generic; TextAsset/CSV/StringTable giữ nguyên (chúng đã có locale riêng theo file/cột).

Độ chính xác phát hiện từng chuỗi (validator `exp/validate_langdetect.py`, ground truth theo khe): Passpartout **81,0%** (en 97,8%, zh 99,8%, ko 99,9%, ru 99,9%), 9 Kings **62,4%** (en 95,8%, el 99,2%, ko 99,7%). Điểm yếu còn lại: no 4,9%, sk 7,2%, fi 20,0%, da 42,3% (ngôn ngữ gần nhau chia phiếu).

## 4. Bảng 5 game (trích xuất tách en/zh — CPython qua `bridge.extract`, locales ["en","zh"])

| Game | Kết quả | Tổng entry / file / thời gian | en (MonoBehaviour) | zh (MonoBehaviour) | Mẫu chuỗi thật |
|---|---|---|---|---|---|
| 9 Kings (9kings.ipa, 173MB) | PASS | 41.110 / 50 / 9,4s | 13.401 (resources 11.356) | 1.888 (resources 1.888) | en: `Throw 9 cards into the pit in a single run.` · zh: `陛下的勝利` (key `Achievement_HisWinningness`) |
| data.unity3d (590MB, UnityFS v5.x) | PASS | 5.308 / 79 / 9,1s | 5.307 (resources 3.834) | 1 | en: `$(PRODUCT_NAME) saves images to photo library` |
| Exploding Kittens 2 (290MB) | PASS | 39.645 / 13 / 11,7s | 17.245 (resources 14.939, sharedassets0 2.290) | 7 | en: `Duel offline with up to four crafty AI opponents` |
| Poly Bridge 3 (482MB) | PASS | 37.270 / 87 / 14,8s | 14.660 (resources 6.261, sharedassets1 7.830) | 2.039 (resources 2.039) | en: `Allows the player to pause the active simulation, leaving moving elements frozen` · zh: `开始模拟` (key `BINDING_START_SIM`) |
| Passpartout (159,7MB) | PASS | 15.066 / 20 / 4,9s | 2.710 (resources 2.707) | 1.715 (resources 1.715) | en: `The cap'n is impressed!` · zh: `丹青妙手，神来之笔啊！` |

Ghi chú theo từng game (nguyên nhân riêng, không gộp chung):

- **9 Kings:** text chính trong `resources.assets` (465 MonoBehaviour + 5 TextAsset) — bảng term I2Languages 1 term + 29 bản dịch; file tách en bắt đầu `AfterGameUISO.s1` (thứ tự object gốc), key term đầu tiên `Achievement_HisWinningness` khớp file mẫu Joseph; `English.json` (StringTable) bắt đầu `Farm`, `Castle`, `Concabulator` — thứ tự gốc, không phải A–Z. Tổng dòng Anh của game (đếm động từ file tách + `English.json`): file tách en 13.401 entry (trong đó `resources.monobehaviour.en.json` 11.356) + `English.json` 100 entry. Tổng dòng Trung: file tách zh 1.888 + `Chinese.json` 26.
- **data.unity3d:** bundle độc lập 590MB — trích xuất qua CPython 9,1s (đường streaming trên trình duyệt trước đây 137,5s, không chết tab; cảnh báo >400MB vẫn bật). Chỉ có 1 entry zh (game này gần như toàn tiếng Anh).
- **EK2:** text trong `sharedassets0.assets` (2.290 entry en) + `resources.assets` (14.939 entry en); chỉ 7 entry zh (game này gần như không có tiếng Trung).
- **PB3:** text chính trong `resources.assets` (CSV chỉ là phụ — `English.json` 2.361 entry từ CSV cộng thêm ngoài file tách MonoBehaviour; `Chinese.json` 2.361 entry). Tổng dòng Anh: file tách en 14.660 + CSV 2.361 = 17.021. Tổng dòng Trung: file tách zh 2.039 + CSV 2.361 = 4.400.
- **Passpartout:** không có `English.json` riêng — text đa ngôn ngữ chung trong MonoBehaviour (`resources.assets` 20,1MB = 2.837 MonoBehaviour + 1.117 TextAsset). Tổng dòng Anh (đếm động, định nghĩa của game này): **1.091** (983 dòng #2 + 108 dòng hải tặc mà chính key gốc đã là tiếng Anh). Tổng dòng Trung: **1.744 dòng thật** (1.764 đếm theo khe #14/#15, thừa 20 dòng không phải tiếng Trung — xem §5).

**Độ nhiễm trong file tách** (checker `exp/check_split_impurity.py`, chỉ nhóm kiểm chứng được chắc chắn bằng khe): Passpartout en sai 16/2.693, zh sai 18/1.715 (toàn kanji Nhật); 9 Kings en sai 20/11.352, zh sai 125/1.888 (toàn kanji Nhật). **#N = 0 ở mọi file tách.** Phần không kiểm chứng được (nhóm ngắn + object không phải bảng term) vẫn có thể còn nhiễm — ghi rõ, không giấu.

**Thứ tự key (ý 7):** mọi file tách giữ thứ tự chèn (thứ tự gốc trong game); file tách en 9 Kings có 2.032 term chung với file mẫu của Joseph (2.086 key), chỉ thiếu 1 key `Skin_` rỗng/hỏng ở vị trí 1699.

## 5. Lỗi dịch — tái hiện trước khi sửa, kiểm chứng sau sửa

### Lỗi 1: dịch sai ngôn ngữ (bridge hard-code nguồn + TM nhiễm độc)

**Tái hiện** (`exp/repro_translate_bug_tmp.mjs` → `exp/translate_bug_repro.json`, mock Google): signature cũ `translate_json_google(json_text, to='vi', credit=True)` KHÔNG có tham số src; mock nhận đúng 1 batch với `sl="en"` chứa cả 8 chuỗi không nằm trong từ điển (Đức, Bồ Đào Nha, Nga, phồn thể, giản thể, Anh, Nhật, Hàn) — 7/7 chuỗi KHÔNG phải tiếng Anh bị gửi đi như tiếng Anh; output có "bản dịch" cho cả chuỗi Trung/Nga.

**Bằng chứng trên file thật** (Joseph 21:24 — xem `exp/translate_recheck_results.md`): Đức `Wie Extraordinär!` → `Wie Extrardär!`; Nga `Терпеть не могу вторичность.` → `Bạn không cần phải làm gì cả.`; Thụy Điển `Enastående!` → `Vui vẻ!`; key gốc đổi 257/537 (48%).

**Cách sửa:** thêm tham số `src` cho cả hai hàm bridge + lọc từng đoạn (`_source_match`) + TM chỉ dùng job cùng `from` + chỉ ghi TM cho key thật sự đã dịch. Kiểm chứng sau sửa (mock Google, 20/20 mục PASS ở section 2): src=en chỉ gửi đúng 1 chuỗi Anh, skipped=7; src=zh chỉ gửi đúng 2 chuỗi Trung, skipped=8; dedupe trải lại đủ key; src==to trả lỗi.

### Lỗi 2: file dịch làm mất key (7.408/15.868)

Nguyên nhân: nút "Tải bản dịch" tải nội dung ô output mà KHÔNG kiểm tra tính đầy đủ — job dừng giữa chừng (hết quota / ngắt mạch Google) rồi file thiếu vẫn tải được. **Cách sửa:** tự kiểm tra trước khi cho tải (bridge `_self_check` + UI `state.translateCheck` + cổng tải chặn) + báo cáo trung thực cuối mỗi lần dịch. Kiểm chứng trên đúng file fixture (section 4, mock Google): **15.868/15.868 key, thứ tự khớp**; dòng Anh 1.091 → đã dịch 1.079, 12 dòng chưa dịch đều có lý do đúng (lệch khe/dấu/dữ liệu — liệt kê từng dòng trong `translate_recheck_results.md` §4), 0 dòng sai; dòng Trung thật 1.744/1.744 (100%); 0 dòng phát hiện mạnh không phải nguồn mà bị dịch; self-check 0/0.

### Lỗi 3 (phát hiện thêm khi đối chiếu): thẻ đóng Unity không được che

Regex placeholder chỉ che thẻ MỞ — dòng chỉ còn thẻ đóng (`</b>`, `</size>`) vẫn có chữ Latin (`size`, `b`) nên bị coi là tiếng Anh yếu rồi gửi đi dịch, làm đổi chuỗi rich-text Hàn/Trung. **Đã sửa:** che cả thẻ đóng + phát hiện trên dòng đã che (cả hai đường Google và offline). Khóa bằng test mới (`test_mask_line_masks_closing_tags`, `test_rich_text_cjk_kept_byte_identical`).

## 6. Số đã dịch / bỏ qua (fixture Passpartout, mock Google, đếm động)

| | Nguồn en | Nguồn zh |
|---|---|---|
| Tổng dòng đếm được | 1.091 | 1.764 theo khe (1.744 dòng Trung thật) |
| Đã dịch (đổi giá trị) | 1.079 | 1.744 |
| Chưa dịch có lý do | 12 (lệch khe: Pháp/Tây Ban Nha/Ba Lan/Trung/Nhật ở khe #2; lưới dấu: `Trés bien!`, `Génial!`; dữ liệu JSON manifest) | 20 (toàn chuỗi giao diện/tên kỹ thuật tiếng Anh ở khe va chạm #14/#15, vd `Normal`, `Pressed`, `SendMessageUpwards`) |
| Chưa dịch không lý do | **0** | **0** |
| Mất key | **0** | **0** |
| Ngôn ngữ khác bị đổi sai (phát hiện mạnh) | **0** | **0** |

Còn lại (giới hạn đã biết, ghi rõ): 1.986 key ở khe không phải tiếng Anh trong nhóm đối thoại bị đổi khi dịch en — nhưng kiểm tra theo từng dòng thì 0 dòng phát hiện MẠNH bị dịch sai; đó toàn là chuỗi Latin ngắn không từ chức năng/dấu (vd `Extraordinaire!`, `Fiasko.`) mà bộ phát hiện rule-based không bắt được. Khi dịch zh: 18 key bị đổi, toàn chuỗi Nhật thuần kanji không kana. Đường dịch chính (file tách sạch) không gặp vấn đề này.

## 7. Repack-ready + provenance cho (E)

- **Repack-ready:** file tách key sạch không `#N` (trùng thật thì `.p<path_id>` ổn định), 1 ngôn ngữ/file, giữ thứ tự gốc — (E) đưa thẳng vào bước ép lại mà không cần chuyển đổi thêm. File `.unity3d` (data.unity3d 590MB): xuất en/zh sạch chạy được qua đường streaming (CPython 9,1s; trình duyệt 137,5s trước đây, không chết tab).
- **Giữ trong phiên:** `state.lastTranslated` = `{name, from, to, translated, keys, at, provenance}`; job Google lưu thêm `job.provenance` vào IndexedDB (tải lại trang vẫn còn). Nút "Ép lại vào file gốc" của (E) lấy thẳng từ phiên, khỏi tải lên lại.
- **Provenance:** `translateProvenanceFor(name, from, to, keyCount)` suy ra từ `bridge.extract` (mỗi file kèm `source`: đơn vị Unity chứa chuỗi) + tên file game đang nạp (`state.file.name`). `origin`: `"extract"` (nạp từ file vừa trích xuất) / `"upload"` (tải file JSON lên, chỉ biết tên file) / `"manual"` (dán tay — (E) sẽ hỏi lại). Nút "Tải provenance (.json)" tải sidecar `translated.vi.provenance.json` (`format: "beartea-translation-provenance/1"`) ghi rõ file game gốc, đơn vị `.assets`/`.bundle`, tên file trích xuất, cặp ngôn ngữ, số key. Cùng cổng tải với bản dịch. File dịch chính vẫn giữ dạng phẳng sạch như file en.json mẫu.

## 8. Harness (33/33 PASS) + hồi quy

| Section | Kết quả |
|---|---|
| 1. Unit Python | 39 test OK (`test_langdetect` + `test_translate_source`); độ chính xác phát hiện: Passpartout 81,0%, 9 Kings 62,4% |
| 2. Bridge Pyodide (mock Google) | 7/7 PASS — src=en chỉ gửi Anh, src=zh chỉ gửi 2 chuỗi Trung, byte-identical, self-check, dedupe trải lại đủ key, src==to lỗi |
| 3. Trích xuất 5 game | 3/3 PASS — 5 game ok, 0 key #N trong file tách, độ nhiễm + thứ tự term đo được |
| 4. Fixture recheck Passpartout | 6/6 PASS — đủ key, đủ dòng Anh/Trung (đếm động), 0 ngôn ngữ khác bị đổi sai, file dịch cũ ghi nhận 7.408/15.868 |
| 5. UI Chromium thật 390px | 10/10 PASS — select `#translate-from` (en/zh, mặc định en), dòng info trung thực, đủ key, giữ trong phiên + provenance, cổng tải chặn khi thiếu 2 key, cho tải khi check sạch, sidecar provenance tải đúng, 0 page error |
| 6. Hồi quy | module-worker 17/17, C2 13/13, C9 16/16, item8 static 0 fails, full suite Python **1.696 test OK** (skipped=2; tăng từ 1.692 nhờ 4 test mới) |

Lưu ý hồi quy: `run_tests.py --tier full` (threaded runner) chết xác định tại test #897 trong sandbox này — lỗi runner/môi trường có từ trước, KHÔNG do thay đổi (A); test đó chạy riêng qua unittest 0,001s OK; full suite xác minh bằng `unittest discover` (1.696 OK) làm chuẩn.

## 9. File đã đổi (đợt này)

Site (map tới gốc repo): `pb3/sdk/langdetect.py` (mới), `pb3/sdk/extraction.py`, `pb3/sdk/discovery.py` (LOCALE_BUILTIN +da/fi/ro/cs/sk/no/el/he/tl/hu), `pb3/sdk/translate.py` (che cả thẻ đóng), `pb3/bridge.py`, `app.js`, `index.html`, `sw.js` (CACHE_VERSION v15-20261116, precache thêm `langdetect.py`), `pb3/files.json` (91→92), `pb3/tests.json` (160→162), `pb3/tests/test_langdetect.py` (mới), `pb3/tests/test_translate_source.py` (mới). Cập nhật kỳ vọng đếm 91→92, 160→162, v14→v15 trong toàn bộ harness cũ (verify_limit_2gb, verify_patch01_web, verify_phase1_ci, check_item8_static, check_analysis_c6/c7/c8/c9_static, verify_analysis_c8, verify_analysis_c9, verify_module_worker).

Mới trong `exp/`: `verify_analysis_translate_full.mjs` (harness (A)), `extract_5games.py` (driver trích xuất 5 game), `check_split_impurity.py` (checker độ nhiễm + thứ tự key), `validate_langdetect.py` (validator độ chính xác phát hiện), `analysis_translate_full_results.json` (kết quả harness), `translate_recheck_results.md`, `translate_audit_results.md`, `analysis_translate_full_results.md` (báo cáo này), `out/translate_full/ui_translate_390px.png` (ảnh chụp UI tab Dịch 390px).

KHÔNG commit: file game trong `~/workspace/real_ipa/`, 2 file fixture trong `~/workspace/user/files/`, file tạm `*_tmp.mjs` trong `exp/`.

## 10. Việc còn kẹt / chưa kiểm chứng (không giấu)

1. Vỏ trang raw ~372KB vẫn vượt 100KB (item6, đã biết từ trước — app.js monolith; đợt này tăng thêm do code mới). Gzip ~108KB.
2. Độ nhiễm còn lại trong file tách (Latin yếu gán nhầm en; kanji Nhật gán nhầm zh) — xem §4; cần ngữ cảnh (khe/vị trí) hoặc từ điển lớn hơn mới giảm thêm được.
3. `read_extracted` giới hạn 4MB — file trích xuất lớn có thể bị đọc thiếu (chưa đo được file nào vượt).
4. `translateJobStore.save(job)` lỗi IndexedDB thì catch im lặng — mất khả năng resume mà không báo.
5. Không kiểm tra ngược ngôn ngữ của kết quả provider trả về — fallback trả sai vẫn tính là xong.
6. Google Translate trên mạng thật + nút Lưu/Chia sẻ trên Safari thật vẫn cần Joseph thử trên thiết bị (sandbox Chromium không có mạng ngoài; Chromium không phải Safari).
7. Full suite qua threaded runner chết ở #897 trong sandbox (lỗi runner có từ trước — xem §8).

## 11. Link kiểm chứng

- Commit (đợt này): **803674ac18eaf6c2e8358ff6df7a2536768a65ed** — "Phần (A): tách ngôn ngữ theo từng chuỗi + file sạch en/zh + sửa tab Dịch + dịch full không sót (harness 33/33, full suite Python 1696 passed)" — https://github.com/MunKai0408/MeoRocky/commit/803674ac18eaf6c2e8358ff6df7a2536768a65ed (29 file; 3 báo cáo .md đẩy sau ở commit tiếp theo).
- Site live: https://munkai0408.github.io/MeoRocky/ (GitHub Pages build lại từ main sau khi đẩy — nếu vừa đẩy xong mà trang chưa đổi thì chờ 1–2 phút rồi tải lại, hoặc kiểm tra raw bên dưới).
- Kiểm chứng trên main (raw.githubusercontent.com/MunKai0408/MeoRocky/main/…): `pb3/sdk/langdetect.py`, `pb3/bridge.py`, `app.js`, `exp/verify_analysis_translate_full.mjs`, `exp/analysis_translate_full_results.json` (total 33, passed 33), `pb3/files.json` (92 file, có `langdetect.py`), `pb3/tests.json` (162 file), `sw.js` (`CACHE_VERSION = "v15-20261116"`).
