"""E1 (plan internal-docs/aim1-three-track-evaluation-plan.md §2.6): the same-gold-type leave-one-out
harness every carryover claim must clear before a mechanism is extended to gold-less languages.

Question it answers: given a mechanism M (stemming, span_extension, ...) whose effect was MEASURED against
gold on some languages, can a gold-FREE predictor predict a held-out language's measured verdict from the
OTHER languages' verdicts? Predictors are `predict(item, train_items) -> bool | None` (None = abstain).

Two rules baked in, both from real earlier failures in this project:
  * PER-GOLD-TYPE. An earlier leave-one-out that mixed gold types (Swedish's lexicon gold against four
    positional Clear golds) was uninterpretable, not a verdict. `loo_report` therefore scores each gold type
    on its own (train fold = the same type only) and labels the pooled number MIXED.
  * FAIR THRESHOLD. The tokens/type rule's cut is RE-FITTED ON THE TRAIN FOLD ONLY, direction included
    (stemming helps at LOW ratio, span_extension at HIGH ratio — the harness learns which). The earlier
    "14/15" in plan §2.3 was in-sample (threshold calibrated on the very points it scored) and flagged as an
    upper bound; this module gives the honest number.
Items of the same iso never appear in each other's train fold (hin has a Clear and a Door43 spanext item).

    python3 -m lexeme_aligner.eval.carryover_eval [--mechanism stemming|spanext] [--no-artefacts] [--folds]
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

VERDICTS_FILE = Path("config/carryover/verdicts.json")
GRAMBANK_FILE = Path("config/grambank/features.json")
LANGUAGES_DB = Path("/home/lgunnars/dev/bcv-commons/bcv-query/resources/languages/languages.db")
GOLD_TYPES = ("clear", "door43", "helfi", "sword")
MIN_SHARED_FEATURES = 20      # a Grambank nearest neighbour needs at least this many shared 0/1 features


@dataclass
class Item:
    mechanism: str
    iso: str
    gold_type: str
    verdict: bool
    delta: float | None = None
    features: dict = field(default_factory=dict)
    artefact: bool = False
    edition: str = ""
    note: str = ""


def load_items(mechanism: str, path: Path = VERDICTS_FILE, include_artefacts: bool = True) -> list[Item]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for r in doc["mechanisms"][mechanism]["items"]:
        if r.get("artefact") and not include_artefacts:
            continue
        out.append(Item(mechanism=mechanism, iso=r["iso"], gold_type=r["gold_type"], verdict=bool(r["verdict"]),
                        delta=r.get("delta"), features={"tokens_per_type": r.get("tokens_per_type")},
                        artefact=bool(r.get("artefact")), edition=r.get("edition", ""), note=r.get("note", "")))
    return out


# --- reference data (read-only) -----------------------------------------------------------------------
_grambank_cache: dict[str, dict[str, dict]] = {}
_branch_cache: dict[tuple[str, str], dict[str, str | None]] = {}


def load_grambank(path: Path = GRAMBANK_FILE) -> dict[str, dict]:
    key = str(path)
    if key not in _grambank_cache:
        p = Path(path)
        _grambank_cache[key] = json.loads(p.read_text(encoding="utf-8")).get("languages", {}) if p.exists() else {}
    return _grambank_cache[key]


def glottolog_level(isos: list[str], level: str = "branch", db: Path = LANGUAGES_DB) -> dict[str, str | None]:
    """{iso: value of the Glottolog `language` table column `level` ('branch' or 'group_') or None}."""
    key = (str(db), level)
    cache = _branch_cache.setdefault(key, {})
    missing = [i for i in isos if i not in cache]
    if missing:
        if not Path(db).exists():
            for i in missing:
                cache[i] = None
        else:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                for i in missing:
                    row = con.execute(f"select {level} from language where iso639_3=?", (i,)).fetchone()
                    cache[i] = row[0] if row and row[0] else None
            finally:
                con.close()
    return {i: cache[i] for i in isos}


# --- predictors ---------------------------------------------------------------------------------------
def _majority(verdicts: list[bool]) -> bool | None:
    t = sum(verdicts)
    f = len(verdicts) - t
    if t == f:
        return None
    return t > f


def _train_excluding(item: Item, train: list[Item]) -> list[Item]:
    return [t for t in train if t.iso != item.iso]


def predict_majority(item: Item, train: list[Item]) -> bool | None:
    """(d) trivial baseline: the majority verdict of the train fold (exact tie -> abstain)."""
    return _majority([t.verdict for t in _train_excluding(item, train)])


def make_glottolog_predictor(level: str = "branch", db: Path = LANGUAGES_DB) -> Callable:
    """(a) majority verdict of train items sharing the held-out item's Glottolog `level`; abstain if none/tie."""
    def predict(item: Item, train: list[Item]) -> bool | None:
        tr = _train_excluding(item, train)
        lv = glottolog_level([item.iso] + [t.iso for t in tr], level, db)
        mine = lv[item.iso]
        if mine is None:
            return None
        return _majority([t.verdict for t in tr if lv[t.iso] == mine])
    predict.__name__ = f"glottolog_{level}"
    return predict


