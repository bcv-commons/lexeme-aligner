"""Offline tests for span_extension.py: direction_for()'s pure logic, and extend_spans()'s end-to-end
behavior against synthetic data (no real spine/Grambank/corpus needed).
"""
import json
from pathlib import Path

import lexeme_aligner.span_extension as se


# --- direction_for --------------------------------------------------------------------------------------
def test_direction_for_before_only():
    assert se.direction_for({"GB074": "1", "GB075": "0"}, "adposition_order") == "before"


def test_direction_for_after_only():
    assert se.direction_for({"GB074": "0", "GB075": "1"}, "adposition_order") == "after"


def test_direction_for_both_is_ambiguous():
    assert se.direction_for({"GB074": "1", "GB075": "1"}, "adposition_order") is None


def test_direction_for_neither_is_none():
    assert se.direction_for({"GB074": "0", "GB075": "0"}, "adposition_order") is None


def test_direction_for_missing_keys_is_none():
    assert se.direction_for({}, "adposition_order") is None


# --- extend_spans (synthetic end-to-end) ----------------------------------------------------------------
def write_align(out_dir, iso, method, book, records):
    fp = out_dir / f"align_{method}_{iso}_{book}.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def pair(h_idx, lexeme, strong, t_idx, content=True):
    return {"h_idx": h_idx, "lexeme": lexeme, "strong": strong, "t_idx": t_idx,
            "target": f"t{t_idx}", "content": content, "score": 0.9}


class _FakeTok:
    def __init__(self, idx, lexeme, state=None, case_=None, construct_group=None, rela=None):
        self.idx = idx
        self.lexeme = lexeme
        self.state = state
        self.case_ = case_
        self.construct_group = construct_group
        self.rela = rela


def _fake_heb_init(has_state=False, has_case=False, has_assimilated_articles=False, assimilated=None,
                   has_phrase=False):
    """A HebrewSource.__init__ replacement carrying just the flags/data extend_spans and
    compute_definite check — everything defaults to "no structured data", so tests written before a
    given signal existed keep exercising the coarser fallback path unchanged. `assimilated`: optional
    {(book, ch, v): {after_idx, ...}} for has_assimilated_articles=True tests."""
    def _init(self):
        self.has_state = has_state
        self.has_case = has_case
        self.has_assimilated_articles = has_assimilated_articles
        self._assimilated_after = assimilated or {}
        self.has_phrase = has_phrase
    return _init


class _FakeVerseRec:
    def __init__(self, book, ch, v, toks, heb):
        self.book, self.ch, self.v, self.toks, self.heb = book, ch, v, toks, heb


def test_extend_spans_postpositional_extends_forward(tmp_path, monkeypatch):
    # Hindi-shaped case: a name at position 2, and the word at position 3 is a function word.
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:name", "H1", [2])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "name", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "NAME", "se", "d"], [_FakeTok(0, "lx:name")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())

    class FakeStop:
        def __init__(self, *a, **k):
            pass

        def is_function(self, word):
            return word == "se"

    monkeypatch.setattr(se, "StopwordFilter", FakeStop)

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_name"] == 1
    pairs = by_book["MAT"][0]["pairs"]
    assert pairs[0]["t_idx"] == [2, 3]
    assert pairs[0]["method"] == "spanext"


def test_extend_spans_prepositional_extends_backward(tmp_path, monkeypatch):
    # Arabic-shaped case: a noun at position 3, position 2 is the case-marking preposition.
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "min", "NOUN", "d"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())

    class FakeStop:
        def __init__(self, *a, **k):
            pass

        def is_function(self, word):
            return word == "min"

    monkeypatch.setattr(se, "StopwordFilter", FakeStop)

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1
    pairs = by_book["MAT"][0]["pairs"]
    assert pairs[0]["t_idx"] == [2, 3]


