"""Synthetic tests for gold_to_fullalign.py (docs/architecture.md §4): record shape + attribution per
layer, occurrence-ambiguity exclusion, refused-verse accounting, vendor-only routing, quarantine flag,
manifest coverage math. No real spine/gold/corpus needed."""
import collections
import json

import lexeme_aligner.eval.gold_to_fullalign as gf
from lexeme_aligner.hebrew_source import HebToken
from lexeme_aligner.eval.pos_score import GoldVerse


def tok(idx, strong, lexeme=None, content=True):
    return HebToken(idx, f"w{idx}", strong, lexeme or f"lx:{strong}", f"l{idx}", None, content)


class _Corpus:
    """Stand-in for gold_to_fullalign.Corpus with hand-built verses."""
    def __init__(self, verses, anchor_of=None):
        self.toks, self.by_key, self.counts, self.by_id = {}, {}, {}, {}
        self.anchor_of = dict(anchor_of or {})
        for ref, (toks, heb) in verses.items():
            self.toks[ref] = toks
            seen = collections.Counter()
            self.by_key[ref] = {}
            self.by_id[ref] = {}
            for t in heb:
                self.by_key[ref][(t.strong, seen[t.strong])] = t
                seen[t.strong] += 1
                for key in t.keys:
                    self.by_id[ref][key] = t
            self.counts[ref] = seen


# --- routing -------------------------------------------------------------------------------------------
def test_route_vendor_only_sword_is_not_publishable():
    r = gf.route("spa", "RV1909", "sword")
    assert r["publishable"] is False and r["license"] == "vendor-only" and r["quarantined"] is False


def test_route_public_domain_sword_is_publishable():
    r = gf.route("cmn", "ChiUns", "sword")
    assert r["publishable"] is True and r["license"] == "Public Domain"


def test_route_clear_rus_is_published_but_quarantined():
    r = gf.route("rus", "RUSSYN", "clear")
    assert r["publishable"] is True and r["quarantined"] is True and r["quarantine_reason"]


def test_route_clear_default_is_cc_by():
    assert gf.route("hin", "IRVHin", "clear") == {"publishable": True, "license": "CC-BY-4.0",
                                                  "quarantined": False, "quarantine_reason": None}


# --- record shape --------------------------------------------------------------------------------------
def test_make_row_keeps_standard_fields_packs_the_rest_into_extra_and_fills_attribution():
    pair = {"h_idx": 2, "lexeme": "grc:2424", "strong": "G2424", "t_idx": [6, 8], "target": "यीशु की",
            "method": "manual", "content": True, "score": None, "tier": "core", "note": "n"}
    row = gf.make_row(40001001, "MAT", pair, {"source": "clear", "kind": "manual", "license": "CC-BY-4.0"})
    assert (row["chapter"], row["verse"]) == (1, 1)
    assert row["t_idx"] == [6, 8] and row["method"] == "manual"
    assert json.loads(row["extra"]) == {"tier": "core", "note": "n"}
    assert row["attribution"]["source"] == "clear" and row["attribution"]["model"] is None
    assert set(row["attribution"]) == set(gf._ATTR_KEYS)


def test_make_row_no_extra_when_only_standard_fields():
    row = gf.make_row(1001001, "GEN", {"h_idx": 0, "t_idx": [1], "method": "eflomal"}, gf.STATISTICAL_ATTR)
    assert row["extra"] is None and row["attribution"]["kind"] == "statistical"


# --- gold -> rows ----------------------------------------------------------------------------------------
def _gold(ref, links, counts_ok=True):
    gv = GoldVerse(ref)
    for (strong, k), pos in links.items():
        gv.links[(strong, k)] = set(pos)
        gv.surfaces[(strong, k)] = " ".join(f"t{p}" for p in sorted(pos))
        gv.raw[(strong, k)] = (f"n{ref}{k:03d}", [f"{ref}{p:03d}" for p in sorted(pos)])
        gv.claimed |= set(pos)
    return {ref: gv}


