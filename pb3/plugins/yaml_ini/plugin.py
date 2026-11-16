# Plugin định dạng YAML/INI (Claude 06): rẽ nhánh theo đuôi file.
# INI (.ini/.cfg/.conf): configparser (không interpolation, giữ nguyên hoa thường,
# strict=True nên key trùng trong section -> E_CORRUPT). File không có section được
# bọc "[__pb3_root__]" để parse, ghi ngược tháo bọc. Comment bị mất khi ghi ngược
# (configparser không giữ comment).
# YAML (.yaml/.yml): parser thuần Python, tập con: map lồng nhau theo thụt đầu dòng,
# list (- item), scalar trần/nháy đơn/nháy kép, comment #, block scalar | và >
# (kèm chomping), flow map/list, anchor/alias cho scalar và collection. Tab trong
# thụt đầu dòng -> E_CORRUPT; nhiều document (---) -> E_UNSUPPORTED_FORMAT.
# Ghi ngược YAML dựng lại từ cấu trúc đã parse (comment mất, style chuẩn hóa:
# scalar trần khi an toàn, không thì nháy kép + escape).
# Kiểm tra: python3 menu.py plugin test yaml_ini
from __future__ import annotations

import configparser
import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.formats import (FormatPluginBase, decode_text, make_entries, stable_row_key,
                         value_queue)
from sdk.types import LocTable

MANIFEST = {
    "id": "yaml_ini",
    "name": "YAML/INI localization",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read", "write"],
    "maturity": "M3",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".yaml", ".yml", ".ini", ".cfg", ".conf"]},
    "write_modes": ["REBUILD_SECTION"],
    "limits": {"max_entry_bytes": 8 * 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:YamlIniPlugin",
    "selftest": True,
    "docs": "YAML (.yaml/.yml, tập con): map lồng nhau theo thụt đầu dòng, list (- item), "
            "scalar trần/nháy đơn/nháy kép, comment #, block scalar | và > (kèm chomping "
            "+/- và số thụt đầu dòng), flow map/list ({k: v}, [a, b]), anchor/alias cho "
            "scalar và collection. KHÔNG hỗ trợ: nhiều document (---), tag (!...), key "
            "phức tạp, tab trong thụt đầu dòng (lỗi), block scalar có dòng thụt sâu hơn "
            "không gấp nếp. Ghi ngược chuẩn hóa style, comment mất. INI (.ini/.cfg/.conf): "
            "configparser, key giữ nguyên hoa thường, key trùng trong section là lỗi, "
            "file không section được bọc section giả khi đọc và tháo khi ghi; comment "
            "bị mất khi ghi ngược; giá trị nhiều dòng được ghi thành dòng nối thụt đầu dòng.",
}

_INI_EXTS = (".ini", ".cfg", ".conf")
_YAML_EXTS = (".yaml", ".yml")
_ROOT_SECTION = "__pb3_root__"

# --------------------------------------------------------------------------- dùng chung
_SCALAR_NOTES = {bool: "json:boolean", int: "json:number", float: "json:number",
                 type(None): "json:null"}


def _scalar_note(v: Any) -> str:
    if isinstance(v, bool):
        return "json:boolean"
    if isinstance(v, (int, float)):
        return "json:number"
    if v is None:
        return "json:null"
    return ""


