# Architecture — what goes in, where it comes from, what uses it

**Status:** written 2026-10-21 as the base for a setup script for fresh clones. The five data structures the repo
produces (lex-lexicon, gram-struct, gram-align, full-align, edition-struct) are described in
[`data-structures.md`](data-structures.md); the full history and pins of every input are in
[`config/PROVENANCE.txt`](../config/PROVENANCE.txt). This page is the map between the two: **which external
resources the pipeline needs, where each one comes from, where it lives in a clone, and which step reads it.**

## 1. The pipeline in one picture

```
  original-language backbone                 target text (one language, every edition)
  lexeme-spine-macula.db  (Hebrew + Greek)   PKF · helloAO · DBT · bibles' own (openbible, crosswire)
            \                                        /
             \                                      v
              \                         1 ingest -> USJ, one folder per edition (pipeline/work/ingest-cache/usj-<tag>)
               \                                    |
                +----------------------> 2 eflomal  (statistical, both directions + GDFA)
                                         3 export eflomal-only lexicon (local)
   prior-pack ------------------------>  4 gloss     (priors bootstrapped from step 3)
   gram-struct + prior-pack ---------->  4b span extension
   gram-struct + prior-pack ---------->  5 gapfill   5b residual eflomal
                                         6 export final lexicon            -> publish/lexeme-alignments
                                         7 aligned_mwe                     -> publish/aligned_mwe
   ubs-senses.db -------------------->   8 senses_attested (OT)            -> publish/senses_attested
   canonical index + stopwords ------->  9 compact alignments per edition  -> publish/compact-alignments
   gold (Clear, BSB, Door43, ...) --->   9a full alignments                -> publish/full-alignments-manual[-sa]
                                         9b ledger entry                   -> config/pipeline_decisions.json

  grammar (separate, `lexeme-aligner grammar all`, run over many languages):
   Grambank · WALS · lang2vec · URIEL+ · Glottolog kin  +  our own alignments  ->  config/gram_struct/<iso>.json
```

Everything the chain writes is local. Publishing to Hugging Face is a separate step (`lexeme-aligner publish`,
dry run unless `--push`). The front door is `python -m lexeme_aligner <run|batch|grammar|publish|status|eval|text-strip>`;
`run <iso>` is `full_chain.py`, steps 1–3 are `onboard.py`.

## 2. Resources, grouped by what they are for

Columns: **where from** = the upstream; **in a clone** = what `git clone` already gives you (`tracked`) or what must
be fetched (`fetch`) or built (`build`); **read by** = the modules that use it.

### 2.1 The source-language backbone (required)

| resource | where from | in a clone | licence | read by |
|---|---|---|---|---|
| `pipeline/lexeme-spine-macula.db` — 607,255 Hebrew + Greek words, one row per token, MACULA lexeme ids (`hbo:0430`, `grc:2424`), morphology, `head_idx` / `phrase_role` / `construct_role`, superscription flags | built by **bcv-query** from Clear-Bible MACULA (WLC + Nestle 1904); download `https://files.qombi.com/files/lexeme-spine-macula.db` (110 MB, sha256 `f1003a2b…`) | **fetch** (`pipeline/*.db` is ignored) | CC BY 4.0 | `hebrew_source` (every alignment step), `macula_syntax`, `derive_typology`, `compact_align` (source index), `versification` (asserts `versification_ot == org`) |
| `pipeline/rp2018-spine.db`, `pipeline/tr-textus-receptus-spine.db` — alternative NT spines (Byzantine, Textus Receptus) | bcv-query | fetch, optional | public domain | only with `--spine-db` / `ALIGNER_SPINE_DB` |
| `pipeline/lexeme-spine.db`, `lexeme-spine-bhsa-baseline.db`, `spine.db` | older spines | **not needed** | BHSA columns are CC BY-NC-SA | private A/B baselines only; nothing published comes from them |

Path override: `ALIGNER_SPINE_DB`. The spine is the one input with no fallback.

### 2.2 Target texts (fetched per language at ingest)

Which editions a language has is decided from **bcv-commons/bibles' catalog**, a copy of which is tracked in
`config/dbt_catalog/` (`catalog-index.json`, `catalog-overlap.json`, `text-route.json`, `pin.json`; refresh with
`python -m lexeme_aligner.catalog_source --fetch`). `onboard.editions_for` turns it into the edition pool. Each source
letter in the catalog has its own adapter:

