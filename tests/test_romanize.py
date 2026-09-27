"""M4 (2026-09-27): gloss_align.romanize() — the uroman-backed target-side romanization for the
name-transliteration prior. Real uroman calls (it's a small, pure-Python, MIT-licensed package,
installed in this env) plus a fallback-path test for when it's absent."""
import lexeme_aligner.gloss_align as ga


def test_romanize_hindi_name():
    assert ga.romanize("रूत") == "ruut"


def test_romanize_bengali_name():
    assert ga.romanize("রূৎ") == "ruuta"


def test_romanize_latin_strips_diacritics():
    assert ga.romanize("Élimélek") == "Elimelek"


def test_romanize_is_cached():
    ga._ROMAN_CACHE.clear()
    ga.romanize("Booz")
    assert "Booz" in ga._ROMAN_CACHE
    # second call must hit the cache, not re-invoke uroman
    calls = []
    real = ga._UROMAN.romanize_string
    ga._UROMAN.romanize_string = lambda w: calls.append(w) or real(w)
    try:
        ga.romanize("Booz")
    finally:
        ga._UROMAN.romanize_string = real
    assert calls == []


def test_romanize_falls_back_to_identity_when_uroman_unavailable(monkeypatch):
    monkeypatch.setattr(ga, "_UROMAN", False)
    monkeypatch.setattr(ga, "_ROMAN_CACHE", {})
    assert ga.romanize("रूत") == "रूत"
