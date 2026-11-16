"""Claude 09 (support/kb): lệnh `kb` qua CLI thật + menu [3] tùy chọn m/k.

Dùng KB GIẢ (fake sdk.kb) vì nhóm KB viết sdk/kb.py song song; fake cài đúng
giao diện đã chốt (export_profile/import_profile/forget/list_games/prune/close)
và lưu JSON vào đúng file kb/pb3_kb.sqlite của app. Fixture xác định,
không os.urandom, không mạng.
"""
import argparse
import contextlib
import io
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import config
from core.errors import PB3Error
from core.paths import Paths
from services import plugin_manager as pm
from tests.helpers import TmpCase

SAMPLE_PROFILE = {
    "game": {"bundle_id": "com.example.game", "version": "1.0.5", "engine": "unity"},
    "files": [{"rel_path": "Payload/Game.app/Data/level.unity3d", "size": 1234,
               "sha256_8": "deadbeef", "role": "bundle", "adapter": "unity"}],
    "locales": [{"entry_path": "en.json", "locale": "en", "adapter": "json_text"}],
}


class FakeKB:
    """KB giả lưu JSON vào đúng đường dẫn file được truyền (kể cả .sqlite)."""

    def __init__(self, path):
        self.path = Path(path)
        self._games = {}
        if self.path.is_file():
            try:
                self._games = json.loads(self.path.read_text(encoding="utf-8")).get("games", {})
            except (OSError, ValueError):
                self._games = {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"games": self._games}, ensure_ascii=False),
                             encoding="utf-8")

    def export_profile(self, bundle_id_or_id):
        if bundle_id_or_id not in self._games:
            raise KeyError(bundle_id_or_id)
        return json.loads(json.dumps(self._games[bundle_id_or_id]))

    def import_profile(self, data):
        gid = data["game"]["bundle_id"]
        self._games[gid] = json.loads(json.dumps(data))
        self._save()
        return gid, []

    def forget(self, bundle_id_or_id):
        if bundle_id_or_id not in self._games:
            return False
        del self._games[bundle_id_or_id]
        self._save()
        return True

    def list_games(self):
        return [{"bundle_id": gid, "version": g["game"].get("version", ""),
                 "engine": g["game"].get("engine", "")}
                for gid, g in sorted(self._games.items())]

    def plugin_stats(self):
        return {}

    def prune(self, max_mb=64):
        return 0

    def close(self):
        pass


def install_fake_kb(testcase):
    """Cài fake sdk.kb vào sys.modules; dọn ở tearDown."""
    mod = types.ModuleType("sdk.kb")
    mod.KB = FakeKB
    sys.modules["sdk.kb"] = mod
    testcase.addCleanup(lambda: sys.modules.pop("sdk.kb", None))
    return mod


def make_app(root):
    paths = Paths(str(root))
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(paths=paths, config=config.load_config(paths, apply_paths=False))


def run_kb(app, action, arg=None, out=None):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = pm.run_cli(app, argparse.Namespace(command="kb", action=action, arg=arg, out=out))
    return rc, buf_out.getvalue(), buf_err.getvalue()


class Script:
    """Giả lập nhập liệu: trả lần lượt từng dòng, hết thì EOFError."""

    def __init__(self, *lines):
        self.lines, self.prompts = list(lines), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


