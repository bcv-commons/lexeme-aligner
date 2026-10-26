`translation_text` is the verse as written (Latin script, tone marks kept). The positions of `translation_words` are the PARTS of
the words: a word the analysis divides appears as several translation words, each with `in_word` (the written word it belongs to)
and `part` (prefix, stem or suffix); a word that is not divided has neither. `akaba naayolekeelela` (MRK 1:7) is two written words
and seven translation words: `a` `ka` (prefixes) `ba` (stem) of `akaba`, then `na` `a` (prefixes) `yolekeelel` (stem) `a` (suffix) of
`naayolekeelela`. Answer about the parts; a part marked `prefix` with the same letters as a free word (`na`, `ni`, `ba`, `ti`) is the prefix, not
that word.

Zinza notes (Bibilia Endagaano Ensya, Wycliffe 2024; a Bantu language of Tanzania, Lake Victoria; drafted 2026-10-26 from the
bsb-add-books morph store — every entry there is marked low confidence, corpus-derived, not checked by a speaker — and from our
statistical alignment; NOT checked against a gold alignment — treat them as hints):

- **Verb parts carry what Greek puts in the verb form.** Give a part to a Greek word only when that Greek word renders the part's
  meaning; otherwise the part goes with the Greek verb (it is part of the verb's rendering), or is `none` when no Greek word
  licenses it:
  - subject markers (`n-` 1s, `o-` 2s, `a-`/`y-` 3s, `tu-`/`tw-` 1p, `mu-`/`mw-` 2p, `ba-`/`b-` 3p, before the tense part): the
    Greek verb's person; a separate Greek subject pronoun (ἐγώ, σύ, αὐτός) gets the free pronoun word if there is one, else this part;
  - tense parts (`ka-`, `a-`, `aá-` past; `na-`, `ni-` present) and the endings `-ile` (past), `-e` (subjunctive / imperative),
    `-w` / `-ibw` (passive), and the final vowel `-a`: go with the Greek verb, never with another word;
  - `ti-` (negation): goes to οὐ / μή (or another negative) when the Greek has one;
  - object markers (`mu-` him/her, `ba-` them, `ku-` you sg., `tu-` us, `n-` me; directly before the stem): go to the Greek object
    pronoun (αὐτόν, αὐτούς, σε, ἡμᾶς, με ...) when there is one, else with the verb;
  - `ku-` at the start of a verb is the infinitive (with an infinitive or a purpose clause); `-il` (applicative, "for / to someone")
    may render a dative or a preposition such as πρός when no other word does.
- **Greek has an article, Zinza does not.** ὁ / ἡ / τό usually has no rendering (`none`); a demonstrative (`ogu`, `ogwo`, `óogwo`)
  renders it only when the sense is "that / this one". Do not give an article the preposition `omu` or `na`.
- **Frequent free words** in the existing alignment: `na` / `no` / `ne` render καί (and "with" = μετά / σύν); `náho` renders δέ;
  `omu` / `omuli` render ἐν / εἰς and with `kuluga` ἐκ / ἀπό; `aha` / `ha` ἐπί or a location; `hali` / `ahali` πρός; `kuba` / `habwo`
  γάρ or ὅτι ("because"); `nikwo` / `nka` ὅτι or ὡς; `mbwéenu` / `bwéecho` οὖν; `aáti` introduces speech (λέγων / ὅτι). Check each case
  in its clause; these are tendencies, not rules.
- **Pronouns**: `íinye` ἐγώ, `íichwe` ἡμεῖς, `íiwe` σύ, `íimwe` ὑμεῖς; possessives follow the noun (`waanze` "my", `waawe` "your",
  `boómwe` "your" pl.).
- **Names** keep a recognisable spelling (`Yeézu`, `Kristu`, `Yohana`, `Peétero`, `Paulo`, `Múungu` = θεός): match them by position as well as by spelling.
