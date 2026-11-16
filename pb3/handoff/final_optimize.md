# Rà soát tổng cuối — Tối ưu hiệu năng + Thanh tiến độ (hạng mục b + f)

Nhóm: tối ưu hiệu năng + rà soát thanh tiến độ. Phạm vi chỉ các file được giao
(xem §7). Không build zip. Không sửa `handoff/decisions.md` / `CURRENT_STATE.md`.

## 1. Kết quả kiểm chứng (tóm tắt)

- Test mới `tests/test_final_optimize.py`: **26 test, OK** (`0.402s`).
- Tầng fast sau thay đổi: **1368/1371 đạt, 0 lỗi, 2 fail, 1 bỏ qua, 86.7s**
  (`TMPDIR=~/workspace/.tmp_suite python3 run_tests.py --tier fast`,
  log `/tmp/fast_after.log`). Cả 2 fail đều **không thuộc phạm vi nhóm này**,
  tái hiện được khi chạy riêng lẻ (xem §6) — không sửa vì ngoài phạm vi.
- **Không chạy full suite** theo chỉ đạo điều phối 04:55 (tmpfs `/tmp` chỉ 512MB,
  5 nhóm chạy song song đã làm đầy đĩa — Errno 28; coordinator chạy full suite
  + mô phỏng iSH ở bước tích hợp). Lần chạy full suite trước đó đã chết sớm vì
  hết đĩa (`OSError: [Errno 28] No space left on device` trong `TestBatchRun`
  setUpClass; `/tmp` khi đó có 82MB `bench.ipa` + 82MB `bench_restored.ipa`
  của nhóm khác — không xóa). Cách làm đã dùng: mọi lần chạy suite đều đặt
  `TMPDIR=~/workspace/.tmp_suite` (đĩa workspace ~149GB trống).
- Các lần chạy kiểm chứng khác (đều xanh): `test_optimize` 31 OK;
  `test_extraction` + `test_engine_detect` 80 OK;
  `test_optimize` + `test_unity` + `test_progress_steady` + `test_color_display`
  153 OK; `test_extraction` + `test_container` + `test_language_cli` +
  `test_translate` + `test_translate_cli` + `test_optimize` + `test_unity` 274 OK.

## 2. Tối ưu hiệu năng — số đo trước/sau (A/B trong tiến trình, min-of-rounds)

Phương pháp: so sánh cũ/mới xen kẽ trong cùng tiến trình (load trung bình
4.04–5.76 do 5 nhóm chạy song song nên số tuyệt đối giữa các lần chạy không
so được; chỉ số A/B xen kẽ mới đáng tin). Fixture trong repo rất nhỏ
(sample.ipa 672B, sample.unity3d 7KB, game1.zip 4KB); IPA thật 21.9MB /
plan ~73k dòng **không có trong repo** — đã dùng fixture tổng hợp lớn
(3000-object v23 `.assets`, IPA 27 bundle, 300 file JSON) để đo.

| Tối ưu | Trước | Sau | Thay đổi |
|---|---|---|---|
| `core/progress.py::display_width` (fast path ASCII + cache độ rộng ký tự) ×2000 mẫu | — | — | **−41.2%** |
| `sdk/discovery.py::detect_locale` (regex biên dịch sẵn + pre-check substring) ×327 | 0.0253s | 0.0080s | **−68.6%** |
| `sdk/discovery.py::engine_signals` (non-Unity; precompute tuple) godot ×327 | 0.0032s | 0.0009s | **−73.2%** |
| `sdk/unity.py::read_text_asset_head` (struct.unpack_from trực tiếp thay `_Reader` + `aligned_string`) ×3000 | 0.0109s | 0.0045s | **−59.1%** |
| `sdk/unity.py::_plan_assets_rows` 3000 object (nhờ head mới) | 0.0182s | 0.0126s | **−30.7%** |
| `sdk/discovery.py::find_referenced_paths._walk` (hoist dirname, pre-filter rẻ trước `splitext`) ×200 | — | — | **−35.3%** |
| `sdk/translate.py::placeholders_ok` (so sánh trực tiếp, bỏ dựng dict) ×3000 | 0.0561s | 0.0376s | **−33%** |
| Plan cache v2 (dòng list thay dict): blob 73k dòng | 14,826,248 B | 8,183,248 B | **−45%** |
| Plan cache v2: `json.loads` blob | — | — | **−32.7%** |
| Plan cache v2: `_plan_from_dict` | — | — | **−31.1%** |
| Plan cache v2: chi phí cache-hit tổng (73k dòng) | ~0.55s | ~0.37s | **~−33%** |
| `sdk/translate.py::TranslationCache.save` (JSON compact, bỏ `indent=1`) 2000 entry | — | — | **−25%** (file −2%: 198,924 → 194,917 B) |
| Extract 300 JSON end-to-end (A/B, hàm discovery cũ monkeypatch lại) | 0.524s | 0.481s | **−8.2%** |

