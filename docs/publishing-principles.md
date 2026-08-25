# Publishing principles — lexeme-anchored, provenance-honest, non-lossy

Design principles for how the aligned data is published. These are **contract-level** decisions: they
shape the schema, the "source of truth", and what we do (and deliberately do **not**) collapse. Written
during the v2 publishing-replan; supersedes the implicit "pick one method and export it" framing.

## The five principles

### 1. Lexeme is the anchor — one source of truth
The canonical published data is anchored on the **MACULA lexeme** (`hbo:0430`, `grc:2316`), not the
bare Strong's number. Strong's conflates homonyms and H/G and collapses sense-splits (one Strong's rolls
up several lexemes — e.g. `H1516` → 7 lexemes). The lexeme is the precise dictionary unit. There is
**one** canonical, lexeme-anchored dataset; every other view is *derived* from it, never a parallel
source. (The schema is already lexeme-primary; this makes it the stated foundation.)

### 2. Strong's is a first-class *bridge*, not the anchor — and it deserves an easy on-ramp
Strong's is the lingua franca of the Bible-software ecosystem; most tools and users key on it. So we
keep `strong` as a **rollup column** on every row, and we ship a **conversion/shaping helper** that
produces a clean Strong's-keyed view (surface ↔ Strong's, filtered/shaped) for people who just want
"the Strong's translations". Strong's stays *discoverable and convenient* — it is simply not the
**anchor of record**. Lexeme-precise consumers use `lexeme`; Strong's-ecosystem consumers use the
derived view. Nothing is hidden; the coarse key is a convenience, the fine key is the truth.

### 3. Be honest about source and process — carry provenance, including gapfill
Every datum must be traceable to **how it was produced**. A surface→lexeme mapping that exists only
because the **gapfill** fallback proposed it must say so — it must not masquerade as an
eflomal/gloss-attested fact. So provenance (`method`) and confidence (`hi_conf`, score) are **carried
on the data**, not summarized away. This is the existing honesty discipline (row-level confidence, the
senses takedown policy, no MARBLE sense labels) extended to method-provenance: the consumer can always
see *who said this and how sure they were* — and can exclude gapfill-only facts if they want.

### 4. Respect enhanced translations — many-to-many, never force-fit 1:1
Observed from the Swedish gold (Kärnbibeln/Folkbibeln): these are **enhanced/amplified** translations —
they freely **add words** and do **not** force one target word to carry all of a Hebrew/Greek lexeme's
meanings. The data model must honor this:
- a lexeme legitimately maps to **many** surfaces (and multi-word phrases → `publish/aligned_mwe`);
- a surface need not map back 1:1;
- `count`/`share` capture the **distribution**, and we **never** reduce a lexeme to a single "canonical"
  surface. Added/explanatory words are signal, not noise.
This is a positive design stance: the richness of an enhanced translation is preserved, not flattened.

### 5. Additive by default — do not run merge unnecessarily or remove words
Merging to a single winner per occurrence is **lossy**: it discards valid alternative renderings and
added words that principle 4 says we should keep. So the **canonical form is the additive union** of the
methods (each contribution kept and tagged), **not** a winner-take-all merge. A merged "best single
pick" view may still be offered as a *labelled, derived convenience* — but it is never the source of
truth, and we do not collapse the union just because a merge is available. When in doubt: **keep and
add, don't pick and drop.**

## What this means for the schema

Canonical dataset — `publish/lexeme-alignments` (implemented; live on HF as `bcv-commons/lexeme-alignments`):

| column | meaning | principle |
|---|---|---|
| `lexeme` | **the anchor** — MACULA `lang:augmented-strong` | 1 |
| `surface` | target rendering, lowercased (content; may be multi-word) | 4 |
| `method` | **which method attested this pair** (eflomal / gloss / gapfill) | 3, 5 |
| `base_text` | which edition attested this pair (pooled multi-edition languages) | 4 |
| `count` | times this (surface → lexeme) was aligned **by that method + edition** | 3, 4 |
| `hi_conf` | alignment reliability (intersection-backed share) | 3 |

`strong` (bridge key, principle 2) and `share` (sense distribution, principle 4) are **deliberately not
stored** — both are exact, lossless derivations from the columns above (`strong` from `lexeme` by
stripping the augment letter + zero-padding + H/G prefix; `share` = `count` normalized within a
`(surface, method, base_text)` group), so storing them would be pure duplication — measured ~32%
smaller Parquet with them dropped, zero information lost. `scripts/strongs_view.py` computes both on
demand for consumers who want them. (Superseded the earlier plan, once sketched here, to store `strong`
+ `share` as columns.)

Rows are **partitioned by `method`** (additive union, principle 5) instead of one pre-merged winner. A
surface→lexeme attested by both eflomal and gapfill is **two rows** (eflomal ×N, gapfill ×M) — nothing
merged away, full provenance. Consumers:
- **everything / max recall** → all rows;
- **exclude gapfill** → `method != gapfill`;
- **high precision** → `hi_conf ≥ x`, `count ≥ 2`;
- **single best pick** → the derived merged view (below), clearly labelled lossy.

## Derived views (never the source of truth)

1. **Strong's on-ramp** (principle 2) — a shaping helper: roll `lexeme`→`strong`, pick/aggregate per
   surface, emit a clean Strong's-keyed table for ecosystem tools. Ship as a small script + a documented
   recipe, so Strong's users get "the easy format" without us duplicating the source of truth.
2. **Merged best-pick** (principle 5, optional) — a single-answer-per-token convenience for consumers who
   want one row, produced by the contest-rule merge, **labelled lossy** and regenerable from the union.

## Status vs current implementation

- ✅ lexeme-anchored schema exists (`export_lex` is lexeme-primary).
- ✅ per-row confidence (`hi_conf`, `share`, `count`) exists.
- ✅ MWE (added-word) channel exists (`publish/aligned_mwe`), honoring principle 4.
- ✅ **method-provenance column emitted** — `export_lex.SCHEMA` includes `method` (and `base_text`) per
  row; the additive union across methods/editions (principles 3+5) is live, not single-`--method`.
- ✅ **naming** — dataset is `bcv-commons/lexeme-alignments`, card `pretty_name: Lexeme-anchored
  alignments (surface → lexeme, Strong's-bridged)`. `aligned_mwe`'s card still said "Strong's-aligned"
  as of 2026-08-25 (fixed locally in `publish/aligned_mwe/README.md`; needs a card re-push to take
  effect on HF — not yet done).
- ✅ **Strong's on-ramp script** — `pipeline/scripts/strongs_view.py` (principle 2), rolls
  `lexeme`→`strong` from a chosen base method, documented as a derived, non-authoritative reshape.

## Status of the former "open decisions"
- ✅ Dataset name: `publish/lexeme-alignments` / `bcv-commons/lexeme-alignments`.
- ✅ `share` semantics: within-`(method, base_text)`, computed on demand (not stored) — the "recommend"
  option above was adopted.
- Merged best-pick view: still a consumer-side recipe (`merge_align.py` + `--contest-rule`), not a
  separate published dataset — matches the "left as a recipe" option.
- ✅ Headline benchmark grain: `benchmark.py --grain lexeme` implemented; tool default left at `strong`
  for continuity with older gold comparisons (see 2026-07 session log in `CLAUDE.local.md`).
