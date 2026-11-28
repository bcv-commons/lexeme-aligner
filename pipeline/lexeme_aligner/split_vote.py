"""Paradigm vote for family templates (split_gen --family with a template that has slot `seeds` or `vote: true`).

Method after make-tanakh-research's Swahili verb analyser (lab/training/verb_slots.py, lab/onboard/slot_cut.py, MIT, 2026-11-14):
every way the template's slots can cut a host word is listed (each slot optional, in order: prefix slots from the word start,
suffix slots from the stem outward), and the cut is kept whose BASE is attested in the most different affix frames (prefix
string + suffix string) among the edition's host word types; a base needs >= MIN_SUPPORT frames. No stem list: the bases come
out of the text. Ties between cuts of the same base and frame (ak|a|ba against a|ka|ba) go to the cut whose pieces are most
frequent across the other words' winning cuts.

Our additions: candidate forms per slot = the template's seeds (e.g. Swahili's closed lists, credited in the template) + the
text's frequent word starts / ends; after a first vote each (slot, form) must be grounded in the word alignment (split_gen._best:
its words link to one of the slot's features, lift >= 2 on both halves of the text) or it is dropped, and the vote is run again
with the kept forms; finally a word whose own linked tokens show none of its cut's features above their base rate stays whole.

The result is written as word entries with `parts` (the engine then splits exactly these words this way) and protected entries
for the other host words, so the engine's own chain rules decide nothing.
"""
from __future__ import annotations

import collections
import math

from . import split_gen as SG

MIN_SUPPORT = 3
MIN_BASE = 2
VOWELS = set("aeiouyɨøæəɛɔʉ")
FREQ_TOP = 60
ALT = (0.4, 1.5, 1.3)           # grounding of a frequent feature: share >= .4, lift >= 1.5, both halves >= 1.3


def _has_vowel(s: str) -> bool:
    return any(c in VOWELS for c in s)


def _host_words(linked, host: str) -> dict[str, list]:
    """plain word -> its linked rows [(fs, half)] for words whose linked tokens are mostly of the host class"""
    by = collections.defaultdict(list)
    for w, fs, ref in linked:
        by[SG._plain(w)].append((fs, ref % 2))
    out = {}
    for w, rows in by.items():
        cls = collections.Counter(SG.word_class(fs) for fs, _h in rows)
        # the host class must lead the other class and reach 30% of the word's links: a copula form (Zinza akaba "he was") is linked
        # to the predicate noun about as often as to the verb, and a strict majority left every one of them whole (2026-11-28)
        other = sum(v for k, v in cls.items() if k not in (host, None))
        if cls[host] >= other and cls[host] >= 0.3 * len(rows) and w.isalpha():
            out[w] = rows
    return out


def _parses(w: str, pre: list[set[str]], suf: list[set[str]]):
    """all (prefix pieces, suffix pieces, base): pieces as [(slot index, form)], suffix pieces stem-outward"""
    out = []

    def go_pre(i, rest, acc):
        if i == len(pre):
            go_suf(len(suf) - 1, rest, acc, [])
            return
        go_pre(i + 1, rest, acc)
        for f in pre[i]:
            if rest.startswith(f) and len(rest) - len(f) >= MIN_BASE:
                go_pre(i + 1, rest[len(f):], acc + [(i, f)])

    def go_suf(j, rest, pacc, sacc):
        if j < 0:
            if len(rest) >= MIN_BASE and _has_vowel(rest):
                out.append((pacc, list(reversed(sacc)), rest))
            return
        go_suf(j - 1, rest, pacc, sacc)
        for f in suf[j]:
            if rest.endswith(f) and len(rest) - len(f) >= MIN_BASE:
                go_suf(j - 1, rest[:-len(f)], pacc, sacc + [(j, f)])

    go_pre(0, w, [])
    return out


