---
license: cc0-1.0
task_categories:
- token-classification
tags:
- morphology
- unsupervised
- multilingual
- bible
---

# target-morphology

Per-language **unsupervised morphology models** — productive suffixes, prefixes, and a stem lexicon,
each learned MDL-free ("Linguistica"-style: a suffix is productive if it attaches to many paradigm stems)
from that language's own Bible text. No labels, no pretrained model, no download — so it runs on any
language with a translation, including those with zero LLM/encoder coverage.

`stem(word)` strips one productive affix when the remainder is a known stem; inflected variants collapse to
a shared stem (e.g. Hindi बोला/बोलता → बोल). Built for the lexeme-aligner (it fills gloss's normalizer and
optionally stems eflomal's input), but published standalone because unsupervised segmentation is reusable.

**CC0-1.0** — models are derived statistics (affix inventories + stem lists), no source text redistributed.
See `manifest.json` for per-language stats + content hashes.

## How good is it? (measured 2026-10-09)

Scored against the SIGMORPHON 2022 word-segmentation dev sets (Batsuren et al. 2022; CC BY-SA 3.0, used for scoring only, nothing
of it is redistributed here). For each test word, the model's one-suffix / one-prefix split is a predicted morpheme boundary; it is
correct when the gold segmentation has a boundary at the same character position (words whose gold morphemes do not spell the surface
form, i.e. allomorphy, are skipped).

| language | words scored | boundaries predicted | precision | share of gold boundaries found |
|---|---|---|---|---|
| ces | 4,000 | 1,779 | 0.82 | 0.14 |
| hun | 56,530 | 6,176 | 0.66 | 0.04 |
| eng | 35,903 | 6,056 | 0.57 | 0.08 |
| rus | 8,549 | 1,074 | 0.55 | 0.06 |
| lat | 86,245 | 3,219 | 0.44 | 0.01 |
| fra | 5,766 | 1,051 | 0.39 | 0.07 |
| ita | 2,986 | 663 | 0.21 | 0.08 |

Read this as a lower bound for dictionary words: the stem lexicon is learned from one Bible translation and most test words never
occur in it, and the model strips at most one affix per side by design (it exists to merge inflected variants for alignment, not to
segment fully). Do not use it as a general-purpose segmenter.
