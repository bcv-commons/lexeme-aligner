"""gold → full-align: convert every per-occurrence gold we hold into the full-align row format, and
lay the statistical + LLM layers of the same editions beside it (docs/architecture.md §4).

full-align is the COMPLETE per-edition alignment as provenance-tagged ROWS, in up to three layers for
the same text — `statistical` (the base chain's own rows, method/score kept), `manual` (gold converted
into the identical record: "gold" is a role a consumer assigns from the attribution, not a data shape),
`llm` (model rows). Layers are never merged; agreement is a consumer-side signal.

Layout (per §4), bulk gitignored, `manifest.json` + `README.md` committed:
    <out>/<iso>/<edition>/statistical/<BOOK>.parquet          CC0-1.0
    <out>/<iso>/<edition>/manual/<base_text>/<BOOK>.parquet    the gold source's license
    <out>/<iso>/<edition>/llm/<cell>/<BOOK>.parquet            CC0-1.0
Two deliberate deviations from §4's shorter path sketch, both because the same edition can carry MORE
THAN ONE of a layer: the manual layer is keyed by `base_text` (fra-lsg has Clear/LSG AND SWORD/
Segond1910; rus_syn has Clear/RUSSYN AND SWORD/RusVZh), and the llm layer by the run cell
(`hinirv.verify.sonnet5.cli`, `hinirv.full.sonnet5.cli`, ...). The manifest records each.

Conversion REUSES the scorer's own mapping (`pos_score.load_gold`: Clear tokenization reconstructed
over OUR edition text → our token positions; `(verse, strong, k-th occurrence)` keying) and the spine
(`run_pilot.build_corpus`, same verse-range pooling as every published position) to resolve `h_idx`
AND the MACULA lexeme — every gold is bare Strong's; the spine lookup is the crosswalk. Losses are
reported in the manifest, never silent: refused verses (tokenization mismatch), ambiguous links (a
Strong's occurring a different number of times on the two sides — excluded, same rule as `pos_score.
score`), links beyond text, punctuation-only links, verses outside the pooled corpus.

gbt (globalbibletools, CC0) is per-source-word GLOSS PHRASES over gbt's own text, not a positional
alignment of one of our editions: source side resolves through the same (strong, k) keying (gbt's rows
cover every source word, glossed or not), target side gets a `t_idx` only when the gloss phrase matches
exactly one contiguous token run in the chosen edition's verse — otherwise `t_idx` is null and the
gloss is kept in `target`. The resolved fraction is reported; it is a property of how close the chosen
edition is to gbt's own text, not a quality judgement.

Routing: CrossWire-restricted SWORD golds (spa RV1909, fra Segond1910, rus RusVZh) go to
`--internal-out` only (`publishable: false`, license `vendor-only`); Clear's rus/RUSSYN is published
with `quarantined: true` (its per-verse strong→word pairings are ~57% positionally wrong — see
config/gold_langs.json) and never counted as gold by anything here. Gold health (positional vs lexical
agreement of our eflomal rows with the gold, `contest_rule.gold_health`'s measure generalized to every
gold source, since that function is hardcoded to Clear) is recorded per manual partition.

    python3 -m lexeme_aligner.gold_to_fullalign --all-gold
    python3 -m lexeme_aligner.gold_to_fullalign --iso hin
    python3 -m lexeme_aligner.gold_to_fullalign --iso arb --edition arbnav --base-text ONAV
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.benchmark import norm_surface
from lexeme_aligner.config import OUT, RESOURCES
from lexeme_aligner.refs import BOOK_NUMBERS, encode
from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS, _BOOK_FILE_NUM

ALL_BOOKS = OT_BOOKS + NT_BOOKS
INGEST = Path("pipeline/work/ingest-cache")
GOLD_LANGS = Path("config/gold_langs.json")
OCCURRENCE_ALIGN = Path("pipeline/work/occurrence_align")
DEFAULT_OUT = Path("publish/full-align")
DEFAULT_INTERNAL_OUT = Path("pipeline/work/full-align")
STAT_METHODS = ("eflomal", "gloss", "spanext", "gapfill", "residual")

# --- routing (§4 gold inventory) -----------------------------------------------------------------------
VENDOR_ONLY = {("spa", "RV1909"), ("fra", "Segond1910"), ("rus", "RusVZh")}   # CrossWire-restricted
QUARANTINED = {("rus", "RUSSYN"): "Clear's RUSSYN gold is itself positionally mis-aligned (~57% of its "
                                  "per-verse strong->word pairings wrong; config/gold_langs.json "
                                  "_quarantine) — published as data, never counted as gold here."}
LICENSE_BY_SOURCE = {"clear": "CC-BY-4.0", "helfi": "CC-BY-4.0", "gbt": "CC0-1.0"}
LICENSE_BY_SWORD_MODULE = {"ChiUns": "Public Domain"}                       # everything else: vendor-only
SOURCE_OF_GOLD_METHOD = {"manual": "clear", "transfer": "clear", "sword": "sword", "helfi": "helfi"}
KIND_OF_GOLD_METHOD = {"manual": "manual", "transfer": "transfer", "sword": "manual", "helfi": "manual"}
# a second Clear base_text is a DIFFERENT text than the language's primary edition — the ingest tag
# whose text matches is picked by probing (lowest refused-verse rate), never assumed
SECOND_EDITION_CANDIDATES = {("arb", "ONAV"): ["arbnav", "arbn"], ("eng", "YLT"): ["eng_ylt", "engy"]}
GBT_EDITION = {"hun": "hunhun", "tam": "tam_tcv", "tel": "tel_irv", "rus": "rus_syn", "njm": None}
STATISTICAL_ATTR = {"source": "lexeme-aligner", "kind": "statistical", "license": "CC0-1.0"}


def route(iso: str, base_text: str, source: str) -> dict:
    """{publishable, license, quarantined, quarantine_reason} for one gold partition."""
    vendor = (iso, base_text) in VENDOR_ONLY
    if source == "sword":
        lic = "vendor-only" if vendor else LICENSE_BY_SWORD_MODULE.get(base_text, "vendor-only")
    else:
        lic = LICENSE_BY_SOURCE[source]
    q = QUARANTINED.get((iso, base_text))
    return {"publishable": not vendor and lic != "vendor-only", "license": lic,
            "quarantined": q is not None, "quarantine_reason": q}


# --- parquet schema --------------------------------------------------------------------------------------
def _schema():
    import pyarrow as pa
    attr = pa.struct([("source", pa.string()), ("kind", pa.string()), ("license", pa.string()),
                      ("base_text", pa.string()), ("source_ids", pa.list_(pa.string())),
                      ("target_ids", pa.list_(pa.string())), ("model", pa.string()),
                      ("strategy", pa.string()), ("prompt_version", pa.string()), ("cell", pa.string())])
    return pa.schema([("ref", pa.int64()), ("book", pa.string()), ("chapter", pa.int32()),
                      ("verse", pa.int32()), ("h_idx", pa.int32()), ("lexeme", pa.string()),
                      ("strong", pa.string()), ("lemma", pa.string()), ("stem", pa.string()),
                      ("surface", pa.string()), ("gloss_en", pa.string()), ("sense", pa.string()),
                      ("target", pa.string()), ("t_idx", pa.list_(pa.int32())), ("score", pa.float64()),
                      ("method", pa.string()), ("content", pa.bool_()), ("prior", pa.string()),
                      ("extra", pa.string()), ("attribution", attr)])


_PAIR_KEYS = ("h_idx", "lexeme", "strong", "lemma", "stem", "surface", "gloss_en", "sense", "target",
              "t_idx", "score", "method", "content", "prior")
_ATTR_KEYS = ("source", "kind", "license", "base_text", "source_ids", "target_ids", "model", "strategy",
              "prompt_version", "cell")


def make_row(ref: int, book: str, pair: dict, attribution: dict) -> dict:
    """One full-align row: the pair's standard fields, anything else JSON-packed into `extra`, plus the
    attribution block (missing attribution keys → null)."""
    row = {"ref": ref, "book": book, "chapter": ref // 1000 % 1000, "verse": ref % 1000}
    for k in _PAIR_KEYS:
        row[k] = pair.get(k)
    if row["t_idx"] is not None:
        row["t_idx"] = [int(i) for i in row["t_idx"]]
    if row["score"] is not None:
        row["score"] = float(row["score"])
    if row["content"] is not None:
        row["content"] = bool(row["content"])
    extra = {k: v for k, v in pair.items() if k not in _PAIR_KEYS}
    row["extra"] = json.dumps(extra, ensure_ascii=False) if extra else None
    row["attribution"] = {k: attribution.get(k) for k in _ATTR_KEYS}
    return row


def write_partition(rows: list[dict], dest_dir: Path) -> dict:
    """rows → <dest_dir>/<BOOK>.parquet per book; returns {book: {rows, content_sha256}}."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    by_book: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_book[r["book"]].append(r)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for book in ALL_BOOKS:
        if book not in by_book:
            continue
        fp = dest_dir / f"{book}.parquet"
        table = pa.Table.from_pylist(by_book[book], schema=_schema())
        pq.write_table(table, fp, compression="zstd")
        out[book] = {"rows": len(by_book[book]),
                     "content_sha256": hashlib.sha256(fp.read_bytes()).hexdigest()}
    return out


