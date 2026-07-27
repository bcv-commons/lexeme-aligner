---
pretty_name: Lexeme-anchored alignments (surface → lexeme, Strong's-bridged)
language:
  - arb
  - asm
  - ben
  - eng
  - fra
  - hau
  - hin
  - ind
  - rus
  - spa
  - swe
  - swk
tags:
  - bible
  - word-alignment
  - lexeme
  - strongs
  - interlinear
task_categories:
  - translation
  - token-classification
license: cc0-1.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: iso=*/data.parquet
---

# lexeme-alignments — surface → original-language lexeme (Strong's-bridged)

For each language, the attested mapping from target **surface word-forms** → the original-language
**lexeme** they render, mined by the aligner. **Lexeme-anchored, provenance-honest, additive** — the
design principles are in [`docs/publishing-principles.md`](../docs/publishing-principles.md). One
language per partition, for consumption by **bcv-commons** and downstream tools.

> The `language:` list above tracks the published partitions; the authoritative list is always
> `manifest.json`.

## Original-language source editions

Every row's `lexeme` is anchored against ONE fixed original-language edition per testament — the same
one for every published language, so cross-language comparison is apples-to-apples:

| testament | edition | source |
|---|---|---|
| Hebrew (OT) | **WLC** (Westminster Leningrad Codex) | Clear-Bible `macula-hebrew@main` |
| Greek (NT) | **Nestle1904** | Clear-Bible `macula-greek@main` |

Combined into this project's `lexeme-spine.db` backbone (CC-BY-4.0, bcv-commons/shoresh's MACULA
export) — the source-side counterpart to each target language's own `base_text`.

## The anchor: lexeme, not Strong's
The anchor of record is the **MACULA lexeme** (`hbo:0430`, `grc:2316`) — the precise dictionary unit.
The bare **Strong's** number is coarser (it conflates homonyms and sense-splits — one Strong's rolls up
several lexemes) and is a pure, lossless *function* of `lexeme` — so it is **not stored** (dropped
2026-07: ~32% smaller Parquet, zero information lost). Derive it yourself (or use `scripts/strongs_view.py`,
below) — one exception below (`hebrew_lexeme_strong.json`) overrides the mechanical derivation for a
small, verified set of lexemes:
```python
def strong_of(lexeme: str) -> str:
    lang, num = lexeme.split(":", 1)
    digits = "".join(c for c in num if c.isdigit())          # strip any trailing augment letter
    return ("H" if lang == "hbo" else "G") + digits.zfill(4)  # e.g. "hbo:6498a" -> "H6498"
```

## Schema (per row)
| column | type | meaning |
|---|---|---|
| `surface` | string | target rendering, lowercased (content tokens; may be multi-word) |
| `lexeme` | string | **the anchor** — MACULA lexical id (`lang:augmented-strong`) |
| `method` | string | **which method attested this pair** — `eflomal` / `gloss` / `gapfill` |
| `base_text` | string | **which edition** the surface is from (e.g. `BSB`, `eng_ylt`) |
| `count` | int32 | times this (surface → lexeme) was aligned **in that method + edition** |
| `hi_conf` | float32 | fraction of this pair's alignments that were intersection-backed (score ≥ 0.9) |

`iso` is recovered from the Hive partition path (`iso=<iso>/`). Two honest provenance axes: `method`
(*how* aligned) and `base_text` (*which* edition).

**Not stored, both exact + lossless from the columns above** (measured: dropping them shrinks the
Parquet ~32% with zero information loss — the two derivations are independent, so drop either or both):
- **`strong`** — see above.
- **`share`** (P(lexeme|surface) within a (surface, method, base_text) group) — group rows by
  `(surface, method, base_text)`, sum their `count`, then `share = count / that sum`.

