# Redesign BearTea — kết quả harness

Chạy: 2026-11-20T01:26:05.508Z · FAIL: 0 / 10

## PASS — static_checks (0.2s)
all_appjs_ids_present=true · file_input_no_accept=true · batch_files_no_accept=true · translate_json_kept=true · kb_import_kept=true · share_btn_markup=true · blob_btn_markup=true · no_5s_revoke=true · revoke_10min=true · pagehide_revoke=true · css_no_global_breakall=true · css_nowrap=true · css_firstcol_break=true · css_table_scroll=true · kb_ui_kept=true · no_muc1_left=true · binary_note_ge4=true · translate_proxy_kept=true · limit_2gb_text=true · no_external_url=true · no_img_tag=true · no_fake_sample_file=true · no_coi=true · views_present=true · home_default_visible=true · bottomnav_present=true · cards_present=true · no_theme=true · save_history_present=true · bear_pastel=true · credit_locked=true · recent_present=true · download_bar_close=true · bear_icon_img=true · mod_ops_honest=true · node_check=true | app.js dùng 296 id, thiếu 0

## PASS — fake_dom_new_ui (0s)
nav chuyển view đúng; không còn theme, spinner tiến độ bật/tắt đúng; recent render + metadata localStorage (không nội dung file); tắt lưu lịch sử xóa metadata

## PASS — browser_boot_home_390 (9.2s)
boot Sẵn sàng; home scrollWidth=390 (không tràn); logo gấu <img> + 6 thẻ + 5 nút nav; hero: Công cụ xử lý game Unity mạnh mẽ ngay trên trình d…

## PASS — browser_dropzone_recent (0.1s)
bấm dropzone mở khung chọn file; chọn game1.zip -> file-info hiện; recent có game1.zip + nút Tải lại

## PASS — browser_list_table_390 (0.7s)
390px: tiêu đề/Loại/Kích thước đúng 1 dòng; bảng block+overflow-x:auto; không tràn ngang (sw=390)

## PASS — browser_unity_info_card (1.8s)
inspect bundle trong ZIP xong; thẻ Thông tin Unity: File: unity_container.zipUnity Version: 2019.4.40f1 · file .assets độc lập3 object · 2 TextAssetĐã sẵn sàng An toàn & Riêng tưXử lý 100% trê…

## PASS — browser_extract_download_bar (1.3s)
extract.zip tải về (extract.zip); thanh Lưu file hiện; kết quả nhắc đúng tên file

## PASS — browser_other_views_390 (4.4s)
#btn-card-translate sw=390 · #btn-card-mod sw=390 · #btn-card-batch sw=390 · #btn-nav-kb sw=390 · #btn-nav-settings sw=390 · #btn-nav-about sw=390 | KB status: KB: 0 game · 88.5 KB · tự lưu vào IndexedDB sau mỗi thay đổi… | Mod: Thay Text thật + 5 op disabled

## PASS — browser_pastel_real (0.3s)
không nút theme, logo <img> icons/icon-192.png, nền body rgb(220, 235, 248), có ghi chú credit luôn gắn

## PASS — browser_no_pageerrors (0s)
pageerror: không có
