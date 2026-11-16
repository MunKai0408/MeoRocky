# Chiến lược test: 5 tầng, chạy theo ngữ cảnh (decisions #58-59)

Mục tiêu: dự án có thể lên tới ~3000 test mà việc chạy hằng ngày vẫn tính bằng giây/phút trên iSH. Nguyên tắc: **không bỏ bất kỳ test nào, chỉ chạy đúng thứ cần chạy đúng lúc.** Bàn giao luôn chạy đủ.

## Năm tầng (tự động xếp, `tests/tiering.py`)
| Tầng | Là gì | Khi nào chạy |
|---|---|---|
| **smoke** | ~60 test then chốt (cờ chọn từ tầng fast, không phân hoạch): khởi động, config, ghi file an toàn, mã lỗi, manifest, IR, nạp plugin | sau MỖI lần sửa, vài giây |
| **fast** | test thuần bộ nhớ / file nhỏ | hằng ngày, `--changed` |
| **ish** | test chỉ có nghĩa trên máy thật: symlink, `/proc`, `/dev/location`, `os.link` | chạy riêng trên iSH để lấy bằng chứng; có trong full |
| **slow** | mở tiến trình con, `copy_project`, `sleep`/timeout thật | khi bàn giao (`--tier full`), hoặc khi chạy cả file của gói |
| **meta** | quét mã nguồn toàn dự án (AST/regex mọi file) | khi bàn giao |

Mỗi test thuộc đúng MỘT tầng phân hoạch (fast/ish/slow/meta; ưu tiên ish > slow > meta > fast); smoke chỉ là cờ và chỉ tính cho test thuộc fast (smoke phải rẻ). Test MỚI tự được xếp tầng, không cần nhớ đánh dấu. Thứ tự quyết định: (1) `tests/tiers.json` (ghi đè tay) -> (2) `@slow`/`@meta`/`@ish`/`@smoke`/`@slow_subprocess` -> (3) dấu hiệu trong mã (gọi subprocess, `copy_project`, `time.sleep`, `read_src`/`ast_nodes`..., kể cả gọi qua hàm phụ hoặc `setUp`) -> (4) **số đo thật trên máy này**: mỗi lần chạy ghi `reports/test_times.json`; test nhanh nào vượt ngân sách (mặc định 1,0 s, `--budget`) tự chuyển sang slow từ lần sau. Số đo trên iSH vì vậy tự điều chỉnh theo máy thật, không phụ thuộc phỏng đoán.

