"""lexeme_aligner.eval.jev_check (moved from pipeline/work/jev-testA/ 2026-10-21): offline parts only — the answer cache and the v4 decisions."""
import hashlib
import json
from types import SimpleNamespace

from lexeme_aligner.eval import jev_check as jc


def test_a_cached_answer_is_returned_without_a_request(tmp_path, monkeypatch):
    monkeypatch.setattr(jc, "WORK", tmp_path)
    body = {"model": "jev-latest", "state": {"x": 1}, "questions": {"h1": {"type": "choice"}}}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    fp = tmp_path / "responses" / (hashlib.sha256(raw).hexdigest()[:24] + ".json")
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps({"answers": {"h1": {"choice": "none"}}}))
    monkeypatch.setattr(jc.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    assert jc.ask(body, "apikey_x") == {"answers": {"h1": {"choice": "none"}}, "_cached": True}


def test_v4_function_word_decisions():
    heb = [SimpleNamespace(idx=0, is_content=True), SimpleNamespace(idx=1, is_content=False),
           SimpleNamespace(idx=2, is_content=False), SimpleNamespace(idx=3, is_content=False)]
    p = SimpleNamespace(heb=heb, owner={0: [0], 1: [1]}, toks=["a", "b", "c"])
    ans = {"h1": {"choice": "t1", "confidence": 0.9},          # its own proposed word: kept
           "h2": {"choice": "t0", "confidence": 0.8},          # a word another source word owns: carried inside it
           "h3": {"choice": "none", "confidence": 0.4}}        # below the threshold: the proposal stands
    edits, tally = jc.decide(p, ans, 0.5, design="v4")
    assert tally["fn keep"] == 1 and tally["fn inside"] == 1 and tally["below threshold (proposal kept)"] == 1
    assert [(e.kind, e.h, e.t) for e in edits] == [("inside", 2, [0])]
