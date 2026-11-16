# Tài khoản đợt vá patch02 — danh sách kiểm font thiếu 30 chữ Latin-1

Ngày: 2026-11-16 (sáng, Joseph phát hiện khi rà lại con số 112).
Zip RIÊNG đợt này: `pb3_handoff_final_patch02_sandbox.zip` (kích thước + md5
chính xác ở thông báo bàn giao kèm zip). Zip patch01 và zip final nguyên vẹn
(`pb3_handoff_final_patch01_sandbox.zip`, 1.135.576 byte;
`pb3_handoff_final_sandbox.zip`, 1.124.103 byte).

## 1. Phát hiện (Joseph, 2026-11-16 ~08:51)

- Joseph nhớ bộ đầy đủ là **134 ký tự và chữ cái tiếng Việt**, trong khi
  danh sách kiểm của tool font chỉ có 112 — ĐÚNG, và đây là lỗ hổng thật
  của bài kiểm tra (không phải lỗi font).
- Bộ 134 = 120 chữ có dấu (12 gốc a ă â e ê i o ô ơ u ư y × 5 dấu
  huyền/sắc/hỏi/ngã/nặng × 2 case) + 14 gốc đặc biệt (ă â đ ê ô ơ ư,
  2 case). Không tính 12 nguyên âm thường (font nào cũng có) và không
  tính dấu kết hợp.
- Danh sách cũ (112 = 14 gốc đặc biệt + dải U+1EA0–U+1EF9 (90) + 8 dấu
  kết hợp) thiếu **30 chữ** nằm ngoài dải U+1EA0–U+1EF9: à á ã è é ì í
  ò ó õ ù ú ý ĩ ũ (thường + hoa) — toàn chữ rất thường gặp. Một font
  thiếu à/á vẫn có thể qua được kiểm tra 112/112.
- Font nhập sẵn Be Vietnam Pro KHÔNG có vấn đề: đã kiểm bằng chính
  parser cmap của PB3 — đủ cả 134/134 chữ cái, đủ 30/30 chữ Latin-1,
  đủ 8/8 dấu kết hợp. Chỉ danh sách kiểm là thiếu, không phải font thiếu.

## 2. Nguyên nhân gốc

- Danh sách cũ liệt kê dải U+1EA0–U+1EF9 (khối Latin Extended Additional)
  làm "toàn bộ chữ ghép sẵn tiếng Việt", nhưng nhiều chữ có dấu của tiếng
  Việt nằm ở khối Latin-1 (U+00C0–U+00FF: à á ã è é ì í ò ó õ ù ú ý,
  28 chữ cả 2 case) và Latin Extended-A (ĩ U+0129, ũ U+0169) — ngoài
  dải đó nên không được kiểm. Đếm lại: 120 chữ có dấu − 90 chữ trong
  dải U+1EA0–U+1EF9 = đúng 30 chữ bị sót.

## 3. Đã sửa

1. **`sdk/font.py` — sinh bộ 134 bằng code thay vì liệt kê dải.**
   `_accented_vietnamese()`: 12 gốc × 5 dấu kết hợp, chuẩn hóa NFC thành
   ký tự ghép sẵn đơn (vd a + U+0300 → à) + `.upper()` cho case hoa; tổ
   hợp nào không ghép được (không xảy ra với tiếng Việt) thì bỏ qua,
   không làm vỡ import. `REQUIRED_VIETNAMESE = _VI_BASE (14) |
   _VI_ACCENTED (120) | _VI_MARKS (8)` = **142 mục**; hằng `_VI_PRECOMPOSED`
   (dải U+1EA0–U+1EF9) đã bỏ. API cũ (`required_vietnamese_chars`,
   `missing_chars`, `coverage`) nguyên vẹn. Comment "112 ký tự" →
   "142 mục (134 chữ cái + 8 dấu kết hợp)".
2. **Rà soát mọi chỗ nhắc 112** — `services/font_tool.py` (docstring
   chế độ 4: 112/112 → 134/134 chữ cái, 142 mục kiểm),
   `tests/fixtures/font/build_fixtures.py` (docstring `vietnamese_cps`).
   `docs/`, menu text, `ui/` không hề nhắc con số 112 nên không có gì
   sửa. Tài liệu lịch sử (`handoff/final_font.md`,
   `handoff/account_final.md`, decisions #195, các hàng cũ trong
   CURRENT_STATE.md) GIỮ NGUYÊN số 112 — đó là ghi chép đúng về thời
   điểm đó, không viết lại lịch sử.
