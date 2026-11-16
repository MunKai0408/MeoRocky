# Rà soát end-to-end luồng dịch trên web (phần A)

Ngày: 2026-11-16. Rà soát từng bước của luồng dịch, trả lời cho mỗi bước: **bước này có thể làm mất chuỗi ở đâu** — và trạng thái sau đợt sửa (A). Lỗi đã chứng minh bằng file thật thì sửa hết; giới hạn còn lại ghi rõ, không giấu.

Luồng: nạp file JSON → tách ngôn ngữ theo từng chuỗi (lúc trích xuất) → chọn ngôn ngữ nguồn (en/zh) → dedupe → hàng đợi/batch → gọi provider → tiến trình + hủy/tục → ghi file output → báo số lượng.

## 1. Nạp file JSON đầu vào

| Đường nạp | Mất chuỗi ở đâu | Trạng thái |
|---|---|---|
| Chọn file vừa trích xuất (dropdown → `bridge.read_extracted`) | `read_extracted` giới hạn **4MB** — file trích xuất lớn hơn bị cắt/đọc thiếu, mất chuỗi im lặng | **Rủi ro còn tồn tại.** File fixture Passpartout ~1MB nên không phải nguyên nhân vụ 21:24, nhưng file MonoBehaviour gộp của 9 Kings (~67.620 entry) có thể vượt 4MB. Cần nâng giới hạn hoặc đọc theo trang — ghi vào việc còn lại, không sửa trong đợt này vì chưa đo được file nào vượt. |
| Dán tay vào textarea | Người dùng dán thiếu / dán nhầm file — tool không biết | Không sửa được bằng code; UI đã có banner "Chưa có nội dung để dịch" + ghi provenance `origin: "manual"` để (E) biết mà hỏi lại. |
| Tải file JSON lên (`translate-json-file`) | Chỉ biết tên file, không rõ trích từ đâu | Đã ghi provenance `origin: "upload"` + tên file (mục A, cho (E)). |
| JSON lỗi cú pháp | `startTranslateJob` báo lỗi, không dịch | Đúng — dừng rõ ràng, không mất lặng. |

## 2. Tách ngôn ngữ theo từng chuỗi (lúc trích xuất — `sdk/extraction.py::_mb_generic_kv`, `sdk/langdetect.py`)

| Điểm mất | Trạng thái |
|---|---|
| Chuỗi Latin ngắn không từ chức năng/dấu (vd `Extraordinaire!`, `Fiasko.`) bị gán nhầm `en` yếu rồi lọt vào file en | **Giới hạn đã biết, còn tồn tại.** Đo bằng ground truth khe: file en Passpartout 2.693 entry có 16 sai (đo được chắc chắn), file en 9 Kings 11.352 entry có 20 sai; phần lớn entry còn lại không kiểm chứng được bằng khe (nhóm ngắn + object không phải bảng term). Bộ lọc dịch ở tab Dịch là lưới thứ hai nhưng dùng CÙNG bộ phát hiện nên không bắt được chuỗi Latin yếu đã gán nhầm. |
| Chuỗi Nhật thuần kanji không kana → `zh` | **Giới hạn đã biết.** File zh Passpartout 1.715 entry có 18 sai (toàn kanji Nhật); file zh 9 Kings 1.888 entry có 125 sai (toàn kanji Nhật). Không phân biệt được nếu không có ngữ cảnh. |
| Học vị trí sai (khe Na Uy học thành `sv`, `sk`→`cs`, `no` chỉ 4,9% chính xác) | Chuỗi rơi vào ngôn ngữ sai nhưng NHẤT QUÁN (không lọt vào en) — ghi rõ là giới hạn. Độ chính xác phát hiện từng chuỗi đo được: Passpartout **81,0%**, 9 Kings **62,4%** (validator `exp/validate_langdetect.py`, ground truth theo khe). |
| Chunk dài hơn modal (gộp nhiều term) | Đã tách lại (`_resplit_long_chunks`); chunk không tách được thì giữ key đường dẫn, không mất chuỗi. |
| Giá trị giống hệt nhau ở nhiều ngôn ngữ (vd tên riêng `Odyssey`) | Gộp 1 bản, đếm `identical_dropped` — KHÔNG mất chuỗi (giá trị giống hệt từng byte). |
| Thứ tự key | Đã sửa: bỏ `sort_keys=True` ở 2 chỗ dump file ảo; giữ thứ tự chèn (thứ tự gốc trong game). Đối chiếu: key term đầu tiên của file tách 9 Kings là `Achievement_HisWinningness`, khớp file mẫu Joseph (2.032 term chung, chỉ thiếu 1 key `Skin_` rỗng/hỏng ở vị trí 1699). |

## 3. Chọn ngôn ngữ nguồn (en/zh)

