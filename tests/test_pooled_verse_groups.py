"""M5 (2026-09-27): pooled_verse_groups renumbers `tok.idx` to a 0-based position within the pooled
group, but `head_idx` (this token's syntactic head's own spine idx, meaningful only relative to the
head's OWN verse) must be remapped through the same table, not left in stale per-verse numbering —
see the fix's own comment in run_pilot.py. No real spine DB: a fake HebrewSource stands in."""
from lexeme_aligner.hebrew_source import HebToken
from lexeme_aligner.run_pilot import pooled_verse_groups


class _FakeHeb:
    def __init__(self, verses_by_book_ch, tokens_by_verse):
        self._verses = verses_by_book_ch
        self._tokens = tokens_by_verse

    def verses(self, book, ch):
        return self._verses[(book, ch)]

    def verse_tokens(self, book, ch, v):
        # return fresh copies each call, same convention as the real HebrewSource — pooled_verse_groups
        # mutates .idx/.head_idx in place, a real per-call load never aliases across verses.
        return [HebToken(idx=t.idx, surface=t.surface, strong=t.strong, lexeme=t.lexeme,
                          lemma=t.lemma, morph=t.morph, is_content=t.is_content, head_idx=t.head_idx)
                for t in self._tokens[(book, ch, v)]]


def _tok(idx, surface, head_idx=None):
    return HebToken(idx=idx, surface=surface, strong=None, lexeme=None, lemma=None, morph=None,
                     is_content=True, head_idx=head_idx)


def test_head_idx_remapped_across_a_pooled_range():
    # verse 10 has 2 tokens (head_idx=1, i.e. points at its own verse's 2nd token — a self-contained
    # clause); verse 11 has 2 tokens, its 2nd token's head is its own 1st token (head_idx=0).
    heb = _FakeHeb(
        {("RUT", 1): [10, 11]},
        {("RUT", 1, 10): [_tok(0, "a", head_idx=1), _tok(1, "b", head_idx=1)],
         ("RUT", 1, 11): [_tok(0, "c", head_idx=0), _tok(1, "d", head_idx=0)]},
    )
    ranges = {(1, 10): {"text": "pooled", "verse_end": 11}}
    groups = list(pooled_verse_groups("RUT", 1, heb, ranges))
    assert len(groups) == 1
    anchor_v, vs, ve, text, members = groups[0]
    assert (anchor_v, vs, ve) == (10, 10, 11)
    toks = [t for _, t in members]
    assert [t.idx for t in toks] == [0, 1, 2, 3]
    # verse 10's tokens: head_idx=1 (own-verse) -> pooled idx 1, for BOTH of them
    assert toks[0].head_idx == 1 and toks[1].head_idx == 1
    # verse 11's tokens: head_idx=0 (own-verse) -> pooled idx 2 (11's own first token), NOT 0
    # (verse 10's first token) — the exact collision the bug would have produced.
    assert toks[2].head_idx == 2 and toks[3].head_idx == 2


def test_head_idx_none_stays_none():
    heb = _FakeHeb({("RUT", 1): [10]}, {("RUT", 1, 10): [_tok(0, "a", head_idx=None)]})
    ranges = {}
    groups = list(pooled_verse_groups("RUT", 1, heb, ranges))
    _, _, _, _, members = groups[0]
    assert members[0][1].head_idx is None


def test_head_idx_outside_the_group_becomes_none_not_a_stale_index():
    # a head_idx that doesn't correspond to any token actually loaded for that verse (should not
    # happen for a genuine intra-verse head, but must fail safe rather than silently keeping a
    # now-meaningless raw number that could collide with an unrelated pooled position).
    heb = _FakeHeb({("RUT", 1): [10]}, {("RUT", 1, 10): [_tok(0, "a", head_idx=5)]})
    groups = list(pooled_verse_groups("RUT", 1, heb, {}))
    _, _, _, _, members = groups[0]
    assert members[0][1].head_idx is None


def test_unpooled_single_verse_head_idx_unchanged():
    # the ordinary (no range) case: idx renumbering is a no-op (already 0-based per verse), and
    # head_idx should round-trip identically — no regression for the common path.
    heb = _FakeHeb({("RUT", 1): [10]}, {("RUT", 1, 10): [_tok(0, "a", head_idx=1), _tok(1, "b", head_idx=1)]})
    groups = list(pooled_verse_groups("RUT", 1, heb, {}))
    _, _, _, _, members = groups[0]
    toks = [t for _, t in members]
    assert toks[0].head_idx == 1 and toks[1].head_idx == 1
