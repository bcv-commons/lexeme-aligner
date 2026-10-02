import pytest

from lexeme_aligner import cli


def _mods(cmds):
    out = []
    for c in cmds:
        if c[1] == "-m":
            out.append([c[2], *c[3:]])                                  # python -m <module> args...
        else:
            out.append([c[1].rsplit("/", 1)[-1], *c[2:]])               # python <script> args...
    return out


def test_run_maps_to_full_chain_with_clean_out():
    assert _mods(cli.plan(["run", "tgl", "--skip-ingest"])) == [["lexeme_aligner.full_chain", "--iso", "tgl", "--clean-out", "--skip-ingest"]]
    assert "--clean-out" not in cli.plan(["run", "tgl", "--no-clean-out"])[0]


def test_batch_modes():
    assert _mods(cli.plan(["batch", "--catalog", "--include-dbt"])) == [["lexeme_aligner.onboard_catalog", "--full", "--include-dbt"]]
    assert _mods(cli.plan(["batch", "--catalog"])) == [["lexeme_aligner.onboard_catalog", "--full"]]
    assert _mods(cli.plan(["batch", "--list", "s.json", "--force"])) == [
        ["lexeme_aligner.onboard_batch", "--spec", "s.json", "--full", "--clean-out", "--force"]]
    assert _mods(cli.plan(["batch", "--all", "--fresh", "--workers", "3"])) == [["lexeme_aligner.batch", "--all", "--fresh", "--workers", "3"]]
    assert _mods(cli.plan(["batch", "--stale-before", "2026-09-28", "--skip-ingest"])) == [
        ["lexeme_aligner.batch", "--stale-before", "2026-09-28", "--skip-ingest"]]
    assert _mods(cli.plan(["batch", "--isos", "tgl,ind"])) == [["lexeme_aligner.batch", "--isos", "tgl,ind"]]


def test_grammar_all_runs_in_dependency_order():
    names = [c[2] for c in cli.plan(["grammar", "all"])]
    assert names == ["lexeme_aligner.article_bound", "lexeme_aligner.derive_typology", "lexeme_aligner.gram_struct"]
    assert _mods(cli.plan(["grammar", "check"])) == [["lexeme_aligner.derive_typology", "--check-known-answers"]]


def test_publish_is_always_the_safe_publisher_and_a_dry_run_by_default():
    cmd = cli.plan(["publish", "--iso", "tgl"])[0]
    assert cmd[1].endswith("publish_safe.py") and "--push" not in cmd


def test_bad_usage_exits_with_a_message():
    for bad in (["run"], ["batch"], ["grammar"], ["nonsense"]):
        with pytest.raises(SystemExit):
            cli.plan(bad)


def test_main_stops_at_the_first_failing_step(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_exec", lambda cmd: (ran.append(cmd[2]), 1 if "derive" in cmd[2] else 0)[1])
    assert cli.main(["grammar", "all"]) == 1
    assert ran == ["lexeme_aligner.article_bound", "lexeme_aligner.derive_typology"]      # gram_struct never started


def test_every_target_module_exists():
    import importlib.util
    for sub in (["run", "x"], ["batch", "--catalog"], ["batch", "--all"], ["grammar", "all"], ["eval"]):
        for cmd in cli.plan(sub):
            if cmd[1] == "-m":
                assert importlib.util.find_spec(cmd[2]) is not None, cmd[2]
