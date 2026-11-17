# -*- coding: utf-8 -*-
"""Xem sâu (deep inspect) — liệt kê bundle/.assets + nguồn ngôn ngữ + lý do bỏ qua.

Chỉ đọc, không sửa gì. Chạy được cả trên web (Pyodide) lẫn Python.
Đọc bundle theo luồng (stream_bundle_entries): không ôm cả payload giải
nén vào RAM — RAM đỉnh ~2× entry .assets lớn nhất + bytes entry đang đọc,
thay vì payload + nhiều bản copy như parse_bundle.

Mỗi file Unity báo cáo: tên, kích thước, UnityFS version, compression,
số entry, số object theo loại (MonoBehaviour/TextAsset/...), TextAsset
(tên + cỡ + 200 ký tự đầu; file nhị phân đếm riêng), nguồn ngôn ngữ tìm
thấy (I2 language_source / StringTable / SharedTableData), và lý do
pipeline trích xuất bỏ qua file đó (nếu có: vượt giới hạn entry 64 MB,
UnityFS v9 chỉ đọc khi ghi/mod, mã hóa...).

Tương thích Python 3.9, stdlib-only.
"""

from __future__ import annotations

import io
import os
from collections import Counter

from core.errors import PB3Error
from core.limits import Limits
from sdk import unity as unity_sdk

__all__ = ["inspect_unity_stream", "inspect_unity_bytes",
           "inspect_container", "LABEL"]

LABEL = "deep-inspect"

MAX_TEXT_HEAD = 200       # ký tự đầu TextAsset đưa vào báo cáo
MAX_SAMPLE_NAMES = 5      # số tên mẫu mỗi loại
MAX_CLASS_ROWS = 10       # số loại object liệt kê (còn lại gộp "khác")


def _compression_name(code):
    return unity_sdk.COMPRESSION_NAMES.get(code, "?")


def _sf_summary(sf, entry_name):
    """Tóm tắt 1 SerializedFile: object theo loại, TextAsset, nguồn ngôn ngữ."""
    out = {"entry": entry_name, "sf_version": int(sf.version),
           "unity_version": sf.unity_version,
           "objects": len(getattr(sf, "objects", []) or [])}
    # Object theo loại (tên class thân thiện, vd MonoBehaviour/TextAsset).
    counts = Counter()
    for o in getattr(sf, "objects", []) or []:
        counts[unity_sdk.class_name(getattr(o, "class_id", -1))] += 1
    top = counts.most_common(MAX_CLASS_ROWS)
    rest = sum(c for _, c in counts.items()) - sum(c for _, c in top)
    out["objects_by_class"] = [{"class": k, "count": int(v)} for k, v in top]
    if rest:
        out["objects_by_class"].append({"class": "khác", "count": int(rest)})
    # TextAsset: tên + cỡ + 200 ký tự đầu; file nhị phân (đọc lỗi) đếm riêng.
    ta_warns = []
    tas = []
    try:
        raw_tas = sf.text_assets(warnings=ta_warns)
    except Exception:  # noqa: BLE001 - inspect không bao giờ crash vì 1 file
        raw_tas = []
    for _pid, name, script in raw_tas:
        tas.append({"name": name, "size": len(script),
                    "head": script[:MAX_TEXT_HEAD]})
        if len(tas) >= 50:
            break
    out["text_assets"] = tas
    out["text_assets_total"] = len(raw_tas)
    out["text_assets_binary_dropped"] = len(ta_warns)
    # MonoBehaviour: đếm theo loại + nguồn ngôn ngữ tìm thấy.
    mb_counts = Counter()
    lang_sources = []
    string_tables = []
    shared_tables = []
    generic_names = []
    mb_warns = []
    try:
        items = sf.monobehaviour_texts(warnings=mb_warns)
    except Exception:  # noqa: BLE001
        items = []
    for item in items:
        kind = item.get("kind")
        mb_counts[kind or "unknown"] += 1
        if kind == "language_source":
            langs = item.get("languages") or []
            terms = item.get("terms") or []
            lang_sources.append({
                "name": item.get("name") or "",
                "languages": [l.get("code") or l.get("name") or ""
                              for l in langs if isinstance(l, dict)],
                "terms": len(terms)})
        elif kind == "string_table":
            entries = item.get("entries") or {}
            string_tables.append({"name": item.get("name") or "",
                                  "locale": item.get("locale"),
                                  "entries": len(entries)})
        elif kind == "shared_table":
            entries = item.get("entries") or {}
            shared_tables.append({"name": item.get("name") or "",
                                  "entries": len(entries)})
        elif kind == "generic":
            nm = item.get("name") or ""
            if nm and len(generic_names) < MAX_SAMPLE_NAMES:
                generic_names.append(nm)
    out["monobehaviours"] = {k: int(v) for k, v in mb_counts.items()}
    out["language_sources"] = lang_sources
    out["string_tables"] = string_tables
    out["shared_tables"] = shared_tables
    out["generic_mb_sample_names"] = generic_names
    out["warnings"] = (ta_warns + mb_warns)[:10]
    return out


