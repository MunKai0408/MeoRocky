# HỢP ĐỒNG PLUGIN SDK v3 (Claude 02b) — Claude 03 trở đi PHẢI tuân theo

Bản này thay hợp đồng 1.0 của Claude 02. Nguồn đặc tả: `PB3_v3.1_iSH_10Claude.txt` mục 3, 4, 5.1, 10.1, Phụ lục A-D.
Mã: `core/model.py` (Evidence, SupportLevel, Locale, Project...), `sdk/{api,types,manifest,vfs,ir,example_kv}.py`.
`core/` và `ui/` KHÔNG import `sdk/` (có test). `api_version = 3` (số nguyên). IR `2.0`. `MODEL_VERSION = 2`. Python >= 3.8, chỉ thư viện chuẩn.

## 1. Import cho plugin (đúng Phụ lục B, đã có test chạy nguyên văn)
```python
from sdk.api import Plugin, api_version
from sdk.types import Evidence, Candidate, LocEntry, LocTable, SourceRef   # + Diagnostic, SupportLevel
```

## 2. Kiểu IR (`sdk/types.py`, đặc tả mục 3)
| Kiểu | Trường |
|---|---|
| `SourceRef` (3.1) | `container_stack: list[str]`, `rel_path`, `entry_offset`, `entry_size`, `encoding`, `extra: dict` |
| `LocEntry` (3.2) | `key`, `value`, `locale`, `key_origin` (`native/derived/generated/rowindex`), `status` (`ok/empty/conflict/truncated/unparsed`), `source: SourceRef` (BẮT BUỘC), `notes` |
| `LocTable` (3.3) | `entries` (giữ thứ tự nguồn), `locale`, `adapter_id`, `schema_hint`, `stats`, `conflicts`, `warnings`; `count_by_status()`, `duplicate_keys()`, `to_key_values()` |
| `Candidate` | `path`, `adapter_id`, `score` ∈ [0,1], `reasons`, `evidence`, `extra` |
| `Evidence` (3.5, `core/model.py`) | `kind`, `value`, `weight` ∈ [0,1], `reason`; `core.model.confidence(evs)` = tổng, chặn 1.0 |
| `Diagnostic` (3.6) | `code` (∈ Phụ lục D), `severity` (`info/warning/error`), `message_vi` (trống -> câu chuẩn của mã), `detail`, `plugin_id`, `where: SourceRef|None` |
| `SupportLevel` (10.1) | `M0..M4` (`ALL`, `rank()`, `valid()`, `label()`) |
Dữ liệu sai kiểu/thiếu/trường lạ -> `E_CORRUPT`. `Locale` (3.4): `code, aliases, script, display, detect_regex, numeric_id`.
`IRDocument` (`sdk/ir.py`) là phong bì `{schema:"pb3.ir", version:"2.0", producer, tables:[LocTable], metadata}`; đọc được khi cùng major và minor <= người đọc, ngược lại `E_CORRUPT`.

## 3. Interface plugin (`sdk/api.py`, mục 4.2)
`detect(ctx) -> [Evidence]`, `list_candidates(ctx) -> [Candidate]`, `probe(ctx, cand) -> dict`, `parse(ctx, cand) -> LocTable`,
`build(ctx, table, base_bytes) -> bytes`, `verify(ctx, base_bytes, new_bytes) -> dict`, `selftest(ctx) -> dict`.
- Không hook nào ghi vào nguồn: VFS chỉ đọc; `build` TRẢ bytes cho planner/patcher (mục 6) ghi ra.
- Khởi tạo `Plugin()` tự `parse_manifest(MANIFEST)`; capability thiếu hook -> `E_PLUGIN_INVALID` (`read`->`parse`, `write`->`build`, `container`->`open_container`, `hint`->`hints`).
- Mặc định trung thực: `detect/list_candidates -> []`, `probe -> {}`, `parse -> E_NOT_IMPLEMENTED`, `build/verify` trên plugin chỉ đọc -> `E_WRITE_DISABLED`,
  `selftest` chưa cài -> `{"ok": None, "skipped": True}` (không bao giờ giả PASS). Conformance kit (Claude 03) phải coi `ok=None` là "chưa kiểm", không phải đạt.
- Hai hook mở rộng ngoài chữ ký 4.2 (đặc tả chỉ nêu capability): `open_container(ctx, path) -> VFS` và `hints(ctx) -> dict`.

