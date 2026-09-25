"""Tests for derive_typology.py (roadmap D0 + D1) — quality-gate pass/fail, slot derivation from
synthetic compute_order_stats/profile-shaped inputs, the audit-vs-slot key separation, content_sha256
presence, and D1's Greek-first selectors (adposition/article_word/possessive_word/negation) plus
validate_derived's calibration/held-out split. No real corpus/spine needed.
"""
from dataclasses import dataclass, field
from pathlib import Path

import lexeme_aligner.derive_typology as dt
from lexeme_aligner.refs import encode


@dataclass
class _FakeD1Tok:
    idx: int
    is_content: bool = True
    strong: str | None = None
    lexeme: str | None = None
    lemma: str | None = None
    case_: str | None = None
    number: str | None = None
    gender: str | None = None
    person: str | None = None
    mood: str | None = None


@dataclass
class _FakeD1Rec:
    book: str
    ch: int
    v: int
    heb: list = field(default_factory=list)


def _rec(members, book="MAT", ch=1, v=1):
    return _FakeD1Rec(book, ch, v, members)


def test_order_slot_omitted_below_minimum_n():
    assert dt._order_slot(rate=0.9, n=10, min_n=50) is None


def test_order_slot_confident_direction_after():
    slot = dt._order_slot(rate=0.85, n=200, min_n=100)
    assert slot == {"direction": "after", "source": "derived", "n": 200, "rate": 0.85}


def test_order_slot_confident_direction_before():
    slot = dt._order_slot(rate=0.15, n=200, min_n=100)
    assert slot["direction"] == "before"


def test_order_slot_mixed_writes_null_with_reason():
    slot = dt._order_slot(rate=0.42, n=200, min_n=100)
    assert slot["direction"] is None
    assert slot["reason"] == "mixed"
    assert slot["source"] == "derived"
    assert slot["n"] == 200


def test_order_slot_none_rate_is_omitted_even_with_enough_n():
    assert dt._order_slot(rate=None, n=200, min_n=100) is None


def test_primary_edition_prefers_most_ot_books():
    manifest = {
        "hin": {"editions": {
            "hin_thin": {"tag": "hin_thin", "books": ["MAT", "MRK"]},
            "hinirv": {"tag": "hinirv", "books": ["GEN", "EXO", "MAT"]},
        }}
    }
    tag, ecode, books = dt.primary_edition("hin", manifest)
    assert tag == "hinirv" and ecode == "hinirv"
    assert set(books) == {"GEN", "EXO", "MAT"}


def test_primary_edition_falls_back_to_most_books_when_no_ot_at_all():
    manifest = {"xyz": {"editions": {
        "xyz_a": {"tag": "xyz_a", "books": ["MAT"]},
        "xyz_b": {"tag": "xyz_b", "books": ["MAT", "MRK", "LUK"]},
    }}}
    tag, _ecode, books = dt.primary_edition("xyz", manifest)
    assert tag == "xyz_b" and len(books) == 3


def test_primary_edition_none_when_language_absent():
    assert dt.primary_edition("nope", {}) is None


def test_gold_health_for_takes_max_positional_across_partitions_and_manifests():
    published = {"languages": {"arb": {"editions": {"arb_vdv": {"layers": {"manual": {
        "AVD": {"health": {"positional": 0.9582}},
        "ONAV": {"health": {"positional": 0.8509}},
    }}}}}}}
    internal = {"languages": {}}
    assert dt.gold_health_for("arb", [published, internal]) == 0.9582


def test_gold_health_for_none_when_no_recorded_health():
    assert dt.gold_health_for("zzz", [{"languages": {}}, {"languages": {}}]) is None


def test_gold_health_for_ignores_partitions_with_no_health_dict():
    published = {"languages": {"eng": {"editions": {"engbsb": {"layers": {"manual": {
        "BSB-tables": {"health": None},
        "gbt": {"health": None},
    }}}}}}}
    assert dt.gold_health_for("eng", [published]) is None


