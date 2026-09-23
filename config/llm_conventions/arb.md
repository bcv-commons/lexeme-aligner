Target positions are the letter/mark runs of the verse in reading order (right-to-left script; position
numbers still refer to reading order, not visual left-to-right order). Punctuation is dropped. Position
numbers refer to that tokenization.

Arabic notes (checked against Clear-Bible's MANUAL NT+OT gold for `arb_vdv`/AVD this session — the
strongest evidentiary standard available, same as `fra`/`hin`: 27,761 one-word-undershoot cases found
across the whole Bible, overwhelmingly a small number of grammatical-particle patterns, not scattered noise):

- **Prepositions realizing a source oblique relation with no separate source word are the single largest
  pattern (~45% of all undershoots): `fi` "in", `min` "from/of", `ila` "to", `ala` "on", `an` "about/from",
  `ind` "at/near".** Greek/Hebrew often expresses a genitive/dative/locative relation through case
  inflection alone, with no separate preposition token on the source side; Arabic (which has no separate
  word-level case marking of its own on nouns in this register) must supply one of these prepositions to
  carry that same relation. When a preposition immediately precedes a noun/name your source token's own
  case/relation would otherwise leave stranded, it is normally PART OF THAT NOUN'S SPAN, not a free
  function word to trim — the same principle as Hindi's postpositions (`config/llm_conventions/hin.md`),
  mirrored to a pre-positional language. Confirmed: Matthew 6:25 "tou endymatos" ("of clothing", bare
  genitive, no separate Greek preposition) renders with a leading `min` ("of/from") — the span needs that
  preposition, not the noun alone.
- **Compound names/words split across two Arabic words for one source lexeme** are a separate, smaller
  pattern from the case-marking one above — both words belong to the span. Confirmed: Matthew 2:1
  "Bethleem" (one Greek token) renders as two words, literally "house of bread" ("bayt lahm") — the span
  needs both, not just the second word; Matthew 7:15 "pseudoprophetes" ("false-prophet", one compound
  Greek lexeme) renders as an adjective+noun pair, literally "the-prophets the-false" — the span needs
  both words, not just the adjective.
- **`qad` marks completed/perfect aspect and belongs to the verb's span when the source verb is
  perfect/aorist with a completed-action sense** — a genuine TAM particle with no separate Greek/Hebrew
  source word behind it (confirms this language's Grambank tam_auxiliary flag, GB119-121). Confirmed:
  Matthew 1:20 "ephane" ("appeared", aorist) renders as `qad zahara`, span needs `qad`, not the verb alone;
  Matthew 2:1 "paregenonto" ("had come/arrived") renders as `qad ja'u`, same pattern.
- **`an` is an infinitive/subjunctive complementizer**, introducing a verb where Greek uses a bare
  infinitive with no separate particle of its own (e.g. after a verb of wanting, fearing, or intending) —
  belongs to the following verb's span, not a free-standing function word. Confirmed: Matthew 1:19 "thelon
  ... deigmatisai" ("[not] wanting ... to make a public example") renders the infinitive as `an
  yushhiraha`, span includes `an`; Matthew 1:20 "labein" ("to take") renders as `an ta'khudha`, same pattern.
- **Vocative particles (`ya`, and `ayyuha` before a definite noun)** mark the vocative case the same way —
  belongs to the addressed name/noun's span when the source is itself vocative case, not a free
  exclamation. Smaller pattern (~4% combined), same principle as the prepositions above.
- **The `articles` Grambank flag (GB020-023) did NOT show up as a real pattern here, for a structural
  reason, not an absence of the underlying phenomenon**: Arabic's definite article (`al-`) is a bound
  PREFIX with no space, already fused into the noun's own token by any letter-run tokenizer — it can never
  surface as a separate position to drop or a multi-word span to miss, unlike Hindi's/Portuguese's
  space-separated function words. Same lesson as `por.md`: a Grambank flag says a category is
  typologically present, not that it will manifest as a token-boundary problem in THIS tokenization scheme.
- This is a large-sample, high-confidence pattern (27,761 cases, dominated by 6 prepositions + 2
  particles), but as always: if a specific verse's own evidence (an attested SEEDS rendering, or the
  source token's own gloss/case) argues against extending the span, that verse's evidence wins — these are
  defaults for the unclear case, not absolute rules.
