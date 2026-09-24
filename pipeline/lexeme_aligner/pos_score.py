"""Positional scoring against Clear-Bible gold — the measuring stick for VERSE alignments (Aim 2).

The existing scorers (`benchmark`, `score_gapfill`, `score_tiers`, ...) ask a DICTIONARY question: is one of
the words we produced for this (verse, Strong's) among the gold's surfaces? That never checks the verse's
partition — a target word claimed by the wrong source token, or a two-word span with one right word, both
score as correct. This module asks the positional question the ASV-style verse aligner has to answer: for
each source token, did we claim exactly the target positions the gold claims?

Three things had to be established before this was buildable (internal-docs/llm-align-experiment-plan.md §2):
  * the gold parquet carries positions — `target_id` = verse + Clear's token index, `source_id` = Clear's
    source token id;
  * Clear's target tokenization is reconstructible from our own edition text: every run of letters/marks/
    digits is a token, a hyphenated word is ONE token, an apostrophe attaches to the preceding word, and every
    other non-space character is its own token. Verified: 100.0% agreement with the gold surfaces on French
    verses without hyphen/apostrophe, 99.9% English, 100% Arabic, 98.6-98.7% Hindi/Spanish (the residue is
    footnote markers leaked into our ingested text — such verses are refused, never guessed);
  * the gold's `surface` column is UNRELIABLE in French after a hyphen/apostrophe (Clear's LSG release has its
    ids and its target file out of sync), and `eng`/`arb` pool two editions — so surfaces are re-derived here
    from our text via the mapped positions, and rows are filtered to the gold edition's `base_text`.

Source side: our NT spine is Nestle1904, Clear's is SBLGNT, and our spine fuses consecutive same-Strong's
tokens, so raw token indices are never compared. Tokens are keyed by (verse, Strong's, k-th occurrence in
verse order) on each side; verses where a Strong's occurs a different number of times on the two sides are
counted as `ambiguous` and excluded from the per-link metrics rather than matched by guesswork.

    python3 -m lexeme_aligner.pos_score --iso fra-lsg --publish-iso fra --usj-dir pipeline/work/ingest-cache/usj-fra-lsg \\
        --book MAT --method eflomal --method merged --method llm:fra-lsg.verify.sonnet5.cli
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import norm_surface
from lexeme_aligner.config import OUT, RESOURCES
from lexeme_aligner.refs import BOOK_NUMBERS
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
from lexeme_aligner.usj_source import _split_unspaced, read_verses, strip_marks, tokenize

_APOS = "'’ʼ"


# --- Clear's tokenization, reconstructed from the edition text -----------------------------------------
def _isw(c: str) -> bool:
    return unicodedata.category(c)[0] in ("L", "M", "N")


def clear_tokens(text: str) -> list[str]:
    """Clear-Bible's target tokenization of `text` (see module docstring). Token i corresponds to
    `target_id` index i+1.

    CJK fix (2026-09-24, found building `sword_source.py`'s Chinese gold support): a Han/Hiragana/
    Katakana script has no spaces, so the plain `_isw` word-run scan below glues an entire clause into
    ONE token ("神创造天地" -> one 5-character token) — `map_positions` then maps that single clear
    token onto ALL 5 of our own per-character `tokenize()` positions as a set, so a gold row anchored
    to just one character (e.g. "神" alone) would wrongly inherit the whole clause's positions. Every
    word RUN this function finds now goes through `usj_source._split_unspaced` (the same CJK/Myanmar
    splitter `tokenize()` itself uses) before being appended — a no-op for every non-CJK/Myanmar script
    (Latin/Cyrillic/Indic/Arabic/… all return unchanged), so no existing gold language's numbers move."""
    t = strip_marks(text)
    toks: list[str] = []
    i, n = 0, len(t)
    while i < n:
        c = t[i]
        if c.isspace():
            i += 1
            continue
        if _isw(c):
            j = i
            while j < n and _isw(t[j]):
                j += 1
            k = j
            while k < n and t[k].isspace():
                k += 1
            if k < n and t[k] in _APOS:                      # "d’" / "c’" — apostrophe attaches (our text may space it)
                j = k + 1
            else:
                while j < n - 1 and t[j] == "-" and _isw(t[j + 1]):    # "Jésus-Christ" is one token
                    j += 1
                    while j < n and _isw(t[j]):
                        j += 1
            toks.extend(_split_unspaced(t[i:j].replace(" ", "")))
            i = j
        else:
            toks.append(c)
            i += 1
    return toks


def _letters(s: str) -> str:
    """Letters and marks only, normalized — the part of a token both tokenizations share (hyphens, apostrophes
    and digits are exactly what they disagree on)."""
    return "".join(c for c in norm_surface(s) if unicodedata.category(c)[0] in ("L", "M"))


def map_positions(text: str) -> list[list[int]] | None:
    """Clear token index -> the positions of the same words in OUR tokenization (`usj_source.tokenize`),
    or None when the two cannot be reconciled for this verse (a leaked footnote number, a digit-only token
    our tokenizer drops, ...). Both are tokenizations of the same string, so a Clear word token maps to the
    next run of our tokens whose letters it contains; punctuation tokens map to nothing."""
    ours = [_letters(w) for w in tokenize(text)]
    out: list[list[int]] = []
    j = 0
    for tok in clear_tokens(text):
        letters = _letters(tok)
        if not letters:                                      # punctuation, or a digit-only token ours drops
            out.append([])
            continue
        taken: list[int] = []
        acc = ""
        while j < len(ours) and len(acc) < len(letters):
            acc += ours[j]
            taken.append(j)
            j += 1
        if acc != letters:
            return None
        out.append(taken)
    if j != len(ours):
        return None
    return out


# --- gold --------------------------------------------------------------------------------------------
@dataclass
class GoldVerse:
    ref: int
    links: dict[tuple[str, int], set[int]] = field(default_factory=dict)   # (strong, k) -> our target positions
    surfaces: dict[tuple[str, int], str] = field(default_factory=dict)     # (strong, k) -> re-derived surface
    claimed: set[int] = field(default_factory=set)                          # every target position the gold aligns
    ambiguous: set[str] = field(default_factory=set)                        # strongs whose k could not be trusted


def _book_file(usj_dir: Path, book: str) -> Path:
    return usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"


def load_gold(iso: str, usj_dir: Path, books: list[str], base_text: str | None, res_dir: Path = RESOURCES,
              gold_methods: tuple[str, ...] = ("manual",)
              ) -> tuple[dict[int, GoldVerse], collections.Counter]:
    """{ref: GoldVerse} for the `gold_methods` links of `base_text`, mapped onto our token positions; plus
    stats (verses seen / mapped / refused, links kept / dropped). Default is `manual` only — Clear's
    hand-aligned rows. Some languages (e.g. por/JFA11) carry ONLY `transfer` rows (machine-projected from a
    manually-aligned edition via verse structure, not a human annotation) — pass `gold_methods=("transfer",)`
    explicitly for those; never silently mix the two without saying so in the report, since transfer rows
    are a weaker evidentiary standard than manual ones."""
    import pyarrow.parquet as pq
    fp = res_dir / "strongs" / "attestations" / f"{iso}.parquet"
    if not fp.exists():
        raise SystemExit(f"[pos_score] no Clear gold for {iso} at {fp}")
    cols = ["ref", "strong", "target_id", "source_id", "method", "base_text"]
    rows = pq.read_table(fp, columns=cols).to_pylist()
    wanted = {BOOK_NUMBERS[b] for b in books}
    by_ref: dict[int, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r["method"] not in gold_methods or (base_text and r["base_text"] != base_text):
            continue
        ref = int(r["ref"])
        if ref // 1_000_000 in wanted:
            by_ref[ref].append(r)
    texts = {b: read_verses(_book_file(usj_dir, b)) for b in books if _book_file(usj_dir, b).exists()}
    stats: collections.Counter = collections.Counter()
    gold: dict[int, GoldVerse] = {}
    for ref, links in by_ref.items():
        book = next(b for b, n in BOOK_NUMBERS.items() if n == ref // 1_000_000)
        text = texts.get(book, {}).get((ref // 1000 % 1000, ref % 1000))
        stats["verses"] += 1
        if not text:
            stats["verses_no_text"] += 1
            continue
        mapping = map_positions(text)
        if mapping is None:
            stats["verses_refused"] += 1
            continue
        toks = tokenize(text)
        # gold source tokens in verse order -> k-th occurrence of each Strong's; a source token may link to
        # several target ids (several rows with the same source_id)
        per_source: dict[str, tuple[str, set[int]]] = {}
        for r in links:
            idx = int(r["target_id"][-3:]) - 1
            if idx >= len(mapping):
                stats["links_beyond_text"] += 1
                continue
            s = per_source.setdefault(r["source_id"], (r["strong"], set()))
            s[1].update(mapping[idx])
        gv = GoldVerse(ref)
        seen: collections.Counter = collections.Counter()
        for sid in sorted(per_source, key=lambda x: int(x[-3:])):
            strong, pos = per_source[sid]
            k = seen[strong]
            seen[strong] += 1
            if not pos:                                       # aligned only to punctuation: nothing on our side
                stats["links_punct_only"] += 1
                continue
            gv.links[(strong, k)] = pos
            gv.surfaces[(strong, k)] = " ".join(toks[p] for p in sorted(pos))
            gv.claimed |= pos
            stats["links"] += 1
        gold[ref] = gv
        stats["verses_mapped"] += 1
    return gold, stats


# --- our side ------------------------------------------------------------------------------------------
@dataclass
class Spine:
    """Per verse: the source tokens in spine order, so occurrences are counted over ALL tokens (not just the
    aligned ones) and `is_content` is known for every (strong, k)."""
    key_of: dict[int, dict[int, tuple[str, int]]]           # ref -> h_idx -> (strong, k)
    content: dict[int, set[tuple[str, int]]]                # ref -> {(strong, k) that are content tokens}
    counts: dict[int, collections.Counter]                  # ref -> strong -> occurrences in the verse


def load_spine(books: list[str], usj_dir: Path, iso: str) -> Spine:
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    recs = build_corpus(books, usj_dir, HebrewSource(), remap=remapper(iso, str(usj_dir)))
    key_of: dict[int, dict[int, tuple[str, int]]] = {}
    content: dict[int, set[tuple[str, int]]] = {}
    counts: dict[int, collections.Counter] = {}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        seen: collections.Counter = collections.Counter()
        key_of[ref], content[ref] = {}, set()
        for t in sorted(r.heb, key=lambda t: t.idx):
            if not t.strong:
                continue
            k = seen[t.strong]
            seen[t.strong] += 1
            key_of[ref][t.idx] = (t.strong, k)
            if t.is_content:
                content[ref].add((t.strong, k))
        counts[ref] = seen
    return Spine(key_of, content, counts)


@dataclass
class Ours:
    spans: dict[int, dict[tuple[str, int], set[int]]] = field(default_factory=dict)   # ref -> (strong,k) -> positions
    vetoed: set[tuple[int, str, int]] = field(default_factory=set)                   # (ref, strong, k) an LLM rejected


def load_ours(tag: str, method: str, out_dir: Path, books: list[str], spine: Spine) -> Ours:
    """Our pairs keyed like the gold. `llm_skipped` entries with status rejected/unrepresented become vetoes:
    a verify cell that rejected eflomal's proposal means the token has NO alignment in the LLM's view."""
    wanted = {BOOK_NUMBERS[b] for b in books}
    o = Ours()
    for fp in tag_files(out_dir, method, tag):
        with fp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                ref = rec["ref"]
                if ref // 1_000_000 not in wanted or ref not in spine.key_of:
                    continue
                verse = o.spans.setdefault(ref, {})
                for p in rec["pairs"]:
                    key = spine.key_of[ref].get(p["h_idx"])
                    if key and p.get("t_idx"):
                        verse[key] = set(p["t_idx"])
                for sk in rec.get("llm_skipped", []):
                    key = spine.key_of[ref].get(sk.get("h_idx", -1))
                    if key and sk.get("status") in ("rejected", "unrepresented"):
                        o.vetoed.add((ref, *key))
    return o


def union(parts: list[Ours]) -> Ours:
    """Earlier parts win a contested token; a veto from an earlier part removes the token from later ones."""
    out = Ours()
    for part in parts:
        out.vetoed |= part.vetoed
        for ref, verse in part.spans.items():
            dst = out.spans.setdefault(ref, {})
            for key, pos in verse.items():
                if key not in dst and (ref, *key) not in out.vetoed:
                    dst[key] = pos
    return out


def load_spec(spec: str, default_tag: str, out_dir: Path, books: list[str], spine: Spine) -> Ours:
    """`eflomal` · `llm:<tag>` · `a+b+c` (union, first wins) — e.g. `llm:x.verify+eflomal+gloss+gapfill`."""
    parts = []
    for item in spec.split("+"):
        method, _, tag = item.partition(":")
        parts.append(load_ours(tag or default_tag, method, out_dir, books, spine))
    return union(parts) if len(parts) > 1 else parts[0]


# --- metrics -------------------------------------------------------------------------------------------
@dataclass
class Metrics:
    links: int = 0             # gold links compared (unambiguous)
    ambiguous: int = 0         # gold links skipped: occurrence count differs between the two sides
    answered: int = 0          # of the compared links, how many we produced a span for
    exact: int = 0             # our span == gold span
    overlap: int = 0           # our span shares at least one position with gold's (the old, lenient notion)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    gold_claimed: int = 0      # target positions the gold aligns to some source (in compared verses)
    ours_claimed: int = 0      # target positions we align to some content source
    over_claimed: int = 0      # positions we claim that the gold leaves unaligned
    verses: int = 0

    def row(self) -> dict:
        p = self.tp / max(1, self.tp + self.fp)
        r = self.tp / max(1, self.tp + self.fn)
        return {"verses": self.verses, "gold_links": self.links, "ambiguous_skipped": self.ambiguous,
                "answered": self.answered, "exact_span": self.exact, "overlap": self.overlap,
                "link_precision": p, "link_recall": r, "link_f1": 2 * p * r / max(1e-9, p + r),
                "aer": 1 - 2 * self.tp / max(1, 2 * self.tp + self.fp + self.fn),
                "target_unclaimed_rate": 1 - self.ours_claimed / max(1, self.gold_claimed),
                "over_claimed": self.over_claimed}


def score(gold: dict[int, GoldVerse], ours: Ours, spine: Spine, content_only: bool = True) -> Metrics:
    """`content_only`: judge only gold links whose source token is a content lexeme — what the statistical
    chain is asked to align. Aim-2 (full-partition) output should be scored with content_only=False."""
    m = Metrics()
    for ref, gv in gold.items():
        if ref not in spine.key_of:
            continue
        ov = ours.spans.get(ref, {})
        m.verses += 1
        g_count = collections.Counter(s for s, _k in gv.links)
        for (strong, k), gpos in gv.links.items():
            if content_only and (strong, k) not in spine.content[ref]:
                continue
            # the gold lists only ALIGNED source tokens: if it aligned fewer occurrences of this Strong's than
            # the verse holds, its k and ours may not denote the same token — skip rather than guess
            if g_count[strong] != spine.counts[ref][strong]:
                m.ambiguous += 1
                continue
            m.links += 1
            opos = ov.get((strong, k))
            if not opos:
                m.fn += len(gpos)
                continue
            m.answered += 1
            m.exact += opos == gpos
            m.overlap += bool(opos & gpos)
            m.tp += len(opos & gpos)
            m.fp += len(opos - gpos)
            m.fn += len(gpos - opos)
        gold_claimed = (set().union(*(pos for key, pos in gv.links.items() if key in spine.content[ref]))
                        if content_only else gv.claimed) if gv.links else set()
        judged = {key: pos for key, pos in ov.items() if not content_only or key in spine.content[ref]}
        claimed = set().union(*judged.values()) if judged else set()
        m.gold_claimed += len(gold_claimed)
        m.ours_claimed += len(claimed & gold_claimed)
        m.over_claimed += len(claimed - gv.claimed)
    return m


def repaired_surfaces(gold: dict[int, GoldVerse]) -> dict[tuple[str, str], set[str]]:
    """{(ref as 8-digit str, strong): {normalized surfaces}} — the dictionary-scorer view of the gold, with
    surfaces taken from OUR text at the gold's positions instead of the parquet's (defective) column."""
    out: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for ref, gv in gold.items():
        for (strong, _k), surf in gv.surfaces.items():
            for w in surf.split():
                out[(f"{ref:08d}", strong)].add(norm_surface(w))
    return out


# --- CLI ---------------------------------------------------------------------------------------------
def _books(a) -> list[str]:
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    return (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in (a.book or ["MAT"])])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="edition TAG whose align_*.jsonl are scored")
    ap.add_argument("--publish-iso", required=True, help="gold language (config/gold_langs.json key)")
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--book", action="append"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--method", action="append", default=None,
                    help="method to score (repeatable). `llm:<out-tag>` scores an LLM cell; `merged` needs "
                         "merge_align output. Default: eflomal, gloss, gapfill")
    ap.add_argument("--base-text", default=None, help="gold edition (default: config/gold_langs.json's)")
    ap.add_argument("--gold-method", action="append", default=None,
                    help="Clear gold row provenance to accept (repeatable). Default: manual. Pass "
                         "'transfer' for languages with no manual rows (e.g. por/JFA11) — machine-projected "
                         "gold, weaker evidence than manual; the printed report always names which was used.")
    ap.add_argument("--all-tokens", action="store_true",
                    help="judge function-word links too (Aim-2 full partition); default: content lexemes only")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    from lexeme_aligner.contest_rule import gold_base_text, gold_usj_dir
    base_text = a.base_text or gold_base_text(a.publish_iso)
    want = gold_usj_dir(a.publish_iso)
    if want and Path(a.usj_dir).resolve() != Path(want).resolve():
        raise SystemExit(f"[pos_score] --usj-dir is not the gold edition for {a.publish_iso} ({want})")
    books = _books(a)
    gold_methods = tuple(a.gold_method) if a.gold_method else ("manual",)
    gold, stats = load_gold(a.publish_iso, a.usj_dir, books, base_text, gold_methods=gold_methods)
    print(f"[pos_score] gold {a.publish_iso}/{base_text} (method={','.join(gold_methods)}): "
          f"{stats['verses_mapped']}/{stats['verses']} verses mapped "
          f"({stats['verses_refused']} refused: tokenization mismatch; {stats['verses_no_text']} no text) · "
          f"{stats['links']} links ({stats['links_punct_only']} punctuation-only dropped, "
          f"{stats['links_beyond_text']} beyond text)", file=sys.stderr)
    spine = load_spine(books, a.usj_dir, a.iso)
    results = {}
    for spec in (a.method or ["eflomal", "gloss", "eflomal+gloss+gapfill"]):
        ours = load_spec(spec, a.iso, a.out, books, spine)
        if not ours.spans:
            print(f"[pos_score] nothing found for {spec} — skipped", file=sys.stderr)
            continue
        results[spec] = score(gold, ours, spine, content_only=not a.all_tokens).row()
    if a.json:
        print(json.dumps({"gold_stats": dict(stats), "results": results}, indent=1))
        return 0
    cols = ["gold_links", "answered", "exact_span", "overlap", "link_precision", "link_recall", "link_f1", "aer",
            "target_unclaimed_rate", "over_claimed", "ambiguous_skipped"]
    print(f"\n=== positional score vs Clear gold — {a.iso} ({', '.join(books)}; "
          f"{'all source tokens' if a.all_tokens else 'content lexemes only'}) ===")
    print(f"  {'method':44} " + " ".join(f"{c[:12]:>12}" for c in cols))
    for spec, r in results.items():
        print(f"  {spec:44} " + " ".join(f"{r[c]:>12.3f}" if isinstance(r[c], float) else f"{r[c]:>12}" for c in cols))
    print("  exact_span/overlap are counts over gold_links; link_* are per target position; aer = 1 - 2TP/(|ours|+|gold|)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
