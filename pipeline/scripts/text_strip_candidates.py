"""Report generator — clues for deciding whether an edition's [...] / (...) content should be opted
into stripping via config/text_strip_rules.json (see that file's `_doc` for the decision it feeds).

usj_source.py never strips anything automatically; a human reads this report, judges each flagged
edition, and writes the decision into config/text_strip_rules.json themselves. This script only
surfaces evidence — span-length distribution, known noise-pattern hits, and samples — it never writes
the decision file.

    make text-strip-report                     # all cached editions, writes config/text_strip_report.md
    python3 pipeline/scripts/text_strip_candidates.py --min-pct 0.05 --usj-root pipeline/work/ingest-cache
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on sys.path for lexeme_aligner

from lexeme_aligner.usj_source import (
    _BRACKET_NOTE_RE,
    _PAREN_BOOK_REF_RE,
    _PAREN_CITATION_RE,
    _PAREN_ERA_RE,
    _PAREN_LITERAL_RE,
    _PAREN_MEANS_RE,
    _PAREN_RE,
    _PAREN_VERSE_REF_RE,
    read_raw_verses,
    tokenize,
)

# _paren_is_noise's rules split into two evidence classes, reported separately so the report doesn't
# overclaim: book/verse-ref ("Yesaya 7:14", "vers 17") is a language-agnostic citation SHAPE — it fires
# correctly on Indonesian, Portuguese, anything, because Bible cross-references look the same regardless
# of translation language. citation/literal/means/era (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.) are
# Swedish VOCABULARY — a hit there is only meaningful evidence for a Swedish-language edition; on
# anything else it would be coincidence, not signal (verified this split matters: gefsgv's 44% "noise"
# hit rate turned out to be 100% book-ref citations, zero Swedish-vocabulary hits — a real, structural
# finding, but the old single "Swedish-lexical" label made it sound like an unlikely coincidence instead
# of the systematic apparatus pattern it actually is).
_STRUCTURAL_REF_RES = (_PAREN_VERSE_REF_RE, _PAREN_BOOK_REF_RE)
_SWEDISH_LEXICAL_RES = (_PAREN_CITATION_RE, _PAREN_LITERAL_RE, _PAREN_MEANS_RE, _PAREN_ERA_RE)


def _new_span_stats() -> dict:
    return {"n_spans": 0, "n_words": 0, "max_words": 0, "n_short": 0,
            "n_structural_ref": 0, "n_swedish_lexical": 0, "samples": []}


def _accumulate(stats: dict, span_re, text: str) -> None:
    for m in span_re.finditer(text):
        content = m.group(1) if span_re.groups else m.group(0)[1:-1]
        content = content.strip()
        if not content:
            continue
        w = len(tokenize(content))
        if w == 0:
            continue
        stats["n_spans"] += 1
        stats["n_words"] += w
        stats["max_words"] = max(stats["max_words"], w)
        if w <= 3:
            stats["n_short"] += 1
        if any(r.search(content) for r in _STRUCTURAL_REF_RES):
            stats["n_structural_ref"] += 1
        if any(r.search(content) for r in _SWEDISH_LEXICAL_RES):
            stats["n_swedish_lexical"] += 1
        stats["samples"].append((w, content))


def _edition_stats(usj_root: Path, tag: str) -> tuple[dict, dict, int]:
    """One read pass per book — bracket stats, paren stats, and total word count together, since all
    three need the same raw verse text and re-reading/re-tokenizing per metric wastes time at this
    edition count (182+ cached editions)."""
    bracket = _new_span_stats()
    paren = _new_span_stats()
    total_words = 0
    edition_dir = usj_root / f"usj-{tag}"
    for fp in sorted(edition_dir.glob("*.json")):
        for text in read_raw_verses(fp).values():
            total_words += len(tokenize(text))
            _accumulate(bracket, _BRACKET_NOTE_RE, text)
            _accumulate(paren, _PAREN_RE, text)
    return bracket, paren, total_words


# Length alone does NOT generalize as a "this is editorial noise" signal across editions — plenty of
# ordinary translations legitimately put a genuine long clause in parens (a translator's aside, a
# quoted-speech attribution, a relative clause), so a moderate median/pct_long bar still flags a huge
# share of totally normal editions (verified: an early median/outlier-ratio version still called 209 of
# 248 bracket/paren sections "commentary-like", incl. engy's parens — 368 spans, median 7 words, several
# genuine translated clauses like "from between the two cherubs, which [are] on the ark of the
# testimony", nothing editorial about it). What actually distinguished swk wasn't "somewhat long" — it
# was EXTREME (spans up to 818 words, far past any plausible in-verse aside) AND a real hit-rate against
# known editorial-note vocabulary (citations/cross-refs/ordagrant/betyder/era — 37% of its bracket
# spans). So "commentary-like" now requires one of those two independently strong signals, not just a
# length percentile that ordinary long-but-legitimate clauses can also produce.
_LONG_WORDS = 10  # a span this long or longer counts as an "outlier" for the small-outlier bucket below
_EXTREME_WORDS = 100  # far beyond any plausible single in-verse aside — swk's bracket max was 818


def _verdict(stats: dict) -> str:
    n = stats["n_spans"]
    if n == 0:
        return ""
    words = sorted(w for w, _ in stats["samples"])
    median = words[n // 2]
    pct_short = stats["n_short"] / n
    n_long = sum(1 for w in words if w >= _LONG_WORDS)
    pct_long = n_long / n
    ref_pct = stats["n_structural_ref"] / n
    lex_pct = stats["n_swedish_lexical"] / n

    if pct_short >= 0.85 and pct_long <= 0.05:
        return ("**short-span-like** (%.0f%% of spans are <=3 words, only %d/%d reach %d+ words) — "
                "resembles a supplied-word or short-gloss convention (e.g. YLT); likely genuine content, "
                "NOT a stripping candidate." % (pct_short * 100, n_long, n, _LONG_WORDS))

    # Deliberately NOT a "most spans are longish" branch: tried it (>=50% at 10+ words, n>=50) and it
    # false-positived on `law` — 84 bracket spans, 83% at 10+ words, median 15 — which turned out to be
    # ordinary full quoted sentences in an indigenous-language edition, not editorial notes; that
    # language's bracket convention (or just its normal sentence length) produces long spans with zero
    # actual commentary. Length alone never discriminates that from swk; only an EXTREME outlier (no
    # legitimate in-verse aside runs to hundreds of words) or a real hit-rate against one of the two
    # `_paren_is_noise` evidence classes does.
    strong_ref_evidence = stats["n_structural_ref"] >= 10 and ref_pct >= 0.20
    strong_lex_evidence = stats["n_swedish_lexical"] >= 10 and lex_pct >= 0.20
    strong_length_evidence = stats["max_words"] >= _EXTREME_WORDS
    if strong_ref_evidence or strong_lex_evidence or strong_length_evidence:
        why = []
        if strong_length_evidence:
            why.append(f"max span reaches {stats['max_words']} words — far past a plausible aside")
        if strong_ref_evidence:
            why.append(f"{stats['n_structural_ref']} spans ({ref_pct * 100:.0f}%) look like verse/book "
                       f"citations (language-agnostic pattern, e.g. 'Yesaya 7:14')")
        if strong_lex_evidence:
            why.append(f"{stats['n_swedish_lexical']} spans ({lex_pct * 100:.0f}%) matched Swedish-specific "
                       f"editorial vocabulary (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.)")
        return ("**commentary-like** (" + "; ".join(why) + ") — resembles swk's editorial-note usage; "
                "worth reviewing for `strip_brackets`/`strip_parens_noise`.")

    if 0 < n_long <= max(3, round(0.05 * n)):
        return ("mostly short (median %d words/span) with %d long outlier(s) (%d+ words) out of %d — "
                "not a bulk pattern, but worth reading those specific outliers before deciding anything."
                % (median, n_long, _LONG_WORDS, n))
    return (f"no strong signal either way (median {median} words/span, {n} spans) — legitimately long "
            "parenthetical/bracketed clauses are common in ordinary translation; read the samples before "
            "assuming this is editorial noise.")


def _format_section(label: str, stats: dict, total_words: int, sample_n: int, seed: random.Random) -> list[str]:
    lines = []
    pct = (stats["n_words"] / total_words * 100) if total_words else 0.0
    lines.append(f"### {label} — {stats['n_spans']} spans, {stats['n_words']} words "
                 f"({pct:.2f}% of edition), max {stats['max_words']} words/span")
    # A handful of coincidental hits isn't a signal — require both a real count and a real share of spans
    # before surfacing either line, so it means something when it appears. Kept separate (see the module
    # comment above _STRUCTURAL_REF_RES): a ref-shape hit is evidence in ANY language, a lexical hit is
    # only evidence if this edition is actually Swedish.
    n = stats["n_spans"]
    ref_pct = stats["n_structural_ref"] / n if n else 0.0
    lex_pct = stats["n_swedish_lexical"] / n if n else 0.0
    if stats["n_structural_ref"] >= 5 and ref_pct >= 0.05:
        lines.append(f"- {stats['n_structural_ref']} span(s) ({ref_pct * 100:.0f}%) look like verse/book "
                      f"citations (e.g. 'Yesaya 7:14', 'vers 17') — a language-agnostic apparatus pattern, "
                      f"not translated content, regardless of this edition's language.")
    if stats["n_swedish_lexical"] >= 5 and lex_pct >= 0.05:
        lines.append(f"- {stats['n_swedish_lexical']} span(s) ({lex_pct * 100:.0f}%) matched "
                      f"Swedish-specific editorial vocabulary (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.) "
                      f"— only meaningful if this edition is actually in Swedish.")
    verdict = _verdict(stats)
    if verdict:
        lines.append(f"- Verdict: {verdict}")
    if stats["samples"]:
        pool = stats["samples"]
        picked = seed.sample(pool, min(sample_n, len(pool)))
        lines.append("- Samples:")
        for w, content in picked:
            shown = content if len(content) <= 160 else content[:157] + "..."
            lines.append(f"  - ({w}w) `{shown}`")
    return lines


def build_report(usj_root: Path, min_pct: float, sample_n: int, decisions: dict) -> str:
    seed = random.Random(0)  # stable sampling across re-runs so diffs are meaningful
    tags = sorted(p.name[len("usj-"):] for p in usj_root.glob("usj-*") if p.is_dir())
    rows = []
    for i, tag in enumerate(tags, 1):
        print(f"[{i}/{len(tags)}] {tag}", file=sys.stderr)
        bracket, paren, total_words = _edition_stats(usj_root, tag)
        if total_words == 0:
            continue
        b_pct = bracket["n_words"] / total_words * 100
        p_pct = paren["n_words"] / total_words * 100
        if max(b_pct, p_pct) < min_pct:
            continue
        rows.append((max(b_pct, p_pct), tag, bracket, paren, total_words, b_pct, p_pct))
    rows.sort(key=lambda r: -r[0])

    out = [
        "# Text-strip candidates",
        "",
        f"Generated by `pipeline/scripts/text_strip_candidates.py` — {len(rows)} of {len(tags)} cached "
        f"editions have >= {min_pct}% of their words inside `[...]` or `(...)`. This is EVIDENCE, not a "
        "decision: nothing here is stripped automatically. Read the samples and verdict for an edition, "
        "then (if warranted) write the decision into `config/text_strip_rules.json` yourself.",
        "",
    ]
    for _, tag, bracket, paren, total_words, b_pct, p_pct in rows:
        out.append(f"## {tag}")
        if tag in decisions:
            d = decisions[tag]
            out.append(f"> Decision already on file: `strip_brackets={d.get('strip_brackets', False)}`, "
                        f"`strip_parens_noise={d.get('strip_parens_noise', False)}` — see "
                        "`config/text_strip_rules.json`.")
        out.append(f"{total_words} words total in this edition.")
        out.append("")
        if bracket["n_spans"]:
            out.extend(_format_section("Brackets `[...]`", bracket, total_words, sample_n, seed))
            out.append("")
        if paren["n_spans"]:
            out.extend(_format_section("Parens `(...)`", paren, total_words, sample_n, seed))
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--min-pct", type=float, default=0.1,
                     help="only report editions where brackets or parens are >= this %% of total words")
    ap.add_argument("--samples", type=int, default=5, help="sample spans shown per bracket/paren section")
    ap.add_argument("--out", type=Path, default=Path("config/text_strip_report.md"))
    ap.add_argument("--rules", type=Path, default=Path("config/text_strip_rules.json"))
    args = ap.parse_args()

    decisions = {}
    if args.rules.exists():
        import json
        decisions = json.loads(args.rules.read_text(encoding="utf-8")).get("editions", {})

    report = build_report(args.usj_root, args.min_pct, args.samples, decisions)
    args.out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
