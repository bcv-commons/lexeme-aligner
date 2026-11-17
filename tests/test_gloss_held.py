"""Gloss's links on function source words are written as `held` (claims, not published links) — 2026-11-16."""
import json
from pathlib import Path

from lexeme_aligner import run_pilot
from lexeme_aligner.gloss_align import Match
from lexeme_aligner.hebrew_source import HebToken


def _tok(idx, strong, content):
    return HebToken(idx=idx, surface="x", strong=strong, lexeme=f"hbo:{strong[1:]}", lemma="x", morph=None, is_content=content)


def _write(tmp_path, tag, monkeypatch, hold=True):
    monkeypatch.setattr(run_pilot, "GLOSS_HOLD_FUNCTION", hold)
    tmp_path.mkdir(parents=True, exist_ok=True)
    rec = run_pilot.VerseRec("RUT", 1, 1, [_tok(0, "H3807", False), _tok(1, "H0776", True)], "to the land", toks=["to", "the", "land"])
    run_pilot.run_method([rec], lambda r: [Match(0, [0], 1.0, "exact"), Match(1, [2], 1.0, "exact")], "eng", tag, tmp_path)
    (fp,) = list(tmp_path.glob(f"align_{tag}_*RUT*.jsonl"))
    return json.loads(fp.read_text().splitlines()[0])


def test_gloss_function_links_are_held_not_published(tmp_path, monkeypatch):
    r = _write(tmp_path, "gloss", monkeypatch)
    assert [p["h_idx"] for p in r["pairs"]] == [1]
    assert [(p["h_idx"], p["t_idx"]) for p in r["held"]] == [(0, [0])]


def test_other_methods_and_switch_off_keep_function_links(tmp_path, monkeypatch):
    assert [p["h_idx"] for p in _write(tmp_path / "e", "eflomal", monkeypatch)["pairs"]] == [0, 1]
    r = _write(tmp_path / "g", "gloss", monkeypatch, hold=False)
    assert [p["h_idx"] for p in r["pairs"]] == [0, 1] and "held" not in r


def _record(tmp_path):
    rec = {"ref": 8001001, "book": "RUT", "chapter": 1, "verse": 1,
           "pairs": [{"h_idx": 1, "t_idx": [2], "content": True, "target": "land", "score": 1.0, "lexeme": "hbo:0776", "strong": "H0776"}],
           "held": [{"h_idx": 0, "t_idx": [0], "content": False, "target": "to", "score": 1.0, "lexeme": "hbo:3807a", "strong": "H3807",
                     "lemma": "לְ"}]}
    (tmp_path / "align_gloss_eng_RUT.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n")


def test_held_links_still_claim_for_gapfill(tmp_path):
    from lexeme_aligner import gapfill
    _record(tmp_path)
    covered_h, taken_t, *_ = gapfill.load_covered("eng", tmp_path, ["gloss"], 0.0, {}, reserve_function_slots=True)
    assert taken_t[8001001] == {0, 2} and covered_h[8001001] == {1}


def test_grammar_analyses_still_read_held_links(tmp_path):
    from lexeme_aligner import derive_typology
    from lexeme_aligner.eval import word_checks
    _record(tmp_path)
    assert derive_typology.full_anchors(tmp_path, "eng", ("gloss",)) == {8001001: {1: 2, 0: 0}}
    assert word_checks.load_alignment("eng", ("gloss",), tmp_path)[8001001] == {1: [2], 0: [0]}


def test_published_readers_ignore_held(tmp_path):
    from lexeme_aligner.eval import pos_score  # noqa: F401  (pos_score reads `pairs` only, like compact/export/fullalign)
    from lexeme_aligner.align_files import tag_files
    _record(tmp_path)
    (fp,) = tag_files(tmp_path, "gloss", "eng")
    rec = json.loads(fp.read_text(encoding="utf-8").splitlines()[0])
    assert {p["h_idx"] for p in rec["pairs"]} == {1}
