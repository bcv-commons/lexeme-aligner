"""Offline tests for edition_struct.py (roadmap E2) — synthetic inputs via path parameters, no real
config files or sibling DB needed."""
import json

import lexeme_aligner.edition_struct as es


def _w(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_build_tag_to_iso_inverts_compact_alignments_manifest(tmp_path):
    manifest = _w(tmp_path / "m.json", {"languages": {"hin": {"editions": {"hinirv": {}}},
                                                       "eng": {"editions": {"engbsb": {}, "eng_ylt": {}}}}})
    out = es.build_tag_to_iso(manifest)
    assert out == {"hinirv": "hin", "engbsb": "eng", "eng_ylt": "eng"}


def test_build_tag_to_iso_keys_are_lowercased_to_join_against_lowercase_pin_filenames(tmp_path):
    # regression: compact-alignments' own `editions` keys are UPPERCASE (AAAMLT), but
    # config/pins/<tag>.json filenames are lowercase (aaamlt.json) — a naive exact-string join
    # silently lost 797 of 1,726 real tags to this casing mismatch alone.
    manifest = _w(tmp_path / "m.json", {"languages": {"aaa": {"editions": {"AAAMLT": {}}}}})
    out = es.build_tag_to_iso(manifest)
    assert out == {"aaamlt": "aaa"}


def test_build_record_resolves_iso_case_insensitively():
    pin = {"provider": "p", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    rec = es.build_record("AAAMLT", pin, {"aaamlt": "aaa"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"


def test_load_helloao_by_version_id(tmp_path):
    fp = _w(tmp_path / "h.json", {"translations": [
        {"id": "AAB", "licenseUrl": "https://x", "sha256": "abc", "textDirection": "ltr"}]})
    out = es.load_helloao_by_version_id(fp)
    assert out["AAB"]["textDirection"] == "ltr"


def test_build_record_resolved_iso_and_helloao_join():
    pin = {"provider": "bible.helloao.org", "version_id": "HINIRV", "name": "IRV", "iso": "hin",
          "license_url": None, "sha256": "abc123", "books": 66, "language_name": "Hindi"}
    tag_to_iso = {"hinirv": "hin"}
    helloao = {"HINIRV": {"textDirection": "ltr", "licenseUrl": "https://real-license", "id": "HINIRV",
                          "sha256": "abc123"}}
    rec = es.build_record("hinirv", pin, tag_to_iso, helloao, {}, {}, {"hin": "Deva"})
    assert rec["tag"] == "hinirv"
    assert rec["iso"] == "hin"
    assert rec["iso_source"] == "pin"
    assert rec["pinned"]["license_url"] == "https://real-license"   # falls back to helloAO's own
    assert rec["pinned"]["helloao"] == {"text_direction": "ltr", "id": "HINIRV"}
    assert rec["derived"]["script"] == {"code": "Deva", "source": "languages_db"}
    assert rec["derived"]["text_strip"] == {"strip_brackets": False, "strip_parens_noise": False,
                                            "source": "default"}


def test_build_record_unresolved_iso_is_explicit_not_a_guess():
    pin = {"provider": "x", "version_id": "ZZZ", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}   # no "iso" key at all
    rec = es.build_record("zzztag", pin, {}, {}, {}, {}, {})
    assert rec["iso"] is None
    assert rec["iso_source"] == "unresolved"
    assert rec["derived"]["script"] is None


def test_build_record_prefers_pins_own_iso_over_compact_alignments():
    # the pin's own iso field covers pinned-but-never-pooled editions the compact-alignments reverse
    # index misses entirely (found while building this module: 459 real cases) — it must win, not
    # just be a fallback.
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaz", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("aazpkf", pin, {}, {}, {}, {}, {})
    assert rec["iso"] == "aaz"
    assert rec["iso_source"] == "pin"


def test_build_record_falls_back_to_compact_alignments_when_pin_has_no_iso():
    pin = {"provider": "p", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "xx"}, {}, {}, {}, {})
    assert rec["iso"] == "xx"
    assert rec["iso_source"] == "compact-alignments"


def test_build_record_flags_a_genuine_disagreement_between_pin_and_compact_alignments():
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaa", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "bbb"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"                      # the pin's own value still wins
    assert rec["iso_conflict"] == {"pin": "aaa", "compact_alignments": "bbb"}


def test_build_record_no_conflict_key_when_sources_agree_or_only_one_resolves():
    pin = {"provider": "p", "version_id": "V", "name": "n", "iso": "aaa", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("tag", pin, {"tag": "aaa"}, {}, {}, {}, {})
    assert "iso_conflict" not in rec


# --- _looks_like_a_real_iso / the pin-iso-is-a-tag-copy placeholder bug -----------------------------------
def test_looks_like_a_real_iso_accepts_three_letter_codes():
    assert es._looks_like_a_real_iso("aaz") is True
    assert es._looks_like_a_real_iso("hin") is True


def test_looks_like_a_real_iso_rejects_a_tag_shaped_value():
    assert es._looks_like_a_real_iso("aaamlt") is False    # 6 chars — a tag, not an iso
    assert es._looks_like_a_real_iso("abt_wos") is False   # underscore — definitely not an iso
    assert es._looks_like_a_real_iso(None) is False
    assert es._looks_like_a_real_iso("") is False


def test_build_record_ignores_a_pin_iso_that_is_just_a_copy_of_the_tag(tmp_path):
    # regression: config/pins/aaamlt.json's real "iso" field is the literal string "aaamlt" (a
    # placeholder, not a real code) — build_record must fall through to compact-alignments instead
    # of trusting it, exactly the bug found and fixed while building this module.
    pin = {"provider": "p", "version_id": "AAAMLT", "name": "n", "iso": "aaamlt",
          "license_url": None, "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("aaamlt", pin, {"aaamlt": "aaa"}, {}, {}, {}, {})
    assert rec["iso"] == "aaa"
    assert rec["iso_source"] == "compact-alignments"
    assert "iso_conflict" not in rec   # the bogus pin value never even enters the comparison


def test_build_record_trusts_a_genuine_three_letter_pin_iso_even_when_it_equals_the_tag(tmp_path):
    # the 10 legitimate single-edition "primary tag == bare iso" cases must NOT be caught by the
    # tag-copy guard just because pin_iso == tag — only the length/alpha shape matters.
    pin = {"provider": "p", "version_id": "HIN", "name": "n", "iso": "hin", "license_url": None,
          "sha256": "s", "books": 1, "language_name": "n"}
    rec = es.build_record("hin", pin, {}, {}, {}, {}, {})
    assert rec["iso"] == "hin"
    assert rec["iso_source"] == "pin"


def test_build_record_carries_own_license_url_over_helloao_when_present():
    pin = {"provider": "x", "version_id": "AAB", "name": "n", "license_url": "https://own",
          "sha256": "s", "books": 1, "language_name": "n"}
    helloao = {"AAB": {"licenseUrl": "https://helloao-fallback", "textDirection": "ltr", "id": "AAB",
                       "sha256": "x"}}
    rec = es.build_record("tag", pin, {}, helloao, {}, {}, {})
    assert rec["pinned"]["license_url"] == "https://own"


def test_build_record_textual_basis_and_text_strip_from_their_own_edition_entries():
    pin = {"provider": "x", "version_id": "V", "name": "n", "license_url": None, "sha256": "s",
          "books": 1, "language_name": "n"}
    tb = {"tag": {"verdict": "tr", "checked": 15, "present": 10, "bracketed": 5}}
    ts = {"tag": {"strip_brackets": True, "strip_parens_noise": True, "reason": "editorial notes"}}
    rec = es.build_record("tag", pin, {}, {}, tb, ts, {})
    assert rec["derived"]["textual_basis"] == {"verdict": "tr", "checked": 15, "present": 10,
                                               "bracketed": 5, "source": "diagnostic-verses"}
    assert rec["derived"]["text_strip"] == {"strip_brackets": True, "strip_parens_noise": True,
                                            "reason": "editorial notes", "source": "manual"}


def test_build_writes_one_file_per_pin_plus_coverage(tmp_path):
    pins = tmp_path / "pins"
    pins.mkdir()
    _w(pins / "aaa.json", {"provider": "p", "version_id": "AAA", "name": "n", "license_url": None,
                           "sha256": "s", "books": 1, "language_name": "n"})
    _w(pins / "bbb.json", {"provider": "p", "version_id": "BBB", "name": "n", "license_url": None,
                           "sha256": "s", "books": 1, "language_name": "n"})
    out = tmp_path / "out"
    manifest = _w(tmp_path / "m.json", {"languages": {"xx": {"editions": {"aaa": {}}}}})
    cov = es.build(pins_dir=pins, out_dir=out, manifest_path=manifest,
                   helloao_file=tmp_path / "absent-helloao.json",
                   textual_basis_file=tmp_path / "absent-tb.json",
                   text_strip_file=tmp_path / "absent-ts.json",
                   languages_db=tmp_path / "absent.db")
    assert cov["editions"] == 2
    assert cov["resolved_iso"] == 1
    assert cov["unresolved_iso"] == 1
    assert json.loads((out / "aaa.json").read_text())["iso"] == "xx"
    assert json.loads((out / "bbb.json").read_text())["iso"] is None
    assert (out / "_coverage.json").exists()


def test_build_degrades_gracefully_when_sibling_db_absent(tmp_path):
    assert es.load_scripts(tmp_path / "nope.db") == {}
