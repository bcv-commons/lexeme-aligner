"""Positional gold scorer (pos_score.py): Clear-tokenization reconstruction, mapping onto our tokens, union
with vetoes, and the link metrics — all on synthetic data, no parquet needed."""
import collections
import pytest

from lexeme_aligner.pos_score import GoldVerse, Metrics, Ours, Spine, clear_tokens, map_positions, score, union
from lexeme_aligner.usj_source import tokenize


def test_clear_tokens_rules():
    assert clear_tokens("Généalogie de Jésus-Christ, fils d’Abraham.") == \
        ["Généalogie", "de", "Jésus-Christ", ",", "fils", "d’", "Abraham", "."]
    assert clear_tokens("c ’ est lui") == ["c’", "est", "lui"]              # our text spaces the apostrophe out
    assert clear_tokens("faites-le-moi savoir ; 12 !") == ["faites-le-moi", "savoir", ";", "12", "!"]
    assert clear_tokens("अब्राहम की सन्तान, दाऊद।")[:4] == ["अबराहम", "की", "सनतान", ","]   # marks stripped like the tokenizer


def test_map_positions_hyphen_apostrophe_punctuation_and_digits():
    text = "Généalogie de Jésus-Christ, fils d’Abraham 4."
    assert tokenize(text) == ["Généalogie", "de", "Jésus", "Christ", "fils", "d", "Abraham"]
    m = map_positions(text)
    #        Généalogie de  Jésus-Christ ,   fils d’  Abraham 4   .
    assert m == [[0],      [1], [2, 3],     [], [4], [5], [6],   [], []]


def test_map_positions_refuses_when_the_tokenizations_disagree(monkeypatch):
    import lexeme_aligner.pos_score as ps
    monkeypatch.setattr(ps, "tokenize", lambda t: ["not", "this", "text"])
    assert map_positions("Généalogie de Jésus") is None


def spine():
    # one verse (ref 1): tokens h0 G1 content, h1 G2 content, h2 G2 content (2nd occurrence), h3 G3 function
    return Spine(key_of={1: {0: ("G1", 0), 1: ("G2", 0), 2: ("G2", 1), 3: ("G3", 0)}},
                 content={1: {("G1", 0), ("G2", 0), ("G2", 1)}},
                 counts={1: collections.Counter({"G1": 1, "G2": 2, "G3": 1})})


def gold():
    gv = GoldVerse(1, links={("G1", 0): {0}, ("G2", 0): {2, 3}, ("G2", 1): {5}, ("G3", 0): {1}})
    gv.claimed = {0, 1, 2, 3, 5}
    return {1: gv}


def test_score_exact_overlap_and_link_counts():
    ours = Ours(spans={1: {("G1", 0): {0}, ("G2", 0): {2}, ("G2", 1): {5, 6}, ("G3", 0): {1}}})
    r = score(gold(), ours, spine(), content_only=True).row()
    assert r["gold_links"] == 3 and r["answered"] == 3                    # G3 is a function word: not judged
    assert r["exact_span"] == 1 and r["overlap"] == 3                      # only G1 exact; G2#1 under-spans, G2#2 over-spans
    assert (r["link_precision"], r["link_recall"]) == (pytest.approx(3 / 4), pytest.approx(3 / 4))   # tp=3 fp=1 fn=1
    assert r["over_claimed"] == 1                                          # position 6: gold never claims it
    r_all = score(gold(), ours, spine(), content_only=False).row()
    assert r_all["gold_links"] == 4 and r_all["exact_span"] == 2


def test_score_skips_ambiguous_occurrences():
    g = gold()
    del g[1].links[("G2", 1)]                                              # gold aligned only one of the two G2s
    r = score(g, Ours(spans={1: {("G1", 0): {0}, ("G2", 0): {2, 3}}}), spine()).row()
    assert r["ambiguous_skipped"] == 1 and r["gold_links"] == 1 and r["exact_span"] == 1


def test_score_missing_answer_counts_as_false_negatives():
    r = score(gold(), Ours(spans={1: {}}), spine()).row()
    assert r["answered"] == 0 and r["link_recall"] == 0 and r["target_unclaimed_rate"] == pytest.approx(1.0)


def test_union_first_wins_and_vetoes_remove_later_claims():
    a = Ours(spans={1: {("G1", 0): {0}}}, vetoed={(1, "G2", 0)})
    b = Ours(spans={1: {("G1", 0): {9}, ("G2", 0): {2}, ("G2", 1): {5}}})
    u = union([a, b])
    assert u.spans[1] == {("G1", 0): {0}, ("G2", 1): {5}}                  # a's G1 kept; a's veto removed b's G2#1
