"""Offline tests for the opt-in LLM-alignment experiment (llm_prompt / llm_providers / llm_align).

Nothing here touches the network or needs an API key: the prompt layer is pure, the provider layer is
exercised through MockProvider/DryRunProvider and a fake client, and the driver runs end to end on a
synthetic corpus. Design: internal-docs/llm-align-experiment-plan.md.
"""
import pytest

from lexeme_aligner.hebrew_source import HebToken
from lexeme_aligner.llm_prompt import (
    SCHEMA_FULL, STRATEGIES, Decision, Packet, PROMPT_VERSION, derive_score, derive_score_full, normalize,
    normalize_full, prior_for, raw_from_lexeme, raw_from_verify, raw_from_verse, render_full_verse_suffix,
    render_lexeme_suffix, render_prefix, render_suffix, render_verse_suffix, review_notes_from, schema_for,
    seed_renderings)


def tok(idx, surface, strong, lexeme, lemma=None, content=True, gloss=None):
    return HebToken(idx, surface, strong, lexeme, lemma or surface, None, content, gloss_en=gloss)


def packet(strategy="gap", **kw):
    """MAT 1:1-like verse: h1 is to be decided; t0/t2/t3 are taken, t1/t5 are function words."""
    heb = [tok(0, "Βίβλος", "G0976", "grc:976", gloss="[The] book"),
           tok(1, "γενέσεως", "G1078", "grc:1078", "γένεσις", gloss="of [the] genealogy"),
           tok(2, "Ἰησοῦ", "G2424", "grc:2424", "Ἰησοῦς", gloss="of Jesus"),
           tok(3, "τοῦ", "G3588", "grc:3588", "ὁ", content=False)]
    base = dict(strategy=strategy, ref=40001001, book="MAT", ch=1, v=1, label="fra, edition fra-lsg",
                toks=["Généalogie", "de", "Jésus", "Christ", "fils", "de", "David"], heb=heb,
                decide=[1], allowed=[4, 6], soft=[1, 5], taken=[0, 2, 3],
                resolved={0: [0], 2: [2, 3]})
    base.update(kw)
    return Packet(**base)


# ── prefix ─────────────────────────────────────────────────────────────────────────────────────────────

def test_prefix_is_byte_deterministic():
    assert render_prefix("fra", "French", "gap") == render_prefix("fra", "French", "gap")


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_prefix_renders_for_every_strategy(strategy):
    pre = render_prefix("fra", "French", strategy)
    assert PROMPT_VERSION in pre and "French" in pre and f"## Strategy: {strategy}" in pre


def test_prefix_differs_by_language_strategy_and_conventions():
    base = render_prefix("fra", "French", "gap")
    assert base != render_prefix("hin", "Hindi", "gap")
    assert base != render_prefix("fra", "French", "verify")
    assert base != render_prefix("fra", "French", "gap", "elision splits l' d' qu'")
    assert "elision splits" in render_prefix("fra", "French", "gap", "elision splits l' d' qu'")


def test_prefix_rejects_an_unknown_strategy():
    with pytest.raises(ValueError):
        render_prefix("fra", "French", "nonsense")


def test_prefix_carries_no_run_specific_text():
    """A cache is a prefix match: nothing that varies per verse or per run may appear in it."""
    pre = render_prefix("fra", "French", "gap-seeded")
    for needle in ("REF 4", "2026-", "run_id"):
        assert needle not in pre


def test_schemas_are_strict():
    for s in (schema_for("gap"), schema_for("lexeme-grouped"), schema_for("verify")):
        assert s["additionalProperties"] is False
        assert set(s["required"]) == set(s["properties"])
        inner = next(v for v in s["properties"].values() if v.get("type") == "array" and "items" in v
                     and v["items"].get("type") == "object")["items"]
        assert inner["additionalProperties"] is False and set(inner["required"]) == set(inner["properties"])


# ── suffix ─────────────────────────────────────────────────────────────────────────────────────────────

def test_verse_suffix_marks_taken_and_function_positions():
    text = render_verse_suffix(packet())
    assert "REF 40001001  MAT 1:1  (fra, edition fra-lsg)" in text
    assert "t0:Généalogie*" in text and "t1:de~" in text and "t4:fils " in text + " "
    assert "h1 γενέσεως <γένεσις> G1078" in text and "DECIDE" in text
    assert "resolved -> t2 t3" in text
    assert "DECIDE: h1" in text


def test_lemma_only_shown_when_it_differs_from_the_surface():
    text = render_verse_suffix(packet())
    assert "Βίβλος <" not in text                       # lemma βίβλος == surface up to case


def test_construct_state_shows_a_hard_gram_tag():
    """HebToken.state (spine's own structured morphology, not gloss text) surfaces as a [construct] tag —
    the hard signal that replaced the unreliable gloss `of.` marker (see PROMPT_VERSION's v8/v9 changelog)."""
    heb = [tok(0, "אֲרוֹן", "H0727", "hbo:0727", gloss="ark")]
    heb[0].state = "construct"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[construct]" in render_verse_suffix(p)


def test_determined_state_shows_a_hard_gram_tag():
    heb = [tok(0, "אֲרוֹן", "H0727", "hbo:0727", gloss="ark")]
    heb[0].state = "determined"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[determined]" in render_verse_suffix(p)


def test_absolute_state_shows_no_gram_tag():
    heb = [tok(0, "אֲרוֹן", "H0727", "hbo:0727", gloss="ark")]
    heb[0].state = "absolute"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    text = render_verse_suffix(p)
    assert "[construct]" not in text and "[determined]" not in text


def test_no_state_shows_no_gram_tag():
    text = render_verse_suffix(packet())                # default tok() leaves state=None
    assert "[construct]" not in text and "[determined]" not in text