Lưu ý trung thực:

- `detect_engine` tổng thể ~phẳng (+0.1%) vì nhánh Unity chiếm phần lớn —
  nhánh Unity không đụng tới.
- `_plan_to_dict` riêng lẻ CHẬM HƠN với dòng list (+~60–85% trong CPython —
  dict literal với key hằng có fast path), nhưng chi phí cache-hit ròng vẫn
  cải thiện ~−33% nhờ blob nhỏ hơn; đường cache-miss gần như trung hòa
  (ghi blob nhỏ hơn bù cho build chậm hơn). Cache v1 vẫn đọc được
  (`PLAN_CACHE_VERSIONS_READABLE = (1, 2)`).
- Extract end-to-end chỉ −8.2% vì phần lớn thời gian nằm ở
  `formats.make_entries` / plugin `list_candidates` — không thuộc file nhóm này.
- `_print_plan` 73k dòng: không đổi theo thiết kế (thử hoist `wrap_text` xem §3).
- Số tuyệt đối cuối dưới tải nặng (load 5.76, min của 3 — KHÔNG dùng làm
  before/after so với số đo đơn lẻ trước đó): `_print_plan` 1.60s,
  `json.loads` blob cache 0.30s, `_plan_from_dict` 0.23s, `_plan_to_dict`
  0.072s, `parse_serialized` 3000 obj 0.0039s, `_plan_assets_rows` 0.0108s,
  `plan_bytes` IPA lớn 0.0296s, extract 300 JSON 0.402s, `detect_locale`
  0.0080s, `detect_engine` 0.0045s, `translate_table` fake 2000 entry 0.035s.
- Translate: chỉ tối ưu phần xử lý cục bộ (`placeholders_ok`, cache save);
  KHÔNG đổi sang batch nhiều chuỗi/request trong đợt này (theo yêu cầu).

### Chi tiết từng thay đổi

- `core/progress.py`: `display_width` fast path ASCII (`len(s)` khi không ANSI
  + `isascii()`), còn lại cộng qua `_prog_char_width` có cache (combining → 0,
  W/F → 2, còn lại 1; cache ≤8192). `truncate_display` dùng chung hàm này.
  Kết quả giống hệt (đã kiểm: combining → 0, ANSI → 0, CJK → 2).
- `sdk/unity.py`: `read_text_asset_head` viết lại bằng `struct.unpack_from`
  trực tiếp trên bytes; giữ nguyên thứ tự kiểm tra và thông điệp lỗi
  (kể cả `"đọc vượt quá dữ liệu (N byte tại P)"`,
  `"chuỗi dài %d byte không hợp lệ"`, lỗi decode qua `error_from_exception`).
- `sdk/discovery.py`: `detect_locale` cache regex theo mã ở cấp module +
  pre-check `code_low in low` trước regex (vòng alias không đổi);
  `engine_signals` (non-Unity) precompute `(name, low, base, ext)` một lần,
  logic khớp chính xác `_name_hit` (giữ `_name_hit` nguyên cho tương thích);
  `inventory` hoist `tuple(UNITY_EXTENSIONS)` thành hằng module
  `_UNITY_EXTS_TUPLE`, thêm tham số tùy chọn `on_entry(done, name)` — đếm
  lũy kế qua container lồng nhau (đệ quy truyền lambda bọc `_tick` của cha
  nên số đếm đơn điệu, không khởi động lại mỗi tầng); tổng chưa biết trước
  nên chỉ báo số lượng, không báo %.
- `sdk/discovery.py::find_referenced_paths._walk`: hoist
  `base = os.path.dirname(path)` ra ngoài `_walk`; pre-filter rẻ mỗi chuỗi
  (bỏ `replace("\\","/")` khi không có backslash; yêu cầu dấu chấm trong
  basename với `dot > slash + 1` — tương đương `splitext(".json")` cho ext
  rỗng; độ dài ext 3–11, đúng khoảng của `_REF_EXTS`; chỉ lowercase phần ext).