### It's an additive union — nothing is merged away
Rows are the **union of the methods**, each tagged with its `method`. A surface→lexeme attested by both
eflomal and gloss is **two rows** (eflomal ×N, gloss ×M) — full provenance, no winner-take-all merge.
This means:
- a **gapfill-only** fact says `method=gapfill` — it can never masquerade as eflomal/gloss-attested
  (`gapfill` is the lower-confidence coverage layer — model-free priors filling positions eflomal+gloss
  left uncovered; see `docs/publishing-principles.md` §3 for why this provenance is never hidden);
- an **enhanced translation** that renders one lexeme with many words keeps all of them — we never force
  a lexeme to a single "canonical" surface;
- `count`s are **per-method**, so *do not sum across methods* to get an occurrence total (the same verse
  is often aligned by more than one method — that would double-count; see the on-ramp script).

**Cross-method agreement** (a real confidence signal, same shape as cross-edition agreement below) —
group rows by `(surface, lexeme, base_text)` and count the *distinct* `method` values present. A fact
independently found by both `eflomal` and `gloss` (two structurally different methods — one statistical,
one dictionary-based) is stronger evidence than either alone. Concrete example from the published `fra`
partition: `(surface="a", lexeme="grc:2192", base_text="fraLSG")` has both a `method=gloss` row
(count=125) and a `method=eflomal` row (count=102) — two independent methods, same conclusion.

