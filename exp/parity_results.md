# Kiểm chứng Phase 2 — ngang chức năng iSH cho bản web PB3

Ngày: 2026-11-16. Pyodide 314.0.8. Chạy bridge.py trên bản vendor trong site/pb3 (KHÔNG dùng cây PB3 gốc).
Không dùng stand-in: sdk/batch.py, sdk/kb.py, sdk/support_matrix.py đều đã có trong site/pb3 (Phase 1 đã vendor, md5 khớp cây iSH).

| Mục | Kết quả | Giây | Chi tiết |
|---|---|---|---|
| write_site_files | PASS | 0 | 87 file pb3/ ghi vào /pb3web; stand-in iSH: KHÔNG — chạy trên bản vendor thật |
| boot_20_plugins | PASS | 1.1 | {"ok":true,"plugins":20,"plugin_ids":["apple_plist","apple_strings","cocos_detect","csv_table","defold_detect","gamemaker_detect","generic_strings","godot_pck","godot_translation","json_text","po_mo","properties","resx","sqlite_loc","unreal |
| bridge_new_fns | PASS | 0 | {"mod_patch":true,"batch_run":true,"kb_add":true,"kb_query":true,"kb_list":true,"kb_forget":true,"kb_export":true,"kb_import":true,"support_matrix":true} |
| mod_dryrun | PASS | 0.4 | verified=true occ=1 mode=PATCH_INPLACE script 17->17B unit 6763->6763B dest=null no_mod_out=true input_unchanged=true progress_mod=true |
| mod_write | PASS | 0 | dest=/tmp/mod_out/resources.assets exists=true patched_script="Chào 9 Kings!\u0000\u0000\u0000" input_unchanged=true |
| mod_bundle | PASS | 0 | entry=CAB-0123456789abcdef0123456789abcdef pid=1001 old="Xin " verified=true occ=1 mode=PATCH_INPLACE |
| kb_add_query_list | PASS | 0.1 | game_id=1 engine=unknown query_found=true files=5 list_games=1 size=115368B |
| kb_export | PASS | 0 | dest=/tmp/pb3_kb.sqlite size=57344B games=1 copy_reopen_games=1 |
| kb_forget | PASS | 0 | forgotten=true list_games_after=0 |
| kb_import | PASS | 0 | file_phase2: imported=0 skipped=1 (game1.zip không có bundle id) list_after=0 source_unchanged=true; file_có_bundle: imported=1 bundle=com.example.kbtest list_after=1 warnings=1 |
| batch_sequential_2jobs | PASS | 0.1 | total=2 ok=2 failed=0 jobs=1 ckpt_games=2 zip=2077B progress_batch_2_2=true games=[ok:game_01_game1,ok:game_02_resources] |
| batch_resume_checkpoint | PASS | 0 | total=2 skipped=2 ok=0 notes=["đã xong (checkpoint)","đã xong (checkpoint)"] |
| support_matrix | PASS | 0 | plugins=20 containers=11 unity=7 real_game=1 unsupported=7 first=apple_plist/M2 md_lines=105 |
| translate_regression | PASS | 0.2 | offline play->Chơi; worker import gt.js=true expose globalThis=true gt.js export=true; google_fn fallback offline=offline play->Chơi |

Thất bại: 0