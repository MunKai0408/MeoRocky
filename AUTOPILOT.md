# AUTOPILOT.md — Sổ trạng thái BearTea/PB3 (chế độ tự động)

> ĐẦU MỖI LƯỢT / MỖI CUỘC TRÒ CHUYỆN MỚI: ĐỌC FILE NÀY TRƯỚC, rồi mới đọc phần còn lại.
> Không làm lại việc đã xong. Mỗi kết luận trong file này gắn nhãn [ĐÃ ĐO THẬT] /
> [FIXTURE] / [HYPOTHESIS]. Cập nhật file này SAU MỖI mục backlog.

## Mốc xuất phát [ĐÃ ĐO THẬT]

- Head xuất phát: `3d0c80c66ab4fbfbb329955f9cf241e491146f06` (main, 2026-11-16T20:01:31Z).
  Cây làm việc là bản giải nén zip, KHÔNG có `.git`; bản pristine đối chiếu nằm ngoài repo.
- Mục 2 (15 phút đầu) XONG 2026-11-17 ~08:55, chỉ kiểm tra, không sửa code/push:
  smoke 59/59; full 1719 = 1586 đạt / 102 lỗi / 27 fail / 4 bỏ qua (95,4s);
  harness 34/34 exit 1 (đường dẫn hardcode `pb3_web/site` + thiếu Playwright).
- Rà soát độc lập XONG 2026-11-17 ~09:11 (chỉ đọc, bản copy sạch): số Mục 2 đúng 40/43,
  0 sai; 25 mâu thuẫn M1–M25 (23 đúng, 1 bác bỏ M22, 1 chưa rõ M20); 15 mâu thuẫn mới
  N1–N15; 3 vi phạm thật (ngưỡng gzip, 2 lỗ innerHTML, precache 13 wheel chết).
- Q1/Q2 mặc định (user không trả lời trực tiếp, đã ghi rõ để sửa nếu sai): Q1(a) sinh
  fixture bằng script trong cây; Q2(a) sửa đường dẫn harness về cây repo gốc, chỉ sửa
  đường dẫn. Q1: full 1682 đạt, 34 đỏ do 2 fixture thiếu. Q2: 37 harness sửa đường dẫn,
  `node --check` 38/38, 2 harness mẫu PASS (item2 17/17, extract_select 8/8).

## Mục đang làm

- **P0-4** — tiếp theo, theo brief mục 7 (xem "Chờ Joseph" #5: cần ảnh Q1/Q2/Q3
  bản v4 + xác nhận bản đang cài; nếu chưa có thì ghi "Chờ Joseph" và bỏ qua
  sang P1-1).

## Đã xong

### P0-3 (trích xuất cấu trúc bảng ngôn ngữ + StringTable, lọc rác, conflicts) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (chưa đẩy; coordinator đẩy nhánh `auto/p0-3`)

- Parser cấu trúc bảng ngôn ngữ của game (`pb3/sdk/unity.py`, kind mới
  `"language_source"`): đọc `mLanguages`/`mTerms`, ánh xạ theo chỉ số, giữ
  chuỗi rỗng, kiểm chứng layout rồi mới nhận (fail-closed); đường field-shape
  cho build có typetree là [HYPOTHESIS] (chỉ dict tổng hợp). Định tuyến riêng
  trong pipeline (`pb3/sdk/extraction.py`): file ảo
  `*.langtable.monobehaviour.<mã gốc>.json`, key tên term thật; biến thể gộp về
  mã gốc. Gộp StringTable theo nhóm (`join_string_tables_grouped`, hàm cũ giữ
  nguyên, đã kiểm vi sai byte-identical). Lọc rác Unity ở đường generic (theo
  mẫu chung, có cơ sở đo). `_export` ghi `conflicts.json` khi gộp có xung đột.
  `rebuild.py` cộng tên term vào `mb_terms`. 35 test mới, không file pb3 mới,
  không đổi precache (files.json 95, tests.json 166, CACHE v21).
