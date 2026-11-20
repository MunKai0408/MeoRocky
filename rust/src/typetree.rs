//! Đọc giá trị theo type tree — port của `read_typetree_value`
//! trong `pb3/sdk/unity.py`.
//!
//! Đầu vào: cây node (JSON `{name, byte_size, children}` — chỉ 3
//! trường mà reader dùng), bytes của object, vị trí bắt đầu, chiều
//! endian. Đầu ra: JSON `{"value": ..., "end": pos}` — dict giữ đúng
//! thứ tự field (serde_json `preserve_order`), bytes (byte[] giải
//! UTF-8 lỗi) mã hóa `{"$bytes": "<base64>"}` để phía Python khôi
//! phục đúng kiểu. Thông điệp lỗi giữ nguyên bản Python.

use serde::Deserialize;
use serde_json::{json, Map, Value};

/// Node type tree (chỉ các trường reader dùng).
#[derive(Debug, Clone, Deserialize)]
pub struct TypeTreeNode {
    pub name: String,
    pub byte_size: i64,
    #[serde(default)]
    pub children: Vec<TypeTreeNode>,
}

fn align4(pos: usize) -> usize {
    (pos + 3) & !3
}

/// Node Array: đúng 1 con, con đó có đúng 2 con tên common_795 + common_106.
fn is_array(node: &TypeTreeNode) -> bool {
    if node.children.len() != 1 {
        return false;
    }
    let arr = &node.children[0];
    if arr.children.len() != 2 {
        return false;
    }
    let size_node = &arr.children[0];
    size_node.byte_size == 4
        && size_node.children.is_empty()
        && arr.children[0].name == "common_795"
        && arr.children[1].name == "common_106"
}

