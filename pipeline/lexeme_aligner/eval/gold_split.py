"""Split Clear's word-level OT gold into MACULA morphemes (2026-10-26).

Clear aligned the Hebrew OT word by word: every link sits on the word's stem morpheme with `source_unit: word` ("covers every
morpheme of the word"), so for בִּימֵי -> "in the days" it never says that "in" is the prefix's. Our spine splits prefixes and suffixes
off, so morpheme-level scoring needs the link divided. Rule: inside the gold span, a prefix takes the leftmost word its lexicon allows
(a fixed list per lemma, plus MACULA's own per-morpheme gloss words for the prepositions), a suffix the rightmost; a morpheme with no
such word in the span gets no link (rendered by nothing of its own); the content morpheme keeps the rest. Words of one morpheme are
copied unchanged. NOT checkable against Clear (it has no morpheme links) — hand-check a sample (`--show`).

    .venv/bin/python -m lexeme_aligner.eval.gold_split --iso bsb --publish-iso eng --book RUT --show 30
"""
from __future__ import annotations

import argparse
import collections
import random
import re
from pathlib import Path

from lexeme_aligner.eval.pos_score import GoldVerse

# per (lemma, prefix|suffix): English words that render it; None = also accept the morpheme's own gloss words
PREFIX = {
    "וְ": ({"and", "but", "so", "then", "now", "or", "yet", "when", "that", "while"}, False),
    "הַ": ({"the"}, False),                     # MACULA's article gloss is often a phrase ("of", "young ..."): the fixed list only
    "הֲ": (set(), False),                        # interrogative: no English word of its own
    "לְ": ({"to", "for", "of", "into", "at", "toward", "towards", "belongs", "belong", "unto"}, True),
    "בְּ": ({"in", "on", "with", "by", "at", "among", "into", "through", "against", "when", "within", "upon"}, True),
    "מִן": ({"from", "than", "out", "of", "some", "since", "away", "more", "because"}, True),
    "כְּ": ({"like", "as", "according", "about", "when", "just", "approximately"}, True),
    "שֶׁ": ({"who", "which", "that", "whom", "what"}, True),
    "אֵת": (set(), True),
}
SUFFIX_WORDS = {"my", "me", "mine", "myself", "i", "our", "us", "ours", "we", "your", "you", "yours", "yourself", "yourselves",
                "his", "him", "he", "himself", "her", "hers", "she", "herself", "its", "it", "itself", "their", "them", "they",
                "theirs", "themselves", "thy", "thee", "thine", "ye"}


def _gloss_words(g: str | None) -> set[str]:
    return {w for w in re.split(r"[.\s_]+", (g or "").lower()) if w and w.isalpha()}


# how each split link was derived (published as the link's attribution, so a consumer can keep or drop any kind)
DERIVATION = {
    "word": "Clear link, unchanged (one-morpheme word)",
    "list": "split from Clear's word link: the word is in our fixed list for this prefix/suffix",
    "gloss": "split from Clear's word link: the word is in MACULA's English gloss of this morpheme",
    "none": "split from Clear's word link: no word of the span renders this morpheme on its own",
    "rest": "split from Clear's word link: what remains for the content morpheme",
}


def allowed(t, kind: str) -> tuple[set[str], set[str]]:
    """(fixed-list words, MACULA-gloss words) this morpheme may take."""
    if kind == "suffix":
        return (SUFFIX_WORDS, set()) if t.lemma == "הוּא" else (set(), _gloss_words(t.gloss_en))
    words, use_gloss = PREFIX.get(t.lemma, (set(), True))
    return words, (_gloss_words(t.gloss_en) - words if use_gloss else set())


