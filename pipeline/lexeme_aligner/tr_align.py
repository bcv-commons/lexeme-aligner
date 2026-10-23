"""Precision-first word alignment of a TR-basis edition against the Textus Receptus spine -> `bcv-commons/alignments-tr`.

WHY (2026-10-23). bcv-query builds an NT clause layout on MACULA Greek and checks it with Salkinson-Ginsburg (heb_scg, public
domain, translated from the TR). They need each Hebrew word linked back to the TR words, precision before coverage ("a Greek
word left unlinked is better than a wrong link"), keyed by the TR spine's own word ids (`grc-tr:MAT:1` ...) with the spine
version pinned. compact-alignments cannot carry this: its srcOrd counts content tokens of the Nestle 1904 spine.

METHOD
  1. eflomal against `pipeline/tr-textus-receptus-spine.db`, run N times (eflomal is seeded from /dev/urandom, so runs differ).
  2. Candidate links from all runs, mapped from align-jsonl positions to TR word keys through `HebrewSource.verse_tokens`
     (positions shift after a merged token such as ἀμὴν ἀμήν, and a pooled verse continues into the next verses).
  3. LXX bridge, independent of eflomal: the target word's pointed form -> MACULA OT word forms (prefixes joined, accents
     dropped, vowels kept) -> the most frequent analysis's content lexemes -> prior-pack `lxx_greek` Strong's.
  4. Tiers kept (anything else stays unlinked):
       stable  link present in every run (content or function word)
       rescue  content link in fewer runs that the bridge confirms; the best-voted one per Greek word, ties dropped
       bridge  content Greek word still unlinked whose Strong's is unique in the verse and in the bridge set of exactly one
               target word not already taken by a content link
     Never linked: a verse's leading verse-number token (Salkinson writes the number as Hebrew letters, א, יז ...).
  Hand check 2026-10-23 (heb_scg, 5 runs, 25 links per group): stable content 73/75, stable function 24/25, rescue ~23/25,
  bridge ~23/25; links in 3-4 runs without bridge support were ~14/25 (content) and ~16/25 (function) and are dropped.

LAYOUT (publish/alignments-tr, mirrors compact-alignments paths)
  <iso[0]>/<iso>/<edition>/<BOOK>_<hash>.json   {"C:V": [[[target positions], [TR keys], tier], ...]}  keyed by the
                                                 edition's verse; positions = tokenizer v2 tokens of that verse
  manifest.json                                  spine pin, tokenizer/fingerprint versions, per edition counts

    python -m lexeme_aligner.tr_align --edition heb_scg --iso heb --runs 5          # align + build
    python -m lexeme_aligner.tr_align --edition heb_scg --iso heb --skip-align       # rebuild from existing runs
        [--analysis <hebrew-nt-analysis>/heb_scg/words.parquet]                      # bcv-query's word analysis, adds to the bridge
    python -m lexeme_aligner.tr_align --publish [--push]                             # dry run unless --push
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import subprocess
import sys
import unicodedata as ud
from pathlib import Path

from lexeme_aligner import text_fingerprint as tf
from lexeme_aligner.usj_source import TOKENIZER_VERSION, read_verses, tokenize

REPO = Path(__file__).resolve().parents[2]
TR_SPINE = REPO / "pipeline/tr-textus-receptus-spine.db"
OT_SPINE = REPO / "pipeline/lexeme-spine-macula.db"
PRIOR_PACK = REPO / "pipeline/vendor/prior-pack/prior_pack.parquet"
WORK = REPO / "pipeline/work/tr-align"
OUT = REPO / "publish/alignments-tr"
HF_REPO = "bcv-commons/alignments-tr"
FORMAT_VERSION = 1
ANALYSIS_REVISION = "v0.1"     # the bcv-commons/hebrew-nt-analysis tag the --analysis file comes from
NT_FIRST = 40


def usj_dir_of(edition: str) -> Path:
    return REPO / f"pipeline/work/ingest-cache/usj-{edition}"


def books_of(usj_dir: Path) -> dict[str, Path]:
    """NT books of an ingested edition: {BOOK: usj path} (files are named NN-BOOK.json)."""
    out = {}
    for p in sorted(usj_dir.glob("*.json")):
        num, _, rest = p.stem.partition("-")
        if num.isdigit() and int(num) >= NT_FIRST:
            out[rest[:3]] = p
    return out


def spine_meta(spine: Path = TR_SPINE) -> dict[str, str]:
    con = sqlite3.connect(f"file:{spine}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT key, value FROM spine_meta"))
    finally:
        con.close()


# ---------------------------------------------------------------- 1. eflomal runs

def align(edition: str, iso: str, runs: int, work: Path) -> None:
    env = dict(os.environ, ALIGNER_SPINE_DB=str(TR_SPINE))
    for i in range(1, runs + 1):
        rd = work / f"run{i}"
        if any(rd.glob("align_eflomal_*.jsonl")):
            print(f"[tr_align] run {i}: exists, kept", file=sys.stderr)
            continue
        rd.mkdir(parents=True, exist_ok=True)
        env["ALIGNER_OUT"] = str(rd)
        print(f"[tr_align] run {i}/{runs}", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "lexeme_aligner.run_pilot", "--method", "eflomal", "--nt",
                        "--usj-dir", str(usj_dir_of(edition)), "--iso", edition, "--publish-iso", iso, "--out", str(rd)],
                       check=True, env=env, cwd=REPO, stdout=(rd / "run.log").open("w"), stderr=subprocess.STDOUT)


# ---------------------------------------------------------------- 2. LXX bridge

_DROP = set(range(0x591, 0x5B0)) | {0x5BD, 0x5BF, 0x5C0, 0x5C3, 0x5C4, 0x5C5}   # cantillation, meteg, rafe, paseq, marks


def pointed(s: str) -> str:
    """A Hebrew word with its vowel points but without accents (MACULA carries cantillation, translations do not)."""
    return ud.normalize("NFC", "".join(c for c in ud.normalize("NFC", s) if ord(c) not in _DROP))


def ot_forms(spine: Path = OT_SPINE) -> dict[str, tuple[str, ...]]:
    """Pointed whole-word form (prefixes joined) -> lexemes of its most frequent MACULA analysis."""
    con = sqlite3.connect(f"file:{spine}?mode=ro", uri=True)
    words: dict[str, list] = collections.defaultdict(list)
    for key, surf, lx in con.execute("SELECT key, surface, lexeme FROM spine_words WHERE substr(key,1,2) < '40' ORDER BY key"):
        words[key[:-1]].append((surf, lx))           # the last key digit numbers the parts of one word
    con.close()
    forms: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for parts in words.values():
        forms[pointed("".join(s for s, _ in parts))][tuple(lx for _, lx in parts)] += 1
    return {f: c.most_common(1)[0][0] for f, c in forms.items()}


def lxx_bridge(prior_pack: Path = PRIOR_PACK) -> dict[str, set[int]]:
    """Content Hebrew lexeme -> Greek Strong's numbers prior-pack links it to through the LXX."""
    import pyarrow.parquet as pq
    out = {}
    for r in pq.read_table(prior_pack, columns=["lexeme", "is_content", "lxx_greek"]).to_pylist():
        if r["is_content"] and r["lxx_greek"]:
            out[r["lexeme"]] = {int(g[1:]) for g in r["lxx_greek"]}
    return out