| letter | source | fetched from | adapter | needs |
|---|---|---|---|---|
| `p` | PKF (Proskomma) | `cdn.bibel.wiki/pkf/…` | `cdn_source.py` → `pipeline/pkf2usfm/export_usfm.mjs` → usfmtc | **Node** + `npm install` in `pipeline/pkf2usfm` |
| `h` | helloAO | `bible.helloao.org` (`complete.json` per translation) | `helloao_source.py` + vendored bibles converter | — |
| `d` | Digital Bible Platform (Bible Brain v4) | `4.dbt.io/api`, route per edition from `text-route.json` | `dbt_source.py` | **`BIBLE_API_KEY`** in `.env` |
| `o` | editions bcv-commons/bibles republishes | `cdn.bibel.wiki/openbible/…` | `openbible_source.py` | — |
| `c` | CrossWire SWORD modules republished by bibles (e.g. Vamvas) | `cdn.bibel.wiki/crosswire/…` | `openbible_source.py --root crosswire` | — |
| `e` | eBible editions (listed in the catalog since 2026-10-21) | — | **none yet**: `onboard` stops with "unknown source 'e'" | — |

Output: `pipeline/work/ingest-cache/usj-<tag>/` (ignored, regenerable) plus a tracked pin `config/pins/<tag>.json`
(sha256 of what was fetched, licence pointer). `config/sources.json` holds the licence pointer published with each
edition. Optional speed-up: `dbt_source` first looks for chapters already downloaded by the sibling **audio-sync**
repo (`AUDIO_SYNC_BB`, default `…/audio-sync/downloads/BB`) and writes newly fetched chapters back there; with no
sibling repo it simply uses the API.

Shared text rules come from **bcv-commons/bibles' verse-text package**, vendored unchanged in
`pipeline/vendor/bibles/` (tracked, CC0, version in `VERSION`): helloAO→USJ conversion, verse extraction, the
tokenizer (`bcv_tokenize.py`) and the text fingerprint. Never edit the copies; re-vendor. Per-edition text clean-up
(brackets, parentheses, inline notes) is tracked config: `config/text_strip_rules.json`, `config/inline_notes.json`.

### 2.3 Verse numbering (tracked, needed for every OT edition)

The spine is Hebrew-numbered (WLC); English-numbered and other editions are paired through
`versification.py` → spine (Hebrew) → KJV/English → the edition's scheme.

| resource | where from | in a clone | licence |
|---|---|---|---|
| `pipeline/vendor/versification/` — `schemes/{hebrew,lxx,rso,vul,orgw,catm}.tsv`, `*.multiverse.json`, `vrs/*.vrs`, `nt-variants.json`, `ot-variants.json` | bcv-commons/bibles' TVTMS-derived maps (`cdn.bibel.wiki/_vrs/`); `lxx.tsv` from bcv-query | tracked | CC BY 4.0, STEPBible.org (TVTMS) |
| `config/bibles_vrs/` — `dbt_vrs_index.json` (scheme per DBT/helloAO edition) and the raw `<scheme>-to-eng.json` maps | bibles | tracked | same |
| `config/versification.json` | ours (manual overrides) | tracked | — |

### 2.4 Priors and lexical resources (the chain reads these)

| resource | where from | in a clone | licence | read by |
|---|---|---|---|---|
| `pipeline/vendor/prior-pack/prior_pack.parquet` — one row per MACULA lexeme: `pos`, `translit`, `word_class`, `keyness`, `lxx_greek` / `lxx_hebrew` | HF dataset `bcv-commons/prior-pack` (bcv-query), sha256 `f41f6d4c…` | **fetch** (parquet ignored; `manifest.json` + README tracked as the pin) | CC BY-SA 4.0 (only the keyness null/not-null bit is used; nothing from it is published) | `bootstrap_priors` (gloss), `span_extension`, `gapfill`, `residual_align`, `target_stopwords`, `article_bound`, `constituent_order`, `derive_typology` |
| `pipeline/ubs-senses.db` — UBS dictionary senses bound to spine tokens (Hebrew; Greek in `grc_*` tables) | **built** by `python -m lexeme_aligner.ubs_senses --build` (and `ubs_greek_senses --build`) from the UBS JSON in `pipeline/work/ubs-dictionary/`, downloaded from `github.com/ubsicap/ubs-open-license/dictionaries/{hebrew,greek}` | fetch + build | CC BY-SA 4.0 | step 8 `senses_attested` (skipped with a message if absent) |
| `pipeline/vendor/resources/llm_strongs_glosses/<iso>.tsv` — external gloss priors | built by `generators/gbt_source.py` from Global Bible Tools | optional | CC0 | `gloss_priors.GlossPriors`; without it gloss uses its bootstrap priors (the normal case) |
| `config/canonical_index/whole_bible.json`, `publish/compact-alignments/_index/` | ours, from the spine | tracked | — | `compact_align` (position index of every source token) |
| `config/light_lexemes.json`, `config/hebrew_lexeme_strong.json`, `config/greek_morph_strong.json`, `config/contest_rule.json`, `config/fn_veto.json`, `config/spanext_flags.json`, `config/fertility_flags.json`, `config/trust_matrix.json` | ours | tracked | — | chain steps 4–9 |
| `publish/target-stopwords/*.txt` | ours, generated per language by the chain | build | CC0 | `compact_align`, `target_stopwords` |

