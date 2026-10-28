import pytest

from lexeme_aligner import align_text, cli


def _names(steps):
    return [n for n, _ in steps]


def test_plan_bridge_and_order(tmp_path):
    steps = align_text.plan(tmp_path / "usj", "kvg_draft", "kvg", tmp_path / "out", "--all", True, "Kuni")
    assert _names(steps) == ["eflomal-nt", "eflomal-ot", "export", "gloss", "spanext", "gapfill", "residual", "compact"]
    d = dict(steps)
    assert d["eflomal-ot"][-2:] == ["--ot", "--nt-bridge"] and d["eflomal-nt"][-1] == "--nt"
    assert all(["--publish-iso", "kvg"] == c[c.index("--publish-iso"):c.index("--publish-iso") + 2] for _, c in steps)
    assert d["export"][d["export"].index("--root") + 1] == str(tmp_path / "out" / "lexroot")
    assert d["compact"][d["compact"].index("--publish") + 1] == str(tmp_path / "out" / "compact")


def test_plan_without_bridge_uses_scope(tmp_path):
    steps = align_text.plan(tmp_path, "x", "eng", tmp_path / "o", "--nt", False, None)
    assert _names(steps)[0] == "eflomal" and steps[0][1][-1] == "--nt"
    assert "--lang-name" not in steps[0][1]


def test_scope_of():
    assert align_text.scope_of({"RUT", "MAT"}) == "--all"
    assert align_text.scope_of({"MAT"}) == "--nt"
    assert align_text.scope_of({"GEN"}) == "--ot"
    with pytest.raises(SystemExit):
        align_text.scope_of({"XYZ"})


def test_step_env_points_every_write_setting_into_out(tmp_path):
    env = align_text.step_env(tmp_path, None)
    for k in ("ALIGNER_OUT", "ALIGNER_LEX_ROOT", "ALIGNER_LLM_CACHE", "ALIGNER_DNS_CACHE", "ALIGNER_STOPWORDS_CACHE", "ALIGNER_MORPH_CACHE"):
        assert env[k].startswith(str(tmp_path))
    assert "ALIGNER_SPINE_DB" not in env or env["ALIGNER_SPINE_DB"] == __import__("os").environ.get("ALIGNER_SPINE_DB")
    env = align_text.step_env(tmp_path, tmp_path / "data")
    assert env["ALIGNER_SPINE_DB"] == str(tmp_path / "data" / "lexeme-spine-macula.db")


def test_text_digest_depends_on_names_and_content(tmp_path):
    (tmp_path / "08-RUT.json").write_text("a")
    d1, books = align_text.text_digest(tmp_path)
    assert books == ["RUT"]
    (tmp_path / "08-RUT.json").write_text("b")
    assert align_text.text_digest(tmp_path)[0] != d1


def test_output_inside_checkout_refused(tmp_path):
    with pytest.raises(SystemExit, match="outside the checkout"):
        align_text.main(["--usj", str(tmp_path), "--edition", "x", "--iso", "eng", "--out", str(align_text.REPO / "publish")])


def test_cli_dispatch():
    cmd = cli.plan(["align-text", "--usj", "u", "--edition", "e", "--iso", "eng", "--out", "o"])[0]
    assert cmd[1:4] == ["-m", "lexeme_aligner.align_text", "--usj"]
