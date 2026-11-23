"""Refine WHOLE-WORD Jev answers to word PARTS, asking Jev only what the word answers leave open (2026-11-23; owner: "keep the whole
word Jev results and just refine what is really needed in order to save costs").

A whole-word answer says: Greek word g is rendered by the translation words T (or by nothing). Inside that frame:

  component = Greek words and translation words joined by the word answers of one verse (one word may render two Greek words);
  piece     = a part of a translation word that the edition's part analysis divides (config/target_parts.json), else the whole word;
  unit      = a source part of the parts spine (word-parts v1.1: stem / ending / augment / preverb ...).

Per component:
  - one piece                      -> no question: each Greek word's carrier unit (the stem) takes it
  - one Greek unit                 -> no question: that unit takes every piece
  - otherwise                      -> ASK: one Choice per piece, "which of these Greek parts does this piece render?", options = the
                                      component's units + `none`. With --trust-chain, a component the chain's own part links divide
                                      cleanly is taken from the chain instead (default off: the chain's part links were right about
                                      2 in 3 to 3 in 4 times in the October hand check).
A Greek word whose answer said "nothing" gives all its units `none` (checked); a Greek word whose parts end up with no piece keeps its
word answer on its carrier, so a refined layer never loses a word-level link. Links to the verse-number token (heb_scg) are dropped.

Requests: one per clause unit of the Greek layout (jev_clauses.load_units), holding only the asked components' questions; the clause's
Greek sentence and the verse text are context. Answers are cached like every Jev request (jev_check.ask / ask_split), --max-usd is
required for a run that sends, --dry-run gives the estimate.

Output: one cell per book on the PARTS spine, `align_llm_jevR.<tag>.<BOOK>.v4.<variant>.jev-0_<BOOK>.jsonl` in --out-dir (pairs with
h_idx = parts-spine unit, t_idx, t_parts), read by part_alignments --llm-cell to make the part-level layer.

Base answers may come from ANOTHER edition of the same language (`accept`): where this edition's own chain links a Greek word to the
same written Hebrew word(s) as the other edition's Jev answer, the link is taken as checked (HEBM95 vs heb_scg: 96.7% of those agree
with HEBM95's own Jev answer). `jev_clauses --accept` then asks the word questions only for the rest.

    python -m lexeme_aligner.eval.jev_refine scope  --tag heb_scg --usj-dir DIR --base-dir pipeline/work/out --base-cell 'jevA.heb_scg.{BOOK}.v4.jev-0'
    python -m lexeme_aligner.eval.jev_refine run    ... --out-dir DIR --variant R1 (--dry-run | --max-usd X)
    python -m lexeme_aligner.eval.jev_refine accept --tag heb_mod --usj-dir DIR --chain-dir WORDS_DIR --sister-dir DIR \\
        --sister-cell 'jevA.heb_scg.{BOOK}.v4.jev-0' --out FILE
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import gzip
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lexeme_aligner.config import PARTS_SPINE_DB, WORD_SPINE_DB

NONE = "none"
CHAIN_METHODS = ("spanext", "eflomal", "gloss", "gapfill", "residual")     # priority order for --trust-chain (compact's)
CONVENTIONS = (
    "Each question is about ONE piece of a translation word: a prefix, a suffix or the stem of a written word, or a whole word. It "
    "is already decided which Greek words that translation word renders (`decided`); the question is only which PART of those "
    "Greek words the piece renders. A Greek stem carries the word's meaning; an ending, augment or prefix carries grammar (case, "
    "number, person, tense, voice). A translation prefix such as a preposition or the article, or a pronoun suffix, usually renders "
    "a Greek ending, a Greek article or a Greek pronoun; a translation stem usually renders a Greek stem. Answer `none` when the "
    "piece renders no part of these Greek words on its own (it only serves the translation's own grammar or spelling).")


def _open(fp: Path):
    return gzip.open(fp, "rt", encoding="utf-8") if str(fp).endswith(".gz") else open(fp, encoding="utf-8")


def cell_files(out_dir: Path, cell: str, book: str) -> list[Path]:
    name = f"align_llm_{cell.format(BOOK=book)}_{book}.jsonl"
    return [p for p in (out_dir / name, out_dir / (name + ".gz")) if p.exists()]


def load_answers(out_dir: Path, cell: str, book: str) -> dict[tuple[int, int], dict]:
    """(ch, v) -> {"links": {h: [t]}, "none": {h}} of a whole-word Jev cell (`llm_skipped` unrepresented = Jev said nothing)."""
    out = {}
    for fp in cell_files(out_dir, cell, book):
        with _open(fp) as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                links = {p["h_idx"]: list(p["t_idx"]) for p in r.get("pairs", ()) if p.get("t_idx")}
                none = {s["h_idx"] for s in r.get("llm_skipped", ()) if s.get("status") == "unrepresented"} - set(links)
                out[(r["chapter"], r["verse"])] = {"links": links, "none": none}
    return out


# --- source: words and their units ---------------------------------------------------------------------------------------------
def _base(k: str) -> str:
    return k.split(".")[0]


def word_units(word_toks: list, part_toks: list) -> dict[int, list]:
    """Word-spine token idx -> its parts-spine units (text order). A unit belongs to the word whose keys hold its base key."""
    owner = {}
    for t in word_toks:
        for k in t.keys or ():
            owner[k] = t.idx
    out: dict[int, list] = collections.defaultdict(list)
    for u in part_toks:
        hs = {owner[_base(k)] for k in (u.keys or ()) if _base(k) in owner}
        for h in hs:
            out[h].append(u)
    return dict(out)


def carrier(units: list):
    """The unit that carries the word's lexeme: the one keyed by the word key itself (no `.<n>`)."""
    return next((u for u in units if u.keys and "." not in u.keys[0]), units[0])


