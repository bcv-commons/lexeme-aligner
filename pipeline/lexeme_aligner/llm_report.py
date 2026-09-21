"""`llm_align --report`: score one finished experiment cell against gold and append a comparable row to
`pipeline/work/out/llm_report.md`.

A row answers the experiment's actual question — precision AND cost, on the SAME books and the SAME judgeable
subset as the baselines it is compared with: the gapfill and residual passes that ran on those tokens, scored
by `score_gapfill`'s own rule (a fill is correct when one of its words is in the gold's set for that
(verse, Strong's)). `$ per 1k correct` divides the run's cost by the judgeable-correct fills, so it is a
LOWER bound on cost-per-correct-fill (fills the gold cannot judge are not counted as either right or wrong).

For `verify` the interesting number is different: of the eflomal pairs the model was shown, how does the
precision of what it kept (confirmed + corrected) compare with the precision of the ORIGINAL proposals on
exactly the same tokens — printed as a second table.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import load_gold_gbt_positional, norm_surface
from lexeme_aligner.config import RESOURCES

_HEADER = ("| cell | strategy | model | effort | route | calls | to decide | fills | judgeable | correct | precision | "
           "gap cov | net cov (pt) | tokens in / cache-read / cache-write / out | $ | $/1k correct | call-time s (summed) |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")


def _judge(a):
    """-> f(ref, strong, words) -> True/False, or None when the gold has no truth for that token."""
    if a.gold == "clear":
        from lexeme_aligner.score_gapfill import clear_gold
        gold = clear_gold(a.publish_iso, RESOURCES, a.gold_iso)
    elif a.gold == "gbt":
        gold = load_gold_gbt_positional(a.gold_iso or a.publish_iso)
    else:
        raise SystemExit("[llm report] --gold lexicon is type-level, not positional; use clear or gbt")

    def judge(ref: int, strong: str, words: list[str]):
        key = (f"{ref:08d}", strong)
        return None if key not in gold else any(w in gold[key] for w in words)
    return judge


def _pairs(iso: str, method: str, out_dir: Path, book_nums: set[int]):
    from lexeme_aligner.llm_align import read_pairs
    for _m, ref, p in read_pairs(iso, out_dir, [method]):
        if ref // 1_000_000 in book_nums and p.get("strong"):
            yield ref, p


def tally(iso: str, method: str, out_dir: Path, book_nums: set[int], judge) -> dict[str, list[int]]:
    """prior -> [fills, judgeable, correct]."""
    t: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for ref, p in _pairs(iso, method, out_dir, book_nums):
        row = t[p.get("prior") or method]
        row[0] += 1
        j = judge(ref, p["strong"], [norm_surface(w) for w in p["target"].split()])
        if j is not None:
            row[1] += 1
            row[2] += bool(j)
    return dict(t)


def _sum(t: dict[str, list[int]]) -> list[int]:
    return [sum(v[i] for v in t.values()) for i in range(3)]


def _pct(c: int, n: int) -> str:
    return f"{100 * c / n:.1f}%" if n else "-"


def _row(cell: str, doc: dict, t: dict[str, list[int]]) -> str:
    fills, judged, correct = _sum(t)
    u = doc.get("usage_incl_cache") or doc.get("usage", {})          # the CELL's cost, however many times it was re-rendered
    decide = doc.get("tokens_to_decide") or 0
    net = 0.0 if doc.get("strategy") == "verify" else 100 * fills / max(1, doc.get("content_tokens") or 0)
    cost = doc.get("cell_cost_usd", doc.get("cost_usd", 0.0))
    return (f"| {cell} | {doc.get('strategy', '')} | {doc.get('model', '')} | {doc.get('effort', '')} | "
            f"{doc.get('provider', '')}{'+batch' if doc.get('batch') else ''} | {doc.get('calls', 0)}"
            f"(+{doc.get('local_cache_hits', 0)} cached) | {decide} | {fills} | {judged} | {correct} | "
            f"{_pct(correct, judged)} | {_pct(fills, decide)} | {net:.2f} | "
            f"{u.get('input_tokens', 0)} / {u.get('cache_read', 0)} / {u.get('cache_write', 0)} / {u.get('output_tokens', 0)} | "
            f"{cost:.3f} | {(f'{1000 * cost / correct:.2f}' if correct and cost else '-')} | {u.get('wall_s', 0):.0f} |")


def _baseline_row(name: str, t: dict[str, list[int]], decide: int) -> str:
    fills, judged, correct = _sum(t)
    return (f"| {name} | (baseline) | - | - | - | - | - | {fills} | {judged} | {correct} | {_pct(correct, judged)} | "
            f"{_pct(fills, decide)} | - | - | 0 | - | - |")


def _verify_table(a, doc: dict, out_dir: Path, book_nums: set[int], judge) -> str:
    """Original eflomal proposal vs what the model kept, on exactly the tokens it was shown."""
    kept: dict[tuple[int, int], str] = {}
    rejected: set[tuple[int, int]] = set()
    for fp in tag_files(out_dir, "llm", a.out_tag):
        for line in fp.open(encoding="utf-8"):
            rec = json.loads(line)
            for p in rec["pairs"]:
                kept[(rec["ref"], p["h_idx"])] = p.get("prior", "")
            for s in rec.get("llm_skipped", []):
                if s.get("status") == "rejected" and "h_idx" in s:
                    rejected.add((rec["ref"], s["h_idx"]))
    shown = set(kept) | rejected
    orig = collections.Counter()
    from lexeme_aligner.llm_align import read_pairs
    for _m, ref, p in read_pairs(a.iso, out_dir, ["eflomal"]):
        if ref // 1_000_000 in book_nums and (ref, p["h_idx"]) in shown and p.get("strong"):
            j = judge(ref, p["strong"], [norm_surface(w) for w in p["target"].split()])
            if j is not None:
                orig["judgeable"] += 1
                orig["correct"] += bool(j)
    final = tally(a.out_tag, "llm", out_dir, book_nums, judge)
    lines = ["", f"verify — {len(shown)} eflomal pairs shown; {len(rejected)} rejected, "
                 f"{sum(1 for v in kept.values() if v.endswith('confirmed'))} confirmed, "
                 f"{sum(1 for v in kept.values() if v.endswith('corrected'))} corrected", "",
             "| set | judgeable | correct | precision |", "|---|---|---|---|",
             f"| original eflomal proposals (same tokens) | {orig['judgeable']} | {orig['correct']} | "
             f"{_pct(orig['correct'], orig['judgeable'])} |"]
    for prior, (_f, jd, c) in sorted(final.items()):
        lines.append(f"| llm `{prior}` | {jd} | {c} | {_pct(c, jd)} |")
    fills, jd, c = _sum(final)
    lines.append(f"| llm kept (confirmed + corrected) | {jd} | {c} | {_pct(c, jd)} |")
    return "\n".join(lines)


def _same_token_table(a, out_dir: Path, book_nums: set[int], judge) -> str:
    """The fair comparison. The systems attempt DIFFERENT tokens, so their precisions alone are not comparable
    (a system that abstains a lot looks precise). Here every gold-judgeable gap token is the denominator, and each
    system is asked the same question about it: did you answer, and was the answer right? Built from files only:
    the gap tokens are the LLM cell's pairs + `llm_skipped` (which carries each declined token's Strong's)."""
    from lexeme_aligner.llm_align import read_pairs
    tokens: dict[tuple[int, int], tuple[str, list[str] | None]] = {}
    for fp in tag_files(out_dir, "llm", a.out_tag):
        for line in fp.open(encoding="utf-8"):
            rec = json.loads(line)
            if rec["ref"] // 1_000_000 not in book_nums:
                continue
            for p in rec["pairs"]:
                tokens[(rec["ref"], p["h_idx"])] = (p["strong"], [norm_surface(w) for w in p["target"].split()])
            for sk in rec.get("llm_skipped", []):
                if "strong" not in sk:
                    return "\n(same-token table needs `strong` in llm_skipped — re-run the cell; cached calls cost $0)\n"
                tokens.setdefault((rec["ref"], sk["h_idx"]), (sk["strong"], None))
    base: dict[str, dict[tuple[int, int], list[str]]] = {}
    for method in ("gapfill", "residual"):
        base[method] = {}
        try:
            for _m, ref, p in read_pairs(a.iso, out_dir, [method]):
                base[method].setdefault((ref, p["h_idx"]), [norm_surface(w) for w in p["target"].split()])
        except SystemExit:
            pass
    systems = {"llm": {k: v[1] for k, v in tokens.items() if v[1] is not None},
               "gapfill": base["gapfill"], "residual": base["residual"],
               "gapfill else residual": {**base["residual"], **base["gapfill"]}}
    truth = {k: v[0] for k, v in tokens.items() if judge(k[0], v[0], []) is not None}     # tokens the gold can judge
    lines = ["", f"same-token comparison — {len(truth)} gold-judgeable gap tokens (of {len(tokens)} sent)", "",
             "| system | answered | correct | wrong | precision | correct / judgeable token |", "|---|---|---|---|---|---|"]
    for name, answers in systems.items():
        got = [k for k in truth if k in answers]
        ok = sum(bool(judge(k[0], truth[k], answers[k])) for k in got)
        lines.append(f"| {name} | {len(got)} | {ok} | {len(got) - ok} | {_pct(ok, len(got))} | {_pct(ok, len(truth))} |")
    llm, alt = systems["llm"], systems["gapfill else residual"]
    abstained = [k for k in truth if k not in llm]
    lost = sum(1 for k in abstained if k in alt and judge(k[0], truth[k], alt[k]))
    saved = sum(1 for k in abstained if k in alt and not judge(k[0], truth[k], alt[k]))
    lines += ["", f"where the LLM abstained ({len(abstained)} judgeable tokens): gapfill/residual had the RIGHT answer "
                  f"on {lost} (missed) and a WRONG one on {saved} (wrong fill avoided); "
                  f"{len(abstained) - lost - saved} had no baseline answer either."]
    return "\n".join(lines)