- Xuất locales [ĐÃ ĐO THẬT] (script ngoài repo, thư mục giao
  `deliver_p03_locales/`): 9 Kings v1.0.6 IPA 2.252 key/file (I2 2.150 =
  mTerms + ST 102), thiếu en 2 / zh-CN 2.218 / zh-TW 106, xung đột 0, rác
  1.006, tự kiểm tra 11/11 PASS (4 giá trị mẫu khớp, rò rỉ cột 0/2.150,
  placeholder/thẻ 0 lệch); đối chiếu file mẫu Build 68: 2.086 key/file, thiếu
  93/38, 14/14 PASS, byte-identical với kết quả mẫu của parent; PB3 v1.1.7
  2.348 key/file, 10/10 PASS; EK2 v0.0.18 1.344 key/file, 10/10 PASS;
  DungeonWithin CHƯA ĐẠT có lý do (0 bảng, thoát 2, không ghi file, không giải
  mã). Cùng parser, không hardcode, đúng trên 4 build (số term/cột/thứ tự cột/
  đuôi khác nhau).
- Hồi quy §4 ĐỔI CÓ LÝ DO (xem bảng mốc bên dưới): key `.sNNNN` thành tên term
  thật; EK2 mất file zh là đúng (7 key cũ là tiếng Nhật gán nhầm). Lỗi va chạm
  tên file ảo (dict ghi đè mất bảng) đã gặp khi kiểm chứng, đã sửa bằng đoạn
  `.langtable` + test khóa. Đường quét phẳng chỉ còn cho object không phải
  bảng ngôn ngữ, ghi rõ lý do trong code.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full 1.846 chạy = 1.816 đạt /
  25 lỗi / 3 fail / 2 bỏ qua (103,2s), tập đỏ trùng hệt mốc trước sửa (2
  fixture thiếu đã biết), không đỏ mới; harness rebuild 13/13 PASS, harness
  cổng 13/13 PASS (Chromium thật); harness gộp §1 đạt, §2–§4 bị chặn từ trước
  (fixture vắng + đường dẫn cũ đã chết), §5: c2 13/13 + item8 đạt, 5 harness
  con FAIL do fixture/đường dẫn vắng từ trước. Chi tiết:
  `exp/p03_language_source_results.md`.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: file 9 Kings v1.0.5 chưa có (mọi số 9 Kings là
  v1.0.6 — xem "Chờ Joseph" #6); EK2 100 bundle mã hóa ngoài phạm vi; đường
  field-shape [HYPOTHESIS]; Safari/iSH [CHƯA KIỂM CHỨNG] (không đổi UI nên
  không có bước thử mới); job CI `unit` exit 1 (nguyên nhân chưa rõ, như cũ).

### P0-2 (cổng kiểm tra trước khi giao, bước 6) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `982174fc`, PR #2, gộp vào main `d93b79a3`)

- Cổng duy nhất: bridge op `delivery_gate` + module mới `pb3/sdk/gate.py`
  (stdlib-only, Python 3.9, chỉ đọc, không ghi file) trả 1 verdict
  PASS/FAIL + 5 kiểm tra (bản dịch đầy đủ, placeholder/thẻ/xuống dòng,
  diffverify theo đúng đường (E) + provenance, độ phủ font bộ 142 mục
  theo từng font, toàn vẹn hash/cấu trúc theo kế hoạch ép) + lỗi dễ hiểu.
  Không verdict thì không giao file. UI: mục cổng trong tab Mod, nút
  "Ép và ghi file" chỉ bật khi cổng ĐẠT cho đúng file + bản dịch + lựa
  chọn hiện tại (khóa theo hash đầu vào); `rebuild_apply` bắt verdict
  PASS khớp sha nguồn + sha bản dịch khi được truyền verdict.
- Quy tắc chặn: FAIL chặn giao tuyệt đối, không có đường giao khi cảnh
  báo — vì file giao sai (thiếu key, mất placeholder, thiếu glyph) hỏng
  game theo cách user khó phát hiện. Áp nhất quán ở UI + bridge.
- Phạm vi + giới hạn có ghi rõ: cổng chỉ áp flow (E); gọi bridge trực
  tiếp không verdict giữ hành vi cũ (UI là đường giao duy nhất, luôn
  truyền verdict). Kiểm pre-write bằng mô phỏng RAM; bundle độc lập
  ≥64MB kiểm file đã ép thật ở chế độ pressed. File đã ép chỉ cần khớp
  nội dung + sha script, không cần byte-identical (khác bytes thì cảnh
  báo). Font nhúng Unity không đọc được bytes → "chưa kiểm được", không
  mặc định tốt/xấu. Placeholder so theo multiset có thứ tự + số lượng
  newline (chặt hơn `placeholders_ok` cũ).
- Đếm file: `files.json` 94→95 (+`pb3/sdk/gate.py`); `tests.json` 165→166
  (+`pb3/tests/test_gate.py`, 52 test); `sw.js` CACHE_VERSION
  v20-20261117→v21-20261117, precache 119→120 (0 mục chết, tổng thật
  16.185.827 B = 15,44 MiB); kỳ vọng đếm/version ở MỌI harness cũ đã
  đồng bộ; driver D1 của harness P0-1 nay chạy cổng (9 key đủ, gồm
  `title`) giữa preview và apply.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover)
  1811 chạy = 1781 đạt / 25 lỗi / 3 fail / 2 bỏ qua (79,6s), tập 28 đỏ
  giống hệt baseline (2 fixture thiếu đã biết), không đỏ mới; harness
  mới `verify_delivery_gate.mjs` 13/13 PASS trên Chromium thật
  (Playwright scratch, C1–C5 + D1–D4 xanh); harness P0-1 chạy lại 13/13
  PASS; item6 giữ 2 fail gzip từ trước, item7 giữ 1 fail fixture từ
  trước. Hồi quy §4 khớp mốc brief (xem bên dưới).
