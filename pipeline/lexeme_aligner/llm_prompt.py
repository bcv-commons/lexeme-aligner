"""Prompt, packet and response-validation layer for the opt-in LLM-alignment experiment.

Pure functions only — no I/O, no network, no optional dependency — so the whole thing is unit-testable
offline (tests/test_llm_align.py) and `llm_align.py --provider mock/--dry-run` runs without an API key.

Design (internal-docs/llm-align-experiment-plan.md): the statistical chain (eflomal -> gloss -> gapfill,
seeded from the language's whole published lexeme-alignments) resolves most tokens deterministically; the
LLM only decides what is LEFT (`gap`, `gap-seeded`, `lexeme-grouped`), re-checks what the aligner was
unsure of (`verify`), or — as a reference point — does the whole verse (`full`). The contract below is
adapted from the ASV alignment project's (example/American-Standard-Version-Bible-Alignment-Data) rule
ledger, minus everything English-specific: ids partitioned exactly once, four statuses, a free-text `note`
naming the competing reading instead of a confidence grade.

A `Packet` is one API call's worth of work. Verse-level strategies emit one packet per verse; the
`lexeme-grouped` strategy emits one packet per lexeme chunk whose `members` are verse-level packets.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from lexeme_aligner.eflomal_align import _longest_contiguous
from lexeme_aligner.hebrew_source import HebToken

PROMPT_VERSION = "llm-align-v5"      # v5: SEEDS caveat — a seed's majority reflects frequency, not this
                                      # occurrence's grammatical role (found diagnosing hin's postposition bug)

STRATEGIES = ("full", "gap", "gap-seeded", "lexeme-grouped", "lexeme-verify", "verify")
SEEDED = ("full", "gap-seeded", "lexeme-grouped", "lexeme-verify")   # strategies carrying whole-language hints

ALIGNED = ("aligned", "noncompositional")            # statuses that become a pair
_DECLINED = ("unrepresented", "rejected")            # statuses that become an `llm_skipped` entry

# --- response schemas ---------------------------------------------------------------------------------
# additionalProperties:false + every field required, as both the Anthropic `output_config.format` and the
# `claude -p --json-schema` route want. Never embedded in the prompt text (the API enforces them).
_STATUS = {"type": "string", "enum": ["aligned", "unrepresented", "noncompositional"]}
_T_IDX = {"type": "array", "items": {"type": "integer"}}

SCHEMA_VERSE = {
    "type": "object", "additionalProperties": False, "required": ["ref", "alignments"],
    "properties": {
        "ref": {"type": "integer"},
        "alignments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["h_idx", "t_idx", "status", "note"],
            "properties": {"h_idx": {"type": "integer"}, "t_idx": _T_IDX, "status": _STATUS,
                           "note": {"type": "string"}}}}}}

SCHEMA_LEXEME = {
    "type": "object", "additionalProperties": False, "required": ["lexeme", "verses"],
    "properties": {
        "lexeme": {"type": "string"},
        "verses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["ref", "h_idx", "t_idx", "status", "note"],
            "properties": {"ref": {"type": "integer"}, "h_idx": {"type": "integer"}, "t_idx": _T_IDX,
                           "status": _STATUS, "note": {"type": "string"}}}}}}

SCHEMA_VERIFY = {
    "type": "object", "additionalProperties": False, "required": ["ref", "verdicts"],
    "properties": {
        "ref": {"type": "integer"},
        "verdicts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["h_idx", "status", "t_idx", "note"],
            "properties": {"h_idx": {"type": "integer"},
                           "status": {"type": "string", "enum": ["confirmed", "corrected", "rejected"]},
                           "t_idx": _T_IDX, "note": {"type": "string"}}}}}}

SCHEMA_LEXEME_VERIFY = {
    "type": "object", "additionalProperties": False, "required": ["lexeme", "verdicts"],
    "properties": {
        "lexeme": {"type": "string"},
        "verdicts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["ref", "h_idx", "status", "t_idx", "note"],
            "properties": {"ref": {"type": "integer"}, "h_idx": {"type": "integer"},
                           "status": {"type": "string", "enum": ["confirmed", "corrected", "rejected"]},
                           "t_idx": _T_IDX, "note": {"type": "string"}}}}}}

# `full` — the ASV-shaped whole-verse contract (port of example/American-Standard-Version-Bible-Alignment-
# Data's shape, minus its English-specific rules): a TWO-SIDED partition. Every source id appears in exactly
# one alignment's `h_idx` (singly, or grouped for `noncompositional`); every target id appears in exactly one
# alignment's `t_idx` — either claimed by a source group (`aligned`/`noncompositional`), or, when the
# translation supplies a word no listed source id licenses, by an `added` entry (`h_idx: []`). A source id with
# no defensible target is `unrepresented` (`t_idx: []`). `h_head`/`t_head` name the single id that carries the
# group's part of speech, for groups bigger than one.
_STATUS_FULL = {"type": "string", "enum": ["aligned", "unrepresented", "added", "noncompositional"]}
_INT_OR_NULL = {"type": ["integer", "null"]}

SCHEMA_FULL = {
    "type": "object", "additionalProperties": False, "required": ["ref", "alignments", "review_notes"],
    "properties": {
        "ref": {"type": "integer"},
        "alignments": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["h_idx", "h_head", "t_idx", "t_head", "status", "note"],
            "properties": {
                "h_idx": {"type": "array", "items": {"type": "integer"}},
                "h_head": _INT_OR_NULL,
                "t_idx": _T_IDX,
                "t_head": _INT_OR_NULL,
                "status": _STATUS_FULL,
                "note": {"type": "string"}}}},
        "review_notes": {"type": "array", "items": {"type": "string"}}}}


def schema_for(strategy: str) -> dict:
    return {"lexeme-grouped": SCHEMA_LEXEME, "lexeme-verify": SCHEMA_LEXEME_VERIFY, "verify": SCHEMA_VERIFY,
            "full": SCHEMA_FULL}.get(strategy, SCHEMA_VERSE)


def wrap_schema(item_schema: dict) -> dict:
    """The item schema, inside one ordered `results` array — the SAME per-verse contract, just a container.
    A packed request asks for the same objects a single request asks for; relaxing anything here (a looser
    field, a shared field hoisted out) would be a second contract, and the whole point of packing is that
    there is only ever one (port of the ASV project's `packing.wrap_schema`, generalised to a dict schema
    instead of a schema string). Deliberately no `minItems`/`maxItems` — unevenly supported across routes,
    and would only make the guarantee look stronger than it is; the count is stated in the prompt and
    checked by the caller instead (a verse missing from `results` is reported invalid, not guessed at)."""
    return {"type": "object", "additionalProperties": False, "required": ["results"],
            "properties": {"results": {"type": "array", "items": item_schema}}}


SCHEMA_FULL_PACKED = wrap_schema(SCHEMA_FULL)


# --- packet -------------------------------------------------------------------------------------------
@dataclass
class Packet:
    """One unit of LLM work. Position sets partition the verse's target tokens: `taken` (unavailable),
    `allowed` (plain available), `soft` (function words: available only inside a span that also holds a
    plain position)."""
    strategy: str
    ref: int
    book: str
    ch: int
    v: int
    label: str                                 # "fra, edition fra-lsg" — shown in the REF line
    toks: list[str]
    heb: list[HebToken]
    decide: list[int]                          # h_idx to decide, sorted
    allowed: list[int]
    soft: list[int]
    taken: list[int]
    resolved: dict[int, list[int]] = field(default_factory=dict)      # h_idx -> t_idx (already aligned)
    proposed: dict[int, tuple[list[int], float]] = field(default_factory=dict)   # verify: h_idx -> (t_idx, score)
    seeds: dict[str, list[tuple[str, int, float]]] = field(default_factory=dict)  # lexeme -> [(word, count, share)]
    meta: dict[str, dict] = field(default_factory=dict)               # lexeme -> {"pos", "translit"}
    lexeme: str | None = None                  # lexeme-grouped: the lexeme this chunk is about
    members: list["Packet"] = field(default_factory=list)            # lexeme-grouped: verse sub-packets

    @property
    def n_decide(self) -> int:
        return sum(len(m.decide) for m in self.members) if self.members else len(self.decide)


@dataclass
class Decision:
    h_idx: int
    t_idx: list[int]
    status: str                                # aligned | noncompositional | unrepresented | rejected | invalid
    note: str = ""
    tag: str | None = None                     # verify: "confirmed" | "corrected"
    repairs: list[str] = field(default_factory=list)

    @property
    def is_pair(self) -> bool:
        return self.status in ALIGNED and bool(self.t_idx)


@dataclass
class FullDecision:
    """One entry of a validated `full`-strategy (two-sided) response. `h_idx` can hold more than one id only
    for `noncompositional`; `added` always has `h_idx == []`; `unrepresented` always has `t_idx == []`."""
    h_idx: list[int]
    h_head: int | None
    t_idx: list[int]
    t_head: int | None
    status: str                                # aligned | noncompositional | unrepresented | added | invalid
    note: str = ""
    repairs: list[str] = field(default_factory=list)

    @property
    def is_pair(self) -> bool:
        return self.status in ALIGNED and bool(self.h_idx) and bool(self.t_idx)


# --- the stable prefix --------------------------------------------------------------------------------
_CONTRACT = """\
# Bible word-alignment task ({version})

