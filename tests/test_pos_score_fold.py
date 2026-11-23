"""pos_score folds a word's other parts' links into the word (parts spine) and leaves the word spine alone."""
import json

from lexeme_aligner.eval import pos_score as ps


def _spine(fold_of):
    key_of = {1: {0: ("G0976", 0), 1: ("G100001", 0), 2: ("G1078", 0)}}
    return ps.Spine(key_of, {1: {("G0976", 0), ("G1078", 0)}}, {}, {}, fold_of)


def _write(tmp_path, pairs):
    fp = tmp_path / "align_eflomal_x_MAT.jsonl"
    fp.write_text(json.dumps({"ref": 1, "pairs": pairs}) + "\n", encoding="utf-8")
    return tmp_path


def test_fold_unions_sub_part_into_word(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "tag_files", lambda out, method, tag: sorted(out.glob(f"align_{method}_{tag}_*.jsonl")))
    monkeypatch.setattr(ps, "BOOK_NUMBERS", {"MAT": 0})
    out = _write(tmp_path, [{"h_idx": 1, "t_idx": [5]}, {"h_idx": 0, "t_idx": [3]}, {"h_idx": 2, "t_idx": [7]}])
    sp = _spine({1: {1: 0}})
    assert ps.load_ours("x", "eflomal", out, ["MAT"], sp).spans[1] == {("G0976", 0): {3, 5}, ("G1078", 0): {7}}
    assert ps.load_ours("x", "eflomal", out, ["MAT"], sp, fold=False).spans[1] == {
        ("G0976", 0): {3}, ("G100001", 0): {5}, ("G1078", 0): {7}}


def test_word_spine_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "tag_files", lambda out, method, tag: sorted(out.glob(f"align_{method}_{tag}_*.jsonl")))
    monkeypatch.setattr(ps, "BOOK_NUMBERS", {"MAT": 0})
    out = _write(tmp_path, [{"h_idx": 0, "t_idx": [3]}, {"h_idx": 0, "t_idx": [4]}])
    assert ps.load_ours("x", "eflomal", out, ["MAT"], _spine({})).spans[1] == {("G0976", 0): {4}}   # last pair wins, as before


def test_fold_block_drops_positions_other_words_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "tag_files", lambda out, method, tag: sorted(out.glob(f"align_{method}_{tag}_*.jsonl")))
    monkeypatch.setattr(ps, "BOOK_NUMBERS", {"MAT": 0})
    out = _write(tmp_path, [{"h_idx": 1, "t_idx": [3, 5, 7]}, {"h_idx": 0, "t_idx": [3]}, {"h_idx": 2, "t_idx": [7]}])
    o = ps.load_ours("x", "eflomal", out, ["MAT"], _spine({1: {1: 0}}), fold_block=True)
    assert o.spans[1] == {("G0976", 0): {3, 5}, ("G1078", 0): {7}}    # 7 belongs to the other word; 3 is the word's own