def _agreement(a: dict, b: dict) -> tuple[float | None, int]:
    keys = [k for k in a if k in b and a[k] in ("0", "1") and b[k] in ("0", "1")]
    if not keys:
        return None, 0
    return sum(a[k] == b[k] for k in keys) / len(keys), len(keys)


def make_grambank_nn_predictor(path: Path = GRAMBANK_FILE) -> Callable:
    """(b) verdict of the train item whose Grambank feature vector agrees most with the held-out language's
    (>= MIN_SHARED_FEATURES shared 0/1 features); abstain if the language or every neighbour lacks Grambank,
    or if the best-agreeing neighbours disagree on the verdict."""
    def predict(item: Item, train: list[Item]) -> bool | None:
        gb = load_grambank(path)
        mine = gb.get(item.iso)
        if not mine:
            return None
        best, verdicts = -1.0, []
        for t in _train_excluding(item, train):
            other = gb.get(t.iso)
            if not other:
                continue
            sim, n = _agreement(mine, other)
            if sim is None or n < MIN_SHARED_FEATURES:
                continue
            if sim > best + 1e-12:
                best, verdicts = sim, [t.verdict]
            elif abs(sim - best) <= 1e-12:
                verdicts.append(t.verdict)
        if not verdicts:
            return None
        return verdicts[0] if len(set(verdicts)) == 1 else None
    predict.__name__ = "grambank_nn"
    return predict


def fit_threshold(train: list[Item], feature: str = "tokens_per_type") -> tuple[str, float] | None:
    """Best single-cut rule on TRAIN ONLY: ('le'|'ge', t) meaning "verdict True iff value <= t (>= t)".
    Candidate cuts are midpoints between adjacent sorted train values; ties on train accuracy prefer 'le' then
    the lower cut (deterministic). None if fewer than 2 usable items."""
    pts = sorted((t.features.get(feature), t.verdict) for t in train if t.features.get(feature) is not None)
    if len(pts) < 2:
        return None
    vals = sorted({v for v, _ in pts})
    cuts = [(a + b) / 2 for a, b in zip(vals, vals[1:])] or [vals[0]]
    best = None
    for direction in ("le", "ge"):
        for c in cuts:
            correct = sum(((v <= c) if direction == "le" else (v >= c)) == y for v, y in pts)
            if best is None or correct > best[0]:
                best = (correct, direction, c)
    return best[1], best[2]


def make_threshold_predictor(feature: str = "tokens_per_type") -> Callable:
    """(c) the tokens/type rule with the cut RE-FITTED ON THE TRAIN FOLD each call (fair LOO). If the train fold
    is single-class the rule degenerates to that class (its own honest prediction)."""
    def predict(item: Item, train: list[Item]) -> bool | None:
        v = item.features.get(feature)
        if v is None:
            return None
        tr = _train_excluding(item, train)
        if tr and len({t.verdict for t in tr}) == 1:
            return tr[0].verdict
        fit = fit_threshold(tr, feature)
        if fit is None:
            return None
        direction, c = fit
        return (v <= c) if direction == "le" else (v >= c)
    predict.__name__ = f"threshold_{feature}"
    return predict


def default_predictors() -> dict[str, Callable]:
    return {
        "majority": predict_majority,
        "glottolog_branch": make_glottolog_predictor("branch"),
        "glottolog_group": make_glottolog_predictor("group_"),
        "grambank_nn": make_grambank_nn_predictor(),
        "tokens_per_type": make_threshold_predictor("tokens_per_type"),
    }


# --- the harness --------------------------------------------------------------------------------------
@dataclass
class Fold:
    predictor: str
    group: str            # a gold type, or 'MIXED'
    iso: str
    gold_type: str
    truth: bool
    pred: bool | None


