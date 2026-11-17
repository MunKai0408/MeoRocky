# BearTea — PB3 bản web

PB3 (BearTea) — tool xử lý file game Unity — chạy ngay trong trình duyệt
bằng Pyodide, **100% trên máy bạn, không server, file không rời khỏi máy**.

Trang chạy tại: **https://munkai0408.github.io/MeoRocky/** (sau khi bật GitHub Pages,
xem bên dưới).

## Bật GitHub Pages (làm 1 lần, bấm tay)

GitHub API không bật được Pages nên bước này cần bấm tay:

1. Mở repo này trên github.com → **Settings** → **Pages**.
2. Mục **Build and deployment → Source**: chọn **Deploy from a branch**.
3. **Branch**: chọn **main**, thư mục **/(root)** → **Save**.
4. Đợi 1–2 phút, mở https://munkai0408.github.io/MeoRocky/ là dùng được.

## Cách dùng

1. Mở trang, đợi trạng thái “Sẵn sàng” (lần đầu cần mạng để tải trang +
   Pyodide ~13MB — đã vendor cùng trang, không qua CDN; sau đó xử lý
   hoàn toàn trên máy bạn).
2. Chọn file (IPA/ZIP/.unity3d/.assets/.ttf/.json/.tar.xz) bằng nút chọn file.
3. Chọn tab tính năng:
   - **Danh sách**: liệt kê entry của ZIP/IPA.
   - **Unity**: inspect bundle .unity3d hoặc file .assets độc lập
     (parser thuần của PB3, UnityFS v6–v9, SerializedFile v23).
   - **Trích xuất**: pipeline trích xuất đầy đủ của PB3, tải về `extract.zip`.
   - **Dịch**: dịch JSON key-value bằng Google Translate thật qua
     `fetch()` (cần mạng; 1 chuỗi/request, cách 0,4 giây) — Google lỗi
     thì tự thử MyMemory rồi dùng từ điển offline và báo rõ lý do; chỉ
     dịch VALUE, giữ KEY; có gắn credit “Việt Hoá Bởi Bear” nếu bật; nút
     Hủy dừng giữa chừng và giữ phần đã dịch. Có thể dán URL proxy dịch
     (Cloudflare Worker, xem `cloudflare/README.md`) vào ô “URL proxy
     dịch” — proxy có cache + hạn mức cứng 500.000 ký tự/tháng, hết hạn
     mức tự rơi về MyMemory + offline, không phát sinh phí. Trang
     `diagnostics.html` kiểm tra Google trực tiếp / proxy / MyMemory
     trên máy thật.
   - **Mod**: patch 1 chuỗi trong 1 TextAsset (menu [1] bản iSH) —
     mặc định dry-run (không ghi file nào); bấm “Patch và ghi file” mới
     tải đơn vị đã patch về (bundle/.assets, không đóng gói lại IPA).
     Trong tab Mod còn có mục **“Ép lại vào file gốc (E)”** (bước 5):
     nhận bản dịch JSON + sidecar provenance, xem trước dry-run (đơn vị/
     key sẽ đổi, key không ép được kèm lý do, xung đột chọn bản thắng),
     rồi ghi các đơn vị đã patch về (1 đơn vị → 1 file, nhiều đơn vị →
     1 ZIP; không phải IPA đóng gói lại, cần re-sign). Sau (E) là
     **“Cổng kiểm tra trước khi giao”** (bước 6, P0-2): 1 verdict
     PASS/FAIL duy nhất gộp 5 kiểm tra (bản dịch đầy đủ, placeholder/
     thẻ/xuống dòng, diffverify ép lại, độ phủ font 142 mục, toàn vẹn
     file); nút “Ép và ghi file” chỉ bật khi cổng ĐẠT cho đúng file +
     bản dịch hiện tại, FAIL thì chặn giao (xem
     `exp/delivery_gate_results.md`).
   - **Batch**: chạy hàng loạt theo manifest JSON/CSV (menu [4] bản iSH)
     — **pool worker song song thật** (mỗi worker là 1 thread hệ điều hành
     thật, chạy 1 Pyodide + PB3 riêng, không dùng threading/SharedArrayBuffer;
     chỉ bên trong một worker thì mỗi game vẫn tuần tự vì Pyodide không có
     pthreads); số worker chọn được 1–2 ở tab Batch (mặc định 2, tự động 1
     khi RAM thiết bị ≤ 4 GB);
     dry-run trước, checkpoint từng game lưu ngay vào IndexedDB sau mỗi game
     xong (resume bỏ qua game đã xong, kể cả sau khi tải lại trang), watchdog
     hợp tác 25 giây (dừng an toàn sau game hiện tại, bấm “Tiếp tục” chạy
     tiếp), tải về `batch.zip` gồm summary + checkpoint + từng game.
     2 worker tốn ~2x RAM; pool lỗi thì tự rơi về 1 worker.
   - **KB**: kho tri thức game (menu [3] bản iSH) — thêm/tra cứu/liệt kê/
     quên game, tải file KB (.sqlite) về, nhập lại file KB đã tải về bằng
     nút “Nhập KB (.sqlite)”. **Lưu lâu dài**: KB trong Worker được ghi
     tự động vào IndexedDB sau mỗi thay đổi và khôi phục tự động khi tải
     lại trang (xem `kb-store.js`, `kb_restore` trong `app.js`); nút tải/
     nhập file .sqlite dùng để chuyển KB sang máy khác. Game không
     có bundle id thì không nhập được (bị bỏ qua, có cảnh báo).
   - **Hỗ trợ**: ma trận hỗ trợ sinh từ dữ liệu thật của PB3 (registry,
     sdk.containers, sdk.unity, KB đã lưu) — không hardcode; cột “ngày
     test” hiện “chưa có” vì bản web không kèm tests/corpus/*.json.
   - **Nén**: nén IPA solid LZMA (.tar.xz); **Giải nén** khôi phục IPA gốc.
   - **Font**: quét font TTF (độ phủ 142 mục: 134 chữ cái + 8 dấu), gộp glyph từ
     font Be Vietnam Pro đi kèm, hoặc lấy font đi kèm về dùng.
   - **Chẩn đoán**: inventory + hex dump + quét chuỗi ASCII (chỉ đọc).
4. Kết quả tải về bằng nút tải xuống của trình duyệt.

## Kiến trúc

- `index.html` + `app.js`: giao diện tiếng Việt, dùng được trên điện thoại.
- `worker.js`: chạy Pyodide trong Web Worker **dạng module** (`new Worker("worker.js", { type: "module" })`
  trong `app.js`) — Pyodide bắt buộc module worker vì `pyodide.asm.mjs` là ES module; worker classic
  bị Safari từ chối với lỗi “Classic web workers are not supported”. Worker báo tiến độ % + ETA thật
  về main thread (luật PB3 #152–153).
- `pb3/`: mã nguồn PB3 được vendor (core, sdk, registry, plugins, config,
  services) — bản web chỉ gọi API tầng sdk qua `pb3/bridge.py`, không dùng
  menu chữ. Trình duyệt chỉ nạp 95 file runtime trong `pb3/files.json`;
  `pb3/tests/` (kể cả `tests/corpus/*.json`) có trong repo nhưng KHÔNG nạp
  vào trình duyệt — tests chỉ chạy ở CI (xem mục CI), corpus chỉ là bằng
  chứng ngày test cho ma trận hỗ trợ.
- `pb3/bridge.py`: thêm `mod_patch` (sdk.unity.apply_patch),
  `rebuild_apply` (sdk.rebuild: nhóm bản dịch theo provenance → 1 plan
  mỗi đơn vị → áp bằng executor của plan + đường rebuild streaming cho
  bundle độc lập lớn; xem `exp/analysis_rebuild_results.md`),
  `delivery_gate` (sdk.gate: cổng kiểm tra bước 6 — 1 verdict PASS/FAIL
  gộp 5 kiểm tra trước khi giao file; `rebuild_apply` bắt verdict PASS
  khớp file + bản dịch khi được truyền verdict; xem
  `exp/delivery_gate_results.md`),
  `batch_run` (sdk.batch.BatchRunner, jobs=1 tuần tự trong 1 worker),
  `batch_split`/`batch_zip`/`batch_merge_zip`/`batch_finalize`/`batch_checkpoint`
  (pool worker, Mục 4),
  `kb_add`/`kb_query`/`kb_list`/`kb_forget`/`kb_export`/`kb_import`
  (sdk.kb.KB, SQLite tại /tmp/kb trong Worker, lưu lâu dài qua IndexedDB;
  `kb_import` mở bản sao của file .sqlite tải lên rồi nhập từng profile bằng
  `export_profile`/`import_profile`), `support_matrix`
  (sdk.support_matrix.generate với rows từ registry thật).
- `fonts/`: font Be Vietnam Pro (SIL OFL 1.1, xem `fonts/OFL.txt`) lưu
  dạng base64 text để đẩy lên GitHub bằng API text-only; trình duyệt
  giải mã lại thành file .ttf khi chạy.
- Pyodide **v314.0.8** (nhãn nội bộ của dự án; `pyodide-lock.json` chỉ
  xác nhận Python 3.14.2 trong Pyodide) được **vendor ngay
  trong repo** (thư mục `pyodide/`: `pyodide.js`, `pyodide.mjs`,
  `pyodide.asm.mjs`, `pyodide.asm.wasm`, `pyodide-lock.json`,
  `python_stdlib.zip`, kèm wheel `lz4`) — tải cùng origin với trang, **không qua CDN**. Lý do: Safari
  trên iPhone chặn `fetch` cross-origin trong Worker (lỗi
  “Network response is CORS-cross-origin” khi tải Pyodide từ
  `cdn.jsdelivr.net`), nên bản web v1.1 bỏ hẳn CDN.

## Kết quả thử nghiệm (đo thật — giai đoạn 1, Node.js trên VM)

Xem đầy đủ ở `exp/phase1_results.md` — file này không có trong cây repo
này (báo cáo lưu ngoài), nên các con số dưới đây không kiểm chứng lại
được từ cây.

| Mục | Kết quả | Thời gian |
|---|---|---|
| Nạp Pyodide | PASS | 2,9 s |
| Extract `game1.zip` (21 entry, 6 file) | PASS | 1,4 s |
| Unity inspect thuần `resources.assets` (v17, 2019.4.40f1) | PASS | 0,0 s |
| `lz4pure` giải nén block LZ4 | PASS | 0,0 s |
| Dịch offline `play` → `Chơi` | PASS, không cần mạng | 0,3 s |
| Nén/giải nén `sample.ipa` (sha256 khớp) | PASS | 0,1 s |
| Mô-đun `lzma`/`sqlite3`/`tarfile`/`zipfile`/`lz4`/`PIL` | PASS cả 6 | 1,2 s |
| UnityPy qua micropip | **FAIL** — thiếu wheel `etcpak` | 16,9 s |

Hai điều chỉnh ở tầng khởi động (không sửa mã PB3): dựng registry với
`import_guard=None` (Pyodide mặc định không start được thread) và shim
`os.link` → `os.replace` (Pyodide không có `os.link`).

## Giới hạn thật (đừng tin ngược lại)

- **Không dùng UnityPy** trên web (không cài được — thiếu wheel `etcpak`).
  Inspect Unity chỉ dùng parser thuần của PB3 + `lz4pure`/binding `lz4`.
- **Batch pool worker — song song thật** (không threading): mỗi worker là
  1 thread hệ điều hành thật nên pool chạy song song thật (batch 2 game
  xong 24,6s trên file thật — con số này không kèm log trong cây,
  [CHƯA KIỂM CHỨNG]); chỉ bên trong một worker thì Python chạy tuần
  tự vì Pyodide không có pthreads — song song đi bằng các worker độc lập
  (mỗi worker 1 Pyodide + PB3 riêng), không dùng pthreads/SharedArrayBuffer/
  COI. Số worker chọn được 1–2 (mặc định 2, tự động 1 khi RAM thiết bị
  ≤ 4 GB). Watchdog hợp tác 25 giây (dừng an toàn, không
  terminate ngang). Checkpoint từng game vào IndexedDB. Tiến độ báo thật
  từng game (xong game nào bước 1 mốc).
- **KB lưu lâu dài trên máy**: file SQLite làm việc trong Worker
  (/tmp/kb) được ghi tự động vào IndexedDB sau mỗi thay đổi và khôi
  phục tự động khi tải lại trang (`kb-store.js`, `kb_restore` khi boot;
  `bridge.py` báo `persisted: True`). Nút “Tải KB về (.sqlite)” /
  “Nhập KB (.sqlite)” dùng để chuyển KB sang máy khác. Profile nhập
  vào chỉ là gợi ý (hash chưa đối chiếu với file game); game không có
  bundle id thì không nhập được.
- **Mod**: mặc định dry-run (không ghi file nào); chỉ ghi khi bấm nút ghi
  rõ ràng (tương đương --allow-write), file gốc không bao giờ bị ghi đè.
  Output là đơn vị nhỏ nhất đã patch (bundle/.assets) — **không đóng gói
  lại IPA** (giới hạn 6.6 của PB3). REBUILD_ALL không hỗ trợ trên web
  (cần maturity M4, chưa có).
- **Ép lại (E) — giới hạn v1 (P0-1):** chỉ ghi TextAsset JSON dạng
  dict; key MonoBehaviour/StringTable vào danh sách “không ép được” kèm
  lý do, không im lặng bỏ (chưa có đường ghi MonoBehaviour an toàn).
  Bundle độc lập ≥64MB dùng đường rebuild streaming (entry đơn >128MB
  thì từ chối, hướng iSH); bundle mã hóa bị từ chối (không giải mã). File >400MB có confirm riêng về RAM/streaming;
  flow trình duyệt (E) chưa kiểm chứng trên Safari thật (xem
  `exp/analysis_rebuild_results.md`).
- **Cổng kiểm tra (bước 6, P0-2):** chỉ áp cho flow (E); đường plan/mod
  trực tiếp giữ dry-run/confirm riêng, chưa qua cổng. Cổng kiểm bằng mô
  phỏng trong RAM (đường RAM của (E)); bundle độc lập ≥64MB thì kiểm file
  đã ép thật ở chế độ pressed thay vì mô phỏng. File đã ép chỉ cần khớp
  nội dung + sha script theo kế hoạch, không cần giống hệt bytes mô phỏng
  (công cụ ép khác vẫn được chấp nhận; khác bytes thì có cảnh báo). Gọi
  bridge `rebuild_apply` trực tiếp mà không truyền verdict thì giữ hành vi
  cũ (không bị cổng chặn) — UI là đường giao duy nhất của sản phẩm và luôn
  truyền verdict. Font nhúng Unity (class 128) chỉ liệt kê được tên, không
  đọc được bytes nên độ phủ của chúng là “chưa kiểm được”, không mặc định
  tốt/xấu; muốn kết luận chắc chắn thì chỉ định font hiển thị hoặc dùng
  font đi kèm. Flow trình duyệt cổng chưa kiểm chứng trên Safari thật (xem
  `exp/delivery_gate_results.md`).
- **Trích xuất bảng ngôn ngữ có cấu trúc (P0-3):** MonoBehaviour chứa bảng
  ngôn ngữ của game được đọc đúng cấu trúc `mLanguages`/`mTerms` (ánh xạ theo
  chỉ số, giữ chuỗi rỗng), không qua quét chuỗi phẳng; object nào không qua
  kiểm chứng cấu trúc thì không được coi là bảng ngôn ngữ. Pipeline xuất 1
  file mỗi mã gốc (`*.langtable.monobehaviour.<ngôn ngữ>.json`, key là tên
  term thật; biến thể gộp về mã gốc). Bundle StringTable gộp theo nhóm
  (bundle, locale, collection), key dạng `table/key`. Rác Unity (kiểu
  `UnityEngine.*`, class USS `unity-*`, UXML, namespace 3 đoạn) bị loại khỏi
  đường generic. Khi gộp có xung đột, ngoài provenance còn ghi file
  `conflicts.json` trong thư mục xuất. Đường quét chuỗi phẳng cũ chỉ còn cho
  object không phải bảng ngôn ngữ (ghi rõ lý do trong code) — xem
  `exp/p03_language_source_results.md`.
- **Ma trận hỗ trợ**: bảng plugin/container/Unity lấy từ dữ liệu thật của
  PB3 (registry + sdk), nhưng cột “ngày test” hiện “chưa có” vì bản web
  không vendor tests/corpus/*.json; cột “số game đã test” chỉ đếm KB đã
  lưu trên máy này (không có số liệu toàn cục).
- **Dịch Google trên web** (`gt.js` + `bridge.translate_json_google`):
  Python async gọi `fetch()` của JS (endpoint gtx miễn phí, không key),
  1 chuỗi/request, cách 0,4 giây, thử lại tối đa 3 lần với backoff khi
  429/5xx; lỗi nào (mất mạng, CORS, 429 hết retry, trang Sorry) cũng thử
  MyMemory rồi rơi về từ điển offline và báo rõ trong kết quả
  (`translated_by`, `network_error`); nút Hủy dừng giữa chừng. **Lưu ý
  trung thực:** từ IP máy chủ phát triển, Google trả HTTP 429 + trang
  Sorry (IP datacenter bị giới hạn) nên dịch Google thật chưa kiểm chứng
  được từ VM này (báo cáo `exp/translate_web_results.md` không có trong
  cây này); IP mạng di động/
  dân cư thường dịch được. Trang `diagnostics.html` kiểm tra Google trực
  tiếp / proxy / MyMemory ngay trên Safari thật.
- **Proxy dịch Cloudflare Worker** (`cloudflare/worker.js` + `wrangler.toml`,
  chưa deploy — cần tài khoản Cloudflare + API key Google của Joseph, xem
  `cloudflare/README.md`): POST `{text, target}` → Google Cloud Translation
  API v2 bằng key trong secret (không có key trong repo); cache KV theo
  hash(text+target+glossary_version); **hạn mức cứng 500.000 ký tự/tháng**
  (đúng phần miễn phí của Google), hết hạn mức trả `quota_exceeded` để
  client rơi về MyMemory + offline — không bao giờ tự phát sinh phí; giới
  hạn 60 request/phút/IP; ngắt mạch sau 5 lỗi Google liên tiếp; tôn trọng
  Retry-After + backoff. Đã kiểm chứng bằng harness Node với KV + fetch
  giả (`exp/item2_results.md`).
- **Asset nhị phân (texture/âm thanh): chỉ đọc, dùng bản iSH.** Bản web
  xem được tên, kích thước, hex, chuỗi text trong asset, nhưng không giải
  mã texture (DXT/ASTC/ETC2/PVRTC) hay âm thanh (Vorbis/MP3/AAC) để xem
  trước (xem đánh giá ở `exp/item9_results.md`).
- **File đầu vào tối đa 2GB trên web** (bằng bản iSH) — giới hạn vì RAM
  tab trình duyệt. Lưu ý trung thực: file gần 2GB vẫn có thể làm Safari
  trên iPhone văng tab (tab Safari chỉ dùng được khoảng 1–1,5GB RAM);
  với file lớn nên dùng bản iSH cho chắc.
- **Chưa thử trên iPhone thật.** Tốc độ và RAM trên Safari di động chưa đo;
  IPA game thật (200MB, nhiều bundle) chưa thử trên web.
- Font: atlas SDF của TextMeshPro không tái tạo được; gộp glyph chỉ cho
  TrueType glyf (giới hạn gốc của PB3).
- Lần mở trang đầu cần mạng (tải trang + Pyodide ~13MB từ chính trang này,
  không qua CDN). Sau đó xử lý trên máy bạn; riêng tính năng dịch Google
  mới cần mạng (và có thể bị Google giới hạn tùy IP, xem trên).
- IPA đã là ZIP nén sẵn nên nén 5:1 thường không đạt — tool báo kích thước
  thật, không hứa quá.

## Đối chiếu web vs iSH (full port, 2026-11-16)

“Web” = trang này (Pyodide, 100% client-side). “iSH” = PB3 v3.1 chạy trên
iPhone bằng iSH. Kiểm chứng bằng harness Node chạy `bridge.py` trên đúng
bản vendor trong `pb3/` (`exp/verify_parity.mjs`: 14/14 PASS, không dùng
stand-in; xem `exp/parity_results.md` và `exp/full_port_results.md`).

| Chức năng | Web | iSH | Ghi chú |
|---|---|---|---|
| Mod (patch chuỗi Unity) | Có (dry-run mặc định, ghi khi bấm nút) | Có (`--allow-write`) | Web chỉ xuất đơn vị nhỏ nhất đã patch (bundle/.assets), không đóng gói lại IPA; **không có REBUILD_ALL** (cần maturity M4, chưa có) |
| Extract | Có (pipeline 7 bước, checkpoint/resume) | Có | Web chạy tuần tự; đã sửa crash XML thụt đầu dòng (patch01) |
| Dịch (Google + offline) | Có (checkbox Google, mặc định bật) | Có | Web: 1 chuỗi/request, 0,4 s, fallback MyMemory rồi offline khi 429/CORS; có proxy Cloudflare Worker tùy chọn (hạn mức cứng 500.000 ký tự/tháng); dịch Google thật chưa kiểm chứng được từ VM (IP datacenter bị 429) — thử bằng diagnostics.html trên Safari thật |
| Nén (solid LZMA .tar.xz) | Có (preset 6 mặc định) | Có | Vào >2GB từ chối; ra >500MB xóa + báo không nén được; 5:1 thường không đạt vì IPA đã nén sẵn |
| Font 142/142 | Có (Be Vietnam Pro đi kèm, đủ 134 chữ + 8 dấu) | Có | Atlas SDF TextMeshPro không tái tạo được (giới hạn gốc); gộp glyph chỉ cho TrueType glyf |
| Chẩn đoán | Có (inventory + hex + strings) | Có | Chỉ đọc |
| Batch | Có, **pool worker song song thật** | Có, song song được | Web: mỗi worker 1 thread hệ điều hành thật — song song thật (không threading/pthreads); số worker chọn được 1–2 (mặc định 2, tự động 1 khi RAM ≤ 4 GB); watchdog hợp tác 25 giây (dừng an toàn, không terminate); checkpoint từng game vào IndexedDB, resume bỏ qua game đã xong kể cả sau reload; batch ở chế độ pool không ghi vào KB (KB các worker không gộp được) |
| KB | Có (thêm/tra cứu/liệt kê/quên/xuất/**nhập**) | Có (SQLite WAL lâu dài) | Web: **lưu lâu dài qua IndexedDB** (autosave + khôi phục khi tải lại trang); file .sqlite tải về dùng để chuyển máy; profile nhập chỉ là gợi ý (hash chưa đối chiếu); game không có bundle id thì không nhập được |
| Support matrix | Có (sinh từ dữ liệu thật) | Có | Web: cột “ngày test” hiện “chưa có” vì trình duyệt không nạp `tests/corpus/*.json` (file có trong repo, chỉ dùng cho CI); cột “số game đã test” chỉ đếm KB đã lưu trên máy này |
| Plugin list 20 | Có (20/20 [ok] khi boot) | Có | 12 format + 7 engine/detect + zip |
| Test suite | **Chỉ ở CI** (`.github/workflows/test.yml`) | Chạy tay trên iSH | Suite không chạy được trong trình duyệt (cần threading/subprocess/CLI thật). Xem mục CI dưới |
| UnityPy | **Không có** (thiếu wheel `etcpak`) | Có (1.25.4) | Web inspect Unity bằng parser thuần (UnityFS v6–v9, SerializedFile v23) |
| Giới hạn file | Tối đa **2GB** (bằng iSH) | Tối đa 2GB | Web: file gần 2GB vẫn có thể làm Safari trên iPhone văng tab vì RAM (tab chỉ dùng được khoảng 1–1,5GB); file lớn nên dùng bản iSH |
| Lần mở trang đầu | Cần mạng (tải trang + Pyodide ~13MB cùng origin, không CDN) | Không cần (đã cài sẵn) | Sau lần đầu, xử lý hoàn toàn trên máy; riêng dịch Google mới cần mạng |

## CI — test suite trên GitHub Actions

Hai workflow (`.github/workflows/test.yml` và `.github/workflows/pages.yml`,
job test giống hệt nhau) chạy suite PB3 bằng **pytest** trên
`ubuntu-latest` với **Python 3.12** (setup-python không còn 3.9 cho
Ubuntu 26.04; code vẫn tương thích Python 3.9 cho iSH), mỗi lần push lên
`main` và mỗi pull request. Ba job: `unit` (chỉ test không cần fixture
nhị phân: `pytest -q -rs -m "not needs_fixture"`), `assets` (toàn bộ
suite với `STRICT_FIXTURES=1`, fixture lấy từ Release asset), và
`pyodide-node` (smoke qua Pyodide, không gate deploy). Log test được lưu
làm artifact (`pb3-test-unit-log`, `pb3-test-full-log`). Suite chạy hoàn
toàn offline; phụ thuộc ngoài stdlib là `lz4` + `pytest`.

20 fixture nhị phân KHÔNG có trên `main` mà lấy từ Release asset
`fixtures-v1` (`pb3-fixtures-v1.zip`); 8 file fixture nhỏ ĐÃ có trên
`main` (xem đầu file workflow để biết danh sách). Đo thật trong sandbox
ngày 2026-11-17 (bản sao cây head 3d0c80c, đã đối chiếu SHA với main,
Python 3.12.3, pytest 9.2.0, `STRICT_FIXTURES=1`): lệnh của job `unit`
cho 1586 passed, 1 skipped, 132 deselected, exit 0 (lặp lại với pytest
8.4.1 và với bản sao checkout sạch — bỏ `__pycache__`/`logs`/`reports` —
đều xanh).

Trạng thái tại head 3d0c80c (run #35/#46): job `assets` đỏ vì Release
`fixtures-v1` chưa có asset nào (`assets: []`, kiểm chứng qua API ngày
2026-11-17) nên bước tải fixture thất bại — cần tải file
`pb3-fixtures-v1.zip` lên Release thì job này mới chạy được. Job `unit`
cũng đỏ (exit 1) nhưng nguyên nhân [CHƯA KIỂM CHỨNG]: lệnh pytest tái
hiện cục bộ đều xanh (xem trên), cần đọc log từng bước trên giao diện
web GitHub Actions mới kết luận được.

Test ngày (`test_support_matrix.py`) đã sửa đúng: test tự đặt mtime file
corpus trong `setUp` (os.utime về 2026-11-15 12:00 UTC), assertion vẫn
strict — mtime sau checkout không còn là nguyên nhân đỏ.

## Kiểm chứng bản vendor (Pyodide cục bộ, Node)

Chạy `bridge.py` trên đúng bản vendor trong `pb3/` (không dùng cây PB3 gốc)
bằng Pyodide npm — 15/15 mục PASS, 0 FAIL (`exp/verify_site.mjs`,
`exp/verify_results.md`):

> **Đính chính 2026-11-17:** file harness `exp/verify_site.mjs` và kết quả
> `exp/verify_results.md` không có trong cây này; bảng dưới đây chỉ có 13
> mục trong khi tiêu đề nói 15/15 — các con số này không kiểm chứng lại
> được, không dùng làm bằng chứng hiện tại.

| Mục | Kết quả |
|---|---|
| Khởi động (20/20 plugin, không lỗi) | PASS, 1,0 s |
| Font đi kèm đủ 142/142 (134 chữ cái + 8 dấu) | PASS |
| Liệt kê entry `game1.zip` (11 entry) | PASS |
| Unity inspect `resources.assets` (v17, 2019.4.40f1, pid1001 “Xin chao 9 Kings!”) | PASS, 0,3 s |
| Unity inspect bundle `sample.unity3d` | PASS |
| Extract `game1.zip` (21 entry, 6 file, 74 event tiến độ qua 6 bước) | PASS, 0,1 s |
| Dịch offline (`play`→`Chơi`, `quit`→`Thoát`) | PASS |
| Dịch + gắn credit (key `title`) | PASS |
| Nén/giải nén `sample.ipa` (byte-identical, sha256 khớp) | PASS, 0,1 s |
| Quét font + gộp glyph (đường merge chạy được) | PASS |
| Lấy font đi kèm về (132.948 byte) | PASS |
| Chẩn đoán `sample.ipa` (3 entry, hex, strings) | PASS |
| SHA-256 đối chiếu | PASS |

Chưa kiểm chứng: đường tải file bằng `fetch` trong Worker trên trình duyệt
thật và trên iPhone (Safari). Logic Python đã xanh; phần JS tải file + Worker
cần thử bằng trình duyệt sau khi bật Pages.

## Kiểm chứng Pyodide vendor (không CDN, Node)

`exp/verify_vendor.mjs` nạp Pyodide với `indexURL` trỏ vào `pyodide/`
và chặn mọi `fetch` http(s) trong lúc chạy — **16/16 mục PASS, 0 FAIL**,
0 lần fetch http trong suốt quá trình boot (`exp/vendor_results.md`):
khởi động 20/20 plugin, nạp binding `lz4` từ wheel vendor, extract
`game1.zip` (21 entry, 6 file), dịch offline + credit, nén/giải nén
byte-identical. Chứng minh boot không còn chạm cross-origin nào.

> **Đính chính 2026-11-17:** file harness `exp/verify_vendor.mjs` và kết
> quả `exp/vendor_results.md` không có trong cây này nên con số 16/16
> trên không kiểm chứng lại được. Đường dẫn đúng trong cây là `pyodide/`
> (không có thư mục `site/`).

## Kiểm chứng cục bộ (không cần trình duyệt)

Trong `exp/` có các harness Node (`verify_*.mjs`) dùng Pyodide npm để chạy
`bridge.py` trên bản vendor này với fixture thật. Chạy lại harness liên
quan sau mỗi lần sửa `pb3/` hoặc `bridge.py` trước khi đẩy lên GitHub.
(Harness cũ `verify_site.mjs` không còn trong cây.)
