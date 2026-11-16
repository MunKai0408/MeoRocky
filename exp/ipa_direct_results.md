# Kiểm chứng nhập trực tiếp file .ipa (không cần đổi tên thành .zip)

Ngày: 2026-11-16. Harness: exp/verify_ipa_direct.mjs.
Trình duyệt thật (Playwright chromium headless shell, 390px, serve site qua HTTP local).
Fixture: exp/fixtures/extract_select_fixture.zip copy thành .ipa (cùng bytes, khác đuôi) + nested.ipa (copy từ cây iSH, chỉ đọc).

## Kết quả
- PASS **ui_ipa_button_and_notes** (5.8s) — nút hiện: true ("📲 Nhập file .ipa"); dòng trực tiếp: "Chọn trực tiếp file .ipa, không cần đổi tên thành .zip.\n    Tab Batch cũng nhận "…; dòng dự phòng: "Nếu iPhone không cho chọn file .ipa, hãy đổi tên một bản sao thành .zip rồi chọn file .zip đó."; accept #file-input=null, #batch-files=null (cần null = không giới hạn đuôi)
- PASS **e2e_ipa_via_new_button** (6.1s) — list 5 entry (cần 5, gồm bundle localization); inspect entry đã chọn: Payload/9Kings.app/Data/level1.unity3d (options: ["Payload/9Kings.app/Data/level1.unity3d","Payload/9Kings.app/Data/resources.assets"]); inspect: "Entry trong IPA/ZIP: Payload/9Kings.app/Data/level1.unity3dBundle Unity: 5.x.x · nén none · 1 entryCAB-bbbbbbbbbbbbbbbbb"…; extract download=extract.zip, members=["Chinese.json","English.json","extraction_checkpoint.json","extraction_report.json","extraction_report.txt"]
- PASS **e2e_zip_same_bytes** (5s) — list 5 entry; extract download=extract.zip, members=["Chinese.json","English.json","extraction_checkpoint.json","extraction_report.json","extraction_report.txt"]
- PASS **ipa_vs_zip_identical** (0s) — list giống hệt: true (5 dòng); inspect giống hệt: true; extract.zip members+sha256 giống hệt: true (5 member); văn bản kết quả extract giống hệt (sau chuẩn hoá tên file): true
- PASS **dragdrop_ipa_accepted_exe_rejected** (4.5s) — drop .ipa vào dropzone nhận file (file-info hiện tên .ipa): true; drop .exe -> lỗi hiện: true "File \"malware.exe\" không phải định dạng PB3 hỗ trợ (.ipa, .zip, .unity3d, .assets, .ttf, .json, .tar"; file đang chọn vẫn là .ipa: true (kéo-thả tổng hợp bằng DataTransfer trong Chromium — không phải kéo-thả OS từ app Files trên iOS, phần đó cần Joseph thử)
- PASS **batch_ipa_accepted** (4.1s) — batch-files-info: "1 file:extract_select_fixture.ipa (1.6 KB) X" (#batch-files không accept nên picker batch cũng nhận .ipa)
- PASS **nested_ipa_list_matches_zip** (8.6s) — .ipa 3 entry, .zip 3 entry, giống hệt: true — ["Payload/Game.app/Data/config.txt|file|8 B","Payload/Game.app/Data/level.unity3d|unity|7.0 KB","Payload/Game.app/Info.plist|file|95 B"]
- PASS **browser_390_no_overflow_no_errors** (0s) — 390px: scrollWidth=390 clientWidth=390 (không tràn); pageerror: không có

Tổng: 8 mục, 8 PASS, 0 FAIL.

## File thay đổi (CHƯA ĐẨY — coordinator đẩy sau khi kiểm chứng)
- `site/index.html`: thêm nút #btn-import-ipa (inline onclick mở #file-input — không sửa app.js), dòng #ipa-direct-note, dòng dự phòng #ipa-fallback-note.
- `site/app.js`: KHÔNG SỬA (không có chỗ nào chặn .ipa — xem dưới).
- Fixture mới: `exp/fixtures/extract_select_fixture.ipa` (copy y bytes từ .zip), `exp/fixtures/nested.ipa` + `nested.zip` (copy từ cây iSH, chỉ đọc).
- Harness: `exp/verify_ipa_direct.mjs` (+ kết quả này, `exp/out/ipa_direct/extract_ipa.zip`, `extract_zip.zip`).

## Rà soát handler drop/change (app.js, chỉ đọc)
- `GAME_EXTS` (app.js:72) đã gồm `.ipa`; `gameExtOk` kiểm tra đuôi không phân biệt hoa/thường.
- `#file-input` change (app.js:742-750): chỉ chặn đuôi KHÔNG trong GAME_EXTS — .ipa đi qua, rồi `handleChosenFile` chỉ kiểm tra kích thước >2GB / cảnh báo >300MB (mục 5: >600MB cảnh báo, >800MB khuyên dùng iSH, vẫn cho tiếp tục).
- `#dropzone` drop (app.js:762-768): cùng check `gameExtOk`, .ipa đi qua.
- `#batch-files` change + `#batch-dropzone` drop (app.js:1909-1948): cùng check, .ipa đi qua; batch cũng không có accept.
- Kết luận: KHÔNG có đoạn nào chặn đuôi .ipa — không cần sửa app.js, không đụng logic streaming của agent anh em.
- `accept` còn lại trong index.html: chỉ ở input JSON dịch (`#translate-json-file`, accept=".json") và import KB (`#kb-import-file`, accept sqlite) — không liên quan, ĐỂ YÊN.

## Safari thật — chưa thử được
- Không có quyền truy cập BrowserStack/LambdaTest sẵn có trong workspace (grep không thấy cấu hình/tài khoản nào); KHÔNG tự đăng ký tài khoản hay trả phí theo brief.
- Vì vậy dòng dự phòng đổi tên thành .zip GIỮ LẠI ngay trong UI dưới khung chọn (#ipa-fallback-note), không chỉ trong sách hướng dẫn.
- CẦN JOSEPH (Safari thật, iPhone): mở trang, bấm nút "Nhập file .ipa", xem picker có cho chọn file .ipa không (có bị làm mờ không); thử kéo-thả file .ipa từ app Files vào khung; nếu picker vẫn làm mờ thì dùng cách dự phòng (đổi tên bản sao thành .zip).

## Không cập nhật sách PDF
- Theo đính chính của Joseph 14:36: KHÔNG cần cập nhật sách hướng dẫn PDF nữa — sách giữ nguyên như hiện tại, không có nợ mục này.

## Còn chờ Joseph kiểm chứng trên Safari thật
- Picker iOS có cho chọn file .ipa trực tiếp không (nút mới + khung chọn không accept).
- Kéo-thả file .ipa từ app Files vào khung (harness chỉ thử được drop tổng hợp bằng DataTransfer trong Chromium).
- Nút "Lưu / Chia sẻ" và Google Translate trên Safari thật (ngoài phạm vi đợt này, đã ghi ở các báo cáo trước).
