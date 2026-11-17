# Cổng kiểm tra trước khi giao (P0-2, bước 6, BearTea web)

Ngày: 2026-11-17. Harness: `exp/verify_delivery_gate.mjs` — **13/13 PASS** trên Chromium headless thật (lần chạy run 3, sau khi sửa kỳ vọng tamper C3 + driver D3, log `~/workspace/beartea/work_p02/harness_gate_run3.log`; xem §3, §4).
Full suite Python: **1811 chạy: 1781 đạt / 25 lỗi / 3 fail / 2 bỏ qua** (79,6s, `unittest discover`, log `~/workspace/beartea/work_p02/after_full.log`); tập 28 đỏ giống hệt baseline trước P0-2, tất cả do 2 fixture thiếu đã biết (`zip/sample.ipa`, `sqlite_loc/a.sqlite`), không có test đỏ mới (xem §5).
Kết quả JSON: `exp/delivery_gate_results.json`, viết từ CÙNG lần chạy harness run 3 (không gộp kết quả cũ; sha256 `a96604bf…`).

Nhãn trung thực dùng trong toàn báo cáo: **[ĐÃ ĐO THẬT]** = số đo trực tiếp trong lượt này; **[FIXTURE]** = fixture tự sinh, không phải game thật; **[HYPOTHESIS]** = suy luận chưa kiểm chứng. Mọi số đo trên game thật ghi rõ build.

## 1. Tính năng cổng và file thay đổi

Trước P0-2, bước 6 rời rạc: `self_check` bản dịch, diffverify trên patch, font scan nằm riêng lẻ, không có cổng pass/fail gộp. P0-2 gộp chúng thành MỘT cổng duy nhất trước khi giao file: nhận (file gốc/bản đã ép + bản dịch + provenance), trả 1 verdict PASS/FAIL duy nhất + chi tiết từng kiểm tra + danh sách lỗi dễ hiểu. Không verdict thì không giao file.