# --- the spine side (reused from run_pilot, keyed like pos_score) ---------------------------------------
class Corpus:
    """Per verse (pooled like every published position): our tokens, and (strong, k) → HebToken."""

    def __init__(self, books: list[str], usj_dir: Path, iso: str):
        from lexeme_aligner.hebrew_source import HebrewSource
        from lexeme_aligner.run_pilot import build_corpus
        from lexeme_aligner.versification import remapper
        self.toks: dict[int, list[str]] = {}
        self.by_key: dict[int, dict[tuple[str, int], object]] = {}
        self.counts: dict[int, collections.Counter] = {}
        recs = build_corpus(books, usj_dir, HebrewSource(), remap=remapper(iso, str(usj_dir)))
        for r in recs:
            ref = encode(r.book, r.ch, r.v)
            self.toks[ref] = list(r.toks)
            seen: collections.Counter = collections.Counter()
            self.by_key[ref] = {}
            for t in sorted(r.heb, key=lambda t: t.idx):
                if not t.strong:
                    continue
                self.by_key[ref][(t.strong, seen[t.strong])] = t
                seen[t.strong] += 1
            self.counts[ref] = seen


def _tok_pair(tok, target: str, t_idx: list[int] | None, method: str, prior: str | None = None) -> dict:
    return {"h_idx": tok.idx, "lexeme": tok.lexeme, "strong": tok.strong, "lemma": tok.lemma,
            "stem": tok.stem, "surface": tok.surface, "gloss_en": tok.gloss_en, "sense": tok.sense,
            "target": target, "t_idx": t_idx, "score": None, "method": method,
            "content": bool(tok.is_content), "prior": prior}


