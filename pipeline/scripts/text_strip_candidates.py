"""Report generator — clues for deciding whether an edition's [...] / (...) content should be opted
into stripping via config/text_strip_rules.json (see that file's `_doc` for the decision it feeds).

usj_source.py never strips anything automatically; a human reads this report, judges each flagged
edition, and writes the decision into config/text_strip_rules.json themselves. This script only
surfaces evidence — it never writes the decision file.

2026-09-30: brackets and parentheses are now analysed SEPARATELY, each with its own signals and its own suggested
role (lexeme_aligner/span_roles.py explains why one yardstick cannot serve both): span length and shape, citation
patterns, the alignment rate inside spans relative to outside (from the edition's alignments on disk), and overlap with
sibling editions of the same language. Output: the readable report (config/text_strip_report.md, only the editions that
need a human look, plus summary tables) and the complete machine-readable evidence (config/text_strip_evidence.json).
Svenska Kärnbibeln (`swk`, `swe_svk`) is excluded by default: checked by hand, a known outlier whose decision is already
on file.

    make text-strip-report                     # all cached editions
    python3 pipeline/scripts/text_strip_candidates.py --workers 3 --no-cache-reuse
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pipeline/ on sys.path for lexeme_aligner

from lexeme_aligner.usj_source import (
    _BRACKET_NOTE_RE,
    _PAREN_BOOK_REF_RE,
    _PAREN_CITATION_RE,
    _PAREN_ERA_RE,
    _PAREN_LITERAL_RE,
    _PAREN_MEANS_RE,
    _PAREN_RE,
    _PAREN_VERSE_REF_RE,
    _token_spans,
    read_raw_verses,
    read_verse_ranges,
    tokenize,
)
from lexeme_aligner.config import OUT
from lexeme_aligner.run_pilot import _BOOK_FILE_NUM, NT_BOOKS, OT_BOOKS
from lexeme_aligner import span_roles as roles

# _paren_is_noise's rules split into two evidence classes, reported separately so the report doesn't
# overclaim: book/verse-ref ("Yesaya 7:14", "vers 17") is a language-agnostic citation SHAPE — it fires
# correctly on Indonesian, Portuguese, anything, because Bible cross-references look the same regardless
# of translation language. citation/literal/means/era (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.) are
# Swedish VOCABULARY — a hit there is only meaningful evidence for a Swedish-language edition; on
# anything else it would be coincidence, not signal (verified this split matters: gefsgv's 44% "noise"
# hit rate turned out to be 100% book-ref citations, zero Swedish-vocabulary hits — a real, structural
# finding, but the old single "Swedish-lexical" label made it sound like an unlikely coincidence instead
# of the systematic apparatus pattern it actually is).
_STRUCTURAL_REF_RES = (_PAREN_VERSE_REF_RE, _PAREN_BOOK_REF_RE)
_SWEDISH_LEXICAL_RES = (_PAREN_CITATION_RE, _PAREN_LITERAL_RE, _PAREN_MEANS_RE, _PAREN_ERA_RE)


def _new_span_stats() -> dict:
    return {"n_spans": 0, "n_words": 0, "max_words": 0, "n_short": 0,
            "n_structural_ref": 0, "n_swedish_lexical": 0, "samples": []}


def _accumulate(stats: dict, span_re, text: str) -> None:
    for m in span_re.finditer(text):
        content = m.group(1) if span_re.groups else m.group(0)[1:-1]
        content = content.strip()
        if not content:
            continue
        w = len(tokenize(content))
        if w == 0:
            continue
        stats["n_spans"] += 1
        stats["n_words"] += w
        stats["max_words"] = max(stats["max_words"], w)
        if w <= 3:
            stats["n_short"] += 1
        if any(r.search(content) for r in _STRUCTURAL_REF_RES):
            stats["n_structural_ref"] += 1
        if any(r.search(content) for r in _SWEDISH_LEXICAL_RES):
            stats["n_swedish_lexical"] += 1
        stats["samples"].append((w, content))


def _edition_stats(usj_root: Path, tag: str) -> tuple[dict, dict, int]:
    """One read pass per book — bracket stats, paren stats, and total word count together, since all
    three need the same raw verse text and re-reading/re-tokenizing per metric wastes time at this
    edition count (182+ cached editions)."""
    bracket = _new_span_stats()
    paren = _new_span_stats()
    total_words = 0
    edition_dir = usj_root / f"usj-{tag}"
    for fp in sorted(edition_dir.glob("*.json")):
        for text in read_raw_verses(fp).values():
            total_words += len(tokenize(text))
            _accumulate(bracket, _BRACKET_NOTE_RE, text)
            _accumulate(paren, _PAREN_RE, text)
    return bracket, paren, total_words


# Length alone does NOT generalize as a "this is editorial noise" signal across editions — plenty of
# ordinary translations legitimately put a genuine long clause in parens (a translator's aside, a
# quoted-speech attribution, a relative clause), so a moderate median/pct_long bar still flags a huge
# share of totally normal editions (verified: an early median/outlier-ratio version still called 209 of
# 248 bracket/paren sections "commentary-like", incl. engy's parens — 368 spans, median 7 words, several
# genuine translated clauses like "from between the two cherubs, which [are] on the ark of the
# testimony", nothing editorial about it). What actually distinguished swk wasn't "somewhat long" — it
# was EXTREME (spans up to 818 words, far past any plausible in-verse aside) AND a real hit-rate against
# known editorial-note vocabulary (citations/cross-refs/ordagrant/betyder/era — 37% of its bracket
# spans). So "commentary-like" now requires one of those two independently strong signals, not just a
# length percentile that ordinary long-but-legitimate clauses can also produce.
_LONG_WORDS = 10  # a span this long or longer counts as an "outlier" for the small-outlier bucket below
_EXTREME_WORDS = 100  # far beyond any plausible single in-verse aside — swk's bracket max was 818


def _verdict(stats: dict) -> str:
    n = stats["n_spans"]
    if n == 0:
        return ""
    words = sorted(w for w, _ in stats["samples"])
    median = words[n // 2]
    pct_short = stats["n_short"] / n
    n_long = sum(1 for w in words if w >= _LONG_WORDS)
    pct_long = n_long / n
    ref_pct = stats["n_structural_ref"] / n
    lex_pct = stats["n_swedish_lexical"] / n

    if pct_short >= 0.85 and pct_long <= 0.05:
        return ("**short-span-like** (%.0f%% of spans are <=3 words, only %d/%d reach %d+ words) — "
                "resembles a supplied-word or short-gloss convention (e.g. YLT); likely genuine content, "
                "NOT a stripping candidate." % (pct_short * 100, n_long, n, _LONG_WORDS))

    # Deliberately NOT a "most spans are longish" branch: tried it (>=50% at 10+ words, n>=50) and it
    # false-positived on `law` — 84 bracket spans, 83% at 10+ words, median 15 — which turned out to be
    # ordinary full quoted sentences in an indigenous-language edition, not editorial notes; that
    # language's bracket convention (or just its normal sentence length) produces long spans with zero
    # actual commentary. Length alone never discriminates that from swk; only an EXTREME outlier (no
    # legitimate in-verse aside runs to hundreds of words) or a real hit-rate against one of the two
    # `_paren_is_noise` evidence classes does.
    strong_ref_evidence = stats["n_structural_ref"] >= 10 and ref_pct >= 0.20
    strong_lex_evidence = stats["n_swedish_lexical"] >= 10 and lex_pct >= 0.20
    strong_length_evidence = stats["max_words"] >= _EXTREME_WORDS
    if strong_ref_evidence or strong_lex_evidence or strong_length_evidence:
        why = []
        if strong_length_evidence:
            why.append(f"max span reaches {stats['max_words']} words — far past a plausible aside")
        if strong_ref_evidence:
            why.append(f"{stats['n_structural_ref']} spans ({ref_pct * 100:.0f}%) look like verse/book "
                       f"citations (language-agnostic pattern, e.g. 'Yesaya 7:14')")
        if strong_lex_evidence:
            why.append(f"{stats['n_swedish_lexical']} spans ({lex_pct * 100:.0f}%) matched Swedish-specific "
                       f"editorial vocabulary (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.)")
        return ("**commentary-like** (" + "; ".join(why) + ") — resembles swk's editorial-note usage; "
                "worth reviewing for `strip_brackets`/`strip_parens_noise`.")

    if 0 < n_long <= max(3, round(0.05 * n)):
        return ("mostly short (median %d words/span) with %d long outlier(s) (%d+ words) out of %d — "
                "not a bulk pattern, but worth reading those specific outliers before deciding anything."
                % (median, n_long, _LONG_WORDS, n))
    return (f"no strong signal either way (median {median} words/span, {n} spans) — legitimately long "
            "parenthetical/bracketed clauses are common in ordinary translation; read the samples before "
            "assuming this is editorial noise.")


def _format_section(label: str, stats: dict, total_words: int, sample_n: int, seed: random.Random) -> list[str]:
    lines = []
    pct = (stats["n_words"] / total_words * 100) if total_words else 0.0
    lines.append(f"### {label} — {stats['n_spans']} spans, {stats['n_words']} words "
                 f"({pct:.2f}% of edition), max {stats['max_words']} words/span")
    # A handful of coincidental hits isn't a signal — require both a real count and a real share of spans
    # before surfacing either line, so it means something when it appears. Kept separate (see the module
    # comment above _STRUCTURAL_REF_RES): a ref-shape hit is evidence in ANY language, a lexical hit is
    # only evidence if this edition is actually Swedish.
    n = stats["n_spans"]
    ref_pct = stats["n_structural_ref"] / n if n else 0.0
    lex_pct = stats["n_swedish_lexical"] / n if n else 0.0
    if stats["n_structural_ref"] >= 5 and ref_pct >= 0.05:
        lines.append(f"- {stats['n_structural_ref']} span(s) ({ref_pct * 100:.0f}%) look like verse/book "
                      f"citations (e.g. 'Yesaya 7:14', 'vers 17') — a language-agnostic apparatus pattern, "
                      f"not translated content, regardless of this edition's language.")
    if stats["n_swedish_lexical"] >= 5 and lex_pct >= 0.05:
        lines.append(f"- {stats['n_swedish_lexical']} span(s) ({lex_pct * 100:.0f}%) matched "
                      f"Swedish-specific editorial vocabulary (hebr./grek./ordagrant/betyder/f.Kr./e.Kr.) "
                      f"— only meaningful if this edition is actually in Swedish.")
    verdict = _verdict(stats)
    if verdict:
        lines.append(f"- Verdict: {verdict}")
    if stats["samples"]:
        pool = stats["samples"]
        picked = seed.sample(pool, min(sample_n, len(pool)))
        lines.append("- Samples:")
        for w, content in picked:
            shown = content if len(content) <= 160 else content[:157] + "..."
            lines.append(f"  - ({w}w) `{shown}`")
    return lines


def build_report(usj_root: Path, min_pct: float, sample_n: int, decisions: dict) -> str:
    seed = random.Random(0)  # stable sampling across re-runs so diffs are meaningful
    tags = sorted(p.name[len("usj-"):] for p in usj_root.glob("usj-*") if p.is_dir())
    rows = []
    for i, tag in enumerate(tags, 1):
        print(f"[{i}/{len(tags)}] {tag}", file=sys.stderr)
        bracket, paren, total_words = _edition_stats(usj_root, tag)
        if total_words == 0:
            continue
        b_pct = bracket["n_words"] / total_words * 100
        p_pct = paren["n_words"] / total_words * 100
        if max(b_pct, p_pct) < min_pct:
            continue
        rows.append((max(b_pct, p_pct), tag, bracket, paren, total_words, b_pct, p_pct))
    rows.sort(key=lambda r: -r[0])

    out = [
        "# Text-strip candidates",
        "",
        f"Generated by `pipeline/scripts/text_strip_candidates.py` — {len(rows)} of {len(tags)} cached "
        f"editions have >= {min_pct}% of their words inside `[...]` or `(...)`. This is EVIDENCE, not a "
        "decision: nothing here is stripped automatically. Read the samples and verdict for an edition, "
        "then (if warranted) write the decision into `config/text_strip_rules.json` yourself.",
        "",
    ]
    for _, tag, bracket, paren, total_words, b_pct, p_pct in rows:
        out.append(f"## {tag}")
        if tag in decisions:
            d = decisions[tag]
            out.append(f"> Decision already on file: `strip_brackets={d.get('strip_brackets', False)}`, "
                        f"`strip_parens_noise={d.get('strip_parens_noise', False)}`, "
                        f"`strip_alternate_brackets={d.get('strip_alternate_brackets', False)}`, "
                        f"`strip_alternate_parens={d.get('strip_alternate_parens', False)}` — see "
                        "`config/text_strip_rules.json`.")
        out.append(f"{total_words} words total in this edition.")
        out.append("")
        if bracket["n_spans"]:
            out.extend(_format_section("Brackets `[...]`", bracket, total_words, sample_n, seed))
            out.append("")
        if paren["n_spans"]:
            out.extend(_format_section("Parens `(...)`", paren, total_words, sample_n, seed))
        out.append("")
    return "\n".join(out)


# ---- per-kind analysis (2026-09-30) ---------------------------------------------------------------------------
EXCLUDE_DEFAULT = ("swk", "swe_svk")
CACHE_DIR = Path("pipeline/work/text_strip/cache")
BOOK_FILES = [(b, _BOOK_FILE_NUM[b]) for b in OT_BOOKS + NT_BOOKS]
SAMPLE_SPANS = 150
EVIDENCE_VERSION = 2


def _script_of(text: str) -> str:
    c: collections.Counter = collections.Counter()
    for ch in text[:600]:
        if ch.isalpha():
            try:
                c[unicodedata.name(ch).split()[0]] += 1
            except ValueError:
                pass
    return c.most_common(1)[0][0] if c else ""


def _sibling_words(tag: str, book: str, usj_root: Path, cache: dict) -> dict:
    key = (tag, book)
    if key not in cache:
        fp = usj_root / f"usj-{tag}" / f"{_BOOK_FILE_NUM[book]}-{book}.json"
        words: dict = {}
        if fp.exists():
            for (ch, vs), rng in read_verse_ranges(fp, rules={}).items():
                text = " ".join(rng["text"]) if isinstance(rng["text"], list) else rng["text"]
                words[(ch, vs)] = (frozenset(w.lower() for w in tokenize(text)), text)
        cache[key] = words
    return cache[key]


def _sibling_overlap(samples: list, siblings: list[str], usj_root: Path) -> float | None:
    """Mean over sampled spans of the best share of the span's words found in the same verse of a sibling edition."""
    if not samples or not siblings:
        return None
    cache: dict = {}
    shares = []
    for book, ch, vs, content in samples:
        ws = {w.lower() for w in tokenize(content)}
        if not ws:
            continue
        best = None
        for sib in siblings:
            hit = _sibling_words(sib, book, usj_root, cache).get((ch, vs))
            if hit and content not in hit[1]:          # an identical span means a near-duplicate text: no evidence
                share = len(ws & hit[0]) / len(ws)
                best = share if best is None else max(best, share)
        if best is not None:
            shares.append(best)
    return round(sum(shares) / len(shares), 3) if len(shares) >= 10 else None