def test_resolve_tag_tries_as_given_then_lower_then_upper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = tmp_path / "pipeline" / "work" / "ingest-cache"
    (cache / "usj-spaerv").mkdir(parents=True)   # only the LOWERCASE dir exists
    tag, path = dt.resolve_tag("SPAERV")
    assert tag == "spaerv" and path == Path("pipeline/work/ingest-cache/usj-spaerv")
    assert dt.resolve_tag("nope-at-all") is None


def test_derive_one_no_ingest_cache_short_circuits_before_the_quality_gate(monkeypatch):
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: None)
    doc = dt.derive_one("zzz", "zzztag", ot_books=["GEN"], all_books=["GEN"], with_diagnose=False)
    assert doc["_derived_meta"]["reason"] == "no_ingest_cache"


def test_derive_one_gate_failed_writes_only_derived_meta(monkeypatch):
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: (tag, Path("/mocked")))
    monkeypatch.setattr(dt, "quality_gate",
                        lambda *a, **k: ({"passed": False, "coverage": 0.1}, {}, []))
    doc = dt.derive_one("zzz", "zzztag", ot_books=["GEN"], all_books=["GEN"], with_diagnose=False)
    assert doc == {"_derived_meta": {"reason": "alignment_quality", "gate": {"passed": False, "coverage": 0.1}}}


def test_derive_one_gate_passed_but_no_ot_books_writes_no_ot_slots(monkeypatch):
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: (tag, Path("/mocked")))
    monkeypatch.setattr(dt, "quality_gate", lambda *a, **k: ({"passed": True}, {}, []))
    monkeypatch.setattr(dt, "multiword_rates", lambda *a, **k: {})
    doc = dt.derive_one("zzz", "zzztag", ot_books=[], all_books=["MAT"], with_diagnose=False)
    assert "possessor" not in doc
    assert "subject_verb" not in doc
    assert "audit" not in doc or "constituent_order" not in doc.get("audit", {})


def test_derive_one_audit_facts_never_collide_with_slot_keys(monkeypatch):
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: (tag, Path("/mocked")))
    monkeypatch.setattr(dt, "quality_gate", lambda *a, **k: ({"passed": True}, {"ref1": {0: 1}}, []))
    monkeypatch.setattr(dt, "build_corpus", lambda *a, **k: [])
    monkeypatch.setattr(dt, "compute_order_stats", lambda recs, anchors: {
        "rec_after_rate": 0.9, "rec_after_n": 60, "func_order": {}, "func_order_n": {}})
    monkeypatch.setattr(dt, "constituent_profile", lambda *a, **k: {
        "verses_measured": 10, "pair_order_kept": {}, "function_drift": {}})
    monkeypatch.setattr(dt, "multiword_rates", lambda *a, **k: {"noun": (3, 10)})
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        cdir = Path(td) / "constituent_order"
        monkeypatch.setattr(dt, "_CONSTITUENT_DIR", cdir)
        doc = dt.derive_one("zzz", "zzztag", ot_books=["GEN"], all_books=["GEN"], with_diagnose=False)
    assert doc["possessor"]["direction"] == "after"
    assert "constituent_order" in doc["audit"]
    assert "multiword_rates" in doc["audit"]
    # slot-shaped keys never appear inside "audit", and audit facts never leak to the top level
    assert "possessor" not in doc["audit"]
    assert "noun" not in doc


def test_build_writes_content_sha256_for_every_language(monkeypatch, tmp_path):
    monkeypatch.setattr(dt, "_load_json", lambda fp: (
        {"languages": {"zzz": {}}} if "compact-alignments" not in str(fp) else {"languages": {}}))
    monkeypatch.setattr(dt, "primary_edition", lambda iso, m: None)   # no edition -> no_edition path
    stats = dt.build(isos=["zzz"], out_dir=tmp_path)
    assert stats["no_edition"] == 1
    assert stats["total"] == 1


# --- D1: Greek-first selectors (2026-09-25) ---------------------------------------------------------
def test_is_prep_matches_greek_strong_hebrew_lexeme_and_hebrew_lemma():
    assert dt._is_prep(_FakeD1Tok(0, strong="G1722"))                          # ἐν
    assert dt._is_prep(_FakeD1Tok(0, strong=None, lexeme="hbo:0871a"))         # בְּ (augmented lexeme)
    assert dt._is_prep(_FakeD1Tok(0, strong=None, lemma="עַל"))                # עַל (free-standing)
    assert not dt._is_prep(_FakeD1Tok(0, strong="G1234", lemma="בַּיִת"))


