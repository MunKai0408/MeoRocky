# Tài khoản đợt vá patch01 — extract crash trên IPA thật (9 Kings)

Ngày: 2026-11-16 (sáng, sau khi Joseph kiểm chứng zip final trên iSH).
Zip RIÊNG đợt này: `pb3_handoff_final_patch01_sandbox.zip` (kích thước + md5
chính xác ở thông báo bàn giao kèm zip). Zip final nguyên vẹn
(`pb3_handoff_final_sandbox.zip`, 1.124.103 byte).

## 1. Bằng chứng thật từ iSH (Joseph, 2026-11-16 ~08:00)

- Menu BearTea chạy tốt trên iSH, đủ [1]–[7] đều đã nối dịch vụ.
- Suite full Joseph hủy ở 299/1539 (ETA còn ~25m45s) — chưa có kết quả xanh/đỏ.
- Menu [2] extract lần đầu báo `E_INPUT_NOT_FOUND` — KHÔNG phải thiếu file:
  file game.ipa CÓ ở `/root/pb3/workspace/input/game.ipa` (141.865.757 byte,
  `-rw-r--r--`, 14/11); nguyên nhân là đường dẫn Joseph gõ thừa một dấu chấm
  cuối: `workspace/input/game.ipa.` thay vì `workspace/input/game.ipa`
  (bàn phím iOS hay tự chèn dấu chấm) — xem fix 4 ở §3.
- Extract chạy lại đúng đường dẫn thì **CRASH thật**: `[E_INTERNAL] Lỗi nội
  bộ. Chi tiết: AttributeError`, tại bước "kiểm tra ứng viên" 6/6; chưa có
  file xuất ra (đường dẫn xuất đúng ra là `/root/pb3/workspace/output/game/`).
  Log: `/root/pb3/logs/session-20261116-005829-18.log`.

## 2. Nguyên nhân gốc (đã tái hiện đúng trên sandbox Python 3.12)

Traceback trong log (đã có đuôi đầy đủ):

```
sdk/extraction.py:564 run_extraction
  -> sdk/formats.py:225 probe
    -> plugins/yaml_ini/plugin.py:1069 _parse_bytes
      -> :192 _parse_ini -> :172 _parse_ini_struct
        -> configparser.read_string("[ROOT]\n" + text)
          -> configparser.py line 1060, in _read: cursect[optname].append(value)
            -> AttributeError: 'NoneType' object has no attribute 'append'
```

Ngoại lệ đầu trong chuỗi: `configparser.MissingSectionHeaderError`,
file `'<string>'`, line 1: `'<?xml version="1.0" encoding="utf-8"?>\n'`.

Cơ chế chính xác:

1. Sniff của yaml_ini (`plugins/yaml_ini/plugin.py::_sniff`) chấm dòng XML
   có dấu `=` thành cặp key=value INI (0.7) — dòng
   `<?xml version="1.0" encoding="utf-8"?>` khớp regex
   `^[^#\s][^:=\n]*[:=]` — nên 1 file XML trong IPA thành ứng viên INI.
2. Probe gọi `_parse_bytes` → `_parse_ini_struct` đưa XML vào configparser.
   Lần đọc đầu lỗi `MissingSectionHeaderError` (dòng 1 là `<?xml ...?>`).
3. Lần đọc bù `"[ROOT]\n" + text`: dòng `<resources>` thành option KHÔNG
   giá trị (lưu `None` vì `allow_no_value=True`); dòng tiếp theo THỤT ĐẦU
   DÒNG (`    <string name="app_name">9 Kings</string>`) bị xem là dòng
   nối tiếp (continuation) của option đó → `cursect[optname].append(value)`
   gọi `.append` trên `None` → AttributeError. Đây là **bug có sẵn của
   configparser** (tồn tại cả trên Python 3.9 iSH lẫn 3.12 sandbox),
   không phải lỗi logic PB3.
4. Lỗi thoát khỏi probe vì `FormatPluginBase.probe` (`sdk/formats.py:222`)
   chỉ bắt `PB3Error`; `run_extraction` (`sdk/extraction.py:564`) cũng chỉ
   bắt `PB3Error` quanh `plugin.probe` → crash cả extract.

Repro khóa trong test (tái hiện đúng cả trên Python 3.12):

```python
from plugins.yaml_ini.plugin import _parse_ini_struct
xml = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
       '    <string name="app_name">9 Kings</string>\n</resources>\n')
_parse_ini_struct(xml, 'test.xml')  # trước vá: AttributeError; sau vá: E_CORRUPT
```

File XML không thụt dòng thì chỉ ra entry rác chứ không crash (đã kiểm
chứng) — vì không có dòng nối tiếp nào sau option không giá trị.

## 3. Đã sửa (4 fix bắt buộc)

1. **probe không bao giờ làm crash extract (2 lớp)** — `sdk/formats.py`:
   `FormatPluginBase.probe` thêm `except Exception` → trả
   `{"ok": False, "reason": "E_INTERNAL: <TênKiểu>"}` (giữ nguyên nhánh
   `PB3Error`); `sdk/extraction.py::run_extraction` quanh `plugin.probe`
   thêm `except Exception` → ghi vào `result.skipped` với lý do
   `probe <id> lỗi [E_INTERNAL: <TênKiểu>]` rồi tiếp tục. Lớp thứ hai bao
   cả plugin ghi đè `probe` (zip/godot_pck/unreal_pak/sqlite_loc đều chỉ
   bắt `PB3Error`/`OSError`).
