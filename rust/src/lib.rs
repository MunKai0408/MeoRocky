//! MeoRocky WASM — các hotspot port sang Rust cho tool web.
//!
//! Core (lz4 / langdetect / typetree) là Rust thuần, không phụ thuộc
//! wasm-bindgen — test được bằng `cargo test` trên host. Lớp
//! `#[wasm_bindgen]` ở đây chỉ là cầu nối JS mỏng: nhận Uint8Array /
//! String, trả Uint8Array / String (JSON), lỗi ném thành JsValue mang
//! đúng thông điệp tiếng Việt của bản Python để phía Python bọc thành
//! PB3Error giống hệt.

use wasm_bindgen::prelude::*;

pub mod lang_tables;
pub mod langdetect;
pub mod lz4;
pub mod typetree;

fn js_err(msg: String) -> JsValue {
    JsValue::from_str(&msg)
}

/// xxhash32(seed=0 mặc định ở phía gọi khi cần).
#[wasm_bindgen]
pub fn rust_xxhash32(data: &[u8], seed: u32) -> u32 {
    lz4::xxhash32(data, seed)
}

/// Giải nén 1 LZ4 block. `uncompressed_size` < 0 = không ghi kích thước
/// (giải tới hết input, giới hạn 128 MB như bản Python).
#[wasm_bindgen]
pub fn rust_lz4_decompress_block(data: &[u8], uncompressed_size: i32) -> Result<Vec<u8>, JsValue> {
    let size = if uncompressed_size < 0 {
        None
    } else {
        Some(uncompressed_size as i64)
    };
    lz4::decompress_block(data, size, lz4::DEFAULT_MAX_SIZE).map_err(js_err)
}

/// Giải nén 1 LZ4 frame (kèm kiểm tra checksum khi cờ bật).
#[wasm_bindgen]
pub fn rust_lz4_decompress_frame(data: &[u8]) -> Result<Vec<u8>, JsValue> {
    lz4::decompress_frame(data).map_err(js_err)
}

/// Mã hóa LZ4 block dạng literal-only (hợp lệ, không nén) — ngang bản Python.
#[wasm_bindgen]
pub fn rust_lz4_compress_block_literals(data: &[u8]) -> Vec<u8> {
    lz4::compress_block_literals(data)
}

/// Phát hiện ngôn ngữ của 1 chuỗi. Trả JSON `["zh","strong"]` hoặc `[null,"none"]`.
#[wasm_bindgen]
pub fn rust_detect_language(text: &str) -> String {
    let (code, conf) = langdetect::detect_language(text);
    let code_json = match code {
        Some(c) => format!("\"{}\"", c),
        None => "null".to_string(),
    };
    format!("[{},\"{}\"]", code_json, conf)
}

/// Phát hiện ngôn ngữ hàng loạt. Vào: JSON mảng chuỗi. Ra: JSON mảng
/// `[mã, độ tin cậy]` (mã null khi không có chữ cái).
#[wasm_bindgen]
pub fn rust_detect_languages(texts_json: &str) -> Result<String, JsValue> {
    let texts: Vec<String> =
        serde_json::from_str(texts_json).map_err(|e| js_err(format!("texts JSON hỏng: {}", e)))?;
    let mut out = String::with_capacity(texts_json.len());
    out.push('[');
    for (i, t) in texts.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        let (code, conf) = langdetect::detect_language(t);
        match code {
            Some(c) => out.push_str(&format!("[\"{}\",\"{}\"]", c, conf)),
            None => out.push_str(&format!("[null,\"{}\"]", conf)),
        }
    }
    out.push(']');
    Ok(out)
}

/// Biến thể Hán (Hant/Hans) — chỉ để thống kê. Trả null khi không xác định.
#[wasm_bindgen]
pub fn rust_detect_chinese_variant(text: &str) -> Option<String> {
    langdetect::detect_chinese_variant(text).map(|s| s.to_string())
}

/// Học ngôn ngữ theo vị trí trong các nhóm lặp. Vào: JSON mảng nhóm
/// (mỗi nhóm là mảng chuỗi). Ra: JSON mảng mã (null = chưa đủ bằng chứng).
#[wasm_bindgen]
pub fn rust_infer_positions(
    groups_json: &str,
    min_votes: usize,
    min_share: f64,
) -> Result<String, JsValue> {
    let groups: Vec<Vec<String>> =
        serde_json::from_str(groups_json).map_err(|e| js_err(format!("groups JSON hỏng: {}", e)))?;
    let langs = langdetect::infer_positions(&groups, min_votes, min_share);
    let mut out = String::with_capacity(groups_json.len() / 4 + 2);
    out.push('[');
    for (i, l) in langs.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        match l {
            Some(c) => out.push_str(&format!("\"{}\"", c)),
            None => out.push_str("null"),
        }
    }
    out.push(']');
    Ok(out)
}

/// Đọc 1 giá trị theo type tree. Vào: node JSON `{name, byte_size,
/// children}`, bytes object, vị trí bắt đầu, `little_endian`.
/// Ra: JSON `{"value": ..., "end": pos}`. Lỗi: đúng thông điệp Python.
#[wasm_bindgen]
pub fn rust_parse_typetree_value(
    node_json: &str,
    data: &[u8],
    pos: usize,
    little_endian: bool,
) -> Result<String, JsValue> {
    typetree::parse_typetree_value_json(node_json, data, pos, little_endian).map_err(js_err)
}
