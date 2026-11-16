# Lỗi "Trích xuất text lỗi: Lỗi không rõ" + nút Chọn/Bỏ chọn tất cả — kết quả kiểm chứng

Ngày: 2026-11-16. Harness mới: `exp/verify_extract_unknown_error.mjs` (**8/8 PASS**) +
`exp/verify_extract_deselect_all.mjs` (**5/5 PASS**).
Code **CHƯA ĐẨY** — coordinator đẩy sau khi kiểm chứng.
Báo cáo nút bỏ chọn: `exp/extract_deselect_all_results.md` (file riêng, xem §7).

## 1. Tóm tắt

- Banner đỏ "Trích xuất text lỗi: Lỗi không rõ" (Joseph 15:42, Safari thật) đã tái hiện
  được bằng fixture và **sửa xong cả 2 tầng**: (a) báo lỗi thật — banner nay hiện đúng
  lý do (mã + message + decision + số mục đã bỏ qua), không bao giờ còn "Lỗi không rõ"
  khi bridge trả `ok:false`; (b) sửa hành vi lọc — entry không xác định được locale
  (vd file config) **không còn bị loại** khi lọc en/zh.
- Thông tin mới của Joseph (15:45 — cả en+zh lẫn en-only đều lỗi) đã kiểm chứng cả
  hai giả thuyết (xem §2): **lọc filter ĐÚNG là thủ phạm trên fixture, nhưng KHÔNG
  giải thích được lỗi en-only trên IPA thật** — trên fixture, en-only ra English.json
  bình thường. Nghĩa là trên IPA thật còn một lỗi nữa ở parse bundle (kích thước/bộ
  nhớ/version/lỗi parse) hoặc bẫy ASK; chỉ lần chạy tới trên Safari thật — với báo
  lỗi mới — mới phân biệt được. Phần báo lỗi (§3) là để phục vụ đúng việc đó.
- Phát hiện thêm khi sửa lọc: **lọc chọn file `source_paths` bị rò** — chọn chỉ bundle
  vẫn ra settings.json (xem §4). Đã sửa cùng đợt (giữ đúng đặc tả B2a/B2b cũ).

## 2. Nguyên nhân thật (đã kiểm chứng)

### 2a. Giả thuyết filter — ĐÚNG trên fixture (nghi phạm số 1 xác nhận)

Tái hiện bằng fixture "chỉ config không locale" (settings.json + BuildSettings.json +
config.plist, mô phỏng IPA thật 13:22 ra 36 file config):

| Fixture | Filter | Trước fix | Sau fix |
|---|---|---|---|
| Chỉ config không locale | en+zh BẬT | `ok:false, decision=none, files=[], errors=[]` → banner "Lỗi không rõ" | `ok:true, decision=done, files=[BuildSettings.json, settings.json]` |
| Chỉ config không locale | en-only | (tương tự — 0 file) | `ok:true, files=[BuildSettings.json, settings.json]` |
| Đầy đủ (bundle + settings) | en+zh BẬT | `files=[English.json, Chinese.json]`, settings.json bị loại với lý do `locale không xác định không phải en/zh` | `files=[settings.json, English.json, Chinese.json]` |
| Đầy đủ | en-only | `files=[English.json]` | `files=[settings.json, English.json]` |

Cơ chế (đọc code trực tiếp, `site/pb3/sdk/extraction.py`): lọc locale có **hai chỗ**,
và chúng mâu thuẫn nhau:

- **Trước trọng tài** (dòng ~702): chỉ loại ứng viên có locale **ĐÃ RÕ** mà không thuộc
  tập lọc; comment ghi rõ "Entry chưa rõ locale (None) vẫn đi tiếp".
- **Vòng export** (dòng ~852, trước fix): `if locales is not None and loc not in locales:`
  — `loc=None` thì `None not in {"en","zh"}` là True nên **loại cả entry không rõ
  locale**, với lý do `locale không xác định không phải en/zh`. Nhánh checkpoint
  (dòng ~813) có cùng lỗi (`loc_code None` bị loại).

