"""full_chain has no privileged edition: compact-alignments runs once per edition and aligned_mwe pools them all."""
import sys
from pathlib import Path

from lexeme_aligner import full_chain as fc


def _run_chain(monkeypatch, tmp_path, editions, extra=()):
    calls = []

    def fake_run(mod, *args, **kw):
        calls.append((mod, [str(a) for a in args]))
        return True

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fc, "_run", fake_run)
    monkeypatch.setattr(fc, "allowed_testaments", lambda iso, exclusions: {"nt", "ot"})
    monkeypatch.setattr(fc, "editions_for", lambda iso, testaments, cfg: [{"edition_code": e} for e in editions])
    monkeypatch.setattr(sys, "argv", ["full_chain", "--iso", "xyz", "--skip-ingest", *extra])
    tags = [fc._tag("xyz", e, is_primary=(i == 0)) for i, e in enumerate(editions)]
    for t in tags[:3]:
        (tmp_path / "pipeline/work/ingest-cache" / f"usj-{t}").mkdir(parents=True)
    assert fc.main() == 0
    return calls, tags


def _arg(args, flag):
    return args[args.index(flag) + 1]


def test_compact_alignments_runs_once_per_edition(monkeypatch, tmp_path):
    calls, tags = _run_chain(monkeypatch, tmp_path, ["AAA", "BBB", "CCC"])
    compact = [a for m, a in calls if m == "compact_align"]
    assert [_arg(a, "--iso") for a in compact] == tags
    for a in compact:
        assert _arg(a, "--usj-dir").endswith(f"usj-{_arg(a, '--iso')}")
        assert _arg(a, "--publish-iso") == "xyz"


def test_aligned_mwe_pools_every_other_edition(monkeypatch, tmp_path):
    calls, tags = _run_chain(monkeypatch, tmp_path, ["AAA", "BBB", "CCC"])
    (mwe,) = [a for m, a in calls if m == "export_mwe"]
    assert _arg(mwe, "--iso") == tags[0]
    assert _arg(mwe, "--pool").split(",") == tags[1:]


def test_single_edition_language_has_no_pool_flag(monkeypatch, tmp_path):
    calls, tags = _run_chain(monkeypatch, tmp_path, ["AAA"])
    (mwe,) = [a for m, a in calls if m == "export_mwe"]
    assert "--pool" not in mwe
    assert len([1 for m, _ in calls if m == "compact_align"]) == 1


def test_ledger_entry_is_the_last_chain_step_with_every_edition(monkeypatch, tmp_path):
    calls, tags = _run_chain(monkeypatch, tmp_path, ["AAA", "BBB", "CCC"])
    assert calls[-1][0] == "pipeline_decisions"                    # after compact_align, nothing runs behind it
    args = calls[-1][1]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--tag"] == tags
    assert [Path(args[i + 1]).name for i, a in enumerate(args) if a == "--usj-dir"] == [f"usj-{t}" for t in tags]
    assert _arg(args, "--publish-iso") == "xyz" and "--no-publish" in args       # dataset-root copies are publish_safe's job


def test_no_ledger_flag_skips_the_step(monkeypatch, tmp_path):
    calls, _ = _run_chain(monkeypatch, tmp_path, ["AAA"], extra=("--no-ledger",))
    assert "pipeline_decisions" not in [m for m, _ in calls]


def test_only_the_ubs_senses_scheme_runs_in_the_chain(monkeypatch, tmp_path):
    calls, tags = _run_chain(monkeypatch, tmp_path, ["AAA", "BBB"])
    (senses,) = [a for m, a in calls if m == "senses_attested"]      # exactly one call: the legacy (BHSA-numbered) scheme is gone
    assert _arg(senses, "--scheme") == "ubs" and _arg(senses, "--iso") == tags[0]
    assert _arg(senses, "--pool").split(",") == tags[1:]
