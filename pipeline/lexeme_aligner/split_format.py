"""Split files (`bcv-split/0.1`, docs/split-format.md): check that every token's parts rebuild the written word exactly.

    python -m lexeme_aligner.split_format check <dir> [--usj pipeline/work/ingest-cache/usj-<tag>] [--show 20]

`<dir>` holds `_split.json` + `words.parquet`. Exit 0 when every token passes; otherwise the failures are listed by kind.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
import unicodedata as ud
from pathlib import Path

SPEC = "bcv-split/0.1"
SLOTS = ("prefix", "stem", "infix", "suffix", "tone", "whole")
WRITTEN = ("fused", "hyphen", "apart")
BASES = ("team", "derived", "external", "model", "labelled")
REQUIRED = ("book", "chapter", "verse", "i", "token", "word", "part", "slot", "text")


def put_tone(text: str, marks: list[str]) -> str:
    """Add tone marks ("<pos>:<mark>", pos = base-letter cluster counted from the end, 1 = last) to `text`; returns NFC."""
    clusters: list[list[str]] = []
    for ch in ud.normalize("NFD", text):
        if ud.combining(ch) and clusters:
            clusters[-1].append(ch)
        else:
            clusters.append([ch])
    letters = [k for k, cl in enumerate(clusters) if ud.category(cl[0]).startswith("L")]
    for m in marks:
        pos, _, mark = m.partition(":")
        n = int(pos)
        if not mark or n < 1 or n > len(letters):
            raise ValueError(f"tone mark {m!r}: no letter cluster {pos} from the end")
        clusters[letters[-n]].append(ud.normalize("NFD", mark))
    return ud.normalize("NFC", "".join("".join(cl) for cl in clusters))


def rebuild(parts: list[dict]) -> str:
    """The word the parts of one token give back (rebuild rule steps 2-3)."""
    text = "".join(p["text"] or "" for p in parts if p["slot"] != "tone")
    marks = [m for p in parts if p["slot"] == "tone" for m in (p.get("tone") or [])]
    return put_tone(text, marks) if marks else ud.normalize("NFC", text)


def check_token(parts: list[dict], tokenize) -> list[str]:
    """Problems of one token's rows (sorted by part); [] = passes."""
    bad = []
    if [p["part"] for p in parts] != list(range(len(parts))):
        bad.append("part numbers not 0..n-1")
    slots = [p["slot"] for p in parts]
    if any(s not in SLOTS for s in slots):
        bad.append("unknown slot")
    if "whole" in slots and len(parts) > 1:
        bad.append("a whole row next to other rows")
    for p in parts:
        if p.get("written") not in (None, *WRITTEN):
            bad.append("unknown written")
        elif p.get("written") in ("hyphen", "apart") and not p.get("host"):
            bad.append("an affix written apart without its host")
        if p.get("basis") not in (None, *BASES):
            bad.append("unknown basis")
        if any(p.get(k) is not None and not 0 <= p[k] <= 1 for k in ("p", "p_split")):
            bad.append("confidence outside 0..1")
        if p["slot"] == "tone" and (p["text"] or not p.get("tone")):
            bad.append("tone part with text or without marks")
    word = parts[0]["word"]
    if any(p["word"] != word or p["token"] != parts[0]["token"] for p in parts):
        bad.append("rows of one token disagree on word/token")
    try:
        if rebuild(parts) != ud.normalize("NFC", word):
            bad.append("parts do not rebuild the word")
    except ValueError as e:
        bad.append(str(e).split(":")[0])
    if tokenize(word) != [parts[0]["token"]]:
        bad.append("token is not the tokenizer's token of the word")
    return bad


def check(d: Path, usj: Path | None = None, show: int = 20) -> int:
    import pyarrow.parquet as pq
    from lexeme_aligner.usj_source import TOKENIZER_VERSION, tokenize
    head = json.loads((d / "_split.json").read_text(encoding="utf-8"))
    fp = d / "words.parquet"
    problems: collections.Counter = collections.Counter()
    examples: list[str] = []
    if head.get("spec") != SPEC:
        problems[f"header: spec is not {SPEC}"] += 1
    if str(head.get("tokenizer_version")) != str(TOKENIZER_VERSION):
        problems[f"header: tokenizer_version {head.get('tokenizer_version')} (this checker: {TOKENIZER_VERSION})"] += 1
    if head.get("sha256") != hashlib.sha256(fp.read_bytes()).hexdigest():
        problems["header: sha256 does not match words.parquet"] += 1
    for k in ("edition", "source", "basis", "text_licence", "text_fp"):
        if not head.get(k):
            problems[f"header: no {k}"] += 1
    if usj and head.get("text_fp"):
        from lexeme_aligner.text_fingerprint import book_file_hash
        files = {f.stem.split("-", 1)[-1]: f for f in usj.glob("*.json")}
        for book, want in sorted(head["text_fp"].items()):
            if book not in files:
                problems["header: text_fp book not in --usj"] += 1
            elif book_file_hash(files[book]) != want:
                problems["header: text_fp differs from --usj (another text)"] += 1
                if len(examples) < show:
                    examples.append(f"{book}: text_fp {want}, --usj {book_file_hash(files[book])}")
    rows = pq.read_table(fp).to_pylist()
    missing = [c for c in REQUIRED if rows and c not in rows[0]]
    if missing:
        print(f"[split] missing columns: {missing}", file=sys.stderr)
        return 2
    by_tok: dict[tuple, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_tok[(r["book"], r["chapter"], r["verse"], r["i"])].append(r)
    verse_toks = _verse_tokens(usj, tokenize) if usj else None
    for key, parts in by_tok.items():
        parts.sort(key=lambda p: p["part"])
        bad = check_token(parts, tokenize)
        if any(p.get("host") and (*key[:3], key[3] + p["host"]) not in by_tok for p in parts):
            bad.append("host token not in the file")
        if verse_toks is not None:
            vt = verse_toks.get(key[:3])
            if vt is None or key[3] >= len(vt) or vt[key[3]] != parts[0]["token"]:
                bad.append("token i is not the edition's token i")
        for b in bad:
            problems[b] += 1
            if len(examples) < show:
                examples.append(f"{key[0]} {key[1]}:{key[2]} i={key[3]} {parts[0]['word']!r}: {b}")
    print(f"[split] {d}: {len(by_tok):,} tokens, {len(rows):,} rows; "
          + ("ALL PASS" if not problems else f"{sum(problems.values()):,} problems"))
    for k, n in problems.most_common():
        print(f"   {n:>8,}  {k}")
    for e in examples:
        print(f"   e.g. {e}")
    return 0 if not problems else 1


def _verse_tokens(usj: Path, tokenize) -> dict[tuple, list[str]]:
    from lexeme_aligner.usj_source import read_verses
    out = {}
    for f in sorted(usj.glob("*.json")):
        book = f.stem.split("-", 1)[-1]
        for (ch, v), text in read_verses(f, warn=__import__("io").StringIO()).items():
            out[(book, ch, v)] = tokenize(text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("dir", type=Path)
    c.add_argument("--usj", type=Path, help="the edition's USJ folder: also check every `i` against our tokens of the verse")
    c.add_argument("--show", type=int, default=20)
    a = ap.parse_args()
    return check(a.dir, a.usj, a.show)


if __name__ == "__main__":
    sys.exit(main())
