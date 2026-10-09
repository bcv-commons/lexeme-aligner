---
pretty_name: Full alignments — manual (gold) layers, share-alike
tags:
  - bible
  - word-alignment
  - interlinear
license: cc-by-sa-4.0
---

# full-alignments-manual-sa — Door43 hand-made word alignments in the compact container format

The share-alike part of [full-alignments-manual](https://huggingface.co/datasets/bcv-commons/full-alignments-manual): word
alignments made by translators in **unfoldingWord / Door43** aligned editions (Gateway Language Translations), converted onto the
same source spine and written in the [compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) format.
Kept in its own repository because its licence is share-alike.

## Layers

A layer id is `<edition>+manual+door43`, e.g. `h/hin/hin_higlt+manual+door43/`. The target text is the Door43 edition itself
(git.door43.org, CC BY-SA 4.0); positions refer to that text, tokenized with compact-alignments' `tokenize.js`. The list of
layers, with rows and books, is in `manifest.json`. Greek NT links are mapped by Strong's number and occurrence; Hebrew OT links
(where a Door43 edition has OT books) by a prefix crosswalk that maps about 82% of spans; compound prefix codes are left out.

## File format

Every layer is a folder `<iso[0]>/<iso>/<layer id>/` with per-book files that look exactly like
[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) files and add channels for what compact
does not carry. No file holds a word of any text: every target reference is a token position in the edition's own verse text
(tokenized with compact-alignments' `tokenize.js`), every source reference an ordinal into compact-alignments' shared `_index/` files.

```
<BOOK>_<hash>.json       main: one string per spine verse, position-parallel to _index/<BOOK>_lexemes.json
                         "srcOrd:span ..." = the winning link of each CONTENT source token (compact's own meaning)
<BOOK>_<hash>.meta.json  channels (a verse's entry is "" when empty; null = the channel has nothing in this book)
_layer.json              attribution constants and the profile table (char -> method / score / prior / extra / attribution)
```

`<hash>` = the last 5 hex characters of the book's content hash (compact-alignments `book_content_hash`), so a file only matches
the exact text it was built on.

**Channels** (in `.meta.json`):

| channel | content |
|---|---|
| `fn` | `"fnOrd:span ..."` links of FUNCTION-WORD source tokens (articles, prepositions, suffixes …; `_index/<BOOK>_fn.json` lists them in order) |
| `wp` | one profile character per main entry; `.` = a view of a multi-source row whose row is in `rows` |
| `fp` | the same per `fn` entry |
| `wx` | per entry, space separated: `_` or `;`-joined parts: `t<ints>` the gold's own target-token numbers, `S<id>` a source id other than the token's spine key, `P<ints>` the profile's arguments. Main entries first, then fn entries |
| `rows` | `"srcs:span:prof[:ext]..."` every row that is not a claimed winner: losing alternatives, multi-token source attachments, unplaced rows, markers, joint rows |
| `off` | `{ref: [row entries]}` rows with no spine source token (e.g. a table row whose source word is not in the Nestle 1904 / WLC spine) |

**Slots:** `c<N>` = content token srcOrd N · `f<N>` = function word fnOrd N · `p<N>` = token N of a verse folded into another
verse's group (two source verses = one target verse).

**Spans:** `5`, `5-7` contiguous, `5,9` scattered, `L3,1,2` an out-of-order list, `~` unplaced (verse not mappable), `!` explicitly
empty, `@6e` / `@-1u` a MARKER: the source word has no target word of its own and sits after target word 6 (-1 = verse start);
kind `u` = untranslated (BSB `-`), `e` = rendered elsewhere (BSB `. . .`).

**Row extensions:** `k<i>` index (into the srcs) of the keyed token · `v` joint with the following phrase (BSB `vvv`) ·
`s<pos,..>` target words the editor SUPPLIED (BSB `[x]`, not in the span) · `i<pos,..>` inflection words made explicit (BSB `{x}`,
in the span, flagged) · `t<ints>` target-token numbers · `S<id>` source id · `T<strong>` the row's own Strong's number when it
differs from the token's · `P<ints>` the profile's arguments.

**Profile arguments:** a number inside a row's `prior` is per-row data (e.g. span extension's `spanext_name_before:12` = the target
position the rule appended). The profile table stores the prior with `#` in its place and the numbers travel with the row
(`P12`), in order; put them back to get the original prior.

**Vetoed rows** (statistical layer only): a row whose profile `extra` carries `"veto": "<check>"` was judged wrong by a word-level
check (a pronominal suffix aligned far from its noun, a prefixed preposition on the wrong side for the language). It is never
claimed into `fn` and never shown as a view; it stays whole in `rows`.

**Derived on decode, never stored:** lexeme / Strong's / lemma / stem / surface / gloss (from the source spine via `_index/`) and
the target words (from the positions and the edition text). No BHSA-derived field is carried.

## License

CC BY-SA 4.0, from the Door43 sources: "unfoldingWord® / Door43 contributors, CC BY-SA 4.0". Each layer's `_layer.json` names
its edition. Derivative works must be shared under the same licence.
