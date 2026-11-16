"""Test khóa đợt rà soát tổng cuối (nhóm F2/F5/F6).

- F6: diagnostics_tool chạy THẬT trên fixture (inventory/hex/strings/encoding),
  cả CLI `diagnose inventory|hex|strings <file>` lẫn menu [5][3].
- F5: file picker (core.file_dialog.choose_path) được nối vào cả hai luồng
  tương tác mà nhóm sở hữu: run_interactive của dịch vụ và menu chẩn đoán.
- F2: _mod_job không còn import services.unity_tool; thông điệp lỗi của
  adapter mức sdk giống hệt services.unity_tool (so từng trường); F2 xong
  ở bước tích hợp: sdk/batch.py không còn import services nào, registry
  được tiêm từ services/batch_tool.py (registry=language_tool._registry(app)).
- #180: mod dài hơn trong cùng khối căn vẫn leo thang (khóa lại sau F2).

Không gọi mạng, không sửa file ngoài thư mục tạm.
"""
import ast
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from core.app import bootstrap
from core.errors import PB3Error
from tests.helpers import ROOT, TmpCase
from tests.helpers import pytest  # noqa: F401 - marker needs_fixture (stub trên iSH)

FIX_ZIP = ROOT / "tests" / "fixtures" / "zip" / "sample.ipa"
FIX_UNITY = ROOT / "tests" / "fixtures" / "unity" / "sample.unity3d"


def _scripted(answers):
    it = iter(answers)
    out = []

    def inp(prompt=""):
        out.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return inp, out


class TestF6Inventory(TmpCase):
    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_inventory_zip_thật(self):
        from services.diagnostics_tool import inventory_of
        inv = inventory_of(str(FIX_ZIP))
        self.assertEqual(inv["kind"], "zip")
        names = [e["name"] for e in inv["entries"]]
        self.assertEqual(names, ["Payload/Game.app/Info.plist",
                                 "Payload/Game.app/Data/level.unity3d",
                                 "Payload/Game.app/Data/config.txt"])
        by_name = {e["name"]: e for e in inv["entries"]}
        self.assertEqual(by_name["Payload/Game.app/Info.plist"]["size"], 93)
        self.assertEqual(by_name["Payload/Game.app/Data/config.txt"]["compress_type"], "deflated")
        self.assertEqual(inv["rejected"], [])
        self.assertEqual(inv["warnings"], [])

    def test_inventory_không_phải_container_báo_rõ(self):
        from services.diagnostics_tool import inventory_of
        txt = self.tmp / "ghi_chu.txt"
        txt.write_bytes("Xin chào 9 Kings!\n".encode("utf-8"))
        with self.assertRaises(PB3Error) as cm:
            inventory_of(str(txt))
        self.assertEqual(cm.exception.code, "E_UNSUPPORTED_FORMAT")

    def test_inventory_file_không_tồn_tại(self):
        from services.diagnostics_tool import inventory_of
        with self.assertRaises(PB3Error) as cm:
            inventory_of(str(self.tmp / "khong_co.zip"))
        self.assertEqual(cm.exception.code, "E_INPUT_NOT_FOUND")


class TestF6Hex(TmpCase):
    def test_hex_lines_khớp_byte_và_vừa_điện_thoại(self):
        from services.diagnostics_tool import hex_lines
        data = b"Hello 9 Kings!\n\x00\xff\x01\x02" + bytes(range(16))
        lines = hex_lines(data, 0, 44)
        self.assertEqual(lines[0], "00000000  48 65 6c 6c 6f 20 39 20|Hello 9 |")
        self.assertEqual(lines[1], "00000008  4b 69 6e 67 73 21 0a 00|Kings!..|")
        self.assertEqual(lines[2], "00000010  ff 01 02 00 01 02 03 04|........|")
        for ln in lines:
            self.assertLessEqual(len(ln), 44, ln)

    def test_hex_offset_tiếp_nối(self):
        from services.diagnostics_tool import hex_lines
        lines = hex_lines(b"0123456789abcdef", 128, 44)
        self.assertTrue(lines[0].startswith("00000080  30 31"))
        self.assertTrue(lines[1].startswith("00000088  38 39"))