def loo_folds(items: list[Item], predictors: dict[str, Callable], group: str) -> list[Fold]:
    """Leave-one-out over `items` restricted to `group` (a gold type = its own train fold only; 'MIXED' = all)."""
    pool = items if group == "MIXED" else [i for i in items if i.gold_type == group]
    folds = []
    for held in pool:
        train = [i for i in pool if i is not held]
        for name, fn in predictors.items():
            folds.append(Fold(name, group, held.iso, held.gold_type, held.verdict, fn(held, train)))
    return folds


def summarize(folds: list[Fold]) -> dict:
    """{(group, predictor): {n, answered, abstained, correct, acc}} — acc over ANSWERED folds only."""
    agg: dict = collections.defaultdict(lambda: {"n": 0, "answered": 0, "correct": 0})
    for f in folds:
        a = agg[(f.group, f.predictor)]
        a["n"] += 1
        if f.pred is not None:
            a["answered"] += 1
            a["correct"] += (f.pred == f.truth)
    out = {}
    for k, a in agg.items():
        out[k] = {**a, "abstained": a["n"] - a["answered"],
                  "acc": (a["correct"] / a["answered"]) if a["answered"] else None}
    return out


def loo_report(items: list[Item], predictors: dict[str, Callable] | None = None, by_gold_type: bool = True,
               show_folds: bool = False, stream=None) -> dict:
    """Print (and return) the per-gold-type + MIXED leave-one-out accuracy of every predictor, plus — for a
    human to hand-check — the per-fold table when `show_folds`. A gold type with < 3 items is listed but its
    accuracy is marked UNINFORMATIVE (n too small to read as evidence)."""
    stream = stream or sys.stdout
    predictors = predictors or default_predictors()
    groups = ([g for g in GOLD_TYPES if any(i.gold_type == g for i in items)] if by_gold_type else []) + ["MIXED"]
    all_folds: list[Fold] = []
    for g in groups:
        all_folds += loo_folds(items, predictors, g)
    summ = summarize(all_folds)
    sizes = {g: (len(items) if g == "MIXED" else sum(i.gold_type == g for i in items)) for g in groups}
    names = list(predictors)
    print(f"{'group':8} {'n':>3} " + " ".join(f"{p:>22}" for p in names), file=stream)
    for g in groups:
        cells = []
        for p in names:
            s = summ[(g, p)]
            acc = "abstain" if s["acc"] is None else f"{s['correct']}/{s['answered']}"
            if s["abstained"] and s["answered"]:
                acc += f" (+{s['abstained']} abst)"
            cells.append(f"{acc:>22}")
        flag = "  <- MIXED gold types: not a clean verdict" if g == "MIXED" else (
            "  <- n<3: UNINFORMATIVE" if sizes[g] < 3 else "")
        print(f"{g:8} {sizes[g]:>3} " + " ".join(cells) + flag, file=stream)
    if show_folds:
        for g in groups:
            print(f"\n-- folds [{g}] --", file=stream)
            print(f"{'iso':5} {'gold':7} {'truth':>6} " + " ".join(f"{p:>22}" for p in names), file=stream)
            byiso = collections.defaultdict(dict)
            for f in all_folds:
                if f.group == g:
                    byiso[(f.iso, f.gold_type, f.truth)][f.predictor] = f.pred
            for (iso, gt, truth), preds in byiso.items():
                cells = [f"{'-' if preds[p] is None else ('T' if preds[p] else 'F') + ('' if preds[p] == truth else ' WRONG'):>22}"
                         for p in names]
                print(f"{iso:5} {gt:7} {'T' if truth else 'F':>6} " + " ".join(cells), file=stream)
    return {"summary": summ, "folds": all_folds, "sizes": sizes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mechanism", choices=("stemming", "spanext"), action="append",
                    help="repeatable; default both")
    ap.add_argument("--verdicts", type=Path, default=VERDICTS_FILE)
    ap.add_argument("--no-artefacts", action="store_true",
                    help="drop items flagged artefact (fra spanext); default reports BOTH with and without")
    ap.add_argument("--folds", action="store_true", help="print the per-fold table")
    a = ap.parse_args(argv)
    for mech in a.mechanism or ["stemming", "spanext"]:
        variants = [("no artefacts", False)] if a.no_artefacts else [("with artefacts", True), ("no artefacts", False)]
        for label, inc in variants:
            items = load_items(mech, a.verdicts, include_artefacts=inc)
            if label == "no artefacts" and len(items) == len(load_items(mech, a.verdicts, True)) and not a.no_artefacts:
                continue          # nothing flagged for this mechanism: the two variants are identical
            print(f"\n=== {mech} ({label}) — {len(items)} items, LOO accuracy over ANSWERED folds ===")
            loo_report(items, show_folds=a.folds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
