"""source_index.py: the stamp, the check, and the lossless srcOrd shift helpers."""
import json
import os
import sys
from pathlib import Path

import pytest

import lexeme_aligner.source_index as si


# --- shift_entry -------------------------------------------------------------------------------------------

def test_shift_moves_only_ordinals_at_or_after_the_insertion_point():
    assert si.shift_entry("0:1 1:3 2:4 3:5-6 4:9,11", 2) == "0:1 1:3 3:4 4:5-6 5:9,11"
    assert si.shift_entry("", 2) == ""
    assert si.shift_entry("0:1", 5) == "0:1"


def test_shift_handles_every_sidecar_item_shape():
    assert si.shift_entry("10:G:23 3:e:4-5", 3) == "11:G:23 4:e:4-5"                # contested
    assert si.shift_entry("10:name_after:48+3:x:9", 3) == "11:name_after:48+3:x:9"  # rule: only the leading srcOrd
    assert si.shift_entry("7:12:40:71", 7) == "8:12:40:71"                          # bonus


def test_shift_refuses_an_item_that_is_not_a_srcord_item():
    with pytest.raises(ValueError):
        si.shift_entry("0:1 oops", 1)


def test_shift_then_ordinals_are_all_below_the_new_length():
    entry = "0:1 1:2 2:3 3:4"
    new = si.shift_entry(entry, 1)
    assert max(si.entry_ordinals(new)) == 4 and 1 not in si.entry_ordinals(new)     # slot 1 = the new token, unaligned


# --- compare_book ------------------------------------------------------------------------------------------

def test_compare_book_finds_a_single_insert_and_its_position():
    cur = {"A 1:1": ["a", "X", "b"], "A 1:2": ["c"]}
    old = {"A 1:1": ["a", "b"], "A 1:2": ["c"]}
    r = si.compare_book(cur, old)
    assert r["keys_equal"] and r["differing"] == ["A 1:1"] and r["inserts"] == {"A 1:1": (1, "X")}


def test_compare_book_does_not_call_a_replacement_an_insert():
    r = si.compare_book({"A 1:1": ["a", "Z"]}, {"A 1:1": ["a", "b"]})
    assert r["differing"] == ["A 1:1"] and r["inserts"] == {}


def test_compare_book_reports_a_different_verse_list():
    assert not si.compare_book({"A 1:1": []}, {"A 1:2": []})["keys_equal"]


# --- stamp / check / refresh / ensure_current (synthetic spine, monkeypatched) ---------------------------------

class _Heb:
    pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    books = {"AAA": {"AAA 1:1": ["l1", "l2"], "AAA 1:2": ["l3"]}}
    monkeypatch.setattr(si, "_lexemes", lambda heb, book: books[book])
    monkeypatch.setattr(si, "_function_words", lambda heb, book: {"AAA 1:1": ["f1", "f2", "f3"], "AAA 1:2": []})
    monkeypatch.setattr(si, "spine_sha", lambda *a, **k: "spine-v1")
    monkeypatch.setattr("lexeme_aligner.compact_align.ALL_BOOKS", ["AAA"])
    return books, tmp_path


def test_refresh_writes_files_and_a_stamp_and_check_passes(world):
    books, root = world
    res = si.refresh(_Heb(), root, ["AAA"])
    assert res["rewritten"] == ["AAA"] and (root / "AAA_lexemes.json").exists()
    stamp = si.read_stamp(root)
    assert stamp["spine_sha256"] == "spine-v1" and stamp["books"]["AAA"]["content_tokens"] == 3
    assert si.check_index(_Heb(), root, ["AAA"])["ok"]
    assert si.refresh(_Heb(), root, ["AAA"])["rewritten"] == []                    # idempotent, byte-identical


def test_check_fails_when_the_spine_gained_a_token(world):
    books, root = world
    si.refresh(_Heb(), root, ["AAA"])
    books["AAA"]["AAA 1:1"] = ["l1", "NEW", "l2"]
    res = si.check_index(_Heb(), root, ["AAA"])
    assert not res["ok"] and any("differ from the spine" in e for e in res["errors"])


