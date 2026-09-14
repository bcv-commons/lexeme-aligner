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
each learned from that language's own Bible text with no labels, no pretrained model, and no download —
so it runs on any language with a translation, including those with zero LLM/encoder coverage.

The method is **MDL-free**: it's "Linguistica"-*style* in spirit (signature-based paradigm induction —
a suffix is productive if it attaches to many shared stems) but, unlike Goldsmith's original Linguistica,
does NOT compute a minimum-description-length score to select the model. Instead it uses a direct
productivity threshold (an affix counts only if it attaches to enough distinct real stems) — simpler,
and fast enough to run per-language on demand.

`stem(word)` strips one productive affix when the remainder is a known stem; inflected variants collapse to
a shared stem (e.g. Hindi बोला/बोलता → बोल). Built for the lexeme-aligner (it fills gloss's normalizer and
optionally stems eflomal's input), but published standalone because unsupervised segmentation is reusable.

**CC0-1.0** — models are derived statistics (affix inventories + stem lists), no source text redistributed.
See `manifest.json` for per-language stats + content hashes.