- `sdk/translate.py`: `TranslationCache.save` bỏ `indent=1` → JSON compact
  (file cache nội bộ; `json.load` đọc được cả hai dạng, file cũ vẫn load);
  `placeholders_ok` so sánh trực tiếp
  `exp.placeholders(source) == exp.placeholders(translated)` thay vì qua
  `check_placeholders` với dict dựng tạm mỗi key (cùng phép so sánh).
- `services/unity_tool.py`: plan cache v2 — dòng là list compact
  `[container, entry, kind, object_type, path_id, offset, size, guess, name]`;
  `_plan_from_dict` nhận cả dòng list (v2) lẫn dòng dict (v1);
  `_load_plan_cache` nhận version 1 và 2.

## 3. Thử nghiệm đã REVERT (không ship) — có số đo

- `core/color.py::wrap_text` hoist `raw.isascii()` theo dòng: A/B +7.4% /
  end-to-end −0.4% (nhiễu, không có lợi). Đã revert về code cũ chính xác.
  File trở lại nguyên trạng.
- `sdk/exporters.py` dedup placeholder bằng set: A/B −0.9% (không có lợi,
  ít placeholder mỗi chuỗi). Đã revert về code cũ chính xác. File trở lại
  nguyên trạng.

## 4. Rà soát thanh tiến độ (luật #152–153) — nơi đã sửa

Nguyên tắc áp dụng: % thật theo công việc đã xong, ETA từ thời gian thực,
tăng từng 1%, không nhảy, không báo láo; chỗ không đo được % thì hiện số
lượng đã xong/tổng. Dùng `SteadyProgress`/`Overall` có sẵn, không chế mẫu mới.

### Mới: `SteppedReporter` (`core/progress.py`)

Báo tiến độ thật qua `app.report_progress`, bước 1% qua `step_pcts` +
`ov.render()` mỗi %; chỉ dùng Overall khi `ov.enabled`, còn không thì fallback
`SteadyProgress` để chạy redirect vẫn có dòng stderr thưa; không vượt 100%,
không lùi; `total <= 0` không báo gì; `note` là chuỗi hoặc callable
`(interp_done, total) -> note`; `finish()` chỉ kết thúc SteadyProgress của
chính nó.

### Từng thao tác đã sửa (file + thao tác)

