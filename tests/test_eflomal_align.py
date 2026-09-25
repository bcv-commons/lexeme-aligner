"""Tests for Step 3's eflomal harness additions: FERF fertility priors alongside LEX priors, and the
save_links/load_links replay mechanism (internal-docs/aim1-typology-source-structure-plan.md, Step 3).
The real `eflomal` C binary is never invoked here — `Aligner` is monkeypatched to a fake that writes
deterministic link files (and, for the priors test, captures what it was handed), the same way
`test_slot_allocation.py` isolates the pure-Python symmetrizer from the C aligner.
"""
import json

import lexeme_aligner.eflomal_align as ea
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, strong, is_content=True):
    return HebToken(idx, f"w{idx}", strong, f"grc:{idx}", f"l{idx}", None, is_content)


class _Rec:
    def __init__(self, book, ch, v, heb, toks):
        self.book, self.ch, self.v, self.heb, self.toks = book, ch, v, heb, toks


class _Norm:
    def forms(self, w):
        return [w]

    def stem(self, w):
        return w


class FakeAligner:
    """Writes fixed fwd/rev link files and (optionally) captures the priors file's own content, so a
    test can assert on exactly what `EflomalAligner.run()` handed the real `eflomal` binary."""
    def __init__(self, fwd_lines, rev_lines, captured=None):
        self.fwd_lines, self.rev_lines, self.captured = fwd_lines, rev_lines, captured

    def align(self, src_input, trg_input, links_filename_fwd, links_filename_rev,
              priors_input=None, quiet=True):
        if self.captured is not None:
            self.captured["priors"] = priors_input.read() if priors_input is not None else None
        with open(links_filename_fwd, "w") as f:
            f.write("\n".join(self.fwd_lines) + "\n")
        with open(links_filename_rev, "w") as f:
            f.write("\n".join(self.rev_lines) + "\n")


class _BoomAligner:
    """Raises if eflomal is invoked at all — used to prove `load_links` skips training entirely."""
    def align(self, *a, **k):
        raise AssertionError("Aligner.align() must not be called when load_links is given")


def test_run_writes_ferf_lines_alongside_lex_priors(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "Aligner", lambda: FakeAligner(["0-0"], ["0-0"], captured))
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1285")], ["target"])]
    eflo = ea.EflomalAligner()
    eflo.run(recs, _Norm(), priors_pairs=[("H1285", "target", 3)],
            fertility_priors={"H1285": (2, 1.5)})
    lines = captured["priors"].splitlines()
    assert "LEX\tH1285\ttarget\t3.0" in lines
    assert "FERF\tH1285\t2\t1.5" in lines


def test_run_omits_priors_file_when_neither_kind_given(monkeypatch):
    captured = {}
    monkeypatch.setattr(ea, "Aligner", lambda: FakeAligner(["0-0"], ["0-0"], captured))
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1285")], ["target"])]
    ea.EflomalAligner().run(recs, _Norm())
    assert captured["priors"] is None


def test_save_links_writes_one_json_row_per_verse(tmp_path, monkeypatch):
    monkeypatch.setattr(ea, "Aligner", lambda: FakeAligner(["0-0"], ["0-0"]))
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1285")], ["target"])]
    dest = tmp_path / "links.jsonl"
    ea.EflomalAligner().run(recs, _Norm(), save_links=dest)
    rows = [json.loads(l) for l in dest.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"book": "RUT", "chapter": 1, "verse": 1, "fwd": [[0, 0]], "rev": [[0, 0]]}]


def test_load_links_never_calls_the_real_aligner_and_reproduces_the_saved_symmetrization(
        tmp_path, monkeypatch):
    links_fp = tmp_path / "links.jsonl"
    links_fp.write_text(json.dumps({"book": "RUT", "chapter": 1, "verse": 1,
                                    "fwd": [[0, 0]], "rev": [[0, 0]]}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ea, "Aligner", _BoomAligner)   # would raise if instantiated/called
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1285")], ["target"])]
    eflo = ea.EflomalAligner()
    eflo.run(recs, _Norm(), load_links=links_fp)       # must not raise
    info = eflo.by_verse[("RUT", 1, 1)]
    assert (0, 0) in info["sym"]
    assert (0, 0) in info["inter"]


def test_load_links_missing_verse_falls_back_to_empty_links(tmp_path, monkeypatch):
    """A verse present in the current corpus but absent from a saved-links file (e.g. corpus changed
    between save and load) gets empty fwd/rev rather than crashing — the same graceful-miss posture
    the rest of this pipeline uses for absent data."""
    links_fp = tmp_path / "links.jsonl"
    links_fp.write_text("", encoding="utf-8")
    monkeypatch.setattr(ea, "Aligner", _BoomAligner)
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1285")], ["target"])]
    eflo = ea.EflomalAligner()
    eflo.run(recs, _Norm(), load_links=links_fp)
    info = eflo.by_verse[("RUT", 1, 1)]
    assert info["sym"] == set()
