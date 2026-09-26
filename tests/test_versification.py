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


# --- 2026-09-26: real exact tables for vul/rso + the catm->hebrew bug fix, from real bcv-commons/bibles
# data (config/bibles_vrs/) — see versification.py's own module docstring for the full comparison this
# was based on (catm==org exactly, org/orgw are subsets of our own hebrew.tsv, vul/rso are genuinely
# distinct from each other and from our own lxx.tsv).
def test_cdn_table_catm_maps_to_hebrew_not_septuagint():
    # regression test for a real bug found and fixed 2026-09-26: catm was wrongly bucketed with the
    # lxx/septuagint family, but catm's real data is byte-identical to org's, which is hebrew-family.
    assert vf._CDN_TABLE["catm"] == "hebrew"


def test_cdn_table_org_and_orgw_still_map_to_hebrew():
    assert vf._CDN_TABLE["org"] == "hebrew"
    assert vf._CDN_TABLE["orgw"] == "hebrew"


def test_cdn_table_vul_and_rso_have_their_own_distinct_tables():
    assert vf._CDN_TABLE["vul"] == "vul"
    assert vf._CDN_TABLE["rso"] == "rso"
    assert vf._CDN_TABLE["vul"] != vf._CDN_TABLE["rso"]


def test_cdn_table_lxx_keeps_its_own_existing_table_unchanged():
    assert vf._CDN_TABLE["lxx"] == "lxx"


def test_vul_reverse_table_loads_real_data():
    rev = vf.load_reverse("vul")
    assert len(rev) > 2000                              # real table, not empty/stub


def test_rso_reverse_table_loads_real_data():
    rev = vf.load_reverse("rso")
    assert len(rev) > 3000


def test_vul_and_rso_tables_are_not_identical():
    # a real, verified finding: vul (2845 rows) and rso (4132 rows) share only 2647 rows — genuinely
    # distinct schemes, not aliases of one another.
    vul = vf.load_reverse("vul")
    rso = vf.load_reverse("rso")
    shared = set(vul) & set(rso)
    disagree = sum(1 for k in shared if vul[k] != rso[k])
    assert disagree > 0                                  # real disagreements exist, not a coincidence


def test_rso_gives_a_genuinely_different_remap_than_the_old_lxx_fallback_for_some_verses():
    # before this fix, rso silently resolved through our own lxx.tsv (the "septuagint" table); now it
    # has its own real table. Confirm the two tables really do disagree on at least one real verse —
    # this is what makes wiring in the dedicated table meaningful rather than a no-op relabeling.
    old = vf.load_reverse("septuagint")
    new = vf.load_reverse("rso")
    shared = set(old) & set(new)
    disagreements = [k for k in shared if old[k] != new[k]]
    assert len(disagreements) > 50                       # real finding: 82 disagreements, not ~0


def test_remapper_for_scheme_rso_uses_the_new_dedicated_table():
    f = vf.remapper_for_scheme("rso")
    assert f is not None
    # a real disagreement verse found during verification: KJV PSA 10:8 differs between old/new tables
    old_f = vf.remapper_for_scheme("septuagint")
    assert f("PSA", 10, 8) != old_f("PSA", 10, 8)
