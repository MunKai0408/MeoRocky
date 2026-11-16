# Phần (D) — Font: liệt kê + gom về 1 font đủ dấu (Joseph 20:48 ý 8)

Ngày: 2026-11-17. Harness: `exp/verify_analysis_font_unify.mjs` — **27/27 PASS, 0 FAIL**
(13 test Python mới + tĩnh + fixture + 5 game thật + UI 390px + hồi quy đầy đủ).
Kết quả chi tiết: `exp/analysis_font_unify_results.json`.

## 1. Cơ chế liệt kê + kiểm tra từng font

Module mới `pb3/sdk/font_list.py` (`scan_source`), gọi qua 2 op bridge mới
`font_list` / `font_unify` (cache `_LAST_FONT_SCAN` dùng lại kết quả liệt kê
khi bấm gom — không quét lại cả IPA). Chỉ đọc; danh sách đầy đủ ghi ra
`/tmp/font_list.json` để tải về.

Tái dùng, không tự định nghĩa lại bộ chữ:

- Bộ 142 mục (134 chữ cái + 8 dấu kết hợp): `sdk/font.py::REQUIRED_VIETNAMESE`.
- Đọc cmap TTF/OTF + gộp glyph: `sdk/font.py` (`coverage`, `merge_vietnamese`).
- Đọc TMP_FontAsset (FaceInfo, bảng glyph 36 byte, độ phủ 134+8):
  `sdk/tmp_font.py` (`scan_serialized_file`, `build_fallback_plan`,
  `build_glyph_plan`).
- Liệt kê Font object nhúng (class 128): `sdk/font.py::list_unity_font_objects`.

Một nguồn game (thư mục / `.ttf/.otf/.ttc` rời / `.assets/.unity3d`/bundle /
ZIP/IPA) cho ra 5 loại mục font, mỗi mục báo đủ bao nhiêu trên 142 + chữ/dấu
nào thiếu (tối đa 150 mục liệt kê chi tiết):

| Loại | Cách kiểm | Kết luận |
|---|---|---|
| `ttf` rời | đọc cmap thật | `full` (142/142) / `partial` + danh sách thiếu |
| `otf` rời | đọc cmap thật | `partial` (chỉ đọc được, không gộp được) |
| `ttc` | magic `ttcf` | `unsupported` — chưa hỗ trợ |
| `tmp_font_asset` | bảng glyph TMP | `full`/`partial` trên 142 (134 chữ + 8 dấu); không đọc được bảng glyph → `unreadable` |
| `unity_font` (class 128 nhúng) | chỉ tên/path_id | `unreadable` — không đọc được bytes font |

Giới hạn RAM tab (giống `sdk/tmp_font`): file Unity > 256 MB hoặc entry
ZIP/IPA > 32 MB thì bỏ qua kèm cảnh báo; entry VFS > 64 MB (giới hạn mặc
định `core/limits.py::max_entry_bytes`) thì `E_TOO_LARGE`.

## 2. Thao tác "dùng chung 1 font" — làm được gì / không làm được gì trên web

Điều tra trước khi code (xác nhận bằng tìm kiếm web + đối chiếu code):

- **TMP_FontAsset: web KHÔNG tạo được atlas SDF.** Tạo TMP_FontAsset/SDF
  chỉ làm được trong Unity Editor (Font Asset Creator + FontEngine;
  docs Unity TextMeshPro). Web chỉ lập được kế hoạch fallback C6
  (`tmp_fallback_plan.json`) + glyph plan (mục 7) để nạp vào Editor —
  tái dùng nguyên `build_fallback_plan` của C6, kèm script
  `unity_editor/TMP_VietnameseFallback.cs` đã có.
- **Font TTF rời: web gộp ĐƯỢC thật.** `sdk/font.merge_vietnamese` copy
  outline glyf + cmap từ Be Vietnam Pro vào font gốc; file đã gộp
  (`*.vi.ttf`) nằm trong `font_unified.zip` để tải về.
- **Font OTF/CFF: chỉ đọc được cmap, KHÔNG gộp được** (giới hạn
  `sdk/font.py`); **TTC: chưa hỗ trợ**.
