Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped. Position
numbers refer to that tokenization.

**Evidentiary tier — read before trusting this file:** Hungarian has no Clear-Bible gold at all; this is
built entirely from globalbibletools' independent CC0 occurrence alignment (`gold: gbt`), the same
corroboration-only tier as `rus.md` — see that file's evidentiary-tier note for why the comparison only
works on a vocabulary-overlapping subset. The sample here is SMALL (44 comparable cases, whole NT, mostly
from a handful of onboarded epistles — `hun_bib`'s eflomal output does not yet cover the whole NT) — small
enough that this file leans more on general, well-established facts about Hungarian grammar than on the
sample size alone to justify each note.

Hungarian notes (checked against `hun_bib`'s eflomal output, whole NT, this session):
- **Definite/indefinite articles (`a`/`az` "the", `egy` "a/an") are a real, separate-word pattern** —
  confirms the Grambank `articles` flag (GB020-023) directly. Hungarian articles are ordinary free words
  (unlike Arabic's bound prefix, `arb.md`), and belong to the noun/name span when the source's own
  determination requires one. Confirmed: 2 Thessalonians 1:1 ὁ κύριος ("the Lord") renders as `az úr`, not
  `úr` alone; 1 John 2:1 παράκλητον ("advocate", anarthrous but definite in context) renders as `egy
  szószólónk`.
- **The verbal particle `meg` is a smaller, distinctively Hungarian pattern, plausible but NOT
  Grambank-flagged** (Grambank's `tam_auxiliary` group doesn't code Hungarian's preverb system) — `meg` is
  a perfectivizing/telic preverb (roughly "to completion"), a genuinely separate word carrying real
  aspectual meaning, and belongs to the verb's span when the source verb's own aspect (a Greek aorist/
  perfect, a completed action) calls for it. Confirmed: 1 John 2:4 τηρῶν ("keeps/keeping") renders as `meg
  tartja`, not `tartja` alone. This is one linguistic pattern, drawn from only 3-4 confirmed instances in
  this sample — treat it as a plausible lead, not an established rule the way the article pattern above is.
- **Case-marking (GB072) and possession-affix (GB430-433) flags did NOT show up here, for the SAME
  structural reason as Russian and Portuguese's false leads**: Hungarian is a textbook agglutinative
  language — both oblique case (18+ case suffixes: ház/házban/házból "house/in-house/from-house") and
  possession (ház-am "my house", a bound suffix, not a separate word) are marked by suffixes fused onto
  the noun itself, never a free word a tokenizer could drop or need to add. A Grambank flag says the
  category is typologically present, never that it will show up as a token-boundary problem in this
  tokenization scheme — the same lesson every language checked this session keeps confirming.
- Given the small sample, treat any specific verse's own evidence (a SEEDS rendering, or the source
  token's own gloss/aspect) as decisive over this default, more so than for the larger-sample languages.
