"""Offline tests for contest_rule.py's gold_health (roadmap F2, 2026-09-25): the canonical,
gold-source-agnostic core moved here from gold_to_fullalign.py's own local reimplementation. Pure
logic, synthetic surf_at/agg/ours — no real Clear parquet or align_*.jsonl needed."""
import lexeme_aligner.contest_rule as cr


def test_gold_health_separates_positional_from_lexical_agreement():
    # gold says H1 -> "foo" at ref 1, and elsewhere (ref 2) attests "bar" too for the same strong.
    surf_at = {(1, "H1"): {"foo"}, (2, "H1"): {"bar"}}
    agg = {"H1": {"foo", "bar"}}
    # our ref-1 rendering is "bar" -- wrong AT THIS POSITION but a real attested rendering elsewhere.
    ours = [(1, "H1", {"bar"})]
    h = cr.gold_health(surf_at, agg, ours)
    assert h == {"positional": 0.0, "lexical": 1.0, "gap": 1.0, "n": 1}


def test_gold_health_positional_match_gives_zero_gap():
    surf_at = {(1, "H1"): {"foo"}}
    agg = {"H1": {"foo"}}
    ours = [(1, "H1", {"foo"})]
    h = cr.gold_health(surf_at, agg, ours)
    assert h == {"positional": 1.0, "lexical": 1.0, "gap": 0.0, "n": 1}


def test_gold_health_skips_refs_the_gold_does_not_judge():
    surf_at = {(1, "H1"): {"foo"}}
    agg = {"H1": {"foo"}}
    ours = [(1, "H1", {"foo"}), (2, "H1", {"whatever"})]   # ref 2 not in surf_at -> excluded from n
    h = cr.gold_health(surf_at, agg, ours)
    assert h["n"] == 1


def test_gold_health_none_when_ours_judges_nothing_the_gold_covers():
    surf_at = {(1, "H1"): {"foo"}}
    agg = {"H1": {"foo"}}
    assert cr.gold_health(surf_at, agg, []) is None


def test_gold_health_missing_strong_in_agg_does_not_raise():
    # a strong present in surf_at (positional) but never separately aggregated -- agg.get(...) default.
    surf_at = {(1, "H9"): {"foo"}}
    agg = {}
    ours = [(1, "H9", {"foo"})]
    h = cr.gold_health(surf_at, agg, ours)
    assert h == {"positional": 1.0, "lexical": 0.0, "gap": -1.0, "n": 1}


def test_gold_health_shape_agnostic_to_ref_type():
    # works identically whether ref is an int or a zero-padded string, as long as ours/surf_at agree.
    surf_at = {("08001001", "H1"): {"foo"}}
    agg = {"H1": {"foo"}}
    ours = [("08001001", "H1", {"foo"})]
    assert cr.gold_health(surf_at, agg, ours) == {"positional": 1.0, "lexical": 1.0, "gap": 0.0, "n": 1}
