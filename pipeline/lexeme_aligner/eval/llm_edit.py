"""Path #2 of the full-alignment plan (internal-docs/full-alignments-two-paths-plan-2026-10-09.md, B): the LLM reviews a COMPLETE
proposed alignment, one chapter per call, and answers with EDITS ONLY.

Why: every earlier LLM strategy paid ~225 output tokens per decided source word (aim1-three-track-evaluation-plan.md §8.8), in
every cell — the model re-stated each decision with a status and a note. Here our own tools write the whole proposal (every
source word placed or marked unrepresented, every target word owned or marked added), flag what they are unsure of (`?`), and the
model returns only what it changes, in a terse code. An unchanged proposal costs no output tokens.

    .venv/bin/python -m lexeme_aligner.eval.llm_edit --iso hinirv --publish-iso hin --book RUT --dry-run          # sizes + cost estimate
    .venv/bin/python -m lexeme_aligner.eval.llm_edit --iso hinirv --publish-iso hin --book RUT --show 1           # print chunk 1's prompt
    .venv/bin/python -m lexeme_aligner.eval.llm_edit --iso hinirv --publish-iso hin --book RUT --provider mock --mock empty
    .venv/bin/python -m lexeme_aligner.eval.llm_edit --iso hinirv --publish-iso hin --book RUT --provider mock --mock oracle
    .venv/bin/python -m lexeme_aligner.eval.llm_edit --iso hinirv --publish-iso hin --book RUT --provider cli --model claude-sonnet-5-5
    .venv/bin/python -m lexeme_aligner.eval.pos_score --iso hinirv --publish-iso hin --usj-dir pipeline/work/ingest-cache/usj-hinirv \\
        --book RUT --method llm:<out-tag> --grain both --all-tokens

The PROPOSAL per verse (`build_proposals`): every source token's winner from compact_align._merged_pairs (the published compact
winner: contest rule, spanext first; function words included), the fn-veto of config/fn_veto.json applied, overlaps between
source tokens resolved (content before function word, then method priority; both flagged `overlap`), then the DOUBT MASK — each
signal a knob (`--doubt`):

    uncovered   a content source word with no target           low       eflomal won with score < 0.9
    spanext     span extension widened it                      gapfill   gap-fill / residual filled it
    contested   eflomal and gloss disagreed (compact `contested`) checks   a word-level check flags it (eval/word_checks)
    overlap     two source words claimed the same target word  unowned   a non-stopword target word no source word owns
    unowned-fn  a STOPWORD no source word owns (under this dataset's convention it usually attaches to a source word; iteration 2)

The CHUNK = one chapter, split into as few even parts as `--cap` (verses) allows; a pooled verse range is one verse record, so it
is never split. The ANSWER: `{"edits": [{"ref": <ref>, "e": "h7>t10 h3= t12+"}]}`, one entry per changed verse:
`h7>t10` / `h7>t10-12` / `h7>t10,14` (scattered) gives h7 those target words (taken from their old owner), `h3=` says h3 has no
target word of its own, `t12+` says t12 renders no source word. `apply_edits` validates every code (unknown ids, positions outside
the verse are dropped and counted) and the result is a complete two-sided partition again.

OUTPUT: `align_llm_<out-tag>_<BOOK>.jsonl` in gapfill's record shape (pairs; `llm_skipped` with status `unrepresented`;
`llm_added`), so pos_score / score_gapfill read it with no new code, plus `llm_usage_<out-tag>.json`. Never published.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EDIT_VERSION = "llm-edit-v1"          # iteration 1: changes only (kept for reproducibility: --mode edits)
CHECK_VERSION = "llm-edit-v2"         # iteration 2 (2026-10-09): a CHECK line per verse, every flagged id answered; this dataset's
                                      # convention written into the rules (function words attach to the source word whose function
                                      # they carry, owner's choice); no "mostly right" framing
SIGNALS = ("uncovered", "low", "spanext", "gapfill", "contested", "checks", "overlap", "unowned", "unowned-fn")
METHODS = ("spanext", "eflomal", "gloss", "gapfill", "residual")
PRIORITY = ["spanext", "eflomal", "gloss", "gapfill", "residual"]
SCHEMA_EDITS = {"type": "object", "additionalProperties": False, "required": ["edits"],
                "properties": {"edits": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["ref", "e"],
                    "properties": {"ref": {"type": "integer"}, "e": {"type": "string"}}}}}}
_CONVENTIONS_DIR = Path("config/llm_conventions")


# --- the proposal --------------------------------------------------------------------------------------------------------
@dataclass
class Proposal:
    ref: int
    book: str
    ch: int
    v: int
    heb: list                                     # HebToken, spine order
    toks: list[str]                               # target words
    owner: dict[int, list[int]] = field(default_factory=dict)        # h_idx -> target positions ([] = unrepresented)
    method: dict[int, str] = field(default_factory=dict)             # h_idx -> winning method (proposal provenance)
    doubt_h: dict[int, set] = field(default_factory=lambda: collections.defaultdict(set))
    doubt_t: dict[int, set] = field(default_factory=lambda: collections.defaultdict(set))
    stop_t: set = field(default_factory=set)      # target positions that are stopwords

    def added(self) -> list[int]:
        owned = {p for ps in self.owner.values() for p in ps}
        return [j for j in range(len(self.toks)) if j not in owned]


def _char_method(char: str) -> str:
    return {"x": "spanext", "e": "eflomal", "E": "eflomal", "g": "gloss", "G": "gloss", "f": "gapfill", "r": "residual"}.get(char, char)


def resolve_overlaps(p: Proposal, content: set[int]) -> int:
    """Make `owner` a partition of the target positions: a position claimed twice stays with the content token (then the higher
    method priority, then the lower h_idx); the loser keeps the rest of its span. Both are flagged `overlap`. Returns the count."""
    rank = lambda h: (0 if h in content else 1, PRIORITY.index(p.method[h]) if p.method.get(h) in PRIORITY else 9, h)   # noqa: E731
    claim: dict[int, int] = {}
    n = 0
    for h in sorted(p.owner, key=rank):
        keep = []
        for j in p.owner[h]:
            if j in claim:
                p.doubt_h[h].add("overlap")
                p.doubt_h[claim[j]].add("overlap")
                n += 1
            else:
                claim[j] = h
                keep.append(j)
        p.owner[h] = keep
    return n


def build_proposals(tag: str, publish_iso: str, usj_dir: Path, books: list[str], *, out_dir: Path | None = None,
                    signals=SIGNALS, heb=None) -> tuple[list[Proposal], collections.Counter]:
    from lexeme_aligner.compact_align import _merged_pairs, load_contest_rule
    from lexeme_aligner.config import OUT
    from lexeme_aligner.eval.word_checks import CHECKS, flagged_idx, language_facts, veto_checks_for
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.target_stopwords import StopwordFilter
    from lexeme_aligner.versification import remapper
    out_dir = Path(out_dir or OUT)
    heb = heb or HebrewSource()
    recs = build_corpus(books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    stop = StopwordFilter(publish_iso, str(usj_dir))
    facts = language_facts(publish_iso)
    veto = veto_checks_for(publish_iso)
    contest = load_contest_rule()
    merged = {b: _merged_pairs(tag, b, out_dir, METHODS, contest) for b in books}
    signals = set(signals)
    st: collections.Counter = collections.Counter()
    out = []
    for r in recs:
        p = Proposal(encode(r.book, r.ch, r.v), r.book, r.ch, r.v, sorted(r.heb, key=lambda t: t.idx), list(r.toks))
        p.stop_t = {j for j, w in enumerate(p.toks) if stop.is_function(w)}
        win = merged.get(r.book, {}).get((r.ch, r.v), {})
        content = {t.idx for t in p.heb if t.is_content and t.strong}
        ntok = len(p.toks)
        for t in p.heb:
            w = win.get(t.idx)
            if w:
                p.owner[t.idx] = sorted(j for j in w["t_idx"] if j < ntok)
                p.method[t.idx] = _char_method(w["char"])
                if "low" in signals and w["char"] == "e":
                    p.doubt_h[t.idx].add("low")
                if "spanext" in signals and w["char"] == "x":
                    p.doubt_h[t.idx].add("spanext")
                if "gapfill" in signals and w["char"] in ("f", "r"):
                    p.doubt_h[t.idx].add("gapfill")
                if "contested" in signals and w.get("alt"):
                    p.doubt_h[t.idx].add("contested")
            else:
                p.owner[t.idx] = []
                if "uncovered" in signals and t.idx in content:
                    p.doubt_h[t.idx].add("uncovered")
        aligned = {h: s for h, s in p.owner.items() if s}
        if aligned:
            fn = lambda j, toks=p.toks: j < len(toks) and stop.is_function(toks[j])    # noqa: E731
            if veto:                                       # the measured fn veto (config/fn_veto.json): those links are dropped
                for h, chk in flagged_idx(p.heb, aligned, facts, fn, veto).items():
                    if h not in content and p.owner.get(h):
                        p.owner[h] = []
                        p.doubt_h[h].add("veto")
                        st["fn links vetoed"] += 1
            if "checks" in signals:
                for h, chk in flagged_idx(p.heb, {h: s for h, s in p.owner.items() if s}, facts, fn, CHECKS).items():
                    p.doubt_h[h].add(f"check:{chk}")
        st["overlaps"] += resolve_overlaps(p, content)
        for j in p.added():
            if "unowned" in signals and j not in p.stop_t:
                p.doubt_t[j].add("unowned")
            elif "unowned-fn" in signals and j in p.stop_t:
                p.doubt_t[j].add("unowned-fn")
        p.doubt_h = {h: s for h, s in p.doubt_h.items() if s}
        st["verses"] += 1
        st["source tokens"] += len(p.heb)
        st["target words"] += ntok
        st["doubtful source tokens"] += len(p.doubt_h)
        st["doubtful target words"] += len(p.doubt_t)
        out.append(p)
    return out, st


# --- chunks ---------------------------------------------------------------------------------------------------------------
def chunks(proposals: list[Proposal], cap: int = 40) -> list[list[Proposal]]:
    """One chapter per chunk; a chapter over `cap` verses -> as few, as even parts as the cap allows. A pooled verse range is one
    record, so it can never be split."""
    from lexeme_aligner.eval.llm_align import _even_packs
    by_ch: dict[tuple[str, int], list[Proposal]] = collections.defaultdict(list)
    for p in proposals:
        by_ch[(p.book, p.ch)].append(p)
    out = []
    for key in sorted(by_ch, key=lambda k: (by_ch[k][0].ref // 1_000_000, k[1])):
        out += _even_packs(by_ch[key], cap)
    refs = [p.ref for c in out for p in c]
    assert len(refs) == len(set(refs)), "a verse appears in two chunks"
    return out


# --- rendering ------------------------------------------------------------------------------------------------------------
_CONTRACT_V1 = """# Word alignment review — edit mode ({version})

