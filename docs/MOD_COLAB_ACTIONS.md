# Mod qua Colab / GitHub Actions — cổng kiểm tra + ép + repack trên máy chủ

Tab Mod trên điện thoại (Safari ~1 GB RAM) sập khi bấm "Kiểm tra trước khi
giao" hoặc "Ép và ghi file" với IPA lớn (vd 9K 165 MB). Bản v51 chỉ chống
sập (từ chối lịch sự khi quá nặng) chứ không chạy được cổng full IPA trên
điện thoại. Đường này chuyển 3 bước nặng (cổng → ép → repack) lên máy chủ,
kết quả **quay trở lại được web** (tiêu chí nghiệm thu số 1).

## Hai đường — thứ tự ưu tiên

| | **Actions (đường chính)** | **Colab (đường dự phòng)** |
|---|---|---|
| Chạy ở đâu | Trong web, không chuyển tab | Mở tab Colab, bấm 2 lần (cho phép Drive, Run All Ctrl+F9) |
| Nhập | Tên game + 3 link Drive + token GitHub | Tên game + 3 link Drive (job JSON) |
| Chạy | Bấm "Ép trên máy chủ" → "Kiểm tra trạng thái" → tải kết quả | Bấm "Mở Colab" → Run All → "Kiểm tra kết quả" |
| Trả về | Artifact (xem giới hạn bên dưới) | Drive `MeoRocky-output/<tên game>-mod/` |
| Khi nào dùng | Mặc định, mọi trường hợp vừa ngân sách | Chỉ khi IPA đã repack >400 MB (Actions không trả được) |

Cả 2 đường chạy đúng 1 code (`sdk.mod_job.run_mod_job`): cổng pre-write →
ép thật → repack (nếu bật) → kiểm lại file đã ép (pressed). Cổng CHƯA ĐẠT
thì **dừng lại, không ép** — đó là kết quả đúng, xem `gate-report.json`.

## Luồng dùng (Actions, trong web)

1. Chuẩn bị 3 file trên Google Drive, chế độ **"ai có link cũng xem được"**:
   file IPA gốc, file JSON gốc (`en.json` đã trích), file JSON bản dịch
   (`translated.vi.json`).
2. **IPA local?** Điện thoại nhận file local trực tiếp ở các bước trên web
   như cũ — nhưng máy chủ không nhìn thấy file trên điện thoại. Dùng mục
   **"Tải file lên Drive từ web"** trong panel Colab (tab Mod): chọn file
   trên máy → tải lên Drive (OAuth 1 lần, upload resumable 8 MB/chunk,
   tắt tab vẫn tiếp tục được) → link Drive tự điền vào các ô. Áp dụng cho
   cả 3 file (IPA, JSON gốc, JSON bản dịch). Xong thì chạy job tiếp mà
   không rời khỏi web.
3. Tab Mod → mục **"Ép trên máy chủ (GitHub Actions)"**: nhập tên game +
   3 link Drive, chọn ngôn ngữ đích / chế độ ép / font kiểm, nhập token
   GitHub (quyền Actions Read and write — tạo riêng, xem
   `docs/ACTIONS_EXTRACT.md`; **không dán token cobonla vào web**, token
   chỉ lưu trong trình duyệt này).
4. Bấm **"Ép trên máy chủ"** → bấm **"Kiểm tra trạng thái"** để hỏi tiến
   độ (hỏi lại mỗi 15 giây; đóng tab rồi mở lại vẫn xem được).
5. Run xong: tải các artifact về (mỗi file là 1 `.zip`):
   `gate-report` (báo cáo cổng), `pressed-gate-report` (báo cáo kiểm lại
   file đã ép), `summary` (tóm tắt + file nào không lên được artifact),
   `applied` (các đơn vị đã ép, dạng zip), `repacked-ipa` (IPA đã ép,
   nếu có — xem giới hạn).