def test_extend_spans_never_claims_an_already_taken_position(tmp_path, monkeypatch):
    # Two pairs in the same verse: the candidate slot for pair A is already claimed by pair B.
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:name", "H1", [2]), pair(1, "lx:other", "H2", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:name": "name", "lx:other": "verb"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "name", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "NAME", "OTHER", "d"],
                      [_FakeTok(0, "lx:name"), _FakeTok(1, "lx:other")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter", lambda *a, **k: type("S", (), {"is_function": lambda self, w: True})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert "extended_name" not in stats


def test_extend_spans_structured_signal_confirms_extension(tmp_path, monkeypatch):
    """A pos-tag match with structured data available and confirming (construct) still extends — the
    pos-tag path and the additive structural path agree here, so either alone would produce this result."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "min", "NOUN", "d"],
                      [_FakeTok(0, "lx:noun", state="construct")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_state=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "min"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]


def test_extend_spans_pos_tag_gate_fires_regardless_of_structured_signal(tmp_path, monkeypatch):
    """The additive design (2026-09-24, chosen after measuring the alternative — see
    case_marking_direction's comment in span_extension.py): the ORIGINAL pos-tag gate still fires even
    when structured data is available and says this occurrence isn't in the marked relation (absolute,
    not construct) — a version that REQUIRED confirmation instead measured worse on real Clear gold for
    both hin and arb, trading away more recall than it gained in precision."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "min", "NOUN", "d"],
                      [_FakeTok(0, "lx:noun", state="absolute")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_state=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "min"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1


def test_extend_spans_structured_signal_adds_a_case_the_pos_tag_gate_missed(tmp_path, monkeypatch):
    """The additive path: a lexeme with NO pos-tag match at all (missing from the prior pack, or a POS
    category the phase-1 audit didn't flag) still extends when its OWN occurrence's structured state/
    case_ confirms case_marking independently — a case the original pos-tag-only gate could never catch."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:mystery", "H1", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))                # no POS entry at all
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "min", "MYSTERY", "d"],
                      [_FakeTok(0, "lx:mystery", state="construct")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_state=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "min"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_struct"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]


def test_extend_spans_greek_case_confirms_extension(tmp_path, monkeypatch):
    """The Greek half of the tightened gate: case_ in {genitive, dative} confirms, same as Hebrew state."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "G1", [3])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "1", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "of", "NOUN", "d"],
                      [_FakeTok(0, "lx:noun", case_="genitive")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_case=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "of"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1


def test_extend_spans_possession_affix_extends_before_when_possessor_precedes(tmp_path, monkeypatch):
    """GB065 (ternary, not a before/after pair) = '1' means the possessor precedes the possessum (English
    "his house") — extends backward from the possessed noun's span, through the standard pos-tag path
    (no bypass needed: the phase-1 audit already flags possession_affix for real languages like eng)."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [2])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB065": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "possession_affix", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "his", "house", "d"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "his"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [1, 2]


def test_extend_spans_possession_affix_ambiguous_order_skips(tmp_path, monkeypatch):
    """GB065 = '3' (both orders occur / free) must never guess a direction."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [2])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB065": "3"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {
        "findings": [{"risk": "possession_affix", "pos": "noun", "prompt_hint": "..."}]})
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}
    assert "skipped" in stats


def test_extend_spans_second_risk_on_same_pos_gets_a_fair_shot(tmp_path, monkeypatch):
    """Two flagged risks sharing a pos, with DIFFERENT directions (articles "before", possession_affix
    "after") — articles' own candidate ("X", not a function word) must fail and fall through to trying
    possession_affix's candidate ("his") on the OTHER side, rather than giving up once articles (listed
    first) has an entry for this pos at all. A version that kept only the first (pos -> single direction)
    match let `articles` silently block `possession_affix` from ever firing, even for occurrences its own
    candidate can't use — caught by testing on real engbsb data: zero possession_affix extensions despite
    a correctly flagged (pos, direction)."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [2])]}])

    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw",
                        lambda _iso, path=None: {"GB020": "1", "GB021": "0", "GB022": "1", "GB023": "0",
                                                  "GB065": "2"})  # articles=before (prenominal); GB065=2=after
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."},
        {"risk": "possession_affix", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "X", "noun", "his"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "his"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_noun"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]


def test_extend_spans_no_grambank_coverage_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}
    assert "skipped" in stats


def test_extend_spans_no_flagged_finding_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": []})
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}
    assert "skipped" in stats


# --- compute_definite (Step 1) --------------------------------------------------------------------------
def test_compute_definite_previous_token_is_the_article_lexeme():
    toks = [_FakeTok(0, "hbo:1886a"), _FakeTok(1, "hbo:1234")]
    d = se.compute_definite(toks, {}, set())
    assert d[0] is False and d[1] is True


def test_compute_definite_assimilated_article_after_idx_plus_one():
    toks = [_FakeTok(3, "hbo:9999"), _FakeTok(4, "hbo:1234")]
    d = se.compute_definite(toks, {}, {3})                          # after_idx=3 -> idx 4 is definite
    assert d[4] is True and d[3] is False


def test_compute_definite_proper_name_is_inherently_definite():
    toks = [_FakeTok(0, "hbo:name1")]
    d = se.compute_definite(toks, {"hbo:name1": "name"}, set())
    assert d[0] is True


def test_compute_definite_plain_content_word_is_not_definite():
    toks = [_FakeTok(0, "hbo:plain"), _FakeTok(1, "hbo:1234")]
    d = se.compute_definite(toks, {"hbo:plain": "noun"}, set())
    assert d[1] is False


def test_compute_definite_construct_head_inherits_from_definite_rectum():
    # "servant of David" — David (a name, inherently definite) is the rectum; the construct HEAD
    # ("servant") inherits definiteness from it, even though the head itself takes no article.
    head = _FakeTok(0, "hbo:servant", state="construct", construct_group="g1")
    rectum = _FakeTok(1, "hbo:david", state="absolute", construct_group="g1")
    d = se.compute_definite([head, rectum], {"hbo:david": "name"}, set())
    assert d[0] is True and d[1] is True


def test_compute_definite_construct_head_stays_indefinite_without_a_definite_rectum():
    head = _FakeTok(0, "hbo:servant", state="construct", construct_group="g1")
    rectum = _FakeTok(1, "hbo:king", state="absolute", construct_group="g1")     # not a name, not marked
    d = se.compute_definite([head, rectum], {}, set())
    assert d[0] is False and d[1] is False


def test_compute_definite_non_construct_group_member_does_not_inherit():
    # only `state == "construct"` members inherit; a bare absolute member sharing the group is untouched
    # (it isn't a HEAD needing the inherited marking — this mirrors real construct-chain semantics).
    a = _FakeTok(0, "hbo:a", state="construct", construct_group="g1")
    b = _FakeTok(1, "hbo:name", state="absolute", construct_group="g1")
    c = _FakeTok(2, "hbo:c", state=None, construct_group="g1")        # not a construct head
    d = se.compute_definite([a, b, c], {"hbo:name": "name"}, set())
    assert d[0] is True and d[2] is False


# --- extend_spans: definite_trigger (Step 1, opt-in) -----------------------------------------------------
def test_extend_spans_definite_trigger_off_by_default(tmp_path, monkeypatch):
    """definite_trigger defaults False — a definite occurrence with no POS-tag/case_marking match gets
    NO extension unless the flag is passed, per the plan's "opt-in until measured" rule."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:name", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))       # no POS entry -> pos-tag path can't fire
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB022": "1", "GB023": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."}]})    # flagged for "noun", not "name"
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "the", "NAME", "d"],
                      [_FakeTok(0, "lx:name")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__",
                        _fake_heb_init(has_state=True, has_assimilated_articles=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "the"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}                                              # definite_trigger not passed -> no extension


def test_extend_spans_definite_trigger_extends_a_proper_name_when_enabled(tmp_path, monkeypatch):
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:name", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB022": "1", "GB023": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."}]})    # flagged for "noun" only
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "the", "NAME", "d"],
                      [_FakeTok(0, "lx:name")])])                       # lex_pos says "name" -> definite
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__",
                        _fake_heb_init(has_state=True, has_assimilated_articles=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "the"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     definite_trigger=True)
    assert stats["extended_definite"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]
    # 2026-09-28: the added clean-text position (2) is embedded after ':' -- this is what lets a
    # client revert the fold (published_span minus this position = the pre-extension span).
    assert by_book["MAT"][0]["pairs"][0]["prior"] == "spanext_definite_before:2"


