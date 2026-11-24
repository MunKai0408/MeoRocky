# P0 — Kết quả đo phủ ghi ngược MonoBehaviour (writeback coverage)

Ngày: 23/11/2026. Repo MeoRocky, main v76 (`71352f86`), nhánh làm việc
`feat/p0-writeback-coverage` (chưa push — commit/PR qua Bear API do main
agent làm sau). Thiết kế theo
`~/workspace/goals/meorocky-repo-editing/hidden_files/p0-design/plan.md`
(D1–D8, §4–§6, gồm sửa D4/T4(b) theo verify Minh phần 2).

Quy ước nhãn số: [ĐÃ ĐO THẬT] = đã đo/chạy, có log hoặc test trong repo;
[FIXTURE] = tổng hợp, không phải game thật; [HYPOTHESIS] = chưa đo.
Mọi số dưới đây là [ĐÃ ĐO THẬT] trừ khi ghi nhãn khác.

## 1. Mục tiêu + phạm vi (và loại trừ có chủ ý)

**Mục tiêu:** chứng minh + đo được đường ghi ngược (writeback) cho bảng
ngôn ngữ dạng MonoBehaviour trên game Unity iOS thật: bảng I2 Localization
(`mTerms`) và Unity StringTable (`m_TableData`/`m_Localized`). Pass khi:
phủ ghi > 0 trên ≥ 2 game thật, verify xanh, sha256 file gốc không đổi,
có regression test mới. **Kết quả: ĐẠT trên cả 3 game** (xem §4).

**Phạm vi P0 — đã làm:**
- Đo phủ ghi ngược end-to-end trên BẢN COPY của 9K v1.0.6, PB3 v1.1.7,
  EK2 v0.0.18 với bản dịch giả `"[VI] " + gốc` (D7).
- Khóa thiết kế writer đã có (M1) + sửa CHỈ 1 lỗi mà phép đo chứng minh
  (fallback locale, xem §5).
- Diffverify-equivalent cho MonoBehaviour (D4, 4 lớp) + tích hợp sẵn có
  vào `rebuild_apply`.
- Cập nhật SUPPORT_MATRIX + đính chính các doc lỗi thời ở Bước 0 §3 liên
  quan P0 (README mục (E), HANDOVER §1, `index.html` mục (E),
  docstring `il2cpp.py:39`).

**Loại trừ có chủ ý — KHÔNG làm ở P0:**
- Không viết writer mới cho bảng list kiểu Kusa (`text_data_list`):
  extraction nhận diện được, writer chưa có đường cho kind này → chỉ đo
  + báo là nhóm chưa phủ được (xem §6).
- Không thiết kế lại CSV: chỉ đo trên dữ liệu thật PB3 (M3 writer đã có).
- Không đụng IL2CPP literal (P2), font/TMP (P3), catalog CRC/hash (P4),
  đóng gói IPA (P1 — M2 `repack_ipa` đã có), REBUILD_ALL/M4,
  delivery_gate (đã là v2), UI mới.
- Không chạy full suite ở đây (T7): main agent chạy verify sau.

## 2. Thiết kế + quyết định chính

Theo plan P0 D1–D8, không đổi trừ 2 điểm sau verify Minh phần 2:

- **D1 — M1 LÀ writer của P0, không viết lại.** Phép đo chứng minh M1
  đủ cho 9K/PB3 (không sửa `sdk/` cho 2 game này); chỉ 1 lỗi được chứng
  minh trên EK2 → sửa tối thiểu (xem §5). Code mới của P0 chỉ là harness
  đo (`pb3/tests/test_p0_writeback_coverage.py`) + báo cáo này + sửa lỗi
  kèm regression test.
- **D4 lớp 4 (sửa theo verify Minh):** ở REBUILD_SECTION, phép so byte
  toàn file PHẢI LOẠI TRỪ object table (`byte_start`/`byte_size` thay đổi
  hợp lệ theo D3); chỉ PATCH_INPLACE mới bắt mọi byte ngoài vùng object
  đã đổi phải giống hệt gốc. Harness so `object_bytes` theo từng
  `path_id` (không so byte thô toàn file) nên object table không bao giờ
  bị so — đúng luật mới; định dạng/thứ tự object/`data_offset` của
  object table do lớp 1 (`_verify_assets`) kiểm. Bản cũ bắt cả object
  table giống hệt sẽ fail oan ở REBUILD_SECTION.
