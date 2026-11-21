"""Test F4: chu con sot — scene/prefab (TMP/UI Text) + chu trong anh.

- Scene: file levelN khong duoi + .unity truoc day inventory xep la
  "file" nen pipeline bo qua ([DA DO THAT] tren 9K that: level0 co
  87 MonoBehaviour, chua "Click to skip"/"No thanks"). Nay phai la
  kind "unity" va chu scene phai vao duong MB generic.
- TMP/UI Text: field m_text (TextMeshPro) / m_Text (UI Text) co type
  tree phai duoc dem rieng (ui_text_keys); duong ghi generic san co
  (scan[i] / field path) phai va duoc chu nay.
- Texture: Texture2D chi liet ke ten de canh bao (khong OCR, khong
  ve lai); bao cao co muc texture_text + scene_text.

Tat ca dung fixture tong hop ([FIXTURE]); game that kiem bang tay
o AUTOPILOT (9K level0 + sharedassets0: 346 Texture2D).
"""
from __future__ import annotations

import os
import struct
import zipfile

import pytest

from core.limits import Limits
from sdk import discovery as disc
from sdk import extraction as ext
from sdk import unity as u
from tests.fixtures.unity.build_fixtures import (
    aligned_string, build_serialized_file)

LIM = Limits()
BE = u.UnityBackend("AUTO", LIM)


def _astr(buf, text):
    raw = text.encode("utf-8")
    buf += struct.pack("<i", len(raw)) + raw
    buf += b"\x00" * ((-len(buf)) % 4)


def _generic_mb_bytes(name, strings, trailing=b""):
    """Bytes object MonoBehaviour generic KHONG type tree."""
    buf = bytearray(28)
    _astr(buf, name)
    for s in strings:
        _astr(buf, s)
    buf += trailing
    return bytes(buf)


def _level_sf():
    """SerializedFile gia scene level0: 1 MB chua chu UI."""
    data = build_serialized_file(
        version=17,
        objects=[(3003, 0, _generic_mb_bytes(
            "TitleText", ("Play Now", "Settings")))],
        types=[(114, b"\x00" * 16)], enable_type_tree=False)
    return data


def _texture_sf():
    """SerializedFile gia .assets: 2 Texture2D + 1 TextAsset."""
    from tests.fixtures.unity.build_fixtures import text_asset_data
    tex1 = aligned_string("Button_BG") + b"\x00" * 32
    tex2 = aligned_string("Title_Logo") + b"\x00" * 32
    data = build_serialized_file(
        version=17,
        objects=[(11, 0, tex1), (12, 0, tex2),
                 (21, 1, text_asset_data("greeting", "hello"))],
        types=[(28, None), (49, None)], enable_type_tree=False)
    return data


def _make_ipa(tmp_path, files):
    zpath = os.path.join(str(tmp_path), "game.ipa")
    with zipfile.ZipFile(zpath, "w") as zf:
        for name, data in files:
            zf.writestr(name, data)
    return zpath


# ------------------------------------------------------- inventory
def test_level_file_is_unity_kind(tmp_path):
    zpath = _make_ipa(tmp_path, [
        ("Payload/Game.app/Data/level0", _level_sf()),
        ("Payload/Game.app/Data/level1.unity", _level_sf()),
        ("Payload/Game.app/Data/readme.txt", b"hello"),
    ])
    root = disc.open_source_vfs(zpath, LIM)
    inv = disc.inventory(root, LIM)
    by_path = {e.path: e.kind for e in inv}
    assert by_path["Payload/Game.app/Data/level0"] == "unity"
    assert by_path["Payload/Game.app/Data/level1.unity"] == "unity"
    assert by_path["Payload/Game.app/Data/readme.txt"] == "file"


def test_is_scene_path():
    assert ext._is_scene_path("Payload/Game.app/Data/level0")
    assert ext._is_scene_path("x/level12")
    assert ext._is_scene_path("x/scene.unity")
    assert ext._is_scene_path("x/SCENE.UNITY")
    assert not ext._is_scene_path("x/sharedassets0.assets")
    assert not ext._is_scene_path("x/level0.resS")
    assert not ext._is_scene_path("x/level.txt")