**Đối chiếu ghi chú UI (mục B của brief):** trước fix, ghi chú dưới select ngôn ngữ
trong `index.html` và code vòng export **thống nhất với nhau** (cả hai đều nói entry
không rõ ngôn ngữ bị bỏ qua) — mâu thuẫn nằm **bên trong code**: comment + hành vi
của lọc trước trọng tài nói "giữ", còn vòng export thì "loại". Dòng ghi chú mà brief
mục B trích ("entry không rõ ngôn ngữ (vd file config) vẫn được giữ lại") là **bản
sau khi sửa** — tôi đã cập nhật ghi chú cùng lúc sửa code, vì ghi chú cũ mô tả đúng
hành vi lỗi. Sau fix, cả ba nơi thống nhất: **giữ entry không rõ locale**, chỉ loại
entry có locale ĐÃ XÁC ĐỊNH mà không thuộc {en, zh}. (Lệnh mới của parent ghi đè đặc
tả 13:27 — xem §6.)

### 2b. Giả thuyết parse bundle thật trên Safari — CHƯA loại trừ, là nghi phạm còn lại

Joseph thử **en-only cũng lỗi** trên IPA thật, trong khi trên fixture en-only ra
English.json bình thường (U3). Suy ra trên IPA thật, bundle localization không cho ra
English.json — pipeline chết trước khi tới bundle, hoặc bundle parse lỗi, hoặc
TextAsset en kẹt ASK. Đã rà các đường lặng lẽ còn sót (để biết lần chạy tới sẽ thấy gì):

- `_expand_unity` (dòng ~216): mọi đường lỗi đều ghi `notes` (parse lỗi kèm mã, entry
  không phải SerializedFile, bundle 0 TextAsset) — đã hết lặng lẽ từ fix en.json.
- `_build_layers` nhánh unity: entry Unity > 64MB (`limits.max_entry_bytes`) thì ghi
  skipped + log rõ (`file Unity N byte > giới hạn entry 67108864 byte — ...`) — đã hết
  lặng lẽ. Bundle localization >64MB "là không tưởng" nhưng IPA thật chưa đo được.
- `parse_bundle` (`sdk/unity.py`): chỉ hỗ trợ UnityFS **v6/v7/v8** (`SUPPORTED_BUNDLE_VERSIONS`),
  SerializedFile v15–v23; bundle **v9** → `E_UNSUPPORTED_FORMAT`; compression lạ →
  `E_UNSUPPORTED_FORMAT`. Các lỗi này đi qua `_expand_unity` → ghi vào `skipped`/log,
  **không** vào `errors` — nên banner cũ không bao giờ hiện chúng.
- Bẫy ASK (`exp/enjson_fix_results.md` §5): TextAsset đơn lẻ (vd tên `en` trong
  resources.assets) kẹt ASK giữa json_text 0,71 / csv_table 0,61 → decision `ask`,
  web không có `ask_fn` → 0 file. Trước fix, nhánh này return mà không append error nào.

Kết luận: **dù bundle thật lỗi theo đường nào, lý do cũng chỉ nằm trong `skipped`/`log`
mà banner cũ không đọc** — đó là lý do phần báo lỗi (§3) là ưu tiên số 1 và phải xong
dù kết luận cuối về bundle/filter là gì. Lần chạy tới trên Safari thật của Joseph sẽ
cho biết chính xác (xem §8).

### 2c. Vì sao banner chỉ hiện "Lỗi không rõ"

- `bridge.extract` trả `{"ok": bool(r.ok), ...}` — khi `r.ok` False theo đường thành
  công thì JSON chỉ có `decision/total_entries/files/skipped/log/errors/zip_path`,
  **không có `error`/`code`**. `bridge._err` (đường exception) thì CÓ trường `error` —
  nên "Lỗi không rõ" chứng tỏ pipeline **chạy xong bình thường** (không crash) nhưng
  trả 0 file, không phải crash/memory.