def _bundle_layout_info(fh, where):
    """Đọc header + blocks-info (không payload): version, nén, entry, cỡ."""
    backend = unity_sdk.UnityBackend("AUTO", Limits())
    layout = unity_sdk._read_bundle_layout(fh, backend, Limits(), where=where)
    comp_codes = Counter(b.compression for b in layout.blocks)
    comp_names = {}
    for code, n in comp_codes.items():
        comp_names[_compression_name(code)] = int(n)
    return {
        "version": int(layout.version),
        "unity_version": layout.unity_version,
        "unity_revision": layout.unity_revision,
        "file_size": int(layout.file_size),
        "compression": comp_names,
        "blocks": len(layout.blocks),
        "total_compressed": sum(b.compressed_size for b in layout.blocks),
        "total_uncompressed": sum(b.uncompressed_size for b in layout.blocks),
        "entries": [{"name": e.name, "size": int(e.size),
                     "serialized": bool(e.is_serialized)}
                    for e in layout.entries],
    }


def inspect_unity_stream(stream_or_path, name, limits=None, where=None):
    """Xem sâu 1 bundle UnityFS (đường dẫn hoặc stream seekable) hoặc 1 file
    .assets độc lập. Trả dict báo cáo; lỗi từng entry ghi vào notes, không
    raise (trừ khi cả file không đọc được header)."""
    lim = limits or Limits()
    where = where or name
    report = {"file": name, "label": LABEL, "notes": []}
    backend = unity_sdk.UnityBackend("AUTO", lim)
    own = isinstance(stream_or_path, (str, os.PathLike))
    fh = None
    try:
        if own:
            fh = open(stream_or_path, "rb")
            stream = fh
        else:
            stream = stream_or_path
        # Phân biệt bundle vs .assets độc lập bằng header.
        head = stream.read(8)
        is_bundle = len(head) >= 7 and head[:7] == b"UnityFS"
        try:
            stream.seek(0)
        except Exception:  # noqa: BLE001 - stream không seek được: đọc lại từ đầu bằng BytesIO
            data = head + stream.read()
            stream = io.BytesIO(data)
            is_bundle = len(data) >= 7 and data[:7] == b"UnityFS"
        if not is_bundle:
            # File .assets độc lập: đọc thẳng (đã ở trong RAM/stream).
            data = stream.read()
            try:
                sf = unity_sdk.parse_serialized(data, lim, where=where)
            except PB3Error as exc:
                report["error"] = {"code": exc.code,
                                   "message": exc.message_vi or exc.detail}
                return report
            report["kind"] = "assets"
            report["size"] = len(data)
            report["files"] = [_sf_summary(sf, name)]
            report["language_sources_found"] = bool(
                report["files"][0]["language_sources"]
                or report["files"][0]["string_tables"])
            return report
        # Bundle: layout (header only) + stream từng entry.
        try:
            stream.seek(0)
        except Exception:  # noqa: BLE001
            pass
        layout = _bundle_layout_info(stream, where)
        report.update({"kind": "bundle",
                       "size": int(layout.get("file_size") or 0)})
        report["bundle"] = layout
        if int(layout["version"]) >= 9:
            report["notes"].append(
                "UnityFS v%d: trích xuất đọc được, ghi/mod chỉ đọc (không "
                "rebuild được bundle v9)" % int(layout["version"]))
        files = []
        for entry_name, entry_data in unity_sdk.stream_bundle_entries(
                stream, backend, lim, where=where):
            info = {"entry": entry_name, "size": int(len(entry_data))}
            serialized = any(e["name"] == entry_name and e["serialized"]
                             for e in layout["entries"])
            if not serialized:
                info["note"] = ("không phải SerializedFile (dữ liệu thô: "
                                ".resS/texture/âm thanh...)")
                files.append(info)
                continue
            try:
                sf = unity_sdk.parse_serialized(entry_data, lim,
                                                where=entry_name)
            except PB3Error as exc:
                info["error"] = {"code": exc.code,
                                 "message": exc.message_vi or exc.detail}
                files.append(info)
                continue
            files.append(_sf_summary(sf, entry_name))
        report["files"] = files
        report["language_sources_found"] = any(
            (f.get("language_sources") or f.get("string_tables"))
            for f in files if isinstance(f, dict))
        return report
    finally:
        if fh is not None:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def inspect_unity_bytes(data, name, limits=None, where=None):
    """Xem sâu bundle/.assets đã ở trong RAM (vd entry trong ZIP)."""
    return inspect_unity_stream(io.BytesIO(bytes(data)), name,
                                limits=limits, where=where or name)


