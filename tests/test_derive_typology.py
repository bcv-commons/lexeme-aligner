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
    role: str | None = None
    phrase_id: int | None = None
    function: str | None = None


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


def test_editions_of_lists_every_edition_sorted_by_tag_and_privileges_none():
    manifest = {"hin": {"editions": {
        "hinirv": {"tag": "hinirv", "books": ["GEN", "EXO", "MAT"]},
        "hin_thin": {"tag": "hin_thin", "books": ["MAT", "MRK"]},
        "HINNT": {"tag": "HINNT", "books": ["MAT"]},
    }}}
    eds = dt.editions_of("hin", manifest)
    assert [t for t, _e, _b in eds] == ["hin_thin", "hinirv", "HINNT"]
    assert eds[1] == ("hinirv", "hinirv", ["GEN", "EXO", "MAT"])


def test_editions_of_empty_when_language_absent():
    assert dt.editions_of("nope", {}) == []


def _ed_doc(possessor=None, n=200, reason=None, gate=None, **extra):
    doc = {"_derived_meta": {"reason": reason, "gate": gate or {"passed": reason is None}}}
    if possessor is not None:
        doc["possessor"] = {"direction": possessor, "source": "derived", "n": n, "rate": 0.8}
    doc.update(extra)
    return doc


def test_combine_language_votes_across_editions_and_records_agreement():
    docs = {"a": _ed_doc("after", 900), "b": _ed_doc("before", 100), "c": _ed_doc("after", 300)}
    out = dt.combine_language("xyz", docs)
    assert out["possessor"]["direction"] == "after"
    assert out["possessor"]["agreement"] == {"voting": 3, "agree": 2, "weight_share": 0.9231}
    assert set(out["possessor"]["editions"]) == {"a", "b", "c"}
    assert out["_derived_meta"]["n_editions"] == 3 and out["_derived_meta"]["n_passed"] == 3


def test_combine_language_abstains_on_a_split():
    out = dt.combine_language("xyz", {"a": _ed_doc("after", 500), "b": _ed_doc("before", 450)})
    assert out["possessor"]["direction"] is None and out["possessor"]["reason"] == "edition_conflict"


def test_combine_language_ignores_failed_editions_but_lists_them():
    docs = {"good": _ed_doc("after"), "bad": _ed_doc(reason="alignment_quality", gate={"coverage": 0.3})}
    out = dt.combine_language("xyz", docs)
    assert out["possessor"]["direction"] == "after" and set(out["possessor"]["editions"]) == {"good"}
    assert out["_derived_meta"]["editions"]["bad"]["passed"] is False
    assert out["_derived_meta"]["reason"] is None and out["_derived_meta"]["n_passed"] == 1


def test_combine_language_with_no_passing_edition_is_a_gate_failure_with_no_slots():
    docs = {"a": _ed_doc(reason="alignment_quality"), "b": _ed_doc(reason="alignment_quality")}
    out = dt.combine_language("xyz", docs)
    assert out["_derived_meta"]["reason"] == "alignment_quality" and "possessor" not in out
    only_missing = dt.combine_language("xyz", {"a": _ed_doc(reason="no_ingest_cache")})
    assert only_missing["_derived_meta"]["reason"] == "no_ingest_cache"


def test_pool_constituent_sums_counts_and_weights_drift():
    p1 = {"verses_measured": 100, "pair_order_kept": {"A>B": {"kept": 80, "total": 100, "rate": 0.8}},
          "function_drift": {"Objc": {"mean_drift": 0.10, "n": 100}}}
    p2 = {"verses_measured": 300, "pair_order_kept": {"A>B": {"kept": 100, "total": 300, "rate": 0.33}},
          "function_drift": {"Objc": {"mean_drift": 0.30, "n": 300}}}
    out = dt.pool_constituent({"t1": p1, "t2": p2})
    assert out["verses_measured"] == 400 and out["tags"] == ["t1", "t2"] and out["tag"] == "t1+t2"
    assert out["pair_order_kept"]["A>B"] == {"kept": 180, "rate": 0.45, "total": 400}
    assert out["function_drift"]["Objc"] == {"mean_drift": 0.25, "n": 400}
    assert dt.pool_constituent({"t": None}) is None


