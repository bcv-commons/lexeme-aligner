"""bcv-commons split engine: split a written word into parts (prefixes, stem, infixes, reduplication, tone, suffixes, clitics) from a
bcv-segfacts/0.1 bundle (seg_facts.json). Deterministic: the same bundle and word give the same parts, on any text.

    import bcv_segment; seg = bcv_segment.load("<bundle dir>"); seg.split("naáyaandika")
    python3 bcv_segment.py <bundle dir> <word> ...

Standalone, stdlib only, one file. A drop-in for the `bcv_segment.py` + `morphs.py` pair inside a bundle: same `load`, `split`, `.S`
(the Segmenter, e.g. `.S.split(word, tones=True)`), `.facts`, `.M`.

Origin: the engine (detect_tone, Morph, clusters, ToneSpec, ToneMorph, Segmenter) is taken verbatim from bcv-commons/make-tanakh
scripts/morphs.py at the tag named in ENGINE_FROM (MIT, (c) 2026 the make-tanakh authors), without the template's store tooling and
without its `grammar` / `register` imports (types only). Maintained by lexeme-aligner since 2026-11-28 (handover agreed by make-tanakh);
see README.md and CHANGELOG.md beside this file.
"""
import collections, json, os, re, sys, types, unicodedata
from typing import NamedTuple

VERSION = "1.0.0"
SPEC = "bcv-segfacts/0.1"
ENGINE_FROM = "bcv-commons/make-tanakh v0.14.0 scripts/morphs.py lines 107-976"

# ---- engine: verbatim from make-tanakh (see ENGINE_FROM) ----
CH = lambda m: chr(int(m[2:], 16)) if m.upper().startswith("U+") else m
REDUP = ("CV", "CVC", "full")   # a reduplication's patterns (slot redup)


# combining marks that orthographies use for tone (acute, grave, circumflex, caron, macron, double acute and grave, the
# vertical line above of Pe̍h-ōe-jī, the contour marks): the candidates. Every other mark (dot below, tilde, diaeresis,
# cedilla, ogonek, ring, horn, breve) is taken as part of its letter; a store can still name it in orthography.tone.
TONE_CANDIDATES = {chr(c) for c in (0x0300, 0x0301, 0x0302, 0x030C, 0x0304, 0x030B, 0x030F, 0x030D,
                                    0x1DC4, 0x1DC5, 0x1DC6, 0x1DC7, 0x1DC8, 0x1DC9)}


def detect_tone(tokens, min_mark=0.002, min_words=0.03, min_twins=0.15, max_top=0.6, min_digits=0.3, neutral=0.35,
                conflict=0.05, min_letter=0.02, min_double=0.01):
    """(proposal, figures) from {token: count} of the corpus as written.

    The tone marks are a layer: marks that never share a letter (one tone per vowel). Marks that do share one are in
    different layers: in a two-layer orthography the other layer is the vowel quality (Vietnamese â ă ơ under the
    tones: ấ; Yoruba's dot below: ọ́). The tone layer starts from the candidates (TONE_CANDIDATES) that occur in at least
    min_mark of the tokens; while two of them share letters (in more than `conflict` of the rarer one's uses), the one
    in most such pairs goes to the other layer. Where there is another layer, a frequent mark outside the candidates
    that never shares a letter with the tone layer but does with the other layer is a tone mark too (Vietnamese hook
    above, tilde, dot below).

    Tone is written with marks when the layer has at least two marks, the marked tokens are min_words of all, and
    min_twins of the marked word types have a twin: a word with the same letters and other tone marks or none (ambá
    beside amba, Yoruba ọkọ̀ ọkọ́ ọkọ; a stress or spelling accent seldom makes one: French 12%), and no letter carries
    more than max_top of the marks (tone falls on every vowel; French accents 79% on e). An orthography that marks one
    tone only (the high tone with an acute, as many Bantu languages do: Zinza Múungu) has a layer of one mark: it is tone
    when that mark stands twice in a word in at least min_double of the marked words (a stress accent stands once at most:
    Spanish, Italian, modern Greek), on min_words of all; the unmarked vowels then have the other tone (absent neutral).
    With digits when
    min_digits of the tokens end a syllable in 1-9 (Jyutping si1); else none. The tone-bearing letters are those of
    which at least min_letter of the occurrences carry a tone mark (and 10 in all); a missing mark is "neutral" (a tone
    of its own: Yoruba's and Vietnamese's level tone, Pinyin's neutral) when most of those letters carry one, else
    "unknown"."""
    N = sum(tokens.values()) or 1
    fig = {"tokens": N}
    dig = sum(n for w, n in tokens.items() if re.search(r"[^\W\d_][1-9](?![0-9])", w) and not w.isdigit())
    fig["digit_syllables"] = round(dig / N, 4)
    per, uses, pair = collections.Counter(), collections.Counter(), collections.Counter()
    for w, n in tokens.items():
        ms = set()
        for cl in clusters(w.lower()):
            here = sorted({m for m in cl[1:] if unicodedata.combining(m)})
            for m in here:
                ms.add(m)
                uses[m] += n
            for x in range(len(here)):
                for y in range(x + 1, len(here)):
                    pair[(here[x], here[y])] += n
        for m in ms:
            per[m] += n
    frequent = {m for m, n in per.items() if n >= min_mark * N}
    clash = lambda a, b: pair[tuple(sorted((a, b)))] > conflict * min(uses[a], uses[b])
    layer = {m for m in frequent if m in TONE_CANDIDATES}
    other = set()
    while True:
        bad = collections.Counter({m: sum(clash(m, o) for o in layer if o != m) for m in layer})
        if not bad or max(bad.values()) == 0:
            break
        m = max(bad, key=lambda k: (bad[k], -uses[k]))
        layer.discard(m)
        other.add(m)
    if other:
        other |= {m for m in frequent - layer - other if any(clash(m, t) for t in layer)}
        layer |= {m for m in frequent - layer - other if not any(clash(m, t) for t in layer) and any(clash(m, o) for o in other)}
    U = lambda ms: sorted(f"U+{ord(m):04X}" for m in ms)
    fig["marks"] = {f"U+{ord(m):04X}": round(per[m] / N, 4) for m in sorted(frequent, key=lambda k: -per[k])}
    if other:
        fig["other_layer"] = U(other)
    strip = lambda w: unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", w.lower()) if c not in layer))
    types = collections.Counter(unicodedata.normalize("NFC", w.lower()) for w in tokens)
    groups = collections.Counter(strip(w) for w in types)
    marked = [w for w in types if any(c in layer for c in unicodedata.normalize("NFD", w))]
    fig["marked_words"] = round(sum(n for w, n in tokens.items() if any(c in layer for c in unicodedata.normalize("NFD", w))) / N, 4)
    fig["twins"] = round(sum(groups[strip(w)] >= 2 for w in marked) / len(marked), 3) if marked else 0
    on = collections.Counter()
    for w, n in tokens.items():
        for cl in clusters(w.lower()):
            if any(m in layer for m in cl[1:]):
                on[cl[0]] += n
    fig["top_letter"] = round(max(on.values()) / sum(on.values()), 3) if on else 0
    if marked:
        dbl = sum(n for w, n in tokens.items() if sum(c in layer for c in unicodedata.normalize("NFD", w)) >= 2)
        fig["double"] = round(dbl / max(1, fig["marked_words"] * N), 3)
    single = (len(layer) == 1 and fig["marked_words"] >= min_words and fig.get("double", 0) >= min_double
              and fig["top_letter"] <= max_top)
    if single or (len(layer) >= 2 and fig["marked_words"] >= min_words and fig["twins"] >= min_twins
                  and fig["top_letter"] <= max_top):
        occ, tone_occ = collections.Counter(), collections.Counter()
        for w, n in tokens.items():
            for cl in clusters(w.lower()):
                occ[cl[0]] += n
                tone_occ[cl[0]] += n * any(m in layer for m in cl[1:])
        vowels = sorted(c for c, k in tone_occ.items() if c.isalpha() and k >= 10 and k >= min_letter * occ[c])
        units = sum(occ[c] for c in vowels)
        fig["marked_units"] = round(sum(tone_occ[c] for c in vowels) / units, 3) if units else 0
        return ({"written": "marks", "marks": U(layer), "vowels": "".join(vowels),
                 "absent": "neutral" if single or fig["marked_units"] >= neutral else "unknown"}, fig)
    if fig["digit_syllables"] >= min_digits:
        return {"written": "digits", "absent": "unknown"}, fig
    fig["why_none"] = ("no frequent tone mark" if not layer else
                       f"one mark, never twice in a word ({fig.get('double', 0):.1%}): a stress accent" if len(layer) == 1 else
                       f"marked words {fig['marked_words']:.1%} < {min_words:.0%}" if fig["marked_words"] < min_words else
                       f"twins {fig['twins']:.0%} < {min_twins:.0%}: the marks don't tell words apart"
                       if fig["twins"] < min_twins else
                       f"{fig['top_letter']:.0%} of the marks on one letter: a spelling accent, not tone")
    return {"written": "none"}, fig


