Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped. Non-spacing marks
(viramas) are removed from the displayed words, so conjuncts look simplified (`उतपनन` for `उत्पन्न`): match by the
visible letters. Position numbers refer to that tokenization.

Hindi notes:
- Hindi is postpositional: `का / की / के`, `से`, `में`, `को`, `ने`, `पर`, `तक` follow the noun as separate words. They are
  function words; a Greek/Hebrew case or preposition aligns to the postposition only together with its noun.
- Verbs are a main verb plus auxiliaries (`है`, `था`, `थे`, `हुआ`, `गया`, `रहा`, `सकता`). Align the source verb to the main
  verb; include an auxiliary only when it carries tense/aspect the source verb encodes.
- Compound and light-verb constructions (`स्वीकार करना`, `प्रकट होना`): a noun or adjective + `होना / करना / देना`. Align the
  source verb to the whole compound (contiguous), light verb included.
- Conjunctions (`और`, `व`, `तथा`) are function words.
- Proper names are transliterated into Devanagari and spelling varies between editions (`हेसरोन` / `हिसरोन` / `हेजरोन`).
  Treat the given transliteration as a guide and match by sound, not by the exact spelling of a seed.
- Word order is SOV: the verb comes late in the clause; do not expect the source order.
