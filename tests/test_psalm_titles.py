from pathlib import Path

import lexeme_aligner.psalm_titles as pt


def test_classify_ratio_bands():
    assert pt.classify_ratio(0.97) == "heading"
    assert pt.classify_ratio(1.04) == "heading"
    assert pt.classify_ratio(1.08) == "unknown"
    assert pt.classify_ratio(1.12) == "unknown"
    assert pt.classify_ratio(1.22) == "inline"
    assert pt.classify_ratio(None) == "unknown"


def test_a_numbered_scheme_is_decided_from_the_vrs_alone(monkeypatch, tmp_path):
    monkeypatch.setattr("lexeme_aligner.versification.edition_scheme", lambda tag, d: "rso")
    r = pt.title_mode("whatever", tmp_path)                     # no Psalms file needed: the scheme already says the title is a verse
    assert r["mode"] == "numbered" and r["ratio"] is None


def test_an_eng_scheme_edition_without_psalms_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr("lexeme_aligner.versification.edition_scheme", lambda tag, d: "eng")
    assert pt.title_mode("whatever", tmp_path)["mode"] == "unknown"
    assert "eng" not in pt.NUMBERED_SCHEMES and {"org", "orgw", "catm", "rso", "lxx", "vul"} <= pt.NUMBERED_SCHEMES
