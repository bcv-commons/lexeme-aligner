"""Derived fact `article_bound`: is the target language's definite article fused into the noun (Swedish
`brodern`, Bulgarian `братът`, Arabic `الرب`, Hebrew `הבית`), rather than a separate word?

WHY. Neither Grambank nor WALS can tell us usefully. Grambank GB020 says outright that "the formal expression
is irrelevant; articles can be free, bound, or marked by suprasegmental markers", and GB022/GB023 give only
prenominal/postnominal (Swedish and Danish are 1/1, so our `article` direction is None; Faroese, Macedonian and
Armenian are 0/1 and therefore look like a free word AFTER the noun). WALS 37A does record "definite affix" but
`typology.py` deliberately never reads it and the vendored coverage is patchy. Meanwhile, measured 2026-09-29,
the Greek article ὁ is aligned to the SAME target word as its noun in only 0.4-1.5% of cases in swe/dan/nob/bul/
isl (English control 0.3%): a fused article is simply lost, and the `articles` span-extension mechanism looks for
a free word that is not there.

METHOD (alignment-derived, gold-free, the same idea as Östling-style typology). Take every NOUN token (prior-pack
pos == "noun") whose base alignment (eflomal, then gloss) is a SINGLE target word, in both testaments. Split them
by whether the source noun has an article token (Greek ὁ, Hebrew הַ) one or two tokens before it. A fused
article shows up as an affix that ends (or begins) the target word far more often when the source noun HAS an
article than when it does not. For every suffix and prefix of 1-4 characters with enough support:

    lift  = P(word has affix | article) - P(word has affix | no article)
    ratio = P(affix | article) / P(affix | no article)

A second, independent statistic looks at each noun LEXEME on its own: its most frequent target form with an article
versus without one (>= 3 occurrences each, the form seen >= 2 times). If the article form is the plain form plus a
1-4 character affix at an edge, that affix is counted; the PARADIGM SHARE is the top affix's share of the lexemes
tested (bul -та/-те/-то, som -ka, eus -a, fao -in, arb ال-, heb ה-).

`bound` when EITHER (a) some affix reaches BOTH `MIN_LIFT` and `MIN_RATIO`, OR (b) the paradigm share reaches
`MIN_PARADIGM_SHARE` over at least `MIN_PARADIGM_LEXEMES` lexemes; `False` otherwise; `None` (no fact, never a
guess) when there are too few nouns. **`False` means "no bound article detected", NOT "free article"**: it also
covers languages with no article at all (hin, fin, rus, cmn), and a real bound article can be missed (isl, amh:
too little signal). Only a positive is ever used to gate anything.

CALIBRATION (2026-09-29, 15 bound-article and 25 free/none languages, then checked against WALS 37A): see the
plan doc. The thresholds were placed on that sample, so they are not a held-out result.

    python3 -m lexeme_aligner.article_bound --tag swe_fol --iso swe
    python3 -m lexeme_aligner.article_bound --build          # every language with alignments -> config/gram_struct/article_bound.json
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
import unicodedata
from pathlib import Path

from lexeme_aligner.align_files import tag_files
from lexeme_aligner.config import OUT, PRIOR_PACK

OUT_FILE = Path("config/gram_struct/article_bound.json")
ARTICLE_LEXEMES = frozenset({"grc:3588", "hbo:1886a"})       # ὁ / Hebrew הַ (as span_extension's own constants)
AFFIX_LENGTHS = (1, 2, 3, 4)
MIN_NOUNS_EACH = 300          # nouns WITH and WITHOUT a preceding article, each
MIN_AFFIX_SUPPORT = 40        # nouns with the article that carry the candidate affix
MIN_WORD_LEN_EXTRA = 2        # the stem left after removing an L-char affix must keep >= 2 chars
MIN_LIFT = 0.07
MIN_RATIO = 1.8
MIN_PARADIGM_SHARE = 0.055
MIN_PARADIGM_LEXEMES = 150


def _norm(word: str) -> str:
    return unicodedata.normalize("NFC", word).lower()


def collect_nouns(tag: str, lex_pos: dict[str, str], article_before: dict[tuple[int, int], bool],
                  out_dir: Path = OUT, methods=("eflomal", "gloss")) -> list[tuple[str, bool, str]]:
    """[(target word, has_article, lexeme)] — one row per noun occurrence whose winning base alignment is a
    single target word. `article_before[(ref, h_idx)]` is the source-side fact (article token 1-2 positions
    before)."""
    seen: set[tuple[int, int]] = set()
    rows: list[tuple[str, bool, str]] = []
    for m in methods:
        for fp in tag_files(out_dir, m, tag):
            op = gzip.open if str(fp).endswith(".gz") else open
            with op(fp, "rt", encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    ref = rec["ref"]
                    for p in rec.get("pairs", []):
                        key = (ref, p["h_idx"])
                        if key in seen or not p.get("content") or lex_pos.get(p.get("lexeme")) != "noun":
                            continue
                        if key not in article_before:
                            continue
                        t = p.get("t_idx") or []
                        if len(t) != 1 or not (p.get("target") or "").strip():
                            continue
                        seen.add(key)
                        rows.append((_norm(p["target"]), article_before[key], p["lexeme"]))
    return rows


def affix_lifts(rows: list[tuple[str, bool, str]], lengths=AFFIX_LENGTHS, min_support: int = MIN_AFFIX_SUPPORT
                ) -> list[dict]:
    """Every (edge, affix) candidate with support, best lift first."""
    n_art = sum(1 for _, a, _l in rows if a)
    n_no = len(rows) - n_art
    out = []
    if not n_art or not n_no:
        return out
    for edge in ("suffix", "prefix"):
        for L in lengths:
            ca: collections.Counter = collections.Counter()
            cn: collections.Counter = collections.Counter()
            for w, a, _l in rows:
                if len(w) < L + MIN_WORD_LEN_EXTRA:
                    continue
                s = w[-L:] if edge == "suffix" else w[:L]
                (ca if a else cn)[s] += 1
            for s, c in ca.items():
                if c < min_support:
                    continue
                pa, pn = c / n_art, cn[s] / n_no
                out.append({"edge": edge, "affix": s, "p_article": round(pa, 4), "p_anarthrous": round(pn, 4),
                            "lift": round(pa - pn, 4), "ratio": round(pa / pn, 2) if pn else float("inf"),
                            "n": c})
    out.sort(key=lambda d: (-round(d["lift"], 3), len(d["affix"])))     # ties: the shorter affix (the marker is a hint)
    return out


def paradigm_stat(rows: list[tuple[str, bool, str]], min_count: int = 3, min_top: int = 2) -> dict:
    """Per-lexeme contrast: {'tested': n lexemes, 'top': [(affix, count)...]}."""
    forms: dict[str, list[collections.Counter]] = collections.defaultdict(
        lambda: [collections.Counter(), collections.Counter()])
    for w, a, lx in rows:
        forms[lx][0 if a else 1][w] += 1
    tested = 0
    aff: collections.Counter = collections.Counter()
    for fa, fn in forms.values():
        if sum(fa.values()) < min_count or sum(fn.values()) < min_count:
            continue
        wa, ca = fa.most_common(1)[0]
        wn, cn = fn.most_common(1)[0]
        if ca < min_top or cn < min_top:
            continue
        tested += 1
        if wa == wn:
            continue
        d = len(wa) - len(wn)
        if 1 <= d <= 4 and wa.startswith(wn):
            aff["-" + wa[len(wn):]] += 1
        if 1 <= d <= 4 and wa.endswith(wn):
            aff[wa[:d] + "-"] += 1
    return {"tested": tested, "top": aff.most_common(3)}


def article_bound_slot(rows: list[tuple[str, bool, str]]) -> dict | None:
    """The derived slot, or None when there are too few nouns to say anything."""
    n_art = sum(1 for _, a, _l in rows if a)
    n_no = len(rows) - n_art
    if n_art < MIN_NOUNS_EACH or n_no < MIN_NOUNS_EACH:
        return None
    cands = affix_lifts(rows)
    qual = [c for c in cands if c["lift"] >= MIN_LIFT and c["ratio"] >= MIN_RATIO]
    best = qual[0] if qual else (cands[0] if cands else None)
    par = paradigm_stat(rows)
    share = (par["top"][0][1] / par["tested"]) if par["tested"] and par["top"] else 0.0
    via_lift = bool(qual)
    via_paradigm = par["tested"] >= MIN_PARADIGM_LEXEMES and share >= MIN_PARADIGM_SHARE
    slot = {"bound": via_lift or via_paradigm, "source": "derived", "n_article": n_art, "n_anarthrous": n_no,
            "affix_lift": ({k: best[k] for k in ("edge", "affix", "lift", "ratio", "n")} if best else None),
            "paradigm": {"lexemes_tested": par["tested"], "top_affix": par["top"][0][0] if par["top"] else None,
                         "share": round(share, 4)},
            "via": [n for n, ok in (("affix_lift", via_lift), ("paradigm", via_paradigm)) if ok]}
    if slot["bound"]:
        if via_lift:
            slot["edge"] = best["edge"]
            slot["marker"] = ("-" if best["edge"] == "suffix" else "") + best["affix"] + ("-" if best["edge"] == "prefix" else "")
        else:
            slot["marker"] = par["top"][0][0]
            slot["edge"] = "suffix" if slot["marker"].startswith("-") else "prefix"
    return slot


def source_article_index(books: list[str]) -> dict[tuple[int, int], bool]:
    """{(ref, h_idx): article token 1-2 positions before} for every content noun-candidate token in `books`,
    straight from the spine (edition independent)."""
    from lexeme_aligner.hebrew_source import HebrewSource
    from lexeme_aligner.refs import encode
    heb = HebrewSource()
    out: dict[tuple[int, int], bool] = {}
    for book in books:
        for ch in heb.chapters(book):
            for v in heb.verses(book, ch):
                toks = {t.idx: t for t in heb.verse_tokens(book, ch, v)}
                ref = encode(book, ch, v)
                for i, t in toks.items():
                    if t.is_content and t.strong:
                        out[(ref, i)] = any(toks[j].lexeme in ARTICLE_LEXEMES for j in (i - 1, i - 2) if j in toks)
    return out


def derive_for_tag(tag: str, article_before: dict, lex_pos: dict, out_dir: Path = OUT) -> dict | None:
    return article_bound_slot(collect_nouns(tag, lex_pos, article_before, out_dir))


def combine_article_bound(slots: dict[str, dict]) -> dict:
    """The language-level verdict from one slot PER EDITION ({tag: slot}): `edition_vote.combine_editions`, each
    edition weighted by its own article evidence (`n_article`). A clear majority wins; a split abstains
    (`bound: null, reason: "edition_conflict"`) — and since only a positive `bound` ever gates anything, an
    abstention never changes behaviour. Every edition's vote and the agreement are recorded."""
    from lexeme_aligner.edition_vote import combine_editions
    out = combine_editions(slots, field="bound", weight_key="n_article")
    return out if out is not None else {"bound": None, "source": "derived", "reason": "no edition analysed"}


