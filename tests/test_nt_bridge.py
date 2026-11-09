import json
from types import SimpleNamespace

import pytest

from lexeme_aligner import nt_bridge

CFG = {"enabled": True, "max_ot_books": 10, "min_nt_books": 20, "scale": 2.0, "top_k": 5, "min_share": 0.1}
NT = ["MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI",
      "TIT", "PHM", "HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV"]


def _usj(tmp_path, books):
    for i, b in enumerate(books):
        (tmp_path / f"{i:02d}-{b}.json").write_text("{}")
    return tmp_path


def test_applies_thin_ot_with_nt(tmp_path):
    assert nt_bridge.applies(_usj(tmp_path, ["RUT", "JON"] + NT), CFG)


def test_not_without_ot_or_nt_or_when_off(tmp_path):
    (a := tmp_path / "a").mkdir(); (b := tmp_path / "b").mkdir(); (c := tmp_path / "c").mkdir()
    assert not nt_bridge.applies(_usj(a, NT), CFG)                         # no OT
    assert not nt_bridge.applies(_usj(b, ["RUT", "JON", "MAT"]), CFG)      # no NT to learn from
    assert not nt_bridge.applies(_usj(c, ["RUT"] + NT), {**CFG, "enabled": False})


def test_not_above_max_ot_books(tmp_path):
    ot = ["GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA", "1KI"]
    assert not nt_bridge.applies(_usj(tmp_path, ot + NT), CFG)


def test_build_priors(tmp_path, monkeypatch):
    pairs = [{"strong": "G2316", "target": "God", "t_idx": [3], "content": True}] * 9 + \
            [{"strong": "G2316", "target": "gods", "t_idx": [1], "content": True},
             {"strong": "G2316", "target": "the god", "t_idx": [1, 2], "content": True},     # two tokens: not used
             {"strong": "G3588", "target": "the", "t_idx": [0], "content": False}]          # function word: not used
    (tmp_path / "align_eflomal_x_MAT.jsonl").write_text(json.dumps({"pairs": pairs}) + "\n")
    monkeypatch.setattr(nt_bridge, "lxx_bridge", lambda: {"hbo:0430": ["G2316", "G3588"]})
    tok = SimpleNamespace(is_content=True, strong="H0430", lexeme="hbo:0430")
    out = nt_bridge.build_priors([SimpleNamespace(heb=[tok])], "x", tmp_path, CFG)
    assert sorted(out) == [("H0430", "god", pytest.approx(1.8)), ("H0430", "gods", pytest.approx(0.2))]


def test_build_priors_needs_the_nt(tmp_path, monkeypatch):
    monkeypatch.setattr(nt_bridge, "lxx_bridge", lambda: {})
    with pytest.raises(SystemExit):
        nt_bridge.build_priors([], "x", tmp_path, CFG)


OT39 = ["GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB",
        "PSA", "PRO", "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO", "OBA", "JON", "MIC", "NAM", "HAB", "ZEP",
        "HAG", "ZEC", "MAL"]
MAN = {"languages": {"bel": {"editions": {"bel_BBL": {"tag": "bbl", "books": OT39 + NT}, "bel_jfc": {"books": OT39 + NT},
                                          "bel_njo": {"books": ["RUT", "JON"] + NT}, "bel_thin": {"books": ["PSA"] + NT}}}}}
POOL = {**CFG, "pool": {"enabled": True, "weight": 0.5, "min_ot_books": 39}}


def test_pool_sources_only_other_full_ot_editions():
    assert nt_bridge.pool_sources("bel_njo", POOL, MAN) == ("bel", {"bel_BBL", "bel_jfc"})    # not itself, not bel_thin
    assert nt_bridge.pool_sources("bel_njo", CFG, MAN) is None                               # pool off
    assert nt_bridge.pool_sources("xyz", POOL, MAN) is None                                  # tag in no language
    one = {"languages": {"bel": {"editions": {"bel_njo": {"books": ["RUT"] + NT}, "bel_thin": {"books": ["PSA"] + NT}}}}}
    assert nt_bridge.pool_sources("bel_njo", POOL, one) is None                              # no full-OT edition


def test_compact_id_join_rule():
    assert nt_bridge.compact_id("eng", "BSB") == "eng_BSB"
    assert nt_bridge.compact_id("arb", "arb_vdv") == "arb_vdv" and nt_bridge.compact_id("eng", "ENGWEB") == "ENGWEB"


def test_build_priors_pools_full_ot_editions(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    (tmp_path / "align_eflomal_bel_njo_MAT.jsonl").write_text(json.dumps({"pairs": [
        {"strong": "G2316", "target": "Бог", "t_idx": [0], "content": True}]}) + "\n")
    monkeypatch.setattr(nt_bridge, "lxx_bridge", lambda: {"hbo:0430": ["G2316"]})
    lex = tmp_path / "lex"
    (lex / "iso=bel").mkdir(parents=True)
    rows = [("Госпад", "BBL", 8), ("Бог", "BBL", 2), ("Божа", "bel_thin", 50), ("Свой", "bel_njo", 50)]   # thin + itself: left out
    pq.write_table(pa.table({"surface": [r[0] for r in rows], "lexeme": ["hbo:0430"] * 4, "method": ["eflomal"] * 4,
                             "base_text": [r[1] for r in rows], "count": [r[2] for r in rows]}), lex / "iso=bel/data.parquet")
    monkeypatch.setattr(nt_bridge, "LEX_ROOT", lex)
    monkeypatch.setattr(nt_bridge, "COMPACT_MANIFEST", MAN)
    tok = SimpleNamespace(is_content=True, strong="H0430", lexeme="hbo:0430")
    out = dict(((s, w), a) for s, w, a in nt_bridge.build_priors([SimpleNamespace(heb=[tok])], "bel_njo", tmp_path, POOL))
    assert out == {("H0430", "бог"): pytest.approx(2.0), ("H0430", "госпад"): pytest.approx(0.8)}   # bridge 1.0, pool 0.5 x 0.8, x scale 2
    assert nt_bridge.build_priors([SimpleNamespace(heb=[tok])], "bel_njo", tmp_path, CFG) == [("H0430", "бог", pytest.approx(2.0))]
