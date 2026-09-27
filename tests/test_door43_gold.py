"""E2 (2026-09-27): Door43 human alignment -> Clear-shaped gold parquet rows. Offline; synthetic spans
in the shape door43_align.usj_verses_for_book produces."""
import lexeme_aligner.door43_gold as dg


def test_word_positions_to_clear_maps_words_onto_clear_tokens():
    text = "पौलुस, परमेश्वर का दास और यीशु"
    words = ["पौलुस", "परमेश्वर", "का", "दास", "और", "यीशु"]
    idx = dg.word_positions_to_clear(words, text)
    toks = dg.clear_tokens(text)
    # every word placed, in increasing token order, and each placed token contains the word's letters
    assert None not in idx
    assert idx == sorted(idx)
    for w, i in zip(words, idx):
        assert dg._letters(w) in dg._letters(toks[i])


def test_word_positions_to_clear_none_for_unplaceable_word():
    idx = dg.word_positions_to_clear(["xyz"], "abc def")
    assert idx == [None]


def test_rows_for_book_shapes_clear_style_rows(monkeypatch):
    monkeypatch.setitem(dg.da.LANGUAGES, "zz", {"org": "o", "repo": "zz_glt", "tag": "zzglt"})
    spans = {(1, 1): [
        {"strong": "G39720", "lemma": "Παῦλος", "seq": 1, "target_words": ["पौलुस"], "target_positions": [0]},
        {"strong": "G23160", "lemma": "θεός", "seq": 2, "target_words": ["परमेश्वर", "का"], "target_positions": [1, 2]},
        {"strong": "G35880", "lemma": "ὁ", "seq": 3, "target_words": [], "target_positions": []},   # empty article
        {"strong": "c:H1961", "lemma": "הָיָה", "seq": 4, "target_words": ["x"], "target_positions": [3]},  # Hebrew-coded
    ]}
    texts = {(1, 1): "पौलुस, परमेश्वर का दास"}
    words = {(1, 1): ["पौलुस", "परमेश्वर", "का", "दास"]}
    rows, st = dg.rows_for_book("zz", "TIT", spans, texts, words)
    assert st["rows"] == 3 and st["zero_target"] == 1 and st["non_greek_or_null_strong"] == 1
    r0 = rows[0]
    assert r0["strong"] == "G3972" and r0["method"] == "door43" and r0["base_text"] == "zz_glt"
    assert r0["ref"] == "56001001" and r0["target_id"] == "56001001001" and r0["source_id"] == "n56001001001"
    # θεός -> two rows, same source_id, target ids 3 and 4 (clear tokens: पौलुस , परमेश्वर का दास → the comma is token 2)
    theos = [r for r in rows if r["strong"] == "G2316"]
    assert len(theos) == 2 and {r["source_id"] for r in theos} == {"n56001001002"}
    assert sorted(r["target_id"] for r in theos) == ["56001001003", "56001001004"]
    assert [r["surface"] for r in theos] == ["परमेश्वर", "का"]
