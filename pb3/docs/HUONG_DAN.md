# HƯỚNG DẪN SỬ DỤNG — PB3 v3.1 (BearTea)

Công cụ mod / trích xuất ngôn ngữ cho game iOS, chạy 100% trong iSH trên iPhone.
Không server, không cloud, không tải mã khi chạy. Tên hiển thị trong menu là
**BearTea**; tên project, đường dẫn và tên file bên trong vẫn là PB3.

> **Mức hỗ trợ chỉ đúng với những gì đã kiểm thử.** Xem `docs/SUPPORT_MATRIX.md`
> (sinh tự động): danh sách đã xác minh trên game thật / mới test fixture /
> chưa hỗ trợ. Không có mục nào đạt M4.

## 1. Chạy

```sh
cd /root/pb3
python3 menu.py                  # menu tương tác (gõ số, 0 để thoát)
python3 menu.py --help           # danh sách lệnh
python3 menu.py -V               # xem phiên bản
```

Tuỳ chọn chung (đặt trước tên lệnh): `--debug` (giữ thư mục tạm, log DEBUG),
`--keepalive` (giữ iSH chạy nền qua vị trí — cần cấp quyền vị trí cho iSH).

## 2. Menu tương tác

| Mục | Chức năng |
|---|---|
| [1] Mod và ghi ngược (đa engine, Unity hạng nhất) | UnityFS v6/v7/v8 + SerializedFile v15–26, patch TextAsset. Mặc định chỉ dry-run; ghi thật cần xác nhận. |
| [2] Trích xuất ngôn ngữ (đa engine) | Discovery + nhận diện engine/locale + export JSON key-value + ghi ngược; có dịch tự động. |
| [3] Quản lý plugin và ma trận hỗ trợ | Gõ số để bật/tắt plugin; `[m]` xem ma trận hỗ trợ; `[k]` quên tri thức game. Tạo mới / kiểm tra plugin bằng lệnh CLI `plugin new/test`. |
| [4] Xử lý hàng loạt | Chạy cùng một việc trên nhiều game theo manifest CSV/JSON (xem §4). |
| [5] Chẩn đoán và điều tra dữ liệu | Môi trường iSH, cách ly tệp tạm dở dang, inventory/hex/strings. |
| [0] Thoát | |

## 3. Lệnh CLI

```sh
python3 menu.py extract <path> [--locale en] [--out DIR] [--dry-run]
python3 menu.py translate <file.json>            # dịch tự động bảng JSON đã trích xuất
python3 menu.py mod <path> [--file <entry>] [--diff] [--allow-write]
python3 menu.py batch <manifest> [--locale L] [--jobs N] [--resume] [--dry-run]
                      [--limit N] [--only PATTERN] [--timeout-game GIÂY]
                      [--out DIR] [--allow-write]
python3 menu.py plugin list
python3 menu.py plugin test <id>
python3 menu.py plugin new <kind> <id> [--write]
python3 menu.py plugin enable <id> | plugin disable <id>
python3 menu.py container list <file>
python3 menu.py container read <file> <entry> --out <dest>
python3 menu.py unity inspect <file>
python3 menu.py unity read <file> [entry] --out <dest>
python3 menu.py unity patch <file> [entry] --object <id> --old <s> --new <s> [--allow-write]
python3 menu.py support [--write]                # in ma trận hỗ trợ; --write ghi docs/SUPPORT_MATRIX.md
python3 menu.py kb export <game> [--out FILE]    # xuất profile game ra JSON
python3 menu.py kb import <file.json>            # nhập profile game vào KB
python3 menu.py kb forget <game>                # quên tri thức game
python3 menu.py kb prune                         # dọn cache KB (giới hạn 64 MB)
python3 menu.py diagnose [path] [--verbose] [--ish] [--deep]
```

Mã thoát: 0 ok | 1 lỗi người dùng | 2 chưa hỗ trợ/chưa triển khai |
3 an toàn/từ chối ghi | 4 lỗi nội bộ.

