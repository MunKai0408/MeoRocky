# Plugin nhận diện engine Defold (Claude 08, nhóm engine-detection).
# CHỈ NHẬN DIỆN: trả Evidence từ tên entry trong VFS + gợi ý (hints).
# Không mở container, không parse localization, không ghi.
# Dấu hiệu (sdk.discovery.engine_signals, một nguồn sự thật duy nhất):
# tên mạnh game.project / .archive / .arci / .arcd / .dmanifest /
# .collection / .atlas, tên yếu .script / .gui. Defold KHÔNG có magic đầu
# file đã xác minh nên tối đa CÓ KHẢ NĂNG, không bao giờ CHẮC CHẮN.
# Kiểm tra: python3 menu.py plugin test defold_detect
from core.errors import PB3Error
from sdk import discovery as _disc
from sdk.api import Plugin, PluginContext
from sdk.vfs import MemoryVFS

MANIFEST = {
    "id": "defold_detect",
    "name": "Nhận diện Defold",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "engine",
    "capabilities": ["hint"],
    "maturity": "M2",  # chỉ nhận diện bằng tên, đã test trên fixture; chưa có bằng chứng game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["defold"],
    "match": {"extensions": [".archive", ".arci", ".arcd", ".dmanifest",
                             ".collection", ".atlas", ".script"],
              "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:DefoldDetectPlugin",
    "selftest": True,
    "docs": "CHỈ NHẬN DIỆN Defold qua tên entry (game.project, .archive/.arci/.arcd, "
            ".dmanifest, .collection, .atlas, .script, .gui). Không có magic đầu "
            "file đã xác minh nên tối đa CÓ KHẢ NĂNG, không CHẮC CHẮN. Không mở "
            "container, không parse localization, không ghi.",
}

_ENGINE = "defold"
# Defold không có magic -> không cần đọc mẫu đầu file nào.
_MAGIC_EXTS = ()
_SELFTEST_FILES = {
    "game.project": b"[project]\ntitle = Test Game\n",
    "main.collection": b"",
    "main.script": b"-- Defold script\n",
    "assets/atlas.atlas": b"",
}


class DefoldDetectPlugin(Plugin):
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
            "container": ".archive/.arci (chưa có backend mở)",
            "suggested_plugins": ["generic_strings"],
            "note": ("CHỈ NHẬN DIỆN Defold qua tên entry (không có magic đã xác minh; "
                     "tối đa CÓ KHẢ NĂNG). Chưa có backend mở container hay plugin "
                     "parse localization riêng cho Defold; chuỗi trong file Defold "
                     "chỉ dò thô được bằng generic_strings."),
        }

    def selftest(self, ctx):
        vfs = MemoryVFS(dict(_SELFTEST_FILES), limits=ctx.limits)
        sub = PluginContext(vfs, ctx.limits, None, None, ctx.tempdir, True,
                            None, None, self.id).for_plugin(self.manifest)
        try:
            hints = self.hints(sub)
        except PB3Error as exc:
            return {"ok": False, "checked": "selftest trong RAM: game.project + .collection + .script + .atlas",
                    "reason": "[%s] %s" % (exc.code, exc.detail)}
        ok = hints["engine"] == _ENGINE and hints["level"] == "CÓ KHẢ NĂNG"
        return {"ok": ok,
                "checked": "selftest trong RAM: game.project + .collection + .script + .atlas -> %s (%s)"
                           % (hints["engine"], hints["level"]),
                "reason": "" if ok else "nhận diện sai: %r" % (hints,)}
