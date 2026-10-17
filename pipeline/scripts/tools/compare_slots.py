"""Compare dropped-word slots and added words of two full-alignment layers (written for the joint test with bsb-add-books, 2026-10-16).

Reads ONLY what a client reads: the `main` / `fn` strings of each book (layer version 3 decision lists) and the shared `_index/`
(source order = key order). No spine, no text. So it doubles as a reference reader for the notation.

Two modes:
  exact   both layers are alignments of the SAME text (e.g. an agent tagging the BSB English vs our eng_BSB+manual+bsb-tables):
          for every source token both call "no target word" (`!`/`u`/`e`): is the slot the same? for every added target word: added
          in both, and for the same source token? plus link agreement per token (same positions) and per Hebrew WORD (the union of
          its tokens' links: the BSB tables link whole words, an agent may link token by token), with precision/recall over
          (word, position) pairs and a recall that also counts b's added words (a whole-word layer folds such words into its phrase).
  anchor  different texts (a Danish draft vs the BSB tables): slots are compared by the source token they attach to — the token
          the slot follows (its shown positions end at the slot) or, failing that, the token it precedes. Word-order differences
          between the languages count as disagreements, so read it as a rough check.

    .venv/bin/python pipeline/scripts/tools/compare_slots.py --mode exact \\
        --a publish/full-alignments-manual/e/eng/eng_BSB+manual+bsb-tables --b <their layer dir> [--book RUT ...] [--json out.json]

Self-contained (standard library only). Outside this repo pass `--index <dir>` = a local copy of compact-alignments' `_index/`
(the `<BOOK>_lexemes.json`, `_keys.json` and `_fn_keys.json` of the books compared).
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

INDEX = Path("publish/compact-alignments/_index")
WITH_ADDED = "word pairs of a: in b's links or b's added words"


# --- a string-level reader of one verse -------------------------------------------------------------------------------------------
def dec_span(s: str) -> list[int]:
    if s in ("~", "!"):
        return []
    s = s[1:] if s.startswith("L") else s
    out: list[int] = []
    for p in s.split(","):
        a, _, b = p.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def dec_items(s: str, sep: str) -> list[tuple[int, str | None]]:
    out = []
    for p in s.split(","):
        if sep in p:
            n, x = p.split(sep, 1)
            out.append((int(n), x))
        else:
            a, _, b = p.partition("-")
            out += [(n, None) for n in range(int(a), int(b or a) + 1)]
    return out


def read_verse(main: str, fn: str, order: dict) -> dict:
    """{'links': {slot: set}, 'none': {slot: (kind, slot_pos)}, 'added': {pos: owner slot | None}} for one verse. `order` =
    {("c"|"f", ord): source position}. Views (profile `.`) count like claimed entries: a reader of the strings sees them."""
    links: dict = {}
    none: dict = {}
    added: dict = {}
    explicit: set = set()                                        # slots written with `@` (the rest are at the default slot)
    pending = []
    for kind, s in (("c", main), ("f", fn)):
        for e in s.split():
            h = e[0]
            if h.isdigit():
                o, sp = e.split(":")
                links[(kind, int(o))] = set(dec_span(sp))
            elif h in "!ue":
                for n, x in dec_items(e[1:], "@"):
                    if h == "!":
                        none[(kind, n)] = ("!", None)
                    elif x is None:
                        pending.append(((kind, n), h))
                    else:
                        none[(kind, n)] = (h, int(x))
                        explicit.add((kind, n))
            elif h == "+":
                for p, own in dec_items(e[1:], ">"):
                    added[p] = None if own is None else (("f", int(own[1:])) if own.startswith("f") else ("c", int(own)))
    shown = {s: set(p) for s, p in links.items()}
    for p, own in added.items():
        if own is not None:
            shown.setdefault(own, set()).add(p)
    for slot, kind in pending:                                   # the default slot rule (full-alignments-manual card)
        h = order[slot]
        prev = [s for s, p in shown.items() if p and order.get(s, h) < h]
        none[slot] = (kind, max(shown[max(prev, key=lambda s: order[s])]) if prev else -1)
    return {"links": links, "none": none, "added": added, "shown": shown, "explicit": explicit}


def read_book(layer: Path, book: str) -> dict[str, dict]:
    mains = [f for f in layer.glob(f"{book}_*.json") if f.name.count(".") == 1]
    if len(mains) != 1:
        return {}
    main = json.loads(mains[0].read_text(encoding="utf-8"))
    mp = mains[0].with_name(mains[0].stem + ".meta.json")
    fn = (json.loads(mp.read_text(encoding="utf-8")).get("fn") if mp.exists() else None) or [""] * len(main)
    refs = list(json.loads((INDEX / f"{book}_lexemes.json").read_text(encoding="utf-8")))
    keys = json.loads((INDEX / f"{book}_keys.json").read_text(encoding="utf-8"))
    fkeys = json.loads((INDEX / f"{book}_fn_keys.json").read_text(encoding="utf-8"))
    out = {}
    for i, ref in enumerate(refs):
        if not main[i] and not fn[i]:
            continue
        order = {("c", n): k for n, k in enumerate(keys.get(ref, []))}
        order.update({("f", n): k for n, k in enumerate(fkeys.get(ref, []))})
        out[ref] = read_verse(main[i] or "", fn[i] or "", order)
        out[ref]["order"] = order
    return out


# --- comparisons --------------------------------------------------------------------------------------------------------------
def anchor_of(v: dict, slot_pos: int):
    """The source token a slot attaches to: the one whose shown positions end at the slot (it follows that token), else the one
    whose positions start right after it (it precedes that token); ('start', None) for -1 with nothing at 0."""
    after = [s for s, p in v["shown"].items() if p and max(p) == slot_pos]
    if after:
        return ("after", min(after, key=lambda s: v["order"][s]))
    before = [s for s, p in v["shown"].items() if p and min(p) == slot_pos + 1]
    if before:
        return ("before", min(before, key=lambda s: v["order"][s]))
    return ("none", None)


def _word_spans(v: dict) -> dict:
    """{word key: set of positions} — the union of the links of a Hebrew word's tokens."""
    out: dict = collections.defaultdict(set)
    for slot, pos in v["links"].items():
        k = v["order"].get(slot)
        if k is not None and pos:
            out[k[:-1]] |= pos
    return dict(out)


