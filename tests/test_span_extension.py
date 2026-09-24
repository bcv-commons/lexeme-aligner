"""Offline tests for span_extension.py: direction_for()'s pure logic, and extend_spans()'s end-to-end
behavior against synthetic data (no real spine/Grambank/corpus needed).
"""
import json

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
    assert by_book["MAT"][0]["pairs"][0]["prior"] == "spanext_definite_before"


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
    assert "spanext_definite_before" in pairs["prior"] and "spanext_struct_after" in pairs["prior"]


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
    still get a direction from the typology table instead of being skipped outright."""
    write_align(tmp_path, "fakeiso", "eflomal", "MAT",
               [{"ref": 40001001, "book": "MAT", "chapter": 1, "verse": 1,
                 "pairs": [pair(0, "lx:noun", "H1", [3])]}])
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)   # NOT in Grambank at all
    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction", lambda iso, slot, path=ty._OUT: "before" if slot == "article" else None)
    monkeypatch.setattr(se, "analyze", lambda *a, **k: {"findings": [
        {"risk": "articles", "pos": "noun", "prompt_hint": "..."}]})
    monkeypatch.setattr(se, "build_corpus", lambda books, usj_dir, heb, remap=None: [
        _FakeVerseRec("MAT", 1, 1, ["a", "b", "X", "NOUN", "d"], [_FakeTok(0, "lx:noun")])])
    monkeypatch.setattr(se, "remapper", lambda iso, usj_dir: None)
    monkeypatch.setattr(se.HebrewSource, "__init__", _fake_heb_init())
    monkeypatch.setattr(se, "StopwordFilter",
                        lambda *a, **k: type("S", (), {"is_function": lambda self, w: w == "X"})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     typology_fallback=True)
    assert stats["extended_noun"] == 1
    assert by_book["MAT"][0]["pairs"][0]["t_idx"] == [2, 3]


def test_extend_spans_typology_fallback_off_by_default_skips_grambank_absent_language(tmp_path, monkeypatch):
    """typology_fallback defaults False — measured net-negative for 2 of 3 languages tested (ben/asm),
    so a Grambank-absent language must be skipped, not silently routed through typology, unless
    explicitly opted in."""
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({"lx:noun": "noun"}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)
    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction", lambda iso, slot, path=ty._OUT: "before")   # would resolve if asked
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert by_book == {}
    assert "skipped" in stats


def test_extend_spans_skips_when_neither_grambank_nor_typology_has_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "load_priors", lambda _pp: ({}, {}))
    monkeypatch.setattr(se, "load_grambank_raw", lambda _iso, path=None: None)
    import lexeme_aligner.typology as ty
    monkeypatch.setattr(ty, "direction", lambda iso, slot, path=ty._OUT: None)   # nothing anywhere
    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path,
                                     typology_fallback=True)
    assert by_book == {}
    assert "skipped" in stats
