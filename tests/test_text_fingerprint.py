"""Text fingerprints (plan: internal-docs/compact-text-fingerprint-plan-2026-10-12.md section 2): the definition,
the extraction rules agreed with bcv-commons/bibles, and the published reference vector."""
import hashlib
import json
from pathlib import Path

import pytest

from lexeme_aligner import text_fingerprint as tf

REF_CHAPTER = "0848eefc352b072d02fe6c2730e363ece252c2d6242d94296e3b58156ef93d24"
REF_BOOK = "6e149a855b269724f0bc352d1367cad54608b130887e31145051ad28fc590873"
FRA_3JN = Path(__file__).resolve().parents[1] / "pipeline/work/ingest-cache/usj-fra_lsg/64-3JN.json"


def _usj(tmp_path, content, name="08-RUT.json"):
    fp = tmp_path / name
    fp.write_text(json.dumps({"type": "USJ", "content": content}), encoding="utf-8")
    return fp


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def test_definition_by_hand():
    verses = {(1, 1): "L’ancien, à Gaïus.", (1, 2): "Bien-aimé!", (2, 1): "Amen."}
    c1 = _sha("1:L ancien à Gaïus\n2:Bien aimé")
    c2 = _sha("1:Amen")
    book, chapters = tf.fingerprints(verses)
    assert chapters == {1: c1, 2: c2}
    assert book == _sha(f"1:{c1}\n2:{c2}")
    assert tf.file_hash(book) == book[:8]


def test_whitespace_punctuation_and_digits_do_not_count():
    a = tf.fingerprints({(1, 1): "In the beginning, God\n created 7 things."})
    b = tf.fingerprints({(1, 1): "In the  beginning God created things"})
    assert a == b


def test_one_verse_edit_changes_only_its_chapter_and_the_book():
    base = {(1, 1): "one two", (1, 2): "three", (2, 1): "four", (2, 2): "five"}
    edited = {**base, (2, 2): "five six"}
    b1, c1 = tf.fingerprints(base)
    b2, c2 = tf.fingerprints(edited)
    assert c1[1] == c2[1] and c1[2] != c2[2] and b1 != b2


def test_verse_numbers_count():
    a = tf.fingerprints({(1, 1): "alpha", (1, 2): "beta"})
    b = tf.fingerprints({(1, 2): "alpha", (1, 3): "beta"})                # same words, renumbered
    assert a[0] != b[0]


def test_verse_without_tokens_is_left_out():
    with_empty = tf.fingerprints({(1, 1): "alpha", (1, 2): "  12, 13. ", (1, 3): "beta"})
    without = tf.fingerprints({(1, 1): "alpha", (1, 3): "beta"})
    assert with_empty == without


def test_map_line_marks_gaps():
    book, chapters = tf.fingerprints({(1, 1): "a", (2, 1): "b", (4, 1): "c"})
    line = tf.map_line("RUT", book, "2026-10-12", chapters).split()
    assert line[:3] == ["RUT", book[:8], "2026-10-12"]
    assert line[3:5] == [chapters[1][:5], chapters[2][:5]] and line[5] == f"4:{chapters[4][:5]}"


def test_usj_bridge_duplicate_note_vp_rq_and_title(tmp_path):
    fp = _usj(tmp_path, [
        {"type": "chapter", "number": "1"},
        {"type": "para", "marker": "d", "content": ["A psalm title"]},             # unnumbered title: not verse text
        {"type": "para", "marker": "p", "content": [
            {"type": "verse", "number": "1"}, "First",
            {"type": "char", "marker": "vp", "content": ["1a"]},
            " verse", {"type": "note", "marker": "f", "content": ["a footnote"]}, ".",
            {"type": "verse", "number": "2-3"}, "Bridged text",
            {"type": "char", "marker": "rq", "content": ["Naṉ. 17:10-14"]}, ".",
            {"type": "verse", "number": "4"}, "Ending one.",
            {"type": "verse", "number": "4"}, "Ending two.",                          # same number twice: joined
        ]},
    ])
    verses = tf.usj_verses(fp)
    assert {k: tf.verse_line(k[1], t) for k, t in verses.items()} == {
        (1, 1): "1:First verse", (1, 2): "2:Bridged text", (1, 4): "4:Ending one Ending two"}


@pytest.mark.skipif(not FRA_3JN.exists(), reason="fra_lsg ingest cache not present")
def test_reference_vector_fra_lsg_3jn():
    book, chapters = tf.usj_fingerprints(FRA_3JN)
    assert chapters == {1: REF_CHAPTER} and book == REF_BOOK
    assert tf.file_hash(book) == "6e149a85"
