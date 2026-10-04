"""Offline tests for helfi_source.py's parsing (Step G0). No network needed — synthetic HELFI-shaped
lines, modeled on the real files (verified by hand against github.com/amikael/HELFI, see the module's
own docstring for the cross-checked Strong's numbers)."""
from lexeme_aligner.eval.helfi_source import (
    build_gold_and_text, build_usj_book, parse_alignment_file, parse_source_file,
    parse_source_morphemes,
)


def test_parse_source_file_extracts_strongs_and_normalizes():
    text = (
        "jd000:001\t1\tἸούδας/2455/2435\tNOM.MASC.SG\tἸούδας\tiūdas\n"
        "jd000:001\t4a\tδοῦλος /1401/-\tNOM.MASC.SG\tδοῦλος\tdūlos\n"
    )
    out = parse_source_file(text, "G")
    assert out[(1, 1, "1")] == "G2455"
    assert out[(1, 1, "4a")] == "G1401"


def test_parse_source_file_single_chapter_book_uses_chapter_000_as_chapter_1():
    text = "pm000:005\t2\tθεός/2316/2298\tDAT.MASC.SG\tΘεῷ\ttheo\n"
    out = parse_source_file(text, "G")
    assert (1, 5, "2") in out


def test_parse_source_file_drops_grammatical_letter_codes_and_compounds():
    text = (
        "ru001:001\t1a\t-/c/-\tCNJ\tוַ\twa\n"          # letter code (conjunction) — no Strong's
        "ru001:001\t10b\t-/1035+/0980\tPR\t...\t...\n"  # compound "+" id — not a plain numeric Strong's
    )
    out = parse_source_file(text, "H")
    assert out[(1, 1, "1a")] is None
    assert out[(1, 1, "10b")] is None


def test_parse_alignment_file_verse_header_resets_state_and_splits_verses():
    text = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t1\t@\t-\tSiihen \n"
        "ru001:002\tru001:002\tru001:002\tVERSE\tRuut 1:2 \n"
        "ru001:002\t1\t@\t-\tMiehen \n"
    )
    verses = parse_alignment_file(text)
    assert set(verses) == {(1, 1), (1, 2)}
    assert verses[(1, 1)][0] == ["Siihen "]
    assert verses[(1, 2)][0] == ["Miehen "]


def test_parse_alignment_file_skips_untranslated_and_qualifier_rows():
    text = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t(1a) (1b)\t-\tUNTRANSLATED\t\n"
        "ru001:001\t7a\t%case\tLOC\t\n"
        "ru001:001\t2\t@\t-\tSiihen \n"
    )
    toks, src_lists = parse_alignment_file(text)[(1, 1)]
    assert toks == ["Siihen "]                                       # only the real word row survives
    assert src_lists == [["2"]]


def test_parse_alignment_file_multiple_source_ids_on_one_row_and_stacked_target_words():
    text = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t2b\t@\t-\tSiihen \n"
        "ru001:001\t2b\t@\t-\taikaan \n"                              # SAME source id, two target words
        "ru001:001\t(4a) 4b\t@\t-\ttuomarit \n"                       # ONE target word, two source ids
    )
    toks, src_lists = parse_alignment_file(text)[(1, 1)]
    assert toks == ["Siihen ", "aikaan ", "tuomarit "]
    assert src_lists == [["2b"], ["2b"], ["4a", "4b"]]


def test_build_gold_and_text_shares_source_id_across_repeated_tag_no_collision():
    align = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t1\t@\t-\tA \n"
        "ru001:001\t2\t@\t-\tB\n"
    )
    source_lookup = {(1, 1, "1"): "H0001", (1, 1, "2"): "H0002"}
    rows, texts, stats = build_gold_and_text(align, source_lookup, "RUT", "HELFI")
    assert texts[(1, 1)] == "A  B"
    src_ids = [r["source_id"] for r in rows]
    assert len(set(src_ids)) == 2                                    # two DIFFERENT strongs, no collision
    strongs = {r["strong"]: r["target_id"] for r in rows}
    assert strongs["H0001"].endswith("001") and strongs["H0002"].endswith("002")


