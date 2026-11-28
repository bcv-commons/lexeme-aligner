# bcv-commons split engine

Splits a written word into parts (prefixes, stem, infixes, reduplication, tone, suffixes, clitics) from a `bcv-segfacts/0.1` bundle
(`seg_facts.json`). It is deterministic: the same bundle and word give the same parts, on any text. It is one file and stdlib only.

```python
import bcv_segment
seg = bcv_segment.load("path/to/bundle")      # the folder holding seg_facts.json
seg.split("naáyaandika")                      # [{"slot", "form", "text", "position", "chain_id"}, ...]
seg.S.split("naáyaandika", tones=True)        # the Segmenter's own Morph tuples
```

`bcv_segment.py` is a drop-in for the `bcv_segment.py` + `morphs.py` pair inside a bundle.

## Pinning

Pin a version by its folder and `SHA256SUMS`. Record `unicodedata.unidata_version` with any split you publish: the engine reads NFD
clusters and combining classes.

## Who maintains it

- **lexeme-aligner** maintains the engine and the format. We find splits for every language in our fleet and decide per edition, with a
  gold-free alignment check, whether parts or whole words align better.
- **make-tanakh** wrote the engine. It pins this engine instead of carrying its own copy, and keeps its tools for making bundles.

## Change rules

- Every published bundle keeps splitting byte-identically within a major version (checked before each release).
- Format changes are additive and announced before release.
- A change that alters any existing bundle's output gets a new major version, and the old version stays available.

## Credit and licence

MIT (`LICENSE`). Engine by the make-tanakh authors (bcv-commons/make-tanakh, `scripts/morphs.py`); see `CHANGELOG.md` for the exact
source.
