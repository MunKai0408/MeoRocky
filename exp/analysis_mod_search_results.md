# PHẦN (B) — Mod tìm theo chữ + nhập file .json: kết quả kiểm chứng

Ngày: 2026-11-16. Head trước khi đẩy: `a45b1ec` (phần (A) xong + live).
Harness: `exp/verify_analysis_mod_search.mjs` — **24/24 PASS** (chạy đầy đủ
1 lượt, không chạy từng phần ghép lại). Kết quả chi tiết:
`exp/analysis_mod_search_results.json`. Ảnh UI 390px:
`exp/out/mod_search/ui_mod_search_390px.png`.

## 1. Đã làm gì

Tab Mod có thêm mục **"Tìm theo chữ + nhập file .json (phần B)"**
(`index.html`, `app.js`), đặt trước mục "Kế hoạch Mod (plan JSON — mục 8)".

### 1.1. Tìm theo chữ — hoạt động thế nào

- Nguồn dữ liệu: file JSON đang mở trong Mod (`state.modJson` — object phẳng
  `{key: value}`, giữ đúng thứ tự key gốc, không `sort_keys`, giống file
  en.json mẫu của Joseph).
- Tìm chuỗi con trong **cả key lẫn value** (mặc định), có chọn "Chỉ key" /
  "Chỉ value". **Mặc định không phân biệt hoa/thường**; tick "Phân biệt
  hoa/thường" để tìm đúng hoa/thường.
- Kết quả hiện đủ số đếm thật ("Tìm thấy N kết quả cho … trong M entry"),
  chỉ vẽ 200 thẻ đầu (gõ cụ thể hơn để thu hẹp). Mỗi thẻ kết quả hiện
  **key + giá trị hiện tại + nguồn** (tên file nguồn rút gọn, vd
  `resources.assets`).
- Sửa trực tiếp ngay trong danh sách: gõ giá trị mới vào ô "Sửa thành" rồi
  bấm **Lưu** từng dòng — không cần nhớ id. Entry đã sửa được đánh dấu
  "(đã sửa)", dòng thông tin đếm số entry đã sửa.
- Sửa xong đi tiếp được ngay bằng 2 đường đã có sẵn, không viết lại:
  - **"Đưa vào ô Bản dịch (để lập plan)"**: chép toàn bộ JSON đang mở
    (kèm các entry đã sửa, giữ thứ tự key gốc, indent 1) vào ô Bản dịch
    của plan Mod (mục 8) → bấm "Lập kế hoạch" như thường; patch pack C7
    đi theo plan đó.
  - **"Tải file .json đã sửa"**: tải về `<tên gốc>.mod.json`, giữ đủ key
    và đúng thứ tự key gốc.

### 1.2. Nhập file .json — hoạt động thế nào

- Nút **"Nhập file .json…"** mở hộp chọn file (chấp nhận `.json`, chọn được
  nhiều file cùng lúc). File được đọc bằng `File.text()` ở main thread rồi
  parse bằng `modParseJsonObject`: chỉ nhận object phẳng `{key: value}`;
  JSON hỏng hoặc mảng thì báo lỗi rõ, không im lặng.
- File > 20MB hỏi xác nhận trước khi mở (giữ toàn bộ JSON trong RAM của
  tab — trên iPhone có thể nặng); file > 50.000 entry vẫn mở được, tìm
  kiếm quét đủ mọi entry.
