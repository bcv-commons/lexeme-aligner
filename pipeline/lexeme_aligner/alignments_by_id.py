"""Word alignments keyed by MACULA Greek word ids -> `bcv-commons/alignments-by-id` (folder `n1904/`, the Nestle 1904 spine).

WHY (2026-10-23). bcv-query checks its NT clause layout (built on the MACULA Greek trees) against Salkinson-Ginsburg (heb_scg).
They need each Greek word's rendering by tree id (`n40001001001`), down to the word part (Hebrew writes ו ה ב ל מ כ ש and
pronoun suffixes inside the word), and an honest "not rendered". compact-alignments cannot carry this: it indexes content
tokens only, by ordinal.

SOURCES (all local; nothing is aligned here)
  chain   the edition's final chain links (`compact_align._merged_pairs`, contest rule), part-aware for an edition pinned in
          config/target_parts.json (`t_parts`, see target_parts.py)
  jev     the Jev v4 cell `align_llm_jevA.<tag>.<BOOK>.v4.jev-0` (eval/jev_check.py): every Greek word's final link, or
          `unrepresented`
Every Greek word gets ONE outcome, its class:
  kept    Jev kept the chain's link (asked or not); the chain's parts are carried over
  edited  Jev changed the link; a token keeps the chain's part when the chain linked that token, else it is whole
  edited-wide  the same, on 2+ target words: Jev's weak spot (a neighbouring word of another source word swept in)
  inside  Jev: a function word rendered inside a word another source word owns; the part is the chain's part on that token,
          else the one part of that token whose class fits the Greek word, else the whole token
  part    Jev said "not rendered" but the chain linked the word to a prefix/suffix part whose class fits it (Greek conj ->
          cj/rel, prep -> prep, det -> art/rel, pron -> pron/rel). Measured 2026-10-23 on 130 such cases (PHM, MRK 1-3, hand
          graded): keeping these links rescues 47 right links and lets 14 wrong ones through (77% right) against taking
          Jev's "none"; showing Jev the parts instead (a second paid pass) rescued 11 (pipeline/work/jev-parts-explore).
  else    the word goes to the verse's `none` list.
A verse's leading verse number (Salkinson writes it in Hebrew letters) is never linked and never `added`.

LAYOUT  publish/alignments-by-id/n1904/<iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json
  {"C:V": {"links": [[[positions], [ids], class], ...], "none": [ids], "added": [positions]}, ...}
  C:V = the edition's verse; positions = tokenizer v2 tokens of that verse; a part is the string "token.part" (part numbers of
  the pinned analysis), a whole token the integer; <hash> = the book's text fingerprint (as compact-alignments).

    python -m lexeme_aligner.alignments_by_id --edition heb_scg --iso heb
    python -m lexeme_aligner.alignments_by_id --publish [--push]        # dry run unless --push
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from lexeme_aligner import target_parts as tp
from lexeme_aligner import text_fingerprint as tf
from lexeme_aligner.config import OUT as ALIGN_OUT, PRIOR_PACK, SPINE_DB
from lexeme_aligner.refs import BOOK_NUMBERS
from lexeme_aligner.tr_align import books_of, numeral, usj_dir_of
from lexeme_aligner.usj_source import TOKENIZER_VERSION, tokenize

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "publish/alignments-by-id"
SPINE_FOLDER = "n1904"
HF_REPO = "bcv-commons/alignments-by-id"
FORMAT_VERSION = 1
CHAIN_METHODS = ("spanext", "eflomal", "gloss", "gapfill")       # full_chain._METHODS: what the compact files resolve
JEV_CELL = "jevA.{tag}.{book}.v4.jev-0"
COMPAT = {"conj": {"cj", "rel"}, "prep": {"prep"}, "det": {"art", "rel"}, "pron": {"pron", "rel"}}
CLASSES = ("kept", "edited", "edited-wide", "inside", "part")
# Hand checks (random samples, graded against the Greek and the edition text; right = the linked words/parts render the Greek
# word and nothing else). "none" = Greek words listed as not rendered that really have no rendering.
HAND_CHECK = {"heb_scg": dict(date="2026-10-23", kept="25/25", edited="25/39", **{"edited-wide": "17/36"}, inside="21/25",
                              part="21/25 (and 47/61 in the 130-case comparison with Jev)", none="19/25")}


def spine_pin(spine: Path = SPINE_DB) -> dict:
    meta = dict(sqlite3.connect(spine).execute("SELECT key, value FROM spine_meta").fetchall())
    return dict(file=spine.name, source_greek=meta.get("source_greek"), versification_nt=meta.get("versification_nt"),
                spine_content_sha256=meta.get("spine_content_sha256"), macula_spine_sha256=meta.get("macula_spine_sha256"))


def _pos(i: int, part) -> int | str:
    return i if part is None else f"{i}.{part}"


def verse_links(heb, toks, chain: dict, jev: dict | None, parts: dict, gpos) -> tuple[list, list, list]:
    """One verse -> (links, none, added). `heb` the Greek tokens, `toks` the edition verse's tokens, `chain` {h: {t_idx, t_parts}},
    `jev` the Jev record ({pairs, llm_skipped}) or None, `parts` {token: {part: (text, class)}}, gpos(lexeme) -> prior-pack pos."""
    skip = {0} if toks and toks[0] in {numeral(n) for n in range(1, 177)} else set()
    jp = {p["h_idx"]: p for p in (jev or {}).get("pairs", [])}
    links, none = [], []
    for h, t in enumerate(heb):
        ids = [f"n{k}" for k in t.keys]
        c = chain.get(h)
        cparts = {i: pt for i, pt in (c.get("t_parts") or [])} if c else {}
        j = jp.get(h)
        fits = COMPAT.get(gpos(t.lexeme), set())
        pos, cls = [], None
        if j is not None:
            prior = j.get("prior", "")
            for i in j.get("t_idx") or []:
                if i in skip:
                    continue
                if prior == "llm_edit_inside":
                    pt = cparts.get(i)
                    if pt is None:
                        cand = [p for p, (_, cl) in parts.get(i, {}).items() if cl in fits]
                        pt = cand[0] if len(cand) == 1 else None
                    pos.append(_pos(i, pt))
                else:
                    pos.append(_pos(i, cparts.get(i)))
            cls = {"llm_edit_kept": "kept", "llm_edit_edited": "edited", "llm_edit_inside": "inside"}.get(prior, "edited")
        elif jev is not None and c and not t.is_content and len(c["t_idx"]) == 1 and c["t_idx"][0] not in skip:
            i = c["t_idx"][0]
            pt = cparts.get(i)
            if pt is not None and parts.get(i, {}).get(pt, ("", ""))[1] in fits:
                pos, cls = [_pos(i, pt)], "part"
        elif jev is None and c:                                   # no Jev record for the verse: the chain link as it is
            pos, cls = [_pos(i, cparts.get(i)) for i in c["t_idx"] if i not in skip], "kept"
        if cls == "edited" and len({int(str(x).split(".")[0]) for x in pos}) >= 2:
            cls = "edited-wide"
        if pos:
            links.append([sorted(set(pos), key=lambda x: (int(str(x).split(".")[0]), str(x))), ids, cls])
        else:
            none.extend(ids)
    used = {int(str(x).split(".")[0]) for l in links for x in l[0]}
    added = [i for i in range(len(toks)) if i not in used and i not in skip]
    return links, none, added


def build(edition: str, iso: str, out: Path = OUT, align_out: Path = ALIGN_OUT) -> dict:
    import pyarrow.parquet as pq
    from lexeme_aligner.compact_align import _merged_pairs, load_contest_rule
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper

    usj = usj_dir_of(edition)
    books = {b: p for b, p in books_of(usj).items() if BOOK_NUMBERS.get(b, 0) >= 40}
    pinned = tp.for_edition(edition)
    allparts: dict = collections.defaultdict(dict)
    if pinned:
        for r in pq.read_table(pinned[0], columns=["book", "chapter", "verse", "i", "part", "text", "class"]).to_pylist():
            if tp._plain(r["text"]):
                allparts[(r["book"], r["chapter"], r["verse"], r["i"])][r["part"]] = (r["text"], r["class"])
    pos_of = {r["lexeme"]: r["pos"] for r in pq.read_table(PRIOR_PACK, columns=["lexeme", "pos"]).to_pylist()}
    contest, heb, remap = load_contest_rule(), HebrewSource(), remapper(edition, str(usj))
    edir = out / SPINE_FOLDER / iso[0] / iso / edition
    edir.mkdir(parents=True, exist_ok=True)
    for old in edir.glob("*.json"):
        old.unlink()
    stats, missing = collections.Counter(), []
    for book, path in sorted(books.items()):
        jf = align_out / f"align_llm_{JEV_CELL.format(tag=edition, book=book)}_{book}.jsonl"
        if not jf.exists():
            raise SystemExit(f"[by-id] {edition} {book}: no Jev cell {jf.name}")
        jev = {(r["chapter"], r["verse"]): r for r in map(json.loads, jf.open(encoding="utf-8"))}
        chain = _merged_pairs(edition, book, align_out, CHAIN_METHODS, contest)
        raw = tf.usj_verses(path)
        doc = {}
        for rec in build_corpus([book], usj, heb, remap):
            if rec.tref not in raw:                               # a spine verse the edition does not have (MRK 16:99, the
                missing.append(f"{book} {rec.ch}:{rec.v}")         # Shorter Ending): no edition verse to key it on
                stats["ids without an edition verse"] += sum(len(t.keys) for t in rec.heb)
                continue
            toks = tokenize(rec.text)
            if tokenize(raw.get(rec.tref, "")) != toks:
                raise SystemExit(f"[by-id] {edition} {book} {rec.tref}: aligned text differs from the published text")
            parts = {i: allparts.get((book,) + rec.tref + (i,), {}) for i in range(len(toks))}
            links, none, added = verse_links(rec.heb, toks, chain.get((rec.ch, rec.v), {}), jev.get((rec.ch, rec.v)), parts,
                                             pos_of.get)
            key = f"{rec.tref[0]}:{rec.tref[1]}"
            if key in doc:                                        # two spine verses on one edition verse: merge
                d = doc[key]
                used = {int(str(x).split(".")[0]) for l in d["links"] + links for x in l[0]}
                doc[key] = dict(links=d["links"] + links, none=d["none"] + none, added=[i for i in d["added"] if i not in used])
            else:
                doc[key] = dict(links=links, none=none, added=added)
            for l in links:
                stats[l[2]] += 1
                stats["ids linked"] += len(l[1])
                stats["part positions"] += sum(isinstance(x, str) for x in l[0])
                stats["ids on 2+ tokens"] += len(l[1]) * (len({int(str(x).split(".")[0]) for x in l[0]}) >= 2)
            stats["ids none"] += len(none)
            stats["tokens added"] += len(added)
        (edir / f"{book}_{tf.book_file_hash(path)}.json").write_text(
            json.dumps(dict(sorted(doc.items(), key=lambda kv: tuple(int(x) for x in kv[0].split(":")))), ensure_ascii=False,
                       separators=(",", ":")) + "\n", encoding="utf-8")
    entry = dict(books=sorted(books), classes={c: stats[c] for c in CLASSES}, ids_linked=stats["ids linked"],
                 ids_none=stats["ids none"], ids_on_2plus_tokens=stats["ids on 2+ tokens"], part_positions=stats["part positions"],
                 tokens_added=stats["tokens added"], spine_verses_not_in_edition=missing,
                 ids_without_edition_verse=stats["ids without an edition verse"], jev=dict(design="v4", theta=0.0, provider="typesafe"), built=tf.today())
    if edition in HAND_CHECK:
        entry["hand_check"] = HAND_CHECK[edition]
    if pinned:
        a, e = pinned
        entry["parts"] = dict(dataset=e["dataset"], revision=e["revision"], file=e["file"],
                              sha256=hashlib.sha256(Path(a).read_bytes()).hexdigest(), min_p_split=e.get("min_p_split", 0.9))
    man_p = out / SPINE_FOLDER / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else {"languages": {}}
    man.update(format_version=FORMAT_VERSION, tokenizer_version=TOKENIZER_VERSION, text_fp_version=tf.TEXT_FP_VERSION,
               spine=spine_pin(), ids="MACULA Greek (Nestle 1904) tree word ids: 'n' + BBCCCVVVWWW, e.g. n40001001001")
    man["languages"].setdefault(iso, {"editions": {}})["editions"][edition] = entry
    man_p.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[by-id] {edition}: {json.dumps(entry, ensure_ascii=False)}", file=sys.stderr)
    return entry


def publish(push: bool, out: Path = OUT) -> None:
    from lexeme_aligner.hf_bulk_publish import install_timeout, publish_chunked
    install_timeout()
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and not p.name.startswith("."))
    publish_chunked(out, HF_REPO, files, create=True, dry_run=not push, label="alignments-by-id", detect_deletions=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition")
    ap.add_argument("--iso")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    if a.publish:
        publish(a.push)
    elif a.edition and a.iso:
        build(a.edition, a.iso)
    else:
        ap.error("--edition and --iso, or --publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
