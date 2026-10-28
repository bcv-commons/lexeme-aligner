import hashlib
import json

from lexeme_aligner.conventions import pin_layer_modules


def test_pin_layer_modules_names_each_books_module_files(tmp_path):
    v = tmp_path / "vendor"
    (v / "abc/source/grc/x").mkdir(parents=True); (v / "abc/kvg/y").mkdir(parents=True)
    (v / "abc/source/grc/x/jev.md").write_text("greek"); (v / "abc/kvg/y/jev.md").write_text("kuni")
    layer = tmp_path / "layer"; layer.mkdir()
    pin_layer_modules(layer, {"MRK": {"source": v / "abc/source/grc/x/jev.md", "target": v / "abc/kvg/y/jev.md"}}, vendor=v)
    d = json.loads((layer / "_conventions.json").read_text())
    assert d["books"] == {"MRK": {"source": "abc/source/grc/x/jev.md", "target": "abc/kvg/y/jev.md"}}
    assert d["files"]["abc/kvg/y/jev.md"]["sha256"] == hashlib.sha256(b"kuni").hexdigest()
    assert d["mode"] == "modules"
