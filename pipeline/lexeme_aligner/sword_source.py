"""SWORD Strong's-tagged module -> the same gold parquet schema Clear-Bible attestations use
(`strong, lemma, surface, ref, target_id, source_id, method, source_corpus, base_text`), so
`pos_score.load_gold` / `contest_rule` / `score_gapfill` work on it UNCHANGED. Step G0 of
internal-docs/aim1-typology-source-structure-plan.md.

WHAT A SWORD MODULE GIVES US — TWO DIFFERENT TAG SHAPES depending on the module's own `SourceType`:
  - **OSIS** (most modules, incl. SpaRV1909/FreSegond1910/ChiUns — the "case varies" of the attribute
    name, `lemma="strong:H1234"` vs `lemma="Strong:H1234"`, is a module quirk, NOT a format
    difference; both parse the same way): the target word is WRAPPED, `<w lemma="strong:H1234">
    surface</w>` — occasionally several ids on one tag when one target word/phrase renders more than
    one source word (`<w lemma="strong:H0622 strong:H0235"> </w>`), and occasionally a MULTI-WORD
    surface inside one tag when the tagger already grouped a contiguous span (`<w
    lemma="Strong:H7225">EN el principio</w>` — SpaRV1909 tags "en el principio" as one span for one
    Hebrew word). Parsed by `extract_verse_rows`.
  - **GBF** (RusVZh is the only one checked so far): the Strong's number is a separate POSTFIX tag,
    `<WG1234>`, placed immediately after the word it annotates with NO wrapping and NO space — and
    several can stack directly (`возлюбил<WG25><WG3588>` — one Russian word rendering both the Greek
    verb and its accompanying article). Other GBF markup (`<FR>`/`<Fr>` red-letter-words spans, etc.)
    can sit BETWEEN the word and its tag (`<FR>возлюбил<Fr><WG25>`) and is stripped as noise before
    parsing — it carries no lexical information for us. Parsed by `extract_verse_rows_gbf`.
`_format_for` auto-detects which parser a module needs from its own `.conf`'s `SourceType=` line, so
callers (and `build_gold`) never need to specify it.

No source-side occurrence index is given by either format (unlike Door43's `x-occurrence`/
`x-occurrences`), so — exactly as aim1-typology-source-structure-plan.md's Step G0 says — we key by
(strong, k-th occurrence of that strong IN DOCUMENT ORDER) exactly the way `pos_score.load_gold`
already keys Clear's `source_id` ordering for its own per-verse rows; this is an approximation
(target word order is not always source word order) but the same one Clear's own transfer rows and
this module's positional design already assume, and `pos_score` already drops a link whose occurrence
counts disagree between sides.

MEASURED (2026-09-24): SpaRV1909's and FreSegond1910's plain (cleaned) text is CHARACTER-IDENTICAL to
our already-ingested `spa_r09` / `fra-lsg` editions (checked Gen 1:1, Matt 1:1) — both modules ARE the
Reina-Valera 1909 / Segond 1910 translations we already aligned, not a different revision. So for these
two languages this is a SECOND, INDEPENDENT TAGGING of a translation we already have base-chain output
for: no new ingest, no new eflomal/gloss run, no versification remap — score the existing
align_eflomal_spa_r09_*/align_eflomal_fra-lsg_* files directly against this new gold via
`pos_score --base-text <name> --gold-method sword`. A language whose SWORD text is a genuinely
DIFFERENT edition needs the full path (§2.6/Step G0 in the plan): ingest the SWORD text as its own
edition, run the base chain, register `{edition, base_text}` in `config/gold_langs.json`.

MORPH-CODE CONTAMINATION (found on ChiUns, not on SpaRV1909/FreSegond1910): some modules' Hebrew
tagging embeds a MORPHOLOGY pseudo-code as if it were a second Strong's id on the same tag, e.g.
`<w lemma="strong:H1254 strong:H8804 strong:H0853">...` where `H8804` is a Sword/OSIS morph code
("Qal Perfect"), not a real Strong's number (Strong's Hebrew tops out at H8674). `_valid_strong`
filters out anything above the real Strong's dictionary range so it never gets treated as a lexeme
entry — see that function's own docstring for the exact bound and the tradeoff it makes.

DECRYPTION: some CrossWire zText modules ship "encrypted" with a Sapphire cipher whose key is
published in the module's own `.conf` (`CipherKey=`) — a packaging formality, not real access
control (SpaRV1909 is one; the key is literally the module name). `_cipherkey_for` reads it from the
extracted `mods.d/*.conf` automatically so callers never need to know or pass it.

LICENSING — read before adding a new module to the default set (see internal-docs/
aim1-typology-source-structure-plan.md §2.6 and §8 for the full per-module table):
  - `DistributionLicense=Public Domain` (e.g. ChiUns) — safe to vendor AND, if ever wanted, publish
    derived data from.
  - `DistributionLicense=Copyrighted; Permission to distribute granted to CrossWire` (SpaRV1909,
    FreSegond1910, RusVZh) — CrossWire itself is authorized to distribute the module; that is NOT a
    blanket redistribution license to a third party. Treated here as VENDOR-ONLY / INTERNAL-SCORING-
    ONLY, same as the karnbibeln lexicon precedent in `senses_attested`: fetched into a gitignored
    vendor directory, used only to compute our own alignment scores, never committed, never
    published, never redistributed as text or as a derived per-verse dataset.
  - A Creative Commons NC/ND license (e.g. GerLeoNA28/Leonberger, CC BY-NC-ND 4.0) is NOT handled by
    this module and was deliberately NOT fetched into the gold set this session — ND specifically
    restricts creating/sharing adapted material, a materially different and stricter question than
    the "Copyrighted, distribute via CrossWire" modules above; left for an explicit decision rather
    than assumed.

    python3 -m lexeme_aligner.sword_source --module <extracted-module-dir> --lang <bare-iso> \\
        --base-text Segond1910 --out pipeline/vendor/resources/strongs/attestations/fra.parquet \\
        [--merge] [--book MAT ...] [--all]
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

from lexeme_aligner.pos_score import clear_tokens
from lexeme_aligner.refs import BOOK_NUMBERS
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS

_W_TAG = re.compile(r'<w\s+lemma="([^"]*)"[^>]*>(.*?)</w>', re.DOTALL)
_STRONG_ID = re.compile(r'[Ss]trong:([GH])0*(\d+)([a-z]?)')

# GBF: a bare postfix Strong's tag, `<WG1234>` / `<WH1234>` — see module docstring.
_GBF_STRONG_TAG = re.compile(r'<W([GH])0*(\d+)>')
# Any OTHER GBF bracket markup (red-letter `<FR>`/`<Fr>`, etc.) — stripped before parsing, since it
# can sit between a word and its own Strong's tag (`<FR>word<Fr><WG1234>`) and carries nothing we use.
_GBF_OTHER_TAG = re.compile(r'<(?!/?W[GH]\d+>)[^>]*>')

# Strong's dictionary size: Hebrew/Aramaic tops out at H8674, Greek at G5624 (both editions in
# practice cap lower, but real published numbering never exceeds these). A "strong:" id above this is
# a morph pseudo-code some modules embed on the same tag (see module docstring), not a lexeme —
# dropped rather than emitted as a bogus gold row. A tradeoff, not a proof: a module could in
# principle assign a real (if unlikely) id in that range for something else; none of the modules
# checked this session do.
_MAX_STRONG = {"H": 8674, "G": 5624}


def _valid_strong(letter: str, num: int) -> bool:
    return num >= 1 and num <= _MAX_STRONG[letter]


def normalize_strong(letter: str, num: int, suffix: str) -> str:
    return f"{letter}{num:04d}{suffix}"


def parse_strong_ids(lemma_attr: str) -> list[str]:
    """Every valid strong:/Strong: id in a `lemma="..."` attribute, normalized, morph-codes dropped,
    order preserved (a tag can legitimately carry two real ids, e.g. a construct chain rendered as one
    target word)."""
    out = []
    for letter, num_s, suffix in _STRONG_ID.findall(lemma_attr):
        num = int(num_s)
        if _valid_strong(letter, num):
            out.append(normalize_strong(letter, num, suffix))
    return out


def _cipherkey_for(module_dir: Path) -> str | None:
    """Read `CipherKey=` from the module's own mods.d/*.conf, if present — see module docstring.
    `module_dir` is typically .../<extracted-root>/modules/texts/ztext/<modname>, and `mods.d` is a
    SIBLING of `modules/` at the extracted root — i.e. up to 5 levels above `module_dir`, not 4 (a
    real off-by-one caught testing on SpaRV1909: 4 levels stops at `modules/` itself and never checks
    its parent, silently returning None and leaving an encrypted module undecrypted -> garbage/empty
    text -> zero verses extracted, no exception raised)."""
    conf_dir = module_dir
    for _ in range(6):
        candidate = conf_dir / "mods.d"
        if candidate.is_dir():
            for fp in candidate.glob("*.conf"):
                text = fp.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"(?m)^CipherKey\s*=\s*(\S+)", text)
                if m:
                    return m.group(1)
            return None
        if conf_dir.parent == conf_dir:
            break
        conf_dir = conf_dir.parent
    return None


def _format_for(module_dir: Path) -> str:
    """"osis" or "gbf", from the module's own `SourceType=` line — same conf-walking approach as
    `_cipherkey_for` (mods.d is up to 5 levels above module_dir). Defaults to "osis" (the common case)
    if no conf is found or the value is unrecognized, rather than raising — a caller can always pass
    `fmt=` explicitly to override."""
    conf_dir = module_dir
    for _ in range(6):
        candidate = conf_dir / "mods.d"
        if candidate.is_dir():
            for fp in candidate.glob("*.conf"):
                text = fp.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"(?mi)^SourceType\s*=\s*(\S+)", text)
                if m:
                    return "gbf" if m.group(1).strip().lower() == "gbf" else "osis"
            return "osis"
        if conf_dir.parent == conf_dir:
            break
        conf_dir = conf_dir.parent
    return "osis"


def _memoize_decompression(bible) -> None:
    """pysword's own `_decompressed_text` has NO cache (`bible.py`): every single `get()` call
    re-reads AND re-decompresses (and, for an encrypted module, re-decrypts byte-by-byte in pure
    Python — `Sapphire.decrypt_bytes` loops one byte at a time) the WHOLE compressed BLOCK the verse
    lives in. `BlockType=BOOK` (every module checked this session) means one block IS a whole book —
    so reading a book verse-by-verse (what a naive per-verse loop does) redecrypts the ENTIRE book
    from scratch on every single verse: ~1,500x too much work for Genesis, and the reason a first cut
    of this function hung for minutes on the one encrypted module tested (SpaRV1909; unencrypted
    FreSegond1910 skips the byte-at-a-time decrypt and was fine). Monkeypatches the bound method with
    an LRU cache keyed on (testament, buf_num) — safe because a SwordBible instance is read-only and
    lives only for the duration of one `iter_module_verses` call."""
    import functools
    bible._decompressed_text = functools.lru_cache(maxsize=None)(bible._decompressed_text)


def iter_module_verses(module_dir: Path, cipherkey: str | None = None):
    """Yield (usfm_book, chapter, verse, raw_osis_text) for the whole Bible, whatever testaments the
    module has. Book mapping: pysword's own canon-order OT/NT lists zipped against ours (both
    canonical GEN..MAL / MAT..REV order) rather than a hand-maintained 66-row OSIS-abbreviation table.
    `cipherkey`: auto-detected from the module's own conf when None."""
    from pysword.bible import SwordBible  # local import: pysword is an optional (G0) dependency

    if cipherkey is None:
        cipherkey = _cipherkey_for(module_dir)
    bible = SwordBible(str(module_dir), cipherkey=cipherkey)
    _memoize_decompression(bible)
    structure = bible.get_structure()
    py_books = structure.get_books()
    our_order = {"ot": OT_BOOKS, "nt": NT_BOOKS}
    for testament, books in py_books.items():
        usfm_codes = our_order.get(testament, [])
        if len(usfm_codes) != len(books):
            print(f"[sword_source] WARNING: {testament} book count mismatch "
                  f"(pysword {len(books)} vs ours {len(usfm_codes)}) — skipping testament", file=sys.stderr)
            continue
        for usfm, book in zip(usfm_codes, books):
            for ch in range(1, book.num_chapters + 1):
                n_verses = book.chapter_lengths[ch - 1]
                for v in range(1, n_verses + 1):
                    raw = bible.get(books=book.name, chapters=ch, verses=v, clean=False)
                    if raw and raw.strip():
                        yield usfm, ch, v, raw


