"""Maximum-compact per-verse alignment format — the token-level companion to `lexeme-alignments`
(which is aggregated/type-level and can't reconstruct what happened in any one verse).

Two artifacts:
  1. The CANONICAL ORDINAL INDEX — published ONCE, shared by every language: a flat array where
     index i -> "BOOK C:V" (raw spine verse order, OT_BOOKS then NT_BOOKS, EVERY verse incl. ones
     with zero content lexemes — so the index never has to change shape and any language's array is
     always the same length / same meaning at each position). Reverse lookup (ref -> ordinal) is
     NOT published — trivially derived client-side (`{ref: i for i, ref in enumerate(index)}`), and
     shipping it would just duplicate every "BOOK C:V" string a second time for free.
  2. Per-LANGUAGE compact array — same length as the index, position-parallel. Each entry is a
     string "srcOrd:span srcOrd:span ..." where srcOrd is the 0-based ordinal among that verse's
     CONTENT lexemes only (in spine order — an unaligned lexeme's ordinal is simply absent, no null
     padding) and span is a target-token-index single int / contiguous "a-b" range / scattered
     "a,b,c" list. A verse with no aligned content lexeme (or no target text at all) is "".

     Uses run_pilot.pooled_verse_groups() — the SAME range-pooling + idx-renumbering logic
     build_corpus()/gapfill.py use — so a PKF-style "M-N" range's ANCHOR verse's string covers every
     content lexeme in the WHOLE pooled range (ordinals span the group), and non-anchor member
     verses are "" (their translation lives in the anchor's combined block, not a real gap).

     Alignment source = the ADDITIVE UNION of eflomal+gloss+gapfill jsonl (align_files.tag_files,
     exact-tag matched — see the 2026-07-24 sibling-tag glob bug), same "what's actually aligned"
     definition used throughout this session's reverse-check work.

    python3 -m lexeme_aligner.compact_align --build-index --out resources/canonical_index/whole_bible.json
    python3 -m lexeme_aligner.compact_align --iso ind --index resources/canonical_index/whole_bible.json \\
        --out out/compact_ind.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT
from lexeme_aligner.hebrew_source import HebrewSource
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, _BOOK_FILE_NUM, pooled_verse_groups
from lexeme_aligner.usj_source import read_verse_ranges, remap_clean_to_raw
from lexeme_aligner.versification import remapper

ALL_BOOKS = OT_BOOKS + NT_BOOKS

_SCHEMA = ["_index/<BOOK>.json = [\"BOOK C:V\", ...] — shared verse-ref index, published once per book",
          "<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json = [\"srcOrd:span ...\", ...] — position-parallel "
          "to that book's _index/<BOOK>.json; hash = last N hex chars of book_content_hash()"]


def update_manifest(path: Path, iso: str, edition: str, entry: dict) -> None:
    """Merge one (iso, edition)'s entry into the deterministic (sorted, timestamp-free) manifest —
    same pattern as export_lex.py's own update_manifest, so both datasets' manifests stay diffable the
    same way. A language can carry several editions (each independently aligned/hashed)."""
    doc = {"schema": _SCHEMA, "languages": {}}
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = _SCHEMA
    doc.setdefault("languages", {}).setdefault(iso, {}).setdefault("editions", {})[edition] = entry
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def build_index(heb: HebrewSource, books: list[str] = ALL_BOOKS) -> list[str]:
    """Flat ["BOOK C:V", ...] — raw spine verse order, EVERY verse (see module docstring)."""
    index = []
    for book in books:
        for ch in heb.chapters(book):
            for v in heb.verses(book, ch):
                index.append(f"{book} {ch}:{v}")
    return index


def build_source_lexemes(heb: HebrewSource, book: str) -> dict[str, list[str]]:
    """{"BOOK C:V": [lexeme, lexeme, ...]} — the CONTENT lexemes of that verse, in source order, using
    the RAW (unpooled) spine verse — i.e. exactly what `srcOrd` in a compact string counts, published
    directly so a client never has to reconstruct `is_content` themselves.

    Motivating case (live client feedback): H0853 (the Hebrew direct-object marker) and its rare noun
    homonym share the SAME bare Strong's/lexeme id in both our spine and a client's own lexicon — no
    letter suffix distinguishes them. Our own `is_content` isn't derived from lexeme/Strong's at all;
    it's MACULA's per-occurrence `class` field (noun/verb/adj vs particle) — the exact same kind of
    per-occurrence morphological signal a client would otherwise have to read from THEIR OWN corpus
    (grammar/POS tag) and hope it agrees with ours. Publishing the already-resolved content-lexeme
    sequence removes that requirement entirely: a client decodes `srcOrd:span` as
    `build_source_lexemes(...)["BOOK C:V"][srcOrd]` — a pure array lookup, no morphology involved.

    EDITION-INDEPENDENT and shared like `_index/<BOOK>.json` — this is the source (Hebrew/Greek) side
    only, published ONCE per book, not per edition. CAVEAT (pooled target-verse ranges): this list is
    per RAW spine verse; an edition whose own verse-range markers pool several source verses into one
    anchor (see module docstring) needs the raw per-verse lists of every member verse concatenated, in
    verse order, to match that edition's own combined ordinal count — same as how `""` on a non-anchor
    member verse already signals "folded into the anchor" for compact strings themselves.

    Lexemes are published WITHOUT the `lang:` prefix (`lexeme-alignments`' full `lang:augmented-strong`,
    e.g. `hbo:0430`, becomes plain `0430`) — redundant here specifically: a BOOK is always entirely one
    testament (OT_BOOKS/NT_BOOKS never overlap), so every entry in one file shares the same implicit
    `hbo`/`grc` prefix; the book code itself already tells a client which one. NOT safe to drop in a
    context that mixes testaments (e.g. `lexeme-alignments`, one partition per LANGUAGE spanning both)."""
    out: dict[str, list[str]] = {}
    for ch in heb.chapters(book):
        for v in heb.verses(book, ch):
            toks = heb.verse_tokens(book, ch, v)
            out[f"{book} {ch}:{v}"] = [t.lexeme.split(":", 1)[-1] for t in toks
                                       if t.strong and t.is_content]
    return out


def _encode_span(t_idx: list[int]) -> str:
    if len(t_idx) == 1:
        return str(t_idx[0])
    lo, hi = min(t_idx), max(t_idx)
    if hi - lo + 1 == len(t_idx):                      # contiguous
        return f"{lo}-{hi}"
    return ",".join(str(i) for i in sorted(t_idx))


def _merged_pairs(iso: str, book: str, out_dir: Path, methods=("eflomal", "gloss", "gapfill")) -> dict:
    """{(chapter, verse): {h_idx: t_idx}} — additive union, first method wins on overlap
    (mirrors merge_align's own priority order: eflomal > gloss > gapfill)."""
    by_verse: dict[tuple[int, int], dict[int, list[int]]] = {}
    for m in methods:
        for fp in tag_files(out_dir, m, iso):
            if fp.stem.rsplit("_", 1)[-1] != book:
                continue
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                d = by_verse.setdefault((rec["chapter"], rec["verse"]), {})
                for p in rec["pairs"]:
                    ti = p.get("t_idx")
                    if not ti or p["h_idx"] in d:
                        continue
                    d[p["h_idx"]] = ti
    return by_verse


def build_compact(iso: str, usj_dir: Path, heb: HebrewSource, out_dir: Path = OUT,
                  books: list[str] = ALL_BOOKS, methods=("eflomal", "gloss", "gapfill")) -> list[str]:
    """Per-language compact array, position-parallel to build_index()'s canonical ordinal index.

    Target-token positions (`span` in "srcOrd:span") are published in RAW-TEXT coordinates — indices
    into `tokenize()` of the edition's UNMODIFIED verse text — even though the alignment itself ran
    against opt-in-cleaned text (config/text_strip_rules.json; see usj_source.py). `_merged_pairs`'s
    `t_idx` values are clean-text indices (that's what the aligner actually tokenized); `remap_clean_to_raw`
    converts each one using a diff between this same verse block's raw and clean text. This matters for
    two reasons: (1) `book_content_hash` (below) hashes raw text, and a client always tokenizes ITS OWN
    untouched copy of the verse — clean-basis positions would silently resolve to the wrong word on any
    edition with an active strip rule; (2) it gives "word inside a stripped span" the correct semantics
    for free — such a word exists in the raw token stream but was never part of what the aligner saw, so
    it never gets a mapped position and is simply absent from the compact string, which already means
    "unaligned" under this format's existing convention. No special-casing needed for stripped content;
    it just never appears as a source of a valid raw_idx."""
    remap = remapper(iso, str(usj_dir))
    by_ref: dict[str, str] = {}                        # "BOOK C:V" -> compact string, filled as we go
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        ranges = read_verse_ranges(usj_path) if usj_path.exists() else {}
        raw_ranges = read_verse_ranges(usj_path, rules={}) if usj_path.exists() else {}
        pairs_by_verse = _merged_pairs(iso, book, out_dir, methods)
        for ch in heb.chapters(book):
            for anchor_v, vs, ve, text, members in pooled_verse_groups(book, ch, heb, ranges, remap):
                pairs = pairs_by_verse.get((ch, anchor_v), {})
                anchor_content = [tok for orig_v, tok in members if orig_v == vs and tok.strong and tok.is_content]
                # Same (tc, vs) key pooled_verse_groups used internally to fetch `text` (see its
                # docstring/source) — re-derived here to fetch the RAW counterpart of the exact same
                # target block, so the two texts being diffed are guaranteed to correspond.
                tc, _tv = (remap(book, ch, anchor_v)[1:] if remap else (ch, anchor_v))
                raw_info = raw_ranges.get((tc, vs))
                raw_text = raw_info["text"] if raw_info else ""
                raw_idx_of = remap_clean_to_raw(raw_text, text) if raw_text else []
                parts = []
                for ordinal, tok in enumerate(anchor_content):
                    ti = pairs.get(tok.idx)
                    if not ti:
                        continue
                    mapped = [raw_idx_of[i] for i in ti if i < len(raw_idx_of) and raw_idx_of[i] >= 0]
                    if mapped:
                        parts.append(f"{ordinal}:{_encode_span(mapped)}")
                by_ref[f"{book} {ch}:{anchor_v}"] = " ".join(parts)
                for orig_v, _tok in members:
                    if orig_v != vs:
                        by_ref.setdefault(f"{book} {ch}:{orig_v}", "")   # pooled non-anchor member
    return by_ref


def book_content_hash(usj_path: Path) -> str:
    """SHA-256 hex digest over a book's TRANSLATABLE TEXT ONLY (not its USJ file bytes/structure) — a
    reader must be able to reproduce this from any copy of the same edition's text, in any container
    format (USJ, USFM, plain verse dump), so the hash basis is the WORDS, not our JSON serialization.
    Deliberately sensitive to any revision: verse text is joined as `"{chapter}:{verse}:{text}"` per
    verse (own-verse `\\n`-joined, ascending chapter/verse) — so a wording change, a re-versification
    (verse split/merge), OR a chapter/verse renumbering all change the hash, per the "extremely sensitive
    to any word difference" requirement. Caller truncates to the last N hex chars for the filename; the
    full digest is returned here so a client wanting stronger collision resistance always has it.

    Hashed over RAW text (`rules={}`, bypassing config/text_strip_rules.json), never the aligner's
    internal cleaned text — a client verifying "is this the same edition I have" only ever has the
    unmodified source file, and this promise predates and is independent of that internal cleaning
    step (see build_compact's docstring for how alignment positions, which DO need the clean/raw
    distinction, are handled separately)."""
    ranges = read_verse_ranges(usj_path, rules={})
    parts = [f"{ch}:{v}:{info['text']}" for (ch, v), info in sorted(ranges.items())]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def edition_id(iso: str, tag: str, sources: dict) -> str:
    """The path's `<edition>` segment: iso-prefixed so it's self-describing even out of context, but
    without DOUBLE-prefixing an edition string that already carries the iso (helloAO-sourced tags like
    `arb_vdv`/`swe_fol` already do; CDN/PKF-sourced ones like `BSB` don't). Derived from `data/sources.json`
    (`source.edition` — the SAME string already published as `base_text` in `lexeme-alignments`, so this
    reuses an existing identifier rather than inventing a new one) with a same-tag fallback if the tag
    isn't in `sources` at all. e.g. iso=eng tag=bsb source.edition='BSB' -> 'eng_BSB';
    iso=arb tag=arb_vdv source.edition='arb_vdv' -> 'arb_vdv' (no double prefix)."""
    ed = (sources.get(tag) or {}).get("edition") or tag
    return ed if ed.lower().startswith(iso.lower()) else f"{iso}_{ed}"


def publish_compact(tag: str, iso: str, usj_dir: Path, heb: HebrewSource, out_root: Path,
                    books: list[str] = ALL_BOOKS, methods=("eflomal", "gloss", "gapfill"),
                    out_dir: Path = OUT, hash_len: int = 5, edition: str | None = None,
                    sources_path: Path = Path("config/sources.json"),
                    index_root: Path = Path("config/canonical_index")) -> dict[str, Path]:
    """Writes one compact-alignment JSON per (edition, book) at
    `<out_root>/<iso[0]>/<iso>/<edition>/<BOOK>_<last-hash_len-hex-of-book-content-hash>.json` — `iso` is
    the true published language code (NOT the internal alignment `tag`, which can be an edition-specific
    id like `arb_vdv`), `edition` defaults to `edition_id(iso, tag, ...)` unless overridden. The content
    hash makes the filename itself the integrity check: a client hashes ITS OWN copy of the book's text
    (see `book_content_hash`) and looks for a matching file; if the edition was revised since this was
    published, no matching filename exists, so a stale alignment is never silently served.

    Per-edition files are a PLAIN ARRAY of compact strings, position-parallel to that book's own shared
    `index_root/<BOOK>_lexemes.json` — `{"BOOK C:V": [lexeme, ...], ...}` (see `build_source_lexemes`) —
    published ONCE, shared by EVERY edition of EVERY language (the verse structure is spine-derived,
    identical regardless of which edition aligns to it). There is deliberately NO separate flat
    `["BOOK C:V", ...]` ref-index file: it would be a pure, lossless subset of `_lexemes.json`'s own
    (ordered) keys — verified byte-identical in content and order — so publishing both would just
    duplicate every ref string a second time for free (same "don't publish a pure derivation of
    something already published" principle as not shipping the reverse ref->ordinal index, or `strong`/
    `share` in lexeme-alignments). A client derives the ref list with one line — see the README. Written
    lazily here (only if missing), so the first edition published for a book creates it and every
    subsequent edition just reuses it — no ref string is ever repeated across the hundreds of edition
    files that will eventually exist for one book.
    Returns {book: written_path} for books that actually have target text (skips ones that don't)."""
    if edition is None:
        sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
        edition = edition_id(iso, tag, sources)
    written: dict[str, Path] = {}
    by_ref = build_compact(tag, usj_dir, heb, out_dir, books, methods)
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not usj_path.exists():
            continue
        book_refs = [ref for ref in by_ref if ref.startswith(f"{book} ")]
        if not book_refs:
            continue
        lexemes_fp = index_root / f"{book}_lexemes.json"
        if not lexemes_fp.exists():
            lexemes_fp.parent.mkdir(parents=True, exist_ok=True)
            lexemes_fp.write_text(json.dumps(build_source_lexemes(heb, book), ensure_ascii=False) + "\n",
                                  encoding="utf-8")
        book_index = list(json.loads(lexemes_fp.read_text(encoding="utf-8")).keys())
        array = [by_ref.get(ref, "") for ref in book_index]
        digest = book_content_hash(usj_path)[-hash_len:]
        out_fp = out_root / iso[0] / iso / edition / f"{book}_{digest}.json"
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        out_fp.write_text(json.dumps(array, ensure_ascii=False) + "\n", encoding="utf-8")
        written[book] = out_fp
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-index", action="store_true", help="write the shared canonical ordinal index")
    ap.add_argument("--iso", default=None, help="internal alignment TAG (align_<method>_<tag>_*.jsonl) — "
                    "the language's TRUE published iso for --publish is --publish-iso, below")
    ap.add_argument("--publish-iso", default=None,
                    help="true published language code for --publish's path (default: same as --iso) — "
                         "set when the tag differs from the iso, e.g. --iso arb_vdv --publish-iso arb")
    ap.add_argument("--usj-dir", type=Path, default=None)
    ap.add_argument("--index", type=Path, default=Path("config/canonical_index/whole_bible.json"),
                    help="the canonical ordinal index (read to align array positions, or write with --build-index)")
    ap.add_argument("--methods", default="eflomal,gloss,gapfill")
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--out", type=Path, default=None,
                    help="single whole-bible array output path (dev/debug mode)")
    ap.add_argument("--publish", type=Path, default=Path("publish/compact-alignments"),
                    help="root dir for the per-book, hash-named publish layout "
                         "(<root>/<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json) — pass --no-publish to "
                         "use the whole-bible dev/debug mode (--out) instead")
    ap.add_argument("--no-publish", action="store_true",
                    help="use the whole-bible single-array dev/debug mode (--out) instead of --publish")
    ap.add_argument("--edition", default=None,
                    help="edition path segment for --publish (default: derived from data/sources.json)")
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    ap.add_argument("--index-root", type=Path, default=Path("publish/compact-alignments/_index"),
                    help="root for the shared per-book verse-ref indices (--publish mode)")
    ap.add_argument("--hash-len", type=int, default=5)
    args = ap.parse_args()

    heb = HebrewSource()

    if args.build_index:
        out = args.out or Path("config/canonical_index/whole_bible.json")
        index = build_index(heb)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(index, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[compact_align] {len(index)} verse(s) → {out}", file=sys.stderr)
        return 0

    if not args.iso:
        raise SystemExit("--iso required (or use --build-index)")
    usj_dir = args.usj_dir or Path(f"pipeline/work/ingest-cache/usj-{args.iso}")
    methods = tuple(m.strip() for m in args.methods.split(","))

    if not args.no_publish:
        publish_iso = args.publish_iso or args.iso
        sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
        resolved_edition = args.edition or edition_id(publish_iso, args.iso, sources)
        written = publish_compact(args.iso, publish_iso, usj_dir, heb, args.publish,
                                  methods=methods, out_dir=args.out_dir, hash_len=args.hash_len,
                                  edition=resolved_edition, sources_path=args.sources,
                                  index_root=args.index_root)
        for book, fp in sorted(written.items()):
            print(f"[compact_align] {publish_iso}/{book} → {fp}", file=sys.stderr)
        manifest_entry = {"tag": args.iso, "books": sorted(written),
                          "source": sources.get(args.iso, {})}
        update_manifest(args.publish / "manifest.json", publish_iso, resolved_edition, manifest_entry)
        print(f"[compact_align] {publish_iso}/{resolved_edition}: {len(written)} book file(s) written "
              f"under {args.publish}, manifest updated", file=sys.stderr)
        return 0

    if not args.out:
        raise SystemExit("--out required for the whole-bible array mode (or use --publish)")
    index = json.loads(args.index.read_text(encoding="utf-8"))
    by_ref = build_compact(args.iso, usj_dir, heb, args.out_dir, methods=methods)
    array = [by_ref.get(ref, "") for ref in index]

    n_aligned_verses = sum(1 for s in array if s)
    n_pairs = sum(s.count(":") for s in array)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(array, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[compact_align] {args.iso}: {n_aligned_verses}/{len(array)} verse(s) with ≥1 aligned lexeme, "
          f"{n_pairs} aligned pair(s) → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
