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
     (parser thuần của PB3, UnityFS v8, SerializedFile v23).
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
   - **Batch**: chạy hàng loạt theo manifest JSON/CSV (menu [4] bản iSH)
     — **chỉ chạy tuần tự** (không có threading trên web, luôn jobs=1);
     dry-run trước, checkpoint/resume (checkpoint lưu vào trình duyệt,
     tải về `batch.zip` gồm summary + checkpoint + từng game).
   - **KB**: kho tri thức game (menu [3] bản iSH) — thêm/tra cứu/liệt kê/
     quên game, tải file KB (.sqlite) về, nhập lại file KB đã tải về bằng
     nút “Nhập KB (.sqlite)”. **Chỉ sống trong phiên**: tải lại trang là
     mất, trừ khi tải file KB về rồi nhập lại trong phiên mới. Game không
     có bundle id thì không nhập được (bị bỏ qua, có cảnh báo).
   - **Hỗ trợ**: ma trận hỗ trợ sinh từ dữ liệu thật của PB3 (registry,
     sdk.containers, sdk.unity, KB của phiên) — không hardcode; cột “ngày
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
  menu chữ. Trình duyệt chỉ nạp 87 file runtime trong `pb3/files.json`;
  `pb3/tests/` (kể cả `tests/corpus/*.json`) có trong repo nhưng KHÔNG nạp
  vào trình duyệt — tests chỉ chạy ở CI (xem mục CI), corpus chỉ là bằng
  chứng ngày test cho ma trận hỗ trợ.
- `pb3/bridge.py`: thêm `mod_patch` (sdk.unity.apply_patch),
  `batch_run` (sdk.batch.BatchRunner, jobs=1 tuần tự),
  `kb_add`/`kb_query`/`kb_list`/`kb_forget`/`kb_export`/`kb_import`
  (sdk.kb.KB, SQLite trong phiên tại /tmp/kb; `kb_import` mở bản sao
  của file .sqlite tải lên rồi nhập từng profile bằng
  `export_profile`/`import_profile`), `support_matrix`
  (sdk.support_matrix.generate với rows từ registry thật).
- `fonts/`: font Be Vietnam Pro (SIL OFL 1.1, xem `fonts/OFL.txt`) lưu
  dạng base64 text để đẩy lên GitHub bằng API text-only; trình duyệt
  giải mã lại thành file .ttf khi chạy.
- Pyodide **v314.0.8** (Python 3.14.2 trong Pyodide) được **vendor ngay
  trong repo** (thư mục `pyodide/`: `pyodide.js`, `pyodide.asm.mjs`,
  `pyodide.asm.wasm`, `pyodide-lock.json`, `python_stdlib.zip`, kèm wheel
  `lz4`) — tải cùng origin với trang, **không qua CDN**. Lý do: Safari
  trên iPhone chặn `fetch` cross-origin trong Worker (lỗi
  “Network response is CORS-cross-origin” khi tải Pyodide từ
  `cdn.jsdelivr.net`), nên bản web v1.1 bỏ hẳn CDN.

## Kết quả thử nghiệm (đo thật — giai đoạn 1, Node.js trên VM)

Xem đầy đủ ở `../exp/phase1_results.md` (không đẩy lên repo này).

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
- **Batch chỉ chạy tuần tự** (jobs=1): Pyodide trên GitHub Pages không có
  threading nên không có chế độ song song như bản iSH (jobs&gt;1). Extract
  cũng chạy tuần tự. Tiến độ báo thật từng game (xong game nào bước 1 mốc).
- **KB chỉ sống trong phiên**: file SQLite nằm trong RAM của Worker
  (/tmp/kb) — tải lại trang là mất hết, trừ khi tải file KB về bằng nút
  “Tải KB về (.sqlite)” rồi nhập lại bằng nút “Nhập KB (.sqlite)” trong
  phiên mới. Profile nhập vào chỉ là gợi ý (hash chưa đối chiếu với file
  game); game không có bundle id thì không nhập được.
- **Mod**: mặc định dry-run (không ghi file nào); chỉ ghi khi bấm nút ghi
  rõ ràng (tương đương --allow-write), file gốc không bao giờ bị ghi đè.
  Output là đơn vị nhỏ nhất đã patch (bundle/.assets) — **không đóng gói
  lại IPA** (giới hạn 6.6 của PB3). REBUILD_ALL không hỗ trợ trên web
  (cần maturity M4, chưa có).