- Bridge op mới duy nhất: `delivery_gate(path, translations_json, source_json=None, provenance_json=None, pressed_path=None, allowlist_json=None, font_path=None, choices_json=None, allow_partial=False)` trong `pb3/bridge.py`. Op CHỈ ĐỌC, không ghi file nào; xác minh sha file gốc sau khi chạy. Hai chế độ: `pre_write` (mô phỏng ép trong RAM theo đúng đường (E), không ghi) và `pressed` (kiểm file đã ép thật do user nhập). Font hiển thị: `font_path` chỉ định (kể cả font đi kèm ở `/pb3web/fonts/BeVietnamPro-Regular.ttf`), trống thì quét font trong file gốc qua cache `font_list` hiện có. Tiến độ thật kind `"gate"` qua `_emit`. [ĐÃ ĐO THẬT: harness C1–C5, §3]
- Module mới `pb3/sdk/gate.py` (57.197 B): 5 kiểm tra, không có trạng thái "bỏ qua" — kiểm tra nào không chạy được thì FAIL kèm lý do (fail-closed): `translation_complete` (mọi key đã dịch, không key thiếu/trống/thừa ngoài allowlist ghi rõ; key không đổi chỉ cảnh báo; `allow_partial=True` là chế độ khai báo, key thiếu tính là "giữ nguyên ngôn ngữ gốc"), `placeholders` (so multiset CÓ THỨ TỰ + số lượng trùng lặp + số newline cho `{...}`, `%s/%d/%i/%f/%1$s`, `$var`, thẻ `<...>`, escape `\n` — chặt hơn `placeholders_ok` cũ: mất 1 bản sao `{0}` trùng thì cổng FAIL trong khi hàm cũ PASS, có test chứng minh), `diffverify` (đọc lại ĐỘC LẬP từ bytes đã ép/mô phỏng, không tin bộ nhớ của executor; theo đúng đường (E): nhóm theo provenance → plan mỗi đơn vị → executor hiện có; key unmatched không được miễn — kể cả `monobehaviour` — thì FAIL; 0 thay đổi thì FAIL; đơn vị bị từ chối thì FAIL), `fonts` (bộ 142 mục, báo cáo theo từng font, liệt kê ký tự thiếu; xem quy tắc font ở §2), `integrity` (tổng plan của cổng đối chiếu khớp chính xác preview dry-run của (E): đơn vị/đổi/chưa ép/bị từ chối; thứ tự/flags entry bundle; hash entry không đổi; tồn kho object SerializedFile + bytes object không đổi; sha script từng entry khớp `after_sha` của plan; chốt dest!=source; chốt file ép giống hệt file gốc). Stdlib-only, Python 3.9, zero ghi file (bytes/dict vào, dict ra), không UnityPy/lz4. [ĐÃ ĐO THẬT: 52 unit test + harness C1–C5]
- `rebuild_apply` bắt verdict: thêm tham số tùy chọn cuối `gate_verdict_json`; khi được truyền và ghi thật (`dry_run=False`), verdict phải PASS + chế độ `pre_write` + khớp `source_sha256` + khớp `translations_sha256`, nếu không thì `E_CONFIG_INVALID`, không ghi gì. Không truyền verdict thì giữ hành vi cũ (giới hạn đã ghi rõ, xem §2). [ĐÃ ĐO THẬT: harness C4]
- UI cổng: mục `<h3>Cổng kiểm tra trước khi giao (bước 6)</h3>` trong `#tab-mod` sau `#rebuild-result`: chọn nguồn (JSON đang mở ở phần B / nhập file `.json` bản gốc), nhập file đã ép (tùy chọn, chế độ pressed), chọn font (quét trong file gốc / font đi kèm Be Vietnam Pro / file font tự chọn), allowlist, cờ partial, nút "Kiểm tra trước khi giao", bảng kết quả (từng kiểm tra + lỗi + font + key chưa ép) render toàn bộ qua `esc()`. Nút "Ép và ghi file" chỉ bật khi đã preview VÀ cổng ĐẠT cho đúng đầu vào hiện tại, khóa theo hash (`gateKey()`) trên file + bản dịch + nguồn + provenance + lựa chọn xung đột + file đã ép + font + allowlist + cờ partial; mọi thay đổi đầu vào đều hủy verdict. `rebuildRun(false)` kiểm lại verdict trước khi ghi và truyền verdict sang bridge. [ĐÃ ĐO THẬT: harness D1–D4 trên Chromium thật]
- Đếm file: `files.json` 94→95 (+`pb3/sdk/gate.py`, đúng vị trí bảng chữ cái); `tests.json` 165→166 (+`pb3/tests/test_gate.py`); `sw.js` CACHE_VERSION v20-20261117→v21-20261117, precache 119→120 (0 mục chết, tổng đo thật 16.185.827 B = 15,44 MiB; xem §2).

File đổi/mới do P0-2 (đối chiếu `diff -rq` với snapshot `MeoRocky-pristine-p01-merged`, loại `__pycache__`/logs/reports/workspace/`.pytest_cache`; danh sách đầy đủ trong `~/workspace/beartea/work_p02/diff_pristine_p02.txt`):
đổi: `README.md`, `HANDOVER.md` (chỉ đúng chỗ P0-2), `AUTOPILOT.md` (mục P0-2 + "Mục đang làm" → P0-3, KHÔNG đụng mục "Đẩy code"), `app.js`, `index.html`, `sw.js`, `pb3/bridge.py`, `pb3/files.json`, `pb3/tests.json`, 8 check script `exp/check_analysis_{c6,c7,c8,c9,font_unify,rebuild}_static.py` + `exp/check_item{7,8}_static.py` (chỉ đồng bộ kỳ vọng đếm/version), 16 harness `.mjs` cũ (chỉ đồng bộ kỳ vọng đếm/version; riêng `verify_analysis_rebuild.mjs` thêm driver cổng ở D1 + key `title` — xem §2);
mới: `pb3/sdk/gate.py`, `pb3/tests/test_gate.py` (52 test), `exp/check_delivery_gate_static.py`, `exp/verify_delivery_gate.mjs`, `exp/delivery_gate_results.json` (+ báo cáo này).
Không đổi: `.github/workflows/`, iSH, các báo cáo `exp/*.json|*.md` cũ (đã khôi phục byte-identical sau mỗi lần chạy harness, kiểm bằng sha256), `deliver_section2/`, `deliver_audit/`, file gốc trong `real_games/` (đã kiểm size+md5 trước khi dùng, khớp cả 4 file).

