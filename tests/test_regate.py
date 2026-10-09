import json

from lexeme_aligner import regate


def _write(d, iso, doc):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{iso}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_directions_reads_direction_and_bound_and_skips_non_languages(tmp_path):
    _write(tmp_path, "aaa", {"adposition": {"direction": "before"}, "article_bound": {"bound": False}, "noise": {"direction": "x"}})
    _write(tmp_path, "_coverage", {"adposition": {"direction": "after"}})
    (tmp_path / "article_bound.json").write_text("{}", encoding="utf-8")
    assert regate.directions(tmp_path) == {"aaa": {"adposition": "before", "article_bound": False}}


def test_changed_reports_flips_gains_and_losses_only():
    before = {"a": {"adposition": "before"}, "b": {"adposition": "after", "article": None}, "c": {"possessor": "after"}, "same": {"adposition": "before"}}
    after = {"a": {"adposition": "after"}, "b": {"adposition": "after", "article": "before"}, "c": {}, "same": {"adposition": "before"}, "new": {"adposition": "before"}}
    diff = regate.changed(before, after)
    assert diff["a"] == {"adposition": ["before", "after"]}
    assert diff["b"] == {"article": [None, "before"]}                 # gained a verdict
    assert diff["c"] == {"possessor": ["after", None]}                # lost a verdict
    assert diff["new"] == {"adposition": [None, "before"]}            # a language that was not there before
    assert "same" not in diff


def test_cli_writes_the_list_and_the_detail(tmp_path):
    before, after = tmp_path / "b", tmp_path / "a"
    _write(before, "xx", {"adposition": {"direction": "before"}})
    _write(after, "xx", {"adposition": {"direction": "after"}})
    _write(before, "yy", {"adposition": {"direction": "before"}})
    _write(after, "yy", {"adposition": {"direction": "before"}})
    out = tmp_path / "list.json"
    assert regate.main(["--before", str(before), "--after", str(after), "--out", str(out), "--all-slots"]) == 0
    assert json.loads(out.read_text()) == ["xx"]
    assert json.loads(out.with_suffix(".detail.json").read_text())["xx"]["adposition"] == ["before", "after"]


def test_snapshot_skips_derived_input(tmp_path):
    gs = tmp_path / "gs"
    _write(gs, "aaa", {"adposition": {"direction": "before"}})
    (gs / "derived_input").mkdir()
    (gs / "derived_input" / "big.json").write_text("{}", encoding="utf-8")
    dest = regate.snapshot(gs, tmp_path / "work")
    assert (dest / "aaa.json").exists() and not (dest / "derived_input").exists()


def test_consumed_diff_keeps_only_slots_the_chain_reads():
    from lexeme_aligner.regate import consumed_diff
    diff = {"aaa": {"adposition": ["before", "after"]}, "bbb": {"possessor": [None, "after"]},
            "spa": {"adposition": ["before", None]}, "ccc": {"article_bound": [False, True], "object_verb": [None, "after"]}}
    out = consumed_diff(diff, {"spa": {"typology_fallback": True}}, {})
    assert out == {"bbb": {"possessor": [None, "after"]}, "spa": {"adposition": ["before", None]},
                   "ccc": {"article_bound": [False, True]}}