def compare(a: dict, b: dict, mode: str) -> collections.Counter:
    t: collections.Counter = collections.Counter()
    for ref in sorted(set(a) & set(b)):
        va, vb = a[ref], b[ref]
        t["verses compared"] += 1
        both = set(va["none"]) & set(vb["none"])
        t["source tokens with no target word: in a"] += len(va["none"])
        t["source tokens with no target word: in b"] += len(vb["none"])
        t["...: in both"] += len(both)
        t["...: no target word in a, linked in b"] += len(set(va["none"]) & set(vb["links"]))
        t["...: no target word in b, linked in a"] += len(set(vb["none"]) & set(va["links"]))
        for s in both:
            ka, pa = va["none"][s]
            kb, pb = vb["none"][s]
            if pa is None or pb is None:                         # `!` has no slot
                continue
            t["slots compared"] += 1
            how = {(True, True): "both explicit", (False, False): "both default"}.get(
                (s in va["explicit"], s in vb["explicit"]), "explicit in a only" if s in va["explicit"] else "explicit in b only")
            t[f"slots compared: {how}"] += 1
            if mode == "exact":
                eq = pa == pb
                t["slots equal"] += eq
            else:
                eq = anchor_of(va, pa) == anchor_of(vb, pb)
                t["slot anchors equal"] += eq
            t[f"slots equal: {how}"] += eq
        if mode == "exact":
            # per Hebrew WORD (tokens whose _index keys share all but the last digit = the morphemes of one word): the union of the
            # word's links, so a whole-word layer (BSB tables) and a token-by-token one compare on the same unit
            wa, wb = _word_spans(va), _word_spans(vb)
            for w in set(wa) & set(wb):
                t["words linked in both"] += 1
                t["words with the same positions"] += wa[w] == wb[w]
                t["word pairs (word, position): in a"] += len(wa[w])
                t["word pairs (word, position): in b"] += len(wb[w])
                t["word pairs: in both"] += len(wa[w] & wb[w])
                t[WITH_ADDED] += len(wa[w] & (wb[w] | set(vb["added"])))
            common = set(va["links"]) & set(vb["links"])
            t["links compared (token linked in both)"] += len(common)
            t["links with the same positions"] += sum(va["links"][s] == vb["links"][s] for s in common)
            aa, ab = va["added"], vb["added"]
            t["added words: in a"] += len(aa)
            t["added words: in b"] += len(ab)
            t["added words: in both"] += len(set(aa) & set(ab))
            t["added words in both: same owner"] += sum(aa[p] == ab[p] for p in set(aa) & set(ab))
    return t


def main(argv=None) -> int:
    global INDEX
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", type=Path, required=True, help="layer dir A (e.g. ours, the BSB tables)")
    ap.add_argument("--b", type=Path, required=True, help="layer dir B (e.g. theirs)")
    ap.add_argument("--mode", choices=("exact", "anchor"), required=True)
    ap.add_argument("--book", action="append", help="books (default: every book both layers have)")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--index", type=Path, default=INDEX, help="compact-alignments _index/ directory (default: this repo's copy)")
    args = ap.parse_args(argv)
    INDEX = args.index
    books = args.book or sorted({f.name.split("_")[0] for f in args.a.glob("*_*.json")} & {f.name.split("_")[0] for f in args.b.glob("*_*.json")})
    t: collections.Counter = collections.Counter()
    for book in books:
        t.update(compare(read_book(args.a, book), read_book(args.b, book), args.mode))
    for k, v in t.items():
        print(f"{k:55s} {v:>8,}")
    rate = lambda n, d: f"{t[n] / t[d]:.1%}" if t[d] else "-"           # noqa: E731
    if args.mode == "exact":
        print(f"slots equal: {rate('slots equal', 'slots compared')} · links equal: "
              f"{rate('links with the same positions', 'links compared (token linked in both)')} · added owners equal: "
              f"{rate('added words in both: same owner', 'added words: in both')}")
        print(f"per Hebrew word: same positions {rate('words with the same positions', 'words linked in both')} · "
              f"precision of b {rate('word pairs: in both', 'word pairs (word, position): in b')} · "
              f"recall of a {rate('word pairs: in both', 'word pairs (word, position): in a')} · "
              f"recall counting b's added words {rate(WITH_ADDED, 'word pairs (word, position): in a')}")
    else:
        print(f"slot anchors equal: {rate('slot anchors equal', 'slots compared')}")
    print("slots by how they were written (`@` explicit or the default slot): " + " · ".join(
        f"{h} {t[f'slots equal: {h}']}/{t[f'slots compared: {h}']}" for h in
        ("both explicit", "explicit in a only", "explicit in b only", "both default") if t[f"slots compared: {h}"]))
    if args.json:
        args.json.write_text(json.dumps({"mode": args.mode, "books": books, "counts": dict(t)}, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
