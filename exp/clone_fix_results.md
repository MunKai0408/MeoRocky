# Hotfix bug clone (Safari DataCloneError) + mojibake + gợi ý ZIP-chứa-IPA

Ngày: 2026-11-16. Harness mới exp/verify_clone_fix.mjs: nạp CHÍNH site/app.js thật,
nối với Worker THẬT (worker_threads, exp/clone_fix_worker.mjs) qua postMessage +
transfer list thật — đúng chuỗi đã lỗi trên Safari (list → inspect → extract …).

## Nguyên nhân gốc (đã chứng minh bằng đối chứng, không phải đoán)
- app.js cũ: callWorker() truyền state.file.data làm transferable trong MỌI lần gọi.
  Sau transfer đầu, ArrayBuffer bị detached (byteLength = 0); lần gọi thứ 2 trở đi
  postMessage với buffer đã detached trong transfer list thì Safari ném DataCloneError
  "The object can not be cloned". Mục control_old_logic_detaches chứng minh lại bằng
  Node worker_threads: sau transfer lần 1 byteLength = 0, transfer lần 2 ném lỗi.
- Mojibake: "Nén" trong Files iOS ghi tên entry bằng byte UTF-8 (tên bắt đầu bằng
  U+200E LEFT-TO-RIGHT MARK, byte E2 80 8E) nhưng KHÔNG đặt cờ UTF-8 bit 11, nên
  zipfile chuẩn giải tên theo cp437 thành "ΓÇÄ …". Fix: mã hóa ngược tên sai thành
  byte cp437 rồi giải lại UTF-8 (sdk/containers.py fix_zip_entry_name).

## File đổi trong đợt này
- site/app.js: cờ fileSentFor/batchSentFor (chỉ gửi file 1 lần/file đã chọn), gửi bản
  copy slice(0), reset cờ khi chọn file mới và khi runOp lỗi; gợi ý ZIP-chứa-IPA.
- site/worker.js: chỉ ghi chú (worker dùng lại file trong FS /tmp/input, không đổi logic).
- site/index.html: 2 phần tử gợi ý list-ipa-hint / unity-ipa-hint.
- site/pb3/sdk/containers.py: fix_zip_entry_name + dùng trong ZipVFS, rebuild_zip,
  _zip_info_copy (ghi lại bằng tên đã sửa để không "đóng đinh" mojibake vào file đích).
- site/pb3/sdk/compress.py: _inventory_zip dùng tên đã sửa (khôi phục đúng tên gốc).
- site/pb3/tests/test_container.py: 4 test hồi quy mojibake (ZipVFS, rebuild, compress).
- exp/verify_clone_fix.mjs + exp/clone_fix_worker.mjs: harness mới (Worker thật).

## Kết quả harness

| Mục | Kết quả | Giây | Chi tiết |
|---|---|---|---|
| control_old_logic_detaches | PASS | 0.2 | sau transfer lần 1 byteLength=0 (kỳ vọng 0); transfer lần 2 ném: "Cannot transfer object of unsupported type." |
| boot_real_appjs | PASS | 0.1 | status="Sẵn sàng — 20 plugin plugin đang chạy · font đi kèm: đủ 142/142 (134 chữ cái + 8 dấu)" |
| sequence_list_inspect_extract_diag_font_compress | PASS | 0.2 | list=true hint_list=true hint_inspect=true extract=true diagnose=true font=true compress=true buffer_nguyen_sau_moi_op=true (byteLength=339/339) worker_nhan_file=1 lần (kỳ vọng 1): ["game.zip"] |
| reselect_resets_flag | PASS | 0.1 | list game2=true worker_nhan=2 lần: ["game.zip","game2.zip"] buffer2_nguyen=true |
| error_resets_flag_retry_resends | PASS | 0 | loi_hien=true buffer_nguyen=true thu_lai_thanh_cong=true worker_nhan_truoc=3 sau=4 (kỳ vọng +1) |
| batch_sent_once_second_run_reuses | PASS | 0.1 | batch1=true batch2=true worker_nhan_sau_lan1=6 sau_lan2=6 (kỳ vọng bằng nhau) log=["game.zip","game2.zip","game.zip","game.zip","b1.zip","b2.zip"] buffer_batch_nguyen=true |
| ipa_hint_negative_cases | PASS | 0.2 | zip_1_ipa_thang_hint_an=true; zip_thuong_khong_ipa_hint_an=true |
| static_hotfix_markers | PASS | 0 | fileSentFor/batchSentFor=true slice(0)=true khong_con_transfer_goc=true hint_fn=true hint_els_html=true worker_ghi_chu=true |
| pyodide_bridge_mojibake | PASS | 3.6 | entries=["‎ 9 Kings_v1.0.5-AppAssassin.ipa","__MACOSX/._‎ 9 Kings_v1.0.5-AppAssassin.ipa"] http_fetch=0 (bridge thật trong Pyodide, file site/pb3 đã sửa) |
| python_mojibake_regression | PASS | 0.3 | status=0, 4/4 test ok trong output unittest (4 dòng "... ok") |

Thất bại: 0

## Lưu ý
- Hành vi picker/clone THẬT trên Safari iPhone thì Joseph kiểm chứng sau khi đẩy
  (harness này chứng minh bằng Worker thật trên Node, không phải Safari thật).
- Tên entry sau khi sửa vẫn giữ ký tự U+200E (không nhìn thấy) ở đầu tên — hiển thị
  đúng " 9 Kings…", chỉ là ký tự điều khiển hướng chữ do iOS chèn, không phải lỗi.
- Fix mojibake nằm ở bản vendor site/pb3 (web); cây iSH ~/workspace/pb3_fix/pb3 chưa
  được sửa — cần port cùng fix sang đó ở đợt vá iSH sau (patch03).
## Kiểm chứng thêm (chạy sau khi harness viết báo cáo này)
- Harness cũ chạy lại, tất cả xanh: verify_module_worker.mjs 17/17,
  verify_patch01_web.mjs 8/8, verify_parity.mjs 14/14, verify_limit_2gb.mjs 6/6,
  verify_phase1_ci.mjs 8/8.
- Full suite site/pb3 (unittest discover, hệ thống): Ran 1566 tests OK
  (1562 cũ + 4 test mojibake mới), 0 fail — gồm test_container 43/43 và
  test_compress 36/36.
- Manifest đã cập nhật: exp/phase1_push_manifest.json 26 → 32 entry
  (cập nhật app.js, index.html, pb3/tests/test_container.py; thêm worker.js,
  pb3/sdk/containers.py, pb3/sdk/compress.py, exp/verify_clone_fix.mjs,
  exp/clone_fix_worker.mjs, exp/clone_fix_results.md với sha256 + bytes đúng).
- KHÔNG đẩy gì lên GitHub trong đợt này (theo yêu cầu task); coordinator lo phần đẩy.
