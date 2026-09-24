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
- **`lexeme-verify` specifically: a correct `of`/`the`/possessive/auxiliary supplial (the categories
  above) is per-OCCURRENCE grammar, not a lexeme-wide convention — do not strip one because sibling
  occurrences of the same lexeme render without it.** Confirmed live regression (Joshua 1:1, hbo:4872
  "Moses"): the `full` pass correctly decided the construct-state occurrence "עֶבֶד מֹשֶׁה" (servant OF
  Moses) as `of Moses` (note: `construct 'of Moses'`) — matching this file's own genitive-`of` rule —
  and the following `lexeme-verify` pass then corrected it back down to bare `Moses`, with the note
  `"t8 'of' is function word, only Moses itself belongs"`, even though `lexeme-verify`'s own strategy
  text already warns "a rendering that differs from the rest of the group is not automatically wrong."
  Most of this lexeme's other occurrences in the same chapter (Joshua 1:1 h15, 1:5, 1:7, 1:13, 1:15 —
  all plain nominative "Moses") legitimately render as bare `Moses`; this ONE occurrence's own
  construct/genitive marking (visible from ITS OWN gloss, e.g. "of.Moses" vs plain "Moses") is what
  licenses the wider span, not majority vote across the lexeme's occurrences. Same pattern confirmed on
  hbo:0001 ("father"): Joshua 2:12/2:18's genitive "my father's"/"your father's" occurrences are the
  ONLY ones of that lexeme in the chapter needing the possessive `'s`; a lexeme-verify pass must not use
  that minority status as evidence for stripping it. When reviewing a genitive/construct occurrence,
  check that occurrence's OWN gloss (a genitive gloss usually reads "of.X" or "X's", not bare "X") before
  trusting the group's dominant span width.
- **The gloss's own `the.` marker is the deciding signal for the definite article — check it directly,
  every time, instead of pattern-matching from a similar case just decided.** The bullet above fixed the
  genitive-`of` regression cleanly (confirmed: every changed genitive-of decision moved correctly), but
  the SAME re-run also flipped several PLAIN (non-genitive) definite-article decisions the WRONG way in
  both directions, each one contradicting its own word's gloss: `אֲר֤וֹן` (gloss `the.ark`) was
  correctly `the ark`, then wrongly corrected to bare `ark`, TWICE, in Joshua 3:3 and 3:8; `רַגְלֵ֤י`
  (gloss `the.feet`) went from correct `the feet` to wrong `feet` in Joshua 3:15 and 4:9; `יְמֵ֥י` (gloss
  `the.days`) went from correct `the days` to wrong `days` in Joshua 4:14; `כֹּֽהֲנִים֙`/`כֹּהֲנִ֔ים`
  (gloss `the.priests`) went from correct `the priests` to a WORSE match in 3:15/4:3; and in the other
  direction, `מַּיִם֩` (gloss plain `waters`, no article marker at all) went from correct bare `waters`
  to wrongly-added `the waters` in Joshua 3:16, and `אָר֗וֹן` (gloss plain `ark`) similarly gained an
  unwarranted `the` in 4:10. **Rule: a gloss written `the.X` or `[the].X` means the span needs `the` (or
  an equivalent definite rendering); a gloss with no `the.` prefix means it does not — regardless of
  whether a construct/genitive neighbor just needed one, regardless of what a sibling occurrence of the
  same lexeme rendered as, and regardless of general plausibility. A compound gloss like `of.the.X` needs
  BOTH the genitive `of` AND the article `the` in the same span (`רַגְלֵ֣י` in Joshua 4:9, gloss
  `of.the.feet`, needs `of the feet` — a case this file's own genitive-of fix did not yet fully realize,
  since it only supplied `the feet`, dropping the `of`). When correcting or confirming ANY occurrence
  that touches definiteness, re-read that occurrence's own gloss line before deciding — do not carry a
  decision over from the previous occurrence you just reviewed.