def unit_kind(u, units: list) -> str:
    lex = u.lexeme or ""
    if ":~" in lex:
        return lex.split(":~", 1)[1].split(":")[0]
    return "stem" if len(units) > 1 else "word"


# --- target: pieces ---------------------------------------------------------------------------------------------------------------
def pieces_of(hp: dict, book: str, tref, toks: list[str], j: int) -> list[tuple[int, int | None, str, str]]:
    """[(token, part, letters, kind)] of target word j: its parts when the analysis divides it (and its token matches), else whole."""
    info = hp.get((book, tref[0], tref[1], j)) if tref else None
    if info and info["token"] == toks[j] and info["parts"]:
        from lexeme_aligner.target_parts import FUNCTION_CLASSES
        core = [pt for pt, _l, _m, c in info["parts"] if c not in FUNCTION_CLASSES]
        out = []
        for pt, letters, _lemma, cls in info["parts"]:
            kind = ("stem" if cls not in FUNCTION_CLASSES or not core else
                    "prefix" if pt < min(core) else "suffix" if pt > max(core) else "infix")
            out.append((j, pt, letters, kind))
        return out
    return [(j, None, toks[j], "word")]


def is_verse_number(hp: dict, book: str, tref, toks: list[str], j: int) -> bool:
    info = hp.get((book, tref[0], tref[1], j)) if tref else None
    return bool(info and info["token"] == toks[j] and not info["parts"])


# --- components ---------------------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Component:
    ref: tuple[int, int]
    words: list[int]                          # word-spine h of the Greek words
    toks: list[int]                           # target tokens
    pieces: list[tuple[int, int | None, str, str]]
    units: list                               # parts-spine units of the words, text order
    how: str = ""                             # one-piece / one-unit / chain / ask
    assign: dict = dataclasses.field(default_factory=dict)    # piece index -> unit idx (parts spine) or None (= none)
    links: dict = dataclasses.field(default_factory=dict)     # h -> its target tokens (the word answer)
    word_units: dict = dataclasses.field(default_factory=dict)  # h -> its units


