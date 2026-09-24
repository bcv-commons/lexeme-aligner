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
    def __init__(self, idx, lexeme, state=None, case_=None):
        self.idx = idx
        self.lexeme = lexeme
        self.state = state
        self.case_ = case_


def _fake_heb_init(has_state=False, has_case=False):
    """A HebrewSource.__init__ replacement carrying just the two flags extend_spans checks — has_state/
    has_case default False (no structured data), so tests written before the structured-signal gate keep
    exercising the coarse POS-only fallback path unchanged."""
    def _init(self):
        self.has_state = has_state
        self.has_case = has_case
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
