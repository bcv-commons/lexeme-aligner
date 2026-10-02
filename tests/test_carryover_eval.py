"""carryover_eval.py (E1 leave-one-out harness): offline — synthetic items, a throwaway Glottolog sqlite and
Grambank json in tmp_path; the one real-data test only checks config/carryover/verdicts.json's integrity."""
import io
import json
import sqlite3
from pathlib import Path

import lexeme_aligner.carryover_eval as ce
from lexeme_aligner.carryover_eval import Item


def item(iso, verdict, ratio=None, gold="clear", mech="m", artefact=False):
    return Item(mechanism=mech, iso=iso, gold_type=gold, verdict=verdict,
                features={"tokens_per_type": ratio}, artefact=artefact)


# --- fit_threshold -------------------------------------------------------------------------------------

def test_fit_threshold_learns_direction_le():
    train = [item("a", True, 2), item("b", True, 4), item("c", False, 10), item("d", False, 20)]
    direction, cut = ce.fit_threshold(train)
    assert direction == "le" and 4 < cut < 10


def test_fit_threshold_learns_direction_ge():
    train = [item("a", False, 2), item("b", False, 4), item("c", True, 10), item("d", True, 20)]
    direction, cut = ce.fit_threshold(train)
    assert direction == "ge" and 4 < cut < 10


def test_fit_threshold_needs_two_points():
    assert ce.fit_threshold([item("a", True, 1)]) is None
    assert ce.fit_threshold([item("a", True, None), item("b", False, None)]) is None


def test_fit_threshold_is_deterministic_on_ties():
    train = [item("a", True, 1), item("b", False, 2)]
    assert ce.fit_threshold(train) == ce.fit_threshold(list(reversed(train)))


# --- the FAIR threshold predictor never sees the held-out point -----------------------------------------

def test_threshold_predictor_refits_on_train_fold_only():
    pred = ce.make_threshold_predictor()
    # train says "low ratio wins, cut ~ 7.5"; the held-out point (ratio 9, truth True) is what an in-sample
    # cut would have absorbed — the fair predictor must call it False (wrong), not cheat.
    train = [item("a", True, 2), item("b", True, 5), item("c", False, 10), item("d", False, 15)]
    held = item("h", True, 9)
    assert pred(held, train) is False


def test_threshold_predictor_single_class_train_degenerates_to_that_class():
    pred = ce.make_threshold_predictor()
    train = [item("a", False, 2), item("b", False, 5)]
    assert pred(item("h", True, 3), train) is False


def test_threshold_predictor_abstains_without_feature():
    pred = ce.make_threshold_predictor()
    assert pred(item("h", True, None), [item("a", True, 1), item("b", False, 9)]) is None


# --- majority baseline / same-iso exclusion ------------------------------------------------------------

def test_majority_predictor_and_tie_abstains():
    assert ce.predict_majority(item("h", True), [item("a", True), item("b", True), item("c", False)]) is True
    assert ce.predict_majority(item("h", True), [item("a", True), item("b", False)]) is None


def test_same_iso_items_never_appear_in_the_train_fold():
    # hin has two items (two gold types); holding one out must not let its twin leak in as evidence.
    held = item("hin", True, gold="clear")
    twin = item("hin", True, gold="door43")
    others = [item("x", False), item("y", False)]
    assert ce.predict_majority(held, [twin] + others) is False


# --- Glottolog / Grambank predictors --------------------------------------------------------------------

def _glottolog_db(tmp_path, rows):
    db = tmp_path / "languages.db"
    con = sqlite3.connect(db)
    con.execute("create table language (iso639_3 text, group_ text, branch text)")
    con.executemany("insert into language values (?,?,?)", rows)
    con.commit()
    con.close()
    return db


def test_glottolog_branch_majority_and_abstain(tmp_path):
    db = _glottolog_db(tmp_path, [("a", "G", "X"), ("b", "G", "X"), ("c", "G", "X"), ("h", "G", "X"),
                                   ("z", "G", "Z"), ("q", "G", None)])
    pred = ce.make_glottolog_predictor("branch", db)
    train = [item("a", True), item("b", True), item("c", False), item("z", False)]
    assert pred(item("h", False), train) is True            # same-branch X: 2 True vs 1 False
    assert pred(item("q", False), train) is None            # no branch recorded -> abstain
    assert pred(item("unknown", False), train) is None      # not in the db at all -> abstain
    assert pred(item("z2", False), train) is None           # (not in db) same


def test_glottolog_group_level_is_coarser_than_branch(tmp_path):
    db = _glottolog_db(tmp_path, [("kan", "SD", "SD-I"), ("tel", "SD", "SD-II")])
    assert ce.make_glottolog_predictor("branch", db)(item("kan", True), [item("tel", True)]) is None
    assert ce.make_glottolog_predictor("group_", db)(item("kan", True), [item("tel", True)]) is True