def _vote(words: list[str], pre, suf):
    """word -> chosen parse (or None: stays whole); support per base"""
    P = {w: _parses(w, pre, suf) for w in words}
    frames = collections.defaultdict(set)
    for w, ps in P.items():
        for p, s, b in ps:
            frames[b].add(("".join(f for _i, f in p), "".join(f for _j, f in s)))
    sup = {b: len(fr) for b, fr in frames.items()}
    best = {}
    for w, ps in P.items():
        top = max((sup[b] for _p, _s, b in ps), default=0)
        cand = [x for x in ps if sup[x[2]] == top]
        best[w] = cand if top >= MIN_SUPPORT else []
    # piece frequency over the winning cuts (ties split evenly), then the tie-break
    freq = collections.Counter()
    for w, cand in best.items():
        for p, s, _b in cand:
            for x in p:
                freq[("p",) + x] += 1 / len(cand)
            for x in s:
                freq[("s",) + x] += 1 / len(cand)
    out = {}
    for w, cand in best.items():
        if not cand:
            out[w] = None
            continue
        score = lambda c: (sum(math.log(freq[("p",) + x] + 1) for x in c[0]) + sum(math.log(freq[("s",) + x] + 1) for x in c[1]),
                           len(c[0]) + len(c[1]))
        out[w] = max(cand, key=score)
    return out, sup


