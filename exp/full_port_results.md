# Full port PB3 iSH → web — báo cáo tổng cuối (Phase 3)

Ngày: 2026-11-16. Repo: MunKai0408/MeoRocky, nhánh `main`.
Head trước Phase 3: `a75cd69` (Phase 1). Commit Phase 3: xem mục "Đẩy lên
main" cuối file (SHA ghi sau khi đẩy xong — file này nằm trong chính
commit đó nên SHA chỉ có sau khi push; agent báo SHA + link trong tin
nhắn bàn giao).

## 1. Kiểm chứng tích hợp — chạy lại từ đầu (Phase 3, không tin mù báo cáo Phase 2)

Pyodide 314.0.8 (Python 3.14.2), Node trên VM, chạy `bridge.py` trên đúng
bản vendor trong `site/pb3/` (không dùng cây PB3 gốc, không stand-in).

| Harness | Kết quả | Ghi chú |
|---|---|---|
| `exp/verify_module_worker.mjs` | **17/17 PASS** | boot 20/20, extract, dịch offline, nén, font 142/142, chẩn đoán, 0 fetch http |
| `exp/verify_patch01_web.mjs` | **8/8 PASS** | hồi quy patch01 (XML thụt đầu dòng không crash, bỏ dấu `.` cuối đường dẫn) |
| `exp/verify_parity.mjs` | **14/14 PASS** | ngang iSH: Mod dry-run + ghi thật + bundle, Batch tuần tự + resume, KB đủ 6 hàm, support matrix, dịch |
| `exp/verify_limit_2gb.mjs` | **6/6 PASS** | giới hạn 2GB đúng biên (2GiB qua, 2GiB+1 chặn) |
| **Tổng** | **45/45 PASS, 0 FAIL** | |

### Sửa harness trong lúc kiểm chứng (không sửa kỳ vọng cho vừa kết quả)

- `verify_patch01_web.mjs` và `verify_limit_2gb.mjs` FAIL ở mục
  `write_site_files`: 2 harness cũ hardcode `man.files.length === 71`,
  nhưng `site/pb3/files.json` đã có **87 entry** từ Phase 1 (71 cũ + 16
  mới: services 10 + sdk 3 + ui 3 — harness `verify_module_worker.mjs`
  đã cập nhật 87 từ trước). Việc ghi file thật ra đã thành công (87 file
  ghi xong, boot 20/20 ngay sau đó); chỉ số kỳ vọng trong harness là lỗi
  thời. Đã sửa 2 harness về `=== 87` kèm chú thích, chạy lại xanh 8/8
  và 6/6. Không sửa code sản phẩm ở mục này.
- `kb_import` FAIL ở lần chạy parity đầu tiên — lỗi thật, đã sửa (xem §2).

## 2. kb_import — LÀM ĐƯỢC (Phase 3)

Khoảng trống Phase 2 để lại đã lấp:

- `site/pb3/bridge.py::kb_import(path)`: mở **bản sao** của file `.sqlite`
  người dùng tải lên làm KB nguồn (KB mở bằng WAL/migrate nên không được
  mở trực tiếp file gốc), xuất từng profile (`export_profile`) rồi nhập
  vào KB phiên (`import_profile`, không có file game để đối chiếu hash
  nên mỗi profile kèm cảnh báo "chưa kiểm hash" — profile chỉ là gợi ý).
  Game trùng bundle id bị ghi đè (hành vi của `import_profile`).
- **Lỗi thật gặp khi kiểm chứng:** file KB do Phase 2 xuất chứa game
  `game1.zip` **không có bundle id** (zip fixture không có Info.plist) mà
  `import_profile` lấy bundle id làm khóa nên ném `E_PLUGIN_INVALID`
  ("Manifest sai, api_version lệch...") làm hỏng cả lần nhập. Đã sửa
  `kb_import`: game không có bundle id bị **bỏ qua** (ghi vào `skipped` +
  cảnh báo rõ lý do), một profile lỗi cũng không làm hỏng các game khác
  (try/except từng game). Trả về `{ok, imported, skipped, games,
  warnings}`.
- UI: tab KB có thêm ô chọn file `.sqlite` + nút **"Nhập KB (.sqlite)"**
  (`index.html`, `app.js`); các dòng "bản web chưa có nút nhập lại file
  KB" trong `bridge.py`/`index.html`/`app.js`/`README.md` đã gỡ hết.
