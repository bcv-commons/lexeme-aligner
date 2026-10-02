"""UBS sense layer: bind the UBS Dictionary of Biblical Hebrew's per-sense Scripture references to our spine tokens.

WHY. The UBS Dictionary of Biblical Hebrew (most of SDBH, © United Bible Societies, CC BY-SA 4.0,
github.com/ubsicap/ubs-open-license/dictionaries/hebrew) is a manually built, academically trusted sense inventory
with 16.6k senses and 288k per-occurrence Scripture references over all 39 OT books. Our own spine `sense` number is
'1' for 97% of tokens and agrees with UBS no better than chance when it says "same sense"
(internal-docs/aim1-three-track-evaluation-plan.md §8.11), so UBS is the reference and the sense layer is bound to it.

INPUT (out-of-band, never committed): the UBS JSON, `pipeline/work/ubs-dictionary/UBSHebrewDic-v0.9.3-en.JSON` (or
`--ubs`). OUTPUT (local, gitignored, like the spine): `pipeline/ubs-senses.db` — see `config/PROVENANCE.txt` for the pin.
Everything derived from it carries the share-alike obligation (CC BY-SA 4.0, credit "UBS Dictionary of Biblical Hebrew ©
United Bible Societies").

REFERENCE FORMAT. 14 digits BBBCCCVVV + W (5 digits). W is 2 x the 1-based word slot n of SDBH's tokenisation, which
splits prefixes but NOT pronominal suffixes, while our spine also splits suffixes: `idx - (n-1)` (the "offset") is the
number of suffix tokens before the word, so it is >= 0 and NON-DECREASING along the verse. Binding:
  1. candidates = tokens of the same verse whose Strong's is in the entry's codes OR whose lemma consonants equal the
     entry's lemma (or an alternate lemma) — ANY token, content or function word (UBS sense-tags prepositions too);
  2. a reference with exactly one candidate is an ANCHOR ('unique'); anchors whose offsets break monotonicity are dropped
     (longest non-decreasing subsequence);
  3. every other reference keeps the candidates whose offset lies between the neighbouring anchors' offsets; exactly one
     left -> 'anchored', several -> the smallest offset ('nearest'), none -> unbound.
`how` is stored per token so consumers can filter (unique > anchored > nearest).

    python3 -m lexeme_aligner.ubs_senses --build
    python3 -m lexeme_aligner.ubs_senses --report
"""
from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from lexeme_aligner.config import SPINE_DB, _p, _PIPELINE

UBS_JSON = Path("pipeline/work/ubs-dictionary/UBSHebrewDic-v0.9.3-en.JSON")
UBS_SENSES_DB = _p("ALIGNER_UBS_SENSES_DB", _PIPELINE / "ubs-senses.db")
UBS_VERSION = "UBSHebrewDic-v0.9.3"
MAX_OFFSET_SPAN = 12          # cap on the suffix-token offset when no later anchor bounds it
BOOKS = ("GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM EZK DAN "
         "HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL").split()


def cons(s: str | None) -> str:
    """Hebrew consonants only (niqqud, cantillation, maqaf and spacing removed)."""
    return "".join(ch for ch in unicodedata.normalize("NFD", s or "") if "א" <= ch <= "ת")


def _strongs(codes) -> set[int]:
    return {int(re.sub(r"\D", "", c)) for c in (codes or []) if re.sub(r"\D", "", c)}


