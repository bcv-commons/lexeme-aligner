"""Step 3 (A) of internal-docs/aim1-typology-source-structure-plan.md — fertility priors for eflomal.

WHY THIS EXISTS: §2.1 of the plan found the biggest measured gap in the whole chain is not WHERE a
source token's target rendering sits (Steps 1/2/1.5 all refine that) but HOW MANY target words it
should claim at all — gold averages 2.18 target words per source content token and is multi-word
76.6% of the time, while `span_extension` is capped at +1 and its own stopword gate blocks 85.2% of
legitimate span members (gapfill.py's own docstrings). eflomal's C core already supports a per-source-
TYPE fertility prior (`FERF <srcword> <fert> <alpha>`, `.venv/.../eflomal/__init__.py:read_priors`) —
we have simply never sent one; `eflomal_align.py` writes only `LEX` lines. This module builds that
missing FERF input from signals THIS PIPELINE ALREADY HAS (Step 1's per-occurrence `rela`/`case_`/
`person` flags + Step 2's typology directions), the same "hard source signal x typology direction"
shape that already won in `span_extension.py` — but as a SOFT prior handed to eflomal's own statistical
model, not a deterministic grab, so it can succeed even in cases (Bengali/Assamese pronoun-stealing,
Hindi's multi-member construct chains) where the deterministic span_extension version measured harm:
eflomal still decides which nearby word to link, using its own co-occurrence evidence, we only nudge
its EXPECTED fertility for the source type upward when there's a language-typology reason to expect one.

RELATIONS (per plan): `possessor`, `dative`, `definite`, `finite_verb` — each counted for a token only
when the RELEVANT typology signal says a language actually needs a free word for it (a language with
no free adposition side has no reason to add fertility for a genitive/dative marker it renders some
other way, e.g. morphological case alone):
  - `possessor` — Hebrew `rela == "rec"` OR Greek `case_ == "genitive"` — counted only when the
    language has a resolved `adposition` direction (Grambank GB074/075, or Step 2's typology
    fallback) — the same existence signal `case_marking`'s direction uses in span_extension.py.
  - `dative` — Greek `case_ == "dative"` — same adposition-existence gate as possessor (this
    pipeline's own convention: Step 1's `dative` role uses `case_marking_direction` too).
  - `definite` — `compute_definite`'s derived signal (span_extension.py; article-token precedence /
    assimilated-article table / proper-name inheritance) — counted only when the language has a
    resolved `article` direction (GB020-023, or the typology fallback).
  - `finite_verb` — the token's own `person` field is populated (a verb whose inflection marks
    person) — counted only when the language's Grambank `subject_indexing` (GB089/090) is
    "incomplete" (neither suffix/enclitic nor prefix/proclitic subject-marking is attested), the
    signal `grambank_fetch.py`'s own docstring already names as the eng/arb subject-word-count spread
    (+1.41 eng / +0.21 arb). NOTE: `span_extension`'s own deterministic `subject_indexing` TRIGGER
    (a candidate-grab) was tried and measured a WASH (see that module's docstring) — this is a
    different, softer use of the exact same underlying signal, feeding eflomal's own model instead of
    grabbing a word ourselves, so the prior finding does not preclude this one working.

FORMULA (per anchor type `a`, the same string `EflomalAligner` keys its source line on):
  `n_a`       = total occurrences of `a` in the corpus being aligned.
  `k_{a,r}`   = occurrences of `a` carrying relation `r`, counted ONLY when `r`'s typology gate is
                open for this language (else always 0 — no fabricated evidence for a relation the
                language has no marked way to render).
  `increments`  = number of DISTINCT relations with `k_{a,r} > 0` for this anchor (0..4) — the plan's
                own "+1 per relation" language; summed as a count of relations that fired at all, not
                as a sum of occurrence counts (a frequent anchor is not entitled to a higher target
                fertility just because it has MORE occurrences of the same one relation).
  `f`         = `1 + increments`, clamped to eflomal's own fertility array bound (0..7 -> clamp <= 7).
  `alpha`     = `lam * sum(k_{a,r} for r with k_{a,r} > 0)` — the plan's `lam * k_{a,r}` generalized to
                a sum across relations when more than one applies to the same anchor (the plan's own
                worked formula only ever names one `r`); capped at `n_a` so the prior's pseudo-count
                can never outweigh the anchor's own real occurrence evidence (an alpha of 100 on a
                5-occurrence type would pin its fertility regardless of what the corpus shows).
Anchors with `increments == 0` get NO FERF line at all (f would just be eflomal's own uniform default
of 1, i.e. a no-op prior not worth writing) — this is what makes `null_prior` (the "just more function
words" control, see `NULL_PRIOR_FERT`) meaningfully different from the real prior rather than the same
set of lines with the weight zeroed out.

`invert`: the placebo control from the plan's §Controls — apply the exact same weights to anchors that
would OTHERWISE have gotten f=1 (increments == 0), instead of the ones the real signals actually flag.
If the placebo moves F1 as much as the real prior, the effect is noise/regularization, not the signal;
if it doesn't, the real prior's placement is doing the work.

RESULTS (2026-09-24, whole Bible, real Clear gold, lambda swept {0.5,1,2} on hin per the plan then
FIXED at 2.0 for arb/eng). Noise floor established first (two baseline replicates per language,
AFTER fixing a real determinism bug found while building this — see eflomal_align.py's own
`_grow_diag_final_and` docstring): hin eflomal F1 spread 0.0003 (+gloss 0.0002), arb 0.0005 (+gloss
0.0003), eng 0.0002 (+gloss 0.0003) — tight enough for a clean win/wash call on all three.
  hin (lambda=2): eflomal F1 .5421->.5487 (Delta+0.0066, >>2x floor), exact_span +147.5 — WIN.
  eng (lambda=2): eflomal F1 .6114->.6150 (Delta+0.0036, >>2x floor), exact_span +286 — WIN.
  arb (lambda=2): eflomal F1 .87275->.8729 (Delta+0.00015, INSIDE the 0.0010 floor) — WASH, not a loss.
Both `+gloss` scoring conditions moved the same direction as `eflomal` alone for all three languages
(gloss itself is deterministic — BootstrapPriors reads static published data — so its own contribution
never varies between replicates; only eflomal's share of the union does).
CONTROLS (hin, lambda=2, same ~7125-anchor evidence scale): a typology-BLIND `build_null_prior` gives a
BIGGER F1 gain (+0.0098) than the real prior but with exact_span DOWN (-285.5) — pure recall inflation
that fails the plan's own "exact_span up" bar, exactly the failure mode that bar exists to catch. An
`invert` placebo (same weight, WRONG anchors) gives only +0.0013 F1, about 1/5 the real prior's gain,
with exact_span flat. Together these confirm the typology-conditioned PLACEMENT of the fertility signal
is doing the work — not "any FERF prior regularizes eflomal," and not "F1 alone would have told a
correct story here" (it would have picked the null prior as the bigger win).
SHIPPED as config-driven opt-in (`config/fertility_flags.json`, same "measure once, remember it, no
code edit" pattern as `span_extension.load_spanext_flags`): hin/eng `enabled=true, lambda=2.0`, arb
`enabled=false`. `run_pilot --fertility-priors` defaults to `None` (consult that file); an explicit
`--fertility-priors`/`--no-fertility-priors` always overrides it for a one-off experiment.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

from lexeme_aligner.grambank_fetch import FEATURES as GRAMBANK_FEATURES
from lexeme_aligner.span_extension import (DIRECTION_FEATURES, compute_definite, direction_for,
                                           load_grambank_raw)

FERT_CAP = 7
NULL_PRIOR_FERT = 2   # the "just more function words" control's flat target fertility (see build_null_prior)

_FERTILITY_FLAGS_FILE = Path("config/fertility_flags.json")


def load_fertility_flags(publish_iso: str, path: Path | None = None) -> dict:
    """{"enabled": bool, "lambda": float} for `publish_iso`, or `{}` if unmeasured — same "measure
    once, remember it in one line, no code edit" pattern as `span_extension.load_spanext_flags`/
    `config/gold_langs.json`/`config/typology/directions.json`. `run_pilot`'s own `--fertility-priors`/
    `--no-fertility-priors`/`--fertility-lambda` always override whatever is recorded here."""
    path = path or _FERTILITY_FLAGS_FILE
    if not Path(path).exists():
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = doc.get(publish_iso, {})
    out = {}
    if isinstance(entry.get("enabled"), bool):
        out["enabled"] = entry["enabled"]
    if isinstance(entry.get("lambda"), (int, float)):
        out["lambda"] = float(entry["lambda"])
    return out


def _has_adposition_side(grambank: dict[str, str], iso: str | None) -> bool:
    return direction_for(grambank, DIRECTION_FEATURES["case_marking"], iso=iso) is not None


def _has_article_side(grambank: dict[str, str], iso: str | None) -> bool:
    return direction_for(grambank, DIRECTION_FEATURES["articles"], iso=iso) is not None


def _subject_indexing_incomplete(grambank: dict[str, str]) -> bool:
    codes = GRAMBANK_FEATURES["subject_indexing"]
    return not any(grambank.get(c) == "1" for c in codes)


def build_fertility_priors(recs, publish_iso: str, lex_pos: dict[str, str], heb, anchor: str = "strong",
                           lam: float = 1.0, invert: bool = False, typology_fallback: bool = False
                          ) -> dict[str, tuple[int, float]]:
    """{anchor_string: (fert, alpha)} ready for `EflomalAligner.run(fertility_priors=...)`. `heb`: the
    same `HebrewSource` instance `build_corpus` was called with (for `assimilated_after_idx`, per
    `compute_definite`'s own signature). `typology_fallback`: use Step 2's WALS/lang2vec table when
    Grambank itself has nothing for this language (mirrors `span_extension`'s own flag; independent
    of that module's opt-in gating — this is a fresh, unmeasured mechanism with its own decision to
    make once real numbers are in)."""
    grambank = load_grambank_raw(publish_iso) or {}
    _iso = publish_iso if typology_fallback else None
    has_adp = _has_adposition_side(grambank, _iso)
    has_art = _has_article_side(grambank, _iso)
    subj_incomplete = _subject_indexing_incomplete(grambank)

    n: collections.Counter = collections.Counter()
    k: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in recs:
        definite = compute_definite(r.heb, lex_pos, heb.assimilated_after_idx(r.book, r.ch, r.v))
        for t in r.heb:
            a = getattr(t, anchor, None)
            if not a:
                continue
            n[a] += 1
            if has_adp and (t.rela == "rec" or t.case_ == "genitive"):
                k[a]["possessor"] += 1
            if has_adp and t.case_ == "dative":
                k[a]["dative"] += 1
            if has_art and definite.get(t.idx):
                k[a]["definite"] += 1
            if subj_incomplete and t.person:
                k[a]["finite_verb"] += 1

    flagged = {a for a, c in k.items() if any(v > 0 for v in c.values())}
    if invert:
        # Placebo: apply the SAME weight distribution to anchors the real signals never flagged,
        # paired off by descending frequency so the placebo's evidence scale matches the real run's.
        real_alphas = sorted((lam * sum(k[a].values()) for a in flagged), reverse=True)
        unflagged = sorted((a for a in n if a not in flagged), key=lambda a: -n[a])
        priors: dict[str, tuple[int, float]] = {}
        for a, alpha in zip(unflagged, real_alphas):
            priors[a] = (min(FERT_CAP, 2), min(alpha, n[a]))   # a fixed, arbitrary f=2 — no real signal to size it
        return priors

    priors = {}
    for a in flagged:
        increments = sum(1 for v in k[a].values() if v > 0)
        f = min(FERT_CAP, 1 + increments)
        alpha = min(n[a], lam * sum(v for v in k[a].values() if v > 0))
        priors[a] = (f, alpha)
    return priors


def build_null_prior(recs, anchor: str = "strong", lam: float = 1.0, fraction: float = 1.0
                     ) -> dict[str, tuple[int, float]]:
    """Control (ii) from the plan: "just align more function words", independent of any typology
    signal — flags a FIXED FRACTION of anchor TYPES (by descending frequency, so it touches the same
    kind of high-value real estate a genuine prior would) at a flat `NULL_PRIOR_FERT`, with alpha
    scaled the same way `build_fertility_priors` scales real evidence. Exists to tell apart "the
    typology-conditioned PLACEMENT of fertility priors helps" from "adding fertility priors ANYWHERE
    helps" — if this control moves F1 as much as the real prior, the win is regularization noise, not
    the typology signal."""
    n: collections.Counter = collections.Counter()
    for r in recs:
        for t in r.heb:
            a = getattr(t, anchor, None)
            if a:
                n[a] += 1
    ranked = sorted(n, key=lambda a: -n[a])
    cutoff = max(1, int(len(ranked) * fraction))
    return {a: (NULL_PRIOR_FERT, min(n[a], lam * n[a])) for a in ranked[:cutoff]}