/// repr() của Python cho một chuỗi (dạng '...') — chỉ dùng trong
/// thông điệp lỗi "vector ... dài ... không hợp lệ".
fn python_repr_str(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for ch in s.chars() {
        match ch {
            '\'' => out.push_str("\\'"),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('\'');
    out
}

fn read_i16(data: &[u8], pos: usize, little: bool) -> i16 {
    let b: [u8; 2] = data[pos..pos + 2].try_into().unwrap();
    if little {
        i16::from_le_bytes(b)
    } else {
        i16::from_be_bytes(b)
    }
}

fn read_i32(data: &[u8], pos: usize, little: bool) -> i32 {
    let b: [u8; 4] = data[pos..pos + 4].try_into().unwrap();
    if little {
        i32::from_le_bytes(b)
    } else {
        i32::from_be_bytes(b)
    }
}

fn read_i64(data: &[u8], pos: usize, little: bool) -> i64 {
    let b: [u8; 8] = data[pos..pos + 8].try_into().unwrap();
    if little {
        i64::from_le_bytes(b)
    } else {
        i64::from_be_bytes(b)
    }
}

/// Đọc 1 giá trị theo node type tree tại pos. Trả (giá trị JSON, vị trí mới).
/// dict = struct, list = vector, str = chuỗi (byte[] giải UTF-8 lỗi thì
/// trả {"$bytes": base64} để bên gọi bỏ qua, không hỏng cả object).
/// Hỏng cấu trúc -> Err với đúng thông điệp của bản Python.
pub fn read_typetree_value(
    data: &[u8],
    node: &TypeTreeNode,
    mut pos: usize,
    little: bool,
) -> Result<(Value, usize), String> {
    let n = data.len();
    if !node.children.is_empty() {
        if is_array(node) {
            let elem = &node.children[0].children[1];
            pos = align4(pos);
            if pos + 4 > n {
                return Err(format!("đọc vượt quá dữ liệu (4 byte tại {})", pos));
            }
            let count = read_i32(data, pos, little);
            pos += 4;
            if !(0..=1_000_000).contains(&count) {
                return Err(format!(
                    "vector {} dài {} không hợp lệ",
                    python_repr_str(&node.name),
                    count
                ));
            }
            let count = count as usize;
            if elem.byte_size == 1 && elem.children.is_empty() {
                if pos + count > n {
                    return Err(format!("chuỗi dài {} vượt quá dữ liệu", count));
                }
                let raw = &data[pos..pos + count];
                pos = align4(pos + count);
                match std::str::from_utf8(raw) {
                    Ok(s) => return Ok((Value::String(s.to_string()), pos)),
                    Err(_) => {
                        let mut m = Map::new();
                        m.insert("$bytes".to_string(), Value::String(base64_encode(raw)));
                        return Ok((Value::Object(m), pos));
                    }
                }
            }
            let mut vals: Vec<Value> = Vec::with_capacity(count);
            for _ in 0..count {
                pos = align4(pos);
                let (v, p) = read_typetree_value(data, elem, pos, little)?;
                pos = p;
                vals.push(v);
            }
            return Ok((Value::Array(vals), pos));
        }
        let mut out = Map::new();
        for c in &node.children {
            pos = align4(pos);
            let (v, p) = read_typetree_value(data, c, pos, little)?;
            pos = p;
            out.insert(c.name.clone(), v);
        }
        return Ok((Value::Object(out), pos));
    }
    if node.byte_size == 0 {
        return Ok((Value::Null, pos));
    }
    if node.byte_size == 1 {
        if pos + 1 > n {
            return Err(format!("đọc vượt quá dữ liệu (1 byte tại {})", pos));
        }
        return Ok((Value::from(data[pos] as i64), pos + 1));
    }
    if node.byte_size == 2 {
        if pos + 2 > n {
            return Err(format!("đọc vượt quá dữ liệu (2 byte tại {})", pos));
        }
        return Ok((Value::from(read_i16(data, pos, little) as i64), pos + 2));
    }
    if node.byte_size == 4 {
        if pos + 4 > n {
            return Err(format!("đọc vượt quá dữ liệu (4 byte tại {})", pos));
        }
        return Ok((Value::from(read_i32(data, pos, little) as i64), pos + 4));
    }
    if node.byte_size == 8 {
        if pos + 8 > n {
            return Err(format!("đọc vượt quá dữ liệu (8 byte tại {})", pos));
        }
        return Ok((Value::from(read_i64(data, pos, little)), pos + 8));
    }
    Err(format!(
        "node lá byte_size={} ({}) chưa hiểu",
        node.byte_size, node.name
    ))
}

/// Base64 chuẩn (có padding) — chỉ mã hóa, phục vụ {"$bytes": ...}.
pub fn base64_encode(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(ALPHABET[(n >> 18 & 63) as usize] as char);
        out.push(ALPHABET[(n >> 12 & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(n >> 6 & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

/// Điểm vào JSON: node_json là cây node, data là bytes object.
/// Trả JSON {"value": ..., "end": pos}.
pub fn parse_typetree_value_json(
    node_json: &str,
    data: &[u8],
    pos: usize,
    little: bool,
) -> Result<String, String> {
    let node: TypeTreeNode =
        serde_json::from_str(node_json).map_err(|e| format!("node JSON hỏng: {}", e))?;
    let (value, end) = read_typetree_value(data, &node, pos, little)?;
    Ok(json!({"value": value, "end": end}).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(name: &str, byte_size: i64, children: Vec<TypeTreeNode>) -> TypeTreeNode {
        TypeTreeNode {
            name: name.to_string(),
            byte_size,
            children,
        }
    }

    fn leaf(name: &str, byte_size: i64) -> TypeTreeNode {
        node(name, byte_size, vec![])
    }

    #[test]
    fn base64_vectors() {
        assert_eq!(base64_encode(b""), "");
        assert_eq!(base64_encode(b"f"), "Zg==");
        assert_eq!(base64_encode(b"fo"), "Zm8=");
        assert_eq!(base64_encode(b"foo"), "Zm9v");
        assert_eq!(base64_encode(b"\xff\x00\xfe"), "/wD+");
    }

    #[test]
    fn struct_and_leaves() {
        // struct { a: i32, b: u8, c: i16, d: i64, e: (byte_size 0) }
        let root = node(
            "root",
            0,
            vec![
                leaf("a", 4),
                leaf("b", 1),
                leaf("c", 2),
                leaf("d", 8),
                leaf("e", 0),
            ],
        );
        // a=0x01020304 LE, b=0x05, c=0x0607, d=0x08090a0b0c0d0e0f
        let mut data: Vec<u8> = Vec::new();
        data.extend_from_slice(&0x01020304i32.to_le_bytes());
        data.push(0x05);
        data.extend_from_slice(&[0, 0, 0]); // đệm tới bội 4 (căn 4 trước c)
        data.extend_from_slice(&0x0607i16.to_le_bytes());
        data.extend_from_slice(&[0, 0]); // đệm tới bội 4 (căn 4 trước d)
        data.extend_from_slice(&0x08090a0b0c0d0e0fi64.to_le_bytes());
        let (v, end) = read_typetree_value(&data, &root, 0, true).unwrap();
        assert_eq!(v["a"], 0x01020304);
        assert_eq!(v["b"], 5);
        assert_eq!(v["c"], 0x0607);
        assert_eq!(v["d"].as_i64(), Some(0x08090a0b0c0d0e0f));
        assert!(v["e"].is_null());
        assert_eq!(end, data.len());
    }

    #[test]
    fn string_array_node() {
        // Node chuỗi: 1 con Array { size: i32 (common_795), data: u8[] (common_106) }.
        let str_node = node(
            "m_Name",
            0,
            vec![node(
                "Array",
                0,
                vec![
                    leaf("common_795", 4),
                    leaf("common_106", 1),
                ],
            )],
        );
        // count=5 "Hello", căn 4 sau chuỗi (5 -> 8).
        let mut data: Vec<u8> = Vec::new();
        data.extend_from_slice(&5i32.to_le_bytes());
        data.extend_from_slice(b"Hello");
        data.extend_from_slice(&[0, 0, 0]);
        let (v, end) = read_typetree_value(&data, &str_node, 0, true).unwrap();
        assert_eq!(v, Value::String("Hello".to_string()));
        assert_eq!(end, 12);
    }

    #[test]
    fn bad_utf8_returns_bytes_marker() {
        let str_node = node(
            "m_Name",
            0,
            vec![node(
                "Array",
                0,
                vec![
                    leaf("common_795", 4),
                    leaf("common_106", 1),
                ],
            )],
        );
        let mut data: Vec<u8> = Vec::new();
        data.extend_from_slice(&3i32.to_le_bytes());
        data.extend_from_slice(&[0xFF, 0xFE, 0x41]);
        data.push(0);
        let (v, _end) = read_typetree_value(&data, &str_node, 0, true).unwrap();
        assert_eq!(v["$bytes"], "//5B");
    }

    #[test]
    fn vector_of_structs() {
        // vector struct { x: i32 } với 2 phần tử. Node phần tử tên
        // "common_106", con của nó là các field của struct.
        let elem = node("common_106", 0, vec![leaf("x", 4)]);
        let vec_node = node(
            "items",
            0,
            vec![node(
                "Array",
                0,
                vec![leaf("common_795", 4), elem],
            )],
        );
        let mut data: Vec<u8> = Vec::new();
        data.extend_from_slice(&2i32.to_le_bytes());
        data.extend_from_slice(&10i32.to_le_bytes());
        data.extend_from_slice(&20i32.to_le_bytes());
        let (v, end) = read_typetree_value(&data, &vec_node, 0, true).unwrap();
        assert_eq!(v[0]["x"], 10);
        assert_eq!(v[1]["x"], 20);
        assert_eq!(end, 12);
    }

    #[test]
    fn corrupt_errors_match_python() {
        let root = leaf("a", 4);
        let err = read_typetree_value(&[0x01, 0x02], &root, 0, true).unwrap_err();
        assert_eq!(err, "đọc vượt quá dữ liệu (4 byte tại 0)");
        let bad_leaf = leaf("weird", 3);
        let err = read_typetree_value(&[0, 0, 0], &bad_leaf, 0, true).unwrap_err();
        assert_eq!(err, "node lá byte_size=3 (weird) chưa hiểu");
        // Vector count vượt 1_000_000.
        let vec_node = node(
            "v",
            0,
            vec![node(
                "Array",
                0,
                vec![leaf("common_795", 4), leaf("common_106", 1)],
            )],
        );
        let data = 2_000_000i32.to_le_bytes().to_vec();
        let err = read_typetree_value(&data, &vec_node, 0, true).unwrap_err();
        assert_eq!(err, "vector 'v' dài 2000000 không hợp lệ");
    }
}