- `run_extraction`: `ok=True` chỉ gán ở cuối khi `decision="done"`; mọi return sớm
  (`not probed`, kẹt ASK, `not chosen`, `not result.files`) đều giữ `ok=False` mà
  **không append error nào** (trừ nhánh `E_LOCALE_NOT_FOUND`).
- `app.js` `runOp`: `throw new Error(parsed.error || parsed.code || "Lỗi không rõ")`
  — không ai đọc `parsed.errors` hay `parsed.skipped`.

## 3. Cách sửa (từng file)

### `site/pb3/sdk/extraction.py`

- **Lọc locale — giữ entry không rõ locale (2 chỗ):**
  - Vòng export: `if locales is not None and loc not in locales:` →
    `if locales is not None and loc is not None and loc not in locales:` (kèm comment
    giải thích + ghi rõ lỗi 2026-11-16).
  - Nhánh checkpoint/reused: `if locales is not None and loc_code not in locales:` →
    thêm `loc_code is not None and`.
- **Mọi return sớm đều append error thật (không còn `ok:false` mà `errors` rỗng):**
  - `not probed` → `E_NO_CANDIDATE`: "không còn ứng viên nào sau bước lọc: bộ lọc
    ngôn ngữ en/zh đã loại N mục…; thử tắt bộ lọc ngôn ngữ rồi trích xuất lại"
    (khi không lọc thì message khác: "không tìm thấy ứng viên trích xuất nào…").
  - Kẹt ASK (`ask_groups and not chosen`) → `E_ASK_NEEDED`: "cần chọn tay: N nhóm
    chưa quyết được vì các định dạng/plugin ngang điểm (locale: …); bản web chưa hỗ
    trợ chọn tay ở bước này — thử chọn đúng file ở chế độ Chọn tay".
  - `not chosen` → `E_NO_CANDIDATE`: "không ứng viên nào vượt ngưỡng trọng tài".
  - `not result.files` → `E_NO_FILES`: "không trích xuất được file nào (quyết định:
    none; đã bỏ qua N mục; trong đó M mục do bộ lọc ngôn ngữ en/zh; thử tắt bộ lọc…)".

### `site/pb3/bridge.py` — `extract()`

- Khi `r.ok` False thì tổng hợp trường **`error`**: lấy message đầu tiên trong
  `r.errors` (kèm `[CODE]` + " (+N lỗi khác)" nếu còn); khi `errors` rỗng thì dựng từ
  decision + số skipped (trong đó bao nhiêu do bộ lọc ngôn ngữ) + gợi ý xem
  `extraction_report.txt` / tắt bộ lọc. `error` luôn có nội dung khi `ok:false`.

### `site/app.js` — `runOp` (dùng chung cho mọi thao tác)

- Khi `parsed.ok === false`: message = `parsed.error` + từng dòng `parsed.errors`
  (code + message, xuống dòng — `#error` có `white-space: pre-wrap` nên hiện thành
  từng dòng trong banner) + dòng `(quyết định: …; đã bỏ qua N mục — xem
  extraction_report.txt)`. Các thao tác khác không có `errors`/`decision` thì hành vi
  giữ nguyên (guard `|| []`). Không đụng tab Font hay logic font.
- **Nút Chọn/Bỏ chọn tất cả (xem §7):** `renderExtractFileList()` đồng bộ `disabled`
  của 2 nút theo chế độ manual; handler 2 nút cập nhật trực tiếp
  `state.extractSelected` (Set) rồi `renderExtractFileList()`; handler
  `btn-extract-run` chặn khi manual + Set rỗng (xem §7).

### `site/index.html`

- Ghi chú dưới select ngôn ngữ: "entry ngôn ngữ khác sẽ bị bỏ qua… — entry không rõ
  ngôn ngữ (vd file config) **vẫn được giữ lại**" (khớp code sau fix; xem §2a).
