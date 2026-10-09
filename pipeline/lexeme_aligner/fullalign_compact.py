"""Full-align in the compact-alignments container format (built per edition by fullalign_build.py, chain step 9a).

One alignment LAYER (statistical / a manual source such as Clear or the BSB tables) is written as per-book files that look
exactly like compact-alignments files and add channels for what compact does not carry. Nothing here stores a word of any
text: every target reference is a token position, every source reference an ordinal into the shared `_index/` files.

  <BOOK>_<hash>.json         main  one string per spine verse (position-parallel to `_index/<BOOK>_lexemes.json`):
                                   "srcOrd:span ..."  - the winning link of each CONTENT source token (compact's meaning).
  <BOOK>_<hash>.meta.json    channels (a verse's entry is "" when empty; `null` = the channel has nothing in this book):
      fn    "fnOrd:span ..."   links of FUNCTION-WORD source tokens (articles, prepositions, ... = the tokens with no srcOrd;
                               `_index/<BOOK>_fn.json` lists them in order)
      wp    one profile char per main entry   `.` = a VIEW of a multi-source row (the row itself is in `rows`)
      fp    the same per fn entry
      wx    per entry, space separated: `_` or `;`-joined parts `t<int,int>` (the gold's target-token numbers), `S<id>` (a
            source id that is not 'o'/'n' + the token's spine key), `P<int,int>` (the profile's arguments, see below); main
            entries first, then fn entries
      rows  "srcs:span:prof[:ext]..." every row that is not a claimed winner: losing duplicates, multi-token source
            attachments, unplaced rows, markers, joint (vvv) rows
      off   {ref: [row entries]}  rows with NO spine source token (a table row whose source word is not in the spine)
  <LAYER>/_layer.json        attribution constants, the profile table (char -> method/score/prior/extra/attribution), scheme.

Slots:   c<N> = srcOrd N (content token) · f<N> = fnOrd N (function word) · p<N> = pooled-group token N of a verse that was
         folded into another verse's group (two Hebrew verses = one target verse).
Spans:   "5" "5-7" "5,9" contiguous / scattered, "L3,1,2" out-of-order list, "~" unplaced (verse not mappable), "!" explicitly
         empty, "@6e" / "@-1u" a MARKER: the source word has no target word of its own; it sits after target word 6 (-1 =
         verse start); kind `u` = untranslated (BSB `-`), `e` = rendered elsewhere (BSB `. . .`).
Row ext: k<i> index (into the srcs) of the keyed token · v joint with the following phrase (BSB `vvv`; the span is that phrase) ·
         s<pos,..> target words the editor SUPPLIED (BSB `[x]`; not in the span) · i<pos,..> inflection words made explicit
         (BSB `{x}`; stay in the span, flagged) · t<ints> target-token numbers · S<id> source id · T<strong> the row's own
         Strong's when it differs from the token's · P<ints> the profile's arguments.
Profile arguments: a number inside a row's `prior` is per-row data (span extension's `spanext_name_before:12` = the target
         position the rule appended), so the profile table stores the prior with `#` in its place (`spanext_name_before:#`) and the
         numbers travel with the row (`P12`), in order. Decoding puts them back: the prior is exactly the original.
Vetoed rows: a row whose profile `extra` carries `"veto": "<check>"` (a word-level check judged the link wrong, eval/word_checks.py)
         is never claimed into main/fn and never shown as a view there; it stays whole in `rows`.

Derived on decode, never stored: lexeme/strong/lemma/stem/surface/gloss_en/content (from the spine) and `target` (from the
positions and the edition text).  The BHSA-derived `sense` is NOT carried (licence).
"""
from __future__ import annotations

