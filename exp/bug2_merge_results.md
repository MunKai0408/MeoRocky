# Harness hồi quy BUG#2 — kết quả

Tiêu chí: đúng 1 file/locale, đủ key theo build, 0 key generic, StringTable dạng Collection/Key, cùng tập key, rác vứt hết.

Tổng: PASS 4, FAIL 0, bỏ qua 0

## build68 — PASS
- [x] chỉ 3 file locale trong out_dir — ['en.json', 'zh-CN.json', 'zh-TW.json']
- [x] zip chỉ chứa 3 file locale — ['en.json', 'zh-CN.json', 'zh-TW.json']
- [x] en.json == en-build68.json (từng key/giá trị) — en=2086 key, expected=2086 key
- [x] en.json 2086 key — 2086
- [x] zh-CN 2086 key — 2086
- [x] zh-CN cùng tập key với en — 0
- [x] zh-CN 0 key generic — []
- [x] zh-TW 2086 key — 2086
- [x] zh-TW cùng tập key với en — 0
- [x] zh-TW 0 key generic — []
- [x] en 0 key generic — []
- [x] bridge ok — 

## 9kings-v1.0.6 — PASS
- [x] chỉ file locale trong out_dir — ['en.json', 'zh-CN.json', 'zh-TW.json']
- [x] zip chỉ chứa file locale — ['en.json', 'zh-CN.json', 'zh-TW.json']
- [x] en.json 2252 key — 2252
- [x] en.json 0 key generic — []
- [x] zh-CN.json 2252 key — 2252
- [x] zh-CN.json 0 key generic — []
- [x] zh-TW.json 2252 key — 2252
- [x] zh-TW.json 0 key generic — []
- [x] zh-CN.json cùng tập key với en.json — 0
- [x] zh-TW.json cùng tập key với en.json — 0
- [x] en.json == file mẫu en.json — khác 0 key
- [x] zh-TW.json == file mẫu zh-TW.json (trừ key fallback ST zh) — khác 26 key, trong đó 26 key fallback cho phép
- [x] bridge ok — 

## pb3-v1.1.7 — PASS
- [x] chỉ file locale trong out_dir — ['en.json', 'zh-CN.json']
- [x] zip chỉ chứa file locale — ['en.json', 'zh-CN.json']
- [x] en.json 2348 key — 2348
- [x] en.json 0 key generic — []
- [x] zh-CN.json 2348 key — 2348
- [x] zh-CN.json 0 key generic — []
- [x] zh-CN.json cùng tập key với en.json — 0
- [x] bridge ok — 

## ek2-v0.0.18 — PASS
- [x] chỉ file locale trong out_dir — ['en.json']
- [x] zip chỉ chứa file locale — ['en.json']
- [x] en.json 1344 key — 1344
- [x] en.json 0 key generic — []
- [x] bridge ok — 
