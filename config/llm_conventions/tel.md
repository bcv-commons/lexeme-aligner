Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped. Position
numbers refer to that tokenization.

**Evidentiary tier — read before trusting this file:** Telugu has no Clear-Bible gold; this is built from
globalbibletools' independent CC0 occurrence alignment (`gold: gbt`), the same corroboration-only tier as
`rus.md`/`hun.md` — see `rus.md`'s evidentiary-tier note for why the comparison only works on a
vocabulary-overlapping subset of tokens (197 of 2,160 compared content links, whole Bible, this session).

Telugu notes (checked against `tel_irv`'s eflomal output, whole Bible, this session):
- **The genitive postposition `యొక్క` (yokka, "of"/"'s") is the dominant pattern for names (12 of 26
  undershoot cases) and belongs to the name's span when the source is a genitive/construct relation** —
  directly confirms the Grambank `case_marking` flag (GB072). Confirmed: Ruth 1:1 Ἰούδα ("of Judah",
  genitive) renders as `యూదా యొక్క`, not `యూదా` alone; Ruth 1:1 Μωάβ ("of Moab") renders as `మోయాబు
  యొక్క`; Ruth 1:3 renders Naomi's own genitive the same way. This is the same underlying pattern as
  Arabic's oblique-case prepositions (`arb.md`) and Hindi's postpositions, a different word-order
  realization of the same gap: the source case-inflects with no separate word, Telugu supplies one.
- **A second, smaller, unrelated pattern: `మరియు` (mariyu, "and") joining two coordinated names** (7 of 26
  name cases) — this is an ordinary conjunction between list items (Ruth 1:2's two sons, "Mahlon and
  Kilyon"), not a case-marking gap; don't conflate the two when deciding whether to extend a span — a
  following name in a coordinated list needs its own separate `h` id and its own `and`, not a merge into
  the preceding name's span.
- **Common nouns did NOT show the same undershoot rate as names** (6.7%, above the 5% anomaly threshold,
  hence not flagged by phase-1) — the genitive-postposition gap here is specifically a NAME phenomenon in
  this data (Biblical genealogies/place-names are genitive-heavy), not a general noun problem.
- This is a real, large-sample pattern (dominant word confirmed across multiple independent verses), but
  drawn from the gbt corroboration tier, not a manual gold — treat any specific verse's own evidence (a
  SEEDS rendering, or the source token's own case) as decisive over this default.
