# NHIỆM VỤ CÒN LẠI CỦA CLAUDE 03 (đợt 2) - làm theo thứ tự

Môi trường chạy: iSH, thư mục dự án `/root/pb3` (xem `decisions.md` #40). Không ghi cứng đường dẫn này trong mã.

Đã xong ở đợt 1: loader, compatibility, registry, arbitration, `plugin list`. Đừng viết lại; mở rộng bằng patch nhỏ.

## A. Isolation (`registry/isolation.py`) - XONG (sandbox 32 test; iSH: suite 393 test PASS, 2026-11-15)
- [x] A1. `run_hook(plugin, name, ctx, *args)`: timeout theo `ctx.limits.hook_timeout_s` (thread + cờ hủy `ctx.check_cancel`; ghi rõ không giết được vòng lặp C), kết quả/exception -> `Diagnostic` (E_PLUGIN_TIMEOUT / E_INTERNAL / mã PB3Error giữ nguyên).
- [x] A2. Guard: chặn `socket.socket`, `subprocess.*`, `os.system`, `os.popen`, `ctypes` load ngoài trong lúc hook chạy (patch tạm có phục hồi, chỉ áp cho luồng hook) -> E_PLUGIN_FORBIDDEN. Tài liệu hóa đây là guard "best effort", không phải sandbox.
- [x] A3. Cắm vào `loader.discover(import_guard=...)` để import/khởi tạo cũng được bọc.
- [x] A4. Chặn plugin ghi ngoài workspace: `ctx.tempdir` là nơi duy nhất; guard `open(..., 'w')` ngoài tempdir (best effort) + kiểm khi build trả bytes.
- [x] A5. Test: plugin raise, plugin treo (dùng timeout ngắn), mở socket, gọi subprocess, os.system, ghi file ngoài tempdir; pipeline vẫn chạy tiếp.
## B. Conformance kit + template (`sdk/testing.py`, `registry/conformance.py`, `registry/template.py`) - XONG (sandbox 55 test; iSH: suite 393 test PASS, 2026-11-15; `plugin new/test thu_nghiem` thật: PASS trên iSH 2026-11-15 18:29, xem C5)
- [x] B1. `run_conformance(record, ...)`: manifest hợp lệ, `detect` không crash, `parse` trả `LocTable` đúng schema, `verify` chạy được, `selftest` pass, không vi phạm guard (dùng A). Kết quả: bảng PASS/FAIL/SKIP có lý do.
- [x] B2. `plugin new <kind> <id>` sinh skeleton (đặc tả Phụ lục B) + fixture rỗng + manifest hợp lệ; id phải qua kiểm `^[a-z][a-z0-9_]{1,40}$`; không ghi đè thư mục sẵn có.
- [x] B3. `plugin test <id>` nối vào `services/plugin_manager.py` (giữ E_NOT_IMPLEMENTED cho `support`/`kb`).
- [x] B4. Test: skeleton sinh ra phải PASS conformance ngay; plugin mẫu cố ý hỏng phải FAIL đúng mục.
## C. Hoàn thiện
- [x] C1. Plugin nhiều file: XONG 2026-11-15 (sandbox 25 test; iSH PASS 2026-11-15 trong suite 527 test). Quyết: package riêng + import tương đối, KHÔNG `sys.path`, quét tĩnh cả cây, không dùng bytecode đệm (decisions #56). Mã: `registry/pluginpkg.py`, `registry/loader.py`.
- [x] C2. Kiểm bằng chứng M3/M4 (R5): XONG 2026-11-15, người dùng chốt hướng 1 (hạ hiệu lực xuống M2 + cảnh báo, không vô hiệu hóa). Bằng chứng là `tests/corpus/<id>.json`. Xem decisions #51, `registry/evidence.py`, 12 test (sandbox PASS; iSH PASS 2026-11-15 trong suite 527 test).
- [x] C3. Lưu bật/tắt plugin: XONG 2026-11-15 (sandbox 34 test; iSH PASS 2026-11-15 trong suite 527 test; `plugin disable/enable thu_nghiem` thật PASS trên iSH 18:29, lưu `config/local.toml`). Config `plugins.disabled` (kiểu `strlist`), `plugin enable|disable <id>`, menu mục 3 "QUẢN LÝ PLUGIN" (decisions #57). Mã: `core/config.py`, `registry/registry.py::build_registry`, `services/plugin_manager.py`, `ui/screens.py`.
- [x] C4. R3: người dùng xác nhận 2026-11-15 giữ vô hiệu hóa cả hai chiều (decisions #52). Không đổi mã.
- [x] C5 phần sandbox (2026-11-15, Muse): kiểm lại prompt 03 + đặc tả §4 trên bản sao mới; chạy `python3 run_tests.py --tier full` -> **527 test OK**; CLI thật trên bản sao: `plugin new/test/disable/enable thu_nghiem`, menu mục 3 đảo bằng số, plugin 2 file (import tương đối, 0 `__pycache__`), R5 M4→M2 (và giữ M4 khi có `tests/corpus/<id>.json` hợp lệ), thiếu `requires` -> `[unavailable]`, trùng id -> user `[ok]` (sau khi sửa decisions #60). Xem bằng chứng đầy đủ ở `CURRENT_STATE.md` (mục C5 phần sandbox) và `handoff/account_03.md`.
- [x] C5 phần iSH (XONG 2026-11-15 — 03 COMPLETE). Suite trong `/root/pb3`: `cd /root/pb3 && python3 run_tests.py --tier full` -> `Ran 527 tests in 264.9s OK (skipped=3, lỗi=0, fail=0)` (524 passed, 3 skipped, 0 failed), 18:28 +07. Plugin thật 18:29 +07: `plugin new format thu_nghiem` -> `plugin test` `KẾT QUẢ: PASS (CHƯA KIỂM DỮ LIỆU) (6 PASS, 0 FAIL, 5 SKIP; fixture: 0 file)` -> `plugin disable` (lưu `config/local.toml`, `plugins.disabled`) -> `list` `thu_nghiem [disabled] M1` ("người dùng đã tắt") -> `plugin enable` ("Đã bật lại plugin 'thu_nghiem'"). Không có test nào FAIL nên các test symlink/ghi nguyên tử trong suite đều qua. Bằng chứng đầy đủ ở `handoff/account_03.md` (mục cuối). Lưu ý: menu-toggle bằng số KHÔNG chạy riêng trên iSH (chỉ sandbox + suite C3 đã PASS trên iSH); dọn 2 thư mục `plugins_user/thu_nghiem` + `tests/fixtures/thu_nghiem` trên iSH đã xong 18:31 (`rm -rf` chạy sạch); dòng `disabled` trong `config/local.toml` chưa có bằng chứng đã xóa.
## D. Việc cho các tài khoản sau (đã chuẩn bị sẵn)
- Claude 04: dùng `sdk/vfs.py::read_policy`; đăng ký container plugin qua `load_plugin_class`/thư mục `plugins/<id>/`.
- Claude 05: dùng tên `write_modes` của 6.5 (`PATCH_INPLACE|REBUILD_SECTION|REBUILD_ALL`).
- Claude 06+: mỗi plugin builtin = `plugins/<id>/plugin.py` với MANIFEST literal; thêm `requires` đúng tên gói (`unitypy`, `lz4`).
- Claude 07: gọi `registry.candidates_for` -> `arbitration.contender_from_candidate` (truyền locale_evidence/manifest_ref/content_lang_fit/kb_agreement thật, và `maturity=rec.maturity_effective`: R5) -> `arbitrate`; chọn `conflict_margin`.
- Claude 09: SUPPORT_MATRIX sinh từ `Registry.rows()` (dùng `maturity_effective`) + bằng chứng test; đối chiếu `tests/corpus/<id>.json` bằng cách chạy lại (loader mới chỉ kiểm hình thức).