- **Font nhúng trong SerializedFile (class 128): chỉ liệt kê** — không đọc
  được bytes, không sửa được object.
- **Web KHÔNG đóng gói font đã gộp trở lại vào `.assets`/IPA gốc** — thay
  trong Unity Editor (hoặc bản iSH để ghi ngược).

Mỗi font trong kế hoạch (`font_unify_plan/1`) có đúng 1 hành động:
`merge_glyphs` (TTF rời, làm thật trên web) | `tmp_fallback` (TMP, kế hoạch
C6 + script Editor) | `replace_with_donor` (OTF/TTC/nhúng — thay bằng
Be Vietnam Pro trong Editor) | `none_full` (đã đủ 142/142) |
`unreadable` (không đọc được nên không bịa hành động).

Nút bấm trong tab Font: **"Liệt kê font của game"** (`btn-font-list`) và
**"Dùng chung 1 font Be Vietnam Pro"** (`btn-font-unify`). Kết quả tải về
qua nút Lưu/Chia sẻ như mọi file xuất khác:

- `font_list.json` — danh sách font đầy đủ.
- `font_unified.zip` — `font_unify_plan.json` + các `*.vi.ttf` đã gộp +
  `tmp_fallback_plan.json` (nếu game có TMP) + `BeVietnamPro-Regular.ttf`
  (đủ 142/142, OFL, đã kiểm chứng lại bằng `sdk/font.coverage`).

## 3. Cách tích hợp trên web (khác gì cách gốc, vì sao)

- **Không dùng `services/font_tool.py`** (lớp dịch vụ CLI của iSH: tiến độ
  terminal + ghi ngược vào ZIP/IPA đã dựng lại). Web dùng tầng `sdk/`
  thuần qua bridge trong module worker Pyodide — đúng luồng file/lưu/
  chia sẻ của web, đúng mẫu các op font/TMP đã có (`font_scan`,
  `font_merge`, `tmp_font_read`, `tmp_font_fallback`).
- **Không nhét nguyên si cách làm ngoài:** kế hoạch fallback TMP tái dùng
  C6 thay vì viết lại; gộp TTF tái dùng `merge_vietnamese` thay vì dựng
  font mới; không cố tạo atlas SDF trên web (đã chứng minh là không làm
  được), không cố sửa Font object nhúng (đã chứng minh là không sửa được).
- **Cache quét** giống `_LAST_TMP_SCAN` của C6: nút gom dùng lại kết quả
  liệt kê cùng file (khóa path+size+mtime), tránh quét lại IPA 100–500 MB.

## 4. Bảng 5 game thật (CPython bridge, `exp/font_unify_5games.py`)

Không game nào có TMP_FontAsset **xác nhận được** (0 `tmp_items` cả 5 game)
— nhưng số MonoBehaviour chưa xác nhận class script (script nằm ngoài file,
thường trong bundle monoscripts) rất lớn, nên **không kết luận "game không
dùng TMP"**: TMP có thể nằm trong số ứng viên đó. Xem mục "Còn kẹt".