6. Nhập báo cáo cổng về tab Mod: tải `gate-report.zip` về, giải nén, bấm
   **"Nhập báo cáo cổng…"** rồi chọn `gate-report.json` — verdict hiện ở
   **Bước 3** bên trên, đúng như cổng chạy local. (Verdict này là của
   file + bản dịch trên máy chủ nên **không** mở khóa nút "Ép và ghi
   file" trên điện thoại — đúng thiết kế: nút đó chỉ mở khi cổng local
   ĐẠT cho đúng file đang mở.)

## Luồng dùng (Colab, dự phòng)

1. Tab Mod → mục **"Ép trên Google Colab"**: nhập tên game + 3 link Drive
   (hoặc tải file local lên Drive như mục trên), chọn chế độ ép / font.
2. Bấm **"Sao chép job"** (hoặc "Tải job.json") rồi **"Mở Colab"**.
   Trong tab Colab: bấm cho phép Drive, rồi Run All (Ctrl+F9). Notebook tự
   bỏ qua Bước 2 + Bước 3, chỉ chạy **Bước 4 Mod**.
3. Bấm **"Kiểm tra kết quả"** (web tự hỏi mỗi phút): xong thì web tải
   `gate-report.json` về và hiện verdict ở **Bước 3**; file lớn
   (`applied.zip`, IPA `-mod.ipa`) hiện **link tải Drive** chứ không nạp
   blob hàng trăm MB vào RAM Safari.
4. Muốn nhập tay: bấm **"Nhập kết quả…"** rồi chọn `gate-report.json`
   đã tải về.

## Đường trả về (từng route)

- **Actions:** artifact của run (lưu 30 ngày). `gate-report.json` +
  `summary.json` **luôn** được trả. `applied.zip` + IPA đã repack chỉ
  trả khi vừa ngân sách (xem dưới). Nếu run xong mà không thấy IPA đã
  repack, web hiện rõ 2 khả năng (cổng CHƯA ĐẠT, hoặc IPA vượt 400 MB)
  và chỉ cách xem `summary.json` để biết chính xác + đường Colab dự
  phòng — không im lặng.
- **Colab:** mọi file lưu về `MeoRocky-output/<tên game>-mod/` trên Drive
  + `handoff-manifest.json` (web poll theo `job_id`). Không giới hạn
  500 MB.

## Giới hạn thật (đã đo)

- **Artifact GitHub miễn phí chỉ 500 MB.** Ngân sách trong code là
  **400 MB** (`sdk.mod_job.ARTIFACT_BUDGET_BYTES`, chừa biên cho nén zip
  + artifact khác): `gate-report.json` + `summary.json` luôn đi kèm;
  `applied.zip` rồi đến IPA đã repack — file nào vượt phần còn lại thì
  bị loại, ghi rõ tên + dung lượng + lý do trong
  `summary["artifact_plan"]`. **Không có cách hợp lệ nào trả IPA repack
  >500 MB qua artifact** — cũng không dùng GitHub Release (sẽ phát tán
  IPA game có bản quyền công khai). Đường Colab (Drive) là cách duy nhất
  cho output lớn.
  - 9K (IPA 165 MB) → repack ~165 MB: **trả được qua Actions**.
  - PB3 (IPA ~482 MB) → repack ~482 MB: **không trả được qua Actions**,
    chỉ qua Colab.
  - DST 1,69 GB: không trả được qua Actions; ngoài ra DST không phải
    Unity nên ngoài phạm vi Mod job.
- **EK2 không ép được — bundle mã hóa.** EK2 v0.0.18: cổng FAIL vì
  100/100 đơn vị bị từ chối `E_ENCRYPTED` (bundle mã hóa, flag 0x243),
  0 đơn vị ép được, 1344 key unmatched (đo thật, cổng 46,5 giây).
  Runner dừng lại đúng thiết kế, vẫn trả `gate-report.json` + manifest
  về web. Muốn ép EK2 cần file IPA mà bundle đã giải mã. Trích xuất
  vẫn được (en.json 1344 key) — chỉ bước ép là không làm được trên
  file này. Đây là giới hạn của file game, không phải lỗi tool.