- Kiểm chứng mới trong `exp/verify_parity.mjs` (mục `kb_import`, 2 trường
  hợp): (1) nhập đúng file KB Phase 2 đã xuất → `imported=0, skipped=1`,
  cảnh báo "không có bundle id", KB vẫn trống, **file nguồn không bị sửa**
  (so byte trước/sau); (2) dựng file KB nguồn có bundle id thật
  (`com.example.kbtest`, bằng `sdk.kb.remember`) rồi nhập →
  `imported=1`, đúng bundle id, `kb_list` = 1 game, có cảnh báo
  "chưa kiểm hash". Mục này PASS trong lần chạy parity cuối (14/14).

## 3. GitHub Actions — `.github/workflows/test.yml` (mới)

- Chạy **toàn bộ suite PB3 (1562 test, `python3 run_tests.py --tier
  full`)** mỗi lần push lên `main` và mỗi pull request, trên
  `ubuntu-latest` với **Python 3.9** (đúng phiên bản iSH; suite cũng đã
  xanh trên 3.12 trong sandbox — ghi rõ trong comment đầu file).
- Các bước: checkout → setup-python 3.9 → `pip install lz4` (phụ thuộc
  duy nhất ngoài stdlib: `tests/test_polish.py` import `lz4.frame` không
  có fallback) → bước **kiểm tra fixture** (liệt kê rõ 28 file còn
  thiếu trong log, không làm fail job) → **khôi phục font** (giải mã
  `fonts/BeVietnamPro-Regular.ttf.b64` thành
  `pb3/fonts/BeVietnamPro-Regular.ttf`, byte-identical 132.948 byte, để
  5 test font chạy được) → chạy suite (log `test-full.log`) → upload
  artifact `pb3-test-log` (log + `reports/failures.txt` +
  `reports/test_times.json`).
- Comment đầu workflow ghi rõ: suite **KHÔNG chạy được trong trình duyệt**
  (cần threading/subprocess/CLI thật + ghi file) nên CI là nơi kiểm chứng.
- Kiểm chứng workflow: YAML parse OK bằng `python3 -c yaml.safe_load`
  (lưu ý: PyYAML đọc `on:` thành boolean True — đúng YAML 1.1, Actions
  hiểu bình thường); đường dẫn/lệnh đối chiếu với cây vendor thật
  (`pb3/run_tests.py` có thật ở `site/pb3/run_tests.py`, tức `pb3/` ở
  gốc repo). **Không chạy được Actions thật từ đây** — lần chạy CI đầu
  tiên chỉ biết sau khi commit này lên main.

## 4. Tình trạng CI — ĐO THẬT, không báo PASS giả

Chạy suite trong bản sao của cây vendor (Python 3.12, sandbox,
`TMPDIR` trên ổ home để tránh /tmp tmpfs 512M đầy — bài học cũ):

| Trường hợp | Tổng | Đạt | Lỗi | Fail | Bỏ qua | Exit |
|---|---|---|---|---|---|---|
| Thiếu 28 file (trạng thái main hiện tại) | 1562 | 1386 | 117 | 58 | 1 | 1 |
| Đủ 28 file + font đã giải mã | 1562 | 1560 | 0 | 1 | 1 | 1 |

- Thiếu 28 file: **175 mục không xanh, trong đó 174 do thiếu fixture**
  (105 mở trực tiếp fixture thiếu → `FileNotFoundError` /
  `E_INPUT_NOT_FOUND`, + 69 lỗi kéo theo vì bước extract trước đó không
  có file để chạy) và **1 do test nhạy mtime** (xem dưới). CI ĐỎ là đúng.
- Đủ 28 file: chỉ còn **1 fail duy nhất**:
  `test_support_matrix.py::test_effective_maturity_uses_corpus_not_declared`
  hardcode `test_date = "2026-11-15"`, nhưng ngày này lấy từ **mtime**
  của `tests/corpus/json_text.json` — git checkout đặt lại mtime thành
  ngày checkout nên test luôn lệch ngày (lần đo này ra `2026-11-16`).
  Đây là lỗi thật trong cây, **không sửa ở Phase 3** (không sửa test cho
  vừa CI). Sửa đúng: test tự đặt mtime file corpus trong `setUp` — để
  dành đợt vá sau.
