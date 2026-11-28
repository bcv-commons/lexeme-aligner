"""Check that pipeline/split_engine/bcv_segment.py splits exactly as a bundle's own engine copy (bcv_segment.py + morphs.py in the
bundle), on every distinct written word of an edition (or a word list): `split(w)` and `S.split(w, tones=True)`, compared as JSON.

    python pipeline/scripts/tools/check_split_engine.py BUNDLE_DIR (--usj USJ_DIR | --words FILE) [--out REPORT.json]

Each side runs in its own `python -I` process (both modules are named bcv_segment / morphs), so nothing leaks between them."""
import argparse, json, re, subprocess, sys, tempfile, unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENGINE = REPO / "pipeline/split_engine"

RUNNER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import bcv_segment
seg = bcv_segment.load(sys.argv[2])
words = json.load(open(sys.argv[3], encoding="utf-8"))
out = {}
for w in words:
    try:
        a = seg.split(w)
    except Exception as e:
        a = "ERROR " + type(e).__name__ + ": " + str(e)
    try:
        b = [list(m) for m in seg.S.split(w, tones=True)]
    except TypeError:
        b = None                                          # an engine copy without the tones keyword
    except Exception as e:
        b = "ERROR " + type(e).__name__ + ": " + str(e)
    out[w] = [a, b]
json.dump(out, open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False)
"""


def edition_words(usj: Path) -> list[str]:
    sys.path.insert(0, str(REPO / "pipeline"))
    sys.path.insert(0, str(REPO / "pipeline/vendor/bibles"))
    from bcv_written import tokenize_written
    from lexeme_aligner.usj_source import read_verses
    words = set()
    for f in sorted(usj.glob("*.json")):
        for text in read_verses(f).values():
            toks = tokenize_written(text)
            for w in toks:
                words.add(w)
                words.add(w.lower())
            for m in re.finditer(r"[^\s-]+(?:-[^\s-]+)+", text):   # hyphen words, split joined (kvg numbering)
                h = "".join(c for c in m.group() if c.isalnum() or c == "-" or unicodedata.category(c).startswith("M")).strip("-")
                if "-" in h:
                    words.update((h, h.lower()))
    return sorted(words)


def run(engine_dir: Path, bundle: Path, words_file: Path, out: Path) -> dict:
    subprocess.run([sys.executable, "-I", "-c", RUNNER, str(engine_dir), str(bundle), str(words_file), str(out)], check=True,
                   cwd=tempfile.gettempdir())
    return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle", type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--usj", type=Path)
    g.add_argument("--words", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    words = edition_words(a.usj) if a.usj else [w for w in a.words.read_text(encoding="utf-8").split() if w]
    with tempfile.TemporaryDirectory() as td:
        wf = Path(td) / "words.json"
        wf.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        old = run(a.bundle.resolve(), a.bundle.resolve(), wf, Path(td) / "old.json")
        new = run(ENGINE, a.bundle.resolve(), wf, Path(td) / "new.json")
    diff = [w for w in words if old[w] != new[w]]
    split_words = sum(1 for w in words if isinstance(new[w][0], list) and len(new[w][0]) > 1)
    errors = sum(1 for w in words if isinstance(new[w][0], str))
    rep = {"bundle": str(a.bundle), "words": len(words), "split": split_words, "errors": errors, "different": len(diff),
           "examples": {w: {"bundle": old[w], "engine": new[w]} for w in diff[:20]}}
    if a.out:
        a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("bundle", "words", "split", "errors", "different")}, ensure_ascii=False))
    return 0 if not diff else 1


if __name__ == "__main__":
    sys.exit(main())