- **Font kiểm (check d):** mặc định dùng font Be Vietnam Pro đi kèm trong
  repo (`display_font="donor"`, giống chế độ "Font đi kèm" trên web).
  Đã đo thật: cổng 9K 2252 key ở chế độ quét (scan) **FAIL duy nhất check
  font** ("Không tìm thấy font nào trong file gốc", 0 font đọc được trong
  IPA; các check khác đều ĐẠT, 35,7 giây) — nên chế độ quét không dùng
  được cho 9K. Chọn "scan" trong panel khi game có font đọc được và muốn
  kiểm đúng font của game.
- **Drive quota tải công khai:** file bị tải quá nhiều gần đây thì Google
  chặn ("Too many users...") tối đa 24 giờ — bước tải fail với thông báo
  riêng, chỉ cần chờ rồi bấm chạy lại (không phải lỗi workflow).
- **Runner:** ubuntu-latest 16 GB RAM, 14 GB disk; timeout 90 phút.
  Trích xuất/ép streaming theo entry nên RAM đỉnh phụ thuộc entry lớn
  nhất, không phải tổng dung lượng IPA.
- **Chữ ký:** IPA đã repack giữ chữ ký cũ nên đã hết hiệu lực — cần ký
  lại bằng chứng chỉ của bạn trước khi cài (ghi trong `summary.json` +
  ghi chú artifact).
- **Cổng CHƯA ĐẠT không phải lỗi:** workflow vẫn xanh, trả
  `gate-report.json` — sửa bản dịch theo báo cáo rồi chạy lại.

## Thiết kế mở — engine game (Unreal / Native iOS sau này)

Bước đặc thù engine (cổng → ép → repack) nằm sau interface chung
`sdk.mod_job.ModEngine` (`run_gate` / `apply` / `repack`) với registry
`mod_job.ENGINES` (hiện chỉ có `"unity"` → `UnityModEngine`, ủy thác
cho `sdk.gate` / `sdk.rebuild` / `sdk.repack`, không viết lại logic).
Job mang trường `engine` (mặc định `"unity"`, validate ở
`colab_job.JOB_ENGINES` — hai danh sách phải khớp nhau, có test khóa),
`summary.json` + `handoff-manifest.json` đều ghi `engine`, web hiện
verdict/file kết quả mà không cần biết engine nào. UI có ô chọn Engine
(Unity chọn được; Unreal Engine / Native iOS đang khóa, ghi "sắp có").
Engine lạ fail-closed (`E_CONFIG_INVALID`) **trước khi đọc file nào** —
ở cả schema job, runner, notebook, CLI Actions và web.

**Hiện tại chỉ làm Unity — không làm Unreal/iOS ngay.** Muốn gắn engine
mới sau này: (1) hiện thực `ModEngine` (3 phương thức, cùng chữ ký +
ý nghĩa trả về như SDK Unity); (2) đăng ký vào `mod_job.ENGINES`;
(3) thêm tên vào `colab_job.JOB_ENGINES`; (4) mở ô chọn trên UI +
thêm input workflow; (5) thêm test (schema, runner, round-trip trên
game thật của engine đó). Không cần sửa runner, manifest, artifact
plan hay luồng nhập kết quả — chúng không gắn Unity.

## File liên quan

- `pb3/sdk/mod_job.py` — runner (`run_mod_job`, ngân sách artifact,
  interface engine `ModEngine`/`ENGINES`/`get_engine`,
  `summary["timings"]` từng bước, không ghi thừa zip).
- `pb3/sdk/colab_job.py` — schema Mod job (`kind="mod"`, `build_mod_job`,
  trường `engine`, `JOB_ENGINES`)
  + manifest Mod (`build_mod_manifest`, `write_mod_manifest`).
- `.github/scripts/mod_large.py` (`--engine`) +
  `.github/workflows/mod-large.yml` (input `engine`) — đường Actions.
- `colab/extract-large-ipa.ipynb` — Bước 4 Mod (cell job nhận Mod job,
  Bước 2/3 tự bỏ qua với Mod job; cell Mod truyền `engine`).
