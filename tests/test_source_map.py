"""source_map.py (plan §2.6c M1) — offline: synthetic tokens/HebrewSource stand-ins, no spine DB."""
import json
from types import SimpleNamespace as NS

import lexeme_aligner.eval.source_map as sm
from lexeme_aligner.compact_align import build_source_lexemes


def tok(idx, lexeme, strong="H1", content=True, **kw):
    base = dict(idx=idx, lexeme=lexeme, strong=strong, is_content=content, state=None, rela=None,
                construct_group=None, case_=None)
    base.update(kw)
    return NS(**base)


CTX = sm.MapContext(
    lex_pos={"hbo:n1": "name", "hbo:n2": "name", "hbo:n3": "name", "hbo:n4": "name", "hbo:noun": "noun"},
    light={"hbo:lite"},
    targets={"H7": {"f": 3, "mean": 2.6, "langs": 9, "multi_langs": 5},
             "H8": {"f": 1, "mean": 1.2, "langs": 9, "multi_langs": 5},     # f < 2: not gated in
             "H9": {"f": 3, "mean": 2.6, "langs": 9, "multi_langs": 2}})    # multi_langs < 3: not gated in


def tags_of(toks, assim=frozenset(), ctx=CTX):
    classified, flags = sm.classify_verse(toks, ctx, assim)
    return [t for _tok, t in classified], flags


def test_only_content_tokens_get_an_ordinal():
    toks = [tok(0, "hbo:noun"), tok(1, "hbo:1886a", content=False), tok(2, "hbo:noun"),
            tok(3, "hbo:x", strong=None)]
    tags, _ = tags_of(toks)
    assert len(tags) == 2


def test_construct_head_rectum_and_chain_member():
    toks = [tok(0, "hbo:noun", state="construct", construct_group="g1"),
            tok(1, "hbo:noun", construct_group="g1"),
            tok(2, "hbo:noun", rela="rec", construct_group="g1")]
    tags, _ = tags_of(toks)
    assert tags[0] == ["construct_head"]
    assert tags[1] == ["construct_chain_member"]
    assert tags[2] == ["construct_rectum"]


def test_name_and_light_and_tags_are_sorted():
    toks = [tok(0, "hbo:n1"), tok(1, "hbo:lite")]
    tags, _ = tags_of(toks)
    assert tags == [["name"], ["light"]]
    t2, _ = tags_of([tok(0, "hbo:n1", state="construct")])
    assert t2[0] == sorted(t2[0]) == ["construct_head", "name"]


def test_name_list_needs_three_consecutive_content_names_skipping_non_content():
    conj = tok(9, "hbo:and", content=False)
    three = [tok(0, "hbo:n1"), conj, tok(1, "hbo:n2"), conj, tok(2, "hbo:n3")]
    tags, flags = tags_of(three)
    assert all("name_list" in t for t in tags) and flags == ["name_dense"]
    two = [tok(0, "hbo:n1"), tok(1, "hbo:n2")]
    tags2, flags2 = tags_of(two)
    assert not any("name_list" in t for t in tags2) and flags2 == []


def test_name_list_broken_by_an_intervening_content_token():
    toks = [tok(0, "hbo:n1"), tok(1, "hbo:n2"), tok(2, "hbo:noun"), tok(3, "hbo:n3"), tok(4, "hbo:n4")]
    tags, flags = tags_of(toks)
    assert not any("name_list" in t for t in tags)        # runs of 2 and 2 — never 3 consecutive
    assert flags == ["name_dense"]                          # but 4 names in the verse -> the verse flag


def test_assimilated_article_marks_the_token_after_the_swallowing_preposition():
    toks = [tok(4, "hbo:prep", content=False), tok(5, "hbo:noun"), tok(6, "hbo:noun")]
    tags, _ = tags_of(toks, assim={4})
    assert "assimilated_article" in tags[0] and "assimilated_article" not in tags[1]


def test_greek_case_tags():
    toks = [tok(0, "grc:1", case_="genitive"), tok(1, "grc:2", case_="dative"),
            tok(2, "grc:3", case_="nominative")]
    tags, _ = tags_of(toks)
    assert tags == [["case_genitive"], ["case_dative"], []]


def test_expected_fertility_uses_the_same_gate_as_fertility_priors():
    toks = [tok(0, "hbo:noun", strong="H7"), tok(1, "hbo:noun", strong="H8"), tok(2, "hbo:noun", strong="H9"),
            tok(3, "hbo:noun", strong="H10")]
    tags, _ = tags_of(toks)
    assert tags == [["expected_fertility=3"], [], [], []]


def test_missing_context_data_degrades_to_fewer_classes_not_an_error():
    tags, flags = tags_of([tok(0, "hbo:n1", state="construct")], ctx=sm.MapContext())
    assert tags == [["construct_head"]] and flags == []


class _FakeHeb:
    has_assimilated_articles = True

    def __init__(self, verses):
        self._v = verses                                       # {(ch, v): [tokens]}

    def chapters(self, book):
        return sorted({c for c, _ in self._v})

    def verses(self, book, ch):
        return sorted(v for c, v in self._v if c == ch)

    def verse_tokens(self, book, ch, v):
        return self._v[(ch, v)]

    def assimilated_after_idx(self, book, ch, v):
        return {1} if (ch, v) == (1, 2) else set()


def _heb():
    return _FakeHeb({(1, 1): [tok(0, "hbo:noun"), tok(1, "hbo:1886a", content=False), tok(2, "hbo:n1")],
                     (1, 2): [tok(0, "hbo:prep", content=False), tok(1, "hbo:prep2", content=False),
                              tok(2, "hbo:noun")],
                     (2, 1): []})


def test_book_map_is_position_parallel_to_compact_align_source_lexemes():
    heb = _heb()
    lexemes = build_source_lexemes(heb, "RUT")
    bmap = sm.build_book_map(heb, "RUT", CTX)
    assert list(bmap) == list(lexemes)                        # same verse keys, same order
    for ref, lex in lexemes.items():
        assert len(bmap[ref]["tags"]) == len(lex)             # same content ordinals, per verse
    assert bmap["RUT 1:2"]["tags"] == [["assimilated_article"]]
    assert bmap["RUT 2:1"] == {"tags": [], "flags": []}


def test_write_and_load_roundtrip(tmp_path):
    fp = sm.write_book_map(_heb(), "RUT", CTX, tmp_path)
    assert fp == tmp_path / "RUT.json"
    assert sm.load_source_map("RUT", tmp_path) == sm.build_book_map(_heb(), "RUT", CTX)


def test_class_counts_collapses_fertility_values_and_counts_flags():
    bmap = {"a": {"tags": [["expected_fertility=2", "name"], ["expected_fertility=3"]], "flags": ["name_dense"]}}
    c = sm.class_counts(bmap)
    assert c["expected_fertility"] == 2 and c["name"] == 1 and c["flag:name_dense"] == 1


def test_meta_documents_what_is_not_emitted(tmp_path, monkeypatch):
    class H(_FakeHeb):
        pass
    monkeypatch.setattr("lexeme_aligner.hebrew_source.HebrewSource", lambda: _heb())
    monkeypatch.setattr(sm, "load_context", lambda *a, **k: CTX)
    assert sm.main(["--books", "RUT", "--out-dir", str(tmp_path)]) == 0
    meta = json.loads((tmp_path / "_meta.json").read_text())
    assert "elided_copula" in meta["not_emitted"] and meta["books"] == 1
