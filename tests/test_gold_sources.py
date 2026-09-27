"""E2 (2026-09-27): contest_rule's gold_langs.json lookups accept a `gold_method` to select a SECOND gold
source built against a different text (Door43 hi_glt beside Clear IRVHin for hin)."""
import lexeme_aligner.contest_rule as cr


def test_primary_entry_when_no_method(monkeypatch):
    monkeypatch.setattr(cr, "_cfg", {"hin": {"gold": "clear", "edition": "hinirv", "base_text": "IRVHin",
                                             "door43": {"edition": "higlt", "base_text": "hi_glt"}}})
    assert cr.gold_edition("hin") == "hinirv"
    assert cr.gold_base_text("hin") == "IRVHin"


def test_second_source_selected_by_method(monkeypatch):
    monkeypatch.setattr(cr, "_cfg", {"hin": {"gold": "clear", "edition": "hinirv", "base_text": "IRVHin",
                                             "door43": {"edition": "higlt", "base_text": "hi_glt"}}})
    assert cr.gold_edition("hin", gold_method="door43") == "higlt"
    assert cr.gold_base_text("hin", gold_method="door43") == "hi_glt"


def test_unknown_method_falls_back_to_primary(monkeypatch):
    monkeypatch.setattr(cr, "_cfg", {"hin": {"gold": "clear", "edition": "hinirv", "base_text": "IRVHin"}})
    assert cr.gold_edition("hin", gold_method="manual") == "hinirv"
    assert cr.gold_base_text("hin", gold_method="sword") == "IRVHin"


def test_gold_usj_dir_uses_second_source_edition(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "_cfg", {"guj": {"gold": "door43", "edition": "gujglt", "base_text": "gu_glt"}})
    (tmp_path / "usj-gujglt").mkdir()
    assert cr.gold_usj_dir("guj", ingest_cache=tmp_path, gold_method="door43") == tmp_path / "usj-gujglt"
    assert cr.gold_usj_dir("guj", ingest_cache=tmp_path) == tmp_path / "usj-gujglt"
