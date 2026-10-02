"""R10 (2026-09-28): tokens_per_type() / should_stem() — the self-derived stemming gate.
_learn() itself (affix induction) is exercised end-to-end elsewhere; these tests are offline, against
small hand-built cache files, so they run without any real corpus."""
import json
from pathlib import Path

import lexeme_aligner.target_morph as tm


def _write_book(usj_dir, book):
    usj_dir.mkdir(parents=True, exist_ok=True)
    (usj_dir / f"{tm._BOOK_FILE_NUM[book]}-{book}.json").write_text("{}", encoding="utf-8")


# --- sample_tokens_per_type() -- the 2026-09-28 corpus-size-confound fix ------------------------------

def test_sample_tokens_per_type_counts_a_small_corpus_exactly(tmp_path, monkeypatch):
    book = "RUT"
    _write_book(tmp_path, book)
    monkeypatch.setattr(tm, "read_verses", lambda fp: {"1:1": "kata berkata", "1:2": "kata berkata"})
    total, n_types = tm.sample_tokens_per_type(tmp_path, [book])
    assert (total, n_types) == (4, 2)


def test_sample_tokens_per_type_stops_at_max_tokens_regardless_of_corpus_size(tmp_path, monkeypatch):
    # the actual bug this fixes: a language's ratio must not depend on how much text happens to be
    # pooled. Capping means a 10-word repeated corpus and a 10,000-word repeated corpus give the SAME
    # tokens/type once both exceed the cap.
    book = "RUT"
    _write_book(tmp_path, book)
    monkeypatch.setattr(tm, "read_verses", lambda fp: {"1:1": " ".join(["alpha", "beta"] * 1000)})
    total, n_types = tm.sample_tokens_per_type(tmp_path, [book], max_tokens=10)
    assert total == 10 and n_types == 2


def test_sample_tokens_per_type_pools_a_list_of_dirs(tmp_path, monkeypatch):
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    book = "RUT"
    _write_book(dir_a, book)
    _write_book(dir_b, book)
    texts = {dir_a: {"1:1": "kata"}, dir_b: {"1:1": "roti"}}
    monkeypatch.setattr(tm, "read_verses", lambda fp: texts[fp.parent])
    total, n_types = tm.sample_tokens_per_type([dir_a, dir_b], [book])
    assert (total, n_types) == (2, 2)


def test_sample_tokens_per_type_dir_order_is_sorted_not_argument_order(tmp_path, monkeypatch):
    # deterministic: pooling order must not depend on the order dirs happen to be passed in.
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    book = "RUT"
    _write_book(dir_a, book)
    _write_book(dir_b, book)
    texts = {dir_a: {"1:1": "kata"}, dir_b: {"1:1": "roti"}}
    monkeypatch.setattr(tm, "read_verses", lambda fp: texts[fp.parent])
    forward = tm.sample_tokens_per_type([dir_a, dir_b], [book], max_tokens=1)
    backward = tm.sample_tokens_per_type([dir_b, dir_a], [book], max_tokens=1)
    assert forward == backward == (1, 1)   # both see "kata" first (dir_a sorts before dir_b), not "roti"


def test_sample_tokens_per_type_empty_corpus(tmp_path):
    assert tm.sample_tokens_per_type(tmp_path, ["RUT"]) == (0, 0)


# --- tokens_per_type() / should_stem() ------------------------------------------------------------------

def test_tokens_per_type_none_when_no_usj_dir():
    assert tm.tokens_per_type("zz", usj_dir=None) is None


def test_tokens_per_type_computes_fresh_from_usj_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "sample_tokens_per_type", lambda usj_dirs, **k: (50, 10))
    assert tm.tokens_per_type("zz", usj_dir="/fake/usj") == 5.0


def test_tokens_per_type_none_on_zero_types(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "sample_tokens_per_type", lambda usj_dirs, **k: (0, 0))
    assert tm.tokens_per_type("zz", usj_dir="/fake/usj") is None


def test_tokens_per_type_never_touches_any_cache_file(tmp_path, monkeypatch):
    # the 2026-09-28 mutation bug this must never regress into: tokens_per_type has no cache_dir
    # concept left at all -- it always computes fresh and writes nothing anywhere.
    monkeypatch.setattr(tm, "sample_tokens_per_type", lambda usj_dirs, **k: (50, 10))
    tm.tokens_per_type("zz", usj_dir="/fake/usj")
    assert list(tmp_path.iterdir()) == []


def test_should_stem_true_at_or_below_threshold(monkeypatch):
    monkeypatch.setattr(tm, "sample_tokens_per_type", lambda usj_dirs, **k: (100 * tm.STEM_RATIO_THRESHOLD, 100))
    decision, ratio = tm.should_stem("rich", usj_dir="/fake")
    assert decision is True and ratio == tm.STEM_RATIO_THRESHOLD