def test_rows_from_gold_resolves_spine_token_lexeme_and_keeps_raw_ids():
    corpus = _Corpus({40001001: (["a", "b", "c"], [tok(0, "G0011"), tok(1, "G1080")])})
    gold = _gold(40001001, {("G0011", 0): [0, 1], ("G1080", 0): [2]})
    st = collections.Counter()
    rows = gf.rows_from_gold(gold, corpus, "manual", {"source": "clear", "kind": "manual", "license": "x"}, st)
    assert [r["h_idx"] for r in rows] == [0, 1]
    assert rows[0]["lexeme"] == "lx:G0011" and rows[0]["target"] == "a b" and rows[0]["t_idx"] == [0, 1]
    assert rows[0]["attribution"]["source_ids"] == ["n40001001000"]
    assert rows[0]["attribution"]["target_ids"] == ["40001001000", "40001001001"]
    assert st["links_converted"] == 2 and st["links_ambiguous"] == 0


def test_rows_from_gold_excludes_ambiguous_occurrence_counts():
    # spine has G0011 twice, gold aligned it once -> k may not denote the same token -> excluded, counted
    corpus = _Corpus({40001001: (["a", "b"], [tok(0, "G0011"), tok(1, "G0011")])})
    gold = _gold(40001001, {("G0011", 0): [0]})
    st = collections.Counter()
    rows = gf.rows_from_gold(gold, corpus, "manual", {}, st)
    assert rows == [] and st["links_ambiguous"] == 1 and st["links_converted"] == 0


def test_rows_from_gold_counts_verses_outside_pooled_corpus():
    corpus = _Corpus({})
    st = collections.Counter()
    rows = gf.rows_from_gold(_gold(40001001, {("G1", 0): [0]}), corpus, "manual", {}, st)
    assert rows == [] and st["verses_outside_corpus"] == 1


def test_function_word_rows_are_kept_and_counted_separately():
    corpus = _Corpus({1001001: (["a", "b"], [tok(0, "H9001", content=False), tok(1, "H7225")])})
    gold = _gold(1001001, {("H9001", 0): [0], ("H7225", 0): [1]})
    st = collections.Counter()
    rows = gf.rows_from_gold(gold, corpus, "manual", {}, st)
    cov = gf.coverage(st, rows)
    assert cov["rows"] == 2 and cov["content_rows"] == 1 and cov["function_word_rows"] == 1


# --- coverage / manifest math -----------------------------------------------------------------------------
def test_coverage_reports_refused_verses_from_load_gold_stats():
    st = collections.Counter({"verses": 100, "verses_mapped": 88, "verses_refused": 12, "links": 500,
                              "links_ambiguous": 40, "links_punct_only": 3})
    cov = gf.coverage(st, [])
    assert cov["verses_refused"] == 12 and cov["verses_mapped"] == 88 and cov["links_ambiguous"] == 40
    assert cov["rows"] == 0 and cov["links_punct_only"] == 3


# --- gbt target matching ------------------------------------------------------------------------------------
def test_match_run_unique_contiguous_only():
    toks = ["Kezdetben", "teremtette", "Isten", "az", "eget", "és", "az", "földet"]
    assert gf._match_run("teremtette Isten", toks) == [1, 2]
    assert gf._match_run("az", toks) is None                # two matches -> ambiguous -> None
    assert gf._match_run("nincs", toks) is None


# --- gold health ----------------------------------------------------------------------------------------
def test_gold_health_separates_positional_from_lexical_agreement():
    corpus = _Corpus({1: (["x", "y"], [tok(0, "H1")]), 2: (["y", "x"], [tok(0, "H1")])})
    gold = {**_gold(1, {("H1", 0): [0]}), **_gold(2, {("H1", 0): [0]})}   # verse1: t0="x"? surfaces are "t0"
    for gv in gold.values():                                              # make surfaces real words
        gv.surfaces[("H1", 0)] = corpus.toks[gv.ref][0]
    eflomal = [{"ref": 1, "strong": "H1", "target": "x", "content": True},
               {"ref": 2, "strong": "H1", "target": "x", "content": True}]   # right word, wrong verse for ref 2
    h = gf.gold_health(gold, eflomal, corpus)
    assert h["n"] == 2 and h["positional"] == 0.5 and h["lexical"] == 1.0 and h["gap"] == 0.5


# --- parquet partition -----------------------------------------------------------------------------------
def test_write_partition_one_file_per_book_with_sha(tmp_path):
    rows = [gf.make_row(1001001, "GEN", {"h_idx": 0, "t_idx": [0], "method": "eflomal"}, gf.STATISTICAL_ATTR),
            gf.make_row(40001001, "MAT", {"h_idx": 0, "t_idx": None, "method": "manual"}, {"source": "gbt"})]
    files = gf.write_partition(rows, tmp_path / "p")
    assert set(files) == {"GEN", "MAT"} and files["GEN"]["rows"] == 1 and len(files["GEN"]["content_sha256"]) == 64
    import pyarrow.parquet as pq
    back = pq.read_table(tmp_path / "p" / "MAT.parquet").to_pylist()
    assert back[0]["t_idx"] is None and back[0]["attribution"]["source"] == "gbt"


