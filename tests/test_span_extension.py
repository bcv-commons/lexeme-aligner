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
    def __init__(self, idx, lexeme):
        self.idx = idx
        self.lexeme = lexeme


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
    monkeypatch.setattr(se.HebrewSource, "__init__", lambda self: None)

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
    monkeypatch.setattr(se.HebrewSource, "__init__", lambda self: None)

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
    monkeypatch.setattr(se.HebrewSource, "__init__", lambda self: None)
    monkeypatch.setattr(se, "StopwordFilter", lambda *a, **k: type("S", (), {"is_function": lambda self, w: True})())

    by_book, stats = se.extend_spans("fakeiso", "fake", tmp_path, ["MAT"], out_dir=tmp_path)
    assert "extended_name" not in stats


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
