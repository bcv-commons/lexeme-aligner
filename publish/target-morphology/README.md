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
