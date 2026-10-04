"""pipeline_decisions.py (2026-09-28) — the published, per-language decision ledger. Offline: analyze()/
load_grambank/load_spanext_flags/load_fertility_flags/should_stem are all monkeypatched so these tests
never touch real alignment output or config files."""
import json
from pathlib import Path

import lexeme_aligner.pipeline_decisions as pd


def _patch_flags(monkeypatch, spanext=None, fertility=None):
    monkeypatch.setattr(pd, "load_spanext_flags", lambda iso, path=None, tag=None: spanext or {})
    monkeypatch.setattr(pd, "load_fertility_flags", lambda iso: fertility or {})


# --- always_on_mechanisms -------------------------------------------------------------------------------

def test_always_on_mechanism_from_real_grambank(monkeypatch):
    _patch_flags(monkeypatch)
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "name", "grambank_ids": ["GB072", "GB074"]},
    ]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(pd, "direction_for", lambda grambank, feat, iso=None: "after")
    out = pd.always_on_mechanisms("zztag", "zz")
    assert out["case_marking"]["value"] is True
    assert out["case_marking"]["source"] == "grambank:GB072,GB074"
    assert out["case_marking"]["direction"] == "after"
    assert out["case_marking"]["pos"] == ["name"]


def test_always_on_mechanism_from_gram_struct_fallback(monkeypatch):
    _patch_flags(monkeypatch, spanext={"typology_fallback": True})
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "grambank_ids": ["typology:article"]},
    ]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: None)
    monkeypatch.setattr(pd, "direction_for", lambda grambank, feat, iso=None: "before")
    out = pd.always_on_mechanisms("zztag", "zz")
    assert out["articles"]["source"] == "gram_struct:article"
    assert out["articles"]["direction"] == "before"


def test_always_on_mechanisms_deduplicates_multiple_pos_for_one_risk(monkeypatch):
    _patch_flags(monkeypatch)
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "grambank_ids": ["GB020"]},
        {"risk": "articles", "pos": "name", "grambank_ids": ["GB020"]},
    ]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    monkeypatch.setattr(pd, "direction_for", lambda *a, **k: None)
    out = pd.always_on_mechanisms("zztag", "zz")
    assert out["articles"]["pos"] == ["name", "noun"]
    assert "direction" not in out["articles"]     # no direction resolved -> key simply absent


def test_always_on_mechanisms_empty_when_nothing_flagged(monkeypatch):
    _patch_flags(monkeypatch)
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": []})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    assert pd.always_on_mechanisms("zztag", "zz") == {}


def test_possession_affix_uses_the_ternary_direction_helper(monkeypatch):
    _patch_flags(monkeypatch)
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "possession_affix", "pos": "noun", "grambank_ids": ["GB430"]},
    ]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    monkeypatch.setattr(pd, "possession_direction_for", lambda grambank, iso=None: "after")
    out = pd.always_on_mechanisms("zztag", "zz")
    assert out["possession_affix"]["direction"] == "after"


def test_always_on_mechanisms_passes_the_full_tag_list_to_analyze_not_one(monkeypatch):
    # 2026-09-28 fix: a pooled multi-edition language must audit ALL its editions together, not one
    # arbitrarily-chosen tag.
    _patch_flags(monkeypatch)
    seen = {}

    def fake_analyze(tags, publish_iso, out_dir, prior_pack, method, use_typology):
        seen["tags"] = tags
        return {"findings": []}
    monkeypatch.setattr(pd, "analyze", fake_analyze)
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    pd.always_on_mechanisms(["tagA", "tagB", "tagC"], "zz")
    assert seen["tags"] == ["tagA", "tagB", "tagC"]


# --- opt_in_mechanisms -----------------------------------------------------------------------------------

def test_opt_in_mechanisms_projects_present_spanext_flags_only(monkeypatch):
    _patch_flags(monkeypatch, spanext={"relation_trigger": True, "typology_fallback": False,
                                       "_note": "prose, not a flag"})
    out = pd.opt_in_mechanisms("hin")
    assert out["relation_trigger"] == {"value": True,
                                       "source": "measured vs gold — see config/spanext_flags.json"}
    assert out["typology_fallback"]["value"] is False
    assert "definite_trigger" not in out          # no verdict recorded -> absent, not a default False
    assert "_note" not in out


def test_opt_in_mechanisms_projects_fertility_flags(monkeypatch):
    _patch_flags(monkeypatch, fertility={"enabled": True, "lambda": 2.0, "lexeme_targets": True})
    out = pd.opt_in_mechanisms("eng")
    assert out["fertility_priors"]["value"] is True
    assert out["lexeme_targets_fertility"]["value"] is True


def test_opt_in_mechanisms_empty_for_an_unmeasured_language(monkeypatch):
    _patch_flags(monkeypatch)
    assert pd.opt_in_mechanisms("zz") == {}


# --- target_normalization_decision -----------------------------------------------------------------------

def test_target_normalization_stem_with_ratio(monkeypatch):
    monkeypatch.setattr(pd, "should_stem", lambda iso, usj_dir=None: (True, 2.57))
    out = pd.target_normalization_decision("guj")
    assert out["value"] == "stem"
    assert "tokens/type=2.57" in out["source"]


def test_target_normalization_surface_unavailable(monkeypatch):
    monkeypatch.setattr(pd, "should_stem", lambda iso, usj_dir=None: (False, None))
    out = pd.target_normalization_decision("zz")
    assert out["value"] == "surface"
    assert "unavailable" in out["source"]


