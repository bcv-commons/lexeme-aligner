"""Jev v4 by CLAUSE, not by verse (2026-10-27; the owner's rule: never divide or send by verse).

A request covers one clause, or 2-3 short clauses of one sentence. Units follow bsb-add-books' CLAUSES.md (language-analysis
069898cf) on bcv-commons/greek-nt-clause-layout (vendored v0.3, its `words` = our spine keys):
  - one layout line = one item; the rows of an interrupted clause are one item (the interrupting clause stays its own item);
  - an item is joined to the next item of the same sentence while either has < JOIN_UNDER source words, up to MAX_LINES items and
    about MAX_WORDS source words; a row with no clause id (a title) always goes with the next item of its sentence.
Target words of a request (counted in WRITTEN words: a divided word is offered whole, all its parts, never half of it):
  - the chain's links of the request's source words, plus the unlinked words between them (the hull), widened by `margin` written
    words on each side (BSB 2, Zinza 3);
  - every target word has one HOME request (the request of its proposal owner; an unowned word the request whose hull holds it, else
    the nearest hull). Its own question (which source word does it render?) is asked only there, and the home window is widened to
    hold it, so no target word falls between two requests. Other requests may offer it as an option.
  - context, never linked: the whole Greek sentence and the text of the target verses the request touches.
Answers are decided per request (`jev_check.decide`), mapped back to verse positions and applied per verse in ascending
probability, so the cells stay per verse (same scorers, same fullalign_build). A link whose source and target words sit in two
different verses cannot be stored per verse: it is counted (`cross-verse answers`) and dropped.
Conventions: `--conventions ours` = our config/llm_conventions/<iso>.md as in jev_check; `--conventions modules` = bsb-add-books'
source jev.md + their target jev.md FILTERED per request (an affix rule only when a part of the window carries that chain_id, a
never-divided word only when the window has it) + our note (config/llm_conventions/<iso>.note.md). Every request's rule ids go to
the unit log, for results per rule id.

    .venv/bin/python -m lexeme_aligner.eval.jev_clauses --iso bsb --publish-iso eng --book MRK --chapter 1 --margin 2 --dry-run
    ALIGNER_OUT=<chain run> .venv/bin/python -m lexeme_aligner.eval.jev_clauses --iso zin_wbt --publish-iso zin --book MRK --margin 3 \
        --usj-dir <parts view> --parts <words.parquet> --conventions modules --variant C3 [--dry-run]
Cells: align_llm_jevC.<iso>.<BOOK>.v4.<variant>.jev-<θ>_<BOOK>.jsonl; unit log + summary in pipeline/work/jev-testA/clauses/.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lexeme_aligner.config import OUT, PRIOR_PACK
from lexeme_aligner.eval import jev_check as jc
from lexeme_aligner.eval import llm_edit as le
from lexeme_aligner.eval.llm_prompt import _GENERIC_CONVENTIONS

CLAUSES = Path("pipeline/vendor/greek-nt-clause-layout/v0.3/data/clauses.parquet")
MODULES = Path("pipeline/vendor/language-analysis/069898cf")
SOURCE_MODULE = {"NT": MODULES / "source/grc/30f77fbf/jev.md"}
TARGET_MODULE = {"zin": MODULES / "zin/ab3848ee/jev.md"}
LOG = jc.WORK / "clauses"
JOIN_UNDER, MAX_LINES, MAX_WORDS = 6, 3, 30
HOME_REACH = 8          # written words a window may grow to hold a homed word; a word further out is asked nowhere (counted)
GAP_MAX = 24            # a unit the chain linked to nothing gets the gap between its neighbours' links, at most this many tokens
UNIT_NOTE = ("\n\nThis request covers one clause, or a few clauses of one sentence, not a verse. `source_sentence` and "
             "`translation_context` are context only: link only the words in `source_words` and `translation_words`. A translation "
             "word marked `proposed_source: \"another clause\"` is proposed for a source word outside this request.")


# --- units --------------------------------------------------------------------------------------------------------------------
def clause_items(rows: list[dict]) -> list[dict]:
    """Layout rows of one book (text order) -> items: a row, or all rows of an interrupted clause together (at its first row)."""
    items, at = [], {}
    for r in rows:
        k = (r["sentence"], r["clause"]) if r["clause"] else None
        if k is not None and k in at:
            it = items[at[k]]                  # the same clause: over a verse break (the last item) or resumed after an interruption
            it["words"] += r["words"]; it["text"] += (" " if it is items[-1] else " … ") + r["text"]
            continue
        if k is not None:
            at[k] = len(items)
        items.append({"sentence": r["sentence"], "clause": r["clause"], "words": list(r["words"]), "text": r["text"]})
    return items


def join_units(items: list[dict], join_under: int = JOIN_UNDER, max_lines: int = MAX_LINES, max_words: int = MAX_WORDS) -> list[dict]:
    units: list[dict] = []
    for it in items:
        u = units[-1] if units else None
        if (u is not None and u["sentence"] == it["sentence"] and u["items"] < max_lines
                and len(u["words"]) + len(it["words"]) <= max_words
                and (u["open"] or len(u["last"]) < join_under or len(it["words"]) < join_under)):
            u["words"] += it["words"]; u["items"] += 1; u["last"] = it["words"]; u["texts"].append(it["text"])
            u["open"] = it["clause"] is None
        else:
            units.append({"sentence": it["sentence"], "words": list(it["words"]), "items": 1, "last": it["words"],
                          "texts": [it["text"]], "open": it["clause"] is None})
    return units


def load_units(book: str, clauses: Path = CLAUSES) -> tuple[list[dict], dict[str, str]]:
    """(units of one book in text order, sentence id -> its Greek text)."""
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
    t = pq.read_table(clauses)
    rows = t.filter(pc.equal(t["book"], book)).to_pylist()
    sent: dict[str, list[str]] = collections.defaultdict(list)
    for r in rows:
        sent[r["sentence"]].append(r["text"])
    return join_units(clause_items(rows)), {k: " ".join(v) for k, v in sent.items()}


# --- target windows -----------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Book:
    """The book's verse proposals laid out in one target order. `word` = written-word id per target token (a parts view: all
    parts of a word share it); `order` = (ref, j) -> global position."""
    props: dict[int, le.Proposal]
    seq: list[tuple[int, int]]
    order: dict[tuple[int, int], int]
    word: list[int]
    key_to_h: dict[str, tuple[int, int]]
    units_of: dict[int, list | None]


def lay_out(props: list[le.Proposal], units_of: dict[int, list | None]) -> Book:
    seq, word, wid = [], [], -1
    key_to_h = {}
    for p in sorted(props, key=lambda p: p.ref):
        us = units_of.get(p.ref)
        prev = None
        for j in range(len(p.toks)):
            w = us[j]["word_index"] if us else j
            if w != prev:
                wid += 1; prev = w
            seq.append((p.ref, j)); word.append(wid)
        for t in p.heb:
            for k in t.keys or ():
                key_to_h[k] = (p.ref, t.idx)
    return Book({p.ref: p for p in props}, seq, {x: i for i, x in enumerate(seq)}, word, key_to_h, units_of)


def _whole_words(b: Book, lo: int, hi: int) -> tuple[int, int]:
    while lo > 0 and b.word[lo - 1] == b.word[lo]:
        lo -= 1
    while hi + 1 < len(b.seq) and b.word[hi + 1] == b.word[hi]:
        hi += 1
    return lo, hi


def _widen(b: Book, lo: int, hi: int, margin: int) -> tuple[int, int]:
    """`margin` written words further out on each side (then whole words)."""
    lo, hi = _whole_words(b, lo, hi)
    for _ in range(margin):
        if lo > 0:
            lo = _whole_words(b, lo - 1, lo - 1)[0]
        if hi + 1 < len(b.seq):
            hi = _whole_words(b, hi + 1, hi + 1)[1]
    return lo, hi


def windows(b: Book, units: list[dict], margin: int) -> list[dict]:
    """Per unit: hs [(ref, idx)], hull (lo, hi) or None, window [lo, hi], ring {global pos: 0 in hull / k-th written word out / -1
    widened for a homed word}, home set of global positions whose question is asked here."""
    owner_at = {}
    for ref, p in b.props.items():
        for h, s in p.owner.items():
            for j in s:
                owner_at[b.order[(ref, j)]] = (ref, h)
    unit_of_h = {}
    out = []
    for ui, u in enumerate(units):
        hs = [b.key_to_h[k] for k in u["words"] if k in b.key_to_h]
        hs = list(dict.fromkeys(hs))
        for h in hs:
            unit_of_h.setdefault(h, ui)
        pos = sorted(b.order[(ref, j)] for ref, h in hs for j in b.props[ref].owner.get(h, []))
        out.append({"unit": u, "hs": hs, "hull": (pos[0], pos[-1]) if pos else None})
    hulls = [(i, o["hull"]) for i, o in enumerate(out) if o["hull"]]
    for i, o in enumerate(out):                            # no link at all: the gap between the neighbours' hulls
        if o["hull"] is None and o["hs"]:
            prev = max((h[1] for k, h in hulls if k < i), default=-1)
            nxt = min((h[0] for k, h in hulls if k > i), default=len(b.seq))
            o["hull"] = (prev + 1, min(nxt - 1, prev + GAP_MAX)) if prev + 1 <= nxt - 1 else (max(prev, 0), max(prev, 0))
            o["no_links"] = True
    for o in out:
        if o["hull"]:
            hl = _whole_words(b, *o["hull"])
            o["lo"], o["hi"] = _widen(b, *o["hull"], margin)
            o["ring"] = {}
            for g in range(o["lo"], o["hi"] + 1):
                o["ring"][g] = 0 if hl[0] <= g <= hl[1] else _ring(b, g, hl)
            o["home"] = set()
    live = [i for i, o in enumerate(out) if o.get("hull")]
    for g in range(len(b.seq)):                            # each target token's home request
        ref, j = b.seq[g]
        if g in owner_at and owner_at[g] in unit_of_h:
            home = unit_of_h[owner_at[g]]
        else:
            inside = [i for i in live if out[i]["hull"][0] <= g <= out[i]["hull"][1]]
            home = inside[0] if inside else min(live, key=lambda i: (min(abs(g - out[i]["hull"][0]), abs(g - out[i]["hull"][1])), i),
                                                default=None)
        if home is None:
            continue
        o = out[home]
        if not o["lo"] <= g <= o["hi"] and _ring(b, g, (o["lo"], o["hi"])) > HOME_REACH:
            o.setdefault("unasked", set()).add(g)
            continue
        if not o["lo"] <= g <= o["hi"]:
            lo, hi = min(o["lo"], g), max(o["hi"], g)
            lo, hi = _whole_words(b, lo, hi)
            for x in range(lo, hi + 1):
                o["ring"].setdefault(x, -1)
            o["lo"], o["hi"] = lo, hi
        o["home"].add(g)
    return out


def _ring(b: Book, g: int, hull: tuple[int, int]) -> int:
    """How many written words out from the hull g sits (1 = the first word outside)."""
    if g < hull[0]:
        return len({b.word[x] for x in range(g, hull[0])})
    return len({b.word[x] for x in range(hull[1] + 1, g + 1)})


# --- one request --------------------------------------------------------------------------------------------------------------
def unit_proposal(b: Book, w: dict) -> tuple[le.Proposal, list, list, list | None]:
    """A synthetic Proposal over the unit's source words and its window: (proposal, src map [(ref, idx)], tgt map [global pos],
    the window's parts units or None). Target words owned by a source word outside the unit are listed in `p.method` as None and
    flagged by the caller."""
    tgt = list(range(w["lo"], w["hi"] + 1))
    local = {g: k for k, g in enumerate(tgt)}
    heb, src = [], []
    first = b.props[w["hs"][0][0]]
    p = le.Proposal(first.ref, first.book, first.ch, first.v, [], [b.props[b.seq[g][0]].toks[b.seq[g][1]] for g in tgt])
    for k, (ref, h) in enumerate(w["hs"]):
        vp = b.props[ref]
        t = next(t for t in vp.heb if t.idx == h)
        heb.append(dataclasses.replace(t, idx=k)); src.append((ref, h))
        own = [local[b.order[(ref, j)]] for j in vp.owner.get(h, []) if b.order[(ref, j)] in local]
        if own or h in vp.owner:
            p.owner[k] = own
        if h in vp.method:
            p.method[k] = vp.method[h]
        if h in vp.doubt_h:
            p.doubt_h[k] = set(vp.doubt_h[h])
    p.heb = heb
    for g in tgt:
        if g not in w["home"]:
            continue
        ref, j = b.seq[g]
        vp = b.props[ref]
        if j in vp.doubt_t:
            p.doubt_t[local[g]] = set(vp.doubt_t[j])
        if j in vp.stop_t:
            p.stop_t.add(local[g])
    units = None
    if any(b.units_of.get(b.seq[g][0]) for g in tgt):
        units = []
        for g in tgt:
            us = b.units_of.get(b.seq[g][0])
            u = dict(us[b.seq[g][1]]) if us else {"in_word": None, "part": None}
            u["word_index"] = b.word[g]
            units.append(u)
    return p, src, tgt, units


def filter_module(text: str, chain_ids: set[str], words: set[str]) -> tuple[str, list[str]]:
    """A target jev.md for one request: an affix line (`m:` id) only when a part in the window carries it, a never-divided word
    (`w:` id) only when the window has that written word; every other line kept. Returns (text, the rule ids kept)."""
    out, kept = [], []
    for line in text.splitlines():
        m = re.search(r"\[`([^`]+)`", line) if line.startswith("- ") else None
        if m:
            rid = m.group(1)
            if (rid.startswith("m:") and rid not in chain_ids) or (rid.startswith("w:") and rid[2:].lower() not in words):
                continue
            kept.append(rid)
        out.append(line)
    return "\n".join(out), kept


def source_rule_ids(text: str) -> list[str]:
    return re.findall(r"`((?:grc|hbo|arc)\.\d+)`", text)


def body_for(b: Book, w: dict, sentences: dict, lex_pos: dict, lang: str, conv: dict, skip_pairs, hints, chain_ids_of) -> tuple:
    p, src, tgt, units = unit_proposal(b, w)
    rules: list[str] = []
    if conv["mode"] == "modules":
        ids = {c for g in tgt for c in chain_ids_of(b.seq[g])}
        written = {(u["in_word"] if u and u.get("in_word") else tok).lower() for u, tok in zip(units or [None] * len(p.toks), p.toks)}
        target, rules = filter_module(conv["target"], ids, written)
        conventions = (jc._CONV + "\n\nSource-language rules:\n" + conv["source"] + f"\n\nTarget-language rules ({lang}):\n" + target
                       + ("\n\n" + conv["note"] if conv["note"] else "") + UNIT_NOTE)
        rules = source_rule_ids(conv["source"]) + rules
    else:
        conventions = conv["text"] + UNIT_NOTE
    body = jc.build_request(p, lex_pos, lang, conventions, "v4", skip_pairs, hints, units)
    outside = set()
    for k, g in enumerate(tgt):                            # a word proposed for a source word of another request
        ref, j = b.seq[g]
        if any(j in s for h, s in b.props[ref].owner.items() if (ref, h) not in set(src)):
            outside.add(k)
    for k, tw in enumerate(body["state"]["translation_words"]):
        if k in outside:
            tw["proposed_source"] = "another clause"
    refs = sorted({ref for ref, _ in src} | {b.seq[g][0] for g in tgt})
    vs = [b.props[r] for r in refs]
    st = body["state"]
    st["reference"] = f"{vs[0].book} {vs[0].ch}:{vs[0].v}" + (f"–{vs[-1].ch}:{vs[-1].v}" if len(vs) > 1 else "")
    st["source_sentence"] = sentences.get(w["unit"]["sentence"])
    st["translation_context"] = " ".join(jc._written(v, b.units_of.get(v.ref)) for v in vs)
    return p, src, tgt, body, rules


# --- answers back to verses ---------------------------------------------------------------------------------------------------
def map_back(b: Book, p: le.Proposal, src: list, tgt: list, h_ed: list, t_ed: list, ring: dict) -> tuple[dict, collections.Counter]:
    """Edits of one request -> {ref: ([(prob, Edit)] source side, [(prob, Edit)] target side)} in verse positions."""
    per: dict[int, tuple[list, list]] = collections.defaultdict(lambda: ([], []))
    c: collections.Counter = collections.Counter()
    for side, eds in ((0, h_ed), (1, t_ed)):
        for prob, e in eds:
            gs = [tgt[j] for j in e.t]
            for g in gs:
                c[f"link to ring {ring.get(g, 0)}"] += e.kind != "added"
            ts = [b.seq[g] for g in gs]
            if e.h is None:
                ref = ts[0][0]
                per[ref][side].append((prob, le.Edit(e.kind, t=[j for _, j in ts], reason=e.reason)))
                continue
            ref, h = src[e.h]
            if any(r != ref for r, _ in ts):
                c["cross-verse answers (dropped)"] += 1
                continue
            kind = e.kind
            vp = b.props[ref]
            tok = next(t for t in vp.heb if t.idx == h)
            if kind == "assign" and not tok.is_content and len(ts) == 1:     # v4: a function word on a word another clause owns
                j = ts[0][1]
                if any(j in s and o != h for o, s in vp.owner.items()):
                    kind = "inside"; c["fn inside (other clause)"] += 1
            per[ref][side].append((prob, le.Edit(kind, h=h, t=[j for _, j in ts], reason=e.reason)))
    return per, c


def apply_verse(p: le.Proposal, h_ed: list, t_ed: list) -> tuple:
    edits = [e for _, e in sorted(h_ed, key=lambda x: x[0])] + [e for _, e in sorted(t_ed, key=lambda x: x[0])]
    inside: dict[int, int] = {}
    owner, changed, _ = le.apply_edits(p, edits, inside)
    owned = {j for s in owner.values() for j in s}
    none_t = {e.t[0] for e in edits if e.kind == "added"} - owned - set(inside.values())
    return owner, changed, inside, none_t


# --- main ---------------------------------------------------------------------------------------------------------------------
def _chain_ids(parts_parquet: Path | None):
    """(ref, j) of the view -> chain_ids of that unit (a parts view's unit = one part of a written word)."""
    if not parts_parquet:
        return lambda x: ()
    import pyarrow.parquet as pq
    from lexeme_aligner.refs import encode
    cid = {}
    for r in pq.read_table(parts_parquet, columns=["book", "chapter", "verse", "i", "part", "chain_id"]).to_pylist():
        if r["chain_id"]:
            cid[(encode(r["book"], r["chapter"], r["verse"]), r["i"], r["part"])] = r["chain_id"]
    return cid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True); ap.add_argument("--publish-iso", required=True); ap.add_argument("--book", required=True)
    ap.add_argument("--chapter", type=int, action="append")
    ap.add_argument("--margin", type=int, required=True, help="written words of margin on each side of the chain's links")
    ap.add_argument("--conventions", default="ours", choices=["ours", "modules"])
    ap.add_argument("--usj-dir", type=Path); ap.add_argument("--parts", type=Path)
    ap.add_argument("--tags", type=Path); ap.add_argument("--skip-pairs", type=Path); ap.add_argument("--hints", type=Path)
    ap.add_argument("--variant", required=True); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true", help="build the requests and report their size; no call, no cell")
    a = ap.parse_args()
    from lexeme_aligner.gapfill import load_priors
    from lexeme_aligner.run_pilot import NT_BOOKS
    if a.book not in NT_BOOKS:
        raise SystemExit("[jev-clauses] OT books need the Hebrew clause layout (not wired yet)")
    usj = a.usj_dir or Path(f"pipeline/work/ingest-cache/usj-{a.iso}")
    tags = json.loads(a.tags.read_text(encoding="utf-8")) if a.tags else None
    props, _ = le.build_proposals(a.iso, a.publish_iso, usj, [a.book], signals=le.SIGNALS + ("uncovered-fn",), strong_tags=tags)
    units_of = {}
    if a.parts:
        from lexeme_aligner import target_parts as tp
        from lexeme_aligner.refs import encode
        units_of = {encode(*k): v for k, v in tp.unit_words(usj, tp.load_parts(a.parts)).items()}
        vmap = json.loads((usj / tp.VIEW_MAP).read_text(encoding="utf-8"))[a.book]
        cid = _chain_ids(a.parts)
        part_at = {encode(a.book, *map(int, cv.split(":"))): um for cv, um in vmap.items()}
        chain_ids_of = lambda x: (cid.get((x[0], *part_at[x[0]][x[1]]), None),) if x[0] in part_at else ()   # noqa: E731
    else:
        chain_ids_of = lambda x: ()                                                                          # noqa: E731
    b = lay_out(props, units_of)
    units, sentences = load_units(a.book)
    ws = [w for w in windows(b, units, a.margin) if w["hs"] and w.get("hull")]
    if a.chapter:
        ws = [w for w in ws if b.props[w["hs"][0][0]].ch in set(a.chapter)]
    lang = jc.LANG.get(a.publish_iso, a.publish_iso)
    if a.conventions == "modules":
        conv = {"mode": "modules", "source": SOURCE_MODULE["NT"].read_text(encoding="utf-8"),
                "target": TARGET_MODULE[a.publish_iso].read_text(encoding="utf-8"),
                "note": (le._CONVENTIONS_DIR / f"{a.publish_iso}.note.md").read_text(encoding="utf-8")
                if (le._CONVENTIONS_DIR / f"{a.publish_iso}.note.md").exists() else ""}
    else:
        fp = le._CONVENTIONS_DIR / f"{a.publish_iso}.md"
        conv = {"mode": "ours", "text": jc._CONV + f"\n\nLanguage conventions ({lang}):\n"
                + (fp.read_text(encoding="utf-8") if fp.exists() else _GENERIC_CONVENTIONS)}
    skip_pairs = ({(x, st) for x, st, *_ in json.loads(a.skip_pairs.read_text(encoding="utf-8"))["pairs"]} if a.skip_pairs else None)
    hints = json.loads(a.hints.read_text(encoding="utf-8")) if a.hints else None
    lex_pos, _ = load_priors(PRIOR_PACK)
    jobs = []
    for w in ws:
        p, src, tgt, body, rules = body_for(b, w, sentences, lex_pos, lang, conv, skip_pairs, hints, chain_ids_of)
        if body["questions"]:
            jobs.append((w, p, src, tgt, body, rules))
    tag = f"jevC.{a.iso}.{a.book}.v4.{a.variant}"
    LOG.mkdir(parents=True, exist_ok=True)
    size = collections.Counter()
    for w, p, src, tgt, body, rules in jobs:
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        size["requests"] += 1; size["questions"] += len(body["questions"]); size["body bytes"] += len(raw)
        size["state bytes"] += len(json.dumps(body["state"], ensure_ascii=False).encode())
        size["conventions bytes"] += len(body["state"]["conventions"].encode())
        size["no chain link (gap window)"] += bool(w.get("no_links"))
        size["source words over a verse break"] += len({r for r, _ in src}) > 1
        size["window over a verse break"] += len({b.seq[g][0] for g in tgt}) > 1
        size["target words asked nowhere (too far)"] += len(w.get("unasked", ()))
    verses = {r for w in ws for r, _ in w["hs"]}
    size["verses"] = len(verses)
    if a.dry_run:
        print(json.dumps({"tag": tag, "dry_run": dict(size)}, ensure_ascii=False))
        return 0
    key = "" if os.environ.get("JEV_CACHE_ONLY") else jc.api_key()
    t0 = time.monotonic()
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(lambda j: jc.ask_split(j[4], key), jobs))
    usage = collections.Counter()
    log = []
    answered = []
    for (w, p, src, tgt, body, rules), res in zip(jobs, results):
        sha = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        log.append({"sentence": w["unit"]["sentence"], "keys": w["unit"]["words"], "source": [list(x) for x in src],
                    "window": [list(b.seq[g]) for g in tgt], "home": sorted(tgt.index(g) for g in w["home"]),
                    "rules": rules, "body": sha, "error": res.get("_error")})
        if "_error" in res:
            usage["requests failed"] += 1
            print(f"[jev-clauses] {body['state']['reference']}: {res['_error']}", file=sys.stderr)
            continue
        answered.append((w, p, src, tgt, res.get("answers", {}), res.get("model", jc.MODEL)))
        usage["questions"] += len(body["questions"]); usage["requests"] += 1
        if not res.get("_cached"):
            u = res.get("usage", {})
            usage["input_tokens"] += u.get("input_tokens", 0); usage["output_tokens"] += u.get("output_tokens", 0)
            usage["requests billed"] += 1
    (LOG / f"{tag}.units.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in log), encoding="utf-8")
    summary = {"tag": tag, "model": jc.MODEL, "margin": a.margin, "conventions": a.conventions, "size": dict(size),
               "seconds": round(time.monotonic() - t0, 1), "usage": dict(usage), "cells": {}}
    vprops = sorted(b.props.values(), key=lambda p: p.ref)
    answered_by = {}
    for theta in jc.THRESHOLDS:
        per: dict[int, tuple[list, list]] = collections.defaultdict(lambda: ([], []))
        tally = collections.Counter()
        for w, p, src, tgt, ans, model in answered:
            h_ed, t_ed, c = jc.decide(p, ans, theta, None, "v4", scored=True)
            tally.update(c)
            m, c2 = map_back(b, p, src, tgt, h_ed, t_ed, w["ring"])
            tally.update(c2)
            for ref, (hh, tt) in m.items():
                per[ref][0].extend(hh); per[ref][1].extend(tt)
            for ref, _ in src:
                answered_by[ref] = model
        finals = {}
        for ref, (hh, tt) in per.items():
            finals[ref] = apply_verse(b.props[ref], hh, tt)
            tally["source tokens changed"] += len(finals[ref][1]); tally["inside links"] += len(finals[ref][2])
        cell = f"{tag}.jev-{theta:g}"
        recs = le.to_records(vprops, finals, {"provider": "typesafe", "model": jc.MODEL, "theta": theta, "design": "v4",
                                              "unit": "clause", "margin": a.margin})
        for book_recs in recs.values():
            for r in book_recs:
                r["llm"] = {**r["llm"], "answered_by": answered_by.get(r["ref"])}
        le.write_records(recs, cell, Path(OUT))
        summary["cells"][cell] = dict(tally)
    (LOG / f"{tag}.summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