class TestF6Strings(TmpCase):
    def _file(self, data: bytes):
        p = self.tmp / "mau.bin"
        p.write_bytes(data)
        return p

    def test_strings_ascii_và_tiếng_việt_utf8_có_offset(self):
        from services.diagnostics_tool import scan_strings
        data = b"\x00\x01Hello 9 Kings!\x00\xff" + "Xin chào BearTea".encode("utf-8") + b"\x00ab\x00"
        p = self._file(data)
        results, info = scan_strings(str(p))
        by_text = {t: o for o, t in results}
        self.assertEqual(by_text["Hello 9 Kings!"], 2)
        self.assertEqual(by_text["Xin chào BearTea"], 18)
        self.assertNotIn("ab", by_text)          # < min_len mặc định (4)
        # File có byte NUL nên đoán chung là nhị phân — chuỗi UTF-8 nhúng
        # bên trong vẫn trích đúng qua đường quét byte.
        self.assertEqual(info["encoding"], "nhị phân/không rõ")
        self.assertFalse(info["truncated"])

    def test_strings_lọc_từ_khóa_và_min_len(self):
        from services.diagnostics_tool import scan_strings
        p = self._file(b"alpha beta gamma\x00ALPHA\x00be\x00")
        results, info = scan_strings(str(p), keyword="ALPHA")
        self.assertEqual([t for _, t in results], ["alpha beta gamma", "ALPHA"])
        results2, _ = scan_strings(str(p), min_len=6)
        self.assertEqual([t for _, t in results2], ["alpha beta gamma"])

    def test_strings_không_nạp_cả_file_lớn_và_có_tiến_độ(self):
        from services.diagnostics_tool import scan_strings
        blob = (b"needle-here\x00" + b"\x00" * 1000) * 300   # > 256KB, qua nhiều khối
        p = self._file(blob)
        # Quét đủ: tổng đúng 300, tiến độ chạy tới cuối file.
        seen = []
        results, info = scan_strings(str(p),
                                     progress_cb=lambda d, t: seen.append((d, t)))
        self.assertEqual(info["total"], 300)
        self.assertFalse(info["truncated"])
        self.assertEqual(len(results), 300)
        self.assertTrue(seen and seen[-1] == (p.stat().st_size, p.stat().st_size))
        self.assertTrue(all(d <= t for d, t in seen))
        # Giới hạn: dừng sớm, total chỉ là số đếm tối thiểu (trung thực).
        results5, info5 = scan_strings(str(p), limit=5)
        self.assertTrue(info5["truncated"])
        self.assertEqual(len(results5), 5)
        self.assertGreaterEqual(info5["total"], 5)

    def test_strings_utf16(self):
        from services.diagnostics_tool import scan_strings
        p = self._file("Xin chào".encode("utf-16-le"))
        results, info = scan_strings(str(p))
        self.assertEqual(info["encoding"], "UTF-16LE")
        self.assertIn("Xin chào", [t for _, t in results])


class TestF6Encoding(TmpCase):
    def test_guess_encoding_bom_và_ascii_và_nhị_phân(self):
        from services.diagnostics_tool import guess_encoding
        self.assertEqual(guess_encoding(b"\xef\xbb\xbfhello")["encoding"], "UTF-8")
        self.assertTrue(guess_encoding(b"\xef\xbb\xbfhello")["certain"])
        self.assertEqual(guess_encoding(b"hello")["encoding"], "ASCII (tương thích UTF-8)")
        self.assertEqual(guess_encoding(b"\xff\xfeh\x00i\x00")["encoding"], "UTF-16LE")
        self.assertEqual(guess_encoding(b"\x00\x01\x02\x03")["encoding"], "nhị phân/không rõ")
        self.assertEqual(guess_encoding(b"")["encoding"], "trống")
        self.assertEqual(guess_encoding("Xin chào".encode("utf-8"))["encoding"], "UTF-8")


