"""E6 (internal-docs/aim1-three-track-evaluation-plan.md §2.6b): gold-free VERIFICATION of span_extension
via the language's OTHER editions.

Every edition aligns to the same Hebrew/Greek token, so the other editions' verse TEXT is a set of
independent renderings of the same source token. span_extension attaches a function word next to a span
(before/after); if that word is really the source token's function word, the other translations should
show a function word in the same place. For each spanext-added word `w` on side `d` of test edition T's
span for source token `k`, and each reference edition R with a high-confidence base alignment for `k`:
is `w` the token immediately adjacent (side `d`) to R's own span for `k`, in R's own verse tokens?

`support` = share of (added word, reference) pairs where the answer is yes. Because a very frequent
particle sits next to many spans by chance, every pair is also scored against a NULL: how often `w` sits
on side `d` of R's span for OTHER source tokens in the same reference verse. `lift = support / null`.

Why the reference is TEXT adjacency and not the reference editions' alignments (pilot v1, 2026-09-29, kept
in the plan doc): the other editions' base ALIGNMENTS share the statistical layers' systematic
under-widening — the exact deficiency span_extension repairs — so any widening disagrees with them by
construction (hin length-agreement .894 -> .078 even though spanext is a gold-verified win there).
References here are read from the base layers (eflomal+gloss+gapfill, score >= .9) only for WHICH R token
renders `k`; the widening itself is judged by what the reference TEXT shows next to it.

The side of each added position is derived GEOMETRICALLY (position below the base span's minimum =
"before", above its maximum = "after"), not from the `prior` label, so it works on spanext files written
before/after the 2026-09-28 `label:position` format change.

    python3 -m lexeme_aligner.eval.xedition_verify --iso hin --tag hinirv --ref hin_cvb --ref hinwtc
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import dataclasses
import gzip
import io
import json
import sys
import unicodedata
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT

HI_CONF = 0.9
NULL_SAMPLES = 8
DUP_MAX = 0.5          # a reference sharing >= this fraction of IDENTICAL verses with the test edition is a
                       # near-duplicate text: agreement with it is trivially high, so it is excluded
# Verdict thresholds on `support` (absolute share of added words a sibling edition's text corroborates).
# Calibrated 2026-09-29 against the 9 gold-judged editions: gold-NEGATIVE spanext (marglt .215, telglt .245,
# ben_irv .270, fin_helfi .301) all sit at or below .30; gold-POSITIVE (arb_vdv .472, hinirv .485, spa_r09
# .496, engbsb .556, fra-lsg .703) all sit at or above .47. Nothing measured lies between .30 and .47, so the
# FLIP/KEEP cut-offs are placed inside that gap and everything between is "uncertain" (status quo kept).
FLIP_BELOW = 0.32
KEEP_AT_LEAST = 0.40
MIN_PAIRS = 300        # fewer compared (word, reference) pairs than this -> no verdict
BASE_METHODS = ("eflomal", "gloss")            # exactly what span_extension reads its pairs from
REF_METHODS = ("eflomal", "gloss", "gapfill")  # what counts as a reference edition's rendering


def load_layers(tag: str, methods, out_dir: Path = OUT) -> dict[tuple[int, int], dict]:
    """First-wins union per (ref, h_idx) of the given methods' CONTENT pairs that carry a target span."""
    d: dict[tuple[int, int], dict] = {}
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            op = gzip.open if str(fp).endswith(".gz") else open
            with op(fp, "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    for p in r.get("pairs", []):
                        if not (p.get("content") and p.get("t_idx")):
                            continue
                        d.setdefault((r["ref"], p["h_idx"]), dict(p, _method=m))
    return d


def load_verse_tokens(tag: str, usj_dir: Path, heb=None, books=None) -> dict[int, list[str]]:
    """{encoded source ref: lowercased target tokens} exactly as the aligner saw them (pooled ranges,
    versification remap, strip rules) — via run_pilot.build_corpus, so positions line up with `t_idx`."""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, build_corpus
    from lexeme_aligner.versification import remapper

    heb = heb or HebrewSource()
    with contextlib.redirect_stderr(io.StringIO()):          # build_corpus prints one line per missing book
        recs = build_corpus(books or (OT_BOOKS + NT_BOOKS), Path(usj_dir), heb,
                            remap=remapper(tag, str(usj_dir)))
    return {encode(r.book, r.ch, r.v): [t.lower() for t in r.toks] for r in recs}


def dominant_script(toks_by_ref: dict[int, list[str]], sample: int = 400) -> str:
    """Most common Unicode script name-prefix among the first letters of up to `sample` tokens
    ('LATIN', 'DEVANAGARI', 'KANNADA', ...). Token EQUALITY across scripts is meaningless (Sanskrit is
    published in 20 regional scripts), so references must share the test edition's script."""
    c: collections.Counter = collections.Counter()
    n = 0
    for toks in toks_by_ref.values():
        for t in toks:
            for ch in t:
                if ch.isalpha():
                    try:
                        c[unicodedata.name(ch).split()[0]] += 1
                    except ValueError:
                        pass
                    break
            n += 1
            if n >= sample:
                return c.most_common(1)[0][0] if c else ""
    return c.most_common(1)[0][0] if c else ""


def identical_verse_rate(a: dict[int, list[str]], b: dict[int, list[str]]) -> float:
    shared = [r for r in a if r in b and a[r] and b[r]]
    return sum(a[r] == b[r] for r in shared) / len(shared) if shared else 0.0


def reference_ok(test_toks: dict[int, list[str]], ref_toks: dict[int, list[str]]) -> tuple[bool, str]:
    """(usable, reason) — same script as the test edition and not a near-duplicate text of it."""
    if dominant_script(ref_toks) != dominant_script(test_toks):
        return False, "script"
    if identical_verse_rate(test_toks, ref_toks) >= DUP_MAX:
        return False, "duplicate"
    return True, ""


@dataclasses.dataclass
class RefEdition:
    tag: str
    toks: dict[int, list[str]]                     # ref -> tokens
    by_ref: dict[int, dict[int, tuple[int, ...]]]  # ref -> {h_idx: hi-conf span}


def make_reference(tag: str, toks: dict[int, list[str]], layers: dict) -> RefEdition:
    by_ref: dict[int, dict[int, tuple[int, ...]]] = collections.defaultdict(dict)
    for (ref, h_idx), p in layers.items():
        if (p.get("score") or 0) >= HI_CONF:
            by_ref[ref][h_idx] = tuple(sorted(p["t_idx"]))
    return RefEdition(tag, toks, by_ref)


def added_positions(base_span, ext_span) -> list[tuple[str, int]]:
    """[(side, position)] for every position span_extension ADDED, side derived from geometry."""
    base = sorted(base_span)
    lo, hi = base[0], base[-1]
    out = []
    for pos in sorted(set(ext_span) - set(base)):
        if pos < lo:
            out.append(("before", pos))
        elif pos > hi:
            out.append(("after", pos))
    return out


def neighbor(span, side: str) -> int:
    return (max(span) + 1) if side == "after" else (min(span) - 1)


@dataclasses.dataclass
class Stats:
    tokens: int = 0            # spanext-touched source tokens evaluated (>=1 usable reference)
    pairs: int = 0             # (added word, reference) comparisons
    hits: int = 0
    null_pairs: int = 0
    null_hits: int = 0
    by_side: dict = dataclasses.field(default_factory=lambda: collections.defaultdict(lambda: [0, 0]))

    @property
    def support(self) -> float:
        return self.hits / self.pairs if self.pairs else float("nan")

    @property
    def null(self) -> float:
        return self.null_hits / self.null_pairs if self.null_pairs else float("nan")

    @property
    def lift(self) -> float:
        n = self.null
        return self.support / n if n and n == n and n > 0 else float("nan")

    def merge(self, other: "Stats") -> None:
        self.tokens += other.tokens
        self.pairs += other.pairs
        self.hits += other.hits
        self.null_pairs += other.null_pairs
        self.null_hits += other.null_hits
        for s, (h, n) in other.by_side.items():
            self.by_side[s][0] += h
            self.by_side[s][1] += n

    def row(self) -> dict:
        return {"tokens": self.tokens, "pairs": self.pairs, "support": round(self.support, 4),
                "null": round(self.null, 4), "lift": round(self.lift, 3),
                "by_side": {s: f"{h}/{n}" for s, (h, n) in self.by_side.items()}}


def evaluate(base: dict, ext: dict, test_toks: dict[int, list[str]], refs: list[RefEdition],
             null_samples: int = NULL_SAMPLES) -> Stats:
    """Judge every spanext-widened token in `ext` (vs its un-widened pair in `base`) against `refs`."""
    st = Stats()
    for key, e in ext.items():
        if e.get("_method") != "spanext" or key not in base:
            continue
        ref, h_idx = key
        ttoks = test_toks.get(ref)
        if not ttoks:
            continue
        used = False
        for side, pos in added_positions(base[key]["t_idx"], e["t_idx"]):
            if pos >= len(ttoks):
                continue
            w = ttoks[pos]
            for R in refs:
                span = R.by_ref.get(ref, {}).get(h_idx)
                rtoks = R.toks.get(ref)
                if not span or not rtoks:
                    continue
                nb = neighbor(span, side)
                if not 0 <= nb < len(rtoks):
                    continue
                used = True
                st.pairs += 1
                st.by_side[side][1] += 1
                if rtoks[nb] == w:
                    st.hits += 1
                    st.by_side[side][0] += 1
                others = [sp for h, sp in sorted(R.by_ref[ref].items()) if h != h_idx][:null_samples]
                for osp in others:
                    onb = neighbor(osp, side)
                    if 0 <= onb < len(rtoks):
                        st.null_pairs += 1
                        st.null_hits += (rtoks[onb] == w)
        st.tokens += used
    return st


def verify_edition(test_tag: str, ref_tags: list[str], ingest_root: Path, out_dir: Path = OUT,
                   heb=None, tok_cache: dict | None = None,
                   excluded: dict | None = None) -> Stats:
    """Full pipeline for one test edition against the given reference editions (all on disk). References
    failing `reference_ok` (other script / near-duplicate text) are dropped; if `excluded` is a dict it
    receives {ref_tag: reason}."""
    from lexeme_aligner.hebrew_source import HebrewSource
    heb = heb or HebrewSource()
    tok_cache = tok_cache if tok_cache is not None else {}

    def toks_of(tag):
        if tag not in tok_cache:
            tok_cache[tag] = load_verse_tokens(tag, ingest_root / f"usj-{tag}", heb)
        return tok_cache[tag]

    base = load_layers(test_tag, BASE_METHODS, out_dir)
    ext = load_layers(test_tag, ("spanext",) + BASE_METHODS, out_dir)
    ttoks = toks_of(test_tag)
    refs = []
    for t in ref_tags:
        ok, why = reference_ok(ttoks, toks_of(t))
        if not ok:
            if excluded is not None:
                excluded[t] = why
            continue
        refs.append(make_reference(t, toks_of(t), load_layers(t, REF_METHODS, out_dir)))
    return evaluate(base, ext, ttoks, refs)


def verdict(support: float, pairs: int) -> str:
    """'keep' | 'flip' | 'uncertain' | 'insufficient' for one edition's E6 result (see the threshold notes)."""
    if pairs < MIN_PAIRS or support != support:
        return "insufficient"
    if support < FLIP_BELOW:
        return "flip"
    return "keep" if support >= KEEP_AT_LEAST else "uncertain"


E6_FILE = Path("config/carryover/e6_spanext.json")
FLAGS_FILE = Path("config/spanext_flags.json")


def record(iso: str, tag: str, refs: list[str], row: dict, excluded: dict, date: str, path: Path = E6_FILE) -> dict:
    """Merge one edition's E6 result into the evidence file (the one writer of config/carryover/e6_spanext.json)."""
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"_doc": "E6 cross-edition verification "
                                                                          "(xedition_verify.py --record)", "editions": {}}
    doc["thresholds"] = {"flip_below": FLIP_BELOW, "keep_at_least": KEEP_AT_LEAST, "min_pairs": MIN_PAIRS}
    entry = {"iso": iso, "refs": [r for r in refs if r not in excluded], "excluded": excluded, "date": date,
             **{k: row[k] for k in ("pairs", "support", "null", "lift")}, "verdict": verdict(row["support"], row["pairs"])}
    doc.setdefault("editions", {})[tag] = entry
    doc["editions"] = dict(sorted(doc["editions"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def apply_flag(tag: str, entry: dict, path: Path = FLAGS_FILE) -> str:
    """Mirror a verdict into config/spanext_flags.json: 'flip' -> per-edition base_mechanisms=false (with the evidence in
    `_note`); 'keep' -> remove a previous E6 flip for this edition; anything else leaves the file alone. Returns what it did."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    cur = doc.get(tag)
    if entry["verdict"] == "flip":
        doc[tag] = {"base_mechanisms": False,
                    "_note": (f"E6 {entry['date']}: only {100 * entry['support']:.1f}% of spanext-added words corroborated by "
                              f"{len(entry['refs'])} sibling edition(s) ({entry['pairs']} comparisons, null {100 * entry['null']:.1f}%); "
                              f"below the gold-calibrated flip cut-off {FLIP_BELOW}. Always-on mechanisms skipped for this edition. "
                              "Evidence: config/carryover/e6_spanext.json. Revert by deleting this entry.")}
        did = "flipped"
    elif entry["verdict"] == "keep" and isinstance(cur, dict) and cur.get("base_mechanisms") is False \
            and str(cur.get("_note", "")).startswith("E6 "):
        del doc[tag]
        did = "reverted an earlier E6 flip"
    else:
        return "unchanged"
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return did


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--tag", required=True, help="the TEST edition (its spanext files are judged)")
    ap.add_argument("--ref", action="append", required=True, help="reference edition tag (repeatable)")
    ap.add_argument("--ingest-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--record", action="store_true", help=f"merge the result into {E6_FILE}")
    ap.add_argument("--apply-flags", action="store_true", help=f"with --record: mirror the verdict into {FLAGS_FILE}")
    a = ap.parse_args(argv)
    excluded: dict = {}
    st = verify_edition(a.tag, a.ref, a.ingest_root, a.out, excluded=excluded)
    row = st.row()
    print(json.dumps({"iso": a.iso, "test": a.tag, "refs": a.ref, "excluded": excluded, **row,
                      "verdict": verdict(st.support, st.pairs)}, indent=1), file=sys.stdout)
    if a.record:
        import datetime
        entry = record(a.iso, a.tag, a.ref, row, excluded, datetime.date.today().isoformat())
        print(f"[xedition_verify] recorded {a.tag}: {entry['verdict']}", file=sys.stderr)
        if a.apply_flags:
            print(f"[xedition_verify] spanext_flags: {apply_flag(a.tag, entry)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