- Thêm 2 nút **ngay trên** `#extract-file-list`: `btn-extract-select-all` ("Chọn tất
  cả") + `btn-extract-deselect-all` ("Bỏ chọn tất cả"), mặc định `disabled` (chế độ
  tự động). Ghi chú chế độ chọn tay có thêm câu về 2 nút này.

## 4. Phát hiện thêm: lọc chọn file `source_paths` bị rò (đã sửa cùng đợt)

Khi kiểm chứng B2a/B2b sau khi sửa lọc locale: chọn chỉ bundle vẫn ra `settings.json`,
chọn chỉ `resources.assets` + lọc zh vẫn ra `settings.json` (trước đây bị lọc locale
che mất vì settings không rõ locale). Nguyên nhân: lọc `source_paths` chỉ loại entry
khỏi `inv` (inventory), nhưng vòng sinh ứng viên quét **lớp gốc** (toàn bộ ZIP) nên
file thường KHÔNG được chọn vẫn lọt vào làm ứng viên (cả vòng `list_candidates` lẫn
vòng sniff; vòng "ứng viên theo tên" duyệt `inv` đã lọc nên không rò).

Sửa trong `run_extraction` (`extraction.py`): thêm `_is_selected(full)` — ứng viên
phải đúng bằng đường dẫn đã chọn hoặc là entry con `w::...` của đường dẫn đã chọn
(TextAsset trong bundle đã chọn, file trong container đã chọn); áp ở cả hai vòng
sinh ứng viên. Lớp Unity ảo chỉ dựng từ `inv` đã lọc nên TextAsset của bundle không
chọn không tới được đây. Entry không được chọn đã có sẵn trong `skipped` với lý do
"không được chọn" nên không mất dấu. **Lưu ý:** cây iSH (`~/workspace/pb3_fix/pb3`,
chỉ đọc) có thể có cùng lỗi này — ghi nợ để port sang patch03, không sửa ở đợt này.

## 5. Harness mới + kết quả

### `exp/verify_extract_unknown_error.mjs` — **8/8 PASS** (Node Pyodide + trình duyệt thật 390px)

| Ca | Nội dung | Kết quả |
|---|---|---|
| U1 | Fixture chỉ config không locale + lọc en+zh BẬT | ra `settings.json` + `BuildSettings.json` (trước fix: 0 file) |
| U2 | Fixture chỉ config không locale + lọc en-only | ra 2 file config |
| U3 | Fixture đầy đủ + lọc en-only | `English.json` + `settings.json`, không `Chinese.json` |
| U4 | Ca 0 file thật sự (chọn resources.assets + lọc zh) | `ok:false`, `error="[E_NO_CANDIDATE] không còn ứng viên nào sau bước lọc: bộ lọc ngôn ngữ zh đã loại 1 mục…"`, không "Lỗi không rõ" |
| U5 | Bẫy ASK (chọn resources.assets, không lọc) | `decision=ask`, `error="[E_ASK_NEEDED] cần chọn tay: 1 nhóm chưa quyết được…"`, không "Lỗi không rõ" |
| U6 | Bất biến: mọi kết quả `ok:false` đều có `error` thật | 2/2 kết quả kiểm đều có error khác rỗng, khác "Lỗi không rõ" |
| D1 | Trình duyệt thật 390px: fixture chỉ config + lọc mặc định | kết quả hiện `settings.json` + `BuildSettings.json`, banner `#error` ẩn |
| D2 | Trình duyệt thật 390px: chọn tay chỉ level1.unity3d (de.json) + lọc mặc định | banner hiện: `Trích xuất text lỗi: [E_NO_CANDIDATE] không còn ứng viên nào sau bước lọc: bộ lọc ngôn ngữ en/zh đã loại 1 mục (xem skipped trong báo cáo); thử tắt bộ lọc ngôn ngữ rồi trích xuất lại` + dòng `(quyết định: none; đã bỏ qua 5 mục — xem extraction_report.txt)` |

Kết quả JSON: `exp/extract_unknown_error_results.json` (harness tự ghi).

