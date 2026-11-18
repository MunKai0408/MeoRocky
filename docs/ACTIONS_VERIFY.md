# Xác minh qua GitHub Actions — Anti-Wave Phase 3

> Tài liệu này ghi lại **sự thật đã đo** về khả năng dispatch workflow và
> verify nhánh qua Actions, để agent sau không suy đoán sai. Mọi con số
> dưới đây đều có nguồn đo kèm theo; phần nào chưa đo thì ghi rõ.

## 1. Bằng chứng quyền Actions (cobonla) — 403 → 404, chưa phải 204

Key Bear API là **cobonla** (fine-grained PAT, chỉ repo MunKai0408/MeoRocky).

| Thời điểm | Thao tác | Kết quả |
|---|---|---|
| Trước 12:17 +07 2026-11-18 | Dispatch `extract-large.yml` qua Bear API | **403** `Resource not accessible by personal access token` (agent Actions đo, ghi trong AUTOPILOT.md của nhánh `feat/actions-extract`) |
| 12:17 +07 | MASTER duyệt, cấp thêm quyền **Actions: Read and write** cho cobonla qua trình duyệt (trang token ghi "Read and Write access to actions, code, pull requests, and workflows"; phạm vi vẫn chỉ MeoRocky; hạn dùng 15/02/2027) | Đã cấp, verify qua giao diện token |
| 12:20 +07 | Dispatch lại `extract-large.yml` (ref `feat/actions-extract`, file_id giả `PROBE-INVALID-ID`) | **404** Not Found — KHÔNG còn 403 nữa |

**Kết luận trung thực:** quyền Actions đã có tác dụng (bước kiểm quyền đã
qua — cùng một lệnh trước đây trả 403, giờ trả 404). Nhưng **chưa có bằng
chứng dispatch thành công (204 + run được tạo)**. Đừng báo "dispatch đã
chạy được" cho tới khi có run thật.

## 2. Vì sao 404 — luật default branch của GitHub

404 này là do **luật đăng ký workflow của GitHub**, đã kiểm chứng từ tài
liệu GitHub (events reference + how-to):

> "To trigger the `workflow_dispatch` event, your workflow must be in the
> default branch."
> "On the GitHub UI, the 'Run workflow' button will be present if the
> workflow file exists on the default branch. Once a workflow has run at
> least once, you can dispatch it against any branch or tag via the GitHub
> API or GitHub CLI."

Nghĩa là: GitHub tra cứu workflow trong registry xây từ **nhánh mặc định
(main)**; `ref` trong lệnh dispatch chỉ quyết định *chạy bản nào*, không
quyết định *tìm thấy workflow hay không*. File workflow chỉ nằm trên nhánh
riêng thì cả nút Run workflow lẫn API đều không thấy nó → 404.

### Đã kiểm chứng đủ 7 điểm (v1.2 §III, parent đo 12:26 +07)

| Điểm kiểm tra | Kết quả [ĐÃ ĐO THẬT] |
|---|---|
| Repo owner/name | `MunKai0408/MeoRocky`, public |
| Default branch | `main` |
| Workflow ID / filename | `extract-large.yml` (đúng tên file, endpoint đúng) |
| Workflow có trên default branch không | **KHÔNG** — `GET contents .github/workflows/extract-large.yml?ref=main` → 404 |
| File trên nhánh riêng | **CÓ** — `ref=feat/actions-extract` → 200, sha `0e9eaf16`, 7670 byte |
| `workflow_dispatch` trong cấu hình | **CÓ** — `on: workflow_dispatch`, inputs `file_id` (bắt buộc), `game_name` (bắt buộc), `locales` (mặc định `"en,zh"`) |
| Token scope | Chỉ `MunKai0408/MeoRocky`; endpoint đúng |

