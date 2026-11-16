"""Sửa các hạn chế còn tồn tại của Claude 01/02 (decisions #55): chú thích local.toml, pid tái dùng,
ghi output không bao giờ đè khi chạy song song, CRLF của plugin kv, test SIGINT khi chạy nền."""
import json
import os
import unittest
from unittest import mock

from core import config, runstate, safe_io
from core.errors import PB3Error
from core.paths import Paths
from sdk.example_kv import KvPlugin, parse_text
from sdk.types import LocEntry, LocTable, SourceRef
from tests.helpers import TmpCase


# Pid của tiến trình cha, chốt MỘT LẦN lúc import (main thread).
# Probe trên iSH 2026-11-16: main getppid()=8 (đúng), nhưng trong daemon
# thread getppid()=1881 (= pid của chính tiến trình, SAI) — kernel usermode
# của iSH trả pid cha sai trong luồng không phải main (cơ chế (A) đã xác
# nhận; kill() thì bình thường). Test chạy trong daemon thread (ThreadedSuite)
# nên KHÔNG ĐƯỢC gọi os.getppid() lúc chạy test để bịa marker — marker sẽ
# mang đúng pid của chính tiến trình, acquire() thấy pid == getpid() nên
# trả 'interrupted' thay vì 'other_running' (mock pid_alive không cứu được
# vì nhánh pid != getpid() đã chặn trước). Dùng _PARENT_PID đã chốt này.
_PARENT_PID = os.getppid()


class TestSaveLocalKeepsComments(TmpCase):
    def setUp(self):
        super().setUp()
        self.p = Paths(self.tmp)
        self.p.local_toml.parent.mkdir(parents=True, exist_ok=True)

    def read(self):
        return self.p.local_toml.read_text(encoding="utf-8")

    def test_comments_and_order_kept_on_update(self):
        self.p.local_toml.write_text("# ghi chú của tôi\n[limits]\n# số luồng\nworkers = 1\n\n[log]\nlevel = \"INFO\"  # cũ\n",
                                     encoding="utf-8")
        config.save_local(self.p, {"limits.workers": 2})
        txt = self.read()
        self.assertIn("# ghi chú của tôi", txt)
        self.assertIn("# số luồng", txt)
        self.assertIn("workers = 2", txt)
        self.assertIn('level = "INFO"  # cũ', txt)          # dòng không đụng tới giữ nguyên cả chú thích cuối dòng
        self.assertEqual(config.load_config(self.p).get("limits.workers"), 2)

    def test_new_key_in_existing_section_and_new_section(self):
        self.p.local_toml.write_text("# c\n[limits]\nworkers = 2\n", encoding="utf-8")
        config.save_local(self.p, {"limits.disk_factor": 3.0, "log.level": "WARNING"})
        txt = self.read()
        self.assertTrue(txt.startswith("# c\n"))
        cfg = config.load_config(self.p)
        self.assertEqual((cfg.get("limits.workers"), cfg.get("limits.disk_factor"), cfg.get("log.level")),
                         (2, 3.0, "WARNING"))
        self.assertEqual([w for w in cfg.warnings if "local" in w], [])

    def test_multiline_array_falls_back_without_corruption(self):
        self.p.local_toml.write_text('[paths]\ninput_extra = [\n  "/root/data",\n]\n[limits]\nworkers = 1\n', encoding="utf-8")
        config.save_local(self.p, {"limits.workers": 2})
        cfg = config.load_config(self.p)
        self.assertEqual(cfg.get("limits.workers"), 2)
        self.assertEqual(cfg.get("paths.input_extra"), ["/root/data"])      # dữ liệu không mất dù chú thích có thể mất

    def test_unknown_user_keys_preserved(self):
        self.p.local_toml.write_text("[limits]\nworkers = 1\n[tuy_chinh]\nx = 5\n", encoding="utf-8")
        config.save_local(self.p, {"limits.workers": 2})
        self.assertIn("[tuy_chinh]\nx = 5", self.read())


class TestPidReuse(TmpCase):
    def setUp(self):
        super().setUp()
        # Các test này chỉ kiểm logic pstart, không kiểm kill(2) — ép pid
        # còn sống để cô lập đúng phần cần kiểm. Marker dùng _PARENT_PID
        # đã chốt lúc import (xem ghi chú ở đầu file), không gọi
        # os.getppid() trong test (sai trên iSH khi chạy trong thread).
        self._pid_alive = mock.patch.object(runstate, "pid_alive", return_value=True)
        self._pid_alive.start()
        self.addCleanup(self._pid_alive.stop)

    def marker(self, **extra):
        rm = runstate.RunMarker(self.tmp, "s-new")
        rec = {"pid": _PARENT_PID, "session": "old", "started": 1.0}
        rec.update(extra)
        (self.tmp / runstate.MARKER_NAME).write_text(json.dumps(rec), encoding="utf-8")
        return rm

    def test_live_pid_with_same_start_is_other_running(self):
        ps = runstate.proc_start(_PARENT_PID)
        if ps is None:
            self.skipTest("không có /proc/<pid>/stat")
        self.assertEqual(self.marker(pstart=ps).acquire().state, "other_running")

    def test_live_pid_with_different_start_is_pid_reused(self):
        if runstate.proc_start(_PARENT_PID) is None:
            self.skipTest("không có /proc/<pid>/stat")
        self.assertEqual(self.marker(pstart="1").acquire().state, "interrupted")

    def test_old_marker_without_pstart_keeps_old_behavior(self):
        self.assertEqual(self.marker().acquire().state, "other_running")

    def test_unreadable_proc_keeps_old_behavior(self):
        with mock.patch.object(runstate, "proc_start", return_value=None):
            self.assertEqual(self.marker(pstart="1").acquire().state, "other_running")

    def test_marker_uses_pinned_parent_pid_not_live_getppid(self):
        # Khóa hồi quy cho probe iSH 2026-11-16: mô phỏng getppid() hỏng
        # trong thread (trả về pid của chính tiến trình như trên iSH) —
        # marker vẫn phải trỏ đúng tiến trình cha đã chốt, acquire() vẫn
        # trả 'other_running' (không phụ thuộc getppid() lúc chạy test).
        with mock.patch.object(os, "getppid", return_value=os.getpid()):
            self.assertEqual(self.marker().acquire().state, "other_running")

    def test_acquire_records_pstart(self):
        rm = runstate.RunMarker(self.tmp, "s1")
        rm.acquire()
        rec = json.loads((self.tmp / runstate.MARKER_NAME).read_text(encoding="utf-8"))
        self.assertIn("pstart", rec)