- **Ma trận hỗ trợ**: bảng plugin/container/Unity lấy từ dữ liệu thật của
  PB3 (registry + sdk), nhưng cột “ngày test” hiện “chưa có” vì bản web
  không vendor tests/corpus/*.json; cột “số game đã test” chỉ đếm KB của
  phiên (tải lại trang là về 0).
- **Dịch Google trên web** (`gt.js` + `bridge.translate_json_google`):
  Python async gọi `fetch()` của JS (endpoint gtx miễn phí, không key),
  1 chuỗi/request, cách 0,4 giây, thử lại tối đa 3 lần với backoff khi
  429/5xx; lỗi nào (mất mạng, CORS, 429 hết retry, trang Sorry) cũng thử
  MyMemory rồi rơi về từ điển offline và báo rõ trong kết quả
  (`translated_by`, `network_error`); nút Hủy dừng giữa chừng. **Lưu ý
  trung thực:** từ IP máy chủ phát triển, Google trả HTTP 429 + trang
  Sorry (IP datacenter bị giới hạn) nên dịch Google thật chưa kiểm chứng
  được từ VM này (`exp/translate_web_results.md` ghi rõ); IP mạng di động/
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
| Batch | Có, **chỉ tuần tự** (jobs=1) | Có, song song được | Web không có threading; **không có timeout từng game**; checkpoint lưu vào trình duyệt, resume bỏ qua game đã xong |
| KB | Có (thêm/tra cứu/liệt kê/quên/xuất/**nhập**) | Có (SQLite WAL lâu dài) | Web: **chỉ trong phiên** — tải lại trang là mất, phải nhập lại file .sqlite đã tải về; profile nhập chỉ là gợi ý (hash chưa đối chiếu); game không có bundle id thì không nhập được |
| Support matrix | Có (sinh từ dữ liệu thật) | Có | Web: cột “ngày test” hiện “chưa có” vì trình duyệt không nạp `tests/corpus/*.json` (file có trong repo, chỉ dùng cho CI); cột “số game đã test” chỉ đếm KB của phiên |
| Plugin list 20 | Có (20/20 [ok] khi boot) | Có | 12 format + 7 engine/detect + zip |
| Test suite | **Chỉ ở CI** (`.github/workflows/test.yml`) | Chạy tay trên iSH | Suite không chạy được trong trình duyệt (cần threading/subprocess/CLI thật). Xem mục CI dưới |
| UnityPy | **Không có** (thiếu wheel `etcpak`) | Có (1.25.4) | Web inspect Unity bằng parser thuần (UnityFS v8, SerializedFile v23) |
| Giới hạn file | Tối đa **2GB** (bằng iSH) | Tối đa 2GB | Web: file gần 2GB vẫn có thể làm Safari trên iPhone văng tab vì RAM (tab chỉ dùng được khoảng 1–1,5GB); file lớn nên dùng bản iSH |
| Lần mở trang đầu | Cần mạng (tải trang + Pyodide ~13MB cùng origin, không CDN) | Không cần (đã cài sẵn) | Sau lần đầu, xử lý hoàn toàn trên máy; riêng dịch Google mới cần mạng |

## CI — test suite trên GitHub Actions

`.github/workflows/test.yml` chạy **toàn bộ suite PB3 (1562 test,
`python3 run_tests.py --tier full`)** mỗi lần push lên `main` và mỗi pull
request, trên `ubuntu-latest` với **Python 3.9** (đúng phiên bản iSH; suite
cũng xanh trên 3.12 trong sandbox). Log test được lưu làm artifact
(`pb3-test-log`). Suite chạy hoàn toàn offline; phụ thuộc duy nhất ngoài
stdlib là `lz4`.

**CI chưa xanh hoàn toàn — và đó là đúng, không phải PASS giả.** 28 file
nhị phân/UTF-16 chưa lên `main` (connector GitHub chỉ nhận file text UTF-8;
xem đầu file workflow để biết danh sách): `pb3/kb/pb3_kb.sqlite`,
24 fixture nhị phân và 3 file UTF-16 trong `pb3/tests/fixtures/`. Đo thật
trong sandbox (thiếu 28 file này): 1562 test thì đạt 1386, còn 117 lỗi +
58 fail — trong đó 174 do thiếu fixture (105 mở trực tiếp + 69 lỗi kéo
theo) và 1 do test nhạy mtime (xem dưới). Khi đủ 28 file, CI đạt
1560/1562.

1 test sẽ đỏ ngay cả khi đủ fixture:
`test_support_matrix.py::test_effective_maturity_uses_corpus_not_declared`
hardcode ngày test `2026-11-15` lấy từ mtime của
`tests/corpus/json_text.json`, mà git checkout đặt lại mtime thành ngày
checkout. Sửa đúng là test tự đặt mtime trong `setUp` — để dành cho đợt
vá sau, không sửa test cho vừa CI.

## Kiểm chứng bản vendor (Pyodide cục bộ, Node)

Chạy `bridge.py` trên đúng bản vendor trong `pb3/` (không dùng cây PB3 gốc)
bằng Pyodide npm — 15/15 mục PASS, 0 FAIL (`exp/verify_site.mjs`,
`exp/verify_results.md`):

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

`exp/verify_vendor.mjs` nạp Pyodide với `indexURL` trỏ vào `site/pyodide/`
và chặn mọi `fetch` http(s) trong lúc chạy — **16/16 mục PASS, 0 FAIL**,
0 lần fetch http trong suốt quá trình boot (`exp/vendor_results.md`):
khởi động 20/20 plugin, nạp binding `lz4` từ wheel vendor, extract
`game1.zip` (21 entry, 6 file), dịch offline + credit, nén/giải nén
byte-identical. Chứng minh boot không còn chạm cross-origin nào.

## Kiểm chứng cục bộ (không cần trình duyệt)

Trong `exp/` có harness Node dùng Pyodide npm để chạy `bridge.py` trên
bản vendor này với fixture thật (`verify_site.mjs` → `verify_results.md`).
Chạy lại sau mỗi lần sửa `pb3/` hoặc `bridge.py` trước khi đẩy lên GitHub.