You align the original-language words of one Bible verse (Hebrew or Greek) to the words of a translation
into {lang_name} ({publish_iso}). A statistical aligner has already handled most of the verse; you decide
only the source words the packet lists after `DECIDE:`. Everything else in the packet is context.

## How to read a packet
- `h<n>` is a source word: surface form, lemma in angle brackets when it differs, Strong's number, part of
  speech and an English gloss. A word marked `resolved -> t..` is already aligned (its positions are shown so
  you can see what is taken). A row marked `fn` is a function word, shown as context and never decided.
- `t<n>` is a target position: one word of the translation, numbered from 0 in reading order. Punctuation is
  not a position. A position marked `*` is taken by another source word and is unavailable. A position marked
  `~` is a function word (article, preposition, particle, auxiliary...) and may be used only inside a span
  that also contains at least one unmarked position.
- SEEDS list renderings of a lexeme attested elsewhere in this language, as `word count/share` (count = how
  often the word renders this lexeme; share = the fraction of that word's occurrences that render it).
  `@t7` marks a seed rendering that occurs in this verse at an available position. Seeds are evidence, not
  commands: the verse may use a different word, or an inflected form of a seed. A seed's count/share is
  taken across EVERY occurrence of that lexeme in the whole language — mostly whatever grammatical role
  (subject, object, plain unmarked form...) the word happens to take most often overall. It says nothing
  about which role THIS occurrence has. In a language that marks case, number, or definiteness with a
  separate word or affix (a postposition, an article, a case ending), a lopsidedly common seed is usually
  just the unmarked form winning by sheer frequency — it is not evidence that this verse's occurrence is
  also unmarked. Decide the case/role from this verse's own construction (the source word's own gloss,
  neighbouring words, its part of speech) before trusting a seed's majority.

## Rules
1. Decide every listed `h` exactly once. Never invent, renumber or omit an id.
2. Use only available target positions. Never use a `*` position.
3. Prefer the smallest span that realizes the source word's lexical meaning. One word is the usual answer.
   Use several positions only when the translation needs several words for one source word (`ne ... pas`, a
   phrasal verb, a compound), and include only the words that belong to it.
4. A span may be discontinuous only when its parts genuinely belong together. Never bridge unrelated words to
   reach a distant one.
5. Do not add a function word merely because it is adjacent. An article, preposition or auxiliary belongs to
   the span only when the source word itself carries that meaning (a genitive rendered `of the`, an inflected
   verb rendered with an auxiliary).
6. If the only available positions are function words, answer `unrepresented`.
7. Names: align to the target's rendering of the name; a name may span several words. A transliteration is
   given when known.
