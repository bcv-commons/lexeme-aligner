---
pretty_name: Alignments to the Textus Receptus
tags:
  - bible
  - word-alignment
  - textus-receptus
license: cc0-1.0
---

# alignments-tr — word alignments against the Textus Receptus, precision first

Translations made from the Textus Receptus (TR), word-aligned to the TR Greek words they translate. The links are keyed by the
TR spine's own word ids (`grc-tr:MAT:1` …), not by the Nestle 1904 ordinals of
[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments).

**Precision comes before coverage.** A Greek word is left unlinked rather than linked when the evidence is weak, so many words
(most of them function words) have no link. These are machine alignments: no person has checked them.

## Editions

| edition | text | TR words linked | of them content words |
|---|---|---|---|
| `heb/heb_scg` | Salkinson–Ginsburg Hebrew NT (public domain; bible.helloao.org, eBible `hebsg`), 27 books | 78,506 of 140,856 (55.7%) | 52,535 of 66,424 (79.1%) |

## The Greek side: pin it

The TR word ids are running numbers made by the spine's build (bcv-commons/bcv-query, `shoresh/macula/build_tr_spine.py`, source
github.com/byztxt/greektext-textus-receptus, public domain). They are not ids from the source text: if the spine is rebuilt they
may shift. `manifest.json` → `spine.spine_content_sha256` names the spine every link points into
(`25c5258651578d593e761653e5a7a9dfa3410cbd17789500040b7834d4ebd3e1`, 140,856 words). Check it before use.

## File format

```
<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json
{"C:V": [[[target positions], [TR word ids], tier], ...], ...}
```

- `C:V` is the verse in the **edition's own numbering**. A link can point at TR words of a following verse when the edition joins
  verses.
- Target positions index the tokens of that verse, by the tokenizer of compact-alignments (`tokenize.js`, tokenizer v2, from
  bcv-commons/bibles verse-text). `<hash>` is the book's text fingerprint, the same name the compact-alignments file of that text
  has, so a file only matches the exact text it was built on.
- One link may hold several TR ids when the aligner treated adjacent words with the same Strong's number as one unit
  (e.g. ἀμὴν ἀμήν).
- A verse's leading verse number written as Hebrew letters (א, יז …) is a token too, but is never linked.

## How the links were chosen

eflomal was run five times against the TR spine. A link is kept in one of three tiers:

| tier | rule | hand check (25-link samples) |
|---|---|---|
| `stable` | the same link in all five runs (content or function word) | content 73/75, function 24/25 |
| `rescue` | a content-word link in fewer runs that the LXX bridge confirms | ~23/25 |
| `bridge` | a content word eflomal did not link, whose Strong's number is unique in the verse and in the bridge set of exactly one target word | ~23/25 |

The **LXX bridge** does not depend on eflomal. A target word's Hebrew lexemes come from two places, and the union of both is used:
its pointed form looked up among the word forms of the MACULA Hebrew OT, and the word analysis in
[bcv-commons/hebrew-nt-analysis](https://huggingface.co/datasets/bcv-commons/hebrew-nt-analysis) (`v0.1`; tagger words only when
`p_split` ≥ 0.9). The lexemes are then linked to their Greek Strong's numbers through the Septuagint (bcv-commons prior-pack,
`lxx_greek`). `manifest.json` names the analysis file and its sha256. Everything else stays unlinked: links in only 3–4 runs were right about 14/25 (content) and 16/25
(function word) times. Code: `lexeme_aligner.tr_align` (github.com/bcv-commons/lexeme-aligner).

## License

The alignments are CC0 1.0. They hold no text; each edition keeps its own licence (Salkinson–Ginsburg: public domain). The TR
spine, MACULA Hebrew (Clear Bible / Biblica, CC BY 4.0) and hebrew-nt-analysis (CC BY 4.0, trained on MACULA Hebrew) are used
as inputs only, to choose links; no value from them is copied into the files.
