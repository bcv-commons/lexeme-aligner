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
lexeme TEXT                                        -- the lexical ANCHOR (present in the current spine)
```
`lexeme` is the target anchor (MACULA lang+augmented-Strong's — finer than bare Strong's, which it
rolls up to; see `docs/data-contracts.md` for the shoresh export contract). `hebrew_source` reads it
directly when present, else derives `<paddedStrong>|<lemma>` as a fallback — so a spine without the
column still works, just at coarser (Strong's-only) precision. Where to get the spine (standalone):
build from **STEPBible** TAHOT/TAGNT or **MACULA** (Clear-Bible) — both open; shoresh builds it
(`shoresh/spine/parse.py`, MACULA in `shoresh/macula/`).

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
langs. Feeds `recipes` (below). Bulk parquet git-ignored; `pipeline/vendor/prior-pack/manifest.json` pins the
version. Pull: `snapshot_download('bcv-commons/prior-pack', repo_type='dataset', local_dir='pipeline/vendor/prior-pack')`.

## Outputs (`ALIGNER_OUT/`)

### `recipe_<name>_<iso>.parquet` — prior-pack recipes (mode-1, aligner-computed)
`lexeme_aligner.recipes` joins the prior pack against data this repo owns, per language (all four built):
- **R1 keyness-filter** — `publish/lexeme-alignments` × `prior_pack.keyness` → content-word seed dictionary (drops
  function words, ranks by hi_conf). `recipe_r1_keyness_<iso>.parquet`.
- **R2 sense-surface** — `publish/senses_attested` × `prior_pack.senses` (sense inventory + base rates) → each
  prior sense marked confirmed / **missing** (disambiguation target) / extra. `recipe_r2_sense_<iso>.parquet`.
- **R3 gap-map** — `lexeme-spine` content lexemes MINUS a language's attested lexemes → what it hasn't
  aligned; sorted by low `xling_confidence` (fragile) then spine frequency. `recipe_r3_gapmap_<iso>.parquet`.
- **LXX NT-gap** — OT `publish/lexeme-alignments` surfaces carried into the NT via `prior_pack.lxx_greek` (Hebrew
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
scattered join artifact — the positional data needed to mine MWEs (see `publish/aligned_mwe` below).

### `report_<method>_<iso>.md` — coverage/precision + `publish/lexeme-alignments` & sense-mining previews.

### Promotable artifacts (benchmark-gated — passed, see docs/benchmark.md)
- `publish/lexeme-alignments/` (HF `bcv-commons/lexeme-alignments`, **CC0**) — an **`iso=<iso>/`-partitioned
  Parquet dataset**, one row per (surface → lexeme → **method**):
  `surface, lexeme, method, base_text, count, hi_conf` — the **lexeme** is the anchor of record.
  `strong` and `share` are **not stored** (dropped 2026-07: ~32% smaller, zero information lost — both
  are exact, lossless derivations from the other columns; see `publish/lexeme-alignments/README.md` for the
  formulas and `scripts/strongs_view.py` for a ready-made derived view). It is an **additive union**,
  not a pre-merged winner, across **two provenance axes**: `method` ∈ `{eflomal, gloss, gapfill}` (*how*
  aligned) and `base_text` (*which* edition). A surface→lexeme attested by two methods or two editions
  is separate rows — nothing merged away, full provenance (a `gapfill`-only fact can never masquerade
  as eflomal/gloss). Several editions of one language can be **pooled** into a single `iso=<lang>`
  partition, each row tagged by `base_text` (`--pool`), so cross-edition agreement (a surface→lexeme
  attested by >1 `base_text`) is derivable as a confidence signal — same for cross-*method* agreement.
  `count` is per-(method, base_text) (do **not** sum across methods); `hi_conf` = fraction of the pair's
  links that were intersection-backed (eflomal score ≥ 0.9); content tokens only. Four small companion
  reference files ship alongside the partitions (light-lexeme list, two Strong's edge-case correction
  tables, the merge disagreement-resolution rule) — see `publish/lexeme-alignments/README.md`. Produced by
  `python3 -m lexeme_aligner.export_lex --iso eng --pool engy --lang-name English` (needs the `[publish]`
  extra), which aggregates the `align_<method>_<iso>_*.jsonl` pairs → `publish/lexeme-alignments/iso=<iso>/data.parquet`.
  **The Parquet is git-ignored and published out-of-band** (Hugging Face dataset / object storage);
  only `publish/lexeme-alignments/manifest.json` (per-language metadata + `content_sha256`), `README.md`, and
  the small companion reference files are committed. This keeps regenerated bulk data out of git
  history at multi-thousand-language scale. Design principles (lexeme anchor, Strong's bridge,
  method-provenance, additive union) live in
  `docs/publishing-principles.md`.
- `publish/aligned_mwe/` (**CC0**) — one row per (lexeme → **contiguous multi-word expression**):
  `lexeme, strong, phrase, n_words, count, share, contig`. Where `publish/lexeme-alignments` is per token, this mines
  the real phrase renderings (חֶסֶד → "kasih setia") using the jsonl `t_idx` positions: only spans whose
  target positions are **contiguous** (`max−min+1 == len`) qualify; scattered join-artifacts are dropped
  and counted in the manifest (`scattered_dropped`). Rides on eflomal's grow-diag-final-and symmetrised
  alignment. Produced by `python3 -m lexeme_aligner.export_mwe --iso <iso> --method eflomal` — **needs
  jsonl re-aligned after the `t_idx` change**. Same partitioned-Parquet + committed-manifest layout.
- `publish/senses_attested/` (**CC-BY**, MACULA-keyed) — the attested-evidence layer shoresh ingests (bcv-query
  data-contract): `lexeme, stem, sense, surface, count, share, method, source_corpus, base_text` — one
  row per target rendering of a lexeme in a disambiguated (binyan, sense); `share = count / Σ count for
  that (lexeme, stem, sense)` *within a `base_text`* (target edition). `base_text` is per-row, so
  **multi-version = several editions POOLED into one `iso=<lang>` partition** (`--iso swe --pool swk`),
  each row edition-tagged; cross-edition agreement = confidence; a takedown = a clean `base_text`
  row-drop (never an anonymized re-emit — see `publish/senses_attested/README.md`). Keyed on
  **`(lexeme, stem, sense)`** — MACULA lexeme + MACULA binyan (read inline from the enriched
  `lexeme-spine.db`). Produced by
  `python3 -m lexeme_aligner.senses_attested --iso <iso> --method eflomal`. OT/Hebrew only. **Licensing:
  CC-BY** — the key is MACULA-derived (attribute Clear-Bible); `sense` is the sense *number* only, no
  English sense label (UBS-MARBLE, not redistributable). It is an HF Parquet dataset consumed by shoresh;
  it *feeds/validates* their curated `senses_i18n/<iso>.tsv`, doesn't replace it. Surfaces project into
  shoresh's
  `surfaces_by_method/<iso>.tsv` as `method=eflomal` (they derive it from our `publish/lexeme-alignments`).
- `compact_align` (per-book, content-addressed compact alignments, **CC0**) — the token-level companion
  to `publish/lexeme-alignments` (which is aggregated/type-level and can't reconstruct any one verse's
  alignment). Published layout: one file per (edition, book) at
  `<iso[0]>/<iso>/<edition>/<BOOK>_<last-5-hex-of-book-content-hash>.json` — `iso` is the TRUE published
  language code, never the internal alignment tag (an edition-specific id like `arb_vdv`); `edition` is
  derived from `config/sources.json`'s `source.edition` (the same identifier already published as
  `base_text` in `publish/lexeme-alignments`), iso-prefixed unless it already carries one (`edition_id()` in
  `compact_align.py`; e.g. iso=eng tag=bsb -> edition `eng_BSB`, iso=arb tag=arb_vdv -> edition `arb_vdv`,
  no double prefix). The filename's hash is computed over the book's TRANSLATABLE TEXT (not JSON bytes),
  so any client can independently verify — or locate — the file matching its own copy of that edition's
  text; a revised book (reworded or re-versified) hashes differently, so a stale alignment can never be
  silently served (see `docs/compact-alignments.md` for the exact hash algorithm). Each per-edition file
  is a **plain array** of compact strings, position-parallel to the (ordered) KEYS of one shared,
  once-published file, `publish/compact-alignments/_index/<BOOK>_lexemes.json`
  (`{"BOOK C:V": [lexeme, ...], ...}`, from `build_source_lexemes()`) — no separate flat `["BOOK C:V",
  ...]` ref-index file exists; it would be a pure, byte-identical subset of this file's own key order,
  so a client derives it as `list(lexemes.keys())` (Python) / `Object.keys(lexemes)` (JS) instead of
  fetching a duplicate file. This same file resolves `srcOrd` to an actual lexeme id directly —
  edition-independent, source-only, written lazily by `publish_compact()` only if missing — so a client
  never has to re-derive `is_content` from its own morphology data (motivating case: Hebrew's
  direct-object marker `H0853` shares its bare lexeme id with a rarer noun homonym; `is_content` here
  comes from MACULA's per-occurrence `class` tag, not the lexeme, so we resolve and publish the answer
  instead of asking every client to replicate that join). Lexeme ids here drop the `lang:` prefix
  `publish/lexeme-alignments` uses (`hbo:0430` -> `0430`) — safe only because one book is always entirely one
  testament. `"srcOrd:span srcOrd:span ..."` where `srcOrd` is the 0-based ordinal among that verse's
  content lexemes (spine order; an unaligned lexeme's ordinal is simply absent) and `span` is a
  target-token-index int / contiguous `"a-b"` range / scattered `"a,b,c"` list. Uses
  `run_pilot.pooled_verse_groups()` (the same range-pooling/idx-renumbering `build_corpus()`
  and `gapfill.py` use), so a pooled range's anchor-verse string covers the whole group and non-anchor
  members are `""`. Alignment source = the additive union of eflomal+gloss+gapfill jsonl. Produced by
  `python3 -m lexeme_aligner.compact_align --iso <tag> --publish-iso <iso> --usj-dir <dir> --publish
  <root>`. A whole-bible single-array dev/debug mode also exists (`--out`, needs a one-time
  `--build-index` — `config/canonical_index/whole_bible.json`, shared across languages) but is not
  the published form. Full format detail in `docs/compact-alignments.md`.
- per-word interlinear. → published to **bcv-commons**; the monorepo consumes them as external resources.
