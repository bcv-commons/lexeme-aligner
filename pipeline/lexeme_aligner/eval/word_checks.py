"""Gold-free consistency checks of one edition's alignment, per source WORD (plan internal-docs/grammar-word-level-plan-2026-10-08.md, Phase B).

Each check reads facts we already have (the spine's per-morpheme tokens and their MACULA keys, the language's gram-struct facts) and asks whether
the alignment is consistent with them. Nothing here changes an alignment; a check only FLAGS tokens. A check earns a consumer (a veto in
span extension or gap-fill) only after its flags are shown, on gold, to pick out wrong alignments far more often than chance (`calibrate`).

  C1 article_word    Greek article (grc:3588) and the next content token: in a language whose article is bound to the noun (article_bound), both
                     should land on the same target word. Flag: they land on different words.
  C2 suffix_pronoun  Hebrew pronominal suffix (a non-content morpheme with `person`, after the content morpheme of the same word): how far is its
                     target from its stem's target? Flag: more than one word away (a suffix is rendered next to its noun or inside it).
  C3 prefix_prep     Hebrew prefixed preposition (ב ל כ, first morpheme of a word, followed by a content morpheme): same target word as the stem
                     (fused), before, or after it. Flag: on the opposite side to the language's adposition direction.
  C4 construct_gap   construct chain (construct_group, content members): the members' target words should form one block. Flag: a gap of more
                     than one non-function word inside the chain's target span.
  C5 superscription  a psalm-title token (is_superscription) aligned at all (the spine treats titles as non-content; any link is suspect).

Positions are the alignment files' own target indices (the same coordinates pos_score scores in). Methods are read in the order given; the first
method that aligns a token wins it (pos_score.union's convention).

    .venv/bin/python -m lexeme_aligner.eval.word_checks --iso hin --tag hinirv --usj-dir pipeline/work/ingest-cache/usj-hinirv --ot --calibrate
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT
from lexeme_aligner.eval.word_bridge import words_of

GREEK_ARTICLE = "grc:3588"
PREFIX_PREPOSITIONS = {"hbo:0871a", "hbo:3807a", "hbo:3509a"}          # ב ל כ (MACULA lexemes; מן is usually a separate word)
CHECKS = ("article_word", "suffix_pronoun", "prefix_prep", "construct_gap", "superscription")
QA_DIR = Path("pipeline/work/qa/word_checks")


def load_alignment(tag: str, methods: tuple[str, ...], out_dir: Path, books: set[int] | None = None) -> dict[int, dict[int, list[int]]]:
    """{ref: {h_idx: sorted target positions}}, first method wins a token."""
    out: dict[int, dict[int, list[int]]] = collections.defaultdict(dict)
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if books and rec["ref"] // 1_000_000 not in books:
                        continue
                    verse = out[rec["ref"]]
                    for p in rec["pairs"]:
                        if p.get("t_idx") and p["h_idx"] not in verse:
                            verse[p["h_idx"]] = sorted(p["t_idx"])
    return out


def _morph_no(t) -> int:
    k = (t.keys or [""])[0]
    return int(k[-1]) if len(k) == 12 and k[-1].isdigit() else 0


def _dist(a: list[int], b: list[int]) -> int:
    return min(abs(x - y) for x in a for y in b)


def verse_checks(heb: list, aligned: dict[int, list[int]], facts: dict, is_function) -> list[dict]:
    """Every judged item of one verse: {check, idx (the token the item is about), word (word id), flagged, detail}."""
    items: list[dict] = []
    by_idx = {t.idx: t for t in heb}
    words = words_of(heb)
    word_of = {i: w.wid for w in words for i in w.idx}
    toks = sorted(heb, key=lambda t: t.idx)

    # C1 — Greek article vs the next content token (bound-article languages only)
    if facts.get("article_bound"):
        for k, t in enumerate(toks):
            if t.lexeme != GREEK_ARTICLE or t.idx not in aligned:
                continue
            nxt = next((u for u in toks[k + 1:k + 4] if u.is_content and u.strong), None)
            if nxt is None or nxt.idx not in aligned:
                continue
            flagged = not set(aligned[t.idx]) & set(aligned[nxt.idx])
            items.append({"check": "article_word", "idx": t.idx, "word": word_of.get(nxt.idx), "flagged": flagged,
                          "detail": _dist(aligned[t.idx], aligned[nxt.idx])})

    for w in words:
        members = [by_idx[i] for i in w.idx]
        stems = [m for m in members if m.is_content and m.strong and m.idx in aligned]
        if not stems:
            continue
        stem = stems[0]
        for m in members:
            if m.is_content or m.idx not in aligned:
                continue
            # C2 — pronominal suffix after the stem inside the same word
            if getattr(m, "person", None) and _morph_no(m) > _morph_no(stem):
                d = _dist(aligned[m.idx], aligned[stem.idx])
                items.append({"check": "suffix_pronoun", "idx": m.idx, "word": w.wid, "flagged": d > 1, "detail": d})
            # C3 — prefixed preposition before the stem inside the same word
            elif m.lexeme in PREFIX_PREPOSITIONS and _morph_no(m) < _morph_no(stem):
                pa, sa = aligned[m.idx], aligned[stem.idx]
                side = "fused" if set(pa) & set(sa) else ("before" if max(pa) < min(sa) else "after" if min(pa) > max(sa) else "mixed")
                want = facts.get("adposition")
                flagged = want in ("before", "after") and side in ("before", "after") and side != want
                items.append({"check": "prefix_prep", "idx": m.idx, "word": w.wid, "flagged": flagged, "detail": side})

    # C4 — construct chain members form one target block
    chains: dict[str, list] = collections.defaultdict(list)
    for t in toks:
        if getattr(t, "construct_group", None) and t.is_content and t.strong and t.idx in aligned:
            chains[t.construct_group].append(t)
    for members in chains.values():
        if len(members) < 2:
            continue
        pos = sorted({p for m in members for p in aligned[m.idx]})
        gap = [p for p in range(pos[0], pos[-1] + 1) if p not in pos]
        foreign = [p for p in gap if not (is_function and is_function(p))]
        for m in members:
            items.append({"check": "construct_gap", "idx": m.idx, "word": word_of.get(m.idx), "flagged": len(foreign) > 1,
                          "detail": len(foreign)})

    # C5 — psalm-title tokens that carry an alignment
    for t in toks:
        if getattr(t, "is_superscription", False) and t.idx in aligned:
            items.append({"check": "superscription", "idx": t.idx, "word": word_of.get(t.idx), "flagged": True, "detail": None})
    return items


def language_facts(iso: str) -> dict:
    from lexeme_aligner import gram_struct
    from lexeme_aligner.article_bound import is_bound
    return {"article_bound": bool(is_bound(iso)), "adposition": gram_struct.merged_direction(iso, "adposition"),
            "possessor": gram_struct.merged_direction(iso, "possessor")}


def run(iso: str, tag: str, usj_dir: Path, books: list[str], methods: tuple[str, ...], out_dir: Path = OUT,
        calibrate: bool = False, gold_method: str = "manual") -> dict:
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import BOOK_NUMBERS, encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.target_stopwords import StopwordFilter
    from lexeme_aligner.versification import remapper
    heb = HebrewSource()
    recs = build_corpus(books, usj_dir, heb, remap=remapper(tag, str(usj_dir)))
    ali = load_alignment(tag, methods, out_dir, {BOOK_NUMBERS[b] for b in books})
    stop = StopwordFilter(iso, str(usj_dir))
    facts = language_facts(iso)
    gold = None
    if calibrate:
        from lexeme_aligner.eval.contest_rule import gold_base_text
        from lexeme_aligner.eval.pos_score import load_gold
        gold, _ = load_gold(iso, usj_dir, books, gold_base_text(iso, gold_method=None if gold_method == "manual" else gold_method),
                            gold_methods=(gold_method,), remap=remapper(tag, str(usj_dir)))
    per: dict[str, collections.Counter] = {c: collections.Counter() for c in CHECKS}
    detail: dict[str, collections.Counter] = {c: collections.Counter() for c in CHECKS}
    flagged_examples: dict[str, list] = {c: [] for c in CHECKS}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        aligned = ali.get(ref, {})
        if not aligned:
            continue
        fn = (lambda p, toks=r.toks: p < len(toks) and stop.is_function(toks[p]))
        items = verse_checks(r.heb, aligned, facts, fn)
        wrong_word = _gold_wrong_words(r.heb, aligned, gold.get(ref)) if gold is not None else None
        for it in items:
            c = per[it["check"]]
            c["n"] += 1
            c["flagged"] += it["flagged"]
            detail[it["check"]][str(it["detail"])] += 1
            if it["flagged"] and len(flagged_examples[it["check"]]) < 20:
                flagged_examples[it["check"]].append({"ref": ref, "h_idx": it["idx"], "t_idx": aligned.get(it["idx"]), "detail": it["detail"]})
            if wrong_word is not None and it["word"] in wrong_word:
                key = "flagged" if it["flagged"] else "unflagged"
                c[f"judged_{key}"] += 1
                c[f"wrong_{key}"] += wrong_word[it["word"]]
    out = {"iso": iso, "tag": tag, "books": len(books), "facts": facts, "methods": list(methods), "checks": {}}
    for ch in CHECKS:
        c = per[ch]
        row = {"n": c["n"], "flagged": c["flagged"], "rate": round(c["flagged"] / c["n"], 4) if c["n"] else None,
               "detail": dict(detail[ch].most_common(8)), "examples": flagged_examples[ch][:5]}
        if gold is not None:
            jf, ju = c["judged_flagged"], c["judged_unflagged"]
            row["calibration"] = {"judged_flagged": jf, "judged_unflagged": ju,
                                  "p_wrong_flagged": round(c["wrong_flagged"] / jf, 4) if jf else None,
                                  "p_wrong_unflagged": round(c["wrong_unflagged"] / ju, 4) if ju else None}
        out["checks"][ch] = row
    return out


def _gold_wrong_words(heb: list, aligned: dict[int, list[int]], gv) -> dict[str, bool] | None:
    """{word id: our word span != the gold's word span} for the words the gold judges (content or not), or None without gold for the verse."""
    if gv is None:
        return None
    seen: collections.Counter = collections.Counter()
    key_idx: dict[tuple, int] = {}
    for t in sorted(heb, key=lambda t: t.idx):
        if t.strong:
            key_idx[(t.strong, seen[t.strong])] = t.idx
            seen[t.strong] += 1
    word_of = {i: w.wid for w in words_of(heb) for i in w.idx}
    g_count = collections.Counter(s for s, _k in gv.links)
    gw: dict[str, set[int]] = collections.defaultdict(set)
    for (s, k), pos in gv.links.items():
        idx = key_idx.get((s, k))
        if idx is None or g_count[s] != seen[s]:
            continue
        gw[word_of[idx]] |= set(pos)
    ow: dict[str, set[int]] = collections.defaultdict(set)
    for idx, pos in aligned.items():
        if idx in word_of:
            ow[word_of[idx]] |= set(pos)
    return {w: ow.get(w, set()) != g for w, g in gw.items()}


def table(qa_dir: Path = QA_DIR) -> list[dict]:
    """One row per (edition, check) from the written per-edition files: flag rate, and, where gold was available, P(word wrong | flagged)
    vs P(word wrong | not flagged) and their ratio (the 'lift' a consumer would need, >= 2 on several languages)."""
    rows = []
    for fp in sorted(Path(qa_dir).glob("*.json")):
        if fp.name.startswith("_"):
            continue
        d = json.loads(fp.read_text(encoding="utf-8"))
        for name, c in d.get("checks", {}).items():
            cal = c.get("calibration") or {}
            pf, pu = cal.get("p_wrong_flagged"), cal.get("p_wrong_unflagged")
            rows.append({"iso": d["iso"], "tag": d["tag"], "check": name, "n": c["n"], "flag_rate": c["rate"],
                         "p_wrong_flagged": pf, "p_wrong_unflagged": pu, "judged_flagged": cal.get("judged_flagged"),
                         "lift": round(pf / pu, 2) if pf is not None and pu else None})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso"); ap.add_argument("--tag"); ap.add_argument("--usj-dir", type=Path)
    ap.add_argument("--table", action="store_true", help=f"summarise every {QA_DIR}/<tag>.json into {QA_DIR}/_table.tsv and print it")
    ap.add_argument("--ot", action="store_true"); ap.add_argument("--nt", action="store_true")
    ap.add_argument("--book", action="append")
    ap.add_argument("--methods", default="spanext,gapfill,eflomal,gloss")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--calibrate", action="store_true", help="also report P(word wrong | flagged) vs unflagged against the language's gold")
    ap.add_argument("--gold-method", default="manual")
    ap.add_argument("--write", action="store_true", help=f"write the result to {QA_DIR}/<tag>.json")
    a = ap.parse_args(argv)
    if a.table:
        rows = table()
        cols = list(rows[0]) if rows else []
        text = "\t".join(cols) + "\n" + "".join("\t".join("" if r[c] is None else str(r[c]) for c in cols) + "\n" for r in rows)
        (QA_DIR / "_table.tsv").write_text(text, encoding="utf-8")
        print(text, end="")
        return 0
    if not (a.iso and a.tag and a.usj_dir):
        ap.error("--iso, --tag and --usj-dir are required (or --table)")
    from lexeme_aligner.run_pilot import NT_BOOKS, OT_BOOKS
    books = a.book or ((OT_BOOKS if a.ot or not a.nt else []) + (NT_BOOKS if a.nt or not a.ot else []))
    res = run(a.iso, a.tag, a.usj_dir, books, tuple(m for m in a.methods.split(",") if m), a.out, a.calibrate, a.gold_method)
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if a.write:
        QA_DIR.mkdir(parents=True, exist_ok=True)
        (QA_DIR / f"{a.tag}.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
