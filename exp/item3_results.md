# Kiểm chứng Mục 3 — KB → IndexedDB + autosave + persist + PWA

Ngày: 2026-11-16. Harness: exp/verify_item3_kb.mjs.
A: unit KBStore với IndexedDB giả. B: bridge thật trên Pyodide Node.
C: kiểm tra tĩnh. D: trình duyệt thật (Playwright chromium headless shell, serve site qua HTTP local).

| Mục | Kết quả | Giây | Chi tiết |
|---|---|---|---|
| kbstore_load_empty | PASS | 0 | load() trên IndexedDB trống -> null |
| kbstore_save_load_roundtrip | PASS | 0 | saveNow 20 byte (magic SQLite + 4 byte) -> load() dài 20 byte, khớp từng byte: true |
| kbstore_markdirty_flush | PASS | 0 | markDirty() + flush() -> IndexedDB có bytes mới (dài 19), lastSavedAt=1794817559595 |
| kbstore_flush_error_redirty | PASS | 0 | flush lỗi -> _dirty=true (lưu lại sau), onStatus=["dirty","saving","error:snapshot loi gia","dirty","saving","saved"]; flush lại sau khi sửa -> load() dài 17 byte đúng: true |
| kbstore_request_persistence | PASS | 0 | requestPersistence() -> true (navigator.storage.persist giả trả true) |
| kbstore_importfile_magic | PASS | 0 | file magic đúng -> importFile trả 17 byte: true; file magic sai -> ném: "Không phải file SQLite hợp lệ" |
| kbstore_lifecycle_flush_on_hide | PASS | 0.2 | debounce 60s nhưng ẩn tab -> flush khẩn chạy, load() dài 18 byte đúng: true |
| kbstore_overwrite | PASS | 0 | save 17 byte rồi save 18 byte -> load() dài 18, byte cuối đúng bản mới |
| bridge_boot_and_kb_fns | PASS | 1.1 | boot plugins=20; bridge fns: {"kb_snapshot":true,"kb_restore":true,"kb_list":true} |
| bridge_kb_list_persisted_flags | PASS | 0.1 | kb_list -> persisted=true session_only=false games=0 |
| bridge_kb_snapshot_restore_roundtrip | PASS | 0.1 | kb_add game_id=1; snapshot games=1 size=57344B magic SQLite=true; query sau snapshot found=true; forget -> list=0; restore games=1 list=1 id_khoi_phuc=1 (phải = 1) |
| bridge_kb_restore_invalid_keeps_old | PASS | 0 | base64 sai -> ok=false (Dữ liệu KB không phải base64 hợp lệ.); base64 đúng nhưng không phải SQLite -> ok=false; rỗng -> ok=false; sau 3 lần restore lỗi, kb_list vẫn 1 game id=1 |
| bridge_kb_snapshot_empty | PASS | 0 | snapshot KB trống: games=0 size=57344B magic SQLite=true; restore -> games=0 list=0 |
| static_manifest_valid | PASS | 0 | manifest parse OK; short_name=BearTea display=standalone start_url=. icons=["192x192","512x512","180x180"] (file tồn tại: true) |
| static_icons_png_size | PASS | 0 | icon-192={"w":192,"h":192} icon-512={"w":512,"h":512} apple-touch-icon={"w":180,"h":180} |
| static_index_wiring | PASS | 0 | thiếu/sai: không có / kb-store.js ở vị trí 43855, app.js ở 43964 (kb-store phải trước) |
| static_app_wiring | PASS | 0 | thiếu/sai: không có |
| static_kbstore_file | PASS | 0.2 | node --check kb-store.js + app.js: OK / có indexedDB.open/requestPersistence/markDirty/flush/importFile/visibilitychange: true,true,true,true,true,true / classic (không export): true / không API OPFS/COI: true |
| static_bridge_kb_api | PASS | 0.1 | py_compile: OK / kb_snapshot=true kb_restore=true session_only False=true persisted True=true magic check=true |
| static_no_coi_item3 | PASS | 0 | file chứa COI: không có |
| browser_boot_and_kb_status | PASS | 6.4 | status=Sẵn sàng; #kb-status sau boot: "KB: 0 game · 88.5 KB · tự lưu vào IndexedDB sau mỗi thay đổi (lần lưu gần nhất: chưa lưu lần nào)." |
| browser_kb_add_autosave | PASS | 0.3 | #kb-result: Đã thêm game vào KB; #kb-status sau autosave: "KB: 1 game · 56.0 KB · tự lưu vào IndexedDB sau mỗi thay đổi (lần lưu gần nhất: 08:26:11 16/11/2026)." |
| browser_export_reminder_and_persist | PASS | 0 | nhắc export hiện: true (Nhắc sao lưu: KB đã có dữ liệu nhưng bạn chưa từng xuất file .sqlite — hãy bấm “Tải KB về …); #kb-persist: "Lưu trữ bền vững: CHƯA CẤP — Safari có thể xóa KB sau ~7 ngày không mở trang; hãy cài BearTea vào Màn hình chính (mục dưới) để g |
| browser_kb_survives_reload | PASS | 5.4 | sau reload + boot lại: #kb-status 1 game; #kb-result sau Liệt kê: "1 game trong KB · dung lượng 56.0 KB · tự lưu vào IndexedDB.IDBundleVersionEngineLần thấy cuối1??unknown2026-11-16T08:26" |
| browser_kb_export_download_and_reminder_off | PASS | 0.1 | download suggestedFilename=pb3_kb.sqlite; localStorage pb3_kb_last_export=1794817577058; nhắc export đã ẩn: true |
| browser_pwa_install_prompt | PASS | 0.3 | dispatch beforeinstallprompt -> nút Cài hiện: true; bấm nút (không có prompt thật) không lỗi trang; fetch manifest.webmanifest short_name=BearTea: true; pageerror: [] |
| browser_no_pageerrors | PASS | 0 | pageerror trong cả phiên (boot x2 + thêm KB + reload + xuất + PWA): không có |

Thất bại: 0