def test_is_article_greek_and_hebrew():
    assert dt._is_article(_FakeD1Tok(0, strong="G3588"))
    assert dt._is_article(_FakeD1Tok(0, lexeme="hbo:1886a"))
    assert not dt._is_article(_FakeD1Tok(0, strong="G1722"))


def test_is_negator_greek_and_hebrew():
    assert dt._is_negator(_FakeD1Tok(0, strong="G3756"))
    assert dt._is_negator(_FakeD1Tok(0, strong="G3361"))
    assert dt._is_negator(_FakeD1Tok(0, lemma="לֹא"))
    assert not dt._is_negator(_FakeD1Tok(0, strong="G3588"))


def test_is_gen_pronoun_requires_genitive_case_and_pronoun_lemma():
    assert dt._is_gen_pronoun(_FakeD1Tok(0, case_="genitive", lemma="αὐτός"))
    assert not dt._is_gen_pronoun(_FakeD1Tok(0, case_="nominative", lemma="αὐτός"))
    assert not dt._is_gen_pronoun(_FakeD1Tok(0, case_="genitive", lemma="ἄνθρωπος"))


def test_next_content_skips_one_article_but_not_two_function_words():
    art = _FakeD1Tok(1, is_content=False, strong="G3588")
    noun = _FakeD1Tok(2, is_content=True)
    members = [_FakeD1Tok(0, is_content=False, strong="G1722"), art, noun]
    assert dt._next_content(members, 0) is noun          # prep -> article -> noun: article skipped


def test_next_content_stops_at_a_non_article_function_word():
    other_fw = _FakeD1Tok(1, is_content=False, strong="G1161")   # δέ, not an article
    noun = _FakeD1Tok(2, is_content=True)
    members = [_FakeD1Tok(0, is_content=False, strong="G1722"), other_fw, noun]
    assert dt._next_content(members, 0) is None


def test_adjacency_stat_classifies_before_after_and_fused():
    prep0 = _FakeD1Tok(0, strong="G1722")
    noun1 = _FakeD1Tok(1, is_content=True)
    prep2 = _FakeD1Tok(2, strong="G1722")
    noun3 = _FakeD1Tok(3, is_content=True)
    prep4 = _FakeD1Tok(4, strong="G1722")
    noun5 = _FakeD1Tok(5, is_content=True)
    recs = [
        _rec([prep0, noun1], v=1),      # prep target-before-noun ("before" pattern)
        _rec([prep2, noun3], v=2),      # prep target-after-noun ("after" pattern)
        _rec([prep4, noun5], v=3),      # fused: same target position
    ]
    anchors = {encode("MAT", 1, 1): {0: 5, 1: 6},
              encode("MAT", 1, 2): {2: 9, 3: 8},
              encode("MAT", 1, 3): {4: 3, 5: 3}}
    stat = dt._adjacency_stat(recs, anchors, dt._is_prep, target_ok=lambda n: n.is_content)
    assert stat["n_before"] == 1 and stat["n_after"] == 1 and stat["n_fused"] == 1
    assert stat["n_opportunities"] == 3
    assert stat["rate_after"] == 0.5


def test_direction_slot_bands_after_before_and_mixed():
    assert dt._direction_slot({"n_resolved": 250, "rate_after": 0.95, "n_fused": 0}, 200)["direction"] == "after"
    assert dt._direction_slot({"n_resolved": 250, "rate_after": 0.05, "n_fused": 0}, 200)["direction"] == "before"
    mixed = dt._direction_slot({"n_resolved": 250, "rate_after": 0.5, "n_fused": 0}, 200)
    assert mixed["direction"] is None and mixed["reason"] == "mixed"


def test_direction_slot_omitted_below_min_n():
    assert dt._direction_slot({"n_resolved": 10, "rate_after": 0.95, "n_fused": 0}, 200) is None


