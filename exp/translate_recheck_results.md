# Đối chiếu file dịch Passpartout — trước/sau sửa (phần A)

Ngày: 2026-11-16. Fixture hồi quy: 2 file thật Joseph gửi 21:24, KHÔNG commit vào repo.

- Input: `~/workspace/user/files/resources.monobehaviour.json` — Passpartout, **15.868 key** (1.091 key gốc không hậu tố + các biến thể ngôn ngữ #2..#581).
- Output dịch CŨ của tool: `~/workspace/user/files/translated.vi.json` — chỉ **7.408 key**.

## 1. File dịch cũ sai ở đâu (đã đối chiếu trực tiếp bằng Python)

| Chỉ số | File cũ | Ghi chú |
|---|---|---|
| Tổng key | 7.408 / 15.868 (47%) | Thiếu **8.460 key** |
| Key gốc (không hậu tố) | 537 / 1.091 | Mất **554 key gốc** |
| Hậu tố tối đa | #15 | Mất toàn bộ hậu tố **#16 trở lên** (566 nhóm hậu tố biến mất) |
| Tiếng Anh #2 | 483 / 983 | Mất ~500 câu tiếng Anh |
| Key giữ nguyên giá trị | 3.226 | Không dịch |
| Key bị thay đổi giá trị | 4.182 | Trong đó có dịch sai ngôn ngữ |

### Số của Joseph và số đo từ file (cả hai đều ghi lại, không khẳng định bên nào sai)

Joseph đếm: **513 dòng đã dịch sang tiếng Việt + 578 dòng còn tiếng Anh gốc = 1.091 dòng** (có thể anh đếm từ màn hình tiến trình của tool lúc chạy).

Đo trực tiếp từ 2 file (định nghĩa: mỗi key gốc = 1 dòng tiếng Anh — lấy #2 nếu có, không thì lấy chính key gốc, vd 108 dòng hải tặc):

- Tổng dòng tiếng Anh: **1.091** (983 dòng #2 + 108 dòng hải tặc mà chính key gốc đã là tiếng Anh, vd `Aristocrat_Marie.s28` "I'm out of words. Too lazy. Arrrr.", "The cap'n is impressed!").
- Đã dịch sang tiếng Việt (giá trị trong file dịch có dấu tiếng Việt): **513** (trong đó #2: 481, key gốc hải tặc: 32).
- Chưa dịch: **578** — trong đó **575 dòng MẤT HẲN khỏi file dịch**, chỉ 2 dòng còn nguyên giá trị gốc và 1 dòng đổi mà không có dấu tiếng Việt.

**Kết luận đã sửa:** lỗi chính KHÔNG phải translator bỏ qua, mà là **FILE DỊCH LÀM MẤT KEY** — 575/578 dòng chưa dịch là do key biến mất khỏi output.

### Dịch sai ngôn ngữ (đã xác nhận từng cặp trong 2 file)

| Ngôn ngữ gốc | Câu gốc | File dịch cũ |
|---|---|---|
| Đức | `Wie Extraordinär!` | `Wie Extrardär!` (mất chữ) |
| Đức | `Unerhört!` | `Unerhort!` |
| Đức | `Die Wahl der Farben könnte verbessert werden.` | `Die Wahl der Farben könnte veressert werden.` |
| Nga | `Терпеть не могу вторичность.` | `Bạn không cần phải làm gì cả.` (sai hoàn toàn) |
| Thụy Điển | `Enastående!` | `Vui vẻ!` |
| Key gốc đổi giá trị | 257/537 (48%) | — |
| #12 đổi giá trị | 194/500 | — |

## 2. Nguyên nhân gốc (điều tra trong code, không đoán)

1. **Bridge hard-code ngôn ngữ nguồn** (`site/pb3/bridge.py`): `translate_json_google` có `src = "vi" if to == "en" else "en"` (không có tham số src); `translate_json` hard-code `source_lang="en"`. Đã tái hiện bằng mock Google: signature cũ không có tham số src; mock nhận đúng 1 batch với `sl="en"` chứa cả 8 chuỗi không nằm trong từ điển (Đức, Bồ Đào Nha, Nga, phồn thể, giản thể, Anh, Nhật, Hàn) — 7/7 chuỗi KHÔNG phải tiếng Anh bị gửi đi như tiếng Anh.
2. **TM (Translation Memory) bị nhiễm độc** (`site/app.js`): `buildTranslateTM(to)` nạp TM từ tối đa 5 job DONE gần nhất cùng target, key CHỈ theo giá trị nguồn (không có ngôn ngữ nguồn); `applyJobTM` áp TM cho mọi key còn lại mà không kiểm tra ngôn ngữ; `job.tm` ghi lại MỌI cặp đã xong, kể cả cặp mà giá trị nguồn không phải ngôn ngữ nguồn.
3. **Mất key — cơ chế đã chứng minh:** `bridge.translate_json_google` và `sdk.translate.translate_table` đều GIỮ ĐỦ key trong dict trả về — bridge/translate_table vô tội về mất key. Nút "Tải bản dịch (.json)" tải đúng nội dung ô translate-output mà KHÔNG kiểm tra tính đầy đủ — file dở dang vẫn tải được như file xong. Job dừng giữa chừng (hết quota / Google lỗi liên tiếp 3 chuỗi → ngắt mạch → chỉ còn từ điển offline → key không dịch được lại vào hàng đợi → dừng) rồi file thiếu vẫn được tải về. Bằng chứng số học: output keys KHÔNG phải prefix đơn giản theo thứ tự file (input đã sắp A–Z): trong 7.408 key đầu của input, 6.929 có mặt trong output; nhóm base: giữ đủ 478, giữ một phần 107 (mất trung bình 7,9 biến thể/nhóm), mất hẳn 506 — phù hợp với "job dừng quanh chunk 15–16 + TM hits rải rác khắp file".
4. **Thẻ đóng Unity không được che** (`sdk/translate.py`, phát hiện thêm khi đối chiếu): regex placeholder `<[A-Za-z][^>]*>` chỉ che thẻ MỞ (`<b>`, `<size=28>`), không che thẻ ĐÓNG (`</b>`, `</size>`). Dòng chỉ còn thẻ đóng vẫn có chữ Latin (`size`, `b`) nên bị coi là tiếng Anh yếu rồi gửi đi dịch — chuỗi rich-text Hàn/Trung bị đổi.

## 3. Chạy lại luồng dịch với bản sửa trên đúng file input (mock Google, không mạng thật)

Harness `exp/verify_analysis_translate_full.mjs` section 4 — mock Google trả `"VI:<text>"`, đếm ĐỘNG từ file đầu vào lúc chạy (không hard-code tổng). Ground truth theo khe chỉ dùng cho 858 nhóm đối thoại đủ 15 biến thể (bare + #2..#15); nhóm ngắn hơn / key `textasset.*` dùng hậu tố làm bộ đếm va chạm, không phải khe ngôn ngữ, nên không dùng làm ground truth.

### Nguồn tiếng Anh (src=en)

| Chỉ số | Kết quả |
|---|---|
| Tổng key input → output | **15.868 → 15.868, thứ tự khớp 100%** (cũ: 7.408) |
| Dòng tiếng Anh đếm được (động) | **1.091** |
| Đã dịch (đổi giá trị) | **1.079** |
| Chưa dịch | **12 — tất cả đều có lý do đúng, 0 dòng sai** |
| Nhóm đối thoại, dòng Anh #2/#8 | 1.690 / 1.716 (26 dòng chưa dịch đều là lệch khe/dấu — xem §4) |
| Dòng phát hiện MẠNH là ngôn ngữ khác mà vẫn bị dịch | **0** |
| Self-check (missing / wrong_lang_changed) | **0 / 0** |

### Nguồn tiếng Trung (src=zh)

| Chỉ số | Kết quả |
|---|---|
| Dòng tiếng Trung đếm được (động, #14 + #15) | **1.764** |
| Đã dịch (đổi giá trị) | **1.744** |
| Chưa dịch | **20 — tất cả đều không phải tiếng Trung (xem §4), 0 dòng sai** |
| Nhóm đối thoại, dòng Trung #14/#15 | 1.714 / 1.716 |
| Dòng phát hiện MẠNH là ngôn ngữ khác mà vẫn bị dịch | **0** |
| Self-check (missing / wrong_lang_changed) | **0 / 0** |

## 4. Từng dòng chưa dịch — danh sách đầy đủ, có lý do

### 12 dòng tiếng Anh chưa dịch (src=en) — KHÔNG dòng nào là lỗi

| Key | Giá trị | Lý do đúng |
|---|---|---|
| `Aristocrat_Elizabeth.s9#2` | `Déjà vu...` | Tiếng Pháp (phát hiện mạnh fr) — nằm lệch khe #2 |
| `Expressionist_Ronald.s37#2` | `¡Passpartout solamente hace obras de arte!` | Tiếng Tây Ban Nha (mạnh es) — lệch khe #2 |
| `Expressionist_Ronald.s38` (bare) | `Passpartout tworzy tylko arcydzieła!` | Tiếng Ba Lan (mạnh pl) — key gốc là tiếng Ba Lan |
| `Hipster_Benjamin.s1#2` | `Trés bien!` | Tiếng Pháp — lưới an toàn dấu (é) chặn |
| `Hipster_Benjamin.s2#2` | `Ho ho! Originalité!` | Phát hiện mạnh it (`ho`) — không phải tiếng Anh |
| `Hipster_Benjamin.s6#2` | `Sacre bleu! Where's l'originalité?` | Tiếng Pháp — lưới an toàn dấu (é) chặn |
| `Hipster_Benjamin.s8#2` | `Originalité s'il vous plaît!` | Tiếng Pháp (mạnh fr) |
| `Minimalist_Claude.s2#2` | `Trés bien!` | Tiếng Pháp — lưới an toàn dấu |
| `Minimalist_Claude.s5#2` | `Génial!` | Tiếng Pháp — lưới an toàn dấu |
| `UnityCloudBuildManifest.scriptable.s1` | `{..."cloudBuildTargetName":"passpartout-ios"...}` | Dữ liệu JSON, không phải câu ngôn ngữ |
| `textasset.s14#2` | `繁體中文` | Tiếng Trung (mạnh zh) — nhãn tên ngôn ngữ, lệch khe #2 |
| `textasset.s17#2` | `日本語` | Tiếng Nhật (phát hiện zh do toàn kanji — giới hạn đã biết) |

Ba lỗi phát hiện sai trước đây đã sửa và khóa bằng test: `A most liberal choice of colour.` (từ `most` vừa là so sánh nhất tiếng Anh vừa là `most` tiếng Hungary), `I would've chosen such fantastic colours.` (mảnh vỡ `ve` của `would've` trùng `ve` tiếng Thổ), `dove-grey` ×5 (`dove` vừa là tên màu tiếng Anh vừa là `dove` tiếng Ý = "ở đâu"). Cả ba giờ về đúng tiếng Anh nhờ hòa phiếu trong danh sách từ chức năng.

### 20 dòng tiếng Trung chưa dịch (src=zh) — KHÔNG dòng nào là tiếng Trung thật

Tất cả đều là chuỗi giao diện/tên kỹ thuật tiếng Anh nằm ở khe va chạm #14/#15 của nhóm không phải đối thoại (vd `textasset.s1` có 581 biến thể — hậu tố ở đây chỉ là bộ đếm va chạm, không phải khe ngôn ngữ):

`Er…` ×2 (`Business_Giorgio.s24#14/#15`), `Normal` ×2, `Pressed` ×2, `Highlighted` ×3, `Disabled` ×2, `UnityEngine.UI.Image, UnityEngine.UI`, `UnityEngine.Object, UnityEngine`, `ChangeMasterVolume`, `SendMessageUpwards` ×2, `Master the Craft`, `OnClearButtonPressed`, `OnDoneButtonPressed`, `UnityEngine.Rendering.DebugUI+ColorField, Unity.RenderPipelines.Core.R…`.

Nói cách khác: **số dòng tiếng Trung thật là 1.744, đã dịch đủ 1.744 (100%)** — con số 1.764 chỉ là đếm theo khe #14/#15, thừa 20 dòng không phải tiếng Trung.

## 5. Ngôn ngữ khác có còn bị đổi không

- **src=en, 858 nhóm đối thoại:** 1.986 key ở khe không phải tiếng Anh bị đổi giá trị (theo khe: es 235, fr 259, nl 364, pl 204, pt 270, ro 208, sv 187, bare/Đức 245, zh 5+5, ko 4). Nhưng kiểm tra THEO TỪNG DÒNG: **0 dòng nào phát hiện MẠNH là ngôn ngữ khác mà vẫn bị dịch**. 1.986 dòng đó toàn là chuỗi Latin ngắn không có từ chức năng/dấu (vd `Extraordinaire!`, `Fiasko.`, `Smuts.`, `Buitengewoon!`) — không bộ phát hiện rule-based nào phân biệt được với tiếng Anh nếu không có ngữ cảnh. Đây là giới hạn đã biết, ghi rõ chứ không giấu. Khe script mạnh (ja/ko/ru/zh) chỉ đổi 14 key — toàn là dòng chỉ có tên riêng (`- Marie` ở cuối thư rich-text Hàn/Trung), Google thật gần như chắc chắn trả nguyên tên.
- **src=zh:** 18 key ở khe khác bị đổi, **0 dòng phát hiện MẠNH không phải tiếng Trung mà bị dịch** — cả 18 đều là chuỗi Nhật thuần kanji không kana (vd `天才。`, `退屈。`, `不完全。`), không phân biệt được với tiếng Trung nếu không có ngữ cảnh (giới hạn đã biết).
- **Đường dịch chính mà Joseph sẽ dùng (file tách sạch en.json/zh.json) không gặp vấn đề này** — file đã chỉ chứa một ngôn ngữ nên không còn gì để lọc sai.

## 6. Kết luận

| Tiêu chí (Joseph 21:32) | Trước | Sau |
|---|---|---|
| Key giữ đủ | 7.408/15.868 (47%) | **15.868/15.868 (100%), thứ tự khớp** |
| Dòng tiếng Anh dịch đủ | 513/1.091 | **1.079/1.091, 12 dòng còn lại đều không phải tiếng Anh (có lý do từng dòng)** |
| Dòng tiếng Trung dịch đủ | (không đo trước) | **1.744/1.744 dòng Trung thật (100%)** |
| Ngôn ngữ khác bị đổi sai | Đức/Nga/Thụy Điển bị "dịch" hỏng | **0 dòng phát hiện mạnh bị đổi sai** |
| Mất key | 8.460 | **0** |
| Chưa dịch không lý do | 575 (do mất key) | **0** |

Harness: `exp/verify_analysis_translate_full.mjs` section 4 (6 mục PASS) — xem thêm `exp/analysis_translate_full_results.md`.
