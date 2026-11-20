# Rust/WASM hotspots cho tool web MeoRocky

Giữ tool Python hiện tại; chỉ port những chỗ **nặng** sang Rust/WASM
(Duy chốt 18:25 + 18:27 20/11: ngưỡng THẤP — chỗ nhẹ thì thôi, chỗ hơi
nặng trở lên đều port). Python cũ giữ nguyên làm fallback/mặc định cho
tới khi Rust được kiểm chứng — không xóa code Python, không đổi hành vi
mặc định khi chưa có số đo thắng rõ ràng.

## Đã port (v0.1.0)

| # | Chỗ port | Đo trước (CPython/JS, máy này) | Đo sau (WASM, Node, máy này) |
|---|----------|-------------------------------|------------------------------|
| 1 | Đọc typetree (`read_typetree_value`) | 2718,5 ms / 1805 object (664 obj/s); object lớn nhất 205,8 ms | 573–718 ms / 1805 object (2514–3149 obj/s); object lớn nhất 31–68 ms — **~4,1×** |
| 2 | LZ4 block/frame + xxhash32 | lz4pure 25,3 MB/s; lz4js 172–247 MB/s | **~141–360 MB/s** (toàn bộ 106 block Kusa: 1,76× lz4js; block 128 KiB: 1,46× lz4js; **~24× lz4pure**) |
| 3 | Phát hiện ngôn ngữ (`detect_language`) | Python ~2917–3958 chuỗi/s | **~33k–82k chuỗi/s** (đơn/batch) — **~10–20×** |

Số đo chi tiết + bảng xếp hạng đầy đủ xem báo cáo của agent (branch
`feat/rust-hotspots`). Số đo trên VM dùng chung, dao động theo tải máy;
tỉ lệ trên là median nhiều lần chạy, không phải lần chạy tốt nhất.

## Cấu trúc

- `Cargo.toml` — crate `meorocky-wasm` (cdylib + rlib), deps: `wasm-bindgen`,
  `serde`, `serde_json` (preserve_order — giữ đúng thứ tự field typetree).
- `src/lz4.rs` — port byte-identical của `pb3/sdk/lz4pure.py` (block,
  frame, xxhash32, literal-only encoder). Thông điệp lỗi giữ nguyên
  tiếng Việt để phía Python bọc thành PB3Error giống hệt.
- `src/langdetect.rs` — port của `pb3/sdk/langdetect.py` (script counts,
  dấu đặc trưng, từ chức năng, infer_positions). Bảng từ vựng ở
  `src/lang_tables.rs` (sinh tự động, xem dưới).
- `src/typetree.rs` — port của `read_typetree_value` (`pb3/sdk/unity.py`).
  Vào: node JSON `{name, byte_size, children}`; ra: JSON
  `{"value": ..., "end": pos}` (bytes → `{"$bytes": base64}`).
- `src/lib.rs` — cầu nối `#[wasm_bindgen]` mỏng (core không phụ thuộc
  wasm-bindgen, test được bằng `cargo test` trên host).
- `gen_lang_tables.py` — sinh `src/lang_tables.rs` từ `pb3/sdk/langdetect.py`
  (import thật, không chép tay): `python3 rust/gen_lang_tables.py`.
- `pkg-web/` — bản build `--target web` đã commit (worker.js nạp);
  `pkg-node/` — bản build `--target nodejs` chỉ để verify bằng Node
  (gitignore, không commit).

## Build

```sh
export RUSTUP_HOME=~/workspace/rust-hotspots/.rustup
export CARGO_HOME=~/workspace/rust-hotspots/.cargo
export PATH=$CARGO_HOME/bin:$PATH
# Lần đầu: rustup target add wasm32-unknown-unknown
#          cargo install wasm-pack wasm-bindgen-cli
cd rust
cargo test && cargo clippy --all-targets        # test host + lint
cargo build --release --target wasm32-unknown-unknown
wasm-bindgen --target nodejs --out-dir pkg-node \
  target/wasm32-unknown-unknown/release/meorocky_wasm.wasm
wasm-bindgen --target web --out-dir pkg-web \
  target/wasm32-unknown-unknown/release/meorocky_wasm.wasm
```

(`wasm-pack build` cũng được nhưng nó tự biên dịch lại wasm-bindgen-cli
từ nguồn, rất chậm trên máy yếu — dùng `wasm-bindgen` đã cài sẵn.)

## Kiểm chứng (byte-identical với Python)

Ground truth sinh từ Python bằng `~/workspace/rust-hotspots/gen_vectors.py`
(block Kusa 106 + frame + lỗi; 34.603 chuỗi thật; 1805 object typetree):

```sh
node exp/verify_rust_wasm.mjs rust/pkg-node          # LZ4 + langdetect
node exp/verify_rust_wasm_typetree.mjs rust/pkg-node  # typetree (streaming)
node exp/bench_rust_node.mjs rust/pkg-node            # benchmark Rust vs lz4js
```

Python phía gọi (test cầu nối bằng module `js` giả):

```sh
cd pb3 && python3 -m unittest tests.test_rust_wasm
```

## Cách nối (Python/JS)

- `pb3/sdk/unity.py`: `UnityBackend._decompress_lz4_rs` /
  `_decompress_frame_rs` (thử sau binding, trước lz4js/lz4pure);
  `read_typetree_value` thử Rust trước, rơi về Python khi vắng cầu nối.
- `pb3/sdk/langdetect.py`: `detect_language` /
  `detect_chinese_variant` thử Rust trước, rơi về Python.
- `worker.js`: nạp `./rust/pkg-web/meorocky_wasm.js`, công bố
  `globalThis.pb3_rust_*` (lz4_decompress, lz4_decompress_frame,
  detect_language(s), detect_chinese_variant, infer_positions,
  typetree_value). Lỗi input hỏng ném `RUST_*_CORRUPT` để Python raise
  E_CORRUPT; lỗi khác đánh dấu cầu nối hỏng, rơi về đường cũ.

## Chưa port (xếp hạng sau 3 chỗ trên — xem báo cáo)

- `_mb_string_is_noise` + regex phân loại chuỗi (~7% thời gian trích xuất):
  trên mức nhẹ, ứng viên port tiếp theo (nhỏ, theo sau langdetect).
- SDF atlas / gate / repack: ngoài phạm vi web (font đã hủy; gate/repack
  chạy phía server Actions/Colab, không phải hotspot trên điện thoại).
- Zip/IPA, JSON encode: đã có mã C (nhẹ) — không port.