- **T4(b) (sửa theo verify Minh):** kiểm "mọi key
  `string_table_other_unit`", không ghi cứng 100 key (số 100 gắn với
  dry-run Build 68, chưa chắc đúng với bản dịch giả). Harness tính tập
  key ST kỳ vọng từ en.json + `mb_index` (key `Collection/Key` mà
  collection có trong SharedTableData, giá trị gốc khác rỗng) và đối
  chiếu tập hợp với tập key ST đã đổi thật.
- **Bản copy IPA để trong workspace** (`~/workspace/p0-implementation/work/`),
  KHÔNG dùng /tmp (tmpfs 512 MB; 3 IPA ≈ 945 MB + bản patch — bài học
  F4 run-1). Harness tự dọn thư mục work khi test đạt, giữ lại khi test
  fail để điều tra.

## 3. File đổi / thêm

| File | Đổi gì |
|---|---|
| `pb3/tests/test_p0_writeback_coverage.py` | MỚI — 8 test (T1–T6 theo plan §4; T7 không chạy ở đây). `unittest.TestCase`, `@slow`, `timeout_s = 1800`, skip khi thiếu IPA thật. |
| `pb3/sdk/unity.py` | SỬA — thêm `ls_source_column()` (fallback locale, xem §5) + dùng ở 2 chỗ lookup cột I2 (`language_source_tt_targets`, nhánh byte-walk của `monobehaviour_write_targets`). |
| `pb3/sdk/rebuild.py` | SỬA — `_collect_mb_info` dùng chung `ls_source_column()` cho cột I2 (phân loại unchanged/unmatched đúng cho EK2). |
| `pb3/tests/test_mb_write_rest.py` | THÊM — 3 test [FIXTURE] cho fallback locale ở đường type-tree (variant / ưu tiên exact / không khớp). |
| `exp/p0_writeback_results.md` | MỚI — báo cáo này. |
| `pb3/docs/SUPPORT_MATRIX.md` | SỬA — thêm hàng writer MonoBehaviour + sửa câu "Patch Unity chỉ mới test fixture" (lỗi thời). |
| `README.md`, `HANDOVER.md`, `index.html`, `pb3/sdk/il2cpp.py` | SỬA — đính chính doc lỗi thời ở Bước 0 §3 (xem §7). |
| `pb3/tests.json` + 8 script `exp/check_*_static.py` | SỬA — đăng ký file test mới: tests.json 208 → 209 file. |

## 4. Bảng verify — game × metric [ĐÃ ĐO THẬT]

Máy đo: VM Chí, 23/11/2026. Bản dịch giả `"[VI] " + gốc` (D7); key gốc
`""` giữ `""` (đếm unchanged). Mọi lần chạy: preview totals == apply
totals (T1, có preview); kiểm chứng độc lập đọc lại từng key đã đổi ==
`"[VI] ..."`; sha256 IPA gốc không đổi sau mỗi lần chạy (assert trong
harness). Chế độ ghi: REBUILD_SECTION (mọi bản dịch giả đều dài hơn gốc
nên luôn leo thang — PATCH_INPLACE quy mô game chưa đo, xem §6).

### 4.1. Tổng phủ ghi ngược (T1–T3)

| Game (build) | en.json keys | Đổi | Giữ nguyên | Không khớp | Từ chối | Đơn vị patch | Thời gian |
|---|---|---|---|---|---|---|---|
| 9 Kings v1.0.6 (sha `3b8cb699e593…`) | 2252 | **2250** | 2 | 0 | 0 | 2 | 43,6 s |
| PB3 v1.1.7 (sha `6955b86ab2a2…`) | 2348 | **4696** | 0 | 0 | 0 | 2 | 240,1 s |
| EK2 v0.0.18 (sha `3cfe35fd5e1f…`) | 1344 | **1325** | 19 | 0 | 0 | 1 | 435,8 s |

Phủ key: 9K 2250/2252 (99,9%), PB3 2348/2348 (100% key, ghi 2 lần —
xem §4.2), EK2 1325/1344 (98,6%). **Cả 3 game đều phủ > 0 → tiêu chí
pass P0 đạt (cần ≥ 2 game).**

