# Plugin nhận diện engine GameMaker (Claude 08, nhóm engine-detection).
# CHỈ NHẬN DIỆN: trả Evidence từ tên entry/magic trong VFS + gợi ý (hints).
# Không mở container, không parse localization, không ghi.
# Dấu hiệu (sdk.discovery.engine_signals, một nguồn sự thật duy nhất):
# magic IFF "FORM" ở đầu data.win + chunk đầu "GEN8" ở offset 8 (0.9 ->
# CHẮC CHẮN); tên mạnh .yyp / .yy / .gml / .yyz, tên yếu .win /
# datafiles/ / options/ (chỉ tên thì tối đa CÓ KHẢ NĂNG).
# Kiểm tra: python3 menu.py plugin test gamemaker_detect
from core.errors import PB3Error
from sdk import discovery as _disc
from sdk.api import Plugin, PluginContext
from sdk.vfs import MemoryVFS

MANIFEST = {
    "id": "gamemaker_detect",
    "name": "Nhận diện GameMaker",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "engine",
    "capabilities": ["hint"],
    "maturity": "M2",  # nhận diện bằng magic + tên, đã test trên fixture; chưa có bằng chứng game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["gamemaker"],
    "match": {"extensions": [".yyp", ".yy", ".gml", ".yyz", ".win"],
              "magic": [{"offset": 0, "hex": "464F524D", "desc": "IFF FORM ở đầu data.win"},
                        {"offset": 8, "hex": "47454E38", "desc": "chunk đầu GEN8"}],
              "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:GameMakerDetectPlugin",
    "selftest": True,
    "docs": "CHỈ NHẬN DIỆN GameMaker qua magic data.win (IFF FORM + chunk đầu GEN8) "
            "và tên entry (.yyp, .yy, .gml, .yyz, .win, datafiles/, options/). "
            "Magic -> CHẮC CHẮN; chỉ tên file thì tối đa CÓ KHẢ NĂNG. Không mở "
            "container, không parse localization, không ghi.",
}

_ENGINE = "gamemaker"
# Chỉ file .win mới có thể mang magic FORM/GEN8.
_MAGIC_EXTS = (".win",)
_SELFTEST_FILES = {
    "game.yyp": b'{"resources": []}',
    "objects/obj_player/obj_player.yy": b"{}",
    "scripts/scr_text/scr_text.gml": b"// GameMaker script\n",
    "data.win": b"FORM" + (100).to_bytes(4, "big") + b"GEN8" + b"\x00" * 20,
}


class GameMakerDetectPlugin(Plugin):
    MANIFEST = MANIFEST

    def _names_and_heads(self, ctx, max_entries=200, head_bytes=64):
        """Tên entry + mẫu đầu (chỉ file có thể mang magic) trong VFS."""
        names, heads = [], {}
        try:
            entries = list(ctx.vfs.iter_entries())
        except PB3Error:
            return names, heads
        for path in entries[:max_entries]:
            names.append(path)
            if path.lower().endswith(_MAGIC_EXTS):
                try:
                    heads[path] = bytes(ctx.vfs.read(path, max_bytes=head_bytes))
                except PB3Error:
                    continue
        return names, heads

    def detect(self, ctx):
        names, heads = self._names_and_heads(ctx)
        evidence, _conf, _magic = _disc.engine_signals(_ENGINE, names, heads)
        return evidence

    def hints(self, ctx):
        names, heads = self._names_and_heads(ctx)
        evidence, conf, has_magic = _disc.engine_signals(_ENGINE, names, heads)
        level = _disc.engine_level(conf, has_magic)
        return {
            "engine": _ENGINE if level != "CHƯA ĐỦ BẰNG CHỨNG" else "unknown",
            "confidence": round(conf, 2),
            "level": level,
            "evidence": [ev.reason for ev in evidence],
            "container": "data.win (IFF/GEN8, chưa có backend mở)",
            "suggested_plugins": ["json_text", "generic_strings"],
            "note": ("CHỈ NHẬN DIỆN GameMaker qua magic data.win (FORM + GEN8) và tên "
                     "entry. Chưa có backend mở data.win hay plugin parse "
                     "localization riêng cho GameMaker; file .yy/.yyp (JSON) có thể "
                     "thử bằng plugin định dạng chung, chuỗi trong .gml dò thô bằng "
                     "generic_strings."),
        }

    def selftest(self, ctx):
        vfs = MemoryVFS(dict(_SELFTEST_FILES), limits=ctx.limits)
        sub = PluginContext(vfs, ctx.limits, None, None, ctx.tempdir, True,
                            None, None, self.id).for_plugin(self.manifest)
        try:
            hints = self.hints(sub)
        except PB3Error as exc:
            return {"ok": False, "checked": "selftest trong RAM: .yyp + .yy + .gml + data.win (FORM/GEN8)",
                    "reason": "[%s] %s" % (exc.code, exc.detail)}
        ok = hints["engine"] == _ENGINE and hints["level"] == "CHẮC CHẮN"
        return {"ok": ok,
                "checked": "selftest trong RAM: .yyp + .yy + .gml + data.win (FORM/GEN8) -> %s (%s)"
                           % (hints["engine"], hints["level"]),
                "reason": "" if ok else "nhận diện sai: %r" % (hints,)}