## 6. Harness cũ chạy lại + thay đổi kỳ vọng

**Tất cả xanh:** `verify_enjson.mjs` **6/6**, `verify_extract_select.mjs` **8/8**,
`verify_translate_connect.mjs` **12/12**, `verify_translate_batch.mjs` **21/21**.

Thay đổi kỳ vọng (do **lệnh mới của parent ghi đè đặc tả 13:27** — entry không rõ
locale được GIỮ khi lọc, không sửa code để ép xanh):

- `verify_extract_select.mjs`:
  - B1a (lọc en): `["English.json"]` → `["English.json", "settings.json"]`.
  - B1b (lọc zh): `["Chinese.json"]` → `["Chinese.json", "settings.json"]`.
  - B1c (lọc cả hai): `["Chinese.json", "English.json"]` → `["Chinese.json", "English.json", "settings.json"]`;
    settings **không còn** trong skipped (trước đây kỳ vọng lý do `không xác định không phải en/zh`).
  - B2b (chọn resources.assets + lọc zh): giữ kỳ vọng `decision=none`, 0 file — **tăng
    cường** thêm kiểm `bridge.error` chứa "không còn ứng viên nào sau bước lọc" và
    `errors[0].code === "E_NO_CANDIDATE"`.
  - B3 (chọn tay, bỏ bundle): giữ kiểm bundle skipped "không được chọn" — **tăng cường**
    thêm kỳ vọng ra `["settings.json"]` (settings được giữ nên đợt này ra file thay vì 0 file).
  - B2a không đổi (`["English.json"]`) — đúng nhờ fix rò rỉ §4; không có fix §4 thì B2a
    đã đỏ vì lọt `settings.json`.
- `verify_enjson.mjs`:
  - A1: thêm kiểm `settings.json` xuất hiện khi lọc en+zh.
  - A2a (bundle hỏng + lọc): thêm kiểm `settings.json` được giữ nên decision nay là
    `done` (trước đây `none` vì TextAsset en kẹt ASK + settings bị lọc); các kiểm bundle
    hỏng/huge.bundle/log giữ nguyên.

## 7. Nút "Bỏ chọn tất cả" (Joseph 15:48 — gộp cùng đợt)

- **Thiết kế đã chọn:** 2 nút "Chọn tất cả" / "Bỏ chọn tất cả" đặt ngay trên danh sách
  file; **chỉ bật ở chế độ chọn tay**, ở chế độ tự động thì `disabled` cả 2 (checkbox
  đang disabled nên bấm cũng vô nghĩa — không tự chuyển sang manual, để user chủ động
  đổi chế độ). Trạng thái `disabled` đồng bộ trong `renderExtractFileList()` nên đổi
  radio là cập nhật ngay, kể cả khi danh sách chưa tải (mặc định `disabled` trong HTML).
- **Bấm thì cập nhật trực tiếp `state.extractSelected` (Set)** rồi vẽ lại bằng
  `renderExtractFileList()` — đúng quy ước hiện tại (Set là nguồn thật duy nhất cho
  `source_paths`, không đọc DOM lúc chạy).
- **Bỏ chọn hết rồi bấm "Trích xuất":** đã kiểm chứng code `btn-extract-run` — Set rỗng
  (`[]`) sẽ bị bridge coi là `None` (= tự động) nên nút "Bỏ chọn tất cả" sẽ vô tác dụng
  nếu không chặn. Nay **chặn ngay tại UI**: manual + Set rỗng → banner
  "Chưa chọn file nào để trích xuất — tick ít nhất 1 file trong danh sách, hoặc chuyển
  về chế độ Tự động." và **không gửi gì lên bridge** (không chạy ngầm).