8. Light words ("be", "have", "say", "do", "all", "one") are often expressed by inflection or by a word another
   source word already holds. If the rendering is taken or absent, answer `unrepresented`. That is a normal,
   correct answer.
9. Prefer `unrepresented` to a guess. A wrong alignment is worse than none.
10. `noncompositional`: only when two or more listed source words are rendered by one fused expression that
    cannot be split. Give each of them the same span and mark each `noncompositional`.
11. A target position belongs to at most one source word, except inside a noncompositional group.
12. Decide from THIS verse's meaning and word order, not only from the seeds.
13. `note`: at most 15 words. When you chose between two readings, name the one you rejected. Empty is fine
    when the answer is clear.
14. Output only the JSON object the schema requires. No prose, no code fences.
"""

_STRATEGY = {
    "full": """\
## Strategy: full — whole-verse, two-sided partition
Nothing in this verse is pre-decided. EVERY listed `h` (content and function words both) must appear in
exactly one alignment entry, and EVERY `t` position must appear in exactly one entry too. This is stricter
than the other strategies: you are not just choosing spans for a subset, you are partitioning the whole verse.
A line marked `(aligner proposed -> t..)` is a statistical aligner's own guess for that word, given as
evidence only — confirm it, correct it, or ignore it; it is never binding and never marks a position taken.
A DECIDE content word's lexeme may also carry a SEEDS line (renderings attested elsewhere in this
language, by count/share, as in the seeded strategies) — evidence for consistency, not a command; the
verse's own words and word order still decide the answer when they point elsewhere. Function words never
carry SEEDS (their renderings are formulaic, not worth showing).

Four statuses:
- `aligned` — one source id, its target span. Usual case.
- `noncompositional` — two or more source ids fused into one target span that cannot be split (a light-verb
  construction, an idiom). List every id it covers in `h_idx`, name the syntactic head in `h_head`, give all
  of them the SAME `t_idx`.
- `unrepresented` — a source id (typically a function word already carried by an inflection, or a light word
  the translation omits) has no defensible target span. `t_idx: []`.
- `added` — a target word that no source id licenses (a supplied word the translation needs but nothing in
  the original states outright: an inserted "the", a connective the translator added for flow). `h_idx: []`,
  `h_head: null`.

For a single-id entry, set `h_head` to that same id and `t_head` to the one target id that best carries the
word's part of speech (the noun in a noun phrase, the verb in a verb phrase) — usually the last content word
of the span. Every source id from `DECIDE:` must be used in `h_idx` across the array exactly once (never
split across two entries, never omitted, never invented). Every target id from 0 to the last one shown must
appear in exactly one entry's `t_idx` — if you truly cannot place one, still record it as its own `added`
entry with a `note` explaining why, rather than leaving it out. `review_notes` is a place for anything you
want a reviewer to double-check (ambiguous idiom, a variant reading, a rare word) — usually empty (`[]`).
Return {"ref": <int>, "alignments": [{"h_idx": [...], "h_head": <int|null>, "t_idx": [...],
"t_head": <int|null>, "status", "note"}, ...], "review_notes": [...]}.
""",
    "gap": """\
## Strategy: gap
The statistical aligner already aligned the `resolved` words. You get only the words it left unaligned
(`DECIDE`) and the target positions still available. Return
{"ref": <int>, "alignments": [{"h_idx", "t_idx", "status", "note"}, ...]}, one entry per DECIDE word.
""",
    "gap-seeded": """\
## Strategy: gap-seeded
As `gap`, and each DECIDE lexeme also carries SEEDS (renderings attested elsewhere in this language). Use them
to recognize the word quickly; fall back to the verse when no seed fits. Return
{"ref": <int>, "alignments": [{"h_idx", "t_idx", "status", "note"}, ...]}, one entry per DECIDE word.
""",
    "lexeme-grouped": """\
## Strategy: lexeme-grouped
One lexeme is shown with its SEEDS, followed by several verses that each contain an unaligned occurrence of it
(marked DECIDE). Decide each occurrence on its own verse's evidence. When several verses use the same
rendering, staying consistent is good evidence; a verse that differs is fine if its words differ. Return
{"lexeme": "<the lexeme>", "verses": [{"ref", "h_idx", "t_idx", "status", "note"}, ...]}, one entry per
DECIDE occurrence.
""",
    "verify": """\
## Strategy: verify
The statistical aligner proposed a span for each DECIDE word but was not sure (`PROPOSED`). For each, answer:
`confirmed` (the proposal is right; repeat its positions in `t_idx`), `corrected` (give the right span among
the available positions), or `rejected` (no available position is a correct rendering; `t_idx` empty). Return
{"ref": <int>, "verdicts": [{"h_idx", "status", "t_idx", "note"}, ...]}, one entry per DECIDE word.
""",
    "lexeme-verify": """\