def test_extend_spans_two_sided_extension_definite_before_and_case_marking_after(tmp_path, monkeypatch):
    """The Step 1 "the covenant OF" case: a definite noun in a construct relation gets BOTH a leading
    article (definite_trigger) AND a trailing genitive (case_marking's additive struct path) in ONE
    pass — two independent signals on two different sides of the same occurrence. Definiteness comes
    from the direct-precedent rule: a separate Hebrew token (h_idx 0, not itself given an align pair —
    the free-standing article is usually not separately aligned) whose lexeme is the article, at spine
    idx 0, immediately preceding the covenant token at idx 1."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(1, "lx:covenant", "H1", [1])]}])          # h_idx=1, target position 1
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))         # no POS entry -> only additive paths
    # GB022=1/GB023=0 -> article_order "before"; GB074=0/GB075=1 -> adposition_order "after"
    monkeypatch.setattr(se, "load_grambank_raw",
                        lambda _iso, path=None: {"GB022": "1", "GB023": "0", "GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["the", "COVENANT", "of", "d"],
                      [_FakeTok(0, "hbo:1886a"),
                       _FakeTok(1, "lx:covenant", state="construct")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_state=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w in ("the", "of")})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     definite_trigger=True)
    assert stats["extended_struct"] == 1 and stats["extended_definite"] == 1
    pairs = by_book["MAT"][0]["pairs"][0]
    assert pairs["t_idx"] == [0, 1, 2]                                    # "the" + COVENANT + "of"
    # added positions: 0 (before) and 2 (after) -- see the single-sided test above for why these are embedded.
    assert "spanext_definite_before:0" in pairs["prior"] and "spanext_struct_after:2" in pairs["prior"]


def test_extend_spans_definite_trigger_never_reclaims_a_side_already_used(tmp_path, monkeypatch):
    """The one-extension-per-side cap: if the pos-tag path already claimed "before", the definite
    trigger (also "before") must NOT try to grab a second word on the same side."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:name", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:name": "name"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB022": "1", "GB023": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "name", "prompt_hint": "..."}]})       # pos-tag ALSO flags "name" now
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "the", "NAME", "d"],
                      [_FakeTok(0, "lx:name")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__",
                        _fake_heb_init(has_state=True, has_assimilated_articles=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "the"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     definite_trigger=True)
    # only ONE word grabbed ("the"), not two — the definite path found "before" already used and skipped
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]
    assert stats.get("extended_definite", 0) == 0                          # pos-tag path claimed it first
    assert stats["extended_name"] == 1


# --- extend_spans: relation_trigger (Step 1, Correction 1, opt-in) ---------------------------------------
def test_extend_spans_relation_trigger_off_by_default(tmp_path, monkeypatch):
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:rectum", "H1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "RECTUM", "ka", "d"],
                      [_FakeTok(0, "lx:rectum", rela="rec")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}                                               # relation_trigger not passed


def test_extend_spans_relation_trigger_extends_the_possessor_forward_postpositional(tmp_path, monkeypatch):
    """Correction 1's own example: a postpositional language ("prabhu KA sevak") needs the function
    word attached to the POSSESSOR, forward — a DIFFERENT token from the construct HEAD the existing
    `state=="construct"` struct path extends."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:rectum", "H1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "RECTUM", "ka", "d"],
                      [_FakeTok(0, "lx:rectum", rela="rec")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     relation_trigger=True)
    assert stats["extended_possessor"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [1, 2]             # RECTUM + "ka"


def test_extend_spans_relation_trigger_never_touches_a_construct_head(tmp_path, monkeypatch):
    """Additive, not a replacement: an occurrence with `rela` unset (a construct HEAD, not a rectum)
    must NOT be touched by the new possessor path — only the pre-existing struct path (state==
    "construct") may extend a head, and only if it independently fires."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:head", "H1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "HEAD", "ka", "d"],
                      [_FakeTok(0, "lx:head", rela=None)])])            # NOT a rectum, no state either
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     relation_trigger=True)
    assert by_book == {}