def vote_template(linked, template: dict) -> tuple[list[dict], list[dict], dict]:
    """(morph entries for the record, word entries with parts or protected, info)"""
    morphs, words, info = [], [], {}
    hosts = list(dict.fromkeys(c["host"] for c in template["chains"]))
    for host in hosts:
        pre_slots = next((c["slots"] for c in template["chains"] if c["host"] == host and c["side"] == "prefix"), [])
        suf_slots = next((c["slots"] for c in template["chains"] if c["host"] == host and c["side"] == "suffix"), [])
        hw = _host_words(linked, host)
        starts, ends = collections.Counter(), collections.Counter()
        for w in hw:
            for n in range(1, 5):
                if len(w) - n >= MIN_BASE:
                    starts[w[:n]] += 1
                    ends[w[-n:]] += 1

        def cands(slots, freq, seeds=True, extra=True):
            out = []
            for s in slots:
                lo = 1 if s.get("one_letter") else 2
                fs = {SG._plain(x) for x in s.get("seeds", [])} if seeds else set()
                if extra and not s.get("seeds_only"):
                    fs |= {a for a, _n in freq.most_common(FREQ_TOP) if len(a) >= lo}
                out.append({f for f in fs if f and f.isalpha()})
            return out

        def made_of(f, forms):
            """f is a sequence of >= 2 forms of `forms`"""
            def go(r, k):
                if not r:
                    return k >= 2
                return any(r.startswith(x) and go(r[len(x):], k + 1) for x in forms if x)
            return go(f, 0)

        words_ = sorted(hw)

        def ground_round(parse, pre, suf):
            kept_pre, kept_suf, stats = [set() for _ in pre], [set() for _ in suf], {}
            for side, slots, sets, kept in (("p", pre_slots, pre, kept_pre), ("s", suf_slots, suf, kept_suf)):
                for i, slot in enumerate(slots):
                    for f in sets[i]:
                        use = {w for w, x in parse.items() if x and (i, f) in (x[0] if side == "p" else x[1])}
                        if not use:
                            continue
                        A = [(w, fs, h) for w in use for fs, h in hw[w]]
                        C = [(w, fs, h) for w in words_ if w not in use and parse.get(w) for fs, h in hw[w]]
                        b = SG._best(A, C, slot["renders"], lambda w: parse[w][2], alt=ALT)
                        if b is None and slot.get("ground") is False and f in {SG._plain(x) for x in slot.get("seeds", [])} \
                                and len(use) >= SG.MIN_STEMS:
                            # a slot that renders nothing of its own (the Bantu final vowel -a): its seeds are cut by the vote alone
                            b = {"feature": "none", "lift": 0.0, "count": len(A), "words": len(use), "stems": len(use),
                                 "halves": [0.0, 0.0], "base": 1.0}
                        if b:
                            kept[i].add(f)
                            stats[(side, i, f)] = b
            return kept_pre, kept_suf, stats

        has_seeds = any(sl.get("seeds") for sl in pre_slots + suf_slots)
        # stage A: seeds only (or the text's frequent forms where the template has none)
        pre, suf = cands(pre_slots, starts, True, not has_seeds), cands(suf_slots, ends, True, not has_seeds)
        parse, sup = _vote(words_, pre, suf)
        kp, ks, stats = ground_round(parse, pre, suf)
        info_stage = {"A": {"prefix": [sorted(x) for x in kp], "suffix": [sorted(x) for x in ks]}}
        if has_seeds and template.get("text_forms", True):
            # stage B: the kept seeds + the text's frequent forms that are not a chain of kept ones (nimu- = ni- + mu-)
            allp = set().union(*kp) if kp else set()
            alls = set().union(*ks) if ks else set()
            fp, fsu = cands(pre_slots, starts, False, True), cands(suf_slots, ends, False, True)
            pre = [kp[i] | {f for f in fp[i] if not made_of(f, allp)} for i in range(len(kp))]
            suf = [ks[i] | {f for f in fsu[i] if not made_of(f[::-1], {x[::-1] for x in alls})} for i in range(len(ks))]
            parse, sup = _vote(words_, pre, suf)
            kp, ks, stats = ground_round(parse, pre, suf)
            info_stage["B"] = {"prefix": [sorted(x) for x in kp], "suffix": [sorted(x) for x in ks]}
        kept_pre, kept_suf = kp, ks
        parse, sup = _vote(words_, kept_pre, kept_suf)
        # per word: its own links must back one of its pieces' features
        n_cut = n_whole = n_unbacked = 0
        for w in words_:
            x = parse.get(w)
            if not x or (not x[0] and not x[1]):
                words.append({"id": f"w:{w}", "kind": "word", "form": w, "value": "WORD", "method": "generated",
                              "by": f"lexeme_aligner.split_vote {SG.VERSION} (no voted cut)"})
                n_whole += 1
                continue
            rows = hw[w]
            backed = len(rows) < 3
            for side, pieces in (("p", x[0]), ("s", x[1])):
                for i, f in pieces:
                    b = stats.get((side, i, f))
                    if b and sum(b["feature"] in fs for fs, _h in rows) / len(rows) > b["base"]:
                        backed = True
            if not backed:
                words.append({"id": f"w:{w}", "kind": "word", "form": w, "value": "WORD", "method": "generated",
                              "by": f"lexeme_aligner.split_vote {SG.VERSION} (own links do not back the cut)"})
                n_unbacked += 1
                continue
            parts = [{"form": f + "-", "text": f} for _i, f in x[0]] + [{"form": x[2], "text": x[2]}] + \
                    [{"form": "-" + f, "text": f} for _j, f in x[1]]
            words.append({"id": f"w:{w}", "kind": "word", "form": w, "value": "WORD", "parts": parts, "method": "generated",
                          "by": f"lexeme_aligner.split_vote {SG.VERSION} (paradigm vote, base support {sup[x[2]]})"})
            n_cut += 1
        for (side, i, f), b in sorted(stats.items()):
            slot = (pre_slots if side == "p" else suf_slots)[i]
            form = f + "-" if side == "p" else "-" + f
            morphs.append({"id": f"g:{host}:{slot['name']}:{form}", "kind": "morph", "value": f"{slot['name']}:{b['feature']}",
                           "slot": "prefix" if side == "p" else "suffix", "form": form, "position": i + 1,
                           "host": host, "confidence": "generated", "method": "generated", "status": "generated",
                           "by": f"lexeme_aligner.split_vote {SG.VERSION}", "feature": b["feature"], "template_slot": slot["name"],
                           "seed": f in {SG._plain(s) for s in slot.get("seeds", [])},
                           "stats": {"count": b["count"], "words": b["words"], "stems": b["stems"], "lift": round(b["lift"], 2),
                                     "half_lifts": [round(v, 2) for v in b["halves"]], "base_rate": round(b["base"], 4)}})
        info[host] = {"stages": info_stage, "host_words": len(words_), "cut": n_cut, "whole": n_whole, "unbacked": n_unbacked,
                      "kept_forms": {"prefix": [sorted(s) for s in kept_pre], "suffix": [sorted(s) for s in kept_suf]}}
    return morphs, words, info
