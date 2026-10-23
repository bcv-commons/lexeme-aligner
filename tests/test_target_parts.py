"""target_parts: loading a part analysis, splitting tokens into eflomal units, folding part links back to tokens."""
import json

import pyarrow as pa
import pyarrow.parquet as pq

from lexeme_aligner.target_parts import load_parts


def _row(i, part, token, text, lemma, cls, source="macula", p=None):
    return dict(book="MAT", chapter=1, verse=1, i=i, part=part, token=token, text=text, lemma=lemma, **{"class": cls},
                source=source, p_split=p)


def _parquet(tmp_path):
    rows = [
        _row(0, 0, "א", "א", None, "verse-number"),
        _row(1, 0, "ובני", "וּ", "וְ", "cj"), _row(1, 1, "ובני", "בְנֵי", "בֵּן", "noun"),
        _row(2, 0, "לבית", "לַ", "לְ", "prep", "model", 0.97), _row(2, 1, "לבית", "", "הַ", "art"),
        _row(2, 2, "לבית", "בַּיִת", "בַּיִת", "noun"),
        _row(3, 0, "ביתו", "בֵּית", "בַּיִת", "noun", "model", 0.5), _row(3, 1, "ביתו", "וֹ", "הוּא", "pron"),
    ]
    p = tmp_path / "words.parquet"
    pq.write_table(pa.Table.from_pylist(rows), p)
    return p


def test_load_parts(tmp_path):
    parts = load_parts(_parquet(tmp_path), 0.9)
    assert set(parts) == {("MAT", 1, 1, 0), ("MAT", 1, 1, 1), ("MAT", 1, 1, 2)}   # one-part words and unsure splits stay whole
    assert parts[("MAT", 1, 1, 0)]["parts"] == []                      # the verse number: left out
    assert parts[("MAT", 1, 1, 1)]["parts"] == [(0, "ו", "ו", "cj"), (1, "בני", "בן", "noun")]
    assert [p[0] for p in parts[("MAT", 1, 1, 2)]["parts"]] == [0, 2]   # the hidden article has no letters: no unit


def _usj(path, verses):
    content = [{"type": "book", "marker": "id", "code": "MAT", "content": []}, {"type": "chapter", "marker": "c", "number": "1"},
               {"type": "para", "marker": "p", "content": [x for v, t in verses for x in ({"type": "verse", "marker": "v", "number": v}, t)]}]
    path.write_text(json.dumps({"type": "USJ", "version": "3.0", "content": content}, ensure_ascii=False), encoding="utf-8")


def test_view_and_fold(tmp_path):
    from lexeme_aligner.target_parts import fold, write_view
    from lexeme_aligner.usj_source import read_verses, tokenize
    parts = load_parts(_parquet(tmp_path), 0.9)
    real, view, vout, out = (tmp_path / d for d in ("real", "view", "vout", "out"))
    real.mkdir()
    _usj(real / "40-MAT.json", [("1", "א ובני לבית ביתו "), ("2", "ויאמר ")])
    maps = write_view(real, view, parts)
    assert tokenize(read_verses(view / "40-MAT.json")[(1, 1)]) == ["ו", "בני", "ל", "בית", "ביתו"]   # verse number out, parts in
    assert maps["MAT"]["1:1"] == [[1, 0], [1, 1], [2, 0], [2, 2], [3, None]]
    assert read_verses(view / "40-MAT.json")[(1, 2)] == "ויאמר"                                        # no analysis: whole
    vout.mkdir()
    rec = {"ref": "MAT 1:1", "book": "MAT", "chapter": 1, "verse": 1, "pairs": [
        {"h_idx": 0, "t_idx": [0], "target": "ו", "method": "gloss"},
        {"h_idx": 1, "t_idx": [2, 3], "target": "ל בית", "method": "gloss"},
        {"h_idx": 2, "t_idx": [4], "target": "ביתו", "method": "gloss", "prior": "spanext_noun_before:4"},
        {"h_idx": 3, "t_idx": [9], "target": "?", "method": "gloss"}]}
    (vout / "align_gloss_t_MAT.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    stats = fold("t", vout, out, view, real, methods=("gloss",), trefs={("MAT", 1, 1): (1, 1)})
    got = [json.loads(line) for line in (out / "align_gloss_t_MAT.jsonl").read_text(encoding="utf-8").splitlines()][0]["pairs"]
    assert [(p["t_idx"], p["t_parts"], p["target"]) for p in got] == [
        ([1], [[1, 0]], "ובני"), ([2], [[2, 0], [2, 2]], "לבית"), ([3], [[3, None]], "ביתו")]
    assert got[2]["prior"] == "spanext_noun_before:3"                   # a span-extension prior's unit position becomes a token position
    assert stats["dropped"] == 1 and stats["gloss"] == 3                # a unit outside the verse is dropped, never guessed


def test_content_priors(tmp_path, monkeypatch):
    from lexeme_aligner import config, target_parts
    pp = tmp_path / "prior_pack.parquet"
    pq.write_table(pa.Table.from_pylist([{"lexeme": "grc:5207", "keyness": 1.0}, {"lexeme": "grc:2532", "keyness": None}]), pp)
    monkeypatch.setattr(config, "PRIOR_PACK", pp)
    parts = load_parts(_parquet(tmp_path), 0.9)
    real, view, vout = (tmp_path / d for d in ("real", "view", "vout"))
    real.mkdir()
    vout.mkdir()
    _usj(real / "40-MAT.json", [("1", "א ובני לבית ביתו ")])
    target_parts.write_view(real, view, parts)                         # units: ו בני ל בית ביתו
    rec = {"book": "MAT", "chapter": 1, "verse": 1, "pairs": [
        {"h_idx": 0, "t_idx": [0], "target": "ו", "lexeme": "grc:2532", "strong": "G2532"},       # function lexeme: out
        {"h_idx": 1, "t_idx": [1], "target": "בני", "lexeme": "grc:5207", "strong": "G5207"},     # content on a noun part: kept
        {"h_idx": 2, "t_idx": [2, 3], "target": "ל בית", "lexeme": "grc:5207", "strong": "G5207"},  # touches a prep part: out
        {"h_idx": 3, "t_idx": [4], "target": "ביתו", "lexeme": "grc:5207", "strong": "G5207"}]}   # whole word with a pron part: out
    (vout / "align_eflomal_t_MAT.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    stats = target_parts.content_priors("t", "heb", vout, view, _parquet(tmp_path), tmp_path / "lex", trefs={("MAT", 1, 1): (1, 1)})
    rows = pq.read_table(tmp_path / "lex" / "iso=heb" / "data.parquet").to_pylist()
    assert [(r["surface"], r["lexeme"], r["method"], r["count"]) for r in rows] == [("בני", "grc:5207", "eflomal", 1)]
    assert stats["kept"] == 1 and stats["skipped"] == 3