## 2. Quyết định thiết kế (mỗi quyết định 1 dòng lý do)

- **FAIL chặn giao tuyệt đối, không có đường giao khi cảnh báo** — vì file giao sai (thiếu key, mất placeholder, thiếu glyph) hỏng game theo cách user khó phát hiện. Áp nhất quán ở UI (nút Ghi tắt) + bridge (`E_CONFIG_INVALID`). Cảnh báo không bao giờ chặn nhưng luôn hiện.
- **Quy tắc font:** nếu user chỉ định font hiển thị thì CHỈ font đó quyết định (thiếu chữ đang dùng → FAIL; font quét trong game thành cảnh báo). Nếu không, MỌI font đọc được trong file gốc phải phủ đủ chữ tiếng Việt đang dùng; 0 font đọc được → FAIL ("chưa kiểm được"); font nhúng Unity (class 128) chỉ liệt kê được tên/path_id, không đọc được bytes → liệt kê theo từng font là "chưa kiểm được", không mặc định tốt/xấu — vì reader hiện tại không trích được bytes font. Bản dịch không dùng chữ nào trong bộ 142 thì PASS kèm ghi chú.
- **File đã ép chỉ cần khớp nội dung + sha script theo kế hoạch, không cần byte-identical với mô phỏng** — vì file ép bằng công cụ khác vẫn hợp lệ; khác bytes thì cổng cảnh báo rõ (`byte_identical_with_simulation` theo từng đơn vị). Đã đo: lật 1 byte giữa bundle ép vẫn PASS đúng thiết kế (byte rơi vào vùng đệm cấu trúc, TextAsset trích ra giống hệt) nhưng có cảnh báo khác bytes — xem §4a.
- **Phạm vi cổng = flow (E) only; đường plan/mod trực tiếp giữ dry-run/confirm riêng** — vì brief P0-2 gắn cổng với đầu ra của (E); mở rộng sang plan/mod là mục tiếp nối, đã ghi rõ. Gọi bridge trực tiếp không verdict giữ hành vi cũ (giới hạn đã biết, không giấu): UI là đường giao duy nhất của sản phẩm và luôn truyền verdict.
- **Kiểm pre-write bằng mô phỏng RAM; bundle độc lập ≥64MB kiểm file đã ép thật ở chế độ pressed** — vì cổng dùng đường RAM của (E); bundle lớn dùng đường streaming của (E) khi ghi, cổng không mô phỏng lại đường đó. Chi phí: cổng chạy ~2× chi phí preview (preview của (E) + tự dựng plan); đo thật trên PB3 IPA 482MB: 142,7s CPython (xem §5b).
- **Đồng bộ kỳ vọng đếm/version ở MỌI harness cũ** (94→95, 165→166, 119→120, v20→v21; key `swPrecacheCount119`→`swPrecacheCount120` ở c5) theo tiền lệ P0-1 — chỉ kỳ vọng đếm/version và comment của chúng, không đổi assertion hành vi; mỗi chỗ ghi rõ trong diff. Driver D1 của harness P0-1 nay chạy cổng giữa preview và apply (nguồn `en_source.json` 9 key + font donor đi kèm) và `VI` thêm key `title` ("9 vị vua") để bản dịch đủ 9 key — C1 (`changes >= 8`), C2, C3, D2, D3 không đổi assertion, vẫn xanh (xem §5d).

## 3. Kết quả harness `verify_delivery_gate.mjs` (run 3, 2026-11-17)

Chạy trên fixture `exp/fixtures/item8_fixture.zip` (bundle v7 LZ4, 3 TextAsset JSON en/menu/dialog, 9 key) + Chromium headless shell 1148, viewport 390px. [FIXTURE] cho C1–C5; D1–D4 là trình duyệt thật.

