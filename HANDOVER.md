# Bàn giao dự án PB3 / BearTea — HANDOVER

- Ngày bàn giao: 2026-11-16
- Chủ dự án: Joseph (timezone Asia/Ho_Chi_Minh, iPhone 11 Pro Max, dùng iSH)
- AI cũ: Muse — AI mới đọc file này trước, rồi làm theo mục 8.
- Ngôn ngữ trả lời Joseph: tiếng Việt, ngắn gọn (tin nhắn điện thoại).

## 1. Dự án là gì

PB3 v3.1 (tên hiển thị: **BearTea**) là công cụ xử lý/mod game Unity, viết bằng Python (stdlib only).

- **Bản iSH**: chạy trên iPhone của Joseph qua iSH, thư mục `/root/pb3`. Game thật: **9 Kings v1.0.5** (file IPA 141.865.757 byte). (Bản khác với file 9 Kings v1.0.6, 173.472.485 byte, dùng trong các báo cáo thử game thật trên web — hai bản build khác nhau, không dùng chung số đo khi chưa đối chiếu danh tính build.)
- **Bản web**: Python chạy trong trình duyệt bằng Pyodide (100% client-side), host trên GitHub Pages. Repo: **MunKai0408/MeoRocky** (public), trang live: https://munkai0408.github.io/MeoRocky/
- Chức năng: Danh sách entry (ZIP/IPA), Inspect Unity, Trích xuất, Dịch (Google Translate + offline), Mod (sửa text TextAsset), Ép lại bản dịch vào file gốc (E, bước 5 — chỉ ghi TextAsset JSON dict, xem `exp/analysis_rebuild_results.md`), Cổng kiểm tra trước khi giao (bước 6 — 1 verdict PASS/FAIL gộp 5 kiểm tra, FAIL chặn giao, xem `exp/delivery_gate_results.md`), Batch, Kho KB, Hỗ trợ, Nén IPA (tar.xz), Font tiếng Việt, Chẩn đoán. Menu iSH có [1]–[7]; web có 11 tab tương ứng.

## 2. Tài liệu và code — đọc trước theo thứ tự