3. **Test hồi quy** — `tests/test_font.py::TestPatch02Vietnamese134`
   (3 test mới, định nghĩa độc lập bộ 134 trong test, không import hằng
   số của code): (a) len(REQUIRED_VIETNAMESE) == 142 và tập chữ cái
   (trừ 8 dấu) == đúng bộ 134 sinh từ gốc + dấu; (b) Be Vietnam Pro
   nhập sẵn đủ 134/134 chữ cái (total 142, is_full); (c) font giả
   thiếu à/á → missing_chars == [0xE0, 0xE1] đúng 2 chữ — khóa lỗ hổng.
   Các test cũ cập nhật theo số mới: font thiếu chữ báo 135/142 (trước
   105/112), gộp +135 glyph (trước +105), tiến độ gộp 270 (trước 210),
   cmap fixture 95+142.

Quyết định #206–208 trong `handoff/decisions.md`.

## 4. Kiểm chứng (sandbox — chưa có gì trong đợt vá chạy trên iSH)

| Kiểm chứng | Kết quả |
|---|---|
| 3 test mới `tests/test_font.py::TestPatch02Vietnamese134` | Xanh hết (bộ 142/134 đúng, Be Vietnam Pro đủ 134/134, font thiếu à/á báo thiếu đúng 2 chữ) |
| Full suite trong cây | `Ran 1562 tests in 83.4s OK (skipped=1, lỗi=0, fail=0)` (1559 cũ + 3 mới), Timeout 0, chậm nhất 2,8s |
| Mô phỏng gộp iSH (pty/TTY, COLUMNS=40, `PB3_TEST_TIMEOUT_SCALE=5`) | `Ran 1562 tests in 84.4s OK (skipped=1, lỗi=0, fail=0)`, Timeout 0, chậm nhất 2,6s |
| Kiểm chứng độc lập (giải nén zip ra thư mục mới) | spec md5 khớp; full suite 1562 xanh (90,5s); plugin list 20 [ok]; font scan Be Vietnam Pro báo đủ tiếng Việt (142/142 ký tự, 527 glyph); font thiếu chữ báo thiếu 135/142, ký tự thiếu đầu tiên là À Á Â Ã È É Ê Ì (đúng nhóm Latin-1 từng bị sót) |
| Zip patch01 + zip final | Nguyên vẹn (không build lại) |

Lưu ý: mỗi lần chạy full suite có 1 test hồi phục sau retry của runner
(in-tree: `TestLanguageCli::test_cli_apply_dry_run`; mô phỏng iSH:
`TestLanguageCli::test_cli_apply_write_plain`) — retry xong xanh, không
phải lỗi sản phẩm; log không ghi lý do lần fail đầu.

## 5. Checklist iSH cho Joseph (chỉ zip patch02 này — TÍCH LŨY, gồm cả fix patch01)

```sh
cd /root/pb3
unzip -o ~/pb3_handoff_final_patch02_sandbox.zip
python3 run_tests.py --tier full
# kỳ vọng: Ran 1562 tests ... OK (skipped=5, lỗi=0, fail=0), ~30+ phút
# (1559 cũ + 3 mới; kỳ vọng ~1557 passed, 5 skipped — 1 skip sandbox + 4 skip iSH)

python3 menu.py
# chọn [2], gõ đúng: workspace/input/game.ipa  (KHÔNG có dấu chấm cuối;
#   bản vá patch01 đã tự bỏ dấu chấm thừa, nhưng gõ đúng vẫn tốt nhất)
# kỳ vọng: extract hoàn thành, KHÔNG crash ở "kiểm tra ứng viên" (fix patch01),
#   output trong /root/pb3/workspace/output/game/

python3 menu.py
# chọn [7] font, quét font trong game / font Be Vietnam Pro nhập sẵn
# kỳ vọng: font nhập sẵn báo đủ 134/134 chữ cái (142/142 mục kiểm)
```