# --- roadmap F3 (2026-09-26): morpheme boundaries, kept instead of collapsed to Strong's-or-None ------
def test_parse_source_morphemes_keeps_grammatical_letter_codes_and_compounds():
    # the SAME two rows parse_source_file collapses to None — parse_source_morphemes keeps their detail.
    text = (
        "ru001:001\t1a\t-/c/-\tCNJ\tוַ\twa\n"
        "ru001:001\t10b\t-/1035+/0980\tPR\tבֵּ֧ית לֶ֣חֶם\tbêt_leḥem\n"
    )
    out = parse_source_morphemes(text)
    assert out[(1, 1, "1a")] == {"morph": "CNJ", "surface": "וַ", "translit": "wa"}
    assert out[(1, 1, "10b")] == {"morph": "PR", "surface": "בֵּ֧ית לֶ֣חֶם", "translit": "bêt_leḥem"}


def test_build_gold_and_text_morphemes_field_covers_all_cited_ids_lexical_or_not():
    # "tuomarit" cites BOTH 4a (the article prefix, no Strong's) and 4b (the lexical participle stem) —
    # the real ru001:001 shape. morphemes must include both, even though only 4b carries a gold row.
    align = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t(4a) 4b\t@\t-\ttuomarit \n"
    )
    source_lookup = {(1, 1, "4a"): None, (1, 1, "4b"): "H8199"}
    morph_lookup = {
        (1, 1, "4a"): {"morph": "ART", "surface": "הַ", "translit": "ha"},
        (1, 1, "4b"): {"morph": "MASC.PL.ACT.PCP.ABS", "surface": "שֹּׁפְטִ֔ים", "translit": "šofĕṭîm"},
    }
    rows, _texts, _stats = build_gold_and_text(align, source_lookup, "RUT", "HELFI", morph_lookup=morph_lookup)
    assert len(rows) == 1                                             # only 4b carries a Strong's -> one row
    morphemes = rows[0]["morphemes"]
    assert [m["token_id"] for m in morphemes] == ["4a", "4b"]
    assert morphemes[0]["morph"] == "ART"
    assert morphemes[1]["morph"] == "MASC.PL.ACT.PCP.ABS"


def test_build_gold_and_text_no_morphemes_field_when_morph_lookup_omitted():
    align = (
        "ru001:001\tru001:001\tru001:001\tVERSE\tRuut 1:1 \n"
        "ru001:001\t1\t@\t-\tA \n"
    )
    rows, _texts, _stats = build_gold_and_text(align, {(1, 1, "1"): "H0001"}, "RUT", "HELFI")
    assert "morphemes" not in rows[0]                                 # backward compatible, opt-in field


def test_build_gold_and_text_drops_verse_on_tokenization_mismatch():
    # "toivoa'" collapses to one clear_token while HELFI kept "toivoa" and "'" as two rows.
    align = (
        "ru001:012\tru001:012\tru001:012\tVERSE\tRuut 1:12 \n"
        "ru001:012\t1\t@\t-\ttoivoa\n"
        "ru001:012\t2\t@\t-\t'\n"
    )
    rows, texts, stats = build_gold_and_text(align, {}, "RUT", "HELFI")
    assert (1, 12) not in texts
    assert stats["verses_dropped_tokenization_mismatch"] == 1
    assert rows == []


def test_build_usj_book_produces_readable_structure():
    doc = build_usj_book("RUT", {(1, 1): "A B.", (1, 2): "C.", (2, 1): "D."})
    assert doc["type"] == "USJ"
    types_markers = [(it.get("type"), it.get("marker")) for it in doc["content"]]
    assert ("book", "id") in types_markers
    assert types_markers.count(("chapter", "c")) == 2                # two distinct chapters
    # round-trip through read_verses
    import json
    import tempfile
    from pathlib import Path
    from lexeme_aligner.usj_source import read_verses
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "08-RUT.json"
        fp.write_text(json.dumps(doc), encoding="utf-8")
        verses = read_verses(fp)
    assert verses[(1, 1)].strip() == "A B."
    assert verses[(2, 1)].strip() == "D."