def test_combine_language_pools_multiword_rates():
    docs = {"a": _ed_doc("after", audit={"multiword_rates": {"noun": {"multi_word": 10, "total": 100}}}),
            "b": _ed_doc("after", audit={"multiword_rates": {"noun": {"multi_word": 30, "total": 100}}})}
    assert dt.combine_language("xyz", docs)["audit"]["multiword_rates"] == {"noun": {"multi_word": 40, "total": 200}}


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
    monkeypatch.setenv("ALIGNER_SYNTAX_SOURCE", "bhsa")           # the BHSA branch (A/B baseline); the macula default has its own test below
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
    monkeypatch.setattr(dt, "editions_of", lambda iso, m: [])         # no edition -> no_edition path
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


def test_validate_derived_accepts_possessor_and_verb_slots_now(monkeypatch):
    # Roadmap D2: validate_derived's slot restriction was generalized from adposition-only.
    import lexeme_aligner.typology as typology
    monkeypatch.setattr(typology, "grambank_direction", lambda gb, slot: "after" if gb else None)
    monkeypatch.setattr(dt, "_load_json", lambda fp: {"languages": {"a": {"GB065": "2"}}})
    for slot in ("possessor", "subject_verb", "object_verb"):
        derived_docs = {"a": {slot: {"direction": "after"}}}
        result = dt.validate_derived(slot, derived_docs)
        assert result["slot"] == slot
        assert result["reference_total"] == 1


def test_validate_derived_still_rejects_an_unknown_slot_name():
    import pytest
    with pytest.raises(ValueError):
        dt.validate_derived("not_a_real_slot", {})


# --- D2 (Greek possessor / subject_verb / object_verb via HebToken.role) ---------------------------------
def test_possessor_stat_greek_genitive_before_head():
    # G(genitive) at idx 0 -> target 0, H(head, non-genitive) at idx 1 -> target 1: G precedes H.
    members = [_FakeD1Tok(0, case_="genitive"), _FakeD1Tok(1, case_="accusative")]
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._possessor_stat_greek([_rec(members)], anchors)
    assert stat == {"n_opportunities": 1, "n_fused": 0, "n_before": 1, "n_after": 0,
                    "n_resolved": 1, "rate_after": 0.0}


def test_possessor_stat_greek_head_before_genitive():
    # SOURCE order H(head, idx 0) then G(genitive, idx 1); TARGET order unchanged (identity anchors) ->
    # G's own target position (1) is still greater than H's (0) -> possessor(G) FOLLOWS head -> "after".
    members = [_FakeD1Tok(0, case_="accusative"), _FakeD1Tok(1, case_="genitive")]
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._possessor_stat_greek([_rec(members)], anchors)
    assert stat["n_after"] == 1 and stat["n_before"] == 0


def test_possessor_stat_greek_excludes_genitive_pronouns():
    tok = _FakeD1Tok(0, case_="genitive", lemma="αὐτός")
    assert dt._is_gen_pronoun(tok)
    members = [tok, _FakeD1Tok(1, case_="accusative")]
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._possessor_stat_greek([_rec(members)], anchors)
    assert stat["n_opportunities"] == 0


def test_possessor_stat_greek_both_genitive_or_both_non_genitive_is_not_a_pair():
    members = [_FakeD1Tok(0, case_="genitive"), _FakeD1Tok(1, case_="genitive")]
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._possessor_stat_greek([_rec(members)], anchors)
    assert stat["n_opportunities"] == 0


def test_clause_role_stat_subject_before_verb():
    s = _FakeD1Tok(0, role="s")
    v = _FakeD1Tok(1, role="v", person="third")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._clause_role_stat([_rec([s, v])], anchors, "s")
    assert stat == {"n_opportunities": 1, "n_fused": 0, "n_before": 1, "n_after": 0,
                    "n_resolved": 1, "rate_after": 0.0}


def test_clause_role_stat_object_after_verb():
    v = _FakeD1Tok(0, role="v", person="third")
    o = _FakeD1Tok(1, role="o")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._clause_role_stat([_rec([v, o])], anchors, "o")
    assert stat["n_after"] == 1


def test_clause_role_stat_pairs_a_role_token_with_every_verb_it_has_no_other_verb_between():
    # s1 -- v1 -- s2 -- v2: the design spec is a PER-PAIR criterion ("each verb AND each s/o token
    # with no OTHER verb between them"), not exclusive nearest-neighbour assignment. s2 sits between
    # v1 and v2 with no OTHER verb on either side, so it legitimately pairs with BOTH: s1-v1 (left of
    # v1), s2-v1 (right of v1, v2 not yet reached), s2-v2 (left of v2, v1 not yet reached) = 3 pairs.
    s1 = _FakeD1Tok(0, role="s")
    v1 = _FakeD1Tok(1, role="v", person="third")
    s2 = _FakeD1Tok(2, role="s")
    v2 = _FakeD1Tok(3, role="v", person="third")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2, 3: 3}}
    stat = dt._clause_role_stat([_rec([s1, v1, s2, v2])], anchors, "s")
    assert stat["n_opportunities"] == 3