def rows_from_gold(gold: dict, corpus: Corpus, method: str, attribution: dict, stats: collections.Counter
                   ) -> list[dict]:
    """pos_score.load_gold's {ref: GoldVerse} → full-align rows via the spine. A link whose Strong's
    occurs a different number of times on the two sides is AMBIGUOUS (its k may not denote the same
    token) — excluded and counted, the same rule `pos_score.score` applies; nothing is guessed."""
    rows = []
    for ref, gv in gold.items():
        if ref not in corpus.by_key:
            stats["verses_outside_corpus"] += 1
            continue
        stats["verses_converted"] += 1
        g_count = collections.Counter(s for s, _k in gv.links)
        for (strong, k), pos in gv.links.items():
            if g_count[strong] != corpus.counts[ref][strong]:
                stats["links_ambiguous"] += 1
                continue
            tok = corpus.by_key[ref].get((strong, k))
            if tok is None:
                stats["links_no_spine_token"] += 1
                continue
            t_idx = sorted(pos)
            toks = corpus.toks[ref]
            target = " ".join(toks[p] for p in t_idx if p < len(toks))
            sid, tids = gv.raw.get((strong, k), (None, []))
            attr = dict(attribution, source_ids=[sid] if sid else [], target_ids=list(tids))
            rows.append(make_row(ref, _book_of(ref), _tok_pair(tok, target, t_idx, method), attr))
            stats["links_converted"] += 1
    return rows


def _book_of(ref: int) -> str:
    n = ref // 1_000_000
    return next(b for b, i in BOOK_NUMBERS.items() if i == n)


# --- gold health (contest_rule.gold_health's measure, for every gold source) ----------------------------
def gold_health(gold: dict, eflomal_rows: list[dict], corpus: Corpus) -> dict | None:
    """positional vs lexical agreement of our eflomal rows with this gold over content tokens the gold
    judges. Same definition as contest_rule.gold_health (hardcoded to Clear there), generalized: a large
    lexical>>positional gap means the GOLD's per-verse pairing is scrambled (rus), not our alignment."""
    surf_at: dict[tuple[int, str], set[str]] = collections.defaultdict(set)
    agg: dict[str, set[str]] = collections.defaultdict(set)
    for ref, gv in gold.items():
        for (strong, _k), surf in gv.surfaces.items():
            ws = {norm_surface(w) for w in surf.split()}
            surf_at[(ref, strong)] |= ws
            agg[strong] |= ws
    pos = lex = n = 0
    for r in eflomal_rows:
        if not r["content"] or not r["strong"] or (r["ref"], r["strong"]) not in surf_at:
            continue
        n += 1
        words = {norm_surface(w) for w in (r["target"] or "").split()}
        pos += bool(words & surf_at[(r["ref"], r["strong"])])
        lex += bool(words & agg[r["strong"]])
    if not n:
        return None
    return {"positional": round(pos / n, 4), "lexical": round(lex / n, 4), "gap": round((lex - pos) / n, 4), "n": n}


