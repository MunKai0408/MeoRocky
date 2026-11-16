"""Game Knowledge Base (KB) + fingerprint + inventory cache (Claude 09, đặc tả §8).

- KB: SQLite cục bộ tại ``kb/pb3_kb.sqlite`` (WAL), ``SCHEMA_VERSION = 1``,
  migration v0 -> v1 an toàn. Chỉ lưu hash/kích thước/vai trò — KHÔNG lưu
  nội dung bản dịch, KHÔNG lưu nội dung file game (đặc tả §8.1).
- Fingerprint: bundle_id/version (đọc từ Info.plist khi có) + hash tối đa
  5 entry đặc trưng, KHÔNG hash toàn bộ IPA (§8.2).
- Profile xuất/nhập là JSON thuần, chỉ là GỢI Ý: khi nhập phải kiểm tra
  hash, không tin tuyệt đối (§8.4). Profile nhập chỉ lưu tiền tố hash
  (sha256_8) nên lookup không tái dùng cho tới khi dò lại từ đầu.
- InventoryCache: cache JSON ``inventory_cache.json`` trong thư mục làm
  việc, ghi nguyên tử (AtomicWriter, mẫu cache plan decisions #111),
  LRU tối đa 32 mục / 8MB; mục hết hạn khi key đổi (mtime/size/hash đổi).

Chỉ thư viện chuẩn, Python >= 3.8. Không gọi mạng, không thêm dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.safe_io import AtomicWriter

SCHEMA_VERSION = 1
KB_FILENAME = "pb3_kb.sqlite"
INVENTORY_FILENAME = "inventory_cache.json"

# Fingerprint: tối đa 5 entry đặc trưng (§8.2: hash của 3-5 file đặc trưng).
MAX_FINGERPRINT_FILES = 5
# File thường: hash toàn bộ khi <= 512MB; lớn hơn chỉ hash 64MB đầu + 64MB cuối.
FULL_HASH_MAX_BYTES = 512 * 1024 * 1024
EDGE_HASH_BYTES = 64 * 1024 * 1024
# Inventory cache: tối đa 32 mục / 8MB (§8.5 + nhiệm vụ).
INVENTORY_MAX_ITEMS = 32
INVENTORY_MAX_BYTES = 8 * 1024 * 1024
# Inventory key: sha256 của 1MB đầu + 1MB cuối file.
INVENTORY_EDGE_BYTES = 1024 * 1024
_CHUNK = 1024 * 1024

_CONTAINER_SUFFIXES = frozenset([
    ".zip", ".ipa", ".pck", ".pak", ".tar", ".gz", ".tgz", ".xz", ".bz2",
])
# Tên gợi ý entry đặc trưng (ưu tiên sau Info.plist).
_FEATURED_WORDS = ("info", "version", "manifest", "config")

_TABLES_DDL = (
    """CREATE TABLE IF NOT EXISTS schema_version (
         version INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS games (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         bundle_id TEXT,
         name TEXT,
         version TEXT,
         engine TEXT,
         engine_conf REAL,
         path_hash TEXT,
         fingerprint TEXT UNIQUE,
         size INTEGER,
         first_seen TEXT,
         last_seen TEXT)""",
    """CREATE TABLE IF NOT EXISTS files (
         game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
         rel_path TEXT NOT NULL,
         size INTEGER,
         sha256 TEXT,
         role TEXT,
         adapter_id TEXT,
         score REAL,
         PRIMARY KEY (game_id, rel_path))""",
    """CREATE TABLE IF NOT EXISTS locales (
         game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
         entry_path TEXT NOT NULL,
         locale TEXT NOT NULL,
         adapter_id TEXT,
         key_count INTEGER,
         maturity TEXT,
         PRIMARY KEY (game_id, entry_path, locale))""",
    """CREATE TABLE IF NOT EXISTS choices (
         game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
         file_hash TEXT NOT NULL,
         adapter_id TEXT,
         chosen_by TEXT,
         note TEXT,
         PRIMARY KEY (game_id, file_hash))""",
    """CREATE TABLE IF NOT EXISTS runs (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         game_id INTEGER REFERENCES games(id) ON DELETE CASCADE,
         mode TEXT,
         started_at TEXT,
         finished_at TEXT,
         result TEXT,
         error_code TEXT)""",
    """CREATE TABLE IF NOT EXISTS plugin_stats (
         adapter_id TEXT NOT NULL,
         version TEXT NOT NULL,
         games_seen INTEGER NOT NULL DEFAULT 0,
         success INTEGER NOT NULL DEFAULT 0,
         fail INTEGER NOT NULL DEFAULT 0,
         PRIMARY KEY (adapter_id, version))""",
)


def _now_iso() -> str:
    """Thời điểm hiện tại, ISO 8601 UTC (vd '2026-11-16T03:30:00+00:00')."""
    return datetime.now(timezone.utc).isoformat()


def _sha256_stream(stream, *, limit: Optional[int] = None) -> str:
    """sha256 streamed theo chunk 1MB (không nạp cả file vào RAM)."""
    h = hashlib.sha256()
    remaining = limit
    while True:
        want = _CHUNK if remaining is None else min(_CHUNK, remaining)
        if want <= 0:
            break
        chunk = stream.read(want)
        if not chunk:
            break
        h.update(chunk)
        if remaining is not None:
            remaining -= len(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_hash(path: str) -> str:
    """path_hash = sha256 của đường dẫn tuyệt đối đã resolve."""
    return _sha256_bytes(os.path.abspath(str(path)).encode("utf-8"))


def _role_for(rel_path: str) -> str:
    """Vai trò của một entry đặc trưng, suy từ tên (chỉ để phân loại)."""
    low = rel_path.lower()
    base = low.rsplit("/", 1)[-1]
    if base == "info.plist" or low.endswith("/info.plist"):
        return "info_plist"
    if "manifest" in low:
        return "manifest"
    if "config" in low:
        return "config"
    if "version" in low:
        return "version"
    return "file"


def _is_info_plist(rel_path: str) -> bool:
    low = rel_path.lower()
    return low == "info.plist" or low.endswith("/info.plist")


def _featured_rank(rel_path: str) -> int:
    """Hạng ưu tiên entry đặc trưng: 0 = Info.plist, 1 = tên chứa
    info/version/manifest/config, 2 = còn lại."""
    if _is_info_plist(rel_path):
        return 0
    low = rel_path.lower()
    if any(w in low for w in _FEATURED_WORDS):
        return 1
    return 2


def _read_plist_dict(vfs, rel_path: str) -> Dict[str, Any]:
    """Đọc Info.plist qua VFS (best effort: lỗi/plist hỏng -> {})."""
    try:
        size = vfs.stat(rel_path).size
    except Exception:
        return {}
    if size <= 0 or size > 1024 * 1024:
        return {}
    try:
        data = vfs.read(rel_path)
    except Exception:
        return {}
    try:
        doc = plistlib.loads(data)
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _hash_vfs_entry(vfs, rel_path: str) -> Tuple[str, bytes]:
    """Hash streamed một entry VFS; trả (sha256, 64 byte đầu).

    Dùng open_stream khi có (không nạp cả entry vào RAM); hỏng thì dùng
    read() làm dự phòng. Entry đọc lỗi -> PB3Error E_IO."""
    head = b""
    h = hashlib.sha256()
    try:
        stream = vfs.open_stream(rel_path)
    except Exception:
        stream = None
    if stream is not None:
        try:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                if len(head) < 64:
                    head = (head + chunk)[:64]
                h.update(chunk)
            return h.hexdigest(), head
        except Exception as exc:
            raise PB3Error("E_IO", "không hash được entry %s: %s"
                           % (rel_path, type(exc).__name__), where=rel_path)
        finally:
            try:
                stream.close()
            except Exception:
                pass
    try:
        data = vfs.read(rel_path)
    except Exception as exc:
        raise PB3Error("E_IO", "không đọc được entry %s: %s"
                       % (rel_path, type(exc).__name__), where=rel_path)
    return _sha256_bytes(data), data[:64]


def _detect_engine_best_effort(names: List[str],
                               heads: Dict[str, bytes]) -> Tuple[str, float]:
    """Nhận diện engine qua sdk.discovery.detect_engine; lỗi -> unknown/0.0
    (best effort, không bao giờ raise)."""
    try:
        from sdk.discovery import detect_engine
        guess = detect_engine(names, heads)
        return guess.engine or "unknown", float(guess.confidence or 0.0)
    except Exception:
        return "unknown", 0.0


def _canonical_fingerprint(bundle_id: Optional[str], version: Optional[str],
                           size: int, files: List[Dict[str, Any]]) -> str:
    """fingerprint = sha256 của JSON chuẩn (sort_keys) gồm bundle_id/version/
    size/danh sách (rel_path, size, sha256) đã sắp theo rel_path."""
    payload = {
        "bundle_id": bundle_id,
        "version": version,
        "size": int(size),
        "files": [[f["rel_path"], int(f["size"]), f["sha256"]]
                  for f in sorted(files, key=lambda d: d["rel_path"])],
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return _sha256_bytes(text.encode("utf-8"))


def _looks_like_container(path: Path, head: bytes) -> bool:
    """Có phải container không: magic qua detect_container_kind, hoặc đuôi
    container quen thuộc (.zip/.ipa/.pck/.pak/...)."""
    try:
        from sdk.containers import detect_container_kind
        if detect_container_kind(head) != "unknown":
            return True
    except Exception:
        pass
    return path.suffix.lower() in _CONTAINER_SUFFIXES


def _fingerprint_container(path: Path, size: int) -> Dict[str, Any]:
    """Fingerprint một container (ZIP/IPA/PCK/PAK/TAR/...).

    - Đọc Info.plist qua open_vfs khi có (bundle_id/version).
    - Hash tối đa 5 entry đặc trưng (Info.plist trước, rồi entry nhỏ có tên
      chứa info/version/manifest/config, còn lại theo thứ tự đường dẫn).
    - KHÔNG hash toàn bộ IPA (§8.2)."""
    from sdk.containers import open_vfs
    vfs = open_vfs(str(path))
    try:
        names = [n for n in vfs.iter_entries() if not n.endswith("/")]
        names.sort()
        # Kích thước từng entry (lỗi stat -> bỏ entry đó).
        sized: List[Tuple[str, int]] = []
        for name in names:
            try:
                sized.append((name, int(vfs.stat(name).size)))
            except Exception:
                continue
        # Chọn tối đa 5 entry đặc trưng: hạng 0 trước, hạng 1 theo size
        # tăng dần (entry nhỏ trước), hạng 2 theo đường dẫn.
        ranked = sorted(
            sized,
            key=lambda item: (_featured_rank(item[0]),
                              item[1] if _featured_rank(item[0]) == 1 else 0,
                              item[0]))
        chosen = ranked[:MAX_FINGERPRINT_FILES]
        # Info.plist: bundle_id/version (entry Info.plist ngắn nhất).
        bundle_id: Optional[str] = None
        version: Optional[str] = None
        plist_names = sorted(n for n, _s in sized if _is_info_plist(n))
        if plist_names:
            doc = _read_plist_dict(vfs, plist_names[0])
            raw_id = doc.get("CFBundleIdentifier")
            if isinstance(raw_id, str) and raw_id.strip():
                bundle_id = raw_id.strip()
            for key in ("CFBundleShortVersionString", "CFBundleVersion"):
                raw_v = doc.get(key)
                if isinstance(raw_v, str) and raw_v.strip():
                    version = raw_v.strip()
                    break
        files: List[Dict[str, Any]] = []
        heads: Dict[str, bytes] = {}
        for rel_path, entry_size in chosen:
            digest, head = _hash_vfs_entry(vfs, rel_path)
            heads[rel_path] = head
            files.append({"rel_path": rel_path, "size": entry_size,
                          "sha256": digest, "role": _role_for(rel_path)})
        files.sort(key=lambda d: d["rel_path"])
        engine, engine_conf = _detect_engine_best_effort(names[:2000], heads)
        fingerprint = _canonical_fingerprint(bundle_id, version, size, files)
        return {"bundle_id": bundle_id, "version": version, "engine": engine,
                "engine_conf": engine_conf, "fingerprint": fingerprint,
                "path_hash": _path_hash(str(path)), "size": int(size),
                "files": files, "hash_mode": "entries"}
    finally:
        try:
            vfs.close()
        except Exception:
            pass


def _hash_plain_file(path: Path, size: int) -> Tuple[str, str]:
    """Hash một file thường, streamed.

    Trả (sha256, hash_mode): <= 512MB hash toàn bộ ("full"); lớn hơn chỉ
    hash 64MB đầu + 64MB cuối + size ("head_tail_64mb") — GIỚI HẠN: hash
    này không phát hiện đổi byte ở giữa file (chỉ đầu/cuối/size)."""
    if size <= FULL_HASH_MAX_BYTES:
        with open(str(path), "rb") as fh:
            return _sha256_stream(fh), "full"
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        h.update(fh.read(EDGE_HASH_BYTES))
        fh.seek(max(0, size - EDGE_HASH_BYTES))
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    h.update(str(size).encode("ascii"))
    return h.hexdigest(), "head_tail_64mb"


def fingerprint_game(path) -> Dict[str, Any]:
    """Fingerprint một game (file IPA/ZIP/container hoặc file thường).

    Trả dict với các khóa: bundle_id, version, engine, engine_conf,
    fingerprint (hex), path_hash, size, files (list {rel_path, size,
    sha256, role}), hash_mode. File không tồn tại/không phải file thường
    -> PB3Error E_INPUT_NOT_FOUND; container hỏng -> PB3Error của open_vfs.
    Engine chỉ là best effort (unknown/0.0 khi không nhận diện được)."""
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file: %s" % str(path),
                       where=str(path))
    size = p.stat().st_size
    with open(str(p), "rb") as fh:
        head = fh.read(2048)
    if _looks_like_container(p, head):
        return _fingerprint_container(p, size)
    digest, mode = _hash_plain_file(p, size)
    with open(str(p), "rb") as fh:
        first = fh.read(64)
    engine, engine_conf = _detect_engine_best_effort(
        [p.name], {p.name: first})
    files = [{"rel_path": p.name, "size": size, "sha256": digest,
              "role": "file"}]
    fingerprint = _canonical_fingerprint(None, None, size, files)
    return {"bundle_id": None, "version": None, "engine": engine,
            "engine_conf": engine_conf, "fingerprint": fingerprint,
            "path_hash": _path_hash(str(p)), "size": int(size),
            "files": files, "hash_mode": mode}


def _resolve_db_path(path) -> Path:
    """Đường dẫn file SQLite cho KB(path).

    - None -> <gốc dự án>/kb/pb3_kb.sqlite (đặc tả §8.1).
    - Thư mục (hoặc đường dẫn chưa tồn tại không có đuôi file) ->
      <đó>/kb/pb3_kb.sqlite.
    - File .sqlite/.sqlite3/.db (hoặc file đã tồn tại) -> dùng trực tiếp."""
    if path is None:
        root = Path(__file__).resolve().parent.parent
        return root / "kb" / KB_FILENAME
    p = Path(os.path.expanduser(str(path)))
    if p.suffix.lower() in (".sqlite", ".sqlite3", ".db") or p.is_file():
        return p
    if p.is_dir() or "." not in p.name:
        return p / "kb" / KB_FILENAME
    return p


class KB:
    """Game Knowledge Base: SQLite cục bộ, WAL, schema v1.

    Chỉ lưu hash/kích thước/vai trò — KHÔNG lưu nội dung bản dịch hay nội
    dung file game. Profile nhập từ JSON chỉ là gợi ý, không tin tuyệt đối.
    """

    def __init__(self, path=None):
        self.db_path = _resolve_db_path(path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(self.db_path))
        except sqlite3.Error as exc:
            raise PB3Error("E_IO", "không mở được KB: %s" % type(exc).__name__,
                           where=str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self.last_lookup_reason: Optional[str] = None
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except PB3Error:
            raise
        except sqlite3.Error as exc:
            raise PB3Error("E_IO", "không khởi tạo được KB: %s"
                           % type(exc).__name__, where=str(self.db_path))

    # ------------------------------------------------------------ schema
    def _migrate(self) -> None:
        """Migration v0 -> v1: chưa có bảng schema_version (v0) hoặc version
        = 0 -> tạo đủ bảng, ghi version = 1. Version > 1 (mới hơn, do bản
        PB3 khác tạo) -> từ chối rõ ràng thay vì ghi đè."""
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='schema_version'")
        if cur.fetchone() is None:
            for ddl in _TABLES_DDL:
                self._conn.execute(ddl)
            self._conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            self._conn.commit()
            return
        row = self._conn.execute(
            "SELECT version FROM schema_version LIMIT 1").fetchone()
        current = int(row["version"]) if row is not None else 0
        if current > SCHEMA_VERSION:
            raise PB3Error(
                "E_INTERNAL",
                "KB schema version %d mới hơn bản PB3 này hỗ trợ (%d); "
                "không ghi đè" % (current, SCHEMA_VERSION),
                where=str(self.db_path))
        if current < SCHEMA_VERSION:
            for ddl in _TABLES_DDL:
                self._conn.execute(ddl)
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,))
            else:
                self._conn.execute(
                    "UPDATE schema_version SET version = ?",
                    (SCHEMA_VERSION,))
            self._conn.commit()
            return
        for ddl in _TABLES_DDL:   # v1: đảm bảo đủ bảng (idempotent)
            self._conn.execute(ddl)
        self._conn.commit()

    @property
    def schema_version(self) -> int:
        """Schema version hiện tại của DB (1)."""
        row = self._conn.execute(
            "SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row["version"]) if row is not None else 0

    def _ensure_open(self) -> None:
        if self._conn is None:
            raise PB3Error("E_INTERNAL", "KB đã đóng", where=str(self.db_path))

    # ------------------------------------------------------------ tiện ích
    def _resolve_game_id(self, bundle_id_or_id) -> Optional[int]:
        """bundle_id (str) hoặc id (int/chuỗi số) -> id, None nếu không có."""
        if isinstance(bundle_id_or_id, bool):
            return None
        if isinstance(bundle_id_or_id, int):
            row = self._conn.execute(
                "SELECT id FROM games WHERE id = ?",
                (bundle_id_or_id,)).fetchone()
            return int(row["id"]) if row is not None else None
        text = str(bundle_id_or_id)
        if text.isdigit():
            row = self._conn.execute(
                "SELECT id FROM games WHERE id = ?",
                (int(text),)).fetchone()
            if row is not None:
                return int(row["id"])
        row = self._conn.execute(
            "SELECT id FROM games WHERE bundle_id = ? "
            "ORDER BY last_seen DESC, id DESC LIMIT 1",
            (text,)).fetchone()
        return int(row["id"]) if row is not None else None

    def _game_row(self, game_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    # ------------------------------------------------------------ ghi nhớ
    def remember(self, fp: Dict[str, Any], files: List[Dict[str, Any]],
                 locales: Optional[List[Dict[str, Any]]] = None,
                 choices=None) -> int:
        """Lưu/cập nhật profile một game từ fingerprint + inventory đã dò.

        ``fp``: dict của fingerprint_game(); ``files``: [{rel_path, size,
        sha256, role, adapter_id?, score?}]; ``locales``: [{entry_path,
        locale, adapter_id?, key_count?, maturity?}]; ``choices``: dict
        {file_hash: adapter_id} hoặc list [{file_hash, adapter_id,
        chosen_by?, note?}]. Ghi đè toàn bộ files/locales/choices cũ của
        game (ảnh chụp mới nhất). Trả game_id."""
        self._ensure_open()
        if not isinstance(fp, dict) or not fp.get("fingerprint"):
            raise PB3Error("E_PLUGIN_INVALID",
                           "remember cần fingerprint dict có 'fingerprint'")
        now = _now_iso()
        bundle_id = fp.get("bundle_id")
        name = fp.get("name") or bundle_id
        row = self._conn.execute(
            "SELECT id, first_seen FROM games WHERE fingerprint = ?",
            (fp["fingerprint"],)).fetchone()
        if row is not None:
            game_id = int(row["id"])
            first_seen = row["first_seen"]
            self._conn.execute(
                "UPDATE games SET bundle_id=?, name=?, version=?, engine=?, "
                "engine_conf=?, path_hash=?, size=?, first_seen=?, "
                "last_seen=? WHERE id=?",
                (bundle_id, name, fp.get("version"), fp.get("engine"),
                 fp.get("engine_conf"), fp.get("path_hash"),
                 fp.get("size"), first_seen, now, game_id))
        else:
            cur = self._conn.execute(
                "INSERT INTO games (bundle_id, name, version, engine, "
                "engine_conf, path_hash, fingerprint, size, first_seen, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bundle_id, name, fp.get("version"), fp.get("engine"),
                 fp.get("engine_conf"), fp.get("path_hash"),
                 fp["fingerprint"], fp.get("size"), now, now))
            game_id = int(cur.lastrowid)
        self._conn.execute("DELETE FROM files WHERE game_id = ?", (game_id,))
        self._conn.execute("DELETE FROM locales WHERE game_id = ?", (game_id,))
        self._conn.execute("DELETE FROM choices WHERE game_id = ?", (game_id,))
        for f in files or []:
            if not isinstance(f, dict) or not f.get("rel_path"):
                raise PB3Error("E_PLUGIN_INVALID",
                               "file trong remember cần 'rel_path'")
            self._conn.execute(
                "INSERT OR REPLACE INTO files (game_id, rel_path, size, "
                "sha256, role, adapter_id, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_id, f["rel_path"], f.get("size"),
                 f.get("sha256"), f.get("role"), f.get("adapter_id"),
                 f.get("score")))
        for loc in locales or []:
            if not isinstance(loc, dict) or not loc.get("entry_path") \
                    or not loc.get("locale"):
                raise PB3Error("E_PLUGIN_INVALID",
                               "locale trong remember cần 'entry_path' + 'locale'")
            self._conn.execute(
                "INSERT OR REPLACE INTO locales (game_id, entry_path, locale, "
                "adapter_id, key_count, maturity) VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, loc["entry_path"], loc["locale"],
                 loc.get("adapter_id"), loc.get("key_count"),
                 loc.get("maturity")))
        for file_hash, adapter_id, chosen_by, note in _iter_choices(choices):
            self._conn.execute(
                "INSERT OR REPLACE INTO choices (game_id, file_hash, "
                "adapter_id, chosen_by, note) VALUES (?, ?, ?, ?, ?)",
                (game_id, file_hash, adapter_id, chosen_by, note))
        self._conn.commit()
        return game_id

    # ------------------------------------------------------------ tra cứu
    def lookup(self, path) -> Optional[Dict[str, Any]]:
        """Tìm profile game theo fingerprint (fallback theo path_hash).

        Xác minh lại hash các file đã lưu với container hiện tại: khớp mới
        tái dùng; lệch -> trả None và ghi lý do vào
        ``self.last_lookup_reason`` (không tái dùng im lặng). Game chưa có
        -> None, lý do "chưa có profile". Trả profile dạng
        {game:{id,bundle_id,version,engine,engine_conf}, files:[...],
        locales:[...], choices:{file_hash: adapter_id}}."""
        self._ensure_open()
        fp = fingerprint_game(path)
        self.last_lookup_reason = None
        row = self._conn.execute(
            "SELECT * FROM games WHERE fingerprint = ?",
            (fp["fingerprint"],)).fetchone()
        if row is None and fp.get("path_hash"):
            row = self._conn.execute(
                "SELECT * FROM games WHERE path_hash = ? "
                "ORDER BY last_seen DESC, id DESC LIMIT 1",
                (fp["path_hash"],)).fetchone()
        if row is None:
            self.last_lookup_reason = (
                "chưa có profile cho game này (fingerprint mới)")
            return None
        game_id = int(row["id"])
        current = {f["rel_path"]: f["sha256"] for f in fp["files"]}
        stored = self._conn.execute(
            "SELECT rel_path, sha256 FROM files WHERE game_id = ?",
            (game_id,)).fetchall()
        for s in stored:
            now_hash = current.get(s["rel_path"])
            if now_hash is None:
                self.last_lookup_reason = (
                    "file đã lưu %r không còn trong game hiện tại; "
                    "không tái dùng profile" % s["rel_path"])
                return None
            if now_hash != s["sha256"]:
                self.last_lookup_reason = (
                    "hash lệch: %r đã đổi (lưu %s != hiện %s); "
                    "không tái dùng profile"
                    % (s["rel_path"], str(s["sha256"])[:8],
                       str(now_hash)[:8]))
                return None
        if row["size"] is not None and fp.get("size") is not None \
                and int(row["size"]) != int(fp["size"]):
            self.last_lookup_reason = (
                "kích thước game đã đổi (lưu %d != hiện %d); "
                "không tái dùng profile" % (int(row["size"]), int(fp["size"])))
            return None
        now = _now_iso()
        self._conn.execute("UPDATE games SET last_seen = ? WHERE id = ?",
                           (now, game_id))
        self._conn.commit()
        files = [dict(r) for r in self._conn.execute(
            "SELECT rel_path, size, sha256, role, adapter_id, score "
            "FROM files WHERE game_id = ? ORDER BY rel_path", (game_id,))]
        locales = [dict(r) for r in self._conn.execute(
            "SELECT entry_path, locale, adapter_id, key_count, maturity "
            "FROM locales WHERE game_id = ? ORDER BY entry_path, locale",
            (game_id,))]
        choices = {r["file_hash"]: r["adapter_id"] for r in self._conn.execute(
            "SELECT file_hash, adapter_id FROM choices WHERE game_id = ?",
            (game_id,))}
        return {"game": {"id": game_id, "bundle_id": row["bundle_id"],
                         "version": row["version"], "engine": row["engine"],
                         "engine_conf": row["engine_conf"]},
                "files": files, "locales": locales, "choices": choices}

    def list_games(self) -> List[Dict[str, Any]]:
        """Danh sách game đã biết, mới thấy gần nhất trước."""
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT id, bundle_id, name, version, engine, engine_conf, "
            "path_hash, fingerprint, size, first_seen, last_seen FROM games "
            "ORDER BY last_seen DESC, id DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ runs/stats
    def record_run(self, game_id: int, mode: str, result: str,
                   error_code: Optional[str] = None) -> int:
        """Ghi một lần chạy (mode vd 'extract'/'mod', result vd 'ok'/'fail').
        Game chưa có -> PB3Error E_INPUT_NOT_FOUND. Trả run id."""
        self._ensure_open()
        if self._game_row(int(game_id)) is None:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "không có game id=%s trong KB" % game_id)
        now = _now_iso()
        cur = self._conn.execute(
            "INSERT INTO runs (game_id, mode, started_at, finished_at, "
            "result, error_code) VALUES (?, ?, ?, ?, ?, ?)",
            (int(game_id), mode, now, now, result, error_code))
        self._conn.commit()
        return int(cur.lastrowid)

    def record_plugin_stats(self, adapter_id: str, version: str,
                            success: bool) -> Dict[str, Any]:
        """Ghi một lần dùng plugin: games_seen +1 (một lần quan sát), success
        hoặc fail +1. Trả dòng plugin_stats sau khi cập nhật."""
        self._ensure_open()
        row = self._conn.execute(
            "SELECT * FROM plugin_stats WHERE adapter_id = ? AND version = ?",
            (adapter_id, version)).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO plugin_stats (adapter_id, version, games_seen, "
                "success, fail) VALUES (?, ?, 1, ?, ?)",
                (adapter_id, version, 1 if success else 0,
                 0 if success else 1))
        else:
            self._conn.execute(
                "UPDATE plugin_stats SET games_seen = games_seen + 1, "
                "success = success + ?, fail = fail + ? "
                "WHERE adapter_id = ? AND version = ?",
                (1 if success else 0, 0 if success else 1,
                 adapter_id, version))
        self._conn.commit()
        row = self._conn.execute(
            "SELECT adapter_id, version, games_seen, success, fail "
            "FROM plugin_stats WHERE adapter_id = ? AND version = ?",
            (adapter_id, version)).fetchone()
        return dict(row)

    def plugin_stats(self) -> Dict[str, int]:
        """Tổng số game đã test theo adapter_id (cộng dồn mọi version).

        Dùng cho ma trận hỗ trợ (sdk/support_matrix.py): cột "số game đã test".
        """
        self._ensure_open()
        rows = self._conn.execute(
            "SELECT adapter_id, SUM(games_seen) FROM plugin_stats "
            "GROUP BY adapter_id").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def _checkpoint(self) -> None:
        """Checkpoint WAL vào file chính (để đo dung lượng thật). Lỗi thì bỏ
        qua — prune vẫn chạy bằng VACUUM."""
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------ quên/dọn
    def forget(self, bundle_id_or_id) -> bool:
        """Quên một game: xóa game + files/locales/choices/runs của nó
        (runs tham chiếu game nên cũng phải xóa, nếu không FK chặn).
        Trả True nếu đã xóa, False nếu không có game đó."""
        self._ensure_open()
        game_id = self._resolve_game_id(bundle_id_or_id)
        if game_id is None:
            return False
        self._conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        self._conn.commit()
        return True

    def prune(self, max_mb: float = 64) -> int:
        """Dọn khi DB vượt dung lượng: xóa runs cũ nhất (một nửa còn lại mỗi
        vòng) rồi VACUUM; trả số byte đã dọn (0 nếu chưa vượt giới hạn).
        Chỉ xóa runs — không xóa game/files/locales/choices. Đo dung lượng
        sau khi checkpoint WAL (nếu không, dữ liệu còn nằm trong file -wal
        và VACUUM không làm file chính nhỏ đi)."""
        self._ensure_open()
        limit = int(max_mb * 1024 * 1024)
        self._checkpoint()
        before = self.size_bytes()
        if before <= limit:
            return 0
        for _ in range(10):
            count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
            if count == 0:
                break
            take = max(1, int(count) // 2)
            self._conn.execute(
                "DELETE FROM runs WHERE id IN (SELECT id FROM runs "
                "ORDER BY started_at ASC, id ASC LIMIT ?)", (take,))
            self._conn.commit()
            self._conn.execute("VACUUM")
            self._checkpoint()
            if self.size_bytes() <= limit:
                break
        self._conn.commit()
        return max(0, before - self.size_bytes())

    # ------------------------------------------------------------ xuất/nhập
    def export_profile(self, bundle_id_or_id) -> Dict[str, Any]:
        """Xuất profile dạng JSON thuần (§8.4): {game:{bundle_id,version,
        engine}, files:[{rel_path,size,sha256_8,role,adapter}],
        locales:[{entry_path,locale,adapter}]} — sha256_8 là 8 ký tự đầu
        của sha256. Thêm khóa phụ "choices" {file_hash: adapter_id} và
        "schema_version" để import vòng tròn giữ nguyên lựa chọn. Không
        chứa dữ liệu game. Game chưa có -> PB3Error E_INPUT_NOT_FOUND."""
        self._ensure_open()
        game_id = self._resolve_game_id(bundle_id_or_id)
        if game_id is None:
            raise PB3Error("E_INPUT_NOT_FOUND",
                           "không có game %r trong KB" % (bundle_id_or_id,))
        row = self._game_row(game_id)
        files = []
        for r in self._conn.execute(
                "SELECT rel_path, size, sha256, role, adapter_id FROM files "
                "WHERE game_id = ? ORDER BY rel_path", (game_id,)):
            sha = r["sha256"] or ""
            files.append({"rel_path": r["rel_path"], "size": r["size"],
                          "sha256_8": sha[:8], "role": r["role"],
                          "adapter": r["adapter_id"]})
        locales = [{"entry_path": r["entry_path"], "locale": r["locale"],
                    "adapter": r["adapter_id"]} for r in self._conn.execute(
            "SELECT entry_path, locale, adapter_id FROM locales "
            "WHERE game_id = ? ORDER BY entry_path, locale", (game_id,))]
        choices = {r["file_hash"]: r["adapter_id"] for r in self._conn.execute(
            "SELECT file_hash, adapter_id FROM choices WHERE game_id = ?",
            (game_id,))}
        return {"schema_version": SCHEMA_VERSION,
                "game": {"bundle_id": row["bundle_id"],
                         "version": row["version"], "engine": row["engine"]},
                "files": files, "locales": locales, "choices": choices}

    def import_profile(self, data: Dict[str, Any],
                       path=None) -> Tuple[int, List[str]]:
        """Nhập profile JSON.

        Profile chỉ là GỢI Ý, không tin tuyệt đối: mỗi file kiểm được thì
        kiểm hash với game tại ``path`` (nếu có); file hash lệch thì BỎ QUA
        + cảnh báo; file không kiểm được thì cảnh báo "chưa kiểm" nhưng vẫn
        lưu (chỉ lưu tiền tố sha256_8, không đủ để lookup tái dùng cho tới
        khi dò lại từ đầu). Không có ``path`` thì mọi file đều "chưa kiểm".
        Dạng sai -> PB3Error E_PLUGIN_INVALID. Trả (game_id, warnings)."""
        self._ensure_open()
        if not isinstance(data, dict) or not isinstance(data.get("game"), dict):
            raise PB3Error("E_PLUGIN_INVALID",
                           "profile cần là dict có khóa 'game'")
        game = data["game"]
        bundle_id = game.get("bundle_id")
        if not isinstance(bundle_id, str) or not bundle_id.strip():
            raise PB3Error("E_PLUGIN_INVALID",
                           "profile cần game.bundle_id là chuỗi không rỗng")
        files_in = data.get("files", [])
        locales_in = data.get("locales", [])
        choices_in = data.get("choices", {})
        if not isinstance(files_in, list) or not isinstance(locales_in, list) \
                or not isinstance(choices_in, dict):
            raise PB3Error("E_PLUGIN_INVALID",
                           "profile sai dạng: 'files'/'locales' cần là list, "
                           "'choices' cần là dict")
        warnings: List[str] = []
        fp: Optional[Dict[str, Any]] = None
        current: Dict[str, str] = {}
        if path is not None:
            fp = fingerprint_game(path)
            current = {f["rel_path"]: f["sha256"] for f in fp["files"]}
        # Kiểm hash từng file nhập.
        kept_files: List[Dict[str, Any]] = []
        for f in files_in:
            if not isinstance(f, dict) or not f.get("rel_path"):
                raise PB3Error("E_PLUGIN_INVALID",
                               "file trong profile cần 'rel_path'")
            rel = f["rel_path"]
            want = f.get("sha256") or f.get("sha256_8") or ""
            want = str(want)
            if path is None:
                kept_files.append({"rel_path": rel, "size": f.get("size"),
                                   "sha256": want[:8] if want else None,
                                   "role": f.get("role"),
                                   "adapter_id": f.get("adapter"),
                                   "score": f.get("score")})
                continue
            have = current.get(rel)
            if have is None:
                warnings.append(
                    "chưa kiểm hash cho %r (không có trong game hiện tại); "
                    "profile chỉ là gợi ý" % rel)
                kept_files.append({"rel_path": rel, "size": f.get("size"),
                                   "sha256": want[:8] if want else None,
                                   "role": f.get("role"),
                                   "adapter_id": f.get("adapter"),
                                   "score": f.get("score")})
            elif want and not have.startswith(want[:8]) \
                    and not want.startswith(have[:8]):
                warnings.append(
                    "hash lệch cho %r (lưu %s != hiện %s): không tin mục "
                    "này, bỏ qua khi nhập" % (rel, want[:8], have[:8]))
            else:
                kept_files.append({"rel_path": rel, "size": f.get("size"),
                                   "sha256": have,
                                   "role": f.get("role"),
                                   "adapter_id": f.get("adapter"),
                                   "score": f.get("score")})
        if path is None and files_in:
            warnings.append(
                "chưa kiểm hash của %d file (không có file game để đối "
                "chiếu — profile chỉ là gợi ý, vẫn cần chạy probe)"
                % len(files_in))
        locales = []
        for loc in locales_in:
            if not isinstance(loc, dict) or not loc.get("entry_path") \
                    or not loc.get("locale"):
                raise PB3Error("E_PLUGIN_INVALID",
                               "locale trong profile cần 'entry_path' + 'locale'")
            locales.append({"entry_path": loc["entry_path"],
                            "locale": loc["locale"],
                            "adapter_id": loc.get("adapter"),
                            "key_count": loc.get("key_count"),
                            "maturity": loc.get("maturity")})
        now = _now_iso()
        row = self._conn.execute(
            "SELECT id FROM games WHERE bundle_id = ? "
            "ORDER BY last_seen DESC, id DESC LIMIT 1",
            (bundle_id,)).fetchone()
        if row is not None:
            game_id = int(row["id"])
            if fp is not None:
                self._conn.execute(
                    "UPDATE games SET version=?, engine=?, engine_conf=?, "
                    "path_hash=?, fingerprint=?, size=?, last_seen=? "
                    "WHERE id=?",
                    (game.get("version"), game.get("engine"),
                     fp.get("engine_conf"), fp.get("path_hash"),
                     fp["fingerprint"], fp.get("size"), now, game_id))
            else:
                self._conn.execute(
                    "UPDATE games SET version=?, engine=?, last_seen=? "
                    "WHERE id=?",
                    (game.get("version"), game.get("engine"), now, game_id))
            self._conn.execute("DELETE FROM files WHERE game_id = ?",
                               (game_id,))
            self._conn.execute("DELETE FROM locales WHERE game_id = ?",
                               (game_id,))
            self._conn.execute("DELETE FROM choices WHERE game_id = ?",
                               (game_id,))
        else:
            cur = self._conn.execute(
                "INSERT INTO games (bundle_id, name, version, engine, "
                "engine_conf, path_hash, fingerprint, size, first_seen, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bundle_id, bundle_id, game.get("version"),
                 game.get("engine"),
                 fp.get("engine_conf") if fp else None,
                 fp.get("path_hash") if fp else None,
                 fp["fingerprint"] if fp else None,
                 fp.get("size") if fp else None, now, now))
            game_id = int(cur.lastrowid)
        for f in kept_files:
            self._conn.execute(
                "INSERT OR REPLACE INTO files (game_id, rel_path, size, "
                "sha256, role, adapter_id, score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_id, f["rel_path"], f.get("size"), f.get("sha256"),
                 f.get("role"), f.get("adapter_id"), f.get("score")))
        for loc in locales:
            self._conn.execute(
                "INSERT OR REPLACE INTO locales (game_id, entry_path, locale, "
                "adapter_id, key_count, maturity) VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, loc["entry_path"], loc["locale"],
                 loc.get("adapter_id"), loc.get("key_count"),
                 loc.get("maturity")))
        for file_hash, adapter_id, chosen_by, note in _iter_choices(choices_in):
            self._conn.execute(
                "INSERT OR REPLACE INTO choices (game_id, file_hash, "
                "adapter_id, chosen_by, note) VALUES (?, ?, ?, ?, ?)",
                (game_id, file_hash, adapter_id, chosen_by, note))
        self._conn.commit()
        return game_id, warnings

    # ------------------------------------------------------------ đóng/đo
    def size_bytes(self) -> int:
        """Dung lượng DB trên đĩa (byte; 0 nếu chưa có file): file SQLite
        chính + file -wal (WAL chưa checkpoint vẫn chiếm dung lượng thật).
        File -shm là bộ nhớ chia sẻ, không tính."""
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += int(os.path.getsize(str(self.db_path) + suffix))
            except OSError:
                pass
        return total

    def close(self) -> None:
        """Đóng kết nối (gọi nhiều lần cũng an toàn)."""
        if self._conn is not None:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def __enter__(self) -> "KB":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def _iter_choices(choices):
    """Chuẩn hóa choices (dict {file_hash: adapter_id} hoặc list dict)
    thành list (file_hash, adapter_id, chosen_by, note)."""
    out = []
    if choices is None:
        return out
    if isinstance(choices, dict):
        for file_hash, adapter_id in choices.items():
            out.append((str(file_hash), adapter_id, "user", None))
        return out
    if isinstance(choices, list):
        for item in choices:
            if not isinstance(item, dict) or not item.get("file_hash"):
                raise PB3Error("E_PLUGIN_INVALID",
                               "choice cần 'file_hash'")
            out.append((str(item["file_hash"]), item.get("adapter_id"),
                        item.get("chosen_by") or "user",
                        item.get("note")))
        return out
    raise PB3Error("E_PLUGIN_INVALID",
                   "choices cần là dict {file_hash: adapter_id} hoặc list")


# ============================================================ inventory cache
def inventory_key(path) -> Tuple[str, int, int, str]:
    """Khóa cache của một file: (đường dẫn tuyệt đối đã resolve, mtime_ns,
    size, sha256 của 1MB đầu + 1MB cuối).

    File <= 2MB thì hash toàn bộ (đầu/cuối chồng nhau). Khóa đổi khi file
    đổi (mtime/size/hash) -> mục cache hết hạn. File không tồn tại ->
    PB3Error E_INPUT_NOT_FOUND."""
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file: %s" % str(path),
                       where=str(path))
    st = p.stat()
    size = int(st.st_size)
    h = hashlib.sha256()
    with open(str(p), "rb") as fh:
        if size <= 2 * INVENTORY_EDGE_BYTES:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        else:
            h.update(fh.read(INVENTORY_EDGE_BYTES))
            fh.seek(size - INVENTORY_EDGE_BYTES)
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    return (str(p.resolve()), int(st.st_mtime_ns), size, h.hexdigest())


class InventoryCache:
    """Cache JSON kết quả inventory: <cache_dir>/inventory_cache.json.

    Ghi nguyên tử (AtomicWriter, mẫu decisions #111); LRU tối đa 32 mục /
    8MB; file cache hỏng -> bỏ qua, bắt đầu rỗng. Hit KHÔNG ghi lại file
    (chỉ đổi thứ tự trong bộ nhớ; thứ tự bền vững được ghi ở lần put sau
    — như cache plan #111). Mục hết hạn khi inventory_key đổi."""

    MAX_ITEMS = INVENTORY_MAX_ITEMS
    MAX_BYTES = INVENTORY_MAX_BYTES

    def __init__(self, cache_dir):
        self.dir = Path(os.path.expanduser(str(cache_dir)))
        self.file = self.dir / INVENTORY_FILENAME
        self._items: List[Dict[str, Any]] = []
        self._load()

    # -- nạp/lưu --
    def _load(self) -> None:
        try:
            with open(str(self.file), "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            self._items = []
            return
        if not isinstance(doc, dict) or doc.get("version") != 1 \
                or not isinstance(doc.get("items"), list):
            self._items = []
            return
        items = []
        for item in doc["items"]:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("path"), str) \
                    or not isinstance(item.get("key"), list) \
                    or len(item["key"]) != 4 \
                    or not isinstance(item.get("entries"), list):
                continue
            items.append({"path": item["path"], "key": item["key"],
                          "entries": item["entries"]})
        self._items = items[: self.MAX_ITEMS]

    def _doc_bytes(self) -> bytes:
        doc = {"version": 1, "items": self._items}
        return json.dumps(doc, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")

    def _save(self) -> None:
        """Ghi nguyên tử; lỗi I/O thì bỏ qua im lặng (cache hỏng không được
        làm hỏng thao tác chính — mẫu cache plan #111)."""
        try:
            data = self._doc_bytes()
            with AtomicWriter(self.file, expected_size=len(data),
                              disk_factor=1.0) as w:
                w.write(data)
        except Exception:
            pass

    def _trim(self) -> None:
        """Cắt LRU: tối đa 32 mục, tổng JSON tối đa 8MB (bỏ mục cũ nhất)."""
        del self._items[self.MAX_ITEMS:]
        while self._items and len(self._doc_bytes()) > self.MAX_BYTES:
            self._items.pop()

    # -- API --
    def key(self, path) -> Tuple[str, int, int, str]:
        """inventory_key của path (xem hàm cùng tên ở module)."""
        return inventory_key(path)

    def get(self, path) -> Optional[List[Any]]:
        """Trả entries đã cache nếu key khớp; None khi miss/hết hạn."""
        try:
            key = inventory_key(path)
        except PB3Error:
            return None
        want_path = str(Path(os.path.expanduser(str(path))).resolve())
        for i, item in enumerate(self._items):
            if item["path"] == want_path and list(key) == list(item["key"]):
                self._items.insert(0, self._items.pop(i))
                return [dict(e) if isinstance(e, dict) else e
                        for e in item["entries"]]
        return None

    def put(self, path, entries) -> bool:
        """Lưu entries cho path. Trả False khi entries không JSON được hoặc
        một mục đơn đã vượt 8MB (không lưu); True khi đã lưu."""
        if not isinstance(entries, list):
            return False
        try:
            json.dumps(entries, ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        try:
            key = inventory_key(path)
        except PB3Error:
            return False
        want_path = str(Path(os.path.expanduser(str(path))).resolve())
        self._items = [it for it in self._items if it["path"] != want_path]
        self._items.insert(0, {"path": want_path, "key": list(key),
                               "entries": entries})
        self._trim()
        if not self._items or self._items[0]["path"] != want_path:
            return False   # mục đơn đã vượt 8MB nên bị cắt
        self._save()
        return True

    def clear(self) -> int:
        """Xóa toàn bộ cache; trả số mục đã xóa."""
        n = len(self._items)
        self._items = []
        try:
            os.unlink(str(self.file))
        except OSError:
            pass
        return n

    def __len__(self) -> int:
        return len(self._items)