1. File này (HANDOVER.md).
2. Repo web https://github.com/MunKai0408/MeoRocky — code web đầy đủ ở gốc repo (`index.html`, `app.js`, `worker.js`, `pb3/` = cây PB3 đã vendor, `pyodide/`). (Không có thư mục `site/` — đó là tên cây staging cũ.)
3. File zip iSH mới nhất: **`pb3_handoff_final_patch02_sandbox.zip`** (1.142.330 byte, md5 `d18a5f7b7caa65f143f56fc3d99cbf30`, 383 entry, suite **1562 test xanh** trong sandbox, 20 plugin [ok]) — đây là cây iSH ĐẦY ĐỦ (code + test + docs + handoff). **Cây iSH đầy đủ CHƯA có trên GitHub** (repo web chỉ có bản vendor trong `pb3/`); AI mới cần file zip này để làm tiếp bản iSH.
4. Trong cây iSH: `handoff/account_01.md` … `account_10.md`, `account_final.md`, `account_patch01.md`, `account_patch02.md`, `decisions.md` (quyết định #1–#208), `CURRENT_STATE.md`.
5. Trong repo web: `exp/*.md` (báo cáo từng đợt: `item2_results.md`, `item3_results.md`, `clone_fix_results.md`, `phase1_ci_results.md`, `analysis_rebuild_results.md`, `delivery_gate_results.md`, …), `README.md`. (Các file `upgrade_results.md`, `redesign_results.md`, `item1_results.md` không có trong cây — kết quả kiểm chứng redesign xem ở `exp/verify_redesign_results.md`.)

## 3. Trạng thái hiện tại (2026-11-16 ~12:36, đã kiểm chứng)

Đã đẩy lên `main` và live:

- `ac1f3e5` — Hotfix Safari đợt 2: **navigator.share làm đường lưu chính** (blob download kẹt trên Safari), bỏ revoke URL sau 5 giây, Inspect ZIP cho chọn bundle `.assets` bên trong, Giải nén trả **tên file gốc** (thay vì `restored.bin`), bảng không vỡ chữ, code proxy dịch mục 2 (chờ deploy).
- `d2e2f9b` — Mục 3: KB chuyển sang IndexedDB + autosave + `persist()` + PWA.
- `37193ac` — Redesign giao diện theo mockup Joseph gửi (hero gấu trà sữa, khung thả file, 6 thẻ tính năng, File gần đây, bottom nav, sáng/tối — chỉ đổi vỏ, giữ nguyên chức năng).
- `f00d098` — CI: pyodide-smoke bỏ qua fixture nhị phân vắng mặt thay vì crash ENOENT.
- `0646735` — CI: Python 3.12 (Ubuntu 26.04 bỏ Python 3.9).
- `ca3caef` — Hotfix bug clone Safari (gửi file sang worker 1 lần + `slice(0)`).

CI: job `unit` xanh lần đầu (run 43803496189: **1440 passed**, 126 deselected, 41,9 giây). Job `assets` chờ Release `fixtures-v1` — Release đã tạo (id 446604484); asset upload qua API GitHub bị 401 nên phải tải file asset lên qua giao diện web github.com.

Đính chính 2026-11-17 (giữ nguyên ảnh chụp run 43803496189 ở trên): đầu file workflow ghi 1436 test cho job `unit` (1436+126=1562, khớp tổng patch02) — con số 1440 chênh 4 test, chưa đối chiếu được, giữ nguyên cả hai. Release `fixtures-v1` tới 2026-11-17 vẫn chưa có asset nào (API GitHub trả `assets: []`) nên job `assets` vẫn đỏ ở bước tải fixture; cần tải `pb3-fixtures-v1.zip` lên Release. Job `unit` tại head 3d0c80c (run #35/#46) đỏ exit 1 nhưng nguyên nhân [CHƯA KIỂM CHỨNG] — lệnh pytest tái hiện cục bộ đều xanh, cần đọc log từng bước trên giao diện web (xem mục CI trong `README.md`).

Đang dở / đang chạy:

- Coordinator (agent chạy nền) đang làm mục 4–9 của kế hoạch gộp (xem mục 6): worker pool (mục 4, đang làm), streaming reader (5), SW precache (6), font atlas (7), plan JSON + REBUILD_ALL (8), wasm texture (9, làm cuối hoặc bỏ). AI mới phải kiểm tra lại trạng thái các mục này (đọc `exp/` + head `main`) trước khi làm tiếp, vì coordinator cũ có thể đã đẩy thêm commit sau thời điểm file này được viết.
- Mục 2 (proxy dịch Cloudflare Worker + KV cache + giới hạn cứng 500.000 ký tự/tháng): code + `wrangler.toml` + hướng dẫn đã viết sẵn, **CHƯA deploy — cần tài khoản Cloudflare + Google API key của Joseph. Không tự tạo tài khoản/key.**
- **RELEASE BLOCKED**: chưa có gì từ đợt tổng cuối (final pass / patch01 / patch02) được kiểm chứng xanh trên iSH. Zip patch02 đã giao cho Joseph nhưng anh ấy chưa chạy xong suite trên iSH.

Lỗi đã biết (đừng chẩn đoán lại từ đầu):

- Download blob (`a.click()`) trên Safari iPhone Joseph kẹt mãi — file dở dang nằm trong Files > Tải về nhưng không dùng được (bảo Joseph xóa đi). Đã sửa bằng nút **Lưu / Chia sẻ** (navigator.share → Lưu vào Tệp). File 280KB cũng kẹt nên không phải do dung lượng.
- Bảng Danh sách từng vỡ chữ từng ký tự (`word-break: break-all` áp cho mọi ô) — đã sửa (chỉ ngắt ở cột đường dẫn).
- Inspect Unity trên nguyên file IPA/ZIP → E_UNSUPPORTED_FORMAT (đã sửa: liệt kê bundle trong IPA cho chọn).
- Giải nén bấm nhầm khi đang chọn file `.zip` → E_CORRUPT (đã thêm kiểm tra đuôi `.tar.xz` + nhãn lỗi theo thao tác).
- Picker iOS làm mờ file `.ipa` (thuộc tính `accept` đã bỏ; cách lách: đổi tên bản copy IPA thành `.zip`).
- Tên entry ZIP bị mojibake tiền tố U+200E (byte E2 80 8E, iOS Files ghi UTF-8 không đặt cờ bit 11) — đã sửa phía web (`fix_zip_entry_name`), **cần port sang cây iSH ở patch03**.

## 4. Quy tắc làm việc (Joseph đã dặn — tuân thủ)

- **Joseph luôn phê duyệt: AI cứ làm thẳng, chỉ báo kết quả, không hỏi ý kiến.** (Thẻ approval của hệ thống vẫn hiện khi ghi GitHub — Joseph sẽ bấm duyệt.)
- Kiểm chứng độc lập: suite phải xanh + zip phải được kiểm chứng độc lập từ bản giải nén mới trước khi giao. Kết quả sandbox KHÔNG thay cho iSH. Một tài khoản chỉ tính xong khi Joseph kiểm chứng trên iSH.
- Code Python 3.9 tương thích (không `match`, không `tomllib`), stdlib only, không `import UnityPy`/`lz4` trực tiếp (chỉ qua importlib). Patch/mod mặc định dry-run; ghi cần `--allow-write`.
- Mọi text tiếng Việt phải có dấu đầy đủ. Mọi thao tác dài phải có progress bar thật (% + ETA từ thời gian thực, tăng từng 1%, không báo láo), đẹp kiểu PB3 (TTY-gated ANSI, vừa màn hình điện thoại 30–44 cột).
- Unity: đọc UnityFS **v6–v9** (v9 chỉ đọc), SerializedFile **v15–v23**; ghi bundle tối đa v8, không ghi v9. Zstd chỉ đọc trên web (qua fzstd, C5), không ghi Zstd. (Quy tắc cũ "tối đa v8, không có v9, không Zstd" chỉ còn đúng cho đường GHI và iSH — xem §10, M4.) **Không UnityPy trên web** (không cài được wheel etcpak) — inspect Unity trên web dùng parser thuần.
- Font: bộ kiểm tra đầy đủ là **142 mục (134 chữ cái + 8 dấu kết hợp)**. Font Be Vietnam Pro đi kèm đủ 134/134.
- Test suite chạy offline hoàn toàn (chỉ tính năng Google Translate mới cần mạng).

## 5. Môi trường iSH (đã kiểm chứng trên máy Joseph)

- iSH trên iOS: Alpine **3.14** (repo `apk.ish.app/v3.14`), Python **3.9.16**, pip **26.0.1**, **UnityPy 1.25.4** (mới nhất trên PyPI). **pip KHÔNG dùng `--break-system-packages`** (flag này không tồn tại, plain `pip install` mới đúng).
- Chạy suite trên iSH mất ~30+ phút (full suite), cần để app foreground + cắm sạc + tắt Auto-Lock. Khi chạy suite trong sandbox thì đặt `TMPDIR` ra ổ home (tmpfs `/tmp` 512M đầy sẽ gây lỗi Errno 28 giả).
- AI KHÔNG chạm được vào iSH (`/root/pb3`): mọi lệnh iSH phải nhờ Joseph chạy và gửi ảnh/log về.

## 6. Kế hoạch gộp 9 mục (Joseph chốt 10:40 — checklist chính)

1. PAT + CI 2 job (`unit` bắt buộc, `assets` tải fixture từ Release) + test ngày sửa bằng `os.utime` trong setUp; **không skip test để lấy CI xanh**.
2. Proxy dịch Cloudflare Worker (key + KV cache server-side) + trang Kiểm tra dịch — **chờ tài khoản Cloudflare của Joseph**.
3. KB → IndexedDB + autosave + `persist()` + PWA install + nhắc export (Safari xóa storage sau ~7 ngày không tương tác nếu không cài vào Màn hình chính). — **DONE (d2e2f9b)**.
4. Worker pool 2 worker + checkpoint từng asset + watchdog trong worker.
5. Streaming reader + copy-through block + `DecompressionStream`.
6. Service worker precache Pyodide + lazy boot.
7. Font: atlas build-time trong Unity Editor (rẻ nhất: sửa material).
8. Plan JSON + UI checkbox REBUILD_ALL (tách planner/executor).
9. Wasm texture/audio — làm cuối hoặc không làm (asset nhị phân: read-only, dùng iSH).

Đã bỏ hẳn COOP/COEP khỏi kế hoạch. Giới hạn chi phí: proxy dịch có **giới hạn cứng 500.000 ký tự/tháng** (hết thì tự rơi về MyMemory + từ điển offline, không tự tốn phí).

## 7. Truy cập AI mới cần (Joseph cấp)

- **Đọc**: repo public — không cần quyền. Trang live + CI runs xem công khai được.
- **Đẩy code**: cần PAT fine-grained của Joseph (repo MeoRocky, Contents Read and write; **không có quyền Workflows** — file `.github/workflows/` phải đẩy qua giao diện web github.com). PAT cũ tên `BearTea`, hết hạn 14/02/2027 — AI mới KHÔNG được xin Joseph gửi token qua chat; Joseph tự tạo token mới cho AI mới trên github.com (Settings → Developer settings → Personal access tokens) hoặc cấp quyền lại.
- **KHÔNG có sẵn**: mật khẩu GitHub, mã OTP, token cũ (nằm trong Secure Vault của AI cũ, không chuyển được). Mọi đăng nhập GitHub mới đều do Joseph tự làm trên trình duyệt của anh ấy.
- **iSH**: chỉ Joseph chạy lệnh; AI mới đưa lệnh chính xác (`cd /root/pb3`, `unzip -o ~/pb3_handoff_final_patch02_sandbox.zip`, `python3 run_tests.py --tier full`, …) và đọc ảnh/log anh ấy gửi về.

## 8. Cách bắt đầu (cho AI mới)

1. Đọc file này + clone repo `MunKai0408/MeoRocky` + giải nén zip patch02 (hỏi Joseph lấy file zip nếu chưa có).
2. Kiểm tra head `main`, trang live (curl), các CI run gần nhất, và thư mục `exp/` để biết coordinator cũ đã làm tới đâu.
3. Đọc `exp/verify_redesign_results.md` (kết quả kiểm chứng redesign) và các báo cáo mới nhất trong `exp/`. (Hai file `upgrade_results.md`, `redesign_results.md` không có trong cây.)
4. Tiếp tục từ todo dưới đây. Báo Joseph bằng tiếng Việt, ngắn gọn, nói rõ cái gì đã kiểm chứng và cái gì chưa.

## 9. Việc đang dở (todo tại thời điểm bàn giao)

- [ ] P0-1 (E) Ép lại bản dịch (làm sau thời điểm bàn giao, trong cây làm việc, chưa đẩy): cần kiểm chứng flow trình duyệt trên Safari thật + thử iSH cho bundle/entry quá lớn theo bước trong `exp/analysis_rebuild_results.md`; phần sửa-lỗi còn lại do agent tiếp nối xử lý. (Cập nhật 2026-11-17: P0-1 đã xong trong cây làm việc; agent sửa-lỗi tiếp nối đã xử lý các mục nhóm 1–4 — xem §10 và báo cáo của agent đó. Kiểm chứng Safari/iSH vẫn chưa làm.)
- [ ] P0-2 (cổng kiểm tra trước khi giao, bước 6): đã xong trong cây làm việc 2026-11-17 (xem `exp/delivery_gate_results.md`); cần kiểm chứng flow trình duyệt trên Safari thật + thử iSH theo bước trong §6 của báo cáo đó. Kiểm chứng Safari/iSH vẫn chưa làm.
- [ ] P0-3 (trích xuất cấu trúc bảng ngôn ngữ + StringTable, lọc rác, conflicts): đã xong trong cây làm việc 2026-11-17 (xem `exp/p03_language_source_results.md`); mốc hồi quy §4 ĐỔI có lý do — key `.sNNNN` thành tên term thật (9K en 13.465→6.694, zh 1.914→2.176; PB3 en 14.556→11.065, zh 2.570→2.350; EK2 en 17.240→10.468, zh mất file vì không có dữ liệu zh; merged==tách 100%, #N=0). Không đổi UI nên không có bước thử Safari/iSH mới. Chờ Joseph: file 9 Kings v1.0.5 để chạy lại đúng bản (mọi số 9 Kings hiện là v1.0.6).
- [ ] Mục 4–9 kế hoạch gộp (worker pool → wasm texture) — kiểm tra trạng thái thực tế rồi làm tiếp. (Cập nhật 2026-11-17, đọc từ cây + `exp/`: mục 4 có pool worker chọn được 1–2 (C9, `worker-pool.js`, 16/16); mục 5 có streaming reader (`stream.js`, `DecompressionStream`) + đường rebuild streaming (P0-1, `sdk/rebuild.py`); mục 6 có SW precache v21 (120 URL, 0 mục chết, sau P0-2 thêm `sdk/gate.py`); mục 7 có tab Font + script atlas trong `unity_editor/` (2 file TMP .cs); mục 8 có plan JSON + UI checkbox (mục 8, 16/16) + patch pack (C7, 13/13), REBUILD_ALL vẫn khóa ở M4 trên web; mục 9 chỉ là đánh giá, quyết định KHÔNG build decoder — asset nhị phân read-only, dùng iSH. Tất cả đều là sản phẩm sandbox; Safari thật/iSH vẫn chưa kiểm chứng.)
- [ ] Joseph thử lại trên Safari thật sau đợt hotfix + redesign: nút Lưu/Chia sẻ có lưu được file không, bảng Danh sách còn vỡ chữ không, Inspect IPA chọn bundle, Giải nén `.tar.xz` về tên gốc, Google Translate trên mạng di động, trang `diagnostics.html`.
- [ ] Joseph kiểm chứng iSH zip patch02: suite full + extract `game.ipa` + font scan (kỳ vọng đủ 142/142). Sau đó mới tính RELEASE.
- [ ] Mục 2 Cloudflare proxy: chờ Joseph tạo tài khoản Cloudflare + Google API key.
- [ ] Patch03 (sau này): port fix mojibake U+200E sang cây iSH.
- [ ] Nếu Joseph mua domain `beartea.app` (đang trống, ~12–15 USD/năm): DNS CNAME → `munkai0408.github.io` + nhập domain ở Settings → Pages → Custom domain.

## 10. Sổ đính chính (2026-11-17, agent sửa-lỗi tiếp nối sau P0-1)

Nguyên tắc: báo cáo lịch sử trong `exp/` KHÔNG sửa nội dung; đính chính
ghi ở đây + `README.md` + báo cáo của agent. "Tin phía X" nghĩa là phía
đó thắng khi hai nguồn mâu thuẫn.

### Ngưỡng gzip — đính chính quan trọng (M12/N3)

- Ngưỡng gzip vỏ trang trong code là **102400B** (`exp/verify_item6.mjs`:
  `shellGzip < 100 * 1024`). Nhãn "(gzip đạt)" trong 3 báo cáo (B)/(C)/(D)
  là SAI: C7 101.817B đạt; C8 103.681B và C9 105.617B đều vượt ngưỡng.
  Các báo cáo cũ không sửa; đọc "(gzip đạt)" ở (B)/(C)/(D) thì hiểu là sai.
- Handover cũ nói ngưỡng "chưa chốt" là sai — ngưỡng đã chốt trong code.

### Mâu thuẫn mới N1–N15 (audit 2026-11-17)

- N1 (precache 13 wheel chết): đã xử lý ở P0-1 (precache 131→119, CACHE
  v20). Kiểm lại 2026-11-17: 119 mục, 0 mục chết, 7/7 file pyodide có
  trên đĩa.
- N2 (C9 .md vs .json): tin bản .md cho thời điểm C9 (chuỗi C8→C9 liền
  mạch); bản .json bị ghi đè bởi lần chạy harness sau đó [HYPOTHESIS về
  nguyên nhân]. Cả hai bộ số đều vượt ngưỡng gzip.
- N3: xem ngưỡng gzip ở trên.
- N4 (timestamp C4 trước C3): tin thứ tự logic C3→C4 theo chuỗi
  CACHE_VERSION; timestamp [CHƯA KIỂM CHỨNG], không dùng để dựng trình tự.
- N5 (nhảy 1657→1692): đếm lại 2026-11-17: hai file test mới của (A) chứa
  39 method (`test_langdetect` 24 + `test_translate_source` 15),
  1657+39=1696 khớp tổng (A). Con số "1692" và "4 test mới" trong (A) §8
  không khớp với đếm này; vì sao báo cáo ghi 1692/4 vẫn [CHƯA KIỂM
  CHỨNG] — không bịa lời giải.
- N6 (Poly Bridge 3 lệch 11): tin bảng Gộp cho số sau gộp (tự cộng khớp);
  lệch 11 chưa giải thích được [HYPOTHESIS: 11 entry CSV bị loại/trùng].
- N7 (Passpartout 3 định nghĩa đếm): dùng số bảng (en 2.710, zh 1.715)
  cho mốc hồi quy; số 1.091/1.744 chỉ dùng khi ghi rõ định nghĩa đếm.
- N8 (đường dẫn chết): đã sửa trong `README.md`/`HANDOVER.md` đợt này
  (chú thích 9 đường dẫn; xem §2/§8 trên). File
  `PB3_v3.1_10_Claude_Prompts.txt` không có trong cây này; tham chiếu
  trong `pb3/handoff/decisions.md` giữ nguyên (không thêm mục quyết định
  mới).
- N9 (Unity v8 vs v6–v9): tin code + ghi chú tab (v6–v9, v9 chỉ đọc).
  Đã sửa footer `index.html` + 2 chỗ trong `README.md` đợt này.
- N10 (Pyodide ~13MB vs ~16,5MB): tin đĩa/README cho cây này (Pyodide
  13.605.176B). Comment `sw.js` đã sửa 2026-11-17 theo số đo lại từ đĩa:
  precache tổng 16.103.797B = 15,36 MiB.
- N11 ("6 bước" vs "7 bước"): tin 7 bước (code + đa số tài liệu). "6
  bước" còn lại trong bảng kiểm chứng lịch sử của `README.md`, có thể do
  đếm gộp bước 6+7 [HYPOTHESIS]; bảng đó đã có ghi chú đính chính.
- N12 (đụng tên C6/C7): hai hệ đánh số khác nhau; trích dẫn "C7" phải
  ghi rõ tên file báo cáo (TMP fallback hay patch pack).
- N13 (I2Languages 29 hay 30): [CHƯA KIỂM CHỨNG]; phía Gộp (30 khe, đọc
  thẳng cấu trúc chunk) đáng tin hơn.
- N14 (số file Pyodide 6/7/20): đáng tin nhất cho cây hiện tại là đếm
  đĩa = 7 file; precache khớp đĩa sau P0-1; danh sách file trong
  `README.md` đã bổ sung `pyodide.mjs` đợt này.
- N15 (nhãn "UnityFS v5.x"): hiểu là player version (Unity 5.x), không
  phải format version [HYPOTHESIS mạnh]; nhãn trong bảng (A) không sửa
  (báo cáo lịch sử).

### Mâu thuẫn M1–M25 còn lại (phần lịch sử-nội bộ; phần sửa trong tài liệu hiện hành xem README/§1–§9)

- M3: phần lõi "HANDOVER lỗi thời" đúng; gán mục 6↔C3, 7↔C6, 8↔C7 là gán
  chéo hai hệ đánh số (xem N12). Trạng thái mục 4–9 xem cập nhật §9.
- M4: tin code + C5 cho đường ĐỌC (v9/Zstd chỉ đọc); tin HANDOVER/
  decisions cho đường GHI và iSH. Đã sửa §4 đợt này; `decisions.md`
  không thêm mục mới.
- M5: 1562 chỉ đúng cho cây patch02. Số đo 2026-11-17: lệnh job `unit`
  trên cây head 3d0c80c cho 1586 passed + 1 skipped + 132 deselected
  (pytest 9.2.0); trên cây làm việc (thêm test P0-1 + sửa-lỗi) là 1631
  passed + 132 deselected; full suite unittest trên cây làm việc: 1759
  chạy = 1729 đạt / 25 lỗi / 3 fail / 2 bỏ qua (28 đỏ do 2 fixture
  thiếu đã biết).
- M6: xem đính chính §3 (giữ 1440, ghi 1436, chênh 4 chưa đối chiếu).
- M11: mâu thuẫn nội bộ C3 (CACHE v10 vs v8→v9); tin chuỗi C4→C5→hiện
  tại (v20).
- M13: xem §1 (hai build 9 Kings khác nhau).
- M14: tin (B) 25/25 (đã hiệu chỉnh) thay vì 24/24.
- M16: tin (D) 1.719 OK là kết quả cuối; nguyên nhân lần chạy 1.694
  FAILED là [HYPOTHESIS] của chính báo cáo; nên chạy lại trong môi
  trường sạch.
- M17: số precache C3/C4 (126) không cộng khớp; tin chuỗi C5 + đếm hiện
  tại (119 sau P0-1).
- M20: giữ [CHƯA KIỂM CHỨNG] — không kết luận cây iSH đầy đủ có trên
  GitHub hay không. (Kiểm tra 2026-11-17 chỉ đối chiếu một số
  file/thư mục lẻ với main.)
- M22: BÁC BỎ — không phải mâu thuẫn thật: decisions #81 dùng v9 làm ví
  dụ của thứ KHÔNG hỗ trợ, nhất quán với #77.
- M23: NGOÀI cây làm việc (repo IPA-LOCALIZATION, chỉ tham chiếu) —
  không sửa được từ đây; khi tích hợp I1–I3 phải đọc code JS trực tiếp.
- M25: xem đính chính §3 (Release chưa có asset — API `assets: []` ngày
  2026-11-17).
- M1/M2/M7/M8/M9/M10/M18/M19/M21/M24: đã sửa trong tài liệu hiện hành
  đợt này (README/HANDOVER/CURRENT_STATE/index.html); xem diff với head
  3d0c80c.