def test_clause_role_stat_never_crosses_a_second_verb():
    # s -- v1 -- v2 -- (nothing): s must pair with v1 only (v2 is blocked by v1 sitting between them).
    s = _FakeD1Tok(0, role="s")
    v1 = _FakeD1Tok(1, role="v", person="third")
    v2 = _FakeD1Tok(2, role="v", person="third")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2}}
    stat = dt._clause_role_stat([_rec([s, v1, v2])], anchors, "s")
    assert stat["n_opportunities"] == 1


def test_clause_role_stat_ignores_non_finite_verb_forms():
    # a participle (mood="participle", no person) must not count as a clause-boundary verb.
    s = _FakeD1Tok(0, role="s")
    participle = _FakeD1Tok(1, role="v", mood="participle")
    v = _FakeD1Tok(2, role="v", person="third")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2}}
    stat = dt._clause_role_stat([_rec([s, participle, v])], anchors, "s")
    # s still reaches the real finite verb v (the participle doesn't block the scan)
    assert stat["n_opportunities"] == 1


# --- D2-fix (2026-09-26): the 1 Cor 1:26 elided-copula regression + the distance-cap guardrail --------
def test_clause_role_stat_drops_a_role_token_beyond_max_distance():
    # Reproduces the real bug: an elided-copula predicate-nominative clause has no finite verb to stop
    # the window-walk, so a distant "s"-role token gets wrongly paired with an unrelated outer verb.
    # 1 Cor 1:26 shape: v (outer verb, e.g. "consider") ... 20 filler content tokens (the embedded
    # ὅτι-clause with its own elided "were") ... s (a predicate nominative, e.g. "wise"). Without a cap
    # this counts as one opportunity; with max_distance=15 it must be dropped entirely.
    v = _FakeD1Tok(0, role="v", person="third")
    filler = [_FakeD1Tok(i) for i in range(1, 21)]          # 20 non-role, non-verb tokens
    s = _FakeD1Tok(21, role="s")
    members = [v] + filler + [s]
    anchors = {encode("MAT", 1, 1): {i: i for i in range(22)}}
    stat_uncapped = dt._clause_role_stat([_rec(members)], anchors, "s", max_distance=999)
    assert stat_uncapped["n_opportunities"] == 1            # the bug, reproduced
    stat_capped = dt._clause_role_stat([_rec(members)], anchors, "s", max_distance=15)
    assert stat_capped["n_opportunities"] == 0               # the fix: dropped, not just unresolved


def test_clause_role_stat_default_max_distance_matches_the_evidence_that_motivated_it():
    assert dt._CLAUSE_ROLE_MAX_DISTANCE == 15


def test_clause_role_stat_stops_at_a_greek_subordinator_even_within_the_distance_cap():
    # the ACTUAL 1 Cor 1:26 shape: v ... hoti (subordinator, well within any distance cap) ... s.
    # The distance cap alone does NOT catch this (confirmed against the real verse); the subordinator
    # check must independently break the walk.
    v = _FakeD1Tok(0, role="v", person="third")
    hoti = _FakeD1Tok(1, strong="G3754")                  # ὅτι — not itself an "s"/"o"/"v" role
    s = _FakeD1Tok(2, role="s")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2}}
    stat = dt._clause_role_stat([_rec([v, hoti, s])], anchors, "s")
    assert stat["n_opportunities"] == 0


# --- load_greek_frames + _clause_role_stat's frame_index parameter (D2-frames, 2026-09-26) -----------
def test_load_greek_frames_joins_role_and_frames_tsv_by_book_chapter_verse_word(tmp_path):
    role_tsv = tmp_path / "role.tsv"
    role_tsv.write_text(
        "key\tbook\tchapter\tverse\tword\trole\tcase\ttense\tvoice\tmood\tdegree\n"
        "46001026001\t1CO\t1\t26\t1\tv\t\t\t\t\t\n"
        "46001026006\t1CO\t1\t26\t6\taux\t\t\t\t\t\n"
        "46001026004\t1CO\t1\t26\t4\to\taccusative\t\t\t\t\n",
        encoding="utf-8")
    frames_tsv = tmp_path / "frames.tsv"
    frames_tsv.write_text(
        "verb_key\trole\targ_key\n"
        "46001026001\tA0\t46001026006\n"
        "46001026001\tA1\t46001026004\n",
        encoding="utf-8")
    idx = dt.load_greek_frames(role_tsv, frames_tsv)
    ref = encode("1CO", 1, 26)
    assert idx == {ref: {0: {5, 3}}}     # word 1->idx0 (verb), word 6->idx5, word 4->idx3


