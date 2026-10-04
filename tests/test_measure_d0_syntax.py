import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("measure_d0_syntax", Path(__file__).resolve().parents[1] / "pipeline/scripts/tools/measure_d0_syntax.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_slot_cell_formats_direction_rate_n_and_abstention():
    assert m.slot_cell({"subject_verb": {"direction": "before", "rate_after": 0.2645, "n": 900}}, "subject_verb") == "before 0.264 n=900" or \
        m.slot_cell({"subject_verb": {"direction": "before", "rate_after": 0.2645, "n": 900}}, "subject_verb").startswith("before 0.26")
    assert m.slot_cell({"possessor": {"direction": None, "reason": "mixed"}}, "possessor") == "null(mixed)"
    assert m.slot_cell({}, "possessor") == "—"


def test_agree_distinguishes_same_different_and_abstain():
    a, b = {"x": {"direction": "before"}}, {"x": {"direction": "before"}}
    assert m.agree(a, b, "x") == "same"
    assert m.agree(a, {"x": {"direction": "after"}}, "x") == "DIFFERENT"
    assert m.agree(a, {"x": {"direction": None}}, "x") == "one abstains"
    assert m.agree({}, {}, "x") == "both abstain"