def components(links: dict[int, list[int]], units_of: dict[int, list], pieces: dict[int, list]) -> list[Component]:
    """Union-find over (Greek word, target token) links of one verse."""
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for h, ts in links.items():
        for t in ts:
            parent[find(("t", t))] = find(("h", h))
    groups: dict = collections.defaultdict(lambda: ([], []))
    for h in links:
        groups[find(("h", h))][0].append(h)
    for t in {t for ts in links.values() for t in ts}:
        groups[find(("t", t))][1].append(t)
    out = []
    for hs, ts in groups.values():
        hs, ts = sorted(hs), sorted(ts)
        us = sorted({u.idx: u for h in hs for u in units_of.get(h, [])}.values(), key=lambda u: u.idx)
        out.append(Component((0, 0), hs, ts, [p for t in ts for p in pieces[t]], us,
                             links={h: links[h] for h in hs}, word_units={h: units_of[h] for h in hs}))
    return out


def classify(c: Component, chain: dict | None = None) -> str:
    """Sets c.how (and c.assign when no question is needed)."""
    if len(c.pieces) == 1:
        c.how = "one-piece"
    elif len(c.units) == 1:
        c.how = "one-unit"
        c.assign = {i: c.units[0].idx for i in range(len(c.pieces))}
    elif chain is not None and (a := chain_split(c, chain)) is not None:
        c.how, c.assign = "chain", a
    else:
        c.how = "ask"
    return c.how


def chain_split(c: Component, chain: dict) -> dict | None:
    """The chain's own part links inside the component, when they give each piece at most one unit and every Greek word a piece."""
    uid = {u.idx for u in c.units}
    a = {}
    for i, (t, pt, _l, _k) in enumerate(c.pieces):
        owners = {u for u in chain.get((t, pt), set()) | chain.get((t, None), set()) if u in uid}
        if len(owners) > 1:
            return None
        a[i] = next(iter(owners)) if owners else None
    got = {x for x in a.values() if x is not None}
    if any(not {u.idx for u in c.word_units[h]} & got for h in c.words):
        return None
    return a


# --- requests -------------------------------------------------------------------------------------------------------------------
def _gloss(t) -> str:
    return f", '{t.gloss_en}'" if getattr(t, "gloss_en", None) else ""


def unit_label(u, units: list, word) -> str:
    from lexeme_aligner.eval.llm_prompt import _gram_tag
    kind = unit_kind(u, units)
    gram = _gram_tag(word).strip(" []")
    if kind == "word":
        return f"the Greek word {word.surface} ({word.lemma}{_gloss(word)}{', ' + gram if gram else ''})"
    return (f"the {kind} {u.surface} of the Greek word {word.surface} ({word.lemma}{_gloss(word)}"
            f"{', ' + gram if gram else ''})")


def piece_label(p, toks: list[str]) -> str:
    t, pt, letters, kind = p
    return f"the whole word {toks[t]}" if pt is None else f"the {kind} {letters} of the word {toks[t]}"