- Game thật [ĐÃ ĐO THẬT]: cổng FAIL đúng trên cả 3 IPA (9K: 13.465 key
  chưa ép, 13.464 MB + 1 non-string; PB3: 14.556 key, 14.517 MB + 39
  not_found + 1 đơn vị CSV bị từ chối; EK2: 17.240 key MB) — (E) v1 vẫn
  phủ 0% key ngôn ngữ thật; font FAIL ở 9K/PB3 (0 font đọc được), PASS
  ở EK2 (Roboto-Bold 139/142 phủ đủ chữ đang dùng). Vòng tròn khép trên
  `resources.assets` 9K (3 key → 5 changes, PASS cả pre-write lẫn
  pressed, sha bản copy không đổi). DungeonWithin probe-only: FAIL
  (key không tìm thấy + font chưa kiểm được), không giải mã.
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM + flow Safari thật [CHƯA KIỂM CHỨNG]
  (bước thử trong `exp/delivery_gate_results.md` §6); thử iSH chưa làm;
  đường ghi MonoBehaviour (G1, để mục sau); job CI `unit` exit 1 (nguyên
  nhân chưa rõ, như P0-1).

### P0-1 (E) — XONG 2026-11-17 [ĐÃ ĐO THẬT] (commit `41e98028`, PR #1, gộp vào main `9fb4a2ed`)

- (E) triển khai đủ: bridge op duy nhất `rebuild_apply`, dry-run mặc định + confirm gate,
  nhóm theo provenance, xung đột đúng schema merge (cap 500), plan-per-unit qua executor
  hiện có, khóa REBUILD_ALL giữ nguyên (`MATURITY="M2"`), output = đơn vị nhỏ nhất/ZIP,
  không đóng gói lại IPA, streaming G7 (none/LZ4/LZMA/Zstd→none, verify, progress đơn điệu),
  UI (E) đầy đủ + thông báo theo mã lỗi thật.
