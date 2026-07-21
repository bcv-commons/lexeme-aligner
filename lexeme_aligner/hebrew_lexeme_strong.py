"""Traditional (Clear-preferred) Strong's numbers for Hebrew LEXEMES our own spine's bare-Strong's
rollup merges with a different, distinct lexeme.

THE ISSUE: unlike the Greek case (greek_morph_strong.py — one lemma, several morphological FORMS),
this is a LEXEME-identity issue: our spine's "Hebrew equivalence-canonicalization" rollup (see
PROVENANCE.txt — done for parity with the older spine.db) sometimes merges two genuinely DISTINCT
MACULA lexemes onto one bare Strong's number, and doesn't always pick the number Clear's gold
actually prefers. Two failure shapes, found by checking every bare Hebrew strong with >1 lexeme
rolled in whose constituent lexemes have DIFFERENT base numbers (not just augment-letter variants
of the same number, which IS the intended/correct rollup — only 8 such cases exist total):
  - WRONG DIRECTION (fixed here): e.g. lexeme hbo:4714 (מִצְרַיִם, "Egypt") rolls to bare strong 4713,
    but Clear's own gold uses H4714 for "Egypt" 1,633:55 over H4713 — our spine picked the minority
    number. Same shape for hbo:8055/hbo:8056 ("rejoice": Clear 440+175 vs 62).
  - GENUINE SPLIT NEEDED, not fixed here (both numbers are heavily, near-equally used by Clear — e.g.
    H0853 direct-object-marker vs H0854 "with" 1,570:1,636 — this isn't a "wrong pick", the rollup
    itself loses real information; would need per-occurrence disambiguation, not a static table).
  - KNOWN INTENTIONAL, not touched: hbo:3068/hbo:3069 (YHWH/Adonai reading tradition) — documented in
    earlier session notes as a deliberate merge, and low-impact (Clear itself uses H3069 only ~3% of
    the time).

THE FIX: unlike Greek, no external morphology bridge is needed — our own pairs already carry
`lexeme` directly (e.g. "hbo:4714"), which the bare `strong` rollup discards. Build a small table
by cross-referencing OUR OWN produced target surfaces (grouped by their true lexeme) against Clear's
gold surface->strong, using eng (our largest OT gold set) as the primary source — the underlying fact
(this Hebrew lexeme's traditional Strong's number) is a property of the SOURCE text, not the target
language, so one table applies everywhere.

Table stored at data/hebrew_lexeme_strong.json (small, committed). Applied ONLY when comparing
against Clear gold (benchmark.py) — never written back into the published `lexeme`/`strong` fields
(docs/publishing-principles.md §2: strong is a bridge, lexeme is the anchor of record).

    python3 -m lexeme_aligner.hebrew_lexeme_strong --build
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_TABLE_PATH = Path("data/hebrew_lexeme_strong.json")
_MIN_N = 10
_MIN_PURITY = 0.90


def _candidate_lexemes(spine_db: Path) -> set[str]:
    """The narrow, VERIFIED anomaly scope: bare Hebrew strongs whose rolled-up lexemes have
    genuinely DIFFERENT base numbers (not augment-letter variants of the same number, which is the
    correct/intended rollup — checked session-time: only 8 such bare-strong groups exist at all).
    Restricting to these avoids the broad-sweep noise a blanket lexeme scan produces (low-frequency
    lexemes picking up spurious matches to common words from our own alignment's occasional errors,
    including nonsense cross-testament Hebrew-lexeme-to-Greek-strong "matches")."""
    import sqlite3, re
    db = sqlite3.connect(str(spine_db))
    cur = db.execute("SELECT DISTINCT lexeme, strong FROM spine_words WHERE lexeme LIKE 'hbo:%'")
    by_strong: dict[int, set] = collections.defaultdict(set)
    for lexeme, strong in cur.fetchall():
        by_strong[strong].add(lexeme)

    def base_num(lex):
        m = re.match(r"hbo:(\d+)", lex)
        return int(m.group(1)) if m else None

    lexemes = set()
    for s, lxs in by_strong.items():
        bases = {base_num(lx) for lx in lxs}
        bases.discard(None)
        if len(bases) > 1:
            lexemes |= lxs
    return lexemes


def build_table(out_dir: Path = Path("out"), gold_path: Path = Path("data/resources/strongs/attestations/eng.parquet"),
                iso: str = "eng", tag: str = "merged", min_n: int = _MIN_N, min_purity: float = _MIN_PURITY,
                spine_db: Path = Path("data/lexeme-spine.db")) -> dict:
    import pyarrow.parquet as pq
    from lexeme_aligner.benchmark import norm_surface

    candidates = _candidate_lexemes(spine_db)

    # Clear's OWN DOMINANT strong per surface (one vote per surface, not raw counts) — using raw
    # counts here would let a common ambiguous target word (e.g. "and") flood the tally for every
    # unrelated lexeme that happens to ever produce it, since Clear's volume for "and" is huge.
    gold_rows = pq.read_table(gold_path, columns=["surface", "strong"]).to_pylist()
    gold_counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in gold_rows:
        s = norm_surface(r["surface"])
        if s:
            gold_counts[s][r["strong"]] += 1
    gold_dominant = {s: c.most_common(1)[0][0] for s, c in gold_counts.items()}

    # Weight by OUR OWN occurrence frequency of (lexeme, surface) — one vote per time WE produced
    # this surface for this lexeme, not per Clear row — so a lexeme's genuinely frequent, recurring
    # rendering dominates over incidental overlap with a common word.
    by_lexeme: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    lexeme_own_strong: dict[str, str] = {}
    for fp in sorted(out_dir.glob(f"align_{tag}_{iso}_*.jsonl")):
        for line in fp.open(encoding="utf-8"):
            rec = json.loads(line)
            for p in rec["pairs"]:
                lexeme = p.get("lexeme") or ""
                if not (p.get("content") and lexeme in candidates):
                    continue
                tgt = (p.get("target") or "").strip()
                if not tgt or " " in tgt:
                    continue
                s = norm_surface(tgt)
                if not s or s not in gold_dominant:
                    continue
                lexeme_own_strong[lexeme] = p.get("strong")
                by_lexeme[lexeme][gold_dominant[s]] += 1

    table = {}
    for lexeme, counter in by_lexeme.items():
        total = sum(counter.values())
        top_strong, top_n = counter.most_common(1)[0]
        own = lexeme_own_strong.get(lexeme)
        if (total >= min_n and (top_n / total) >= min_purity and top_strong != own
                and top_strong.startswith("H")):     # guard: never remap a Hebrew lexeme to a Greek strong
            table[lexeme] = top_strong
    return table


def load_table(path: Path = _TABLE_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--out", type=Path, default=_TABLE_PATH)
    args = ap.parse_args()
    if not args.build:
        ap.error("nothing to do without --build")
        return 1

    table = build_table()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[hebrew_lexeme_strong] {len(table)} lexemes remapped -> {args.out}\n"
          f"  {table}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
