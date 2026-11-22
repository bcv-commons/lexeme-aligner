"""Source spine by word parts (owner decision 2026-11-22: the source units of every alignment are word parts, `part-alignments`).

Builds a derived copy of the MACULA spine in which every row is one part of bcv-commons/word-parts (pinned in config/PROVENANCE.txt):
  Greek   a Nestle 1904 word -> preverb / augment / reduplication / stem / ending / fused / particle (the article, relatives,
          indeclinables stay one `particle` part);
  Hebrew  a MACULA part -> prefix (yiqtol person) / stem / ending (verb person-number, noun gender-number-state) / fused; prefixed
          particles and pronominal suffixes are already MACULA parts and stay one piece.

Every step of the chain reads it unchanged through ALIGNER_SPINE_DB (HebrewSource reads `SELECT *`):
  - the part that carries the word (stem, fused, or the single part) keeps the row's key, lexeme, strong, is_content, gloss,
    construct_group/role and lemma, so content units, their order and their Strong's counts are the same as in the word spine
    (gold scoring and the compact source index of content tokens are unchanged by construction);
  - every other part is a function unit: is_content 0, key = <word key>.<n>, and an anchor of its own so eflomal and gloss see it:
    lexeme `<lang>:~<kind>[:<features>]` (e.g. `grc:~ending:N-NSF`, `hbo:~ending:Ncmpa`, `grc:~preverb:preverb=απο`) and a
    strong in a reserved range (>= ANCHOR_BASE, numbered in sorted order of the lexemes, so a rebuild from the same release gives
    the same numbers); morphology columns are copied to every part of the word;
  - idx is renumbered per verse; head_idx and spine_assimilated_articles.after_idx are mapped to the new numbering (head ->
    the carrying part, after_idx -> the word's last part);
  - extra columns: part_id (word-parts id), part_kind, word_key (the spine key of the word / MACULA part it belongs to).

    python -m lexeme_aligner.source_parts --out pipeline/work/parts-spine/lexeme-spine-macula-parts-v1.0.db
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PARTS_DIR = REPO / "pipeline/vendor/word-parts/v1.0"
PARTS_PIN = {"dataset": "bcv-commons/word-parts", "tag": "v1.0", "revision": "9733875e4bb3361b1240d9814611c7535bf3480b",
             "sha256sums": "e34d278e3ea05d88"}
ANCHOR_BASE = 100000
CARRIERS = {"stem", "fused", "particle"}
EXTRA = ("part_id", "part_kind", "word_key")
NOT_COPIED = {"key", "idx", "surface", "lexeme", "strong", "is_content", "gloss", "lemma", "construct_group", "construct_role",
              "head_idx", "is_superscription"}


def _read(fp: Path) -> list[dict]:
    with open(fp, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def load_parts(parts_dir: Path = PARTS_DIR) -> dict[str, list[dict]]:
    """spine key -> its parts in order, each {part_id, n, text, kind, features, lang}."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for lang, f, keycol in (("grc", "greek/parts.tsv", "word_id"), ("hbo", "hebrew/parts.tsv", "macula_id")):
        for r in _read(Path(parts_dir) / f):
            out[r[keycol]].append({"part_id": r["part_id"], "n": int(r["n"]), "text": r["text"], "kind": r["kind"],
                                   "features": r["features"] or "", "lang": lang})
    for v in out.values():
        v.sort(key=lambda p: p["n"])
    return out


def anchor_lexeme(p: dict) -> str:
    return f'{p["lang"]}:~{p["kind"]}' + (f':{p["features"]}' if p["features"] else "")


def carrier_index(parts: list[dict]) -> int:
    hits = [i for i, p in enumerate(parts) if p["kind"] in CARRIERS]
    if len(hits) != 1:
        raise ValueError(f"expected one carrying part, got {[p['kind'] for p in parts]} ({parts[0]['part_id']})")
    return hits[0]


