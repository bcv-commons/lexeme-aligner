---
license: cc0-1.0
tags:
- alignment
- multilingual
- bible
- interlingua
---

# cross-lingual-span-profile

A per-**MACULA-lexeme** structural profile — span length / multi-word tendency — aggregated across every
language the lexeme-aligner has aligned. Every language anchors to the same lexeme, so this is a
language-independent INTERLINGUA signal: it tells you whether a Hebrew/Greek lexeme typically needs a
single target word or a multi-word phrase (compound place names — "Kadesh Barnea" — compound numbers —
"four thousand"), based on what OTHER languages actually did, with NO target-language model for the
language you're applying it to.

`n_langs` = how many independent languages (editions of the same language pooled first, so a 2-edition
language doesn't out-vote a 1-edition one) attest the lexeme; `multiword_rate`/`span_mean` = the per-
language-averaged span statistics. Confidence scales with `n_langs` — refresh as more languages are
aligned (see the lexeme-aligner's `cross_lang_prior.py`).

**CC0-1.0** — derived alignment statistics, no source text redistributed.
