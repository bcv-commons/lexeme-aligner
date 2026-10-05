"""Aggregate the per-verse jsonl into `senses_attested` — attested target renderings per BHSA sense.

The empirical **evidence** layer shoresh ingests (bcv-query data-contract): for a BHSA lexeme in a
disambiguated sense+binyan, which target words attest it, with counts — the *supply* that fills
shoresh's `senses_i18n/_gaps` demand and validates the LLM glosses. It does NOT replace shoresh's
curated `senses_i18n/<iso>.tsv`; it's consumed as an HF Parquet dataset.

Key: **(lexeme, stem, sense)** — MACULA lexeme (the anchor; BHSA `lex` dropped) + MACULA binyan +
sense number, read inline from the enriched `lexeme-spine.db`. OT/Hebrew only (senses are Hebrew;
Greek tokens carry none, so `sense` presence is the OT filter).

    lexeme, stem, sense, surface, count, share, method, source_corpus, base_text
`share = count / Σ count for that (lexeme, stem, sense)` **within one `base_text`** (target edition).
`base_text` is the per-row provenance dimension, so **multi-version = a union of per-edition runs**
(pool N translations of a language → N sets of rows tagged by edition; cross-edition agreement — how
many editions attest a sense→surface — becomes the confidence signal). `source_corpus` = the original
Hebrew corpus (constant for OT). **Licensing: CC-BY** — the key is MACULA-derived (CC-BY, attribute
Clear-Bible), and we carry the sense *number* only — NO English sense label (that's UBS-MARBLE).

    python3 -m lexeme_aligner.senses_attested --iso ind --method eflomal --lang-name Indonesian
    → senses_attested/iso=ind/data.parquet  (git-ignored)  +  senses_attested/manifest.json  (committed)

Multi-version — POOL several editions of one language into a single language partition, each row
tagged by `base_text` (cross-edition agreement then derivable from the rows; a rights-holder takedown
is a clean `base_text` row-drop, never an anonymized re-emit):

    python3 -m lexeme_aligner.senses_attested --iso swe --pool swk --lang-name Swedish
    → iso=swe/data.parquet with base_texts [swe_fol (Folkbibeln), swe_svk (Kärnbibeln)]

**UBS scheme (2026-09-30) — `--scheme ubs`.** Our own sense number is '1' for 97% of tokens and agrees with the manually
built UBS Dictionary of Biblical Hebrew (SDBH extract, CC BY-SA 4.0) no better than chance when it says "same sense"
(plan doc §8.11), so the trusted sense key is UBS's. `--scheme ubs` keys on **(lexeme, stem, ubs_sense)** where `ubs_sense`
is the UBS sense id (`LEXID`, e.g. 000001001001000) bound to each token by `ubs_senses.py` (`pipeline/ubs-senses.db`).
It is written to its OWN dataset root, `publish/senses_attested/` (named `senses_attested_ubs/` until 2026-10-05; the legacy BHSA one is now `publish/senses_attested_bhsa/`), with a **CC BY-SA 4.0** card and a `senses.tsv`
naming the ids — never mixed into the CC-BY `senses_attested`, following the license-partition rule. Function words
carry UBS senses too (prepositions, conjunctions), so unlike the legacy scheme they are included. A pair is joined to a
UBS sense by (book, chapter, verse, h_idx) and kept only if the pair's lexeme equals the lexeme stored with the binding
(`h_idx` is first translated to the spine's own (verse, idx) by `SpinePositions`; see there).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files, tag_files_any_method
from lexeme_aligner.manifest_io import update_json
from lexeme_aligner.config import HF_CHUNK_SIZE, OUT, SPINE_DB
from lexeme_aligner.export_lex import publish_to_hf   # reuse the HF uploader (generic)

SCHEMA = ["lexeme", "stem", "sense", "surface", "count", "share", "method", "source_corpus", "base_text"]
SCHEMA_UBS = ["lexeme", "stem", "ubs_sense", "surface", "count", "share", "method", "source_corpus", "base_text"]
UBS_LICENSE = "cc-by-sa-4.0"
UBS_CREDIT = ("UBS Dictionary of Biblical Hebrew (c) United Bible Societies 2023, adapted from the Semantic Dictionary of "
              "Biblical Hebrew (c) 2000-2023 United Bible Societies; CC BY-SA 4.0")
Row = tuple[str, str, str, str, int, float, str, str, str]


def publish_all_to_hf(root: Path, repo_id: str, create: bool, dry_run: bool,
                      chunk_size: int = HF_CHUNK_SIZE) -> None:
    """Bulk-publish EVERY already-exported language partition + manifest/README in one chunked batch —
    same pattern as export_lex.publish_all_to_hf. Assumes every partition already exists locally."""
    from lexeme_aligner.hf_bulk_publish import publish_chunked
    partitions = sorted(str(fp.relative_to(root)) for fp in root.glob("iso=*/data.parquet"))
    shared = [f for f in ["manifest.json", "README.md"] if (root / f).exists()]
    publish_chunked(root, repo_id, partitions + shared, create, dry_run, chunk_size, label="senses_attested")


def _method_of(fp: Path) -> str:
    """align_<method>_<tag>_<BOOK>.jsonl -> <method>."""
    return fp.name.split("_", 2)[1]


def _resolve_files(out_dir: Path, align_iso: str, method: str) -> list[tuple[Path, str]]:
    """method="all" -> every method present for this tag, each file tagged with its own actual method
    (additive union, same spirit as lexeme-alignments — a pair attested by two methods is two rows,
    nothing silently merged). Otherwise a comma-separated list restricts to just those methods."""
    if method == "all":
        return [(fp, _method_of(fp)) for fp in tag_files_any_method(out_dir, align_iso)]
    names = [m.strip() for m in method.split(",") if m.strip()]
    return [(fp, m) for m in names for fp in tag_files(out_dir, m, align_iso)]


def hebrew_corpus() -> str:
    """The spine's Hebrew original corpus (WLC…) from spine_meta — the `source_corpus` for OT senses."""
    try:
        con = sqlite3.connect(f"file:{SPINE_DB}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT key, value FROM spine_meta").fetchall())
        con.close()
        m = re.search(r"\b(WLC|BHSA|BHS)\b", meta.get("source_hebrew", ""))
        return m.group(1) if m else "WLC"
    except sqlite3.Error:
        return "WLC"


