import copy
import io
import unittest

from tests.helpers import ROOT  # noqa: F401
from core import model as M
from core.errors import ERROR_SPECS, PB3Error
from core.limits import Limits
from sdk import api, example_kv as K
from sdk.api import LocaleRequest, Plugin, PluginContext, effective_limits
from sdk.manifest import Manifest, api_compat, check_api, parse_manifest, parse_manifest_text
from sdk.types import Candidate, Diagnostic, Evidence, LocEntry, LocTable, SourceRef, SupportLevel
from sdk.vfs import LimitedVFS, MemoryVFS, VFS, check_vpath, vpath_join, vpath_split

APPX_A = {
    "id": "text_json", "name": "JSON localization", "version": "1.0.0", "api_version": 3, "kind": "format",
    "capabilities": ["read", "write"], "maturity": "M2", "priority": 50, "true_cost": "cheap",
    "engines": ["unity", "generic", "cocos", "godot"],
    "match": {"extensions": [".json", ".jsonc"], "magic": [{"offset": 0, "hex": "7B", "desc": "dấu { đầu file"}],
              "name_regex": "(?i)(local|lang|i18n|text|string)", "container_kind": None, "min_score_to_probe": 0.2},
    "locale_hints": {"filename_regex": "(?i)(en|english|zh|chinese|hans|hant)[-_.]?([a-z]{2})?",
                     "per_locale_file_expected": True},
    "write_modes": ["REBUILD_SECTION"], "limits": {"max_entry_bytes": 67108864, "hook_timeout_s": 20},
    "requires": [], "entry": "plugin:JsonPlugin", "selftest": True,
    "docs": "Hỗ trợ object key-value phẳng.", "deprecated_for": None,
}


def man(**kw):
    d = copy.deepcopy(APPX_A)
    d.update(kw)
    return d


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except PB3Error as e:
        return e.code
    return None


def src(p="a.kv"):
    return SourceRef([p], p)


