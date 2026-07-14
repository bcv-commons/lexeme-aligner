# lexeme-aligner — roadmap

Align **any** Bible translation to the **Strong's-tagged original** (Hebrew/Greek). Out comes a
word-level interlinear + `lexeme-alignments` (`surface → lexeme`, Strong's-bridged), and — because everything keys on
Strong's — mined multilingual glosses/senses/domains + a name-bridge for free. An **offline producer
of data**, not a service.

## Status
Standalone repo, extracted from the `bcv-query` monorepo. Package is **`lexeme_aligner`** (imports
are `from lexeme_aligner.…`; console script `lexeme-align`). All modules byte-compile and imports
resolve; `refs.encode('GEN',1,1)=1001001`.

**Standalone extraction validated.** Full-OT eflomal run (39 books, 23,145 verses, `ind`) reproduces
the pilot: **88.8% coverage / 61.8% hi-conf**, from `spine.db` + target USJ alone (no glosses/senses).
The `lexeme-alignments` producer (`lexeme_aligner.export_lex`) aggregates the per-verse `.jsonl` into the
contracted lexeme-anchored schema — 63.5k rows / 34k surfaces / 7.7k Strong's for `ind`.

**Gold benchmark passed — promotion gate cleared.** eflomal scored against Clear-Bible manual gold
(`lexeme_aligner.benchmark`): **91.8% (fra) / 95.6% (hau)** token-weighted top-1. Hausa (distant,
lower-resource) beats French, so the method generalizes rather than overfitting — trustworthy for
no-gold languages like `ind`. Full write-up + recipe: **`docs/benchmark.md`**.

Design docs: **`docs/architecture.md`** (the map) · **`docs/aligner-plan.md`** (full spec) ·
**`docs/bibles-recipe-layer.md`** (source-of-truth ingest) · **`docs/data-contracts.md`** (cross-repo
flows + the shoresh lexeme-spine ask) · **`docs/benchmark.md`** (validation). Schemas: **`DATA.md`**.
Packaging/run: **`README.md`**.

## Architecture — the ensemble
- **gloss-anchored** (`gloss_align`, $0) — match target tokens against known per-Strong's glosses.
  Precise but dictionary-bounded.
- **statistical** — **IBM-1** (`stat_align`, pure Python) and **eflomal** (`eflomal_align`, HMM
  distortion). Needs only parallel text + Strong's → **works for any language, no LLM/encoder**. This
  is the **universal spine** and the workhorse.
- **gapfill** — model-free gap-filling (strong-rollup back-off + name transliteration + #1 cross-lingual
  span extension + #3 target-stopword gate) for tokens neither eflomal nor gloss aligned. A neural
  (LaBSE/bge-m3) approach was tried and retired (~7.5% contribution on its best-case language, zero on
  languages without encoder coverage) — no target-language model needed, works for any language.

**Pilot result** (full-OT Indonesian, 23k verses): gloss 51% · IBM-1 62% · **eflomal 89% coverage /
62% hi-conf** · union 95%. Gold (gloss ∩ eflomal hi-conf, same target) ~18% @ ~99% precision.

## The four projection channels (why Strong's-anchoring pays off)
Everything keys on Strong's, so aligning once unlocks: (1) occurrence-direct (morphology/sense/
frame-role/coref), (2) Strong's-join (glosses/domains/keyness), (3) verse-correspondence (speaker/
xrefs/topics/entities — needs only a verse map, **not** word alignment), (4) name-bridge. Coverage is
a **dial**: auto pass ~50–80% → LLM/manual raise → re-harvest → derive. Detail in `docs/aligner-plan.md`.

## Roadmap / next steps
1. ~~Copy `spine.db` in + smoke-test `--method eflomal --book RUT`.~~ **Done** — spine + `usj-ind`
   in place, full-OT run validated.
2. ~~`lexeme-alignments` producer (`surface, lexeme, strong, method, count, share, hi_conf`).~~ **Done** —
   `lexeme_aligner.export_lex` writes an `iso=<iso>/`-partitioned Parquet dataset (additive union of
   methods, lexeme-anchored, `strong` a rollup bridge) + a committed
   `lexeme-alignments/manifest.json`; bulk data git-ignored, published out-of-band (see `lexeme-alignments/README.md`).
3. ~~Benchmark vs gold.~~ **Done + generalized** — `lexeme_aligner.benchmark` now scores any
   `--method` against `--gold clear|lexicon`: Clear-Bible attestations (91.8% fra / 95.6% hau) *and* a
   manual Strong's→translation lexicon (karnbibeln.se Swedish — Greek ~92% both translations, Hebrew
   ~87% swk / ~79% swe). `docs/benchmark.md`. **Only the statistical (eflomal) mode is scored so far;**
   gloss (needs priors), IBM-1, gapfill, and the merged ensemble are runnable through the same tool once
   produced — a natural companion to the multi-version work below.
