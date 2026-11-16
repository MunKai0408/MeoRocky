# TÀI KHOẢN CLAUDE 06 — PLUGIN ĐỊNH DẠNG / LOCALIZATION

Trạng thái: **SANDBOX DONE (2026-11-16)** — sandbox 1025/1025 OK; `plugin test`
PASS cho cả 12 plugin qua CLI thật; iSH NOT RUN. Tiến độ 10 gói không đổi
(06 là gói thứ 6 trong 10 prompt, sau 05b/polish).
Phạm vi: plugin định dạng / localization theo prompt 06 — JSON, CSV/TSV, XML,
YAML/INI, Properties, plist, .strings, SQLite, PO/MO, XLIFF, RESX + extractor
chuỗi tổng quát. Không làm 07–10 (không viết engine extraction tổng thể).

## 1. Đã làm (sandbox, 2026-11-16)

### Lớp nền dùng chung — `sdk/formats.py` (decisions #117–124)
- `FormatPluginBase(Plugin)`: lo detect / list_candidates / probe / parse /
  build / verify / selftest theo hợp đồng SDK; plugin con chỉ cài
  `_parse_bytes` (+ `_build_bytes` nếu ghi được, + `_sniff`, + `_is_writable`
  cho định dạng hỗn hợp). Kèm helper `decode_text`/`TextDoc` (giữ
  encoding+BOM gốc khi ghi ngược: UTF-8/UTF-16LE/UTF-16BE, UTF-32 từ chối),
  `make_entries` (key trùng giữ bản ghi đầu, đặc tả 7.5; hỗ trợ locale riêng
  từng entry cho CSV đa locale), `stable_row_key`/`stable_hash_key` (key sinh
  ổn định, đặc tả 7.5).
- Verify mặc định: giải mã UTF-8 nghiêm ngặt + parse lại cả hai file + so tập
  key theo đúng thứ tự; lật 1 byte trong văn bản ASCII làm hỏng giải mã UTF-8
  nên bị từ chối (cơ chế kiểm âm của conformance). `build` tự xác minh trước
  khi trả bytes (đọc lại phải khớp đúng giá trị cần ghi, đặc tả 6.7).
- Ứng viên ghi được xếp trước trong `list_candidates` (để conformance kiểm
  write trên file ghi được ở plugin hỗn hợp).

### 12 plugin — `plugins/<id>/plugin.py` (mỗi cái có MANIFEST + fixture + test)
- **json_text** (M3, read+write, `.json`): object/danh sách object key-value,
  object lồng nhau thành key chấm (i18next), mảng chuỗi thành key[i], số/
  bool/null giữ kiểu qua notes `json:number/boolean/null/complex`; key trùng
  giữ bản ghi đầu, giá trị rớt vẫn giữ trong bảng (status conflict) để không
  mất dữ liệu im lặng; ghi ngược dựng lại JSON (indent gốc nếu đoán được,
  giữ UTF-8/UTF-16 + BOM).
- **csv_table** (M3, `.csv`/`.tsv`): đoán delimiter (`,` `;` `\t` `|`),
  header theo {key,id,name,string_id,key_id}, cột giá trị thành entry theo
  locale = tên cột, không header thì cột 0=key cột 1=value (key rỗng ->
  `row_000001`); build áp theo notes vị trí ô `csv:<hàng>:<cột>`, giữ
  delimiter và `\n`/`\r\n` gốc.
- **xml_strings** (M3, `.xml`): `<resources>` kiểu strings.xml (`<string>`,
  `<string-array>` -> key[i], `<plurals>` -> key[quantity],
  `translatable="false"` bỏ qua), generic `<string name/key/id>` hoặc con
  trực tiếp của root; escape kiểu strings.xml; từ chối `<!ENTITY`; giữ comment trong
  root (ElementTree `insert_comments=True`).
- **yaml_ini** (M3, `.yaml/.yml/.ini/.cfg/.conf`): INI qua configparser (key
  giữ nguyên hoa thường, file không section bọc section giả rồi tháo khi ghi);
  YAML parser thuần Python, tập con: map lồng, list, scalar trần/nháy đơn/
  nháy kép, comment, block scalar `|`/`>`, flow map/list, anchor/alias.
  Tab thụt đầu dòng -> E_CORRUPT; nhiều document -> E_UNSUPPORTED_FORMAT.
- **properties** (M3, `.properties`): chuẩn Java (escape `\uXXXX`, nối dòng,
  separator `=`/`:`/khoảng trắng, UTF-8 rồi latin-1); build thay theo span
  nên comment/thứ tự/separator giữ nguyên byte.