class Morph(NamedTuple):
    slot: str       # prefix, infix, redup, stem, tone, suffix, clitic
    form: str       # the store's form (qo-, -eav, tone-proh) or, for the stem, its plain text
    text: str       # as written (NFC); for a tone morph the marks it took, as "pos:mark" items
    position: int = 0   # an affix of a chain: its position (the store's `position`), else 0


def clusters(w):
    """a word (NFD) as [base character + its combining marks and modifier letters (a length mark ꞉ belongs to its
    vowel)]"""
    out = []
    for c in unicodedata.normalize("NFD", w):
        if out and (unicodedata.combining(c) or unicodedata.category(c) in ("Lm", "Sk")):
            out[-1] += c
        else:
            out.append(c)
    return out


class ToneSpec:
    def __init__(self, orth):
        t = (orth or {}).get("tone") or {}
        self.written = t.get("written", "marks" if orth and orth.get("ignore_marks") else "none")
        self.marks = {CH(m) for m in (t.get("marks") or (orth or {}).get("ignore_marks") or [])}
        self.vowels = set(t.get("vowels") or "aeiou")
        self.absent = t.get("absent", "unknown")

    def units(self, cl):
        """the indexes of the clusters that carry tone: vowels (marks), or the digit ending each syllable (digits)"""
        if self.written == "marks":
            return [i for i, c in enumerate(cl) if c[0].lower() in self.vowels]
        if self.written == "digits":
            return [i for i, c in enumerate(cl) if c[0].isdigit()]
        return []

    def marks_at(self, cl, i):
        return {c for c in cl[i][1:] if c in self.marks} if self.written == "marks" else {cl[i][0]}


class ToneMorph:
    def __init__(self, e):
        self.form, self.value = e["form"], e.get("value")
        self.pattern = [(p["at"], CH(p["mark"])) for p in e["tone_pattern"]]
        # `with`: the prefixes the pattern belongs to (be-: grave on the first, acute on the last is its affirmative);
        # the pattern is read only on a word that begins with one of them (plain letters)
        w = e.get("with") or []
        self.with_ = [x.strip("-=").lower() for x in ([w] if isinstance(w, str) else w)]

    def match(self, spec, cl, plain=None):
        """[(cluster index, mark)] the pattern takes from the word, or None; plain: the word's plain letters (for `with`)"""
        if self.with_ and not (plain and any(plain.startswith(x) for x in self.with_)):
            return None
        u = spec.units(cl)
        took = []
        for at, mark in self.pattern:
            if at.startswith("<"):
                k = int(at[1:])
                cand = [i for i in u[:k] if mark in spec.marks_at(cl, i)] if len(u) >= -k else []
                if not cand:
                    return None
                took.append((cand[-1], mark))
            else:
                k = int(at)                       # negative: from the end; 0, 1, ...: from the start
                if (len(u) < -k if k < 0 else len(u) <= k) or mark not in spec.marks_at(cl, u[k]):
                    return None
                took.append((u[k], mark))
        return took