import collections
import json
import string
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_CHARS = string.ascii_letters + string.digits
PRIORITY = ["spanext", "eflomal", "exact", "stem", "prefix", "head", "multi", "fuzzy", "gapfill", "residual", "llm", "manual"]
MARK_ROWS = {"-": "u", ". . .": "e"}               # BSB-tables placeholder cells -> marker kind
JOINT_CELL = "vvv"
LAYER_VERSION = 2                                  # 2: profile arguments (P) — a prior's numbers are per-row data


# --- span codec ------------------------------------------------------------------------------------------
def enc_span(t) -> str:
    if t is None:
        return "~"
    if len(t) == 0:
        return "!"
    if list(t) != sorted(set(t)):
        return "L" + ",".join(map(str, t))
    if len(t) == 1:
        return str(t[0])
    lo, hi = min(t), max(t)
    return f"{lo}-{hi}" if hi - lo + 1 == len(t) else ",".join(map(str, t))


def dec_span(s: str):
    if s == "~":
        return None
    if s == "!":
        return []
    if s.startswith("L"):
        return [int(x) for x in s[1:].split(",")]
    out: list[int] = []
    for p in s.split(","):
        if "-" in p:
            a, b = p.split("-")
            out += range(int(a), int(b) + 1)
        else:
            out.append(int(p))
    return out


def enc_marker(k: int, kind: str) -> str:
    return f"@{k}{kind}"


def dec_marker(s: str) -> tuple[int, str]:
    return int(s[1:-1]), s[-1]


def is_marker(s: str) -> bool:
    return s.startswith("@")


# --- slots -------------------------------------------------------------------------------------------------
class Group:
    """One spine verse's tokens (pooled with the verses folded into it) and the slot each token has."""

    def __init__(self, ref: str, anchor_v: int, members: list):
        self.ref = ref
        self.tok_of: dict[int, object] = {}
        self.slot_of: dict[int, tuple[str, int]] = {}
        self.h_of: dict[tuple[str, int], int] = {}
        c = f = 0
        for orig_v, t in members:
            self.tok_of[t.idx] = t
            if orig_v == anchor_v:
                if t.strong and t.is_content:
                    slot = ("c", c)
                    c += 1
                else:
                    slot = ("f", f)
                    f += 1
            else:
                slot = ("p", t.idx)
            self.slot_of[t.idx] = slot
            self.h_of[slot] = t.idx
        self.n_content, self.n_fn = c, f


def slot_str(slot: tuple[str, int]) -> str:
    return f"{slot[0]}{slot[1]}"


def parse_slot(s: str) -> tuple[str, int]:
    return s[0], int(s[1:])


_NUM = __import__("re").compile(r":(-?\d+)")


def split_prior(prior):
    """'spanext_name_before:12+spanext_noun_before:3' -> ('spanext_name_before:#+spanext_noun_before:#', [12, 3])."""
    if not isinstance(prior, str):
        return prior, []
    args = [int(x) for x in _NUM.findall(prior)]
    return (_NUM.sub(":#", prior), args) if args else (prior, [])


def join_prior(template, args):
    if not args:
        return template
    it = iter(args)
    return __import__("re").sub(r":#", lambda _m: f":{next(it)}", template)


# --- canonical row -----------------------------------------------------------------------------------------
@dataclass
class CRow:
    """A row reduced to what the layer really says (no derived fields, no text)."""
    ref: str                                   # spine ref of the group ("BOOK C:V"); the stored ref for off-index rows
    src: list = field(default_factory=list)    # [(kind, n), ...]
    key: int | None = None                     # index into src of the keyed token (BSB tables)
    span: list | None = field(default_factory=list)   # None = unplaced, [] = empty, [..] = placed (supplied words removed)
    mark: tuple | None = None                  # (k, kind)
    joint: bool = False
    sup: list = field(default_factory=list)
    inf: list = field(default_factory=list)
    prof: tuple = ()                           # (method, score, prior, extra, attr_diff_json)
    tids: list | None = None
    sid: str | None = None
    tstrong: str | None = None
    pargs: list = field(default_factory=list)  # profile arguments (the numbers taken out of the prior)

    def canon(self) -> str:
        return json.dumps([self.ref, self.src, self.key, self.span, self.mark, self.joint, self.sup, self.inf,
                           self.prof, self.tids, self.sid, self.tstrong, self.pargs], ensure_ascii=False, default=list)

    def prior(self):
        """The row's original prior (template + arguments)."""
        return join_prior(self.prof[2] if self.prof else None, self.pargs)