def _scalar_text(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    return json.dumps(v, ensure_ascii=False)


def _walk(value: Any, prefix: str, pairs: List[Tuple[str, str, str, List[str]]],
          stats: Dict[str, Any]) -> None:
    if isinstance(value, str):
        pairs.append((prefix, value, "ok", []))
    elif isinstance(value, dict):
        if not value:
            pairs.append((prefix, "{}", "ok", ["json:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        for k, v in value.items():
            _walk(v, (prefix + "." + str(k)) if prefix else str(k), pairs, stats)
    elif isinstance(value, list):
        if not value:
            pairs.append((prefix, "[]", "ok", ["json:complex"]))
            stats["complex"] = stats.get("complex", 0) + 1
            return
        for i, x in enumerate(value):
            _walk(x, "%s[%d]" % (prefix, i), pairs, stats)
    else:
        pairs.append((prefix, _scalar_text(value), "ok", [_scalar_note(value)]))


def _restore(value: str, notes: List[str]) -> Any:
    if "json:complex" in notes:
        try:
            return json.loads(value)
        except ValueError:
            return value
    for note in notes:
        if note in ("json:number", "json:boolean", "json:null"):
            try:
                return json.loads(value)
            except ValueError:
                return value
    return value


_INDEX_RE = re.compile(r"^(.*)\[(\d+)\]$")


def _tokenize_path(dotted: str) -> List[Tuple[str, Any]]:
    """'a.b[0].c' / '[0].k' -> [('key','a'), ('key','b'), ('idx',0), ('key','c')]."""
    tokens: List[Tuple[str, Any]] = []
    for part in dotted.split("."):
        m = re.match(r"^([^\[\]]*)((?:\[\d+\])*)$", part)
        if m is None:
            tokens.append(("key", part))
            continue
        base, idxs = m.group(1), re.findall(r"\[(\d+)\]", m.group(2))
        if base:
            tokens.append(("key", base))
        for ix in idxs:
            tokens.append(("idx", int(ix)))
    return tokens


def _set_path(obj: Any, dotted: str, value: Any) -> bool:
    """Đặt giá trị theo key chấm / key[i] / [i] ở gốc. False nếu đường dẫn không có."""
    tokens = _tokenize_path(dotted)
    cur = obj
    for n, (kind, tok) in enumerate(tokens):
        last = n == len(tokens) - 1
        if kind == "key":
            if not isinstance(cur, dict) or tok not in cur:
                return False
            if last:
                cur[tok] = value
                return True
            cur = cur[tok]
        else:
            if not isinstance(cur, list) or tok >= len(cur):
                return False
            if last:
                cur[tok] = value
                return True
            cur = cur[tok]
    return False


# --------------------------------------------------------------------------- INI
def _parse_ini_struct(text: str, path: str):
    """Trả (sections, sectionless): sections = [(tên|None, [(key, value)])]."""
    cp = configparser.ConfigParser(interpolation=None, strict=True, allow_no_value=True)
    cp.optionxform = str  # type: ignore[assignment]
    sectionless = False
    try:
        cp.read_string(text)
    except configparser.MissingSectionHeaderError:
        sectionless = True
        try:
            cp.read_string("[%s]\n%s" % (_ROOT_SECTION, text))
        except configparser.Error:
            raise PB3Error("E_CORRUPT", "INI sai cú pháp", where=path)
    except configparser.Error:
        raise PB3Error("E_CORRUPT", "INI sai cú pháp (key/section trùng hoặc sai định dạng)",
                       where=path)
    sections: List[Tuple[Optional[str], List[Tuple[str, str]]]] = []
    if sectionless:
        opts = cp._sections.get(_ROOT_SECTION, {})
        pairs = [(k, ("" if v is None else v)) for k, v in opts.items() if k != "__name__"]
        sections.append((None, pairs))
        return sections, True
    for name in cp.sections():
        opts = cp._sections.get(name, {})
        pairs = [(k, ("" if v is None else v)) for k, v in opts.items() if k != "__name__"]
        sections.append((name, pairs))
    return sections, False


def _parse_ini(data: bytes, path: str, locale: Optional[str], doc) -> Dict[str, Any]:
    sections, sectionless = _parse_ini_struct(doc.text, path)
    pairs: List[Tuple[str, str, str, List[str]]] = []
    for name, opts in sections:
        for k, v in opts:
            key = k if name is None else "%s.%s" % (name, k)
            pairs.append((key, v, "ok", []))
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    return {"entries": entries, "schema_hint": {"flavor": "ini", "sectionless": sectionless},
            "stats": {"keys": len(entries), "sections": len(sections)},
            "conflicts": conflicts, "warnings": []}


def _build_ini(table: LocTable, base_bytes: bytes) -> bytes:
    doc = decode_text(base_bytes, "base")
    sections, sectionless = _parse_ini_struct(doc.text, "base")
    want: Dict[Tuple[Optional[str], str], str] = {}
    for e in table.entries:
        if e.status not in ("ok", "empty"):
            continue
        if sectionless:
            want[(None, e.key)] = e.value
        else:
            if "." in e.key:
                sec, opt = e.key.split(".", 1)
                want[(sec, opt)] = e.value
            else:
                want[(None, e.key)] = e.value
    lines: List[str] = []
    for si, (name, opts) in enumerate(sections):
        if name is not None:
            if si:
                lines.append("")
            lines.append("[%s]" % name)
        for k, v in opts:
            new_v = want.get((name, k), v)
            parts = new_v.split("\n")
            lines.append("%s = %s" % (k, parts[0]) if name is not None else "%s=%s" % (k, parts[0]))
            for cont in parts[1:]:
                lines.append("    %s" % cont)
    out = "\n".join(lines) + ("\n" if lines else "")
    return doc.encode(out)


# --------------------------------------------------------------------------- YAML
def _strip_comment(s: str) -> str:
    """Bỏ comment # (chỉ khi # đứng đầu hoặc sau khoảng trắng, ngoài nháy)."""
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote == '"':
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
                return s[:i].rstrip()
        i += 1
    return s.rstrip()


_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_OCT_RE = re.compile(r"^0o[0-7]+$")
_FLOAT_RE = re.compile(r"^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$")

_DQ_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n",
               "v": "\v", "f": "\f", "r": "\r", "e": "\x1b", '"': '"', "/": "/",
               "\\": "\\", " ": " ", "N": "\x85", "_": "\xa0", "L": "\u2028",
               "P": "\u2029"}


def _parse_double_quoted(s: str, path: str) -> Tuple[str, int]:
    """s bắt đầu bằng \". Trả (giá trị, vị trí sau nháy đóng)."""
    out: List[str] = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == '"':
            return "".join(out), i + 1
        if ch == "\\":
            if i + 1 >= len(s):
                raise PB3Error("E_CORRUPT", "chuỗi nháy kép kết thúc bằng backslash", where=path)
            nxt = s[i + 1]
            if nxt in ("x", "u", "U"):
                n = {"x": 2, "u": 4, "U": 8}[nxt]
                hexpart = s[i + 2:i + 2 + n]
                if len(hexpart) != n or not all(c in "0123456789abcdefABCDEF" for c in hexpart):
                    raise PB3Error("E_CORRUPT", "escape \\%s sai trong chuỗi nháy kép" % nxt,
                                   where=path)
                out.append(chr(int(hexpart, 16)))
                i += 2 + n
                continue
            out.append(_DQ_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    raise PB3Error("E_CORRUPT", "chuỗi nháy kép không đóng", where=path)


def _parse_single_quoted(s: str, path: str) -> Tuple[str, int]:
    out: List[str] = []
    i = 1
    while i < len(s):
        ch = s[i]
        if ch == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise PB3Error("E_CORRUPT", "chuỗi nháy đơn không đóng", where=path)


def _parse_plain_scalar(text: str, path: str) -> Any:
    t = text.strip()
    if t in ("", "~", "null", "Null", "NULL"):
        return None
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if t in (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"):
        return float("inf")
    if t in ("-.inf", "-.Inf", "-.INF"):
        return float("-inf")
    if t in (".nan", ".NaN", ".NAN"):
        return float("nan")
    if _INT_RE.match(t):
        try:
            return int(t)
        except ValueError:
            pass
    if _HEX_RE.match(t):
        return int(t, 16)
    if _OCT_RE.match(t):
        return int(t, 8)
    if _FLOAT_RE.match(t):
        try:
            return float(t)
        except ValueError:
            pass
    if t[:1] in ("!", "%", "@", "`"):
        raise PB3Error("E_UNSUPPORTED_FORMAT", "YAML tag/chỉ thị không hỗ trợ: %s" % t[:10],
                       where=path)
    return t


class _FlowParser:
    """Parser đệ quy cho flow map/list trên một chuỗi đã gộp dòng."""

    def __init__(self, text: str, path: str, anchors: Dict[str, Any]):
        self.s = text
        self.i = 0
        self.path = path
        self.anchors = anchors

    def _skip_ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\n":
            self.i += 1

    def parse_value(self) -> Any:
        self._skip_ws()
        if self.i >= len(self.s):
            raise PB3Error("E_CORRUPT", "giá trị flow bị cắt cụt", where=self.path)
        ch = self.s[self.i]
        if ch == "{":
            return self._parse_map()
        if ch == "[":
            return self._parse_list()
        if ch == '"':
            val, end = _parse_double_quoted(self.s[self.i:], self.path)
            self.i += end
            return val
        if ch == "'":
            val, end = _parse_single_quoted(self.s[self.i:], self.path)
            self.i += end
            return val
        if ch == "&":
            m = re.match(r"&([A-Za-z0-9_-]+)\s*", self.s[self.i:])
            if not m:
                raise PB3Error("E_CORRUPT", "anchor sai cú pháp trong flow", where=self.path)
            self.i += m.end()
            val = self.parse_value()
            self.anchors[m.group(1)] = copy.deepcopy(val)
            return val
        if ch == "*":
            m = re.match(r"\*([A-Za-z0-9_-]+)", self.s[self.i:])
            if not m or m.group(1) not in self.anchors:
                raise PB3Error("E_CORRUPT", "alias không rõ trong flow", where=self.path)
            self.i += m.end()
            return copy.deepcopy(self.anchors[m.group(1)])
        # Scalar trần: tới , ] } hoặc ": " (trong map).
        start = self.i
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in ",]}":
                break
            if c == ":" and self.i + 1 < len(self.s) and self.s[self.i + 1] in " \n":
                break
            self.i += 1
        return _parse_plain_scalar(self.s[start:self.i], self.path)

    def _parse_key(self) -> str:
        self._skip_ws()
        if self.i < len(self.s) and self.s[self.i] in "\"'":
            if self.s[self.i] == '"':
                val, end = _parse_double_quoted(self.s[self.i:], self.path)
            else:
                val, end = _parse_single_quoted(self.s[self.i:], self.path)
            self.i += end
            return val
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in ":,{}[]":
            self.i += 1
        return self.s[start:self.i].strip()

    def _parse_map(self) -> Dict[str, Any]:
        self.i += 1  # {
        out: Dict[str, Any] = {}
        self._skip_ws()
        if self.i < len(self.s) and self.s[self.i] == "}":
            self.i += 1
            return out
        while True:
            key = self._parse_key()
            self._skip_ws()
            if self.i < len(self.s) and self.s[self.i] == ":":
                self.i += 1
                val = self.parse_value()
            else:
                val = None
            if key not in out:
                out[key] = val
            self._skip_ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i < len(self.s) and self.s[self.i] == "}":
                self.i += 1
                return out
            raise PB3Error("E_CORRUPT", "flow map sai cú pháp", where=self.path)

    def _parse_list(self) -> List[Any]:
        self.i += 1  # [
        out: List[Any] = []
        self._skip_ws()
        if self.i < len(self.s) and self.s[self.i] == "]":
            self.i += 1
            return out
        while True:
            out.append(self.parse_value())
            self._skip_ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1
                continue
            if self.i < len(self.s) and self.s[self.i] == "]":
                self.i += 1
                return out
            raise PB3Error("E_CORRUPT", "flow list sai cú pháp", where=self.path)


class _YamlParser:
    def __init__(self, text: str, path: str):
        self.path = path
        self.anchors: Dict[str, Any] = {}
        self.dups: List[Tuple[str, Any]] = []
        raw_lines = text.split("\n")
        # Bỏ document start/end ở đầu/cuối; document thứ hai -> lỗi.
        lines: List[str] = []
        seen_start = False
        for idx, ln in enumerate(raw_lines):
            s = ln.rstrip("\r")
            stripped = s.strip()
            if stripped == "---" or stripped.startswith("--- "):
                if seen_start or any(l.strip() for l in lines):
                    raise PB3Error("E_UNSUPPORTED_FORMAT",
                                   "nhiều document YAML (---) chưa hỗ trợ", where=path)
                seen_start = True
                if stripped.startswith("--- "):
                    lines.append(s[s.index("---") + 4:])
                continue
            if stripped == "...":
                if any(l.strip() for l in raw_lines[idx + 1:]):
                    raise PB3Error("E_UNSUPPORTED_FORMAT",
                                   "nhiều document YAML (sau ...) chưa hỗ trợ", where=path)
                break
            lines.append(s)
        # Bỏ phần tử rỗng giả sau \n cuối file (không phải một dòng thật).
        if text.endswith("\n") and lines and lines[-1] == "":
            lines.pop()
        self.lines = lines

    # ---- tiện ích dòng ----
    def _blank(self, ln: str) -> bool:
        return _strip_comment(ln).strip() == ""

    def _indent(self, ln: str) -> int:
        lead = ln[:len(ln) - len(ln.lstrip(" "))]
        if "\t" in ln[:len(ln) - len(ln.lstrip())]:
            raise PB3Error("E_CORRUPT", "tab trong thụt đầu dòng YAML", where=self.path)
        return len(lead)

    def _next_content(self, i: int) -> int:
        while i < len(self.lines) and self._blank(self.lines[i]):
            i += 1
        return i

    # ---- điểm vào ----
    def parse(self) -> Any:
        i = self._next_content(0)
        if i >= len(self.lines):
            raise PB3Error("E_UNSUPPORTED_FORMAT", "file YAML rỗng", where=self.path)
        node, end = self._parse_block(i, self._indent(self.lines[i]), "")
        if self._next_content(end) < len(self.lines):
            raise PB3Error("E_CORRUPT", "nội dung thừa sau giá trị YAML", where=self.path)
        return node

    def _parse_block(self, i: int, indent: int, prefix: str) -> Tuple[Any, int]:
        i = self._next_content(i)
        if i >= len(self.lines):
            return None, i
        ln = self.lines[i]
        ind = self._indent(ln)
        if ind < indent:
            return None, i
        content = _strip_comment(ln).strip()
        if content == "-" or content.startswith("- "):
            return self._parse_seq(i, ind, prefix)
        if _split_key_value(content) is not None:
            return self._parse_map(i, ind, prefix)
        # Scalar trần một khối (có thể nhiều dòng nối).
        parts = [content]
        i += 1
        while True:
            j = self._next_content(i)
            if j >= len(self.lines):
                break
            if self._indent(self.lines[j]) <= ind:
                break
            nxt = _strip_comment(self.lines[j]).strip()
            if nxt == "-" or nxt.startswith("- ") or _split_key_value(nxt) is not None:
                raise PB3Error("E_CORRUPT", "dòng nối của scalar trần trông như cấu trúc",
                               where=self.path)
            parts.append(nxt)
            i = j + 1
        return _parse_plain_scalar(" ".join(parts), self.path), i

    def _parse_map(self, i: int, indent: int, prefix: str,
                   first: Optional[Tuple[str, int]] = None) -> Tuple[Dict[str, Any], int]:
        out: Dict[str, Any] = {}
        first_done = first is None
        while True:
            if first is not None and not first_done:
                content, _col = first
                first_done = True
            else:
                i = self._next_content(i)
                if i >= len(self.lines):
                    break
                ln = self.lines[i]
                ind = self._indent(ln)
                if ind < indent:
                    break
                if ind > indent:
                    raise PB3Error("E_CORRUPT", "thụt đầu dòng không đúng trong map YAML",
                                   where=self.path)
                content = _strip_comment(ln).strip()
                if content == "-" or content.startswith("- "):
                    break
                i += 1
            kv = _split_key_value(content)
            if kv is None:
                raise PB3Error("E_CORRUPT", "dòng không phải cặp key: value trong map YAML",
                               where=self.path)
            key_text, val_text = kv
            key = self._parse_key_text(key_text)
            full = (prefix + "." + key) if prefix else key
            val, i = self._parse_value_after_key(i, indent, val_text, full)
            if key in out:
                self.dups.append((full, val))
            else:
                out[key] = val
        return out, i

    def _parse_key_text(self, key_text: str) -> str:
        t = key_text.strip()
        if t.startswith('"'):
            val, end = _parse_double_quoted(t, self.path)
            if t[end:].strip():
                raise PB3Error("E_CORRUPT", "key YAML có ký tự thừa sau nháy đóng", where=self.path)
            return val
        if t.startswith("'"):
            val, end = _parse_single_quoted(t, self.path)
            if t[end:].strip():
                raise PB3Error("E_CORRUPT", "key YAML có ký tự thừa sau nháy đóng", where=self.path)
            return val
        return t

    def _parse_seq(self, i: int, indent: int, prefix: str,
                   first: Optional[Tuple[str, int]] = None) -> Tuple[List[Any], int]:
        out: List[Any] = []
        first_done = first is None
        while True:
            if first is not None and not first_done:
                content, ind = first
                first_done = True
            else:
                i = self._next_content(i)
                if i >= len(self.lines):
                    break
                ln = self.lines[i]
                ind = self._indent(ln)
                if ind < indent:
                    break
                if ind > indent:
                    raise PB3Error("E_CORRUPT", "thụt đầu dòng không đúng trong list YAML",
                                   where=self.path)
                content = _strip_comment(ln).strip()
                if not (content == "-" or content.startswith("- ")):
                    break
                i += 1
            rest = "" if content == "-" else content[2:].lstrip()
            rest_col = ind + (len(content) - len(rest)) if rest else ind + 2
            item_prefix = "%s[%d]" % (prefix, len(out))
            if not rest:
                node, i = self._parse_block(i, ind + 1, item_prefix)
                out.append(node)
            elif _split_key_value(rest) is not None:
                node, i = self._parse_map(i, rest_col, item_prefix, first=(rest, rest_col))
                out.append(node)
            elif rest == "-" or rest.startswith("- "):
                node, i = self._parse_seq(i, rest_col, item_prefix, first=(rest, rest_col))
                out.append(node)
            else:
                val, i = self._parse_inline_or_block(rest, i, ind, item_prefix)
                out.append(val)
        return out, i

    def _parse_value_after_key(self, i: int, key_indent: int,
                               val_text: Optional[str], prefix: str = "") -> Tuple[Any, int]:
        """val_text là phần sau 'key:' trên cùng dòng (None = trống)."""
        if val_text is not None:
            return self._parse_inline_or_block(val_text, i, key_indent, prefix)
        # Giá trị ở khối sau: map/list thụt sâu hơn, hoặc list cùng mức thụt.
        j = self._next_content(i)
        if j >= len(self.lines):
            return None, i
        ind = self._indent(self.lines[j])
        content = _strip_comment(self.lines[j]).strip()
        if ind > key_indent:
            return self._parse_block(j, ind, prefix)
        if content == "-" or content.startswith("- "):
            return self._parse_block(j, ind, prefix)
        return None, i

    def _parse_inline_or_block(self, text: str, i: int, parent_indent: int,
                               prefix: str = "") -> Tuple[Any, int]:
        """Giá trị viết ngay trên dòng: scalar/flow/block scalar/anchor/alias."""
        t = text.strip()
        anchor = None
        m = re.match(r"&([A-Za-z0-9_-]+)\s*(.*)$", t, re.DOTALL)
        if m:
            anchor = m.group(1)
            t = m.group(2).strip()
            if not t:
                # Anchor cho khối lồng sau.
                node, i = self._parse_value_after_key(i, parent_indent, None, prefix)
                self.anchors[anchor] = copy.deepcopy(node)
                return node, i
        if t.startswith("*"):
            m2 = re.match(r"\*([A-Za-z0-9_-]+)\s*$", t)
            if not m2 or m2.group(1) not in self.anchors:
                raise PB3Error("E_CORRUPT", "alias YAML không rõ", where=self.path)
            return copy.deepcopy(self.anchors[m2.group(1)]), i
        if t[:1] in ("|", ">"):
            node = self._parse_block_scalar(t, i, parent_indent)
            # _parse_block_scalar tự tiến con trỏ qua các dòng nội dung.
            node, i = node
            if anchor is not None:
                self.anchors[anchor] = copy.deepcopy(node)
            return node, i
        if t[:1] in ("{", "["):
            flow_text, i = self._collect_flow(t, i)
            val = _FlowParser(flow_text, self.path, self.anchors).parse_value()
            if anchor is not None:
                self.anchors[anchor] = copy.deepcopy(val)
            return val, i
        if t.startswith('"'):
            val, end = _parse_double_quoted(t, self.path)
            if t[end:].strip():
                raise PB3Error("E_CORRUPT", "ký tự thừa sau chuỗi nháy kép YAML", where=self.path)
            if anchor is not None:
                self.anchors[anchor] = copy.deepcopy(val)
            return val, i
        if t.startswith("'"):
            val, end = _parse_single_quoted(t, self.path)
            if t[end:].strip():
                raise PB3Error("E_CORRUPT", "ký tự thừa sau chuỗi nháy đơn YAML", where=self.path)
            if anchor is not None:
                self.anchors[anchor] = copy.deepcopy(val)
            return val, i
        val = _parse_plain_scalar(_strip_comment(t), self.path)
        if anchor is not None:
            self.anchors[anchor] = copy.deepcopy(val)
        return val, i

    def _collect_flow(self, first: str, i: int) -> Tuple[str, int]:
        """Gộp các dòng tới khi ngoặc flow cân bằng. Trả (chuỗi flow, chỉ số dòng sau)."""
        text = first
        while True:
            depth = 0
            quote = None
            k = 0
            balanced = True
            while k < len(text):
                ch = text[k]
                if quote == '"':
                    if ch == "\\":
                        k += 2
                        continue
                    if ch == '"':
                        quote = None
                elif quote == "'":
                    if ch == "'":
                        if k + 1 < len(text) and text[k + 1] == "'":
                            k += 2
                            continue
                        quote = None
                else:
                    if ch in "\"'":
                        quote = ch
                    elif ch in "[{":
                        depth += 1
                    elif ch in "]}":
                        depth -= 1
                k += 1
            if quote is None and depth <= 0:
                return text, i
            if i >= len(self.lines):
                raise PB3Error("E_CORRUPT", "flow collection YAML không đóng", where=self.path)
            text += "\n" + self.lines[i]
            i += 1

    def _parse_block_scalar(self, indicator: str, i: int,
                            parent_indent: int) -> Tuple[str, int]:
        """indicator như |, |-, |+, >, >2, |-2. i = dòng sau dòng chứa indicator."""
        style = indicator[0]
        rest = indicator[1:].strip()
        chomp = None
        explicit_indent = None
        for ch in rest:
            if ch in "+-":
                if chomp is not None:
                    raise PB3Error("E_CORRUPT", "chomping block scalar YAML sai", where=self.path)
                chomp = ch
            elif ch.isdigit():
                if explicit_indent is not None:
                    raise PB3Error("E_CORRUPT", "số thụt đầu dòng block scalar YAML sai",
                                   where=self.path)
                explicit_indent = int(ch)
            else:
                raise PB3Error("E_CORRUPT", "ký tự lạ trong chỉ thị block scalar YAML",
                               where=self.path)
        # Tìm dòng nội dung đầu để xác định độ thụt.
        j = i
        content_indent = None
        while j < len(self.lines):
            ln = self.lines[j]
            if ln.strip() == "":
                j += 1
                continue
            raw_ind = len(ln) - len(ln.lstrip(" \t"))
            content_indent = (parent_indent + explicit_indent) if explicit_indent else raw_ind
            break
        if content_indent is None or content_indent <= parent_indent:
            return "", i
        parts: List[str] = []
        k = i
        while k < len(self.lines):
            ln = self.lines[k]
            if ln.strip() == "":
                parts.append("")
                k += 1
                continue
            raw_ind = len(ln) - len(ln.lstrip(" \t"))
            if raw_ind < content_indent:
                break
            parts.append(ln[content_indent:])
            k += 1
        # Bỏ các dòng trống ở cuối khỏi parts rồi xử lý chomping riêng.
        # parts hiện gồm cả dòng trống xen giữa và cuối khối.
        trailing = 0
        while parts and parts[-1] == "":
            parts.pop()
            trailing += 1
        if style == "|":
            body = "\n".join(parts)
            if parts:
                body += "\n"
        else:
            out: List[str] = []
            for idx, ln in enumerate(parts):
                if ln == "":
                    out.append("\n")
                elif out and not out[-1].endswith("\n"):
                    out[-1] = out[-1] + " " + ln
                else:
                    out.append(ln)
            body = "".join(out)
            if parts:
                body += "\n"
        if chomp == "-":
            body = body.rstrip("\n")
        elif chomp == "+":
            body = body + "\n" * trailing
        else:  # clip: đúng một \n cuối
            if body:
                body = body.rstrip("\n") + "\n"
        return body, k


def _split_key_value(content: str) -> Optional[Tuple[str, Optional[str]]]:
    """Tách 'key: value' / 'key:'; None nếu không phải dòng map."""
    if content.startswith(('"', "'")):
        quote = content[0]
        i = 1
        while i < len(content):
            ch = content[i]
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if quote == "'" and ch == "'" and i + 1 < len(content) and content[i + 1] == "'":
                i += 2
                continue
            if ch == quote:
                rest = content[i + 1:].lstrip()
                if rest.startswith(":"):
                    after = rest[1:]
                    return content[:i + 1], (after.strip() or None) if after.strip() else None
                return None
            i += 1
        return None
    i = 0
    while i < len(content):
        ch = content[i]
        if ch == ":" and (i + 1 >= len(content) or content[i + 1] in " \t"):
            key = content[:i].strip()
            if not key:
                return None
            after = content[i + 1:].strip()
            return key, after or None
        i += 1
    return None


def _parse_yaml_struct(text: str, path: str):
    parser = _YamlParser(text, path)
    root = parser.parse()
    return root, parser.dups


def _parse_yaml(data: bytes, path: str, locale: Optional[str], doc) -> Dict[str, Any]:
    root, dups = _parse_yaml_struct(doc.text, path)
    pairs: List[Tuple[str, str, str, List[str]]] = []
    stats: Dict[str, Any] = {}
    if isinstance(root, dict):
        for k, v in root.items():
            _walk(v, str(k), pairs, stats)
    elif isinstance(root, list):
        if all(isinstance(x, str) for x in root):
            for i, x in enumerate(root):
                pairs.append((stable_row_key(i + 1), x, "ok", []))
        elif all(isinstance(x, dict) for x in root):
            for item in root:
                kf = next((f for f in ("key", "id", "name") if f in item), None)
                vf = next((f for f in ("value", "text", "translation") if f in item), None)
                if kf is None or vf is None or not isinstance(item.get(kf), str):
                    raise PB3Error("E_UNSUPPORTED_FORMAT",
                                   "danh sách YAML phải là object có key/value", where=path)
                _walk(item[vf], str(item[kf]), pairs, stats)
        else:
            raise PB3Error("E_UNSUPPORTED_FORMAT",
                           "YAML phải là map hoặc danh sách object key-value", where=path)
    else:
        raise PB3Error("E_UNSUPPORTED_FORMAT",
                       "YAML phải là map hoặc danh sách, không phải giá trị đơn", where=path)
    for full, val in dups:
        _walk(val, full, pairs, stats)
    entries, conflicts = make_entries(pairs, path, len(data), doc.encoding, locale)
    for full, _val in dups:
        conflicts.append({"key": full, "msg": "key trùng trong YAML, giữ bản ghi đầu"})
    stats["keys"] = len(entries)
    return {"entries": entries, "schema_hint": {"flavor": "yaml"},
            "stats": stats, "conflicts": conflicts, "warnings": []}


# --------------------------------------------------------------------------- YAML build
def _looks_typed(s: str) -> bool:
    t = s.strip()
    if t in ("", "~", "null", "Null", "NULL", "true", "True", "TRUE",
             "false", "False", "FALSE"):
        return True
    return bool(_INT_RE.match(t) or _HEX_RE.match(t) or _OCT_RE.match(t)
                or _FLOAT_RE.match(t))


_PLAIN_UNSAFE_START = set("-?:,[]{}#&*!|>'\"%@`")


def _dump_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return json.dumps(v, ensure_ascii=False)
    s = str(v)
    if (s and s != s.strip() or not s or _looks_typed(s)
            or s[0] in _PLAIN_UNSAFE_START or s.startswith("- ") or s.startswith("? ")
            or s.startswith(": ") or ": " in s or " #" in s
            or any(ord(c) < 0x20 and c not in ("\t",) for c in s)):
        return json.dumps(s, ensure_ascii=False)
    return s


def _dump_key(k: Any) -> str:
    s = str(k)
    if s and s == s.strip() and not _looks_typed(s) and s[0] not in _PLAIN_UNSAFE_START \
            and ": " not in s and " #" not in s and not s.startswith("- "):
        return s
    return json.dumps(s, ensure_ascii=False)


def _dump_node(node: Any, indent: int) -> List[str]:
    if isinstance(node, dict):
        if not node:
            return [" " * indent + "{}"]
        lines: List[str] = []
        for k, v in node.items():
            key = _dump_key(k)
            if isinstance(v, dict) and v:
                lines.append(" " * indent + "%s:" % key)
                lines.extend(_dump_node(v, indent + 2))
            elif isinstance(v, list) and v:
                lines.append(" " * indent + "%s:" % key)
                lines.extend(_dump_node(v, indent + 2))
            else:
                lines.append(" " * indent + "%s: %s" % (key, _dump_scalar(v)))
        return lines
    if isinstance(node, list):
        if not node:
            return [" " * indent + "[]"]
        lines = []
        for item in node:
            if isinstance(item, dict) and item:
                sub = _dump_node(item, indent + 2)
                sub[0] = " " * indent + "- " + sub[0][indent + 2:]
                lines.extend(sub)
            elif isinstance(item, list) and item:
                sub = _dump_node(item, indent + 2)
                sub[0] = " " * indent + "- " + sub[0][indent + 2:]
                lines.extend(sub)
            else:
                lines.append(" " * indent + "- " + _dump_scalar(item))
        return lines
    return [" " * indent + _dump_scalar(node)]


def _build_yaml(table: LocTable, base_bytes: bytes) -> bytes:
    doc = decode_text(base_bytes, "base")
    root, _dups = _parse_yaml_struct(doc.text, "base")
    notes_by_key: Dict[str, List[str]] = {}
    for e in table.entries:
        if e.status in ("ok", "empty"):
            notes_by_key.setdefault(e.key, e.notes)
    # Áp theo thứ tự duyệt gốc: thu thập đường dẫn lá rồi áp hàng đợi theo key.
    leaves: List[str] = []

    def _collect(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _collect((prefix + "." + str(k)) if prefix else str(k), v)
        elif isinstance(node, list):
            for i, x in enumerate(node):
                _collect("%s[%d]" % (prefix, i), x)
        else:
            leaves.append(prefix)

    if isinstance(root, dict):
        for k, v in root.items():
            _collect(str(k), v)
    elif isinstance(root, list):
        for i, x in enumerate(root):
            _collect("%s[%d]" % ("", i), x)
    queue = value_queue(table)
    for key in leaves:
        if queue.get(key):
            value = queue[key].pop(0)
            restored = _restore(value, notes_by_key.get(key, []))
            _set_path(root, key, restored)
    if isinstance(root, list) and root and all(isinstance(x, dict) for x in root):
        for item in root:
            kf = next((f for f in ("key", "id", "name") if f in item), None)
            vf = next((f for f in ("value", "text", "translation") if f in item), None)
            if kf is None or vf is None:
                continue
            k = str(item[kf])
            if queue.get(k):
                item[vf] = _restore(queue[k].pop(0), notes_by_key.get(k, []))
    out = "\n".join(_dump_node(root, 0)) + "\n"
    return doc.encode(out)


# --------------------------------------------------------------------------- plugin
def _detect_flavor(data: bytes, path: str) -> str:
    low = path.lower()
    if low.endswith(_INI_EXTS):
        return "ini"
    if low.endswith(_YAML_EXTS):
        return "yaml"
    # Không rõ đuôi (vd "<gốc>"): section -> INI; YAML parse được map/list -> YAML.
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - chỉ để đoán định dạng
        return "ini"
    if re.search(r"(?m)^\s*\[[^\]\n]+\]\s*$", text):
        return "ini"
    try:
        doc = decode_text(data, path)
        root, _d = _parse_yaml_struct(doc.text, path)
        if isinstance(root, (dict, list)):
            return "yaml"
    except PB3Error:
        pass
    return "ini"


class YamlIniPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".yaml", ".yml", ".ini", ".cfg", ".conf")
    FLAVOR = "yaml_ini"
    SELFTEST_BASE = b"hello: Xin chao\nbye: Tam biet\n"

    def _sniff(self, head: bytes, path: str) -> float:
        try:
            text = head.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001 - sniff không được làm hỏng detect
            return 0.0
        for ln in text.splitlines():
            s = ln.strip()
            if re.match(r"^\[[^\]]+\]$", s):
                return 0.8
            if re.match(r"^[^#\s][^:=\n]*[:=]", ln):
                return 0.7
        return 0.0

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        doc = decode_text(data, path)
        if _detect_flavor(data, path) == "ini":
            return _parse_ini(data, path, locale, doc)
        return _parse_yaml(data, path, locale, doc)

    def _build_bytes(self, table: LocTable, base_bytes: bytes) -> bytes:
        flavor = (table.schema_hint or {}).get("flavor")
        if flavor not in ("yaml", "ini"):
            flavor = _detect_flavor(base_bytes, "<gốc>")
        if flavor == "ini":
            return _build_ini(table, base_bytes)
        return _build_yaml(table, base_bytes)