class TestApiVersion(unittest.TestCase):
    def test_is_integer_3(self):
        self.assertEqual(api.api_version, 3)
        self.assertEqual(api.API_VERSION, 3)
        self.assertIsInstance(api.api_version, int)

    def test_compat(self):
        self.assertEqual([api_compat(x) for x in (3, 2, 4, "3", 3.0, True, None, "1.0")],
                         ["ok", "too_old", "too_new", "invalid", "invalid", "invalid", "invalid", "invalid"])
        self.assertEqual(check_api(3), 3)
        for bad in (2, 4, "3", "1.0", None):
            self.assertEqual(code_of(check_api, bad), "E_PLUGIN_INVALID")

    def test_manifest_does_not_import_api(self):
        # C10 (#181): vòng import sdk.api <-> sdk.manifest — API_VERSION
        # định nghĩa ở sdk/manifest.py, sdk/api.py chỉ import lại + re-export.
        import ast
        from pathlib import Path
        from sdk import manifest
        src = (Path(__file__).resolve().parent.parent / "sdk"
               / "manifest.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("sdk.api", imported)
        self.assertEqual(manifest.API_VERSION, api.API_VERSION)
        self.assertEqual(manifest.API_MIN_VERSION, api.API_MIN_VERSION)
        self.assertEqual(manifest.API_VERSION, 3)
        self.assertEqual(manifest.API_MIN_VERSION, 3)


class TestManifest(unittest.TestCase):
    def test_appendix_a_example_valid(self):
        m = parse_manifest(APPX_A)
        self.assertTrue(m.can_write and not m.read_only)
        self.assertEqual((m.max_entry_bytes, m.hook_timeout_s, m.maturity_rank), (67108864, 20.0, 2))
        self.assertEqual(m.match["magic"], [{"offset": 0, "hex": "7B", "desc": "dấu { đầu file"}])
        self.assertEqual(Manifest.from_dict(m.to_dict()), m)

    def test_required_fields_each(self):
        from sdk.manifest import REQUIRED
        for f in REQUIRED:
            d = man()
            del d[f]
            self.assertEqual(code_of(parse_manifest, d), "E_PLUGIN_INVALID", f)

    def test_optional_fields_default(self):
        d = man()
        for f in ("true_cost", "engines", "locale_hints", "write_modes", "requires", "deprecated_for"):
            d.pop(f)
        d["capabilities"], d["maturity"] = ["read"], "M2"
        m = parse_manifest(d)
        self.assertEqual((m.true_cost, m.engines, m.write_modes, m.deprecated_for), ("normal", [], [], None))

    def test_invalid_manifests_all_E_PLUGIN_INVALID(self):
        bads = [man(id="Bad-Id"), man(id="a"), man(api_version="3"), man(api_version=2), man(api_version=4),
                man(api_version=True), man(kind="plugin"), man(maturity="M5"), man(maturity="supported"),
                man(priority="50"), man(priority=-1), man(priority=True), man(true_cost="free"),
                man(entry="khong_co_hai_cham"), man(capabilities=[]), man(capabilities=["read", "read"]),
                man(capabilities=["hack"], write_modes=[]), man(capabilities="read"), man(name=5),
                man(version="1.0"), man(version=" "), man(docs=" "), man(selftest="true"),
                man(engines=[""]), man(requires=[1]), man(deprecated_for="Bad Id"),
                man(limits={"max_entry_bytes": 0, "hook_timeout_s": 5}),
                man(limits={"max_entry_bytes": 10}), man(limits={"max_entry_bytes": 10, "hook_timeout_s": 0}),
                man(limits={"max_entry_bytes": True, "hook_timeout_s": 5}),
                man(match={"extensions": ["json"]}), man(match={"extensions": [".JSON"]}),
                man(match={"magic": [{"offset": -1, "hex": "7B"}]}), man(match={"magic": [{"offset": 0, "hex": "7"}]}),
                man(match={"magic": [{"offset": 0, "hex": "ZZ"}]}), man(match={"name_regex": "("}),
                man(match={"name_regex": "a" * 400}), man(match={"min_score_to_probe": 1.5}),
                man(match={"unknown": 1}), man(match="x"), man(locale_hints={"x": 1}),
                man(locale_hints={"filename_regex": "["}), {"id": "x"}, "không dict", None, 5]
        for b in bads:
            self.assertEqual(code_of(parse_manifest, b), "E_PLUGIN_INVALID", repr(b)[:90])
        self.assertEqual(code_of(parse_manifest_text, "id = = 1"), "E_PLUGIN_INVALID")

    def test_limits_unknown_key_is_ignored_forward_compat(self):
        # Khóa limits LẠ (plugin mới hơn) không vô hiệu hóa plugin: bỏ qua +
        # ghi vào ignored_fields (polish F25).
        m = parse_manifest(man(limits={"max_entry_bytes": 10, "hook_timeout_s": 5, "x": 1}))
        self.assertEqual(m.limits, {"max_entry_bytes": 10, "hook_timeout_s": 5})
        self.assertIn("limits.x", m.ignored_fields)

    def test_requires_accepts_module_spec_rejects_bad_id(self):
        parse_manifest(man(requires=["lz4>=3", "no_such_module_xyz"]))
        self.assertEqual(code_of(parse_manifest, man(requires=["Bad Id"])), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(parse_manifest, man(requires=[""])), "E_PLUGIN_INVALID")

    def test_kind_capability_rules(self):
        # engine/profile: chỉ 'hint'; extractor: chỉ 'read', tối đa M2; M3/M4 cần 'write'
        ok_engine = man(kind="engine", capabilities=["hint"], write_modes=[], maturity="M1")
        parse_manifest(ok_engine)
        bads = [dict(ok_engine, capabilities=["read"]), dict(ok_engine, capabilities=["hint", "read"]),
                man(kind="extractor", capabilities=["read", "write"]),
                man(kind="extractor", capabilities=["read"], write_modes=[], maturity="M3"),
                man(kind="format", capabilities=["write"]),                  # write thiếu read
                man(kind="format", capabilities=["container"], write_modes=[]),
                man(kind="container", capabilities=["read"], write_modes=[]),
                man(capabilities=["read"], write_modes=["REBUILD_SECTION"]),   # write_modes không có write
                man(write_modes=[]),                                           # write mà không có write_modes
                man(write_modes=["IN_PLACE"]), man(write_modes=["REBUILD_SECTION", "REBUILD_SECTION"]),
                man(capabilities=["read"], write_modes=[], maturity="M3"),     # M3 mà chỉ đọc
                man(capabilities=["read", "hint"], write_modes=[])]
        for b in bads:
            self.assertEqual(code_of(parse_manifest, b), "E_PLUGIN_INVALID", repr(b)[:120])
        parse_manifest(man(kind="container", capabilities=["container"], write_modes=[], maturity="M2"))
        parse_manifest(man(kind="extractor", capabilities=["read"], write_modes=[], maturity="M2"))
        parse_manifest(man(maturity="M4", write_modes=["PATCH_INPLACE", "REBUILD_SECTION", "REBUILD_ALL"]))

    def test_unknown_fields_ignored_not_fatal(self):
        m = parse_manifest(man(future_field=1, another=[2]))
        self.assertEqual(m.ignored_fields, ["another", "future_field"])

    def test_toml_manifest(self):
        t = ('id = "myengine_text"\nname = "MyEngine"\nversion = "0.1.0"\napi_version = 3\nkind = "format"\n'
             'capabilities = ["read"]\nmaturity = "M2"\npriority = 40\nselftest = true\n'
             'entry = "plugin:MyEnginePlugin"\ndocs = "KEY=value"\n'
             '[match]\nextensions = [".txt"]\nmagic = ["0:7B:dấu {", "4:FFEE"]\nname_regex = "(?i)(lang|text)"\n'
             '[limits]\nmax_entry_bytes = 16777216\nhook_timeout_s = 10\n')
        m = parse_manifest_text(t)
        self.assertTrue(m.read_only)
        self.assertEqual(m.match["magic"], [{"offset": 0, "hex": "7B", "desc": "dấu {"},
                                            {"offset": 4, "hex": "FFEE"}])
        self.assertEqual(code_of(parse_manifest_text, t.replace("api_version = 3", 'api_version = "1.0"')),
                         "E_PLUGIN_INVALID")


class TestTypes(unittest.TestCase):
    def test_evidence_and_support_reexported(self):
        self.assertEqual(Evidence("magic", "UnityFS", 1.0, "r").weight, 1.0)
        self.assertEqual(SupportLevel.ALL, M.SupportLevel.ALL)

    def test_sourceref_spec_fields_roundtrip(self):
        s = SourceRef(["a.ipa::b", "c.assets"], "x", 0, 10, "utf-16-le", {"path_id": 1})
        self.assertEqual(SourceRef.from_dict(s.to_dict()), s)
        for bad in (dict(container_stack="x"), dict(container_stack=[""]), dict(entry_offset=-1),
                    dict(entry_size=True), dict(encoding=""), dict(extra=[])):
            with self.assertRaises(PB3Error, msg=str(bad)):
                SourceRef(**bad)

    def test_locentry_and_loctable(self):
        e = LocEntry("k", "v", "en", "native", "ok", src(), ["n"])
        self.assertEqual(LocEntry.from_dict(e.to_dict()), e)
        for kw in (dict(key_origin="x"), dict(status="x"), dict(source=None), dict(locale=3), dict(notes="x")):
            args = dict(key="k", value="v", source=src())
            args.update(kw)
            with self.assertRaises(PB3Error, msg=str(kw)):
                LocEntry(**args)
        t = LocTable([e, LocEntry("k", "w", "en", "native", "conflict", src())], "en", "p")
        self.assertEqual(t.count_by_status()["conflict"], 1)
        self.assertEqual(t.duplicate_keys(), ["k"])
        self.assertEqual(LocTable.from_dict(t.to_dict()), t)
        self.assertEqual(t.to_key_values(), {"k": "w"})                   # JSON đầu ra chỉ key-value

    def test_candidate_score_range(self):
        Candidate("a", "p", 0.55)
        for s in (-0.1, 1.1, True, "0.5"):
            with self.assertRaises(PB3Error, msg=repr(s)):
                Candidate("a", "p", s)
        with self.assertRaises(PB3Error):
            Candidate("", "p")

    def test_diagnostic_spec(self):
        d = Diagnostic("E_PARSE_PARTIAL", "warning", plugin_id="p", where=src("x"))
        self.assertEqual(d.message_vi, ERROR_SPECS["E_PARSE_PARTIAL"].message_vi)      # tự lấy câu chuẩn
        self.assertEqual(Diagnostic.from_dict(d.to_dict()), d)
        for bad in (dict(code="E_NOPE"), dict(code="E_OK", severity="fatal"), dict(code="E_OK", where="x")):
            self.assertEqual(code_of(Diagnostic, **bad), "E_PLUGIN_INVALID")
        e = Diagnostic.from_error(PB3Error("E_UNSAFE_PATH", "x", where="a/b", plugin_id="q"))
        self.assertEqual((e.code, e.plugin_id, e.where.rel_path), ("E_UNSAFE_PATH", "q", "a/b"))
        self.assertEqual(all(c in ERROR_SPECS for c in ("E_PLUGIN_TIMEOUT", "E_PLUGIN_FORBIDDEN")), True)


class TestVFS(unittest.TestCase):
    def setUp(self):
        self.v = MemoryVFS({"a.txt": b"0123456789", "g.ipa::x.assets": b"AB", "dir/b.kv": b"k=v"},
                           Limits(max_entry_bytes=8, max_memory_read_bytes=64))

    def test_paths(self):
        self.assertEqual(vpath_split("Payload/G.app/Data/x.unity3d::resources.assets"),
                         ["Payload/G.app/Data/x.unity3d", "resources.assets"])
        self.assertEqual(vpath_join("a.ipa", "b/c"), "a.ipa::b/c")
        for bad in ("", "../x", "/abs", "a//b", "a\\b", "a/./b", "C:/x", "a\x00b", "a::", "::b", "a::../b",
                    "a:::b", None, 5):
            self.assertEqual(code_of(check_vpath, bad), "E_UNSAFE_PATH", repr(bad))
            self.assertEqual(code_of(self.v.stat, bad), "E_UNSAFE_PATH", repr(bad))

    def test_iter_stat_exists(self):
        self.assertEqual(list(self.v.iter_entries()), ["a.txt", "dir/b.kv", "g.ipa::x.assets"])
        self.assertEqual(list(self.v.iter_entries("dir/")), ["dir/b.kv"])
        st = self.v.stat("g.ipa::x.assets")
        self.assertEqual((st.size, st.crc_verified), (2, False))
        self.assertTrue(self.v.exists("a.txt"))
        self.assertFalse(self.v.exists("nope"))
        self.assertEqual(code_of(self.v.stat, "nope"), "E_INPUT_NOT_FOUND")

    def test_read_convention(self):
        self.assertEqual(self.v.read("a.txt", max_bytes=4), b"0123")            # tiền tố (đọc đầu file)
        self.assertEqual(self.v.read("dir/b.kv"), b"k=v")
        self.assertEqual(self.v.read("a.txt", max_bytes=0), b"")
        # nhỏ hơn max_entry_bytes (8) -> tiền tố; None/lớn -> nguyên entry mà quá 8 -> E_TOO_LARGE (không cắt cụt)
        self.assertEqual(code_of(self.v.read, "a.txt"), "E_TOO_LARGE")
        self.assertEqual(code_of(self.v.read, "a.txt", 8), "E_TOO_LARGE")
        self.assertEqual(code_of(self.v.read, "a.txt", -1), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(self.v.read, "a.txt", True), "E_PLUGIN_INVALID")

    def test_memory_cap(self):
        v = MemoryVFS({"big": b"x" * 100}, Limits(max_entry_bytes=1000, max_memory_read_bytes=10))
        self.assertEqual(code_of(v.read, "big"), "E_TOO_LARGE")
        self.assertEqual(len(v.read("big", 5)), 5)
        self.assertEqual(code_of(v.slice, "big", 0, 50), "E_TOO_LARGE")

    def test_slice(self):
        self.assertEqual(self.v.slice("a.txt", 2, 3), b"234")
        self.assertEqual(self.v.slice("a.txt", 8, 100), b"89")
        self.assertEqual(self.v.slice("a.txt", 50, 3), b"")
        self.assertEqual(code_of(self.v.slice, "a.txt", -1, 3), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(self.v.slice, "a.txt", 0, True), "E_PLUGIN_INVALID")

    def test_stream_readonly_and_seekable(self):
        with self.v.open_stream("a.txt") as s:
            self.assertEqual(s.read(3), b"012")
            s.seek(8)
            self.assertEqual((s.tell(), s.read()), (8, b"89"))
            self.assertFalse(s.writable())
            self.assertEqual(code_of(s.write, b"x"), "E_PLUGIN_FORBIDDEN")

    def test_no_write_api_at_all(self):
        for name in ("write", "writable", "delete", "remove", "rename"):
            self.assertFalse(hasattr(VFS, name), name)
            self.assertFalse(hasattr(self.v, name), name)

    def test_abstract_base(self):
        for fn, args in ((VFS().iter_entries, ()), (VFS().stat, ("a",)), (VFS().read, ("a",)),
                         (VFS().slice, ("a", 0, 1)), (VFS().open_stream, ("a",))):
            self.assertEqual(code_of(lambda f=fn, a=args: list(f(*a))), "E_NOT_IMPLEMENTED")
        self.assertEqual(code_of(MemoryVFS, {"../x": b""}), "E_UNSAFE_PATH")
        self.assertEqual(code_of(MemoryVFS, {"x": "str"}), "E_PLUGIN_INVALID")

    def test_limited_vfs_plugin_cannot_loosen(self):
        host = MemoryVFS({"a": b"x" * 20, "b": b"y" * 5}, Limits(max_entry_bytes=100))
        lv = LimitedVFS(host, Limits(max_entry_bytes=10, max_memory_read_bytes=8))
        self.assertEqual(code_of(lv.read, "a"), "E_TOO_LARGE")        # 20 > 10 của plugin dù host cho 100
        self.assertEqual(lv.read("b"), b"y" * 5)
        self.assertEqual(lv.read("a", 3), b"xxx")
        self.assertEqual(code_of(lv.slice, "a", 0, 9), "E_TOO_LARGE")
        self.assertEqual(code_of(lv.read, "../a"), "E_UNSAFE_PATH")
        self.assertEqual(list(lv.iter_entries()), ["a", "b"])
        self.assertEqual(code_of(LimitedVFS, object(), Limits()), "E_PLUGIN_INVALID")


class TestContext(unittest.TestCase):
    def ctx(self, **kw):
        return PluginContext(MemoryVFS({"a.kv": b"x=1"}), **kw)

    def test_spec_members(self):
        c = self.ctx(locale="en", order=2, tempdir="/t", dry_run=True, settings={"a": 1})
        for name in ("vfs", "limits", "locale", "order", "log", "warn", "diag", "tempdir", "dry_run",
                     "kb_read", "kb_write", "settings"):
            self.assertTrue(hasattr(c, name), name)
        self.assertEqual((c.locale.code, c.order, c.tempdir, c.dry_run, c.settings), ("en", 2, "/t", True, {"a": 1}))
        self.assertIsNone(self.ctx().locale.code)
        self.assertIsNone(self.ctx().order)
        self.assertIsInstance(c.vfs, VFS)
        self.assertEqual(code_of(PluginContext, object()), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(self.ctx, order=True), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(LocaleRequest, ""), "E_PLUGIN_INVALID")

    def test_log_warn_diag_sanitized(self):
        seen = []
        c = self.ctx(plugin_id="p", sink=lambda lv, m: seen.append((lv, m)))
        c.log("a\x00b" + "x" * 1000)
        c.warn("w")
        d = c.diag("E_PARSE_PARTIAL", detail="d", severity="warning", where=src("k"))
        self.assertEqual((d.plugin_id, d.code, d.where.rel_path), ("p", "E_PARSE_PARTIAL", "k"))
        self.assertEqual(c.diagnostics, [d])
        self.assertTrue(all("\x00" not in m and len(m) < 400 for _, m in c.messages))
        self.assertEqual(len(seen), 3)
        self.assertEqual(code_of(c.diag, "E_BAD_CODE"), "E_PLUGIN_INVALID")

    def test_sink_failure_and_message_cap(self):
        c = self.ctx(sink=lambda lv, m: 1 / 0)
        for _ in range(api.MAX_MESSAGES + 50):
            c.log("x")
        self.assertEqual(len(c.messages), api.MAX_MESSAGES)

    def test_cancel(self):
        flag = {"v": False}
        c = self.ctx(plugin_id="p", cancel=lambda: flag["v"])
        c.check_cancel()
        flag["v"] = True
        self.assertEqual(code_of(c.check_cancel), "E_CANCELLED")

    def test_kb_controlled(self):
        class KB:
            def __init__(self):
                self.d = {}

            def get(self, pid, k):
                return self.d.get((pid, k))

            def put(self, pid, k, v):
                self.d[(pid, k)] = v

        kb = KB()
        c = self.ctx(plugin_id="p", kb=kb)
        self.assertTrue(c.kb_write("k", {"a": 1}))
        self.assertEqual(c.kb_read("k"), {"a": 1})
        self.assertEqual(kb.d, {("p", "k"): {"a": 1}})                  # tự gắn plugin_id
        self.assertEqual(c.kb_read("zz", "mặc định"), "mặc định")
        dry = self.ctx(plugin_id="p", kb=kb, dry_run=True)
        self.assertFalse(dry.kb_write("n", 1))                          # dry-run không ghi
        self.assertNotIn(("p", "n"), kb.d)
        nokb = self.ctx()
        self.assertFalse(nokb.kb_write("k", 1))
        self.assertIsNone(nokb.kb_read("k"))
        for bad in ("", None, "x" * 200, "a\x00"):
            self.assertEqual(code_of(c.kb_read, bad), "E_PLUGIN_INVALID")

    def test_for_plugin_lowers_limits(self):
        host = Limits(max_entry_bytes=10 ** 9, hook_timeout_s=20.0, hook_timeout_heavy_s=120.0)
        m = parse_manifest(man(limits={"max_entry_bytes": 1000, "hook_timeout_s": 5}))
        c = PluginContext(MemoryVFS({"a": b"x" * 2000}), host).for_plugin(m)
        self.assertEqual((c.limits.max_entry_bytes, c.limits.hook_timeout_s, c.plugin_id), (1000, 5.0, "text_json"))
        self.assertEqual(code_of(c.vfs.read, "a"), "E_TOO_LARGE")
        big = parse_manifest(man(limits={"max_entry_bytes": 10 ** 12, "hook_timeout_s": 3000}))
        e = effective_limits(host, big)
        self.assertEqual((e.max_entry_bytes, e.hook_timeout_s), (10 ** 9, 20.0))     # không nới
        heavy = parse_manifest(man(true_cost="heavy", limits={"max_entry_bytes": 5, "hook_timeout_s": 3000}))
        self.assertEqual(effective_limits(host, heavy).hook_timeout_s, 120.0)         # heavy dùng ngưỡng 120s


class MinimalRead(Plugin):
    MANIFEST = man(capabilities=["read"], write_modes=[], maturity="M2")

    def parse(self, ctx, cand):
        return LocTable([], None, self.id)


class TestPluginBase(unittest.TestCase):
    def test_instantiation_validates_manifest(self):
        class Bad(Plugin):
            MANIFEST = man(api_version=2)

        class NoManifest(Plugin):
            pass
        self.assertEqual(code_of(Bad), "E_PLUGIN_INVALID")
        self.assertEqual(code_of(NoManifest), "E_PLUGIN_INVALID")
        self.assertEqual(MinimalRead().id, "text_json")

    def test_declared_capability_needs_hook(self):
        for caps, kind, mode, mat in ((["read"], "format", [], "M2"), (["read", "write"], "format",
                                                                       ["REBUILD_SECTION"], "M3"),
                                      (["container"], "container", [], "M1"), (["hint"], "engine", [], "M1")):
            class P(Plugin):
                MANIFEST = man(capabilities=caps, kind=kind, write_modes=mode, maturity=mat)
            self.assertEqual(code_of(P), "E_PLUGIN_INVALID", str(caps))

        class WriteNoBuild(MinimalRead):
            MANIFEST = man()                     # read+write nhưng chỉ cài parse
        self.assertEqual(code_of(WriteNoBuild), "E_PLUGIN_INVALID")

        class Eng(Plugin):
            MANIFEST = man(kind="engine", capabilities=["hint"], write_modes=[], maturity="M1")

            def hints(self, ctx):
                return {"paths": ["StreamingAssets"]}
        self.assertEqual(Eng().hints(None), {"paths": ["StreamingAssets"]})

    def test_read_only_plugin_cannot_pretend_to_write(self):
        p, ctx = MinimalRead(), PluginContext(MemoryVFS({}))
        self.assertEqual(code_of(p.build, ctx, LocTable(), b""), "E_WRITE_DISABLED")
        self.assertEqual(code_of(p.verify, ctx, b"", b""), "E_WRITE_DISABLED")

    def test_defaults_are_honest(self):
        p, ctx = MinimalRead(), PluginContext(MemoryVFS({}))
        self.assertEqual(p.detect(ctx), [])
        self.assertEqual(p.list_candidates(ctx), [])
        self.assertEqual(p.probe(ctx, Candidate("a", "p")), {})
        st = p.selftest(ctx)
        self.assertIsNone(st["ok"])                  # không giả PASS khi chưa cài selftest
        self.assertTrue(st["skipped"])

        class W(K.KvPlugin):
            verify = Plugin.verify
        v = W().verify(ctx, b"", b"")
        self.assertIsNone(v["ok"])
        self.assertFalse(v["checked"])

    def test_no_method_takes_or_exposes_write_target(self):
        import inspect
        for name in ("detect", "list_candidates", "probe", "parse", "build", "verify", "selftest"):
            self.assertTrue(callable(getattr(Plugin, name)), name)
        self.assertEqual(list(inspect.signature(Plugin.build).parameters), ["self", "ctx", "table", "base_bytes"])
        self.assertEqual(list(inspect.signature(Plugin.verify).parameters),
                         ["self", "ctx", "base_bytes", "new_bytes"])
        self.assertEqual(list(inspect.signature(Plugin.parse).parameters), ["self", "ctx", "cand"])


class TestExamplePlugin(unittest.TestCase):
    SRC = b"# chao\nx=1\ny=a\\nb\nz=3"

    def setUp(self):
        self.p = K.KvPlugin()
        self.vfs = MemoryVFS({"a.kv": self.SRC, "b.txt": b"k=v", "c.kv": b"\x00\x01"})
        self.ctx = PluginContext(self.vfs, locale="en").for_plugin(self.p.manifest)

    def test_candidates_probe_parse(self):
        c = self.p.list_candidates(self.ctx)
        self.assertEqual([x.path for x in c], ["a.kv"])
        self.assertEqual(self.p.probe(self.ctx, c[0])["pairs"], 3)
        t = self.p.parse(self.ctx, c[0])
        self.assertEqual([(e.key, e.value) for e in t.entries], [("x", "1"), ("y", "a\nb"), ("z", "3")])
        self.assertEqual((t.locale, t.adapter_id, t.stats["ok"]), ("en", "example_kv", 3))
        self.assertEqual(t.entries[0].source.container_stack, ["a.kv"])

    def test_build_preserves_everything_else_and_does_not_touch_vfs(self):
        c = self.p.list_candidates(self.ctx)[0]
        t = self.p.parse(self.ctx, c)
        t.entries[1].value = "mới\nxuống dòng"
        new = self.p.build(self.ctx, t, self.SRC)
        self.assertEqual(new, b"# chao\nx=1\ny=m\xe1\xbb\x9bi\\nxu\xe1\xbb\x91ng d\xc3\xb2ng\nz=3")
        self.assertEqual(self.vfs.read("a.kv"), self.SRC)               # nguồn không bị đổi
        self.assertTrue(self.p.verify(self.ctx, self.SRC, new)["ok"])
        self.assertTrue(self.p.selftest(self.ctx)["ok"])

    def test_missing_key_warns_not_appends(self):
        t = LocTable([LocEntry("nope", "1", None, "native", "ok", src())], None, "example_kv")
        new = self.p.build(self.ctx, t, self.SRC)
        self.assertEqual(new, self.SRC)
        self.assertEqual(t.warnings[0]["code"], "E_PARSE_PARTIAL")

    def test_bad_input_file(self):
        v = MemoryVFS({"a.kv": b"\xff\xfe=1"})
        ctx = PluginContext(v).for_plugin(self.p.manifest)
        self.assertEqual(code_of(self.p.parse, ctx, Candidate("a.kv", "example_kv")), "E_CORRUPT")
        self.assertEqual(code_of(self.p.build, ctx, LocTable(), b"\xff"), "E_CORRUPT")

    def test_entry_size_limit_from_manifest(self):
        v = MemoryVFS({"a.kv": b"k=" + b"v" * (2 * 1024 * 1024)})
        ctx = PluginContext(v).for_plugin(self.p.manifest)
        self.assertEqual(code_of(self.p.parse, ctx, Candidate("a.kv", "example_kv")), "E_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
