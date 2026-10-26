---
pretty_name: Alignment conventions — attributed rules
tags:
  - bible
  - word-alignment
  - provenance
license: cc-by-4.0
---

# alignment-conventions — the rules our aligners were given, and where each rule came from

When an LLM checks a word alignment (the `full-alignments-llm` layers), it is given, besides the verse, a short **conventions**
text for the language: how the text is cut into positions, where English `the` / `of` / auxiliaries go, what a Hebrew prefix is,
and so on. This dataset publishes every such text **exactly as it was sent**, and the same text split into **rules, each with a
derivation record**, so you can tell a measured fact from a rule an LLM wrote.

## Kinds of rule

| kind | what it means | derivation record | licence |
|---|---|---|---|
| `measured` | counted on a hand-made alignment: what a function word renders and how often | gold source, base text, books, method, script hashes, date, the counts (`figures`) | the gold's (Clear-Bible/Alignments: CC BY 4.0) |
| `by-construction` | true because of how the data are built (e.g. the tokenizer) | the code that makes it true | CC0 |
| `llm-authored` | written by an LLM (Claude, Anthropic) in a lexeme-aligner working session | the file, its first and last commit, the evidence the text itself cites; for edition facts the markup it rests on (`basis`) | CC0 |

An `llm-authored` rule carries a `check`: `unchecked`, and where measured rules exist about the same words, their ids
(`related_measured`). That is a pointer, not a verdict: whether the measured figures confirm the rule is left to the reader until a
recorded check says otherwise.

**How much the wording matters (measured, 2026-10-26):** on the BSB (Mark 1–3 against Clear's gold, Ruth against the morpheme-split
gold), four different conventions texts — long, short, measured-only, and both — moved the checker's link F1 by less than 0.01
(chain alone .690 / .675, with the checker .775–.783 / .746–.759). Adding the measured default to each function-word question moved it
by at most +0.003. Read these rules as a record of the choices behind the alignment more than as a lever on it.

## Layout

```
<lang>/<name>/<sha8>/conventions.md    the exact text sent to the aligner; <sha8> = first 8 hex of its sha256 (every version kept)
<lang>/<name>/<sha8>/rules.json        {"license", "sha256", "rules": [...]} — the same text as rules
<lang>/measured/<profile>/rules.json   measured rules, e.g. eng/measured/bsb-clear (BSB, Clear's manual alignment)
manifest.json                          every set: name, sha256, whether it is the current text, rule counts by kind
```

A rule:

```json
{"id": "eng.measured.bsb-clear.OT.source.וְ", "kind": "measured",
 "scope": {"language": "eng", "testament": "OT", "source": "וְ"},
 "text": "וְ: no word of its own 57%; 'and' 33%",
 "figures": {"n": 11286, "renderings": {"none": 6481, "and": 3717, "but": 322, "so": 226, "then": 146, "now": 109}},
 "derivation": {"source": "Clear-Bible/Alignments (manual), base text BSB", "license": "CC-BY-4.0", "books": ["GEN", "1SA", "PSA", "ISA"],
                "method": "...word-level links split into MACULA morphemes by lexeme_aligner.eval.gold_split...", "date": "2026-10-26"}}
```

## Which layer used which text

Each layer of [full-alignments-llm](https://huggingface.co/datasets/bcv-commons/full-alignments-llm) carries `_conventions.json`:
`books` maps every book to the `<lang>/<name>/<sha8>` it was aligned with (one edition can use several versions, e.g. the KJV used
three), and `sets` repeats those rule sets, so a layer is self-contained.

## Measured rules for the Hebrew Old Testament

Clear's Hebrew alignment links whole words (`source_unit: word`): for בִּימֵי -> "in the days" it does not say that "in" is the prefix
ב's. Our source text splits prefixes and suffixes off, so the OT figures come from Clear's links **split into morphemes** by a stated
rule (`lexeme_aligner.eval.gold_split`): inside the gold span a prefix takes the leftmost word its fixed list or its MACULA gloss
allows, a suffix the rightmost, the stem keeps the rest; a part with no such word gets none. Every split link records how it was made.
Clear has no morpheme links to check this against; a hand check of 30 random words found the split right where the gold span itself
was.

## Credits

Clear-Bible/Alignments and Clear-Bible MACULA Hebrew/Greek (CC BY 4.0); the conventions texts and the scripts are by the
lexeme-aligner project (CC0), LLM-authored rules written with Claude (Anthropic).