## Bộ nhớ đệm _Facts + nạp chọn lọc (đợt rà soát cuối, hạng mục c)
- **Cache `_Facts` trên đĩa** (`reports/test_facts_cache.json`): phân tích 60 file test bằng regex tốn ~0,5 s mỗi lần chạy trên sandbox (đắt hơn nhiều trên iSH). Cache lưu (tầng, cờ smoke) từng test, khóa bằng sha1 nội dung file + hash luật xếp tầng: file đổi hay luật đổi thì cache tự hết hiệu lực, không bao giờ cho kết quả cũ. Ghi theo kiểu "bẩn rồi xả một lần" cuối lần chạy; mọi lỗi đọc/ghi cache đều nuốt (hỏng cache thì phân tích lại). `pytest_sessionfinish` và cuối `_build` đều xả cache.
- **Nạp chọn lọc**: khi không chỉ định file và không dùng `--changed`, bộ chạy chỉ nạp module chứa test thuộc tầng đang chạy (tính bằng `_Facts`, không import — smoke chỉ nạp 9/60 module). Đếm lại sau khi nạp: lệch khỏi kế hoạch nhẹ (test động, lỗi import...) thì nạp đủ rồi mới chọn — không bao giờ mất test âm thầm. `--changed` vẫn nạp đủ để đếm đúng số test bị ảnh hưởng nằm ngoài tầng đang chạy.
- **Bản sao repo ấm** (`_copy_repo` trong `test_runner.py`): bản sao dùng cho test chạy tiến trình con GIỮ `__pycache__` (Python kiểm `.pyc` bằng mtime+size nguồn nên không sợ bytecode cũ; file nào test sửa trong bản sao thì biên dịch lại) và sao chép cả `test_facts_cache.json`. Trước đây bản sao lạnh phải biên dịch lại mọi module (~4,5 s mỗi lần chạy con).
- **Tiến độ pipe bước từng 1%** (luật #152): khi không phải TTY, bộ chạy in một dòng mỗi khi % nguyên đổi (tối đa ~100 dòng), không nhảy ~4% như trước (in mỗi 50 test). Khi TTY vẫn cập nhật từng test qua `\r`.
- **Watchdog nêu rõ pha treo**: fixture lớp/module chạy trên main thread nên timeout của test không áp dụng được — `ThreadedSuite` ghi pha (`setUpClass`/`setUpModule`/`tearDownClass` + tên test) để luồng watch báo rõ khi treo. Ngưỡng cảnh báo treo 15 s nhân theo hệ số máy (75 s trên iSH) để test khỏe trên iSH không spam cảnh báo oan.
- **Đĩa đầy không giết bộ chạy**: mọi ghi tiến độ/báo cáo qua `_emit`/`_print` đều nuốt lỗi ghi (ENOSPC/pipe vỡ) — suite vẫn chạy hết, mã thoát vẫn theo kết quả test, `reports/failures.txt` vẫn cứu được danh sách lỗi. Bài học 2026-11-16: hai lần full chạy song song làm đầy `/tmp` 512 MB, lần chạy viết log ra `/tmp` chết ở test ~151 vì `write()` tiến độ không được bắt — **không bao giờ chạy hai lần full song song trong cùng một cây**.
- **Không chạy song song test**: bộ test KHÔNG chạy song song (luồng/tiến trình) một cách có chủ ý — nhiều test đổi trạng thái toàn cục dùng chung (biến môi trường, chế độ màu, registry) và test tiến trình con ghi `reports/` trong cây thật; trên iSH chỉ có 1 CPU giả lập + bug `getppid()` trong daemon thread nên song song còn chậm hơn và rủi ro hơn. Tốc độ lấy từ cache, nạp chọn lọc, và bản sao ấm — không từ song song.

## Lệnh
| Việc | Lệnh | Ghi chú |
|---|---|---|
| Sau mỗi lần sửa | `python3 run_tests.py --tier smoke` | vài giây |
| Hằng ngày | `python3 run_tests.py` | chỉ tầng fast |
| Bằng chứng iSH | `python3 run_tests.py --tier ish` | test cần máy thật; ghép được: `--tier fast,ish` |
| Sau khi sửa mã | `python3 run_tests.py --changed` | chỉ test có (gián tiếp) import file đã đổi kể từ lần FULL xanh gần nhất; chưa có mốc thì chạy cả tầng fast |
| Một gói | `python3 run_tests.py tests/test_plugin_toggle.py` | chạy ĐỦ file, kể cả test chậm của file đó |
| Bàn giao | `python3 run_tests.py --tier full` | tất cả; xanh thì lập mốc cho `--changed` |
| Xem kế hoạch | `python3 run_tests.py --plan` | số test mỗi tầng, không chạy |
| Lọc | `-k chuoi` | khớp trong tên test |
| pytest | `python3 -m pytest tests --tier fast` (cũng `smoke`, `ish`, `fast,ish`) / `python3 -m pytest tests` / `-m slow` | mặc định pytest = ĐẦY ĐỦ (không bỏ âm thầm) |

`--changed` theo dõi file `.py/.toml/.json`. Đổi file cấu hình/dữ liệu hoặc `tests/{conftest,tiering}.py`, `tiers.json` thì chọn mọi test. Đổi tài liệu `.md/.txt` không kích hoạt gì (test quét tài liệu thuộc tầng meta, chạy ở bàn giao). `--changed` là công cụ tăng tốc, KHÔNG thay lần chạy full khi bàn giao: import động (plugin nạp bằng đường dẫn) và test chậm chạy cả ứng dụng nằm ngoài đồ thị import (test chậm được coi là phụ thuộc mọi file nguồn).

## Thêm tầng mới (khi bộ test lớn hơn)
Tầng khai báo MỘT chỗ: danh sách `TIERS` trong `tests/tiering.py`. Thêm tầng = (1) thêm 1 dòng vào `TIERS`; (2) nếu muốn tự nhận diện, thêm 1 cặp `(tên, regex)` vào `RULES` theo thứ tự ưu tiên; (3) viết 1 test trong `test_tiering.py`. Bộ chạy, pytest, `--plan` và nhãn tự nhận tầng mới. Ví dụ hợp lý khi cần: tách `slow` theo nguyên nhân (`proc`, `sleep`) hoặc thêm tầng `integration`. Chỉ thêm khi có tiêu chí xếp rõ và đã có test để xếp.

## 5 quy tắc viết test rẻ (để bộ nhanh không phình)
1. Một test nhanh làm việc trong bộ nhớ hoặc vài file nhỏ trong `self.tmp`; không mở tiến trình con.
2. Cần cả dự án trong thư mục tạm: dựng MỘT bản cho cả lớp (`setUpClass`, xem `TestMenuSubprocess`), không `copy_project` cho từng test.
3. Cần kiểm CLI thật: gom nhiều kiểm tra vào một lần chạy tiến trình con (một test, nhiều `assert`), thay vì mỗi assert một tiến trình.
4. Không `sleep` thật để chờ: dùng đồng hồ giả (`Clock` trong `test_overall_keepalive.py`) hoặc cờ hủy; timeout test dùng giá trị nhỏ nhất đủ ổn định.
5. Test quét mã nguồn dùng `read_src`/`ast_nodes`/`imports_of` của `tests/helpers.py` (có đệm) và mỗi quy tắc quét toàn cây một lần, không parse lại trong từng test.

## Chốt chặn
- `tests/test_tiering.py` kiểm cơ chế xếp tầng, chọn theo thay đổi, và buộc **tầng fast >= 70% tổng số test** (co lại là dấu hiệu xếp sai hoặc đang viết test nặng) và **smoke trong khoảng 20-80 test**, đều thuộc fast, chỉ nêu lớp có thật.
- Sau mỗi lần chạy, bộ chạy in các test nhanh vượt ngân sách. Danh sách ở `reports/test_times.json` cho biết test nào đáng tối ưu nhất.
- Khi tầng fast vượt mục tiêu thời gian (đề nghị <= 2 phút trên iSH), tối ưu theo thứ tự: test vượt ngân sách -> lớp có `setUp` nặng -> gộp test CLI -> chia nhỏ `--changed`.

## Trung thực về số liệu
Sandbox Linux (Python 3.12.3), 756 test (sau đợt polish): smoke 59 test; fast 651; ish 11; slow 44; meta 50; full 756 / 29,8 s (755 passed, 1 skipped). iSH: bản 527 test PASS 2026-11-15 (524 passed, 3 skipped, 264,9 s); bản 575 test PASS 2026-11-15 19:20 (572 passed, 3 skipped, 449,0 s; 42 test vượt ngân sách 1.0s). Tiến trình con và file I/O chậm hơn nhiều trên iSH nên tỷ lệ thật được kỳ vọng còn lợi hơn, nhưng chỉ tin số đo thật: chạy `python3 run_tests.py --tier full` một lần trên iSH để có `reports/test_times.json` của máy đó.

Đợt rà soát cuối, hạng mục c (tối ưu bộ test, 2026-11-16, sandbox này — số đo dao động ±5–7 s giữa các lần chạy do CPU dùng chung):
- Trước: full 1395 test / 92,7 s, chậm nhất 4,1 s; fast 1241 test / 29,6 s; smoke 59 test / 1,13 s; `_build` (nạp + xếp tầng) 1,08 s.
- Sau: full 1536 test / 86,5 s, chậm nhất 4,3 s (cùng test `TestPrintSpeed`, mã sản phẩm); fast 1371 test / 29,7 s; smoke 59 test / 0,71–0,98 s; `_build` ~0,3 s (smoke), `plan_modules` ấm 0,02 s (lạnh 0,74 s); chạy con `--changed` trong bản sao 7,1 s → 3,3 s.
- Full sau nhanh hơn tuyệt đối dù thêm 141 test (các nhóm khác thêm trong lúc rà soát): 66,5 ms/test → 56,3 ms/test. Lần full sau có 2 lỗi ngoài phạm vi nhóm test: (1) `test_unimplemented_shows_clear_status...` fail vì menu [5][3] đã được triển khai (test cũ còn đòi "CHƯA TRIỂN KHAI"); (2) `TestUnrealCli.setUpClass` lỗi ENOSPC thoáng qua do một suite khác chạy song song trên /tmp. Vì fail nên lần full đó không lập mốc (đúng luật) — cần sửa test menu rồi chạy lại full xanh ở bước tích hợp.
- Chậm nhất toàn suite vẫn là `TestPrintSpeed::test_print_plan_matches_old_wrap` (~4 s, `test_optimize.py`) — mã sản phẩm, nhóm tối ưu xử lý, không thuộc hạng mục test.
