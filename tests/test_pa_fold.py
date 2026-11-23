"""pa_fold: part-alignments -> compact-alignments (Python), and the same result from the JavaScript fold."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lexeme_aligner import pa_fold

REPO = Path(__file__).resolve().parents[1]

# Greek-like verse: three tokens. tok0 = καί (function word, one unit), tok1 = λέγει (stem + ending), tok2 = αὐτοῖς (stem + ending)
VERSE = {
    "units": [["400010010011", "particle", "καὶ", 0],
              ["400010010021", "stem", "λέγ", 1], ["400010010022", "ending", "ει", 1],
              ["400010010031", "stem", "αὐτ", 2], ["400010010032", "ending", "οῖς", 2]],
    "tokens": [["40001001001", "2532", 0, 0], ["40001001002", "3004", 1, 1], ["40001001003", "0846", 1, 3]],
}


def test_ending_adds_unclaimed_words_only():
    # stem λέγ -> 1 ("says"), ending ει -> 0 ("he") and 3 ("them", claimed by αὐτ's carrier) -> folded λέγει = 0-1
    main, meta, extra = pa_fold.fold_verse(VERSE, "0:2 1:1 2:0,3 3:3", {"method": "EEeE", "conf": "2112"})
    assert main == "0:0-1 1:3"                         # compact srcOrd: λέγει = 0, αὐτοῖς = 1; καί has no slot
    assert meta == {"method": "EE", "conf": "12"}      # from the carriers (units 1 and 3: the 2nd and 4th entry)
    assert extra == ""


def test_word_with_only_an_ending_link_and_llm_none():
    main, meta, _ = pa_fold.fold_verse(VERSE, "1:_ 2:4 3:_", {"method": "l", "conf": "1"})
    assert main == "0:4"                               # the ending gives the word its link; `_` gives nothing
    assert meta == {"method": "l", "conf": "1"}        # dense channels follow the linked entries only


def test_bonus_and_extra_for_gaps():
    main, meta, extra = pa_fold.fold_verse(VERSE, "1:1", {"method": "E", "bonus": "3:7:12:40"}, "3:5 4:6")
    assert main == "0:1"
    assert meta["bonus"] == "1:7:12:40"                # αὐτοῖς (compact 1) has no entry: its carrier's bonus
    assert extra == "1:5-6"                            # its units' residual words together


def test_compact_index():
    ix = pa_fold.compact_index({"MAT 1:1": VERSE})
    assert ix == {"lexemes": {"MAT 1:1": ["3004", "0846"]}, "keys": {"MAT 1:1": ["40001001002", "40001001003"]},
                  "fn": {"MAT 1:1": ["2532"]}, "fn_keys": {"MAT 1:1": ["40001001001"]}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_fold_matches_python(tmp_path):
    parts = tmp_path / "pa"
    (parts / "_index").mkdir(parents=True)
    (parts / "_index" / "MAT_parts.json").write_text(json.dumps({"MAT 1:1": VERSE, "MAT 1:2": VERSE}), encoding="utf-8")
    ed = parts / "e" / "eng" / "x"
    ed.mkdir(parents=True)
    (ed / "MAT_0000abcd.json").write_text(json.dumps(["0:2 1:1 2:0,3 3:3", "1:_ 2:4"]), encoding="utf-8")
    (ed / "MAT_0000abcd.meta.json").write_text(json.dumps({"method": ["EEeE", "l"], "conf": ["2112", "1"], "contested": ["1:g:2", ""],
                                                           "bonus": ["", "3:7:12:40"], "rule": ["", ""], "rend": ["1 1 2 1", "3"]}), encoding="utf-8")
    (ed / "MAT_0000abcd.extra.json").write_text(json.dumps(["", "3:5 4:6"]), encoding="utf-8")
    py, js = tmp_path / "py", tmp_path / "js"
    pa_fold.fold_edition(parts, ed, py)
    pa_fold.write_compact_index(parts, py)
    mjs = REPO / "pipeline/lexeme_aligner/js/pa_fold.mjs"
    subprocess.run(["node", str(mjs), "--parts", str(parts), "--out", str(js)], check=True)
    subprocess.run(["node", str(mjs), "--parts", str(parts), "--out", str(js), "--index-only"], check=True)
    files = sorted(p.relative_to(py) for p in py.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(js) for p in js.rglob("*") if p.is_file())
    for f in files:
        assert (py / f).read_bytes() == (js / f).read_bytes(), f
    assert json.loads((py / "e/eng/x/MAT_0000abcd.json").read_text()) == ["0:0-1 1:3", "0:4"]


def test_parts_fold_replaces_an_editions_old_files(tmp_path, monkeypatch):
    """full_chain folds into the shared out dir: a book the parts run wrote nothing for must not keep an older run's file of that method;
    other editions and methods the run did not produce stay."""
    import gzip
    from lexeme_aligner import hebrew_source, parts_fold, run_pilot, versification
    monkeypatch.setattr(hebrew_source, "HebrewSource", lambda **k: None)
    monkeypatch.setattr(run_pilot, "build_corpus", lambda *a, **k: [])
    monkeypatch.setattr(versification, "remapper", lambda *a: None)
    src, dst = tmp_path / "parts", tmp_path / "out"
    src.mkdir(); dst.mkdir()
    (src / "align_gapfill_xx_GEN.jsonl").write_text('{"ref": 1}\n')
    (src / "align_gapfill_yy_GEN.jsonl").write_text('{"ref": 1}\n')                 # another edition: not folded here
    for name in ("align_gapfill_xx_GEN.jsonl", "align_gapfill_xx_NAM.jsonl", "align_gapfill_yy_NAM.jsonl", "align_llm_xx_GEN.jsonl"):
        with gzip.open(dst / (name + ".gz"), "wt") as fh:
            fh.write('{"ref": 0}\n')
    n = parts_fold.fold_dir(tmp_path, src, dst, ["GEN", "NAM"], Path("p.db"), Path("w.db"), "xx", only_tag="xx")
    assert n["old files removed"] == 2
    assert sorted(p.name for p in dst.iterdir()) == ["align_gapfill_xx_GEN.jsonl", "align_gapfill_yy_NAM.jsonl.gz", "align_llm_xx_GEN.jsonl.gz"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_readers_print_the_same(tmp_path):
    """read_verse.py and read_verse.mjs (published beside the data) give the same lines, from the published companions."""
    from lexeme_aligner.part_alignments import write_companions
    root = tmp_path / "pa"
    (root / "_index").mkdir(parents=True)
    (root / "_index" / "MAT_parts.json").write_text(json.dumps({"MAT 1:1": VERSE}), encoding="utf-8")
    ed = root / "x" / "xyz" / "xyz_e"
    ed.mkdir(parents=True)
    (ed / "MAT_0000abcd.json").write_text(json.dumps(["0:2 1:1 2:0,3 3:3"]), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"languages": {"xyz": {"editions": {"xyz_e": {"books": {"MAT": "MAT_0000abcd.json"}}}}}}))
    write_companions(root)
    args = [".", "xyz", "xyz_e", "MAT 1:1", "he says to them and"]
    py = subprocess.run(["python3", "-I", "read_verse.py", *args], cwd=root, capture_output=True, text=True, check=True).stdout
    js = subprocess.run(["node", "read_verse.mjs", *args], cwd=root, capture_output=True, text=True, check=True).stdout
    assert py == js
    assert "-> he says" in py and "λέγει" in py
