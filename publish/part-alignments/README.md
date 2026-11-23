---
license: cc-by-4.0
pretty_name: Part alignments (Greek and Hebrew word parts to translation words)
tags:
- bible
- alignment
- word-alignment
- morphology
- greek
- hebrew
---

# part-alignments — every Greek and Hebrew word PART, linked to the words of a translation

Format `part-alignments/0.1`. Built by [lexeme-aligner](https://github.com/bcv-commons/lexeme-aligner); the source units are
[bcv-commons/word-parts](https://huggingface.co/datasets/bcv-commons/word-parts) v1.1.

## One verse, read

Mark 1:1 in Louis Segond 1910 (`fra/fra_lsg`), printed by `read_verse.py` (in this repo; `read_verse.mjs` prints the same):

```
$ python read_verse.py . fra fra_lsg "MRK 1:1" "Commencement de l’Évangile de Jésus-Christ, Fils de Dieu."
source parts:
    0  410010010011     stem           Ἀρχ          -> Commencement
    1  410010010012     ending         ὴ            -> -
    2  410010010021     stem           τοῦ          -> de l
    3  410010010031     stem           εὐαγγελί     -> Évangile
    4  410010010032     ending         ου           -> Évangile de
    5  410010010041     stem           Ἰησοῦ        -> Jésus
    6  410010010051     stem           Χριστ        -> Christ
    7  410010010052     ending         οῦ           -> Christ
    8  410010010061     stem           Υἱ           -> Fils
    9  410010010062     ending         οῦ           -> de
   10  410010010071     stem           Θε           -> Dieu
   11  410010010072     ending         οῦ           -> -
whole words (compact-alignments view, by the fold):
    0  Ἀρχὴ           746        -> Commencement
    -  τοῦ            3588       (function word: no slot in compact)
    1  εὐαγγελίου     2098       -> Évangile de
    2  Ἰησοῦ          2424       -> Jésus
    3  Χριστοῦ        5547       -> Christ
    4  Υἱοῦ           5207       -> Fils de
    5  Θεοῦ           2316       -> Dieu
```

The files behind it:

```
_index/MRK_parts.json      "MRK 1:1": {"units":  [["410010010011", "stem", "Ἀρχ", 0], ["410010010012", "ending", "ὴ", 0], ...],
                                       "tokens": [["41001001001", "746", 1, 0], ["41001001002", "3588", 0, 2], ...]}
f/fra/fra_lsg/MRK_<fp8>.json   [..., "0:0 2:1-2 3:3 4:3-4 5:5 6:6 7:6 8:7 9:8 10:9", ...]     one string per verse of the index
```

`0:0` = unit 0 (Ἀρχ) links target word 0 (*Commencement*); `4:3-4` = unit 4 (the ending ου) links words 3-4 (*Évangile de*).
Target positions count the verse's words as `bcv_tokenize.py` / `tokenize.js` (in this repo) cut them: `l’Évangile` is two words.

## Where the files live: three repos, one path scheme

As compact-alignments did, the dataset is split so no repo passes Hugging Face's recommended 100,000 files:

| Repo | Holds |
|---|---|
| `bcv-commons/part-alignments` (this one) | README, manifest, `_index/`, every `<BOOK>_<fp8>.json`, `_fingerprints.txt`, the fold library, readers and tokenizers |
| [`bcv-commons/part-alignments-meta`](https://huggingface.co/datasets/bcv-commons/part-alignments-meta) | every `<BOOK>_<fp8>.meta.json` (provenance per unit) |
| [`bcv-commons/part-alignments-extra`](https://huggingface.co/datasets/bcv-commons/part-alignments-extra) | every `<BOOK>_<fp8>.extra.json` (the residual layer) |

The relative path of a file is the same in all three. Download the ones you need into ONE folder and `fold.py` / `read_verse.py`
find the sidecars beside the main files.

## Grain: part or word

An edition's manifest entry says how it was aligned:

- no `grain` key: aligned on the parts. An ending, prefix or augment has links of its own.
- `"grain": "word"`: aligned on whole words. The editions not re-run on the parts yet were converted from the last compact-alignments
  release, with each word's links on its carrying unit (the stem, or the word's only unit) and nothing on its other units. They say
  which target words render the whole source word, not which part renders what. Folding them gives back that compact release
  exactly. Re-running an edition on the parts replaces its entry.

This dataset replaces compact-alignments, which is frozen since 2026-11-23 and removed one week after this dataset was first published (the date is on compact-alignments' card).

## Files

```
_index/<BOOK>_parts.json                  per verse: units [part id, kind, text, token] and tokens [key, lexeme, content, carrier]
_index/_source.json                       format, the word-parts pin, the parts spine's content pin
<iso[0]>/<iso>/<edition>/<BOOK>_<fp8>.json        ["srcOrd:span srcOrd:span ...", ...]  one string per index verse, index order
<iso[0]>/<iso>/<edition>/<BOOK>_<fp8>.meta.json   per entry: method, conf, contested, bonus, rule, rend, parts[, runs]
<iso[0]>/<iso>/<edition>/<BOOK>_<fp8>.extra.json  the residual layer (links for units the main array leaves out)
<iso[0]>/<iso>/<edition>/_fingerprints.txt        book and chapter text fingerprints of the aligned text
<iso[0]>/<iso>/<edition>+llm+<name>/...           an LLM-checked layer, same shape; `srcOrd:_` = checked, renders nothing
manifest.json                             per edition: books (file names), methods, tokenizer_version, text licence pointer
fold.py / fold.mjs                        the fold library: part-alignments -> compact-alignments (no dependencies)
read_verse.py / read_verse.mjs            the example above
bcv_tokenize.py / tokenize.js             the tokenizer every target position refers to (bcv-commons/bibles, CC0)
```

- **A unit** is one part of word-parts v1.1. Greek: a Nestle 1904 word cut into preverb / augment / reduplication / stem / ending /
  fused / particle (article, relatives and indeclinables stay one `particle`). Hebrew: a MACULA part (prefixed particles and
  pronominal suffixes are already parts there) cut into prefix (yiqtol person) / stem / ending / fused. The part id is the word id
  (Greek 11 digits = spine key; Hebrew 12 digits = MACULA id) plus the part number. Units the source text merges (fused names such as
  בֵּית לֶחֶם, adjacent words with the same Strong's number) are their ids joined with `+`.
- **`srcOrd`** = position in the verse's `units` list: every unit, content and function alike.
- **`tokens`**: the source words as compact-alignments counts them. `content` 1 = has a slot in compact (`srcOrd` = rank among
  the content tokens), 0 = a function word (compact's `_fn` index). `carrier` = the unit that carries the word's lexeme; the written
  word is the units with that `token` together.
- **Span grammar** (compact's): `3` one word, `3-5` a run, `3,7` scattered; positions in the edition's own verse text.
- **`<fp8>`** = the text fingerprint of the edition's book (as compact): fingerprint YOUR copy of the text (`text_fingerprint.mjs` in
  compact-alignments) and take the file with that name; a revised text finds no file rather than a wrong one.
- Units with no link are left out. In an LLM layer `srcOrd:_` means: checked, the unit renders nothing in this translation.

## The word view = compact-alignments

[compact-alignments](https://huggingface.co/datasets/bcv-commons/compact-alignments) is computed FROM these files by `fold.py`
(equally `fold.mjs`), the same code shipped here, so the two never disagree. Its manifest says so per edition
(`derived_from: part-alignments/0.1`). To read compact from part-alignments yourself:

```python
from fold import fold_book
index = json.load(open("_index/MRK_parts.json"))
main, meta, extra = fold_book(index, json.load(open(".../MRK_<fp8>.json")), json.load(open(".../MRK_<fp8>.meta.json")))
```

```js
import { foldBook } from "./fold.mjs";
const [main, meta, extra] = foldBook(index, partMain, partMeta, partExtra);
```

`python fold.py --parts DIR --out DIR` does a whole downloaded tree; `--index-only` writes compact's `_index` files
(`<BOOK>_lexemes.json`, `_keys`, `_fn`, `_fn_keys`). The rule, per verse: a word's target words are its carrier unit's, plus the
words its other units link that no other word's carrier claims; provenance comes from the carrier (else the first linked unit).
See the docstring of `fold.py`.

**Two fold rules.** The rule above is `fold` (the default, and what compact-alignments is made with: each compact manifest entry
says so in `fold_rule`). `stem` keeps only the carrier unit: Υἱοῦ in the example becomes *Fils*, not *Fils de*; the ending's link
to *de* stays here in part-alignments. Choose `stem` if you follow the convention of gold standards that leave such words (French
*de*, Spanish *se*) unaligned; it equals the whole-word alignments made before part-alignments. The links are the same either way;
only the word view differs.

```python
main, meta, extra = fold_book(index, part_main, part_meta, part_extra, fold_rule="stem")
```

```js
const [main, meta, extra] = foldBook(index, partMain, partMeta, partExtra, "stem");
```

`python fold.py --parts DIR --out DIR --fold-rule stem` (and `node fold.mjs ... --fold-rule stem`) does a whole tree that way.

**Choosing a fold rule.** It is a convention, not a quality setting: does a word like French *de* or English *of*, which renders
a Greek or Hebrew ending, belong to the noun's rendering? Measured against manual gold alignments (content words, link F1; New
Testament unless noted), each gold standard prefers the rule that matches its own convention:

| Gold alignment | `stem` | `fold` |
|---|---|---|
| English, BSB | .524 | **.569** |
| Hindi, IRV | .612 | **.655** |
| French, Louis Segond (leaves *de* unaligned) | **.873** | .780 |
| Spanish, Reina-Valera 1909 (leaves *de*, *se* unaligned) | **.849** | .810 |
| Arabic, Van Dyck (NT / OT) | .700 / **.884** | **.704** / .883 |

Two in-between rules were measured as well: folding only the parts' links that touch the word's own words, and folding everything
but endings. Neither beat the better of `stem` and `fold` for any of these gold alignments, so the fold library ships just the two.

## Licences

Data: CC BY 4.0 — source analysis from MACULA Greek and Hebrew (Clear Bible, CC BY 4.0) through the spine and word-parts v1.1
(CC BY 4.0, see its card). Alignment links are ours (CC BY 4.0 here, attribution: bcv-commons). The translation texts are NOT
included; each edition's manifest entry points at its source and licence. `fold.py`, `fold.mjs`, `read_verse.*`: GPL-3.0-or-later
(lexeme-aligner). `bcv_tokenize.py`, `tokenize.js`: CC0 1.0 (bcv-commons/bibles).
