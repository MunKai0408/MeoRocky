# Hotfix Safari đợt 2 — kết quả harness

Chạy: 2026-11-16T09:58:04.652Z · FAIL: 0 / 17

## PASS — download_blob_no_share (0s)
blob bấm ngay; revoke 5s/60s/599s = 0, 600s = 1; hint: Đang tải về out.tar.xz (4 B) — trình duyệt sẽ hỏi thì bấm Tả…

## PASS — download_share_primary (0s)
blob không tự bấm; Share+blob hiện; share đúng tên extract.zip; hint: File extract.zip (3 B) đã sẵn sàng — bấm “Lưu / Chia sẻ” để …

## PASS — download_share_errors (0s)
AbortError im lặng; lỗi thật báo 'Chia sẻ lỗi: …'

## PASS — download_revoke_old_on_new (0s)
lần mới hủy URL cũ ngay; timer cũ không chạm URL mới; mỗi URL sống đúng 10 phút

## PASS — download_blob_after_revoke (0s)
quá 10 phút: tạo lại URL từ blob đang giữ rồi bấm

## PASS — static_checks (0s)
no_5s_revoke=true · revoke_10min=true · pagehide_revoke=true · share_primary=true · no_unlabeled_error=true · runop_throws_labeled=true · decompress_guard=true · compress_guard=true · unity_picker=true · bridge_inspect_entry=true · bridge_container=true · bridge_filename=true · worker_filename=true · index_share_btn=true · index_blob_btn=true · index_compress_wording=true · css_no_global_breakall=true · css_nowrap=true · css_firstcol_break=true · css_table_scroll=true

## PASS — no_http_fetch_during_load (0s)
http fetch trong lúc loadPyodide: 0

## PASS — pyodide_boot_bridge (0.4s)
89 file pb3/ + font ghi vào /pb3web, bridge import OK

## PASS — bridge_boot (1.1s)
boot ok=true plugins=20

## PASS — unity_inspect_zip_lists_bundles (0s)
is_container, bundles=["Payload/Game.app/Data/resources.assets","Payload/Game.app/Data/sample.unity3d"], total_entries=15

## PASS — unity_inspect_entry (0.3s)
bundle files=2; assets version=17 2019.4.40f1

## PASS — unity_inspect_zip_no_bundles (0s)
game1.zip: is_container, bundles=[] (UI hiện hướng dẫn trích xuất trước)

## PASS — unity_inspect_direct_assets (0s)
version=17 2019.4.40f1 (không đổi so với trước)

## PASS — decompress_original_filename (0.1s)
filename=sample.ipa (không còn restored.bin), byte_identical=true

## PASS — decompress_zip_clean_error (0s)
ok=false, error=E_BAD_ARCHIVE: Archive hỏng (CRC sai hoặc cấu trúc lỗi).

## PASS — table_old_css_broken_control (0.5s)
CSS cũ: chữ trong ô tiêu đề/Loại tối đa 2 dòng (vỡ chữ nếu >1) — th=[{"text":"Đường dẫn","lines":1},{"text":"Loại","lines":2},{"text":"Kích thước","lines":2}]

## PASS — table_390px_new_css (0.2s)
390px: tiêu đề/Loại/Kích thước đúng 1 dòng (không vỡ chữ); cột đường dẫn [3,2,1,1] dòng (được ngắt); bảng block+overflow-x:auto, plugin scrollW=354 > clientW=340