class SpinePositions:
    """Alignment `h_idx` -> (spine verse, spine idx), the key the UBS binding table uses.

    `h_idx` in the align jsonl is a token's POSITION in its verse group (`run_pilot.pooled_verse_groups`
    renumbers 0..N-1), not the spine's own `idx`: the two differ after any token HebrewSource merges from
    several spine rows ("בֵּית לֶחֶם" = idx 16+17 -> one token), and a pooled verse range continues the count
    into the following spine verses. Joining `h_idx` to the binding table directly silently dropped 3.5% of
    bound OT content tokens (2026-10-05). A group is consecutive spine verses starting at the record's
    verse, so the position is walked through them; the caller still checks the lexeme."""

    def __init__(self, heb=None):
        if heb is None:
            from lexeme_aligner.hebrew_source import HebrewSource
            heb = HebrewSource()
        self.heb = heb
        self._verses: dict = {}
        self._idx: dict = {}

    def _tokens(self, book, ch, v):
        key = (book, ch, v)
        if key not in self._idx:
            self._idx[key] = [t.idx for t in self.heb.verse_tokens(book, ch, v)]
        return self._idx[key]

    def __call__(self, book, ch, v, pos):
        if pos is None:
            return None
        if (book, ch) not in self._verses:
            self._verses[(book, ch)] = list(self.heb.verses(book, ch))
        verses = self._verses[(book, ch)]
        if v not in verses:
            return None
        for v2 in verses[verses.index(v):]:
            idx = self._tokens(book, ch, v2)
            if pos < len(idx):
                return v2, idx[pos]
            pos -= len(idx)
        return None