Quy tắc ghi ngược: mặc định chỉ dry-run (không ghi file); thêm `--allow-write`
mới ghi thật. Đầu ra là đơn vị nhỏ nhất có thể thay thế (chỉ bundle/assets,
không bao giờ đóng gói lại toàn bộ IPA); không ghi đè nguồn (output versioned
`.v1`, `.v2`…); không ghi đè file đích đã có.

**Cảnh báo chữ ký:** file đã sửa cần được đóng gói và ký lại bằng công cụ của
bạn (ví dụ dùng tài khoản dev của bạn). PB3 không ký lại. PB3 không hướng dẫn
vượt qua bảo vệ bản quyền; chỉ mô tả kỹ thuật file.

## 4. Xử lý hàng loạt (menu [4] / lệnh `batch`)

Đầu vào là manifest CSV hoặc JSON: danh sách đường dẫn game, locale cần lấy,
chế độ (`extract` | `mod`), tuỳ chọn ghi đè profile. Chạy tuần tự hoặc song
song (`--jobs N`), mỗi game cô lập — lỗi một game không dừng cả lô. Checkpoint
sau mỗi game; `--resume` chỉ làm phần chưa xong. `--dry-run` chạy thử không ghi.

Tiếp tục lần chạy dở: `--resume` không kèm `--out` thì tự tìm lần chạy cũ
gần nhất có checkpoint khớp manifest (in rõ "tiếp tục từ: ..." kèm thư mục);
không tìm thấy thì chạy mới và báo rõ. `--out DIR` chỉ rõ thư mục output
(dùng cùng `--resume` để tiếp tục đúng lần chạy cũ).

```sh
python3 menu.py batch batch.csv --locale en --jobs 2 --resume --dry-run
python3 menu.py batch batch.json --limit 10 --only "game1" --timeout-game 300
python3 menu.py batch batch.csv --resume --out workspace/output/_batch/20261116-030000
```

Ví dụ manifest CSV (`batch.csv`):

```csv
path,locale,mode
workspace/input/game1.ipa,en,extract
workspace/input/game2.ipa,zh,extract
workspace/input/game3.ipa,en,mod
```

Ví dụ manifest JSON (`batch.json`):

```json
[
  {"path": "workspace/input/game1.ipa", "locale": "en", "mode": "extract"},
  {"path": "workspace/input/game2.ipa", "locale": "zh", "mode": "extract"}
]
```

```sh
python3 menu.py batch batch.csv --locale en --jobs 2 --resume --dry-run
python3 menu.py batch batch.json --limit 10 --only "game1" --timeout-game 300
```

Đầu ra: `workspace/output/_batch/<timestamp>/` gồm kết quả từng game +
`index.csv` + `summary.json` + `failures.txt` (kèm mã lỗi). Không bao giờ đóng
gói lại IPA trong batch; chỉ xuất đơn vị tối thiểu.

## 5. Kho tri thức game (KB)

Game đã quét một lần thì lần sau nhận diện lại gần như tức thời nhờ profile
lưu trong KB (`kb/pb3_kb.sqlite`, chỉ trên máy bạn, không đồng bộ cloud).
Profile chỉ là gợi ý (fingerprint, inventory, lựa chọn plugin) — pipeline vẫn
chạy probe; file đổi hash thì KB không tái dùng, dò lại từ đầu. Batch ghi
profile sau mỗi lần trích/mod thành công, và lần sau dùng preset từ KB (file
+ locale đã chọn, in rõ "dùng tri thức từ ..."); lựa chọn file chỉ áp dụng
khi locale của file khớp locale của job. Profile chỉ ghi nội dung khi bạn
xuất/import chủ động; KB không lưu nội dung dịch.

```sh
python3 menu.py kb export com.example.game            # in profile JSON ra stdout
python3 menu.py kb export com.example.game --out game.json
python3 menu.py kb import game.json                   # nhập profile (kiểm tra hash, không tin tuyệt đối)
python3 menu.py kb forget com.example.game            # quên tri thức game đó
python3 menu.py kb prune                              # dọn cache KB (giới hạn 64 MB)
```

