---
pretty_name: Alignments by Greek word id
tags:
  - bible
  - word-alignment
  - new-testament
  - macula
license: cc0-1.0
---

# alignments-by-id — translations word-aligned to MACULA Greek word ids

New Testament translations word-aligned to the Greek words they translate. Each Greek word is identified by its id in the
**MACULA Greek (Nestle 1904) trees** (`n40001001001` = Matthew 1:1, word 1), so a link can be joined to the trees' syntax
directly. Where the translation writes a function word inside another word (Hebrew ו ה ב ל מ כ ש, pronoun suffixes), the
link points at that **part** of the word.

These are machine alignments. Each link carries a class whose hand-checked precision is in the manifest; use the classes to
choose your own precision.

## Editions

| folder | edition | Greek words linked | not rendered (`none`) |
|---|---|---|---|
| `n1904/h/heb/heb_scg` | Salkinson–Ginsburg Hebrew NT (public domain; bible.helloao.org, eBible `hebsg`), 27 books | 118,166 of 137,779 | 19,580 |

The Shorter Ending of Mark (MACULA `MRK 16:99`, 33 words) is not in Salkinson–Ginsburg; it is listed in the manifest
(`spine_verses_not_in_edition`) and appears in no file.

## The Greek side

`n1904/` holds alignments to the Nestle 1904 text of MACULA Greek (Clear Bible, CC BY 4.0). An id is `n` + book (40–66) +
chapter (3 digits) + verse (3) + word (3), exactly the `xml:id` of the word in the trees; all 137,779 ids were checked against
the trees. `n1904/manifest.json` → `spine` pins the word list the alignment was made on (`spine_content_sha256`).

## File format

```
n1904/<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json
{"C:V": {"links": [[[positions], [ids], class], ...], "none": [ids], "added": [positions]}, ...}
```

- `C:V` is the verse in the **edition's own numbering**. When the edition joins verses, a verse can hold ids of the following verses.
- A position is a token of that verse, by the tokenizer of
  [compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) (`tokenize.js`, tokenizer v2, from
  bcv-commons/bibles verse-text): the integer `6` is the whole token, the string `"6.0"` is part 0 of token 6. Part numbers
  are those of the word analysis named in the manifest (`parts`:
  [bcv-commons/hebrew-nt-analysis](https://huggingface.co/datasets/bcv-commons/hebrew-nt-analysis) `v0.1`, words split only
  when the analysis is sure, `p_split` ≥ 0.9). Part letters can be read from that dataset.
- One link may hold several ids when adjacent Greek words with the same Strong's number were aligned as one unit.
- `none`: Greek words of the verse that the translation does not render. `added`: tokens that render no Greek word.
- A verse's leading verse number (written in Hebrew letters, א, יז …) is a token too; it is never linked and never `added`.
- `<hash>` is the book's text fingerprint, the same name the compact-alignments file of that text has, so a file only matches
  the exact text it was built on.

## Classes

| class | what it means | hand check (heb_scg) |
|---|---|---|
| `kept` | the aligner's link, which the reviewer (Jev) kept | 25/25 |
| `inside` | Jev: a function word rendered inside a word another Greek word owns; the part is the aligner's, or the one part whose class fits | 21/25 |
| `part` | Jev said "not rendered", but the aligner linked the word to a prefix or suffix whose class fits it (conjunction → ו, preposition → ב ל מ כ, article → ה, pronoun → suffix) | 21/25 (47/61 in a separate comparison) |
| `edited` | Jev changed the link, one target word | 25/39 |
| `edited-wide` | Jev changed the link, two or more target words; often a neighbouring word of another Greek word swept in | 17/36 |

`none` was right (the word really has no rendering) in 19/25 checked cases.

Greek words on 2+ target words: 7,428, of which 5,316 are `edited-wide` (see its precision) and 2,112 `kept`.

## How the links were made

1. The standard lexeme-aligner chain (eflomal, gloss, span extension, gap fill) on a **parts view** of the edition: each
   word the analysis splits is given to the aligner as its parts, then the links are folded back to tokens and parts.
2. Jev (TypeSafe), a reviewer model, checked the proposed links of every verse (design v4, threshold 0): keep, change, rendered
   inside another word, or not rendered.
3. The `part` rule, measured on 130 hand-graded cases where Jev said "not rendered" for a prefix the aligner had linked.

Code: `lexeme_aligner.alignments_by_id` (github.com/bcv-commons/lexeme-aligner).

## License

The alignments are CC0 1.0. They hold no text; each edition keeps its own licence (Salkinson–Ginsburg: public domain).
MACULA Greek (CC BY 4.0) supplies the ids; hebrew-nt-analysis (CC BY 4.0) the part numbers. No other value from either is
copied into the files.
