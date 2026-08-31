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
("God", "Lord") are never dropped.

A candidate word is rescued out of the list (judged a real content word, not a function word) only when
**all four** hold — see the lexeme-aligner's `target_stopwords.py`:

1. it carries at least **25 aligned occurrences** — a share read off one or two observations is noise,
   and function words are systematically under-aligned, so thin aligned mass is itself evidence;
2. its **dominant Hebrew/Greek lexeme holds ≥40%** of its aligned mass — a true function word instead
   scatters thinly across dozens or hundreds of distinct lexemes;
3. that lexeme is marked **content** in the source-side prior pack;
4. that lexeme is not itself **semantically light** (`config/light_lexemes.json`). Copulas, *have*,
   quantifiers, negators, possessives, modals and the Hebrew nouns grammaticalised into prepositions
   (*panim* → "before", *yad* → "by") are all correctly rendered by target FUNCTION words, so aligning
   to one proves nothing about content-hood — without this the lists lost *is/are*, *all*, *one*, *no*,
   *can*, *before*.

Many of the covered languages have **no existing curated stopword list anywhere** — this is a reusable
resource for search, IR, topic modeling, or any NLP task needing one in these languages.

Languages written in scripts without whitespace word separation (Han, Japanese, Myanmar) are segmented
by rule — Han per character, Japanese at script boundaries, Myanmar per syllable — with no segmenter
model or download, so the method still runs on any language that has a Bible and nothing else.

**CC0-1.0** — derived word-frequency statistics, no source text redistributed. See `manifest.json` for
per-language stats + content hashes.