def test_should_stem_false_above_threshold(monkeypatch):
    monkeypatch.setattr(tm, "sample_tokens_per_type",
                        lambda usj_dirs, **k: (100 * (tm.STEM_RATIO_THRESHOLD + 0.01), 100))
    decision, ratio = tm.should_stem("sparse", usj_dir="/fake")
    assert decision is False and ratio > tm.STEM_RATIO_THRESHOLD


def test_should_stem_fails_closed_when_ratio_unknown():
    decision, ratio = tm.should_stem("zz", usj_dir=None)
    assert decision is False and ratio is None


def test_learn_computes_n_tokens_as_the_real_token_count(tmp_path, monkeypatch):
    # end-to-end _learn() with a tiny fake corpus: "kata berkata" repeated -> 2 types, 4 tokens.
    book = "RUT"
    (tmp_path / f"{tm._BOOK_FILE_NUM[book]}-{book}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tm, "read_verses", lambda fp: {"1:1": "kata berkata", "1:2": "kata berkata"})
    model = tm._learn(tmp_path, [book])
    assert model["n_types"] == 2
    assert model["n_tokens"] == 4


def test_learn_accepts_a_single_dir_or_a_list_and_pools_them(tmp_path, monkeypatch):
    book = "RUT"
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    for d in (dir_a, dir_b):
        d.mkdir()
        (d / f"{tm._BOOK_FILE_NUM[book]}-{book}.json").write_text("{}", encoding="utf-8")
    texts = {dir_a: {"1:1": "kata"}, dir_b: {"1:1": "roti"}}
    monkeypatch.setattr(tm, "read_verses", lambda fp: texts[fp.parent])
    single = tm._learn(dir_a, [book])
    assert single["n_types"] == 1 and single["n_tokens"] == 1
    pooled = tm._learn([dir_a, dir_b], [book])
    assert pooled["n_types"] == 2 and pooled["n_tokens"] == 2


# --- 2026-09-28: _learn()'s top-N affix selection must not depend on Python's per-process hash seed ---

def test_learn_top_affix_selection_ties_break_alphabetically_not_by_insertion_order():
    # unit-level lock on the actual fix: sorted(items, key=lambda kv: (-kv[1], kv[0]))[:N] must keep the
    # alphabetically-first candidates among a count tie, regardless of the dict's OWN iteration/
    # insertion order (which is what a plain Counter.most_common() tie-break depended on).
    import collections
    tied = collections.Counter({"zz": 5, "aa": 5, "mm": 5, "bb": 5})   # all tie at count 5
    kept = [a for a, n in sorted(tied.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    assert kept == ["aa", "bb"]              # alphabetically first two of the tied set, not insertion order
    # Counter.most_common() itself, by contrast, would have kept insertion order ("zz","aa") here --
    # confirming this is a genuine behavior change, not a no-op refactor.
    assert [a for a, n in tied.most_common(2)] == ["zz", "aa"]


def test_learn_is_deterministic_across_separate_processes_real_proof(tmp_path):
    # THE real regression test, reproducing exactly how the bug was found and confirmed this session:
    # PYTHONHASHSEED is randomized per-process by default, so this only proves the fix if each subprocess
    # gets its OWN random seed (never pin PYTHONHASHSEED here, that would hide a regression). The
    # vocabulary is engineered (same shape as the deleted-in-favor-of-this-test helper) so MORE than
    # _TOP_AFFIX 2-letter suffixes tie at the same minimum qualifying count -- the exact condition that
    # exposed the real bug (Counter.most_common()'s insertion-order tie-break, itself downstream of a
    # set's hash-seed-dependent iteration order).
    import os
    import subprocess
    import sys

    book = "RUT"
    (tmp_path / f"{tm._BOOK_FILE_NUM[book]}-{book}.json").write_text("{}", encoding="utf-8")
    pipeline_dir = str(Path(__file__).resolve().parents[1] / "pipeline")
    script = f"""
import hashlib, itertools, json, string, sys
sys.path.insert(0, {pipeline_dir!r})
import lexeme_aligner.target_morph as tm
stems = [f"qz{{c}}" for c in string.ascii_lowercase[:tm._MIN_STEMS]]
pairs = ("".join(p) for p in itertools.product(string.ascii_lowercase, repeat=2))
suffixes = list(itertools.islice(pairs, tm._TOP_AFFIX + 10))
words = [f"{{stem}}{{suf}}" for stem in stems for suf in suffixes]
tm.read_verses = lambda fp: {{"1:1": " ".join(words)}}
m = tm._learn({str(tmp_path)!r}, [{book!r}])
payload = json.dumps({{k: m[k] for k in ("suffixes", "prefixes")}}, sort_keys=True)
print(hashlib.sha256(payload.encode()).hexdigest())
"""
    hashes = set()
    for _ in range(3):
        env = dict(os.environ)
        env.pop("PYTHONHASHSEED", None)     # explicitly let each subprocess pick its own random seed
        out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                             env=env, check=True)
        hashes.add(out.stdout.strip())
    assert len(hashes) == 1, f"non-deterministic across processes: {hashes}"
