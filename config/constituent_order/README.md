---
license: cc0-1.0
tags:
- typology
- word-order
- multilingual
- bible
---

# constituent-order-profile

Per-language constituent-order statistics — how a language reorders Hebrew's clause-level syntax
(the seven MACULA Hebrew `phrase_role` labels: `v` verb, `s` subject, `o` object, `o2` second object,
`p` non-verbal predicate, `pp` clause-level prepositional phrase, `adv` adverbial), measured directly
from alignment data, not asserted from grammar references. Every language aligns to the same Hebrew
source, so each aligned OT verse is a small parallel-order observation; aggregated over tens of thousands
of verses per language this yields real, per-language word-order fingerprints — validated against known
typology (Arabic preserves Hebrew's verb-first `v>s` order 91% of the time; English keeps it only 40%, i.e.
flips it to subject-first 60% of the time; Indonesian sits at 50%, consistent with its verb-initial narrative
register; Hindi keeps `v>s` 33% and `v>o` 39%, the signature of a verb-final language).

`pair_order_kept`: for each ADJACENT source-order constituent pair (a before b in the Hebrew), the share of
aligned verses where the target preserved that order. `function_drift`: mean normalized target-position minus
source-position per constituent label — which constituents a language systematically fronts or defers (the
key keeps its historical name; the labels are the `phrase_role` values above). Both are computed only from
cells with >=30 observations (see `constituent_order.py`). A constituent is a maximal run of consecutive
source tokens with the same clause (the verb they depend on) and `phrase_role`; its target position is the
mean of its aligned content tokens. Each file carries `label_scheme: "macula_phrase_role"`.

**Label vocabulary changed 2026-10-04.** Earlier versions of these files used the ETCBC BHSA `function`
labels (`Subj`, `Pred`, `Objc`, ...). BHSA is licensed CC BY-NC-SA, so the data was re-derived from the
CC BY 4.0 MACULA treebank columns only and the BHSA-labelled files were withdrawn. The two vocabularies are
not interchangeable (7 labels instead of 14+), but the shared pairs agree closely (English verb-then-subject
kept 0.404 under BHSA, 0.403 under MACULA).

OT-only (the MACULA phrase layer is Hebrew-only). **CC0-1.0** — derived alignment statistics, no source text
redistributed.