class Profiles:
    """prof tuple <-> one character."""

    def __init__(self):
        self.char_of: dict[tuple, str] = {}
        self.of_char: dict[str, tuple] = {}

    def add(self, prof: tuple) -> str:
        if prof not in self.char_of:
            if len(self.char_of) >= len(PROFILE_CHARS):
                raise ValueError("more distinct row profiles than profile characters")
            c = PROFILE_CHARS[len(self.char_of)]
            self.char_of[prof] = c
            self.of_char[c] = prof
        return self.char_of[prof]

    def to_json(self) -> dict:
        return {c: {"method": p[0], "score": p[1], "prior": p[2], "extra": p[3], "attr": json.loads(p[4])}
                for c, p in self.of_char.items()}

    @classmethod
    def from_json(cls, d: dict) -> "Profiles":
        p = cls()
        for c, v in d.items():
            prof = (v["method"], v["score"], v["prior"], v["extra"], json.dumps(v["attr"], sort_keys=True))
            p.char_of[prof] = c
            p.of_char[c] = prof
        return p


def attr_diff(attribution: dict, base: dict) -> str:
    d = {k: v for k, v in attribution.items() if k not in ("source_ids", "target_ids") and v != base.get(k)}
    return json.dumps(d, sort_keys=True)


# --- bracket roles (BSB tables) ----------------------------------------------------------------------------
def cell_segments(text: str) -> list[tuple[str, str]]:
    """[(role, text)] of a BSB cell: role '' plain, 's' inside [..] (supplied), 'i' inside {..} (inflection made explicit)."""
    out: list[tuple[str, str]] = []
    role, buf = "", []
    for ch in text:
        if ch in "[{" and not role:
            if buf:
                out.append(("", "".join(buf)))
            buf, role = [], "s" if ch == "[" else "i"
        elif (ch == "]" and role == "s") or (ch == "}" and role == "i"):
            out.append((role, "".join(buf)))
            buf, role = [], ""
        else:
            buf.append(ch)
    if buf:
        out.append((role, "".join(buf)))
    return out


def roles_of_positions(text: str, positions: list[int], toks: list[str], letters) -> dict[int, str]:
    """{position: 's'|'i'} for the target words of `positions` that sit inside [..] / {..} in the cell `text`.
    `letters` = the letters-only normalizer the cell mapping used (bsb_tables._letters). Words are matched to the cell
    letter by letter in order; a word takes the role of its first letter."""
    seq: list[str] = []
    for role, seg in cell_segments(text):
        seq.extend(role for _ in letters(seg))
    out: dict[int, str] = {}
    i = 0
    for p in positions:
        n = len(letters(toks[p])) if p < len(toks) else 0
        if i < len(seq) and seq[i]:
            out[p] = seq[i]
        i += n
    return out