| Game | Font tìm được | Chi tiết từng font (trên 142) | Gom 1 font cho ra gì |
|---|---|---|---|
| 9 Kings (173 MB) | **3** (nhúng) | PerfectDOSVGA437, LiberationSans (resources.assets), NotInter-Regular (sharedassets4.assets) — cả 3 `unreadable` (chỉ liệt kê được) | `font_unified.zip` 53.339 B: plan + BeVietnamPro-Regular.ttf; 3 font → `replace_with_donor` (thay trong Editor). `sharedassets0.assets` 154.591.236 B bỏ qua: `E_TOO_LARGE` (giới hạn entry VFS 64 MB) — font trong đó (nếu có) chưa liệt kê được |
| data.unity3d (590 MB) | **0** | File 590.760.029 B vượt giới hạn 256 MB của quét Unity trên web → bỏ qua kèm cảnh báo, 0 font | `font_unified.zip` 53.108 B: plan rỗng + donor. Cần đường đọc streaming/chia nhỏ mới liệt kê được font trong bundle này |
| Exploding Kittens 2 (290 MB) | **8** (7 nhúng + 1 TTF rời) | 7 nhúng (PerfectDOSVGA437, BebasNeue-Regular, NotoSansJP/KR/Regular/Bold, LiberationSans) `unreadable`; **Roboto-Bold.ttf** (trong `GoogleSignIn.bundle`) **139/142, thiếu đúng 3 dấu kết hợp U+0302 (mũ), U+0306 (breve), U+031B (móc)** | `font_unified.zip` 121.189 B: plan + **Roboto-Bold.vi.ttf — đã gộp 3 glyph, xác minh lại 142/142** + donor; 7 font nhúng → `replace_with_donor` |
| Poly Bridge 3 (482 MB) | **16** (nhúng) | Fedoka-Regular, NotoSans-Regular, Aniara_Light/Bold/Regular/Graphics, ARIAL, NotoSansCJKkr/sc/jp-DemiLight, PerfectDOSVGA437, londrina.thin-regular, LiberationSans, Roboto-Regular/Bold, CONSOLA — cả 16 `unreadable` | `font_unified.zip` 53.596 B: plan + donor; 16 font → `replace_with_donor` |
| Passpartout (159 MB) | **7** (nhúng) | loremipsum, COUR, Perfect DOS VGA 437, LinLibertine_aDRS/aS, OptimusPrinceps, F25_Bank_Printer — cả 7 `unreadable` | `font_unified.zip` 53.429 B: plan + donor; 7 font → `replace_with_donor` |

Tổng: 34 font liệt kê được (33 nhúng + 1 TTF rời); 0 font đủ 142/142 trong
game (chưa font nhúng nào kiểm được độ phủ); 1 font gộp thật thành công
trên web (Roboto-Bold.ttf → 142/142, đã xác minh bằng cách đọc lại cmap
của file đã gộp).

## 5. Harness (D) — 27 mục

`node exp/verify_analysis_font_unify.mjs` — **27/27 PASS, 0 FAIL**:

1. Unit Python `tests.test_font_unify` **13 test OK** (TTF thiếu đúng 4 ký
   tự, TTF đủ, OTF/TTC, ZIP entry, TMP thiếu 42, Font nhúng, gộp thật ra
   142/142, tên entry Unity).
2. Tĩnh: files.json **93** (thêm `pb3/sdk/font_list.py`), tests.json **164**
   (thêm `pb3/tests/test_font_unify.py`), sw.js `CACHE_VERSION
   "v19-20261116"` + precache có `pb3/sdk/font_list.py` (131 mục);
   `node --check` app.js/worker.js/sw.js + `py_compile` bridge/font_list/
   test OK; `check_analysis_font_unify_static.py` 0 fails.
3. Fixture IPA tổng hợp (`exp/check_font_unify_fixture.py`): 5 font
   (TTF thiếu 138/142 đúng 4 ký tự U+0300/U+0301/U+1EA0/U+1EA1, TTF đủ,
   OTF, TMP 100/142 thiếu 42, Font nhúng path_id 40) → hành động đúng từng
   loại; zip có đủ 4 entry (plan + GameFont.vi.ttf 142/142 + fallback
   plan + donor).
4. 5 game thật: đúng số font từng game (3/0/8/16/7), EK2 merged đã xác
   minh 142/142.
5. UI Chromium thật 390px (`exp/out/font_unify/ui_font_unify_390px.png`):
   2 nút có sẵn, liệt kê ra đủ 5 font fixture, gom ra kế hoạch +
   `font_unified.zip`, **0 pageerror, không tràn ngang**.
6. Hồi quy xanh: module-worker 17/17, C2 13/13, C9 16/16, (A) 33/33,
   (B) 25/25, (C) 26/26, Gộp 1 file 27/27 (xác nhận thêm bằng
   `analysis_merge_one_file_results.json` 27/27, 0 fails), item8 static
   0 fails, full suite Python **1.719 test OK (skipped=2)**.

   Lỗi thoáng qua đã gặp và cách xử lý (ghi rõ, không giấu): trong lần
   chạy hồi quy đầu, mục `regress_full_suite_python` báo **1.694 test,
   FAILED** — thiếu đúng 25 test của `test_multifile` so với 1.719.
   Không đổi code gì, chạy lại full suite **3 lần liên tiếp đều 1.719
   OK (skipped=2)**. Kết luận: lỗi thoáng qua do các tiến trình harness
   lồng nhau (merge harness chạy full suite riêng ở section 5 của nó)
   chưa tắt hẳn khi suite của harness này bắt đầu — đã thấy tiến trình
   `verify_analysis_translate_full/mod_search/reselect.mjs` còn tồn tại
   trong `ps` sau khi harness của chúng báo xong. Cùng lần chạy đó,
   harness crash `EPERM` khi ghi `analysis_font_unify_results.json`
   (file bị cắt về 0 byte) — ghi lại sau đó bình thường; harness đã
   được thêm fallback ghi file `.fallback.json` nếu ghi chính lỗi.