- 28 file chưa lên main (connector GitHub chỉ nhận UTF-8, parent sẽ đẩy
  bằng trình duyệt sau): `pb3/kb/pb3_kb.sqlite` (1), 24 fixture nhị phân
  trong `pb3/tests/fixtures/` (`.zip`/`.ipa`/`.assets`/`.unity3d`/`.pak`/
  `.locres`/`.mo`/`.sqlite`/`.bin`/`b.plist`), 3 file text UTF-16
  (`b.strings`, `unicode.json`, `unicode.xml`). 25 file `test_*.py` nhắc
  tới ít nhất một fixture thiếu.
- Ngoài 28 file trên còn **1 file nhị phân nữa chưa lên main**:
  `pb3/fonts/BeVietnamPro-Regular.ttf` (bản web chỉ có dạng `.b64`).
  Workflow CI đã tự giải mã `.b64` nên 5 test font không bị ảnh hưởng;
  sau khi file `.ttf` lên main thì bước giải mã trong workflow là thừa
  (vẫn chạy được, ghi đè bằng đúng nội dung).

## 5. README parity — `site/README.md` (đã cập nhật)

- Thêm mục **"Đối chiếu web vs iSH (full port)"**: bảng 14 hàng (Mod,
  Extract, Dịch, Nén, Font 142/142, Chẩn đoán, Batch, KB, Support matrix,
  Plugin list 20, Test suite, UnityPy, giới hạn file 2GB, lần mở trang
  đầu cần mạng) với cột web / iSH / ghi chú.
- Thêm mục **"CI — test suite trên GitHub Actions"** (số liệu đo thật
  ở §4 + test mtime sẽ đỏ).
- Cập nhật: KB đã có nút nhập (cả mục Cách dùng, mục Giới hạn thật và
  mô tả `bridge.py` thêm `kb_import`); sửa dòng "Không vendor: test
  suite" lỗi thời — `pb3/tests/` có trong repo (CI chạy) nhưng trình
  duyệt chỉ nạp 87 file runtime trong `pb3/files.json`.

## 6. Bảng parity web vs iSH (tóm tắt)

| Chức năng | Web | iSH |
|---|---|---|
| Mod | Có | Có — web không có REBUILD_ALL, không đóng gói lại IPA |
| Extract | Có | Có |
| Dịch Google + offline | Có | Có — Google có thể bị chặn (429/CORS) → fallback offline |
| Nén | Có | Có |
| Font 142/142 | Có | Có |
| Chẩn đoán | Có | Có |
| Batch | Có, **tuần tự** (jobs=1), không timeout từng game | Có, song song được |
| KB | Có, **chỉ trong phiên** (+ nhập file .sqlite) | Có, SQLite WAL lâu dài |
| Support matrix | Có | Có — web: cột ngày test "chưa có" |
| Plugin list 20 | Có (20/20 [ok]) | Có |
| Test suite | **Chỉ ở CI** | Chạy tay trên iSH |
| UnityPy | **Không có** (parser thuần) | Có (1.25.4) |
| Giới hạn file | 2GB (RAM tab Safari là rào cản thật) | 2GB |

## 7. Giới hạn thật (nhắc lại, không giấu)

- Không UnityPy trên web (thiếu wheel `etcpak`); inspect Unity bằng
  parser thuần (UnityFS v8, SerializedFile v23).
- Batch tuần tự, không timeout từng game; Mod không có REBUILD_ALL.
- KB chỉ trong phiên; profile nhập chỉ là gợi ý, game không bundle id
  không nhập được.
- Dịch Google có thể bị chặn (429/CORS) → tự rơi về offline và báo rõ.
- Test suite chỉ ở CI; CI đỏ tới khi đủ 28 file + sửa test mtime.
- Lần mở trang đầu cần mạng (Pyodide ~13MB cùng origin, không CDN).

## 8. Đẩy lên main — ⛔ BỊ CHẶN (403, chưa đẩy được)

- Commit dự kiến (1 commit, toàn file text): "Full port: web parity with
  iSH (Mod/Batch/KB/support matrix) + CI" — 15 file đã chuẩn bị sẵn trong
  staging dir `~/workspace/pb3_web/_push_stage/` + manifest
  `~/workspace/pb3_web/_push_paths.txt` (đã kiểm: đủ 15 file, toàn UTF-8,
  tổng 171.393 byte).