def analyse_edition(tag: str, usj_root: str, out_dir: str, siblings: list[str], rule_tags: list[str],
                    min_pct: float) -> dict:
    """Everything known about one edition's brackets and parentheses, each kind on its own."""
    usj_root_p, out_p = Path(usj_root), Path(out_dir)
    edition_dir = usj_root_p / f"usj-{tag}"
    rng = random.Random(tag)
    acc = {k: {"spans": [], "ref": 0, "lex": 0, "samples": [], "rep": [], "alt_samples": []}
           for k in ("brackets", "parens")}
    total_words = 0
    script_text = ""
    for book, num in BOOK_FILES:
        fp = edition_dir / f"{num}-{book}.json"
        if not fp.exists():
            continue
        for (ch, vs), r in read_verse_ranges(fp, rules={}).items():
            text = " ".join(r["text"]) if isinstance(r["text"], list) else r["text"]
            total_words += len(tokenize(text))
            if len(script_text) < 600:
                script_text += text[:200]
            masked = text
            for span_re in (_BRACKET_NOTE_RE, _PAREN_RE):
                masked = span_re.sub(" ", masked)
            outside = roles.content_words(tokenize(masked))
            for kind, span_re in (("brackets", _BRACKET_NOTE_RE), ("parens", _PAREN_RE)):
                a = acc[kind]
                for m in span_re.finditer(text):
                    content = (m.group(1) if span_re.groups else m.group(0)[1:-1]).strip()
                    w = len(tokenize(content)) if content else 0
                    if not w:
                        continue
                    a["spans"].append((w, content))
                    share = roles.repetition_share(tokenize(content), outside)
                    if share is not None:
                        a["rep"].append((w, share))
                        if share >= roles.ALT_SPAN_SHARE and len(a["alt_samples"]) < 40:
                            a["alt_samples"].append(content)
                    a["ref"] += any(x.search(content) for x in _STRUCTURAL_REF_RES)
                    a["lex"] += any(x.search(content) for x in _SWEDISH_LEXICAL_RES)
                    if len(a["samples"]) < SAMPLE_SPANS:
                        a["samples"].append((book, ch, vs, content))
                    else:                                  # reservoir: keeps a uniform sample without storing everything
                        j = rng.randrange(len(a["spans"]))
                        if j < SAMPLE_SPANS:
                            a["samples"][j] = (book, ch, vs, content)
    result: dict = {"version": EVIDENCE_VERSION, "tag": tag, "words": total_words, "script": _script_of(script_text)}
    if total_words == 0:
        return result
    pcts = {k: sum(w for w, _ in acc[k]["spans"]) / total_words * 100 for k in acc}
    rates = None
    if max(pcts.values()) >= min_pct and tag not in rule_tags:       # an edition with a strip rule has shifted indices
        rates = roles.alignment_rates(tag, usj_root_p, out_p, _BRACKET_NOTE_RE, _PAREN_RE, _token_spans,
                                      read_verse_ranges, BOOK_FILES)
    for kind in ("brackets", "parens"):
        a = acc[kind]
        st = roles.span_length_stats(a["spans"], a["ref"], a["lex"])
        st.update(roles.repetition_stats(a["rep"]))
        st["pct_alt_of_edition"] = round(st["n_dup_words"] / total_words * 100, 4)
        st["pct_of_edition"] = round(pcts[kind], 4)
        ar = roles.ratio(rates, kind)
        st["align_ratio"] = None if ar is None else round(ar, 3)
        st["align_words_inside"] = rates[kind][1] if rates else 0
        st["sibling_overlap"] = (_sibling_overlap(a["samples"], siblings, usj_root_p)
                                 if pcts[kind] >= min_pct else None)
        st["role"], st["reasons"] = roles.classify_role(kind, st, pcts[kind], ar)
        st["samples"] = [c if len(c) <= 140 else c[:137] + "..." for _b, _c, _v, c in
                         rng.sample(a["samples"], min(4, len(a["samples"])))]
        st["alt_samples"] = [c if len(c) <= 140 else c[:137] + "..." for c in
                             rng.sample(a["alt_samples"], min(3, len(a["alt_samples"])))]
        result[kind] = st
    if rates:
        result["alignment"] = {k: {"aligned": v[0], "words": v[1]} for k, v in rates.items()}
    return result