### 4.2. Phủ theo game × đơn vị × kind

| Game | Đơn vị | Kind | Đổi | Kích thước trước → sau (byte) | Mode |
|---|---|---|---|---|---|
| 9K | `Payload/9Kings.app/Data/resources.assets` | language_source (I2) | 2150 | 25.558.592 → 25.490.536 | REBUILD_SECTION |
| 9K | `.../Raw/aa/iOS/localization-string-tables-english(en)_assets_all.bundle` | string_table | 100 | 5.090 → 11.244 | REBUILD_SECTION |
| PB3 | `Payload/ipa.store.app/Data/Raw/Localization/PB3_LOC_DEFAULT.csv` | csv | 2348 | 966.957 → 978.698 | REBUILD_SECTION |
| PB3 | `Payload/ipa.store.app/Data/resources.assets` | language_source (I2) | 2348 | 59.285.312 → 59.296.960 | REBUILD_SECTION |
| EK2 | `Payload/ipastore.app/Data/resources.assets` | language_source (I2) | 1325 | 1.832.616 → 1.839.152 | REBUILD_SECTION |

Ghi chú theo game:
- **9K:** 2 key giữ nguyên = 2 key ST rỗng (`Spells/Cemetery_Desc`,
  `Spells/Rebirth_Desc`, gốc `""` → giả `""` theo D7). T4(b): tập key
  ST đã đổi == tập key ST kỳ vọng (100/100), 0 key rò sang
  `resources.assets`.
- **PB3:** MỖI key trong en.json (2348) tồn tại ở CẢ 2 nơi — file CSV
  `PB3_LOC_DEFAULT.csv` VÀ bảng I2 trong `resources.assets` — và (E)
  ghi cả 2 nơi (4696 = 2348 × 2). Đây là hành vi hiện tại của (E) (1
  plan mỗi đơn vị), không phải lỗi P0 sửa; game đọc nguồn nào trong 2
  nguồn này thì chưa đo (cần test máy thật, xem §6). Đo CSV toàn bảng
  lần đầu: 2348/2348 key, đọc lại đúng, 0 ô lệch (trước đây chỉ có đo
  nhỏ 1 entry/3 change — xem §7).
- **EK2:** 19 key giữ nguyên = 19 key có giá trị gốc `""`. Chỉ 1 đơn
  vị patch (`resources.assets`, bảng `I2Languages` pid 1666).

### 4.3. Diffverify 4 lớp (D4) — số object/entry đã kiểm

| Game | Object đã kiểm byte (lớp 4) | Entry bundle khác đã kiểm | Kết quả |
|---|---|---|---|
| 9K | 19.754 | 0 (bundle ST chỉ có 1 entry) | Mọi object ngoài vùng patch byte-identical |
| PB3 | 1.651 | 0 | Mọi object ngoài vùng patch byte-identical |
| EK2 | 2.321 | 0 | Mọi object ngoài vùng patch byte-identical |

Lớp 1–3 (cấu trúc / giá trị / đơn vị) do writer + plan tự verify
(`_verify_assets`, `_verify_mb_values`, sha object == `after_sha` trong
plan) — không có `E_VERIFY_FAILED` nào trong cả 3 lần chạy.

### 4.4. Các test còn lại (T4–T6)

| Test | Kết quả [ĐÃ ĐO THẬT] |
|---|---|
| T4a — bundle ST 9K KHÔNG có `mb_index` | 0 key ST được ghi, không crash (fail-closed: `build_plan` báo `ValueError "Khong lap duoc entry nao"`, preview changed = 0 / refused = 0) |
| T4b — ST join có `mb_index` đầy đủ | Gộp trong T1: 100/100 key ST rơi đúng bundle ST, 0 key rò sang `resources.assets` |
| T5 — chuỗi dài hơn leo thang | Term I2 thật 9K (`Achievement_HisWinningness`), mode PATCH_INPLACE + bản dịch dài hơn → leo thang REBUILD_SECTION, verify xanh, đọc lại đúng. Phát hiện thêm: output REBUILD_SECTION có thể NHỎ HƠN gốc (rebuild nén lại padding căn chỉnh giữa các object — probe: chỉ object 15312 đổi (+72 B), file nhỏ đi 78.744 B) |
| T6 — key generic mơ hồ (>1 locator) | 9K: 18.603 key generic / 9 cặp mơ hồ / 8 đơn vị (31 đơn vị quét); PB3: 24.736 / 3 / 2 (307 đơn vị); EK2: 25.669 / 6 / 5 (112 đơn vị). Tỉ lệ mơ hồ < 0,05% ở cả 3 game |
| Module P0 đầy đủ (8 test) | **8/8 đạt, 0 lỗi, 0 fail, 773,3 s** (log `~/workspace/p0-implementation/p0-all.log`) |