### 2.5 Grammar facts (gram-struct)

The chain reads one fact sheet per language, `config/gram_struct/<iso>.json` (via `gram_struct.merged_direction`):
`span_extension`, `gapfill`, `fertility_priors`, `article_bound`, `negation_order`, `compact_align`. **That folder is not
tracked**; without it those lookups return "unknown" and the gated mechanisms stay off, so a fresh clone's output is
valid but not identical to ours until it is built (`lexeme-aligner grammar all`, or just `python -m lexeme_aligner.gram_struct --build`
for the external partitions).

| input to gram-struct | where from | in a clone | licence |
|---|---|---|---|
| `config/grambank/features.json` — Grambank features per ISO | built by `grambank_fetch.py` from Grambank v1.0 + Glottolog (raw CSVs in `pipeline/vendor/grambank/`, ignored) | tracked (the built file) | CC BY 4.0 |
| `config/typology/directions.json` — WALS (85A/86A/82A/83A) and lang2vec directions | built by `typology.py` from `pipeline/vendor/wals/` (cldf-datasets/wals v2020.5, ignored) and the `lang2vec` pip package (`[typology]` extra) | tracked | WALS CC BY 4.0; lang2vec CC BY-SA 4.0 |
| `config/typology/uriel_plus.json` | built by `uriel_plus.py` from the `urielplus[imputation]` pip package | tracked | CC BY-SA 4.0 |
| kin prior (nearest related language) | bcv-query's `resources/languages/languages.db` (Glottolog relatedness) | **hard-coded absolute path** in `kin_prior.py` | CC BY 4.0 |
| `config/grc_frames/{role,frames}.tsv` — Greek NT argument structure | bcv-query export | tracked | CC BY |
| `config/gram_struct/derived_input/`, `article_bound.json`, `config/constituent_order/` | ours: `derive_typology.py` / `article_bound.py` / `constituent_order.py` over our own alignments of every language | built (constituent_order is tracked) | CC0 |

### 2.6 Gold alignments (evaluation and full-align manual layers only)

None of these is needed to run the chain. They are used for scoring (`lexeme-aligner eval`, `pipeline/scripts/tools/measure_*.py`)
and for the manual layers of full-align (step 9a, which skips languages without gold).

| resource | where from | in a clone | licence |
|---|---|---|---|
| `pipeline/vendor/resources/strongs/attestations/<iso>.parquet` — Clear-Bible manual alignments (10 languages) plus our Door43 and SWORD rows | HF `bcv-commons/strongs` (Clear rows) + `eval/door43_gold.py`, `eval/sword_source.py` (`[gold]` extra) | fetch / build | Clear CC BY 4.0; some SWORD modules redistribution-only → never published |
| `pipeline/vendor/bsb/` — BSB display spans and the BSB Translation Tables | `BSB-publishing/bsb-data-output` (pinned commit) and `bereanbible.com/bsb_tables.tsv` via `eval/bsb_tables.py --fetch` | fetch | CC0 / public domain |
| `pipeline/vendor/gbt/` — Global Bible Tools glosses (40 languages) | `globalbibletools/data` (pinned commit) via `generators/gbt_fetch.py` | fetch | CC0 |
| `pipeline/vendor/helfi/` — Hebrew-Greek-Finnish alignment | `github.com/amikael/HELFI` (pinned commit, alignment folders only) | fetch | CC BY 4.0 / PD |
| `config/gold_langs.json` | ours: which gold language counts, quarantine notes | tracked | — |
| `config/ebible/translations.csv` | `BibleNLP/ebible` metadata | tracked | — |

