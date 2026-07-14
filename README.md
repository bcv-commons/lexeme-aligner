# lexeme-aligner

Align *any* Bible translation to the **Strong's-tagged original** (Hebrew/Greek), and out comes a
word-level interlinear plus `lexeme-alignments` (`surface → Strong's`) — and, because everything keys on
Strong's, mined multilingual glosses, senses, domains, and a name-bridge attach for free. An offline
**producer of data**, not a service. Architecture map: `docs/architecture.md`; full design:
`docs/aligner-plan.md`; ingest/source-of-truth: `docs/bibles-recipe-layer.md`; cross-repo data flows:
`docs/data-contracts.md`.

## Methods (ensemble)
- **gloss-anchored** ($0) — match target tokens against known per-Strong's glosses. Precise, but
  dictionary-bounded.
- **statistical** — IBM-1 (pure Python, `stat_align`) and **eflomal** (HMM distortion, `eflomal_align`).
  Needs only parallel text + Strong's → **works for any language, no LLM/encoder** (the universal spine).
- **gapfill** — model-free gap-filling (strong-rollup back-off + name transliteration + cross-lingual span priors) for tokens neither eflomal nor gloss aligned. No target-language model — works for any language, same as eflomal/gloss.

**Pilot result** (full-OT Indonesian, 23k verses): gloss 51% · IBM-1 62% · **eflomal 89% coverage /
62% high-confidence** · union 95%. eflomal is the workhorse.

## Install
```bash
pip install -e .            # core: eflomal + numpy
pip install -e '.[ingest]'  # + usfmtc (USFM/USX → USJ)
```
**eflomal on macOS (Apple clang):** the PyPI wheel fails on `-fopenmp`. Build from source with libomp:
```bash
brew install libomp
git clone https://github.com/robertostling/eflomal && cd eflomal
# in src/Makefile: -fopenmp → "-Xpreprocessor -fopenmp -I$(brew --prefix libomp)/include"
#                  LDFLAGS  → "-lm -L$(brew --prefix libomp)/lib -lomp"
pip install Cython && pip install --no-build-isolation .
```

## Run
```bash
python -m lexeme_aligner.run_pilot --method all --ot \
    --usj-dir <dir of NN-BOOK.json USJ files> --iso ind --lang-name Indonesian
# methods: gloss | stat | eflomal | both | all ; --eflomal-priors = semi-supervised
```
Outputs to `$ALIGNER_OUT` (default `aligner/out/`, gitignored): `align_<method>_<iso>_<BOOK>.jsonl`
+ `report_<method>_<iso>.md`.

## Inputs / config (all env-overridable — see `config.py`)
| env | what |
|---|---|
| `ALIGNER_SPINE_DB` | original backbone: `spine_words(book,chapter,verse,idx,surface,strong,lemma,morph,is_content)` |
| `ALIGNER_HBO_DB` | per-occurrence sense sidecar (optional — sense-mining only) |
| `ALIGNER_RESOURCES` | gloss priors dir (optional — gloss-anchored method only) |
| `ALIGNER_OUT` | experiment output dir |
| `--usj-dir` | target text as USJ (build it from USFM/PKF — see `docs/bibles-recipe-layer.md`) |

Schemas in `DATA.md`. The **eflomal** method needs *only* the spine + the target USJ — no glosses,
no senses — so the standalone core has minimal inputs.

## Modules
`config` (paths) · `refs` (BBCCCVVV, vendored) · `usj_source` (USJ→verse tokens) ·
`hebrew_source` (spine + hbo.db) · `gloss_priors` · `gloss_align` (gloss method) ·
`stat_align` (IBM-1) · `eflomal_align` (eflomal) · `run_pilot` (runner + report).
