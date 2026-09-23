Target positions are the letter/mark runs of the verse in reading order (Cyrillic script). Punctuation is
dropped. Position numbers refer to that tokenization.

**Evidentiary tier — read before trusting this file:** unlike `fra`/`hin`/`arb`/`eng` (Clear-Bible gold,
manual or transfer), Russian's Clear-Bible gold is QUARANTINED (its RUSSYN parquet is itself positionally
scrambled — see the `_quarantine` note in `config/gold_langs.json`). The evidence below instead comes from
globalbibletools' independent CC0 occurrence alignment (`gold: gbt`), which glosses ITS OWN target text —
often not word-for-word the same translation choices as `rus_syn` — so the comparison only works on the
~4% of cases where both texts happen to render a lexeme with an overlapping vocabulary (614 of 15,364
compared content links this session, whole-NT). This is a real, corroboration-tier signal, weaker than a
manual positional gold, but the pattern it surfaces below is large-sample (413 cases) and matches this
language's own Grambank profile exactly, so it is trustworthy as a real base-chain behavior.

Russian notes (checked against `rus_syn`/RUSSYN's eflomal output, whole NT, this session):
- **Personal subject pronouns (`он` he, `она` she, `они` they, `я` I, `ты`/`вы` you, `мы` we) are the
  dominant pattern (~40% of undershoots) and belong to the verb's span when the source Greek verb is
  pro-drop (no separate Greek pronoun) and Russian morphology alone would leave the subject ambiguous.**
  Confirms the `subject_indexing` Grambank flag (GB089=1 suffix marking exists, GB090=0 no prefix marking
  — an INCOMPLETE paradigm, same shape as English). The real linguistic reason cuts deeper than the binary
  flag shows: Russian's PRESENT/FUTURE tense fully marks person by suffix (я иду́, ты идёшь, он идёт — no
  pronoun strictly needed), but the PAST tense marks only gender and number, not person at all (я/ты/он
  шёл are identical) — so a past-tense verb genuinely cannot express "who" without an explicit pronoun,
  while a present-tense verb technically can. Confirmed: Matthew 2:22 ἐφοβήθη ("he was afraid", aorist/past
  sense) renders with `он убоялся`, not `убоялся` alone; Matthew 2:11 εἶδον ("they saw") renders with `они
  увидели`.
- **Future/aspectual auxiliaries (`будет`/`будут` "will", `есть` "is/be") are a smaller but real pattern**
  — confirms the `tam_auxiliary` Grambank flag (GB119). Russian's synthetic future exists only for
  imperfective verbs; the periphrastic future (auxiliary `быть` + infinitive) is the ordinary way to render
  a perfective future, and belongs to the verb's span. Confirmed: Matthew 4:4 ζήσεται ("will live", future)
  renders with `будет жить`, not `жить` alone.
- **Case-marking (GB072) and possessive-affix (GB432/433) flags did NOT show a comparably clean pattern
  here** — plausible reason, not yet verified with the same rigor as the two patterns above: Russian
  expresses both oblique case and possession through NOUN INFLECTION (a suffix on the noun itself, e.g.
  дом/до́ма/до́му "house/of-house/to-house"), not a separate free word, so — the same lesson as `por`'s
  compound words and `arb`'s bound definite article — the Grambank flag being "on" does not predict a
  free-word problem when the language's actual realization is a bound affix rather than a separate token.
- This is a real pattern (413 cases, dominated by 8-10 pronoun/auxiliary word forms) but drawn from a
  smaller, noisier, translation-mismatched sample than the manual-gold languages — treat any specific
  verse's own evidence (a SEEDS rendering, or the source token's own person/tense marking) as decisive
  over this default.