# --- per-book context --------------------------------------------------------------------------------------
def build_groups(heb, book: str, usj_path: Path, remap) -> dict[str, Group]:
    """{"BOOK C:V" anchor spine ref: Group} for a book, pooled exactly like the alignment run (and compact_align) pooled
    it. Positions are CLEAN-text positions; for an edition without strip rules those are the raw positions too —
    anything else is refused until the clean->raw conversion is wired in."""
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import pooled_verse_groups
    from lexeme_aligner.usj_source import read_verse_ranges, tokenize
    clean = read_verse_ranges(usj_path)
    raw = read_verse_ranges(usj_path, rules={})
    if {k: v["text"] for k, v in clean.items()} != {k: v["text"] for k, v in raw.items()}:
        raise NotImplementedError(f"{usj_path}: the edition has text-strip rules; clean->raw positions are not wired in yet")
    groups: dict[str, Group] = {}
    for ch in heb.chapters(book):
        for anchor_v, _vs, _ve, text, members in pooled_verse_groups(book, ch, heb, clean, remap):
            ref = f"{book} {ch}:{anchor_v}"
            g = Group(ref, anchor_v, members)
            g.toks = tokenize(text) if text else []
            tc, tv = remap(book, ch, anchor_v)[1:] if remap else (ch, anchor_v)
            g.tprefix = "%08d" % encode(book, tc, tv) if tv else None
            groups[ref] = g
    return groups


def default_sid(group: Group, slot: tuple[str, int]) -> str | None:
    tok = group.tok_of.get(group.h_of.get(slot, -1))
    if tok is None or not tok.keys:
        return None
    return ("o" if len(tok.keys[0]) == 12 else "n") + tok.keys[0]


# --- parquet row -> canonical row ----------------------------------------------------------------------------
def simple_crow(row: dict, group: Group, base: dict, has_ids: bool) -> CRow:
    """Statistical / Clear row: one source token (an int h_idx), the span is the row's t_idx. A WORD-LEVEL gold link (extra
    `word_h_idx`, see gold_to_fullalign.rows_from_gold) is one row from every morpheme of the word: src = all of them, `key` = the
    row's own (content) token; `word_h_idx` itself is not kept in the profile — the srcs carry it."""
    h = row["h_idx"]
    extra = row.get("extra")
    src = [group.slot_of[h]]
    key = None
    if extra and "word_h_idx" in extra:
        d = json.loads(extra)
        word = d.pop("word_h_idx")
        extra = json.dumps(d, ensure_ascii=False, sort_keys=True) if d else None
        src = [group.slot_of[i] for i in word]
        key = word.index(h)
    c = CRow(ref=group.ref, src=src, key=key, span=None if row["t_idx"] is None else list(row["t_idx"]))
    tmpl, c.pargs = split_prior(row.get("prior"))
    c.prof = (row["method"], row["score"], tmpl, extra, attr_diff(row["attribution"], base))
    if has_ids:
        a = row["attribution"]
        sids = a.get("source_ids") or []
        c.sid = sids[0] if sids else None
        tids = a.get("target_ids") or []
        if tids:
            for t in tids:
                if t[:-3] != group.tprefix:
                    raise ValueError(f"{group.ref}: target id {t} does not start with the group's target verse {group.tprefix}")
            c.tids = [int(t[-3:]) for t in tids]
    return c


