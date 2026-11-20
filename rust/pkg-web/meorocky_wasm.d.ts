/* tslint:disable */
/* eslint-disable */

/**
 * Biến thể Hán (Hant/Hans) — chỉ để thống kê. Trả null khi không xác định.
 */
export function rust_detect_chinese_variant(text: string): string | undefined;

/**
 * Phát hiện ngôn ngữ của 1 chuỗi. Trả JSON `["zh","strong"]` hoặc `[null,"none"]`.
 */
export function rust_detect_language(text: string): string;

/**
 * Phát hiện ngôn ngữ hàng loạt. Vào: JSON mảng chuỗi. Ra: JSON mảng
 * `[mã, độ tin cậy]` (mã null khi không có chữ cái).
 */
export function rust_detect_languages(texts_json: string): string;

/**
 * Học ngôn ngữ theo vị trí trong các nhóm lặp. Vào: JSON mảng nhóm
 * (mỗi nhóm là mảng chuỗi). Ra: JSON mảng mã (null = chưa đủ bằng chứng).
 */
export function rust_infer_positions(groups_json: string, min_votes: number, min_share: number): string;

/**
 * Mã hóa LZ4 block dạng literal-only (hợp lệ, không nén) — ngang bản Python.
 */
export function rust_lz4_compress_block_literals(data: Uint8Array | number[]): Uint8Array;

/**
 * Giải nén 1 LZ4 block. `uncompressed_size` < 0 = không ghi kích thước
 * (giải tới hết input, giới hạn 128 MB như bản Python).
 */
export function rust_lz4_decompress_block(data: Uint8Array | number[], uncompressed_size: number): Uint8Array;

/**
 * Giải nén 1 LZ4 frame (kèm kiểm tra checksum khi cờ bật).
 */
export function rust_lz4_decompress_frame(data: Uint8Array | number[]): Uint8Array;

/**
 * Đọc 1 giá trị theo type tree. Vào: node JSON `{name, byte_size,
 * children}`, bytes object, vị trí bắt đầu, `little_endian`.
 * Ra: JSON `{"value": ..., "end": pos}`. Lỗi: đúng thông điệp Python.
 */
export function rust_parse_typetree_value(node_json: string, data: Uint8Array | number[], pos: number, little_endian: boolean): string;

/**
 * xxhash32(seed=0 mặc định ở phía gọi khi cần).
 */
export function rust_xxhash32(data: Uint8Array | number[], seed: number): number;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly rust_detect_chinese_variant: (a: number, b: number) => [number, number];
    readonly rust_detect_language: (a: number, b: number) => [number, number];
    readonly rust_detect_languages: (a: number, b: number) => [number, number, number, number];
    readonly rust_infer_positions: (a: number, b: number, c: number, d: number) => [number, number, number, number];
    readonly rust_lz4_compress_block_literals: (a: number, b: number) => [number, number];
    readonly rust_lz4_decompress_block: (a: number, b: number, c: number) => [number, number, number, number];
    readonly rust_lz4_decompress_frame: (a: number, b: number) => [number, number, number, number];
    readonly rust_parse_typetree_value: (a: number, b: number, c: number, d: number, e: number, f: number) => [number, number, number, number];
    readonly rust_xxhash32: (a: number, b: number, c: number) => number;
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_realloc: (a: number, b: number, c: number, d: number) => number;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __externref_table_dealloc: (a: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
