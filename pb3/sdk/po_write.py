"""Ghi lại file .po trong IPA/ZIP (kể cả lồng nhau) — caps-po.

Đường ghi .po cho game không-Unity (vd Don't Starve Together: file
`vietnamese.po` 18 MB nằm trong `scripts.zip` trong IPA, đã dịch đủ
100% nhưng cần sửa lại vài msgstr rồi đóng gói lại).

- Đọc .po lá qua NestedVFS (vpath dạng "a.zip::b.zip::dir/file.po",
  tối đa 4 đoạn, fail-closed nếu hơn), parse bằng plugin po_mo
  (đã có _build_bytes + build tự verify bằng parse lại, test khóa
  trong test_format_po_mo.py).
- Chỉ sửa key CÓ trong file; key lạ báo unknown, không ghi thêm
  (đúng luật plugin.build); key dịch giống giá trị cũ tính là
  unchanged, không ghi.
- dry_run (mặc định): chỉ trả preview, KHÔNG ghi file.
- apply (dry_run=False + confirm=True; thiếu confirm ->
  E_CONFIG_INVALID, không ghi gì): rebuild từ trong ra ngoài bằng
  containers.rebuild_zip (temp file + AtomicWriter + verify), output
  "<stem>-po-mod.<ext>" cạnh file gốc (KHÔNG ghi đè gốc); verify sau
  ghi: mở lại output, trích .po, parse lại, đối chiếu key đã sửa ==
  giá trị mới + key còn lại không đổi + cùng thứ tự; FAIL thì xóa
  output lỗi + E_VERIFY_FAILED (như M2 repack).
- KHÔNG đi qua (E)/gate: .po là game không-Unity, không có provenance
  Unity; verify 2 lớp của riêng nó (plugin.build + re-parse sau ghi)
  là đủ.

Chỉ thư viện chuẩn, Python >= 3.9. Không import UnityPy/lz4.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from core.errors import PB3Error
from core.limits import Limits
from sdk import discovery as disc
from sdk.api import PluginContext
from sdk.containers import NestedVFS, rebuild_zip
from sdk.types import Candidate, LocEntry, LocTable, SourceRef
from sdk.vfs import SEP, MemoryVFS, check_vpath

MAX_PO_VPATH_SEGMENTS = 4


def sha256_file(path: str) -> str:
    """SHA-256 của file, đọc theo chunk 1 MB (không nạp cả file lớn vào RAM)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _po_plugin():
    """Plugin po_mo (import lười để tránh vòng import lúc nạp sdk)."""
    from plugins.po_mo.plugin import PoMoPlugin

    return PoMoPlugin()


def _parse_po_bytes(po_bytes: bytes, leaf: str, limits):
    """Parse bytes .po bằng plugin po_mo -> (plugin, ctx, LocTable)."""
    plugin = _po_plugin()
    ctx = PluginContext(MemoryVFS({leaf: po_bytes}), limits).for_plugin(
        plugin.manifest)
    table = plugin.parse(ctx, Candidate.for_path(leaf, plugin.id, 1.0))
    return plugin, ctx, table


def _check_po_values(po_bytes: bytes, leaf: str, limits,
                     want_changed: Dict[str, str],
                     old_values: Dict[str, str], old_order: List[str]) -> None:
    """Verify sau ghi: parse lại .po mới, đối chiếu toàn bộ.

    - key đã sửa phải == giá trị mới;
    - key còn lại phải == giá trị cũ;
    - tập key + thứ tự key phải giữ nguyên.
    Lệch -> E_VERIFY_FAILED.
    """
    _plugin, _ctx, table = _parse_po_bytes(po_bytes, leaf, limits)
    new_order = [e.key for e in table.entries]
    if new_order != old_order:
        raise PB3Error("E_VERIFY_FAILED",
                       "tập/thứ tự key .po sau khi ghi khác gốc "
                       "(%d -> %d key)" % (len(old_order), len(new_order)),
                       where=leaf)
    new_values = {e.key: e.value for e in table.entries}
    for key, want in want_changed.items():
        if new_values.get(key) != want:
            raise PB3Error("E_VERIFY_FAILED",
                           "key %r sau khi ghi != giá trị mới" % key,
                           where=leaf)
    for key, old in old_values.items():
        if key not in want_changed and new_values.get(key) != old:
            raise PB3Error("E_VERIFY_FAILED",
                           "key %r đổi ngoài ý muốn khi ghi" % key,
                           where=leaf)