- Harness riêng: `exp/verify_extract_deselect_all.mjs` — **5/5 PASS** (trình duyệt thật
  390px): S1 tự động → 2 nút disabled; S2 manual → 2 nút bật, checkbox 5/5 tick; S3 bỏ
  tick tay 1 file (4/5) → "Bỏ chọn tất cả" (0/5) → "Chọn tất cả" (5/5); S4 bỏ chọn
  hết + Trích xuất → banner "Chưa chọn file nào…", `extract-result` vẫn trống; S5 chọn
  tất cả + Trích xuất → ra `English.json`, banner ẩn. Báo cáo chi tiết (file riêng):
  `exp/extract_deselect_all_results.md` (+ `.json`).

## 8. Nợ + cái gì còn chờ Joseph kiểm chứng trên Safari thật

- **Nợ (ghi lại, không đợi):** tab **Chẩn đoán + nút Tự kiểm tra** (đợt phân tích) càng
  cần sau lỗi này — lỗi này cho thấy khi pipeline trả 0 file mà không có lý do thì
  không chẩn đoán tiếp được trên máy user. Chưa làm ở đợt này.
- **Chờ Joseph (IPA 9 Kings thật 141MB, Safari thật):**
  1. Trích xuất với lọc mặc định (Cả Anh + Trung): kỳ vọng ra file (ít nhất config +
     English.json nếu bundle mở được). Nếu vẫn 0 file, **banner nay phải hiện đúng lý
     do** — chụp lại toàn bộ banner gửi về (mã lỗi + decision + số mục đã bỏ qua) để
     phân biệt: bundle >64MB, bundle v9/compression lạ (`E_UNSUPPORTED_FORMAT`), hay
     kẹt ASK (`E_ASK_NEEDED`), hay hết ứng viên (`E_NO_CANDIDATE`).
  2. Thử nút "Bỏ chọn tất cả" / "Chọn tất cả" ở chế độ chọn tay + bấm Trích xuất khi
     rỗng (kỳ vọng banner "Chưa chọn file nào…").
  3. Tải `extract.zip` về, mở `extraction_report.txt` — kiểm dòng `Unity: …localization-locales_assets_all.bundle -> N TextAsset` và mục skipped của bundle (nếu có).
- **Chưa kiểm chứng được trong sandbox:** IPA thật không có trong sandbox; mọi kết luận
  về bundle thật (kích thước, version UnityFS, compression) vẫn là giả thuyết cho tới
  khi Joseph chạy lại. Lỗi iSH tương ứng (cùng code lọc) ghi nợ port sang patch03.

## 9. File thay đổi (chưa đẩy)

- `site/pb3/sdk/extraction.py` — lọc locale giữ entry không rõ locale (vòng export +
  checkpoint); error thật ở 4 nhánh return sớm; fix rò rỉ lọc `source_paths`
  (`_is_selected`, 2 vòng sinh ứng viên).
- `site/pb3/bridge.py` — `extract()`: tổng hợp trường `error` khi `r.ok` False.
- `site/app.js` — `runOp`: hiện `parsed.errors` + decision + số skipped; 2 handler nút
  chọn/bỏ chọn; chặn Trích xuất khi manual + Set rỗng. (Phẫu thuật bằng `muse.edit`,
  không ghi đè file; không đụng tab Font/logic font, `worker.js`, `worker-pool.js`,
  `stream.js`, `sw.js`, `gt.js`, `kb-store.js`.)
- `site/index.html` — ghi chú lọc ngôn ngữ (khớp code sau fix); 2 nút Chọn/Bỏ chọn tất
  cả; ghi chú chế độ chọn tay.
- `exp/verify_extract_unknown_error.mjs` (mới) + `exp/extract_unknown_error_results.json`.
- `exp/verify_extract_deselect_all.mjs` (mới) + `exp/extract_deselect_all_results.json`
  + `exp/extract_deselect_all_results.md`.
- `exp/verify_extract_select.mjs`, `exp/verify_enjson.mjs` — cập nhật kỳ vọng (§6).
- Không sửa `~/workspace/pb3_fix/pb3`, không sửa PDF hướng dẫn, không dùng
  COI/COOP/COEP/SharedArrayBuffer, code Python chỉ dùng stdlib, tương thích Python 3.9.
