"""R10 (2026-09-28): resolve_eflomal_stem() — the --eflomal-stem None-means-consult resolution."""
import lexeme_aligner.run_pilot as rp


def test_explicit_true_always_wins_no_lookup(monkeypatch):
    import lexeme_aligner.target_morph as tm

    def _boom(*a, **k):
        raise AssertionError("should_stem must not be called when explicit is given")
    monkeypatch.setattr(tm, "should_stem", _boom)
    use_stem, ratio = rp.resolve_eflomal_stem(True, "eng", "/fake")
    assert use_stem is True and ratio is None


def test_explicit_false_always_wins_no_lookup():
    use_stem, ratio = rp.resolve_eflomal_stem(False, "eng", "/fake")
    assert use_stem is False and ratio is None


def test_none_defers_to_target_morph_should_stem(monkeypatch):
    import lexeme_aligner.target_morph as tm
    monkeypatch.setattr(tm, "should_stem", lambda iso, usj_dir=None, **k: (True, 2.5))
    use_stem, ratio = rp.resolve_eflomal_stem(None, "guj", "/some/usj")
    assert use_stem is True and ratio == 2.5


def test_none_with_unavailable_ratio_fails_closed(monkeypatch):
    import lexeme_aligner.target_morph as tm
    monkeypatch.setattr(tm, "should_stem", lambda iso, usj_dir=None, **k: (False, None))
    use_stem, ratio = rp.resolve_eflomal_stem(None, "zz", None)
    assert use_stem is False and ratio is None