### Multiple editions of one language (pooling)
Some languages ship several editions pooled into one partition, each row tagged by `base_text`
(e.g. `eng` = BSB + YLT; `arb` = Van Dyck + New Arabic Version; `swe` = Folkbibeln + Kärnbibeln). This
is additive — every edition's renderings are kept, and:
- **single edition** → filter `base_text = '<edition>'`;
- **cross-edition agreement** (a strong confidence signal) → a surface→lexeme attested by **more than one**
  `base_text` is corroborated across independent translations; derive it by counting distinct `base_text`
  per (surface, lexeme). An enhanced/literal edition (e.g. YLT's `begat`/`begotten`) contributes its own
  renderings without overwriting the others.
- **takedown** → if a rights-holder objects, drop that `base_text`'s rows and republish (content-addressed);
  never re-emit provenance-stripped.

The `manifest.json` entry lists the pooled `base_texts`, per-edition row counts (`by_base_text`), and a
`sources` pointer per edition.

## Using the data — pick your operating point
Three independent signals; combine them. The dataset ships the full distribution rather than
pre-filtering, so precision / coverage / provenance are sliders **you** control:

| goal | filter |
|---|---|
| everything / max recall | all rows |
| **exclude the gapfill coverage layer** | `method != 'gapfill'` |
| one edition only | `base_text == '<edition>'` |
| cross-edition-corroborated | keep (surface, lexeme) with ≥2 distinct `base_text` |
| cross-method-corroborated | keep (surface, lexeme, base_text) with ≥2 distinct `method` |
| balanced (recommended default) | argmax-derived-`share` per (surface, method), `count ≥ 2` |
| high precision | `hi_conf ≥ 0.5`, `count ≥ 2` |
| one method only | `method == 'eflomal'` (or `gloss`) |
| treat with extra caution | `lexeme` is a key in `light_lexemes.json` (below) — see that section |

- **`share`** (derived, see above) → *which lexeme* (P(lexeme\|surface) within a method).
- **`hi_conf`** → *how reliable the placement* (intersection-backed share).
- **`count`** → *how much evidence* (`count: 1` rests on a single occurrence).
- **no recommended universal minimum** — `count: 1` rows are real (not noise-filtered away), just
  weaker evidence; if you need a floor, `count ≥ 2` is the ablation-tested "balanced" default above.

## Derived views (example scripts, never a second source of truth)
1. **Strong's on-ramp** — roll `lexeme→strong` from a single base method into a clean Strong's-keyed
   table (surface + frequency), for ecosystem tools:
   ```bash
   python3 scripts/strongs_view.py --iso swe                    # → out/strongs_view_swe.tsv
   python3 scripts/strongs_view.py --iso swe --hi-conf 0.5 --min-share 0.02
   ```
   It picks one base method (default `eflomal`) so per-method counts don't double-count, derives `strong`
   from `lexeme` (checking `hebrew_lexeme_strong.json` first — see below), then aggregates per
   (strong, surface) with `share = P(surface | strong)`.
2. **Merged best-pick** (optional, lossy, NOT reproducible from this dataset alone) — a single-answer-
   per-token convenience our own pipeline builds from the *per-occurrence* jsonl (not published — only
   the aggregated rows here are), using `contest_rule.json`'s disagreement-resolution rule:
   ```bash
   python3 -m lexeme_aligner.merge_align --iso swe --methods eflomal,gloss,gapfill \
     --contest-rule contest_rule.json      # → align_merged_swe_*.jsonl, then export --methods merged
   ```
   Labelled **lossy** on purpose — it drops valid alternatives; use it only when you want exactly one row.
   `contest_rule.json` is published here for transparency (see below) but its keys need per-occurrence
   data (eflomal's raw score, gloss's match-type) that this dataset's aggregated rows don't carry — you
   can't run this rule yourself on the Parquet alone, only approximate its spirit via `hi_conf`/`count`.

## Companion reference resources (root of this dataset, small + committed)
Four small JSON files sit alongside `manifest.json` — each is knowledge our own pipeline uses internally
that can't be *derived* from the row data itself (unlike `strong`/`share` above), so it's published
outright rather than left for every consumer to rediscover independently.

| file | keyed by | directly usable on this dataset's rows? |
|---|---|---|
| `light_lexemes.json` | `lexeme` | **yes** |
| `hebrew_lexeme_strong.json` | `lexeme` | **yes** |
| `greek_morph_strong.json` | `lexeme` + source grammar code | no — needs external morphology |
| `contest_rule.json` | eflomal score + gloss match-type | no — needs per-occurrence data |

- **`light_lexemes.json`** — `{lexeme: avg_target_dominance}`, ~545 entries. Source lexemes so
  semantically broad (light verbs like Hebrew הָיָה/"to be", Greek γίνομαι/"to become"; generic nouns)
  that no single target rendering dominates in *any* language — a single-method row for one of these is
  weaker evidence than for an ordinary content word. **Directly applicable**: if `row.lexeme` is a key
  here, prefer rows that are cross-method-corroborated (see above) over trusting a lone `gloss` row.
  Computed by `cross_lang_prior.py` from cross-lingual target-dominance across every aligned language.
- **`hebrew_lexeme_strong.json`** — `{lexeme: strong}`, a small, verified override for the handful of
  Hebrew lexemes where the mechanical `strong` derivation (above) would be wrong: our spine's own
  "equivalence-canonicalization" occasionally rolls two genuinely distinct lexemes onto one bare
  Strong's number and doesn't always pick the number in wider use (verified case: `hbo:4714` "Egypt"
  mechanically derives to `H4713` "Egyptian" — wrong; this table corrects it to `H4714`, matching
  Clear-Bible gold's own usage 1,633:55). **Directly applicable**: check this table BEFORE the mechanical
  derivation; only 1 entry currently, scoped to verified cases, not a blanket table (a broad, unscoped
  sweep for this was tried and produced nonsense — see `hebrew_lexeme_strong.py`'s docstring).
- **`greek_morph_strong.json`** — `{"lemma_strong|grammar_code": traditional_strong}`. Clear-Bible's gold
  uses the *traditional* Strong's Concordance numbering for irregular Greek verbs — separate numbers per
  tense/person (εἰμί: `G2258` imperfect, `G1526` present-3pl, etc.) — while `lexeme`'s mechanical rollup
  collapses them all to one lemma number (`G1510`). This table recovers the traditional number **if**
  you have the source occurrence's own morphological parse (e.g. `V-IIA-3S`) in the same coding
  convention as `globalbibletools/data`'s `hbo+grc` source (see `greek_morph_strong.py`) — this dataset's
  rows don't carry that, so it's not self-contained, but it's the exact table our own benchmark scoring
  uses, published for anyone doing source-morphology-aware work.
- **`contest_rule.json`** — `{"score <eflomal_score> | <gloss_match_type>": "ef"|"gl"}`. An empirically
  validated (leave-one-out tested across 10 gold languages), universal rule for which method's answer to
  trust when eflomal and gloss *disagree* on the same source token. Published for transparency about what
  the "merged best-pick" derived view (above) actually does — not directly runnable against this
  dataset's aggregated rows (see the table above), since both tier keys need per-occurrence values this
  dataset doesn't carry.

## Layout — why the bulk data isn't in git
```
lexeme-alignments/
  README.md                    # committed — this file
  manifest.json                 # committed — per-language metadata + content hash (the durable record)
  light_lexemes.json            # committed — see "Companion reference resources"
  hebrew_lexeme_strong.json     # committed — see "Companion reference resources"
  greek_morph_strong.json       # committed — see "Companion reference resources"
  contest_rule.json             # committed — see "Companion reference resources"
  iso=<iso>/                   # GIT-IGNORED — bulk data, published out-of-band (HF / object storage)
    data.parquet
```
`manifest.json` is git's small, diffable record of what exists and what it hashes to; each partition is
keyed by its `content_sha256`.

## Provenance & quality
Per-language provenance (methods present, per-method row counts, testament, counts, `hi_conf_ge_0.9`,
spine tags, content hash) lives in `manifest.json`. Every language is produced by the **same pipeline**,
validated against Clear-Bible manual gold **where it exists** — token-weighted top-1 of ~92–97%
(Strong's grain) / ~89–92% (lexeme grain — the anchor's headline; `docs/benchmark.md`). Languages without
usable gold (`ind`; `rus`, whose only manual reference is itself mis-aligned) run the **identical**
pipeline and are **not lower quality — simply un-cross-checked**. We do **not** stamp a verified/unverified
tier. Your confidence signal is the same for every language: the row-level `method` / `hi_conf` / `count`
(plus the derived `share` — see above). These are raw aligned counts, not hand-checked.

## Reproducibility (content-addressed)
The statistical aligner (eflomal) seeds from `/dev/urandom`, so regeneration varies ~1% run-to-run. This
is a **content-addressed release**: inputs are pinned (spine tags + each source text's `sha256`,
`data/pins/`), and each partition is fixed by its **`content_sha256`** in `manifest.json` — that hash
*is* the identity of what was released. Consume a specific release by its hash; a rebuild won't match
byte-for-byte.

## Authentication & publishing (one-time)
```bash
python3 -c "from huggingface_hub import login; login()"        # cached → ~/.cache/huggingface/token
python3 -m lexeme_aligner.export_lex --iso <iso> --lang-name <Name> \
  --publish bcv-commons/lexeme-alignments --create
```
Use a fine-grained write token scoped to the target dataset. The push uploads this language's partition +
the shared `manifest.json`/`README.md`/companion resource files (`light_lexemes.json`,
`hebrew_lexeme_strong.json`, `greek_morph_strong.json`, `contest_rule.json` — global, not per-language, but
small enough to just re-upload each time so they never drift out of sync); other languages' partitions are
untouched.

## License
**This catalogue is CC0-1.0.** It is *derived, factual* data — lexeme ids, Strong's rollups, alignment
counts, `share`/`hi_conf` statistics, method tags, and a de-arranged type-level list of word forms. It
does **not** reproduce the running text of any translation (no verse refs, no word order), so the
copyrightable expression of the sources is not present.

Each `surface` is nonetheless a word form from a **source translation**, and those keep their **own**
licenses. Every language's `manifest.json` entry carries a `source` **pointer**
(`provider`/`edition`/`license_url`) — follow it for the authoritative terms. Pointing to a source does
not by itself grant permission to derive from it; for any source whose terms restrict derivatives, obtain
that separately.