def extract_verse_rows(raw_text: str, ref: str, base_text: str, source_corpus: str
                       ) -> tuple[list[dict], str] | tuple[None, str]:
    """Gold rows for one verse, in `pos_score.load_gold`'s exact schema — `target_id`'s trailing 3
    digits are a `pos_score.clear_tokens` index (1-based), NOT our own `usj_source.tokenize` index:
    `load_gold` always re-tokenizes the edition text with `clear_tokens` and maps that to our own
    tokenization via `map_positions`, for EVERY gold source (Clear's own rows included) — so a SWORD
    row must count target words the same way for that translation step to work unchanged. Occurrence
    index `k` is assigned per strong id in the order its TAG appears in the raw text (document/target
    order — see module docstring).

    `clear_tokens` has a small cross-token lookahead (an apostrophe or hyphen can pull in the START of
    the NEXT token even across whitespace — see its own docstring). Tokenizing each tag/gap SEGMENT
    separately, as this function must (a `<w>` tag's own word count has to be known to place it), can
    only disagree with tokenizing the WHOLE verse at once in that one narrow case: a word split exactly
    at a tag boundary by a hyphen/apostrophe. Detected, not guessed around: if the per-segment token
    count doesn't sum to the whole-verse count, the verse is dropped (returns `(None, clean_text)`) —
    same "refuse rather than misplace" discipline `pos_score.load_gold` itself already uses for a
    verse it can't reconcile.

    `source_id`'s own trailing ordinal must be a per-VERSE, monotonically increasing sequence number
    (one per emitted (tag, strong) pair, in document order) — NOT a per-strong occurrence count: k is
    something `load_gold` re-derives itself, by sorting all of a verse's `source_id`s by that trailing
    number and counting each strong's occurrences in THAT order (`pos_score.load_gold`'s own
    `per_source`/`seen` loop). Two DIFFERENT strongs both at their own first occurrence would both want
    ordinal 0 if this used a per-strong counter — an actual bug caught while building this, since
    `source_id` is a dict key there and a collision silently drops one of them."""
    rows: list[dict] = []
    clean_parts: list[str] = []
    pos = 0
    word_idx = 0                                                 # 0-based clear_tokens index so far
    tag_seq = 0                                                   # per-verse, per-(tag,strong) ordinal
    pending: list[tuple[str, list[int], str]] = []                # (surface, target_positions, lemma_attr)
    for m in _W_TAG.finditer(raw_text):
        gap = raw_text[pos:m.start()]
        clean_parts.append(gap)
        word_idx += len(clear_tokens(gap))
        lemma_attr, surface = m.group(1), m.group(2)
        clean_parts.append(surface)
        n_words = len(clear_tokens(surface))
        target_positions = list(range(word_idx, word_idx + n_words))
        word_idx += n_words
        pending.append((surface, target_positions, lemma_attr))
        pos = m.end()
    tail = raw_text[pos:]
    clean_parts.append(tail)
    word_idx += len(clear_tokens(tail))
    clean_text = "".join(clean_parts)

    if word_idx != len(clear_tokens(clean_text)):
        return None, clean_text                                  # tag-boundary tokenization mismatch — drop

    for surface, target_positions, lemma_attr in pending:
        for strong in parse_strong_ids(lemma_attr):
            for pos_idx in target_positions:
                rows.append({
                    "strong": strong, "lemma": "", "surface": surface,
                    "ref": ref, "target_id": f"{ref}{pos_idx + 1:03d}",
                    "source_id": f"n{ref}{tag_seq:03d}",
                    "method": "sword", "source_corpus": source_corpus, "base_text": base_text,
                })
            tag_seq += 1
    return rows, clean_text


