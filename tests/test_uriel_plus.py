"""Offline tests for uriel_plus.py's pure logic (roadmap I1). No real urielplus package or vendored
Grambank data needed — these exercise `_slot_direction`, `validate_agreement`'s Grambank-comparison
logic, and `build_directions`'s shipped-slot filtering against synthetic tables. The real, measured
per-slot agreement numbers (which slots ship, which don't) are recorded in the module's own docstring
and are not re-derived here — this file only locks down the mechanics."""
import json

import numpy as np

import lexeme_aligner.uriel_plus as up


# --- _slot_direction -----------------------------------------------------------------------------------
def test_slot_direction_before():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.9, 1.0]), "S_ADPOSITION_AFTER_NOUN": np.array([0.0, 0.1])}
    assert up._slot_direction(row, "adposition") == "before"


def test_slot_direction_after():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.1]), "S_ADPOSITION_AFTER_NOUN": np.array([0.9])}
    assert up._slot_direction(row, "adposition") == "after"


def test_slot_direction_both_high_is_none():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.9]), "S_ADPOSITION_AFTER_NOUN": np.array([0.9])}
    assert up._slot_direction(row, "adposition") is None


def test_slot_direction_both_low_is_none():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.1]), "S_ADPOSITION_AFTER_NOUN": np.array([0.1])}
    assert up._slot_direction(row, "adposition") is None


def test_slot_direction_missing_feature_is_none():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.9])}
    assert up._slot_direction(row, "adposition") is None


def test_slot_direction_all_sentinel_values_is_none():
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([-1, -1]), "S_ADPOSITION_AFTER_NOUN": np.array([-1])}
    assert up._slot_direction(row, "adposition") is None


def test_slot_direction_sentinel_values_excluded_from_mean():
    # one real 0.9 plus a -1 sentinel that must be dropped, not averaged in (which would give 0.45).
    row = {"S_ADPOSITION_BEFORE_NOUN": np.array([0.9, -1]), "S_ADPOSITION_AFTER_NOUN": np.array([0.1])}
    assert up._slot_direction(row, "adposition") == "before"


# --- validate_agreement ---------------------------------------------------------------------------------
def _gb_doc(languages):
    return {
        "_features": {
            "adposition_order": ["GB074", "GB075"],
            "article_order": ["GB022", "GB023"],
            "possession_order": ["GB065"],
            "subject_verb_order": ["GB131", "GB133"],
            "object_verb_order": ["GB131", "GB133"],
        },
        "languages": languages,
    }


def test_validate_agreement_counts_agreement_on_resolved_overlap(tmp_path):
    gb_path = tmp_path / "features.json"
    gb_path.write_text(json.dumps(_gb_doc({
        "aaa": {"GB074": "1", "GB075": "0"},   # gold: before
        "bbb": {"GB074": "0", "GB075": "1"},   # gold: after
    })), encoding="utf-8")
    uriel_directions = {"aaa": {"adposition": "before"}, "bbb": {"adposition": "before"}}
    results = up.validate_agreement(uriel_directions, gb_path)
    assert results["adposition"] == (1, 2)   # aaa agrees, bbb disagrees


def test_validate_agreement_skips_when_either_side_unresolved(tmp_path):
    gb_path = tmp_path / "features.json"
    gb_path.write_text(json.dumps(_gb_doc({
        "aaa": {"GB074": "1", "GB075": "1"},          # gold ambiguous -> unresolved
        "bbb": {},                                    # gold has neither code
        "ccc": {"GB074": "1", "GB075": "0"},          # gold resolved, but uriel doesn't cover ccc
    })), encoding="utf-8")
    uriel_directions = {"aaa": {"adposition": "before"}, "bbb": {"adposition": "before"}}
    results = up.validate_agreement(uriel_directions, gb_path)
    assert results["adposition"] == (0, 0)


def test_validate_agreement_possessor_uses_ternary_gb065(tmp_path):
    gb_path = tmp_path / "features.json"
    gb_path.write_text(json.dumps(_gb_doc({
        "aaa": {"GB065": "1"},   # before
        "bbb": {"GB065": "2"},   # after
        "ccc": {"GB065": "3"},   # both/free -> unresolved
    })), encoding="utf-8")
    uriel_directions = {"aaa": {"possessor": "before"}, "bbb": {"possessor": "before"}, "ccc": {"possessor": "after"}}
    results = up.validate_agreement(uriel_directions, gb_path)
    assert results["possessor"] == (1, 2)   # aaa agrees, bbb disagrees, ccc excluded (gold unresolved)


# --- build_directions (shipped-slot filtering) ----------------------------------------------------------
def test_build_directions_only_writes_shipped_slots(tmp_path, monkeypatch):
    all_directions = {"aaa": {"adposition": "before", "possessor": "after"}}
    monkeypatch.setattr(up, "build_all_slot_directions", lambda: all_directions)
    out_path = tmp_path / "uriel_plus.json"
    shipped = up.build_directions(out_path)
    assert set(shipped["aaa"]) == {"adposition"}   # possessor is NOT in SHIPPED_SLOTS -> dropped
    assert shipped["aaa"]["adposition"]["source"] == "uriel_plus"
    assert shipped["aaa"]["adposition"]["confidence"] == up._MEASURED_AGREEMENT["adposition"]


def test_build_directions_drops_a_language_with_no_shipped_slot_at_all(tmp_path, monkeypatch):
    all_directions = {"aaa": {"possessor": "after"}}   # only an unshipped slot
    monkeypatch.setattr(up, "build_all_slot_directions", lambda: all_directions)
    out_path = tmp_path / "uriel_plus.json"
    shipped = up.build_directions(out_path)
    assert shipped == {}


def test_build_directions_writes_valid_json_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "build_all_slot_directions", lambda: {"aaa": {"object_verb": "after"}})
    out_path = tmp_path / "uriel_plus.json"
    up.build_directions(out_path)
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk == {"aaa": {"object_verb": {"direction": "after", "source": "uriel_plus",
                                               "confidence": up._MEASURED_AGREEMENT["object_verb"]}}}


def test_shipped_slots_is_exactly_adposition_and_object_verb():
    # locks down the module's own ship/withhold decision (docstring: article/possessor/subject_verb
    # all failed the >=90% gate this run) so a future edit can't silently widen it without a new measurement.
    assert set(up.SHIPPED_SLOTS) == {"adposition", "object_verb"}
