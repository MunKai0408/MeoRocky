#!/usr/bin/env python3
"""Sinh rust/src/lang_tables.rs tu pb3/sdk/langdetect.py.

Bang tu vung/dau dac trung (~2500 tu, 21 ngon ngu) duoc lay TRUC TIEP tu
module Python (import that) roi ghi ra Rust — khong chep tay, khong lech.
Chay lai moi khi langdetect.py thay doi:

    python3 rust/gen_lang_tables.py

Dau ra: rust/src/lang_tables.rs (ghi de). Cac danh sach tu duoc sap xep
de Rust dung binary_search (khong can HashSet).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "pb3"))

from sdk import langdetect as ld  # noqa: E402


def _rust_str(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= o < 0x7F:
            out.append(ch)
        else:
            out.append("\\u{%x}" % o)
    return '"' + "".join(out) + '"'


def main():
    lines = []
    lines.append("//! Bang tra cuu ngon ngu — SINH TU DONG tu pb3/sdk/langdetect.py.")
    lines.append("//! Khong sua tay; chay `python3 rust/gen_lang_tables.py` khi bang")
    lines.append("//! Python thay doi. Cac danh sach tu da sap xep (binary_search).")
    lines.append("")
    lines.append("//! Dinh dang: (ma ngon ngu, chuoi ky tu dac trung).")
    lines.append("pub static DISTINCTIVE: &[(&str, &str)] = &[")
    for lang, chars in ld._DISTINCTIVE.items():
        lines.append("    (%s, %s)," % (_rust_str(lang), _rust_str("".join(sorted(chars)))))
    lines.append("];")
    lines.append("")
    lines.append("/// Tu chuc nang theo ngon ngu (danh sach tu da sap xep).")
    lines.append("pub static FUNCTION_WORDS: &[(&str, &[&str])] = &[")
    for lang, words in ld._FUNCTION_WORDS.items():
        ws = ", ".join(_rust_str(w) for w in sorted(words))
        lines.append("    (%s, &[%s])," % (_rust_str(lang), ws))
    lines.append("];")
    lines.append("")
    lines.append("/// Chu chi co o phon the (thong ke Hans/Hant).")
    lines.append("pub static TRAD_ONLY: &str = %s;" % _rust_str("".join(sorted(ld._TRAD_ONLY))))
    lines.append("/// Chu chi co o gian the (thong ke Hans/Hant).")
    lines.append("pub static SIMP_ONLY: &str = %s;" % _rust_str("".join(sorted(ld._SIMP_ONLY))))
    lines.append("")
    out_path = os.path.join(HERE, "src", "lang_tables.rs")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%d bytes, %d langs, %d distinctive)"
          % (out_path, os.path.getsize(out_path), len(ld._FUNCTION_WORDS),
             len(ld._DISTINCTIVE)))


if __name__ == "__main__":
    main()
