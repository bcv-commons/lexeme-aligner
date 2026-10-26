# Split format (`bcv-split/0.1`): a translation's words divided into parts

Draft 2026-10-26, written with bsb-add-books. A **split file** gives, for every token of one edition, the parts it is made of
(prefixes, stem, suffixes, infixes, a grammatical tone), so that an aligner can align the parts and fold the links back to the
written words. Every producer writes the same format: a language store (bsb-add-books' `morphs.py export-split`), a part tagger
(bcv-commons/hebrew-nt-analysis), or our own segmenter once there is one. The validator is
`python -m lexeme_aligner.split_format check <dir>`.

With a split file, an edition gets **two alignments**: one by written words (as today) and one by parts (`target_parts.py`).

## Files

```
<edition>/_split.json       header (below)
<edition>/words.parquet     one row per part
```

`_split.json`:

| field | meaning |
|---|---|
| `spec` | `bcv-split/0.1` |
| `edition` | the edition id (the compact-alignments edition, e.g. `heb_scg`) |
| `tokenizer_version` | the bcv tokenizer version whose tokens `i` and `token` follow (today `2`). A file for another version is a different file. |
| `source` | the default producer: name, version, project, date (e.g. `bsb-add-books store, zinza-nt, 2026-11-02`); a row may name its own |
| `basis` | the default basis (below); a row may give its own |
| `sha256` | sha256 of `words.parquet` |
| `text_licence` | the edition's text licence. **The file holds the edition's words** (`word`, `text`), so it is published only where that licence allows; otherwise it stays with its producer (local mode). |
| `books` | the books covered |
| `text_fp` | `{BOOK: hash}`: each book's text fingerprint, the 8-hex hash compact-alignments names its files with (compact README, "The text fingerprint", version `text_fp_version`). A reader without the text can then tell that a file was made on another text of the "same" edition (a newer working copy, other books). |
| `text_fp_version` | the fingerprint version (today `1`) |
| `date` | when the file was written |

## Rows (`words.parquet`)

One row per part, in reading order. A token that is not divided has one row (slot `whole`).

| column | type | required | meaning |
|---|---|---|---|
| `book`, `chapter`, `verse` | str, int, int | yes | the edition's own verse numbering (as in its USJ) |
| `i` | int | yes | the token's number in the verse, by `tokenizer_version` |
| `token` | str | yes | the token as that tokenizer gives it (marks stripped, NFC) |
| `word` | str | yes | the token as written in the edition, NFC |
| `part` | int | yes | 0, 1, 2 ... in reading order within the token |
| `slot` | str | yes | `prefix`, `stem`, `infix`, `suffix`, `tone`, or `whole` |
| `position` | int | no | prefixes counted from the word's start (1 = first), suffixes from the stem outward (1 = next to the stem); 0 = an affix outside a chain |
| `text` | str | yes | the part as written in this token; empty for a `tone` part, and for a part with no letters of its own (Hebrew's article hidden in a preposition's vowel, בַּבַּיִת: the article is a part with empty text, the vowel stays in the preposition's text) |
| `tone` | list[str] | for `tone` | the tone part's marks, `"<pos>:<mark>"`: `pos` counts base-letter clusters from the word's end (1 = last), `mark` is the combining character(s), NFD |
| `label` | str | no | a name for a part with no letters of its own, e.g. `tone-proh` |
| `form` | str | no | the affix this part is an instance of, as the chain names it: `aá-` and `aa-` are two forms; `tu-`/`tw-` can be one |
| `chain_id` | str | no | the part's entry in the language's fact file (`seg.chain`) |
| `written` | str | no | `fused` (default), `hyphen` or `apart`: how an affix is written. Tokenizer v2 splits at a hyphen ("Yesu-ka" is two tokens, `Yesu` and `ka`), so a hyphenated affix is a token of its own: one row, slot `suffix` (or `prefix`), `written: hyphen`, `text` without the hyphen, and `host` |
| `host` | int | with `hyphen`/`apart` | for a token that is itself an affix (hyphenated, or a clitic written apart): the offset of the token it attaches to (−1 = the token before) |
| `class`, `lemma`, `lexeme`, `strong` | str | no | what the producer knows about the part |
| `source` | str | no | this part's producer, when not the header's |
| `basis` | str | no | this part's basis, when not the header's |
| `p_split` | float | no | on part 0: the producer's confidence that the token is divided this way. Below the reader's threshold the token is used whole. |
| `p` | float | no | on any row: the producer's confidence in this part (its boundaries and slot), 0–1. With per-part `basis`, a reader can trust a team-confirmed prefix while still weighing a tagger's stem. |

**Bases**, strongest first: `team` (a translation team decided it), `derived` (counted on the corpus, figures in the fact file),
`external` (an outside analysis, e.g. MACULA), `model` (a statistical tagger), `labelled` (an agent's tag not yet tested). A reader
applies them per part, so a team-checked part outranks a tagger's guess in the same file.

**Words never split.** A token that only looks as if it carries an affix (a noun beginning like a verb prefix) is one `whole`
row with `basis: team` (or `derived` with figures) and `p_split: 0`. A reader does not split such a token, whatever another source says.

## The rebuild rule (what `check` tests)

For every token:

1. `part` runs 0, 1, 2 ... without gaps; `slot` is one of the six; at most one `whole` row, and then no other row; a `hyphen` or
   `apart` row names a `host` token that exists in the verse.
2. Join the `text` of every part that is not `tone`, in `part` order.
3. Put back the marks of each `tone` part: NFD the joined text, count base-letter clusters (a letter plus the combining marks after
   it) from the end, and add each `mark` to the cluster at `pos`.
4. The NFC of the result must equal `word`, exactly.
5. `token` must equal the tokenizer's output for `word` (one token), and, when the edition's USJ is given (`--usj`), the tokenizer's
   token `i` of that verse.
6. With `--usj`, every book's `text_fp` must equal the fingerprint of that book's USJ; a book whose hash differs is reported, and
   its rows are still checked.

A file passes only if every token passes.

## Status

- Today our chain reads the hebrew-nt-analysis `v0.1` file of `heb_scg` (`config/target_parts.json`), the shape this spec grew
  from (it has `token`, `word`, `part`, `text`, `class`, `lemma`, `source`, `p_split`, but no `slot`, `basis` or header). Once
  this spec is agreed, the reader will take `bcv-split/0.1` files and that file will be converted to it.
- Tokenizer v3 (bibles issue #1, Myanmar syllables) will change `i` for some languages; its files will say `tokenizer_version: 3`.
