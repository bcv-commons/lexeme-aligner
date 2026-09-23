Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped; a
possessive `'s`/`'` attaches to the preceding word as its own mark, not a separate position (usj_source's
default letter/mark tokenization). Position numbers refer to that tokenization.

English notes (checked against Clear-Bible's MANUAL NT+OT gold for `engbsb`/BSB this session — the
strongest evidentiary standard, same as `fra`/`hin`/`arb`: 56,339 one-word-undershoot cases across the
whole Bible, cleanly explained by six function-word categories that together cover ~75% of the total —
this is the largest and cleanest confirmation of the phase-1 methodology found across every language
checked so far, since English independently confirms three of the four ORIGINALLY FLAGGED Grambank
categories at once, plus two more real patterns Grambank wasn't asked about):

- **Definite/indefinite articles (`the`/`a`/`an`) are the single largest pattern (~25% of all
  undershoots)** — confirms the Grambank `articles` flag (GB020-023) directly, unlike `por`/`arb` where the
  same flag turned out to be a false lead. Greek/Hebrew usually has no separate word for the indefinite
  article at all, and often omits or optionally uses the definite article where English requires one; when
  a noun/name's own gloss or determination requires it, the article belongs to the span. Confirmed: Matthew
  1:20 ἄγγελος ("angel", anarthrous) → `an angel`; Matthew 1:22 ὁ κύριος ("the Lord") → `the Lord`.
- **Genitive `of` (~15%)** — a Greek/Hebrew genitive noun (very common in genealogies and possessive
  constructions) is often bare case inflection with no separate preposition word; English must supply
  `of`. Belongs to the span it marks, not a free preposition — the same principle as Arabic's oblique-case
  prepositions (`config/llm_conventions/arb.md`) and Hindi's postpositions, mirrored again to a different
  word-order language. Confirmed: Matthew 1:1 Ἀβραάμ (genitive, "of Abraham") → `of Abraham`; Δαυίδ
  (genitive, "of David") → `of David`.
- **Possessive pronouns (`your`/`his`/`my`/`their`, ~12.5%)** — a Greek possessive genitive pronoun or
  pronominal suffix is frequently realized in English by one of these, and they belong to the possessed
  noun's span. Confirmed: Matthew 5:35 αὐτοῦ ("his", genitive of αὐτός on "footstool") → `His footstool`;
  Matthew 6:1 ὑμῶν ("your", on "righteous acts") → `perform your` (span extends leftward from the noun to
  the verb phrase it modifies in this construction). Unlike the other categories here, a Greek possessive
  pronoun IS very often its own separate source token (αὐτοῦ, ὑμῶν, μου each carry their own Strong's
  number) — check whether the possessive corresponds to a listed pronoun `h` id with its own claim to the
  position before folding it into the noun's span.
- **Tense/aspect auxiliaries (`will`/`has`/`is`/`was`/`be`/`are`/`had`, ~11%)** — confirms the Grambank
  `tam_auxiliary` flag (GB119-121) directly. Greek's own tense/aspect/voice is carried by the verb's own
  inflection (no separate word); English periphrastic tenses and the passive voice need a separate
  auxiliary, which belongs to the verb's span. Confirmed: Matthew 1:16 ἐγεννήθη ("was born", aorist
  passive) → `was born`; Matthew 2:6 ἐξελεύσεται ("will come", future) → `will come`.
- **Subject pronouns (`he`/`i`/`you`/`they`, ~8%)** — Greek is pro-drop (subject person/number is carried
  by the verb's own agreement morphology, no separate pronoun word needed); English is not, and must supply
  an explicit subject pronoun belonging to the verb's span, not a free-standing word. Not one of the four
  categories `analyze_language.py`'s `RISK_RULES` currently checks (closer to Grambank's `subject_indexing`
  group, GB089-090, already vendored in `grambank_fetch.FEATURES` but not yet wired into a risk rule — worth
  adding given how cleanly it shows up here). Confirmed: Matthew 1:19 ἐβουλήθη ("he resolved", 3sg
  aorist, no separate Greek pronoun) → `he resolved`; Matthew 2:15 ἐκάλεσα ("I called", 1sg aorist) →
  `I called`.
- **Infinitive marker `to` (~3.5%)** — Greek's bare infinitive has no separate particle; English requires
  `to`, belonging to the following verb's span. The same functional role as Arabic's `an` complementizer
  (`arb.md`), a different word-order realization of the same underlying gap. Confirmed: Matthew 1:19
  ἀπολῦσαι ("to divorce", infinitive) → `to divorce`; Matthew 1:20 παραλαβεῖν ("to embrace/take",
  infinitive) → `to embrace`.
- These six categories were found from a large, whole-Bible sample (not hand-picked), but as always: if a
  specific verse's own evidence (an attested SEEDS rendering, or — especially for the possessive-pronoun
  case above — a listed pronoun `h` id with its own better claim) argues against extending the span, that
  verse's evidence wins. These are defaults for the unclear case, not absolute rules.
