"""Full alignments in the compact container format, for one edition: chain step 9a (after compact-alignments, before the ledger).

    .venv/bin/python -m lexeme_aligner.fullalign_build --iso bsb --publish-iso eng --usj-dir pipeline/work/ingest-cache/usj-bsb
    .venv/bin/python -m lexeme_aligner.fullalign_build ... --stat-out pipeline/work/fa-scratch --manual-out pipeline/work/fa-scratch   # nothing under publish/
    .venv/bin/python -m lexeme_aligner.fullalign_build ... --show "RUT 1:1"                                                          # every layer of one verse

Two kinds of layer (plan internal-docs/full-alignments-two-paths-plan-2026-10-09.md, A1):

  statistical   every row of the base chain (eflomal, gloss, spanext, gapfill, residual; losing rows kept), read from the SAME
                align_*.jsonl compact_align read a moment earlier. The main array IS compact's (byte-identical; checked); the new
                channels fn / wp / fp / wx / rows / off are merged INTO compact's own `<BOOK>_<hash>.meta.json`, and `_layer.json`
                (the profile table) sits next to them. A compact reader keeps working unchanged. OPT-IN: `--stat-in-place` (or
                `--stat-out` for a scratch tree); without either only the manual layers are built (owner decision 2026-10-09).
  manual        one layer per published gold source of this edition (Clear, HELFI, SWORD/ChiUns, BSB tables; Door43 once registered),
                id `<compact edition>+manual+<source>`, own main + meta, written to `publish/full-alignments-manual/` (CC BY / PD / CC0)
                or `publish/full-alignments-manual-sa/` (Door43, CC BY-SA 4.0), same relative paths as compact. Source rows come from the
                full-align parquet the converters (eval/gold_to_fullalign.py, eval/bsb_tables.py) write into `--src-root`; gold does not
                change between chain runs, so those are regenerated only when a converter or a gold file changes.

Left out on purpose: GBT layers (set aside, owner 2026-10-05), quarantined partitions (rus RUSSYN: Clear's own gold is positionally
wrong), `transfer` partitions (por JFA11: machine-projected, owner 2026-10-09), vendor-only partitions (not in `--src-root/pub`), LLM layers
(never published).

SCOPE: the chain calls this for every edition; it builds only editions that have a manual layer in `--src-root` (the full-align editions),
unless `--force`. That keeps the meta repo's growth bounded (+~4.5 MB per edition) until a wider rollout is decided.

Every layer is VERIFIED through the written files (exit 1 on any problem): (1) decode == the canonical rows the encoder started from
(multiset); (2) the fields not stored (lexeme strong lemma stem surface gloss content target) equal what the spine + text give;
(3) BSB tables: markers / joint spans recomputed from the table order; (4) statistical: main array and every pre-existing compact
channel byte-identical to compact's; (5) after the last book, EVERY book is decoded again with the FINAL `_layer.json` (one profile table
per layer, characters only appended; the 2026-10-06 pilot wrote a fresh table per book, so 33 of its 66 statistical books could not be
decoded from the files it left).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import shutil
import sys
from pathlib import Path

import lexeme_aligner.fullalign_compact as fc

STAT_METHODS = ("eflomal", "gloss", "spanext", "gapfill", "residual")
FA_CHANNELS = ("fn", "wp", "fp", "wx", "rows", "off")          # what this builder adds to compact's meta
COMPACT_ROOT = Path("publish/compact-alignments")
SRC_ROOT = Path("pipeline/work/full-align-src/pub")
MANUAL_ROOTS = {"manual": Path("publish/full-alignments-manual"), "manual-sa": Path("publish/full-alignments-manual-sa")}
SA_SOURCES = {"door43"}                                        # share-alike sources: their own repo
SKIP_SOURCES = {"gbt": "GBT layers are set aside (owner 2026-10-05)"}
HAS_IDS = {"clear", "door43"}                                  # sources whose rows carry gold word ids (wx channel)


# --- rows -> canonical rows (moved from pipeline/scripts/tools/fullalign_pilot.py) ------------------------------------
def read_rows(path: Path, kind: str) -> list[dict]:
    import pyarrow.parquet as pq
    cols = None
    if kind == "bsb":
        cols = ["ref", "book", "chapter", "verse", "h_idx", "h_idx_key", "lexeme", "strong", "t_idx", "target", "content",
                "method", "score", "attribution"]
    return pq.read_table(path, columns=cols).to_pylist() if path.exists() else []


def base_attribution(rows: list[dict]) -> dict:
    cnt = collections.Counter(json.dumps({k: v for k, v in r["attribution"].items() if k not in ("source_ids", "target_ids")},
                                         sort_keys=True) for r in rows)
    return json.loads(cnt.most_common(1)[0][0]) if cnt else {}


def is_padding(r: dict) -> bool:
    """A BSB-tables row that says nothing about alignment: no source token, no target words, no marker (an empty cell). The table
    uses such rows only to carry headings / footnotes / cross-references in its own text columns, which this format does not carry."""
    return not r["h_idx"] and not r["t_idx"] and not (r.get("target") or "").strip()


def _hidx(r: dict) -> set:
    h = r.get("h_idx")
    return {h} if isinstance(h, int) else set(h or [])


def to_crows(kind: str, rows: list[dict], groups: dict, base: dict, has_ids: bool,
             dropped: collections.Counter | None = None, title_tokens: dict | None = None) -> dict[str, list]:
    """`title_tokens` ({"PSA 3:1": {spine idx, ...}}): for an edition that prints psalm titles as headings (psalm_titles mode
    `heading`) the statistical layer's alignments on those tokens are noise (the English verse has no title words), so rows made
    only of title tokens are left out of the statistical layer."""
    by_verse: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if kind == "statistical" and title_tokens:
            tt = title_tokens.get(f"{r['book']} {r['chapter']}:{r['verse']}")
            hs = _hidx(r)
            if tt and hs and hs <= tt:
                if dropped is not None:
                    dropped["psalm-title token rows dropped (edition prints titles as headings)"] += 1
                continue
        if kind == "bsb" and is_padding(r):
            if dropped is not None:
                dropped["padding rows dropped (empty cell, no source)"] += 1
            continue
        by_verse[f"{r['book']} {r['chapter']}:{r['verse']}"].append(r)
    out: dict[str, list] = {}
    letters = None
    if kind == "bsb":
        from lexeme_aligner.eval.bsb_tables import _letters as letters
    for ref, vrows in by_verse.items():
        if kind == "bsb":
            if ref in groups:                                         # ALL rows of the verse, in table order (see bsb_crows)
                crows = fc.bsb_crows(vrows, groups[ref], base, letters)
            else:                                                     # an English verse with no spine verse: nothing is placed
                crows = []
                for r in vrows:
                    if r["h_idx"]:
                        raise KeyError(f"{ref}: BSB-table rows with source tokens but no spine group")
                    c = fc.CRow(ref=ref, span=None if r["t_idx"] is None else list(r["t_idx"]))
                    c.prof = (r["method"], r["score"], None, None, fc.attr_diff(r["attribution"], base))
                    if r.get("strong") is not None:
                        c.tstrong = r["strong"]
                    crows.append(c)
        else:
            if ref not in groups:
                if dropped is not None:
                    dropped["rows of a verse with no spine group"] += len(vrows)
                continue
            crows = [fc.simple_crow(r, groups[ref], base, has_ids) for r in vrows]
        out[ref] = crows
    return out


def verify_derived(kind: str, rows: list[dict], groups: dict) -> collections.Counter:
    """Check 2: fields the encoding does not store must be derivable."""
    bad: collections.Counter = collections.Counter()
    for r in rows:
        ref = f"{r['book']} {r['chapter']}:{r['verse']}"
        g = groups.get(ref)
        h = r["h_idx"]
        if not h and h != 0:
            continue
        key = h if isinstance(h, int) else r.get("h_idx_key")
        tok = g.tok_of.get(key) if g and key is not None else None
        if tok is None:
            if kind == "bsb" and r.get("h_idx_key") is None:          # attached by adjacency: no keyed token, so none derived
                if r["lexeme"] is not None or r["content"] is not None:
                    bad["lexeme/content set without a key token"] += 1
            elif g is None:
                pass                                                  # counted by to_crows as "no spine group"
            else:
                bad["no key token"] += 1
            continue
        for k, v in (("lexeme", tok.lexeme), ("content", bool(tok.is_content))):
            if r[k] != v:
                bad[k] += 1
        if kind != "bsb":
            for k, v in (("strong", tok.strong), ("lemma", tok.lemma), ("stem", tok.stem), ("surface", tok.surface),
                         ("gloss_en", tok.gloss_en)):
                if r[k] != v:
                    bad[k] += 1
            t = r["t_idx"]
            if t is not None and r["target"] != " ".join(g.toks[i] for i in t if i < len(g.toks)):
                bad["target"] += 1
    return bad


def verify_bsb_semantics(rows: list[dict], decoded: list, groups: dict) -> collections.Counter:
    """Check 3: rebuild the table's own fields from the decoded canonical rows and compare with the parquet rows; recompute
    marker anchors and joint spans from the table order independently."""
    res: collections.Counter = collections.Counter()
    by_verse_rows: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if not is_padding(r):                                         # dropped on purpose and counted separately
            by_verse_rows[f"{r['book']} {r['chapter']}:{r['verse']}"].append(r)
    dec_by_ref: dict[str, list] = collections.defaultdict(list)
    for c in decoded:
        dec_by_ref[c.ref].append(c)
    for ref, vrows in by_verse_rows.items():
        g = groups.get(ref)

        def row_fields(c):
            hs = [g.h_of[s] for s in c.src] if c.src else None
            if c.mark:
                cell, t = {"u": "-", "e": ". . ."}[c.mark[1]], []
            elif c.joint:
                cell, t = "vvv", []
            else:
                cell, t = None, (None if c.span is None else sorted(set(c.span) | set(c.sup) | set(c.inf)))
            return (json.dumps(hs), json.dumps(hs[c.key] if hs and c.key is not None else None), json.dumps(t), cell)

        want = collections.Counter()
        for r in vrows:
            cell = (r.get("target") or "").strip()
            t = r["t_idx"]
            marker = cell if t is not None and not t and (cell in fc.MARK_ROWS or cell == fc.JOINT_CELL) else None
            h = r["h_idx"] or None
            want[(json.dumps(h), json.dumps(r.get("h_idx_key") if h else None), json.dumps(t), marker)] += 1
        got = collections.Counter(row_fields(c) for c in dec_by_ref.get(ref, []))
        res["rows"] += sum(want.values())
        res["rows lost"] += sum((want - got).values())
        res["rows invented"] += sum((got - want).values())
        last_end = -1
        mine = [c for c in dec_by_ref.get(ref, []) if c.mark or c.joint]
        for r in vrows:
            t = r["t_idx"]
            if t:
                last_end = max(t)
            elif t is not None and (r.get("target") or "").strip() in fc.MARK_ROWS and r["h_idx"]:
                res["markers"] += 1
                kind = fc.MARK_ROWS[(r["target"] or "").strip()]
                if not any(c.mark == (last_end, kind) and [g.h_of[s] for s in c.src] == r["h_idx"] for c in mine):
                    res["marker anchor mismatches"] += 1
    return res


# --- the word-check veto (A3) ------------------------------------------------------------------------------------------
VETO_ORDER = ("spanext", "eflomal", "gloss", "gapfill")       # first method wins a token, as in word_checks.veto_ab


def apply_veto(crows_by_ref: dict[str, list], groups: dict, checks: tuple[str, ...], facts: dict, is_function, t: collections.Counter) -> None:
    """Mark the statistical FUNCTION-word rows that one of `checks` flags (profile extra `veto: <check>`), so the fn channel leaves
    the token empty and the row stays in `rows`. `is_function(group, position)`."""
    from lexeme_aligner.eval.word_checks import flagged_idx
    for ref, crows in crows_by_ref.items():
        g = groups.get(ref)
        if g is None:
            continue
        aligned: dict[int, list[int]] = {}
        for m in VETO_ORDER:
            for c in crows:
                if c.prof and c.prof[0] == m and len(c.src) == 1 and c.span:
                    aligned.setdefault(g.h_of[c.src[0]], sorted(c.span))
        if not aligned:
            continue
        fl = flagged_idx(list(g.tok_of.values()), aligned, facts, lambda p, g=g: is_function(g, p), checks)
        if not fl:
            continue
        for c in crows:
            if len(c.src) == 1 and c.src[0][0] == "f":
                chk = fl.get(g.h_of[c.src[0]])
                if chk:
                    m, sc, pr, ex, at = c.prof
                    c.prof = (m, sc, pr, fc.with_veto(ex, chk), at)
                    t[f"fn rows vetoed ({chk})"] += 1


# --- inputs ------------------------------------------------------------------------------------------------------------
def statistical_rows(tag: str, books: list[str], out_dir: Path, methods=STAT_METHODS) -> dict[str, list[dict]]:
    """{book: [full-align row]} — EVERY row of every method (not compact's winners), the same rows
    gold_to_fullalign.convert_statistical writes to parquet."""
    from lexeme_aligner.align_files import tag_files
    from lexeme_aligner.eval.gold_to_fullalign import STATISTICAL_ATTR, make_row
    from lexeme_aligner.refs import BOOK_NUMBERS
    wanted = {BOOK_NUMBERS[b]: b for b in books}
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    b = wanted.get(rec["ref"] // 1_000_000)
                    if b is None:
                        continue
                    for p in rec["pairs"]:
                        out[b].append(make_row(rec["ref"], rec["book"], p, STATISTICAL_ATTR))
    return out


def fa_edition_of(publish_iso: str, tag: str, src_manifest: dict, gold_langs: dict) -> str | None:
    """The full-align edition (path name in --src-root) whose gold maps onto chain tag `tag`: the tag itself, or the gold
    registry entry whose `align_tag` is this tag (eng bsb -> engbsb, fra fra_lsg -> fra-lsg)."""
    eds = (src_manifest.get("languages", {}).get(publish_iso) or {}).get("editions", {})
    if tag in eds:
        return tag
    reg = gold_langs.get(publish_iso)
    if isinstance(reg, dict) and reg.get("align_tag") == tag and reg.get("edition") in eds:
        return reg["edition"]
    return None


def manual_layers(publish_iso: str, fa_edition: str, src_manifest: dict) -> tuple[list[dict], list[str]]:
    """([{part, source, base_text, license, kind}], [why each left-out partition is left out]) for one full-align edition."""
    ed = (src_manifest.get("languages", {}).get(publish_iso) or {}).get("editions", {}).get(fa_edition) or {}
    out, skipped = [], []
    for part, m in sorted((ed.get("layers") or {}).get("manual", {}).items()):
        src = m.get("source")
        why = (SKIP_SOURCES.get(src) or ("quarantined: " + (m.get("quarantine_reason") or "") if m.get("quarantined") else None)
               or ("kind transfer (machine-projected, not manual; owner 2026-10-09)" if m.get("kind") == "transfer" else None)
               or (None if m.get("publishable", True) else "not publishable"))
        if why:
            skipped.append(f"{part} ({src}): {why}")
            continue
        out.append({"part": part, "source": src, "base_text": part, "license": m.get("license"),
                    "kind": "bsb" if src == "bsb-tables" else "manual"})
    return out, skipped


def title_tokens_of(tag: str, usj_dir: Path, heb) -> dict[str, set]:
    from lexeme_aligner.psalm_titles import title_mode
    if title_mode(tag, usj_dir, heb)["mode"] != "heading":
        return {}
    out = {}
    for ch in heb.chapters("PSA"):
        for v in heb.verses("PSA", ch):
            tt = {t.idx for t in heb.verse_tokens("PSA", ch, v) if t.is_superscription}
            if tt:
                out[f"PSA {ch}:{v}"] = tt
    return out


def _compact_book(ed_dir: Path, book: str) -> tuple[Path | None, list | None, dict]:
    cm = [f for f in ed_dir.glob(f"{book}_*.json") if f.name.count(".") == 1]
    if not cm:
        return None, None, {}
    mp = cm[0].with_name(cm[0].stem + ".meta.json")
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    return cm[0], json.loads(cm[0].read_text(encoding="utf-8")), meta


# --- one layer of one book ---------------------------------------------------------------------------------------------
def _canon_digest(crows) -> str:
    return hashlib.sha256("\n".join(sorted(repr(c.canon()) for c in crows)).encode("utf-8")).hexdigest()


def encode_verify(kind: str, lid: str, rows: list[dict], groups: dict, refs: list[str], layer_dir: Path, book: str,
                  digest: str, t: collections.Counter, encoders: dict, *, has_ids: bool, title_tokens=None,
                  compact_main: list | None = None, compact_meta: dict | None = None, veto=None) -> str:
    """Encode `rows` into `layer_dir`, then decode the WRITTEN files and run checks 1-4 into counter `t`. For the statistical layer
    (`compact_main` given) the main array written is compact's own and the new channels are merged into compact's meta."""
    base = base_attribution(rows)
    le = encoders.get(lid)
    if le is None:
        le = encoders[lid] = fc.LayerEncoder(lid, "statistical" if kind == "statistical" else "manual", base, has_ids)
    elif le.base != base:
        t["books whose base attribution differs from the layer's first book"] += 1
    dropped: collections.Counter = collections.Counter()
    crows = to_crows(kind, rows, groups, le.base, has_ids, dropped, title_tokens if book == "PSA" else None)
    t.update(dropped)
    if veto:                                                    # (checks, facts, is_function)
        apply_veto(crows, groups, *veto, t)
    hints = None
    if compact_main is not None:
        hints = {ref: {int(e.split(":")[0]): fc.dec_span(e.split(":")[1]) for e in compact_main[i].split()}
                 for i, ref in enumerate(refs) if compact_main[i]}
    main_arr, meta = le.encode_book(crows, refs, groups, hints)
    if compact_main is not None:
        if main_arr != compact_main:
            t["main differs from compact"] += 1
            i = next(i for i, (x, y) in enumerate(zip(main_arr, compact_main)) if x != y)
            print(f"   [diag] {lid} {book} {refs[i]}: encoder main {main_arr[i]!r} vs compact {compact_main[i]!r}", file=sys.stderr)
        mp = layer_dir / f"{book}_{digest}.meta.json"           # idempotent: drop this builder's channels from an earlier run first
        if mp.exists():
            cur = json.loads(mp.read_text(encoding="utf-8"))
            if any(k in cur for k in FA_CHANNELS):
                kept = {k: v for k, v in cur.items() if k not in FA_CHANNELS}
                mp.write_text(json.dumps(kept, ensure_ascii=False) + "\n", encoding="utf-8")
        fc.write_book(layer_dir, book, digest, compact_main, meta, merge=True)
    else:
        for old in layer_dir.glob(f"{book}_*.json"):              # a revised text has a new digest: never leave the old file behind
            if not old.name.startswith(f"{book}_{digest}."):
                old.unlink()
        fc.write_book(layer_dir, book, digest, main_arr, meta)
    fc.write_layer_json(layer_dir, le)
    # ---- decode from the FILES and compare
    main_back, meta_back = fc.read_book(layer_dir, book)
    if compact_main is not None:
        for k, v in (compact_meta or {}).items():
            if k not in FA_CHANNELS and meta_back.get(k) != v:
                t["compact channel changed"] += 1
        if main_back != compact_main:
            t["main differs from compact"] += 1
    dec = fc.LayerDecoder(fc.read_layer_json(layer_dir)).decode_book(main_back, meta_back, refs, groups)
    a_ = collections.Counter(c.canon() for cl in crows.values() for c in cl)
    b_ = collections.Counter(c.canon() for c in dec)
    t["rows"] += sum(a_.values())
    t["rows lost"] += sum((a_ - b_).values())
    t["rows invented"] += sum((b_ - a_).values())
    if a_ - b_:
        print(f"   [diag] {lid} {book}: {sum((a_ - b_).values())} rows lost, e.g. {str(next(iter(a_ - b_)))[:260]}", file=sys.stderr)
    for k, v in verify_derived(kind, rows, groups).items():
        t[f"derived mismatch: {k}"] += v
    if kind == "bsb":
        t["rows (excluding padding)"] += sum(1 for r in rows if not is_padding(r))
        for k, v in verify_bsb_semantics(rows, dec, groups).items():
            if k != "rows":
                t[k] += v
        t["supplied words"] += sum(len(c.sup) for c in dec)
        t["joint rows"] += sum(c.joint for c in dec)
        t["marker rows"] += sum(1 for c in dec if c.mark)
    t["books"] += 1
    t["container bytes"] += sum(f.stat().st_size for f in layer_dir.glob(f"{book}_{digest}*.json") if ".extra." not in f.name)
    return _canon_digest(c for cl in crows.values() for c in cl)


def final_check(lid: str, layer_dir: Path, written: dict, groups_of: dict, refs_of: dict, t: collections.Counter) -> None:
    """Check 5: decode every written book of the layer with the FINAL _layer.json."""
    dec = fc.LayerDecoder(fc.read_layer_json(layer_dir))
    for book, want in written.items():
        main, meta = fc.read_book(layer_dir, book)
        try:
            got = _canon_digest(dec.decode_book(main, meta, refs_of[book], groups_of[book]))
        except (KeyError, ValueError) as e:
            got = f"error {e}"
        if got != want:
            t["books not decodable with the final _layer.json"] += 1


def problems(t: collections.Counter) -> list[str]:
    return [k for k, v in t.items() if v and (k in ("rows lost", "rows invented", "main differs from compact", "compact channel changed",
                                                     "rows of a verse with no spine group", "books not decodable with the final _layer.json")
                                               or k.startswith("derived mismatch") or "mismatch" in k)]


# --- one edition -------------------------------------------------------------------------------------------------------
def build_edition(tag: str, publish_iso: str, usj_dir: Path, *, books: list[str] | None = None, methods=STAT_METHODS,
                  out_dir: Path | None = None, compact_root: Path = COMPACT_ROOT, src_root: Path = SRC_ROOT,
                  stat_out: Path | None = None, manual_out: Path | None = None, layers=("statistical", "manual"),
                  force: bool = False, heb=None, gold_langs_path: Path = Path("config/gold_langs.json"),
                  sources_path: Path = Path("config/sources.json")) -> dict:
    """Build (and verify) the full-align layers of one chain edition. Returns a report {edition, layers: {lid: counts}, skipped,
    out_of_scope}. `stat_out` / `manual_out` redirect the writes into a scratch tree (the compact edition's files are copied there
    first, so nothing under publish/ changes)."""
    from lexeme_aligner.compact_align import ALL_BOOKS, book_content_hash, edition_id
    from lexeme_aligner.config import OUT
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import _BOOK_FILE_NUM
    from lexeme_aligner.versification import remapper
    out_dir = Path(out_dir or OUT)
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    ced = edition_id(publish_iso, tag, sources)
    report: dict = {"tag": tag, "iso": publish_iso, "edition": ced, "layers": {}, "skipped": []}
    src_manifest_fp = src_root / "manifest.json"
    src_manifest = json.loads(src_manifest_fp.read_text(encoding="utf-8")) if src_manifest_fp.exists() else {}
    gold_langs = json.loads(gold_langs_path.read_text(encoding="utf-8")) if gold_langs_path.exists() else {}
    fa_ed = fa_edition_of(publish_iso, tag, src_manifest, gold_langs)
    mlayers, skipped = manual_layers(publish_iso, fa_ed, src_manifest) if fa_ed else ([], [])
    report["skipped"] += skipped
    report["fa_edition"] = fa_ed
    if not mlayers and not force:
        report["out_of_scope"] = "no published manual layer for this edition (the full-align editions only; --force to build anyway)"
        return report
    heb = heb or HebrewSource()
    remap = remapper(tag, str(usj_dir))
    index_root = compact_root / "_index"
    books = books or [b for b in ALL_BOOKS if (index_root / f"{b}_lexemes.json").exists()]
    compact_ed_dir = compact_root / publish_iso[0] / publish_iso / ced
    stat_dir = (stat_out or compact_root) / publish_iso[0] / publish_iso / ced
    fa_usj = Path("pipeline/work/ingest-cache") / f"usj-{fa_ed}" if fa_ed else None
    title_tokens = title_tokens_of(tag, usj_dir, heb) if "statistical" in layers else {}
    stat_rows = statistical_rows(tag, books, out_dir, methods) if "statistical" in layers else {}
    veto = None
    if "statistical" in layers:
        from lexeme_aligner.eval.word_checks import language_facts, veto_checks_for
        checks = veto_checks_for(publish_iso)
        if checks:
            from lexeme_aligner.target_stopwords import StopwordFilter
            stop = StopwordFilter(publish_iso, str(usj_dir))
            veto = (checks, language_facts(publish_iso), lambda g, p: p < len(g.toks) and stop.is_function(g.toks[p]))
            report["fn_veto"] = list(checks)
    totals: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    encoders: dict = {}
    written: dict[str, dict] = collections.defaultdict(dict)        # lid -> {book: canon digest}
    layer_dirs: dict[str, Path] = {}
    groups_of, refs_of = {}, {}
    lids = {}
    for ml in mlayers:
        root = manual_out or MANUAL_ROOTS["manual-sa" if ml["source"] in SA_SOURCES else "manual"]
        lid = f"{ced}+manual+{ml['source']}"
        lids[lid] = (ml, root / publish_iso[0] / publish_iso / lid)
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not usj_path.exists():
            continue
        refs = list(json.loads((index_root / f"{book}_lexemes.json").read_text(encoding="utf-8")))
        try:
            groups = fc.build_groups(heb, book, usj_path, remap)
        except NotImplementedError as e:
            report["refused"] = str(e)
            return report
        digest = book_content_hash(usj_path)[-5:]
        groups_of[book], refs_of[book] = groups, refs
        if "statistical" in layers:
            cpath, cmain, cmeta = _compact_book(compact_ed_dir, book)
            if cpath is None:
                totals[ced]["books with no compact file (skipped)"] += 1
            elif stat_rows.get(book):
                if stat_dir != compact_ed_dir:                       # scratch tree: start from compact's own files
                    stat_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy(cpath, stat_dir / cpath.name)
                    mp = cpath.with_name(cpath.stem + ".meta.json")
                    if mp.exists():
                        shutil.copy(mp, stat_dir / mp.name)
                written[ced][book] = encode_verify(
                    "statistical", ced, stat_rows[book], groups, refs, stat_dir, book, cpath.stem.split("_")[-1], totals[ced],
                    encoders, has_ids=False, title_tokens=title_tokens, compact_main=cmain, compact_meta=cmeta, veto=veto)
                layer_dirs[ced] = stat_dir
        if "manual" in layers:
            bad_refs = None                                     # verses whose TOKENS differ between the gold's text and this text
            for lid, (ml, ldir) in lids.items():
                rows = read_rows(src_root / publish_iso / fa_ed / "manual" / ml["part"] / f"{book}.parquet", ml["kind"])
                if not rows:
                    continue
                if bad_refs is None:
                    bad_refs = set()
                    if fa_ed != tag:                            # the gold was mapped onto fa_ed's text: positions hold where the tokens agree
                        fp = next(iter(sorted(fa_usj.glob(f"*-{book}.json"))), None) if fa_usj else None
                        if fp is None:
                            bad_refs = set(groups)
                        elif book_content_hash(fp) != book_content_hash(usj_path):
                            fg = fc.build_groups(heb, book, fp, remapper(fa_ed, str(fa_usj)))
                            bad_refs = {r for r, g in groups.items() if fg.get(r) is None or fg[r].toks != g.toks}
                if bad_refs:
                    keep = [r for r in rows if f"{r['book']} {r['chapter']}:{r['verse']}" not in bad_refs]
                    totals[lid]["rows dropped: verse tokenizes differently in the gold's text"] += len(rows) - len(keep)
                    totals[lid]["verses dropped: tokenize differently in the gold's text"] += len(bad_refs)
                    rows = keep
                if not rows:
                    continue
                written[lid][book] = encode_verify(ml["kind"], lid, rows, groups, refs, ldir, book, digest, totals[lid],
                                                   encoders, has_ids=ml["source"] in HAS_IDS)
                layer_dirs[lid] = ldir
    for lid, books_done in written.items():
        final_check(lid, layer_dirs[lid], books_done, groups_of, refs_of, totals[lid])
    for lid, t in totals.items():
        report["layers"][lid] = dict(t)
        report["layers"][lid]["problems"] = problems(t)
    if lids:
        _update_manual_manifests(publish_iso, ced, lids, report, manual_out)
    return report


def _update_manual_manifests(iso: str, ced: str, lids: dict, report: dict, manual_out: Path | None) -> None:
    from lexeme_aligner.manifest_io import update_json
    by_root: dict[Path, dict] = collections.defaultdict(dict)
    for lid, (ml, ldir) in lids.items():
        t = report["layers"].get(lid)
        if not t or t.get("problems"):
            continue
        root = ldir.parents[2]
        files = {f.name.split("_")[0]: f.name for f in sorted(ldir.glob("*_*.json")) if f.name.count(".") == 1 and not f.name.startswith("_")}
        by_root[root][lid] = {"edition": ced, "source": ml["source"], "base_text": ml["base_text"], "license": ml["license"],
                              "rows": t.get("rows", 0), "books": files, "has_ids": ml["source"] in HAS_IDS}

    for root, entries in by_root.items():
        def mutate(doc, entries=entries):
            lang = doc.setdefault("languages", {}).setdefault(iso, {"layers": {}})
            lang.setdefault("layers", {}).update(entries)
            doc["layout"] = "<iso[0]>/<iso>/<edition>+manual+<source>/<BOOK>_<hash>.json (+ .meta.json, _layer.json)"
        update_json(root / "manifest.json", mutate)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="the chain's edition TAG (as compact_align --iso)")
    ap.add_argument("--publish-iso", required=True)
    ap.add_argument("--usj-dir", type=Path, required=True)
    ap.add_argument("--methods", default=",".join(STAT_METHODS), help="statistical methods carried (every row of each)")
    ap.add_argument("--book", action="append")
    ap.add_argument("--layers", default="statistical,manual")
    ap.add_argument("--src-root", type=Path, default=SRC_ROOT, help="full-align parquet the converters wrote (manual source rows)")
    ap.add_argument("--compact-root", type=Path, default=COMPACT_ROOT)
    ap.add_argument("--stat-out", type=Path, default=None, help="scratch tree for the statistical layer")
    ap.add_argument("--stat-in-place", action="store_true",
                    help="merge the statistical channels INTO publish/compact-alignments' meta files. Off by default: owner decision "
                         "2026-10-09, no merge yet — without this (and without --stat-out) only the manual layers are built")
    ap.add_argument("--manual-out", type=Path, default=None, help="scratch tree for the manual layers (default: publish/full-alignments-manual[-sa])")
    ap.add_argument("--force", action="store_true", help="build the statistical channels even without a manual layer")
    ap.add_argument("--report", type=Path, default=None, help="write the JSON report here")
    a = ap.parse_args(argv)
    layers = tuple(a.layers.split(","))
    if "statistical" in layers and a.stat_out is None and not a.stat_in_place:
        layers = tuple(x for x in layers if x != "statistical")
        print(f"[fullalign_build] {a.iso}: statistical channels not merged into compact meta (pass --stat-in-place or --stat-out)",
              file=sys.stderr)
    rep = build_edition(a.iso, a.publish_iso, a.usj_dir, books=a.book, methods=tuple(a.methods.split(",")),
                        compact_root=a.compact_root, src_root=a.src_root, stat_out=a.stat_out, manual_out=a.manual_out,
                        layers=layers, force=a.force)
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if rep.get("out_of_scope"):
        print(f"[fullalign_build] {a.iso}: skipped — {rep['out_of_scope']}", file=sys.stderr)
        return 0
    if rep.get("refused"):
        print(f"[fullalign_build] {a.iso}: refused — {rep['refused']}", file=sys.stderr)
        return 0
    for s in rep["skipped"]:
        print(f"[fullalign_build] {a.iso}: left out {s}", file=sys.stderr)
    bad = False
    for lid, t in rep["layers"].items():
        print(f"[fullalign_build] {lid}: " + ", ".join(f"{k} {v:,}" for k, v in t.items() if k != "problems" and v), file=sys.stderr)
        if t["problems"]:
            bad = True
            print(f"[fullalign_build] {lid}: PROBLEMS {t['problems']}", file=sys.stderr)
    print(f"[fullalign_build] {a.iso}: {'ROUND TRIP OK' if not bad else 'PROBLEMS FOUND'}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