- G1 — PHẠM VI THU HẸP CÓ GHI RÕ: (E) v1 chỉ ghi TextAsset JSON dict. Đo thật: cả 3 game
  có 0 key TextAsset JSON dict; 100% key ngôn ngữ là MonoBehaviour (PB3 thêm CSV) → (E) v1
  phủ 0% key ngôn ngữ thật, mọi key MB vào unmatched `monobehaviour` có đếm rõ. Vòng tròn
  chỉ khép cho TextAsset (chân B: `resources.assets` 25.558.592 B, 3 key → 5 changes,
  dict khớp 100% + thứ tự key). Không tuyên bố đủ tiêu chí vòng tròn cho nhóm MB.
- G7 streaming: đạt trên fixture; bundle thật ≥64MB độc lập chưa có để thử.
- Sửa lỗi kèm theo (đợt sửa-lỗi + D2): vá 2 lỗ innerHTML (4/4 `f.source` qua `esc()`);
  precache 131→119 (xóa 13 wheel chết, +1 `rebuild.py`, CACHE `v20-20261117`);
  đính chính gzip 102400B; 3 chốt chặn dest==source (containers/compress/patch_pack);
  sửa lỗi thay-theo-giá-trị-trần ở plan.py + patch_pack.py; bundle mã hóa hiện refused
  `E_ENCRYPTED`; tài liệu M1–M25/N1–N15 phần lớn đã sửa (chi tiết trong HANDOVER §10);
  lỗi D2 (reselect xóa state) đã sửa, driver D3 hoàn thiện theo tiền lệ c4/reselect.
- Tự kiểm tra §E: smoke 59/59 [ĐÃ ĐO THẬT]; full (unittest discover) 1759 chạy =
  1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua (77,1s), 28 đỏ đều do 2 fixture thiếu đã biết
  (`zip/sample.ipa`, `sqlite_loc/a.sqlite`); harness mới `verify_analysis_rebuild.mjs`
  13/13 PASS trên Chromium thật (Playwright scratch, D1–D4 xanh); harness liên quan khác
  giữ nguyên trạng thái đỏ-từ-trước đã ghi (item6 2 fail gzip, item7 1 fail fixture,
  item8 1 fail import, c9 thiếu bigtest.zip). Hồi quy §4 khớp mốc brief (xem bên dưới).
- CHƯA ĐẠT / CHƯA KIỂM CHỨNG: RAM Safari thật [CHƯA KIỂM CHỨNG] (không có Safari thật);
  vòng tròn trên data.unity3d 590MB (file không được cung cấp); đường ghi MonoBehaviour
  (G1, để mục sau); job CI `unit` exit 1 (tái hiện cục bộ xanh, nguyên nhân chưa rõ);
  M20/M23/N5-"1692" còn mở (xem HANDOVER §10).

## Bị chặn / Chờ Joseph (ghi + bỏ qua, không đứng chờ)

1. Tải asset `pb3-fixtures-v1.zip` lên Release `fixtures-v1` (id 446604484, hiện
   `assets: []`) — nguyên nhân job CI `assets` exit 1 đã xác định [ĐÃ ĐO THẬT].
   Không sửa code/workflow nào khắc phục được; tải qua giao diện web GitHub.
2. Thử Safari thật + đo RAM (bước thử trong `exp/analysis_rebuild_results.md` §6
   cho (E) và `exp/delivery_gate_results.md` §6 cho cổng);
   chạy iSH (patch02 suite + extract + font scan) khi tới P2-4.
3. File `data.unity3d` 590MB (brief §4) chưa được cung cấp — cần để đủ tiêu chí vòng tròn
   P0-1 trên file lớn; đã thay bằng bundle lớn nhất có thật + ghi rõ.
4. Cloudflare translate-proxy (cần tài khoản + Google API key, trần 500.000 ký tự/tháng),
   PAT mới — khi tới các mục cần chúng.