def load_article_bound(iso: str, path: Path | None = None) -> dict | None:
    """The stored slot for `iso`, or None (unknown). Looked up at CALL time so tests can monkeypatch `path`."""
    path = path or OUT_FILE
    if not Path(path).exists():
        return None
    try:
        return (json.loads(Path(path).read_text(encoding="utf-8")).get("languages") or {}).get(iso)
    except (OSError, ValueError):          # never let a half-written / unreadable file break a chain
        return None


def is_bound(iso: str, path: Path | None = None) -> bool:
    """True only for a positive `bound` fact — an absent or None slot never counts as bound."""
    slot = load_article_bound(iso, path)
    return bool(slot and slot.get("bound"))


def _write_atomic(path: Path, doc: dict) -> None:
    """tmp + rename: the chain's span_extension reads this file while a sweep may be rewriting it."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag")
    ap.add_argument("--iso")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    from lexeme_aligner.compact_align import ALL_BOOKS
    from lexeme_aligner.span_extension import load_priors
    lex_pos, _ = load_priors(PRIOR_PACK)
    idx = source_article_index(list(ALL_BOOKS))
    if a.tag:
        rows = collect_nouns(a.tag, lex_pos, idx, a.out)
        slot = article_bound_slot(rows)
        print(json.dumps({"iso": a.iso, "tag": a.tag, "nouns": len(rows), "slot": slot,
                          "top": affix_lifts(rows)[:5]}, ensure_ascii=False, indent=1))
        return 0
    if not a.build:
        ap.error("pass --tag or --build")
    from lexeme_aligner.derive_typology import editions_of, resolve_tag
    manifest = json.loads(Path("publish/compact-alignments/manifest.json").read_text(encoding="utf-8"))["languages"]
    prior = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {"languages": {}}
    langs = prior.setdefault("languages", {})
    for n, iso in enumerate(sorted(manifest), 1):
        if iso in langs and "editions" in (langs[iso] or {}):
            continue                      # already a per-edition verdict; older single-edition entries are recomputed
        slots: dict[str, dict] = {}
        failed = False
        for tag, _ecode, _books in editions_of(iso, manifest):
            resolved = resolve_tag(tag)
            if not resolved or not tag_files(a.out, "eflomal", resolved[0]):
                continue                  # no alignment output on disk for this edition: no fact, not "too few"
            try:
                slot = derive_for_tag(resolved[0], idx, lex_pos, a.out)
            except (OSError, EOFError, ValueError) as e:   # a file the batch is writing right now: retry next run
                print(f"[article_bound] {iso}/{resolved[0]}: skipped ({e})", file=sys.stderr)
                failed = True
                continue
            slots[resolved[0]] = slot if slot is not None else {"bound": None, "source": "derived",
                                                                "reason": "too few nouns"}
        if not slots or failed:
            continue
        langs[iso] = combine_article_bound(slots)
        if n % 25 == 0:
            _write_atomic(OUT_FILE, prior)
            print(f"[article_bound] {n} languages", file=sys.stderr)
    prior["_doc"] = ("article_bound: alignment-derived fact — is the definite article fused into the noun? "
                     "bound=true/false/null per LANGUAGE (null = too few nouns, or editions split); each edition is analysed "
                     "separately and recorded under `editions` with the `agreement`. See lexeme_aligner/article_bound.py.")
    prior["_thresholds"] = {"min_lift": MIN_LIFT, "min_ratio": MIN_RATIO, "min_nouns_each": MIN_NOUNS_EACH,
                            "min_paradigm_share": MIN_PARADIGM_SHARE, "min_paradigm_lexemes": MIN_PARADIGM_LEXEMES}
    _write_atomic(OUT_FILE, prior)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