| Mục | Kết quả | Thời gian | Nội dung chính [ĐÃ ĐO THẬT] |
|---|---|---|---|
| static_node_py_check | PASS | 0,4s | `node --check` app.js/sw.js/harness + `py_compile` bridge/gate/rebuild/plan/test_gate |
| static_gate_checks | PASS | 0,1s | gate.py stdlib-only + 3.9, không ghi file, manifest 95/166, precache 120 + v21, bridge + UI cổng |
| python_unit_gate_rebuild_plan | PASS | 4,6s | 103 test (test_gate 52 + test_rebuild + test_plan), OK |
| C1_gate_prewrite_pass | PASS | 0,6s | 9/9 key đổi, 1 đơn vị, 5/5 kiểm tra pass, sha file gốc không đổi |
| C2_gate_fail_cases | PASS | 0,1s | Thiếu key / gãy placeholder `{0}` / không nguồn / font ASCII thiếu 10 chữ đang dùng / key lạ `ghost_key` đều FAIL đúng kiểm tra |
| C3_gate_pressed_mode | PASS | 0,1s | File ép thật PASS (pressed); ép sai 1 giá trị FAIL (diffverify); cắt ngắn 16B FAIL; lật 1 byte vùng đệm PASS + cảnh báo khác bytes (xem §4a); pressed == gốc FAIL |
| C4_verdict_authorizes_apply | PASS | 0s | Verdict PASS ghi được; FAIL / sai bản dịch / sai sha nguồn đều `E_CONFIG_INVALID`, không ghi |
| C5_gate_refusals_v9_encrypted | PASS | 0,1s | Bundle v9 → FAIL + refused `E_UNSUPPORTED_FORMAT`; bundle gắn cờ mã hóa → FAIL + refused `E_ENCRYPTED` |
| browser_gate_pass_enables_apply | PASS | 7,7s | Nút Ghi tắt sau preview, cổng ĐẠT bật nút, apply + download được |
| browser_gate_pressed_mode | PASS | 0,1s | Kiểm file đã ép qua UI → ĐẠT, chế độ "kiểm file đã ép" |
| browser_gate_fail_blocks_apply | PASS | 0,2s | Thiếu key `title` → CHƯA ĐẠT, lỗi nhắc `title`, nút Ghi vẫn tắt |
| browser_390_no_overflow_no_errors | PASS | 0s | scrollWidth=390=clientWidth, không pageerror/console-error |
| shell_size_after_gate | PASS | 0s | Shell lần tải đầu: raw 453.149 B (442,5KB), gzip 128.117 B (125,1KB); ngưỡng 102400B: raw CHƯA đạt, gzip CHƯA đạt (xem §5d) |

## 4. Hai vấn đề tìm được trong lúc chạy (đều đã xử lý + ghi rõ)

### 4a. Kỳ vọng tamper C3 sai — không phải lỗi cổng, là lỗi kỳ vọng của test (ĐÃ ĐO THẬT)

Run 1–2, C3 FAIL vì lật 1 byte giữa file đã ép vẫn cho verdict PASS. Chẩn đoán bằng CPython: byte giữa (offset 296/593) lật đúng 1 byte ở offset 139 trong entry SerializedFile đã giải nén, nhưng cả 3 TextAsset trích ra giống hệt — byte đó nằm ở vùng đệm cấu trúc, ngoài script. Theo thiết kế §2 (khớp nội dung + sha script, không bắt byte-identical), PASS là đúng; cổng đã báo cảnh báo khác bytes với mô phỏng. Sửa harness (không sửa cổng): tamper đổi NỘI DUNG thật (ép bằng bản dịch khác 1 giá trị → FAIL qua diffverify) + cắt ngắn file (FAIL qua cấu trúc) + giữ lật-byte-giữa nhưng assert PASS kèm cảnh báo. Run 3 xanh. Giới hạn còn lại ghi rõ: cổng không phát hiện được thay đổi 1 byte ở vùng đệm cấu trúc không ảnh hưởng nội dung — chấp nhận được vì file như vậy vẫn khớp mọi nội dung + sha theo kế hoạch.