def request_body(comps: list, word_toks: dict, toks_of: dict, text_of: dict, sentence: str | None, lang: str, ref_label: str) -> tuple:
    """One request for the asked components of a clause: (body, qmap {qid: (component, piece index)}, umap {uid: unit idx})."""
    from lexeme_aligner.eval import jev_check as jc
    decided, src_parts, tr_parts, qs, qmap, umap = [], [], [], {}, {}, {}
    nu = npc = 0
    for c in comps:
        toks = toks_of[c.ref]
        wt = {t.idx: t for t in word_toks[c.ref]}
        units_by_word = {h: [u for u in c.units if u.idx in {x.idx for x in c.word_units[h]}] for h in c.words}
        ids = {}
        for h in c.words:
            for u in units_by_word[h]:
                ids[u.idx] = f"u{nu}"; umap[f"u{nu}"] = (c, u.idx); nu += 1
                src_parts.append({"id": ids[u.idx], "part": unit_kind(u, units_by_word[h]), "text": u.surface,
                                  "of_greek_word": wt[h].surface})
        for h in c.words:
            decided.append(f"The Greek word {wt[h].surface} is rendered by: "
                           + " ".join(toks[t] for t in c.links[h]))
        for i, p in enumerate(c.pieces):
            pid = f"p{npc}"; npc += 1
            tr_parts.append({"id": pid, "piece": p[3], "text": p[2], "of_word": toks[p[0]]})
            crit = {ids[u.idx]: unit_label(u, units_by_word[h], wt[h]) for h in c.words for u in units_by_word[h]}
            crit[NONE] = "this piece renders no part of these Greek words on its own"
            qs[pid] = {"type": "choice", "criteria": crit, "instructions":
                       f"Piece {pid} ({piece_label(p, toks)}) in `translation_pieces`: which part of the Greek words in `decided` "
                       f"does it render? Follow `conventions`."}
            qmap[pid] = (c, i)
    state = {"reference": ref_label, "source_language": "Greek", "translation_language": lang, "conventions": CONVENTIONS,
             "source_sentence": sentence, "translation_text": " ".join(text_of[r] for r in sorted({c.ref for c in comps})),
             "decided": decided, "source_parts": src_parts, "translation_pieces": tr_parts}
    return {"model": jc.MODEL, "state": state, "questions": qs}, qmap, umap


# --- one book ---------------------------------------------------------------------------------------------------------------------
def book_components(tag: str, book: str, usj: Path, answers: dict, hp: dict, chain: dict | None):
    """Per verse rec of the parts corpus: (word rec, parts rec, components)."""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    remap = remapper(tag, str(usj))
    wrecs = {(r.ch, r.v): r for r in build_corpus([book], usj, HebrewSource(spine_db=WORD_SPINE_DB), remap)}
    prec = {(r.ch, r.v): r for r in build_corpus([book], usj, HebrewSource(spine_db=PARTS_SPINE_DB), remap)}
    out = []
    for key, pr in prec.items():
        wr = wrecs.get(key)
        ans = answers.get(key)
        if wr is None or ans is None:
            out.append((wr, pr, [], ans)); continue
        uo = word_units(wr.heb, pr.heb)
        links = {h: [t for t in ts if t < len(pr.toks) and not is_verse_number(hp, book, pr.tref, pr.toks, t)]
                 for h, ts in ans["links"].items() if h in uo}
        links = {h: ts for h, ts in links.items() if ts}
        pieces = {t: pieces_of(hp, book, pr.tref, pr.toks, t) for ts in links.values() for t in ts}
        cs = components(links, uo, pieces)
        for c in cs:
            c.ref = key
            classify(c, (chain or {}).get(key) if chain is not None else None)
        out.append((wr, pr, cs, ans))
    return out


def load_chain(out_dir: Path, tag: str, book: str) -> dict:
    """(ch, v) -> {(token, part|None): {parts-spine unit idx}} from the chain's parts-run align files, first method with a link."""
    from lexeme_aligner.align_files import tag_files
    per: dict = collections.defaultdict(dict)
    for m in reversed(CHAIN_METHODS):                  # later = higher priority overwrites
        for fp in tag_files(out_dir, m, tag):
            if not fp.name.endswith((f"_{book}.jsonl", f"_{book}.jsonl.gz")):
                continue
            with _open(fp) as fh:
                for line in fh:
                    if line.strip():
                        r = json.loads(line)
                        for p in r.get("pairs", ()):
                            if p.get("t_idx"):
                                per[(r["chapter"], r["verse"])][p["h_idx"]] = [tuple(x) for x in (p.get("t_parts") or
                                                                                                    [[t, None] for t in p["t_idx"]])]
    out = {}
    for key, links in per.items():
        d: dict = collections.defaultdict(set)
        for u, tps in links.items():
            for t, pt in tps:
                d[(t, pt)].add(u)
        out[key] = dict(d)
    return out


