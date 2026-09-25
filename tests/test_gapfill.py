"""Tests for gapfill.compute_order_stats() — extracted from main()'s own inline computation (2026-09-25,
roadmap item D0) so derive_typology.py can reuse the raw construct-order / cross-phrase function-order
statistics. Synthetic HebToken/VerseRec data, no real corpus needed.
"""
import lexeme_aligner.gapfill as gf
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, strong, phrase_id=None, function=None, rela=None, is_content=True):
    t = HebToken(idx, f"w{idx}", strong, f"lx:{strong}", f"l{idx}", None, is_content)
    t.phrase_id, t.function, t.rela = phrase_id, function, rela
    return t


class _Rec:
    def __init__(self, book, ch, v, heb):
        self.book, self.ch, self.v, self.heb = book, ch, v, heb


def _ref(r):
    from lexeme_aligner.refs import encode
    return encode(r.book, r.ch, r.v)


def test_rec_after_rate_none_below_n_50():
    # a single head/dep pair observed once — far below the n>=50 floor
    head = tok(0, "H1", phrase_id="p1", rela="NA")
    dep = tok(1, "H2", phrase_id="p1", rela="rec")
    r = _Rec("RUT", 1, 1, [head, dep])
    anchors = {_ref(r): {0: 5, 1: 10}}   # dep's target (10) comes AFTER head's (5)
    stats = gf.compute_order_stats([r], anchors)
    assert stats["rec_after_n"] == 1
    assert stats["rec_after_rate"] is None


def test_rec_after_rate_computed_once_n_clears_50():
    recs, anchors = [], {}
    for i in range(60):
        head = tok(0, "H1", phrase_id=f"p{i}", rela="NA")
        dep = tok(1, "H2", phrase_id=f"p{i}", rela="rec")
        r = _Rec("RUT", 1, i + 1, [head, dep])
        recs.append(r)
        anchors[_ref(r)] = {0: 5, 1: 10}   # dep always after head -> rate should be 1.0
    stats = gf.compute_order_stats(recs, anchors)
    assert stats["rec_after_n"] == 60
    assert stats["rec_after_rate"] == 1.0


def test_func_order_pair_counts_and_rates():
    recs, anchors = [], {}
    for i in range(5):
        pred = tok(0, "H1", phrase_id="pA", function="Pred")
        subj = tok(1, "H2", phrase_id="pB", function="Subj")
        r = _Rec("RUT", 1, i + 1, [pred, subj])
        recs.append(r)
        # Pred (source idx 0) target=3, Subj (source idx 1) target=7 -> target KEEPS source order
        anchors[_ref(r)] = {0: 3, 1: 7}
    stats = gf.compute_order_stats(recs, anchors)
    assert stats["func_order_n"][("Pred", "Subj")] == 5
    assert stats["func_order"][("Pred", "Subj")] == 1.0


def test_func_order_omits_pairs_with_zero_observations():
    stats = gf.compute_order_stats([], {})
    assert stats["func_order"] == {}
    assert stats["func_order_n"] == {}
    assert stats["rec_after_rate"] is None
    assert stats["rec_after_n"] == 0


def test_verses_with_no_anchor_are_skipped():
    head = tok(0, "H1", phrase_id="p1", rela="NA")
    dep = tok(1, "H2", phrase_id="p1", rela="rec")
    r = _Rec("RUT", 1, 1, [head, dep])
    stats = gf.compute_order_stats([r], {})   # no anchors entry for this ref at all
    assert stats["rec_after_n"] == 0
