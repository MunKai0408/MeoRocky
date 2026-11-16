# Bàn giao dự án PB3 / BearTea — HANDOVER

- Ngày bàn giao: 2026-11-16
- Chủ dự án: Joseph (timezone Asia/Ho_Chi_Minh, iPhone 11 Pro Max, dùng iSH)
- AI cũ: Muse — AI mới đọc file này trước, rồi làm theo mục 8.
- Ngôn ngữ trả lời Joseph: tiếng Việt, ngắn gọn (tin nhắn điện thoại).

## 1. Dự án là gì

PB3 v3.1 (tên hiển thị: **BearTea**) là công cụ xử lý/mod game Unity, viết bằng Python (stdlib only).

- **Bản iSH**: chạy trên iPhone của Joseph qua iSH, thư mục `/root/pb3`. Game thật: **9 Kings v1.0.5** (file IPA 141.865.757 byte).
- **Bản web**: Python chạy trong trình duyệt bằng Pyodide (100% client-side), host trên GitHub Pages. Repo: **MunKai0408/MeoRocky** (public), trang live: https://munkai0408.github.io/MeoRocky/
- Chức năng: Danh sách entry (ZIP/IPA), Inspect Unity, Trích xuất, Dịch (Google Translate + offline), Mod (sửa text TextAsset), Batch, Kho KB, Hỗ trợ, Nén IPA (tar.xz), Font tiếng Việt, Chẩn đoán. Menu iSH có [1]–[7]; web có 11 tab tương ứng.

## 2. Tài liệu và code — đọc trước theo thứ tự

1. File này (HANDOVER.md).
2. Repo web https://github.com/MunKai0408/MeoRocky — code web đầy đủ trong `site/` (`index.html`, `app.js`, `worker.js`, `pb3/` = cây PB3 đã vendor, `pyodide/`).
3. File zip iSH mới nhất: **`pb3_handoff_final_patch02_sandbox.zip`** (1.142.330 byte, md5 `d18a5f7b7caa65f143f56fc3d99cbf30`, 383 entry, suite **1562 test xanh** trong sandbox, 20 plugin [ok]) — đây là cây iSH ĐẦY ĐỦ (code + test + docs + handoff). **Cây iSH đầy đủ CHƯA có trên GitHub** (repo web chỉ có bản vendor trong `site/pb3/`); AI mới cần file zip này để làm tiếp bản iSH.
4. Trong cây iSH: `handoff/account_01.md` … `account_10.md`, `account_final.md`, `account_patch01.md`, `account_patch02.md`, `decisions.md` (quyết định #1–#208), `CURRENT_STATE.md`.
5. Trong repo web: `exp/*.md` (báo cáo từng đợt: `upgrade_results.md`, `redesign_results.md`, `item1_results.md`, `item2_results.md`, `item3_results.md`, `clone_fix_results.md`, `phase1_ci_results.md` …), `README.md`.

## 3. Trạng thái hiện tại (2026-11-16 ~12:36, đã kiểm chứng)

Đã đẩy lên `main` và live:

- `ac1f3e5` — Hotfix Safari đợt 2: **navigator.share làm đường lưu chính** (blob download kẹt trên Safari), bỏ revoke URL sau 5 giây, Inspect ZIP cho chọn bundle `.assets` bên trong, Giải nén trả **tên file gốc** (thay vì `restored.bin`), bảng không vỡ chữ, code proxy dịch mục 2 (chờ deploy).
- `d2e2f9b` — Mục 3: KB chuyển sang IndexedDB + autosave + `persist()` + PWA.
- `37193ac` — Redesign giao diện theo mockup Joseph gửi (hero gấu trà sữa, khung thả file, 6 thẻ tính năng, File gần đây, bottom nav, sáng/tối — chỉ đổi vỏ, giữ nguyên chức năng).
- `f00d098` — CI: pyodide-smoke bỏ qua fixture nhị phân vắng mặt thay vì crash ENOENT.
- `0646735` — CI: Python 3.12 (Ubuntu 26.04 bỏ Python 3.9).
- `ca3caef` — Hotfix bug clone Safari (gửi file sang worker 1 lần + `slice(0)`).

CI: job `unit` xanh lần đầu (run 43803496189: **1440 passed**, 126 deselected, 41,9 giây). Job `assets` chờ Release `fixtures-v1` — Release đã tạo (id 446604484); asset upload qua API GitHub bị 401 nên phải tải file asset lên qua giao diện web github.com.

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
- Unity: UnityFS tối đa **v8**, SerializedFile tối đa **v23**, không có v9 (không bịa). Không Zstd. **Không UnityPy trên web** (không cài được wheel etcpak) — inspect Unity trên web dùng parser thuần.
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
3. Đọc `exp/upgrade_results.md` và `exp/redesign_results.md` mới nhất.
4. Tiếp tục từ todo dưới đây. Báo Joseph bằng tiếng Việt, ngắn gọn, nói rõ cái gì đã kiểm chứng và cái gì chưa.

## 9. Việc đang dở (todo tại thời điểm bàn giao)

- [ ] Mục 4–9 kế hoạch gộp (worker pool → wasm texture) — kiểm tra trạng thái thực tế rồi làm tiếp.
- [ ] Joseph thử lại trên Safari thật sau đợt hotfix + redesign: nút Lưu/Chia sẻ có lưu được file không, bảng Danh sách còn vỡ chữ không, Inspect IPA chọn bundle, Giải nén `.tar.xz` về tên gốc, Google Translate trên mạng di động, trang `diagnostics.html`.
- [ ] Joseph kiểm chứng iSH zip patch02: suite full + extract `game.ipa` + font scan (kỳ vọng đủ 142/142). Sau đó mới tính RELEASE.
- [ ] Mục 2 Cloudflare proxy: chờ Joseph tạo tài khoản Cloudflare + Google API key.
- [ ] Patch03 (sau này): port fix mojibake U+200E sang cây iSH.
- [ ] Nếu Joseph mua domain `beartea.app` (đang trống, ~12–15 USD/năm): DNS CNAME → `munkai0408.github.io` + nhập domain ở Settings → Pages → Custom domain.
