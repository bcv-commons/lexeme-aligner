"""Extract source(heb/grc)<->target occurrence alignment from Clear-Bible gold attestations
(data/resources/strongs/attestations/<iso>.parquet) — a THIRD occurrence-alignment source alongside
gbt_align.py and bsb_align.py, same "occurrence layer" concept. This data has been in the repo the
whole time and is used for benchmarking (benchmark.py); this module does NOT change or re-score
anything — it's a re-packaging of the SAME rows into the shared occurrence_align schema so Clear's
alignment is citable/cross-checkable alongside gbt/bsb, not just used as a scoring oracle.

Shape: each row is (strong, lemma, surface, ref, target_id, source_id, method, source_corpus,
base_text) — already a positional link (`source_id` <-> `target_id`), not an id-gap scheme like gbt
or a Strong's-FIFO match like bsb. Many-to-many is encoded by REPEATED ids across rows: a `source_id`
appearing on >1 row is one source word rendered by several target words (1:many — common, e.g.
eng/BSB has 217k such source_ids); a `target_id` appearing on >1 row is several source words
collapsing onto one target word (many:1 — rarer, e.g. eng/BSB's H7785+H1886 both -> "thigh").
Grouping is by CONNECTED COMPONENTS over the (source_id, target_id) bipartite graph, built per verse
per base_text (editions never cross-link) — the correct general approach here since source_id/
target_id ARE the alignment graph already, unlike gbt/bsb where the source's own addressing scheme
had to be reverse-engineered.

Each row also carries `base_text` (edition — a language can have >1, e.g. eng=BSB+YLT, never pooled)
and `clear_method` (Clear's own manual/transfer distinction — `transfer` is algorithmically projected,
not human-aligned; lower trust, per por's data).

`verse_ref` = int(ref), matching lexeme_aligner.refs.encode() directly (verified: the raw `ref`
string already IS the BBCCCVVV integer as text, no decoding needed).

    python3 -m lexeme_aligner.clear_align --lang fra
    python3 -m lexeme_aligner.clear_align --all       # every attestations/*.parquet found
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_GOLD_DIR = Path("data/resources/strongs/attestations")
_OUT_DIR = Path("resources/occurrence_align")


def _connected_components(rows: list[dict]) -> list[list[dict]]:
    """Group rows into connected components over the (source_id, target_id) bipartite graph."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for r in rows:
        s_key, t_key = ("s", r["source_id"]), ("t", r["target_id"])
        parent.setdefault(s_key, s_key)
        parent.setdefault(t_key, t_key)
        union(s_key, t_key)

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        root = find(("s", r["source_id"]))
        groups[root].append(r)
    return list(groups.values())


def extract_lang(iso: str, gold_dir: Path = _GOLD_DIR) -> list[dict]:
    import pyarrow.parquet as pq
    fp = gold_dir / f"{iso}.parquet"
    if not fp.exists():
        raise SystemExit(f"[clear_align] no gold file for {iso}: {fp}")
    all_rows = pq.read_table(fp).to_pylist()

    by_base_text: dict[str, list[dict]] = collections.defaultdict(list)
    for r in all_rows:
        by_base_text[r["base_text"]].append(r)

    out_rows = []
    for base_text, rows in by_base_text.items():
        by_verse: dict[str, list[dict]] = collections.defaultdict(list)
        for r in rows:
            by_verse[r["ref"]].append(r)
        for ref, verse_rows in by_verse.items():
            for comp in _connected_components(verse_rows):
                source_ids = sorted({r["source_id"] for r in comp})
                target_ids = sorted({r["target_id"] for r in comp})
                strongs = [r["strong"] for r in comp]
                lemmas = [r["lemma"] for r in comp]
                surfaces = [r["surface"] for r in comp]
                ns, nt = len(source_ids), len(target_ids)
                kind = ("1:1" if (ns, nt) == (1, 1) else "1:many" if ns == 1
                        else "many:1" if nt == 1 else "many:many")
                out_rows.append({
                    "source_ids": source_ids, "source_text": lemmas, "source_strong": strongs,
                    "target_ids": target_ids, "target_gloss": surfaces, "kind": kind,
                    "verse_ref": int(ref), "lang": iso, "source": "clear",
                    "base_text": base_text, "clear_method": comp[0]["method"],
                })
    return out_rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", help="single gold language (e.g. fra)")
    ap.add_argument("--all", action="store_true", help="every attestations/*.parquet found")
    ap.add_argument("--gold-dir", type=Path, default=_GOLD_DIR)
    ap.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = ap.parse_args()

    if args.all:
        isos = sorted(fp.stem for fp in args.gold_dir.glob("*.parquet"))
    elif args.lang:
        isos = [args.lang]
    else:
        ap.error("need --lang or --all")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for iso in isos:
        rows = extract_lang(iso, args.gold_dir)
        kind_counts = collections.Counter(r["kind"] for r in rows)
        by_base_text = collections.Counter(r["base_text"] for r in rows)
        out_fp = args.out_dir / f"clear_{iso}.jsonl"
        with out_fp.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[clear_align] {iso}: {len(rows)} groups -> {out_fp}\n"
              f"  kind breakdown: {dict(kind_counts)}\n"
              f"  by base_text: {dict(by_base_text)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
