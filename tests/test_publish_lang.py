import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("publish_lang", Path(__file__).resolve().parents[1] / "pipeline/scripts/publish_lang.py")
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


def test_default_is_a_dry_run_through_publish_safe():
    cmd = pl.build_command("tgl", push=False, skip=[], include_mwe=False)
    assert cmd[1].endswith("publish_safe.py") and "--push" not in cmd
    assert cmd[cmd.index("--datasets") + 1] == "lexeme-alignments,senses_attested_ubs,compact-alignments"


def test_push_skip_and_mwe():
    cmd = pl.build_command("tgl", push=True, skip=["senses_attested_ubs"], include_mwe=True)
    assert "--push" in cmd and "--include-mwe" in cmd
    assert cmd[cmd.index("--datasets") + 1] == "lexeme-alignments,compact-alignments,aligned_mwe"


def test_skipping_everything_is_an_error():
    with pytest.raises(SystemExit):
        pl.build_command("tgl", push=True, skip=["lexeme-alignments", "senses_attested_ubs", "compact-alignments"], include_mwe=False)