def aggregate(out_dir: Path, editions: list[tuple[str, str]], method: str = "all", scheme: str = "legacy",
              ubs: dict | None = None, positions=None):
    """Fold OT content pairs (lexeme, stem, sense) into attested target renderings, per edition.

    `editions` is a list of (align_iso, base_text). Pooling several editions of ONE language into a
    single partition keeps every row tagged by its `base_text`, with `share` computed WITHIN that
    edition — so cross-edition agreement (a sense→surface attested by >1 base_text) stays derivable
    from the rows, without laundering provenance. A takedown is then a clean `base_text` row-drop.

    method="all" (the default) UNIONS every method present for each tag (eflomal/gloss/gapfill) —
    each row also carries its own `method`, so a sense→surface attested by two methods is two rows,
    same additive-union spirit as lexeme-alignments (nothing silently merged across methods)."""
    counts: collections.Counter = collections.Counter()  # (lexeme, stem, sense, surface, base_text, method) -> count
    n_files = 0
    if scheme == "ubs" and ubs is None:
        from lexeme_aligner.ubs_senses import load_token_senses
        ubs = load_token_senses(with_lexeme=True)
        if not ubs:
            raise SystemExit("no UBS sense database (pipeline/ubs-senses.db) — run `python3 -m lexeme_aligner.ubs_senses --build`")
    if scheme == "ubs" and positions is None:
        positions = SpinePositions()
    for align_iso, base_text in editions:
        files = _resolve_files(out_dir, align_iso, method)
        if not files:
            raise SystemExit(f"no align_*_{align_iso}_*.jsonl under {out_dir} — run the aligner first")
        n_files += len(files)
        for fp, file_method in files:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    for p in rec["pairs"]:
                        lexeme, tgt = p.get("lexeme"), p.get("target")
                        if scheme == "ubs":
                            loc = positions(rec.get("book"), rec.get("chapter"), rec.get("verse"), p.get("h_idx"))
                            hit = ubs.get((rec.get("book"), rec.get("chapter"), *loc)) if loc else None
                            if not (hit and lexeme and tgt and hit[1] == lexeme):
                                continue                       # no UBS sense here (lexeme check = safety net)
                            se = hit[0]
                        else:
                            se = p.get("sense")
                            if not (p.get("content") and lexeme and se and tgt):  # sense ⇒ OT/Hebrew only
                                continue
                        counts[(lexeme, p.get("stem") or "", str(se), tgt.strip().lower(),
                               base_text, file_method)] += 1

    return counts, n_files


def _totals(counts) -> collections.Counter:
    """Σ count per (lexeme, stem, sense, base_text, method) — the `share` denominator (WITHIN edition
    AND method, mirroring lexeme-alignments' "share is P(x|surface) within a (method, base_text)
    group" convention).
    Computed AFTER any exclusion so survivor shares renormalise and a removed row leaves no trace."""
    per_key: collections.Counter = collections.Counter()
    for (lexeme, stem, se, _su, bt, m), n in counts.items():
        per_key[(lexeme, stem, se, bt, m)] += n
    return per_key


_EXCL_FIELDS = ("lexeme", "stem", "sense", "surface", "base_text")


def load_excludes(path: Path | None) -> list[dict]:
    """Read the optional exclusion config: a rights-holder takedown record (auditable, committed).

    `{"exclude": [ {<field>: <value>, …}, … ]}` — a row is dropped if it matches ANY rule, where a
    rule matches when ALL its stated fields equal the row's (fields: lexeme, stem, sense, surface,
    base_text; omit a field to wildcard it — e.g. `{"base_text": "swe_fol"}` drops a whole edition).
    Absent file → no exclusions. `surface` is compared lowercased (that's how rows are stored)."""
    if not path or not Path(path).exists():
        return []
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = doc.get("exclude", []) if isinstance(doc, dict) else doc
    rules = []
    for r in raw:
        rule = {k: (str(v).strip().lower() if k == "surface" else str(v))
                for k, v in r.items() if k in _EXCL_FIELDS}
        if rule:
            rules.append(rule)
    return rules


def apply_excludes(counts, rules: list[dict]):
    """Drop every count whose (lexeme, stem, sense, surface, base_text) matches a rule. Returns
    (kept_counts, n_dropped). Removal is total — the row is not re-emitted anonymized elsewhere."""
    if not rules:
        return counts, 0
    kept: collections.Counter = collections.Counter()
    dropped = 0
    for (lx, stem, se, su, bt, m), n in counts.items():
        row = {"lexeme": lx, "stem": stem, "sense": se, "surface": su, "base_text": bt}
        if any(all(row[k] == v for k, v in rule.items()) for rule in rules):
            dropped += 1
            continue
        kept[(lx, stem, se, su, bt, m)] = n
    return kept, dropped


def build_rows(counts, per_key, source_corpus: str, min_count: int) -> list[Row]:
    rows: list[Row] = [(lx, stem, se, su, n, n / per_key[(lx, stem, se, bt, m)], m, source_corpus, bt)
                       for (lx, stem, se, su, bt, m), n in counts.items() if n >= min_count]
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[8], -r[4]))  # group by (lex, stem, sense, base_text); top first
    return rows


