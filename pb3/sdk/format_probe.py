"""Format probe cho engine lạ (mục phân tích C8, BearTea web).

Probe một file bất kỳ và trả báo cáo CÓ CẤU TRÚC — định dạng/magic/
kích thước, engine đoán được + độ tin cậy + bằng chứng, thao tác nào
được hỗ trợ (liệt kê thật theo plugin hiện có), thao tác nào KHÔNG hỗ
trợ, bước tiếp theo gợi ý — thay vì crash hoặc giả vờ hỗ trợ.

Nguyên tắc trung thực (ghi rõ trong báo cáo, không hardcode PASS giả):
- Nhận diện dùng MỘT nguồn sự thật duy nhất: sdk.discovery
  (engine_signals / detect_engine / engine_level). Probe chỉ CỘNG THÊM
  2 bằng chứng mà discovery chưa xét ở cấp file đơn lẻ, và ghi rõ từng
  bằng chứng thêm đó trong report:
  (1) magic footer PAK của Unreal (0x5A6F12E1 trong 4KB cuối file —
  discovery chỉ xét mẫu ĐẦU file nên file .pak đơn lẻ không có magic
  đầu; footer này cũng là thứ sdk.unreal.PakVFS xác minh khi mở);
  (2) magic "TR" ở đầu file .translation của Godot (định dạng theo
  nhiệm vụ Claude 08 — CHƯA đối chiếu file Godot thật xuất ra, file
  thật dạng RSRC sẽ KHÔNG khớp và probe báo đúng là chưa đủ bằng chứng).
- Mức hỗ trợ thật lấy từ MANIFEST của từng plugin (maturity +
  capabilities) qua `active_plugins` do bridge truyền vào (plugin nào
  chưa bật trong phiên thì thao tác của nó rơi xuống "không hỗ trợ"
  kèm lý do). Bảng chữ trong ENGINE_SUPPORT chỉ là DIỄN ĐẠT lại docs
  của manifest — không tuyên bố thêm thao tác nào.
- File hoàn toàn lạ -> engine "unknown", supported rỗng, ok=True (probe
  THÀNH CÔNG với kết luận "chưa hỗ trợ") — không raise, không ném lỗi
  khó hiểu. Chỉ lỗi đọc file / lỗi hệ thống mới trả về qua bridge.
- Không gọi mạng, không ghi file. Chỉ thư viện chuẩn, Python 3.8+.

Schema báo cáo (probe_version 1) — xem probe_data().
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Tuple

from core.errors import PB3Error
from core.model import confidence as _confidence
from sdk import containers as _containers
from sdk import discovery as _disc

PROBE_VERSION = 1
PROBE_FORMAT = "pb3-format-probe"

# Giới hạn probe (RAM tab trình duyệt): chỉ xét tối đa 400 tên entry cho
# detect, đọc mẫu đầu tối đa 64 entry; quét chuỗi ASCII trong 256KB đầu.
MAX_PROBE_NAMES = 400
MAX_PROBE_HEADS = 64
HEAD_BYTES = 64
STRINGS_SCAN_BYTES = 256 * 1024
MAX_STRINGS_SAMPLE = 10
MAX_ENTRIES_SAMPLE = 20

# Đuôi entry có thể mang magic đầu file (chỉ những đuôi này mới đọc mẫu
# đầu khi probe container — tránh đọc cả trăm MB texture/audio).
_MAGIC_EXTS = (".pck", ".locres", ".win", ".unity3d", ".assets",
               ".translation", ".bundle", ".resS")
_ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")

# Plugin engine theo manifest (id -> engine). Dùng để báo plugins_checked
# và để hạ cấp thao tác khi plugin chưa bật trong phiên.
ENGINE_PLUGINS = {
    "godot": ("godot_pck", "godot_translation"),
    "unreal": ("unreal_pak", "unreal_locres"),
    "cocos": ("cocos_detect",),
    "defold": ("defold_detect",),
    "gamemaker": ("gamemaker_detect",),
}
ALL_ENGINE_PLUGIN_IDS = ("godot_pck", "godot_translation", "unreal_pak",
                         "unreal_locres", "cocos_detect", "defold_detect",
                         "gamemaker_detect")

# Mức hỗ trợ THẬT từng engine — diễn đạt lại docs/maturity của manifest
# (không tuyên bố thêm). Mỗi mục supported có "plugin": id plugin thực
# hiện nó (None = thao tác chung của PB3, không qua plugin engine).
# "note": bằng chứng kiểm chứng tới đâu (fixture hay game thật).
ENGINE_SUPPORT = {
    "unity": {
        "label": "Unity",
        "note": ("Đã kiểm chứng trên IPA game thật 9 Kings "
                 "(inspect/trích xuất)."),
        "supported": [
            {"op": "Liệt kê entry trong IPA/ZIP", "plugin": None},
            {"op": ("Inspect bundle .unity3d / file .assets (UnityFS tới v8, "
                    "SerializedFile v15–v23)"), "plugin": None},
            {"op": ("Trích xuất text (TextAsset + MonoBehaviour trong "
                    "resources.assets)"), "plugin": None},
            {"op": "Dịch sang tiếng Việt (Google/MyMemory/offline)",
             "plugin": None},
            {"op": ("Mod thử (dry-run) + xuất patch pack để áp sang bản iSH"),
             "plugin": None},
        ],
        "unsupported": [
            {"op": "Bundle Unity mã hoá",
             "reason": "báo lỗi E_ENCRYPTED, không giải mã"},
        ],
        "next_steps": [
            "Dùng tab Inspect để xem bundle, tab Trích xuất để lấy text.",
        ],
    },
    "godot": {
        "label": "Godot",
        "note": ("Chỉ kiểm chứng trên fixture tự dựng — chưa có file game "
                 "Godot thật."),
        "supported": [
            {"op": ("Mở/lấy entry file .pck v1/v2 (Godot 3/4), chỉ đọc "
                    "entry không nén, không mã hoá"),
             "plugin": "godot_pck"},
            {"op": ("Đọc file .translation định dạng “TR” (chỉ đọc)"),
             "plugin": "godot_translation"},
        ],
        "unsupported": [
            {"op": "Trích xuất text game Godot (chưa có pipeline)",
             "reason": "chưa có backend trích xuất Godot"},
            {"op": "PCK v0/v3+, PCK mã hoá, PCK nhúng trong file thực thi",
             "reason": "plugin godot_pck chưa hỗ trợ"},
            {"op": "File .translation dạng RSRC (Godot thật xuất ra)",
             "reason": "plugin chỉ đọc layout “TR” theo nhiệm vụ, chưa đối chiếu file thật"},
            {"op": "Mod/ghi lại PCK", "reason": "plugin chỉ đọc"},
        ],
        "next_steps": [
            "Mở tab Danh sách để xem entry bên trong file .pck (chỉ đọc).",
            ("File .translation dạng “TR” đọc được qua plugin định dạng; "
             "file RSRC do Godot thật xuất ra thì chưa đọc được — gửi file "
             "mẫu để thêm hỗ trợ."),
        ],
    },
    "unreal": {
        "label": "Unreal",
        "note": ("Chỉ kiểm chứng trên fixture tự dựng — chưa có file game "
                 "Unreal thật."),
        "supported": [
            {"op": ("Mở/lấy entry file .pak UE4/UE5 (chỉ entry không nén, "
                    "không mã hoá), chỉ đọc"),
             "plugin": "unreal_pak"},
            {"op": "Đọc file .locres v0–v3 (chỉ đọc)",
             "plugin": "unreal_locres"},
        ],
        "unsupported": [
            {"op": "Trích xuất text game Unreal (chưa có pipeline)",
             "reason": "chưa có backend trích xuất Unreal"},
            {"op": "PAK entry nén/mã hoá/Oodle",
             "reason": "plugin unreal_pak chỉ đọc entry không nén, không mã hoá"},
            {"op": ".locmeta nhị phân", "reason": "chưa có parser"},
            {"op": "Mod/ghi lại PAK", "reason": "plugin chỉ đọc"},
        ],
        "next_steps": [
            "Mở tab Danh sách để xem entry bên trong file .pak (chỉ đọc).",
            ("File .locres đọc được qua plugin định dạng; muốn trích xuất "
             "toàn game thì gửi file mẫu để thêm hỗ trợ."),
        ],
    },
    "cocos": {
        "label": "Cocos (Cocos2d-x / Cocos Creator)",
        "note": ("Chỉ nhận diện bằng tên entry — Cocos không có magic đầu "
                 "file đã xác minh nên tối đa CÓ KHẢ NĂNG; kiểm chứng trên "
                 "fixture."),
        "supported": [
            {"op": ("Nhận diện qua tên entry (project.json, main.js, "
                    ".ccb, .fire)"),
             "plugin": "cocos_detect"},
        ],
        "unsupported": [
            {"op": "Mở container, trích xuất text, dịch, mod",
             "reason": "chưa có backend Cocos (chỉ có plugin nhận diện)"},
        ],
        "next_steps": [
            ("Chưa trích xuất được game Cocos — gửi file mẫu (kèm tên game) "
             "để thêm hỗ trợ."),
            "Dùng bản iSH để thử các thao tác dò khác (menu Chẩn đoán).",
        ],
    },
    "defold": {
        "label": "Defold",
        "note": ("Chỉ nhận diện bằng tên entry — Defold không có magic đầu "
                 "file đã xác minh nên tối đa CÓ KHẢ NĂNG; kiểm chứng trên "
                 "fixture."),
        "supported": [
            {"op": ("Nhận diện qua tên entry (game.project, .archive, "
                    ".collection, .atlas)"),
             "plugin": "defold_detect"},
        ],
        "unsupported": [
            {"op": "Mở container, trích xuất text, dịch, mod",
             "reason": "chưa có backend Defold (chỉ có plugin nhận diện)"},
        ],
        "next_steps": [
            ("Chưa trích xuất được game Defold — gửi file mẫu (kèm tên game) "
             "để thêm hỗ trợ."),
            "Dùng bản iSH để thử các thao tác dò khác (menu Chẩn đoán).",
        ],
    },
    "gamemaker": {
        "label": "GameMaker",
        "note": "Nhận diện bằng magic FORM+GEN8 trong data.win; kiểm chứng trên fixture.",
        "supported": [
            {"op": ("Nhận diện bằng magic IFF “FORM” + chunk đầu “GEN8” "
                    "trong data.win"),
             "plugin": "gamemaker_detect"},
            {"op": "Nhận diện qua tên entry (.yyp/.yy/.gml/.yyz)",
             "plugin": "gamemaker_detect"},
        ],
        "unsupported": [
            {"op": "Mở data.win, trích xuất text, dịch, mod",
             "reason": "chưa có backend GameMaker (chỉ có plugin nhận diện)"},
        ],
        "next_steps": [
            ("Chưa trích xuất được game GameMaker — gửi file mẫu (kèm tên "
             "game) để thêm hỗ trợ."),
            "Dùng bản iSH để thử các thao tác dò khác (menu Chẩn đoán).",
        ],
    },
    "unknown": {
        "label": "Chưa rõ (unknown)",
        "note": "Không khớp dấu hiệu engine nào đã biết.",
        "supported": [],
        "unsupported": [
            {"op": "Trích xuất text, dịch, mod",
             "reason": "chưa nhận diện được engine nên chưa có backend nào đọc được file"},
        ],
        "next_steps": [
            ("File này chưa được hỗ trợ — đừng dùng nó để trích xuất/dịch, "
             "kết quả sẽ không đúng."),
            ("Gửi file mẫu (kèm tên game + engine nếu biết) để thêm hỗ trợ."),
            "Dùng bản iSH để thử các thao tác dò khác (menu Chẩn đoán).",
        ],
    },
}

_ENGINE_PRIORITY = ("unity", "godot", "unreal", "cocos", "defold",
                    "gamemaker")


def _head_magic_name(head: bytes) -> Optional[str]:
    """Tên magic khớp ở đầu file (None = không khớp magic nào đã biết)."""
    if head[:7] == b"UnityFS":
        return "UnityFS"
    if head[:4] == _containers.GODOT_PCK_MAGIC:
        return "GDPC (Godot PCK)"
    if head[:16] == _disc.LOCRES_MAGIC:
        return "LOCRES (Unreal)"
    if (head[:4] == _disc.GAMEMAKER_FORM_MAGIC
            and head[8:12] == _disc.GAMEMAKER_GEN8_CHUNK):
        return "FORM+GEN8 (GameMaker data.win)"
    if head[:2] == b"TR":
        return "TR (Godot .translation — dạng theo nhiệm vụ, chưa đối chiếu file thật)"
    return None


def _scan_strings(data: bytes) -> Dict[str, object]:
    """Quét chuỗi ASCII in được trong 256KB đầu (tín hiệu phụ: file có
    chứa text đọc được không). Không dùng để kết luận engine."""
    scanned = data[:STRINGS_SCAN_BYTES]
    found = _ASCII_RUN.findall(scanned)
    sample = []
    for raw in found[:MAX_STRINGS_SAMPLE]:
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            continue
        sample.append(text[:80])
        if len(sample) >= MAX_STRINGS_SAMPLE:
            break
    return {"scanned_bytes": len(scanned), "count": len(found),
            "sample": sample}


def _candidate(engine: str, names: List[str], heads: Dict[str, bytes],
               *, pak_footer: bool, tr_head: bool
               ) -> Dict[str, object]:
    """Ứng viên 1 engine: evidence + confidence + mức, theo discovery +
    2 bằng chứng thêm của probe (ghi rõ trong reason)."""
    from core.model import Evidence
    evidence, conf, has_magic = _disc.engine_signals(engine, names, heads)
    if engine == "unreal" and pak_footer:
        evidence.append(Evidence(
            "magic", "PAK-FOOTER", 0.9,
            "magic footer PAK (0x5A6F12E1) trong 4KB cuối file "
            "(bằng chứng thêm của probe — discovery chỉ xét mẫu đầu file)"))
        has_magic = True
    if engine == "godot" and tr_head:
        evidence.append(Evidence(
            "magic", "TR", 0.3,
            "magic “TR” ở đầu file .translation (bằng chứng thêm của probe — "
            "định dạng theo nhiệm vụ, chưa đối chiếu file Godot thật)"))
    conf = _confidence(evidence)
    level = _disc.engine_level(conf, has_magic)
    return {"engine": engine,
            "label": ENGINE_SUPPORT[engine]["label"],
            "level": level,
            "confidence": round(conf, 2),
            "has_magic": bool(has_magic),
            "evidence": [{"kind": ev.kind, "value": str(ev.value),
                          "weight": float(ev.weight), "reason": ev.reason}
                         for ev in evidence]}


def _rank(cand: Dict[str, object]) -> Tuple[int, float, int, int]:
    rank = {"CHẮC CHẮN": 2, "CÓ KHẢ NĂNG": 1}.get(cand["level"], 0)
    return (rank, float(cand["confidence"]),
            1 if cand["has_magic"] else 0,
            -_ENGINE_PRIORITY.index(cand["engine"]))


def _split_support(engine: str, active_plugins) -> Tuple[list, list]:
    """Chia supported/unsupported theo plugin đang bật: thao tác nào cần
    plugin chưa bật thì rơi xuống unsupported kèm lý do thật."""
    spec = ENGINE_SUPPORT[engine]
    supported, unsupported = [], list(spec["unsupported"])
    for item in spec["supported"]:
        pid = item.get("plugin")
        if pid is not None and pid not in active_plugins:
            unsupported.append({
                "op": item["op"],
                "reason": "plugin %s chưa bật trong phiên này" % pid})
        else:
            supported.append({"op": item["op"], "plugin": pid})
    return supported, unsupported


def probe_data(data: bytes, name: str, *,
               active_plugins=frozenset()) -> Dict[str, object]:
    """Probe bytes của 1 file, trả báo cáo (dict, chưa bọc {"ok": ...}).

    Không bao giờ raise vì "file lạ": mọi nhánh không nhận diện được đều
    trả engine "unknown" kèm những gì đã đo được. Chỉ raise lỗi hệ thống
    thật (vd data không phải bytes).
    """
    if not isinstance(data, (bytes, bytearray)):
        raise PB3Error("E_PLUGIN_INVALID", "probe_data cần bytes",
                       where=name or "?")
    data = bytes(data)
    size = len(data)
    head = data[:HEAD_BYTES]
    kind = _containers.detect_container_kind(data)
    try:
        pak_footer = bool(_containers._looks_like_pak(data))
    except Exception:  # noqa: BLE001 - heuristic đuôi file lỗi thì coi như không phải PAK
        pak_footer = False
    magic_head = _head_magic_name(head)
    tr_head = (name.lower().endswith(".translation")
               and head[:2] == b"TR")

    names: List[str] = [name]
    heads: Dict[str, bytes] = {name: head}
    entries_sample: List[Dict[str, object]] = []
    entries_total = 0
    entries_truncated = False
    open_error: Optional[Dict[str, str]] = None
    notes: List[str] = []
    container_opened = False

    # Container thì mở + liệt kê entry để detect theo tên/magic BÊN TRONG
    # (vd ZIP game Cocos/Defold, PCK Godot, PAK Unreal). 7z mở không được
    # thì ghi open_error rồi vẫn probe theo tên file ngoài.
    if kind != _containers.KIND_UNKNOWN or pak_footer or kind == _containers.KIND_GODOT_PCK:
        try:
            from core.limits import Limits
            vfs = _containers.open_vfs_bytes(data, source_name=name)
            try:
                inv = _disc.inventory(vfs, Limits())
            finally:
                pass  # inventory trả list thường; VFS đóng sau khi đọc head xong
            entries_total = len(inv)
            inner_names = [e.path for e in inv][:MAX_PROBE_NAMES]
            if len(inv) > MAX_PROBE_NAMES:
                entries_truncated = True
                notes.append("chỉ xét %d/%d tên entry đầu để nhận diện"
                             % (MAX_PROBE_NAMES, len(inv)))
            entries_sample = [{"path": e.path, "size": int(e.size),
                               "kind": e.kind}
                              for e in inv[:MAX_ENTRIES_SAMPLE]]
            if kind == _containers.KIND_GODOT_PCK:
                # File PCK: tên + magic của chính nó cũng là bằng chứng.
                names = [name] + inner_names
            else:
                names = inner_names
            heads = {}
            if kind == _containers.KIND_GODOT_PCK:
                heads[name] = head
            read_heads = 0
            for entry in inv:
                if read_heads >= MAX_PROBE_HEADS:
                    break
                low = entry.path.lower()
                if low.endswith(_MAGIC_EXTS) or low.endswith("/data.win") \
                        or low == "data.win":
                    try:
                        heads[entry.path] = bytes(
                            vfs.read(entry.path, max_bytes=HEAD_BYTES))
                        read_heads += 1
                    except PB3Error:
                        continue
            container_opened = True
            try:
                vfs.close()
            except Exception:  # noqa: BLE001 - đóng VFS lỗi thì bỏ qua
                pass
        except PB3Error as exc:
            open_error = {"code": exc.code, "message": str(exc.detail or exc)}
            notes.append("không mở được container (%s) nên chỉ probe theo "
                         "tên + byte đầu của file ngoài" % exc.code)
            names = [name]
            heads = {name: head}
        except Exception as exc:  # noqa: BLE001 - lỗi lạ khi mở container: ghi lại, không crash probe
            open_error = {"code": "E_PROBE_OPEN",
                          "message": "%s: %s" % (type(exc).__name__, exc)}
            notes.append("không mở được container (lỗi lạ) nên chỉ probe "
                         "theo tên + byte đầu của file ngoài")
            names = [name]
            heads = {name: head}

    candidates = [_candidate(e, names, heads, pak_footer=pak_footer,
                             tr_head=tr_head)
                  for e in _ENGINE_PRIORITY]
    candidates.sort(key=_rank, reverse=True)
    best = candidates[0]
    if best["level"] == "CHƯA ĐỦ BẰNG CHỨNG":
        engine_id = "unknown"
    else:
        engine_id = best["engine"]
    spec = ENGINE_SUPPORT[engine_id]
    supported, unsupported = _split_support(engine_id, active_plugins)
    plugins_checked = {pid: (pid in active_plugins)
                       for pid in ALL_ENGINE_PLUGIN_IDS}
    if engine_id == "unknown":
        notes.append("không khớp dấu hiệu engine nào đã biết "
                     "(magic UnityFS/GDPC/LOCRES/FORM+GEN8, tên entry "
                     ".pck/.pak/.locres/project.godot/game.project/.yyp…)")
    elif best["level"] == "CÓ KHẢ NĂNG":
        notes.append("chỉ CÓ KHẢ NĂNG (không có magic) — cần thêm bằng "
                     "chứng (vd file .pck/.pak/.locres/data.win) để chắc chắn")
    if kind == _containers.KIND_UNKNOWN and not pak_footer \
            and engine_id == "unknown":
        notes.append("không phải container đã biết (ZIP/TAR/GZ/XZ/BZ2/PCK/PAK)")

    return {
        "format": PROBE_FORMAT,
        "probe_version": PROBE_VERSION,
        "file": {
            "name": name,
            "size": size,
            "size_human": _fmt_bytes(size),
            "sha256": hashlib.sha256(data).hexdigest(),
            "head_hex": head.hex(),
            "container_kind": kind,
            "magic_head": magic_head,
            "pak_footer": bool(pak_footer),
            "container_opened": container_opened,
        },
        "engine": {
            "id": engine_id,
            "label": spec["label"],
            "level": (best["level"] if engine_id != "unknown"
                      else "CHƯA ĐỦ BẰNG CHỨNG"),
            "confidence": (best["confidence"] if engine_id != "unknown"
                           else best["confidence"]),
            "evidence": (best["evidence"] if engine_id != "unknown" else []),
            "note": spec["note"],
        },
        "candidates": [
            {"engine": c["engine"], "label": c["label"],
             "level": c["level"], "confidence": c["confidence"],
             "evidence": [ev["reason"] for ev in c["evidence"]]}
            for c in candidates if c["evidence"]
        ],
        "entries": {"total": entries_total, "sample": entries_sample,
                    "truncated": entries_truncated},
        "open_error": open_error,
        "supported": supported,
        "unsupported": unsupported,
        "next_steps": list(spec["next_steps"]),
        "strings": _scan_strings(data),
        "plugins_checked": plugins_checked,
        "notes": notes,
    }


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / (1024 * 1024))
    return "%.2f GB" % (n / (1024 * 1024 * 1024))
