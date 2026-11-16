# Kiểm chứng bản web PB3 (Pyodide cục bộ, Node)

Ngày: 2026-11-16. Pyodide 314.0.8. Chạy bridge.py trên bản vendor trong site/ (không dùng cây PB3 gốc).

| Mục | Kết quả | Giây | Chi tiết |
|---|---|---|---|
| no_http_fetch_during_load | PASS | 0 | http fetch trong lúc loadPyodide: 0 |
| module_worker_static | PASS | 0 | worker import .mjs=true no importScripts=true app type:module=true no cross-origin=true pyodide.mjs vendored 17758B=true |
| load_lz4 | PASS | 0.3 | loadPackage lz4 OK |
| write_site_files | PASS | 0 | 93 file pb3/ + font b64 (132948 byte) ghi vào /pb3web |
| boot | PASS | 1.2 | {"ok":true,"plugins":20,"plugin_ids":["apple_plist","apple_strings","cocos_detect","csv_table","defold_detect","gamemaker_detect","generic_strings","godot_pck","godot_translation","json_text","po_mo","properties","resx", |
| font_donor_142 | PASS | 0 | {"ok":true,"family":"Be Vietnam Pro","kind":"ttf","num_glyphs":527,"total":142,"covered":142,"pct":100,"is_full":true,"summary":"Be Vietnam Pro: đủ tiếng Việt (142/142 ký tự, 527 glyph)"} |
| list_entries | PASS | 0 | kind=zip total=11 sample=[{"path":"data/font.ttf","size":0,"kind":"excluded"},{"path":"inner.zip","size":161,"kind":"container"},{"path":"inner.zip::en.json","size":57,"kind":"file"}] |
| unity_inspect_assets | PASS | 0.4 | {"ok":true,"is_container":false,"bundle":false,"files":[{"entry":"resources.assets","version":17,"unity_version":"2019.4.40f1","objects":3,"text_assets":[{"path_id":1001,"name":"greeting","head":"Xin chao 9 Kings!"},{"pa |
| unity_inspect_bundle | PASS | 0 | {"ok":true,"is_container":false,"bundle":true,"files":[{"entry":"CAB-0123456789abcdef0123456789abcdef","version":17,"unity_version":"2019.4.40f1","objects":3,"text_assets":[{"path_id":1001,"name":"greeting","head":"Xin c |
| extract | PASS | 0.1 | decision=done total=21 files=6 zip=3893B phases=extract:inventory,extract:layers,extract:candidates,extract:sniff,extract:probe,extract:export progress_events=74 |
| translate_offline | PASS | 0.2 | translated={"play":"Chơi","quit":"Thoát"} untranslated=0 progress_events=2 |
| translate_credit | PASS | 0 | credit_key=title title="9 Kings — Việt Hoá Bởi Bear" |
| compress_roundtrip | PASS | 0.1 | orig=672 comp=976 ratio=0.69 restored_sha=ef27b9cf2258432c byte_identical=true progress_events=7 |
| font_scan_merge | PASS | 0.1 | scan=Be Vietnam Pro: đủ tiếng Việt (142/142 ký tự, 527 glyph) merge_added=0 already=142 glyphs_copied=0 merged_bytes=132920 |
| font_donor_file | PASS | 0 | dest=/tmp/BeVietnamPro-Regular.ttf bytes=132948 |
| diagnose | PASS | 0 | kind=zip entries=3 hex_lines=32 strings=15 first_str="l]o]n" |
| file_sha256 | PASS | 0 | {"ok":true,"sha256":"ef27b9cf2258432c8c91966140d4fc46decd105b2fff5fd4e4bccc7e09e366f8","size":672} |

Thất bại: 0