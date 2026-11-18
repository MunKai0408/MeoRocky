# Kết quả kiểm chứng tái tạo atlas SDF TextMeshPro

Chạy: `python3 exp/verify_sdf_regen.py` (stdlib, so SDF SDK tự tạo với
atlas thật trong game; ngưỡng đạt: median glyph MAE < 3.0).

## Lần 2 (2026-11-18 ~03:00 +07) — 8 asset, 3 PASS, 3 game

| Game | Asset | Layout | Font | Median MAE | Mean | <3 | Kết quả |
|------|-------|--------|------|-----------|------|----|---------|
| PB3 | Aniara_Graphics SDF | old | embedded | 2.05 | 4.62 | 61% | PASS |
| PB3 | Aniara_Light SDF | old | embedded | 1.52 | 5.69 | 69% | PASS |
| PB3 | Aniara_Regular SDF | old | embedded | 2.36 | 5.81 | 60% | PASS |
| 9K | LiberationSans SDF | new | embedded | 20.18 | 21.68 | 0% | FAIL (padding sai) |
| EK2 | COURBD SDF | old | PROXY Liberation Mono | 38.97 | 41.16 | 1% | FAIL (proxy) |
| EK2 | LiberationSans SDF | new | embedded | 4.20 | 5.86 | 35% | FAIL |
| PB3 | LiberationSans SDF | new | embedded | 4.20 | 5.86 | 35% | FAIL |
| PB3 | NotoSans-Regular SDF | new | embedded | 7.55 | 8.08 | 9% | FAIL |

Kết luận chung: FAIL (cần mọi asset PASS + ≥3 game).

## Phân tích nguyên nhân (đã đo thật)

- **Padding** (FaceInfo mới không có padding, verify mặc định 5):
  9K LiberationSans thực tế pad=9 (đo riêng: pad=5 → MAE 20.31,
  pad=9 → 4.56). NotoSans thực tế pad=5 (pad=5 → 8.75, pad=9 → 20.65).
- **Font-version** (không phải bug generator): placement đúng tuyệt đối
  (shift-search: shift (0,0) tốt nhất cho 'H'/'!'/'A'/'e'; glyph tốt
  MAE 1.15–2.21). Font nhúng tốt hơn font hệ thống (LiberationSans:
  nhúng 4.01 vs hệ thống 5.07). MAE còn lại (4.20 / 7.55) là do font
  nhúng khác version với font đã build atlas gốc.
- **COURBD**: không có font gốc nhúng; proxy Liberation Mono khác
  hình dạng Courier New nên MAE cao (38.97) — giới hạn của proxy,
  không phải của generator.
- **ContinuumBold (PvZ Fusion)**: atlas BITMAP (95% pixel 0/255, chỉ
  5% trung gian; SDF thật 68–73%) — không thuộc phạm vi tái tạo SDF.
  Verify + SDK nay phát hiện và skip/fail-closed rõ ràng
  (`sdf.is_sdf_atlas`, ngưỡng 15% pixel trung gian).

## Khảo sát IPA (11 IPA)

- Unity + có TMP SDF: 9K, EK2, PB3, Tom, PvZ Fusion.
- Không phải Unity: DST, balatro, fortune-mill, carrion, Dead Cells
  (Heaps).
- Afterimage: Drive quota exceeded, không tải được (03:11 +07).
- Tom: TMP asset có nhưng không có font nguồn nhúng → không verify được.

## Mô hình SDF (đã xác nhận bằng đo thật)

- value = clamp(round(126.5 + s·255/(2·spread))), spread = padding + 1.
- Atlas BOTTOM-UP; rect y layout cũ từ TOP, layout mới từ BOTTOM.
- Generator: parse font (TTF glyf + CFF/Type 2) → flatten outline →
  SDF chính xác (khớp brute-force, diff 0) → pack atlas → bảng glyph
  2 layout (36B cũ / 52B mới).
