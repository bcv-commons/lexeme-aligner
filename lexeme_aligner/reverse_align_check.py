"""Reverse-alignment QA — walk the ORIGINAL-language lexeme sequence (Hebrew WLC OT, Greek
Nestle1904 NT, in canonical verse order) and check, per lexeme occurrence, whether our existing
eflomal output actually aligned it to something in the target text.

Built for the PKF `ind` anchor specifically, to investigate a known target-side issue: PKF IND
often uses an explicit verse RANGE marker ("3-4", "10-14", ...) to pool several source verses'
translation into one combined block. `usj_source.read_verses()` (used by `build_corpus()` in
run_pilot.py) keys a range by its FIRST number only and silently discards the range's end — so a
non-anchor verse inside the range (e.g. verse 4 of a "3-4" marker) looks like it has NO target text
at all, when really its translation is sitting in the anchor verse's (3's) combined block.

`usj_source.read_verse_ranges()` (added alongside this script) preserves the range end, so this
check can tell that apart from a GENUINE gap (no range marker at all, verse truly has no target).
Verified against real cases (1CH 3:10-14, 1CH 4:3-4, ...) — see internal-docs / session notes for
the worked examples that caught the original (index-of-suspicion) heuristic's bug: it guessed the
"absorbing" verse was whichever came AFTER a run of empty verses, when it's actually the range's own
FIRST verse, found directly from the marker string, not inferred.

    python3 -m lexeme_aligner.reverse_align_check --iso ind --testament ot
    python3 -m lexeme_aligner.reverse_align_check --iso ind --testament nt --out out/reverse_check_ind_nt.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.config import LEX_ROOT, OUT
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, _BOOK_FILE_NUM, pooled_verse_groups
from lexeme_aligner.usj_source import read_verse_ranges, tokenize


def load_alignment_pairs(iso: str, book: str, out_dir: Path, method: str = "eflomal") -> dict:
    """{(chapter, verse): {h_idx: pair_dict}} from the existing align_<method>_<iso>_<BOOK>.jsonl."""
    fp = out_dir / f"align_{method}_{iso}_{book}.jsonl"
    by_verse: dict[tuple[int, int], dict[int, dict]] = {}
    if not fp.exists():
        return by_verse
    for line in fp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        by_verse[(rec["chapter"], rec["verse"])] = {p["h_idx"]: p for p in rec["pairs"]}
    return by_verse


def check_book(book: str, heb: HebrewSource, usj_dir: Path, iso: str, out_dir: Path,
               method: str = "eflomal") -> list[dict]:
    """One row per LEXEME occurrence: {book, chapter, verse, idx, lexeme, surface, status, target}.
    status:
      'aligned'           — got a pair in the existing alignment output
      'unaligned'         — this verse (or its range anchor) HAD target text, word still missed
      'pooled_non_anchor' — verse is a non-first member of an explicit "M-N" range marker; its own
                            translation lives in the anchor verse's combined block, not a real gap
      'truly_skipped'     — genuinely no target text AND no range marker explains it

    Uses run_pilot.pooled_verse_groups() — the SAME range-pooling + idx-renumbering logic
    build_corpus() uses to produce the alignment output being checked here, so this can never drift
    out of sync with what the aligner actually saw (a real bug we hit: the first version of this
    check still looked up pairs by each verse's OWN number and OWN un-renumbered idx, silently
    reporting stale zero-change numbers after the pooling fix went in)."""
    usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    if not usj_path.exists():
        print(f"[reverse_check] skip {book}: no target USJ at {usj_path}", file=sys.stderr)
        return []
    ranges = read_verse_ranges(usj_path)
    pairs_by_verse = load_alignment_pairs(iso, book, out_dir, method)

    rows = []
    for ch in heb.chapters(book):
        for anchor_v, vs, ve, text, members in pooled_verse_groups(book, ch, heb, ranges):
            has_target = bool(text.strip())
            group_tokens = tokenize(text) if has_target else []
            pairs = pairs_by_verse.get((ch, anchor_v), {})

            for orig_v, tok in members:
                pair = pairs.get(tok.idx)   # tok.idx is renumbered within the pooled group
                is_anchor_member = (orig_v == vs)
                if pair is not None:
                    status = "aligned"
                elif not is_anchor_member:
                    status = "pooled_non_anchor"
                elif has_target:
                    status = "unaligned"
                else:
                    status = "truly_skipped"
                rows.append({
                    "book": book, "chapter": ch, "verse": orig_v, "idx": tok.idx,
                    "lexeme": tok.lexeme, "surface": tok.surface, "is_content": tok.is_content,
                    "status": status, "target": pair["target"] if pair else None,
                    "range_anchor_verse": vs,
                    "range_end_verse": ve,
                    "anchor_target_tok_count": len(group_tokens),
                    "group_target_tokens": group_tokens,
                })
    return rows


def load_lexeme_vocab(iso: str, root: Path = LEX_ROOT, hi_conf_only: bool = False) -> dict[str, set[str]]:
    """{lexeme: {surface, ...}} from the PUBLISHED lexeme-alignments/iso=<iso>/data.parquet — the
    additive union across ALL methods (eflomal/gloss/gapfill) and ALL pooled base_texts (for `ind`:
    the PKF anchor PLUS 5 other editions). Deliberately the LARGER cross-edition vocabulary, not just
    what PKF's own eflomal run learned — a word another edition uses for this lexeme is still real
    Indonesian evidence for what the lexeme can mean, even if PKF's own training never confidently
    picked it. Used by find_recoverable() as a reference set, not as ground truth — a candidate still
    has to be a real word IN THE SPECIFIC VERSE being checked to count (see find_recoverable).
    `hi_conf_only`: restrict to intersection-backed (score>=0.9) rows — the unfiltered vocab pulls in
    real noise (common function words showing up against content-word lexemes from low-confidence
    alignments elsewhere in the corpus; live-verified: 'itu'/'di'/'dan' turning up as "known"
    renderings of clearly unrelated Hebrew content words)."""
    import pyarrow.parquet as papq
    fp = root / f"iso={iso}" / "data.parquet"
    if not fp.exists():
        raise SystemExit(f"[reverse_check] no published data at {fp} — export_lex first")
    cols = ["lexeme", "surface"] + (["hi_conf"] if hi_conf_only else [])
    table = papq.read_table(fp, columns=cols)
    lexemes = table.column("lexeme").to_pylist()
    surfaces = table.column("surface").to_pylist()
    hi_conf = table.column("hi_conf").to_pylist() if hi_conf_only else None
    vocab: dict[str, set[str]] = collections.defaultdict(set)
    for i, (lexeme, surface) in enumerate(zip(lexemes, surfaces)):
        if hi_conf_only and not hi_conf[i]:
            continue
        for word in tokenize(surface):   # a "surface" can itself be a multi-word phrase
            vocab[lexeme].add(word)
    return dict(vocab)


def find_recoverable(rows: list[dict], vocab: dict[str, set[str]]) -> list[dict]:
    """Among 'unaligned' rows (target text existed, eflomal's own decode just missed this token):
    does the SAME verse-group's own target text contain a word already known — from elsewhere in the
    corpus's successful alignments (any method, any pooled edition) — to render this exact lexeme?
    If so, that word is a real, present-in-the-text candidate, not a fabrication — just not the one
    eflomal happened to pick for this specific occurrence. Diagnostic only: doesn't touch any
    alignment output, just reports how many 'unaligned' rows COULD be explained this way, and with
    which candidate word(s)."""
    recoverable = []
    for r in rows:
        if r["status"] != "unaligned":
            continue
        known = vocab.get(r["lexeme"])
        if not known:
            continue
        candidates = [w for w in r["group_target_tokens"] if w in known]
        if candidates:
            recoverable.append({**r, "candidates": candidates})
    return recoverable


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    by_status = collections.Counter(r["status"] for r in rows)
    content_rows = [r for r in rows if r["is_content"]]
    content_by_status = collections.Counter(r["status"] for r in content_rows)
    return {
        "total_lexemes": total,
        "by_status": dict(by_status),
        "aligned_rate": by_status["aligned"] / total if total else 0.0,
        "content_lexemes": len(content_rows),
        "content_by_status": dict(content_by_status),
        "content_aligned_rate": content_by_status["aligned"] / len(content_rows) if content_rows else 0.0,
    }


def list_ranges(rows: list[dict], min_span: int = 2) -> list[dict]:
    """Distinct multi-verse ranges actually found (verse_end > verse_start), with how many source
    lexemes they pool and how many target tokens the anchor verse's combined text carries."""
    seen = {}
    for r in rows:
        key = (r["book"], r["chapter"], r["range_anchor_verse"], r["range_end_verse"])
        if r["range_end_verse"] - r["range_anchor_verse"] + 1 < min_span:
            continue
        seen.setdefault(key, {"lexeme_count": 0, "target_tok_count": r["anchor_target_tok_count"]})
        seen[key]["lexeme_count"] += 1
    return [{"book": b, "chapter": c, "verse_start": vs, "verse_end": ve, **info}
            for (b, c, vs, ve), info in sorted(seen.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", default="ind")
    ap.add_argument("--testament", choices=["ot", "nt", "all"], default="all")
    ap.add_argument("--method", default="eflomal")
    ap.add_argument("--usj-dir", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--out", type=Path, default=None, help="write per-lexeme rows as jsonl")
    ap.add_argument("--recoverable", action="store_true",
                    help="diagnose 'unaligned' rows against the published lexeme-alignments vocab "
                         "(see load_lexeme_vocab/find_recoverable)")
    ap.add_argument("--vocab-iso", default=None, help="iso to load the vocab from (default: --iso)")
    ap.add_argument("--vocab-hi-conf-only", action="store_true",
                    help="restrict the recoverable-vocab check to hi_conf (score>=0.9) rows only")
    args = ap.parse_args()

    usj_dir = args.usj_dir or Path(f"data/usj-{args.iso}")
    books = (OT_BOOKS if args.testament == "ot" else
             NT_BOOKS if args.testament == "nt" else OT_BOOKS + NT_BOOKS)

    heb = HebrewSource()
    all_rows = []
    for book in books:
        rows = check_book(book, heb, usj_dir, args.iso, args.out_dir, args.method)
        all_rows.extend(rows)
        if rows:
            s = summarize(rows)
            print(f"[reverse_check] {book}: {s['aligned_rate']*100:.1f}% aligned "
                  f"({s['by_status']})", file=sys.stderr)

    print(f"\n[reverse_check] === {args.iso} {args.testament.upper()} overall ===", file=sys.stderr)
    summary = summarize(all_rows)
    print(json.dumps(summary, indent=2), file=sys.stderr)

    ranges = list_ranges(all_rows)
    print(f"\n[reverse_check] {len(ranges)} multi-verse RANGE(s) found (explicit \"M-N\" markers, "
          f"span>=2)", file=sys.stderr)
    for r in ranges[:20]:
        print(f"  {r['book']} {r['chapter']}:{r['verse_start']}-{r['verse_end']} "
              f"({r['verse_end']-r['verse_start']+1} source verses pooled, "
              f"{r['lexeme_count']} lexemes, {r['target_tok_count']} target tokens)", file=sys.stderr)
    if len(ranges) > 20:
        print(f"  ... and {len(ranges) - 20} more", file=sys.stderr)

    truly_skipped = [r for r in all_rows if r["status"] == "truly_skipped"]
    skipped_verses = sorted({(r["book"], r["chapter"], r["verse"]) for r in truly_skipped})
    print(f"\n[reverse_check] {len(skipped_verses)} verse(s) with GENUINELY no target text "
          f"(no range marker explains it):", file=sys.stderr)
    for b, c, v in skipped_verses[:20]:
        print(f"  {b} {c}:{v}", file=sys.stderr)
    if len(skipped_verses) > 20:
        print(f"  ... and {len(skipped_verses) - 20} more", file=sys.stderr)

    if args.recoverable:
        vocab = load_lexeme_vocab(args.vocab_iso or args.iso, hi_conf_only=args.vocab_hi_conf_only)
        unaligned_count = sum(1 for r in all_rows if r["status"] == "unaligned")
        recoverable = find_recoverable(all_rows, vocab)
        content_recoverable = [r for r in recoverable if r["is_content"]]
        print(f"\n[reverse_check] === recoverable-via-corpus-vocab diagnostic ===", file=sys.stderr)
        print(f"  vocab: {len(vocab)} distinct lexemes, from lexeme-alignments/iso="
              f"{args.vocab_iso or args.iso}/", file=sys.stderr)
        print(f"  {len(recoverable)}/{unaligned_count} unaligned lexemes ({len(recoverable)/unaligned_count*100:.1f}%) "
              f"have a known-elsewhere word present in their own verse "
              f"({len(content_recoverable)} of them content words)", file=sys.stderr)
        for r in recoverable[:20]:
            print(f"  {r['book']} {r['chapter']}:{r['verse']} [{r['lexeme']}] "
                  f"source={r['surface']!r} candidates={r['candidates']}", file=sys.stderr)
        if len(recoverable) > 20:
            print(f"  ... and {len(recoverable) - 20} more", file=sys.stderr)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for r in all_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n[reverse_check] wrote {len(all_rows)} row(s) → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