def extract_verse_rows_gbf(raw_text: str, ref: str, base_text: str, source_corpus: str
                           ) -> tuple[list[dict], str] | tuple[None, str]:
    """The GBF counterpart of `extract_verse_rows` — see module docstring for the tag shape. Every
    Strong's tag here attaches to exactly ONE preceding target word (GBF has no OSIS-style multi-word
    span tagging), so unlike the OSIS parser there is only ever one target position per row, and
    STACKED tags on one word (`возлюбил<WG25><WG3588>`) become separate rows sharing that one position
    rather than needing a shared `source_id` for several positions.

    Approach: strip non-Strong's GBF markup first (it can sit between a word and its own tag), then
    split the remaining text on Strong's tags. Splitting on a 2-group regex gives `[text_0, letter_1,
    digits_1, text_1, letter_2, digits_2, text_2, ...]` — `text_k` is exactly the text BETWEEN tag k
    and tag k+1 (or before tag 1, for k=0), so the word tag k+1 attaches to is the LAST clear_tokens
    token of everything seen so far. An EMPTY `text_k` (stacked tags, nothing between them) correctly
    leaves that "last token" position unchanged, so both tags land on the same word."""
    stripped = _GBF_OTHER_TAG.sub("", raw_text)
    parts = _GBF_STRONG_TAG.split(stripped)
    literal_chunks = parts[0::3]
    letters, digit_strs = parts[1::3], parts[2::3]
    clean = "".join(literal_chunks)                                # tags removed — this is our own edition's text

    rows: list[dict] = []
    word_idx = 0                                                  # running clear_tokens count so far
    last_word_pos: int | None = None                               # 0-based index of the most recent word
    tag_seq = 0
    for k, chunk in enumerate(literal_chunks):
        n_tokens = len(clear_tokens(chunk))
        if n_tokens:
            word_idx += n_tokens
            last_word_pos = word_idx - 1
        if k >= len(letters):
            continue                                               # trailing text after the last tag
        letter, num = letters[k], int(digit_strs[k])
        if _valid_strong(letter, num) and last_word_pos is not None:
            strong = normalize_strong(letter, num, "")
            rows.append({
                "strong": strong, "lemma": "", "surface": "",
                "ref": ref, "target_id": f"{ref}{last_word_pos + 1:03d}",
                "source_id": f"n{ref}{tag_seq:03d}",
                "method": "sword", "source_corpus": source_corpus, "base_text": base_text,
            })
            tag_seq += 1

    if word_idx != len(clear_tokens(clean)):
        return None, clean                                        # tag-boundary tokenization mismatch — drop
    return rows, clean