### 4b. Thanh Lưu file che nút cổng ở driver D3 (lỗi driver, đã sửa)

Run 1, click `#btn-gate-run` ở D3 timeout vì `#download-bar` (fixed, hiện sau apply ở D1) chặn pointer events. Sửa driver: đóng thanh bằng nút `#btn-download-close` (đúng thao tác user) + `scrollIntoView({block:"center"})` trước khi click. Không sửa app. Run 2–3 xanh. (Cũng trong lượt: khối code cổng trong `app.js` lúc đầu chèn nhầm vào trong `setCurrentGameFile`; đã dời đúng chỗ trước mọi lần chạy, `node --check` xanh, hàm gốc nguyên vẹn — lỗi thao tác, không ảnh hưởng kết quả.)

## 5. Hồi quy + cổng trên game thật [ĐÃ ĐO THẬT]

### 5a. Bảng brief §4 chạy lại — KHỚP CHÍNH XÁC cả 3 game

CPython qua `bridge.extract`, en+zh, gộp 1 file (script `work_p01/regress_one.py`, log `work_p02/regression/*/summary.json`):

| Game (build) | en / xung đột | zh / xung đột | #N | Gộp khớp tách |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 13.465 / 17 | 1.914 / 0 | 0 | 100% |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 14.556 / 293 | 2.570 / 224 | 0 | 100% |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 17.240 / 2 | 7 / 0 | 0 | 100% |

Không lệch nào so với mốc AUTOPILOT.md. TextAsset JSON dict = 0 ở cả 3 game; 100% key ngôn ngữ là MonoBehaviour (PB3 thêm CSV) — (E) v1 vẫn phủ 0% key ngôn ngữ thật (G1, như P0-1).

### 5b. Cổng trên 3 IPA thật — FAIL đúng (bản dịch giả + provenance thật)

CPython qua `bridge.delivery_gate`, bản dịch giả `"Tiếng Việt: " + giá trị gốc` (ép kiểm tra độ phủ font), provenance thật từ hồi quy, không chỉ định font (log `work_p02/realgame_gate.log`, JSON `work_p02/realgame_gate/realgame_gate.json`):

| Game | Verdict | diffverify | fonts | integrity | Ghi chú |
|---|---|---|---|---|---|
| 9 Kings | FAIL | fail: 13.465 key chưa ép (13.464 `monobehaviour` + 1 `non_string_value`), 0 đổi | fail: 3/3 font `unity_font` chưa kiểm được | pass | 22,1s |
| Poly Bridge 3 | FAIL | fail: 14.556 key chưa ép (14.517 MB + 39 `not_found`), 1 đơn vị bị từ chối | fail: 16/16 font chưa kiểm được | fail (do đơn vị bị từ chối) | 142,7s; đơn vị CSV `PB3_LOC_DEFAULT.csv` bị từ chối `E_CONFIG_INVALID` (không phải SerializedFile) — hành vi (E) có từ trước, cổng chỉ phơi rõ |
| Exploding Kittens 2 | FAIL | fail: 17.240 key chưa ép (`monobehaviour`) | pass: Roboto-Bold 139/142 phủ đủ chữ đang dùng; 7 font còn lại chưa kiểm được (cảnh báo) | pass | 12,1s |

RSS đỉnh 1.192 MB (CPython, cả 3 game + vòng tròn + probe). Kết luận: cổng chặn giao đúng trên game thật vì (E) v1 không ép được key MB — đây là hành vi đúng, không phải lỗi cổng; muốn PASS trên game thật cần đường ghi MonoBehaviour (G1, mục sau) hoặc allowlist `allowed_unpressed` khai báo rõ.

### 5c. Vòng tròn 9 Kings `resources.assets` — PASS cả 2 chế độ

Bản COPY của `Payload/9Kings.app/Data/resources.assets` (25.558.592 B): 2 dict JSON all-string (`Version` 2 key, `BillingMode` 1 key — CONFIG, không phải text ngôn ngữ, ghi rõ như P0-1), 3 key → 5 changes, font donor đi kèm: pre-write PASS (5/5 kiểm tra), ép thật bằng `rebuild_apply` với verdict → PASS, kiểm pressed PASS (5/5), sha bản copy không đổi. [ĐÃ ĐO THẬT]

