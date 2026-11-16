# HANDOFF - CLAUDE 01
- Trạng thái: COMPLETE (phạm vi Claude 01; xem "CHƯA CHẠY TRÊN iSH" bên dưới)
- Thời điểm cập nhật: 2026-11-14
- Commit/hash hoặc patch: git tag `account-01-complete` (xem `git log -1 account-01-complete`); kèm `pb3_account01.zip`
- Phạm vi được giao: khảo sát, bootstrap, hạ tầng dùng chung, menu + README, test + bàn giao. Không làm: registry/plugin, parser game, ghi ngược.

## Nhiệm vụ nhỏ đã hoàn thành
1. Khảo sát mã cũ -> `docs/INVENTORY.md` (rủi ro, trùng lặp, vi phạm iSH-only). Mã cũ giữ nguyên byte ở `legacy/` (đối chiếu sha256 15/15).
2. Bootstrap: `core/paths.py`, `core/config.py` + `config/default.toml`, `core/ish_compat.py`, `core/app.py`; gốc dự án suy ra từ vị trí mã, không phụ thuộc đường dẫn ngoài.
3. Hạ tầng: `errors`, `logging`, `safe_io`, `limits`, `progress`, `hashing`, `runstate`, `tomlmini`, `file_dialog`.
4. Menu 5 mục đúng nhãn đặc tả 12.1 + CLI theo 12.2 (`menu.py`, `ui/`); mục 1-4 hiển thị "CHƯA TRIỂN KHAI"; mục 5 có sẵn `diagnose --ish` và cách ly tệp tạm. README.txt, CHANGELOG.txt.
5. Test: 155 test, gồm luật kiến trúc iSH-only và tương thích cú pháp Python 3.8.

## File đã tạo
`menu.py`, `core/{__init__,errors,paths,tomlmini,config,limits,hashing,progress,safe_io,runstate,logging,ish_compat,file_dialog,app}.py`,
`ui/{__init__,handlers,screens,cli}.py`, `config/default.toml`, `tests/{__init__,helpers,test_errors,test_paths_safe,test_tomlmini_config,test_logging_limits,test_safe_io,test_progress_env_dialog,test_app_menu_cli,test_rules_ish_only}.py`,
`README.txt`, `CHANGELOG.txt`, `CURRENT_STATE.md`, `.gitignore`, `docs/INVENTORY.md`, `handoff/{account_01,decisions}.md`, `handoff/CURRENT_STATE.md` (trỏ), `legacy/*` (bản sao nguyên byte).
## File đã sửa
Không có (mã cũ chỉ được sao chép, đổi tên 2 tệp: `menu.py`->`legacy/menu_legacy.py`, `pb3 pack v2.py`->`legacy/pb3_pack_v2.py`).
## File không được sửa
`legacy/*` (tham khảo). Không đổi API/hợp đồng `core.errors`, `core.paths`, `core.safe_io`, `ui/handlers.py` mà không cập nhật consumer + test.

## Test đã chạy và kết quả THỰC TẾ
Môi trường: sandbox Linux x86_64, **Python 3.12.3** (KHÔNG phải iSH, KHÔNG phải Python 3.9).
- `python3 -m unittest discover -s tests -t .` -> **PASS 155/155** (~1.2 s). Chạy lại sau lần sửa cuối.
- Phát hiện và sửa 1 lỗi thật nhờ test: `resolve_inside` báo nhầm `E_UNSAFE_PATH` khi thư mục gốc chưa tồn tại (nay có test hồi quy `test_dry_run_creates_nothing`).
- Đo (sandbox, không phải iSH): `menu.py --version` ~65 ms, mở menu+thoát ~68 ms; RAM đỉnh ~20 MB. Chưa đo trên iSH.
- Test mô phỏng thiếu `tomllib` (như Python 3.9): PASS (`test_runs_without_tomllib_like_python39`).

## CHƯA CHẠY TRÊN iSH (NOT RUN ON DEVICE)
- Toàn bộ suite, `python3 menu.py`, `diagnose --ish` trên iSH thật.
- Chạy với Python 3.9 thật (chỉ kiểm ngữ pháp 3.8 bằng `ast` + rà soát API; chưa chạy).
- `--deep` import lz4/UnityPy; tốc độ/RAM/dung lượng trên iPhone; hành vi `os.fsync`, `os.replace`, `os.kill(pid,0)`,
  `/proc/ish`, `shutil.disk_usage`, `scandir` trên filesystem iSH; xuống dòng ở terminal ~48 cột.