def test_greek_case_shows_only_the_marked_values():
    heb = [tok(0, "Θεοῦ", "G2316", "grc:2316", gloss="of.God")]
    heb[0].case_ = "genitive"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[genitive]" in render_verse_suffix(p)
    heb[0].case_ = "nominative"                          # the default, unmarked case — no tag
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[" not in render_verse_suffix(p).split("SOURCE:")[1].split("TARGET:")[0]


def test_greek_tam_and_person_combine_in_one_bracket():
    heb = [tok(0, "ἐγεννήθη", "G1080", "grc:1080", gloss="was.born")]
    heb[0].tense, heb[0].voice, heb[0].mood = "aorist", "passive", "indicative"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[passive·aorist]" in render_verse_suffix(p)   # mood=indicative is the default — omitted


def test_person_and_number_combine_into_a_compact_tag():
    heb = [tok(0, "ἐκάλεσα", "G2564", "grc:2564", gloss="i.called")]
    heb[0].person, heb[0].number = "first", "singular"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[1sg]" in render_verse_suffix(p)


def test_degree_shows_comparative_and_superlative():
    heb = [tok(0, "μείζων", "G3187", "grc:3187", gloss="greater")]
    heb[0].degree = "comparative"
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "[comparative]" in render_verse_suffix(p)


def test_construct_chain_partners_shown_across_scattered_members():
    """construct_group cross-references OTHER listed h ids sharing the group, even when not adjacent —
    the case `state` alone (Phase 1) can't signal on its own."""
    heb = [tok(0, "אֲרוֹן", "H0727", "hbo:0727", gloss="ark"),
           tok(1, "זָהָב", "H2091", "hbo:2091", gloss="gold", content=False),
           tok(2, "בְּרִית", "H1285", "hbo:1285", gloss="the.covenant")]
    heb[0].state = "construct"
    heb[0].construct_group = "cg1"
    heb[2].construct_group = "cg1"
    p = packet(heb=heb, decide=[0, 2], allowed=[1, 2], soft=[], taken=[], resolved={},
               toks=["gold", "ark", "of", "the", "covenant"])
    text = render_verse_suffix(p)
    assert "{construct-chain: h2}" in text
    assert "{construct-chain: h0}" in text


def test_no_construct_group_shows_no_cross_reference():
    text = render_verse_suffix(packet())
    assert "construct-chain" not in text


def test_clause_verb_ref_shown_when_the_verb_is_listed():
    heb = [tok(0, "יֹּ֤אמֶר", "H0559", "hbo:0559", gloss="he.said"),
           tok(1, "יְהוֹשֻׁ֣עַ", "H3091", "hbo:3091", gloss="Joshua", content=False)]
    heb[1].head_idx = 0
    p = packet(heb=heb, decide=[0, 1], allowed=[1, 2], soft=[], taken=[], resolved={},
               toks=["and", "said", "joshua"])
    text = render_verse_suffix(p)
    assert "{clause-verb: h0}" in text


def test_clause_verb_ref_omitted_when_it_is_the_token_itself_or_unlisted():
    heb = [tok(0, "יֹּ֤אמֶר", "H0559", "hbo:0559", gloss="he.said")]
    heb[0].head_idx = 0                                  # points at itself — nonsensical, must not render
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "clause-verb" not in render_verse_suffix(p)
    heb[0].head_idx = 99                                 # not among the listed h ids
    p = packet(heb=heb, decide=[0], allowed=[1], soft=[], taken=[], resolved={})
    assert "clause-verb" not in render_verse_suffix(p)


def test_non_content_rows_only_shown_for_full():
    assert " fn" not in render_verse_suffix(packet("gap"))
    p = packet("full", resolved={}, taken=[], decide=[0, 1, 2], allowed=[0, 2, 3, 4, 6], soft=[1, 5])
    assert "  fn" in render_verse_suffix(p)


def test_released_light_slot_is_flagged():
    p = packet(resolved={0: [4]})                       # t4 is NOT in taken -> slot released
    assert "resolved -> t4 (slot still available)" in render_verse_suffix(p)


def test_seeds_show_counts_and_mark_renderings_present_in_the_verse():
    p = packet("gap-seeded", seeds={"grc:1078": [("fils", 9, 0.5), ("naissance", 12, 0.61)]},
               meta={"grc:1078": {"pos": "noun", "translit": "genesis"}})
    text = render_verse_suffix(p)
    assert "SEEDS:" in text and "grc:1078 (pos noun, translit genesis)" in text
    assert "fils 9/0.50 @t4" in text and "naissance 12/0.61" in text and "naissance 12/0.61 @" not in text


def test_unseeded_strategies_have_no_seed_block():
    assert "SEEDS" not in render_verse_suffix(packet("gap"))


def test_verify_suffix_shows_the_proposal():
    p = packet("verify", decide=[2], proposed={2: ([4, 5], 0.6)}, resolved={0: [0]}, taken=[0])
    text = render_verse_suffix(p)
    assert "PROPOSED -> t4 t5 (score 0.6)" in text


def test_neighbourhood_context_shows_a_neighbor_construct_tag():
    """The rectum of a construct chain needs a supplied 'of' because of the PRECEDING word's state, not
    its own — so the context line must show a neighbor's [construct] tag, not just the DECIDE word's own."""
    heb = [tok(0, "אֲרוֹן", "H0727", "hbo:0727", gloss="ark"),
           tok(1, "בְּרִית", "H1285", "hbo:1285", gloss="the.covenant")]
    heb[0].state = "construct"
    m = packet("lexeme-grouped", heb=heb, decide=[1], allowed=[2], soft=[], taken=[], resolved={0: [0]},
               toks=["the", "ark", "of", "the", "covenant"], seeds={}, meta={})
    g = Packet(strategy="lexeme-grouped", ref=0, book="", ch=0, v=0, label="", toks=[], heb=[],
               decide=[], allowed=[], soft=[], taken=[], lexeme="hbo:1285",
               seeds={"hbo:1285": []}, meta={"hbo:1285": {}}, members=[m])
    text = render_suffix(g)
    assert "אֲרוֹן->the [construct]" in text and "[בְּרִית]" in text


def test_lexeme_suffix_groups_verses_and_shows_context():
    m = packet("lexeme-grouped", seeds={}, meta={})
    g = Packet(strategy="lexeme-grouped", ref=0, book="", ch=0, v=0, label="", toks=[], heb=[],
               decide=[], allowed=[], soft=[], taken=[], lexeme="grc:1078",
               seeds={"grc:1078": [("fils", 9, 0.5)]}, meta={"grc:1078": {"pos": "noun"}}, members=[m])
    text = render_suffix(g)
    assert text.startswith("LEXEME grc:1078") and "1 verse(s):" in text
    assert "DECIDE h1   context: Βίβλος->Généalogie | [γενέσεως] | Ἰησοῦ->Jésus Christ" in text
    assert "seeds present here: fils@t4" in text
    assert g.n_decide == 1


def test_seed_renderings_rank_by_count_times_share_and_filter_noise():
    vocab = {"grc:1": {"de": (500, 0.02), "aimer": (40, 0.9), "amour": (60, 0.5), "x": (3, 0.04)}}
    got = seed_renderings("grc:1", vocab, top_k=2)
    assert [w for w, _c, _s in got] == ["aimer", "amour"]      # `de` is frequent but not exclusive; `x` below min_share
    assert seed_renderings("grc:missing", vocab) == []


# ── normalize ──────────────────────────────────────────────────────────────────────────────────────────

def item(h, ts, status="aligned", note="", tag=None):
    d = {"h_idx": h, "t_idx": ts, "status": status, "note": note}
    if tag:
        d["tag"] = tag
    return d


def test_normalize_keeps_a_clean_answer():
    (d,), reps = normalize([item(1, [4])], packet())
    assert (d.h_idx, d.t_idx, d.status, d.repairs) == (1, [4], "aligned", []) and reps == []
    assert d.is_pair


def test_normalize_drops_unknown_and_duplicate_ids():
    ds, reps = normalize([item(9, [4]), item(1, [4]), item(1, [6])], packet())
    assert [(d.h_idx, d.t_idx) for d in ds] == [(1, [4])]
    assert any("unknown h_idx 9" in r for r in reps) and any("duplicate" in r for r in reps)


def test_normalize_removes_unavailable_positions():
    (d,), _ = normalize([item(1, [0, 4])], packet())          # t0 is taken
    assert d.t_idx == [4] and any("removed unavailable" in r for r in d.repairs)


def test_normalize_trims_a_scattered_span_to_the_longest_run():
    p = packet(allowed=[3, 4, 6], soft=[5], taken=[0, 1, 2])
    (d,), _ = normalize([item(1, [3, 4, 6])], p)
    assert d.t_idx == [3, 4] and any("contiguous" in r for r in d.repairs)


def test_normalize_keeps_a_scattered_span_when_allowed():
    p = packet(allowed=[3, 4, 6], soft=[5], taken=[0, 1, 2])
    (d,), _ = normalize([item(1, [3, 6])], p, allow_scattered=True)
    assert d.t_idx == [3, 6] and not d.repairs


def test_normalize_function_word_only_span_becomes_unrepresented():
    (d,), _ = normalize([item(1, [1, 5])], packet())          # both soft
    assert d.status == "unrepresented" and d.t_idx == [] and not d.is_pair
    assert any("function-word-only" in r for r in d.repairs)


def test_normalize_allows_a_function_word_inside_a_span_with_a_plain_one():
    p = packet(allowed=[6], soft=[5], taken=[0, 1, 2, 3, 4])
    (d,), _ = normalize([item(1, [5, 6])], p)
    assert d.t_idx == [5, 6]


def test_normalize_empty_aligned_becomes_unrepresented():
    (d,), _ = normalize([item(1, [])], packet())
    assert d.status == "unrepresented" and not d.is_pair


def test_normalize_missing_id_is_invalid_not_dropped():
    p = packet(decide=[1, 2], allowed=[4, 6], resolved={0: [0]})
    ds, _ = normalize([item(1, [4])], p)
    assert {d.h_idx: d.status for d in ds} == {1: "aligned", 2: "invalid"}
    assert "missing" in ds[1].repairs[0]


def test_normalize_unknown_status_is_invalid():
    (d,), _ = normalize([item(1, [4], status="maybe")], packet())
    assert d.status == "invalid" and not d.is_pair


def test_normalize_position_claimed_twice_second_claim_loses():
    p = packet(decide=[1, 2], allowed=[4, 6], soft=[])
    ds, _ = normalize([item(1, [4]), item(2, [4, 6])], p)
    by = {d.h_idx: d for d in ds}
    assert by[1].t_idx == [4] and by[2].t_idx == [6]
    assert any("already claimed by h1" in r for r in by[2].repairs)


def test_normalize_noncompositional_group_may_share_a_span():
    p = packet(decide=[1, 2], allowed=[4, 6], soft=[])
    ds, _ = normalize([item(1, [4], "noncompositional"), item(2, [4], "noncompositional")], p)
    assert [d.t_idx for d in ds] == [[4], [4]] and all(d.is_pair for d in ds)


def test_normalize_declined_statuses_pass_through():
    (d,), _ = normalize([item(1, [], "unrepresented", "carried by inflection")], packet())
    assert d.status == "unrepresented" and d.note == "carried by inflection" and not d.repairs


# ── full (two-sided partition) ────────────────────────────────────────────────────────────────────────

def full_item(h_idx, t_idx, status="aligned", note="", h_head=None, t_head=None):
    return {"h_idx": h_idx, "h_head": h_head if h_head is not None else (h_idx[0] if h_idx else None),
            "t_idx": t_idx, "t_head": t_head if t_head is not None else (t_idx[-1] if t_idx else None),
            "status": status, "note": note}


def test_schema_full_requires_two_sided_fields():
    item_schema = SCHEMA_FULL["properties"]["alignments"]["items"]
    assert set(item_schema["required"]) == {"h_idx", "h_head", "t_idx", "t_head", "status", "note"}
    assert item_schema["properties"]["status"]["enum"] == ["aligned", "unrepresented", "added",
                                                            "noncompositional"]
    assert schema_for("full") is SCHEMA_FULL


def test_normalize_full_partitions_both_sides_including_added():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    resp = [full_item([0], [0]), full_item([1], [1, 2]), full_item([2], [3, 4]),
            full_item([3], [], "unrepresented"), full_item([], [5, 6], "added", note="extra")]
    ds, repairs, stats = normalize_full(resp, p)
    assert {tuple(d.h_idx) for d in ds} == {(0,), (1,), (2,), (3,), ()}
    assert sum(len(d.t_idx) for d in ds) == 7 and stats["target_unclaimed"] == 0 and stats["added"] == 1
    assert not any("dropped" in r or "missing" in r for r in repairs)


def test_normalize_full_reports_unclaimed_target_without_inventing_coverage():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    resp = [full_item([0], [0]), full_item([1], []), full_item([2], []), full_item([3], [], "unrepresented")]
    ds, _, stats = normalize_full(resp, p)
    assert stats["target_unclaimed"] == 6 and stats["unclaimed_t_idx"] == [1, 2, 3, 4, 5, 6]
    assert not any(d.status == "added" for d in ds)          # never fabricated


def test_normalize_full_missing_h_becomes_invalid_not_dropped():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    ds, repairs, _ = normalize_full([full_item([0], [0]), full_item([1], [1]), full_item([2], [2])], p)
    by = {tuple(d.h_idx): d for d in ds}
    assert by[(3,)].status == "invalid" and "missing from the response" in by[(3,)].repairs[0]
    assert any("h3" in r for r in repairs)


def test_normalize_full_added_with_h_idx_is_demoted_to_aligned():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    ds, repairs, _ = normalize_full([full_item([0], [0], "added")] +
                                     [full_item([h], [h + 1]) for h in (1, 2, 3)], p)
    by = {tuple(d.h_idx): d for d in ds}
    assert by[(0,)].status == "aligned"
    assert any("demoted to aligned" in r for r in repairs)


def test_normalize_full_second_claim_of_a_target_position_loses():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    resp = [full_item([0], [0]), full_item([1], [0]), full_item([2], [2]),
            full_item([3], [], "unrepresented")]
    ds, repairs, _ = normalize_full(resp, p)
    by = {tuple(d.h_idx): d for d in ds}
    assert by[(1,)].status == "unrepresented" and by[(1,)].t_idx == []
    assert any("already claimed" in r for r in repairs)


def test_normalize_full_noncompositional_group_shares_one_span():
    p = packet("full", decide=[0, 1, 2, 3], resolved={})
    resp = [full_item([0, 1], [0, 1], "noncompositional", h_head=1),
            full_item([2], [2]), full_item([3], [], "unrepresented")]
    ds, _, stats = normalize_full(resp, p)
    by = {tuple(sorted(d.h_idx)): d for d in ds}
    assert by[(0, 1)].t_idx == [0, 1] and by[(0, 1)].h_head == 1 and by[(0, 1)].is_pair
    assert stats["target_unclaimed"] == 4


def test_normalize_full_unknown_status_becomes_invalid():
    p = packet("full", decide=[0], resolved={})
    ds, _, _ = normalize_full([full_item([0], [0], "confirmed")], p)
    assert ds[0].status == "invalid"


def test_derive_score_full_agreement_raises_to_hi_conf():
    assert derive_score_full(agrees=False) == 0.75
    assert derive_score_full(agrees=True) == 0.9


def test_render_full_verse_suffix_shows_every_token_incl_function_as_decide():
    p = packet("full", decide=[0, 1, 2, 3], resolved={2: [3, 4]}, taken=[])
    out = render_full_verse_suffix(p)
    assert "DECIDE: h0, h1, h2, h3" in out
    for h in (0, 1, 2, 3):
        assert f"h{h} " in out
    assert "aligner proposed -> t3 t4" in out
    assert "*" not in out.split("TARGET:")[1].split("DECIDE:")[0]     # nothing pre-taken in `full`


def test_render_full_verse_suffix_seeds_only_lexemes_present_in_p_seeds():
    # h3's lexeme (grc:3588, the function word "τοῦ") is deliberately absent from p.seeds — membership in
    # that dict, not `tok.lexeme` truthiness, gates the SEEDS line (see render_full_verse_suffix docstring).
    p = packet("full", decide=[0, 1, 2, 3], resolved={}, taken=[],
              seeds={"grc:1078": [("fils", 9, 0.5)]}, meta={"grc:1078": {"pos": "noun"}})
    out = render_full_verse_suffix(p)
    assert "SEEDS:" in out and "grc:1078" in out and "fils 9/0.50" in out
    assert "grc:3588" not in out.split("SEEDS:")[1]


def test_review_notes_from_tolerates_garbage():
    assert review_notes_from({}) == []
    assert review_notes_from({"review_notes": ["check idiom", 3, None]}) == ["check idiom"]


# ── verify / grouped parsing, scoring, provenance ─────────────────────────────────────────────────────

def test_verify_confirmed_restores_the_proposed_span_and_corrected_uses_the_models():
    p = packet("verify", decide=[1, 2], proposed={1: ([4], 0.6), 2: ([6], 0.6)}, allowed=[4, 6], soft=[],
               taken=[0])
    resp = {"ref": 1, "verdicts": [
        {"h_idx": 1, "status": "confirmed", "t_idx": [], "note": ""},
        {"h_idx": 2, "status": "corrected", "t_idx": [4], "note": "moved"}]}
    ds, _ = normalize(raw_from_verify(resp, p), p)
    by = {d.h_idx: d for d in ds}
    assert by[1].t_idx == [4] and by[1].tag == "confirmed"
    assert by[2].t_idx == [] and by[2].status == "unrepresented"      # t4 already claimed by h1 -> repaired away
    assert any("already claimed by h1" in r for r in by[2].repairs)
    assert prior_for("verify", by[1]) == "llm_verify_confirmed"


def test_verify_rejected_is_declined():
    p = packet("verify", decide=[1], proposed={1: ([4], 0.6)})
    ds, _ = normalize(raw_from_verify({"verdicts": [{"h_idx": 1, "status": "rejected", "t_idx": [],
                                                     "note": "no fit"}]}, p), p)
    assert ds[0].status == "rejected" and not ds[0].is_pair


def test_raw_from_lexeme_groups_by_verse_in_response_order():
    got = raw_from_lexeme({"verses": [{"ref": 2, "h_idx": 1}, {"ref": 1, "h_idx": 0}, {"ref": 2, "h_idx": 3}]})
    assert {r: [a["h_idx"] for a in v] for r, v in got.items()} == {2: [1, 3], 1: [0]}


def test_raw_from_verse_tolerates_garbage():
    assert raw_from_verse({}) == [] and raw_from_verse({"alignments": ["x", {"h_idx": 1}]}) == [{"h_idx": 1}]


def test_derive_score_agreement_and_confirmation_raise_to_hi_conf():
    plain = Decision(1, [4], "aligned")
    assert derive_score(plain, agrees=False) == 0.75
    assert derive_score(plain, agrees=True) == 0.9
    assert derive_score(Decision(1, [4], "aligned", tag="confirmed"), agrees=False) == 0.9


def test_prior_names():
    assert prior_for("gap-seeded", Decision(1, [4], "aligned")) == "llm_gap-seeded"
    assert prior_for("verify", Decision(1, [4], "aligned", tag="corrected")) == "llm_verify_corrected"


# ═════════════════════════════════════════════════════════════════════════════════════════════════════
# providers: pricing, cache identity, mock, Anthropic request shape, CLI command/env/envelope
# ═════════════════════════════════════════════════════════════════════════════════════════════════════
import json
import subprocess
from types import SimpleNamespace

from lexeme_aligner.llm_prompt import SCHEMA_LEXEME, SCHEMA_VERIFY, SCHEMA_VERSE
from lexeme_aligner.llm_providers import (
    AnthropicProvider, ClaudeCliProvider, Job, MockProvider, Price, Provider, ProviderError, ResponseCache, Usage,
    cache_key, load_prices, make_provider, supports_effort)


def test_price_math_cache_and_batch_discounts():
    p = Price(2.0, 10.0)                                       # $/MTok in/out
    assert p.cost(1_000_000, 0, 0, 0) == pytest.approx(2.0)
    assert p.cost(0, 1_000_000, 0, 0) == pytest.approx(10.0)
    assert p.cost(0, 0, 1_000_000, 0) == pytest.approx(0.2)          # cache read = 0.1x
    assert p.cost(0, 0, 0, 1_000_000) == pytest.approx(2.5)          # cache write = 1.25x
    assert p.cost(1_000_000, 0, 0, 0, batch=True) == pytest.approx(1.0)
    assert p.cost(0, 0, 1_000_000, 1_000_000, cached=False) == pytest.approx(4.0)   # what caching saved


def test_usage_priced_and_add():
    u = Usage.priced(Price(2.0, 10.0), 1000, 100, 2000, 0, wall_s=1.0)
    assert u.cost_usd == pytest.approx((1000 * 2 + 100 * 10 + 2000 * 0.2) / 1e6)
    assert u.cost_usd_if_uncached > u.cost_usd
    assert (u + u).input_tokens == 2000 and (u + u).cost_usd == pytest.approx(2 * u.cost_usd)
    assert Usage.from_dict(u.to_dict()).cost_usd == u.cost_usd


def test_usage_billing_neutral_elements_do_not_make_a_run_mixed():
    acc = Usage(billing="")
    for u in (Usage(billing="subscription", cost_usd=1), Usage(billing="none"), Usage(billing="subscription", cost_usd=2)):
        acc = acc + u
    assert acc.billing == "subscription" and acc.cost_usd == 3
    assert (Usage(billing="api") + Usage(billing="subscription")).billing == "mixed"


def test_load_prices_overrides(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"claude-sonnet-5": {"input": 9, "output": 9}, "new-model": {"input": 1, "output": 2}}))
    pr = load_prices(f)
    assert pr["claude-sonnet-5"].input == 9 and pr["new-model"].output == 2 and "claude-opus-5" in pr


