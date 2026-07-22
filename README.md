# lexeme-aligner

Word-level alignments between Bible translations and their **Strong's-tagged Hebrew/Greek original**,
growing in language coverage over time — plus derived datasets mined from the same alignment work (word
senses, target-language stopword lists, cross-lingual structural stats). All published, all free to use.

**Just want the data? Start here — no installation, no cloning, no Python package needed.**

## The main dataset: `lexeme-alignments`

For each language: every target-language word-form (`surface`) attested for each original-language
**lexeme**, with frequency and confidence. [**bcv-commons/lexeme-alignments**](https://huggingface.co/datasets/bcv-commons/lexeme-alignments) · CC0-1.0

```python
import pandas as pd
df = pd.read_parquet("hf://datasets/bcv-commons/lexeme-alignments/iso=fra/data.parquet")
df[df.lexeme == "grc:26"]   # ἀγάπη (agape/love) — every French rendering we found, with counts
```

or with `pyarrow` directly:

```python
import pyarrow.parquet as pq
t = pq.read_table("hf://datasets/bcv-commons/lexeme-alignments/iso=fra/data.parquet")
```

See [`manifest.json`](https://huggingface.co/datasets/bcv-commons/lexeme-alignments/blob/main/manifest.json)
for the current, always-up-to-date list of published languages — new ones are added regularly, so
check there rather than trusting a list in prose. Each row is tagged with **how** it was found
(`method`) and **which edition** (`base_text`) — nothing is silently merged away, so you can filter for
exactly the confidence level you need.

**Full schema, worked examples, and the four companion reference files (light-lexeme list, Strong's
edge-case tables, the disagreement-resolution rule) are documented in the dataset's own README:**
[**lexeme-alignments/README.md**](lexeme-alignments/README.md) — read that first if you're building
anything on this data. It covers:
- the exact schema and how to derive `strong`/`share` (dropped from storage — they're pure functions
  of the other columns, kept out to shrink the dataset ~32%)
- how to read the `method`/`base_text` provenance tags, and how to use cross-method / cross-edition
  agreement as a confidence signal
- the companion resource files, and honestly which ones you can use standalone vs. which need extra
  data you likely don't have

## Companion datasets

Mined as a byproduct of the same alignment work — each is its own standalone, citable resource:

| dataset | what it is | license |
|---|---|---|
| [`senses-attested`](https://huggingface.co/datasets/bcv-commons/senses-attested) | which word-sense (of several a Strong's number can carry) each rendering attests | CC-BY |
| [`target-stopwords`](https://huggingface.co/datasets/bcv-commons/target-stopwords) | induced function-word lists per target language — many have no other curated list anywhere | CC0-1.0 |
| [`target-morphology`](https://huggingface.co/datasets/bcv-commons/target-morphology) | learned per-language morphology (prefixes/suffixes) used internally for alignment | CC0-1.0 |
| [`cross-lingual-span-profile`](https://huggingface.co/datasets/bcv-commons/cross-lingual-span-profile) | per-lexeme "does this typically need one word or a phrase," aggregated across every aligned language | CC0-1.0 |

## License

`lexeme-alignments` and most companions are **CC0-1.0** — derived counts and statistics, not the
running text of any translation, so no copyrightable expression is reproduced. Each dataset's own
README has the exact terms; `lexeme-alignments/manifest.json` links each language's source-text
license (we never copy source text, only point to where its terms live).

## Building or extending this yourself

The above is everything most people need. If you want to run the alignment pipeline, add a new
language, or understand how the data is produced: see [**docs/architecture.md**](docs/architecture.md).
