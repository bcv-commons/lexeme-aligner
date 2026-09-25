"""Offline tests for versification.py — synthetic USJ/.vrs data, no real vendored files.

Written for roadmap item E1 (internal-docs/aim1-typology-source-structure-plan.md §4R): the
declared-vs-detected CDN versification diff. Finding: `scheme_of()` already prefers structure-based
auto-detection (`detect_scheme`) over the manual `config/versification.json` fallback whenever a
usj_dir is given — true at every real call site in this codebase (run_pilot, gapfill, span_extension,
compact_align, derive_typology, pos_score, ...) — so a wrong/missing manual entry is a latent risk
only for a language with no ingested text to fingerprint. Verified against the 22 editions the CDN
declares non-eng that we align: 21/22 auto-detect to the exact declared scheme (hun/HUNHUN, the
flagged critical Psalm-superscription case, at 100% confidence); the 22nd (ind/INDASV) has an empty
ingest-cache directory (0 files) — an onboarding gap, not a versification bug. No `config/
versification.json` edit was warranted by this diff: every entry it could add for these 22 would be
dead code, shadowed by auto-detection.
"""
import json

import lexeme_aligner.versification as vf


def _usj(book, verses_per_chapter):
    """A minimal single-book USJ doc: {chapter: n_verses}."""
    content = [{"type": "book", "marker": "id", "code": book, "content": []}]
    for ch, n in verses_per_chapter.items():
        content.append({"type": "chapter", "marker": "c", "number": str(ch)})
        content.append({"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": str(v)} for v in range(1, n + 1)]})
    return {"type": "USJ", "version": "3.0", "content": content}


def _write_usj(tmp_path, book, verses_per_chapter):
    fp = tmp_path / f"{book}.json"
    fp.write_text(json.dumps(_usj(book, verses_per_chapter)), encoding="utf-8")
    return tmp_path


def test_usj_structure_reads_last_verse_per_chapter(tmp_path):
    _write_usj(tmp_path, "GEN", {1: 31, 2: 25})
    struct = vf._usj_structure(str(tmp_path))
    assert struct["GEN"] == {1: 31, 2: 25}


def test_usj_structure_handles_bridged_verse_numbers(tmp_path):
    # a "3-4" bridged verse marker — takes the first number, matching real USJ ingestion
    fp = tmp_path / "GEN.json"
    doc = {"type": "USJ", "content": [
        {"type": "book", "marker": "id", "code": "GEN", "content": []},
        {"type": "chapter", "marker": "c", "number": "1"},
        {"type": "para", "marker": "p", "content": [
            {"type": "verse", "marker": "v", "number": "1"},
            {"type": "verse", "marker": "v", "number": "3-4"}]}]}
    fp.write_text(json.dumps(doc), encoding="utf-8")
    struct = vf._usj_structure(str(tmp_path))
    assert struct["GEN"][1] == 3


def test_detect_scheme_picks_the_best_matching_cdn_scheme(tmp_path, monkeypatch):
    # PSA 51 with a 2-verse superscription (Hebrew/org numbering: 21 verses vs KJV/eng's 19)
    usj_dir = _write_usj(tmp_path, "PSA", {51: 21})
    monkeypatch.setattr(vf, "_load_vrs", lambda name: {
        "eng": {"PSA": {51: 19}}, "org": {"PSA": {51: 21}}, "orgw": {"PSA": {51: 20}},
    }.get(name, {}))
    vf._DETECT_CACHE.clear()
    label, best_cdn, scores = vf.detect_scheme(str(usj_dir))
    assert best_cdn == "org"
    assert label == "hebrew"
    assert scores["org"] == 1.0
    assert scores["eng"] < 1.0


def test_detect_scheme_empty_usj_returns_none(tmp_path):
    vf._DETECT_CACHE.clear()
    assert vf.detect_scheme(str(tmp_path)) == (None, None, {})


def test_scheme_of_prefers_autodetect_over_manual_file(tmp_path, monkeypatch):
    """The core E1 finding: a usj_dir that auto-detects cleanly wins over config/versification.json,
    even when the manual file disagrees or has no entry at all."""
    usj_dir = _write_usj(tmp_path, "PSA", {51: 21})
    monkeypatch.setattr(vf, "_load_vrs", lambda name: {
        "eng": {"PSA": {51: 19}}, "org": {"PSA": {51: 21}},
    }.get(name, {}))
    vf._DETECT_CACHE.clear()
    # manual file says protestant (or is silent) for this iso — auto-detect must still win
    versif_fp = tmp_path.parent / "versification_manual.json"
    versif_fp.write_text(json.dumps({"hunhun": "protestant"}), encoding="utf-8")
    monkeypatch.setattr(vf, "_VERSIF", versif_fp)
    assert vf.scheme_of("hunhun", str(usj_dir)) == "hebrew"


def test_scheme_of_falls_back_to_manual_file_when_no_usj(tmp_path, monkeypatch):
    versif_fp = tmp_path / "versification_manual.json"
    versif_fp.write_text(json.dumps({"rus": "septuagint"}), encoding="utf-8")
    monkeypatch.setattr(vf, "_VERSIF", versif_fp)
    assert vf.scheme_of("rus", None) == "septuagint"
    assert vf.scheme_of("rus", "/no/such/dir") == "septuagint"


def test_scheme_of_defaults_to_protestant_with_no_signal_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(vf, "_VERSIF", tmp_path / "nope.json")
    assert vf.scheme_of("zzz", None) == "protestant"


def test_remapper_maps_kjv_ref_to_hebrew_superscription_offset():
    f = vf.remapper_for_scheme("hebrew")
    # PSA 51's real hebrew.tsv table: KJV v1 ("Have mercy...") -> Hebrew v3 (past the 2-line title)
    assert f("PSA", 51, 1) == ("PSA", 51, 3)


def test_remapper_is_identity_for_protestant():
    assert vf.remapper_for_scheme("protestant") is None