**Kết luận:** 404 chỉ do workflow chưa nằm trên main. Muốn dispatch thật
(204), file `extract-large.yml` phải được gộp lên main trước — đó là việc
của agent Actions (rebase nhánh lên main mới nhất, verify, mở PR lại,
gộp sau khi CI + bảo vệ đạt; PR #27 cũ đã đóng chưa gộp, lý do đang hỏi).

### Ý nghĩa mã HTTP khi dispatch

| Mã | Nghĩa | Hành động |
|---|---|---|
| 401 | Xác thực thất bại (token sai / hết hạn / bị thu hồi) | Kiểm tra key, không tạo key mới khi chưa có lệnh MASTER |
| 403 | Thiếu quyền hoặc bị policy chặn | Cần Actions: Read and write (đã cấp cho cobonla) |
| 404 | Không tìm thấy workflow trong ngữ cảnh được yêu cầu | Kiểm tra 7 điểm ở bảng trên — thường là workflow chưa có trên main |
| 204 | Dispatch thành công, không có response body | Poll run mới nhất để lấy kết quả |
| 422 | Ref/input không hợp lệ, hoặc workflow không có `workflow_dispatch` | Kiểm tra ref tồn tại + input khớp khai báo YAML |

## 3. Workflow hiện có (đo 12:30 +07)

Registry (API `GET /actions/workflows`, total 4):

| Workflow | File | Trigger | Dispatch được? |
|---|---|---|---|
| PB3 test suite | `.github/workflows/test.yml` | push main + pull_request | **KHÔNG** — không có `workflow_dispatch` |
| test-and-deploy | `.github/workflows/pages.yml` | push main + pull_request | **KHÔNG** — không có `workflow_dispatch` |
| Muse Bridge Test | `.github/workflows/muse-bridge-test.yml` | (xem file) | Chưa xác minh trigger |
| pages-build-deployment | `dynamic/pages/pages-build-deployment` | (GitHub Pages tự sinh) | Không phải workflow của repo |

Mới ở Phase 3 (nhánh `feat/anti-wave-phase3-prep`, chưa lên main):

| Workflow | Trigger | Ghi chú |
|---|---|---|
| `anti-wave-ci.yml` | `workflow_dispatch` (inputs `ref`, `scope` unit/full/smoke) | Wrapper verify nhánh; chạy đúng lệnh của test.yml + ghi `failset.json` |
| `regression.yml` | schedule 01:00 +07 + `workflow_dispatch` | Chỉ chạy lại lệnh unit của test.yml — KHÔNG phải suite thứ hai |
| `integrity-check.yml` | `workflow_dispatch` + pull_request (chỉ khi chạm file anti-wave) | Checksum + file 0-byte + checkpoint |

**Quan sát phụ [ĐÃ ĐO THẬT]:** API registry liệt kê `muse-bridge-test.yml`
là active, nhưng `GET contents` file đó trên main trả 404 (thư mục
`.github/workflows/` trên main chỉ có `pages.yml` + `test.yml`). File có
thể đã bị xóa khỏi main sau lần chạy đầu (registry giữ lại mục cũ), hoặc
nằm ở nhánh khác — cần agent Actions xác minh, không suy đoán thêm.

**Tên cũ cần biết:** kế hoạch Phase 1 gọi workflow verify mới là
`verify-branch.yml`; bản chuẩn bị v1.2 này đặt tên là `anti-wave-ci.yml`
(1 workflow, không phải 2) để khỏi trùng với `test.yml`.

## 4. Ba đường trigger — đường nào dùng được khi nào

### Đường 1 — Mở PR, test.yml tự chạy (dùng được NGAY, cả lúc sóng bật)

- Mở PR từ nhánh feature vào main → `test.yml` chạy tự động (unit 1436
  test + assets full 1562 test + pyodide-node smoke).
- Agent không cần quyền dispatch; chỉ cần đọc runs/artifacts qua Bear API
  (`GET /actions/runs`, `GET /actions/runs/{id}/artifacts` — Actions READ
  đã đo 200).
- **Đây là đường verify chính trước khi workflow dispatchable lên main.**

### Đường 2 — User bấm Run workflow tay (chỉ sau khi workflow lên main)

- Tab Actions → chọn workflow → Run workflow → chọn nhánh.
- Nút Run workflow chỉ xuất hiện khi file workflow đã có trên main.
- Không cần token của agent; user tự bấm.

### Đường 3 — Web UI dispatch bằng token user nhập (thiết kế extract đã có)

- Tab Trích xuất của web tool: user nhập game + Drive ID/link + token
  GitHub (chỉ lưu localStorage) → web gọi API dispatch → poll run → tải
  artifact từng file.
- Chỉ dùng cho `extract-large.yml` (trích xuất IPA lớn), không phải để
  verify code.

### Đường KHÔNG có: agent tự dispatch lúc sóng bật — CHƯA, cho tới khi...

Agent tự dispatch được **chỉ khi cả hai điều kiện** đều đúng:
1. Token có Actions: Read and write — ✅ đã có (12:17 +07, bằng chứng 403→404).
2. File workflow đã có trên main — ❌ chưa (cả `extract-large.yml` lẫn
   `anti-wave-ci.yml` đều chưa lên main).

Khi (2) xong, agent dispatch bằng `anti_wave/github_sync.py::dispatch_workflow`
hoặc `POST /repos/MunKai0408/MeoRocky/actions/workflows/<file>/dispatches`
với `{"ref": "<nhanh>", "inputs": {...}}` → 204 là đã nhận lệnh.

## 5. Cách verify một nhánh (quy trình chuẩn Phase 3)

1. **Chuẩn bị trên nhánh riêng** (làm được lúc sóng bật, qua Bear API):
   code + checkpoint (`anti_wave/checkpoints/<agent_id>.json`) + patch
   (`.patch` kèm theo), kiểm tra head SHA trước khi ghi để khỏi ghi đè.
2. **Mở PR** vào main → `test.yml` tự chạy (Đường 1). Hoặc, khi
   `anti-wave-ci.yml` đã lên main: dispatch với `ref=<nhanh>`,
   `scope=unit` (nhanh) rồi `scope=full` (đủ).
3. **Poll kết quả:** `GET /actions/workflows/anti-wave-ci.yml/runs`
   (hoặc `test.yml`), chờ `status=completed`, đọc `conclusion`.
4. **Đọc fail-set:** tải artifact `antiwave-verify-unit` /
   `antiwave-verify-full` / `pb3-test-unit-log` → `failset.json`
   (`{tests, failures, errors, skipped, failed: [...], error_ids: [...]}`).
   So sánh fail-set với baseline main (cùng môi trường) — 0 new red mới
   được gộp, không so số fail thô.
5. **Integrity:** `integrity-check.yml` (hoặc chạy local
   `python3 scripts/anti_wave_check.py --strict`) — bắt file 0-byte/
   truncate do sóng để lại.
6. **Chỉ gộp sau khi** CI xanh + MASTER phê duyệt (nhiệm vụ anti-wave
   GHI ĐÈ auto-merge thường lệ: không tự gộp).

## 6. Giới hạn trung thực (đừng hứa quá)

- Actions runner **chỉ chạy workflow có sẵn, không phải terminal**:
  agent không thể nhờ Actions chạy lệnh tùy ý, sửa code giùm, hay mở
  terminal. Bear API **chỉ lưu/đọc, không thực thi**.
- Runner không thay thế verify local hoàn toàn: harness (`exp/verify_*`)
  và smoke local vẫn cần VM khi sóng tắt (ghi `/tmp` + cây local).
- `tempfile` / `TmpCase` chết trong sóng EPERM (Python 3.12 probe ghi
  thư mục tạm → `FileNotFoundError`): test dùng tempfile sẽ đỏ local
  trong sóng — ghi rõ khi báo cáo, verify lại qua Actions.
- Dispatch 204 chỉ nghĩa là "đã nhận lệnh", không phải "test đã xanh":
  phải poll tới `completed` rồi đọc fail-set mới kết luận được.

## 7. Chi phí

$0 cho mọi bước trên: repo public dùng runner chuẩn GitHub-hosted miễn
phí, không giới hạn phút; artifact Free 500 MB; cache 10 GB/repo.
Codespaces (workspace dự phòng, Phase sau) trong hạn mức Free 120
core-hours/tháng + 15 GB-month — hết quota bị chặn, không phát sinh phí
bất ngờ khi không đặt spending limit.
