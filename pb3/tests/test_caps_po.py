"""Test nang tran kich thuoc + duong ghi lai .po (caps-po).

- Tran moi: entry 256 MiB, plan 32 MiB, stream_entry_bytes 32 MiB;
  manifest po_mo 32 MiB.
- Streaming bundle trong ZIP: _expand_unity_stream_entry (spool-to-temp
  + stream) khop _expand_unity tren fixture; Tom data.unity3d 78 MB mo
  duoc (do RSS).
- read_extracted chunked: file dich ~6,5 MB (87.800 key, gia lap DST)
  nap duoc theo chunk, ke ca ky tu ngoai BMP.
- po_write: dry-run + apply tren zip long nhau tong hop, file .po tran,
  tu choi confirm thieu / .mo / vpath qua sau; tren DST that (sua 2
  msgstr vietnamese.po, apply, doc lai dung gia tri moi).
- DST extract vi: ra vi.json ~87,8k key (truoc: 0 file).

Fixture TU DUNG (zip/.po tong hop bang zipfile); khong dung mang.
Test IPA that (@slow) skip neu thieu file trong ~/workspace/test-ipas/.
- fix-two-reds (2026-11-18): _enumerate_units bo qua entry > tran doc
  tron RAM (khong refused gia); _resolve_vpath giu E_TOO_LARGE; raw
  .assets > tran stream khong di streaming (fallback doc tron).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import bridge  # noqa: E402

bridge.WEB_ROOT = str(ROOT)
json.loads(bridge.boot())

from core.errors import PB3Error  # noqa: E402
from core.limits import Limits  # noqa: E402
from plugins.po_mo.plugin import PoMoPlugin  # noqa: E402
from sdk import batch as _batch  # noqa: E402
from sdk import discovery as disc  # noqa: E402
from sdk import extraction as ext  # noqa: E402
from sdk import po_write as _pw  # noqa: E402
from sdk import rebuild as _rb  # noqa: E402
from sdk import unity as u  # noqa: E402
from sdk.containers import NestedVFS  # noqa: E402
from services import unity_tool as _ut  # noqa: E402
from tests.helpers import pytest  # noqa: E402 - marker needs_fixture
from tests.tiering import slow  # noqa: E402

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)

DST_IPA = Path.home() / "workspace" / "test-ipas" / "dont-starve-together-2.1.0-vh.ipa"
TOM_IPA = Path.home() / "workspace" / "test-ipas" / "My_Talking_Tom_v26.5.1_Decrypted.ipa"
TOM_ENTRY = ("Payload/MyTalkingTom-row.app/Frameworks/UnityFramework.framework/"
             "Data/data.unity3d")
DST_PO_VPATH = ("Payload/dontstarvetogether.app/data/databundles/scripts.zip::"
                "scripts/languages/vietnamese.po")

SAMPLE_PO = (b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
             b'msgid "hello"\nmsgstr "Hello"\n\n'
             b'msgid "bye"\nmsgstr "Bye"\n\n'
             b'msgid "play"\nmsgstr "Play"\n')


def _rss_mb():
    # RSS đỉnh (MB). Pyodide không có module resource -> 0.0 (test đo RSS
    # chỉ có ý nghĩa trên CPython; test @slow cũng bị loại khỏi tier smoke).
    try:
        import resource
    except ImportError:
        return 0.0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _synthetic_nested_zip(tmp, name="game.zip"):
    """outer.zip > inner.zip > dir/test.po (3 entry, 2 can sua)."""
    path = os.path.join(tmp, name)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("dir/test.po", SAMPLE_PO)
        zf.writestr("dir/other.txt", b"khong phai .po")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inner.zip", inner.getvalue())
        zf.writestr("readme.txt", b"game")
    return path


def _read_po_from_zip(path, po_vpath):
    """Doc bytes .po la tu file (co the long nhau) qua NestedVFS."""
    root = disc.open_source_vfs(path, LIM)
    nested = NestedVFS(root, limits=LIM)
    return bytes(nested.read(po_vpath, None))


def _parse_po_bytes(data):
    plug = PoMoPlugin()
    from sdk.api import PluginContext
    from sdk.types import Candidate
    from sdk.vfs import MemoryVFS
    ctx = PluginContext(MemoryVFS({"t.po": data})).for_plugin(plug.manifest)
    return plug.parse(ctx, Candidate.for_path("t.po", plug.id, 1.0))


class CapsPoTest(unittest.TestCase):
    def test_caps_defaults(self):
        # caps-po: entry 64 MiB -> 256 MiB; plan 8 MiB -> 32 MiB;
        # moi stream_entry_bytes 32 MiB; manifest po_mo 8 MiB -> 32 MiB.
        self.assertEqual(LIM.max_entry_bytes, 256 * 1024 * 1024)
        self.assertEqual(LIM.plan_max_entry_bytes, 32 * 1024 * 1024)
        self.assertEqual(LIM.stream_entry_bytes, 32 * 1024 * 1024)
        self.assertLess(LIM.stream_entry_bytes, LIM.max_entry_bytes)
        self.assertEqual(PoMoPlugin.MANIFEST["limits"]["max_entry_bytes"],
                         32 * 1024 * 1024)
        self.assertEqual(PoMoPlugin.MANIFEST["version"], "1.1.0")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_enumerate_units_skips_entry_over_memory_read_cap(self):
        # [HỒI QUY] sharedassets0.assets 9K (147 MB) bi refused=1 sau caps-po:
        # liet ke nhan entry toi 256 MiB nhung duong ep chi doc tron toi
        # 128 MiB RAM. Entry vuot tran RAM phai duoc bo qua o muc liet ke.
        data = (ROOT / "tests/fixtures/unity/resources.assets").read_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Payload/Game.app/Data/big.assets", data)
        raw = buf.getvalue()
        small = Limits(max_memory_read_bytes=len(data) - 1)
        with self.assertRaises(PB3Error) as ctx:
            _rb._enumerate_units(raw, BE, small)
        self.assertEqual(ctx.exception.code, "E_INPUT_NOT_FOUND")
        got = _rb._enumerate_units(raw, BE, LIM)
        self.assertEqual(got, [("Payload/Game.app/Data/big.assets", "assets")])

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_resolve_vpath_oversize_keeps_e_too_large(self):
        # (a) hoi quy fix-two-reds (2026-11-18): entry vuot tran doc tron
        # vao RAM phai giu nguyen ma E_TOO_LARGE, khong duoc doi thanh
        # E_CONFIG_INVALID ("khong tim thay ... trong container/bundle")
        # o ca hai _resolve_vpath (batch + services/unity_tool).
        data = (ROOT / "tests/fixtures/unity/resources.assets").read_bytes()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Payload/Game.app/Data/big.assets", data)
        raw = buf.getvalue()
        small = Limits(max_memory_read_bytes=len(data) - 1)
        for fn in (_batch._sdk_resolve_vpath, _ut._resolve_vpath):
            with self.assertRaises(PB3Error) as ctx:
                fn(raw, "Payload/Game.app/Data/big.assets", BE, small)
            self.assertEqual(ctx.exception.code, "E_TOO_LARGE")

    @pytest.mark.needs_fixture("unity/resources.assets")
    def test_build_layers_raw_assets_over_stream_cap_not_skipped(self):
        # (b) hoi quy fix-two-reds (2026-11-18): raw .assets (SerializedFile)
        # > stream_entry_bytes KHONG duoc di duong streaming (chi biet doc
        # bundle UnityFS) — phai fallback ve duong cu (doc tron +
        # _expand_unity) va van cho ra 2 TextAsset (greeting, longtext).
        # Truoc fix: _expand_unity_stream_entry -> E_UNSUPPORTED_FORMAT ->
        # ca file bi skip, mat sach du lieu (PvZ: 521 -> 11 key).
        small = Limits(stream_entry_bytes=64)
        with tempfile.TemporaryDirectory() as tmp:
            zpath = os.path.join(tmp, "game.zip")
            with zipfile.ZipFile(zpath, "w") as zf:
                zf.write(str(ROOT / "tests/fixtures/unity/resources.assets"),
                         "Payload/Game.app/Data/resources.assets")
            root = disc.open_source_vfs(zpath, small)
            inv = disc.inventory(root, small)
            layers, _shared = ext._build_layers(root, inv, small, BE,
                                                lambda m: None)
        virtual = {}
        for layer in layers:
            if layer.kind == "virtual":
                virtual.update(layer.origin_map)
        self.assertEqual(sorted(virtual), [
            "Payload/Game.app/Data/resources.assets::greeting",
            "Payload/Game.app/Data/resources.assets::longtext",
        ])
        self.assertEqual(set(virtual.values()), {"text_asset"})

    def test_expand_unity_stream_entry_matches_expand_unity(self):
        # Spool-to-temp + stream cho ket qua giong het duong cu (ca notes).
        for fixture in ("tests/fixtures/unity/sample_v9.unity3d",
                        "tests/fixtures/unity/nested_v8.ipa"):
            fpath = str(ROOT / fixture)
            root = disc.open_source_vfs(fpath, LIM)
            inv = disc.inventory(root, LIM)
            nested = NestedVFS(root, limits=LIM)
            for e in inv:
                if e.kind != "unity":
                    continue
                got_s, notes_s, mb_s = ext._expand_unity_stream_entry(
                    nested, e.path, BE, LIM)
                data = bytes(nested.read(e.path, None))
                got, notes, mb = ext._expand_unity(data, e.path, BE, LIM)
                self.assertEqual(sorted(got_s), sorted(got),
                                 fixture)
                self.assertEqual(notes_s, notes, fixture)
                self.assertEqual(mb_s, mb, fixture)

    def test_po_write_dry_run_nested_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _synthetic_nested_zip(tmp)
            before = _pw.sha256_file(src)
            res = _pw.po_write(src, "inner.zip::dir/test.po",
                               {"hello": "Xin chao", "bye": "Bye",
                                "nope": "Khong co"},
                               dry_run=True, confirm=False)
            self.assertTrue(res["ok"])
            self.assertTrue(res["dry_run"])
            self.assertEqual(res["total_entries"], 3)
            self.assertEqual(res["changed"], 1)
            self.assertEqual(res["changed_keys"], ["hello"])
            self.assertEqual(res["unchanged"], 1)
            self.assertEqual(res["unknown"], ["nope"])
            self.assertIsNone(res["output"])
            # Dry-run khong ghi gi: khong co file -po-mod, goc nguyen sha.
            self.assertEqual(_pw.sha256_file(src), before)
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "game-po-mod.zip")))

    def test_po_write_apply_nested_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _synthetic_nested_zip(tmp)
            before = _pw.sha256_file(src)
            res = _pw.po_write(src, "inner.zip::dir/test.po",
                               {"hello": "Xin chao", "play": "Choi"},
                               dry_run=False, confirm=True)
            self.assertTrue(res["ok"])
            self.assertFalse(res["dry_run"])
            self.assertEqual(res["changed"], 2)
            out = res["output"]
            self.assertTrue(out.endswith("game-po-mod.zip"))
            self.assertTrue(os.path.isfile(out))
            self.assertEqual(_pw.sha256_file(src), before)
            # Doc lai .po trong output: gia tri moi + key khac giu nguyen.
            data = _read_po_from_zip(out, "inner.zip::dir/test.po")
            table = _parse_po_bytes(data)
            vals = {e.key: e.value for e in table.entries}
            self.assertEqual(vals, {"hello": "Xin chao", "bye": "Bye",
                                    "play": "Choi"})
            self.assertEqual([e.key for e in table.entries],
                             ["hello", "bye", "play"])
            # Thu tu entry zip giu nguyen.
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(zf.namelist(), ["inner.zip", "readme.txt"])
            with zipfile.ZipFile(src) as zf:
                self.assertEqual(zf.namelist(), ["inner.zip", "readme.txt"])

    def test_po_write_bare_po(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "test.po")
            with open(src, "wb") as fh:
                fh.write(SAMPLE_PO)
            res = _pw.po_write(src, "test.po", {"bye": "Tam biet"},
                               dry_run=False, confirm=True)
            self.assertTrue(res["ok"])
            self.assertTrue(res["output"].endswith("test-po-mod.po"))
            table = _parse_po_bytes(open(res["output"], "rb").read())
            vals = {e.key: e.value for e in table.entries}
            self.assertEqual(vals["bye"], "Tam biet")
            self.assertEqual(vals["hello"], "Hello")
            self.assertEqual(open(src, "rb").read(), SAMPLE_PO)

    def test_po_write_requires_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _synthetic_nested_zip(tmp)
            with self.assertRaises(PB3Error) as cm:
                _pw.po_write(src, "inner.zip::dir/test.po",
                             {"hello": "Xin chao"},
                             dry_run=False, confirm=False)
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "game-po-mod.zip")))

    def test_po_write_rejects_mo_deep_vpath_and_unknown_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _synthetic_nested_zip(tmp)
            with self.assertRaises(PB3Error) as cm:
                _pw.po_write(src, "inner.zip::dir/test.mo", {"a": "b"})
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
            with self.assertRaises(PB3Error) as cm:
                _pw.po_write(src, "a::b::c::d::e.po", {"a": "b"})
            self.assertEqual(cm.exception.code, "E_CONFIG_INVALID")
            # Chi key la: changed=0, apply khong ghi file.
            res = _pw.po_write(src, "inner.zip::dir/test.po",
                               {"nope": "Khong co"},
                               dry_run=False, confirm=True)
            self.assertEqual(res["changed"], 0)
            self.assertIsNone(res["output"])
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "game-po-mod.zip")))

    def test_po_write_bridge_op_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _synthetic_nested_zip(tmp)
            out = json.loads(bridge.po_apply(
                src, "inner.zip::dir/test.po",
                json.dumps({"hello": "Xin chao"}, ensure_ascii=False)))
            self.assertTrue(out["ok"])
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["changed"], 1)
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "game-po-mod.zip")))

    def test_read_extracted_chunked(self):
        # File dich gia lap DST: 87.800 key, ~6,5 MB, co ca ky tu ngoai BMP.
        big = {"k%06d" % i: "value number %d with some Vietnamese text "
                            "Tieng Viet" % i for i in range(87800)}
        big["emoji"] = "emoji \U0001F600\U0001F600 ket thuc"
        outdir = "/tmp/pb3out"
        os.makedirs(outdir, exist_ok=True)
        fp = os.path.join(outdir, "vi-big-test.json")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(big, fh, ensure_ascii=False)
        self.addCleanup(lambda: os.path.exists(fp) and os.unlink(fp))
        raw = open(fp, encoding="utf-8").read()
        # Che do cu: > 4 MB bi chan ro.
        old = json.loads(bridge.read_extracted("vi-big-test.json"))
        self.assertFalse(old["ok"])
        self.assertIn("4.0 MB", old["error"])
        # Che do chunk: ghep lai dung toan van (dung `end` server tra ve).
        parts, offset = [], 0
        for _ in range(100):
            chunk = json.loads(bridge.read_extracted(
                "vi-big-test.json", offset, 1024 * 1024))
            self.assertTrue(chunk["ok"], chunk)
            self.assertTrue(chunk["chunked"])
            parts.append(chunk["text"])
            self.assertEqual(chunk["offset"], offset)
            if chunk["done"]:
                break
            offset = chunk["end"]
        else:
            self.fail("chunked khong done sau 100 chunk")
        self.assertEqual("".join(parts), raw)
        self.assertEqual(json.loads("".join(parts)), big)
        # Offset vuot EOF: done ngay, text rong.
        tail = json.loads(bridge.read_extracted(
            "vi-big-test.json", len(raw) + 10, 1024))
        self.assertTrue(tail["ok"])
        self.assertEqual(tail["text"], "")
        self.assertTrue(tail["done"])
        # Offset am: loi ro.
        bad = json.loads(bridge.read_extracted("vi-big-test.json", -1, 10))
        self.assertFalse(bad["ok"])
        # File nho: 1 chunk la xong.
        small = os.path.join(outdir, "small-test.json")
        with open(small, "w", encoding="utf-8") as fh:
            json.dump({"a": "1"}, fh)
        self.addCleanup(lambda: os.path.exists(small) and os.unlink(small))
        one = json.loads(bridge.read_extracted("small-test.json", 0, 1024 * 1024))
        self.assertTrue(one["ok"])
        self.assertTrue(one["done"])
        self.assertEqual(json.loads(one["text"]), {"a": "1"})

    @slow
    def test_tom_data_unity3d_streams(self):
        # [ĐÃ ĐO THẬT] Tom data.unity3d 78 MB trong IPA: mo bang spool +
        # stream (khong skip vi > 64 MiB nhu truoc), do RSS tang them.
        if not TOM_IPA.exists():
            raise unittest.SkipTest("thieu IPA Tom: %s" % TOM_IPA)
        root = disc.open_source_vfs(str(TOM_IPA), LIM)
        nested = NestedVFS(root, limits=LIM)
        rss_before = _rss_mb()
        t0 = time.time()
        got, notes, mb = ext._expand_unity_stream_entry(
            nested, TOM_ENTRY, BE, LIM)
        dt = time.time() - t0
        rss_after = _rss_mb()
        joined = " ".join(notes)
        self.assertIn("SerializedFile", joined)
        # Bundle Tom khong co TextAsset ngon ngu (da biet tu audit) nhung
        # phai mo + parse duoc cac SerializedFile, khong phai skip.
        self.assertNotIn("giới hạn entry", joined)
        print("\n[Tom stream] %.1fs, RSS %.1f -> %.1f MB (+%.1f MB)"
              % (dt, rss_before, rss_after, rss_after - rss_before))
        # Streaming khong duoc doi them qua ~512 MB RAM (duong cu om ca
        # entry 78 MB + payload giai nen 171 MB + ban copy).
        self.assertLess(rss_after - rss_before, 512.0)

    @slow
    def test_dst_extract_vi(self):
        # [ĐÃ ĐO THẬT] DST extract locale vi, merge: truoc 0 file (ca .po
        # bi loai E_TOO_LARGE); nay ra vi.json ~87,8k key.
        if not DST_IPA.exists():
            raise unittest.SkipTest("thieu IPA DST: %s" % DST_IPA)
        t0 = time.time()
        res = json.loads(bridge.extract(str(DST_IPA), locales=["vi"],
                                        merge_one_file=True))
        dt = time.time() - t0
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res.get("errors"), [])
        vi = [f for f in res.get("files", [])
              if (f.get("exported") or "").endswith("vi.json")]
        self.assertEqual(len(vi), 1, res.get("files"))
        n_keys = vi[0].get("entries") or vi[0].get("keys") or 0
        if not n_keys:
            # Dem truc tiep tu file da xuat neu report khong ghi so key.
            data = json.load(open("/tmp/pb3out/vi.json", encoding="utf-8"))
            n_keys = len(data)
        print("\n[DST extract vi] %.1fs, vi.json %d key, total_entries=%s"
              % (dt, n_keys, res.get("total_entries")))
        self.assertGreater(n_keys, 87000)
        self.assertLess(n_keys, 89000)
        self.assertGreater(res.get("total_entries") or 0, 80000)
        # File dich ~9 MB nap duoc qua read_extracted chunked.
        parts, offset = [], 0
        for _ in range(100):
            chunk = json.loads(bridge.read_extracted(
                "vi.json", offset, 1024 * 1024))
            self.assertTrue(chunk["ok"], chunk)
            parts.append(chunk["text"])
            if chunk["done"]:
                break
            offset = chunk["end"]
        else:
            self.fail("vi.json chunked khong done sau 100 chunk")
        data = json.loads("".join(parts))
        self.assertEqual(len(data), n_keys)
        self.addCleanup(lambda: os.path.exists("/tmp/pb3out/vi.json")
                        and os.unlink("/tmp/pb3out/vi.json"))

    @slow
    def test_po_write_dst_real(self):
        # [ĐÃ ĐO THẬT] Sua 2 msgstr vietnamese.po that trong DST, apply,
        # doc lai dung gia tri moi; file goc sha khong doi.
        if not DST_IPA.exists():
            raise unittest.SkipTest("thieu IPA DST: %s" % DST_IPA)
        out_path = str(DST_IPA).replace(".ipa", "-po-mod.ipa")
        self.addCleanup(lambda: os.path.exists(out_path)
                        and os.unlink(out_path))
        root = disc.open_source_vfs(str(DST_IPA), LIM)
        nested = NestedVFS(root, limits=LIM)
        po_bytes = bytes(nested.read(DST_PO_VPATH, None))
        table = _parse_po_bytes(po_bytes)
        picks = [(e.key, e.value) for e in table.entries
                 if e.value][:2]
        self.assertEqual(len(picks), 2)
        translations = {k: v + " [SUA KIEM TRA]" for k, v in picks}
        preview = _pw.po_write(str(DST_IPA), DST_PO_VPATH, translations,
                               dry_run=True)
        self.assertEqual(preview["changed"], 2)
        self.assertEqual(preview["total_entries"], 87828)
        before = _pw.sha256_file(str(DST_IPA))
        t0 = time.time()
        res = _pw.po_write(str(DST_IPA), DST_PO_VPATH, translations,
                           dry_run=False, confirm=True)
        dt = time.time() - t0
        self.assertTrue(res["ok"])
        self.assertEqual(res["output"], out_path)
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(_pw.sha256_file(str(DST_IPA)), before)
        # Doc lai .po trong output.
        new_bytes = _read_po_from_zip(out_path, DST_PO_VPATH)
        new_table = _parse_po_bytes(new_bytes)
        new_vals = {e.key: e.value for e in new_table.entries}
        self.assertEqual(len(new_table.entries), 87828)
        for k, v in translations.items():
            self.assertEqual(new_vals[k], v)
        old_vals = {e.key: e.value for e in table.entries}
        for k, v in old_vals.items():
            if k not in translations:
                self.assertEqual(new_vals[k], v)
        print("\n[DST po_write] apply %.1fs, output %d B"
              % (dt, os.path.getsize(out_path)))

    # Tran timeout rieng cho test nay (run_tests.py: mac dinh 180s):
    # [ĐÃ ĐO THẬT] chay rieng 222,5s (apply 168,1s + doc/parse lai .po
    # 87.828 entry 2 lan + doi chieu toan bo) tren DST that 1,69 GB —
    # chi cham, khong phai loi that — nen 180s khong du. Dat 1800s
    # (cung muc tien le test_mod_job.py::test_round_trip_* dung cho
    # round-trip PB3 ~16 phut); tran la gioi han tren, khong phai muc
    # tieu thoi gian. Chi ap cho method nay, khong nang tran chung.
    test_po_write_dst_real.timeout_s = 1800


if __name__ == "__main__":
    unittest.main()