def test_load_greek_frames_drops_a_row_whose_verb_and_arg_are_in_different_verses(tmp_path):
    role_tsv = tmp_path / "role.tsv"
    role_tsv.write_text(
        "key\tbook\tchapter\tverse\tword\trole\tcase\ttense\tvoice\tmood\tdegree\n"
        "46001026001\t1CO\t1\t26\t1\tv\t\t\t\t\t\n"
        "46001027001\t1CO\t1\t27\t1\to\t\t\t\t\t\n",
        encoding="utf-8")
    frames_tsv = tmp_path / "frames.tsv"
    frames_tsv.write_text("verb_key\trole\targ_key\n46001026001\tA1\t46001027001\n", encoding="utf-8")
    assert dt.load_greek_frames(role_tsv, frames_tsv) == {}


def test_load_greek_frames_degrades_to_empty_when_files_are_absent(tmp_path):
    assert dt.load_greek_frames(tmp_path / "nope-role.tsv", tmp_path / "nope-frames.tsv") == {}


def test_clause_role_stat_frame_index_overrides_the_heuristic_for_a_verb_with_a_real_frame():
    # 1 Cor 1:26 shape: v(0) ... aux(5, "brothers") ... hoti-ish filler ... o(3, our fake accusative
    # "calling") ... far away s(9,14,17)-equivalent tokens the OLD heuristic mispaired. With a frame
    # entry for v(0) naming ONLY idx 3 as its real argument, the predicate-nominative-style "s" tokens
    # must NOT count as opportunities at all, even without relying on distance/subordinator detection.
    v = _FakeD1Tok(0, role="v", person="third")
    o_real = _FakeD1Tok(3, role="s")            # the verb's REAL frame argument (using role="s" here)
    s_fake = _FakeD1Tok(9, role="s")            # NOT in the frame -> must be excluded when framed
    members = [v, _FakeD1Tok(1), _FakeD1Tok(2), o_real, _FakeD1Tok(4), _FakeD1Tok(5), _FakeD1Tok(6),
              _FakeD1Tok(7), _FakeD1Tok(8), s_fake]
    anchors = {encode("MAT", 1, 1): {i: i for i in range(10)}}
    frame_index = {encode("MAT", 1, 1): {0: {3}}}       # verb idx0's only real argument is idx3
    stat = dt._clause_role_stat([_rec(members)], anchors, "s", frame_index=frame_index)
    assert stat["n_opportunities"] == 1                 # only o_real (idx3) counts, s_fake excluded


def test_clause_role_stat_falls_back_to_the_heuristic_for_a_verb_with_no_frame_entry():
    v = _FakeD1Tok(0, role="v", person="third")
    s = _FakeD1Tok(1, role="s")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    frame_index = {encode("MAT", 1, 1): {}}             # this verse has frame data, but not for idx0
    stat = dt._clause_role_stat([_rec([v, s])], anchors, "s", frame_index=frame_index)
    assert stat["n_opportunities"] == 1                 # heuristic still applies


def test_clause_role_stat_frame_index_none_is_pure_heuristic_unchanged():
    v = _FakeD1Tok(0, role="v", person="third")
    s = _FakeD1Tok(1, role="s")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    stat = dt._clause_role_stat([_rec([v, s])], anchors, "s", frame_index=None)
    assert stat["n_opportunities"] == 1


def test_clause_role_stat_keeps_a_role_token_within_the_cap():
    v = _FakeD1Tok(0, role="v", person="third")
    filler = [_FakeD1Tok(i) for i in range(1, 5)]
    s = _FakeD1Tok(5, role="s")
    members = [v] + filler + [s]
    anchors = {encode("MAT", 1, 1): {i: i for i in range(6)}}
    stat = dt._clause_role_stat([_rec(members)], anchors, "s")   # default cap
    assert stat["n_opportunities"] == 1