4. **Publish `lexeme-alignments` to a data channel** — `export_lex --publish <hf-repo> --create` is wired
   (uploads the partition + manifest + card via `huggingface_hub`, credential-gated, `--dry-run`
   verified). Remaining: create the HF dataset repo + `huggingface-cli login`, then run the push for
   `ind`. Free public tier fits thousands of langs.
5. **Multi-source ingest + end-to-end driver** — **working**. Two text sources behind the USJ seam:
   `cdn_source` (cdn.bibel.wiki **PKF**, 589 langs, Node edge `pkf2usfm/`) and `helloao_source`
   (bible.helloao.org **JSON**, ~1,256 translations, pure Python — reaches beyond PKF, e.g. Swedish).
   Both pin (sha256) + link the source licence, never copy it. `lexeme_aligner.pipeline
   --source pkf|helloao [--all]` chains ingest → align → export [→ publish] per language; `--all` does
   the whole Bible (OT then NT — separate spines — aggregated into one lexicon). Verified end-to-end:
   `ind` via PKF (~50s); `swe` whole Bible via helloAO in ~47s → 67.5k rows / 12,999 Strong's (H + G).
   DBT (`/dbt/`) is audio, not text. *Reproducibility (decided — content-addressed, see below):* eflomal
   seeds from `/dev/urandom` (`random.c`), so it's non-deterministic by design — the pinned **inputs** are
   reproducible, and each published Parquet's `content_sha256` is the immutable release record (~1%
   regeneration drift is expected, not a bug). Next: a source **resolver** — the CDN is now fully live, so discovery
   is a two-index union (no more waiting on `availability.json`, which is superseded): `pkf/manifest.json`
   (589 text langs + `codex`) ∪ helloAO `available_translations` (~1,256 text translations, per-iso).
   `dbt/_app/media-index.json` (1,950 langs) is audio+**names** only — use it to auto-fill `--lang-name`.
   Resolver: prefer helloAO (no-Node) → else PKF; it also enumerates a language's versions (multi-version).
6. **bcv-commons dataset contract** — the *consume* side is still open: pin the exact published
   names/schemas this repo reads from `bcv-commons/strongs` (glosses/senses). The *produce* side
   (`lexeme-alignments`) is pinned by DATA.md + `export_lex`; the gold-consume side is pinned by `benchmark`.
7. ~~`senses_i18n` — blocked on `hbo.db`.~~ **Done as `senses_attested`** (per the bcv-query contract).
   The enriched `lexeme-spine.db` carries MACULA `stem`(binyan)/`sense` inline; `hebrew_source` reads
   them, and `lexeme_aligner.senses_attested` emits `lexeme, stem, sense, surface, count, share, method,
   source_corpus, base_text` — keyed on **`(lexeme, stem, sense)`** (MACULA lexeme + binyan; BHSA `lex`
   dropped). **CC-BY** (MACULA-keyed), sense *number* only (no MARBLE label). `base_text` = target
   edition per-row → multi-version = union of per-edition runs (cross-edition agreement = confidence).
   Evidence that feeds shoresh's curated `senses_i18n`/`_gaps`; surfaces project into their
   `surfaces_by_method` as `method=eflomal`. Verified on `ind`: e.g. `hbo:0006` **hiphil** → `membinasakan`.
