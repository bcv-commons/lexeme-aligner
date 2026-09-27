"""Spine-side occurrence mapping for `door43_align.py`'s raw `(strong, occurrence)` rows onto our
shared MACULA spine's own `(strong, k-th occurrence)` h_idx keys -- the "third-manual-layer
integration" step `door43_align.py`'s own module docstring names as not-yet-built for any of its 10
languages (roadmap item, tackled here for Hindi first, 2026-09-27).

SCOPE, HONESTLY BOUNDED (same pattern as `door43_align.py`'s own partial-scope notes): **Greek NT
only.** Door43's Hebrew OT rows use a DIFFERENT convention -- unfoldingWord's UHB tags each BOUND
MORPHEME of a Hebrew word with its own `\\zaln-s` and a morpheme-PREFIX-coded strong (`c:H1961` =
conjunction waw, `d:H8199` = definite article he, `b:H3117` = preposition bet; confirmed on real
Hindi RUT data) -- these don't correspond 1:1 to our own spine's prefix-splitting without a dedicated
prefix-code -> spine-token crosswalk. NOT attempted in this pass; a real follow-up, comparable in
scope to this Greek half, not a quick add.

Greek rows are simpler and handled in full here: unfoldingWord's `x-strong` is a bare "G####" plus
ONE trailing augment character (almost always "0" = no augmentation, occasionally a real letter for
an augmented variant) -- confirmed against real Hindi hi_glt data: 100% of non-null NT `x-strong`
values are exactly 6 characters (G + 4-digit body + 1 trailing char). Stripping that last character
and re-padding gives exactly the bare "G####" ROLLUP key our own spine's `HebToken.strong` already
is (see hebrew_source.py) -- the same key `pos_score`/`gold_to_fullalign` already key gold rows on,
so no augmented-variant crosswalk is needed for this bare-strong join.
"""
from __future__ import annotations

import collections
import unicodedata

from lexeme_aligner.hebrew_source import HebrewSource


def normalize_greek_strong(raw: str | None) -> str | None:
    """"G39720" -> "G3972"; None for anything not of that shape (Hebrew's `c:H1961`-style prefixed
    rows, or a missing/empty strong on a punctuation-only span)."""
    if not raw or not raw.startswith("G") or len(raw) < 3:
        return None
    body = raw[1:-1]
    if not body.isdigit():
        return None
    return f"G{int(body):04d}"


def _mark_stripped(s: str) -> str:
    """NFD + drop combining marks (Greek accents/breathings) -- the same convention `bsb_tables.
    _word_forms` uses, so a spine surface and a door43 `content` field compare on letters alone.
    Lowercased too: sentence-initial capitalization differs between the spine and door43's own
    source text independent of anything this module is checking."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def map_book(rows: list[dict], heb: HebrewSource, book: str) -> tuple[list[dict], dict]:
    """Attach `h_idx` (+ a `spine_surface` diagnostic field when matched) to each door43 row for one
    book. A row's `h_idx` stays `None` when unmatched: a non-Greek (prefixed Hebrew) strong, an
    occurrence count past what the spine has for that (verse, strong), or a verse the spine doesn't
    carry at all (should not happen for canonical NT books, kept as its own stat rather than silently
    dropped if it ever does).

    Deliberately does NOT trust door43's own `x-occurrence`/`x-occurrences` fields for the k-th-
    occurrence key -- confirmed on real Hindi hi_glt data (2026-09-27) that these are scoped to the
    ENCLOSING nested `\\zaln` milestone, not the whole verse, whenever alignment groups nest (a word
    repeated across two different parent phrase groups gets `occurrence`/`occurrences` re-numbered
    from 1 within each group, not the true verse-wide count) -- naively trusting them mismatched ~9%
    of rows against the spine's own surface. Instead recomputes k itself, sorted by `seq` (the span's
    OPEN order -- monotonic in real text position regardless of nesting; see `usj_spans_for_book`'s
    own comment) grouped by (verse, bare strong) -- the exact same `seen[strong]` counting convention
    the spine itself uses (hebrew_source.py / pos_score.load_spine), so the two sides count the same
    way by construction."""
    stats: collections.Counter = collections.Counter()
    by_verse: dict[tuple[int, int], list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_verse[(r["chapter"], r["verse"])].append(r)
    out: list[dict] = []
    for (ch, v), vrows in sorted(by_verse.items()):
        toks = heb.verse_tokens(book, ch, v)
        if not toks:
            stats["verse_not_in_spine"] += len(vrows)
            out.extend({**r, "h_idx": None} for r in vrows)
            continue
        seen: collections.Counter = collections.Counter()
        spine_k: dict[tuple[str, int], int] = {}
        surface_of: dict[int, str] = {}
        for t in toks:
            surface_of[t.idx] = t.surface
            if t.strong:
                spine_k[(t.strong, seen[t.strong])] = t.idx
                seen[t.strong] += 1
        # our own k-th-occurrence count over this verse's door43 rows, in true text (`seq`) order --
        # replaces trusting `occurrence`/`occurrences` directly (see docstring).
        bare_of: dict[int, str | None] = {}
        for r in vrows:
            raw_strong = r.get("strong")
            bare = normalize_greek_strong(raw_strong)
            bare_of[id(r)] = bare
            if raw_strong and not bare:
                stats["non_greek_strong"] += 1
        our_k: dict[int, int] = {}
        our_seen: collections.Counter = collections.Counter()
        for r in sorted(vrows, key=lambda r: r.get("seq", 0)):
            bare = bare_of[id(r)]
            if bare:
                our_k[id(r)] = our_seen[bare]
                our_seen[bare] += 1
        for r in vrows:
            bare = bare_of[id(r)]
            h_idx = spine_k.get((bare, our_k[id(r)])) if bare and id(r) in our_k else None
            stats["matched" if h_idx is not None else "unmatched"] += 1
            row_out = {**r, "h_idx": h_idx}
            if h_idx is not None:
                row_out["spine_surface"] = surface_of.get(h_idx)
            out.append(row_out)
    return out, stats


def verify_content_match(rows: list[dict]) -> dict:
    """For matched rows only: does door43's own `content` field (the Greek source word it tagged)
    look like the spine's own surface at the h_idx we matched it to? Mark-stripped equality/
    containment, never a guess -- this is the round-trip sanity check (same spirit as F1/BSB's
    round-trip test and the fra/spa gold-convention investigation earlier this session: verify a
    mapping against real content before trusting its aggregate stats)."""
    n = n_ok = 0
    mismatches = []
    for r in rows:
        if r.get("h_idx") is None or not r.get("content") or not r.get("spine_surface"):
            continue
        n += 1
        a, b = _mark_stripped(r["content"]), _mark_stripped(r["spine_surface"])
        if a == b or a in b or b in a:
            n_ok += 1
        elif len(mismatches) < 10:
            mismatches.append((r["book"], r["chapter"], r["verse"], r["content"], r["spine_surface"]))
    return {"checked": n, "content_matches": n_ok,
            "match_rate": n_ok / n if n else None, "sample_mismatches": mismatches}
