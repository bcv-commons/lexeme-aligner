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


def test_jev_plan_asks_book_by_book_with_modules_and_the_fixed_variant(tmp_path):
    plan = align_text.jev_plan("engweb_sample", "eng", tmp_path, ["RUT", "PHM"], 4)
    assert [b for b, _ in plan] == ["RUT", "PHM"]
    cmd = plan[0][1]
    assert cmd[0] == "lexeme_aligner.eval.jev_clauses" and "--dry-run" not in cmd and "--max-usd" not in cmd
    assert cmd[cmd.index("--conventions") + 1] == "modules" and cmd[cmd.index("--variant") + 1] == align_text.JEV_VARIANT


def test_plan_parts_runs_the_view_route_then_folds_and_compacts_morphed(tmp_path):
    steps = align_text.plan_parts(tmp_path / "usj", "zin_wbt", "zin", tmp_path, "--nt", False, None, tmp_path / "words.parquet")
    names = [s[0] for s in steps]
    assert names == ["parts-view", "eflomal", "part-priors", "gloss", "spanext", "gapfill", "residual", "fold", "compact"]
    env = dict((s[0], s[2]) for s in steps)
    assert env["eflomal"]["ALIGNER_OUT"] == str(tmp_path / "parts" / "out")
    assert env["gloss"]["ALIGNER_LEX_ROOT"] == str(tmp_path / "parts" / "priors")
    compact = steps[-1][1]
    assert "--morphed" in compact and compact[compact.index("--usj-dir") + 1] == str(tmp_path / "usj")
    assert "--parts" in align_text.jev_plan("zin_wbt", "zin", tmp_path, ["PHM"], 2, tmp_path / "w.parquet")[0][1]


def test_resolve_split_refuses_a_split_made_from_another_text(tmp_path):
    import json
    usj = tmp_path / "usj"
    usj.mkdir()
    (usj / "57-PHM.json").write_text(json.dumps({"type": "USJ", "content": [{"type": "book", "code": "PHM"}, {"type": "chapter", "number": "1"},
                                                  {"type": "para", "content": [{"type": "verse", "number": "1"}, "Paulo"]}]}))
    split = tmp_path / "words.parquet"
    split.write_bytes(b"x")
    (tmp_path / "_split.json").write_text(json.dumps({"text_fp": {"PHM": "00000000", "MAT": "11111111"}}))
    with pytest.raises(SystemExit, match="another text for PHM"):
        align_text.resolve_split(str(split), "zin", None, usj)
    from lexeme_aligner.text_fingerprint import book_file_hash
    (tmp_path / "_split.json").write_text(json.dumps({"text_fp": {"PHM": book_file_hash(usj / "57-PHM.json")}}))
    fp, pin = align_text.resolve_split(str(split), "zin", None, usj)
    assert fp == split and pin["format"] == "bcv-split" and pin["file"] == "words.parquet"
