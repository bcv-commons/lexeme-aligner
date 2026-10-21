Target positions are the letter runs of the verse in reading order (right-to-left script; position numbers still follow reading
order). Vowel points and cantillation are removed from the displayed words (`במדבר` for `בַּמִּדְבָּר`), punctuation is dropped, and a
maqaf splits a compound (`בן־אלהים` is `בן` + `אלהים`). Position numbers refer to that tokenization.

Modern Hebrew notes (Greek New Testament -> Modern Hebrew; drafted 2026-10-20, not yet checked against a gold alignment):

- **Prefixes stay inside the word.** Hebrew writes the conjunction ו, the article ה, the prepositions ב (in/with), ל (to/for), מ (from),
  כ (as/like) and the relative ש as prefixes: `במדבר` "in the wilderness", `והאיש` "and the man", `כשהוא` "while he". The target word
  is NEVER split. It belongs to the CONTENT source word it renders (ἐρήμῳ owns `במדבר`).
- **A Greek function word carried by such a prefix is "rendered inside" that word**, not untranslated: ἐν, εἰς, ἐκ/ἀπό, ὡς, the article ὁ,
  καί/δέ, ὅτι/ὅς when they are realised as ב / ל / מ / כ / ה / ו / ש on a target word. Record it as carried by that word (see the answer
  codes), never as `=` (no rendering) when the prefix is there.
- **Free-standing function words get their own source word** like in any language: `את` (no Greek word of its own: it marks the
  definite object, so it goes with that object noun), `של` (genitive: goes with the possessor noun, the Greek genitive), `על`, `אל`,
  `עם`, `אצל`, `לפני`, `אחרי`, `בין`, `כי`, `אשר`, `גם`, `אף`, `לא` / `אל` (negation: with οὐ / μή), `הנה` (ἰδού), `אז` / `ואז`.
- **Pronominal suffixes** (`אליו` "to him", `חטאיהם` "their sins", `דרכך` "your way") render a Greek pronoun αὐτοῦ / σου / αὐτοῖς:
  that pronoun is carried by the word (as for prefixes); the word itself belongs to the content word (`חטאיהם` to ἁμαρτιῶν), or to the
  preposition when the word is a preposition with a suffix (`אליו` to πρός, αὐτόν carried inside it).
- **Construct chains** (`סליחת חטאים` "forgiveness of sins", `אנשי ירושלים`): the first noun is in construct form and carries the
  "of"; each noun goes to its own Greek word; the Greek article of the genitive is carried by ה on the second noun if present.
- **Verbs carry person and number in their form**: a Greek subject pronoun ἐγώ / αὐτός may have its own word (`הוא`, `אני`) — then that
  word is its rendering — or none (then `=`). Participles with `היה` / `יהיה` (periphrastic tense) go together with the Greek verb.
- **Periphrastic Hebrew for one Greek word** belongs entirely to that word: `כשהוא מכריז` for κηρύσσων (`כשהוא` carries the participle's
  "while"), `לשם` for εἰς "for (the purpose of)", `בהיותם` "while they were" for a participle.
- **The divine name `יהוה`** in quotations renders κύριος.
- Hebrew word order differs (verb-first clauses, adjectives after nouns, `כל` before its noun); do not expect the Greek order.