def _render(rows: list[Row]) -> list[str]:
    return [f"{lx}\t{st}\t{se}\t{su}\t{c}\t{sh:.4f}\t{m}\t{sc}\t{bt}"
            for lx, st, se, su, c, sh, m, sc, bt in rows]


def write_parquet(rows: list[Row], dest: Path, sense_col: str = "sense") -> None:
    import pyarrow as pa
    import pyarrow.parquet as papq
    cols = list(zip(*rows)) if rows else ([],) * 9
    papq.write_table(pa.table({
        "lexeme": pa.array(cols[0], pa.string()), "stem": pa.array(cols[1], pa.string()),
        sense_col: pa.array(cols[2], pa.string()), "surface": pa.array(cols[3], pa.string()),
        "count": pa.array(cols[4], pa.int32()),
        "share": pa.array([round(x, 4) for x in cols[5]], pa.float32()),
        "method": pa.array(cols[6], pa.string()), "source_corpus": pa.array(cols[7], pa.string()),
        "base_text": pa.array(cols[8], pa.string()),
    }), dest, compression="zstd")


def write_tsv(rows: list[Row], dest: Path, sense_col: str = "sense") -> None:
    dest.write_text("\t".join(SCHEMA_UBS if sense_col == "ubs_sense" else SCHEMA) + "\n" + "\n".join(_render(rows)) + "\n", encoding="utf-8")


def build_entry(rows: list[Row], min_count: int, books: int, lang_name: str | None,
                rel_file: str, sources: dict | None) -> dict:
    methods_label = "+".join(sorted({r[6] for r in rows})) if rows else None
    entry = {
        "language": lang_name, "method": methods_label, "min_count": min_count, "testament": "OT",
        "books": books, "rows": len(rows), "source_corpus": rows[0][7] if rows else None,
        "base_texts": sorted({r[8] for r in rows}),          # the edition(s) attested (multi-version)
        "lexemes": len({r[0] for r in rows}), "lexeme_stem_senses": len({(r[0], r[1], r[2]) for r in rows}),
        "surfaces": len({r[3] for r in rows}), "file": rel_file,
        "content_sha256": hashlib.sha256("\n".join(_render(rows)).encode()).hexdigest(),
    }
    if sources:                                              # {base_text: license pointer} — per edition
        entry["sources"] = sources
    return {k: v for k, v in entry.items() if v is not None}


def update_manifest(path: Path, iso: str, entry: dict, scheme: str = "legacy") -> None:
    def _merge(doc: dict) -> None:
        doc["schema"] = SCHEMA_UBS if scheme == "ubs" else SCHEMA
        if scheme == "ubs":
            doc["license"] = UBS_LICENSE
            doc["credit"] = UBS_CREDIT
        doc.setdefault("languages", {})[iso] = entry

    update_json(path, _merge)                 # locked: several chains can finish a language at the same moment


def write_ubs_sense_table(rows: list[Row], dest: Path, ubs_db: Path | None = None) -> int:
    """`senses.tsv`: one line per UBS sense id that occurs in `rows` — id, UBS entry id, lemma, Strong's codes, short English
    gloss, lexical domain codes. Enough to read the ids; the full dictionary (all languages) is at UBS. Merged with any
    existing table so a multi-language root keeps every id it uses. Returns the number of ids written."""
    from lexeme_aligner.ubs_senses import UBS_SENSES_DB
    ids = {r[2] for r in rows}
    have: dict[str, str] = {}
    if dest.exists():
        for line in dest.read_text(encoding="utf-8").splitlines()[1:]:
            have[line.split("\t", 1)[0]] = line
    con = sqlite3.connect(f"file:{ubs_db or UBS_SENSES_DB}?mode=ro", uri=True)
    try:
        for lex_id, main_id, lemma, strongs, gloss, dom in con.execute(
                "SELECT lex_id,main_id,lemma,strongs,gloss_en,domains FROM sense"):
            if lex_id in ids:
                clean = lambda x: re.sub(r"[\t\r\n]+", " ", x or "").strip()      # one physical line per sense
                have[lex_id] = "\t".join([lex_id, main_id, clean(lemma), ",".join(f"H{x:04d}" for x in json.loads(strongs)),
                                          clean(gloss), ",".join(json.loads(dom))])
    finally:
        con.close()
    dest.write_text("ubs_sense\tubs_entry\tlemma\tstrongs\tgloss_en\tdomains\n"
                    + "\n".join(have[k] for k in sorted(have)) + "\n", encoding="utf-8")
    return len(have)


