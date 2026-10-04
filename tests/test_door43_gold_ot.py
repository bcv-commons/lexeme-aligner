"""E2 follow-up (2026-09-27): Hebrew OT prefix crosswalk in door43_gold.py — unfoldingWord's per-morpheme
zaln rows (`c:H1961`, `d:H8199`, bare `H8199a` for augmented content) mapped onto the spine's own prefix
tokens (matched by NFC-normalised lemma, not the placeholder strong) and content tokens (augment-stripped
strong). Offline; a lightweight fake HebrewSource stands in for the real spine."""
import lexeme_aligner.eval.door43_gold as dg


def test_split_hebrew_strong():
    assert dg._split_hebrew_strong("d:H8199") == ("d", "H8199")
    assert dg._split_hebrew_strong("H8199") == ("", "H8199")
    assert dg._split_hebrew_strong("c:m:H1035") is None      # compound, not handled
    assert dg._split_hebrew_strong("i:H3808") == ("i", "H3808")
    assert dg._split_hebrew_strong(None) is None
    assert dg._split_hebrew_strong("x:H1") is None            # unrecognised code


def test_strip_augment():
    assert dg._strip_augment("H1121a") == "H1121"
    assert dg._strip_augment("H8199") == "H8199"
    assert dg._strip_augment("") == ""


def test_nfc_reconciles_combining_mark_order():
    # the real bug found on live data: spine lemma has dagesh-before-shva, hand-typed table the reverse
    spine_order = "בְּ"     # bet + dagesh + shva
    table_order = "בְּ"     # bet + shva + dagesh
    assert spine_order != table_order
    assert dg._nfc(spine_order) == dg._nfc(table_order)


class _Tok:
    def __init__(self, idx, surface, strong, lemma, is_content):
        self.idx, self.surface, self.strong, self.lemma, self.is_content = idx, surface, strong, lemma, is_content


class _FakeHeb:
    def __init__(self, toks):
        self._toks = toks

    def verse_tokens(self, book, ch, v):
        return self._toks


def _span(strong, seq, target_words, target_positions):
    return {"strong": strong, "lemma": "", "seq": seq, "target_words": target_words,
           "target_positions": target_positions}


def test_rows_for_ot_book_matches_prefix_and_content_and_strips_augment():
    # RUT 1:1-shaped: waw + finite verb ("and it happened"), then a content noun with an AUGMENTED
    # strong in the door43 row (H8199a) that the spine only knows as the bare rollup (H8199).
    toks = [_Tok(0, "and", "H2050", dg._HEB_PREFIX_LEMMA_STRONG["c"][0], False),
           _Tok(1, "it-happened", "H1961", "היה", True),
           _Tok(2, "the", "H1886", dg._HEB_PREFIX_LEMMA_STRONG["d"][0], False),
           _Tok(3, "judges", "H8199", "שפט", True)]
    heb = _FakeHeb(toks)
    spans = {(1, 1): [_span("c:H1961", 1, ["वह"], [0]), _span("H1961", 2, ["हुआ"], [1]),
                      _span("d:H8199a", 3, ["वे"], [2]), _span("H8199a", 4, ["न्यायी"], [3])]}
    texts = {(1, 1): "वह हुआ वे न्यायी"}
    words = {(1, 1): ["वह", "हुआ", "वे", "न्यायी"]}
    rows, st = dg.rows_for_ot_book("hin", "RUT", spans, texts, words, heb)
    assert st.get("no_spine_token", 0) == 0 and st.get("compound_or_unrecognised", 0) == 0
    assert len(rows) == 4
    by_surface = {r["surface"]: r["strong"] for r in rows}
    assert by_surface["वह"] == "H2050"       # the waw prefix token's OWN real strong, not the placeholder
    assert by_surface["हुआ"] == "H1961"
    assert by_surface["वे"] == "H1886"
    assert by_surface["न्यायी"] == "H8199"   # augment stripped to match the spine's bare rollup


def test_rows_for_ot_book_skips_compound_codes():
    toks = [_Tok(0, "m", "H4480", dg._HEB_PREFIX_LEMMA_STRONG["m"][0], False),
           _Tok(1, "house", "H1035", "בית", True)]
    heb = _FakeHeb(toks)
    spans = {(1, 1): [_span("c:m:H1035", 1, ["se"], [0]), _span("H1035", 2, ["ghar"], [1])]}
    texts = {(1, 1): "se ghar"}
    words = {(1, 1): ["se", "ghar"]}
    rows, st = dg.rows_for_ot_book("hin", "RUT", spans, texts, words, heb)
    assert st["compound_or_unrecognised"] == 1
    assert len(rows) == 1 and rows[0]["surface"] == "ghar"


def test_rows_for_ot_book_no_spine_token_when_prefix_absent():
    toks = [_Tok(0, "house", "H1035", "בית", True)]     # no waw token exists in this verse at all
    heb = _FakeHeb(toks)
    spans = {(1, 1): [_span("c:H1035", 1, ["aur"], [0])]}
    texts = {(1, 1): "aur"}
    words = {(1, 1): ["aur"]}
    rows, st = dg.rows_for_ot_book("hin", "RUT", spans, texts, words, heb)
    assert st["no_spine_token"] == 1 and rows == []


def test_build_gold_include_ot_flag(monkeypatch, tmp_path):
    monkeypatch.setitem(dg.da.LANGUAGES, "zz", {"org": "o", "repo": "zz_glt", "tag": "zzglt"})
    monkeypatch.setattr(dg.da, "list_available_books", lambda iso: ["TIT", "RUT"])
    monkeypatch.setattr(dg.da, "parsed_book", lambda iso, book: ({}, {}, {}))
    calls = []
    monkeypatch.setattr(dg, "rows_for_book", lambda *a: (calls.append("nt"), ([], {}))[1])
    monkeypatch.setattr(dg, "rows_for_ot_book", lambda *a: (calls.append("ot"), ([], {}))[1])
    dg.build_gold("zz", res_dir=tmp_path, include_ot=True)
    assert calls == ["nt", "ot"]
    calls.clear()
    dg.build_gold("zz", res_dir=tmp_path, include_ot=False)
    assert calls == ["nt"]