### 2.7 LLM passes (opt-in, never in the default chain, never published)

`eval/llm_align.py` / `eval/llm_edit.py` send verses to a model and write LLM layers to `pipeline/work/full-alignments-llm/`.
Providers in `eval/llm_providers.py`: the `claude` CLI (subscription) or the Anthropic API (`ANTHROPIC_API_KEY`, `[llm]` extra).
Responses are cached in `pipeline/work/llm-cache/` (`ALIGNER_LLM_CACHE`), so re-runs are free. Per-language conventions sent
with each request: `config/llm_conventions/<iso>.md` (tracked). The TypeSafe Jev runs (HEBM95, Vamvas) use a work script
under `pipeline/work/jev-testA/` that is not part of the repo yet.

### 2.8 Publishing

`publish_safe.py` pushes to the `bcv-commons` Hugging Face org (`hf auth login`, `[publish]` extra). The tracked
`publish/*/manifest.json` and `README.md` files are the dataset cards and the local record of what was built; the data
partitions themselves are ignored.

## 3. Secrets and environment

| variable | needed for | where |
|---|---|---|
| `BIBLE_API_KEY` (+ optional `BIBLE_API_BASE_URL`, `BIBLE_API_DELAY_MS`) | DBT editions (source `d`) — most of the catalog's languages | `.env` (ignored; the Makefile loads it) |
| `ANTHROPIC_API_KEY` | LLM passes with the API provider | `.env` |
| Hugging Face token | publishing only | `hf auth login` |
| `ALIGNER_SPINE_DB`, `ALIGNER_PRIOR_PACK`, `ALIGNER_OUT`, `ALIGNER_RESOURCES`, `ALIGNER_LLM_CACHE`, `ALIGNER_UBS_SENSES_DB`, `ALIGNER_SYNTAX_SOURCE`, `AUDIO_SYNC_BB`, `ALIGNER_HF_CHUNK_SIZE` | path / behaviour overrides | all default to paths inside the repo (`pipeline/lexeme_aligner/config.py`) |

## 4. What a fresh clone needs, by goal

| goal | needs |
|---|---|
| use the published data | nothing: read from Hugging Face (see the root README) |
| align one language from PKF / helloAO / bibles sources | Python ≥ 3.10, `pip install -e '.[ingest,publish]'` (eflomal builds from C; on macOS see `advanced-docs/architecture.md`), the **spine**, the **prior pack** |
| … and DBT-only languages | + `BIBLE_API_KEY` |
| … and PKF editions | + Node and `npm install` in `pipeline/pkf2usfm` |
| results identical to ours | + `config/gram_struct/` built (`lexeme-aligner grammar …`), + `pipeline/ubs-senses.db` for step 8 |
| rebuild grammar from scratch | + `[typology]` extra, `urielplus[imputation]`, Grambank / WALS raw files, bcv-query's `languages.db`, alignments of many languages |
| score against gold / build manual full-align layers | + section 2.6 |
| LLM experiments | + `[llm]` extra or the `claude` CLI |
| publish | + `[publish]` extra and an HF token with write access to `bcv-commons` |

## 5. Gaps a setup script has to close

What a clone does not get today without manual steps, found while writing this page (2026-10-21):

1. **Spine and prior pack are manual downloads.** Both have a stable URL and a pinned sha256 (sections 2.1, 2.4); a script
   can fetch and verify them.
2. **`ubs-senses.db`** must be built from two UBS JSON files fetched by hand into `pipeline/work/ubs-dictionary/`.
3. **`config/gram_struct/` is ignored**, so a clone runs the chain with grammar-gated mechanisms off. Either track the merged
   `<iso>.json` files, publish them (`pipeline/scripts/tools/stage_gram_struct.py` already stages `publish/gram-struct/`) and pull them, or document `grammar all` as a
   setup step.
4. **`kin_prior.DEFAULT_DB`** is an absolute path on this machine (`/home/lgunnars/dev/bcv-commons/bcv-query/…`).
5. **Source `e` (eBible)** is listed in the refreshed catalog for about 13 languages but has no adapter.
6. **Raw typology inputs** (Grambank, WALS) have fetch code but no single entry point; only needed to rebuild
   `config/grambank` / `config/typology`, which are tracked.
7. Gold fetches are spread over several modules (`bsb_tables --fetch`, `gbt_fetch`, HF `bcv-commons/strongs`, HELFI by hand).

None of this has been tested on a fresh clone yet.
