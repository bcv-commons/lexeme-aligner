"""Compare alignment layers at the grain of the source WORD, not the MACULA morpheme.

The spine splits a Hebrew word into morphemes (בִּימֵי -> בִּ + ימֵי; אִשְׁתּוֹ -> אִשְׁתּ + וֹ), each with its own token index. Our statistical rows align
morphemes (the prefix "In" on בִּ, "days" on ימֵי); Clear's gold puts a whole phrase ("In the days") on ONE morpheme of the word; the BSB tables
tag whole words. Compared token by token these are disagreements that are not disagreements.

Every MACULA Hebrew node key is `BBCCCVVVWWWM` (book, chapter, verse, word, morpheme): dropping the last digit gives the word (Greek keys are 11 digits,
one per word already). A spine token that holds
several keys (the merged בֵּית לֶחֶם) joins the words it touches into one group. A layer projected to words = the union of the target
positions of every row that touches any token of the word.

    .venv/bin/python -m lexeme_aligner.eval.word_bridge --book RUT
    .venv/bin/python -m lexeme_aligner.eval.word_bridge --show "RUT 1:1"
"""
from __future__ import annotations

import argparse
import collections
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("pipeline/work/pilot/fa-baseline/eng/engbsb")
INDEX = Path("publish/compact-alignments/_index")
USJ = Path("pipeline/work/ingest-cache/usj-engbsb")
STOPWORDS = Path("config/external_stopwords/stopwords-iso.json")
LAYERS = {"statistical": "statistical", "clear": "manual/BSB", "tables": "manual/BSB-tables"}


@dataclass
class Word:
    wid: str                                 # first key without the morpheme digit
    idx: list[int] = field(default_factory=list)       # spine token indices
    content: list[int] = field(default_factory=list)   # the content ones (is_content)
    surface: str = ""


def word_key(key: str) -> str:
    """Hebrew keys are 12 digits (word + morpheme digit); Greek keys are 11 digits and already one per word."""
    return key[:-1] if len(key) == 12 else key


def words_of(tokens) -> list[Word]:
    """Spine tokens of ONE verse -> source words. Tokens sharing a word id, or one token holding keys of two words, form a single word."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    wids_of: dict[int, list[str]] = {}
    for t in tokens:
        w = sorted({word_key(k) for k in (t.keys or [])}) or [f"i{t.idx}"]       # no key: its own word
        wids_of[t.idx] = w
        find(w[0])
        for x in w[1:]:
            union(w[0], x)
    groups: dict[str, Word] = {}
    for t in sorted(tokens, key=lambda t: t.idx):
        root = find(wids_of[t.idx][0])
        w = groups.setdefault(root, Word(wid=min(wids_of[t.idx])))
        w.wid = min(w.wid, *wids_of[t.idx])
        w.idx.append(t.idx)
        if t.is_content:
            w.content.append(t.idx)
        w.surface += (" " if w.surface else "") + t.surface
    return sorted(groups.values(), key=lambda w: w.idx[0])


def _hidx(r: dict) -> set[int]:
    h = r.get("h_idx")
    return {h} if isinstance(h, int) else set(h or [])


def project(rows: list[dict], words: list[Word]) -> dict[str, set[int]]:
    """{word id: union of t_idx of every row touching one of its tokens}. A row spanning several words gives each the same positions
    (a joint row: the layer did not say how to split them)."""
    word_of_idx = {i: w.wid for w in words for i in w.idx}
    out: dict[str, set[int]] = {w.wid: set() for w in words}
    for r in rows:
        for i in _hidx(r):
            wid = word_of_idx.get(i)
            if wid is not None:
                out[wid] |= set(r.get("t_idx") or [])
    return out


def classify(a: set[int], b: set[int]) -> str:
    """How two layers' target positions for one word relate."""
    if not a and not b:
        return "neither"
    if not a or not b:
        return "one_empty"
    if a == b:
        return "equal"
    if a < b:
        return "a_inside_b"
    if b < a:
        return "b_inside_a"
    return "overlap" if a & b else "disjoint"


def token_level(rows: list[dict]) -> dict[int, set[int]]:
    """The old, morpheme grain: {spine token: union of its rows' positions}."""
    out: dict[int, set[int]] = collections.defaultdict(set)
    for r in rows:
        for i in _hidx(r):
            out[i] |= set(r.get("t_idx") or [])
    return out


def target_function_positions(book: str, ch: int, v: int, vmap: dict, stop: set[str]) -> tuple[list[str], set[int]]:
    """(target words, positions of its function words) for the edition verse holding spine verse `book ch:v` (eng edition; stopwords-iso list)."""
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    from lexeme_aligner.usj_source import read_verse_ranges, strip_marks, tokenize
    tref = vmap.get(f"{book} {ch}:{v}", f"{book} {ch}:{v}")
    tch, tv = map(int, tref.split()[1].split(":"))
    fp = USJ / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    rng = read_verse_ranges(fp, rules={})
    if (tch, tv) not in rng:
        return [], set()
    toks = tokenize(rng[(tch, tv)]["text"])
    return toks, {i for i, w in enumerate(toks) if strip_marks(w).lower() in stop}


