import json

from lexeme_aligner import batch


def _manifest():
    return {"big": {"base_texts": ["a", "b", "c"]}, "mid": {"base_texts": ["a", "b"]}, "small": {"base_texts": ["a"]}, "alpha": {"base_texts": ["a"]}}


def test_queue_is_smallest_first_then_alphabetical():
    assert batch.select_languages(_manifest()) == ["alpha", "small", "mid", "big"]


def test_explicit_list_keeps_only_those_and_orders_them_the_same_way():
    assert batch.select_languages(_manifest(), isos=["big", "small"]) == ["small", "big"]


def test_stale_before_uses_the_partition_mtime():
    mt = {"alpha": 100.0, "small": 300.0, "mid": 50.0, "big": None}
    got = batch.select_languages(_manifest(), stale_before=200.0, partition_mtime=lambda i: mt[i])
    assert got == ["alpha", "mid", "big"]                 # a missing partition counts as stale, "small" (300) is current


def test_state_is_resumable_and_atomic(tmp_path):
    p = tmp_path / "s.json"
    s = batch.State(p)
    s.record("aaa", True)
    s.record("bbb", False)
    again = batch.State(p)
    assert again.done == ["aaa"] and again.failed == ["bbb"] and not list(tmp_path.glob("*.tmp"))
    assert batch.State(p, fresh=True).done == []


def test_run_batch_skips_done_and_failed_and_reports_failures(tmp_path):
    st = batch.State(tmp_path / "s.json")
    st.record("done1", True)
    st.record("bad1", False)
    ran = []

    def run_one(iso):
        ran.append(iso)
        return iso != "boom"

    rc = batch.run_batch(["done1", "bad1", "x", "boom", "y"], run_one, st, workers=1, free=lambda: 100.0, log=lambda m: None)
    assert ran == ["x", "boom", "y"]                       # the finished and the failed ones were not repeated
    assert rc == 1 and st.done == ["done1", "x", "y"] and st.failed == ["bad1", "boom"]


def test_retry_failed_runs_them_again_and_clears_the_failure(tmp_path):
    st = batch.State(tmp_path / "s.json")
    st.record("bad1", False)
    st.reset_failed()
    rc = batch.run_batch(["bad1"], lambda iso: True, st, free=lambda: 100.0, log=lambda m: None)
    assert rc == 0 and st.done == ["bad1"] and st.failed == []


def test_free_space_guard_stops_before_running_anything(tmp_path):
    ran = []
    st = batch.State(tmp_path / "s.json")
    rc = batch.run_batch(["a", "b"], lambda iso: ran.append(iso) or True, st, min_free_gb=20.0, free=lambda: 5.0, log=lambda m: None)
    assert rc == 2 and ran == [] and st.done == []


def test_an_exception_in_one_language_is_a_failure_not_a_crash(tmp_path):
    st = batch.State(tmp_path / "s.json")

    def run_one(iso):
        if iso == "x":
            raise RuntimeError("chain blew up")
        return True

    rc = batch.run_batch(["x", "y"], run_one, st, free=lambda: 100.0, log=lambda m: None)
    assert rc == 1 and st.failed == ["x"] and st.done == ["y"]


def test_several_workers_each_language_runs_exactly_once(tmp_path):
    st = batch.State(tmp_path / "s.json")
    seen = []
    rc = batch.run_batch([f"l{i}" for i in range(20)], lambda iso: seen.append(iso) or True, st, workers=4, free=lambda: 100.0, log=lambda m: None)
    assert rc == 0 and sorted(seen) == sorted(f"l{i}" for i in range(20)) and len(set(seen)) == 20
    assert json.loads((tmp_path / "s.json").read_text())["failed"] == []
