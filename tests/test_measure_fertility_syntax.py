import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("measure_fertility_syntax", Path(__file__).resolve().parents[1] / "pipeline/scripts/tools/measure_fertility_syntax.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_arm_env_and_commands_keep_scratch_out_and_off_flag(tmp_path):
    e = m.arm_env("macula", {"A": "1"})
    assert e["A"] == "1" and e["ALIGNER_SYNTAX_SOURCE"] == "macula" and e["ALIGNER_SPINE_DB"].endswith("lexeme-spine-macula.db")
    for arm in ("bhsa", "off"):                                   # both read the private BHSA baseline spine
        e = m.arm_env(arm, {})
        assert e["ALIGNER_SYNTAX_SOURCE"] == "bhsa" and e["ALIGNER_SPINE_DB"].endswith("lexeme-spine-bhsa-baseline.db")
    with pytest.raises(ValueError):
        m.arm_env("etcbc", {})
    off = m.pilot_cmd("py", "hinirv", "hin", Path("/u"), tmp_path, "off")
    on = m.pilot_cmd("py", "hinirv", "hin", Path("/u"), tmp_path, "bhsa")
    assert "--no-fertility-priors" in off and "--no-fertility-priors" not in on
    assert off[off.index("--out") + 1] == str(tmp_path) and "--ot" in off and "--method" in off


def test_flagged_anchor_parsing():
    assert m.flagged_anchors("[pilot] Step 3 fertility priors: 2383 anchor(s) flagged (R1 lexeme-targets gated), lambda=2.0") == 2383
    assert m.flagged_anchors("nothing about fertility") is None


def test_compare_uses_two_times_the_larger_spread_with_a_floor():
    a, b = m.summarize([0.700, 0.704, 0.702]), m.summarize([0.701, 0.703, 0.705])
    assert m.tolerance(a, b) == pytest.approx(0.008)
    assert m.compare(a, b, "BETTER", "WORSE", "NEUTRAL")["verdict"] == "NEUTRAL"        # +0.001 vs tolerance 0.008
    far = m.summarize([0.760, 0.761, 0.762])
    assert m.compare(a, far, "BETTER", "WORSE", "NEUTRAL")["verdict"] == "BETTER"
    assert m.compare(far, a, "BETTER", "WORSE", "NEUTRAL")["verdict"] == "WORSE"
    flat = m.summarize([0.7, 0.7, 0.7])
    assert m.tolerance(flat, flat) == m.MIN_TOL                                            # identical replicates never give tolerance 0
    assert m.compare(m.summarize([]), flat, "B", "W", "N")["verdict"] == "no data"


def test_measure_language_neutral_when_arms_match_and_fertility_helps(tmp_path):
    arms = {"bhsa": [0.70, 0.702, 0.701], "macula": [0.7005, 0.702, 0.7015], "off": [0.66, 0.662, 0.661]}
    seen = []
    counters = {a: 0 for a in arms}

    def run(cmd, env):
        arm = "off" if "--no-fertility-priors" in cmd else env["ALIGNER_SYNTAX_SOURCE"]
        seen.append((cmd[2], arm))
        if cmd[2].endswith("run_pilot"):
            run.arm = arm
            return "[pilot] Step 3 fertility priors: 10 anchor(s) flagged\n", ""
        i = counters[run.arm]
        counters[run.arm] += 1
        v = arms[run.arm][i]
        return json.dumps({"results": {"eflomal": {"link_f1": v, "gold_links": 5}, "eflomal [conv]": {"link_f1": v}}}), ""
    r = m.measure_language("hin", "hinirv", Path("/u"), tmp_path, 3, True, run, "py")
    s = r["strict"]
    assert s["macula_vs_bhsa"]["verdict"] == "NEUTRAL" and s["bhsa_vs_off"]["verdict"] == "HELPS"
    assert [x for x in seen if x[0].endswith("run_pilot")][:3] == [("lexeme_aligner.run_pilot", a) for a in ("bhsa", "macula", "off")]   # interleaved
    assert r["anchors"]["macula"] == 10 and "conv" in r


def test_measure_language_skips_when_no_gold_in_ot(tmp_path):
    def run(cmd, env):
        if cmd[2].endswith("run_pilot"):
            return "", ""
        return json.dumps({"results": {"eflomal": {"link_f1": 0.0, "gold_links": 0}}}), ""
    assert "skipped" in m.measure_language("fra", "fra-lsg", Path("/u"), tmp_path, 1, False, run, "py")


def test_render_table_reports_verdicts_and_skips():
    ok = {"tag": "t", "gold_links": 9, "anchors": {"bhsa": 5, "macula": 6},
          "strict": {a: m.summarize([0.7, 0.701]) for a in ("off", "bhsa", "macula")}}
    ok["strict"]["macula_vs_bhsa"] = m.compare(ok["strict"]["bhsa"], ok["strict"]["macula"], "BETTER", "WORSE", "NEUTRAL")
    ok["strict"]["bhsa_vs_off"] = m.compare(ok["strict"]["off"], ok["strict"]["bhsa"], "HELPS", "HURTS", "no effect")
    t = m.render_table({"hin": ok, "fra": {"skipped": "no gold links in the OT for this language"}}, 2)
    assert "NEUTRAL" in t and "skipped: no gold links" in t and "1 language(s) measured, 1 NEUTRAL" in t
