"""Offline tests for door43_map.py (F4 follow-up, 2026-09-27) — the spine-side occurrence mapping for
door43_align.py's raw rows. No network calls, no real spine DB: `_FakeHeb` stands in for
`HebrewSource.verse_tokens`."""
import lexeme_aligner.door43_map as dm


def test_normalize_greek_strong_strips_trailing_augment_char():
    assert dm.normalize_greek_strong("G39720") == "G3972"
    assert dm.normalize_greek_strong("G25320") == "G2532"


def test_normalize_greek_strong_none_for_hebrew_prefixed_rows():
    assert dm.normalize_greek_strong("c:H1961") is None
    assert dm.normalize_greek_strong("d:H8199") is None
    assert dm.normalize_greek_strong(None) is None
    assert dm.normalize_greek_strong("") is None


class _Tok:
    def __init__(self, idx, strong, surface):
        self.idx, self.strong, self.surface = idx, strong, surface


class _FakeHeb:
    def __init__(self, by_verse):
        self._by_verse = by_verse

    def verse_tokens(self, book, ch, v):
        return self._by_verse.get((book, ch, v), [])


def _row(ch, v, strong, occ, occs, seq, content):
    return {"book": "TIT", "chapter": ch, "verse": v, "strong": strong, "occurrence": str(occ),
            "occurrences": str(occs), "seq": seq, "content": content, "target_words": []}


def test_map_book_matches_on_recomputed_seq_order_not_on_occurrence_field():
    # two "ὁ" (G3588) tokens in the spine; door43's own occurrence numbering is WRONG (both claim
    # "1 of 1", as if scoped to two different nested groups) but `seq` gives the true text order.
    heb = _FakeHeb({("TIT", 1, 1): [_Tok(0, "G3588", "τῇ"), _Tok(1, "G2316", "θεοῦ"), _Tok(2, "G3588", "τὸ")]})
    rows = [
        _row(1, 1, "G35880", 1, 1, seq=1, content="τῇ"),     # bogus occurrence, correct seq
        _row(1, 1, "G23160", 1, 1, seq=2, content="θεοῦ"),
        _row(1, 1, "G35880", 1, 1, seq=3, content="τὸ"),     # same bogus "1 of 1" as the first row
    ]
    out, stats = dm.map_book(rows, heb, "TIT")
    assert stats["matched"] == 3 and stats["unmatched"] == 0
    by_seq = {r["seq"]: r["h_idx"] for r in out}
    assert by_seq[1] == 0     # first "τῇ" -> spine idx 0
    assert by_seq[2] == 1     # "θεοῦ" -> spine idx 1
    assert by_seq[3] == 2     # second "τῇ" -> spine idx 2, NOT idx 0 again


def test_map_book_non_greek_row_is_unmatched():
    heb = _FakeHeb({("TIT", 1, 1): [_Tok(0, "H1961", "וַֽיְהִי֙")]})
    rows = [_row(1, 1, "c:H1961", 1, 1, seq=1, content="וַֽיְהִי֙")]
    out, stats = dm.map_book(rows, heb, "TIT")
    assert stats["non_greek_strong"] == 1 and stats["unmatched"] == 1
    assert out[0]["h_idx"] is None


def test_map_book_verse_not_in_spine():
    heb = _FakeHeb({})
    rows = [_row(9, 9, "G23160", 1, 1, seq=1, content="θεοῦ")]
    out, stats = dm.map_book(rows, heb, "TIT")
    assert stats["verse_not_in_spine"] == 1
    assert out[0]["h_idx"] is None


def test_verify_content_match_case_and_mark_insensitive():
    rows = [{"h_idx": 0, "content": "Θεοῦ", "spine_surface": "θεοῦ", "book": "TIT", "chapter": 1, "verse": 1}]
    v = dm.verify_content_match(rows)
    assert v["checked"] == 1 and v["content_matches"] == 1


def test_verify_content_match_real_mismatch_flagged():
    rows = [{"h_idx": 0, "content": "τοῦ", "spine_surface": "τῇ", "book": "TIT", "chapter": 1, "verse": 1}]
    v = dm.verify_content_match(rows)
    assert v["checked"] == 1 and v["content_matches"] == 0
    assert v["sample_mismatches"]
