//! LZ4 thuần Rust — port byte-identical của `pb3/sdk/lz4pure.py`.
//!
//! - LZ4 block: giải mã đầy đủ (kiểm tra offset/kích thước), mã hóa
//!   dạng literal-only (hợp lệ, không nén — phương án dự phòng).
//! - LZ4 frame: phát hiện + giải mã (khối nén/không nén, kiểm tra
//!   checksum nội dung bằng xxhash32 khi cờ bật).
//! - Thông điệp lỗi giữ nguyên tiếng Việt như bản Python để phía
//!   Python bọc thành PB3Error giống hệt (xem `UnityBackend`).

/// Kích thước giải nén tối đa mặc định (như `lz4pure.decompress_block`).
pub const DEFAULT_MAX_SIZE: usize = 128 * 1024 * 1024;

const P1: u32 = 2654435761;
const P2: u32 = 2246822519;
const P3: u32 = 3266489917;
const P4: u32 = 668265263;
const P5: u32 = 374761393;
const MASK: u32 = 0xFFFF_FFFF;

const LZ4_FRAME_MAGIC: [u8; 4] = [0x04, 0x22, 0x4D, 0x18];

fn rotl(x: u32, r: u32) -> u32 {
    x.rotate_left(r)
}

/// xxHash32 thuần Rust (dùng kiểm checksum của LZ4 frame).
pub fn xxhash32(data: &[u8], seed: u32) -> u32 {
    let n = data.len();
    let mut pos = 0usize;
    let h_init: u32 = if n >= 16 {
        let mut v1 = seed.wrapping_add(P1).wrapping_add(P2);
        let mut v2 = seed.wrapping_add(P2);
        let mut v3 = seed;
        let mut v4 = seed.wrapping_sub(P1);
        while pos + 16 <= n {
            let lane = |k: usize| u32::from_le_bytes(data[pos + k..pos + k + 4].try_into().unwrap());
            v1 = rotl(v1.wrapping_add(lane(0).wrapping_mul(P2)), 13).wrapping_mul(P1);
            v2 = rotl(v2.wrapping_add(lane(4).wrapping_mul(P2)), 13).wrapping_mul(P1);
            v3 = rotl(v3.wrapping_add(lane(8).wrapping_mul(P2)), 13).wrapping_mul(P1);
            v4 = rotl(v4.wrapping_add(lane(12).wrapping_mul(P2)), 13).wrapping_mul(P1);
            pos += 16;
        }
        rotl(v1, 1)
            .wrapping_add(rotl(v2, 7))
            .wrapping_add(rotl(v3, 12))
            .wrapping_add(rotl(v4, 18))
    } else {
        seed.wrapping_add(P5)
    };
    let mut h = h_init.wrapping_add(n as u32);
    while pos + 4 <= n {
        let lane = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap());
        h = rotl(h.wrapping_add(lane.wrapping_mul(P3)), 17).wrapping_mul(P4);
        pos += 4;
    }
    while pos < n {
        h = rotl(h.wrapping_add((data[pos] as u32).wrapping_mul(P5)), 11).wrapping_mul(P1);
        pos += 1;
    }
    h ^= h >> 15;
    h = h.wrapping_mul(P2);
    h ^= h >> 13;
    h = h.wrapping_mul(P3);
    h ^= h >> 16;
    h & MASK
}

/// Đọc độ dài mở rộng (byte 255 nối tiếp); trả (giá trị, vị trí mới).
fn read_ext(data: &[u8], mut pos: usize) -> Result<(usize, usize), String> {
    let mut total: usize = 0;
    loop {
        if pos >= data.len() {
            return Err("khối LZ4 cắt cụt ở độ dài mở rộng".to_string());
        }
        let b = data[pos];
        pos += 1;
        total += b as usize;
        if b != 255 {
            break;
        }
    }
    Ok((total, pos))
}

