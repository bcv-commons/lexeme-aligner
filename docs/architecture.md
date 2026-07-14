# lexeme-aligner — architecture

The "you are here" map. Detail lives in the companion docs: **`aligner-plan.md`** (the ensemble +
projection channels, the full spec), **`bibles-recipe-layer.md`** (target-text ingest),
**`benchmark.md`** (validation), **`data-contracts.md`** (cross-repo/service flows), **`DATA.md`**
(input/output schemas).

## What this is
An **offline producer of data**, not a service. Given any Bible translation, it aligns the target
text to the **original-language backbone** and emits a word-level interlinear + **`lexeme-alignments`**
(`surface → Strong's`) per language. Because everything keys on the backbone, aligning once also
unlocks mined multilingual glosses/senses/domains and a name-bridge (the four projection channels).

## The pipeline — one command per language
`lexeme_aligner.pipeline` chains four stages; `benchmark` is the QA gate.

```
  backbone (spine) ─┐
                    ├─►  ALIGN  ─►  EXPORT  ─►  PUBLISH
  target text (USJ)─┘   eflomal/…   Parquet+     HF dataset
        ▲                          manifest
        │ INGEST (pin)                 │
  cdn.bibel.wiki PKF / helloAO JSON    └─►  BENCHMARK  (vs clear | lexicon gold)
```

| stage | module | in → out |
|---|---|---|
| **ingest** | `cdn_source` (PKF, Node edge) · `helloao_source` (JSON, pure-Python) | source text → pin + USJ |
| **align** | `run_pilot` + `eflomal_align` / `stat_align` / `gloss_align` | spine + USJ → per-verse `align_<method>_<iso>_<BOOK>.jsonl` |
| **export** | `export_lex` | jsonl → `lexeme-alignments/iso=<iso>/data.parquet` + `manifest.json` |
| **publish** | `export_lex --publish` | partition + manifest + card → Hugging Face dataset |
| **benchmark** | `benchmark` (`--gold clear\|lexicon`, `--method <mode>`) | scored vs a manual gold |

`pipeline --source pkf|helloao [--all]` drives ingest→align→export→publish for one language (`--all`
= whole Bible: OT then NT, aggregated).

## The three alignment modes (the ensemble)
No single method covers every language; they run as an ensemble (agreement ⇒ confidence). Detail in
`aligner-plan.md`.
- **gloss-anchored** (`gloss_align`, $0) — match target tokens to known per-Strong's glosses. Precise,
  dictionary-bounded. Needs gloss priors (absent here → no-op).
- **statistical** — **IBM-1** (`stat_align`) and **eflomal** (`eflomal_align`, HMM). Needs only
  parallel text + the backbone → works for any language. **The universal spine and the workhorse.**
- **gapfill** — the 3rd method: model-free gap-filling for tokens neither eflomal nor gloss aligned (strong-rollup back-off + name transliteration + #1 cross-lingual span extension + #3 target-stopword gate). A neural (LaBSE/bge-m3) approach was tried and retired — measured at ~7.5% target-selection contribution on its best-case language, zero on languages without encoder coverage, i.e. most of the actual target population.

A **merged/ensemble** output is future work; when built it's just another `--method` tag, scored the
same way by `benchmark`.

## Canonical internal format: USJ
**USJ is the format-agnostic seam.** Every source (PKF, helloAO JSON, eBible USFM/USX) is converted to
USJ on ingest; everything downstream of USJ (align, export, publish, benchmark) is source-, format-,
and language-agnostic. Adding a source = adding one adapter that emits USJ.

## The anchor — Strong's today, lexeme tomorrow
Currently everything keys on **Strong's** (a bare int, re-prefixed H/G per testament). Strong's is
**lossy**: it conflates homonyms and the H/G numbering, so it is *coarser* than the lexical grain
(8,575 Strong's vs 12,751 lemmas in the spine). The planned shift is to anchor on the **lexeme**
(MACULA augmented-Strong's + lemma), with bare Strong's as a derived rollup — a richer, more correct
key that also aligns with the benchmark gold's grain. This depends on a lexeme-anchored spine from
shoresh — see `data-contracts.md`.

## Data model
| artifact | role | source | schema |
|---|---|---|---|
| **spine** (`spine.db`) | original-language backbone | shoresh (pinned) | `spine_words(book,chapter,verse,idx,surface,strong,lemma,morph,is_content)` |
| **target USJ** | the translation to align | CDN/helloAO (pinned) | one `<NN>-<BOOK>.json`, USJ 3.0 |
| **lexeme-alignments** | the published product | this repo | `surface, strong, count, share, hi_conf` (→ lexeme-primary) |

Full schemas in `DATA.md`.

## Reproducibility — content-addressed
eflomal seeds from `/dev/urandom` (non-deterministic by design; no seed knob). So we don't promise
byte-reproducible rebuilds: **inputs are pinned** (spine tags + each text's `sha256`), and each
published partition's **`content_sha256`** in `manifest.json` *is* the release identity. A re-run
yields a new, equally-valid partition with a new hash (~1% drift). See `lexeme-alignments/README.md`.

## The four projection channels
Aligning once, keyed on the backbone, unlocks four channels of increasing cost: (1) occurrence-direct
(morphology/sense/frame-role/coref), (2) Strong's/lexeme join (glosses/domains/keyness),
(3) verse-correspondence (speaker/xrefs/topics — needs only a verse map), (4) name-bridge. Detail in
`aligner-plan.md`.

## Key design decisions (and why)
- **USJ seam** — one format layer; sources are pluggable adapters.
- **Source pinning (recipe layer)** — text stays at origin; we pin version + `sha256`, never cache the
  text. Rebuild re-fetches + verifies. See `bibles-recipe-layer.md`.
- **Content-addressed releases** — stochastic aligner ⇒ pin inputs + the output hash, not the process.
- **CC0 catalogue + license pointers** — our derived data is CC0; each `surface`'s source translation
  keeps its own license, linked (never copied) in the manifest `source` block.
- **Multi-source, prefer no-Node** — helloAO (pure Python) before PKF (Node edge), per the recipe layer.
- **Cross-repo via pinned artifacts, not code/services** — see `data-contracts.md`; keeps this repo
  standalone and avoids double implementations of the backbone logic.

## Module map
`config` (paths) · `refs` (BBCCCVVV + `BOOK_NUMBERS`, vendored) · `usj_source` · `hebrew_source`
(spine + optional hbo.db; NT→G/OT→H) · `gloss_priors` · `gloss_align` · `stat_align` · `eflomal_align`
· `run_pilot` (runner + report) · `export_lex` (→ Parquet + manifest) · `benchmark` (clear|lexicon
golds) · `cdn_source` (PKF ingest) · `helloao_source` (JSON ingest) · `pipeline` (end-to-end driver).
`pkf2usfm/` is the one Node edge (Proskomma PKF→USFM).