class Segmenter:
    def __init__(self, G, orth, vocab, classes=None):
        self.spec = ToneSpec(orth)
        self.ignore = {CH(m) for m in (orth or {}).get("ignore_marks") or []}
        # parts written with a hyphen (a morph's `written`: "fused", the default; "hyphen": only as a whole piece before
        # or after a hyphen; "both"): a word with an internal hyphen is one word with a boundary inside. A prefix written
        # so is the whole first piece (a bound person/TAM part before the stem: x-stem), a suffix the whole last piece
        # (a clitic: noun-postposition); what is between is segmented as a word. Grammatical tone is read on the whole
        # word, across the hyphen. A "hyphen" morph never comes off a fused word
        wr = lambda e: e.get("written") or "fused"
        # clitics (slot clitic): a part that attaches to a whole word, its side by its form (=ng, -ng after the word; ng=
        # before it); written with a hyphen they are hyphen parts as below, fused they come off in split_clitic
        side = lambda e: (("suffix" if e.get("form", "")[:1] in ("-", "=") else "prefix" if e.get("form", "")[-1:] in ("-", "=")
                           else None) if e.get("slot") == "clitic" else e.get("slot"))
        self.hy_pre = {self.plain(e["form"].strip("-=")): e["form"] for e in G.morphs
                       if side(e) == "prefix" and wr(e) in ("hyphen", "both") and e.get("form", "").strip("-=")}
        self.hy_suf = {self.plain(e["form"].strip("-=")): e["form"] for e in G.morphs
                       if side(e) == "suffix" and wr(e) in ("hyphen", "both") and e.get("form", "").strip("-=")}
        self.hy_clitic = {e["form"] for e in G.morphs if e.get("slot") == "clitic" and e.get("form")}
        cl_ = lambda sd: sorted({(self.plain(e["form"].strip("-=")), e["form"]) for e in G.morphs if side(e) == sd
                                 and e.get("slot") == "clitic" and wr(e) in ("fused", "both") and e["form"].strip("-=")},
                                key=lambda x: -len(x[0]))
        self.encl, self.procl = cl_("suffix"), cl_("prefix")
        self.hyphen = bool(self.hy_pre or self.hy_suf)
        if any(wr(e) == "hyphen" for e in G.morphs):
            G = types.SimpleNamespace(morphs=[e for e in G.morphs if wr(e) != "hyphen"], words=getattr(G, "words", []))
        aff = lambda slot, host=None, site=None: sorted(
            {self.plain(e["form"].strip("-=")) for e in G.morphs if e.get("slot") == slot and e.get("form")
             and (host is None or e.get("host") == host) and (site is None or (e.get("site") or "after_prefix") == site)}
            - {""}, key=len, reverse=True)
        self.pre, self.inf, self.suf = aff("prefix"), aff("infix", site="after_prefix"), aff("suffix")
        # the stem's own morphology (scripts/morphs.py's docstring, *Infixes and reduplication*): an infix with `site:
        # after_onset` (s-um-ulat, um-alis) and the reduplicated syllable (slot redup, `pattern` CV | CVC | full: su-sulat),
        # in the stem left by the prefixes and suffixes; vowels: orthography.tone.vowels (default aeiou)
        self.inf_onset = aff("infix", site="after_onset")
        self.redup = sorted({(e["pattern"], e.get("form") or f"redup:{e['pattern']}") for e in G.morphs
                             if e.get("slot") == "redup" and e.get("pattern") in REDUP}, key=lambda x: REDUP.index(x[0]))
        self.stem_on = bool(self.inf_onset or self.redup)
        self.stem_hosts = collections.defaultdict(set)   # a stem morph's label -> its hosts
        for e in G.morphs:
            if e.get("slot") == "infix" and e.get("site") == "after_onset" and e.get("form", "").strip("-="):
                self.stem_hosts["-" + self.plain(e["form"].strip("-=")) + "-"].add(e.get("host") or "any")
            elif e.get("slot") == "redup" and e.get("pattern") in REDUP:
                self.stem_hosts[e.get("form") or f"redup:{e['pattern']}"].add(e.get("host") or "any")
        # affix chains (a Bantu verb: ni-tu-ka-mu-sohoz-a): morph entries with a `position`, prefixes counted from the
        # word's start, suffixes from the stem outward; several of them per word, each position once, in order
        self.chain = any(e.get("position") for e in G.morphs if e.get("slot") in ("prefix", "suffix"))
        # affixes told apart by tone alone (aá- past beside aa- future; bē- sequential beside be-): forms with an
        # ignored mark, matched on their plain letters, labelled by the mark as written: {(slot, position, plain):
        # {written: store form}}; an affix without a position is at 0
        self.tone_forms = collections.defaultdict(dict)
        for e in G.morphs:
            fm = unicodedata.normalize("NFC", (e.get("form") or "").lower())
            if e.get("slot") in ("prefix", "suffix") and fm.strip("-="):
                self.tone_forms[(e["slot"], int(e.get("position") or 0), self.plain(fm.strip("-=")))][fm.strip("-=")] = fm
        self.tone_forms = {k: v for k, v in self.tone_forms.items() if len(v) > 1 or any(w != k[2] for w in v)}
        # vowel harmony (-ge written -gi after i): an affix entry's `variants`, [{"written": "-gi", "after": "i"}]: the
        # written form, after a part ending in one of `after`'s letters, is this morph. A written form that is an affix of
        # its own keeps that reading; where the two clash the store settles it word by word (a word entry's `parts`)
        self.variants = collections.defaultdict(list)    # (slot, plain written) -> [(after letters, store form)]
        for e in G.morphs:
            for v in e.get("variants") or []:
                wf = self.plain(v.get("written", "").strip("-="))
                if wf and e.get("slot") in ("prefix", "suffix"):
                    self.variants[(e["slot"], wf)].append((self.plain(v.get("after") or ""), e["form"]))
        if self.chain:
            def by_pos(slot, hosts=None):
                out = collections.defaultdict(set)
                for e in G.morphs:
                    # the host filter is the chain's: an affix without a position is not part of a chain and comes
                    # off any word, as in a store without positions (a position switched on for two prefixes must not
                    # stop the store's other affixes)
                    if (e.get("slot") == slot and e.get("form", "").strip("-=")
                            and (hosts is None or not e.get("position") or (e.get("host") or "any") in hosts)):
                        out[int(e.get("position") or 1)].add(self.plain(e["form"].strip("-=")))
                return {p: sorted(v, key=len, reverse=True) for p, v in sorted(out.items())}
            self.pre_at, self.suf_at = by_pos("prefix"), by_pos("suffix")
            # (slot, position, plain form) of the affixes the store gives a position: the others are parsed at
            # position 1 and reported without one (position 0, as the store has them)
            self.positioned = {(e["slot"], int(e["position"]), self.plain(e["form"].strip("-=")))
                               for e in G.morphs if e.get("position") and e.get("slot") in ("prefix", "suffix")
                               and (e.get("form") or "").strip("-=")}
            self.chain_hosts = {e.get("host") or "any" for e in G.morphs if e.get("position")} - {"any"}
            self.at_for = {}                              # hosts -> (prefixes, suffixes) by position
            for hs in [frozenset({"any", h}) for h in self.chain_hosts] + [frozenset({"any"})]:
                self.at_for[hs] = (by_pos("prefix", hs), by_pos("suffix", hs))
        hosts = {e.get("host") for e in G.morphs if e.get("slot") in ("prefix", "suffix")}
        # words never split: free morphs (a pronoun, a conjunction) and the store's protected words (kind word: a name,
        # a word that only begins like a prefix: Danish uge 'week', not u-ge); matched on plain lowercase letters
        self.free = {self.plain(e["form"]) for e in G.morphs if e.get("slot") == "free" and e.get("form")}
        self.protected = {self.plain(e["form"]) for e in getattr(G, "words", []) if e.get("form")}
        # a word entry's `parts` (an analysis the store settles by hand: nigi = ni- + -ge written gi, by vowel
        # harmony, not ni- + -gi): [{"form": "ni-", "text": "ni"}, {"form": "-ge", "text": "gi"}]; the texts, joined, are
        # the word's plain letters; a part whose form is a stem's has no hyphen (or is "" for none)
        self.analysed = {}
        for e in getattr(G, "words", []):
            ps = e.get("parts")
            if ps and e.get("form") and "".join(self.plain(x.get("text", "")) for x in ps) == self.plain(e["form"]):
                self.analysed[self.plain(e["form"])] = [(x.get("form", ""), self.plain(x.get("text", ""))) for x in ps]
        self.free |= self.protected
        self.pairs_by_host = {h: {(p, q) for p in aff("prefix", h) for q in aff("suffix", h)} for h in hosts}
        self.tones = [ToneMorph(e) for e in G.morphs if e.get("slot") == "tone" and e.get("tone_pattern")]
        if self.spec.written in ("letters", "none"):
            self.tones = []
        self.vocab = collections.Counter()
        for w, n in (vocab.items() if isinstance(vocab, dict) else ((w, 1) for w in vocab)):
            # with hyphen parts, a piece after a hyphen (a tokeniser's "-x") is the rest of a word: evidence for its stem
            self.vocab[self.plain(w[1:] if self.hyphen and w.startswith("-") and len(w) > 1 else w)] += n
        self.forms = collections.defaultdict(collections.Counter)  # a possible stem -> {(prefix, suffix): tokens}
        self.min_stem = 2 if self.chain else 3
        for w, n in self.vocab.items():
            if w in self.protected:                       # a protected word is no evidence for a split either
                continue
            if self.chain:                                # (prefix chain, suffix chain) as tuples of forms
                for pc, lp in self.chains(w, "p"):
                    for sc, ls in self.chains(w, "s"):
                        if len(w) - lp - ls >= self.min_stem:
                            self.forms[w[lp:len(w) - ls]][(pc, sc)] += n
                continue
            for p in [""] + [p for p in self.pre if w.startswith(p)]:
                for q in [""] + [q for q in self.suf if w.endswith(q)]:
                    if len(w) - len(p) - len(q) >= 3:
                        self.forms[w[len(p):len(w) - len(q)]][(p, q)] += n
        # the roots the stem's morphology leaves, with what took them: {root: Counter((infix, redup): tokens)}; a piece
        # comes off a stem where its root is attested: a stem of its own (forms), or with another infix or copy
        self.roots = collections.defaultdict(collections.Counter)
        if self.inf_onset or self.redup:
            for t, F in self.forms.items():
                for root, _, key in self.stem_cands(t):
                    self.roots[root][key] += sum(F.values())
        # a word that is a prefix and a suffix of one host and nothing else (no-qa "my (thing)": person + class), for a
        # host where most such pairs are words of the corpus (a paradigm without stems), not where one happens to be
        # (t-at): measured, not assumed
        self.pairs = set()
        for h, ps in self.pairs_by_host.items():
            if ps and sum(p + q in self.vocab for p, q in ps) >= 0.5 * len(ps):
                self.pairs |= ps
        self.min_share, self.min_productive = 0.03, 0.4
        # a productive affix: most corpus words that begin (end) with it are attested splits (two-letter qo-, ge-);
        # it is taken off any word that leaves a stem of 3 letters or more. The others (one letter: n-, t-, z-; -av)
        # only where the stem is attested
        self.productive = set()
        for side, affs in (("p", self.pre), ("s", self.suf)):
            for a in affs:
                ws = [w for w in self.vocab if (w.startswith(a) if side == "p" else w.endswith(a))
                      and len(w) - len(a) >= 3 and w not in self.free]
                if self.chain:                            # the affix alone, at any of its positions
                    at_ = self.pre_at if side == "p" else self.suf_at
                    ones, none = [((p, a),) for p, fs in at_.items() if a in fs], ()
                else:
                    ones, none = [a], ""
                ok = sum(1 for w in ws if any(self.attested(w[len(a):], o, none, self.vocab[w]) for o in ones) if side == "p")
                ok += sum(1 for w in ws if any(self.attested(w[:-len(a)], none, o, self.vocab[w]) for o in ones) if side == "s")
                if len(ws) >= 10 and ok >= self.min_productive * len(ws):
                    self.productive.add(a + "-" if side == "p" else "-" + a)
        # and the affixes `try` found productive by their Greek feature (`productive: true`): taken off every word,
        # they still mark it (a negation suffix on verbs whose stems seldom occur in another form)
        for e in G.morphs:
            fm = self.plain(unicodedata.normalize("NFC", (e.get("form") or "").lower()).strip("-="))
            if e.get("productive") is True and fm and e.get("slot") in ("prefix", "suffix"):
                self.productive.add(fm + "-" if e["slot"] == "prefix" else "-" + fm)
        # host-aware chains: a word's classes from the NT aligned to the Greek (classes: {word: Counter(Greek class:
        # tokens linked to one, and "" all its linked tokens)}): each class linked in at least half its tokens (2 or
        # more), so a verb that also renders its subject's article (bakagaamba: οἱ ... εἶπον) is still a verb. A chain's
        # affixes of a host (verb) come off only that class's words, and off a word the NT doesn't link where what they
        # leave is the stem of such a word (stems_of)
        self.word_class, self.stems_of, self.host_roots_ = {}, {}, {}
        if (self.chain or self.stem_on) and classes:
            acc = collections.defaultdict(collections.Counter)
            for w, c in classes.items():
                acc[self.plain(w)].update(c)
            for w, c in acc.items():
                if c[""] >= 2:
                    self.word_class[w] = frozenset(k for k, n in c.items() if k and n >= 0.5 * c[""])
        self.cache = {}

    def copy_of(self, pattern, r):
        """the copy a reduplication pattern makes of the root r (plain): CV its first consonant and vowel (a vowel-initial
        root: the vowel), CVC with the consonant after, full the whole root; None without a vowel"""
        c = next((k for k, ch in enumerate(r) if ch in self.spec.vowels), None)
        if c is None:
            return None
        if pattern == "full":
            return r
        cv = (r[0] if c else "") + r[c]
        return cv + r[c + 1] if pattern == "CVC" and c + 1 < len(r) and r[c + 1] not in self.spec.vowels else cv

    def stem_cands(self, t):
        """the plain stem t as an infix after its onset (its consonants before the first vowel) and/or a copy before its
        root: [(root, pieces, (infix, redup label))], pieces [(slot, form, a, b)] in t's letters, the root's own of slot
        stem and form the root (two pieces where the infix cuts it: s / -um- / ulat; the copy likewise: s / -um- / u)"""
        on = next((k for k, c in enumerate(t) if c in self.spec.vowels), len(t))
        out = []
        for f in [None] + [f for f in self.inf_onset if t.startswith(f, on)]:
            u = t if f is None else t[:on] + t[on + len(f):]
            if f is not None and not (on < len(u) and u[on] in self.spec.vowels):
                continue                                 # the infix stands before the stem's first vowel (not in-yong)
            for pat, lab in [(None, None)] + self.redup:
                if f is None and pat is None:
                    continue
                k = 0 if pat is None else next((k for k in range(1, len(u)) if self.copy_of(pat, u[k:]) == u[:k]), None)
                if k is None or len(u) - k < self.min_stem:
                    continue
                root = u[k:]
                ps = ([("redup", lab, 0, k)] if k else []) + [("stem", root, k, len(u))]
                if f is not None:                        # the infix goes in at the onset, cutting the piece there
                    n, cut = len(f), []
                    for sl, fm, a, b in ps:
                        if b <= on:
                            cut.append((sl, fm, a, b))
                        elif a >= on:
                            cut.append((sl, fm, a + n, b + n))
                        else:
                            cut += [(sl, fm, a, on), (sl, fm, on + n, b + n)]
                    ps = sorted(cut + [("infix", "-" + f + "-", on, on + n)], key=lambda x: (x[2], x[3]))
                out.append((root, ps, (f, lab)))
        return out

    def cells(self, x, own=None, key=None):
        """in how many other forms the plain stem or root x is attested: its prefix/suffix pairs (forms) and its infix/copy
        pairs (roots), its own (p, q) and (infix, redup) left out"""
        return len([k for k in self.forms.get(x, {}) if k != own]) + len([k for k in self.roots.get(x, {}) if k != key])

    def stem_best(self, t, s, cut_ok=lambda a: True):
        """the best analysis of the plain stem t (of the word s) by its infix after the onset and its copy, or None:
        (tier, letters off, morphs, cells, evidence, pieces). Only where the root is attested (a stem of its own, or with
        another infix or copy) and the morphs' host fits the word (host_ok). A root spelled like a free word (tayo "we" in
        t-um-ayo) is attested only by an infix or copy on it in other words, not by its own uses, and is tier 0: taken only
        where the word has no other reading (not k-in-a-in beside k-in-ain); cut_ok(a): a boundary a letters into t may be
        cut"""
        n, best = self.vocab[s], None
        for root, ps, key in self.stem_cands(t):
            if not all(cut_ok(a) for _, _, a, _ in ps):
                continue
            other = sum(v for k, v in self.roots[root].items() if k != key)
            if root in self.free:                        # its own uses ("we") are no evidence for a root: only an infix
                ev = other                               # or copy on it in other words (t-um-ayo beside ta-tayo)
            else:
                ev = sum(self.forms.get(root, {}).values()) + other
            labs = {fm for sl, fm, _, _ in ps if sl != "stem"}
            if not ev or ev < self.min_share * n or not self.host_ok(s, root, labs):
                continue
            r = (int(root not in self.free), len(t) - len(root), len(labs), self.cells(root, key=key), ev, ps)
            if best is None or (r[0], r[1], -r[2], r[3], r[4]) > (best[0], best[1], -best[2], best[3], best[4]):
                best = r
        return best

    def host_ok(self, s, root, labs):
        """may the stem morphs labs come off the plain word s? Where the NT is aligned to the Greek, only off words linked
        to their host (verb) in at least half their tokens, and off a word it doesn't link where the root is the root
        of such a word; without it (or for host any), off every word"""
        hs = set().union(*(self.stem_hosts.get(x, {"any"}) for x in labs)) if labs else {"any"}
        if not self.word_class or "any" in hs:
            return True
        k = self.word_class.get(s)
        if k:
            return bool(k & hs)
        hs = frozenset(hs)
        if hs not in self.host_roots_:
            rs = set()
            for w, c in self.word_class.items():
                if c & hs:
                    for p in [""] + [p for p in self.pre if w.startswith(p)]:
                        for q in [""] + [q for q in self.suf if w.endswith(q) and len(w) > len(p) + len(q)]:
                            core = w[len(p):len(w) - len(q)]
                            rs |= {core} | {r for r, _, _ in self.stem_cands(core)}
            self.host_roots_[hs] = rs
        return root in self.host_roots_[hs]

    def stem_morphs(self, s, i, j, idx, at, cut, pieces):
        """[Morph] of the stem s[idx[i]:idx[j]] (plain): its pieces [(slot, form, a, b)] (stem_best), or whole (None)"""
        t = s[idx[i]:idx[j]]
        if not pieces:
            return [Morph("stem", t, cut(i, j))]
        return [Morph(sl, fm, cut(at(idx[i] + a), at(idx[i] + b) if b < len(t) else j)) for sl, fm, a, b in pieces]

    def tone_label(self, slot, pos, plain, text):
        """the store form of a chain affix: where the store tells its forms apart by tone (aá- beside aa-), the one
        written so (marks as in the text), the unmarked one where the text has no mark, else the form as written"""
        v = self.tone_forms.get((slot, pos, plain))
        dash = (lambda x: x + "-") if slot == "prefix" else (lambda x: "-" + x)
        if not v:
            return dash(plain)
        t = unicodedata.normalize("NFC", text.lower())
        if t in v:
            return v[t]
        return v.get(plain) or dash(plain) if t == plain else dash(t)

    def hosts_of(self, s):
        """the hosts whose chain affixes may come off the plain word s (None: all, no word classes known)"""
        if not self.word_class:
            return None
        k = self.word_class.get(s)
        if k:                                            # no class in half its tokens: as a word the NT doesn't link
            hs = k & self.chain_hosts
            return frozenset({"any", min(hs)}) if hs else frozenset({"any"})   # one host's chain (verb before noun)
        for h in sorted(self.chain_hosts):
            if h not in self.stems_of:
                self.stems_of[h] = {r[0] for w, c in self.word_class.items() if h in c
                                    for r in [self.chain_parse(w, frozenset({"any", h}))] if r}
            r = self.chain_parse(s, frozenset({"any", h}))
            if r and r[0] in self.stems_of[h]:
                return frozenset({"any", h})
        return frozenset({"any"})

    def chain_parse(self, s, hosts=None, cut_ok=lambda n: True, fit=lambda pc, sc: 0):
        """(stem, prefix chain, suffix chain) of the plain word s, or None: of the chains that leave an attested stem
        (or are all productive), the one that takes off most letters in the fewest pieces (ba-, not b-a-), then the one
        whose affixes are written as the store writes them (fit: b-aá-, not ba-á-, where aá- is a form and á- is not),
        then the one with more of the chain's own (positioned) affixes: where a form is both a chain affix of this host and
        an affix without a position (an infinitive oku- at 2 beside a class-15 noun prefix oku-), the chain's reading wins
        on the words of its host; then the stem with most other forms. cut_ok(n): a boundary n letters from the start may
        be cut"""
        at = self.at_for.get(hosts) if hosts is not None else None
        best = None
        for pc, lp in self.chains(s, "p", cut_ok, at):
            for sc, ls in self.chains(s, "s", lambda n: cut_ok(len(s) - n), at):
                if not (pc or sc or self.stem_on) or len(s) - lp - ls < self.min_stem:
                    continue
                core = s[lp:len(s) - ls]
                ev = (pc or sc) and (self.attested(core, pc, sc, self.vocab[s]) or (
                    len(core) >= 2 and all(a + "-" in self.productive for _, a in pc)
                    and all("-" + a in self.productive for _, a in sc)))
                own = sum(("prefix", p, a) in self.positioned for p, a in pc) + sum(
                    ("suffix", p, a) in self.positioned for p, a in sc)
                if not self.stem_on:
                    if ev:
                        key = (lp + ls, -len(pc) - len(sc), fit(pc, sc), own, ev)
                        if best is None or key > best[0]:
                            best = (key, core, pc, sc, None)
                    continue
                # with the stem's own morphology: the chains and the stem's infix and copy chosen together (k-in-ain,
                # not kina-in), the root's evidence enough for the chains around it (nag-sa-sabi); then the stem or root
                # attested in more forms
                own_k = (tuple(f for _, f in pc), tuple(f for _, f in sc))
                opts = [(1, 0, 0, self.cells(core, own_k), ev, None)] if ev else []
                sb = self.stem_best(core, s, lambda a: cut_ok(lp + a))
                if sb:
                    opts.append(sb)
                for tier, off, nm, cel, e, ps in opts:
                    key = (tier, lp + ls + off, -len(pc) - len(sc) - nm, cel, fit(pc, sc), own, e)
                    if best is None or key > best[0]:
                        best = (key, core, pc, sc, ps)
        return best[1:] if best else None

    def plain(self, w):
        return unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", w.lower())
                                                    if c not in self.ignore))

    def chains(self, s, side, ok=lambda n: True, at_=None):
        """[(chain of (position, form), length)] of every affix chain that fits the start (side p) or the end (side s) of the plain
        string s, the empty one included; positions rise from the word's start (prefixes) or from the stem outward
        (suffixes: the outermost is taken first, so from the end they fall). ok(n): a boundary n letters from that edge
        may be cut (a cluster boundary)"""
        at = (at_[0] if side == "p" else at_[1]) if at_ else (self.pre_at if side == "p" else self.suf_at)
        out = []

        def walk(i, last, ch):
            out.append((tuple(ch), i))
            for p in (sorted(at) if side == "p" else sorted(at, reverse=True)):
                if (side == "p" and p <= last) or (side == "s" and p >= last):
                    continue
                for f in at[p]:
                    hit = s.startswith(f, i) if side == "p" else s[:len(s) - i].endswith(f)
                    if hit and len(s) - i - len(f) >= self.min_stem and ok(i + len(f)):
                        walk(i + len(f), p, ch + [(p, f)])
        walk(0, 0 if side == "p" else 10 ** 6, [])
        return out

    def attested(self, stem, p, q, n):
        """a paradigm: an affix is taken off only where the stem also occurs with another one in its place (or none):
        ge-nanim-ák beside qo-nanim, nanim-im; a stem that is a word of its own is not enough (n-amba), nor the same
        affix on another word (n-dap beside n-dap-eáv); and the stem's other forms are not rare beside this one (a
        frequent word that merely looks affixed: n-øme, 2,343 times, beside 41: under min_share). Returns the stem's
        other forms' tokens (the evidence), 0 when not attested. For a chain, each of its affixes: tu-ka-batiz-ibw-a,
        not tu-ka-ba-tiz-, as every form of tiz has ba- before it"""
        F = self.forms.get(stem, {})
        others = {k: v for k, v in F.items() if k != (p, q)}
        if self.chain:                                   # p, q chains: each affix missing or replaced in another form
            # by letters, not parses: an affix comes off where another form of the stem lacks the letters from it to the
            # stem (any parse: ku-b-a-tiz has the letters of ba- before tiz, so ba- stays in batiz)
            pre = lambda ch: "".join(f for _, f in ch)                 # prefixes from the word's start
            suf = lambda ch: "".join(f for _, f in reversed(ch))       # suffixes from the stem outward
            ok = (len(stem) >= self.min_stem
                  and all(any(not pre(k[0]).endswith(pre(p[i:])) for k in others) for i in range(len(p)))
                  and all(any(not suf(k[1]).startswith(suf(q[i:])) for k in others) for i in range(len(q)))
                  and sum(others.values()) >= self.min_share * n)
        else:
            ok = (len(stem) >= self.min_stem and (not p or any(k[0] != p for k in others))
                  and (not q or any(k[1] != q for k in others)) and sum(others.values()) >= self.min_share * n)
        return sum(others.values()) if ok else 0

    def split(self, w, tones=True):
        """[Morph] of an exact token, in the order prefix, infix, stem, tone, suffix (a word with hyphen parts: the
        hyphen prefix first, the hyphen suffixes last). tones=False: no grammatical tone is read (a word's inside)"""
        key = w if tones else ("\0", w)
        if key not in self.cache:
            r = self.split_analysed(w, tones)
            r = r or (self.split_hyphen(w, tones) if self.hyphen and "-" in w.strip("-") else None)
            r = r or (self.split_clitic(w, tones) if self.encl or self.procl else None)
            self.cache[key] = r or self.split_plain(w, self.tones if tones else ())
        return self.cache[key]

    def split_clitic(self, w, tones=True):
        """a word with a fused clitic (slot clitic): the word it attaches to, split as any word, and the clitic, or None.
        A clitic attaches to a whole word, so it comes off only where what remains is a word of the corpus, not rare
        beside this one (dati=ng, not dumati=ng: dumati is no word). Several stack, each leaving a word. Grammatical
        tone is read on the word before an enclitic, not after a proclitic"""
        cl = clusters(w.lower())
        pl = [self.plain(c) for c in cl]
        s = "".join(pl)
        if s in self.free or "-" in s.strip("-"):
            return None
        idx = [0]
        for p in pl:
            idx.append(idx[-1] + len(p))
        cut = lambda a, b: unicodedata.normalize("NFC", "".join(cl[a:b]))
        n = self.vocab[s]
        word = lambda r: len(r) >= 2 and r not in self.protected and self.vocab[r] and self.vocab[r] >= self.min_share * n
        for c, form in self.encl:
            if s.endswith(c) and len(s) - len(c) in idx[1:-1] and word(s[:len(s) - len(c)]):
                k = idx.index(len(s) - len(c))
                return self.split(cut(0, k), tones) + [Morph("clitic", form, cut(k, len(cl)))]
        for c, form in self.procl:
            if s.startswith(c) and len(c) in idx[1:-1] and word(s[len(c):]):
                k = idx.index(len(c))
                return [Morph("clitic", form, cut(0, k))] + self.split(cut(k, len(cl)), tones=False)
        return None

    def split_analysed(self, w, tones=True):
        """a word the store analyses by hand (a word entry's `parts`): its parts cut from the written word, or None"""
        ps = self.analysed.get(self.plain(w))
        if not ps:
            return None
        cl = clusters(w.lower())
        tone = self.read_tone(cl, self.tones) if tones else None
        if tone:
            cl = list(cl)
            for i, m in tone[1]:
                cl[i] = cl[i].replace(m, "", 1)
        idx = [0]
        for c in cl:
            idx.append(idx[-1] + len(self.plain(c)))
        out, i, stem = [], 0, False
        for form, text in ps:
            if idx[i] + len(text) not in idx:
                return None                              # a part ends inside a letter's marks
            k = idx.index(idx[i] + len(text))
            t = unicodedata.normalize("NFC", "".join(cl[i:k]))
            slot = "prefix" if form.endswith("-") and not form.startswith("-") else "suffix" if form.startswith("-") and not form.endswith("-") \
                else "infix" if form.startswith("-") else "redup" if form.startswith("redup:") else "stem"
            if slot == "suffix" and not stem:
                out.append(Morph("stem", "", "")); stem = True
                if tone:
                    out.append(Morph("tone", tone[0].form, " ".join(f"{a}:U+{ord(m):04X}" for a, m in tone[1])))
            if slot == "stem":
                out.append(Morph("stem", self.plain(t), t)); stem = True
                if tone:
                    out.append(Morph("tone", tone[0].form, " ".join(f"{a}:U+{ord(m):04X}" for a, m in tone[1])))
            else:
                out.append(Morph(slot, form, t))
            i = k
        if not stem:
            out.append(Morph("stem", "", ""))
            if tone:
                out.append(Morph("tone", tone[0].form, " ".join(f"{a}:U+{ord(m):04X}" for a, m in tone[1])))
        return out if i == len(cl) else None

    def prefix_cover(self, f):
        """the store prefixes that make up the whole plain piece f before a hyphen, in order (fewest pieces), or None:
        a hyphen has no grammar of its own, so ge-mbo- before it is two prefixes, as fused"""
        forms = set(self.hy_pre) | set(self.pre)
        best = {0: []}
        for i in range(len(f)):
            if i not in best:
                continue
            for p in forms:
                if p and f.startswith(p, i) and (i + len(p) not in best or len(best[i]) + 1 < len(best[i + len(p)])):
                    best[i + len(p)] = best[i] + [p]
        return best.get(len(f))

    def read_tone(self, cl, tones):
        pl = self.plain("".join(cl))
        for t in tones:
            took = t.match(self.spec, cl, pl)
            if took:
                return t, took
        return None

    def split_hyphen(self, w, tones=True, whole=False):
        """a word with an internal hyphen and a store part written with one: [hyphen prefix] + its inside segmented +
        [tone] + [hyphen suffixes], or None (the word is segmented as any other). whole: the inside is not segmented (a
        name: Zelani-ko is the name + a clitic)"""
        cl = clusters(w.lower())
        if self.plain("".join(cl)) in self.free:
            return None
        hy = [i for i, c in enumerate(cl) if c == "-" and 0 < i < len(cl) - 1 and cl[i - 1] != "-"]
        if hy and self.plain("".join(cl[:hy[0]])) in self.free:
            return None                                  # a protected word before the hyphen (zita-te): no prefix off it
        if not hy:
            return None
        piece = lambda a, b: self.plain("".join(cl[a:b]))
        a, b, pre, post = 0, len(cl), [], []
        f = piece(0, hy[0])
        cover = None
        if f in self.hy_pre:
            a, hy = hy[0] + 1, hy[1:]
        elif f not in self.free and f and (cover := self.prefix_cover(f)) and len(cover) > 1 and cover[-1] in self.hy_pre:
            a, hy = hy[0] + 1, hy[1:]                    # a prefix chain before the hyphen, its last part one written so
        while hy and piece(hy[-1] + 1, b) in self.hy_suf:
            post.insert(0, (hy[-1], b, self.hy_suf[piece(hy[-1] + 1, b)]))
            b = hy[-1]
            hy = hy[:-1]
        if not a and not post:
            return None
        tone = self.read_tone(cl, self.tones) if tones else None
        if tone:
            cl = list(cl)
            for i, m in tone[1]:
                cl[i] = cl[i].replace(m, "", 1)
        cut = lambda x, y: unicodedata.normalize("NFC", "".join(cl[x:y]))
        if a and cover and len(cover) > 1:
            out, x = [], 0
            for p in cover:                              # each prefix's clusters: its plain letters from x
                y = x
                while y < a - 1 and len(self.plain("".join(cl[x:y]))) < len(p):
                    y += 1
                last = p == cover[-1] and y >= a - 1
                t = cut(x, a if last else y)
                fm = self.tone_label("prefix", 0, p, t.rstrip("-")) if p not in self.hy_pre or last else self.hy_pre[p]
                out.append(Morph("clitic" if fm in self.hy_clitic else "prefix", fm, t))
                x = y
        else:
            fm = self.tone_label("prefix", 0, f, cut(0, a).rstrip("-")) if (("prefix", 0, f) in self.tone_forms) \
                else self.hy_pre.get(f)
            out = [Morph("clitic" if fm in self.hy_clitic else "prefix", fm, cut(0, a))] if a else []
        inner = ([Morph("stem", self.plain(cut(a, b)), cut(a, b))] if whole else self.split(cut(a, b), tones=False)) if b > a else []
        k = next((n for n, m in enumerate(inner) if m.slot == "stem"), len(inner)) + 1
        out += inner[:k] + ([Morph("tone", tone[0].form, " ".join(f"{i}:U+{ord(m):04X}" for i, m in tone[1]))]
                           if tone else []) + inner[k:]
        out += [Morph("clitic" if form in self.hy_clitic else "suffix", form, cut(x, y)) for x, y, form in post]
        return out

    def split_plain(self, w, tone_morphs=()):
        """split's work, uncached; tone_morphs: the grammatical tone patterns to read"""
        cl = clusters(w.lower())
        tone = None
        for t in tone_morphs:
            took = t.match(self.spec, cl, self.plain("".join(cl)))
            if took:
                tone = (t, took)
                break
        if tone:                                         # the grammatical marks out of the word
            cl = list(cl)
            for i, m in tone[1]:
                cl[i] = cl[i].replace(m, "", 1)
        pl = [self.plain(c) for c in cl]                 # plain text per cluster (ignored marks off)
        s = "".join(pl)
        cut = lambda a, b: unicodedata.normalize("NFC", "".join(cl[a:b]))
        # the cluster index where the plain text reaches n characters
        idx = [0]
        for p in pl:
            idx.append(idx[-1] + len(p))
        at = lambda n: idx.index(n) if n in idx else None
        out, i, j = [], 0, len(cl)
        pieces, redo = None, False                       # the stem's pieces (stem_best); redo: analyse what an infix left
        for p, q in (self.pairs if s not in self.free else ()):
            if s == p + q and at(len(p)):
                k = at(len(p))
                return [Morph("prefix", p + "-", cut(0, k)), Morph("stem", "", "")] + (
                    [Morph("tone", tone[0].form, " ".join(f"{a}:U+{ord(m):04X}" for a, m in tone[1]))] if tone else []) + [
                    Morph("suffix", "-" + q, cut(k, j))]
        blocked = s in self.free or ("-" in s.strip("-") and any(x in self.free for x in s.split("-")))
        if self.chain and not blocked and not w.startswith("-"):
            def fit(pc, sc):                             # +1 an affix written with a store form's mark, -1 one written
                score, a, b = 0, 0, len(cl)              # with a mark no store form of it has
                for slot, ch_ in (("prefix", pc), ("suffix", sc)):
                    for p, f in ch_:
                        if slot == "prefix":
                            k = at(idx[a] + len(f)); t = cut(a, k); a = k
                        else:
                            k = at(idx[b] - len(f)); t = cut(k, b); b = k
                        t = unicodedata.normalize("NFC", t.lower())
                        if t != f:
                            score += 1 if t in self.tone_forms.get((slot, p, f), {}) else -1
                return score
            best = self.chain_parse(s, self.hosts_of(s), lambda n: at(n) is not None, fit)
            tail = []
            if best:
                _, pc, sc, pieces = best
                for p, a in pc:
                    k = at(idx[i] + len(a))
                    out.append(Morph("prefix", self.tone_label("prefix", p, a, cut(i, k)), cut(i, k),
                                     p if ("prefix", p, a) in self.positioned else 0))
                    i = k
                if pc and self.inf:                      # an infix after the prefixes, as without a chain
                    r = s[idx[i]:len(s) - sum(len(a) for _, a in sc)]
                    for f in self.inf:
                        if r.startswith(f) and at(idx[i] + len(f)) and len(r) - len(f) >= 3:
                            k = at(idx[i] + len(f))
                            out.append(Morph("infix", "-" + f + "-", cut(i, k)))
                            i = k
                            pieces = None                # the stem the infix leaves: analysed again below
                            redo = True
                            break
                for p, a in sc:                          # from the outside in; written from the stem outward
                    k = at(idx[j] - len(a))
                    tail.insert(0, Morph("suffix", self.tone_label("suffix", p, a, cut(k, j)), cut(k, j),
                                         p if ("suffix", p, a) in self.positioned else 0))
                    j = k
        elif not blocked and not w.startswith("-"):     # a free morph (a pronoun, a conjunction) is a word
            best = None                                  # prefix and suffix chosen together: génanimák = ge-nanim-ák
            sufs = list(self.suf) + [v for (sl, v) in self.variants if sl == "suffix" and v not in self.suf]
            for p in [""] + [p for p in self.pre if s.startswith(p) and at(len(p))]:
                for q in [""] + [q for q in sufs if s.endswith(q) and at(len(s) - len(q)) and len(s) > len(p) + len(q)
                                 and (q in self.suf or self.variant_of("suffix", q, s[:len(s) - len(q)]))]:
                    if not (p or q or self.stem_on):
                        continue
                    core = s[len(p):len(s) - len(q)]
                    ev = (p or q) and (self.attested(core, p, q, self.vocab[s]) or (
                        len(core) >= 2 and (not p or p + "-" in self.productive) and (not q or "-" + q in self.productive)))
                    if not self.stem_on:
                        if ev:                           # most affixes, then the stem with the most other forms
                            key = (bool(p) + bool(q), ev)
                            if best is None or key > best[0]:
                                best = (key, p, q, None)
                        continue
                    # with the stem's own morphology: chosen together with the prefix and suffix, as in chain_parse
                    opts = [(1, 0, 0, self.cells(core, (p, q)), ev, None)] if ev else []
                    sb = self.stem_best(core, s, lambda a: at(len(p) + a) is not None)
                    if sb:
                        opts.append(sb)
                    for tier, off, nm, cel, e, ps in opts:
                        key = (tier, bool(p) + bool(q) + nm, cel, e)
                        if best is None or key > best[0]:
                            best = (key, p, q, ps)
            if best:
                _, p, q, pieces = best
                if p:
                    i = at(len(p))
                    out.append(Morph("prefix", self.tone_label("prefix", 0, p, cut(0, i)), cut(0, i)))
                    for f in self.inf:
                        r = s[idx[i]:len(s) - len(q)]
                        if r.startswith(f) and at(idx[i] + len(f)) and len(r) - len(f) >= 3:
                            k = at(idx[i] + len(f))
                            out.append(Morph("infix", "-" + f + "-", cut(i, k)))
                            i = k
                            pieces = None
                            redo = True
                            break
                tail = []
                if q:
                    j = at(len(s) - len(q))
                    lab = self.tone_label("suffix", 0, q, cut(j, len(cl))) if q in self.suf else \
                        self.variant_of("suffix", q, s[:len(s) - len(q)])
                    tail = [Morph("suffix", lab, cut(j, len(cl)))]
            else:
                tail = []
        else:
            tail = []
        if redo and self.stem_on:
            sb = self.stem_best(s[idx[i]:idx[j]], s, lambda a: at(idx[i] + a) is not None)
            pieces = sb[5] if sb else None
        out += self.stem_morphs(s, i, j, idx, at, cut, pieces)
        if tone:
            out.append(Morph("tone", tone[0].form, " ".join(f"{a}:U+{ord(m):04X}" for a, m in tone[1])))
        out += tail
        return out

    def variant_of(self, slot, written, before):
        """the store form a harmony variant stands for after the plain text `before` (a suffix) / before it (a prefix), or
        None"""
        for after, form in self.variants.get((slot, written), ()):
            if before and (not after or before[-1] in after):
                return form
        return None

    def labels(self, w):
        return {m.form for m in self.split(w) if m.slot != "stem"}

    def rebuild(self, ms):
        """the exact token from its morphs (the tone morph's marks put back on their clusters)"""
        cl = clusters("".join(m.text for m in ms if m.slot != "tone"))
        for m in ms:
            if m.slot == "tone":
                for item in m.text.split():
                    a, mk = item.split(":")
                    c = cl[int(a)]                       # after the base letter, before a modifier letter (꞉);
                    cl[int(a)] = c[0] + CH(mk) + c[1:]   # NFC below puts the marks in their canonical order
        return unicodedata.normalize("NFC", "".join(cl))

# ---- end of the engine ----


class Seg:
    """A bundle's segmenter, read from its seg_facts.json (the bundle loader's API)."""

    def __init__(self, d):
        with open(os.path.join(d, "seg_facts.json"), encoding="utf-8") as fh:
            f = json.load(fh)
        assert f["spec"] == SPEC, f["spec"]
        self.M, self.facts = sys.modules[__name__], f
        G = types.SimpleNamespace(morphs=f["morphs"], words=f["words"])
        classes = {w: collections.Counter(c) for w, c in (f.get("classes") or {}).items()} or None
        self.S = Segmenter(G, f["orthography"], f["vocab"], classes)
        self.ids = {(e["form"], e.get("position") or 0): e.get("id") for e in f["morphs"]}

    def split(self, word):
        return [{"slot": m.slot, "form": m.form, "text": m.text, "position": m.position,
                 "chain_id": self.ids.get((m.form, m.position)) if m.slot != "stem" else None} for m in self.S.split(word)]


def load(d):
    return Seg(d)


if __name__ == "__main__":
    s = load(sys.argv[1])
    for w in sys.argv[2:]:
        print(w, s.split(w))