def bsb_crows(vrows: list[dict], group: Group, base: dict, letters) -> list[CRow]:
    """One verse of BSB-tables rows (in TABLE order) -> canonical rows with markers, joint spans and supplied/inflection words."""
    kinds, finals, full = [], [], []
    for r in vrows:
        t, cell = r["t_idx"], (r.get("target") or "")
        if t is None:
            kinds.append("unplaced")
        elif len(t):
            kinds.append("placed")
        else:
            s = cell.strip()
            kinds.append(f"mark_{MARK_ROWS[s]}" if s in MARK_ROWS else "joint" if s == JOINT_CELL else "empty")
        full.append(list(t) if t else [])
        finals.append(None)
    roles: list[dict] = [{} for _ in vrows]
    for i, r in enumerate(vrows):
        if kinds[i] == "placed":
            roles[i] = roles_of_positions(r.get("target") or "", full[i], group.toks, letters)
            finals[i] = [p for p in full[i] if roles[i].get(p) != "s"]
    out: list[CRow] = []
    last_end = -1
    for i, r in enumerate(vrows):
        h = r["h_idx"]
        c = CRow(ref=group.ref)
        if h:                                    # a row with no spine source token still has target words: it takes part in the
            c.src = [group.slot_of[x] for x in h]   # marker anchors and joint spans below, and is filed under `off` by the encoder
            kh = r.get("h_idx_key")
            c.key = h.index(kh) if kh in h else None
        c.prof = (r["method"], r["score"], None, None, attr_diff(r["attribution"], base))
        tok = group.tok_of.get(h[c.key]) if h and c.key is not None else None
        if (tok.strong if tok else None) != r.get("strong") or tok is None and r.get("strong") is not None:
            c.tstrong = r.get("strong") if r.get("strong") is not None else "-"
        k = kinds[i]
        if k == "unplaced":
            c.span = None
        elif k == "placed":
            c.span = finals[i]
            c.sup = [p for p, ro in sorted(roles[i].items()) if ro == "s"]
            c.inf = [p for p, ro in sorted(roles[i].items()) if ro == "i"]
            last_end = max(full[i])
        elif k.startswith("mark_"):
            c.span = []
            c.mark = (last_end, k[-1])
        elif k == "joint":
            j = i + 1
            while j < len(vrows) and kinds[j] in ("joint", "empty"):
                j += 1
            c.joint = True
            c.span = list(finals[j]) if j < len(vrows) and kinds[j] == "placed" else []
        else:                                   # empty cell
            c.span = []
        out.append(c)
    return out


# --- row entries (the `rows` channel) -------------------------------------------------------------------------
def fmt_row(c: CRow, profiles: Profiles, has_ids: bool, group: Group | None) -> str:
    srcs = ",".join(slot_str(s) for s in c.src) or "-"
    span = enc_marker(*c.mark) if c.mark else enc_span(c.span)
    parts = [srcs, span, profiles.add(c.prof)]
    if c.key is not None:                       # present <=> the row has a keyed token (BSB tables)
        parts.append(f"k{c.key}")
    if c.joint:
        parts.append("v")
    if c.sup:
        parts.append("s" + ",".join(map(str, c.sup)))
    if c.inf:
        parts.append("i" + ",".join(map(str, c.inf)))
    if c.tids:
        parts.append("t" + ",".join(map(str, c.tids)))
    if has_ids and c.sid is not None and c.sid != (default_sid(group, c.src[0]) if group and c.src else None):
        parts.append("S" + c.sid)
    if c.tstrong is not None:
        parts.append("T" + c.tstrong)
    if c.pargs:
        parts.append("P" + ",".join(map(str, c.pargs)))
    return ":".join(parts)


def parse_row(entry: str, ref: str, profiles: Profiles, has_ids: bool, group: Group | None) -> CRow:
    f = entry.split(":")
    c = CRow(ref=ref)
    if f[0] != "-":
        c.src = [parse_slot(s) for s in f[0].split(",")]
    if is_marker(f[1]):
        c.mark = dec_marker(f[1])
        c.span = []
    else:
        c.span = dec_span(f[1])
    c.prof = profiles.of_char[f[2]]
    sid_override = None
    for x in f[3:]:
        t, rest = x[0], x[1:]
        if t == "k":
            c.key = int(rest)
        elif t == "v":
            c.joint = True
        elif t == "s":
            c.sup = [int(p) for p in rest.split(",")]
        elif t == "i":
            c.inf = [int(p) for p in rest.split(",")]
        elif t == "t":
            c.tids = [int(p) for p in rest.split(",")]
        elif t == "S":
            sid_override = rest
        elif t == "T":
            c.tstrong = rest
        elif t == "P":
            c.pargs = [int(p) for p in rest.split(",")]
    if has_ids and c.src:
        c.sid = sid_override if sid_override is not None else (default_sid(group, c.src[0]) if group else None)
    return c


# --- encoding one verse / one book ---------------------------------------------------------------------------
def _prio(c: CRow) -> int:
    m = c.prof[0]
    return PRIORITY.index(m) if m in PRIORITY else len(PRIORITY)


