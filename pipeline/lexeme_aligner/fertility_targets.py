"""R1 (internal-docs/aim1-multiword-grammar-tools-plan.md, 2026-09-27): per-LEXEME fertility targets
from EXTERNAL gold, for `fertility_priors.py`.

WHY: the shipped fertility prior boosts every anchor that carries a typology-gated RELATION (a genitive-
marked noun, a definite noun, a finite verb) — 7,727 anchors for spa — while bcv-query's independent
`fertility` MWE list confirms real multi-word fertility for 215 spa lexemes: 87% recall, **2.4% lexeme
precision** (plan §8 / F5). And every predictor learned from OUR OWN spans has failed (cross_lang r=0.075,
D3 −0.002 vs +1.41) because our own output has ~no span variance. This table is built from the one
source that has real variance and is not ours: the gold parquets on disk (Clear `manual`, SWORD,
HELFI, Door43 — `pipeline/vendor/resources/strongs/attestations/<iso>.parquet`), one row per
(source token, target word), so a source token's fertility in a gold language is simply its row count.

WHAT IT WRITES: `config/fertility/lexeme_targets.json` — `{anchor: {"f": int, "mean": float,
"langs": n, "multi_langs": k}}` where `anchor` is the bare Strong's rollup eflomal keys its source line
on (`H0559`, `G2316`), `mean` the cross-language mean of per-language mean span length, `langs` how many
gold (iso, base_text) sources attest the anchor at all, `multi_langs` how many of those render it
multi-word at least half the time, `f` = round(mean) clamped to eflomal's 1..7. Punctuation-only target
rows are ignored (same as `pos_score.load_gold`). Per-language means are computed first so a heavily
annotated language (eng, 113k links) cannot outvote a thin one.

HOW IT IS CONSUMED (fertility_priors.py, `--fertility-lexeme-targets` / config `lexeme_targets`): a
FERF line is emitted only for anchors that BOTH carry an open relation gate (unchanged) AND have
`multi_langs >= min_langs`; `f` comes from the table instead of `1 + increments`. Nothing here is
language-specific — a lexeme's tendency to render as a phrase ("חֶסֶד → loving kindness / bonté /
misericordia") is a lexeme fact across analytic languages, which is exactly why gold from other languages
can inform a language that has none.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import RESOURCES

_TARGETS_FILE = Path("config/fertility/lexeme_targets.json")


def _has_letters(s: str) -> bool:
    return any(ch.isalpha() for ch in (s or ""))


def per_source_fertility(rows: list[dict]) -> dict[tuple[str, str], list[int]]:
    """{(base_text, anchor): [span lengths]} for one language's gold rows (all methods)."""
    spans: dict[tuple[str, str, str, str], int] = collections.Counter()
    strong_of: dict[tuple[str, str, str, str], str] = {}
    for r in rows:
        if not _has_letters(r.get("surface") or ""):
            continue
        key = (r.get("method") or "", r.get("base_text") or "", r["ref"], r["source_id"])
        spans[key] += 1
        strong_of[key] = r["strong"]
    out: dict[tuple[str, str], list[int]] = collections.defaultdict(list)
    for key, n in spans.items():
        out[(key[1], strong_of[key])].append(n)
    return out


def build_targets(res_dir: Path = RESOURCES, min_occurrences: int = 3) -> dict[str, dict]:
    """Aggregate every gold parquet under `res_dir/strongs/attestations/`. An (iso, base_text) source
    contributes an anchor only when it attests it >= `min_occurrences` times (one-off spans are noise)."""
    import pyarrow.parquet as pq
    per_anchor_means: dict[str, list[float]] = collections.defaultdict(list)
    per_anchor_multi: dict[str, int] = collections.Counter()
    cols = ["strong", "surface", "ref", "source_id", "method", "base_text"]
    for fp in sorted((res_dir / "strongs" / "attestations").glob("*.parquet")):
        rows = pq.read_table(fp, columns=cols).to_pylist()
        for (base_text, anchor), lengths in per_source_fertility(rows).items():
            if len(lengths) < min_occurrences or not anchor:
                continue
            mean = sum(lengths) / len(lengths)
            per_anchor_means[anchor].append(mean)
            if sum(1 for n in lengths if n >= 2) / len(lengths) >= 0.5:
                per_anchor_multi[anchor] += 1
    table: dict[str, dict] = {}
    for anchor, means in per_anchor_means.items():
        mean = sum(means) / len(means)
        table[anchor] = {"f": max(1, min(7, round(mean))), "mean": round(mean, 3),
                         "langs": len(means), "multi_langs": per_anchor_multi[anchor]}
    return table


def load_targets(path: Path = _TARGETS_FILE) -> dict[str, dict]:
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8")).get("anchors", {})


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resources", type=Path, default=RESOURCES)
    ap.add_argument("--out", type=Path, default=_TARGETS_FILE)
    ap.add_argument("--min-occurrences", type=int, default=3)
    a = ap.parse_args(argv)
    table = build_targets(a.resources, a.min_occurrences)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    multi2 = sum(1 for v in table.values() if v["multi_langs"] >= 2)
    doc = {"_doc": "R1 per-lexeme fertility targets from external gold — see fertility_targets.py.",
           "n_anchors": len(table), "n_multi_langs_ge2": multi2, "anchors": dict(sorted(table.items()))}
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[fertility_targets] {len(table)} anchors; {multi2} multi-word in >=2 gold sources → {a.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