## 5. Bug tìm được + đã sửa

### BUG P0-1 — Writer không ghi được EK2: lookup cột locale chỉ khớp chính xác [ĐÃ ĐO THẬT]

**Triệu chứng (trước fix):** T3 chạy lần đầu: phủ ghi EK2 = 0
(1344 key unmatched, 0 đơn vị, 428,5 s). `build_plan` trên
`resources.assets` báo `ValueError "Khong lap duoc entry nao"`.

**Nguyên nhân gốc (đo thật):**
- Bảng I2 của EK2 (`I2Languages`, pid 1666, `resources.assets`) có các
  cột: Status (`""`), Description (`""`), English (UK) `en-GB`,
  English (US) `en-US`, German `de`, French `fr`, … — KHÔNG có cột
  nào mã đúng `"en"`.
- Trích xuất lấy cột `en-GB` làm nguồn `"en"` (en.json 1344 key từ
  `resources.langtable.monobehaviour.en-gb.json`), nhưng writer
  (`monobehaviour_write_targets` + `language_source_tt_targets`) tìm
  cột bằng `lang.get("code") == source_locale` (khớp chính xác) →
  0 target, kèm note `"bảng I2 không có cột 'en' — không ghi được"`.
- Với `source_locale="en-GB"` (probe): 1344 target, plan 1325 change /
  0 unmatched / 0 refused — chứng minh writer đủ sức ghi, chỉ sai
  chỗ chọn cột.

**Fix (tối thiểu):** thêm `ls_source_column(languages, source_locale)`
(`pb3/sdk/unity.py`): ưu tiên khớp chính xác mã locale (giữ hành vi
cũ); nếu không có, lấy cột ĐẦU TIÊN cùng họ ngôn ngữ (mã gốc sau dấu
`-`, vd `"en"` → `"en-GB"` — khớp quy tắc họ locale của
`discovery.locale_in_wanted`, viết lại 1 dòng để unity không import
discovery). Không có cột nào khớp → `None` (fail-closed, như cũ).
Áp dụng ở 2 chỗ lookup cột I2 trong writer (đường type-tree +
byte-walk) và 1 chỗ phân loại trong `_collect_mb_info`
(`pb3/sdk/rebuild.py` — để 19 key rỗng EK2 phân loại đúng thành
unchanged thay vì unmatched với lý do `monobehaviour`).

**Kiểm chứng sau fix:** T3 chạy lại: 1325 đổi / 19 giữ nguyên /
0 unmatched / 0 refused (435,8 s); module P0 đầy đủ 8/8 đạt.
Regression test: 3 test [FIXTURE] mới trong
`test_mb_write_rest.py::TestTypetreeLanguageSource` (fallback sang
biến thể đầu tiên / ưu tiên khớp chính xác / không khớp họ nào →
`None`); T3 là regression test trên game thật cho đường byte-walk.

**KHÔNG sửa (ghi nhận follow-up):** cùng kiểu lookup chính xác còn ở
nhánh StringTable (`unity.py:3448` `m_LocaleId.m_Code`,
`rebuild.py:522`) và cột CSV (`csvutil.py:93`) — chưa có game thật nào
đo được trường hợp biến thể ở 2 chỗ này nên chưa sửa (xem §6).

## 6. Giới hạn còn lại / rủi ro / cần Duy test trên máy thật