def _siblings_of(tags: list[str], usj_root: Path) -> dict[str, list[str]]:
    by_prefix: dict[str, list[str]] = collections.defaultdict(list)
    for t in tags:
        by_prefix[t[:3]].append(t)
    scripts: dict[str, str] = {}

    def script(t: str) -> str:
        if t not in scripts:
            for book, num in (("MAT", _BOOK_FILE_NUM["MAT"]), ("GEN", _BOOK_FILE_NUM["GEN"])):
                fp = usj_root / f"usj-{t}" / f"{num}-{book}.json"
                if fp.exists():
                    txt = " ".join(" ".join(r["text"]) if isinstance(r["text"], list) else r["text"]
                                   for r in list(read_verse_ranges(fp, rules={}).values())[:6])
                    scripts[t] = _script_of(txt)
                    break
            else:
                scripts[t] = ""
        return scripts[t]

    out: dict[str, list[str]] = {}
    for t in tags:
        sibs = [x for x in by_prefix[t[:3]] if x != t and "." not in x]
        out[t] = [x for x in sibs if script(x) == script(t)][:2] if sibs else []
    return out


def _worker(args):
    return analyse_edition(*args)


def build_evidence(usj_root: Path, out_dir: Path, min_pct: float, exclude: set[str], rule_tags: list[str],
                   workers: int, reuse: bool, only: set[str] | None = None) -> dict:
    tags = sorted(p.name[len("usj-"):] for p in usj_root.glob("usj-*") if p.is_dir())
    tags = [t for t in tags if t not in exclude]
    sibs = _siblings_of(tags, usj_root)
    if only:
        tags = [t for t in tags if t in only]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    todo = []
    for t in tags:
        fp = CACHE_DIR / f"{t}.json"
        if reuse and fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            if d.get("version") == EVIDENCE_VERSION:
                done[t] = d
                continue
        todo.append(t)
    print(f"[text-strip] {len(tags)} editions ({len(exclude)} excluded), {len(done)} cached, {len(todo)} to analyse", file=sys.stderr)
    jobs = [(t, str(usj_root), str(out_dir), sibs[t], rule_tags, min_pct) for t in todo]
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, res in enumerate(ex.map(_worker, jobs, chunksize=1), 1):
            done[res["tag"]] = res
            tmp = CACHE_DIR / f".{res['tag']}.tmp"
            tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            tmp.replace(CACHE_DIR / f"{res['tag']}.json")
            if i % 25 == 0:
                print(f"[text-strip] {i}/{len(jobs)} analysed", file=sys.stderr)
    return {t: done[t] for t in tags}