def load_dictionary(path: Path = UBS_JSON):
    """(senses, refs). senses: {lex_id: {main_id, lemma, strongs, gloss, definition, domains}};
    refs: [(book, ch, v, n_slot, main_id, lex_id)] for every 14-digit reference (21/22-digit range refs are skipped)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    senses: dict[str, dict] = {}
    refs = []
    entry = {}
    for e in d:
        lemmas = {cons(e["Lemma"])} | {cons(a if isinstance(a, str) else (a or {}).get("Lemma", ""))
                                       for a in (e.get("AlternateLemmas") or [])}
        entry[e["MainId"]] = {"strongs": _strongs(e["StrongCodes"]), "lemmas": {x for x in lemmas if x}}
        for bf in e["BaseForms"] or []:
            for m in bf["LEXMeanings"] or []:
                s = (m.get("LEXSenses") or [{}])[0]
                senses[m["LEXID"]] = {
                    "main_id": e["MainId"], "lemma": e["Lemma"], "strongs": sorted(entry[e["MainId"]]["strongs"]),
                    "gloss": ", ".join(s.get("Glosses") or []), "definition": s.get("DefinitionShort") or "",
                    "domains": [x.get("DomainCode") for x in (m.get("LEXDomains") or [])]}
                for r in m.get("LEXReferences") or []:
                    if len(r) == 14:
                        refs.append((BOOKS[int(r[:3]) - 1], int(r[3:6]), int(r[6:9]), int(r[9:]) // 2,
                                     e["MainId"], m["LEXID"]))
    return senses, refs, entry


def _lnds(offsets: list[int]) -> list[int]:
    """Indices of a longest non-decreasing subsequence of `offsets`."""
    tails: list[int] = []
    tail_idx: list[int] = []
    prev = [-1] * len(offsets)
    for i, x in enumerate(offsets):
        j = bisect.bisect_right(tails, x)
        if j == len(tails):
            tails.append(x)
            tail_idx.append(i)
        else:
            tails[j], tail_idx[j] = x, i
        prev[i] = tail_idx[j - 1] if j else -1
    out, k = [], tail_idx[-1] if tail_idx else -1
    while k != -1:
        out.append(k)
        k = prev[k]
    return out[::-1]


def bind_verse(refs, tokens, entry) -> tuple[dict[int, tuple[str, str, str]], collections.Counter]:
    """refs: [(n_slot, main_id, lex_id)] of ONE verse; tokens: [{idx, strong, lemma_c}].
    -> ({token idx: (lex_id, main_id, how)}, stats)."""
    stats: collections.Counter = collections.Counter()
    cand: list[list[int]] = []
    for n, main_id, _lex in refs:
        e = entry[main_id]
        cand.append([t["idx"] for t in tokens if t["strong"] in e["strongs"] or t["lemma_c"] in e["lemmas"]])
    order = sorted(range(len(refs)), key=lambda i: refs[i][0])
    anchors = [i for i in order if len(cand[i]) == 1]
    keep = _lnds([cand[i][0] - (refs[i][0] - 1) for i in anchors])
    anchor_set = {anchors[k] for k in keep}
    stats["anchors_dropped_nonmonotone"] += len(anchors) - len(anchor_set)
    a_sorted = sorted(anchor_set, key=lambda i: refs[i][0])
    a_n = [refs[i][0] for i in a_sorted]
    a_off = [cand[i][0] - (refs[i][0] - 1) for i in a_sorted]
    bound: dict[int, tuple[str, str, str]] = {}

    def claim(i, idx, how):
        if idx in bound and bound[idx][0] != refs[i][2]:
            stats["token claimed by two senses (first kept)"] += 1
            return
        bound[idx] = (refs[i][2], refs[i][1], how)
        stats[how] += 1

    for i in order:
        n = refs[i][0]
        if i in anchor_set:
            claim(i, cand[i][0], "unique")
            continue
        j = bisect.bisect_right(a_n, n)                       # anchors with slot <= n are before/at, the rest after
        lo = a_off[j - 1] if j else 0
        hi = a_off[j] if j < len(a_off) else lo + MAX_OFFSET_SPAN
        ok = [x for x in cand[i] if lo <= x - (n - 1) <= hi]
        if len(ok) == 1:
            claim(i, ok[0], "anchored")
        elif ok:
            claim(i, min(ok, key=lambda x: x - (n - 1)), "nearest")
        else:
            stats["unbound"] += 1
    return bound, stats


def bind_all(refs, entry, spine_db: Path = SPINE_DB):
    con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
    verse: dict[tuple, list[dict]] = collections.defaultdict(list)
    for b, c, v, i, st, lemma, lex in con.execute(
            "SELECT book,chapter,verse,idx,strong,lemma,lexeme FROM spine_words WHERE lexeme LIKE 'hbo:%' ORDER BY idx"):
        verse[(b, c, v)].append({"idx": i, "strong": st, "lemma_c": cons(lemma), "lexeme": lex})
    con.close()
    by_verse: dict[tuple, list] = collections.defaultdict(list)
    for b, c, v, n, main_id, lex_id in refs:
        by_verse[(b, c, v)].append((n, main_id, lex_id))
    out: dict[tuple, tuple] = {}
    stats: collections.Counter = collections.Counter()
    for key, rs in by_verse.items():
        toks = verse.get(key)
        if not toks:
            stats["verse missing in spine"] += len(rs)
            continue
        bound, st = bind_verse(rs, toks, entry)
        stats.update(st)
        lexeme_of = {t["idx"]: t["lexeme"] for t in toks}
        for idx, val in bound.items():
            out[key + (idx,)] = val + (lexeme_of[idx],)
    stats["references"] = len(refs)
    stats["tokens_bound"] = len(out)
    return out, stats


def write_db(dest: Path, senses: dict, bound: dict, ubs_path: Path) -> None:
    dest = Path(dest)
    if dest.exists():
        dest.unlink()
    con = sqlite3.connect(dest)
    con.executescript("""
        CREATE TABLE sense (lex_id TEXT PRIMARY KEY, main_id TEXT, lemma TEXT, strongs TEXT, gloss_en TEXT,
                            definition_en TEXT, domains TEXT);
        CREATE TABLE token_sense (book TEXT, chapter INT, verse INT, idx INT, lex_id TEXT, main_id TEXT, how TEXT,
                                  lexeme TEXT, PRIMARY KEY (book, chapter, verse, idx));
        CREATE TABLE ubs_meta (key TEXT PRIMARY KEY, value TEXT);""")
    con.executemany("INSERT INTO sense VALUES (?,?,?,?,?,?,?)",
                    [(k, v["main_id"], v["lemma"], json.dumps(v["strongs"]), v["gloss"], v["definition"],
                      json.dumps(v["domains"])) for k, v in senses.items()])
    con.executemany("INSERT INTO token_sense VALUES (?,?,?,?,?,?,?,?)",
                    [k + v for k, v in sorted(bound.items())])
    meta = {"source": "UBS Dictionary of Biblical Hebrew (SDBH extract), github.com/ubsicap/ubs-open-license",
            "version": UBS_VERSION, "license": "CC-BY-SA-4.0",
            "credit": "UBS Dictionary of Biblical Hebrew © United Bible Societies 2023; adapted from SDBH © 2000-2023 UBS",
            "source_sha256": hashlib.sha256(Path(ubs_path).read_bytes()).hexdigest(),
            "spine_sha256": sqlite3.connect(f"file:{SPINE_DB}?mode=ro", uri=True).execute(
                "SELECT value FROM spine_meta WHERE key='macula_spine_sha256'").fetchone()[0]}
    con.executemany("INSERT INTO ubs_meta VALUES (?,?)", meta.items())
    con.commit()
    con.close()


def load_token_senses(db: Path | None = None, min_how: tuple[str, ...] = ("unique", "anchored", "nearest"),
                      with_lexeme: bool = False) -> dict[tuple, str | tuple[str, str]]:
    """{(book, chapter, verse, idx): UBS sense id} (or (sense id, spine lexeme) with `with_lexeme`) for tokens bound with
    a `how` in `min_how`; {} if the DB is absent (never an error — the sense layer is optional)."""
    db = Path(db or UBS_SENSES_DB)
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(min_how))
        return {(b, c, v, i): ((lx, lexeme) if with_lexeme else lx) for b, c, v, i, lx, lexeme in con.execute(
            f"SELECT book,chapter,verse,idx,lex_id,lexeme FROM token_sense WHERE how IN ({q})", min_how)}
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ubs", type=Path, default=UBS_JSON)
    ap.add_argument("--db", type=Path, default=UBS_SENSES_DB)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if not (a.build or a.report):
        ap.error("pass --build or --report")
    senses, refs, entry = load_dictionary(a.ubs)
    bound, stats = bind_all(refs, entry)
    print(json.dumps(dict(stats), indent=1), file=sys.stderr)
    if a.build:
        write_db(a.db, senses, bound, a.ubs)
        print(f"[ubs_senses] wrote {a.db}: {len(senses)} senses, {len(bound)} tokens", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