def test_cache_key_covers_every_input_and_the_route():
    base = ("anthropic", "claude-sonnet-5", "low", "PFX", "SFX", SCHEMA_VERSE)
    k = cache_key(*base)
    assert k == cache_key(*base) and len(k) == 64
    for i, alt in enumerate(["cli", "claude-opus-5", "high", "PFX2", "SFX2", SCHEMA_VERIFY]):
        changed = list(base)
        changed[i] = alt
        assert cache_key(*changed) != k, i


def test_response_cache_roundtrip_and_corruption_tolerance(tmp_path):
    c = ResponseCache(tmp_path)
    assert c.get("ab" + "0" * 62) is None
    u = Usage.priced(Price(1, 1), 10, 5, 0, 0)
    c.put("ab" + "0" * 62, {"ref": 1, "alignments": []}, u, {"model": "m"})
    resp, u2 = c.get("ab" + "0" * 62)
    assert resp == {"ref": 1, "alignments": []} and u2.input_tokens == 10
    (tmp_path / "ab" / ("ab" + "0" * 62 + ".json")).write_text("{not json")
    assert c.get("ab" + "0" * 62) is None                    # a corrupt entry is a miss, never a crash


def test_mock_answers_from_the_packet_text_via_its_oracle():
    p = packet("gap", decide=[1])
    oracle = {(40001001, 1): [4]}
    m = MockProvider(lambda ref, h: oracle.get((ref, h)))
    resp, usage = m.complete("", render_verse_suffix(p), SCHEMA_VERSE, max_tokens=100)
    assert resp["ref"] == 40001001 and resp["alignments"] == [
        {"h_idx": 1, "t_idx": [4], "status": "aligned", "note": "mock:oracle"}]
    assert usage.billing == "none" and not m.cacheable
    resp, _ = MockProvider().complete("", render_verse_suffix(p), SCHEMA_VERSE, max_tokens=100)
    assert resp["alignments"][0]["status"] == "unrepresented"