5. P0-4 (Space Marshals 2 mất chữ): cần ảnh chụp màn hình Q1/Q2/Q3 bản v4 + xác nhận bản
   đang cài trên máy; tới lượt P0-4 sẽ ghi "Chờ Joseph" và bỏ qua sang P1-1 nếu chưa có.
6. File 9 Kings v1.0.5 để chạy lại đúng bản user nhắc tới — hiện chỉ có v1.0.6
   (173.472.485 B, md5 4f53043c…); mọi số P0-3 của 9 Kings là v1.0.6, không
   tuyên bố số v1.0.5.

## Mốc hồi quy mới nhất [ĐÃ ĐO THẬT] (CPython qua bridge.extract, en+zh, gộp 1 file)

Mốc mới sau P0-3 (2026-11-17) — số cũ trước P0-3 trong ngoặc:

| Game (build) | en / xung đột | zh / xung đột | #N | Gộp khớp tách |
|---|---|---|---|---|
| 9 Kings v1.0.6 (173.472.485 B) | 6.694 / 13 (trước: 13.465 / 17) | 2.176 / 0 (trước: 1.914 / 0) | 0 | 100% |
| Poly Bridge 3 v1.1.7 (482.039.645 B) | 11.065 / 126 (trước: 14.556 / 293) | 2.350 / 5 (trước: 2.570 / 224) | 0 | 100% |
| Exploding Kittens 2 v0.0.18 (290.547.090 B) | 10.468 / 2 (trước: 17.240 / 2) | không còn file (trước: 7 / 0) | 0 | 100% |

Lý do đổi (đã đo bằng diff trực tiếp, xem `exp/p03_language_source_results.md`
§4): key `.sNNNN`/`.pNNNN` được thay bằng tên term thật từ bảng ngôn ngữ có cấu
trúc (mọi term có mặt đầy đủ: 2.150/2.150, 2.348/2.348, 1.344/1.344); bộ lọc rác
loại giá trị Unity khỏi đường generic; trùng lặp giống hệt gộp lại nên xung đột
giảm. EK2 mất file zh là ĐÚNG: 7 key zh cũ là chuỗi tiếng Nhật `日本語` gán
nhầm, EK2 không có dữ liệu tiếng Trung. Mốc cũ (trước P0-3) khớp chính xác mốc
brief §4 → mốc đo trên đúng 3 build này. Phân loại key (trước P0-3): 9K
mb_generic 15.254 + mb_table 125; PB3 mb_generic 12.426 + CSV 4.700; EK2
mb_generic 17.247; TextAsset JSON dict = 0 ở cả 3. DungeonWithin (91.389.555 B):
chỉ quét/probe, 4 bundle Addressables mã hóa AES — KHÔNG giải mã.
Passpartout/Dead Cells/data.unity3d: chưa có file.
Chạy lại sau P0-2 (2026-11-17): khớp chính xác cả 3 game, merged==tách 100%, không lệch.

## Lệnh đã chạy (mốc + kiểm tra)

```bash
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 run_tests.py --tier smoke
cd pb3 && TMPDIR=~/workspace/beartea/tmpdir_p01 python3 -m unittest discover -s tests -t .
cd exp && node verify_analysis_rebuild.mjs   # D1-D4 cần Playwright (scratch ngoài repo)
cd exp && node verify_delivery_gate.mjs      # P0-2, D1-D4 cần Playwright (scratch ngoài repo)
diff -rq -x __pycache__ -x logs -x reports -x workspace -x .pytest_cache <pristine> <cây>
```

## Quyết định đã chọn (1 dòng lý do mỗi quyết định)

- G1 thu hẹp: chỉ ghi TextAsset JSON dict, MB vào unmatched có đếm — vì không có nền tảng
  ghi MB an toàn + đã kiểm chứng được; viết mới từ đầu rủi ro hỏng file game.
- Precache xóa 13 wheel chết thay vì bổ sung wheel — vì đĩa chỉ có 1 wheel, danh sách cũ
  theo cây staging cũ, không theo head.
