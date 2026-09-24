"""Offline tests for typology.py's pure logic (Step 2). No real vendored WALS/Grambank/lang2vec data
needed — these exercise grambank_direction, wals_direction, and direction()'s file-based lookup
against synthetic tables."""
import json

import lexeme_aligner.typology as ty


# --- grambank_direction ------------------------------------------------------------------------------
def test_grambank_direction_adposition_before():
    assert ty.grambank_direction({"GB074": "1", "GB075": "0"}, "adposition") == "before"


def test_grambank_direction_adposition_after():
    assert ty.grambank_direction({"GB074": "0", "GB075": "1"}, "adposition") == "after"


def test_grambank_direction_ambiguous_is_none():
    assert ty.grambank_direction({"GB074": "1", "GB075": "1"}, "adposition") is None


def test_grambank_direction_possessor_ternary():
    assert ty.grambank_direction({"GB065": "1"}, "possessor") == "before"
    assert ty.grambank_direction({"GB065": "2"}, "possessor") == "after"
    assert ty.grambank_direction({"GB065": "3"}, "possessor") is None


def test_grambank_direction_none_grambank_is_none():
    assert ty.grambank_direction(None, "adposition") is None


def test_grambank_direction_article_has_no_wals_style_pair_but_grambank_works():
    assert ty.grambank_direction({"GB022": "1", "GB023": "0"}, "article") == "before"


def test_grambank_direction_subject_object_verb_use_the_shared_gb131_133_pair():
    # verb-final (GB133=1) -> subject/object BEFORE verb; verb-initial (GB131=1) -> AFTER.
    assert ty.grambank_direction({"GB131": "0", "GB133": "1"}, "subject_verb") == "before"
    assert ty.grambank_direction({"GB131": "1", "GB133": "0"}, "subject_verb") == "after"
    assert ty.grambank_direction({"GB131": "0", "GB133": "1"}, "object_verb") == "before"
    assert ty.grambank_direction({"GB131": "1", "GB133": "0"}, "object_verb") == "after"


# --- wals_direction ------------------------------------------------------------------------------------
def test_wals_direction_majority_vote_single_entry():
    iso_to_wals = {"xyz": ["xyz1"]}
    values = {("85A", "xyz1"): "2"}          # 85A-2 = Prepositions = before
    assert ty.wals_direction("xyz", "adposition", iso_to_wals, values) == "before"


def test_wals_direction_majority_vote_multi_entry_agreement():
    iso_to_wals = {"xyz": ["a", "b", "c"]}
    values = {("85A", "a"): "2", ("85A", "b"): "2", ("85A", "c"): "1"}
    assert ty.wals_direction("xyz", "adposition", iso_to_wals, values) == "before"   # 2/3 before


def test_wals_direction_tied_vote_is_none():
    iso_to_wals = {"xyz": ["a", "b"]}
    values = {("85A", "a"): "2", ("85A", "b"): "1"}   # one before, one after -> tie
    assert ty.wals_direction("xyz", "adposition", iso_to_wals, values) is None


def test_wals_direction_no_data_is_none():
    assert ty.wals_direction("xyz", "adposition", {}, {}) is None


def test_wals_direction_no_article_parameter_mapping():
    # WALS has no article-order parameter at all — see module docstring.
    iso_to_wals = {"xyz": ["a"]}
    assert ty.wals_direction("xyz", "article", iso_to_wals, {}) is None


def test_wals_direction_possessor_genitive_noun_order():
    iso_to_wals = {"xyz": ["a"]}
    assert ty.wals_direction("xyz", "possessor", iso_to_wals, {("86A", "a"): "1"}) == "before"
    assert ty.wals_direction("xyz", "possessor", iso_to_wals, {("86A", "a"): "2"}) == "after"


# --- direction() (file-based lookup) --------------------------------------------------------------------
def test_direction_reads_from_the_built_table(tmp_path):
    fp = tmp_path / "directions.json"
    fp.write_text(json.dumps({"spa": {"adposition": {"direction": "before", "source": "wals",
                                                      "confidence": 0.98}}}), encoding="utf-8")
    assert ty.direction("spa", "adposition", path=fp) == "before"


def test_direction_missing_language_is_none(tmp_path):
    fp = tmp_path / "directions.json"
    fp.write_text(json.dumps({"spa": {}}), encoding="utf-8")
    assert ty.direction("xyz", "adposition", path=fp) is None


def test_direction_missing_slot_is_none(tmp_path):
    fp = tmp_path / "directions.json"
    fp.write_text(json.dumps({"spa": {"adposition": {"direction": "before"}}}), encoding="utf-8")
    assert ty.direction("spa", "possessor", path=fp) is None


def test_direction_missing_file_is_none(tmp_path):
    assert ty.direction("spa", "adposition", path=tmp_path / "nope.json") is None
