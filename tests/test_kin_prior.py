"""Tests for kin_prior.py (roadmap X3) — pure logic against synthetic relatedness/resolved data,
no real languages.db needed."""
import lexeme_aligner.kin_prior as kp


def test_nearest_resolved_picks_closest_distance_first():
    # `nearest_resolved` trusts its input to already be sorted nearest-first (load_relatedness's own
    # job) and takes the first match — the fixture reflects that real contract, not raw DB order.
    relatedness = {"xxx": [("bbb", 1.0, 1), ("aaa", 2.0, 1), ("ccc", 5.0, 1)]}
    resolved = {"aaa": {"adposition": "after"}, "bbb": {"adposition": "before"},
               "ccc": {"adposition": "after"}}
    hit = kp.nearest_resolved("xxx", "adposition", relatedness, resolved)
    assert hit == ("bbb", 1.0)                      # bbb is closer (distance 1) than aaa (distance 2)


def test_nearest_resolved_skips_unresolved_neighbours():
    relatedness = {"xxx": [("aaa", 1.0, 1), ("bbb", 2.0, 1)]}
    resolved = {"bbb": {"adposition": "before"}}     # aaa has no adposition fact at all
    hit = kp.nearest_resolved("xxx", "adposition", relatedness, resolved)
    assert hit == ("bbb", 2.0)


def test_nearest_resolved_none_when_nothing_resolves():
    relatedness = {"xxx": [("aaa", 1.0, 1)]}
    assert kp.nearest_resolved("xxx", "adposition", relatedness, {}) is None


def test_nearest_resolved_respects_exclude():
    relatedness = {"xxx": [("aaa", 1.0, 1), ("bbb", 2.0, 1)]}
    resolved = {"aaa": {"adposition": "after"}, "bbb": {"adposition": "before"}}
    hit = kp.nearest_resolved("xxx", "adposition", relatedness, resolved, exclude="aaa")
    assert hit == ("bbb", 2.0)


def test_band_for_covers_the_real_distance_range():
    assert kp._band_for(0.0) == (0, 1)
    assert kp._band_for(1.0) == (0, 1)
    assert kp._band_for(3.0) == (2, 3)
    assert kp._band_for(6.0) == (4, 6)
    assert kp._band_for(13.0) == (7, 13)
    assert kp._band_for(20.0) is None               # out of the real observed range


def test_build_resolved_directions_only_keeps_real_directions():
    external = {"aaa": {"adposition": {"direction": "after"}, "article": {"direction": None}}}
    imputed = {"bbb": {"adposition": {"direction": "before"}}}
    resolved = kp.build_resolved_directions(external, imputed)
    assert resolved == {"aaa": {"adposition": "after"}, "bbb": {"adposition": "before"}}
    assert "article" not in resolved["aaa"]          # resolved-null is not a value to propagate


def test_build_resolved_directions_external_wins_over_imputed_for_same_iso():
    external = {"aaa": {"adposition": {"direction": "after"}}}
    imputed = {"aaa": {"adposition": {"direction": "before"}}}
    resolved = kp.build_resolved_directions(external, imputed)
    assert resolved["aaa"]["adposition"] == "after"  # external processed first, setdefault keeps it


def test_leave_one_out_measures_agreement_by_distance_band():
    # A chain of 3 languages all agreeing on "after" at distance 1 from each other.
    relatedness = {
        "aaa": [("bbb", 1.0, 1), ("ccc", 2.0, 1)],
        "bbb": [("aaa", 1.0, 1), ("ccc", 1.0, 1)],
        "ccc": [("bbb", 1.0, 1), ("aaa", 2.0, 1)],
    }
    resolved = {"aaa": {"adposition": "after"}, "bbb": {"adposition": "after"},
               "ccc": {"adposition": "after"}}
    loo = kp.leave_one_out(relatedness, resolved)
    assert loo["adposition@0-1"]["agree"] == 3       # every language's nearest (dist<=1) neighbour agrees
    assert loo["adposition@0-1"]["total"] == 3
    assert loo["adposition@0-1"]["rate"] == 1.0


def test_leave_one_out_excludes_the_held_out_languages_own_fact():
    relatedness = {"aaa": [("bbb", 1.0, 1)], "bbb": [("aaa", 1.0, 1)]}
    resolved = {"aaa": {"adposition": "after"}, "bbb": {"adposition": "before"}}
    loo = kp.leave_one_out(relatedness, resolved)
    # aaa's nearest (excluding itself) is bbb=before != aaa's true "after" -> disagreement
    assert loo["adposition@0-1"]["agree"] == 0
    assert loo["adposition@0-1"]["total"] == 2


def test_build_kin_fills_only_absent_slots():
    relatedness = {"xxx": [("aaa", 1.0, 1)]}
    resolved = {"aaa": {"adposition": "after", "possessor": "before"}}
    existing_keys = {"possessor"}                    # xxx already has possessor from another partition
    out = kp.build_kin("xxx", relatedness, resolved, existing_keys, {})
    assert "possessor" not in out                    # never fills an already-occupied key
    assert out["adposition"] == {"direction": "after", "source": "kin", "neighbour": "aaa",
                                 "distance": 1.0, "confidence": None}


def test_build_kin_attaches_band_confidence():
    relatedness = {"xxx": [("aaa", 1.0, 1)]}
    resolved = {"aaa": {"adposition": "after"}}
    confidence = {"adposition@0-1": {"agree": 9, "total": 10, "rate": 0.9}}
    out = kp.build_kin("xxx", relatedness, resolved, set(), confidence)
    assert out["adposition"]["confidence"] == 0.9


def test_build_kin_empty_when_no_neighbour_resolves_anything():
    relatedness = {"xxx": [("aaa", 1.0, 1)]}
    out = kp.build_kin("xxx", relatedness, {}, set(), {})
    assert out == {}
