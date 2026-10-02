"""What do an edition's `[...]` and `(...)` spans MEAN? Separate evidence and a separate role for each kind.

Brackets and parentheses are not interchangeable. Measured 2026-09-30 (alignment rate of a word, inside a span vs outside):
ASV, BSB and Spanish RV09 parentheses align at the ordinary rate (38-41% vs 39-42% outside), so they are real translation
content; YLT brackets align at 5.9% against 40.6% outside, because they are supplied words that have no source token.
Kärnbibeln's bracketed text is editorial commentary. One yardstick for both kinds cannot tell these apart, so every signal
here is computed per kind and the role is decided per kind.

SIGNALS (per edition, per kind)
  length      spans, words, share of the edition, median / 90th-percentile / maximum words per span, share <= 3 words,
              share "clause-like" (>= 4 words and terminal punctuation inside, or >= 8 words), share with digits.
  noise       share of spans that look like verse/book citations (language-agnostic shape); the Swedish-vocabulary class
              is kept apart and only counted for Swedish editions.
  alignment   inside-rate / outside-rate: the share of words that eflomal aligned to a source token, inside spans divided
              by outside spans (non-pooled verses only). Near 1 means the span words are translated text; near 0 means
              supplied words or commentary. Needs alignments on disk, otherwise None.
  repetition  share of a span's content words that also occur OUTSIDE every span of the same verse. A span that mostly
              repeats its own verse is a second rendering of it (an alternate), not new text and not commentary. Reported
              as the share of evaluated spans at >= 50% repetition, and the words those spans hold.
  siblings    mean share of a span's words that occur in the same verse of other editions of the same language and script.
              Reported as evidence only; it is not used by the role rule (translations differ too much lexically).

ROLES (what the output side should do with the kind)
  content       real translation text: keep it in training and in the main array.
  supplied      bracketed words that have no source counterpart (YLT style): harmless either way, keep as content.
  mixed         mostly content with a minority of noise spans: strip only the spans a classifier flags.
  commentary    editorial notes: strip from training and keep out of the main array.
  alternate     spans that mostly repeat or paraphrase their own verse (a parallel rendering, "[or ...]" variants): strip
                from training so the verse is not counted twice, keep out of the main array.
  negligible    under 0.05% of the edition's words: the decision cannot matter.
  insufficient  fewer than 20 spans of this kind: not enough to judge.
  unclear       the signals disagree or are missing; needs a human look at the samples.
"""
from __future__ import annotations

import gzip
import json
import re
import statistics
from pathlib import Path

NEGLIGIBLE_PCT = 0.05
MIN_SPANS = 20
EXTREME_WORDS = 100
LOW_RATIO = 0.35
HIGH_RATIO = 0.75
ALT_SPAN_SHARE = 0.5      # a span counts as a repetition when at least this share of its content words recur outside it
ALT_EDITION_PCT = 0.30    # ... and the kind is an alternate when this share of the evaluated spans are repetitions
MIN_CONTENT_WORDS = 2     # spans with fewer content words give no repetition evidence
_TERMINAL_RE = re.compile(r"[.!?;]\s*$|[.!?;]\s+\S")
_DIGIT_RE = re.compile(r"\d")


def _percentile(sorted_vals: list[int], q: float) -> int:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))] if sorted_vals else 0


def content_words(tokens: list[str]) -> set[str]:
    """Lower-cased distinct words that can carry repetition evidence: 4+ letters, so articles, prepositions and the like
    cannot make an unrelated span look like a repeat. Scripts whose words are short (CJK, Indic) keep 2+ letters."""
    out = set()
    for t in tokens:
        t = t.lower()
        short_script = any(ord(c) > 0x590 for c in t)
        if len(t) >= (2 if short_script else 4):
            out.add(t)
    return out


def repetition_share(span_tokens: list[str], outside_words: set[str]) -> float | None:
    """Share of the span's content words that also occur in the rest of the verse; None without enough evidence."""
    ws = content_words(span_tokens)
    if len(ws) < MIN_CONTENT_WORDS:
        return None
    return len(ws & outside_words) / len(ws)


def repetition_stats(shares: list[tuple[int, float]]) -> dict:
    """`shares` = [(span word count, repetition share)] for the spans that had evidence."""
    n = len(shares)
    rep = [(w, sh) for w, sh in shares if sh >= ALT_SPAN_SHARE]
    return {"n_dup_eval": n,
            "dup_median": round(statistics.median(sh for _, sh in shares), 3) if n else None,
            "pct_dup": round(len(rep) / n, 3) if n else 0.0,
            "n_dup_words": sum(w for w, _ in rep)}


def span_length_stats(spans: list[tuple[int, str]], structural_hits: int, lexical_hits: int) -> dict:
    """`spans` = [(word count, span text)]. Shape/length statistics, no alignment involved."""
    n = len(spans)
    words = sorted(w for w, _ in spans)
    clause = sum(1 for w, t in spans if (w >= 4 and _TERMINAL_RE.search(t)) or w >= 8)
    return {"n_spans": n, "n_words": sum(words), "median_words": statistics.median(words) if words else 0,
            "p90_words": _percentile(words, 0.9), "max_words": words[-1] if words else 0,
            "pct_short": (sum(1 for w in words if w <= 3) / n) if n else 0.0,
            "pct_clause": clause / n if n else 0.0,
            "pct_digits": (sum(1 for _, t in spans if _DIGIT_RE.search(t)) / n) if n else 0.0,
            "n_structural_ref": structural_hits, "n_swedish_lexical": lexical_hits}


