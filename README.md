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
     thì tự dùng từ điển offline và báo rõ lý do; chỉ dịch VALUE, giữ
     KEY; có gắn credit “Việt Hoá Bởi Bear” nếu bật; nút Hủy dừng giữa
     chừng và giữ phần đã dịch.
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
- `pb3/`: mã nguồn PB3 được vendor (core, sdk, registry, plugins, config)
  — bản web chỉ gọi API tầng sdk qua `pb3/bridge.py`, không dùng menu chữ.
  Không vendor: batch, KB, support matrix, test suite (không có trên web).
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
- **Không chạy được tính năng cần threading** (batch song song). Extract
  chạy tuần tự.
- **Dịch Google trên web** (`gt.js` + `bridge.translate_json_google`):
  Python async gọi `fetch()` của JS (endpoint gtx miễn phí, không key),
  1 chuỗi/request, cách 0,4 giây, thử lại tối đa 3 lần với backoff khi
  429/5xx; lỗi nào (mất mạng, CORS, 429 hết retry, trang Sorry) cũng rơi
  về từ điển offline và báo rõ trong kết quả (`translated_by`,
  `network_error`); nút Hủy dừng giữa chừng. **Lưu ý trung thực:** từ IP
  máy chủ phát triển, Google trả HTTP 429 + trang Sorry (IP datacenter bị
  giới hạn) nên dịch Google thật chưa kiểm chứng được từ VM này
  (`exp/translate_web_results.md` ghi rõ); IP mạng di động/dân cư thường
  dịch được, và CORS trên Safari thật vẫn cần thử bằng tay.
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
