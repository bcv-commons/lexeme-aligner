"""Slot-allocation tests — protection #1 (docs/pipeline-overview.md).

The pipeline's recurring failure is a function-word source token holding a target position a content
token needed. These lock down the two mechanisms that decide who gets a position, so a future change
to the symmetrizer can't silently reopen it.
"""
import pytest

from lexeme_aligner.eflomal_align import _content_priority, _grow_diag_final_and
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, is_content, strong="G0001"):
    return HebToken(idx, f"w{idx}", strong, f"grc:{idx}", f"l{idx}", None, is_content)


# ── the symmetrizer we are protecting ──────────────────────────────────────────────────

def test_gdfa_keeps_the_intersection():
    fwd, rev = {(0, 0), (1, 1)}, {(0, 0), (1, 2)}
    sym, inter = _grow_diag_final_and(fwd, rev, 2, 3)
    assert inter == {(0, 0)}
    assert {(0, 0)} <= sym


def test_gdfa_final_and_gives_a_free_slot_to_whoever_is_free():
    """The behaviour protection #1 exists to constrain: the last pass hands a free target position to
    any free source token, with no content preference and no score threshold."""
    fwd, rev = {(0, 0)}, {(0, 0), (1, 1)}
    sym, _ = _grow_diag_final_and(fwd, rev, 2, 2)
    assert (1, 1) in sym


# ── _content_priority ──────────────────────────────────────────────────────────────────

def test_reassigns_a_noncontent_held_slot_to_an_unaligned_content_token():
    src = [tok(0, False), tok(1, True)]          # 0 = article-like, 1 = real content
    sym = {(0, 5)}                                # the function word holds position 5
    union = sym | {(1, 5)}                        # ...but the model also proposed it for content
    out, moved = _content_priority(sym, union, src)
    assert moved == 1
    assert (1, 5) in out and (0, 5) not in out


def test_never_takes_a_slot_another_content_token_holds():
    src = [tok(0, True), tok(1, True)]
    sym = {(0, 5)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 0 and out == sym


def test_does_not_let_an_already_aligned_content_token_grow():
    """Growing an existing span is span extension — a different question with its own measurements."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}                        # content token already has position 2
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 0 and out == sym


def test_requires_the_model_to_have_proposed_the_link():
    """Reallocation may only use links eflomal itself put in the union — never invent one."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5)}
    out, moved = _content_priority(sym, union=sym, src_toks=src)
    assert moved == 0 and out == sym


def test_is_a_noop_when_every_slot_has_a_content_holder():
    src = [tok(0, True)]
    sym = {(0, 1)}
    out, moved = _content_priority(sym, sym | {(0, 2)}, src)
    assert moved == 0 and out is sym


def test_one_content_token_cannot_take_two_reassignable_slots():
    src = [tok(0, False), tok(1, False), tok(2, True)]
    sym = {(0, 3), (1, 4)}
    union = sym | {(2, 3), (2, 4)}
    out, moved = _content_priority(sym, union, src)
    assert moved == 1
    assert len([t for s, t in out if s == 2]) == 1


# ── _longest_contiguous (protection #2) ────────────────────────────────────────────────

from lexeme_aligner.eflomal_align import _longest_contiguous


def test_keeps_the_only_run_untouched():
    assert _longest_contiguous([3, 4, 5], set()) == [3, 4, 5]


def test_drops_the_scattered_outlier():
    assert _longest_contiguous([3, 4, 9], set()) == [3, 4]


def test_prefers_the_run_holding_an_intersection_backed_link():
    """A shorter run both alignment directions agreed on beats a longer, weaker one."""
    assert _longest_contiguous([1, 5, 6], inter_ts={1}) == [1]


def test_falls_back_to_length_then_earliest_when_no_intersection():
    assert _longest_contiguous([1, 5, 6], inter_ts=set()) == [5, 6]
    assert _longest_contiguous([1, 2, 8, 9], inter_ts=set()) == [1, 2]


def test_single_position_is_unchanged():
    assert _longest_contiguous([7], {7}) == [7]


# ── _content_priority level 2: displacement (protection #1, risky tier) ────────────────

def test_level2_displaces_a_weak_link_for_a_function_word_held_slot():
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}                       # content token holds 2, but only weakly
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset(), displace_weak=True)
    assert moved == 1
    assert (1, 5) in out and (1, 2) not in out and (0, 5) not in out