# --- extend_spans: Step 2 typology-table fallback for Grambank-uncovered languages ------------------------
def test_extend_spans_falls_back_to_typology_when_grambank_is_none(tmp_path, monkeypatch):
    """A language absent from Grambank entirely (spa/ben/asm — Step 2's own target languages) must
    still get a direction from gram-struct's own merged view (D4a, 2026-09-27 — was the narrower
    typology.py table before) instead of being skipped outright."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)   # NOT in Grambank at all
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction",
                        lambda iso, slot, out_dir=gs.OUT_DIR: "before" if slot == "article" else None)
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "X", "NOUN", "d"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "X"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     typology_fallback=True, typology_fallback_articles=True)
    assert stats["extended_noun"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]


def test_extend_spans_typology_fallback_alone_does_not_enable_articles(tmp_path, monkeypatch):
    """D4a follow-up (2026-09-27): gram_struct newly resolves an `article` direction for spa that the
    old typology table never could, which would otherwise silently activate the 'articles' mechanism
    for any typology_fallback=true language. Dedicated measurement found ~38-41% artifact-corrected
    precision there (real conflicts: genealogy name-list/genitive-construct steals) — well below what
    `typology_fallback`'s own possessor/case_marking verdict was actually validated against. So
    `typology_fallback=True` alone (spa's own real, shipped setting) must NOT fire the articles risk;
    only the separate `typology_fallback_articles` flag may."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)   # NOT in Grambank at all
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction",
                        lambda iso, slot, out_dir=gs.OUT_DIR: "before" if slot == "article" else None)
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "X", "NOUN", "d"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "X"})())

    # typology_fallback=True but typology_fallback_articles left at its default (None -> off, no
    # config entry for "fake") -- the only direction gram_struct has at all is "article", so if the
    # split gating is broken, `active` ends up empty and the whole call returns "skipped" instead.
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     typology_fallback=True)
    assert stats.get("skipped") == "no flagged (pos, direction) combination for this language"
    assert by_book == {}


def test_extend_spans_typology_fallback_off_by_default_skips_grambank_absent_language(tmp_path, monkeypatch):
    """typology_fallback defaults False — measured net-negative for 2 of 3 languages tested against the
    old, narrower table (ben/asm) — so a Grambank-absent language must be skipped, not silently routed
    through gram-struct's merged view, unless explicitly opted in."""
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction", lambda iso, slot, out_dir=gs.OUT_DIR: "before")   # would resolve if asked
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}
    assert "skipped" in stats


def test_extend_spans_skips_when_neither_grambank_nor_typology_has_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction", lambda iso, slot, out_dir=gs.OUT_DIR: None)   # nothing anywhere
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     typology_fallback=True)
    assert by_book == {}
    assert "skipped" in stats


# --- build_surface_identity / _has_strong_identity (the candidate-word identity guard) --------------------
def test_build_surface_identity_dominant_lexeme_and_share():
    unioned = {
        1: {0: pair(0, "lx:autos", "G1", [5]), 1: pair(1, "lx:noun", "H1", [3, 4])},
        2: {0: pair(0, "lx:autos", "G1", [2])},
        3: {0: pair(0, "lx:autos", "G1", [7])},
    }
    lexeme_of = {1: {0: "lx:autos", 1: "lx:noun"}, 2: {0: "lx:autos"}, 3: {0: "lx:autos"}}
    verse_toks = {1: ["a", "b", "c", "ses", "x", "ses", "y"],
                 2: ["a", "b", "ses"],
                 3: ["a", "b", "c", "d", "e", "f", "g", "ses"]}
    identity = se.build_surface_identity(unioned, lexeme_of, verse_toks)
    assert identity["ses"] == ("lx:autos", 1.0, 3)


def test_build_surface_identity_excludes_multi_word_spans():
    # a 2-word span never attributes identity to either of its member words.
    unioned = {1: {0: pair(0, "lx:noun", "H1", [3, 4])}}
    lexeme_of = {1: {0: "lx:noun"}}
    verse_toks = {1: ["a", "b", "c", "tempat", "pengirikan"]}
    identity = se.build_surface_identity(unioned, lexeme_of, verse_toks)
    assert "tempat" not in identity and "pengirikan" not in identity


def test_build_surface_identity_spread_across_many_lexemes_has_low_share():
    # a genuine function word attaches to MANY different lexemes -> low share for any one.
    unioned = {i: {0: pair(0, f"lx:noun{i}", "H1", [0])} for i in range(10)}
    lexeme_of = {i: {0: f"lx:noun{i}"} for i in range(10)}
    verse_toks = {i: ["ki", "x"] for i in range(10)}
    identity = se.build_surface_identity(unioned, lexeme_of, verse_toks)
    lexeme, share, total = identity["ki"]
    assert total == 10 and share == 0.1                     # spread evenly -> no dominant identity


def test_has_strong_identity_blocks_a_different_lexemes_word():
    identity = {"ses": ("lx:autos", 1.0, 10)}
    assert se._has_strong_identity("ses", identity, current_lexeme="lx:disciples") is True