# --- layers ------------------------------------------------------------------------------------------
def gold_editions(iso: str, res_dir: Path = RESOURCES, gold_langs: Path = GOLD_LANGS) -> list[dict]:
    """Every (gold method, base_text) partition the attestation parquet holds for `iso`, with the ingest
    tag whose text it was built on. The language's registered edition (config/gold_langs.json) serves
    every base_text except a second Clear text (ONAV/YLT), which is probed (see pick_edition)."""
    import pyarrow.parquet as pq
    fp = res_dir / "strongs" / "attestations" / f"{iso}.parquet"
    if not fp.exists():
        return []
    t = pq.read_table(fp, columns=["method", "base_text"])
    combos = sorted(collections.Counter(zip(t.column("method").to_pylist(),
                                             t.column("base_text").to_pylist())).items())
    reg = json.loads(gold_langs.read_text(encoding="utf-8")).get(iso, {}) if gold_langs.exists() else {}
    primary = reg.get("edition") if isinstance(reg, dict) else None
    out = []
    for (gm, bt), n in combos:
        source = SOURCE_OF_GOLD_METHOD.get(gm)
        if source is None:
            continue
        cands = SECOND_EDITION_CANDIDATES.get((iso, bt))
        out.append({"iso": iso, "gold_method": gm, "source": source, "kind": KIND_OF_GOLD_METHOD[gm],
                    "base_text": bt, "rows": n, "edition": None if cands else primary,
                    "candidates": cands, **route(iso, bt, source)})
    return out


def pick_edition(iso: str, base_text: str, gold_method: str, candidates: list[str],
                 res_dir: Path = RESOURCES, probe_books: tuple[str, ...] = ("MAT", "GEN")) -> tuple[str | None, dict]:
    """Which ingest tag holds the text a second Clear base_text was aligned on: the candidate with the
    lowest refused-verse rate on the probe books (a text mismatch refuses nearly every verse)."""
    from lexeme_aligner.pos_score import load_gold
    best, report = None, {}
    for tag in candidates:
        usj = INGEST / f"usj-{tag}"
        if not usj.exists():
            report[tag] = "no ingest dir"
            continue
        _, st = load_gold(iso, usj, list(probe_books), base_text, res_dir, gold_methods=(gold_method,))
        rate = st["verses_refused"] / max(1, st["verses"])
        report[tag] = {"verses": st["verses"], "refused": st["verses_refused"], "refused_rate": round(rate, 4)}
        if best is None or rate < best[1]:
            best = (tag, rate)
    return (best[0] if best else None), report


def convert_manual(ge: dict, edition: str, books: list[str], corpus: Corpus, out_root: Path,
                   res_dir: Path = RESOURCES, eflomal_rows: list[dict] | None = None) -> dict:
    from lexeme_aligner.pos_score import load_gold
    usj = INGEST / f"usj-{edition}"
    gold, st = load_gold(ge["iso"], usj, books, ge["base_text"], res_dir, gold_methods=(ge["gold_method"],))
    attribution = {"source": ge["source"], "kind": ge["kind"], "license": ge["license"], "base_text": ge["base_text"]}
    method = "transfer" if ge["kind"] == "transfer" else "manual"
    rows = rows_from_gold(gold, corpus, method, attribution, st)
    files = write_partition(rows, out_root / ge["iso"] / edition / "manual" / ge["base_text"])
    return {"source": ge["source"], "gold_method": ge["gold_method"], "kind": ge["kind"],
            "license": ge["license"], "publishable": ge["publishable"], "quarantined": ge["quarantined"],
            "quarantine_reason": ge["quarantine_reason"], "coverage": coverage(st, rows),
            "health": gold_health(gold, eflomal_rows, corpus) if eflomal_rows else None, "files": files}


def coverage(st: collections.Counter, rows: list[dict]) -> dict:
    return {"verses": st["verses"], "verses_mapped": st["verses_mapped"], "verses_refused": st["verses_refused"],
            "verses_no_text": st["verses_no_text"], "verses_outside_corpus": st["verses_outside_corpus"],
            "links": st["links"], "links_ambiguous": st["links_ambiguous"],
            "links_beyond_text": st["links_beyond_text"], "links_punct_only": st["links_punct_only"],
            "links_no_spine_token": st["links_no_spine_token"], "rows": len(rows),
            "content_rows": sum(1 for r in rows if r["content"]),
            "function_word_rows": sum(1 for r in rows if not r["content"])}