def classify_role(kind: str, st: dict, pct_of_edition: float, align_ratio: float | None) -> tuple[str, list[str]]:
    """(role, reasons) for ONE kind of span in ONE edition. `st` comes from `span_length_stats`."""
    n = st["n_spans"]
    if n == 0 or pct_of_edition < NEGLIGIBLE_PCT:
        return "negligible", [f"{pct_of_edition:.3f}% of the edition's words"]
    reasons: list[str] = []
    ref_pct = st["n_structural_ref"] / n
    citation_commentary = st["n_structural_ref"] >= 10 and ref_pct >= 0.20
    if (not citation_commentary and st.get("n_dup_eval", 0) >= MIN_SPANS and st.get("pct_dup", 0) >= ALT_EDITION_PCT):
        # Checked before the length and alignment-rate commentary signals: a parallel rendering of the verse is long and
        # aligns badly too, but what it is is a repetition, and it is handled differently from editorial notes.
        return "alternate", [f"{st['pct_dup']:.0%} of {st['n_dup_eval']} evaluable spans repeat at least "
                             f"{ALT_SPAN_SHARE:.0%} of their content words from elsewhere in the same verse "
                             f"(median {st['dup_median']:.2f})"]
    commentary_signals = []
    if st["max_words"] >= EXTREME_WORDS:
        commentary_signals.append(f"a span reaches {st['max_words']} words")
    if st["n_structural_ref"] >= 10 and ref_pct >= 0.20:
        commentary_signals.append(f"{st['n_structural_ref']} spans ({ref_pct:.0%}) are verse or book citations")
    if align_ratio is not None and align_ratio <= LOW_RATIO and st["median_words"] >= 6 and pct_of_edition >= 1.0:
        commentary_signals.append(f"long spans (median {st['median_words']:g} words) that align at {align_ratio:.2f} of the "
                                  f"outside rate")
    if commentary_signals:
        return "commentary", commentary_signals
    if n < MIN_SPANS:
        return "insufficient", [f"only {n} spans"]
    if align_ratio is not None:
        if align_ratio <= LOW_RATIO and kind == "brackets" and st["pct_short"] >= 0.6:
            return "supplied", [f"{st['pct_short']:.0%} of spans are <=3 words and they align at {align_ratio:.2f} of the "
                                f"outside rate (no source counterpart)"]
        if align_ratio >= HIGH_RATIO:
            if st["n_structural_ref"] >= 5 and ref_pct >= 0.05:
                return "mixed", [f"aligns like ordinary text ({align_ratio:.2f}) but {ref_pct:.0%} of spans are citations"]
            return "content", [f"aligns at {align_ratio:.2f} of the outside rate"]
        reasons.append(f"alignment ratio {align_ratio:.2f} is between {LOW_RATIO} and {HIGH_RATIO}")
    else:
        if st["pct_short"] >= 0.85 and st["max_words"] < 30:
            return "content", ["short spans only (no alignment data on disk)"]
        reasons.append("no alignment data on disk")
    if ref_pct >= 0.05:
        reasons.append(f"{ref_pct:.0%} of spans are citations")
    return "unclear", reasons


def alignment_rates(tag: str, usj_root: Path, out_dir: Path, brackets_re, parens_re, token_spans, read_ranges,
                    book_files: list[tuple[str, int]]) -> dict | None:
    """{'outside': (aligned, words), 'parens': (..), 'brackets': (..)} over NON-pooled verses, from the edition's eflomal
    output; None when the edition has no alignment files. Indices are raw-text token indices, which equal the aligner's
    only for editions with no strip rule (the caller must not pass one that has)."""
    from lexeme_aligner.align_files import tag_files
    files = tag_files(out_dir, "eflomal", tag)
    if not files:
        return None
    by_book: dict[str, Path] = {}
    for fp in files:
        m = re.search(r"_([0-9A-Z]{3})\.jsonl", fp.name)
        if m:
            by_book[m.group(1)] = fp
    acc = {"outside": [0, 0], "parens": [0, 0], "brackets": [0, 0]}
    edition_dir = usj_root / f"usj-{tag}"
    for book, num in book_files:
        if book not in by_book:
            continue
        usj = edition_dir / f"{num}-{book}.json"
        if not usj.exists():
            continue
        cov: dict[tuple[int, int], set[int]] = {}
        op = gzip.open if str(by_book[book]).endswith(".gz") else open
        with op(by_book[book], "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                s = cov.setdefault((r["chapter"], r["verse"]), set())
                for p in r["pairs"]:
                    if p.get("content"):
                        s.update(p.get("t_idx") or [])
        for (ch, vs), rng in read_ranges(usj, rules={}).items():
            if rng["verse_end"] != vs:
                continue
            text = " ".join(rng["text"]) if isinstance(rng["text"], list) else rng["text"]
            b = [(m.start(), m.end()) for m in brackets_re.finditer(text)]
            p = [(m.start(), m.end()) for m in parens_re.finditer(text)]
            cv = cov.get((ch, vs), set())
            for i, (s0, _e0) in enumerate(token_spans(text)):
                kind = ("brackets" if any(x <= s0 < y for x, y in b) else
                        "parens" if any(x <= s0 < y for x, y in p) else "outside")
                acc[kind][1] += 1
                acc[kind][0] += i in cv
    return {k: tuple(v) for k, v in acc.items()}


def ratio(rates: dict | None, kind: str) -> float | None:
    """inside-rate / outside-rate for `kind` ('parens'|'brackets'); None without data or without enough words."""
    if not rates:
        return None
    (ia, iw), (oa, ow) = rates[kind], rates["outside"]
    if iw < 50 or ow < 1000 or oa == 0:
        return None
    return (ia / iw) / (oa / ow)
