"""align-text text input: USFM, eBible corpus (vref.txt), plain `BOOK C:V text` lines -> our USJ book files."""
import pytest

from lexeme_aligner import text_input as ti
from lexeme_aligner.usj_source import read_verse_ranges, read_verses


def test_plain_text_lines_become_usj_books(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mine.txt").write_text("# a comment\nRUT 1:1 In the days\n\nRUT 1:2-3 two verses\nJON 1:1 Now the word\nTOB 1:1 skipped\n")
    usj, fmt = ti.prepare(src, tmp_path / "out")
    assert fmt == "plain" and sorted(p.name for p in usj.iterdir()) == ["08-RUT.json", "32-JON.json"]
    assert read_verses(usj / "08-RUT.json", rules={}) == {(1, 1): "In the days", (1, 2): "two verses"}
    assert read_verse_ranges(usj / "08-RUT.json", rules={})[(1, 2)]["verse_end"] == 3


def test_a_book_named_file_takes_chapter_verse_lines(tmp_path):
    (tmp_path / "08-RUT.txt").write_text("1:1 In the days\n1:2 A man\n")
    usj, _ = ti.prepare(tmp_path / "08-RUT.txt", tmp_path / "out")
    assert read_verses(usj / "08-RUT.json", rules={})[(1, 2)] == "A man"


def test_a_line_without_a_reference_is_refused(tmp_path):
    (tmp_path / "x.txt").write_text("In the days\n")
    with pytest.raises(SystemExit, match="line 1"):
        ti.prepare(tmp_path, tmp_path / "out")


def test_ebible_corpus_layout_with_ranges_and_gaps(tmp_path):
    (tmp_path / "vref.txt").write_text("RUT 1:1\nRUT 1:2\nRUT 1:3\nRUT 1:4\n")
    (tmp_path / "xyz-xyz.txt").write_text("one\ntwo and three\n<range>\n\n")
    usj, fmt = ti.prepare(tmp_path, tmp_path / "out")
    assert fmt == "ebible" and read_verses(usj / "08-RUT.json", rules={}) == {(1, 1): "one", (1, 2): "two and three"}
    assert read_verse_ranges(usj / "08-RUT.json", rules={})[(1, 2)]["verse_end"] == 3


def test_usfm_files_are_converted(tmp_path):
    pytest.importorskip("usfmtc")
    (tmp_path / "rut.usfm").write_text("\\id RUT test\n\\c 1\n\\p\n\\v 1 In the days\n\\v 2 A \\w man\\w* went\n")
    usj, fmt = ti.prepare(tmp_path, tmp_path / "out")
    assert fmt == "usfm" and read_verses(usj / "08-RUT.json", rules={}) == {(1, 1): "In the days", (1, 2): "A man went"}


def test_a_usj_folder_is_used_in_place(tmp_path):
    (tmp_path / "08-RUT.json").write_text("{}")
    assert ti.prepare(tmp_path, tmp_path / "out") == (tmp_path, "usj")