def _match_run(gloss: str, toks: list[str]) -> list[int] | None:
    """Positions of the ONE contiguous token run equal to `gloss` (normalized); None if 0 or 2+ matches."""
    g = [norm_surface(w) for w in gloss.split()]
    if not g:
        return None
    n = [norm_surface(t) for t in toks]
    hits = [i for i in range(len(n) - len(g) + 1) if n[i:i + len(g)] == g]
    return list(range(hits[0], hits[0] + len(g))) if len(hits) == 1 else None


def convert_gbt(iso: str, edition: str | None, books: list[str], corpus: Corpus | None, out_root: Path,
                occ_dir: Path = OCCURRENCE_ALIGN) -> dict | None:
    fp = occ_dir / f"gbt_{iso}.jsonl"
    if not fp.exists():
        return None
    wanted = {BOOK_NUMBERS[b] for b in books}
    by_ref: dict[int, list[dict]] = collections.defaultdict(list)
    with fp.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["verse_ref"] // 1_000_000 in wanted:
                by_ref[r["verse_ref"]].append(r)
    st: collections.Counter = collections.Counter()
    rows = []
    attribution = {"source": "gbt", "kind": "manual", "license": "CC0-1.0", "base_text": "gbt"}
    for ref, recs in sorted(by_ref.items()):
        st["verses"] += 1
        if corpus is None or ref not in corpus.by_key:
            st["verses_outside_corpus"] += 1
            continue
        st["verses_mapped"] += 1
        seen: collections.Counter = collections.Counter()
        toks = corpus.toks[ref]
        for r in sorted(recs, key=lambda r: min(r["source_ids"])):
            keys = []
            for strong in r["source_strong"]:
                keys.append((strong, seen[strong]))
                seen[strong] += 1
            glosses = [g for g in (r.get("target_gloss") or []) if g]
            if not glosses:
                st["links_unglossed"] += 1
                continue
            gloss = " ".join(glosses)
            t_idx = _match_run(gloss, toks) if toks else None
            st["links_target_resolved" if t_idx else "links_target_unresolved"] += 1
            for strong, k in keys:
                tok = corpus.by_key[ref].get((strong, k))
                if tok is None:
                    st["links_no_spine_token"] += 1
                    continue
                pair = _tok_pair(tok, gloss, t_idx, "manual")
                pair["kind"] = r.get("kind")
                attr = dict(attribution, source_ids=[str(s) for s in r["source_ids"]],
                            target_ids=[str(t) for t in r.get("target_ids") or []])
                rows.append(make_row(ref, _book_of(ref), pair, attr))
                st["links"] += 1
    if not rows:
        return None
    tag = edition or "gbt"
    files = write_partition(rows, out_root / iso / tag / "manual" / "gbt")
    cov = coverage(st, rows)
    cov.update({"links_unglossed": st["links_unglossed"], "links_target_resolved": st["links_target_resolved"],
                "links_target_unresolved": st["links_target_unresolved"]})
    return {"source": "gbt", "gold_method": "gbt", "kind": "manual", "license": "CC0-1.0", "publishable": True,
            "quarantined": False, "quarantine_reason": None, "coverage": cov, "health": None, "files": files,
            "edition_note": None if edition else "gbt rows for a language with no ingested edition: t_idx is "
                                                 "never resolvable, target is gbt's own gloss phrase"}


