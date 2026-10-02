from lexeme_aligner.edition_vote import combine_editions, vote_field


def _d(direction, n, rate=None, **kw):
    return {"direction": direction, "source": "derived", "n": n, **({"rate": rate} if rate is not None else {}), **kw}


def test_no_edition_with_a_fact_gives_none():
    assert combine_editions({"a": None, "b": None}) is None


def test_single_edition_is_recorded_as_a_thin_basis():
    out = combine_editions({"a": _d("after", 120, 0.8)})
    assert out["direction"] == "after"
    assert out["agreement"] == {"voting": 1, "agree": 1, "weight_share": 1.0}
    assert out["editions"]["a"]["direction"] == "after"


def test_agreeing_editions_keep_the_strongest_slot_and_list_all():
    out = combine_editions({"a": _d("before", 60, 0.2), "b": _d("before", 300, 0.15), "c": _d("before", 90, 0.25)})
    assert out["direction"] == "before" and out["n"] == 300 and out["rate"] == 0.15
    assert out["agreement"] == {"voting": 3, "agree": 3, "weight_share": 1.0}
    assert set(out["editions"]) == {"a", "b", "c"}


def test_weighted_majority_wins_when_it_clears_the_bar():
    out = combine_editions({"a": _d("after", 900), "b": _d("before", 100)})
    assert out["direction"] == "after"
    assert out["agreement"]["weight_share"] == 0.9 and out["agreement"]["agree"] == 1


def test_a_split_abstains_instead_of_picking():
    out = combine_editions({"a": _d("after", 500), "b": _d("before", 400)})      # 0.556 < 0.60
    assert out["direction"] is None and out["reason"] == "edition_conflict"
    assert out["editions"]["a"]["direction"] == "after" and out["editions"]["b"]["direction"] == "before"


def test_an_exact_tie_abstains():
    out = combine_editions({"a": _d("after", 100), "b": _d("before", 100)})
    assert out["direction"] is None and out["reason"] == "edition_conflict"


def test_abstaining_editions_are_listed_but_do_not_vote():
    out = combine_editions({"a": _d("after", 200), "b": _d(None, 50, reason="mixed")})
    assert out["direction"] == "after" and out["agreement"]["voting"] == 1
    assert out["editions"]["b"]["direction"] is None and out["editions"]["b"]["reason"] == "mixed"


def test_nobody_voting_passes_the_strongest_through():
    out = combine_editions({"a": _d(None, 20, reason="mixed"), "b": _d(None, 80, reason="mixed")})
    assert out["direction"] is None and out["n"] == 80 and out["agreement"]["voting"] == 0


def test_boolean_fields_vote_too():
    a = {"present": True, "n": 400, "source": "derived"}
    b = {"present": False, "n": 100, "source": "derived"}
    assert vote_field(a) == "present"
    out = combine_editions({"a": a, "b": b})
    assert out["present"] is True and out["agreement"]["weight_share"] == 0.8
    bound = combine_editions({"a": {"bound": True, "n": 10}, "b": {"bound": False, "n": 10}}, weight_key="n")
    assert bound["bound"] is None and bound["reason"] == "edition_conflict"


def test_result_does_not_depend_on_edition_order():
    slots = {"a": _d("after", 300), "b": _d("before", 100), "c": _d("after", 50)}
    rev = dict(reversed(list(slots.items())))
    assert combine_editions(slots) == combine_editions(rev)