| Điểm mất | Trạng thái |
|---|---|
| Bridge hard-code nguồn `en` (chọn dịch zh vẫn gửi chuỗi đi như tiếng Anh) | **ĐÃ SỬA.** `translate_json` + `translate_json_google` thêm tham số `src` (mặc định `en`, validate qua `check_lang`, `src==to` thì lỗi). Đã tái hiện lỗi cũ bằng mock Google trước khi sửa (7/7 chuỗi không phải tiếng Anh bị gửi như tiếng Anh) và kiểm chứng sau sửa (20/20 mục PASS, xem §9). |
| Không có select ngôn ngữ nguồn trên UI | **ĐÃ SỬA.** Thêm `#translate-from` (en mặc định / zh) cạnh `#translate-to`; job lưu `from`; TM chỉ dùng job cùng `from`; job cũ không có `from` bị cách ly hoàn toàn. |
| Lọc chỉ ở cấp file (chuỗi sai ngôn ngữ trong cùng file vẫn lọt vào hàng đợi) | **ĐÃ SỬA.** Lọc theo từng ĐOẠN trong bridge (`_source_match`): phát hiện mạnh ≠ nguồn → bỏ qua, giữ nguyên từng byte, ghi lý do vào `skipped_by_reason`/`skipped_by_lang`; Latin yếu chỉ tính là en (nguồn zh thì loại); lưới an toàn dấu chặn chuỗi có dấu Latin không phải tiếng Anh. Không nạp được `langdetect` thì RAISE (fail-closed) thay vì im lặng nhận hết — lỗi này từng xảy ra thật khi `langdetect.py` chưa có trong `files.json` (91→92). |
| Thẻ Unity làm nhiễu phát hiện (dòng chỉ còn thẻ đóng `</b>`, `</size>` vẫn có chữ Latin `size`/`b` → bị coi là tiếng Anh yếu) | **ĐÃ SỬA (phát hiện khi đối chiếu fixture).** `mask_line` nay che cả thẻ đóng (`</?[A-Za-z][^>]*>`); bridge phát hiện trên dòng ĐÃ CHE thay vì dòng thô; đường offline cũng phát hiện trên giá trị đã che. Khóa bằng test (`test_rich_text_cjk_kept_byte_identical`, `test_mask_line_masks_closing_tags`). |

## 4. Dedupe (gộp chuỗi trùng để gọi API)

| Điểm mất | Trạng thái |
|---|---|
| Key khác nhau cùng value bị gộp mất | **KHÔNG mất.** Dedupe theo dòng đã che đã chuẩn hoá (`_norm_segment_text`), dịch 1 lần rồi map lại cho MỌI key (`uniques[uid]["keys"]`); unmask dùng tokens riêng từng dòng. Kiểm chứng: `bridge_dedupe_reexpands_all_keys` PASS (1 chuỗi duy nhất gửi đi, đủ key trải lại). |
| Chuỗi trùng nhưng ngữ cảnh khác nhau (cùng value, khác nghĩa theo key) | Dedupe theo value chính xác — cùng value thì cùng bản dịch; đây là hành vi có chủ ý của TM/dedupe, ghi rõ trong báo cáo số lượng ("N chuỗi, M chuỗi duy nhất đã gọi dịch"). |
| `skipped` không tính vào dedupe | Đoạn bị bỏ qua (không thuộc nguồn) không vào hàng đợi dedupe, giữ nguyên từng byte. |

## 5. Hàng đợi / batch (app.js `runTranslateGoogleJob` chunk 500 key; bridge batch 100 chuỗi / 5000 ký tự, marker ⟦n⟧)

| Điểm mất | Trạng thái |
|---|---|
| Batch trả sai số chuỗi (Google bỏ marker) | Bridge kiểm tra `len(items) != len(batch)` thì raise → rơi về dịch từng chuỗi cho batch đó. Không mất, chỉ chậm lại. |
| Batch lỗi liên tiếp 2 lần → tắt batch (`_BATCH_FAIL_LATCH = 2`) | Rơi về từng chuỗi, không mất. |
| Google lỗi liên tiếp 3 chuỗi → ngắt mạch (`_gt = None`, chỉ còn từ điển offline) | Key không dịch được đưa lại vào hàng đợi (`incomplete_keys`); nếu cả chunk không dịch được chuỗi nào (`completedInChunk === 0`) thì job dừng để tránh lặp vô hạn — job ở trạng thái cancelled, có thể resume. Đây là cơ chế dừng an toàn, KHÔNG phải mất key — nhưng trước đây file dở dang vẫn tải được (xem §8). |
| Hủy rồi tục mất hàng đợi | Job lưu IndexedDB (`translateJobStore`) kèm `items` còn lại + `results` đã có + `provenance`; nút "Tiếp tục dịch" dùng lại job đó. Rủi ro còn tồn tại: `save(job)` lỗi IndexedDB thì catch im lặng ("mất resume, vẫn dịch tiếp") — chưa sửa (cần báo lỗi lưu job, ghi vào việc còn lại). |
| TM áp sai (job cũ không có `from`) | **ĐÃ SỬA.** TM chỉ dùng job cùng `from`; chỉ ghi TM cho key THẬT SỰ đã dịch (giá trị đổi VÀ không trong `skipped_keys` của chunk). |
| Chunk biên: chuỗi ở cuối chunk bị rơi | Không có cắt biên — chunk chia theo key (500 key/chunk), mỗi key xử lý đủ các dòng của nó; key chưa xong đưa về đầu hàng đợi. |

