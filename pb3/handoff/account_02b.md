# Bàn giao Claude 02b -> Claude 03

- Trạng thái: **COMPLETE** trong phạm vi 02b (sandbox PASS 240/240; iSH: xem bảng bằng chứng).
- Ngày: 2026-11-14. Căn cứ: `SDK_VS_SPEC.md` (12 lệch, #1-#11 đã sửa) + đặc tả `PB3_v3.1_iSH_10Claude.txt` mục 3, 4, 5.1, 10.1, Phụ lục A-D.
- Đây là yêu cầu thay đổi có ghi nhận theo mục 16; chi tiết quyết định: `handoff/decisions.md` #23-30. Hợp đồng mới: `docs/SDK_CONTRACT.md` (thay bản 1.0).

## Đã làm
| # lệch | Kết quả |
|---|---|
| 1 | `sdk/api.py`: `api_version = 3` (int; tối thiểu 3). Manifest `api_version` phải là int. `from sdk.api import Plugin, api_version` chạy được |
| 2 | `Plugin`: `detect/list_candidates/probe/parse/build/verify/selftest` đúng chữ ký 4.2; bỏ `do_*` |
| 3 | `sdk/types.py`: `SourceRef, LocEntry, LocTable, Candidate` đủ trường 3.1-3.3 |
| 4 | `Evidence(kind,value,weight,reason)` + `confidence()` (tổng, chặn 1.0) ở `core/model.py` |
| 5 | `Diagnostic(code, severity, message_vi, detail, plugin_id, where: SourceRef)` |
| 6 | `SupportLevel` M0..M4 (`core/model.py`); `Project` node dùng M-level, mặc định M0 |
| 7-8 | Manifest Phụ lục A đầy đủ (13 bắt buộc + 6 tùy chọn), `kind`, capabilities `read/write/container/hint`, ràng buộc chéo, `ignored_fields` |
| 9 | `PluginContext`: `vfs, limits, locale, order, log/warn/diag, tempdir, dry_run, kb_read/kb_write, settings`; `for_plugin(manifest)` hạ limits |
| 10 | VFS: `iter_entries/stat/read/slice/open_stream`, path ảo `a::b`, `LimitedVFS`, `MemoryVFS` |
| 11 | Không còn API ghi cho plugin; `build()` trả bytes |
Giữ nguyên: `core/model.py` (đường dẫn an toàn, `E_CORRUPT`, `to_dict/from_dict`, `Project.validate`), phong bì IR (nâng lên 2.0), `core/errors.py`, `core/limits.py`.

## File đã tạo/sửa
- Mới: `sdk/api.py`, `tests/test_appendix_b.py`, `tests/appendix_b_plugin.py` (Phụ lục B chép nguyên văn), `handoff/account_02b.md`.
- Viết lại: `sdk/types.py`, `sdk/manifest.py`, `sdk/vfs.py`, `sdk/ir.py`, `sdk/example_kv.py`, `docs/SDK_CONTRACT.md`, `tests/test_sdk.py`, `tests/test_ir.py`.
- Sửa: `core/model.py` (Evidence, SupportLevel, Locale; bỏ `TextEntry/ExtractionResult`), `tests/test_model.py`, `handoff/decisions.md`, `CHANGELOG.txt`, `CURRENT_STATE.md`.
- Xóa: `sdk/plugin.py`.

## Bằng chứng
| Hạng mục | Môi trường | Kết quả |
|---|---|---|
| Suite đầy đủ `python3 run_tests.py`: 240 test | sandbox Python 3.13 | **PASS** (OK, skipped=0) |
| Trước khi sửa (baseline) | sandbox Python 3.13 | PASS 216 |
| Phụ lục B nguyên văn (import, list_candidates, probe, parse, build bị chặn) | sandbox | **PASS** |
| Luật cú pháp Python 3.8/không API stdlib mới, không mạng/subprocess, `core/` không import `sdk/` (gồm cả `sdk/`) | sandbox | **PASS** |
| Suite 240 test trên iSH (Python 3.9.16, pytest) | iSH | Lần 1: 236 passed, 3 skipped, 1 failed (test heartbeat, flaky do cách kiểm) -> đã sửa. Sau sửa: **237 passed, 3 skipped, 0 failed, 204.32 s** (có log) |

Lệnh người dùng chạy trong iSH sau khi chép mã: `cd /root/pb3 && python3 -m unittest discover -s tests -t . > /root/t.log 2>&1; tail -4 /root/t.log` (kỳ vọng `Ran 240 tests`, `OK`).

## Hạn chế / còn tồn tại
- `sdk/testing.py` (conformance kit) chưa có — thuộc Claude 03 (mục 16, nhiệm vụ 4). Quy ước: `selftest/verify` mặc định `ok=None` = chưa kiểm, không phải đạt.
- Không có timeout/guard socket-subprocess/loader/registry: Claude 03. SDK chỉ cấp limits đã hạ + VFS chỉ đọc.
- `core/model.py` còn `InnerPath` cho `Project/Asset`, còn VFS dùng chuỗi `a::b`; Claude 04 chuyển đổi khi cần.
- Hook `open_container/hints` là bổ sung tối thiểu (decisions #28), có thể chỉnh.
- Manifest không tự chứng minh bằng chứng M3/M4 (cần `tests/corpus/`): loader/SUPPORT_MATRIX phải kiểm.
- Chưa có `config/locales.toml` + nạp `Locale` từ file (Claude 06/07); `ctx.locale` hiện chỉ có `code` và `mapping`.
- Dữ liệu IR 1.0 cũ không đọc được (đã chấp nhận: chưa có dữ liệu thật nào dùng).

## Việc kế tiếp (Claude 03)
1. Đọc `docs/SDK_CONTRACT.md`, `decisions.md` #23-30. Chạy suite trên iSH, xác nhận 240 test.
2. `registry/loader.py`: nạp `plugins/` + `plugins_user/`, dùng `parse_manifest`/`api_compat`, import `entry` (`module:Class`), khởi tạo `Plugin()`; lỗi -> trạng thái disabled/unavailable, không crash.
3. `registry/isolation.py`: timeout theo `ctx.limits.hook_timeout_s`, guard socket/subprocess/os.system, bắt exception -> `Diagnostic`.
4. `registry/arbitration.py`: chấm điểm Phụ lục C (`[scoring]` trong `config/default.toml`), tie-break maturity/priority/rel_path/adapter_id.
5. `sdk/testing.py` + `plugin new/test/list`. Không đổi chữ ký SDK nếu không có lỗi chứng minh được; nếu đổi, ghi vào `decisions.md`.