def build(spine_in: Path, out: Path, parts_dir: Path = PARTS_DIR) -> dict:
    parts = load_parts(parts_dir)
    src = sqlite3.connect(f"file:{spine_in}?mode=ro", uri=True)
    cols = [r[1] for r in src.execute("PRAGMA table_info(spine_words)")]
    rows = [dict(zip(cols, r)) for r in src.execute(f"SELECT {','.join(cols)} FROM spine_words ORDER BY book, chapter, verse, idx")]
    missing = [r["key"] for r in rows if r["key"] not in parts]
    if missing:
        raise SystemExit(f"[source-parts] {len(missing)} spine keys have no parts, e.g. {missing[:3]}")
    anchors = sorted({anchor_lexeme(p) for r in rows for i, p in enumerate(parts[r["key"]])
                      if len(parts[r["key"]]) > 1 and i != carrier_index(parts[r["key"]])})
    strong_of = {lx: ANCHOR_BASE + i for i, lx in enumerate(anchors)}

    new_rows, idx_map, last_map = [], {}, {}
    verse, nxt = None, 0
    stats = collections.Counter()
    for r in rows:
        v = (r["book"], r["chapter"], r["verse"])
        if v != verse:
            verse, nxt = v, 0
        ps = parts[r["key"]]
        ci = carrier_index(ps) if len(ps) > 1 else 0
        for i, p in enumerate(ps):
            if i == ci:
                row = dict(r, idx=nxt, surface=p["text"] if len(ps) > 1 else r["surface"])
                idx_map[v + (r["idx"],)] = nxt
                stats["carrier"] += 1
            else:
                row = {c: (r[c] if c not in NOT_COPIED else None) for c in cols}
                lx = anchor_lexeme(p)
                row.update(book=r["book"], chapter=r["chapter"], verse=r["verse"], idx=nxt, key=f'{r["key"]}.{p["n"]}',
                           surface=p["text"], lexeme=lx, strong=strong_of[lx], is_content=0, morph=p["features"] or None,
                           is_superscription=r["is_superscription"])
                stats[f'{p["lang"]}:{p["kind"]}'] += 1
            row.update(part_id=p["part_id"], part_kind=p["kind"], word_key=r["key"])
            new_rows.append(row)
            nxt += 1
        last_map[v + (r["idx"],)] = nxt - 1
    for row in new_rows:                                  # head_idx points at a spine idx of the same verse -> its carrying part
        if row.get("head_idx") is not None:
            row["head_idx"] = idx_map.get((row["book"], row["chapter"], row["verse"], row["head_idx"]))

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    dst = sqlite3.connect(tmp)
    schema = src.execute("SELECT sql FROM sqlite_master WHERE name='spine_words'").fetchone()[0]
    dst.execute(schema)
    for c in EXTRA:
        dst.execute(f"ALTER TABLE spine_words ADD COLUMN {c} TEXT")
    allc = cols + list(EXTRA)
    dst.executemany(f"INSERT INTO spine_words ({','.join(allc)}) VALUES ({','.join('?' * len(allc))})",
                    [[row.get(c) for c in allc] for row in new_rows])
    for name, sql in src.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        dst.execute(sql)
    dst.execute("CREATE TABLE spine_meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = dict(src.execute("SELECT key, value FROM spine_meta"))
    meta.pop("spine_content_sha256", None)                # describes the word spine, not this one
    meta.update(words=str(len(new_rows)), source_parts=json.dumps(PARTS_PIN), source_parts_base=str(spine_in.name),
                source_parts_base_content_sha256=dict(src.execute("SELECT key, value FROM spine_meta")).get("spine_content_sha256", ""))
    dst.executemany("INSERT INTO spine_meta VALUES (?, ?)", sorted(meta.items()))
    dst.execute("CREATE TABLE spine_part_anchors (strong INTEGER PRIMARY KEY, lexeme TEXT NOT NULL UNIQUE)")
    dst.executemany("INSERT INTO spine_part_anchors VALUES (?, ?)", [(s, lx) for lx, s in strong_of.items()])
    has_assim = src.execute("SELECT 1 FROM sqlite_master WHERE name='spine_assimilated_articles'").fetchone()
    if has_assim:
        dst.execute(src.execute("SELECT sql FROM sqlite_master WHERE name='spine_assimilated_articles'").fetchone()[0])
        acols = [r[1] for r in src.execute("PRAGMA table_info(spine_assimilated_articles)")]
        arows = []
        for a in src.execute(f"SELECT {','.join(acols)} FROM spine_assimilated_articles"):
            a = dict(zip(acols, a))
            a["after_idx"] = last_map[(a["book"], a["chapter"], a["verse"], a["after_idx"])]
            arows.append([a[c] for c in acols])
        dst.executemany(f"INSERT INTO spine_assimilated_articles VALUES ({','.join('?' * len(acols))})", arows)
    dst.commit()
    dst.close()
    tmp.replace(out)
    return {"words_in": len(rows), "rows_out": len(new_rows), "anchors": len(anchors), "parts": dict(sorted(stats.items()))}


def main(argv=None) -> int:
    from lexeme_aligner.config import SPINE_DB
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spine", type=Path, default=SPINE_DB)
    ap.add_argument("--parts", type=Path, default=PARTS_DIR)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    print(json.dumps(build(a.spine, a.out, a.parts), ensure_ascii=False, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