def test_check_fails_on_a_missing_stamp_a_missing_book_and_a_hand_edit(world):
    books, root = world
    assert not si.check_index(_Heb(), root, ["AAA"])["ok"]                          # nothing there at all
    si.refresh(_Heb(), root, ["AAA"])
    fp = root / "AAA_lexemes.json"
    fp.write_text(json.dumps(books["AAA"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")   # same content, other bytes
    res = si.check_index(_Heb(), root, ["AAA"])
    assert not res["ok"] and any("stamped sha256" in e for e in res["errors"])


def test_spine_hash_only_difference_warns_unless_strict(world, monkeypatch):
    books, root = world
    si.refresh(_Heb(), root, ["AAA"])
    monkeypatch.setattr(si, "spine_sha", lambda *a, **k: "spine-v2")
    res = si.check_index(_Heb(), root, ["AAA"])
    assert res["ok"] and res["warnings"]
    assert not si.check_index(_Heb(), root, ["AAA"], strict=True)["ok"]


def test_ensure_current_fast_path_and_failure(world, monkeypatch):
    books, root = world
    si.refresh(_Heb(), root, ["AAA"])
    si.ensure_current(_Heb(), "AAA", root)                                          # stamp matches: no comparison needed
    books["AAA"]["AAA 1:2"] = ["l3", "l4"]
    si.ensure_current(_Heb(), "AAA", root)                                          # still the fast path (same spine hash)
    monkeypatch.setattr(si, "spine_sha", lambda *a, **k: "spine-v2")                # spine changed -> full comparison
    with pytest.raises(si.IndexMismatch, match="--refresh --books AAA"):
        si.ensure_current(_Heb(), "AAA", root)


# --- migration -----------------------------------------------------------------------------------------------

def _trio(tmp_path, main, extra=None, meta=None, age=-1000):
    d = tmp_path / "a" / "aaa" / "aaa_x"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / "AAA_abc12.json"
    fp.write_text(json.dumps(main) + "\n", encoding="utf-8")
    if extra is not None:
        (d / "AAA_abc12.extra.json").write_text(json.dumps(extra) + "\n", encoding="utf-8")
    if meta is not None:
        (d / "AAA_abc12.meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    t = 1_000_000 + age
    for f in d.iterdir():
        os.utime(f, (t, t))
    return fp


# --- compact_align.publish_compact refuses to write against a disagreeing index -------------------------------

def _publish(tmp_path, monkeypatch, lexemes, spine="spine-v1"):
    import lexeme_aligner.compact_align as ca
    monkeypatch.setattr(ca, "build_compact", lambda *a, **k: ({"RUT 1:1": "0:1"}, {}))
    monkeypatch.setattr(ca, "build_layer", lambda *a, **k: {})
    monkeypatch.setattr(ca, "build_source_lexemes", lambda heb, book: lexemes)
    monkeypatch.setattr(ca, "build_source_function_words", lambda heb, book: {})
    monkeypatch.setattr(ca, "book_content_hash", lambda p: "0" * 10 + "abcde")
    monkeypatch.setattr(si, "spine_sha", lambda *a, **k: spine)
    usj = tmp_path / "usj"
    usj.mkdir(exist_ok=True)
    (usj / f"{ca._BOOK_FILE_NUM['RUT']}-RUT.json").write_text("{}", encoding="utf-8")
    return ca.publish_compact("tg", "tg", usj, object(), tmp_path / "out", tmp_path / "idx", books=["RUT"],
                              edition="tg_x", with_layer=False, with_sidecars=False)


def test_publish_compact_creates_and_stamps_the_index_on_first_use_then_accepts_it(tmp_path, monkeypatch):
    lex = {"RUT 1:1": ["a", "b"]}
    _publish(tmp_path, monkeypatch, lex)
    stamp = si.read_stamp(tmp_path / "idx")
    assert stamp["spine_sha256"] == "spine-v1" and "RUT" in stamp["books"]
    _publish(tmp_path, monkeypatch, lex)                                             # second edition: reuses it


def test_publish_compact_raises_when_the_spine_gained_a_token_since_the_index_was_built(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {"RUT 1:1": ["a", "b"]})
    with pytest.raises(si.IndexMismatch, match="RUT"):                              # a rebuilt spine, one more token
        _publish(tmp_path, monkeypatch, {"RUT 1:1": ["a", "NEW", "b"]}, spine="spine-v2")


# --- the function-word index (full-align `fn` channel), 2026-10-06 -------------------------------------------
def test_refresh_writes_the_function_word_index_and_stamps_its_hash(world):
    books, root = world
    res = si.refresh(_Heb(), root, ["AAA"])
    assert res["rewritten_fn"] == ["AAA"]
    assert json.loads((root / "AAA_fn.json").read_text(encoding="utf-8")) == {"AAA 1:1": ["f1", "f2", "f3"], "AAA 1:2": []}
    st = json.loads((root / "_source.json").read_text(encoding="utf-8"))["books"]["AAA"]
    assert st["fn_tokens"] == 3 and len(st["fn_sha256"]) == 64
    assert si.refresh(_Heb(), root, ["AAA"])["rewritten_fn"] == []              # idempotent


def test_check_flags_a_function_word_index_that_differs_from_the_spine(world):
    books, root = world
    si.refresh(_Heb(), root, ["AAA"])
    (root / "AAA_fn.json").write_text(json.dumps({"AAA 1:1": ["f1"], "AAA 1:2": []}) + "\n", encoding="utf-8")
    res = si.check_index(_Heb(), root, ["AAA"])
    assert not res["ok"] and any("AAA_fn.json" in e for e in res["errors"])


def test_a_missing_function_word_index_is_a_warning_unless_strict(world):
    books, root = world
    si.refresh(_Heb(), root, ["AAA"])
    (root / "AAA_fn.json").unlink()
    assert si.check_index(_Heb(), root, ["AAA"])["ok"]
    res = si.check_index(_Heb(), root, ["AAA"], strict=True)
    assert not res["ok"] and any("AAA_fn.json" in e for e in res["errors"])