_UBS_CARD = """---
pretty_name: Attested target renderings per UBS Hebrew sense
tags:
  - bible
  - word-sense
  - lexeme
  - hebrew
license: cc-by-sa-4.0
configs:
  - config_name: default
    data_files:
      - split: train
        path: iso=*/data.parquet
---

# senses-attested — attested target renderings per UBS sense

For each Hebrew lexeme and each sense of the **UBS Dictionary of Biblical Hebrew**, which target-language words render
it in practice, with counts, mined from the word alignments of `bcv-commons/lexeme-alignments`. Columns: `lexeme`
(MACULA anchor), `stem` (binyan, empty for non-verbs), `ubs_sense` (UBS sense id, see `senses.tsv`), `surface`, `count`,
`share` (within lexeme, stem, sense, method and edition), `method`, `source_corpus`, `base_text` (the target edition).

**Why UBS senses.** The senses are the manually built, academically maintained sense inventory of the United Bible
Societies; each sense id is bound to individual tokens through the dictionary's own per-occurrence Scripture references.
Function words (prepositions, conjunctions) carry senses too and are included. `senses.tsv` names every id used here
(entry id, lemma, Strong's codes, short English gloss, lexical domain codes).

**License and credit — CC BY-SA 4.0 (share-alike).** This dataset is derived from the UBS Dictionary of Biblical Hebrew,
(c) United Bible Societies 2023, adapted from the Semantic Dictionary of Biblical Hebrew (c) 2000-2023 United Bible
Societies, released under CC BY-SA 4.0 (https://github.com/ubsicap/ubs-open-license). Anything you build from these
files must be released under the same or a compatible license with this credit. It is deliberately a SEPARATE dataset
from `bcv-commons/senses-attested-bhsa` (the retired legacy set: sense numbers from a BHSA-derived clustering, CC BY-NC-SA) so the two licenses never mix.

**Known limits.** Hebrew Bible only (the UBS Greek dictionary is not used yet); the UBS Hebrew dictionary covers about
90% of Old Testament words; ids are bound to tokens by verse-level matching (unique / anchored / nearest, see
`lexeme_aligner/ubs_senses.py`), validated against the spine's own glosses (61% word overlap for unique bindings against
13% for shuffled senses). Tokens in pooled verse ranges (one target verse for several source verses) are resolved to their own source verse
and kept; a token is only counted when its lexeme equals the lexeme stored with the binding.

## Verse mapping

Hebrew and English Bibles number some Old Testament verses differently (Psalm superscriptions, 1 Chronicles 6,
Joel, Malachi, Daniel 4 and 6, ...). Source and target verses are paired with the verse-mapping tables of **TVTMS**
(Translators Versification Traditions with Methodology for Standardisation), part of STEPBible Data by Tyndale
House, Cambridge — **CC BY 4.0**, https://github.com/STEPBible/STEPBible-Data — with each edition's numbering
detected from its own text (cross-checked against bcv-commons/bibles). Source verse references follow the Hebrew
(WLC) numbering of the MACULA source text.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scheme", choices=["legacy", "ubs"], default="legacy",
                    help="sense key: 'legacy' = the BHSA-derived spine sense number (RETIRED 2026-10-03, dataset relabelled CC BY-NC-SA 4.0, needs a BHSA spine; publish/senses_attested); "
                         "'ubs' = UBS Dictionary of Biblical Hebrew sense ids (CC BY-SA dataset, "
                         "publish/senses_attested)")
    ap.add_argument("--publish-all", metavar="REPO_ID", default=None,
                    help="bulk-publish EVERY already-exported iso=*/data.parquet in one chunked batch, "
                         "instead of exporting one language")
    ap.add_argument("--chunk-size", type=int, default=HF_CHUNK_SIZE,
                    help="files per HF commit, with --publish-all (default from ALIGNER_HF_CHUNK_SIZE)")
    ap.add_argument("--iso", default="ind", help="language partition (ISO 639-3); also the primary edition")
    ap.add_argument("--pool", default=None, help="comma-sep additional align isos to POOL into this one "
                    "language partition (e.g. --iso swe --pool swk → base_texts swe_fol+swe_svk, each row "
                    "tagged; cross-edition agreement derivable). Their base_text comes from data/sources.json.")
    ap.add_argument("--method", default="all", help="'all' (default) unions every method present; "
                    "or a specific name / comma-separated list to restrict to")
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--publish-iso", default=None,
                    help="true published language code for the output partition (default: same as "
                         "--iso) — set when --iso is an edition TAG that differs from the bare iso, "
                         "e.g. --iso arb_vdv --publish-iso arb, so the partition lands at iso=arb/ "
                         "(not iso=arb_vdv/) and stays discoverable by an already-published check.")
    ap.add_argument("--lang-name", default=None)
    ap.add_argument("--base-text", default=None, help="override the PRIMARY iso's edition tag (default: source.edition)")
    ap.add_argument("--format", choices=["parquet", "tsv"], default="parquet")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--root", type=Path, default=None,
                    help="dataset root (default: publish/senses_attested for --scheme ubs, publish/senses_attested_bhsa for legacy)")
    ap.add_argument("--sources", type=Path, default=Path("config/sources.json"))
    ap.add_argument("--exclude", type=Path, default=Path("config/senses_exclude.json"),
                    help="optional takedown/exclusion config (committed, auditable); absent → no-op. "
                         "Rows matching any rule are DROPPED (not anonymized) + shares renormalise.")
    ap.add_argument("--publish", metavar="REPO_ID", default=None)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.root = args.root or Path("publish/senses_attested" if args.scheme == "ubs" else "publish/senses_attested_bhsa")

    if args.publish_all:
        publish_all_to_hf(args.root, args.publish_all, args.create, args.dry_run, args.chunk_size)
        return 0

    publish_iso = args.publish_iso or args.iso
    all_sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources.exists() else {}
    pool_isos = [args.iso] + [s.strip() for s in (args.pool.split(",") if args.pool else []) if s.strip()]
    editions: list[tuple[str, str]] = []     # (align_iso, base_text) — one per edition pooled here
    sources: dict[str, dict] = {}            # base_text -> license pointer (per edition)
    for i, al_iso in enumerate(pool_isos):
        src = all_sources.get(al_iso)
        bt = (args.base_text if i == 0 and args.base_text else None) or (src or {}).get("edition") or al_iso
        editions.append((al_iso, bt))
        if src:
            sources[bt] = src
    counts, n_files = aggregate(args.out, editions, args.method, scheme=args.scheme)
    rules = load_excludes(args.exclude)
    counts, n_excl = apply_excludes(counts, rules)
    if rules:
        print(f"[senses_attested] exclude: {len(rules)} rule(s) from {args.exclude} → dropped {n_excl} "
              f"(lexeme,stem,sense,surface,base_text) row(s)", file=sys.stderr)
    per_key = _totals(counts)                                 # share denominator AFTER exclusion
    rows = build_rows(counts, per_key, hebrew_corpus(), args.min_count)
    if not rows:
        print(f"[senses_attested] {args.iso}: no sensed OT pairs (needs the enriched spine + OT books)",
              file=sys.stderr)
        return 0
    part = args.root / f"iso={publish_iso}"
    part.mkdir(parents=True, exist_ok=True)
    rel_file = f"iso={publish_iso}/data.{'parquet' if args.format == 'parquet' else 'tsv'}"
    dest = args.root / rel_file
    sense_col = "ubs_sense" if args.scheme == "ubs" else "sense"
    (write_parquet if args.format == "parquet" else write_tsv)(rows, dest, sense_col)
    if args.scheme == "ubs":
        (args.root / "README.md").write_text(_UBS_CARD, encoding="utf-8")
        n_ids = write_ubs_sense_table(rows, args.root / "senses.tsv")
        print(f"[senses_attested] senses.tsv now names {n_ids} UBS sense id(s)", file=sys.stderr)

    entry = build_entry(rows, args.min_count, n_files, args.lang_name, rel_file, sources)
    if rules:                                                # record the takedown application (auditable)
        entry["excluded"] = {"rules": len(rules), "rows_dropped": n_excl}
    update_manifest(args.root / "manifest.json", publish_iso, entry, args.scheme)
    print(f"[senses_attested] {n_files} file(s) · {entry['rows']} rows · {entry['lexemes']} lexemes · "
          f"{entry['lexeme_stem_senses']} (lexeme,stem,sense) · base_texts={entry['base_texts']}  → {dest}",
          file=sys.stderr)

    if args.publish:
        publish_to_hf(args.root, publish_iso, rel_file, entry, args.publish, args.create, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
