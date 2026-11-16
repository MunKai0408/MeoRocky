# Hồi quy patch01 trên bản web PB3 (Pyodide cục bộ, Node)

Ngày: 2026-11-16. Pyodide 314.0.8. Chạy bridge.py trên bản vendor trong site/.

| Mục | Kết quả | Giây | Chi tiết |
|---|---|---|---|
| no_http_fetch_during_load | PASS | 0 | http fetch trong lúc loadPyodide: 0 |
| write_site_files | PASS | 0 | 91 file pb3/ + font b64 ghi vào /pb3web |
| boot | PASS | 1 | {"ok":true,"plugins":20,"plugin_ids":["apple_plist","apple_strings","cocos_detect","csv_table","defold_detect","gamemaker_detect","generic_strings","godot_pck","godot_translation","json_text","po_mo","properties","resx", |
| extract_xml_trigger_no_crash | PASS | 0.4 | decision=done total_entries=3 files=[res/values/strings.xml@xml_strings, data/en.json@json_text] errors=[] |
| xml_routed_to_xml_strings | PASS | 0 | strings.xml plugin=xml_strings entries=1; có file nào qua yaml_ini=false |
| yaml_ini_sniff_xml_zero | PASS | 0 | sniff(XML thụt đầu dòng) = 0 (mong đợi 0.0) |
| parse_ini_struct_xml_e_corrupt | PASS | 0 | _parse_ini_struct(trigger XML) = PB3Error:E_CORRUPT (mong đợi PB3Error:E_CORRUPT) |
| normalize_typed_path_trailing_dot | PASS | 0 | game.ipa. -> /tmp/pathtest/game.ipa; game.ipa -> /tmp/pathtest/game.ipa; khongco.ipa. -> /tmp/pathtest/khongco.ipa. |

Thất bại: 0