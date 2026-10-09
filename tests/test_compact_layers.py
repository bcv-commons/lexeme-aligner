import os

from lexeme_aligner import compact_layers as cl


def test_layer_of_and_split():
    files = ["a/aaz/aaz_C01/GEN_1a2b3.json", "a/aaz/aaz_C01/GEN_1a2b3.meta.json", "a/aaz/aaz_C01/GEN_1a2b3.extra.json",
             "_index/GEN_lexemes.json", "manifest.json", "README.md"]
    assert [cl.layer_of(f) for f in files] == ["main", "meta", "extra", "main", "main", "main"]
    assert cl.layer_of("e/eng/eng_BSB/_layer.json") == "meta" and cl.layer_of("e/eng/eng_BSB/GEN_1a2b3.json") == "main"
    s = cl.split_layers(files)
    assert s["meta"] == ["a/aaz/aaz_C01/GEN_1a2b3.meta.json"] and s["extra"] == ["a/aaz/aaz_C01/GEN_1a2b3.extra.json"]
    assert len(s["main"]) == 4                       # alignments + the shared files keep their path in the main repo


def test_stage_layer_hardlinks_and_adds_the_layer_readme(tmp_path, monkeypatch):
    src = tmp_path / "src"
    f = src / "a/aaz/aaz_C01/GEN_1a2b3.meta.json"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    readme = tmp_path / "meta_readme.md"
    readme.write_text("# meta layer")
    monkeypatch.setattr(cl, "layer_readme", lambda layer: readme)
    root, files = cl.stage_layer(src, "meta", ["a/aaz/aaz_C01/GEN_1a2b3.meta.json"], tmp_path / "stage")
    assert files == ["README.md", "a/aaz/aaz_C01/GEN_1a2b3.meta.json"]
    assert os.path.samefile(root / files[1], f)                       # hard link: no extra disk
    assert (root / "README.md").read_text() == "# meta layer"         # the layer's OWN card, not the main README
    root, files2 = cl.stage_layer(src, "meta", ["a/aaz/aaz_C01/GEN_1a2b3.meta.json"], tmp_path / "stage")   # idempotent re-run
    assert files2 == files


def test_publish_safe_compact_selection_excludes_sidecars(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("publish_safe2", Path(__file__).resolve().parents[1] / "pipeline/scripts/publish_safe.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)
    d = tmp_path / "a/aaz/aaz_C01"
    d.mkdir(parents=True)
    for n in ("GEN_1a2b3.json", "GEN_1a2b3.meta.json", "GEN_1a2b3.extra.json"):
        (d / n).write_text("[]")
    (tmp_path / "_index").mkdir()
    (tmp_path / "_index/GEN_lexemes.json").write_text("[]")
    main = ps.selected_files("compact-alignments", "compact", ["aaz"], tmp_path)
    assert "a/aaz/aaz_C01/GEN_1a2b3.json" in main and "_index/GEN_lexemes.json" in main
    assert not [f for f in main if f.endswith((".meta.json", ".extra.json"))]
    assert ps.layer_files(["aaz"], "meta", tmp_path) == ["a/aaz/aaz_C01/GEN_1a2b3.meta.json"]
    assert ps.layer_files(["aaz"], "extra", tmp_path) == ["a/aaz/aaz_C01/GEN_1a2b3.extra.json"]