## 6. File đổi trong đợt đẩy

- Thêm: `pb3/sdk/font_list.py`, `pb3/tests/test_font_unify.py`,
  `exp/check_analysis_font_unify_static.py`,
  `exp/check_font_unify_fixture.py`, `exp/font_unify_5games.py`,
  `exp/verify_analysis_font_unify.mjs`, `exp/analysis_font_unify_results.md`,
  `exp/analysis_font_unify_results.json`.
- Sửa: `pb3/bridge.py` (op `font_list`/`font_unify` + cache),
  `pb3/files.json` (92→93), `pb3/tests.json` (163→164), `sw.js`
  (CACHE_VERSION v18→v19 + precache `pb3/sdk/font_list.py`),
  `index.html` + `app.js` (2 nút + handler tab Font).
- Cập nhật kỳ vọng đếm file trong toàn bộ harness cũ: `verify_limit_2gb.mjs`,
  `verify_module_worker.mjs`, `verify_patch01_web.mjs`,
  `verify_phase1_ci.mjs`, `check_item8_static.py`,
  `check_analysis_c6_static.py`, `check_analysis_c7_static.py`,
  `check_analysis_c8_static.py`, `check_analysis_c9_static.py`,
  `verify_analysis_c8.mjs`, `verify_analysis_c9.mjs`,
  `verify_analysis_reselect.mjs`, `verify_analysis_merge_one_file.mjs`
  (92→93, 163→164, v18→v19; docstring ghi số cũ đã sửa theo).
- Không đụng: `.github/workflows/`, cây iSH (`~/workspace/pb3_fix`).
- FAIL duy nhất còn lại (đã biết từ trước, không sửa trong phần này):
  item6 vỏ trang raw ~372 KB > 100 KB (gzip đạt).

## 7. Còn kẹt / việc chưa làm được

1. **TMP chưa xác nhận được trên game thật.** 0 `tmp_items` cả 5 game,
   nhưng ứng viên MonoBehaviour chưa xác nhận class script rất lớn
   (9 Kings 22.206, PB3 21.148, Passpartout 6.159, EK2 1.116) — script
   TMP (nếu có) nằm trong bundle monoscripts ngoài file nên không đối
   chiếu được. Cần đường phân giải script xuyên file (đọc MonoScript
   trong bundle monoscripts/globalgamemanagers) mới biết chắc game có
   TMP_FontAsset hay không. Đây là việc mới, ngoài phạm vi phần (D).
2. **Font trong `sharedassets0.assets` của 9 Kings (154 MB) chưa liệt kê
   được** — entry VFS giới hạn 64 MB (`E_TOO_LARGE`). Cần đường đọc
   streaming/slice (như trích xuất đã làm cho data.unity3d) mới quét
   được file .assets lớn.
3. **data.unity3d 590 MB chưa liệt kê được font** — vượt giới hạn 256 MB
   của quét Unity trên web (bảo vệ RAM tab Safari). Cùng đường streaming
   như mục 2.
4. **Font nhúng (33/34 font liệt kê được) không kiểm được độ phủ và
   không sửa được trên web** — chỉ có đường thay bằng Be Vietnam Pro
   trong Unity Editor. Đây là giới hạn thật của `sdk/font.py`, đã ghi rõ
   trong plan + UI.
5. Chưa kiểm chứng trên Safari thật (nút Lưu/Chia sẻ với file zip,
   RAM tab với IPA 100–500 MB) — cần Joseph thử trên máy thật.
