"""tr_align: Hebrew verse numerals, pointed-form matching, position -> TR key mapping, tier selection."""
import json
from types import SimpleNamespace

from lexeme_aligner import tr_align as ta


def test_numeral():
    assert [ta.numeral(n) for n in (1, 9, 10, 11, 15, 16, 17, 20, 41, 100, 176)] == \
        ["א", "ט", "י", "יא", "טו", "טז", "יז", "כ", "מא", "ק", "קעו"]


def test_pointed_drops_accents_keeps_vowels():
    assert ta.pointed("הָ֥אָ֗רֶץ") == ta.pointed("הָאָרֶץ") == "הָאָרֶץ"


def _tok(keys, strong="G0001", content=True):
    return SimpleNamespace(keys=keys, strong=strong, is_content=content)


class FakePos:
    """Two TR verses: MAT 1:1 = [A, B(merged two words), C]; MAT 1:2 = [D]."""
    V = {("MAT", 1, 1): [_tok(["k1"], "G0100"), _tok(["k2", "k3"], "G0200"), _tok(["k4"], "G0300")],
         ("MAT", 1, 2): [_tok(["k5"], "G0400")]}

    def tokens(self, b, c, v):
        return self.V.get((b, c, v), [])

    def __call__(self, b, c, v, pos):
        while True:
            toks = self.tokens(b, c, v)
            if not toks:
                return None
            if pos < len(toks):
                return toks[pos]
            pos, v = pos - len(toks), v + 1


def _runs(tmp_path, runs):
    for i, pairs in enumerate(runs, 1):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "align_eflomal_ed_MAT.jsonl").write_text(json.dumps(
            {"book": "MAT", "chapter": 1, "verse": 1, "pairs": [{"h_idx": h, "t_idx": t} for h, t in pairs]}) + "\n")


def test_select_tiers(tmp_path):
    # target verse MAT 1:1 = "א אלף בית גימל"; token 0 is the verse numeral
    verses = {"MAT": {(1, 1): "א אלף בית גימל"}}
    forms = {"בית": ("hbo:b",), "גימל": ("hbo:g",)}
    bridge = {"hbo:b": {200}, "hbo:g": {400}}
    _runs(tmp_path, [
        [(0, [1]), (1, [0]), (1, [3])],       # h0 -> 1 stable; h1 -> numeral never; h1 -> 3 once
        [(0, [1]), (1, [0]), (1, [2])],       # h1 -> 2 once (bridge confirms G0200 at token 2)
        [(0, [1]), (3, [3])],                 # h3 = MAT 1:2 D via pooling, once; the bridge confirms G0400 at token 3
    ])
    out = ta.select("ed", tmp_path, verses, forms=forms, bridge=bridge, pos=FakePos())
    got = {(tuple(l["keys"]), tuple(l["t"]), l["tier"]) for l in out}
    assert (("k1",), (1,), "stable") in got
    assert (("k2", "k3"), (2,), "rescue") in got           # merged token keeps both ids
    assert (("k5",), (3,), "rescue") in got                # pooled position walks into the next verse
    assert not any(0 in l["t"] for l in out)                # the numeral token is never linked
    assert not any(l["tier"] == "rescue" and l["t"] == [3] and l["keys"] == ["k2", "k3"] for l in out)


def test_select_bridge_only(tmp_path):
    verses = {"MAT": {(1, 1): "א אלף בית גימל"}}
    forms = {"גימל": ("hbo:g",)}
    bridge = {"hbo:g": {300}}
    _runs(tmp_path, [[(0, [1])]])
    out = ta.select("ed", tmp_path, verses, forms=forms, bridge=bridge, pos=FakePos())
    assert {(tuple(l["keys"]), tuple(l["t"]), l["tier"]) for l in out} == {(("k1",), (1,), "stable"), (("k4",), (3,), "bridge")}


def test_load_analysis_and_union(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows = [  # MAT 1:1 token 2 ("בית"): tagger word, confident; token 3: tagger word below the threshold
        dict(book="MAT", chapter=1, verse=1, i=2, part=0, lexeme="hbo:x", source="model", p_split=0.95),
        dict(book="MAT", chapter=1, verse=1, i=2, part=1, lexeme="hbo:b", source="model", p_split=None),
        dict(book="MAT", chapter=1, verse=1, i=3, part=0, lexeme="hbo:g", source="model", p_split=0.5),
    ]
    p = tmp_path / "words.parquet"
    pq.write_table(pa.Table.from_pylist(rows), p)
    an = ta.load_analysis(p, 0.9)
    assert an == {("MAT", 1, 1, 2): ("hbo:x", "hbo:b")}
    # the analysis alone finds G0200 at token 2; the form lookup alone finds G0300 at token 3 -> both bridge links
    runs = tmp_path / "w"
    runs.mkdir()
    _runs(runs, [[(0, [1])]])
    out = ta.select("ed", runs, {"MAT": {(1, 1): "א אלף בית גימל"}}, forms={"גימל": ("hbo:g",)},
                    bridge={"hbo:b": {200}, "hbo:g": {300}}, pos=FakePos(), analysis=an)
    assert {(tuple(l["keys"]), l["tier"]) for l in out} == {(("k1",), "stable"), (("k2", "k3"), "bridge"), (("k4",), "bridge")}
