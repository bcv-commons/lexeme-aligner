"""Text fingerprint v1 as the compact file name (2026-10-21, plan internal-docs/compact-text-fingerprint-plan-2026-10-12.md steps 2-4):
the vendored tokenizer, the per-edition _fingerprints.txt map, routing, the publish preflight and JS/Python parity."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lexeme_aligner import compact_layers as cl
from lexeme_aligner import text_fingerprint as tf
from lexeme_aligner import usj_source as us

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("publish_safe_fp", ROOT / "pipeline/scripts/publish_safe.py")
ps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ps)


def _usj(path: Path, verses: dict[tuple[int, int], str]) -> Path:
    content, ch = [], None
    for (c, v), text in sorted(verses.items()):
        if c != ch:
            content.append({"type": "chapter", "number": str(c)})
            ch = c
        content.append({"type": "para", "marker": "p", "content": [{"type": "verse", "number": str(v)}, text]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "USJ", "content": content}), encoding="utf-8")
    return path


def test_tokenizer_is_the_vendored_bibles_copy():
    assert us.tokenize is us._BCV.tokenize and us.strip_marks is us._BCV.strip_marks
    assert us.TOKENIZER_VERSION == 3 and tf.TEXT_FP_VERSION == 1 and tf.NAME_HEX == 8
    assert tf.fingerprints is us._BCV.fingerprints


def test_book_file_hash_is_the_first_8_hex_of_the_book_fingerprint(tmp_path):
    fp = _usj(tmp_path / "08-RUT.json", {(1, 1): "In the days", (1, 2): "when judges ruled"})
    book, _ = tf.fingerprints({(1, 1): "In the days", (1, 2): "when judges ruled"})
    assert tf.book_file_hash(fp) == book[:8]


def test_update_map_replaces_a_books_line_and_keeps_canonical_order(tmp_path):
    d = tmp_path / "e/eng/eng_X"
    tf.update_map(d, {"MAT": "MAT aaaaaaaa 2026-10-01 11111", "GEN": "GEN bbbbbbbb 2026-10-01 22222"})
    tf.update_map(d, {"MAT": "MAT cccccccc 2026-10-21 33333", "RUT": "RUT dddddddd 2026-10-21 44444"})
    lines = (d / tf.MAP_NAME).read_text(encoding="utf-8").splitlines()
    assert [ln.split()[0] for ln in lines] == ["GEN", "RUT", "MAT"]          # canonical order, not alphabetical
    assert tf.read_map(d)["MAT"] == "MAT cccccccc 2026-10-21 33333"           # replaced, not appended
    assert not list(d.glob(".*.tmp"))


def test_the_map_goes_to_the_meta_repo():
    assert cl.layer_of("e/eng/eng_BSB/_fingerprints.txt") == "meta"
    assert cl.layer_of("e/eng/eng_BSB/GEN_1a2b3c4d.json") == "main"


def test_layer_files_include_the_per_edition_names(tmp_path):
    d = tmp_path / "e/eng/eng_X"
    d.mkdir(parents=True)
    for n in ("GEN_1a2b3c4d.json", "GEN_1a2b3c4d.meta.json", "_layer.json", "_fingerprints.txt", "GEN_1a2b3c4d.extra.json"):
        (d / n).write_text("x")
    assert ps.layer_files(["eng"], "meta", tmp_path) == ["e/eng/eng_X/GEN_1a2b3c4d.meta.json", "e/eng/eng_X/_fingerprints.txt",
                                                         "e/eng/eng_X/_layer.json"]
    assert ps.layer_files(["eng"], "extra", tmp_path) == ["e/eng/eng_X/GEN_1a2b3c4d.extra.json"]


def test_preflight_fingerprint_check(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "REPO", tmp_path)
    usj = _usj(tmp_path / "pipeline/work/ingest-cache/usj-engx/08-RUT.json", {(1, 1): "In the days"})
    d = tmp_path / "publish/compact-alignments/e/eng/eng_X"
    d.mkdir(parents=True)
    eds = {"eng_X": {"tag": "engx", "books": ["RUT"]}}
    good = tf.book_file_hash(usj)
    (d / f"RUT_{good}.json").write_text("[]")
    assert ps.fingerprint_problems("eng", eds, {}) == []
    (d / "RUT_0bad0bad.json").write_text("[]")                                 # a second, stale version next to it
    assert "several files" in ps.fingerprint_problems("eng", eds, {})[0]
    (d / f"RUT_{good}.json").unlink()
    why = ps.fingerprint_problems("eng", eds, {})
    assert len(why) == 1 and "not named with their text fingerprint" in why[0] and good in why[0]


def test_sync_js_copies_the_vendored_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for n in ps.JS_FILES:
        (src / n).write_text(f"// {n}")
    root = tmp_path / "root"
    root.mkdir()
    assert ps.sync_js(root, src) == list(ps.JS_FILES)
    assert ps.sync_js(root, src) == []                                       # unchanged: nothing rewritten


def test_published_js_equals_the_vendored_copy():
    for n in ps.JS_FILES:
        assert (ROOT / "publish/compact-alignments" / n).read_bytes() == (ps.JS_SRC / n).read_bytes()


SAMPLES = {(1, 1): "In the beginning, God created the heavens and the earth.", (1, 2): "Gaïus, l’ancien — à toi!",
           (1, 3): "दाऊद ने कहा", (1, 4): "فِي البَدْءِ خَلَقَ اللهُ", (2, 1): "神の子イエス・キリスト", (2, 2): "中文聖經和合本",
           (2, 3): "အာဗြဟံ ဒါဝိဒ်", (2, 4): "  12, 13. ", (3, 1): "Bridged ʼaleph and qof"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_and_python_fingerprints_agree(tmp_path):
    book, chapters = tf.fingerprints(SAMPLES)
    script = tmp_path / "fp.mjs"
    mod = (ps.JS_SRC / "text_fingerprint.mjs").as_uri()
    pairs = json.dumps([[f"{c}:{v}", t] for (c, v), t in SAMPLES.items()], ensure_ascii=False)
    script.write_text(f'import {{fingerprints}} from "{mod}";\n'
                      f"const r = await fingerprints(new Map({pairs}));\nconsole.log(JSON.stringify(r));\n", encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    assert out["book"] == book
    assert {int(k): v for k, v in out["chapters"].items()} == chapters


def test_move_superseded_keeps_one_version_per_book(tmp_path):
    from lexeme_aligner.compact_align import move_superseded
    root = tmp_path / "publish"
    d = root / "e/eng/eng_X"
    d.mkdir(parents=True)
    for n in ("RUT_aaaaaaaa.json", "RUT_aaaaaaaa.meta.json", "RUT_bbbbbbbb.json", "RUT_bbbbbbbb.extra.json", "GEN_cccccccc.json"):
        (d / n).write_text("[]")
    moved = move_superseded(d, "RUT", "bbbbbbbb", root, tmp_path / "superseded")
    assert sorted(p.name for p in d.iterdir()) == ["GEN_cccccccc.json", "RUT_bbbbbbbb.extra.json", "RUT_bbbbbbbb.json"]
    assert sorted(p.relative_to(tmp_path / "superseded").as_posix() for p in moved) == ["e/eng/eng_X/RUT_aaaaaaaa.json",
                                                                                       "e/eng/eng_X/RUT_aaaaaaaa.meta.json"]


def test_language_map_combines_the_edition_maps(tmp_path):
    lang = tmp_path / "e/eng"
    tf.update_map(lang / "eng_B", {"RUT": "RUT 44444444 2026-10-21 aaaaa"})
    tf.update_map(lang / "eng_A", {"MAT": "MAT 11111111 2026-10-01 bbbbb", "GEN": "GEN 22222222 2026-10-01 ccccc"})
    assert (lang / tf.MAP_NAME).read_text(encoding="utf-8").splitlines() == [
        "eng_A GEN 22222222 2026-10-01 ccccc", "eng_A MAT 11111111 2026-10-01 bbbbb", "eng_B RUT 44444444 2026-10-21 aaaaa"]
    tf.update_map(lang / "eng_B", {"RUT": "RUT 55555555 2026-10-22 ddddd"})                 # an edition update refreshes it
    assert (lang / tf.MAP_NAME).read_text(encoding="utf-8").splitlines()[-1] == "eng_B RUT 55555555 2026-10-22 ddddd"
    assert cl.layer_of("e/eng/_fingerprints.txt") == "meta"
    assert ps.layer_files(["eng"], "meta", tmp_path) == ["e/eng/_fingerprints.txt", "e/eng/eng_A/_fingerprints.txt",
                                                         "e/eng/eng_B/_fingerprints.txt"]


def test_language_map_is_removed_without_edition_maps(tmp_path):
    lang = tmp_path / "e/eng"
    (lang / "eng_A").mkdir(parents=True)
    (lang / tf.MAP_NAME).write_text("stale\n")
    assert tf.update_language_map(lang) is None and not (lang / tf.MAP_NAME).exists()


def test_language_map_lists_layer_only_editions_and_keeps_them(tmp_path):
    lang = tmp_path / "e/eng"
    tf.update_map(lang / "eng_A", {"GEN": "GEN 22222222 2026-10-01 ccccc"})
    layer = tmp_path / "fa/e/eng/eng_glt+manual+door43"
    tf.update_map(layer, {"RUT": "RUT 33333333 2026-10-16 ddddd"}, language=False)
    assert not (layer.parent / tf.MAP_NAME).exists()                         # no language map in a full-align tree
    tf.update_language_map(lang, {"full-alignments-manual-sa/eng_glt+manual+door43": layer})
    want = ["eng_A GEN 22222222 2026-10-01 ccccc", "full-alignments-manual-sa/eng_glt+manual+door43 RUT 33333333 2026-10-16 ddddd"]
    assert (lang / tf.MAP_NAME).read_text(encoding="utf-8").splitlines() == want
    tf.update_map(lang / "eng_A", {"GEN": "GEN 44444444 2026-10-22 eeeee"})   # a chain rewrite keeps the layer line
    assert (lang / tf.MAP_NAME).read_text(encoding="utf-8").splitlines()[1] == want[1]
    tf.update_language_map(lang, {})                                           # publish_safe: the layer no longer passes the gate
    assert (lang / tf.MAP_NAME).read_text(encoding="utf-8").splitlines() == ["eng_A GEN 44444444 2026-10-22 eeeee"]


def test_layer_map_lines_only_for_the_text_the_layer_was_built_on(tmp_path):
    usj = _usj(tmp_path / "usj/08-RUT.json", {(1, 1): "In the days"})
    _usj(tmp_path / "usj/57-TIT.json", {(1, 1): "Paul a servant"})
    layer = tmp_path / "layer"
    layer.mkdir()
    (layer / f"RUT_{tf.book_file_hash(usj)}.json").write_text("[]")
    (layer / "TIT_0bad0bad.json").write_text("[]")                            # built on an older text: no line
    (layer / "_layer.json").write_text("{}")
    lines = tf.layer_map_lines(layer, tmp_path / "usj")
    assert list(lines) == ["RUT"] and lines["RUT"].split()[1] == tf.book_file_hash(usj)


def test_language_map_layers_respect_the_publish_gate(tmp_path):
    comp = tmp_path / "publish/compact-alignments"
    (comp / "e/eng/eng_A").mkdir(parents=True)
    (comp / "manifest.json").write_text(json.dumps({"languages": {"eng": {"editions": {"eng_A": {"tag": "enga"}}}}}))
    root = tmp_path / "publish/full-alignments-manual-sa"
    layers = {"eng_A+manual+door43": "eng_A", "eng_glt+manual+door43": "eng_glt", "eng_held+manual+door43": "eng_held"}
    for lid in layers:
        tf.update_map(root / "e/eng" / lid, {"RUT": "RUT 33333333 2026-10-16 ddddd"}, language=False)
    (root / "manifest.json").write_text(json.dumps({"languages": {"eng": {"layers": {
        lid: {"edition": ed, "license": "CC BY-SA 4.0"} for lid, ed in layers.items()}}}}))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/fullalign_publish.json").write_text(json.dumps({"open_licence": r"\bcc[- ]?by\b",
                                                                         "hold": {"eng_held+manual+door43": {"reason": "test"}},
                                                                         "text_licences": {e: {"licence": "CC BY-SA 4.0"} for e in ("eng_glt", "eng_held")}}))
    got = ps.language_map_layers("eng", tmp_path)
    assert list(got) == ["full-alignments-manual-sa/eng_glt+manual+door43"]   # has a compact edition / held: not listed


def test_manual_manifest_update_writes_the_layer_map(tmp_path):
    # 2026-10-21: _update_manual_manifests called tf without importing it (NameError after the layer files were written)
    from lexeme_aligner import fullalign_build as fb
    usj = _usj(tmp_path / "usj/08-RUT.json", {(1, 1): "In the days"})
    ldir = tmp_path / "root/e/eng/eng_X+manual+clear"
    ldir.mkdir(parents=True)
    (ldir / f"RUT_{tf.book_file_hash(usj)}.json").write_text("[]")
    ml = {"source": "clear", "base_text": "X", "license": "CC BY 4.0"}
    fb._update_manual_manifests("eng", "eng_X", {"eng_X+manual+clear": (ml, ldir)}, {"layers": {"eng_X+manual+clear": {"rows": 1}}},
                                None, tmp_path / "usj")
    assert list(tf.read_map(ldir)) == ["RUT"]
    entry = json.loads((tmp_path / "root/manifest.json").read_text())["languages"]["eng"]["layers"]["eng_X+manual+clear"]
    assert entry["books"] == {"RUT": f"RUT_{tf.book_file_hash(usj)}.json"}


def test_fingerprint_cache_from_an_older_tokenizer_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "REPO", tmp_path)
    usj = _usj(tmp_path / "pipeline/work/ingest-cache/usj-engx/08-RUT.json", {(1, 1): "In the days"})
    d = tmp_path / "publish/compact-alignments/e/eng/eng_X"
    d.mkdir(parents=True)
    (d / f"RUT_{tf.book_file_hash(usj)}.json").write_text("[]")
    st = usj.stat()
    old = {f"{usj}|{st.st_size}|{st.st_mtime_ns}": "0bad0bad"}                  # 2026-10-28: key without versions, hash of tokenizer v2
    assert ps.fingerprint_problems("eng", {"eng_X": {"tag": "engx", "books": ["RUT"]}}, old) == []