# ------------------------------------------------------- TMP / UI Text
def _nested_typetree_blob(spec, version=17):
    """Blob type tree v17 tu spec long nhau (copy tu test_mb_write)."""
    flat = []

    def walk(node, level, index):
        t, n, bs, children = node
        flat.append((level, t, n, bs, index))
        for i, c in enumerate(children):
            walk(c, level + 1, i)

    walk(spec, 0, 0)
    names = []
    for _lv, t, n, _bs, _ix in flat:
        for s in (t, n):
            if s not in names:
                names.append(s)
    strings = b"".join(s.encode("utf-8") + b"\x00" for s in names)

    def off(s):
        return strings.index(s.encode("utf-8"))

    body = b"".join(struct.pack("<hBBIIiii", 1, lv, 0, off(t), off(n), bs, ix, 0)
                    for lv, t, n, bs, ix in flat)
    return struct.pack("<ii", len(flat), len(strings)) + body + strings


def _array_node(elem_type, elem_byte_size, elem_children=()):
    return ("Array", "Array", -1, [
        ("int", "common_795", 4, []),
        (elem_type, "common_106", elem_byte_size, list(elem_children)),
    ])


def _string_node(name):
    return ("string", name, -1, [_array_node("char", 1)])


def _pptr_node(name, target):
    return ("PPtr<%s>" % target, name, -1, [
        ("int", "m_FileID", 4, []),
        ("long", "m_PathID", 8, []),
    ])


def _tmp_spec():
    """Type tree MonoBehaviour TMP: m_text + m_fontSize (chi can thiet)."""
    spec = ("MonoBehaviour", "MonoBehaviour", -1, [
        _pptr_node("m_GameObject", "GameObject"),
        ("bool", "m_Enabled", 1, []),
        _pptr_node("m_Script", "MonoScript"),
        _string_node("m_Name"),
        _string_node("m_text"),
        ("float", "m_fontSize", 4, []),
    ])
    return _nested_typetree_blob(spec, version=17)


def _tmp_object_bytes(name, text, font_size=24.0):
    w = u._Writer("<")
    w.pack("i", 0)
    w.pack("q", 0)          # m_GameObject PPtr
    w.write(bytes([1]))     # m_Enabled
    w.align(4)
    w.pack("i", 0)
    w.pack("q", 0)          # m_Script PPtr
    w.align(4)
    w.aligned_string(name)  # m_Name
    w.align(4)
    w.aligned_string(text)  # m_text
    w.align(4)
    w.pack("f", font_size)  # m_fontSize
    return w.bytes()


def _tmp_sf():
    blob = _tmp_spec()
    data = build_serialized_file(
        version=17,
        objects=[(4001, 0, _tmp_object_bytes("TitleText", "Play Now"))],
        types=[(114, b"\x00" * 16)], enable_type_tree=True,
        typetree_blobs={0: blob})
    return u.parse_serialized(data, LIM, where="tmp-fixture")


def test_tmp_m_text_captured_and_counted():
    sf = _tmp_sf()
    items = [it for it in sf.monobehaviour_texts()
             if it.get("kind") == "generic"]
    assert len(items) == 1
    assert items[0]["fields"] == {"m_text": "Play Now"}
    res = ext._mb_generic_kv(items, split_languages=False)
    assert res["kv"] == {"TitleText.m_text": "Play Now"}
    assert res["stats"]["ui_text_keys"] == 1
    assert res["stats"]["scene_keys"] == 0  # chua gan co scene


def test_tmp_m_text_write_roundtrip_via_generic_targets():
    sf = _tmp_sf()
    targets = [t for t in ext.generic_write_targets(sf)
               if t["key"] == "TitleText.m_text"]
    assert len(targets) == 1
    assert targets[0]["field_path"] == "m_text"
    assert targets[0]["before"] == "Play Now"
    new, applied = u.write_monobehaviour_object_bytes(
        sf, 4001, [{"field_path": "m_text", "before": "Play Now",
                    "after": "Choi ngay"}])
    assert [a["field_path"] for a in applied] == ["m_text"]
    sf2 = u.parse_serialized(
        build_serialized_file(
            version=17,
            objects=[(4001, 0, new)],
            types=[(114, b"\x00" * 16)], enable_type_tree=True,
            typetree_blobs={0: _tmp_spec()}),
        LIM, where="tmp-patched")
    items = [it for it in sf2.monobehaviour_texts()
             if it.get("kind") == "generic"]
    assert items[0]["fields"] == {"m_text": "Choi ngay"}


