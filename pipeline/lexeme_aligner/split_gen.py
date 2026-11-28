"""Generate a bcv-segfacts/0.1 bundle (seg_facts.json) for an edition from its own text and its word alignment, for the split engine
(pipeline/split_engine). Plan: internal-docs/split-generator-plan-2026-11-28.md.

Candidates are the affixes of bcv-commons/target-morphology (frequency based, every language). An affix is KEPT only where the
alignment grounds it: words carrying it are linked to source words with a feature (negation, person, tense, mood, case, number,
Hebrew stem, or a source function word such as a preposition, the article or a conjunction) clearly more often than comparable words
without it. This is make-tanakh's own test for its morph entries (lift of a Greek feature), done with our alignments, so it runs for
any language. The engine then decides per word whether a cut is attested (it never cuts a word whose stem the vocabulary does not
support).

    python -m lexeme_aligner.split_gen --iso zin --tag zin_wbt --usj DIR --align DIR --out BUNDLE_DIR [--morph FILE]

The bundle holds rules and word counts, never the text. `--align` = a folder with align_eflomal_<tag>_<BOOK>.jsonl[.gz] files of a
WHOLE-WORD run (target tokens = the edition's tokens).
Judging a generated bundle (2026-11-28): by target_split_check (content_stable / function_stable against whole words), not by agreement
with a hand split (pipeline/work/split-gen/compare_splits.py stays a diagnostic). Against Clear (Arabic) and HELFI (Finnish) gold,
content_stable ranked split variants as gold does; cut agreement with a hand split and "function linked" did not.
"""
import argparse
import collections
import gzip
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MORPH = REPO / "publish/target-morphology"
NEG = {"οὐ", "μή", "οὐδέ", "μηδέ", "οὐχί", "οὔτε", "μήτε", "οὐδείς", "μηδείς", "οὐκέτι", "μηκέτι", "οὔπω", "μήπω",
       "לֹא", "אַל", "אַיִן", "בִּלְתִּי", "בַּל", "בְּלִי"}
MIN_COUNT, MIN_STEMS, MIN_LIFT, MIN_HALF_LIFT, MIN_STEM_LEN, MIN_SHARE, MIN_AFFIX_LEN = 20, 10, 2.0, 1.5, 3, 0.1, 2
VERSION = "0.1"


def _engine():
    sys.path.insert(0, str(REPO / "pipeline/split_engine"))
    import bcv_segment
    return bcv_segment