def render_evidence_report(evid: dict, exclude: set[str], min_pct: float, decisions: dict) -> str:
    n = len(evid)
    gated = {t: e for t, e in evid.items() if e.get("words") and
             max(e.get("brackets", {}).get("pct_of_edition", 0), e.get("parens", {}).get("pct_of_edition", 0)) >= min_pct}
    count = {k: collections.Counter(e[k]["role"] for e in evid.values() if k in e) for k in ("brackets", "parens")}
    lines = ["# Text-strip candidates", "",
             f"Generated by `pipeline/scripts/text_strip_candidates.py`. {n} cached editions analysed "
             f"({', '.join(sorted(exclude))} excluded: checked by hand, a known outlier with its decision on file); "
             f"{len(gated)} have >= {min_pct}% of their words inside `[...]` or `(...)`. Brackets and parentheses are judged "
             "SEPARATELY: each kind has its own signals and its own suggested role. This is EVIDENCE, not a decision: "
             "nothing is stripped automatically, and the full per-edition numbers are in `config/text_strip_evidence.json`.", "",
             "## Roles across editions", "", "| role | brackets | parentheses |", "|---|---|---|"]
    for role in ("content", "supplied", "mixed", "commentary", "alternate", "unclear", "insufficient", "negligible"):
        lines.append(f"| {role} | {count['brackets'].get(role, 0)} | {count['parens'].get(role, 0)} |")
    lines += ["", "`content` and `supplied` need no action; `negligible` (< 0.05% of words) cannot matter. The editions below "
              "are the ones a human should read: any `commentary`, `alternate`, `mixed` or `unclear` role, in either kind.", ""]
    need = [(t, e) for t, e in gated.items()
            if any(e[k]["role"] in ("commentary", "alternate", "mixed", "unclear") for k in ("brackets", "parens") if k in e)]
    need.sort(key=lambda te: -max(te[1]["brackets"]["pct_of_edition"], te[1]["parens"]["pct_of_edition"]))
    lines.append(f"## Editions needing a look ({len(need)})")
    lines.append("")
    for tag, e in need:
        lines.append(f"### {tag}")
        if tag in decisions:
            lines.append(f"> Decision already on file in `config/text_strip_rules.json`: {decisions[tag]}")
        lines.append(f"{e['words']:,} words, script {e['script']}.")
        for kind, label in (("brackets", "Brackets `[...]`"), ("parens", "Parentheses `(...)`")):
            k = e.get(kind)
            if not k or not k["n_spans"]:
                continue
            ar = "n/a" if k["align_ratio"] is None else f"{k['align_ratio']:.2f}"
            so = "n/a" if k["sibling_overlap"] is None else f"{k['sibling_overlap']:.2f}"
            lines.append(f"- **{label}: {k['role']}**. {k['n_spans']} spans, {k['n_words']} words "
                         f"({k['pct_of_edition']:.2f}% of the edition), median {k['median_words']:g} / 90th pct {k['p90_words']} / "
                         f"max {k['max_words']} words per span; {k['pct_short']:.0%} short, {k['pct_clause']:.0%} clause-like, "
                         f"{k['pct_digits']:.0%} with digits. Alignment inside/outside {ar}, sibling overlap {so}. "
                         f"Repeats its verse: {k['pct_dup']:.0%} of {k['n_dup_eval']} evaluable spans "
                         f"({k['pct_alt_of_edition']:.2f}% of the edition's words). "
                         f"Why: {'; '.join(k['reasons'])}.")
            for smp in (k["alt_samples"] if k["role"] == "alternate" and k.get("alt_samples") else k["samples"])[:3]:
                lines.append(f"  - `{smp}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usj-root", type=Path, default=Path("pipeline/work/ingest-cache"))
    ap.add_argument("--min-pct", type=float, default=0.1,
                     help="only report editions where brackets or parens are >= this %% of total words")
    ap.add_argument("--samples", type=int, default=5, help="sample spans shown per bracket/paren section")
    ap.add_argument("--out", type=Path, default=Path("config/text_strip_report.md"))
    ap.add_argument("--rules", type=Path, default=Path("config/text_strip_rules.json"))
    ap.add_argument("--out-json", type=Path, default=Path("config/text_strip_evidence.json"))
    ap.add_argument("--alignments", type=Path, default=OUT, help="directory with align_eflomal_<tag>_<BOOK>.jsonl(.gz)")
    ap.add_argument("--exclude", default=",".join(EXCLUDE_DEFAULT),
                    help="comma-separated edition tags left out of the analysis (default: Svenska Kärnbibeln, both tags)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated edition tags to analyse (testing)")
    ap.add_argument("--no-cache-reuse", action="store_true", help="re-analyse every edition even if a cached result exists")
    ap.add_argument("--legacy", action="store_true", help="the old single-yardstick report (kept for comparison)")
    args = ap.parse_args()

    decisions = {}
    if args.rules.exists():
        import json
        decisions = json.loads(args.rules.read_text(encoding="utf-8")).get("editions", {})

    if args.legacy:
        args.out.write_text(build_report(args.usj_root, args.min_pct, args.samples, decisions), encoding="utf-8")
        print(f"wrote {args.out} (legacy report)", file=sys.stderr)
        return 0
    exclude = {t for t in args.exclude.split(",") if t}
    only = {t for t in args.only.split(",") if t}
    evid = build_evidence(args.usj_root, args.alignments, args.min_pct, exclude, sorted(decisions), args.workers,
                          not args.no_cache_reuse, only or None)
    if only:                                       # a test run never overwrites the real report or evidence file
        args.out = args.out.with_name("text_strip_report.test.md")
        args.out_json = args.out_json.with_name("text_strip_evidence.test.json")
    doc = {"_doc": ("Per-edition evidence for the meaning of [...] and (...) spans, computed separately for brackets and "
                    "parentheses (lexeme_aligner/span_roles.py explains each field and role). Evidence only, never a decision. "
                    f"Excluded by hand: {sorted(exclude)}."),
           "editions": {t: {k: v for k, v in e.items() if k not in ("version", "tag")} for t, e in evid.items()}}
    args.out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    args.out.write_text(render_evidence_report(evid, exclude, args.min_pct, {k: {f: v for f, v in d.items() if f != "reason"}
                                                                               for k, d in decisions.items()}),
                        encoding="utf-8")
    print(f"wrote {args.out} and {args.out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
