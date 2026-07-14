---
license: cc0-1.0
task_categories:
- text-classification
tags:
- stopwords
- unsupervised
- multilingual
- bible
---

# target-stopwords

Per-language **function-word lists**, induced from that language's own Bible text — frequency +
dispersion (the classic corpus-linguistics stopword-induction recipe), then RESCUED against the
language's own alignment output + a source-anchored content signal so genuinely frequent CONTENT words
("God", "Lord") are never dropped. See the lexeme-aligner's `target_stopwords.py` for the mechanism: a
candidate is rescued if it concentrates most of its aligned mass on one prior-pack content lexeme (≥40%
share) — a true function word instead scatters thinly across dozens of distinct lexemes.

Many of the covered languages have **no existing curated stopword list anywhere** — this is a reusable
resource for search, IR, topic modeling, or any NLP task needing one in these languages.

**CC0-1.0** — derived word-frequency statistics, no source text redistributed. See `manifest.json` for
per-language stats + content hashes.
