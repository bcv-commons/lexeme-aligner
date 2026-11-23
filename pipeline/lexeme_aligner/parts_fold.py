"""Word view of a parts-spine run (part-alignments, source_parts.py).

A run on the parts spine writes align files whose `h_idx` are positions in the PARTS corpus: a word's carrying part (key = the word
key) and its other parts (key "<word key>.<n>": an ending, prefix, augment, preverb, reduplication). Every word-level output (compact
alignments, full-alignment layers, lexeme-alignments) is built on the WORD spine. This module writes word-view copies of those files:

- a word unit takes its carrying part's pair, re-indexed to the word corpus (`h_idx`, `surface` = the whole word);
- a non-carrying part's links are added to its word's span, minus target positions another word of the verse claims (the
  claimed-target rule, as run_pilot.part_blocked); a word with no link of its own takes its first linked part's pair;
- `held` is folded the same way; `llm_skipped` keeps the words' entries (re-indexed) and drops the parts' (a part with no rendering is
  part-level information: it belongs in part-alignments, not in the word view); every other record field is copied.

Then the word-level builders run unchanged with ALIGNER_SPINE_DB = the word spine on the folded folder.

`rule` (FOLD_RULE, default "stem"; owner 2026-11-23): "stem" keeps only the carrying part's pair (a word whose carrying part has no
link gets no pair; its parts' links stay in part-alignments). This word view feeds the LEXICAL outputs (lexeme-alignments, senses,
aligned_mwe, gloss and pooled priors, the NT bridge), where an ending's "de" / "je" is not a rendering of the lexeme: with "fold"
fra_lsg's NT eflomal surfaces went from 3.1% to 26.7% multi-word (θεός -> "dieu a", λέγω -> "je"). "fold" = the rules above.

    python -m lexeme_aligner.parts_fold --usj DIR --in OUT --out OUT/words --books MRK,RUT --iso TAG [--parts-spine DB] [--word-spine DB]
                                        [--rule stem|fold]
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import re
import sys
from pathlib import Path


def _open(fp: Path):
    return gzip.open(fp, "rt", encoding="utf-8") if fp.name.endswith(".gz") else fp.open(encoding="utf-8")


FOLD_RULE = "stem"
RULES = ("stem", "fold")


def fold_record(rec: dict, parts_toks: list, word_toks: list, rule: str = FOLD_RULE) -> dict:
    """One align record of the parts corpus -> the same record on the word corpus (see the module docstring)."""
    pkey = {t.idx: t.keys[0] for t in parts_toks if t.keys}
    widx = {tuple(t.keys): t for t in word_toks if t.keys}
    wfirst = {t.keys[0]: t for t in word_toks if t.keys}

    def word_of(h):                       # parts h_idx -> word token (or None)
        k = pkey.get(h)
        if k is None:
            return None
        if "." in k:
            return wfirst.get(k.split(".")[0])
        ptok = next((t for t in parts_toks if t.idx == h), None)
        return widx.get(tuple(ptok.keys)) if ptok is not None else wfirst.get(k)

    out = {k: v for k, v in rec.items() if k not in ("pairs", "held", "llm_skipped")}
    for field in ("pairs", "held"):
        if field not in rec:
            continue
        words, subs = {}, []
        for p in rec[field]:
            w = word_of(p["h_idx"])
            if w is None:
                continue
            if "." in pkey[p["h_idx"]]:
                if rule == "fold":
                    subs.append((w, p))
            elif w.idx not in words:
                words[w.idx] = dict(p, h_idx=w.idx, surface=w.surface)
        owner: dict[int, set[int]] = collections.defaultdict(set)
        for wi, p in words.items():
            for j in p.get("t_idx") or ():
                owner[j].add(wi)
        for w, p in subs:
            pos = [j for j in p.get("t_idx") or () if not (owner.get(j, set()) - {w.idx})]
            if not pos:
                continue
            sub_tp = [tp for tp in p.get("t_parts") or () if tp[0] in pos]      # target parts (a target-parts edition) on those tokens
            if w.idx in words:
                q = words[w.idx]
                if not set(pos) <= set(q.get("t_idx") or ()):
                    t_idx = sorted(set(q.get("t_idx") or ()) | set(pos))
                    words[w.idx] = dict(q, t_idx=t_idx)
                if sub_tp:
                    have = [tuple(tp) for tp in words[w.idx].get("t_parts") or ()]
                    words[w.idx] = dict(words[w.idx], t_parts=[list(tp) for tp in sorted(set(have) | {tuple(tp) for tp in sub_tp})])
            else:
                words[w.idx] = dict(p, h_idx=w.idx, surface=w.surface, lexeme=w.lexeme, strong=w.strong, lemma=w.lemma,
                                    content=bool(w.is_content), t_idx=sorted(pos))
                if "t_parts" in p:
                    words[w.idx]["t_parts"] = sub_tp
        out[field] = [words[i] for i in sorted(words)]
    if "llm_skipped" in rec:
        sk = []
        for s in rec["llm_skipped"]:
            h = s.get("h_idx")
            if h is None:
                sk.append(s)
                continue
            w = word_of(h)
            if w is not None and "." not in pkey[h]:
                sk.append(dict(s, h_idx=w.idx))
        out["llm_skipped"] = sk
    return out


def target_text(rec: dict, toks: list[str]) -> None:
    for field in ("pairs", "held"):
        for p in rec.get(field, ()):
            if p.get("t_idx") is not None:
                p["target"] = " ".join(toks[j] for j in p["t_idx"] if j < len(toks))


def fold_dir(usj: Path, src: Path, dst: Path, books: list[str], parts_spine: Path, word_spine: Path, tag: str,
             only_tag: str | None = None, rule: str = FOLD_RULE) -> dict:
    """Every align_*.jsonl(.gz) of `books` in `src` -> word view in `dst` (plain .jsonl). Returns counts.
    `only_tag`: fold only that edition's files (a shared out dir, as full_chain's, holds every edition's); a `.jsonl.gz` of the same
    name left in `dst` by an earlier run's --clean-out is removed first, so readers never see both."""
    from lexeme_aligner.align_files import tag_files_any_method, unlink_stale_gz
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    remap = remapper(tag, str(usj))                     # the run's own verse mapping (run_pilot: remapper(iso, usj_dir))
    dst.mkdir(parents=True, exist_ok=True)
    n = collections.Counter()
    if only_tag is not None:
        # a method's files for this edition in dst are replaced as a whole: a book this run wrote nothing for (gapfill with no fills)
        # must not keep an earlier run's file. Methods the parts run did not produce (an LLM cell, spanext when off) are left alone.
        rx = re.compile(rf"^align_([a-z]+)_{re.escape(only_tag)}_")
        folded = {m.group(1) for f in tag_files_any_method(src, only_tag) if (m := rx.match(f.name))}
        for f in tag_files_any_method(dst, only_tag):
            if (m := rx.match(f.name)) and m.group(1) in folded and f.name.split(".jsonl")[0].rsplit("_", 1)[-1] in books:
                f.unlink()
                n["old files removed"] += 1
    for book in books:
        if only_tag is not None:
            files = [f for f in tag_files_any_method(src, only_tag) if f.name.split(".jsonl")[0].endswith(f"_{book}")]
        else:
            files = sorted(f for f in src.glob("align_*.jsonl*") if f.name.split(".jsonl")[0].endswith(f"_{book}"))
        if not files:
            continue
        recs_p = build_corpus([book], usj, HebrewSource(spine_db=parts_spine), remap=remap)
        recs_w = build_corpus([book], usj, HebrewSource(spine_db=word_spine), remap=remap)
        P = {encode(r.book, r.ch, r.v): r for r in recs_p}
        Wd = {encode(r.book, r.ch, r.v): r for r in recs_w}
        for fp in files:
            name = fp.name[:-3] if fp.name.endswith(".gz") else fp.name
            unlink_stale_gz(dst / name)
            with _open(fp) as fh, (dst / name).open("w", encoding="utf-8") as oh:
                for line in fh:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    p, w = P.get(rec["ref"]), Wd.get(rec["ref"])
                    if p is None or w is None:
                        oh.write(line if line.endswith("\n") else line + "\n")
                        n["records copied"] += 1
                        continue
                    f = fold_record(rec, p.heb, w.heb, rule)
                    target_text(f, w.toks)
                    oh.write(json.dumps(f, ensure_ascii=False) + "\n")
                    n["records folded"] += 1
            n["files"] += 1
    return dict(n)


def main(argv=None) -> int:
    from lexeme_aligner.config import SPINE_DB, WORD_SPINE_DB
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj", type=Path, required=True)
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--books", required=True, help="comma-separated, or ALL")
    ap.add_argument("--iso", required=True, help="the run's edition tag (its versification, as run_pilot --iso)")
    ap.add_argument("--parts-spine", type=Path, default=SPINE_DB)
    ap.add_argument("--word-spine", type=Path, default=WORD_SPINE_DB)
    ap.add_argument("--tag", default=None, help="fold only this edition's files (default: every align file in --in)")
    ap.add_argument("--rule", choices=RULES, default=FOLD_RULE, help="stem: the carrying part only (default); fold: + unclaimed part links")
    a = ap.parse_args(argv)
    from lexeme_aligner.compact_align import ALL_BOOKS
    books = ALL_BOOKS if a.books == "ALL" else a.books.split(",")
    print(json.dumps(fold_dir(a.usj, a.src, a.out, books, a.parts_spine, a.word_spine, a.iso, a.tag, a.rule)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
