import collections
from types import SimpleNamespace as T

from lexeme_aligner.negation_order import verdict, verse_counts


def tok(idx, strong, content, head=None, mood=None):
    return T(idx=idx, strong=strong, is_content=content, head_idx=head, mood=mood)


def test_hebrew_negator_uses_its_clause_verb():
    heb = [tok(0, "H3808", False, head=1), tok(1, "H7523", True)]
    c = collections.Counter()
    verse_counts(heb, {0: [2], 1: [3]}, "OT", c)              # "You shall not murder": not (2) before murder (3)
    verse_counts(heb, {0: [3], 1: [3]}, "OT", c)              # one target word carries both: fused
    verse_counts(heb, {1: [3]}, "OT", c)                       # negator unaligned, verb aligned
    assert c == {"before": 1, "fused": 1, "unaligned": 1}


def test_greek_negator_takes_the_next_verb():
    heb = [tok(0, "G3756", False), tok(1, "G2064", True, mood="indicative")]
    c = collections.Counter()
    verse_counts(heb, {0: [5], 1: [4]}, "NT", c)
    assert c == {"after": 1}


def test_verdict_bands():
    assert verdict(collections.Counter(before=90, after=10))["direction"] == "before"
    assert verdict(collections.Counter(before=60, after=40))["direction"] == "mixed"
    assert verdict(collections.Counter(before=10))["direction"] is None
