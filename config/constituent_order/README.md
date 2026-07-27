---
license: cc0-1.0
tags:
- typology
- word-order
- multilingual
- bible
---

# constituent-order-profile

Per-language constituent-order statistics — how a language reorders Hebrew's phrase-level syntax
(BHSA `function`: Subject/Predicate/Object/Time/Location/Adjunct/Complement/...), measured directly
from alignment data, not asserted from grammar references. Every language aligns to the same Hebrew
source, so each aligned OT verse is a small parallel-order observation; aggregated over ~20-30k verses
per language this yields real, per-language word-order fingerprints — validated against known typology
(Arabic preserves Hebrew's verb-first `Pred>Subj` order 94% of the time; English flips it 56% of the
time to Subject-first; Indonesian sits near 50%, consistent with its verb-initial narrative register).

`pair_order_kept`: for each ADJACENT source-order phrase-function pair (a before b in the Hebrew), the
share of aligned verses where the target preserved that order. `function_drift`: mean normalized
target-position minus source-position per function type — which constituents a language systematically
fronts or defers. Both computed only from cells with >=30 observations (see `constituent_order.py`).

OT-only (BHSA has no Greek phrase layer). **CC0-1.0** — derived alignment statistics, no source text
redistributed.