Lệnh người dùng chạy trong iSH (sau khi chép vào /root/pb3), rồi gửi ảnh/đầu ra:
```
cd /root/pb3 && python3 --version && python3 menu.py diagnose --ish
python3 -m unittest discover -s tests -t . 2>&1 | tail -5
printf '0\n' | python3 menu.py | head -20
```
Kỳ vọng: `Phát hiện iSH: CÓ`, test PASS; nếu FAIL ghi nguyên văn dòng lỗi để Claude 02/10 xử lý.

## Log/report liên quan
`logs/` (pb3.log, session-*.log) tạo khi chạy; `docs/INVENTORY.md`; `CHANGELOG.txt`.

## Lỗi/hạn chế còn tồn tại
1. Chưa xác minh trên iSH/Python 3.9 (trên).
2. `Progress` dùng `shutil.get_terminal_size` (cột-4); nếu iSH báo sai số cột thì dòng có thể gập - cần quan sát thực tế.
3. `run_marker` dựa pid: sau khi iSH khởi động lại pid có thể trùng -> có thể báo nhầm `other_running` (chỉ cảnh báo).
4. `save_local` không giữ chú thích trong local.toml cũ (đã ghi chú trong docstring).
5. `AtomicWriter`: giữa lúc chọn tên `.vN` và ghi có khe hở đua (hai phiên song song) - đã chấp nhận vì mặc định 1 worker; Claude 09 (batch) cần cân nhắc nếu bật song song.
6. `locales.toml`, `encoding.py`, `textdetect.py` chưa có (xem decisions #13).
7. Chưa có SUPPORT_MATRIX/maturity nào (chưa có plugin). Không có mục nào đạt M1+.

## Quyết định kỹ thuật mới và lý do
Xem `handoff/decisions.md` (13 mục). Nổi bật: Python >=3.8 + `tomlmini` (iSH không có tomllib); test bằng `unittest`;
`workspace/` thay "thư mục làm việc local/"; output không ghi đè (`.v1`, `.v2` trước phần mở rộng); hợp đồng `services/*` trong `ui/handlers.py`.

## Phụ thuộc đã đáp ứng
Không có (tài khoản đầu tiên). Dependency ngoài: **không** (chỉ thư viện chuẩn). UnityPy/lz4: chưa cài, chưa kiểm thử.

## ĐẦU VÀO CHUYỂN CHO CLAUDE 02 (SDK/IR/Plugin API)
Đem sang phiên mới: `PB3_v3.1_iSH_10Claude.txt` + prompt 02 + **toàn bộ cây `/root/pb3`** (zip bàn giao, bỏ `legacy/` nếu muốn nhẹ; vẫn giữ `docs/INVENTORY.md`) + `CURRENT_STATE.md` + `handoff/account_01.md` + `handoff/decisions.md`. Kết quả chạy iSH (nếu đã có).
Claude 02 nên:
1. Xác minh working tree: `python3 -m unittest discover -s tests -t .` phải PASS 155 trước khi sửa.
2. Tạo `sdk/` (types, api, manifest, vfs, testing) - **import `core.errors.ERROR_SPECS` cho `Diagnostic.code`**, không định nghĩa lại mã lỗi; dùng `core.limits.Limits` làm nền cho `ctx.limits`, `core.safe_io`/`core.paths` cho đường dẫn an toàn.
3. Không import ngược: `core/` không được import `sdk/` (có test `test_dependency_rule_core_does_not_import_upper_layers`).
4. Thêm test SDK mới vào `tests/` (cùng kiểu `unittest`); không sửa `tests/test_rules_ish_only.py` trừ khi thêm thư mục mới vào danh sách kiểm (nếu SDK dùng `subprocess`/mạng là SAI).
5. Khi tạo `services/`, tuân hợp đồng ở `ui/handlers.py`; chỉ nâng "maturity" trong bảng đó khi có bằng chứng.
6. Cập nhật `CURRENT_STATE.md`, tạo `handoff/account_02.md`.

## Lệnh/đường dẫn để tiếp tục
```
cd /root/pb3
git status && git log --oneline | head
python3 -m unittest discover -s tests -t . 2>&1 | tail -3
cat CURRENT_STATE.md handoff/decisions.md
```