def build_gold(module_dir: Path, base_text: str, source_corpus: str, books: list[str] | None = None,
              fmt: str | None = None) -> tuple[list[dict], dict]:
    """All gold rows for `books` (default: whole Bible), plus verse-count stats. `fmt`: "osis" or
    "gbf", auto-detected from the module's own conf (`_format_for`) when None — see module docstring
    for what each shape needs. Multiple target positions from a single multi-word OSIS `<w>` tag all
    get the SAME `source_id` (one source occurrence), matching Clear's own "several target_ids under
    one source_id" shape — `pos_score.load_gold` already unions them via `s[1].update(mapping[idx])`."""
    extract = extract_verse_rows_gbf if (fmt or _format_for(module_dir)) == "gbf" else extract_verse_rows
    wanted = set(books) if books else None
    rows: list[dict] = []
    stats: collections.Counter = collections.Counter()
    for usfm, ch, v, raw in iter_module_verses(module_dir):
        if wanted and usfm not in wanted:
            continue
        stats["verses"] += 1
        ref = f"{BOOK_NUMBERS[usfm]:02d}{ch:03d}{v:03d}"
        vrows, _clean = extract(raw, ref, base_text, source_corpus)
        if vrows is None:
            stats["verses_dropped_tag_boundary"] += 1
            continue
        rows.extend(vrows)
        stats["rows"] += len(vrows)
    return rows, dict(stats)