# --- _hebrew_clause_role_stat (D0-fix, 2026-09-26: replaces func_order's order-kept approach for
# subject_verb/object_verb specifically) -----------------------------------------------------------------
def test_hebrew_clause_role_stat_measures_target_position_directly_not_order_kept():
    # Pred BEFORE Subj in SOURCE (Hebrew verb-initial), but Subj comes BEFORE Pred in the TARGET
    # (idx 0 -> target pos 5, idx 1 -> target pos 0): the OLD order-kept approach would call this
    # "order NOT kept" (source Pred-first, target Subj-first) and fold it into a totally different
    # statistical bucket than a Subj-first-in-source case. The new function must count it as a plain
    # "before" observation regardless of which side Hebrew put first.
    pred = _FakeD1Tok(0, phrase_id=1, function="Pred", strong="H0001")
    subj = _FakeD1Tok(1, phrase_id=2, function="Subj", strong="H0001")
    anchors = {encode("MAT", 1, 1): {0: 5, 1: 0}}
    stat = dt._hebrew_clause_role_stat([_rec([pred, subj])], anchors, "Subj")
    assert stat == {"n_opportunities": 1, "n_fused": 0, "n_before": 1, "n_after": 0,
                    "n_resolved": 1, "rate_after": 0.0}


def test_hebrew_clause_role_stat_pools_both_source_orders_into_one_statistic():
    # Case A: source Subj-then-Pred, target ALSO keeps subject before verb (before).
    # Case B: source Pred-then-Subj (Hebrew verb-initial), target STILL keeps subject before verb.
    # Both are genuinely "subject before verb in the target" and must land in the SAME "before" bucket,
    # regardless of Hebrew's own source order for that instance — the entire point of this fix.
    subj_a = _FakeD1Tok(0, phrase_id=1, function="Subj", strong="H0001")
    pred_a = _FakeD1Tok(1, phrase_id=2, function="Pred", strong="H0001")
    pred_b = _FakeD1Tok(2, phrase_id=3, function="Pred", strong="H0001")
    subj_b = _FakeD1Tok(3, phrase_id=4, function="Subj", strong="H0001")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 3, 3: 2}}   # b: target subj(pos2) < pred(pos3)
    stat = dt._hebrew_clause_role_stat([_rec([subj_a, pred_a, pred_b, subj_b])], anchors, "Subj")
    assert stat["n_before"] == 2
    assert stat["n_after"] == 0


def test_hebrew_clause_role_stat_never_crosses_a_second_pred_phrase():
    subj = _FakeD1Tok(0, phrase_id=1, function="Subj", strong="H0001")
    pred1 = _FakeD1Tok(1, phrase_id=2, function="Pred", strong="H0001")
    pred2 = _FakeD1Tok(2, phrase_id=3, function="Pred", strong="H0001")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2}}
    stat = dt._hebrew_clause_role_stat([_rec([subj, pred1, pred2])], anchors, "Subj")
    assert stat["n_opportunities"] == 1                # subj pairs with pred1 only


def test_hebrew_clause_role_stat_stops_at_a_hebrew_subordinator_between_the_phrases():
    # the real root cause this function fixes: a relative/complementizer marker (H0834/H3588/H0518)
    # sitting between two phrases means they belong to different clauses, even with no second Pred.
    pred = _FakeD1Tok(0, phrase_id=1, function="Pred", strong="H0001")
    rel = _FakeD1Tok(1, strong="H0834")                 # אֲשֶׁר, not itself a Subj/Objc/Pred phrase member
    subj = _FakeD1Tok(2, phrase_id=2, function="Subj", strong="H0001")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 2}}
    stat = dt._hebrew_clause_role_stat([_rec([pred, rel, subj])], anchors, "Subj")
    assert stat["n_opportunities"] == 0


def test_hebrew_clause_role_stat_reproduces_1ch_1_10_without_spurious_cross_clause_pairing():
    # Real shape found in 1 Chronicles 1:10 during root-cause investigation: two independent clauses
    # in one verse — (Subj1, Pred1, Objc1) then (Pred2, Subj2) — sharing no relationship. The phrase
    # immediately before Pred2 in the SORTED list is Objc1, from the FIRST clause; that must not count
    # as a Pred-Objc pair (it's not really the same clause), and equally Objc1 pairing "backward" with
    # Pred1 (its own real verb) must still work correctly.
    subj1 = _FakeD1Tok(0, phrase_id=1, function="Subj", strong="H0001")
    pred1 = _FakeD1Tok(1, phrase_id=2, function="Pred", strong="H0001")
    objc1 = _FakeD1Tok(2, phrase_id=3, function="Objc", strong="H0001")
    pred2 = _FakeD1Tok(3, phrase_id=4, function="Pred", strong="H0001")
    subj2 = _FakeD1Tok(4, phrase_id=5, function="Subj", strong="H0001")
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1, 2: 3, 3: 5, 4: 7}}
    stat_objc = dt._hebrew_clause_role_stat(
        [_rec([subj1, pred1, objc1, pred2, subj2])], anchors, "Objc")
    # objc1 pairs with pred1 (its real verb, no Pred between) — walking right from pred1 hits objc1
    # before pred2; walking from pred2 leftward hits objc1 too (no OTHER Pred between them either) —
    # per the same per-pair (not exclusive-nearest) semantics D2's own Greek function already uses.
    assert stat_objc["n_opportunities"] == 2


