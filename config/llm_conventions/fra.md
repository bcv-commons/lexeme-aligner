Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped; an apostrophe
splits a word (`l'homme` is `l` + `homme`; likewise `d'`, `qu'`, `n'`, `s'`, `j'`, `c'`) and a hyphen splits a
compound (`Jean-Baptiste` is `jean` + `baptiste`, `dit-il` is `dit` + `il`). Position numbers refer to that
tokenization.

French notes:
- Articles (`le`, `la`, `les`, `un`), prepositions (`de`, `à`, `en`), the elided clitics (`l`, `d`, `qu`, `n`, `s`) and
  auxiliaries (`est`, `a`, `ont`) are function words. They belong to a span only when the source word carries their
  meaning: a Greek genitive is often `de` + noun, a plural article is `les` + noun.
- Negation is discontinuous: `ne ... pas / point / jamais`. A source negative aligns to `ne` + `pas` only when the
  span brackets the verb it negates.
- The compound past (`a dit`, `ont été`) is auxiliary + participle: align the source verb to the participle (or
  auxiliary + participle), never to the auxiliary alone.
- Reflexive/pronominal verbs carry a clitic (`se`, `s`): include it only when the source verb is middle/reflexive.
- A hyphenated name is two positions; align both (`Marie-Madeleine`).
