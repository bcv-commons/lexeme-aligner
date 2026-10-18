"""usj_source: canonical book formats besides USJ (2026-10-17, agreed with bcv-commons/bibles): a Sofria chapter kept as fetched, and
plain verses for a source with nothing structured. Verse text comes from bibles' extractor; every reader built on _walk_verses works."""
import json

from lexeme_aligner import usj_source as us
from lexeme_aligner import versification as vz
from lexeme_aligner import text_fingerprint as tf


def _verse(n, *content):
    return {"type": "wrapper", "subtype": "verses", "atts": {"number": n},
            "content": [{"type": "mark", "subtype": "verses_label", "atts": {"number": n}}, *content]}


def sofria_chapter(ch):
    """The shape DBT's text_json returns (heading graft, chapter wrapper, verse wrappers), with a bridge and a \\rq reference."""
    return {"schema": {"structure": "nested", "structure_version": "0.2.1"}, "metadata": {},
            "sequence": {"type": "main", "blocks": [
                {"type": "graft", "sequence": {"type": "heading", "blocks": [
                    {"type": "paragraph", "subtype": "usfm:s", "content": ["A heading"]}]}},
                {"type": "paragraph", "subtype": "usfm:p", "content": [
                    {"type": "wrapper", "subtype": "chapter", "content": [
                        {"type": "mark", "subtype": "chapter_label", "atts": {"number": str(ch)}},
                        _verse("1", "In the beginning was the Word. "),
                        _verse("2-3", "He was with God; all things were made by him, ",
                               {"type": "wrapper", "subtype": "usfm:rq", "content": ["(Gen 1:1)"]}),
                        _verse("4", "In him was life."),
                    ], "atts": {"number": str(ch)}}]}]}}


def test_a_sofria_book_reads_through_bibles_extractor(tmp_path):
    fp = tmp_path / "43-JHN.json"
    fp.write_text(json.dumps({"format": "sofria", "book": "JHN", "chapters": {"1": sofria_chapter(1)}}), encoding="utf-8")
    v = us.read_verse_ranges(fp, rules={})
    assert sorted(v) == [(1, 1), (1, 2), (1, 4)]
    assert v[(1, 2)] == {"text": "He was with God; all things were made by him,", "verse_end": 3}   # \rq left out, bridge kept
    assert "heading" not in us.read_verses(fp, rules={})[(1, 1)]
    assert us.verse_markers(fp) == ("JHN", [(1, 1, 1), (1, 2, 3), (1, 4, 4)])
    assert tf.usj_fingerprints(fp)[1][1]                                     # the fingerprint works on it


def test_a_plain_verse_book_is_read_with_bibles_plain_reader(tmp_path):
    fp = tmp_path / "08-RUT.json"
    doc = {"format": "verses", "book": "RUT", "chapters": {
        "1": [{"verse_start": 1, "verse_end": 1, "verse_text": "In the days | when the judges ruled,"},
              {"verse_start": 2, "verse_end": 3, "verse_text": "two  verses   in one"},
              {"verse_start": 4, "verse_end": 4, "verse_text": "no~break//line"},
              {"verse_start": 5, "verse_end": 5, "verse_text": "|iEli, Eli|r said |sc he"}]}}
    fp.write_text(json.dumps(doc), encoding="utf-8")
    v = us.read_verse_ranges(fp, rules={})
    assert v[(1, 1)]["text"] == "In the days | when the judges ruled,"     # a literal | before a space is text: nothing parses it
    assert v[(1, 2)] == {"text": "two verses in one", "verse_end": 3}
    assert v[(1, 4)]["text"] == "no break line"                              # `~` and a literal `//` read as spaces
    assert v[(1, 5)]["text"] == "Eli, Eli said he"                           # legacy codes |i |r |sc dropped where they read as codes
    assert us.verse_markers(fp) == ("RUT", [(1, 1, 1), (1, 2, 3), (1, 4, 4), (1, 5, 5)])


def test_the_usj_walker_follows_bibles_pipe_rules(tmp_path):
    usj = {"type": "USJ", "version": "3.0", "content": [
        {"type": "book", "marker": "id", "code": "MAT", "content": []}, {"type": "chapter", "marker": "c", "number": "1"},
        {"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": "1"}, "A ",
            {"type": "char", "marker": "w", "content": ["tambu|tambuu"]}, " and a Sanskrit | danda, ",
            "plus proskomma's | default=\"X\" reading."]}]}
    fp = tmp_path / "40-MAT.json"
    fp.write_text(json.dumps(usj), encoding="utf-8")
    assert us.read_verses(fp, rules={})[(1, 1)] == "A tambu and a Sanskrit | danda, plus proskomma's |X reading."


def test_structure_detection_reads_every_format(tmp_path):
    (tmp_path / "43-JHN.json").write_text(json.dumps({"format": "sofria", "book": "JHN", "chapters": {"1": sofria_chapter(1)}}))
    (tmp_path / "08-RUT.json").write_text(json.dumps({"format": "verses", "book": "RUT", "chapters": {
        "1": [{"verse_start": 1, "verse_end": 1, "verse_text": "x"}, {"verse_start": 22, "verse_end": 22, "verse_text": "y"}]}}))
    assert vz._usj_structure(str(tmp_path)) == {"JHN": {1: 4}, "RUT": {1: 22}}
    assert vz._usj_last_verses(str(tmp_path)) == {"JHN": {1: 4}}


def test_a_usj_file_is_still_usj():
    assert us.book_format({"type": "USJ", "content": []}) == "usj"
    assert us.book_format({"format": "sofria"}) == "sofria" and us.book_format({"format": "other"}) == "usj"