def test_mock_verify_confirms_and_lexeme_group_is_answered_per_block():
    pv = packet("verify", decide=[1], proposed={1: ([4], 0.6)})
    resp, _ = MockProvider().complete("", render_verse_suffix(pv), SCHEMA_VERIFY, max_tokens=1)
    assert resp["verdicts"][0]["status"] == "confirmed"
    g = Packet(strategy="lexeme-grouped", ref=0, book="", ch=0, v=0, label="", toks=[], heb=[], decide=[], allowed=[],
               soft=[], taken=[], lexeme="grc:1078", members=[packet("lexeme-grouped")])
    m = MockProvider(lambda ref, h: [4] if (ref, h) == (40001001, 1) else None)
    resp, _ = m.complete("", render_lexeme_suffix(g), SCHEMA_LEXEME, max_tokens=1)
    assert resp["lexeme"] == "grc:1078" and resp["verses"][0]["t_idx"] == [4]


def test_supports_effort_excludes_haiku():
    assert supports_effort("claude-sonnet-5") and supports_effort("claude-opus-5")
    assert not supports_effort("claude-haiku-4-5")


class FakeMessages:
    """Stands in for anthropic.Anthropic().messages (and .beta.messages): records the request, replays canned replies."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=1234)


def anthropic_reply(text='{"ref":1,"alignments":[]}', stop="end_turn", **usage):
    u = SimpleNamespace(input_tokens=usage.get("i", 100), output_tokens=usage.get("o", 20),
                        cache_read_input_tokens=usage.get("cr", 0), cache_creation_input_tokens=usage.get("cw", 0))
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=u,
                           stop_details=None)


def fake_client(replies):
    msgs = FakeMessages(replies)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=msgs)), msgs


def test_anthropic_request_shape_sonnet_caches_prefix_and_constrains_output():
    client, msgs = fake_client([anthropic_reply(cw=1500)])
    p = AnthropicProvider("claude-sonnet-5", "low", client=client)
    resp, usage = p.complete("PREFIX", "SUFFIX", SCHEMA_VERSE, max_tokens=4096)
    kw = msgs.calls[0]
    assert kw["model"] == "claude-sonnet-5" and kw["max_tokens"] == 4096
    assert kw["system"] == [{"type": "text", "text": "PREFIX", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "SUFFIX"}]
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA_VERSE}, "effort": "low"}
    assert kw["thinking"] == {"type": "adaptive"} and "betas" not in kw
    assert usage.cache_write == 1500 and usage.cost_usd > 0 and resp == {"ref": 1, "alignments": []}


def test_anthropic_haiku_gets_no_thinking_or_effort():
    client, msgs = fake_client([anthropic_reply()])
    AnthropicProvider("claude-haiku-4-5", "low", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert "thinking" not in msgs.calls[0] and "effort" not in msgs.calls[0]["output_config"]


def test_anthropic_opus_uses_server_side_refusal_fallbacks_by_default_and_can_opt_out():
    client, msgs = fake_client([anthropic_reply(), anthropic_reply()])
    AnthropicProvider("claude-opus-5", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert msgs.calls[0]["betas"] == ["server-side-fallback-2026-07-01"] and msgs.calls[0]["fallbacks"] == "default"
    AnthropicProvider("claude-opus-5", client=client, fallbacks=False).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert "fallbacks" not in msgs.calls[1]
    client2, msgs2 = fake_client([anthropic_reply()])
    AnthropicProvider("claude-sonnet-5", client=client2).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert "fallbacks" not in msgs2.calls[0]


def test_anthropic_refusal_and_bad_json_are_typed_errors():
    client, _ = fake_client([anthropic_reply(stop="refusal")])
    with pytest.raises(ProviderError) as e:
        AnthropicProvider("claude-sonnet-5", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert e.value.kind == "refusal"
    client, _ = fake_client([anthropic_reply(text="not json")])
    with pytest.raises(ProviderError) as e:
        AnthropicProvider("claude-sonnet-5", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert e.value.kind == "invalid" and e.value.usage.output_tokens == 20     # a paid-for bad answer is still on the ledger


def test_anthropic_truncation_retries_once_with_double_room_and_bills_both_attempts():
    client, msgs = fake_client([anthropic_reply(stop="max_tokens", o=50), anthropic_reply(o=30)])
    resp, usage = AnthropicProvider("claude-sonnet-5", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=100)
    assert [c["max_tokens"] for c in msgs.calls] == [100, 200]
    assert usage.output_tokens == 80 and resp == {"ref": 1, "alignments": []}


def test_anthropic_sdk_errors_become_provider_errors():
    class Boom:
        def create(self, **kw):
            raise RuntimeError("503 overloaded")
    client = SimpleNamespace(messages=Boom(), beta=SimpleNamespace(messages=Boom()))
    with pytest.raises(ProviderError) as e:
        AnthropicProvider("claude-sonnet-5", client=client).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert e.value.kind == "api" and "503" in str(e.value)


def test_anthropic_count_tokens_uses_the_api_and_no_generation():
    client, _ = fake_client([])
    assert AnthropicProvider("claude-sonnet-5", client=client).count_tokens("P", "S") == 1234


def test_anthropic_batch_submit_poll_and_collect_keyed_by_custom_id(tmp_path):
    ok = anthropic_reply(text='{"ref":7,"alignments":[]}')
    class Batches:
        def __init__(self):
            self.created = None
            self.polls = 0
        def create(self, requests):
            self.created = requests
            return SimpleNamespace(id="msgbatch_1")
        def retrieve(self, bid):
            self.polls += 1
            return SimpleNamespace(processing_status="ended" if self.polls > 1 else "in_progress",
                                   request_counts=SimpleNamespace(processing=1, succeeded=0))
        def results(self, bid):
            yield SimpleNamespace(custom_id="k2", result=SimpleNamespace(type="errored", error=SimpleNamespace(type="overloaded")))
            yield SimpleNamespace(custom_id="k1", result=SimpleNamespace(type="succeeded", message=ok))
    batches = Batches()
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches), beta=None)
    p = AnthropicProvider("claude-sonnet-5", client=client, batch=True, state_dir=tmp_path, poll_s=0)
    out = p.complete_many([Job("k1", "P", "S1", SCHEMA_VERSE, 100), Job("k2", "P", "S2", SCHEMA_VERSE, 100)])
    assert [r["custom_id"] for r in batches.created] == ["k1", "k2"]
    assert out["k1"][0] == {"ref": 7, "alignments": []} and out["k1"][1].billing == "batch"
    assert out["k2"][0] is None and out["k2"][2].startswith("api:")
    assert (tmp_path / "batches" / "msgbatch_1.json").exists()          # id persisted so a killed run can --resume-batch
    # batch price = half the sequential price for identical tokens
    assert out["k1"][1].cost_usd == pytest.approx(
        Price(2.0, 10.0).cost(100, 20, 0, 0) * 0.5)


class Recorder:
    """A fake subprocess.run for the CLI route."""
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr, self.calls = stdout, returncode, stderr, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, self.stderr)


def test_cli_command_is_isolated_keeps_subscription_auth_and_strips_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    envelope = {"is_error": False, "structured_output": {"ref": 1, "alignments": []},
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2000,
                          "cache_creation_input_tokens": 0}, "total_cost_usd": 0.0123}
    rec = Recorder(json.dumps(envelope))
    p = ClaudeCliProvider("claude-sonnet-5", "low", max_budget_usd=0.5, runner=rec)
    resp, usage = p.complete("SYSTEM PREFIX", "USER SUFFIX", SCHEMA_VERSE, max_tokens=4096)
    cmd, kw = rec.calls[0]
    assert "--safe-mode" in cmd and "--bare" not in cmd                   # --bare would defeat subscription auth
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA_VERSE
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5" and cmd[cmd.index("--effort") + 1] == "low"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.50"
    assert kw["input"] == "USER SUFFIX"                                    # prompt on stdin, never argv
    assert "ANTHROPIC_API_KEY" not in kw["env"] and "ANTHROPIC_AUTH_TOKEN" not in kw["env"]
    assert kw["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    assert resp == {"ref": 1, "alignments": []}
    assert (usage.billing, usage.cost_usd, usage.cache_read) == ("subscription", 0.0123, 2000)


def test_cli_prices_the_uncached_counterfactual_when_prices_are_given():
    # total_cost_usd is real money the CLI's own billing already reports (cache-discounted); ONLY
    # cost_usd_if_uncached is a separately-priced diagnostic, previously hardcoded equal to cost_usd here.
    envelope = {"is_error": False, "structured_output": {"ref": 1, "alignments": []},
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2000,
                          "cache_creation_input_tokens": 0}, "total_cost_usd": 0.0123}
    from lexeme_aligner.llm_providers import PRICES
    p = ClaudeCliProvider("claude-sonnet-5", runner=Recorder(json.dumps(envelope)), prices=PRICES)
    _, usage = p.complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert usage.cost_usd == 0.0123                                    # real billed cost: untouched
    expected = PRICES["claude-sonnet-5"].cost(10, 5, 2000, 0, cached=False)   # 2000 tokens at the FULL rate
    assert usage.cost_usd_if_uncached == expected and expected > PRICES["claude-sonnet-5"].cost(10, 5, 2000, 0)
    # no price data for the model at all: falls back to the old equal-to-cost_usd behaviour, not a crash
    p2 = ClaudeCliProvider("unknown-model", runner=Recorder(json.dumps(envelope)))
    _, usage2 = p2.complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert usage2.cost_usd_if_uncached == usage2.cost_usd


def test_cli_decodes_fenced_json_in_result_when_there_is_no_structured_output():
    env = json.dumps({"is_error": False, "result": '```json\n{"ref": 2, "alignments": []}\n```', "usage": {}})
    resp, _ = ClaudeCliProvider("m", runner=Recorder(env)).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert resp == {"ref": 2, "alignments": []}


@pytest.mark.parametrize("stdout,rc,kind", [
    (json.dumps({"is_error": True, "result": "rate limited"}), 0, "api"),
    ("not an envelope", 0, "api"),
    (json.dumps({"is_error": False, "result": "no json here"}), 0, "invalid"),
    (json.dumps({"is_error": False, "structured_output": {}}), 1, "api"),
])
def test_cli_failures_are_typed(stdout, rc, kind):
    with pytest.raises(ProviderError) as e:
        ClaudeCliProvider("m", runner=Recorder(stdout, rc)).complete("P", "S", SCHEMA_VERSE, max_tokens=10)
    assert e.value.kind == kind


def test_cli_missing_binary_and_timeout_are_typed():
    def missing(cmd, **kw):
        raise FileNotFoundError()
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    for runner in (missing, slow):
        with pytest.raises(ProviderError):
            ClaudeCliProvider("m", runner=runner).complete("P", "S", SCHEMA_VERSE, max_tokens=10)


def test_make_provider_routes_and_refuses_batch_on_cli():
    assert isinstance(make_provider("mock", "m", "low"), MockProvider)
    assert isinstance(make_provider("cli", "m", "low"), ClaudeCliProvider)
    with pytest.raises(SystemExit):
        make_provider("cli", "m", "low", batch=True)
    with pytest.raises(SystemExit):
        make_provider("nope", "m", "low")


def test_anthropic_route_needs_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        AnthropicProvider("claude-sonnet-5")
    assert "ANTHROPIC_API_KEY" in str(e.value) and "--provider cli" in str(e.value)


# ═════════════════════════════════════════════════════════════════════════════════════════════════════
# driver: packet building per strategy, execute (cache/retry/budget/breaker), write -> read -> score
# ═════════════════════════════════════════════════════════════════════════════════════════════════════
from lexeme_aligner.align_files import tag_files
from lexeme_aligner.llm_align import (
    Inputs, Ledger, _even_packs, build_packets, build_repair_packets, estimate, execute, max_tokens_for,
    merge_repairs, model_short, recompute_full_stats, resolve, to_records, write_outputs)
from lexeme_aligner.llm_prompt import FullDecision, raw_from_packed
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import VerseRec

FUNC = {"de", "la"}


def verse(v=1, lexemes=("grc:1", "grc:2", "grc:3")):
    """h0/h1/h2 content (lexemes given), h3 a function word; 6 target tokens, t4/t5 are function words."""
    heb = [tok(0, "A", "G0001", lexemes[0]), tok(1, "B", "G0002", lexemes[1]), tok(2, "C", "G0003", lexemes[2]),
           tok(3, "τό", "G3588", "grc:588", content=False)]
    return VerseRec("MAT", 1, v, heb, "alpha beta gamma delta de la", ["alpha", "beta", "gamma", "delta", "de", "la"])


def inputs(**kw):
    r = verse()
    ref = encode("MAT", 1, 1)
    base = dict(recs=[r], covered_h={ref: {0}}, taken_t={ref: {0}}, spans={ref: {0: [0]}},
                low_conf={ref: {1: ([2], 0.6)}}, candidates={ref: {2, 3}}, is_function=lambda w: w in FUNC,
                vocab={"grc:2": {"beta": (9, 0.9)}, "grc:3": {"gamma": (5, 0.8), "delta": (2, 0.4)}},
                lex_pos={"grc:2": "noun"}, lex_translit={}, others={(ref, 1): [1], (ref, 2): [2]},
                label="xx, edition xx")
    base.update(kw)
    return Inputs(**base)


REF = encode("MAT", 1, 1)


def test_gap_packet_decides_only_the_gap_and_partitions_the_target_positions():
    pkts, base, stats = build_packets("gap", inputs())
    (p,) = pkts
    assert p.decide == [1, 2] and p.allowed == [2, 3] and p.taken == [0] and p.soft == [1, 4, 5]
    assert set(p.allowed) | set(p.soft) | set(p.taken) == set(range(6))              # a partition of the verse
    assert not (set(p.allowed) & set(p.soft)) and p.resolved == {0: [0]}
    assert stats["gap_tokens"] == 2 and stats["tokens_to_decide"] == 2 and p.seeds == {}
    assert base[REF] is p


def test_gap_seeded_carries_the_whole_language_seeds_and_pos_meta():
    (p,), _, _ = build_packets("gap-seeded", inputs())
    assert p.seeds["grc:2"] == [("beta", 9, 0.9)] and p.seeds["grc:3"][0][0] == "gamma"
    assert p.meta["grc:2"]["pos"] == "noun"


def test_verses_with_no_available_position_are_not_sent():
    pkts, base, stats = build_packets("gap", inputs(candidates={}))
    assert pkts == [] and base == {} and stats["gap_tokens_no_candidates"] == 2


def test_verses_with_no_gap_are_not_sent():
    pkts, _, _ = build_packets("gap", inputs(covered_h={REF: {0, 1, 2}}))
    assert pkts == []


def test_full_packet_decides_every_source_token_with_nothing_taken():
    (p,), _, _ = build_packets("full", inputs())
    assert p.decide == [0, 1, 2, 3] and p.taken == [] and p.soft == [4, 5] and p.allowed == [0, 1, 2, 3]
    assert p.resolved == {0: [0]}      # the base chain's own span for h0 — an EVIDENCE hint, not a taken state


def test_full_packet_seeds_content_lexemes_only_not_function_words():
    (p,), _, _ = build_packets("full", inputs())
    # h0/h1/h2 = grc:1/grc:2/grc:3 (content); h3 = grc:588 (function, "τό")
    assert set(p.seeds) == {"grc:1", "grc:2", "grc:3"}                 # grc:588 never even attempted
    assert p.seeds["grc:2"] == [("beta", 9, 0.9)] and p.seeds["grc:1"] == []      # no vocab -> empty, not absent
    assert set(p.meta) == {"grc:1", "grc:2", "grc:3"}                  # meta (pos/translit) matches the same scope
    text = render_suffix(p)
    assert "SEEDS:" in text and "grc:2" in text and "beta 9/0.90" in text
    assert "grc:588" not in text.split("SEEDS:")[1]                    # the function word never gets a SEEDS line


# ── packed `full` calls ───────────────────────────────────────────────────────────────────────────────

def test_even_packs_splits_evenly_not_lopsided():
    assert [len(g) for g in _even_packs(list(range(75)), 50)] == [38, 37]     # not the lopsided 50+25
    assert [len(g) for g in _even_packs(list(range(4)), 1)] == [1, 1, 1, 1]   # cap<=1: identity split
    assert _even_packs([], 5) == []


def test_build_packets_packs_full_evenly_but_base_stays_per_verse():
    inp = inputs(recs=[verse(1), verse(2), verse(3)])
    packets, base, stats = build_packets("full", inp, pack_size=2)
    assert stats["packs"] == 2 and [len(p.members) for p in packets] == [2, 1]     # 3 verses, cap 2 -> 2+1
    assert all(p.strategy == "full" and p.members for p in packets)
    assert set(base) == {encode("MAT", 1, v) for v in (1, 2, 3)}                   # unaffected by packing


def test_build_packets_pack_size_one_is_the_identity_unpacked_shape():
    inp = inputs(recs=[verse(1), verse(2)])
    packets, _, stats = build_packets("full", inp, pack_size=1)
    assert "packs" not in stats and all(not p.members for p in packets)           # exactly today's behaviour


def test_render_packed_suffix_shows_every_member_and_states_the_wrapper_once():
    inp = inputs(recs=[verse(1), verse(2)])
    (pack,), _, _ = build_packets("full", inp, pack_size=5)
    text = render_suffix(pack)
    assert text.count("SOURCE:") == 2 and text.count("Return ONE object") == 1
    assert '"results"' in text and "2 objects" in text


def test_raw_from_packed_keys_by_each_items_own_ref():
    resp = {"results": [
        {"ref": 1, "alignments": [{"h_idx": [0]}], "review_notes": []},
        {"ref": 2, "alignments": [{"h_idx": [1]}], "review_notes": []},
        "garbage", {"no_ref": True}]}
    got = raw_from_packed(resp)
    assert set(got) == {1, 2} and got[1] == [{"h_idx": [0]}] and got[2] == [{"h_idx": [1]}]
    assert raw_from_packed({}) == {}


def test_resolve_reassembles_a_packed_response_per_verse():
    inp = inputs(recs=[verse(1), verse(2)])
    (pack,), base, _ = build_packets("full", inp, pack_size=5)
    ref1, ref2 = encode("MAT", 1, 1), encode("MAT", 1, 2)
    resp = {"results": [
        {"ref": ref1, "alignments": [{"h_idx": [1], "h_head": 1, "t_idx": [2], "t_head": 2,
                                      "status": "aligned", "note": ""}], "review_notes": []},
        {"ref": ref2, "alignments": [{"h_idx": [1], "h_head": 1, "t_idx": [4], "t_head": 4,
                                      "status": "aligned", "note": ""}], "review_notes": []}]}
    decisions, _, _ = resolve([(pack, resp, None)], base, "full")
    assert set(decisions) == {ref1, ref2}
    assert next(d for d in decisions[ref1] if d.h_idx == [1]).t_idx == [2]
    assert next(d for d in decisions[ref2] if d.h_idx == [1]).t_idx == [4]


def test_max_tokens_for_scales_by_ids_to_decide_not_by_verse_count():
    # verse() has 4 tokens (h0-h3, all decided by `full`); a single verse never needs more than the floor.
    (single,), _, _ = build_packets("full", inputs(recs=[verse(1)]))
    assert max_tokens_for(single) == 8192                             # 4 ids * 400 < the 8192 floor
    # 10 verses packed (40 ids) genuinely needs more than one verse's worth of budget.
    inp = inputs(recs=[verse(i) for i in range(1, 11)])
    (pack,), _, _ = build_packets("full", inp, pack_size=20)
    assert max_tokens_for(pack) == 40 * 400 > 8192
    # 45 verses (180 ids) would ask for more than the ceiling — capped, not left uncapped.
    inp2 = inputs(recs=[verse(i) for i in range(1, 46)])
    (huge,), _, _ = build_packets("full", inp2, pack_size=100)
    assert max_tokens_for(huge) == 64000


# ── repair pass for a `full` response truncated mid-verse ────────────────────────────────────────────

def test_build_repair_packets_only_for_missing_ids_and_marks_first_pass_claims_taken():
    (base_p,), base, _ = build_packets("full", inputs())
    decisions = {base_p.ref: [
        FullDecision([0], 0, [0], 0, "aligned"),
        FullDecision([1], 1, [], None, "invalid", "", ["missing from the response"]),
        FullDecision([2], 2, [], None, "invalid", "", ["missing from the response"]),
        FullDecision([3], 3, [2], 3, "aligned"),
    ]}
    (repair,) = build_repair_packets(base, decisions)
    assert repair.decide == [1, 2]
    assert set(repair.taken) >= {0, 2}                       # positions the first pass already claimed
    assert 0 not in repair.allowed and 2 not in repair.allowed


def test_build_repair_packets_skips_verses_with_nothing_missing():
    (base_p,), base, _ = build_packets("full", inputs())
    decisions = {base_p.ref: [FullDecision([0], 0, [0], 0, "aligned")]}
    assert build_repair_packets(base, decisions) == []


def test_merge_repairs_replaces_missing_entries_and_the_first_pass_wins_a_conflict():
    decisions = {REF: [
        FullDecision([0], 0, [0], 0, "aligned"),
        FullDecision([1], 1, [], None, "invalid", "", ["missing from the response"]),
        FullDecision([2], 2, [], None, "invalid", "", ["missing from the response"]),
        FullDecision([3], 3, [2], 3, "aligned"),
    ]}
    repaired = {REF: [
        FullDecision([1], 1, [2], 2, "aligned"),             # conflicts with h3's t2 -> first pass wins
        FullDecision([2], 2, [4], 4, "aligned"),             # clean, no conflict
    ]}
    n = merge_repairs(decisions, repaired)
    assert n == 2
    by_h = {d.h_idx[0]: d for d in decisions[REF]}
    assert by_h[1].t_idx == [] and by_h[1].status == "unrepresented"
    assert any("already claimed by the first pass" in r for r in by_h[1].repairs)
    assert by_h[2].t_idx == [4] and by_h[2].status == "aligned"
    assert by_h[3].t_idx == [2]                              # untouched, first pass kept its position
    assert not any(d.status == "invalid" for d in decisions[REF])   # no missing placeholders left


def test_recompute_full_stats_reflects_the_merged_decisions():
    (base_p,), base, _ = build_packets("full", inputs())
    decisions = {base_p.ref: [FullDecision([0], 0, [0], 0, "aligned"), FullDecision([1], 1, [1], 1, "aligned")]}
    stats = recompute_full_stats(decisions, base)
    n_t = len(base[base_p.ref].toks)
    assert stats[base_p.ref]["target_unclaimed"] == n_t - 2


def test_verify_packet_frees_the_proposals_own_positions_and_allows_them_even_if_function_words():
    (p,), _, _ = build_packets("verify", inputs(taken_t={REF: {0, 4}}, low_conf={REF: {1: ([4], 0.6)}}))
    assert p.decide == [1] and p.proposed == {1: ([4], 0.6)}
    assert 4 in p.allowed and 4 not in p.taken and p.taken == [0]                    # own span freed + always acceptable
    assert p.resolved == {0: [0]}


def test_verify_skips_verses_without_low_confidence_pairs():
    assert build_packets("verify", inputs(low_conf={}))[0] == []


def test_lexeme_grouped_buckets_by_lexeme_biggest_first_and_chunks_by_verse():
    recs = [verse(1, ("grc:1", "grc:9", "grc:9")), verse(2, ("grc:1", "grc:9", "grc:9"))]
    r1, r2 = encode("MAT", 1, 1), encode("MAT", 1, 2)
    inp = inputs(recs=recs, covered_h={r1: {0}, r2: {0}}, taken_t={r1: {0}, r2: {0}}, spans={r1: {0: [0]}, r2: {0: [0]}},
                 candidates={r1: {2, 3}, r2: {2, 3}}, low_conf={}, others={})
    groups, base, stats = build_packets("lexeme-grouped", inp, group_size=1)
    assert stats["lexemes"] == 1 and [g.lexeme for g in groups] == ["grc:9", "grc:9"]      # 4 occurrences, 2 verses, 1 verse/call
    assert [[m.ref for m in g.members] for g in groups] == [[r1], [r2]]
    assert all(m.decide == [1, 2] for g in groups for m in g.members)
    one, _, _ = build_packets("lexeme-grouped", inp, group_size=12)
    assert len(one) == 1 and len(one[0].members) == 2 and one[0].n_decide == 4
    assert base[r1].decide == [1, 2]                                    # responses are validated against the full verse


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        build_packets("nope", inputs())


class Scripted(Provider):
    """A cacheable provider that answers each verse packet from a script and can fail on demand."""
    name, model, effort = "scripted", "scripted-1", "low"

    def __init__(self, cost=0.5, fail_first=None, always_fail=None, concurrency=1):
        self.cost, self.fail_first, self.always_fail, self.concurrency = cost, fail_first, always_fail, concurrency
        self.calls = []

    def complete(self, prefix, suffix, schema, *, max_tokens):
        self.calls.append(suffix)
        if self.always_fail:
            raise ProviderError("boom", self.always_fail)
        if self.fail_first and len(self.calls) == 1:
            raise ProviderError("garbled", self.fail_first, Usage(cost_usd=0.1))
        ref = int(suffix.split()[1])
        return {"ref": ref, "alignments": [{"h_idx": 1, "t_idx": [2], "status": "aligned", "note": ""},
                                           {"h_idx": 2, "t_idx": [3], "status": "aligned", "note": ""}]}, Usage(cost_usd=self.cost)


def two_packets():
    recs = [verse(1), verse(2)]
    r1, r2 = encode("MAT", 1, 1), encode("MAT", 1, 2)
    inp = inputs(recs=recs, covered_h={r1: {0}, r2: {0}}, taken_t={r1: {0}, r2: {0}}, spans={}, low_conf={},
                 candidates={r1: {2, 3}, r2: {2, 3}}, others={})
    return build_packets("gap", inp)[0]


def test_execute_second_run_is_served_entirely_from_the_local_cache(tmp_path):
    pkts, cache = two_packets(), ResponseCache(tmp_path)
    prov, ledger = Scripted(), Ledger()
    r1 = execute(pkts, prov, cache, "PFX", SCHEMA_VERSE, ledger, max_usd=99, batch=False, log=lambda *a, **k: None)
    assert len(prov.calls) == 2 and ledger.calls == 2 and ledger.usage.cost_usd == pytest.approx(1.0)
    prov2, ledger2 = Scripted(), Ledger()
    r2 = execute(pkts, prov2, cache, "PFX", SCHEMA_VERSE, ledger2, max_usd=99, batch=False, log=lambda *a, **k: None)
    assert prov2.calls == [] and ledger2.local_hits == 2 and ledger2.usage.cost_usd == 0
    assert [x[1] for x in r1] == [x[1] for x in r2]
    prov3, ledger3 = Scripted(), Ledger()                                        # a different prefix is a different request
    execute(pkts, prov3, cache, "OTHER PFX", SCHEMA_VERSE, ledger3, max_usd=99, batch=False, log=lambda *a, **k: None)
    assert len(prov3.calls) == 2


def test_execute_never_caches_mock_answers(tmp_path):
    pkts, cache = two_packets(), ResponseCache(tmp_path)
    m = MockProvider(lambda ref, h: [2])
    execute(pkts, m, cache, "PFX", SCHEMA_VERSE, Ledger(), max_usd=99, batch=False, log=lambda *a, **k: None)
    assert not list(tmp_path.rglob("*.json"))


def test_execute_retries_an_unparseable_answer_once_with_a_repair_line_and_bills_both(tmp_path):
    pkts = two_packets()[:1]
    prov, ledger = Scripted(fail_first="invalid"), Ledger()
    res = execute(pkts, prov, ResponseCache(tmp_path), "PFX", SCHEMA_VERSE, ledger, max_usd=99, batch=False,
                  log=lambda *a, **k: None)
    assert res[0][1] is not None and res[0][2] is None
    assert len(prov.calls) == 2 and prov.calls[1].endswith("Return only the JSON object.")
    assert ledger.repaired_calls == 1 and ledger.usage.cost_usd == pytest.approx(0.1 + 0.5)


def test_execute_stops_at_the_spend_cap_and_says_which_calls_did_not_run(tmp_path):
    pkts = two_packets() * 3                                              # 6 calls; 2 per slice at concurrency 1
    prov, ledger = Scripted(cost=0.5), Ledger()
    res = execute(pkts, prov, ResponseCache(tmp_path), "PFX", SCHEMA_VERSE, ledger, max_usd=1.0, batch=False,
                  log=lambda *a, **k: None)
    assert len(prov.calls) == 2 and ledger.usage.cost_usd == pytest.approx(1.0)
    assert [e for _p, r, e in res if r is None] and all(e.startswith("budget") for _p, r, e in res[2:])


def test_execute_aborts_on_repeated_api_failures_instead_of_failing_every_call(tmp_path):
    pkts = two_packets() * 4
    with pytest.raises(SystemExit) as e:
        execute(pkts, Scripted(always_fail="api", concurrency=4), ResponseCache(tmp_path), "PFX", SCHEMA_VERSE, Ledger(),
                max_usd=99, batch=False, log=lambda *a, **k: None)
    assert "consecutive API failures" in str(e.value)


def test_max_tokens_by_strategy():
    assert max_tokens_for(packet("gap")) == 4096 and max_tokens_for(packet("full")) == 8192


def test_resolve_to_records_write_and_the_scorer_reads_it_back(tmp_path):
    from lexeme_aligner.score_gapfill import _gap_pairs
    pkts, base, _ = build_packets("gap", inputs())
    results = [(pkts[0], {"ref": REF, "alignments": [
        {"h_idx": 1, "t_idx": [2], "status": "aligned", "note": "picked beta"},
        {"h_idx": 2, "t_idx": [], "status": "unrepresented", "note": "carried by inflection"}]}, None)]
    inp = inputs()
    decisions, tally, _ = resolve(results, base, "gap")
    assert tally["aligned"] == 1 and tally["unrepresented"] == 1
    by_book = to_records(decisions, base, "gap", inp, {"model": "m", "run_id": "r"})
    files = write_outputs(by_book, tmp_path, "xx.gap.m")
    assert [f.name for f in files] == ["align_llm_xx.gap.m_MAT.jsonl"]
    (rec,) = [json.loads(l) for l in files[0].read_text().splitlines()]
    (pair,) = rec["pairs"]
    assert (pair["h_idx"], pair["t_idx"], pair["target"], pair["method"], pair["prior"]) == (1, [2], "gamma", "llm", "llm_gap")
    assert pair["score"] == 0.75 and pair["note"] == "picked beta"          # another method chose [1], the LLM [2]: no agreement
    assert rec["llm_skipped"] == [{"h_idx": 2, "strong": "G0003", "lexeme": "grc:3", "status": "unrepresented",
                                   "note": "carried by inflection"}]              # strong lets the report judge declined tokens
    assert rec["llm"] == {"model": "m", "run_id": "r"}
    # the existing gold scorer reads the file unchanged, keyed by the pair's `prior`
    assert list(_gap_pairs("xx.gap.m", tmp_path, "llm")) == [(REF, "G0002", ["gamma"], "llm_gap")]


def test_agreement_with_another_method_raises_the_score_to_hi_conf(tmp_path):
    pkts, base, _ = build_packets("gap", inputs())
    results = [(pkts[0], {"ref": REF, "alignments": [
        {"h_idx": 1, "t_idx": [1], "status": "aligned", "note": ""},
        {"h_idx": 2, "t_idx": [2], "status": "aligned", "note": ""}]}, None)]
    decisions, _, _ = resolve(results, base, "gap")
    # h1 -> t1 is a function-word-only span here (soft), so it is repaired away; h2 -> t2 matches others[(REF, 2)]
    by = to_records(decisions, base, "gap", inputs(), {})
    (pair,) = by["MAT"][0]["pairs"]
    assert pair["h_idx"] == 2 and pair["score"] == 0.9


def test_failed_calls_leave_invalid_decisions_and_no_pairs():
    pkts, base, _ = build_packets("gap", inputs())
    decisions, tally, _ = resolve([(pkts[0], None, "api: boom")], base, "gap")
    assert tally["invalid"] == 2 and not any(d.is_pair for d in decisions[REF])
    assert all(d.repairs == ["call failed"] for d in decisions[REF])


def test_grouped_answers_for_one_verse_are_merged_and_conflicts_resolved_across_calls():
    recs = [verse(1, ("grc:1", "grc:8", "grc:9"))]
    inp = inputs(recs=recs, covered_h={REF: {0}}, spans={REF: {0: [0]}}, low_conf={}, others={})
    groups, base, _ = build_packets("lexeme-grouped", inp)
    assert {g.lexeme for g in groups} == {"grc:8", "grc:9"}                     # one verse answered across two calls
    # both calls claim t2: the LATER claim (lexeme order: h1 before h2) must lose
    results = []
    for g in sorted(groups, key=lambda g: g.members[0].decide):
        h = g.members[0].decide[0]
        results.append((g, {"lexeme": g.lexeme, "verses": [
            {"ref": REF, "h_idx": h, "t_idx": [2], "status": "aligned", "note": ""}]}, None))
    decisions, tally, _ = resolve(results, base, "lexeme-grouped")
    kept = {d.h_idx: d.t_idx for d in decisions[REF]}
    assert kept == {1: [2], 2: []} and tally["conflict_dropped"] == 1


# ── lexeme-verify (review a completed `full` run, grouped by lexeme) ──────────────────────────────────

def _write_llm_output(out_dir, tag, book, records):
    fp = out_dir / f"align_llm_{tag}_{book}.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return fp


def test_build_lexeme_verify_packets_reviews_only_lexemes_with_a_peer(tmp_path):
    from lexeme_aligner.llm_align import build_lexeme_verify_packets
    ref1, ref2 = encode("MAT", 1, 1), encode("MAT", 1, 2)
    inp = inputs(recs=[verse(1, ("grc:1", "grc:2", "grc:3")), verse(2, ("grc:1", "grc:9", "grc:9"))])
    _write_llm_output(tmp_path, "srctag", "MAT", [
        {"ref": ref1, "book": "MAT", "chapter": 1, "verse": 1, "llm": {}, "llm_skipped": [], "pairs": [
            {"h_idx": 0, "lexeme": "grc:1", "strong": "G0001", "t_idx": [1], "target": "beta",
             "score": 0.75, "content": True}]},
        {"ref": ref2, "book": "MAT", "chapter": 1, "verse": 2, "llm": {}, "llm_skipped": [], "pairs": [
            {"h_idx": 0, "lexeme": "grc:1", "strong": "G0001", "t_idx": [2], "target": "gamma",
             "score": 0.75, "content": True},
            {"h_idx": 1, "lexeme": "grc:9", "strong": "G0009", "t_idx": [3], "target": "delta",
             "score": 0.9, "content": True},
            {"h_idx": 2, "lexeme": "grc:9", "strong": "G0009", "t_idx": [4], "target": "eps",
             "score": 0.9, "content": True}]}])
    packets, base, stats = build_lexeme_verify_packets("srctag", inp, tmp_path)
    assert stats == {"lexemes_reviewed": 2, "occurrences_reviewed": 4, "packs": 2}     # grc:1 x2, grc:9 x2
    assert {p.lexeme for p in packets} == {"grc:1", "grc:9"}
    grc1 = next(p for p in packets if p.lexeme == "grc:1")
    assert {(m.ref, m.decide[0]) for m in grc1.members} == {(ref1, 0), (ref2, 0)}
    assert sorted((tuple(m.proposed[0][0]), m.proposed[0][1]) for m in grc1.members) == [((1,), 0.75), ((2,), 0.75)]
    assert set(base) == {ref1, ref2}
    assert base[ref2].decide == [0, 1, 2]           # grc:1's h0 AND grc:9's h1/h2 both reviewed in verse 2
    assert base[ref2].resolved == {0: [2], 1: [3], 2: [4]}     # the FULL PASS's own spans, for context


def test_build_lexeme_verify_packets_skips_a_lexeme_seen_only_once(tmp_path):
    from lexeme_aligner.llm_align import build_lexeme_verify_packets
    ref1 = encode("MAT", 1, 1)
    inp = inputs(recs=[verse(1, ("grc:1", "grc:2", "grc:3"))])
    _write_llm_output(tmp_path, "srctag", "MAT", [
        {"ref": ref1, "book": "MAT", "chapter": 1, "verse": 1, "llm": {}, "llm_skipped": [], "pairs": [
            {"h_idx": 0, "lexeme": "grc:1", "strong": "G0001", "t_idx": [1], "target": "beta",
             "score": 0.75, "content": True}]}])
    packets, base, stats = build_lexeme_verify_packets("srctag", inp, tmp_path)
    assert packets == [] and base == {} and stats["lexemes_reviewed"] == 0


def test_lexeme_verify_end_to_end_confirmed_corrected_rejected(tmp_path):
    from lexeme_aligner.llm_align import build_lexeme_verify_packets
    ref1, ref2, ref3 = encode("MAT", 1, 1), encode("MAT", 1, 2), encode("MAT", 1, 3)
    inp = inputs(recs=[verse(1), verse(2), verse(3)])
    _write_llm_output(tmp_path, "srctag", "MAT", [
        {"ref": r, "book": "MAT", "chapter": 1, "verse": v, "llm": {}, "llm_skipped": [], "pairs": [
            {"h_idx": 0, "lexeme": "grc:1", "strong": "G0001", "t_idx": [v], "target": "x",
             "score": 0.75, "content": True}]}
        for v, r in ((1, ref1), (2, ref2), (3, ref3))])
    (group,), base, _ = build_lexeme_verify_packets("srctag", inp, tmp_path)
    resp = {"lexeme": "grc:1", "verdicts": [
        {"ref": ref1, "h_idx": 0, "status": "confirmed", "t_idx": [], "note": ""},          # t_idx ignored
        {"ref": ref2, "h_idx": 0, "status": "corrected", "t_idx": [2], "note": "moved"},   # t2 = "gamma" (content)
        {"ref": ref3, "h_idx": 0, "status": "rejected", "t_idx": [], "note": "wrong word entirely"}]}
    decisions, tally, _ = resolve([(group, resp, None)], base, "lexeme-verify")
    by_ref = {ref: decisions[ref][0] for ref in (ref1, ref2, ref3)}
    assert by_ref[ref1].t_idx == [1] and by_ref[ref1].tag == "confirmed"        # restored from `proposed`
    assert by_ref[ref2].t_idx == [2] and by_ref[ref2].tag == "corrected"
    assert by_ref[ref3].status == "rejected" and not by_ref[ref3].is_pair
    inp2 = inputs(recs=[verse(1), verse(2), verse(3)])
    by_book = to_records(decisions, base, "lexeme-verify", inp2, {"model": "m", "run_id": "r"})
    by_ref_rec = {rec["ref"]: rec for rec in by_book["MAT"]}
    assert by_ref_rec[ref1]["pairs"][0]["prior"] == "llm_lexeme_verify_confirmed"
    assert by_ref_rec[ref2]["pairs"][0]["prior"] == "llm_lexeme_verify_corrected"
    assert by_ref_rec[ref3]["pairs"] == [] and by_ref_rec[ref3]["llm_skipped"][0]["status"] == "rejected"


def test_estimate_and_model_short_and_the_ledger_arithmetic():
    from lexeme_aligner.llm_providers import PRICES
    pkts, _, _ = build_packets("gap", inputs())
    est = estimate(pkts, "PFX" * 100, "claude-sonnet-5", PRICES, batch=False)
    assert est["calls"] == 1 and est["est_cost_usd"] > 0
    assert estimate(pkts, "P", "unknown-model", PRICES, batch=False)["est_cost_usd"] is None
    assert model_short("claude-sonnet-5") == "sonnet5" and model_short("claude-haiku-4-5") == "haiku45"
    led = Ledger()
    led.add(Usage(cost_usd=1.0, input_tokens=5))
    led.add(Usage(cost_usd=9.0), hit=True)                                      # a local-cache hit is free THIS run...
    assert (led.calls, led.local_hits, led.usage.cost_usd, led.usage.input_tokens) == (1, 1, 1.0, 5)
    assert led.cached_usage.cost_usd == 9.0                                     # ...but the cell still remembers what it cost
