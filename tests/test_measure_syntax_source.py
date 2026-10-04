import importlib.util
import json
from pathlib import Path

import pytest

_tools = Path(__file__).resolve().parents[1] / "pipeline/scripts/tools"
spec = importlib.util.spec_from_file_location("measure_syntax_source", _tools / "measure_syntax_source.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def test_arm_env_sets_only_the_syntax_source():
    base = {"PATH": "/bin", "KEEP": "1", "ALIGNER_SYNTAX_SOURCE": "bhsa"}
    env = ms.arm_env("macula", base)
    assert env["PATH"] == "/bin" and env["KEEP"] == "1" and env["ALIGNER_SYNTAX_SOURCE"] == "macula"
    assert env["ALIGNER_SPINE_DB"].endswith("lexeme-spine-macula.db")                     # the macula arm reads the MACULA-only spine
    for arm in ("bhsa", "bhsa-nophrase"):
        e = ms.arm_env(arm, base)
        assert e["ALIGNER_SYNTAX_SOURCE"] == "bhsa" and e["ALIGNER_SPINE_DB"].endswith("lexeme-spine-bhsa-baseline.db")
    with pytest.raises(ValueError):
        ms.arm_env("etcbc", base)


def test_gapfill_cmd_scratch_out_and_nophrase_flags(tmp_path):
    plain = ms.gapfill_cmd("py", "hinirv", "hin", Path("/u"), tmp_path, "all", "bhsa")
    nop = ms.gapfill_cmd("py", "hinirv", "hin", Path("/u"), tmp_path, "all", "bhsa-nophrase")
    assert plain[plain.index("--out") + 1] == str(tmp_path) and "--no-phrase" not in plain
    assert "--no-phrase" in nop and "--no-func-order" in nop
    assert plain[plain.index("--methods") + 1] == "eflomal,gloss,spanext"


def test_clear_removes_only_derived_methods(tmp_path):
    for n in ("align_spanext_t_GEN.jsonl", "align_gapfill_t_GEN.jsonl.gz", "align_eflomal_t_GEN.jsonl", "align_gloss_t_GEN.jsonl",
              "align_spanext_other_GEN.jsonl"):
        (tmp_path / n).write_text("x")
    ms._clear(tmp_path, "t")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["align_eflomal_t_GEN.jsonl", "align_gloss_t_GEN.jsonl", "align_spanext_other_GEN.jsonl"]


def _fake_runner(f1_by_arm: dict, seen: list):
    """Stub chain: records (module, arm); pos_score answers with the F1 of the arm that ran last."""
    def run(cmd, env):
        arm = env["ALIGNER_SYNTAX_SOURCE"]
        mod = cmd[2]
        flags = " ".join(cmd)
        label = "bhsa-nophrase" if "--no-phrase" in flags else arm
        seen.append((mod, label))
        if mod == "lexeme_aligner.gapfill":
            run.last = label
        if mod == "lexeme_aligner.eval.pos_score":
            label = getattr(run, "last", arm)
            res = {ms.SPEC_FULL: {"link_f1": f1_by_arm[label], "link_precision": 0.8, "exact_span": 100, "gold_links": 10}}
            if ms.SPEC_BASE in " ".join(cmd):
                res[ms.SPEC_BASE] = {"link_f1": 0.5, "link_precision": 0.8, "exact_span": 90, "gold_links": 10}
            return "log line\n" + json.dumps({"results": res})
        return ""
    return run


def test_measure_language_scores_each_arm_and_compares_to_bhsa(tmp_path):
    seen: list = []
    r = ms.measure_language("hin", "hinirv", Path("/u"), tmp_path, "all", False,
                            _fake_runner({"bhsa": 0.70, "macula": 0.69, "bhsa-nophrase": 0.70}, seen), "py", 0.002, 0.01)
    s = r["strict"]
    assert s["baseline"]["link_f1"] == 0.5
    assert s["macula_vs_bhsa"]["verdict"] == "LOSS" and s["macula_vs_bhsa"]["delta"]["link_f1"] == pytest.approx(-0.01)
    assert s["nophrase_vs_bhsa"]["verdict"] in ("wash", "no data") or s["nophrase_vs_bhsa"]["delta"]["link_f1"] == pytest.approx(0)
    assert [m for m, _ in seen].count("lexeme_aligner.gapfill") == 3 and [m for m, _ in seen].count("lexeme_aligner.span_extension") == 3


def test_span_extension_flag_is_left_at_config_default(tmp_path):
    seen_cmds: list = []

    def run(cmd, env):
        seen_cmds.append(cmd)
        return json.dumps({"results": {}}) if cmd[2].endswith("pos_score") else ""
    ms.measure_language("hin", "hinirv", Path("/u"), tmp_path, "all", False, run, "py", 0.002, 0.01, arms=("bhsa",))
    se = [c for c in seen_cmds if c[2].endswith("span_extension")][0]
    assert not any("relation-trigger" in x for x in se)          # no explicit override: each language keeps its own config


def test_render_table_marks_skipped_and_counts():
    ok = {"tag": "t", "strict": {"baseline": {"link_f1": .5}, "bhsa": {"link_f1": .7}, "macula": {"link_f1": .69}, "bhsa-nophrase": {"link_f1": .7},
                                 "macula_vs_bhsa": {"delta": {"link_f1": -.01, "exact_span": -3, "link_precision": 0.0}, "verdict": "LOSS"},
                                 "nophrase_vs_bhsa": {"delta": {"link_f1": 0.0, "exact_span": 0, "link_precision": 0.0}, "verdict": "wash"}}}
    t = ms.render_table({"hin": ok, "rus": {"skipped": "no finished eflomal/gloss files"}}, "all", 0.002, False)
    assert "skipped: no finished" in t and "1 language(s) measured" in t and "1 loss" in t


def test_zero_gold_links_is_no_data_not_a_wash(tmp_path):
    def run(cmd, env):
        if cmd[2].endswith("pos_score"):
            z = {"gold_links": 0, "link_f1": 0.0, "link_precision": 0.0, "exact_span": 0}
            return json.dumps({"results": {ms.SPEC_BASE: z, ms.SPEC_FULL: z}})
        return ""
    r = ms.measure_language("fra", "fra-lsg", Path("/u"), tmp_path, "ot", False, run, "py", 0.002, 0.01)
    assert r["strict"]["macula_vs_bhsa"]["verdict"] == "no gold"
    t = ms.render_table({"fra": r}, "ot", 0.002, False)
    assert "no gold in this scope" in t and "0 language(s) measured, 1 with no gold" in t