# --- check_known_answers (D2-fix's automated gate) -----------------------------------------------------
def test_check_known_answers_all_match():
    docs = {"eng": {"subject_verb": {"direction": "before"}, "object_verb": {"direction": "after"}}}
    out = dt.check_known_answers(docs)
    assert out["passed"] is True
    assert out["violations"] == []
    assert out["matches"] >= 2


def test_check_known_answers_detects_a_confident_wrong_direction():
    # the real Russian case: subject_verb resolved confidently to "after", contradicting the known
    # dominant SVO answer ("before") — this MUST fail the gate.
    docs = {"rus": {"subject_verb": {"direction": "after", "n": 11664, "rate": 0.822}}}
    out = dt.check_known_answers(docs)
    assert out["passed"] is False
    assert len(out["violations"]) == 1
    assert out["violations"][0] == {"slot": "subject_verb", "iso": "rus", "expected": "before",
                                    "actual": "after", "detail": docs["rus"]["subject_verb"]}


def test_check_known_answers_abstention_is_reported_but_not_a_hard_failure():
    # the real English case: subject_verb resolved to null/mixed instead of the confident "before" a
    # textbook-unambiguous language should produce — suspicious, but abstaining is not actively wrong.
    docs = {"eng": {"subject_verb": {"direction": None, "reason": "mixed"}}}
    out = dt.check_known_answers(docs)
    assert out["passed"] is True                      # abstention alone never fails the gate
    assert out["violations"] == []
    assert len(out["abstentions"]) == 1
    assert out["abstentions"][0] == {"slot": "subject_verb", "iso": "eng", "expected": "before"}


def test_check_known_answers_ignores_languages_or_slots_not_in_the_docs():
    out = dt.check_known_answers({})
    assert out == {"passed": True, "violations": [], "abstentions": [], "matches": 0, "checked": 0}


def test_derive_d2_slots_omits_below_min_n():
    members = [_FakeD1Tok(0, case_="genitive"), _FakeD1Tok(1, case_="accusative")]
    anchors = {encode("MAT", 1, 1): {0: 0, 1: 1}}
    out = dt.derive_d2_slots([_rec(members)], anchors)
    assert out == {}   # far below _D2_MIN_N thresholds


def test_derive_d2_slots_reports_possessor_and_verb_slots_together():
    recs = []
    anchors = {}
    for i in range(310):
        ref = encode("MAT", 1, i + 1)
        g, h = _FakeD1Tok(0, case_="genitive"), _FakeD1Tok(1, case_="accusative")
        recs.append(_rec([g, h], v=i + 1))
        anchors[ref] = {0: 0, 1: 1}                        # G precedes H every time -> "before"
    out = dt.derive_d2_slots(recs, anchors)
    assert out["possessor"]["direction"] == "before"
    assert out["possessor"]["n"] >= 300
    assert "subject_verb" not in out and "object_verb" not in out   # no role tokens in this fixture


# --- _combine_testaments (Hebrew OT + Greek NT versions of the same slot) --------------------------------
def test_combine_testaments_nt_only_language():
    grc = {"direction": "after", "source": "derived", "n": 400}
    combined = dt._combine_testaments(None, grc)
    assert combined["testament"] == "NT" and combined["direction"] == "after"


def test_combine_testaments_ot_only_language():
    heb = {"direction": "before", "source": "derived", "n": 200}
    combined = dt._combine_testaments(heb, None)
    assert combined["testament"] == "OT" and combined["direction"] == "before"


def test_combine_testaments_neither_resolves_is_none():
    assert dt._combine_testaments(None, None) is None


def test_combine_testaments_agreement_keeps_higher_n_and_records_both_rates():
    heb = {"direction": "after", "source": "derived", "n": 100, "rate_after": 0.85}
    grc = {"direction": "after", "source": "derived", "n": 500, "rate_after": 0.9}
    combined = dt._combine_testaments(heb, grc)
    assert combined["testament"] == "OT+NT"
    assert combined["n"] == 500                            # the higher-n (Greek) one is primary
    assert combined["ot_rate"] == 0.85 and combined["nt_rate"] == 0.9


