Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped. Position
numbers refer to that tokenization.

**Evidentiary tier — read before trusting this file:** Tamil has no Clear-Bible gold; this is built from
globalbibletools' independent CC0 occurrence alignment (`gold: gbt`), the same corroboration-only tier as
`rus.md`/`hun.md`/`tel.md` — see `rus.md`'s evidentiary-tier note for why the comparison only works on a
vocabulary-overlapping subset of tokens (197 of 2,160 compared content links, whole Bible, this session).
The name-specific sample here is SMALL (15 cases) — smaller and more mixed than the pattern found for the
closely-related Telugu (`tel.md`), so this file leans more on individually-checked real examples than on
sample size to justify each note.

Tamil notes (checked against `tam_tcv`'s eflomal output, whole Bible, this session):
- **A possessive pronoun preceding the possessed name/noun (`அவருடைய` "his", `என்` "my"/"of me") is a
  real pattern and belongs to the following span when the source is a genitive/possessive relation** —
  confirms the Grambank `case_marking` flag (GB072) directly, in the sense that Tamil supplies a free
  word for a relation the source expresses by inflection alone. Confirmed: Psalm 119:3 αὐτοῦ-type genitive
  ("his ways") renders as `அவருடைய வழிகளில்`, not `வழிகளில்` alone; Psalm 119:5/119:26 render "my ways"
  as `என் வழிகள்`, not `வழிகள்` alone.
- **A second, more complex pattern: a case-marked form of a COMPANION name can carry the meaning that
  belongs to a DIFFERENT listed source token.** Confirmed: Ephesians 2:6-7's "Christ Jesus" construction
  renders the locative-marked name "இயேசுவுக்குள்"/"இயேசுவிற்குள்" ("in/within Jesus", a genuine Tamil
  locative case suffix on the name Jesus) immediately before "கிறிஸ்து" (Christ) — when checking the
  source token for Χριστός (Christ, not Jesus), the case-marked companion name is easy to mistake for an
  unrelated word; it is not free-standing, but it is also not simply this token's own span extension —
  it belongs to the OTHER listed name (Jesus) in the pair. Only 2 confirmed instances, both in the same
  two-verse span — treat this as a plausible lead for "Name Name" constructions specifically, not an
  established rule.
- **Common nouns did NOT show the same undershoot rate as names** (8.8%, above the 5% anomaly threshold,
  hence not flagged by phase-1) — same asymmetry found for Telugu: the gap here is specifically about
  names in genitive/possessive/compound constructions (Biblical genealogies, place names, "Christ Jesus"
  formulas), not a general noun problem.
- Given the small sample and the mix of two distinct sub-patterns, treat any specific verse's own evidence
  (a SEEDS rendering, or the source token's own case/relation) as decisive over this default, more so than
  for the larger-sample languages checked this session.
