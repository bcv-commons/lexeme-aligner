"""E1 (2026-09-27): gold curation-convention profile + convention-aware scoring in pos_score.py.
Synthetic gold/ours/spine — no real corpus."""
import collections

import lexeme_aligner.pos_score as ps


def _spine(ref, keys):
    # keys: [(strong, k)] in order, all content
    key_of = {ref: {i: key for i, key in enumerate(keys)}}
    content = {ref: set(keys)}
    counts = {ref: collections.Counter(s for s, _ in keys)}
    return ps.Spine(key_of, content, counts)


def _gold(ref, links):
    gv = ps.GoldVerse(ref)
    for key, pos in links.items():
        gv.links[key] = set(pos)
        gv.claimed |= set(pos)
    return {ref: gv}


def test_profile_detects_a_gold_that_never_credits_function_words():
    ref = 1
    toks = {ref: ["el", "siervo", "de", "Dios"]}
    gold = _gold(ref, {("H5650", 0): {1}, ("H0430", 0): {3}})     # only the content words
    is_function = lambda w: w in {"el", "de"}
    p = ps.gold_convention_profile(gold, toks, is_function)
    assert p["function_positions"] == 2 and p["function_credited"] == 0
    assert p["credit_rate"] == 0.0 and p["credits_function_words"] is False


def test_profile_detects_a_gold_that_credits_function_words():
    ref = 1
    toks = {ref: ["the", "servant", "of", "God"]}
    gold = _gold(ref, {("H5650", 0): {0, 1}, ("H0430", 0): {2, 3}})   # articles/of attached to nouns
    is_function = lambda w: w in {"the", "of"}
    p = ps.gold_convention_profile(gold, toks, is_function)
    assert p["credit_rate"] == 1.0 and p["credits_function_words"] is True
    assert p["gold_multiword_share"] == 1.0


def test_neutral_positions_are_unclaimed_function_words_only():
    ref = 1
    toks = {ref: ["el", "siervo", "de", "Dios"]}
    gold = _gold(ref, {("H5650", 0): {1}, ("H0430", 0): {2, 3}})   # gold DID claim 'de' here
    n = ps.neutral_positions(gold, toks, lambda w: w in {"el", "de"})
    assert n == {ref: {0}}


def test_score_strict_penalises_an_uncredited_article_but_conv_does_not():
    ref = 1
    keys = [("H5650", 0), ("H0430", 0)]
    spine = _spine(ref, keys)
    gold = _gold(ref, {("H5650", 0): {1}, ("H0430", 0): {3}})
    ours = ps.Ours(spans={ref: {("H5650", 0): {0, 1}, ("H0430", 0): {3}}})   # we attached 'el' to siervo
    strict = ps.score(gold, ours, spine)
    assert strict.exact == 1 and strict.fp == 1 and strict.over_claimed == 1
    conv = ps.score(gold, ours, spine, neutral={ref: {0, 2}})
    assert conv.exact == 2 and conv.fp == 0 and conv.over_claimed == 0
    assert conv.tp == strict.tp == 2


def test_neutral_never_hides_a_real_steal():
    # position 2 ('de') is claimed by the gold for H0430; we wrongly gave it to H5650 — that is NOT neutral
    ref = 1
    keys = [("H5650", 0), ("H0430", 0)]
    spine = _spine(ref, keys)
    gold = _gold(ref, {("H5650", 0): {1}, ("H0430", 0): {2, 3}})
    ours = ps.Ours(spans={ref: {("H5650", 0): {1, 2}, ("H0430", 0): {3}}})
    toks = {ref: ["el", "siervo", "de", "Dios"]}
    neutral = ps.neutral_positions(gold, toks, lambda w: w in {"el", "de"})
    conv = ps.score(gold, ours, spine, neutral=neutral)
    assert conv.fp == 1 and conv.exact == 0          # the steal still counts against us


def test_neutral_none_is_strict():
    ref = 1
    keys = [("H5650", 0)]
    spine = _spine(ref, keys)
    gold = _gold(ref, {("H5650", 0): {1}})
    ours = ps.Ours(spans={ref: {("H5650", 0): {0, 1}}})
    assert ps.score(gold, ours, spine).row() == ps.score(gold, ours, spine, neutral=None).row()