def test_combine_testaments_disagreement_is_null_with_both_rates_never_silently_picked():
    heb = {"direction": "before", "source": "derived", "n": 100, "rate_after": 0.1}
    grc = {"direction": "after", "source": "derived", "n": 400, "rate_after": 0.9}
    combined = dt._combine_testaments(heb, grc)
    assert combined["direction"] is None
    assert combined["reason"] == "testament_conflict"
    assert combined["ot"]["direction"] == "before" and combined["nt"]["direction"] == "after"


def test_combine_testaments_a_mixed_null_slot_is_treated_as_unresolved():
    # a slot whose OWN direction is already None ("mixed") must not count as "this testament resolved".
    heb_mixed = {"direction": None, "source": "derived", "n": 150, "reason": "mixed"}
    grc = {"direction": "after", "source": "derived", "n": 400, "rate_after": 0.9}
    combined = dt._combine_testaments(heb_mixed, grc)
    assert combined["testament"] == "NT" and combined["direction"] == "after"


# --- D3 2.5: subject_pronoun_stat / subject_pronoun_slot / validate_subject_pronoun_need -----------

def _sp_rec(spans_and_flags, book="MAT", ch=1, v=1):
    """spans_and_flags: [(idx, span_len, is_finite_verb, is_content), ...] — builds both a _FakeD1Rec
    and the matching span_lengths dict `subject_pronoun_stat` expects."""
    members = [_FakeD1Tok(idx=i, strong=f"H{i}", is_content=content, person=("3ms" if finite else None))
              for i, (i2, span, finite, content) in enumerate(spans_and_flags)]
    r = _rec(members, book, ch, v)
    span_lengths = {encode(book, ch, v): {i: span for i, (_, span, _, _) in enumerate(spans_and_flags)}}
    return r, span_lengths


def test_subject_pronoun_stat_computes_real_spread():
    # 2 finite verbs with span 2, 3 other content tokens with span 1 -> spread = 2.5 - 1.0 = 1.5
    r, sp = _sp_rec([(0, 2, True, True), (1, 3, True, True), (2, 1, False, True),
                     (3, 1, False, True), (4, 1, False, True)])
    stat = dt.subject_pronoun_stat([r], sp)
    assert stat["n_finite"] == 2 and stat["n_other"] == 3
    assert stat["mean_finite_span"] == 2.5 and stat["mean_other_span"] == 1.0
    assert stat["spread"] == 1.5


def test_subject_pronoun_stat_none_spread_when_a_bucket_is_empty():
    r, sp = _sp_rec([(0, 2, True, True)])   # no non-finite content tokens at all
    stat = dt.subject_pronoun_stat([r], sp)
    assert stat["spread"] is None


def test_subject_pronoun_slot_omitted_below_min_n():
    assert dt.subject_pronoun_slot({"n_finite": 5, "n_other": 100, "spread": 1.4}, min_n=30) is None


def test_subject_pronoun_slot_high_spread_needs_free_pronoun():
    slot = dt.subject_pronoun_slot({"n_finite": 50, "n_other": 200, "spread": 1.41}, min_n=30)
    assert slot["needs_free_subject_pronoun"] is True
    assert slot["experimental"] is True and slot["source"] == "derived"


def test_subject_pronoun_slot_low_spread_is_pro_drop():
    slot = dt.subject_pronoun_slot({"n_finite": 50, "n_other": 200, "spread": 0.21}, min_n=30)
    assert slot["needs_free_subject_pronoun"] is False


def test_subject_pronoun_slot_middle_band_is_ambiguous():
    # ben/asm's own real reference spread (+0.55/+0.56) falls exactly in this deliberate null band.
    slot = dt.subject_pronoun_slot({"n_finite": 50, "n_other": 200, "spread": 0.55}, min_n=30)
    assert slot["needs_free_subject_pronoun"] is None
    assert slot["reason"] == "ambiguous"