class TestF6Cli(TmpCase):
    def _run(self, argv, app=None):
        from services import diagnostics_tool as dt
        from ui import cli as cli_mod
        ns = cli_mod.build_parser().parse_args(argv)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = dt.run_cli(app, ns)
        return rc, buf.getvalue()

    def test_parser_dạng_mới_và_dạng_cũ(self):
        from ui import cli as cli_mod
        p = cli_mod.build_parser()
        ns = p.parse_args(["diagnose", "inventory", "x.zip"])
        self.assertEqual((ns.path, ns.extra), ("inventory", "x.zip"))
        ns = p.parse_args(["diagnose", "x.zip"])
        self.assertEqual((ns.path, ns.extra), ("x.zip", None))
        ns = p.parse_args(["diagnose", "hex", "x.zip", "--offset", "8", "--length", "16"])
        self.assertEqual((ns.offset, ns.length), (8, 16))

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_cli_inventory_zip_thật(self):
        rc, out = self._run(["diagnose", "inventory", str(FIX_ZIP)])
        self.assertEqual(rc, 0)
        self.assertIn("Payload/Game.app/Data/level.unity3d", out)
        self.assertIn("300", out)
        self.assertIn("deflated", out)

    @pytest.mark.needs_fixture("zip/sample.ipa")
    def test_cli_mặc_định_là_inventory(self):
        rc, out = self._run(["diagnose", str(FIX_ZIP)])
        self.assertEqual(rc, 0)
        self.assertIn("level.unity3d", out)

    def test_cli_hex_và_strings(self):
        data = self.tmp / "mau.bin"
        data.write_bytes(b"Hello 9 Kings!\x00" + "Xin chào".encode("utf-8"))
        rc, out = self._run(["diagnose", "hex", str(data), "--length", "16"])
        self.assertEqual(rc, 0)
        # Dòng hex vừa màn hình điện thoại: cột ASCII bị cắt ngắn theo chiều
        # rộng (hex_lines cắt theo term_width) nên chỉ khóa phần ổn định
        # theo chiều rộng (offset + byte hex); nội dung ASCII kiểm qua
        # strings bên dưới. (Từng fail khi mô phỏng iSH COLUMNS=40 vì khóa
        # cả cột ASCII đầy đủ "|Hello 9 |" — hành vi cắt ngắn là đúng.)
        self.assertIn("00000000  48 65 6c 6c 6f 20 39 20", out)
        self.assertIn("00000008  4b 69 6e 67 73 21 00 58", out)
        from core.color import display_width
        for ln in out.splitlines():
            self.assertLessEqual(display_width(ln), 44, ln)
        rc, out = self._run(["diagnose", "strings", str(data)])
        self.assertEqual(rc, 0)
        self.assertIn("Hello 9 Kings!", out)
        self.assertIn("Xin chào", out)
        rc, out = self._run(["diagnose", "strings", str(data), "--keyword", "kings"])
        self.assertEqual(rc, 0)
        self.assertIn("Hello 9 Kings!", out)
        self.assertNotIn("Xin chào", out)

    def test_cli_không_phải_container_báo_rõ_không_crash(self):
        txt = self.tmp / "ghi_chu.txt"
        txt.write_bytes("Xin chào\n".encode("utf-8"))
        rc, out = self._run(["diagnose", "inventory", str(txt)])
        self.assertEqual(rc, 0)
        self.assertIn("Không phải container", out)
        self.assertIn("UTF-8", out)

    def test_cli_thiếu_file_lỗi_e_config_invalid(self):
        with self.assertRaises(PB3Error) as cm:
            self._run(["diagnose", "inventory"])
        self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")


