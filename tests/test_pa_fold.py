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
