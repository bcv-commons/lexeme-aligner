"""UBS Greek NT sense layer (plan internal-docs/grammar-word-level-plan-2026-10-08.md, S1): bind the UBS Dictionary of New Testament
Greek's per-sense Scripture references (SDGNT / Louw-Nida based, © United Bible Societies, CC BY-SA 4.0,
github.com/ubsicap/ubs-open-license/dictionaries/greek) to our NT spine tokens — the Greek counterpart of `ubs_senses` (Hebrew).

Same reference format (14 digits BBBCCCVVV + W, W = 2 x the 1-based word slot of UBS's own Greek text) and the same binding
(`ubs_senses.bind_verse`: candidates by Strong's or lemma within the verse, unique references anchor a monotone slot offset, the rest
are placed between anchors). UBS's Greek text is not Nestle 1904, so the offset absorbs text differences the same way it absorbs
Hebrew suffix tokens. Lemmas are compared without accents/breathings.

INPUT (out-of-band): pipeline/work/ubs-dictionary/UBSGreekNTDic-v1.1-en.JSON. OUTPUT: tables `grc_sense` / `grc_token_sense` in
pipeline/ubs-senses.db (same gitignored, share-alike file as the Hebrew layer; the Hebrew tables are left untouched).

    python3 -m lexeme_aligner.ubs_greek_senses --report      # binding statistics only
    python3 -m lexeme_aligner.ubs_greek_senses --build
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path

from lexeme_aligner.config import SPINE_DB
from lexeme_aligner.ubs_senses import UBS_SENSES_DB, _strongs, bind_verse

UBS_GREEK_JSON = Path("pipeline/work/ubs-dictionary/UBSGreekNTDic-v1.1-en.JSON")
UBS_GREEK_VERSION = "UBSGreekNTDic-v1.1"
NT_BOOKS = "MAT MRK LUK JHN ACT ROM 1CO 2CO GAL EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JN 2JN 3JN JUD REV".split()


def gnorm(s: str | None) -> str:
    """Greek letters only, lower case, without accents/breathings/diaeresis (final sigma folded)."""
    out = "".join(ch for ch in unicodedata.normalize("NFD", s or "").lower() if unicodedata.category(ch)[0] == "L")
    return out.replace("ς", "σ")


def load_dictionary(path: Path = UBS_GREEK_JSON):
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    senses, refs, entry = {}, [], {}
    for e in d:
        lemmas = {gnorm(e["Lemma"])} | {gnorm(a if isinstance(a, str) else (a or {}).get("Lemma", "")) for a in (e.get("AlternateLemmas") or [])}
        entry[e["MainId"]] = {"strongs": _strongs(e["StrongCodes"]), "lemmas": {x for x in lemmas if x}}
        for bf in e["BaseForms"] or []:
            for m in bf["LEXMeanings"] or []:
                s = next((x for x in (m.get("LEXSenses") or []) if x.get("LanguageCode") == "en"), (m.get("LEXSenses") or [{}])[0])
                senses[m["LEXID"]] = {"main_id": e["MainId"], "lemma": e["Lemma"], "strongs": sorted(entry[e["MainId"]]["strongs"]),
                                      "gloss": ", ".join(s.get("Glosses") or []), "definition": s.get("DefinitionShort") or "",
                                      "ln": m.get("LEXEntryCode"), "domains": [x.get("DomainCode") for x in (m.get("LEXDomains") or [])]}
                for r in m.get("LEXReferences") or []:
                    b = int(r[:3]) if len(r) == 14 else 0
                    if 40 <= b <= 66:
                        refs.append((NT_BOOKS[b - 40], int(r[3:6]), int(r[6:9]), int(r[9:]) // 2, e["MainId"], m["LEXID"]))
    return senses, refs, entry


def bind_all(refs, entry, spine_db: Path = SPINE_DB):
    con = sqlite3.connect(f"file:{spine_db}?mode=ro", uri=True)
    verse: dict[tuple, list[dict]] = collections.defaultdict(list)
    for b, c, v, i, st, lemma, lex in con.execute(
            "SELECT book,chapter,verse,idx,strong,lemma,lexeme FROM spine_words WHERE lexeme LIKE 'grc:%' ORDER BY idx"):
        verse[(b, c, v)].append({"idx": i, "strong": st, "lemma_c": gnorm(lemma), "lexeme": lex})
    con.close()
    by_verse: dict[tuple, list] = collections.defaultdict(list)
    for b, c, v, n, main_id, lex_id in refs:
        by_verse[(b, c, v)].append((n, main_id, lex_id))
    out, stats = {}, collections.Counter()
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
    stats["nt_tokens_in_spine"] = sum(len(v) for v in verse.values())
    return out, stats


def write_tables(db: Path, senses: dict, bound: dict, src: Path) -> None:
    con = sqlite3.connect(db)
    con.executescript("""
        DROP TABLE IF EXISTS grc_sense; DROP TABLE IF EXISTS grc_token_sense; DROP TABLE IF EXISTS grc_ubs_meta;
        CREATE TABLE grc_sense (lex_id TEXT PRIMARY KEY, main_id TEXT, lemma TEXT, strongs TEXT, ln TEXT, gloss_en TEXT,
                                definition_en TEXT, domains TEXT);
        CREATE TABLE grc_token_sense (book TEXT, chapter INT, verse INT, idx INT, lex_id TEXT, main_id TEXT, how TEXT, lexeme TEXT,
                                      PRIMARY KEY (book, chapter, verse, idx));
        CREATE TABLE grc_ubs_meta (key TEXT PRIMARY KEY, value TEXT);""")
    con.executemany("INSERT INTO grc_sense VALUES (?,?,?,?,?,?,?,?)",
                    [(k, v["main_id"], v["lemma"], json.dumps(v["strongs"]), v["ln"], v["gloss"], v["definition"],
                      json.dumps(v["domains"])) for k, v in senses.items()])
    con.executemany("INSERT INTO grc_token_sense VALUES (?,?,?,?,?,?,?,?)", [k + v for k, v in sorted(bound.items())])
    meta = {"source": "UBS Dictionary of New Testament Greek (SDGNT extract), github.com/ubsicap/ubs-open-license",
            "version": UBS_GREEK_VERSION, "license": "CC-BY-SA-4.0",
            "credit": "UBS Dictionary of New Testament Greek © United Bible Societies 2023; adapted from SDBG © UBS 2018-2023, "
                      "Louw & Nida © UBS 1988, 1989",
            "source_sha256": hashlib.sha256(Path(src).read_bytes()).hexdigest()}
    con.executemany("INSERT INTO grc_ubs_meta VALUES (?,?)", meta.items())
    con.commit()
    con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ubs", type=Path, default=UBS_GREEK_JSON)
    ap.add_argument("--db", type=Path, default=UBS_SENSES_DB)
    ap.add_argument("--build", action="store_true"); ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if not (a.build or a.report):
        ap.error("pass --build or --report")
    senses, refs, entry = load_dictionary(a.ubs)
    bound, stats = bind_all(refs, entry)
    print(json.dumps(dict(stats), indent=1), file=sys.stderr)
    if a.build:
        write_tables(a.db, senses, bound, a.ubs)
        print(f"[ubs_greek_senses] wrote grc tables to {a.db}: {len(senses)} senses, {len(bound)} tokens", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
