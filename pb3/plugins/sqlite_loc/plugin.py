# Plugin định dạng SQLite localization (Claude 06): chỉ đọc.
# Mở DB ở chế độ chỉ đọc (mode=ro) qua file tạm trong tempdir của ctx
# (guard cấm ghi ngoài tempdir nên _parse_bytes không dùng được dưới guard:
# parse/probe/selftest đều đi qua _parse_file trên đường dẫn ctx.tempfile();
# _parse_bytes chỉ là vỏ bọc dùng trực tiếp khi không có guard, vd trong test).
# Bảng localization là bảng có cột key TEXT (key/id/name/string_id/...) +
# ít nhất 1 cột giá trị TEXT, hoặc tên bảng chứa lang/locale/string/text/
# translation. Bảng khác bị bỏ qua kèm cảnh báo. Không bao giờ ghi vào DB.
# Kiểm tra: python3 menu.py plugin test sqlite_loc
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from sdk.api import PluginContext
from sdk.formats import FormatPluginBase, make_entries
from sdk.types import Candidate, LocTable

MANIFEST = {
    "id": "sqlite_loc",
    "name": "SQLite localization",
    "version": "1.0.0",
    "api_version": 3,
    "kind": "format",
    "capabilities": ["read"],
    "maturity": "M2",
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["generic"],
    "match": {"extensions": [".sqlite", ".db", ".sqlite3", ".db3"]},
    "limits": {"max_entry_bytes": 16 * 1024 * 1024, "hook_timeout_s": 20},
    "entry": "plugin:SqliteLocPlugin",
    "selftest": True,
    "docs": "Đọc bảng localization trong cơ sở dữ liệu SQLite (chỉ đọc, không ghi "
            "vào DB). Bảng localization có cột key TEXT + ít nhất 1 cột giá trị TEXT, "
            "hoặc tên bảng chứa lang/locale/string/text/translation. Mỗi hàng cho một "
            "entry trên mỗi cột giá trị; tên cột trông như mã locale (en, vi, en-US) "
            "làm locale, không thì locale để trống. Giới hạn 100.000 entry.",
}

MAX_ENTRIES = 100000
_KEY_NAMES = ("key", "id", "name", "string_id", "msgid", "key_name", "string_key")
_NAME_HINTS = ("lang", "locale", "string", "text", "translation")
_LOCALE_RE = re.compile(r"^[a-z]{2,3}([-_][A-Za-z]{2,4})?$")


def _uri_escape(path: str) -> str:
    return (path.replace("%", "%25").replace("?", "%3F")
            .replace("#", "%23").replace(" ", "%20"))


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _is_text_type(decl: Any) -> bool:
    if decl is None:
        return True
    t = str(decl).upper()
    return t == "" or "TEXT" in t or "CHAR" in t or "CLOB" in t


def _name_has_hint(name: str) -> bool:
    low = str(name).lower()
    return any(h in low for h in _NAME_HINTS)


def _classify_table(name: str, cols: List[Tuple[str, str]]) -> Tuple[Optional[str], List[str]]:
    """(cột key, [cột giá trị]) hoặc (None, []) nếu không phải bảng localization."""
    if not cols:
        return None, []
    key_col: Optional[str] = None
    for cname, ctype in cols:
        if cname.lower() in _KEY_NAMES and _is_text_type(ctype):
            key_col = cname
            break
    if key_col is None and _name_has_hint(name):
        # Tên bảng gợi ý localization nhưng cột key không mang tên quen thuộc:
        # lấy cột đầu làm key (vd bảng texts(id INTEGER, en TEXT, vi TEXT)).
        key_col = cols[0][0]
    if key_col is None:
        return None, []
    value_cols = [c for c, t in cols if c != key_col and _is_text_type(t)]
    return key_col, value_cols


