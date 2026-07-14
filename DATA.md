# Data contracts

Exactly what the aligner reads and writes — so a standalone repo knows what to feed it (and where
to get it) and what it emits. All paths are `config.py` env vars.

## Inputs

### 1. Original backbone — `ALIGNER_SPINE_DB` (SQLite, **required**)
Table `spine_words`, one row per original-language token:
```
book TEXT, chapter INT, verse INT, idx INT,       -- PK (book,chapter,verse,idx)
surface TEXT, strong INT, lemma TEXT, morph TEXT,  -- strong is a bare int; H vs G from OT/NT book
is_content INT                                     -- 1 = N/V/A head-POS content word
lexeme TEXT                                        -- OPTIONAL, forthcoming: the lexical ANCHOR
```
`lexeme` is the target anchor (MACULA lang+augmented-Strong's — finer than bare Strong's, which it
rolls up to; see `docs/data-contracts.md` for the shoresh export contract). `hebrew_source` uses the
column when present; **until it lands it derives `<paddedStrong>|<lemma>`**, so the pipeline is already
lexeme-anchored. Where to get the spine (standalone): build from **STEPBible** TAHOT/TAGNT or **MACULA**
(Clear-Bible) — both open; shoresh builds it (`shoresh/spine/parse.py`, MACULA in `shoresh/macula/`).

### 2. Per-occurrence senses — `ALIGNER_HBO_DB` (SQLite, **optional** — sense-mining only)
Table `occurrence`: `node, ref (BBCCCVVV), book, chapter, verse, lex, stem, sp, strong (H####),
gloss, sense, sense_source, sense_conf`. Joined by `ref + strong` (strong-in-order). Hebrew/OT only.
Absent → the sense-mining enrichment is skipped; alignment is unaffected.

### 3. Gloss priors — `ALIGNER_RESOURCES/` (**optional** — gloss-anchored method only)
- `word_glosses/hbo/<LangName>.csv` — `lex` + per-binyan columns (`default,qal,nif,piel,…`).
- `llm_strongs_glosses/<iso>.tsv` — `strong, lemma_ref, en_ref, gloss`.
- `strongs_tw.tsv` (`strong, tw_article, …`) + `tw_articles/<iso>.json` (`{article_id: {title}}`).
Published forms live in **bcv-commons/strongs** — consume those standalone. Absent → gloss method
is a no-op; **eflomal needs none of this**.

### 4. Target text — `--usj-dir` (USJ, **required**)
One `<NN>-<BOOK>.json` per book (USFM Paratext numbering), USJ 3.0. The adapter walks `content`,
tracks `chapter`/`verse` markers, keeps `char:w`/paragraph text, excludes `note`/`para:s*`/`para:d`.
Build USJ from USFM/USX (`usfmtc`) or PKF (Proskomma) — see `docs/bibles-recipe-layer.md`.

### 5. Prior pack — `ALIGNER_PRIOR_PACK` (Parquet, **optional** — recipes only)
Pulled from **`bcv-commons/prior-pack`** (HF, CC-BY): one row per MACULA lexeme with `keyness`,
`lxx_greek`/`lxx_hebrew`, `senses` inventory, `neighbors`, `xling_confidence` (schema:
internal-docs/aligner-handover.md · monorepo prior-pack.md). Language-independent — one pull serves all
langs. Feeds `recipes` (below). Bulk parquet git-ignored; `resources/prior-pack/manifest.json` pins the
version. Pull: `snapshot_download('bcv-commons/prior-pack', repo_type='dataset', local_dir='resources/prior-pack')`.

## Outputs (`ALIGNER_OUT/`)

### `recipe_<name>_<iso>.parquet` — prior-pack recipes (mode-1, aligner-computed)
`lexeme_aligner.recipes` joins the prior pack against data this repo owns, per language (all four built):
- **R1 keyness-filter** — `lexeme-alignments` × `prior_pack.keyness` → content-word seed dictionary (drops
  function words, ranks by hi_conf). `recipe_r1_keyness_<iso>.parquet`.
- **R2 sense-surface** — `senses_attested` × `prior_pack.senses` (sense inventory + base rates) → each
  prior sense marked confirmed / **missing** (disambiguation target) / extra. `recipe_r2_sense_<iso>.parquet`.
- **R3 gap-map** — `lexeme-spine` content lexemes MINUS a language's attested lexemes → what it hasn't
  aligned; sorted by low `xling_confidence` (fragile) then spine frequency. `recipe_r3_gapmap_<iso>.parquet`.
- **LXX NT-gap** — OT `lexeme-alignments` surfaces carried into the NT via `prior_pack.lxx_greek` (Hebrew
  lexeme → LXX → Greek); candidate NT renderings, `nt_total=0` gaps first, restricted to CONTENT Greek
  lexemes (keyness-filtered — else the article/prepositions flood it). `recipe_lxx_ntgap_<iso>.parquet`.
`python3 -m lexeme_aligner.recipes --iso <iso> --recipe all` (or `r1|r2|r3|lxx`).

