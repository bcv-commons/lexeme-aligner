import json
from argparse import Namespace

import pytest

from lexeme_aligner import target_split_check as sc


def f(content, function, stable):
    return {"content": content, "function": function, "stable": stable}


@pytest.mark.parametrize("words,parts,want", [
    (f(.943, .559, .795), f(.955, .820, .838), "split"),     # heb_scg, 2026-10-23
    (f(.966, .698, .580), f(.967, .762, .574), "split"),     # Zinza P vs Wr, 2026-10-26
    (f(.958, .692, .600), f(.953, .708, .600), "unclear"),   # Kuni-like: +1.6 function, content -0.5
    (f(.958, .692, .600), f(.958, .695, .600), "words"),     # no function gain
    (f(.958, .692, .600), f(.930, .800, .600), "words"),     # big function gain, content falls 2.8 points
])
def test_verdict(words, parts, want):
    assert sc.verdict(words, parts)[0] == want


def test_apply_only_existing_entries_and_clear_verdicts(tmp_path):
    cfg = tmp_path / "target_parts.json"
    cfg.write_text(json.dumps({"x_ed": {"format": "bcv-split", "chain": False}}), encoding="utf-8")
    out = tmp_path / "check"
    out.mkdir()
    res = {"edition": "x_ed", "checked": "2026-11-23", "verdict": "split", "delta": {}, "figures": {}, "thresholds": sc.THRESHOLDS,
           "books": "MRK", "runs": 2}
    (out / sc.RESULT).write_text(json.dumps(res), encoding="utf-8")
    sc.apply(Namespace(out=out, config=cfg))
    e = json.loads(cfg.read_text(encoding="utf-8"))["x_ed"]
    assert e["chain"] is True and e["split_check"]["verdict"] == "split" and e["format"] == "bcv-split"
    (out / sc.RESULT).write_text(json.dumps(res | {"verdict": "unclear"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        sc.apply(Namespace(out=out, config=cfg))
    (out / sc.RESULT).write_text(json.dumps(res | {"edition": "other"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        sc.apply(Namespace(out=out, config=cfg))