def report(a) -> int:
    out_dir: Path = a.out
    ledger_fp = out_dir / f"llm_usage_{a.out_tag}.json"
    if not ledger_fp.exists():
        raise SystemExit(f"[llm report] no ledger {ledger_fp} — run the cell first")
    doc = json.loads(ledger_fp.read_text(encoding="utf-8"))
    from lexeme_aligner.refs import BOOK_NUMBERS
    book_nums = {BOOK_NUMBERS[b] for b in doc["books"]}
    judge = _judge(a)
    cell = tally(a.out_tag, "llm", out_dir, book_nums, judge)
    rows = [_row(a.out_tag, doc, cell)]
    decide = doc.get("tokens_to_decide") or 0
    for method in ("gapfill", "residual"):
        try:
            rows.append(_baseline_row(f"{a.iso} {method}", tally(a.iso, method, out_dir, book_nums, judge), decide))
        except SystemExit:
            pass
    table = _HEADER + "\n".join(rows)
    extra = (_verify_table(a, doc, out_dir, book_nums, judge) if doc.get("strategy") == "verify"
             else _same_token_table(a, out_dir, book_nums, judge))
    text = (f"\n### {a.out_tag} — {doc.get('ts', '')} (run {doc.get('run_id', '')}, prompt {doc.get('prompt_sha8', '')})"
            f"\n\n{table}\n{extra}\n")
    print(text)
    md = out_dir / "llm_report.md"
    if not md.exists():
        md.write_text("# LLM alignment experiment — cells\n\nGold = " + a.gold + f" ({a.gold_iso or a.publish_iso}). "
                      "`$/1k correct` = run cost / judgeable-correct fills (a lower bound on cost per correct fill). "
                      "Baselines are scored on the same books, same judgeable rule.\n", encoding="utf-8")
    with md.open("a", encoding="utf-8") as fh:
        fh.write(text)
    print(f"[llm report] appended to {md}", file=sys.stderr)
    return 0