8. ~~Lexeme anchoring (on the MACULA spine).~~ **Landed + working.** shoresh shipped
   `data/lexeme-spine.db` (MACULA, 607k words; `lexeme = lang:augmented-strong`, `strong` = rollup,
   + `gloss`/`role`; pinned by `macula_spine_sha256`). `hebrew_source` auto-detects the `lexeme` column
   (`has_lexeme`), `run_pilot`/`export_lex` are lexeme-primary (`surface, lexeme, strong, …`), and it's
   now the **default spine**. Verified end-to-end: Kärnbibeln whole Bible → 13,700 lexemes; benchmark vs
   karnbibeln **improved** (Greek 92→**93.4%**, Hebrew 87→**88.7%**). Contract: `docs/data-contracts.md`;
   rationale: `docs/architecture.md`. **Phase-2 A/B done:** `run_pilot --anchor strong|lexeme` — aligning
   *on* lexeme vs strong is a **wash** on the (strong-keyed) benchmark at every threshold. The
   granularity comes from the lexeme **labeling**, not the anchor key: both separate homonyms (e.g.
   `hbo:3068 herren` vs `hbo:3069 adonai jahveh`, a distinction the canonicalized Strong's erases) —
   255 such splits. So default stays `--anchor strong` (denser). **Lexeme-grain benchmark built:**
   `benchmark --gold clear --grain lexeme` scores surface→(Strong's+lemma) — homonyms distinguished by
   the source lemma both sides carry (no id-mapping). Result on Clear's Greek-NT gold: fra 92.2→89.3%,
   arb 93.2→89.5% — a uniform ~3pt drop (finer grain is stricter), no Semitic advantage. **But the gold
   is Greek NT**, but Clear's `arb` gold **also has full OT** (Hebrew, 289k) — a Semitic OT gold in-hand
   (plus eng/spa/rus/hin OT). **Hypothesis tested (arb vs eng, same Hebrew source, editions AVD/BSB):
   NOT supported** — arb 97.4→92.4 (−5.0pt), eng 95.7→91.0 (−4.7pt), same lexeme-grain drop; Semitic
   relatedness gives no homonym-distinguishing advantage by top-1 (arb *is* higher at strong grain).
   Lemma match gold↔spine ~93% after niqqud-strip (a residual noise floor, equal for both → comparison
   holds). (Also fixed: the tokenizer shattered diacritized scripts at combining marks — `فِي`→`ف,ي`;
   now strips harakat/niqqud, so Arabic tokenizes by word.)
9. **Multi-version pooling** — align a language's several translations together for a richer lexicon
   (design below).
10. ~~Neural method~~ **Done, then retired** — SimAlign+LaBSE was built, measured (~7.5% contribution on
    its best-case language, zero on languages without encoder coverage), and replaced by **gapfill**
    (model-free: strong-rollup back-off, name transliteration, #1 cross-lingual span, #3 stopword gate).
11. **Standalone README polish** — a "Standalone setup" section (env table + dangling-defaults note)
    if desired.

## Multi-version pooling (design)

A language often has several translations (helloAO: Swedish has 2 — `swe_fol` full, `swe_svk` NT+;
English has dozens). Aligning them *together* against the one Strong's spine should yield a richer,
higher-confidence `lexeme-alignments`. Each verse is a training pair `source = Strong's sequence`
(identical across versions) ↔ `target = that version's words`, so N versions = every verse seen N
times with N renderings.

### Why it helps
- **Rare-Strong's recall (the tail).** Where a single Bible starves (a Strong's occurring 2–3×), the
  pooled corpus has 2–3× the co-occurrence — the biggest statistical win, exactly where one version
  is weakest. Common words already saturate on one full Bible (Swedish `swe_fol` alone: 96% cov /
  80% hi-conf), so the gain is concentrated in the tail.
- **Lexicon breadth (the main prize).** Different translations render the same Strong's differently
  (`Herren` / `HERREN` / `Jahve` → H3068); pooling captures *all* those surface forms — precisely
  what a comprehensive surface→Strong's lexicon wants.
- **A new confidence axis — cross-*translation* agreement.** Independent translations agreeing on a
  `surface → Strong's` is far stronger evidence than one version's own forward/backward agreement —
  an ensemble vote across translators.

### Two implementations (composable)
- **(a) Pool in one eflomal run** — concatenate all versions' verse-pairs, train one lexical table.
  Maximizes tail recall (statistics share strength during training). Best for low-resource languages.
- **(b) Align each version separately, then merge the `lexeme-alignments`** — union the `(surface→Strong's)`
  counts, keeping per-version provenance and a clean cross-version `versions`/agreement signal, and
  staying robust to a bad version. **Preferred default** for the lexicon product.

### Caveats
- **Same versification + spine** across pooled versions, or verse-pairs misalign (helloAO normalizes
  versification; a divergent Psalm numbering would need the vrs map).
- **Pool per testament, not per language** — versions differ in coverage (`swe_svk` is NT-only), so
  the OT pool and NT pool have different member sets. (This is why "all OT versions, then all NT
  versions" is the right framing.)
- **Paraphrases add noise** — dynamic/loose translations align messier; thresholding on
  `share`/`hi_conf`/`versions` filters them, but formal-equivalence versions pool more cleanly.

### Code impact
Today one `iso` = one translation (`align_<method>_<iso>_<BOOK>.jsonl`; `export_lex` aggregates by
`iso`), so a second version would overwrite the first. Multi-version needs either a **version tag** in
the ingest/align/output path (approach b) or **multi-`usj-dir` corpus building** in `run_pilot`
(approach a), plus the manifest `source` recording *multiple* editions and an added `versions` column.
Recommended first step: approach (b) — discover a language's helloAO translations, align each
per-testament, merge into one enriched `lexeme-alignments/iso=<iso>/` with a cross-version agreement signal.
