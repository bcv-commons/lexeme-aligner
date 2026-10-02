"""publish_safe: the safety properties of a partial publish (merge on the HF manifest, schema guard, staging, preflight)."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("publish_safe", Path(__file__).resolve().parents[1] / "pipeline/scripts/publish_safe.py")
ps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ps)


def _m(langs, schema=("a", "b")):
    return {"schema": list(schema), "languages": langs}


def test_merge_replaces_only_the_selected_languages_and_keeps_the_published_schema():
    hf = _m({"aaa": {"rows": 1}, "bbb": {"rows": 2}, "ccc": {"rows": 3}})
    local = _m({"aaa": {"rows": 10}, "bbb": {"rows": 20}, "ccc": {"rows": 30}, "ddd": {"rows": 4}})
    out = ps.merge_manifest(hf, local, ["bbb", "ddd"])
    assert out["languages"] == {"aaa": {"rows": 1}, "bbb": {"rows": 20}, "ccc": {"rows": 3}, "ddd": {"rows": 4}}
    assert out["schema"] == ["a", "b"]                       # the schema comes from HF, never from the local manifest


def test_merge_never_drops_a_missing_selection_silently():
    with pytest.raises(KeyError):
        ps.merge_manifest(_m({}), _m({"aaa": {}}), ["zzz"])


def test_a_parquet_column_change_is_refused_but_extra_compact_documentation_is_allowed():
    assert ps.schema_conflict(_m({}), _m({})) is None
    assert "schema" in ps.schema_conflict(_m({}), _m({}, schema=("a", "b", "base_text")))                  # parquet: extra column = break
    assert ps.schema_conflict(_m({}), _m({}, schema=("a", "b", "new paragraph")), "compact") is None      # compact: additive docs only
    assert ps.schema_conflict(_m({}), _m({}, schema=("a", "CHANGED")), "compact")                           # a changed paragraph is refused
    assert "tokenizer_version" in ps.schema_conflict({"tokenizer_version": 1}, {"tokenizer_version": 2}, "compact")


def test_merged_top_level_takes_additive_compact_docs_from_local_only():
    hf, loc = _m({}), _m({}, schema=("a", "b", "new"))
    assert ps.merged_top_level(hf, loc, "compact")["schema"] == ["a", "b", "new"]
    assert ps.merged_top_level(hf, loc, "partition")["schema"] == ["a", "b"]


def test_running_isos_picks_up_languages_and_tags_being_processed():
    text = ("/x/.venv/bin/python3 -m lexeme_aligner.full_chain --iso rug --clean-out\n"
            "/x/.venv/bin/python3 -m lexeme_aligner.run_pilot --method eflomal --all --usj-dir u --iso rugwbt --publish-iso rug\n"
            "/usr/bin/python other_tool.py --iso zzz\n")
    assert ps.running_isos(text) == {"rug", "rugwbt"}


def test_staging_contains_only_the_current_selection_and_keeps_the_caches(tmp_path):
    src, stage = tmp_path / "src", tmp_path / "stage"
    for iso in ("aaa", "bbb", "ccc"):
        (src / f"iso={iso}").mkdir(parents=True)
        (src / f"iso={iso}/data.parquet").write_text(iso)
    stage.mkdir()
    (stage / ".publish_state.json").write_text("{}")
    (stage / ".publish_rate.json").write_text("{}")
    ps.stage_links(src, stage, ["iso=aaa/data.parquet", "iso=bbb/data.parquet"])
    ps.stage_links(src, stage, ["iso=ccc/data.parquet"])                      # a later, different selection
    left = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
    assert left == [".publish_rate.json", ".publish_state.json", "iso=ccc/data.parquet"]
    assert (stage / "iso=ccc/data.parquet").stat().st_ino == (src / "iso=ccc/data.parquet").stat().st_ino   # a hard link, no copy


def _world(tmp_path, monkeypatch, rows=3, manifest_rows=3, books=("GEN",), files=("GEN_ab.json",)):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    monkeypatch.setattr(ps, "REPO", tmp_path)
    part = tmp_path / "publish/lexeme-alignments/iso=xx"
    part.mkdir(parents=True)
    pq.write_table(pa.table({"a": list(range(rows))}), part / "data.parquet")
    ed = tmp_path / "publish/compact-alignments/x/xx/ED1"
    ed.mkdir(parents=True)
    for f in files:
        (ed / f).write_text("{}")
    lex = {"languages": {"xx": {"rows": manifest_rows, "base_texts": ["ED1"]}}}
    comp = {"languages": {"xx": {"editions": {"ED1": {"tag": "ed1", "books": list(books)}}}}}
    return lex, comp


def test_preflight_passes_a_complete_current_language(tmp_path, monkeypatch):
    lex, comp = _world(tmp_path, monkeypatch)
    assert ps.preflight("xx", {"xx": {}}, set(), lex, comp, allow_stale=True) == []


def test_preflight_catches_each_unsafe_condition(tmp_path, monkeypatch):
    lex, comp = _world(tmp_path, monkeypatch, manifest_rows=99)
    why = " | ".join(ps.preflight("xx", {}, {"ed1"}, lex, comp, allow_stale=True))
    assert "3 rows but the manifest says 99" in why and "no pipeline_decisions ledger entry" in why and "running for it right now" in why
    lex, comp = _world(tmp_path / "w2", monkeypatch, books=("GEN", "EXO"))
    assert any("missing book files" in w for w in ps.preflight("xx", {"xx": {}}, set(), lex, comp, allow_stale=True))
    lex["languages"]["xx"]["base_texts"] = ["ED1", "ED2"]
    assert any("no compact-alignments for edition" in w for w in ps.preflight("xx", {"xx": {}}, set(), lex, comp, allow_stale=True))


def test_preflight_leaves_out_a_language_unchanged_since_the_last_publish(tmp_path, monkeypatch):
    import os
    lex, comp = _world(tmp_path, monkeypatch)
    f = tmp_path / "publish/lexeme-alignments/iso=xx/data.parquet"
    os.utime(f, (1_700_000_000, 1_700_000_000))                               # 2023
    assert any("stale" in w for w in ps.preflight("xx", {"xx": {}}, set(), lex, comp, allow_stale=False))
    assert not any("stale" in w for w in ps.preflight("xx", {"xx": {}}, set(), lex, comp, allow_stale=True))


def test_paths_info_batched_batches_and_waits_out_a_429():
    import types
    from huggingface_hub.errors import HfHubHTTPError

    calls, slept = [], []

    import httpx

    def Resp():
        return httpx.Response(429, headers={"Retry-After": "7"}, request=httpx.Request("GET", "https://huggingface.co/api/x"))

    class FakeApi:
        def __init__(self):
            self.failed = False

        def get_paths_info(self, repo, paths, repo_type=None):
            calls.append(list(paths))
            if len(calls) == 2 and not self.failed:        # the second batch is rate limited once
                self.failed = True
                raise HfHubHTTPError("429", response=Resp())
            return [types.SimpleNamespace(path=p) for p in paths if not p.endswith("missing")]

    paths = [f"iso={i:03d}/data.parquet" for i in range(250)] + ["iso=zzz/missing"]
    out = ps.paths_info_batched(FakeApi(), "r/x", paths, batch=100, sleep=slept.append)
    assert [len(c) for c in calls] == [100, 100, 100, 51]       # 3 full-size batches + the remainder, the 429'd one retried
    assert slept == [12.0]                                        # Retry-After 7 + 5
    assert "iso=000/data.parquet" in out and "iso=zzz/missing" not in out and len(out) == 250