- **apple_plist** (M3, `.plist/.stringsdict/.xcstrings`): `plistlib` đọc cả XML
  lẫn nhị phân; `.stringsdict` -> `key.format` + `key.plural.<rule>`;
  `.xcstrings` -> entry theo từng key+locale + biến thể plural/device.
  **Plist nhị phân chỉ đọc** (build -> E_WRITE_DISABLED); plist XML ghi ngược
  qua `plistlib.dumps(sort_keys=False)` nên comment/DTD gốc bị mất.
- **apple_strings** (M3, `.strings`): `"key" = "value";`, escape, comment
  `/* */` và `//`; build thay đúng span value, giữ nguyên encoding+BOM gốc
  (thường UTF-16LE có BOM).
- **sqlite_loc** (M2, **chỉ đọc**, `.sqlite/.db/.sqlite3/.db3`): mở qua URI
  `mode=ro` + file tạm trong `ctx.tempdir`, không bao giờ ghi vào DB gốc;
  bảng localization suy ra theo cột key TEXT + cột giá trị TEXT hoặc tên bảng;
  key = `<bảng>.<key>` khi nhiều bảng; giới hạn 100.000 entry.
  (Quyết định #120: không khai write vì verify không phân biệt được byte lật
  trong giá trị với một sửa đổi hợp lệ khi không có bảng kỳ vọng.)
- **po_mo** (M3, `.po`/`.mo`): PO đọc+ghi (msgid/msgstr, số nhiều, msgctxt
  với key `msgctxt\x04msgid`, fuzzy, obsolete bỏ qua); build thay theo span
  msgstr, giữ comment/flag/thứ tự. **MO chỉ đọc** (build ->
  E_WRITE_DISABLED); parse cả LE/BE.
- **xliff** (M3, `.xlf/.xliff`): XLIFF 1.2 (`<trans-unit>`) và 2.0
  (`<unit>/<segment>`); key = resname nếu có, không thì id; target chưa có
  thì value = source + notes "chưa-dịch"; target có markup nội tuyến ->
  status unparsed, build bỏ qua kèm warning (không làm phẳng im lặng).
- **resx** (M3, `.resx`): `<data name><value>`; `type`/`mimetype` hoặc value
  có markup -> unparsed, build bỏ qua; `xml:space="preserve"` không trim.
- **generic_strings** (M2, kind **extractor**, chỉ đọc, `heavy`, priority 1):
  quét chuỗi ASCII/UTF-16LE/BE (≥4 ký tự) trong file không thuộc định dạng đã
  biết; key `off_%08x` theo offset; tối đa 5000 entry + warning tổng số;
  luôn cảnh báo "dò chuỗi thô, không phải bảng localization đã xác minh".

### Test mới — 230 test (795 -> 1025)
| File test | Số test |
|---|---|
| test_format_json_text.py | 19 |
| test_format_csv_table.py | 20 |
| test_format_xml_strings.py | 18 |
| test_format_yaml_ini.py | 26 |
| test_format_properties.py | 19 |
| test_format_apple_plist.py | 20 |
| test_format_apple_strings.py | 20 |
| test_format_sqlite_loc.py | 18 |
| test_format_po_mo.py | 22 |
| test_format_xliff.py | 18 |
| test_format_resx.py | 16 |
| test_format_generic_strings.py | 12 |
| test_format_cli.py (plugin list + plugin test thật) | 2 |
- Mỗi file: parse đúng, build round-trip, Unicode/BOM/encoding, escape, số
  nhiều (nơi có), key trùng, file hỏng đúng mã lỗi, verify chấp nhận bản tốt /
  từ chối bản lật 1 byte, selftest, conformance trong bộ nhớ
  (`run_conformance`, rep.ok + data_tested), detect/candidates/probe.