You review a word-by-word alignment between a Hebrew (Old Testament) or Greek (New Testament) source text and its translation in
{lang_name} ({publish_iso}). Several consecutive verses of one chapter are given; use the whole passage as context.

For every verse you get:
- SOURCE: the source words `h<n>` with surface form, lemma, Strong's number, part of speech, grammar tags, an English gloss, and
  the PROPOSAL: `> t3-4` = this source word is rendered by target words t3..t4; `=` = it has no target word of its own.
- TARGET: the translation's words `t<n>`. A target word marked `+` renders no source word in the proposal (added).

The proposal is a complete partition: every target word belongs to exactly one source word or is `+`. It was made by statistical
aligners and is mostly right. `?` marks what they were unsure of — check those first — but correct anything that is wrong.

ANSWER WITH CHANGES ONLY, as codes, one entry per verse that changes (leave unchanged verses out, add no explanations):
  h7>t10       source word h7 is rendered by t10 (this REPLACES h7's current target words)
  h7>t10-12    ... by the contiguous run t10, t11, t12
  h7>t10,14    ... by t10 and t14 (only when the language really splits the rendering, e.g. a separable particle)
  h3=          h3 has no target word of its own
  t12+         t12 renders no source word
A target word you give to a source word is taken from its old owner automatically; give each target word to at most one source
word. Codes in one entry are separated by single spaces.

How to decide:
1. A target word belongs to the source word whose meaning or grammatical function it renders.
2. Function words follow what licenses them: a separate target preposition, article, object marker or possessive word goes to the
   source morpheme that carries that function (Hebrew prefixes and suffixes are their own source words h<n>); a Greek article goes
   to the article itself when the target has one, else it is `=`.
3. Prefer the smallest correct unit: do not give a source word target words that render a different source word.
4. When the translation restructures (one target word for two source words, or a paraphrase), give the target words to the source
   word they mainly render and mark the others `=`.
5. Grammar tags (from the source's own morphology) and glosses are evidence; the proposal is evidence, not authority.
"""

_CONTRACT_V2 = """# Word alignment review — check mode ({version})

You review a word-by-word alignment between a Hebrew (Old Testament) or Greek (New Testament) source text and its translation in
{lang_name} ({publish_iso}). Several consecutive verses of one chapter are given; use the whole passage as context.

For every verse you get:
- SOURCE: the source words `h<n>` with surface form, lemma, Strong's number, part of speech, grammar tags, an English gloss, and
  the PROPOSAL: `> t3-4` = this source word is rendered by target words t3..t4; `=` = it has no target word of its own.
- TARGET: the translation's words `t<n>`. A target word marked `+` renders no source word in the proposal (added).
- CHECK: the ids a statistical aligner was unsure of. Many of them are wrong — often a third to a half. Judge each one on the text.

The proposal is a complete partition: every target word belongs to exactly one source word or is `+`.

ANSWER: one entry for EVERY verse that has a CHECK line, holding one code for EVERY id on that line (and, optionally, changes to
ids not on it). Verses without a CHECK line and without changes are left out. No explanations.
  h7.          h7 is right as proposed
  t12.         t12 is right as added (it renders no source word)
  h7>t10       h7 is rendered by t10 (this REPLACES h7's current target words)
  h7>t10-12    ... by the contiguous run t10..t12      h7>t10,14   ... by t10 and t14 (a split form, see rule 2)
  h7+t12       ADD t12 to h7's words (use this to attach a function word)
  h3=          h3 has no target word of its own
  t12+         t12 renders no source word
A target word given to a source word is taken from its old owner automatically. Codes are separated by single spaces.

This dataset's convention — EVERY target word is accounted for:
1. A target word belongs to the source word whose meaning OR grammatical function it renders. `+` is only for words that render
   nothing in the source (a translator's explanation, a word supplied for style).
2. Function words attach to the source word whose function they carry:
   - a preposition or case particle that renders a CASE or a construct relation goes with that noun (Greek genitive θεοῦ -> "de Dieu",
     Hebrew construct "the house OF the king": "of" goes with "king") — unless the source has its own word for it (a Greek
     preposition, a Hebrew prefix בְּ לְ כְּ מִ), which then gets it;
   - an article goes to the source article when there is one, else to its noun;
   - a pronoun suffix or possessive goes to its own source morpheme when the source has one, else to its noun or verb;
   - an auxiliary, a subject pronoun or a particle that renders the verb's person / tense / mood goes with that verb;
   - BOTH parts of a split form go to the one source word they render (French "ne ... pas" for οὐ: h4>t3,6).
3. Otherwise prefer the smallest correct unit: do not give a source word target words that render a different source word.
4. When the translation restructures (one target word for two source words, a paraphrase), give the target words to the source
   word they mainly render and mark the others `=`.
5. Grammar tags (the source's own morphology) and glosses are evidence; the proposal is evidence, not authority.
"""

_EXAMPLE_V2 = """Input (English target, format only; the proposal below contains mistakes on purpose):
REF 8001016  RUT 1:16
SOURCE:
  h0 וַ <וְ> H9003 conjunction "and" > t0
  h1 תֹּאמֶר <אמר> H0559 verb [3sg] "said" > t1-2 ?
  h2 ר֣וּת H7327 name "Ruth" = ?
  h3 אַל H0408 adverb "not" = ?
  h4 תִּפְגְּעִי <פגע> H6293 verb [2sg] "urge" > t3-5 ?
  h5 בִּי H9002 preposition "in.me" = ?
TARGET:
  t0:But t1:Ruth t2:replied t3:Do t4:not t5:urge t6:me+?
CHECK: h1 h2 h3 h4 h5 t6
Answer: {"edits": [{"ref": 8001016, "e": "h1>t2 h2>t1 h3>t4 h4>t3,5 h5>t6"}]}
(h1 loses t1 to h2; the auxiliary "Do" renders the verb's mood, so it goes with h4: h4>t3,5; t6 is answered by h5>t6.)
"""

_EXAMPLE = """Input (English target, format only; the proposal below contains mistakes on purpose):
REF 8001016  RUT 1:16
SOURCE:
  h0 וַ <וְ> H9003 conjunction "and" > t0
  h1 תֹּאמֶר <אמר> H0559 verb [3sg] "said" > t1-2 ?
  h2 ר֣וּת H7327 name "Ruth" = ?
  h3 אַל H0408 adverb "not" = ?
  h4 תִּפְגְּעִי <פגע> H6293 verb [2sg] "urge" > t3-5 ?
  h5 בִּי H9002 preposition "in.me" = ?
TARGET:
  t0:But t1:Ruth t2:replied t3:Do t4:not t5:urge t6:me+?
Answer: {"edits": [{"ref": 8001016, "e": "h1>t2 h2>t1 h3>t4 h4>t5 h5>t6 t3+"}]}
(h1 loses t1 to h2; "Do" renders no source word.)
"""


def render_prefix(publish_iso: str, lang_name: str, conventions_md: str | None = None, mode: str = "check") -> str:
    from lexeme_aligner.eval.llm_prompt import _GENERIC_CONVENTIONS
    contract, version, example = ((_CONTRACT_V2, CHECK_VERSION, _EXAMPLE_V2) if mode == "check" else (_CONTRACT_V1, EDIT_VERSION, _EXAMPLE))
    return "\n".join([contract.format(version=version, lang_name=lang_name, publish_iso=publish_iso),
                      f"## Language conventions ({lang_name})", (conventions_md or _GENERIC_CONVENTIONS).strip() + "\n",
                      "## Worked example", example])


def _fmt_span(ts: list[int]) -> str:
    """[3,4,5] -> 't3-5'; [3,7] -> 't3,7'; mixed runs -> 't3-5,9'."""
    ts = sorted(ts)
    parts, i = [], 0
    while i < len(ts):
        j = i
        while j + 1 < len(ts) and ts[j + 1] == ts[j] + 1:
            j += 1
        parts.append(f"{ts[i]}" if i == j else f"{ts[i]}-{ts[j]}")
        i = j + 1
    return "t" + ",".join(parts)


def check_ids(p: Proposal) -> list[str]:
    return [f"h{h}" for h in sorted(p.doubt_h)] + [f"t{j}" for j in sorted(p.doubt_t)]


def render_verse(p: Proposal, lex_pos: dict, mode: str = "check") -> str:
    from lexeme_aligner.eval.llm_prompt import _source_row
    lines = [f"REF {p.ref}  {p.book} {p.ch}:{p.v}", "SOURCE:"]
    for t in p.heb:
        own = p.owner.get(t.idx, [])
        state = f" > {_fmt_span(own)}" if own else " ="
        if t.idx in p.doubt_h:
            state += " ?"
        lines.append(_source_row(t, state, lex_pos.get(t.lexeme), p.heb))
    added = set(p.added())
    tw = []
    for j, w in enumerate(p.toks):
        mark = ("+?" if j in p.doubt_t else "+") if j in added else ""
        tw.append(f"t{j}:{w}{mark}")
    lines += ["TARGET:", "  " + " ".join(tw)]
    if mode == "check" and check_ids(p):
        lines.append("CHECK: " + " ".join(check_ids(p)))
    return "\n".join(lines)


def render_chunk(chunk: list[Proposal], label: str, lex_pos: dict, mode: str = "check") -> str:
    head = f"PASSAGE {chunk[0].book} {chunk[0].ch}:{chunk[0].v}-{chunk[-1].ch}:{chunk[-1].v}  ({label}, {len(chunk)} verses)"
    return "\n---\n".join([head] + [render_verse(p, lex_pos, mode) for p in chunk])


# --- edits ----------------------------------------------------------------------------------------------------------------
_CODE = re.compile(r"^(?:h(\d+)(?:>t?([\d,\-t]+)|\+t?([\d,\-t]+)|(=)|(\.))|t(\d+)([+.]))$")


@dataclass
class Edit:
    kind: str                 # "assign" | "add" | "none" | "added" | "keep" (h. / t.)
    h: int | None = None
    t: list[int] = field(default_factory=list)


def _positions(spec: str) -> list[int] | None:
    ts: list[int] = []
    for part in spec.replace("t", "").split(","):
        a, _, b = part.partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            return None
        ts += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return sorted(set(ts)) or None


def parse_edits(e: str) -> tuple[list[Edit], list[str]]:
    """'h7>t10-12 h3= t12+ h4+t9 h5. t2.' -> ([Edit, ...], [unparsed codes])."""
    edits, bad = [], []
    for code in e.split():
        m = _CODE.match(code)
        if not m:
            bad.append(code)
            continue
        h = int(m.group(1)) if m.group(1) is not None else None
        if m.group(6) is not None:
            edits.append(Edit("added" if m.group(7) == "+" else "keep", t=[int(m.group(6))]))
        elif m.group(5):
            edits.append(Edit("keep", h=h))
        elif m.group(4):
            edits.append(Edit("none", h=h))
        else:
            ts = _positions(m.group(2) or m.group(3))
            if ts is None:
                bad.append(code)
            else:
                edits.append(Edit("assign" if m.group(2) else "add", h=h, t=ts))
    return edits, bad


def apply_edits(p: Proposal, edits: list[Edit]) -> tuple[dict[int, list[int]], set[int], collections.Counter]:
    """(final owner map, h_idx changed, counts). Invalid codes (unknown h, a position outside the verse) are dropped and counted;
    the result is always a partition."""
    owner = {h: list(s) for h, s in p.owner.items()}
    hs = {t.idx for t in p.heb}
    n = len(p.toks)
    changed: set[int] = set()
    c: collections.Counter = collections.Counter()
    for e in edits:
        if e.h is not None and e.h not in hs:
            c["dropped: unknown source id"] += 1
            continue
        if any(j < 0 or j >= n for j in e.t):
            c["dropped: target position outside the verse"] += 1
            continue
        if e.kind == "keep":
            c["kept (h. / t.)"] += 1
            continue
        if e.kind == "add":                                     # h+t: extend h's span with these positions
            e = Edit("assign", h=e.h, t=sorted(set(owner.get(e.h, [])) | set(e.t)))
            c["h+t"] += 1
        if e.kind == "none":
            if owner.get(e.h):
                owner[e.h] = []
                changed.add(e.h)
            c["h="] += 1
        elif e.kind == "added":
            j = e.t[0]
            for h, s in owner.items():
                if j in s:
                    owner[h] = [x for x in s if x != j]
                    changed.add(h)
            c["t+"] += 1
        else:
            for h, s in owner.items():                          # take the positions from their old owners
                if h != e.h and set(s) & set(e.t):
                    owner[h] = [x for x in s if x not in e.t]
                    changed.add(h)
            if owner.get(e.h) != e.t:
                owner[e.h] = list(e.t)
                changed.add(e.h)
            c["h>t"] += 1
            if len(e.t) > 1 and e.t[-1] - e.t[0] + 1 != len(e.t):
                c["scattered spans"] += 1
    return owner, changed, c


# --- output ---------------------------------------------------------------------------------------------------------------
def to_records(proposals: list[Proposal], finals: dict[int, tuple[dict, set]], run_meta: dict) -> dict[str, list[dict]]:
    """{BOOK: [verse record]} in gapfill's shape (pairs / llm_skipped / llm_added), read by pos_score `--method llm:<tag>`."""
    by_book: dict[str, list[dict]] = collections.defaultdict(list)
    for p in proposals:
        owner, changed = finals.get(p.ref, (p.owner, set()))
        pairs, skipped = [], []
        for t in p.heb:
            s = owner.get(t.idx, [])
            how = "edited" if t.idx in changed else "kept"
            if s:
                pairs.append({"h_idx": t.idx, "lexeme": t.lexeme, "strong": t.strong, "lemma": t.lemma, "surface": t.surface,
                              "target": " ".join(p.toks[j] for j in s), "t_idx": s, "score": 0.9 if how == "kept" else 0.75,
                              "method": "llm", "content": bool(t.is_content), "prior": f"llm_edit_{how}",
                              "proposal_method": p.method.get(t.idx)})
            else:
                skipped.append({"h_idx": t.idx, "strong": t.strong, "lexeme": t.lexeme, "status": "unrepresented",
                                "prior": f"llm_edit_{how}"})
        owned = {j for s in owner.values() for j in s}
        by_book[p.book].append({"ref": p.ref, "book": p.book, "chapter": p.ch, "verse": p.v, "pairs": pairs,
                                "llm_skipped": skipped, "llm_added": [j for j in range(len(p.toks)) if j not in owned],
                                "llm": run_meta})
    return by_book


def write_records(by_book: dict[str, list[dict]], out_tag: str, out_dir: Path) -> list[Path]:
    paths = []
    for book, recs in by_book.items():
        fp = out_dir / f"align_llm_{out_tag}_{book}.jsonl"
        fp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
        paths.append(fp)
    return paths


# --- mocks ----------------------------------------------------------------------------------------------------------------
def oracle_edits(chunk: list[Proposal], gold: dict, spine) -> dict:
    """What a perfect reviewer limited to the DOUBT MASK would answer: every doubtful source token the gold judges gets the gold's
    span (or `=` when the gold gives it no target). The ceiling the current mask can reach; free."""
    out = []
    for p in chunk:
        gv = gold.get(p.ref)
        if gv is None:
            continue
        key_of = spine.key_of.get(p.ref, {})
        codes = []
        for h in sorted(p.doubt_h):
            key = key_of.get(h)
            if key is None or key[0] in gv.ambiguous:
                continue
            g = sorted(gv.links.get(key, set()))
            if key not in gv.links:
                continue                                   # the gold does not judge this token
            if g != p.owner.get(h, []):
                codes.append(f"h{h}>{_fmt_span(g)}" if g else f"h{h}=")
        if codes:
            out.append({"ref": p.ref, "e": " ".join(codes)})
    return {"edits": out}


# --- cost -----------------------------------------------------------------------------------------------------------------
def estimate(chunks_: list[list[Proposal]], prefix: str, rendered: list[str], price, p_edit: float = 0.5) -> dict:
    """Pessimistic (chars/2.5) and optimistic (chars/4) input tokens, the prefix cached after the first call; output = edits of
    `p_edit` of the doubtful items at ~8 tokens each + ~6 per changed verse. Thinking tokens are NOT included (measured on the
    first real calls)."""
    n_doubt = sum(len(p.doubt_h) + len(p.doubt_t) for c in chunks_ for p in c)
    n_verses = sum(len(c) for c in chunks_)
    out_tok = n_doubt * p_edit * 8 + n_verses * 0.6 * 6
    res = {"calls": len(chunks_), "verses": n_verses, "doubtful_items": n_doubt, "prefix_chars": len(prefix),
           "suffix_chars": sum(len(s) for s in rendered), "output_tokens_est": int(out_tok)}
    for name, div in (("pessimistic", 2.5), ("optimistic", 4.0)):
        pre, suf = len(prefix) / div, sum(len(s) for s in rendered) / div
        if price:
            cost = price.cost(int(suf), int(out_tok), int(pre * (len(chunks_) - 1)), int(pre))
        else:
            cost = None
        res[name] = {"input_tokens": int(pre * len(chunks_) + suf), "usd": round(cost, 4) if cost is not None else None}
    return res


# --- driver ---------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="the chain's edition TAG"); ap.add_argument("--publish-iso", required=True)
    ap.add_argument("--usj-dir", type=Path, default=None, help="default pipeline/work/ingest-cache/usj-<tag>")
    ap.add_argument("--book", action="append", required=True)
    ap.add_argument("--chapter", type=int, action="append", help="only these chapters (a pilot slice)")
    ap.add_argument("--cap", type=int, default=40, help="max verses per call; a longer chapter is split evenly")
    ap.add_argument("--doubt", default=",".join(SIGNALS), help=f"doubt signals (any of {','.join(SIGNALS)})")
    ap.add_argument("--mode", default="check", choices=["check", "edits"],
                    help="check (iteration 2, default): every flagged id answered; edits (iteration 1): changes only")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--provider", default="mock", choices=["mock", "cli", "anthropic"])
    ap.add_argument("--mock", default="empty", choices=["empty", "oracle"], help="mock answers: none, or the gold on doubtful ids")
    ap.add_argument("--gold-method", default="manual")
    ap.add_argument("--model", default="claude-sonnet-5-5"); ap.add_argument("--effort", default="low")
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--prices", type=Path, default=None)
    ap.add_argument("--out-tag", default=None, help="default <tag>.edit.<provider>[.<mock>|.<model>]")
    ap.add_argument("--dry-run", action="store_true", help="sizes and cost estimate only")
    ap.add_argument("--show", type=int, default=None, metavar="N", help="print chunk N's prompt (1-based) and exit")
    a = ap.parse_args(argv)
    from lexeme_aligner.config import LLM_CACHE, OUT, PRIOR_PACK
    from lexeme_aligner.eval.llm_providers import PRICES, Job, ResponseCache, Usage, cache_key, load_prices, make_provider
    from lexeme_aligner.gapfill import load_priors
    usj = a.usj_dir or Path(f"pipeline/work/ingest-cache/usj-{a.iso}")
    props, st = build_proposals(a.iso, a.publish_iso, usj, a.book, signals=tuple(s for s in a.doubt.split(",") if s))
    if a.chapter:
        props = [p for p in props if p.ch in set(a.chapter)]
    cs = chunks(props, a.cap)
    lex_pos, _ = load_priors(PRIOR_PACK)
    lang = a.lang_name or a.publish_iso
    conv_fp = _CONVENTIONS_DIR / f"{a.publish_iso}.md"
    prefix = render_prefix(a.publish_iso, lang, conv_fp.read_text(encoding="utf-8") if conv_fp.exists() else None, a.mode)
    label = f"{lang}, edition {a.iso}"
    rendered = [render_chunk(c, label, lex_pos, a.mode) for c in cs]
    if a.show:
        print(prefix + "\n=====\n" + rendered[a.show - 1])
        return 0
    prices = load_prices(a.prices) if a.prices else dict(PRICES)
    prices.setdefault("claude-sonnet-5-5", prices["claude-sonnet-5"])     # ASSUMPTION: same list price as sonnet-5 until checked
    est = estimate(cs, prefix, rendered, prices.get(a.model))
    print(json.dumps({"proposal": dict(st), "chunks": [len(c) for c in cs], "estimate": est}, ensure_ascii=False), file=sys.stderr)
    if a.dry_run:
        return 0
    out_tag = a.out_tag or f"{a.iso}.edit.{a.provider}.{a.mock if a.provider == 'mock' else a.model}"
    answers: list[dict | None] = []
    usage = Usage(billing="")
    if a.provider == "mock":
        gold = spine = None
        if a.mock == "oracle":
            from lexeme_aligner.eval.contest_rule import gold_base_text
            from lexeme_aligner.eval.pos_score import load_gold, load_spine
            from lexeme_aligner.versification import remapper
            gold, _ = load_gold(a.publish_iso, usj, a.book, gold_base_text(a.publish_iso, None if a.gold_method == "manual" else a.gold_method),
                                gold_methods=(a.gold_method,), remap=remapper(a.iso, str(usj)))
            spine = load_spine(a.book, usj, a.iso)
        answers = [oracle_edits(c, gold, spine) if a.mock == "oracle" else {"edits": []} for c in cs]
    else:
        provider = make_provider(a.provider, a.model, a.effort, prices=prices, concurrency=1, max_budget_usd=a.max_usd)
        cache = ResponseCache(LLM_CACHE)
        for i, (c, suffix) in enumerate(zip(cs, rendered)):
            if usage.cost_usd >= a.max_usd:
                print(f"[llm_edit] budget reached (${usage.cost_usd:.2f}) — {len(cs) - i} chunk(s) not sent", file=sys.stderr)
                answers += [None] * (len(cs) - i)
                break
            key = cache_key(provider.name, provider.model, provider.effort, prefix, suffix, SCHEMA_EDITS)
            hit = cache.get(key)
            if hit:
                answers.append(hit[0])
                continue
            n_doubt = sum(len(p.doubt_h) + len(p.doubt_t) for p in c)
            res = provider.complete_many([Job(key, prefix, suffix, SCHEMA_EDITS, min(64000, 16000 + 40 * n_doubt))])[key]
            resp, u, err = res
            usage = usage + u
            if err or resp is None:
                print(f"[llm_edit] chunk {i + 1}: {err}", file=sys.stderr)
                answers.append(None)
                continue
            cache.put(key, resp, u, {"model": provider.model, "provider": provider.name, "edit_version": EDIT_VERSION})
            answers.append(resp)
            print(f"[llm_edit] chunk {i + 1}/{len(cs)} {c[0].book} {c[0].ch}: {len(resp.get('edits', []))} verse(s) edited, "
                  f"out {u.output_tokens} tok, ${usage.cost_usd:.3f}", file=sys.stderr)
    finals: dict[int, tuple[dict, set]] = {}
    tally: collections.Counter = collections.Counter()
    by_ref = {p.ref: p for p in props}
    for c, ans in zip(cs, answers):
        if ans is None:
            tally["chunks without an answer (proposal kept)"] += 1
            continue
        if a.mode == "check":                                  # did the model answer every flagged id?
            said: dict[int, set] = collections.defaultdict(set)
            for item in ans.get("edits", []):
                for e in parse_edits(item.get("e", ""))[0]:
                    if e.h is not None:
                        said[item.get("ref")].add(f"h{e.h}")
                    said[item.get("ref")].update(f"t{j}" for j in e.t)
            for p in c:
                ids = set(check_ids(p))
                tally["check ids"] += len(ids)
                tally["check ids answered"] += len(ids & said.get(p.ref, set()))
        for item in ans.get("edits", []):
            p = by_ref.get(item.get("ref"))
            if p is None or p not in c:
                tally["dropped: ref not in this chunk"] += 1
                continue
            edits, bad = parse_edits(item.get("e", ""))
            tally["dropped: unparsed code"] += len(bad)
            owner, changed, cnt = apply_edits(p, edits)
            prev = finals.get(p.ref)
            finals[p.ref] = (owner, changed | (prev[1] if prev else set()))
            tally.update(cnt)
            tally["verses edited"] += 1
            tally["source tokens changed"] += len(changed)
            tally["changed tokens that were doubtful"] += len(changed & set(p.doubt_h))
    meta = {"edit_version": CHECK_VERSION if a.mode == "check" else EDIT_VERSION, "mode": a.mode, "provider": a.provider, "model": a.model if a.provider != "mock" else f"mock-{a.mock}",
            "effort": a.effort, "doubt": a.doubt, "cap": a.cap}
    paths = write_records(to_records(props, finals, meta), out_tag, Path(OUT))
    ledger = {"out_tag": out_tag, **meta, "chunks": len(cs), "verses": len(props), "proposal": dict(st), "edits": dict(tally),
              "usage": usage.to_dict(), "estimate": est}
    (Path(OUT) / f"llm_usage_{out_tag}.json").write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out_tag": out_tag, "files": len(paths), "edits": dict(tally), "cost_usd": round(usage.cost_usd, 4),
                      "output_tokens": usage.output_tokens}, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
