Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped; a hyphen
splits: an enclitic pronoun attached to a verb (`alegraram-se` is `alegraram` + `se`, `deixá-la` is `deixá` +
`la`) and a compound noun (`meia-noite` is `meia` + `noite`) are each two positions. Position numbers refer
to that tokenization.

Portuguese notes:
- **Enclitic pronouns are the dominant real error found in this language's base-chain output** (checked
  against Clear-Bible's transfer-provenance NT gold for `porjfa`/JFA11 this session: 781 verbs where the
  base chain's span stopped at the verb stem and left the hyphenated pronoun unclaimed, out of ~16,900 verb
  occurrences — by far the largest single error class found, dwarfing the Grambank-flagged categories that
  motivated looking at this language at all; see caveat below). A Portuguese verb followed by a hyphenated
  `-se/-te/-vos/-me/-nos/-lhe/-lhes/-o/-a/-os/-as/-lo/-la/-los/-las/-no/-na` is very often the CORRECT span
  for the source verb token, not a free-standing pronoun to leave aside — check before trimming it off:
  - **Deponent/passive/middle Greek verbs** commonly surface as a lexicalized Portuguese reflexive with no
    separate Greek word behind the clitic at all — the whole hyphenated form is the verb's span. Confirmed
    cases: Matthew 2:10 ἐχάρησαν ("they rejoiced", aorist passive-deponent) → `alegraram-se`, span
    `alegraram se`, not `alegraram` alone; Matthew 2:20 ἐγέρθητι ("arise", aorist passive imperative) →
    `Levanta-te`, span `Levanta te`.
  - **Some reflexives are pure Portuguese lexical choice**, not tied to Greek voice morphology at all —
    same rule, different source: Matthew 3:2 μετανοεῖτε ("repent", active voice, no passive/middle marking)
    still renders as `Arrependei-vos`, span `Arrependei vos`.
  - **Some are a genuine separate Greek pronoun folded in by Portuguese enclisis** — here the clitic
    corresponds to an actual accusative/dative source token, not the verb's own valency: Matthew 2:8
    καὶ πέμψας αὐτοὺς ("and having sent them") → `enviando-os`; the gold links `-os` to the sending verb's
    own span rather than to αὐτούς, but the safer general instruction is the same either way — do not strip
    the hyphenated pronoun from the verb's span unless it is clearly counted as its own separate `h` id with
    its own better claim to that position.
  - This is a real, large-sample pattern (checked against ~45,700 gold-comparable content links; not one
    hand-picked verse), but the THREE-WAY split above (lexicalized deponent / pure valency / genuine object
    pronoun) is diagnosed from a handful of confirmed cases, not verified exhaustively — if a specific
    verse's own evidence (an attested SEEDS rendering, or a listed pronoun `h` id that plausibly owns the
    position) argues against including the clitic, that verse's evidence wins.
- **Hyphenated compound words** (`meia-noite` "midnight", `meio-dia` "midday/noon", `bestas-feras` "wild
  beasts") are a much smaller pattern (4 of ~18,300 noun occurrences) but the same principle applies: both
  halves belong to the one source lexeme's span when the source word is a single content lexeme rendered as
  a Portuguese compound — Matthew 25:6 μεσονυκτίου ("midnight") → `meia-noite`, span `meia noite`.
- Articles (`o`, `a`, `os`, `as`, `um`, `uma`) and the tense/aspect auxiliaries this language's Grambank
  profile flags as typologically possible (`ter`, `haver`, `estar` + participle) did **not** show up as a
  real error pattern in this same gold check — a Grambank flag says a category is worth auditing, not that
  it IS a problem here; this language's real problem turned out to be pronominal enclisis (closer to
  Grambank's agent/argument-indexing territory than the tense-auxiliary or article categories originally
  suspected). Treat articles/auxiliaries normally: they belong to a span only when the source word carries
  their meaning.

**Evidentiary caveat:** unlike `fra`/`hin` (Clear-Bible manual gold), Portuguese's only Clear-Bible NT gold
rows are `method=transfer` — machine-projected from a manually-aligned edition via verse structure, not a
human annotation. The enclitic-pronoun pattern is corroborated by a genuinely large sample (781 cases) so
it is trustworthy as a real base-chain behavior; the specific gold LINK choice in ambiguous cases (e.g.
whether `-os` belongs to the verb or to a separate pronoun token) is a weaker evidentiary standard than a
manually-verified gold and should be treated as suggestive, not authoritative, when the two disagree.