- D3 hoàn thiện driver (đuôi `.unity3d` + bấm modal) thay vì sửa app — vì cổng đuôi/modal
  là hành vi đúng đã có tiền lệ ở harness c4/reselect; assertion D3 không đổi.
- Đẩy P0-1 lên nhánh `auto/p0-1` thay vì main — vì là thay đổi rủi ro cao, cần CI/harness
  trên nhánh trước khi gộp; gộp chỉ khi test xanh.
- Tài liệu theo code, không sửa test cho vừa tài liệu — trừ đồng bộ kỳ vọng đếm/version
  đã lệch từ trước theo giá trị thật của cây (ghi rõ từng chỗ).
- Cổng FAIL chặn giao tuyệt đối, không đường giao khi cảnh báo — vì file giao sai hỏng
  game theo cách user khó phát hiện; cảnh báo không bao giờ chặn nhưng luôn hiện.
- File đã ép chỉ cần khớp nội dung + sha script, không cần byte-identical với mô phỏng
  — vì file ép bằng công cụ khác vẫn hợp lệ; khác bytes thì cảnh báo rõ.
- Font nhúng Unity không đọc được bytes thì "chưa kiểm được", không mặc định tốt/xấu
  — vì reader hiện tại không trích được bytes font; kết luận chắc chắn cần font chỉ định.
- Parser bảng ngôn ngữ fail-closed, không fallback âm thầm về quét phẳng — vì
  đoán sai layout gán nhầm ngôn ngữ cho hàng nghìn term mà không ai phát hiện.
- Pipeline gộp biến thể về mã gốc, xuất locales giữ đủ biến thể — vì bảng locale
  hiện có gộp biến thể và mốc §4 cần so được; độ trung thực biến thể nằm ở
  artifact locales.
- Đường quét phẳng chỉ còn cho object không phải bảng ngôn ngữ, ghi rõ lý do —
  vì xóa hẳn mất hàng nghìn chuỗi thật không có nguồn thay thế + phá test hiện có.
- File ảo bảng ngôn ngữ mang đoạn `.langtable` — vì tên cũ đụng file generic
  cùng đơn vị, dict ảo ghi đè làm mất bảng (lỗi thật, đã có test khóa).

## Đẩy code

- P0-1 + đợt sửa-lỗi: nhánh `auto/p0-1`, commit snapshot `41e98028d3c5daf8cbb49152c9d6405a93ef3872`
  (70 file text, từ head `3d0c80c`, sync-base đã kiểm khớp), PR #1, ĐÃ GỘP vào main
  (`9fb4a2edf004b10d97d2fc95d3fc08df6fec9a13`) 2026-11-17. CI trên main: job `assets` vẫn
  đỏ-từ-trước do Release `fixtures-v1` rỗng (xem "Chờ Joseph" #1); job `unit` tái hiện
  cục bộ xanh, nguyên nhân đỏ trên CI [CHƯA KIỂM CHỨNG].
- Gói nhị phân chưa lên repo: `handoff_p01.zip` (18 fixture/font) — Joseph đẩy theo
  `HUONG_DAN.txt` (iSH hoặc web). Nhánh `auto/p0-1` giữ nguyên, không xóa.
- P0-2: nhánh `auto/p0-2`, commit snapshot `982174fc9c7b8bc0ef2f7bfc86a4a52e44a74c74`
  (39 file text, từ head `37a1133f`, sync-base đã kiểm khớp), PR #2, ĐÃ GỘP vào main
  (`d93b79a3272d98b1580d88a7d5ff7dc83c45f17a`) 2026-11-17. Nhánh `auto/p0-2` giữ nguyên,
  không xóa. Không có gói nhị phân cho P0-2.

## Backlog còn lại (thứ tự)

P0-4 (xem "Chờ Joseph" #5) → P1-1 → P1-2 → P1-3 (I1) → P1-4 (I2) →
P1-5 (I3) → P2-1 → P2-2 → P2-3 → P2-4 → P3. Tiêu chí XONG từng mục theo brief mục 7.