## 4. Context (`PluginContext`, mục 4.4)
`vfs, limits, locale (.code, .mapping), order, log/warn/diag, tempdir, dry_run, kb_read/kb_write, settings` (+ `plugin_id`, `check_cancel()`).
- `ctx.for_plugin(manifest)` hạ limits: `max_entry_bytes = min(host, manifest)`, `hook_timeout_s = min(host (heavy: 120s), manifest)` — plugin không nới được.
- `ctx.vfs` là `LimitedVFS` áp limits của plugin lên mọi lần đọc. Log/diag được làm sạch (cắt 300 ký tự, bỏ ký tự điều khiển), tối đa 1000 dòng.
- `kb_read/kb_write` qua đối tượng `kb` host cấp (`get(plugin_id, key)` / `put(plugin_id, key, value)`), khóa tự gắn `plugin_id`; `dry_run` hoặc không có KB -> không ghi, trả `False`.
- Timeout, cấm socket/subprocess, nạp plugin: việc của `registry/isolation.py` (Claude 03). SDK KHÔNG giả vờ làm các việc này.

## 5. Manifest (`sdk/manifest.py`, mục 4.3, Phụ lục A)
Bắt buộc: `id` (`^[a-z][a-z0-9_]{1,40}$`), `name`, `version` (`X.Y.Z`), `api_version` (int, 3), `kind` (`container|format|engine|profile|extractor`),
`capabilities` (`read|write|container|hint`), `maturity` (`M0..M4`), `priority` (0..1000), `match`, `limits` (`max_entry_bytes`, `hook_timeout_s`), `entry` (`module:Class`), `selftest` (bool), `docs` (không rỗng).
Tùy chọn: `true_cost` (`cheap|normal|heavy`, mặc định `normal`), `engines`, `locale_hints`, `write_modes` (`PATCH_INPLACE|REBUILD_SECTION|REBUILD_ALL`, mục 6.5), `requires`, `deprecated_for`.
- `match`: `extensions` (`.ext` chữ thường), `magic` (`{offset, hex, desc?}`; TOML viết `"OFFSET:HEX[:mô tả]"` vì `tomlmini` không có bảng nội tuyến), `name_regex`, `container_kind`, `min_score_to_probe`.
- Ràng buộc chéo: `write` cần `read` + `write_modes`; `engine/profile` chỉ `hint`; `extractor` chỉ `['read']` và tối đa M2; `container` cần capability `container`; M3/M4 cần `write`.
- Trường lạ bị bỏ qua và liệt kê ở `ignored_fields` (4.9). `api_compat()` trả `ok|too_old|too_new|invalid` để loader phân biệt lý do vô hiệu hóa; `parse_manifest` raise `E_PLUGIN_INVALID` với mọi mức khác `ok`.
- M3/M4 cần bằng chứng `tests/corpus/<id>.json` = `{"id": "<id>", "results": {"read": "PASS", "write": "PASS", "verify": "PASS"}}` (M3: read+write; M4: thêm verify). Loader (`registry/evidence.py`) KHÔNG vô hiệu hóa plugin thiếu bằng chứng mà hạ `PluginRecord.maturity_effective` (sàn M2) + cảnh báo; manifest không bị sửa. Chỉ kiểm hình thức; đối chiếu chạy lại thuộc SUPPORT_MATRIX (decisions #51). Trọng tài/SUPPORT_MATRIX dùng `maturity_effective`, không dùng mức khai.

## 6. VFS (`sdk/vfs.py`, mục 5.1)
Đường dẫn là chuỗi, tầng container nối bằng `::` (`"Payload/G.app/Data/x.unity3d::resources.assets"`). Cấm `..`, `.`, đoạn rỗng, `\`, NUL, `:` lẻ, tuyệt đối -> `E_UNSAFE_PATH`.
Giao diện: `iter_entries(prefix=None)`, `stat(path) -> VStat(path, size, crc_verified)`, `read(path, max_bytes=None)`, `slice(path, offset, size)`, `open_stream(path)` (nhị phân chỉ đọc, có `seek`), cộng `exists()`, `read_entry()`.
**Quy ước `read`** (Phụ lục B dùng cả đọc "đầu file" lẫn đọc nguyên entry): `max_bytes` < `limits.max_entry_bytes` -> tiền tố; `None` hoặc >= `max_entry_bytes` -> nguyên entry và entry lớn hơn -> `E_TOO_LARGE`
(không cắt cụt im lặng). Nạp RAM tối đa `max_memory_read_bytes`, vượt -> `E_TOO_LARGE` (dùng `slice`/`open_stream`). Không có API ghi/xóa/đổi tên nào. `MemoryVFS` là bản tham chiếu; VFS thật do Claude 04.

## 7. Kiểm thử bắt buộc khi thêm plugin
manifest hợp lệ, `detect` không crash, `parse` trả `LocTable` đúng schema, `verify` chạy được, `selftest`, round-trip IR, (nếu ghi) `build` giữ nguyên phần không sửa + key thiếu -> cảnh báo,
file hỏng -> mã lỗi chuẩn. Mẫu: `sdk/example_kv.py`, `tests/test_sdk.py`; Phụ lục B nguyên văn: `tests/appendix_b_plugin.py` + `tests/test_appendix_b.py`.

## Phụ: hợp đồng loader/registry (Claude 03 đợt 1; không đổi chữ ký SDK)
- Một plugin = một thư mục trong `plugins/` hoặc `plugins_user/` có `plugin.toml` HOẶC `plugin.py` với `MANIFEST` literal ở mức module (đọc tĩnh, không chạy mã; chấp nhận `16 * 1024 * 1024`, không gọi hàm/biến).
- `entry = "plugin:Class"`: module là file trong thư mục plugin; lớp phải kế thừa `sdk.api.Plugin`. Manifest lúc chạy phải bằng manifest đã duyệt.
- Đăng ký thống nhất cho plugin nhúng: `registry.loader.load_plugin_class(cls)`; từ thư mục: `registry.registry.build_registry(root, cfg)`.
- Trạng thái: `ok | disabled | unavailable` (+ công tắc `enabled`). Chỉ plugin `ok` và `enabled` được `Registry.active()/candidates_for()` trả về.
- Chọn plugin: `registry.candidates_for(path, head)` -> `arbitration.contender_from_candidate(...)` -> `arbitration.arbitrate(...)` (Phụ lục C). Chi tiết: decisions #32-39.

## Phụ: conformance kit + skeleton (Claude 03 đợt 2, nhiệm vụ B)
- Tạo plugin mới: `python3 menu.py plugin new <format|extractor|container|engine> <id>` -> `plugins_user/<id>/plugin.py` + `tests/fixtures/<id>/.gitkeep` (rỗng). Không ghi đè; id phải `^[a-z][a-z0-9_]{1,40}$` và không trùng thư mục builtin. `profile` là JSON dữ liệu, chưa hỗ trợ ở lệnh này.
- Kiểm: `python3 menu.py plugin test <id>` hoặc trong test: `from registry.conformance import run_conformance; run_conformance(plugin|record|lớp, fixture="tests/fixtures/<id>")`.
  (`sdk/testing.py` giữ logic và nhận bộ chạy hook được tiêm vào; sdk không import registry, nên cổng vào là `registry.conformance`.)
- Các mục: `manifest, readonly, detect, candidates, probe, parse, write, container, hints, selftest, guard`; mỗi mục PASS/FAIL/SKIP kèm lý do; kết quả chung FAIL nếu có >= 1 FAIL. Quy ước nghiêm: `selftest=true` mà trả `ok=None` là FAIL; plugin khai `write` mà `verify` trả `ok=None` là FAIL; plugin chỉ đọc mà `build/verify` không trả `E_WRITE_DISABLED` là FAIL.
- Fixture rỗng -> `probe/parse/write` SKIP và có ghi chú "fixture rỗng": PASS của skeleton chỉ chứng minh hợp đồng + selftest, KHÔNG chứng minh đúng trên dữ liệu thật.
- Skeleton khai `maturity: M1`. Nâng lên M2 sau khi có fixture + test parse; M3/M4 cần ghi ngược + bằng chứng `tests/corpus/<id>.json`.

## Phụ: plugin nhiều file (Claude 03, C1, decisions #56)
- Thư mục plugin có thể chứa nhiều file `.py`. File anh em import **tương đối**: `from .helper import X`, `from .lib.util import f` (thư mục con cần `__init__.py`). `import helper` không chạy (loader báo gợi ý). `entry` có thể là `sub.main:Class`.
- Không dùng `sys.path`; mỗi plugin là package riêng. `__init__.py` ở gốc plugin không được chạy. Chỉ nạp `.py` (không `.pyc` đứng một mình, không `.so`); không dùng bytecode đệm.
- Plugin user: mọi `.py` trong cây bị quét tĩnh (tối đa 64 file, sâu 4 cấp, mỗi file 1 MiB, tổng 4 MiB; symlink bị từ chối). Vẫn là lint, không phải sandbox.