def test_has_strong_identity_allows_when_identity_matches_current_lexeme():
    identity = {"ses": ("lx:autos", 1.0, 10)}
    assert se._has_strong_identity("ses", identity, current_lexeme="lx:autos") is False


def test_has_strong_identity_allows_below_count_threshold():
    identity = {"rare": ("lx:x", 1.0, 2)}                    # only 2 occurrences — not enough evidence
    assert se._has_strong_identity("rare", identity, current_lexeme="lx:other") is False


def test_has_strong_identity_allows_below_share_threshold():
    identity = {"ki": ("lx:x", 0.3, 10)}                      # spread thin — genuine function word shape
    assert se._has_strong_identity("ki", identity, current_lexeme="lx:other") is False


def test_has_strong_identity_allows_a_pronoun_with_shifting_referent_despite_high_volume():
    """A volume-only ("high total, regardless of share") second rule was tried and REJECTED — it
    regressed Hindi's own already-shipped case_marking win, since Hindi's genuine postpositions are
    JUST AS high-volume as a pronoun whose referent changes every occurrence (see the module's own
    _IDENTITY_SHARE_MAX comment for the full measurement). Only the share rule is live: a word this
    common but this spread across lexemes (low share) must stay allowed."""
    identity = {"সে": ("grc:1510", 0.12, 184)}                # low share, high volume -> NOT blocked
    assert se._has_strong_identity("সে", identity, current_lexeme="lx:other") is False


def test_has_strong_identity_allows_low_share_low_volume_case_marker():
    identity = {"র": ("hbo:3117", 0.31, 51)}                  # low share AND low volume -> genuine marker
    assert se._has_strong_identity("র", identity, current_lexeme="lx:other") is False


def test_has_strong_identity_unknown_word_is_allowed():
    assert se._has_strong_identity("unknown", {}, current_lexeme="lx:other") is False


def test_extend_spans_identity_guard_blocks_a_steal(tmp_path, monkeypatch):
    """The integration case this whole guard exists for: "ses disciples" — "ses" already has a strong
    identity as autos/G0846 elsewhere in the SAME corpus, so possession_affix must NOT grab it."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT", [
        {"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
         "pairs": [pair(0, "lx:autos", "G1", [0]), pair(1, "lx:autos", "G1", [4]),
                  pair(2, "lx:autos", "G1", [7]), pair(3, "lx:autos", "G1", [10]),
                  pair(4, "lx:autos", "G1", [13]),                     # 5 confirmed single-word "ses"
                  pair(5, "lx:disciples", "H1", [16])]}])              # the noun to extend
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:disciples": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB432": "1", "GB065": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "possession_affix", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["ses", "b", "c", "d", "ses", "e", "f", "ses", "g", "h", "ses",
                                    "i", "j", "ses", "k", "l", "disciples"],
                      [_FakeTok(i, "lx:autos") for i in range(5)] + [_FakeTok(5, "lx:disciples")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ses"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}                                      # blocked — "ses" belongs to lx:autos
    assert stats.get("extended_noun", 0) == 0


# --- _chain_neighbor_boundary (the multi-member construct-chain fix) -------------------------------------
def test_chain_neighbor_boundary_blocks_crossing_into_the_previous_members_span():
    a = _FakeTok(0, "lx:a", construct_group="g1")
    b = _FakeTok(1, "lx:b", construct_group="g1")
    members = [a, b]
    unioned_verse = {0: {"t_idx": [2, 3]}}                     # member a already claims target 2-3
    boundary = se._chain_neighbor_boundary(members, this_idx=1, direction="before", unioned_verse=unioned_verse)
    assert boundary == 3                                       # member b may not claim <= 3


def test_chain_neighbor_boundary_blocks_crossing_into_the_next_members_span():
    a = _FakeTok(0, "lx:a", construct_group="g1")
    b = _FakeTok(1, "lx:b", construct_group="g1")
    members = [a, b]
    unioned_verse = {1: {"t_idx": [5]}}                        # member b already claims target 5
    boundary = se._chain_neighbor_boundary(members, this_idx=0, direction="after", unioned_verse=unioned_verse)
    assert boundary == 5                                       # member a may not claim >= 5


def test_chain_neighbor_boundary_none_at_the_end_of_the_chain():
    a = _FakeTok(0, "lx:a", construct_group="g1")
    b = _FakeTok(1, "lx:b", construct_group="g1")
    members = [a, b]
    assert se._chain_neighbor_boundary(members, this_idx=1, direction="after", unioned_verse={}) is None
    assert se._chain_neighbor_boundary(members, this_idx=0, direction="before", unioned_verse={}) is None


def test_chain_neighbor_boundary_none_when_neighbor_unaligned():
    a = _FakeTok(0, "lx:a", construct_group="g1")
    b = _FakeTok(1, "lx:b", construct_group="g1")
    members = [a, b]
    assert se._chain_neighbor_boundary(members, this_idx=1, direction="before", unioned_verse={}) is None


def test_extend_spans_relation_trigger_respects_chain_boundary(tmp_path, monkeypatch):
    """A 2-member construct_group where the FIRST member already claims the only candidate slot the
    naive rule would have handed to the SECOND member — the boundary must block it."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:first", "H1", [1]), pair(1, "lx:second", "H2", [2])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "FIRST", "SECOND", "ka", "d"],
                      [_FakeTok(0, "lx:first", rela="rec", construct_group="g1"),
                       _FakeTok(1, "lx:second", rela="rec", construct_group="g1")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     relation_trigger=True)
    # "second" (member 1, idx 2) can grab "ka" (idx 3) forward — no boundary on that side (last in chain).
    # "first" (member 0, idx 1) would also want "ka" forward, but member 1's own span (idx 2) bounds it.
    assert stats["extended_possessor"] == 1
    pairs = {p["h_idx"]: p for p in by_book["MAT"][0]["pairs"]}
    assert 1 in pairs and 0 not in pairs