def finish(rows, results: dict, book: str) -> list[dict]:
    """Records of the parts corpus from the components and the answers ({(ref, comp index): {piece index: unit idx | None}})."""
    recs = []
    for wr, pr, cs, ans in rows:
        pairs, skipped, seen = [], [], set()
        by_unit: dict = collections.defaultdict(list)
        prior: dict = {}
        if ans is not None and wr is not None:
            uo = word_units(wr.heb, pr.heb)
            for h in ans["none"]:
                for u in uo.get(h, []):
                    skipped.append({"h_idx": u.idx, "strong": u.strong, "lexeme": u.lexeme, "status": "unrepresented",
                                    "prior": "llm_refine_word_none"}); seen.add(u.idx)
        for ci, c in enumerate(cs):
            if c.how == "one-piece":
                for h in c.words:
                    u = carrier(c.word_units[h])
                    by_unit[u.idx].append(c.pieces[0]); prior[u.idx] = "llm_refine_word"
                continue
            a = results.get((c.ref, ci), c.assign) if c.how == "ask" else c.assign
            if c.how == "ask" and (c.ref, ci) not in results:          # not answered: the word answers, on the carriers
                for h in c.words:
                    u = carrier(c.word_units[h])
                    by_unit[u.idx] += [p for p in c.pieces if p[0] in c.links[h]]; prior[u.idx] = "llm_refine_unanswered"
                continue
            for i, ux in a.items():
                if ux is not None:
                    by_unit[ux].append(c.pieces[i]); prior[ux] = f"llm_refine_{c.how}"
            for h in c.words:                                        # never lose a word-level link
                if not any(u.idx in by_unit for u in c.word_units[h]):
                    u = carrier(c.word_units[h])
                    by_unit[u.idx] += [p for p in c.pieces if p[0] in c.links[h]]; prior[u.idx] = "llm_refine_word_kept"
            if c.how == "ask":
                for u in c.units:
                    if u.idx not in by_unit and u.idx not in seen:
                        skipped.append({"h_idx": u.idx, "strong": u.strong, "lexeme": u.lexeme, "status": "unrepresented",
                                        "prior": "llm_refine_ask"}); seen.add(u.idx)
        units = {u.idx: u for u in pr.heb}
        for ux in sorted(by_unit):
            ps = sorted(set(by_unit[ux]), key=lambda p: (p[0], -1 if p[1] is None else p[1]))
            u = units[ux]
            ts = sorted({p[0] for p in ps})
            pairs.append({"h_idx": ux, "lexeme": u.lexeme, "strong": u.strong, "lemma": u.lemma, "surface": u.surface,
                          "target": " ".join(pr.toks[t] for t in ts), "t_idx": ts, "t_parts": [[p[0], p[1]] for p in ps],
                          "score": 0.9, "method": "llm", "content": bool(u.is_content), "prior": prior[ux]})
        owned = {t for p in pairs for t in p["t_idx"]}
        from lexeme_aligner.refs import encode
        recs.append({"ref": encode(book, pr.ch, pr.v), "book": book, "chapter": pr.ch, "verse": pr.v, "pairs": pairs,
                     "llm_skipped": skipped, "llm_added": [j for j in range(len(pr.toks)) if j not in owned]})
    return recs


