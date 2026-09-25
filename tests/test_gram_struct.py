"""Offline tests for gram_struct.py — synthetic inputs via path parameters, no real config files."""
import json

import pytest

import lexeme_aligner.gram_struct as gs


def _w(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _build(tmp_path, *, features=None, directions=None, constituent=None, spanext=None,
           fertility=None, gold=None, conventions=None, isos):
    out = tmp_path / "out"
    cdir = tmp_path / "constituent_order"
    cdir.mkdir()
    for iso, doc in (constituent or {}).items():
        _w(cdir / f"{iso}.json", doc)
    conv = tmp_path / "conventions"
    conv.mkdir()
    for iso, text in (conventions or {}).items():
        (conv / f"{iso}.md").write_text(text, encoding="utf-8")
    cov = gs.build(
        out, isos=isos,
        features_file=_w(tmp_path / "features.json", {"languages": features or {}}),
        directions_file=_w(tmp_path / "directions.json", directions or {}),
        constituent_dir=cdir,
        spanext_file=_w(tmp_path / "spanext.json", spanext or {}),
        fertility_file=_w(tmp_path / "fertility.json", fertility or {}),
        gold_file=_w(tmp_path / "gold.json", gold or {}),
        conventions_dir=conv,
        manifest=tmp_path / "absent-manifest.json")
    return out, cov


def _read(out, *parts):
    return json.loads((out.joinpath(*parts)).read_text(encoding="utf-8"))


# --- partition routing by source ------------------------------------------------------------------------
def test_grambank_slot_goes_to_external_and_lang2vec_to_imputed(tmp_path):
    out, _ = _build(tmp_path, isos=["xx", "yy"],
                    features={"xx": {"GB074": "0", "GB075": "1"}},
                    directions={"xx": {"adposition": {"direction": "after", "source": "grambank", "confidence": 1.0}},
                                "yy": {"adposition": {"direction": "before", "source": "lang2vec", "confidence": 0.93}}})
    ext = _read(out, "external", "xx.json")
    assert ext["adposition"] == {"direction": "after", "source": "grambank", "codes": ["GB074", "GB075"],
                                 "confidence": 1.0}
    assert not (out / "imputed" / "xx.json").exists()
    imp = _read(out, "imputed", "yy.json")
    assert imp["adposition"] == {"direction": "before", "source": "lang2vec", "confidence": 0.93}
    assert not (out / "external" / "yy.json").exists()


def test_wals_slot_goes_to_external_with_wals_code(tmp_path):
    out, _ = _build(tmp_path, isos=["zz"],
                    directions={"zz": {"possessor": {"direction": "after", "source": "wals", "confidence": 0.95}}})
    ext = _read(out, "external", "zz.json")
    assert ext["possessor"]["source"] == "wals"
    assert ext["possessor"]["codes"] == ["WALS:86A"]


def test_grambank_wins_over_lang2vec_for_the_same_slot(tmp_path):
    out, _ = _build(tmp_path, isos=["xx"],
                    features={"xx": {"GB074": "1", "GB075": "0"}},
                    directions={"xx": {"adposition": {"direction": "after", "source": "lang2vec", "confidence": 0.9}}})
    assert _read(out, "external", "xx.json")["adposition"]["direction"] == "before"
    assert not (out / "imputed" / "xx.json").exists()


# --- null-vs-absent ------------------------------------------------------------------------------------
def test_null_direction_is_a_fact_with_a_source_when_codes_are_known(tmp_path):
    out, cov = _build(tmp_path, isos=["hi"], features={"hi": {"GB022": "0", "GB023": "0"}})
    art = _read(out, "external", "hi.json")["article"]
    assert art["direction"] is None and art["source"] == "grambank" and art["codes"] == ["GB022", "GB023"]
    assert cov["slots"]["article"]["null"] == 1


def test_unknown_codes_leave_the_slot_absent(tmp_path):
    out, cov = _build(tmp_path, isos=["hi"], features={"hi": {"GB022": "0"}})   # GB023 unknown
    ext = _read(out, "external", "hi.json") if (out / "external" / "hi.json").exists() else {}
    assert "article" not in ext
    assert cov["slots"]["article"]["null"] == 0


# --- existence polarity reuse (analyze_language.RISK_RULES) ---------------------------------------------
def test_existence_any_one_present_and_flagged_agree(tmp_path):
    out, cov = _build(tmp_path, isos=["xx"], features={"xx": {"GB070": "1", "GB072": "0"}})
    cm = _read(out, "external", "xx.json")["case_marking"]
    assert cm["polarity"] == "any_one" and cm["flagged"] is True and cm["present"] is True
    assert cm["matched"] == ["GB070"] and cm["known"] == ["GB070", "GB072"]
    assert cm["codes"] == ["GB070", "GB072", "GB074", "GB075"]        # FEATURES order, full list
    assert cov["existence"]["case_marking"]["present"] == 1


def test_existence_not_all_one_flags_incomplete_and_reads_present_false(tmp_path):
    out, _ = _build(tmp_path, isos=["xx"], features={"xx": {"GB089": "0", "GB090": "0"}})
    si = _read(out, "external", "xx.json")["subject_indexing"]
    assert si["polarity"] == "not_all_one" and si["flagged"] is True and si["present"] is False
    assert si["matched"] == ["GB089", "GB090"]


def test_existence_not_all_one_complete_is_present(tmp_path):
    out, _ = _build(tmp_path, isos=["xx"], features={"xx": {"GB089": "1", "GB090": "1"}})
    si = _read(out, "external", "xx.json")["subject_indexing"]
    assert si["flagged"] is False and si["present"] is True and si["matched"] == []


def test_existence_absent_when_no_code_known(tmp_path):
    out, cov = _build(tmp_path, isos=["xx"], features={"xx": {"GB074": "1"}})
    assert "subject_indexing" not in _read(out, "external", "xx.json")
    assert cov["existence"]["subject_indexing"] == {"present": 0, "absent": 0}


def test_existence_polarities_come_from_risk_rules():
    assert gs._EXISTENCE_POLARITY["subject_indexing"] == "not_all_one"
    assert gs._EXISTENCE_POLARITY["case_marking"] == "any_one"
    assert "agent_indexing" not in gs._EXISTENCE_POLARITY       # falls back to the documented default
    assert gs.existence_fact({"GB091": "1"}, "agent_indexing")["polarity"] == "any_one"


# --- derived / measured ----------------------------------------------------------------------------------
def test_derived_pins_constituent_order_with_sha(tmp_path):
    doc = {"tag": "xxed", "verses_measured": 12, "pair_order_kept": {"Pred>Subj": {"rate": 0.4}},
           "function_drift": {"Subj": {"mean_drift": 0.1, "n": 40}}}
    out, cov = _build(tmp_path, isos=["xx"], constituent={"xx": doc})
    der = _read(out, "derived", "xx.json")["constituent_order"]
    assert der["source"] == "derived" and len(der["content_sha256"]) == 64
    assert der["pair_order_kept"] == doc["pair_order_kept"] and der["verses_measured"] == 12
    assert cov["languages_written"]["derived"] == 1


def test_measured_records_verdicts_dated_with_gold_and_routes_notes(tmp_path):
    out, _ = _build(tmp_path, isos=["hi"],
                    spanext={"hi": {"relation_trigger": True, "_note": "primary note",
                                    "definite_trigger": False, "typology_fallback": False,
                                    "_definite_note": "n/a for hi"}},
                    fertility={"hi": {"enabled": True, "lambda": 2.0, "_note": "fert note"}},
                    gold={"hi": {"gold": "clear", "base_text": "IRVHin", "edition": "hiirv"}},
                    conventions={"hi": "notes"})
    m = _read(out, "measured", "hi.json")
    mech = m["mechanisms"]
    assert mech["spanext.relation_trigger"] == {"enabled": True, "source": "measured", "date": gs._MEASURED_DATE,
                                                "gold": "clear/IRVHin", "note": "primary note"}
    assert mech["spanext.definite_trigger"]["note"] == "n/a for hi"
    assert "note" not in mech["spanext.typology_fallback"]          # entry-wide note is not copied to every flag
    assert mech["fertility_priors"] == {"enabled": True, "lambda": 2.0, "source": "measured",
                                        "date": gs._MEASURED_DATE, "gold": "clear/IRVHin", "note": "fert note"}
    assert m["conventions_md"].endswith("hi.md")


def test_gold_without_base_text_is_bare_method(tmp_path):
    out, _ = _build(tmp_path, isos=["hu"], fertility={"hu": {"enabled": False}},
                    gold={"hu": {"gold": "gbt"}})
    assert _read(out, "measured", "hu.json")["mechanisms"]["fertility_priors"]["gold"] == "gbt"


# --- merge / empties / coverage --------------------------------------------------------------------------
def test_merge_conflict_is_an_error():
    with pytest.raises(ValueError):
        gs.merge_partitions("xx", {"external": {"adposition": {}}, "imputed": {"adposition": {}}})


def test_merged_view_is_the_union_of_partitions_plus_iso(tmp_path):
    out, cov = _build(tmp_path, isos=["xx"],
                      features={"xx": {"GB074": "0", "GB075": "1"}},
                      directions={"xx": {"subject_verb": {"direction": "before", "source": "lang2vec", "confidence": 0.9}}},
                      fertility={"xx": {"enabled": False}})
    merged = _read(out, "xx.json")
    assert merged["iso"] == "xx"
    assert merged["adposition"]["source"] == "grambank"
    assert merged["subject_verb"]["source"] == "lang2vec"
    assert merged["mechanisms"]["fertility_priors"]["enabled"] is False
    assert cov["languages_written"]["merged"] == 1


def test_no_empty_files_are_written(tmp_path):
    out, cov = _build(tmp_path, isos=["nothing"])
    assert not (out / "nothing.json").exists()
    assert not any((out / p / "nothing.json").exists() for p in gs.PARTITIONS)
    assert cov["languages_requested"] == 1 and cov["languages_written"] == {}
    assert (out / "README.md").exists() and (out / "_coverage.json").exists()


def test_coverage_counts_slots_by_source(tmp_path):
    _, cov = _build(tmp_path, isos=["a", "b", "c"],
                    features={"a": {"GB074": "1", "GB075": "0"}},
                    directions={"b": {"adposition": {"direction": "after", "source": "wals", "confidence": 0.9}},
                                "c": {"adposition": {"direction": "after", "source": "lang2vec", "confidence": 0.9}}})
    assert cov["slots"]["adposition"] == {"grambank": 1, "wals": 1, "lang2vec": 1, "null": 0}
    assert cov["grambank_direction_mismatch"] == 0
