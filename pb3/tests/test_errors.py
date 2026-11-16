import errno
import unittest
import zipfile

from tests import helpers  # noqa: F401
from core import errors
from core.errors import PB3Error

APPENDIX_D = ["E_OK", "E_INPUT_NOT_FOUND", "E_BAD_ARCHIVE", "E_ARCHIVE_BOMB", "E_UNSAFE_PATH",
              "E_NO_CANDIDATE", "E_AMBIGUOUS", "E_UNSUPPORTED_FORMAT", "E_ENCRYPTED", "E_MISSING_DATA",
              "E_PARSE_PARTIAL", "E_LOCALE_NOT_FOUND", "E_WRITE_DISABLED", "E_VERIFY_FAILED",
              "E_OUTPUT_EXISTS", "E_NO_SPACE", "E_PLUGIN_INVALID", "E_PLUGIN_TIMEOUT",
              "E_PLUGIN_FORBIDDEN", "E_CANCELLED", "E_INTERNAL"]


class TestErrorCodes(unittest.TestCase):
    def test_all_appendix_d_codes_exist(self):
        for c in APPENDIX_D:
            self.assertIn(c, errors.ERROR_SPECS, c)
            self.assertFalse(errors.ERROR_SPECS[c].extension, c)

    def test_extension_codes_flagged(self):
        ext = {c for c, s in errors.ERROR_SPECS.items() if s.extension}
        self.assertEqual(ext, {"E_UNSUPPORTED_ON_ISH", "E_CONFIG_INVALID", "E_TOO_LARGE", "E_IO",
                               "E_CORRUPT", "E_NOT_IMPLEMENTED", "E_NETWORK", "E_TIMEOUT"})

    def test_every_code_has_vi_message_and_valid_exit(self):
        for c, s in errors.ERROR_SPECS.items():
            self.assertTrue(s.message_vi.strip(), c)
            self.assertIn(s.exit_code, (0, 1, 2, 3, 4), c)
        self.assertEqual(errors.ERROR_SPECS["E_OK"].exit_code, 0)

    def test_exit_code_mapping_examples(self):
        self.assertEqual(PB3Error("E_INPUT_NOT_FOUND").exit_code, errors.EXIT_USER)
        self.assertEqual(PB3Error("E_UNSUPPORTED_FORMAT").exit_code, errors.EXIT_UNSUPPORTED)
        self.assertEqual(PB3Error("E_VERIFY_FAILED").exit_code, errors.EXIT_SAFETY)
        self.assertEqual(PB3Error("E_INTERNAL").exit_code, errors.EXIT_INTERNAL)

    def test_unknown_code_becomes_internal_not_silent(self):
        e = PB3Error("E_BOGUS", "x")
        self.assertEqual(e.code, "E_INTERNAL")
        self.assertIn("E_BOGUS", e.detail)

    def test_detail_is_sanitized_and_truncated(self):
        e = PB3Error("E_IO", "a\x00b\x07" + "x" * 1000)
        self.assertNotIn("\x00", e.detail)
        self.assertLess(len(e.detail), 400)
        self.assertIn("ký tự]", e.detail)

    def test_format_error_is_vietnamese_without_traceback(self):
        e = PB3Error("E_NO_SPACE", "cần 10", plugin_id="p1", where="a/b")
        txt = errors.format_error(e)
        for needle in ("[E_NO_SPACE]", "Chi tiết:", "Tại: a/b", "Plugin: p1", "Gợi ý:"):
            self.assertIn(needle, txt)
        self.assertNotIn("Traceback", txt)

    def test_to_dict_has_required_fields(self):
        d = PB3Error("E_BAD_ARCHIVE", "x", where="w").to_dict()
        for k in ("code", "message_vi", "detail", "plugin_id", "where", "hint", "exit_code"):
            self.assertIn(k, d)


class TestFromException(unittest.TestCase):
    def conv(self, exc, op="read"):
        return errors.error_from_exception(exc, where="w", op=op).code

    def test_mapping(self):
        self.assertEqual(self.conv(FileNotFoundError()), "E_INPUT_NOT_FOUND")
        self.assertEqual(self.conv(PermissionError()), "E_INPUT_NOT_FOUND")
        self.assertEqual(self.conv(PermissionError(), "write"), "E_IO")
        self.assertEqual(self.conv(OSError(errno.ENOSPC, "x")), "E_NO_SPACE")
        self.assertEqual(self.conv(OSError(errno.ENAMETOOLONG, "x")), "E_UNSAFE_PATH")
        self.assertEqual(self.conv(MemoryError()), "E_TOO_LARGE")
        self.assertEqual(self.conv(zipfile.BadZipFile("x")), "E_BAD_ARCHIVE")
        self.assertEqual(self.conv(KeyboardInterrupt()), "E_CANCELLED")
        self.assertEqual(self.conv(ValueError("x")), "E_INTERNAL")
        self.assertEqual(self.conv(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")), "E_CORRUPT")

    def test_write_messages(self):
        """X10: nhánh op="write" nói rõ lỗi ghi (không lẫn với 'đầu vào không tồn tại' của nhánh read)."""
        e = errors.error_from_exception(PermissionError("x"), where="w", op="write")
        self.assertEqual(e.code, "E_IO")
        self.assertIn("không có quyền ghi", e.detail)
        e = errors.error_from_exception(IsADirectoryError("x"), where="w", op="write")
        self.assertEqual(e.code, "E_IO")
        self.assertIn("đường đích là thư mục", e.detail)
        e = errors.error_from_exception(PermissionError("x"), where="w", op="read")
        self.assertEqual(e.code, "E_INPUT_NOT_FOUND")

    def test_passthrough(self):
        e = PB3Error("E_AMBIGUOUS")
        self.assertIs(errors.error_from_exception(e), e)

    def test_no_file_content_leaks_from_unicode_error(self):
        secret = b"SECRET-CONTENT-\xff"
        try:
            secret.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = errors.format_error(errors.error_from_exception(exc, where="f"))
        self.assertNotIn("SECRET", msg)


if __name__ == "__main__":
    unittest.main()