class TestKbCli(TmpCase):
    def setUp(self):
        super().setUp()
        install_fake_kb(self)
        self.app = make_app(self.tmp)

    def test_empty_kb_reports_clearly_without_crash(self):
        for action, arg in (("export", "com.example.game"), ("forget", "com.example.game")):
            rc, out, err = run_kb(self.app, action, arg)
            self.assertEqual(rc, 0, err)
            self.assertIn("KB trống", out)
        rc, out, err = run_kb(self.app, "prune")
        self.assertEqual(rc, 0, err)
        self.assertIn("KB trống", out)

    def test_import_export_roundtrip_keeps_choices(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        rc, out, err = run_kb(self.app, "import", str(src))
        self.assertEqual(rc, 0, err)
        self.assertIn("com.example.game", out)
        rc, out, err = run_kb(self.app, "export", "com.example.game")
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["game"], SAMPLE_PROFILE["game"])
        self.assertEqual(data["files"], SAMPLE_PROFILE["files"])
        self.assertEqual(data["locales"], SAMPLE_PROFILE["locales"])

    def test_export_out_writes_file(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        run_kb(self.app, "import", str(src))
        dest = self.tmp / "out.json"
        rc, out, err = run_kb(self.app, "export", "com.example.game", out=str(dest))
        self.assertEqual(rc, 0, err)
        self.assertIn("Đã xuất profile", out)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8"))["game"],
                         SAMPLE_PROFILE["game"])

    def test_forget_removes_game(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        run_kb(self.app, "import", str(src))
        rc, out, err = run_kb(self.app, "forget", "com.example.game")
        self.assertEqual(rc, 0, err)
        self.assertIn("Đã quên tri thức game", out)
        rc, out, err = run_kb(self.app, "export", "com.example.game")
        self.assertEqual(rc, 0)
        self.assertIn("KB trống", out)

    def test_forget_unknown_game_is_user_error(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        run_kb(self.app, "import", str(src))
        rc, out, err = run_kb(self.app, "forget", "com.khong.co")
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)

    def test_prune_reports_bytes(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        run_kb(self.app, "import", str(src))
        rc, out, err = run_kb(self.app, "prune")
        self.assertEqual(rc, 0, err)
        self.assertIn("Đã dọn 0 byte", out)

    def test_import_missing_file_and_bad_json(self):
        rc, out, err = run_kb(self.app, "import", str(self.tmp / "khong_co.json"))
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)
        bad = self.tmp / "bad.json"
        bad.write_text("{không phải json", encoding="utf-8")
        rc, out, err = run_kb(self.app, "import", str(bad))
        self.assertEqual(rc, PB3Error("E_CORRUPT").exit_code)
        self.assertIn("E_CORRUPT", err)

    def test_export_unknown_game_is_user_error(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        run_kb(self.app, "import", str(src))
        rc, out, err = run_kb(self.app, "export", "com.khong.co")
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)


class TestRealKb(TmpCase):
    """Tích hợp thật với sdk/kb.py (nhóm KB đã giao): không dùng KB giả."""

    def setUp(self):
        super().setUp()
        sys.modules.pop("sdk.kb", None)   # chắc chắn không còn KB giả của test khác
        self.app = make_app(self.tmp)

    def test_real_kb_import_export_roundtrip(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        rc, out, err = run_kb(self.app, "import", str(src))
        self.assertEqual(rc, 0, err)
        self.assertIn("com.example.game", out)
        rc, out, err = run_kb(self.app, "export", "com.example.game")
        self.assertEqual(rc, 0, err)
        data = json.loads(out)
        self.assertEqual(data["game"], SAMPLE_PROFILE["game"])
        self.assertEqual(data["files"], SAMPLE_PROFILE["files"])
        self.assertEqual(data["locales"], SAMPLE_PROFILE["locales"])
        rc, out, err = run_kb(self.app, "forget", "com.example.game")
        self.assertEqual(rc, 0, err)
        rc, out, err = run_kb(self.app, "export", "com.example.game")
        self.assertEqual(rc, 0)
        self.assertIn("KB trống", out)

    def test_real_kb_export_unknown_game_is_user_error(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(run_kb(self.app, "import", str(src))[0], 0)
        rc, out, err = run_kb(self.app, "export", "com.khong.co")
        self.assertEqual(rc, 1)
        self.assertIn("E_INPUT_NOT_FOUND", err)


class TestManageMenuMK(TmpCase):
    """Menu [3]: tùy chọn [m] ma trận hỗ trợ, [k] quên tri thức game."""

    def setUp(self):
        super().setUp()
        install_fake_kb(self)
        self.app = make_app(self.tmp)
        for pid in ("alpha_csv", "beta_csv"):
            rc, out, err = self._run_cmd("new", "format", pid)
            self.assertEqual(rc, 0, out + err)

    def _run_cmd(self, action, *args):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = pm.run_cli(self.app, argparse.Namespace(
                command="plugin", action=action, args=list(args)))
        return rc, buf_out.getvalue(), buf_err.getvalue()

    def run_menu(self, *lines):
        said, ask = [], Script(*lines)
        pm.run_interactive(self.app, input_fn=ask, say=said.append)
        return "\n".join(said)

    def test_m_shows_support_matrix(self):
        out = self.run_menu("m", "0")
        self.assertIn("MA TRẬN HỖ TRỢ", out)
        self.assertIn("Mức hỗ trợ chỉ đúng với những gì đã kiểm thử", out)
        self.assertIn("alpha_csv", out)

    def test_k_forgets_game(self):
        src = self.tmp / "profile.json"
        src.write_text(json.dumps(SAMPLE_PROFILE, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(run_kb(self.app, "import", str(src))[0], 0)
        out = self.run_menu("k", "com.example.game", "0")
        self.assertIn("Đã quên tri thức game 'com.example.game'", out)
        rc, out2, _err = run_kb(self.app, "export", "com.example.game")
        self.assertEqual(rc, 0)
        self.assertIn("KB trống", out2)

    def test_k_empty_kb_reports_clearly(self):
        out = self.run_menu("k", "com.example.game", "0")
        self.assertIn("KB trống", out)

    def test_zero_still_exits(self):
        out = self.run_menu("0")
        self.assertIn("Tạo mới / kiểm tra", out)


if __name__ == "__main__":
    unittest.main()
