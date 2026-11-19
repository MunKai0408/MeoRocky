"""Test dich trong notebook Colab (Buoc 3): WorkerProvider + cell dich.

Khong goi mang that: urlopen gia cho worker + gtx. Cell dich duoc exec tu
colab/extract-large-ipa.ipynb (nguon that duy nhat), khong copy code.
Tang fast: thuan bo nho/file nho, khong tien trinh con.
"""
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from core.errors import PB3Error
from sdk import exporters as exp
from sdk import translate as tr
from sdk.gate import check_placeholders
from tests.helpers import ROOT, TmpCase

NB = ROOT.parent / "colab" / "extract-large-ipa.ipynb"


class _Resp(object):
    """Gia HTTP response (context manager co read)."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _find_cell_source():
    nb = json.loads(NB.read_text(encoding="utf-8"))
    codes = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(codes) == 7, len(codes)
    src = "".join(codes[3]["source"])
    assert "GAME_FILE_NAME" in src and "find_ipa_by_name" in src
    return src


def _cell_source():
    nb = json.loads(NB.read_text(encoding="utf-8"))
    codes = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(codes) == 7, len(codes)
    # Cell dich khong con la cell code cuoi (cell Mod dung sau no).
    found = ["".join(c["source"]) for c in codes
             if "WorkerProvider" in "".join(c["source"])]
    assert len(found) == 1, "phai co dung 1 cell dich (WorkerProvider)"
    src = found[0]
    assert "TRANSLATE_AFTER_EXTRACT" in src
    return src


def _fake_gtx_urlopen(req, timeout=None):
    """Gia endpoint gtx: dich q thanh '[vi] q' (giu nguyen token mask)."""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
    q = (qs.get("q") or [""])[0]
    body = json.dumps([[["[vi] " + q, q, None, None]]]).encode("utf-8")
    return _Resp(body)


class TestNotebookStructure(TmpCase):
    def test_cells(self):
        nb = json.loads(NB.read_text(encoding="utf-8"))
        self.assertEqual(len(nb["cells"]), 13)
        kinds = [c["cell_type"] for c in nb["cells"]]
        self.assertEqual(kinds, ["markdown", "code", "code", "code",
                                 "markdown", "code", "markdown", "code",
                                 "markdown", "code", "markdown", "code",
                                 "markdown"])
        step3 = "".join(nb["cells"][8]["source"])
        self.assertIn("Bước 3", step3)
        code = "".join(nb["cells"][9]["source"])
        self.assertIn("WorkerProvider", code)
        self.assertIn("translated.%s.json", code)
        self.assertIn("translate_json_file", code)
        # Buoc 4 Mod (PR mod-colab-actions): markdown + code dung sau
        # cell dich, truoc cell ket thuc.
        step4 = "".join(nb["cells"][10]["source"])
        self.assertIn("Bước 4", step4)
        mod_code = "".join(nb["cells"][11]["source"])
        self.assertIn("run_mod_job", mod_code)
        self.assertIn("MOD_GATE_ONLY", mod_code)


class TestFindCell(TmpCase):
    def test_no_dst_defaults(self):
        nb = json.loads(NB.read_text(encoding="utf-8"))
        whole = "".join("".join(c["source"]) for c in nb["cells"])
        self.assertNotIn("1DfcaEn0hEfs88qv2pkO4ed6n4546TsP1", whole)
        self.assertNotIn("Don't Starve Together_2.1.0 VH.ipa", whole)
        self.assertNotIn("Game mặc định: Don't Starve Together", whole)
        self.assertNotIn("Notebook mặc định trích game", whole)
        notes = "".join(nb["cells"][6]["source"])
        self.assertNotIn("game mặc định", notes)
        src = _find_cell_source()
        self.assertIn('GAME_FILE_NAME = globals().get("GAME_FILE_NAME", "")',
                      src)
        self.assertIn('DRIVE_FILE_ID = globals().get("DRIVE_FILE_ID", "")',
                      src)
        self.assertIn("file/d/", notes)
        self.assertIn("Ví dụ: Don't Starve Together", notes)

    def test_empty_game_raises(self):
        with self.assertRaises(ValueError) as ctx:
            exec(compile(_find_cell_source(), "colab_find_cell", "exec"),
                 {"__name__": "__main__"})
        self.assertIn("Chưa điền game nào", str(ctx.exception))

    def test_find_by_name(self):
        ipa = self.tmp / "test.ipa"
        ipa.write_bytes(b"fake")
        src = _find_cell_source()
        src = src.replace('GAME_FILE_NAME = globals().get("GAME_FILE_NAME", "")',
                          'GAME_FILE_NAME = "test.ipa"')
        src = src.replace(
            'SEARCH_ROOTS = ["/content/drive/MyDrive", '
            '"/content/drive/Shareddrives"]',
            "SEARCH_ROOTS = [%r]" % str(self.tmp))
        ns = {"__name__": "__main__"}
        exec(compile(src, "colab_find_cell", "exec"), ns)
        self.assertEqual(ns["IPA_PATH"], str(ipa))
        self.assertEqual(ns["GAME_NAME"], "test")
        self.assertTrue(ns["OUT_DIR"].endswith("/test"))


class TestWorkerProvider(TmpCase):
    def _run_batch(self, handler, texts):
        import sys
        sys.path.insert(0, str(ROOT))
        ns = {"__name__": "__main__",
              "REPO_DIR": str(ROOT.parent),
              "OUT_DIR": str(self.tmp),
              "GAME_NAME": "Test",
              "FROM_LANG": "en",
              "TO_LANG": "vi",
              "TRANSLATE_AFTER_EXTRACT": False,
              "WORKER_URL": ""}
        exec(compile(_cell_source(), "colab_translate_cell", "exec"), ns)
        cls = ns["WorkerProvider"]
        calls = []

        def fake(req, timeout=None):
            calls.append(req)
            return handler(req)

        prov = cls("https://demo.workers.dev", urlopen=fake)
        out = prov.translate_batch(texts, "en", "vi")
        return prov, out, calls

    def test_batch_ok(self):
        def handler(req):
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["target"], "vi")
            self.assertEqual(body["source"], "en")
            self.assertTrue(req.full_url.startswith("https://demo.workers.dev/"))
            got = ["[w] " + t for t in body["texts"]]
            return _Resp(json.dumps(
                {"ok": True, "translated": got,
                 "provider": "workers-ai"}).encode("utf-8"))

        _prov, out, _calls = self._run_batch(
            handler, ["Hello", "Settings"])
        self.assertEqual(out, ["[w] Hello", "[w] Settings"])

    def test_quota_raises_enetwork(self):
        def handler(req):
            return _Resp(json.dumps(
                {"ok": False, "code": "quota_exceeded",
                 "month_used": 500000, "month_limit": 500000}
            ).encode("utf-8"))

        with self.assertRaises(PB3Error) as ctx:
            self._run_batch(handler, ["Hello"])
        self.assertEqual(ctx.exception.code, "E_NETWORK")

    def test_breaker_raises_enetwork(self):
        def handler(req):
            return _Resp(json.dumps(
                {"ok": False, "code": "breaker_open"}).encode("utf-8"))

        with self.assertRaises(PB3Error) as ctx:
            self._run_batch(handler, ["Hello"])
        self.assertEqual(ctx.exception.code, "E_NETWORK")

    def test_http_error_with_quota_body(self):
        def handler(req):
            body = json.dumps({"ok": False, "code": "quota_exceeded"})
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {},
                io.BytesIO(body.encode("utf-8")))

        with self.assertRaises(PB3Error) as ctx:
            self._run_batch(handler, ["Hello"])
        self.assertEqual(ctx.exception.code, "E_NETWORK")

    def test_healthy(self):
        import sys
        sys.path.insert(0, str(ROOT))
        ns = {"__name__": "__main__",
              "REPO_DIR": str(ROOT.parent),
              "OUT_DIR": str(self.tmp),
              "GAME_NAME": "Test",
              "FROM_LANG": "en",
              "TO_LANG": "vi",
              "TRANSLATE_AFTER_EXTRACT": False,
              "WORKER_URL": ""}
        exec(compile(_cell_source(), "colab_translate_cell", "exec"), ns)
        cls = ns["WorkerProvider"]

        def ok(req, timeout=None):
            return _Resp(json.dumps(
                {"ok": True, "service": "beartea-translate-proxy"}
            ).encode("utf-8"))

        def wrong(req, timeout=None):
            return _Resp(json.dumps({"ok": True, "service": "other"}
                                    ).encode("utf-8"))

        def boom(req, timeout=None):
            raise urllib.error.URLError("no net")

        self.assertTrue(cls("https://demo.workers.dev", urlopen=ok).healthy())
        self.assertFalse(cls("https://demo.workers.dev", urlopen=wrong).healthy())
        self.assertFalse(cls("https://demo.workers.dev", urlopen=boom).healthy())

    def test_long_text_without_gtx_is_none(self):
        long_text = "x" * 2001
        _prov, out, _calls = self._run_batch(
            lambda req: _Resp(json.dumps(
                {"ok": True, "translated": ["[w] short"],
                 "provider": "workers-ai"}).encode("utf-8")),
            ["short", long_text])
        self.assertEqual(out[0], "[w] short")
        self.assertIsNone(out[1])

    def test_long_text_uses_gtx_with_fallback(self):
        import sys
        sys.path.insert(0, str(ROOT))
        ns = {"__name__": "__main__",
              "REPO_DIR": str(ROOT.parent),
              "OUT_DIR": str(self.tmp),
              "GAME_NAME": "Test",
              "FROM_LANG": "en",
              "TO_LANG": "vi",
              "TRANSLATE_AFTER_EXTRACT": False,
              "WORKER_URL": ""}
        exec(compile(_cell_source(), "colab_translate_cell", "exec"), ns)
        cls = ns["WorkerProvider"]
        gtx = tr.FakeProvider()

        def handler(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(len(body["texts"]), 1)
            self.assertEqual(body["texts"][0], "short")
            return _Resp(json.dumps(
                {"ok": True, "translated": ["[w] short"],
                 "provider": "workers-ai"}).encode("utf-8"))

        prov = cls("https://demo.workers.dev", gtx=gtx, urlopen=handler)
        out = prov.translate_batch(["short", "y" * 2001], "en", "vi")
        self.assertEqual(out[0], "[w] short")
        self.assertEqual(out[1], "[vi] " + "y" * 2001)


def _exec_cell(tmp, game="Test", from_lang="en", to_lang="vi",
               translate=True, worker_url="", extra_files=None):
    """Exec cell dich trong namespace gia lap Colab; tra namespace."""
    import sys
    sys.path.insert(0, str(ROOT))
    out_dir = Path(tmp)
    src_file = {"en": "en.json", "zh": "zh-CN.json"}.get(from_lang,
                                                        from_lang + ".json")
    if extra_files is None:
        files = {src_file: {"Hello": "Hello",
                            "Settings": "Settings",
                            "Win": "Win the game {VAR1}!",
                            "Multi": "Line one\nLine two {KING}",
                            "Empty": ""}}
    else:
        files = extra_files
    for name, kv in files.items():
        (out_dir / name).write_text(
            json.dumps(kv, ensure_ascii=False, indent=2),
            encoding="utf-8")
    ns = {"__name__": "__main__",
          "REPO_DIR": str(ROOT.parent),
          "OUT_DIR": str(out_dir),
          "GAME_NAME": game,
          "FROM_LANG": from_lang,
          "TO_LANG": to_lang,
          "TRANSLATE_AFTER_EXTRACT": translate,
          "WORKER_URL": worker_url}
    exec(compile(_cell_source(), "colab_translate_cell", "exec"), ns)
    return ns, out_dir


class TestTranslateCellEndToEnd(TmpCase):
    def test_gtx_end_to_end(self):
        old = urllib.request.urlopen
        urllib.request.urlopen = _fake_gtx_urlopen
        try:
            _ns, out_dir = _exec_cell(self.tmp)
        finally:
            urllib.request.urlopen = old
        out = out_dir / "translated.vi.json"
        self.assertTrue(out.is_file(), "thieu file dich")
        got = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(list(got), ["Hello", "Settings", "Win", "Multi",
                                     "Empty"])
        self.assertEqual(got["Hello"], "[vi] Hello")
        self.assertEqual(got["Win"], "[vi] Win the game {VAR1}!")
        self.assertEqual(got["Multi"], "[vi] Line one\n[vi] Line two {KING}")
        self.assertEqual(got["Empty"], "")
        src = json.loads((out_dir / "en.json").read_text(encoding="utf-8"))
        gate = check_placeholders(src, got)
        self.assertEqual(gate["status"], "pass", gate)
        check = exp.verify_export_bytes(out.read_bytes(), got)
        self.assertTrue(check["ok"], check)
        prov = json.loads((out_dir / "translated.vi.provenance.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(prov["provider"], "google")
        self.assertEqual(prov["translationFile"], "translated.vi.json")
        self.assertEqual(prov["keys"], 5)

    def test_worker_end_to_end(self):
        def fake(req, timeout=None):
            if req.data is None:
                return _Resp(json.dumps(
                    {"ok": True, "service": "beartea-translate-proxy"}
                ).encode("utf-8"))
            body = json.loads(req.data.decode("utf-8"))
            got = ["[w] " + t for t in body["texts"]]
            return _Resp(json.dumps(
                {"ok": True, "translated": got,
                 "provider": "workers-ai"}).encode("utf-8"))

        old = urllib.request.urlopen
        urllib.request.urlopen = fake
        try:
            _ns, out_dir = _exec_cell(
                self.tmp, worker_url="https://demo.workers.dev/")
        finally:
            urllib.request.urlopen = old
        got = json.loads((out_dir / "translated.vi.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(got["Hello"], "[w] Hello")
        prov = json.loads((out_dir / "translated.vi.provenance.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(prov["provider"], "worker")

    def test_skip_when_output_exists(self):
        src = {"Hello": "Hello", "Settings": "Settings"}
        (self.tmp / "en.json").write_text(
            json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
        old_out = {"Hello": "Xin chào", "Settings": "Cài đặt"}
        (self.tmp / "translated.vi.json").write_text(
            json.dumps(old_out, ensure_ascii=False, indent=2),
            encoding="utf-8")
        old = urllib.request.urlopen
        urllib.request.urlopen = _fake_gtx_urlopen
        try:
            _ns, out_dir = _exec_cell(self.tmp, extra_files={
                "en.json": src,
                "translated.vi.json": old_out})
        finally:
            urllib.request.urlopen = old
        got = json.loads((out_dir / "translated.vi.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(got, old_out, "file dich cu phai giu nguyen")

    def test_missing_source_file(self):
        old = urllib.request.urlopen
        urllib.request.urlopen = _fake_gtx_urlopen
        try:
            _ns, out_dir = _exec_cell(self.tmp, extra_files={})
        finally:
            urllib.request.urlopen = old
        self.assertFalse((out_dir / "translated.vi.json").exists())

    def test_disabled_toggle(self):
        old = urllib.request.urlopen
        urllib.request.urlopen = _fake_gtx_urlopen
        try:
            _ns, out_dir = _exec_cell(self.tmp, translate=False)
        finally:
            urllib.request.urlopen = old
        self.assertFalse((out_dir / "translated.vi.json").exists())


class TestFullFileMock(TmpCase):
    def test_full_2252_keys_fake_provider(self):
        import sys
        sys.path.insert(0, str(ROOT))
        ns = {"__name__": "__main__",
              "REPO_DIR": str(ROOT.parent),
              "OUT_DIR": str(self.tmp),
              "GAME_NAME": "Test",
              "FROM_LANG": "en",
              "TO_LANG": "vi",
              "TRANSLATE_AFTER_EXTRACT": False,
              "WORKER_URL": ""}
        exec(compile(_cell_source(), "colab_translate_cell", "exec"), ns)
        kv = {}
        for i in range(2252):
            if i % 10 == 0:
                kv["Key_%04d" % i] = "Win the game {VAR1}!"
            elif i % 10 == 1:
                kv["Key_%04d" % i] = "Line one\nLine two {KING}"
            elif i % 10 == 2:
                kv["Key_%04d" % i] = ""
            else:
                kv["Key_%04d" % i] = "Sample text number %d" % i
        src_path = self.tmp / "en.json"
        out_path = self.tmp / "translated.vi.json"
        src_path.write_text(json.dumps(kv, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        res = ns["translate_json_file"](
            str(src_path), str(out_path), source_lang="en", target_lang="vi",
            provider=tr.FakeProvider(),
            fallback=tr.OfflineGlossaryProvider(), credit=True)
        self.assertEqual(res.total, 2252)
        self.assertEqual(res.untranslated, 0, res.untranslated_keys[:5])
        got = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(list(got), list(kv), "thu tu key phai giu nguyen")
        gate = check_placeholders(kv, got)
        self.assertEqual(gate["status"], "pass", gate["failures"][:3])
        check = exp.verify_export_bytes(out_path.read_bytes(), got)
        self.assertTrue(check["ok"], check)


if __name__ == "__main__":
    unittest.main()