def is_vetoed(c: CRow) -> bool:
    e = c.prof[3] if len(c.prof) > 3 else None
    return isinstance(e, str) and '"veto"' in e


def with_veto(extra: str | None, check: str) -> str:
    """The row's `extra` JSON with `veto` added (an existing extra is kept)."""
    d = json.loads(extra) if extra else {}
    d["veto"] = check
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def plain_single(c: CRow) -> bool:
    """A row that can be a claimed winner: one c/f source token, no keyed/marker/joint/role data, a real span, not vetoed."""
    return (len(c.src) == 1 and c.src[0][0] in "cf" and c.key is None and c.mark is None and not c.joint
            and not c.sup and not c.inf and c.tstrong is None and bool(c.span) and not is_vetoed(c))


@dataclass
class VerseOut:
    main: list = field(default_factory=list)       # [(ord, span_str, prof_char, ext)]
    fn: list = field(default_factory=list)
    rows: list = field(default_factory=list)       # row entry strings


def encode_verse(crows: list[CRow], group: Group, profiles: Profiles, has_ids: bool,
                 hint: dict | None = None) -> VerseOut:
    """`hint` = {srcOrd: span list} of an existing compact main entry for this verse (statistical layer): main then keeps
    exactly those entries (compact's own winners) and claims the matching row of each."""
    out = VerseOut()
    claimed: set[int] = set()
    cand: dict[tuple, list[int]] = collections.defaultdict(list)
    for i, c in enumerate(crows):
        if plain_single(c):
            cand[c.src[0]].append(i)

    def pick(slot, want=None):
        idxs = [i for i in cand.get(slot, []) if i not in claimed and (want is None or crows[i].span == want)]
        return min(idxs, key=lambda i: (_prio(crows[i]), i)) if idxs else None

    def ext_of(c: CRow) -> str:
        bits = []
        if has_ids and c.tids:
            bits.append("t" + ",".join(map(str, c.tids)))
        if has_ids and c.sid is not None and c.sid != default_sid(group, c.src[0]):
            bits.append("S" + c.sid)
        if c.pargs:
            bits.append("P" + ",".join(map(str, c.pargs)))
        return ";".join(bits) or "_"

    if hint is not None:
        for o in sorted(hint):
            i = pick(("c", o), hint[o])
            if i is None:
                out.main.append((o, enc_span(hint[o]), ".", "_"))
            else:
                claimed.add(i)
                out.main.append((o, enc_span(hint[o]), profiles.add(crows[i].prof), ext_of(crows[i])))
    else:
        for slot in sorted((s for s in cand if s[0] == "c"), key=lambda s: s[1]):
            i = pick(slot)
            if i is not None:
                claimed.add(i)
                out.main.append((slot[1], enc_span(crows[i].span), profiles.add(crows[i].prof), ext_of(crows[i])))
    for slot in sorted((s for s in cand if s[0] == "f"), key=lambda s: s[1]):
        i = pick(slot)
        if i is not None:
            claimed.add(i)
            out.fn.append((slot[1], enc_span(crows[i].span), profiles.add(crows[i].prof), ext_of(crows[i])))
    # every other row is kept whole; a placed one also shows as a VIEW on each of its still-empty c/f slots
    have_main = {o for o, *_ in out.main}
    have_fn = {o for o, *_ in out.fn}
    for i, c in enumerate(crows):
        if i in claimed:
            continue
        out.rows.append(fmt_row(c, profiles, has_ids, group))
        if c.span and c.mark is None and not c.joint and not is_vetoed(c):   # joint (BSB `vvv`) / vetoed rows: sidecar ONLY
            for kind, n in c.src:
                if kind == "c" and hint is None and n not in have_main:
                    out.main.append((n, enc_span(c.span), ".", "_"))
                    have_main.add(n)
                elif kind == "f" and n not in have_fn:
                    out.fn.append((n, enc_span(c.span), ".", "_"))
                    have_fn.add(n)
    out.main.sort(key=lambda e: e[0])
    out.fn.sort(key=lambda e: e[0])
    return out


