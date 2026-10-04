"""Offline tests for door43_align.py (roadmap F4) — real minimal USFM3 \\zaln snippets run through
the REAL usfmtc parser (no network calls; usfmtc itself is already a dependency of this repo, used by
cdn_source.py/dbt_source.py/helloao_source.py). Fixtures mirror the real shape confirmed against
ar_arst's live data (git.door43.org/BSOJ/ar_arst)."""
import lexeme_aligner.eval.door43_align as da

_HEADER = "\\id TIT\n\\usfm 3.0\n"


def _spans(body: str) -> dict:
    return da.usj_spans_for_book(da.usj_from_usfm(_HEADER + body))


def test_simple_span():
    body = ('\\c 1\n\\v 1 '
           r'\zaln-s |x-strong="G23160" x-lemma="θεός" x-morph="Gr,N,,,,,GMS," x-occurrence="1" '
           r'x-occurrences="2" x-content="Θεοῦ"\*\w لله|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*'
           '\n')
    spans = _spans(body)[(1, 1)]
    assert len(spans) == 1
    assert spans[0]["strong"] == "G23160"
    assert spans[0]["lemma"] == "θεός"
    assert spans[0]["target_words"] == ["لله"]


def test_back_to_back_spans_with_no_target_words():
    # the real TIT 1:5 case: a zaln-s immediately followed by another zaln-s, no zaln-e between them —
    # a stack, so the spans CLOSE in LIFO order: the inner (later-opened) span closes first and claims
    # the word, the outer span closes second with zero target words. Verified against real live data:
    # G11610 (δέ) got "ورسولٌ", G06520 (ἀπόστολος) got [].
    body = ('\\c 1\n\\v 1 '
           r'\zaln-s |x-strong="G06520" x-lemma="ἀπόστολος"\*'
           r'\zaln-s |x-strong="G11610" x-lemma="δέ"\*\w ورسولٌ|x-occurrence="1" x-occurrences="1"\w*'
           r'\zaln-e\*\zaln-e\*' '\n')
    spans = _spans(body)[(1, 1)]
    assert len(spans) == 2
    de, apostolos = spans                          # closes in LIFO order, not open order
    assert de["strong"] == "G11610"
    assert de["target_words"] == ["ورسولٌ"]
    assert apostolos["strong"] == "G06520"
    assert apostolos["target_words"] == []


def test_multiple_target_words_one_span():
    # a "simplified text" style expansion: one source word, several target words in its span.
    body = ('\\c 1\n\\v 1 '
           r'\zaln-s |x-strong="G39720" x-lemma="Παῦλος"\*'
           r'\w أنا|x-occurrence="1" x-occurrences="3"\w* '
           r'\w بولس|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*' '\n')
    spans = _spans(body)[(1, 1)]
    assert len(spans) == 1
    assert spans[0]["target_words"] == ["أنا", "بولس"]


def test_no_spans_at_all():
    body = "\\c 1\n\\v 1 plain text with no alignment markup\n"
    assert _spans(body) == {}


def test_words_outside_any_span_are_ignored():
    body = ('\\c 1\n\\v 1 '
           r'\w orphan|x-occurrence="1" x-occurrences="1"\w*\zaln-s |x-strong="G1"\*\zaln-e\*' '\n')
    spans = _spans(body)[(1, 1)]
    assert len(spans) == 1
    assert spans[0]["target_words"] == []          # "orphan" attached to nothing, correctly dropped


def test_splits_on_chapter_and_verse_markers():
    body = (
        '\\c 1\n'
        r'\v 1 \zaln-s |x-strong="G1" x-lemma="a"\*\w x|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*'
        '\n'
        r'\v 2 \zaln-s |x-strong="G2" x-lemma="b"\*\w y|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*'
        '\n')
    verses = _spans(body)
    assert set(verses.keys()) == {(1, 1), (1, 2)}
    assert verses[(1, 1)][0]["strong"] == "G1"
    assert verses[(1, 2)][0]["strong"] == "G2"


def test_tracks_chapter_across_multiple_chapters():
    body = ('\\c 1\n' r'\v 1 \zaln-s |x-strong="G1"\*\zaln-e\*' '\n'
           '\\c 2\n' r'\v 1 \zaln-s |x-strong="G2"\*\zaln-e\*' '\n')
    verses = _spans(body)
    assert set(verses.keys()) == {(1, 1), (2, 1)}


def test_book_files_covers_all_66_standard_books():
    assert len(da._BOOK_FILES) == 66
    assert da._BOOK_FILES["RUT"] == "08-RUT"
    assert da._BOOK_FILES["REV"] == "67-REV"


def test_usj_verses_for_book_gives_positions_text_and_word_list():
    # E2 (2026-09-27): every \w gets a verse-local position (aligned or not); punctuation attaches to the
    # preceding word in the clean text; `words` lists every \w in order.
    body = ('\\c 1\n\\v 1 '
           r'\zaln-s |x-strong="G39720"\*\w Pablo|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*, '
           r'\zaln-s |x-strong="G23160"\*\w de|x-occurrence="1" x-occurrences="1"\w* '
           r'\w Dios|x-occurrence="1" x-occurrences="1"\w*\zaln-e\* \w siervo|x-occurrence="1" x-occurrences="1"\w*'
           '\n')
    spans, texts, words = da.usj_verses_for_book(da.usj_from_usfm(_HEADER + body))
    assert words[(1, 1)] == ["Pablo", "de", "Dios", "siervo"]          # 'siervo' is outside any span
    assert texts[(1, 1)] == "Pablo, de Dios siervo"
    by_strong = {s["strong"]: s for s in spans[(1, 1)]}
    assert by_strong["G39720"]["target_positions"] == [0]
    assert by_strong["G23160"]["target_positions"] == [1, 2]
    assert by_strong["G23160"]["target_words"] == ["de", "Dios"]


def test_zero_width_joiners_are_stripped_from_words_and_text():
    # Nepali ne_glt writes ZWJ inside words; both the word list and the clean text must drop it so the
    # gold's clear-token positions and our tokenizer index one and the same string (E2, 2026-09-27).
    body = ('\\c 1\n\\v 1 '
           r'\zaln-s |x-strong="G23160"\*\w परमेश्' + "‍" + r'वरको|x-occurrence="1" x-occurrences="1"\w*\zaln-e\*'
           '\n')
    spans, texts, words = da.usj_verses_for_book(da.usj_from_usfm(_HEADER + body))
    assert words[(1, 1)] == ["परमेश्वरको"]
    assert "‍" not in texts[(1, 1)]
    assert spans[(1, 1)][0]["target_words"] == ["परमेश्वरको"]
