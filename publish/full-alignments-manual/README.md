---
pretty_name: Full alignments — manual (gold) layers
tags:
  - bible
  - word-alignment
  - interlinear
license: cc-by-4.0
---

# full-alignments-manual — hand-made word alignments in the compact container format

Every word of a verse, linked to the Hebrew or Greek source word it translates, as made **by people** (no statistics, no LLM):
one layer per published gold source and edition. Function words (articles, prepositions, pronoun suffixes) are included, and so are
the cases where a source word has no target word of its own (markers) or a target word was supplied by the editor.

The files use the format of [compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) (same paths,
same `_index/`, same tokenizer), so a compact reader works on them unchanged; the extra channels are described below.
The statistical full alignment of the same editions (every row of every method) lives in
[compact-alignments-meta](https://huggingface.co/datasets/bcv-commons/compact-alignments-meta), merged into each book's
`.meta.json` and decoded with that edition's `_layer.json`.

## Layers

A layer id is `<edition>+manual+<source>`, where `<edition>` is the compact-alignments edition id of the same text, e.g.
`e/eng/eng_BSB+manual+clear/` and `e/eng/eng_BSB+manual+bsb-tables/`. The list of layers, with rows and books, is in `manifest.json`.

| source | what | licence |
|---|---|---|
| `clear` | Clear Bible alignments (github.com/Clear-Bible/Alignments): Hebrew OT by word id, Greek NT by Strong's number and occurrence | CC BY 4.0 |
| `clear-split` | the same Clear Hebrew OT links, each word's link divided among its morphemes by a stated rule (below); OT only | CC BY 4.0 |
| `helfi` | HELFI Finnish (University of Helsinki), morpheme-level | CC BY 4.0 |
| `sword` | SWORD ChiUns (Chinese Union Version) | Public Domain |
| `bsb-tables` | Berean Standard Bible translation tables (the publisher's own interlinear): markers, joint phrases, supplied and inflection words | CC0 |

Each layer's `_layer.json` repeats its source, licence and base text. Left out on purpose: the Clear Russian Synodal gold
(positionally wrong in the source itself), the Clear Portuguese layer (machine-projected by Clear, not hand-made), and gold whose
licence does not allow redistribution. Door43 (CC BY-SA 4.0) layers are in the separate
[full-alignments-manual-sa](https://huggingface.co/datasets/bcv-commons/full-alignments-manual-sa).

## File format

Every layer is a folder `<iso[0]>/<iso>/<layer id>/` with per-book files that look exactly like
[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) files and add channels for what compact
does not carry. No file holds a word of any text: every target reference is a token position in the edition's own verse text
(tokenized with compact-alignments' `tokenize.js`), every source reference an ordinal into compact-alignments' shared `_index/` files.

```
<BOOK>_<hash>.json       main: one string per spine verse, position-parallel to _index/<BOOK>_lexemes.json
                         "srcOrd:span ..." = the winning link of each CONTENT source token (compact's own meaning)
<BOOK>_<hash>.meta.json  channels (a verse's entry is "" when empty; null = the channel has nothing in this book)
_layer.json              version, flags (`lists`, `keyed`), the `spine_index_sha256` of the `_index/_source.json` the layer was
                         built against, attribution constants and the profile table (char -> method / score / prior / extra / attribution)
```

`<hash>` = the first 8 hex characters of the book's text fingerprint (compact-alignments README, "The text fingerprint"), the same
name the compact file of that text has, so a file only matches the exact text it was built on.

**Channels** (in `.meta.json`):

| channel | content |
|---|---|
| `fn` | `"fnOrd:span ..."` links of FUNCTION-WORD source tokens (articles, prepositions, suffixes …; `_index/<BOOK>_fn.json` lists them in order), plus their decision lists and the verse's `+` entry (below) |
| `wp` | one profile character per main entry; `.` = a view of a row kept in `rows`; `-` = a `+` entry whose words belong to their owner's link |
| `fp` | the same per `fn` entry |
| `wx` | per entry, space separated: `_` or `;`-joined parts: `t<ints>` the gold's own target-token numbers, `S<id>` a source id other than the token's spine key, `P<ints>` the profile's arguments, `R<ids>` the source table's row id (BSB tables: `BSB Sort`; a list entry has one per item, comma-joined in ordinal order, `-` = none). Main entries first, then fn entries |
| `rows` | `"srcs:span:prof[:ext]..."` every row that is not a claimed winner: losing alternatives, multi-token source attachments, unplaced rows, markers, joint rows |
| `off` | `{ref: [row entries]}` rows with no spine source token (e.g. a table row whose source word is not in the Nestle 1904 / WLC spine) |

**Slots:** `c<N>` = content token srcOrd N · `f<N>` = function word fnOrd N · `p<N>` = token N of a verse folded into another
verse's group (two source verses = one target verse).

**Spans** (links and `rows`): `5`, `5-7` contiguous, `5,9` scattered, `L3,1,2` an out-of-order list, `~` unplaced (verse not
mappable), `!` explicitly empty, `@6e` / `@-1u` a MARKER in `rows`: the source word has no target word of its own and sits after target
word 6 (-1 = verse start); kind `u` = untranslated (BSB `-`), `e` = rendered elsewhere (BSB `. . .`).

**Decision lists** (layer version 3, `"lists": true` in `_layer.json`). A source token or target word that the `main` / `fn`
strings do not mention is **not decided**. Every other decision is a short list entry in the same strings, written with the span
syntax (numbers, `4-5` ranges, commas); each entry takes one profile character in `wp` / `fp` and one `wx` entry, like a link.

| entry | meaning | example |
|---|---|---|
| `!<ordinals>` | these source tokens have no target word | `!6`, `!0,4-5` |
| `u<ord>[@<pos>],…` | untranslated (BSB `-`) | `u9`, `u9@10` |
| `e<ord>[@<pos>],…` | rendered elsewhere (BSB `. . .`) | `e6` |
| `+<pos>[><ord>],…` | `fn` only: these target words were SUPPLIED by the editor (BSB `[x]`), for content token `<ord>` when given (`>f<n>` = function token), else for the grammar. They belong to that token's link: read a link's span together with the `+` words naming its token | `+1>5,10,29` |

`!`, `u` and `e` go in the string of the tokens they list: content ordinals in `main`, function ordinals in `fn`.
`@<pos>` is the **slot** of a source word with no target word of its own (it sits after target word `<pos>`, `-1` = verse start), for
reverse interlinears. It is written only where the slot differs from the **default slot**, which a reader computes from the
`main` / `fn` strings alone: take the nearest *preceding* source token (source order = `_index` key order) for which the strings show
target positions — its link's span (a view counts) plus the `+` words that name it as owner — and the slot is after the last of
those positions; `-1` if no preceding token shows any. On the BSB tables the default holds for 69% of untranslated and 82% of rendered-elsewhere marks; the rest are
word-order changes (Ruth 1:6: אֶת sits after "attended to", not after "the LORD").

A verse is **complete** when every ordinal of `_index/<BOOK>_keys.json` and `_fn_keys.json` appears (in a link or a list) and every
target position is covered (by a span or by `+`); no flag is needed. Example, Ruth 1:6 in `eng_BSB+manual+bsb-tables`:

```
main  "0:24 1:19-23 2:25-26 3:27-28 4:29-30 5:2 7:3-4 8:8-10 9:6-7 10:11-12 11:13-14 12:16-17 e6"
fn    "0:24 1:18 2:19-23 3:25-26 4:25-26 5:27-28 6:0 8:5 10:13-14 11:13-14 12:15 13:15 u9@10 e7 +1>5"
```

(c6 שְׂדֵה rendered elsewhere at the default slot; f9 אֶת untranslated after word 10; "Naomi", word 1, supplied for c5 "she had heard".
Entries with profile `.` are views of multi-token BSB rows, whose full data stays in `rows`.)

**Keyed layers** (`"keyed": true`, the BSB tables): a row with one source token has that token as its keyed token unless the row says
`k-`; `k<i>` is written only for rows with several source tokens.

**Row extensions:** `k<i>` index (into the srcs) of the keyed token · `v` joint with the following phrase (BSB `vvv`) ·
`s<pos,..>` target words the editor SUPPLIED (BSB `[x]`, not in the span; they also show in the verse's `+` entry under the row's token) · `i<pos,..>` inflection words made explicit (BSB `{x}`,
in the span, flagged) · `t<ints>` target-token numbers · `S<id>` source id · `T<strong>` the row's own Strong's number when it
differs from the token's · `P<ints>` the profile's arguments · `R<id>` the source table's row id (BSB tables: `BSB Sort`).

**Profile arguments:** a number inside a row's `prior` is per-row data (e.g. span extension's `spanext_name_before:12` = the target
position the rule appended). The profile table stores the prior with `#` in its place and the numbers travel with the row
(`P12`), in order; put them back to get the original prior.

**Vetoed rows** (statistical layer only): a row whose profile `extra` carries `"veto": "<check>"` was judged wrong by a word-level
check (a pronominal suffix aligned far from its noun, a prefixed preposition on the wrong side for the language). It is never
claimed into `fn` and never shown as a view; it stays whole in `rows`.

**Derived on decode, never stored:** lexeme / Strong's / lemma / stem / surface / gloss (from the source spine via `_index/`) and
the target words (from the positions and the edition text). No BHSA-derived field is carried.

## The Clear split layer

Clear aligns the Hebrew OT word by word: בִּימֵי → "in the days" is one link on the word, which never says that "in" is the prefix's.
`eng_BSB+manual+clear-split` divides each such link among the word's MACULA morphemes (`lexeme_aligner.eval.gold_split`): inside the
gold span a prefix takes the leftmost word its fixed list or its MACULA gloss allows (וְ and/but/then…, הַ the, בְּ in/with/by…, לְ
to/for…), a pronoun suffix the rightmost (his, them, you…), the content morpheme keeps the rest; a morpheme with no such word gets an
explicit no-rendering (`!`). Each row's profile `prior` says how it was made: `gold_split:word` (a one-morpheme word, copied),
`gold_split:list` / `gold_split:gloss` (the prefix's or suffix's word, by the list or by the gloss), `gold_split:none`,
`gold_split:rest` (the content morpheme). Rows keep Clear's word id (`S<id>` in `wx`).

Counts (BSB, 39 books, 23,146 verses): 135,695 one-morpheme rows, 132,502 content morphemes, 73,478 prefix/suffix words by the list,
9,641 by the gloss, 66,527 with no word of their own; in 6,068 words the prefix took the span's only word and the content morpheme
was left with none (idioms such as בְּיַד "by"). Clear has no morpheme links to check this against; a hand check of 30 random split
words found the split right wherever the gold span itself was. The unsplit links stay in `eng_BSB+manual+clear`.

## The BSB tables layer

`eng_BSB+manual+bsb-tables` is the publisher's own interlinear (bereanbible.com `bsb_tables.tsv`), one table row per source word and
English chunk, converted without guessing (`lexeme_aligner.eval.bsb_tables`):

- **Source word.** Table rows (Heb Sort / Greek Sort order) are aligned in order to the MACULA words of the verse, on Strong's
  number and spelling together, and a row takes every token of its word (a Hebrew word's prefix, stem and pronoun suffix). The
  row's `attribution.match` (the profile) says how: `strong` (same Strong's number), `spelling` (same place and spelling, another
  number: the table's G2036 where MACULA has G3004, ketiv/qere, a row with no number), `fused` (see below), `none` (a word of
  another edition, not in Nestle 1904 / WLC: the row has no source token and is in `off` or `rows`).
- **Supplied words** (`[x]` in the table) are the `+` words of their row's token, and `s<pos>` in a row kept in `rows`.
- **Untranslated** (`-`) and **rendered elsewhere** (`. . .`) rows are `u` / `e` markers, never links.
- **Row ids.** Every link, list item, `+` row and `rows` entry carries its table row's `BSB Sort` as `R<id>` (channel `wx` /
  row extension), so a link can be checked against the table.
- **Text basis.** `_basis.json` lists per verse the table words that are not in the spine (`table_only`, by row id) and the spine
  tokens no table row covers (`spine_only`, by slot): a difference of text, not a missed link.
- **Two words on one token.** The spine joins some adjacent words into one token (repeated names "Ἰσαάκ, Ἰσαὰκ" in MAT 1:2, compound
  names "בֵּית אֵל"). Both table rows sit on that token: the first is its link, the second is in `rows` (match `fused`, its own
  `R`), with its supplied words too (`s<pos>`; they are not `+` words of the token); read both rows for the token's words. In
  general a row's supplied words appear as `+` words only beside a token that shows that row.

Revised 2026-10-26 (asked for by bsb-add-books): before, rows were matched by (Strong's number, k-th occurrence) and a Hebrew prefix or
suffix was attached to a neighbouring row. That swapped repeated numbers (εἶπεν / λέγω, εἶδον / ἰδοὺ, genealogy names) and gave
most OT pronoun suffixes to the next word (16% of 31,541 suffix morphemes sat in their own word's row; now all 34,049); Aramaic rows
(Daniel, Ezra) were read without their Strong's numbers and out of order (3,605 rows of Daniel had no source word; now none); a row
whose words were all supplied ("[wife]") showed them only in `rows`. Checked against the table itself: for 99.8% of source words the
`main`/`fn` strings, read with their `+` words, now give exactly the table's words (97.5% before); the rest (712) are the two-words-on-
one-token case above.

## Verse mapping

Spans are keyed by the source verse (Hebrew WLC numbering in the OT); read the target words from the edition's mapped verse
(compact-alignments README, "Verse numbering"). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(STEPBible Data, Tyndale House, Cambridge — CC BY 4.0, https://github.com/STEPBible/STEPBible-Data).

## License

Each layer keeps its source's licence (table above); the dataset as a whole is CC BY 4.0. Attribute the source named in the
layer's `_layer.json`.