def test_level2_will_not_displace_an_intersection_backed_link():
    """A link both alignment directions agreed on is never traded away."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset({(1, 2)}), displace_weak=True)
    assert moved == 0 and out == sym


def test_level2_displaces_rather_than_adds():
    """Displacement must never grow a span — that is the trap that made content-only look good."""
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, _ = _content_priority(sym, union, src, inter=frozenset(), displace_weak=True)
    assert len([t for s, t in out if s == 1]) == 1


def test_level1_still_refuses_to_displace():
    src = [tok(0, False), tok(1, True)]
    sym = {(0, 5), (1, 2)}
    union = sym | {(1, 5)}
    out, moved = _content_priority(sym, union, src, inter=frozenset(), displace_weak=False)
    assert moved == 0 and out == sym


# ── default posture (protection #2 is ON by default since 2026-09-01) ─────────────────

def test_contiguity_protection_is_on_by_default():
    """Guards the default flip: a regression here silently republishes scattered spans."""
    from lexeme_aligner.eflomal_align import EflomalAligner
    assert EflomalAligner().contiguous_only is True
    assert EflomalAligner().displace_weak is False
    assert EflomalAligner().content_only is False


# ── gloss source-gated stopword filter (protection #4, ON by default) ─────────────────

class _Priors:
    def __init__(self, m): self.m = m
    def lookup(self, tok): return self.m.get(tok.lexeme, [])


class _SW:
    def __init__(self, words): self.words = words
    def is_function(self, w): return w.lower() in self.words


def _gloss(heb, tokens, priors, **kw):
    from lexeme_aligner.gloss_align import align_verse
    return align_verse(heb, tokens, _Priors(priors), "xx", **kw)


def test_source_gate_blocks_a_content_lexeme_from_a_stopword_target():
    h = tok(0, True); h.lexeme = "grc:1"
    got = _gloss([h], ["de"], {"grc:1": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert got == []


def test_source_gate_still_allows_a_FUNCTION_lexeme_onto_a_stopword():
    """The unconditional gate's fatal flaw: it also blocked legitimate function<->function matches."""
    h = tok(0, False); h.lexeme = "grc:3588"
    got = _gloss([h], ["de"], {"grc:3588": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert [m.t_idx for m in got] == [[0]]


def test_unconditional_gate_blocks_both_which_is_why_it_is_not_used():
    h = tok(0, False); h.lexeme = "grc:3588"
    got = _gloss([h], ["de"], {"grc:3588": [("de",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=False)
    assert got == []


def test_source_gate_is_a_noop_on_non_stopword_targets():
    h = tok(0, True); h.lexeme = "grc:1"
    got = _gloss([h], ["maison"], {"grc:1": [("maison",)]},
                 stopwords=_SW({"de"}), source_gated_stopwords=True)
    assert [m.t_idx for m in got] == [[0]]


def test_light_last_defers_a_light_lexeme_to_a_real_match():
    light = tok(0, True); light.lexeme = "grc:1510"
    real = tok(1, True); real.lexeme = "grc:2316"
    priors = {"grc:1510": [("est",)], "grc:2316": [("est",)]}
    got = _gloss([light, real], ["est"], priors, skip_lexemes={"grc:1510"}, light_last=True)
    assert [(m.h_idx, m.t_idx) for m in got] == [(1, [0])]


# ── gap-fill slot bookkeeping (protections #3 and #5) ─────────────────────────────────

def _covered(tmp_path, pairs, **kw):
    import json
    from lexeme_aligner.gapfill import load_covered
    (tmp_path / "align_eflomal_xx_RUT.jsonl").write_text(
        json.dumps({"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1, "pairs": pairs}) + "\n")
    return load_covered("xx", tmp_path, ("eflomal",), 0.0, {}, **kw)


def test_light_pair_stays_covered_but_releases_its_slot(tmp_path):
    p = {"h_idx": 0, "strong": "G1510", "lexeme": "grc:1510", "target": "est",
         "t_idx": [3], "score": 0.9, "content": True, "light": True}
    covered, taken, anchors, _, _ = _covered(tmp_path, [p], release_light=True)
    assert 0 in covered[8001001]          # never re-attempted as a gap
    assert taken[8001001] == set()        # ...but a real match may take position 3
    assert anchors[8001001] == {}


def test_light_release_can_be_ablated(tmp_path):
    p = {"h_idx": 0, "strong": "G1510", "lexeme": "grc:1510", "target": "est",
         "t_idx": [3], "score": 0.9, "content": True, "light": True}
    _, taken, _, _, _ = _covered(tmp_path, [p], release_light=False)
    assert taken[8001001] == {3}


def test_function_word_slots_are_free_by_default(tmp_path):
    """#5 default OFF: reserving them costs 29-42% of gap fills for ~0 precision."""
    p = {"h_idx": 0, "strong": "G3588", "lexeme": "grc:3588", "target": "le",
         "t_idx": [2], "score": 0.9, "content": False}
    covered, taken, _, _, _ = _covered(tmp_path, [p])
    assert taken[8001001] == set() and covered[8001001] == set()


def test_function_word_slots_can_be_reserved_on_request(tmp_path):
    p = {"h_idx": 0, "strong": "G3588", "lexeme": "grc:3588", "target": "le",
         "t_idx": [2], "score": 0.9, "content": False}
    covered, taken, _, _, _ = _covered(tmp_path, [p], reserve_function_slots=True)
    assert taken[8001001] == {2}
    assert covered[8001001] == set()      # still never a gap, and never a vocabulary exemplar