def _plain(w: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", w.lower()) if not unicodedata.combining(c))


PRON = {"αὐτός", "σύ", "ἐγώ", "ἡμεῖς", "ὑμεῖς", "ἑαυτοῦ", "σεαυτοῦ", "ἐμαυτοῦ"}


def source_features(tok) -> set[str]:
    """The features of one source word (a HebToken) an affix may render."""
    f = set()
    lemma = unicodedata.normalize("NFC", tok.lemma or "")
    if lemma in NEG:
        f.add("neg")
    if not tok.is_content and lemma:
        f.add(f"fn={lemma}")
    if lemma in PRON and getattr(tok, "case_", None) in ("accusative", "dative"):
        f.add("obj")                                      # an object pronoun: what a verb's object marker renders
    for name in ("person", "tense", "mood", "case_", "stem", "voice"):
        v = getattr(tok, name, None)
        if v:
            f.add(f"{name.rstrip('_')}={v}")
    if getattr(tok, "person", None) and getattr(tok, "number", None):
        f.add(f"pn={tok.person}.{tok.number}")
    elif getattr(tok, "number", None) == "plural":
        f.add("plural")
    return f


def _open(p: Path):
    return gzip.open(p, "rt", encoding="utf-8") if p.suffix == ".gz" else open(p, encoding="utf-8")


def linked_tokens(tag: str, usj: Path, align: Path, books=None):
    """[(written token lowercased NFC, features of the source words linked to it, verse ref)] for every linked target token, and
    {written token: count} for the whole text."""
    sys.path.insert(0, str(REPO / "pipeline/vendor/bibles"))
    from bcv_written import tokenize_written
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.run_pilot import build_corpus
    from lexeme_aligner.versification import remapper
    files = sorted(list(align.glob(f"align_eflomal_{tag}_*.jsonl")) + list(align.glob(f"align_eflomal_{tag}_*.jsonl.gz")))
    by_book = {f.name.split("_")[-1].split(".")[0]: f for f in files}
    if books:
        by_book = {b: f for b, f in by_book.items() if b in books}
    heb, remap = HebrewSource(), remapper(tag, str(usj))
    vocab, out = collections.Counter(), []
    for book, f in sorted(by_book.items()):
        recs = {(r.book, r.ch, r.v): r for r in build_corpus([book], usj, heb, remap)}
        for r in recs.values():
            vocab.update(unicodedata.normalize("NFC", w).lower() for w in tokenize_written(r.text or ""))
        with _open(f) as fh:
            for line in fh:
                d = json.loads(line)
                r = recs.get((d["book"], d["chapter"], d["verse"]))
                if r is None:
                    continue
                written = [unicodedata.normalize("NFC", w).lower() for w in tokenize_written(r.text or "")]
                feats = collections.defaultdict(set)
                for p in list(d.get("pairs") or []) + list(d.get("held") or []):
                    h = p.get("h_idx")
                    if h is None or h >= len(r.heb):
                        continue
                    fs = source_features(r.heb[h])
                    for t in p.get("t_idx") or []:
                        feats[t] |= fs
                for t, fs in feats.items():
                    if t < len(written):
                        out.append((written[t], fs, d["ref"]))
    return out, vocab


def ground(cands: dict[str, list[str]], linked, vocab) -> list[dict]:
    """Keep each candidate affix whose words are linked to some source feature at >= MIN_LIFT times the rate of comparable words
    (linked words long enough to carry it, without it), with >= MIN_COUNT such words, >= MIN_STEMS distinct stems, and the lift
    holding (>= MIN_HALF_LIFT) on both halves of the verses (odd / even ref); the feature must be on >= MIN_SHARE of the affix's words."""
    plain_vocab = {_plain(w) for w in vocab}
    rows = [(_plain(w), fs, ref % 2) for w, fs, ref in linked]
    kept = []
    for side in ("prefix", "suffix"):
        known = plain_vocab | _stem_set(plain_vocab, side)
        for a in cands.get(side + "es", []):
            a = _plain(a)
            if not a:                                     # one letter is kept here, but used only inside a chain (chains())
                continue
            has = (lambda w: w.startswith(a)) if side == "prefix" else (lambda w: w.endswith(a))
            rest = (lambda w: w[len(a):]) if side == "prefix" else (lambda w: w[:-len(a)])
            A, C = [], []
            for w, fs, half in rows:
                if len(w) < len(a) + MIN_STEM_LEN:
                    continue
                (A if has(w) and rest(w) in known else C).append((w, fs, half))
            if len(A) < MIN_COUNT or not C:
                continue
            best = None
            fa, fc = collections.Counter(), collections.Counter()
            for _, fs, _h in A:
                fa.update(fs)
            for _, fs, _h in C:
                fc.update(fs)
            for feat, k in fa.items():
                if k < MIN_COUNT or k < MIN_SHARE * len(A):   # the feature must go with a real share of the affix's words
                    continue
                fam = _family(feat)                       # compare like with like: a verb feature among words linked to verbs
                a_f = [(w, fs, h) for w, fs, h in A if fam(fs)]
                c_f = [(w, fs, h) for w, fs, h in C if fam(fs)]
                if not a_f or not c_f:
                    continue
                lift = (k / len(a_f)) / ((sum(feat in fs for _, fs, _h in c_f) + 1) / (len(c_f) + 1))
                if lift < MIN_LIFT:
                    continue
                stems = {rest(w) for w, fs, _h in A if feat in fs}
                if len(stems) < MIN_STEMS:
                    continue
                halves = []
                for h in (0, 1):
                    a_h = [fs for _, fs, hh in a_f if hh == h]
                    c_h = [fs for _, fs, hh in c_f if hh == h]
                    ka, kc = sum(feat in fs for fs in a_h), sum(feat in fs for fs in c_h)
                    halves.append((ka / max(len(a_h), 1)) / ((kc + 1) / (len(c_h) + 1)))
                if min(halves) < MIN_HALF_LIFT:
                    continue
                if best is None or lift > best[1]:
                    base = (sum(feat in fs for _, fs, _h in c_f) + 1) / (len(c_f) + 1)
                    best = (feat, lift, k, len(stems), halves, base)
            if best:
                feat, lift, k, ns, halves, base = best
                form = a + "-" if side == "prefix" else "-" + a
                host = _host(A)
                kept.append({"id": f"g:{form}", "kind": "morph", "value": feat, "slot": side, "form": form, "position": 1,
                             **({"host": host} if host else {}),
                             "confidence": "generated", "method": "generated", "by": f"lexeme_aligner.split_gen {VERSION}",
                             "date": date.today().isoformat(), "status": "generated", "feature": feat,
                             "stats": {"count": k, "words": len(A), "stems": ns, "lift": round(lift, 2),
                                       "half_lifts": [round(x, 2) for x in halves], "base_rate": round(base, 4)}})
    return kept


def renders_word(feat: str) -> bool:
    """A feature that is a source WORD of its own (a negation, preposition, article, conjunction, pronoun): an affix rendering it
    links to that word once cut off."""
    return feat == "neg" or feat.startswith("fn=")


def host_filter(kept: list[dict]) -> list[dict]:
    """Verb affixes may render the verb's inflection (person, tense, mood); on nouns and host-less affixes only what renders a
    source word of its own is cut (Arabic al- = the article, wa- = and), not agreement such as Bantu noun classes (aba|antu: the
    Zinza check lost stability with those cut, 2026-11-28)."""
    return [m for m in kept if m.get("host") == "verb" or renders_word(m["feature"])]


def chains(kept: list[dict]) -> tuple[list[dict], dict]:
    """Affix chains, as the hand bundles have them (ni-mu-ka-, ku-gaamb-a). A kept affix that is a sequence of other kept affixes of
    its side (nimu- = ni- + mu-) is dropped; its pieces get chain positions = their place in such sequences, left to right (prefix 1
    at the word start, suffix 1 next to the stem), one entry per (form, position) seen. A kept affix no sequence explains stays at
    position 1. A one-letter affix is kept only as a piece of a chain (alone it cuts short frequent words: o|mu, Mú|ungu)."""
    out, info = [], {"blocks": 0}
    for side in ("prefix", "suffix"):
        by = {m["form"].strip("-"): m for m in kept if m["slot"] == side}
        forms = set(by)

        def parts(b):
            """The fewest pieces (>= 2, each a kept form, none = b) that make b; ties: the highest smallest lift."""
            best = None

            def go(rest, acc):
                nonlocal best
                if not rest:
                    if len(acc) >= 2:
                        key = (len(acc), -min(by[x]["stats"]["lift"] for x in acc))
                        if best is None or key < best[0]:
                            best = (key, list(acc))
                    return
                if best is not None and len(acc) + 1 > best[0][0]:
                    return
                for n in range(1, len(rest) + 1):
                    if rest[:n] in forms and rest[:n] != b:
                        go(rest[n:], acc + [rest[:n]])
            go(b, [])
            return best[1] if best else None

        positions = collections.defaultdict(set)
        blocks = set()
        for b in sorted(forms, key=len):
            ps = parts(b) if len(b) >= 2 else None
            if ps:
                blocks.add(b)
                for i, x in enumerate(ps, 1):
                    positions[x].add(i)
        info["blocks"] += len(blocks)
        for f, m in by.items():
            if f in blocks:
                continue
            pos = positions.get(f)
            if not pos:
                if len(f) < MIN_AFFIX_LEN:
                    continue
                pos = {1}
            for p in sorted(pos):
                out.append({**m, "id": f"{m['id']}@{p}", "position": p})
    return out, info


_VERBAL, _NOMINAL = ("person=", "pn=", "tense=", "mood=", "stem="), ("case=", "plural")


def _family(feat: str):
    """Which linked words a feature is compared among: verb features among words linked to a verb (a person / tense / mood /
    Hebrew stem), noun features among words linked to a noun form (case, plural); negation and function words among all."""
    if feat.startswith(_VERBAL):
        return lambda fs: any(f.startswith(_VERBAL) for f in fs)
    if feat.startswith(_NOMINAL):
        return lambda fs: any(f.startswith(_NOMINAL) for f in fs)
    return lambda fs: True


def word_class(fs: set[str]) -> str | None:
    """The source class a linked target word renders: verb, noun, or None."""
    if any(f.startswith(_VERBAL) for f in fs):
        return "verb"
    if any(f.startswith(_NOMINAL) for f in fs):
        return "noun"
    return None


def _host(rows) -> str | None:
    """An affix's host: the class (verb / noun) of at least 60% of its words that are linked to a verb or a noun, else none."""
    c = collections.Counter(word_class(fs) for _w, fs, _h in rows)
    n = c["verb"] + c["noun"]
    for cls in ("verb", "noun"):
        if n and c[cls] >= 0.6 * n:
            return cls
    return None


def classes_of(linked) -> dict[str, dict[str, int]]:
    """{written word: {"": linked tokens, "verb": n, "noun": n}} for the engine's host-aware chains."""
    out = collections.defaultdict(collections.Counter)
    for w, fs, _ref in linked:
        out[w][""] += 1
        if (c := word_class(fs)):
            out[w][c] += 1
    return {w: dict(c) for w, c in out.items()}


_stem_cache: dict = {}


def _stem_set(plain_vocab: set[str], side: str) -> set[str]:
    """Stems seen with some OTHER affix-like start/end: a remainder that occurs as the rest of another vocabulary word after
    stripping 1-3 letters on the same side (abantu -> bantu, ntu...). Keeps a stem that never occurs bare (Bantu verbs) in play."""
    key = (id(plain_vocab), side)
    if key not in _stem_cache:
        s = set()
        for w in plain_vocab:
            for n in (1, 2, 3, 4):
                if len(w) - n >= MIN_STEM_LEN:
                    s.add(w[n:] if side == "prefix" else w[:-n])
        _stem_cache[key] = s
    return _stem_cache[key]


def own_candidates(vocab, top: int = 150, max_len: int = 4) -> dict[str, list[str]]:
    """Affix candidates from the text itself: the starts (ends) of 1..max_len letters shared by the most word types whose rest is at
    least MIN_STEM_LEN letters (target-morphology keeps only its top 40 per side, which can miss a frequent marker, e.g. Zinza ti-)."""
    pv = {_plain(w) for w in vocab}
    pre, suf = collections.Counter(), collections.Counter()
    for w in pv:
        for n in range(1, max_len + 1):
            if len(w) - n >= MIN_STEM_LEN:
                pre[w[:n]] += 1
                suf[w[-n:]] += 1
    alpha = lambda a: all(c.isalpha() for c in a)
    return {"prefixes": [a for a, _ in pre.most_common() if alpha(a)][:top], "suffixes": [a for a, _ in suf.most_common() if alpha(a)][:top]}


FAMILIES = REPO / "config/split_families"


def family_of(iso: str) -> str | None:
    """The family template for a language: config/split_families/<name>.json whose `glottolog` lists the language's Glottolog family
    (pipeline/vendor/grambank/glottolog_languages.csv), or None."""
    import csv
    fam = next((r["Family_ID"] for r in csv.DictReader(open(REPO / "pipeline/vendor/grambank/glottolog_languages.csv", encoding="utf-8"))
                if r["ISO639P3code"] == iso), None)
    for f in sorted(FAMILIES.glob("*.json")):
        if fam and fam in json.loads(f.read_text(encoding="utf-8")).get("glottolog", []):
            return f.stem
    return None


def _renders(feat: str, renders: list[str]) -> bool:
    return any(feat == r or (r.endswith("=") and feat.startswith(r)) for r in renders)


def _best(A, C, renders, rest, alt: tuple | None = None):
    """The slot feature that grounds A against C best (lift, count, share, stems, both halves), or None. Items (form, fs, half)."""
    if len(A) < MIN_COUNT or not C:
        return None
    fa = collections.Counter(f for _w, fs, _h in A for f in fs if _renders(f, renders))
    best = None
    for feat, k in fa.items():
        if k < MIN_COUNT or k < MIN_SHARE * len(A):
            continue
        kc = sum(feat in fs for _w, fs, _h in C)
        base = (kc + 1) / (len(C) + 1)
        lift = (k / len(A)) / base
        # alt = (share, lift, half lift): a frequent feature (3sg on half the verbs) cannot reach lift 2; it may pass with a high
        # share in A and a smaller lift (split_vote)
        relaxed = alt is not None and k / len(A) >= alt[0] and lift >= alt[1]
        if lift < MIN_LIFT and not relaxed:
            continue
        stems = {rest(w) for w, fs, _h in A if feat in fs}
        if len(stems) < MIN_STEMS:
            continue
        halves = []
        for h in (0, 1):
            a_h = [fs for _w, fs, hh in A if hh == h]
            c_h = [fs for _w, fs, hh in C if hh == h]
            halves.append((sum(feat in fs for fs in a_h) / max(len(a_h), 1)) / ((sum(feat in fs for fs in c_h) + 1) / (len(c_h) + 1)))
        if min(halves) < (alt[2] if relaxed and lift < MIN_LIFT else MIN_HALF_LIFT):
            continue
        if best is None or lift > best["lift"]:
            best = {"feature": feat, "lift": lift, "count": k, "words": len(A), "stems": len(stems), "halves": halves, "base": base}
    return best


def ground_template(linked, template: dict, top: int = 150) -> list[dict]:
    """Affixes slot by slot along each chain of a family template. A chain's rows are the linked words of its host class (verb /
    noun: word_class). Prefix slots are grounded from the word start in order, suffix slots from the word end, outermost slot first;
    after a slot is grounded its kept forms are stripped (longest match, optional) so the next slot is grounded on what is left.
    A form is kept in a slot when its words link to one of the slot's features (_best); a one-letter form only where the slot
    allows it."""
    kept = []
    for ch in template["chains"]:
        host, side, slots = ch["host"], ch["side"], ch["slots"]
        rows = [[_plain(w), fs, ref % 2] for w, fs, ref in linked if word_class(fs) == host]
        order = list(enumerate(slots, 1)) if side == "prefix" else list(reversed(list(enumerate(slots, 1))))
        for pos, slot in order:
            lo = 1 if slot.get("one_letter") else 2
            cnt = collections.Counter()
            for rem in {r[0] for r in rows}:
                for n in range(lo, 5):
                    if len(rem) - n >= MIN_STEM_LEN:
                        cnt[rem[:n] if side == "prefix" else rem[-n:]] += 1
            got = []
            for a, _n in cnt.most_common(top):
                if not all(c.isalpha() for c in a):
                    continue
                has = (lambda w, a=a: w.startswith(a)) if side == "prefix" else (lambda w, a=a: w.endswith(a))
                rest = (lambda w, a=a: w[len(a):]) if side == "prefix" else (lambda w, a=a: w[:-len(a)])
                A = [(r[0], r[1], r[2]) for r in rows if has(r[0]) and len(r[0]) - len(a) >= MIN_STEM_LEN]
                C = [(r[0], r[1], r[2]) for r in rows if not has(r[0])]
                b = _best(A, C, slot["renders"], rest)
                if b:
                    got.append((a, b))
            # a longer form that is only a kept shorter one plus letters of the next slot is not this slot's (nimu- = ni- + mu-)
            forms = {a for a, _b in got}
            got = [(a, b) for a, b in got if not any(a != f and (a.startswith(f) if side == "prefix" else a.endswith(f)) and
                                                     _b_feat(got, f) == b["feature"] for f in forms)]
            for a, b in got:
                form = a + "-" if side == "prefix" else "-" + a
                kept.append({"id": f"g:{host}:{slot['name']}:{form}", "kind": "morph", "value": f"{slot['name']}:{b['feature']}",
                             "slot": side, "form": form, "position": pos, "host": host,
                             "confidence": "generated", "method": "generated", "by": f"lexeme_aligner.split_gen {VERSION}",
                             "date": date.today().isoformat(), "status": "generated", "feature": b["feature"], "template_slot": slot["name"],
                             "stats": {"count": b["count"], "words": b["words"], "stems": b["stems"], "lift": round(b["lift"], 2),
                                       "half_lifts": [round(x, 2) for x in b["halves"]], "base_rate": round(b["base"], 4)}})
            strip = sorted({a for a, _b in got}, key=len, reverse=True)
            for r in rows:                                # strip this slot's form (longest), so the next slot sees the rest
                for a in strip:
                    if (r[0].startswith(a) if side == "prefix" else r[0].endswith(a)) and len(r[0]) - len(a) >= MIN_STEM_LEN:
                        r[0] = r[0][len(a):] if side == "prefix" else r[0][:-len(a)]
                        break
    return kept


def _b_feat(got, f):
    return next((b["feature"] for a, b in got if a == f), None)


def protect_words(facts: dict, linked) -> list[dict]:
    """Word entries (never split) for words whose OWN links do not back their cut: a word with >= 3 linked tokens is protected
    when none of its affixes' features is on its linked tokens at more than the affix's base rate (the rate among comparable words
    without it). Words seen less often keep the engine's decision."""
    eng = _engine()
    G = type("G", (), {})()
    G.morphs, G.words = facts["morphs"], []
    classes = {w: collections.Counter(c) for w, c in (facts.get("classes") or {}).items()} or None
    S = eng.Segmenter(G, facts["orthography"], facts["vocab"], classes)
    by_form = collections.defaultdict(list)
    for m in facts["morphs"]:
        by_form[(m["form"], m.get("position") or 0)].append(m)
    own = collections.defaultdict(list)
    for w, fs, _ref in linked:
        own[w].append(fs)
    out = []
    for w, fss in own.items():
        if len(fss) < 3:
            continue
        ms = [m for m in S.split(w) if m.slot in ("prefix", "suffix")]
        if not ms:
            continue
        backed = False
        for m in ms:
            for e in by_form.get((m.form, m.position), []):
                rate = sum(e["feature"] in fs for fs in fss) / len(fss)
                if rate > e["stats"]["base_rate"]:
                    backed = True
        if not backed:
            out.append({"id": f"w:{w}", "kind": "word", "form": w, "value": "WORD", "method": "generated",
                        "by": f"lexeme_aligner.split_gen {VERSION} (own links do not back the cut)"})
    return out


def build_bundle(iso: str, tag: str, usj: Path, align: Path, out: Path, morph_file: Path | None = None, books=None,
                 family: str | None = None, protect: bool = True) -> dict:
    eng = _engine()
    mf = morph_file or MORPH / f"{iso}.json"
    tm = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    linked, vocab = linked_tokens(tag, usj, align, books)
    own = own_candidates(vocab)
    cands = {k: list(dict.fromkeys([_plain(a) for a in tm.get(k, [])] + own[k])) for k in ("prefixes", "suffixes")}
    tfile = (Path(family) if family and family.endswith(".json") else FAMILIES / f"{family}.json") if family else None
    template = json.loads(tfile.read_text(encoding="utf-8")) if family else None
    voted = None
    if template and template.get("vote"):
        from .split_vote import vote_template
        morphs, voted, vote_info = vote_template(linked, template)
        chain_info = {"blocks": 0}
    elif template:
        morphs, chain_info = ground_template(linked, template), {"blocks": 0}
    else:
        morphs, chain_info = chains(host_filter(ground(cands, linked, vocab)))
    tone, fig = eng.detect_tone(dict(vocab))
    orth = {"tone": {**tone, "derived": {"by": f"lexeme_aligner.split_gen {VERSION} (detect_tone)", "figures": fig}}}
    if tone.get("written") == "marks":
        orth["ignore_marks"] = tone.get("marks") or []
    facts = {"spec": eng.SPEC, "project": f"{iso}/{tag}", "date": date.today().isoformat(),
             "generator": {"name": "lexeme_aligner.split_gen", "version": VERSION, "engine": eng.VERSION,
                           "candidates": f"bcv-commons/target-morphology {iso}.json + the text's own top 150 starts/ends per side",
                           "thresholds": {"min_count": MIN_COUNT, "min_stems": MIN_STEMS, "min_lift": MIN_LIFT,
                                          "min_half_lift": MIN_HALF_LIFT, "min_stem_len": MIN_STEM_LEN,
                                          "min_share": MIN_SHARE, "min_affix_len": MIN_AFFIX_LEN},
                           "linked_tokens": len(linked), "chain_blocks_dropped": chain_info["blocks"]},
             "licence": {"morphs, orthography": "CC0", "vocab, classes": "CC0 (word counts and linked source classes of the edition, not its wording)",
                         "code": "MIT (split engine)"},
             "morphs": morphs, "words": [], "orthography": orth, "vocab": dict(vocab), "classes": classes_of(linked)}
    if template:
        facts["generator"]["family"] = {"template": family, "sha256": __import__("hashlib").sha256(
            tfile.read_bytes()).hexdigest()}
    if voted is not None:
        # the voted cuts are the word entries' parts; the affixes are kept as a record only, so the engine's own chain rules
        # cut no other word (they cut words the vote left whole: Kamano ana, Zinza aha, 2026-11-28)
        facts["words"] = voted
        facts["generator"]["vote"] = vote_info
        facts["generator"]["voted_affixes"] = facts.pop("morphs")
        facts["morphs"] = []
    elif protect:
        facts["words"] = protect_words(facts, linked)
        facts["generator"]["protected_words"] = len(facts["words"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "seg_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"family": family, "protected": len(facts["words"]), "morphs": len(morphs), "prefixes": sum(m["slot"] == "prefix" for m in morphs),
            "suffixes": sum(m["slot"] == "suffix" for m in morphs), "vocab": len(vocab), "linked": len(linked)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--usj", type=Path, required=True)
    ap.add_argument("--align", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--morph", type=Path, default=None, help="candidate file (default publish/target-morphology/<iso>.json)")
    ap.add_argument("--books", default=None)
    ap.add_argument("--family", default="auto", help="template in config/split_families (auto: by Glottolog family; none: generic)")
    ap.add_argument("--no-protect", action="store_true", help="no per-word protection")
    a = ap.parse_args(argv)
    books = set(a.books.split(",")) if a.books else None
    fam = family_of(a.iso) if a.family == "auto" else (None if a.family == "none" else a.family)
    print(json.dumps(build_bundle(a.iso, a.tag, a.usj, a.align, a.out, a.morph, books, fam, not a.no_protect)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