def test_scene_scan_strings_write_roundtrip():
    sf = u.parse_serialized(_level_sf(), LIM, where="level0")
    items = [it for it in sf.monobehaviour_texts()
             if it.get("kind") == "generic"]
    assert items[0]["strings"] == ["Play Now", "Settings"]
    targets = ext.generic_write_targets(sf)
    by_key = {t["key"]: t for t in targets}
    assert by_key["TitleText.s1"]["field_path"] == "scan[0]"
    raw = sf.object_bytes(3003)
    new = u.write_scanned_monobehaviour_bytes(raw, [
        {"field_path": "scan[0]", "before": "Play Now",
         "after": "Choi ngay"}])
    _name, strings = u.scan_monobehaviour_strings(new, "<")
    assert strings[0] == "Choi ngay"
    assert strings[1] == "Settings"


# ------------------------------------------------------- texture
def test_textures_listed_by_name():
    sf = u.parse_serialized(_texture_sf(), LIM, where="tex-fixture")
    assert sf.textures() == [(11, "Button_BG"), (12, "Title_Logo")]
    out, mb, _notes, _n_ta, _n_mb, _n_s = ext._expand_serialized(
        sf, "sharedassets0.assets", "Payload/Game.app/Data/sharedassets0.assets")
    assert mb["textures"] == [("sharedassets0.assets", 11, "Button_BG"),
                              ("sharedassets0.assets", 12, "Title_Logo")]
    assert "greeting" in str(out.keys())


def test_build_layers_scene_and_textures(tmp_path):
    zpath = _make_ipa(tmp_path, [
        ("Payload/Game.app/Data/level0", _level_sf()),
        ("Payload/Game.app/Data/sharedassets0.assets", _texture_sf()),
    ])
    root = disc.open_source_vfs(zpath, LIM)
    inv = disc.inventory(root, LIM)
    assert [e.kind for e in inv if "level0" in e.path] == ["unity"]
    extra: dict = {}
    layers, _shared = ext._build_layers(root, inv, LIM, BE, lambda m: None,
                                        extra_out=extra)
    assert extra["scene_text"]["units"] == 1
    assert extra["scene_text"]["keys"] == 2
    assert extra["scene_text"]["items"] == 1
    assert len(extra["textures"]) == 2
    assert sorted(t[2] for t in extra["textures"]) == ["Button_BG", "Title_Logo"]
    virtual = {}
    for layer in layers:
        if layer.kind == "virtual":
            virtual.update(layer.origin_map)
    assert any("level0" in vp and "monobehaviour" in vp for vp in virtual)


def test_build_report_has_f4_sections(tmp_path):
    zpath = _make_ipa(tmp_path, [
        ("Payload/Game.app/Data/level0", _level_sf()),
        ("Payload/Game.app/Data/sharedassets0.assets", _texture_sf()),
    ])
    root = disc.open_source_vfs(zpath, LIM)
    inv = disc.inventory(root, LIM)
    extra: dict = {}
    ext._build_layers(root, inv, LIM, BE, lambda m: None, extra_out=extra)
    result = ext.ExtractionResult(ok=True, decision="done")
    result.texture_total = len(extra["textures"])
    result.texture_warnings = [
        {"source": u + ("::" + a if a else ""), "name": n}
        for u, a, n in extra["textures"]]
    result.scene_text = dict(extra["scene_text"])
    req = ext.ExtractionRequest(source=zpath, out_dir=None)
    report = ext._build_report(req, result, [], 0.0)
    assert report["texture_text"]["total"] == 2
    assert len(report["texture_text"]["textures"]) == 2
    assert "Khong OCR" in report["texture_text"]["note"] \
        or "không OCR" in report["texture_text"]["note"]
    assert report["scene_text"]["keys"] == 2