def pack_entries(entries: list) -> tuple[str, str, str]:
    """[(ord, span, prof, ext)] -> ("ord:span ...", "profs", "ext ext ...")"""
    return (" ".join(f"{o}:{sp}" for o, sp, _p, _e in entries), "".join(p for _o, _s, p, _e in entries),
            " ".join(e for _o, _s, _p, e in entries))


class LayerEncoder:
    def __init__(self, layer_id: str, kind: str, base_attribution: dict, has_ids: bool):
        self.layer_id, self.kind, self.base, self.has_ids = layer_id, kind, base_attribution, has_ids
        self.profiles = Profiles()

    def encode_book(self, crows_by_ref: dict[str, list[CRow]], refs: list[str], groups: dict[str, Group],
                    hints: dict[str, dict] | None = None) -> tuple[list, dict]:
        """-> (main, meta). `crows_by_ref` keys are group refs (anchor spine verses) or, for rows without a spine source
        token, any ref string (they go to `off`). `hints` = {ref: {srcOrd: span}} (compact's own main entries)."""
        n = len(refs)
        pos = {r: i for i, r in enumerate(refs)}
        main = [""] * n
        ch = {k: [""] * n for k in ("fn", "wp", "fp", "wx", "rows")}
        off: dict[str, list[str]] = {}
        for ref, crows in crows_by_ref.items():
            sourced = [c for c in crows if c.src]
            for c in crows:
                if not c.src:
                    off.setdefault(ref, []).append(fmt_row(c, self.profiles, self.has_ids, None))
            if not sourced:
                continue
            if ref not in groups or ref not in pos:
                raise KeyError(f"{ref}: rows with source tokens but no spine group/index entry")
            i = pos[ref]
            # With hints (compact's own main), a verse compact has NO entries for (e.g. a parenthetical whose alignments compact
            # drops on purpose) is an EMPTY hint, never "no hint": nothing may be claimed into a main that stays empty.
            vo = encode_verse(sourced, groups[ref], self.profiles, self.has_ids,
                              None if hints is None else hints.get(ref, {}))
            main[i], ch["wp"][i], wx_m = pack_entries(vo.main)
            ch["fn"][i], ch["fp"][i], wx_f = pack_entries(vo.fn)
            ch["wx"][i] = " ".join(x for x in (wx_m, wx_f) if x)
            ch["rows"][i] = " ".join(vo.rows)
        if hints:                                              # compact entries of verses that have no row at all
            for ref, h in hints.items():
                i = pos[ref]
                if ref not in crows_by_ref and h:
                    vo = encode_verse([], groups[ref], self.profiles, self.has_ids, h)
                    main[i], ch["wp"][i], wx = pack_entries(vo.main)
                    ch["wx"][i] = wx
        meta: dict = {k: (v if any(v) else None) for k, v in ch.items()}
        if meta["wx"] is not None and all(x == "_" for e in meta["wx"] for x in e.split()):
            meta["wx"] = None                                  # nothing but "_": the channel says nothing
        meta["off"] = off or None
        return main, meta

    def layer_json(self) -> dict:
        return {"version": LAYER_VERSION, "layer": self.layer_id, "kind": self.kind, "has_ids": self.has_ids,
                "attribution": self.base, "profiles": self.profiles.to_json()}


