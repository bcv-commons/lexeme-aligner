"""HebrewSource syntax-source switch (MACULA-only migration, internal-docs/macula-only-migration-plan.md).

Synthetic two-verse spines, no real DB: one WITH the six BHSA-derived columns (the current spine), one WITHOUT
them plus `construct_role` (the stripped MACULA-only variant bcv-query delivered 2026-10-03)."""
import sqlite3

import pytest

from lexeme_aligner.hebrew_source import HebrewSource, syntax_source

_BASE = ["book TEXT", "chapter INT", "verse INT", "idx INT", "surface TEXT", "lexeme TEXT", "strong INT",
         "lemma TEXT", "is_content INT", "morph TEXT", "gloss TEXT", "stem TEXT", "state TEXT",
         "construct_group TEXT", "head_idx INT", "phrase_role TEXT"]
_BHSA = ["phrase_id TEXT", "function TEXT", "rela TEXT", "sense TEXT", "sense_conf REAL", "sense_source TEXT"]
_ROWS = [  # idx, surface, lexeme, strong, state, group, head, role  (+ bhsa: phrase, function, rela, sense)
    (1, "בֵּית", "hbo:1004", 1004, "construct", "g1", None, "s", ("p1", "Subj", "NA", "1")),
    (2, "אֱלֹהִים", "hbo:0430", 430, "absolute", "g1", 1, "s", ("p1", "Subj", "rec", "1")),
    (3, "אָמַר", "hbo:0559", 559, None, None, None, "v", ("p2", "Pred", "NA", "2")),
]
_ROLE = {1: "regens", 2: "rectum", 3: None}


def _make(path, bhsa: bool, role_col: bool):
    cols = _BASE + (_BHSA if bhsa else []) + (["construct_role TEXT"] if role_col else [])
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE spine_words ({', '.join(cols)})")
    for idx, sf, lx, st, state, grp, head, prole, b in _ROWS:
        vals = ["GEN", 1, 1, idx, sf, lx, st, "l", 1, "m", "g", None, state, grp, head, prole]
        if bhsa:
            vals += [b[0], b[1], b[2], b[3], 0.9, "x"]
        if role_col:
            vals += [_ROLE[idx]]
        con.execute(f"INSERT INTO spine_words VALUES ({','.join('?' * len(vals))})", vals)
    con.commit()
    con.close()
    return path


@pytest.fixture
def current(tmp_path):
    return _make(tmp_path / "current.db", bhsa=True, role_col=False)


@pytest.fixture
def stripped(tmp_path):
    return _make(tmp_path / "stripped.db", bhsa=False, role_col=True)


def test_default_is_macula_since_the_switch(monkeypatch):
    monkeypatch.delenv("ALIGNER_SYNTAX_SOURCE", raising=False)
    assert syntax_source() == "macula"
    monkeypatch.setenv("ALIGNER_SYNTAX_SOURCE", "bhsa")             # the A/B baseline arm stays reachable explicitly
    assert syntax_source() == "bhsa"


def test_default_spine_is_the_macula_only_variant():
    from lexeme_aligner import config
    assert config.SPINE_DB.name == "lexeme-spine-macula.db" or "ALIGNER_SPINE_DB" in __import__("os").environ
    assert config.BHSA_BASELINE_SPINE.name == "lexeme-spine-bhsa-baseline.db"


def test_bad_value_rejected(monkeypatch):
    monkeypatch.setenv("ALIGNER_SYNTAX_SOURCE", "etcbc")
    with pytest.raises(ValueError):
        syntax_source()


def test_bhsa_mode_reads_bhsa_columns(current, tmp_path):
    src = HebrewSource(current, tmp_path / "none.db", syntax="bhsa")
    t = src.verse_tokens("GEN", 1, 1)
    assert (t[1].function, t[1].rela, t[1].phrase_id, t[1].sense) == ("Subj", "rec", "p1", "1")
    assert t[1].phrase_role == "s" and t[1].construct_group == "g1"      # MACULA columns read either way


def test_macula_mode_ignores_bhsa_columns_even_when_present(current, tmp_path):
    src = HebrewSource(current, tmp_path / "none.db", syntax="macula")
    assert not src.has_phrase and not src.has_sense
    for t in src.verse_tokens("GEN", 1, 1):
        assert (t.function, t.rela, t.phrase_id, t.sense, t.sense_conf) == (None, None, None, None, None)
    t = src.verse_tokens("GEN", 1, 1)
    assert t[1].phrase_role == "s" and t[1].head_idx == 1 and t[1].state == "absolute"   # MACULA stays on


def test_macula_mode_never_opens_hbo_sidecar(current, tmp_path):
    hbo = tmp_path / "hbo.db"
    sqlite3.connect(hbo).close()
    assert HebrewSource(current, hbo, syntax="macula").hbo is None
    assert HebrewSource(current, hbo, syntax="bhsa").hbo is not None


def test_stripped_spine_reads_construct_role(stripped, tmp_path):
    src = HebrewSource(stripped, tmp_path / "none.db", syntax="macula")
    assert src.has_construct_role and not src.has_phrase
    assert [t.construct_role for t in src.verse_tokens("GEN", 1, 1)] == ["regens", "rectum", None]


def test_stripped_spine_in_bhsa_mode_warns_loudly(stripped, tmp_path, capsys):
    src = HebrewSource(stripped, tmp_path / "none.db", syntax="bhsa")
    assert not src.has_phrase
    assert "WARNING: spine has no BHSA phrase columns" in capsys.readouterr().err


def test_deliberate_macula_mode_does_not_warn(stripped, tmp_path, capsys):
    HebrewSource(stripped, tmp_path / "none.db", syntax="macula")
    assert capsys.readouterr().err == ""


def test_env_var_selects_mode(current, tmp_path, monkeypatch):
    monkeypatch.setenv("ALIGNER_SYNTAX_SOURCE", "macula")
    assert HebrewSource(current, tmp_path / "none.db").syntax_source == "macula"