def classify_core(a: set[int], b: set[int], fn: set[int]) -> str:
    """classify() after both sides drop the target's function words: 'the' / 'of' placement is a convention, not a content disagreement."""
    return classify(a - fn, b - fn)


def compare(a: dict, b: dict, words: list[Word]) -> collections.Counter:
    """Word-level relation counts between two projections."""
    return collections.Counter(classify(a[w.wid], b[w.wid]) for w in words)


def compare_tokens(a: dict[int, set[int]], b: dict[int, set[int]], idxs: list[int]) -> collections.Counter:
    return collections.Counter(classify(a.get(i, set()), b.get(i, set())) for i in idxs)


def load_layer(name: str, book: str, root: Path = ROOT) -> dict[str, list[dict]]:
    import pyarrow.parquet as pq
    p = root / LAYERS[name] / f"{book}.parquet"
    by_verse: dict[str, list[dict]] = collections.defaultdict(list)
    if not p.exists():
        return by_verse
    for r in pq.read_table(p, columns=["book", "chapter", "verse", "h_idx", "t_idx"]).to_pylist():
        if r["t_idx"] is not None and _hidx(r):
            by_verse[f"{r['book']} {r['chapter']}:{r['verse']}"].append(r)
    return by_verse


def fmt(positions: set[int], toks: list[str]) -> str:
    return " ".join(toks[p] for p in sorted(positions) if p < len(toks)) or "-"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", action="append", help="repeatable; default RUT")
    ap.add_argument("--show", metavar="'RUT 1:1'", help="print one verse word by word")
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = HebrewSource()
    books = a.book or ([a.show.split()[0]] if a.show else ["RUT"])
    import json
    vmap = json.loads((INDEX / "_versification_eng.json").read_text(encoding="utf-8"))["map"]
    stop = set(json.loads(STOPWORDS.read_text(encoding="utf-8")).get("en", []))
    core_tot = {p: collections.Counter() for p in [("statistical", "clear"), ("statistical", "tables"), ("clear", "tables")]}
    pairs = [("statistical", "clear"), ("statistical", "tables"), ("clear", "tables")]
    totals = {p: [collections.Counter(), collections.Counter()] for p in pairs}      # [word grain, morpheme grain]
    for book in books:
        layer = {n: load_layer(n, book, a.root) for n in LAYERS}
        for ref in sorted(set().union(*layer.values())):
            ch, v = map(int, ref.split()[1].split(":"))
            toks = heb.verse_tokens(book, ch, v)
            if not toks:
                continue
            words = words_of(toks)
            proj = {n: project(layer[n].get(ref, []), words) for n in LAYERS}
            tok = {n: token_level(layer[n].get(ref, [])) for n in LAYERS}
            idxs = [t.idx for t in toks]
            _toks, fn = target_function_positions(book, ch, v, vmap, stop)
            for p in pairs:
                core_tot[p].update(classify_core(proj[p[0]][w.wid], proj[p[1]][w.wid], fn) for w in words)
                totals[p][0] += compare(proj[p[0]], proj[p[1]], words)
                totals[p][1] += compare_tokens(tok[p[0]], tok[p[1]], idxs)
            if a.show == ref:
                _show(ref, toks, words, proj)
    for p, (wc, tc) in totals.items():
        print(f"\n{p[0]} vs {p[1]}")
        for lab, c in (("word", wc), ("word, no fn", core_tot[p]), ("morpheme", tc)):
            n = sum(v for k, v in c.items() if k != "neither")
            both = c["equal"] + c["a_inside_b"] + c["b_inside_a"] + c["overlap"] + c["disjoint"]
            print(f"  {lab:<11} units judged {n:>6}  equal {c['equal']/max(n,1):6.1%}  nested {(c['a_inside_b']+c['b_inside_a'])/max(n,1):6.1%}  "
                  f"overlap {c['overlap']/max(n,1):6.1%}  disjoint {c['disjoint']/max(n,1):6.1%}  one-empty {c['one_empty']/max(n,1):6.1%}"
                  f"   (equal among both-present: {c['equal']/max(both,1):.1%})")
    return 0


def _show(ref, toks, words, proj):
    print(f"\n{ref}: {len(words)} words from {len(toks)} spine tokens")
    for w in words:
        row = "  ".join(f"{n}:{sorted(proj[n][w.wid])}" for n in LAYERS)
        print(f"  {w.wid[-3:]} {w.surface:<18} idx {w.idx}  {row}")


if __name__ == "__main__":
    raise SystemExit(main())
