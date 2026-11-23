"""Print one verse of bcv-commons/part-alignments: every source part and the target words it links to, then the whole-word view
(= compact-alignments, computed here by fold.py). Standard library only; published beside fold.py and bcv_tokenize.py.

    python read_verse.py ROOT ISO EDITION "MRK 1:1" "the verse text as the edition has it"

ROOT: a local copy of the dataset (at least manifest.json, _index/<BOOK>_parts.json and the edition's file for the book).
The verse text must be the edition's own (bcv-commons/bibles' verse-text rule); its words are counted with bcv_tokenize.tokenize,
the tokenizer every position in the files refers to (manifest `tokenizer_version`).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcv_tokenize import tokenize                      # noqa: E402
from fold import entries, fold_verse, positions        # noqa: E402


def load(root: Path, iso: str, edition: str, ref: str):
    book = ref.split()[0]
    index = json.loads((root / "_index" / f"{book}_parts.json").read_text(encoding="utf-8"))
    name = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["languages"][iso]["editions"][edition]["books"][book]
    folder = root / iso[0] / iso / edition
    main = json.loads((folder / name).read_text(encoding="utf-8"))
    meta_fp = folder / name.replace(".json", ".meta.json")
    meta = json.loads(meta_fp.read_text(encoding="utf-8")) if meta_fp.exists() else {}
    i = list(index).index(ref)
    return index[ref], main[i], {c: v[i] for c, v in meta.items()}


def words_of(span: str, toks: list[str]) -> str:
    if span == "_":
        return "(checked: renders nothing)"
    return " ".join(toks[p] if p < len(toks) else f"#{p}" for p in positions(span))


def report(verse: dict, main: str, meta: dict, toks: list[str]) -> list[str]:
    out = ["source parts:"]
    links = entries(main)
    for o, (pid, kind, text, tok) in enumerate(verse["units"]):
        out.append(f"  {o:>3}  {pid:<16} {kind:<14} {text:<12} -> {words_of(links[o], toks) if o in links else '-'}")
    cmain, _cmeta, _ = fold_verse(verse, main, meta)
    cl = entries(cmain)
    out.append("whole words (compact-alignments view, by the fold):")
    cord = -1
    for k, (key, lexeme, content, _carrier) in enumerate(verse["tokens"]):
        word = "".join(u[2] for u in verse["units"] if u[3] == k)
        if not content:
            out.append(f"    -  {word:<14} {lexeme:<10} (function word: no slot in compact)")
            continue
        cord += 1
        out.append(f"  {cord:>3}  {word:<14} {lexeme:<10} -> {words_of(cl[cord], toks) if cord in cl else '-'}")
    return out


def main(argv=None) -> int:
    root, iso, edition, ref, text = (argv or sys.argv[1:])[:5]
    verse, main_s, meta = load(Path(root), iso, edition, ref)
    print(f"{ref}  {edition}")
    print("\n".join(report(verse, main_s, meta, tokenize(text))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