### 5d. Full suite + harness hồi quy

- Smoke: 59/59 (0,5s). Full `unittest discover`: 1811 chạy = 1781 đạt / 25 lỗi / 3 fail / 2 bỏ qua (79,6s); +52 test mới (test_gate), tập đỏ giống hệt baseline trước P0-2. Mọi check tĩnh `exp/check_*_static.py`: 9/9 exit 0.
- Harness P0-1 `verify_analysis_rebuild.mjs` (driver D1 đã chèn cổng): 13/13 PASS. `verify_item6.mjs`: giữ 2 fail gzip từ trước (shell gzip 128.117 B, raw 453.149 B, đều trên ngưỡng 102400B — P0-2 làm app.js tăng thêm nhưng nguyên nhân fail không đổi: monolith app.js + index.html; tách chunk lazy là việc riêng). `verify_item7.mjs`: giữ 1 fail fixture từ trước (`python_tmp_fixture_match`). Không chạy các harness khác trong lượt này (ghi rõ).
- `exp/*.json|*.md` cũ + `pb3/logs|reports` đã khôi phục byte-identical sau mọi lần chạy (kiểm bằng sha256 với snapshot trước khi sửa: 85/85 khớp).

### 5e. DungeonWithin — probe-only, FAIL, không giải mã

IPA 91.389.555 B, bundle Addressables mã hóa AES: probe 1 key → FAIL (key không tìm thấy trong đơn vị nào + 6 font chưa kiểm được, 0 đơn vị bị từ chối trong cấu hình probe này — các bundle mã hóa không cho đơn vị trích được nào cho key probe; cổng FAIL qua diffverify + fonts, không qua refusal `E_ENCRYPTED` ở đây). Không thử giải mã, không vượt DRM. [ĐÃ ĐO THẬT]

## 6. Điểm chưa đạt + bước thử Safari/iSH cho user

- **Flow trình duyệt + RAM Safari thật: [CHƯA KIỂM CHỨNG]** — sandbox chỉ có Chromium headless; không có Safari thật. Không báo PASS giả. Bước thử Safari (iPhone/iPad, trang live sau khi đẩy):
  1. Mở trang live, lần thăm 2 (đã precache) bật Chế độ máy bay → trang vẫn tải + boot tới "Sẵn sàng" (SW v21).
  2. Chọn file `exp/fixtures/item8_fixture.zip` (tải từ repo về máy trước) → đợi boot xong → tab Công cụ → tab Mod.
  3. Nhập bản dịch 9 key (file `vi.json` mẫu trong §3: hello/play/quit/title/start/options/exit/greeting/farewell) → bấm "Xem trước" → thấy "XEM TRƯỚC", nút "Ép và ghi file" VẪN TẮT.
  4. Ở mục cổng: chế độ nguồn = "Nhập file .json bản gốc", nhập file nguồn 9 key (Hello/Play/Quit/9 Kings/Start Game/Options/Exit/Welcome, hero!/Goodbye!); chế độ font = "Font đi kèm Be Vietnam Pro" → bấm "Kiểm tra trước khi giao" → thấy "ĐẠT — được giao file", nút Ghi BẬT.
  5. Bấm "Ép và ghi file" → thấy "ĐÃ GHI" + tải được file về; mở file ép bằng BearTea (chế độ pressed ở cổng, nhập file vừa tải) → cổng ĐẠT, chế độ "kiểm file đã ép".
  6. Thử FAIL: nhập bản dịch thiếu key `title` → xem trước → chạy cổng → thấy "CHƯA ĐẠT", lỗi nhắc `title`, nút Ghi vẫn TẮT.
  7. Đo RAM: trên macOS, Safari → Develop → [iPhone] → Timelines/Allocations trong lúc chạy bước 4–5 với file IPA thật (9 Kings v1.0.6); ghi đỉnh RAM + có/không reload trang. Kỳ vọng [HYPOTHESIS]: RAM ~2× kích thước file + entry lớn nhất (mô phỏng RAM của cổng + preview (E)); số thật do Joseph đo.
