import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("measure_flag", Path(__file__).resolve().parents[1] / "pipeline/scripts/tools/measure_flag.py")
mf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mf)


def test_flag_spelling_matches_argparse_booleanoptionalaction():
    assert mf.flag_arg("typology_fallback", True) == "--typology-fallback"
    assert mf.flag_arg("typology_fallback_articles", False) == "--no-typology-fallback-articles"
    with pytest.raises(ValueError):
        mf.flag_arg("nonsense", True)


def test_commands_point_at_the_scratch_out_folder_never_the_live_one(tmp_path):
    se = mf.span_extension_cmd("py", "spa_r09", "spa", Path("/u"), tmp_path, "typology_fallback", True, "all")
    ps = mf.pos_score_cmd("py", "spa_r09", "spa", Path("/u"), tmp_path, ["eflomal+gloss", "spanext+eflomal+gloss"], "all", True)
    assert se[se.index("--out") + 1] == str(tmp_path) and ps[ps.index("--out") + 1] == str(tmp_path)
    assert "--typology-fallback" in se and ps.count("--method") == 2 and "--convention-aware" in ps and "--json" in ps


def test_running_names_finds_languages_and_tags():
    ps = "x -m lexeme_aligner.full_chain --iso kyu --clean-out\ny -m lexeme_aligner.gapfill --iso spa_r09 --publish-iso spa"
    assert mf.running_names(ps) == {"kyu", "spa_r09", "spa"}


def test_verdict_rules():
    assert mf.verdict({"link_f1": 0.01, "exact_span": 50, "link_precision": 0.0}) == "WIN"
    assert mf.verdict({"link_f1": 0.01, "exact_span": -3, "link_precision": 0.0}) == "wash"           # F1 up but exact_span down
    assert mf.verdict({"link_f1": 0.01, "exact_span": 50, "link_precision": -0.05}) == "wash"         # precision fell too far
    assert mf.verdict({"link_f1": -0.004, "exact_span": 10, "link_precision": 0.0}) == "LOSS"
    assert mf.verdict({"link_f1": 0.0005, "exact_span": 1, "link_precision": 0.0}) == "wash"          # below min_gain
    assert mf.verdict({"link_f1": None, "exact_span": None}) == "no data"


def test_measure_language_runs_off_then_on_and_clears_the_previous_variant(tmp_path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    calls = []
    rows = {"eflomal+gloss": {"link_f1": 0.60, "exact_span": 100, "link_precision": 0.70},
            "spanext+eflomal+gloss": {"link_f1": 0.62, "exact_span": 130, "link_precision": 0.71},
            "eflomal+gloss [conv]": {"link_f1": 0.65, "exact_span": 100, "link_precision": 0.74},
            "spanext+eflomal+gloss [conv]": {"link_f1": 0.66, "exact_span": 120, "link_precision": 0.74}}
    state = {"on": False}

    def run(cmd):
        calls.append(cmd)
        if cmd[2] == "lexeme_aligner.span_extension":
            had_old = list(scratch.glob("align_spanext_t_*"))
            state["on"] = "--typology-fallback" in cmd
            assert not (state["on"] and had_old), "the OFF variant's files must be gone before the ON run"
            (scratch / "align_spanext_t_GEN.jsonl").write_text("x")
            return ""
        specs = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--method"]
        want = {k: dict(v) for k, v in rows.items()}
        if state["on"]:                                                    # ON run: improve
            want["spanext+eflomal+gloss"] = {"link_f1": 0.63, "exact_span": 160, "link_precision": 0.71}
            want["spanext+eflomal+gloss [conv]"] = {"link_f1": 0.67, "exact_span": 150, "link_precision": 0.74}
        res = {s: want[s] for s in specs}
        res.update({s + " [conv]": want[s + " [conv]"] for s in specs if "--convention-aware" in cmd})
        return "noise?\n" + json.dumps({"results": res})

    out = mf.measure_language("spa", "t", Path("/u"), scratch, "typology_fallback", "all", True, run, "py", 0.002, 0.01)
    assert [c[2] for c in calls] == ["lexeme_aligner.span_extension", "lexeme_aligner.pos_score",
                                     "lexeme_aligner.span_extension", "lexeme_aligner.pos_score"]
    assert out["strict"]["delta"]["link_f1"] == pytest.approx(0.01) and out["strict"]["delta"]["exact_span"] == 30
    assert out["strict"]["verdict"] == "WIN" and out["conv"]["verdict"] == "WIN"
    assert out["strict"]["baseline"]["link_f1"] == 0.60


def test_table_has_a_row_per_language_and_a_summary():
    res = {"spa": {"tag": "spa_r09", "strict": {"baseline": {"link_f1": .6}, "off": {"link_f1": .6}, "on": {"link_f1": .63},
                                                "delta": {"link_f1": .03, "exact_span": 10, "link_precision": .01}, "verdict": "WIN"}},
           "ben": {"skipped": "a chain is running"}}
    t = mf.render_table(res, "typology_fallback", "all", 0.002)
    assert "| spa | spa_r09 |" in t and "skipped: a chain is running" in t and "1 language(s) measured: 1 win, 0 loss, 0 wash" in t