def test_llm_cells_excludes_mock(tmp_path):
    for name in ("align_llm_hinirv.verify.sonnet5.cli_MAT.jsonl", "align_llm_hinirv.gap-seeded.mock_MAT.jsonl",
                 "align_llm_hinirv.full.sonnet5.cli_ROM.jsonl.gz", "align_llm_other.full.x_MAT.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert gf.llm_cells("hinirv", tmp_path) == ["hinirv.full.sonnet5.cli", "hinirv.verify.sonnet5.cli"]


# --- foreign-owned partitions survive a regeneration of the language (bsb_tables.py's BSB-tables) ----------
def test_carry_over_foreign_partitions_keeps_owned_entries_and_never_overrides_new_ones():
    from lexeme_aligner.eval.gold_to_fullalign import carry_over_foreign_partitions
    prev = {"editions": {"engbsb": {"layers": {"manual": {
        "BSB": {"source": "clear", "rows": 1},                              # ours — regenerated, not carried
        "BSB-tables": {"source": "bsb-tables", "owner": "bsb_tables", "rows": 3}}}}}}
    new = {"editions": {"engbsb": {"layers": {"manual": {"BSB": {"source": "clear", "rows": 2}},
                                              "statistical": {"rows": 9}}}}}
    out = carry_over_foreign_partitions(prev, new)
    manual = out["editions"]["engbsb"]["layers"]["manual"]
    assert manual["BSB"] == {"source": "clear", "rows": 2}                 # new wins for our own partitions
    assert manual["BSB-tables"]["owner"] == "bsb_tables"                   # foreign one carried over
    assert out["editions"]["engbsb"]["layers"]["statistical"] == {"rows": 9}
    assert carry_over_foreign_partitions(None, {"editions": {}}) == {"editions": {}}
    # an edition that vanished from the new report still keeps its foreign partition
    out2 = carry_over_foreign_partitions(prev, {"editions": {}})
    assert set(out2["editions"]["engbsb"]["layers"]["manual"]) == {"BSB-tables"}


# --- 2026-10-06: Clear OT links are mapped by source id ('o' + spine key), not by Strong's counting ------------
def _gold_ids(ref, links):
    """links: {(strong, k): (target positions, source id)} — a GoldVerse that carries the gold's own source ids."""
    gv = GoldVerse(ref)
    for key, (pos, sid) in links.items():
        gv.links[key] = set(pos)
        gv.raw[key] = (sid, [f"{ref}00{p + 1}" for p in pos])
    return {ref: gv}


def _tok_keys(idx, strong, *keys):
    t = tok(idx, strong)
    t.keys = list(keys)
    return t


def test_ot_link_maps_by_id_even_when_the_strong_count_is_ambiguous():
    # the gold has one H1 link, the spine two H1 tokens: by (strong, k) this is ambiguous and dropped; the id picks the token
    corpus = _Corpus({8001001: (["a", "b"], [_tok_keys(0, "H0001", "080010010011"), _tok_keys(1, "H0001", "080010010021")])})
    gold = _gold_ids(8001001, {("H0001", 0): ([1], "o080010010021")})
    st = collections.Counter()
    rows = gf.rows_from_gold(gold, corpus, "manual", {}, st)
    assert len(rows) == 1 and rows[0]["h_idx"] == 1                      # the SECOND token, by id
    assert st["links_by_id"] == 1 and st["links_ambiguous"] == 0


def test_merged_token_is_reachable_by_either_of_its_row_keys():
    corpus = _Corpus({8001001: (["a"], [_tok_keys(0, "H1035", "080010010161", "080010010171")])})
    for sid in ("o080010010161", "o080010010171"):
        rows = gf.rows_from_gold(_gold_ids(8001001, {("H1035", 0): ([0], sid)}), corpus, "manual", {}, collections.Counter())
        assert len(rows) == 1 and rows[0]["h_idx"] == 0


def test_an_id_that_is_not_in_the_corpus_falls_back_to_strong_counting():
    corpus = _Corpus({8001001: (["a"], [_tok_keys(0, "H0001", "080010010011")])})
    st = collections.Counter()
    rows = gf.rows_from_gold(_gold_ids(8001001, {("H0001", 0): ([0], "o080010019991")}), corpus, "manual", {}, st)
    assert len(rows) == 1 and st["links_by_strong"] == 1 and st["links_by_id"] == 0


def test_the_gold_strong_disagreeing_with_the_spine_at_that_id_is_counted_for_qa():
    corpus = _Corpus({8001001: (["a"], [_tok_keys(0, "H0002", "080010010011")])})
    st = collections.Counter()
    rows = gf.rows_from_gold(_gold_ids(8001001, {("H0001", 0): ([0], "o080010010011")}), corpus, "manual", {}, st)
    assert len(rows) == 1 and st["links_id_strong_differs"] == 1


def test_a_verse_folded_into_another_verses_group_is_rehomed_to_the_anchor():
    # two Hebrew verses = one English verse: the corpus holds both under the anchor ref 11022043
    corpus = _Corpus({11022043: (["a", "b"], [_tok_keys(0, "H0001", "110220430011"), _tok_keys(1, "H0002", "110220440011")])},
                     anchor_of={11022044: 11022043})
    st = collections.Counter()
    rows = gf.rows_from_gold(_gold_ids(11022044, {("H0002", 0): ([1], "o110220440011")}), corpus, "manual", {}, st)
    assert len(rows) == 1 and rows[0]["ref"] == 11022043 and rows[0]["h_idx"] == 1


def test_a_link_on_a_prefix_that_leaves_its_word_unlinked_becomes_a_whole_word_link_labelled_by_the_stem():
    # וְ (H2050, prefix) + רָחַצְתְּ (H7364, stem) = one word; the gold puts "you bathe" on the prefix only (Clear BSB / IRVHin habit)
    pre, stem, nxt = tok(0, "H2050", content=False), tok(1, "H7364"), tok(2, "H5480")
    pre.keys, stem.keys, nxt.keys = ["080030030011"], ["080030030012"], ["080030030021"]
    corpus = _Corpus({8003003: (["you", "bathe", "anoint"], [pre, stem, nxt])})
    corpus.word_of = {8003003: {0: [pre, stem], 1: [pre, stem], 2: [nxt]}}
    st = collections.Counter()
    rows = gf.rows_from_gold(_gold(8003003, {("H2050", 0): [0, 1], ("H5480", 0): [2]}), corpus, "manual", {}, st)
    word = next(r for r in rows if r["t_idx"] == [0, 1])
    assert word["h_idx"] == 1 and word["strong"] == "H7364" and json.loads(word["extra"]) == {"word_h_idx": [0, 1]}
    assert st["links_word_level"] == 1
    # the same prefix link is NOT word-level when the stem has its own link
    st2 = collections.Counter()
    rows2 = gf.rows_from_gold(_gold(8003003, {("H2050", 0): [0], ("H7364", 0): [1]}), corpus, "manual", {}, st2)
    assert sorted(r["h_idx"] for r in rows2) == [0, 1] and st2["links_word_level"] == 0


def test_source_unit_word_makes_a_whole_word_row_even_when_the_gold_links_the_stem():
    # bcv-commons/strongs >= 22549b1: the BSB / IRVHin record names the stem (H7364) and says source_unit = "word"
    pre, stem = tok(0, "H2050", content=False), tok(1, "H7364")
    pre.keys, stem.keys = ["080030030011"], ["080030030012"]
    corpus = _Corpus({8003003: (["you", "bathe"], [pre, stem])})
    corpus.word_of = {8003003: {0: [pre, stem], 1: [pre, stem]}}
    gold = _gold(8003003, {("H7364", 0): [0, 1]})
    gold[8003003].unit[("H7364", 0)] = "word"
    st = collections.Counter()
    rows = gf.rows_from_gold(gold, corpus, "manual", {}, st)
    assert len(rows) == 1 and rows[0]["h_idx"] == 1 and json.loads(rows[0]["extra"]) == {"word_h_idx": [0, 1]}
    assert st["links_word_level_by_source_unit"] == 1
    gold[8003003].unit[("H7364", 0)] = "morpheme"                                   # stated as part of a word: kept as it is
    rows = gf.rows_from_gold(gold, corpus, "manual", {}, collections.Counter())
    assert rows[0]["extra"] is None