# --- load_spanext_flags / config-driven flag resolution (per-language reuse mechanism) --------------------
def test_load_spanext_flags_reads_a_languages_entry(tmp_path):
    fp = tmp_path / "spanext_flags.json"
    fp.write_text('{"hin": {"relation_trigger": true, "_note": "measured, kept"}}', encoding="utf-8")
    assert se.load_spanext_flags("hin", path=fp) == {"relation_trigger": True}


def test_load_spanext_flags_skips_free_text_keys(tmp_path):
    fp = tmp_path / "spanext_flags.json"
    fp.write_text('{"ben": {"typology_fallback": false, "_note": "not a bool, must be skipped"}}',
                 encoding="utf-8")
    assert se.load_spanext_flags("ben", path=fp) == {"typology_fallback": False}


def test_load_spanext_flags_missing_language_is_empty(tmp_path):
    fp = tmp_path / "spanext_flags.json"
    fp.write_text('{"hin": {"relation_trigger": true}}', encoding="utf-8")
    assert se.load_spanext_flags("xyz", path=fp) == {}


def test_load_spanext_flags_missing_file_is_empty(tmp_path):
    assert se.load_spanext_flags("hin", path=tmp_path / "nope.json") == {}


def test_extend_spans_consults_config_when_flag_not_explicitly_passed(tmp_path, monkeypatch):
    """The core reuse mechanism: a language recorded with relation_trigger=true in the config gets it
    automatically, with NO CLI flag / explicit Python argument needed."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:rectum", "H1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "RECTUM", "ka", "d"],
                      [_FakeTok(0, "lx:rectum", rela="rec")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())
    flags_fp = tmp_path / "flags.json"
    flags_fp.write_text('{"fake": {"relation_trigger": true}}', encoding="utf-8")
    monkeypatch.setattr(se, "_SPANEXT_FLAGS_FILE", flags_fp)

    # NOTE: relation_trigger is NOT passed at all — must come from the config file.
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert stats["extended_possessor"] == 1


def test_extend_spans_explicit_false_overrides_a_true_config_entry(tmp_path, monkeypatch):
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:rectum", "H1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "RECTUM", "ka", "d"],
                      [_FakeTok(0, "lx:rectum", rela="rec")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init(has_phrase=True))
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "ka"})())
    flags_fp = tmp_path / "flags.json"
    flags_fp.write_text('{"fake": {"relation_trigger": true}}', encoding="utf-8")
    monkeypatch.setattr(se, "_SPANEXT_FLAGS_FILE", flags_fp)

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     relation_trigger=False)      # explicit override beats the config
    assert stats.get("extended_possessor", 0) == 0


# --- diagnose() (pre-flight triage report, not an automatic classifier) ----------------------------------
def test_diagnose_reports_block_rate_and_top_stopwords(tmp_path, monkeypatch):
    """diagnose() surfaces both signals a human needs: the block rate (from forcing every opt-in
    trigger on) and a ranked list of this language's own stopwords that already carry a strong
    single-word identity — reusing the exact "ses" steal scenario the identity guard itself is
    tested against, so a fully-blocked trigger shows rate 1.0 and "ses" tops the stopword table."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT", [
        {"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
         "pairs": [pair(0, "lx:autos", "G1", [0]), pair(1, "lx:autos", "G1", [4]),
                  pair(2, "lx:autos", "G1", [7]), pair(3, "lx:autos", "G1", [10]),
                  pair(4, "lx:autos", "G1", [13]),
                  pair(5, "lx:disciples", "H1", [16])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:disciples": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB432": "1", "GB065": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "possession_affix", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        # "before"-direction candidate for "disciples" (idx 16) is position 15 — placed as another
        # "ses" so the identity guard actually reaches (and blocks) it; the 5 pair()-attributed "ses"
        # occurrences at 0/4/7/10/13 are what give it its strong lx:autos identity in the first place.
        _FakeVerseRec("MAT", 1, 1, ["ses", "b", "c", "d", "ses", "e", "f", "ses", "g", "h", "ses",
                                    "i", "j", "ses", "k", "ses", "disciples"],
                      [_FakeTok(i, "lx:autos") for i in range(5)] + [_FakeTok(5, "lx:disciples")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"words": {"ses"},
                                                        "is_function": lambda self, w: w == "ses"})())

    report = se.diagnose("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert report["block_rates"]["noun"] == {"checked": 1, "blocked": 1, "rate": 1.0}
    top = report["top_high_volume_stopwords"]
    assert top[0] == {"word": "ses", "total": 5, "share": 1.0, "lexeme": "lx:autos"}
    assert report["n_stopwords_with_identity"] == 1


def test_diagnose_handles_no_flagged_language_gracefully(tmp_path, monkeypatch):
    """No findings at all -> extend_spans returns its own early skip; diagnose() must not crash on the
    resulting stats dict having no identity_checked_/identity_blocked_ keys."""
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "0"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": []})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"words": set(),
                                                        "is_function": lambda self, w: False})())

    report = se.diagnose("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert report["block_rates"] == {}
    assert report["top_high_volume_stopwords"] == []


def test_direction_for_falls_back_to_gram_struct_merged_direction_when_grambank_silent(monkeypatch):
    # D4a (2026-09-27): the fallback now reads gram-struct's own merged view, not the older, narrower
    # typology.py WALS/lang2vec-only table.
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction", lambda iso, slot, out_dir=gs.OUT_DIR: "after")
    assert se.direction_for({}, "adposition_order", iso="xx") == "after"


def test_possession_direction_for_falls_back_to_gram_struct_merged_direction(monkeypatch):
    import lexeme_aligner.gram_struct as gs
    monkeypatch.setattr(gs, "merged_direction", lambda iso, slot, out_dir=gs.OUT_DIR: "before")
    assert se.possession_direction_for({}, iso="xx") == "before"


def test_phrase_window_gate_blocks_a_candidate_inside_another_phrases_window(tmp_path, monkeypatch):
    """P2 (2026-09-27): token 0 (phrase A) wants to extend forward onto position 3 — but position 3 lies
    inside phrase B's aligned window (B's own member sits at 2 and 4), so it is B's word."""
    write_align(tmp_path, "fakeiso", "eflomal", "RUT",
               [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [1]), pair(1, "lx:other", "H2", [2]), pair(2, "lx:other2", "H3", [4])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun", "lx:other": "noun", "lx:other2": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})   # postpositional
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "noun", "prompt_hint": "..."}]})
    toks = ["a", "NOUN", "B1", "X", "B2", "e"]
    t0, t1, t2 = _FakeTok(0, "lx:noun"), _FakeTok(1, "lx:other"), _FakeTok(2, "lx:other2")
    t0.phrase_id, t1.phrase_id, t2.phrase_id = "A", "B", "B"
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("RUT", 1, 1, toks, [t0, t1, t2])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "X"})())
    # without the gate: token 0 grabs X (position 2? no — forward from 1 is 2, which is claimed; so nothing)
    # make the geometry explicit: token 0 at position 1 extends forward to 2 (claimed by t1) -> fails;
    # so put the noun at position 2 and X at 3 instead.
    write_align(tmp_path, "fakeiso", "eflomal", "RUT",
               [{"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [2]), pair(1, "lx:other", "H2", [1]), pair(2, "lx:other2", "H3", [4])]}])
    toks = ["a", "B1", "NOUN", "X", "B2", "e"]
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("RUT", 1, 1, toks, [t0, t1, t2])])
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["RUT"], out_dir=tmp_path,
                                     phrase_window_gate=False)
    assert stats.get("extended_noun") == 1                      # ungated: X taken
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["RUT"], out_dir=tmp_path,
                                     phrase_window_gate=True)
    assert stats.get("phrase_blocked_noun") == 1 and not stats.get("extended_noun")