- Ba nguồn mở file JSON trong Mod (cùng vào `state.modJson`):
  1. **File trích xuất vừa rồi** (dropdown + nút "Lấy file trích xuất đã
     chọn"): nạp qua worker `read_extracted` — nội dung file nằm trong FS
     worker sau extract — kèm provenance trích xuất (giống tab Dịch).
  2. **Bản dịch xong trong phiên** (nút "Lấy từ bản dịch"):
     `state.lastTranslated` của mục (A) kèm provenance của nó.
  3. **Nhập từ máy** (xem trên).
- Xử lý provenance khi nhập:
  - Chọn kèm file sidecar `*.provenance.json` (định dạng
    `beartea-translation-provenance/1` của mục A) thì **giữ nguyên
    provenance** (khớp theo tên file, vd `translated.vi.provenance.json`
    đi với `translated.vi.json`, hoặc lấy sidecar duy nhất đã chọn).
  - Không có sidecar thì dòng thông tin ghi rõ **"không rõ nguồn gốc
    (không có provenance đi kèm)"**, provenance ghi `origin: "upload"`
    (chỉ biết tên file) — **không bịa** nguồn.
  - Sidecar sai định dạng / JSON hỏng thì bị bỏ (trả `null`), không crash.

### 1.3. Cách tích hợp trên web (khác gì cách gốc, vì sao)

- Tìm/sửa chạy **hoàn toàn ở main thread trên object JSON đã nằm trong
  bộ nhớ phiên** — KHÔNG qua worker/Pyodide, KHÔNG đọc lại file game.
  Lý do: file JSON trích xuất/dịch/nhập đã là text phẳng trong RAM (kể cả
  file lớn nhất thật: `resources.monobehaviour.en.json` của EK2 ~15k entry
  vẫn nhẹ); đưa qua worker chỉ thêm chuyển buffer + boot Pyodide mà không
  thêm gì. Chỉ bước nạp file trích xuất mới qua worker (`read_extracted`)
  vì file đó nằm trong FS worker sau extract.
- Không có tham chiếu "cách gốc" ngoài repo để port theo: đây là tính năng
  UI mới viết theo kiến trúc web hiện tại (module worker + bridge Pyodide
  + tab UI), tái dùng `read_extracted`, `translateProvenanceFor`,
  `state.lastTranslated`, `download()` và plan Mod mục 8 đã kiểm chứng.
- RAM tab Safari: JSON đang mở giữ 1 bản trong RAM (không nhân bản thêm);
  file .unity3d 590MB không bao giờ được nạp nguyên vào Mod — chỉ file
  JSON trích xuất ra từ nó (vài nghìn entry) mới được mở. Cảnh báo >400MB
  + streaming chống OOM của trích xuất vẫn giữ nguyên ở tab Trích xuất.
- Sửa lỗi UI phát hiện khi kiểm chứng: kết quả tìm kiếm ban đầu vẽ dạng
  bảng 5 cột làm nút "Lưu" bị bóp thành chữ xếp dọc ở 390px (xem ảnh chụp
  lần đầu) — đã đổi sang **thẻ xếp dọc** (key + value + ô sửa + nút Lưu +
  nguồn), vừa 390px, không tràn ngang, không pageerror.

## 2. Bảng 5 game (file thật)

File dùng kiểm chứng: `resources.monobehaviour.en.json` trích xuất thật của
từng game (thư mục `exp/out/extract_5games/`, trích xuất lại bởi harness
(A) ngày 2026-11-16). Từ khóa tìm thử: **"setting"** trên cả 5 game
(+ "play" cho Passpartout vì "setting" = 0 ở đó — không phải lỗi, game này
thật sự không có chuỗi nào chứa "setting").

| Game | File game thật | File JSON trích xuất | Entry | "setting" | Từ khóa khác | Kết quả |
|---|---|---|---|---|---|---|
| 9 Kings | 9kings.ipa 173MB | resources.monobehaviour.en.json | 11.356 | **74** | — | PASS — mẫu `Default Localization Settings.s1` có trong kết quả |
| data.unity3d | data.unity3d 590MB | resources.monobehaviour.en.json | 3.834 | **79** | — | PASS — mẫu `NPSettings.s2` có trong kết quả (chỉ mở file JSON đã trích xuất, không nạp file 590MB vào Mod) |
| Exploding Kittens 2 | exploding_kittens2.ipa 290MB | resources.monobehaviour.en.json | 14.939 | **113** | — | PASS — mẫu `PhotonServerSettings.s2` có trong kết quả |
| Poly Bridge 3 | polybridge3.ipa 482MB | resources.monobehaviour.en.json | 6.261 | **204** | — | PASS — mẫu `MAINMENU_SETTINGS` có trong kết quả |
| Passpartout | passpartout.ipa 160MB | resources.monobehaviour.en.json | 2.707 | **0** | "play" = **68** | PASS — "setting" = 0 là đúng (không có chuỗi nào chứa "setting"); "play" = 68 |

Số kết quả đếm bằng UI trên Chromium thật khớp đúng số đếm bằng Python từ
cùng file (kỳ vọng ghi cứng trong harness, đếm trước từ file thật).
Không game nào FAIL; không có nguyên nhân riêng nào cần ghi.

Kiểm chứng sửa + đi tiếp (9 Kings, `Default Localization Settings.s1`):
`"English"` → `"CÀI ĐẶT KIỂM CHỨNG B"` ngay trong danh sách → thông tin
hiện "đã sửa 1 entry" → "Đưa vào ô Bản dịch" → ô plan-translations có đủ
11.356 key và giá trị mới đúng key → tải `resources.monobehaviour.en.mod.json`
(11.356 key, giá trị mới đúng key, key đầu tiên giữ đúng thứ tự gốc).

## 3. Harness (B): 24/24 PASS

`node exp/verify_analysis_mod_search.mjs` — chạy trên Chromium thật 390px
(HTTP local phục vụ cây `site/`, đúng code sẽ đẩy; KHÔNG phải Safari thật).

| Section | Nội dung | Kết quả |
|---|---|---|
| 1. Hàm thuần (3 mục) | `modParseJsonObject` giữ thứ tự key, từ chối mảng/JSON hỏng; `modSearchEntries` không phân biệt hoa/thường mặc định, đúng key/value/scope, tìm được cả value số; `modProvenanceFromSidecar` đúng format thì giữ, sai thì null | 3/3 |
| 2. UI 390px — bảng 5 game (10 mục) | Nhập file thật từng game + tìm "setting" đúng số; "play" = 68 ở Passpartout; sửa 1 entry; sửa vào được plan; tải file đã sửa đúng nội dung + thứ tự key; 0 pageerror | 10/10 |
| 3. End-to-end qua worker (2 mục) | Trích xuất zip nhỏ thật (`game.json`, 3 entry) rồi "Lấy file trích xuất đã chọn" nạp đúng file + provenance extract, "setting" = 1; "Lấy từ bản dịch" (phiên) nạp đúng 3 entry, "cài" = 1 | 2/2 |
| 4. Provenance + lỗi (4 mục) | Nhập kèm sidecar giữ provenance extract (`9kings.ipa::…/resources.assets`); không sidecar ghi rõ "không rõ nguồn gốc"; JSON hỏng/mảng báo lỗi rõ; ô tìm trống báo rõ; "xin" không phân biệt = 1, phân biệt = 0 | 4/4 |
| 5. Hồi quy (5 mục) | module-worker 17/17; C2 13/13; C9 16/16; item8 static 0 fails; full suite Python 1.696 test OK | 5/5 |

## 4. Hồi quy

- (B) section 5: module-worker **17/17**, C2 **13/13**, C9 **16/16**,
  item8 static **0 fails**, full suite Python **1.696 test, OK**.
- (A) chạy lại đầy đủ sau khi sửa (B): **33/33 PASS**
  (`exp/analysis_translate_full_results.json` — chạy nền độc lập, xong
  cùng đợt; kết quả này cũng được đẩy kèm).
- Không bỏ qua test nào để xanh. Không dùng COI. Không đụng cây iSH.
- `files.json` = 92 file, `tests.json` = 162 test — **không đổi** (phần (B)
  không thêm/bớt file Python hay test nào). `CACHE_VERSION` trong `sw.js`
  nâng `v15-20261116` → **`v16-20261116`** (đổi file nên đổi cache theo quy
  ước); đã cập nhật kỳ vọng v15 → v16 trong toàn bộ harness cũ:
  `check_item8_static.py`, `check_analysis_c7_static.py`,
  `check_analysis_c8_static.py`, `check_analysis_c9_static.py`,
  `verify_analysis_c8.mjs`, `verify_analysis_c9.mjs`
  (`check_analysis_c6_static.py`, `verify_limit_2gb.mjs`,
  `verify_patch01_web.mjs`, `verify_phase1_ci.mjs`,
  `verify_analysis_translate_full.mjs` không chứa kỳ vọng v15 nên không
  cần sửa; đã grep kiểm tra toàn bộ `exp/`).

## 5. File đổi trong đợt đẩy

- `app.js` — mục Mod tìm theo chữ + nhập file .json (hàm thuần
  `modParseJsonObject` / `modSearchEntries` / `modProvenanceFromSidecar`,
  `state.modJson`, 3 nguồn nạp, sửa trong danh sách, đưa vào plan, tải
  `.mod.json`).
- `index.html` — mục UI phần (B) trong tab Mod + CSS thẻ kết quả xếp dọc.
- `sw.js` — `CACHE_VERSION` v16-20261116.
- `exp/verify_analysis_mod_search.mjs` (mới), `exp/analysis_mod_search_results.md`,
  `exp/analysis_mod_search_results.json` (mới).
- `exp/check_item8_static.py`, `exp/check_analysis_c7_static.py`,
  `exp/check_analysis_c8_static.py`, `exp/check_analysis_c9_static.py`,
  `exp/verify_analysis_c8.mjs`, `exp/verify_analysis_c9.mjs` — chỉ đổi kỳ
  vọng CACHE_VERSION v15 → v16.
- `exp/analysis_translate_full_results.json` — kết quả chạy lại (A) 33/33
  sau khi sửa (B).

Không đụng `.github/workflows/`. Không commit file game thật.

## 6. Việc còn kẹt / chưa kiểm chứng

- **FAIL duy nhất còn lại (đã biết từ trước, không thuộc phần B):** item6
  vỏ trang raw ~372KB > 100KB (gzip đạt). Không sửa trong phần này.
- **Chưa kiểm chứng trên Safari thật / iPhone của Joseph:** toàn bộ trên
  là Chromium sandbox + file trích xuất thật. Cần Joseph thử trên Safari
  thật: nhập 1 file `.json` (vd `resources.monobehaviour.en.json`), tìm
  "setting", sửa 1 entry, đưa vào plan; và thử nút "Lấy file trích xuất
  đã chọn" sau khi trích xuất xong trên máy.
- Giới hạn đã biết: tìm kiếm chỉ quét file JSON đang mở (không tìm xuyên
  nhiều file cùng lúc); mỗi lần chỉ mở 1 file .json chính (chọn nhiều file
  thì chỉ mở file đầu tiên và báo rõ); kết quả chỉ hiện 200 thẻ đầu.
