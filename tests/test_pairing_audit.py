"""pipeline/scripts/tools/pairing_audit.py: the candidate-table loader and the per-book audit, on synthetic data."""
import importlib.util
import json
from pathlib import Path

import pytest

import lexeme_aligner.versification as vf

_tools = Path(__file__).resolve().parents[1] / "pipeline/scripts/tools"
spec = importlib.util.spec_from_file_location("pairing_audit", _tools / "pairing_audit.py")
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)


@pytest.fixture
def restore_reg_dir():
    old = vf._REG_DIR
    yield
    vf._REG_DIR = old


def test_install_table_replaces_only_the_named_scheme_file_from_bibles_json(tmp_path, restore_reg_dir):
    cand = tmp_path / "rso-to-eng.json"
    cand.write_text(json.dumps({"map": [{"s": "PSA 50:3", "t": "PSA 51:1"}, {"s": "PSA 50:1", "t": "PSA 51:title"}]}),
                    encoding="utf-8")
    tmp = pa.install_table(cand, "rso")
    assert vf._REG_DIR == tmp
    rows = (tmp / "rso.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[1] == "source_ref\tstandard_ref\taction" and "PSA 50:3\tPSA 51:1\tRenumber verse" in rows
    assert (tmp / "hebrew.tsv").exists()                                    # the other tables were copied over untouched
    assert vf.load_reverse_all("rso")[("PSA", 51, 1)] == [("PSA", 50, 3)]


def test_install_table_refuses_a_scheme_without_a_table_file(tmp_path, restore_reg_dir):
    with pytest.raises(SystemExit):
        pa.install_table(tmp_path / "x.json", "protestant")


class _Heb:
    def chapters(self, book):
        return [1]

    def verses(self, book, ch):
        return [1, 2, 3]


def test_audit_book_counts_titles_missing_target_verses_and_untargeted_verses(tmp_path, monkeypatch):
    # spine verses 1-3 map to target 1:0 (title), 1:2 (exists), 1:9 (does not exist); the target has verses 1, 2
    monkeypatch.setattr(pa, "_BOOK_FILE_NUM", {"GEN": "01"})
    (tmp_path / "01-GEN.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pa, "read_verse_ranges", lambda fp, rules=None: {(1, 1): {"text": "a"}, (1, 2): {"text": "b"}})
    monkeypatch.setattr(pa, "_range_coverage", lambda ranges: {})
    table = {1: ("GEN", 1, 0), 2: ("GEN", 1, 2), 3: ("GEN", 1, 9)}
    res = pa.audit_book("GEN", tmp_path, _Heb(), lambda b, c, v: table[v])
    assert res["title"] == ["GEN 1:1"]
    assert res["no_text"] == [("GEN 1:3", "1:9")]
    assert res["untargeted"] == [(1, 1)]                                     # target verse 1 is never reached
