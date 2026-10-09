"""Build one edition's full-align layers in the compact container format (scratch tree) and VERIFY the round trip.

    .venv/bin/python pipeline/scripts/tools/fullalign_pilot.py --books RUT GEN            # a quick look
    .venv/bin/python pipeline/scripts/tools/fullalign_pilot.py                            # all 66 books

Inputs  : the full-align parquet tree (`--parquet-root`, default the rebuilt scratch baseline of 2026-10-06), the edition's
          compact files (`publish/compact-alignments/e/eng/eng_BSB`, read only), the spine and the edition text.
Outputs : `--out`/e/eng/eng_BSB (compact meta + the new channels; main is compact's own), eng_BSB+manual+clear,
          eng_BSB+manual+bsb-tables, each with `_layer.json`.
Checks  : (1) decode(files) == the canonical rows the encoder started from, as multisets, THROUGH the written JSON files;
          (2) every parquet row's derived fields (lexeme, strong, lemma, stem, surface, gloss_en, content, target text)
              equal what the spine + edition text give, so they need not be stored;
          (3) BSB tables: markers / joint spans / supplied words recomputed independently from the table order and checked;
          (4) the statistical layer's main array and compact channels are byte-identical to compact's.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "pipeline"))

import pyarrow.parquet as pq  # noqa: E402

import lexeme_aligner.fullalign_compact as fc  # noqa: E402
from lexeme_aligner.compact_align import book_content_hash  # noqa: E402
from lexeme_aligner.hebrew_source import HebrewSource  # noqa: E402
from lexeme_aligner.compact_align import ALL_BOOKS  # noqa: E402
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM  # noqa: E402
from lexeme_aligner.versification import remapper  # noqa: E402

ISO, EDITION, COMPACT_ED, USJ_TAG = "eng", "engbsb", "eng_BSB", "engbsb"
INDEX = REPO / "publish/compact-alignments/_index"
COMPACT = REPO / "publish/compact-alignments/e/eng/eng_BSB"
LAYERS = [("statistical", "statistical", COMPACT_ED, False),
          ("manual/BSB", "manual", f"{COMPACT_ED}+manual+clear", True),
          ("manual/BSB-tables", "bsb", f"{COMPACT_ED}+manual+bsb-tables", False)]


# Row -> canonical-row helpers and the checks live in lexeme_aligner.fullalign_build (the chain's builder, step 9a); this
# tool keeps only the engbsb pilot driver over a parquet tree and `--show`.
from lexeme_aligner.fullalign_build import (base_attribution, is_padding, read_rows, to_crows,  # noqa: E402,F401
                                            verify_bsb_semantics, verify_derived)


def show_verse(out: Path, ref: str) -> None:
    """Print one spine verse with every layer side by side, reading ONLY the published-style files: `_index/` lexeme + function-word
    lists, the container files, the verse map and the edition text (a client's view; no spine objects, no decoder)."""
    from lexeme_aligner.usj_source import read_verse_ranges, tokenize
    book = ref.split()[0]
    ch, v = map(int, ref.split()[1].split(":"))
    refs = list(json.loads((INDEX / f"{book}_lexemes.json").read_text(encoding="utf-8")))
    lex = json.loads((INDEX / f"{book}_lexemes.json").read_text(encoding="utf-8"))[ref]
    fnx = json.loads((INDEX / f"{book}_fn.json").read_text(encoding="utf-8"))[ref]
    vm = json.loads((INDEX / "_versification_eng.json").read_text(encoding="utf-8"))["map"]
    tref = vm.get(ref, ref)
    tch, tv = map(int, tref.split()[1].split(":"))
    usj = REPO / "pipeline/work/ingest-cache" / f"usj-{USJ_TAG}" / f"{_BOOK_FILE_NUM[book]}-{book}.json"
    toks = tokenize(read_verse_ranges(usj, rules={})[(tch, tv)]["text"])
    print(f"{ref}  (target verse {tref}, {len(toks)} words)")
    print("   " + " ".join(f"{i}:{w}" for i, w in enumerate(toks)))
    i = refs.index(ref)
    layers = {}
    for lab, _kind, lid, _h in LAYERS:
        d = out / "e" / ISO / lid
        if not d.exists():
            continue
        main, meta = fc.read_book(d, book)
        words = lambda sp: " ".join(toks[x] for x in fc.dec_span(sp) or [] if x < len(toks))      # noqa: E731
        cells: dict[str, list[str]] = collections.defaultdict(list)
        profs = (meta.get("wp") or [""] * len(refs))[i]
        for k, e in enumerate(main[i].split()):
            o, sp = e.split(":")
            cells[f"c{o}"].append(words(sp) + ("" if profs[k] != "." else " (view)"))
        for k, e in enumerate(((meta.get("fn") or [""] * len(refs))[i] or "").split()):
            o, sp = e.split(":")
            cells[f"f{o}"].append(words(sp))
        for entry in ((meta.get("rows") or [""] * len(refs))[i] or "").split():
            f = entry.split(":")
            for slot in ([] if f[0] == "-" else f[0].split(",")):
                if fc.is_marker(f[1]):
                    k2, kind = fc.dec_marker(f[1])
                    cells[slot].append(f"[{'untranslated' if kind == 'u' else 'rendered elsewhere'} after word {k2}]")
                elif any(x[0] == "v" for x in f[3:]):
                    cells[slot].append(f"[joint with: {words(f[1])}]")
                else:
                    extras = [("supplied: " + words(",".join(x[1:].split(",")).replace(" ", "")) ) for x in f[3:] if x[0] == "s"]
                    cells[slot].append((words(f[1]) if f[1] != "~" else "?") + (f"  ({'; '.join(extras)})" if extras else "")
                                       + ("  <row>" if len(f[0].split(",")) > 1 else "  <alt>"))
        layers[lab] = cells
    labs = list(layers)
    w = 34
    print(f"   {'source token':<22}" + "".join(f"{lab:<{w}}" for lab in labs))
    slots = [(f"c{n}", lx) for n, lx in enumerate(lex)] + [(f"f{n}", lx) for n, lx in enumerate(fnx)]
    for slot, lx in slots:
        row = [" | ".join(layers[lab].get(slot, [])) or "-" for lab in labs]
        if any(r != "-" for r in row):
            print(f"   {slot:<4}{lx:<18}" + "".join(f"{r[:w - 2]:<{w}}" for r in row))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parquet-root", type=Path, default=REPO / "pipeline/work/pilot/fa-baseline")
    ap.add_argument("--out", type=Path, default=REPO / "pipeline/work/pilot/fac")
    ap.add_argument("--books", nargs="*", default=None)
    ap.add_argument("--layers", nargs="*", default=None, help="subset of: statistical clear bsb-tables")
    ap.add_argument("--show", default=None, metavar="'BOOK C:V'", help="print that spine verse from the built files and exit")
    a = ap.parse_args(argv)
    if a.show:
        show_verse(a.out, a.show)
        return 0
    heb = HebrewSource()
    usj_dir = REPO / "pipeline/work/ingest-cache" / f"usj-{USJ_TAG}"
    remap = remapper(USJ_TAG, str(usj_dir))
    books = a.books or [b for b in ALL_BOOKS if (INDEX / f"{b}_lexemes.json").exists()]
    if a.out.exists() and not a.books:
        shutil.rmtree(a.out)
    pq_root = a.parquet_root / ISO / EDITION
    enc = {}
    for lab, kind, lid, has_ids in LAYERS:
        short = {"statistical": "statistical", "manual/BSB": "clear", "manual/BSB-tables": "bsb-tables"}[lab]
        if a.layers and short not in a.layers:
            continue
        enc[lab] = (kind, lid, has_ids, None)
    from lexeme_aligner.psalm_titles import title_mode
    tmode = title_mode(USJ_TAG, usj_dir, heb)
    print(f"[pilot] psalm titles in {USJ_TAG}: {tmode}")
    title_tokens = {}
    if tmode["mode"] == "heading":
        for ch in heb.chapters("PSA"):
            for v in heb.verses("PSA", ch):
                tt = {t.idx for t in heb.verse_tokens("PSA", ch, v) if t.is_superscription}
                if tt:
                    title_tokens[f"PSA {ch}:{v}"] = tt
    totals: dict[str, collections.Counter] = {lab: collections.Counter() for lab in enc}
    encoders: dict[str, fc.LayerEncoder] = {}          # ONE profile table per layer (a fresh one per book left books undecodable)
    for book in books:
        usj_path = usj_dir / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        if not usj_path.exists():
            continue
        refs = list(json.loads((INDEX / f"{book}_lexemes.json").read_text(encoding="utf-8")))
        groups = fc.build_groups(heb, book, usj_path, remap)
        digest = book_content_hash(usj_path)[-5:]
        for lab, (kind, lid, has_ids, _) in enc.items():
            rows = read_rows(pq_root / lab / f"{book}.parquet", kind)
            if not rows:
                continue
            t = totals[lab]
            base = base_attribution(rows)
            layer_dir = a.out / ISO[0] / ISO / lid
            layer_dir = a.out / "e" / ISO / lid
            le = encoders.setdefault(lab, fc.LayerEncoder(lid, "statistical" if kind == "statistical" else "manual", base, has_ids))
            dropped: collections.Counter = collections.Counter()
            crows = to_crows(kind, rows, groups, le.base, has_ids, dropped, title_tokens if book == 'PSA' else None)
            t.update(dropped)
            hints = None
            compact_main = compact_meta = None
            if kind == "statistical":
                cm = [f for f in COMPACT.glob(f"{book}_*.json") if f.name.count(".") == 1]
                if not cm:
                    raise FileNotFoundError(f"no compact file for {book} in {COMPACT}")
                compact_main = json.loads(cm[0].read_text(encoding="utf-8"))
                mp = cm[0].with_name(cm[0].stem + ".meta.json")
                compact_meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
                digest_c = cm[0].stem.split("_")[-1]
                hints = {ref: {int(e.split(":")[0]): fc.dec_span(e.split(":")[1]) for e in compact_main[i].split()}
                         for i, ref in enumerate(refs) if compact_main[i]}
            main_arr, meta = le.encode_book(crows, refs, groups, hints)
            dg = digest_c if kind == "statistical" else digest
            if kind == "statistical":
                layer_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy(cm[0], layer_dir / cm[0].name)                  # compact's own main, untouched
                if mp.exists():
                    shutil.copy(mp, layer_dir / mp.name)
                if main_arr != compact_main:
                    t["main differs from compact"] += 1
                    i = next(i for i, (x, y) in enumerate(zip(main_arr, compact_main)) if x != y)
                    print(f"   [diag] {book} {refs[i]}: encoder main {main_arr[i]!r} vs compact {compact_main[i]!r}")
                fc.write_book(layer_dir, book, dg, compact_main, meta, merge=True)
            else:
                fc.write_book(layer_dir, book, dg, main_arr, meta)
            fc.write_layer_json(layer_dir, le)
            # ---- decode from the FILES and compare
            main_back, meta_back = fc.read_book(layer_dir, book)
            if kind == "statistical":
                for k, v in (compact_meta or {}).items():
                    if meta_back.get(k) != v:
                        t["compact channel changed"] += 1
                if main_back != compact_main:
                    t["main differs from compact"] += 1
            dec = fc.LayerDecoder(fc.read_layer_json(layer_dir)).decode_book(main_back, meta_back, refs, groups)
            a_ = collections.Counter(c.canon() for cl in crows.values() for c in cl)
            b_ = collections.Counter(c.canon() for c in dec)
            t["rows"] += sum(a_.values())
            t["rows lost"] += sum((a_ - b_).values())
            if a_ - b_:
                print(f"   [diag] {lab} {book}: {sum((a_ - b_).values())} rows lost, e.g. {next(iter(a_ - b_))[:260]}")
            t["rows invented"] += sum((b_ - a_).values())
            bad = verify_derived(kind, rows, groups)
            for k, v in bad.items():
                t[f"derived mismatch: {k}"] += v
            if kind == "bsb":
                t["rows (excluding padding)"] += sum(1 for r in rows if not is_padding(r))
                for k, v in verify_bsb_semantics(rows, dec, groups).items():
                    if k != "rows":
                        t[k] += v
                t["supplied words"] += sum(len(c.sup) for c in dec)
                t["inflection words"] += sum(len(c.inf) for c in dec)
                t["joint rows"] += sum(c.joint for c in dec)
                t["marker rows"] += sum(1 for c in dec if c.mark)
            nonlocal_parquet = (pq_root / lab / f"{book}.parquet").stat().st_size
            t["parquet bytes"] += nonlocal_parquet
            files = list(layer_dir.glob(f"{book}_*.json"))
            t["container bytes"] += sum(f.stat().st_size for f in files)
            if kind == "statistical":                                       # only what the full-align adds on top of compact
                t["compact bytes (already published)"] += sum(f.stat().st_size for f in COMPACT.glob(f"{book}_*.json")
                                                              if ".extra." not in f.name)
    print(f"books: {len(books)}  out: {a.out}")
    ok = True
    for lab, t in totals.items():
        print(f"\n== {lab}")
        for k, v in t.items():
            print(f"   {k}: {v:,}")
        if t["rows lost"] or t["rows invented"] or any(k.startswith("derived mismatch") or "mismatch" in k for k in t):
            ok = False
    print("\nROUND TRIP:", "OK (0 lost, 0 invented, 0 mismatches)" if ok else "PROBLEMS FOUND — see counts above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
