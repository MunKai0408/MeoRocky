"""Bộ đọc/ghi TOML tối giản bằng Python thuần.

Lý do tồn tại: iSH thường chỉ có Python 3.9, KHÔNG có tomllib (3.11+). Nếu có
tomllib thì config.py ưu tiên dùng nó; module này là phương án dự phòng và được
đối chiếu với tomllib trong test.

Hỗ trợ: bảng [a.b], key = value, chuỗi "..."/'...', số nguyên (cho phép _),
số thực, true/false, mảng (nhiều dòng được) chứa giá trị đơn giản, chú thích #.
KHÔNG hỗ trợ: inline table, array-of-tables [[x]], datetime, chuỗi nhiều dòng.
Gặp cú pháp ngoài phạm vi -> TomlError (không đoán).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class TomlError(ValueError):
    def __init__(self, line: int, msg: str):
        super().__init__("dòng %d: %s" % (line, msg))
        self.line = line
        self.msg = msg


_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_INT = re.compile(r"^[+-]?(0|[1-9](_?[0-9])*)$")
_FLOAT = re.compile(r"^[+-]?(0|[1-9](_?[0-9])*)(\.[0-9](_?[0-9])*)?([eE][+-]?[0-9](_?[0-9])*)?$")


def _strip_comment(s: str) -> str:
    out, q, esc = [], None, False
    for ch in s:
        if q:
            out.append(ch)
            if q == '"' and not esc and ch == "\\":
                esc = True
                continue
            if ch == q and not esc:
                q = None
            esc = False
        else:
            if ch == "#":
                break
            if ch in "\"'":
                q = ch
            out.append(ch)
    return "".join(out)


def _split_dotted(s: str, ln: int) -> List[str]:
    parts, cur, q, i = [], "", None, 0
    while i < len(s):
        ch = s[i]
        if q:
            if ch == q:
                q = None
            else:
                cur += ch
        elif ch in "\"'":
            q = ch
        elif ch == ".":
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur.strip())
    if q or any(p == "" for p in parts):
        raise TomlError(ln, "tên khóa không hợp lệ: %r" % s)
    return parts


def _parse_string(s: str, i: int, ln: int) -> Tuple[str, int]:
    q = s[i]
    i += 1
    buf = []
    while i < len(s):
        ch = s[i]
        if q == "'":
            if ch == "'":
                return "".join(buf), i + 1
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            return "".join(buf), i + 1
        if ch == "\\":
            i += 1
            if i >= len(s):
                break
            e = s[i]
            if e in _ESC:
                buf.append(_ESC[e])
            elif e in "uU":
                n = 4 if e == "u" else 8
                hx = s[i + 1:i + 1 + n]
                if len(hx) != n or not re.match(r"^[0-9a-fA-F]+$", hx):
                    raise TomlError(ln, "escape unicode sai")
                try:
                    buf.append(chr(int(hx, 16)))
                except ValueError:
                    raise TomlError(ln, "escape unicode ngoài phạm vi: \\U%s" % hx)
                i += n
            else:
                raise TomlError(ln, "escape không hợp lệ: \\%s" % e)
        else:
            buf.append(ch)
        i += 1
    raise TomlError(ln, "chuỗi chưa đóng")


def _parse_value(s: str, i: int, ln: int) -> Tuple[Any, int]:
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s):
        raise TomlError(ln, "thiếu giá trị")
    ch = s[i]
    if ch in "\"'":
        if s.startswith(ch * 3, i):
            raise TomlError(ln, "chuỗi nhiều dòng không hỗ trợ")
        return _parse_string(s, i, ln)
    if ch == "[":
        items, i = [], i + 1
        while True:
            while i < len(s) and s[i] in " \t,\n":
                if s[i] == ",":
                    # cho phép dấu phẩy thừa sau phần tử cuối, nhưng không đầu mảng
                    if not items:
                        raise TomlError(ln, "dấu phẩy thừa trong mảng")
                i += 1
            if i >= len(s):
                raise TomlError(ln, "mảng chưa đóng")
            if s[i] == "]":
                return items, i + 1
            v, i = _parse_value(s, i, ln)
            items.append(v)
            while i < len(s) and s[i] in " \t\n":
                i += 1
            if i < len(s) and s[i] not in ",]":
                raise TomlError(ln, "thiếu dấu phẩy trong mảng")
    if ch == "{":
        raise TomlError(ln, "inline table không hỗ trợ")
    j = i
    while j < len(s) and s[j] not in ",] \t\n":
        j += 1
    tok = s[i:j]
    if tok == "true":
        return True, j
    if tok == "false":
        return False, j
    if _INT.match(tok):
        return int(tok.replace("_", "")), j
    if _FLOAT.match(tok):
        return float(tok.replace("_", "")), j
    raise TomlError(ln, "giá trị không hiểu: %r" % tok)


def _brackets_open(s: str) -> int:
    depth, q, esc = 0, None, False
    for ch in s:
        if q:
            if q == '"' and not esc and ch == "\\":
                esc = True
                continue
            if ch == q and not esc:
                q = None
            esc = False
        elif ch in "\"'":
            q = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth


def loads(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    cur = root
    defined = set()
    lines = text.splitlines()
    n, i = len(lines), 0
    while i < n:
        ln = i + 1
        s = _strip_comment(lines[i]).strip()
        i += 1
        if not s:
            continue
        if s.startswith("[["):
            raise TomlError(ln, "array-of-tables không hỗ trợ")
        if s.startswith("["):
            if not s.endswith("]"):
                raise TomlError(ln, "tiêu đề bảng thiếu ']'")
            path = _split_dotted(s[1:-1].strip(), ln)
            key = tuple(path)
            if key in defined:
                raise TomlError(ln, "bảng bị khai báo lặp: %s" % ".".join(path))
            defined.add(key)
            cur = root
            for k in path:
                nxt = cur.setdefault(k, {})
                if not isinstance(nxt, dict):
                    raise TomlError(ln, "'%s' không phải bảng" % k)
                cur = nxt
            continue
        if "=" not in s:
            raise TomlError(ln, "thiếu dấu '='")
        k_raw, _, v_raw = s.partition("=")
        keys = _split_dotted(k_raw.strip(), ln)
        v_raw = v_raw.strip()
        while v_raw.startswith("[") and _brackets_open(v_raw) > 0:   # mảng nhiều dòng
            if i >= n:
                raise TomlError(ln, "mảng chưa đóng")
            v_raw += "\n" + _strip_comment(lines[i]).strip()
            i += 1
        val, end = _parse_value(v_raw, 0, ln)
        if v_raw[end:].strip():
            raise TomlError(ln, "thừa nội dung sau giá trị")
        tgt = cur
        for k in keys[:-1]:
            tgt = tgt.setdefault(k, {})
            if not isinstance(tgt, dict):
                raise TomlError(ln, "'%s' không phải bảng" % k)
        if keys[-1] in tgt:
            raise TomlError(ln, "khóa lặp: %s" % keys[-1])
        tgt[keys[-1]] = val
    return root


# ---------------------------------------------------------------- ghi

def _fmt_key(k: str) -> str:
    return k if _BARE.match(k) else '"%s"' % k.replace("\\", "\\\\").replace('"', '\\"')


def _fmt_str(s: str) -> str:
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _fmt_str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt_value(x) for x in v) + "]"
    raise TypeError("kiểu không ghi được: %s" % type(v).__name__)


def dumps(data: Dict[str, Any]) -> str:
    """Ghi dict (giá trị đơn giản + bảng lồng) ra TOML. Chú thích cũ KHÔNG được giữ."""
    out: List[str] = []

    def emit(d: Dict[str, Any], prefix: List[str]) -> None:
        scalars = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in d.items() if isinstance(v, dict)]
        if prefix and (scalars or not tables):
            out.append("[%s]" % ".".join(_fmt_key(p) for p in prefix))
        for k, v in scalars:
            out.append("%s = %s" % (_fmt_key(k), _fmt_value(v)))
        if scalars or (prefix and not tables):
            out.append("")
        for k, v in tables:
            emit(v, prefix + [k])

    emit(data, [])
    return "\n".join(out).rstrip("\n") + "\n"