- **Chưa kiểm chứng trên máy thật (R1/R2/R3):** P0 chỉ chứng minh đúng
  ở cấp byte + parse lại. Game có chấp nhận bundle đã sửa khi chạy
  không (catalog CRC/hash — thuộc P4), Unity có chấp nhận layout
  REBUILD_SECTION không, và chuỗi ST chèn mới (`string_table_element_template`
  copy từ entry đầu) có hiển thị đúng không — đều cần Duy cài bản đã
  patch (qua M2 repack + tự ký lại) và kiểm tra: (a) game mở được,
  không văng lúc load; (b) chuỗi có tiền tố `"[VI] "` hiện đúng chỗ
  (cả chuỗi I2 lẫn chuỗi ST, vd. tên phép trong 9K); (c) chuỗi nào
  không đổi thì báo lại tên/key để đối chiếu bảng phủ; (d) gửi kèm ảnh
  chụp màn hình chỗ hiện/không hiện.
- **PATCH_INPLACE quy mô game chưa đo:** mọi bản dịch giả `"[VI] "`
  đều dài hơn gốc nên T1–T3 chỉ đo đường leo thang REBUILD_SECTION.
  Đo PATCH_INPLACE quy mô game là follow-up, không chặn P0.
- **Dự phòng gate OOM khi đo T2 (R6, plan v3):** IPA PB3 482 MB trong
  VM 6 GB — nếu gate toàn IPA OOM thì đo theo từng đơn vị
  bundle/.assets (giải nén IPA ra workspace, gate theo đơn vị), hoặc
  chia nhỏ file dịch; chỉ dùng gate toàn IPA khi đủ bộ nhớ. (Bổ sung
  23/11 theo verify Minh v2 — chưa phải dùng tới ở P0 vì T2 chạy đủ bộ
  nhớ.)
- **Key có nhiều giá trị gốc khác nhau (phát hiện, chưa sửa):** PB3 có
  189 key có > 1 giá trị gốc trong `mb_values` (đo thật). Vd
  `UI_SANDBOX_STYLE` = `'Style {0} of {1}'` trong CSV nhưng `'Style'`
  trong MB — en.json giữ giá trị CSV, (E) áp bản dịch đó cho CẢ 2 nơi,
  tức là chèn placeholder `{0}`/`{1}` vào chỗ gốc không có placeholder.
  Plan có cảnh báo `"placeholder doi"` nhưng vẫn ghi. Đây là hành vi
  gộp-xung-đột hiện tại, cần quyết định sản phẩm (dịch theo từng nơi
  xuất hiện hay giữ nguyên 1 bản dịch/key) — không phải lỗi writer P0.
- **Generic không bao giờ tới được bản dịch gộp (thiết kế, không phải
  lỗi):** en.json gộp của cả 3 game chứa 0 key generic (quy tắc
  MERGE-MB-GENERIC: generic chỉ gộp khi game KHÔNG có nguồn có cấu
  trúc). Writer generic vì thế không chạm tới được qua flow (E) thường
  cho 3 game này — phủ generic thực tế qua (E) = 0 key, dù writer có
  thể ghi key generic không mơ hồ (đã chứng minh 1 key thật trước đây).
  Key generic mơ hồ bị bỏ qua theo thiết kế: 9K 9 cặp, PB3 3 cặp,
  EK2 6 cặp (T6).
- **Bảng list kiểu Kusa (`text_data_list`):** extraction nhận diện,
  writer chưa có kind này → nhóm key này hiện không có đường ghi (loại
  trừ §1, để P5 hoặc task riêng).
- **T7 full suite (đã đo trên nhánh này, máy Dũng, xong 19:24 +07
  23/11):** 2.968 test, 2.950 đạt, **0 fail**, 3 lỗi, 15 skip,
  3.469,3 s (~58 phút). 3 lỗi: (1) `test_round_trip_pb3` timeout —
  lỗi đã biết ở baseline; (2) `test_t2_polybridge3` (test P0 MỚI)
  timeout 180 s; (3) `test_t3_explodingkittens2` (test P0 MỚI)
  timeout 180 s. So fail-set với baseline main v76 (2.958 test,
  0 fail, 1 lỗi đã biết, 17 skip): **0 fail, 0 red mới ở test cũ**;
  2 lỗi thêm chỉ là 2 test P0 mới vượt mốc 180 s/test của full
  suite — chạy lẻ ở module P0 thì T2/T3 đều đạt (T2 áp thật 198,4 s,
  T3 áp thật 393,0 s). Skip 17 → 15 vì carrion + fortune-mill đã
  link (chỉ còn DST non-Unity thiếu IPA).