- `colab_handoff.js` (`buildModJob`, panel `mod-colab-*`) + `app.js`
  (`modServer*`, hook `window.MeoRockyMod.showGateReport`) + `index.html`
  (2 panel `#mod-server-section`, `#mod-colab-section`, ô chọn Engine).
- Test: `pb3/tests/test_mod_job.py` (schema, manifest, ngân sách,
  runner trên fixture, round-trip 9K/PB3 + EK2 fail-closed, engine,
  timings, script + workflow + cell notebook + JS/UI).

## Thời gian ép (đo thật trên máy VM 1,5 CPU — xem báo cáo PR)

- `summary["timings"]` ghi số giây từng bước: `gate_seconds`,
  `apply_seconds`, `repack_seconds`, `pressed_gate_seconds`,
  `total_seconds` (+ `peak_ram_mb`).
- 9K (IPA 165 MB, 2252 key, bản dịch [FIXTURE] "[VI] " + giá trị gốc,
  đo 2 lần liên tiếp trên cùng máy):
  | | REBUILD_SECTION | PATCH_INPLACE |
  |---|---|---|
  | cổng pre-write | 61,0 s | 54,9 s |
  | ép | 26,0 s | 23,2 s |
  | repack | 84,9 s | 62,5 s |
  | kiểm lại pressed | 55,7 s | 44,2 s |
  | **tổng** | **230,4 s** | **187,6 s** |
  | RAM đỉnh | 692,9 MB | 692,9 MB |
  | IPA repack | 163.386.981 B | 163.386.981 B |
  | verdict / changed | PASS / 2252 | PASS / 2252 |
  | bytes đã ép | md5 giống hệt nhau ở cả 2 đơn vị |
  Đọc đúng số liệu: 2 mode cho **output giống hệt nhau** (cùng verdict,
  cùng số changed, cùng md5 file đã ép) — an toàn như nhau. Nhanh hơn
  tổng 42,8 giây chủ yếu ở các bước repack/kiểm lại, là các bước
  **không phụ thuộc chế độ ép** (output giống hệt nhau) nên chênh lệch
  đó là nhiễu tải máy (các agent khác đang chạy suite cùng lúc), không
  phải do PATCH_INPLACE. Riêng bước ép nhanh hơn 2,8 giây (~11%).
  Lý do PATCH không nhanh hơn nhiều ở đây: bản dịch [FIXTURE] dài hơn
  gốc ("[VI] " + gốc) nên entry dài leo thang về REBUILD_SECTION theo
  SDK — với bản dịch thật (ngắn/ngang gốc) PATCH có thể nhanh hơn,
  nhưng đó là [HYPOTHESIS] cho tới khi đo bằng bản dịch thật. Vì vậy
  mặc định Mod job vẫn REBUILD_SECTION (đường chung an toàn nhất);
  người dùng muốn thử đường nhanh thì chọn PATCH_INPLACE trên panel.
  Cổng + repack + kiểm lại chiếm ~202/230 giây — tăng tốc các bước đó
  thuộc hướng tốc độ chung (speed coordinator), không phải Mod job.
- PB3 (IPA 482 MB, 2348 key): runner xong (cổng PASS, pressed PASS,
  repack 475 MB) — thời gian từng bước trong `summary["timings"]`
  của lần chạy verify (xem báo cáo PR).
- EK2 (IPA 290 MB): cổng 46,5 giây rồi FAIL (bundle mã hóa) — không
  ép, không repack.

## Verify (đo thật — xem báo cáo PR)

- Round-trip 9K/PB3: cổng khớp `run_gate` trực tiếp, bytes đã ép ==
  `apply_rebuild` trực tiếp, IPA repack đạt verify 6 mục, cổng pressed
  PASS, kết quả nhập lại được từ `out/` (gate-report + manifest).
- EK2: không round-trip được (bundle mã hóa) — test khóa hành vi
  fail-closed (cổng FAIL → dừng, vẫn trả báo cáo + manifest về web).
- Test file `pb3/tests/test_mod_job.py`: xem số liệu trong báo cáo PR
  (tổng số test, số pass/fail, thời gian chạy).