2. **yaml_ini từ chối sớm XML/HTML** — `_sniff`: dòng đầu không trống bắt
   đầu bằng `<` (kể cả BOM) → trả 0.0 ngay; `_parse_ini`: nội dung bắt đầu
   bằng `<` → raise `PB3Error E_CORRUPT` trước khi chạm configparser.
   File XML trong IPA nay do plugin `xml_strings` nhận (kiểm chứng đầu-cuối:
   entry `config` → `xml_strings`, xuất `config.json`).
3. **`_parse_ini_struct` đổi mọi lỗi lạ thành E_CORRUPT** — cả hai lần
   `read_string` đều có thêm `except Exception` → `PB3Error E_CORRUPT`
   (sau nhánh `configparser.Error`, giữ `except PB3Error: raise`).
   `_build_ini` nay cũng chỉ nhận E_CORRUPT thay vì AttributeError.
4. **Bỏ dấu '.' cuối đường dẫn gõ tay** — `core/safe_io.py` mới
   `normalize_typed_path(path)`: bỏ mọi dấu `.` cuối CHỈ KHI đường dẫn có
   dấu chấm KHÔNG tồn tại nhưng bỏ đi thì TỒN TẠI; đường dẫn đã tồn tại
   (kể cả tên thật kết thúc bằng '.') và đường dẫn chưa tồn tại đều giữ
   nguyên; `"."`/`".."` không đổi. Gọi ở đầu `check_readable_input` (bao
   extraction qua containers, unity_tool, diagnostics, batch, godot/unreal)
   và ngay sau `ask()` ở 4 màn hình tương tác: language_tool, unity_tool,
   compress_tool, font_tool (cả đường dẫn font thay thế ở chế độ replace).

Quyết định #200–205 trong `handoff/decisions.md`.

## 4. Kiểm chứng (sandbox — chưa có gì trong đợt vá chạy trên iSH)

| Kiểm chứng | Kết quả |
|---|---|
| 20 test mới `tests/test_patch01.py` | Xanh hết (repro đúng log iSH + biến thể DOCTYPE/plist/CDATA/strings.xml, probe không crash, extraction đầu-cuối với XML thụt dòng, đường dẫn dấu chấm) |
| Full suite trong cây | `Ran 1559 tests in 81.5s OK (skipped=1, lỗi=0, fail=0)` (1539 cũ + 20 mới), Timeout 0, chậm nhất 2,8s |
| Mô phỏng gộp iSH (pty/TTY, COLUMNS=40, `PB3_TEST_TIMEOUT_SCALE=5`) | `Ran 1559 tests in 80.7s OK (skipped=1, lỗi=0, fail=0)`, Timeout 0, chậm nhất 2,6s |
| Kiểm chứng độc lập (giải nén zip ra thư mục mới) | spec md5 khớp; full suite 1559 xanh; extract thử `game1.zip` (21 entry, 6 file) + zip chứa XML thụt dòng (không crash, XML về đúng plugin `xml_strings`) |
| Zip final | Nguyên vẹn (không build lại) |

## 5. Checklist iSH cho Joseph (chỉ zip patch01 này)

```sh
cd /root/pb3
unzip -o ~/pb3_handoff_final_patch01_sandbox.zip
python3 run_tests.py --tier full
# kỳ vọng: Ran 1559 tests ... OK (skipped=5, lỗi=0, fail=0), ~30+ phút
# (1539 cũ + 20 mới; kỳ vọng ~1554 passed, 5 skipped — 1 skip sandbox + 4 skip iSH)

python3 menu.py
# chọn [2], gõ đúng: workspace/input/game.ipa  (KHÔNG có dấu chấm cuối;
# nay gõ thừa dấu chấm tool cũng tự bỏ — nhưng cứ gõ đúng cho chắc)
# kỳ vọng: extract chạy hết, không còn [E_INTERNAL] AttributeError;
#   file xuất ra ở /root/pb3/workspace/output/game/
ls /root/pb3/workspace/output/game/
```

- Nếu extract vẫn lỗi: chụp màn hình + gửi đuôi log mới trong
  `/root/pb3/logs/session-*.log` (lệnh: `grep -n "Traceback" -A 20
  /root/pb3/logs/session-<mới nhất>.log | tail -30`), đừng sửa tay trong cây iSH.
- Suite full lần trước mới chạy 299/1539 thì hủy — lần này để chạy hết một
  lần cho biết xanh hay đỏ (ETA ~30 phút, cắm sạc + tắt auto-lock + để iSH
  ở foreground).

## 6. Giới hạn trung thực + việc còn lại

- Chưa có gì trong đợt vá chạy trên iSH — extract trên IPA 9 Kings thật
  vẫn chờ Joseph chạy lại trên zip patch01.
- YAML bắt đầu bằng `<` (plain scalar, cực hiếm) cũng bị sniff 0.0 —
  chấp nhận vì YAML thật không mở đầu bằng thẻ đánh dấu.
- Probe của plugin ghi đè (zip/godot_pck/unreal_pak/sqlite_loc) vẫn chỉ bắt
  `PB3Error`/`OSError` trong thân chúng — đã được lưới ở `run_extraction`
  bao lại, không sửa từng file để tránh động chạm rộng.
- Việc còn lại: bản web Pyodide trên repo MunKai0408/MeoRocky (đang làm
  song song, không thuộc đợt vá này).