- **Timeout T2/T3 tăng lên 900 s/test (23/11, theo quyết của Chí —
  Room 17 message 12662):** harness bản này đặt `timeout_s = 1800`
  cho cả lớp game thật (bao phủ quyết 900 s; theo tiền lệ F6
  `test_po_write_dst_real.timeout_s = 1800`, commit 40a8e85). Chỉ
  đổi mốc riêng test P0, không đổi mốc chung 180 s của suite. Lý do:
  cả 2 chạy lẻ đều đạt — chậm chứ không sai; tối ưu tốc độ áp thật
  tách riêng thành task sau, không chặn P0. T7 chạy lại sau khi tăng
  timeout đang chạy trên máy Dũng (lần 5 attempt 2, ETA ~09:30 +07
  24/11) — cập nhật số sau khi xong.

## 7. Đính chính doc lỗi thời (Bước 0 §3) — P0 sửa / còn lại

P0 sửa (liên quan trực tiếp phép đo writeback):
- `README.md` mục (E): "Ép lại (E) — giới hạn v1" nói key
  MonoBehaviour/StringTable "không ép được" — **sai từ M1**: (E) ghi
  được I2/ST/generic/CSV (đo thật §4). Đã sửa wording + ghi rõ giới
  hạn còn lại (generic mơ hồ, `text_data_list`, PATCH_INPLACE).
- `HANDOVER.md` §1: "Ép lại bản dịch vào file gốc (E, bước 5 — chỉ ghi
  TextAsset JSON dict…)" — **sai**: đã sửa thành ghi được TextAsset
  JSON dict + CSV + MonoBehaviour/StringTable.
- `index.html` mục (E): "bảng ngôn ngữ CÓ type tree vẫn chưa ghi
  được" — **sai**: đường `language_source_tt` đã ghi được (test
  `test_mb_write_rest.py` + đo T1/T3). Đã sửa.
- `pb3/sdk/il2cpp.py:39` docstring "Chỉ đọc, không sửa." — **sai**:
  il2cpp đã ghi (patch/relayout từ F1). Đã sửa thành "Đọc + ghi
  (patch/relayout từ F1)".
- `pb3/docs/SUPPORT_MATRIX.md`: sửa câu "Patch Unity chỉ mới test
  fixture" (lỗi thời — nay đã patch trên 3 game thật, §4) + thêm hàng
  writer MonoBehaviour (I2 / I2-tt / ST / generic / CSV) với số game
  đã đo.
- Câu chữ "chỉ thiếu phép đo CSV thật" (plan §2.3 mục 3, theo ghi chú
  Minh phần 1): **đã có đo nhỏ** (1 entry/3 change, đọc lại 3/3 đúng,
  2345/2345 ô giữ nguyên, gate PASS); P0 bổ sung **đo toàn bảng**
  (T2: 2348 key CSV trên file thật PB3 + gate toàn IPA).

KHÔNG sửa ở P0 (thuộc P1–P5, ghi nhận để task sau làm):
- README SDF/font (§3): wording tuyệt đối về SDF vs regen từng phần —
  thuộc P3.
- README "Mod"/web-iSH table (M2 repack qualifier), README gate v2 —
  thuộc P1/P4.
- `exp/analysis_rebuild_results.md:134,136` — file lịch sử trong
  `exp/`, không sửa (luật HANDOVER §10); đính chính ghi ở đây.

## 8. Task tiếp theo (đề xuất)

1. **P1 — Full IPA export + test trên máy thật:** đóng gói lại IPA đã
   patch (M2 `repack_ipa`) cho 1 game (9K hoặc PB3), Duy tự ký + cài,
   kiểm tra chuỗi `"[VI] "` hiện đúng chỗ (mục §6a–d).
2. **P4 — Addressables + integrity:** khảo sát catalog CRC/hash có từ
   chối bundle đã patch không (R1) — quyết định xem bản patch có chơi
   được thật không.
3. **Follow-up nhỏ:** fallback locale cho nhánh StringTable +
   `_collect_mb_info` ST (cùng họ lỗi §5, chưa đo) + đo PATCH_INPLACE
   quy mô game (bản dịch ngắn hơn/bằng gốc).
