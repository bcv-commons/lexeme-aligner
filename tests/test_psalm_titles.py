"""ALIGNER_PSALM_TITLES (experiment, off by default): an unnumbered \\d psalm title is read as verse 0 of its chapter."""
import json

from lexeme_aligner import usj_source


def _book(tmp_path, code, paras):
    doc = {"type": "USJ", "version": "3.0", "content": [{"type": "book", "marker": "id", "code": code, "content": []},
                                                         {"type": "chapter", "marker": "c", "number": "3"}, *paras]}
    p = tmp_path / f"19-{code}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _v(n, text):
    return [{"type": "verse", "marker": "v", "number": str(n)}, text]


TITLE = {"type": "para", "marker": "d", "content": ["A psalm of David."]}
BODY = {"type": "para", "marker": "q1", "content": [*_v(1, "O LORD, how many are my foes! "), *_v(2, "Many say of me")]}


def test_off_by_default_drops_title(tmp_path, monkeypatch):
    monkeypatch.setattr(usj_source, "PSALM_TITLES", False)
    v = usj_source.read_verses(_book(tmp_path, "PSA", [TITLE, BODY]))
    assert (3, 0) not in v and v[(3, 1)].startswith("O LORD")


def test_on_title_is_verse_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(usj_source, "PSALM_TITLES", True)
    p = _book(tmp_path, "PSA", [TITLE, BODY])
    v = usj_source.read_verses(p)
    assert v[(3, 0)] == "A psalm of David."
    monkeypatch.setattr(usj_source, "PSALM_TITLES", False)
    off = usj_source.read_verses(p)
    assert {k: x for k, x in v.items() if k != (3, 0)} == off      # every other verse unchanged


def test_on_numbered_title_and_title_after_verse_unchanged(tmp_path, monkeypatch):
    numbered = {"type": "para", "marker": "d", "content": [*_v(1, "A psalm of David.")]}
    late = {"type": "para", "marker": "d", "content": ["Selah heading"]}
    paras = [numbered, {"type": "para", "marker": "q1", "content": _v(2, "O LORD")}, late]
    monkeypatch.setattr(usj_source, "PSALM_TITLES", True)
    on = usj_source.read_verses(_book(tmp_path, "PSA", paras))
    monkeypatch.setattr(usj_source, "PSALM_TITLES", False)
    off = usj_source.read_verses(_book(tmp_path, "PSA", paras))
    assert on == off and (3, 0) not in on


def test_on_other_books_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(usj_source, "PSALM_TITLES", True)
    v = usj_source.read_verses(_book(tmp_path, "HAB", [TITLE, BODY]))
    assert (3, 0) not in v