def main(argv=None) -> int:
    from lexeme_aligner.eval import jev_check as jc
    from lexeme_aligner.eval import jev_clauses as jv
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["scope", "run", "accept"])
    ap.add_argument("--tag", required=True); ap.add_argument("--publish-iso", default="heb")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--book", action="append")
    ap.add_argument("--base-dir", type=Path); ap.add_argument("--base-cell")
    ap.add_argument("--chain-dir", type=Path, help="run/scope --trust-chain: the parts run's align files; accept: the word view's")
    ap.add_argument("--trust-chain", action="store_true")
    ap.add_argument("--no-target-parts", action="store_true", help="treat every translation word as one piece")
    ap.add_argument("--out-dir", type=Path); ap.add_argument("--variant", default="R1"); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--max-usd", type=float)
    ap.add_argument("--sister-dir", type=Path); ap.add_argument("--sister-cell"); ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    from lexeme_aligner.run_pilot import NT_BOOKS
    books = [b.upper() for b in a.book] if a.book else [b for b in NT_BOOKS if any(a.usj_dir.glob(f"*-{b}.json"))]
    if a.cmd == "accept":
        return accept_main(a, books)
    hp = {}
    if not a.no_target_parts:
        from lexeme_aligner import target_parts as tp
        got = tp.for_edition(a.tag)
        if got:
            hp = tp.load_parts(got[0], got[1].get("min_p_split", 0.9))
    lang = jc.LANG.get(a.publish_iso, a.publish_iso)
    total = collections.Counter()
    est_all = collections.Counter()
    spent = {"tokens": 0}
    for book in books:
        answers = load_answers(a.base_dir, a.base_cell, book)
        if not answers:
            print(f"[jev-refine] {book}: no base answers", file=sys.stderr); continue
        chain = load_chain(a.chain_dir, a.tag, book) if a.trust_chain and a.chain_dir else None
        rows = book_components(a.tag, book, a.usj_dir, answers, hp, chain)
        cnt = collections.Counter()
        asked = []
        for wr, pr, cs, ans in rows:
            for ci, c in enumerate(cs):
                cnt[f"components {c.how}"] += 1
                if c.how == "ask":
                    cnt["questions"] += len(c.pieces); asked.append((ci, c, wr))
        # requests: one per clause unit
        units, sentences = jv.load_units(book)
        unit_of = {k: ui for ui, u in enumerate(units) for k in u["words"]}
        groups: dict = collections.defaultdict(list)
        for ci, c, wr in asked:
            wt = {t.idx: t for t in wr.heb}
            k = (wt[c.words[0]].keys or [""])[0]
            groups[unit_of.get(k, ("verse", c.ref))].append((ci, c))
        word_toks = {(wr.ch, wr.v): wr.heb for wr, *_ in rows if wr is not None}
        toks_of = {(pr.ch, pr.v): pr.toks for _wr, pr, *_ in rows}
        text_of = {(pr.ch, pr.v): pr.text for _wr, pr, *_ in rows}
        jobs = []
        for g, items in groups.items():
            sent = units[g]["sentence"] if isinstance(g, int) else None
            refs = sorted({c.ref for _ci, c in items})
            label = f"{book} {refs[0][0]}:{refs[0][1]}" + (f"–{refs[-1][0]}:{refs[-1][1]}" if len(refs) > 1 else "")
            body, qmap, umap = request_body([c for _ci, c in items], word_toks, toks_of, text_of, sentences.get(sent), lang, label)
            jobs.append((items, body, qmap, umap))
        cnt["requests"] = len(jobs)
        raws = {json.dumps(j[1], ensure_ascii=False, sort_keys=True).encode(): j[1] for j in jobs}
        est = jv.estimate(list(raws), lambda raw: jv._in_cache(raw, raws[raw]))
        est_all.update({k: v for k, v in est.items() if isinstance(v, (int, float))})
        total.update(cnt)
        print(json.dumps({"book": book, **dict(cnt), "estimate": est}, ensure_ascii=False), file=sys.stderr)
        if a.cmd == "scope" or a.dry_run:
            if (dump := os.environ.get("JEV_DUMP_BODIES")) and jobs and book == books[0]:
                Path(dump).write_text(json.dumps([j[1] for j in jobs[:3]], ensure_ascii=False, indent=1), encoding="utf-8")
            continue
        cache_only = bool(os.environ.get("JEV_CACHE_ONLY"))
        if not cache_only and est["requests to send"]:
            if a.max_usd is None:
                raise SystemExit(f"jev_refine: {book} would send {est['requests to send']} requests, about ${est['usd (est., high side)']}; "
                                 "pass --max-usd (or --dry-run)")
            if est_all["usd (est., high side)"] > a.max_usd:
                raise SystemExit(f"jev_refine: estimate so far ${round(est_all['usd (est., high side)'], 2)} is above --max-usd {a.max_usd}")
        key = "" if cache_only else jc.api_key()
        lock = threading.Lock()

        def ask(job):                                      # running cap over the whole run (every book), on billed input
            with lock:
                if a.max_usd is not None and spent["tokens"] / 1e6 * jv.PRICE_PER_M_INPUT >= a.max_usd:
                    return {"_error": "not sent: --max-usd reached"}
            r = jc.ask_split(job[1], key)
            if not r.get("_cached"):
                with lock:
                    spent["tokens"] += (r.get("usage") or {}).get("input_tokens", 0)
            return r
        with ThreadPoolExecutor(a.workers) as ex:
            res = list(ex.map(ask, jobs))
        results: dict = {}
        for (items, body, qmap, umap), r in zip(jobs, res):
            if "_error" in r:
                total["requests failed"] += 1; continue
            for qid, ans in (r.get("answers") or {}).items():
                if qid not in qmap:
                    continue
                c, i = qmap[qid]
                ci = next(ci for ci, cc in items if cc is c)
                ch = ans.get("choice")
                results.setdefault((c.ref, ci), {})[i] = umap[ch][1] if ch in umap else None
        recs = finish(rows, results, book)
        for r in recs:
            r["llm"] = {"provider": "typesafe", "model": jc.MODEL, "theta": 0.0, "design": "refine", "variant": a.variant,
                        "base": a.base_cell}
        a.out_dir.mkdir(parents=True, exist_ok=True)
        cell = f"jevR.{a.tag}.{book}.v4.{a.variant}.jev-0"
        (a.out_dir / f"align_llm_{cell}_{book}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                                                                    encoding="utf-8")
    total["input tokens billed"] = spent["tokens"]
    print(json.dumps({"total": dict(total), "estimate": {k: round(v, 2) for k, v in est_all.items()}}, ensure_ascii=False))
    return 0


# --- accept: another edition's answers where this edition's chain agrees ------------------------------------------------------------
def accept_main(a, books) -> int:
    """{BOOK: {"C:V": {h: [t_idx]}}}: this edition's chain links (word view) whose written target equals the sister's Jev answer."""
    out, c = {}, collections.Counter()
    for book in books:
        sister = {}
        for fp in cell_files(a.sister_dir, a.sister_cell, book):
            with _open(fp) as fh:
                for line in fh:
                    if line.strip():
                        r = json.loads(line)
                        sister[(r["chapter"], r["verse"])] = {p["h_idx"]: p["target"] for p in r.get("pairs", ()) if p.get("t_idx")}
        if not sister:
            continue
        chain: dict = collections.defaultdict(lambda: collections.defaultdict(dict))
        from lexeme_aligner.align_files import tag_files
        for m in CHAIN_METHODS:
            for fp in tag_files(a.chain_dir, m, a.tag):
                if not fp.name.endswith((f"_{book}.jsonl", f"_{book}.jsonl.gz")):
                    continue
                with _open(fp) as fh:
                    for line in fh:
                        if line.strip():
                            r = json.loads(line)
                            for p in r.get("pairs", ()):
                                if p.get("t_idx"):
                                    chain[(r["chapter"], r["verse"])][p["h_idx"]].setdefault(p["target"], p["t_idx"])
        bk = {}
        for key, sv in sister.items():
            for h, target in sv.items():
                c["sister links"] += 1
                ts = chain.get(key, {}).get(h, {}).get(target)
                if ts:
                    bk.setdefault(f"{key[0]}:{key[1]}", {})[str(h)] = ts; c["accepted"] += 1
        out[book] = bk
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"tag": a.tag, "sister_cell": a.sister_cell, "books": out}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(dict(c)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
