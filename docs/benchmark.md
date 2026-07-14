# Gold benchmark — alignment modes vs manual references

`lexeme_aligner.benchmark` scores **any alignment mode** (`--method gloss|stat|eflomal|…`, reading
`align_<method>_<iso>_*.jsonl`) against a manual gold, with two backends (`--gold`):

- **`clear`** — Clear-Bible attestations (surface→Strong's, per-occurrence manual). Metric: token-
  weighted **top-1 accuracy**. For the ~10 Clear gold languages (fra, hau, …).
- **`lexicon`** — a hand-curated **Strong's→translation** lexicon (e.g. karnbibeln.se for Swedish).
  Metric: **stem-overlap agreement** between our top surface per Strong's and the manual gloss.

Both are type/lexicon-level and ref-agnostic (sidestepping versification + tokenization). So far only
the **statistical** mode (eflomal) has been scored; gloss/gapfill/merged are runnable through the same
tool once produced.

## Clear backend — eflomal vs Clear-Bible alignments

The statistical spine (eflomal) is validated against **human-made** alignments: **91.8% (fra) /
95.6% (hau)** token-weighted top-1 accuracy. Both are NT runs scored against the Clear-Bible manual
gold; Hausa — a distant, lower-resource language — scores *higher* than French, so the method
generalizes cleanly rather than overfitting a high-resource case. **This clears the promotion gate:**
eflomal is trustworthy for no-gold languages (e.g. Indonesian).

## Results (verified 2026-07)

| iso | language | books | top-1 (token-weighted) | top-1 (per type) | gold coverage (token-wtd) |
|---|---|---|---|---|---|
| **fra** | French (Louis Segond) | 27 (NT) | **91.8%** | 78.6% | 95.4% |
| **hau** | Hausa (OHCB) | 27 (NT) | **95.6%** | 86.4% | 95.3% |

- **fra:** produced 7,879 surfaces · gold 8,401 · shared 6,999.
- **hau:** produced 4,805 surfaces · gold 5,893 · shared 4,648.

*Token-weighted* is the headline: it weights each surface by how often it occurs, so the score
reflects running text rather than the long tail of rare word types. *Per-type* is the unweighted
per-word-form number (the rare tail drags it down but matters less in practice). *Coverage* is how
much of the gold vocabulary the aligner also committed to.

## Recipe (reproduce)

```bash
# 1. produce the alignment (NT, French Louis Segond)
python -m lexeme_aligner.run_pilot --method eflomal --nt --usj-dir data/usj-fra-lsg --iso fra
# 2. score it against the manual gold
python -m lexeme_aligner.benchmark --iso fra --tag eflomal        # add --misses to inspect errors
```
Same for `--iso hau --usj-dir data/usj-hau-ohcb`. The benchmark reads
`$ALIGNER_OUT/align_eflomal_<iso>_*.jsonl` (from step 1) and
`$ALIGNER_RESOURCES/strongs/attestations/<iso>.{tsv,parquet}` (the gold).

## Lexicon backend — Swedish (karnbibeln.se / Kärnbibeln)

A second, independent validation against a **hand-curated Strong's→Swedish lexicon** (Greek 5,607 +
Hebrew 7,577 glosses). Two runs: **Kärnbibeln** (`swe_svk`, the lexicon's *own* translation — isolates
alignment quality) and **Folkbibeln** (`swe_fol`, a *different* translation — adds a cross-translation
check). Agreement = stem overlap (Snowball SV) between our top surface per Strong's and the manual gloss.

| run | Greek (NT), count ≥ 10 | Hebrew (OT), count ≥ 10 |
|---|---|---|
| **swk** — Kärnbibeln vs its own lexicon | **~92%** | **~87%** |
| **swe** — Folkbibeln vs Kärnbibeln lexicon | **~92%** | **~79%** |

**Greek ~92% for both translations** → the agreement is alignment-driven, not translation-specific
(Folkbibeln, aligned independently, matches the lexicon as well as Kärnbibeln does). The ~8-pt Hebrew
gap between the two is genuine *translation choice* (different Swedish OT words), cleanly separated from
alignment quality by having both runs. The number is a conservative floor — a lexical matcher can't see
synonyms or vowel-alternations (`man/män`), so true agreement is higher.

```bash
python -m lexeme_aligner.pipeline --source helloao --translation swe_svk --iso swk --all   # align
python -m lexeme_aligner.benchmark --gold lexicon --iso swk --method eflomal --testament greek --min-count 10
```
Needs the `[validate]` extra (`pyarrow` + `snowballstemmer`). The lexicon is fetched from karnbibeln.se
and cached under `data/karnbibeln/` (git-ignored); disagreements are written to a sorted md report.

## What is being measured — and what isn't

The benchmark is **type-level and reference-agnostic**: it does *not* match by verse ref or token id.
On each side it builds `target-surface → Strong's` and asks whether the aligner assigns a Strong's a
human also assigned to that surface (top-1 = the aligner's argmax Strong's is among the gold's for
that surface). That deliberately sidesteps the two things that would otherwise make a cross-edition
benchmark meaningless:

- **versification** (Hebrew vs Protestant numbering) — irrelevant when not matching by ref;
- **tokenization** differences between the aligned edition and Clear's base text.

The price: it scores the learned **lexicon** (does the method recover the right dictionary?), not
per-occurrence disambiguation. For a first gold signal that is exactly the right question.

## Gold provenance

`data/resources/strongs/attestations/<iso>.{tsv,parquet}` — per-occurrence **manual +
machine-transfer** alignments for ~10 languages, from
[Clear-Bible/Alignments](https://github.com/Clear-Bible/Alignments). Provided out-of-band and
git-ignored (see `data/PROVENANCE.txt`); the `method` flag on each row marks manual vs transfer.