KB trống (chưa có game nào) thì các lệnh báo rõ "KB trống", không crash.
Quên tri thức cũng có trong menu [3]: gõ `[k]` rồi gõ bundle id.

## 6. Thanh tiến độ (luật bắt buộc, decisions #152–153)

Mọi thao tác dài đều có thanh tiến độ theo % THẬT + thời gian còn lại (ETA)
tính từ % đã xong và thời gian thực đã chạy. Tiến độ tăng dần từng 1%
(1% → 2% → 3%…), không nhảy cục (không 60% → 80% trong một bước), không báo
láo tiến độ. Thanh có dạng: vạch + % + số lượng đã xong/tổng + ETA, gọn một
dòng, vừa màn hình điện thoại 30–44 cột, màu ANSI vừa phải, chỉ bật khi chạy
trong terminal thật (tự tắt khi chuyển hướng ra file/pipe hoặc `NO_COLOR`).

Ép bật/tắt: `PB3_PROGRESS=1` / `PB3_PROGRESS=0`, hoặc
`progress.overall = false` trong `config/local.toml`.

## 7. Khắc phục lỗi

| Mã / triệu chứng | Ý nghĩa và cách xử lý |
|---|---|
| `E_INPUT_NOT_FOUND` | Không có file/game/plugin đó. Kiểm tra đường dẫn, bundle id, hoặc chạy `plugin list` để xem id. |
| `E_UNSUPPORTED_FORMAT` | Định dạng chưa hỗ trợ (vd UnityFS version lạ, 7z, PAK nén). Xem `docs/SUPPORT_MATRIX.md` mục "Chưa hỗ trợ"; cần plugin mới cho định dạng đó. Có thể thử extractor `generic_strings` để dò chuỗi thô (chỉ đọc chuỗi, không phải bảng localization đã xác minh). |
| `E_ENCRYPTED` | File/entry mã hóa (bundle Unity mã hóa, binary FairPlay). PB3 không giải mã; dừng an toàn. |
| `E_NOT_IMPLEMENTED` | Chức năng chưa triển khai; không giả lập kết quả. Xem `CURRENT_STATE.md`. |
| `E_ARCHIVE_BOMB` | Archive vượt giới hạn (quá nhiều entry / giải nén quá lớn / container lồng quá sâu). Kiểm tra file có phải archive bomb không; giới hạn xem `core/limits.py`. |
| `E_TOO_LARGE` | File/entry vượt giới hạn bộ nhớ iSH. Chia nhỏ đầu vào hoặc tăng giới hạn trong `config/local.toml` (cẩn thận RAM). |
| `E_OUTPUT_EXISTS` | File đích đã có; PB3 không ghi đè. Đổi tên đích hoặc để PB3 tự tạo `.v1`, `.v2`… |
| `E_WRITE_DISABLED` | Ghi bị cấm (plugin chỉ đọc, hoặc `REBUILD_ALL` cần maturity M4). Bỏ `--allow-write` để dry-run, hoặc dùng chế độ ghi được phép. |
| `E_VERIFY_FAILED` | Ghi ngược không vượt qua kiểm tra (verify): file cuối KHÔNG được ghi; bản ứng viên giữ trong thư mục tạm (`patch-failed.bin`) để điều tra. |
| `E_UNSAFE_PATH` | Đường dẫn không an toàn (`..`, tuyệt đối, symlink thoát ra ngoài). Dùng đường dẫn tương đối trong dự án. |
| `E_NO_SPACE` | Hết dung lượng. Dọn `workspace/temp/`, `workspace/cache/`, `logs/` cũ; chạy `kb prune` để dọn cache KB. |
| `E_CANCELLED` | Đã hủy bằng Ctrl-C. Lần chạy sau phát hiện và xử lý output/checkpoint chưa hoàn tất; tệp tạm dở dang được cách ly (không tự xóa). |
| `E_INTERNAL` | Lỗi nội bộ (không có traceback trên màn hình). Chi tiết kỹ thuật nằm trong `logs/session-*.log` — gửi log đó khi báo lỗi. |
| Menu / lệnh treo lâu | iSH chậm, không phải treo: xem tiến độ (mục §6). Inspect IPA lớn lần đầu có thể mất nhiều phút; lần sau dùng cache plan sẽ nhanh hơn. Đừng tắt iSH giữa chừng khi suite đang chạy. |
| `plugin list` hiện `M4→M2` | Plugin khai M4 nhưng thiếu bằng chứng `tests/corpus/<id>.json` nên hiệu lực bị hạ xuống M2 (decisions #51). Thêm bằng chứng đúng dạng thì tự lên lại. |

Lỗi nào cũng có thông báo tiếng Việt + mã lỗi; traceback chỉ nằm trong
`logs/session-*.log`, không hiện làm nội dung chính.

## 8. Giới hạn iSH (đọc trước khi chạy việc lớn)

- **RAM:** iPhone có RAM hạn chế (mỗi worker batch ≤ 800 MB; không nạp file
  > 512 MB vào bộ nhớ — dùng streaming/slice). File IPA lớn (2–3 GB) chỉ
  đọc từng entry, không giải nén toàn bộ.
- **Tốc độ:** CPU iSH giả lập chậm hơn máy tính nhiều lần; full suite test
  mất hàng trăm giây, inspect IPA lớn lần đầu mất nhiều phút. Dùng cache
  plan/KB để lần sau nhanh hơn.
- **Chạy nền:** iOS có thể đóng iSH khi chuyển app; bật `--keepalive`
  (cần quyền vị trí) để giữ chạy nền, nhưng không đảm bảo iOS giữ nền.
  Để suite đang chạy xong hẳn rồi mới thoát app.
- **Chữ ký:** sửa bất kỳ file trong `Payload/*.app` sẽ làm hỏng chữ ký;
  PB3 không ký lại (xem §3).
- **Dữ liệu không có trong gói:** asset tải từ server (ODR, Addressables
  remote) thì IPA không chứa dữ liệu — PB3 báo rõ "dữ liệu không có trong
  gói", không bịa dữ liệu.
- **Chuỗi động:** chuỗi đến từ server/remote config hoặc sinh trong code
  (ghép chuỗi, format động) thì không trích được toàn bộ; PB3 báo rõ giới
  hạn và những gì tìm được.
- **Không tải mã khi chạy:** mọi xử lý cục bộ trong iSH, trừ dịch tự động
  (`sdk/translate.py` gọi endpoint Google Translate miễn phí — file duy
  nhất được dùng mạng, cần mạng, không cần key; có từ điển offline dự phòng).

## 9. Chạy test

```sh
python3 run_tests.py --tier smoke     # vài giây, test then chốt (sau mỗi lần sửa)
python3 run_tests.py                  # hằng ngày: chỉ bộ NHANH
python3 run_tests.py --tier ish       # test chỉ có nghĩa trên máy thật
python3 run_tests.py tests/test_X.py  # một gói: chạy ĐỦ file test của gói
python3 run_tests.py --tier full      # BÀN GIAO: tất cả (xanh thì lập mốc cho --changed)
python3 run_tests.py --changed        # chỉ test bị ảnh hưởng bởi file đã đổi
```

Chi tiết: `docs/TEST_STRATEGY.md`. Test chạy ở môi trường nào thì chỉ là
bằng chứng cho môi trường đó; kết quả sandbox không thay bằng chứng iSH.

## 10. Nghiệm thu (Claude 10)

Bảng nghiệm thu 13 tiêu chí (prompt 10 §4) ở `handoff/account_10.md` §4 —
mỗi tiêu chí PASS/FAIL/NOT RUN kèm bằng chứng. Trạng thái hiện tại:
**12/13 PASS, tiêu chí 1 NOT RUN ON DEVICE → RELEASE BLOCKED**, chờ chạy
checklist iSH ở `handoff/account_10.md` §5 trên zip cuối. Không đánh dấu
RELEASE READY khi còn tiêu chí chưa PASS.