def po_write(path, po_vpath, translations, dry_run=True, confirm=False,
             limits=None, tempdir=None) -> Dict[str, Any]:
    """Sửa msgstr trong 1 file .po rồi ghi lại (xem docstring module).

    path: file nguồn (IPA/ZIP chứa .po, hoặc chính file .po).
    po_vpath: đường dẫn ảo tới .po, vd
      "Payload/dontstarvetogether.app/data/databundles/scripts.zip::"
      "scripts/languages/vietnamese.po" (tối đa 4 đoạn).
    translations: dict {msgid: msgstr mới} (chuỗi -> chuỗi).
    dry_run=True (mặc định): chỉ preview. dry_run=False + confirm=True
    mới ghi thật ra "<stem>-po-mod.<ext>" cạnh file gốc.
    Trả dict kết quả (xem bridge.po_apply).
    """
    lim = limits or Limits()
    if not isinstance(path, (str, os.PathLike)) or not str(path):
        raise PB3Error("E_CONFIG_INVALID", "path trống", where="?")
    if not isinstance(po_vpath, str) or not po_vpath:
        raise PB3Error("E_CONFIG_INVALID", "po_vpath trống", where="?")
    if not isinstance(translations, dict) or not translations:
        raise PB3Error("E_CONFIG_INVALID",
                       "translations phải là dict {msgid: msgstr mới} "
                       "không rỗng", where=po_vpath)
    for key, val in translations.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise PB3Error("E_CONFIG_INVALID",
                           "translations chỉ nhận chuỗi -> chuỗi",
                           where=po_vpath)
    check_vpath(po_vpath)
    segments = po_vpath.split(SEP)
    if len(segments) > MAX_PO_VPATH_SEGMENTS:
        raise PB3Error("E_CONFIG_INVALID",
                       "po_vpath quá %d đoạn (được %d)"
                       % (MAX_PO_VPATH_SEGMENTS, len(segments)),
                       where=po_vpath)
    if any(not s for s in segments):
        raise PB3Error("E_CONFIG_INVALID", "po_vpath có đoạn rỗng",
                       where=po_vpath)
    leaf = segments[-1]
    if not leaf.lower().endswith(".po"):
        raise PB3Error("E_CONFIG_INVALID",
                       "chỉ ghi được file .po (không ghi .mo nhị phân)",
                       where=leaf)
    src_path = str(path)
    if not os.path.isfile(src_path):
        raise PB3Error("E_INPUT_NOT_FOUND", "không có file nguồn",
                       where=src_path)
    orig_sha = sha256_file(src_path)

    # ---- đọc + parse .po lá ----
    if len(segments) == 1:
        # Chính file nguồn là .po (không container).
        with open(src_path, "rb") as fh:
            po_bytes = fh.read()
        lim.check_entry_size(len(po_bytes), leaf)
    else:
        root_vfs = disc.open_source_vfs(src_path, lim)
        nested = NestedVFS(root_vfs, limits=lim)
        po_bytes = nested.read(po_vpath, None)
    plugin, ctx, table = _parse_po_bytes(po_bytes, leaf, lim)
    old_values = {e.key: e.value for e in table.entries}
    old_order = [e.key for e in table.entries]
    changed = {k: v for k, v in translations.items()
               if k in old_values and old_values[k] != v}
    unchanged = sorted(k for k, v in translations.items()
                       if k in old_values and old_values[k] == v)
    unknown = sorted(k for k in translations if k not in old_values)

    # ---- build bytes mới (plugin tự verify bằng parse lại) ----
    if changed:
        edited = LocTable(
            [LocEntry(k, v, table.locale, "native", "ok",
                      SourceRef([po_vpath], po_vpath))
             for k, v in sorted(changed.items())],
            table.locale, plugin.id)
        new_po_bytes = plugin.build(ctx, edited, po_bytes)
    else:
        new_po_bytes = po_bytes
    result: Dict[str, Any] = {
        "ok": True, "dry_run": bool(dry_run), "path": src_path,
        "po_vpath": po_vpath, "total_entries": len(old_values),
        "changed": len(changed), "changed_keys": sorted(changed),
        "unchanged": len(unchanged), "unknown": unknown,
        "before_sha256": hashlib.sha256(po_bytes).hexdigest(),
        "after_sha256": hashlib.sha256(new_po_bytes).hexdigest(),
        "new_size": len(new_po_bytes), "output": None,
        "output_size": None,
    }
    if dry_run or not changed:
        return result
    if not confirm:
        raise PB3Error("E_CONFIG_INVALID",
                       "ghi thật .po cần confirm=True (dry_run=False "
                       "chưa đủ — chống ghi nhầm)", where=po_vpath)

    # ---- ghi thật: rebuild từ trong ra ngoài ----
    stem, ext = os.path.splitext(os.path.basename(src_path))
    out_name = "%s-po-mod%s" % (stem, ext)
    out_path = os.path.join(os.path.dirname(os.path.abspath(src_path)),
                            out_name)
    workdir = tempfile.mkdtemp(prefix="pb3-po-", dir=tempdir)
    try:
        if len(segments) == 1:
            # File .po trần: ghi trực tiếp bằng AtomicWriter + verify.
            from core.safe_io import AtomicWriter

            def _verify_po(tmp) -> None:
                with open(str(tmp), "rb") as fh:
                    data = fh.read()
                _check_po_values(data, leaf, lim, changed, old_values,
                                 old_order)

            with AtomicWriter(out_path, expected_size=len(new_po_bytes),
                              verify=_verify_po) as w:
                w.write(new_po_bytes)
        else:
            containers = segments[:-1]
            # Bytes từng tầng container (đọc từ ngoài vào).
            level_bytes: List[bytes] = []
            for i in range(len(containers)):
                level_bytes.append(
                    nested.read(SEP.join(segments[:i + 1]), None))
            cur_bytes = new_po_bytes
            cur_name = leaf
            for i in range(len(containers) - 1, -1, -1):
                tmp_dest = os.path.join(workdir, "level%d.zip" % i)
                rebuild_zip(level_bytes[i], tmp_dest,
                            {cur_name: cur_bytes}, limits=lim)
                with open(tmp_dest, "rb") as fh:
                    cur_bytes = fh.read()
                cur_name = containers[i]
                os.unlink(tmp_dest)
            rebuild_zip(src_path, out_path, {containers[0]: cur_bytes},
                        limits=lim)
        # ---- verify sau ghi: mở lại output, trích .po, parse lại ----
        out_vfs = disc.open_source_vfs(out_path, lim)
        out_nested = NestedVFS(out_vfs, limits=lim)
        if len(segments) == 1:
            with open(out_path, "rb") as fh:
                out_po = fh.read()
        else:
            out_po = out_nested.read(po_vpath, None)
        _check_po_values(out_po, leaf, lim, changed, old_values, old_order)
    except Exception:
        try:
            if os.path.isfile(out_path):
                os.unlink(out_path)
        except OSError:
            pass
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    # File gốc không đổi (rebuild_zip không bao giờ ghi đè nguồn, nhưng
    # xác minh lại cho chắc như M2 repack).
    if sha256_file(src_path) != orig_sha:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        raise PB3Error("E_VERIFY_FAILED",
                       "file gốc đổi trong lúc ghi .po — từ chối kết quả",
                       where=src_path)
    result["output"] = out_path
    result["output_size"] = os.path.getsize(out_path)
    return result
