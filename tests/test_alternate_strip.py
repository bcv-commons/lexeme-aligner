"""Per-span stripping of alternates (spans that repeat their own verse): usj_source.alternate_spans, the walker,
removed_spans and the raw-position remap must all agree."""
import json

from lexeme_aligner import usj_source as u

VERSE = "In the beginning God made the heavens and the earth (In the beginning God made out of nothing the heavens and the earth) and (so) it was."
RULES = {"strip_alternate_parens": True}


def _usj(tmp_path, pieces, tag="xxalt"):
    d = tmp_path / f"usj-{tag}"
    d.mkdir()
    content = [{"type": "chapter", "number": "1"}, {"type": "verse", "number": "1"}]
    content += [{"type": "para", "marker": "p", "content": [p]} for p in pieces]
    fp = d / "01-GEN.json"
    fp.write_text(json.dumps({"type": "USJ", "content": content}), encoding="utf-8")
    return fp


def test_repeating_span_is_found_and_short_one_kept():
    spans = u.alternate_spans(VERSE, RULES)
    assert len(spans) == 1
    s, e = spans[0]
    assert VERSE[s:e].startswith("(In the beginning God made out of nothing")


def test_off_by_default_and_per_kind():
    assert u.alternate_spans(VERSE, {}) == []
    assert u.alternate_spans(VERSE, {"strip_alternate_brackets": True}) == []       # the repeat is in parens


def test_span_that_does_not_repeat_is_kept():
    text = "Jesus went up the mountain (a high place near the lake) and prayed."
    assert u.alternate_spans(text, RULES) == []


def test_walker_strips_only_the_alternate(tmp_path):
    fp = _usj(tmp_path, [VERSE])
    out = u.read_verses(fp, rules=RULES)[(1, 1)]
    assert "out of nothing" not in out
    assert "(so)" in out or " so " in out or "so" in out.split()
    assert u.read_verses(fp, rules={})[(1, 1)] == VERSE


def test_repetition_is_judged_across_pieces_of_one_verse(tmp_path):
    fp = _usj(tmp_path, ["In the beginning God made the heavens and the earth.",
                         "(In the beginning God made out of nothing the heavens and the earth)"])
    assert "out of nothing" not in u.read_verses(fp, rules=RULES)[(1, 1)]


def test_removed_spans_and_remap_agree_with_the_clean_text(tmp_path):
    fp = _usj(tmp_path, [VERSE])
    raw = u.read_verse_ranges(fp, rules={})[(1, 1)]["text"]
    clean = u.read_verse_ranges(fp, rules=RULES)[(1, 1)]["text"]
    idx = u.remap_clean_to_raw(raw, clean, RULES)
    raw_toks, clean_toks = u.tokenize(raw), u.tokenize(clean)
    assert len(idx) == len(clean_toks) and -1 not in idx
    assert [raw_toks[i] for i in idx] == clean_toks


def test_combines_with_other_rules(tmp_path):
    fp = _usj(tmp_path, ["The word [note to the reader] came " + VERSE])
    rules = {"strip_brackets": True, "strip_alternate_parens": True}
    out = u.read_verses(fp, rules=rules)[(1, 1)]
    assert "note to the reader" not in out and "out of nothing" not in out
    raw = u.read_verse_ranges(fp, rules={})[(1, 1)]["text"]
    idx = u.remap_clean_to_raw(raw, out, rules)
    assert [u.tokenize(raw)[i] for i in idx] == u.tokenize(out)


def test_token_spans_match_tokenize_when_a_combining_mark_stands_alone():
    # a lone Sinhala virama (Mn, dropped by strip_marks) is not a token for tokenize(), so it must not be a span either
    text = "ශ්‍රී ් ලංකා ් ගම"
    assert len(u._token_spans(text)) == len(u.tokenize(text))
    spans = u._token_spans(text)
    assert [u.tokenize(text[s:e])[0] for s, e in spans] == u.tokenize(text)


def test_token_spans_keep_spacing_marks_and_ordinary_words():
    text = "दाऊद और ेश"            # Mc/Mn handling: every run tokenize() emits has a span
    assert len(u._token_spans(text)) == len(u.tokenize(text))


def test_token_spans_are_exactly_the_tokens_for_han_japanese_and_myanmar():
    for text in ("神の子イエス 神は愛です。", "太初有道，道与神同在。", "အာဗြဟံ နှင့် ဒါဝိဒ်", "abc 神の子 def"):
        spans = u._token_spans(text)
        toks = u.tokenize(text)
        assert len(spans) == len(toks), text
        assert [u.strip_marks(text[s:e]) for s, e in spans] == toks, text


def test_token_spans_survive_stray_marks_inside_an_unspaced_script():
    text = "神́の ් 子イエス"                       # marks that strip_marks drops, between and alone
    spans = u._token_spans(text)
    assert len(spans) == len(u.tokenize(text))
