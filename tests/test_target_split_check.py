import json
from argparse import Namespace

import pytest

from lexeme_aligner import target_split_check as sc


def f(cs, fs):
    return {"content_stable": cs, "function_stable": fs, "content": .9, "function": .7, "stable": .8}


@pytest.mark.parametrize("words,parts,want", [         # figures measured 2026-11-28 (internal-docs/split-generator-plan-2026-11-28.md)
    (f(.6759, .4557), f(.6893, .4753), "split"),       # Zinza, make-tanakh hand bundle
    (f(.8231, .5548), f(.8294, .6254), "split"),       # Swahili, generated (vote)
    (f(.9355, .3727), f(.9577, .7362), "split"),       # Arabic, rule split (gold F1 .888 -> .901)
    (f(.9355, .3727), f(.9347, .7609), "words"),       # Arabic, one extra cut per word (gold F1 .882): function stable up, content not
    (f(.6759, .4557), f(.6680, .4632), "words"),       # Zinza generated (vote + Proto-Bantu seeds): content -0.8
    (f(.6759, .4557), f(.6190, .4070), "words"),       # Zinza placebo
    (f(.8231, .5548), f(.7842, .5729), "words"),       # Swahili placebo: function up, content down
    (f(.6759, .4557), f(.6792, .4598), "unclear"),     # content +0.3: between noise and gain
    (f(.4713, .3352), f(.4946, .3110), "split"),       # Kuni MRK: content +2.3; function stability -2.4 is no veto (a later pass)
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