- **iSH: chưa thử.** Bước thử (cây iSH đầy đủ, sau khi port): `cd pb3 && python3 -m unittest tests.test_gate` (kỳ vọng 52 test xanh); `python3 run_tests.py --tier smoke` (59/59); chạy cổng qua Python trực tiếp trên bản copy `resources.assets` như §5c (không chạy trên file gốc). Cổng là stdlib-only, không phụ thuộc web — kỳ vọng chạy được trên iSH, nhưng [CHƯA KIỂM CHỨNG].
- **Chưa đạt khác:** đường ghi MonoBehaviour (G1) — cổng sẽ tiếp tục FAIL trên game thật cho tới khi có đường ghi đó hoặc allowlist khai báo; vòng tròn trên `data.unity3d` 590MB (file không được cung cấp); job CI `unit` exit 1 (nguyên nhân chưa rõ, như P0-1); Zstd bundle thật + RAM Safari thật chưa đo.

## 7. Tiêu chí P0-2

| # | Tiêu chí | Kết quả |
|---|---|---|
| 1 | Một điểm vào cổng duy nhất (bridge op + UI), 1 verdict + chi tiết + lỗi dễ hiểu; không verdict thì không giao | ĐẠT — `delivery_gate` + UI tab Mod; nút Ghi khóa theo verdict + hash đầu vào [ĐÃ ĐO THẬT: C1–C5, D1–D4] |
| 2a | Self-check bản dịch: mọi key đã dịch, không thiếu/trống ngoài allowlist ghi rõ | ĐẠT — `translation_complete` + allowlist 5 nhóm, echo trong kết quả [C2, unit test] |
| 2b | Bảo toàn placeholder/thẻ/newline từng key | ĐẠT — multiset có thứ tự + newline, chặt hơn hàm cũ [C2, unit test] |
| 2c | Diffverify: file đã ép trích lại khớp bản dịch theo đường (E) + provenance, unmatched/MB báo rõ chưa ép | ĐẠT — đọc lại độc lập từ bytes, unmatched có lý do + đếm, MB báo rõ [C1–C3, C5, §5b] |
| 2d | Font: mọi chữ dùng trong bản dịch nằm trong vùng phủ font hiển thị (bộ 142), báo cáo từng font, liệt kê chữ thiếu | ĐẠT — theo từng font + `missing_used_chars`; font nhúng không đọc được thì "chưa kiểm được" có ghi rõ [C1, C2, §5b] |
| 2e | Toàn vẹn: hash/cấu trúc khớp kế hoạch ép, không ghi đè file gốc | ĐẠT — đối chiếu plan + entry/object/sha + 2 chốt dest!=source/giống-hệt-gốc; giới hạn byte-vùng-đệm ghi rõ ở §4a [C3, C4] |
| 3 | Thất bại chặn giao (1 quy tắc, 1 dòng lý do, nhất quán UI + bridge) | ĐẠT — chặn tuyệt đối, không đường giao khi cảnh báo; lý do ở §2 [C4, D1–D2] |
| 4 | Test Python từng kiểm tra (kể cả fail) + harness mới + báo cáo từ cùng 1 lần chạy, không ghi đè JSON cũ, khôi phục file bẩn byte-identical | ĐẠT — 52 unit test, harness 13/13, JSON+md này từ run 3, 85/85 file exp cũ khôi phục byte-identical |
| 5 | Hồi quy §4 + smoke + full + harness liên quan; đồng bộ kỳ vọng đếm ở MỌI harness cũ + tăng CACHE_VERSION; cập nhật README/HANDOVER/AUTOPILOT đúng chỗ | ĐẠT — §4 khớp chính xác, smoke/full như §5d, harness P0-1 13/13, item6/item7 giữ trạng thái đỏ-từ-trước; đếm 95/166/120 + v21; 3 tài liệu đã cập nhật (AUTOPILOT không đụng "Đẩy code") |