- `services/unity_tool.py::cmd_inspect` — quét plan: tạo
  `SteppedReporter(app, "quét")`, truyền `on_entry` vào plan build; trước đây
  chỉ có 1 dòng stderr/entry khi Overall tắt (#113).
- `services/unity_tool.py::_run_mod_cli` (đường plan của mod CLI) — như trên;
  trước đây mod CLI **không báo tiến độ nào** trong lúc quét.
- `services/unity_tool.py::run_interactive` — như trên; trước đây menu Mod
  **không báo tiến độ nào** trong lúc quét.
- `services/unity_tool.py::_inspect_progress(app, done, total, name, stepper=None)` —
  có stepper thì đây là kênh tiến độ duy nhất (không trùng dòng stderr);
  không stepper thì giữ đúng hành vi cũ (1 lần `report_progress` +
  dòng `"quét i/N: name"` khi bar tắt).
- `sdk/extraction.py::run_extraction` — `ExtractionRequest` thêm
  `progress_fn: Optional[Callable[[str, int, Optional[int]], None]] = None`
  (cộng thêm, mặc định None). Các pha: `"inventory"` (total None — chỉ đếm),
  `"layers"`, `"candidates"`, `"sniff"`, `"probe"`, `"export"`. Báo theo kiểu
  lag (báo i−1 ở đầu item i nên % luôn là công việc ĐÃ xong; lần cuối đóng
  về total). Đã nối: inventory `on_entry`, `_build_layers(..., progress_fn)`,
  vòng candidates (total = records × layers), pass sniff (thu danh sách trước
  để có total), vòng probe (báo đúng số đã probe, kể cả break sớm ở
  `MAX_PROBE_CANDIDATES`), vòng export (file đã chọn). `_build_layers` nhận
  thêm `progress_fn=None` với báo lag. `apply_edits` thêm `progress_fn` tùy
  chọn + wrapper `_ap_safe`; nối inventory + `_build_layers`. Callback lỗi
  không bao giờ làm hỏng extraction (nuốt lỗi trong `_progress` nội bộ).
- `services/language_tool.py` — mới `_ExtractProgressAdapter(app)` +
  `_PHASE_LABELS` (inventory="liệt kê file", layers="mở lớp container",
  candidates="tìm ứng viên", sniff="dò nội dung", probe="kiểm tra ứng viên",
  export="xuất bảng"). Có Overall: mỗi pha là một stage (`ov.begin`), bước
  1% với note `"label i/N"`; pha inventory (total None) chỉ cập nhật note
  `"%s: %d entry"` và render tối đa 1 lần/giây (không % giả). Không Overall:
  một `SteadyProgress` mỗi pha. Đã nối vào `_run_extract`, `_run_apply`,
  `run_interactive` (request extract + cả hai lần gọi `apply_edits`).
- `services/container_tool.py::cmd_list` — `SteppedReporter(app, "liệt kê")`
  mỗi entry; trước đây `app.report_progress((i+1)/N)` thô nhảy 33%/entry
  trên fixture 3 entry.
- `services/container_tool.py::cmd_read` — copy streaming báo theo từng chunk
  qua `SteppedReporter(app, "đọc entry")` với `done` byte / `size`; trước đây
  im lặng cho tới khi xong; `stepper.finish()` ở cuối.

### Đã rà soát, KHÔNG sửa được trong phạm vi này (ghi cho coordinator/nhóm khác)

- `sdk/batch.py::_report_progress` gọi `app.report_progress(done/total)`
  trực tiếp mỗi game nên thanh Overall nhảy theo game (SteadyProgress nội bộ
  của nó thì ổn) — `sdk/batch.py` thuộc nhóm review.
- `plugin test` (`services/plugin_manager.py::cmd_test` →
  `registry.conformance.run_conformance`) không có progress callback để móc —
  không hiện % thật được nếu không thêm hook trong `registry/conformance.py`
  (ngoài phạm vi); thanh Overall của CLI vẫn phủ bằng ước lượng thời gian.
- `services/diagnostics_tool.py` **không tồn tại** trong cây lúc nhóm này bắt
  đầu (diagnose nằm trong `ui/handlers.py`, không thuộc phạm vi). Lưu ý: tới
  lúc chạy fast tier, file này ĐÃ xuất hiện với `inventory_of`/`hex_lines`
  (nhóm khác tạo) — xem §6.
- Các stage mờ theo thời gian của Overall là thiết kế có sẵn (#20/#115),
  không phải vi phạm #152 cần thiết kế lại — tiến độ thật đã nối ở mọi nơi
  đo được.
- Phát hiện ngoài phạm vi (không sửa, để nhóm sở hữu xem):
  `sdk/formats.py::FormatPluginBase.list_candidates` đọc head mỗi ứng viên
  HAI lần (`ctx.vfs.read(p, max_bytes=4096)` trong vòng chấm điểm rồi lại
  trong list comprehension cho `_evidence_for`) — 2× head read mỗi ứng viên
  mỗi plugin.

## 5. Test mới — `tests/test_final_optimize.py` (26 test, OK)

- `read_text_asset_head` == đọc đầy đủ + tham chiếu `_Reader` cũ (kể cả
  bằng thông điệp lỗi: UTF-8 script/name hỏng, padding cắt, độ dài âm,
  input ngắn).
- `detect_locale` == tham chiếu regex-per-call cũ trên corpus;
  `engine_signals` == tham chiếu `_name_hit`; `find_referenced_paths`
  trường hợp biên (chuỗi tên ẩn `.json`, `..`, backslash, `UPPER.XML`).
- `placeholders_ok` các trường hợp + tương đương `check_placeholders`
  khi số newline khớp.
- Plan cache v2 round-trip, dòng dict v1 vẫn load, blob compact <70% blob
  dict ở 5000 dòng; `TranslationCache` save/load compact + file indent cũ
  vẫn load.
- `SteppedReporter` bước 1% với Overall giả, kẹp 100%, không lùi, total=0
  im lặng, fallback SteadyProgress (không Overall / Overall tắt);
  `_inspect_progress` có stepper (bước 1%) và không stepper (1 lần gọi,
  hành vi cũ); container list bước 1% + container read đúng byte +
  `100%` trong stderr.
- Extraction `progress_fn`: đủ pha (inventory/probe/export), đơn điệu
  từng pha, done ≤ total, probe/export đóng ở done==total;
  `_ExtractProgressAdapter` bước 1%, inventory chỉ đếm (không gọi frac),
  fallback không Overall.
- 4 lỗi test đã sửa trong lúc làm (phía test, không phải sản phẩm):
  `_head_old` cần bọc `error_from_exception`; test `detect_engine` cần tên
  thật sự detect được Unity (`level.unity3d` + `globalgamemanagers` +
  `resources.assets` → `CÓ KHẢ NĂNG`); so sánh placeholder chỉ hợp lệ khi
  số newline khớp; entry container read phải là đường dẫn đầy đủ
  `Payload/Game.app/Data/level.unity3d`.

## 6. Hai fail ở tầng fast — KHÔNG thuộc phạm vi nhóm này

Chạy `TMPDIR=~/workspace/.tmp_suite python3 -m unittest
tests.test_app_menu_cli.TestMenuInProcess.test_unimplemented_shows_clear_status_and_changes_nothing
tests.test_font.TestScan.test_scan_dir` → cả 2 fail lại, xác định (không phải
nhiễu tải):

1. `tests.test_app_menu_cli.TestMenuInProcess.test_unimplemented_shows_clear_status_and_changes_nothing`
   — test kỳ vọng menu [5] > [3] Inventory/hex/strings hiện
   `"CHƯA TRIỂN KHAI"` + `"không giả lập"` (vì `services/diagnostics_tool`
   chưa có, đó là nợ F6 của Claude 10). Hiện tại [5][3] mở file picker
   (`== /home/...` → `"Số không hợp lệ."` → `"Đã hủy, chưa chọn file."`):
   nhóm khác đã triển khai F6 (file `services/diagnostics_tool.py` với
   `inventory_of`/`hex_lines` giờ đã tồn tại) nên kỳ vọng của test đã cũ.
   Nhóm này không hề chạm `ui/handlers.py` hay `services/diagnostics_tool.py`.
   Đề nghị coordinator: cập nhật test theo hành vi mới (nhóm sở hữu F6).
2. `tests.test_font.TestScan.test_scan_dir` — `len(scan.sdf_suspects)` = 3,
   kỳ vọng 1. Thuộc nhóm font (`sdk/font.py` / `services/font_tool.py`,
   không phải file nhóm này). Lưu ý: điều phối báo nhóm font đã xong
   (41 test xanh, Be Vietnam Pro đủ 112/112 ký tự) nhưng test này vẫn fail
   trong lần chạy fast 21:54 và khi chạy riêng sau đó — coordinator nên
   xác nhận lại với nhóm font.

## 7. File đã sửa (chỉ trong phạm vi được giao)

- `core/progress.py` (display_width + `SteppedReporter`)
- `sdk/unity.py` (`read_text_asset_head`)
- `sdk/discovery.py` (`detect_locale`, `engine_signals`, `inventory`,
  `find_referenced_paths._walk`)
- `sdk/translate.py` (`TranslationCache.save`, `placeholders_ok`)
- `sdk/extraction.py` (`progress_fn` cho `ExtractionRequest`,
  `run_extraction`, `_build_layers`, `apply_edits`)
- `services/unity_tool.py` (plan cache v2, `_inspect_progress`,
  `SteppedReporter` ở inspect/mod CLI/interactive)
- `services/language_tool.py` (`_ExtractProgressAdapter`, nối vào
  extract/apply/interactive)
- `services/container_tool.py` (`cmd_list`, `cmd_read`)
- `tests/test_final_optimize.py` (MỚI, 26 test)
- Không đổi API, không đổi hành vi/output (byte-identical ở chỗ test khóa),
  stdlib Python 3.9, không thêm dependency, không gọi mạng mới, dấu tiếng
  Việt đầy đủ, màu chỉ qua `core/color.py`, dry-run mặc định khi ghi ngược.
- `core/color.py`, `sdk/exporters.py`: đã thử tối ưu rồi revert — trở lại
  nguyên trạng, không có thay đổi ròng.

## 8. Giới hạn trung thực

- Không có IPA thật 21.9MB trong repo — mọi số đo lớn dùng fixture tổng hợp;
  cần Joseph xác minh trên iSH với IPA thật (9 Kings).
- `detect_engine` tổng thể phẳng; extract end-to-end chỉ −8.2% (hotspot còn
  lại nằm ngoài file nhóm này); `_print_plan` không cải thiện.
- Translate chưa batch nhiều chuỗi/request (theo yêu cầu đợt này); ước tính
  cũ 16k chuỗi ~2+ giờ vẫn đúng với provider hiện tại.
- Full suite + mô phỏng iSH chưa chạy trong đợt này (chỉ đạo điều phối 04:55
  — để bước tích hợp); tầng fast 1368/1371 với 2 fail ngoài phạm vi (§6).
- Số đo tuyệt đối bị nhiễu bởi 5 nhóm chạy song song trên cell 1.5 CPU
  (load 4–5.76); chỉ số A/B xen kẽ trong tiến trình là đáng tin.