### `align_<method>_<iso>_<BOOK>.jsonl` — per-verse alignments
```json
{"ref": 8001016, "book": "RUT", "chapter": 1, "verse": 16,
 "pairs": [{"h_idx", "lexeme", "strong", "stem", "surface", "gloss_en", "sense",
            "target", "t_idx", "score", "method", "content"}]}
```
`lexeme` is the lexical anchor; `strong` is its rollup (see backbone note above). `t_idx` is the target
token position(s) this lexeme aligned to (in the verse's token order); `target` is those tokens joined.
A **contiguous** `t_idx` run (`max−min+1 == len`) is a real multi-word expression; a gapped one is a
scattered join artifact — the positional data needed to mine MWEs (see `aligned_mwe` below).

### `report_<method>_<iso>.md` — coverage/precision + `lexeme-alignments` & sense-mining previews.

### Promotable artifacts (benchmark-gated — passed, see docs/benchmark.md)
- `lexeme-alignments/` (HF `bcv-commons/lexeme-alignments`, **CC0**) — an **`iso=<iso>/`-partitioned
  Parquet dataset**, one row per (surface → lexeme → **method**):
  `surface, lexeme, strong, method, base_text, count, share, hi_conf` — the **lexeme** is the anchor of
  record, `strong` its rollup **bridge** (each lexeme → one Strong's, so a Strong's-ecosystem consumer can
  group either way). It is an **additive union**, not a pre-merged winner, across **two provenance axes**:
  `method` ∈ `{eflomal, gloss, gapfill}` (*how* aligned) and `base_text` (*which* edition). A surface→lexeme
  attested by two methods or two editions is separate rows — nothing merged away, full provenance (a
  `gapfill`-only fact can never masquerade as eflomal/gloss). Several editions of one language can be
  **pooled** into a single `iso=<lang>` partition, each row tagged by `base_text` (`--pool`), so
  cross-edition agreement (a surface→lexeme attested by >1 `base_text`) is derivable as a confidence signal.
  `count` is per-(method, base_text) (do **not** sum across methods); `share = count / Σ count for that
  surface` **within (method, base_text)** = P(lexeme | surface); `hi_conf` = fraction of the pair's links
  that were intersection-backed (eflomal score ≥ 0.9); content tokens only. Produced by
  `python3 -m lexeme_aligner.export_lex --iso eng --pool engy --lang-name English` (needs the `[publish]`
  extra), which aggregates the `align_<method>_<iso>_*.jsonl` pairs → `lexeme-alignments/iso=<iso>/data.parquet`.
  **The Parquet is git-ignored and published out-of-band** (Hugging Face dataset / object storage);
  only `lexeme-alignments/manifest.json` (per-language metadata + `content_sha256`) and `README.md` are
  committed. This keeps regenerated bulk data out of git history at multi-thousand-language scale. Design
  principles (lexeme anchor, Strong's bridge, method-provenance, additive union) live in
  `docs/publishing-principles.md`.
- `aligned_mwe/` (**CC0**) — one row per (lexeme → **contiguous multi-word expression**):
  `lexeme, strong, phrase, n_words, count, share, contig`. Where `lexeme-alignments` is per token, this mines
  the real phrase renderings (חֶסֶד → "kasih setia") using the jsonl `t_idx` positions: only spans whose
  target positions are **contiguous** (`max−min+1 == len`) qualify; scattered join-artifacts are dropped
  and counted in the manifest (`scattered_dropped`). Rides on eflomal's grow-diag-final-and symmetrised
  alignment. Produced by `python3 -m lexeme_aligner.export_mwe --iso <iso> --method eflomal` — **needs
  jsonl re-aligned after the `t_idx` change**. Same partitioned-Parquet + committed-manifest layout.
- `senses_attested/` (**CC-BY**, MACULA-keyed) — the attested-evidence layer shoresh ingests (bcv-query
  data-contract): `lexeme, stem, sense, surface, count, share, method, source_corpus, base_text` — one
  row per target rendering of a lexeme in a disambiguated (binyan, sense); `share = count / Σ count for
  that (lexeme, stem, sense)` *within a `base_text`* (target edition). `base_text` is per-row, so
  **multi-version = several editions POOLED into one `iso=<lang>` partition** (`--iso swe --pool swk`),
  each row edition-tagged; cross-edition agreement = confidence; a takedown = a clean `base_text`
  row-drop (never an anonymized re-emit — see `senses_attested/README.md`). Keyed on
  **`(lexeme, stem, sense)`** — MACULA lexeme + MACULA binyan (read inline from the enriched
  `lexeme-spine.db`). Produced by
  `python3 -m lexeme_aligner.senses_attested --iso <iso> --method eflomal`. OT/Hebrew only. **Licensing:
  CC-BY** — the key is MACULA-derived (attribute Clear-Bible); `sense` is the sense *number* only, no
  English sense label (UBS-MARBLE, not redistributable). It is an HF Parquet dataset consumed by shoresh;
  it *feeds/validates* their curated `senses_i18n/<iso>.tsv`, doesn't replace it. Surfaces project into
  shoresh's
  `surfaces_by_method/<iso>.tsv` as `method=eflomal` (they derive it from our `lexeme-alignments`).
- per-word interlinear. → published to **bcv-commons**; the monorepo consumes them as external resources.
