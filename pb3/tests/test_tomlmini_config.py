import unittest

from tests import helpers
from tests.helpers import TmpCase
from core import config, tomlmini
from core.errors import PB3Error
from core.paths import Paths

try:
    import tomllib
except ImportError:
    tomllib = None

DOC = '''# chú thích
a = 1_000
neg = -5
f = 2.5e2
[x.y]
s = "xin chào \\u00e9 # không phải comment"  # comment thật
lit = 'a\\b'
l = ['a', "b",
  3, true ]
[z]
k = false
"khóa lạ" = "v"
'''


class TestTomlMini(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "không có tomllib để đối chiếu")
    def test_matches_tomllib(self):
        self.assertEqual(tomlmini.loads(DOC), tomllib.loads(DOC))

    @unittest.skipIf(tomllib is None, "không có tomllib để đối chiếu")
    def test_default_toml_matches_tomllib(self):
        text = (helpers.ROOT / "config" / "default.toml").read_text(encoding="utf-8")
        self.assertEqual(tomlmini.loads(text), tomllib.loads(text))

    def test_roundtrip_dumps(self):
        d = tomlmini.loads(DOC)
        self.assertEqual(tomlmini.loads(tomlmini.dumps(d)), d)

    def test_rejects_unsupported_and_bad(self):
        for bad in ["a = {b=1}", "[[t]]", "a = ", "a = 'x", "a = [1, 2", "[t", "a 1",
                    "a = 1\na = 2", "[t]\n[t]", 'a = """x"""', "a = 1 2", "a = nope"]:
            with self.assertRaises(tomlmini.TomlError, msg=bad):
                tomlmini.loads(bad)

    def test_error_has_line_number(self):
        try:
            tomlmini.loads("a = 1\nb = ?")
        except tomlmini.TomlError as e:
            self.assertEqual(e.line, 2)


class TestConfig(TmpCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "config").mkdir()
        self.p = Paths(self.tmp)

    def write_local(self, text):
        self.p.local_toml.write_text(text, encoding="utf-8")

    def test_default_toml_in_sync_with_options(self):
        tree = config.parse_toml((helpers.ROOT / "config" / "default.toml").read_text(encoding="utf-8"))
        ok, warns = config.validate_tree(tree)
        self.assertEqual(warns, [])
        self.assertEqual(ok, config.builtin_defaults())

    def test_missing_default_uses_builtin_with_warning(self):
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("limits.workers"), 1)
        self.assertTrue(any("default.toml" in w for w in cfg.warnings))

    def test_local_overrides(self):
        self.write_local("[limits]\nworkers = 3\n[log]\nlevel = \"DEBUG\"\n")
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("limits.workers"), 3)
        self.assertEqual(cfg.get("log.level"), "DEBUG")
        self.assertEqual(cfg.section("limits")["workers"], 3)

    def test_invalid_value_falls_back_with_warning(self):
        self.write_local("[limits]\nworkers = 99\nchunk_size = \"x\"\nmax_depth = true\n")
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("limits.workers"), 1)
        self.assertEqual(cfg.get("limits.max_depth"), 4)
        self.assertEqual(len([w for w in cfg.warnings if "limits." in w]), 3)

    def test_unknown_key_warns(self):
        self.write_local("[limits]\nbogus = 1\n[nope]\nx = 1\ntop = 1\n")
        cfg = config.load_config(self.p)
        self.assertTrue(any("limits.bogus" in w for w in cfg.warnings))
        self.assertTrue(any("nope.x" in w for w in cfg.warnings))

    def test_broken_syntax_ignored_no_crash(self):
        self.write_local("[limits\nworkers = ")
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("limits.workers"), 1)
        self.assertTrue(any("local.toml" in w for w in cfg.warnings))

    def test_non_utf8_local_ignored(self):
        self.p.local_toml.write_bytes(b"\xff\xfe\x00bad")
        cfg = config.load_config(self.p)
        self.assertTrue(cfg.warnings)

    def test_unsafe_paths_rejected(self):
        self.write_local('[paths]\nworkspace = "../out"\nlogs = "/abs"\n')
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("paths.workspace"), "workspace")
        self.assertEqual(cfg.get("paths.logs"), "logs")
        self.assertEqual(self.p.workspace, self.tmp / "workspace")

    def test_input_extra_must_be_absolute(self):
        self.write_local('[paths]\ninput_extra = ["rel/x"]\n')
        self.assertEqual(config.load_config(self.p).get("paths.input_extra"), [])
        self.write_local('[paths]\ninput_extra = ["/root/data"]\n')
        self.assertEqual(config.load_config(self.p).get("paths.input_extra"), ["/root/data"])

    def test_int_accepted_for_float_but_bool_not(self):
        self.write_local("[limits]\ndisk_factor = 3\n")
        self.assertEqual(config.load_config(self.p).get("limits.disk_factor"), 3.0)
        self.write_local("[limits]\ndisk_factor = true\n")
        self.assertEqual(config.load_config(self.p).get("limits.disk_factor"), 2.5)

    def test_save_local_roundtrip_and_merge(self):
        config.save_local(self.p, {"limits.workers": 2})
        config.save_local(self.p, {"log.level": "WARNING"})
        cfg = config.load_config(self.p)
        self.assertEqual((cfg.get("limits.workers"), cfg.get("log.level")), (2, "WARNING"))
        self.assertEqual([w for w in cfg.warnings if "local" in w], [])

    def test_save_local_invalid_writes_nothing(self):
        for upd in ({"limits.workers": 0}, {"nope.key": 1}, {"limits.workers": "2"}):
            with self.assertRaises(PB3Error) as cm:
                config.save_local(self.p, upd)
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
        self.assertFalse(self.p.local_toml.exists())

    def test_save_local_backs_up_corrupt_file(self):
        self.write_local("[broken")
        config.save_local(self.p, {"limits.workers": 2})
        self.assertTrue(list((self.tmp / "config").glob("local.toml.bak-*")))
        self.assertEqual(config.load_config(self.p).get("limits.workers"), 2)

    def test_pure_python_backend_parses_same(self):
        text = (helpers.ROOT / "config" / "default.toml").read_text(encoding="utf-8")
        a, _ = config.validate_tree(tomlmini.loads(text))
        self.assertEqual(a, config.builtin_defaults())


if __name__ == "__main__":
    unittest.main()