- Tích hợp: cập nhật `tests/test_registry.py`
  (`test_real_project_has_first_builtin_plugin_zip` — kỳ vọng cũ ["zip"] vỡ
  vì Claude 06 thêm 12 plugin builtin thật; nay assert đủ 13 id + kind +
  maturity từng nhóm, decision #125); `plugins/README.txt` liệt kê plugin
  mới; `tests/corpus/<id>.json` cho 10 plugin M3 (decision #124/#126).
- Tổng suite: **795 -> 1025 test**.

### Không đổi
- Lõi, registry, CLI, Unity, container không đổi giao diện. Mã tương thích
  Python 3.9 (đã kiểm AST `feature_version=(3, 8)` cho mọi file mới; không
  `match`/`tomllib`); không thêm dependency; không có câu `import UnityPy`/
  `import lz4` nào trong mã (chỉ một dòng comment trong docstring của
  `sdk/formats.py` nói rõ quy tắc này). Không có socket/subprocess/eval.

## 2. Định dạng: mức hỗ trợ

| Định dạng | Đọc | Ghi | Maturity | Ghi chú |
|---|---|---|---|---|
| JSON (.json) | có | có (dựng lại) | M3 | key chấm, key[i], giữ kiểu số/bool/null; mất comment/khoảng trắng gốc |
| CSV/TSV | có | có (dựng lại) | M3 | đa locale theo cột; quoting có thể đổi, nội dung parse giữ nguyên |
| XML strings | có | có | M3 | strings.xml + generic; comment trước root bị mất |
| YAML (tập con) | có | có (dựng lại) | M3 | xem §4; comment mất khi ghi |
| INI | có | có (dựng lại) | M3 | comment mất khi ghi |
| Properties | có | có (theo span) | M3 | giữ nguyên byte phần không sửa |
| plist XML | có | có | M3 | comment/DTD mất khi ghi |
| plist nhị phân | có | **không** | M3* | chỉ đọc; *maturity của plugin (phần ghi là XML) |
| .stringsdict | có | có | M3 | số nhiều Apple |
| .xcstrings | có | có | M3 | catalog JSON, theo (locale, key) |
| .strings | có | có (theo span) | M3 | giữ encoding/BOM gốc |
| SQLite | có | **không** | M2 | chỉ đọc; bảng suy ra theo heuristic |
| PO | có | có (theo span) | M3 | số nhiều, msgctxt, fuzzy |
| MO | có | **không** | M3* | chỉ đọc; *maturity của plugin (phần ghi là PO) |
| XLIFF 1.2/2.0 | có | có | M3 | markup nội tuyến -> unparsed, không ghi |
| RESX | có | có | M3 | entry nhị phân -> unparsed, không ghi |
| Chuỗi thô (extractor) | có | **không** | M2 | dò chuỗi, false positive có thể xảy ra |

## 3. Bằng chứng sandbox (Python 3.12.3, 2026-11-16)

- `python3 run_tests.py --tier full`: **`Ran 1025 tests in 28.9s OK
  (skipped=1, lỗi=0, fail=0)`** (795 cũ + 230 mới). `--plan`: fast 910,
  ish 11, slow 51, meta 53, smoke 59.
- `python3 menu.py plugin list`: đủ 13 plugin builtin, trạng thái [ok]:
  10 × M3 (json_text, csv_table, xml_strings, yaml_ini, properties,
  apple_plist, apple_strings, po_mo, xliff, resx) + 3 × M2 (zip,
  sqlite_loc, generic_strings). Không còn cảnh báo M3→M2 (đã có
  `tests/corpus/<id>.json`, decision #126).
- `python3 menu.py plugin test <id>` (chạy thật, cây đã tích hợp) — cả 12
  đều PASS, mỗi cái `8 PASS, 0 FAIL, 3 SKIP` (3 SKIP là container/hints
  không khai + readonly/write theo đúng loại plugin), kiểm âm lật 1 byte đều
  bị từ chối:

| Plugin | Fixture | Kết quả |
|---|---|---|
| json_text | 3 file | PASS |
| csv_table | 4 file | PASS |
| xml_strings | 3 file | PASS |
| yaml_ini | 3 file | PASS |
| properties | 2 file | PASS |
| apple_plist | 4 file | PASS |
| apple_strings | 2 file | PASS |
| sqlite_loc | 1 file | PASS |
| po_mo | 2 file | PASS |
| xliff | 2 file | PASS |
| resx | 1 file | PASS |
| generic_strings | 3 file | PASS |

- Hai lỗi thật tìm được khi viết test và đã sửa trong mã (không sửa test cho
  qua): (1) `.xcstrings` build khớp theo (locale, key) thay vì key thuần —
  sửa chỉ locale `vi` trước đây rơi nhầm sang `en` (decision #127);
  (2) `sqlite_loc` không parse được dưới guard vì ghi file tạm hệ thống bị
  chặn — nay parse/probe/selftest dùng `ctx.tempfile()` (decision #127).
- Quyết định ghi ở `handoff/decisions.md` #117–127.

## 4. Giới hạn trung thực (đã biết, không giấu)
- Chỉ hỗ trợ các định dạng trong bảng §2, ở mức đã test fixture. Chưa test
  trên game thật nào (M3 ở đây nghĩa là parse + ghi ngược + verify đã test
  fixture, chưa phải M4).
- Ghi ngược dạng "dựng lại" (JSON, CSV, YAML, INI, plist XML, .xcstrings)
  không bảo toàn comment/khoảng trắng gốc; chỉ dạng "theo span"
  (Properties, .strings, PO) mới giữ nguyên byte phần không sửa.
- YAML chỉ là tập con (xem §1); INI comment mất khi ghi; XML comment trước
  thẻ root bị mất; khai báo XML khi ghi lại dùng nháy đơn (vẫn hợp lệ).
- Verify của `properties` với file latin-1 không bắt được mọi byte lật
  trong value (byte lật thành ký tự latin-1 in được thì decode vẫn qua);
  file UTF-8 thì luôn bị từ chối (fixture conformance dùng UTF-8).
- SQLite chỉ đọc; bảng localization suy ra theo heuristic tên/cột, không
  đọc được schema lạ; giới hạn 100.000 entry chưa có test trực tiếp.
- `generic_strings` chỉ dò chuỗi thô — không phải bảng localization đã xác
  minh; không dò UTF-16 ngoài khoảng ASCII in được.
- XLIFF/RESX: có thể đổi tiền tố namespace khi ghi ngược (giữ nguyên ngữ
  nghĩa); không hỗ trợ `<alt-trans>`; RESX không khôi phục dữ liệu nhị phân.
- PO: không hỗ trợ encoding khác UTF-8/UTF-16; ghi lại ngắt dòng giá trị dài
  (không giữ cách ngắt gốc).
- Mã tương thích Python 3.9; đã kiểm trên Python 3.12.3 (sandbox). Chạy thật
  trên Python 3.9.16 (iSH) chưa có — xem mục 5.

## 5. Kiểm chứng trên iSH — NOT RUN (chờ Joseph, sáng 2026-11-16)

Kỳ vọng sau khi giải nén zip Claude 06 đè lên `/root/pb3` (như mọi lần):

```sh
cd /root/pb3
python3 run_tests.py --tier full
```

Kỳ vọng: `Ran 1025 tests ... OK (skipped=5, lỗi=0, fail=0)` — **1020 passed,
5 skipped** (2 thiếu `tomllib`, 1 máy có `/dev/location`, 1 UnityPy có mặt,
1 test signal ngoài main thread; số skip có thể lệch 1, quan trọng là 0 fail
0 lỗi). Thời gian khoảng 20+ phút. Nếu fail, xem `reports/failures.txt`
thay vì chụp màn hình.

Plugin CLI (trong `/root/pb3`):

```sh
python3 menu.py plugin list
python3 menu.py plugin test json_text
python3 menu.py plugin test csv_table
python3 menu.py plugin test xml_strings
python3 menu.py plugin test yaml_ini
python3 menu.py plugin test properties
python3 menu.py plugin test apple_plist
python3 menu.py plugin test apple_strings
python3 menu.py plugin test sqlite_loc
python3 menu.py plugin test po_mo
python3 menu.py plugin test xliff
python3 menu.py plugin test resx
python3 menu.py plugin test generic_strings
```

Kỳ vọng:
- `plugin list`: đủ 13 plugin, trạng thái [ok], 10 × M3 + 3 × M2, không có
  cảnh báo "khai M3 nhưng hiệu lực M2".
- Mỗi `plugin test <id>`: `KẾT QUẢ: PASS (8 PASS, 0 FAIL, 3 SKIP)` với đúng
  số fixture như bảng §3 (json 3, csv 4, xml 3, yaml 3, properties 2,
  apple_plist 4, apple_strings 2, sqlite 1, po_mo 2, xliff 2, resx 1,
  generic 3). Nếu plugin nào FAIL thì chụp nguyên văn kết quả gửi lại.
- Không cần kiểm gì thêm trên IPA thật ở bước này (Claude 07 mới dùng các
  plugin này để dò file ngôn ngữ trong game).

## 6. Việc cho Claude 07 (chưa làm)
- Dùng các plugin này qua `parse`/`build` của SDK, không viết lại parser
  (prompt 07 cấm viết lại parser của 06). Pipeline trích xuất gọi
  `list_candidates`/`probe`/`parse` của từng plugin; `generic_strings` là
  lưới cuối khi không plugin nào nhận diện được (luôn kèm cảnh báo false
  positive trong report).
- Lưu ý khi dùng `build`: plugin chỉ đọc (sqlite_loc, generic_strings) và
  định dạng con chỉ đọc (plist nhị phân, MO) từ chối bằng E_WRITE_DISABLED;
  entry unparsed (XLIFF/RESX markup, RESX nhị phân) bị bỏ qua kèm warning;
  entry conflict (key trùng) chỉ ghi bản ghi đầu.
- `support`/`kb` (ma trận hỗ trợ tự động) vẫn là Claude 09; chưa có lệnh
  `extract` hoàn chỉnh (Claude 07).