def split_verse(gv: GoldVerse, heb: list, toks: list[str], key_of: dict, stats: collections.Counter) -> GoldVerse:
    """gv (keys (strong, k) counted over the gold's rows, raw = Clear source id) -> GoldVerse keyed by the spine's (strong, k)."""
    from lexeme_aligner.eval.word_bridge import words_of
    tok_of_key = {k: t for t in heb for k in (t.keys or [])}
    word_of = {i: w for w in words_of(heb) for i in w.idx}
    by_idx = {t.idx: t for t in heb}
    low = [w.lower() for w in toks]
    out = GoldVerse(gv.ref, claimed=set(gv.claimed), exact_keys=True)
    for gkey, span in gv.links.items():
        sid = gv.raw.get(gkey, (None,))[0]
        t = tok_of_key.get(sid[1:] if sid and sid[0] in "on" else sid)
        if t is None:
            stats["gold link: no spine token for its id"] += 1
            continue
        w = word_of[t.idx]
        parts: dict[int, set[int]] = {}
        how: dict[int, str] = {}
        if len(w.idx) == 1:
            parts[t.idx] = set(span); how[t.idx] = "word"
            stats["one-morpheme word"] += 1
        else:
            stats["multi-morpheme word"] += 1
            heads = w.content or [t.idx]
            left = set(span)
            for m in w.idx:
                if m in heads:
                    continue
                kind = "prefix" if m < heads[0] else "suffix"
                fixed, gloss = allowed(by_idx[m], kind)
                cand = sorted(j for j in left if low[j] in fixed | gloss)
                if cand:
                    j = cand[0] if kind == "prefix" else cand[-1]
                    left.discard(j)
                    parts[m] = {j}; how[m] = "list" if low[j] in fixed else "gloss"
                    stats[f"{kind}: own word ({how[m]})"] += 1
                else:
                    parts[m] = set(); how[m] = "none"
                    stats[f"{kind}: no word of its own"] += 1
            for h in heads:
                parts[h] = left if h == heads[0] else set(); how[h] = "rest"
            if not left:
                stats["content morpheme left without a word"] += 1
        for h, pos in parts.items():
            key = key_of.get(h)
            if key is None:
                continue
            out.links[key] = pos
            out.surfaces[key] = " ".join(toks[j] for j in sorted(pos))
            out.raw[key] = gv.raw.get(gkey, ("", []))
            out.unit[key] = how[h]
    return out


def split_gold(gold: dict[int, GoldVerse], recs, spine, stats: collections.Counter | None = None) -> dict[int, GoldVerse]:
    from lexeme_aligner.refs import encode
    stats = stats if stats is not None else collections.Counter()
    out = {}
    for r in recs:
        ref = encode(r.book, r.ch, r.v)
        gv = gold.get(ref)
        if gv is None or ref not in spine.key_of:
            continue
        out[ref] = split_verse(gv, r.heb, list(r.toks), spine.key_of[ref], stats)
    return out


def main() -> int:
    from lexeme_aligner.eval.contest_rule import gold_base_text
    from lexeme_aligner.eval.pos_score import load_gold, load_spine
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True); ap.add_argument("--publish-iso", required=True)
    ap.add_argument("--book", action="append", required=True)
    ap.add_argument("--show", type=int, default=0, help="print N random split words (multi-morpheme) for a hand check")
    a = ap.parse_args()
    usj = Path(f"pipeline/work/ingest-cache/usj-{a.iso}")
    rm = remapper(a.iso, str(usj))
    gold, _ = load_gold(a.publish_iso, usj, a.book, gold_base_text(a.publish_iso), gold_methods=("manual",), remap=rm)
    spine = load_spine(a.book, usj, a.iso)
    recs = build_corpus(a.book, usj, HebrewSource(), remap=rm)
    stats: collections.Counter = collections.Counter()
    split = split_gold(gold, recs, spine, stats)
    for k, v in sorted(stats.items()):
        print(f"{k:42} {v}")
    if a.show:
        from lexeme_aligner.eval.word_bridge import words_of
        rows = []
        for r in recs:
            sv = split.get(encode(r.book, r.ch, r.v))
            if sv is None:
                continue
            ko = spine.key_of[encode(r.book, r.ch, r.v)]
            for w in words_of(r.heb):
                if len(w.idx) > 1 and any(ko.get(h) in sv.links for h in w.idx):
                    bits = [f"{next(t.surface for t in r.heb if t.idx == h)}={sv.surfaces.get(ko.get(h), '?') or '-'}" for h in w.idx]
                    rows.append(f"{r.book} {r.ch}:{r.v}  " + "  ".join(bits))
        random.seed(11)
        for x in random.sample(rows, min(a.show, len(rows))):
            print("  ", x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