def test_name_guard_blocks_a_candidate_that_is_another_names_transliteration(tmp_path, monkeypatch):
    """P4 (2026-09-27): a Latin-script target where the 'stopword' next to name A is actually name B."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:cush", "G1", [1])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:cush": "name", "lx:raema": "name"},
                                                        {"lx:cush": "Chus", "lx:raema": "Raema"}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: {"GB074": "0", "GB075": "1"})
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "case_marking", "pos": "name", "prompt_hint": "..."}]})
    toks = ["y", "Chus", "raema", "z"]
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, toks, [_FakeTok(0, "lx:cush"), _FakeTok(1, "lx:raema")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    # pretend the stopword list (wrongly) contains 'raema' — the real-data situation behind the steals
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "raema"})())
    _, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path, name_guard=False)
    assert stats.get("extended_name") == 1
    _, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path, name_guard=True)
    assert stats.get("name_blocked_name") == 1 and not stats.get("extended_name")


# --- P6 (2026-09-27): Greek agreement-based NP chunking, the NT fallback for phrase_id -------------

class _AgreeTok:
    def __init__(self, idx, case_=None, number=None, gender=None):
        self.idx, self.case_, self.number, self.gender = idx, case_, number, gender


def test_greek_np_chunks_groups_adjacent_matching_agreement():
    toks = [_AgreeTok(0, "nominative", "singular", "masculine"),
           _AgreeTok(1, "nominative", "singular", "masculine"),
           _AgreeTok(2, "nominative", "singular", "masculine"),
           _AgreeTok(3)]
    chunks = se.greek_np_chunks(toks)
    assert chunks == {0: "npchunk1", 1: "npchunk1", 2: "npchunk1"}
    assert 3 not in chunks


def test_greek_np_chunks_breaks_on_a_different_agreement_triple():
    toks = [_AgreeTok(0, "nominative", "singular", "masculine"),
           _AgreeTok(1, "genitive", "singular", "feminine"),
           _AgreeTok(2, "genitive", "singular", "feminine")]
    chunks = se.greek_np_chunks(toks)
    assert 0 not in chunks                 # singleton triple, dropped
    assert chunks == {1: "npchunk2", 2: "npchunk2"}


def test_greek_np_chunks_no_bridging_over_an_ungoverned_token():
    toks = [_AgreeTok(0, "nominative", "singular", "masculine"),
           _AgreeTok(1),
           _AgreeTok(2, "nominative", "singular", "masculine")]
    chunks = se.greek_np_chunks(toks)
    assert chunks == {}                    # each side is a singleton once the gap breaks the run


# --- E6 flip: base_mechanisms gate (2026-09-29) -------------------------------------------------------------

def _flags_file(tmp_path, doc):
    fp = tmp_path / "flags.json"
    fp.write_text(json.dumps(doc), encoding="utf-8")
    return fp


def test_load_spanext_flags_tag_entry_overrides_language_entry(tmp_path):
    fp = _flags_file(tmp_path, {"zz": {"relation_trigger": True, "base_mechanisms": True},
                                "zz_wtc": {"base_mechanisms": False, "_note": "x"}})
    assert se.load_spanext_flags("zz", fp) == {"relation_trigger": True, "base_mechanisms": True}
    assert se.load_spanext_flags("zz", fp, tag="zz_vdv") == {"relation_trigger": True, "base_mechanisms": True}
    assert se.load_spanext_flags("zz", fp, tag="zz_wtc") == {"relation_trigger": True, "base_mechanisms": False}
    assert se.base_mechanisms_enabled({}) is True
    assert se.base_mechanisms_enabled({"base_mechanisms": False}) is False


def test_extend_spans_skips_entirely_when_base_mechanisms_gated_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(se, "load_spanext_flags", lambda iso, path=None, tag=None: {"base_mechanisms": False})
    monkeypatch.setattr(se, "load_priors", lambda _pp: called.append("priors") or ({}, {}))
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {} and stats["gated_off"] is True and called == []   # nothing else even loaded


def test_explicit_base_mechanisms_true_overrides_the_config_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "load_spanext_flags", lambda iso, path=None, tag=None: {"base_mechanisms": False})
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)   # -> skipped for another reason
    _, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path, base_mechanisms=True)
    assert "gated_off" not in stats


def test_shipped_config_never_gates_off_an_edition_that_has_opt_in_triggers():
    """extend_spans skips the WHOLE layer when base_mechanisms is false, which would silently drop an opt-in
    trigger recorded true for the same language/edition."""
    doc = json.loads(Path("config/spanext_flags.json").read_text(encoding="utf-8"))
    opt_in = ("definite_trigger", "relation_trigger", "typology_fallback", "typology_fallback_articles",
              "typed_gate", "phrase_window_gate", "name_guard")
    lang_of = lambda k: k.split("_")[0][:3]
    for key, entry in doc.items():
        if key.startswith("_") or not isinstance(entry, dict) or entry.get("base_mechanisms") is not False:
            continue
        eff = dict(doc.get(lang_of(key), {})); eff.update(entry)
        assert not any(eff.get(f) is True for f in opt_in), key


def test_a_skipped_rerun_removes_the_stale_layer_of_the_books_it_covers(tmp_path, monkeypatch):
    """2026-10-05: stale spanext files used to survive any skip except `gated_off` (16 editions, e.g. hinirv)."""
    for book in ("GEN", "EXO"):
        (tmp_path / f"align_spanext_zz_{book}.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(se, "extend_spans", lambda *a, **k: ({}, {"skipped": "no flagged (pos, direction) combination"}))
    assert se.main(["--iso", "zz", "--publish-iso", "zz", "--usj-dir", str(tmp_path), "--out", str(tmp_path),
                    "--book", "GEN"]) == 0
    assert not (tmp_path / "align_spanext_zz_GEN.jsonl").exists()
    assert (tmp_path / "align_spanext_zz_EXO.jsonl").exists()          # a book outside this run is left alone


# --- 2026-10-09: per-mechanism switch and the possession occurrence gate --------------------------------------
def test_nominal_possessor_idx_needs_a_content_rectum_or_greek_genitive():
    from types import SimpleNamespace as T
    from lexeme_aligner.span_extension import nominal_possessor_idx
    tok = lambda idx, content, group=None, case_=None, lexeme="x": T(idx=idx, is_content=content, strong="H1" if content else None,  # noqa: E731
                                                                     construct_group=group, case_=case_, lexeme=lexeme)
    # בְנֵי יִשְׂרָאֵל: regens + content rectum -> the regens counts; אִשְׁתּ + וֹ: suffix only -> does not
    heb = [tok(0, True, "g1"), tok(1, True, "g1"), tok(2, True, "g2"), tok(3, False, "g2")]
    assert nominal_possessor_idx(heb) == {0}
    grc = [tok(0, True, case_="nominative"), tok(1, False, case_="genitive"), tok(2, True, case_="genitive"),
           tok(3, True, case_="nominative"), tok(4, True, case_="genitive", lexeme="grc:0846")]
    assert nominal_possessor_idx(grc) == {0}


def test_load_disabled_risks_merges_language_and_edition(tmp_path):
    import json
    from lexeme_aligner.span_extension import load_disabled_risks
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"spa": {"disabled_risks": ["possession_affix"]}, "spa_x": {"disabled_risks": []}}), encoding="utf-8")
    assert load_disabled_risks("spa", p) == {"possession_affix"}
    assert load_disabled_risks("spa", p, tag="spa_x") == frozenset()
    assert load_disabled_risks("eng", p) == frozenset()
