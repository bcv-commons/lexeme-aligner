"""Derive function-word defaults for Jev from a manual gold alignment (2026-10-26).

Two profiles per testament, measured on the gold of books OTHER than the ones you test on:
  target: each frequent target FUNCTION word (the language's stopword list) -> what it renders: its own source function word (which
          lemmas), part of a content word's span (which part of speech), or nothing;
  source: each source FUNCTION lemma (ὁ, καί, ו, ה, ב, suffix הוּא ...) -> which target word renders it, or none.
OT gold is Clear's word-level gold split into MACULA morphemes (`gold_split`), so Hebrew prefixes and suffixes get their own figures.
Output: a JSON profile (`--json`, read by `jev_check --hints`) and a readable conventions page (`--out`).

    .venv/bin/python -m lexeme_aligner.eval.derive_conventions --iso bsb --publish-iso eng --books 1SA,PSA,ISA,MAT,JHN,ROM,HEB \\
        --json pipeline/work/kjv/conv_test/eng_hints.json --out pipeline/work/kjv/conv_test/eng_derived.md
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from lexeme_aligner.config import PRIOR_PACK
from lexeme_aligner.eval.contest_rule import gold_base_text
from lexeme_aligner.eval.pos_score import load_gold, load_spine
from lexeme_aligner.refs import encode
from lexeme_aligner.run_pilot import NT_BOOKS
from lexeme_aligner.versification import remapper

GOLD_SOURCES = {"clear": ("Clear-Bible/Alignments (manual)", "CC-BY-4.0"), "helfi": ("HELFI (University of Helsinki)", "CC-BY-4.0"),
                "door43": ("unfoldingWord Door43 aligned GL texts", "CC-BY-SA-4.0"), "sword": ("SWORD ChiUns", "Public Domain")}

HEADER = ("Target positions are the letter runs of the verse in reading order. Punctuation is dropped. Position numbers refer to that "
          "tokenization.\n\nHow this translation's function words are aligned, MEASURED on a manual gold alignment of the same "
          "language ({src}; books {books}). Each line: the word, what it renders and how often. Use these as defaults; the verse in "
          "front of you decides.\n")


def _owners(gv, r, spine_keys: dict, exact: bool) -> dict[int, list]:
    """target position -> source tokens the gold links it to."""
    by_idx = {t.idx: t for t in r.heb}
    if exact:                                                # split gold: keys are the spine's own
        tok_of = {key: by_idx[h] for h, key in spine_keys.items() if h in by_idx}
    else:                                                    # Clear NT: resolve through the gold's source id
        by_id = {k: t for t in r.heb for k in (t.keys or [])}
        tok_of = {}
        for key in gv.links:
            sid = gv.raw.get(key, (None,))[0]
            t = by_id.get(sid[1:] if sid and sid[0] in "on" else sid)
            if t is not None:
                tok_of[key] = t
    own: dict[int, list] = collections.defaultdict(list)
    for key, ts in gv.links.items():
        if key in tok_of:
            for j in ts:
                own[j].append(tok_of[key])
    return own


def profile(iso: str, publish_iso: str, books: list[str]) -> dict:
    from lexeme_aligner.eval.gold_split import split_gold
    from lexeme_aligner.gapfill import load_priors
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.target_stopwords import StopwordFilter
    usj = Path(f"pipeline/work/ingest-cache/usj-{iso}")
    rm = remapper(iso, str(usj))
    base = gold_base_text(publish_iso)
    stop = StopwordFilter(publish_iso, str(usj))
    lex_pos, _ = load_priors(PRIOR_PACK)
    tgt: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    tgt_lem: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    src: dict = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    for book in books:
        testament = "NT" if book in NT_BOOKS else "OT"
        gold, _ = load_gold(publish_iso, usj, [book], base, gold_methods=("manual",), remap=rm)
        spine = load_spine([book], usj, iso)
        recs = build_corpus([book], usj, HebrewSource(), remap=rm)
        if testament == "OT":
            gold = split_gold(gold, recs, spine)
        for r in recs:
            ref = encode(r.book, r.ch, r.v)
            gv = gold.get(ref)
            if gv is None:
                continue
            own = _owners(gv, r, spine.key_of.get(ref, {}), testament == "OT")
            for j, w in enumerate(r.toks):                       # target side
                if not stop.is_function(w):
                    continue
                k = w.lower()
                o = own.get(j, [])
                if not o:
                    tgt[testament][k]["nothing"] += 1
                elif o[0].is_content:
                    tgt[testament][k][f"part of a {lex_pos.get(o[0].lexeme) or 'content word'}'s span"] += 1
                else:
                    tgt[testament][k]["its own source word"] += 1
                    tgt_lem[testament][k][o[0].lemma] += 1
            linked_by: dict[int, list[int]] = collections.defaultdict(list)
            for j, ts in own.items():
                for t in ts:
                    linked_by[t.idx].append(j)
            for t in r.heb:                                       # source side: Clear lists linked words only, so unlinked = none
                if t.is_content:
                    continue
                js = linked_by.get(t.idx, [])
                key = f"{t.lemma} (suffix)" if testament == "OT" and t.lemma == "הוּא" else t.lemma
                src[testament][key][" ".join(r.toks[j].lower() for j in sorted(js)) if js else "none"] += 1
    import datetime, hashlib, json as _json
    gold_kind = _json.loads(Path("config/gold_langs.json").read_text(encoding="utf-8")).get(publish_iso, {}).get("gold")
    src_name, lic = GOLD_SOURCES.get(gold_kind, (gold_kind, None))
    here = Path(__file__).resolve().parent
    scripts = {f: hashlib.sha256((here / f).read_bytes()).hexdigest()[:16] for f in ("derive_conventions.py", "gold_split.py")}
    return {"base_text": base, "books": books, "gold_source": f"{src_name}, base text {base}", "gold_license": lic,
            "method": ("NT: the gold's links as given; OT: the gold's word-level links split into MACULA morphemes by "
                       "lexeme_aligner.eval.gold_split (each link records how it was split)"),
            "script": {"modules": "lexeme_aligner.eval.derive_conventions, lexeme_aligner.eval.gold_split", "sha256_16": scripts},
            "date": datetime.date.today().isoformat(),
            "target": {T: {w: {"n": sum(c.values()), "what": dict(c), "lemmas": dict(tgt_lem[T][w].most_common(3))}
                           for w, c in d.items()} for T, d in tgt.items()},
            "source": {T: {l: {"n": sum(c.values()), "renderings": dict(c.most_common(6))} for l, c in d.items()}
                       for T, d in src.items()}}


def target_hint(entry: dict) -> str:
    n = entry["n"]
    parts = []
    for what, c in sorted(entry["what"].items(), key=lambda x: -x[1])[:3]:
        share = round(100 * c / n)
        if share < 5:
            continue
        if what == "its own source word" and entry["lemmas"]:
            what += " (" + ", ".join(entry["lemmas"]) + ")"
        parts.append(f"{what} {share}%")
    return "; ".join(parts)


def source_hint(entry: dict) -> str:
    n = entry["n"]
    return "; ".join(f"{'no word of its own' if w == 'none' else repr(w)} {round(100 * c / n)}%"
                     for w, c in entry["renderings"].items() if c / n >= 0.05)


def page(prof: dict, min_n: int = 40, top: int = 45) -> str:
    lines = []
    for T, label in (("NT", "New Testament (Greek source)"), ("OT", "Old Testament (Hebrew source)")):
        d = prof["target"].get(T, {})
        keys = sorted((w for w in d if d[w]["n"] >= min_n), key=lambda w: -d[w]["n"])[:top]
        if keys:
            lines.append(f"\n{label}, translation function words:")
            lines += [f"- `{w}`: {target_hint(d[w])}" for w in keys]
        s = prof["source"].get(T, {})
        keys = sorted((l for l in s if s[l]["n"] >= min_n), key=lambda l: -s[l]["n"])[:top]
        if keys:
            lines.append(f"\n{label}, source function words:")
            lines += [f"- {l}: {source_hint(s[l])}" for l in keys]
    return HEADER.format(src=prof["base_text"], books=", ".join(prof["books"])) + "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, help="edition tag whose text the gold is mapped onto")
    ap.add_argument("--publish-iso", required=True)
    ap.add_argument("--books", required=True)
    ap.add_argument("--json", type=Path, help="profile for jev_check --hints")
    ap.add_argument("--out", type=Path, help="readable conventions page")
    a = ap.parse_args()
    prof = profile(a.iso, a.publish_iso, a.books.split(","))
    for path, text in ((a.json, json.dumps(prof, ensure_ascii=False, indent=1)), (a.out, page(prof))):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(page(prof))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
