Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped. Non-spacing marks
(viramas) are removed from the displayed words, so conjuncts look simplified (`उतपनन` for `उत्पन्न`): match by the
visible letters. Position numbers refer to that tokenization.

Hindi notes:
- GENITIVE `का / की / के` belongs to the POSSESSOR it follows, never to the possessed noun after it: `दुष्टों की | योजना` —
  `की` goes with `दुष्टों` (in a Hebrew construct chain that is the second noun, the rectum), `योजना` alone with its own source word.
- Hindi is postpositional: `का / की / के`, `से`, `में`, `को`, `ने`, `पर`, `तक` follow the noun as separate words. A
  postposition immediately following a name or noun is normally PART OF THAT NOUN'S OWN SPAN, not a free-standing
  function word to trim off separately — a Greek/Hebrew genitive, dative, or preposition is usually realized BY
  that postposition, so the two travel together (e.g. "of Abraham" -> `अब्राहम से`, keeping `से`; "Mary's" ->
  `मरियम की`, keeping `की`). Do not remove a postposition from a name/noun span just because it is a "function
  word" in isolation — check whether removing it would leave the source word's case/relation unrepresented before
  trimming it.
- Verbs are a main verb plus auxiliaries (`है`, `था`, `थे`, `हुआ`, `गया`, `रहा`, `सकता`). The auxiliaries render the source verb's
  tense / aspect / person, so they ALWAYS go with that verb, contiguous with the main verb: `बैठता है` (not `बैठता` + `है` marked
  `+`). Never mark an auxiliary as supplied (`+s`) when a source verb is there to own it.
- Compound and light-verb constructions (`स्वीकार करना`, `प्रकट होना`): a noun or adjective + `होना / करना / देना`. Align the
  source verb to the whole compound (contiguous), light verb included.
- Conjunctions (`और`, `व`, `तथा`, `न`, `परन्तु`, `तब`) render the Hebrew prefix וְ / וַ (its own source id, the first morpheme of
  the next word) or Greek καί / δέ. Give the conjunction to THAT source id: in `और न` / `और दोनों` / `और उसकी पत्नी` the `और`
  belongs to the וְ of the following Hebrew word. Do not leave `और` unowned or `+s` when the source has a conjunction.
- Proper names are transliterated into Devanagari and spelling varies between editions (`हेसरोन` / `हिसरोन` / `हेजरोन`).
  Treat the given transliteration as a guide and match by sound, not by the exact spelling of a seed.
- Word order is SOV: the verb comes late in the clause; do not expect the source order.

Worked example (Matthew 1:2, a real case this project got wrong before this note existed): Greek
"Ἀβραὰμ ἐγέννησεν τὸν Ἰσαάκ" (Abraham begat Isaac) renders in this edition as "अबराहम से इसहाक उतपनन हुआ"
("from Abraham, Isaac was born") — a common Hindi strategy for "begat", marking the FATHER (the Greek
subject) with the ablative postposition `से`. Ἀβραάμ (h0) must align to त0+त1 (`अबराहम से`), not just त0
(`अबराहम`) — trimming `से` is wrong here even though it looks like a free-standing function word in
isolation, and even though the whole-language SEEDS for this exact name show bare `अबराहम` (no `से`) as
the dominant known rendering by a wide margin. That dominant form comes from Abraham's OTHER occurrences
(subject, object, etc.) where `से` does not apply — a seed's frequency reflects the name's occurrences
across the WHOLE language, not which grammatical construction THIS verse uses. Decide from this verse's
own construction; a strong seed majority is not evidence about this specific occurrence's case.