@pytest.mark.needs_fixture("zip/sample.ipa")
class TestF5PickerWiring(TmpCase):
    def setUp(self):
        super().setUp()
        self.app = bootstrap(self.tmp)
        self.addCleanup(self.app.close)
        self.app.paths.input.mkdir(parents=True, exist_ok=True)
        self.sample = self.app.paths.input / "mau.zip"
        self.sample.write_bytes(FIX_ZIP.read_bytes())

    def test_run_interactive_dùng_picker_chọn_file_bằng_số(self):
        """Luồng 1 (dịch vụ): picker liệt kê file, gõ số để chọn, [1] inventory
        hiện entry thật, [0] quay lại, q thoát."""
        from services import diagnostics_tool as dt
        ask, _ = _scripted(["1", "1", "0", "q"])
        out = []
        dt.run_interactive(self.app, input_fn=ask, say=out.append)
        txt = "\n".join(out)
        self.assertIn("mau.zip", txt)
        self.assertIn("level.unity3d", txt)
        self.assertIn("300", txt)

    def test_menu_chẩn_đoán_dùng_picker(self):
        """Luồng 2 (menu chẩn đoán [5][3]): cùng picker, qua diagnose_menu."""
        from tests.test_app_menu_cli import run_menu_scripted
        rc, txt = run_menu_scripted(
            self.app, ["5", "3", "1", "1", "0", "q", "0", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("mau.zip", txt)
        self.assertIn("level.unity3d", txt)
        self.assertNotIn("CHƯA TRIỂN KHAI", txt)

    def test_picker_hủy_thì_không_làm_gì(self):
        from services import diagnostics_tool as dt
        ask, _ = _scripted(["q"])
        out = []
        dt.run_interactive(self.app, input_fn=ask, say=out.append)
        self.assertNotIn("level.unity3d", "\n".join(out))


class TestF2SdkAdapter(TmpCase):
    def test_batch_không_còn_import_services(self):
        src = (ROOT / "sdk" / "batch.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # F2 xong ở bước tích hợp đợt tổng cuối: sdk/batch.py không còn
        # import services nào (kể cả lười trong hàm) — registry được TIÊM
        # từ services/batch_tool.py (registry=language_tool._registry(app)).
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "services":
                found.append(tuple(a.name for a in node.names))
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "services" or a.name.startswith("services."):
                        found.append((a.name,))
        self.assertEqual(found, [], found)
        # _get_registry không còn import lười, chỉ trả registry đã tiêm.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_get_registry":
                inside = [n for n in ast.walk(node)
                          if isinstance(n, (ast.Import, ast.ImportFrom))]
                self.assertEqual(inside, [])

    def test_thông_điệp_lỗi_sdk_giống_hệt_unity_tool(self):
        """Adapter mức sdk phải cho cùng code/detail/hint/message_vi như
        services.unity_tool._usage — CLI không được thấy lỗi khác đi."""
        from sdk import batch as batch_sdk
        from services import unity_tool as ut
        cases = [
            ("file không phải bundle/assets; cần --entry chỉ tới bundle/assets bên trong",
             "unity patch <file> --entry <vpath> --object <id> ..."),
            ("không tìm thấy 'x.assets' trong container/bundle",
             "unity patch <file> --entry <vpath> --object <id> ..."),
        ]
        for what, usage in cases:
            a = batch_sdk._sdk_unity_usage(what, usage)
            b = ut._usage(what, usage)
            self.assertEqual((a.code, a.detail, a.hint, a.message_vi),
                             (b.code, b.detail, b.hint, b.message_vi))

    @pytest.mark.needs_fixture("unity/sample.unity3d", "zip/sample.ipa")
    def test_resolve_vpath_lỗi_giống_hệt_unity_tool(self):
        from sdk import batch as batch_sdk
        from sdk import unity as unity_sdk
        from services import unity_tool as ut
        from core.limits import Limits
        backend = unity_sdk.UnityBackend("AUTO", Limits())
        # Zip (container) + entry không có: cả hai phía phải cho cùng lỗi
        # _usage "không tìm thấy ..." (code/detail/hint/message_vi).
        data = FIX_ZIP.read_bytes()
        errs = []
        for fn in (batch_sdk._sdk_resolve_vpath, ut._resolve_vpath):
            with self.assertRaises(PB3Error) as cm:
                fn(data, "khong_co.txt", backend, Limits())
            errs.append(cm.exception)
        self.assertEqual(
            [(e.code, e.detail, e.hint, e.message_vi) for e in errs],
            [(errs[0].code, errs[0].detail, errs[0].hint, errs[0].message_vi)] * 2)
        self.assertEqual(errs[0].code, "E_CONFIG_INVALID")
        self.assertIn("không tìm thấy 'khong_co.txt'", errs[0].detail)
        # File thường, không entry: lỗi "file không phải bundle/assets" giống nhau.
        errs = []
        for fn in (batch_sdk._sdk_resolve_vpath, ut._resolve_vpath):
            with self.assertRaises(PB3Error) as cm:
                fn(b"chi la text", None, backend, Limits())
            errs.append(cm.exception)
        self.assertEqual([(e.code, e.detail, e.hint, e.message_vi) for e in errs],
                         [(errs[0].code, errs[0].detail, errs[0].hint, errs[0].message_vi)] * 2)
        # Đường đúng vẫn đi được qua adapter sdk (bundle + entry trong bundle).
        kind, unit, entry, label = batch_sdk._sdk_resolve_vpath(
            FIX_UNITY.read_bytes(), "CAB-0123456789abcdef0123456789abcdef",
            backend, Limits())
        self.assertEqual((kind, entry), ("bundle", "CAB-0123456789abcdef0123456789abcdef"))

    def test_default_output_rel_giống_hệt_unity_tool(self):
        from sdk import batch as batch_sdk
        from services import unity_tool as ut
        for args in (("game.ipa", "bundle", "Payload/Game.app/Data/level.unity3d"),
                     ("resources.assets", "assets", "assets"),
                     ("nested.ipa", "bundle", "bundle")):
            self.assertEqual(batch_sdk._sdk_default_output_rel(*args),
                             ut._default_output_rel(*args))

    @pytest.mark.needs_fixture("unity/sample.unity3d")
    def test_180_vẫn_xanh_qua_adapter_sdk_mới(self):
        """#180 (C10): 'Xin chao' -> 'Xin chào' (17->18 byte, cùng khối căn)
        qua batch mod thật (adapter sdk mới) phải ok và file đã patch đọc
        lại đúng chuỗi mới — trước C10 trường hợp này fail E_WRITE_DISABLED."""
        from sdk.batch import BatchItem, BatchRunner
        item = BatchItem(path=str(FIX_UNITY), mode="mod",
                         file="CAB-0123456789abcdef0123456789abcdef",
                         old="Xin chao", new="Xin chào", object="1001")
        s = BatchRunner().run([item], out_dir=self.tmp / "out", dry_run=False)
        self.assertEqual(s.ok, 1, s.games[0].error_message)
        self.assertNotEqual(s.games[0].error_code, "E_WRITE_DISABLED")
        patched = Path(s.games[0].game_dir) / "sample.unity3d"
        self.assertIn("Xin chào 9 Kings!".encode("utf-8"), patched.read_bytes())


if __name__ == "__main__":
    unittest.main()