class TestPidAlive(TmpCase):
    def test_unknown_oserror_means_alive(self):
        # Chỉ ESRCH mới chắc chắn là đã chết; errno lạ -> coi như còn sống
        # để khỏi giành marker của phiên đang chạy.
        with mock.patch.object(runstate.os, "kill", side_effect=OSError(22, "lỗi lạ")):
            self.assertTrue(runstate.pid_alive(1234))

    def test_process_lookup_means_dead(self):
        with mock.patch.object(runstate.os, "kill", side_effect=ProcessLookupError(3, "không có")):
            self.assertFalse(runstate.pid_alive(1234))

    def test_permission_error_means_alive(self):
        with mock.patch.object(runstate.os, "kill", side_effect=PermissionError(1, "cấm")):
            self.assertTrue(runstate.pid_alive(1234))

    def test_nonpositive_pid_is_dead(self):
        self.assertFalse(runstate.pid_alive(0))
        self.assertFalse(runstate.pid_alive(-5))


class TestExclusiveWrite(TmpCase):
    def test_exclusive_never_overwrites(self):
        dest = self.tmp / "a.bin"
        dest.write_bytes(b"cu")
        with self.assertRaises(PB3Error) as cm:
            safe_io.atomic_write_bytes(dest, b"moi", exclusive=True)
        self.assertEqual(cm.exception.code, "E_OUTPUT_EXISTS")
        self.assertEqual(dest.read_bytes(), b"cu")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.bin"])        # tmp đã dọn

    def test_exclusive_creates_when_free(self):
        dest = self.tmp / "sub" / "b.bin"
        safe_io.atomic_write_bytes(dest, b"x", exclusive=True)
        self.assertEqual(dest.read_bytes(), b"x")
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["b.bin"])

    def test_non_exclusive_still_replaces(self):
        dest = self.tmp / "c.bin"
        dest.write_bytes(b"cu")
        safe_io.atomic_write_bytes(dest, b"moi")
        self.assertEqual(dest.read_bytes(), b"moi")

    def test_race_on_version_name_retries_next_number(self):
        (self.tmp / "o.txt").write_bytes(b"goc")
        real = safe_io.resolve_output
        calls = {"n": 0}

        def stale(root, rel, policy="version"):
            calls["n"] += 1
            r = real(root, rel, policy=policy)
            if calls["n"] == 1:                       # lần chọn đầu: "phiên khác" giành mất o.v1.txt ngay trước khi ta ghi
                r.write_bytes(b"cua-phien-khac")
            return r
        with mock.patch.object(safe_io, "resolve_output", stale):
            out = safe_io.write_output_bytes(self.tmp, "o.txt", b"moi")
        self.assertEqual(out.name, "o.v2.txt")
        self.assertEqual((self.tmp / "o.v1.txt").read_bytes(), b"cua-phien-khac")
        self.assertEqual((self.tmp / "o.txt").read_bytes(), b"goc")
        self.assertEqual(out.read_bytes(), b"moi")

    def test_policy_error_still_raises(self):
        (self.tmp / "o.txt").write_bytes(b"goc")
        with self.assertRaises(PB3Error) as cm:
            safe_io.write_output_bytes(self.tmp, "o.txt", b"moi", policy="error")
        self.assertEqual(cm.exception.code, "E_OUTPUT_EXISTS")


class TestKvCrlf(TmpCase):
    def test_parse_strips_cr(self):
        self.assertEqual(parse_text("# c\r\nx=1\r\ny=2"), [("x", "1"), ("y", "2")])

    def test_build_keeps_crlf(self):
        base = b"# c\r\nx=1\r\ny=a\r\n"
        t = LocTable([LocEntry("x", "9", None, "native", "ok", SourceRef(["m.kv"], "m.kv"))], None, "kv")
        p = KvPlugin()
        ctx = mock.MagicMock()
        new = p.build(ctx, t, base)
        self.assertEqual(new, b"# c\r\nx=9\r\ny=a\r\n")


if __name__ == "__main__":
    unittest.main()
