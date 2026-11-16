# Mục 9 — Đánh giá decoder WASM cho texture/âm thanh (2026-11-16)

**Kết luận: KHÔNG build decoder trong đợt này. Chỉ thêm ghi chú UI
“Asset nhị phân (texture/âm thanh): chỉ đọc, dùng bản iSH”.**

## 1. Texture Unity cần gì

Texture trong file Unity (`.assets` / bundle) không nằm trong container
chuẩn nào cả — chúng là **khối nén GPU thô** (raw blocks) nhúng trong
SerializedFile: DXT1/DXT5 (= BC1/BC3), ASTC, ETC2, PVRTC, cộng thêm
texture không nén (RGBA32/RGB24). Muốn xem trước trên web phải: (a) bóc
được byte texture thô từ `Texture2D` trong SerializedFile (bridge hiện
chưa expose byte này — chỉ liệt kê TextAsset), rồi (b) giải mã từng
định dạng block sang RGBA để vẽ lên canvas.

## 2. Thư viện đã xem (tìm bằng web search 2026-11-16)

| Ứng viên | Kết quả |
|---|---|
| **Basis Universal transcoder WASM** (~500 KiB, Apache-2.0, BinomialLLC/basis_universal; dùng trong Three.js/Babylon/PlayCanvas qua KTX2Loader) | **Không dùng được cho Unity.** Nó chỉ transcode container `.basis`/`.ktx2` (ETC1S/UASTC supercompressed) sang định dạng GPU — không giải mã khối DXT/ASTC/ETC2/PVRTC thô. Texture Unity không phải `.basis`/`.ktx2`. |
| Decoder DXT (BC1/BC3) thuần JS | Có các implementation nhỏ (vài trăm dòng), định dạng đơn giản nhất — khả thi nhất, nhưng chỉ phủ 1 trong 4 định dạng. |
| Decoder ASTC / ETC2 / PVRTC thuần JS/WASM | Không tìm được thư viện nhỏ, đã kiểm chứng, phủ đủ: ASTC là spec phức tạp (14 kích thước block, nhiều mode); ETC2 tương tự; PVRTC là định dạng độc quyền, khó nhất. Không có “1 thư viện nhỏ lo hết”. |
| **@wasm-audio-decoders/ogg-vorbis** (~99 KiB minified, libvorbis, npm, có demo) | Nhỏ và đã kiểm chứng cho **Ogg Vorbis thuần**. Nhưng `AudioClip` của Unity trên iOS thường là Vorbis bọc trong FMOD/FSB, không phải file `.ogg` thuần — cần bóc + gỡ framing FSB trước, rồi mới đưa vào decoder. MP3/AAC cần decoder khác nữa. Safari `decodeAudioData` cũng không giải Ogg trên iOS cũ (< 18.5). |

## 3. Vì sao không làm đợt này

1. **Chưa có nhu cầu đã kiểm chứng:** Joseph chưa từng yêu cầu xem
   texture/nghe âm thanh trên web; nhu cầu đã kiểm chứng là dịch text
   (mục 2) và extract/inspect/mod text. Bản iSH đã lo được texture/âm
   thanh.
2. **Không có 1 decoder nhỏ lo hết:** cần ít nhất 4 decoder texture +
   2–3 decoder âm thanh + plumbing bóc byte thô từ SerializedFile
   (bridge chưa có) — đó là 1 phase riêng, không phải “thêm tối thiểu”.
3. **Rủi ro/rào cản Safari:** thêm WASM phải vendor cùng origin (như
   Pyodide), chạy trong module worker, tốn RAM tab iPhone; mỗi decoder
   là 1 bề mặt lỗi mới trên thiết bị thật mà chưa có cách kiểm chứng
   nhanh.
4. **Basis Universal là ngõ cụt cho Unity:** thư viện WASM “chuẩn” nhất
   cũng không giải được khối thô của Unity — dễ hiểu lầm là “đã có
   decoder” rồi.

## 4. Đã làm đợt này: ghi chú UI (kiểm chứng tĩnh)

Thêm đúng câu này vào `site/index.html`, 4 chỗ:

- Tab Unity: “Asset nhị phân (texture/âm thanh): chỉ đọc, dùng bản iSH.”
- Tab Mod: cùng câu.
- Tab Chẩn đoán: cùng câu.
- Mục “Giới hạn của bản web”: “**Asset nhị phân (texture/âm thanh): chỉ
  đọc, dùng bản iSH.** Bản web xem được tên, kích thước, hex, chuỗi text
  trong asset, nhưng không giải mã texture (DXT/ASTC/ETC2/PVRTC) hay âm
  thanh (Vorbis/MP3/AAC) để xem trước — xem texture/nghe âm thanh thì
  dùng bản iSH.”

Đồng thời ghi thêm đoạn tương tự vào `site/README.md` (mục giới hạn).

**Không thêm dependency nào.** Kiểm chứng: `exp/verify_item2.mjs`
(check `static_wiring`: câu ghi chú xuất hiện 4 lần trong index.html;
`static_no_coi_no_key` xanh).

## 5. Đợt decoder tương lai cần gì (nếu Joseph yêu cầu)

1. **Bridge expose byte thô:** parser `Texture2D`/`AudioClip` trong
   SerializedFile trả về (width, height, format, bytes) — việc này
   thuộc cây PB3, làm trên iSH trước, port sang web sau.
2. **Texture — làm DXT trước:** decoder BC1/BC3 thuần JS (nhỏ nhất,
   dễ kiểm chứng nhất bằng fixture), vẽ RGBA lên canvas; ASTC/ETC2/
   PVRTC đánh giá riêng sau (có thể cần port C sang WASM).
3. **Âm thanh — làm Ogg Vorbis trước:** vendor
   `@wasm-audio-decoders/ogg-vorbis` cùng origin + parser framing
   FSB/FMOD tối thiểu để lấy stream Vorbis thuần; xuất PCM → WAV blob
   → thẻ `<audio>`; MP3/AAC sau (hoặc dùng `decodeAudioData` của Safari
   cho MP3/AAC vì Safari giải mã sẵn 2 định dạng này).
4. **Kiểm chứng trên Safari thật** với 1 texture DXT + 1 clip Vorbis
   từ IPA 9 Kings trước khi nhận là “xong”.