- **Lỗi gặp (2026-11-16, Phase 3):** `push_files` → `403 Resource not
  accessible by integration` ở `POST .../git/trees`; thử dự phòng
  `create_or_update_file` cho 1 file mới cũng 403 ở `PUT
  .../contents/.github/workflows/test.yml`. Đọc vẫn chạy tốt
  (`list_commits`, `get_file_contents` OK), head main vẫn là `a75cd69`
  (không có commit nào bị đẩy nửa chừng). Tài khoản đúng là MunKai0408
  (id 134508215), không cần re-auth.
- **Nguyên nhân có thể nhất:** quyền ghi của GitHub App "Meta Muse AI"
  trên repo này đã mất/thay đổi sau các lần đẩy trước (các commit
  `df52f736`…`a75cd69` trước đó đẩy qua connector vẫn thành công) — lỗi
  403 này cũng từng xuất hiện khi tạo repo mới qua API. Agent không tự
  sửa được quyền App từ đây.
- **Cách gỡ (cho Joseph/parent):** (1) kiểm tra lại cài đặt App trên
  github.com: repo Settings → GitHub Apps → Meta Muse AI → Repository
  access (phải gồm `MeoRocky`) và Permissions → Contents phải là
  Read & write; sửa xong thì chạy lại đúng lệnh `push-files` với staging
  dir + manifest ở trên (đã kiểm tra sync-base: 4 file sửa còn ở trạng
  thái trước Phase 2, 11 file mới chưa tồn tại trên main). (2) Dự phòng:
  đẩy bằng trình duyệt — github.com → Upload files, kéo cả 15 file
  (giữ đúng cấu trúc thư mục) vào 1 lần upload = 1 commit với đúng
  message trên.
- SHA commit: **chưa có** (chưa đẩy được — xem tin nhắn bàn giao của
  agent sau khi gỡ chặn).
- Kiểm chứng sau đẩy (khi đã đẩy xong): head main = SHA commit này (đọc
  lại qua connector), trang live `https://munkai0408.github.io/MeoRocky/`
  vẫn HTTP 200, 1–2 file đã đổi kiểm tra qua raw.githubusercontent.com.
  Trang live kiểm tra bằng curl ngày 2026-11-16 (trước khi đẩy, vẫn ở
  commit `a75cd69`): HTTP 200, 9.437 byte, có chữ BearTea (xem §8b).

### 8b. Trang live (kiểm tra bằng curl, 2026-11-16)

- `https://munkai0408.github.io/MeoRocky/` → HTTP 200, 9.437 byte, nội
  dung đúng BearTea (vẫn là bản ở commit `a75cd69`, chưa có thay đổi
  Phase 2/3 vì commit chưa đẩy được — xem §8).
- `raw.githubusercontent.com/.../main/pb3/bridge.py` → HTTP 200 (bản cũ
  trên main, chưa có `mod_patch`/`kb_import`).

## 9. Còn cần Joseph thử trên Safari thật

1. **Boot**: tải lại cứng trang live, đợi trạng thái "Sẵn sàng" (lần đầu
   cần mạng tải Pyodide ~13MB).
2. **Extract IPA 9 Kings thật** (file `game.ipa`, lưu ý bàn phím iOS hay
   tự chèn dấu `.` cuối đường dẫn — đã có fix bỏ dấu `.` cuối).
3. **Thử Mod** (patch 1 chuỗi, dry-run rồi ghi thật), **Batch** (manifest
   2 game, xem checkpoint/resume), **KB** (thêm game → tải file KB về →
   tải lại trang → nhập lại file KB → liệt kê phải thấy lại game).
4. **Dịch Google thật**: bật checkbox Google, dịch 1 file JSON nhỏ trên
   mạng di động — kiểm chứng xem Google có trả 429 như từ IP máy chủ
   không (nếu bị chặn, trang tự rơi về offline và báo rõ lý do).
5. Sau khi 28 file nhị phân lên main: xem lần chạy CI đầu tiên trên tab
   Actions (kỳ vọng đỏ 1 test mtime — đó là lỗi đã biết, xem §4).
