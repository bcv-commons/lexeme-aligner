"""split_format: the rebuild rule of docs/split-format.md."""
import unicodedata as ud

from lexeme_aligner import split_format as sf
from lexeme_aligner.usj_source import tokenize


def _p(part, slot, text, word, token=None, **kw):
    return {"part": part, "slot": slot, "text": text, "word": word, "token": token or tokenize(word)[0], **kw}


def test_prefix_and_stem_rebuild_the_written_word():
    w = "הַשָּׁלִיחַ"
    assert sf.check_token([_p(0, "prefix", "הַ", w), _p(1, "stem", "שָּׁלִיחַ", w)], tokenize) == []


def test_a_wrong_part_fails_the_rebuild():
    w = "titwamulolile"
    bad = sf.check_token([_p(0, "prefix", "ti", w), _p(1, "stem", "twamulolil", w)], tokenize)
    assert "parts do not rebuild the word" in bad


def test_tone_marks_go_back_on_the_counted_cluster():
    w = ud.normalize("NFC", "mulóle")                     # tone on the 3rd letter cluster from the end
    parts = [_p(0, "stem", "mulole", w), _p(1, "tone", "", w, tone=["3:́"], label="tone-proh")]
    assert sf.rebuild(parts) == w
    assert sf.check_token(parts, tokenize) == []
    assert "parts do not rebuild the word" in sf.check_token(
        [_p(0, "stem", "mulole", w), _p(1, "tone", "", w, tone=["2:́"])], tokenize)


def test_a_hyphenated_affix_is_its_own_token_with_a_host_and_whole_stands_alone():
    assert sf.check_token([_p(0, "suffix", "ka", "ka", written="hyphen", host=-1)], tokenize) == []
    assert "an affix written apart without its host" in sf.check_token([_p(0, "suffix", "ka", "ka", written="hyphen")], tokenize)
    assert "a whole row next to other rows" in sf.check_token(
        [_p(0, "whole", "ni", "nimu"), _p(1, "stem", "mu", "nimu")], tokenize)


def test_part_numbers_must_run_without_gaps():
    assert "part numbers not 0..n-1" in sf.check_token([_p(0, "prefix", "a", "ab"), _p(2, "stem", "b", "ab")], tokenize)