def _grambank(tmp_path, langs):
    fp = tmp_path / "features.json"
    fp.write_text(json.dumps({"languages": langs}), encoding="utf-8")
    ce._grambank_cache.clear()
    return fp


def test_grambank_nn_picks_nearest_and_requires_enough_shared_features(tmp_path):
    ones = {f"GB{i:03d}": "1" for i in range(30)}
    near = dict(ones); near["GB000"] = "0"                     # 29/30 agreement with `ones`
    far = {k: ("0" if i % 2 else "1") for i, k in enumerate(ones)}   # ~50% agreement
    tiny = {"GB000": "1", "GB001": "1"}                         # < MIN_SHARED_FEATURES shared
    fp = _grambank(tmp_path, {"h": ones, "near": near, "far": far, "tiny": tiny})
    pred = ce.make_grambank_nn_predictor(fp)
    train = [item("near", True), item("far", False), item("tiny", False)]
    assert pred(item("h", False), train) is True
    assert pred(item("nogb", False), train) is None
    assert pred(item("h", False), [item("tiny", False)]) is None   # only a too-sparse neighbour


def test_grambank_nn_abstains_when_tied_neighbours_disagree(tmp_path):
    ones = {f"GB{i:03d}": "1" for i in range(30)}
    fp = _grambank(tmp_path, {"h": ones, "a": dict(ones), "b": dict(ones)})
    pred = ce.make_grambank_nn_predictor(fp)
    assert pred(item("h", True), [item("a", True), item("b", False)]) is None
    assert pred(item("h", True), [item("a", True), item("b", True)]) is True


# --- the harness: per-gold-type separation --------------------------------------------------------------

def test_loo_folds_restrict_train_to_the_same_gold_type():
    seen = {}

    def spy(held, train):
        seen[held.iso] = sorted(t.gold_type for t in train)
        return None
    items = [item("a", True, gold="clear"), item("b", True, gold="clear"), item("c", False, gold="door43"),
             item("d", False, gold="door43"), item("e", False, gold="door43")]
    ce.loo_folds(items, {"spy": spy}, "clear")
    assert seen == {"a": ["clear"], "b": ["clear"]}          # door43 items never reach a clear fold
    seen.clear()
    ce.loo_folds(items, {"spy": spy}, "MIXED")
    assert set(seen["a"]) == {"clear", "door43"}


def test_summarize_counts_abstentions_out_of_accuracy():
    folds = [ce.Fold("p", "clear", "a", "clear", True, True), ce.Fold("p", "clear", "b", "clear", True, False),
             ce.Fold("p", "clear", "c", "clear", True, None)]
    s = ce.summarize(folds)[("clear", "p")]
    assert (s["n"], s["answered"], s["abstained"], s["correct"]) == (3, 2, 1, 1)
    assert s["acc"] == 0.5


def test_loo_report_labels_mixed_and_flags_tiny_groups():
    items = [item("a", True, 2, "clear"), item("b", True, 3, "clear"), item("c", False, 20, "clear"),
             item("d", True, 2, "sword")]
    buf = io.StringIO()
    out = ce.loo_report(items, {"majority": ce.predict_majority}, stream=buf)
    text = buf.getvalue()
    assert "MIXED gold types: not a clean verdict" in text
    assert "n<3: UNINFORMATIVE" in text                      # the lone sword item
    assert out["sizes"] == {"clear": 3, "sword": 1, "MIXED": 4}


def test_loo_report_fold_table_marks_wrong_calls():
    items = [item("a", True, 2), item("b", True, 3), item("c", False, 20)]
    buf = io.StringIO()
    ce.loo_report(items, {"majority": ce.predict_majority}, show_folds=True, stream=buf)
    assert "WRONG" in buf.getvalue()                          # holding out c: majority says True, truth False


# --- verdict data integrity (real file) ------------------------------------------------------------------

def test_verdicts_file_integrity():
    for mech in ("stemming", "spanext"):
        items = ce.load_items(mech)
        assert len(items) == 15
        assert all(i.gold_type in ce.GOLD_TYPES for i in items)
        assert all(i.features["tokens_per_type"] and i.features["tokens_per_type"] > 0 for i in items)
    assert [i.iso for i in ce.load_items("spanext") if i.artefact] == ["fra"]
    assert not any(i.artefact for i in ce.load_items("stemming"))
    assert len(ce.load_items("spanext", include_artefacts=False)) == 14


def test_stemming_verdicts_match_their_threshold():
    doc = json.loads(Path(ce.VERDICTS_FILE).read_text(encoding="utf-8"))["mechanisms"]["stemming"]
    for r in doc["items"]:
        assert r["verdict"] == (r["delta"] >= doc["threshold_delta"]), r["iso"]