class SqliteLocPlugin(FormatPluginBase):
    MANIFEST = MANIFEST
    EXTENSIONS = (".sqlite", ".db", ".sqlite3", ".db3")
    FLAVOR = "sqlite"

    def _sniff(self, head: bytes, path: str) -> float:
        if head.startswith(b"SQLite format 3\x00"):
            return 0.9
        return 0.0

    # ---- lõi đọc (đường dẫn file DB thật, chỉ đọc) ----
    def _parse_file(self, db_path: str, display_path: str, locale: Optional[str],
                    size: int = 0) -> Dict[str, Any]:
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % _uri_escape(db_path),
                                   uri=True, timeout=5)
        except sqlite3.Error:
            raise PB3Error("E_CORRUPT", "không mở được cơ sở dữ liệu SQLite",
                           where=display_path)
        try:
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            except sqlite3.Error:
                raise PB3Error("E_CORRUPT", "cơ sở dữ liệu SQLite hỏng", where=display_path)
            plan: List[Tuple[str, str, List[str]]] = []
            warnings: List[Dict[str, Any]] = []
            for name in tables:
                try:
                    cols = [(r[1], r[2] or "") for r in conn.execute(
                        "PRAGMA table_info(%s)" % _quote_ident(name)).fetchall()]
                except sqlite3.Error:
                    raise PB3Error("E_CORRUPT", "không đọc được cấu trúc bảng",
                                   where=display_path)
                key_col, value_cols = _classify_table(name, cols)
                if key_col is None or not value_cols:
                    warnings.append({"code": "E_PARSE_PARTIAL", "key": name,
                                     "msg": "bỏ qua bảng %s (không phải bảng localization)" % name})
                    continue
                plan.append((name, key_col, value_cols))
            multi = len(plan) > 1
            pairs: List[Tuple] = []
            total_rows = 0
            for (name, key_col, value_cols) in plan:
                cols_sql = ", ".join(_quote_ident(c) for c in [key_col] + value_cols)
                try:
                    cur = conn.execute("SELECT %s FROM %s" % (cols_sql, _quote_ident(name)))
                    for row in cur:
                        total_rows += 1
                        if row[0] is None:
                            continue
                        key = str(row[0])
                        if multi:
                            key = name + "." + key
                        for j, col in enumerate(value_cols):
                            v = row[j + 1]
                            text = "" if v is None else (v if isinstance(v, str) else str(v))
                            loc = col if _LOCALE_RE.match(col) else None
                            pairs.append((key, text, "ok", [], loc))
                            if len(pairs) > MAX_ENTRIES:
                                raise PB3Error(
                                    "E_TOO_LARGE",
                                    "vượt giới hạn %d entry (bảng %s quá lớn)" % (MAX_ENTRIES, name),
                                    where=display_path)
                except PB3Error:
                    raise
                except sqlite3.Error:
                    raise PB3Error("E_CORRUPT", "không đọc được dữ liệu bảng %s" % name,
                                   where=display_path)
            entries, conflicts = make_entries(pairs, display_path, size, "utf-8", locale)
            return {"entries": entries,
                    "schema_hint": {"flavor": "sqlite",
                                    "tables": [n for (n, _k, _v) in plan],
                                    "key_col": {n: k for (n, k, _v) in plan},
                                    "value_cols": {n: v for (n, _k, v) in plan}},
                    "stats": {"tables": len(plan), "tables_total": len(tables),
                              "tables_skipped": len(tables) - len(plan),
                              "rows": total_rows, "keys": len(entries)},
                    "conflicts": conflicts, "warnings": warnings}
        finally:
            conn.close()

    def _parse_bytes(self, data: bytes, path: str, locale: Optional[str]) -> Dict[str, Any]:
        """Chỉ dùng khi không có guard (test trực tiếp): ghi bytes ra file tạm
        hệ thống rồi parse. Dưới guard, parse/probe/selftest dùng _parse_file
        trên đường dẫn ctx.tempfile() thay cho hàm này."""
        if data and not data.startswith(b"SQLite format 3\x00"):
            raise PB3Error("E_CORRUPT", "không phải cơ sở dữ liệu SQLite", where=path)
        fd, tmp = tempfile.mkstemp(prefix="pb3-sqlite-", suffix=".sqlite")
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.close(fd)
            fd = None
            return self._parse_file(tmp, path, locale, len(data))
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ---- hook (ghi DB bytes vào file tạm của ctx, chỉ đọc) ----
    def _write_temp(self, ctx: PluginContext, data: bytes) -> str:
        tmp = ctx.tempfile("sqlite", ".sqlite")
        with open(tmp, "wb") as fh:
            fh.write(data)
        return tmp

    def probe(self, ctx: PluginContext, cand: Candidate) -> Dict[str, Any]:
        try:
            data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
            if data and not data.startswith(b"SQLite format 3\x00"):
                return {"ok": False, "reason": "E_CORRUPT: không phải cơ sở dữ liệu SQLite"}
            tmp = self._write_temp(ctx, data)
            res = self._parse_file(tmp, cand.path, ctx.locale.code, len(data))
            return {"ok": True, "schema": self.FLAVOR, "entries": len(res["entries"]),
                    "warnings": len(res.get("warnings", [])),
                    "conflicts": len(res.get("conflicts", []))}
        except PB3Error as e:
            return {"ok": False, "reason": "%s: %s" % (e.code, e.detail or e.message_vi)}
        except OSError as e:
            return {"ok": False, "reason": "E_IO: %s" % type(e).__name__}

    def parse(self, ctx: PluginContext, cand: Candidate) -> LocTable:
        data = ctx.vfs.read(cand.path, max_bytes=ctx.limits.max_entry_bytes)
        if len(data) > self.manifest.max_entry_bytes:
            raise PB3Error("E_TOO_LARGE", "file %d byte > giới hạn %d"
                           % (len(data), self.manifest.max_entry_bytes), where=cand.path)
        if not data.startswith(b"SQLite format 3\x00") and data:
            raise PB3Error("E_CORRUPT", "không phải cơ sở dữ liệu SQLite", where=cand.path)
        tmp = self._write_temp(ctx, data)
        res = self._parse_file(tmp, cand.path, ctx.locale.code, len(data))
        t = LocTable(res["entries"], ctx.locale.code, self.id,
                     dict(res.get("schema_hint") or {"flavor": "sqlite"}),
                     dict(res.get("stats") or {}),
                     list(res.get("conflicts") or []), list(res.get("warnings") or []))
        t.stats = {"entries": len(t.entries), **t.count_by_status(), **(t.stats or {})}
        return t

    def selftest(self, ctx: PluginContext) -> Dict[str, Any]:
        try:
            tmp = ctx.tempfile("sqlite-selftest", ".sqlite")
            conn = sqlite3.connect(tmp, timeout=5)
            try:
                conn.execute("CREATE TABLE strings (key TEXT PRIMARY KEY, en TEXT, vi TEXT)")
                conn.execute("INSERT INTO strings VALUES ('hello', 'Hello', 'Xin chào')")
                conn.execute("INSERT INTO strings VALUES ('bye', 'Goodbye', 'Tạm biệt')")
                conn.execute("CREATE TABLE scores (id INTEGER PRIMARY KEY, points INTEGER)")
                conn.execute("INSERT INTO scores VALUES (1, 100)")
                conn.commit()
            finally:
                conn.close()
            res = self._parse_file(tmp, "selftest", None)
            entries = res["entries"]
            keys = {(e.locale, e.key) for e in entries}
            skipped = any("scores" in (w.get("msg", "") or "")
                          for w in res.get("warnings", []))
            ok = len(entries) == 4 and ("en", "hello") in keys and ("vi", "hello") in keys \
                and ("en", "bye") in keys and ("vi", "bye") in keys and skipped
            return {"ok": ok,
                    "checked": "selftest: parse được %d entry, bảng scores bị bỏ qua" % len(entries),
                    "reason": "" if ok else "thiếu entry hoặc bảng không phải localization "
                                        "không bị bỏ qua"}
        except PB3Error as e:
            return {"ok": False, "checked": "selftest",
                    "reason": "%s: %s" % (e.code, e.detail or e.message_vi)}