def numeral(n: int) -> str:
    """Hebrew letter numeral as a verse number is written (15 = טו, 16 = טז), without geresh."""
    out = ""
    for val, ch in ((400, "ת"), (300, "ש"), (200, "ר"), (100, "ק")):
        while n >= val:
            out, n = out + ch, n - val
    if n in (15, 16):
        return out + ("טו" if n == 15 else "טז")
    if n >= 10:
        out, n = out + "_יכלמנסעפצ"[n // 10], n % 10
    return out + ("_אבגדהוזחט"[n] if n else "")


def load_analysis(words_parquet: Path, min_p_split: float = 0.0) -> dict[tuple, tuple[str, ...]]:
    """bcv-query's hebrew-nt-analysis `words.parquet` (one row per word part, `i` = our token number) -> (book, chapter,
    verse, i) -> MACULA lexemes of the word's parts. A tagger word (`source` = model) is used only when its split
    confidence `p_split` is at least `min_p_split`; parts without a lexeme are skipped."""
    import pyarrow.parquet as pq
    rows = pq.read_table(words_parquet, columns=["book", "chapter", "verse", "i", "part", "lexeme", "source", "p_split"]).to_pylist()
    ok = {(r["book"], r["chapter"], r["verse"], r["i"]) for r in rows
          if r["part"] == 0 and (r["source"] == "macula" or (r["source"] == "model" and (r["p_split"] or 0) >= min_p_split))}
    out: dict = collections.defaultdict(list)
    for r in rows:
        k = (r["book"], r["chapter"], r["verse"], r["i"])
        if k in ok and r["lexeme"]:
            out[k].append(r["lexeme"])
    return {k: tuple(v) for k, v in out.items()}


# ---------------------------------------------------------------- 3. collect + select

class TrPositions:
    """align-jsonl source position -> TR word keys (one token may hold several merged words)."""

    def __init__(self, spine: Path = TR_SPINE):
        from lexeme_aligner.hebrew_source import HebrewSource
        self.src = HebrewSource(spine_db=spine)
        self._v: dict = {}

    def tokens(self, b, c, v):
        if (b, c, v) not in self._v:
            self._v[(b, c, v)] = self.src.verse_tokens(b, c, v)
        return self._v[(b, c, v)]

    def __call__(self, b, c, v, pos):
        while True:
            toks = self.tokens(b, c, v)
            if not toks:
                return None
            if pos < len(toks):
                return toks[pos]
            pos, v = pos - len(toks), v + 1


def select(edition: str, work: Path, verses: dict[str, dict], forms=None, bridge=None, pos=None, analysis=None) -> list[dict]:
    """The kept links: dicts b, c, v (edition verse), t (target positions), keys (TR), tier. `analysis` ((b, c, v, i) ->
    lexemes of the word's parts, see `load_analysis`) adds to the MACULA form lookup: the bridge set of a word is the union
    of both (measured on heb_scg 2026-10-23: either one alone loses links the other finds, both sets of extra links ~23/25 right)."""
    forms = ot_forms() if forms is None else forms
    analysis = analysis or {}
    bridge = lxx_bridge() if bridge is None else bridge
    pos = TrPositions() if pos is None else pos
    toks = {(b, c, v): tokenize(t) for b, vs in verses.items() for (c, v), t in vs.items()}
    pts = {(b, c, v): [pointed(w) for w in _pointed_words(t)] for b, vs in verses.items() for (c, v), t in vs.items()}

    def gset(k, i):
        w = pts[k][i] if i < len(pts[k]) else None
        lxs = set(forms.get(w, ())) | set(analysis.get(k + (i,), ()))
        return set().union(*(bridge.get(lx, ()) for lx in lxs)) if (w in forms or k + (i,) in analysis) else None

    runs = sorted(work.glob("run[0-9]*"))
    votes: dict = collections.Counter()
    for rd in runs:
        for f in rd.glob(f"align_eflomal_{edition}_*.jsonl"):
            for line in f.open(encoding="utf-8"):
                r = json.loads(line)
                for p in r["pairs"]:
                    votes[(r["book"], r["chapter"], r["verse"], p["h_idx"], tuple(p["t_idx"]))] += 1
    n_runs = len(runs)
    kept: dict = {}
    rescue: dict = collections.defaultdict(list)
    for (b, c, v, h, ts), n in votes.items():
        k = (b, c, v)
        if k not in toks or (toks[k] and toks[k][0] == numeral(v) and 0 in ts):
            continue
        tok = pos(b, c, v, h)
        if tok is None:
            continue
        if n == n_runs:
            kept.setdefault((b, c, v, h), []).append(dict(b=b, c=c, v=v, t=list(ts), keys=tok.keys, tier="stable",
                                                         content=tok.is_content))
        elif tok.is_content and tok.strong:
            st = int(tok.strong[1:])
            if any(st in (gset(k, i) or ()) for i in ts):
                rescue[(b, c, v, h)].append((n, ts, tok))
    for kh, cands in rescue.items():
        if kh in kept:
            continue
        cands.sort(key=lambda x: -x[0])
        if len(cands) > 1 and cands[0][0] == cands[1][0]:
            continue
        n, ts, tok = cands[0]
        kept[kh] = [dict(b=kh[0], c=kh[1], v=kh[2], t=list(ts), keys=tok.keys, tier="rescue", content=True)]
    taken = collections.defaultdict(set)
    for ls in kept.values():
        for l in ls:
            if l["content"]:
                taken[(l["b"], l["c"], l["v"])].update(l["t"])
    for k in toks:
        b, c, v = k
        src = pos.tokens(b, c, v)
        cnt = collections.Counter(t.strong for t in src if t.is_content and t.strong)
        for h, tok in enumerate(src):
            if not tok.is_content or not tok.strong or (b, c, v, h) in kept or cnt[tok.strong] != 1:
                continue
            st = int(tok.strong[1:])
            hits = [i for i in range(len(toks[k])) if st in (gset(k, i) or ())]
            if len(hits) == 1 and hits[0] not in taken[k] and not (hits[0] == 0 and toks[k][0] == numeral(v)):
                kept[(b, c, v, h)] = [dict(b=b, c=c, v=v, t=hits, keys=tok.keys, tier="bridge", content=True)]
                taken[k].add(hits[0])
    return [l for ls in kept.values() for l in ls]


def _pointed_words(text: str) -> list[str]:
    """The verse's words WITH their marks, position-parallel to tokenize(text)."""
    from lexeme_aligner.usj_source import _token_spans
    return [text[a:z] for a, z in _token_spans(text)]


# ---------------------------------------------------------------- 4. files

def build(edition: str, iso: str, work: Path, out: Path = OUT, analysis: Path | None = None, min_p_split: float = 0.9) -> dict:
    books = books_of(usj_dir_of(edition))
    verses = {b: read_verses(p) for b, p in books.items()}
    for b, p in books.items():                     # positions must index the published (raw) text
        raw = tf.usj_verses(p)
        if any(tokenize(raw.get(k, "")) != tokenize(t) for k, t in verses[b].items()):
            raise SystemExit(f"[tr_align] {edition} {b}: strip rules change the tokens; positions would need a remap")
    links = select(edition, work, verses, analysis=load_analysis(analysis, min_p_split) if analysis else None)
    edir = out / iso[0] / iso / edition
    edir.mkdir(parents=True, exist_ok=True)
    for old in edir.glob("*.json"):
        old.unlink()
    by_book: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for l in links:
        by_book[l["b"]][f'{l["c"]}:{l["v"]}'].append([sorted(l["t"]), l["keys"], l["tier"]])
    tiers, words = collections.Counter(l["tier"] for l in links), sum(len(l["keys"]) for l in links)
    for b, vs in by_book.items():
        doc = {cv: sorted(rows, key=lambda r: (r[0], r[1])) for cv, rows in
               sorted(vs.items(), key=lambda kv: tuple(int(x) for x in kv[0].split(":")))}
        (edir / f"{b}_{tf.book_file_hash(books[b])}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
                                                                      encoding="utf-8")
    meta = spine_meta()
    total = int(meta.get("words", 0))
    entry = dict(books=sorted(by_book), links=len(links), tr_words_linked=words, tr_words=total, tiers=dict(sorted(tiers.items())),
                 runs=len(list(work.glob("run[0-9]*"))), built=tf.today())
    if analysis:
        import hashlib
        entry["analysis"] = dict(dataset="bcv-commons/hebrew-nt-analysis", revision=ANALYSIS_REVISION, file=f"{edition}/{analysis.name}",
                                 sha256=hashlib.sha256(analysis.read_bytes()).hexdigest(), min_p_split=min_p_split)
    man_p = out / "manifest.json"
    man = json.loads(man_p.read_text(encoding="utf-8")) if man_p.exists() else {"languages": {}}
    man.update(format_version=FORMAT_VERSION, tokenizer_version=TOKENIZER_VERSION, text_fp_version=tf.TEXT_FP_VERSION,
               spine=dict(file=TR_SPINE.name, edition=meta.get("edition", "").split(" ")[0],
                          spine_content_sha256=meta.get("spine_content_sha256"), source_sha256=meta.get("source_sha256"),
                          words=total))
    man["languages"].setdefault(iso, {"editions": {}})["editions"][edition] = entry
    man_p.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[tr_align] {edition}: {len(links)} links, {words}/{total} TR words ({words / total:.1%}), tiers {dict(tiers)}",
          file=sys.stderr)
    return entry


def publish(push: bool, out: Path = OUT) -> None:
    from lexeme_aligner.hf_bulk_publish import install_timeout, publish_chunked
    install_timeout()
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and not p.name.startswith("."))
    publish_chunked(out, HF_REPO, files, create=True, dry_run=not push, label="alignments-tr", detect_deletions=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition")
    ap.add_argument("--iso")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--skip-align", action="store_true")
    ap.add_argument("--analysis", type=Path, help="hebrew-nt-analysis words.parquet for this edition (adds to the bridge)")
    ap.add_argument("--min-p-split", type=float, default=0.9)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    if a.edition:
        work = WORK / a.edition
        if not a.skip_align:
            align(a.edition, a.iso, a.runs, work)
        build(a.edition, a.iso, work, analysis=a.analysis, min_p_split=a.min_p_split)
    if a.publish:
        publish(a.push)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
