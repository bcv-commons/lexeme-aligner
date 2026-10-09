"""helloao_source: footnotes helloAO glued into the verse text are removed using the edition's eBible USFM (config/inline_notes.json)."""
import zipfile

from lexeme_aligner import helloao_source as h


def _zip(tmp_path, text):
    zp = tmp_path / "x_usfm.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("08-RUTx.usfm", text)
    return zp


def test_notes_are_read_per_verse_even_when_they_run_over_several_lines(tmp_path):
    usfm = ("\\id RUT\n\\c 2\n\\p\n\\v 9 और \\it उन्हीं के पीछे चला करना\\f + \\fr 2:9 \\fq उन्हीं के पीछे चला करना: \\ft उनके खेतों\n"
            "\\fp \\ft के बाढ़े नहीं थे। \\f*\\it*। क्या मैंने\n\\v 10 तब वह गिरी।\n")
    notes = h.ebible_notes(_zip(tmp_path, usfm))
    assert notes == {("RUT", 2, 9): ["2:9 उन्हीं के पीछे चला करना: उनके खेतों के बाढ़े नहीं थे।"]}


def test_strip_notes_removes_the_glued_note_and_nothing_else():
    text = "और उन्हीं के पीछे चला करना2:9 उन्हीं के पीछे चला करना: उनके खेतों के बाढ़े नहीं थे।। क्या मैंने"
    out, n = h.strip_notes(text, ["2:9 उन्हीं के पीछे चला करना: उनके खेतों के बाढ़े नहीं थे।"])
    assert n == 1 and out == "और उन्हीं के पीछे चला करना । क्या मैंने"
    assert h.strip_notes("तब वह गिरी।", ["1:1 कोई और"]) == ("तब वह गिरी।", 0)      # a note helloAO kept as noteId: not in the text


def test_the_book_writer_applies_the_notes_and_counts_them():
    book = {"id": "RUT", "chapters": [{"chapter": {"number": 2, "content": [
        {"type": "verse", "number": 9, "content": ["चला करना2:9 चला करना: नोट।। क्या"]}]}}]}
    stats: dict = {}
    usfm = h._book_usfm(book, {("RUT", 2, 9): ["2:9 चला करना: नोट।"]}, stats)
    assert "\\v 9 चला करना । क्या" in usfm and stats == {"notes": 1, "removed": 1}