/// Giải mã một LZ4 block.
///
/// `uncompressed_size`: kích thước đã biết (bundle Unity) — kiểm tra đúng
/// kích thước; `None` (block độc lập trong frame): giải mã tới hết input,
/// giới hạn bằng `max_size`.
pub fn decompress_block(
    data: &[u8],
    uncompressed_size: Option<i64>,
    max_size: usize,
) -> Result<Vec<u8>, String> {
    if let Some(size) = uncompressed_size {
        if size < 0 {
            return Err("kích thước giải nén âm".to_string());
        }
    }
    let limit = match uncompressed_size {
        Some(size) => size as usize,
        None => max_size,
    };
    if uncompressed_size == Some(0) {
        if !data.is_empty() {
            return Err("khối LZ4 có dữ liệu nhưng kích thước 0".to_string());
        }
        return Ok(Vec::new());
    }
    let mut out: Vec<u8> = Vec::new();
    let mut pos = 0usize;
    let n = data.len();
    loop {
        if pos >= n {
            return Err("khối LZ4 cắt cụt ở token".to_string());
        }
        let token = data[pos];
        pos += 1;
        let mut lit_len = (token >> 4) as usize;
        if lit_len == 15 {
            let (ext, p) = read_ext(data, pos)?;
            lit_len += ext;
            pos = p;
        }
        if pos + lit_len > n {
            return Err("khối LZ4: literal vượt quá dữ liệu".to_string());
        }
        if out.len() + lit_len > limit {
            return Err("khối LZ4: giải nén vượt kích thước cho phép".to_string());
        }
        out.extend_from_slice(&data[pos..pos + lit_len]);
        pos += lit_len;
        if pos == n {
            break;
        }
        if pos + 2 > n {
            return Err("khối LZ4 cắt cụt ở offset".to_string());
        }
        let offset = (data[pos] as usize) | ((data[pos + 1] as usize) << 8);
        pos += 2;
        if offset == 0 || offset > out.len() {
            return Err(format!("khối LZ4: offset không hợp lệ ({})", offset));
        }
        let mut match_len = (token & 0x0F) as usize;
        if match_len == 15 {
            let (ext, p) = read_ext(data, pos)?;
            match_len += ext;
            pos = p;
        }
        match_len += 4;
        if out.len() + match_len > limit {
            return Err("khối LZ4: match vượt kích thước cho phép".to_string());
        }
        let start = out.len() - offset;
        if offset >= match_len {
            // Không chồng lấn: sao chép theo lát.
            let old_len = out.len();
            out.resize(old_len + match_len, 0);
            out.copy_within(start..start + match_len, old_len);
        } else {
            for i in 0..match_len {
                let b = out[start + i];
                out.push(b);
            }
        }
    }
    if let Some(size) = uncompressed_size {
        if out.len() != size as usize {
            return Err(format!(
                "khối LZ4: giải nén được {}/{} byte",
                out.len(),
                size
            ));
        }
    }
    Ok(out)
}

/// Mã hóa LZ4 block dạng literal-only (hợp lệ, không nén).
pub fn compress_block_literals(data: &[u8]) -> Vec<u8> {
    let mut out: Vec<u8> = Vec::new();
    let n = data.len();
    if n < 15 {
        out.push((n as u8) << 4);
    } else {
        out.push(0xF0);
        let mut rest = n - 15;
        while rest >= 255 {
            out.push(255);
            rest -= 255;
        }
        out.push(rest as u8);
    }
    out.extend_from_slice(data);
    out
}

/// True khi 4 byte đầu là magic của LZ4 frame.
pub fn is_lz4_frame(data: &[u8]) -> bool {
    data.len() >= 4 && data[..4] == LZ4_FRAME_MAGIC
}

fn max_block_size(id: u8) -> Option<usize> {
    match id {
        4 => Some(64 * 1024),
        5 => Some(256 * 1024),
        6 => Some(1024 * 1024),
        7 => Some(4 * 1024 * 1024),
        _ => None,
    }
}