def _read_align(fp) -> list[dict]:
    with fp.open(encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def convert_statistical(iso: str, edition: str, books: list[str], out_dir: Path, out_root: Path,
                        methods: tuple[str, ...] = STAT_METHODS) -> tuple[dict | None, list[dict]]:
    """Every base-chain row for `edition`, each method kept (no first-wins union — consumers union).
    Returns (manifest entry or None when no base-chain output exists, the eflomal rows for health)."""
    wanted = {BOOK_NUMBERS[b] for b in books}
    rows, per_method = [], collections.Counter()
    for m in methods:
        for fp in tag_files(out_dir, m, edition):
            for rec in _read_align(fp):
                if rec["ref"] // 1_000_000 not in wanted:
                    continue
                for p in rec["pairs"]:
                    rows.append(make_row(rec["ref"], rec["book"], p, STATISTICAL_ATTR))
                    per_method[p.get("method", m)] += 1
    if not rows:
        return None, []
    files = write_partition(rows, out_root / iso / edition / "statistical")
    eflomal_rows = [r for r in rows if r["method"] == "eflomal"]
    return {"license": "CC0-1.0", "attribution": STATISTICAL_ATTR, "rows": len(rows),
            "by_method": dict(per_method), "files": files}, eflomal_rows


def llm_cells(edition: str, out_dir: Path) -> list[str]:
    """Run cells `<edition>.<strategy>.<model>...` with output on disk; `.mock` cells excluded (the mock
    provider echoes gapfill's own fills — not a model)."""
    cells = set()
    for fp in out_dir.glob(f"align_llm_{edition}.*.jsonl*"):
        stem = fp.name[len("align_llm_"):]
        stem = stem[:-len(".jsonl.gz")] if stem.endswith(".jsonl.gz") else stem[:-len(".jsonl")]
        cell = stem.rsplit("_", 1)[0]
        if ".mock" not in cell:
            cells.add(cell)
    return sorted(cells)


def convert_llm(iso: str, edition: str, books: list[str], out_dir: Path, out_root: Path) -> dict:
    wanted = {BOOK_NUMBERS[b] for b in books}
    out = {}
    for cell in llm_cells(edition, out_dir):
        usage_fp = out_dir / f"llm_usage_{cell}.json"
        usage = json.loads(usage_fp.read_text(encoding="utf-8")) if usage_fp.exists() else {}
        rows, n_skipped = [], 0
        for fp in tag_files(out_dir, "llm", cell):
            for rec in _read_align(fp):
                if rec["ref"] // 1_000_000 not in wanted:
                    continue
                meta = rec.get("llm") or {}
                attr = {"source": f"{meta.get('provider') or usage.get('provider') or 'llm'}/"
                                  f"{meta.get('model') or usage.get('model') or 'unknown'}",
                        "kind": "model", "license": "CC0-1.0", "cell": cell,
                        "model": meta.get("model") or usage.get("model"),
                        "strategy": meta.get("strategy") or usage.get("strategy"),
                        "prompt_version": usage.get("prompt_version") or meta.get("prompt_sha8")}
                for p in rec["pairs"]:
                    rows.append(make_row(rec["ref"], rec["book"], p, attr))
                for sk in rec.get("llm_skipped", []):        # a verify veto is a decision too: keep it
                    p = dict(sk, t_idx=[], target=None, method="llm", content=True,
                             prior=f"llm_{attr['strategy']}_skipped")
                    rows.append(make_row(rec["ref"], rec["book"], p, attr))
                    n_skipped += 1
        if not rows:
            continue
        files = write_partition(rows, out_root / iso / edition / "llm" / cell)
        out[cell] = {"license": "CC0-1.0", "rows": len(rows), "skipped_rows": n_skipped,
                     "model": usage.get("model"), "strategy": usage.get("strategy"),
                     "prompt_version": usage.get("prompt_version"), "files": files}
    return out


# --- driver -----------------------------------------------------------------------------------------
def _books(a) -> list[str]:
    return (ALL_BOOKS if a.all else OT_BOOKS if a.ot else NT_BOOKS if a.nt
            else [b.upper() for b in (a.book or ALL_BOOKS)])


def convert_language(iso: str, books: list[str], out: Path, internal_out: Path, out_dir: Path,
                     edition_override: str | None = None, base_text: str | None = None,
                     skip_statistical: bool = False, skip_llm: bool = False, res_dir: Path = RESOURCES) -> dict:
    report: dict = {"editions": {}}
    ges = [g for g in gold_editions(iso, res_dir) if not base_text or g["base_text"] == base_text]
    # resolve each gold partition to its edition tag
    for ge in ges:
        if edition_override:
            ge["edition"] = edition_override
        elif ge["candidates"]:
            ge["edition"], ge["probe"] = pick_edition(iso, ge["base_text"], ge["gold_method"], ge["candidates"], res_dir)
    reg = json.loads(GOLD_LANGS.read_text(encoding="utf-8")).get(iso, {}) if GOLD_LANGS.exists() else {}
    primary = reg.get("edition") if isinstance(reg, dict) else None
    # gbt maps onto the language's registered gold edition when there is one (hin/eng/fra/spa/por/rus),
    # else the published primary edition (hun/tam/tel), else nothing (njm: no ingested edition at all)
    gbt_edition = edition_override or primary or GBT_EDITION.get(iso)
    has_gbt = (OCCURRENCE_ALIGN / f"gbt_{iso}.jsonl").exists()
    editions = sorted({g["edition"] for g in ges if g["edition"]} | ({gbt_edition} if has_gbt and gbt_edition else set()))
    corpora: dict[str, Corpus] = {}
    for edition in editions:
        usj = INGEST / f"usj-{edition}"
        if not usj.exists():
            report["editions"][edition] = {"error": f"no ingest dir {usj}"}
            continue
        print(f"[full-align] {iso}/{edition}: loading corpus", file=sys.stderr)
        corpora[edition] = Corpus(books, usj, edition)
        entry: dict = {"layers": {}}
        eflomal_rows: list[dict] = []
        if not skip_statistical:
            stat, eflomal_rows = convert_statistical(iso, edition, books, out_dir, out)
            entry["layers"]["statistical"] = stat or {"absent": "no base-chain output on disk for this edition"}
        if not skip_llm:
            llm = convert_llm(iso, edition, books, out_dir, out)
            if llm:
                entry["layers"]["llm"] = llm
        report["editions"][edition] = entry
    # manual partitions, routed
    eflomal_cache: dict[str, list[dict]] = {}
    for ge in ges:
        edition = ge["edition"]
        if not edition or edition not in corpora:
            report.setdefault("unconverted", []).append({**{k: ge[k] for k in ("base_text", "gold_method")},
                                                         "reason": "no edition text to map onto"})
            continue
        root = out if ge["publishable"] else internal_out
        print(f"[full-align] {iso}/{edition}/manual/{ge['base_text']} ({ge['source']}) → {root}", file=sys.stderr)
        if edition not in eflomal_cache:
            eflomal_cache[edition] = _eflomal_of(out, iso, edition)
        m = convert_manual(ge, edition, books, corpora[edition], root, res_dir, eflomal_rows=eflomal_cache[edition])
        if "probe" in ge:
            m["edition_probe"] = ge["probe"]
        report["editions"][edition]["layers"].setdefault("manual", {})[ge["base_text"]] = m
    if has_gbt:
        edition = gbt_edition if gbt_edition in corpora else None
        g = convert_gbt(iso, edition, books, corpora.get(edition) if edition else None, out)
        if g:
            key = edition or "gbt"
            report["editions"].setdefault(key, {"layers": {}})["layers"].setdefault("manual", {})["gbt"] = g
    lex_note = json.loads(GOLD_LANGS.read_text(encoding="utf-8")).get(iso, {})
    if isinstance(lex_note, dict) and lex_note.get("gold") == "lexicon":
        report["skipped"] = "karnbibeln lexicon gold is a type-level dictionary, not per-occurrence — cannot be a full-align layer"
    return report


def _eflomal_of(out: Path, iso: str, edition: str) -> list[dict]:
    """The just-written statistical eflomal rows (for gold health), read back from the partition."""
    import pyarrow.parquet as pq
    d = out / iso / edition / "statistical"
    rows = []
    for fp in sorted(d.glob("*.parquet")) if d.exists() else []:
        t = pq.read_table(fp, columns=["ref", "strong", "target", "content", "method"],
                          filters=[("method", "==", "eflomal")])
        rows.extend(t.to_pylist())
    return rows


def all_gold_isos(gold_langs: Path = GOLD_LANGS, res_dir: Path = RESOURCES) -> list[str]:
    reg = json.loads(gold_langs.read_text(encoding="utf-8"))
    isos = {k for k in reg if not k.startswith("_")}
    isos |= {p.stem for p in (res_dir / "strongs" / "attestations").glob("*.parquet")}
    return sorted(isos)


def write_card(out: Path, manifest: dict, internal: bool) -> None:
    lines = ["# full-align" + (" (internal, vendor-only partitions)" if internal else ""), "",
             "The complete per-edition alignment as provenance-tagged rows, in up to three layers for the same "
             "text — `statistical` (the base chain's own rows), `manual` (per-occurrence gold converted into the "
             "identical record), `llm` (model rows). Layers are never merged; agreement between them is a signal a "
             "consumer computes. See docs/architecture.md §4.", "",
             "**\"Gold\" is a role, not a method.** `method` keeps lex-lexicon's vocabulary (`eflomal`/`gloss`/"
             "`spanext`/`gapfill`/`llm`/`manual`/`transfer`); who made a row and under what terms is the "
             "`attribution` block (`source`, `kind`, `license`, `base_text`, the source's raw `source_ids`/"
             "`target_ids`, and for model rows `model`/`strategy`/`prompt_version`/`cell`).", "",
             "## Licenses — one per layer / partition",
             "- `statistical/` and `llm/`: **CC0-1.0** (derived alignment data, no source text redistributed).",
             "- `manual/<base_text>/`: the gold source's own license — Clear-Bible CC-BY-4.0, HELFI CC-BY-4.0, "
             "SWORD ChiUns Public Domain, globalbibletools CC0-1.0. CrossWire-restricted SWORD golds are NOT here "
             "(internal tree only).", "",
             "## Contract",
             "- `t_idx` null = the source knew the link but its target could not be placed in this edition's "
             "text (gbt gloss phrases with no unique match); `t_idx: []` = an explicit LLM veto (`llm_skipped`).",
             "- A manual partition with `quarantined: true` in the manifest is published as data for "
             "transparency and is never treated as gold by this repo (its `health` says why).",
             "- Coverage losses are in the manifest per partition, never silent: refused verses (tokenization "
             "mismatch), ambiguous links (Strong's occurrence counts differ between gold and spine — excluded), "
             "links beyond text, punctuation-only links.", "",
             f"Languages: {len(manifest['languages'])} · generated by `python -m lexeme_aligner.gold_to_fullalign`."]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", help="bare gold language (config/gold_langs.json key)")
    ap.add_argument("--all-gold", action="store_true", help="every gold language")
    ap.add_argument("--edition", default=None, help="force the ingest tag the gold text maps onto")
    ap.add_argument("--base-text", default=None, help="only this gold base_text")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--internal-out", type=Path, default=DEFAULT_INTERNAL_OUT)
    ap.add_argument("--out-dir", type=Path, default=OUT, help="where align_*.jsonl live")
    ap.add_argument("--book", action="append")
    ap.add_argument("--nt", action="store_true")
    ap.add_argument("--ot", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--skip-statistical", action="store_true")
    ap.add_argument("--skip-llm", action="store_true")
    a = ap.parse_args(argv)
    if not a.iso and not a.all_gold:
        ap.error("--iso or --all-gold")
    books = _books(a)
    isos = all_gold_isos() if a.all_gold else [a.iso]
    manifests = {}
    for root in (a.out, a.internal_out):
        mf = root / "manifest.json"
        manifests[root] = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {"languages": {}}
    for iso in isos:
        rep = convert_language(iso, books, a.out, a.internal_out, a.out_dir, a.edition, a.base_text,
                               a.skip_statistical, a.skip_llm)
        # split the report by where each partition landed
        pub = {"editions": {}}
        internal = {"editions": {}}
        for ed, entry in rep["editions"].items():
            layers = entry.get("layers", {})
            pub_layers = {k: v for k, v in layers.items() if k != "manual"}
            pub_manual = {bt: m for bt, m in layers.get("manual", {}).items() if m.get("publishable")}
            int_manual = {bt: m for bt, m in layers.get("manual", {}).items() if not m.get("publishable")}
            if pub_manual:
                pub_layers["manual"] = pub_manual
            pub["editions"][ed] = {**{k: v for k, v in entry.items() if k != "layers"}, "layers": pub_layers}
            if int_manual:
                internal["editions"][ed] = {"layers": {"manual": int_manual}}
        for k in ("unconverted", "skipped"):
            if k in rep:
                pub[k] = rep[k]
        manifests[a.out]["languages"][iso] = pub
        if internal["editions"]:
            manifests[a.internal_out]["languages"][iso] = internal
        print(f"[full-align] {iso}: {json.dumps(_summary(rep), ensure_ascii=False)}", file=sys.stderr)
    for root, m in manifests.items():
        if not m["languages"]:
            continue
        root.mkdir(parents=True, exist_ok=True)
        m["layout"] = "<iso>/<edition>/{statistical,manual/<base_text>,llm/<cell>}/<BOOK>.parquet"
        m["schema"] = [f.name for f in _schema()]
        (root / "manifest.json").write_text(json.dumps(m, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                                            encoding="utf-8")
        write_card(root, m, internal=(root == a.internal_out))
    return 0


def _summary(rep: dict) -> dict:
    out = {}
    for ed, entry in rep["editions"].items():
        L = entry.get("layers", {})
        s = {}
        if "statistical" in L:
            s["statistical"] = L["statistical"].get("rows", L["statistical"].get("absent"))
        for bt, m in L.get("manual", {}).items():
            c = m["coverage"]
            s[f"manual/{bt}"] = {"rows": c["rows"], "mapped": c["verses_mapped"], "refused": c["verses_refused"],
                                 "ambiguous": c["links_ambiguous"], "health": (m.get("health") or {}).get("positional"),
                                 "publishable": m["publishable"], "quarantined": m["quarantined"]}
        if "llm" in L:
            s["llm"] = {cell: v["rows"] for cell, v in L["llm"].items()}
        out[ed] = s
    return out


if __name__ == "__main__":
    raise SystemExit(main())