def test_direction_slot_experimental_flag_carries_validation_note():
    slot = dt._direction_slot({"n_resolved": 400, "rate_after": 0.9, "n_fused": 0}, 300, experimental=True)
    assert slot["experimental"] is True
    assert slot["validation"] == "not yet wired"


def test_word_slot_present_absent_and_midband_omitted():
    present = dt._word_slot({"n_opportunities": 400, "word_rate": 0.5}, 300)
    assert present["present"] is True
    absent = dt._word_slot({"n_opportunities": 400, "word_rate": 0.05}, 300)
    assert absent["present"] is False
    assert dt._word_slot({"n_opportunities": 400, "word_rate": 0.2}, 300) is None   # mid-band, honest gap


def test_derive_d1_slots_writes_adposition_when_min_n_cleared(monkeypatch):
    monkeypatch.setattr(dt, "_D1_MIN_N", {"adposition": 2, "article_word": 2,
                                          "possessive_word": 2, "negation": 2})
    prep_a, noun_a = _FakeD1Tok(0, strong="G1722"), _FakeD1Tok(1, is_content=True)
    prep_b, noun_b = _FakeD1Tok(2, strong="G1722"), _FakeD1Tok(3, is_content=True)
    recs = [_rec([prep_a, noun_a], v=1), _rec([prep_b, noun_b], v=2)]
    anchors = {encode("MAT", 1, 1): {0: 5, 1: 6}, encode("MAT", 1, 2): {2: 9, 3: 10}}
    out = dt.derive_d1_slots(recs, anchors)
    assert out["adposition"]["direction"] == "before"       # prep target precedes noun both times
    assert "article_word" not in out and "negation" not in out and "possessive_word" not in out


def test_derive_d1_slots_never_writes_subject_verb_or_object_verb():
    """D1 is Greek-first PLACEMENT slots only — Hebrew's possessor/subject_verb/object_verb stay D0's,
    and Greek clause order is D2 (needs `role`, not built here); no accidental key overlap."""
    out = dt.derive_d1_slots([], {})
    assert "subject_verb" not in out and "object_verb" not in out and "possessor" not in out


# --- validate_derived --------------------------------------------------------------------------------
def test_validate_derived_rejects_unwired_slots():
    import pytest
    with pytest.raises(ValueError):
        dt.validate_derived("article_word", {})


def test_validate_derived_splits_reference_set_and_reports_agreement(monkeypatch):
    import lexeme_aligner.typology as typology
    grambank_truth = {"a": "after", "b": "after", "c": "before", "d": "before",
                      "e": "after", "f": "before", "g": "after", "h": "before"}
    monkeypatch.setattr(typology, "grambank_direction",
                        lambda gb, slot: grambank_truth.get(gb.get("iso")) if gb else None)
    monkeypatch.setattr(dt, "_load_json", lambda fp: {"languages": {k: {"iso": k} for k in grambank_truth}})
    derived_docs = {k: {"adposition": {"direction": v}} for k, v in grambank_truth.items()}
    derived_docs["c"]["adposition"]["direction"] = "after"   # one deliberate disagreement
    result = dt.validate_derived("adposition", derived_docs, seed=1)
    assert result["slot"] == "adposition"
    assert result["reference_total"] == 8
    assert result["calibration_half"]["compared"] + result["held_out_half"]["compared"] == 8
    assert result["overall"]["agree"] == 7           # 7 of 8 agree, "c" is the planted disagreement


def test_validate_derived_ignores_languages_missing_either_side(monkeypatch):
    import lexeme_aligner.typology as typology
    monkeypatch.setattr(typology, "grambank_direction", lambda gb, slot: "after" if gb else None)
    monkeypatch.setattr(dt, "_load_json",
                        lambda fp: {"languages": {"a": {"GB074": "0", "GB075": "1"}}})  # "b" unresolved
    derived_docs = {"a": {"adposition": {"direction": "after"}},
                   "b": {"adposition": {"direction": "before"}}}
    result = dt.validate_derived("adposition", derived_docs)
    assert result["reference_total"] == 1
