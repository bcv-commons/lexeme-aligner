import json

import pytest

from lexeme_aligner import takedown as td


def _write(tmp_path, editions):
    p = tmp_path / "takedown.json"
    p.write_text(json.dumps({"editions": editions}), encoding="utf-8")
    return p


def test_missing_file_is_an_empty_record(tmp_path):
    assert td.load(tmp_path / "none.json") == {} and td.rend_scope("engbsb", tmp_path / "none.json") == "all"


def test_levels_map_to_scopes_and_tags_match_loosely(tmp_path):
    p = _write(tmp_path, {"ENG-WMV": {"withdraw": "rend"}, "bsb": {"withdraw": "rend_above_eflomal"}, "x_y": {"withdraw": "edition"}})
    assert td.rend_scope("eng_wmv", p) == "none"
    assert td.rend_scope("BSB", p) == "eflomal"
    assert td.rend_scope("other", p) == "all"                      # the default: nobody is listed unless named
    assert td.is_withdrawn("X-Y", p) and not td.is_withdrawn("bsb", p)
    assert td.withdrawn_slugs(p) == {"x_y"}


def test_an_unknown_level_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        td.load(_write(tmp_path, {"a": {"withdraw": "someday"}}))


def test_the_committed_file_loads():
    assert isinstance(td.load(), dict)


def test_rend_entry_follows_the_scope():
    from lexeme_aligner.compact_align import rend_entry
    ids: dict = {}
    args = (ids, "0430", ["God"], [0], frozenset())
    assert rend_entry("all", "g", *args) == "1"
    assert rend_entry("eflomal", "g", *args) == "0"                # withheld: another stage than eflomal produced it
    assert rend_entry("eflomal", "E", *args) == "1"                # same rendering, same id; the withheld entry took none
    assert rend_entry("eflomal", "e", ids, "0430", ["Elohim"], [0], frozenset()) == "2"
    assert rend_entry("none", "E", *args) is None


def test_editions_for_skips_a_withdrawn_edition(monkeypatch):
    import lexeme_aligner.onboard as ob
    monkeypatch.setattr(td, "withdrawn_slugs", lambda path=td.PATH: {"aaa_x"})      # only a full withdrawal removes an edition
    monkeypatch.setattr(ob, "_pool_editions", lambda iso, t, c=None: [{"edition_code": "x"}, {"edition_code": "y"}])
    monkeypatch.setattr(ob, "_tag", lambda iso, code, primary: f"{'aaa' if code == 'x' else 'bbb'}_{code}")
    assert [e["edition_code"] for e in ob.editions_for("zzz", {"ot"})] == ["y"]