def inspect_container(source, limits=None, *, entry=None):
    """Xem sâu 1 file container (IPA/ZIP/TAR) hoặc 1 file Unity độc lập.

    source: đường dẫn file (khuyến nghị — VFS lười, không nạp cả file)
    hoặc bytes. entry: chỉ xem sâu 1 entry Unity trong container (None =
    xem tất cả). Trả dict báo cáo tổng + từng file Unity + lý do bỏ qua
    của pipeline trích xuất.
    """
    from sdk import containers, discovery as disc
    from sdk.containers import NestedVFS
    from sdk.vfs import SingleFileVFS

    lim = limits or Limits()
    # Giới hạn MẶC ĐỊNH của pipeline trích xuất (64 MB/entry): lý do bỏ qua
    # của pipeline luôn so với giới hạn này, KHÔNG phải giới hạn đã nới
    # để inspect đọc sâu (nới rồi mới so thì file 78 MB sẽ bị báo nhầm
    # là "pipeline sẽ mở").
    default_lim = Limits()
    name = source if isinstance(source, str) else "<bytes>"
    report = {"label": LABEL, "file": str(name), "unity_files": [],
              "skipped": [], "notes": []}
    try:
        root_vfs = disc.open_source_vfs(source, lim)
    except PB3Error as exc:
        report["ok"] = False
        report["error"] = {"code": exc.code,
                           "message": exc.message_vi or exc.detail}
        return report
    try:
        inv = disc.inventory(root_vfs, lim)
    except PB3Error as exc:
        report["ok"] = False
        report["error"] = {"code": exc.code,
                           "message": exc.message_vi or exc.detail}
        return report
    # Entry Unity vượt giới hạn đọc mặc định (64 MB): inspect cố ý đọc sâu
    # nên mở lại VFS với giới hạn nới lên đúng cỡ entry lớn nhất đã khai
    # báo trong inventory (không phải giới hạn mở — entry vẫn được stream
    # từng phần sau đó, không ôm cả payload; file độc lập trên đĩa thì
    # đã stream từ đường dẫn nên không cần nới).
    if not isinstance(root_vfs, SingleFileVFS):
        oversize = [int(e.size) for e in inv
                    if e.kind == "unity" and e.size > lim.max_entry_bytes]
        if oversize:
            need = max(oversize)
            lim = lim.with_overrides(
                max_entry_bytes=need,
                max_memory_read_bytes=max(lim.max_memory_read_bytes, need))
            root_vfs = disc.open_source_vfs(source, lim)
            inv = disc.inventory(root_vfs, lim)
            report["notes"].append(
                "giới hạn đọc nới lên %d byte để xem sâu entry Unity lớn "
                "nhất (mặc định %d byte)" % (need, Limits().max_entry_bytes))
    report["total_entries"] = len(inv)
    by_kind = Counter(e.kind for e in inv)
    report["entries_by_kind"] = {k: int(v) for k, v in by_kind.items()}
    biggest = sorted(inv, key=lambda e: -e.size)[:10]
    report["biggest_entries"] = [{"path": e.path, "size": int(e.size),
                                  "kind": e.kind} for e in biggest]
    nested = NestedVFS(root_vfs, limits=lim)
    for e in inv:
        if e.kind != "unity":
            continue
        if entry is not None and e.path != entry:
            continue
        item = {"path": e.path, "size": int(e.size)}
        # Lý do pipeline trích xuất bỏ qua (nếu có) — khớp _build_layers,
        # SO VỚI GIỚI HẠN MẶC ĐỊNH (xem default_lim ở trên).
        if e.size > default_lim.max_entry_bytes:
            if isinstance(root_vfs, SingleFileVFS) and e.path == root_vfs.name:
                item["pipeline"] = ("vượt giới hạn entry nhưng là file độc "
                                    "lập trên đĩa → pipeline đọc streaming")
            else:
                item["pipeline"] = (
                    "pipeline trích xuất BỎ QUA: file Unity %d byte > giới "
                    "hạn entry %d byte (nằm trong ZIP/IPA nên không stream "
                    "được)" % (e.size, default_lim.max_entry_bytes))
                report["skipped"].append(
                    {"path": e.path,
                     "reason": item["pipeline"]})
        else:
            item["pipeline"] = "pipeline trích xuất sẽ mở"
        # Xem sâu nội dung: stream từ đĩa nếu là file độc lập, còn không
        # đọc entry vào RAM rồi stream từ BytesIO (giới hạn: 1 bản copy
        # của entry + cửa sổ trượt ~2× entry .assets lớn nhất).
        try:
            if isinstance(root_vfs, SingleFileVFS) and e.path == root_vfs.name:
                deep = inspect_unity_stream(root_vfs.source_path, e.path,
                                            limits=lim, where=e.path)
            else:
                data = bytes(nested.read(e.path, None))
                deep = inspect_unity_bytes(data, e.path, limits=lim,
                                           where=e.path)
        except PB3Error as exc:
            item["error"] = {"code": exc.code,
                             "message": exc.message_vi or exc.detail}
            report["unity_files"].append(item)
            continue
        except Exception as exc:  # noqa: BLE001 - inspect không crash vì 1 file
            item["error"] = {"code": "E_INSPECT",
                             "message": str(exc)}
            report["unity_files"].append(item)
            continue
        item["deep"] = deep
        report["unity_files"].append(item)
    if entry is not None and not report["unity_files"]:
        report["notes"].append("không tìm thấy entry Unity %r trong file"
                               % entry)
    report["ok"] = True
    return report