## Strategy: lexeme-verify
A REVIEW pass over a completed `full` alignment, batched by lexeme instead of by verse — is the SAME
original-language word rendered consistently across every place it occurs? One lexeme is shown with its
SEEDS, followed by every occurrence's own `PROPOSED` span (what the earlier pass decided) and its verse
context. For each occurrence answer `confirmed` (the earlier decision is right; repeat its positions in
`t_idx`), `corrected` (give the right span among the positions shown in that verse's TARGET line), or
`rejected` (no position in that verse is a correct rendering; `t_idx` empty). A rendering that differs from
the rest of the group is not automatically wrong — trust the verse's own words and word order over the
majority when they disagree; note why when you correct or reject. Return {"lexeme": "<the lexeme>",
"verdicts": [{"ref", "h_idx", "status", "t_idx", "note"}, ...]}, one entry per occurrence shown.
""",
}

_GENERIC_CONVENTIONS = """\
Target positions are the letter/mark runs of the verse in reading order. Punctuation is dropped, an
apostrophe splits a word (`l'homme` is `l` + `homme`) and a hyphen splits a compound. Non-spacing combining
marks (Indic viramas, Arabic harakat, Hebrew points) are removed from the displayed words, so a conjunct or a
vocalized word looks simplified (`उतपनन` for `उत्पन्न`): match by the visible letters. Position numbers refer
to that tokenization."""

_EXAMPLE_PACKET = """\
REF 1001001  GEN 1:1  (English, edition example)
SOURCE:
  h0 רֵאשִׁית H7225 noun "beginning"  resolved -> t2
  h1 בָּרָא H1254 verb "to create"  DECIDE
  h2 אֱלֹהִים H0430 noun "God"  resolved -> t3
  h3 שָׁמַיִם H8064 noun "heaven"  DECIDE
  h4 אֶרֶץ H0776 noun "earth"  DECIDE
TARGET:
  t0:in~ t1:the~ t2:beginning* t3:god* t4:created t5:the~ t6:heavens t7:and~ t8:the~ t9:earth
DECIDE: h1, h3, h4
"""

_EXAMPLES = {
    "verse": _EXAMPLE_PACKET + """\
ANSWER: {"ref": 1001001, "alignments": [
 {"h_idx": 1, "t_idx": [4], "status": "aligned", "note": ""},
 {"h_idx": 3, "t_idx": [6], "status": "aligned", "note": "plural noun; the article stays out"},
 {"h_idx": 4, "t_idx": [9], "status": "aligned", "note": ""}]}
""",
    "lexeme-grouped": """\
LEXEME hbo:8064  H8064 noun  known renderings: heavens 40/0.9, heaven 12/0.8
[REF 1001001  GEN 1:1]  DECIDE: h3   (context: ...beginning->beginning | h3 | earth...)
  t0:in~ t1:the~ t2:beginning* t3:god* t4:created* t5:the~ t6:heavens t7:and~ t8:the~ t9:earth*
ANSWER: {"lexeme": "hbo:8064", "verses": [
 {"ref": 1001001, "h_idx": 3, "t_idx": [6], "status": "aligned", "note": ""}]}
""",
    "verify": """\
REF 1001001  GEN 1:1  (English, edition example)
SOURCE:
  h0 רֵאשִׁית H7225 noun "beginning"  resolved -> t2
  h1 בָּרָא H1254 verb "to create"  resolved -> t4
  h2 אֱלֹהִים H0430 noun "God"  resolved -> t3
  h3 שָׁמַיִם H8064 noun "heaven"  PROPOSED -> t6 (score 0.6)
  h4 אֶרֶץ H0776 noun "earth"  PROPOSED -> t8 (score 0.6)
TARGET:
  t0:in~ t1:the~ t2:beginning* t3:god* t4:created* t5:the~ t6:heavens t7:and~ t8:the~ t9:earth
DECIDE: h3, h4
ANSWER: {"ref": 1001001, "verdicts": [
 {"h_idx": 3, "status": "confirmed", "t_idx": [6], "note": ""},
 {"h_idx": 4, "status": "corrected", "t_idx": [9], "note": "proposal took the article t8, not the noun"}]}
""",
    "full": """\
REF 1001001  GEN 1:1  (English, edition example)
SOURCE:
  h0 בְּ prep "in" (function)  DECIDE
  h1 רֵאשִׁית H7225 noun "beginning"  DECIDE  (aligner proposed -> t1, t2)
  h2 בָּרָא H1254 verb "to create"  DECIDE  (aligner proposed -> t4)
  h3 אֱלֹהִים H0430 noun "God"  DECIDE
  h4 שָׁמַיִם H8064 noun "heaven"  DECIDE
  h5 וְ conj "and" (function)  DECIDE
  h6 אֶרֶץ H0776 noun "earth"  DECIDE
TARGET:
  t0:In t1:the~ t2:beginning t3:God t4:created t5:the~ t6:heavens t7:and~ t8:the~ t9:earth
DECIDE: h0, h1, h2, h3, h4, h5, h6
ANSWER: {"ref": 1001001, "alignments": [
 {"h_idx": [0], "h_head": 0, "t_idx": [0], "t_head": 0, "status": "aligned", "note": ""},
 {"h_idx": [1], "h_head": 1, "t_idx": [1, 2], "t_head": 2, "status": "aligned", "note": ""},
 {"h_idx": [2], "h_head": 2, "t_idx": [4], "t_head": 4, "status": "aligned", "note": "aligner's proposal confirmed"},
 {"h_idx": [3], "h_head": 3, "t_idx": [3], "t_head": 3, "status": "aligned", "note": ""},
 {"h_idx": [4], "h_head": 4, "t_idx": [5, 6], "t_head": 6, "status": "aligned", "note": "plural noun"},
 {"h_idx": [5], "h_head": 5, "t_idx": [7], "t_head": 7, "status": "aligned", "note": ""},
 {"h_idx": [6], "h_head": 6, "t_idx": [8, 9], "t_head": 9, "status": "aligned", "note": ""}],
 "review_notes": []}
Every h0-h6 appears exactly once; every t0-t9 appears exactly once. Three more cases, shown in isolation
(not part of this verse): a light-verb idiom realized by one fused span —
 {"h_idx": [7, 8], "h_head": 8, "t_idx": [11, 12], "t_head": 12, "status": "noncompositional",
  "note": "\\"gave answer\\" for h7 (gave) + h8 (answer), a fixed idiom for \\"answered\\""}
— a source word the translation drops (its meaning was already carried by an inflection kept elsewhere) —
 {"h_idx": [9], "h_head": 9, "t_idx": [], "t_head": null, "status": "unrepresented",
  "note": "resumptive pronoun; ASV's verb ending already carries it"}
— and a target word the translation supplies that nothing in the source states —
 {"h_idx": [], "h_head": null, "t_idx": [14], "t_head": null, "status": "added",
  "note": "translator-supplied \\"therefore\\" for English flow"}
""",
    "lexeme-verify": """\
LEXEME hbo:8064  H8064 noun  known renderings: heavens 40/0.9, heaven 12/0.8
[REF 1001001  GEN 1:1]  h3  PROPOSED -> t6  context: ...beginning->beginning | [heaven] | earth->earth...
  t0:In t1:the~ t2:beginning t3:God t4:created t5:the~ t6:heavens t7:and~ t8:the~ t9:earth
[REF 1010003  GEN 10:3]  h2  PROPOSED -> t3  context: ...Gomer->Gomer | [heaven] | Riphath->Riphath...
  t0:the~ t1:sons t2:of~ t3:Gomer t4:sky t5:and~ t6:Riphath
ANSWER: {"lexeme": "hbo:8064", "verdicts": [
 {"ref": 1001001, "h_idx": 3, "status": "confirmed", "t_idx": [6], "note": ""},
 {"ref": 1010003, "h_idx": 2, "status": "corrected", "t_idx": [4],
  "note": "proposal took the neighbouring name Gomer (t3); the lexeme's own word is sky (t4)"}]}
""",
}


def render_prefix(publish_iso: str, lang_name: str, strategy: str, conventions_md: str | None = None) -> str:
    """The stable, cacheable system prefix. Byte-deterministic: identical inputs -> identical string (no
    timestamps, no dict-order dependence), because a prompt cache is a prefix match and any drift here would
    silently turn every request into a cold write."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    example = _EXAMPLES.get(strategy, _EXAMPLES["verse"])
    return "\n".join([
        _CONTRACT.format(version=PROMPT_VERSION, lang_name=lang_name, publish_iso=publish_iso),
        _STRATEGY[strategy],
        f"## Language conventions ({lang_name})",
        (conventions_md or _GENERIC_CONVENTIONS).strip() + "\n",
        "## Worked example (English target, for the format only)",
        example,
    ])


# --- suffix rendering ---------------------------------------------------------------------------------
_MAX_GLOSS = 40


def _strong_lexeme(tok: HebToken) -> str:
    """`G1078` — plus the lexeme only when it carries information the Strong's number doesn't (an augment
    letter, or a rollup onto a different number)."""
    s = tok.strong or ""
    lx = tok.lexeme or ""
    num = lx.split(":", 1)[1] if ":" in lx else lx
    if not lx or (s and num.lstrip("0") == s[1:].lstrip("0")):
        return s
    return f"{s} {lx}".strip()


def _source_row(tok: HebToken, state: str, pos: str | None) -> str:
    lemma = f" <{tok.lemma}>" if tok.lemma and tok.lemma.lower() != tok.surface.lower() else ""
    gloss = f' "{tok.gloss_en[:_MAX_GLOSS]}"' if tok.gloss_en else ""
    return f"  h{tok.idx} {tok.surface}{lemma} {_strong_lexeme(tok)}{' ' + pos if pos else ''}{gloss}{state}".rstrip()


def _fmt_pos(ts: Iterable[int]) -> str:
    return " ".join(f"t{t}" for t in ts)


def _target_line(p: Packet) -> str:
    taken, soft = set(p.taken), set(p.soft)
    out = []
    for j, w in enumerate(p.toks):
        out.append(f"t{j}:{w}{'*' if j in taken else '~' if j in soft else ''}")
    return "  " + " ".join(out)


def _present(p: Packet) -> dict[str, list[int]]:
    """word (lowercased) -> the AVAILABLE positions where it occurs in this verse."""
    avail = set(p.allowed) | set(p.soft)
    words: dict[str, list[int]] = {}
    for j in sorted(avail):
        if j < len(p.toks):
            words.setdefault(p.toks[j].lower(), []).append(j)
    return words


def _seed_line(lexeme: str, p: Packet, present: dict[str, list[int]] | None) -> str:
    meta = p.meta.get(lexeme, {})
    head = f"  {lexeme}"
    extra = ", ".join(f"{k} {v}" for k, v in (("pos", meta.get("pos")), ("translit", meta.get("translit"))) if v)
    if extra:
        head += f" ({extra})"
    renders = p.seeds.get(lexeme, [])
    if not renders:
        return f"{head} — no rendering attested yet"
    parts = []
    for word, count, share in renders:
        mark = ""
        if present is not None and word.lower() in present:
            mark = " @" + ",".join(f"t{j}" for j in present[word.lower()])
        parts.append(f"{word} {count}/{share:.2f}{mark}")
    return f"{head} — known renderings: " + ", ".join(parts)


def _header(p: Packet) -> str:
    return f"REF {p.ref}  {p.book} {p.ch}:{p.v}  ({p.label})"


def render_verse_suffix(p: Packet) -> str:
    """The per-verse user message. Plain text, not JSON (~40% fewer tokens); deterministic order."""
    show_fn = p.strategy == "full"
    lines = [_header(p), "SOURCE:"]
    decide = set(p.decide)
    for tok in p.heb:
        if tok.idx in decide:
            if p.strategy == "verify" and tok.idx in p.proposed:
                ts, score = p.proposed[tok.idx]
                state = f"  PROPOSED -> {_fmt_pos(ts)} (score {score:.1f})"
            else:
                state = "  DECIDE"
        elif tok.idx in p.resolved:
            ts = p.resolved[tok.idx]
            released = "" if set(ts) & set(p.taken) else " (slot still available)"
            state = f"  resolved -> {_fmt_pos(ts)}{released}"
        elif show_fn and not (tok.strong and tok.is_content):
            state = "  fn"
        else:
            continue
        lines.append(_source_row(tok, state, p.meta.get(tok.lexeme or "", {}).get("pos")))
    lines += ["TARGET:", _target_line(p), "DECIDE: " + ", ".join(f"h{h}" for h in p.decide)]
    if p.seeds:
        present = _present(p)
        lines.append("SEEDS:")
        seen: set[str] = set()
        for tok in p.heb:
            if tok.idx in decide and tok.lexeme and tok.lexeme not in seen:
                seen.add(tok.lexeme)
                lines.append(_seed_line(tok.lexeme, p, present))
    return "\n".join(lines) + "\n"


def render_full_verse_suffix(p: Packet) -> str:
    """The `full`-strategy user message: every source token is DECIDE (function words included — nothing is
    context-only), every target token is free (nothing is `*` taken). `p.resolved` still carries the base
    statistical chain's own proposal per h_idx, but here it is shown as a non-binding EVIDENCE hint on the
    DECIDE line itself, never as an already-settled state. SEEDS (v3) cover CONTENT lexemes only — a
    function word's "known renderings" are formulaic and not worth the tokens; `p.seeds` only has keys for
    the lexemes actually worth seeding (see `_verse_packet`'s content-only `seed_scope`), so membership in
    that dict, not truthiness of `tok.lexeme`, is what decides whether a lexeme gets a SEEDS line."""
    lines = [_header(p), "SOURCE:"]
    decide = set(p.decide)
    for tok in p.heb:
        ev = p.resolved.get(tok.idx)
        state = f"  DECIDE  (aligner proposed -> {_fmt_pos(ev)})" if ev else "  DECIDE"
        lines.append(_source_row(tok, state, p.meta.get(tok.lexeme or "", {}).get("pos")))
    lines += ["TARGET:", _target_line(p), "DECIDE: " + ", ".join(f"h{h}" for h in p.decide)]
    if p.seeds:
        present = _present(p)
        lines.append("SEEDS:")
        seen: set[str] = set()
        for tok in p.heb:
            if tok.idx in decide and tok.lexeme in p.seeds and tok.lexeme not in seen:
                seen.add(tok.lexeme)
                lines.append(_seed_line(tok.lexeme, p, present))
    return "\n".join(lines) + "\n"


def _neighbourhood(p: Packet, h_idx: int, k: int = 2) -> str:
    """The k content words either side of a DECIDE word, each with its already-aligned target word."""
    content = [t for t in p.heb if (t.strong and t.is_content) or t.idx == h_idx]
    at = next((i for i, t in enumerate(content) if t.idx == h_idx), None)
    if at is None:
        return ""
    def one(t: HebToken) -> str:
        if t.idx == h_idx:
            return f"[{t.surface}]"
        ts = p.resolved.get(t.idx)
        got = " ".join(p.toks[j] for j in ts if j < len(p.toks)) if ts else "?"
        return f"{t.surface}->{got}"
    return " | ".join(one(t) for t in content[max(0, at - k): at + k + 1])


def render_lexeme_suffix(p: Packet) -> str:
    """One lexeme card + N verse blocks (the `lexeme-grouped` user message)."""
    assert p.lexeme and p.members, "render_lexeme_suffix needs a lexeme-grouped packet"
    lines = [f"LEXEME {p.lexeme}", _seed_line(p.lexeme, p, None).strip(), f"{len(p.members)} verse(s):", ""]
    for m in p.members:
        present = _present(m)
        lines.append(f"[{_header(m)}]")
        for h in m.decide:
            lines.append(f"  DECIDE h{h}   context: {_neighbourhood(m, h)}")
        seed_hits = [f"{w}@" + ",".join(f"t{j}" for j in present[w.lower()])
                     for w, _c, _s in p.seeds.get(p.lexeme, []) if w.lower() in present]
        if seed_hits:
            lines.append("  seeds present here: " + ", ".join(seed_hits))
        lines.append(_target_line(m))
        lines.append("")
    return "\n".join(lines)


def render_lexeme_verify_suffix(p: Packet) -> str:
    """One lexeme card + N occurrence blocks (the `lexeme-verify` user message) — a REVIEW of a completed
    `full` pass, not a fresh decision: each occurrence shows what that pass already decided (`PROPOSED`,
    exactly as `verify` renders a low-confidence pair) plus its verse context, for a cross-verse consistency
    check. Far cheaper than re-deciding the verse: only the lexemes with more than one occurrence are ever
    shown, and each occurrence costs one small block, not a whole verse's worth of source+target rows."""
    assert p.lexeme and p.members, "render_lexeme_verify_suffix needs a lexeme-verify packet"
    lines = [f"LEXEME {p.lexeme}", _seed_line(p.lexeme, p, None).strip(), f"{len(p.members)} occurrence(s):", ""]
    for m in p.members:
        h = m.decide[0]
        ts, score = m.proposed[h]
        lines.append(f"[{_header(m)}]  h{h}  PROPOSED -> {_fmt_pos(ts)}  context: {_neighbourhood(m, h)}")
        lines.append(_target_line(m))
        lines.append("")
    return "\n".join(lines)


def raw_from_lexeme_verify(resp: dict, group: Packet) -> dict[int, list[dict]]:
    """A `lexeme-verify` response -> {ref: raw items}. `confirmed` restores the group member's own PROPOSED
    span (the same trick `raw_from_verify` uses for a single verse, generalised across the group's several
    (ref, h) occurrences); `corrected` uses the model's span; `rejected` declines."""
    proposed = {(m.ref, m.decide[0]): m.proposed[m.decide[0]] for m in group.members}
    out: dict[int, list[dict]] = {}
    for v in (resp or {}).get("verdicts", []):
        if not (isinstance(v, dict) and isinstance(v.get("ref"), int) and isinstance(v.get("h_idx"), int)):
            continue
        key, status, note = (v["ref"], v["h_idx"]), v.get("status"), str(v.get("note") or "")
        if status == "confirmed":
            ts = proposed.get(key, ([], 0.0))[0]
            item = {"h_idx": v["h_idx"], "t_idx": list(ts), "status": "aligned", "note": note, "tag": "confirmed"}
        elif status == "corrected":
            item = {"h_idx": v["h_idx"], "t_idx": v.get("t_idx") or [], "status": "aligned", "note": note,
                    "tag": "corrected"}
        else:
            item = {"h_idx": v["h_idx"], "t_idx": [], "status": "rejected", "note": note}
        out.setdefault(v["ref"], []).append(item)
    return out


def render_packed_suffix(members: list[Packet]) -> str:
    """Several complete verse tasks, one wrapper (the packed-call mode; port of the ASV project's
    `packing.packed_prompt`, adapted to `full`'s per-verse body). Each member keeps its own
    REF/SOURCE/TARGET/DECIDE/SEEDS block exactly as a single-verse `full` call renders it — nothing about a
    verse's own task changes when it travels with others — and the wrapper instruction is stated ONCE at
    the end, not once per verse, so it never contradicts a per-verse rule."""
    bodies = [render_full_verse_suffix(m) for m in members]
    return ("\n---\n".join(bodies) +
            f"\n\nReturn ONE object: {{\"results\": [<{len(members)} objects, one per REF above, in the SAME "
            f"order, each the usual {{\"ref\", \"alignments\", \"review_notes\"}} shape>]}}.\n")


def raw_from_packed(resp: dict) -> dict[int, list[dict]]:
    """A packed `full` response -> {ref: alignments}. Each item carries its own `ref` (the ordinary `full`
    schema requires it), so a verse the response reordered, duplicated or omitted is identified correctly —
    or correctly reported missing by `normalize_full`'s existing "missing from the response" path — rather
    than matched by position in the array."""
    out: dict[int, list[dict]] = {}
    for item in (resp or {}).get("results", []):
        if isinstance(item, dict) and isinstance(item.get("ref"), int):
            out[item["ref"]] = raw_from_verse(item)
    return out


def render_suffix(p: Packet) -> str:
    if p.strategy == "lexeme-grouped":
        return render_lexeme_suffix(p)
    if p.strategy == "lexeme-verify":
        return render_lexeme_verify_suffix(p)
    if p.strategy == "full":
        return render_packed_suffix(p.members) if p.members else render_full_verse_suffix(p)
    return render_verse_suffix(p)


# --- seeds --------------------------------------------------------------------------------------------
def seed_renderings(lexeme: str, vocab_scored: dict[str, dict[str, tuple[int, float]]],
                    top_k: int = 6, min_share: float = 0.05) -> list[tuple[str, int, float]]:
    """The whole-language known renderings of `lexeme` from the published lexeme-alignments
    (`reverse_align_check.load_lexeme_vocab_scored`): (word, count, share), best evidence first. Ranked by
    count x share, i.e. frequent AND exclusive to this lexeme — a word that renders many lexemes weakly
    (a bare `de`) sinks below one that renders this lexeme reliably, even at a lower raw count."""
    rows = [(w, c, s) for w, (c, s) in vocab_scored.get(lexeme, {}).items() if s >= min_share]
    rows.sort(key=lambda r: (-r[1] * r[2], -r[1], r[0]))
    return rows[:top_k]


# --- response handling --------------------------------------------------------------------------------
def raw_from_verse(resp: dict) -> list[dict]:
    return [dict(a) for a in (resp or {}).get("alignments", []) if isinstance(a, dict)]


def raw_from_lexeme(resp: dict) -> dict[int, list[dict]]:
    """A lexeme-grouped response -> raw items per verse ref, in response order."""
    out: dict[int, list[dict]] = {}
    for a in (resp or {}).get("verses", []):
        if isinstance(a, dict) and isinstance(a.get("ref"), int):
            out.setdefault(a["ref"], []).append(dict(a))
    return out


def raw_from_verify(resp: dict, packet: Packet) -> list[dict]:
    """Verify verdicts -> raw items. `confirmed` restores the PROPOSED span itself (the model only has to
    say yes); `corrected` uses the model's span; `rejected` declines."""
    out = []
    for a in (resp or {}).get("verdicts", []):
        if not (isinstance(a, dict) and isinstance(a.get("h_idx"), int)):
            continue
        st = a.get("status")
        if st == "confirmed":
            ts = packet.proposed.get(a["h_idx"], ([], 0.0))[0]
            out.append({"h_idx": a["h_idx"], "t_idx": list(ts), "status": "aligned", "note": a.get("note", ""),
                        "tag": "confirmed"})
        elif st == "corrected":
            out.append({"h_idx": a["h_idx"], "t_idx": a.get("t_idx") or [], "status": "aligned",
                        "note": a.get("note", ""), "tag": "corrected"})
        else:
            out.append({"h_idx": a["h_idx"], "t_idx": [], "status": "rejected", "note": a.get("note", "")})
    return out


def review_notes_from(resp: dict) -> list[str]:
    return [str(n) for n in (resp or {}).get("review_notes", []) if isinstance(n, str)]


def normalize_full(raw_items: list[dict], packet: Packet, *, allow_scattered: bool = False
                   ) -> tuple[list[FullDecision], list[str], dict]:
    """Validate a `full`-strategy (two-sided) response. Guarantees: every id in `packet.decide` is claimed by
    exactly one entry's `h_idx` (singly, or as part of a `noncompositional` group, or synthesized as
    `invalid` when the model never mentioned it); a kept target position is claimed by at most one entry; a
    span is contiguous unless `allow_scattered`. Unlike `normalize`, this validates BOTH sides — a target
    position no entry claims is reported in `stats["target_unclaimed"]`, never silently invented into an
    `added` entry (that would credit the model with coverage it did not actually produce)."""
    decide = set(packet.decide)
    n_t = len(packet.toks)
    claimed_h: dict[int, int] = {}
    claimed_t: dict[int, int] = {}
    decisions: list[FullDecision] = []
    packet_repairs: list[str] = []

    for item in raw_items:
        raw_h = item.get("h_idx")
        hs = sorted({h for h in (raw_h if isinstance(raw_h, list) else []) if isinstance(h, int)})
        status = item.get("status") if item.get("status") in ("aligned", "unrepresented", "added",
                                                               "noncompositional") else "invalid"
        note = str(item.get("note") or "").strip()[:200]
        repairs: list[str] = []
        unknown = [h for h in hs if h not in decide]
        if unknown:
            repairs.append(f"dropped unknown h_idx {unknown}")
            hs = [h for h in hs if h in decide]
        dup = [h for h in hs if h in claimed_h]
        if dup:
            repairs.append(f"dropped h_idx already claimed by another entry {dup}")
            hs = [h for h in hs if h not in claimed_h]
        if status == "invalid":
            repairs.append(f"unknown status {item.get('status')!r}")
        if status == "added" and hs:
            repairs.append("`added` must carry no h_idx; entry demoted to aligned")
            status = "aligned"
        if status != "added" and not hs:
            packet_repairs.append("dropped an entry left with no usable h_idx")
            continue

        raw_t = item.get("t_idx")
        ts = sorted({t for t in (raw_t if isinstance(raw_t, list) else []) if isinstance(t, int)})
        valid_t = [t for t in ts if 0 <= t < n_t]
        if len(valid_t) != len(ts):
            repairs.append("removed out-of-range t_idx " + str(sorted(set(ts) - set(valid_t))))
        kept_t = []
        for t in valid_t:
            if t in claimed_t:
                repairs.append(f"t{t} already claimed by another entry")
            else:
                kept_t.append(t)
        if kept_t and not allow_scattered:
            run = _longest_contiguous(kept_t, set())
            if run != kept_t:
                repairs.append("trimmed to the longest contiguous run")
                kept_t = run
        if status == "unrepresented" and kept_t:
            repairs.append("`unrepresented` carried t_idx; cleared")
            kept_t = []
        if status in ("aligned", "noncompositional") and not kept_t:
            repairs.append("no usable target positions; demoted to unrepresented")
            status = "unrepresented"
        if status == "added" and not kept_t:
            packet_repairs.append("dropped an empty `added` entry")
            continue

        h_head = item.get("h_head") if item.get("h_head") in hs else (hs[0] if hs else None)
        t_head = item.get("t_head") if item.get("t_head") in kept_t else (kept_t[-1] if kept_t else None)
        d = FullDecision(hs, h_head, kept_t, t_head, status, note, repairs)
        idx = len(decisions)
        decisions.append(d)
        for h in hs:
            claimed_h[h] = idx
        for t in kept_t:
            claimed_t[t] = idx
        packet_repairs.extend(f"h{hs or '[]'}/t{kept_t}: {r}" for r in repairs)

    for h in sorted(decide - set(claimed_h)):
        decisions.append(FullDecision([h], h, [], None, "invalid", "", ["missing from the response"]))
        packet_repairs.append(f"h{h}: missing from the response")

    unclaimed_t = sorted(set(range(n_t)) - set(claimed_t))
    stats = {"target_unclaimed": len(unclaimed_t), "unclaimed_t_idx": unclaimed_t,
             "added": sum(1 for d in decisions if d.status == "added")}
    return decisions, packet_repairs, stats


def derive_score_full(agrees: bool, base: float = 0.75, agree_score: float = 0.9) -> float:
    """Same reasoning as `derive_score`, for `FullDecision`s (no verify `tag` to check)."""
    return agree_score if agrees else base


def normalize(raw_items: list[dict], packet: Packet, *, allow_scattered: bool = False
              ) -> tuple[list[Decision], list[str]]:
    """Validate a model's answer against the packet and repair what can be repaired, counting every repair.

    Guarantees: exactly one Decision per `packet.decide` id; every kept position is available; no position
    is claimed twice (except inside a noncompositional group); a span is contiguous (unless
    `allow_scattered`) and never function-words-only. Anything that cannot be salvaged becomes
    `unrepresented`/`invalid` — declined tokens are never written as pairs."""
    decide = set(packet.decide)
    plain, soft = set(packet.allowed), set(packet.soft)
    claims: dict[int, tuple[int, str]] = {}
    seen: dict[int, Decision] = {}
    packet_repairs: list[str] = []

    for item in raw_items:
        h = item.get("h_idx")
        if not isinstance(h, int) or h not in decide:
            packet_repairs.append(f"dropped unknown h_idx {h!r}")
            continue
        if h in seen:
            packet_repairs.append(f"dropped duplicate answer for h{h}")
            continue
        status = item.get("status")
        d = Decision(h, [], status if isinstance(status, str) else "invalid",
                     str(item.get("note") or "").strip()[:200], item.get("tag"))
        seen[h] = d
        if status in _DECLINED:
            d.t_idx = []
            continue
        if status not in ALIGNED:
            d.status = "invalid"
            d.repairs.append(f"unknown status {status!r}")
            continue
        ts = sorted({t for t in (item.get("t_idx") or []) if isinstance(t, int)})
        valid = [t for t in ts if t in plain or t in soft]
        if len(valid) != len(ts):
            d.repairs.append("removed unavailable positions " + str(sorted(set(ts) - set(valid))))
        kept = []
        for t in valid:
            other = claims.get(t)
            if other and not (other[1] == "noncompositional" and status == "noncompositional"):
                d.repairs.append(f"t{t} already claimed by h{other[0]}")
            else:
                kept.append(t)
        if kept and not allow_scattered:
            run = _longest_contiguous(kept, set())
            if run != kept:
                d.repairs.append("trimmed to the longest contiguous run")
                kept = run
        if kept and not any(t in plain for t in kept):
            d.repairs.append("function-word-only span")
            kept = []
        if not kept:
            if status in ALIGNED:
                d.repairs.append("no usable positions")
            d.status = "unrepresented"
            d.tag = None
        d.t_idx = kept
        for t in kept:
            claims[t] = (h, status)

    for h in packet.decide:
        if h not in seen:
            seen[h] = Decision(h, [], "invalid", "", None, ["missing from the response"])
    decisions = [seen[h] for h in sorted(seen)]
    return decisions, packet_repairs


def derive_score(d: Decision, agrees: bool, base: float = 0.75, agree_score: float = 0.9) -> float:
    """0.75 = residual's fill score (deliberately below export_lex's 0.9 hi-conf bar); 0.9 when an
    INDEPENDENT mechanism (gapfill/residual) picked the same span for the same token — the same
    reasoning `residual_align.combine_with_gapfill` uses — or when verify confirmed the aligner's own
    proposal."""
    if d.tag == "confirmed" or agrees:
        return agree_score
    return base


def prior_for(strategy: str, d: Decision) -> str:
    if strategy in ("verify", "lexeme-verify"):
        return f"llm_{strategy.replace('-', '_')}_{d.tag or 'corrected'}"
    return f"llm_{strategy}"


def to_json(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True)