## 6. Gọi provider (proxy → Google → MyMemory → LibreTranslate (mặc định tắt) → offline)

| Điểm mất | Trạng thái |
|---|---|
| Fallback trả về kết quả sai ngôn ngữ mà vẫn tính là "đã dịch" | **Rủi ro còn tồn tại (chưa có kiểm tra ngược).** Bridge chấp nhận BẤT KỲ chuỗi nào provider trả về (`val = str(got)`) làm "google", không kiểm tra lại ngôn ngữ của kết quả. Nếu MyMemory/LibreTranslate trả lại nguyên văn nguồn hoặc dịch sai ngôn ngữ, dòng đó vẫn tính là xong. Lọc đầu vào (chỉ gửi đúng ngôn ngữ nguồn) đã chặn phần lớn rủi ro này, nhưng kiểm tra ngược kết quả là việc còn lại nên làm. |
| Hết quota proxy tháng → dừng Google | Dừng gọi Google, dùng từ điển offline cho phần còn lại, ghi warning rõ; job vẫn xong (với phần offline), không im lặng. |
| 429 / mạng lỗi | Token bucket gt.js (2 dồn, nạp 1/500ms) + backoff; lỗi ghi vào `network_error`, UI hiện rõ. |
| Google Translate trên mạng thật (Safari) | **Chưa kiểm chứng** — sandbox Chromium không có mạng ngoài; vẫn cần Joseph thử trên Safari thật. |

## 7. Tiến trình + hủy / tục

| Điểm mất | Trạng thái |
|---|---|
| Tiến trình không hiện số đã dịch / bỏ qua | **ĐÃ SỬA.** Dòng info trung thực cuối mỗi lần dịch: "Nguồn tiếng Anh: X đoạn · Đã dịch D/Y key (Google/glossary/TM/dedupe/batch) · Bỏ qua S đoạn (lý do...) · Mất: 0 key · Ngôn ngữ khác bị đổi: 0 key" + ví dụ thiếu/sai nếu có. Chỉ báo "Xong" khi tự kiểm tra sạch; nếu chưa xong thì báo "CHƯA xong — ... không cho tải file thiếu. Bấm 'Tiếp tục dịch'." |
| Báo "xong" khi còn dòng chưa dịch không lý do | **ĐÃ SỬA.** `state.translateCheck.complete` yêu cầu job done + hết items + missing 0 + wrong 0; dòng info không bao giờ báo xong khi còn dòng chưa dịch không lý do. |
| Hủy giữa chừng làm mất phần đã dịch | Không mất — kết quả từng phần giữ trong `job.results` + IndexedDB; hủy thì trả kết quả từng phần với `cancelled=True`. |

## 8. Ghi file output

| Điểm mất | Trạng thái |
|---|---|
| File dở dang vẫn tải được như file xong (nguyên nhân chính vụ 21:24 — 7.408/15.868 key) | **ĐÃ SỬA — cổng tải + tự kiểm tra.** Bridge `_self_check` đối chiếu output với input theo từng key (missing_keys + wrong_lang_changed, phải rỗng; ngoại lệ duy nhất là credit_key). UI: `btn-translate-download` CHẶN khi `translateCheck` chưa complete, hiện số key thiếu / ngôn ngữ khác bị đổi / ví dụ + hướng "Tiếp tục dịch". Kiểm chứng trên Chromium thật 390px: chặn khi thiếu 2 key, cho tải khi check sạch, 0 page error. |
| Ghi file sort_keys / mất thứ tự | **ĐÃ SỬA** (xem §2). File dịch ghi bằng `json.dumps(..., ensure_ascii=False, indent=2)` theo đúng thứ tự key input (`{key: translated[key] for key in kv}`). |
| Key bị mất trong dict trả về | Bridge dựng lại dict theo đúng thứ tự key input nên luôn đủ; `_self_check` bắt missing nếu có lỗi lập trình. Kiểm chứng fixture: 15.868/15.868 key, thứ tự khớp. |
| Provenance bị mất (chỉ còn dict key→value trơ trụi, (E) không biết ép về đâu) | **ĐÃ SỬA (Joseph 22:34, cho (E) ép nhiều file).** `state.lastTranslated.provenance` + `job.provenance` (IndexedDB) + nút "Tải provenance (.json)" tải file sidecar `translated.vi.provenance.json` (`format: beartea-translation-provenance/1`) ghi rõ: file game gốc, đơn vị `.assets`/`.bundle` chứa chuỗi (`source`), tên file trích xuất, cặp ngôn ngữ, số key. File dịch chính vẫn giữ dạng phẳng sạch như file en.json mẫu. Xem §10. |

