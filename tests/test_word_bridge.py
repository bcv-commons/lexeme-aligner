from types import SimpleNamespace as T

from lexeme_aligner.eval.word_bridge import classify, classify_core, project, words_of


def tok(idx, keys, content=True, surface="x"):
    return T(idx=idx, keys=keys, is_content=content, surface=surface)


def test_prefix_and_stem_form_one_word():
    ws = words_of([tok(0, ["080010010021"], False), tok(1, ["080010010022"]), tok(2, ["080010010031"])])
    assert [w.idx for w in ws] == [[0, 1], [2]]
    assert ws[0].content == [1]


def test_token_holding_two_words_joins_them():
    toks = [tok(0, ["080010010101"], False), tok(1, ["080010010102", "080010010111"])]
    assert [w.idx for w in words_of(toks)] == [[0, 1]]


def test_greek_keys_are_one_word_each():
    toks = [tok(i, [f"4000100100{i + 1}"]) for i in range(3)]
    assert [w.idx for w in words_of(toks)] == [[0], [1], [2]]


def test_projection_unions_morphemes_and_is_grain_independent():
    ws = words_of([tok(2, ["080010010021"], False), tok(3, ["080010010022"])])
    stat = [{"h_idx": 2, "t_idx": [0]}, {"h_idx": 3, "t_idx": [2]}]
    gold = [{"h_idx": [2], "t_idx": [0, 2]}]
    assert project(stat, ws) == project(gold, ws) == {ws[0].wid: {0, 2}}


def test_classify_and_function_word_relaxation():
    assert classify({1}, {1}) == "equal"
    assert classify({1}, {1, 2}) == "a_inside_b"
    assert classify(set(), {1}) == "one_empty"
    assert classify_core({0, 2}, {0, 1, 2}, fn={1}) == "equal"
