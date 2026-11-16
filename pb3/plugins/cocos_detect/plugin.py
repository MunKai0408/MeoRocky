# Plugin nhận diện engine Cocos (Claude 08, nhóm engine-detection).
# CHỈ NHẬN DIỆN: trả Evidence từ tên entry trong VFS + gợi ý (hints).
# Không mở container, không parse localization, không ghi.
# Dấu hiệu (sdk.discovery.engine_signals, một nguồn sự thật duy nhất):
# tên mạnh project.json / cocos2d / .ccb / .fire, tên yếu main.js / res/ /
# settings/. Cocos KHÔNG có magic đầu file đã xác minh nên tối đa
# CÓ KHẢ NĂNG, không bao giờ CHẮC CHẮN.
# Kiểm tra: python3 menu.py plugin test cocos_detect
from core.errors import PB3Error
from sdk import discovery as _disc
from sdk.api import Plugin, PluginContext
from sdk.vfs import MemoryVFS

MANIFEST = {
    "id": "cocos_detect",
    "name": "Nhận diện Cocos (Cocos2d-x / Cocos Creator)",
    "version": "0.1.0",
    "api_version": 3,
    "kind": "engine",
    "capabilities": ["hint"],
    "maturity": "M2",  # chỉ nhận diện bằng tên, đã test trên fixture; chưa có bằng chứng game thật
    "priority": 10,
    "true_cost": "cheap",
    "engines": ["cocos"],
    "match": {"extensions": [".ccb", ".fire"], "min_score_to_probe": 0.0},
    "limits": {"max_entry_bytes": 1024 * 1024, "hook_timeout_s": 10},
    "entry": "plugin:CocosDetectPlugin",
    "selftest": True,
    "docs": "CHỈ NHẬN DIỆN Cocos2d-x / Cocos Creator qua tên entry (project.json, "
            "main.js, cocos2d, .ccb, .fire, res/, settings/). Không có magic đầu "
            "file đã xác minh nên tối đa CÓ KHẢ NĂNG, không CHẮC CHẮN. Không mở "
            "container, không parse localization, không ghi.",
}

_ENGINE = "cocos"
# Cocos không có magic -> không cần đọc mẫu đầu file nào.
_MAGIC_EXTS = ()
_SELFTEST_FILES = {
    "project.json": b'{"version": "3.8.0", "assets": []}',
    "main.js": b"// Cocos Creator\ncc.game.run();\n",
    "res/icon.png": b"\x89PNG\r\n\x1a\n",
    "assets/scene.fire": b"[]",
}


class CocosDetectPlugin(Plugin):
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
            "container": None,
            "suggested_plugins": ["json_text", "apple_plist", "generic_strings"],
            "note": ("CHỈ NHẬN DIỆN Cocos qua tên entry (không có magic đã xác minh; "
                     "tối đa CÓ KHẢ NĂNG). Chưa có backend mở container hay plugin "
                     "parse localization riêng cho Cocos; file .json/.ccb (plist) "
                     "có thể thử bằng plugin định dạng chung."),
        }

    def selftest(self, ctx):
        vfs = MemoryVFS(dict(_SELFTEST_FILES), limits=ctx.limits)
        sub = PluginContext(vfs, ctx.limits, None, None, ctx.tempdir, True,
                            None, None, self.id).for_plugin(self.manifest)
        try:
            hints = self.hints(sub)
        except PB3Error as exc:
            return {"ok": False, "checked": "selftest trong RAM: project.json + main.js + res/ + .fire",
                    "reason": "[%s] %s" % (exc.code, exc.detail)}
        ok = hints["engine"] == _ENGINE and hints["level"] == "CÓ KHẢ NĂNG"
        return {"ok": ok,
                "checked": "selftest trong RAM: project.json + main.js + res/ + .fire -> %s (%s)"
                           % (hints["engine"], hints["level"]),
                "reason": "" if ok else "nhận diện sai: %r" % (hints,)}
