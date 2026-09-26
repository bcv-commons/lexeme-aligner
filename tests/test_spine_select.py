"""Offline tests for spine_select.py (roadmap E3, plumbing half) — synthetic inputs via path
parameters, no real edition_struct/textual_basis files or sibling spine DBs needed."""
import json
from pathlib import Path

import lexeme_aligner.spine_select as ss


def _w(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_textual_basis_verdict_from_edition_struct(tmp_path):
    es_dir = tmp_path / "edition_struct"
    es_dir.mkdir()
    _w(es_dir / "aaamlt.json", {"derived": {"textual_basis": {"verdict": "tr"}}})
    assert ss.textual_basis_verdict("aaamlt", es_dir, tmp_path / "absent.json") == "tr"


def test_textual_basis_verdict_falls_back_to_raw_textual_basis_json(tmp_path):
    tb = _w(tmp_path / "tb.json", {"editions": {"aaamlt": {"verdict": "byzantine"}}})
    assert ss.textual_basis_verdict("aaamlt", tmp_path / "absent-dir", tb) == "byzantine"


def test_textual_basis_verdict_none_when_unclassified(tmp_path):
    assert ss.textual_basis_verdict("zzz", tmp_path / "absent-dir", tmp_path / "absent.json") is None


def test_spine_for_tag_tr_verdict_selects_tr_spine(tmp_path, monkeypatch):
    tr_spine = tmp_path / "tr-textus-receptus-spine.db"
    tr_spine.touch()
    monkeypatch.setitem(ss._SPINE_BY_VERDICT, "tr", tr_spine)
    es_dir = tmp_path / "edition_struct"
    es_dir.mkdir()
    _w(es_dir / "tag.json", {"derived": {"textual_basis": {"verdict": "tr"}}})
    spine, reason = ss.spine_for_tag("tag", Path("/default/spine.db"), es_dir, tmp_path / "absent.json")
    assert spine == tr_spine
    assert reason == "tr"


def test_spine_for_tag_byzantine_verdict_selects_rp2018_spine(tmp_path, monkeypatch):
    rp_spine = tmp_path / "rp2018-spine.db"
    rp_spine.touch()
    monkeypatch.setitem(ss._SPINE_BY_VERDICT, "byzantine", rp_spine)
    es_dir = tmp_path / "edition_struct"
    es_dir.mkdir()
    _w(es_dir / "tag.json", {"derived": {"textual_basis": {"verdict": "byzantine"}}})
    spine, reason = ss.spine_for_tag("tag", Path("/default/spine.db"), es_dir, tmp_path / "absent.json")
    assert spine == rp_spine
    assert reason == "byzantine"


def test_spine_for_tag_mixed_critical_indeterminate_all_keep_the_default(tmp_path):
    default = Path("/default/spine.db")
    es_dir = tmp_path / "edition_struct"
    es_dir.mkdir()
    for i, verdict in enumerate(("mixed", "critical", "indeterminate")):
        _w(es_dir / f"tag{i}.json", {"derived": {"textual_basis": {"verdict": verdict}}})
        spine, reason = ss.spine_for_tag(f"tag{i}", default, es_dir, tmp_path / "absent.json")
        assert spine == default
        assert reason == "default"


def test_spine_for_tag_unclassified_edition_keeps_the_default(tmp_path):
    default = Path("/default/spine.db")
    spine, reason = ss.spine_for_tag("nevertagged", default, tmp_path / "absent-dir", tmp_path / "absent.json")
    assert spine == default
    assert reason == "default"


def test_spine_for_tag_falls_back_to_default_when_the_matching_spine_file_is_missing(tmp_path, monkeypatch):
    # a tr verdict is real, but if the pinned TR spine file isn't actually on disk (sibling repo
    # absent, path moved), this must degrade to the default rather than crash or point at nothing.
    monkeypatch.setitem(ss._SPINE_BY_VERDICT, "tr", tmp_path / "does-not-exist.db")
    es_dir = tmp_path / "edition_struct"
    es_dir.mkdir()
    _w(es_dir / "tag.json", {"derived": {"textual_basis": {"verdict": "tr"}}})
    default = Path("/default/spine.db")
    spine, reason = ss.spine_for_tag("tag", default, es_dir, tmp_path / "absent.json")
    assert spine == default
    assert reason == "default"


def test_real_spine_files_referenced_by_this_module_exist_on_disk():
    # not a synthetic test: confirms the two real, pinned sibling-repo spine files this module points
    # at by default actually exist where the module docstring says they do.
    for verdict, path in ss._SPINE_BY_VERDICT.items():
        assert path.exists(), f"{verdict} spine missing at {path}"