def write_parquet(rows: list[dict], out_fp: Path, merge: bool = True) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = ["strong", "lemma", "surface", "ref", "target_id", "source_id", "method",
            "source_corpus", "base_text"]
    if merge and out_fp.exists():
        existing = pq.read_table(out_fp, columns=cols).to_pylist()
        keep_base_texts = {r["base_text"] for r in rows}
        existing = [r for r in existing if r["base_text"] not in keep_base_texts]
        rows = existing + rows
    out_fp.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=pa.schema([(c, pa.string()) for c in cols]))
    pq.write_table(table, out_fp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", type=Path, required=True, help="extracted SWORD module directory")
    ap.add_argument("--lang", required=True, help="bare ISO — output file name")
    ap.add_argument("--base-text", required=True, help="Clear-schema base_text label, e.g. Segond1910")
    ap.add_argument("--source-corpus", default="", help="e.g. WLC/SBLGNT if known; blank if not")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--book", action="append")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-merge", action="store_true", help="overwrite the file instead of merging")
    ap.add_argument("--format", choices=["osis", "gbf"], default=None,
                    help="override auto-detection (from the module's own SourceType=)")
    a = ap.parse_args(argv)

    books = (OT_BOOKS + NT_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in a.book] if a.book else None)
    out_fp = a.out or Path(f"pipeline/vendor/resources/strongs/attestations/{a.lang}.parquet")
    rows, stats = build_gold(a.module, a.base_text, a.source_corpus, books, fmt=a.format)
    if stats.get("verses", 0) == 0:
        raise SystemExit(f"[sword_source] zero verses read from {a.module} — wrong module path, or "
                         f"an encrypted module whose cipherkey wasn't found (see _cipherkey_for)")
    write_parquet(rows, out_fp, merge=not a.no_merge)
    print(f"[sword_source] {a.module.name} -> {out_fp}: {stats['verses']} verses, "
          f"{stats['rows']} gold rows (base_text={a.base_text})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