## 9. Báo số lượng (cuối mỗi lần dịch)

Báo cáo trung thực và đầy đủ: tổng dòng nguồn (đếm ĐỘNG từ file đầu vào lúc chạy, theo cấu trúc của từng game — không hard-code tổng nào) / đã dịch / bỏ qua kèm lý do từng dòng / mất (phải bằng 0) / ngôn ngữ khác bị đổi (phải bằng 0). Dedupe chỉ gộp để gọi API, khi ghi output trải lại đủ mọi key; báo cáo ghi rõ "N chuỗi, M chuỗi duy nhất đã gọi dịch" qua `deduped`/`unique_segments` của bridge.

Số liệu thật trên fixture Passpartout (mock Google, đếm động — xem `exp/translate_recheck_results.md`): en 1.091 dòng → đã dịch 1.079, 12 dòng chưa dịch đều có lý do đúng (lệch khe/dấu/dữ liệu), 0 dòng sai; zh 1.764 dòng đếm theo khe → 1.744 dòng Trung thật, dịch đủ 100%; mất key 0; dòng phát hiện mạnh không phải nguồn mà bị dịch 0.

## 10. Provenance cho (E) ép nhiều file (Joseph 22:34)

Bản dịch giữ đầy đủ provenance ở 3 chỗ:

1. **Trong phiên:** `state.lastTranslated.provenance` = `{origin, gameFile, extractName, source, locale, from, to, keys, at}`; job Google lưu thêm `job.provenance` vào IndexedDB (tải lại trang vẫn còn).
2. **File sidecar:** nút "Tải provenance (.json)" → `translated.vi.provenance.json` (`format: "beartea-translation-provenance/1"`, kèm `translationFile` + `note`). Cùng cổng tải với bản dịch (chưa xong thì chặn cả hai).
3. **Nguồn gốc được suy ra từ đâu:** `bridge.extract` đã trả mỗi file kèm `source` (đơn vị Unity chứa chuỗi, vd `.../resources.assets`, `game.zip::Data/x.assets`) + tên file game đang nạp (`state.file.name`). Tab Dịch chỉ GIỮ LẠI, không bịa thêm. `origin` có 3 giá trị: `"extract"` (nạp từ file vừa trích xuất trong phiên), `"upload"` (tải file JSON lên — chỉ biết tên file), `"manual"` (dán tay — (E) sẽ phải hỏi lại nguồn gốc).

(E) dùng provenance để: nhóm các file dịch theo `(gameFile, source)`, ép từng file vào đúng đơn vị đó trong file gốc, báo xung đột khi 2 file trùng key. Kiểm chứng UI (Chromium 390px): `translateProvenanceFor` với entry giả cho ra đúng `origin: "extract"`, `gameFile: "9kings.ipa"`, `source: "9kings.ipa::Payload/9Kings.app/Data/resources.assets"`; sidecar tải về khớp provenance trong phiên.

## 11. Việc còn lại (không giấu)

1. `read_extracted` giới hạn 4MB — file trích xuất lớn có thể bị đọc thiếu (chưa đo được file nào vượt; cần nâng giới hạn hoặc đọc theo trang).
2. `translateJobStore.save(job)` lỗi IndexedDB thì catch im lặng — mất khả năng resume mà không báo.
3. Không kiểm tra ngược ngôn ngữ của kết quả provider trả về (§6) — fallback trả sai vẫn tính là xong.
4. Chuỗi Latin yếu gán nhầm en / kanji Nhật gán nhầm zh (§2) — giới hạn của phát hiện rule-based, chỉ giảm được bằng ngữ cảnh (khe/vị trí) hoặc từ điển lớn hơn.
5. Google Translate trên mạng thật + nút Lưu/Chia sẻ trên Safari thật vẫn cần Joseph thử trên thiết bị (sandbox không có mạng ngoài; Chromium không phải Safari).
6. Vỏ trang raw ~372KB vẫn vượt 100KB (item6, đã biết từ trước — app.js monolith; đợt này tăng thêm do code mới).

Harness: `exp/verify_analysis_translate_full.mjs` — 33/33 PASS (xem `exp/analysis_translate_full_results.md`).
