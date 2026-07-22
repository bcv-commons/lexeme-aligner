# lexeme-aligner — developer architecture

**Not what you're looking for?** If you just want to *use* the published data, see the
[root README](../README.md) and [`lexeme-alignments/README.md`](../lexeme-alignments/README.md) instead —
this doc is for building, running, or extending the pipeline itself.

The "you are here" map. Detail lives in the companion docs: **`aligner-plan.md`** (the ensemble +
projection channels, the full spec), **`bibles-recipe-layer.md`** (target-text ingest),
**`benchmark.md`** (validation), **`data-contracts.md`** (cross-repo/service flows), **`DATA.md`**
(input/output schemas), **`publishing-principles.md`** (the lexeme-anchored, additive-union publish
design).

## What this is
An **offline producer of data**, not a service. Given any Bible translation, it aligns the target
text to the **original-language backbone** (Hebrew/Greek, MACULA lexeme-anchored) and emits a
word-level interlinear + **`lexeme-alignments`** (`surface → lexeme`, Strong's-bridged) per language.
Because everything keys on the backbone, aligning once also unlocks mined multilingual glosses/senses
and stopword lists (the projection channels — `aligner-plan.md`).

## Getting started

```bash
pip install -e .            # core: eflomal + numpy
pip install -e '.[ingest]'  # + usfmtc, needed to convert USFM/USX/PKF → the internal USJ format
pip install -e '.[publish]' # + pyarrow, huggingface_hub — needed to export/publish
```

**eflomal on macOS (Apple clang):** the PyPI wheel fails on `-fopenmp`. Build from source with libomp:
```bash
brew install libomp
git clone https://github.com/robertostling/eflomal && cd eflomal
# in src/Makefile: -fopenmp → "-Xpreprocessor -fopenmp -I$(brew --prefix libomp)/include"
#                  LDFLAGS  → "-lm -L$(brew --prefix libomp)/lib -lomp"
pip install Cython && pip install --no-build-isolation .
```

**One command per language**, ingest through publish:
```bash
python3 -m lexeme_aligner.pipeline --iso ind --lang-name Indonesian --source pkf --all
python3 -m lexeme_aligner.pipeline --iso swe --lang-name Swedish --source auto --all   # auto-resolve the source
```
`--source pkf|helloao|auto` (`auto` resolves via `catalog_source.py`'s cross-source index — see below);
`--all` = whole Bible (OT then NT, separate spines, aggregated); `--ot`/`--nt`/`--book X` to scope
narrower. Add `--publish bcv-commons/lexeme-alignments --create` to push straight to Hugging Face
(needs `huggingface-cli login` or `HF_TOKEN` first).

Or run each stage yourself — useful for iterating on one stage without re-running the others:
```bash
python3 -m lexeme_aligner.run_pilot --method eflomal --ot --usj-dir <dir> --iso ind --lang-name Indonesian
# methods: gloss | stat | eflomal | gapfill | all
python3 -m lexeme_aligner.export_lex --iso ind --lang-name Indonesian
python3 -m lexeme_aligner.benchmark --gold clear --iso ind --method eflomal
```
Alignment output goes to `$ALIGNER_OUT` (default `out/`, gitignored):
`align_<method>_<iso>_<BOOK>.jsonl` + `report_<method>_<iso>.md`.

### Config (all env-overridable — see `config.py`)
| env | what | required? |
|---|---|---|
| `ALIGNER_SPINE_DB` | original-language backbone (`spine_words`, lexeme-anchored) | **yes** |
| `ALIGNER_HBO_DB` | per-occurrence sense sidecar | optional — sense-mining only |
| `ALIGNER_RESOURCES` | gloss priors dir (external CSVs) | optional — gloss falls back to bootstrap priors if absent |
| `ALIGNER_OUT` | experiment output dir | defaults to `out/` |
| `--usj-dir` | target text as USJ (`<NN>-<BOOK>.json`) | **yes**, per run |

The **eflomal** method needs only the spine + target USJ — no glosses, no senses — the cleanest
decoupling check that the standalone core has minimal inputs. Full schemas: `DATA.md`.

## The pipeline — one command per language
`lexeme_aligner.pipeline` chains four stages; `benchmark` is the QA gate.

```
  backbone (spine) ─┐
                    ├─►  ALIGN  ─►  EXPORT  ─►  PUBLISH
  target text (USJ)─┘  eflomal/gloss/  Parquet+     HF dataset
        ▲              gapfill        manifest
        │ INGEST (pin)                    │
  cdn.bibel.wiki PKF / helloAO JSON       └─►  BENCHMARK  (vs clear | lexicon gold)
```

| stage | module | in → out |
|---|---|---|
| **ingest** | `cdn_source` (PKF, Node edge) · `helloao_source` (JSON, pure Python) · `catalog_source` (cross-source discovery/routing) | source text → pin + USJ |
| **align** | `run_pilot` + `eflomal_align` / `stat_align` / `gloss_align` / `gapfill` | spine + USJ → per-verse `align_<method>_<iso>_<BOOK>.jsonl` |
| **export** | `export_lex` | jsonl → `lexeme-alignments/iso=<iso>/data.parquet` + `manifest.json` |
| **publish** | `export_lex --publish` | partition + manifest + companion resources + card → Hugging Face dataset |
| **benchmark** | `benchmark` (`--gold clear\|lexicon`, `--method <mode>`) | scored vs a manual gold |

## The alignment methods (the ensemble)
No single method covers every language; they run as an ensemble (agreement ⇒ confidence). Detail in
`aligner-plan.md`.
- **statistical** — **eflomal** (`eflomal_align`, HMM distortion) needs only parallel text + the
  backbone → works for any language, no LLM/encoder. **The universal spine and the workhorse.** IBM-1
  (`stat_align`, pure Python) exists as a lighter fallback.
- **gloss-anchored** (`gloss_align`, $0) — match target tokens against known per-Strong's glosses.
  Precise but dictionary-bounded; bootstraps its own priors from this language's *own* `eflomal` output
  when no external gloss CSVs are supplied (`bootstrap_priors`). Tags semantically **light** source
  lexemes (light verbs, generic nouns — `cross_lang_prior.build_light_lexemes`) so their gloss calls
  don't outvote eflomal on exactly the class of word where a dictionary approach is least reliable
  (`merge_align`'s contest resolution reads this tag — see below).
- **gapfill** (`gapfill_align`, `gapfill.py`) — model-free gap-filling for tokens neither eflomal nor
  gloss aligned (strong-rollup back-off + name transliteration + cross-lingual span priors from
  `cross_lang_prior`). No target-language model — works for any language, same as eflomal/gloss. A
  neural (LaBSE/bge-m3) approach was tried and retired: measured ~7.5% target-selection contribution on
  its best-case language, zero on languages without encoder coverage — most of the actual target
  population.
- **merge** (`merge_align`, optional, not published) — a single-best-answer-per-token derived view.
  When eflomal and gloss disagree, `contest_rule.json` (empirically validated, leave-one-out tested
  across 10 gold languages) decides the winner. Lossy by design — drops valid alternatives — so it's a
  convenience regenerable from the union, never the published source of truth
  (`docs/publishing-principles.md` §5).

## Canonical internal format: USJ
**USJ is the format-agnostic seam.** Every source (PKF, helloAO JSON, eBible USFM/USX) is converted to
USJ on ingest; everything downstream of USJ (align, export, publish, benchmark) is source-, format-,
and language-agnostic. Adding a source = adding one adapter that emits USJ.

## The anchor: lexeme, Strong's is the bridge
Everything keys on the **MACULA lexeme** (`hbo:0430`, `grc:2316` — lang + augmented-Strong's), the
precise dictionary unit. Bare **Strong's** is coarser (it conflates homonyms and sense-splits — one
Strong's rolls up several lexemes), so it's derived as a **bridge**, never the anchor — and, since it's
a pure function of `lexeme`, it isn't even stored in the published data (see
`lexeme-alignments/README.md`'s derivation note). `hebrew_source` reads `lexeme` from the spine when
present, else derives `<paddedStrong>|<lemma>` — so the pipeline works either way.

Two small, narrowly-scoped correction tables exist for cases the mechanical Strong's derivation gets
wrong (both `benchmark.py`-side only — see `greek_morph_strong.py` and `hebrew_lexeme_strong.py`'s
docstrings for the full story, and `lexeme-alignments/README.md` for how a consumer can use them):
- **`greek_morph_strong.json`** — Clear-Bible's gold uses tense/case-specific traditional Strong's
  numbers for irregular Greek verbs (εἰμί etc.); our lemma-level rollup collapses them to one number.
  Derived from source morphology, independent of any target language.
- **`hebrew_lexeme_strong.json`** — a handful of verified cases where the spine's own bare-Strong's
  rollup merges two genuinely distinct lexemes and picks the minority-usage direction.

## Data model
| artifact | role | source | schema |
|---|---|---|---|
| **spine** (`lexeme-spine.db`) | original-language backbone | shoresh (pinned, MACULA-based) | `spine_words(book,chapter,verse,idx,surface,lexeme,strong,lemma,morph,is_content,gloss,role)` |
| **target USJ** | the translation to align | CDN/helloAO (pinned) | one `<NN>-<BOOK>.json`, USJ 3.0 |
| **lexeme-alignments** | the published product | this repo | `surface, lexeme, method, base_text, count, hi_conf` (`strong`/`share` are derived, not stored) |

Full schemas in `DATA.md`.

## Reproducibility — content-addressed
eflomal seeds from `/dev/urandom` (non-deterministic by design; no seed knob). So we don't promise
byte-reproducible rebuilds: **inputs are pinned** (spine tags + each text's `sha256`), and each
published partition's **`content_sha256`** in `manifest.json` *is* the release identity. A re-run
yields a new, equally-valid partition with a new hash (~1% drift). See `lexeme-alignments/README.md`.

## Key design decisions (and why)
- **USJ seam** — one format layer; sources are pluggable adapters.
- **Source pinning (recipe layer)** — text stays at origin; we pin version + `sha256`, never cache the
  text. Rebuild re-fetches + verifies. See `bibles-recipe-layer.md`.
- **Content-addressed releases** — stochastic aligner ⇒ pin inputs + the output hash, not the process.
- **Lexeme-anchored, additive-union publishing** — never merge methods/editions away; carry provenance
  on every row. See `publishing-principles.md`.
- **Benchmark-side-only correctness fixes** — the Strong's correction tables above only affect
  `benchmark.py`'s scoring, never the published `strong`/`lexeme` fields — a documented, narrow scope
  rather than silently changing what ships.
- **CC0 catalogue + license pointers** — our derived data is CC0; each `surface`'s source translation
  keeps its own license, linked (never copied) in the manifest `source` block.
- **Multi-source, prefer no-Node** — helloAO (pure Python) before PKF (Node edge), per the recipe layer.
- **Cross-repo via pinned artifacts, not code/services** — see `data-contracts.md`; keeps this repo
  standalone and avoids double implementations of the backbone logic.

## Module map
**Core pipeline:** `config` (paths) · `refs` (BBCCCVVV + `BOOK_NUMBERS`, vendored) · `usj_source` ·
`hebrew_source` (spine + optional hbo.db; NT→G/OT→H) · `gloss_priors` · `bootstrap_priors` ·
`gloss_align` · `stat_align` · `eflomal_align` · `gapfill_align` · `gapfill` · `merge_align` ·
`run_pilot` (runner + report) · `export_lex` (→ Parquet + manifest).

**Ingest:** `cdn_source` (PKF) · `helloao_source` (JSON) · `catalog_source` (cross-source discovery)
· `pipeline` (end-to-end driver) · `pkf2usfm/` (the one Node edge, Proskomma PKF→USFM).

**Benchmark & correctness:** `benchmark` (clear|lexicon golds) · `greek_morph_strong` ·
`hebrew_lexeme_strong` · `cross_lang_prior` (span profile + light-lexeme detection) ·
`target_stopwords` · `target_morph` · `verify_stopwords`.

**Other published exports:** `export_mwe` (multi-word expressions) · `export_stopwords` ·
`export_morph`.

**Occurrence-alignment research (separate from the main pipeline)** — cross-checking our own alignment
against independently-produced sources: `gbt_align` / `gbt_fetch` (globalbibletools/data) ·
`bsb_align` / `bsb_fetch` (Berean Standard Bible's own Strong's-tagged spans) · `clear_align`
(Clear-Bible gold, reframed as a source rather than just a benchmark oracle) · `occurrence_union`
(additive union of the above).

**Vendored/misc:** `versification` (KJV-standard verse mapping) · `recipes` (prior-pack joins).