# --- decoding --------------------------------------------------------------------------------------------------
class LayerDecoder:
    def __init__(self, layer_json: dict):
        self.layer = layer_json
        self.has_ids = layer_json["has_ids"]
        self.profiles = Profiles.from_json(layer_json["profiles"])

    def _claimed(self, ref: str, group: Group | None, slot_kind: str, entries: list[str], profs: str, exts: list[str]
                 ) -> list[CRow]:
        out = []
        for k, e in enumerate(entries):
            if profs[k] == ".":
                continue                                     # a view of a multi-source row (the row is in `rows`)
            o, sp = e.split(":")
            c = CRow(ref=ref, src=[(slot_kind, int(o))], span=dec_span(sp), prof=self.profiles.of_char[profs[k]])
            if self.has_ids:
                c.sid = default_sid(group, c.src[0]) if group else None
            if exts[k] != "_":
                for part in exts[k].split(";"):
                    if part[0] == "t" and self.has_ids:
                        c.tids = [int(x) for x in part[1:].split(",")]
                    elif part[0] == "S" and self.has_ids:
                        c.sid = part[1:]
                    elif part[0] == "P":
                        c.pargs = [int(x) for x in part[1:].split(",")]
            out.append(c)
        return out

    def decode_book(self, main: list, meta: dict, refs: list[str], groups: dict[str, Group]) -> list[CRow]:
        out: list[CRow] = []
        g = lambda k, i: ((meta.get(k) or [""] * len(refs))[i] or "")           # noqa: E731
        for i, ref in enumerate(refs):
            group = groups.get(ref)
            m_entries = main[i].split() if main[i] else []
            f_entries = g("fn", i).split()
            exts = g("wx", i).split()
            m_ext = exts[:len(m_entries)] if exts else ["_"] * len(m_entries)
            f_ext = exts[len(m_entries):] if exts else ["_"] * len(f_entries)
            out += self._claimed(ref, group, "c", m_entries, g("wp", i), m_ext)
            out += self._claimed(ref, group, "f", f_entries, g("fp", i), f_ext)
            for entry in g("rows", i).split():
                out.append(parse_row(entry, ref, self.profiles, self.has_ids, group))
        for ref, entries in (meta.get("off") or {}).items():
            out += [parse_row(e, ref, self.profiles, self.has_ids, None) for e in entries]
        return out


# --- files ---------------------------------------------------------------------------------------------------------
def merge_meta(existing: dict | None, new: dict) -> dict:
    """compact's own meta channels stay as they are; the full-align channels are added (a name clash is a bug)."""
    out = dict(existing or {})
    for k, v in new.items():
        if v is None:
            continue
        if k in out and out[k] != v:
            raise ValueError(f"meta channel {k!r} already exists with different content")
        out[k] = v
    return out


def write_book(layer_dir: Path, book: str, digest: str, main: list, meta: dict, merge: bool = False) -> None:
    layer_dir.mkdir(parents=True, exist_ok=True)
    (layer_dir / f"{book}_{digest}.json").write_text(json.dumps(main, ensure_ascii=False) + "\n", encoding="utf-8")
    mp = layer_dir / f"{book}_{digest}.meta.json"
    existing = json.loads(mp.read_text(encoding="utf-8")) if merge and mp.exists() else None
    meta = merge_meta(existing, meta) if merge else {k: v for k, v in meta.items() if v is not None}
    if meta:
        mp.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")


def read_book(layer_dir: Path, book: str) -> tuple[list, dict]:
    mains = [f for f in layer_dir.glob(f"{book}_*.json") if f.name.count(".") == 1]
    if len(mains) != 1:
        raise FileNotFoundError(f"{layer_dir}/{book}_*.json: {len(mains)} candidates")
    main = json.loads(mains[0].read_text(encoding="utf-8"))
    mp = mains[0].with_name(mains[0].stem + ".meta.json")
    return main, (json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {})


def write_layer_json(layer_dir: Path, enc: LayerEncoder) -> None:
    (layer_dir / "_layer.json").write_text(json.dumps(enc.layer_json(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def read_layer_json(layer_dir: Path) -> dict:
    return json.loads((layer_dir / "_layer.json").read_text(encoding="utf-8"))
