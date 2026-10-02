"""onboard._drop_near_duplicates must never drop an edition that covers a testament its partner lacks."""
from lexeme_aligner import onboard as ob


def _seen(*keys):
    out = {}
    for k in keys:
        src, code = k.split(":")
        out[k] = {"source": src, "param": code, "edition_code": code}
    return out


def _kept(seen, classification, coverage=None):
    return sorted(f"{v['source']}:{v['edition_code']}" for v in ob._drop_near_duplicates(seen, classification, coverage))


def test_jav_case_keeps_the_edition_that_has_the_ot():
    seen = _seen("dbt:JAVLAI", "dbt:JAVNRF")
    cls = {"dbt:JAVLAI": [("near_identical", "dbt:JAVNRF")]}
    cov = {"dbt:JAVLAI": {"nt", "ot"}, "dbt:JAVNRF": {"nt"}}
    assert _kept(seen, cls, cov) == ["dbt:JAVLAI"]
    assert _kept(seen, cls) == ["dbt:JAVNRF"]               # without coverage: the old sorted-key tiebreak (the bug)


def test_the_result_does_not_depend_on_which_side_the_catalog_flags():
    seen = _seen("dbt:JAVLAI", "dbt:JAVNRF")
    cls = {"dbt:JAVNRF": [("near_identical", "dbt:JAVLAI")]}
    cov = {"dbt:JAVLAI": {"nt", "ot"}, "dbt:JAVNRF": {"nt"}}
    assert _kept(seen, cls, cov) == ["dbt:JAVLAI"]


def test_a_lower_priority_edition_with_more_coverage_beats_a_higher_priority_one():
    seen = _seen("pkf:XXXPKF", "helloao:xxx_new")
    cls = {"helloao:xxx_new": [("dialect_variant", "pkf:XXXPKF")]}
    cov = {"pkf:XXXPKF": {"nt"}, "helloao:xxx_new": {"nt", "ot"}}
    assert _kept(seen, cls, cov) == ["helloao:xxx_new"]


def test_when_each_covers_something_the_other_lacks_both_are_kept():
    seen = _seen("dbt:AAA", "dbt:BBB")
    cls = {"dbt:AAA": [("near_identical", "dbt:BBB")]}
    cov = {"dbt:AAA": {"nt"}, "dbt:BBB": {"ot"}}
    assert _kept(seen, cls, cov) == ["dbt:AAA", "dbt:BBB"]


def test_equal_coverage_keeps_the_old_priority_rule():
    seen = _seen("pkf:XXXPKF", "dbt:XXXDBT")
    cls = {"dbt:XXXDBT": [("near_identical", "pkf:XXXPKF")]}
    cov = {"pkf:XXXPKF": {"nt", "ot"}, "dbt:XXXDBT": {"nt", "ot"}}
    assert _kept(seen, cls, cov) == ["pkf:XXXPKF"]          # PKF outranks DBT, as before


def test_the_dominated_edition_is_still_dropped_when_it_adds_nothing():
    seen = _seen("pkf:XXXPKF", "dbt:XXXDBT")
    cls = {"dbt:XXXDBT": [("near_identical", "pkf:XXXPKF")]}
    cov = {"pkf:XXXPKF": {"nt", "ot"}, "dbt:XXXDBT": {"nt"}}
    assert _kept(seen, cls, cov) == ["pkf:XXXPKF"]


def _rec(source, code, same_text_as=None, fetchable=True, likely=None, closest=None):
    return {"source": source, "param": code, "edition_code": code, "fetchable": fetchable,
            "same_text_as": same_text_as, "likely": likely, "closest": closest}


def test_family_pick_prefers_the_member_that_covers_the_ot(monkeypatch, tmp_path):
    # bco: PKF BCOPKF (NT only) is the same text as helloAO bco_wbt, which ALSO has the OT. Source priority alone
    # picked PKF and the OT was never ingested.
    versions = {"nt": [_rec("helloao", "bco_wbt"), _rec("pkf", "BCOPKF", same_text_as="helloao:bco_wbt")],
                "ot": [_rec("helloao", "bco_wbt")]}
    monkeypatch.setattr(ob, "all_versions", lambda iso, testament: versions[testament])
    eds = ob.editions_for("bco", {"nt", "ot"}, tmp_path / "none.json")
    assert [e["edition_code"] for e in eds] == ["bco_wbt"]


def test_family_pick_keeps_the_source_priority_when_coverage_is_equal(monkeypatch, tmp_path):
    versions = {"nt": [_rec("helloao", "xx_a"), _rec("pkf", "XXPKF", same_text_as="helloao:xx_a")],
                "ot": [_rec("helloao", "xx_a"), _rec("pkf", "XXPKF", same_text_as="helloao:xx_a")]}
    monkeypatch.setattr(ob, "all_versions", lambda iso, testament: versions[testament])
    eds = ob.editions_for("xx", {"nt", "ot"}, tmp_path / "none.json")
    assert [e["edition_code"] for e in eds] == ["XXPKF"]            # PKF still wins when both cover NT+OT


def test_editions_for_jav_style_pair_keeps_the_ot_edition(monkeypatch, tmp_path):
    versions = {"nt": [_rec("dbt", "JAVLAI", likely="near_identical", closest="dbt:JAVNRF"), _rec("dbt", "JAVNRF")],
                "ot": [_rec("dbt", "JAVLAI")]}
    monkeypatch.setattr(ob, "all_versions", lambda iso, testament: versions[testament])
    eds = ob.editions_for("jav", {"nt", "ot"}, tmp_path / "none.json")
    assert [e["edition_code"] for e in eds] == ["JAVLAI"]
