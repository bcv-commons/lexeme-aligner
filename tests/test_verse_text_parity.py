"""usj_source's walker follows bcv-commons/bibles' verse-text rule (pipeline/vendor/bibles/verse_text.py, the Python twin of their
rule.js): for the same USJ both give the same tokens per verse. One synthetic book exercises every clause of the rule; a sample of the
local ingest cache is compared when it is present (the full-cache check on 2026-10-12: 957,100 chapters, 2 differences, both rule gaps
reported to bibles: `\\v 43a`/`\\v 43b` and a stray `\\v 0`)."""
import importlib.util
import json
import random
from pathlib import Path

import pytest

from lexeme_aligner import usj_source as u

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bibles_verse_text", ROOT / "pipeline/vendor/bibles/verse_text.py")
vt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vt)
CACHE = ROOT / "pipeline/work/ingest-cache"


def ours(path):
    return {k: u.tokenize(t) for k, t in u.read_verses(path, rules={}).items() if u.tokenize(t)}


def theirs(path):
    out = {}
    for c, verses in vt.extract_usj(json.loads(Path(path).read_text(encoding="utf-8"))).items():
        for x in verses:
            toks = u.tokenize(x["text"])
            if toks:
                out[(int(c), int(x["verse"]))] = toks
    return out


BOOK = [
    {"type": "book", "code": "RUT", "content": ["Ruth"]},
    {"type": "para", "marker": "ip", "content": ["An introduction."]},
    {"type": "chapter", "number": "1"},
    {"type": "para", "marker": "s1", "content": ["A section heading"]},
    {"type": "para", "marker": "p", "content": [
        {"type": "verse", "number": "1"}, "Bless", {"type": "char", "marker": "nd", "content": ["ed"]}, " be",        # markup inside a word
        {"type": "note", "marker": "f", "content": ["a footnote"]}, " the ",
        {"type": "char", "marker": "w", "content": ["LORD"]}, " ", {"type": "char", "marker": "w", "content": ["God"]},
        {"type": "char", "marker": "fm", "content": ["a"]}, ".",
        {"type": "verse", "number": "2-3"}, "Bridged", {"type": "char", "marker": "rq", "content": ["Gen 1:1"]}, " text.",
        {"type": "figure", "marker": "fig", "content": ["A caption"]},
    ]},
    {"type": "para", "marker": "q1", "content": ["a poetry line"]},                                                  # line ends: a space
    {"type": "para", "marker": "q2", "content": ["after a line"]},
    {"type": "para", "marker": "d", "content": ["Psalm title words", {"type": "verse", "number": "4"}, "title verse text"]},
    {"type": "para", "marker": "s2", "content": ["heading then", {"type": "verse", "number": "5"}, "verse five"]},
    {"type": "para", "marker": "p", "content": [{"type": "char", "marker": "qac", "content": ["א"]}, " acrostic line"]},
    {"type": "para", "marker": "rem", "content": [{"type": "verse", "number": "6"}, "a remark"]},
    {"type": "para", "marker": "restore", "content": ["metadata"]},
    {"type": "table", "content": [{"type": "table:row", "marker": "tr", "content": [
        {"type": "table:cell", "marker": "tc1", "content": [{"type": "verse", "number": "7"}, "cell"]},
        {"type": "table:cell", "marker": "tc2", "content": ["boundary"]}]}]},
    {"type": "para", "marker": "p", "content": [{"type": "verse", "number": "8"}, "first part",
                                                {"type": "verse", "number": "9"}, "nine",
                                                {"type": "verse", "number": "8"}, "second part"]},            # number given twice
    {"type": "para", "marker": "qa", "content": ["Aleph"]},
    {"type": "para", "marker": "lit", "content": ["Glory"]},
    {"type": "chapter", "number": "2"},
    {"type": "para", "marker": "p", "content": [{"type": "verse", "number": "1"}, "Chapter two", {"type": "char", "marker": "vp", "content": ["1a"]}]},
]


def test_synthetic_book_matches_the_rule(tmp_path):
    fp = tmp_path / "08-RUT.json"
    fp.write_text(json.dumps({"type": "USJ", "content": BOOK}, ensure_ascii=False), encoding="utf-8")
    assert ours(fp) == theirs(fp)
    got = ours(fp)
    assert got[(1, 1)] == ["Blessed", "be", "the", "LORD", "God"]
    assert got[(1, 2)] == ["Bridged", "text", "a", "poetry", "line", "after", "a", "line"]
    assert got[(1, 4)] == ["title", "verse", "text"]
    assert got[(1, 5)] == ["verse", "five", "acrostic", "line"]
    assert got[(1, 7)] == ["cell", "boundary"]
    assert got[(1, 8)] == ["first", "part", "second", "part", "Glory"]        # \lit counts; verse 8 is open again
    assert got[(1, 9)] == ["nine"]
    assert (1, 6) not in got and got[(2, 1)] == ["Chapter", "two"]


@pytest.mark.skipif(not CACHE.is_dir(), reason="ingest cache not present")
def test_sample_of_the_ingest_cache():
    rng = random.Random(20261012)
    dirs = sorted(d for d in CACHE.iterdir() if d.name.startswith("usj-"))
    files = [f for d in rng.sample(dirs, min(40, len(dirs))) for f in sorted(d.glob("*.json"))[:3]]
    assert files
    bad = [f for f in files if ours(f) != theirs(f)]
    assert not bad, bad[:5]