def test_target_normalization_passes_the_full_usj_dir_list_through(monkeypatch):
    seen = {}

    def fake_should_stem(iso, usj_dir=None):
        seen["dirs"] = usj_dir
        return False, None
    monkeypatch.setattr(pd, "should_stem", fake_should_stem)
    pd.target_normalization_decision("zz", usj_dirs=["/a", "/b"])
    assert seen["dirs"] == ["/a", "/b"]


# --- build_decisions / write_decisions --------------------------------------------------------------------

def test_build_decisions_assembles_all_three_sources(monkeypatch):
    _patch_flags(monkeypatch, spanext={"relation_trigger": True})
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": []})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    monkeypatch.setattr(pd, "should_stem", lambda iso, usj_dir=None: (False, 50.4))
    doc = pd.build_decisions(["hinirv"], "hin")
    assert doc["relation_trigger"]["value"] is True
    assert doc["target_normalization"]["value"] == "surface"


def test_write_decisions_merges_without_clobbering_other_languages(tmp_path):
    config_path = tmp_path / "pipeline_decisions.json"
    pd.write_decisions({"eng": {"target_normalization": {"value": "surface"}}}, config_path=config_path,
                       publish_roots=[])
    pd.write_decisions({"guj": {"target_normalization": {"value": "stem"}}}, config_path=config_path,
                       publish_roots=[])
    doc = json.loads(config_path.read_text())
    assert doc["eng"]["target_normalization"]["value"] == "surface"
    assert doc["guj"]["target_normalization"]["value"] == "stem"
    assert "_doc" in doc


def test_write_decisions_copies_into_existing_publish_roots_only(tmp_path):
    config_path = tmp_path / "config" / "pipeline_decisions.json"
    root_a = tmp_path / "publish" / "a-dataset"
    root_a.mkdir(parents=True)
    root_b = tmp_path / "publish" / "b-dataset"           # deliberately not created
    pd.write_decisions({"eng": {"target_normalization": {"value": "surface"}}}, config_path=config_path,
                       publish_roots=[root_a, root_b])
    assert (root_a / "pipeline_decisions.json").exists()
    assert not root_b.exists()
    a_doc = json.loads((root_a / "pipeline_decisions.json").read_text())
    assert a_doc["eng"]["target_normalization"]["value"] == "surface"


def test_write_decisions_output_is_deterministic(tmp_path):
    config_path = tmp_path / "pipeline_decisions.json"
    pd.write_decisions({"b": {"x": 1}, "a": {"y": 2}}, config_path=config_path, publish_roots=[])
    text1 = config_path.read_text()
    pd.write_decisions({}, config_path=config_path, publish_roots=[])
    text2 = config_path.read_text()
    assert text1 == text2       # re-running with no new entries doesn't churn the file


def test_always_on_records_gated_off_editions(monkeypatch):
    _patch_flags(monkeypatch)
    monkeypatch.setattr(pd, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "name", "grambank_ids": ["GB072"]}]})
    monkeypatch.setattr(pd, "load_grambank", lambda iso: {})
    monkeypatch.setattr(pd, "direction_for", lambda *a, **k: "after")
    monkeypatch.setattr(pd, "load_spanext_flags",
                        lambda iso, path=None, tag=None: {"base_mechanisms": False} if tag == "zz_b" else {})
    part = pd.always_on_mechanisms(["zz_a", "zz_b"], "zz")["case_marking"]
    assert part["value"] is True and part["gated_off_editions"] == ["zz_b"]
    whole = pd.always_on_mechanisms(["zz_b"], "zz")["case_marking"]
    assert whole["value"] is False and whole["gated_off_editions"] == ["zz_b"]
    none = pd.always_on_mechanisms(["zz_a"], "zz")["case_marking"]
    assert none["value"] is True and "gated_off_editions" not in none


def test_target_normalization_records_editions_that_disagree_with_the_pooled_decision(monkeypatch):
    ratios = {"usj-a": (True, 5.0), "usj-b": (False, 20.0), "usj-c": (True, 6.0)}

    def fake(iso, usj_dir=None):
        if isinstance(usj_dir, list):
            return True, 9.0                       # pooled: stem
        return ratios[Path(usj_dir).name]
    monkeypatch.setattr(pd, "should_stem", fake)
    out = pd.target_normalization_decision("zz", [Path("x/usj-a"), Path("x/usj-b"), Path("x/usj-c")])
    assert out["value"] == "stem" and out["edition_overrides"] == {"b": "surface"}
    single = pd.target_normalization_decision("zz", [Path("x/usj-a")])
    assert "edition_overrides" not in single


def test_write_decisions_loses_no_entry_when_many_processes_write_at_once(tmp_path):
    """Every chain now writes its own entry as its last step, so several PROCESSES finish together; an unlocked read-merge-write lost entries."""
    import json
    import subprocess
    import sys
    cfg = tmp_path / "pipeline_decisions.json"
    code = ("import sys\nfrom pathlib import Path\nimport lexeme_aligner.pipeline_decisions as pd\n"
            "for iso in sys.argv[2:]:\n"
            "    pd.write_decisions({iso: {'target_normalization': {'value': 'surface'}}}, config_path=Path(sys.argv[1]), publish_roots=[])\n")
    isos = [f"l{i:02d}" for i in range(24)]
    procs = [subprocess.Popen([sys.executable, "-c", code, str(cfg), *isos[k::8]]) for k in range(8)]     # 8 writers, 3 entries each, all at once
    assert [p.wait(timeout=120) for p in procs] == [0] * 8
    doc = json.loads(cfg.read_text(encoding="utf-8"))
    assert sorted(k for k in doc if k != "_doc") == isos
    assert not list(tmp_path.glob(".*.tmp"))                      # no temp file left behind