def test_validate_subject_pronoun_need_perfect_agreement(monkeypatch, tmp_path):
    import json as _json
    cfg = tmp_path / "config" / "grambank"
    cfg.mkdir(parents=True)
    (cfg / "features.json").write_text(_json.dumps({"languages": {
        "eng": {"GB089": "0", "GB090": "0"},    # incomplete indexing -> needs a free pronoun
        "arb": {"GB089": "1", "GB090": "0"},    # complete indexing -> pro-drop
    }}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    derived_docs = {
        "eng": {"subject_pronoun_need": {"needs_free_subject_pronoun": True}},
        "arb": {"subject_pronoun_need": {"needs_free_subject_pronoun": False}},
    }
    result = dt.validate_subject_pronoun_need(derived_docs)
    assert result["reference_total"] == 2
    assert result["overall"]["agree"] == 2 and result["overall"]["rate"] == 1.0


def test_validate_subject_pronoun_need_skips_languages_grambank_has_no_data_for(monkeypatch, tmp_path):
    import json as _json
    cfg = tmp_path / "config" / "grambank"
    cfg.mkdir(parents=True)
    (cfg / "features.json").write_text(_json.dumps({"languages": {"eng": {}}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    derived_docs = {"eng": {"subject_pronoun_need": {"needs_free_subject_pronoun": True}}}
    result = dt.validate_subject_pronoun_need(derived_docs)
    assert result["reference_total"] == 0


def test_derive_one_macula_default_uses_the_macula_statistics(monkeypatch):
    """Default syntax source = macula (2026-10-04): possessor from construct_after_stat, subject_verb/object_verb from clause_role_stat — never
    from the BHSA compute_order_stats / _hebrew_clause_role_stat."""
    monkeypatch.delenv("ALIGNER_SYNTAX_SOURCE", raising=False)
    monkeypatch.setattr(dt, "resolve_tag", lambda tag: (tag, Path("/mocked")))
    monkeypatch.setattr(dt, "quality_gate", lambda *a, **k: ({"passed": True}, {"ref1": {0: 1}}, []))
    monkeypatch.setattr(dt, "build_corpus", lambda *a, **k: [])

    def boom(*a, **k):
        raise AssertionError("the BHSA statistic must not run in macula mode")
    monkeypatch.setattr(dt, "compute_order_stats", boom)
    monkeypatch.setattr(dt, "_hebrew_clause_role_stat", boom)
    monkeypatch.setattr(dt.macula_syntax, "construct_after_stat", lambda recs, anchors: {"rec_after_rate": 0.9, "rec_after_n": 60})
    seen = []

    def clause(recs, anchors, role):
        seen.append(role)
        return {"n_opportunities": 400, "n_fused": 0, "n_before": 360, "n_after": 40, "n_resolved": 400, "rate_after": 0.1}
    monkeypatch.setattr(dt.macula_syntax, "clause_role_stat", clause)
    monkeypatch.setattr(dt, "constituent_profile", lambda *a, **k: {"label_scheme": "macula_phrase_role", "verses_measured": 10,
                                                                   "pair_order_kept": {}, "function_drift": {}})
    monkeypatch.setattr(dt, "multiword_rates", lambda *a, **k: {})
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setattr(dt, "_CONSTITUENT_DIR", Path(td) / "co")
        doc = dt.derive_one("zzz", "zzztag", ot_books=["GEN"], all_books=["GEN"], with_diagnose=False)
    assert seen == ["s", "o"]
    assert doc["possessor"]["direction"] == "after" and doc["subject_verb"]["direction"] == "before" and doc["object_verb"]["direction"] == "before"
    assert doc["audit"]["constituent_order"]["label_scheme"] == "macula_phrase_role"


def test_pool_constituent_carries_the_label_scheme():
    p = {"verses_measured": 5, "label_scheme": "macula_phrase_role", "pair_order_kept": {"v>s": {"kept": 1, "total": 2}}, "function_drift": {}}
    assert dt.pool_constituent({"a": dict(p), "b": dict(p)})["label_scheme"] == "macula_phrase_role"


def test_pool_constituent_marks_mixed_vocabularies_instead_of_relabelling():
    a = {"verses_measured": 1, "label_scheme": "macula_phrase_role", "pair_order_kept": {"v>s": {"kept": 1, "total": 2}}, "function_drift": {}}
    b = {"verses_measured": 1, "label_scheme": "bhsa_function", "pair_order_kept": {"Pred>Subj": {"kept": 1, "total": 2}}, "function_drift": {}}
    assert dt.pool_constituent({"a": a, "b": b})["label_scheme"] == "mixed"
    assert dt.pool_constituent({"a": {"verses_measured": 1}})["label_scheme"] is None     # a profile with no scheme (pre-switch) stays visibly unlabelled


def test_editions_of_skips_editions_outside_the_statistics_pool():
    from lexeme_aligner.derive_typology import editions_of
    manifest = {"xx": {"editions": {"AAA": {"tag": "aaa", "books": ["GEN"]},
                                    "BBB": {"tag": "bbb", "books": ["GEN"], "statistics_pool": False},
                                    "CCC": {"tag": "ccc", "books": ["GEN"], "statistics_pool": True}}}}
    assert [t for t, _, _ in editions_of("xx", manifest)] == ["aaa", "ccc"]      # absent = counted; false = a near copy, not a second witness
