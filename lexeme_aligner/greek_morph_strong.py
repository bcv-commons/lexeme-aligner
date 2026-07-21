"""Traditional (tense/case/person-specific) Strong's numbers for Greek words whose lemma-level
Strong's (what our own MACULA-based spine emits) doesn't match what Clear-Bible's gold uses.

THE ISSUE: Strong's numbering, in its original 1890 concordance form, assigns SEPARATE numbers to
distinct inflected FORMS of highly irregular/suppletive Greek words (εἰμί "to be" has no shared root
spelling across ἦν/εἰμί/ἔσομαι/etc., so each principal part got its own concordance entry). Clear's
gold preserves that traditional numbering. Modern digital tagging (including MACULA, which our own
spine is built from) simplifies this to ONE lemma-level number (G1510) — our spine's `morph` column
is empty, so we currently CANNOT reconstruct which specific form a given occurrence is. This makes
our benchmark score against Clear look worse than our alignment actually is: eflomal/gloss correctly
picked the French/target word for εἰμί, but the Strong's comparison fails because Clear expects
G2258 (imperfect) where we emit G1510 (lemma).

THE FIX: gbt's `hbo+grc` source data (data/gbt/hbo+grc/, already fetched — see PROVENANCE.txt)
carries the FULL morphological parse (e.g. `V-IIA-3S` = imperfect indicative active 3rd singular) for
every Greek word, independent of any target language. Cross-referencing that against Clear's own
Strong's usage (eng/BSB, the largest gold set — 723k rows) gives a clean, near-deterministic function:
(lemma, grammar_code) -> traditional_strong, 97%+ pure wherever there's enough data. Verified
consistent across independent gold languages (eng vs fra) for well-sampled codes — this is a fact
about the GREEK SOURCE TEXT, not something that varies by target language.

SCOPE: only 12 Greek lemmas show this pattern with enough volume+purity to trust (εἰμί and other
common irregular/suppletive words: pronouns, negations, a few verbs) — 74 (lemma, grammar_code)
cells total, filtered to purity>=90%, n>=10 occurrences. NOT built for Hebrew: Hebrew's root-
consonant-based morphology doesn't need this (a session-time check found H1961/הָיָה's own
non-prefixed grammar codes ALL map to H1961 itself — no traditional-number splitting exists there;
the only apparent variance was gbt's whole-word tokenization not matching Clear's separately-
attested PREFIX particles, which our own spine already splits correctly via MACULA prefix-splitting).

Table stored at data/greek_morph_strong.json (small, committed — a derived reference table, not bulk
data). Applied ONLY when comparing against Clear gold (benchmark.py) — NOT written back into our own
published `strong` field, which stays the stable MACULA lemma-rollup (docs/publishing-principles.md
§2: strong is a bridge key, lexeme is the anchor of record; this table is a benchmark-side translation
layer, not a schema change).

    python3 -m lexeme_aligner.greek_morph_strong --build     # rebuild the table from gbt + eng/BSB gold
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import sys
from pathlib import Path

_TABLE_PATH = Path("data/greek_morph_strong.json")
_MIN_N = 10
_MIN_PURITY = 0.90


def _load_gbt_morphology(hbo_grc_dir: Path) -> dict[int, tuple[str, str]]:
    """word_id -> (lemma, grammar_code), from gbt's hbo+grc source (Greek NT books only)."""
    morph_by_id = {}
    for fp in glob.glob(str(hbo_grc_dir / "4*.json")):
        d = json.loads(Path(fp).read_text(encoding="utf-8"))
        for ch in d["chapters"]:
            for v in ch["verses"]:
                for w in v["words"]:
                    morph_by_id[int(w["id"])] = (w["lemma"], w["grammar"])
    return morph_by_id


def build_table(hbo_grc_dir: Path = Path("data/gbt/hbo+grc"),
                gold_path: Path = Path("data/resources/strongs/attestations/eng.parquet"),
                base_text: str = "BSB", min_n: int = _MIN_N, min_purity: float = _MIN_PURITY) -> dict:
    import pyarrow.parquet as pq
    morph_by_id = _load_gbt_morphology(hbo_grc_dir)
    rows = pq.read_table(gold_path).to_pylist()
    rows = [r for r in rows if r["base_text"] == base_text]

    by_cell: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        sid = r["source_id"]
        if not (sid.startswith("n") and len(sid) == 12):    # NT-only node-id shape (gbt Greek scope)
            continue
        ref, wordnum = int(sid[1:9]), int(sid[9:12])
        m = morph_by_id.get(ref * 100 + wordnum)
        if m:
            by_cell[m][r["strong"]] += 1

    table = {}
    for (lemma, gcode), counter in by_cell.items():
        total = sum(counter.values())
        top_strong, top_n = counter.most_common(1)[0]
        if total >= min_n and top_strong != lemma and (top_n / total) >= min_purity:
            table[f"{lemma}|{gcode}"] = top_strong
    return table


def load_table(path: Path = _TABLE_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="rebuild the table from gbt morphology + eng/BSB gold")
    ap.add_argument("--out", type=Path, default=_TABLE_PATH)
    args = ap.parse_args()

    if not args.build:
        ap.error("nothing to do without --build")
        return 1

    table = build_table()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    lemmas = sorted({k.split("|")[0] for k in table})
    print(f"[greek_morph_strong] {len(table)} (lemma,grammar) cells across {len(lemmas)} lemmas -> {args.out}\n"
          f"  lemmas: {lemmas}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