/// Giải mã LZ4 block mà match có thể trỏ về dữ liệu của block trước
/// (`prefix` = toàn bộ output đã giải của các block trước trong frame).
fn decompress_block_linked(data: &[u8], prefix: &[u8]) -> Result<Vec<u8>, String> {
    let mut out: Vec<u8> = Vec::with_capacity(prefix.len() + data.len() * 2);
    out.extend_from_slice(prefix);
    let base = prefix.len();
    let mut pos = 0usize;
    let n = data.len();
    loop {
        if pos >= n {
            break;
        }
        let token = data[pos];
        pos += 1;
        let mut lit_len = (token >> 4) as usize;
        if lit_len == 15 {
            let (ext, p) = read_ext(data, pos)?;
            lit_len += ext;
            pos = p;
        }
        if pos + lit_len > n {
            return Err("khối LZ4: literal vượt quá dữ liệu".to_string());
        }
        out.extend_from_slice(&data[pos..pos + lit_len]);
        pos += lit_len;
        if pos == n {
            break;
        }
        if pos + 2 > n {
            return Err("khối LZ4 cắt cụt ở offset".to_string());
        }
        let offset = (data[pos] as usize) | ((data[pos + 1] as usize) << 8);
        pos += 2;
        if offset == 0 || offset > out.len() {
            return Err(format!("khối LZ4: offset không hợp lệ ({})", offset));
        }
        let mut match_len = (token & 0x0F) as usize;
        if match_len == 15 {
            let (ext, p) = read_ext(data, pos)?;
            match_len += ext;
            pos = p;
        }
        match_len += 4;
        let start = out.len() - offset;
        if offset >= match_len {
            let old_len = out.len();
            out.resize(old_len + match_len, 0);
            out.copy_within(start..start + match_len, old_len);
        } else {
            for i in 0..match_len {
                let b = out[start + i];
                out.push(b);
            }
        }
    }
    Ok(out[base..].to_vec())
}

/// Giải mã một LZ4 frame; kiểm tra checksum nội dung nếu có.
pub fn decompress_frame(data: &[u8]) -> Result<Vec<u8>, String> {
    if data.len() < 7 || !is_lz4_frame(data) {
        return Err("không phải LZ4 frame".to_string());
    }
    let mut pos = 4usize;
    let flg = data[pos];
    pos += 1;
    if flg >> 6 != 1 {
        return Err(format!("LZ4 frame version lạ ({})", flg >> 6));
    }
    let block_independent = flg & 0x20 != 0;
    let block_checksum = flg & 0x10 != 0;
    let has_content_size = flg & 0x08 != 0;
    let has_content_checksum = flg & 0x04 != 0;
    let has_dict_id = flg & 0x01 != 0;
    if flg & 0x02 != 0 {
        return Err("LZ4 frame có bit dự trữ".to_string());
    }
    if pos >= data.len() {
        return Err("LZ4 frame cắt cụt ở BD".to_string());
    }
    let bd = data[pos];
    pos += 1;
    let max_block = match max_block_size((bd >> 4) & 0x07) {
        Some(m) => m,
        None => return Err("LZ4 frame: cỡ block lạ".to_string()),
    };
    let mut content_size: Option<u64> = None;
    if has_content_size {
        if pos + 8 > data.len() {
            return Err("LZ4 frame cắt cụt ở content size".to_string());
        }
        content_size = Some(u64::from_le_bytes(data[pos..pos + 8].try_into().unwrap()));
        pos += 8;
    }
    if has_dict_id {
        if pos + 4 > data.len() {
            return Err("LZ4 frame cắt cụt ở dict id".to_string());
        }
        let dict_id = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap());
        pos += 4;
        if dict_id != 0 {
            return Err(format!(
                "LZ4 frame cần từ điển ngoài (dict_id={}), chưa hỗ trợ",
                dict_id
            ));
        }
    }
    if pos >= data.len() {
        return Err("LZ4 frame cắt cụt ở header checksum".to_string());
    }
    let header = &data[4..pos];
    let expect_hc = ((xxhash32(header, 0) >> 8) & 0xFF) as u8;
    if data[pos] != expect_hc {
        return Err("LZ4 frame: header checksum sai".to_string());
    }
    pos += 1;
    let mut out: Vec<u8> = Vec::new();
    loop {
        if pos + 4 > data.len() {
            return Err("LZ4 frame cắt cụt ở block size".to_string());
        }
        let mut block_size = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap()) as usize;
        pos += 4;
        if block_size == 0 {
            break;
        }
        let uncompressed_flag = block_size & 0x8000_0000 != 0;
        block_size &= 0x7FFF_FFFF;
        if block_size > max_block + max_block / 255 + 16 && !uncompressed_flag {
            return Err("LZ4 frame: block quá lớn".to_string());
        }
        if pos + block_size > data.len() {
            return Err("LZ4 frame: block cắt cụt".to_string());
        }
        let block = &data[pos..pos + block_size];
        pos += block_size;
        if block_checksum {
            if pos + 4 > data.len() {
                return Err("LZ4 frame cắt cụt ở block checksum".to_string());
            }
            let expect_bc = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap());
            pos += 4;
            if xxhash32(block, 0) != expect_bc {
                return Err("LZ4 frame: block checksum sai".to_string());
            }
        }
        if uncompressed_flag {
            out.extend_from_slice(block);
        } else if block_independent {
            let dec = decompress_block(block, None, max_block)?;
            out.extend_from_slice(&dec);
        } else {
            let dec = decompress_block_linked(block, &out)?;
            out.extend_from_slice(&dec);
        }
    }
    if has_content_checksum {
        if pos + 4 > data.len() {
            return Err("LZ4 frame cắt cụt ở content checksum".to_string());
        }
        let expect_cc = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap());
        if xxhash32(&out, 0) != expect_cc {
            return Err("LZ4 frame: content checksum sai".to_string());
        }
    }
    if let Some(size) = content_size {
        if out.len() as u64 != size {
            return Err(format!(
                "LZ4 frame: giải nén được {}/{} byte",
                out.len(),
                size
            ));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xxhash32_empty() {
        assert_eq!(xxhash32(b"", 0), 0x02CC5D05);
    }

    #[test]
    fn xxhash32_known() {
        // Giá trị đối chiếu với lz4pure.xxhash32 (Python) seed 0.
        assert_eq!(xxhash32(b"hello", 0), 0xFB0077F9);
        assert_eq!(xxhash32(b"hello world", 0), 0xCEBB6622);
        assert_eq!(xxhash32(b"1234567890123456", 0), 0x03BF5152);
        assert_eq!(xxhash32(&[0u8; 256].iter().enumerate().map(|(i, _)| i as u8).collect::<Vec<u8>>(), 0), 0x59441253);
    }

    #[test]
    fn block_roundtrip_literals() {
        let plain = b"Xin chao BearTea - cau noi LZ4 thu nghiem 12345";
        let block = compress_block_literals(plain);
        assert_eq!(decompress_block(&block, Some(plain.len() as i64), DEFAULT_MAX_SIZE).unwrap(), plain);
    }

    #[test]
    fn block_empty() {
        assert_eq!(decompress_block(b"", Some(0), DEFAULT_MAX_SIZE).unwrap(), b"");
        assert!(decompress_block(b"\x00", Some(0), DEFAULT_MAX_SIZE).is_err());
    }

    #[test]
    fn block_bad_offset() {
        // token literal 1 byte 'A', rồi offset 0 -> lỗi.
        let bad = vec![0x10, b'A', 0x00, 0x00];
        let err = decompress_block(&bad, Some(5), DEFAULT_MAX_SIZE).unwrap_err();
        assert!(err.contains("offset không hợp lệ (0)"), "err={}", err);
    }

    #[test]
    fn block_truncated_token() {
        let err = decompress_block(b"", Some(5), DEFAULT_MAX_SIZE).unwrap_err();
        assert!(err.contains("cắt cụt ở token"), "err={}", err);
    }

    #[test]
    fn block_wrong_size() {
        // literal-only cho 4 byte nhưng khai 999.
        let block = compress_block_literals(b"abcd");
        let err = decompress_block(&block, Some(999), DEFAULT_MAX_SIZE).unwrap_err();
        assert!(err.contains("giải nén được 4/999 byte"), "err={}", err);
    }

    #[test]
    fn frame_detect() {
        assert!(is_lz4_frame(&[0x04, 0x22, 0x4D, 0x18, 0x00]));
        assert!(!is_lz4_frame(b"UnityFS"));
        assert!(!is_lz4_frame(b""));
    }

    #[test]
    fn frame_roundtrip_via_literals() {
        // Frame thủ công: magic + FLG(version 1, independent, no checksum)
        // + BD(64K) + header checksum + 1 block literal-only + end mark.
        let plain = b"frame test 1234567890 frame test";
        let block = compress_block_literals(plain);
        let mut frame: Vec<u8> = vec![0x04, 0x22, 0x4D, 0x18, 0x60, 0x40];
        let hc = ((xxhash32(&frame[4..6], 0) >> 8) & 0xFF) as u8;
        frame.push(hc);
        frame.extend_from_slice(&(block.len() as u32).to_le_bytes());
        frame.extend_from_slice(&block);
        frame.extend_from_slice(&[0, 0, 0, 0]);
        assert_eq!(decompress_frame(&frame).unwrap(), plain);
    }
}
